"""Canonical agent-backed what-you-missed review posting flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

from MarketingKnowledgeBase.agent.state import load_run, save_run
from MarketingKnowledgeBase.agent.workflow import agent_generate
from MarketingKnowledgeBase.archives import preferred_candidate_paths
from MarketingKnowledgeBase.discord_log import _post_discord_payload, post_marketing_generation_audit
from MarketingKnowledgeBase.feedback import record_review_post
from MarketingKnowledgeBase.post_history import record_story_usage
from MarketingKnowledgeBase.post_publisher import publish_marketing_draft
from MarketingKnowledgeBase.what_you_missed_post import pick_top_story_id

BASE = Path(__file__).resolve().parent


def _load_config() -> Dict[str, Any]:
    path = BASE / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _pick_archive_story(cfg: Dict[str, Any], *, story_id: str = "") -> Tuple[str, str]:
    tz_name = str(((cfg.get("publishing") or {}).get("schedule") or {}).get("timezone") or "America/New_York")
    for path in preferred_candidate_paths(BASE / "data", tz_name):
        if not path.exists():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        try:
            picked = str(story_id or pick_top_story_id(doc))
            if picked:
                return picked, str(path)
        except Exception:
            continue
    raise RuntimeError("No eligible story found in daily/weekly archive candidates.")


def review_controls_payload(run_id: str, *, intro: str = "Draft ready for review. Use the buttons below or reply with feedback.") -> Dict[str, Any]:
    content = (
        f"{intro}\n\n"
        "AI Review Controls\n"
        f"Run: `{run_id}`\n\n"
        "Buttons:\n"
        "- Status: current run state and validation\n"
        "- Explain: sources, proof, memory, and claim status\n"
        "- Revise: asks what to change, then posts a new draft\n"
        "- Remember: asks what rule to save for future drafts\n"
        "- Approve: marks this run approved\n"
        "- Publish: asks for confirmation before live posting\n"
        "- Reject: asks why and stores the rejection reason\n\n"
        "You can also reply in this channel, like: `less hype, more proof`."
    )
    return {
        "content": content[:1900],
        "allowed_mentions": {"parse": []},
        "components": [
            {
                "type": 1,
                "components": [
                    {"type": 2, "style": 2, "label": "Status", "custom_id": "mkb_review_status"},
                    {"type": 2, "style": 2, "label": "Explain", "custom_id": "mkb_review_explain"},
                    {"type": 2, "style": 1, "label": "Revise", "custom_id": "mkb_review_revise"},
                    {"type": 2, "style": 1, "label": "Remember", "custom_id": "mkb_review_remember"},
                    {"type": 2, "style": 3, "label": "Approve", "custom_id": "mkb_review_approve"},
                ],
            },
            {
                "type": 1,
                "components": [
                    {"type": 2, "style": 3, "label": "Publish", "custom_id": "mkb_review_publish"},
                    {"type": 2, "style": 4, "label": "Reject", "custom_id": "mkb_review_reject"},
                ],
            },
        ],
    }


def post_review_controls(*, channel_id: int, run_id: str, intro: str = "") -> None:
    payload = review_controls_payload(run_id, intro=intro or "Draft ready for review. Use the buttons below or reply with feedback.")
    _post_discord_payload(channel_id=int(channel_id), payload=payload, label="agent review controls")


def generate_and_post_agent_review(
    *,
    requested_by: str = "scheduler",
    trigger: str = "scheduled_review",
    story_id: str = "",
    channel_id: int = 0,
    post_controls: bool = True,
    record_history: bool = True,
    audit: bool = True,
    slot: str = "",
) -> Dict[str, Any]:
    cfg = _load_config()
    pub = cfg.get("publishing") or {}
    agent_cfg = cfg.get("agent") or {}
    waitlist_channel_id = int(pub.get("waitlist_channel_id") or 0)
    review_channel_id = int(channel_id or agent_cfg.get("review_channel_id") or pub.get("review_channel_id") or 0)
    if review_channel_id <= 0:
        raise RuntimeError("Marketing agent review_channel_id is not configured.")

    picked_story_id, candidate_source = _pick_archive_story(cfg, story_id=story_id)
    extra = (
        f"End with a CTA that includes the waitlist channel mention exactly: <#{waitlist_channel_id}>.\n"
        "This is a what-you-missed review draft for staff. Keep it grounded, conversational, and not overly hype."
    )
    result = agent_generate(
        story_id=picked_story_id,
        destination_id="discord_what_you_missed",
        requested_by=requested_by,
        target_channel=str(review_channel_id),
        extra_instructions=extra,
    )
    run = result.get("run") or {}
    run_id = str(run.get("run_id") or "")
    draft = result.get("draft") or {}
    draft["archive_source"] = draft.get("archive_source") or candidate_source
    draft["trigger"] = trigger
    posted = publish_marketing_draft(draft, channel_id=review_channel_id, dry_run=False)
    if run_id:
        run = load_run(run_id)
        run.setdefault("metadata", {})["review_message_id"] = str(posted.get("message_id") or "")
        run.setdefault("metadata", {})["review_message_url"] = str(posted.get("url") or "")
        run.setdefault("metadata", {})["candidate_source"] = candidate_source
        run.setdefault("metadata", {})["trigger"] = trigger
        save_run(run)

    controls_posted = False
    if post_controls and run_id:
        post_review_controls(channel_id=review_channel_id, run_id=run_id)
        controls_posted = True

    history_row: Dict[str, Any] = {}
    if record_history:
        if trigger != "manual_review":
            record_review_post(posted=posted, draft=draft, slot=slot or trigger, channel_id=review_channel_id)
        history_row = record_story_usage(
            draft=draft,
            mode=trigger,
            channel_id=review_channel_id,
            posted=posted,
        )
        draft["post_history_recorded"] = True

    audit_result: Dict[str, Any] = {}
    if audit:
        audit_channel_id = int(pub.get("neo_test_preview_channel_id") or 0)
        if audit_channel_id > 0:
            try:
                post_marketing_generation_audit(channel_id=audit_channel_id, draft=draft, posted=posted)
                audit_result = {"channel_id": audit_channel_id, "posted": True}
            except Exception as exc:
                audit_result = {"channel_id": audit_channel_id, "posted": False, "error": str(exc)[:300]}

    return {
        "ok": True,
        "run_id": run_id,
        "story_id": picked_story_id,
        "candidate_source": candidate_source,
        "review_channel_id": review_channel_id,
        "review_message_url": posted.get("url"),
        "posted": posted,
        "controls_posted": controls_posted,
        "validation": result.get("validation") or {},
        "tool_summary": result.get("tool_summary") or [],
        "post_history": history_row,
        "audit_log": audit_result,
    }
