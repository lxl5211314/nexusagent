from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import DocumentOut, KnowledgeBaseIn, KnowledgeBaseOut
from app.core.config import get_settings
from app.core.tenant import get_tenant
from app.db.base import get_db
from app.db.models import Chunk, Document, KnowledgeBase
from app.rag.index import invalidate
from app.rag.ingest import DuplicateDocument, ingest_document
from app.rag.stores import get_graph_store, get_vector_store
from app.services import audit

router = APIRouter(prefix="/kb", tags=["knowledge"])


def _get_owned(db: Session, kb_id: str, tenant_id: str) -> KnowledgeBase:
    """Fetch a KB the tenant actually owns.

    A cross-tenant id answers 404, never 403: existence itself is information.
    """
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.tenant_id != tenant_id:
        raise HTTPException(404, "knowledge base not found")
    return kb


@router.post("", response_model=KnowledgeBaseOut, status_code=201)
def create_kb(
    payload: KnowledgeBaseIn,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant),
) -> KnowledgeBase:
    kb = KnowledgeBase(name=payload.name, description=payload.description, tenant_id=tenant_id)
    db.add(kb)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        action="kb.create",
        resource_type="knowledge_base",
        resource_id=kb.id,
        detail={"name": kb.name},
    )
    db.commit()
    db.refresh(kb)
    return _to_out(kb, 0, 0)


@router.get("", response_model=list[KnowledgeBaseOut])
def list_kb(
    db: Session = Depends(get_db), tenant_id: str = Depends(get_tenant)
) -> list[KnowledgeBase]:
    rows = db.execute(
        select(
            KnowledgeBase,
            func.count(Document.id).label("docs"),
            func.count(Chunk.id).label("chunks"),
        )
        .outerjoin(Document, Document.kb_id == KnowledgeBase.id)
        .outerjoin(Chunk, Chunk.kb_id == KnowledgeBase.id)
        .where(KnowledgeBase.tenant_id == tenant_id)
        .group_by(KnowledgeBase.id)
    ).all()
    return [_to_out(kb, docs or 0, chunks or 0) for kb, docs, chunks in rows]


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
def get_kb(
    kb_id: str, db: Session = Depends(get_db), tenant_id: str = Depends(get_tenant)
) -> KnowledgeBase:
    kb = _get_owned(db, kb_id, tenant_id)
    docs = db.execute(select(func.count(Document.id)).where(Document.kb_id == kb_id)).scalar_one()
    chunks = db.execute(select(func.count(Chunk.id)).where(Chunk.kb_id == kb_id)).scalar_one()
    return _to_out(kb, docs, chunks)


@router.delete("/{kb_id}", status_code=204)
def delete_kb(
    kb_id: str, db: Session = Depends(get_db), tenant_id: str = Depends(get_tenant)
) -> None:
    kb = _get_owned(db, kb_id, tenant_id)
    audit.record(
        db,
        tenant_id=tenant_id,
        action="kb.delete",
        resource_type="knowledge_base",
        resource_id=kb_id,
        detail={"name": kb.name},
    )
    db.delete(kb)
    db.commit()
    invalidate(kb_id)
    # External stores keep their own copies; drop them too or deleted corpora
    # would keep coming back in hybrid retrieval.
    if get_settings().vector_backend != "local":
        get_vector_store().delete_kb(kb_id)
    if get_settings().graph_backend != "local":
        get_graph_store().delete_kb(kb_id)


@router.post("/{kb_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    kb_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant),
) -> Document:
    _get_owned(db, kb_id, tenant_id)
    data = await file.read()
    filename = file.filename or "unnamed"
    try:
        document = ingest_document(db, kb_id, filename, data, file.content_type or "")
    except DuplicateDocument as exc:
        existing = db.get(Document, exc.document_id)
        return _doc_out(existing)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.record(
        db,
        tenant_id=tenant_id,
        action="document.upload",
        resource_type="document",
        resource_id=document.id,
        detail={"kb_id": kb_id, "filename": filename, "size": len(data)},
    )
    db.commit()
    db.refresh(document)
    return _doc_out(document)


@router.get("/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(
    kb_id: str, db: Session = Depends(get_db), tenant_id: str = Depends(get_tenant)
) -> list[Document]:
    _get_owned(db, kb_id, tenant_id)
    rows = (
        db.execute(select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at))
        .scalars()
        .all()
    )
    return [_doc_out(doc) for doc in rows]


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
def delete_document(
    kb_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant),
) -> None:
    _get_owned(db, kb_id, tenant_id)
    doc = db.get(Document, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(404, "document not found")
    audit.record(
        db,
        tenant_id=tenant_id,
        action="document.delete",
        resource_type="document",
        resource_id=doc_id,
        detail={"kb_id": kb_id, "filename": doc.filename},
    )
    db.delete(doc)
    db.commit()
    invalidate(kb_id)
    if get_settings().vector_backend != "local":
        get_vector_store().delete_document(kb_id, doc_id)
    if get_settings().graph_backend != "local":
        # Graph nodes are per-chunk; a doc's terms must be recomputed from the
        # remaining chunks, so a full rebuild is the correct granularity.
        remaining = db.execute(select(Chunk).where(Chunk.kb_id == kb_id)).scalars().all()
        get_graph_store().rebuild(kb_id, [(row.id, row.content) for row in remaining])


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
