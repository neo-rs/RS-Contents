"""Pull recent Discord activity into the marketing knowledge base."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from .discord_api import (
    extract_message_text,
    fetch_channel_messages,
    fetch_channel_messages_window,
    list_guild_channels,
    normalize_message_entry,
)
from .knowledge_store import KnowledgeStore


def _channel_map(channels: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for ch in channels:
        if not isinstance(ch, dict):
            continue
        try:
            cid = int(ch.get("id"))
        except (TypeError, ValueError):
            continue
        out[cid] = ch
    return out


def _text_channels_in_category(channels: List[Dict[str, Any]], category_id: int) -> List[int]:
    ids: List[int] = []
    for ch in channels:
        if str(ch.get("type")) not in {"0", "5"}:
            continue
        try:
            parent = int(ch.get("parent_id") or 0)
        except (TypeError, ValueError):
            parent = 0
        if parent == category_id:
            try:
                ids.append(int(ch["id"]))
            except (TypeError, ValueError, KeyError):
                pass
    return ids


def _channels_by_name_contains(all_channels: List[Dict[str, Any]], patterns: List[str]) -> List[int]:
    ids: List[int] = []
    pats = [str(p or "").lower() for p in patterns if str(p or "").strip()]
    for ch in all_channels:
        if str(ch.get("type")) not in {"0", "5"}:
            continue
        name = str(ch.get("name") or "").lower()
        if any(p in name for p in pats):
            try:
                ids.append(int(ch["id"]))
            except (TypeError, ValueError, KeyError):
                pass
    return ids


def _resolve_bucket_channels(
    *,
    bucket: str,
    spec: Dict[str, Any],
    all_channels: List[Dict[str, Any]],
    monitoring_max_channels: int,
) -> List[int]:
    channel_ids: Set[int] = set()
    for raw in spec.get("channel_ids") or []:
        channel_ids.add(int(raw))
    for raw in spec.get("extra_channel_ids") or []:
        channel_ids.add(int(raw))

    for pattern in spec.get("channel_name_contains") or []:
        channel_ids.update(_channels_by_name_contains(all_channels, [str(pattern)]))

    category_id = spec.get("category_id")
    if category_id:
        channel_ids.update(_text_channels_in_category(all_channels, int(category_id)))

    for raw in spec.get("category_ids") or []:
        channel_ids.update(_text_channels_in_category(all_channels, int(raw)))

    if bucket == "monitoring" and len(channel_ids) > monitoring_max_channels:
        # Keep channels with shortest names first (store monitors) then cap count.
        by_id = _channel_map(all_channels)
        ranked = sorted(
            channel_ids,
            key=lambda cid: str((by_id.get(cid) or {}).get("name") or ""),
        )
        channel_ids = set(ranked[:monitoring_max_channels])

    return sorted(channel_ids)


def sync_discord_to_store(
    *,
    cfg: Dict[str, Any],
    store: KnowledgeStore,
    headers: dict,
    per_channel_limit: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    max_pages_per_channel: int = 25,
    apply_retention: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    guild_id = int(cfg["guild_id"])
    sources = cfg.get("sources") or {}
    storage = cfg.get("storage") or {}
    monitoring_max = int(storage.get("monitoring_max_channels") or 35)
    retention = storage.get("retention_days_by_bucket") or {}

    all_channels = list_guild_channels(guild_id, headers)
    by_id = _channel_map(all_channels)
    doc = store.load_live()
    sync_state = store.load_sync_state()
    now_iso = datetime.now(timezone.utc).isoformat()
    progress_enabled = str(os.environ.get("MKB_SYNC_PROGRESS") or "").strip().lower() in {"1", "true", "yes"}
    window_label = ""
    if start_at and end_at:
        window_label = f" window={start_at.isoformat()}..{end_at.isoformat()}"

    for bucket, spec in sources.items():
        if not isinstance(spec, dict):
            continue
        label = str(spec.get("label") or bucket)
        channel_ids = _resolve_bucket_channels(
            bucket=bucket,
            spec=spec,
            all_channels=all_channels,
            monitoring_max_channels=monitoring_max,
        )
        new_entries: List[Dict[str, Any]] = []
        total_channels = len(channel_ids)
        if progress_enabled:
            print(f"SYNC_BUCKET_START bucket={bucket} label={label} channels={total_channels}{window_label}", flush=True)
        for idx, channel_id in enumerate(channel_ids, start=1):
            ch = by_id.get(channel_id) or {}
            channel_name = str(ch.get("name") or channel_id)
            if progress_enabled:
                print(
                    f"SYNC_CHANNEL_START bucket={bucket} channel={idx}/{total_channels} id={channel_id} name={channel_name}{window_label}",
                    flush=True,
                )
            try:
                if start_at and end_at:
                    messages = fetch_channel_messages_window(
                        channel_id,
                        headers,
                        start_at=start_at,
                        end_at=end_at,
                        max_pages=max_pages_per_channel,
                    )
                else:
                    messages = fetch_channel_messages(channel_id, headers, limit=per_channel_limit)
            except Exception as exc:
                sync_state.setdefault("channels", {})[str(channel_id)] = {
                    "bucket": bucket,
                    "channel_name": channel_name,
                    "last_error": str(exc)[:300],
                    "last_sync_at": now_iso,
                }
                if progress_enabled:
                    print(
                        f"SYNC_CHANNEL_ERROR bucket={bucket} channel={idx}/{total_channels} id={channel_id} name={channel_name} error={type(exc).__name__}: {str(exc)[:180]}",
                        flush=True,
                    )
                continue

            saved_count = 0
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                text = extract_message_text(msg)
                if not text and not msg.get("attachments"):
                    continue
                entry = normalize_message_entry(
                    msg=msg,
                    bucket=bucket,
                    channel_id=channel_id,
                    channel_name=channel_name,
                    guild_id=guild_id,
                )
                entry["synced_at"] = now_iso
                new_entries.append(entry)
                saved_count += 1

            sync_state.setdefault("channels", {})[str(channel_id)] = {
                "bucket": bucket,
                "channel_name": channel_name,
                "messages_fetched": len(messages),
                "date_window_start": start_at.isoformat() if start_at else None,
                "date_window_end": end_at.isoformat() if end_at else None,
                "last_sync_at": now_iso,
                "last_error": None,
            }
            if progress_enabled:
                print(
                    f"SYNC_CHANNEL_DONE bucket={bucket} channel={idx}/{total_channels} id={channel_id} name={channel_name} fetched={len(messages)} saved={saved_count}",
                    flush=True,
                )

        store.merge_bucket_entries(doc, bucket, label, new_entries)
        if progress_enabled:
            print(f"SYNC_BUCKET_DONE bucket={bucket} saved={len(new_entries)}", flush=True)

    doc["last_sync_at"] = now_iso
    if apply_retention:
        doc = store.apply_retention(doc, retention)
    else:
        doc["stats"] = {
            "total_entries": sum(
                len((payload or {}).get("entries") or [])
                for payload in (doc.get("buckets") or {}).values()
                if isinstance(payload, dict)
            ),
            "approx_bytes": 0,
        }
        doc = store._enforce_total_bytes(doc)
    store.save_live(doc)
    store.save_sync_state(sync_state)
    return doc, sync_state
