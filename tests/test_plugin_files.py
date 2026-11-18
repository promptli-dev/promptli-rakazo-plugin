"""The Rakazo plugin files: valid for Rakazo's previewer, and they carry Promptli's rules."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.validate_plugin import frontmatter, validate

ROOT = Path(__file__).resolve().parent.parent


def test_repository_is_a_valid_rakazo_plugin() -> None:
    assert validate(ROOT) == []


def test_mcp_points_at_hosted_promptli_mcp_without_credentials() -> None:
    doc = json.loads((ROOT / "mcp.json").read_text())
    assert doc["mcpServers"] == {
        "promptli": {"type": "streamable-http", "url": "https://api.comms.promptli.com/mcp"}
    }


def test_skill_carries_calling_and_secret_rules() -> None:
    text = (ROOT / "skills/promptli-comms/SKILL.md").read_text()
    parsed = frontmatter(text)
    assert parsed is not None and parsed[0]["name"] == "promptli-comms"
    assert "AI assistant" in text and "9 AM and 9 PM IST" in text and "+91" in text
    assert "secrets" in text and "untrusted" in text
    for tool in ("reply_email", "send_text", "place_call", "schedule_call", "get_conversation"):
        assert f"`{tool}`" in text


def test_commands_never_ask_for_keys_in_chat() -> None:
    for p in (ROOT / "commands").glob("*.md"):
        text = p.read_text().lower()
        assert "never" in text and ("key" in text or "token" in text), p.name


@pytest.mark.parametrize(
    ("path", "content", "problem"),
    [
        (
            "mcp.json",
            '{"$schema":"https://agent-plugins.org/schemas/1.1.0/mcp.schema.json","mcpServers":'
            '{"p":{"type":"streamable-http","url":"https://x.example/mcp","headers":{"Authorization":"${KEY}"}}}}',
            "placeholders",
        ),
        (
            "mcp.json",
            '{"$schema":"https://agent-plugins.org/schemas/1.1.0/mcp.schema.json","mcpServers":'
            '{"p":{"command":"promptli","type":"stdio"}}}',
            "streamable-http",
        ),
        (
            "mcp.json",
            '{"$schema":"https://agent-plugins.org/schemas/1.0.0/mcp.schema.json","mcpServers":{}}',
            "must match",
        ),
        ("skills/promptli-comms/SKILL.md", "no frontmatter", "frontmatter"),
        ("skills/promptli-comms/run.sh", "echo hi", "text references"),
        ("hooks/hooks.json", "{}", "does not support"),
        ("commands/promptli-comms.md", "dup", "named like a skill"),
    ],
)
def test_validator_catches_what_rakazo_skips(tmp_path: Path, path: str, content: str, problem: str) -> None:
    root = tmp_path / "plugin"
    root.mkdir()
    for part in ("plugin.json", "mcp.json", "skills", "commands"):
        src = ROOT / part
        (shutil.copytree if src.is_dir() else shutil.copy)(src, root / part)
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(content)
    assert any(problem in e for e in validate(root)), validate(root)


def test_manifest_name_rules(tmp_path: Path) -> None:
    (tmp_path / "plugin.json").write_text(
        '{"$schema":"https://agent-plugins.org/schemas/1.1.0/plugin.schema.json","name":"Bad--Name"}'
    )
    assert any("invalid name" in e for e in validate(tmp_path))
