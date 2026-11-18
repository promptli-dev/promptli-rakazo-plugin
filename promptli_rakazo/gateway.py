"""The gateway loop for Rakazo.

promptli-gateway 0.2.x keeps one worker per contact and frames its whole queue with the channel of the
first event. When the same person emails and then texts (or leaves a voicemail) while that worker is
alive, the second event is framed as the wrong channel and the turn fails. The Claude Code plugin hides
this behind one long-lived session per person; a webhook has no session to keep, so give each
(contact, channel) pair its own queue instead.
"""

from __future__ import annotations

from typing import Any

from promptli_gateway.core import Gateway

SEP = "|"


class RakazoGateway(Gateway):
    def _route(self, event: dict[str, Any]) -> Any:
        job = super()._route(event)
        if job is not None and not job.session_key.endswith(SEP + job.channel):
            job.session_key = f"{job.session_key}{SEP}{job.channel}"
        return job


def contact_key(session_key: str) -> str:
    return session_key.rsplit(SEP, 1)[0]
