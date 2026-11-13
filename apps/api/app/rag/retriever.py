"""Hybrid retrieval pipeline: dense + BM25 + graph → RRF → rerank → compress."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.rag.embedding import get_embedder
from app.rag.fusion import rrf_fuse
from app.rag.index import get_index
from app.rag.rerank import HeuristicReranker, compress_context
from app.rag.stores import get_graph_store, get_vector_store


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    doc_name: str
    ordinal: int
    heading: str
    content: str
    compressed: str
    rank: int
    scores: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class RetrievalTrace:
    dense: list[dict] = field(default_factory=list)
    sparse: list[dict] = field(default_factory=list)
    graph: list[dict] = field(default_factory=list)
    fused: list[dict] = field(default_factory=list)
    reranked: list[dict] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    channels: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class RetrievalResult:
    items: list[RetrievedChunk] = field(default_factory=list)
    trace: RetrievalTrace = field(default_factory=RetrievalTrace)


def retrieve(
    kb_id: str,
    query: str,
    top_k: int | None = None,
    keep: int | None = None,
    with_trace: bool = False,
) -> RetrievalResult:
    settings = get_settings()
    keep = keep or settings.retrieve_top_k
    top_k = top_k or settings.rerank_top_k
    index = get_index(kb_id)
    trace = RetrievalTrace()

    started = time.perf_counter()
    vector = get_embedder().embed(query)
    dense = get_vector_store().search(kb_id, vector, keep)
    trace.timings_ms["dense"] = round((time.perf_counter() - started) * 1000, 2)

    started = time.perf_counter()
    sparse = index.sparse(query, keep)
    trace.timings_ms["sparse"] = round((time.perf_counter() - started) * 1000, 2)

    started = time.perf_counter()
    graph = get_graph_store().recall(kb_id, query, keep)
    trace.timings_ms["graph"] = round((time.perf_counter() - started) * 1000, 2)

    started = time.perf_counter()
    fused = rrf_fuse(
        {
            "dense": [cid for cid, _ in dense],
            "sparse": [cid for cid, _ in sparse],
            "graph": [cid for cid, _ in graph],
        },
        k=settings.rrf_k,
        weights={"dense": 1.0, "sparse": 1.0, "graph": 0.5},
    )
    trace.timings_ms["fusion"] = round((time.perf_counter() - started) * 1000, 2)

    candidates = []
    for chunk_id, _score in fused[:keep]:
        view = index.by_id(chunk_id)
        if view is None:
            continue
        candidates.append(
            {
                "chunk_id": view.id,
                "doc_id": view.doc_id,
                "doc_name": view.doc_name,
                "ordinal": view.ordinal,
                "heading": view.heading,
                "content": view.content,
            }
        )

    started = time.perf_counter()
    reranker = HeuristicReranker()
    scores = reranker.score(query, candidates)
    for candidate, score in zip(candidates, scores, strict=False):
        candidate["rerank"] = round(score, 4)
    candidates.sort(key=lambda item: item["rerank"], reverse=True)
    trace.timings_ms["rerank"] = round((time.perf_counter() - started) * 1000, 2)

    dense_map = {cid: round(score, 4) for cid, score in dense}
    sparse_map = {cid: round(score, 4) for cid, score in sparse}
    graph_map = {cid: round(score, 4) for cid, score in graph}
    fused_map = {cid: round(score, 6) for cid, score in fused}

    items: list[RetrievedChunk] = []
    for rank, candidate in enumerate(candidates[:top_k], start=1):
        chunk_id = candidate["chunk_id"]
        items.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                doc_id=candidate["doc_id"],
                doc_name=candidate["doc_name"],
                ordinal=candidate["ordinal"],
                heading=candidate["heading"],
                content=candidate["content"],
                compressed=compress_context(candidate["content"], query),
                rank=rank,
                scores={
                    "dense": dense_map.get(chunk_id, 0.0),
                    "sparse": sparse_map.get(chunk_id, 0.0),
                    "graph": graph_map.get(chunk_id, 0.0),
                    "rrf": fused_map.get(chunk_id, 0.0),
                    "rerank": candidate["rerank"],
                },
            )
        )

    if with_trace:
        trace.dense = _preview(index, dense)
        trace.sparse = _preview(index, sparse)
        trace.graph = _preview(index, graph)
        trace.fused = _preview(index, fused)
        trace.reranked = [
            {
                "chunk_id": c["chunk_id"],
                "doc_name": c["doc_name"],
                "heading": c["heading"],
                "score": c["rerank"],
            }
            for c in candidates[:top_k]
        ]
        trace.channels = {"dense": len(dense), "sparse": len(sparse), "graph": len(graph)}

    return RetrievalResult(items=items, trace=trace)


def _preview(index, ranked: list[tuple[str, float]], limit: int = 8) -> list[dict]:
    preview = []
    for cid, score in ranked[:limit]:
        view = index.by_id(cid)
        if view is None:
            continue
        preview.append(
            {
                "chunk_id": cid,
                "doc_name": view.doc_name,
                "heading": view.heading,
                "score": round(score, 6),
                "snippet": view.content[:80],
            }
        )
    return preview
