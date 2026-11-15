"""Local (in-process) stores: the default, zero-dependency implementation."""

from __future__ import annotations

from app.rag.index import get_index
from app.rag.kg import graph_snapshot
from app.rag.stores.base import StoreStats, VectorRecord


class LocalVectorStore:
    backend = "local"

    def upsert(self, records: list[VectorRecord]) -> None:
        # Local mode reads straight from SQLite, so there is nothing to push.
        return None

    def delete_kb(self, kb_id: str) -> None:
        return None

    def delete_document(self, kb_id: str, doc_id: str) -> None:
        return None

    def search(self, kb_id: str, vector: list[float], limit: int) -> list[tuple[str, float]]:
        return get_index(kb_id).dense(vector, limit)

    def health(self) -> dict:
        return {"backend": "local", "ok": True}


class LocalGraphStore:
    backend = "local"

    def rebuild(self, kb_id: str, chunks: list[tuple[str, str]]) -> None:
        # Rebuilt lazily off the corpus index; nothing to push.
        return None

    def recall(self, kb_id: str, query: str, limit: int) -> list[tuple[str, float]]:
        return get_index(kb_id).graph_recall(query, limit)

    def stats(self, kb_id: str) -> StoreStats:
        stats = get_index(kb_id).stats()
        return StoreStats(backend="local", chunks=stats.chunks, nodes=stats.nodes, edges=stats.edges)

    def snapshot(self, kb_id: str, limit: int = 200) -> dict:
        graph = get_index(kb_id).graph
        if graph is None:
            return {"nodes": [], "edges": [], "stats": {"nodes": 0, "edges": 0, "chunks": 0}}
        return graph_snapshot(graph, limit)

    def delete_kb(self, kb_id: str) -> None:
        return None

    def health(self) -> dict:
        return {"backend": "local", "ok": True}
