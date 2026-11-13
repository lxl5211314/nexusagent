"""LangGraph agent graph.

Shape (a real Supervisor loop with a parallel map-reduce fan-out):

    START → supervisor ──┬─→ plan ──→ supervisor
                         ├─→ dispatch ──(Send per subtask, parallel)──→ worker ──→ supervisor
                         ├─→ synthesize ──→ supervisor
                         └─→ finalize ──→ END

``supervisor`` is the single decision point: it emits the routing decision as an
edge from ``route()``, which inspects the current state directly (no ``next`` key
in state — LangGraph would drop it because it is not a declared field). Fan-out
uses ``Send`` so sub-agents genuinely run concurrently and merge through state
reducers — the "拆解 → 并行 → 汇总" flow on the resume.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agent.llm import get_llm
from app.agent.state import AgentState, Finding
from app.agent.tools import ToolContext, registry
from app.core.config import get_settings
from app.guard.citation import check_answer
from app.rag.retriever import retrieve


def _emit(config: RunnableConfig, type_: str, payload: dict[str, Any]) -> None:
    emitter = config.get("configurable", {}).get("emit")
    if emitter:
        emitter(type_, payload)


async def supervisor(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Decide where to go next. Control flow lives here and nowhere else."""
    return {}


def route(state: AgentState) -> str:
    """Pure routing: read the current state, return the next node name."""
    settings = get_settings()
    if not state.get("answer"):
        if not state.get("plan"):
            return "plan"
        if not state.get("findings"):
            return "dispatch"
        return "synthesize"

    if state.get("answer_valid"):
        return "finalize"

    # Answer exists but failed the citation guard. Retry once with a stricter
    # prompt, then give up and refuse rather than emit an unsupported statement.
    if state.get("attempts", 0) <= settings.citation_max_retry:
        return "synthesize"
    return "finalize"


async def plan(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    settings = get_settings()
    _emit(config, "step.started", {"node": "plan", "note": "意图理解与任务拆解"})
    subtasks = await get_llm().plan(state["question"], settings.max_subtasks)
    return {"plan": subtasks}


async def dispatch(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    _emit(
        config,
        "step.started",
        {"node": "dispatch", "note": f"并行分发 {len(state['plan'])} 个子任务"},
    )
    return {}


def fan_out(state: AgentState) -> list[Send]:
    """Dynamic fan-out: one Send per subtask, run concurrently (map-reduce)."""
    return [
        Send("worker", {"task": task, "kb_id": state["kb_id"], "run_id": state["run_id"]})
        for task in state["plan"]
    ]


async def worker(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    task = state["task"]
    kb_id = state["kb_id"]
    run_id = state["run_id"]
    _emit(
        config,
        "step.started",
        {"node": "worker", "task_id": task.id, "note": f"执行子任务：{task.question}"},
    )

    ctx = ToolContext(kb_id=kb_id, run_id=run_id, emit=lambda t, p: _emit(config, t, p))
    result = registry.invoke("kb.search", {"query": task.question, "top_k": 3}, ctx)

    retrieval = retrieve(kb_id, task.question, top_k=3, keep=12)
    contexts = retrieval.items

    if result.ok and contexts:
        summary = f"命中 {len(contexts)} 个片段，首个来自《{contexts[0].doc_name}》：{contexts[0].compressed[:120]}"
    elif result.ok:
        summary = "该子问题未检索到足够相关片段"
    else:
        summary = f"检索失败：{result.error}"

    return {
        "findings": [Finding(task_id=task.id, question=task.question, summary=summary)],
        "contexts": contexts,
    }


async def synthesize(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    attempts = state.get("attempts", 0)
    strict = attempts > 0
    _emit(
        config,
        "step.started",
        {"node": "synthesize", "attempt": attempts + 1, "strict": strict, "note": "融合子任务结果并生成答案"},
    )

    contexts = state.get("contexts", [])
    findings = state.get("findings", [])
    llm = get_llm()

    answer_parts: list[str] = []
    async for delta in llm.generate_stream(state["question"], contexts, findings, strict=strict):
        answer_parts.append(delta)
        _emit(config, "token", {"text": delta})
    answer = "".join(answer_parts)

    check = check_answer(answer, contexts)
    if not check.valid:
        _emit(config, "guard.violation", {"violations": check.violations, "attempt": attempts + 1})

    return {
        "answer": answer,
        "answer_valid": check.valid,
        "citations": check.citations,
        "violations": check.violations,
        "attempts": attempts + 1,
    }


async def finalize(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    settings = get_settings()
    contexts = state.get("contexts", [])
    valid = state.get("answer_valid", False)

    cited_ids = {c.chunk_id for c in state.get("citations", [])}
    used = [ctx for ctx in contexts if ctx.chunk_id in cited_ids]
    if used:
        confidence = sum(max(0.0, min(1.0, ctx.scores.get("rerank", 0.0) * 2.0)) for ctx in used) / len(used)
    else:
        confidence = 0.0
    refused = (not valid) or confidence < settings.low_confidence_threshold

    _emit(
        config,
        "run.completed",
        {
            "refused": refused,
            "confidence": round(confidence, 3),
            "citations": [
                {
                    "index": c.index,
                    "chunk_id": c.chunk_id,
                    "doc_name": c.doc_name,
                    "heading": c.heading,
                    "snippet": c.snippet,
                }
                for c in state.get("citations", [])
            ],
            "attempts": state.get("attempts", 0),
            "contexts_used": len(used),
        },
    )
    return {"confidence": round(confidence, 3), "refused": refused}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("plan", plan)
    graph.add_node("dispatch", dispatch)
    graph.add_node("worker", worker)
    graph.add_node("synthesize", synthesize)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route,
        {
            "plan": "plan",
            "dispatch": "dispatch",
            "synthesize": "synthesize",
            "finalize": "finalize",
        },
    )
    graph.add_edge("plan", "supervisor")
    graph.add_conditional_edges("dispatch", fan_out, ["worker"])
    graph.add_edge("worker", "supervisor")
    graph.add_edge("synthesize", "supervisor")
    graph.add_edge("finalize", END)
    return graph.compile()
