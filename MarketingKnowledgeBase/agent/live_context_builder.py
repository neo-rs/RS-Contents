"""Build focused evidence packs for Reese live chat."""

from __future__ import annotations

from typing import Any, Dict, List

from MarketingKnowledgeBase.agent.live_tools import (
    answer_server_setup_question,
    check_role_channel_access,
    fetch_recent_channel_messages,
    inspect_linked_message,
    inspect_replied_message,
    pick_primary_source_message,
    pull_market_context,
    resolve_discord_references,
    search_current_server_context,
    search_archive_content,
    search_ghl_sms_docs,
    search_ticket_context,
)


def build_live_chat_context(
    *,
    route: Dict[str, Any],
    channel_id: int,
    message_text: str,
    discord_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    intent = str(route.get("intent") or "general_chat")
    context: Dict[str, Any] = {
        "intent": intent,
        "context_used": {
            "active_run_loaded": bool(route.get("requires_active_run")),
            "referenced_channels": [],
            "source_messages": 0,
            "ghl_docs": 0,
            "ticket_context_loaded": False,
            "permission_data_loaded": False,
            "live_market_verified": False,
        },
        "evidence_pack": {
            "references": {},
            "reply_message": {},
            "primary_message": {},
            "recent_channel_messages": [],
            "server_context_results": [],
            "role_access": {},
            "ticket_context": {},
            "ghl_sms_docs": [],
            "market_context": {},
            "archive_content": {},
            "setup_facts": {},
        },
    }

    references = resolve_discord_references(
        message_text=message_text,
        channel_id=channel_id,
        reply_message_id=str((discord_context or {}).get("reply_message_id") or ""),
        discord_context=discord_context,
    )
    context["evidence_pack"]["references"] = references
    referenced_channels = references.get("referenced_channels") or []
    context["context_used"]["referenced_channels"] = [
        r.get("channel_name") or r.get("channel_id") for r in referenced_channels
    ]

    if intent in {"new_lead_copy", "ghl_sms_copy", "market_research"}:
        reply = inspect_replied_message(discord_context=discord_context)
        if reply.get("ok"):
            context["evidence_pack"]["reply_message"] = reply.get("message") or {}
        linked = inspect_linked_message(
            message_text=message_text,
            references=references,
            discord_context=discord_context,
        )
        if linked.get("ok"):
            context["evidence_pack"]["linked_message"] = linked.get("message") or {}
        target_channels = referenced_channels[:2]
        recent_messages: List[Dict[str, Any]] = []
        if not target_channels and intent in {"new_lead_copy", "market_research"}:
            # Use current channel only if no reference was supplied; better than guessing from active review context.
            target_channels = [{"channel_id": str(channel_id), "channel_name": "", "bucket": ""}]
        for channel in target_channels:
            cid = str(channel.get("channel_id") or "")
            if not cid:
                continue
            result = fetch_recent_channel_messages(channel_id=cid, limit=10, discord_context=discord_context)
            recent_messages.extend(result.get("messages") or [])
        context["evidence_pack"]["recent_channel_messages"] = recent_messages[:15]
        context["context_used"]["source_messages"] = len(recent_messages[:15])
        primary = pick_primary_source_message(
            replied_message=context["evidence_pack"].get("reply_message")
            or context["evidence_pack"].get("linked_message")
            or {},
            recent_messages=recent_messages,
        )
        context["evidence_pack"]["primary_message"] = primary
        market = pull_market_context(source_messages=[primary] + recent_messages[:6], query=message_text)
        context["evidence_pack"]["market_context"] = market
        context["context_used"]["live_market_verified"] = bool(market.get("verified_live_market"))

    if intent == "content_discovery":
        archive = search_archive_content(query=message_text, max_results=6)
        candidates = archive.get("candidates") or []
        context["evidence_pack"]["archive_content"] = archive
        context["evidence_pack"]["primary_message"] = candidates[0] if candidates else {}
        context["context_used"]["source_messages"] = len(candidates)
        context["context_used"]["archive_sources_checked"] = [
            row.get("label") for row in archive.get("sources_checked") or [] if row.get("exists")
        ]

    if intent in {"channel_question", "market_research"}:
        channel_filter = ""
        if referenced_channels:
            channel_filter = str(referenced_channels[0].get("channel_id") or "")
        search = search_current_server_context(query=message_text, channel_id=channel_filter, max_results=8)
        context["evidence_pack"]["server_context_results"] = search.get("results") or []

    if intent == "role_access_question":
        target = referenced_channels[0].get("channel_id") if referenced_channels else str(channel_id)
        access = check_role_channel_access(channel_id=target, discord_context=discord_context)
        context["evidence_pack"]["role_access"] = access
        context["context_used"]["permission_data_loaded"] = bool(access.get("ok"))

    if intent in {"ticket_support", "cancellation_save", "help_inquiry"}:
        ticket = search_ticket_context(query=message_text)
        context["evidence_pack"]["ticket_context"] = ticket
        context["context_used"]["ticket_context_loaded"] = bool(ticket.get("ready"))

    if intent == "ghl_sms_copy":
        docs = search_ghl_sms_docs(query=message_text, max_results=5)
        context["evidence_pack"]["ghl_sms_docs"] = docs.get("results") or []
        context["context_used"]["ghl_docs"] = len(docs.get("results") or [])

    if intent == "server_setup_question":
        setup = answer_server_setup_question(query=message_text)
        context["evidence_pack"]["setup_facts"] = setup.get("facts") or {}

    return context
