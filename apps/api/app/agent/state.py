"""Agent state and the small value objects shared by nodes."""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from app.rag.retriever import RetrievedChunk


@dataclass(slots=True)
class SubTask:
    id: int
    question: str
    tool: str = "kb.search"


@dataclass(slots=True)
class Finding:
    task_id: int
    question: str
    summary: str
    chunk_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Citation:
    index: int
    chunk_id: str
    doc_name: str
    heading: str
    snippet: str


def merge_unique(left: list, right: list) -> list:
    """Reducer used by parallel branches: keep first occurrence, preserve order."""
    seen = {_key(item) for item in left}
    merged = list(left)
    for item in right:
        key = _key(item)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def _key(item) -> str:
    if isinstance(item, RetrievedChunk):
        return item.chunk_id
    if isinstance(item, Finding):
        return f"finding:{item.task_id}"
    if isinstance(item, dict):
        return str(item.get("id") or item.get("chunk_id") or item)
    return str(item)


class AgentState(TypedDict, total=False):
    question: str
    kb_id: str
    run_id: str
    plan: list[SubTask]
    findings: Annotated[list[Finding], operator.add]
    contexts: Annotated[list[RetrievedChunk], merge_unique]
    answer: str
    answer_valid: bool
    citations: list[Citation]
    confidence: float
    refused: bool
    kind: str
    attempts: int
    violations: list[str]
