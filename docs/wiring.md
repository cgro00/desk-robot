# Wiring

This is the V2 robot: printed neck and head, power in at the base, the amp and
speaker in the head. Every connection is in the README's
[Build the body](../README.md#build-the-body) section. The hardware bundle on my
website adds the print files, the board files, a wiring diagram and an
illustrated assembly guide. V1's breadboard wiring for the off-the-shelf
pan-tilt bracket is in the
[v1.0 release](https://github.com/cgro00/desk-robot/releases/tag/v1.0).

The firmware's pin assignments are in `firmware/include/config.h`.

## First power-up

1. Flash the firmware and open the serial console (see the
   [README](../README.md#flash-the-firmware-and-test-over-usb)).
2. **Assembly mode** before fitting the pan gear and the tilt horn: press the
   XIAO's BOOT button within 5 seconds of power-up (or type `assembly on`).
   The screen shows ASSEMBLY and both servos go to centre and hold, so the
   head can be fitted pointing straight ahead and level. Press BOOT again
   (or `assembly off`) to leave.
3. Check the directions with small moves first:
   - `tilt 15` must look **up**. If it looks down, flip `TILT_INVERT` in
     `firmware/include/config.h`.
   - `pan 30` must turn to **Rocky's right**, which is your left when you face
     him. If not, flip `PAN_INVERT`.
4. If the head isn't straight or level at `center`, adjust `PAN_TRIM_DEG` /
   `TILT_TRIM_DEG` a few degrees at a time. For pan, a long way off is
   better fixed by re-meshing the gears one tooth (12 head degrees) with the
   servo centred.
5. With the brain running, check the camera the same way: `pan 15` must move
   the room **left** in the live picture, `tilt 15` **down**. If not, see
   `CAMERA_ROTATE` and `CAMERA_MIRROR` in `server/brain/config.py`.

If the OLED stays black, the usual suspects are swapped SDA/SCL or a loose
wire. If the face shows upside down, flip `OLED_FLIP`.
