"""Live Discord chat bridge for the marketing agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import requests

from MarketingKnowledgeBase.agent.live_context_builder import build_live_chat_context
from MarketingKnowledgeBase.agent.live_intent import route_live_chat_intent
from MarketingKnowledgeBase.agent.memory import relevant_memory_prompt, remember_rule
from MarketingKnowledgeBase.agent.state import (
    DATA,
    get_active_review_run,
    load_run,
    now_iso,
    read_json,
    write_json,
)
from MarketingKnowledgeBase.agent.webhook_profile import load_webhook_profile
from MarketingKnowledgeBase.openai_client import OpenAIResponsesClient
from MarketingKnowledgeBase.secrets import load_secrets

BASE = Path(__file__).resolve().parents[1]
CHAT_STATE = DATA / "agent_chat_sessions.json"
TOKEN_USAGE = DATA / "agent_token_usage.json"


def _load_config() -> Dict[str, Any]:
    path = BASE / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _chat_config() -> Dict[str, Any]:
    agent = (_load_config().get("agent") or {})
    chat = agent.get("chat") or {}
    return chat if isinstance(chat, dict) else {}


def _agent_config() -> Dict[str, Any]:
    agent = (_load_config().get("agent") or {})
    return agent if isinstance(agent, dict) else {}


def _voice_profile() -> Dict[str, Any]:
    agent = _agent_config()
    profile = agent.get("voice_profile") or {}
    return profile if isinstance(profile, dict) else {}


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
    cfg = _load_config()
    chat_cfg = _chat_config()
    profile = load_webhook_profile(cfg)
    payload: Dict[str, Any] = {
        "content": str(content or "")[:2000],
        "allowed_mentions": {"parse": []},
    }
    username = str(
        profile.get("username")
        or chat_cfg.get("webhook_username")
        or secrets.get("agent_chat_webhook_username")
        or "Reese"
    ).strip()
    avatar_url = str(
        profile.get("avatar_url")
        or secrets.get("agent_chat_webhook_avatar_url")
        or chat_cfg.get("webhook_avatar_url")
        or ""
    ).strip()
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


def _history_limit() -> int:
    chat = _chat_config()
    try:
        return max(20, min(50, int(chat.get("short_term_history_limit") or 30)))
    except Exception:
        return 30


def _model_for_intent(intent: str) -> str:
    agent = _agent_config()
    chat = agent.get("chat") or {}
    stages = agent.get("model_by_stage") or {}
    if intent in {"general_chat", "server_setup_question", "role_access_question"}:
        return str(chat.get("model") or stages.get("draft") or "gpt-4o")
    if intent in {"new_lead_copy", "ghl_sms_copy", "market_research", "content_discovery"}:
        return str(chat.get("draft_model") or stages.get("draft") or "gpt-4o")
    if intent in {"active_review_help", "cancellation_save"}:
        return str(chat.get("quality_model") or stages.get("final") or chat.get("model") or "gpt-5.5")
    return str(chat.get("model") or stages.get("draft") or "gpt-4o")


def _estimate_tokens(text: str) -> int:
    return max(1, int(len(str(text or "")) / 4))


def _usage_int(usage: Dict[str, Any], *keys: str) -> int:
    for key in keys:
        val = usage.get(key)
        if isinstance(val, int):
            return val
    nested = usage.get("input_tokens_details") or usage.get("output_tokens_details") or {}
    for key in keys:
        val = nested.get(key) if isinstance(nested, dict) else None
        if isinstance(val, int):
            return val
    return 0


def _token_budget() -> Dict[str, Any]:
    budget = (_agent_config().get("token_budget") or {})
    return budget if isinstance(budget, dict) else {}


def _feature_enabled(flag: str, default: bool = True) -> bool:
    chat = _chat_config()
    return bool(chat.get(flag, default))


def _disabled_feature_reply(intent: str) -> str:
    if intent in {"new_lead_copy", "content_discovery"} and not _feature_enabled("channel_tools_enabled", True):
        return "Channel tools are disabled right now, so I can’t safely read that lead context yet."
    if intent == "role_access_question" and not _feature_enabled("role_tools_enabled", False):
        return "Role/channel access tools are disabled right now, so I won’t guess who can see it."
    if intent == "ghl_sms_copy" and not _feature_enabled("ghl_sms_tools_enabled", True):
        return "GHL/SMS draft tools are disabled right now. I won’t draft campaign copy until that flag is on."
    if intent in {"ticket_support", "cancellation_save", "help_inquiry"} and not _feature_enabled("ticket_tools_enabled", False):
        return (
            "I can recognize that workflow, but ticket/cancellation tools are not wired yet. "
            "Before I answer member-specific stuff, we still need the ticket data source, privacy rules, save-offer rules, and escalation rules configured."
        )
    return ""


def _intent_token_cap(intent: str) -> int:
    budget = _token_budget()
    key = "simple_chat_max_input_tokens"
    if intent == "active_review_help":
        key = "active_review_max_input_tokens"
    elif intent in {"new_lead_copy", "channel_question", "role_access_question", "market_research", "ghl_sms_copy", "content_discovery"}:
        key = "channel_context_max_input_tokens"
    elif intent in {"ticket_support", "cancellation_save", "help_inquiry"}:
        key = "ticket_context_max_input_tokens"
    try:
        return int(budget.get(key) or 0)
    except Exception:
        return 0


def _tool_round_count(route: Dict[str, Any]) -> int:
    workflow_steps = {"draft_lead_copy", "draft_ghl_sms", "load_active_review", "recent_chat_memory", "store_memory_rule"}
    return len([tool for tool in (route.get("needs_tools") or []) if str(tool) not in workflow_steps])


def _load_token_usage() -> Dict[str, Any]:
    doc = read_json(TOKEN_USAGE, {"version": 1, "entries": []})
    return doc if isinstance(doc, dict) else {"version": 1, "entries": []}


def _record_token_usage(row: Dict[str, Any]) -> None:
    doc = _load_token_usage()
    entries = doc.setdefault("entries", [])
    entries.insert(0, row)
    doc["entries"] = entries[:2000]
    doc["updated_at"] = now_iso()
    write_json(TOKEN_USAGE, doc)


def _daily_estimated_tokens() -> int:
    today = now_iso()[:10]
    doc = _load_token_usage()
    total = 0
    for row in doc.get("entries") or []:
        if str(row.get("created_at") or "").startswith(today):
            total += int(row.get("estimated_input_tokens") or 0)
            total += int(row.get("output_tokens") or 0)
    return total


def _budget_allowed(*, estimated_input_tokens: int) -> Dict[str, Any]:
    if not _feature_enabled("token_budget_enabled", True):
        return {"allowed": True, "daily_total": 0, "projected": int(estimated_input_tokens or 0), "disabled": True}
    budget = _token_budget()
    hard = int(budget.get("hard_stop_after_daily_estimated_tokens") or 0)
    warn = int(budget.get("warn_after_daily_estimated_tokens") or 0)
    daily_total = _daily_estimated_tokens()
    projected = daily_total + int(estimated_input_tokens or 0)
    if hard and projected > hard:
        return {"allowed": False, "daily_total": daily_total, "projected": projected, "hard_stop": hard}
    return {"allowed": True, "daily_total": daily_total, "projected": projected, "warn": bool(warn and projected > warn)}


def _build_prompt(
    *,
    channel_id: int,
    user_name: str,
    message_text: str,
    history: List[Dict[str, Any]],
    route: Dict[str, Any],
    live_context: Dict[str, Any],
) -> str:
    recent = history[-12:]
    transcript = "\n".join(
        f"{row.get('role')}: {row.get('name') or ''} {row.get('content') or ''}".strip()
        for row in recent
    )
    memory = relevant_memory_prompt(content_type="agent_live_chat", channel_id=str(channel_id))
    active = _active_run_summary() if route.get("requires_active_run") else "Not loaded for this request."
    voice = _voice_profile()
    return (
        f"User: {user_name}\n"
        f"Message: {message_text}\n\n"
        f"Detected intent:\n{json.dumps(route, ensure_ascii=False, indent=2)}\n\n"
        f"Focused context/evidence:\n{json.dumps(live_context, ensure_ascii=False, indent=2)[:12000]}\n\n"
        f"Recent chat:\n{transcript or '-'}\n\n"
        f"Voice profile:\n{json.dumps(voice, ensure_ascii=False, indent=2) if voice else '-'}\n\n"
        f"{memory or ''}\n\n"
        f"Current review context:\n{active}"
    ).strip()


def _image_urls_from_live_context(live_context: Dict[str, Any]) -> List[str]:
    evidence = (live_context.get("evidence_pack") or {}) if isinstance(live_context, dict) else {}
    urls: List[str] = []
    for key in ("primary_message", "current_message", "reply_message", "linked_message"):
        row = evidence.get(key) or {}
        if not isinstance(row, dict):
            continue
        for url in row.get("image_urls") or []:
            if isinstance(url, str) and url.startswith("http") and url not in urls:
                urls.append(url)
        for bucket in ("attachments", "embed_images", "images"):
            values = row.get(bucket) or []
            if isinstance(values, dict):
                values = [values]
            for item in values if isinstance(values, list) else []:
                if isinstance(item, str):
                    url = item
                elif isinstance(item, dict):
                    url = str(item.get("url") or item.get("proxy_url") or "")
                else:
                    url = ""
                if url.startswith("http") and url not in urls:
                    urls.append(url)
    return urls[:4]


def _instructions_for_intent(intent: str) -> str:
    assistant_name = load_webhook_profile(_load_config()).get("username") or "Reese"
    base = (
        f"You are {assistant_name}, the RS marketing AI chat assistant. Be direct, useful, and grounded. "
        "This is an internal RS admin channel, not public marketing copy. "
        "Tone: RS/street-smart, not too formal, no cursing, no fake hype, no kissing ass. "
        "Do not invent facts, checkouts, profit, urgency, role access, market comps, or member wins. "
        "Use the provided Focused context/evidence first. Do not say data is not loaded when the evidence pack includes setup_facts, archive_content, recent_channel_messages, or primary_message. "
        "If external tools are missing, explain the specific missing external piece without ignoring local RS archive/live-context evidence."
    )
    if intent == "new_lead_copy":
        return (
            base
            + " The user is asking for lead/alert copy. Use the source message/recent channel context. "
            "If there is no success post, frame it as a lead or alert only. Do not say members cooked, hit, checked out, "
            "made profit, or secured unless the source proves it. If primary_image_url is present, say that is the image to use. "
            "If the current_message or primary_message includes attachments/embed_images/image_urls, treat those as visible source images and do not say you cannot see them. "
            "Include suggested copy plus what not to claim."
        )
    if intent == "content_discovery":
        return (
            base
            + " The user is asking what content is strongest right now. Use archive_content candidates only. "
            "Recommend the best 1-3 options with why they rank, the source message link, and the exact primary_image_url when present. "
            "If archive_content.excluded_terms is present, do not recommend candidates marked excluded_by_query. "
            "If no candidate matches the exact product term, say that and give the best alternative RS archive candidates. "
            "Do not swap images between candidates. If no image is available for a candidate, say so. "
            "Keep claims separated: lead/alert vs success/member win."
        )
    if intent == "ghl_sms_copy":
        return (
            base
            + " Draft-only GHL/SMS help. Do not send, schedule, segment contacts, or imply live GHL access. "
            "Output short plain text SMS options with no Discord mentions/custom emoji and clear unsupported-claim notes."
        )
    if intent == "role_access_question":
        return base + " For role/channel access, only explain permission data if provided. If not provided, do not guess."
    if intent == "market_research":
        return (
            base
            + " Use local archive_content, server_context_results, primary_message, and market_context. "
            "External live market comps are not configured unless verified_live_market is true, but local RS archive/source clues are still usable. "
            "Give a grounded worth-posting read instead of refusing."
        )
    if intent == "server_setup_question":
        return (
            base
            + " Answer setup/access/storage questions from provided setup_facts. "
            "If setup_facts show live_context, archive search, or content_record_storage, state those capabilities plainly. "
            "Do not say you only have generic OpenAI knowledge when local KB/storage facts are present. "
            "Be clear about limits: no independent browser-like server browsing, no ticket/member/private data unless wired, and no live market verification unless provided."
        )
    return (
        base
        + " Only use active review context when the detected intent says it is loaded. Keep normal chat brief."
    )


def handle_live_chat_message(
    *,
    channel_id: int,
    user_id: str,
    user_name: str,
    message_text: str,
    discord_context: Dict[str, Any] | None = None,
    send_reply: bool = True,
) -> Dict[str, Any]:
    text = str(message_text or "").strip()
    if not text:
        return {"ok": False, "skip": True, "reason": "empty"}

    reply_image_urls: List[str] = []
    doc = _load_state()
    history = _channel_rows(doc, int(channel_id))
    lowered = text.lower()
    mentioned_channel_ids = [str(x) for x in (discord_context or {}).get("mentioned_channel_ids") or []]
    reply_message_id = str((discord_context or {}).get("reply_message_id") or "")
    route = route_live_chat_intent(
        message_text=text,
        mentioned_channel_ids=mentioned_channel_ids,
        replied_to_message_id=reply_message_id,
        active_run_summary="available" if get_active_review_run() else "",
        history=history[-12:],
    )
    current_message = (discord_context or {}).get("current_message") or {}
    current_has_media = bool(
        isinstance(current_message, dict)
        and (current_message.get("attachments") or current_message.get("embed_images") or current_message.get("image_urls"))
    )
    if current_has_media and str(route.get("intent") or "") == "general_chat":
        route = {
            **route,
            "intent": "new_lead_copy",
            "confidence": 0.82,
            "needs_tools": [
                "resolve_discord_references",
                "inspect_replied_message",
                "fetch_recent_channel_messages",
                "pull_market_context",
                "draft_lead_copy",
            ],
            "requires_active_run": False,
            "reason": "Current message includes media; treat it as source material for content drafting.",
        }
    if lowered in {"help", "commands"}:
        reply = (
            "I can help with active review drafts, new lead/alert copy from mentioned channels, draft-only GHL/SMS ideas, "
            "best-content picks from the KB archives, server setup questions, and memory rules. Try: `status`, `what's the best content right now`, `what can you write for this lead in #online-important`, "
            "`write a GHL SMS for this`, or `remember: no member win claims without proof`."
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
    elif _disabled_feature_reply(str(route.get("intent") or "")):
        reply = _disabled_feature_reply(str(route.get("intent") or ""))
    else:
        max_tool_rounds = int(_token_budget().get("max_tool_rounds") or 0)
        tool_rounds = _tool_round_count(route)
        if max_tool_rounds and tool_rounds > max_tool_rounds:
            reply = (
                "That request needs more context steps than the current tool-round cap allows. "
                "Reply with the exact message/link or narrow the ask and I’ll handle it tighter."
            )
            history.append(
                {
                    "role": "user",
                    "user_id": str(user_id),
                    "name": str(user_name or ""),
                    "content": text,
                    "created_at": now_iso(),
                    "intent": route.get("intent"),
                }
            )
            history.append(
                {
                    "role": "assistant",
                    "name": load_webhook_profile(_load_config()).get("username") or "Reese",
                    "content": reply,
                    "created_at": now_iso(),
                    "intent": route.get("intent"),
                }
            )
            del history[:-_history_limit()]
            _save_state(doc)
            post_result = post_chat_webhook(channel_id=int(channel_id), content=reply) if send_reply else {"ok": True, "transport": "caller"}
            return {"ok": True, "reply": reply, "post": post_result, "intent": route}
        live_context = build_live_chat_context(
            route=route,
            channel_id=int(channel_id),
            message_text=text,
            discord_context=discord_context or {},
        )
        reply_image_urls = _image_urls_from_live_context(live_context)
        model = _model_for_intent(str(route.get("intent") or "general_chat"))
        prompt = _build_prompt(
            channel_id=channel_id,
            user_name=user_name,
            message_text=text,
            history=history,
            route=route,
            live_context=live_context,
        )
        estimated = _estimate_tokens(prompt)
        intent_cap = _intent_token_cap(str(route.get("intent") or "general_chat")) if _feature_enabled("token_budget_enabled", True) else 0
        if intent_cap and estimated > intent_cap:
            reply = (
                "That context is too big for the current token cap. "
                f"Estimated input is about {estimated} tokens, cap is {intent_cap}. "
                "Reply with the exact message link/reply target or narrow the channel/time window."
            )
            _record_token_usage(
                {
                    "created_at": now_iso(),
                    "channel_id": str(channel_id),
                    "user_id": str(user_id),
                    "intent": route.get("intent"),
                    "model": model,
                    "estimated_input_tokens": estimated,
                    "output_tokens": 0,
                    "tool_rounds": tool_rounds,
                    "blocked": True,
                    "reason": "intent_token_cap",
                    "intent_cap": intent_cap,
                }
            )
            history.append(
                {
                    "role": "user",
                    "user_id": str(user_id),
                    "name": str(user_name or ""),
                    "content": text,
                    "created_at": now_iso(),
                    "intent": route.get("intent"),
                }
            )
            history.append(
                {
                    "role": "assistant",
                    "name": load_webhook_profile(_load_config()).get("username") or "Reese",
                    "content": reply,
                    "created_at": now_iso(),
                    "intent": route.get("intent"),
                }
            )
            del history[:-_history_limit()]
            _save_state(doc)
            post_result = post_chat_webhook(channel_id=int(channel_id), content=reply) if send_reply else {"ok": True, "transport": "caller"}
            return {"ok": True, "reply": reply, "post": post_result, "intent": route}
        budget = _budget_allowed(estimated_input_tokens=estimated)
        if not budget.get("allowed"):
            reply = (
                "I’m pausing this one because today’s estimated token budget is at the hard cap. "
                f"Daily estimate: {budget.get('daily_total')} tokens; projected: {budget.get('projected')}."
            )
            _record_token_usage(
                {
                    "created_at": now_iso(),
                    "channel_id": str(channel_id),
                    "user_id": str(user_id),
                    "intent": route.get("intent"),
                    "model": model,
                    "estimated_input_tokens": estimated,
                    "output_tokens": 0,
                    "tool_rounds": tool_rounds,
                    "blocked": True,
                    "reason": "daily_hard_cap",
                }
            )
        else:
            result = OpenAIResponsesClient(timeout_s=120).responses_text(
                model=model,
                instructions=_instructions_for_intent(str(route.get("intent") or "general_chat")),
                input_text=prompt,
                reasoning_effort="medium",
                max_output_tokens=900 if route.get("intent") in {"new_lead_copy", "ghl_sms_copy", "content_discovery"} else 700,
            )
            if not result.ok:
                raise RuntimeError(result.error or "OpenAI returned no live chat response")
            reply = result.text
            usage = result.usage or {}
            output_tokens = _usage_int(usage, "output_tokens", "completion_tokens")
            input_tokens = _usage_int(usage, "input_tokens", "prompt_tokens") or estimated
            _record_token_usage(
                {
                    "created_at": now_iso(),
                    "channel_id": str(channel_id),
                    "user_id": str(user_id),
                    "intent": route.get("intent"),
                    "model": result.model or model,
                    "endpoint": result.endpoint,
                    "estimated_input_tokens": input_tokens,
                    "output_tokens": output_tokens or _estimate_tokens(reply),
                    "tool_rounds": tool_rounds,
                    "context_used": live_context.get("context_used") or {},
                    "blocked": False,
                    "warn_daily_budget": bool(budget.get("warn")),
                }
            )

    history.append(
        {
            "role": "user",
            "user_id": str(user_id),
            "name": str(user_name or ""),
            "content": text,
            "created_at": now_iso(),
            "intent": route.get("intent"),
        }
    )
    assistant_name = load_webhook_profile(_load_config()).get("username") or "Reese"
    history.append(
        {
            "role": "assistant",
            "name": assistant_name,
            "content": reply,
            "created_at": now_iso(),
            "intent": route.get("intent"),
        }
    )
    del history[:-_history_limit()]
    _save_state(doc)
    post_result = post_chat_webhook(channel_id=int(channel_id), content=reply) if send_reply else {"ok": True, "transport": "caller"}
    return {"ok": True, "reply": reply, "post": post_result, "intent": route, "image_urls": reply_image_urls}
