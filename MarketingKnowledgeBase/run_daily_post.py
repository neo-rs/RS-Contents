"""Scheduled what-you-missed post runner (morning + evening EST windows)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from MarketingKnowledgeBase.post_publisher import publish_marketing_draft
from MarketingKnowledgeBase.feedback import record_review_post
from MarketingKnowledgeBase.what_you_missed_post import build_what_you_missed_post


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _slot_key(now: datetime, hour: int) -> str:
    return f"{now.date().isoformat()}:h{hour}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run scheduled marketing post if due")
    parser.add_argument("--force", action="store_true", help="Post even if slot already ran today")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--production", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    cfg_path = Path(__file__).resolve().parent / "config.json"
    cfg = _load_json(cfg_path)
    pub = cfg.get("publishing") or {}
    sched = pub.get("schedule") or {}
    if not sched.get("enabled", True) and not args.force:
        print(json.dumps({"skipped": True, "reason": "schedule disabled"}))
        return 0

    tz_name = str(sched.get("timezone") or "America/New_York")
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    morning = int(sched.get("morning_hour") or 9)
    evening = int(sched.get("evening_hour") or 22)
    hour = now.hour
    if hour not in (morning, evening) and not args.force:
        print(
            json.dumps(
                {
                    "skipped": True,
                    "reason": f"not in post window (hour={hour}, windows={morning},{evening})",
                }
            )
        )
        return 0

    slot_hour = morning if hour == morning or (args.force and hour < 12) else evening
    state_path = Path(__file__).resolve().parent / "data" / "daily_post_state.json"
    state = _load_json(state_path)
    key = _slot_key(now, slot_hour)
    if state.get("last_slot") == key and not args.force:
        print(json.dumps({"skipped": True, "reason": "slot already posted", "slot": key}))
        return 0

    waitlist_id = int(pub.get("waitlist_channel_id") or 0)
    if waitlist_id <= 0:
        raise SystemExit("publishing.waitlist_channel_id missing")

    if args.production:
        channel_id = int(cfg.get("what_you_missed_channel_id") or 0)
    else:
        channel_id = int(pub.get("review_channel_id") or pub.get("neo_test_preview_channel_id") or 0)

    draft = build_what_you_missed_post(waitlist_channel_id=waitlist_id)
    result: dict = {"slot": key, "channel_id": channel_id, "draft": draft}
    if not args.dry_run:
        posted = publish_marketing_draft(draft, channel_id=channel_id, dry_run=False)
        result["posted"] = posted
        if not args.production:
            record_review_post(posted=posted, draft=draft, slot=key, channel_id=channel_id)
        state["last_slot"] = key
        state["last_posted_at"] = now.isoformat()
        state["last_message_url"] = posted.get("url")
        _save_json(state_path, state)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
