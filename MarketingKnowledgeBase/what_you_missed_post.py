"""Generate and publish #what-you-missed style posts."""

from __future__ import annotations

from typing import Any, Dict, Optional

from MarketingKnowledgeBase.archives import preferred_candidate_paths
from MarketingKnowledgeBase.ai_writer import generate_marketing_copy
from MarketingKnowledgeBase.what_you_missed_sourcing import pick_what_you_missed_story


def _waitlist_mention(channel_id: int) -> str:
    return f"<#{int(channel_id)}>"


def ensure_waitlist_cta(body: str, waitlist_channel_id: int) -> str:
    """Append waitlist channel mention if missing from post body."""
    text = str(body or "").strip()
    cid = str(int(waitlist_channel_id))
    if cid in text or _waitlist_mention(waitlist_channel_id) in text:
        return text
    return (
        f"{text}\n\n"
        f"Ready for full access? Get on the waitlist: {_waitlist_mention(waitlist_channel_id)}."
    ).strip()


def _has_waitlist_reference(body: str, waitlist_channel_id: int) -> bool:
    cid = str(int(waitlist_channel_id))
    return cid in body or _waitlist_mention(waitlist_channel_id) in body


def pick_top_story_id(candidates_doc: Dict[str, Any], *, bucket: Optional[str] = None) -> str:
    """Canonical story picker for what-you-missed (admin hype, not monitor bots)."""
    if bucket:
        from MarketingKnowledgeBase.what_you_missed_sourcing import filter_eligible_candidates

        rows = [r for r in filter_eligible_candidates(candidates_doc) if r.get("bucket") == bucket]
        if rows and rows[0].get("story_id"):
            return str(rows[0]["story_id"])
        raise RuntimeError(f"No eligible story in bucket {bucket!r}. Run MarketingKnowledgeBase.sync first.")

    cand, mode = pick_what_you_missed_story(candidates_doc)
    if cand and cand.get("story_id"):
        return str(cand["story_id"])
    raise RuntimeError(
        "No eligible what-you-missed stories. Sync admin channels + staff wins, then retry."
    )


def build_what_you_missed_post(
    *,
    story_id: Optional[str] = None,
    candidates_doc: Optional[Dict[str, Any]] = None,
    waitlist_channel_id: int,
    extra_instructions: str = "",
    model_override: Optional[str] = None,
) -> Dict[str, Any]:
    from pathlib import Path
    import json

    base = Path(__file__).resolve().parent
    archive_source = "provided"
    if candidates_doc is None:
        cfg_path = base / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        tz_name = str(((cfg.get("publishing") or {}).get("schedule") or {}).get("timezone") or "America/New_York")
        candidates_doc = {"candidates": []}
        archive_source = "none"
        for path in preferred_candidate_paths(base / "data", tz_name):
            if not path.exists():
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            cand_probe, _ = pick_what_you_missed_story(doc, story_id=story_id)
            if cand_probe:
                candidates_doc = doc
                archive_source = str(path)
                break

    cand, mode = pick_what_you_missed_story(candidates_doc, story_id=story_id)
    if not cand:
        raise RuntimeError(
            "Nothing worth posting from daily/weekly archive candidates. "
            "Run MarketingKnowledgeBase.sync --scheduled-current-archive and ensure important/full-send-info/staff wins have recent posts."
        )

    sid = str(cand.get("story_id") or story_id or pick_top_story_id(candidates_doc))
    waitlist_note = (
        f"End with a CTA that includes the waitlist channel mention exactly: "
        f"{_waitlist_mention(waitlist_channel_id)}"
    )
    mode_hint = ""
    if mode == "wins_fallback" or str(cand.get("bucket")) == "staff_wins":
        mode_hint = (
            "Write WIN OF THE DAY style hype: highlight the member win, profit numbers from grounding, "
            "and community energy. Tag the vibe of staff wins channel posts."
        )
    elif mode == "primary":
        mode_hint = (
            "This is a what-you-missed recap of an admin deal post members on the free server missed. "
            "Hype the deal in plain language. No monitor bot jargon."
        )

    merged_extra = f"{waitlist_note}\n{mode_hint}\n{extra_instructions}".strip()

    draft = generate_marketing_copy(
        story_id=sid,
        target_channel="what-you-missed",
        extra_instructions=merged_extra,
        post_mode=mode,
        candidates_doc=candidates_doc,
        model_override=model_override,
    )
    body = ensure_waitlist_cta(str(draft.get("body_markdown") or ""), waitlist_channel_id)
    draft["body_markdown"] = body
    draft["waitlist_channel_id"] = waitlist_channel_id
    draft["waitlist_included"] = _has_waitlist_reference(body, waitlist_channel_id)
    draft["sourcing_mode"] = mode
    draft["source_bucket"] = cand.get("bucket")
    draft["archive_source"] = archive_source
    return draft
