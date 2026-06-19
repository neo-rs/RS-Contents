"""Compact marketing memory built from feedback and synced candidates."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

_BASE = Path(__file__).resolve().parent
_DATA = _BASE / "data"
_URL_RE = re.compile(r"https?://\S+", re.I)
_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]{2,}", re.I)
_STOP = {
    "the",
    "and",
    "for",
    "you",
    "with",
    "that",
    "this",
    "from",
    "was",
    "are",
    "our",
    "your",
    "into",
    "just",
    "have",
    "has",
    "been",
    "get",
    "got",
    "can",
    "not",
    "all",
    "out",
    "but",
    "now",
    "you've",
    "youre",
    "congratulations",
    "awarded",
    "point",
    "points",
    "sharing",
    "current",
    "entry",
    "entries",
    "winner",
    "winners",
    "success",
    "link",
    "links",
    "minute",
    "reminder",
    "absolute",
    "also",
    "blue",
    "siren",
    "rsfire",
    "reminders",
    "here",
    "check",
    "keep",
    "starting",
    "tomorrow",
    "today",
    "morning",
    "local",
    "posted",
    "member",
    "free",
    "price",
    "prices",
    "buy",
    "these",
    "those",
    "going",
    "retail",
    "market",
    "only",
    "each",
    "available",
    "chance",
    "still",
    "will",
}
_NOISY_LINE_RE = re.compile(
    r"(<a?:[^>]+>|<[#@&]?\d+>|@everyone|daily reminder|info (?:will be )?provided|congratulations <@|current points|awarded \d+ point)",
    re.I,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _clean_text(text: str, limit: int = 500) -> str:
    text = _URL_RE.sub("[link omitted]", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line and not _NOISY_LINE_RE.search(line):
            cleaned = _clean_text(line, 180).strip(" #*_")
            if len(cleaned) >= 12:
                return cleaned
    return ""


def _keywords(texts: Iterable[str], limit: int = 18) -> List[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        cleaned = _URL_RE.sub(" ", str(text or "").lower())
        for word in _WORD_RE.findall(cleaned):
            if word in _STOP or len(word) < 3 or word.isdigit():
                continue
            counts[word] += 1
    return [word for word, _ in counts.most_common(limit)]


def _load_candidates(data_dir: Path) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for path in [
        *sorted((data_dir / "daily").glob("*/story_candidates.json") if (data_dir / "daily").exists() else []),
        *sorted((data_dir / "weekly").glob("*/story_candidates.json") if (data_dir / "weekly").exists() else []),
    ]:
        doc = _read_json(path)
        if isinstance(doc, dict):
            docs.extend([row for row in (doc.get("candidates") or []) if isinstance(row, dict)])
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in docs:
        sid = str(row.get("story_id") or row.get("message_id") or "")
        if sid:
            prev = by_id.get(sid)
            if not prev or int(row.get("score") or 0) > int(prev.get("score") or 0):
                by_id[sid] = row
    return list(by_id.values())


def build_marketing_memory(data_dir: Path | None = None) -> Dict[str, Any]:
    """Build small persistent memory from approved/rejected posts and candidate themes."""

    data_dir = data_dir or _DATA
    review_feedback = _read_json(data_dir / "review_feedback.json") or {}
    approved_examples = _read_json(data_dir / "approved_examples.json") or {}
    review_posts = _read_json(data_dir / "review_posts.json") or {}
    candidates = _load_candidates(data_dir)

    feedback_by_story = {
        str(row.get("story_id") or row.get("source_message_id") or ""): row
        for row in (review_feedback.get("feedback") or [])
        if isinstance(row, dict)
    }
    rejected_story_ids = {
        sid
        for sid, row in feedback_by_story.items()
        if str(row.get("status") or "") in {"rejected", "mixed"}
    }
    approved_story_ids = {
        sid for sid, row in feedback_by_story.items() if str(row.get("status") or "") == "approved"
    }

    approved_texts = [
        str(row.get("text") or "")
        for row in (approved_examples.get("examples") or [])
        if isinstance(row, dict) and str(row.get("text") or "").strip()
    ]
    candidate_texts = [str(row.get("text") or "") for row in candidates if not (row.get("deal_facts") or {}).get("has_filler")]
    fact_theme_texts: List[str] = []

    bucket_counts: defaultdict[str, int] = defaultdict(int)
    channel_counts: defaultdict[str, int] = defaultdict(int)
    money_examples: List[str] = []
    winning_hooks: List[str] = []
    for row in candidates:
        bucket = str(row.get("bucket") or "")
        if bucket:
            bucket_counts[bucket] += 1
        channel = str(row.get("channel_name") or "")
        if channel:
            channel_counts[channel] += 1
        if len(winning_hooks) < 10:
            hook = _first_line(str(row.get("text") or ""))
            if hook:
                winning_hooks.append(hook)
        facts = row.get("deal_facts") or {}
        for key in ("title", "store_or_platform", "urgency_hints"):
            val = facts.get(key)
            if isinstance(val, list):
                fact_theme_texts.extend(str(x) for x in val)
            elif val:
                fact_theme_texts.append(str(val))
        amounts = row.get("dollar_amounts") or _MONEY_RE.findall(str(row.get("text") or ""))
        facts = row.get("deal_facts") or {}
        price_context = []
        for key in ("price", "retail_or_msrp", "market_value"):
            if facts.get(key):
                price_context.append(f"{key}={facts.get(key)}")
        if (amounts or price_context) and len(money_examples) < 12:
            label = facts.get("title") or bucket or "story"
            money_examples.append(f"{str(label)[:80]}: {', '.join(price_context or [str(x) for x in amounts[:4]])}")

    approved_patterns = []
    if approved_texts:
        approved_patterns.append("Approved examples favor direct hooks, short proof, and a waitlist CTA.")
        approved_patterns.extend(_clean_text(text, 220) for text in approved_texts[:4])
    else:
        approved_patterns.append("No approved examples synced yet; stay grounded, specific, and concise.")

    rejected_phrases = [
        "do not invent profit, sell-through, urgency, or member wins",
        "avoid generic hype if the source only contains a simple deal",
        "do not include raw URLs or monitor jargon",
    ]
    if rejected_story_ids:
        rejected_phrases.append("avoid patterns from rejected/mixed review posts")

    return {
        "version": 1,
        "updated_at": _now(),
        "source_counts": {
            "candidates": len(candidates),
            "review_posts": len(review_posts.get("posts") or []),
            "approved_examples": len(approved_texts),
            "feedback_rows": len(review_feedback.get("feedback") or []),
            "approved_story_ids": len(approved_story_ids),
            "rejected_or_mixed_story_ids": len(rejected_story_ids),
        },
        "approved_patterns": approved_patterns[:6],
        "rejected_phrases": rejected_phrases,
        "winning_hooks": winning_hooks[:8],
        "recurring_deal_themes": _keywords([*fact_theme_texts, *candidate_texts], limit=16),
        "money_proof_examples": money_examples[:8],
        "bucket_memory": dict(sorted(bucket_counts.items(), key=lambda item: item[1], reverse=True)[:10]),
        "active_channels": dict(sorted(channel_counts.items(), key=lambda item: item[1], reverse=True)[:12]),
    }


def refresh_marketing_memory(data_dir: Path | None = None) -> Dict[str, Any]:
    data_dir = data_dir or _DATA
    memory = build_marketing_memory(data_dir)
    _write_json(data_dir / "marketing_memory.json", memory)
    return memory


def load_marketing_memory(data_dir: Path | None = None, *, auto_refresh: bool = True) -> Dict[str, Any]:
    data_dir = data_dir or _DATA
    path = data_dir / "marketing_memory.json"
    doc = _read_json(path)
    if isinstance(doc, dict) and doc.get("version"):
        return doc
    if auto_refresh:
        return refresh_marketing_memory(data_dir)
    return {}


def memory_prompt(memory: Dict[str, Any], *, max_chars: int = 1800) -> str:
    if not memory:
        return ""
    lines = ["MARKETING MEMORY (compact, reusable; do not treat as new facts for the selected deal):"]
    for key, label in [
        ("approved_patterns", "Approved patterns"),
        ("rejected_phrases", "Avoid/reject patterns"),
        ("recurring_deal_themes", "Recurring themes"),
        ("money_proof_examples", "Money proof examples"),
        ("winning_hooks", "Recent hook shapes"),
    ]:
        vals = memory.get(key) or []
        if vals:
            lines.append(f"{label}:")
            lines.extend(f"- {_clean_text(str(val), 240)}" for val in vals[:6])
    out = "\n".join(lines)
    return out[:max_chars]
