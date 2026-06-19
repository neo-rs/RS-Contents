"""Small related-story retrieval for grounded marketing copy."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from MarketingKnowledgeBase.channel_display import channel_mention

_BASE = Path(__file__).resolve().parent
_DATA = _BASE / "data"
_URL_RE = re.compile(r"https?://\S+", re.I)
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]{3,}", re.I)
_STOP = {
    "this",
    "that",
    "with",
    "from",
    "have",
    "your",
    "just",
    "they",
    "were",
    "will",
    "been",
    "into",
    "deal",
    "deals",
    "members",
    "price",
}


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_ts(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        out = datetime.fromisoformat(text)
        return out if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _clean(text: str, limit: int = 420) -> str:
    text = _URL_RE.sub("[link omitted]", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _keywords(text: str) -> set[str]:
    words = set()
    for word in _WORD_RE.findall(str(text or "").lower()):
        if word not in _STOP:
            words.add(word)
    return words


def _iter_entries(doc: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for bucket, payload in (doc.get("buckets") or {}).items():
        if not isinstance(payload, dict):
            continue
        for entry in payload.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            row = dict(entry)
            row.setdefault("bucket", bucket)
            yield row


def _candidate_docs(data_dir: Path) -> List[Tuple[str, Dict[str, Any]]]:
    paths = [
        *sorted((data_dir / "daily").glob("*/live_context.json") if (data_dir / "daily").exists() else []),
        *sorted((data_dir / "weekly").glob("*/live_context.json") if (data_dir / "weekly").exists() else []),
    ]
    docs: List[Tuple[str, Dict[str, Any]]] = []
    for path in paths:
        doc = _read_json(path)
        if isinstance(doc, dict):
            docs.append((str(path), doc))
    return docs


def build_research_pack(
    candidate: Dict[str, Any],
    *,
    data_dir: Path | None = None,
    max_related: int = 6,
) -> Dict[str, Any]:
    data_dir = data_dir or _DATA
    target_mid = str(candidate.get("message_id") or "")
    target_bucket = str(candidate.get("bucket") or "")
    target_channel = str(candidate.get("channel_id") or "")
    target_ts = _parse_ts(candidate.get("posted_at"))
    target_words = _keywords(str(candidate.get("text") or ""))

    scored: Dict[str, Dict[str, Any]] = {}
    source_paths: set[str] = set()
    for source_path, doc in _candidate_docs(data_dir):
        for row in _iter_entries(doc):
            mid = str(row.get("message_id") or "")
            if not mid or mid == target_mid:
                continue
            score = 0
            if str(row.get("bucket") or "") == target_bucket:
                score += 4
            if target_channel and str(row.get("channel_id") or "") == target_channel:
                score += 4
            overlap = target_words & _keywords(str(row.get("text") or ""))
            score += min(len(overlap), 5)
            row_ts = _parse_ts(row.get("posted_at"))
            if target_ts != datetime.min.replace(tzinfo=timezone.utc) and row_ts != datetime.min.replace(tzinfo=timezone.utc):
                age_hours = abs((target_ts - row_ts).total_seconds()) / 3600
                if age_hours <= 6:
                    score += 4
                elif age_hours <= 24:
                    score += 2
                elif age_hours <= 72:
                    score += 1
            if row.get("attachments") or row.get("embed_images"):
                score += 1
            if score < 5:
                continue
            prev = scored.get(mid)
            if not prev or score > int(prev.get("_research_score") or 0):
                scored[mid] = {**row, "_research_score": score, "_overlap": sorted(overlap)[:8]}
                source_paths.add(source_path)

    related = sorted(
        scored.values(),
        key=lambda row: (int(row.get("_research_score") or 0), str(row.get("posted_at") or "")),
        reverse=True,
    )[:max_related]
    return {
        "version": 1,
        "source_paths": sorted(source_paths)[:12],
        "primary_story_id": candidate.get("story_id"),
        "related_count": len(related),
        "related": related,
    }


def research_prompt(pack: Dict[str, Any], *, max_chars: int = 2200) -> str:
    related = pack.get("related") or []
    if not related:
        return "RELATED CONTEXT PACK: no strong related messages found; use only the primary story."
    lines = [
        "RELATED CONTEXT PACK (retrieved, limited; use only if it supports the primary story):",
    ]
    for row in related[:6]:
        ch_id = row.get("channel_id")
        ch_ref = channel_mention(ch_id) if ch_id else str(row.get("channel_name") or "unknown channel")
        lines.append(
            "\n".join(
                [
                    f"- score={row.get('_research_score')} bucket={row.get('bucket')} channel={ch_ref} posted_at={row.get('posted_at')}",
                    f"  overlap={', '.join(row.get('_overlap') or [])}",
                    f"  text={_clean(str(row.get('text') or ''))}",
                ]
            )
        )
    out = "\n".join(lines)
    return out[:max_chars]
