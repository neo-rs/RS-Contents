"""Intent routing for Captain Hook live chat."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List


ACTIVE_REVIEW_HINTS = (
    "active run",
    "current run",
    "status",
    "review draft",
    "revise",
    "rewrite",
    "less hype",
    "more hype",
    "approve",
    "publish",
)
NEW_LEAD_HINTS = (
    "lead",
    "no success post",
    "write for this",
    "write this up",
    "alert",
    "important",
    "online-important",
    "deals-important",
)
GHL_SMS_HINTS = ("ghl", "sms", "text blast", "automation", "campaign", "follow up", "follow-up")
MARKET_HINTS = ("market", "comps", "resale", "ebay", "stockx", "profit", "worth", "selling for")
ROLE_HINTS = ("who has access", "who can see", "roles", "permission", "permissions", "access to")
SERVER_HINTS = ("setup", "configured", "config", "webhook", "service", "timer", "channel does", "where is")
TICKET_HINTS = ("ticket", "support ticket", "help inquiry", "member issue")
CANCEL_HINTS = ("cancel", "cancellation", "refund", "win back", "save attempt")
SUGGESTION_HINTS = ("suggestion", "suggestions channel", "feature request")


@dataclass
class IntentRoute:
    intent: str
    confidence: float
    referenced_channel_ids: List[str] = field(default_factory=list)
    needs_tools: List[str] = field(default_factory=list)
    requires_active_run: bool = False
    memory_scope: str = "short_term"
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": round(float(self.confidence), 2),
            "referenced_channel_ids": list(self.referenced_channel_ids),
            "needs_tools": list(self.needs_tools),
            "requires_active_run": bool(self.requires_active_run),
            "memory_scope": self.memory_scope,
            "reason": self.reason,
        }


def _channel_ids(text: str, mentioned_channel_ids: List[str] | None = None) -> List[str]:
    ids = [str(x) for x in (mentioned_channel_ids or []) if str(x).strip()]
    ids.extend(re.findall(r"<#(\d{15,25})>", text or ""))
    ids.extend(re.findall(r"discord(?:app)?\.com/channels/\d+/(\d{15,25})/\d{15,25}", text or ""))
    seen: set[str] = set()
    out: List[str] = []
    for cid in ids:
        if cid not in seen:
            out.append(cid)
            seen.add(cid)
    return out


def route_live_chat_intent(
    *,
    message_text: str,
    mentioned_channel_ids: List[str] | None = None,
    replied_to_message_id: str = "",
    active_run_summary: str = "",
    history: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Classify the live chat request without burning model tokens.

    This router intentionally uses deterministic rules first. The live chat still
    uses configured OpenAI models for answer generation, but routing stays cheap
    and avoids stale model-name placeholders.
    """

    text = str(message_text or "").strip()
    lowered = text.lower()
    channel_ids = _channel_ids(text, mentioned_channel_ids)
    has_reply = bool(str(replied_to_message_id or "").strip())

    if lowered.startswith("remember:"):
        return IntentRoute(
            intent="remember_rule",
            confidence=0.99,
            referenced_channel_ids=channel_ids,
            needs_tools=["store_memory_rule"],
            memory_scope="durable",
            reason="Message uses the explicit remember command.",
        ).to_dict()

    if any(h in lowered for h in TICKET_HINTS):
        return IntentRoute(
            intent="ticket_support",
            confidence=0.82,
            referenced_channel_ids=channel_ids,
            needs_tools=["search_ticket_context"],
            reason="Message asks about support/ticket context; this workflow is reserved but not wired.",
        ).to_dict()

    if any(h in lowered for h in CANCEL_HINTS):
        return IntentRoute(
            intent="cancellation_save",
            confidence=0.82,
            referenced_channel_ids=channel_ids,
            needs_tools=["search_ticket_context"],
            reason="Message asks about cancellation/save behavior; this workflow is reserved but not wired.",
        ).to_dict()

    if any(h in lowered for h in SUGGESTION_HINTS):
        return IntentRoute(
            intent="suggestion_channel_reply",
            confidence=0.74,
            referenced_channel_ids=channel_ids,
            needs_tools=["search_current_server_context"],
            reason="Message references suggestion-channel style work; future workflow is recognized.",
        ).to_dict()

    if any(h in lowered for h in GHL_SMS_HINTS):
        return IntentRoute(
            intent="ghl_sms_copy",
            confidence=0.9,
            referenced_channel_ids=channel_ids,
            needs_tools=["search_ghl_sms_docs", "draft_ghl_sms"],
            reason="Message asks for GHL/SMS/campaign copy.",
        ).to_dict()

    if any(h in lowered for h in ROLE_HINTS):
        return IntentRoute(
            intent="role_access_question",
            confidence=0.88,
            referenced_channel_ids=channel_ids,
            needs_tools=["resolve_discord_references", "check_role_channel_access"],
            reason="Message asks who can see or access a channel.",
        ).to_dict()

    if any(h in lowered for h in NEW_LEAD_HINTS) and (channel_ids or has_reply or "lead" in lowered):
        return IntentRoute(
            intent="new_lead_copy",
            confidence=0.9,
            referenced_channel_ids=channel_ids,
            needs_tools=[
                "resolve_discord_references",
                "inspect_replied_message",
                "fetch_recent_channel_messages",
                "pull_market_context",
                "draft_lead_copy",
            ],
            requires_active_run=False,
            reason="Message asks for copy for a lead/channel source, not the active review draft.",
        ).to_dict()

    if any(h in lowered for h in MARKET_HINTS):
        return IntentRoute(
            intent="market_research",
            confidence=0.78,
            referenced_channel_ids=channel_ids,
            needs_tools=["resolve_discord_references", "search_current_server_context", "pull_market_context"],
            reason="Message asks about market, comps, resale, or profit.",
        ).to_dict()

    if any(h in lowered for h in SERVER_HINTS):
        return IntentRoute(
            intent="server_setup_question",
            confidence=0.78,
            referenced_channel_ids=channel_ids,
            needs_tools=["answer_server_setup_question"],
            reason="Message asks about setup/config/services/channels.",
        ).to_dict()

    if any(h in lowered for h in ACTIVE_REVIEW_HINTS):
        return IntentRoute(
            intent="active_review_help",
            confidence=0.84,
            referenced_channel_ids=channel_ids,
            needs_tools=["load_active_review"],
            requires_active_run=True,
            reason="Message matches active review/run help wording.",
        ).to_dict()

    return IntentRoute(
        intent="general_chat",
        confidence=0.65,
        referenced_channel_ids=channel_ids,
        needs_tools=["recent_chat_memory"],
        reason="No specialized workflow matched.",
    ).to_dict()
