#pragma once

#include <Arduino.h>
#include <U8g2lib.h>

// Idle games: what the home screen can show instead of the clock while Rocky sleeps. Each one plays itself and
// never loses: the Pong paddles never miss, Tetris clears the board and starts over before it tops out, and the
// frog waits for gaps instead of getting hit. The time of day stays in a corner. Drawn at about 15 frames a second
// (HomeScreen calls step() then draw() each frame); everything moves, so a game is easy on the OLED.

// Roam isn't a game: the face stays up and the head looks around the room (main.cpp, roamUpdate). Night: the clock.
enum class IdleMode : uint8_t { Clock, Pong, Tetris, Frogger, Roam };

const char* idleModeName(IdleMode m);
bool idleModeFromName(const char* name, IdleMode& out);

class IdleGames {
 public:
  explicit IdleGames(U8G2& display) : u8g2_(display) {}

  // A fresh game of `m` (Clock: nothing to do).
  void reset(IdleMode m);
  IdleMode mode() const { return mode_; }
  // One frame forward.
  void step();
  // Into the screen buffer (the caller clears it first and sends it after). `clock` is the time to show ("7:35 PM"),
  // dx / dy the burn-in shift for that text.
  void draw(const char* clock, int dx, int dy);

 private:
  // ── Pong ──
  struct Pong {
    float bx = 64, by = 36, vx = 2.2f, vy = 1.0f;
    float pad[2] = {36, 36};   // paddle centres (left, right)
    float aim[2] = {0, 0};     // where on the paddle it means to hit (changes the bounce)
    uint16_t hits = 0;
  };
  void pongReset();
  void pongStep();
  void pongDraw(const char* clock, int dx, int dy);
  float pongPredict(int side) const;

  // ── Tetris ──
  struct Tetris {
    uint16_t rows[20] = {};    // 10 bits each, bit 0 = left column; row 0 is the top
    uint8_t piece = 0, rot = 0, next = 0;
    int8_t x = 3, y = -1;
    uint8_t targetRot = 0;
    int8_t targetX = 3;
    uint8_t phase = 0;         // 0 moving into place, 1 dropping, 2 clearing lines, 3 wiping the board
    uint8_t timer = 0;
    uint32_t clearRows = 0;    // rows being cleared, bit per row
    uint16_t lines = 0;
    uint8_t bag[7] = {};
    uint8_t bagLeft = 0;
  };
  void tetrisReset();
  void tetrisStep();
  void tetrisDraw(const char* clock, int dx, int dy);
  void tetrisSpawn();
  uint8_t tetrisDraw7();
  bool tetrisCollides(const uint16_t* rows, uint8_t piece, uint8_t rot, int x, int y) const;
  void tetrisPlan();

  // ── Frogger ──
  struct Lane {
    int8_t dir = 1;
    float speed = 0.5f;
    float x[4] = {};           // object left edges, wrapping round SPAN
    uint8_t len[4] = {};
    uint8_t count = 0;
  };
  struct Frogger {
    Lane lanes[9];             // rows 1-3 river (logs), 5-7 road (cars); 0, 4, 8 are safe ground
    float fx = 60;             // frog's left edge
    uint8_t row = 8;           // 0 = home at the top, 8 = the start
    uint8_t hop = 0;           // frames left of a hop (drawn mid-jump)
    uint8_t wait = 0;          // frames before the next decision
    uint8_t party = 0;         // frames left of the celebration at the top
    uint8_t level = 1;
    uint16_t stuck = 0;        // frames without a move
  };
  void frogReset(bool keepLevel);
  void frogStep();
  void frogDraw(const char* clock, int dx, int dy);
  bool frogSafe(uint8_t row, float fx, int fromT, int toT) const;
  float laneX(const Lane& l, int i, int t) const;

  U8G2& u8g2_;
  IdleMode mode_ = IdleMode::Clock;
  uint32_t frame_ = 0;
  Pong pong_;
  Tetris tet_;
  Frogger frog_;
};
