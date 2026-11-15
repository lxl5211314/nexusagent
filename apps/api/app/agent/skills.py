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


@dataclass(frozen=True)
class SkillSummary:
    """Name + description only: what the agent sees before it picks a skill."""

    name: str
    description: str
    path: str


def load_catalog(root: str) -> list[SkillSummary]:
    return [
        SkillSummary(name=card.name, description=card.description, path=card.path)
        for card in load_skills(root)
    ]


def select_skills(
    question: str, cards: list[SkillSummary], limit: int = 2, threshold: float = 0.12
) -> list[SkillSummary]:
    """Rank skill cards by how many of the question's terms they mention."""
    from app.rag.text import tokenize

    terms = set(tokenize(question))
    if not terms:
        return []
    scored: list[tuple[float, SkillSummary]] = []
    for card in cards:
        words = set(tokenize(card.name.replace("-", " "))) | set(tokenize(card.description))
        if not words:
            continue
        score = len(terms & words) / len(terms)
        if score >= threshold:
            scored.append((score, card))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [card for _score, card in scored[:limit]]


def load_body(card: SkillSummary) -> str:
    """Read the instructions only when a skill was actually selected."""
    text = Path(card.path).read_text(encoding="utf-8", errors="replace")
    _name, _description, body = parse_skill(text)
    return body
