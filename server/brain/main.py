"""desk-robot brain server.

Run with:  python -m brain.main   (from the server/ directory, venv active)

Three jobs:
  1. WebSocket server the robot connects to over WiFi.
  2. An interactive console so you can talk to the brain and puppet the
     robot from your keyboard right now.
  3. Listening: hears "hey Rocky" on the mic, sends what you say to
     the language model, and speaks the reply through the robot (or the Mac).

A reply is a pipeline, not a wait: the model's words stream in, each
finished sentence goes to the voice as soon as it exists, and the voice's
audio streams to the speaker as it's made. If you start talking again
before Rocky has begun speaking, the reply is dropped and he listens to
the rest of what you're saying, then answers once.

Console commands:
  ask <question>   send a question to the brain, print and speak the reply
  say <text>       speak text (robot speaker if connected, else the Mac)
  volume <0-1>     set the robot's speaker volume
  track on|off     face tracking (head follows you)
  listen           toggle listening on the microphone
  mic              3-second level meter to check the microphone
  emo <name>       push a face to the connected robot
  pan <deg>        push a head turn to the connected robot
  tilt <deg>       push a head nod to the connected robot
  gesture <name>   try an emotion's body language (face + head moves)
  status           show whether a robot is connected
  quit
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import queue
import secrets
import sys
import threading
import time

import websockets

from . import computer, config, faces, mouth, personality, settings
from .body import Body
from .dance import Dancer, length as dance_length
from .thinking import Interrupted, RobotBrain
from .ears import Ears, normalize, strip_wake_word
from .eyes import Eyes, rotate_jpeg
from .perception import Perception
from .tracker import Tracker

robot_socket: websockets.ServerConnection | None = None
robot_peer: str | None = None      # the connected robot's address, and when it last sent anything (it sends a
robot_heard_at = 0.0               # heartbeat every 5 s, and mic audio and camera frames far more often)
_greeting = 0                      # connections still saying hello (each may hold a message for up to 2 s)
MAX_GREETING = 8
MIN_TOKEN_LEN = 16
_bad_token = asyncio.Lock()        # wrong tokens wait out their second one at a time, however many come at once
brain: RobotBrain | None = None  # created in main() once the event loop exists
ears: Ears | None = None
# (text, when speech began, when it ended, when the transcript was ready)
heard: asyncio.Queue[tuple[str, float, float, float]] = asyncio.Queue()
speech_started = asyncio.Event()  # the human began a new turn (set by the ears)
awake_until = 0.0  # while time.time() < this, he's awake: no wake word needed
robot_speak_done = asyncio.Event()  # robot finished playing the last reply
eyes = Eyes()
head_moves: asyncio.Queue[tuple[float | None, float | None, bool]] = asyncio.Queue()
tracker: Tracker | None = None
perception: Perception | None = None
body: Body | None = None   # small head moves while he talks (body.py)
# The newest sharp still from the robot (rotated like the stream) and when it arrived; asked for as soon as you
# start talking, so it's ready by the time the question is.
snapshot: tuple[bytes, float] = (b"", 0.0)
snapshot_asked_at = 0.0
snapshot_arrived = asyncio.Event()

FRAME_BYTES = 3200  # 100 ms of 16 kHz s16le per audio frame to the robot
# How far ahead of the robot's playback its audio may run. The voice is made faster than it plays; sent all at once,
# seconds of it queue on the WiFi link in front of everything else for the robot, its heartbeat replies among them,
# and it drops the connection mid-reply (the face freezes, the camera goes away). Two seconds rides out WiFi hiccups.
ROBOT_AUDIO_AHEAD_S = 2.0

# What the live-view console shows and can change (see console_state / console_command).
current_emotion = "neutral"
speaker_volume = 0.7        # mirrors firmware SPEAKER_VOLUME until changed here
speaker_out = config.SPEAKER_OUT   # "robot" or "computer"; the live view can switch it
thinking = False            # a reply is being composed (before it's spoken)
roam_moving_until = 0.0     # the robot's own head moves (the "roam" idle screen): until then the camera is turning
TUNABLE = {                 # config knobs the console may change live: (min, max)
    "VAD_THRESHOLD": (0.1, 0.95),
    "TURN_THRESHOLD": (0.1, 0.95),
    "TURN_MAX_SILENCE": (0.5, 6.0),
    "AWAKE_SECONDS": (10.0, 600.0),
}
VOICE_TUNABLE = {           # speaker tuning the console may change live (mouth.Leveler reads these per reply)
    "TTS_LEVEL": (0.08, 0.40),
    "TTS_HIGHPASS_HZ": (0.0, 600.0),
    "TTS_PRESENCE_DB": (0.0, 12.0),
}


async def send_to_robot(payload: dict) -> bool:
    global current_emotion
    kind = payload.get("type")
    if kind == "emotion":
        current_emotion = payload.get("name", current_emotion)
        if body is not None:
            body.feel(current_emotion)
    # Where the head was last sent, whoever sent it (the dashboard's sliders, `look`, tracking, a dance, body
    # language), for the dashboard: its sliders show this. Asleep, the robot centers the head itself.
    if kind in ("pan", "tilt") and isinstance(payload.get("deg"), (int, float)):
        eyes.tracking_info[kind] = payload["deg"]
    elif kind == "asleep" and payload.get("on"):
        eyes.tracking_info.update(pan=0.0, tilt=0.0)
    if robot_socket is None:
        print("(no robot connected — command not sent)")
        return False
    await robot_socket.send(json.dumps(payload))
    return True


def pick_mic_source() -> None:
    """auto: the robot's mic while it's connected, the Mac's otherwise. A computer input that isn't plugged in
    counts as auto until it is (Ears has the system default open meanwhile)."""
    if ears is None:
        return
    mode = config.MIC_SOURCE
    if mode == "mac" and ears.device_missing:
        mode = "auto"
    if mode == "auto":
        source = "robot" if robot_socket is not None else "mac"
    else:
        source = mode
    if source != ears.source:
        ears.set_source(source)
        if source == "mac" and not ears.has_mac_mic:
            print(f"(no Mac mic: {ears.mac_error} — nothing is listening until the robot connects)")
        else:
            print("listening through the robot mic" if source == "robot" else f"listening through \"{ears.device_name}\"")


# Why the robot's WiFi last dropped (the reason code its WiFi stack reported: 802.11's, or ESP-IDF's 200+).
WIFI_DROP_REASONS = {
    2: "the router's login timed out", 3: "the router dropped it", 4: "the router dropped it for going quiet",
    8: "it left the network", 15: "WiFi password handshake timed out", 200: "lost the router's signal",
    201: "router not found", 202: "WiFi login failed", 203: "router refused it", 204: "WiFi handshake timed out",
    205: "connection failed",
}


def robot_health(hello: dict) -> str:
    """Firmware 0.4.3+ says in its hello how it's been: why it last restarted, its signal, and how often WiFi and
    the link have dropped since. Shown on the "robot connected!" line, so a drop explains itself."""
    num = lambda k: hello.get(k) if isinstance(hello.get(k), (int, float)) and not isinstance(hello.get(k), bool) else None
    if num("up_s") is None:
        return ""
    up = int(num("up_s"))
    parts = [f"up {up // 3600}h{up % 3600 // 60:02d}m" if up >= 3600 else f"up {up // 60}m{up % 60:02d}s"]
    if isinstance(hello.get("boot"), str):
        parts.append(f"last restart: {hello['boot'][:80]}")
    if num("rssi") is not None:
        parts.append(f"WiFi {int(num('rssi'))} dBm" + (", low transmit power" if hello.get("tx_low") is True else ""))
    drops, links = num("wifi_drops") or 0, num("link_drops") or 0
    if drops:
        code = int(num("drop_reason") or 0)
        why = WIFI_DROP_REASONS.get(code, "reason")
        down = f", down {num('down_s'):.0f} s" if num("down_s") else ""
        ago = f", {int(num('drop_ago_s'))} s ago" if num("drop_ago_s") is not None else ""
        parts.append(f"WiFi dropped {int(drops)}x since then (last: {why} {code}{down}{ago})")
    if links:
        parts.append(f"link dropped {int(links)}x")
    if (num("stall_ms") or 0) > 1000:   # 0.4.3+: its main loop froze (the brain heard nothing meanwhile)
        part = str(hello.get("stall_part", "?"))[:20]
        parts.append(f"its loop FROZE for {num('stall_ms') / 1000:.1f} s, in {part}")
    if (num("face_gap_ms") or 0) > 1000:
        parts.append(f"face froze for {num('face_gap_ms') / 1000:.1f} s")
    return "; " + "; ".join(parts)


async def handle_robot(websocket: websockets.ServerConnection) -> None:
    global robot_socket, roam_moving_until, robot_peer, robot_heard_at, _greeting
    peer = websocket.remote_address[0] if websocket.remote_address else "?"
    # Anyone on the WiFi can reach this port, so the first message must be a
    # hello carrying the shared token from server/.env. Anything else is
    # dropped without a reply.
    if _greeting >= MAX_GREETING:      # a flood of connections that never say hello
        await websocket.close(1013)
        return
    _greeting += 1
    try:
        first = await asyncio.wait_for(websocket.recv(), timeout=2)
        # the robot's hello is ~100 bytes: anything big isn't one
        hello = json.loads(first) if isinstance(first, str) and len(first) <= 4096 else {}
    except (asyncio.TimeoutError, json.JSONDecodeError, websockets.ConnectionClosed):
        hello = {}
    finally:
        _greeting -= 1
        first = None
    if not isinstance(hello, dict):
        hello = {}  # "[1]", "42", "null" are valid JSON but not a hello
    expected = os.environ.get("ROBOT_TOKEN", "")
    if len(expected) < MIN_TOKEN_LEN or expected == "change-me":   # the example's placeholder: everyone knows it
        print(f"(refusing robot: set ROBOT_TOKEN in server/.env and firmware/include/secrets.h to the same random "
              f"string of at least {MIN_TOKEN_LEN} characters, e.g. from "
              f"`python3 -c \"import secrets; print(secrets.token_hex(16))\"`)")
        await websocket.close(1008)
        return
    offered = str(hello.get("token", "")).encode()  # bytes: compare_digest rejects non-ASCII str
    if hello.get("type") != "hello" or not secrets.compare_digest(offered, expected.encode()):
        print(f"(refused a connection from {peer}: bad or missing token)")
        async with _bad_token:   # a wrong token costs a second, one at a time: no rapid guessing in parallel
            await asyncio.sleep(1)
        await websocket.close(1008)
        return
    if robot_socket is not None:
        # The robot back after a reboot or a replug, before its old connection is noticed as dead (that takes up to
        # ~40 s of missed pings). Refusing it left the robot retrying until then, so it takes over: if it comes from
        # the same address, or the old link has gone quiet (a live robot sends something every few seconds).
        # Anything else is a second robot (or someone else with the token) and is refused, as before.
        if peer != robot_peer and time.time() - robot_heard_at < 8:
            print(f"(refused a second robot from {peer}: one is already connected)")
            await websocket.close(1013)
            return
        old = robot_socket
        print(f"(robot reconnected from {peer}: dropping its old connection)")
        robot_socket = None
        asyncio.create_task(old.close(1012))
    robot_socket = websocket
    robot_peer, robot_heard_at = peer, time.time()
    print(f"robot connected! (fw {hello.get('fw', '?')}, {peer}{robot_health(hello)})")
    pick_mic_source()
    await update_robot_mic()
    await send_to_robot({"type": "stream", "on": True, "fps": config.CAMERA_FPS, "size": config.CAMERA_STREAM_SIZE})
    # No idle head glances: they fight deliberate looks. The eyes still move.
    await send_to_robot({"type": "glance", "on": False})
    # Its clock / weather / screen settings (it keeps a copy in flash), its volume, and whether he's awake: it
    # starts on the clock screen and stays there unless he is.
    await send_to_robot(settings.robot_payload(current_settings))
    await send_face_pack()
    await send_to_robot({"type": "volume", "level": speaker_volume})
    await send_to_robot({"type": "asleep", "on": time.time() >= awake_until})
    try:
        async for message in websocket:
            if robot_socket is websocket:
                robot_heard_at = time.time()
            if isinstance(message, bytes):
                kind = message[:1]
                if kind == b"\x01" and ears is not None and len(message) % 2 == 1:
                    ears.push_audio(message[1:])  # 1 type byte + whole 16-bit samples
                elif kind == b"\x02":
                    eyes.push_frame(message[1:])
                elif kind == b"\x03":
                    on_snapshot(message[1:])
                continue
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "state":
                continue  # heartbeat every 5 s; not worth the console space
            if event.get("type") == "speak_done":
                robot_speak_done.set()
                dry, rssi, nxt = (event.get(k) for k in ("dry_ms", "rssi", "next_ms"))
                if isinstance(dry, int) and dry > 0:   # firmware 0.4.1+ says how it went
                    num = lambda v: v if isinstance(v, int) else "?"   # numbers only into the log
                    print(f"  (robot: the voice ran dry for {dry} ms; WiFi {num(rssi)} dBm; next reply "
                          f"buffers {num(nxt)} ms first)")
                continue
            if event.get("type") == "head_moving":
                # roaming: the robot moves its head itself. Hold the wave watch off while it turns, and a moment
                # after (a stop message lost with the link can't leave it off for longer than 10 s)
                roam_moving_until = time.time() + (10.0 if event.get("on") else 0.5)
                continue
            if event.get("type") == "temp":
                try:
                    c = float(event.get("c"))
                    if math.isfinite(c):     # NaN would make /status invalid JSON
                        eyes.temperature = c
                except (TypeError, ValueError):
                    pass  # not a number; leave the last reading
                continue
            print(f"robot: {event}")
    except websockets.ConnectionClosed:
        pass
    finally:
        # Only clear the slot if it's still ours: when the robot reboots, the
        # new connection can arrive before the old one is noticed as dead.
        if robot_socket is websocket:
            robot_socket = None
            quiet = time.time() - robot_heard_at
            print("robot disconnected" + (f" (nothing from it for {quiet:.0f} s first)" if quiet >= 3 else ""))
            pick_mic_source()


async def handle_console_line(line: str) -> bool:
    """Returns False when the server should shut down."""
    line = line.strip()
    if not line:
        return True
    cmd, _, arg = line.partition(" ")
    cmd = cmd.lower()
    arg = arg.strip()

    if cmd == "quit":
        return False
    if cmd == "status":
        print("robot connected" if robot_socket else "no robot connected")
    elif cmd == "emo":
        if arg in config.EMOTIONS:
            await send_to_robot({"type": "emotion", "name": arg})
        else:
            print(f"emotions: {', '.join(config.EMOTIONS)}")
    elif cmd in ("pan", "tilt"):
        try:
            deg = float(arg)
            if not math.isfinite(deg):
                raise ValueError("not a number")
            deg = max(-180.0, min(180.0, deg))   # the robot clamps to its own limits; this keeps the JSON valid
            if body is not None:
                body.stop()
            await send_to_robot({"type": cmd, "deg": deg})
            if tracker is not None:
                tracker.note_pose(**{cmd: deg})
        except ValueError:
            print(f"usage: {cmd} <degrees>")
    elif cmd == "gesture":
        try:
            await _console_command("gesture", {"name": arg})
        except ValueError as e:
            print(e)
    elif cmd == "track":
        await _console_command("track", {"on": arg != "off"})   # same as the dashboard: wakes him if asleep
    elif cmd == "ask":
        if not arg:
            print("usage: ask <question>")
            return True
        await converse(arg)
    elif cmd == "listen":
        if ears is None:
            await start_listening()
        else:
            stop_listening()
    elif cmd == "mic":
        await mic_meter()
    elif cmd == "say":
        if arg:
            await say(arg)
        else:
            print("usage: say <text>")
    elif cmd == "volume":
        try:
            await set_volume(float(arg))
        except ValueError:
            print("usage: volume <0.0-1.0>")
    else:
        print("commands: ask <q> | say <text> | volume <0-1> | listen | mic | track on|off | emo <name> | gesture <name> | pan <deg> | tilt <deg> | status | quit")
    return True


# ── Rocky's abilities (called by the brain, from its worker thread) ──────────

async def look(args: dict) -> tuple:
    """Move the head, wait for it to get there, and grab a fresh frame.
    Only the axis that was asked for moves: "left"/"right" pan, "up"/
    "down"/"level" tilt, "center" both. see = false ("turn your head left", nothing asked about what's there): just
    the move, no picture, and the reply ends with his line (he described the room after every head turn)."""
    see = args.get("see", True)
    see = see.strip().lower() != "false" if isinstance(see, str) else bool(see)
    pan = tracker.pan if tracker else 0.0
    tilt = tracker.tilt if tracker else 0.0
    move_pan = move_tilt = False
    d = args.get("direction")
    try:
        amount = float(args["degrees"]) if args.get("degrees") is not None else None
    except (TypeError, ValueError):
        amount = None
    if d in ("left", "right"):
        deg = amount if amount is not None else 40.0
        pan, move_pan = (-deg if d == "left" else deg), True
    elif d == "up":
        tilt, move_tilt = (amount if amount is not None else 20.0), True
    elif d == "down":
        tilt, move_tilt = -(amount if amount is not None else 20.0), True  # a normal glance; ask for degrees to go to the limit
    elif d == "level":
        tilt, move_tilt = 0.0, True
    elif d == "center":
        pan, tilt, move_pan, move_tilt = 0.0, 0.0, True, True
    if not (move_pan or move_tilt):
        return ("Say where to look: left, right, up, down, level, or center.", None)
    pan = max(-config.TRACK_PAN_LIMIT, min(config.TRACK_PAN_LIMIT, pan))
    tilt = max(config.TRACK_TILT_MIN, min(config.TRACK_TILT_MAX, tilt))

    if tracker is not None and tracker.enabled:
        await set_tracking(False, announce=False)  # tracking would drag the head back
    await set_head_held(d != "center")
    async with (body.hold() if body is not None else _no_hold()):   # no body language until the picture is taken
        if move_pan:
            await send_to_robot({"type": "pan", "deg": pan})
        if move_tilt:
            await send_to_robot({"type": "tilt", "deg": tilt})
        if tracker is not None:
            tracker.note_pose(pan=pan if move_pan else None, tilt=tilt if move_tilt else None)
        if not see:
            return (_head_now(pan, tilt) + " Moved only, as asked: no picture. Say where your head went, "
                    "nothing about the room.", None, True)
        # Wait as long as the move really takes (the tracker runs a copy of the firmware's motion, body language
        # included), plus a moment for the command to arrive and the head to stop shaking, then take a sharp still
        # from the new angle.
        travel = tracker.arrival_in() if tracker is not None else 1.0
        await asyncio.sleep(travel + LOOK_SETTLE_S)
        jpeg = await best_picture(since=time.time() - 0.05, wait=1.5)
    return (_head_now(pan, tilt) + (" Fresh camera image attached." if jpeg else " No camera image available."), jpeg)


def _head_now(pan: float, tilt: float) -> str:
    pan_word = "left" if pan < -5 else "right" if pan > 5 else "center"
    if tilt <= config.TRACK_TILT_MIN + 0.5:
        tilt_word = "down, as far as it goes"
    elif tilt >= config.TRACK_TILT_MAX - 0.5:
        tilt_word = "up, as far as it goes"
    else:
        tilt_word = "down" if tilt < -5 else "up" if tilt > 5 else "level"
    return f"Head is now at pan {pan:.0f} deg ({pan_word}), tilt {tilt:.0f} deg ({tilt_word})."


LOOK_SETTLE_S = 0.3  # after the head arrives: WiFi, servo lag, and the head settling before the picture


@contextlib.asynccontextmanager
async def _no_hold():
    yield
head_held = False  # he was told to look somewhere and is holding that pose


async def set_head_held(held: bool) -> None:
    """Remember that a deliberate look is in effect (shown on the console).
    Idle glances are always off, so nothing else needs to move."""
    global head_held
    head_held = held


async def set_tracking(on: bool, announce: bool = True) -> tuple[str, bytes | None]:
    if tracker is None:
        return ("no tracker running", None)
    tracker.enabled = on
    if on and body is not None:
        body.stop()   # the tracker owns the head now
    if not on and tracker.tracking:
        tracker.tracking = False
        eyes.tracking_info["tracking"] = False
    if announce:
        print(f"tracking {'on' if on else 'off'}")
    return ("now following the human's face" if on else "stopped following", None)


async def fall_asleep() -> None:
    """Everything that goes with sleep: face tracking off and the head back to center (the robot centers it on
    `asleep` and its servos go quiet once they settle there), then eyes shut and the clock screen."""
    if dancer is not None:
        dancer.stop()
    if body is not None:
        body.stop()
    if tracker is not None and tracker.enabled:
        await set_tracking(False)
    await set_head_held(False)
    await send_to_robot({"type": "pan", "deg": 0})
    await send_to_robot({"type": "tilt", "deg": 0})
    if tracker is not None:
        tracker.note_pose(pan=0, tilt=0)
    await send_to_robot({"type": "asleep", "on": True})


async def wake_up() -> None:
    """Eyes open (the face replaces the clock screen)."""
    await send_to_robot({"type": "asleep", "on": False})


def _sync(coro_fn):
    """Wrap an async ability so the brain's worker thread can call it."""
    def run(args: dict):
        return asyncio.run_coroutine_threadsafe(coro_fn(args), main_loop).result(timeout=15)
    return run


dancer: Dancer | None = None  # created in main()


def start_dance(name: str | None = None) -> str | None:
    """One of his four routines, at random (or by name); it starts once his line is said. The routine's name, or
    None with no robot to dance."""
    global awake_until
    if dancer is None or robot_socket is None:
        return None
    picked = dancer.start(name)
    awake_until = max(awake_until, time.time() + dance_length(picked) + config.AWAKE_SECONDS)   # no dozing mid-dance
    return picked


async def dance() -> tuple:
    """The `dance` ability. Done: nothing more for the model to say about it."""
    picked = start_dance()
    if picked is None:
        return ("No body connected: can't dance right now.", None)
    return (f"Dancing now ({picked}, about {dance_length(picked):.0f} s). Your line before it was enough: say nothing "
            "more, and don't describe the dance.", None, True)


async def _before_dancing() -> None:
    """The dance has the head: body language stops, face tracking goes off, no held pose."""
    if body is not None:
        body.stop()
    if tracker is not None and tracker.enabled:
        await set_tracking(False, announce=False)
    await set_head_held(False)


main_loop: asyncio.AbstractEventLoop | None = None
ABILITIES = {
    "look": _sync(look),
    "track_face": _sync(lambda args: set_tracking(bool(args.get("on", True)))),
    "dance": _sync(lambda args: dance()),
    # the computer abilities (computer.py) are plain functions: no event loop, nothing sent to the robot
    **{name: (lambda fn: lambda args: (fn(args), None))(fn) for name, fn in computer.ACTIONS.items()},
}


# ── Settings (server/settings.json, edited in the dashboard) ─────────────────

current_settings: dict = settings.defaults()
_TUNE_SECTION = {"VAD_THRESHOLD": "listening", "TURN_THRESHOLD": "listening", "TURN_MAX_SILENCE": "listening",
                 "TTS_LEVEL": "voice", "TTS_HIGHPASS_HZ": "voice", "TTS_PRESENCE_DB": "voice"}


def apply_settings(s: dict) -> None:
    """Make the brain match the settings file (everything except your name, which needs a restart). The mic
    choice is taken here; reload_settings() switches the mic itself."""
    global current_settings, speaker_volume, speaker_out
    current_settings = s
    r = s["rocky"]
    config.AWAKE_SECONDS = r["awake_seconds"]
    speaker_volume = r["volume"]
    speaker_out = r["speaker_out"]
    config.MIC_SOURCE = {"computer": "mac"}.get(r["mic"], r["mic"])
    config.MIC_DEVICE = r["mic_device"] or os.environ.get("MIC_DEVICE") or None
    config.CAMERA_ROTATE = r["camera_rotate"]
    config.BODY_LANGUAGE = r["body_language"]
    v = s["vision"]
    config.VISION_EVERY_QUESTION = v["every_question"]
    config.SNAPSHOT_SIZE = "" if v["snapshot"] == "off" else v["snapshot"]
    config.PERCEPTION = v["perception"]
    config.GESTURE_REACTIONS = v["gestures"]
    computer.controller.configure(s["computer"])
    config.CAMERA_STREAM_SIZE = v["stream"]
    if perception is not None:
        perception.enabled = config.PERCEPTION
    for sec in ("listening", "voice"):
        for key, value in s[sec].items():
            if value is not None:
                setattr(config, key, value)
    if ears is not None:
        ears.apply_config()


def persist(section: str, key: str, value) -> None:
    """A change made on the dashboard's live controls: keep it for next time too."""
    global current_settings
    try:
        current_settings = settings.update({section: {key: value}})
    except (OSError, ValueError) as e:
        print(f"(couldn't save {section}.{key}: {e})")


async def reload_settings() -> None:
    """The launcher saved the settings file: apply it and send the robot its part."""
    apply_settings(settings.load())
    await switch_mic()
    await send_to_robot(settings.robot_payload(current_settings))
    await send_face_pack()
    await send_to_robot({"type": "volume", "level": speaker_volume})
    await send_to_robot({"type": "stream", "on": True, "fps": config.CAMERA_FPS, "size": config.CAMERA_STREAM_SIZE})
    print("settings: reloaded from settings.json")


async def switch_mic() -> None:
    """Open the computer input the settings name (if it changed), pick robot or computer, and tell the robot
    whether to stream its mic."""
    if ears is not None and ears.device != config.MIC_DEVICE:
        name = await asyncio.get_running_loop().run_in_executor(None, ears.set_device, config.MIC_DEVICE)
        print(f"computer mic: \"{name}\"" + (f" (\"{ears.device_missing}\" isn't plugged in: using the default "
                                              f"until it is)" if ears.device_missing else ""))
    pick_mic_source()
    await update_robot_mic()


async def update_robot_mic() -> None:
    """The robot streams its mic only while it's the one being listened to (it's WiFi and heat for nothing
    otherwise). Listening off: whatever the setting allows, so `listen` finds it running."""
    if robot_socket is None:
        return
    on = ears.source == "robot" if ears is not None else config.MIC_SOURCE in ("auto", "robot")
    await send_to_robot({"type": "mic", "on": on})


async def mic_watch() -> None:
    """A chosen computer input that wasn't plugged in: look for it again every 10 s (between turns: reopening
    drops a moment of sound), and switch to it once it's there."""
    while True:
        await asyncio.sleep(10)
        if ears is None or not ears.device_missing or ears.hearing or ears.transcribing:
            continue
        await asyncio.get_running_loop().run_in_executor(None, ears.set_device, ears.device)
        if not ears.device_missing:
            print(f"computer mic: \"{ears.device_name}\" is plugged in now")
            pick_mic_source()
            await update_robot_mic()


async def send_face_pack() -> None:
    """The face pack picked in the dashboard (Rocky's own if it's missing); the robot keeps it in flash."""
    _, pack = faces.load_or_default(current_settings["face"]["pack"])
    await send_to_robot(faces.robot_message(pack))


def wants_camera(question: str) -> bool:
    """Does the question sound like it's about what Rocky can see? (config.CAMERA_WORDS, as whole words)"""
    q = " " + normalize(question) + " "
    return any(f" {w} " in q for w in config.CAMERA_WORDS)


# ── Speaking ─────────────────────────────────────────────────────────────────

class Timeline:
    """Stage times of one reply, measured from the end of the human's speech."""

    ORDER = ("heard", "picture", "face", "first sentence", "first audio", "speaking", "done")

    def __init__(self, t0: float) -> None:
        self.t0 = t0
        self.marks: dict[str, float] = {}

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, time.time())

    def report(self) -> None:
        parts = [f"{n} {self.marks[n] - self.t0:.2f}s" for n in self.ORDER if n in self.marks]
        if parts:
            print("  timing (after your last word): " + " · ".join(parts))


class SpeechTrack:
    """Where his voice is in the reply right now, so the dashboard can show his words as he says them.

    Positions count bytes of the reply's audio (16 kHz 16-bit: 32000 a second). The voice thread records where
    each sentence starts and ends; the playback side pins the audible position to the clock and says how much the
    speaker has been given, so a speaker that ran dry (the voice falling behind) doesn't count as still talking."""

    RATE = 32000

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.lines: list[list] = []            # [text, start byte, end byte or None while it's being voiced]
        self.made = 0                          # bytes of audio voiced so far
        self.given = 0                         # bytes handed to the speaker so far
        self.anchor = (0, float("inf"))        # (byte, time.time()) the audible position was last pinned at

    # the voice thread
    def begin(self, text: str) -> None:
        with self.lock:
            self.lines.append([text, self.made, None])

    def voiced(self, n: int) -> None:
        with self.lock:
            self.made += n

    def end(self) -> None:
        with self.lock:
            if self.lines and self.lines[-1][2] is None:
                self.lines[-1][2] = self.made

    # the playback side
    def _pos(self, now: float) -> float:
        b, t = self.anchor
        return min(self.given, b + max(0.0, now - t) * self.RATE)

    def play_from(self, byte: int, at: float, given: int) -> None:
        """Audio from `byte` becomes audible at time `at`; the speaker has `given` bytes in all."""
        with self.lock:
            self.anchor = (byte, at)
            self.given = given

    def gave(self, given: int) -> None:
        """The speaker now has `given` bytes. If it had run dry it picks up again from here, now."""
        with self.lock:
            now = time.time()
            if self.given and self._pos(now) >= self.given:
                self.anchor = (self.given, now)
            self.given = given

    def snapshot(self) -> dict:
        with self.lock:
            r = self.RATE
            return {"lines": [[t, s / r, None if e is None else e / r] for t, s, e in self.lines],
                    "pos": round(self._pos(time.time()) / r, 3), "given": round(self.given / r, 3)}


speech_now: SpeechTrack | None = None   # the reply being spoken (for the dashboard), None when he's quiet


class SpokenReply:
    """One reply in flight, as a three-stage pipeline:
    brain thread (model → sentences) → voice thread (Fish → PCM) → the speaker.

    Each stage hands off through a queue, so the first sentence is being
    voiced while the model writes the second, and audio plays while the
    voice is still generating."""

    BREAK = b""  # marks a sentence boundary in the audio queue

    def __init__(self, loop: asyncio.AbstractEventLoop, timeline: Timeline) -> None:
        self.loop = loop
        self.tl = timeline
        self.leveler = mouth.Leveler()  # one per reply: steady loudness across its sentences
        self.sentences: queue.Queue[str | None] = queue.Queue()
        self.audio: queue.Queue[bytes | None] = queue.Queue()
        self.cancel = threading.Event()
        self.spoken: list[str] = []
        self.pcm: list[bytes] = []
        self.track = SpeechTrack()

    @property
    def text(self) -> str:
        return " ".join(self.spoken)

    def think_and_speak(self, question: str, jpeg: bytes | None, camera_wanted: bool = False, notes: str = "",
                        passive: bool = False) -> None:
        threading.Thread(target=self._think, args=(question, jpeg, camera_wanted, notes, passive), daemon=True).start()
        threading.Thread(target=self._voice, daemon=True).start()

    def speak_fixed(self, text: str) -> None:
        """A canned line: no brain involved."""
        self.spoken.append(text)
        self.sentences.put(text)
        self.sentences.put(None)
        threading.Thread(target=self._voice, daemon=True).start()

    def _on_emotion(self, name: str) -> None:
        # Called from the brain thread as soon as the reply's emotion is known.
        if self.cancel.is_set():
            return
        self.tl.mark("face")
        asyncio.run_coroutine_threadsafe(send_to_robot({"type": "emotion", "name": name}), self.loop)

    def _think(self, question: str, jpeg: bytes | None, camera_wanted: bool, notes: str, passive: bool) -> None:
        gen = brain.reply(question, jpeg, on_emotion=self._on_emotion, cancelled=self.cancel,
                          camera_wanted=camera_wanted, notes=notes, passive=passive)
        try:
            for sentence in gen:
                if self.cancel.is_set():
                    break
                if not self.spoken:
                    self.tl.mark("first sentence")
                self.spoken.append(mouth.without_cues(sentence))   # the page shows his words, not voice directions
                print(f"{config.ROBOT_NAME} [{brain.emotion}]: {sentence}")
                self.sentences.put(sentence)
        except Interrupted:
            pass
        except Exception as e:  # never let a brain hiccup kill the server
            print(f"(brain error: {e})")
        finally:
            gen.close()  # drops the question from his memory if we bailed early
            self.sentences.put(None)

    def _voice(self) -> None:
        try:
            while (sentence := self.sentences.get()) is not None:
                if self.cancel.is_set():
                    continue
                self.track.begin(mouth.without_cues(sentence))
                for chunk in mouth.stream(sentence, self.leveler):
                    if self.cancel.is_set():
                        break
                    if not self.pcm:
                        self.tl.mark("first audio")
                    self.pcm.append(chunk)
                    self.track.voiced(len(chunk))
                    self.audio.put(chunk)
                self.track.end()
                self.audio.put(self.BREAK)
        except Exception as e:
            print(f"(voice error: {e})")
        finally:
            self.audio.put(None)

    async def _next(self) -> bytes | None:
        return await self.loop.run_in_executor(None, self.audio.get)

    async def play(self, interruptible: bool = True) -> bool:
        """Send the audio to the speaker as it arrives. Until the first audio
        is ready the human can cancel the whole reply by talking again;
        returns False in that case. Once Rocky is speaking the mic is muted
        (his voice would trigger it) and he finishes what he's saying."""
        get = asyncio.ensure_future(self._next())
        while not get.done():
            if interruptible and (speech_started.is_set() or (ears is not None and ears.talking)):
                self._abort()
                await get
                return False
            await asyncio.wait({get}, timeout=0.05)
        first = get.result()
        if first is None:
            return True  # nothing to say
        global speech_now
        if ears is not None:
            ears.muted.set()
        speech_now = self.track
        if body is not None:
            body.speaking(current_emotion)   # the reply's emotion is on his face by now
        try:
            if robot_socket is not None and speaker_out == "robot":
                await self._play_robot(first)
            else:
                await self._play_mac(first)
        finally:
            if self.text:
                eyes.last_said = self.text   # before the live track goes, so the page never flicks back
                computer.controller.said(self.text)   # a shortcut's "Run X?" only counts if X was said
            speech_now = None
            if body is not None:
                body.quiet()
            if ears is not None:
                ears.muted.clear()
        self.tl.mark("done")
        return True

    def _abort(self) -> None:
        self.cancel.set()
        brain.abandon()
        self.sentences.put(None)  # wake the voice thread so it can exit
        self.audio.put(None)

    async def _play_robot(self, first: bytes) -> None:
        """Stream PCM to the robot (docs/protocol.md) and wait until it has played."""
        robot_speak_done.clear()
        # bytes 0 = length unknown: the robot starts after 200 ms of buffer.
        await send_to_robot({"type": "speak_begin", "bytes": 0})
        self.tl.mark("speaking")
        self.track.play_from(0, time.time() + self.ROBOT_START_LAG, 0)
        buf = bytearray()
        total = 0
        started = time.monotonic()
        chunk: bytes | None = first
        while chunk is not None:
            buf += chunk
            while len(buf) >= FRAME_BYTES:
                ahead = total / SpeechTrack.RATE - (time.monotonic() - started)
                if ahead > ROBOT_AUDIO_AHEAD_S:
                    await asyncio.sleep(ahead - ROBOT_AUDIO_AHEAD_S)
                if robot_socket is None:
                    return
                await robot_socket.send(b"\x01" + bytes(buf[:FRAME_BYTES]))
                del buf[:FRAME_BYTES]
                total += FRAME_BYTES
                self.track.gave(total)
            chunk = await self._next()
        if buf and robot_socket is not None:
            await robot_socket.send(b"\x01" + bytes(buf))
            total += len(buf)
            self.track.gave(total)
        await send_to_robot({"type": "speak_end"})
        try:
            await asyncio.wait_for(robot_speak_done.wait(), timeout=total / 32000 + 5)
        except asyncio.TimeoutError:
            print("(robot never said it finished speaking)")

    async def _play_mac(self, first: bytes) -> None:
        """No robot, or the voice set to the computer: play each sentence on the Mac as soon as it's complete."""
        self.tl.mark("speaking")
        buf = bytearray()
        played = 0
        chunk: bytes | None = first
        try:
            while chunk is not None:
                if chunk == self.BREAK:
                    if buf:
                        await self._play_mac_line(bytes(buf), played)
                        played += len(buf)
                        buf = bytearray()
                else:
                    buf += chunk
                chunk = await self._next()
            if buf:
                await self._play_mac_line(bytes(buf), played)
        finally:
            await self._send_mouth(-1)   # done talking: mouth shut

    async def _play_mac_line(self, pcm: bytes, at_byte: int) -> None:
        """Play one sentence on the Mac while the robot's mouth moves along. The robot only moves its mouth by
        itself for audio its own speaker plays, so here we send it the loudness, in step with the playback."""
        self.track.play_from(at_byte, time.time() + self.MAC_PLAY_LAG, at_byte + len(pcm))
        moving = asyncio.ensure_future(self._mouth_along(pcm))
        try:
            await self.loop.run_in_executor(None, mouth.play, pcm)
        finally:
            moving.cancel()

    MOUTH_STEP = 0.04      # s per loudness message (25 a second)
    MAC_PLAY_LAG = 0.08    # s before afplay is audible
    ROBOT_START_LAG = 0.2  # s: the robot starts playing once it holds 200 ms of audio

    async def _mouth_along(self, pcm: bytes) -> None:
        import numpy as np
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        n = int(mouth.SAMPLE_RATE * self.MOUTH_STEP)
        t0 = self.loop.time() + self.MAC_PLAY_LAG
        for k, i in enumerate(range(0, len(a), n)):
            level = float(np.sqrt(np.mean(a[i:i + n] ** 2)))   # same measure as the robot's speaker.level()
            wait = t0 + k * self.MOUTH_STEP - self.loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            await self._send_mouth(level)

    @staticmethod
    async def _send_mouth(level: float) -> None:
        if robot_socket is None:
            return
        try:
            await robot_socket.send(json.dumps({"type": "mouth", "level": round(level, 3)}))
        except Exception:
            pass   # the robot dropped off; speech carries on


# ── Seeing ───────────────────────────────────────────────────────────────────

async def request_snapshot(force: bool = False) -> None:
    """Ask the robot for one sharp still (SNAPSHOT_SIZE). One request in flight at a time unless forced."""
    global snapshot_asked_at
    if robot_socket is None or not config.SNAPSHOT_SIZE or not config.SEND_CAMERA_TO_BRAIN:
        return
    if not force and time.time() - snapshot_asked_at < 1.0:
        return
    snapshot_asked_at = time.time()
    snapshot_arrived.clear()
    await send_to_robot({"type": "snapshot", "size": config.SNAPSHOT_SIZE})


def on_snapshot(jpeg: bytes) -> None:
    global snapshot
    snapshot = (rotate_jpeg(jpeg, config.CAMERA_ROTATE, config.CAMERA_MIRROR), time.time())
    snapshot_arrived.set()


async def best_picture(since: float | None = None, wait: float = 1.0) -> bytes | None:
    """The sharpest picture taken after `since` (default: the last 4 s): a snapshot, waiting up to `wait` for one
    that's on its way (asking for one if none is), else the newest stream frame."""
    since = time.time() - 4.0 if since is None else since
    if config.SNAPSHOT_SIZE and robot_socket is not None:
        if snapshot[1] < since:
            if snapshot_asked_at < since:
                await request_snapshot(force=True)
            end = time.time() + wait
            while snapshot[1] < since and time.time() < end:
                try:
                    await asyncio.wait_for(snapshot_arrived.wait(), max(0.01, end - time.time()))
                except asyncio.TimeoutError:
                    break
                snapshot_arrived.clear()
        if snapshot[1] >= since:
            return snapshot[0]
    return eyes.latest()


def speech_began() -> None:
    """You started talking (the ears' callback, on the event loop)."""
    speech_started.set()


def turn_ended() -> None:
    """You stopped talking (the ears' callback, on the event loop): take a sharp still now, while your words are
    turned into text, so a question about seeing has it at once, and it shows what you held up while asking."""
    asyncio.ensure_future(request_snapshot(force=True))


def speaking_now() -> bool:
    """His voice is playing (the ears are muted for it)."""
    return ears is not None and ears.muted.is_set()


async def gesture_seen(name: str) -> None:
    """A wave from perception, while he's asleep, wakes him (the only gesture that does anything)."""
    global awake_until
    if not config.GESTURE_REACTIONS or name != "wave" or robot_socket is None or thinking or speaking_now():
        return
    if time.time() < awake_until:
        return   # awake already: nothing to do
    print(f"(saw a wave — {config.ROBOT_NAME} wakes up)")
    awake_until = time.time() + config.AWAKE_SECONDS
    await wake_up()
    await send_to_robot({"type": "emotion", "name": "surprised"})
    await say(personality.LINES["wake"])
    awake_until = time.time() + config.AWAKE_SECONDS


async def converse(question: str, ended_at: float | None = None, heard_at: float | None = None) -> bool:
    """Ask the brain, show the face, and speak the answer as it forms.
    Returns False if the human started talking again before Rocky spoke
    (the reply was dropped and the question is still open)."""
    global awake_until
    computer.controller.new_turn(question)
    loop = asyncio.get_running_loop()
    tl = Timeline(ended_at or time.time())
    if heard_at is not None:
        tl.marks["heard"] = heard_at
    speech_started.clear()
    await send_to_robot({"type": "emotion", "name": "thinking"})
    camera_wanted = config.SEND_CAMERA_TO_BRAIN and wants_camera(question)
    passive = False
    if camera_wanted:
        jpeg = await best_picture(since=ended_at)   # the still taken as you stopped talking
    elif config.SEND_CAMERA_TO_BRAIN and config.VISION_EVERY_QUESTION:
        jpeg = eyes.latest()   # for context only: the live stream frame, no waiting, and lighter for the model
        passive = jpeg is not None
    else:
        jpeg = None
    if jpeg is not None:
        tl.mark("picture")
    if camera_wanted and jpeg is None:
        print("  (camera has no fresh frame — telling him he can't see right now)")
    # The face detector's note, only next to a picture (a question about seeing, or one along for context): with
    # other questions it only invited him to talk about how you look in answers that had nothing to do with it.
    notes = perception.describe() \
        if perception is not None and config.PERCEPTION and (camera_wanted or jpeg is not None) else ""
    if notes:
        print("  " + notes)
    reply = SpokenReply(loop, tl)
    reply.think_and_speak(question, jpeg, camera_wanted, notes, passive)
    if body is not None:
        body.ponder()   # after the picture: a glance up while he thinks
    global thinking
    thinking = True
    try:
        finished = await reply.play()
    except Exception as e:  # a speaker hiccup shouldn't kill the server
        print(f"(could not speak: {e})")
        finished = True
    finally:
        thinking = False
        if body is not None:
            body.quiet()
    if not finished:
        print("  (you kept talking — Rocky will hear the rest and answer once)")
        await send_to_robot({"type": "emotion", "name": "neutral"})
        return False
    if reply.text:
        eyes.last_said = reply.text
        if config.DEBUG_TTS_CHECK and reply.pcm:
            loop.run_in_executor(None, check_tts, reply.text, b"".join(reply.pcm))
    awake_until = time.time() + config.AWAKE_SECONDS
    tl.report()
    return True


async def say(text: str) -> bytes:
    """Speak a fixed line: through the robot's speaker when it's connected,
    else the Mac. Ears are muted meanwhile so Rocky doesn't hear himself."""
    reply = SpokenReply(asyncio.get_running_loop(), Timeline(time.time()))
    reply.speak_fixed(text)
    try:
        await reply.play(interruptible=False)
    except Exception as e:
        print(f"(could not speak: {e})")
    return b"".join(reply.pcm)


_tts_checker = None


def check_tts(text: str, pcm: bytes) -> None:
    """Transcribe what the voice model produced and compare it with the text
    it was given. Extra words = the model hallucinated (a laugh, "hello", a
    sound). Runs in a worker thread; prints only when something's off."""
    global _tts_checker
    from .ears import Transcriber
    if _tts_checker is None:
        _tts_checker = Transcriber("base.en")
    import numpy as np
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    heard_text = _tts_checker.transcribe(audio)
    words = lambda t: set(re.findall(r"[a-z']+", t.lower()))
    said, got = words(mouth.clean_for_tts(text)), words(heard_text)
    extra = got - said
    missing = said - got
    # Transcription is imperfect: only shout when several words are foreign.
    if len(extra) >= 3 or (len(extra) >= 2 and len(extra) >= len(got) / 3):
        print(f"  !! TTS CHECK: audio contains words not in the text: {sorted(extra)}")
        print(f"     text : {text}\n     heard: {heard_text}")
    elif len(missing) >= max(3, len(said) // 2):
        print(f"  !! TTS CHECK: audio is missing much of the text. heard: {heard_text}")


async def set_volume(level: float) -> None:
    global speaker_volume
    speaker_volume = max(0.0, min(1.0, level))
    await send_to_robot({"type": "volume", "level": speaker_volume})
    persist("rocky", "volume", speaker_volume)


# ── The live-view console (the brain's own page on :8767; the launcher's on :8766) 

def console_state() -> dict:
    """Extra fields for /status: everything the page shows beyond the camera."""
    now = time.time()
    return {
        "robot": robot_socket is not None,
        "listening": ears is not None,
        "mic": ears.source if ears is not None else None,
        "mic_device": ears.device_name if ears is not None and ears.has_mac_mic else None,
        "mic_missing": ears.device_missing if ears is not None else None,
        "level": ears.level if ears is not None else 0.0,
        "speech_prob": ears.speech_prob if ears is not None else 0.0,
        "hearing": ears.hearing if ears is not None else False,
        # the dashboard strip's bar: the whole last quarter second, not just its newest 32 ms
        **dict(zip(("level_recent", "speech_recent", "voice_recent"), ears.recent() if ears is not None else (0.0, 0.0, False))),
        "speaking": ears.muted.is_set() if ears is not None else False,
        "thinking": thinking,
        "awake": now < awake_until,
        "awake_for": max(0.0, awake_until - now),
        "emotion": current_emotion,
        "emotions": config.EMOTIONS,
        "notices": perception.summary() if perception is not None and config.PERCEPTION else None,
        "voice": {k: getattr(config, k) for k in VOICE_TUNABLE},
        "head_held": head_held,
        "tracking_enabled": tracker.enabled if tracker is not None else False,
        "volume": speaker_volume,
        "speaker_out": speaker_out,
        "camera_rotate": config.CAMERA_ROTATE,
        "limits": {"pan": config.TRACK_PAN_LIMIT, "tilt": [config.TRACK_TILT_MIN, config.TRACK_TILT_MAX]},
        "tuning": {k: getattr(config, k) for k in TUNABLE},
        "speech": speech_now.snapshot() if speech_now is not None else None,   # his words as he says them
        "computer": computer.controller.status(),
    }


def console_command(action: str, payload: dict) -> dict:
    """A control from the page. Runs on the HTTP thread; hops onto the event
    loop. Raises ValueError for anything the page shouldn't have asked."""
    fut = asyncio.run_coroutine_threadsafe(_console_command(action, payload), main_loop)
    return fut.result(timeout=10)


def _number(payload: dict, key: str, lo: float, hi: float) -> float:
    try:
        v = float(payload[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{key} must be a number")
    if not (lo <= v <= hi):
        raise ValueError(f"{key} must be between {lo:g} and {hi:g}")
    return v


async def _console_command(action: str, payload: dict) -> dict:
    global awake_until, speaker_out
    if action == "emotion":
        name = payload.get("name")
        if name not in config.EMOTIONS:
            raise ValueError(f"emotions: {', '.join(config.EMOTIONS)}")
        await send_to_robot({"type": "emotion", "name": name})
    elif action == "face_preview":
        # The dashboard's face editor: show an unsaved pack on the robot (not kept in its flash), awake, with this
        # emotion. face_restore puts the chosen pack back.
        pack = faces.clean(payload.get("pack"))
        name = payload.get("emotion") or "neutral"
        if name not in config.EMOTIONS:
            raise ValueError(f"emotions: {', '.join(config.EMOTIONS)}")
        if robot_socket is None:
            raise ValueError("the robot isn't connected")
        if time.time() >= awake_until:
            awake_until = time.time() + config.AWAKE_SECONDS
            await wake_up()
        await send_to_robot(faces.robot_message(pack, save=False))
        await send_to_robot({"type": "emotion", "name": name})
    elif action == "face_restore":
        await send_face_pack()
    elif action == "head":
        pan = _number(payload, "pan", -config.TRACK_PAN_LIMIT, config.TRACK_PAN_LIMIT)
        tilt = _number(payload, "tilt", config.TRACK_TILT_MIN, config.TRACK_TILT_MAX)
        if body is not None:
            body.stop()
        if tracker is not None and tracker.enabled:
            await set_tracking(False)
        await set_head_held(True)
        await send_to_robot({"type": "pan", "deg": pan})
        await send_to_robot({"type": "tilt", "deg": tilt})
        if tracker is not None:
            tracker.note_pose(pan=pan, tilt=tilt)
    elif action == "center":
        if body is not None:
            body.stop()
        if tracker is not None and tracker.enabled:
            await set_tracking(False)
        await set_head_held(False)
        await send_to_robot({"type": "pan", "deg": 0})
        await send_to_robot({"type": "tilt", "deg": 0})
        if tracker is not None:
            tracker.note_pose(pan=0, tilt=0)
    elif action == "gesture":
        # Try one emotion's body language: his face shows it and the head plays its gesture and a few seconds of
        # sway (wakes him if he's asleep; nothing moves while tracking is on).
        name = payload.get("name")
        if name not in config.EMOTIONS:
            raise ValueError(f"emotions: {', '.join(config.EMOTIONS)}")
        if time.time() >= awake_until:
            await wake_up()
        awake_until = max(awake_until, time.time() + config.AWAKE_SECONDS)
        await send_to_robot({"type": "emotion", "name": name})
        if body is not None:
            body.test(name)
    elif action == "dance":
        # the dashboard's Dance button (a routine at random, or {"name": ...}); wakes him if he's asleep
        if time.time() >= awake_until:
            await wake_up()
            await send_to_robot({"type": "emotion", "name": "happy"})
        awake_until = max(awake_until, time.time() + config.AWAKE_SECONDS)
        name = payload.get("name")
        picked = start_dance(name if isinstance(name, str) else None)
        if picked is None:
            raise ValueError("the robot isn't connected")
        return {"dance": picked}
    elif action == "volume":
        await set_volume(_number(payload, "level", 0.0, 1.0))
    elif action == "speaker_out":
        out = payload.get("out")
        if out not in ("robot", "computer"):
            raise ValueError("out must be robot or computer")
        speaker_out = out
        persist("rocky", "speaker_out", out)
        print(f"console: voice plays on the {out}")
    elif action == "camera_rotate":
        deg = int(_number(payload, "deg", 0, 270))
        if deg not in (0, 90, 180, 270):
            raise ValueError("deg must be 0, 90, 180 or 270")
        config.CAMERA_ROTATE = deg
        persist("rocky", "camera_rotate", deg)
        print(f"console: camera turned {deg} deg clockwise")
    elif action == "track":
        on = bool(payload.get("on", True))
        if on and time.time() >= awake_until:
            awake_until = time.time() + config.AWAKE_SECONDS
            await wake_up()
            await send_to_robot({"type": "emotion", "name": "neutral"})
        await set_tracking(on)
    elif action == "sleep":
        if payload.get("on", True):
            awake_until = 0.0
            await send_to_robot({"type": "emotion", "name": "sleepy"})
            await fall_asleep()
        else:
            awake_until = time.time() + config.AWAKE_SECONDS
            await wake_up()
            await send_to_robot({"type": "emotion", "name": "neutral"})
    elif action in ("say", "ask"):
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > 300:
            raise ValueError("text must be 1 to 300 characters")
        print(f"console: {action} {text}")
        asyncio.create_task(say(text) if action == "say" else converse(text))
    elif action == "tune":
        key = payload.get("key")
        if key not in TUNABLE:
            raise ValueError(f"tunable: {', '.join(TUNABLE)}")
        value = _number(payload, "value", *TUNABLE[key])
        setattr(config, key, value)
        if ears is not None:
            ears.apply_config()
        if key == "AWAKE_SECONDS":
            persist("rocky", "awake_seconds", value)
        else:
            persist(_TUNE_SECTION[key], key, value)
        print(f"console: {key} = {value:g} (saved)")
    elif action == "voice":
        # Speaker tuning; takes effect on the next reply.
        key = payload.get("key")
        if key not in VOICE_TUNABLE:
            raise ValueError(f"voice settings: {', '.join(VOICE_TUNABLE)}")
        value = _number(payload, "value", *VOICE_TUNABLE[key])
        setattr(config, key, value)
        persist("voice", key, value)
        print(f"console: {key} = {value:g} (saved)")
    elif action == "settings_reload":
        await reload_settings()
    else:
        raise ValueError(f"no such control: {action}")
    return {}


# ── Listening ────────────────────────────────────────────────────────────────

async def mic_meter() -> None:
    """Three seconds of live mic level and speech probability, so you can see
    whether it hears you."""
    if ears is None:
        print("(not listening — type `listen` first)")
        return
    which = "robot mic" if ears.source == "robot" else f"Mac mic \"{ears.device_name}\""
    print(f"{which}: talk for 3 seconds...")
    peak_level = 0.0
    peak_prob = 0.0
    for _ in range(12):
        await asyncio.sleep(0.25)
        level, prob = ears.level, ears.speech_prob
        peak_level = max(peak_level, level)
        peak_prob = max(peak_prob, prob)
        print(f"  level {level:.3f} {'#' * min(40, int(level * 500)):40s} speech {prob:.2f}")
    print(f"peak level {peak_level:.3f}, peak speech probability {peak_prob:.2f} (needs > {config.VAD_THRESHOLD}) ->",
          "speech detected" if peak_prob > config.VAD_THRESHOLD else
          ("NOT DETECTED: raise MIC_GAIN in firmware config.h, or get closer" if ears.source == "robot" else
           "NOT DETECTED: wrong mic, gain down, or no mic permission"))
    if ears.source == "mac":
        import sounddevice as sd
        print("other inputs:", ", ".join(f"[{i}] {d['name']}" for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0))


async def start_listening() -> None:
    global ears
    loop = asyncio.get_running_loop()
    print(f"loading speech model {config.STT_MODEL} and the turn-taking models (first run downloads them)...")
    try:
        ears = await loop.run_in_executor(
            None,
            lambda: Ears(
                on_utterance=lambda text, t0, t1: loop.call_soon_threadsafe(
                    heard.put_nowait, (text, t0, t1, time.time())
                ),
                on_speech_start=lambda: loop.call_soon_threadsafe(speech_began),
                on_turn_end=lambda: loop.call_soon_threadsafe(turn_ended),
                device=config.MIC_DEVICE,
            ),
        )
        mic = await loop.run_in_executor(None, ears.start)
    except Exception as e:
        ears = None
        print(f"(could not start listening: {e})")
        print("  check: mic plugged in? Is your terminal allowed to use the microphone (macOS:")
        print("  System Settings > Privacy & Security > Microphone)? MIC_DEVICE in server/.env?")
        return
    if ears.mac_error:
        print(f"(Mac mic unavailable: {ears.mac_error})")
        print("  the robot's mic still works; for a Mac fallback check MIC_DEVICE in server/.env,")
        print("  plug the interface in, or allow the Terminal to use the microphone")
    else:
        print(f"listening on \"{mic}\" — say \"hey {config.ROBOT_NAME}\"")
    if ears.device_missing:
        print(f"(\"{ears.device_missing}\" isn't plugged in: listening on the default input until it is)")
    pick_mic_source()
    await update_robot_mic()


def stop_listening() -> None:
    global ears
    if ears is not None:
        ears.stop()
        ears = None
    print("stopped listening")


async def head_loop() -> None:
    """Sends the tracker's head moves to the robot, and says when a face is found or lost."""
    was_tracking = False
    while True:
        pan, tilt, tracking = await head_moves.get()
        if tracker is not None and not tracker.enabled:
            continue
        if tracking != was_tracking:
            print("tracking: face found — head follows" if tracking else "tracking: face lost — head stays put")
            was_tracking = tracking
        if pan is not None:
            await send_to_robot({"type": "pan", "deg": pan})
        if tilt is not None:
            await send_to_robot({"type": "tilt", "deg": tilt})
        eyes.tracking_info["tracking"] = tracking


async def doze_loop() -> None:
    """When the awake clock runs out, he nods off on his own (sleepy face,
    no announcement). Saying "hey Rocky" wakes him again."""
    was_awake = False
    while True:
        await asyncio.sleep(1)
        awake = time.time() < awake_until
        if was_awake and not awake:
            print(f"({config.ROBOT_NAME} dozed off — say \"hey {config.ROBOT_NAME}\" to wake him)")
            await fall_asleep()
        was_awake = awake


async def _next_heard() -> tuple[str, float, float, float] | None:
    """Wait for the rest of an interrupted question. Keeps waiting while the
    ears still hear speech; gives up after a few quiet seconds."""
    waited = 0.0
    while True:
        try:
            return await asyncio.wait_for(heard.get(), timeout=0.5)
        except asyncio.TimeoutError:
            waited += 0.5
            hearing = ears is not None and ears.hearing
            if (not hearing and waited >= 4.0) or waited >= 30.0:
                return None


async def voice_loop() -> None:
    """Turns what the ears hear into conversations. A bug in one turn must
    not kill the loop (then nothing you say would get through), so each turn
    is guarded."""
    while True:
        item = await heard.get()
        try:
            await _handle_heard(item)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"(voice loop error, ignoring that turn: {e})")


async def _handle_heard(item: tuple[str, float, float, float]) -> None:
    global awake_until
    text, started_at, ended_at, heard_at = item
    woke, question = strip_wake_word(text)
    # Judge the follow-up window by when you STARTED talking, not by when
    # the transcript arrived — long sentences shouldn't time out.
    if not woke and started_at >= awake_until:
        print(f"(heard, ignoring: {text})")
        return
    print(f"{config.HUMAN_NAME}: {text}")
    eyes.last_heard = text
    awake_until = time.time() + config.AWAKE_SECONDS  # anything you say keeps him up
    norm = normalize(text)
    lines = personality.LINES
    if any(p in norm for p in config.TRACK_ON_PHRASES) and not any(p in norm for p in config.TRACK_OFF_PHRASES):
        if woke:
            await wake_up()
        await set_tracking(True)
        print(f"{config.ROBOT_NAME} [happy]: {lines['track_on']}")
        await send_to_robot({"type": "emotion", "name": "happy"})
        await say(lines["track_on"])
        return
    if any(p in norm for p in config.TRACK_OFF_PHRASES):
        await set_tracking(False)
        print(f"{config.ROBOT_NAME} [neutral]: {lines['track_off']}")
        await send_to_robot({"type": "emotion", "name": "neutral"})
        await say(lines["track_off"])
        return
    if any(p in norm for p in config.SLEEP_PHRASES):
        # "Rocky, sleep": goodnight line, sleepy face, and only "hey Rocky"
        # wakes him. No brain call.
        awake_until = 0.0
        print(f"{config.ROBOT_NAME} [sleepy]: {lines['sleep']}")
        if tracker is not None and tracker.enabled:
            await set_tracking(False)   # stop following now, not after the goodnight line
        await send_to_robot({"type": "emotion", "name": "sleepy"})
        await say(lines["sleep"])
        await fall_asleep()
        awake_until = 0.0  # say() doesn't touch it, but be explicit
        return
    if woke:
        # Heard his name: eyes open, head back to eye level.
        await wake_up()
        await send_to_robot({"type": "emotion", "name": "surprised"})
        if tracker is None or not tracker.tracking:
            await send_to_robot({"type": "tilt", "deg": 0})
            if tracker is not None:
                tracker.note_pose(tilt=0)
    if woke and not question:
        # Just "hey Rocky" — wait for the actual question.
        await say(lines["wake"])
        awake_until = time.time() + config.AWAKE_SECONDS
        return
    finished = await converse(question, ended_at, heard_at)
    while not finished:
        # He was cut off while thinking: wait for the rest of the sentence
        # and answer the whole thing once.
        item = await _next_heard()
        if item is None:
            print("  (didn't catch the rest — answering what I heard)")
            finished = await converse(question)
            continue
        text, started_at, ended_at, heard_at = item
        _, more = strip_wake_word(text)
        print(f"{config.HUMAN_NAME}: {text}")
        question = f"{question} {more}".strip()
        eyes.last_heard = question
        awake_until = time.time() + config.AWAKE_SECONDS
        finished = await converse(question, ended_at, heard_at)


async def console_loop() -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:  # EOF
            break
        if not await handle_console_line(line):
            break


async def main() -> None:
    print(f"{config.ROBOT_NAME} brain server — model {config.MODEL}")
    print(f"listening for the robot on ws://0.0.0.0:{config.PORT}")
    eyes.state_provider = console_state
    eyes.command_handler = console_command
    eyes.serve(config.LIVE_VIEW_PORT, config.LIVE_VIEW_BIND)
    print(f"live view + controls: http://localhost:{config.LIVE_VIEW_PORT}/  (this computer only; the launcher's "
          f"dashboard on :8766 adds settings and a Start button)")
    if not os.environ.get("ROBOT_TOKEN"):
        print("WARNING: ROBOT_TOKEN is not set in server/.env — the robot will be refused")
    global tracker, brain, main_loop, perception, body, dancer
    apply_settings(settings.load())
    loop = asyncio.get_running_loop()
    main_loop = loop
    brain = RobotBrain(ABILITIES, extra_tools=lambda: computer.tools(computer.controller), quick=computer.QUICK)
    tracker = Tracker(eyes, lambda p, t, on: loop.call_soon_threadsafe(head_moves.put_nowait, (p, t, on)))
    body = Body(send_to_robot, tracker)
    dancer = Dancer(send_to_robot, tracker)
    dancer.ready = lambda: not thinking and not speaking_now()
    dancer.before = _before_dancing
    body.allowed = lambda: (robot_socket is not None and time.time() < awake_until and not tracker.enabled
                            and current_emotion != "sleepy" and not dancer.dancing)
    eyes.has_annotator = True
    perception = Perception(eyes, lambda name: asyncio.run_coroutine_threadsafe(gesture_seen(name), loop))
    perception.awake = lambda: time.time() < awake_until
    perception.paused = lambda: ((ears is not None and ears.transcribing) or body.moving()   # (moving: blur, fake waves)
                                 or time.time() < roam_moving_until or dancer.dancing)
    perception.enabled = config.PERCEPTION
    tracker.perception = perception   # its boxes go on the live view
    perception.start()
    tracker.start()
    print("face tracking:", "on" if config.TRACKING else f"off — say \"{config.ROBOT_NAME}, track me\" or type `track on`")
    print("type `ask <question>` to talk to the brain right now\n")
    # The robot's WebSocket library always sends "Origin: file://"; a browser always sends its page's origin.
    # Accepting only that (or none) keeps web pages from connecting and posing as the robot.
    async with websockets.serve(handle_robot, "0.0.0.0", config.PORT, max_size=1024 * 1024,  # snapshots ~40-300 KB
                                origins=["file://", None]):
        voice_task = asyncio.create_task(voice_loop())
        doze_task = asyncio.create_task(doze_loop())
        head_task = asyncio.create_task(head_loop())
        mic_task = asyncio.create_task(mic_watch())
        if config.LISTEN_ON_START:
            await start_listening()
        try:
            await console_loop()
        finally:
            voice_task.cancel()
            doze_task.cancel()
            head_task.cancel()
            mic_task.cancel()
            if ears is not None:
                stop_listening()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
