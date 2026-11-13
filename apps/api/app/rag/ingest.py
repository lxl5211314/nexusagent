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
    return document
