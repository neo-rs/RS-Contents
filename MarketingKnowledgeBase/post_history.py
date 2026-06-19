"""Durable story usage history for marketing posts and tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Set

_BASE = Path(__file__).resolve().parent
_DATA = _BASE / "data"


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


def _source_message_id(draft: Dict[str, Any]) -> str:
    link = str(draft.get("source_message_link") or "").rstrip("/")
    if "/" in link:
        return link.rsplit("/", 1)[-1]
    return str(draft.get("source_message_id") or draft.get("message_id") or "")


def load_post_history() -> Dict[str, Any]:
    return _read_json(_DATA / "post_history.json") or {"version": 1, "items": []}


def used_story_ids(*, include_review_posts: bool = True, limit: int = 300) -> Set[str]:
    out: Set[str] = set()
    history = load_post_history()
    for row in (history.get("items") or [])[:limit]:
        if not isinstance(row, dict):
            continue
        for key in ("story_id", "source_message_id"):
            val = str(row.get(key) or "").strip()
            if val:
                out.add(val)

    if include_review_posts:
        review_posts = _read_json(_DATA / "review_posts.json") or {}
        for row in (review_posts.get("posts") or [])[:limit]:
            if not isinstance(row, dict):
                continue
            for key in ("story_id", "source_message_id"):
                val = str(row.get(key) or "").strip()
                if val:
                    out.add(val)
    return out


def record_story_usage(
    *,
    draft: Dict[str, Any],
    mode: str,
    channel_id: int = 0,
    posted: Dict[str, Any] | None = None,
    audit_log: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Record that a story was used, even for dry-run tests."""

    doc = load_post_history()
    items = list(doc.get("items") or [])
    row = {
        "story_id": str(draft.get("story_id") or ""),
        "source_message_id": _source_message_id(draft),
        "source_bucket": str(draft.get("source_bucket") or ""),
        "archive_source": str(draft.get("archive_source") or ""),
        "mode": str(mode or "unknown"),
        "channel_id": str(channel_id or ""),
        "posted_message_id": str((posted or {}).get("message_id") or ""),
        "posted_url": (posted or {}).get("url"),
        "audit_logged": bool((audit_log or {}).get("posted")),
        "model": ((draft.get("model_routing") or {}).get("model") or ""),
        "used_at": _now(),
    }
    key = (row["story_id"], row["source_message_id"], row["mode"], row["posted_message_id"])
    by_key = {
        (
            str(item.get("story_id") or ""),
            str(item.get("source_message_id") or ""),
            str(item.get("mode") or ""),
            str(item.get("posted_message_id") or ""),
        ): item
        for item in items
        if isinstance(item, dict)
    }
    by_key[key] = row
    doc["items"] = sorted(by_key.values(), key=lambda x: str(x.get("used_at") or ""), reverse=True)[:300]
    doc["updated_at"] = _now()
    _write_json(_DATA / "post_history.json", doc)
    return row
