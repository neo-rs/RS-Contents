"""Context tools for Reese live chat.

These tools use local synced knowledge and optional Discord.py context passed by
ReeseBot or RSAdminBot. They do not call external live market, ticket, or GHL send APIs.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from MarketingKnowledgeBase.agent.state import DATA
from MarketingKnowledgeBase.story_candidates import build_story_candidates

BASE = Path(__file__).resolve().parents[1]
REPO = BASE.parent
LIVE_CONTEXT = DATA / "live_context.json"
STORY_CANDIDATES = DATA / "story_candidates.json"
APPROVED_EXAMPLES = DATA / "approved_examples.json"
CHAT_STATE = DATA / "agent_chat_sessions.json"
TOKEN_USAGE = DATA / "agent_token_usage.json"
AGENT_MEMORY = DATA / "agent_memory.json"
POST_HISTORY = DATA / "post_history.json"
REVIEW_POSTS = DATA / "review_posts.json"
AGENT_RUNS = DATA / "agent_runs"
MESSAGE_LINK_RE = re.compile(
    r"(?:https?://)?(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/(\d{15,25})/(\d{15,25})/(\d{15,25})"
)


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
        "reese-live-chat": chat.get("channel_id"),
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


def live_context_status() -> Dict[str, Any]:
    live = _read_json(LIVE_CONTEXT, {})
    buckets = live.get("buckets") if isinstance(live, dict) else {}
    counts: Dict[str, int] = {}
    if isinstance(buckets, dict):
        for bucket, payload in buckets.items():
            counts[str(bucket)] = len((payload or {}).get("entries") or [])
    return {
        "path": str(LIVE_CONTEXT),
        "exists": LIVE_CONTEXT.exists(),
        "last_sync_at": str(live.get("last_sync_at") or "") if isinstance(live, dict) else "",
        "bucket_counts": counts,
    }


def _parse_ts(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _image_url_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value if value.startswith("http") else ""
    if not isinstance(value, dict):
        return ""
    url = str(value.get("url") or value.get("proxy_url") or "").strip()
    if not url.startswith("http"):
        return ""
    content_type = str(value.get("content_type") or "").lower()
    filename = str(value.get("filename") or "").lower()
    if content_type.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return url
    return url if not content_type else ""


def _extract_image_urls(row: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    for key in ("attachments", "embed_images", "images"):
        values = row.get(key) or []
        if isinstance(values, dict):
            values = [values]
        for value in values if isinstance(values, list) else []:
            url = _image_url_from_value(value)
            if url and url not in urls:
                urls.append(url)
    image_url = str(row.get("image_url") or row.get("primary_image_url") or "").strip()
    if image_url.startswith("http") and image_url not in urls:
        urls.insert(0, image_url)
    return urls[:6]


def _message_links(text: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for match in MESSAGE_LINK_RE.finditer(text or ""):
        rows.append({"guild_id": match.group(1), "channel_id": match.group(2), "message_id": match.group(3)})
    return rows


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
    message_links = _message_links(text)
    refs.extend(row["channel_id"] for row in message_links)
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
        "referenced_messages": message_links,
        "reply_message_id": str(reply_message_id or (discord_context or {}).get("reply_message_id") or ""),
        "reference_type": "message_link" if message_links else ("reply" if reply_message_id else ("mentioned_channel" if channels else "current_channel")),
        "current_channel_id": str(channel_id or ""),
    }


def _normalize_live_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    image_urls = _extract_image_urls(entry)
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
        "image_urls": image_urls,
        "primary_image_url": image_urls[0] if image_urls else "",
        "reactions": entry.get("reactions") or [],
        "message_link": str(entry.get("message_link") or entry.get("jump_url") or ""),
        "source": str(entry.get("source") or "live_context"),
    }


def _latest_child_with_file(root: Path, filename: str) -> Path | None:
    if not root.exists():
        return None
    candidates = [child / filename for child in root.iterdir() if child.is_dir() and (child / filename).exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _archive_candidate_paths() -> List[Tuple[str, Path]]:
    paths: List[Tuple[str, Path]] = [
        ("root_live_context", LIVE_CONTEXT),
        ("root_story_candidates", STORY_CANDIDATES),
    ]
    daily_live = _latest_child_with_file(DATA / "daily", "live_context.json")
    daily_candidates = _latest_child_with_file(DATA / "daily", "story_candidates.json")
    weekly_live = _latest_child_with_file(DATA / "weekly", "live_context.json")
    weekly_candidates = _latest_child_with_file(DATA / "weekly", "story_candidates.json")
    for label, path in (
        ("latest_daily_live_context", daily_live),
        ("latest_daily_story_candidates", daily_candidates),
        ("current_weekly_live_context", weekly_live),
        ("current_weekly_story_candidates", weekly_candidates),
    ):
        if path:
            paths.append((label, path))
    return paths


def _candidate_from_story(item: Dict[str, Any], *, source_label: str, source_path: Path) -> Dict[str, Any]:
    image_urls = _extract_image_urls(item)
    return {
        "story_id": str(item.get("story_id") or f"{item.get('bucket')}:{item.get('message_id')}"),
        "message_id": str(item.get("message_id") or ""),
        "bucket": str(item.get("bucket") or ""),
        "channel_id": str(item.get("channel_id") or ""),
        "channel_name": str(item.get("channel_name") or ""),
        "posted_at": str(item.get("posted_at") or ""),
        "text": str(item.get("text") or "")[:1800],
        "message_link": str(item.get("message_link") or ""),
        "score": int(item.get("score") or 0),
        "deal_facts": item.get("deal_facts") or {},
        "headline_hints": item.get("headline_hints") or [],
        "dollar_amounts": item.get("dollar_amounts") or [],
        "attachments": item.get("attachments") or [],
        "embed_images": item.get("embed_images") or [],
        "image_urls": image_urls,
        "primary_image_url": image_urls[0] if image_urls else "",
        "source_label": source_label,
        "source_path": str(source_path),
    }


def _candidates_from_doc(doc: Dict[str, Any], *, source_label: str, source_path: Path) -> List[Dict[str, Any]]:
    if isinstance(doc.get("candidates"), list):
        return [_candidate_from_story(item, source_label=source_label, source_path=source_path) for item in doc.get("candidates") or [] if isinstance(item, dict)]
    if isinstance(doc.get("buckets"), dict):
        built = build_story_candidates(doc)
        return [
            _candidate_from_story(item, source_label=source_label, source_path=source_path)
            for item in built.get("candidates") or []
            if isinstance(item, dict)
        ]
    return []


def search_archive_content(*, query: str = "", max_results: int = 6) -> Dict[str, Any]:
    """Search current live data plus latest daily/weekly story archives."""

    terms = _tokens(query)
    specific_terms = _archive_query_terms(query)
    excluded = _excluded_terms(query)
    rows: List[Dict[str, Any]] = []
    sources_checked: List[Dict[str, Any]] = []
    latest_sync = ""
    for label, path in _archive_candidate_paths():
        exists = path.exists()
        source_row = {"label": label, "path": str(path), "exists": exists}
        if not exists:
            sources_checked.append(source_row)
            continue
        doc = _read_json(path, {})
        if isinstance(doc, dict):
            source_row["last_sync_at"] = str(doc.get("last_sync_at") or doc.get("source_last_sync_at") or doc.get("generated_at") or "")
            latest_sync = max(latest_sync, str(source_row["last_sync_at"] or ""))
            candidates = _candidates_from_doc(doc, source_label=label, source_path=path)
            source_row["candidates"] = len(candidates)
            rows.extend(candidates)
        sources_checked.append(source_row)

    by_message: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        mid = str(row.get("message_id") or row.get("story_id") or "")
        if not mid:
            continue
        hay = " ".join(
            [
                str(row.get("text") or ""),
                str(row.get("channel_name") or ""),
                str(row.get("bucket") or ""),
                json.dumps(row.get("deal_facts") or {}, ensure_ascii=False),
            ]
        ).lower()
        query_matches = [term for term in specific_terms if term in hay]
        query_boost = sum(3 for term in terms if term in hay) + (12 * len(query_matches))
        excluded_matches = [term for term in excluded if term in hay]
        exclude_penalty = 100 if excluded_matches else 0
        image_boost = 4 if row.get("primary_image_url") else 0
        admin_boost = 3 if row.get("bucket") in {"important", "important_instore", "important_trading_cards", "full_send_info"} else 0
        recency_boost = max(0, 6 - int((datetime.now(timezone.utc) - _parse_ts(row.get("posted_at"))).total_seconds() // 43200)) if row.get("posted_at") else 0
        row["excluded_by_query"] = excluded_matches
        row["query_matches"] = query_matches
        row["archive_rank_score"] = int(row.get("score") or 0) + query_boost + image_boost + admin_boost + recency_boost - exclude_penalty
        prev = by_message.get(mid)
        if not prev or int(row.get("archive_rank_score") or 0) > int(prev.get("archive_rank_score") or 0):
            by_message[mid] = row

    ranked = sorted(
        by_message.values(),
        key=lambda row: (
            bool(row.get("query_matches")) if specific_terms else True,
            not bool(row.get("excluded_by_query")),
            int(row.get("archive_rank_score") or 0),
            _parse_ts(row.get("posted_at")),
        ),
        reverse=True,
    )
    cap = max(1, min(12, int(max_results or 6)))
    return {
        "ok": True,
        "query": query,
        "latest_sync_at": latest_sync,
        "sources_checked": sources_checked,
        "specific_terms": specific_terms,
        "excluded_terms": excluded,
        "matched_candidate_count": sum(1 for row in ranked if row.get("query_matches")),
        "candidates": ranked[:cap],
        "note": "Use primary_image_url from the selected candidate when drafting visual content.",
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


def inspect_linked_message(
    *,
    message_text: str = "",
    references: Dict[str, Any] | None = None,
    discord_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    links = list((references or {}).get("referenced_messages") or []) or _message_links(message_text)
    if not links:
        return {"ok": False, "reason": "No Discord message link was provided."}
    linked = (discord_context or {}).get("linked_messages") or {}
    for link in links:
        mid = str(link.get("message_id") or "")
        if mid and isinstance(linked.get(mid), dict):
            return {"ok": True, "message": _normalize_live_entry(linked[mid]), "source": "discord_gateway"}
    wanted = {str(link.get("message_id") or "") for link in links}
    for entry in _iter_live_entries():
        if str(entry.get("message_id") or "") in wanted:
            return {"ok": True, "message": _normalize_live_entry(entry), "source": "live_context_cache"}
    return {"ok": False, "reason": "Message link was parsed, but the exact message was not available in gateway context or live_context cache.", "links": links}


def _tokens(query: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9$.\-]+", str(query or "").lower()) if len(t) >= 3]


def _excluded_terms(query: str) -> List[str]:
    terms: List[str] = []
    text = str(query or "").lower()
    for pattern in (
        r"\bno more\s+([a-z0-9][a-z0-9\-]{2,})",
        r"\bnot\s+([a-z0-9][a-z0-9\-]{2,})",
        r"\bexclude\s+([a-z0-9][a-z0-9\-]{2,})",
        r"\bwithout\s+([a-z0-9][a-z0-9\-]{2,})",
    ):
        terms.extend(re.findall(pattern, text))
    return list(dict.fromkeys(terms))


def _archive_query_terms(query: str) -> List[str]:
    stop = {
        "any",
        "are",
        "content",
        "deal",
        "deals",
        "from",
        "give",
        "good",
        "have",
        "lead",
        "leads",
        "live",
        "more",
        "other",
        "post",
        "posts",
        "posting",
        "search",
        "there",
        "this",
        "yeah",
        "context",
        "we",
        "what",
        "whats",
        "with",
        "worthy",
        "worth",
        "your",
    }
    return [term for term in _tokens(query) if term not in stop and not term.startswith("$")]


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


def _json_count(path: Path, key: str) -> int:
    doc = _read_json(path, {})
    if not isinstance(doc, dict):
        return 0
    val = doc.get(key)
    return len(val) if isinstance(val, list) else 0


def content_record_status() -> Dict[str, Any]:
    chat_doc = _read_json(CHAT_STATE, {})
    channels = (chat_doc.get("channels") or {}) if isinstance(chat_doc, dict) else {}
    chat_messages = 0
    for row in channels.values():
        if isinstance(row, dict):
            chat_messages += len(row.get("messages") or [])
    run_count = len(list(AGENT_RUNS.glob("*.json"))) if AGENT_RUNS.exists() else 0
    return {
        "short_term_chat_memory": {
            "path": str(CHAT_STATE),
            "exists": CHAT_STATE.exists(),
            "channel_count": len(channels),
            "message_count": chat_messages,
            "purpose": "Recent live-chat memory used for ongoing channel conversation.",
        },
        "review_runs_and_drafts": {
            "path": str(AGENT_RUNS),
            "exists": AGENT_RUNS.exists(),
            "run_file_count": run_count,
            "purpose": "Generated review runs, drafts, feedback, validation, and publish attempts.",
        },
        "post_history": {
            "path": str(POST_HISTORY),
            "exists": POST_HISTORY.exists(),
            "item_count": _json_count(POST_HISTORY, "items"),
            "purpose": "Tracks stories/posts already used to reduce repeats.",
        },
        "review_posts": {
            "path": str(REVIEW_POSTS),
            "exists": REVIEW_POSTS.exists(),
            "post_count": _json_count(REVIEW_POSTS, "posts"),
            "purpose": "Tracks review messages posted for approval/feedback.",
        },
        "durable_memory_rules": {
            "path": str(AGENT_MEMORY),
            "exists": AGENT_MEMORY.exists(),
            "purpose": "Stores durable rules from remember commands and do-not-claim rules.",
        },
        "token_usage_log": {
            "path": str(TOKEN_USAGE),
            "exists": TOKEN_USAGE.exists(),
            "entry_count": _json_count(TOKEN_USAGE, "entries"),
            "purpose": "Tracks live-chat model/token usage estimates.",
        },
        "limitation": "This is not yet a polished searchable content library/swipe-file for every ad hoc live-chat draft. Review workflows are stored more completely than casual chat drafts.",
    }


def answer_server_setup_question(*, query: str) -> Dict[str, Any]:
    cfg = load_config()
    agent = cfg.get("agent") or {}
    chat = agent.get("chat") or {}
    reese_bot = agent.get("reese_bot") or {}
    publishing = cfg.get("publishing") or {}
    feedback = cfg.get("feedback") or {}
    bot_owned_channel_ids = [str(x) for x in (reese_bot.get("bot_owned_channel_ids") or [])]
    archive = search_archive_content(query=query or "setup access", max_results=3)
    return {
        "ok": True,
        "query": query,
        "facts": {
            "guild_id": cfg.get("guild_id"),
            "reese_chat_channel_id": chat.get("channel_id"),
            "reese_chat_enabled": chat.get("enabled", True),
            "review_channel_id": publishing.get("review_channel_id") or feedback.get("review_channel_id"),
            "review_approval_role_id": publishing.get("review_approval_role_id") or feedback.get("approval_role_id"),
            "reese_bot_enabled": bool(reese_bot.get("enabled", False)),
            "reese_bot_service": "mirror-world-reesebot.service",
            "reese_bot_owned_channel_ids": bot_owned_channel_ids,
            "chat_response_transport": "ReeseBot Discord Gateway client sends as the Reese bot account; webhook is fallback only.",
            "gateway_bridge": "MarketingKnowledgeBase.agent.reese_bot on_message for live chat.",
            "rsadminbot_role": "RSAdminBot remains active for admin tooling and review controls/buttons; it backs out of the Reese live chat channel when standalone ReeseBot is enabled.",
            "rsadminbot_chat_bridge_disabled": bool(reese_bot.get("disable_rsadminbot_chat_bridge", False)),
            "review_controls_transport": "RSAdminBot/existing control path unless agent.reese_bot.controls_as_bot is enabled.",
            "knowledge_base_live_context": live_context_status(),
            "knowledge_base_archive_search": {
                "available": bool(archive.get("ok")),
                "latest_sync_at": archive.get("latest_sync_at"),
                "sources_checked": archive.get("sources_checked") or [],
                "candidate_count_returned": len(archive.get("candidates") or []),
                "search_note": archive.get("note"),
            },
            "content_record_storage": content_record_status(),
        },
    }
