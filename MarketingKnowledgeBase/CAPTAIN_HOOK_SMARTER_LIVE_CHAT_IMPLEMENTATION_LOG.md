# Captain Hook Smarter Live Chat Implementation Log

## Purpose

This document records what was implemented for the smarter Captain Hook live chat upgrade, what is real/live-ready now, and what remains future-only. It is meant to prevent stale placeholders from being forgotten in code.

## Model Rule

No unconfirmed model names were added to runtime config or code.

Current model usage:

- `gpt-4o-mini`: confirmed existing low-cost tier, reserved in config as `router_model` for future optional AI routing.
- `gpt-4o`: normal chat, new lead drafts, and GHL/SMS draft generation.
- `gpt-5.5`: hard/final reasoning path and existing quality/final stages.

The v1 intent router is deterministic, so normal intent classification does not burn OpenAI tokens.

## Implemented Flow

The live chat now follows this flow:

```text
RSAdminBot on_message
  -> confirm message is in Captain Hook live chat channel
  -> check allowed staff role/user
  -> collect Discord context from mentions/replies
  -> fetch recent messages from mentioned channels when Discord Gateway permits it
  -> call MarketingKnowledgeBase.agent.live_chat.handle_live_chat_message()
  -> save short-term user message
  -> route intent
  -> gather focused context/evidence
  -> choose configured model
  -> generate reply/draft
  -> log token usage
  -> save assistant reply
  -> post through Captain Hook webhook
```

Key behavior change:

- Active review context is no longer injected into every normal answer.
- New lead questions use referenced channel/replied message context first.
- Ticket/cancellation/member-specific workflows are recognized but return a clear not-wired response.

## Implemented Files

### `MarketingKnowledgeBase/agent/live_intent.py`

Implemented deterministic intent routing for:

- `active_review_help`
- `new_lead_copy`
- `channel_question`
- `role_access_question`
- `ghl_sms_copy`
- `market_research`
- `server_setup_question`
- `remember_rule`
- `general_chat`
- `ticket_support`
- `cancellation_save`
- `help_inquiry`
- `suggestion_channel_reply`

Future ticket/cancellation/suggestion intents are recognized only so Captain Hook can say the workflow is not wired yet instead of guessing.

### `MarketingKnowledgeBase/agent/live_tools.py`

Implemented local/server context tools:

- `resolve_discord_references`
- `fetch_recent_channel_messages`
- `inspect_replied_message`
- `search_current_server_context`
- `check_role_channel_access`
- `search_ticket_context`
- `search_ghl_sms_docs`
- `pull_market_context`
- `answer_server_setup_question`

Important limitations:

- `fetch_recent_channel_messages` uses Discord Gateway context passed by `RSAdminBot` when available and falls back to `MarketingKnowledgeBase/data/live_context.json`.
- `check_role_channel_access` will not invent access rules if live permission overwrites are not available.
- `pull_market_context` extracts source-message clues only. There is no live market comp API enabled.
- `search_ticket_context` is a reserved/not-enabled response.

### `MarketingKnowledgeBase/agent/live_context_builder.py`

Implemented focused context building by intent:

- `new_lead_copy` gathers replied message, mentioned channel messages, primary source, and source-message market clues.
- `ghl_sms_copy` gathers local GHL/SMS docs.
- `role_access_question` checks available channel/permission context and refuses to guess.
- `server_setup_question` answers from config facts.
- `ticket_support`, `cancellation_save`, and `help_inquiry` return reserved/not-wired context.

### `MarketingKnowledgeBase/agent/live_chat.py`

Upgraded the live chat orchestrator:

- Routes every message before prompting.
- Builds an evidence pack based on intent.
- Avoids active review context unless the route requires it.
- Loads the new voice profile from config.
- Uses configured models only.
- Logs token usage to `MarketingKnowledgeBase/data/agent_token_usage.json`.
- Keeps `remember:` and `status` fast/direct.
- Keeps GHL/SMS as draft-only.

### `RSAdminBot/admin_bot.py`

Enhanced the Discord gateway bridge:

- Passes mentioned channel IDs to live chat.
- Passes replied-to message context when available.
- Normalizes source message text, author, attachments, embed images, timestamps, and message links.
- Fetches recent messages from up to two mentioned channels with the bot's Discord Gateway access.

### `MarketingKnowledgeBase/run_tool.py`

Added local smoke commands:

```powershell
python -m MarketingKnowledgeBase.run_tool agent_route_chat --message "what can you write for this lead no success post <#1255590577144201358>"
python -m MarketingKnowledgeBase.run_tool agent_live_context --message "what can you write for this lead no success post <#1255590577144201358>" --channel-id 1517746409112080404
python -m MarketingKnowledgeBase.run_tool agent_token_usage --today
```

### `MarketingKnowledgeBase/config.json`

Added live chat settings:

- `agent.chat.draft_model`: `gpt-4o`
- `agent.chat.router_model`: `gpt-4o-mini`
- `agent.chat.quality_model`: `gpt-5.5`
- `agent.chat.short_term_history_limit`: `40`
- smart-flow feature flags

Added:

- `agent.voice_profile`
- `agent.token_budget`

## Implemented User-Facing Improvements

For the user example:

```text
what can you write for this lead tho there is no success post for it #online-important
```

Captain Hook should now:

- Detect `new_lead_copy`.
- Resolve the mentioned channel.
- Fetch recent messages from that channel when available.
- Prefer a replied-to source message if the staff reply was attached to one.
- Avoid using the active review draft unless the request is actually about the active run.
- Draft a lead/alert angle.
- Avoid checkout, profit, and member-win claims unless the source proves them.

## Placeholders And Future-Only Items

These are intentionally not live implementations yet:

- Full ticket support.
- Cancellation save replies using member/ticket context.
- Help inquiry workflow.
- Suggestion channel auto-replies.
- Live GHL contact lookup.
- Live GHL/SMS campaign sending.
- Live market comp API.
- Vision analysis for live chat source images.
- Durable memory promotion classifier.
- Session memory files under `data/agent_sessions/`.
- Exact Discord role/channel access explanation from permission overwrites.

Current behavior for these:

- Captain Hook recognizes the intent.
- Captain Hook explains the workflow is reserved/not wired.
- Captain Hook does not fabricate data.

## Verification Commands

Run locally:

```powershell
python -m compileall MarketingKnowledgeBase RSAdminBot
python scripts\verify_marketing_ai_flow.py
python -m MarketingKnowledgeBase.run_tool agent_route_chat --message "what can you write for this lead no success post <#1255590577144201358>"
python -m MarketingKnowledgeBase.run_tool agent_live_context --message "what can you write for this lead no success post <#1255590577144201358>" --channel-id 1517746409112080404
```

Oracle/service verification after deploy:

```bash
systemctl is-active mirror-world-rsadminbot.service
systemctl is-active mirror-world-marketing-daily-post.timer
systemctl is-enabled mirror-world-marketing-review-agent.service || true
systemctl is-active mirror-world-marketing-review-agent.service || true
```

Expected:

- `mirror-world-rsadminbot.service` active.
- `mirror-world-marketing-daily-post.timer` active.
- old marketing review poller disabled/inactive unless intentionally re-enabled.

## Notes For Future Work

Do not add placeholder model names. Use the models confirmed in config unless the OpenAI account is checked and config is updated deliberately.

Do not enable ticket/cancellation behavior until data source, privacy policy, save rules, refund/cancel rules, and escalation rules are documented.

Do not enable live GHL/SMS sending from Captain Hook without explicit approval. Current behavior is draft-only.
