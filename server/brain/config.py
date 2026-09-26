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

# The robot's name — the wake word is "hey <name>".
ROBOT_NAME = "Rocky"

# Your name — Rocky calls you this. Set HUMAN_NAME in server/.env so it
# stays out of the repo; "friend" until you do.
HUMAN_NAME = os.environ.get("HUMAN_NAME", "friend")

# Language model. The brain speaks the OpenAI-style chat API, which
# OpenRouter, Anthropic and OpenAI all serve, so pick a provider here and
# put its key in server/.env as LLM_API_KEY. OpenRouter is the default
# because one key reaches every model, and the model is just a string:
#
#   provider    LLM_BASE_URL                       MODEL (examples)
#   OpenRouter  https://openrouter.ai/api/v1       anthropic/claude-haiku-4.5   (best at staying in character, ~$1-4/month)
#                                                  google/gemini-2.5-flash-lite (cheapest that still sounds like Rocky)
#                                                  openai/gpt-4.1-mini          (middle ground)
#   Anthropic   https://api.anthropic.com/v1/      claude-haiku-4-5
#   OpenAI      https://api.openai.com/v1          gpt-4.1-mini
#
# The model must accept images (Rocky sends camera frames) and tool calls
# (he moves his head with them).

# LLM_BASE_URL and MODEL in server/.env override the defaults below.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.environ.get("MODEL", "anthropic/claude-haiku-4.5")

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
#                 bass cut before the speaker, 0 = off. The small speaker
#                 can't reproduce bass; it just rattles the shell. A deep
#                 voice wants 250-350.
#   TTS_PRESENCE_DB
#                 boost above ~1.5 kHz, where speech clarity lives, 0 = off.
#                 Lifts a muffled voice; too much sounds thin and hissy.
# The console page has sliders for level / bass cut / presence that apply
# live; set the winners here to keep them.
TTS_VOICE_ID = "6dd07916890445e59c5f019ad0fc7879"
TTS_TEMPERATURE = 0.4
TTS_TOP_P = 0.6
REPLY_MAX_SENTENCES = 3
TTS_LEVEL = 0.12
TTS_HIGHPASS_HZ = 0.0
TTS_PRESENCE_DB = 0.0

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
STT_MODEL = "base.en"    # faster-whisper model: base.en ~0.3 s per utterance on an Apple
                         # Silicon Mac, small.en hears a little better but takes ~1 s
# The Hugging Face commit of that model to download (Systran/faster-whisper-<STT_MODEL>).
# Pinned so a changed upload can't be loaded unnoticed; set to None to take the latest.
STT_REVISION = "3d3d5dee26484f91867d81cb899cfcf72b96be6c"
STT_THREADS = 8          # CPU threads for transcription (0 = library default of 4)
STT_PROMPT = f"Hey {ROBOT_NAME}. {ROBOT_NAME} is a robot."  # name hint for the model
MIC_SOURCE = "auto"      # "robot" = the robot's mic, "mac" = MIC_DEVICE below,
                         # "auto" = robot when it's connected, else this computer
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

# Emotions the firmware knows how to display (see firmware/src/face.cpp).
EMOTIONS = [
    "neutral",
    "happy",
    "sad",
    "angry",
    "surprised",
    "sleepy",
    "thinking",
]

# Camera. The robot streams small JPEGs while connected; the live view
# is at http://localhost:<LIVE_VIEW_PORT>/ on this computer.
CAMERA_FPS = 10
LIVE_VIEW_PORT = 8766
LIVE_VIEW_BIND = "127.0.0.1"  # this computer only. "0.0.0.0" would show the camera to the whole LAN.
SEND_CAMERA_TO_BRAIN = True  # let Rocky see the camera when a question is about seeing
# A frame is attached only when the question is about seeing (any of these
# words or phrases). Everyday words like "this", "that", "here", "there" and
# "right" are deliberately NOT in the list: they made him describe the room
# in answers that had nothing to do with it. He can always use his `look`
# ability to get a fresh picture on his own.
CAMERA_WORDS = [
    "see", "seeing", "look", "looking", "watch", "watching", "camera", "picture",
    "photo", "image", "view", "describe", "notice", "recognize", "recognise",
    "holding", "wearing", "what color", "what colour", "what am i", "who is",
    "who's", "how many", "in front of you", "behind you", "on my desk", "on the desk",
    "in the room", "whiteboard", "on the screen", "on my screen", "read this", "read that",
    "read the", "read what",
]

# Face tracking: the head follows the biggest face in the picture.
# Off until you ask ("Rocky, track me"); phrases below switch it without a
# brain call, and Rocky can also switch it himself when asked in other words.
TRACKING = False
TRACK_ON_PHRASES = ["track me", "follow me", "watch me", "keep your eyes on me", "look at me"]
TRACK_OFF_PHRASES = ["stop tracking", "stop following", "stop watching", "stop looking at me"]
# What he says when tracking starts/stops: personality.py, LINES.
TRACK_HFOV = 62.0          # camera field of view, degrees (OV2640 stock lens)
TRACK_VFOV = 48.0
TRACK_GAIN = 0.5           # fraction of the error corrected per frame (lower = calmer)
TRACK_DEADBAND = 0.10      # ignore errors smaller than this fraction of half-frame
TRACK_PAN_SIGN = 1         # flip to -1 if the head turns AWAY from you
TRACK_TILT_SIGN = 1        # flip to -1 if it nods the wrong way
TRACK_PAN_LIMIT = 60.0     # must match PAN_MIN/MAX_DEG in firmware config.h
TRACK_TILT_MIN = -60.0     # must match TILT_MIN/MAX_DEG in firmware config.h
TRACK_TILT_MAX = 0.0
TRACK_LOST_SECONDS = 4.0   # no face this long → idle glances resume

# How many conversation turns to remember before forgetting the oldest.
MAX_HISTORY_TURNS = 20
