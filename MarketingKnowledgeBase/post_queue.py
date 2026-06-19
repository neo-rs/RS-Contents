"""Approval queue for marketing drafts (JSON-only, capped)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

MAX_QUEUE_ITEMS = 20


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostQueue:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "items": []}
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, doc: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def list_items(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        doc = self.load()
        items = doc.get("items") or []
        if status:
            return [i for i in items if str(i.get("status")) == status]
        return items

    def add_draft(self, draft: Dict[str, Any]) -> Dict[str, Any]:
        doc = self.load()
        items: List[Dict[str, Any]] = list(doc.get("items") or [])
        item = dict(draft)
        item.setdefault("queue_id", str(uuid4()))
        item.setdefault("status", "pending")
        item.setdefault("queued_at", _utc_now())
        items.insert(0, item)
        items = items[:MAX_QUEUE_ITEMS]
        doc["items"] = items
        self.save(doc)
        return item

    def update_status(self, queue_id: str, status: str, *, note: str = "") -> Optional[Dict[str, Any]]:
        doc = self.load()
        items: List[Dict[str, Any]] = list(doc.get("items") or [])
        updated: Optional[Dict[str, Any]] = None
        for item in items:
            if str(item.get("queue_id")) == queue_id:
                item["status"] = status
                item["updated_at"] = _utc_now()
                if note:
                    item["note"] = note
                updated = item
                break
        if updated:
            doc["items"] = items
            self.save(doc)
        return updated

    def get_item(self, queue_id: str) -> Optional[Dict[str, Any]]:
        for item in self.list_items():
            if str(item.get("queue_id")) == queue_id:
                return item
        return None
