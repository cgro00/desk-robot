#include "timer_badge.h"

#include <Arduino.h>

namespace timer_badge {

static volatile uint32_t endMs = 0;   // 0 = no timer
static volatile bool on = false;
static char lbl[17] = "";
static const uint32_t RING_SHOW_MS = 30000;   // blink "0:00" this long after it runs out

void set(uint32_t secondsLeft, const char* label) {
  if (secondsLeft == 0) { on = false; return; }
  strlcpy(lbl, label ? label : "", sizeof(lbl));
  endMs = millis() + secondsLeft * 1000UL;
  on = true;
}

static int32_t msLeft() { return static_cast<int32_t>(endMs - millis()); }

bool active() {
  if (!on) return false;
  if (msLeft() < -static_cast<int32_t>(RING_SHOW_MS)) { on = false; return false; }
  return true;
}

const char* label() { return lbl; }

bool text(char* buf, size_t len) {
  if (!active()) return false;
  int32_t ms = msLeft();
  uint32_t s = ms > 0 ? (static_cast<uint32_t>(ms) + 999) / 1000 : 0;   // round up: shows 0:00 only when done
  if (s >= 3600) snprintf(buf, len, "%lu:%02lu:%02lu", (unsigned long)(s / 3600), (unsigned long)(s / 60 % 60),
                          (unsigned long)(s % 60));
  else snprintf(buf, len, "%lu:%02lu", (unsigned long)(s / 60), (unsigned long)(s % 60));
  return true;
}

void drawCorner(U8G2& u8g2) {
  char buf[12];
  if (!text(buf, sizeof(buf))) return;
  if (msLeft() <= 0 && (millis() / 400) % 2) return;   // ringing: blink
  u8g2.setFont(u8g2_font_helvB08_tr);
  int w = u8g2.getStrWidth(buf) + 4;
  int x = 128 - w;
  u8g2.setDrawColor(1);
  u8g2.drawRBox(x, 0, w, 11, 2);
  u8g2.setDrawColor(0);
  u8g2.drawStr(x + 2, 9, buf);
  u8g2.setDrawColor(1);
}

}  // namespace timer_badge
