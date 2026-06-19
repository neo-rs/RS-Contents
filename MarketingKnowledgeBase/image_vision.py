"""Auditable OpenAI vision summaries for Discord proof screenshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from MarketingKnowledgeBase.model_router import resolve_model
from MarketingKnowledgeBase.openai_client import OpenAIResponsesClient
from MarketingKnowledgeBase.secrets import openai_api_key

_BASE = Path(__file__).resolve().parent
_VISION_ATTEMPTS = _BASE / "data" / "vision_attempts.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def _record_attempt(row: Dict[str, Any]) -> None:
    doc = _read_json(_VISION_ATTEMPTS) or {"version": 1, "attempts": []}
    attempts = list(doc.get("attempts") or [])
    attempts.insert(0, row)
    doc["attempts"] = attempts[:500]
    doc["updated_at"] = _now()
    _write_json(_VISION_ATTEMPTS, doc)


def _parse_sections(text: str) -> Dict[str, List[str]]:
    facts: List[str] = []
    uncertainty: List[str] = []
    current = ""
    for line in str(text or "").splitlines():
        stripped = line.strip(" -\t")
        upper = stripped.upper()
        if upper.startswith("VISIBLE"):
            current = "facts"
            continue
        if upper.startswith("UNCERTAINTY") or upper.startswith("DO NOT"):
            current = "uncertainty"
            continue
        if not stripped:
            continue
        if current == "facts":
            facts.append(stripped)
        elif current == "uncertainty":
            uncertainty.append(stripped)
    return {"visible_facts": facts[:20], "uncertainty_notes": uncertainty[:20]}


def describe_proof_images_detailed(
    image_urls: List[str],
    *,
    context: str = "",
    source_message_id: str = "",
    max_images: int = 4,
) -> Dict[str, Any]:
    """Return image understanding with status, errors, and logged audit data."""
    urls = [str(u or "").strip() for u in image_urls if str(u or "").strip()][: max(1, int(max_images))]
    routing = resolve_model(task="vision_summary", grounding_chars=len(context))
    model = routing.get("model") or "gpt-4o"
    row: Dict[str, Any] = {
        "created_at": _now(),
        "source_message_id": str(source_message_id or ""),
        "image_urls": urls,
        "model": model,
        "routing": routing,
        "ok": False,
        "summary": "",
        "visible_facts": [],
        "uncertainty_notes": [],
        "error": "",
    }
    if not urls:
        row["error"] = "no image URLs available"
        _record_attempt(row)
        return row

    api_key = openai_api_key()
    if not api_key:
        row["error"] = "missing OpenAI API key"
        _record_attempt(row)
        return row

    instructions = (
        "You read Discord proof screenshots for RS marketing. Only state what is visible. "
        "Separate visible facts from uncertainty. Do not invent prices, profit, market value, "
        "member names, or sell-through."
    )
    prompt = (
        "Describe the attached proof images in compact bullets.\n"
        "Use these exact section headers: VISIBLE FACTS, UNCERTAINTY, DO NOT CLAIM.\n\n"
        f"Source context:\n{context[:1800]}"
    )
    result = OpenAIResponsesClient(api_key=api_key, timeout_s=90).responses_text(
        model=model,
        instructions=instructions,
        input_text=prompt,
        image_urls=urls,
        reasoning_effort="medium",
        max_output_tokens=500,
        fallback_to_chat=True,
    )
    row["endpoint"] = result.endpoint
    row["usage"] = result.usage
    if result.ok:
        row["ok"] = True
        row["summary"] = result.text
        row.update(_parse_sections(result.text))
    else:
        row["error"] = result.error or "OpenAI returned no image summary"
    _record_attempt(row)
    return row


def describe_proof_images(image_urls: List[str], *, context: str = "") -> Optional[str]:
    """Backward-compatible plain summary used by existing writer code."""
    result = describe_proof_images_detailed(image_urls, context=context, max_images=2)
    return str(result.get("summary") or "").strip() if result.get("ok") else None
