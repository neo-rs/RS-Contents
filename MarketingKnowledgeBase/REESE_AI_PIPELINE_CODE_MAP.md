# Reese AI Pipeline Code Map

Last updated: 2026-06-21

This document maps which Python files are actually used by Reese, scheduled marketing posts, review replies, and the MarketingKnowledgeBase archive. It also records the fixes made after Reese missed uploaded photos and gave vague chat answers.

## Current Oracle Findings

- The 10pm EST post was the last scheduled post because `systemd/mirror-world-marketing-daily-post.timer` is configured as `08..22:00:00 America/New_York`.
- Oracle timer status showed `mirror-world-marketing-daily-post.timer` active, with next run at 8am New York time.
- Oracle `daily_post_state.json` showed the last completed slot as `2026-06-20:h22`.
- Reese chat channel `1517746409112080404` showed Jake sending source context and photos, but Reese responded as if it could not see the photos.
- The live chat issue was code wiring, not just the model. GPT-5.5 is used for harder/final quality work, but chat routing and evidence loading decide what the model actually receives.

## Main Runtime Entrypoints

### Standalone Reese Chat Bot

Entrypoint:

- `MarketingKnowledgeBase/agent/reese_bot.py`

Oracle service:

- `mirror-world-reesebot.service`

What it does:

- Logs in as the Reese Discord bot.
- Listens to the configured Reese chat channel.
- Builds Discord context from current message, replies, linked messages, mentioned channels, and recent channel messages.
- Calls `handle_live_chat_message()` from `MarketingKnowledgeBase/agent/live_chat.py`.
- Sends Reese's reply back as the Reese bot.

Important fix:

- Current-message attachments and embed images are now included in `discord_context["current_message"]`.
- Recent messages from the Reese chat channel are now included, so “photo I sent above” has a chance to resolve from recent context.
- Image-only messages are no longer ignored.
- ReeseBot can now attach selected source images back to its reply.

### Live Chat Brain

Entrypoint:

- `MarketingKnowledgeBase/agent/live_chat.py`

What it does:

- Routes each chat message using `agent/live_intent.py`.
- Builds a focused evidence pack using `agent/live_context_builder.py`.
- Chooses a model based on intent:
  - `general_chat`, setup, role questions: `agent.chat.model` fallback.
  - `new_lead_copy`, `content_discovery`, `market_research`, `ghl_sms_copy`: `agent.chat.draft_model`.
  - `active_review_help`, cancellation/save workflows: `agent.chat.quality_model`.
- Calls OpenAI through `OpenAIResponsesClient`.
- Logs estimated token usage to `data/agent_token_usage.json`.

Important fix:

- If the current Discord message has attachments/images and the text is vague, it is forced into `new_lead_copy` instead of generic chat.
- The live chat response now returns `image_urls` so `reese_bot.py` can attach them in Discord.
- Instructions now explicitly tell Reese not to say it cannot see images when current/primary evidence includes attachments.

### Intent Router

File:

- `MarketingKnowledgeBase/agent/live_intent.py`

What it does:

- Cheap deterministic routing before model calls.
- Avoids burning GPT tokens just to classify every chat.

Important fix:

- Added content drafting hints:
  - `make me`
  - `make it`
  - `draft`
  - `win post`
  - `member win`
  - `instore win`
  - `hype up members`
  - `add in the photo`
  - `screenshot`
- This prevents normal content requests from falling into the future ticket/cancellation placeholder.

### Live Context Builder

File:

- `MarketingKnowledgeBase/agent/live_context_builder.py`

What it does:

- Converts routed intent into a focused evidence pack.
- Uses:
  - current message
  - replied message
  - linked Discord message
  - recent mentioned channel messages
  - archive search results
  - setup facts
  - ticket/GHL placeholders

Important fix:

- Adds `current_message` to the evidence pack.
- Allows `current_message` to become `primary_message` when it has images.
- Keeps source images available to the final ReeseBot sender.

## Scheduled Review Posts

Entrypoint:

- `MarketingKnowledgeBase/run_daily_post.py`

Oracle timer:

- `systemd/mirror-world-marketing-daily-post.timer`

Oracle service:

- `systemd/mirror-world-marketing-daily-post.service`

Flow:

1. Systemd timer runs hourly from 8am through 10pm New York time.
2. `run_daily_post.py` checks whether current hour is in `publishing.schedule.post_hours`.
3. It checks `data/daily_post_state.json` so the same slot does not post twice.
4. It calls `generate_and_post_agent_review()` in `MarketingKnowledgeBase/agent_review_post.py`.
5. The draft is posted to the review channel unless `--production` is used.

Why nothing posted after 10pm:

- This is expected with the current timer.
- Current configured post hours are `8` through `22`.
- Next scheduled post after 10pm is 8am the next day.

## Review Draft Generation

Main file:

- `MarketingKnowledgeBase/agent_review_post.py`

Flow:

1. Picks an archive story using `what_you_missed_post.pick_top_story_id()`.
2. Calls `agent.workflow.agent_generate()`.
3. Agent/tool orchestration can use:
   - `get_story_context`
   - `get_post_assets`
   - `describe_images`
   - `search_related_context`
   - `draft_content`
   - `critique_content`
4. Posts draft through `post_publisher.publish_marketing_draft()`.
5. Posts text-command controls for review.

Related files:

- `MarketingKnowledgeBase/agent/workflow.py`
- `MarketingKnowledgeBase/agent/orchestrator.py`
- `MarketingKnowledgeBase/agent/tools.py`
- `MarketingKnowledgeBase/agent/validation.py`
- `MarketingKnowledgeBase/post_publisher.py`

## Marketing Copy Writer

File:

- `MarketingKnowledgeBase/ai_writer.py`

Used by:

- `MarketingKnowledgeBase/agent/tools.py` through `draft_content()`.
- `MarketingKnowledgeBase/run_tool.py generate_marketing_copy`.
- Some DM sequence generation helpers.

Not used by:

- Normal live chat answer generation in `agent/live_chat.py`.

What it does:

- Uses archive-backed story candidates.
- Builds grounding context from the primary story, memory, voice rules, offers, and related research.
- Calls OpenAI through `OpenAIResponsesClient`.
- Produces `body_markdown`, source link, assets, deal facts, model routing, and rule violations.

## CLI / MCP Backend

File:

- `MarketingKnowledgeBase/run_tool.py`

Purpose:

- Command-line backend for manual tools and MCP-style calls.
- Useful for smoke tests, showing runs, routing chat, listing candidates, generating draft copy, and inspecting token usage.

Examples:

- `python -m MarketingKnowledgeBase.run_tool agent_route_chat --message "..."`
- `python -m MarketingKnowledgeBase.run_tool agent_live_context --message "..."`
- `python -m MarketingKnowledgeBase.run_tool agent_token_usage --today`
- `python -m MarketingKnowledgeBase.run_tool generate_marketing_copy --story-id "..."`

This file is not the persistent live bot. It is a CLI surface.

## Knowledge Sync

Entrypoint:

- `MarketingKnowledgeBase/sync.py`

Core sync file:

- `MarketingKnowledgeBase/discord_sync.py`

What it stores:

- `MarketingKnowledgeBase/data/live_context.json`
- `MarketingKnowledgeBase/data/story_candidates.json`
- `MarketingKnowledgeBase/data/daily/*/live_context.json`
- `MarketingKnowledgeBase/data/daily/*/story_candidates.json`
- `MarketingKnowledgeBase/data/weekly/*/live_context.json`
- `MarketingKnowledgeBase/data/weekly/*/story_candidates.json`

How channels are selected:

- `config.json` source buckets can include:
  - `channel_ids`
  - `extra_channel_ids`
  - `category_id`
  - `category_ids`
  - `channel_name_contains`

Important fix:

- `important_instore` now includes the two instore categories:
  - `1400165387001135134`
  - `1341477669682024588`
- Future syncs should be able to archive theater/instore channels from those categories.

## Image Handling

Scheduled/review posts:

- Images come from story candidate `attachments` and `embed_images`.
- `post_publisher.py` downloads image URLs and posts them as Discord files.
- Asset filtering prevents mixing images from a different source message.

Live chat before this fix:

- ReeseBot normalized attachments but did not include the current message in the evidence pack.
- Image-only messages were skipped because empty text returned early.
- Reese replies were text-only, so it could say `[Attach photo]` but not attach anything.

Live chat after this fix:

- Current message media is part of the evidence pack.
- Recent channel message media is available.
- Media-only messages are handled.
- `live_chat.py` returns selected image URLs.
- `reese_bot.py` attaches downloaded images when sending Reese's reply.

## Model Routing

Configured in:

- `MarketingKnowledgeBase/config.json`

Current chat model fields:

- `agent.chat.model`: general chat/setup style replies.
- `agent.chat.draft_model`: lead/content/GHL-style drafts.
- `agent.chat.router_model`: reserved/configured router tier.
- `agent.chat.quality_model`: active review and harder quality work.

Current stage model fields:

- `agent.model_by_stage.extract`
- `agent.model_by_stage.classify`
- `agent.model_by_stage.draft`
- `agent.model_by_stage.rewrite`
- `agent.model_by_stage.critique`
- `agent.model_by_stage.repair`
- `agent.model_by_stage.final`
- `agent.model_by_stage.vision`

Important clarification:

- GPT-5.5 is not “just for chatting.”
- Reese was vague because the evidence pack was missing images/data, and routing sent some content requests to placeholders.
- Model quality cannot fix missing context. The code must pass the right Discord messages, images, archive data, and tool results into the model.

## Remaining Gaps

- The newly added instore category config still needs a successful sync/deploy cycle on Oracle before old/new theater posts appear in MKB archives.
- Live market comps are still not a real external provider. Reese can use source-provided eBay/resell clues, but not independently verify live market prices unless a provider is added.
- Ticket/cancellation support is still intentionally placeholder-only.
- Role/channel permission explanations still need fuller role-map sync for human-readable access answers.
- `run_tool.py agent_live_context` does not yet simulate current-message attachments; it is mostly for text/channel reference tests.

## Quick Debug Commands

Oracle timer status:

```bash
systemctl list-timers mirror-world-marketing-daily-post.timer --no-pager
systemctl show mirror-world-marketing-daily-post.service -p Result -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp --no-pager
```

Oracle logs:

```bash
journalctl -u mirror-world-marketing-daily-post.service --since "today" --no-pager
journalctl -u mirror-world-reesebot.service --since "today" --no-pager
```

Local compile check:

```bash
python -m py_compile MarketingKnowledgeBase/agent/reese_bot.py MarketingKnowledgeBase/agent/live_chat.py MarketingKnowledgeBase/agent/live_context_builder.py MarketingKnowledgeBase/agent/live_intent.py
```

