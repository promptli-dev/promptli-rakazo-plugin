"""Offline check of the Rakazo plugin files against the rules Rakazo applies when it previews a plugin.

Mirrors `packages/adapters/src/integrations/plugin-connectors.ts` and `docs/build-a-plugin.md` in
elie222/rakazo: the Agent Plugins 1.1.0 manifest, `mcp.json` (remote servers only, no secrets), skills
with `name` and `description` frontmatter, commands, unsupported parts and size limits.

Usage: python scripts/validate_plugin.py [plugin_dir]   (exit 1 with one line per problem)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PLUGIN_SCHEMAS = {
    "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "https://agent-plugins.org/schemas/1.1.0/plugin.schema.json",
}
NAME = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
MANIFEST_KEYS = {
    "$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords",
}  # fmt: skip
AUTHOR_KEYS = {"name", "email", "url"}
SERVER_KEYS = {"type", "url", "headers"}
TRANSPORTS = {"streamable-http", "sse"}
UNSUPPORTED = {"rules", "hooks", "automations", ".lsp.json"}
COMMAND = re.compile(r"^commands/([^/_][^/]*)\.md$")
SKILL = re.compile(r"^skills/([^/]+)/SKILL\.md$")
SKILL_TEXT = re.compile(r"\.(md|mdx|txt|json|ya?ml|csv)$", re.I)
MAX_FILE_BYTES = 100_000
MAX_FILES = 200
MAX_BYTES = 4_000_000
# Built-in Rakazo skill names a plugin skill must not reuse (it would be skipped).
BUILTIN_SKILLS = {"interrogate"}


def frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    m = re.match(r"^﻿?---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not m:
        return None
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip().strip("\"'")
    return fields, text[m.end() :]


def check_manifest(root: Path, errors: list[str]) -> str | None:
    p = root / "plugin.json"
    if not p.exists():
        errors.append("plugin.json is missing")
        return None
    doc: dict[str, Any] = json.loads(p.read_text())
    schema = doc.get("$schema")
    if schema not in PLUGIN_SCHEMAS:
        errors.append(f"plugin.json: unsupported $schema {schema!r}")
    name = doc.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or not NAME.match(name):
        errors.append(f"plugin.json: invalid name {name!r}")
    for key in set(doc) - MANIFEST_KEYS:
        errors.append(f"plugin.json: unknown field {key!r}")
    author = doc.get("author")
    if author is not None and (not isinstance(author, dict) or set(author) - AUTHOR_KEYS):  # pyright: ignore[reportUnknownArgumentType]
        errors.append("plugin.json: author accepts only name, email and url")
    keywords = doc.get("keywords")
    if keywords is not None and not (
        isinstance(keywords, list) and all(isinstance(k, str) for k in keywords)  # pyright: ignore[reportUnknownVariableType]
    ):
        errors.append("plugin.json: keywords must be an array of strings")
    return schema if isinstance(schema, str) else None


def check_mcp(root: Path, schema: str | None, errors: list[str]) -> int:
    p = root / "mcp.json"
    if not p.exists():
        return 0
    raw = p.read_text()
    if "${" in raw:
        errors.append("mcp.json: ${VAR} placeholders are rejected by Rakazo")
    doc: dict[str, Any] = json.loads(raw)
    if schema and doc.get("$schema") != schema.replace("plugin.schema.json", "mcp.schema.json"):
        errors.append("mcp.json: $schema version must match plugin.json")
    if set(doc) - {"$schema", "mcpServers"}:
        errors.append("mcp.json: only $schema and mcpServers are allowed")
    servers: dict[str, Any] = doc.get("mcpServers") or {}
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            errors.append(f"mcp.json: {name}: server must be an object")
            continue
        fields: dict[str, Any] = entry  # pyright: ignore[reportUnknownVariableType]
        if set(fields) - SERVER_KEYS:
            errors.append(f"mcp.json: {name}: only type, url and headers are allowed")
        if fields.get("type") not in TRANSPORTS:
            errors.append(f"mcp.json: {name}: type must be streamable-http or sse")
        url = urlsplit(str(fields.get("url", "")))
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            errors.append(f"mcp.json: {name}: url must be public https without credentials")
        if url.hostname in ("localhost", "127.0.0.1") or url.query:
            errors.append(f"mcp.json: {name}: no localhost or query string")
        headers: dict[str, Any] = fields.get("headers") or {}
        for h in headers:
            if h.lower() in ("authorization", "cookie") or re.search(r"key|token|secret", h, re.I):
                errors.append(f"mcp.json: {name}: header {h!r} looks like a credential")
    return len(servers)


def check_components(root: Path, errors: list[str]) -> int:
    names: set[str] = set()
    count = 0
    fetched: list[Path] = []
    for p in sorted(root.iterdir()):
        if p.name in UNSUPPORTED:
            errors.append(f"{p.name}: Rakazo does not support this part (rules, hooks, automations, LSP)")
    for part in ("skills", "commands", "agents"):
        for p in sorted((root / part).rglob("*")) if (root / part).exists() else []:
            if p.is_symlink():
                errors.append(f"{p.relative_to(root).as_posix()}: symlinks are not followed")
    for p in sorted((root / "skills").glob("*/*")) if (root / "skills").exists() else []:
        rel = p.relative_to(root).as_posix()
        fetched.append(p)
        if SKILL.match(rel):
            parsed = frontmatter(p.read_text())
            if parsed is None:
                errors.append(f"{rel}: must start with YAML frontmatter")
                continue
            fields, _ = parsed
            name, desc = fields.get("name", ""), fields.get("description", "")
            if not name or len(name) > 80:
                errors.append(f"{rel}: name is required, at most 80 characters")
            if not desc or len(desc) > 2000:
                errors.append(f"{rel}: description is required, at most 2000 characters")
            if name.lower() in names or name.lower() in BUILTIN_SKILLS:
                errors.append(f"{rel}: duplicate or built-in skill name {name!r}")
            names.add(name.lower())
            count += 1
        elif not SKILL_TEXT.search(rel):
            errors.append(f"{rel}: skill companions must be text references, not scripts or binaries")
    for d in sorted((root / "skills").iterdir()) if (root / "skills").exists() else []:
        if d.is_dir() and not (d / "SKILL.md").exists():
            errors.append(f"skills/{d.name}: missing SKILL.md")
    for p in sorted((root / "commands").glob("*.md")) if (root / "commands").exists() else []:
        rel = p.relative_to(root).as_posix()
        m = COMMAND.match(rel)
        if not m:
            continue
        fetched.append(p)
        if m.group(1).lower() in names:
            errors.append(f"{rel}: a command named like a skill is dropped")
        names.add(m.group(1).lower())
        count += 1
    fetched += [root / f for f in ("plugin.json", "mcp.json") if (root / f).exists()]
    sizes = [p.stat().st_size for p in fetched]
    if any(s > MAX_FILE_BYTES for s in sizes):
        errors.append("a plugin file is over 100,000 bytes")
    if len(fetched) > MAX_FILES or sum(sizes) > MAX_BYTES:
        errors.append("plugin is over Rakazo's file count or size limit")
    return count


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    schema = check_manifest(root, errors)
    servers = check_mcp(root, schema, errors)
    skills = check_components(root, errors)
    if not servers and not skills:
        errors.append("a plugin needs at least one MCP server or skill")
    if not (root / "plugin.json").exists() and any(root.glob(".*-plugin/plugin.json")):
        errors.append("a hidden .<tool>-plugin manifest without plugin.json previews as incomplete")
    return errors


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    errors = validate(root)
    for e in errors:
        print(f"FAIL {e}")
    if errors:
        sys.exit(1)
    print(f"ok   {root.name}: Rakazo plugin files are valid")


if __name__ == "__main__":
    main()
