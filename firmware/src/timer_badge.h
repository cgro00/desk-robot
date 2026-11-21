#pragma once
// A countdown on the screen while one of the brain's timers runs: "pasta 7:55". The brain sends the soonest timer
// ({"type":"timer","left":475,"label":"pasta"}; left 0 clears it). The face draws it small in the top-right corner,
// the clock screen in place of the date. When it reaches 0:00 it blinks for a while, then goes away on its own.

#include <U8g2lib.h>

namespace timer_badge {

// Start (or replace) the countdown: secondsLeft from now; 0 clears it.
void set(uint32_t secondsLeft, const char* label);
// True while there's something to show (counting down, or blinking at 0:00).
bool active();
// "7:55", "1:02:03", "0:00" into buf; false when inactive.
bool text(char* buf, size_t len);
const char* label();
// The face's corner badge (inverted box, small font). Does nothing while inactive or on a blink-off frame.
void drawCorner(U8G2& u8g2);

}  // namespace timer_badge
