"""Original uploads: stored, served back, and removed with the document."""

from __future__ import annotations

import io

import pytest

from app.rag import ocr
from app.storage import document_key, get_object_store, safe_name
from app.storage.local import LocalObjectStore


def test_safe_name_strips_paths_and_keeps_extension():
    assert safe_name("../../etc/passwd.txt") == "passwd.txt"
    assert safe_name("季度 报告.docx") == "季度_报告.docx"
    assert safe_name("") == "unnamed"


def test_document_key_keeps_id_hierarchy():
    key = document_key("kb1", "doc1", "a b.pdf")
    assert key.startswith("kb1/doc1/")
    assert key.endswith("a_b.pdf")


def test_local_store_refuses_to_escape(tmp_path):
    store = LocalObjectStore(tmp_path)
    with pytest.raises(ValueError):
        store.put("../outside.txt", b"x")


def test_local_store_roundtrip_and_prefix_delete(tmp_path):
    store = LocalObjectStore(tmp_path)
    store.put("kb/doc/1-a.txt", b"hello")
    store.put("kb/doc/2-b.txt", b"world")
    store.put("kb/other/1-c.txt", b"keep")
    assert store.get("kb/doc/1-a.txt") == b"hello"
    assert store.delete_prefix("kb/doc/") == 2
    assert store.delete_prefix("kb/doc/") == 0
    assert store.health() is True


def test_upload_keeps_the_original_and_download_serves_it(client, seeded_kb):
    payload = "原件留档测试。\n\n这段内容只用于验证下载接口。".encode()
    files = {"file": ("note.txt", io.BytesIO(payload), "text/plain")}
    created = client.post(f"/api/kb/{seeded_kb}/documents", files=files)
    assert created.status_code == 201
    doc_id = created.json()["id"]

    got = client.get(f"/api/kb/{seeded_kb}/documents/{doc_id}/content")
    assert got.status_code == 200
    assert got.content == payload
    assert "note.txt" in got.headers["content-disposition"]

    assert client.delete(f"/api/kb/{seeded_kb}/documents/{doc_id}").status_code == 204
    assert client.get(f"/api/kb/{seeded_kb}/documents/{doc_id}/content").status_code == 404

    store = get_object_store()
    with pytest.raises(FileNotFoundError):
        store.get(f"{seeded_kb}/{doc_id}/0-note.txt")


def test_paddle_backend_stitches_page_markdown(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "result": {
                    "layoutParsingResults": [
                        {"markdown": {"text": "# 第一页\n\n内容一"}},
                        {"markdown": {"text": "# 第二页\n\n内容二"}},
                    ]
                }
            }

    calls = {}

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["json"] = kwargs["json"]
        return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("NEXUS_OCR_BACKEND", "paddle")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        text = ocr.ocr_parse("scan.pdf", b"%PDF-1.4 fake")
        assert "第一页" in text and "第二页" in text
        assert calls["url"].endswith("/layout-parsing")
        assert "file" in calls["json"]
    finally:
        get_settings.cache_clear()
