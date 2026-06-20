"""Live Discord chat bridge for the marketing agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import requests

from MarketingKnowledgeBase.agent.memory import relevant_memory_prompt, remember_rule
from MarketingKnowledgeBase.agent.state import (
    DATA,
    get_active_review_run,
    load_run,
    now_iso,
    read_json,
    write_json,
)
from MarketingKnowledgeBase.openai_client import OpenAIResponsesClient
from MarketingKnowledgeBase.secrets import load_secrets

BASE = Path(__file__).resolve().parents[1]
CHAT_STATE = DATA / "agent_chat_sessions.json"


def _load_config() -> Dict[str, Any]:
    path = BASE / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _chat_config() -> Dict[str, Any]:
    agent = (_load_config().get("agent") or {})
    chat = agent.get("chat") or {}
    return chat if isinstance(chat, dict) else {}


def _webhook_url(channel_id: int) -> str:
    secrets = load_secrets()
    chat_url = str(secrets.get("agent_chat_webhook_url") or "").strip()
    if chat_url.startswith("https://"):
        return chat_url
    by_channel = secrets.get("channel_webhooks") or {}
    if isinstance(by_channel, dict):
        url = str(by_channel.get(str(channel_id)) or "").strip()
        if url.startswith("https://"):
            return url
    return ""


def post_chat_webhook(*, channel_id: int, content: str) -> Dict[str, Any]:
    url = _webhook_url(channel_id)
    if not url:
        raise RuntimeError("Missing agent chat webhook URL in MarketingKnowledgeBase/config.secrets.json")
    secrets = load_secrets()
    chat_cfg = _chat_config()
    payload: Dict[str, Any] = {
        "content": str(content or "")[:2000],
        "allowed_mentions": {"parse": []},
    }
    username = str(secrets.get("agent_chat_webhook_username") or chat_cfg.get("webhook_username") or "").strip()
    avatar_url = str(secrets.get("agent_chat_webhook_avatar_url") or chat_cfg.get("webhook_avatar_url") or "").strip()
    if username:
        payload["username"] = username[:80]
    if avatar_url.startswith("https://"):
        payload["avatar_url"] = avatar_url
    resp = requests.post(url, json=payload, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"Agent chat webhook failed: {resp.status_code} {resp.text[:300]}")
    return {"ok": True, "status_code": resp.status_code}


def _load_state() -> Dict[str, Any]:
    doc = read_json(CHAT_STATE, {"version": 1, "channels": {}})
    return doc if isinstance(doc, dict) else {"version": 1, "channels": {}}


def _save_state(doc: Dict[str, Any]) -> None:
    doc["updated_at"] = now_iso()
    write_json(CHAT_STATE, doc)


def _channel_rows(doc: Dict[str, Any], channel_id: int) -> List[Dict[str, Any]]:
    channels = doc.setdefault("channels", {})
    row = channels.setdefault(str(channel_id), {"messages": []})
    messages = row.setdefault("messages", [])
    return messages if isinstance(messages, list) else []


def _active_run_summary() -> str:
    run_id = get_active_review_run()
    if not run_id:
        return "No active review run."
    try:
        run = load_run(run_id)
    except Exception:
        return f"Active review run id: {run_id} (details unavailable)."
    metadata = run.get("metadata") or {}
    draft = run.get("final_output") or {}
    text = str(draft.get("full_text") or draft.get("body_markdown") or "")[:900]
    return (
        f"Active review run: {run_id}\n"
        f"Status: {run.get('status')}\n"
        f"Story: {metadata.get('story_id') or '-'}\n"
        f"Review URL: {metadata.get('review_message_url') or '-'}\n"
        f"Latest draft excerpt:\n{text or '-'}"
    )


def _build_prompt(*, channel_id: int, user_name: str, message_text: str, history: List[Dict[str, Any]]) -> str:
    recent = history[-12:]
    transcript = "\n".join(
        f"{row.get('role')}: {row.get('name') or ''} {row.get('content') or ''}".strip()
        for row in recent
    )
    memory = relevant_memory_prompt(content_type="agent_live_chat", channel_id=str(channel_id))
    return (
        f"User: {user_name}\n"
        f"Message: {message_text}\n\n"
        f"Recent chat:\n{transcript or '-'}\n\n"
        f"{memory or ''}\n\n"
        f"Current review context:\n{_active_run_summary()}"
    ).strip()


def handle_live_chat_message(
    *,
    channel_id: int,
    user_id: str,
    user_name: str,
    message_text: str,
) -> Dict[str, Any]:
    text = str(message_text or "").strip()
    if not text:
        return {"ok": False, "skip": True, "reason": "empty"}

    doc = _load_state()
    history = _channel_rows(doc, int(channel_id))
    lowered = text.lower()
    if lowered in {"help", "commands"}:
        reply = (
            "I can chat about the current marketing draft, explain the active review run, help rewrite copy, "
            "or remember style rules. Try: `make this less hype`, `what is the active run?`, or "
            "`remember: avoid saying members got pinged`."
        )
    elif lowered.startswith("remember:"):
        rule = text.split(":", 1)[1].strip()
        if not rule:
            reply = "Tell me what to remember, like `remember: avoid saying members got pinged`."
        else:
            remember_rule(rule, scope="channel_memory", channel_id=str(channel_id), created_by=str(user_id))
            reply = "Remembered for this chat channel."
    elif lowered in {"status", "active run", "current run"}:
        reply = _active_run_summary()
    else:
        cfg = _load_config()
        agent = cfg.get("agent") or {}
        model = str((agent.get("chat") or {}).get("model") or agent.get("chat_model") or "gpt-5.5")
        instructions = (
            "You are Captain Hook, the RS marketing AI chat assistant. "
            "Be conversational, concise, and practical. You can discuss active review drafts, rewrite ideas, "
            "explain why copy is grounded, and suggest next actions. Do not invent facts. "
            "If the user asks to change a live review draft, explain what you would change and suggest using the review buttons/chat on the active run if needed."
        )
        result = OpenAIResponsesClient(timeout_s=120).responses_text(
            model=model,
            instructions=instructions,
            input_text=_build_prompt(channel_id=channel_id, user_name=user_name, message_text=text, history=history),
            reasoning_effort="medium",
            max_output_tokens=700,
        )
        if not result.ok:
            raise RuntimeError(result.error or "OpenAI returned no live chat response")
        reply = result.text

    history.append(
        {
            "role": "user",
            "user_id": str(user_id),
            "name": str(user_name or ""),
            "content": text,
            "created_at": now_iso(),
        }
    )
    history.append({"role": "assistant", "name": "Captain Hook", "content": reply, "created_at": now_iso()})
    del history[:-30]
    _save_state(doc)
    post_result = post_chat_webhook(channel_id=int(channel_id), content=reply)
    return {"ok": True, "reply": reply, "post": post_result}
