"""Mouth: turn the robot's words into sound.

Two voices:
  * Fish Audio (Rocky's real voice, config.TTS_VOICE_ID) when
    FISH_AUDIO_API_KEY is set. Plain HTTPS POST asking for raw 16 kHz PCM. The response body
    streams, so the first audio arrives ~0.3 s in, long before the sentence
    is finished, and is passed straight on to the speaker.
  * the computer's built-in voice as the fallback so the loop is audible
    before that's set up: `say` on macOS, Windows' speech engine through
    PowerShell, espeak-ng on Linux.

`stream(text)` yields 16 kHz mono s16le PCM chunks — the exact format the
robot's speaker expects (docs/protocol.md) — as they're produced. `play()`
plays a buffer on this computer.
"""

from __future__ import annotations

import io
import json
import os
import re
import ssl
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from collections.abc import Iterator

import certifi

import numpy as np

from . import config

SAMPLE_RATE = 16_000
FISH_TTS_URL = "https://api.fish.audio/v1/tts"


def fish_available() -> bool:
    return bool(config.TTS_VOICE_ID and os.environ.get("FISH_AUDIO_API_KEY"))


_MARKUP = re.compile(r"[*_`#~<>\[\]{}|\\]")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


# Drama 3's acting cues (config.TTS_ACTING): a [direction] before the words it changes, and paired tags around words.
_PAIRED = ("whisper", "emphasis", "soft", "fast", "slow", "stress", "forceful")
_CUE = re.compile(r"\[[^\[\]]{1,60}\]|</?(?:" + "|".join(_PAIRED) + r")>", re.IGNORECASE)


def without_cues(text: str) -> str:
    """The words alone, for showing what he said (and for a voice that would read the cues out)."""
    if not config.TTS_ACTING:
        return text
    return re.sub(r"\s+", " ", _CUE.sub(" ", text)).strip()


def clean_for_acting(text: str) -> str:
    """clean_for_tts for a voice that performs cues: keeps the [directions] and paired tags, drops other markup.
    Each sentence is its own request, so a tag the model opened in one sentence and closed in the next is closed
    (or reopened) inside each."""
    keep: list[str] = []

    def stash(m: re.Match) -> str:
        keep.append(m.group(0).lower() if m.group(0).startswith("<") else m.group(0))
        return f" \x00{len(keep) - 1}\x00 "

    text = _CUE.sub(stash, text.replace("\x00", ""))   # \x00 marks the kept cues below
    text = clean_for_tts(text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], text)
    for tag in _PAIRED:
        opens = len(re.findall(f"<{tag}>", text))
        closes = len(re.findall(f"</{tag}>", text))
        if closes > opens:
            text = f"<{tag}>" * (closes - opens) + text
        elif opens > closes:
            text = text + f"</{tag}>" * (opens - closes)
    words = _CUE.sub(" ", text)
    return text if re.search(r"\w", words) else ""


def clean_for_tts(text: str) -> str:
    """What the voice model gets: plain spoken words. Markdown marks, emoji
    and runs of punctuation tend to make generated speech do odd things."""
    text = _EMOJI.sub("", _MARKUP.sub("", text))
    text = re.sub(r"([!?.,])\1+", r"\1", text)      # "!!!" -> "!"
    text = re.sub(r"\.{2,}", ".", text)              # "..." -> "."
    text = re.sub(r"\s+", " ", text).strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _fish_stream(text: str) -> Iterator[bytes]:
    """Raw 16 kHz PCM from Fish Audio, yielded as the server produces it."""
    body = json.dumps(
        {
            "text": text,
            "reference_id": config.TTS_VOICE_ID,
            "format": "pcm",          # headerless s16le at sample_rate: nothing to parse
            "sample_rate": SAMPLE_RATE,
            "latency": "balanced",    # a little faster to the first byte than "normal"
            "temperature": config.TTS_TEMPERATURE,
            "top_p": config.TTS_TOP_P,
        }
    ).encode()
    headers = {
        "Authorization": f"Bearer {os.environ['FISH_AUDIO_API_KEY']}",
        "Content-Type": "application/json",
    }
    if config.TTS_MODEL:
        headers["model"] = config.TTS_MODEL   # without it Fish uses its default model, quietly
    req = urllib.request.Request(FISH_TTS_URL, data=body, method="POST", headers=headers)
    # python.org builds of Python on macOS don't see the system root certs;
    # certifi's bundle (already installed with the openai package) does.
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        carry = b""  # a chunk boundary can split a 16-bit sample in half
        while chunk := resp.read(4096):
            chunk = carry + chunk
            if len(chunk) % 2:
                chunk, carry = chunk[:-1], chunk[-1:]
            else:
                carry = b""
            if chunk:
                yield chunk


class Biquad:
    """One second-order IIR filter section with streaming state, so a chunk
    picks up exactly where the last one left off (no clicks at chunk edges)."""

    def __init__(self, b: np.ndarray, a: np.ndarray) -> None:
        self.b = b / a[0]
        self.a = a / a[0]
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    @classmethod
    def highpass(cls, fc: float, sr: int = SAMPLE_RATE, q: float = 0.7071) -> "Biquad":
        w0 = 2 * np.pi * fc / sr
        al = np.sin(w0) / (2 * q)
        c = np.cos(w0)
        return cls(np.array([(1 + c) / 2, -(1 + c), (1 + c) / 2]), np.array([1 + al, -2 * c, 1 - al]))

    @classmethod
    def lowpass(cls, fc: float, sr: int = SAMPLE_RATE, q: float = 0.7071) -> "Biquad":
        w0 = 2 * np.pi * fc / sr
        al = np.sin(w0) / (2 * q)
        c = np.cos(w0)
        return cls(np.array([(1 - c) / 2, 1 - c, (1 - c) / 2]), np.array([1 + al, -2 * c, 1 - al]))

    @classmethod
    def high_shelf(cls, fc: float, gain_db: float, sr: int = SAMPLE_RATE, q: float = 0.7071) -> "Biquad":
        A = 10 ** (gain_db / 40)
        w0 = 2 * np.pi * fc / sr
        al = np.sin(w0) / (2 * q)
        c = np.cos(w0)
        s = 2 * np.sqrt(A) * al
        b = np.array([A * ((A + 1) + (A - 1) * c + s), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - s)])
        a = np.array([(A + 1) - (A - 1) * c + s, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - s])
        return cls(b, a)

    def retune(self, other: "Biquad") -> None:
        """Take another filter's coefficients and keep this one's state, so a setting can change mid-stream."""
        self.b, self.a = other.b, other.a

    def process(self, x: np.ndarray) -> np.ndarray:
        b0, b1, b2 = self.b
        _, a1, a2 = self.a
        x1, x2, y1, y2 = self.x1, self.x2, self.y1, self.y2
        y = np.empty_like(x)
        for i, v in enumerate(x.tolist()):
            o = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, v, y1, o
            y[i] = o
        self.x1, self.x2, self.y1, self.y2 = x1, x2, y1, y2
        return y


PRESENCE_HZ = 1500.0  # the presence shelf starts here; speech clarity is 1-4 kHz


# What the automatic presence lift learned about each voice (voice ID + model): [low-mid energy, clarity energy,
# lift in dB], kept while the brain runs so a voice's next reply starts where its last one ended.
_LEARNED_PRESENCE: dict[str, list[float]] = {}


class Equalizer:
    """Speaker tuning: a bass cut (the small speaker can't
    reproduce bass, it just rattles the shell) and a presence lift for a
    voice that comes out muffled. Both off = pass-through.

    With `auto` on, the presence lift also follows the voice: voices differ in how much of their sound is in the
    low mids (300 Hz-1 kHz, body) against 1-4 kHz (clarity), and on this speaker a voice heavy in the low mids sounds
    muffled. The two are summed per voice over the last half minute or so of speech (before the lift; loud chunks
    count most, so a whisper or a laugh doesn't swing it) and the lift steers toward config.TTS_CLARITY_DB, up to
    config.TTS_AUTO_PRESENCE_MAX more than the fixed presence_db. A voice that is already clear enough gets nothing
    extra (Rocky's own voice measures about -1.2 dB)."""

    DECAY = 0.995   # per chunk (~130 ms): how long the per-voice sums remember
    STEP = 0.2      # per chunk: how far the lift moves toward what the sums call for

    def __init__(self, highpass_hz: float = 0.0, presence_db: float = 0.0, auto: bool = False,
                 voice: str = "") -> None:
        self.stages: list[Biquad] = []
        if highpass_hz > 0:
            # Two stages (4th order, 24 dB/octave): a single one is still
            # letting half the energy through an octave below the cutoff.
            self.stages.append(Biquad.highpass(highpass_hz))
            self.stages.append(Biquad.highpass(highpass_hz))
        self.fixed_db = presence_db
        self.auto = auto
        self.voice = voice
        self.memory = _LEARNED_PRESENCE.setdefault(voice, [0.0, 0.0, 0.0]) if auto else [0.0, 0.0, 0.0]
        self.learned = self.memory[2]
        self.shelf_db = presence_db + self.learned
        self.shelf = Biquad.high_shelf(PRESENCE_HZ, self.shelf_db) if self.shelf_db else None

    def process(self, x: np.ndarray) -> np.ndarray:
        for stage in self.stages:
            x = stage.process(x)
        if self.auto:
            self._learn(x)
        if self.shelf is not None:
            x = self.shelf.process(x)
        return x

    def _learn(self, x: np.ndarray) -> None:
        if len(x) < 512 or float(np.sqrt(np.mean(x * x))) < Leveler.GATE * 2:
            return   # too short to read, or a gap
        f = np.fft.rfftfreq(len(x), 1 / SAMPLE_RATE)
        p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        body = p[(f >= 300) & (f < 1000)].sum()
        clear = p[(f >= 1000) & (f < 4000)].sum()
        m = self.memory
        m[0] = m[0] * self.DECAY + body
        m[1] = m[1] * self.DECAY + clear
        if m[0] <= 0 or m[1] <= 0:
            return
        need = float(np.clip(config.TTS_CLARITY_DB - 10 * np.log10(m[1] / m[0]), 0.0, config.TTS_AUTO_PRESENCE_MAX))
        self.learned += self.STEP * (need - self.learned)
        m[2] = self.learned
        want = self.fixed_db + self.learned
        if abs(want - self.shelf_db) >= 0.25:
            self.shelf_db = want
            if self.shelf is None:
                self.shelf = Biquad.high_shelf(PRESENCE_HZ, want)
            else:
                self.shelf.retune(Biquad.high_shelf(PRESENCE_HZ, want))


class Leveler:
    """Streaming automatic gain: holds the voice near a steady loudness.

    Fish's level wanders from sentence to sentence and within one, and audio
    streams to the speaker as it's made, so it can't be normalized after the
    fact. Each chunk (~130 ms) gets a loudness reading; a smoothed estimate
    follows it upward over ~0.3 s and downward over ~1.5 s, ignoring
    near-silence so gaps don't pump up the noise. The gain steers toward
    TTS_LEVEL / estimate, capped at TTS_MAX_GAIN, and is ramped
    across each chunk so nothing clicks. The shape inside a chunk (syllables)
    is kept. The equalizer runs first, so the level is judged on
    what the speaker will actually get. Use one Leveler per reply so its
    sentences match each other."""

    GATE = 0.004        # RMS below this is a gap, not a quiet word
    ATTACK = 0.5        # per chunk: louder than expected → follow fast
    RELEASE = 0.1       # per chunk: quieter than expected → follow slowly
    SQUEEZE = 0.35      # how far each chunk is pulled toward the running level
                        # (0 = only sentence-scale leveling, 1 = flatten every chunk)
    # never let a chunk's peak exceed config.TTS_PEAK: the chunk is turned down, not bent

    # Cone protection (config.TTS_CONE_LIMIT): the speaker's cone moves furthest for the low part of the voice, and
    # past its limit it distorts (a rattly, clipped sound on loud, deep syllables). The low band (below SPLIT_HZ)
    # is turned down just enough, the rest of the voice is left alone.
    SPLIT_HZ = 700.0
    CONE_HZ = 274.0     # the speaker's resonance: above it, cone travel falls 12 dB per octave

    def __init__(self) -> None:
        # Read once per reply, so the console's sliders apply from the next one.
        self.level = config.TTS_LEVEL
        self.ceiling = config.TTS_PEAK
        self.eq = Equalizer(config.TTS_HIGHPASS_HZ, config.TTS_PRESENCE_DB, auto=config.TTS_AUTO_PRESENCE,
                            voice=f"{config.TTS_VOICE_ID}/{config.TTS_MODEL}")
        self.env = 0.0      # loudness estimate; 0 = nothing heard yet
        self.gain = 1.0
        self.cone_limit = config.TTS_CONE_LIMIT
        # a Linkwitz-Riley crossover (two Butterworth sections per side): the bands stay in phase and add back up
        # flat, so turning the low one down really turns the lows down
        self.split_lo = [Biquad.lowpass(self.SPLIT_HZ), Biquad.lowpass(self.SPLIT_HZ)]
        self.split_hi = [Biquad.highpass(self.SPLIT_HZ), Biquad.highpass(self.SPLIT_HZ)]
        self.cone_lo = Biquad.lowpass(self.CONE_HZ)   # how far each band moves the cone
        self.cone_hi = Biquad.lowpass(self.CONE_HZ)
        self.low_env = _Lookahead(release_s=0.2)
        self.peak_env = _Lookahead(release_s=0.05)
        # the last LOOKAHEAD samples, held back until the samples after them are known: high band, low band, gain,
        # and how far each band alone would move the cone
        self.held = [np.zeros(0, np.float32)] * 5

    def process(self, pcm: bytes) -> bytes:
        """The chunk, leveled. Comes out _Lookahead.SAMPLES (3 ms) late; flush() at the end of a line."""
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if not len(x):
            return pcm
        x = self.eq.process(x).astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x)))
        if rms > self.GATE:
            if self.env == 0.0:
                self.env = rms                                  # first sound: trust it
            elif rms > self.env:
                self.env += self.ATTACK * (rms - self.env)
            else:
                self.env += self.RELEASE * (rms - self.env)
        if self.env and rms > self.GATE:
            # Judge this chunk by a blend of the running level and its own
            # level: loud bits come down a little, quiet words come up a little.
            judged = self.env * (rms / self.env) ** self.SQUEEZE
            target = min(config.TTS_MAX_GAIN, max(0.5, self.level / judged))
        else:
            target = self.gain  # a gap: hold the gain where it is
        # Peak limiting the way the original build did it (2026-09-06): cap the
        # chunk's gain so its peak stays under the ceiling. The waveform is never
        # bent, so loud syllables get quieter rather than harsher.
        peak = float(np.abs(x).max())
        if peak > 0:
            target = min(target, self.ceiling / peak)
        gain = np.linspace(self.gain, target, len(x), dtype=np.float32)
        self.gain = target
        low, high = x, x
        for f in self.split_lo:
            low = f.process(low)
        for f in self.split_hi:
            high = f.process(high)
        new = [high, low, gain, np.abs(self.cone_lo.process(low)), np.abs(self.cone_hi.process(high))]
        self.held = [np.concatenate([h, n.astype(np.float32)]) for h, n in zip(self.held, new)]
        return self._release(len(self.held[0]) - _Lookahead.SAMPLES)

    def flush(self) -> bytes:
        """The held-back end of the line."""
        return self._release(len(self.held[0]), final=True)

    def _release(self, n: int, final: bool = False) -> bytes:
        """Send out the first n held samples. Each gain below can see LOOKAHEAD samples ahead, so it is already down
        when a loud moment arrives instead of jumping at it: the ramp in a chunk's gain from the last chunk's could
        start a loud syllable above the ceiling, and the whole-chunk trim that caught it used to step the level at
        the chunk's edge (a click)."""
        if n <= 0:
            return b""
        high, low, gain, cone_lo, cone_hi = self.held
        if self.cone_limit > 0:
            # the high band's share of the travel stays; the low band gets what's left of the limit
            room = np.maximum(self.cone_limit - cone_hi * gain, 0.0)
            need = np.minimum(1.0, room / np.maximum(cone_lo * gain, 1e-9))
            g = self.low_env.gains(need, n, final)
            # the samples still held get a provisional gain: enough for the peak limiter to see ahead
            low = low * np.concatenate([g, np.minimum(g[-1], need[n:])])
        y0 = (high + low) * gain
        y = y0[:n] * self.peak_env.gains(np.minimum(1.0, self.ceiling / np.maximum(np.abs(y0), 1e-9)), n, final)
        self.held = [a[n:] for a in self.held]
        return (np.clip(y, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


class _Lookahead:
    """A gain that never lets a sample through above what it may have, without ever jumping: it starts down
    SAMPLES before the sample that needs it (in a straight line), and comes back up over `release_s`."""

    SAMPLES = 48    # 3 ms

    def __init__(self, release_s: float) -> None:
        self.step = 1.0 / (release_s * SAMPLE_RATE)
        self.mins = np.ones(self.SAMPLES - 1, np.float32)   # the last few window minimums: the next ramp starts from them
        self.last = 1.0

    def gains(self, allowed: np.ndarray, n: int, final: bool) -> np.ndarray:
        """Gains for the first n samples of `allowed` (the most each sample may have; it runs SAMPLES past n unless
        `final`). The minimum over the next SAMPLES samples, averaged over the last SAMPLES of those minimums, is a
        ramp that reaches each limit by the time its sample comes; then a slow way back up."""
        k = self.SAMPLES
        a = np.concatenate([allowed.astype(np.float32), np.full(k - 1, allowed[-1] if final else 1.0, np.float32)])
        mins = np.lib.stride_tricks.sliding_window_view(a, k).min(axis=1)[:n]
        ext = np.concatenate([self.mins, mins])
        c = np.concatenate([[0.0], np.cumsum(ext, dtype=np.float64)])
        ramp = ((c[k:k + n] - c[:n]) / k).astype(np.float32)
        out = np.empty(n, np.float32)
        last, step = self.last, self.step
        for i, r in enumerate(ramp.tolist()):
            last = r if r < last + step else last + step
            out[i] = last
        self.last = last
        self.mins = ext[-(k - 1):]
        return out


def _wav_to_pcm16k(wav_bytes: bytes) -> bytes:
    """Any WAV → 16 kHz mono s16le, whatever rate/channels/width it came in."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width == 2:
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768
    elif width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif width == 4:
        samples = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2**31
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        n_out = int(len(samples) * SAMPLE_RATE / rate)
        x_old = np.linspace(0, 1, len(samples), endpoint=False)
        x_new = np.linspace(0, 1, n_out, endpoint=False)
        samples = np.interp(x_new, x_old, samples).astype(np.float32)
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()


class NoBuiltinVoice(RuntimeError):
    """This computer has no speech engine we know how to drive."""


# PowerShell script for Windows' built-in speech engine: text on stdin,
# 16 kHz mono WAV out. Built and tested on macOS; this path is untested.
_WINDOWS_TTS = """
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile($args[0], $fmt)
$s.Speak([Console]::In.ReadToEnd())
$s.Dispose()
"""


def _synthesize_builtin(text: str) -> bytes:
    """The computer's own voice as 16 kHz s16le PCM. Text always goes in on
    stdin, never as an argument, so a reply starting with "-" can't turn
    into options ("-f /etc/hosts" would make `say` read a file)."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        if sys.platform == "darwin":
            cmd = ["say", "-v", config.TTS_FALLBACK_VOICE, "-o", path, "--data-format=LEI16@16000"]
        elif sys.platform == "win32":
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_TTS, path]
        elif shutil.which("espeak-ng") or shutil.which("espeak"):
            cmd = [shutil.which("espeak-ng") or "espeak", "--stdin", "-w", path]
        else:
            raise NoBuiltinVoice("no built-in voice on this system (install espeak-ng, or set FISH_AUDIO_API_KEY)")
        subprocess.run(cmd, input=text.encode(), check=True, timeout=60)
        with open(path, "rb") as f:
            wav = f.read()
    finally:
        os.unlink(path)
    return _wav_to_pcm16k(wav)


def stream(text: str, leveler: Leveler | None = None) -> Iterator[bytes]:
    """Rocky's words as 16 kHz mono s16le PCM chunks, yielded as they're made.
    Fish Audio when it's set up and reachable, else the computer's own voice
    (all at once). Loudness is steadied by `leveler` (a fresh one if none is
    given). A saved copy goes to debug/tts/ when DEBUG_SAVE_TTS is on."""
    acting = config.TTS_ACTING
    plain = clean_for_tts(without_cues(text))   # the built-in voice would read cues out
    text = clean_for_acting(text) if acting else plain
    if not text:
        return
    leveler = leveler or Leveler()
    parts: list[bytes] = []
    complete = False
    try:
        chunks: Iterator[bytes] | None = None
        first = b""
        if fish_available():
            try:
                chunks = _fish_stream(text)
                first = next(chunks, b"")
            except Exception as e:  # network, auth, bad voice id... still speak
                print(f"(fish audio failed, using the built-in voice: {e})")
                chunks = None
        if chunks is None:
            try:
                first = _synthesize_builtin(plain)
            except NoBuiltinVoice as e:
                print(f"(cannot speak: {e})")
                return
        if first:
            first = leveler.process(first)
            parts.append(first)
            yield first
        if chunks is not None:
            try:
                for chunk in chunks:
                    chunk = leveler.process(chunk)
                    parts.append(chunk)
                    yield chunk
            except Exception as e:  # dropped mid-stream: say what we have
                print(f"(fish audio stream cut short: {e})")
        tail = leveler.flush()
        if tail:
            parts.append(tail)
            yield tail
        complete = True
    finally:
        if complete and parts and config.DEBUG_SAVE_TTS:
            _save_clip(b"".join(parts), text)


def _save_clip(pcm: bytes, text: str) -> None:
    """Keep recent clips on disk so a weird one can be inspected."""
    try:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug", "tts")
        os.makedirs(d, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")  # date too, or a morning clip sorts "older" than last night's
        path = os.path.join(d, f"{stamp}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE); w.writeframes(pcm)
        with open(path[:-4] + ".txt", "w") as f:
            f.write(text + "\n")
        wavs = [f for f in os.listdir(d) if f.endswith(".wav")]
        old = sorted(wavs, key=lambda f: os.path.getmtime(os.path.join(d, f)))[:-30]
        for f in old:
            for ext in (".wav", ".txt"):
                try: os.unlink(os.path.join(d, f[:-4] + ext))
                except OSError: pass
    except OSError:
        pass


def play(pcm: bytes) -> None:
    """Play PCM on this computer's default output. Blocks until done.

    On macOS this uses the system player rather than sounddevice: opening an
    output stream while the mic stream is running trips CoreAudio ("cannot
    do in current context") and can hang, and afplay is a separate process
    so it can't. Elsewhere sounddevice plays it directly (untested here)."""
    if not pcm:
        return
    if sys.platform != "darwin":
        import sounddevice as sd
        sd.play(np.frombuffer(pcm, dtype=np.int16), SAMPLE_RATE, blocking=True)
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        subprocess.run(["afplay", path], check=False, timeout=60)
    finally:
        os.unlink(path)
