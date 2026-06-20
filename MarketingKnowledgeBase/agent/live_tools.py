"""Context tools for Reese live chat.

These tools use local synced knowledge and optional Discord.py context passed by
RSAdminBot. They do not call external live market, ticket, or GHL send APIs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from MarketingKnowledgeBase.agent.state import DATA

BASE = Path(__file__).resolve().parents[1]
REPO = BASE.parent
LIVE_CONTEXT = DATA / "live_context.json"
STORY_CANDIDATES = DATA / "story_candidates.json"
APPROVED_EXAMPLES = DATA / "approved_examples.json"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_config() -> Dict[str, Any]:
    return _read_json(BASE / "config.json", {})


def _clean_channel_name(name: str) -> str:
    raw = str(name or "").strip().lower()
    raw = re.sub(r"^[^\w#]+", "", raw)
    raw = raw.replace("┃", "-").replace("|", "-")
    raw = re.sub(r"[^a-z0-9#-]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw


def _iter_config_channels() -> Iterable[Dict[str, str]]:
    cfg = load_config()
    guild_id = str(cfg.get("guild_id") or "")
    for bucket, source in (cfg.get("sources") or {}).items():
        if not isinstance(source, dict):
            continue
        label = str(source.get("label") or bucket)
        for cid in source.get("channel_ids") or []:
            yield {
                "channel_id": str(cid),
                "channel_name": label,
                "bucket": str(bucket),
                "guild_id": guild_id,
                "source": "config.sources",
            }
        for cid in source.get("extra_channel_ids") or []:
            yield {
                "channel_id": str(cid),
                "channel_name": label,
                "bucket": str(bucket),
                "guild_id": guild_id,
                "source": "config.sources.extra",
            }
    publishing = cfg.get("publishing") or {}
    feedback = cfg.get("feedback") or {}
    agent = cfg.get("agent") or {}
    chat = agent.get("chat") or {}
    known = {
        "waitlist": publishing.get("waitlist_channel_id"),
        "review": publishing.get("review_channel_id") or feedback.get("review_channel_id"),
        "captain-hook-live-chat": chat.get("channel_id"),
        "what-you-missed": cfg.get("what_you_missed_channel_id"),
    }
    for name, cid in known.items():
        if cid:
            yield {
                "channel_id": str(cid),
                "channel_name": name,
                "bucket": name,
                "guild_id": guild_id,
                "source": "config.known",
            }


def _iter_live_entries() -> Iterable[Dict[str, Any]]:
    live = _read_json(LIVE_CONTEXT, {})
    for bucket, payload in (live.get("buckets") or {}).items():
        for entry in (payload or {}).get("entries") or []:
            if isinstance(entry, dict):
                row = dict(entry)
                row.setdefault("bucket", bucket)
                yield row


def channel_index() -> Dict[str, Dict[str, str]]:
    by_id: Dict[str, Dict[str, str]] = {}
    for row in _iter_config_channels():
        by_id.setdefault(str(row.get("channel_id")), row)
    for entry in _iter_live_entries():
        cid = str(entry.get("channel_id") or "")
        if not cid:
            continue
        by_id[cid] = {
            "channel_id": cid,
            "channel_name": str(entry.get("channel_name") or by_id.get(cid, {}).get("channel_name") or ""),
            "bucket": str(entry.get("bucket") or by_id.get(cid, {}).get("bucket") or ""),
            "guild_id": str(load_config().get("guild_id") or ""),
            "source": "live_context",
        }
    return by_id


def resolve_discord_references(
    *,
    message_text: str,
    channel_id: int | str,
    reply_message_id: str = "",
    discord_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    text = str(message_text or "")
    refs: List[str] = []
    refs.extend(re.findall(r"<#(\d{15,25})>", text))
    refs.extend(re.findall(r"discord(?:app)?\.com/channels/\d+/(\d{15,25})/\d{15,25}", text))
    for cid in (discord_context or {}).get("mentioned_channel_ids") or []:
        refs.append(str(cid))
    explicit_refs = bool(refs)

    idx = channel_index()
    if not explicit_refs:
        lowered = _clean_channel_name(text)
        for cid, row in idx.items():
            name = _clean_channel_name(row.get("channel_name") or "")
            # Only fuzzy-match channel-like names. Bucket words such as "success"
            # are too broad and can appear in phrases like "no success post."
            if name and "-" in name and name in lowered:
                refs.append(cid)

    seen: set[str] = set()
    channels: List[Dict[str, Any]] = []
    for cid in refs:
        cid = str(cid)
        if not cid or cid in seen:
            continue
        seen.add(cid)
        row = idx.get(cid, {})
        channels.append(
            {
                "channel_id": cid,
                "channel_name": row.get("channel_name") or "",
                "bucket": row.get("bucket") or "",
                "source": row.get("source") or "message_reference",
            }
        )

    return {
        "referenced_channels": channels,
        "reply_message_id": str(reply_message_id or (discord_context or {}).get("reply_message_id") or ""),
        "reference_type": "reply" if reply_message_id else ("mentioned_channel" if channels else "current_channel"),
        "current_channel_id": str(channel_id or ""),
    }


def _normalize_live_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "message_id": str(entry.get("message_id") or ""),
        "channel_id": str(entry.get("channel_id") or ""),
        "channel_name": str(entry.get("channel_name") or ""),
        "bucket": str(entry.get("bucket") or ""),
        "posted_at": str(entry.get("posted_at") or entry.get("created_at") or ""),
        "author": entry.get("author") or {},
        "text": str(entry.get("text") or entry.get("content") or "")[:1800],
        "urls": entry.get("urls") or [],
        "attachments": entry.get("attachments") or [],
        "embed_images": entry.get("embed_images") or [],
        "reactions": entry.get("reactions") or [],
        "message_link": str(entry.get("message_link") or entry.get("jump_url") or ""),
        "source": str(entry.get("source") or "live_context"),
    }


def fetch_recent_channel_messages(
    *,
    channel_id: int | str,
    limit: int = 10,
    discord_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    cid = str(channel_id or "")
    cap = max(1, min(25, int(limit or 10)))
    live_rows = ((discord_context or {}).get("recent_channel_messages") or {}).get(cid)
    if isinstance(live_rows, list) and live_rows:
        return {
            "ok": True,
            "channel_id": cid,
            "source": "discord_gateway",
            "messages": [_normalize_live_entry(row) for row in live_rows[:cap]],
        }
    rows = [_normalize_live_entry(e) for e in _iter_live_entries() if str(e.get("channel_id") or "") == cid]
    rows = sorted(rows, key=lambda r: r.get("posted_at") or "", reverse=True)[:cap]
    return {"ok": True, "channel_id": cid, "source": "live_context_cache", "messages": rows}


def inspect_replied_message(*, discord_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    reply = (discord_context or {}).get("reply_message") or {}
    if not isinstance(reply, dict) or not reply.get("message_id"):
        return {"ok": False, "reason": "No replied-to message was provided."}
    return {"ok": True, "message": _normalize_live_entry(reply)}


def _tokens(query: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9$.\-]+", str(query or "").lower()) if len(t) >= 3]


def search_current_server_context(*, query: str, channel_id: str = "", max_results: int = 8) -> Dict[str, Any]:
    terms = _tokens(query)
    rows: List[Dict[str, Any]] = []
    for entry in _iter_live_entries():
        if channel_id and str(entry.get("channel_id") or "") != str(channel_id):
            continue
        hay = " ".join(
            [
                str(entry.get("text") or ""),
                str(entry.get("channel_name") or ""),
                str(entry.get("bucket") or ""),
            ]
        ).lower()
        score = sum(1 for term in terms if term in hay)
        if score:
            row = _normalize_live_entry(entry)
            row["score"] = score
            rows.append(row)
    rows.sort(key=lambda r: (int(r.get("score") or 0), r.get("posted_at") or ""), reverse=True)
    return {"ok": True, "query": query, "results": rows[: max(1, min(20, int(max_results or 8)))]}


def check_role_channel_access(
    *,
    channel_id: int | str,
    discord_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    channel_info = ((discord_context or {}).get("channel_info") or {}).get(str(channel_id or ""), {})
    overwrites = channel_info.get("permission_overwrites") or channel_info.get("overwrites") or []
    if overwrites:
        return {
            "ok": True,
            "channel_id": str(channel_id or ""),
            "source": "discord_gateway",
            "permission_overwrites": overwrites,
            "note": "Raw permission overwrites are available; exact role explanation can be expanded after role mapping is synced.",
        }
    row = channel_index().get(str(channel_id or ""), {})
    return {
        "ok": False,
        "channel_id": str(channel_id or ""),
        "channel_name": row.get("channel_name") or "",
        "bucket": row.get("bucket") or "",
        "reason": "Live permission overwrites are not wired into Reese yet, so I will not guess who can see it.",
    }


def search_ticket_context(*, query: str = "") -> Dict[str, Any]:
    return {
        "ready": False,
        "query": query,
        "reason": "Ticket/cancellation data sources are reserved for the future setup but are not wired yet.",
    }


def search_ghl_sms_docs(*, query: str, max_results: int = 5) -> Dict[str, Any]:
    sources = [
        REPO / "telnyx_discord_sms_bridge" / "GHL_WIRING_README.md",
        REPO / "telnyx_discord_sms_bridge" / "README.md",
        REPO / "telnyx_discord_sms_bridge" / "BRIDGE_ARCHITECTURE.md",
    ]
    terms = _tokens(query)
    results: List[Dict[str, Any]] = []
    for path in sources:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        chunks = [c.strip() for c in re.split(r"\n(?=## |\# )", text) if c.strip()]
        for chunk in chunks:
            hay = chunk.lower()
            score = sum(1 for term in terms if term in hay)
            if score or "ghl" in hay or "sms" in hay:
                results.append({"path": str(path.relative_to(REPO)), "score": score, "excerpt": chunk[:1200]})
    results.sort(key=lambda r: int(r.get("score") or 0), reverse=True)
    return {"ok": True, "query": query, "results": results[: max(1, min(12, int(max_results or 5)))]}


def pull_market_context(*, source_messages: List[Dict[str, Any]] | None = None, query: str = "") -> Dict[str, Any]:
    text = "\n".join(str(m.get("text") or "") for m in (source_messages or []))
    amounts = list(dict.fromkeys(re.findall(r"\$\s?\d[\d,]*(?:\.\d{2})?(?:\+)?", text)))
    market_lines = []
    for line in text.splitlines():
        low = line.lower()
        if any(word in low for word in ("market", "resale", "ebay", "profit", "going for", "retail", "msrp")):
            market_lines.append(line.strip())
    return {
        "ok": True,
        "query": query,
        "amounts": amounts[:12],
        "market_lines": market_lines[:8],
        "verified_live_market": False,
        "note": "No live market provider is configured; use only source-message market clues.",
    }


def pick_primary_source_message(
    *,
    replied_message: Dict[str, Any] | None = None,
    recent_messages: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    if replied_message and replied_message.get("message_id"):
        return replied_message
    for row in recent_messages or []:
        if str(row.get("text") or "").strip() or row.get("attachments") or row.get("embed_images"):
            return row
    return {}


def answer_server_setup_question(*, query: str) -> Dict[str, Any]:
    cfg = load_config()
    agent = cfg.get("agent") or {}
    chat = agent.get("chat") or {}
    publishing = cfg.get("publishing") or {}
    feedback = cfg.get("feedback") or {}
    return {
        "ok": True,
        "query": query,
        "facts": {
            "guild_id": cfg.get("guild_id"),
            "captain_hook_chat_channel_id": chat.get("channel_id"),
            "captain_hook_chat_enabled": chat.get("enabled", True),
            "review_channel_id": publishing.get("review_channel_id") or feedback.get("review_channel_id"),
            "review_approval_role_id": publishing.get("review_approval_role_id") or feedback.get("approval_role_id"),
            "chat_response_transport": "webhook via config.secrets.json",
            "gateway_bridge": "RSAdminBot on_message",
        },
    }
