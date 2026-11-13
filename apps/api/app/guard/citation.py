"""Citation guard: every ``[n]`` in an answer must resolve to a retrieved chunk.

This is the hallucination-suppression mechanism from the resume. The guard does
not trust the LLM — it re-checks the final text against the actual context that
was fed in, and the graph reacts by retrying once (stricter prompt) and then by
refusing to answer rather than emitting an unsupported statement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agent.state import Citation
from app.rag.retriever import RetrievedChunk

_CITE_RE = re.compile(r"\[(\d{1,2})\]")


@dataclass(slots=True)
class CitationCheck:
    valid: bool
    citations: list[Citation] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


def parse_citations(answer: str) -> list[int]:
    """All distinct citation indexes, in order of first appearance."""
    seen: dict[int, None] = {}
    for match in _CITE_RE.finditer(answer):
        seen[int(match.group(1))] = None
    return list(seen)


def check_answer(answer: str, contexts: list[RetrievedChunk]) -> CitationCheck:
    indexes = parse_citations(answer)
    if not indexes:
        return CitationCheck(valid=False, violations=["answer contains no citation markers"])

    out_of_range = [idx for idx in indexes if not (1 <= idx <= len(contexts))]
    if out_of_range:
        return CitationCheck(
            valid=False,
            violations=[f"citation out of range: {out_of_range} (contexts: {len(contexts)})"],
        )

    citations: list[Citation] = []
    for idx in indexes:
        ctx = contexts[idx - 1]
        snippet = ctx.compressed.strip().lstrip("#").strip()
        citations.append(
            Citation(
                index=idx,
                chunk_id=ctx.chunk_id,
                doc_name=ctx.doc_name,
                heading=ctx.heading,
                snippet=snippet[:160],
            )
        )
    return CitationCheck(valid=True, citations=citations)
