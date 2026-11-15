"""Settings the UI may change at runtime (currently the MCP toggles)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core import runtime
from app.core.config import get_settings
from app.core.rbac import Identity, require_permission
from app.db.base import get_db
from app.services import audit

router = APIRouter(tags=["settings"])


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mcp_tool_router: bool | None = None
    mcp_cite_external: bool | None = None


@router.get("/settings")
def read_settings() -> dict[str, Any]:
    settings = get_settings()
    return {
        "items": runtime.current(),
        "mcp": {
            "configured": bool(settings.mcp_config),
            "timeout_s": settings.mcp_timeout_s,
        },
    }


@router.put("/settings")
def write_settings(
    body: SettingsIn,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("settings:write")),
) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    try:
        items = runtime.update(changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(
        db,
        tenant_id=identity.tenant_id,
        action="settings.update",
        resource_type="settings",
        resource_id=",".join(sorted(changes)),
        actor=identity.user_id,
        detail=items,
    )
    db.commit()
    return {"items": items}
