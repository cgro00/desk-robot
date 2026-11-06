// desk-robot firmware: an expressive face, a turning head, a speaker, a
// microphone, and a camera. Driven over USB serial (115200 baud,
// type `help`) and, when include/secrets.h exists, over WiFi by the brain.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <U8g2lib.h>
#include <WiFi.h>
#include <Wire.h>

#include "camera.h"
#include "config.h"
#include "face.h"
#include "home_screen.h"
#include "link.h"
#include "mic.h"
#include "servo_neck.h"
#include "speaker.h"

#if __has_include("secrets.h")
#include "secrets.h"
#define HAVE_BRAIN 1
#else
#define HAVE_BRAIN 0
#endif

// The 128x64 OLED over hardware I2C (SDA=D4/GPIO5, SCL=D5/GPIO6): the V2 head's 2.42" SSD1309, or V1's 1.3"
// SH1106 (OLED_SSD1309_242 in config.h). OLED_FLIP picks R2 = rotated 180 degrees for an upside-down mount.
#if OLED_SSD1309_242
U8G2_SSD1309_128X64_NONAME0_F_HW_I2C u8g2(OLED_FLIP ? U8G2_R2 : U8G2_R0, U8X8_PIN_NONE);
#else
U8G2_SH1106_128X64_NONAME_F_HW_I2C u8g2(OLED_FLIP ? U8G2_R2 : U8G2_R0, U8X8_PIN_NONE);
#endif

Face face(u8g2);
HomeScreen home(u8g2);

// What the OLED shows. Asleep = the home screen (clock, date, weather); awake = the face. Going to sleep shows the
// sleep face for SLEEP_FACE_MS first. Power-up starts on the home screen; the brain wakes him ("hey Rocky").
enum class Screen : uint8_t { Home, Face, Sleeping };
Screen screen = Screen::Home;
volatile bool roamActive = false;   // the "roam" idle screen is moving the head (roamUpdate)
uint32_t sleepFaceUntil = 0;
constexpr uint32_t SLEEP_FACE_MS = 5000;
ServoNeck panNeck;
ServoNeck tiltNeck;
Link brainLink;
Speaker speaker;
Mic mic;
Camera camera;
uint32_t nextTempMs = 0;
volatile bool speakDonePending = false;  // set by the speaker task, sent from loop()
uint32_t slowestLoopMs = 0;              // longest pass of loop() during the last reply (reported with it)
// While he talks the camera stream is thinned to this (8 fps), so sending pictures doesn't crowd out his voice
// arriving over the same WiFi link. (It was 4 fps while the face was drawn in loop(); the face has its own task now.)
constexpr uint32_t TALKING_FRAME_MS = 125;
// Asleep, 5 fps: all the brain does with the stream then is watch for a wave, 5 times a second. The other pictures
// would only be sent for nothing (WiFi sending is what heats the chip most).
constexpr uint32_t ASLEEP_FRAME_MS = 200;
// Talking mouth while the voice plays on the computer instead of our speaker: the brain sends `mouth <level>`
// (the voice's loudness) ~25 times a second, and -1 when it's done. Stale after MOUTH_TIMEOUT_MS.
float brainMouthLevel = -1.0f;
uint32_t brainMouthMs = 0;
constexpr uint32_t MOUTH_TIMEOUT_MS = 300;

// `loop`: the slowest time each part of loop() has taken since the last `loop` (finding what stalls the face).
enum LoopPart : uint8_t { LP_LINK, LP_MIC, LP_SNAPSHOT, LP_JPEG, LP_FACE, LP_SERVO, LP_WHOLE, LP_COUNT };
const char* const LOOP_PART_NAMES[LP_COUNT] = {"link", "mic", "snapshot", "jpeg", "face", "servo", "whole pass"};
uint32_t loopWorstUs[LP_COUNT] = {};
uint32_t loopPasses = 0, loopSlowPasses = 0;   // passes, and passes over 40 ms (the face misses a frame)
uint32_t passPartUs[LP_COUNT] = {};             // this pass, part by part (a pass over 1 s is reported)
uint32_t stallWorstMs = 0;                     // the longest pass since the brain last connected, and its slowest part
uint8_t stallWorstPart = LP_WHOLE;
inline void loopTime(LoopPart p, uint32_t t0) {
  uint32_t dt = micros() - t0;
  passPartUs[p] = dt;
  if (dt > loopWorstUs[p]) loopWorstUs[p] = dt;
}

// The screen (face, clock, assembly screen) and the servos run in tasks of their own, so nothing loop() waits on
// (a camera picture going out, a message still arriving over WiFi) can freeze the face or stall the head. Each task
// holds its lock while it works; anything else that changes the face, the screen or the servos takes it too.
SemaphoreHandle_t screenLock;   // face, home screen, which screen is up, the OLED and its I2C bus
SemaphoreHandle_t neckLock;     // both servos
struct Hold {
  SemaphoreHandle_t m;
  explicit Hold(SemaphoreHandle_t lock) : m(lock) { xSemaphoreTakeRecursive(m, portMAX_DELAY); }
  ~Hold() { xSemaphoreGiveRecursive(m); }
};
uint32_t faceGapWorstMs = 0;     // longest time between two face frames since the last `loop`
uint32_t faceGapSpeechMs = 0;    // ... during the last reply (reported with it)
TaskHandle_t screenTaskHandle = nullptr, neckTaskHandle = nullptr;
void handleCommand(String line);

uint32_t nextStateMs = 0;
bool demoMode = false; // `demo on`: cycles emotions on its own (to show the faces off without the brain)
bool brainConnected = false;
bool glanceWanted = true;  // the brain's `glance on|off` (off while it holds a pose or tracks a face)
bool assemblyMode = false; // servos held at centre for fitting gears and horns (BOOT button / `assembly`)

// Idle glances only while Rocky is awake and his brain is connected. Asleep,
// or with no server running, the head stays still.
void applyGlances() {
  bool on = !assemblyMode && brainConnected && !face.asleep() && glanceWanted;
  panNeck.setIdleGlances(on);
  tiltNeck.setIdleGlances(on);
}
uint32_t nextDemoEmotionMs = 0;
uint8_t demoEmotionIdx = 0;

void setAssemblyMode(bool on) {
  assemblyMode = on;
  if (on) {
    panNeck.setTarget(0);
    tiltNeck.setTarget(0);
  }
  applyGlances();
  Serial.println(on ? F("assembly mode: both servos centred and held. Fit the pan gear and the tilt horn now.")
                    : F("assembly mode off"));
}

// What the OLED shows in assembly mode instead of the face.
void drawAssemblyScreen() {
  u8g2.clearBuffer();
  u8g2.setFont(u8g2_font_6x10_tr);
  u8g2.drawStr(34, 12, "ASSEMBLY");
  u8g2.drawStr(10, 26, "servos centred");
  u8g2.drawStr(10, 38, "+ held. Fit gears");
  u8g2.drawStr(10, 50, "BOOT again = exit");
  u8g2.drawFrame(0, 0, 128, 64);
  u8g2.sendBuffer();
}

void printHelp() {
  Serial.println(F("desk-robot commands:"));
  Serial.println(F("  emo <name>   neutral|happy|sad|angry|surprised|sleepy|thinking|curious|skeptical|excited|love|confused"));
  Serial.println(F("  face         face pack in use | face still <emotion> | face live | face pack <json>"));
  Serial.println(F("  pan <deg>    turn head, -95 (left)..95 (right), 0 = center"));
  Serial.println(F("  tilt <deg>   nod head, -85 (down)..85 (up), 0 = level"));
  Serial.println(F("  center       head to center on both axes"));
  Serial.println(F("  assembly on|off  hold both servos at centre for fitting gears/horns (or BOOT within 5 s of power-up)"));
  Serial.println(F("  motion pan|tilt <speed> <accel>  tune head motion live (deg/s, deg/s^2; the brain sends it too)"));
  Serial.println(F("  raw pan|tilt <deg>  calibration move to any head angle the servo can reach, ignoring the limits (watch it!)"));
  Serial.println(F("  blink        blink now"));
  Serial.println(F("  sleep on|off eyes shut, breathing, Z's"));
  Serial.println(F("  demo on|off  wake up and cycle through the faces on his own"));
  Serial.println(F("  home         clock / weather settings and status"));
  Serial.println(F("  screen       dump what the OLED shows (hex), for a screenshot"));
  Serial.println(F("  weather      fetch the weather now"));
  Serial.println(F("  settings <json>  apply a settings message, as the brain sends it"));
  Serial.println(F("  glance on|off  allow idle head glances (only happen while the brain is connected and he is awake)"));
  Serial.println(F("  volume <0-1> speaker volume"));
  Serial.println(F("  beep         play a test tone through the speaker"));
  Serial.println(F("  oled off|on | oled <cmd> [arg]  screen power, or a raw SSD1309 command in hex (whine hunting)"));
  Serial.println(F("  mic on|off   stream the microphone to the brain"));
  Serial.println(F("  miclevel     print mic level for 3 s (talk to it)"));
  Serial.println(F("  stream on|off [fps] [qvga|vga]  stream camera JPEGs to the brain"));
  Serial.println(F("  snap         grab one frame and report its size"));
  Serial.println(F("  snapshot xga one sharp still (vga svga xga sxga uxga) for the brain"));
  Serial.println(F("  servo        check the servo PWM timing"));
  Serial.println(F("  temp         chip temperature"));
  Serial.println(F("  loop         slowest time of each part of the main loop since the last `loop`"));
  Serial.println(F("  help         this text"));
}

static void runCommand(String line) {
  line.trim();
  if (line.isEmpty()) return;

  int space = line.indexOf(' ');
  String cmd = (space < 0) ? line : line.substring(0, space);
  String arg = (space < 0) ? String() : line.substring(space + 1);
  cmd.toLowerCase();
  arg.trim();

  if (cmd == "help") {
    printHelp();
  } else if (cmd == "emo") {
    Emotion e;
    if (emotionFromName(arg.c_str(), e)) {
      face.setEmotion(e);
      Serial.printf("emotion: %s\n", emotionName(e));
    } else {
      Serial.print(F("unknown emotion — try:"));
      for (int i = 0; i < static_cast<int>(Emotion::COUNT); ++i) Serial.printf(" %s", emotionName(static_cast<Emotion>(i)));
      Serial.println();
    }
  } else if (cmd == "face") {
    // face | face still <emotion> | face live | face pack <json>
    if (arg.startsWith("still")) {
      Emotion e = Emotion::Neutral;
      String name = arg.substring(5);
      name.trim();
      if (name.length() && !emotionFromName(name.c_str(), e)) {
        Serial.println(F("face still <emotion>"));
        return;
      }
      if (screen != Screen::Face) {
        screen = Screen::Face;
        home.restoreDisplay();
      }
      face.setStill(true, e);
      Serial.printf("face: still %s\n", emotionName(e));
    } else if (arg == "live") {
      face.setStill(false);
      Serial.println(F("face: live"));
    } else if (arg.startsWith("pack ")) {
      bool ok = face.applyPackJson(arg.substring(5).c_str());
      Serial.printf("face: %s\n", ok ? face.packName() : "that pack doesn't parse");
    } else {
      Serial.printf("face: pack \"%s\", emotion %s\n", face.packName(), emotionName(face.emotion()));
    }
  } else if (assemblyMode && (cmd == "pan" || cmd == "tilt")) {
    Serial.println(F("assembly mode: head held at centre (`assembly off` to leave)"));
  } else if (cmd == "assembly") {
    setAssemblyMode(arg == "on");
  } else if (cmd == "pan") {
    panNeck.setTarget(arg.toFloat());
    Serial.printf("pan -> %.0f deg\n", panNeck.target());
  } else if (cmd == "tilt") {
    tiltNeck.setTarget(arg.toFloat());
    Serial.printf("tilt -> %.0f deg\n", tiltNeck.target());
  } else if (cmd == "raw") {
    // raw pan|tilt <deg>: calibration, ignores the limits. Watch the mechanism!
    int sp = arg.indexOf(' ');
    String axis = sp < 0 ? arg : arg.substring(0, sp);
    float deg = sp < 0 ? 0 : arg.substring(sp + 1).toFloat();
    if (axis == "tilt") tiltNeck.setRaw(deg);
    else if (axis == "pan") panNeck.setRaw(deg);
    else { Serial.println(F("usage: raw pan|tilt <deg>")); return; }
    Serial.printf("raw %s -> %.0f deg (limits ignored)\n", axis.c_str(), (axis == "tilt" ? tiltNeck : panNeck).current());
  } else if (cmd == "center") {
    panNeck.setTarget(0);
    tiltNeck.setTarget(0);
    Serial.println(F("head -> center"));
  } else if (cmd == "blink") {
    face.blink();
  } else if (cmd == "sleep") {
    bool on = arg == "on";
    face.setAsleep(on);
    if (on) {                   // head home (straight ahead, level) to sleep; the face shows it, then the clock
      panNeck.setTarget(0);
      tiltNeck.setTarget(0);
      if (screen == Screen::Face) {
        screen = Screen::Sleeping;
        sleepFaceUntil = millis() + SLEEP_FACE_MS;
      }
    } else {
      screen = Screen::Face;
      home.restoreDisplay();
    }
    applyGlances();
    Serial.printf("sleep %s\n", face.asleep() ? "on" : "off");
  } else if (cmd == "screen") {
    // what the OLED shows right now, as hex (8 pages x 128 columns, a byte = 8 pixels down), for a screenshot
    uint8_t* buf = u8g2.getBufferPtr();
    Serial.print(F("screen:"));
    for (int i = 0; i < 1024; ++i) Serial.printf("%02x", buf[i]);
    Serial.println();
  } else if (cmd == "home") {
    home.printStatus();
  } else if (cmd == "weather") {
    home.refreshWeather();
    Serial.println(F("weather: fetching (see `home` in a few seconds)"));
  } else if (cmd == "settings") {
    if (home.applyJson(arg.c_str())) {
      if (screen != Screen::Home) home.restoreDisplay();   // brightness for the face
      Serial.println(F("settings: applied and saved"));
    } else {
      Serial.println(F("settings: not valid JSON"));
    }
  } else if (cmd == "oled") {
    // oled off|on, or oled <cmd> [arg] in hex: a raw SSD1309 command for hunting the module's coil whine, e.g.
    // `oled d5 f0` (display clock: osc freq high nibble, divide low), `oled 81 40` (contrast), `oled d9 22`
    // (pre-charge). Lasts until the next reboot.
    if (arg == "off" || arg == "on") {
      u8g2.setPowerSave(arg == "off");
      Serial.printf("oled %s\n", arg.c_str());
    } else {
      int sp = arg.indexOf(' ');
      long c = strtol((sp < 0 ? arg : arg.substring(0, sp)).c_str(), nullptr, 16);
      u8x8_t* u = u8g2.getU8x8();
      u8x8_cad_StartTransfer(u);
      u8x8_cad_SendCmd(u, static_cast<uint8_t>(c));
      if (sp > 0) u8x8_cad_SendArg(u, static_cast<uint8_t>(strtol(arg.substring(sp + 1).c_str(), nullptr, 16)));
      u8x8_cad_EndTransfer(u);
      Serial.printf("oled cmd 0x%02lX%s\n", c, sp > 0 ? (" " + arg.substring(sp + 1)).c_str() : "");
    }
  } else if (cmd == "speak_begin") {
    speaker.beginSpeech(static_cast<size_t>(arg.toInt()));
    slowestLoopMs = 0;
    faceGapSpeechMs = 0;
  } else if (cmd == "speak_end") {
    speaker.endSpeech();
  } else if (cmd == "mouth") {
    brainMouthLevel = arg.toFloat();   // no print: it comes 25 times a second
    brainMouthMs = millis();
  } else if (cmd == "volume") {
    speaker.setVolume(arg.toFloat());
    Serial.printf("volume -> %.2f\n", arg.toFloat());
  } else if (cmd == "beep") {
    // 0.4 s of 440 Hz so the amp can be checked without the brain.
    static int16_t tone[16000 * 4 / 10];
    for (size_t i = 0; i < sizeof(tone) / sizeof(tone[0]); ++i) {
      tone[i] = static_cast<int16_t>(8000 * sinf(2 * PI * 440 * i / 16000.0f));
    }
    speaker.beginSpeech();
    speaker.feed(reinterpret_cast<uint8_t*>(tone), sizeof(tone));
    speaker.endSpeech();
    Serial.println(F("beep"));
  } else if (cmd == "mic") {
    mic.setStreaming(arg == "on");
    Serial.printf("mic %s\n", mic.streaming() ? "on" : "off");
  } else if (cmd == "miclevel") {
    for (int i = 0; i < 12; ++i) {
      delay(250);
      int bars = static_cast<int>(mic.level() * 200);
      Serial.printf("  level %.3f %.*s\n", mic.level(), min(bars, 40), "########################################");
    }
  } else if (cmd == "stream") {
    // stream on|off [fps] [qvga|vga]
    int sp = arg.indexOf(' ');
    String onoff = sp < 0 ? arg : arg.substring(0, sp);
    String rest = sp < 0 ? String() : arg.substring(sp + 1);
    int sp2 = rest.indexOf(' ');
    float fps = rest.length() ? (sp2 < 0 ? rest : rest.substring(0, sp2)).toFloat() : 10.0f;
    if (sp2 >= 0) camera.setStreamSize(rest.substring(sp2 + 1).c_str());
    camera.setStreaming(onoff == "on", fps);
    Serial.printf("stream %s @ %.1f fps, %s\n", camera.streaming() ? "on" : "off", fps, camera.streamSize());
  } else if (cmd == "snapshot") {
    // snapshot <vga|svga|xga|sxga|uxga>: one sharp still; sent to the brain when ready (and reported here)
    bool ok = camera.requestSnapshot(arg.length() ? arg.c_str() : "xga");
    if (!ok) Serial.println(F("snapshot <vga|svga|xga|sxga|uxga> (camera must be running)"));
  } else if (cmd == "snap") {
    if (!camera.ok()) {
      Serial.println(F("camera not available"));
    } else {
      bool was = camera.streaming();
      camera.setStreaming(true, 10);
      static uint8_t* jpg = static_cast<uint8_t*>(ps_malloc(64 * 1024));
      size_t n = 0;
      for (int i = 0; i < 40 && n == 0 && jpg; ++i) { delay(25); n = camera.takeFrame(jpg, 64 * 1024); }
      camera.setStreaming(was, 10);
      Serial.printf("snap: %u bytes (%s)\n", n, n ? "ok" : "no frame");
    }
  } else if (cmd == "motion") {
    // motion pan|tilt <maxSpeed> <accel>: live tuning (until reboot); no args = show
    int sp1 = arg.indexOf(' ');
    String axis = sp1 < 0 ? arg : arg.substring(0, sp1);
    ServoNeck* n = axis == "pan" ? &panNeck : axis == "tilt" ? &tiltNeck : nullptr;
    if (n && sp1 > 0) {
      String rest = arg.substring(sp1 + 1);
      int sp2 = rest.indexOf(' ');
      float v = rest.toFloat();
      float a = sp2 > 0 ? rest.substring(sp2 + 1).toFloat() : n->accel();
      if (v > 0 && a > 0) n->setMotion(fminf(v, 720.0f), fminf(a, 6000.0f));   // past that it's only a jolt
    }
    Serial.printf("motion pan %.0f deg/s %.0f deg/s^2 | tilt %.0f deg/s %.0f deg/s^2\n",
                  panNeck.maxSpeed(), panNeck.accel(), tiltNeck.maxSpeed(), tiltNeck.accel());
  } else if (cmd == "servo") {
    // PWM check: each servo's timer, and where in the period a start/stop would happen (must be 150-850)
    for (ServoNeck* n : {&panNeck, &tiltNeck}) {
      int worst = 1000, best = 0;
      for (int i = 0; i < 20; ++i) {
        int p = n->gapCheck(i % 2);
        worst = min(worst, p);
        best = max(best, p);
      }
      Serial.printf("servo %s: period %u ticks, %s, switch points %d..%d of 1000 (pulse ends by 120)\n",
                    n == &panNeck ? "pan" : "tilt", static_cast<unsigned>(n->pwmPeriodTicks()),
                    n->powered() ? "powered" : "quiet", worst, best);
    }
  } else if (cmd == "loop") {
    Serial.printf("loop: %u passes, %u over 40 ms; worst ms:", static_cast<unsigned>(loopPasses), static_cast<unsigned>(loopSlowPasses));
    for (int i = 0; i < LP_COUNT; ++i) Serial.printf(" %s %.1f", LOOP_PART_NAMES[i], loopWorstUs[i] / 1000.0f);
    Serial.printf("; longest gap between face frames %u ms; stack left: screen %u, neck %u bytes\n",
                  static_cast<unsigned>(faceGapWorstMs),
                  screenTaskHandle ? static_cast<unsigned>(uxTaskGetStackHighWaterMark(screenTaskHandle)) : 0,
                  neckTaskHandle ? static_cast<unsigned>(uxTaskGetStackHighWaterMark(neckTaskHandle)) : 0);
    for (uint32_t& w : loopWorstUs) w = 0;
    faceGapWorstMs = 0;
    loopPasses = loopSlowPasses = 0;
  } else if (cmd == "temp") {
    Serial.printf("chip %.1f C\n", temperatureRead());
  } else if (cmd == "glance") {
    glanceWanted = (arg == "on");
    applyGlances();
    Serial.printf("glance %s\n", glanceWanted ? "on" : "off");
  } else if (cmd == "demo") {
    demoMode = (arg == "on");
    if (demoMode) handleCommand("sleep off");
    Serial.printf("demo %s\n", demoMode ? "on" : "off");
  } else {
    Serial.println(F("unknown command — type `help`"));
  }
}

// Commands change what the screen and the servos are doing, so they take both locks (the screen's first: its task
// can hold it for a whole frame, ~40 ms, the neck's task only for a moment). The slow console-only commands that
// touch neither don't, so a 3 s `miclevel` can't freeze the face.
void handleCommand(String line) {
  String head = line;
  head.trim();
  int sp = head.indexOf(' ');
  if (sp >= 0) head = head.substring(0, sp);
  head.toLowerCase();
  if (head == "miclevel" || head == "snap" || head == "loop" || head == "temp" || head == "help") {
    runCommand(line);
    return;
  }
  Hold screenHeld(screenLock);
  Hold neckHeld(neckLock);
  runCommand(line);
}

// The screen: the face as fast as the OLED takes it (up to 30 fps; ~23 in practice: drawing takes ~17 ms and the
// I2C transfer ~25 ms at the SSD1309's 400 kHz maximum), the clock / assembly screen twice a second, or an idle
// game ~15 times a second. The I2C
// driver waits for its transfer without holding the CPU, so loop() runs meanwhile.
static void screenTask(void*) {
  uint32_t lastFaceMs = 0, lastSlowMs = 0;
  for (;;) {
    uint32_t start = millis();
    {
      Hold held(screenLock);
      uint32_t now = millis();
      if (screen == Screen::Sleeping && static_cast<int32_t>(now - sleepFaceUntil) >= 0) screen = Screen::Home;
      if (assemblyMode || (screen == Screen::Home && !roamActive)) {
        if (now - lastSlowMs >= (assemblyMode ? 500 : home.frameMs())) {
          lastSlowMs = now;
          if (assemblyMode) drawAssemblyScreen();
          else home.draw();
        }
        lastFaceMs = 0;
      } else {
        uint32_t t0 = micros();
        bool brainTalking = brainMouthLevel >= 0 && millis() - brainMouthMs < MOUTH_TIMEOUT_MS;
        if (speaker.speaking()) face.setTalking(true, speaker.level());
        else face.setTalking(brainTalking, brainTalking ? brainMouthLevel : 0.0f);
        face.update(now);
        loopTime(LP_FACE, t0);
        if (lastFaceMs) {
          uint32_t gap = now - lastFaceMs;
          if (gap > faceGapWorstMs) faceGapWorstMs = gap;
          if (speaker.speaking() && gap > faceGapSpeechMs) faceGapSpeechMs = gap;
        }
        lastFaceMs = now;
      }
    }
    uint32_t spent = millis() - start;
    vTaskDelay(pdMS_TO_TICKS(spent < FRAME_INTERVAL_MS ? FRAME_INTERVAL_MS - spent : 1));
  }
}

// The servos on their own steady 50 Hz tick, so every servo pulse gets a fresh, evenly spaced position.
static void neckTask(void*) {
  TickType_t wake = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&wake, pdMS_TO_TICKS(SERVO_UPDATE_MS));
    Hold held(neckLock);
    uint32_t t0 = micros();
    // While one axis swings, keep the other powered so it can't sag. In assembly
    // mode both stay powered at centre so they can't be turned by hand.
    if (panNeck.moving() || assemblyMode) tiltNeck.hold();
    if (tiltNeck.moving() || assemblyMode) panNeck.hold();
    uint32_t now = millis();
    panNeck.update(now);
    tiltNeck.update(now);
    loopTime(LP_SERVO, t0);
  }
}

// ── Roaming: the "roam" idle screen ────────────────────────────────────────────────────────────────────────────
// While he's asleep, the idle screen is "roam" and it isn't night (then: the clock), his neutral face is up and the
// head looks round the room on its own: a new spot every few seconds anywhere across the pan range and half the
// tilt range, both servos moving at once and timed to arrive together, the eyes going first. Looks near level hold
// longer (the tilt servo rests there and the head goes quiet); looks up or well down are short, since the tilt servo
// has to keep holding them. The brain hears when the head starts and stops (`head_moving`), so a turning camera
// can't pass for a wave. Waking, night or another idle screen: head back to the middle. Runs in loop() (it sends to
// the brain), taking the locks for the face and the servos.
// Calmer than a look you ask for, but not slow: crawling servos step visibly (2026-10-21: up from 70/200 and 50/160,
// which looked jittery). The pauses between looks are set further down and didn't change.
constexpr float ROAM_PAN_SPEED = 105.0f, ROAM_PAN_ACCEL = 320.0f;
constexpr float ROAM_TILT_SPEED = 80.0f, ROAM_TILT_ACCEL = 260.0f;
constexpr float ROAM_TILT_RANGE = (TILT_MAX_DEG < -TILT_MIN_DEG ? TILT_MAX_DEG : -TILT_MIN_DEG) / 2;
struct RoamState {
  uint8_t phase = 0;           // 0 pick a spot, 1 eyes have gone: move, 2 moving, 3 settling
  uint32_t at = 0;             // when the next phase may start
  float pan = 0, tilt = 0;     // where it's going
  bool told = false;           // the brain was told the head is moving
} roam;

static float frandIn(float lo, float hi) { return lo + (hi - lo) * (random(0, 10001) / 10000.0f); }

// Seconds a move of d degrees takes at speed v, acceleration a (speed up, maybe cruise, slow down).
static float moveTime(float d, float v, float a) { return d <= v * v / a ? 2.0f * sqrtf(d / a) : d / v + v / a; }

// The fraction of (v, a) at which a move of d takes T seconds: the quicker axis slowed to arrive with the other.
static float slowTo(float d, float v, float a, float T) {
  float lo = 0.02f, hi = 1.0f;
  for (int i = 0; i < 20; ++i) {
    float k = (lo + hi) / 2;
    if (moveTime(d, v * k, a * k) > T) lo = k; else hi = k;
  }
  return hi;
}

static void roamTell(bool moving) {
#if HAVE_BRAIN
  if (moving == roam.told) return;
  roam.told = moving;
  brainLink.sendJson(moving ? "{\"type\":\"head_moving\",\"on\":true}" : "{\"type\":\"head_moving\",\"on\":false}");
#else
  (void)moving;
#endif
}

static void roamUpdate(uint32_t now) {
  bool want = !assemblyMode && screen == Screen::Home && home.roamingNow();
  if (want != roamActive) {
    Hold screenHeld(screenLock);
    Hold neckHeld(neckLock);
    roamActive = want;
    face.setRoaming(want);
    if (want) {
      roam.phase = 0;
      roam.at = now + 1500;    // eyes open a moment before the first look
    } else {                   // back to normal speeds, head to the middle
      panNeck.setMotion(PAN_MAX_SPEED, PAN_ACCEL);
      tiltNeck.setMotion(TILT_MAX_SPEED, TILT_ACCEL);
      panNeck.setTarget(0);
      tiltNeck.setTarget(0);
      roamTell(false);
    }
    return;
  }
  if (!roamActive || static_cast<int32_t>(now - roam.at) < 0) return;
  if (roam.phase == 0) {
    // somewhere new: a glance from here, back to the middle, or anywhere across the room
    float pan0 = panNeck.target(), tilt0 = tiltNeck.target();
    int kind = random(100);
    float pan = kind < 25 ? pan0 + frandIn(15, 35) * (random(2) ? 1 : -1) : kind < 40 ? frandIn(-10, 10)
                                                                                     : frandIn(PAN_MIN_DEG, PAN_MAX_DEG);
    roam.pan = constrain(pan, PAN_MIN_DEG, PAN_MAX_DEG);
    int band = random(100);    // mostly near level, where the tilt servo can rest
    roam.tilt = band < 55 ? frandIn(TILT_RELAX_MIN_DEG, 0) : band < 80 ? frandIn(2, ROAM_TILT_RANGE)
                                                                       : frandIn(-ROAM_TILT_RANGE, TILT_RELAX_MIN_DEG - 1);
    {
      // the eyes go first. Pan + is his right, which is your left as you face the screen: pupils to screen left
      Hold screenHeld(screenLock);
      face.lookToward(-(roam.pan - pan0) / 60.0f, -(roam.tilt - tilt0) / 30.0f, 1800);
    }
    roam.phase = 1;
    roam.at = now + 250;
  } else if (roam.phase == 1) {
    Hold neckHeld(neckLock);
    float dp = fabsf(roam.pan - panNeck.current()), dt = fabsf(roam.tilt - tiltNeck.current());
    float tp = moveTime(dp, ROAM_PAN_SPEED, ROAM_PAN_ACCEL), tt = moveTime(dt, ROAM_TILT_SPEED, ROAM_TILT_ACCEL);
    float T = fmaxf(tp, tt);
    float kp = tp < T && dp > 0.5f ? slowTo(dp, ROAM_PAN_SPEED, ROAM_PAN_ACCEL, T) : 1.0f;
    float kt = tt < T && dt > 0.5f ? slowTo(dt, ROAM_TILT_SPEED, ROAM_TILT_ACCEL, T) : 1.0f;
    panNeck.setMotion(ROAM_PAN_SPEED * kp, ROAM_PAN_ACCEL * kp);
    tiltNeck.setMotion(ROAM_TILT_SPEED * kt, ROAM_TILT_ACCEL * kt);
    panNeck.setTarget(roam.pan);
    tiltNeck.setTarget(roam.tilt);
    roamTell(true);
    roam.phase = 2;
  } else if (roam.phase == 2) {
    if (panNeck.moving() || tiltNeck.moving()) return;
    roam.phase = 3;
    roam.at = now + 300;       // the head stops shaking
  } else {
    roamTell(false);
    bool quiet = roam.tilt >= TILT_RELAX_MIN_DEG && roam.tilt <= TILT_RELAX_MAX_DEG;
    roam.at = now + (quiet ? random(4000, 9001) : random(1500, 3501));
    roam.phase = 0;
  }
}

// Servo signal lines held low from the first moment, before anything else runs. Until a servo is started its line
// would otherwise float (nothing drives it) for about a second: the camera starts first, then pan, then tilt. A
// powered servo reads the noise on a floating line, and the pan's pulses on the wire beside it, as commands and
// jumps (the head snapping to look straight up at power-up). Low = no pulses = the servo stays where it is.
static void quietServoLines() {
  for (uint8_t pin : {PIN_SERVO_PAN, PIN_SERVO_TILT}) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }
}
// A constructor runs before setup(), a few ms after the bootloader hands over.
static struct QuietServosAtStart { QuietServosAtStart() { quietServoLines(); } } quietServosAtStart;

const char* resetReason() {   // also in the hello (link.cpp)
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "power on";
    case ESP_RST_BROWNOUT: return "BROWNOUT (the supply dipped: servos + camera on a weak USB port?)";
    case ESP_RST_PANIC: return "crash";
    case ESP_RST_INT_WDT: case ESP_RST_TASK_WDT: case ESP_RST_WDT: return "watchdog";
    case ESP_RST_SW: return "restart";
    default: return "other";
  }
}

void setup() {
  quietServoLines();   // again, in case the core touched the pins since
  screenLock = xSemaphoreCreateRecursiveMutex();
  neckLock = xSemaphoreCreateRecursiveMutex();
  Serial.begin(115200);
  randomSeed(esp_random());

  // ESP32Servo wants its LEDC timers claimed up front.
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);

  face.begin();
  // Camera first: the servos jump to home when they start, and that current
  // surge on USB power can brown out the camera's probe.
  if (camera.begin()) Serial.printf("camera: ready (%s)\n", camera.model());
  // Both claim their PWM output first (no pulses yet: claiming the second disturbs the first), then each goes to
  // home in turn, one servo surge at a time.
  panNeck.begin(PIN_SERVO_PAN, PAN_MIN_DEG, PAN_MAX_DEG, PAN_MAX_SPEED, PAN_ACCEL,
                SERVO_RELAX_MS, PAN_TRIM_DEG, /*glanceRange=*/25.0f,
                PAN_INVERT, PAN_GEAR_RATIO);
  tiltNeck.begin(PIN_SERVO_TILT, TILT_MIN_DEG, TILT_MAX_DEG, TILT_MAX_SPEED, TILT_ACCEL,
                 TILT_RELAX_MS, TILT_TRIM_DEG, /*glanceRange=*/10.0f,
                 TILT_INVERT);
  tiltNeck.setRelaxRange(TILT_RELAX_MIN_DEG, TILT_RELAX_MAX_DEG);
  tiltNeck.setRefresh(TILT_REFRESH_MS, TILT_REFRESH_BURST_MS);
  panNeck.hold();
  delay(300);
  tiltNeck.hold();

  speaker.begin(PIN_I2S_BCLK, PIN_I2S_LRC, PIN_I2S_DIN, SPEAKER_VOLUME,
                []() { speakDonePending = true; });
  mic.begin(PIN_PDM_CLK, PIN_PDM_DATA, MIC_GAIN);

  pinMode(PIN_BOOT_BUTTON, INPUT_PULLUP);

  face.setIdle(true);       // blinks and glances of the eyes whenever the face is up
  face.setAsleep(true);     // starts on the home screen
  home.begin();
  applyGlances();  // off: no brain yet
  nextDemoEmotionMs = millis() + 8000;

  Serial.println(F("\ndesk-robot v" FW_VERSION " — hello!"));
  Serial.printf("boot: %s\n", resetReason());
  printHelp();

#if HAVE_BRAIN
  brainLink.onAudio([](const uint8_t* pcm, size_t len) { speaker.feed(pcm, len); });
  brainLink.onHello([](JsonDocument& doc) {
    // How the last connection went from here: the longest loop() pass and where it went, the longest face gap.
    doc["stall_ms"] = stallWorstMs;
    if (stallWorstMs) doc["stall_part"] = LOOP_PART_NAMES[stallWorstPart];
    doc["face_gap_ms"] = faceGapWorstMs;
    stallWorstMs = 0;
    faceGapWorstMs = 0;
  });
  brainLink.onSettings([](const char* json) { handleCommand(String("settings ") + json); });
  brainLink.onFace([](const char* json) {
    // {"type":"face","save":true,"pack":{...}}: save = false while the dashboard previews an edit
    Hold held(screenLock);
    JsonDocument doc;
    if (deserializeJson(doc, json)) return;
    String pack;
    serializeJson(doc["pack"], pack);
    bool ok = face.applyPackJson(pack.c_str(), doc["save"] | true);
    Serial.printf("face: %s%s\n", ok ? face.packName() : "bad pack from the brain", (doc["save"] | true) ? "" : " (preview)");
  });
#ifndef ROBOT_TOKEN
#define ROBOT_TOKEN ""
#endif
  brainLink.begin(WIFI_SSID, WIFI_PASS, BRAIN_HOST, BRAIN_PORT, ROBOT_TOKEN,
             [](const String& cmd) { handleCommand(cmd); },
             [](bool connected) {
               // The brain says whether he's awake when it connects. If it goes away while he's awake, he can't hear
               // any more: he goes to sleep (sleep face, then the clock). Idle glances only while connected and awake.
               Hold screenHeld(screenLock);
               Hold neckHeld(neckLock);
               brainConnected = connected;
               if (!connected) {
                 // Camera and mic go quiet with the link: only the next brain session that asks starts them again
                 // (the brain sends `stream` and `mic` every time it connects).
                 mic.setStreaming(false);
                 camera.setStreaming(false, 10);
               }
               if (!connected && screen == Screen::Face && !demoMode) handleCommand("sleep on");
               applyGlances();
             });
  home.startClock();         // internet time, once WiFi is up
#else
  Serial.println(F("no include/secrets.h — USB-only mode (see secrets.h.example)"));
#endif
  // Above loop() (priority 1) on the same core, so they run on time; the neck's tick is short and comes first.
  xTaskCreatePinnedToCore(neckTask, "neck", 4096, nullptr, 3, &neckTaskHandle, 1);
  xTaskCreatePinnedToCore(screenTask, "screen", 8192, nullptr, 2, &screenTaskHandle, 1);
}

void loop() {
  // Serial console.
  static String lineBuf;
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      if (!lineBuf.isEmpty()) handleCommand(lineBuf);
      lineBuf = "";
    } else if (lineBuf.length() < 4096) {    // long enough for a `settings {...}` or `face pack {...}` line
      lineBuf += c;
    }
  }

  uint32_t now = millis();
  {
    static uint32_t lastPassUs = micros();
    uint32_t us = micros();
    if (loopPasses++) {
      if (us - lastPassUs > loopWorstUs[LP_WHOLE]) loopWorstUs[LP_WHOLE] = us - lastPassUs;
      if (us - lastPassUs > 40000) ++loopSlowPasses;
      // A pass over a second freezes everything that waits on loop() (the brain hears nothing, its heartbeat goes
      // unanswered): say where the time went, here and in the next hello.
      uint32_t passMs = (us - lastPassUs) / 1000;
      if (passMs > 1000) {
        uint8_t worst = LP_WHOLE;
        for (uint8_t i = 0; i < LP_WHOLE; ++i)
          if (worst == LP_WHOLE || passPartUs[i] > passPartUs[worst]) worst = i;
        Serial.printf("loop: one pass took %u ms, most in %s (%u ms)\n", static_cast<unsigned>(passMs),
                      LOOP_PART_NAMES[worst], static_cast<unsigned>(passPartUs[worst] / 1000));
        if (passMs > stallWorstMs) {
          stallWorstMs = passMs;
          stallWorstPart = worst;
        }
      }
    }
    for (uint32_t& p : passPartUs) p = 0;
    lastPassUs = us;
    static uint32_t lastPassMs = now;
    if (speaker.speaking() && now - lastPassMs > slowestLoopMs) slowestLoopMs = now - lastPassMs;
    lastPassMs = now;
  }

  uint32_t t0;
#if HAVE_BRAIN
  t0 = micros();
  brainLink.update(now);
  loopTime(LP_LINK, t0);
  if (speakDonePending) {
    speakDonePending = false;
    // With how it went, so the brain's log shows a voice that ran dry and the WiFi signal at the time.
    char done[128];
    snprintf(done, sizeof(done), "{\"type\":\"speak_done\",\"dry_ms\":%u,\"wait_ms\":%u,\"next_ms\":%u,\"rssi\":%d}",
             static_cast<unsigned>(speaker.lastUnderruns() * 5), static_cast<unsigned>(speaker.lastPrebufferMs()),
             static_cast<unsigned>(speaker.headStartMs()), static_cast<int>(WiFi.RSSI()));
    brainLink.sendJson(done);
    Serial.printf("speech: %u underruns, prebuffered %u bytes in %u ms, slowest loop %u ms, longest face gap %u ms, "
                  "wifi %d dBm\n", speaker.lastUnderruns(), speaker.lastPrebuffered(), speaker.lastPrebufferMs(),
                  static_cast<unsigned>(slowestLoopMs), static_cast<unsigned>(faceGapSpeechMs), WiFi.RSSI());
  }
  {
    // The microphone. Not while he talks: the brain doesn't listen then (he'd hear himself), and those 33 small
    // messages a second would only compete with his voice for the WiFi link.
    static uint8_t frame[Mic::FRAME_BYTES];
    t0 = micros();
    bool talking = speaker.speaking();
    while (mic.nextFrame(frame)) {
      if (brainLink.connected() && !talking) brainLink.sendBinary(0x01, frame, sizeof(frame));
    }
    loopTime(LP_MIC, t0);
  }
  t0 = micros();
  if (size_t n = camera.snapshotLen()) {
    if (brainLink.connected()) brainLink.sendBinary(0x03, camera.snapshotData(), n);
    Serial.printf("snapshot: %u bytes in %u ms\n", static_cast<unsigned>(n), static_cast<unsigned>(camera.snapshotMs()));
    camera.snapshotDone();
  }
  loopTime(LP_SNAPSHOT, t0);
  t0 = micros();
  static uint32_t lastJpgMs = 0;
  uint32_t frameGap = speaker.speaking() ? TALKING_FRAME_MS : face.asleep() ? ASLEEP_FRAME_MS : 0;
  if (brainLink.connected() && camera.streaming() && now - lastJpgMs >= frameGap) {
    static uint8_t* jpg = static_cast<uint8_t*>(ps_malloc(64 * 1024));
    size_t n = jpg ? camera.takeFrame(jpg, 64 * 1024) : 0;
    if (n > 0) {
      brainLink.sendBinary(0x02, jpg, n);
      lastJpgMs = now;
    }
  }
  loopTime(LP_JPEG, t0);
  if (brainLink.connected() && now >= nextTempMs) {
    nextTempMs = now + 10000;
    brainLink.sendJson(String("{\"type\":\"temp\",\"c\":") + String(temperatureRead(), 1) + "}");
  }
  if (brainLink.connected() && now >= nextStateMs) {
    nextStateMs = now + 5000;
    brainLink.sendState(panNeck.current(), emotionName(face.emotion()));
  }
#endif

  // Demo mode: wander through emotions so a fresh flash shows everything.
  if (demoMode && now >= nextDemoEmotionMs) {
    static const Emotion cycle[] = {
        Emotion::Neutral, Emotion::Happy,   Emotion::Thinking, Emotion::Curious, Emotion::Neutral,
        Emotion::Surprised, Emotion::Excited, Emotion::Love, Emotion::Skeptical, Emotion::Confused,
        Emotion::Sad,     Emotion::Angry,   Emotion::Sleepy,
    };
    demoEmotionIdx = (demoEmotionIdx + 1) % (sizeof(cycle) / sizeof(cycle[0]));
    Hold held(screenLock);
    face.setEmotion(cycle[demoEmotionIdx]);
    nextDemoEmotionMs = now + random(6000, 12000);
  }

  // BOOT button: enters assembly mode in the first few seconds after power-up, and leaves it any time.
  {
    static bool wasDown = false;
    bool down = digitalRead(PIN_BOOT_BUTTON) == LOW;
    if (down && !wasDown) {
      Hold screenHeld(screenLock);
      Hold neckHeld(neckLock);
      if (assemblyMode) setAssemblyMode(false);
      else if (now < ASSEMBLY_WINDOW_MS) setAssemblyMode(true);
    }
    wasDown = down;
  }
  // The screen and the servos: screenTask() and neckTask(). Roaming decides where the head goes next.
  roamUpdate(now);

  // Rest 1 ms each pass. Without it loop() spun ~150,000 times a second with nothing to do, keeping a core flat out
  // (heat); with it the core sleeps between passes. Nothing here needs it sooner: the voice arrives 10 times a
  // second, the mic 33, the camera 10.
  delay(1);
}
