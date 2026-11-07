"""Thinking: sends a question (and the newest camera frame) to the language
model and hands back what Rocky should say and feel, one sentence at a time
as the model writes it.

The request is the OpenAI-style chat API, which OpenRouter, Anthropic and
OpenAI all serve, so the provider is a base URL and the model is a string
(config.LLM_BASE_URL / config.MODEL; the key is LLM_API_KEY in server/.env).

Rocky has two real abilities the model can call (tool use): `look` moves
the head and comes back with a fresh camera frame from the new angle, and
`track_face` starts/stops following the human. main.py supplies the
functions that actually do those things, plus more tools that change while
running (the computer abilities in computer.py, offered as `extra_tools()`).
Abilities in `quick` that succeed after he has said his line end the reply
there: no second call to the model just to say "done".
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from collections.abc import Callable, Iterator

import openai

from . import config
from . import personality

_EMOTION_TAG = re.compile(r"^\s*\[(\w+)\]\s*", re.S)
# A sentence ends at . ! or ? followed by a space — but not at an ellipsis
# ("just... clear answer." is one sentence, not two) or a title like "Mr."
# ("play Mr. Blue Sky" is one sentence too).
_SENTENCE_END = re.compile(r"(?<=[!?])\s+|(?<=[^.]\.)(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)"
                           r"(?<!\bJr\.)(?<!\bSr\.)(?<!\bvs\.)\s+", re.IGNORECASE)

# The model thinking out loud about its instructions instead of talking to its human ("The question is 'what is
# this?' and the detector note says a thumbs down, but the question is about seeing. I should look to get a
# picture."). The prompt says not to; a sentence like this is dropped before it's spoken, as a backstop.
_REASONING = re.compile(r"\b(?:detectors?|the note|note says|the question is|question is about|is an? (?:\w+ )?question about|"
                        r"knowledge question|the user|"
                        r"my instructions|the instructions say|system prompt|"
                        r"i should (?:look|use|call|check|get|describe|answer))\b", re.IGNORECASE)


def _thinking_aloud(sentence: str) -> bool:
    if _REASONING.search(sentence):
        print(f"  (not said, thinking out loud: {sentence[:120]})")
        return True
    return False


def _without_thinking(text: str) -> str:
    """The model's text with those sentences taken out, for his memory: so he doesn't pick the habit up from it."""
    return " ".join(p for p in _SENTENCE_END.split(text) if p.strip() and not _REASONING.search(p)).strip()


# An action returns (text for the model, optional fresh camera JPEG), or (text, JPEG, done): done = nothing more to
# say about it (a head turn with no picture), so the reply ends there once he has said his line, like a quick action.
Action = Callable[[dict], tuple]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "look",
            "description": (
                "Move your head. Use it whenever you are asked to turn, move or point your "
                "head, to look left/right/down/up/around, or to look at, check or find something. "
                "Never use it on your own to answer a question: when your human mentions a thing "
                "(\"this capacitor\", \"which way does this LED go\") and asks how it works or how to "
                "use it, answer from what you know without looking. Always call it when asked, even if you think "
                "you are already there: the result tells you where your head really "
                "is and whether it is at a limit. With see true you get a fresh camera image "
                "afterwards; describe only what that image shows. With see false there is "
                "no picture: just say where your head went, nothing about the room. Your neck turns up "
                f"to {config.TRACK_PAN_LIMIT:.0f} degrees left or right and tilts up to "
                f"{config.TRACK_TILT_MAX:.0f} degrees up or {-config.TRACK_TILT_MIN:.0f} down."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["left", "right", "up", "down", "level", "center"],
                        "description": (
                            "left/right turn the head only (no nod); up/down/level nod only (no turn); "
                            "center = straight ahead and level"
                        ),
                    },
                    "degrees": {
                        "type": "number",
                        "description": (
                            f"How far: 5-{config.TRACK_PAN_LIMIT:.0f} for left/right, "
                            f"5-{max(config.TRACK_TILT_MAX, -config.TRACK_TILT_MIN):.0f} for up/down. "
                            "Omit for a normal look."
                        ),
                    },
                    "see": {
                        "type": "boolean",
                        "description": (
                            "true when your human wants to know what is there (look at the door, check "
                            "something, what is over there). false when they only tell you to move, turn "
                            "or point your head (turn left, look up, head down) and ask nothing about "
                            "what is there."
                        ),
                    },
                },
                "required": ["direction", "see"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dance",
            "description": (
                "Dance with your head for about 10 seconds: one of your four routines, picked at random. Use it "
                "when your human asks you to dance, groove, vibe to music or show your moves. Say one short excited "
                "line first (or nothing); the dance starts when you finish it. Don't describe the dance."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_face",
            "description": "Start or stop following the human's face with your head.",
            "parameters": {
                "type": "object",
                "properties": {"on": {"type": "boolean"}},
                "required": ["on"],
            },
        },
    },
]


class Interrupted(Exception):
    """The human started talking again; drop this reply."""


def _image_part(jpeg: bytes) -> dict:
    data = base64.standard_b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


class RobotBrain:
    def __init__(self, actions: dict[str, Action] | None = None,
                 extra_tools: Callable[[], list[dict]] | None = None, quick: set[str] | None = None) -> None:
        self.client = openai.OpenAI(
            base_url=config.LLM_BASE_URL,
            api_key=os.environ.get("LLM_API_KEY", "missing"),
            default_headers={"X-Title": "desk-robot"},  # shows up in OpenRouter's usage page; others ignore it
        )
        # The model's hidden reasoning, off unless config.LLM_REASONING. Each provider has its own switch for it
        # (OpenRouter's "reasoning", Anthropic's "thinking"); other providers get nothing extra.
        if config.LLM_REASONING:
            self._extra = {}
        elif "openrouter.ai" in config.LLM_BASE_URL:
            self._extra = {"reasoning": {"enabled": False}}
        elif "api.anthropic.com" in config.LLM_BASE_URL:
            self._extra = {"thinking": {"type": "disabled"}}
        else:
            self._extra = {}
        self.history: list[dict] = []
        self.actions = actions or {}
        self.extra_tools = extra_tools or (lambda: [])
        self.quick = quick or set()
        self.emotion = "neutral"        # emotion of the reply in progress
        self._inflight: tuple[int, dict] | None = None  # (index, user message) being answered

    def reply(
        self,
        question: str,
        jpeg: bytes | None = None,
        on_emotion: Callable[[str], None] | None = None,
        cancelled: threading.Event | None = None,
        camera_wanted: bool = False,
        notes: str = "",
        passive: bool = False,
    ) -> Iterator[str]:
        """Rocky's reply, one sentence at a time as the model writes it.

        on_emotion(name) is called as soon as the emotion tag at the start of
        the reply is known — before the first sentence — so the face can
        change while he's still composing. Set `cancelled`, or close the
        generator early, and the question is dropped from his memory as if it
        was never asked. `camera_wanted` with no `jpeg` means the question
        was about seeing but the camera had no fresh picture; he's told so,
        otherwise he answers from memory and claims to see things. `notes` is perception's line about what the
        camera's detectors notice; `passive` = the picture came along for context, not because he was asked to look.
        """
        content: list[dict] = [{"type": "text", "text": question}]
        if notes:
            content.append({"type": "text", "text": notes})
        if jpeg is not None and passive:
            content.append({"type": "text", "text": "(Live picture from your camera, taken just now, for context. Use it if it helps; don't describe it unless the question is about it.)"})
            content.append(_image_part(jpeg))
        elif jpeg is not None:
            content.append({"type": "text", "text": "(Live picture from your camera, taken just now, because the question seems to be about what you can see. If it isn't, ignore the picture and don't mention it.)"})
            content.append(_image_part(jpeg))
        elif camera_wanted:
            content.append({"type": "text", "text": "(Your camera has no fresh picture right now, so you cannot see anything at the moment.)"})
        user_msg = {"role": "user", "content": content}
        self.history.append(user_msg)
        mark = len(self.history) - 1
        self._inflight = (mark, user_msg)
        self.emotion = "neutral"
        spoken: list[str] = []
        error: tuple[str, str] | None = None

        gen = self._converse(on_emotion, cancelled or threading.Event())
        try:
            for sentence in gen:
                spoken.append(sentence)
                yield sentence
        except (Interrupted, GeneratorExit):
            gen.close()
            self._forget(mark, user_msg)
            raise
        except openai.APIConnectionError:
            error = ("Brain cannot reach internet. Bad bad bad.", "sad")
        except openai.AuthenticationError:
            error = ("Brain has no key. Set LLM_API_KEY, human.", "sad")
        except openai.APIStatusError as e:
            error = (f"Ow. Brain hurts. API error {e.status_code}.", "sad")
        finally:
            gen.close()

        if error is not None:
            self._forget(mark, user_msg)
            self.emotion = error[1]
            if on_emotion is not None:
                on_emotion(self.emotion)
            yield error[0]
            return

        if spoken:
            # Remember what was actually said, so he can't refer back to a
            # part that got cut.
            self.history.append({"role": "assistant", "content": f"[{self.emotion}] {' '.join(spoken)}"})
        self._inflight = None
        self._strip_images()
        self._trim_history()

    def abandon(self) -> None:
        """Forget the question being answered right now (the human kept talking)."""
        if self._inflight is not None:
            self._forget(*self._inflight)

    def _forget(self, mark: int, user_msg: dict) -> None:
        # Only cut if that question is still where we left it: a newer one may
        # already have taken its place.
        if mark < len(self.history) and self.history[mark] is user_msg:
            del self.history[mark:]
        self._inflight = None

    def _converse(self, on_emotion: Callable[[str], None] | None, cancelled: threading.Event) -> Iterator[str]:
        """One question, possibly several model calls if it uses its abilities.
        Yields sentences as they complete."""
        nudged = False
        limit = config.REPLY_MAX_SENTENCES
        said = 0                 # sentences spoken so far in this reply, across rounds
        for _ in range(5):
            if cancelled.is_set():
                raise Interrupted()
            stream = self.client.chat.completions.create(
                model=config.MODEL,
                max_tokens=200,  # backstop; the sentence limit does the real work
                messages=[{"role": "system", "content": personality.SYSTEM_PROMPT}, *self.history],
                tools=[*TOOLS, *self.extra_tools()] if self.actions else openai.NOT_GIVEN,
                stream=True,
                extra_body=self._extra,
            )
            buf = ""                 # text not yet released as a sentence
            raw: list[str] = []      # everything the model wrote this round
            tag_decided = False
            calls: dict[int, dict] = {}
            spoken = 0
            capped = False           # said all he may; still listen for an ability he asks for after it
            try:
                for chunk in stream:
                    if cancelled.is_set():
                        raise Interrupted()
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    for tc in delta.tool_calls or []:
                        entry = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function is not None:
                            if tc.function.name:
                                entry["name"] = tc.function.name
                            if tc.function.arguments:
                                entry["arguments"] += tc.function.arguments
                    if not delta.content or capped:
                        continue
                    raw.append(delta.content)
                    buf += delta.content
                    if not tag_decided:
                        buf, tag_decided = self._take_emotion_tag(buf, final=False)
                        if not tag_decided:
                            continue
                        if on_emotion is not None:
                            on_emotion(self.emotion)
                    parts = _SENTENCE_END.split(buf)
                    while len(parts) > 1:  # everything but the last piece is a whole sentence
                        s = parts.pop(0).strip()
                        if s and not _thinking_aloud(s):
                            spoken += 1
                            said += 1
                            yield s
                        if spoken >= limit:
                            break
                    buf = parts[-1] if parts else ""
                    if spoken >= limit:
                        print(f"  (trimmed reply to {limit} sentences)")
                        buf = ""
                        capped = True
            finally:
                stream.close()

            if not tag_decided:
                buf, _ = self._take_emotion_tag(buf, final=True)
                if on_emotion is not None and (buf.strip() or not calls):
                    on_emotion(self.emotion)

            if calls:
                # He decided to do something: say any lead-in, run it, tell him what happened.
                if buf.strip() and not _thinking_aloud(buf.strip()):
                    said += 1
                    yield buf.strip()
                    buf = ""
                written = "".join(raw)
                self.history.append({
                    "role": "assistant",
                    "content": _without_thinking(written) or (f"[{self.emotion}]" if written.strip() else ""),
                    "tool_calls": [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": c["arguments"]}}
                        for _, c in sorted(calls.items())
                    ],
                })
                fresh: bytes | None = None
                all_quick = True
                for _, c in sorted(calls.items()):
                    if cancelled.is_set():
                        raise Interrupted()
                    try:
                        args = json.loads(c["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    action = self.actions.get(c["name"])
                    ok = done = False
                    if action is None:
                        text = f"unknown ability {c['name']}"
                    else:
                        try:
                            result = action(args)
                            text, img = result[0], result[1]
                            done = len(result) > 2 and bool(result[2])
                            ok = True
                        except Exception as e:  # the robot didn't cooperate; say so
                            text, img = f"could not do that: {e}", None
                        if img:
                            fresh = img
                    all_quick = all_quick and ok and (c["name"] in self.quick or done)
                    print(f"  [{c['name']} {args} -> {text}]")
                    self.history.append({"role": "tool", "tool_call_id": c["id"], "content": text})
                if fresh is not None:
                    self.history.append({
                        "role": "user",
                        "content": [{"type": "text", "text": "Camera view after moving:"}, _image_part(fresh)],
                    })
                if all_quick and said:
                    return   # done, and he already said so
                continue

            if not "".join(raw).strip() and not nudged:
                # Some models go quiet right after using an ability. Ask once.
                nudged = True
                self.history.append({"role": "user", "content": "(Tell your human what you just did, in one short line.)"})
                continue
            if nudged and self.history and self.history[-1].get("role") == "user":
                self.history.pop()  # don't keep the nudge in the transcript
            tail = buf.strip()
            if tail and _thinking_aloud(tail):
                tail = ""
            if tail and spoken < limit:
                yield tail
            elif not spoken and not tail:
                self.emotion = "thinking"
                if on_emotion is not None:
                    on_emotion(self.emotion)
                yield "Hmm. Words did not come. Ask again."
            return

        self.emotion = "thinking"
        if on_emotion is not None:
            on_emotion(self.emotion)
        yield "Too many things at once. Ask again, human."

    def _take_emotion_tag(self, buf: str, final: bool) -> tuple[str, bool]:
        """Look for "[happy] " at the start of the reply. Returns (text with the
        tag removed, decided). Not decided means: need more text to know."""
        lead = buf.lstrip()
        if not lead:
            return buf, final
        if not lead.startswith("["):
            return buf, True
        if "]" in lead:
            m = _EMOTION_TAG.match(lead)
            if m:
                candidate = m.group(1).lower()
                if candidate in config.EMOTIONS:
                    self.emotion = candidate
                    return _EMOTION_TAG.sub("", lead, count=1), True
                if not config.TTS_ACTING:
                    return _EMOTION_TAG.sub("", lead, count=1), True
            if config.TTS_ACTING:
                return self._take_acting_lead(lead, final)
            return buf, True
        if final or len(lead) > 24:
            return buf, True  # a "[" with no "]" in sight: not a tag
        return buf, False

    def _take_acting_lead(self, lead: str, final: bool) -> tuple[str, bool]:
        """A voice that acts (config.TTS_ACTING): the model sometimes puts a direction where the emotion tag goes.
        "[excited, talking fast] Hah!" -> excited + "[talking fast] Hah!"; "[sighs] [sad] Oh." -> sad + "[sighs] Oh."
        Anything else stays as written, for the voice to perform."""
        m = re.match(r"\[([^\[\]]*)\]\s*", lead)
        if not m:
            return lead, True
        first, _, rest = m.group(1).partition(",")
        if first.strip().lower() in config.EMOTIONS:
            self.emotion = first.strip().lower()
            return (f"[{rest.strip()}] " if rest.strip() else "") + lead[m.end():], True
        after = lead[m.end():]
        if not after or (after.startswith("[") and "]" not in after):
            return lead, final or len(lead) > 80      # the emotion tag may be next: wait for it
        m2 = _EMOTION_TAG.match(after)
        if m2 and m2.group(1).lower() in config.EMOTIONS:
            self.emotion = m2.group(1).lower()
            return lead[:m.end()] + after[m2.end():], True
        return lead, True

    def _strip_images(self) -> None:
        """Replace old camera frames with a note. Keeping every image in the
        history would make each later question cost far more."""
        for m in self.history:
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                parts = [p for p in m["content"] if p.get("type") == "text"]
                if len(parts) != len(m["content"]):
                    parts.append({"type": "text", "text": "(a camera image was attached here)"})
                m["content"] = parts

    def _trim_history(self) -> None:
        max_msgs = config.MAX_HISTORY_TURNS * 2
        if len(self.history) > max_msgs:
            cut = len(self.history) - max_msgs
            # Never start the history on a tool result: back up to a user turn.
            while cut < len(self.history) and self.history[cut].get("role") != "user":
                cut += 1
            del self.history[:cut]
