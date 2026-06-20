"""Structured feedback memory for the RS content automation agent."""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import uuid4

from MarketingKnowledgeBase.agent.state import DATA, now_iso, read_json, write_json

MEMORY_PATH = DATA / "agent_memory.json"
FEEDBACK_PATH = DATA / "agent_feedback.json"


def load_agent_memory() -> Dict[str, Any]:
    doc = read_json(MEMORY_PATH, None)
    if isinstance(doc, dict) and doc.get("version"):
        return doc
    doc = {
        "version": 1,
        "updated_at": now_iso(),
        "global_rs_memory": [],
        "content_type_memory": {},
        "channel_memory": {},
        "do_not_claim_memory": [
            {
                "memory_id": str(uuid4()),
                "text": "Do not say spots are limited unless a real cap is configured.",
                "enabled": True,
                "created_at": now_iso(),
            },
            {
                "memory_id": str(uuid4()),
                "text": "Do not invent profit, sell-through, urgency, or member wins.",
                "enabled": True,
                "created_at": now_iso(),
            },
        ],
    }
    write_json(MEMORY_PATH, doc)
    return doc


def remember_rule(
    text: str,
    *,
    scope: str = "global_rs_memory",
    content_type: str = "",
    channel_id: str = "",
    created_by: str = "",
) -> Dict[str, Any]:
    doc = load_agent_memory()
    row = {
        "memory_id": str(uuid4()),
        "text": str(text or "").strip(),
        "scope": scope,
        "content_type": content_type,
        "channel_id": str(channel_id or ""),
        "created_by": str(created_by or ""),
        "enabled": True,
        "created_at": now_iso(),
    }
    if scope == "content_type_memory" and content_type:
        doc.setdefault("content_type_memory", {}).setdefault(content_type, []).append(row)
    elif scope == "channel_memory" and channel_id:
        doc.setdefault("channel_memory", {}).setdefault(str(channel_id), []).append(row)
    elif scope == "do_not_claim_memory":
        doc.setdefault("do_not_claim_memory", []).append(row)
    else:
        doc.setdefault("global_rs_memory", []).append(row)
    doc["updated_at"] = now_iso()
    write_json(MEMORY_PATH, doc)
    return row


def record_feedback(event: Dict[str, Any]) -> Dict[str, Any]:
    doc = read_json(FEEDBACK_PATH, {"version": 1, "feedback": []})
    row = dict(event)
    row.setdefault("feedback_id", str(uuid4()))
    row.setdefault("created_at", now_iso())
    doc.setdefault("feedback", []).insert(0, row)
    doc["feedback"] = doc["feedback"][:500]
    doc["updated_at"] = now_iso()
    write_json(FEEDBACK_PATH, doc)
    return row


def relevant_memory_prompt(*, content_type: str = "", channel_id: str = "", max_items: int = 16) -> str:
    doc = load_agent_memory()
    rows: List[Dict[str, Any]] = []
    rows.extend([r for r in doc.get("do_not_claim_memory") or [] if r.get("enabled", True)])
    rows.extend([r for r in doc.get("global_rs_memory") or [] if r.get("enabled", True)])
    if content_type:
        rows.extend(
            [
                r
                for r in (doc.get("content_type_memory") or {}).get(content_type, [])
                if r.get("enabled", True)
            ]
        )
    if channel_id:
        rows.extend(
            [
                r
                for r in (doc.get("channel_memory") or {}).get(str(channel_id), [])
                if r.get("enabled", True)
            ]
        )
    if not rows:
        return ""
    lines = ["AGENT MEMORY (durable correction/style rules):"]
    for row in rows[:max_items]:
        lines.append(f"- {row.get('text')}")
    return "\n".join(lines)

