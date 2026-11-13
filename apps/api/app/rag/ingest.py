"""Ingestion: parse → chunk → embed → persist → invalidate index."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Chunk, Document, KnowledgeBase
from app.rag.chunking import chunk_text
from app.rag.embedding import get_embedder
from app.rag.index import invalidate
from app.rag.parsing import parse
from app.rag.stores import VectorRecord, get_graph_store, get_vector_store
from app.storage import document_key, get_object_store


class DuplicateDocument(Exception):
    def __init__(self, document_id: str) -> None:
        super().__init__(document_id)
        self.document_id = document_id


def ingest_document(session: Session, kb_id: str, filename: str, data: bytes, mime: str = "") -> Document:
    settings = get_settings()
    if session.get(KnowledgeBase, kb_id) is None:
        raise LookupError(f"knowledge base not found: {kb_id}")

    checksum = hashlib.sha256(data).hexdigest()
    existing = session.execute(
        select(Document).where(Document.kb_id == kb_id, Document.checksum == checksum)
    ).scalar_one_or_none()
    if existing is not None:
        # Idempotent upload: re-uploading the same bytes must not duplicate chunks.
        raise DuplicateDocument(existing.id)

    text = parse(filename, data)
    drafts = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
    if not drafts:
        raise ValueError("document produced no chunks (empty content?)")

    embedder = get_embedder()
    document = Document(
        kb_id=kb_id,
        filename=filename,
        mime=mime or "application/octet-stream",
        size=len(data),
        checksum=checksum,
        status="ready",
        chunk_count=len(drafts),
    )
    session.add(document)
    session.flush()

    # Keep the bytes the user uploaded: citations and the download button point
    # back at the original file, and a re-index must not need a second upload.
    key = document_key(kb_id, document.id, filename)
    get_object_store().put(key, data, mime or "application/octet-stream")
    document.object_key = key

    for draft in drafts:
        session.add(
            Chunk(
                kb_id=kb_id,
                document_id=document.id,
                ordinal=draft.ordinal,
                heading=draft.heading[:255],
                content=draft.content,
                tokens=draft.tokens,
                embedding=json.dumps(embedder.embed(draft.content)),
            )
        )

    session.flush()
    invalidate(kb_id)
    _sync_external(session, kb_id)
    return document


def _sync_external(session: Session, kb_id: str) -> None:
    """Push the knowledge base into Milvus/Neo4j when those backends are on.

    Local mode reads SQLite directly, so this is a no-op for the default setup.
    """
    settings = get_settings()
    if settings.vector_backend == "local" and settings.graph_backend == "local":
        return

    rows = session.execute(select(Chunk).where(Chunk.kb_id == kb_id)).scalars().all()
    if settings.vector_backend != "local":
        get_vector_store().upsert(
            [
                VectorRecord(
                    chunk_id=row.id,
                    kb_id=row.kb_id,
                    doc_id=row.document_id,
                    content=row.content,
                    heading=row.heading,
                    vector=json.loads(row.embedding),
                )
                for row in rows
            ]
        )
    if settings.graph_backend != "local":
        get_graph_store().rebuild(kb_id, [(row.id, row.content) for row in rows])
