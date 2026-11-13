"""RAG layer: chunking, tokenizer, BM25, fusion, embedding, index, retrieve."""

from __future__ import annotations

import pytest

from app.rag.bm25 import BM25
from app.rag.chunking import chunk_text
from app.rag.fusion import rrf_fuse
from app.rag.graph_store import TermGraph
from app.rag.text import cosine, split_sentences, tokenize


def test_tokenize_cjk_bigrams():
    tokens = tokenize("企业知识库")
    assert "知识" in tokens
    assert tokens  # non-empty


def test_tokenize_latin():
    tokens = tokenize("NexusAgent RAG pipeline")
    assert "nexusagent" in tokens
    assert "rag" in tokens


def test_split_sentences():
    sentences = split_sentences("第一句。第二句！第三句？第四句")
    assert sentences == ["第一句。", "第二句！", "第三句？", "第四句"]


def test_cosine():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_chunk_text_no_loss():
    text = "\n\n".join(f"## 章节{i}\n" + "内容" * 200 for i in range(5))
    chunks = chunk_text(text)
    assert chunks
    joined = "".join(c.content for c in chunks)
    assert "章节0" in joined and "章节4" in joined


def test_chunk_text_respects_size():
    text = "句子一。" * 500
    chunks = chunk_text(text, chunk_size=200, overlap=40)
    for c in chunks:
        assert len(c.content) <= 260  # chunk_size + margin


def test_bm25_ranking():
    docs = [
        "RRF 融合公式的 k 取 60，使用倒数排名融合",
        "向量召回在 Milvus 中做近邻搜索",
        "BM25 是关键词召回，使用倒排索引",
    ]
    bm25 = BM25.build(docs)
    scored = bm25.score("RRF 融合公式")
    top_idx = max(range(len(scored)), key=lambda i: scored[i])
    assert top_idx == 0
    assert scored[0] > 0


def test_rrf_fusion():
    rankings = {
        "dense": ["a", "b", "c"],
        "sparse": ["b", "a", "d"],
    }
    fused = rrf_fuse(rankings, k=60)
    # a = 1/61(dense) + 1/62(sparse); b = 1/62(dense) + 1/61(sparse) → equal
    scores = dict(fused)
    assert scores["a"] == pytest.approx(scores["b"])
    # d only appears in sparse at rank 3 → strictly lower than a/b
    assert scores["d"] < scores["a"]


def test_term_graph_build_and_recall():
    chunks = [("c1", "RRF 融合公式的 k 取 60"), ("c2", "向量召回在 Milvus"), ("c3", "BM25 关键词召回")]
    graph = TermGraph.build(chunks)
    recalled = graph.recall("RRF 融合公式")
    assert any(cid == "c1" for cid, _ in recalled)
