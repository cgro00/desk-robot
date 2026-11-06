# Robot ↔ brain WebSocket protocol (v0)

The robot connects to `ws://<mac-ip>:8765`. The first message must be a
`hello` carrying the shared `ROBOT_TOKEN` (server/.env, firmware secrets.h);
the server drops anything else without replying, and allows one robot at a
time. Plain `ws://` on the home LAN, no TLS. Text frames are JSON. Binary
frames start with one type byte: `0x01` = mic audio, `0x02` = camera JPEG,
`0x03` = a sharp still. The rest of the frame is the payload.

## Robot → server

```json
{"type": "hello", "who": "desk-robot", "fw": "0.4.3", "token": "...",   // must be the first message; token = ROBOT_TOKEN
 "boot": "power on", "up_s": 3600, "rssi": -58, "tx_low": true,
 "wifi_drops": 1, "link_drops": 2, "drop_reason": 200, "drop_ago_s": 900, "down_s": 4.2,
 "stall_ms": 0, "face_gap_ms": 0}
                                  // 0.4.3+: how the robot has been, for the brain's "robot connected!" log line:
                                  // why it last restarted, seconds up, WiFi signal (dBm) and whether transmit power
                                  // is turned down, WiFi and brain-link drops since boot (the last WiFi drop's
                                  // reason code, 802.11 or ESP-IDF's 200+, how long ago, how long it was down;
                                  // only after a drop), the longest main-loop freeze since the last connection
                                  // ("stall_part" names where, when there was one) and the longest gap between
                                  // face frames, both in ms. Older firmware sends the first four.
{"type": "state", "pan": 12.5, "emotion": "neutral"}
{"type": "temp", "c": 52.0}       // chip temperature, sent every ~10 s
{"type": "speak_done", "dry_ms": 0, "wait_ms": 210, "next_ms": 200, "rssi": -58}
                                  // finished playing the last reply: how long the audio ran dry mid-reply,
                                  // how long it buffered before playing, the next reply's head start (ms),
                                  // and the WiFi signal (dBm). Firmware before 0.4.1 sends only the type.
```

Binary `0x01` frames: microphone audio, 16 kHz mono signed 16-bit PCM,
little-endian, 30 ms (480 samples) per frame.

Binary `0x02` frames: one camera JPEG per frame, at the size and `fps` of the
last `stream` message (the server asks for VGA, 640×480, at 10 fps; the
firmware's own default is QVGA, 320×240), sent continuously while streaming
is on. The server keeps the latest frame for the live-view page, the face
tracker, perception, and the language model.

Binary `0x03` frames: one sharp still (JPEG), the answer to a `snapshot` request. The stream pauses for it
(~0.1-0.4 s). The server's WebSocket accepts messages up to 1 MB.

## Server → robot

```json
{"type": "emotion", "name": "happy"}   // neutral happy sad angry surprised sleepy thinking curious skeptical excited love confused
{"type": "pan", "deg": -20}
{"type": "tilt", "deg": 15}
{"type": "speak_begin", "bytes": 0}      // binary TTS audio frames follow; bytes = total if known, 0 = streaming (robot starts after 200 ms of audio)
{"type": "speak_end"}             // no more audio; robot replies speak_done when played out
{"type": "volume", "level": 0.4}  // speaker volume 0.0-1.0
{"type": "mouth", "level": 0.08}  // voice playing on the computer: its loudness (RMS, 0-1) ~25x/s moves the mouth; -1 = done
{"type": "asleep", "on": true}    // sleep face for ~5 s, then the clock screen; false = wake up (the face). Asleep = no idle glances.
{"type": "glance", "on": false}   // allow/forbid the firmware's idle head glances; the server sends off on connect and leaves them off
{"type": "mic", "on": true}       // stream the microphone to the server
{"type": "stream", "on": true, "fps": 10, "size": "vga"}   // start/stop the camera stream, set rate and size (qvga | vga)
{"type": "snapshot", "size": "xga"}   // one sharp still back as a 0x03 frame: vga svga xga sxga uxga
{"type": "settings", "tz": "EST5EDT,M3.2.0,M11.1.0", "h24": false, "dmy": false, "wday": true,
 "wx": true, "lat": 40.71, "lon": -74.01, "unit": "f", "sun": false, "wxmin": 30,
 "bri": 80, "night": "dim", "n0": "23:00", "n1": "07:00", "nbri": 10}
                                  // the clock screen's settings (from server/settings.json); the robot keeps them in flash
{"type": "face", "save": true, "pack": {...}}   // a face pack (server/faces/*.json, minus its description); save = keep it
                                  // in flash (false while the dashboard previews an edit)
```

`settings` keys: `tz` POSIX time zone rule; `h24` 24-hour clock; `dmy` day
before month; `wday` show the weekday; `wx` show the weather (needs
`lat`/`lon`); `unit` `f` or `c`; `sun` sunrise/sunset instead of high/low;
`wxmin` weather refresh in minutes; `bri` brightness %; `night` `off`,
`dim` or `dark` between `n0` and `n1` (local `HH:MM`); `nbri` night
brightness %. Any subset is accepted; missing keys keep their value. The
robot fetches the time (SNTP) and the weather (Open-Meteo) itself.

On connect the server sends `mic on`, `stream on` at `CAMERA_FPS`, `glance
off`, `settings`, `face`, `volume`, and `asleep` (whether Rocky is asleep). The live page
is served by `server/brain/eyes.py` at http://localhost:8767/, and with
settings by the launcher at http://localhost:8766/.

Binary frames: type byte `0x01` then TTS audio for the speaker, same
PCM format as above, ~100 ms per frame. The server streams each reply as
the voice generates it (`bytes: 0`); the robot starts playing once it has a
head start buffered (400 ms after power-on, up to 600 ms after replies that
ran dry, settling to 200 ms after clean ones) and keeps a 32 s ring buffer in
PSRAM. If the robot connects again while its old connection is still open
(a reboot or replug), the new connection replaces the old one when it comes
from the same address or the old one has sent nothing for 8 s; otherwise
it is refused (1013). The server
waits for `speak_done` before it un-mutes the microphone.

Firmware side: `firmware/src/link.cpp` maps each server message onto the
same text commands the USB console uses (`emo`, `pan`, `tilt`), so both
paths behave identically. The robot sends `state` every 5 s.

Keep this file in sync with `firmware/src/link.cpp` and `server/brain/main.py`
whenever a message type is added.
