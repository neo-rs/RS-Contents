# Captain Hook Smarter Live Chat Build Plan

## Purpose

Captain Hook currently works as a live chat assistant, but it is still mostly a conversational layer over recent chat history and the active marketing review run. The next build should turn it into a smarter RS server assistant that can understand what staff is asking, choose the right tools, retrieve the right context, and answer with grounded RS-style copy or guidance.

The first focus is making the current live chat smarter for marketing/content/server-context questions. Ticket support, cancellation saves, help channels, and suggestion channels should be designed into the architecture now, but they should remain placeholders until their data sources and policies are wired.

Target shift:

```text
Current:
Captain Hook can chat with recent memory and active draft context.

Target:
Captain Hook understands RS channels, roles, leads, content, marketing memory, GHL/SMS needs, and active drafts through tools and layered memory.
```

## Current Baseline

Current live chat implementation:

- RSAdminBot receives messages through Discord Gateway.
- RSAdminBot passes live chat messages to `MarketingKnowledgeBase.agent.live_chat.handle_live_chat_message()`.
- Replies are posted through the configured Captain Hook webhook.
- Recent chat is stored in `MarketingKnowledgeBase/data/agent_chat_sessions.json`.
- Durable memory rules are stored in `MarketingKnowledgeBase/data/agent_memory.json`.
- Active review context is read from `MarketingKnowledgeBase/data/agent_review_state.json` and `data/agent_runs/`.
- The model can answer `help`, `status`, `active run`, `current run`, and `remember: ...`.

Current limitation:

- The assistant does not yet inspect referenced channels.
- It does not fetch recent messages from channels like `online-important`.
- It does not inspect the Discord message being replied to.
- It does not resolve channel mentions into channel IDs and source context.
- It does not search server setup, role permissions, ticket docs, or GHL/SMS docs.
- It does not have a real intent router.
- It often overuses the active review run even when the user is asking about a different lead.

## Phase 1 - Intent Router

Goal: before answering, Captain Hook should decide what kind of request this is and which tools/context are needed.

Add an intent router module:

```text
MarketingKnowledgeBase/agent/live_intent.py
```

Supported initial intents:

- `active_review_help`
- `new_lead_copy`
- `channel_question`
- `role_access_question`
- `ghl_sms_copy`
- `market_research`
- `server_setup_question`
- `remember_rule`
- `general_chat`

Future-ready placeholder intents:

- `ticket_support`
- `cancellation_save`
- `help_inquiry`
- `suggestion_channel_reply`

Router input:

- user ID
- user display name
- channel ID
- raw message text
- mentioned channel IDs
- replied-to message ID, if available
- active run summary
- last 10-20 chat messages

Router output:

```json
{
  "intent": "new_lead_copy",
  "confidence": 0.87,
  "referenced_channel_ids": ["1255590577144201358"],
  "needs_tools": ["resolve_channel_mention", "fetch_recent_channel_messages", "draft_lead_copy"],
  "requires_active_run": false,
  "memory_scope": "short_term",
  "reason": "User asked for copy for a lead in online-important and explicitly said there is no success post."
}
```

Routing model:

- Use `gpt-5.4-mini` or configured fast model.
- Keep prompt small.
- Return strict JSON.
- Do not use GPT-5.5 for simple intent routing unless the router fails or the request is high-risk.

Acceptance tests:

- `hey` -> `general_chat`
- `status` -> `active_review_help`
- `revise: less hype` -> `active_review_help`
- `what can you write for this lead in #online-important no success post` -> `new_lead_copy`
- `who has access to this channel` -> `role_access_question`
- `write a GHL sms for this` -> `ghl_sms_copy`
- `remember: don't claim limited spots unless configured` -> `remember_rule`

## Phase 2 - Live Chat Tool Layer

Goal: give Captain Hook real tools for server context and content work.

Add tool module:

```text
MarketingKnowledgeBase/agent/live_tools.py
```

Initial tools to implement:

### `resolve_discord_references`

Purpose:

- Parse Discord channel mentions like `<#1255590577144201358>`.
- Map channel IDs to channel names using synced Discord channel data.
- Detect whether a user is referring to the current channel, mentioned channel, active review, or replied-to message.

Input:

```json
{
  "message_text": "what can you write for this lead #online-important",
  "channel_id": "1517746409112080404",
  "reply_message_id": ""
}
```

Output:

```json
{
  "referenced_channels": [
    {
      "channel_id": "1255590577144201358",
      "channel_name": "online-important",
      "bucket": "important"
    }
  ],
  "reply_message_id": "",
  "reference_type": "mentioned_channel"
}
```

### `fetch_recent_channel_messages`

Purpose:

- Fetch recent messages from a specific Discord channel using bot token.
- Return normalized text, author, timestamp, attachments, embeds, and message link.

Safety:

- Only allow configured guild/channel IDs.
- Cap message count, default 10-25.
- Do not expose secrets or private system channels unless configured.

Output should include:

- message ID
- channel ID/name
- posted timestamp
- text excerpt
- attachments
- embed images
- URLs
- reactions
- message link

### `inspect_replied_message`

Purpose:

- If the user replies to a Discord message, fetch and normalize that exact message.
- This is critical for questions like "write this up" or "what can we say for this lead?"

Future behavior:

- If the replied message has image attachments, pass them to vision.
- If it has product/store/price clues, extract deal facts.

### `search_current_server_context`

Purpose:

- Search current synced knowledge in `data/live_context.json`, daily archives, weekly archives, and story candidates.
- Used when the user asks, "what happened with this," "what channels have this," or "what do we know about this product/lead?"

Search inputs:

- keywords
- bucket filter
- channel ID
- time window
- max results

### `check_role_channel_access`

Purpose:

- Answer questions about who can see a channel and what roles have access.
- Future-ready for deeper permission analysis.

Initial v1:

- Use cached channel map/config where available.
- If exact Discord permission overwrites are not synced yet, return "permission data unavailable" instead of guessing.

Future v2:

- Pull live channel permission overwrites from Discord API.
- Map role IDs to role names.
- Explain access in plain English.

### `search_ticket_context`

Purpose:

- Placeholder for future ticket support.
- Do not answer ticket-specific questions until the ticket data source is configured.

Initial behavior:

```json
{
  "ready": false,
  "reason": "Ticket context tools are reserved but not wired yet."
}
```

### `search_ghl_sms_docs`

Purpose:

- Search GHL/SMS docs, prior SMS copy, rules, and campaign examples.
- Initially local-doc based.
- Future version can connect to GHL APIs.

Sources to search first:

- `telnyx_discord_sms_bridge/GHL_WIRING_README.md`
- GHL summary files in the repo root
- existing marketing memory
- approved/rejected examples
- future `data/ghl_sms_library.json`

### `pull_market_context`

Purpose:

- Pull market details when available without inventing.

Initial v1:

- Extract price, retail, market/resale clues from source message.
- Search recent archive messages for matching product/store.
- If no live market API is configured, say market data is not verified.

Future v2:

- Add approved market data providers/APIs.
- Cache market lookups with timestamps.

### `draft_lead_copy`

Purpose:

- Draft content for a lead/alert when there is no success post.
- Must avoid "members cooked", "members hit", profit claims, checkout claims, or resale claims unless sourced.

Inputs:

- source message
- channel/bucket
- destination profile
- memory/tone profile
- market context

Output:

- structured draft
- claim list
- unsupported claim list
- source references

### `answer_server_setup_question`

Purpose:

- Answer questions about current setup from config/docs.
- Use config and docs first, not model memory.

Examples:

- "what channel does Captain Hook listen in?"
- "what service runs daily posts?"
- "where is the webhook configured?"
- "which role can use this?"

## Phase 3 - Smart Context Gathering

Goal: make the assistant gather only the context required for the detected intent.

Context rules:

- For `general_chat`: use recent chat + durable memory only.
- For `active_review_help`: load active run + latest draft + validation.
- For `new_lead_copy`: resolve channel/replied message, fetch recent messages, extract lead facts, maybe image vision, then draft.
- For `channel_question`: resolve channel, search config/channel map.
- For `role_access_question`: resolve channel, inspect permission data if available, otherwise explain limitation.
- For `ghl_sms_copy`: gather SMS rules, source facts, audience/destination, then draft short plain text.
- For `market_research`: gather source facts and cached/retrieved market context. Do not fake comps.
- For future `ticket_support`: load ticket/session context only after ticket tools are wired.

Add context builder:

```text
MarketingKnowledgeBase/agent/live_context_builder.py
```

Output:

```json
{
  "intent": "new_lead_copy",
  "context_used": {
    "chat_history_messages": 8,
    "active_run_loaded": false,
    "referenced_channels": ["online-important"],
    "source_messages": 5,
    "vision_attempted": false,
    "memory_items": 6,
    "ghl_docs": 0
  },
  "evidence_pack": {
    "primary_message": {},
    "recent_channel_messages": [],
    "market_context": {},
    "memory": []
  }
}
```

## Phase 4 - Layered Memory

Goal: make memory useful without storing every message forever.

### Short-Term Chat Memory

Storage:

```text
data/agent_chat_sessions.json
```

Behavior:

- Store last 20-50 messages per channel/thread.
- Include role, user ID, display name, content, timestamp.
- Keep current cap configurable.
- Used only for immediate conversation continuity.

Config:

```json
{
  "agent": {
    "chat": {
      "short_term_history_limit": 40
    }
  }
}
```

### Ticket/Session Memory

Storage:

```text
data/agent_sessions/<session_id>.json
```

Initial placeholders:

- ticket support session
- cancellation save session
- help inquiry session
- GHL campaign draft session
- suggestion channel thread/session

Fields:

```json
{
  "session_id": "",
  "session_type": "ghl_sms_campaign",
  "channel_id": "",
  "thread_id": "",
  "user_id": "",
  "status": "open",
  "expires_at": "",
  "messages": [],
  "summary": "",
  "important_context": [],
  "do_not_store": []
}
```

Behavior:

- Temporary by default.
- Expires after configured window.
- Summarized when long.
- Not promoted to durable memory unless memory promotion says it is important.

### Durable RS Brain

Storage:

```text
data/agent_memory.json
```

Memory scopes:

- `global_rs_memory`
- `content_type_memory`
- `channel_memory`
- `do_not_claim_memory`
- future `ticket_policy_memory`
- future `cancellation_save_memory`
- future `ghl_sms_memory`

Durable memory should include only high-value facts/rules:

- server offer
- role meanings
- channel purpose
- what not to claim
- cancellation save rules
- tone/style rules
- GHL/SMS rules
- known policies

Do not store:

- random small talk
- private user details unless necessary and approved
- secrets
- raw ticket history forever
- every prompt/response

### Knowledge Indexes

Initial indexes:

```text
data/server_knowledge_index.json
data/channel_role_index.json
data/content_knowledge_index.json
data/ghl_sms_knowledge_index.json
```

Index content:

- channel map
- role access map
- recent important posts
- success posts
- ticket docs placeholders
- onboarding docs
- SMS/GHL copy library
- previous approved/rejected examples

Build command:

```bash
python -m MarketingKnowledgeBase.run_tool agent_rebuild_indexes
```

Future command:

```bash
python -m MarketingKnowledgeBase.run_tool agent_search_index --query "online important Cantu"
```

### Memory Promotion

Add module:

```text
MarketingKnowledgeBase/agent/memory_promotion.py
```

Memory classification:

- `durable_rule`
- `temporary_session_context`
- `conversation_noise`
- `forget`
- `needs_human_confirmation`

Promotion rules:

- `remember: ...` stores directly, scoped by command.
- Free-form messages are not automatically stored as durable memory unless classifier says high value.
- Sensitive/private ticket context should remain session-scoped.
- If uncertain, save as session summary or ask for confirmation.

Example output:

```json
{
  "classification": "durable_rule",
  "scope": "do_not_claim_memory",
  "text": "Do not say members checked out unless checkout screenshots or source text prove it.",
  "confidence": 0.92
}
```

## Phase 5 - Better Voice Profile

Goal: move the voice from scattered prompts into config/memory.

Add config section:

```json
{
  "agent": {
    "voice_profile": {
      "name": "RS Captain Hook",
      "style": [
        "street-smart RS tone",
        "not too formal",
        "confident but not fake hype",
        "grounded in facts",
        "market details when available",
        "persuasive but not desperate"
      ],
      "avoid": [
        "cursing",
        "kissing ass",
        "overpromising",
        "fake urgency",
        "claiming profit without proof",
        "claiming members cooked without success proof"
      ],
      "content_rules": {
        "lead_without_success_post": "Frame as lead/alert. Do not claim checkouts, profit, or member wins.",
        "cancellation_save": "Win back with value and confidence. Do not beg or over-discount unless policy allows.",
        "ghl_sms": "Short, direct, no Discord formatting."
      }
    }
  }
}
```

Prompt behavior:

- Load voice profile into live chat, draft tools, GHL/SMS tools, and future ticket tools.
- Keep the assistant human and RS-native, but no cursing.
- Be direct when facts are missing:
  - "I would not say anyone hit this yet."
  - "This is better framed as a lead, not a win."
  - "Market data is not verified from source yet."

## Phase 6 - New Lead Copy Flow

Goal: fix the exact failure shown in the screenshot.

Expected behavior for:

```text
what can you write for this lead tho there is no success post for it #online-important
```

Flow:

1. Intent router detects `new_lead_copy`.
2. Resolver detects referenced channel `online-important`.
3. Tool fetches recent messages from that channel.
4. Tool picks likely lead:
   - replied-to message first
   - newest relevant lead second
   - asks clarification if multiple plausible leads
5. Extract facts:
   - product
   - store/platform
   - price
   - retail/MSRP
   - market/resale if sourced
   - urgency hints
   - attachments/images
6. If images exist, run vision.
7. Draft lead/alert copy.
8. Validate claims.
9. Reply with:
   - suggested copy
   - what not to claim
   - source confidence
   - optional next actions

Correct answer shape:

```text
Yeah, since there is no success post yet, I would not frame it like members hit.
This is a lead/alert angle.

[draft]

I would avoid:
- checkout claims
- profit claims
- "members cooked"
- resale numbers unless the source shows it
```

## Phase 7 - GHL/SMS Readiness

Goal: make Captain Hook useful for GHL/SMS content ideas without sending live SMS yet.

Initial behavior:

- Draft-only.
- No GHL sending.
- No contact access.
- No campaign execution.

Tools:

- `search_ghl_sms_docs`
- `draft_ghl_sms`
- `validate_sms_rules`

SMS draft rules:

- plain text
- short
- no Discord mentions
- no custom emoji
- no unsupported claims
- optional compliance/opt-out footer only if configured
- audience and CTA must be explicit

Future placeholders:

- GHL contact segment lookup
- campaign history
- automation performance
- opt-out/compliance policies

## Phase 8 - Future Ticket And Cancellation Readiness

Do not build full ticket support yet. Make it ready.

Placeholders:

- `ticket_support` intent
- `cancellation_save` intent
- `search_ticket_context` tool
- `load_member_context` tool
- `draft_ticket_reply` tool
- `draft_cancellation_save` tool

Required before enabling:

- ticket data source
- privacy rules
- what member info can be used
- refund/cancel policy
- approved save offers
- escalation rules
- logging/audit rules

Cancellation tone:

- persuasive, not desperate
- does not kiss ass
- reinforces value
- gives market/member benefit
- no fake scarcity
- no promises beyond policy

## Phase 9 - Token Budget Controls

Goal: prevent runaway GPT-5.5 usage.

Add token budget config:

```json
{
  "agent": {
    "token_budget": {
      "simple_chat_max_input_tokens": 4000,
      "active_review_max_input_tokens": 10000,
      "channel_context_max_input_tokens": 18000,
      "ticket_context_max_input_tokens": 30000,
      "max_tool_rounds": 5,
      "warn_after_daily_estimated_tokens": 250000,
      "hard_stop_after_daily_estimated_tokens": 750000
    }
  }
}
```

Model routing:

- `gpt-5.4-mini`: intent classify, memory triage, cheap summaries
- `gpt-5.4`: normal chat, normal drafts, GHL/SMS first pass
- `gpt-5.5`: hard content, claim repair, market reasoning, cancellation strategy, final review

Logging:

```text
data/agent_token_usage.json
```

Track:

- date
- channel ID
- user ID
- intent
- model
- estimated input tokens
- output tokens
- tool rounds
- cost tier label
- blocked/allowed

Rules:

- Never load full channel history by default.
- Fetch recent messages only after intent requires it.
- Summarize old sessions.
- Cache server/role/channel indexes.
- Use retrieval snippets instead of full files.
- Hard cap tool rounds.

## Phase 10 - Live Chat Response Orchestrator

Goal: one smarter entrypoint for live chat.

Replace current simple direct model call with:

```text
handle_live_chat_message()
  -> save user message to short-term memory
  -> route intent
  -> gather context with tools
  -> choose model
  -> generate answer/draft
  -> validate claims when content is drafted
  -> run memory promotion
  -> save assistant reply
  -> post webhook reply
```

The assistant should always know:

- what intent it is answering
- what context it used
- what it did not know
- whether claims are sourced
- whether it should store anything

## Phase 11 - CLI And Testing

Add CLI commands:

```bash
python -m MarketingKnowledgeBase.run_tool agent_route_chat --message "..."
python -m MarketingKnowledgeBase.run_tool agent_live_context --message "..."
python -m MarketingKnowledgeBase.run_tool agent_rebuild_indexes
python -m MarketingKnowledgeBase.run_tool agent_search_index --query "..."
python -m MarketingKnowledgeBase.run_tool agent_memory_promote --message "..."
python -m MarketingKnowledgeBase.run_tool agent_token_usage --today
```

Tests:

- Intent router classifies examples correctly.
- Mentioned channel resolves correctly.
- Recent messages fetch from configured channel.
- Replied-to message is preferred over random recent channel messages.
- Lead without success post does not claim member wins.
- GHL/SMS output has no Discord formatting.
- Role access question does not invent permissions.
- Token budget blocks oversized context.
- Memory promotion does not store small talk as durable memory.
- `remember:` stores durable/channel memory.

Manual acceptance:

1. Ask `hey`.
   - Bot replies naturally and briefly.
2. Ask `status`.
   - Bot returns active review run or says none.
3. Ask about `#online-important` lead with no success post.
   - Bot fetches recent channel messages and drafts lead/alert copy.
4. Ask `who can see this channel?`
   - Bot uses role/channel access data or says permission data is not wired.
5. Ask for GHL SMS.
   - Bot drafts short SMS and says live sending is not enabled.
6. Say `remember: no member cooked claims without screenshots`.
   - Bot stores durable memory.
7. Ask a ticket/cancellation question.
   - Bot says ticket tools are reserved but not enabled yet, unless configured.

## Deployment Plan

1. Implement locally behind config flags.
2. Run compile checks.
3. Run local CLI tests.
4. Deploy through `Deploy Marketing AI Flow`.
5. Verify RSAdminBot active.
6. Verify old polling service disabled/inactive.
7. Test in live chat channel:
   - `help`
   - `status`
   - lead copy request
   - GHL SMS request
   - `remember: ...`
8. Watch logs for errors.

## Config Flags

Add these flags to allow safe rollout:

```json
{
  "agent": {
    "chat": {
      "smart_router_enabled": true,
      "channel_tools_enabled": true,
      "role_tools_enabled": false,
      "ticket_tools_enabled": false,
      "ghl_sms_tools_enabled": true,
      "market_tools_enabled": false,
      "memory_promotion_enabled": true,
      "token_budget_enabled": true
    }
  }
}
```

Default:

- channel tools on
- GHL/SMS draft tools on
- memory promotion on
- token budget on
- ticket tools off
- cancellation tools off
- live market API off unless configured

## Key Risks

- Token burn if channel/ticket history is loaded too aggressively.
- Wrong answers if Discord permissions are not actually synced.
- Privacy risk if ticket/member context is stored durably.
- Duplicate responses if old polling service and RSAdminBot chat bridge both run.
- Overclaiming if source facts are not validated.
- Staff may expect it to know a channel/message when no mention/reply is provided.

Mitigations:

- Intent router first.
- Tool-gathered context only.
- Clear "I do not have that wired yet" responses.
- Token budgets.
- Memory promotion rules.
- Claim validation.
- Feature flags.
