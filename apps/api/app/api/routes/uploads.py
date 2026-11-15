"""Chunked uploads: the browser sends 5 MB parts and can resume after a drop.

The bytes live in the object store; the row here only records which parts landed
so a reconnecting client can ask and continue. Nothing is indexed until the
client calls complete, so a half-uploaded file never becomes a document.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.knowledge import DocumentOut, _doc_out, _get_owned
from app.core.rbac import Identity, require_permission
from app.db.base import get_db
from app.db.models import Upload, UploadPart
from app.rag.ingest import DuplicateDocument, ingest_document
from app.services import audit
from app.storage import document_key, get_object_store, safe_name

router = APIRouter(prefix="/kb/{kb_id}/uploads", tags=["uploads"])

PART_SIZE = 5 * 1024 * 1024


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = ""


class UploadOut(BaseModel):
    upload_id: str
    filename: str
    part_size: int
    uploaded: list[int]
    bytes_received: int


class CompleteIn(BaseModel):
    parts: list[int] = Field(default_factory=list)


def _owned_upload(db: Session, kb_id: str, upload_id: str, tenant: str) -> Upload:
    upload = db.get(Upload, upload_id)
    if upload is None or upload.kb_id != kb_id or upload.tenant_id != tenant:
        raise HTTPException(status_code=404, detail="upload not found")
    return upload


def _parts(db: Session, upload_id: str) -> list[UploadPart]:
    return list(
        db.execute(
            select(UploadPart)
            .where(UploadPart.upload_id == upload_id)
            .order_by(UploadPart.part_number)
        ).scalars()
    )


def _out(db: Session, upload: Upload) -> UploadOut:
    parts = _parts(db, upload.id)
    return UploadOut(
        upload_id=upload.id,
        filename=upload.filename,
        part_size=PART_SIZE,
        uploaded=[part.part_number for part in parts],
        bytes_received=sum(part.size for part in parts),
    )


@router.post("", response_model=UploadOut, status_code=201)
def start_upload(
    kb_id: str,
    body: UploadIn,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("doc:write")),
) -> UploadOut:
    _get_owned(db, kb_id, identity.tenant_id)
    upload_id = uuid.uuid4().hex
    name = safe_name(body.filename)
    key = f"{kb_id}/incoming/{upload_id}/{name}"
    store = get_object_store()
    store.create_multipart(key, body.content_type)
    upload = Upload(
        id=upload_id,
        tenant_id=identity.tenant_id,
        kb_id=kb_id,
        object_key=key,
        filename=name,
        mime=body.content_type or "application/octet-stream",
    )
    db.add(upload)
    audit.record(
        db,
        tenant_id=identity.tenant_id,
        action="document.upload.start",
        resource_type="upload",
        resource_id=upload_id,
        actor=identity.user_id,
        detail={"kb_id": kb_id, "filename": name},
    )
    db.commit()
    return _out(db, upload)


@router.get("/{upload_id}", response_model=UploadOut)
def upload_status(
    kb_id: str,
    upload_id: str,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("doc:write")),
) -> UploadOut:
    upload = _owned_upload(db, kb_id, upload_id, identity.tenant_id)
    return _out(db, upload)


@router.put("/{upload_id}/parts/{part_number}", response_model=UploadOut)
async def put_part(
    kb_id: str,
    upload_id: str,
    part_number: int,
    request: Request,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("doc:write")),
) -> UploadOut:
    upload = _owned_upload(db, kb_id, upload_id, identity.tenant_id)
    if part_number < 1:
        raise HTTPException(status_code=400, detail="part numbers start at 1")
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty part")
    etag = get_object_store().upload_part(upload.object_key, upload.id, part_number, data)
    row = db.get(UploadPart, (upload.id, part_number))
    if row is None:
        db.add(UploadPart(upload_id=upload.id, part_number=part_number, size=len(data), etag=etag))
    else:
        row.size = len(data)
        row.etag = etag
    db.commit()
    return _out(db, upload)


@router.post("/{upload_id}/complete", response_model=DocumentOut, status_code=201)
def complete_upload(
    kb_id: str,
    upload_id: str,
    body: CompleteIn,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("doc:write")),
) -> DocumentOut:
    upload = _owned_upload(db, kb_id, upload_id, identity.tenant_id)
    stored = [part.part_number for part in _parts(db, upload.id)]
    if not stored:
        raise HTTPException(status_code=400, detail="no parts uploaded")
    wanted = body.parts or stored
    missing = sorted(set(wanted) - set(stored))
    if missing:
        raise HTTPException(status_code=409, detail=f"parts not uploaded: {missing}")

    store = get_object_store()
    store.complete_multipart(upload.object_key, upload.id, wanted)
    data = store.get(upload.object_key)
    try:
        document = ingest_document(db, kb_id, upload.filename, data, upload.mime)
    except DuplicateDocument as exc:
        raise HTTPException(status_code=409, detail="document already exists") from exc
    except Exception:
        store.delete_prefix(f"{kb_id}/incoming/{upload.id}/")
        raise
    store.delete_prefix(f"{kb_id}/incoming/{upload.id}/")
    db.query(UploadPart).filter(UploadPart.upload_id == upload.id).delete()
    db.delete(upload)
    audit.record(
        db,
        tenant_id=identity.tenant_id,
        action="document.upload.complete",
        resource_type="document",
        resource_id=document.id,
        actor=identity.user_id,
        detail={"kb_id": kb_id, "filename": upload.filename, "parts": len(wanted)},
    )
    db.commit()
    return _doc_out(document)


@router.delete("/{upload_id}", status_code=204)
def abort_upload(
    kb_id: str,
    upload_id: str,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("doc:write")),
) -> None:
    upload = _owned_upload(db, kb_id, upload_id, identity.tenant_id)
    get_object_store().abort_multipart(upload.object_key, upload.id)
    get_object_store().delete_prefix(f"{kb_id}/incoming/{upload.id}/")
    db.query(UploadPart).filter(UploadPart.upload_id == upload.id).delete()
    db.delete(upload)
    db.commit()


__all__ = ["document_key", "router"]
