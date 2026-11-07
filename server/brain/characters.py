"""Characters: who the robot is.

Rocky is built in (personality.py) and is the default. Characters you make in the dashboard's Personalities tab are
saved in server/characters/ (git-ignored; export one from the dashboard to share it), one JSON file each:

    {
      "name": "Juniper",                     also the wake word: "hey Juniper"
      "description": "...",                  what you asked for, kept so you can redo the draft
      "voice_id": "<32 hex>",                a Fish Audio voice (the ID in the voice's page address)
      "tts_model": "drama-3",                Fish's model: "" = Fish's default (Rocky's), "drama-3" = Drama 3
      "prompt": "...",                       who they are and how they talk ({human} = your name)
      "examples": "[happy] ...",             example replies, one per line, each starting with an emotion tag
      "lines": {"wake", "sleep", "track_on", "track_off"},   said without asking the language model
      "wake_words": ["hey juniper", ...]     what speech-to-text tends to hear for "hey <name>"
    }

The brain reads the chosen one (settings.json, character.id) once at start; switching restarts it. The robot's
rules (short spoken replies, the camera, the head, emotion tags) are added by personality.py, so a character only
says who they are. `draft()` asks the language model to write the long parts from a short description.
Standard library only (the launcher imports it).
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "characters"
DEFAULT = "rocky"
# Fish Audio models a character can use: "" sends no model header, so Fish picks its default (what Rocky uses).
TTS_MODELS = ("", "drama-3")
ACTING_MODELS = ("drama-3",)     # models that perform [directions] and <whisper>...</whisper> instead of reading them
LINE_KEYS = ("wake", "sleep", "track_on", "track_off")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_VOICE = re.compile(r"[0-9a-f]{32}")
_NAME = re.compile(r"^[A-Za-z][A-Za-z' -]{0,22}[A-Za-z]$")
_PHRASE = re.compile(r"^[a-z][a-z' ]{1,38}[a-z]$")


def _text(v, n: int, name: str) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError(f"{name} must be text")
    v = v.strip()
    if len(v) > n:
        raise ValueError(f"{name} must be at most {n} characters")
    return v


def voice_id(v) -> str:
    """A Fish voice ID from the ID itself or the voice's page address ("" if there's none)."""
    m = _VOICE.search(str(v or "").lower())
    return m.group(0) if m else ""


def default_wake_words(name: str) -> list[str]:
    n = name.lower()
    return [f"hey {n}", f"hi {n}", f"a {n}"]


def clean(ch) -> dict:
    """The character, checked. Raises ValueError with a message for the dashboard."""
    if not isinstance(ch, dict):
        raise ValueError("a character must be an object")
    name = _text(ch.get("name"), 24, "name")
    if not _NAME.fullmatch(name):
        raise ValueError("a name is 2 to 24 letters (spaces, dashes and apostrophes are fine)")
    if name.lower() == DEFAULT:
        raise ValueError("Rocky is taken: he's the built-in one")
    vid = voice_id(ch.get("voice_id"))
    if not vid:
        raise ValueError("the voice ID is the 32 letters and digits in the voice's address on fish.audio")
    model = ch.get("tts_model") or ""
    if model not in TTS_MODELS:
        raise ValueError(f"voice model must be one of: {', '.join(m or 'default' for m in TTS_MODELS)}")
    prompt = _text(ch.get("prompt"), 6000, "personality")
    if len(prompt) < 40:
        raise ValueError("the personality needs a few sentences (or press Write it for me)")
    lines_in = ch.get("lines") or {}
    if not isinstance(lines_in, dict):
        raise ValueError("lines must be an object")
    lines = {k: _text(lines_in.get(k), 200, k.replace("_", " ") + " line") for k in LINE_KEYS}
    for k, v in lines.items():
        if not v:
            raise ValueError(f"the {k.replace('_', ' ')} line can't be empty")
    words = ch.get("wake_words") or []
    if isinstance(words, str):
        words = words.splitlines()
    if not isinstance(words, list):
        raise ValueError("wake words must be a list")
    phrases = []
    for w in [*default_wake_words(name)[:1], *words]:
        w = " ".join(str(w).lower().replace(",", " ").replace("-", " ").split())
        if not w:
            continue
        if not _PHRASE.fullmatch(w):
            raise ValueError(f"wake word “{w}”: letters, spaces and apostrophes only, 3 to 40 characters")
        if len(w.split()) < 2:
            raise ValueError(f"wake word “{w}”: use two words or more, like “hey {w}” (one word wakes it by accident)")
        if w not in phrases:
            phrases.append(w)
    if len(phrases) > 16:
        raise ValueError("at most 16 wake words")
    return {
        "name": name,
        "description": _text(ch.get("description"), 1000, "description"),
        "voice_id": vid,
        "tts_model": model,
        "prompt": prompt,
        "examples": _text(ch.get("examples"), 3000, "example replies"),
        "lines": lines,
        "wake_words": phrases,
    }


def _path(cid: str) -> Path:
    if not _ID.fullmatch(cid or "") or cid == DEFAULT:
        raise ValueError("a character id is lowercase letters, digits and dashes")
    return DIR / (cid + ".json")


def load(cid: str) -> dict:
    return clean(json.loads(_path(cid).read_text()))


def active(cid: str) -> tuple[str, dict | None]:
    """(id, character) for the brain; (DEFAULT, None) = Rocky, also when the chosen one is missing or broken."""
    if not cid or cid == DEFAULT:
        return DEFAULT, None
    try:
        return cid, load(cid)
    except (OSError, ValueError) as e:
        print(f"character {cid!r}: {e}; using Rocky")
        return DEFAULT, None


def name_of(cid: str) -> str:
    return (active(cid)[1] or {}).get("name", "Rocky")


def list_all() -> list[dict]:
    out = [{"id": DEFAULT, "builtin": True, "character": {
        "name": "Rocky", "tts_model": "",
        "description": "The Eridian engineer from Project Hail Mary. Built in: his personality is in "
                       "server/brain/personality.py."}}]
    for f in sorted(DIR.glob("*.json")) if DIR.is_dir() else []:
        try:
            out.append({"id": f.stem, "builtin": False, "character": load(f.stem)})
        except (OSError, ValueError) as e:
            out.append({"id": f.stem, "builtin": False, "error": str(e)})
    return out


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]
    return s if s and s != DEFAULT else "character"


def save(ch: dict, cid: str | None = None) -> str:
    """Save to server/characters/. A new one (no id) gets a fresh id from its name. Returns the id."""
    ch = clean(ch)
    DIR.mkdir(parents=True, exist_ok=True)
    if cid:
        path = _path(cid)
    else:
        base = slug(ch["name"])
        n, path = 1, DIR / f"{base}.json"
        while path.exists():
            n += 1
            path = DIR / f"{base}-{n}.json"
        cid = path.stem
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ch, indent=2) + "\n")
    tmp.replace(path)
    return cid


def delete(cid: str) -> None:
    if cid == DEFAULT:
        raise ValueError("Rocky is built in and can't be deleted")
    _path(cid).unlink(missing_ok=True)


# ── Drafting a character from a short description ────────────────────────────────────────────────────────────────

_DRAFT_SYSTEM = """You write characters for a small desk robot. It has an OLED face with animated eyes, a head \
that turns, a camera, and a speaker; a language model plays the character and every word it writes is spoken \
aloud. Given a name and a short description, write the character. Answer with one JSON object and nothing else:

{
  "prompt": "...",
  "examples": ["...", "..."],
  "lines": {"wake": "...", "sleep": "...", "track_on": "...", "track_off": "..."},
  "wake_words": ["...", "..."]
}

prompt: second person ("You are <name>, ..."). Open with one or two sentences on who they are and what they're \
like as a robot living on their human's desk. Then "Who you are:" with 5 to 7 bullet lines ("- ...") on their \
personality, what they care about, their quirks, and how they treat their human. Then "How you talk:" with 5 to \
8 bullet lines on their speaking style: rhythm, vocabulary, signature words and phrases, verbal habits. Make \
the voice specific enough that a single line is unmistakably theirs. They speak in short bursts (a robot's \
replies are one to three short sentences), so nothing about long speeches or rambling. Write {human} wherever the human's name \
goes. Leave out rules about reply length, formatting, the camera, abilities and emotion tags: those are added \
separately. Expand on the description; don't contradict it.

examples: 8 to 10 example replies in the character's voice, each one to three short spoken sentences, each \
starting with exactly one emotion tag in square brackets from this list: EMOTIONS. Use a mix of emotions and \
everyday desk moments, most under 25 words: a greeting, a bug in some code, late-night work, coffee, a question about what they \
see, being asked to dance, a small win, a fact they love.ACTING

lines: short lines said without thinking, in their voice: wake (when someone says "hey <name>" and nothing \
else; one to four words, inviting them to talk), sleep (going to sleep), track_on (starting to follow the \
human's face with their head; may use {human}), track_off (stopping).

wake_words: 4 to 8 lowercase phrases that speech-to-text (Whisper) might write when someone says \
"hey <name>": "hey <name>" itself, "hi <name>", and likely mishearings of the name. Two or more words each, \
letters and spaces only."""

_ACTING = """ Their voice is generated by an expressive speech model that performs stage directions: a short \
direction in square brackets right before the words it changes ([sighs], [small laugh], [whispering], \
[excited, talking fast]) and a few paired tags around words (<whisper>...</whisper>, <emphasis>...</emphasis>, \
<slow>...</slow>, <fast>...</fast>). Use three or four of these across the examples, at most one per reply, \
after the emotion tag (like "[happy] [small laugh] ...")."""


def draft(name: str, description: str, acting: bool, emotions: list[str],
          base_url: str, model: str, api_key: str, timeout: float = 60) -> dict:
    """Ask the language model to write the character. Returns {prompt, examples, lines, wake_words}; raises
    ValueError with a message for the dashboard."""
    name = _text(name, 24, "name")
    if not _NAME.fullmatch(name):
        raise ValueError("give it a name first (2 to 24 letters)")
    description = _text(description, 1000, "description")
    if len(description) < 10:
        raise ValueError("describe the personality in a sentence or two first")
    if not api_key:
        raise ValueError("no LLM_API_KEY in server/.env")
    system = (_DRAFT_SYSTEM.replace("EMOTIONS", ", ".join(emotions))
              .replace("ACTING", _ACTING if acting else ""))
    body = {"model": model, "max_tokens": 3000,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": f"Name: {name}\nDescription: {description}"}]}
    if "openrouter.ai" in base_url:
        body["reasoning"] = {"enabled": False}
    elif "api.anthropic.com" in base_url:
        body["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Title": "desk-robot"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl()) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ValueError(f"the language model said no (HTTP {e.code}): {e.read()[:200].decode('utf-8', 'replace')}")
    except (OSError, ValueError) as e:
        raise ValueError(f"couldn't reach the language model: {e}")
    try:
        text = data["choices"][0]["message"]["content"] or ""
        m = re.search(r"\{.*\}", text, re.S)
        out = json.loads(m.group(0))
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise ValueError("the language model's answer wasn't usable: press Write it for me again")
    examples = out.get("examples") or []
    if isinstance(examples, str):
        examples = examples.splitlines()
    examples = [_face_first(str(x).strip(), emotions) for x in examples if str(x).strip()]
    lines = out.get("lines") if isinstance(out.get("lines"), dict) else {}
    words = [w for w in (out.get("wake_words") or []) if isinstance(w, str)]
    return {
        "prompt": str(out.get("prompt") or "").strip(),
        "examples": "\n".join(examples),
        "lines": {k: str(lines.get(k) or "").strip() for k in LINE_KEYS},
        "wake_words": list(dict.fromkeys([*default_wake_words(name)[:2], *(w.lower().strip() for w in words)])),
    }


def _face_first(line: str, emotions: list[str]) -> str:
    """An example reply that starts with an emotion tag, as the brain needs: "[excited, talking fast] Hah!" becomes
    "[excited] [talking fast] Hah!", and one with no emotion first gets [neutral]."""
    m = re.match(r"\[([^\]]*)\]\s*", line)
    if m and m.group(1).strip().lower() in emotions:
        return line
    if m:
        first, _, rest = m.group(1).partition(",")
        if first.strip().lower() in emotions:
            return f"[{first.strip().lower()}] " + (f"[{rest.strip()}] " if rest.strip() else "") + line[m.end():]
    return "[neutral] " + line


def _ssl():
    try:
        import certifi  # in the brain's venv; Python.org builds on macOS have no system roots
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None


def voice_sample(voice: str, model: str, text: str, api_key: str, timeout: float = 30) -> bytes:
    """A short MP3 of `text` in this voice, for the dashboard's Test voice button."""
    vid = voice_id(voice)
    if not vid:
        raise ValueError("paste the voice ID (or the voice's address on fish.audio) first")
    if model not in TTS_MODELS:
        raise ValueError("unknown voice model")
    if not api_key:
        raise ValueError("no FISH_AUDIO_API_KEY in server/.env")
    text = _text(text, 300, "sample text") or "Hello! This is how I sound."
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if model:
        headers["model"] = model
    body = {"text": text, "reference_id": vid, "format": "mp3"}
    req = urllib.request.Request("https://api.fish.audio/v1/tts", data=json.dumps(body).encode(), method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl()) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        hint = " (check the voice ID)" if e.code in (400, 404) else ""
        raise ValueError(f"Fish Audio said no (HTTP {e.code}){hint}")
    except OSError as e:
        raise ValueError(f"couldn't reach Fish Audio: {e}")
