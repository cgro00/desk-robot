"""Ears: listen on a microphone, cut the sound into turns, turn each one
into text.

Two possible microphones feed the same pipeline: the Mac's (sounddevice
stream) or the robot's PDM mic, whose frames arrive over the WebSocket and
are handed in through push_audio(). `source` picks which one is live.

Pipeline:  mic blocks → Segmenter (Silero VAD + Smart Turn, see turn.py) →
Transcriber (faster-whisper, local, no cloud) → on_utterance(text, ...)

Where a turn ends is decided the way people do it, not by a fixed silence:
a 0.2 s pause makes the Smart Turn model listen to the whole sentence and
judge whether you sound finished. If you do, Rocky answers right away; if
you sound mid-thought he keeps listening.
"""

from __future__ import annotations

import contextlib
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np

from . import config
from .turn import VAD_CHUNK, SileroVAD, SmartTurn

SAMPLE_RATE = 16_000
BLOCK_SECONDS = 0.03  # 30 ms per mic block
BLOCK_SAMPLES = int(SAMPLE_RATE * BLOCK_SECONDS)
CHUNK_SECONDS = VAD_CHUNK / SAMPLE_RATE  # 32 ms: the VAD's step


class HighPass:
    """One-pole high-pass (~120 Hz). The PDM mic puts out a DC offset and a lot
    of sub-150 Hz rumble that swamps the noise floor; speech doesn't live there."""

    def __init__(self, cutoff_hz: float = 120.0) -> None:
        self.a = 1.0 / (1.0 + 2.0 * np.pi * cutoff_hz / SAMPLE_RATE)
        self.prev_x = 0.0
        self.prev_y = 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        out = np.empty_like(block)
        px, py, a = self.prev_x, self.prev_y, self.a
        for i, x in enumerate(block):
            py = a * (py + x - px)
            px = x
            out[i] = py
        self.prev_x, self.prev_y = px, py
        return out


class Segmenter:
    """Feed float32 audio in (any block size); get whole turns (numpy arrays) out.

    Every 32 ms chunk goes through the VAD. A turn starts on the first speech
    chunk (a short pre-roll is kept so the first syllable isn't clipped) and
    ends when you sound finished: after TURN_PAUSE_SECONDS of quiet the Smart
    Turn model judges the whole turn. Complete → the turn ends now. Incomplete
    → keep listening and re-judge every TURN_RECHECK_SECONDS, giving up after
    TURN_MAX_SILENCE of quiet. `on_speech_start` fires once per turn after
    CANCEL_MIN_SPEECH of speech, so the brain can stop a reply in progress.
    """

    def __init__(
        self,
        vad: SileroVAD,
        judge: SmartTurn | None,
        on_speech_start: Callable[[], None] | None = None,
        pre_roll: float = 0.3,
        min_length: float = 0.4,
        max_length: float = 15.0,
    ) -> None:
        self.vad = vad
        self.judge = judge
        self.on_speech_start = on_speech_start
        self.pre_roll: deque[np.ndarray] = deque(maxlen=max(1, int(pre_roll / CHUNK_SECONDS)))
        self.min_chunks = int(min_length / CHUNK_SECONDS)
        self.max_chunks = int(max_length / CHUNK_SECONDS)
        self.apply_config()
        self._pending = np.zeros(0, dtype=np.float32)  # samples not yet a full VAD chunk
        self._last_push = 0.0
        self.current: list[np.ndarray] = []
        self.quiet_run = 0
        self.speech_chunks = 0
        self.speaking = False
        self.announced = False   # on_speech_start fired for this turn
        self.started_at = 0.0    # wall-clock time the current turn began
        self.ended_at = 0.0      # wall-clock time of the last speech in the finished turn
        self._last_loud_at = 0.0
        self._next_check = 0
        self.speech_prob = 0.0   # VAD output for the latest chunk (for `mic`)
        self.last_decision = ""  # why the last turn ended (for logs)
        # The last ~2 s, chunk by chunk: (time, level, VAD score, counted as voice), for the dashboard's strip, which
        # shows one bar per look (5 a second): the latest 32 ms chunk alone was often a gap between syllables.
        self._recent: deque[tuple[float, float, float, bool]] = deque(maxlen=64)
        self._recent_lock = threading.Lock()

    def apply_config(self) -> None:
        """Re-read the timing knobs from config (they can change at runtime)."""
        self.pause_chunks = max(1, int(config.TURN_PAUSE_SECONDS / CHUNK_SECONDS))
        self.recheck_chunks = max(1, int(config.TURN_RECHECK_SECONDS / CHUNK_SECONDS))
        self.max_quiet_chunks = max(self.pause_chunks, int(config.TURN_MAX_SILENCE / CHUNK_SECONDS))
        self.confirm_chunks = max(1, int(config.CANCEL_MIN_SPEECH / CHUNK_SECONDS))

    @property
    def talking(self) -> bool:
        """Someone has been speaking for at least CANCEL_MIN_SPEECH."""
        return self.speaking and self.announced

    def recent(self, seconds: float) -> tuple[float, float, bool]:
        """Over the last `seconds`: the loudest level, the highest speech score, and whether any of it counted as
        voice (with the softer cutoff that holds once someone has started talking, as the turn itself uses)."""
        since = time.time() - seconds
        with self._recent_lock:
            got = [r for r in self._recent if r[0] >= since]
        if not got:
            return 0.0, 0.0, False
        return max(r[1] for r in got), max(r[2] for r in got), any(r[3] for r in got)

    def push(self, block: np.ndarray) -> np.ndarray | None:
        now = time.time()
        if now - self._last_push > 1.0:
            # A gap in the audio means we were muted (Rocky was talking):
            # don't glue stale pre-roll onto whatever comes next.
            self._pending = np.zeros(0, dtype=np.float32)
            self.pre_roll.clear()
        self._last_push = now
        block = np.asarray(block, dtype=np.float32)
        self._pending = np.concatenate([self._pending, block]) if len(self._pending) else block
        result = None
        while len(self._pending) >= VAD_CHUNK:
            chunk, self._pending = self._pending[:VAD_CHUNK], self._pending[VAD_CHUNK:]
            got = self._step(chunk)
            if got is not None and result is None:
                result = got
        return result

    def _step(self, chunk: np.ndarray) -> np.ndarray | None:
        prob = self.vad(chunk)
        self.speech_prob = prob
        # Match Silero's separate start/stop cutoffs: once a voice has started,
        # keep its softer syllables instead of treating them as a turn-ending
        # pause. Derive from the live setting so console tuning affects both.
        threshold = config.VAD_THRESHOLD
        if self.speaking:
            threshold = max(0.01, threshold - 0.15)
        loud = prob > threshold
        with self._recent_lock:
            self._recent.append((time.time(), float(np.sqrt(np.mean(chunk * chunk))), prob, loud))
        if not self.speaking:
            self.pre_roll.append(chunk)
            if loud:
                self.speaking = True
                self.announced = False
                self.started_at = time.time()
                self._last_loud_at = self.started_at
                self.current = list(self.pre_roll)
                self.quiet_run = 0
                self.speech_chunks = 1
                self._next_check = self.pause_chunks
            return None

        self.current.append(chunk)
        if loud:
            self.quiet_run = 0
            self.speech_chunks += 1
            self._last_loud_at = time.time()
            self._next_check = self.pause_chunks
            if not self.announced and self.speech_chunks >= self.confirm_chunks:
                self.announced = True
                if self.on_speech_start is not None:
                    self.on_speech_start()
            if len(self.current) >= self.max_chunks:
                return self._finish("max length reached")
            return None

        self.quiet_run += 1
        if self.quiet_run >= self.max_quiet_chunks:
            return self._finish(f"{self.quiet_run * CHUNK_SECONDS:.1f}s of quiet")
        if self.quiet_run >= self._next_check:
            self._next_check = self.quiet_run + self.recheck_chunks
            pause = self.quiet_run * CHUNK_SECONDS
            if self.judge is None:
                return self._finish(f"{pause:.1f}s pause")
            p = self.judge.complete_probability(np.concatenate(self.current))
            if p > config.TURN_THRESHOLD:
                return self._finish(f"sounds finished, p={p:.2f} after {pause:.1f}s pause, {self.judge.last_ms:.0f} ms")
            print(f"  (turn: not finished, p={p:.2f} after {pause:.1f}s pause — still listening)")
        return None

    def _finish(self, why: str) -> np.ndarray | None:
        turn = self.current
        self.speaking = False
        self.announced = False
        self.ended_at = self._last_loud_at
        self.current = []
        self.pre_roll.clear()
        self.last_decision = why
        if len(turn) - self.quiet_run < self.min_chunks:
            return None  # a click or a cough, not words
        return np.concatenate(turn)


class Transcriber:
    """Local speech-to-text. The first run downloads the model (~150 MB for
    base.en) into ~/.cache; after that it's offline."""

    def __init__(self, model_name: str = config.STT_MODEL) -> None:
        from faster_whisper import WhisperModel  # slow import, keep it lazy

        self.model = WhisperModel(
            model_name, device="cpu", compute_type="int8", cpu_threads=config.STT_THREADS,
            revision=config.STT_REVISION if model_name == config.STT_MODEL else None,
        )

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language="en",
            beam_size=1,
            vad_filter=True,
            # Tells the model the name to expect; without this "Rocky" comes
            # out as "right" / "righty" about half the time.
            initial_prompt=config.STT_PROMPT,
        )
        return " ".join(s.text.strip() for s in segments).strip()


_WAKE_CLEAN = re.compile(r"[^a-z' ]+")


def normalize(text: str) -> str:
    return " ".join(_WAKE_CLEAN.sub(" ", text.lower()).split())


def _wake_pattern() -> re.Pattern:
    # "hey rocky" -> matches "Hey, Rocky!" etc.: any punctuation/space between
    # the words, case-insensitive, and swallows the punctuation after it.
    alts = "|".join(r"\W+".join(map(re.escape, p.split())) for p in config.WAKE_PHRASES)
    return re.compile(rf"\b(?:{alts})\b[\s,.!?:;-]*", re.IGNORECASE)


_WAKE_RE = _wake_pattern()


def strip_wake_word(text: str) -> tuple[bool, str]:
    """('Hey Rocky, what's 9 times 16?') -> (True, "what's 9 times 16?").
    The question keeps its digits, punctuation and case — the brain needs
    them. Returns (False, text) when no wake phrase is present."""
    m = _WAKE_RE.search(text)
    if m:
        return True, text[m.end():].strip()
    return False, text.strip()


def _save_wav(audio: np.ndarray, path: str) -> None:
    """Keep the last utterance on disk so mic quality can be inspected."""
    import os
    import wave

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    except OSError:
        pass


class Ears:
    """Owns the microphone stream and a worker thread. Calls
    on_utterance(text, started_at, ended_at) from the worker thread for every
    turn heard: started_at is when the person began talking, ended_at when
    their last word ended. on_speech_start() is called (same thread) once a
    new turn has lasted CANCEL_MIN_SPEECH."""

    def __init__(
        self,
        on_utterance: Callable[[str, float, float], None],
        on_speech_start: Callable[[], None] | None = None,
        device: int | str | None = None,
        on_turn_end: Callable[[], None] | None = None,
    ) -> None:
        self.on_utterance = on_utterance
        self.on_turn_end = on_turn_end      # the moment a turn ends, before it's transcribed
        self.transcribing = False           # a turn is being turned into text right now
        self.device = device
        self.device_name = "?"
        self.mac_error: str | None = None  # why the Mac mic could not be opened, if it couldn't
        self.device_missing: str | None = None  # the input asked for, when it isn't there (the default is open instead)
        self.muted = threading.Event()  # set while Rocky is talking (no echo cancel)
        self.source = "mac"             # "mac" or "robot": whose audio is live
        self.level = 0.0                # RMS of the latest live block (for `mic`)
        self._blocks: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stream = None
        self._worker: threading.Thread | None = None
        self._highpass = HighPass()
        self._segmenter = Segmenter(SileroVAD(), SmartTurn(), on_speech_start)
        self.transcriber = Transcriber()

    def set_source(self, source: str) -> None:
        """Switch between the Mac mic and the robot mic."""
        self.source = source

    @property
    def speech_prob(self) -> float:
        return self._segmenter.speech_prob

    def apply_config(self) -> None:
        """Pick up changed listening knobs from config without restarting."""
        self._segmenter.apply_config()

    @property
    def hearing(self) -> bool:
        """Speech is going on right now (a turn is open)."""
        return self._segmenter.speaking

    def recent(self, seconds: float = 0.25) -> tuple[float, float, bool]:
        """(loudest level, highest speech score, any of it voice) over the last `seconds` (the dashboard's strip)."""
        return self._segmenter.recent(seconds)

    @property
    def talking(self) -> bool:
        """Speech has been going on for at least CANCEL_MIN_SPEECH."""
        return self._segmenter.talking

    def push_audio(self, pcm16: bytes) -> None:
        """Robot mic frame (16 kHz mono s16le) from the WebSocket."""
        if self.source != "robot" or self.muted.is_set():
            return
        block = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        block = self._highpass.process(block)
        self.level = float(np.sqrt(np.mean(block * block)))
        self._blocks.put(block)

    def start(self) -> str:
        """Start the worker and try to open the Mac mic. A missing Mac mic
        (interface unplugged, wrong MIC_DEVICE) must not stop the robot's own
        mic from working: it only means there is no Mac fallback, so the
        failure is recorded in mac_error and start() still succeeds. If the
        input asked for isn't there, the system default opens instead and
        device_missing says which one was wanted."""
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        self._open_wanted()
        return self.device_name

    def set_device(self, device: str | None) -> str:
        """Switch to another input on this computer (None = the system default), live."""
        self.device = device
        self._close()
        try:
            import sounddevice as sd
            sd._terminate()   # PortAudio lists the devices once, when it starts: look again, for one plugged in since
            sd._initialize()
        except Exception:
            pass
        self._open_wanted()
        return self.device_name

    def _open_wanted(self) -> None:
        self.device_missing = None
        if not self._open(self.device) and self.device is not None:
            self.device_missing = str(self.device)
            self._open(None)

    def _open(self, device) -> bool:
        stream = None
        try:
            import sounddevice as sd

            # Open every input the device has (a USB interface often has two) and mix them,
            # so it doesn't matter which jack the mic is plugged into.
            channels = max(1, int(sd.query_devices(device, "input")["max_input_channels"]))
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=channels,
                dtype="float32",
                blocksize=BLOCK_SAMPLES,
                device=device,
                callback=self._on_audio,
            )
            stream.start()
            name = sd.query_devices(stream.device)["name"]
        except Exception as e:
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()
            if self._stream is None:
                self.device_name = "no Mac mic"
            self.mac_error = str(e)
            return False
        self._stream, self.device_name, self.mac_error = stream, name, None   # together: the status never sees half
        return True

    def _close(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None

    @property
    def has_mac_mic(self) -> bool:
        return self._stream is not None

    def stop(self) -> None:
        self._close()
        self._blocks.put(None)

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if self.source == "mac" and not self.muted.is_set():
            block = np.clip(indata.sum(axis=1), -1.0, 1.0)
            self.level = float(np.sqrt(np.mean(block * block)))
            self._blocks.put(block)

    def _run(self) -> None:
        segmenter = self._segmenter
        while True:
            block = self._blocks.get()
            if block is None:
                return
            utterance = segmenter.push(block)
            if utterance is None:
                continue
            if self.on_turn_end is not None:
                self.on_turn_end()
            # Bring quiet speech up to a healthy level for the model.
            peak = float(np.abs(utterance).max())
            if peak > 0.001:
                utterance = utterance * min(0.9 / peak, 20.0)
            if config.DEBUG_SAVE_UTTERANCE:
                _save_wav(utterance, config.DEBUG_SAVE_UTTERANCE)
            t0 = time.time()
            self.transcribing = True
            try:
                text = self.transcriber.transcribe(utterance)
            finally:
                self.transcribing = False
            print(f"  (turn: {segmenter.last_decision}; transcribed in {time.time() - t0:.2f}s)")
            if text:
                self.on_utterance(text, segmenter.started_at, segmenter.ended_at)
