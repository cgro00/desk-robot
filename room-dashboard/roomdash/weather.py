"""Weather outside, from Open-Meteo (open-meteo.com): free, no account, no key.

Fetched by the server (not the browser) every WEATHER_MINUTES, so the page
itself never talks to anything but this computer. Only your configured
latitude and longitude are sent.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

API = "https://api.open-meteo.com/v1/forecast"
WEATHER_MINUTES = 15


def forecast_url(lat: float, lon: float) -> str:
    q = {
        "latitude": f"{lat:.3f}",
        "longitude": f"{lon:.3f}",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                   "weather_code,wind_speed_10m,precipitation,is_day",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "sunrise,sunset,temperature_2m_max,temperature_2m_min",
        "wind_speed_unit": "ms",
        "timezone": "auto",
        "forecast_days": "2",
    }
    return f"{API}?{urllib.parse.urlencode(q)}"


def parse(data: dict, now: float) -> dict:
    """Open-Meteo's response -> the small shape the page uses."""
    cur = data.get("current") or {}
    hourly = data.get("hourly") or {}
    daily = data.get("daily") or {}
    offset = int(data.get("utc_offset_seconds") or 0)

    def epoch(local_iso: str) -> float:
        # Open-Meteo gives local wall-clock times plus utc_offset_seconds.
        from datetime import datetime, timezone
        return datetime.fromisoformat(local_iso).replace(tzinfo=timezone.utc).timestamp() - offset

    hours = []
    times = hourly.get("time") or []
    for i, t in enumerate(times):
        at = epoch(t)
        if at < now - 3600:
            continue
        hours.append({
            "at": at,
            "temperature": _num(hourly.get("temperature_2m"), i),
            "rain_chance": _num(hourly.get("precipitation_probability"), i),
            "code": _num(hourly.get("weather_code"), i),
        })
        if len(hours) >= 13:
            break
    return {
        "fetched_at": now,
        "current": {
            "temperature": cur.get("temperature_2m"),
            "feels_like": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"),
            "wind": cur.get("wind_speed_10m"),
            "precipitation": cur.get("precipitation"),
            "code": cur.get("weather_code"),
            "is_day": bool(cur.get("is_day", 1)),
        },
        "today": {
            "max": _num(daily.get("temperature_2m_max"), 0),
            "min": _num(daily.get("temperature_2m_min"), 0),
            "sunrise": epoch(daily["sunrise"][0]) if daily.get("sunrise") else None,
            "sunset": epoch(daily["sunset"][0]) if daily.get("sunset") else None,
        },
        "hours": hours,
    }


def _num(seq, i):
    try:
        v = seq[i]
    except (TypeError, IndexError):
        return None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def fetch(lat: float, lon: float) -> dict:
    req = urllib.request.Request(forecast_url(lat, lon), headers={"User-Agent": "room-dashboard"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return parse(json.loads(resp.read(2_000_000)), time.time())
