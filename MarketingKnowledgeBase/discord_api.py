"""Minimal Discord REST helpers for MarketingKnowledgeBase sync."""

from __future__ import annotations

import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

DISCORD_API = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1420070400000
_URL_RE = re.compile(r"https?://\S+")


def discord_get_json(url: str, headers: dict, *, timeout_s: float = 20.0, max_tries: int = 5) -> Any:
    last_err: Optional[str] = None
    for attempt in range(1, max_tries + 1):
        r = requests.get(url, headers=headers, timeout=timeout_s)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            try:
                body = r.json()
            except Exception:
                body = {}
            retry_after = body.get("retry_after")
            try:
                retry_after_s = float(retry_after)
            except Exception:
                retry_after_s = 2.0
            retry_after_s = max(0.5, min(30.0, retry_after_s))
            time.sleep(retry_after_s)
            continue
        last_err = f"{r.status_code}: {r.text[:500]}"
        break
    raise RuntimeError(f"Discord API request failed for {url}: {last_err or 'unknown error'}")


def list_guild_channels(guild_id: int, headers: dict) -> List[Dict[str, Any]]:
    data = discord_get_json(f"{DISCORD_API}/guilds/{guild_id}/channels", headers)
    return data if isinstance(data, list) else []


def fetch_channel_messages(channel_id: int, headers: dict, *, limit: int = 25) -> List[Dict[str, Any]]:
    limit = max(1, min(100, int(limit)))
    data = discord_get_json(f"{DISCORD_API}/channels/{channel_id}/messages?limit={limit}", headers)
    return data if isinstance(data, list) else []


def snowflake_for_datetime(dt: datetime) -> int:
    """Return a Discord snowflake lower bound for a datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ms = int(dt.astimezone(timezone.utc).timestamp() * 1000)
    return max(0, (ms - DISCORD_EPOCH_MS) << 22)


def fetch_channel_messages_window(
    channel_id: int,
    headers: dict,
    *,
    start_at: datetime,
    end_at: datetime,
    max_pages: int = 25,
) -> List[Dict[str, Any]]:
    """Fetch messages posted in [start_at, end_at) by paging backward from end_at."""
    start_utc = start_at.astimezone(timezone.utc) if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)
    end_utc = end_at.astimezone(timezone.utc) if end_at.tzinfo else end_at.replace(tzinfo=timezone.utc)
    before = str(snowflake_for_datetime(end_utc))
    out: List[Dict[str, Any]] = []
    pages = max(1, int(max_pages))

    for _ in range(pages):
        url = f"{DISCORD_API}/channels/{channel_id}/messages?limit=100&before={before}"
        data = discord_get_json(url, headers)
        messages = data if isinstance(data, list) else []
        if not messages:
            break
        oldest_seen: Optional[datetime] = None
        for msg in messages:
            ts = str(msg.get("timestamp") or "")
            try:
                msg_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if oldest_seen is None or msg_dt < oldest_seen:
                oldest_seen = msg_dt
            if start_utc <= msg_dt < end_utc:
                out.append(msg)
        last_id = str(messages[-1].get("id") or "")
        if not last_id:
            break
        before = last_id
        if oldest_seen and oldest_seen < start_utc:
            break
    return out


def fetch_reaction_users(channel_id: int, message_id: str, emoji: str, headers: dict, *, limit: int = 100) -> List[Dict[str, Any]]:
    encoded = urllib.parse.quote(str(emoji), safe="")
    limit = max(1, min(100, int(limit)))
    url = f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}/reactions/{encoded}?limit={limit}"
    data = discord_get_json(url, headers)
    return data if isinstance(data, list) else []


def fetch_guild_member(guild_id: int, user_id: str, headers: dict) -> Dict[str, Any]:
    data = discord_get_json(f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}", headers)
    return data if isinstance(data, dict) else {}


def extract_message_text(msg: dict) -> str:
    parts: List[str] = []
    content = str(msg.get("content") or "").strip()
    if content:
        parts.append(content)
    for embed in msg.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        for key in ("title", "description"):
            val = str(embed.get(key) or "").strip()
            if val:
                parts.append(val)
        for field in embed.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            value = str(field.get("value") or "").strip()
            if name or value:
                parts.append(f"{name}: {value}".strip(": "))
    return "\n".join(parts).strip()


def extract_urls(msg: dict) -> List[str]:
    text = extract_message_text(msg)
    urls = _URL_RE.findall(text)
    for embed in msg.get("embeds") or []:
        if isinstance(embed, dict):
            u = str(embed.get("url") or "").strip()
            if u:
                urls.append(u)
    seen = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def extract_attachments(msg: dict) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for att in msg.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        url = str(att.get("url") or att.get("proxy_url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "url": url,
                "filename": str(att.get("filename") or ""),
                "content_type": str(att.get("content_type") or ""),
            }
        )
    return out[:6]


def extract_embed_images(msg: dict) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for embed in msg.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        for key in ("image", "thumbnail"):
            block = embed.get(key)
            if isinstance(block, dict):
                url = str(block.get("url") or "").strip()
                if url:
                    out.append({"url": url, "source": key})
        img_url = str(embed.get("url") or "").strip()
        if img_url and img_url.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            out.append({"url": img_url, "source": "embed_url"})
    seen = set()
    deduped: List[Dict[str, str]] = []
    for item in out:
        u = item["url"]
        if u in seen:
            continue
        seen.add(u)
        deduped.append(item)
    return deduped[:6]


def extract_author(msg: dict) -> Dict[str, str]:
    author = msg.get("author") or {}
    if not isinstance(author, dict):
        return {"id": "", "username": "", "display_name": ""}
    return {
        "id": str(author.get("id") or ""),
        "username": str(author.get("username") or ""),
        "display_name": str(author.get("global_name") or author.get("username") or ""),
    }


def extract_reactions(msg: dict) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for reaction in msg.get("reactions") or []:
        if not isinstance(reaction, dict):
            continue
        emoji = reaction.get("emoji") or {}
        name = str(emoji.get("name") or "")
        count = int(reaction.get("count") or 0)
        if name and count > 0:
            out.append({"emoji": name, "count": count})
    return out[:20]


def normalize_message_entry(
    *,
    msg: dict,
    bucket: str,
    channel_id: int,
    channel_name: str,
    guild_id: int,
) -> Dict[str, Any]:
    message_id = str(msg.get("id") or "")
    ts = msg.get("timestamp")
    text = extract_message_text(msg)
    urls = extract_urls(msg)
    attachments = extract_attachments(msg)
    embed_images = extract_embed_images(msg)
    author = extract_author(msg)
    return {
        "message_id": message_id,
        "bucket": bucket,
        "channel_id": str(channel_id),
        "channel_name": channel_name,
        "posted_at": ts,
        "synced_at": None,
        "text": text[:4000],
        "urls": urls[:10],
        "attachments": attachments,
        "embed_images": embed_images,
        "author": author,
        "reactions": extract_reactions(msg),
        "has_attachments": bool(attachments),
        "has_embed_images": bool(embed_images),
        "message_link": f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}",
    }
