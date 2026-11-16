"""Guard ablation: the same labeled questions answered with the guard off and on.

The two arms differ in exactly one setting (``NEXUS_GUARD_ENABLED``):

* ``off`` — what a prompt-only RAG chain does: retrieve, generate, return. The
  system prompt still *asks* the model to refuse without evidence, but nothing
  enforces it, so every question gets an answer.
* ``on``  — the three guard layers: citation markers must resolve to a retrieved
  chunk, each cited sentence must be lexically supported by that chunk, and an
  answer whose evidence scores below the calibrated rerank threshold is refused.

``unsupported_rate`` counts answers to questions the corpus cannot answer: an
answer that was produced at all is, by definition, unsupported. ``over_refusal_rate``
counts the price of the guard on questions the corpus *can* answer.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

from app.agent import graph as agent_graph
from app.core.config import get_settings
from app.eval.runner import load_dataset

ARMS = ("off", "on")
_ENV_KEY = "NEXUS_GUARD_ENABLED"


def _set_arm(arm: str) -> None:
    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm} (expected one of {ARMS})")
    os.environ[_ENV_KEY] = "1" if arm == "on" else "0"
    get_settings.cache_clear()


def summarize(rows: list[dict[str, Any]], dataset: str, arm: str) -> dict[str, Any]:
    unanswerable = [row for row in rows if not row["answerable"]]
    answerable = [row for row in rows if row["answerable"]]
    answered_unanswerable = [row for row in unanswerable if not row["refused"]]
    refused_answerable = [row for row in answerable if row["refused"]]
    reasons = Counter(
        violation.split(":")[0] for row in rows for violation in row.get("violations", [])
    )
    return {
        "dataset": dataset,
        "arm": arm,
        "cases": len(rows),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "answered_unanswerable": len(answered_unanswerable),
        "unsupported_rate": round(len(answered_unanswerable) / len(unanswerable), 4) if unanswerable else None,
        "refused_answerable": len(refused_answerable),
        "over_refusal_rate": round(len(refused_answerable) / len(answerable), 4) if answerable else None,
        "mean_confidence": round(sum(row["confidence"] for row in rows) / len(rows), 4) if rows else 0.0,
        "violations": dict(reasons),
    }


async def evaluate(
    kb_id: str,
    dataset: str = "unanswerable",
    arm: str = "on",
    limit: int = 0,
) -> dict[str, Any]:
    _set_arm(arm)
    cases = load_dataset(dataset)
    if limit > 0:
        cases = cases[:limit]

    compiled = agent_graph.build_graph()
    rows: list[dict[str, Any]] = []
    for case in cases:
        question = case["question"]
        state = await compiled.ainvoke(
            {"question": question, "kb_id": kb_id, "run_id": "eval", "attempts": 0},
            config={"configurable": {}},
        )
        rows.append(
            {
                "question": question,
                "answerable": bool(case.get("answerable", True)),
                "refused": bool(state.get("refused", False)),
                "confidence": float(state.get("confidence", 0.0)),
                "answer": (state.get("answer") or "")[:160],
                "violations": list(state.get("violations") or []),
            }
        )

    return {"metrics": summarize(rows, dataset, arm), "results": rows}
