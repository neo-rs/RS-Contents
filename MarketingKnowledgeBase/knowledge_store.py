"""JSON knowledge store with retention caps for Oracle-safe growth."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _entry_ts(entry: Dict[str, Any]) -> datetime:
    ts = _parse_ts(entry.get("posted_at")) or _parse_ts(entry.get("synced_at"))
    return ts or datetime.min.replace(tzinfo=timezone.utc)


class KnowledgeStore:
    def __init__(self, data_dir: Path, storage_cfg: Dict[str, Any]) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage_cfg = storage_cfg
        self.live_path = self.data_dir / "live_context.json"
        self.sync_state_path = self.data_dir / "sync_state.json"

    def load_live(self) -> Dict[str, Any]:
        if not self.live_path.exists():
            return {
                "version": 1,
                "last_sync_at": None,
                "buckets": {},
                "stats": {"total_entries": 0, "approx_bytes": 0},
            }
        with open(self.live_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_live(self, doc: Dict[str, Any]) -> None:
        doc["stats"]["approx_bytes"] = len(
            json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        tmp = self.live_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        tmp.replace(self.live_path)

    def load_sync_state(self) -> Dict[str, Any]:
        if not self.sync_state_path.exists():
            return {"channels": {}}
        with open(self.sync_state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_sync_state(self, state: Dict[str, Any]) -> None:
        tmp = self.sync_state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(self.sync_state_path)

    def prune_bucket(
        self,
        bucket: str,
        entries: List[Dict[str, Any]],
        *,
        retention_days: int,
        max_entries: int,
    ) -> List[Dict[str, Any]]:
        cutoff = _utc_now() - timedelta(days=max(1, retention_days))
        kept: List[Dict[str, Any]] = []
        for entry in entries:
            ts = _entry_ts(entry)
            if ts >= cutoff:
                kept.append(entry)
        kept.sort(key=_entry_ts, reverse=True)

        by_channel: Dict[str, List[Dict[str, Any]]] = {}
        for entry in kept:
            cid = str(entry.get("channel_id") or "")
            by_channel.setdefault(cid, []).append(entry)

        per_channel_cap = int(self.storage_cfg.get("max_entries_per_channel") or 25)
        channel_trimmed: List[Dict[str, Any]] = []
        for channel_entries in by_channel.values():
            channel_entries.sort(key=_entry_ts, reverse=True)
            channel_trimmed.extend(channel_entries[:per_channel_cap])

        channel_trimmed.sort(key=_entry_ts, reverse=True)
        return channel_trimmed[:max_entries]

    def apply_retention(self, doc: Dict[str, Any], retention_by_bucket: Dict[str, int]) -> Dict[str, Any]:
        max_per_bucket = int(self.storage_cfg.get("max_entries_per_bucket") or 150)
        buckets = doc.get("buckets") or {}
        total = 0
        for bucket, payload in buckets.items():
            entries = payload.get("entries") if isinstance(payload, dict) else []
            if not isinstance(entries, list):
                entries = []
            days = int(retention_by_bucket.get(bucket) or 7)
            pruned = self.prune_bucket(bucket, entries, retention_days=days, max_entries=max_per_bucket)
            buckets[bucket] = {
                "label": payload.get("label") if isinstance(payload, dict) else bucket,
                "entries": pruned,
            }
            total += len(pruned)
        doc["buckets"] = buckets
        doc["stats"] = {"total_entries": total, "approx_bytes": 0}
        return self._enforce_total_bytes(doc)

    def _enforce_total_bytes(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        max_bytes = int(self.storage_cfg.get("max_total_bytes") or 5_242_880)
        while True:
            encoded = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) <= max_bytes:
                doc["stats"]["approx_bytes"] = len(encoded)
                return doc
            removed = self._drop_oldest_entry(doc)
            if not removed:
                doc["stats"]["approx_bytes"] = len(encoded)
                return doc

    def _drop_oldest_entry(self, doc: Dict[str, Any]) -> bool:
        buckets = doc.get("buckets") or {}
        oldest_bucket: Optional[str] = None
        oldest_entry_idx: Optional[int] = None
        oldest_ts = datetime.max.replace(tzinfo=timezone.utc)
        for bucket, payload in buckets.items():
            entries = payload.get("entries") or []
            for idx, entry in enumerate(entries):
                ts = _entry_ts(entry)
                if ts <= oldest_ts:
                    oldest_ts = ts
                    oldest_bucket = bucket
                    oldest_entry_idx = idx
        if oldest_bucket is None or oldest_entry_idx is None:
            return False
        entries = buckets[oldest_bucket]["entries"]
        del entries[oldest_entry_idx]
        return True

    def save_story_candidates(self, doc: Dict[str, Any]) -> None:
        path = self.data_dir / "story_candidates.json"
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def merge_bucket_entries(
        self,
        doc: Dict[str, Any],
        bucket: str,
        label: str,
        new_entries: List[Dict[str, Any]],
    ) -> None:
        buckets = doc.setdefault("buckets", {})
        payload = buckets.setdefault(bucket, {"label": label, "entries": []})
        existing = {str(e.get("message_id")): e for e in payload.get("entries") or [] if e.get("message_id")}
        for entry in new_entries:
            mid = str(entry.get("message_id") or "")
            if not mid:
                continue
            existing[mid] = entry
        merged = list(existing.values())
        merged.sort(key=_entry_ts, reverse=True)
        payload["label"] = label
        payload["entries"] = merged
