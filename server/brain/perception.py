"""Perception: what Rocky notices on camera, worked out on this computer.

A background thread watches the robot's camera stream (a few frames a second) with two small open models:

  MediaPipe gesture recognizer (Apache-2.0)  hands: an open hand moving side to side is a wave, and a wave wakes
                                             him. (It knows other signs too; nothing uses them.)
  MediaPipe face landmarker (Apache-2.0)     faces: smiling, mouth open, eyes closed, looking at Rocky or away,
                                             roughly how far

What's in the room, what you hold, reading a label: the language model does that from the camera picture (it's far
better at it than the object detector and naming model that used to run here, which were often wrong). The face
model's short note ("one person about 1 m away, looking at you, smiling") goes with questions about seeing, next to
that picture. It also stands aside while your words are being turned into text, so they come back sooner. Nothing
leaves this computer; only the note and, when asked, the picture go to the model provider.

While Rocky is asleep only the hands are watched, a few times a second (a wave is all that matters then).

The models download on first use into server/models/ (git-ignored), pinned to fixed versions and checked against
their SHA-256. If anything fails, perception switches itself off and Rocky carries on without it.
"""

from __future__ import annotations

import hashlib
import math
import ssl
import threading
import time
import urllib.request
from collections import deque
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import config

MODELS = Path(__file__).resolve().parent.parent / "models"
DOWNLOADS = {  # file: (url, sha256)
    "gesture_recognizer.task": (
        "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task",
        "97952348cf6a6a4915c2ea1496b4b37ebabc50cbbf80571435643c455f2b0482"),
    "face_landmarker.task": (
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
        "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"),
}
RECENT = 2.5          # seconds a sighting counts as "now"
FACE_HALF_WIDTH_M = 0.07
ASLEEP_FPS = 5.0      # looks per second while he's asleep: hands only, enough to catch a wave


def ensure_models() -> None:
    """Download any missing model (once), checking its SHA-256."""
    MODELS.mkdir(exist_ok=True)
    ctx = None
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for name, (url, sha) in DOWNLOADS.items():
        path = MODELS / name
        if path.exists():
            continue
        print(f"perception: downloading {name} (first run only)...")
        req = urllib.request.Request(url, headers={"User-Agent": "desk-robot"})
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != sha:
            raise RuntimeError(f"{name}: download doesn't match its checksum")
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)


class Perception:
    def __init__(self, eyes, on_gesture: Callable[[str], None] | None = None) -> None:
        self.eyes = eyes
        self.on_gesture = on_gesture       # called (from this thread) with "wave"
        self.awake: Callable[[], bool] = lambda: True   # main.py: is Rocky awake? Asleep = hands only
        self.paused: Callable[[], bool] = lambda: False  # main.py: hold off (your words are being transcribed)
        self.enabled = True
        self.ready = False
        self.error = ""
        self.boxes: list[tuple[str, int, int, int, int]] = []   # newest frame, for the live view overlay
        self._lock = threading.Lock()
        self._people: deque = deque(maxlen=40)  # (time, faces seen)
        self._face: dict | None = None
        self._waved_at = 0.0
        self._palm: deque = deque(maxlen=20)    # (time, wrist x 0..1) while an open hand is up
        self._event_at: dict[str, float] = {}
        self._frame_at = 0.0
        self._looks = 0

    def start(self) -> None:
        threading.Thread(target=self._run, name="perception", daemon=True).start()

    # ── the models ──
    def _load(self) -> None:
        from mediapipe.tasks.python import BaseOptions, vision
        self._gest = vision.GestureRecognizer.create_from_options(vision.GestureRecognizerOptions(
            base_options=BaseOptions(model_asset_path=str(MODELS / "gesture_recognizer.task")), num_hands=2,
            min_hand_detection_confidence=0.5))
        self._faces = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODELS / "face_landmarker.task")), num_faces=2,
            output_face_blendshapes=True, output_facial_transformation_matrixes=True))

    def _run(self) -> None:
        try:
            ensure_models()
            self._load()
            self.ready = True
            print("perception: on (faces, and a wave to wake him)")
        except Exception as e:  # missing package, no network on first run, bad file: carry on without it
            self.error = str(e)
            print(f"perception: off ({e})")
            return
        import cv2
        seq, next_at, warned = -1, 0.0, False
        while True:
            seq = self.eyes.wait_for_new(seq, timeout=1.0)
            now = time.time()
            if not self.enabled or now < next_at or not self.eyes.jpeg or self.paused():
                continue
            next_at = now + 1.0 / max(1.0, config.PERCEPTION_FPS if self.awake() else ASLEEP_FPS)
            img = cv2.imdecode(np.frombuffer(self.eyes.jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            try:
                self._look(img, self.eyes.frame_at, cv2)
            except Exception as e:
                if not warned:
                    print(f"perception: a frame failed ({e})")
                    warned = True

    def _look(self, img, at: float, cv2) -> None:
        import mediapipe as mp
        h, w = img.shape[:2]
        now = time.time()
        awake = self.awake()
        self._looks += 1
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        frame = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # Hands, every look: a wave needs the samples. Only an open hand matters (its wrist moving side to side).
        hands = []
        g = self._gest.recognize(frame)
        for lms, cats in zip(g.hand_landmarks, g.gestures):
            xs, ys = [p.x for p in lms], [p.y for p in lms]
            box = (min(xs) * w, min(ys) * h, (max(xs) - min(xs)) * w, (max(ys) - min(ys)) * h)
            top = cats[0] if cats else None
            palm = top is not None and top.category_name == "Open_Palm" and top.score >= 0.6
            hands.append((palm, box, lms[0].x))

        # Faces every other look (they change slower); not at all while he's asleep.
        faces = self._faces.detect(frame) if awake and self._looks % 2 == 0 else None

        waved = False
        with self._lock:
            self._frame_at = at
            # a wave: an open hand that moves side to side
            palm = next((wx for p, _, wx in hands if p), None)
            if palm is not None:
                self._palm.append((now, palm))
            recent = [x for t, x in self._palm if now - t < 1.5]
            if len(recent) >= 4 and max(recent) - min(recent) > 0.06 and now - self._waved_at > 4:
                self._waved_at = now
                self._palm.clear()
                waved = True
            hand_boxes = [("hand", int(b[0]), int(b[1]), int(b[2]), int(b[3])) for _, b, _ in hands]
            if faces is not None:
                self._face = self._read_face(faces, w, h, now)
                self._people.append((now, len(faces.face_landmarks)))
                face_boxes = []
                for lm in faces.face_landmarks:
                    xs, ys = [p.x for p in lm], [p.y for p in lm]
                    face_boxes.append(("face", int(min(xs) * w), int(min(ys) * h),
                                       int((max(xs) - min(xs)) * w), int((max(ys) - min(ys)) * h)))
                self.boxes = face_boxes + hand_boxes
            elif not awake:
                self.boxes = hand_boxes
        if waved and self._event_at.get("wave", 0) < now - 3 and self.on_gesture is not None:
            self._event_at["wave"] = now
            self.on_gesture("wave")

    def _read_face(self, res, w: int, h: int, now: float) -> dict | None:
        if not res.face_landmarks:
            return None
        sizes = [max(p.x for p in lm) - min(p.x for p in lm) for lm in res.face_landmarks]
        i = int(np.argmax(sizes))
        shapes = {c.category_name: c.score for c in res.face_blendshapes[i]} if res.face_blendshapes else {}
        smile = (shapes.get("mouthSmileLeft", 0) + shapes.get("mouthSmileRight", 0)) / 2
        blink = min(shapes.get("eyeBlinkLeft", 0), shapes.get("eyeBlinkRight", 0))
        yaw = pitch = 0.0
        if res.facial_transformation_matrixes:
            r = np.array(res.facial_transformation_matrixes[i])[:3, :3]
            yaw = math.degrees(math.asin(max(-1.0, min(1.0, -r[2, 0]))))
            pitch = math.degrees(math.atan2(r[2, 1], r[2, 2]))
        # How far: the face's width against the camera's field of view (rough; the lens is very wide).
        hfov = config.TRACK_VFOV if config.CAMERA_ROTATE % 180 else config.TRACK_HFOV
        half = math.radians(sizes[i] * hfov / 2)
        dist = FACE_HALF_WIDTH_M / math.tan(half) if half > 0.01 else None
        return {"at": now, "smiling": smile > 0.45, "mouth_open": shapes.get("jawOpen", 0) > 0.45,
                "eyes_closed": blink > 0.6, "looking": abs(yaw) < 25 and abs(pitch) < 25,
                "distance_m": dist, "yaw": round(yaw), "pitch": round(pitch)}

    # ── what the brain reads ──
    def summary(self) -> dict:
        """The current state, for the prompt note and the dashboard."""
        now = time.time()
        with self._lock:
            live = self.ready and now - self._frame_at < 3
            people = max((n for t, n in self._people if now - t < RECENT), default=0)
            face = self._face if self._face and now - self._face["at"] < RECENT else None
            if face and not people:
                people = 1
            waved = now - self._waved_at < 5
        return {"live": live, "people": people, "face": face, "waving": waved, "ready": self.ready,
                "error": self.error}

    def describe(self) -> str:
        """The face model's line for the model, with a question about seeing, or "" when it has nothing (perception
        off, no live picture, no face in view). Never "no one in view": it only sees faces turned to the camera, and
        the picture that goes with the question shows the rest."""
        s = self.summary()
        if not s["ready"] or not s["live"] or not s["people"]:
            return ""
        who = "one person" if s["people"] == 1 else f"{s['people']} people"
        f = s["face"]
        if f:
            d = f["distance_m"]
            if d is not None:
                who += " very close" if d < 0.4 else f" about {max(0.5, round(d * 2) / 2):g} m away"
            who += ", looking at you" if f["looking"] else ", looking away"
            for key, words in (("smiling", "smiling"), ("eyes_closed", "eyes closed"), ("mouth_open", "mouth open")):
                if f[key]:
                    who += ", " + words
        return f"(What your camera's face detector notices right now, may be wrong: {who}.)"
