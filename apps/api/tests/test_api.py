"""API integration: health, KB CRUD, upload, chat SSE, retrieval, eval."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Chunk
from app.rag.index import invalidate


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_kb_crud(client):
    resp = client.post("/api/kb", json={"name": "测试库", "description": "集成测试"})
    assert resp.status_code == 201
    kb_id = resp.json()["id"]
    assert kb_id

    resp = client.get("/api/kb")
    assert resp.status_code == 200
    assert any(kb["id"] == kb_id for kb in resp.json())

    resp = client.get(f"/api/kb/{kb_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "测试库"


def test_kb_not_found(client):
    resp = client.get("/api/kb/nonexistent")
    assert resp.status_code == 404


def test_upload_and_index(client):
    resp = client.post("/api/kb", json={"name": "上传库"})
    kb_id = resp.json()["id"]

    content = "# 测试文档\n\nRRF 融合公式的 k 取 60，使用倒数排名融合。\n\n向量召回在 Milvus 中做近邻搜索。"
    resp = client.post(
        f"/api/kb/{kb_id}/documents",
        files={"file": ("test.md", content.encode(), "text/markdown")},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] in ("ready", "failed")

    # Retrieval should now find the content
    resp = client.post(
        "/api/retrieval/search", json={"kb_id": kb_id, "query": "RRF 融合公式", "top_k": 3}
    )
    assert resp.status_code == 200
    assert resp.json()["items"]
    invalidate(kb_id)


def test_upload_duplicate(client):
    resp = client.post("/api/kb", json={"name": "去重库"})
    kb_id = resp.json()["id"]
    content = "# 相同\n\n去重测试内容。".encode()
    r1 = client.post(f"/api/kb/{kb_id}/documents", files={"file": ("dup.md", content, "text/markdown")})
    assert r1.status_code == 201
    r2 = client.post(f"/api/kb/{kb_id}/documents", files={"file": ("dup.md", content, "text/markdown")})
    assert r2.status_code == 201  # dedup returns existing, does not fail
    invalidate(kb_id)


def test_chat_sse(client):
    resp = client.post("/api/kb", json={"name": "对话库"})
    kb_id = resp.json()["id"]
    content = "# RRF\n\nRRF 融合公式的 k 取 60。\n\n这是第二句相关内容。"
    client.post(f"/api/kb/{kb_id}/documents", files={"file": ("r.md", content.encode(), "text/markdown")})

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"kb_id": kb_id, "question": "RRF 融合公式的 k 取多少？"},
    ) as resp:
        assert resp.status_code == 200
        body = resp.read().decode()
        assert "event: run.finished" in body
        # Tokens are sliced, so a citation marker can straddle two frames;
        # assert on the structured citations instead of the raw text.
        assert '"citations": [{' in body or "guard.violation" in body
    invalidate(kb_id)


def test_eval_runs(client):
    resp = client.post("/api/kb", json={"name": "评估库"})
    kb_id = resp.json()["id"]
    for name, content in [
        ("overview.md", "# 概述\n\nNexusAgent 解决企业文档分散、答案无来源问题。"),
        ("retrieval.md", "# 检索\n\nRRF 融合公式的 k 取 60。\n\n向量召回在 Milvus。\n\n图召回在 Neo4j。"),
    ]:
        client.post(f"/api/kb/{kb_id}/documents", files={"file": (name, content.encode(), "text/markdown")})

    resp = client.post("/api/eval/runs", json={"kb_id": kb_id, "dataset": "sample"})
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    assert metrics["cases"] > 0
    assert 0 <= metrics["recall@5"] <= 1
    invalidate(kb_id)


def test_eval_unknown_dataset(client):
    resp = client.post("/api/kb", json={"name": "评估库2"})
    kb_id = resp.json()["id"]
    resp = client.post("/api/eval/runs", json={"kb_id": kb_id, "dataset": "does_not_exist"})
    assert resp.status_code == 404
    invalidate(kb_id)


def test_chunk_detail_for_citation_drawer(client, session):
    kb_id = client.post("/api/kb", json={"name": "引用库"}).json()["id"]
    content = "# 标题\n\n这一段是正文，用来验证引用抽屉能拿到完整块。"
    client.post(
        f"/api/kb/{kb_id}/documents",
        files={"file": ("c.md", content.encode(), "text/markdown")},
    )
    chunk = session.execute(select(Chunk).where(Chunk.kb_id == kb_id)).scalars().first()
    assert chunk is not None

    ok = client.get(f"/api/kb/{kb_id}/chunks/{chunk.id}")
    assert ok.status_code == 200
    body = ok.json()
    assert "正文" in body["content"]
    assert body["doc_name"] == "c.md"

    hidden = client.get(f"/api/kb/{kb_id}/chunks/{chunk.id}", headers={"X-Tenant-Id": "other"})
    assert hidden.status_code == 404


def test_trace_header_is_echoed(client):
    given = client.get("/api/whoami", headers={"X-Trace-Id": "abc123"})
    assert given.headers["X-Trace-Id"] == "abc123"

    minted = client.get("/api/whoami")
    assert len(minted.headers["X-Trace-Id"]) == 16
