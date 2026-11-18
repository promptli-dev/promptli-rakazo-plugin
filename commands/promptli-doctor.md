---
description: Diagnose why Promptli Comms email, texts or inbound deliveries are not working for this bot.
---

Check each item and report one line per check, marked ok or FAIL, with the fix for each failure:

1. **Tools**: is the Promptli Comms server assigned to this bot? Call `whoami`. An authorization error
   means **Sign in** under **Integrations**; a missing tool means **Use in this bot**.
2. **Mode**: live or test. In test mode messages are simulated.
3. **Number**: does the agent have a phone number? Without one, texts and calls are unavailable.
4. **Recent activity**: call `list_conversations` with a limit of 5. If people wrote in recently but no
   `promptli.*` delivery appeared in this chat, the bridge is not delivering: on the machine running it,
   run `promptli-rakazo doctor`, and check that this bot still has an active routine with a Webhook trigger.
5. **Approvals**: if replies were drafted but not sent, `reply_email` or `send_text` is waiting for
   approval; suggest allowing them for this bot in **Settings → Approvals**.

Never ask for or display keys, tokens or passwords. Do not send any message or place any call while
diagnosing.
