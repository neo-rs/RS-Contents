"""Auto model routing for MarketingKnowledgeBase AI writer (Cursor-style)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from MarketingKnowledgeBase.secrets import load_secrets

_BASE_DEFAULTS = {
    "fast": "gpt-4o-mini",
    "balanced": "gpt-4o",
    "quality": "gpt-5.5",
    "pro": "gpt-5.5-pro",
}

# Welcome + close days need stronger persuasion; story-anchored days stay fast.
_WELCOME_CLOSE_DAYS = frozenset({"day_1", "day_7a", "day_7b"})
_LONG_CONTEXT_CHARS = 3500


def _ai_writer_config() -> Dict[str, Any]:
    try:
        from pathlib import Path
        import json

        path = Path(__file__).resolve().parent / "config.json"
        if path.exists():
            cfg = json.loads(path.read_text(encoding="utf-8"))
            block = cfg.get("ai_writer")
            if isinstance(block, dict):
                return block
    except Exception:
        pass
    return {}


def _tier_models() -> Dict[str, str]:
    cfg = _ai_writer_config()
    models = dict(_BASE_DEFAULTS)
    overrides = cfg.get("models")
    if isinstance(overrides, dict):
        for tier in ("fast", "balanced", "quality", "pro"):
            val = str(overrides.get(tier) or "").strip()
            if val:
                models[tier] = val
    return models


def _configured_default() -> str:
    secrets = load_secrets()
    for key in ("openai_model", "OPENAI_MODEL"):
        val = str(secrets.get(key) or "").strip()
        if val:
            return val
    cfg = _ai_writer_config()
    return str(cfg.get("default_model") or "auto").strip() or "auto"


def _is_auto(value: str) -> bool:
    return str(value or "").strip().lower() in ("auto", "automatic", "default")


def _bump_tier(tier: str) -> str:
    if tier == "fast":
        return "balanced"
    if tier == "balanced":
        return "quality"
    return "quality"


def resolve_model(
    *,
    task: str,
    day_key: Optional[str] = None,
    grounding_chars: int = 0,
    model_override: Optional[str] = None,
) -> Dict[str, str]:
    """Pick OpenAI model for a task. Returns model id + tier + reason."""
    models = _tier_models()
    requested = str(model_override or _configured_default()).strip()

    if requested and not _is_auto(requested):
        return {
            "model": requested,
            "tier": "manual",
            "reason": f"Fixed model from config: {requested}",
            "mode": "manual",
        }

    task = str(task or "").strip().lower()
    tier = "balanced"
    reason = "Default balanced tier"

    if task in ("generate_marketing_copy", "marketing_copy"):
        tier = "balanced"
        reason = "Marketing post — balanced quality for voice + grounded facts"
    elif task in ("vision_summary", "image_vision", "proof_image_summary"):
        tier = "quality"
        reason = "Proof image understanding — quality multimodal tier"
    elif task in ("generate_dm_day_copy", "dm_day_copy"):
        dk = str(day_key or "").strip()
        if dk in _WELCOME_CLOSE_DAYS:
            tier = "balanced"
            reason = f"{dk} — welcome/close copy needs stronger persuasion"
        else:
            tier = "fast"
            reason = f"{dk or 'dm day'} — story-anchored short DM (speed tier)"
    elif task in ("rewrite_dm_sequence", "dm_sequence"):
        tier = "fast"
        reason = "Full sequence — fast tier per day (8 calls)"
    else:
        tier = "balanced"
        reason = f"Unknown task {task!r} — balanced fallback"

    if grounding_chars >= _LONG_CONTEXT_CHARS:
        bumped = _bump_tier(tier)
        reason += f"; bumped {tier}->{bumped} (large grounding {grounding_chars} chars)"
        tier = bumped

    model = models.get(tier) or models["balanced"]
    return {
        "model": model,
        "tier": tier,
        "reason": reason,
        "mode": "auto",
    }


def resolve_model_for_stage(
    *,
    workflow_type: str,
    stage: str,
    grounding_chars: int = 0,
    model_override: Optional[str] = None,
) -> Dict[str, str]:
    """Pick a model by workflow stage for agentic content automation."""
    cfg = _ai_writer_config()
    stage_overrides = cfg.get("models_by_stage") or cfg.get("model_by_stage") or {}
    requested = str(model_override or "").strip()
    if requested and not _is_auto(requested):
        return {
            "model": requested,
            "tier": "manual",
            "reason": f"Fixed model override for {workflow_type}/{stage}: {requested}",
            "mode": "manual",
        }
    if isinstance(stage_overrides, dict):
        val = str(stage_overrides.get(stage) or "").strip()
        if val:
            return {
                "model": val,
                "tier": stage,
                "reason": f"Configured stage model for {workflow_type}/{stage}",
                "mode": "stage",
            }

    models = _tier_models()
    stage_key = str(stage or "").strip().lower()
    workflow = str(workflow_type or "").strip().lower()
    if stage_key in {"extract", "classify", "command_parse", "summarize"}:
        tier = "fast"
    elif stage_key in {"draft", "rewrite"}:
        tier = "balanced"
    elif stage_key in {"reason", "critique", "repair", "final", "vision", "tool_orchestration"}:
        tier = "quality"
    elif stage_key in {"campaign_strategy", "hard_repair"}:
        tier = "pro"
    else:
        tier = "balanced"

    if workflow.startswith("ghl_") and stage_key in {"draft", "rewrite"}:
        tier = "balanced"
    if grounding_chars >= _LONG_CONTEXT_CHARS and tier in {"fast", "balanced"}:
        tier = _bump_tier(tier)

    return {
        "model": models.get(tier) or models["balanced"],
        "tier": tier,
        "reason": f"Stage routing for {workflow_type}/{stage} ({grounding_chars} chars)",
        "mode": "stage",
    }
