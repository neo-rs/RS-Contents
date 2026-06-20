"""Discord logging helpers for MarketingKnowledgeBase operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

import requests  # type: ignore[import-not-found]

from MarketingKnowledgeBase.secrets import discord_bot_token

DISCORD_API = "https://discord.com/api/v10"


def _load_config() -> Dict[str, Any]:
    try:
        from pathlib import Path

        return json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _prefer_reese_bot_for_channel(channel_id: int) -> bool:
    cfg = _load_config()
    agent = cfg.get("agent") or {}
    bot_cfg = agent.get("reese_bot") or {}
    if not isinstance(bot_cfg, dict) or not bool(bot_cfg.get("enabled", False)):
        return False
    if not bool(bot_cfg.get("controls_as_bot", False)):
        return False
    owned = {str(x) for x in (bot_cfg.get("bot_owned_channel_ids") or [])}
    return str(int(channel_id or 0)) in owned


def _webhook_for_channel(channel_id: int) -> str:
    try:
        from MarketingKnowledgeBase.secrets import load_secrets

        secrets = load_secrets()
    except Exception:
        secrets = {}
    by_channel = secrets.get("channel_webhooks") or {}
    if isinstance(by_channel, dict):
        url = str(by_channel.get(str(channel_id)) or "").strip()
        if url.startswith("https://discord.com/api/webhooks/"):
            return url
    cfg = _load_config()
    pub = cfg.get("publishing") or {}
    preview = int(pub.get("neo_test_preview_channel_id") or 0)
    if int(channel_id) == preview:
        url = str(secrets.get("neo_test_preview_webhook_url") or pub.get("neo_test_preview_webhook_url") or "").strip()
        if url.startswith("https://discord.com/api/webhooks/"):
            return url
    return ""


def _post_discord_payload(*, channel_id: int, payload: Dict[str, Any], label: str) -> None:
    webhook = _webhook_for_channel(channel_id)
    token = discord_bot_token()
    if _prefer_reese_bot_for_channel(channel_id) and token and int(channel_id or 0) > 0:
        resp = requests.post(
            f"{DISCORD_API}/channels/{int(channel_id)}/messages",
            json=payload,
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            timeout=20,
        )
    elif webhook:
        resp = requests.post(
            webhook,
            json=payload,
            timeout=20,
        )
    else:
        if not token or int(channel_id or 0) <= 0:
            return
        resp = requests.post(
            f"{DISCORD_API}/channels/{int(channel_id)}/messages",
            json=payload,
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            timeout=20,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Discord {label} failed: {resp.status_code} {resp.text[:500]}")


def _short(value: Any, limit: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text or "-"
    return text[: limit - 1].rstrip() + "…"


def _bucket_counts(doc: Dict[str, Any]) -> str:
    buckets = doc.get("buckets") or {}
    parts: list[str] = []
    for name, payload in sorted(buckets.items()):
        if not isinstance(payload, dict):
            continue
        entries = payload.get("entries") or []
        parts.append(f"{name}: {len(entries)}")
    return _short(", ".join(parts), 1000)


def _candidate_counts(candidates: Dict[str, Any]) -> str:
    counts: dict[str, int] = {}
    for row in candidates.get("candidates") or []:
        bucket = str(row.get("bucket") or "unknown")
        counts[bucket] = counts.get(bucket, 0) + 1
    return _short(", ".join(f"{k}: {v}" for k, v in sorted(counts.items())), 1000)


def _top_candidates(candidates: Dict[str, Any], limit: int = 5) -> str:
    lines: list[str] = []
    for row in (candidates.get("candidates") or [])[:limit]:
        story_id = row.get("story_id") or "unknown"
        text = str(row.get("text") or "").replace("\n", " ").strip()
        lines.append(f"`{story_id}` - {_short(text, 120)}")
    return "\n".join(lines) or "-"


def post_knowledge_sync_report(
    *,
    channel_id: int,
    summary: Dict[str, Any],
    live_doc: Dict[str, Any],
    candidates: Dict[str, Any],
) -> None:
    """Post a compact sync report to a Discord channel using the bot token."""
    if int(channel_id or 0) <= 0:
        return

    embed = {
        "title": "Knowledge Data Sync Report",
        "description": "Marketing knowledge data was refreshed and story candidates were rebuilt.",
        "color": 5793266,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": [
            {"name": "Entries Saved", "value": str(summary.get("total_entries") or "-"), "inline": True},
            {"name": "Channels Synced", "value": str(summary.get("channels_synced") or "-"), "inline": True},
            {"name": "Story Candidates", "value": str(summary.get("story_candidates") or "-"), "inline": True},
            {"name": "Approx Bytes", "value": str(summary.get("approx_bytes") or "-"), "inline": True},
            {"name": "Last Sync", "value": str(summary.get("last_sync_at") or "-"), "inline": False},
            {"name": "Bucket Counts", "value": _bucket_counts(live_doc), "inline": False},
            {"name": "Candidate Buckets", "value": _candidate_counts(candidates), "inline": False},
            {"name": "Top Candidates", "value": _top_candidates(candidates), "inline": False},
        ],
        "footer": {"text": "MarketingKnowledgeBase.sync"},
    }
    payload = {
        "content": "",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    _post_discord_payload(channel_id=channel_id, payload=payload, label="knowledge log")


def post_marketing_generation_audit(
    *,
    channel_id: int,
    draft: Dict[str, Any],
    posted: Dict[str, Any] | None = None,
) -> None:
    """Post compact generation/model/token details for review visibility."""
    if int(channel_id or 0) <= 0:
        return

    routing = draft.get("model_routing") or {}
    usage = routing.get("usage") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    fields = [
        {"name": "Story", "value": _short(f"`{draft.get('story_id')}`\n{draft.get('source_message_link')}", 1000), "inline": False},
        {"name": "Model", "value": _short(f"{routing.get('model')} ({routing.get('tier')}/{routing.get('mode')})", 250), "inline": True},
        {"name": "Prompt Tokens", "value": str(usage.get("prompt_tokens") or "-"), "inline": True},
        {"name": "Completion Tokens", "value": str(usage.get("completion_tokens") or "-"), "inline": True},
        {"name": "Total Tokens", "value": str(usage.get("total_tokens") or "-"), "inline": True},
        {"name": "Reasoning Tokens", "value": str(completion_details.get("reasoning_tokens") or 0), "inline": True},
        {"name": "Research Related", "value": str(draft.get("research_related_count") or 0), "inline": True},
        {"name": "Memory Used", "value": str(bool(draft.get("memory_used"))), "inline": True},
        {"name": "Vision Used", "value": str(bool(draft.get("vision_used"))), "inline": True},
        {"name": "Market Enrichment", "value": str(bool(draft.get("market_enrichment_used"))), "inline": True},
        {"name": "Candidate Score", "value": str(draft.get("candidate_score") or "-"), "inline": True},
        {"name": "Post History Recorded", "value": str(bool(draft.get("post_history_recorded"))), "inline": True},
        {"name": "Style Variant", "value": _short(str(draft.get("style_variant") or "-"), 200), "inline": True},
        {"name": "Preferred Emoji", "value": _short(str(draft.get("preferred_emoji") or "-"), 200), "inline": True},
        {"name": "Source", "value": _short(str(draft.get("archive_source") or "-"), 700), "inline": False},
        {"name": "Research Sources", "value": _short("\n".join(str(p) for p in (draft.get("research_source_files") or [])) or "-", 900), "inline": False},
        {"name": "Story Angle", "value": _short(str(draft.get("story_angle") or "-"), 700), "inline": False},
        {"name": "Style Direction", "value": _short(str(draft.get("style_variant_instruction") or "-"), 700), "inline": False},
        {"name": "Routing Reason", "value": _short(str(routing.get("reason") or "-"), 700), "inline": False},
        {"name": "Rule Violations", "value": _short(", ".join(draft.get("rule_violations") or []) or "none", 500), "inline": False},
    ]
    if posted:
        fields.append({"name": "Posted", "value": _short(str(posted.get("url") or posted), 1000), "inline": False})

    embed = {
        "title": "What-You-Missed Generation Audit",
        "description": _short(str(draft.get("body_markdown") or ""), 900),
        "color": 3447003,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": fields[:25],
        "footer": {"text": "MarketingKnowledgeBase.post_what_you_missed"},
    }
    payload = {"content": "", "embeds": [embed], "allowed_mentions": {"parse": []}}
    _post_discord_payload(channel_id=channel_id, payload=payload, label="generation audit")
