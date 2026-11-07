#pragma once

#include <Arduino.h>
#include <functional>

// Speaker: plays 16 kHz mono s16le PCM through a MAX98357A over I2S.
//
// The brain streams a whole reply as binary WebSocket frames. They land in a
// big ring buffer in PSRAM; a playback task drains it into the I2S DMA. A
// short pre-buffer hides WiFi jitter at the start of a line.
//
//   beginSpeech()  → reset the buffer, start waiting for audio
//   feed(pcm, n)   → append audio (any chunk size)
//   endSpeech()    → no more audio coming; play out what's left, then idle
//   onDone         → called (from the playback task) when playback finishes

class Speaker {
 public:
  using DoneHandler = std::function<void()>;

  void begin(uint8_t bclkPin, uint8_t lrcPin, uint8_t dinPin, float volume,
             DoneHandler onDone = nullptr);
  // expectedBytes: total size of the reply if the brain knows it (0 = unknown).
  // Playback starts once that much (capped at ~1 s) has arrived.
  void beginSpeech(size_t expectedBytes = 0);
  void feed(const uint8_t* pcm, size_t len);
  void endSpeech();
  bool speaking() const { return speaking_; }
  void setVolume(float v) { volume_ = constrain(v, 0.0f, 1.0f); }
  // Loudness (RMS 0..1) of the audio being played right now; 0 when silent.
  float level() const { return speaking_ ? level_ : 0.0f; }

  // Diagnostics for the last line played: times the buffer ran dry mid-line,
  // bytes buffered before playback started, ms spent waiting for that.
  uint32_t lastUnderruns() const { return underruns_; }
  size_t lastPrebuffered() const { return prebuffered_; }
  uint32_t lastPrebufferMs() const { return prebufferMs_; }
  // How much the next line buffers before it plays, in ms (adapts: see Speaker::task).
  uint32_t headStartMs() const { return prebufferBytes_ / 32; }

 private:
  static void taskEntry(void* self);
  void task();
  size_t available() const;
  size_t readInto(uint8_t* out, size_t maxLen);

  uint8_t* ring_ = nullptr;
  size_t ringSize_ = 0;
  volatile size_t head_ = 0;  // write index
  volatile size_t tail_ = 0;  // read index
  volatile bool speaking_ = false;
  volatile bool ended_ = false;
  size_t expectedBytes_ = 0;
  volatile uint32_t underruns_ = 0;   // 5 ms polls that found the buffer dry mid-line
  size_t prebufferBytes_ = 12800;     // START_PREBUFFER
  volatile size_t prebuffered_ = 0;
  volatile uint32_t prebufferMs_ = 0;
  uint32_t speechStartMs_ = 0;
  float volume_ = 0.5f;
  volatile float level_ = 0.0f;
  DoneHandler onDone_;
  portMUX_TYPE lock_ = portMUX_INITIALIZER_UNLOCKED;
};
