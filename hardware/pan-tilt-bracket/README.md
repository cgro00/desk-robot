# Pan-tilt bracket (3D-printed, DIY alternative)

STL files for a 3D-printed pan-tilt bracket for two TowerPro/SG90 9g micro
servos, used instead of buying the assembled Adafruit/SparkFun Pan-Tilt kit
that the main [README](../../README.md) lists as a purchasable part.

Print in PLA (or PETG for more durability) on any FDM printer. No specific
slicer settings required beyond the usual 9g-servo-bracket tolerances —
test-fit the servo horn slots before gluing/screwing anything.

## Files

- `pan_tilt_for_tower_pro_9g_servo_sg90_base.stl` — base plate
- `minimalized_pan_tilt_tower_pro_9g_face.stl` — tilt face bracket (mount the
  OLED + XIAO here, facing forward, per the main README)
- `minimalized_pan_tilt_tower_pro_9g_v20_critical_update.stl` and
  `minimalized_pan_tilt_tower_pro_9g_20_critical_update.stl` — two variants of
  the updated chassis piece; the original design page notes an earlier
  version was missing a servo spacer, so test-fit both and use whichever
  matches your servos

No firmware changes are needed to use this bracket instead of the assembled
kit: `firmware/include/config.h` calibrates pan/tilt range, trim, and
inversion in software (`PAN_TRIM_DEG`, `TILT_TRIM_DEG`, `TILT_INVERT`, etc.),
so any two-servo pan-tilt mechanism works as long as the OLED ends up facing
forward on the tilt platform.

## Attribution and license

Design: ["pan tilt for tower pro 9g servo SG90"](https://www.thingiverse.com/thing:887075)
by [cazantyl](https://www.thingiverse.com/cazantyl) on Thingiverse.

Licensed under [Creative Commons — Attribution — Non-Commercial (CC BY-NC)](https://creativecommons.org/licenses/by-nc/4.0/).
Not affiliated with or endorsed by the desk-robot project.
