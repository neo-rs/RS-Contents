"""Publish marketing drafts as plain Discord chat messages (no embeds)."""

from __future__ import annotations

import json
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests

from MarketingKnowledgeBase.secrets import discord_bot_token, load_secrets

_BASE = Path(__file__).resolve().parent


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _what_you_missed_channel_id() -> int:
    cfg = _read_json(_BASE / "config.json")
    return int(cfg.get("what_you_missed_channel_id") or 0)


def _publishing_config() -> Dict[str, Any]:
    cfg = _read_json(_BASE / "config.json") or {}
    pub = cfg.get("publishing")
    return pub if isinstance(pub, dict) else {}


def _guild_id_for_channel(channel_id: int) -> int:
    pub = _publishing_config()
    review = int(pub.get("review_channel_id") or 0)
    if channel_id == review:
        return int(pub.get("review_guild_id") or pub.get("production_guild_id") or 876528050081251379)
    preview = int(pub.get("neo_test_preview_channel_id") or 0)
    if channel_id == preview:
        return int(pub.get("neo_test_guild_id") or 1451275225512546497)
    return int(pub.get("production_guild_id") or 876528050081251379)


def _webhook_for_channel(channel_id: int) -> Optional[str]:
    secrets = load_secrets()
    by_channel = secrets.get("channel_webhooks") or {}
    if isinstance(by_channel, dict):
        url = str(by_channel.get(str(channel_id)) or "").strip()
        if url.startswith("https://discord.com/api/webhooks/"):
            return url
    pub = _publishing_config()
    review = int(pub.get("review_channel_id") or 0)
    if channel_id == review:
        url = str(secrets.get("review_webhook_url") or pub.get("review_webhook_url") or "").strip()
        if url.startswith("https://"):
            return url
    preview = int(pub.get("neo_test_preview_channel_id") or 0)
    if channel_id == preview:
        url = str(secrets.get("neo_test_preview_webhook_url") or pub.get("neo_test_preview_webhook_url") or "").strip()
        if url.startswith("https://"):
            return url
    return None


def _download_image(url: str) -> Tuple[bytes, str, str]:
    resp = requests.get(url, timeout=45)
    resp.raise_for_status()
    data = resp.content
    path = urlparse(url).path
    filename = Path(path).name or "proof.png"
    if "." not in filename:
        filename = f"{filename}.png"
    ctype = resp.headers.get("Content-Type") or mimetypes.guess_type(filename)[0] or "image/png"
    return data, filename, ctype


def _post_multipart(
    *,
    url: str,
    content: str,
    image_urls: List[str],
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"content": content[:2000]}
    files = []
    handles = []
    for idx, img_url in enumerate(image_urls[:4]):
        try:
            data, filename, ctype = _download_image(img_url)
        except Exception:
            continue
        files.append((f"files[{idx}]", (filename, data, ctype)))
    if files:
        resp = requests.post(
            url,
            data={"payload_json": json.dumps(payload)},
            files=files,
            headers=headers or {},
            timeout=90,
        )
    else:
        resp = requests.post(url, json=payload, headers=headers or {}, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"Discord post failed {resp.status_code}: {resp.text[:500]}")
    return resp.json() if resp.text.strip() else {}


def _add_reaction(*, channel_id: int, message_id: str, emoji: str, token: str) -> Dict[str, Any]:
    encoded = quote(str(emoji), safe="")
    url = f"https://discord.com/api/v10/channels/{int(channel_id)}/messages/{message_id}/reactions/{encoded}/@me"
    resp = requests.put(url, headers={"Authorization": f"Bot {token}"}, timeout=20)
    if resp.status_code == 429:
        try:
            retry_after = float((resp.json() or {}).get("retry_after") or 1)
        except Exception:
            retry_after = 1.0
        time.sleep(min(max(retry_after, 0.25), 5.0))
        resp = requests.put(url, headers={"Authorization": f"Bot {token}"}, timeout=20)
    return {
        "emoji": emoji,
        "ok": resp.status_code in (200, 204),
        "status_code": resp.status_code,
        "error": "" if resp.status_code in (200, 204) else resp.text[:300],
    }


def _maybe_add_review_reactions(*, channel_id: int, message_id: str, token: str) -> List[str]:
    if not message_id:
        return []
    cfg = _read_json(_BASE / "config.json")
    pub = cfg.get("publishing") or {}
    feedback = cfg.get("feedback") or {}
    review_channel_id = int(feedback.get("review_channel_id") or pub.get("review_channel_id") or 0)
    if int(channel_id) != review_channel_id:
        return []
    added: List[str] = []
    details: List[Dict[str, Any]] = []
    for emoji in (str(feedback.get("approve_emoji") or "✅"), str(feedback.get("reject_emoji") or "❌")):
        try:
            result = _add_reaction(channel_id=channel_id, message_id=message_id, emoji=emoji, token=token)
            details.append(result)
            if result.get("ok"):
                added.append(emoji)
        except Exception as exc:
            details.append({"emoji": emoji, "ok": False, "status_code": 0, "error": str(exc)[:300]})
    _maybe_add_review_reactions.last_details = details  # type: ignore[attr-defined]
    return added


def publish_marketing_draft(
    draft: Dict[str, Any],
    *,
    channel_id: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    channel_id = int(channel_id or _what_you_missed_channel_id())
    if channel_id <= 0:
        raise RuntimeError("what_you_missed_channel_id is not configured.")

    content = str(draft.get("body_markdown") or draft.get("description") or "").strip()
    if not content:
        raise ValueError("Draft has no body_markdown/description.")

    assets = draft.get("reuse_assets") or []
    image_urls = [str(a.get("url") or "").strip() for a in assets if a.get("url")]

    preview = {
        "channel_id": channel_id,
        "content": content,
        "image_urls": image_urls,
        "message_style": "plain_chat",
        "dry_run": dry_run,
        "source_message_link": draft.get("source_message_link"),
    }
    if dry_run:
        return preview

    webhook = _webhook_for_channel(channel_id)
    token = discord_bot_token() or ""
    if webhook:
        sent = _post_multipart(url=webhook, content=content, image_urls=image_urls)
    else:
        if not token:
            raise RuntimeError("Missing Discord bot token for publish.")
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {"Authorization": f"Bot {token}"}
        sent = _post_multipart(url=url, content=content, image_urls=image_urls, headers=headers)

    guild_id = _guild_id_for_channel(channel_id)
    mid = sent.get("id")
    review_reactions_added = _maybe_add_review_reactions(channel_id=channel_id, message_id=str(mid or ""), token=token) if token else []
    return {
        "ok": True,
        "message_id": mid,
        "channel_id": channel_id,
        "message_style": "plain_chat",
        "used_webhook": bool(webhook),
        "review_reactions_added": review_reactions_added,
        "review_reaction_details": getattr(_maybe_add_review_reactions, "last_details", []),
        "url": f"https://discord.com/channels/{guild_id}/{channel_id}/{mid}" if mid else None,
    }
