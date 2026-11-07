"""Central knobs for the desk-robot brain."""

import os
from pathlib import Path

# ── Secrets ──────────────────────────────────────────────────────────────────
# API keys live in server/.env (git-ignored; see .env.example), one KEY=VALUE
# per line. Anything already exported in the shell wins over the file.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
if _ENV_FILE.is_file():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _v = _v.strip().strip("'\"")
        if _v:  # a blank line in .env means "not set", not "set to nothing"
            os.environ.setdefault(_k.strip(), _v)

from . import settings as _settings
from . import characters as _characters

# Who he is: Rocky (built in, personality.py), or a character made in the dashboard's Personalities tab
# (server/characters/<id>.json), which brings its own name, voice and wake words (they replace Rocky's below).
# Read once at start: switching characters restarts the brain.
CHARACTER_ID, CHARACTER = _characters.active(_settings.load()["character"]["id"])

# The robot's name — the wake word is "hey <name>".
ROBOT_NAME = CHARACTER["name"] if CHARACTER else "Rocky"

# Your name — Rocky calls you this. Set it in the dashboard (Settings > Rocky; saved in server/settings.json), or as
# HUMAN_NAME in server/.env; "friend" until you do. Read once at start: the character prompt is built from it.
HUMAN_NAME = _settings.load()["rocky"]["human_name"] or os.environ.get("HUMAN_NAME", "friend")

# Language model. The brain speaks the OpenAI-style chat API, which
# OpenRouter, Anthropic and OpenAI all serve, so pick a provider and put its
# key in server/.env as LLM_API_KEY. OpenRouter is the default because one key
# reaches every model, and the model is just a string. To use another, set
# LLM_BASE_URL and LLM_MODEL in server/.env (or change the defaults below):
#
#   provider    LLM_BASE_URL                       MODEL (examples)
#   OpenRouter  https://openrouter.ai/api/v1       anthropic/claude-haiku-5.5   (fast, stays in character, ~$0.0005 a question)
#                                                  google/gemini-2.5-flash-lite (cheapest that still sounds like Rocky)
#                                                  openai/gpt-4.1-mini          (middle ground)
#   Anthropic   https://api.anthropic.com/v1/      claude-haiku-5-5             (the same Haiku 5.5, billed by Anthropic)
#   OpenAI      https://api.openai.com/v1          gpt-4.1-mini
#
# The model must accept images (Rocky sends camera frames) and tool calls
# (he moves his head with them).
LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or "https://openrouter.ai/api/v1"
MODEL = os.environ.get("LLM_MODEL") or "anthropic/claude-haiku-5.5"
# Haiku 5.5 thinks before it answers unless told not to: hidden reasoning that delays his first word by a second or
# more, and with a picture can use up the whole reply so he says nothing. His replies are a few sentences in
# character, not puzzles, so it's off. Sent to OpenRouter (its "reasoning" option) and Anthropic (its "thinking"
# option; without it a question with a picture can come back empty); other providers get nothing.
LLM_REASONING = False

# WebSocket port the robot connects to.
PORT = 8765

# Rocky's voice and how much he says. The character itself (system prompt,
# canned lines) is in personality.py.
#   TTS_VOICE_ID  Fish Audio (fish.audio) voice: open the voice's page on the
#                 site and copy the 32-char ID from the URL. Needs
#                 FISH_AUDIO_API_KEY in server/.env.
#   TTS_TEMPERATURE / TTS_TOP_P
#                 Fish generation settings. Lower = steadier, fewer
#                 hallucinated sounds/laughs/extra words (a known quirk of
#                 generated speech); higher = more expressive. Fish's
#                 defaults are 0.7 / 0.7.
#   REPLY_MAX_SENTENCES
#                 the prompt asks for at most this many, and the server cuts
#                 anything past it before it's spoken (keeps voice cost down).
#   TTS_LEVEL     loudness the voice is held to (RMS, 0..1; see TTS_MAX_GAIN).
#   TTS_HIGHPASS_HZ
#                 bass cut before the speaker (24 dB/octave), 0 = off. Set for
#                 the Dayton Audio CE32A-8 (1-1/4", 8 ohm): it resonates at
#                 274 Hz and its cone moves only 0.7 mm, so anything lower
#                 can't be heard, only felt as rattle and distortion. A
#                 different small speaker: just above its resonance (Fs).
#   TTS_PRESENCE_DB
#                 boost above ~1.5 kHz, where speech clarity lives, 0 = off.
#                 Lifts a muffled voice; too much sounds thin and hissy.
# The console page has sliders for level / bass cut / presence that apply
# live; set the winners here to keep them.
TTS_VOICE_ID = "6dd07916890445e59c5f019ad0fc7879"
TTS_MODEL = ""    # Fish's model (its `model` header): "" = Fish's default, "drama-3" = Drama 3. A character sets its own
TTS_ACTING = False  # the voice model performs [directions] and <whisper>...</whisper> (Drama 3), so they're kept in
TTS_TEMPERATURE = 0.4
TTS_TOP_P = 0.6
REPLY_MAX_SENTENCES = 3
TTS_LEVEL = 0.12
TTS_HIGHPASS_HZ = 300.0
TTS_PRESENCE_DB = 0.0
# Any voice, not just Rocky's (mouth.Leveler / Equalizer):
#   TTS_CONE_LIMIT  how far the speaker's cone may travel (relative; 0 = off). Loud, deep syllables (a laugh, a
#                   shout, a voice with a lot of body) push the small cone past its limit and it distorts; above this
#                   only the low part of the voice is turned down. 0.15 is where Rocky's loudest lines sit.
#   TTS_AUTO_PRESENCE  learn each voice's balance and lift its 1-4 kHz (clarity) when it's heavy in the low mids,
#                   which sounds muffled on this speaker, toward TTS_CLARITY_DB (clarity energy vs 300 Hz-1 kHz, dB;
#                   Rocky's voice is about -1.2) by at most TTS_AUTO_PRESENCE_MAX dB, on top of TTS_PRESENCE_DB.
TTS_CONE_LIMIT = 0.15
TTS_AUTO_PRESENCE = True
TTS_CLARITY_DB = -1.5
TTS_AUTO_PRESENCE_MAX = 6.0

# Text-to-speech. The voice comes from Fish Audio (TTS_VOICE_ID above).
TTS_FALLBACK_VOICE = "Fred"  # built-in voice used until Fish Audio is set up: a macOS `say`
                             # voice name (`say -v ?` lists them); Windows and Linux use their default
# Loudness. Fish's level wanders from line to line, so the audio goes through
# an automatic gain control (mouth.Leveler) that holds it near TTS_LEVEL
# (RMS, 0..1) using at most TTS_MAX_GAIN of boost, with a soft limiter on
# the peaks. 0.12 / 6.0 are the values of the original build (2026-09-06),
# which sounded right on the robot's speaker; 0.26 drove every peak into
# the limiter and the amp near clipping (crackly, muffled). Raise the
# robot's `volume` for everyday loudness, not this.
TTS_MAX_GAIN = 6.0
# The loudest a sample may get (0..1 of full scale) before the robot's volume. The MAX98357A amp at its default
# 9 dB gain asks for about 1.6 W into an 8 ohm speaker at full scale but stays clean only to about 1.4 W, and less
# when the servos pull the 5 V supply down, so the very top is reserved: peaks stop about 2 dB short of it.
TTS_PEAK = 0.8
# Diagnostics, both off by default so nothing is written to disk. DEBUG_SAVE_TTS
# saves each spoken clip and its text to server/debug/tts/ (git-ignored, keeps
# the last 30); DEBUG_TTS_CHECK also transcribes each clip afterwards to flag
# audio that doesn't match the text, i.e. the voice model made something up.
# The check runs a second speech model right after every reply, which slows
# the next transcription if you answer quickly.
DEBUG_SAVE_TTS = False
DEBUG_TTS_CHECK = False

# Listening. The robot's mic when it is connected, the computer's otherwise.
LISTEN_ON_START = True
WAKE_PHRASES = [  # what speech-to-text tends to hear for "hey Rocky"
    "hey rocky",
    "hey rocket",
    "hey rocking",
    "hi rocky",
    "a rocky",
    "hey ricky",
]
if CHARACTER:
    TTS_VOICE_ID = CHARACTER["voice_id"]
    TTS_MODEL = CHARACTER["tts_model"]
    TTS_ACTING = TTS_MODEL in _characters.ACTING_MODELS
    WAKE_PHRASES = CHARACTER["wake_words"]
STT_MODEL = "base.en"    # faster-whisper model: base.en ~0.3 s per utterance on an Apple
                         # Silicon Mac, small.en hears a little better but takes ~1 s
# The Hugging Face commit of that model to download (Systran/faster-whisper-<STT_MODEL>).
# Pinned so a changed upload can't be loaded unnoticed; set to None to take the latest.
STT_REVISION = "3d3d5dee26484f91867d81cb899cfcf72b96be6c"
STT_THREADS = 8          # CPU threads for transcription (0 = library default of 4)
STT_PROMPT = f"Hey {ROBOT_NAME}. {ROBOT_NAME} is a robot."  # name hint for the model
MIC_SOURCE = "auto"      # "robot" = the robot's mic, "mac" = MIC_DEVICE below,
                         # "auto" = robot when it's connected, else this computer
                         # (the dashboard's "He listens through" sets both: settings rocky.mic / mic_device)
SPEAKER_OUT = "robot"    # where Rocky's voice plays: "robot" = the robot's speaker when it's connected
                         # (else this computer), "computer" = always this computer. The live view
                         # switches it too (until restart).
MIC_DEVICE = os.environ.get("MIC_DEVICE") or None  # local input by name (set MIC_DEVICE in
                         # server/.env); None = system default. List devices: python -m sounddevice
# Speech detection (server/brain/turn.py). A Silero VAD model decides whether
# each 32 ms chunk is speech (VAD_THRESHOLD, 0..1: lower = more sensitive).
# Once speech starts, a cutoff 0.15 lower keeps softer syllables from being
# mistaken for a pause. Microphone level and background noise still matter.
# When you pause for TURN_PAUSE_SECONDS the Smart Turn model listens to the
# whole sentence and
# decides whether you sound finished (probability above TURN_THRESHOLD ->
# Rocky answers now). If it thinks you're mid-thought it keeps listening,
# re-checking every TURN_RECHECK_SECONDS, and gives up waiting after
# TURN_MAX_SILENCE seconds of quiet. If Rocky keeps cutting you off, raise
# TURN_THRESHOLD; if he waits too long after you finish, lower it.
VAD_THRESHOLD = 0.4
TURN_PAUSE_SECONDS = 0.2
TURN_RECHECK_SECONDS = 0.6
TURN_MAX_SILENCE = 2.5
TURN_THRESHOLD = 0.5
# If you start talking again while Rocky is still thinking (before he speaks),
# speech that lasts this long cancels his reply and he listens to the rest.
CANCEL_MIN_SPEECH = 0.3
DEBUG_SAVE_UTTERANCE = ""  # set to "debug/last_utterance.wav" to keep the last thing heard, for mic tuning
AWAKE_SECONDS = 60.0     # after "hey Rocky" he stays awake; each thing you say
                         # resets this clock, and when it runs out he dozes off
_NAME = ROBOT_NAME.lower()
SLEEP_PHRASES = [        # any of these puts him to sleep until the next "hey <name>"
    f"{_NAME} sleep",
    f"{_NAME} go to sleep",
    "go to sleep",
    "goodnight",
    "good night",
    "stop listening",
]
# What he says as he goes to sleep: personality.py, LINES.

# Emotions the firmware knows how to display (firmware/src/face.cpp; their look comes from the face pack,
# server/faces/). The model picks one per reply from this list.
from .faces import EMOTIONS  # noqa: E402

# Camera. The robot streams JPEGs while connected; the live view
# is at http://localhost:<LIVE_VIEW_PORT>/ on this computer.
CAMERA_FPS = 10
CAMERA_STREAM_SIZE = "vga"   # "vga" 640x480 (sharper face tracking, ~170 KB/s of WiFi) or "qvga" 320x240
LIVE_VIEW_PORT = 8767   # the brain's own page + API. The launcher (server/launcher.py) serves the dashboard on 8766
                        # and passes these through; without the launcher, open this port directly
LIVE_VIEW_BIND = "127.0.0.1"  # this computer only. "0.0.0.0" would show the page and camera to the whole LAN
                              # (the controls still answer this computer only).
SEND_CAMERA_TO_BRAIN = True  # let Rocky see the camera at all (False = no picture ever goes to the model)
# Where the pictures come from while no robot is connected. "robot" (the default): no robot, no camera. "mac":
# this computer's camera, on any system (dashboard: Settings > Vision; CAMERA_LOCAL_INDEX picks a second
# camera, None = the default). While a robot IS connected its camera always wins, so this only matters running
# the server alone. The value is "mac" for consistency with the mic's MIC_SOURCE (same wording, same idea).
CAMERA_SOURCE = (os.environ.get("CAMERA_SOURCE") or "robot").strip().lower()
CAMERA_LOCAL_INDEX = (os.environ.get("CAMERA_LOCAL_INDEX") or "").strip() or None  # cv2 camera number; None = default
CAMERA_LOCAL_ROTATE = 0      # turn THIS computer's pictures 0/90/180/270 deg (the robot's CAMERA_ROTATE is its own)
CAMERA_LOCAL_MIRROR = False  # and mirror them left-right (webcam pictures already come out the right way round)
# Seeing better (dashboard: Settings > Vision).
#   VISION_EVERY_QUESTION  attach the camera's picture to every question while he's awake, not only ones about
#                          seeing, so he knows what you're doing (a small picture costs ~100 tokens, a sharp one
#                          ~1,000, about $0.0001 on Haiku 5.5; the picture goes to the model provider either way)
#   SNAPSHOT_SIZE          the sharp still for questions about seeing and for `look` (taken the moment you stop
#                          talking, while your words are turned into text, so it costs no time): "svga" 800x600,
#                          "xga" 1024x768, "uxga" 1600x1200, "" = stream frame. A context picture (above) is the
#                          stream frame: a sharp one makes every answer ~0.4 s slower
#   PERCEPTION             watch the stream with two small local models (brain/perception.py): faces (a short
#                          note next to the picture with questions about seeing) and hands (for a wave)
#   GESTURE_REACTIONS      a wave wakes him (objects and what you hold come from the picture, read by the model)
VISION_EVERY_QUESTION = True
SNAPSHOT_SIZE = "xga"
PERCEPTION = True
PERCEPTION_FPS = 8.0         # looks per second (hands every look; faces every other)
GESTURE_REACTIONS = True
# A question with any of these phrases (whole words, anywhere in it) is about seeing: he waits for a sharp still
# and is told to look at it. Other questions get the picture only for context (VISION_EVERY_QUESTION), or none.
# Phrases, not single words: "see", "look", "watch", "who is" and "how many" alone sent a picture with "see you
# later", "look, I had a rough day", "who is Grace?" and "how many legs does a spider have?", and he described the
# room. He can always use his `look` ability to get a fresh picture on his own.
CAMERA_WORDS = [
    # asking what he sees
    "what do you see", "what can you see", "can you see", "do you see", "what you see", "are you seeing",
    "what is this", "what's this",
    "what are you looking at", "what do you notice", "do you notice", "notice anything",
    # pointing him at something
    "look at", "take a look", "have a look", "look around", "watching me",
    # what the human shows or wears
    "what am i holding", "what i'm holding", "what is this i'm holding", "what am i wearing", "what i'm wearing",
    "how do i look", "do i look", "how many fingers", "what color is this", "what colour is this",
    "what color is my", "what colour is my", "what color am i", "what colour am i",
    "who is this", "who is that", "who's this", "who's that", "who is here", "who's here", "who is with me",
    "recognize me", "recognise me", "recognize this", "recognise this", "recognize that", "recognise that",
    "describe what", "describe this", "describe that", "describe me", "describe my",
    # the camera, and places in view
    "your camera", "the camera", "a picture", "a photo", "in front of you", "behind you", "on my desk",
    "on the desk", "in the room", "whiteboard", "on the screen", "on my screen",
    "read this", "read that", "read what", "can you read",
]

# Body language (body.py): small head moves that go with his emotion while he talks, and a thinking glance while he
# works out a reply. Never while asleep, tracking, or while a look is taking its picture. Dashboard: Rocky card.
BODY_LANGUAGE = True

# Focus mode (focus.py): during a focus session he checks the camera picture every few seconds with OpenAI's
# Decisions API (decisions.py; OPENAI_API_KEY in server/.env) and tells you off when you're on your phone. A frame
# is ~640 tokens: a 25-minute session costs about 3 cents. Start it by voice ("help me focus for 25 minutes") or the
# dashboard's Focus card.
DECISIONS_MODEL = "gpt-6-luna"     # the only model the Decisions API runs (2026-11)
DECISIONS_BASE_URL = "https://api.openai.com/v1"
DECISIONS_TIMEOUT = 5.0            # s; it answers in under 1, and a late answer is about an old picture
FOCUS_MINUTES = 25.0               # a session's length when none is asked for
FOCUS_CHECK_EVERY = 3.0            # s between looks
FOCUS_PHONE_PROB = 0.8             # a look counts as "on the phone" at this probability or more
FOCUS_STREAK = 3                   # ...this many looks in a row (~8 s) before he says anything: a glance is fine
FOCUS_PUT_DOWN_PROB = 0.3          # after he caught you: below this two looks running = put down (he approves)
FOCUS_NAG_EVERY = 30.0             # s at least between two tellings-off, if you keep at it
FOCUS_TURN_S = 1.2                 # s of face tracking to turn to you before he tells you off
# During a session he answers nothing he hears, so you can talk to people; "hey Rocky, stop focus" (any of these
# after his name) is the one thing that still works: it ends the session.
FOCUS_STOP_PHRASES = ["stop focus", "end focus", "stop the focus", "end the focus", "cancel focus",
                      "stop focusing", "focus off", "turn off focus"]

# Face tracking: the head follows the biggest face in the picture.
# Off until you ask ("Rocky, track me"); phrases below switch it without a
# brain call, and Rocky can also switch it himself when asked in other words.
TRACKING = False
TRACK_ON_PHRASES = ["track me", "follow me", "watch me", "keep your eyes on me", "look at me"]
TRACK_OFF_PHRASES = ["stop tracking", "stop following", "stop watching", "stop looking at me"]
# What he says when tracking starts/stops: personality.py, LINES.
# Camera field of view in HEAD degrees (how far the head turns to move something edge to edge), along the sensor's
# long and short sides; the tracker swaps them itself when CAMERA_ROTATE turns the picture 90 degrees. Measured on
# the V2 head by moving the head and matching the room between frames (the lens is much wider than its data sheet's
# 62 / 48).
TRACK_HFOV = 104.0
TRACK_VFOV = 92.0
# The V2 head holds the camera on its side, so the brain turns every frame upright as it arrives (before the
# live view, face tracking and Rocky's own look at the camera use it). Degrees clockwise: 0, 90, 180 or 270.
# The live view's Rotate button changes it until restart.
CAMERA_ROTATE = 270
# The picture also comes out mirrored left-right (panning the head right moved the room right in the frame, where
# a true view moves it left). Mirroring it back keeps Rocky's left and right true, for face tracking and for what
# he says he sees. To check a new head: `pan 15` must shift the room LEFT in the picture, `tilt 15` DOWN.
CAMERA_MIRROR = True
TRACK_GAIN = 0.5           # fraction of the way to the face the head is sent per frame (lower = calmer)
TRACK_DEADBAND = 0.05      # ignore errors smaller than this fraction of half-frame (~3 deg with the wide lens)
TRACK_PAN_SIGN = 1         # flip to -1 if the head turns AWAY from you
TRACK_TILT_SIGN = 1        # flip to -1 if it nods the wrong way
# The head's range, used everywhere the brain moves it (tracking, `look`, the dashboard's sliders, what the model
# is told it can do).
TRACK_PAN_LIMIT = 95.0     # must match PAN_MIN/MAX_DEG in firmware config.h
TRACK_TILT_MIN = -85.0     # must match TILT_MIN/MAX_DEG in firmware config.h
TRACK_TILT_MAX = 85.0
TRACK_LOST_SECONDS = 4.0   # no face this long → he stops following and the head stays where it is
# Face tracking's own tilt range, inside the head's +-85: plenty to follow a face without nodding right down.
TRACK_FOLLOW_TILT = (-30.0, 60.0)
# A frame shows where the head was a moment ago (exposure, JPEG, WiFi), and the head is still moving to where it
# was last sent. The tracker keeps a copy of the firmware's motion (speed + acceleration limits, firmware config.h)
# to know where the head pointed when each frame was taken, so it aims at where the face really is instead of
# piling fresh moves on top of ones the camera hasn't seen yet (that overshoots and oscillates).
TRACK_LATENCY_S = 0.3      # camera → brain delay plus servo lag, seconds (measured 0.18-0.38)
TRACK_PAN_SPEED, TRACK_PAN_ACCEL = 150.0, 450.0     # = PAN_MAX_SPEED, PAN_ACCEL
TRACK_TILT_SPEED, TRACK_TILT_ACCEL = 160.0, 600.0   # = TILT_MAX_SPEED, TILT_ACCEL

# How many conversation turns to remember before forgetting the oldest.
MAX_HISTORY_TURNS = 20
