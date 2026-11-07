#pragma once

// desk-robot firmware for the V2 robot: printed pan/tilt neck (38/30 geared pan), 2.42" SSD1309 face, camera on
// its side in the head, Dayton CE32A-8 speaker. V1 (Adafruit bracket, 1.3" SH1106) is the v1.0 tag.
#define FW_VERSION "0.4.3"

// Turn the WiFi transmit power down to 11 dBm while the router is loud (less heat). Full power made no difference to
// the 2026-10-23 slow link (that was the power supply), so it stays on.
#define WIFI_SAVE_TX_POWER 1

// ─── Pin map (XIAO ESP32S3 Sense) ────────────────────────────────────────────
// Silkscreen label → GPIO number. The camera and PDM mic on the Sense
// expansion board use their own internal pins; its microSD slot shares D8-D10.
//
//   D0  = GPIO1   I2S BCLK  → MAX98357A BCLK      (speaker)
//   D1  = GPIO2   I2S LRC   → MAX98357A LRC       (speaker)
//   D2  = GPIO3   I2S DIN   → MAX98357A DIN       (speaker)
//   D3  = GPIO4   pan servo signal (orange wire)
//   D4  = GPIO5   I2C SDA   → OLED SDA
//   D5  = GPIO6   I2C SCL   → OLED SCL
//   D6  = GPIO43  spare: it's the ESP32's UART0 TX, which prints boot text, and a
//                  servo on it lunges to the end of its travel at every power-up
//   D7  = GPIO44  spare (UART0 RX: avoid for servos too)
//   D8  = GPIO7   tilt servo signal (orange wire). Shared with the Sense board's
//                  microSD slot (SCK): leave the slot empty

constexpr uint8_t PIN_SERVO_PAN = 4;   // D3
constexpr uint8_t PIN_SERVO_TILT = 7;  // D8 (was D6/GPIO43: boot text on it jerked the servo)

constexpr uint8_t PIN_I2S_BCLK = 1; // D0 → MAX98357A BCLK
constexpr uint8_t PIN_I2S_LRC = 2;  // D1 → MAX98357A LRC
constexpr uint8_t PIN_I2S_DIN = 3;  // D2 → MAX98357A DIN

// I2C uses the XIAO's default Wire pins (SDA=GPIO5/D4, SCL=GPIO6/D5). The OLED answers at 0x3C.

// ─── Display ─────────────────────────────────────────────────────────────────
// Which OLED: 1 = the 2.42" SSD1309 (HiLetgo) in the V2 head, 0 = the 1.3" SH1106 of V1. Same 128x64 pixels, so
// the face code is the same for both.
#define OLED_SSD1309_242 1
// Turn the picture 180 degrees: the V2 head holds the 2.42" screen upside down.
constexpr bool OLED_FLIP = true;

// Assembly mode: press the XIAO's BOOT button within ASSEMBLY_WINDOW_MS of
// power-up (not while resetting: holding BOOT during a reset starts the
// bootloader). Both servos go to centre and hold there, so gears and horns
// can be fitted straight; press BOOT again (or type `assembly off`) to leave.
constexpr uint8_t PIN_BOOT_BUTTON = 0;   // GPIO0, the BOOT button
constexpr uint32_t ASSEMBLY_WINDOW_MS = 5000;

// ─── Motion limits ───────────────────────────────────────────────────────────
// V2 printed pan/tilt (docs/cad). Angles are "head degrees": 0/0 = straight
// ahead and level, the pose the neck was assembled in (both servos centred).
// It is also where the head goes when Rocky falls asleep.
//
// Pan is geared UP: the 38T gear on the servo drives the 30T gear built into the
// turntable, so the head turns 38/30 = 1.267x the servo. On paper the ~180 deg servo
// gives the head about +-114 deg, but each servo's end stops sit a little differently:
// measured 2026-10-13 with the camera, this pan servo stops at about 2316 us going
// right (head +103) and still turns past 524 us going left (head -111). At +110 it
// pushed on its stop and ground. The limits stay ~8 deg short of the nearer stop, the
// same both ways. (To measure another servo: `raw pan <deg>` past the limits, see
// where the head stops following.) Re-mesh one tooth to move home a long way: each
// tooth = 12 head degrees.
constexpr float PAN_MIN_DEG = -95.0f;   // head turn limit, robot's left
constexpr float PAN_MAX_DEG = 95.0f;    // robot's right
constexpr float PAN_GEAR_RATIO = 38.0f / 30.0f;  // head degrees per servo degree
constexpr bool PAN_INVERT = false;      // flip if `pan 30` turns Rocky's LEFT (your right)
constexpr float PAN_MAX_SPEED = 150.0f; // head deg/sec ceiling (the servo itself does ~120 servo deg/sec)
constexpr float PAN_ACCEL = 450.0f;     // head deg/sec^2: how gently it speeds up and slows down
constexpr float PAN_TRIM_DEG = 0.0f;    // SERVO degrees; 1 here moves the head 1.27. Keep it small: to
                                        // move home a long way, re-mesh the gears with the servo
                                        // centred instead

// Tilt drives the head plate directly. The head clears 94 deg face-down and 99 deg
// face-up, so the servo's own +-90 is the limit; these stop 5 deg short of it.
constexpr float TILT_MIN_DEG = -85.0f;  // look-down limit
constexpr float TILT_MAX_DEG = 85.0f;   // look-up limit
// The head's weight sits ~40 mm above the tilt axis and ~3 mm in front of it, so it balances about 4 deg above
// level. Level or a little down it leans forward and the servo's gears hold it unpowered; tipped up past the balance
// point it falls back until it looks at the ceiling. So the tilt servo only goes quiet at rest between these angles,
// and holds anywhere else.
constexpr float TILT_RELAX_MIN_DEG = -15.0f;
constexpr float TILT_RELAX_MAX_DEG = 1.0f;
// While the tilt servo is quiet, drive it for TILT_REFRESH_BURST_MS every TILT_REFRESH_MS: a head that got knocked,
// or kicked by electrical noise on the signal wire, goes back to where it belongs. 0 = off. Off since 2026-10-13:
// the servo hums each time it switches on (a buzz every few seconds at rest), and the relax range above already
// keeps the head from falling. If it ever tips up on its own again, a 10k resistor from the tilt signal to ground
// on the head board is the quiet fix; 30000 here is the fallback.
constexpr uint32_t TILT_REFRESH_MS = 0;
constexpr uint32_t TILT_REFRESH_BURST_MS = 300;
constexpr float TILT_MAX_SPEED = 160.0f;
constexpr float TILT_ACCEL = 600.0f;
constexpr float TILT_TRIM_DEG = 0.0f;   // tweak so the head sits level at 0
constexpr bool TILT_INVERT = true;      // flip if `tilt 15` looks DOWN instead of up

// First time on new hardware: try small moves (`pan 20`, `tilt 15`) before big
// ones, and check each axis turns the right way.

// Stop driving a servo after it has been at rest this long, so it doesn't buzz
// or burn power. 0 = never (holds, but the tilt servo buzzes holding the head).
// Tilt only relaxes near level (TILT_RELAX_MIN/MAX_DEG above), where the head
// doesn't fall.
constexpr uint32_t SERVO_RELAX_MS = 1500;
constexpr uint32_t TILT_RELAX_MS = 1500;

// ─── Microphone ──────────────────────────────────────────────────────────────
// The Sense board's PDM mic is hard-wired to these GPIOs (no header pins).
constexpr uint8_t PIN_PDM_CLK = 42;
constexpr uint8_t PIN_PDM_DATA = 41;
// Software gain on the mic samples. The PDM mic is quiet; raise if the brain
// says "too quiet", lower if loud speech clips.
constexpr float MIC_GAIN = 6.0f;

// ─── Camera ──────────────────────────────────────────────────────────────────
// Flip + mirror = rotate the sensor's picture 180 degrees. The V2 head holds the
// camera on its side; the brain turns the frames upright (CAMERA_ROTATE and
// CAMERA_MIRROR in server/brain/config.py).
constexpr bool CAMERA_VFLIP = true;
constexpr bool CAMERA_HMIRROR = true;

// ─── Speaker ─────────────────────────────────────────────────────────────────
// 0.0-1.0 software volume for the MAX98357A (the brain sends its own on connect). The amp's GAIN pin: floating =
// 9 dB, GND = 12 dB, 100k to GND = 15 dB. With an 8 ohm speaker (the Dayton CE32A-8) leave it floating: at 9 dB a
// full-scale signal already asks for more than the amp's clean ~1.4 W, so more gain only adds distortion. A 4 ohm
// speaker gets twice the power at the same setting.
constexpr float SPEAKER_VOLUME = 0.7f;

// ─── Behavior ────────────────────────────────────────────────────────────────
constexpr uint32_t FRAME_INTERVAL_MS = 33; // ~30 FPS face animation
constexpr uint32_t SERVO_UPDATE_MS = 20;   // 50 Hz, the servos' own pulse rate
