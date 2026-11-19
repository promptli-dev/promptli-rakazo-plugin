<img src="assets/icon.png" alt="Promptli Comms" width="96" align="right">

# Promptli Comms for Rakazo

Give a [Rakazo](https://github.com/elie222/rakazo) bot its own email address and phone number. The bot can
email, text and place AI-disclosed calls, and people who email, text or leave a voicemail for it reach
the bot in Rakazo.

The repository has two parts:

- **The Rakazo plugin.** These are the files Rakazo reads: `plugin.json`, `mcp.json`, `skills/` and
  `commands/`, in the [Agent Plugins](https://agent-plugins.org) 1.1.0 format that Rakazo installs from
  GitHub. It adds the hosted Promptli Comms MCP server (OAuth, no key pasting), a `promptli-comms` skill,
  and `/promptli-setup`, `/promptli-status` and `/promptli-doctor`. Rakazo never runs code from a plugin
  repository.
- **`promptli-rakazo`.** This optional bridge service is for inbound mail, texts and voicemail. It holds
  an outbound Agent Link WebSocket to Promptli Comms and hands each person's message to the bot through
  the bot's own webhook (`POST /api/v1/bots/<botId>/webhook`), which is Rakazo's supported inbound
  path.

```
person ──email/SMS/voicemail──▶ Promptli Comms ──Agent Link──▶ promptli-rakazo ──webhook──▶ Rakazo bot
Rakazo bot ──MCP (OAuth)──▶ api.comms.promptli.com/mcp ──▶ reply_email / send_text / place_call
```

## 1. Install the plugin in Rakazo

1. Open **Integrations**, then **+**, then **From GitHub**. Paste
   `https://github.com/promptli-dev/promptli-rakazo-plugin`, then choose **Preview** and **Connect**.
   Choose **Add skills** to add the skill and commands.
2. On **Promptli Comms**, choose **Sign in**. In the browser, pick an existing agent or create one, and
   add a phone number if you want SMS and calls. Rakazo finds the OAuth server on its own (RFC 9728 and
   8414 discovery, dynamic client registration, PKCE).
3. With a bot selected, choose **Use in this bot**, then ask the bot: `/promptli-status`.

The bot can now send email and texts, place calls, and read its conversations. Tools that send something
ask for approval like any other tool that changes something.

## 2. Answer inbound messages (optional)

Rakazo's chat-app channels (Slack, Telegram, WhatsApp, iMessage) are built into the server and can't be
added by a plugin. Outside services reach a bot through its webhook instead.

1. Ask the bot to run `/promptli-setup`. It offers to create a routine named **Promptli Comms inbox** with
   a **Webhook** trigger. Copy the URL and key the routine shows.
2. On a machine that stays on (the Rakazo server itself is fine), run:

   ```bash
   uv tool install promptli-rakazo     # or: uv tool install git+https://github.com/promptli-dev/promptli-rakazo-plugin
   promptli-rakazo setup               # browser login to Promptli Comms, then paste the webhook URL and key
   ```

   `setup` shows the security warning and logs you in with your browser; no Promptli key is pasted. It
   then asks for the webhook URL, and for the key with hidden input. Both are stored in
   `~/.promptli/promptli-rakazo/rakazo.json` (mode 600). Last, it starts the background service
   (launchd on macOS, `systemd --user` on Linux).
3. In Rakazo, go to **Settings → Approvals** and allow `reply_email` and `send_text` for this bot.
   Otherwise each reply to an outside person waits for approval, and the request expires after 30
   minutes because nobody is watching a webhook run.

Each delivery arrives in the bot's chat as a JSON event that Rakazo marks as untrusted:
`promptli.email`, `promptli.sms` or `promptli.voicemail`. The event names the sender, the channel and the
tool to answer with (`reply.tool`, plus `reply.message_id` or `reply.to`). The bot replies with the
Promptli Comms tools; the bridge never sends anything itself.

**Calls.** Live calls are answered by Promptli's hosted voice assistant for the agent, configured in the
Promptli console. It says it is an AI. A webhook can't answer in real time, so the bridge doesn't
advertise voice. Voicemails come to the bot as deliveries for follow-up.

> **Security:** anyone who learns the agent's address or number can write to your bot. Use a bot made for
> outside contacts, and give it only the tools it needs. Rakazo checks tools that change something before
> outside text can use them; anything you set to **Always allow** can be used freely.

## Commands (`promptli-rakazo`)

| Command | What it does |
| --- | --- |
| `setup [--number] [--test] [--no-browser]` | Browser login, bot webhook, start the service. `--test` uses Promptli test mode, where nothing reaches real people |
| `status [--json]` | Agent, address, number, Rakazo webhook (the key is never shown) and service state |
| `doctor [--json]` | Login, channels, Agent Link, clock, service, and whether Rakazo is reachable |
| `test-delivery` | Send one `promptli.test` event to the bot's chat |
| `start` / `stop` / `restart` / `logs -f` / `run -v` | Manage the background service, or run it in the foreground |
| `logout` / `uninstall [--purge]` | Revoke the login and forget the key, or remove the service and local state |

`RAKAZO_WEBHOOK_URL` and `RAKAZO_WEBHOOK_KEY` override the stored webhook, for containers.
`PROMPTLI_API_KEY` and `PROMPTLI_BASE_URL` work as in the other Promptli plugins.

## How it maps to Rakazo

| Need | Rakazo extension point | Here |
| --- | --- | --- |
| Tools | Plugin `mcp.json` (remote `streamable-http`, OAuth discovered from the server) | `mcp.json` → `https://api.comms.promptli.com/mcp` |
| Know-how | Plugin `skills/<name>/SKILL.md` | `skills/promptli-comms` (tools, calling rules, inbound handling) |
| Slash commands | Plugin `commands/<name>.md` (become skills invoked with `/name`) | `promptli-setup`, `promptli-status`, `promptli-doctor` |
| Inbound channel | Bot webhook `POST /api/v1/bots/:botId/webhook` + a Webhook routine | `promptli-rakazo` (Agent Link → webhook) |
| Listing | Rakazo plugin marketplace (reviewed submissions) | See *Publishing* |

Built on `promptli-gateway`, which handles Agent Link, dedupe and acks, per-person queues, framing, browser
login, doctor and autostart. This repo adds only the webhook adapter (`promptli_rakazo/adapter.py`), the
payload (`promptli_rakazo/payload.py`), the CLI and the plugin files.

Known limits:
- A Rakazo bot has one chat, so every person's deliveries share it. The skill tells the bot to keep
  people apart by `contact`, and to read history with `get_conversation`.
- On current Rakazo, a webhook delivery is ignored unless the bot has an active Webhook routine. The bridge
  logs this, and `doctor` and `/promptli-doctor` point at it.

## Development

```bash
uv sync
uv run pytest                       # offline: mock HTTP transport, real gateway core, no real messages
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run python scripts/validate_plugin.py .   # Rakazo's plugin rules, offline
```

## Publishing (maintainers)

1. Push this repo public as `promptli-dev/promptli-rakazo-plugin`. Rakazo installs plugins only from
   public GitHub repositories.
2. Test it in Rakazo: **Integrations → + → From GitHub**, then Preview, then check that nothing is
   listed under skipped parts. Then **Sign in**, **Use in this bot**, and a real tool call in Promptli
   test mode.
3. Publish `promptli-rakazo` to PyPI (tag `rakazo-v0.1.1`).
4. Submit to the Rakazo marketplace at <https://rakazo.com/plugins/submit/>, or with
   `POST https://app.rakazo.com/api/v1/plugin-submissions` (`email`, `name`, `logo`, `source`,
   `description`, `testing`, `tested`). Approval pins the reviewed commit; resubmit after changes.

## License

MIT
