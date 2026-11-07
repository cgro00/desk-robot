#include "speaker.h"

#include <driver/i2s.h>

// I2S1: the PDM mic needs I2S0 (the only port with PDM receive on the S3).
static const i2s_port_t I2S_PORT = I2S_NUM_1;
static const int SAMPLE_RATE = 16000;
static const size_t RING_BYTES = 1024 * 1024;  // 32 s of audio, in PSRAM
static const size_t MIN_PREBUFFER = 6400;      // 200 ms, if the size is unknown...
static const size_t START_PREBUFFER = 12800;   // ...but 400 ms at power-on, while the WiFi link settles...
static const size_t GAPPY_PREBUFFER = 19200;   // ...and up to 600 ms while lines keep running dry
static const size_t MAX_PREBUFFER = 32000;     // 1 s: never wait longer than this
static const size_t CHUNK_SAMPLES = 256;

void Speaker::begin(uint8_t bclkPin, uint8_t lrcPin, uint8_t dinPin,
                    float volume, DoneHandler onDone) {
  volume_ = constrain(volume, 0.0f, 1.0f);
  onDone_ = std::move(onDone);

  ring_ = static_cast<uint8_t*>(ps_malloc(RING_BYTES));
  if (ring_ == nullptr) {
    // No PSRAM? Fall back to a small internal buffer (~2 s).
    ringSize_ = 64 * 1024;
    ring_ = static_cast<uint8_t*>(malloc(ringSize_));
  } else {
    ringSize_ = RING_BYTES;
  }

  i2s_config_t cfg = {};
  cfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
  cfg.sample_rate = SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  // Send the same sample on both channels; the MAX98357A with SD floating
  // plays (L+R)/2, so this comes out at full level.
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = CHUNK_SAMPLES;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = true;  // silence, not garbage, when we underrun
  i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);

  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = bclkPin;
  pins.ws_io_num = lrcPin;
  pins.data_out_num = dinPin;
  pins.data_in_num = I2S_PIN_NO_CHANGE;
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
  // Idle with the clocks stopped: the MAX98357A goes to standby and ignores
  // noise coupled onto its data line (camera/WiFi bursts were audible as
  // random clicks). Clocks restart when a reply begins.
  i2s_stop(I2S_PORT);

  xTaskCreatePinnedToCore(taskEntry, "speaker", 4096, this, 3, nullptr, 0);
}

void Speaker::beginSpeech(size_t expectedBytes) {
  i2s_zero_dma_buffer(I2S_PORT);
  i2s_start(I2S_PORT);  // amp wakes while we pre-buffer
  portENTER_CRITICAL(&lock_);
  head_ = tail_ = 0;
  ended_ = false;
  expectedBytes_ = expectedBytes;
  underruns_ = 0;
  prebuffered_ = 0;
  speechStartMs_ = millis();
  speaking_ = true;
  portEXIT_CRITICAL(&lock_);
}

void Speaker::feed(const uint8_t* pcm, size_t len) {
  if (!speaking_ || ring_ == nullptr) return;
  portENTER_CRITICAL(&lock_);
  size_t used = (head_ + ringSize_ - tail_) % ringSize_;
  size_t space = ringSize_ - 1 - used;
  if (len > space) len = space;  // drop the tail rather than wrap over unread audio
  for (size_t i = 0; i < len; ++i) {
    ring_[head_] = pcm[i];
    head_ = (head_ + 1) % ringSize_;
  }
  portEXIT_CRITICAL(&lock_);
}

void Speaker::endSpeech() { ended_ = true; }

size_t Speaker::available() const {
  return (head_ + ringSize_ - tail_) % ringSize_;
}

size_t Speaker::readInto(uint8_t* out, size_t maxLen) {
  portENTER_CRITICAL(&lock_);
  size_t n = min(maxLen, available());
  for (size_t i = 0; i < n; ++i) {
    out[i] = ring_[tail_];
    tail_ = (tail_ + 1) % ringSize_;
  }
  portEXIT_CRITICAL(&lock_);
  return n;
}

void Speaker::taskEntry(void* self) { static_cast<Speaker*>(self)->task(); }

void Speaker::task() {
  static uint8_t mono[CHUNK_SAMPLES * 2];
  static int16_t stereo[CHUNK_SAMPLES * 2];
  bool started = false;

  for (;;) {
    if (!speaking_) {
      started = false;
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    size_t avail = available();
    if (!started) {
      // Buffer the whole line if it's short, else up to 1 s, before playing.
      // The brain sends the entire reply at once, so this costs little
      // latency and rides out WiFi hiccups.
      size_t want = expectedBytes_ ? min(expectedBytes_, MAX_PREBUFFER) : prebufferBytes_;
      if (avail < want && !ended_) {
        vTaskDelay(pdMS_TO_TICKS(5));
        continue;
      }
      started = true;
      prebuffered_ = avail;
      prebufferMs_ = millis() - speechStartMs_;
    }
    if (avail == 0) {
      if (ended_) {
        // Done: let the DMA drain, then stop the clocks so the amp sleeps.
        vTaskDelay(pdMS_TO_TICKS(80));
        i2s_zero_dma_buffer(I2S_PORT);
        i2s_stop(I2S_PORT);
        // The next line's head start: longer after a line that ran dry (100 ms more), back toward 200 ms after
        // clean ones (50 ms a line). Gaps cost more than the wait does.
        if (underruns_ > 0) {
          prebufferBytes_ = min(prebufferBytes_ + 3200, GAPPY_PREBUFFER);
        } else if (prebufferBytes_ > MIN_PREBUFFER) {
          prebufferBytes_ = max(prebufferBytes_ - 1600, MIN_PREBUFFER);
        }
        speaking_ = false;
        level_ = 0;
        if (onDone_) onDone_();
      } else {
        underruns_ = underruns_ + 1;  // ran dry mid-line: audible gap
        vTaskDelay(pdMS_TO_TICKS(5));
      }
      continue;
    }

    size_t n = readInto(mono, sizeof(mono)) / 2;  // samples
    const int16_t* in = reinterpret_cast<const int16_t*>(mono);
    float sumSq = 0;
    for (size_t i = 0; i < n; ++i) {
      int16_t s = static_cast<int16_t>(in[i] * volume_);
      stereo[2 * i] = s;
      stereo[2 * i + 1] = s;
      float f = in[i] / 32768.0f;
      sumSq += f * f;
    }
    if (n > 0) level_ = 0.6f * level_ + 0.4f * sqrtf(sumSq / n);
    size_t written = 0;
    i2s_write(I2S_PORT, stereo, n * 2 * sizeof(int16_t), &written, portMAX_DELAY);
  }
}
