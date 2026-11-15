from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.api.schemas import DocumentOut, KnowledgeBaseIn, KnowledgeBaseOut
from app.core import org
from app.core.config import get_settings
from app.core.rbac import Identity, require_permission
from app.db.base import get_db
from app.db.models import Chunk, Document, KnowledgeBase
from app.rag.index import invalidate
from app.rag.ingest import DuplicateDocument, ingest_document
from app.rag.stores import get_graph_store, get_vector_store
from app.services import audit
from app.storage import get_object_store

router = APIRouter(prefix="/kb", tags=["knowledge"])


def _get_owned(db: Session, kb_id: str, identity: Identity) -> KnowledgeBase:
    """Fetch a KB this caller may see.

    A cross-tenant id answers 404, never 403: existence itself is information.
    The same holds for a KB that exists but sits outside the caller's
    department subtree — saying "forbidden" would leak the id.
    """
    scope = org.resolve_scope(db, identity)
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.tenant_id != scope.tenant_id or not org.can_access_kb(db, scope, kb):
        raise HTTPException(404, "knowledge base not found")
    return kb


@router.post("", response_model=KnowledgeBaseOut, status_code=201)
def create_kb(
    payload: KnowledgeBaseIn,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("kb:write")),
) -> KnowledgeBase:
    tenant_id = identity.tenant_id
    scope = org.resolve_scope(db, identity)
    if payload.department_id:
        path = org.dept_path(db, tenant_id, payload.department_id)
        if path is None:
            raise HTTPException(404, "department not found")
        if not scope.unrestricted and not (
            scope.dept_path and path.startswith(scope.dept_path)
        ):
            raise HTTPException(403, "department outside your scope")
    kb = KnowledgeBase(
        name=payload.name,
        description=payload.description,
        tenant_id=tenant_id,
        department_id=payload.department_id,
    )
    db.add(kb)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        action="kb.create",
        resource_type="knowledge_base",
        resource_id=kb.id,
        actor=identity.user_id,
        detail={"name": kb.name, "role": identity.role},
    )
    db.commit()
    db.refresh(kb)
    return _to_out(kb, 0, 0)


@router.get("", response_model=list[KnowledgeBaseOut])
def list_kb(
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("kb:read")),
) -> list[KnowledgeBase]:
    scope = org.resolve_scope(db, identity)
    rows = db.execute(
        select(
            KnowledgeBase,
            # distinct: joining documents and chunks multiplies rows, so a plain
            # COUNT would report docs × chunks instead of the real counts.
            func.count(distinct(Document.id)).label("docs"),
            func.count(distinct(Chunk.id)).label("chunks"),
        )
        .outerjoin(Document, Document.kb_id == KnowledgeBase.id)
        .outerjoin(Chunk, Chunk.kb_id == KnowledgeBase.id)
        .where(org.visible_kb_filter(scope))
        .group_by(KnowledgeBase.id)
    ).all()
    return [_to_out(kb, docs or 0, chunks or 0) for kb, docs, chunks in rows]


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
def get_kb(
    kb_id: str,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("kb:read")),
) -> KnowledgeBase:
    kb = _get_owned(db, kb_id, identity)
    docs = db.execute(select(func.count(Document.id)).where(Document.kb_id == kb_id)).scalar_one()
    chunks = db.execute(select(func.count(Chunk.id)).where(Chunk.kb_id == kb_id)).scalar_one()
    return _to_out(kb, docs, chunks)


@router.delete("/{kb_id}", status_code=204)
def delete_kb(
    kb_id: str,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("kb:write")),
) -> None:
    tenant_id = identity.tenant_id
    kb = _get_owned(db, kb_id, identity)
    audit.record(
        db,
        tenant_id=tenant_id,
        action="kb.delete",
        resource_type="knowledge_base",
        resource_id=kb_id,
        actor=identity.user_id,
        detail={"name": kb.name},
    )
    db.delete(kb)
    db.commit()
    invalidate(kb_id)
    get_object_store().delete_prefix(f"{kb_id}/")
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
    identity: Identity = Depends(require_permission("doc:write")),
) -> Document:
    tenant_id = identity.tenant_id
    _get_owned(db, kb_id, identity)
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
        detail={"kb_id": kb_id, "filename": filename, "size": len(data), "role": identity.role},
    )
    db.commit()
    db.refresh(document)
    return _doc_out(document)


@router.get("/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(
    kb_id: str, db: Session = Depends(get_db), identity: Identity = Depends(require_permission("kb:read"))
) -> list[Document]:
    _get_owned(db, kb_id, identity)
    rows = (
        db.execute(select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at))
        .scalars()
        .all()
    )
    return [_doc_out(doc) for doc in rows]


@router.get("/{kb_id}/chunks/{chunk_id}")
def get_chunk(
    kb_id: str,
    chunk_id: str,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("kb:read")),
) -> dict:
    """Full text of one chunk, for the citation drawer.

    Search returns a compressed slice of the chunk; the drawer needs the
    original so the reader can check what was actually indexed.
    """
    _get_owned(db, kb_id, identity)
    chunk = db.get(Chunk, chunk_id)
    if chunk is None or chunk.kb_id != kb_id:
        raise HTTPException(404, "chunk not found")
    doc = db.get(Document, chunk.document_id)
    return {
        "id": chunk.id,
        "doc_id": chunk.document_id,
        "doc_name": doc.filename if doc else "",
        "heading": chunk.heading,
        "ordinal": chunk.ordinal,
        "content": chunk.content,
    }


@router.get("/{kb_id}/documents/{doc_id}/content")
def download_document(
    kb_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("kb:read")),
) -> Response:
    _get_owned(db, kb_id, identity)
    doc = db.get(Document, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(404, "document not found")
    if not doc.object_key:
        raise HTTPException(404, "original file not stored for this document")
    try:
        data = get_object_store().get(doc.object_key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "original file missing from storage") from exc
    return Response(
        content=data,
        media_type=doc.mime or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
def delete_document(
    kb_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("doc:write")),
) -> None:
    tenant_id = identity.tenant_id
    _get_owned(db, kb_id, identity)
    doc = db.get(Document, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(404, "document not found")
    audit.record(
        db,
        tenant_id=tenant_id,
        action="document.delete",
        resource_type="document",
        resource_id=doc_id,
        detail={"kb_id": kb_id, "filename": doc.filename, "role": identity.role},
    )
    db.delete(doc)
    db.commit()
    invalidate(kb_id)
    obj = get_object_store()
    obj.delete_prefix(f"{kb_id}/{doc_id}/")
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
        department_id=kb.department_id,
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
