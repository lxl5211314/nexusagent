"""In-memory BM25 (Okapi) over chunk token lists."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from app.rag.text import tokenize


class BM25:
    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = (sum(self.doc_len) / len(corpus)) if corpus else 0.0
        self.tf: list[Counter[str]] = [Counter(doc) for doc in corpus]
        df: dict[str, int] = defaultdict(int)
        for counter in self.tf:
            for term in counter:
                df[term] += 1
        total = max(1, len(corpus))
        self.idf = {term: math.log(1 + (total - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def score(self, query: str) -> list[float]:
        terms = tokenize(query)
        scores = [0.0] * len(self.corpus)
        if not terms:
            return scores
        for idx, counter in enumerate(self.tf):
            dl = self.doc_len[idx] or 1
            total = 0.0
            for term in terms:
                freq = counter.get(term)
                if not freq:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = freq + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                total += idf * freq * (self.k1 + 1) / denom
            scores[idx] = total
        return scores

    @staticmethod
    def build(texts: list[str]) -> BM25:
        return BM25([tokenize(t) for t in texts])
