"""Structured draft validation and lightweight claim auditing."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from MarketingKnowledgeBase.writing_rules import validate_output

URL_RE = re.compile(r"https?://\S+", re.I)
MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
LIMITED_RE = re.compile(r"\b(limited spots?|spots? left|only \d+ spots?|last chance|closing soon)\b", re.I)
PROFIT_RE = re.compile(r"\b(profit|made|cashed out|sold for|resale|resell|market value)\b", re.I)
SOURCE_TERM_RE = re.compile(
    r"\b(polymarket|amazon|walmart|target|pokemon|pokémon|jurassic|amc|nike|stockx|ebay|gamestop|costco|best buy|microcenter)\b",
    re.I,
)


def extract_claims(text: str) -> List[Dict[str, Any]]:
    claims: List[Dict[str, Any]] = []
    for amount in MONEY_RE.findall(text or ""):
        claims.append(
            {
                "claim_text": amount,
                "claim_type": "money",
                "source_type": "",
                "source_id": "",
                "confidence": "unknown",
                "supported": False,
                "needs_human_review": True,
            }
        )
    if LIMITED_RE.search(text or ""):
        claims.append(
            {
                "claim_text": LIMITED_RE.search(text or "").group(0),
                "claim_type": "scarcity",
                "source_type": "",
                "source_id": "",
                "confidence": "unknown",
                "supported": False,
                "needs_human_review": True,
            }
        )
    if PROFIT_RE.search(text or ""):
        claims.append(
            {
                "claim_text": PROFIT_RE.search(text or "").group(0),
                "claim_type": "profit_or_resale",
                "source_type": "",
                "source_id": "",
                "confidence": "unknown",
                "supported": False,
                "needs_human_review": True,
            }
        )
    for match in SOURCE_TERM_RE.finditer(text or ""):
        term = match.group(1)
        if term.lower() in {"rs"}:
            continue
        claims.append(
            {
                "claim_text": term,
                "claim_type": "source_specific_term",
                "source_type": "",
                "source_id": "",
                "confidence": "unknown",
                "supported": False,
                "needs_human_review": True,
            }
        )
    return claims


def _source_haystack(*, story: Dict[str, Any], vision: Dict[str, Any], offers: Dict[str, Any]) -> str:
    parts = [
        str(story.get("text") or ""),
        str(story.get("deal_facts") or ""),
        str(story.get("dollar_amounts") or ""),
        str(vision.get("summary") or ""),
        str(vision.get("visible_facts") or ""),
        str(offers or ""),
    ]
    return "\n".join(parts).lower()


def validate_structured_draft(
    draft: Dict[str, Any],
    *,
    destination: Dict[str, Any],
    story: Dict[str, Any] | None = None,
    vision: Dict[str, Any] | None = None,
    offers: Dict[str, Any] | None = None,
    rejected: bool = False,
) -> Dict[str, Any]:
    story = story or {}
    vision = vision or {}
    offers = offers or {}
    text = str(draft.get("full_text") or draft.get("body_markdown") or draft.get("description") or "")
    issues = list(validate_output(text, context="marketing_copy"))
    fmt = destination.get("format_rules") or {}
    if not fmt.get("allow_urls", False) and URL_RE.search(text):
        issues.append("destination does not allow URLs")
    max_length = int(fmt.get("max_length") or 2000)
    if len(text) > max_length:
        issues.append(f"content exceeds destination max_length {max_length}")
    if rejected:
        issues.append("run has unresolved rejection feedback")
    if destination.get("publish_adapter") == "draft_only":
        issues.append("destination is draft-only; live publish is disabled")

    claims = list(draft.get("claims") or []) or extract_claims(text)
    haystack = _source_haystack(story=story, vision=vision, offers=offers)
    supported_claims: List[Dict[str, Any]] = []
    unsupported_claims: List[Dict[str, Any]] = []
    for claim in claims:
        row = dict(claim)
        claim_text = str(row.get("claim_text") or "")
        normalized = claim_text.lower().strip()
        supported = bool(normalized and normalized in haystack)
        if row.get("claim_type") == "scarcity" and "limited" not in haystack and "spots" not in haystack:
            supported = False
        row["supported"] = supported
        row["needs_human_review"] = not supported
        row["source_type"] = row.get("source_type") or ("source_or_vision" if supported else "")
        if supported:
            supported_claims.append(row)
        else:
            unsupported_claims.append(row)
    if unsupported_claims:
        issues.append("contains unsupported factual claims")

    ready = not issues
    return {
        "ready_to_publish": ready,
        "issues": issues,
        "claims": supported_claims + unsupported_claims,
        "unsupported_claims": unsupported_claims,
        "destination_id": destination.get("destination_id"),
        "validation_status": "ready" if ready else "blocked",
    }

