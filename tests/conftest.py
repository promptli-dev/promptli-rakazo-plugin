from __future__ import annotations

from typing import Any

EMAIL_SENDER = "jane@example.com"
PHONE = "+15550100001"  # fictional 555-01xx number


def email_event(seq: int, body: str, sender: str = EMAIL_SENDER) -> dict[str, Any]:
    return {
        "id": f"evt_{seq}",
        "seq": seq,
        "type": "message.received",
        "data": {
            "message": {
                "id": f"msg_{seq}",
                "thread_id": "thr_1",
                "from": sender,
                "subject": "Hi",
                "text": body,
                "attachments": [],
                "folder": "inbox",
            },
            "contacts": [{"id": "ctc_jane", "name": "Jane"}],
            "contact_key": "ctc_jane",
        },
    }


def text_event(seq: int, body: str, sender: str = PHONE, group: bool = False) -> dict[str, Any]:
    conv: dict[str, Any] = {"id": "conv_1", "is_group": group}
    if group:
        conv["remotes"] = [sender, "+15550100002"]
    return {
        "id": f"evt_{seq}",
        "seq": seq,
        "type": "text.received",
        "data": {
            "text": {"id": f"txt_{seq}", "from": sender, "body": body, "media": []},
            "conversation": conv,
            "contacts": [],
            "contact_key": "ctc_bob",
        },
    }


def voicemail_event(seq: int, said: str) -> dict[str, Any]:
    return {
        "id": f"evt_{seq}",
        "seq": seq,
        "type": "call.ended",
        "data": {
            "call": {"id": "call_9", "from": PHONE, "outcome": "voicemail"},
            "transcript": [{"party": "caller", "text": said}],
            "contacts": [{"name": "Bob"}],
            "contact_key": "ctc_bob",
        },
    }
