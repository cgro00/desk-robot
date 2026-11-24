"""Drawing on his face: "Rocky, draw a spaceship".

The language model draws the picture as a short list of strokes (lines, pen paths, circles, ellipses, boxes) on the
128x64 screen. Here they become pictures for the robot: the same drawing with more and more of the pen's path in it,
so it appears line by line, as if he were sketching it. The robot shows each one in place of his eyes (the `drawing`
message, firmware 0.4.4+) and puts his face back DRAW_HOLD_S after the last.

Simple line art at this size reads well: a spaceship, a cat, a coffee mug, a house. It takes the model ~5 s and
about 1000 output tokens.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re

import cv2
import numpy as np
import openai

from . import config
from .thinking import reasoning_switch

W, H = 128, 64

PROMPT = """You draw small pictures for a 128x64 pixel black-and-white screen (x 0-127 left to right, y 0-63 top to \
bottom).
Draw: {subject}.
Reply with JSON only: {{"strokes": [...]}} in the order a person would draw them, at most 40 strokes. Each stroke is \
one of:
  {{"line": [x1, y1, x2, y2]}}
  {{"poly": [x1, y1, x2, y2, ...], "closed": false}}   (a pen path; closed joins the end to the start)
  {{"circle": [cx, cy, r], "fill": false}}
  {{"ellipse": [cx, cy, rx, ry], "fill": false}}
  {{"rect": [x, y, w, h], "fill": false}}
Simple bold line art, like a clear doodle or an icon: recognisable at a glance, using most of the screen, lines not \
too close together. White on black."""

# Added for circuits and diagrams, which otherwise come out as a jumble of boxes at this size.
DIAGRAM_RULES = """
This is a circuit, schematic or diagram: show only the essential parts (at most 4 or 5), spread out across the whole \
screen with clear gaps between them. Wires are straight horizontal and vertical lines that meet the parts' ends. Use \
the standard symbols, drawn about 16-24 pixels big: resistor = a zigzag poly, LED or diode = a triangle with a bar at \
its tip (LED: plus two short arrow lines pointing away), battery = a pair of parallel lines, one long and one short, \
capacitor = two short parallel lines with a gap, switch = a short line at an angle, ground = three shrinking \
horizontal lines, a chip = a rectangle with short pin lines. No text or letters."""
_DIAGRAM = re.compile(r"\b(circuit|schematic|diagram|wiring|breadboard)", re.I)

# Added for Pictionary (pictionary.py), where a label would give the answer away.
GAME_RULES = """
This is for a guessing game: draw the thing itself so it can be recognised from its shape. No text, letters or \
numbers anywhere."""

_client: openai.OpenAI | None = None


def get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(base_url=config.LLM_BASE_URL, api_key=os.environ.get("LLM_API_KEY", "missing"),
                                timeout=config.DRAW_TIMEOUT, max_retries=0)
    return _client


def prompt_for(subject: str, rules: str = "") -> str:
    return PROMPT.format(subject=subject) + (DIAGRAM_RULES if _DIAGRAM.search(subject) else "") + rules


def strokes_for(subject: str, rules: str = "") -> list[dict]:
    """Ask the model to draw `subject` (`rules`: more instructions, like GAME_RULES). Blocking (run it in an
    executor). Raises if it couldn't."""
    reply = get_client().chat.completions.create(
        model=config.DRAW_MODEL or config.MODEL, max_tokens=4000, extra_body=reasoning_switch(),
        messages=[{"role": "user", "content": prompt_for(subject, rules)}])
    strokes = clean(parse(reply.choices[0].message.content or ""))
    if reply.choices[0].finish_reason == "length":
        print(f"  (drawing of {subject!r} was cut off at max_tokens: {len(strokes)} strokes made it)")
    if not strokes:
        raise ValueError("the drawing came out empty")
    return strokes


def parse(text: str) -> list:
    """The strokes in the model's reply. A reply that isn't quite JSON (a stray comma, a cut-off end) still gives
    every stroke that is whole."""
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            doc = json.loads(text[start:end + 1])
            if isinstance(doc, dict) and isinstance(doc.get("strokes"), list):
                return doc["strokes"]
        except json.JSONDecodeError:
            pass
    found = []
    for piece in re.findall(r"\{[^{}]*\}", text):   # the strokes themselves have no braces inside
        try:
            found.append(json.loads(piece))
        except json.JSONDecodeError:
            continue
    return found


_SHAPES = {"line": 4, "circle": 3, "ellipse": 4, "rect": 4}


def clean(raw: list) -> list[dict]:
    """Strokes with whole-number coordinates near the screen, at most DRAW_MAX_STROKES; anything malformed is
    dropped."""
    out = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        for kind in ("line", "poly", "circle", "ellipse", "rect"):
            nums = s.get(kind)
            if not isinstance(nums, list) or not all(isinstance(n, (int, float)) and not isinstance(n, bool)
                                                     and math.isfinite(n) for n in nums):
                continue
            nums = [int(round(max(-64, min(n, 191)))) for n in nums]   # a little off-screen is fine; far is junk
            if kind == "poly":
                nums = nums[:2 * 64]
                if len(nums) < 4 or len(nums) % 2:
                    continue
            elif len(nums) != _SHAPES[kind]:
                continue
            out.append({kind: nums, "fill": bool(s.get("fill")), "closed": bool(s.get("closed"))})
            break
        if len(out) >= config.DRAW_MAX_STROKES:
            break
    return out


def _path(s: dict) -> tuple[np.ndarray, bool]:
    """The pen's path for a stroke: points in drawing order, and whether it's a filled shape."""
    if "line" in s:
        x1, y1, x2, y2 = s["line"]
        return np.array([[x1, y1], [x2, y2]], np.int32), False
    if "poly" in s:
        pts = np.array(s["poly"], np.int32).reshape(-1, 2)
        return (np.vstack([pts, pts[:1]]) if s["closed"] else pts), False
    if "rect" in s:
        x, y, w, h = s["rect"]
        return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]], np.int32), s["fill"]
    cx, cy, rx, ry = (s["circle"] + s["circle"][2:]) if "circle" in s else s["ellipse"]
    pts = cv2.ellipse2Poly((cx, cy), (max(abs(rx), 1), max(abs(ry), 1)), 0, 0, 360, 8)
    return pts.astype(np.int32), s["fill"]


def _partial(pts: np.ndarray, length: float) -> np.ndarray:
    """The first `length` pixels of the path."""
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        seg = float(np.hypot(*(b - a)))
        if length >= seg:
            out.append(b)
            length -= seg
            continue
        if seg > 0 and length > 0:
            out.append((a + (b - a) * (length / seg)).round().astype(np.int32))
        break
    return np.array(out, np.int32)


def frames(strokes: list[dict], count: int | None = None) -> list[np.ndarray]:
    """The drawing as it appears: `count` pictures (H x W, 0 or 255) with more and more of the pen's path, the
    last one the whole drawing. A filled shape fills in once its outline is done."""
    count = max(1, count or config.DRAW_FRAMES)
    paths = [_path(s) for s in strokes]
    lengths = [float(np.hypot(*np.diff(p, axis=0).T).sum()) if len(p) > 1 else 0.0 for p, _ in paths]
    total = sum(lengths) or 1.0
    out = []
    for k in range(1, count + 1):
        budget = total * k / count
        img = np.zeros((H, W), np.uint8)
        for (pts, fill), length in zip(paths, lengths):
            if budget <= 0:
                break
            if budget >= length:
                if fill:
                    cv2.fillPoly(img, [pts], 255)
                cv2.polylines(img, [pts], False, 255, 1)
            elif len(pts) > 1:
                cv2.polylines(img, [_partial(pts, budget)], False, 255, 1)
            budget -= length
        out.append(img)
    return out


def xbm(img: np.ndarray) -> str:
    """A picture as the robot takes it: 1 bit a pixel, rows top to bottom, the leftmost pixel in each byte's lowest
    bit (XBM), base64."""
    bits = np.packbits(img[:H, :W] > 0, axis=1, bitorder="little")
    return base64.standard_b64encode(bits.tobytes()).decode()
