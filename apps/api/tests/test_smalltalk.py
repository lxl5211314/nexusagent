"""Small talk: a greeting is not a retrieval problem, and a refusal must not
leave the model's guess on screen."""

from __future__ import annotations

import asyncio

from app.agent.graph import finalize, identity, route
from app.agent.smalltalk import compose_reply, refusal_text, smalltalk_kind
from app.rag.retriever import RetrievedChunk


def _ctx(idx: int, content: str, rerank: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"c{idx}",
        doc_id="d1",
        doc_name="doc.md",
        ordinal=idx,
        heading="h",
        content=content,
        compressed=content[:80],
        rank=idx,
        scores={"rerank": rerank},
    )


def test_greetings_are_recognised():
    for question in ("你好", "您好！", "hello", "在吗", "早上好。"):
        assert smalltalk_kind(question) == "greeting", question


def test_a_greeting_with_a_real_question_still_retrieves():
    # Politeness prefix, real question: must go through the pipeline.
    assert smalltalk_kind("你好，RRF 的 k 取多少") is None
    assert smalltalk_kind("您好，请问三级检索是哪三级？") is None


def test_identity_and_thanks():
    assert smalltalk_kind("你是谁") == "identity"
    assert smalltalk_kind("你能做什么？") == "identity"
    assert smalltalk_kind("谢谢") == "thanks"
    assert smalltalk_kind("thanks!") == "thanks"


def test_a_long_question_that_mentions_you_is_not_smalltalk():
    assert smalltalk_kind("文档里写的你是谁不重要，我问的是审计日志怎么存") is None
    assert smalltalk_kind("RRF 是怎么融合三路结果的") is None


def test_replies_name_the_knowledge_base():
    text = compose_reply("identity", "NexusAgent 示例库")
    assert "NexusAgent" in text
    assert "NexusAgent 示例库" in text
    assert "[1]" not in text


def test_route_sends_a_greeting_to_identity():
    assert route({"question": "你好", "kb_id": "kb1"}) == "identity"
    assert route({"question": "RRF 的 k 取多少", "kb_id": "kb1"}) == "plan"


def test_identity_node_answers_without_citations():
    events: list[tuple[str, dict]] = []
    config = {"configurable": {"emit": lambda t, p: events.append((t, p))}}
    out = asyncio.run(identity({"question": "你好", "kb_id": ""}, config))
    assert out["kind"] == "greeting"
    assert out["citations"] == []
    assert out["answer_valid"] is True
    assert "NexusAgent" in out["answer"]
    assert [t for t, _ in events].count("token") > 1


def test_identity_reply_is_not_refused():
    events: list[tuple[str, dict]] = []
    config = {"configurable": {"emit": lambda t, p: events.append((t, p))}}
    for question in ("你是谁", "你好", "谢谢"):
        out = asyncio.run(identity({"question": question, "kb_id": ""}, config))
        # No citations -> no evidence score; must not hit the corpus confidence gate.
        final = asyncio.run(finalize({**out, "contexts": []}, config))
        assert final["refused"] is False, question
        assert final["answer"] == out["answer"]
    assert not [p for t, p in events if t == "answer.replaced"]


def test_refusal_replaces_the_draft_instead_of_showing_it():
    events: list[tuple[str, dict]] = []
    config = {"configurable": {"emit": lambda t, p: events.append((t, p))}}
    # A draft with a citation, but nothing in the contexts supports it.
    state = {
        "question": "生产环境要几核",
        "answer": "生产环境推荐 8 核 32GB [1]。",
        "answer_valid": False,
        "citations": [],
        "contexts": [],
        "attempts": 1,
    }
    out = asyncio.run(finalize(state, config))

    assert out["refused"] is True
    assert out["answer"] == refusal_text()
    replaced = [p for t, p in events if t == "answer.replaced"]
    assert replaced and replaced[0]["answer"] == refusal_text()
    completed = next(p for t, p in events if t == "run.completed")
    assert completed["answer"] == refusal_text()
    assert "8 核" not in completed["answer"]


def test_a_supported_answer_is_not_replaced():
    events: list[tuple[str, dict]] = []
    config = {"configurable": {"emit": lambda t, p: events.append((t, p))}}
    ctx = _ctx(1, "RRF 的 k 取 60。", rerank=0.62)
    state = {
        "question": "RRF 的 k 取多少",
        "answer": "RRF 的 k 取 60 [1]。",
        "answer_valid": True,
        "citations": [],
        "contexts": [ctx],
        "attempts": 1,
    }
    out = asyncio.run(finalize(state, config))

    assert out["refused"] is False
    assert out["answer"].startswith("RRF 的 k 取 60")
    assert not [p for t, p in events if t == "answer.replaced"]
