"""RakazoWebhookAdapter: hands each Promptli Comms turn to a Rakazo bot through its inbound webhook.

Rakazo's supported way for an outside service to reach a bot is `POST /api/v1/bots/{botId}/webhook`
with the bot's webhook key as a bearer token. The delivery lands in the bot's chat (and runs its
webhook routines); the bot answers people with the Promptli Comms MCP tools from the plugin. The
webhook is one-way, so a turn is "done" once Rakazo accepted it, and nothing is auto-replied.

Live calls are not routed here: Promptli's hosted voice assistant answers them (it discloses that it
is an AI), and voicemails arrive as deliveries for follow-up.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import httpx
from promptli_gateway.adapter import (
    AdapterCapabilities,
    GatewayContext,
    TurnChunk,
    TurnDone,
    TurnError,
    TurnKind,
)
from promptli_gateway.tools import ToolSpec

from promptli_rakazo import __version__
from promptli_rakazo.payload import build_payload, idempotency_key

log = logging.getLogger("promptli.rakazo")

RUNTIME = "promptli-rakazo"
NO_REPLY = "NO_REPLY"


class WebhookIgnored(Exception):
    pass


def webhook_url_problem(url: str) -> str | None:
    """None when `url` looks like a Rakazo bot webhook we may send a key to, else the reason."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "not a URL"
    if parts.username or parts.password:
        return "the URL must not contain credentials"
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        return "use https (http is only allowed for localhost)"
    if not parts.path.rstrip("/").endswith("/webhook") or "/api/v1/bots/" not in parts.path:
        return "expected https://<rakazo>/api/v1/bots/<botId>/webhook"
    if parts.query or parts.fragment:
        return "the URL must not have a query or fragment (the key goes in the Authorization header)"
    return None


class RakazoWebhookAdapter:
    name = "rakazo"
    capabilities = AdapterCapabilities(
        streams_text=False, native_tools=False, resumable_sessions=False, interruptible=False, voice=False
    )

    def __init__(
        self,
        url: str,
        secret: str,
        *,
        timeout: float = 30.0,
        retry_delays: tuple[float, ...] = (2.0, 10.0, 30.0),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        problem = webhook_url_problem(url)
        if problem:
            raise ValueError(f"Rakazo webhook URL: {problem}")
        if not secret:
            raise ValueError("Rakazo webhook key is missing")
        self.url = url
        self._headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "User-Agent": f"promptli-rakazo/{__version__}",
        }
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False)
        self.retry_delays = retry_delays
        self.ctx: GatewayContext | None = None
        self.delivered = 0

    async def start(self, ctx: GatewayContext, tools: list[ToolSpec]) -> None:
        # The bot gets the Promptli tools from the plugin's hosted MCP server, not from this process.
        self.ctx = ctx

    async def deliver(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        res = await self._client.post(
            self.url, json=payload, headers={**self._headers, "Idempotency-Key": key}
        )
        if res.status_code == 401:
            raise PermissionError("Rakazo refused the webhook key (rotate it and run setup again)")
        if res.status_code >= 300:
            raise httpx.HTTPStatusError(f"HTTP {res.status_code}", request=res.request, response=res)
        body: dict[str, Any] = res.json() if res.content else {}
        if body.get("ignored"):
            raise WebhookIgnored("the bot has no active webhook routine, so Rakazo ignored the delivery")
        return body

    async def run_turn(
        self, session_key: str, prompt: str, *, kind: TurnKind, resume_id: str | None
    ) -> AsyncIterator[TurnChunk]:
        if kind is not TurnKind.CHANNEL:
            yield TurnDone(text=NO_REPLY)  # calls are answered by the hosted assistant, not this bot
            return
        identity = self.ctx.identity_handle if self.ctx else None
        payload = build_payload(session_key, prompt, identity=identity)
        key = idempotency_key(session_key, prompt)
        error: TurnError | None = None
        body: dict[str, Any] = {}
        # The gateway acks a turn once it ends, so retry here: Rakazo restarting must not drop a message.
        # The idempotency key makes a retry of an accepted delivery a no-op on Rakazo's side.
        for delay in (*self.retry_delays, None):
            error = await self._attempt(payload, key, body)
            if error is None or not error.retryable or delay is None:
                break
            log.warning("%s; retrying in %.0fs", error.message, delay)
            await asyncio.sleep(delay)
        if error is not None:
            yield error
            return
        self.delivered += 1
        log.info(
            "delivered %s turn for %s to Rakazo (run %s)", payload["channel"], session_key, body.get("runId")
        )
        yield TurnDone(text=NO_REPLY)

    async def _attempt(self, payload: dict[str, Any], key: str, out: dict[str, Any]) -> TurnError | None:
        try:
            out.update(await self.deliver(payload, key))
        except PermissionError as e:
            return TurnError(str(e), retryable=False)
        except WebhookIgnored as e:
            log.warning("%s", e)
            return TurnError(str(e), retryable=False)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            return TurnError(f"Rakazo webhook returned HTTP {code}", retryable=code >= 500 or code == 429)
        except httpx.HTTPError as e:
            return TurnError(f"Rakazo unreachable: {type(e).__name__}", retryable=True)
        return None

    async def interrupt(self, session_key: str) -> None:
        return None

    async def reset_session(self, session_key: str) -> None:
        return None

    async def close(self) -> None:
        await self._client.aclose()
