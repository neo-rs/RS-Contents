"""Polling bridge between a Discord review channel and the RS agent."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from MarketingKnowledgeBase.agent.state import (
    get_active_review_run,
    get_review_last_seen,
    set_review_last_seen,
)
from MarketingKnowledgeBase.agent.workflow import agent_handle_review_message
from MarketingKnowledgeBase.discord_api import extract_message_text, fetch_channel_messages, fetch_guild_member
from MarketingKnowledgeBase.discord_log import _post_discord_payload
from MarketingKnowledgeBase.secrets import discord_bot_token

BASE = Path(__file__).resolve().parents[1]
RUN_RE = re.compile(r"\brun[:\s]+([0-9a-fA-F-]{24,})\b")


def _read_config() -> Dict[str, Any]:
    with open(BASE / "config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _review_channel_id(cfg: Dict[str, Any]) -> int:
    agent = cfg.get("agent") or {}
    feedback = cfg.get("feedback") or {}
    publishing = cfg.get("publishing") or {}
    return int(
        agent.get("review_channel_id")
        or feedback.get("review_channel_id")
        or publishing.get("review_channel_id")
        or 0
    )


def _message_author_id(msg: Dict[str, Any]) -> str:
    author = msg.get("author") or {}
    return str(author.get("id") or "") if isinstance(author, dict) else ""


def _review_guild_id(cfg: Dict[str, Any]) -> int:
    agent = cfg.get("agent") or {}
    feedback = cfg.get("feedback") or {}
    publishing = cfg.get("publishing") or {}
    return int(
        agent.get("review_guild_id")
        or feedback.get("review_guild_id")
        or publishing.get("review_guild_id")
        or publishing.get("production_guild_id")
        or cfg.get("guild_id")
        or 0
    )


def _author_allowed(*, cfg: Dict[str, Any], headers: Dict[str, str], user_id: str) -> bool:
    agent = cfg.get("agent") or {}
    allowed_users = {str(uid) for uid in (agent.get("allowed_user_ids") or []) if str(uid).strip()}
    allowed_roles = {str(rid) for rid in (agent.get("allowed_role_ids") or []) if str(rid).strip()}
    if not allowed_users and not allowed_roles:
        return True
    if user_id in allowed_users:
        return True
    if not allowed_roles:
        return False
    guild_id = _review_guild_id(cfg)
    if guild_id <= 0 or not user_id:
        return False
    try:
        member = fetch_guild_member(guild_id, user_id, headers)
    except Exception:
        return False
    roles = {str(role) for role in (member.get("roles") or [])}
    return bool(roles & allowed_roles)


def _is_bot_message(msg: Dict[str, Any]) -> bool:
    author = msg.get("author") or {}
    return bool(isinstance(author, dict) and author.get("bot"))


def _run_id_from_text(text: str) -> str:
    match = RUN_RE.search(str(text or ""))
    if match:
        return match.group(1)
    return get_active_review_run()


def _format_ack(result: Dict[str, Any]) -> str:
    ack = str(result.get("ack") or "").strip()
    command = str(result.get("command") or "").strip()
    if command == "explain":
        exp = result.get("explanation") or {}
        return (
            "Source explanation:\n"
            f"- Story: `{exp.get('story_id') or '-'}`\n"
            f"- Source: {exp.get('source_message_link') or '-'}\n"
            f"- Vision used: {bool(exp.get('vision_used'))}\n"
            f"- Memory used: {bool(exp.get('memory_used'))}\n"
            f"- Unsupported claims: {len(exp.get('unsupported_claims') or [])}"
        )[:1900]
    if command == "status":
        run = result.get("run") or {}
        return (
            f"Run `{run.get('run_id')}` status: `{run.get('status')}`\n"
            f"Drafts: {len(run.get('drafts') or [])}\n"
            f"Validations: {len(run.get('validation_results') or [])}"
        )
    if command == "publish":
        pub = result.get("publish_result") or {}
        if pub.get("ok"):
            return f"Published: {pub.get('url') or pub}"
        return f"Publish blocked: {pub.get('reason') or pub.get('error') or pub}"
    draft = result.get("draft") or {}
    if draft:
        text = str(draft.get("full_text") or draft.get("body_markdown") or "")
        vision = result.get("vision") or {}
        validation = result.get("validation") or {}
        source = (draft.get("source_refs") or draft.get("source_message_link") or ["-"])
        if isinstance(source, list):
            source_text = source[0] if source else "-"
        else:
            source_text = str(source or "-")
        vision_line = "Vision saw: not used or no readable image."
        if isinstance(vision, dict) and vision.get("ok"):
            facts = vision.get("visible_facts") or []
            vision_line = "Vision saw: " + ("; ".join(str(x) for x in facts[:3]) or str(vision.get("summary") or "")[:240])
        return (
            f"{ack or 'Draft ready for review.'}\n"
            f"{vision_line}\n"
            f"Sources used: {source_text}\n"
            f"Validation: {validation.get('validation_status') or '-'}"
            f" ({'ready' if validation.get('ready_to_publish') else 'needs review'})\n\n"
            f"{text}"
        )[:1900]
    return (ack or f"Handled `{command or 'message'}`.")[:1900]


def poll_review_channel_once(*, limit: int = 20, channel_id: int = 0) -> Dict[str, Any]:
    cfg = _read_config()
    channel_id = int(channel_id or _review_channel_id(cfg))
    if channel_id <= 0:
        return {"ok": False, "error": "review channel is not configured"}
    token = discord_bot_token()
    if not token:
        return {"ok": False, "error": "missing Discord bot token"}

    headers = {"Authorization": f"Bot {token}"}
    messages = fetch_channel_messages(channel_id, headers, limit=limit)
    last_seen = get_review_last_seen(channel_id)
    new_messages: List[Dict[str, Any]] = []
    for msg in reversed(messages):
        mid = str(msg.get("id") or "")
        if not mid or _is_bot_message(msg):
            continue
        if last_seen and int(mid) <= int(last_seen):
            continue
        new_messages.append(msg)

    handled: List[Dict[str, Any]] = []
    for msg in new_messages:
        mid = str(msg.get("id") or "")
        text = extract_message_text(msg)
        author_id = _message_author_id(msg)
        if not _author_allowed(cfg=cfg, headers=headers, user_id=author_id):
            handled.append({"message_id": mid, "ok": False, "skipped": "unauthorized_author", "author_id": author_id})
            set_review_last_seen(channel_id, mid)
            continue
        run_id = _run_id_from_text(text)
        if not run_id:
            set_review_last_seen(channel_id, mid)
            continue
        try:
            result = agent_handle_review_message(
                run_id=run_id,
                message_text=text,
                requested_by=_message_author_id(msg),
                channel_id=channel_id,
            )
            ack = _format_ack(result)
            _post_discord_payload(
                channel_id=channel_id,
                payload={"content": ack, "allowed_mentions": {"parse": []}},
                label="agent review ack",
            )
            handled.append({"message_id": mid, "run_id": run_id, "command": result.get("command"), "ok": True})
        except Exception as exc:
            err = f"Agent review error for run `{run_id}`: {type(exc).__name__}: {str(exc)[:500]}"
            _post_discord_payload(
                channel_id=channel_id,
                payload={"content": err, "allowed_mentions": {"parse": []}},
                label="agent review error",
            )
            handled.append({"message_id": mid, "run_id": run_id, "ok": False, "error": err})
        set_review_last_seen(channel_id, mid)
    return {"ok": True, "channel_id": channel_id, "seen": len(new_messages), "handled": handled}


def run_review_agent(*, interval_s: int = 10, limit: int = 20, channel_id: int = 0, once: bool = False) -> Dict[str, Any]:
    if once:
        return poll_review_channel_once(limit=limit, channel_id=channel_id)
    while True:
        poll_review_channel_once(limit=limit, channel_id=channel_id)
        time.sleep(max(2, int(interval_s)))
