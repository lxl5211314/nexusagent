"""Expose the discovered MCP catalogue and skill cards.

Read-only discovery is open to any tenant; calling an MCP tool goes through the
same permission + approval gate as in-process tools. A tool the server marks as
not read-only is treated as a write and needs an approval token.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.skills import find_skill, load_skills
from app.agent.tools import PermissionDenied, ToolContext, registry
from app.core.config import get_settings
from app.core.tenant import get_tenant
from app.db.base import get_db
from app.mcp.catalog import get_catalog, register_catalog_tools
from app.services import audit

router = APIRouter(tags=["extensions"])


class MCPCall(BaseModel):
    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    approval_token: str = ""


@router.get("/mcp/servers")
def list_servers() -> dict[str, Any]:
    return {"items": get_catalog().servers()}


@router.get("/mcp/tools")
def list_tools() -> dict[str, Any]:
    catalog = get_catalog()
    return {
        "items": [
            {
                "server": tool.server,
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "side_effect": "read" if tool.read_only else "write",
                "permission": f"mcp:{tool.server}" if tool.read_only else f"mcp:{tool.server}:write",
            }
            for tool in catalog.tools()
        ],
        "errors": catalog.errors(),
    }


@router.post("/mcp/refresh")
def refresh() -> dict[str, Any]:
    catalog = get_catalog()
    tools = catalog.refresh()
    # New servers mean new tools; keep the agent's registry in step with discovery.
    register_catalog_tools(registry, catalog)
    return {"count": len(tools), "errors": catalog.errors()}


@router.post("/mcp/call")
def call(
    body: MCPCall,
    db: Session = Depends(get_db),
    tenant: str = Depends(get_tenant),
) -> dict[str, Any]:
    catalog = get_catalog()
    info = next(
        (tool for tool in catalog.tools() if tool.server == body.server and tool.name == body.tool),
        None,
    )
    if info is None:
        _audit(db, tenant, body, "missing", "unknown tool")
        raise HTTPException(status_code=404, detail="unknown MCP tool")
    permission = f"mcp:{body.server}" if info.read_only else f"mcp:{body.server}:write"
    context = ToolContext(
        kb_id="",
        run_id=f"mcp-{tenant}",
        permissions={"kb:read", "tool:calc", permission},
        approval_token=body.approval_token,
    )
    try:
        if not info.read_only and not context.approval_token:
            raise PermissionDenied("write tools need an approval token")
        result = catalog.call(body.server, body.tool, body.arguments)
    except PermissionDenied as exc:
        _audit(db, tenant, body, "denied", str(exc))
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        _audit(db, tenant, body, "error", str(exc)[:200])
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc
    _audit(db, tenant, body, "ok" if not result["is_error"] else "tool_error", result["text"][:200])
    return {"ok": not result["is_error"], "text": result["text"], "content": result["content"]}


def _audit(db: Session, tenant: str, body: MCPCall, outcome: str, detail: str) -> None:
    try:
        audit.record(
            db,
            tenant_id=tenant,
            action="mcp.call",
            resource_type="mcp_tool",
            resource_id=f"{body.server}.{body.tool}",
            actor="api",
            detail={
                "outcome": outcome,
                "arguments": sorted(body.arguments),
                "detail": detail,
            },
        )
        db.commit()
    except Exception:  # noqa: BLE001 - an audit hiccup must not break the call path
        db.rollback()


@router.get("/skills")
def list_skills() -> dict[str, Any]:
    cards = load_skills(get_settings().skills_dir)
    return {
        "items": [
            {"name": card.name, "description": card.description, "path": card.path}
            for card in cards
        ]
    }


@router.get("/skills/{name}")
def get_skill(name: str) -> dict[str, Any]:
    card = find_skill(get_settings().skills_dir, name)
    if card is None:
        raise HTTPException(status_code=404, detail="unknown skill")
    return {"name": card.name, "description": card.description, "body": card.body}
