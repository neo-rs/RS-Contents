# Reese Smarter Live Chat Implementation Status

## Purpose

This file tracks what exists, what is being built, what is placeholder-only, and what remains pending for the smarter Reese live chat upgrade.

If implementation work runs out of context, update this file before stopping.

## Current Snapshot

Current live chat exists and is wired through RSAdminBot.

Implemented today before the smarter upgrade:

- `MarketingKnowledgeBase/agent/live_chat.py`
- RSAdminBot live chat gateway bridge
- Reese webhook replies
- channel-specific short-term chat history
- active review run summary
- `help`, `status`, `active run`, `current run`
- `remember: ...`
- durable memory through `agent_memory.json`
- setup doc: `MARKETING_AI_LIVE_CHAT_SETUP.md`

Main limitation:

- Reese can chat, but it does not yet have enough server tools/context to understand new leads, mentioned channels, role access, GHL/SMS docs, or future ticket workflows.

## Implementation Checklist

### Phase 1 - Intent Router

Status: implemented

Implemented files:

- `MarketingKnowledgeBase/agent/live_intent.py`

Implemented tasks:

- Added deterministic intent router that returns strict dict/JSON-compatible output without burning tokens.
- Added intents:
  - `active_review_help`
  - `new_lead_copy`
  - `channel_question`
  - `role_access_question`
  - `ghl_sms_copy`
  - `market_research`
  - `server_setup_question`
  - `remember_rule`
  - `general_chat`
- Add future placeholder intents:
  - `ticket_support`
  - `cancellation_save`
  - `help_inquiry`
  - `suggestion_channel_reply`
- Added CLI smoke command: `python -m MarketingKnowledgeBase.run_tool agent_route_chat --message "..."`

Acceptance:

- Message about `#online-important` with no success post routes to `new_lead_copy`.
- `status` routes to `active_review_help`.
- Random greeting routes to `general_chat`.

### Phase 2 - Live Chat Tools

Status: partially implemented

Implemented files:

- `MarketingKnowledgeBase/agent/live_tools.py`

Implemented tools:

- `resolve_discord_references`
- `fetch_recent_channel_messages`
- `inspect_replied_message`
- `search_current_server_context`
- `check_role_channel_access`
- `search_ticket_context` reserved/not-enabled response
- `search_ghl_sms_docs`
- `pull_market_context`
- `answer_server_setup_question`

Notes:

- Ticket/cancellation context returns a clear not-wired response for now.
- Role access does not invent permissions if live permission overwrites are not available.
- Market context extracts source-message market clues but does not invent live comps.
- Drafting is handled by `live_chat.py` with the focused evidence pack, not a separate send/post tool.

### Phase 3 - Smart Context Builder

Status: implemented

Implemented files:

- `MarketingKnowledgeBase/agent/live_context_builder.py`

Implemented tasks:

- Gathers context based on intent.
- Loads active run only when the route requires it.
- Fetches referenced channel context only for intents that need it.
- Prefers replied-to messages over recent channel messages.
- Builds an evidence pack for answer generation.
- Tracks context usage counts in the evidence pack and token usage log.

Acceptance:

- New lead requests load lead/channel context, not only active review draft.

### Phase 4 - Layered Memory

Status: partially implemented

Already exists:

- `data/agent_chat_sessions.json`
- `data/agent_memory.json`
- `data/marketing_memory.json`
- `data/agent_runs/`

Implemented in this pass:

- Added configurable short-term history limit in `agent.chat.short_term_history_limit`.

Pending:

- Add `data/agent_sessions/<session_id>.json`.
- Add session memory for:
  - GHL SMS draft session
  - future ticket support
  - future cancellation save
  - future help inquiry
  - future suggestion channel discussion
- Add memory promotion classifier.
- Add memory expiration/summarization.

Planned files:

- `MarketingKnowledgeBase/agent/memory_promotion.py`

### Phase 5 - Knowledge Indexes

Status: pending

Planned data files:

- `data/server_knowledge_index.json`
- `data/channel_role_index.json`
- `data/content_knowledge_index.json`
- `data/ghl_sms_knowledge_index.json`

Pending:

- Build index from config and synced Discord context.
- Add CLI command to rebuild indexes.
- Add search command.
- Add index usage to live chat context builder.

### Phase 6 - Voice Profile

Status: implemented

Implemented config:

- `agent.voice_profile`
- `agent.webhook_profile`
- `MarketingKnowledgeBase/agant-ai-discord-webhook-profile/profile.json`

Voice requirements:

- not too formal
- street-smart RS tone
- no cursing
- does not kiss ass
- does not overpromise
- grounded in facts
- market details when available
- confident but not fake hype
- cancellation saves persuasive but not desperate

Implemented:

- Added config section.
- Loaded voice profile into live chat prompts, new lead drafting, GHL/SMS drafting, and future workflow guardrails.
- Renamed the webhook identity to Reese.
- Added shared Reese webhook profile loader in `MarketingKnowledgeBase/agent/webhook_profile.py`.

### Phase 7 - New Lead Copy Flow

Status: partially implemented

Target behavior:

- Detects `new_lead_copy`.
- Resolves mentioned/replied channel/message.
- Fetches recent mentioned-channel messages through Discord Gateway when available, with `live_context.json` fallback.
- Extracts source-message price/market clues from text.
- Drafts as lead/alert when there is no success post.
- Avoids member win/profit/checkout claims unless sourced.

Pending:

- Vision on source attachments/images for this live chat flow.

Acceptance example:

Input:

```text
what can you write for this lead tho there is no success post for it #online-important
```

Expected:

- Bot does not rely only on active Cantu review run unless that is actually the referenced lead.
- Bot says this should be framed as lead/alert.
- Bot drafts grounded copy.
- Bot lists what not to claim.

### Phase 8 - GHL/SMS Readiness

Status: partially implemented

Implemented:

- Added local `search_ghl_sms_docs`.
- GHL/SMS requests route to `ghl_sms_copy`.
- Output is draft-only through live chat prompts.
- Live GHL/SMS sending is not enabled.

Pending:

- Dedicated `validate_sms_rules` module.
- Dedicated reusable `draft_ghl_sms` tool outside the live chat prompt.

Future-only:

- GHL API connector
- contact segments
- campaign execution
- SMS performance memory

### Phase 9 - Ticket/Cancellation Readiness

Status: placeholder only

Do not enable live ticket/cancellation behavior yet.

Pending future requirements:

- ticket data source
- member context rules
- privacy policy
- cancellation/save offer rules
- escalation rules
- audit logging

Placeholder intents:

- `ticket_support`
- `cancellation_save`
- `help_inquiry`
- `suggestion_channel_reply`

Expected current response:

- "This workflow is reserved but not wired yet."

### Phase 10 - Token Budget Monitoring

Status: implemented

Planned files:

- `data/agent_token_usage.json`

Implemented:

- Estimates tokens per live chat request.
- Logs model, intent, context usage, tool rounds, input/output token usage to `data/agent_token_usage.json`.
- Adds soft/hard daily cap config.
- Enforces per-intent input caps from config.
- Enforces `max_tool_rounds` from config.
- Honors `token_budget_enabled`.
- Routes normal drafts to `gpt-4o` and hard/final work to `gpt-5.5`.
- Keeps routing deterministic to avoid burning tokens on simple classification.

Target model routing:

- Deterministic v1 router: intent routing without token burn.
- `gpt-4o-mini`: future low-cost AI classification/summaries only if explicitly enabled.
- `gpt-4o`: normal chat and drafts.
- `gpt-5.5`: hard reasoning, claim repair, market reasoning, cancellation strategy.

### Phase 11 - Live Chat Orchestrator

Status: partially implemented

Current:

- `handle_live_chat_message()` now saves user/assistant memory, routes intent, gathers focused context, chooses model, generates an answer/draft, logs token usage, saves the assistant reply, and posts through webhook.

Target:

```text
handle_live_chat_message()
  -> save user message
  -> route intent
  -> gather context with tools
  -> choose model
  -> generate answer/draft
  -> validate claims if needed
  -> classify memory promotion
  -> save assistant reply
  -> post webhook
```

Pending:

- Dedicated claim validation module for live lead drafts.
- Memory promotion classifier.
- More granular tool-round accounting if future model tool calling is added.

## Placeholder Inventory

These should exist as safe placeholders, not full live implementations:

- ticket support
- cancellation saves
- help inquiry channel
- suggestion channel assistant
- live GHL/SMS sending
- live market API/comps
- live role permission overwrites if not yet synced

## Files Expected To Change During Implementation

Likely files:

- `MarketingKnowledgeBase/agent/live_chat.py`
- `MarketingKnowledgeBase/agent/live_intent.py`
- `MarketingKnowledgeBase/agent/live_tools.py`
- `MarketingKnowledgeBase/agent/live_context_builder.py`
- `MarketingKnowledgeBase/agent/memory.py`
- `MarketingKnowledgeBase/agent/memory_promotion.py`
- `MarketingKnowledgeBase/agent/validation.py`
- `MarketingKnowledgeBase/run_tool.py`
- `MarketingKnowledgeBase/config.json`
- `MARKETING_AI_LIVE_CHAT_SETUP.md`

Possible files:

- `RSAdminBot/admin_bot.py`
- `scripts/verify_marketing_ai_flow.py`
- `scripts/deploy_marketing_ai_flow.py`

## Verification Checklist

Run after implementation:

```powershell
python -m compileall MarketingKnowledgeBase
python scripts\verify_marketing_ai_flow.py
```

Manual Discord tests:

- `help`
- `status`
- `hey`
- `what can you write for this lead tho there is no success post for it #online-important`
- `remember: don't say members cooked unless there is a success screenshot`
- `write a GHL SMS for this`
- `who can see this channel?`

Oracle checks:

```bash
systemctl is-active mirror-world-rsadminbot.service
systemctl is-active mirror-world-marketing-daily-post.timer
systemctl is-enabled mirror-world-marketing-review-agent.service || true
systemctl is-active mirror-world-marketing-review-agent.service || true
```

Expected:

```text
rsadminbot active
daily-post timer active
old marketing-review-agent disabled/inactive
```

## Notes For Future Codex/Agent

If you resume this task:

1. Read this file first.
2. Read `REESE_SMARTER_LIVE_CHAT_BUILD_PLAN.md`.
3. Inspect current `agent/live_chat.py`.
4. Implement one phase at a time.
5. Update this status file after every phase.
6. Do not enable ticket/cancellation live behavior until data source, privacy, and policy rules are configured.
7. Keep GHL/SMS as draft-only until explicit approval for sending is given.
