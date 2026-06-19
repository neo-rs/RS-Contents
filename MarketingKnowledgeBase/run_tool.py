"""CLI handlers for marketing-knowledge MCP tools (single source of truth)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from MarketingKnowledgeBase.ai_writer import generate_dm_day_copy, generate_dm_sequence, generate_marketing_copy
from MarketingKnowledgeBase.agent.workflow import (
    agent_explain,
    agent_generate,
    agent_handle_review_message,
    agent_list_runs,
    agent_publish,
    agent_remember,
    agent_revise,
    agent_show_run,
)
from MarketingKnowledgeBase.draft_composer import compose_marketing_draft
from MarketingKnowledgeBase.post_queue import PostQueue
from MarketingKnowledgeBase.story_candidates import find_candidate, find_candidate_by_message_id


def _base_dir() -> Path:
    return Path(__file__).resolve().parent


def _data_dir() -> Path:
    return _base_dir() / "data"


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_candidates() -> Dict[str, Any]:
    return _read_json(_data_dir() / "story_candidates.json") or {"candidates": []}


def _load_live_entry(message_id: str) -> Optional[Dict[str, Any]]:
    live = _read_json(_data_dir() / "live_context.json") or {}
    for payload in (live.get("buckets") or {}).values():
        for entry in payload.get("entries") or []:
            if str(entry.get("message_id")) == str(message_id):
                return entry
    return None


def cmd_list_story_candidates(args: argparse.Namespace) -> Dict[str, Any]:
    doc = _load_candidates()
    items = list(doc.get("candidates") or [])
    if args.bucket:
        items = [i for i in items if i.get("bucket") == args.bucket]
    limit = max(1, min(50, int(args.limit or 15)))
    return {
        "generated_at": doc.get("generated_at"),
        "count": len(items[:limit]),
        "candidates": items[:limit],
    }


def cmd_get_post_assets(args: argparse.Namespace) -> Dict[str, Any]:
    message_id = str(args.message_id or "").strip()
    if not message_id:
        raise ValueError("message_id is required")

    entry = _load_live_entry(message_id)
    candidates = _load_candidates()
    candidate = find_candidate_by_message_id(candidates, message_id)

    attachments = (entry or {}).get("attachments") or (candidate or {}).get("attachments") or []
    embed_images = (entry or {}).get("embed_images") or (candidate or {}).get("embed_images") or []
    return {
        "message_id": message_id,
        "message_link": (entry or candidate or {}).get("message_link"),
        "attachments": attachments,
        "embed_images": embed_images,
        "reuse_rule": "Attach these URLs in Discord posts. Do not invent replacement images.",
    }


def cmd_draft_marketing_post(args: argparse.Namespace) -> Dict[str, Any]:
    story_id = str(args.story_id or "").strip()
    if not story_id:
        raise ValueError("story_id is required")

    candidates = _load_candidates()
    candidate = find_candidate(candidates, story_id)
    if not candidate:
        raise ValueError(f"Unknown story_id: {story_id}")

    voice = _read_json(_base_dir() / "voice.json") or {}
    offers = _read_json(_base_dir() / "offers.json") or {}
    target = str(args.target_channel or "what-you-missed")
    return compose_marketing_draft(candidate, voice=voice, offers=offers, target_channel=target)


def cmd_queue_marketing_draft(args: argparse.Namespace) -> Dict[str, Any]:
    raw = args.draft_json or sys.stdin.read()
    draft = json.loads(raw)
    if not isinstance(draft, dict):
        raise ValueError("draft must be a JSON object")
    queue = PostQueue(_data_dir() / "post_queue.json")
    item = queue.add_draft(draft)
    return {"ok": True, "queued": item}


def cmd_list_post_queue(args: argparse.Namespace) -> Dict[str, Any]:
    queue = PostQueue(_data_dir() / "post_queue.json")
    status = str(args.status).strip() if args.status else None
    items = queue.list_items(status=status or None)
    return {"count": len(items), "items": items}


def cmd_update_post_queue_status(args: argparse.Namespace) -> Dict[str, Any]:
    queue_id = str(args.queue_id or "").strip()
    status = str(args.status or "").strip()
    if not queue_id or not status:
        raise ValueError("queue_id and status are required")
    queue = PostQueue(_data_dir() / "post_queue.json")
    updated = queue.update_status(queue_id, status, note=str(args.note or ""))
    if not updated:
        raise ValueError(f"queue_id not found: {queue_id}")
    return {"ok": True, "item": updated}


def cmd_generate_marketing_copy(args: argparse.Namespace) -> Dict[str, Any]:
    story_id = str(args.story_id or "").strip()
    if not story_id:
        raise ValueError("story_id is required")
    return generate_marketing_copy(
        story_id=story_id,
        target_channel=str(args.target_channel or "what-you-missed"),
        extra_instructions=str(args.extra_instructions or ""),
    )


def cmd_generate_dm_day_copy(args: argparse.Namespace) -> Dict[str, Any]:
    day_key = str(args.day or "").strip()
    if not day_key:
        raise ValueError("day is required")
    messages_path = Path(str(args.messages_path or (_REPO_ROOT / "RSCheckerbot" / "messages.json")))
    prev_desc = ""
    if messages_path.exists():
        doc = _read_json(messages_path) or {}
        prev = (doc.get("days") or {}).get(day_key) or {}
        prev_desc = str(prev.get("description") or "")
    return generate_dm_day_copy(
        day_key,
        story_id=str(args.story_id).strip() if args.story_id else None,
        current_description=prev_desc,
    )


def cmd_rewrite_dm_sequence(args: argparse.Namespace) -> Dict[str, Any]:
    messages_path = Path(str(args.messages_path or (_REPO_ROOT / "RSCheckerbot" / "messages.json")))
    return generate_dm_sequence(messages_path=messages_path, dry_run=bool(args.dry_run))


def cmd_publish_approved_post(args: argparse.Namespace) -> Dict[str, Any]:
    from MarketingKnowledgeBase.post_publisher import publish_marketing_draft

    queue_id = str(args.queue_id or "").strip()
    queue = PostQueue(_data_dir() / "post_queue.json")
    draft: Dict[str, Any]
    if queue_id:
        item = queue.get_item(queue_id)
        if not item:
            raise ValueError(f"queue_id not found: {queue_id}")
        if str(item.get("status") or "") != "approved" and not args.force:
            raise ValueError("Queue item must be approved (or use --force).")
        draft = item
    else:
        raw = args.draft_json or sys.stdin.read()
        draft = json.loads(raw)
    if not isinstance(draft, dict):
        raise ValueError("draft must be a JSON object")
    dry_run = not bool(args.live)
    result = publish_marketing_draft(
        draft,
        channel_id=int(args.channel_id) if args.channel_id else None,
        dry_run=dry_run,
    )
    if queue_id and args.live:
        queue.update_status(queue_id, "posted", note="published via marketing-knowledge MCP")
    return result


def cmd_agent_generate(args: argparse.Namespace) -> Dict[str, Any]:
    story_id = str(args.story_id or "").strip()
    if not story_id:
        raise ValueError("story_id is required")
    return agent_generate(
        story_id=story_id,
        destination_id=str(args.destination_id or "discord_what_you_missed"),
        requested_by=str(args.requested_by or "cli"),
        target_channel=str(args.target_channel or ""),
        extra_instructions=str(args.extra_instructions or ""),
        use_tool_orchestrator=not bool(args.deterministic),
    )


def cmd_agent_revise(args: argparse.Namespace) -> Dict[str, Any]:
    return agent_revise(
        run_id=str(args.run_id or "").strip(),
        feedback_text=str(args.feedback or ""),
        requested_by=str(args.requested_by or "cli"),
    )


def cmd_agent_explain(args: argparse.Namespace) -> Dict[str, Any]:
    return agent_explain(run_id=str(args.run_id or "").strip())


def cmd_agent_remember(args: argparse.Namespace) -> Dict[str, Any]:
    return agent_remember(
        run_id=str(args.run_id or "").strip(),
        rule_text=str(args.rule or ""),
        requested_by=str(args.requested_by or "cli"),
        scope=str(args.scope or "global_rs_memory"),
    )


def cmd_agent_publish(args: argparse.Namespace) -> Dict[str, Any]:
    return agent_publish(
        run_id=str(args.run_id or "").strip(),
        channel_id=int(args.channel_id) if args.channel_id else 0,
        requested_by=str(args.requested_by or "cli"),
    )


def cmd_agent_handle_review_message(args: argparse.Namespace) -> Dict[str, Any]:
    return agent_handle_review_message(
        run_id=str(args.run_id or "").strip(),
        message_text=str(args.message or ""),
        requested_by=str(args.requested_by or "cli"),
        channel_id=int(args.channel_id) if args.channel_id else 0,
    )


def cmd_agent_list_runs(args: argparse.Namespace) -> Dict[str, Any]:
    return agent_list_runs(limit=int(args.limit or 25))


def cmd_agent_show_run(args: argparse.Namespace) -> Dict[str, Any]:
    return agent_show_run(run_id=str(args.run_id or "").strip())


def cmd_agent_start_review(args: argparse.Namespace) -> Dict[str, Any]:
    from MarketingKnowledgeBase.agent.discord_review_agent import run_review_agent

    return run_review_agent(
        interval_s=int(args.interval or 10),
        limit=int(args.limit or 20),
        channel_id=int(args.channel_id) if args.channel_id else 0,
        once=bool(args.once),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Marketing knowledge MCP tool backend")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list_story_candidates")
    p_list.add_argument("--bucket", default=None)
    p_list.add_argument("--limit", type=int, default=15)

    p_assets = sub.add_parser("get_post_assets")
    p_assets.add_argument("--message-id", required=True)

    p_draft = sub.add_parser("draft_marketing_post")
    p_draft.add_argument("--story-id", required=True)
    p_draft.add_argument("--target-channel", default="what-you-missed")

    p_queue = sub.add_parser("queue_marketing_draft")
    p_queue.add_argument("--draft-json", default="")

    p_ql = sub.add_parser("list_post_queue")
    p_ql.add_argument("--status", default=None)

    p_qu = sub.add_parser("update_post_queue_status")
    p_qu.add_argument("--queue-id", required=True)
    p_qu.add_argument("--status", required=True)
    p_qu.add_argument("--note", default="")

    p_gen = sub.add_parser("generate_marketing_copy")
    p_gen.add_argument("--story-id", required=True)
    p_gen.add_argument("--target-channel", default="what-you-missed")
    p_gen.add_argument("--extra-instructions", default="")

    p_dm = sub.add_parser("generate_dm_day_copy")
    p_dm.add_argument("--day", required=True)
    p_dm.add_argument("--story-id", default=None)
    p_dm.add_argument("--messages-path", default="")

    p_rdm = sub.add_parser("rewrite_dm_sequence")
    p_rdm.add_argument("--dry-run", action="store_true")
    p_rdm.add_argument("--messages-path", default="")

    p_pub = sub.add_parser("publish_approved_post")
    p_pub.add_argument("--queue-id", default="")
    p_pub.add_argument("--draft-json", default="")
    p_pub.add_argument("--channel-id", default="")
    p_pub.add_argument("--dry-run", action="store_true", help="Preview only (default when neither flag set)")
    p_pub.add_argument("--live", action="store_true", help="Actually post to Discord")
    p_pub.add_argument("--force", action="store_true")

    p_ag = sub.add_parser("agent_generate")
    p_ag.add_argument("--story-id", required=True)
    p_ag.add_argument("--destination-id", default="discord_what_you_missed")
    p_ag.add_argument("--target-channel", default="")
    p_ag.add_argument("--extra-instructions", default="")
    p_ag.add_argument("--requested-by", default="cli")
    p_ag.add_argument("--deterministic", action="store_true")

    p_ar = sub.add_parser("agent_revise")
    p_ar.add_argument("--run-id", required=True)
    p_ar.add_argument("--feedback", required=True)
    p_ar.add_argument("--requested-by", default="cli")

    p_ax = sub.add_parser("agent_explain")
    p_ax.add_argument("--run-id", required=True)

    p_am = sub.add_parser("agent_remember")
    p_am.add_argument("--rule", required=True)
    p_am.add_argument("--run-id", default="")
    p_am.add_argument("--scope", default="global_rs_memory")
    p_am.add_argument("--requested-by", default="cli")

    p_ap = sub.add_parser("agent_publish")
    p_ap.add_argument("--run-id", required=True)
    p_ap.add_argument("--channel-id", default="")
    p_ap.add_argument("--requested-by", default="cli")

    p_ah = sub.add_parser("agent_handle_review_message")
    p_ah.add_argument("--run-id", required=True)
    p_ah.add_argument("--message", required=True)
    p_ah.add_argument("--requested-by", default="cli")
    p_ah.add_argument("--channel-id", default="")

    p_al = sub.add_parser("agent_list_runs")
    p_al.add_argument("--limit", type=int, default=25)

    p_as = sub.add_parser("agent_show_run")
    p_as.add_argument("--run-id", required=True)

    p_sr = sub.add_parser("agent_start_review")
    p_sr.add_argument("--once", action="store_true")
    p_sr.add_argument("--interval", type=int, default=10)
    p_sr.add_argument("--limit", type=int, default=20)
    p_sr.add_argument("--channel-id", default="")

    args = parser.parse_args(argv)
    handlers = {
        "list_story_candidates": cmd_list_story_candidates,
        "get_post_assets": cmd_get_post_assets,
        "draft_marketing_post": cmd_draft_marketing_post,
        "queue_marketing_draft": cmd_queue_marketing_draft,
        "list_post_queue": cmd_list_post_queue,
        "update_post_queue_status": cmd_update_post_queue_status,
        "generate_marketing_copy": cmd_generate_marketing_copy,
        "generate_dm_day_copy": cmd_generate_dm_day_copy,
        "rewrite_dm_sequence": cmd_rewrite_dm_sequence,
        "publish_approved_post": cmd_publish_approved_post,
        "agent_generate": cmd_agent_generate,
        "agent_revise": cmd_agent_revise,
        "agent_explain": cmd_agent_explain,
        "agent_remember": cmd_agent_remember,
        "agent_publish": cmd_agent_publish,
        "agent_handle_review_message": cmd_agent_handle_review_message,
        "agent_list_runs": cmd_agent_list_runs,
        "agent_show_run": cmd_agent_show_run,
        "agent_start_review": cmd_agent_start_review,
    }
    result = handlers[args.command](args)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
