"""Settings people change from the dashboard, saved in server/settings.json.

One file, one schema. The dashboard (served by the launcher even when the brain is stopped) reads and writes it; the
brain applies it at start and whenever the launcher tells it the file changed; the robot gets the clock / weather /
screen part and keeps its own copy in flash, so its clock and weather work with the computer off.

Every setting has a default, so the file only needs what someone changed. Unknown keys are dropped, bad values fall
back to the default. Standard library only (the launcher imports this without the brain's dependencies).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from . import computer

PATH = Path(__file__).resolve().parent.parent / "settings.json"

# Each setting: (default, kind, extra). kind: "bool", "int", "float", "str", "choice", "time"
#   int / float extra = (min, max); choice extra = allowed values; str extra = max length; time = "HH:MM".
# `live` = applies at once; anything else needs the brain restarted (noted in the dashboard).
SCHEMA: dict[str, dict[str, tuple]] = {
    "rocky": {
        "human_name":    ("", "str", 40),                 # empty = HUMAN_NAME from .env, else "friend" (restart)
        "awake_seconds": (60.0, "float", (10.0, 600.0)),  # how long he stays awake after you talk
        "volume":        (0.7, "float", (0.0, 1.0)),
        "speaker_out":   ("robot", "choice", ("robot", "computer")),
        # which mic he listens through: "auto" = the robot's while it's connected, else this computer's; "robot";
        # "computer" = this computer's `mic_device` (by name; empty = MIC_DEVICE from .env, else the system default)
        "mic":           ("auto", "choice", ("auto", "robot", "computer")),
        "mic_device":    ("", "str", 120),
        "camera_rotate": (270, "choice", (0, 90, 180, 270)),
        "body_language": (True, "bool", None),           # small head moves while he talks (body.py)
    },
    "clock": {
        "hour_format":   (12, "choice", (12, 24)),
        "date_format":   ("month_day", "choice", ("month_day", "day_month")),   # "Mon Oct 5" / "Mon 5 Oct"
        "show_weekday":  (True, "bool", None),
        "timezone":      ("", "str", 64),                 # IANA name; empty = this computer's time zone
    },
    "weather": {
        "show":          (True, "bool", None),
        "location":      ("", "str", 80),                 # label shown in the dashboard
        "lat":           (None, "float", (-90.0, 90.0)),  # None = no location yet: no weather
        "lon":           (None, "float", (-180.0, 180.0)),
        "units":         ("f", "choice", ("f", "c")),
        "show_sun":      (False, "bool", None),           # sunrise / sunset instead of the day's high / low
        "refresh_min":   (30, "int", (10, 180)),
    },
    "vision": {
        "every_question":   (True, "bool", None),        # a picture with every question while awake
        "snapshot":         ("xga", "choice", ("off", "svga", "xga", "uxga")),  # sharp still when he looks
        "perception":       (True, "bool", None),        # local detectors: faces, and hands for a wave
        "gestures":         (True, "bool", None),        # a wave wakes him
        "stream":           ("vga", "choice", ("qvga", "vga")),   # the camera stream: 320x240 or 640x480
    },
    "character": {
        "id":            ("rocky", "str", 40),           # "rocky" (built in) or one of server/characters/<id>.json
    },
    "face": {
        "pack":          ("rocky", "str", 48),           # a face pack id: server/faces/<id>.json or custom/<id>
    },
    "screen": {
        "brightness":    (80, "int", (5, 100)),           # percent, for the clock screen and the face
        "night_mode":    ("dim", "choice", ("off", "dim", "dark")),   # clock screen at night
        "night_start":   ("23:00", "time", None),
        "night_end":     ("07:00", "time", None),
        "night_brightness": (10, "int", (1, 100)),
        # what the screen shows while he sleeps: the clock, a game that plays itself (firmware idle_games.cpp), or
        # "roam": his face up and the head looking round the room (the clock at night)
        "idle_screen":   ("clock", "choice", ("clock", "pong", "tetris", "frogger", "roam")),
    },
    # What Rocky may do on this computer (brain/computer.py). Off until you switch it on; the choice is remembered.
    "computer": {
        "control":       (False, "bool", None),
        "apps":          (computer.DEFAULT_APPS, "str", 1000),   # comma or line separated
        "bookmarks":     ("", "str", 3000),               # "Name = https://..." per line
        "shortcuts":     ("", "str", 1000),               # Apple Shortcuts he may run, comma or line separated
        "confirm_shortcuts": (True, "bool", None),        # ask "Run X?" and wait for a yes first
    },
    # Listening and voice tuning (the dashboard's Advanced section); None = the default in config.py
    "listening": {
        "VAD_THRESHOLD":    (None, "float", (0.1, 0.95)),
        "TURN_THRESHOLD":   (None, "float", (0.1, 0.95)),
        "TURN_MAX_SILENCE": (None, "float", (0.5, 6.0)),
    },
    "voice": {
        "TTS_LEVEL":        (None, "float", (0.08, 0.40)),
        "TTS_HIGHPASS_HZ":  (None, "float", (0.0, 600.0)),
        "TTS_PRESENCE_DB":  (None, "float", (0.0, 12.0)),
    },
}
RESTART_NEEDED = {("rocky", "human_name"), ("character", "id")}
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def defaults() -> dict:
    return {sec: {k: spec[0] for k, spec in keys.items()} for sec, keys in SCHEMA.items()}


def clean_value(section: str, key: str, value):
    """The value, checked against the schema. Raises ValueError with a message for the dashboard."""
    default, kind, extra = SCHEMA[section][key]
    if value is None:
        if default is None:
            return None
        raise ValueError(f"{key} can't be empty")
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    if kind in ("int", "float"):
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        lo, hi = extra
        if not lo <= v <= hi:
            raise ValueError(f"{key} must be between {lo:g} and {hi:g}")
        return int(round(v)) if kind == "int" else v
    if kind == "choice":
        for c in extra:
            if value == c or str(value) == str(c):
                return c
        raise ValueError(f"{key} must be one of: {', '.join(map(str, extra))}")
    if kind == "time":
        if not isinstance(value, str) or not _TIME.match(value):
            raise ValueError(f"{key} must be a time like 07:30")
        return value
    if kind == "str":
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        value = value.strip()
        if len(value) > extra:
            raise ValueError(f"{key} must be at most {extra} characters")
        if section == "clock" and key == "timezone" and value and posix_tz(value) is None:
            raise ValueError(f"unknown time zone: {value}")
        if section == "character" and key == "id" and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", value):
            raise ValueError("a character id is lowercase letters, digits and dashes")
        if section == "face" and key == "pack" and not re.fullmatch(r"(custom/)?[a-z0-9][a-z0-9-]{0,39}", value):
            raise ValueError("a face pack id is lowercase letters, digits and dashes")
        if section == "computer" and key == "bookmarks":
            computer.parse_bookmarks(value)   # raises ValueError naming the bad line
        return value
    raise ValueError(f"{key}: unknown kind {kind}")


def load() -> dict:
    """Defaults overlaid with whatever valid values the file has. Never raises."""
    out = defaults()
    try:
        raw = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return out
    if not isinstance(raw, dict):
        return out
    for sec, keys in SCHEMA.items():
        given = raw.get(sec)
        if not isinstance(given, dict):
            continue
        for k in keys:
            if k in given:
                try:
                    out[sec][k] = clean_value(sec, k, given[k])
                except ValueError:
                    pass  # a bad value in the file: keep the default
    return out


def update(changes: dict) -> dict:
    """Merge {section: {key: value}} into the file (validated; raises ValueError). Returns the full settings."""
    if not isinstance(changes, dict):
        raise ValueError("settings must be an object")
    cur = load()
    for sec, keys in changes.items():
        if sec not in SCHEMA or not isinstance(keys, dict):
            raise ValueError(f"unknown settings section: {sec}")
        for k, v in keys.items():
            if k not in SCHEMA[sec]:
                raise ValueError(f"unknown setting: {sec}.{k}")
            cur[sec][k] = clean_value(sec, k, v)
    _write(cur)
    return cur


def _write(data: dict) -> None:
    # write-then-rename, so a crash or the other process never sees half a file
    PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PATH.parent, prefix=".settings-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, PATH)


# ── Time zones ───────────────────────────────────────────────────────────────

def computer_timezone() -> str:
    """This computer's IANA time zone name, e.g. America/New_York ("UTC" if it can't tell)."""
    tz = os.environ.get("TZ", "")
    if "/" in tz and posix_tz(tz.lstrip(":")):
        return tz.lstrip(":")
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def posix_tz(name: str) -> str | None:
    """The POSIX rule string the robot's clock needs (e.g. "EST5EDT,M3.2.0,M11.1.0"), read from the footer of the
    system's tzdata file. None if the zone is unknown."""
    if not name or ".." in name or name.startswith("/"):
        return None
    for root in ("/usr/share/zoneinfo", "/var/db/timezone/zoneinfo", "/usr/lib/zoneinfo"):
        p = Path(root) / name
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if not data.startswith(b"TZif"):
            return None
        lines = data.rstrip(b"\n").rsplit(b"\n", 1)
        if len(lines) == 2:
            footer = lines[1].decode("ascii", "ignore").strip()
            if footer and len(footer) < 60:
                return footer
        return None
    try:  # tzdata from pip (Windows, minimal Linux)
        import importlib.resources
        parts = name.split("/")
        data = importlib.resources.files("tzdata.zoneinfo").joinpath(*parts).read_bytes()
        footer = data.rstrip(b"\n").rsplit(b"\n", 1)[1].decode("ascii", "ignore").strip()
        return footer or None
    except Exception:
        return None


def effective_timezone(s: dict) -> str:
    return s["clock"]["timezone"] or computer_timezone()


# ── What the robot gets ──────────────────────────────────────────────────────

def robot_payload(s: dict) -> dict:
    """The clock / weather / screen settings as the robot's `settings` message (short keys: it parses JSON on a
    microcontroller)."""
    c, w, sc = s["clock"], s["weather"], s["screen"]
    tz = effective_timezone(s)
    has_loc = w["lat"] is not None and w["lon"] is not None
    return {
        "type": "settings",
        "tz": posix_tz(tz) or "UTC0",
        "h24": c["hour_format"] == 24,
        "dmy": c["date_format"] == "day_month",
        "wday": c["show_weekday"],
        "wx": bool(w["show"] and has_loc),
        "lat": round(w["lat"], 2) if has_loc else 0.0,     # ~1 km: plenty for weather, and the robot
        "lon": round(w["lon"], 2) if has_loc else 0.0,     # never stores (or sends out) an exact location
        "unit": w["units"],
        "sun": w["show_sun"],
        "wxmin": w["refresh_min"],
        "bri": sc["brightness"],
        "night": sc["night_mode"],
        "n0": sc["night_start"],
        "n1": sc["night_end"],
        "nbri": sc["night_brightness"],
        "idle": sc["idle_screen"],
    }


# ── Place search for the weather location (Open-Meteo geocoding: free, no key) ─────────────────────────────────

def geocode(query: str, count: int = 6) -> list[dict]:
    q = (query or "").strip()
    if not q or len(q) > 80:
        return []
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": q, "count": count, "language": "en", "format": "json"})
    ctx = None
    try:
        import ssl
        import certifi  # in the brain's venv; Python.org builds on macOS have no system roots
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    req = urllib.request.Request(url, headers={"User-Agent": "desk-robot"})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
        data = json.loads(r.read())
    out = []
    for p in data.get("results") or []:
        label = ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country")) if x)
        out.append({"label": label, "lat": p.get("latitude"), "lon": p.get("longitude"),
                    "timezone": p.get("timezone") or "", "country_code": p.get("country_code") or ""})
    return out
