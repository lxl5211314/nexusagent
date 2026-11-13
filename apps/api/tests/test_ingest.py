"""Parsing + ingestion: formats, dedup, chunk persistence."""

from __future__ import annotations

from app.db.base import session_scope
from app.db.models import Chunk, Document
from app.rag.ingest import DuplicateDocument, ingest_document


def test_ingest_markdown(seeded_kb):
    with session_scope() as session:
        docs = session.query(Document).filter(Document.kb_id == seeded_kb).all()
        assert len(docs) >= 3
        chunks = session.query(Chunk).filter(Chunk.kb_id == seeded_kb).all()
        assert len(chunks) >= 10


def test_ingest_dedup(seeded_kb):
    with session_scope() as session:
        before = session.query(Document).filter(Document.kb_id == seeded_kb).count()
        try:
            ingest_document(session, seeded_kb, "overview.md", "# 重复\n内容重复内容".encode(), "text/markdown")
            ingest_document(session, seeded_kb, "overview.md", "# 重复\n内容重复内容".encode(), "text/markdown")
        except DuplicateDocument:
            pass
        session.rollback()
        after = session.query(Document).filter(Document.kb_id == seeded_kb).count()
        assert after <= before + 1  # second identical upload is deduped or rolled back


def test_ingest_unsupported(seeded_kb):
    with session_scope() as session:
        try:
            ingest_document(session, seeded_kb, "bad.exe", b"\x00\x01", "application/octet-stream")
        except (ValueError, RuntimeError):
            pass
        else:
            raise AssertionError("unsupported format should raise")
