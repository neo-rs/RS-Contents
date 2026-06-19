"""Canonical Discord channel references for marketing copy."""

from __future__ import annotations

import re
from typing import Optional

# Raw display names like "❗┃deals-important" must not appear in published copy.
_RAW_CHANNEL_NAME_RE = re.compile(r"[❗🚀⭐🤑🛠️┃|]+\s*[\w-]+", re.UNICODE)


def channel_mention(channel_id: Optional[int | str]) -> str:
    try:
        cid = int(channel_id or 0)
    except (TypeError, ValueError):
        return "our deal alerts"
    if cid <= 0:
        return "our deal alerts"
    return f"<#{cid}>"


def sanitize_channel_display_names(text: str, *, channel_id: Optional[int | str] = None) -> str:
    """Replace raw channel display names with <#id> or generic phrasing."""
    out = str(text or "")
    if "┃" in out or "❗" in out:
        mention = channel_mention(channel_id) if channel_id else "our deal alerts"
        out = re.sub(r"[#*\s]*[❗🚀⭐🤑🛠️]+\s*┃\s*[\w-]+", mention, out)
        out = re.sub(r"❗┃[\w-]+", mention, out)
    return out
