#pragma once

#include <Arduino.h>
#include <ESP32Servo.h>

// One head axis (pan or tilt): smooth motion, auto-relax.
//
// Motion is speed- AND acceleration-limited: the head speeds up gently,
// cruises at up to maxSpeed, and slows down gently to stop on the target. A new
// target mid-move just bends the motion (no restart from standstill), so face
// tracking, which retargets ~10x a second, stays fluid. Call update() at a
// steady rate; 50 Hz matches the servo's own pulse rate.
//
// Angles are in "head degrees": 0 = centered. For pan, negative = robot's
// left; for tilt, negative = down. The class maps that onto the servo's
// 0-180 range, with an optional trim offset, direction flip and gear ratio.
//
// Pulses start and stop only in the gap between two pulses. Cutting the pin
// over mid-pulse sends the servo one short pulse, and a short pulse means "go
// to the end of your travel": the servo kicks toward it.

class ServoNeck {
 public:
  // gearRatio = head degrees per servo degree (38/30 for the V2 geared pan, 1 for
  // a servo that drives the head directly).
  void begin(uint8_t pin, float minDeg, float maxDeg, float maxSpeedDegPerSec, float accelDegPerSec2,
             uint32_t relaxAfterMs, float trimDeg = 0.0f,
             float glanceRangeDeg = 25.0f, bool invert = false,
             float gearRatio = 1.0f);

  void setTarget(float deg);
  // Calibration only: drive straight to a head angle, ignoring the limits (but
  // not past the servo's own +-90). Use it to find which way the servo turns and
  // where its travel ends before trusting the limits.
  void setRaw(float deg);
  // Keep this axis powered at its current angle. An unpowered servo sags
  // under the head's weight when the other axis swings; call this while the
  // other axis is moving.
  void hold();
  bool moving() const { return fabsf(targetDeg_ - currentDeg_) > 0.25f || fabsf(velocity_) > 1.0f; }
  float current() const { return currentDeg_; }
  // Live tuning (console `motion pan|tilt <maxSpeed> <accel>`); head deg/s and deg/s^2.
  void setMotion(float maxSpeed, float accel) { maxSpeed_ = maxSpeed; accel_ = accel; }
  float maxSpeed() const { return maxSpeed_; }
  float accel() const { return accel_; }
  float target() const { return targetDeg_; }

  // When idle glances are on, the head occasionally turns a little on its
  // own — paired with the face saccades it makes the robot feel alive.
  void setIdleGlances(bool on) { idleGlances_ = on; }
  // Only go quiet (relax) at rest between these head angles; outside them the
  // servo keeps holding. For tilt: the head's weight sits above the axis, so
  // an unpowered tilt servo lets a tipped head fall.
  void setRelaxRange(float lo, float hi) { relaxLo_ = lo; relaxHi_ = hi; }
  // While relaxed, drive the servo for burstMs every periodMs, so a head that
  // got knocked or slipped goes back where it belongs. 0 = never.
  void setRefresh(uint32_t periodMs, uint32_t burstMs) { refreshMs_ = periodMs; refreshBurstMs_ = burstMs; }
  bool powered() const { return attached_; }
  // Console `servo`: where in its 20 ms period this servo's PWM is right after waiting for the gap between pulses,
  // in thousandths (the pulse itself is the first 25-120). -1 if it isn't on MCPWM.
  int gapCheck(bool afterPeriodStart);
  uint32_t pwmPeriodTicks() const;

  // Call every frame.
  void update(uint32_t nowMs);

 private:
  void writeAngle(float deg);
  void attachIfNeeded();   // start the pulses (at the current angle), if they're off
  void relax(bool now = false);   // stop them: the pin held low, the servo goes quiet
  void waitForGap(bool afterPeriodStart);

  Servo servo_;
  uint8_t pin_ = 0;
  float minDeg_ = -60, maxDeg_ = 60;
  float maxSpeed_ = 180; // head deg/sec
  float accel_ = 600;    // head deg/sec^2
  float velocity_ = 0;   // head deg/sec, signed
  uint32_t relaxAfterMs_ = 1500;
  float relaxLo_ = -1000, relaxHi_ = 1000;   // relax anywhere unless set
  uint32_t refreshMs_ = 0, refreshBurstMs_ = 0;
  uint32_t relaxedSinceMs_ = 0;
  float trimDeg_ = 0;
  float glanceRange_ = 25;
  bool invert_ = false;
  float gearRatio_ = 1.0f;

  float currentDeg_ = 0;
  float targetDeg_ = 0;
  bool attached_ = false;   // pulses going out on the pin
  int mcpwmCode_ = -1;      // MCPWM output claimed once at begin (unit*10+timer*2+op), 999 = LEDC
  uint32_t settledSinceMs_ = 0;
  uint32_t lastUpdateMs_ = 0;
  uint32_t nextGlanceMs_ = 0;
  bool idleGlances_ = true;
};
