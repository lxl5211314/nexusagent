from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import DocumentOut, KnowledgeBaseIn, KnowledgeBaseOut
from app.db.base import get_db
from app.db.models import Chunk, Document, KnowledgeBase
from app.rag.index import invalidate
from app.rag.ingest import DuplicateDocument, ingest_document

router = APIRouter(prefix="/kb", tags=["knowledge"])


@router.post("", response_model=KnowledgeBaseOut, status_code=201)
def create_kb(payload: KnowledgeBaseIn, db: Session = Depends(get_db)) -> KnowledgeBase:
    kb = KnowledgeBase(name=payload.name, description=payload.description)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return _to_out(kb, 0, 0)


@router.get("", response_model=list[KnowledgeBaseOut])
def list_kb(db: Session = Depends(get_db)) -> list[KnowledgeBase]:
    rows = db.execute(
        select(
            KnowledgeBase,
            func.count(Document.id).label("docs"),
            func.count(Chunk.id).label("chunks"),
        )
        .outerjoin(Document, Document.kb_id == KnowledgeBase.id)
        .outerjoin(Chunk, Chunk.kb_id == KnowledgeBase.id)
        .group_by(KnowledgeBase.id)
    ).all()
    return [_to_out(kb, docs or 0, chunks or 0) for kb, docs, chunks in rows]


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
def get_kb(kb_id: str, db: Session = Depends(get_db)) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(404, "knowledge base not found")
    docs = db.execute(select(func.count(Document.id)).where(Document.kb_id == kb_id)).scalar_one()
    chunks = db.execute(select(func.count(Chunk.id)).where(Chunk.kb_id == kb_id)).scalar_one()
    return _to_out(kb, docs, chunks)


@router.delete("/{kb_id}", status_code=204)
def delete_kb(kb_id: str, db: Session = Depends(get_db)) -> None:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(404, "knowledge base not found")
    db.delete(kb)
    db.commit()
    invalidate(kb_id)


@router.post("/{kb_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    kb_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
) -> Document:
    if db.get(KnowledgeBase, kb_id) is None:
        raise HTTPException(404, "knowledge base not found")
    data = await file.read()
    try:
        document = ingest_document(db, kb_id, file.filename or "unnamed", data, file.content_type or "")
    except DuplicateDocument as exc:
        existing = db.get(Document, exc.document_id)
        return _doc_out(existing)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    db.refresh(document)
    return _doc_out(document)


@router.get("/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(kb_id: str, db: Session = Depends(get_db)) -> list[Document]:
    rows = (
        db.execute(select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at))
        .scalars()
        .all()
    )
    return [_doc_out(doc) for doc in rows]


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
def delete_document(kb_id: str, doc_id: str, db: Session = Depends(get_db)) -> None:
    doc = db.get(Document, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(404, "document not found")
    db.delete(doc)
    db.commit()
    invalidate(kb_id)


def _to_out(kb: KnowledgeBase, docs: int, chunks: int) -> KnowledgeBaseOut:
    return KnowledgeBaseOut(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        document_count=docs,
        chunk_count=chunks,
        created_at=kb.created_at,
    )


def _doc_out(doc: Document) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        kb_id=doc.kb_id,
        filename=doc.filename,
        mime=doc.mime,
        size=doc.size,
        status=doc.status,
        error=doc.error,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
    )
