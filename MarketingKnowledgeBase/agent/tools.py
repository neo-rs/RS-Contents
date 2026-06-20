"""Structured tools available to the RS content automation agent."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from MarketingKnowledgeBase.agent.destinations import get_destination
from MarketingKnowledgeBase.agent.memory import record_feedback, relevant_memory_prompt, remember_rule
from MarketingKnowledgeBase.agent.state import DATA, append_tool_call, load_run, read_json
from MarketingKnowledgeBase.agent.validation import validate_structured_draft
from MarketingKnowledgeBase.ai_writer import generate_marketing_copy
from MarketingKnowledgeBase.draft_composer import _asset_urls
from MarketingKnowledgeBase.image_vision import describe_proof_images_detailed
from MarketingKnowledgeBase.marketing_memory import load_marketing_memory, memory_prompt
from MarketingKnowledgeBase.post_queue import PostQueue
from MarketingKnowledgeBase.story_candidates import find_candidate, find_candidate_by_message_id
from MarketingKnowledgeBase.story_research import build_research_pack, research_prompt

BASE = Path(__file__).resolve().parents[1]


def _read_base_json(name: str, default: Any) -> Any:
    return read_json(BASE / name, default)


def _load_candidates() -> Dict[str, Any]:
    try:
        from MarketingKnowledgeBase.archives import preferred_candidate_paths

        cfg = read_json(BASE / "config.json", {}) or {}
        tz_name = str(((cfg.get("publishing") or {}).get("schedule") or {}).get("timezone") or "America/New_York")
        for path in preferred_candidate_paths(DATA, tz_name):
            if path.exists():
                doc = read_json(path, {"candidates": []})
                if isinstance(doc, dict) and doc.get("candidates"):
                    doc["_agent_candidate_source"] = str(path)
                    return doc
    except Exception:
        pass
    return read_json(DATA / "story_candidates.json", {"candidates": []})


def list_story_candidates(*, bucket: str = "", limit: int = 10) -> Dict[str, Any]:
    doc = _load_candidates()
    rows = list(doc.get("candidates") or [])
    if bucket:
        rows = [r for r in rows if str(r.get("bucket") or "") == bucket]
    return {"generated_at": doc.get("generated_at"), "count": len(rows[:limit]), "candidates": rows[:limit]}


def get_story_context(*, story_id: str = "", message_id: str = "") -> Dict[str, Any]:
    doc = _load_candidates()
    candidate = find_candidate(doc, story_id) if story_id else None
    if not candidate and message_id:
        candidate = find_candidate_by_message_id(doc, message_id)
    if not candidate:
        raise ValueError(f"Story not found: story_id={story_id!r} message_id={message_id!r}")
    return {"story": candidate}


def get_post_assets(*, story_id: str) -> Dict[str, Any]:
    story = get_story_context(story_id=story_id)["story"]
    return {"story_id": story_id, "assets": _asset_urls(story), "source_message_link": story.get("message_link")}


def describe_images(*, story_id: str, max_images: int = 4) -> Dict[str, Any]:
    story = get_story_context(story_id=story_id)["story"]
    urls = [str(a.get("url") or "") for a in (story.get("attachments") or []) if a.get("url")]
    urls.extend(str(a.get("url") or "") for a in (story.get("embed_images") or []) if a.get("url"))
    return describe_proof_images_detailed(
        urls[:max_images],
        context=str(story.get("text") or "")[:1500],
        source_message_id=str(story.get("message_id") or ""),
    )


def search_related_context(*, story_id: str) -> Dict[str, Any]:
    story = get_story_context(story_id=story_id)["story"]
    pack = build_research_pack(story)
    return {"research_pack": pack, "prompt": research_prompt(pack)}


def load_memory_tool(*, content_type: str = "", channel_id: str = "") -> Dict[str, Any]:
    memory = load_marketing_memory(auto_refresh=True)
    return {
        "marketing_memory": memory,
        "marketing_memory_prompt": memory_prompt(memory),
        "agent_memory_prompt": relevant_memory_prompt(content_type=content_type, channel_id=channel_id),
    }


def _split_draft_text(text: str) -> Dict[str, str]:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    headline = lines[0] if lines else ""
    cta = lines[-1] if len(lines) > 1 else ""
    body = "\n".join(lines[1:-1] if len(lines) > 2 else lines[1:])
    return {"headline": headline, "body": body, "cta": cta, "full_text": str(text or "").strip()}


def draft_content(
    *,
    story_id: str,
    destination_id: str = "discord_what_you_missed",
    extra_instructions: str = "",
) -> Dict[str, Any]:
    destination = get_destination(destination_id)
    memory_context = load_memory_tool(
        content_type=str(destination.get("content_type") or ""),
        channel_id=str(destination_id or ""),
    )
    memory_instructions = "\n\n".join(
        str(memory_context.get(key) or "")
        for key in ("marketing_memory_prompt", "agent_memory_prompt")
        if memory_context.get(key)
    )
    combined_instructions = "\n\n".join(
        part for part in (extra_instructions, memory_instructions) if str(part or "").strip()
    )
    if destination.get("platform") == "ghl_sms":
        story = get_story_context(story_id=story_id)["story"]
        facts = story.get("deal_facts") or {}
        title = facts.get("title") or (story.get("headline_hints") or ["RS update"])[0]
        text = f"RS alert: {str(title)[:120]}. Reply/check RS for details before it dries up."
        parts = _split_draft_text(text[: int((destination.get("format_rules") or {}).get("max_length") or 320)])
        return {
            "content_type": destination.get("content_type"),
            "destination": destination_id,
            **parts,
            "assets_to_attach": [],
            "facts_used": [story_id],
            "claims": [],
            "unsupported_claims": [],
            "tone_notes": destination.get("tone_profile"),
            "source_refs": [story.get("message_link")],
            "memory_refs": [],
            "validation_status": "draft",
        }

    draft = generate_marketing_copy(
        story_id=story_id,
        target_channel="what-you-missed",
        extra_instructions=combined_instructions,
        candidates_doc=_load_candidates(),
    )
    parts = _split_draft_text(str(draft.get("body_markdown") or ""))
    return {
        **draft,
        "content_type": destination.get("content_type"),
        "destination": destination_id,
        **parts,
        "assets_to_attach": draft.get("reuse_assets") or [],
        "facts_used": [story_id],
        "claims": [],
        "unsupported_claims": [],
        "tone_notes": destination.get("tone_profile"),
        "source_refs": [draft.get("source_message_link")],
        "memory_refs": [],
        "validation_status": "draft",
    }


def generate_draft(
    *,
    story_id: str,
    destination_id: str = "discord_what_you_missed",
    extra_instructions: str = "",
) -> Dict[str, Any]:
    return draft_content(
        story_id=story_id,
        destination_id=destination_id,
        extra_instructions=extra_instructions,
    )


def critique_content(
    *,
    draft: Dict[str, Any],
    story_id: str,
    destination_id: str = "discord_what_you_missed",
    rejected: bool = False,
    vision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    story = get_story_context(story_id=story_id)["story"]
    destination = get_destination(destination_id)
    offers = _read_base_json("offers.json", {})
    research_pack = build_research_pack(story)
    if research_pack.get("related"):
        offers = {
            **offers,
            "related_source_context": [
                {
                    "bucket": row.get("bucket"),
                    "channel_id": row.get("channel_id"),
                    "posted_at": row.get("posted_at"),
                    "text": row.get("text"),
                    "deal_facts": row.get("deal_facts") or {},
                }
                for row in (research_pack.get("related") or [])[:6]
            ],
        }
    return validate_structured_draft(
        draft,
        destination=destination,
        story=story,
        vision=vision or {},
        offers=offers,
        rejected=rejected,
    )


def critique_draft(
    *,
    draft: Dict[str, Any],
    story_id: str,
    destination_id: str = "discord_what_you_missed",
    rejected: bool = False,
    vision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return critique_content(
        draft=draft,
        story_id=story_id,
        destination_id=destination_id,
        rejected=rejected,
        vision=vision,
    )


def revise_content(
    *,
    draft: Dict[str, Any],
    feedback_text: str,
    story_id: str,
    destination_id: str = "discord_what_you_missed",
) -> Dict[str, Any]:
    instruction = (
        "Revise the previous draft using this human feedback. Keep only sourced facts. "
        f"Human feedback: {feedback_text}\n\nPrevious draft:\n{draft.get('full_text') or draft.get('body_markdown') or ''}"
    )
    return draft_content(story_id=story_id, destination_id=destination_id, extra_instructions=instruction)


def apply_feedback(
    *,
    draft: Dict[str, Any],
    feedback_text: str,
    story_id: str,
    destination_id: str = "discord_what_you_missed",
) -> Dict[str, Any]:
    return revise_content(
        draft=draft,
        feedback_text=feedback_text,
        story_id=story_id,
        destination_id=destination_id,
    )


def store_feedback(*, run_id: str, feedback_text: str, event_type: str = "feedback", created_by: str = "") -> Dict[str, Any]:
    return record_feedback(
        {
            "run_id": run_id,
            "event_type": event_type,
            "feedback_text": feedback_text,
            "created_by": created_by,
        }
    )


def explain_sources(*, run: Dict[str, Any]) -> Dict[str, Any]:
    draft = (run.get("final_output") or {}) if isinstance(run, dict) else {}
    story_id = str((run.get("metadata") or {}).get("story_id") or draft.get("story_id") or "")
    story = get_story_context(story_id=story_id)["story"] if story_id else {}
    return {
        "run_id": run.get("run_id"),
        "story_id": story_id,
        "source_message_link": story.get("message_link") or draft.get("source_message_link"),
        "source_text_excerpt": str(story.get("text") or "")[:900],
        "deal_facts": story.get("deal_facts") or {},
        "assets": _asset_urls(story) if story else [],
        "model_routing": draft.get("model_routing") or {},
        "vision_used": bool(draft.get("vision_used")),
        "memory_used": bool(draft.get("memory_used")),
        "claims": draft.get("claims") or [],
        "unsupported_claims": draft.get("unsupported_claims") or [],
        "validation_results": run.get("validation_results") or [],
    }


def queue_content(*, draft: Dict[str, Any]) -> Dict[str, Any]:
    item = PostQueue(DATA / "post_queue.json").add_draft(draft)
    return {"ok": True, "queued": item}


def queue_revision(*, draft: Dict[str, Any]) -> Dict[str, Any]:
    return queue_content(draft=draft)


def publish_content(*, draft: Dict[str, Any], channel_id: int = 0, validation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from MarketingKnowledgeBase.post_publisher import publish_marketing_draft

    validation = validation or {}
    if not validation.get("ready_to_publish"):
        return {"ok": False, "blocked": True, "reason": "validation is not ready_to_publish", "validation": validation}
    payload = dict(draft)
    payload.setdefault("body_markdown", payload.get("full_text") or "")
    payload.setdefault("reuse_assets", payload.get("assets_to_attach") or [])
    return publish_marketing_draft(payload, channel_id=channel_id or None, dry_run=False)


def publish_approved_post(*, draft: Dict[str, Any], channel_id: int = 0, validation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return publish_content(draft=draft, channel_id=channel_id, validation=validation)


def remember_style_rule(
    *,
    rule_text: str,
    scope: str = "global_rs_memory",
    created_by: str = "",
) -> Dict[str, Any]:
    return remember_rule(rule_text, scope=scope, created_by=created_by)


def search_past_wins(*, limit: int = 8) -> Dict[str, Any]:
    doc = _load_candidates()
    rows = [
        row
        for row in (doc.get("candidates") or [])
        if str(row.get("bucket") or "") in {"success", "staff_wins", "full_send_info"}
    ][: max(1, min(20, int(limit or 8)))]
    return {"count": len(rows), "wins": rows}


def explain_claim_sources(*, run_id: str) -> Dict[str, Any]:
    return explain_sources(run=load_run(run_id))


def log_audit(*, run: Dict[str, Any], event: str, details: Dict[str, Any]) -> Dict[str, Any]:
    return {"run_id": run.get("run_id"), "event": event, "details": details}


TOOL_REGISTRY = {
    "list_story_candidates": list_story_candidates,
    "get_story_context": get_story_context,
    "get_post_assets": get_post_assets,
    "describe_images": describe_images,
    "search_related_context": search_related_context,
    "load_marketing_memory": load_memory_tool,
    "draft_content": draft_content,
    "generate_draft": generate_draft,
    "critique_content": critique_content,
    "critique_draft": critique_draft,
    "revise_content": revise_content,
    "apply_feedback": apply_feedback,
    "store_feedback": store_feedback,
    "explain_sources": explain_sources,
    "explain_claim_sources": explain_claim_sources,
    "queue_content": queue_content,
    "queue_revision": queue_revision,
    "publish_content": publish_content,
    "publish_approved_post": publish_approved_post,
    "remember_style_rule": remember_style_rule,
    "search_past_wins": search_past_wins,
    "log_audit": log_audit,
}


def call_tool(run: Optional[Dict[str, Any]], name: str, **kwargs: Any) -> Any:
    if name not in TOOL_REGISTRY:
        raise ValueError(f"Unknown agent tool: {name}")
    result = TOOL_REGISTRY[name](**kwargs)
    if run is not None:
        run_id = str(run.get("run_id") or "")
        current = load_run(run_id) if run_id else run
        append_tool_call(current, name, kwargs, result)
    return result
