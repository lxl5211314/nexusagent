"""A tiny term co-occurrence graph.

The in-process graph stands in for the Neo4j entity graph: nodes are salient terms, edges are
co-occurrence within a chunk, and ``expand`` walks one hop from query terms to
chunks that never matched lexically. Same shape as the Neo4j adapter will be, so
the retrieval channel does not change when it is swapped in.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from app.rag.text import tokenize

_STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "are", "was", "not",
    "我们", "他们", "可以", "这个", "那个", "以及", "如果", "因为", "所以",
    "一个", "进行", "通过", "需要", "使用", "支持", "实现", "问题", "系统",
}


@dataclass(slots=True)
class GraphStats:
    nodes: int
    edges: int
    chunks: int


@dataclass
class TermGraph:
    term_chunks: dict[str, set[str]] = field(default_factory=dict)
    edges: dict[tuple[str, str], int] = field(default_factory=dict)
    chunk_count: int = 0

    @classmethod
    def build(cls, chunks: list[tuple[str, str]]) -> TermGraph:
        graph = TermGraph(chunk_count=len(chunks))
        df: Counter[str] = Counter()
        per_chunk: list[list[str]] = []

        for chunk_id, content in chunks:
            terms = [t for t in tokenize(content) if t not in _STOPWORDS and len(t) >= 2]
            unique = list(dict.fromkeys(terms))[:24]
            per_chunk.append(unique)
            for term in unique:
                graph.term_chunks.setdefault(term, set()).add(chunk_id)
                df[term] += 1

        max_df = max(1, int(len(chunks) * 0.4))
        for unique in per_chunk:
            salient = [t for t in unique if 1 < df[t] <= max_df][:12]
            for i, left in enumerate(salient):
                for right in salient[i + 1 :]:
                    key = (left, right) if left < right else (right, left)
                    graph.edges[key] = graph.edges.get(key, 0) + 1
        return graph

    def neighbours(self, term: str, limit: int = 8) -> list[str]:
        linked = [
            (other, weight)
            for (left, right), weight in self.edges.items()
            if term in (left, right)
            for other in ([right] if left == term else [left])
        ]
        linked.sort(key=lambda item: item[1], reverse=True)
        return [other for other, _ in linked[:limit]]

    def recall(self, query: str, limit: int = 20) -> list[tuple[str, float]]:
        terms = [t for t in tokenize(query) if t in self.term_chunks][:6]
        if not terms:
            return []

        scores: dict[str, float] = defaultdict(float)
        for term in terms:
            for chunk_id in self.term_chunks.get(term, set()):
                scores[chunk_id] += 1.0
            for weight in (0.5, 0.25):
                for neighbour in self.neighbours(term, limit=4):
                    for chunk_id in self.term_chunks.get(neighbour, set()):
                        scores[chunk_id] += weight
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return ranked[:limit]

    def stats(self) -> GraphStats:
        return GraphStats(nodes=len(self.term_chunks), edges=len(self.edges), chunks=self.chunk_count)
