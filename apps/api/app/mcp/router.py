"""Pick an external tool for a subtask when the knowledge base has nothing.

The rule is deliberately boring: score read-only tools by token overlap with the
question, require most of the question's terms to appear, and only fill in
arguments that have defaults. A tool whose required arguments are unknown is
skipped rather than called with garbage — a failed call costs a round trip and
pollutes the trace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.agent.tools import PermissionDenied, ToolContext, ToolRegistry, ToolResult
from app.mcp.catalog import MCPCatalog, MCPToolInfo
from app.rag.text import tokenize

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolChoice:
    server: str
    name: str
    score: float
    arguments: dict[str, Any]

    @property
    def spec_name(self) -> str:
        return f"mcp.{self.server}.{self.name}"


def _name_tokens(tool: MCPToolInfo) -> set[str]:
    return set(tokenize(tool.name.replace("_", " ")))


def choose_tool(question: str, tools: list[MCPToolInfo], threshold: float = 0.34) -> ToolChoice | None:
    terms = set(tokenize(question))
    if not terms:
        return None
    best: ToolChoice | None = None
    for tool in tools:
        if not tool.read_only:
            continue
        arguments = build_arguments(tool.input_schema)
        if arguments is None:
            continue
        words = _name_tokens(tool) | set(tokenize(tool.description))
        if not words:
            continue
        coverage = len(terms & words) / len(terms)
        # A name hit is worth more than a hit in prose.
        score = coverage + (0.2 if terms & _name_tokens(tool) else 0.0)
        if score < threshold:
            continue
        if best is None or score > best.score:
            best = ToolChoice(tool.server, tool.name, round(score, 3), arguments)
    return best


def build_arguments(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return an argument dict, or None when a required field has no default."""
    properties = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    arguments: dict[str, Any] = {}
    for key, spec in properties.items():
        if isinstance(spec, dict) and "default" in spec:
            arguments[key] = spec["default"]
        elif key in required:
            return None
    return arguments


def enrich_with_mcp(
    question: str,
    ctx: ToolContext,
    registry: ToolRegistry,
    catalog: MCPCatalog,
    threshold: float = 0.34,
) -> tuple[ToolChoice, ToolResult] | None:
    """Consult the best external tool, if one plausibly answers this subtask."""
    try:
        tools = catalog.tools()
    except Exception as exc:  # noqa: BLE001 - discovery is best effort
        log.warning("mcp discovery failed for a subtask: %s", exc)
        return None
    choice = choose_tool(question, tools, threshold=threshold)
    if choice is None:
        return None
    permission = f"mcp:{choice.server}"
    spec = next((item for item in registry.list() if item["name"] == choice.spec_name), None)
    if spec is None or spec["side_effect"] != "read":
        return None
    scoped = ToolContext(
        kb_id=ctx.kb_id,
        run_id=ctx.run_id,
        permissions=set(ctx.permissions) | {permission},
        approval_token=ctx.approval_token,
        emit=ctx.emit,
    )
    try:
        return choice, registry.invoke(choice.spec_name, choice.arguments, scoped)
    except PermissionDenied as exc:
        log.warning("mcp tool %s not permitted: %s", choice.spec_name, exc)
        return None
