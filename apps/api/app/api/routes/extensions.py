"""Expose the discovered MCP catalogue and skill cards.

Read-only discovery is open to any tenant; calling an MCP tool goes through the
same permission + approval gate as in-process tools. A tool the server marks as
not read-only is treated as a write and needs an approval token.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.agent.skills import find_skill, load_skills
from app.agent.tools import PermissionDenied, ToolContext
from app.core.config import get_settings
from app.core.tenant import get_tenant
from app.mcp.catalog import get_catalog

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
    return {"count": len(tools), "errors": catalog.errors()}


@router.post("/mcp/call")
def call(body: MCPCall, tenant: str = Depends(get_tenant)) -> dict[str, Any]:
    catalog = get_catalog()
    info = next(
        (tool for tool in catalog.tools() if tool.server == body.server and tool.name == body.tool),
        None,
    )
    if info is None:
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
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc
    return {"ok": not result["is_error"], "text": result["text"], "content": result["content"]}


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
