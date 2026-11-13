"""Evaluation runner: dataset-driven retrieval + answer quality, with trace.

The dataset format is JSONL, one case per line:

    {"question": "...", "gold": ["snippet that must appear in retrieved text"], "gold_answer": "..."}

``gold`` items are literal substrings that must appear in a retrieved chunk for
the case to count as a hit — this keeps the eval self-contained and lets a
human label the dataset without knowing chunk IDs.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.db.base import session_scope
from app.db.models import EvalRun
from app.eval.metrics import faithfulness, mrr_at_k, recall_at_k
from app.rag.retriever import retrieve

DATASETS_DIR = Path(__file__).resolve().parents[2] / "data" / "eval"


def load_dataset(name: str) -> list[dict]:
    path = DATASETS_DIR / f"{name}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {name} (looked at {path})")
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def run_eval(kb_id: str, dataset: str = "sample", limit: int = 0, tenant_id: str = "local") -> dict:
    cases = load_dataset(dataset)
    if limit > 0:
        cases = cases[:limit]

    results: list[dict] = []
    for case in cases:
        question = case["question"]
        gold = case.get("gold", [])
        retrieval = retrieve(kb_id, question, top_k=5, keep=20)
        contents = [item.content for item in retrieval.items]
        cited = [item.content for item in retrieval.items if item.scores.get("rerank", 0) > 0.2]

        answer = ""
        if retrieval.items:
            top = retrieval.items[0]
            answer = f"根据知识库内容：{top.compressed[:160]} [1]。"
            cited = [top.content]

        results.append(
            {
                "question": question,
                "recall@5": round(recall_at_k(contents, gold, 5), 4),
                "mrr@5": round(mrr_at_k(contents, gold, 5), 4),
                "faithfulness": round(faithfulness(answer, cited), 4),
                "top1": retrieval.items[0].doc_name if retrieval.items else None,
            }
        )

    n = max(1, len(results))
    metrics = {
        "cases": len(results),
        "dataset": dataset,
        "recall@5": round(sum(r["recall@5"] for r in results) / n, 4),
        "mrr@5": round(sum(r["mrr@5"] for r in results) / n, 4),
        "faithfulness": round(sum(r["faithfulness"] for r in results) / n, 4),
        "zero_hit_cases": sum(1 for r in results if r["recall@5"] == 0),
    }

    with session_scope() as session:
        session.add(
            EvalRun(
                kb_id=kb_id,
                tenant_id=tenant_id,
                dataset=dataset,
                metrics=json.dumps(metrics, ensure_ascii=False),
                detail=json.dumps(results, ensure_ascii=False),
            )
        )

    return {"metrics": metrics, "results": results}
