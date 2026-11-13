"""Process-local corpus index, rebuilt lazily per knowledge base.

Trade-off: corpora are small (hundreds of chunks), so the index lives in
memory and is rebuilt after ingestion. The ``VectorStore`` / ``GraphStore``
interfaces are what a Milvus/Neo4j deployment implements instead.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

from app.db.base import SessionLocal
from app.db.models import Chunk, Document
from app.rag.bm25 import BM25
from app.rag.graph_store import GraphStats, TermGraph
from app.rag.text import cosine, tokenize


@dataclass(slots=True)
class ChunkView:
    id: str
    doc_id: str
    doc_name: str
    ordinal: int
    heading: str
    content: str
    embedding: list[float]


@dataclass
class CorpusIndex:
    kb_id: str
    chunks: list[ChunkView] = field(default_factory=list)
    bm25: BM25 | None = None
    graph: TermGraph | None = None

    # -- channels -----------------------------------------------------------
    def dense(self, vector: list[float], limit: int) -> list[tuple[str, float]]:
        scored = [(chunk.id, cosine(vector, chunk.embedding)) for chunk in self.chunks]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def sparse(self, query: str, limit: int) -> list[tuple[str, float]]:
        if self.bm25 is None:
            return []
        scores = self.bm25.score(query)
        pairs = [(chunk.id, scores[idx]) for idx, chunk in enumerate(self.chunks)]
        pairs.sort(key=lambda item: item[1], reverse=True)
        return [pair for pair in pairs[:limit] if pair[1] > 0]

    def graph_recall(self, query: str, limit: int) -> list[tuple[str, float]]:
        if self.graph is None:
            return []
        return self.graph.recall(query, limit)

    def by_id(self, chunk_id: str) -> ChunkView | None:
        return next((c for c in self.chunks if c.id == chunk_id), None)

    def stats(self) -> GraphStats:
        return self.graph.stats() if self.graph else GraphStats(0, 0, len(self.chunks))


_cache: dict[str, CorpusIndex] = {}
_versions: dict[str, int] = {}
_lock = threading.Lock()


def invalidate(kb_id: str) -> None:
    with _lock:
        _versions[kb_id] = _versions.get(kb_id, 0) + 1
        _cache.pop(kb_id, None)


def _load(kb_id: str) -> CorpusIndex:
    index = CorpusIndex(kb_id=kb_id)
    with SessionLocal() as session:
        rows = (
            session.query(Chunk, Document.filename)
            .join(Document, Document.id == Chunk.document_id)
            .filter(Chunk.kb_id == kb_id)
            .order_by(Chunk.document_id, Chunk.ordinal)
            .all()
        )
        for chunk, filename in rows:
            try:
                embedding = json.loads(chunk.embedding or "[]")
            except json.JSONDecodeError:
                embedding = []
            index.chunks.append(
                ChunkView(
                    id=chunk.id,
                    doc_id=chunk.document_id,
                    doc_name=filename,
                    ordinal=chunk.ordinal,
                    heading=chunk.heading,
                    content=chunk.content,
                    embedding=embedding,
                )
            )
    if index.chunks:
        index.bm25 = BM25.build([chunk.content for chunk in index.chunks])
        index.graph = TermGraph.build([(chunk.id, chunk.content) for chunk in index.chunks])
    return index


def get_index(kb_id: str) -> CorpusIndex:
    version = _versions.get(kb_id, 0)
    key = f"{kb_id}@{version}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    index = _load(kb_id)
    with _lock:
        _cache[key] = index
        # Drop older versions of the same KB so memory does not grow unbounded.
        for old_key in [k for k in _cache if k.startswith(f"{kb_id}@") and k != key]:
            _cache.pop(old_key, None)
    return index


def query_terms(query: str) -> list[str]:
    return tokenize(query)[:32]
