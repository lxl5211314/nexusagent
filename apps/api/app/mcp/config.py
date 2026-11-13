"""Loaded MCP server definitions.

The file format mirrors the client config most MCP hosts use, so a server entry
can be copied from `opencode.json` (or Claude Desktop) without translation:

    {
      "servers": {
        "github": {"type": "remote", "url": "https://...", "headers": {...}},
        "files":  {"type": "local",  "command": ["npx", "-y", "some-server"]}
      }
    }

`${VAR}` placeholders are expanded from the environment so tokens stay out of
the repository.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    name: str
    type: str
    command: list[str] = field(default_factory=list)
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(value: str) -> str:
    return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _expand_required(value: str) -> str | None:
    """Return None when a placeholder has no value, so the entry is dropped."""
    missing = False

    def _sub(match: re.Match[str]) -> str:
        nonlocal missing
        resolved = os.environ.get(match.group(1), "")
        if not resolved:
            missing = True
        return resolved

    expanded = _ENV_PATTERN.sub(_sub, value)
    return None if missing else expanded


def _expand_map(raw: dict[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, val in (raw or {}).items():
        expanded = _expand_required(str(val))
        if expanded:
            result[key] = expanded
    return result


def parse_servers(payload: dict, default_timeout: float = 30.0) -> list[ServerConfig]:
    block = payload.get("servers", payload.get("mcp", {}).get("servers", payload))
    servers: list[ServerConfig] = []
    for name, raw in dict(block or {}).items():
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or ("remote" if raw.get("url") else "local"))
        timeout = float(raw.get("timeout", default_timeout) or default_timeout)
        servers.append(
            ServerConfig(
                name=str(name),
                type=kind,
                command=[str(part) for part in raw.get("command", [])],
                url=_expand(str(raw.get("url", ""))),
                env=_expand_map(raw.get("environment") or raw.get("env")),
                headers=_expand_map(raw.get("headers")),
                timeout=timeout,
            )
        )
    return servers


def load_servers(path: str = "", default_timeout: float = 30.0) -> list[ServerConfig]:
    if not path:
        return []
    file = Path(path).expanduser()
    if not file.is_file():
        return []
    payload = json.loads(file.read_text(encoding="utf-8-sig"))
    return parse_servers(payload, default_timeout=default_timeout)
