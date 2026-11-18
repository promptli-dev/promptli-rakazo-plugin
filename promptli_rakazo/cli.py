"""`promptli-rakazo`: connect a Rakazo bot to Promptli Comms email, texts and voicemail."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import socket
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx
import typer
from promptli import AsyncPromptli, OAuthCredential, OAuthToken, PromptliError, browser_login
from promptli_gateway.config import DEFAULT_BASE_URL, GatewayConfig, home_dir
from promptli_gateway.setup.autostart import autostart_status, install_autostart, uninstall_autostart
from promptli_gateway.setup.bootstrap import BootstrapResult, bootstrap
from promptli_gateway.setup.doctor import doctor as run_doctor

from promptli_rakazo import __version__
from promptli_rakazo.adapter import RUNTIME, RakazoWebhookAdapter, webhook_url_problem
from promptli_rakazo.gateway import RakazoGateway
from promptli_rakazo.settings import RakazoSettings, settings_path

app = typer.Typer(
    help="Connect a Rakazo bot to Promptli Comms: inbound email, texts and voicemail reach the bot.",
    no_args_is_help=True,
    add_completion=False,
)

BASE_URL_ENV = "PROMPTLI_BASE_URL"

STRANGER_WARNING = (
    "Anyone who knows the agent's email address or phone number can reach your Rakazo bot. Each delivery "
    "is marked as untrusted outside data, and Rakazo checks tools that change something before an outside "
    "message can use them, but the bot can still be talked into using any tool you set to Always allow. "
    "Point this at a bot made for outside contacts, with only the tools it needs."
)

ROUTINE_HINT = (
    'In Rakazo, give the bot a routine with a Webhook trigger (ask the bot: "create a webhook routine '
    'named Promptli Comms inbox that answers each promptli.* delivery with the promptli-comms skill"), '
    "then copy the webhook URL and key it shows."
)


def _base_url(opt: str | None) -> str:
    return opt or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL


def _print_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _show_bootstrap(res: BootstrapResult) -> None:
    if res.status == "error" and res.error:
        typer.secho(f"Setup failed: {res.error.get('code')}: {res.error.get('message')}", fg="red")
        raise typer.Exit(1)
    typer.secho(f"Agent @{res.identity} is ready ({res.env} mode).", fg="green")
    if res.email_address:
        typer.echo(f"  email  {res.email_address}")
    typer.echo(f"  phone  {res.phone_number or 'none (run setup --number to add one)'}")


def _ask_webhook(url: str | None) -> RakazoSettings:
    typer.echo("\n" + ROUTINE_HINT)
    while True:
        candidate = (url or str(typer.prompt("Webhook URL"))).strip()
        problem = webhook_url_problem(candidate)
        if not problem:
            break
        typer.secho(f"  {problem}", fg="red")
        url = None
    key = os.environ.get("RAKAZO_WEBHOOK_KEY") or typer.prompt("Webhook key (input hidden)", hide_input=True)
    return RakazoSettings(candidate, str(key).strip())


def _browser_login(url: str, *, number: bool, test: bool, no_browser: bool) -> OAuthCredential:
    def show(link: str) -> None:
        verb = "Open this link on any device and approve" if no_browser else "Opening your browser. If not"
        typer.echo(f"\n{verb}:\n  {link}")

    async def paste() -> str:
        return await asyncio.to_thread(typer.prompt, "Paste the code shown after you approve")

    host = socket.gethostname().split(".")[0]
    try:
        return asyncio.run(
            browser_login(
                url,
                client_name=f"Rakazo bridge on {host}",
                open_browser=not no_browser,
                want_number=number,
                env="test" if test else None,
                on_url=show,
                paste_code=paste if no_browser else None,
            )
        )
    except PromptliError as e:
        typer.secho(f"Login failed: {e.message} ({e.code})", fg="red", err=True)
        raise typer.Exit(1) from e


# The bot answers with the plugin's MCP tools, so the gateway never auto-replies, and live calls go to
# Promptli's hosted assistant (no supervisor turns: a webhook cannot answer in time).
SERVICE_EXTRA = {"supervise": False, "post_call_turn": False}


def _finish_config() -> None:
    cfg = _require_config()
    cfg.auto_reply = False
    cfg.extra.update(SERVICE_EXTRA)
    cfg.save()


@app.command()
def setup(
    webhook_url: str | None = typer.Option(None, help="The bot's Rakazo webhook URL"),
    number: bool = typer.Option(False, "--number", help="Also get the agent a phone number"),
    base_url: str | None = typer.Option(None, help="Promptli API base URL"),
    start: bool = typer.Option(True, help="Start the background service when done"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept the security warning"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Print the login link (SSH, headless)"),
    test: bool = typer.Option(False, "--test", help="Test mode: simulated, nothing reaches real people"),
) -> None:
    """Log in to Promptli Comms with your browser, connect the bot's webhook, start the service."""
    typer.secho("Promptli Comms for Rakazo", bold=True)
    typer.secho("\nBefore you continue:", fg="yellow", bold=True)
    typer.echo(STRANGER_WARNING)
    if not yes and not typer.confirm("\nI understand. Continue?", default=False):
        raise typer.Exit(1)
    key = os.environ.get("PROMPTLI_API_KEY")
    if key:
        res = asyncio.run(bootstrap(runtime=RUNTIME, api_key=key, base_url=_base_url(base_url)))
    else:
        cred = _browser_login(_base_url(base_url), number=number, test=test, no_browser=no_browser)
        res = asyncio.run(bootstrap(runtime=RUNTIME, credential=cred))
    _show_bootstrap(res)
    _finish_config()
    path = _ask_webhook(webhook_url).save()
    typer.echo(f"Saved the Rakazo webhook to {path} (mode 600).")
    typer.echo(
        "In Rakazo: Integrations → + → From GitHub → promptli-dev/promptli-rakazo-plugin, Connect, "
        "Sign in, then Use in this bot. Allow reply_email and send_text for this bot in Settings → "
        "Approvals, or each reply waits for your approval."
    )
    if start:
        _start()


@app.command()
def logout(yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Revoke this machine's Promptli login, forget the webhook key and stop the service."""
    cfg = GatewayConfig.load(RUNTIME)
    if cfg is None:
        typer.echo("Not set up.")
        return
    if not yes and not typer.confirm(f"Log out @{cfg.identity} on this machine?", default=True):
        raise typer.Exit(1)
    uninstall_autostart(RUNTIME)
    src = None
    with contextlib.suppress(Exception):
        src = cfg.auth()
    if isinstance(src, OAuthToken):
        asyncio.run(src.revoke())
    cfg.credentials_path.unlink(missing_ok=True)
    settings_path().unlink(missing_ok=True)
    typer.echo("Logged out. Rotate the bot's webhook key in Rakazo if it may have leaked.")


def _require_config() -> GatewayConfig:
    cfg = GatewayConfig.load(RUNTIME)
    if cfg is None:
        typer.secho("Not set up yet: run `promptli-rakazo setup`.", fg="red", err=True)
        raise typer.Exit(2)
    return cfg


def _require_settings() -> RakazoSettings:
    s = RakazoSettings.load()
    if s is None:
        typer.secho("No Rakazo webhook: run `promptli-rakazo setup`.", fg="red", err=True)
        raise typer.Exit(2)
    return s


@app.command()
def run(verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    """Run the bridge in the foreground (what the background service runs)."""
    cfg = _require_config()
    s = _require_settings()
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    adapter = RakazoWebhookAdapter(s.webhook_url, s.webhook_key)

    async def main_loop() -> None:
        gw = RakazoGateway(cfg, adapter)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, gw.stop)
        await gw.run()

    asyncio.run(main_loop())


def _start() -> None:
    cfg = _require_config()
    _require_settings()
    keep = ("PROMPTLI_HOME", "HOME", "PROMPTLI_BASE_URL")
    env = {k: v for k, v in os.environ.items() if k in keep}
    path = install_autostart(RUNTIME, [sys.executable, "-m", "promptli_rakazo", "run"], env=env)
    typer.secho(f"Background service started for @{cfg.identity} ({path}).", fg="green")


@app.command()
def start() -> None:
    """Install and start the background service (launchd on macOS, systemd --user on Linux)."""
    _start()


@app.command()
def stop() -> None:
    """Stop the background service."""
    typer.echo("Stopped." if uninstall_autostart(RUNTIME) else "Service was not installed.")


@app.command()
def restart() -> None:
    """Restart the background service."""
    uninstall_autostart(RUNTIME)
    _start()


@app.command()
def status(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show the agent, its channels, the Rakazo webhook and the service state. Never prints keys."""
    cfg = GatewayConfig.load(RUNTIME)
    s = RakazoSettings.load()
    running, detail = autostart_status(RUNTIME)
    data: dict[str, Any] = {
        "version": __version__,
        "configured": cfg is not None,
        "rakazo": s.redacted() if s else None,
        "service": {"running": running, "detail": detail},
    }
    if cfg is not None:
        data.update(identity=cfg.identity, base_url=cfg.base_url)
        with contextlib.suppress(Exception):

            async def channels() -> dict[str, Any]:
                async with AsyncPromptli(cfg.auth(), base_url=cfg.base_url) as p:
                    st = await p.identity(cfg.identity).channel_status()
                    return {"email": st.email.address, "sms": st.sms.number}

            data["channels"] = asyncio.run(channels())
    if as_json:
        _print_json(data)
        return
    if cfg is None:
        typer.echo("Not set up: run `promptli-rakazo setup`.")
        return
    ch = data.get("channels", {})
    typer.echo(f"Agent      @{cfg.identity}")
    typer.echo(f"Email      {ch.get('email') or '-'}")
    typer.echo(f"Phone      {ch.get('sms') or '-'}")
    typer.echo(f"Rakazo     {s.webhook_url if s else 'not connected'}")
    typer.echo(f"Service    {'running' if running else detail}")


async def rakazo_check() -> tuple[bool, str]:
    s = RakazoSettings.load()
    if s is None:
        return False, "no Rakazo webhook URL and key (run setup)"
    problem = webhook_url_problem(s.webhook_url)
    if problem:
        return False, problem
    parts = urlsplit(s.webhook_url)
    health = f"{parts.scheme}://{parts.netloc}/health"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(health)
    except httpx.HTTPError as e:
        return False, f"Rakazo unreachable at {parts.netloc}: {type(e).__name__}"
    if r.status_code >= 500:
        return False, f"Rakazo at {parts.netloc} answered HTTP {r.status_code}"
    return True, f"Rakazo reachable at {parts.netloc}"


@app.command()
def doctor(as_json: bool = typer.Option(False, "--json")) -> None:
    """Check the Promptli login, channels, Agent Link, clock, service and the Rakazo webhook."""
    rep = asyncio.run(run_doctor(RUNTIME, runtime_check=rakazo_check))
    if as_json:
        _print_json(rep.as_dict())
    else:
        for c in rep.checks:
            mark = typer.style("ok  ", fg="green") if c.ok else typer.style("FAIL", fg="red")
            typer.echo(f"{mark} {c.name:<14} {c.detail}")
    if not rep.ok:
        raise typer.Exit(1)


@app.command("test-delivery")
def test_delivery(yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Send one `promptli.test` delivery to the bot (it reaches only your bot, never a real person)."""
    s = _require_settings()
    if not yes and not typer.confirm("Send a test delivery to the bot's chat?", default=True):
        raise typer.Exit(1)
    adapter = RakazoWebhookAdapter(s.webhook_url, s.webhook_key, retry_delays=())
    payload = {
        "event": "promptli.test",
        "source": "promptli-comms",
        "content": "Test delivery from promptli-rakazo. Do not contact anyone; answer in this chat only.",
        "auto_reply": False,
    }

    async def go() -> dict[str, Any]:
        try:
            return await adapter.deliver(payload, f"promptli-test-{os.urandom(6).hex()}")
        finally:
            await adapter.close()

    try:
        body = asyncio.run(go())
    except Exception as e:
        typer.secho(f"Delivery failed: {e}", fg="red", err=True)
        raise typer.Exit(1) from e
    typer.secho(f"Delivered (run {body.get('runId') or '-'}). Check the bot's chat in Rakazo.", fg="green")


@app.command()
def logs(follow: bool = typer.Option(False, "-f", "--follow"), lines: int = typer.Option(50, "-n")) -> None:
    """Show the background service's logs."""
    d = home_dir(RUNTIME) / "logs"
    files = [p for p in (d / "gateway.err.log", d / "gateway.log") if p.exists()]
    if not files:
        typer.echo(f"No logs yet in {d}.")
        return
    if follow:
        os.execvp("tail", ["tail", "-n", str(lines), "-F", *map(str, files)])  # noqa: S606, S607
    for f in files:
        for line in f.read_text(errors="replace").splitlines()[-lines:]:
            typer.echo(line)


@app.command()
def uninstall(
    purge: bool = typer.Option(False, help="Also delete local config, credentials and state"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Stop the service; with --purge also remove ~/.promptli/promptli-rakazo (the agent is kept)."""
    uninstall_autostart(RUNTIME)
    if purge:
        d = home_dir(RUNTIME)
        if yes or typer.confirm(f"Delete {d}?", default=False):
            shutil.rmtree(d, ignore_errors=True)
    typer.echo("Uninstalled. The agent and its history stay in your Promptli account.")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"promptli-rakazo {__version__}")


def main() -> None:
    app()
