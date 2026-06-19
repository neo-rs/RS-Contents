"""Persist review feedback and approved style examples from Discord reactions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from MarketingKnowledgeBase.discord_api import (
    extract_attachments,
    extract_embed_images,
    extract_message_text,
    fetch_channel_messages,
    fetch_guild_member,
    fetch_reaction_users,
)

_BASE = Path(__file__).resolve().parent


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


def data_path(name: str) -> Path:
    return _BASE / "data" / name


def load_review_posts() -> Dict[str, Any]:
    return _read_json(data_path("review_posts.json")) or {"version": 1, "posts": []}


def record_review_post(*, posted: Dict[str, Any], draft: Dict[str, Any], slot: str, channel_id: int) -> None:
    """Store metadata linking a review-channel message back to its source story."""
    message_id = str(posted.get("message_id") or "")
    if not message_id:
        return
    doc = load_review_posts()
    posts: List[Dict[str, Any]] = list(doc.get("posts") or [])
    row = {
        "message_id": message_id,
        "channel_id": str(channel_id),
        "slot": slot,
        "story_id": str(draft.get("story_id") or ""),
        "source_message_id": str((draft.get("source_message_link") or "").rstrip("/").split("/")[-1]),
        "source_bucket": str(draft.get("source_bucket") or ""),
        "posted_at": _now(),
        "url": posted.get("url"),
    }
    by_id = {str(item.get("message_id")): item for item in posts if item.get("message_id")}
    by_id[message_id] = row
    doc["posts"] = sorted(by_id.values(), key=lambda x: str(x.get("posted_at") or ""), reverse=True)[:100]
    doc["updated_at"] = _now()
    _write_json(data_path("review_posts.json"), doc)


def _member_has_role(*, guild_id: int, user_id: str, role_id: str, headers: dict) -> bool:
    try:
        member = fetch_guild_member(guild_id, user_id, headers)
    except Exception:
        return False
    return role_id in {str(role) for role in (member.get("roles") or [])}


def _reaction_user_ids(channel_id: int, message_id: str, emoji: str, headers: dict) -> List[str]:
    try:
        users = fetch_reaction_users(channel_id, message_id, emoji, headers)
    except Exception:
        return []
    return [str(user.get("id") or "") for user in users if user.get("id")]


def sync_review_feedback(cfg: Dict[str, Any], headers: dict) -> Dict[str, Any]:
    fb = cfg.get("feedback") or {}
    if not fb.get("enabled", True):
        return {"enabled": False}
    channel_id = int(fb.get("review_channel_id") or 0)
    guild_id = int(fb.get("review_guild_id") or 0)
    role_id = str(fb.get("approval_role_id") or "")
    if channel_id <= 0 or guild_id <= 0 or not role_id:
        return {"enabled": True, "skipped": "missing_review_config"}

    review_posts = load_review_posts()
    by_message = {str(post.get("message_id")): post for post in (review_posts.get("posts") or [])}
    messages = fetch_channel_messages(channel_id, headers, limit=int(fb.get("max_review_messages") or 50))
    approve_emoji = str(fb.get("approve_emoji") or "✅")
    reject_emoji = str(fb.get("reject_emoji") or "❌")
    rows: List[Dict[str, Any]] = []

    for msg in messages:
        mid = str(msg.get("id") or "")
        if not mid:
            continue
        approved_by = [
            uid
            for uid in _reaction_user_ids(channel_id, mid, approve_emoji, headers)
            if _member_has_role(guild_id=guild_id, user_id=uid, role_id=role_id, headers=headers)
        ]
        rejected_by = [
            uid
            for uid in _reaction_user_ids(channel_id, mid, reject_emoji, headers)
            if _member_has_role(guild_id=guild_id, user_id=uid, role_id=role_id, headers=headers)
        ]
        if not approved_by and not rejected_by:
            continue
        meta = by_message.get(mid) or {}
        status = "approved" if approved_by and not rejected_by else "rejected" if rejected_by and not approved_by else "mixed"
        rows.append(
            {
                "message_id": mid,
                "channel_id": str(channel_id),
                "status": status,
                "approved_by": approved_by,
                "rejected_by": rejected_by,
                "story_id": meta.get("story_id") or "",
                "source_message_id": meta.get("source_message_id") or "",
                "source_bucket": meta.get("source_bucket") or "",
                "review_url": f"https://discord.com/channels/{guild_id}/{channel_id}/{mid}",
                "synced_at": _now(),
            }
        )

    doc = {"version": 1, "updated_at": _now(), "feedback": rows}
    _write_json(data_path("review_feedback.json"), doc)
    return {"enabled": True, "review_feedback": len(rows)}


def sync_approved_examples(cfg: Dict[str, Any], headers: dict) -> Dict[str, Any]:
    fb = cfg.get("feedback") or {}
    channel_id = int(fb.get("good_examples_channel_id") or cfg.get("what_you_missed_channel_id") or 0)
    guild_id = int((cfg.get("publishing") or {}).get("production_guild_id") or cfg.get("guild_id") or 0)
    allowed = {str(uid) for uid in (fb.get("good_example_reactor_user_ids") or [])}
    if channel_id <= 0 or guild_id <= 0 or not allowed:
        return {"approved_examples": 0, "skipped": "missing_examples_config"}

    approve_emoji = str(fb.get("approve_emoji") or "✅")
    scan_limit = int(fb.get("max_example_scan_messages") or 35)
    messages = fetch_channel_messages(channel_id, headers, limit=scan_limit)
    examples: List[Dict[str, Any]] = []
    for msg in messages:
        mid = str(msg.get("id") or "")
        has_approve_reaction = any(
            str((reaction.get("emoji") or {}).get("name") or "") == approve_emoji
            for reaction in (msg.get("reactions") or [])
            if isinstance(reaction, dict)
        )
        if not has_approve_reaction:
            continue
        reactors = set(_reaction_user_ids(channel_id, mid, approve_emoji, headers))
        approved_by = sorted(reactors & allowed)
        if not approved_by:
            continue
        text = extract_message_text(msg)
        if not text:
            continue
        examples.append(
            {
                "message_id": mid,
                "channel_id": str(channel_id),
                "approved_by": approved_by,
                "posted_at": msg.get("timestamp"),
                "text": text[:1800],
                "attachments": extract_attachments(msg),
                "embed_images": extract_embed_images(msg),
                "message_link": f"https://discord.com/channels/{guild_id}/{channel_id}/{mid}",
            }
        )

    max_examples = int(fb.get("max_examples") or 12)
    doc = {"version": 1, "updated_at": _now(), "examples": examples[:max_examples]}
    _write_json(data_path("approved_examples.json"), doc)
    return {"approved_examples": len(doc["examples"])}


def load_feedback_index() -> Dict[str, Dict[str, Any]]:
    doc = _read_json(data_path("review_feedback.json")) or {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in doc.get("feedback") or []:
        for key in (row.get("story_id"), row.get("source_message_id")):
            if key:
                out[str(key)] = row
    return out


def load_approved_examples(limit: int = 5) -> List[Dict[str, Any]]:
    doc = _read_json(data_path("approved_examples.json")) or {}
    return list(doc.get("examples") or [])[:limit]
