"""Room dashboard: your room's climate, the weather, and the time, in one page.

Run from this folder (Python 3.10 or later, nothing to install):

    python3 server.py            # uses config.json, or demo data if there is none
    python3 server.py --demo     # demo data even if config.json exists

then open http://127.0.0.1:8790 in the browser on this computer.

Every POLL_SECONDS the server reads your sensors (Home Assistant, Homey, or
a JSON file), stores the numbers in data/history.json, and fetches the
weather from Open-Meteo every 15 minutes. The page asks this server for
everything; it makes no other requests.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from roomdash import sources, weather
from roomdash.history import History

HERE = Path(__file__).resolve().parent
PUBLIC = HERE / "public"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Dashboard:
    def __init__(self, cfg: dict, demo: bool) -> None:
        self.cfg = cfg
        self.title = str(cfg.get("title") or "Mitt rum")
        self.poll_s = max(15.0, float(cfg.get("poll_seconds", 60)))
        loc = cfg.get("location") or {}
        self.location = {
            "name": str(loc.get("name") or ""),
            "lat": loc.get("latitude"),
            "lon": loc.get("longitude"),
        }
        self.source = sources.DemoSource() if demo else sources.make_source(cfg.get("source") or {}, HERE)
        self.demo = getattr(self.source, "demo", False)
        # Demo data lives only in memory, so it can never mix with real readings.
        self.history = History(None if self.demo else HERE / "data" / "history.json")
        self.latest: dict = {"at": None, "values": {}, "errors": {}}
        self.weather: dict | None = None
        self.weather_error = ""
        if self.demo:
            self._backfill_demo()

    def _backfill_demo(self) -> None:
        now = time.time()
        t = now - 24 * 3600
        while t < now:
            self.history.add(t, {**sources.demo_values(t), "outdoor": sources.demo_outdoor(t)},
                             self.source.label)
            t += 300

    # ── background jobs ─────────────────────────────────────────────────────
    def start(self) -> None:
        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self._weather_loop, daemon=True).start()

    def poll_once(self) -> None:
        now = time.time()
        reading = self.source.read()
        values = {k: v for k, v in reading.values.items() if k in sources.METRICS}
        outdoor = ((self.weather or {}).get("current") or {}).get("temperature")
        if isinstance(outdoor, (int, float)) and now - (self.weather or {}).get("fetched_at", 0) < 3600:
            values["outdoor"] = round(float(outdoor), 1)
        elif self.demo:
            values["outdoor"] = sources.demo_outdoor(now)
        self.latest = {"at": now, "values": values, "errors": reading.errors}
        if any(v is not None for k, v in values.items() if k != "outdoor"):
            self.history.add(now, values, self.source.label)
        for metric, why in reading.errors.items():
            print(f"  {metric}: {why}")

    def _poll_loop(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception:
                traceback.print_exc()
            time.sleep(self.poll_s)

    def _weather_loop(self) -> None:
        lat, lon = self.location["lat"], self.location["lon"]
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            self.weather_error = "Ange location.latitude och location.longitude i config.json"
            return
        while True:
            try:
                self.weather = weather.fetch(lat, lon)
                self.weather_error = ""
            except Exception as e:
                self.weather_error = f"Kunde inte hämta vädret: {e}"
                print(f"(weather: {e})")
            time.sleep(weather.WEATHER_MINUTES * 60)

    # ── what the page asks for ──────────────────────────────────────────────
    def now_json(self) -> dict:
        return {
            "title": self.title,
            "demo": self.demo,
            "source": self.source.label,
            "location": self.location["name"],
            "poll_seconds": self.poll_s,
            "room": self.latest,
            "weather": self.weather,
            "weather_error": self.weather_error,
            "server_time": time.time(),
        }

    def history_json(self, hours: float) -> dict:
        hours = min(max(hours, 1.0), 24.0 * 7)
        return {"source": self.history.source or self.source.label,
                "samples": self.history.since(time.time() - hours * 3600)}


def make_handler(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        server_version = "room-dashboard"
        sys_version = ""

        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            # Only answer requests addressed to this computer by a local name
            # (stops a web page from reading us through DNS rebinding).
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            if host not in LOCAL_HOSTS:
                return self._reply(403, "text/plain", b"forbidden")
            url = urlparse(self.path)
            if url.path == "/api/now":
                return self._json(dash.now_json())
            if url.path == "/api/history":
                try:
                    hours = float(parse_qs(url.query).get("hours", ["24"])[0])
                except ValueError:
                    hours = 24.0
                return self._json(dash.history_json(hours))
            name = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            file = (PUBLIC / name).resolve()
            if PUBLIC not in file.parents or not file.is_file():
                return self._reply(404, "text/plain", b"not found")
            ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
                ctype += "; charset=utf-8"
            self._reply(200, ctype, file.read_bytes())

        def _json(self, obj: dict) -> None:
            self._reply(200, "application/json", json.dumps(obj).encode())

        def _reply(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def load_config(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path.name} är inte giltig JSON: {e}")
    if not isinstance(cfg, dict):
        raise SystemExit(f"{path.name} ska innehålla ett JSON-objekt")
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser(description="Room dashboard")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--demo", action="store_true", help="use synthetic demo data")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    if cfg is None:
        print("Ingen config.json hittades: visar demodata.")
        print("Kopiera config.example.json till config.json och fyll i dina sensorer.\n")
        cfg = {}
    demo = args.demo or (cfg.get("source") or {}).get("type", "demo") == "demo"
    try:
        dash = Dashboard(cfg, demo)
    except ValueError as e:
        raise SystemExit(f"Fel i config.json: {e}")
    port = args.port or int(cfg.get("port", 8790))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(dash))
    server.daemon_threads = True
    dash.start()
    print(f"{dash.title}: {dash.source.label}")
    print(f"Öppna http://127.0.0.1:{port} i webbläsaren. Stoppa med Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
