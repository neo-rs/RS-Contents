# Reese Standalone Bot Implementation Log

Date: 2026-06-20

## Why This Change Was Made

Reese was previously replying through a Discord webhook while `RSAdminBot` listened to the live chat channel. That caused Discord to show `RSAdminBot is typing`, even though the visible webhook message used Reese's name/avatar.

The goal of this change was to make Reese a real Discord bot client:

- Reese should show as the typing user in the live chat channel.
- Reese should send live-chat replies as the Reese bot account.
- RSAdminBot should stay as the admin/review-control bridge.
- Existing Reese AI logic, memory, routing, archive search, and context tools should remain the same.
- Webhook delivery should remain available as a fallback only.

## Channels In Scope

- Review channel: `1517212561697673347`
- Reese live chat channel: `1517746409112080404`

Both are now listed in `agent.reese_bot.bot_owned_channel_ids` in `MarketingKnowledgeBase/config.json`.

## Main Files Added

### `MarketingKnowledgeBase/agent/reese_bot.py`

New standalone Discord client for Reese.

What it does:

- Logs into Discord using the token from `MarketingKnowledgeBase/config.secrets.json`.
- Uses Discord intents for `guilds`, `messages`, `message_content`, and `reactions`.
- Listens to the Reese live chat channel.
- Ignores bot messages to avoid loops.
- Builds the same Discord context RSAdminBot used to pass:
  - current message ID and URL
  - mentioned channel IDs
  - replied-to message
  - linked Discord messages
  - recent messages from mentioned channels
  - attachments and embed image URLs
- Calls `handle_live_chat_message(..., send_reply=False)` so the AI brain generates a reply without posting through webhook.
- Sends the reply as the Reese bot account.
- Falls back to webhook posting if bot sending fails and fallback is enabled.

## Main Files Changed

### `MarketingKnowledgeBase/agent/live_chat.py`

Added `send_reply: bool = True` to `handle_live_chat_message()`.

Why:

- Old behavior: generate reply and immediately post through webhook.
- New behavior: ReeseBot can generate only, then send the reply itself as the bot account.
- Existing RSAdminBot/webhook behavior still works when `send_reply=True`.

### `MarketingKnowledgeBase/config.json`

Added:

```json
"reese_bot": {
  "enabled": true,
  "disable_rsadminbot_chat_bridge": true,
  "token_secret_keys": [
    "reese_bot_token",
    "discord_bot_token",
    "bot_token"
  ],
  "bot_owned_channel_ids": [
    1517212561697673347,
    1517746409112080404
  ],
  "fallback_to_webhook": true,
  "controls_as_bot": false
}
```

Important behavior:

- `enabled: true` turns on standalone ReeseBot behavior.
- `disable_rsadminbot_chat_bridge: true` prevents duplicate live-chat replies.
- `fallback_to_webhook: true` keeps old webhook posting as emergency fallback.
- `controls_as_bot: false` intentionally keeps review control/button posts out of the new ReeseBot transport for now, because Discord button interactions can be tied to the application that posted them.

### `RSAdminBot/admin_bot.py`

Changed `_marketing_agent_chat_channel_id()` so RSAdminBot returns `0` for the Reese chat channel when standalone ReeseBot is enabled.

Why:

- Prevents RSAdminBot from responding in the Reese live chat channel.
- Prevents duplicate messages.
- Prevents Discord from showing `RSAdminBot is typing` in the Reese chat channel.

RSAdminBot still remains active for:

- Admin tooling
- Review controls
- Button interactions
- Existing server/bot management flows

### `MarketingKnowledgeBase/post_publisher.py`

Changed publishing behavior for Reese-owned channels.

Before:

- If a webhook existed for a channel, publishing preferred the webhook.

After:

- For channels listed in `agent.reese_bot.bot_owned_channel_ids`, publishing prefers the Reese bot token.
- If the token is missing and a webhook exists, it can fall back to webhook.
- Other non-Reese channels keep existing behavior.

Why:

- Review draft bodies in `1517212561697673347` should be sent by Reese when possible.
- Normal publishing outside Reese-owned channels should not be unexpectedly changed.

### `MarketingKnowledgeBase/discord_log.py`

Added config support for preferring Reese bot transport, but gated it behind `controls_as_bot`.

Current behavior:

- `controls_as_bot` is `false`.
- Review controls/buttons remain on the safer existing path.

Why:

- The review channel uses buttons/components.
- Moving button posts to a different Discord application can break or complicate interaction routing unless fully tested.

### `MarketingKnowledgeBase/secrets.py`

Updated `discord_bot_token()` lookup order to support Reese token keys:

1. `reese_bot_token`
2. `discord_bot_token`
3. `bot_token`
4. fallback to existing bot secret locations

Current Oracle verification showed the remote secret has `discord_bot_token`.

### `MarketingKnowledgeBase/config.secrets.example.json`

Added `reese_bot_token` documentation.

No real token is stored in this example file.

### `systemd/mirror-world-reesebot.service`

New systemd service:

```ini
ExecStart=/home/rsadmin/bots/mirror-world/.venv/bin/python -m MarketingKnowledgeBase.agent.reese_bot
```

This runs ReeseBot independently from RSAdminBot.

### `scripts/install_marketing_knowledge_timer.sh`

Updated to install and enable `mirror-world-reesebot.service`.

Also updated wording:

- RSAdminBot remains the admin/review-controls bridge.
- ReeseBot owns the live chat bot identity.

### `scripts/run_oracle_deploy_marketing_knowledge.py`

Updated local-to-Oracle deploy path to include:

- `systemd/mirror-world-reesebot.service`

Also updated systemd copy logic so the Reese service is uploaded to the remote `systemd` folder.

### `scripts/update_oracle_marketing_chat_secrets.py`

Updated secret sync helper to include Reese bot token keys:

- `reese_bot_token`
- `discord_bot_token`
- `bot_token`

This script still avoids printing secret values.

## Server Changes Made On Oracle

Oracle path:

`/home/rsadmin/bots/mirror-world`

Actions completed:

- Uploaded updated `MarketingKnowledgeBase` directly to Oracle.
- Installed `mirror-world-reesebot.service`.
- Enabled and started `mirror-world-reesebot.service`.
- Uploaded updated `RSAdminBot/admin_bot.py` directly to Oracle.
- Restarted `mirror-world-rsadminbot.service`.

## Verified On Oracle

Remote compile passed for:

- `MarketingKnowledgeBase/agent/reese_bot.py`
- `MarketingKnowledgeBase/agent/live_chat.py`
- `MarketingKnowledgeBase/post_publisher.py`
- `MarketingKnowledgeBase/discord_log.py`
- `MarketingKnowledgeBase/secrets.py`
- `RSAdminBot/admin_bot.py`

Remote config/secret verification:

- `reese_bot.enabled`: `true`
- `disable_rsadminbot_chat_bridge`: `true`
- bot-owned channels:
  - `1517212561697673347`
  - `1517746409112080404`
- remote secret has `discord_bot_token`: `true`

Remote service status:

- `mirror-world-reesebot.service`: `active`
- `mirror-world-rsadminbot.service`: `active`

ReeseBot journal showed:

```text
ReeseBot ready as Reese (1517836946557370378); chat_channel=1517746409112080404; review_channel=1517212561697673347
```

## Expected Behavior Now

### Reese Live Chat Channel

Channel: `1517746409112080404`

Expected:

- Reese bot should show as typing.
- Reese bot should send the reply.
- RSAdminBot should not answer this channel.
- Reese still uses the same AI brain, context builder, archive search, memory, and token logging.
- If bot sending fails, webhook fallback can still send the message.

### Review Channel

Channel: `1517212561697673347`

Expected:

- Scheduled/manual review draft bodies should prefer Reese bot transport.
- RSAdminBot still handles review controls and admin/button workflows.
- Controls are intentionally not forced through ReeseBot yet because `controls_as_bot` is `false`.

## What Was Intentionally Not Changed

- RSAdminBot still runs.
- RSAdminBot still owns admin commands and review control logic.
- Existing webhook URLs were not removed.
- Existing review button/control architecture was not fully moved to ReeseBot.
- Ticket/cancellation/GHL live-send workflows were not enabled.
- Discord Developer Portal bot bio cannot be set from this runtime code.

## Discord Bio Note

Reese's bio text is stored in:

- `MarketingKnowledgeBase/agant-ai-discord-webhook-profile/profile.json`
- `MarketingKnowledgeBase/config.json`

But Discord bot profile bio/description must be set in the Discord Developer Portal. A normal bot token cannot update that visible profile bio through this code.

## Safety Notes

The safest part of this setup:

- ReeseBot owns only the live-chat identity.
- RSAdminBot keeps the complex admin/review-control behavior.
- Webhook fallback remains available.

Main risk:

- If the Reese token is invalid, missing Message Content Intent, or removed from the server, ReeseBot will fail or not read messages.
- RSAdminBot will not reply in the Reese chat channel while `disable_rsadminbot_chat_bridge` is `true`.

## Quick Rollback

If ReeseBot misbehaves and you need the old RSAdminBot/webhook live chat back:

1. Disable ReeseBot:

```bash
sudo systemctl disable --now mirror-world-reesebot.service
```

2. Edit `MarketingKnowledgeBase/config.json`:

```json
"disable_rsadminbot_chat_bridge": false
```

3. Restart RSAdminBot:

```bash
sudo systemctl restart mirror-world-rsadminbot.service
```

That restores RSAdminBot as the live-chat listener and webhook as the sender.

## Useful Status Commands

```bash
systemctl is-active mirror-world-reesebot.service
systemctl is-active mirror-world-rsadminbot.service
journalctl -u mirror-world-reesebot.service -n 80 --no-pager
journalctl -u mirror-world-rsadminbot.service -n 80 --no-pager
```

## Current Recommendation

Test in the Reese chat channel first:

```text
hey Reese what's the best content right now?
```

Expected result:

- Discord should show Reese typing.
- Reese should answer from the real bot account.
- The answer should use the archive search tool and include source/message/image details when available.

## Follow-Up Cleanup

After the standalone bot deploy, `MarketingKnowledgeBase/agent/live_tools.py` still had old setup-answer wording that described live chat as `RSAdminBot on_message` plus webhook transport.

That was cleaned up so Reese setup answers now report:

- `mirror-world-reesebot.service` as the Reese live-chat service.
- ReeseBot Discord Gateway as the live-chat listener.
- Reese bot account sending as the primary chat response transport.
- Webhook as fallback only.
- RSAdminBot as still active for admin tooling and review controls/buttons.
- RSAdminBot chat bridge disabled for the Reese live chat channel.
