"""Retrieval + answer-quality metrics.

``recall_at_k`` / ``mrr_at_k`` run over retrieved chunks vs gold snippets.
``faithfulness`` is a lexical stand-in (cited chunk token coverage) — in v0.2 it
is replaced by an LLM judge, keeping the same function signature.
"""

from __future__ import annotations

from app.rag.text import split_sentences, tokenize


def _hit(retrieved_contents: list[str], gold: str) -> bool:
    gold_norm = gold.strip()
    if not gold_norm:
        return False
    return any(gold_norm in content for content in retrieved_contents)


def recall_at_k(retrieved: list[str], gold_snippets: list[str], k: int) -> float:
    top = retrieved[:k]
    hits = sum(1 for gold in gold_snippets if _hit(top, gold))
    return hits / max(1, len(gold_snippets))


def mrr_at_k(retrieved: list[str], gold_snippets: list[str], k: int) -> float:
    for rank, content in enumerate(retrieved[:k], start=1):
        if any(_hit([content], gold) for gold in gold_snippets):
            return 1.0 / rank
    return 0.0


def faithfulness(answer: str, cited_chunks: list[str]) -> float:
    """Fraction of answer sentences supported by the cited chunks."""
    if not cited_chunks:
        return 0.0
    union = set()
    for content in cited_chunks:
        union |= set(tokenize(content))
    if not union:
        return 0.0
    sentences = [s for s in split_sentences(answer) if len(s) >= 8]
    if not sentences:
        return 0.0
    supported = 0
    for sentence in sentences:
        terms = set(tokenize(sentence))
        if not terms:
            continue
        overlap = len(terms & union) / len(terms)
        if overlap >= 0.6 or any(sentence in content for content in cited_chunks):
            supported += 1
    return supported / len(sentences)
