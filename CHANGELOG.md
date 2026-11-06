# Changelog

Each version of the robot is a git tag and a GitHub Release. `main` is the
latest work. A video matches the tagged version it was made with.

## v2.1 - 2026-11-06

Software:

- Focus mode: "Rocky, help me focus for 25 minutes", or the Focus card on
  the dashboard. He watches you, and when you pick up your phone he turns to
  you and tells you off, in character, more dramatically if you keep at it.
  He approves when you put it down and tells you how it went at the end.
  During a session he answers nothing he hears, so you can talk to people;
  "hey Rocky, stop focus" ends it early. The phone check is OpenAI's
  Decisions API on the camera frame every 3 seconds (an optional
  `OPENAI_API_KEY`, about 3 cents a 25-minute session).
- The dashboard's hearing strip shows Rocky's own voice rising and falling
  while he talks (it was a flat red line).
- A question about a part you're working with ("which way does this LED
  go?") is answered from what he knows, without turning to look.

Firmware 0.4.3 (optional: nothing in the brain needs it):

- The robot tells the brain how it's been when it connects: why it last
  restarted, its WiFi signal, how often WiFi and the link dropped and why,
  and any freeze of its main loop. The brain prints it on the "robot
  connected!" line, so a dropped connection explains itself.

Updating from v2.0: pull, restart the brain, and add `OPENAI_API_KEY` to
`server/.env` if you want focus mode. No new Python packages. Reflash only
for the diagnostics.

## v2.0 - 2026-10-22

Hardware:

- New 3D-printed neck and head with MG90S servos: pan geared up 38:30
  (±95°), tilt ±85°, the 2.42" SSD1309 screen, the camera on its side in
  the head, the Dayton CE32A-8 speaker and the amp in the head, power in at
  the base. Replaces the off-the-shelf SG90 bracket.
- Custom head and base boards in place of the perfboards (the board files
  are in the hardware bundle; the connections are in the README).

Software:

- Clock and weather screen while Rocky is asleep, run by the robot itself.
- Dashboard (`server/launcher.py`, http://localhost:8766/): live view,
  settings saved in `server/settings.json`, face packs, server log, and a
  Start / Stop button for the brain.
- Face packs: twelve emotions, four built-in looks, your own made in the
  dashboard.
- Personalities: make new characters in the dashboard from a short
  description (name and wake word, Fish Audio voice, Fish's Drama 3 model
  for sighs, laughs and whispers). Rocky stays the default.
- Seeing: a picture with every question, sharp stills when he looks, and
  a local model that finds faces so he can follow you. A wave wakes him.
- Voice tuned for the CE32A-8 (bass cut at 300 Hz, peaks kept under the
  amp's clean limit).
- Language model: Claude Haiku 5.5 (was Haiku 4.5).
- Assembly mode (BOOT button) centres both servos for fitting the gears.
- Body language: small head moves with his emotion while he talks, and a
  thinking glance; still while asleep, tracking, or taking a picture.
- Computer control (off until you switch it on): he can search the web
  and open sites on any computer, and on a Mac open apps, play music,
  change the volume and run Shortcuts you allow.
  [docs/computer-control-windows.md](docs/computer-control-windows.md)
  has ideas for the rest on Windows.
- Robot link: a robot that restarts reconnects at once, the firmware joins
  the strongest access point and adapts its speaker buffer, and the
  connection token is checked more strictly.

## v1.0 - 2026-09-22

The robot in the videos: XIAO ESP32S3 Sense, OLED face, off-the-shelf
pan/tilt bracket with SG90 servos, camera, PDM mic, MAX98357A amp and
speaker; Python brain on your computer with wake word, speech-to-text,
a language model, and a cloned voice.

- Fixed: the voice was crackly on the robot's speaker since the release
  prep on 2026-09-17. Back to the original (2026-09-06) voice pipeline:
  the original loudness and gain cap, and a limiter that turns loud
  chunks down instead of clipping them. The bass-cut and presence
  sliders are still on the console, off by default.
