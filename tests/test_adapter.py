from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from promptli_gateway.adapter import GatewayContext, TurnDone, TurnError, TurnKind
from promptli_gateway.config import GatewayConfig
from promptli_gateway.framing import frame_email

from promptli_rakazo.adapter import RakazoWebhookAdapter, webhook_url_problem
from promptli_rakazo.gateway import RakazoGateway
from tests.conftest import email_event, text_event, voicemail_event

URL = "https://rakazo.example.com/api/v1/bots/bot_123/webhook"
KEY = "whk_test_not_a_real_key"
CTX = GatewayContext("glow", "glow@agents.promptli.com", "+15550100000", None)


class FakeRakazo:
    """Stands in for Rakazo's webhook route: records deliveries, answers like the real handler."""

    def __init__(self, statuses: list[int] | None = None, body: dict[str, Any] | None = None) -> None:
        self.statuses = statuses or []
        self.body = body or {"ok": True, "messageId": "m1", "runId": "r1", "seq": 1}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else 200
        return httpx.Response(status, json=self.body if status == 200 else {"error": "x"})

    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def adapter(fake: FakeRakazo, **kw: Any) -> RakazoWebhookAdapter:
    return RakazoWebhookAdapter(URL, KEY, transport=httpx.MockTransport(fake), retry_delays=(0, 0), **kw)


async def turn(a: RakazoWebhookAdapter, prompt: str, kind: TurnKind = TurnKind.CHANNEL) -> list[Any]:
    await a.start(CTX, [])
    return [c async for c in a.run_turn("ctc_jane", prompt, kind=kind, resume_id=None)]


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (URL, True),
        ("http://localhost:3100/api/v1/bots/b/webhook", True),
        ("http://rakazo.example.com/api/v1/bots/b/webhook", False),
        ("https://user:pw@rakazo.example.com/api/v1/bots/b/webhook", False),
        ("https://rakazo.example.com/api/v1/bots/b/webhook?token=x", False),
        ("https://rakazo.example.com/rpc/threads/send", False),
    ],
)
def test_webhook_url_rules(url: str, ok: bool) -> None:
    assert (webhook_url_problem(url) is None) is ok


async def test_delivery_posts_json_with_bearer_key_and_idempotency_and_never_auto_replies() -> None:
    fake = FakeRakazo()
    a = adapter(fake)
    chunks = await turn(a, frame_email(email_event(1, "Hello"), CTX.identity_email))
    assert chunks == [TurnDone(text="NO_REPLY")]
    req = fake.requests[0]
    assert req.method == "POST" and str(req.url) == URL
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["idempotency-key"].startswith("promptli-")
    p = fake.payloads()[0]
    assert p["event"] == "promptli.email" and p["agent"] == "glow" and p["reply"]["message_id"] == "msg_1"
    assert KEY not in req.content.decode()
    await a.close()


async def test_server_errors_are_retried_with_the_same_idempotency_key() -> None:
    fake = FakeRakazo(statuses=[503, 502])
    chunks = await turn(adapter(fake), frame_email(email_event(1, "Hello"), None))
    assert chunks == [TurnDone(text="NO_REPLY")] and len(fake.requests) == 3
    assert len({r.headers["idempotency-key"] for r in fake.requests}) == 1


async def test_bad_key_is_not_retried() -> None:
    fake = FakeRakazo(statuses=[401])
    chunks = await turn(adapter(fake), frame_email(email_event(1, "Hello"), None))
    assert isinstance(chunks[0], TurnError) and not chunks[0].retryable and len(fake.requests) == 1
    assert KEY not in chunks[0].message


async def test_gives_up_after_retries() -> None:
    fake = FakeRakazo(statuses=[500, 500, 500])
    chunks = await turn(adapter(fake), frame_email(email_event(1, "Hello"), None))
    assert isinstance(chunks[0], TurnError) and chunks[0].retryable and len(fake.requests) == 3


async def test_ignored_delivery_means_no_webhook_routine() -> None:
    fake = FakeRakazo(body={"ok": True, "ignored": True})
    chunks = await turn(adapter(fake), frame_email(email_event(1, "Hello"), None))
    assert isinstance(chunks[0], TurnError) and "webhook routine" in chunks[0].message


async def test_redirects_are_not_followed_so_the_key_never_leaves_the_host() -> None:
    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"location": "https://evil.example.net/steal"})

    a = RakazoWebhookAdapter(URL, KEY, transport=httpx.MockTransport(redirect), retry_delays=())
    chunks = await turn(a, frame_email(email_event(1, "Hello"), None))
    assert isinstance(chunks[0], TurnError)


async def test_voice_and_supervisor_turns_never_reach_the_webhook() -> None:
    fake = FakeRakazo()
    a = adapter(fake)
    assert a.capabilities.voice is False
    for kind in (TurnKind.VOICE, TurnKind.SUPERVISOR, TurnKind.POST_CALL):
        assert await turn(a, "hello", kind) == [TurnDone(text="NO_REPLY")]
    assert fake.requests == []


def test_refuses_bad_config() -> None:
    with pytest.raises(ValueError, match="https"):
        RakazoWebhookAdapter("http://rakazo.example.com/api/v1/bots/b/webhook", KEY)
    with pytest.raises(ValueError, match="key"):
        RakazoWebhookAdapter(URL, "")


# ---- the real gateway loop, with fake Promptli client and fake Rakazo ----------------------------


class FakeIdentity:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.log = log

    async def reply(self, message_id: str, **kw: Any) -> None:
        self.log.append(("reply", message_id))

    async def send_text(self, to: Any, text: str = "", **_: Any) -> None:
        self.log.append(("text", to))


class FakePromptli:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []

    def identity(self, _ref: str) -> FakeIdentity:
        return FakeIdentity(self.log)

    async def close(self) -> None:
        return None


class FakeLink:
    def __init__(self) -> None:
        self.acks: list[int] = []

    async def ack(self, seq: int) -> None:
        self.acks.append(seq)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTLI_HOME", str(tmp_path))


async def run_gateway(events: list[dict[str, Any]], fake: FakeRakazo) -> tuple[FakePromptli, FakeLink]:
    cfg = GatewayConfig(runtime="promptli-rakazo", identity="glow", batch_window_s=0.05, auto_reply=False)
    a = adapter(fake)
    client = FakePromptli()
    gw = RakazoGateway(cfg, a, client=client)  # type: ignore[arg-type]
    gw.identity_address, gw.identity_number = CTX.identity_email, CTX.identity_number
    link = FakeLink()
    gw.link = link  # type: ignore[assignment]
    await a.start(CTX, [])
    for e in events:
        await gw.handle_event(e)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if not gw._pending:  # pyright: ignore[reportPrivateUsage]
            break
    await a.close()
    gw.state.close()
    return client, link


async def test_gateway_routes_each_channel_to_the_webhook_and_never_sends_itself() -> None:
    fake = FakeRakazo()
    events = [
        email_event(1, "Can we move to 4pm?"),
        text_event(2, "running late"),
        text_event(3, "10 min"),  # same person within the batch window: one delivery
        voicemail_event(4, "please call me back"),
    ]
    client, link = await run_gateway(events, fake)
    kinds = sorted(p["event"] for p in fake.payloads())
    assert kinds == ["promptli.email", "promptli.sms", "promptli.voicemail"]
    sms = next(p for p in fake.payloads() if p["event"] == "promptli.sms")
    assert "running late\n10 min" in sms["content"]
    assert client.log == []  # replies come from the bot's MCP tools, never from the bridge
    assert link.acks and link.acks[-1] == 4
    assert {p["contact_key"] for p in fake.payloads()} == {"ctc_jane", "ctc_bob"}


async def test_same_person_on_two_channels_at_once_gets_both_delivered() -> None:
    # Plain promptli-gateway 0.2.x frames the second channel with the first one's framing and fails.
    fake = FakeRakazo()
    mail = email_event(1, "see attached")
    mail["data"]["contact_key"] = "ctc_bob"
    await run_gateway([mail, text_event(2, "did you get my email?")], fake)
    assert sorted(p["event"] for p in fake.payloads()) == ["promptli.email", "promptli.sms"]


async def test_gateway_skips_stop_keywords_and_our_own_mail() -> None:
    fake = FakeRakazo()
    stop = text_event(1, "STOP")
    stop["data"]["keyword"] = "stop"
    own = email_event(2, "loop", sender="glow@agents.promptli.com")
    await run_gateway([stop, own], fake)
    assert fake.requests == []
