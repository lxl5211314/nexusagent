"""Citation guard: structural checks plus a lexical support check.

Three layers, applied to the final text after generation:

1. every claim must carry a ``[n]`` marker;
2. every marker must resolve to a chunk that was actually retrieved;
3. every cited sentence must be lexically supported by the chunk it cites.

Layer 3 is the one that catches a model that cites ``[1]`` while stating
something chunk 1 never said — a valid-looking citation is not evidence. It is
lexical (token coverage) on purpose: it runs offline, deterministically, and it
is the cheap pre-filter before an LLM judge would be involved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agent.state import Citation
from app.rag.retriever import RetrievedChunk
from app.rag.text import split_sentences, tokenize

_CITE_RE = re.compile(r"\[(\d{1,2})\]")

# High-frequency function bigrams carry no claim content and inflate coverage.
_STOP = {
    "的是", "了的", "了一", "我们", "你们", "他们", "它们", "这个", "那个", "这些",
    "那些", "可以", "需要", "没有", "不是", "就是", "以及", "并且", "因为", "所以",
    "如果", "但是", "而且", "或者", "一个", "进行", "通过", "对于", "关于", "之后",
    "之前", "其中", "什么", "怎么", "如何", "多少", "哪些", "是否", "这种", "那种",
}

# Uncited sentences shorter than this are treated as scaffolding ("根据片段："),
# longer ones are a claim without evidence.
_UNCITED_MAX = 20


@dataclass(slots=True)
class CitationCheck:
    valid: bool
    citations: list[Citation] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    support: float = 1.0


def parse_citations(answer: str) -> list[int]:
    """All distinct citation indexes, in order of first appearance."""
    seen: dict[int, None] = {}
    for match in _CITE_RE.finditer(answer):
        seen[int(match.group(1))] = None
    return list(seen)


def _terms(text: str) -> set[str]:
    return {token for token in tokenize(text) if token not in _STOP}


def _coverage(terms: set[str], pool: set[str]) -> float:
    if not terms:
        return 1.0
    return len(terms & pool) / len(terms)


def sentence_support(sentence: str, cited_texts: list[str]) -> float:
    """Share of the sentence's content tokens that appear in the cited text."""
    terms = _terms(_CITE_RE.sub(" ", sentence))
    pool: set[str] = set()
    for text in cited_texts:
        pool |= _terms(text)
    return _coverage(terms, pool)


def check_answer(
    answer: str,
    contexts: list[RetrievedChunk],
    min_support: float = 0.5,
) -> CitationCheck:
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
                snippet=snippet[:400],
            )
        )

    violations: list[str] = []
    scores: list[float] = []
    for sentence in split_sentences(answer):
        if len(sentence.strip()) < 8:
            continue
        cited = [int(m) for m in _CITE_RE.findall(sentence)]
        if not cited:
            if len(sentence.strip()) > _UNCITED_MAX:
                violations.append(f"uncited claim: {sentence.strip()[:40]}")
            continue
        support = sentence_support(sentence, [contexts[i - 1].content for i in cited])
        scores.append(support)
        if support < min_support:
            violations.append(
                f"unsupported claim: {sentence.strip()[:40]} (support {support:.2f} < {min_support:.2f})"
            )

    mean_support = sum(scores) / len(scores) if scores else 1.0
    return CitationCheck(
        valid=not violations, citations=citations, violations=violations, support=round(mean_support, 3)
    )
