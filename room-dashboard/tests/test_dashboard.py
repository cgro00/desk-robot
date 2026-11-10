"""Run from room-dashboard/:  python3 -m unittest discover tests

No network, no sensors: HTTP calls are replaced with canned answers."""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402
from roomdash import sources, weather  # noqa: E402
from roomdash.history import History  # noqa: E402


class CleanTest(unittest.TestCase):
    def test_numbers_and_junk(self):
        self.assertEqual(sources.clean("temperature", "21.44"), 21.4)
        self.assertEqual(sources.clean("co2", 812.6), 813)
        for junk in ("unavailable", "unknown", "", None, True, float("nan"), "inf"):
            self.assertIsNone(sources.clean("temperature", junk), junk)

    def test_out_of_range_is_missing_not_clamped(self):
        self.assertIsNone(sources.clean("humidity", 140))
        self.assertIsNone(sources.clean("co2", 0))  # a dead sensor reporting 0 is not fresh air


class HomeAssistantTest(unittest.TestCase):
    def test_parse_state(self):
        st = {"state": "21.7", "attributes": {"unit_of_measurement": "°C"}}
        self.assertEqual(sources.parse_ha_state("temperature", st), 21.7)
        st = {"state": "70.0", "attributes": {"unit_of_measurement": "°F"}}
        self.assertEqual(sources.parse_ha_state("temperature", st), 21.1)
        self.assertIsNone(sources.parse_ha_state("humidity", {"state": "unavailable", "attributes": {}}))

    def test_read_with_one_broken_sensor(self):
        src = sources.HomeAssistantSource({
            "url": "http://ha.local:8123/", "token": "t",
            "entities": {"temperature": "sensor.t", "humidity": "sensor.h", "co2": ""},
        })
        self.assertNotIn("co2", src.entities)  # blank = not connected

        def fake(url, token):
            self.assertEqual(token, "t")
            if url.endswith("sensor.t"):
                return {"state": "22.0", "attributes": {"unit_of_measurement": "°C"}}
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)

        with mock.patch.object(sources, "_get_json", fake):
            r = src.read()
        self.assertEqual(r.values, {"temperature": 22.0, "humidity": None})
        self.assertIn("404", r.errors["humidity"])

    def test_config_errors(self):
        with self.assertRaises(ValueError):
            sources.HomeAssistantSource({"url": "ha.local", "token": "t", "entities": {"temperature": "x"}})
        with self.assertRaises(ValueError):
            sources.HomeAssistantSource({"url": "http://ha", "token": "", "entities": {"temperature": "x"}})
        with self.assertRaises(ValueError):
            sources.HomeAssistantSource({"url": "http://ha", "token": "t", "entities": {"lux": "x"}})


class HomeyTest(unittest.TestCase):
    def test_one_request_per_device(self):
        src = sources.HomeySource({
            "url": "http://192.168.1.50", "token": "k",
            "devices": {"temperature": {"device": "abc"}, "humidity": "abc", "co2": ""},
        })
        device = {"capabilitiesObj": {
            "measure_temperature": {"value": 20.25},
            "measure_humidity": {"value": 41},
        }}
        calls = []
        with mock.patch.object(sources, "_get_json", lambda url, token: calls.append(url) or device):
            r = src.read()
        self.assertEqual(r.values, {"temperature": 20.2, "humidity": 41.0})
        self.assertEqual(calls, ["http://192.168.1.50/api/manager/devices/device/abc"])

    def test_missing_capability(self):
        self.assertIsNone(sources.parse_homey_device("co2", {"capabilitiesObj": {}}, "measure_co2"))


class FileSourceTest(unittest.TestCase):
    def test_fresh_stale_and_bad(self):
        now = time.time()
        fresh = {"at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 60)),
                 "values": {"temperature": 20.5, "co2": "n/a"}}
        r = sources.parse_file_reading(fresh, now, 900)
        self.assertEqual(r.values, {"temperature": 20.5, "co2": None})
        old = dict(fresh, at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 3600)))
        self.assertIn("gammal", sources.parse_file_reading(old, now, 900).errors["*"])
        naive = dict(fresh, at="2026-11-07T12:00:00")  # no timezone: refuse to guess
        self.assertIn("*", sources.parse_file_reading(naive, now, 900).errors)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            r = sources.FileSource({"path": "nope.json"}, Path(d)).read()
        self.assertIn("finns inte", r.errors["*"])


class WeatherTest(unittest.TestCase):
    SAMPLE = {
        "utc_offset_seconds": 3600,
        "current": {"temperature_2m": 4.1, "apparent_temperature": 1.0, "relative_humidity_2m": 88,
                    "wind_speed_10m": 3.4, "precipitation": 0.0, "weather_code": 61, "is_day": 0},
        "hourly": {"time": ["2026-11-07T17:00", "2026-11-07T18:00", "2026-11-07T19:00"],
                   "temperature_2m": [4.5, 4.1, None], "precipitation_probability": [10, 60, 70],
                   "weather_code": [3, 61, 61]},
        "daily": {"sunrise": ["2026-11-07T07:24"], "sunset": ["2026-11-07T15:48"],
                  "temperature_2m_max": [6.0], "temperature_2m_min": [1.5]},
    }

    def test_parse(self):
        now = 1794070800  # 2026-11-07T17:00:00Z = 18:00 local at +01:00
        w = weather.parse(self.SAMPLE, now)
        self.assertEqual(w["current"]["temperature"], 4.1)
        self.assertFalse(w["current"]["is_day"])
        # local 17:00 at +01:00 is 16:00Z, an hour before now: still kept as "this hour"
        self.assertEqual([h["at"] for h in w["hours"]], [now - 3600, now, now + 3600])
        self.assertIsNone(w["hours"][2]["temperature"])
        self.assertEqual(w["today"]["sunrise"], 1794032640)  # 06:24Z
        self.assertEqual(w["today"]["min"], 1.5)

    def test_url_sends_only_position(self):
        url = weather.forecast_url(59.3293, 18.0686)
        self.assertIn("latitude=59.329", url)
        self.assertIn("longitude=18.069", url)


class HistoryTest(unittest.TestCase):
    def test_trim_persist_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "history.json"
            h = History(path, keep_s=3600)
            h.add(1000, {"temperature": 20}, "A")
            h.add(1000, {"temperature": 99}, "A")  # same time: ignored
            h.add(2000, {"temperature": 21}, "A")
            h.add(5000, {"temperature": 22}, "A")  # pushes 1000 out of the hour
            self.assertEqual([s["at"] for s in h.samples], [2000, 5000])
            again = History(path, keep_s=3600)
            self.assertEqual(again.samples, h.samples)
            self.assertEqual(again.source, "A")

    def test_source_change_archives(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "history.json"
            h = History(path)
            h.add(1000, {"temperature": 20}, "Homey")
            h.add(2000, {"temperature": 21}, "Home Assistant")
            self.assertEqual(len(h.samples), 1)
            self.assertTrue((Path(d) / "history-1000.json").exists())

    def test_broken_file_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "history.json"
            path.write_text("{not json")
            h = History(path)
            self.assertEqual(h.samples, [])
            self.assertTrue((Path(d) / "history.broken.json").exists())


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dash = server.Dashboard({"title": "Testrum"}, demo=True)
        cls.dash.poll_once()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.dash))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path, host=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if host:
            req.add_header("Host", host)
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers, r.read()

    def test_now_and_history(self):
        _, _, body = self.get("/api/now")
        now = json.loads(body)
        self.assertTrue(now["demo"])
        self.assertEqual(now["title"], "Testrum")
        self.assertIn("co2", now["room"]["values"])
        self.assertTrue(now["camera"])  # on unless config.json says "camera": false
        _, _, body = self.get("/api/history?hours=6")
        samples = json.loads(body)["samples"]
        self.assertGreater(len(samples), 60)
        self.assertGreaterEqual(samples[0]["at"], time.time() - 6 * 3600 - 5)

    def test_static_files(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        status, headers, _ = self.get("/app.js")
        self.assertIn("javascript", headers["Content-Type"])

    def test_no_escape_from_public(self):
        for path in ("/../server.py", "/%2e%2e/server.py", "/config.example.json"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 404)

    def test_foreign_host_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/now", host="evil.example:8790")
        self.assertEqual(cm.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
