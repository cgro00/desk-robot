# Changelog

Each version of the robot is a git tag and a GitHub Release. `main` is the
latest work. A video matches the tagged version it was made with.

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
