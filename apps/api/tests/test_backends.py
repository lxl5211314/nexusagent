"""Storage-backend seam: local adapters, OCR fallback, health surface."""

from __future__ import annotations

import pytest

from app.rag import ocr
from app.rag.embedding import get_embedder
from app.rag.index import get_index
from app.rag.parsing import ParseError, UnsupportedFormat, parse
from app.rag.stores import (
    LocalGraphStore,
    LocalVectorStore,
    StoreStats,
    VectorRecord,
    get_graph_store,
    get_vector_store,
    reset_stores,
)


def test_local_vector_store_matches_index(seeded_kb):
    index = get_index(seeded_kb)
    vector = get_embedder().embed("RRF 融合 检索")
    store = LocalVectorStore()

    assert store.search(seeded_kb, vector, 5) == index.dense(vector, 5)


def test_local_graph_store_reports_stats(seeded_kb):
    store = LocalGraphStore()
    stats = store.stats(seeded_kb)

    assert isinstance(stats, StoreStats)
    assert stats.backend == "local"
    assert stats.chunks > 0
    assert stats.edges > 0


def test_factories_default_to_local():
    reset_stores()
    assert get_vector_store().backend == "local"
    assert get_graph_store().backend == "local"
    assert get_vector_store() is get_vector_store()


def test_local_writes_are_noops(seeded_kb):
    store = LocalVectorStore()
    store.upsert([VectorRecord("c1", seeded_kb, "d1", "content", "h", [0.1, 0.2])])
    store.delete_document(seeded_kb, "d1")
    store.delete_kb(seeded_kb)

    assert get_index(seeded_kb).stats().chunks > 0


def test_scanned_image_without_ocr_explains_the_switch(monkeypatch):
    monkeypatch.setattr(ocr, "ocr_enabled", lambda: False)

    with pytest.raises(ParseError, match="NEXUS_OCR_BACKEND=mineru"):
        parse("scan.png", b"\x89PNG\r\n")


def test_scanned_image_uses_ocr(monkeypatch):
    monkeypatch.setattr(ocr, "ocr_enabled", lambda: True)
    monkeypatch.setattr(ocr, "ocr_parse", lambda name, data: "# 发票\n金额 100 元")

    assert parse("scan.png", b"\x89PNG\r\n").startswith("# 发票")


def test_mineru_payload_is_searched_for_markdown():
    payload = {
        "code": 0,
        "results": {"scan.pdf": {"md_content": "# 标题\n\n正文内容足够长以通过长度过滤。", "images": []}},
    }

    assert ocr._find_markdown(payload).startswith("# 标题")
    assert ocr._find_markdown({"code": 0}) == ""


def test_backends_route_reports_local(client):
    response = client.get("/api/system/backends")

    assert response.status_code == 200
    body = response.json()
    assert body["vector"]["backend"] == "local"
    assert body["graph"]["ok"] is True
    assert body["embedding"]["dim"] > 0


def test_unsupported_format_names_the_alternatives():
    with pytest.raises(UnsupportedFormat, match="png"):
        parse("archive.zip", b"PK")


def test_factory_wires_milvus_from_settings(monkeypatch):
    from app.core.config import get_settings
    from app.rag.stores import milvus_store

    settings = get_settings()
    monkeypatch.setattr(settings, "vector_backend", "milvus")
    captured: dict = {}

    class FakeMilvus:
        backend = "milvus"

        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(milvus_store, "MilvusVectorStore", FakeMilvus)
    reset_stores()

    store = get_vector_store()

    assert store.backend == "milvus"
    assert captured["uri"] == settings.milvus_uri
    assert captured["collection"] == settings.milvus_collection
    assert captured["dim"] == get_embedder().dim
    reset_stores()


def test_factory_wires_neo4j_from_settings(monkeypatch):
    from app.core.config import get_settings
    from app.rag.stores import neo4j_store

    settings = get_settings()
    monkeypatch.setattr(settings, "graph_backend", "neo4j")
    captured: dict = {}

    class FakeNeo4j:
        backend = "neo4j"

        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(neo4j_store, "Neo4jGraphStore", FakeNeo4j)
    reset_stores()

    store = get_graph_store()

    assert store.backend == "neo4j"
    assert captured["uri"] == settings.neo4j_uri
    assert captured["database"] == settings.neo4j_database
    reset_stores()
