#pragma once

#include <Arduino.h>
#include <U8g2lib.h>

#include "idle_games.h"

// The home screen: what the OLED shows while Rocky is asleep. Time of day and date (internet time, in the time zone
// from the dashboard), and the weather from Open-Meteo (free, no key) when a location is set.
//
// Everything comes from the robot itself: it fetches the time and the weather over WiFi, so the clock works with the
// brain server off. The settings arrive in the brain's `settings` message and are kept in flash, so they survive a
// power cut. Against OLED burn-in the whole layout shifts a pixel or two every minute, and it dims (or turns the
// screen off) at night. Instead of the clock it can play a game that plays itself (idle_games.h), with the time in
// a corner.

struct HomeSettings {
  char tz[64] = "UTC0";       // POSIX time zone rule, e.g. "EST5EDT,M3.2.0,M11.1.0"
  bool h24 = false;           // 24-hour clock
  bool dmy = false;           // "Mon 5 Oct" instead of "Mon Oct 5"
  bool weekday = true;
  bool weather = false;       // show the weather (needs lat / lon)
  float lat = 0.0f, lon = 0.0f;
  char unit = 'f';            // 'f' or 'c'
  bool sun = false;           // sunrise / sunset instead of the day's high / low
  uint16_t wxMinutes = 30;    // weather refresh
  uint8_t bright = 80;        // percent
  uint8_t night = 1;          // 0 off, 1 dim, 2 dark (screen off)
  uint16_t nightStart = 23 * 60, nightEnd = 7 * 60;   // minutes after midnight
  uint8_t nightBright = 10;   // percent, when night = dim
  IdleMode idle = IdleMode::Clock;   // what it shows: the clock, or a game
};

class HomeScreen {
 public:
  explicit HomeScreen(U8G2& display) : u8g2_(display), games_(display) {}

  // Loads the saved settings and starts the weather task (it waits for WiFi).
  void begin();
  // Starts internet time (SNTP). Call after WiFi has been started.
  void startClock();
  // A `settings` message (JSON) from the brain or the console: applies and saves it. False if it doesn't parse.
  bool applyJson(const char* json, bool save = true);
  const HomeSettings& settings() const { return s_; }

  // Call every frameMs() while the home screen is up. Draws it (and handles night dimming).
  void draw();
  // How often draw() wants calling: twice a second for the clock, ~15 times for a game.
  uint32_t frameMs() const { return s_.idle == IdleMode::Clock || s_.idle == IdleMode::Roam ? 500 : 66; }
  // The "roam" idle screen, and not night (then it's the clock): the head should be looking around. Checks the
  // clock at most once a second.
  bool roamingNow();
  // Leaving the home screen: screen back on at the normal brightness (for the face).
  void restoreDisplay();
  // OLED contrast (0-255) for the face, from the brightness setting.
  uint8_t dayContrast() const { return static_cast<uint8_t>(s_.bright * 255 / 100); }

  void refreshWeather();      // fetch now (console `weather`)
  void printStatus();         // console `home`

 private:
  struct Weather {
    bool valid = false;
    float temp = 0, hi = 0, lo = 0;
    int code = -1;
    bool day = true;
    char sunrise[6] = "", sunset[6] = "";
    uint32_t fetchedMs = 0;
    char error[40] = "";
  };

  static void weatherTask(void* self);
  void weatherLoop();
  bool fetchWeather(Weather& out);
  void drawIcon(int code, bool day, int x, int y);
  bool isNight(const struct tm& t) const;

  U8G2& u8g2_;
  IdleGames games_;
  HomeSettings s_;
  Weather wx_;
  portMUX_TYPE lock_ = portMUX_INITIALIZER_UNLOCKED;   // s_ / wx_ are shared with the weather task
  TaskHandle_t task_ = nullptr;
  bool dark_ = false;         // screen switched off for the night
  uint32_t roamCheckMs_ = 0;
  bool roamDay_ = true;
  uint8_t contrast_ = 255;
};
