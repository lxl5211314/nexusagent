"""Tool registry with the contract / permission / side-effect triple.

This mirrors the "智能交付 Agent 工具注册规范" from the resume: every tool
declares its input/output schema, its permission domain and whether it is
read-only. The registry refuses any invocation that is not explicitly granted,
and refuses *write* tools unless the caller passes an approval token — the
platform default is read-only, exactly like the YonBIP agent gateway.

Tools stay dumb: they call domain services and return serialisable results. The
agent graph never touches the registry's internals.
"""

from __future__ import annotations

import ast
import operator as _operator
import time
from dataclasses import dataclass, field
from typing import Any, Callable


class PermissionDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission: str
    side_effect: str  # "read" | "write"
    handler: Callable[..., Any]
    timeout_s: float = 30.0


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    latency_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    kb_id: str
    run_id: str
    permissions: set[str] = field(default_factory=lambda: {"kb:read", "tool:calc"})
    approval_token: str = ""
    emit: Callable[[str, dict], None] | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise PermissionDenied(f"unknown tool: {name}") from exc

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "output_schema": spec.output_schema,
                "permission": spec.permission,
                "side_effect": spec.side_effect,
            }
            for spec in self._specs.values()
        ]

    def invoke(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        spec = self.get(name)
        if spec.permission not in ctx.permissions:
            raise PermissionDenied(
                f"permission denied: tool '{name}' requires '{spec.permission}'"
            )
        if spec.side_effect == "write" and not ctx.approval_token:
            raise PermissionDenied(f"write tool '{name}' requires an approval token (human gate)")

        started = time.perf_counter()
        if ctx.emit:
            ctx.emit(
                "tool.call",
                {"tool": name, "permission": spec.permission, "side_effect": spec.side_effect, "args": args},
            )
        try:
            data = spec.handler(args, ctx)
            latency = round((time.perf_counter() - started) * 1000, 1)
            if ctx.emit:
                ctx.emit("tool.result", {"tool": name, "ok": True, "latency_ms": latency})
            return ToolResult(ok=True, data=data, latency_ms=latency)
        except Exception as exc:  # noqa: BLE001 - surfaced to caller as ToolResult
            latency = round((time.perf_counter() - started) * 1000, 1)
            if ctx.emit:
                ctx.emit("tool.result", {"tool": name, "ok": False, "error": str(exc), "latency_ms": latency})
            return ToolResult(ok=False, error=str(exc), latency_ms=latency)


# ---------------------------------------------------------------------------
# The three MVP tools
# ---------------------------------------------------------------------------
def _kb_search(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.rag.retriever import retrieve

    query = str(args.get("query", "")).strip()
    top_k = int(args.get("top_k", 3))
    if not query:
        raise ValueError("query is required")
    result = retrieve(ctx.kb_id, query, top_k=top_k, keep=15, with_trace=False)
    return {
        "items": [
            {
                "chunk_id": item.chunk_id,
                "doc_name": item.doc_name,
                "heading": item.heading,
                "snippet": item.compressed[:160],
                "score": item.scores,
            }
            for item in result.items
        ],
        "count": len(result.items),
    }


def _kg_expand(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.rag.index import get_index

    term = str(args.get("term", "")).strip()
    if not term:
        raise ValueError("term is required")
    index = get_index(ctx.kb_id)
    neighbours = index.graph.neighbours(term, limit=6) if index.graph else []
    hits = []
    for chunk_id in neighbours:
        view = index.by_id(chunk_id)
        if view:
            hits.append({"chunk_id": chunk_id, "doc_name": view.doc_name, "snippet": view.content[:100]})
    return {"term": term, "neighbours": neighbours, "hits": hits}


def _calc_eval(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    expression = str(args.get("expression", "")).strip()
    if not expression:
        raise ValueError("expression is required")
    return {"expression": expression, "value": _safe_eval(expression)}


_SAFE_OPS = {
    ast.Add: _operator.add,
    ast.Sub: _operator.sub,
    ast.Mult: _operator.mul,
    ast.Div: _operator.truediv,
    ast.Pow: _operator.pow,
    ast.FloorDiv: _operator.floordiv,
    ast.Mod: _operator.mod,
    ast.USub: _operator.neg,
    ast.UAdd: _operator.pos,
}


def _safe_eval(expression: str) -> float:
    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ValueError("exponent too large")
            return _SAFE_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
            return _SAFE_OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"unsupported expression element: {type(node).__name__}")

    return float(_eval(ast.parse(expression, mode="eval")))


registry = ToolRegistry()
registry.register(
    ToolSpec(
        name="kb.search",
        description="按语义+关键词在知识库中检索文档片段，返回排序后的片段及引用块 ID",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}},
        output_schema={"type": "object", "properties": {"items": {"type": "array"}}},
        permission="kb:read",
        side_effect="read",
        handler=_kb_search,
    )
)
registry.register(
    ToolSpec(
        name="kg.expand",
        description="按术语沿知识图谱一跳扩展相关片段",
        input_schema={"type": "object", "properties": {"term": {"type": "string"}}},
        output_schema={"type": "object", "properties": {"hits": {"type": "array"}}},
        permission="kb:read",
        side_effect="read",
        handler=_kg_expand,
    )
)
registry.register(
    ToolSpec(
        name="calc.eval",
        description="在受控沙箱内计算四则运算表达式（用于数值类子问题）",
        input_schema={"type": "object", "properties": {"expression": {"type": "string"}}},
        output_schema={"type": "object", "properties": {"value": {"type": "number"}}},
        permission="tool:calc",
        side_effect="read",
        handler=_calc_eval,
    )
)
