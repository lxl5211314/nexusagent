"""Reranking.

``HeuristicReranker`` is deterministic and dependency-free: it scores query-term
coverage, heading match and lexical proximity to the query. The protocol lets a
cross-encoder (bge-reranker) drop in without touching callers.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.rag.text import tokenize


class Reranker(Protocol):
    def score(self, query: str, candidates: list[dict]) -> list[float]: ...


class HeuristicReranker:
    def __init__(self, heading_weight: float = 0.25, coverage_weight: float = 0.6) -> None:
        self.heading_weight = heading_weight
        self.coverage_weight = coverage_weight

    def score(self, query: str, candidates: list[dict]) -> list[float]:
        query_terms = set(tokenize(query))
        if not query_terms:
            return [0.0 for _ in candidates]

        scores: list[float] = []
        for candidate in candidates:
            content = candidate.get("content", "")
            heading = candidate.get("heading", "")
            content_terms = set(tokenize(content))
            coverage = len(query_terms & content_terms) / len(query_terms)
            heading_hit = len(query_terms & set(tokenize(heading))) / len(query_terms)

            position = _first_hit_position(content, query_terms)
            proximity = 1.0 if position is None else 1.0 / (1.0 + position / 120.0)
            length_penalty = min(1.0, 600 / max(1, len(content)))

            scores.append(
                self.coverage_weight * coverage
                + self.heading_weight * heading_hit
                + 0.1 * proximity
                + 0.05 * length_penalty
            )
        return scores


def _first_hit_position(content: str, terms: set[str]) -> int | None:
    lowered = content.lower()
    hits = [lowered.find(term) for term in terms if term and lowered.find(term) >= 0]
    if not hits:
        return None
    return min(hits)


def compress_context(content: str, query: str, max_chars: int = 700) -> str:
    """Keep only query-relevant sentences, in original order."""
    from app.rag.text import split_sentences

    sentences = split_sentences(content)
    if not sentences:
        return content
    terms = set(tokenize(query))
    scored = []
    for idx, sentence in enumerate(sentences):
        overlap = len(terms & set(tokenize(sentence)))
        scored.append((overlap, idx, sentence))

    kept: list[tuple[int, str]] = []
    budget = max_chars
    for overlap, idx, sentence in sorted(scored, key=lambda item: (-item[0], item[1])):
        if budget <= 0:
            break
        if overlap == 0 and kept:
            continue
        if len(sentence) > budget:
            sentence = sentence[:budget]
        kept.append((idx, sentence))
        budget -= len(sentence)

    if not kept:
        return content[:max_chars]
    return "".join(text for _, text in sorted(kept))


def _normalise_spaces(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text)
