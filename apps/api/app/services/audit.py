"""Audit trail.

Every tenant-visible mutation goes through here. Rows are append-only: the API
never updates or deletes them, and the read endpoint is tenant-scoped.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog


def record(
    session: Session,
    *,
    tenant_id: str,
    action: str,
    resource_type: str,
    resource_id: str = "",
    actor: str = "",
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    """Stage an audit row on ``session``; the caller owns the commit."""
    row = AuditLog(
        tenant_id=tenant_id,
        actor=actor[:64],
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id)[:64],
        detail=json.dumps(detail or {}, ensure_ascii=False)[:4000],
    )
    session.add(row)
    return row


def recent(session: Session, tenant_id: str, limit: int = 50) -> list[AuditLog]:
    rows = session.execute(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.id.desc())
        .limit(max(1, min(limit, 200)))
    ).scalars()
    return list(rows)
