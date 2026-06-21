"""Command parsing for live Discord review/chat feedback."""

from __future__ import annotations

import re
from typing import Any, Dict

COMMANDS = {
    "revise",
    "remember",
    "explain",
    "image",
    "approve",
    "publish",
    "reject",
    "regenerate",
    "status",
    "undo",
}

CORRECTION_RE = re.compile(
    r"\b(too|make it|remove|change|fix|less|more|dont|don't|never|rewrite|revise|tone|hype|proof|cta|claim)\b",
    re.I,
)


def parse_review_message(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    lowered = raw.lower()
    for command in COMMANDS:
        if lowered == command:
            return {"command": command, "argument": "", "natural": False}
        prefix = f"{command}:"
        if lowered.startswith(prefix):
            return {"command": command, "argument": raw[len(prefix) :].strip(), "natural": False}
    if CORRECTION_RE.search(raw):
        return {"command": "revise", "argument": raw, "natural": True}
    return {"command": "unknown", "argument": raw, "natural": True}

