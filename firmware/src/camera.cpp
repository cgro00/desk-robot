#include "camera.h"

#include <esp_camera.h>

#include "config.h"

// XIAO ESP32S3 Sense camera wiring (from the Arduino core's camera_pins.h).
static const int PIN_PWDN = -1, PIN_RESET = -1, PIN_XCLK = 10;
static const int PIN_SIOD = 40, PIN_SIOC = 39;
static const int PIN_Y9 = 48, PIN_Y8 = 11, PIN_Y7 = 12, PIN_Y6 = 14;
static const int PIN_Y5 = 16, PIN_Y4 = 18, PIN_Y3 = 17, PIN_Y2 = 15;
static const int PIN_VSYNC = 38, PIN_HREF = 47, PIN_PCLK = 13;

static const size_t LATEST_CAP = 64 * 1024;  // stream JPEGs: QVGA ~10 KB, VGA ~25 KB
static const size_t SNAP_CAP = 480 * 1024;   // a UXGA still is ~100-300 KB

struct SnapSize {
  const char* name;
  framesize_t size;
};
static const SnapSize kSnapSizes[] = {
    {"vga", FRAMESIZE_VGA}, {"svga", FRAMESIZE_SVGA}, {"xga", FRAMESIZE_XGA},
    {"sxga", FRAMESIZE_SXGA}, {"uxga", FRAMESIZE_UXGA},
};

bool Camera::begin() {
  streamSize_ = FRAMESIZE_QVGA;
  latest_ = static_cast<uint8_t*>(ps_malloc(LATEST_CAP));
  latestCap_ = latest_ ? LATEST_CAP : 0;
  snap_ = static_cast<uint8_t*>(ps_malloc(SNAP_CAP));
  if (latest_ == nullptr) {
    Serial.println(F("camera: no PSRAM for frames"));
    return false;
  }
  for (int i = 0; i < 3 && !ok_; ++i) {   // a supply dip at boot can make the first probe miss
    if (i > 0) { esp_camera_deinit(); delay(200); }
    ok_ = initSensor();
  }
  if (!ok_) Serial.println(F("camera: init failed, will keep retrying"));
  lastGoodMs_ = lastInitTryMs_ = millis();
  xTaskCreatePinnedToCore(taskEntry, "camera", 4096, this, 1, nullptr, 0);
  return ok_;
}

void Camera::restart(const char* why) {
  esp_camera_deinit();
  vTaskDelay(pdMS_TO_TICKS(100));
  ok_ = initSensor();
  restarts_ = restarts_ + 1;
  lastGoodMs_ = lastInitTryMs_ = millis();
  Serial.printf("camera: %s -> restart %s (#%u)\n", why, ok_ ? "ok" : "failed", static_cast<unsigned>(restarts_));
}

bool Camera::initSensor() {
  camera_config_t cfg = {};
  cfg.ledc_channel = LEDC_CHANNEL_4;  // 0-3 are taken by the servos
  cfg.ledc_timer = LEDC_TIMER_2;
  cfg.pin_d0 = PIN_Y2;
  cfg.pin_d1 = PIN_Y3;
  cfg.pin_d2 = PIN_Y4;
  cfg.pin_d3 = PIN_Y5;
  cfg.pin_d4 = PIN_Y6;
  cfg.pin_d5 = PIN_Y7;
  cfg.pin_d6 = PIN_Y8;
  cfg.pin_d7 = PIN_Y9;
  cfg.pin_xclk = PIN_XCLK;
  cfg.pin_pclk = PIN_PCLK;
  cfg.pin_vsync = PIN_VSYNC;
  cfg.pin_href = PIN_HREF;
  cfg.pin_sccb_sda = PIN_SIOD;
  cfg.pin_sccb_scl = PIN_SIOC;
  cfg.pin_pwdn = PIN_PWDN;
  cfg.pin_reset = PIN_RESET;
  cfg.xclk_freq_hz = 20000000;
  cfg.pixel_format = PIXFORMAT_JPEG;
  // Buffers sized for the biggest still (UXGA, 1600x1200); the stream runs at VGA or QVGA (the brain picks) and a
  // snapshot switches up for one frame.
  cfg.frame_size = FRAMESIZE_UXGA;
  cfg.jpeg_quality = 12;            // 0-63, lower = better; 12 is ~25 KB for a VGA frame
  cfg.fb_count = 2;
  cfg.fb_location = CAMERA_FB_IN_PSRAM;
  cfg.grab_mode = CAMERA_GRAB_LATEST;

  if (esp_camera_init(&cfg) != ESP_OK) return false;
  sensor_t* s = esp_camera_sensor_get();
  if (s != nullptr) {
    s->set_vflip(s, CAMERA_VFLIP ? 1 : 0);
    s->set_hmirror(s, CAMERA_HMIRROR ? 1 : 0);
    s->set_framesize(s, static_cast<framesize_t>(streamSize_));
    switch (s->id.PID) {
      case OV2640_PID: strlcpy(model_, "OV2640", sizeof(model_)); break;
      case OV3660_PID: strlcpy(model_, "OV3660", sizeof(model_)); break;
      case OV5640_PID: strlcpy(model_, "OV5640", sizeof(model_)); break;
      default: snprintf(model_, sizeof(model_), "PID %04x", s->id.PID); break;
    }
  }

  return true;
}

void Camera::setStreaming(bool on, float fps) {
  if (fps < 0.5f) fps = 0.5f;
  if (fps > 20.0f) fps = 20.0f;
  intervalMs_ = static_cast<uint32_t>(1000.0f / fps);
  streaming_ = on;
}

size_t Camera::takeFrame(uint8_t* out, size_t cap) {
  size_t n = 0;
  portENTER_CRITICAL(&lock_);
  if (fresh_ && latestLen_ <= cap) {
    n = latestLen_;
    memcpy(out, latest_, n);
    fresh_ = false;
  }
  portEXIT_CRITICAL(&lock_);
  return n;
}

bool Camera::setStreamSize(const char* size) {
  framesize_t f;
  if (!strcasecmp(size, "qvga")) f = FRAMESIZE_QVGA;
  else if (!strcasecmp(size, "vga")) f = FRAMESIZE_VGA;
  else return false;
  if (f != streamSize_) {
    streamSize_ = f;
    streamSizeChanged_ = true;   // the camera task applies it
  }
  return true;
}

const char* Camera::streamSize() const { return streamSize_ == FRAMESIZE_VGA ? "vga" : "qvga"; }

bool Camera::requestSnapshot(const char* size) {
  for (const SnapSize& s : kSnapSizes) {
    if (!strcasecmp(size, s.name)) {
      if (snap_ == nullptr || !ok_) return false;
      snapReady_ = false;
      snapWanted_ = s.size;
      return true;
    }
  }
  return false;
}

// In the camera task: switch up, let the sensor settle (the first frames after a size change are the old size or
// half-exposed), keep one, switch back.
void Camera::takeSnapshot() {
  framesize_t want = static_cast<framesize_t>(snapWanted_);
  snapWanted_ = -1;
  sensor_t* s = esp_camera_sensor_get();
  if (s == nullptr) return;
  uint32_t t0 = millis();
  s->set_framesize(s, want);
  for (int i = 0; i < 2; ++i) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) esp_camera_fb_return(fb);
  }
  camera_fb_t* fb = esp_camera_fb_get();
  if (fb != nullptr) {
    if (fb->len <= SNAP_CAP) {
      memcpy(snap_, fb->buf, fb->len);
      snapLen_ = fb->len;
      snapMs_ = millis() - t0;
      snapReady_ = true;
    }
    esp_camera_fb_return(fb);
  }
  s->set_framesize(s, static_cast<framesize_t>(streamSize_));
  fb = esp_camera_fb_get();  // drop the frame already on its way at the big size
  if (fb) esp_camera_fb_return(fb);
  lastGoodMs_ = millis();
}

void Camera::taskEntry(void* self) { static_cast<Camera*>(self)->task(); }

void Camera::task() {
  for (;;) {
    uint32_t now = millis();
    if (!ok_) {                                   // not up yet (or a restart failed): retry every 3 s
      if (now - lastInitTryMs_ >= 3000) restart("not running");
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    if (snapWanted_ >= 0) {
      takeSnapshot();
      continue;
    }
    if (streamSizeChanged_) {
      streamSizeChanged_ = false;
      sensor_t* s = esp_camera_sensor_get();
      if (s != nullptr) s->set_framesize(s, static_cast<framesize_t>(streamSize_));
      camera_fb_t* fb = esp_camera_fb_get();   // the old size, still in the pipe
      if (fb) esp_camera_fb_return(fb);
      lastGoodMs_ = millis();
      continue;
    }
    if (!streaming_) {
      lastGoodMs_ = now;                          // idle is not a stall
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }
    uint32_t t0 = millis();
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb != nullptr) {
      if (fb->len <= latestCap_) {
        portENTER_CRITICAL(&lock_);
        memcpy(latest_, fb->buf, fb->len);
        latestLen_ = fb->len;
        fresh_ = true;
        portEXIT_CRITICAL(&lock_);
        captured_ = captured_ + 1;
      }
      esp_camera_fb_return(fb);
      lastGoodMs_ = millis();
    } else if (millis() - lastGoodMs_ > 2500) {
      restart("no frames for 2.5 s");
      continue;
    }
    uint32_t spent = millis() - t0;
    uint32_t interval = intervalMs_;
    vTaskDelay(pdMS_TO_TICKS(spent < interval ? interval - spent : 1));
  }
}
