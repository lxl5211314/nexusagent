"""Agent graph: plan/dispatch/worker/synthesize/finalize and the citation guard."""

from __future__ import annotations

from app.agent.llm import MockLLM, split_subtasks
from app.agent.state import SubTask
from app.guard.citation import check_answer, parse_citations
from app.rag.retriever import RetrievedChunk


def _ctx(idx: int, content: str = "内容") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"c{idx}",
        doc_id="d1",
        doc_name="doc.md",
        ordinal=idx,
        heading="h",
        content=content,
        compressed=content[:80],
        rank=idx,
        scores={"rerank": 0.5},
    )


def test_split_subtasks_connectors():
    plan = split_subtasks("问题A。问题B。问题C", 5)
    assert len(plan) == 3
    assert isinstance(plan[0], SubTask)


def test_split_subtasks_cap():
    plan = split_subtasks("；".join(f"问题{i}" for i in range(10)), 3)
    assert len(plan) == 3


def test_parse_citations():
    assert parse_citations("依据 [1] 和 [2] 以及 [1]。") == [1, 2]


def test_check_answer_no_citation():
    check = check_answer("没有引用的答案", [])
    assert not check.valid
    assert "no citation" in check.violations[0]


def test_check_answer_out_of_range():
    check = check_answer("依据 [3]。", [_ctx(1)])
    assert not check.valid
    assert "out of range" in check.violations[0]


def test_check_answer_valid():
    ctxs = [_ctx(i) for i in range(1, 4)]
    check = check_answer("结论 [2] 和 [1]。", ctxs)
    assert check.valid
    assert {c.index for c in check.citations} == {1, 2}


def test_mock_llm_compose_has_citation():
    llm = MockLLM()
    ctxs = [_ctx(1, content="RRF 融合公式的 k 取 60。这是内容。")]
    text = llm._compose("RRF 公式 k 取多少", ctxs, [], strict=False)
    assert "[1]" in text
