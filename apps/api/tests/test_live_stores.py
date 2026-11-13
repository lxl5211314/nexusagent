"""Opt-in integration test against the real Milvus Lite / Neo4j servers.

Skipped by default: it needs `milvus-lite` and a running Bolt endpoint. Run with

    NEXUS_LIVE_STORES=1 NEXUS_NEO4J_URI=bolt://127.0.0.1:7687 pytest tests/test_live_stores.py
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.db.models import KnowledgeBase
from app.rag.ingest import ingest_document
from app.rag.retriever import retrieve
from app.rag.stores import get_graph_store, get_vector_store, reset_stores

LIVE = os.environ.get("NEXUS_LIVE_STORES") == "1"

pytestmark = pytest.mark.skipif(not LIVE, reason="set NEXUS_LIVE_STORES=1 to hit real stores")

DOC = (
    "# 检索链路\n"
    "混合召回由稠密向量、BM25 稀疏与图谱一跳三路组成，再用 RRF 融合，k 取 60。\n"
    "## 幻觉抑制\n"
    "答案必须绑定引用块，引用越界或缺失就判为无效并重写。\n"
)


@pytest.fixture()
def live_stores(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="nexus-live-"))
    settings = get_settings()
    monkeypatch.setattr(settings, "vector_backend", "milvus")
    monkeypatch.setattr(settings, "milvus_uri", str(tmp / "milvus.db"))
    monkeypatch.setattr(settings, "milvus_collection", "nexusagent_live_test")
    monkeypatch.setattr(settings, "graph_backend", "neo4j")
    monkeypatch.setattr(settings, "neo4j_password", os.environ.get("NEXUS_NEO4J_PASSWORD", "nexusagent"))
    reset_stores()
    yield settings
    reset_stores()


def test_roundtrip_and_cleanup(live_stores, session):
    kb_id = "live-kb"
    session.add(KnowledgeBase(id=kb_id, name="live", description="", tenant_id="local"))
    session.flush()
    ingest_document(session, kb_id, "live.md", DOC.encode("utf-8"))
    session.commit()  # the retriever reads through its own session; commit so it can see the chunks

    vector, graph = get_vector_store(), get_graph_store()
    assert vector.health()["ok"] is True
    assert vector.stats(kb_id).chunks == graph.stats(kb_id).chunks > 0
    assert graph.stats(kb_id).edges > 0

    result = retrieve(kb_id, "RRF 的 k 取多少", top_k=3)
    assert result.items, "hybrid retrieval returned nothing against real stores"
    assert any("rrf" in hit.scores for hit in result.items)

    vector.delete_kb(kb_id)
    graph.delete_kb(kb_id)
    assert vector.stats(kb_id).chunks == 0
    assert graph.stats(kb_id).chunks == 0
