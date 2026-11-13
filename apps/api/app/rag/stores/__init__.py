"""Store factories: one cached instance per backend, selected by settings."""

from __future__ import annotations

from app.core.config import get_settings
from app.rag.embedding import get_embedder
from app.rag.stores.base import GraphStore, StoreStats, VectorRecord, VectorStore
from app.rag.stores.local import LocalGraphStore, LocalVectorStore

__all__ = [
    "GraphStore",
    "StoreStats",
    "VectorRecord",
    "VectorStore",
    "get_graph_store",
    "get_vector_store",
    "reset_stores",
]

_vector_store: VectorStore | None = None
_graph_store: GraphStore | None = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        settings = get_settings()
        if settings.vector_backend == "milvus":
            from app.rag.stores.milvus_store import MilvusVectorStore

            _vector_store = MilvusVectorStore(
                uri=settings.milvus_uri,
                collection=settings.milvus_collection,
                dim=get_embedder().dim,
                token=settings.milvus_token,
            )
        else:
            _vector_store = LocalVectorStore()
    return _vector_store


def get_graph_store() -> GraphStore:
    global _graph_store
    if _graph_store is None:
        settings = get_settings()
        if settings.graph_backend == "neo4j":
            from app.rag.stores.neo4j_store import Neo4jGraphStore

            _graph_store = Neo4jGraphStore(
                uri=settings.neo4j_uri,
                user=settings.neo4j_user,
                password=settings.neo4j_password,
                database=settings.neo4j_database,
            )
        else:
            _graph_store = LocalGraphStore()
    return _graph_store


def reset_stores() -> None:
    """Drop cached clients (tests, or after a settings change)."""
    global _vector_store, _graph_store
    if _graph_store is not None and hasattr(_graph_store, "close"):
        _graph_store.close()  # type: ignore[attr-defined]
    _vector_store = None
    _graph_store = None
