"""CLI: rewrite RSCheckerbot 7-day DM sequence using grounded AI writer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from MarketingKnowledgeBase.ai_writer import generate_dm_day_copy, generate_dm_sequence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rewrite RSCheckerbot DM sequence (grounded OpenAI)")
    parser.add_argument("--day", default=None, help="Single day key, e.g. day_2")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON only; do not write messages.json")
    parser.add_argument(
        "--messages-path",
        default=str(_REPO_ROOT / "RSCheckerbot" / "messages.json"),
        help="Path to messages.json",
    )
    parser.add_argument("--story-id", default=None, help="Force anchor story_id for --day")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")

    messages_path = Path(args.messages_path)
    if args.day:
        with open(messages_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        prev = (doc.get("days") or {}).get(args.day) or {}
        result = generate_dm_day_copy(
            args.day,
            story_id=args.story_id,
            current_description=str(prev.get("description") or ""),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    result = generate_dm_sequence(messages_path=messages_path, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
