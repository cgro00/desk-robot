#include "mic.h"

#include <driver/i2s.h>

// PDM receive only works on I2S0 on the ESP32-S3 (the speaker uses I2S1).
static const i2s_port_t I2S_PORT = I2S_NUM_0;
static const int SAMPLE_RATE = 16000;

void Mic::begin(uint8_t clkPin, uint8_t dataPin, float gain) {
  gain_ = gain;
  // ~2 s of frames, in PSRAM: loop() sometimes waits half a second on WiFi (a camera picture going out), and a
  // 0.5 s queue then dropped the start of what was said. Tasks only touch the queue (no ISR), so PSRAM is fine.
  static const UBaseType_t FRAMES = 64;
  static StaticQueue_t queueState;
  if (uint8_t* storage = static_cast<uint8_t*>(ps_malloc(FRAMES * FRAME_BYTES))) {
    queue_ = xQueueCreateStatic(FRAMES, FRAME_BYTES, storage, &queueState);
  } else {
    queue_ = xQueueCreate(16, FRAME_BYTES);   // no PSRAM: ~0.5 s in internal RAM
  }

  i2s_config_t cfg = {};
  cfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM);
  cfg.sample_rate = SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 256;
  i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);

  // In PDM mode the clock rides on the WS pin.
  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = I2S_PIN_NO_CHANGE;
  pins.ws_io_num = clkPin;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = dataPin;
  i2s_set_pin(I2S_PORT, &pins);

  xTaskCreatePinnedToCore(taskEntry, "mic", 4096, this, 2, nullptr, 0);
}

bool Mic::nextFrame(uint8_t* out) {
  return queue_ != nullptr && xQueueReceive(queue_, out, 0) == pdTRUE;
}

void Mic::taskEntry(void* self) { static_cast<Mic*>(self)->task(); }

void Mic::task() {
  static int16_t frame[FRAME_SAMPLES];
  for (;;) {
    size_t got = 0;
    i2s_read(I2S_PORT, frame, sizeof(frame), &got, portMAX_DELAY);
    size_t n = got / 2;
    if (n == 0) continue;

    float sumSq = 0;
    for (size_t i = 0; i < n; ++i) {
      float s = frame[i] * gain_;
      s = constrain(s, -32768.0f, 32767.0f);
      frame[i] = static_cast<int16_t>(s);
      sumSq += (s / 32768.0f) * (s / 32768.0f);
    }
    level_ = sqrtf(sumSq / n);

    if (streaming_ && n == FRAME_SAMPLES) {
      // If loop() has fallen behind, drop the oldest frame rather than block.
      if (uxQueueSpacesAvailable(queue_) == 0) {
        static uint8_t scratch[FRAME_BYTES];
        xQueueReceive(queue_, scratch, 0);
      }
      xQueueSend(queue_, frame, 0);
    }
  }
}
