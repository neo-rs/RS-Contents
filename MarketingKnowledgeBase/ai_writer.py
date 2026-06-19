"""Grounded OpenAI copywriter for RS marketing + DM sequence."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from MarketingKnowledgeBase.channel_display import channel_mention, sanitize_channel_display_names
from MarketingKnowledgeBase.draft_composer import _asset_urls, compose_marketing_draft
from MarketingKnowledgeBase.marketing_memory import load_marketing_memory, memory_prompt
from MarketingKnowledgeBase.model_router import resolve_model
from MarketingKnowledgeBase.secrets import load_secrets, openai_api_key
from MarketingKnowledgeBase.story_research import build_research_pack, research_prompt
from MarketingKnowledgeBase.story_candidates import find_candidate
from MarketingKnowledgeBase.writing_rules import (
    apply_writing_rules_postprocess,
    build_rules_prompt,
    validate_output,
)

_BASE = Path(__file__).resolve().parent
_DAY_KEYS = ["day_1", "day_2", "day_3", "day_4", "day_5", "day_6", "day_7a", "day_7b"]

_DM_DAY_THEMES = {
    "day_1": "Welcome — what RS membership includes (monitors, schedules, success culture). No specific stale deal.",
    "day_2": "Member success / profit proof from synced stories.",
    "day_3": "Important channel deal or glitch (Amazon/retail) from synced stories.",
    "day_4": "Sneaker drop or SNKRS win from synced stories.",
    "day_5": "Another member success haul with proof image.",
    "day_6": "In-store / flip / monitor lead from synced stories.",
    "day_7a": "Membership urgency — waitlist / limited spots. Do not invent fake inventory counts unless in offers.",
    "day_7b": "Final follow-up — last chance waitlist CTA.",
}


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _pick_image_url(candidate: Optional[Dict[str, Any]]) -> Optional[str]:
    if not candidate:
        return None
    for asset in _asset_urls(candidate):
        url = str(asset.get("url") or "").strip()
        if url:
            return url
    return None


def _sanitize_grounding_text(text: str) -> str:
    out = re.sub(r"https?://\S+", "[link omitted]", str(text or ""))
    out = re.sub(r"zephr\.app\S*", "[monitor link omitted]", out, flags=re.I)
    return out


def _story_brief(candidate: Dict[str, Any], *, include_source_link: bool = False) -> str:
    ch_id = candidate.get("channel_id")
    ch_ref = channel_mention(ch_id) if ch_id else "member channels"
    parts = [
        f"bucket={candidate.get('bucket')}",
        f"channel_ref={ch_ref}",
        f"posted_at={candidate.get('posted_at')}",
        f"text={_sanitize_grounding_text(str(candidate.get('text') or ''))[:1200]}",
    ]
    amounts = candidate.get("dollar_amounts") or []
    if amounts:
        parts.append(f"amounts={', '.join(amounts)}")
    hints = candidate.get("headline_hints") or []
    if hints:
        parts.append(f"headline_hints={'; '.join(hints[:3])}")
    facts = candidate.get("deal_facts") or {}
    if facts:
        compact_facts = {
            key: facts.get(key)
            for key in (
                "title",
                "price",
                "retail_or_msrp",
                "market_value",
                "price_delta_pct",
                "promo_code",
                "store_or_platform",
                "urgency_hints",
            )
            if facts.get(key) not in (None, "", [])
        }
        if compact_facts:
            parts.append(f"deal_facts={json.dumps(compact_facts, ensure_ascii=False)}")
    link = candidate.get("message_link")
    if link and include_source_link:
        parts.append(f"source={link}")
    vision_note = candidate.get("vision_summary")
    if vision_note:
        parts.append(f"proof_image_summary={vision_note}")
    return "\n".join(parts)


def _maybe_attach_vision(candidate: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _read_json(_BASE / "config.json") or {}
    vision_cfg = cfg.get("vision") or {}
    if not vision_cfg.get("enabled", True):
        return candidate
    if not vision_cfg.get("use_for_success_attachments", True):
        return candidate
    bucket = str(candidate.get("bucket") or "")
    if bucket not in ("success", "full_send_info", "staff_wins", "important", "important_instore", "important_trading_cards"):
        return candidate
    urls = [str(a.get("url") or "") for a in (candidate.get("attachments") or []) if a.get("url")]
    if not urls:
        urls = [str(i.get("url") or "") for i in (candidate.get("embed_images") or []) if i.get("url")]
    if not urls:
        return candidate
    from MarketingKnowledgeBase.image_vision import describe_proof_images

    summary = describe_proof_images(urls, context=_story_brief({**candidate, "vision_summary": ""}))
    if summary:
        out = dict(candidate)
        out["vision_summary"] = summary
        return out
    return candidate


def _pick_dm_stories(candidates_doc: Dict[str, Any]) -> Dict[str, Optional[Dict[str, Any]]]:
    items = list(candidates_doc.get("candidates") or [])

    def by_bucket(bucket: str, limit: int = 8) -> List[Dict[str, Any]]:
        return [i for i in items if i.get("bucket") == bucket][:limit]

    success = by_bucket("success")
    important = by_bucket("important")
    sneakers = by_bucket("sneakers")

    def first_with_image(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for row in rows:
            if _pick_image_url(row):
                return row
        return rows[0] if rows else None

    return {
        "day_1": None,
        "day_2": first_with_image(success) or first_with_image(important),
        "day_3": first_with_image(important) or first_with_image(success),
        "day_4": first_with_image(sneakers) or first_with_image(success),
        "day_5": success[1] if len(success) > 1 else first_with_image(success),
        "day_6": sneakers[1] if len(sneakers) > 1 else first_with_image(sneakers),
        "day_7a": None,
        "day_7b": None,
    }


def _build_grounding_context(
    *,
    story_id: Optional[str] = None,
    bucket: Optional[str] = None,
    limit: int = 8,
    candidates_doc: Optional[Dict[str, Any]] = None,
    memory: Optional[Dict[str, Any]] = None,
    research_pack: Optional[Dict[str, Any]] = None,
) -> str:
    voice = _read_json(_BASE / "voice.json") or {}
    offers = _read_json(_BASE / "offers.json") or {}
    candidates_doc = candidates_doc or {"candidates": []}

    blocks: List[str] = [
        "VOICE RULES:",
        *[f"- {r}" for r in (voice.get("voice_rules") or [])],
        "",
        "AVOID:",
        ", ".join(voice.get("avoid_phrases") or []),
        "",
        "VALUE PROPS:",
        *[f"- {v}" for v in (offers.get("value_props") or [])],
        "",
        "DO NOT CLAIM:",
        *[f"- {d}" for d in ((offers.get("products") or [{}])[0].get("do_not_claim") or [])],
    ]

    try:
        from MarketingKnowledgeBase.feedback import load_approved_examples

        examples = load_approved_examples(limit=4)
    except Exception:
        examples = []
    if examples:
        blocks.extend(["", "APPROVED WHAT-YOU-MISSED STYLE EXAMPLES:"])
        for ex in examples:
            text = _sanitize_grounding_text(str(ex.get("text") or ""))[:900]
            if text:
                blocks.append(f"- approved_by={','.join(ex.get('approved_by') or [])} text={text}")

    memory = memory if memory is not None else load_marketing_memory(auto_refresh=True)
    memory_block = memory_prompt(memory)
    if memory_block:
        blocks.extend(["", memory_block])

    if story_id:
        cand = find_candidate(candidates_doc, story_id)
        if cand:
            blocks.extend(["", "PRIMARY STORY:", _story_brief(cand)])
            pack = research_pack if research_pack is not None else build_research_pack(cand)
            blocks.extend(["", research_prompt(pack)])
    elif bucket:
        rows = [c for c in (candidates_doc.get("candidates") or []) if c.get("bucket") == bucket][:limit]
        if rows:
            blocks.append("")
            blocks.append(f"STORIES ({bucket}):")
            for row in rows:
                blocks.append(_story_brief(row))
                blocks.append("---")
    else:
        blocks.append("")
        blocks.append("TOP STORY CANDIDATES:")
        for row in (candidates_doc.get("candidates") or [])[:limit]:
            blocks.append(_story_brief(row))
            blocks.append("---")

    return "\n".join(blocks)


def _build_story_angle(candidate: Dict[str, Any], research_pack: Dict[str, Any]) -> str:
    facts = candidate.get("deal_facts") or {}
    title = str(facts.get("title") or (candidate.get("headline_hints") or [""])[0] or "this deal").strip()
    price = str(facts.get("price") or "").strip()
    market = str(facts.get("market_value") or facts.get("resale_value") or "").strip()
    retail = str(facts.get("retail_or_msrp") or "").strip()
    delta = facts.get("price_delta_pct")
    stores = facts.get("store_or_platform") or []
    related_count = int(research_pack.get("related_count") or 0)

    if price and market:
        angle = f"{title} turned into a {price} entry with {market} market/resale context"
    elif price and retail and delta is not None:
        angle = f"{title} was posted at {price}, roughly {delta}% under {retail}"
    elif "free" in " ".join(facts.get("urgency_hints") or []).lower() and market:
        angle = f"{title} framed as a free/local pickup opportunity with {market} market value"
    elif price:
        angle = f"{title} was a time-sensitive member alert at {price}"
    else:
        angle = f"{title} was a member-only alert worth framing as missed timing"

    if stores:
        angle += f" via {', '.join(str(s) for s in stores[:3])}"
    if related_count:
        angle += f"; related archive context found {related_count} supporting message(s)"
    return angle[:500]


def _openai_chat(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    temperature: float = 0.7,
    task: str = "unknown",
    day_key: Optional[str] = None,
) -> Tuple[str, Dict[str, str]]:
    api_key = openai_api_key()
    if not api_key:
        raise RuntimeError(
            "Missing OpenAI API key. Create MarketingKnowledgeBase/config.secrets.json "
            "with openai_api_key, or set OPENAI_API_KEY."
        )

    routing = resolve_model(
        task=task,
        day_key=day_key,
        grounding_chars=len(system) + len(user),
        model_override=model,
    )
    chosen_model = routing["model"]

    payload = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if not str(chosen_model).lower().startswith("gpt-5"):
        payload["temperature"] = temperature
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error {exc.code}: {detail}") from exc

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI returned no choices: {body}")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI returned empty content")
    routing["usage"] = body.get("usage") or {}
    return content.strip(), routing


def _strip_code_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()


def generate_marketing_copy(
    *,
    story_id: str,
    target_channel: str = "what-you-missed",
    extra_instructions: str = "",
    post_mode: str = "primary",
    candidates_doc: Optional[Dict[str, Any]] = None,
    model_override: Optional[str] = None,
) -> Dict[str, Any]:
    if candidates_doc is None:
        raise ValueError("generate_marketing_copy requires archive-backed candidates_doc; root story_candidates fallback is disabled")
    candidate = find_candidate(candidates_doc, story_id)
    if not candidate:
        raise ValueError(f"Unknown story_id: {story_id}")
    candidate = _maybe_attach_vision(candidate)
    memory = load_marketing_memory(auto_refresh=True)
    research_pack = build_research_pack(candidate)
    story_angle = _build_story_angle(candidate, research_pack)

    voice = _read_json(_BASE / "voice.json") or {}
    offers = _read_json(_BASE / "offers.json") or {}
    skeleton = compose_marketing_draft(
        candidate, voice=voice, offers=offers, target_channel=target_channel
    )
    grounding = _build_grounding_context(
        story_id=story_id,
        candidates_doc=candidates_doc,
        memory=memory,
        research_pack=research_pack,
    )
    rules_block = build_rules_prompt(
        context="marketing_copy",
        waitlist_channel_id=int(
            (_read_json(_BASE / "config.json") or {}).get("publishing", {}).get("waitlist_channel_id") or 0
        )
        or None,
    )

    system = (
        "You write Discord marketing copy for Reselling Secrets (#what-you-missed style). "
        "Use ONLY facts from the grounding context. Never invent products, prices, or wins. "
        "Output plain Discord chat text (no embeds). Use **bold** for headline hook, not # markdown. "
        "NEVER include any URL in the output text — no http links, no zephr, no message links. "
        "No JSON, no preamble.\n\n"
        f"{rules_block}"
    )
    wins_note = ""
    if post_mode == "wins_fallback" or str(candidate.get("bucket")) == "staff_wins":
        wins_note = (
            "\nSTYLE: WIN OF THE DAY — celebrate a specific member win with profit proof. "
            "Example energy: 'I want to highlight @member's success... paid around $X, cashed out $Y... LETS STAY COOKING'"
        )
    user = (
        f"{grounding}\n\n"
        f"NARRATIVE ANGLE:\n{story_angle}\n\n"
        f"SKELETON DRAFT:\n{skeleton.get('body_markdown')}\n\n"
        f"Target channel: {target_channel}\n"
        f"Write final post copy: ALL CAPS headline hook (1-2 emojis max), "
        f"short body, urgency, waitlist CTA. ZERO URLs in the text.{wins_note}\n"
        f"{extra_instructions}".strip()
    )
    copy, routing = _openai_chat(
        system=system,
        user=user,
        task="generate_marketing_copy",
        model=model_override,
    )
    copy = apply_writing_rules_postprocess(_strip_code_fence(copy), context="marketing_copy")
    copy = sanitize_channel_display_names(copy, channel_id=candidate.get("channel_id"))
    violations = validate_output(copy, context="marketing_copy")
    return {
        "story_id": story_id,
        "target_channel": target_channel,
        "headline": skeleton.get("headline"),
        "body_markdown": copy,
        "reuse_assets": skeleton.get("reuse_assets") or [],
        "source_message_link": candidate.get("message_link"),
        "post_mode": post_mode,
        "source_bucket": candidate.get("bucket"),
        "candidate_score": candidate.get("score"),
        "wym_score": candidate.get("wym_score"),
        "deal_facts": candidate.get("deal_facts") or {},
        "story_angle": story_angle,
        "research_related_count": research_pack.get("related_count", 0),
        "research_source_files": research_pack.get("source_paths") or [],
        "memory_used": bool(memory),
        "memory_source_counts": memory.get("source_counts") or {},
        "vision_used": bool(candidate.get("vision_summary")),
        "market_enrichment_used": False,
        "grounding_rule": "Facts must match synced Discord activity only.",
        "model_routing": routing,
        "rule_violations": violations,
    }


def generate_dm_day_copy(
    day_key: str,
    *,
    story_id: Optional[str] = None,
    current_description: str = "",
) -> Dict[str, Any]:
    day_key = str(day_key or "").strip()
    if day_key not in _DAY_KEYS:
        raise ValueError(f"Invalid day_key: {day_key}")

    candidates_doc = _read_json(_BASE / "data" / "story_candidates.json") or {"candidates": []}
    picks = _pick_dm_stories(candidates_doc)
    candidate = find_candidate(candidates_doc, story_id) if story_id else picks.get(day_key)

    grounding = _build_grounding_context(
        story_id=str(candidate.get("story_id")) if candidate else None,
        limit=6,
    )
    theme = _DM_DAY_THEMES.get(day_key, "RS membership value")
    rules_block = build_rules_prompt(context="dm_sequence")

    system = (
        "You rewrite RSCheckerbot 7-day DM sequence messages for Discord embeds. "
        "Output ONLY the description field text — no JSON.\n\n"
        f"{rules_block}"
    )
    user = (
        f"DAY: {day_key}\nTHEME: {theme}\n\n"
        f"{grounding}\n\n"
        f"CURRENT (outdated — rewrite):\n{current_description}\n\n"
    )
    if candidate:
        user += f"ANCHOR STORY:\n{_story_brief(candidate)}\n\n"
    user += (
        "Write a fresh description (~3-6 short lines). "
        "End with a CTA link using {join_url}."
    )

    description, routing = _openai_chat(
        system=system,
        user=user,
        temperature=0.65,
        task="generate_dm_day_copy",
        day_key=day_key,
    )
    description = apply_writing_rules_postprocess(
        _strip_code_fence(description), context="dm_sequence"
    )
    if "{join_url}" not in description:
        description += "\n\n[JOIN THE WAITLIST NOW]({join_url})"
    violations = validate_output(description, context="dm_sequence")

    return {
        "day_key": day_key,
        "description": description,
        "suggested_main_image_url": _pick_image_url(candidate),
        "anchor_story_id": (candidate or {}).get("story_id"),
        "anchor_message_link": (candidate or {}).get("message_link"),
        "model_routing": routing,
        "rule_violations": violations,
    }


def generate_dm_sequence(
    *,
    messages_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    messages_path = messages_path or (_BASE.parent / "RSCheckerbot" / "messages.json")
    current = _read_json(messages_path)
    if not isinstance(current, dict):
        raise RuntimeError(f"Invalid messages file: {messages_path}")

    days = dict(current.get("days") or {})
    results: Dict[str, Any] = {}
    for day_key in _DAY_KEYS:
        prev = days.get(day_key) or {}
        prev_desc = str(prev.get("description") or "")
        generated = generate_dm_day_copy(day_key, current_description=prev_desc)
        updated = dict(prev)
        updated["description"] = generated["description"]
        img = generated.get("suggested_main_image_url")
        if img and day_key not in ("day_1", "day_7a", "day_7b"):
            updated["main_image_url"] = img
        days[day_key] = updated
        results[day_key] = generated

    out_doc = dict(current)
    out_doc["days"] = days

    if not dry_run:
        backup = messages_path.with_suffix(".json.bak")
        if messages_path.exists() and not backup.exists():
            backup.write_text(messages_path.read_text(encoding="utf-8"), encoding="utf-8")
        with open(messages_path, "w", encoding="utf-8") as f:
            json.dump(out_doc, f, indent=2, ensure_ascii=False)
            f.write("\n")

    return {
        "dry_run": dry_run,
        "messages_path": str(messages_path),
        "days": results,
        "updated_messages": out_doc if dry_run else None,
    }
