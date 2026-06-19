"""Destination profiles for Discord now and GHL/SMS later."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from MarketingKnowledgeBase.agent.state import DATA, read_json, write_json

BASE = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATIONS: List[Dict[str, Any]] = [
    {
        "destination_id": "discord_what_you_missed",
        "platform": "discord",
        "content_type": "what_you_missed",
        "audience": "RS public/free server waitlist audience",
        "format_rules": {
            "plain_chat": True,
            "allow_discord_markdown": True,
            "allow_channel_mentions": True,
            "allow_urls": False,
            "max_length": 2000,
        },
        "required_cta": "waitlist_channel",
        "approval_policy": "trusted_after_validation",
        "auto_publish_policy": "trusted_after_validation",
        "tone_profile": "RS insider, direct, grounded, proof-first",
        "publish_adapter": "discord",
    },
    {
        "destination_id": "discord_review",
        "platform": "discord",
        "content_type": "review",
        "audience": "internal RS review team",
        "format_rules": {"plain_chat": True, "allow_urls": True, "max_length": 2000},
        "required_cta": "",
        "approval_policy": "manual_only",
        "auto_publish_policy": "manual_only",
        "tone_profile": "compact operational review",
        "publish_adapter": "discord",
    },
    {
        "destination_id": "discord_member_win",
        "platform": "discord",
        "content_type": "member_win",
        "audience": "RS members and prospects",
        "format_rules": {"plain_chat": True, "allow_urls": False, "max_length": 2000},
        "required_cta": "waitlist_channel",
        "approval_policy": "trusted_after_validation",
        "auto_publish_policy": "trusted_after_validation",
        "tone_profile": "celebratory but specific",
        "publish_adapter": "discord",
    },
    {
        "destination_id": "discord_announcement",
        "platform": "discord",
        "content_type": "announcement",
        "audience": "RS Discord audience",
        "format_rules": {"plain_chat": True, "allow_urls": True, "max_length": 2000},
        "required_cta": "",
        "approval_policy": "manual_only",
        "auto_publish_policy": "manual_only",
        "tone_profile": "clear and operational",
        "publish_adapter": "discord",
    },
    {
        "destination_id": "ghl_sms_campaign",
        "platform": "ghl_sms",
        "content_type": "sms_campaign",
        "audience": "GHL contact segment",
        "format_rules": {
            "plain_text": True,
            "allow_discord_markdown": False,
            "allow_channel_mentions": False,
            "allow_custom_emoji": False,
            "max_length": 320,
        },
        "required_cta": "configured_sms_cta",
        "approval_policy": "dry_run_only",
        "auto_publish_policy": "dry_run_only",
        "tone_profile": "short, direct, compliant",
        "publish_adapter": "draft_only",
    },
    {
        "destination_id": "ghl_sms_followup",
        "platform": "ghl_sms",
        "content_type": "sms_followup",
        "audience": "GHL follow-up segment",
        "format_rules": {
            "plain_text": True,
            "allow_discord_markdown": False,
            "allow_channel_mentions": False,
            "allow_custom_emoji": False,
            "max_length": 320,
        },
        "required_cta": "configured_sms_cta",
        "approval_policy": "dry_run_only",
        "auto_publish_policy": "dry_run_only",
        "tone_profile": "short, personal, compliant",
        "publish_adapter": "draft_only",
    },
]


def destinations_path():
    return DATA / "content_destinations.json"


def load_destinations() -> Dict[str, Dict[str, Any]]:
    cfg = read_json(BASE / "config.json", {}) or {}
    if isinstance(cfg.get("destinations"), list):
        return {
            str(row.get("destination_id")): row
            for row in cfg.get("destinations") or []
            if isinstance(row, dict) and row.get("destination_id")
        }
    path = destinations_path()
    doc = read_json(path, None)
    if not isinstance(doc, dict) or not isinstance(doc.get("destinations"), list):
        doc = {"version": 1, "destinations": DEFAULT_DESTINATIONS}
        write_json(path, doc)
    return {
        str(row.get("destination_id")): row
        for row in doc.get("destinations") or []
        if isinstance(row, dict) and row.get("destination_id")
    }


def get_destination(destination_id: str) -> Dict[str, Any]:
    destinations = load_destinations()
    if destination_id in destinations:
        return destinations[destination_id]
    return destinations["discord_what_you_missed"]
