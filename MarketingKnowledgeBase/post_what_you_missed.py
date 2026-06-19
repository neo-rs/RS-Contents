"""CLI: generate + publish a #what-you-missed preview post."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from MarketingKnowledgeBase.post_publisher import publish_marketing_draft
from MarketingKnowledgeBase.discord_log import post_marketing_generation_audit
from MarketingKnowledgeBase.feedback import record_review_post
from MarketingKnowledgeBase.post_history import record_story_usage
from MarketingKnowledgeBase.what_you_missed_post import build_what_you_missed_post


def _load_config() -> dict:
    path = Path(__file__).resolve().parent / "config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and publish what-you-missed post")
    parser.add_argument("--story-id", default=None, help="Optional story_id; default = top candidate")
    parser.add_argument("--dry-run", action="store_true", help="Generate only; do not post to Discord")
    parser.add_argument("--model", default=None, help="Optional OpenAI model id override for dry-run/model comparison")
    parser.add_argument("--no-audit-log", action="store_true", help="Skip generation/token audit log")
    parser.add_argument("--audit-channel-id", default=None, help="Override audit log channel id")
    parser.add_argument("--no-record-history", action="store_true", help="Do not record selected story in post_history.json")
    parser.add_argument(
        "--channel-id",
        default=None,
        help="Override Discord channel (default: configured review channel)",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="Post to production #what-you-missed instead of neo preview",
    )
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    cfg = _load_config()
    pub = cfg.get("publishing") or {}
    waitlist_id = int(pub.get("waitlist_channel_id") or 0)
    if waitlist_id <= 0:
        raise SystemExit("publishing.waitlist_channel_id is not configured in MarketingKnowledgeBase/config.json")

    if args.channel_id:
        channel_id = int(args.channel_id)
    elif args.production:
        channel_id = int(cfg.get("what_you_missed_channel_id") or 0)
    else:
        channel_id = int(pub.get("review_channel_id") or pub.get("neo_test_preview_channel_id") or 0)

    if channel_id <= 0:
        raise SystemExit("No target channel_id resolved.")

    draft = build_what_you_missed_post(
        story_id=args.story_id,
        waitlist_channel_id=waitlist_id,
        model_override=args.model,
    )

    result: dict = {"draft": draft, "channel_id": channel_id}
    posted = None
    if not args.dry_run:
        posted = publish_marketing_draft(draft, channel_id=channel_id, dry_run=False)
        guild_key = "production_guild_id" if args.production else "review_guild_id"
        guild_id = int(pub.get(guild_key) or pub.get("neo_test_guild_id") or 0)
        if posted.get("message_id") and guild_id:
            posted["url"] = f"https://discord.com/channels/{guild_id}/{channel_id}/{posted['message_id']}"
        if not args.production:
            record_review_post(posted=posted, draft=draft, slot="manual", channel_id=channel_id)
        result["posted"] = posted

    if not args.no_record_history:
        history_row = record_story_usage(
            draft=draft,
            mode="dry_run" if args.dry_run else "posted",
            channel_id=channel_id,
            posted=posted,
        )
        draft["post_history_recorded"] = True
        result["post_history"] = history_row

    if not args.no_audit_log:
        audit_channel_id = int(args.audit_channel_id or pub.get("neo_test_preview_channel_id") or 0)
        if audit_channel_id > 0:
            try:
                post_marketing_generation_audit(channel_id=audit_channel_id, draft=draft, posted=posted)
                result["audit_log"] = {"channel_id": audit_channel_id, "posted": True}
            except Exception as exc:
                result["audit_log"] = {"channel_id": audit_channel_id, "posted": False, "error": str(exc)[:300]}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
