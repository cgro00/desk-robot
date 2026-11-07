#include "face.h"

#include <ArduinoJson.h>
#include <Preferences.h>

namespace {

constexpr int SCREEN_W = 128;
constexpr int SCREEN_H = 64;

const char* kEmotionNames[] = {
    "neutral", "happy", "sad", "angry", "surprised", "sleepy", "thinking",
    "curious", "skeptical", "excited", "love", "confused",
};
static_assert(sizeof(kEmotionNames) / sizeof(kEmotionNames[0]) == static_cast<int>(Emotion::COUNT), "emotion names");

struct FxName {
  const char* name;
  uint16_t bit;
};
const FxName kFx[] = {
    {"blush", FX_BLUSH}, {"tear", FX_TEAR},         {"dots", FX_DOTS},       {"pop", FX_POP},
    {"steam", FX_STEAM}, {"shake", FX_SHAKE},       {"hearts", FX_HEARTS},   {"question", FX_QUESTION},
    {"sparkle", FX_SPARKLE}, {"bounce", FX_BOUNCE}, {"wobble", FX_WOBBLE},
};

// Small pictures, a row per string ('#' = lit). The dashboard has the same ones.
const char* const kBang[] = {"###", "###", "###", "###", "###", "###", "###", "...", "###", "###"};
const char* const kQuestion[] = {".#####.", "##...##", "##...##", ".....##", "....##.", "...##..",
                                 "..##...", "..##...", ".......", "..##...", "..##..."};
const char* const kHeart[] = {".##.##.", "#######", "#######", ".#####.", "..###..", "...#..."};

float approach(float cur, float target, float factor) { return cur + (target - cur) * factor; }

void morph(EyeShape& c, const EyeShape& t, float f) {
  c.w = approach(c.w, t.w, f);
  c.h = approach(c.h, t.h, f);
  c.rt = approach(c.rt, t.rt, f);
  c.rb = approach(c.rb, t.rb, f);
  c.st = approach(c.st, t.st, f);
  c.sb = approach(c.sb, t.sb, f);
  c.lid = approach(c.lid, t.lid, f);
  c.cheek = approach(c.cheek, t.cheek, f);
  c.dx = approach(c.dx, t.dx, f);
  c.dy = approach(c.dy, t.dy, f);
}

EyeShape eye(float w, float h, float rt, float rb, float st = 0, float sb = 0, float lid = 0, float cheek = 0,
             float dx = 0, float dy = 0) {
  EyeShape e;
  e.w = w; e.h = h; e.rt = rt; e.rb = rb; e.st = st; e.sb = sb; e.lid = lid; e.cheek = cheek; e.dx = dx; e.dy = dy;
  return e;
}

EmotionLook both(const EyeShape& e, uint16_t fx = 0, float pop = 0.12f, float gx = 0, float gy = 0) {
  EmotionLook l;
  l.l = l.r = e;
  l.fx = fx;
  l.pop = pop;
  l.gazeX = gx;
  l.gazeY = gy;
  return l;
}

// Rocky's own face: the pack the robot has before the brain sends one. Keep in step with server/faces/rocky.json.
void rockyPack(FacePack& p) {
  p = FacePack();
  using E = Emotion;
  auto& L = p.looks;
  L[(int)E::Neutral] = both(eye(34, 34, 12, 12));
  L[(int)E::Happy] = both(eye(36, 36, 12, 12, 0, 0, 0, 16), FX_BLUSH);
  L[(int)E::Sad] = both(eye(32, 26, 10, 10, -9, 0, 4), FX_TEAR);
  L[(int)E::Angry] = both(eye(34, 24, 8, 8, 10), FX_STEAM | FX_SHAKE);
  L[(int)E::Surprised] = both(eye(38, 44, 19, 19), FX_POP, 0.3f);
  L[(int)E::Sleepy] = both(eye(34, 26, 10, 10, 0, 0, 14));
  L[(int)E::Thinking] = both(eye(30, 30, 11, 11, 0, 0, 5), FX_DOTS, 0.12f, 7, -5);
  L[(int)E::Curious] = both(eye(30, 30, 11, 11), 0, 0.15f, -5, -3);
  L[(int)E::Curious].r = eye(38, 40, 15, 15, 0, 0, 0, 0, 0, -2);
  L[(int)E::Skeptical] = both(eye(34, 38, 13, 12, -3), 0, 0.12f, 4, 0);
  L[(int)E::Skeptical].r = eye(34, 34, 12, 12, 0, 0, 17);
  L[(int)E::Excited] = both(eye(38, 40, 16, 16, 0, 0, 0, 10), FX_SPARKLE | FX_BOUNCE, 0.25f);
  L[(int)E::Love] = both(eye(36, 34, 14, 14, 0, 0, 0, 12), FX_HEARTS | FX_BLUSH, 0.2f);
  L[(int)E::Confused] = both(eye(32, 36, 13, 12, -5), FX_QUESTION | FX_WOBBLE);
  L[(int)E::Confused].r = eye(28, 24, 10, 10, 4, 0, 0, 0, 0, 4);
  p.asleep = eye(30, 3, 1, 1);
}

void readEye(JsonObjectConst o, EyeShape& e) {
  if (o.isNull()) return;
  e.w = constrain(o["w"] | e.w, 4.0f, 120.0f);
  e.h = constrain(o["h"] | e.h, 2.0f, 64.0f);
  e.rt = constrain(o["rt"] | e.rt, 0.0f, 32.0f);
  e.rb = constrain(o["rb"] | e.rb, 0.0f, 32.0f);
  e.st = constrain(o["st"] | e.st, -30.0f, 30.0f);
  e.sb = constrain(o["sb"] | e.sb, -30.0f, 30.0f);
  e.lid = constrain(o["lid"] | e.lid, 0.0f, 40.0f);
  e.cheek = constrain(o["cheek"] | e.cheek, 0.0f, 40.0f);
  e.dx = constrain(o["dx"] | e.dx, -30.0f, 30.0f);
  e.dy = constrain(o["dy"] | e.dy, -30.0f, 30.0f);
}

}  // namespace

const char* emotionName(Emotion e) { return kEmotionNames[static_cast<uint8_t>(e)]; }

bool emotionFromName(const char* name, Emotion& out) {
  for (uint8_t i = 0; i < static_cast<uint8_t>(Emotion::COUNT); i++) {
    if (strcasecmp(name, kEmotionNames[i]) == 0) {
      out = static_cast<Emotion>(i);
      return true;
    }
  }
  return false;
}

// ── packs ───────────────────────────────────────────────────────────────────

bool Face::applyPackJson(const char* json, bool save) {
  JsonDocument doc;
  if (deserializeJson(doc, json)) return false;
  JsonObjectConst p = doc.as<JsonObjectConst>();
  if (p.isNull()) return false;
  FacePack n;
  rockyPack(n);  // anything a pack leaves out stays as Rocky's
  strlcpy(n.name, p["name"] | "unnamed", sizeof(n.name));
  n.gap = constrain(p["gap"] | n.gap, 0.0f, 60.0f);
  n.y = constrain(p["y"] | n.y, -20.0f, 20.0f);
  n.cyclops = p["cyclops"] | false;
  n.outline = p["outline"] | false;
  n.glint = p["glint"] | true;
  const char* mouth = p["mouth"] | "talk";
  n.mouth = !strcmp(mouth, "always") ? 1 : !strcmp(mouth, "never") ? 2 : 0;
  readEye(p["asleep"], n.asleep);
  JsonObjectConst emos = p["emotions"];
  for (int i = 0; i < static_cast<int>(Emotion::COUNT); ++i) {
    JsonObjectConst e = emos[kEmotionNames[i]];
    if (e.isNull()) continue;
    EmotionLook& look = n.looks[i];
    EyeShape base;  // a listed emotion starts from the plain eye, not from Rocky's version of it
    readEye(e, base);
    look.l = base;
    look.r = base;
    readEye(e["right"], look.r);
    look.pop = constrain(e["pop"] | 0.12f, 0.0f, 0.6f);
    look.smile = constrain(e["smile"] | 0.0f, -6.0f, 6.0f);
    look.gazeX = constrain(e["gaze"][0] | 0.0f, -20.0f, 20.0f);
    look.gazeY = constrain(e["gaze"][1] | 0.0f, -20.0f, 20.0f);
    look.fx = 0;
    for (JsonVariantConst f : e["fx"].as<JsonArrayConst>()) {
      const char* name = f | "";
      for (const FxName& fx : kFx) {
        if (!strcmp(name, fx.name)) look.fx |= fx.bit;
      }
    }
  }
  pack_ = n;
  Emotion e = emotion_;
  emotion_ = Emotion::COUNT;  // force setEmotion to pick up the new look
  setEmotion(e);
  if (still_) setStill(true, e);
  if (save) {
    Preferences prefs;
    prefs.begin("face", false);
    size_t len = strlen(json);
    bool same = prefs.getBytesLength("pack") == len;
    if (same) {
      char* buf = static_cast<char*>(malloc(len));
      same = buf && prefs.getBytes("pack", buf, len) == len && memcmp(buf, json, len) == 0;
      free(buf);
    }
    if (!same) prefs.putBytes("pack", json, len);  // only write flash when the pack really changed
    prefs.end();
  }
  return true;
}

void Face::setStill(bool on, Emotion e) {
  still_ = on;
  if (!on) return;
  asleep_ = false;
  setEmotion(e);
  const EmotionLook& look = pack_.looks[static_cast<int>(e)];
  curL_ = look.l;
  curR_ = look.r;
  smile_ = look.smile;
  blinkAmount_ = 0;
  blinkClosing_ = false;
  pop_ = squint_ = twitch_ = 0;
  gazeX_ = gazeTargetX_;
  gazeY_ = gazeTargetY_;
  pupilX_ = pupilY_ = pupilTargetX_ = pupilTargetY_ = 0;
  mouth_ = 0;
  talking_ = false;
  jitterX_ = 0;
}

// ── state ───────────────────────────────────────────────────────────────────

void Face::begin() {
  u8g2_.begin();
  u8g2_.setBusClock(400000);
  rockyPack(pack_);
  {
    Preferences prefs;
    prefs.begin("face", true);
    size_t len = prefs.getBytesLength("pack");
    if (len > 0 && len < 16384) {
      char* buf = static_cast<char*>(malloc(len + 1));
      if (buf && prefs.getBytes("pack", buf, len) == len) {
        buf[len] = 0;
        prefs.end();
        applyPackJson(buf, false);
      } else {
        prefs.end();
      }
      free(buf);
    } else {
      prefs.end();
    }
  }
  curL_ = pack_.looks[0].l;
  curR_ = pack_.looks[0].r;
  // Wake-up: start with eyes shut, then open.
  blinkAmount_ = 1.0f;
  blinkClosing_ = false;
  nextBlinkMs_ = millis() + 3000;
  nextSaccadeMs_ = millis() + 1500;
  nextSquintMs_ = millis() + 9000;
}

void Face::setEmotion(Emotion e) {
  if (e >= Emotion::COUNT) return;
  if (e != emotion_) {
    pop_ = 1.0f;  // a little overshoot sells the change
    emotionSinceMs_ = millis();
    tearY_ = -1.0f;
    nextTearMs_ = millis() + 1500;
    thinkDots_ = 0;
    for (Heart& h : hearts_) h.alive = false;
    nextHeartMs_ = millis() + 200;
  }
  emotion_ = e;
  // Some emotions look somewhere (thinking: up and to the side); the rest release the gaze.
  const EmotionLook& look = pack_.looks[static_cast<int>(e)];
  gazeTargetX_ = look.gazeX;
  gazeTargetY_ = look.gazeY;
}

void Face::blink() { blinkClosing_ = true; }

void Face::setAsleep(bool on) {
  if (on == asleep_) return;
  asleep_ = on;
  if (on) {
    for (Zed& z : zeds_) z.alive = false;
    nextZedMs_ = millis() + 800;
    nextTwitchMs_ = millis() + random(5000, 9000);
    gazeTargetX_ = gazeTargetY_ = 0;
  } else {
    pop_ = 1.0f;  // eyes spring open
    blinkAmount_ = 0.0f;
    setEmotion(emotion_);
  }
}

void Face::setRoaming(bool on) {
  if (on == roaming_) return;
  roaming_ = on;
  if (on && asleep_) {
    pop_ = 1.0f;  // eyes spring open
    blinkAmount_ = 0.0f;
    gazeTargetX_ = gazeTargetY_ = 0;
  }
}

void Face::lookToward(float x, float y, uint32_t holdMs) {
  pupilTargetX_ = constrain(x, -1.0f, 1.0f);
  pupilTargetY_ = constrain(y, -1.0f, 1.0f);
  gazeTargetX_ = pupilTargetX_ * 6.0f;   // the whole eye leans that way a little too
  gazeTargetY_ = pupilTargetY_ * 3.0f;
  nextPupilMs_ = nextSaccadeMs_ = millis() + holdMs;
}

void Face::setTalking(bool on, float level) {
  talking_ = on;
  mouthLevel_ = constrain(level, 0.0f, 1.0f);
}

void Face::stepAnimation(uint32_t nowMs) {
  frame_++;
  if (still_) return;
  const EmotionLook& look = pack_.looks[static_cast<int>(shown())];

  // Morph the eye shapes toward the current emotion (or shut, if asleep).
  float f = sleepLook() ? 0.12f : 0.25f;  // eyes close slowly, change fast
  morph(curL_, sleepLook() ? pack_.asleep : look.l, f);
  morph(curR_, sleepLook() ? pack_.asleep : look.r, f);
  smile_ = approach(smile_, sleepLook() ? 0 : look.smile, f);

  pop_ *= 0.82f;
  squint_ *= 0.85f;

  if (sleepLook()) {
    // Slow breathing; the odd dream twitch; Z's drifting up.
    breath_ += 0.035f;
    blinkAmount_ = 0.0f;
    blinkClosing_ = false;
    twitch_ *= 0.8f;
    if (nowMs >= nextTwitchMs_) {
      twitch_ = 1.0f;
      nextTwitchMs_ = nowMs + random(6000, 12000);
    }
    if (nowMs >= nextZedMs_) {
      for (Zed& z : zeds_) {
        if (!z.alive) {
          z.alive = true;
          z.age = 0;
          z.x = fminf(pack_.cyclops ? SCREEN_W / 2 + curL_.w / 2 + 2 : SCREEN_W / 2 + pack_.gap / 2 + curL_.w + 2, 108);
          z.y = SCREEN_H / 2 - 6;
          break;
        }
      }
      nextZedMs_ = nowMs + random(1100, 1700);
    }
    for (Zed& z : zeds_) {
      if (!z.alive) continue;
      z.age += 0.012f;
      z.y -= 0.32f;
      z.x += 0.22f;
      if (z.age >= 1.0f || z.y < -12) z.alive = false;
    }
    gazeX_ = approach(gazeX_, 0, 0.2f);
    gazeY_ = approach(gazeY_, 0, 0.2f);
    mouth_ = approach(mouth_, 0, 0.3f);
    return;
  }

  // Blink: snap shut fast, reopen a little slower — reads as natural.
  if (blinkClosing_) {
    blinkAmount_ += 0.45f;
    if (blinkAmount_ >= 1.0f) {
      blinkAmount_ = 1.0f;
      blinkClosing_ = false;
    }
  } else if (blinkAmount_ > 0.0f) {
    blinkAmount_ = max(0.0f, blinkAmount_ - 0.28f);
  }

  if (idle_) {
    if (nowMs >= nextBlinkMs_) {
      blinkClosing_ = true;
      nextBlinkMs_ = nowMs + random(2200, 6000);
      // Occasional double blink.
      if (random(100) < 20) nextBlinkMs_ = nowMs + 400;
    }
    bool looksSomewhere = look.gazeX != 0 || look.gazeY != 0;
    if (nowMs >= nextSaccadeMs_ && !looksSomewhere) {
      gazeTargetX_ = static_cast<float>(random(-8, 9));
      gazeTargetY_ = static_cast<float>(random(-4, 5));
      // Mostly return to center so the robot doesn't look shifty.
      if (random(100) < 40) gazeTargetX_ = gazeTargetY_ = 0;
      nextSaccadeMs_ = nowMs + random(1200, 4000);
    }
    if (nowMs >= nextPupilMs_) {
      // The pupils look at something new: a quick dart, about a third of the time back near the middle.
      if (random(100) < 35) {
        pupilTargetX_ = random(-25, 26) / 100.0f;
        pupilTargetY_ = random(-20, 21) / 100.0f;
      } else {
        pupilTargetX_ = random(-90, 91) / 100.0f;
        pupilTargetY_ = random(-60, 61) / 100.0f;
      }
      nextPupilMs_ = nowMs + random(600, 2600);
    }
    if (nowMs >= nextSquintMs_ && emotion_ == Emotion::Neutral) {
      squint_ = 1.0f;
      nextSquintMs_ = nowMs + random(8000, 16000);
    }
  }

  // Flourishes.
  if (look.fx & FX_TEAR) {
    if (tearY_ < 0 && nowMs >= nextTearMs_) tearY_ = 0;
    if (tearY_ >= 0) {
      tearY_ += 0.7f;
      if (tearY_ > SCREEN_H) {
        tearY_ = -1;
        nextTearMs_ = nowMs + random(2500, 5000);
      }
    }
  }
  if ((look.fx & FX_DOTS) && nowMs >= nextDotMs_) {
    thinkDots_ = (thinkDots_ + 1) % 4;
    nextDotMs_ = nowMs + 420;
  }
  if ((look.fx & FX_HEARTS) && nowMs >= nextHeartMs_) {
    for (Heart& h : hearts_) {
      if (!h.alive) {
        h.alive = true;
        h.age = 0;
        heartSide_ = !heartSide_;
        h.x = heartSide_ ? SCREEN_W / 2 + pack_.gap / 2 + curR_.w + 2 : SCREEN_W / 2 - pack_.gap / 2 - curL_.w - 9;
        if (pack_.cyclops) h.x = heartSide_ ? SCREEN_W / 2 + curL_.w / 2 + 3 : SCREEN_W / 2 - curL_.w / 2 - 10;
        h.y = SCREEN_H / 2 + 6;
        break;
      }
    }
    nextHeartMs_ = nowMs + random(600, 1000);
  }
  for (Heart& h : hearts_) {
    if (!h.alive) continue;
    h.age += 0.015f;
    h.y -= 0.45f;
    if (h.age >= 1.0f || h.y < -6) h.alive = false;
  }
  jitterX_ = ((look.fx & FX_SHAKE) && (frame_ % 3 == 0)) ? random(-1, 2) : 0;

  // Mouth follows the speaker's loudness while talking, closes otherwise.
  float mouthGoal = talking_ ? constrain(0.15f + mouthLevel_ * 4.0f, 0.15f, 1.0f) : 0.0f;
  mouth_ = approach(mouth_, mouthGoal, talking_ ? 0.5f : 0.3f);

  gazeX_ = approach(gazeX_, gazeTargetX_, 0.35f);
  gazeY_ = approach(gazeY_, gazeTargetY_, 0.35f);
  pupilX_ = approach(pupilX_, pupilTargetX_, 0.5f);   // eyes dart: there in a few frames
  pupilY_ = approach(pupilY_, pupilTargetY_, 0.5f);
}

// ── drawing ─────────────────────────────────────────────────────────────────

void Face::dot(int x, int y, uint8_t c) {
  if (x < 0 || y < 0 || x >= SCREEN_W || y >= SCREEN_H) return;
  u8g2_.setDrawColor(c);
  u8g2_.drawPixel(x, y);
}

void Face::rect(int x, int y, int w, int h, uint8_t c) {
  for (int j = 0; j < h; ++j)
    for (int i = 0; i < w; ++i) dot(x + i, y + j, c);
}

void Face::roundBox(int x, int y, int w, int h, float r, uint8_t c) {
  r = fminf(r, fminf(w, h) * 0.5f);
  for (int j = 0; j < h; ++j) {
    for (int i = 0; i < w; ++i) {
      float px = i + 0.5f, py = j + 0.5f;
      float qx = constrain(px, r, w - r), qy = constrain(py, r, h - r);
      if ((px - qx) * (px - qx) + (py - qy) * (py - qy) <= r * r) dot(x + i, y + j, c);
    }
  }
}

void Face::line(int x0, int y0, int x1, int y1) {
  int dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
  int dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
  int err = dx + dy;
  while (true) {
    dot(x0, y0, 1);
    if (x0 == x1 && y0 == y1) break;
    int e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
}

void Face::disc(int cx, int cy, int r) {
  for (int j = -r; j <= r; ++j)
    for (int i = -r; i <= r; ++i)
      if (i * i + j * j <= r * r + r) dot(cx + i, cy + j, 1);
}

void Face::glyph(const char* const* rows, int n, int x, int y) {
  for (int j = 0; j < n; ++j)
    for (int i = 0; rows[j][i]; ++i)
      if (rows[j][i] == '#') dot(x + i, y + j, 1);
}

namespace {
// Where one eye is, worked out once per frame.
struct Geo {
  float cx, w, left, right, top, bot, rt, rb, st, sb, lid, cheek;
  int inner;  // +1: the inner edge is to the right (the left eye), -1: to the left
};

bool inEye(const Geo& g, float x, float y) {
  if (x < g.left || x >= g.right || y < g.top || y >= g.bot) return false;
  float u = (x - g.cx) / (g.w * 0.5f) * g.inner;  // -1 outer edge .. +1 inner edge
  float topCut = g.st >= 0 ? g.st * (u + 1) * 0.5f : -g.st * (1 - u) * 0.5f;
  float botCut = g.sb >= 0 ? g.sb * (u + 1) * 0.5f : -g.sb * (1 - u) * 0.5f;
  // Slants tilt the whole rounded eye (a shear), so slanted corners stay round: look the point up in the
  // unslanted eye.
  float hh = g.bot - g.top;
  float yy = y - topCut * (g.bot - y) / hh + botCut * (y - g.top) / hh;
  if (yy < g.top + g.lid || yy >= g.bot) return false;
  if (yy < g.top + g.rt) {
    float qx = constrain(x, g.left + g.rt, g.right - g.rt), dx = x - qx, dy = yy - (g.top + g.rt);
    if (dx * dx + dy * dy > g.rt * g.rt) return false;
  }
  if (yy > g.bot - g.rb) {
    float qx = constrain(x, g.left + g.rb, g.right - g.rb), dx = x - qx, dy = yy - (g.bot - g.rb);
    if (dx * dx + dy * dy > g.rb * g.rb) return false;
  }
  if (g.cheek > 0.5f) {  // a round cheek pushing up from below
    float rr = g.w, ccy = g.bot + rr - g.cheek, dx = x - g.cx, dy = y - ccy;
    if (dx * dx + dy * dy < rr * rr) return false;
  }
  return true;
}
}  // namespace

void Face::drawEye(const EyeShape& e, float cx, float cy, int inner, float hScale) {
  float w = e.w, h = e.h * hScale * (1.0f - blinkAmount_);
  if (h < 2) h = 2;
  Geo g;
  g.cx = cx - inner * e.dx;
  float ecy = cy + e.dy;
  g.w = w;
  g.left = g.cx - w * 0.5f;
  g.right = g.cx + w * 0.5f;
  g.top = ecy - h * 0.5f;
  g.bot = ecy + h * 0.5f;
  g.rt = fmaxf(0, fminf(e.rt, fminf(w, h) * 0.5f));
  g.rb = fmaxf(0, fminf(e.rb, fminf(w, h) * 0.5f));
  g.st = e.st;
  g.sb = e.sb;
  g.lid = e.lid;
  g.cheek = e.cheek;
  g.inner = inner;
  int x0 = static_cast<int>(floorf(g.left)), x1 = static_cast<int>(ceilf(g.right));
  int y0 = static_cast<int>(floorf(g.top)), y1 = static_cast<int>(ceilf(g.bot));
  for (int y = y0; y < y1; ++y) {
    for (int x = x0; x < x1; ++x) {
      float px = x + 0.5f, py = y + 0.5f;
      bool on = inEye(g, px, py);
      if (on && pack_.outline)
        on = !(inEye(g, px - 2, py) && inEye(g, px + 2, py) && inEye(g, px, py - 2) && inEye(g, px, py + 2));
      if (on) dot(x, y, 1);
    }
  }
  // Pupil: a dark disc (lit in outline packs) with a small highlight, somewhere in the part of the eye that shows,
  // so lids and cheeks cover it like real ones. It also leans the way the whole eye is glancing.
  if (pack_.glint && h > 12 && !sleepLook()) {
    float r = constrain(g.w * 0.14f, 3.0f, 6.0f);   // ~10 px across on a 34 px eye
    int top = -1, bot = -1;
    for (int y = y0; y < y1; ++y) {
      if (inEye(g, g.cx, y + 0.5f)) {
        if (top < 0) top = y;
        bot = y;
      }
    }
    if (top < 0) return;
    float vcy = (top + bot + 1) * 0.5f;
    int left = -1, right = -1;
    for (int x = x0; x < x1; ++x) {
      if (inEye(g, x + 0.5f, vcy)) {
        if (left < 0) left = x;
        right = x;
      }
    }
    if (left < 0) return;
    float vcx = (left + right + 1) * 0.5f;
    float roomX = fmaxf(0, (right - left + 1) * 0.5f - r - 2.0f), roomY = fmaxf(0, (bot - top + 1) * 0.5f - r - 1.5f);
    float px = constrain(pupilX_ + gazeX_ / 12.0f, -1.0f, 1.0f), py = constrain(pupilY_ + gazeY_ / 8.0f, -1.0f, 1.0f);
    float pcx = vcx + px * roomX, pcy = vcy + py * roomY;
    uint8_t ink = pack_.outline ? 1 : 0;
    int ri = static_cast<int>(ceilf(r));
    for (int y = static_cast<int>(floorf(pcy)) - ri; y <= static_cast<int>(floorf(pcy)) + ri; ++y) {
      for (int x = static_cast<int>(floorf(pcx)) - ri; x <= static_cast<int>(floorf(pcx)) + ri; ++x) {
        float dx = x + 0.5f - pcx, dy = y + 0.5f - pcy;
        if (dx * dx + dy * dy <= r * r && inEye(g, x + 0.5f, y + 0.5f)) dot(x, y, ink);
      }
    }
    if (!pack_.outline && r >= 4) {   // the highlight: up and to the left in the pupil
      int hx = static_cast<int>(floorf(pcx - r * 0.5f)), hy = static_cast<int>(floorf(pcy - r * 0.55f));
      if (inEye(g, hx + 0.5f, hy + 0.5f)) rect(hx, hy, 2, 2, 1);
    }
  }
}

void Face::drawMouth(int cx, int cy, int eyeBottom) {
  (void)eyeBottom;
  int my0 = static_cast<int>(floorf(cy + curL_.h * 0.5f));
  if (mouth_ >= 0.05f && pack_.mouth != 2) {
    int mh = 2 + static_cast<int>(floorf(mouth_ * 9));
    int mw = 20 + static_cast<int>(floorf(mouth_ * 8));
    int my = my0 + 4;
    if (my + mh > SCREEN_H - 1) my = SCREEN_H - 1 - mh;
    roundBox(cx - mw / 2, my, mw, mh, fminf(mh / 2, 4), 1);
    if (mh > 6) roundBox(cx - mw / 2 + 3, my + 2, mw - 6, mh - 4, 2, 0);  // a darker inside when it's wide open
  } else if (pack_.mouth == 1) {
    // Resting mouth: a short curve, smiling or frowning.
    const int mw = 14;
    int my = min(my0 + 6, SCREEN_H - 4);
    for (int i = 0; i <= mw; ++i) {
      float t = (i - mw * 0.5f) / (mw * 0.5f);
      int yy = static_cast<int>(floorf(my + smile_ * (1 - t * t) + 0.5f));
      dot(cx - mw / 2 + i, yy, 1);
      dot(cx - mw / 2 + i, yy + 1, 1);
    }
  }
}

void Face::drawFlourishes(float leftOuter, float rightOuter, int eyeTop, int eyeBottom) {
  const EmotionLook& look = pack_.looks[static_cast<int>(shown())];
  uint16_t fx = look.fx;
  uint32_t since = millis() - emotionSinceMs_;
  int lo = static_cast<int>(floorf(leftOuter)), ro = static_cast<int>(floorf(rightOuter));

  if ((fx & FX_POP) && since < 900) glyph(kBang, 10, 116, 4);
  if (fx & FX_BLUSH) {
    for (int i = 0; i < 2; ++i) {  // two short slashes below the outer corner of each eye
      int lx = lo - 8 + i * 3, rx = ro + 3 + i * 3;
      line(lx, eyeBottom + 1, lx + 3, eyeBottom - 3);
      line(rx, eyeBottom + 1, rx + 3, eyeBottom - 3);
    }
  }
  if ((fx & FX_TEAR) && tearY_ >= 0) {
    int tx = ro - 3, ty = eyeBottom + static_cast<int>(floorf(tearY_));
    disc(tx, ty, 2);
    line(tx, ty - 5, tx, ty - 2);
  }
  if (fx & FX_DOTS) {
    for (int i = 0; i < thinkDots_; ++i) disc(SCREEN_W - 26 + i * 8, 9, 2);
  }
  if ((fx & FX_STEAM) && (frame_ / 6) % 2 == 0) {  // two short strokes above the outer corners
    line(lo - 4, eyeTop - 4, lo - 2, eyeTop - 9);
    line(ro + 4, eyeTop - 4, ro + 2, eyeTop - 9);
  }
  if (fx & FX_HEARTS) {
    for (const Heart& h : hearts_) {
      if (!h.alive || (h.age > 0.8f && frame_ % 2 == 0)) continue;
      glyph(kHeart, 6, static_cast<int>(floorf(h.x)), static_cast<int>(floorf(h.y)));
    }
  }
  if (fx & FX_QUESTION) glyph(kQuestion, 11, 112, 3 + static_cast<int>(floorf(sinf(frame_ * 0.15f) * 2)));
  if (fx & FX_SPARKLE) {
    bool a = (frame_ / 5) % 2 == 0;
    int pts[4] = {a ? lo - 6 : lo - 5, a ? eyeTop + 3 : eyeBottom - 5, a ? ro + 6 : ro + 5, a ? eyeBottom - 4 : eyeTop + 2};
    for (int k = 0; k < 2; ++k) {
      int x = pts[k * 2], y = pts[k * 2 + 1];
      line(x - 2, y, x + 2, y);
      line(x, y - 2, x, y + 2);
    }
  }
}

void Face::drawZeds() {
  for (const Zed& z : zeds_) {
    if (!z.alive) continue;
    if (z.age > 0.8f && (frame_ % 2 == 0)) continue;  // flicker out at the end
    int s = z.age < 0.33f ? 4 : z.age < 0.66f ? 6 : 8;
    int x = static_cast<int>(floorf(z.x)), y = static_cast<int>(floorf(z.y)) - s;
    if (x < 0 || x >= SCREEN_W - 4 || y < 0) continue;
    for (int b = 0; b < (s >= 8 ? 2 : 1); ++b) {  // the biggest Z is drawn bold
      line(x + b, y, x + s + b, y);
      line(x + s + b, y, x + b, y + s);
      line(x + b, y + s, x + s + b, y + s);
    }
  }
}

void Face::render() {
  u8g2_.clearBuffer();
  const EmotionLook& look = pack_.looks[static_cast<int>(shown())];
  bool lively = !still_ && !sleepLook();

  // Vertical bob: breathing while asleep, a bounce with the mouth while talking.
  float bob = sleepLook() ? sinf(breath_) * 2.0f : -mouth_ * 2.0f;
  if (lively && (look.fx & FX_BOUNCE)) bob -= fabsf(sinf(frame_ * 0.18f)) * 3.0f;
  float sway = (lively && (look.fx & FX_WOBBLE)) ? sinf(frame_ * 0.08f) * 3.0f : 0.0f;
  float cy = SCREEN_H / 2 + pack_.y + gazeY_ + bob;
  if (talking_) cy -= 2;  // make room for the mouth
  float gx = gazeX_ + jitterX_ + sway;

  // Height scale: emotion-change overshoot, idle squint, dream twitch.
  float hScale = 1.0f + pop_ * look.pop;
  hScale *= 1.0f - squint_ * 0.4f;
  if (sleepLook()) hScale += twitch_ * 2.5f;  // eyelids flutter open a crack

  float leftOuter, rightOuter;
  if (pack_.cyclops) {
    float cx = SCREEN_W / 2 + gx;
    drawEye(curL_, cx, cy, 1, hScale);
    leftOuter = cx - curL_.dx - curL_.w / 2;
    rightOuter = cx - curL_.dx + curL_.w / 2;
  } else {
    float lcx = SCREEN_W / 2 - pack_.gap / 2 - curL_.w / 2 + gx;
    float rcx = SCREEN_W / 2 + pack_.gap / 2 + curR_.w / 2 + gx;
    drawEye(curL_, lcx, cy, 1, hScale);
    drawEye(curR_, rcx, cy, -1, hScale);
    leftOuter = lcx - curL_.dx - curL_.w / 2;
    rightOuter = rcx + curR_.dx + curR_.w / 2;
  }

  int eyeTop = static_cast<int>(floorf(cy - curL_.h * hScale / 2));
  int eyeBottom = static_cast<int>(floorf(cy + curL_.h * hScale / 2));
  if (sleepLook()) {
    drawZeds();
  } else {
    if (!still_) drawFlourishes(leftOuter, rightOuter, eyeTop, eyeBottom);
    drawMouth(SCREEN_W / 2 + static_cast<int>(floorf(gazeX_ * 0.5f)), static_cast<int>(floorf(cy)), eyeBottom);
  }
  u8g2_.setDrawColor(1);
  u8g2_.sendBuffer();
}

void Face::update(uint32_t nowMs) {
  lastFrameMs_ = nowMs;
  stepAnimation(nowMs);
  render();
}
