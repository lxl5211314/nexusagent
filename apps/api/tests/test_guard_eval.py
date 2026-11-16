"""Guard ablation: metric accounting, arm switch, and the confidence refusal."""

from __future__ import annotations

from app.agent import graph as agent_graph
from app.agent.state import Citation
from app.core.config import get_settings
from app.eval.guard_eval import _set_arm, summarize
from app.rag.retriever import RetrievedChunk


def _ctx(idx: int, rerank: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"c{idx}",
        doc_id="d1",
        doc_name="doc.md",
        ordinal=idx,
        heading="h",
        content="RRF 的 k 取 60。",
        compressed="RRF 的 k 取 60。",
        rank=idx,
        scores={"rerank": rerank},
    )


def test_summarize_separates_unsupported_from_over_refusal():
    rows = [
        {"answerable": False, "refused": False, "confidence": 0.4, "violations": []},
        {"answerable": False, "refused": True, "confidence": 0.1, "violations": ["unsupported claim: x"]},
        {"answerable": True, "refused": False, "confidence": 0.6, "violations": []},
        {"answerable": True, "refused": True, "confidence": 0.2, "violations": ["unsupported claim: y"]},
    ]
    metrics = summarize(rows, "mixed", "on")
    assert metrics["unsupported_rate"] == 0.5
    assert metrics["over_refusal_rate"] == 0.5
    assert metrics["violations"] == {"unsupported claim": 2}


def test_arm_switch_flips_the_setting(monkeypatch):
    monkeypatch.delenv("NEXUS_GUARD_ENABLED", raising=False)
    _set_arm("off")
    assert get_settings().guard_enabled is False
    _set_arm("on")
    assert get_settings().guard_enabled is True


async def test_low_confidence_answer_is_refused(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "guard_enabled", True)
    state = {
        "contexts": [_ctx(1, rerank=0.1)],
        "citations": [Citation(index=1, chunk_id="c1", doc_name="doc.md", heading="h", snippet="RRF")],
        "answer_valid": True,
        "answer": "RRF 的 k 取 60 [1]。",
    }
    out = await agent_graph.finalize(state, {"configurable": {}})
    assert out["refused"] is True
    assert out["confidence"] == 0.1


async def test_guard_off_accepts_the_same_answer(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "guard_enabled", False)
    state = {
        "contexts": [_ctx(1, rerank=0.1)],
        "citations": [Citation(index=1, chunk_id="c1", doc_name="doc.md", heading="h", snippet="RRF")],
        "answer_valid": True,
        "answer": "生产环境推荐 8 核 32GB [1]。",
    }
    out = await agent_graph.finalize(state, {"configurable": {}})
    assert out["refused"] is False
