#pragma once

#include <Arduino.h>
#include <U8g2lib.h>

// Expressive two-eye face for a 128x64 mono OLED, drawn from a face pack.
//
// A pack gives every emotion a shape for each eye (size, corner rounding, slanted edges, eyelids, cheeks) and a
// set of flourishes (blush, tears, hearts, "?"...). The face morphs smoothly from one shape to the next and adds
// life on top: blinks, glances, pupils that look around, a mouth that moves with the speaker's audio, and a sleep
// state with slow breathing and floating Z's.
//
// Packs come from the brain (server/faces/*.json, picked in the dashboard) and are kept in flash. Every pixel is
// worked out with plain arithmetic (no library shapes or fonts), the same way the dashboard's preview
// (server/brain/dashboard.html, faceRender) does it, so the preview matches the robot's screen. Change one, change
// the other.

enum class Emotion : uint8_t {
  Neutral,
  Happy,
  Sad,
  Angry,
  Surprised,
  Sleepy,
  Thinking,
  Curious,
  Skeptical,
  Excited,
  Love,
  Confused,
  COUNT,
};

const char* emotionName(Emotion e);
bool emotionFromName(const char* name, Emotion& out);

// One eye. Pixels. "Inner" is the side toward the other eye.
struct EyeShape {
  float w = 34, h = 34;    // size
  float rt = 12, rb = 12;  // corner rounding, top / bottom
  float st = 0, sb = 0;    // edge slant, top / bottom: > 0 the inner corner moves in by this much (angry brow,
                           // squint), < 0 the outer corner does (sad)
  float lid = 0;           // upper lid: a flat droop from the top (sleepy)
  float cheek = 0;         // lower lid: a round cheek pushing up from below (happy crescent)
  float dx = 0, dy = 0;    // offset: + = outward / down
};

// Flourishes an emotion can switch on.
enum : uint16_t {
  FX_BLUSH = 1 << 0,     // slashes under the outer corners
  FX_TEAR = 1 << 1,      // a tear falls from the right eye
  FX_DOTS = 1 << 2,      // thinking dots, top right
  FX_POP = 1 << 3,       // "!" for a moment after the change
  FX_STEAM = 1 << 4,     // flickering strokes above the outer corners
  FX_SHAKE = 1 << 5,     // jitters side to side
  FX_HEARTS = 1 << 6,    // hearts float up
  FX_QUESTION = 1 << 7,  // a bobbing "?"
  FX_SPARKLE = 1 << 8,   // twinkles beside the eyes
  FX_BOUNCE = 1 << 9,    // eyes bounce
  FX_WOBBLE = 1 << 10,   // eyes sway
};

struct EmotionLook {
  EyeShape l, r;            // left / right eye as you look at the screen
  float gazeX = 0, gazeY = 0;  // where the eyes rest (thinking looks up and to the side)
  float pop = 0.12f;        // how much the eyes overshoot when this emotion arrives
  float smile = 0;          // the resting mouth (packs with mouth "always"): + smile, - frown
  uint16_t fx = 0;
};

struct FacePack {
  char name[32] = "Rocky";
  float gap = 16;           // between the eyes
  float y = 0;              // eye height: + = lower
  bool cyclops = false;     // one eye in the middle (the left eye's shape)
  bool outline = false;     // eyes drawn as outlines instead of solid
  bool glint = true;        // a pupil in each eye (dark, with a highlight) that looks around on its own
  uint8_t mouth = 0;        // 0 = only while talking, 1 = always (a resting smile), 2 = never
  EmotionLook looks[static_cast<int>(Emotion::COUNT)];
  EyeShape asleep;          // the shut eyes while sleeping
};

class Face {
 public:
  explicit Face(U8G2& display) : u8g2_(display) {}

  // Starts the display and loads the face pack saved in flash (Rocky's own if none).
  void begin();
  void setEmotion(Emotion e);
  Emotion emotion() const { return emotion_; }
  void blink();

  // Asleep: eyes shut, breathing, Z's. Overrides the emotion while on.
  void setAsleep(bool on);
  bool asleep() const { return asleep_; }
  // Roaming (the "roam" idle screen): asleep as far as everything else goes, but drawn awake with the neutral face,
  // looking around as the head moves.
  void setRoaming(bool on);
  // Eyes lead the head: pupils (and the eyes a little) toward x, y (-1..1, + = screen right / down), held holdMs.
  void lookToward(float x, float y, uint32_t holdMs);

  // Talking: draws a mouth that opens with `level` (0..1, the speaker's
  // current loudness). Call every frame while audio plays.
  void setTalking(bool on, float level = 0.0f);

  // When idle behavior is on, the face blinks and glances around on its own.
  void setIdle(bool on) { idle_ = on; }

  // A face pack (JSON, as server/faces/*.json) from the brain. save = keep it in flash. False if it doesn't parse.
  bool applyPackJson(const char* json, bool save = true);
  const char* packName() const { return pack_.name; }
  // Testing: hold `e` settled and still (no blinks, glances or flourishes), to compare with the dashboard preview.
  void setStill(bool on, Emotion e = Emotion::Neutral);

  // Call every frame: advances animation, then renders.
  void update(uint32_t nowMs);

 private:
  struct Zed {
    bool alive = false;
    float x = 0, y = 0;
    float age = 0;  // 0..1 over its life
  };
  struct Heart {
    bool alive = false;
    float x = 0, y = 0, age = 0;
  };

  bool sleepLook() const { return asleep_ && !roaming_; }        // eyes shut, breathing, Z's
  Emotion shown() const { return roaming_ ? Emotion::Neutral : emotion_; }
  void stepAnimation(uint32_t nowMs);
  void render();
  void drawEye(const EyeShape& e, float cx, float cy, int inner, float hScale);
  void drawMouth(int cx, int cy, int eyeBottom);
  void drawFlourishes(float leftOuter, float rightOuter, int eyeTop, int eyeBottom);
  void drawZeds();
  // Pixels, the same way the dashboard's preview draws them.
  void dot(int x, int y, uint8_t c);
  void rect(int x, int y, int w, int h, uint8_t c);
  void roundBox(int x, int y, int w, int h, float r, uint8_t c);
  void line(int x0, int y0, int x1, int y1);
  void disc(int cx, int cy, int r);
  void glyph(const char* const* rows, int n, int x, int y);

  U8G2& u8g2_;
  FacePack pack_;
  Emotion emotion_ = Emotion::Neutral;
  EyeShape curL_, curR_;
  uint32_t emotionSinceMs_ = 0;
  bool still_ = false;
  bool roaming_ = false;

  // Blink state: 0 = open, 1 = fully closed.
  float blinkAmount_ = 0.0f;
  bool blinkClosing_ = false;
  uint32_t nextBlinkMs_ = 0;

  // Gaze offset from center, eased toward gazeTarget.
  float gazeX_ = 0, gazeY_ = 0;
  float gazeTargetX_ = 0, gazeTargetY_ = 0;
  uint32_t nextSaccadeMs_ = 0;

  // Pupils (packs with `glint`): where they look inside the eye, -1..1 of the room they have, eased toward the
  // target. They dart about on their own, more often than the whole eye glances, as if he's looking at things.
  float pupilX_ = 0, pupilY_ = 0;
  float pupilTargetX_ = 0, pupilTargetY_ = 0;
  uint32_t nextPupilMs_ = 0;

  // Flourish state.
  float pop_ = 0.0f;            // overshoot on emotion change, decays to 0
  float squint_ = 0.0f;         // 1 while squinting, decays
  uint32_t nextSquintMs_ = 0;
  float tearY_ = -1.0f;         // -1 = no tear; else its y
  uint32_t nextTearMs_ = 0;
  uint8_t thinkDots_ = 0;
  uint32_t nextDotMs_ = 0;
  int jitterX_ = 0;
  Heart hearts_[3];
  uint32_t nextHeartMs_ = 0;
  bool heartSide_ = false;

  // Sleep.
  bool asleep_ = false;
  float breath_ = 0.0f;         // phase
  Zed zeds_[3];
  uint32_t nextZedMs_ = 0;
  uint32_t nextTwitchMs_ = 0;
  float twitch_ = 0.0f;

  // Mouth.
  bool talking_ = false;
  float mouthLevel_ = 0.0f;
  float mouth_ = 0.0f;          // eased openness 0..1
  float smile_ = 0.0f;          // eased resting-mouth curve

  bool idle_ = true;
  uint32_t lastFrameMs_ = 0;
  uint32_t frame_ = 0;
};
