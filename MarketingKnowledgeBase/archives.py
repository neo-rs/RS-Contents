"""Daily and weekly marketing knowledge archive helpers."""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo

from MarketingKnowledgeBase.knowledge_store import KnowledgeStore
from MarketingKnowledgeBase.story_candidates import build_story_candidates

_BASE = Path(__file__).resolve().parent


def tz(name: str = "America/New_York") -> ZoneInfo:
    return ZoneInfo(name)


def eastern_today(tz_name: str = "America/New_York") -> date:
    return datetime.now(tz(tz_name)).date()


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def day_bounds(day: date, tz_name: str = "America/New_York") -> Tuple[datetime, datetime]:
    zone = tz(tz_name)
    start = datetime.combine(day, time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


def day_folder_name(day: date) -> str:
    return f"{day.strftime('%A').lower()}-{day.strftime('%m-%d-%y')}"


def week_folder_name(start_day: date) -> str:
    end_day = start_day + timedelta(days=6)
    return f"weeklydata-{start_day.strftime('%m-%d')}-{end_day.strftime('%d')}"


def daily_dir(data_dir: Path, day: date) -> Path:
    return data_dir / "daily" / day_folder_name(day)


def weekly_dir(data_dir: Path, start_day: date) -> Path:
    return data_dir / "weekly" / week_folder_name(start_day)


def date_range(start_day: date, end_day: date) -> List[date]:
    days: List[date] = []
    cur = start_day
    while cur <= end_day:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def backfill_days(today: date | None = None) -> Dict[str, List[date]]:
    current = today or eastern_today()
    current_start = week_start(current)
    previous_start = current_start - timedelta(days=7)
    return {
        "last_full_week": date_range(previous_start, previous_start + timedelta(days=6)),
        "current_week": date_range(current_start, current),
    }


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_story_candidates_for_doc(store: KnowledgeStore, doc: Dict[str, Any]) -> Dict[str, Any]:
    candidates = build_story_candidates(doc)
    store.save_story_candidates(candidates)
    return candidates


def aggregate_docs(docs: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"version": 1, "last_sync_at": None, "buckets": {}, "stats": {"total_entries": 0, "approx_bytes": 0}}
    latest_sync = ""
    for doc in docs:
        if str(doc.get("last_sync_at") or "") > latest_sync:
            latest_sync = str(doc.get("last_sync_at") or "")
        for bucket, payload in (doc.get("buckets") or {}).items():
            if not isinstance(payload, dict):
                continue
            target = out["buckets"].setdefault(bucket, {"label": payload.get("label") or bucket, "entries": []})
            existing = {str(row.get("message_id")): row for row in target.get("entries") or [] if row.get("message_id")}
            for row in payload.get("entries") or []:
                mid = str(row.get("message_id") or "")
                if mid:
                    existing[mid] = row
            target["entries"] = sorted(existing.values(), key=lambda x: str(x.get("posted_at") or ""), reverse=True)
    out["last_sync_at"] = latest_sync or None
    total = sum(len((payload or {}).get("entries") or []) for payload in out["buckets"].values())
    out["stats"] = {"total_entries": total, "approx_bytes": len(json.dumps(out, ensure_ascii=False).encode("utf-8"))}
    return out


def write_weekly_archive(data_dir: Path, start_day: date, days: List[date], storage_cfg: Dict[str, Any]) -> Dict[str, Any]:
    docs = []
    for day in days:
        path = daily_dir(data_dir, day) / "live_context.json"
        if path.exists():
            docs.append(load_json(path))
    doc = aggregate_docs(docs)
    out_dir = weekly_dir(data_dir, start_day)
    store = KnowledgeStore(out_dir, storage_cfg)
    store.save_live(doc)
    candidates = save_story_candidates_for_doc(store, doc)
    return {"path": str(out_dir), "entries": (doc.get("stats") or {}).get("total_entries"), "candidates": len(candidates.get("candidates") or [])}


def prune_archives(data_dir: Path, keep_days: List[date], keep_week_starts: List[date]) -> None:
    daily_root = data_dir / "daily"
    keep_daily_names = {day_folder_name(day) for day in keep_days}
    if daily_root.exists():
        for child in daily_root.iterdir():
            if child.is_dir() and child.name not in keep_daily_names:
                shutil.rmtree(child, ignore_errors=True)

    weekly_root = data_dir / "weekly"
    keep_week_names = {week_folder_name(day) for day in keep_week_starts}
    if weekly_root.exists():
        for child in weekly_root.iterdir():
            if child.is_dir() and child.name not in keep_week_names:
                shutil.rmtree(child, ignore_errors=True)


def preferred_candidate_paths(data_dir: Path, tz_name: str = "America/New_York") -> List[Path]:
    today = eastern_today(tz_name)
    yesterday = today - timedelta(days=1)
    current_start = week_start(today)
    return [
        daily_dir(data_dir, today) / "story_candidates.json",
        daily_dir(data_dir, yesterday) / "story_candidates.json",
        weekly_dir(data_dir, current_start) / "story_candidates.json",
    ]
