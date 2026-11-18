---
description: Connect this bot to Promptli Comms so it gets its own email address and phone number, and can answer people who write to it.
---

Help the user connect this bot to Promptli Comms. Do each step only after the previous one works.

1. Call the Promptli Comms `whoami` tool. If it fails with an authorization error, tell the user:
   open **Integrations**, find **Promptli Comms**, choose **Sign in**, pick or create the agent in the
   browser, then choose **Use in this bot**. Stop until they say it is done.
2. Report the agent's handle, email address and phone number (or that it has none yet), and whether it is
   in live or test mode. In test mode nothing reaches real people.
3. Outgoing mail and texts work now. To also answer people who write in, the user needs the bridge
   service, which runs on their own computer or server:
   - Offer to create a routine named "Promptli Comms inbox" with a **Webhook** trigger whose instruction
     is: "Answer each promptli.* delivery with the promptli-comms skill. Never contact anyone except the
     sender." Create it only when the user agrees.
   - Tell the user to copy the webhook URL and key the routine shows, then run on that machine:
     `uv tool install promptli-rakazo && promptli-rakazo setup`. The key is pasted there with hidden input;
     never ask for it in this chat and never repeat it.
4. Suggest allowing `reply_email` and `send_text` for this bot in **Settings → Approvals**, otherwise each
   reply to an outside person waits up to 30 minutes for approval and then is dropped.
5. Finish with one line: what works now and what is still missing.

$ARGUMENTS
