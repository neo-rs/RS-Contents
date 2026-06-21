"""Standalone Discord client for Reese live chat.

RSAdminBot remains the admin/review bridge. This runner owns the Reese live chat
channel so Discord shows Reese as the typing/sending bot instead of RSAdminBot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import discord
import requests

from MarketingKnowledgeBase.agent.live_chat import handle_live_chat_message, post_chat_webhook
from MarketingKnowledgeBase.secrets import discord_bot_token

BASE = Path(__file__).resolve().parents[1]


def _load_config() -> Dict[str, Any]:
    path = BASE / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _agent_config() -> Dict[str, Any]:
    agent = (_load_config().get("agent") or {})
    return agent if isinstance(agent, dict) else {}


def _chat_channel_id() -> int:
    chat = (_agent_config().get("chat") or {})
    try:
        if not bool(chat.get("enabled", True)):
            return 0
        return int(chat.get("channel_id") or 0)
    except Exception:
        return 0


def _review_channel_id() -> int:
    cfg = _load_config()
    agent = cfg.get("agent") or {}
    feedback = cfg.get("feedback") or {}
    publishing = cfg.get("publishing") or {}
    try:
        return int(agent.get("review_channel_id") or feedback.get("review_channel_id") or publishing.get("review_channel_id") or 0)
    except Exception:
        return 0


def _reese_bot_config() -> Dict[str, Any]:
    bot_cfg = (_agent_config().get("reese_bot") or {})
    return bot_cfg if isinstance(bot_cfg, dict) else {}


def _allowed_user(user: Any) -> bool:
    agent = _agent_config()
    allowed_users = {int(x) for x in (agent.get("allowed_user_ids") or []) if str(x).strip().isdigit()}
    allowed_roles = {int(x) for x in (agent.get("allowed_role_ids") or []) if str(x).strip().isdigit()}
    try:
        user_id = int(getattr(user, "id", 0) or 0)
    except Exception:
        user_id = 0
    if not allowed_users and not allowed_roles:
        return True
    if user_id and user_id in allowed_users:
        return True
    for role in getattr(user, "roles", None) or []:
        try:
            if int(getattr(role, "id", 0) or 0) in allowed_roles:
                return True
        except Exception:
            continue
    return False


async def _normalize_message(msg: Any) -> Dict[str, Any]:
    attachments = []
    for att in getattr(msg, "attachments", []) or []:
        attachments.append(
            {
                "url": str(getattr(att, "url", "") or ""),
                "filename": str(getattr(att, "filename", "") or ""),
                "content_type": str(getattr(att, "content_type", "") or ""),
            }
        )
    embed_images = []
    for emb in getattr(msg, "embeds", []) or []:
        try:
            if getattr(getattr(emb, "image", None), "url", None):
                embed_images.append({"url": str(emb.image.url), "source": "image"})
            if getattr(getattr(emb, "thumbnail", None), "url", None):
                embed_images.append({"url": str(emb.thumbnail.url), "source": "thumbnail"})
        except Exception:
            pass
    author = getattr(msg, "author", None)
    channel = getattr(msg, "channel", None)
    return {
        "message_id": str(getattr(msg, "id", "") or ""),
        "channel_id": str(getattr(channel, "id", "") or ""),
        "channel_name": str(getattr(channel, "name", "") or ""),
        "posted_at": str(getattr(msg, "created_at", "") or ""),
        "text": str(getattr(msg, "content", "") or ""),
        "attachments": attachments,
        "embed_images": embed_images,
        "author": {
            "id": str(getattr(author, "id", "") or ""),
            "username": str(getattr(author, "name", "") or ""),
            "display_name": str(getattr(author, "display_name", "") or getattr(author, "name", "") or ""),
        },
        "message_link": str(getattr(msg, "jump_url", "") or ""),
        "source": "discord_gateway",
    }


async def _build_discord_context(client: discord.Client, message: discord.Message) -> Dict[str, Any]:
    mentioned_channels = list(getattr(message, "channel_mentions", []) or [])
    current_message = await _normalize_message(message)
    context: Dict[str, Any] = {
        "message_id": str(getattr(message, "id", "") or ""),
        "message_url": str(getattr(message, "jump_url", "") or ""),
        "current_message": current_message,
        "mentioned_channel_ids": [str(getattr(ch, "id", "") or "") for ch in mentioned_channels],
        "reply_message_id": "",
        "reply_message": {},
        "linked_messages": {},
        "recent_channel_messages": {},
        "channel_info": {},
    }
    try:
        cid = str(getattr(getattr(message, "channel", None), "id", "") or "")
        rows = []
        async for msg in message.channel.history(limit=12):
            rows.append(await _normalize_message(msg))
        if cid:
            context["recent_channel_messages"][cid] = rows
    except Exception:
        pass
    try:
        ref = getattr(message, "reference", None)
        resolved = getattr(ref, "resolved", None)
        if resolved:
            context["reply_message_id"] = str(getattr(resolved, "id", "") or "")
            context["reply_message"] = await _normalize_message(resolved)
    except Exception:
        pass

    for mentioned_channel in mentioned_channels[:2]:
        try:
            cid = str(getattr(mentioned_channel, "id", "") or "")
            rows = []
            async for msg in mentioned_channel.history(limit=10):
                rows.append(await _normalize_message(msg))
            if cid:
                context["recent_channel_messages"][cid] = rows
        except Exception:
            continue

    link_matches = re.findall(
        r"(?:https?://)?(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/(\d{15,25})/(\d{15,25})/(\d{15,25})",
        str(getattr(message, "content", "") or ""),
    )
    for _guild_id, linked_channel_id, linked_message_id in link_matches[:3]:
        try:
            linked_channel = client.get_channel(int(linked_channel_id))
            if linked_channel is None:
                linked_channel = await client.fetch_channel(int(linked_channel_id))
            linked_message = await linked_channel.fetch_message(int(linked_message_id))  # type: ignore[attr-defined]
            context["linked_messages"][str(linked_message_id)] = await _normalize_message(linked_message)
        except Exception:
            continue
    return context


def _download_discord_image(url: str) -> discord.File | None:
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        filename = Path(str(url).split("?", 1)[0]).name or "source-image.png"
        if "." not in filename:
            filename = f"{filename}.png"
        from io import BytesIO

        return discord.File(BytesIO(resp.content), filename=filename[:120])
    except Exception:
        return None


async def _send_reply(message: discord.Message, reply: str, *, image_urls: List[str] | None = None) -> Dict[str, Any]:
    text = str(reply or "").strip()
    if not text:
        return {"ok": False, "reason": "empty_reply"}
    sent_ids: List[str] = []
    files: List[discord.File] = []
    for url in (image_urls or [])[:3]:
        file_obj = await asyncio.to_thread(_download_discord_image, str(url or ""))
        if file_obj:
            files.append(file_obj)
    for idx in range(0, len(text), 2000):
        chunk = text[idx : idx + 2000]
        send_files = files if idx == 0 else []
        sent = await message.channel.send(content=chunk, files=send_files, allowed_mentions=discord.AllowedMentions.none())
        sent_ids.append(str(getattr(sent, "id", "") or ""))
    return {"ok": True, "transport": "reese_bot", "message_ids": sent_ids, "attached_images": len(files)}


class ReeseClient(discord.Client):
    async def on_ready(self) -> None:
        chat_id = _chat_channel_id()
        review_id = _review_channel_id()
        print(
            f"ReeseBot ready as {getattr(self.user, 'name', 'unknown')} "
            f"({getattr(self.user, 'id', 'unknown')}); chat_channel={chat_id}; review_channel={review_id}",
            flush=True,
        )

    async def on_message(self, message: discord.Message) -> None:
        try:
            if not message or getattr(getattr(message, "author", None), "bot", False):
                return
            if not bool(_reese_bot_config().get("enabled", False)):
                return
            chat_channel_id = _chat_channel_id()
            if chat_channel_id <= 0 or int(getattr(getattr(message, "channel", None), "id", 0) or 0) != chat_channel_id:
                return
            if not _allowed_user(message.author):
                return
            raw_text = str(getattr(message, "content", "") or "").strip()
            has_media = bool(getattr(message, "attachments", []) or getattr(message, "embeds", []))
            text = raw_text or ("[image attached]" if has_media else "")
            if not text:
                return
            async with message.channel.typing():
                context = await _build_discord_context(self, message)

                def _work() -> Dict[str, Any]:
                    return handle_live_chat_message(
                        channel_id=int(message.channel.id),
                        user_id=str(getattr(message.author, "id", "") or ""),
                        user_name=str(getattr(message.author, "display_name", "") or getattr(message.author, "name", "") or ""),
                        message_text=text,
                        discord_context=context,
                        send_reply=False,
                    )

                result = await asyncio.to_thread(_work)
                reply = str(result.get("reply") or "").strip()
                try:
                    post_result = await _send_reply(message, reply, image_urls=result.get("image_urls") or [])
                    result["post"] = post_result
                except Exception:
                    if bool(_reese_bot_config().get("fallback_to_webhook", True)):
                        result["post"] = await asyncio.to_thread(
                            post_chat_webhook,
                            channel_id=int(message.channel.id),
                            content=reply,
                        )
                    else:
                        raise
        except Exception as exc:
            try:
                await message.channel.send(
                    f"Reese chat error: `{type(exc).__name__}: {str(exc)[:500]}`",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                pass


def run() -> None:
    token = discord_bot_token()
    if not token:
        raise RuntimeError("Missing Reese bot token in MarketingKnowledgeBase/config.secrets.json")
    intents = discord.Intents.default()
    intents.guilds = True
    intents.messages = True
    intents.message_content = True
    intents.reactions = True
    client = ReeseClient(intents=intents, allowed_mentions=discord.AllowedMentions.none())
    client.run(token)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run standalone Reese Discord live-chat bot.")
    parser.parse_args(argv)
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
