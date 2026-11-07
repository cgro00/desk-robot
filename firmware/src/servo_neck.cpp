#include "servo_neck.h"

#include <driver/mcpwm.h>
#include <soc/mcpwm_struct.h>

void ServoNeck::begin(uint8_t pin, float minDeg, float maxDeg,
                      float maxSpeedDegPerSec, float accelDegPerSec2, uint32_t relaxAfterMs,
                      float trimDeg, float glanceRangeDeg, bool invert,
                      float gearRatio) {
  pin_ = pin;
  minDeg_ = minDeg;
  maxDeg_ = maxDeg;
  maxSpeed_ = maxSpeedDegPerSec;
  accel_ = accelDegPerSec2;
  velocity_ = 0;
  relaxAfterMs_ = relaxAfterMs;
  trimDeg_ = trimDeg;
  glanceRange_ = glanceRangeDeg;
  invert_ = invert;
  gearRatio_ = gearRatio > 0.0f ? gearRatio : 1.0f;
  currentDeg_ = 0;
  targetDeg_ = 0;
  lastUpdateMs_ = millis();
  nextGlanceMs_ = millis() + 5000;
  // Claim the PWM output once. The library starts it straight away, before any angle is set (and claiming the
  // second servo restarts the timer the two share, zeroing the first one's pulse width), so take the pin back at
  // once: quiet until hold() or the first move starts the pulses properly. Both servos are begun before either
  // moves.
  servo_.setPeriodHertz(50);
  // 500-2400us covers the full travel of SG90/MG90S-class servos. On the S3
  // the library returns 100 + unit*10 + timer*2 + operator for an MCPWM output.
  int ch = servo_.attach(pin_, 500, 2400);
  mcpwmCode_ = ch >= 100 ? ch - 100 : 999;
  attached_ = true;
  relax(true);
}

// The MCPWM registers must be read as whole 32-bit words: read through the struct's 16-bit fields, the compiler may
// load just 16 bits, and the peripheral then answers 0.
static uint32_t timerCount(const mcpwm_dev_t& dev, int t) { return dev.timer[t].timer_status.val & 0xFFFF; }
static uint32_t timerPeriod(const mcpwm_dev_t& dev, int t) { return (dev.timer[t].timer_cfg0.val >> 8) & 0xFFFF; }

// This servo's MCPWM timer goes 0..period every 20 ms and the pulse is high from 0 to its width (at most 2.4 ms, 12%
// of the period). Wait until the count is well past the pulse and well before the next one. afterPeriodStart: let a
// period start go by first, so a pulse width just written (the hardware takes it at the period start) is in force.
void ServoNeck::waitForGap(bool afterPeriodStart) {
  if (mcpwmCode_ >= 100) return;   // LEDC: not used on the S3
  mcpwm_dev_t& dev = mcpwmCode_ / 10 ? MCPWM1 : MCPWM0;
  int t = (mcpwmCode_ % 10) / 2;
  uint32_t period = timerPeriod(dev, t);
  if (period == 0) return;
  uint32_t lo = period * 15 / 100, hi = period * 85 / 100;
  uint32_t last = timerCount(dev, t);
  bool started = !afterPeriodStart;
  for (uint32_t t0 = micros(); micros() - t0 < 45000;) {   // two periods at most
    uint32_t c = timerCount(dev, t);
    if (c < last) started = true;
    last = c;
    if (started && c > lo && c < hi) return;
    // Look again in 1 ms instead of spinning: the window is 14 ms wide, so a 1 ms step can't miss it, and the
    // screen and loop() get the CPU meanwhile (spinning here held the face up to 20 ms).
    vTaskDelay(1);
  }
}

uint32_t ServoNeck::pwmPeriodTicks() const {
  if (mcpwmCode_ >= 100) return 0;
  mcpwm_dev_t& dev = mcpwmCode_ / 10 ? MCPWM1 : MCPWM0;
  return timerPeriod(dev, (mcpwmCode_ % 10) / 2);
}

int ServoNeck::gapCheck(bool afterPeriodStart) {
  uint32_t period = pwmPeriodTicks();
  if (period == 0) return -1;
  waitForGap(afterPeriodStart);
  mcpwm_dev_t& dev = mcpwmCode_ / 10 ? MCPWM1 : MCPWM0;
  return static_cast<int>(timerCount(dev, (mcpwmCode_ % 10) / 2) * 1000 / period);
}

void ServoNeck::attachIfNeeded() {
  if (attached_) return;
  if (mcpwmCode_ < 100) {
    // Route our MCPWM output back to the pin: the pulse width first, then the pin, between two pulses. (Not a
    // detach/re-attach through the library: on the S3 it hands out operators by count, so a servo that re-attaches
    // while the other is still running can land on the other's operator, and then both get the same pulses.)
    writeAngle(currentDeg_);
    waitForGap(true);
    int unit = mcpwmCode_ / 10, op = mcpwmCode_ % 2;
    mcpwm_io_signals_t sig = unit == 0 ? (op == 0 ? MCPWM0A : MCPWM0B) : (op == 0 ? MCPWM1A : MCPWM1B);
    mcpwm_gpio_init(static_cast<mcpwm_unit_t>(unit), sig, pin_);
  } else {
    servo_.attach(pin_, 500, 2400);  // LEDC output (not expected on the S3)
    writeAngle(currentDeg_);
  }
  attached_ = true;
}

void ServoNeck::relax(bool now) {
  if (!attached_) return;
  // Take the pin off the PWM and hold it low: no pulses, so the servo goes quiet (the library's detach() leaves
  // MCPWM driving the pin on the S3). Between two pulses, unless `now` (at begin, before any angle was set).
  if (!now) waitForGap(false);
  digitalWrite(pin_, LOW);   // the level the pin takes the moment it leaves the PWM
  pinMode(pin_, OUTPUT);
  digitalWrite(pin_, LOW);
  if (mcpwmCode_ >= 100) servo_.detach();
  attached_ = false;
  relaxedSinceMs_ = millis();
}

void ServoNeck::writeAngle(float deg) {
  // Head degrees → servo degrees (through the gear ratio) → microseconds;
  // 0 head-deg = servo center. The trim is in servo degrees.
  float servoDeg = (invert_ ? -deg : deg) / gearRatio_ + trimDeg_;
  float t = (servoDeg + 90.0f) / 180.0f;
  int us = 500 + static_cast<int>(t * 1900.0f);
  servo_.writeMicroseconds(constrain(us, 500, 2400));
}

void ServoNeck::setRaw(float deg) {
  float reach = 90.0f * gearRatio_;   // the servo's whole travel, in head degrees
  deg = constrain(deg, -reach, reach);
  attachIfNeeded();
  currentDeg_ = targetDeg_ = deg;
  velocity_ = 0;
  settledSinceMs_ = 0;
  writeAngle(deg);
}

void ServoNeck::hold() {
  bool wasAttached = attached_;
  attachIfNeeded();
  if (!wasAttached) writeAngle(currentDeg_);  // put it back where it belongs
  settledSinceMs_ = 0;                        // and don't relax while held
}

void ServoNeck::setTarget(float deg) {
  targetDeg_ = constrain(deg, minDeg_, maxDeg_);
  settledSinceMs_ = 0;
  attachIfNeeded();
}

void ServoNeck::update(uint32_t nowMs) {
  float dt = (nowMs - lastUpdateMs_) / 1000.0f;
  lastUpdateMs_ = nowMs;
  if (dt <= 0 || dt > 0.5f) dt = 0.02f;

  if (idleGlances_ && nowMs >= nextGlanceMs_) {
    // Small wander, biased back toward center.
    int range = static_cast<int>(glanceRange_);
    float glance = static_cast<float>(random(-range, range + 1));
    if (random(100) < 50) glance = 0;
    setTarget(glance);
    nextGlanceMs_ = nowMs + random(6000, 16000);
  }

  float diff = targetDeg_ - currentDeg_;
  if (fabsf(diff) > 0.05f || fabsf(velocity_) > 0.5f) {
    // The fastest speed from which we can still stop exactly on the target at
    // accel_ is about sqrt(2*a*d) (the h terms correct it for stepping every dt
    // instead of continuously). Aim for that, capped at maxSpeed_, and change the
    // velocity toward it by at most accel_*dt per tick. Speeding up, cruising,
    // slowing down and bending toward a moved target all fall out of this.
    float dist = fabsf(diff);
    float h = accel_ * dt * 0.5f;
    float want = fminf(maxSpeed_, sqrtf(h * h + 2.0f * accel_ * dist) - h) * (diff > 0 ? 1.0f : -1.0f);
    float dv = constrain(want - velocity_, -accel_ * dt, accel_ * dt);
    velocity_ += dv;
    float step = velocity_ * dt;
    if ((diff > 0 && step >= diff) || (diff < 0 && step <= diff) || (dist < 0.05f && fabsf(velocity_) < 3.0f)) {
      currentDeg_ = targetDeg_;   // arrive exactly, no overshoot
      velocity_ = 0;
    } else {
      currentDeg_ += step;
    }
    attachIfNeeded();
    writeAngle(currentDeg_);
    settledSinceMs_ = 0;
  } else if (attached_ && relaxAfterMs_ > 0 && currentDeg_ >= relaxLo_ && currentDeg_ <= relaxHi_) {
    if (settledSinceMs_ == 0) {
      settledSinceMs_ = nowMs;
    } else if (nowMs - settledSinceMs_ >= relaxAfterMs_) {
      relax();   // at rest long enough: stop driving the servo so it doesn't buzz
    }
  } else if (!attached_ && refreshMs_ > 0 && nowMs - relaxedSinceMs_ >= refreshMs_) {
    // Quiet a while: drive it briefly at its angle, which puts back a head that slipped and is silent otherwise.
    attachIfNeeded();
    settledSinceMs_ = nowMs - relaxAfterMs_ + refreshBurstMs_;   // relax again after the burst
  }
}
