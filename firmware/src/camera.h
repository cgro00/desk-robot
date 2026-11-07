#pragma once

#include <Arduino.h>

// Camera: the Sense board's OV3660 (or an older board's OV2640) → a stream of JPEGs, and a sharp still on request.
//
// A capture task grabs frames at the requested rate and keeps only the
// latest one (copied into PSRAM). loop() picks it up with takeFrame() and
// ships it to the brain, so a slow WiFi moment just skips frames instead
// of piling them up.
//
// The camera browns out if the supply dips (two servos starting together on
// USB power is enough). The capture task therefore restarts the sensor on its
// own: if it didn't come up at boot (retry every 3 s), or if a stream stops
// delivering frames for 2.5 s.

class Camera {
 public:
  bool begin();  // false if the camera didn't come up (it keeps retrying)
  bool ok() const { return ok_; }
  uint32_t restarts() const { return restarts_; }

  void setStreaming(bool on, float fps);
  // Stream frame size: "qvga" (320x240) or "vga" (640x480). False if unknown.
  bool setStreamSize(const char* size);
  const char* streamSize() const;
  bool streaming() const { return streaming_; }

  // Copies the newest unsent JPEG into `out` (capacity `cap`); returns its
  // size, or 0 if there's nothing new or it didn't fit.
  size_t takeFrame(uint8_t* out, size_t cap);

  uint32_t framesCaptured() const { return captured_; }

  // A sharp still for the brain to look at ("vga" "svga" "xga" "sxga" "uxga"); the stream pauses for it (~0.3 s).
  // False if the size is unknown. When it's ready, snapshotLen() > 0: send snapshotData(), then snapshotDone().
  bool requestSnapshot(const char* size);
  size_t snapshotLen() const { return snapReady_ ? snapLen_ : 0; }
  const uint8_t* snapshotData() const { return snap_; }
  uint32_t snapshotMs() const { return snapMs_; }
  void snapshotDone() { snapReady_ = false; }
  const char* model() const { return model_; }

 private:
  static void taskEntry(void* self);
  void task();
  bool initSensor();   // esp_camera_init + orientation; only from begin() or the task
  void restart(const char* why);
  void takeSnapshot();

  volatile bool ok_ = false;
  volatile uint32_t restarts_ = 0;
  uint32_t lastGoodMs_ = 0;
  uint32_t lastInitTryMs_ = 0;
  volatile bool streaming_ = false;
  volatile uint32_t intervalMs_ = 100;
  uint8_t* latest_ = nullptr;      // PSRAM
  size_t latestCap_ = 0;
  volatile size_t latestLen_ = 0;
  volatile bool fresh_ = false;
  volatile uint32_t captured_ = 0;
  portMUX_TYPE lock_ = portMUX_INITIALIZER_UNLOCKED;
  volatile int snapWanted_ = -1;   // a framesize_t, or -1
  volatile int streamSize_ = 0;    // framesize_t of the stream (set to FRAMESIZE_QVGA in begin())
  volatile bool streamSizeChanged_ = false;
  uint8_t* snap_ = nullptr;        // PSRAM
  volatile size_t snapLen_ = 0;
  volatile bool snapReady_ = false;
  volatile uint32_t snapMs_ = 0;
  char model_[12] = "?";
};
