"""Decisions: quick yes/no answers about a camera picture, from OpenAI's Decisions API
(https://developers.openai.com/api/docs/guides/decisions).

The Decisions API takes a picture (or text) and a list of questions and answers each with a number instead of
words: a predicate gets the probability that it's true, a choice gets one of its labels. It's fast (0.3-0.8 s for a
camera frame) and cheap (a 640x480 frame is about 640 input tokens, $0.10 per million, and output is free), so it can
look every few seconds. Focus mode (focus.py) asks it whether you're on your phone.

The key is OPENAI_API_KEY in server/.env, separate from LLM_API_KEY (the language model he talks with). Without it
nothing here runs and focus mode isn't offered. The SDK has no method for this endpoint yet, so it's a plain POST
through the client (its timeout and error types still apply).
"""

from __future__ import annotations

import base64
import os

import openai

from . import config

_client: openai.OpenAI | None = None


def available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "missing"), base_url=config.DECISIONS_BASE_URL,
                                timeout=config.DECISIONS_TIMEOUT, max_retries=0)   # a late answer is a stale one
    return _client


def predicate(name: str, instructions: str) -> dict:
    return {"type": "predicate", "name": name, "instructions": instructions}


def ask(questions: list[dict], jpeg: bytes | None = None, text: str = "") -> dict[str, dict]:
    """Ask about a picture and/or some text. Blocking (run it in an executor). Returns {question name: answer}, an
    answer being the API's own: {"type": "predicate", "probability": 0.93}, or {"type": "refusal", ...} for one it
    wouldn't answer. Raises the openai errors (no key, no internet, a bad request)."""
    content: list[dict] = []
    if text:
        content.append({"type": "input_text", "text": text})
    if jpeg:
        content.append({"type": "input_image",
                        "image_url": "data:image/jpeg;base64," + base64.standard_b64encode(jpeg).decode()})
    body = {"model": config.DECISIONS_MODEL, "input": [{"role": "user", "content": content}], "questions": questions}
    reply = _get_client().post("/decisions", body=body, cast_to=object)
    return named_answers(reply)


def named_answers(reply: object) -> dict[str, dict]:
    """The reply's answers by question name (they come back in the order asked, each with its name)."""
    answers = reply.get("answers") if isinstance(reply, dict) else None
    return {a["name"]: a for a in answers or [] if isinstance(a, dict) and isinstance(a.get("name"), str)}


def probability(answers: dict[str, dict], name: str) -> float | None:
    """A predicate's probability, or None if it was refused or missing."""
    a = answers.get(name)
    p = a.get("probability") if a and a.get("type") == "predicate" else None
    return float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) else None
