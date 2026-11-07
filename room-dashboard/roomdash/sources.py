"""Where the room's readings come from.

Each source has one method, ``read()``, that returns a ``Reading``: the
current value of every metric it knows, or ``None`` for a metric it could
not read right now. A source never invents a value. A sensor that says
"unavailable" becomes ``None`` and shows up as missing on the page.

Sources:
  demo           synthetic numbers, clearly labelled, for trying the page
  homeassistant  Home Assistant's REST API (long-lived access token)
  homey          Homey Pro's local Web API (API key)
  file           a JSON file that something else writes (e.g. a HomeyScript)
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# The metrics this dashboard knows. Units are fixed; sources convert.
METRICS = {
    "temperature": {"unit": "°C", "lo": -40.0, "hi": 80.0},
    "humidity": {"unit": "%", "lo": 0.0, "hi": 100.0},
    "co2": {"unit": "ppm", "lo": 250.0, "hi": 10000.0},
}

TIMEOUT_S = 8


@dataclass
class Reading:
    values: dict[str, float | None]
    errors: dict[str, str] = field(default_factory=dict)  # metric -> why it is missing


def clean(metric: str, raw) -> float | None:
    """A finite number inside the metric's sane range, else None."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None  # "unavailable", "unknown", "" ...
    spec = METRICS[metric]
    if not math.isfinite(v) or not spec["lo"] <= v <= spec["hi"]:
        return None
    return round(v, 1 if metric != "co2" else 0)


def _get_json(url: str, token: str) -> object:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "room-dashboard",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read(2_000_000))


def _base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"the url must start with http:// or https:// (got {url!r})")
    return url


class DemoSource:
    """Believable but synthetic: a day/night temperature swing, CO2 that rises
    while 'someone' is in the room and drops when the window 'opens'."""

    label = "Demodata (påhittade värden)"
    demo = True

    def read(self, now: float | None = None) -> Reading:
        return Reading(demo_values(now if now is not None else time.time()))


def demo_values(t: float) -> dict[str, float]:
    lt = time.localtime(t)
    hour = lt.tm_hour + lt.tm_min / 60
    temp = 21.0 + 1.2 * math.sin((hour - 9) / 24 * 2 * math.pi) + 0.15 * math.sin(t / 900)
    home = 16 <= hour or hour < 8          # someone is in the room evenings and nights
    co2 = 520 + (650 * min(1.0, ((hour - 16) % 24) / 5) if home else 80) + 40 * math.sin(t / 1300)
    if 21 <= hour < 21.5:                  # the window is open for half an hour
        co2 = 480
    hum = 38 + 6 * math.sin((hour - 3) / 24 * 2 * math.pi) + 2 * math.sin(t / 2000)
    return {"temperature": round(temp, 1), "humidity": round(hum, 1), "co2": round(co2)}


def demo_outdoor(t: float) -> float:
    lt = time.localtime(t)
    hour = lt.tm_hour + lt.tm_min / 60
    return round(4.0 + 3.5 * math.sin((hour - 9) / 24 * 2 * math.pi) + 0.6 * math.sin(t / 5000), 1)


class HomeAssistantSource:
    """GET /api/states/<entity_id> for each metric. Needs a long-lived access
    token (your profile page in Home Assistant, at the bottom)."""

    demo = False

    def __init__(self, cfg: dict) -> None:
        self.url = _base(cfg.get("url", ""))
        self.token = cfg.get("token", "")
        self.entities: dict[str, str] = {k: v for k, v in (cfg.get("entities") or {}).items() if v}
        unknown = set(self.entities) - set(METRICS)
        if unknown:
            raise ValueError(f"unknown metric(s) in entities: {', '.join(sorted(unknown))}")
        if not self.token:
            raise ValueError("homeassistant.token is empty")
        if not self.entities:
            raise ValueError("homeassistant.entities lists no sensors")
        self.label = "Home Assistant"

    def read(self) -> Reading:
        out = Reading({m: None for m in self.entities})
        for metric, entity in self.entities.items():
            try:
                state = _get_json(f"{self.url}/api/states/{urllib.parse.quote(entity)}", self.token)
                out.values[metric] = parse_ha_state(metric, state)
                if out.values[metric] is None:
                    out.errors[metric] = f"{entity}: {state.get('state') if isinstance(state, dict) else 'oläsbart'}"
            except Exception as e:  # one broken sensor must not hide the others
                out.errors[metric] = f"{entity}: {_why(e)}"
        return out


def parse_ha_state(metric: str, state: object) -> float | None:
    if not isinstance(state, dict):
        return None
    value = state.get("state")
    unit = ((state.get("attributes") or {}).get("unit_of_measurement") or "").strip()
    if metric == "temperature" and unit in ("°F", "F"):
        try:
            value = (float(value) - 32) * 5 / 9
        except (TypeError, ValueError):
            return None
    return clean(metric, value)


class HomeySource:
    """Homey Pro's local API: GET /api/manager/devices/device/<id> and read
    capabilitiesObj.<capability>.value. Needs an API key (Homey web app,
    Settings, API Keys) with permission to read devices."""

    demo = False
    DEFAULT_CAPABILITY = {
        "temperature": "measure_temperature",
        "humidity": "measure_humidity",
        "co2": "measure_co2",
    }

    def __init__(self, cfg: dict) -> None:
        self.url = _base(cfg.get("url", ""))
        self.token = cfg.get("token", "")
        self.devices: dict[str, dict] = {}
        for metric, spec in (cfg.get("devices") or {}).items():
            if metric not in METRICS:
                raise ValueError(f"unknown metric in devices: {metric}")
            if isinstance(spec, str):
                spec = {"device": spec}
            if not isinstance(spec, dict) or not spec.get("device"):
                continue
            self.devices[metric] = {
                "device": spec["device"],
                "capability": spec.get("capability") or self.DEFAULT_CAPABILITY[metric],
            }
        if not self.token:
            raise ValueError("homey.token is empty")
        if not self.devices:
            raise ValueError("homey.devices lists no sensors")
        self.label = "Homey"

    def read(self) -> Reading:
        out = Reading({m: None for m in self.devices})
        cache: dict[str, object] = {}  # one request per device, even if it has several sensors
        for metric, spec in self.devices.items():
            dev = spec["device"]
            try:
                if dev not in cache:
                    cache[dev] = _get_json(
                        f"{self.url}/api/manager/devices/device/{urllib.parse.quote(dev)}", self.token)
                out.values[metric] = parse_homey_device(metric, cache[dev], spec["capability"])
                if out.values[metric] is None:
                    out.errors[metric] = f"{spec['capability']} saknas eller har inget värde"
            except Exception as e:
                out.errors[metric] = _why(e)
        return out


def parse_homey_device(metric: str, device: object, capability: str) -> float | None:
    if not isinstance(device, dict):
        return None
    cap = (device.get("capabilitiesObj") or {}).get(capability)
    if not isinstance(cap, dict):
        return None
    return clean(metric, cap.get("value"))


class FileSource:
    """A JSON file another program keeps up to date:
    {"at": "2026-11-07T18:00:00+01:00", "values": {"temperature": 21.4, ...}}
    Older than max_age_minutes counts as missing, so a stopped writer shows
    up as gaps instead of a frozen value."""

    demo = False

    def __init__(self, cfg: dict, base_dir: Path) -> None:
        path = Path(cfg.get("path") or "")
        if not str(path):
            raise ValueError("file.path is empty")
        self.path = path if path.is_absolute() else base_dir / path
        self.max_age = float(cfg.get("max_age_minutes", 15)) * 60
        self.label = f"Fil: {self.path.name}"

    def read(self, now: float | None = None) -> Reading:
        now = time.time() if now is None else now
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Reading({m: None for m in METRICS}, {"*": f"{self.path.name} finns inte"})
        except (OSError, json.JSONDecodeError) as e:
            return Reading({m: None for m in METRICS}, {"*": _why(e)})
        return parse_file_reading(data, now, self.max_age)


def parse_file_reading(data: object, now: float, max_age: float) -> Reading:
    if not isinstance(data, dict) or not isinstance(data.get("values"), dict):
        return Reading({m: None for m in METRICS}, {"*": "filen saknar values"})
    at = parse_time(data.get("at"))
    if at is None:
        return Reading({m: None for m in METRICS}, {"*": "filen saknar en giltig tid i at"})
    if now - at > max_age:
        return Reading({m: None for m in METRICS}, {"*": "filen är för gammal"})
    values = {}
    for metric in METRICS:
        if metric in data["values"]:
            values[metric] = clean(metric, data["values"][metric])
    return Reading(values)


def parse_time(s: object) -> float | None:
    """ISO 8601 with a timezone -> epoch seconds."""
    from datetime import datetime
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # a time without a zone is ambiguous
    return dt.timestamp()


def _why(e: Exception) -> str:
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 401:
            return "nekad (401): fel eller utgången token"
        if e.code == 404:
            return "hittades inte (404): kontrollera id/namn"
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        return f"når inte enheten ({e.reason})"
    if isinstance(e, TimeoutError):
        return "svarar inte (timeout)"
    return f"{type(e).__name__}: {e}"


def make_source(cfg: dict, base_dir: Path):
    kind = (cfg.get("type") or "demo").lower()
    if kind == "demo":
        return DemoSource()
    if kind == "homeassistant":
        return HomeAssistantSource(cfg.get("homeassistant") or {})
    if kind == "homey":
        return HomeySource(cfg.get("homey") or {})
    if kind == "file":
        return FileSource(cfg.get("file") or {}, base_dir)
    raise ValueError(f"unknown source type {kind!r} (demo, homeassistant, homey, file)")
