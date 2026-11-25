# Changelog

Each version of the robot is a git tag and a GitHub Release. `main` is the
latest work. A video matches the tagged version it was made with.

## v2.2 - 2026-11-24

Software:

- "Tell me when": "Rocky, tell me when the cat comes back", "tell me when
  someone comes in". He watches wherever his head points (or turns there
  first if you say where it is), and every 3 seconds asks OpenAI's
  Decisions API one yes-or-no question about the camera picture. It only
  counts after a check where it isn't so yet, so you walking out doesn't
  set off "someone comes in". He tells you in his own words and reminds
  you if you don't answer; something urgent he keeps saying, more panicked
  each time, until you answer. "Rocky, stop watching" stops it. Uses the
  same optional `OPENAI_API_KEY` as focus mode, about 1 cent per 10
  minutes.
- Drawing: "Rocky, draw a spaceship". The language model draws it as
  simple line art and it appears stroke by stroke on his face.
- Pictionary: "Rocky, let's play Pictionary". He draws something secret,
  you guess, he keeps score. He doesn't know the answer himself until you
  guess, so chatting mid-game can't give it away.
- Rocky is a problem-solving helper now: he dives into whatever you're
  working on, and no longer checks on your sleep, food or breaks unless you
  bring it up.
- "Look a little more down" moves from where his head already is, and
  "head down" said again goes further down.
- The dashboard's Restart button can't start two brains at once any more.
- A long ability call cut off mid-reply no longer breaks the next question.

Firmware 0.4.4 (needed for drawing and Pictionary):

- The face screen shows a picture from the brain in place of his eyes.
  `draw test` on the serial monitor shows a test picture.

Updating from v2.1: pull and restart the brain. No new Python packages.
Reflash for drawing and Pictionary; everything else works on 0.4.3.

## v2.1 - 2026-11-06

- New 3D-printed pan/tilt neck: geared 360° pan, unrestricted tilt, flat
  head plate. Replaces the off-the-shelf SG90-style bracket.
- New `room-dashboard/`: a standalone room dashboard (temperature,
  humidity and CO2 from Home Assistant, Homey or a JSON file, plus the
  weather from Open-Meteo and a clock). Does not need the robot.

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
