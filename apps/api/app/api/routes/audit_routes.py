"""Tenant-facing audit and identity endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.schemas import AuditOut, TenantOut
from app.core.rbac import Identity, require_permission
from app.core.tenant import get_tenant
from app.db.base import get_db
from app.services import audit

router = APIRouter(tags=["audit"])


@router.get("/whoami", response_model=TenantOut)
def whoami(tenant_id: str = Depends(get_tenant)) -> TenantOut:
    """Echo the tenant the gateway resolved — handy for debugging headers."""
    return TenantOut(tenant_id=tenant_id)


@router.get("/audit", response_model=list[AuditOut])
def list_audit(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("audit:read")),
):
    return [
        AuditOut(
            id=row.id,
            tenant_id=row.tenant_id,
            actor=row.actor,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            detail=row.detail,
            created_at=row.created_at,
        )
        for row in audit.recent(db, identity.tenant_id, limit)
    ]
