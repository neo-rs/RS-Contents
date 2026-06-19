"""Persistent run state for the RS content automation agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
RUNS = DATA / "agent_runs"
REVIEW_STATE = DATA / "agent_review_state.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def run_path(run_id: str) -> Path:
    safe = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in ("-", "_"))
    return RUNS / f"{safe}.json"


def new_run(
    *,
    workflow_type: str,
    requested_by: str = "",
    input_message: str = "",
    source_channel: str = "",
    target_channel: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    run = {
        "version": 1,
        "run_id": str(uuid4()),
        "workflow_type": workflow_type,
        "source_channel": str(source_channel or ""),
        "target_channel": str(target_channel or ""),
        "requested_by": str(requested_by or ""),
        "input_message": str(input_message or ""),
        "tool_calls": [],
        "drafts": [],
        "feedback_events": [],
        "validation_results": [],
        "publish_attempts": [],
        "final_output": None,
        "status": "created",
        "metadata": metadata or {},
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    save_run(run)
    return run


def save_run(run: Dict[str, Any]) -> Dict[str, Any]:
    run["updated_at"] = now_iso()
    write_json(run_path(str(run.get("run_id"))), run)
    return run


def load_run(run_id: str) -> Dict[str, Any]:
    run = read_json(run_path(run_id), {})
    if not isinstance(run, dict) or not run.get("run_id"):
        raise ValueError(f"Unknown agent run: {run_id}")
    return run


def list_runs(limit: int = 25) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not RUNS.exists():
        return rows
    for path in sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        doc = read_json(path, {})
        if isinstance(doc, dict):
            rows.append(
                {
                    "run_id": doc.get("run_id"),
                    "workflow_type": doc.get("workflow_type"),
                    "status": doc.get("status"),
                    "story_id": (doc.get("metadata") or {}).get("story_id"),
                    "target_channel": doc.get("target_channel"),
                    "created_at": doc.get("created_at"),
                    "updated_at": doc.get("updated_at"),
                    "draft_count": len(doc.get("drafts") or []),
                }
            )
        if len(rows) >= limit:
            break
    return rows


def set_active_review_run(run_id: str) -> None:
    doc = read_json(REVIEW_STATE, {"version": 1}) or {"version": 1}
    doc["active_run_id"] = str(run_id or "")
    doc["updated_at"] = now_iso()
    write_json(REVIEW_STATE, doc)


def get_active_review_run() -> str:
    doc = read_json(REVIEW_STATE, {}) or {}
    return str(doc.get("active_run_id") or "")


def get_review_last_seen(channel_id: int) -> str:
    doc = read_json(REVIEW_STATE, {}) or {}
    return str((doc.get("channels") or {}).get(str(channel_id), {}).get("last_seen_message_id") or "")


def set_review_last_seen(channel_id: int, message_id: str) -> None:
    doc = read_json(REVIEW_STATE, {"version": 1}) or {"version": 1}
    channels = doc.setdefault("channels", {})
    channels[str(channel_id)] = {"last_seen_message_id": str(message_id or ""), "updated_at": now_iso()}
    doc["updated_at"] = now_iso()
    write_json(REVIEW_STATE, doc)


def append_tool_call(run: Dict[str, Any], name: str, args: Dict[str, Any], result: Any) -> Dict[str, Any]:
    run.setdefault("tool_calls", []).append(
        {"name": name, "args": args, "result": result, "created_at": now_iso()}
    )
    return save_run(run)


def append_draft(run: Dict[str, Any], draft: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(draft)
    row.setdefault("draft_id", str(uuid4()))
    row.setdefault("created_at", now_iso())
    run.setdefault("drafts", []).append(row)
    run["final_output"] = row
    run["status"] = "drafted"
    return save_run(run)


def append_feedback(run: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(event)
    row.setdefault("created_at", now_iso())
    run.setdefault("feedback_events", []).append(row)
    return save_run(run)


def append_validation(run: Dict[str, Any], validation: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(validation)
    row.setdefault("created_at", now_iso())
    run.setdefault("validation_results", []).append(row)
    return save_run(run)


def latest_draft(run: Dict[str, Any]) -> Dict[str, Any]:
    drafts = run.get("drafts") or []
    return dict(drafts[-1]) if drafts else {}
