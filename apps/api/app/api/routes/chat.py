from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.schemas import ChatRequest, RetrievalRequest
from app.db.base import get_db
from app.db.models import KnowledgeBase, Run
from app.rag.retriever import retrieve
from app.streaming.runner import get_run, run_chat, stream_run

router = APIRouter(tags=["chat"])


def _sse(event: dict) -> str:
    return (
        f"id: {event['seq']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"
    )


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, db: Session = Depends(get_db)):
    if db.get(KnowledgeBase, payload.kb_id) is None:
        raise HTTPException(404, "knowledge base not found")

    run_id, queue = await run_chat(payload.kb_id, payload.question)

    async def generate():
        # Drain the live queue; the writer also persists to DB, so a disconnect
        # costs nothing — the run finishes and can be replayed via GET.
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if get_run(run_id).status != "running":
                    break
                yield ": keepalive\n\n"
                continue
            yield _sse(event)
            if event["type"] in {"run.failed", "run.finished"}:
                break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/stream")
async def resume_stream(
    run_id: str,
    last_event_id: int = Query(0, alias="Last-Event-ID"),
    after_seq: int = Query(0),
):
    try:
        run = get_run(run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

    start = max(last_event_id, after_seq)

    async def generate():
        async for event in stream_run(run_id, start):
            yield _sse(event)
            if event["type"] in {"run.failed", "run.finished"}:
                return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}")
async def get_run_detail(run_id: str):
    run = get_run(run_id)
    return {
        "id": run.id,
        "kb_id": run.kb_id,
        "question": run.question,
        "status": run.status,
        "answer": run.answer,
        "confidence": run.confidence,
        "refused": run.refused,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.post("/retrieval/search")
async def search(payload: RetrievalRequest, db: Session = Depends(get_db)):
    if db.get(KnowledgeBase, payload.kb_id) is None:
        raise HTTPException(404, "knowledge base not found")
    result = retrieve(payload.kb_id, payload.query, top_k=payload.top_k, with_trace=True)
    return {
        "items": [
            {
                "rank": item.rank,
                "chunk_id": item.chunk_id,
                "doc_name": item.doc_name,
                "heading": item.heading,
                "content": item.content[:400],
                "compressed": item.compressed,
                "scores": item.scores,
            }
            for item in result.items
        ],
        "trace": {
            "dense": result.trace.dense,
            "sparse": result.trace.sparse,
            "graph": result.trace.graph,
            "fused": result.trace.fused,
            "reranked": result.trace.reranked,
            "timings_ms": result.trace.timings_ms,
            "channels": result.trace.channels,
        },
    }
