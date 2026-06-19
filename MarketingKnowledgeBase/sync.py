"""CLI entrypoint: python -m MarketingKnowledgeBase.sync"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mirror_world_config import is_placeholder_secret, load_config_with_secrets

from MarketingKnowledgeBase.archives import (
    backfill_days,
    date_range,
    day_bounds,
    daily_dir,
    eastern_today,
    prune_archives,
    save_story_candidates_for_doc,
    week_start,
    write_weekly_archive,
)
from MarketingKnowledgeBase.discord_log import post_knowledge_sync_report
from MarketingKnowledgeBase.discord_sync import sync_discord_to_store
from MarketingKnowledgeBase.feedback import sync_approved_examples, sync_review_feedback
from MarketingKnowledgeBase.knowledge_store import KnowledgeStore
from MarketingKnowledgeBase.marketing_memory import refresh_marketing_memory
from MarketingKnowledgeBase.story_candidates import build_story_candidates


def _load_mkb_config() -> dict:
    base = Path(__file__).resolve().parent
    with open(base / "config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _bot_headers(cfg: dict) -> dict:
    bot_dir = _REPO_ROOT / str(cfg.get("bot_config_dir") or "RSAdminBot")
    merged, _, secrets_path = load_config_with_secrets(bot_dir)
    token = merged.get("bot_token")
    if is_placeholder_secret(token):
        raise RuntimeError(f"Missing RSAdminBot bot_token in {secrets_path}")
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json"}


def _copy_latest_daily_to_live(*, data_dir: Path, storage: dict, days: list) -> tuple[dict, dict]:
    live_store = KnowledgeStore(data_dir, storage)
    for day in reversed(days):
        doc_path = daily_dir(data_dir, day) / "live_context.json"
        cand_path = daily_dir(data_dir, day) / "story_candidates.json"
        if not doc_path.exists() or not cand_path.exists():
            continue
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        candidates = json.loads(cand_path.read_text(encoding="utf-8"))
        if ((doc.get("stats") or {}).get("total_entries") or 0) > 0:
            live_store.save_live(doc)
            live_store.save_story_candidates(candidates)
            return doc, candidates
    return live_store.load_live(), json.loads((data_dir / "story_candidates.json").read_text(encoding="utf-8")) if (data_dir / "story_candidates.json").exists() else {"candidates": []}


def _parse_day(value: str) -> date:
    try:
        return datetime.strptime(str(value or "").strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _sync_archive_day(
    *,
    cfg: dict,
    headers: dict,
    data_dir: Path,
    storage: dict,
    per_channel_limit: int,
    max_pages_per_channel: int,
    day: date,
    update_live: bool,
) -> tuple[dict, dict, dict]:
    tz_name = str(((cfg.get("publishing") or {}).get("schedule") or {}).get("timezone") or "America/New_York")
    start_at, end_at = day_bounds(day, tz_name)
    day_path = daily_dir(data_dir, day)
    day_store = KnowledgeStore(day_path, storage)
    doc, sync_state = sync_discord_to_store(
        cfg=cfg,
        store=day_store,
        headers=headers,
        per_channel_limit=per_channel_limit,
        start_at=start_at,
        end_at=end_at,
        max_pages_per_channel=max_pages_per_channel,
        apply_retention=False,
    )
    candidates = save_story_candidates_for_doc(day_store, doc)
    if update_live:
        live_store = KnowledgeStore(data_dir, storage)
        live_store.save_live(doc)
        live_store.save_story_candidates(candidates)
    summary = {
        "archive_mode": "day",
        "archive_day": day.isoformat(),
        "daily_folders": [
            {
                "date": day.isoformat(),
                "path": str(day_path),
                "entries": (doc.get("stats") or {}).get("total_entries"),
                "candidates": len(candidates.get("candidates") or []),
                "channels_synced": len((sync_state.get("channels") or {})),
            }
        ],
    }
    return doc, candidates, summary


def _build_archive_week(
    *,
    data_dir: Path,
    storage: dict,
    start_day: date,
    end_day: date,
    update_live: bool,
) -> tuple[dict, dict, dict]:
    days = date_range(start_day, end_day)
    result = write_weekly_archive(data_dir, week_start(start_day), days, storage)
    doc_path = Path(result["path"]) / "live_context.json"
    candidates_path = Path(result["path"]) / "story_candidates.json"
    doc = json.loads(doc_path.read_text(encoding="utf-8")) if doc_path.exists() else {"stats": {"total_entries": 0}, "buckets": {}}
    candidates = json.loads(candidates_path.read_text(encoding="utf-8")) if candidates_path.exists() else {"candidates": []}
    if update_live:
        _copy_latest_daily_to_live(data_dir=data_dir, storage=storage, days=days)
    summary = {
        "archive_mode": "week",
        "archive_week_start": start_day.isoformat(),
        "archive_week_end": end_day.isoformat(),
        "weekly_archives": [result],
    }
    return doc, candidates, summary


def _run_backfill(
    *,
    cfg: dict,
    headers: dict,
    data_dir: Path,
    storage: dict,
    per_channel_limit: int,
    max_pages_per_channel: int,
) -> tuple[dict, dict, dict]:
    tz_name = str(((cfg.get("publishing") or {}).get("schedule") or {}).get("timezone") or "America/New_York")
    day_groups = backfill_days()
    all_days = day_groups["last_full_week"] + day_groups["current_week"]
    daily_results = []

    for day in all_days:
        start_at, end_at = day_bounds(day, tz_name)
        day_store = KnowledgeStore(daily_dir(data_dir, day), storage)
        doc, sync_state = sync_discord_to_store(
            cfg=cfg,
            store=day_store,
            headers=headers,
            per_channel_limit=per_channel_limit,
            start_at=start_at,
            end_at=end_at,
            max_pages_per_channel=max_pages_per_channel,
            apply_retention=False,
        )
        candidates = save_story_candidates_for_doc(day_store, doc)
        daily_results.append(
            {
                "date": day.isoformat(),
                "path": str(daily_dir(data_dir, day)),
                "entries": (doc.get("stats") or {}).get("total_entries"),
                "candidates": len(candidates.get("candidates") or []),
                "channels_synced": len((sync_state.get("channels") or {})),
            }
        )

    previous_start = week_start(day_groups["last_full_week"][0])
    current_start = week_start(day_groups["current_week"][0])
    weekly_results = [
        write_weekly_archive(data_dir, previous_start, day_groups["last_full_week"], storage),
        write_weekly_archive(data_dir, current_start, day_groups["current_week"], storage),
    ]
    prune_archives(data_dir, all_days, [previous_start, current_start])
    latest_doc, latest_candidates = _copy_latest_daily_to_live(data_dir=data_dir, storage=storage, days=day_groups["current_week"])
    summary = {
        "backfill": "last-full-week-and-current-week",
        "daily_folders": daily_results,
        "weekly_archives": weekly_results,
        "total_daily_entries": sum(int(row.get("entries") or 0) for row in daily_results),
    }
    return latest_doc, latest_candidates, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync Discord server activity into marketing knowledge base.")
    parser.add_argument("--quiet", action="store_true", help="Only print JSON summary line.")
    parser.add_argument(
        "--per-channel-limit",
        type=int,
        default=None,
        help="Messages fetched per channel (default: config storage max_entries_per_channel).",
    )
    parser.add_argument("--no-discord-log", action="store_true", help="Skip Discord sync report logging.")
    parser.add_argument(
        "--backfill",
        choices=["none", "last-full-week-and-current-week"],
        default="none",
        help="Build daily/weekly date archives instead of only the latest live snapshot.",
    )
    parser.add_argument(
        "--date-window-max-pages",
        type=int,
        default=None,
        help="Max Discord message pages per channel for date-window backfill.",
    )
    parser.add_argument("--archive-day", type=_parse_day, default=None, help="Sync one Eastern date into its daily archive folder (YYYY-MM-DD).")
    parser.add_argument("--archive-week-start", type=_parse_day, default=None, help="Build weekly archive from existing daily folders, start date YYYY-MM-DD.")
    parser.add_argument("--archive-week-end", type=_parse_day, default=None, help="Build weekly archive from existing daily folders, end date YYYY-MM-DD.")
    parser.add_argument("--scheduled-current-archive", action="store_true", help="Archive current Eastern day, rebuild current week, and update live compatibility files.")
    parser.add_argument("--update-live", action="store_true", help="Update compatibility live_context/story_candidates from this archive operation.")
    parser.add_argument("--skip-feedback-sync", action="store_true", help="Skip review feedback/example reaction sync for day-by-day archive runs.")
    args = parser.parse_args(argv)

    cfg = _load_mkb_config()
    base = Path(__file__).resolve().parent
    storage = cfg.get("storage") or {}
    data_dir = base / str(storage.get("data_dir") or "data")
    store = KnowledgeStore(data_dir, storage)
    per_channel_limit = args.per_channel_limit or int(storage.get("max_entries_per_channel") or 25)

    headers = _bot_headers(cfg)
    feedback_summary = {}
    examples_summary = {}
    if not args.skip_feedback_sync:
        feedback_summary = sync_review_feedback(cfg, headers)
        examples_summary = sync_approved_examples(cfg, headers)

    archive_summary = {}
    if args.scheduled_current_archive:
        tz_name = str(((cfg.get("publishing") or {}).get("schedule") or {}).get("timezone") or "America/New_York")
        today = eastern_today(tz_name)
        current_start = week_start(today)
        max_pages = args.date_window_max_pages or int(storage.get("date_window_max_pages_per_channel") or 40)
        _sync_archive_day(
            cfg=cfg,
            headers=headers,
            data_dir=data_dir,
            storage=storage,
            per_channel_limit=per_channel_limit,
            max_pages_per_channel=max_pages,
            day=today,
            update_live=False,
        )
        doc, candidates, archive_summary = _build_archive_week(
            data_dir=data_dir,
            storage=storage,
            start_day=current_start,
            end_day=today,
            update_live=True,
        )
        archive_summary["archive_mode"] = "scheduled-current-archive"
        archive_summary["archive_day"] = today.isoformat()
        sync_state = store.load_sync_state()
    elif args.archive_day:
        max_pages = args.date_window_max_pages or int(storage.get("date_window_max_pages_per_channel") or 40)
        doc, candidates, archive_summary = _sync_archive_day(
            cfg=cfg,
            headers=headers,
            data_dir=data_dir,
            storage=storage,
            per_channel_limit=per_channel_limit,
            max_pages_per_channel=max_pages,
            day=args.archive_day,
            update_live=args.update_live,
        )
        sync_state = store.load_sync_state()
    elif args.archive_week_start:
        end_day = args.archive_week_end or (args.archive_week_start + timedelta(days=6))
        doc, candidates, archive_summary = _build_archive_week(
            data_dir=data_dir,
            storage=storage,
            start_day=args.archive_week_start,
            end_day=end_day,
            update_live=args.update_live,
        )
        sync_state = store.load_sync_state()
    elif args.backfill == "last-full-week-and-current-week":
        max_pages = args.date_window_max_pages or int(storage.get("date_window_max_pages_per_channel") or 40)
        doc, candidates, archive_summary = _run_backfill(
            cfg=cfg,
            headers=headers,
            data_dir=data_dir,
            storage=storage,
            per_channel_limit=per_channel_limit,
            max_pages_per_channel=max_pages,
        )
        sync_state = store.load_sync_state()
    else:
        doc, sync_state = sync_discord_to_store(
            cfg=cfg,
            store=store,
            headers=headers,
            per_channel_limit=per_channel_limit,
        )
        candidates = build_story_candidates(doc)
        store.save_story_candidates(candidates)

    summary = {
        "ok": True,
        "last_sync_at": doc.get("last_sync_at"),
        "total_entries": (doc.get("stats") or {}).get("total_entries"),
        "approx_bytes": (doc.get("stats") or {}).get("approx_bytes"),
        "channels_synced": len((sync_state.get("channels") or {})),
        "story_candidates": len(candidates.get("candidates") or []),
        **archive_summary,
        **feedback_summary,
        **examples_summary,
        "data_path": str(data_dir / "live_context.json"),
        "candidates_path": str(data_dir / "story_candidates.json"),
    }
    try:
        memory = refresh_marketing_memory(data_dir)
        summary["marketing_memory"] = (memory.get("source_counts") or {}).get("candidates")
        summary["marketing_memory_path"] = str(data_dir / "marketing_memory.json")
    except Exception as exc:
        summary["marketing_memory_error"] = str(exc)[:200]
    log_cfg = cfg.get("discord_logging") or {}
    if log_cfg.get("enabled", True) and not args.no_discord_log:
        channel_id = int(log_cfg.get("knowledge_data_logs_channel_id") or 0)
        try:
            post_knowledge_sync_report(
                channel_id=channel_id,
                summary=summary,
                live_doc=doc,
                candidates=candidates,
            )
            summary["discord_log"] = "posted"
        except Exception as exc:
            summary["discord_log"] = f"failed: {type(exc).__name__}: {str(exc)[:160]}"
    if args.quiet:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print("Marketing knowledge sync complete.")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
