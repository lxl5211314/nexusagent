"""Storage ports: the vector-store and graph-store seams.

The default build ships ``local`` implementations (in-process). ``milvus`` / ``neo4j``
implement the same protocols so the retrieval pipeline does not branch on the
backend — only the factory does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class VectorRecord:
    chunk_id: str
    kb_id: str
    doc_id: str
    content: str
    heading: str
    vector: list[float]


@dataclass(slots=True)
class StoreStats:
    backend: str
    chunks: int = 0
    nodes: int = 0
    edges: int = 0


@runtime_checkable
class VectorStore(Protocol):
    backend: str

    def upsert(self, records: list[VectorRecord]) -> None: ...

    def delete_kb(self, kb_id: str) -> None: ...

    def delete_document(self, kb_id: str, doc_id: str) -> None: ...

    def search(self, kb_id: str, vector: list[float], limit: int) -> list[tuple[str, float]]: ...

    def health(self) -> dict: ...


@runtime_checkable
class GraphStore(Protocol):
    backend: str

    def rebuild(self, kb_id: str, chunks: list[tuple[str, str]]) -> None: ...

    def recall(self, kb_id: str, query: str, limit: int) -> list[tuple[str, float]]: ...

    def stats(self, kb_id: str) -> StoreStats: ...

    def snapshot(self, kb_id: str, limit: int = 200) -> dict: ...

    def delete_kb(self, kb_id: str) -> None: ...

    def health(self) -> dict: ...
