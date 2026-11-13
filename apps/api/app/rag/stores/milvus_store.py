"""Milvus vector store adapter (pymilvus ``MilvusClient``).

One collection holds every knowledge base; ``kb_id`` is a scalar filter field so
multi-tenant search stays a single round trip. The embedding dimension is taken
from settings, so the same code serves hash (384) and BGE (1024) embeddings.
"""

from __future__ import annotations

import logging

from app.rag.stores.base import StoreStats, VectorRecord

log = logging.getLogger(__name__)


class MilvusVectorStore:
    backend = "milvus"

    def __init__(self, uri: str, collection: str, dim: int, token: str = "") -> None:
        # Imported lazily so the local mode never needs pymilvus installed.
        from pymilvus import MilvusClient

        self.collection = collection
        self.dim = dim
        self._client = MilvusClient(uri=uri, token=token or None)
        self._ensure_collection()

    # -- schema -------------------------------------------------------------
    def _ensure_collection(self) -> None:
        from pymilvus import DataType

        if self._client.has_collection(self.collection):
            # A collection reopened from disk starts released in Lite/standalone;
            # search fails with "call load() before search" until we load it.
            self._client.load_collection(self.collection)
            return

        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("kb_id", DataType.VARCHAR, max_length=64)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("heading", DataType.VARCHAR, max_length=512)
        schema.add_field("content", DataType.VARCHAR, max_length=16384)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dim)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        # Scalar index keeps the kb_id filter cheap once the collection grows.
        index_params.add_index(field_name="kb_id", index_type="INVERTED")

        self._client.create_collection(
            collection_name=self.collection, schema=schema, index_params=index_params
        )
        log.info("created milvus collection %s (dim=%s)", self.collection, self.dim)

    # -- writes -------------------------------------------------------------
    def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        rows = [
            {
                "chunk_id": record.chunk_id,
                "kb_id": record.kb_id,
                "doc_id": record.doc_id,
                "heading": record.heading[:512],
                "content": record.content[:16000],
                "vector": record.vector,
            }
            for record in records
        ]
        self._client.upsert(collection_name=self.collection, data=rows)

    def delete_kb(self, kb_id: str) -> None:
        self._client.delete(collection_name=self.collection, filter=f'kb_id == "{kb_id}"')

    def delete_document(self, kb_id: str, doc_id: str) -> None:
        self._client.delete(
            collection_name=self.collection, filter=f'kb_id == "{kb_id}" and doc_id == "{doc_id}"'
        )

    # -- reads --------------------------------------------------------------
    def stats(self, kb_id: str) -> StoreStats:
        rows = self._client.query(
            collection_name=self.collection,
            filter=f'kb_id == "{kb_id}"',
            output_fields=["count(*)"],
        )
        total = int(rows[0]["count(*)"]) if rows else 0
        return StoreStats(backend="milvus", chunks=total)

    def search(self, kb_id: str, vector: list[float], limit: int) -> list[tuple[str, float]]:
        hits = self._client.search(
            collection_name=self.collection,
            data=[vector],
            anns_field="vector",
            filter=f'kb_id == "{kb_id}"',
            limit=limit,
            output_fields=["chunk_id"],
            search_params={"metric_type": "COSINE", "params": {"ef": max(64, limit * 4)}},
        )
        results: list[tuple[str, float]] = []
        for hit in (hits[0] if hits else []):
            chunk_id = hit.get("chunk_id") or hit.get("id")
            if chunk_id:
                results.append((str(chunk_id), float(hit.get("distance", 0.0))))
        return results

    def health(self) -> dict:
        try:
            stats = self._client.get_collection_stats(self.collection)
            return {"backend": "milvus", "ok": True, "rows": int(stats.get("row_count", 0))}
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return {"backend": "milvus", "ok": False, "error": str(exc)}
