"""Canonical RS marketing writing rules — prompt builder + post-processor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_BASE = Path(__file__).resolve().parent
_RULES_PATH = _BASE / "writing_rules.json"

_BARE_URL_RE = re.compile(r"(?<![(<])(https?://[^\s\)>]+)")
_PLACEHOLDER_URL_RE = re.compile(
    r"https?://(?:example\.com|your-link-here|link\.here|placeholder|localhost)[^\s\)]*",
    re.I,
)
_UNICODE_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "]+",
    flags=re.UNICODE,
)
_RS_EMOJI_RE = re.compile(r"<(?:a)?:\w+:\d+>")


def load_writing_rules() -> Dict[str, Any]:
    if not _RULES_PATH.exists():
        return {}
    with open(_RULES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def build_rules_prompt(*, context: str = "dm_sequence", waitlist_channel_id: Optional[int] = None) -> str:
    rules = load_writing_rules()
    lines: List[str] = [
        "STRICT RS WRITING RULES (violations = regenerate):",
        f"Tone: {rules.get('tone', 'Casual RS insider')}",
        "",
        "FORMATTING:",
    ]
    for item in rules.get("formatting_rules") or []:
        lines.append(f"- {item}")
    lines.extend(["", "LINKS:"])
    for item in rules.get("link_rules") or []:
        lines.append(f"- {item}")

    mavely = rules.get("mavely_affiliate") or {}
    if mavely:
        lines.extend(
            [
                "",
                "MAVELY AFFILIATE:",
                f"- {mavely.get('note', '')}",
                f"- Reformatter reference channel: <#{mavely.get('reformatter_channel_id', '')}> ({mavely.get('reformatter_channel_name', '')})",
                f"- Skip domains (no Mavely rewrite): {', '.join(mavely.get('skip_affiliate_domains') or [])}",
            ]
        )

    lines.extend(["", "RS EMOJIS (prefer these over unicode):"])
    rs = rules.get("rs_emojis") or {}
    for name, token in rs.items():
        lines.append(f"- {name}: {token}")
    for item in rules.get("emoji_rules") or []:
        lines.append(f"- {item}")

    lines.extend(["", "GROUNDING:"])
    for item in rules.get("grounding_rules") or []:
        lines.append(f"- {item}")

    lines.extend(["", "NEVER USE THESE PHRASES:"])
    lines.append(", ".join(rules.get("avoid_phrases") or []))

    lines.extend(["", "NEVER USE THESE CHARACTERS:"])
    lines.append(" ".join(repr(c) for c in (rules.get("avoid_characters") or ["—", "–"])))

    lines.extend(["", "CHANNEL REFERENCES:"])
    for item in rules.get("channel_reference_rules") or []:
        lines.append(f"- {item}")
    for item in rules.get("post_style_rules") or []:
        lines.append(f"- {item}")

    hints = (rules.get("context_hints") or {}).get(context)
    if hints:
        lines.extend(["", f"CONTEXT ({context}):", hints])

    if context in ("marketing_copy", "what_you_missed"):
        wym = rules.get("what_you_missed_rules") or []
        if wym:
            lines.extend(["", "WHAT-YOU-MISSED (mandatory):"])
            for item in wym:
                lines.append(f"- {item}")

    if waitlist_channel_id and context in ("marketing_copy", "what_you_missed"):
        mention = f"<#{int(waitlist_channel_id)}>"
        lines.extend(
            [
                "",
                "WAITLIST CTA (required):",
                f"- Every post must include {mention} in the closing CTA.",
                "- Vary the CTA wording every time; do not reuse one fixed sentence across posts.",
                "- Match the CTA to the story angle: missed deal, member win, proof image, local play, or time-sensitive drop.",
            ]
        )
        examples = (rules.get("waitlist_cta") or {}).get("examples") or []
        if examples:
            lines.append("- Example CTA shapes, do not copy exactly:")
            for example in examples[:5]:
                lines.append(f"  - {str(example).replace('<#waitlist_channel_id>', mention)}")

    return "\n".join(lines)


def _strip_all_urls(text: str) -> str:
    """Remove every URL and markdown link from marketing copy."""
    out = str(text or "")
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)
    out = re.sub(r"<https?://[^>]+>", "", out)
    out = _BARE_URL_RE.sub("", out)
    out = re.sub(r"https?://\S+", "", out)
    return out


def _strip_bot_jargon(text: str) -> str:
    patterns = [
        r"check the full size run here[:\s]*",
        r"https?://zephr\.app\S*",
        r"\bFSR\b",
        r"\bquicktask\b",
        r"\bATC\b",
    ]
    out = text
    for pat in patterns:
        out = re.sub(pat, "", out, flags=re.I)
    return out


def _wrap_bare_urls(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        url = match.group(1).rstrip(".,;")
        trailing = match.group(1)[len(url) :]
        return f"<{url}>{trailing}"

    return _BARE_URL_RE.sub(repl, text)


def _strip_placeholder_urls(text: str) -> str:
    return _PLACEHOLDER_URL_RE.sub("", text)


def _replace_dashes(text: str) -> str:
    rules = load_writing_rules()
    for ch in rules.get("avoid_characters") or ["—", "–"]:
        text = text.replace(ch, "-")
    return text


def _trim_unicode_emojis(text: str, max_unicode: int = 1) -> str:
    found = list(_UNICODE_EMOJI_RE.finditer(text))
    if len(found) <= max_unicode:
        return text
    # Keep first N unicode emojis, remove the rest
    to_remove = found[max_unicode:]
    out = text
    for match in reversed(to_remove):
        out = out[: match.start()] + out[match.end() :]
    return out


def _scrub_avoid_phrases(text: str) -> str:
    rules = load_writing_rules()
    out = text
    for phrase in rules.get("avoid_phrases") or []:
        if not phrase:
            continue
        pattern = re.compile(re.escape(phrase), re.I)
        out = pattern.sub("", out)
    return re.sub(r"  +", " ", out)


def _strip_markdown_headings(text: str) -> str:
    return re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)


_RS_ANIMATED_EMOJI_RE = re.compile(r"<a:\w+:\d+>")


def _trim_animated_rs_emojis(text: str, max_animated: int = 1) -> str:
    found = list(_RS_ANIMATED_EMOJI_RE.finditer(text))
    if len(found) <= max_animated:
        return text
    out = text
    for match in reversed(found[max_animated:]):
        out = out[: match.start()] + out[match.end() :]
    return re.sub(r"  +", " ", out)


def apply_writing_rules_postprocess(text: str, *, context: str = "dm_sequence") -> str:
    """Deterministic cleanup after model output."""
    out = str(text or "").strip()
    out = _replace_dashes(out)
    out = _strip_placeholder_urls(out)
    if context in ("marketing_copy", "what_you_missed"):
        out = _strip_all_urls(out)
        out = _strip_bot_jargon(out)
        out = _strip_markdown_headings(out)
    else:
        out = _wrap_bare_urls(out)
    out = _trim_animated_rs_emojis(out, max_animated=1)
    out = _trim_unicode_emojis(out, max_unicode=1 if context == "dm_sequence" else 2)
    out = _scrub_avoid_phrases(out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"  +", " ", out)
    return out.strip()


def validate_output(text: str, *, context: str = "dm_sequence") -> List[str]:
    """Return list of rule violations (empty = ok)."""
    issues: List[str] = []
    rules = load_writing_rules()
    for ch in rules.get("avoid_characters") or ["—", "–"]:
        if ch in text:
            issues.append(f"contains forbidden dash {ch!r}")
    for phrase in rules.get("avoid_phrases") or []:
        if phrase and phrase.lower() in text.lower():
            issues.append(f"contains avoid phrase: {phrase!r}")
    if _PLACEHOLDER_URL_RE.search(text):
        issues.append("contains placeholder URL")
    if context == "dm_sequence" and "{join_url}" not in text:
        issues.append("missing {join_url} placeholder")
    naked = _BARE_URL_RE.findall(text)
    if naked and context == "dm_sequence":
        issues.append(f"DM has bare merchant URLs (use {{join_url}} only): {naked[:2]}")
    if context in ("marketing_copy", "what_you_missed"):
        if _BARE_URL_RE.search(text) or re.search(r"https?://", text):
            issues.append("what-you-missed must not contain any URLs")
    return issues
