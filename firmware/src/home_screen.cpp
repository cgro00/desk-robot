#include "home_screen.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <time.h>

static const char* WEEKDAYS[] = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"};
static const char* MONTHS[] = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"};

static uint16_t parseHHMM(const char* s, uint16_t fallback) {
  int h, m;
  if (!s || sscanf(s, "%d:%d", &h, &m) != 2 || h < 0 || h > 23 || m < 0 || m > 59) return fallback;
  return static_cast<uint16_t>(h * 60 + m);
}

// ── settings ────────────────────────────────────────────────────────────────

void HomeScreen::begin() {
  Preferences p;
  p.begin("home", true);
  String saved = p.getString("json", "");
  p.end();
  if (saved.length()) applyJson(saved.c_str(), false);
  setenv("TZ", s_.tz, 1);
  tzset();
  xTaskCreatePinnedToCore(weatherTask, "weather", 8192, this, 1, &task_, 0);
}

void HomeScreen::startClock() { configTzTime(s_.tz, "pool.ntp.org", "time.google.com", "time.nist.gov"); }

bool HomeScreen::applyJson(const char* json, bool save) {
  JsonDocument doc;
  if (deserializeJson(doc, json)) return false;
  HomeSettings n = s_;
  if (doc["tz"].is<const char*>()) strlcpy(n.tz, doc["tz"], sizeof(n.tz));
  n.h24 = doc["h24"] | n.h24;
  n.dmy = doc["dmy"] | n.dmy;
  n.weekday = doc["wday"] | n.weekday;
  n.weather = doc["wx"] | n.weather;
  n.lat = doc["lat"] | n.lat;
  n.lon = doc["lon"] | n.lon;
  const char* unit = doc["unit"] | "";
  if (unit[0] == 'c' || unit[0] == 'f') n.unit = unit[0];
  n.sun = doc["sun"] | n.sun;
  n.wxMinutes = constrain(static_cast<int>(doc["wxmin"] | static_cast<int>(n.wxMinutes)), 10, 180);
  n.bright = constrain(static_cast<int>(doc["bri"] | static_cast<int>(n.bright)), 5, 100);
  const char* night = doc["night"] | "";
  if (!strcmp(night, "off")) n.night = 0;
  else if (!strcmp(night, "dim")) n.night = 1;
  else if (!strcmp(night, "dark")) n.night = 2;
  n.nightStart = parseHHMM(doc["n0"] | "", n.nightStart);
  n.nightEnd = parseHHMM(doc["n1"] | "", n.nightEnd);
  n.nightBright = constrain(static_cast<int>(doc["nbri"] | static_cast<int>(n.nightBright)), 1, 100);
  IdleMode idle;
  if (idleModeFromName(doc["idle"] | "", idle)) n.idle = idle;
  if (n.idle != games_.mode()) games_.reset(n.idle);      // a fresh game when it's switched on (or after a reboot)

  bool tzChanged = strcmp(n.tz, s_.tz) != 0;
  bool wxChanged = n.weather != s_.weather || n.lat != s_.lat || n.lon != s_.lon || n.unit != s_.unit;
  portENTER_CRITICAL(&lock_);
  s_ = n;
  if (wxChanged) wx_ = Weather();          // old place / units: don't show them
  portEXIT_CRITICAL(&lock_);
  if (tzChanged) {
    setenv("TZ", s_.tz, 1);
    tzset();
  }
  if (wxChanged) refreshWeather();
  if (save) {
    // save the whole set (the message may have carried only a few keys)
    JsonDocument out;
    out["tz"] = s_.tz; out["h24"] = s_.h24; out["dmy"] = s_.dmy; out["wday"] = s_.weekday; out["wx"] = s_.weather;
    out["lat"] = s_.lat; out["lon"] = s_.lon; out["unit"] = String(s_.unit); out["sun"] = s_.sun;
    out["wxmin"] = s_.wxMinutes; out["bri"] = s_.bright;
    out["night"] = s_.night == 0 ? "off" : s_.night == 1 ? "dim" : "dark";
    char t0[6], t1[6];
    snprintf(t0, sizeof(t0), "%02u:%02u", s_.nightStart / 60, s_.nightStart % 60);
    snprintf(t1, sizeof(t1), "%02u:%02u", s_.nightEnd / 60, s_.nightEnd % 60);
    out["n0"] = t0; out["n1"] = t1; out["nbri"] = s_.nightBright;
    out["idle"] = idleModeName(s_.idle);
    String js;
    serializeJson(out, js);
    Preferences p;
    p.begin("home", false);
    if (p.getString("json", "") != js) p.putString("json", js);   // only write flash when something changed
    p.end();
  }
  return true;
}

// ── weather (Open-Meteo, its own task so a slow request never stalls the face or the servos) ────────────────────

void HomeScreen::weatherTask(void* self) { static_cast<HomeScreen*>(self)->weatherLoop(); }

void HomeScreen::refreshWeather() {
  if (task_) xTaskNotifyGive(task_);
}

void HomeScreen::weatherLoop() {
  uint32_t waitMs = 2000;
  for (;;) {
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(waitMs));   // wakes early on refreshWeather()
    HomeSettings s;
    portENTER_CRITICAL(&lock_);
    s = s_;
    portEXIT_CRITICAL(&lock_);
    if (!s.weather) { waitMs = 60000; continue; }
    if (WiFi.status() != WL_CONNECTED) { waitMs = 5000; continue; }
    Weather w;
    bool ok = fetchWeather(w);
    portENTER_CRITICAL(&lock_);
    if (ok) wx_ = w;
    else strlcpy(wx_.error, w.error, sizeof(wx_.error));   // keep showing the last good reading
    portEXIT_CRITICAL(&lock_);
    waitMs = ok ? s.wxMinutes * 60000UL : 120000UL;          // retry a failure in 2 minutes
  }
}

bool HomeScreen::fetchWeather(Weather& w) {
  HomeSettings s;
  portENTER_CRITICAL(&lock_);
  s = s_;
  portEXIT_CRITICAL(&lock_);
  char url[360];
  snprintf(url, sizeof(url),
           "http://api.open-meteo.com/v1/forecast?latitude=%.2f&longitude=%.2f"
           "&current=temperature_2m,weather_code,is_day&daily=temperature_2m_max,temperature_2m_min,sunrise,sunset"
           "&timezone=auto&forecast_days=1&temperature_unit=%s",
           s.lat, s.lon, s.unit == 'c' ? "celsius" : "fahrenheit");
  HTTPClient http;
  http.setTimeout(8000);
  http.useHTTP10(true);       // no chunked transfer: the body streams straight into the JSON parser
  if (!http.begin(url)) { strlcpy(w.error, "bad url", sizeof(w.error)); return false; }
  int code = http.GET();
  if (code != 200) {
    snprintf(w.error, sizeof(w.error), "http %d", code);
    http.end();
    return false;
  }
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, http.getStream());
  http.end();
  if (err) { snprintf(w.error, sizeof(w.error), "json %s", err.c_str()); return false; }
  JsonObject cur = doc["current"], day = doc["daily"];
  if (cur.isNull() || day.isNull()) { strlcpy(w.error, "no data", sizeof(w.error)); return false; }
  w.temp = cur["temperature_2m"] | 0.0f;
  w.code = cur["weather_code"] | -1;
  w.day = (cur["is_day"] | 1) == 1;
  w.hi = day["temperature_2m_max"][0] | 0.0f;
  w.lo = day["temperature_2m_min"][0] | 0.0f;
  const char* rise = day["sunrise"][0] | "";
  const char* set = day["sunset"][0] | "";
  if (strlen(rise) >= 16) strlcpy(w.sunrise, rise + 11, sizeof(w.sunrise));   // "2026-10-05T06:51" -> "06:51"
  if (strlen(set) >= 16) strlcpy(w.sunset, set + 11, sizeof(w.sunset));
  w.valid = true;
  w.fetchedMs = millis();
  w.error[0] = 0;
  return true;
}

// ── drawing ─────────────────────────────────────────────────────────────────

bool HomeScreen::isNight(const struct tm& t) const {
  if (s_.night == 0 || s_.nightStart == s_.nightEnd) return false;
  int m = t.tm_hour * 60 + t.tm_min;
  if (s_.nightStart < s_.nightEnd) return m >= s_.nightStart && m < s_.nightEnd;
  return m >= s_.nightStart || m < s_.nightEnd;                 // e.g. 23:00 .. 07:00
}

bool HomeScreen::roamingNow() {
  if (s_.idle != IdleMode::Roam) return false;
  uint32_t now = millis();
  if (roamCheckMs_ == 0 || now - roamCheckMs_ >= 1000) {
    roamCheckMs_ = now ? now : 1;
    struct tm t;
    roamDay_ = !(getLocalTime(&t, 0) && isNight(t));   // no time yet: roam
  }
  return roamDay_;
}

void HomeScreen::restoreDisplay() {
  if (dark_) { u8g2_.setPowerSave(0); dark_ = false; }
  contrast_ = dayContrast();
  u8g2_.setContrast(contrast_);
}

void HomeScreen::draw() {
  HomeSettings s;
  Weather w;
  portENTER_CRITICAL(&lock_);
  s = s_;
  w = wx_;
  portEXIT_CRITICAL(&lock_);

  struct tm t;
  bool haveTime = getLocalTime(&t, 0);

  // night: dim or dark (only once the clock knows what time it is)
  bool night = haveTime && isNight(t);
  if (night && s.night == 2) {
    if (!dark_) { u8g2_.setPowerSave(1); dark_ = true; }
    return;
  }
  if (dark_) { u8g2_.setPowerSave(0); dark_ = false; }
  uint8_t want = static_cast<uint8_t>((night ? s.nightBright : s.bright) * 255 / 100);
  if (want != contrast_) { u8g2_.setContrast(want); contrast_ = want; }

  // burn-in: move everything a little each minute (a 5 x 3 pattern of offsets)
  int mn = haveTime ? t.tm_hour * 60 + t.tm_min : static_cast<int>(millis() / 60000);
  int dx = (mn * 3) % 5 - 2, dy = (mn * 7) % 3 - 1;

  u8g2_.clearBuffer();
  u8g2_.setDrawColor(1);
  char buf[32];
  if (s.idle != IdleMode::Clock && s.idle != IdleMode::Roam) {      // a game, with the time small in a corner
    if (!haveTime) {
      strlcpy(buf, "--:--", sizeof(buf));
    } else if (s.h24) {
      snprintf(buf, sizeof(buf), "%02d:%02d", t.tm_hour, t.tm_min);
    } else {
      int h12 = t.tm_hour % 12 == 0 ? 12 : t.tm_hour % 12;
      snprintf(buf, sizeof(buf), "%d:%02d %s", h12, t.tm_min, t.tm_hour < 12 ? "AM" : "PM");
    }
    games_.step();
    games_.draw(buf, dx, dy);
    u8g2_.sendBuffer();
    return;
  }
  if (!haveTime) {
    u8g2_.setFont(u8g2_font_logisoso28_tn);
    u8g2_.drawStr(64 - u8g2_.getStrWidth("--:--") / 2 + dx, 40 + dy, "--:--");
    u8g2_.setFont(u8g2_font_helvB08_tr);
    const char* msg = WiFi.status() == WL_CONNECTED ? "SETTING THE CLOCK..." : "WAITING FOR WIFI...";
    u8g2_.drawStr(64 - u8g2_.getStrWidth(msg) / 2 + dx, 58 + dy, msg);
    u8g2_.sendBuffer();
    return;
  }

  // date line
  if (s.weekday) {
    if (s.dmy) snprintf(buf, sizeof(buf), "%s %d %s", WEEKDAYS[t.tm_wday], t.tm_mday, MONTHS[t.tm_mon]);
    else snprintf(buf, sizeof(buf), "%s %s %d", WEEKDAYS[t.tm_wday], MONTHS[t.tm_mon], t.tm_mday);
  } else {
    if (s.dmy) snprintf(buf, sizeof(buf), "%d %s %d", t.tm_mday, MONTHS[t.tm_mon], t.tm_year + 1900);
    else snprintf(buf, sizeof(buf), "%s %d, %d", MONTHS[t.tm_mon], t.tm_mday, t.tm_year + 1900);
  }
  u8g2_.setFont(u8g2_font_helvB08_tr);
  u8g2_.drawStr(64 - u8g2_.getStrWidth(buf) / 2 + dx, 10 + dy, buf);

  // time, big; AM / PM small beside it
  int hour = t.tm_hour;
  const char* ampm = "";
  if (!s.h24) { ampm = hour < 12 ? "AM" : "PM"; hour = hour % 12 == 0 ? 12 : hour % 12; }
  snprintf(buf, sizeof(buf), s.h24 ? "%02d:%02d" : "%d:%02d", hour, t.tm_min);
  u8g2_.setFont(u8g2_font_logisoso28_tn);
  int tw = u8g2_.getStrWidth(buf);
  u8g2_.setFont(u8g2_font_helvB08_tr);
  int aw = ampm[0] ? u8g2_.getStrWidth(ampm) + 3 : 0;
  int tx = 64 - (tw + aw) / 2 + dx;
  bool wxRow = s.weather;
  int ty = (wxRow ? 42 : 48) + dy;
  u8g2_.setFont(u8g2_font_logisoso28_tn);
  u8g2_.drawStr(tx, ty, buf);
  if (aw) {
    u8g2_.setFont(u8g2_font_helvB08_tr);
    u8g2_.drawStr(tx + tw + 3, ty, ampm);
  }

  // weather row
  if (wxRow) {
    if (w.valid) {
      drawIcon(w.code, w.day, 6 + dx, 46 + dy);
      u8g2_.setFont(u8g2_font_helvB12_tf);
      snprintf(buf, sizeof(buf), "%d\xC2\xB0%c", static_cast<int>(lroundf(w.temp)), s.unit == 'c' ? 'C' : 'F');
      u8g2_.drawUTF8(27 + dx, 62 + dy, buf);
      u8g2_.setFont(u8g2_font_helvB08_tr);
      if (s.sun && w.sunrise[0]) {
        // two small lines on the right: a tiny up arrow + sunrise, a down arrow + sunset
        u8g2_.setFont(u8g2_font_5x8_tr);
        const char* times[2] = {w.sunrise, w.sunset};
        for (int i = 0; i < 2; ++i) {
          uint16_t m = parseHHMM(times[i], 0);
          if (s.h24) snprintf(buf, sizeof(buf), "%02d:%02d", m / 60, m % 60);
          else snprintf(buf, sizeof(buf), "%d:%02d", (m / 60) % 12 ? (m / 60) % 12 : 12, m % 60);
          int base = (i == 0 ? 53 : 62) + dy;
          int bx = 124 - u8g2_.getStrWidth(buf) + dx;
          u8g2_.drawStr(bx, base, buf);
          if (i == 0) u8g2_.drawTriangle(bx - 7, base - 1, bx - 2, base - 1, bx - 4, base - 6);    // up
          else u8g2_.drawTriangle(bx - 7, base - 6, bx - 2, base - 6, bx - 4, base - 1);          // down
        }
      } else {
        snprintf(buf, sizeof(buf), "H%d L%d", static_cast<int>(lroundf(w.hi)), static_cast<int>(lroundf(w.lo)));
        u8g2_.drawStr(124 - u8g2_.getStrWidth(buf) + dx, 62 + dy, buf);
      }
    } else {
      u8g2_.setFont(u8g2_font_helvB08_tr);
      const char* msg = w.error[0] ? "NO WEATHER" : "GETTING WEATHER...";
      u8g2_.drawStr(64 - u8g2_.getStrWidth(msg) / 2 + dx, 60 + dy, msg);
    }
  }
  u8g2_.sendBuffer();
}

// 16 x 16 weather icons from the WMO code (Open-Meteo's weather_code), drawn with plain lines and circles.
void HomeScreen::drawIcon(int code, bool day, int x, int y) {
  auto sun = [&](int cx, int cy, int r) {
    u8g2_.drawDisc(cx, cy, r);
    for (int i = 0; i < 8; ++i) {
      float a = i * PI / 4;
      u8g2_.drawLine(cx + cosf(a) * (r + 2), cy + sinf(a) * (r + 2), cx + cosf(a) * (r + 4), cy + sinf(a) * (r + 4));
    }
  };
  auto moon = [&](int cx, int cy, int r) {
    u8g2_.drawDisc(cx, cy, r);
    u8g2_.setDrawColor(0);
    u8g2_.drawDisc(cx + r / 2 + 1, cy - r / 2, r);
    u8g2_.setDrawColor(1);
  };
  auto cloud = [&](int cx, int cy) {       // about 14 x 8, cy = its bottom
    u8g2_.drawDisc(cx - 3, cy - 3, 3);
    u8g2_.drawDisc(cx + 1, cy - 5, 4);
    u8g2_.drawDisc(cx + 5, cy - 3, 3);
    u8g2_.drawBox(cx - 3, cy - 3, 9, 4);
  };
  int cx = x + 8;
  if (code == 0) { day ? sun(cx, y + 8, 3) : moon(cx, y + 8, 5); return; }                 // clear
  if (code == 1 || code == 2) {                                                              // partly cloudy
    day ? sun(x + 5, y + 5, 2) : moon(x + 5, y + 5, 4);
    u8g2_.setDrawColor(0); u8g2_.drawBox(x + 4, y + 7, 12, 9); u8g2_.setDrawColor(1);
    cloud(cx + 1, y + 15);
    return;
  }
  if (code == 3) { cloud(cx, y + 13); return; }                                              // overcast
  if (code == 45 || code == 48) {                                                            // fog
    for (int i = 0; i < 4; ++i) u8g2_.drawHLine(x + 1 + (i % 2) * 2, y + 3 + i * 4, 13);
    return;
  }
  cloud(cx, y + 10);
  if ((code >= 71 && code <= 77) || code == 85 || code == 86) {                              // snow
    for (int i = 0; i < 3; ++i) { u8g2_.drawPixel(x + 4 + i * 4, y + 13); u8g2_.drawPixel(x + 6 + i * 4, y + 15); }
    return;
  }
  if (code >= 95) {                                                                          // thunderstorm
    u8g2_.drawLine(cx + 1, y + 11, cx - 2, y + 14); u8g2_.drawLine(cx - 2, y + 14, cx + 2, y + 14);
    u8g2_.drawLine(cx + 2, y + 14, cx - 1, y + 17);
    return;
  }
  for (int i = 0; i < 3; ++i) u8g2_.drawLine(x + 5 + i * 4, y + 12, x + 3 + i * 4, y + 15);  // drizzle / rain
}

void HomeScreen::printStatus() {
  HomeSettings s;
  Weather w;
  portENTER_CRITICAL(&lock_);
  s = s_;
  w = wx_;
  portEXIT_CRITICAL(&lock_);
  struct tm t;
  char now[32] = "not set yet";
  if (getLocalTime(&t, 0)) strftime(now, sizeof(now), "%Y-%m-%d %H:%M:%S", &t);
  Serial.printf("home: time %s (tz %s), %s clock, weekday %s, showing %s\n", now, s.tz, s.h24 ? "24h" : "12h",
                s.weekday ? "on" : "off", idleModeName(s.idle));
  Serial.printf("home: weather %s at %.4f, %.4f in %c every %u min; brightness %u%%, night %s %02u:%02u-%02u:%02u (%u%%)\n",
                s.weather ? "on" : "off", s.lat, s.lon, s.unit, s.wxMinutes, s.bright,
                s.night == 0 ? "off" : s.night == 1 ? "dim" : "dark", s.nightStart / 60, s.nightStart % 60,
                s.nightEnd / 60, s.nightEnd % 60, s.nightBright);
  if (w.valid)
    Serial.printf("home: %.1f now (code %d, %s), high %.1f low %.1f, sun %s-%s, %lus ago\n", w.temp, w.code,
                  w.day ? "day" : "night", w.hi, w.lo, w.sunrise, w.sunset, (millis() - w.fetchedMs) / 1000);
  else
    Serial.printf("home: no weather yet%s%s\n", w.error[0] ? " - last error " : "", w.error);
}
