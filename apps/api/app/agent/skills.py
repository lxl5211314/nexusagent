"""Skill cards: the prompt-side counterpart of MCP tools.

A skill is a directory with a `SKILL.md` whose front matter carries a name and a
description, plus a body of instructions. Keeping them on disk means a deployment
can add capability by dropping in a folder, and the agent can load a body only
when the task actually needs it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_KEY_VALUE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")


@dataclass(frozen=True)
class SkillCard:
    name: str
    description: str
    body: str
    path: str


def parse_skill(text: str, fallback_name: str = "") -> tuple[str, str, str]:
    match = _FRONT_MATTER.match(text)
    if not match:
        return fallback_name, "", text.strip()
    header, body = match.group(1), match.group(2)
    fields: dict[str, str] = {}
    for line in header.splitlines():
        found = _KEY_VALUE.match(line.strip())
        if found:
            fields[found.group(1)] = found.group(2).strip().strip("\"'")
    return fields.get("name", fallback_name), fields.get("description", ""), body.strip()


def load_skills(root: str) -> list[SkillCard]:
    if not root:
        return []
    directory = Path(root).expanduser()
    if not directory.is_dir():
        return []
    cards: list[SkillCard] = []
    for file in sorted(directory.glob("*/SKILL.md")):
        name, description, body = parse_skill(
            file.read_text(encoding="utf-8", errors="replace"), file.parent.name
        )
        cards.append(
            SkillCard(name=name or file.parent.name, description=description, body=body, path=str(file))
        )
    return cards


def find_skill(root: str, name: str) -> SkillCard | None:
    for card in load_skills(root):
        if card.name == name:
            return card
    return None
