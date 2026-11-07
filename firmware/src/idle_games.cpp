#include "idle_games.h"

namespace {

const char* const MODE_NAMES[] = {"clock", "pong", "tetris", "frogger", "roam"};

float frand(float lo, float hi) { return lo + (hi - lo) * (random(0, 10001) / 10000.0f); }

// ── Tetris pieces: 4 rotations each, 4 x 4 cells, bit 15 = row 0 column 0 ───────────────────────────────────────
const uint16_t SHAPES[7][4] = {
    {0x0F00, 0x2222, 0x00F0, 0x4444},   // I
    {0x8E00, 0x6440, 0x0E20, 0x44C0},   // J
    {0x2E00, 0x4460, 0x0E80, 0xC440},   // L
    {0x6600, 0x6600, 0x6600, 0x6600},   // O
    {0x6C00, 0x4620, 0x06C0, 0x8C40},   // S
    {0x4E00, 0x4640, 0x0E40, 0x4C40},   // T
    {0xC600, 0x2640, 0x0C60, 0x4C80},   // Z
};
inline bool cell(uint16_t mask, int r, int c) { return (mask >> (15 - (r * 4 + c))) & 1; }
constexpr int COLS = 10, ROWS = 20;
constexpr uint16_t FULL = (1 << COLS) - 1;
constexpr int WELL_X = 49, WELL_Y = 2, CELL = 3;   // the well: 30 x 60 px in the middle of the screen

// ── Frogger: 9 rows of 6 px under a 9 px bar; objects wrap round a strip wider than the screen ─────────────────────
constexpr int FROG_ROW0 = 9, FROG_ROW_H = 6;
constexpr float SPAN = 160.0f;          // -32 .. 128: objects leave one side and come back on the other
constexpr int HOP = 3;                  // frames a hop takes
inline int rowY(int r) { return FROG_ROW0 + r * FROG_ROW_H; }
inline bool river(int r) { return r >= 1 && r <= 3; }
inline bool road(int r) { return r >= 5 && r <= 7; }
float wrapX(float v) {
  v = fmodf(v + 32.0f, SPAN);
  if (v < 0) v += SPAN;
  return v - 32.0f;
}

}  // namespace

const char* idleModeName(IdleMode m) { return MODE_NAMES[static_cast<int>(m)]; }

bool idleModeFromName(const char* name, IdleMode& out) {
  for (int i = 0; i < 5; ++i) {
    if (!strcmp(name, MODE_NAMES[i])) {
      out = static_cast<IdleMode>(i);
      return true;
    }
  }
  return false;
}

void IdleGames::reset(IdleMode m) {
  mode_ = m;
  frame_ = 0;
  if (m == IdleMode::Pong) pongReset();
  else if (m == IdleMode::Tetris) tetrisReset();
  else if (m == IdleMode::Frogger) frogReset(false);
}

void IdleGames::step() {
  ++frame_;
  if (mode_ == IdleMode::Pong) pongStep();
  else if (mode_ == IdleMode::Tetris) tetrisStep();
  else if (mode_ == IdleMode::Frogger) frogStep();
}

void IdleGames::draw(const char* clock, int dx, int dy) {
  u8g2_.setDrawColor(1);
  if (mode_ == IdleMode::Pong) pongDraw(clock, dx, dy);
  else if (mode_ == IdleMode::Tetris) tetrisDraw(clock, dx, dy);
  else if (mode_ == IdleMode::Frogger) frogDraw(clock, dx, dy);
}

// ── Pong ─────────────────────────────────────────────────────────────────────────────────────────────────────────
// The field is y 10..63 under a bar with the time. Paddles 2 x 12 at x 2 and 124; the ball is 2 x 2. Each paddle
// works out where the ball will reach it (bounces included) and heads there, aiming a little off-centre so the
// rally changes angle; the hit itself always happens, so it never misses.

namespace {
constexpr float PONG_TOP = 10, PONG_BOT = 62;   // the ball's top edge stays in here (it's 2 px tall)
constexpr float PAD_HALF = 6, LEFT_FACE = 4, RIGHT_FACE = 122;
}

void IdleGames::pongReset() {
  Pong p;
  p.vx = random(2) ? 2.2f : -2.2f;
  p.vy = frand(0.5f, 1.3f) * (random(2) ? 1 : -1);
  pong_ = p;
}

float IdleGames::pongPredict(int side) const {
  float x = pong_.bx, y = pong_.by, vx = pong_.vx, vy = pong_.vy;
  float face = side == 0 ? LEFT_FACE : RIGHT_FACE;
  for (int i = 0; i < 400; ++i) {
    if ((vx < 0 && x <= face) || (vx > 0 && x >= face)) break;
    x += vx;
    y += vy;
    if (y < PONG_TOP) { y = 2 * PONG_TOP - y; vy = -vy; }
    if (y > PONG_BOT) { y = 2 * PONG_BOT - y; vy = -vy; }
  }
  return y + 1;
}

void IdleGames::pongStep() {
  Pong& p = pong_;
  for (int s = 0; s < 2; ++s) {
    bool coming = s == 0 ? p.vx < 0 : p.vx > 0;
    float want = coming ? pongPredict(s) - p.aim[s] : 36.0f;
    float maxStep = coming ? 2.8f : 0.8f;
    float d = constrain(want - p.pad[s], -maxStep, maxStep);
    p.pad[s] = constrain(p.pad[s] + d, PONG_TOP + PAD_HALF, PONG_BOT + 2 - PAD_HALF);
  }
  p.bx += p.vx;
  p.by += p.vy;
  if (p.by < PONG_TOP) { p.by = 2 * PONG_TOP - p.by; p.vy = -p.vy; }
  if (p.by > PONG_BOT) { p.by = 2 * PONG_BOT - p.by; p.vy = -p.vy; }
  int side = p.vx < 0 && p.bx <= LEFT_FACE ? 0 : p.vx > 0 && p.bx >= RIGHT_FACE ? 1 : -1;
  if (side >= 0) {
    // it never misses: if the paddle is somehow off, it's already there
    float ballC = p.by + 1;
    if (fabsf(ballC - p.pad[side]) > PAD_HALF) p.pad[side] = ballC + (ballC > p.pad[side] ? -PAD_HALF + 1 : PAD_HALF - 1);
    float rel = constrain((ballC - p.pad[side]) / PAD_HALF, -1.0f, 1.0f);
    p.bx = side == 0 ? LEFT_FACE : RIGHT_FACE;
    p.vx = -p.vx * 1.04f;
    if (fabsf(p.vx) > 3.6f) p.vx = p.vx > 0 ? 3.6f : -3.6f;
    p.vy = constrain(p.vy * 0.6f + rel * 1.7f, -2.4f, 2.4f);
    if (fabsf(p.vy) < 0.35f) p.vy = p.vy < 0 ? -0.35f : 0.35f;
    p.aim[side] = frand(-4.5f, 4.5f);          // next time, a different spot on the paddle
    if (++p.hits % 40 == 0) p.vx = p.vx > 0 ? 2.2f : -2.2f;   // a long rally: calm down, build up again
  }
}

void IdleGames::pongDraw(const char* clock, int dx, int dy) {
  u8g2_.setFont(u8g2_font_5x8_tr);
  u8g2_.drawStr(64 - u8g2_.getStrWidth(clock) / 2 + dx, 7 + (dy > 0 ? 0 : dy), clock);
  u8g2_.drawHLine(0, 9, 128);
  for (int y = 11; y < 64; y += 4) u8g2_.drawVLine(63, y, 2);
  for (int s = 0; s < 2; ++s)
    u8g2_.drawBox(s == 0 ? 2 : 124, static_cast<int>(lroundf(pong_.pad[s] - PAD_HALF)), 2, static_cast<int>(PAD_HALF * 2));
  u8g2_.drawBox(static_cast<int>(lroundf(pong_.bx)), static_cast<int>(lroundf(pong_.by)), 2, 2);
}

// ── Tetris ───────────────────────────────────────────────────────────────────────────────────────────────────────
// A 7-bag of pieces; for each new piece a small player tries every rotation and column and picks the placement with
// the lowest, flattest stack and the fewest holes (the usual four-number score). The piece slides and turns into
// place near the top, then drops a row a frame. Full rows flash and go. If the stack ever gets near the top, the
// board fills and empties with a wipe and the game goes on: it never ends.

void IdleGames::tetrisReset() {
  uint16_t lines = tet_.lines;
  tet_ = Tetris();
  tet_.lines = lines;
  tet_.next = tetrisDraw7();
  tetrisSpawn();
}

uint8_t IdleGames::tetrisDraw7() {
  if (tet_.bagLeft == 0) {
    for (int i = 0; i < 7; ++i) tet_.bag[i] = i;
    for (int i = 6; i > 0; --i) {
      int j = random(0, i + 1);
      uint8_t t = tet_.bag[i]; tet_.bag[i] = tet_.bag[j]; tet_.bag[j] = t;
    }
    tet_.bagLeft = 7;
  }
  return tet_.bag[--tet_.bagLeft];
}

bool IdleGames::tetrisCollides(const uint16_t* rows, uint8_t piece, uint8_t rot, int x, int y) const {
  uint16_t m = SHAPES[piece][rot & 3];
  for (int r = 0; r < 4; ++r)
    for (int c = 0; c < 4; ++c) {
      if (!cell(m, r, c)) continue;
      int bx = x + c, by = y + r;
      if (bx < 0 || bx >= COLS || by >= ROWS) return true;
      if (by >= 0 && (rows[by] >> bx & 1)) return true;
    }
  return false;
}

void IdleGames::tetrisPlan() {
  Tetris& t = tet_;
  float best = -1e9f;
  t.targetRot = t.rot;
  t.targetX = t.x;
  for (int rot = 0; rot < 4; ++rot) {
    for (int x = -2; x < COLS; ++x) {
      if (tetrisCollides(t.rows, t.piece, rot, x, -2)) continue;
      int y = -2;
      while (!tetrisCollides(t.rows, t.piece, rot, x, y + 1)) ++y;
      uint16_t b[ROWS];
      memcpy(b, t.rows, sizeof(b));
      bool over = false;
      uint16_t m = SHAPES[t.piece][rot];
      for (int r = 0; r < 4; ++r)
        for (int c = 0; c < 4; ++c)
          if (cell(m, r, c)) {
            if (y + r < 0) over = true;
            else b[y + r] |= 1 << (x + c);
          }
      if (over) continue;
      int cleared = 0;
      for (int r = 0; r < ROWS; ++r) if (b[r] == FULL) ++cleared;
      // heights and holes, as if the full rows were gone
      int h[COLS], holes = 0, agg = 0, bump = 0;
      for (int c = 0; c < COLS; ++c) {
        h[c] = 0;
        bool seen = false;
        for (int r = 0; r < ROWS; ++r) {
          if (b[r] == FULL) continue;
          bool filled = b[r] >> c & 1;
          if (filled && !seen) { seen = true; h[c] = ROWS - r; }
          if (!filled && seen) ++holes;
        }
        h[c] = h[c] > cleared ? h[c] - cleared : 0;
        agg += h[c];
      }
      for (int c = 0; c + 1 < COLS; ++c) bump += abs(h[c] - h[c + 1]);
      float score = -0.510066f * agg + 0.760666f * cleared - 0.35663f * holes - 0.184483f * bump;
      score += frand(0, 0.01f);                // ties go either way: a little variety
      if (score > best) { best = score; t.targetRot = rot; t.targetX = x; }
    }
  }
}

void IdleGames::tetrisSpawn() {
  Tetris& t = tet_;
  t.piece = t.next;
  t.next = tetrisDraw7();
  t.rot = 0;
  t.x = 3;
  t.y = -1;
  t.phase = 0;
  t.timer = 0;
  int top = ROWS;
  for (int r = 0; r < ROWS; ++r) if (t.rows[r]) { top = r; break; }
  if (top < 4 || tetrisCollides(t.rows, t.piece, t.rot, t.x, t.y)) {   // nearly full: wipe and carry on
    t.phase = 3;
    return;
  }
  tetrisPlan();
}

void IdleGames::tetrisStep() {
  Tetris& t = tet_;
  if (t.phase == 0) {          // turning and sliding into place, drifting down a little
    if (t.rot != t.targetRot) {
      uint8_t r = (t.rot + 1) & 3;
      if (!tetrisCollides(t.rows, t.piece, r, t.x, t.y)) t.rot = r;
      else t.phase = 1;
    } else if (t.x != t.targetX) {
      int nx = t.x + (t.targetX > t.x ? 1 : -1);
      if (!tetrisCollides(t.rows, t.piece, t.rot, nx, t.y)) t.x = nx;
      else t.phase = 1;
    } else {
      t.phase = 1;
    }
    if (t.phase == 0 && ++t.timer % 3 == 0 && !tetrisCollides(t.rows, t.piece, t.rot, t.x, t.y + 1)) ++t.y;
    return;
  }
  if (t.phase == 1) {          // dropping
    if (!tetrisCollides(t.rows, t.piece, t.rot, t.x, t.y + 1)) {
      ++t.y;
      return;
    }
    uint16_t m = SHAPES[t.piece][t.rot];
    for (int r = 0; r < 4; ++r)
      for (int c = 0; c < 4; ++c)
        if (cell(m, r, c) && t.y + r >= 0) t.rows[t.y + r] |= 1 << (t.x + c);
    t.clearRows = 0;
    for (int r = 0; r < ROWS; ++r) if (t.rows[r] == FULL) t.clearRows |= 1UL << r;
    if (t.clearRows) {
      t.phase = 2;
      t.timer = 0;
    } else {
      tetrisSpawn();
    }
    return;
  }
  if (t.phase == 2) {          // full rows flash, then go
    if (++t.timer < 6) return;
    int w = ROWS - 1;
    for (int r = ROWS - 1; r >= 0; --r) {
      if (t.clearRows >> r & 1) { ++t.lines; continue; }
      t.rows[w--] = t.rows[r];
    }
    while (w >= 0) t.rows[w--] = 0;
    t.clearRows = 0;
    tetrisSpawn();
    return;
  }
  // phase 3: wipe. Fill from the bottom up, then empty from the top down, then a new game.
  ++t.timer;
  if (t.timer <= ROWS) {
    t.rows[ROWS - t.timer] = FULL;
  } else if (t.timer <= 2 * ROWS) {
    t.rows[t.timer - ROWS - 1] = 0;
  } else {
    tetrisReset();
  }
}

void IdleGames::tetrisDraw(const char* clock, int dx, int dy) {
  const Tetris& t = tet_;
  u8g2_.drawFrame(WELL_X - 2, 0, COLS * CELL + 4, 64);
  bool flashOn = (t.timer / 2) % 2 == 0;
  for (int r = 0; r < ROWS; ++r) {
    if ((t.clearRows >> r & 1) && t.phase == 2) {
      if (flashOn) u8g2_.drawBox(WELL_X, WELL_Y + r * CELL, COLS * CELL - 1, CELL - 1);
      continue;
    }
    for (int c = 0; c < COLS; ++c)
      if (t.rows[r] >> c & 1) u8g2_.drawBox(WELL_X + c * CELL, WELL_Y + r * CELL, CELL - 1, CELL - 1);
  }
  if (t.phase <= 1) {
    uint16_t m = SHAPES[t.piece][t.rot];
    for (int r = 0; r < 4; ++r)
      for (int c = 0; c < 4; ++c)
        if (cell(m, r, c) && t.y + r >= 0)
          u8g2_.drawBox(WELL_X + (t.x + c) * CELL, WELL_Y + (t.y + r) * CELL, CELL - 1, CELL - 1);
  }
  // left: the time (number big, AM / PM small under it) and the lines cleared
  char num[12] = "", ampm[4] = "";
  sscanf(clock, "%11s %3s", num, ampm);
  u8g2_.setFont(u8g2_font_helvB10_tr);
  u8g2_.drawStr(23 - u8g2_.getStrWidth(num) / 2 + dx, 16 + dy, num);
  u8g2_.setFont(u8g2_font_5x8_tr);
  if (ampm[0]) u8g2_.drawStr(23 - u8g2_.getStrWidth(ampm) / 2 + dx, 26 + dy, ampm);
  u8g2_.drawStr(4 + dx, 50 + dy, "LINES");
  char buf[8];
  snprintf(buf, sizeof(buf), "%u", t.lines);
  u8g2_.drawStr(4 + dx, 60 + dy, buf);
  // right: the next piece
  u8g2_.drawStr(92 + dx, 10 + dy, "NEXT");
  uint16_t nm = SHAPES[t.next][0];
  for (int r = 0; r < 4; ++r)
    for (int c = 0; c < 4; ++c)
      if (cell(nm, r, c)) u8g2_.drawBox(94 + c * 4 + dx, 16 + r * 4 + dy, 3, 3);
}

// ── Frogger ──────────────────────────────────────────────────────────────────────────────────────────────────────
// Rows from the top: 0 home (grass), 1-3 a river with logs, 4 the bank in the middle, 5-7 a road with cars, 8 the
// start. The frog only hops when the row ahead will be safe when it lands (a gap in the traffic, a log under it)
// and for a moment after; on the road it steps back or sideways if a car would reach it. At the top it celebrates,
// the traffic gets a little faster, and it starts again from the bottom.

float IdleGames::laneX(const Lane& l, int i, int t) const { return wrapX(l.x[i] + l.dir * l.speed * t); }

void IdleGames::frogReset(bool keepLevel) {
  uint8_t level = keepLevel ? frog_.level : 1;
  frog_ = Frogger();
  frog_.level = level;
  float k = 1.0f + 0.08f * (level - 1);
  const float riverSpeed[3] = {0.6f, 0.9f, 0.7f}, roadSpeed[3] = {0.9f, 1.3f, 0.7f};
  for (int r = 1; r <= 7; ++r) {
    if (!river(r) && !road(r)) continue;
    Lane& l = frog_.lanes[r];
    l.dir = (r % 2) ? 1 : -1;
    l.speed = (river(r) ? riverSpeed[r - 1] : roadSpeed[r - 5]) * k;
    l.count = river(r) ? 3 : (r == 6 ? 2 : 3);
    float gap = SPAN / l.count;
    for (int i = 0; i < l.count; ++i) {
      l.len[i] = river(r) ? random(24, 35) : random(10, 15);
      l.x[i] = wrapX(i * gap + random(0, 10));
    }
  }
  frog_.fx = random(2, 13) * 8 + 2;
  frog_.row = 8;
  frog_.wait = 8;
}

bool IdleGames::frogSafe(uint8_t row, float fx, int fromT, int toT) const {
  if (fx < 0 || fx > 123) return false;               // stay on the screen
  if (!river(row) && !road(row)) return true;
  const Lane& l = frog_.lanes[row];
  if (road(row)) {
    for (int t = fromT; t <= toT; ++t)
      for (int i = 0; i < l.count; ++i) {
        float cx = laneX(l, i, t);
        if (fx + 6 > cx && fx - 1 < cx + l.len[i]) return false;
      }
    return true;
  }
  // river: there has to be a log under it when it lands, with a pixel to spare each side
  for (int i = 0; i < l.count; ++i) {
    float lx = laneX(l, i, fromT);
    if (fx >= lx + 1 && fx + 5 <= lx + l.len[i] - 1) return true;
  }
  return false;
}

void IdleGames::frogStep() {
  Frogger& f = frog_;
  // traffic and logs move; a frog on a log goes with it
  for (int r = 1; r <= 7; ++r) {
    Lane& l = f.lanes[r];
    for (int i = 0; i < l.count; ++i) l.x[i] = wrapX(l.x[i] + l.dir * l.speed);
  }
  if (river(f.row) && f.hop == 0) f.fx = wrapX(f.fx + f.lanes[f.row].dir * f.lanes[f.row].speed);

  if (f.party) {
    if (--f.party == 0) {
      if (f.level < 8) ++f.level;
      frogReset(true);
    }
    return;
  }
  if (f.hop) {
    --f.hop;
    if (f.hop == 0 && f.row == 0) f.party = 24;      // made it home
    return;
  }
  bool visible = f.fx >= 0 && f.fx <= 123;
  // on the road: is a car about to reach it where it stands?
  bool danger = road(f.row) && !frogSafe(f.row, f.fx, 0, 4);
  if (f.wait && !danger) {
    --f.wait;
    return;
  }
  auto hopTo = [&](uint8_t row, float fx) {
    f.row = row;
    f.fx = fx;
    f.hop = HOP;
    f.wait = random(1, 5);
    f.stuck = 0;
  };
  if (visible && f.row > 0 && frogSafe(f.row - 1, f.fx, HOP, HOP + 6)) {   // forward
    hopTo(f.row - 1, f.fx);
    return;
  }
  if (danger) {                                                         // back, or a step aside
    if (f.row < 8 && frogSafe(f.row + 1, f.fx, HOP, HOP + 6)) { hopTo(f.row + 1, f.fx); return; }
    for (float side : {-6.0f, 6.0f})
      if (frogSafe(f.row, f.fx + side, HOP, HOP + 6)) { hopTo(f.row, f.fx + side); return; }
  }
  // waiting a long while on safe ground or the road: try a step sideways, to line up with another gap
  if (!river(f.row) && ++f.stuck > 45) {
    float side = random(2) ? 6.0f : -6.0f;
    if (frogSafe(f.row, f.fx + side, HOP, HOP + 6)) hopTo(f.row, f.fx + side);
    f.stuck = 0;
  }
}

void IdleGames::frogDraw(const char* clock, int dx, int dy) {
  const Frogger& f = frog_;
  u8g2_.setFont(u8g2_font_5x8_tr);
  char buf[16];
  snprintf(buf, sizeof(buf), "FROG LV%u", f.level);
  u8g2_.drawStr(1 + (dx > 0 ? dx : 0), 7 + (dy > 0 ? 0 : dy), buf);
  u8g2_.drawStr(125 - u8g2_.getStrWidth(clock) + (dx < 0 ? dx : 0), 7 + (dy > 0 ? 0 : dy), clock);   // never past the edge
  // home row: grass tufts; the bank and the start: a dotted edge
  for (int x = 1; x < 128; x += 4) {
    u8g2_.drawPixel(x, rowY(0) + 4);
    u8g2_.drawPixel(x + 1, rowY(0) + 3);
  }
  for (int x = 0; x < 128; x += 2) {
    u8g2_.drawPixel(x, rowY(4));
    u8g2_.drawPixel(x, rowY(5) - 1);
    u8g2_.drawPixel(x, rowY(8));
  }
  // road lane marks
  for (int r = 6; r <= 7; ++r)
    for (int x = static_cast<int>(frame_ % 8); x < 128; x += 8) u8g2_.drawHLine(x, rowY(r), 3);
  for (int r = 1; r <= 7; ++r) {
    const Lane& l = f.lanes[r];
    if (river(r)) {
      for (int x = (r * 5 + static_cast<int>(frame_ / 3)) % 8; x < 128; x += 8) u8g2_.drawPixel(x, rowY(r) + 3);
    }
    for (int i = 0; i < l.count; ++i) {
      int x = static_cast<int>(lroundf(l.x[i])), y = rowY(r);
      if (river(r)) {                                 // a log: solid, with a groove
        u8g2_.drawBox(x, y + 1, l.len[i], 4);
        u8g2_.setDrawColor(0);
        u8g2_.drawHLine(x + 3, y + 2, l.len[i] - 6);
        u8g2_.setDrawColor(1);
      } else {                                        // a car: rounded, with a window toward its front
        u8g2_.drawRBox(x, y + 1, l.len[i], 4, 1);
        u8g2_.setDrawColor(0);
        u8g2_.drawBox(l.dir > 0 ? x + l.len[i] - 4 : x + 2, y + 2, 2, 1);
        u8g2_.setDrawColor(1);
      }
    }
  }
  // the frog, drawn inverted so it shows on a log too; mid-hop it's a pixel up; at home it blinks
  if (f.party && (f.party / 3) % 2) return;
  static const uint8_t FROG[5] = {0b01010, 0b11111, 0b01110, 0b11111, 0b10001};
  int fx = static_cast<int>(lroundf(f.fx)), fy = rowY(f.row) + (f.hop ? -1 : 0);
  u8g2_.setDrawColor(2);
  for (int r = 0; r < 5; ++r)
    for (int c = 0; c < 5; ++c)
      if (FROG[r] >> (4 - c) & 1) u8g2_.drawPixel(fx + c, fy + r);
  u8g2_.setDrawColor(1);
}
