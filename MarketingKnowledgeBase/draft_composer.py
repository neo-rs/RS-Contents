"""Compose grounded #what-you-missed style drafts from story candidates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _fact_bullets(candidate: Dict[str, Any], *, include_urls: bool = False) -> List[str]:
    bullets: List[str] = []
    text = str(candidate.get("text") or "").strip()
    if text:
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "http://" in s or "https://" in s or "zephr.app" in s.lower():
                continue
            if len(s) > 20:
                bullets.append(s[:280])
            if len(bullets) >= 5:
                break
    for amt in candidate.get("dollar_amounts") or []:
        bullets.append(f"Mentioned amount: {amt}")
    if include_urls:
        for url in (candidate.get("urls") or [])[:3]:
            bullets.append(f"Source link: {url}")
    return bullets[:8]


def _asset_urls(candidate: Dict[str, Any]) -> List[Dict[str, str]]:
    assets: List[Dict[str, str]] = []
    for att in candidate.get("attachments") or []:
        url = str(att.get("url") or "").strip()
        if url:
            assets.append(
                {
                    "type": "attachment",
                    "url": url,
                    "filename": str(att.get("filename") or ""),
                    "content_type": str(att.get("content_type") or ""),
                }
            )
    for img in candidate.get("embed_images") or []:
        url = str(img.get("url") or "").strip()
        if url:
            assets.append(
                {
                    "type": "embed_image",
                    "url": url,
                    "source": str(img.get("source") or "embed"),
                }
            )
    return assets


def compose_marketing_draft(
    candidate: Dict[str, Any],
    *,
    voice: Optional[Dict[str, Any]] = None,
    offers: Optional[Dict[str, Any]] = None,
    target_channel: str = "what-you-missed",
) -> Dict[str, Any]:
    voice = voice or {}
    offers = offers or {}
    text = str(candidate.get("text") or "")
    hints = candidate.get("headline_hints") or []
    headline = hints[0] if hints else _first_line(text) or "RS members are cooking"

    ctas = list(offers.get("ctas") or [])
    cta_block = "Stop missing out — join the waitlist if membership is closed."
    if ctas:
        cta_block = ctas[0]

    assets = _asset_urls(candidate)
    body_parts = [
        "Draft grounded in synced Discord activity. Do not invent products, prices, or wins not listed below.",
        "",
        "Suggested opening (edit for voice):",
        f"**{headline[:220]}**",
        "",
        "Facts you may reference:",
    ]
    for bullet in _fact_bullets(candidate, include_urls=False):
        body_parts.append(f"- {bullet}")

    body_parts.extend(
        [
            "",
            "Suggested CTA (from offers.json — edit as needed):",
            cta_block,
            "",
            "Voice reminders:",
        ]
    )
    for rule in (voice.get("voice_rules") or [])[:5]:
        body_parts.append(f"- {rule}")

    avoid = voice.get("avoid_phrases") or []
    if avoid:
        body_parts.append("")
        body_parts.append(f"Avoid phrases: {', '.join(avoid[:6])}")

    return {
        "draft_id": f"draft-{candidate.get('story_id')}",
        "story_id": candidate.get("story_id"),
        "source_message_id": candidate.get("message_id"),
        "source_message_link": candidate.get("message_link"),
        "source_bucket": candidate.get("bucket"),
        "target_channel": target_channel,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "headline": headline[:256],
        "body_markdown": "\n".join(body_parts),
        "reuse_assets": assets,
        "asset_reuse_rule": "Reuse these Discord CDN URLs only. Do not generate fake screenshots.",
        "grounding": {
            "author": candidate.get("author"),
            "channel_name": candidate.get("channel_name"),
            "posted_at": candidate.get("posted_at"),
            "dollar_amounts": candidate.get("dollar_amounts") or [],
        },
        "status": "draft",
    }
