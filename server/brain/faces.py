"""Face packs: how Rocky's eyes look for every emotion.

A pack is one JSON file. The built-in ones are in server/faces/ (in the repo); packs you make in the dashboard go in
server/faces/custom/ (git-ignored; export one from the dashboard to share it). The brain sends the chosen pack to
the robot, which keeps it in flash. The firmware (firmware/src/face.cpp) and the dashboard's preview draw a pack the
same way.

    {
      "name": "Rocky", "author": "...", "description": "...",
      "gap": 16, "y": 0,                    eye spacing, eye height (+ = lower)
      "cyclops": false, "outline": false,   one eye in the middle; outlined eyes
      "glint": true, "mouth": "talk",       pupils that look around; mouth: talk | always | never
      "asleep": {eye},                      the shut eyes while sleeping
      "emotions": {
        "happy": {eye, "right": {eye}, "fx": ["blush"], "gaze": [0, 0], "pop": 0.12, "smile": 0},
        ...
      }
    }

An eye is any of: w h (size), rt rb (corner rounding, top / bottom), st sb (edge slant, top / bottom: + tilts the
inner corner in, - the outer), lid (upper lid droop), cheek (lower lid push-up, a happy crescent), dx dy (offset,
+ = outward / down). "right" changes the right eye only (as you look at the screen); without it both eyes match.
Anything left out takes the default. Standard library only (the launcher imports it).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "faces"
CUSTOM_DIR = BUILTIN_DIR / "custom"
DEFAULT_PACK = "rocky"

# Must match firmware/src/face.cpp (kEmotionNames) and config.EMOTIONS.
EMOTIONS = ["neutral", "happy", "sad", "angry", "surprised", "sleepy", "thinking",
            "curious", "skeptical", "excited", "love", "confused"]
FX = ["blush", "tear", "dots", "pop", "steam", "shake", "hearts", "question", "sparkle", "bounce", "wobble"]
MOUTHS = ["talk", "always", "never"]
EYE_KEYS = {  # key: (default, min, max)
    "w": (34, 4, 120), "h": (34, 2, 64), "rt": (12, 0, 32), "rb": (12, 0, 32),
    "st": (0, -30, 30), "sb": (0, -30, 30), "lid": (0, 0, 40), "cheek": (0, 0, 40),
    "dx": (0, -30, 30), "dy": (0, -30, 30),
}
_ID = re.compile(r"^(custom/)?[a-z0-9][a-z0-9-]{0,39}$")


def _num(v, lo, hi, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{name} must be a number")
    v = float(v)
    if not lo <= v <= hi:
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
    return int(v) if v == int(v) else round(v, 2)


def _text(v, n, name):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError(f"{name} must be text")
    v = v.strip()
    if len(v) > n:
        raise ValueError(f"{name} must be at most {n} characters")
    return v


def _eye(d, where):
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise ValueError(f"{where} must be an object")
    return {k: _num(d[k], lo, hi, f"{where}.{k}") for k, (_, lo, hi) in EYE_KEYS.items() if k in d}


def clean(pack) -> dict:
    """The pack, checked: known keys only, numbers in range. Raises ValueError with a message for the dashboard."""
    if not isinstance(pack, dict):
        raise ValueError("a face pack must be an object")
    out = {"name": _text(pack.get("name"), 24, "name") or "Unnamed",
           "author": _text(pack.get("author"), 40, "author"),
           "description": _text(pack.get("description"), 160, "description"),
           "gap": _num(pack.get("gap", 16), 0, 60, "gap"),
           "y": _num(pack.get("y", 0), -20, 20, "y")}
    for k in ("cyclops", "outline", "glint"):
        v = pack.get(k, k == "glint")
        if not isinstance(v, bool):
            raise ValueError(f"{k} must be true or false")
        out[k] = v
    mouth = pack.get("mouth", "talk")
    if mouth not in MOUTHS:
        raise ValueError(f"mouth must be one of: {', '.join(MOUTHS)}")
    out["mouth"] = mouth
    out["asleep"] = _eye(pack.get("asleep"), "asleep")
    emos = pack.get("emotions") or {}
    if not isinstance(emos, dict):
        raise ValueError("emotions must be an object")
    out["emotions"] = {}
    for name, e in emos.items():
        if name not in EMOTIONS:
            raise ValueError(f"unknown emotion: {name}")
        if not isinstance(e, dict):
            raise ValueError(f"{name} must be an object")
        c = _eye(e, name)
        if e.get("right") is not None:
            c["right"] = _eye(e["right"], f"{name}.right")
        fx = e.get("fx") or []
        if not isinstance(fx, list) or any(f not in FX for f in fx):
            raise ValueError(f"{name}.fx: pick from {', '.join(FX)}")
        if fx:
            c["fx"] = list(dict.fromkeys(fx))
        if "gaze" in e:
            g = e["gaze"]
            if not (isinstance(g, list) and len(g) == 2):
                raise ValueError(f"{name}.gaze must be [x, y]")
            c["gaze"] = [_num(g[0], -20, 20, f"{name}.gaze"), _num(g[1], -20, 20, f"{name}.gaze")]
        if "pop" in e:
            c["pop"] = _num(e["pop"], 0, 0.6, f"{name}.pop")
        if "smile" in e:
            c["smile"] = _num(e["smile"], -6, 6, f"{name}.smile")
        out["emotions"][name] = c
    return out


def _path(pack_id: str) -> Path:
    if not _ID.fullmatch(pack_id or ""):
        raise ValueError("a pack id is lowercase letters, digits and dashes")
    return (CUSTOM_DIR / (pack_id[7:] + ".json")) if pack_id.startswith("custom/") else BUILTIN_DIR / (pack_id + ".json")


def load(pack_id: str) -> dict:
    return clean(json.loads(_path(pack_id).read_text()))


def load_or_default(pack_id: str) -> tuple[str, dict]:
    """The pack, or Rocky's own if it's missing or broken (a bad file must not blank his face)."""
    try:
        return pack_id, load(pack_id)
    except (OSError, ValueError) as e:
        if pack_id != DEFAULT_PACK:
            print(f"face pack {pack_id!r}: {e}; using {DEFAULT_PACK}")
        return DEFAULT_PACK, load(DEFAULT_PACK)


def list_packs() -> list[dict]:
    out = []
    for folder, prefix, builtin in ((BUILTIN_DIR, "", True), (CUSTOM_DIR, "custom/", False)):
        for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            pid = prefix + f.stem
            try:
                out.append({"id": pid, "builtin": builtin, "pack": load(pid)})
            except (OSError, ValueError) as e:
                out.append({"id": pid, "builtin": builtin, "error": str(e)})
    order = {"rocky": 0}
    out.sort(key=lambda p: (not p["builtin"], order.get(p["id"], 1), p["id"]))
    return out


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]
    return s or "my-face"


def save_custom(pack: dict, pack_id: str | None = None) -> str:
    """Save to server/faces/custom/. A new pack (no id) gets a fresh id from its name. Returns the id."""
    pack = clean(pack)
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    if pack_id:
        if not pack_id.startswith("custom/"):
            raise ValueError("built-in packs can't be changed: duplicate it first")
        path = _path(pack_id)
    else:
        base = slug(pack["name"])
        n, path = 1, CUSTOM_DIR / f"{base}.json"
        while path.exists():
            n += 1
            path = CUSTOM_DIR / f"{base}-{n}.json"
        pack_id = "custom/" + path.stem
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(pack, indent=2) + "\n")
    tmp.replace(path)
    return pack_id


def delete_custom(pack_id: str) -> None:
    if not (pack_id or "").startswith("custom/"):
        raise ValueError("only your own packs can be deleted")
    _path(pack_id).unlink(missing_ok=True)


def robot_message(pack: dict, save: bool = True) -> dict:
    """The robot's `face` message. It ignores the name-only fields it doesn't draw."""
    p = {k: v for k, v in pack.items() if k not in ("author", "description")}
    return {"type": "face", "save": save, "pack": p}
