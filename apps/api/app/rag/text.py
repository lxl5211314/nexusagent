"""Text utilities: tokenisation for CJK + latin, sentence splitting, cosine."""

from __future__ import annotations

import math
import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-z0-9_]+")
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;\n])")


def tokenize(text: str) -> list[str]:
    """Latin words + CJK character bigrams.

    Bigrams are the cheapest tokeniser that still gives useful BM25 signal for
    Chinese without pulling in jieba; the interface allows swapping later.
    """
    lowered = text.lower()
    tokens = _WORD_RE.findall(lowered)
    cjk = "".join(_CJK_RE.findall(lowered))
    tokens.extend(cjk[i : i + 2] for i in range(len(cjk) - 1))
    return tokens


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(text)]
    return [p for p in parts if p]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def char_count(text: str) -> int:
    return len(text)
