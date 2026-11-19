"""Turn a gateway turn into the JSON body of a Rakazo bot webhook delivery.

Rakazo fences every webhook body as untrusted data before the bot sees it, so the body carries the
facts the bot needs (who, which channel, what they said) and how to answer with the Promptli Comms
tools. Nothing is answered automatically: the bot replies with `reply_email` or `send_text` itself.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Rakazo refuses webhook bodies over 64 KB; stay well under it after JSON escaping.
MAX_CONTENT_CHARS = 24_000

_CLOSE = "</external_content>"
_MESSAGE_ID = re.compile(r"^Message id: (\S+)", re.M)
_FROM_EMAIL = re.compile(r"^From: .*?<([^>\s]+)>\s*$", re.M)
_FROM_TEXT = re.compile(r"^From: (?:.* )?(\+?[0-9][0-9 ()-]{5,})\s*$", re.M)
_CALL = re.compile(r"\bcall ([A-Za-z0-9_-]+)\.\s*$", re.M)


_CHANNEL = re.compile(r'<external_content channel="([a-z-]+)">')
_VOICEMAIL_FROM = re.compile(r"^New voicemail from .*?\((\+?[0-9][0-9 ()-]{5,})\)", re.M)


def channel_of(prompt: str) -> str:
    """email | sms | sms-group | voicemail, from the gateway's framing (session keys may be bare contact
    ids such as `ctc_...`, so they do not say which channel a turn came from)."""
    m = _CHANNEL.search(prompt)
    channel = m.group(1) if m else "message"
    if channel == "sms" and "in a group conversation" in prompt.split("\n", 1)[0]:
        return "sms-group"
    return channel


def contact_of(channel: str, prompt: str, session_key: str) -> str:
    pattern = {"email": _FROM_EMAIL, "sms": _FROM_TEXT, "sms-group": _FROM_TEXT, "voicemail": _VOICEMAIL_FROM}
    m = pattern[channel].search(prompt) if channel in pattern else None
    if not m:
        return session_key
    value = m.group(1)
    return value.lower() if channel == "email" else re.sub(r"[ ()-]", "", value)


def strip_runtime_instructions(prompt: str) -> str:
    """The gateway ends each framed turn with instructions for runtimes whose final answer is sent back
    (\"write the reply as your final answer\"). A webhook delivery has no final answer, so drop them."""
    end = prompt.rfind(_CLOSE)
    return prompt if end < 0 else prompt[: end + len(_CLOSE)]


def reply_hint(channel: str, prompt: str) -> dict[str, Any]:
    if channel == "email":
        mid = _MESSAGE_ID.search(prompt)
        return {
            "tool": "reply_email",
            "message_id": mid.group(1) if mid else None,
            "note": "Reply in the same thread with reply_email and this message id, or do nothing.",
        }
    if channel in ("sms", "sms-group"):
        who = _FROM_TEXT.search(prompt)
        return {
            "tool": "send_text",
            "to": re.sub(r"[ ()-]", "", who.group(1)) if who else None,
            "note": "Short plain text, no markdown. For a group, text every participant listed.",
        }
    call = _CALL.search(prompt)
    return {
        "tool": None,
        "call_id": call.group(1) if call else None,
        "note": "Follow up only if useful. Callbacks follow the calling rules in the promptli-comms skill.",
    }


def build_payload(session_key: str, prompt: str, *, identity: str | None = None) -> dict[str, Any]:
    channel = channel_of(prompt)
    content = strip_runtime_instructions(prompt)
    truncated = len(content) > MAX_CONTENT_CHARS
    if truncated:
        content = content[:MAX_CONTENT_CHARS] + "\n[truncated: read the rest with get_conversation]"
    return {
        "event": "promptli." + ("sms" if channel == "sms-group" else channel),
        "source": "promptli-comms",
        "agent": identity,
        "channel": channel,
        "contact": contact_of(channel, prompt, session_key),
        "contact_key": session_key,
        "content": content,
        "truncated": truncated,
        "reply": reply_hint(channel, prompt),
        "auto_reply": False,
    }


def idempotency_key(session_key: str, prompt: str) -> str:
    """The same inbound turn retried after a crash maps to the same key, so Rakazo runs it once."""
    return "promptli-" + hashlib.sha256(f"{session_key}\n{prompt}".encode()).hexdigest()[:40]
