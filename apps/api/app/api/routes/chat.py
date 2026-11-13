from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.schemas import ChatRequest, RetrievalRequest
from app.core.tenant import get_tenant
from app.db.base import get_db, session_scope
from app.db.models import KnowledgeBase
from app.rag.retriever import retrieve
from app.services import audit
from app.streaming.runner import get_run, pending_events, run_chat, stream_run

router = APIRouter(tags=["chat"])


def _sse(event: dict) -> str:
    return (
        f"id: {event['seq']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"
    )


def _owned_run(run_id: str, tenant_id: str):
    """Lookup that hides other tenants' runs behind a 404."""
    try:
        run = get_run(run_id)
    except LookupError:
        return None
    return run if run.tenant_id == tenant_id else None


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant),
):
    kb = db.get(KnowledgeBase, payload.kb_id)
    if kb is None or kb.tenant_id != tenant_id:
        raise HTTPException(404, "knowledge base not found")

    with session_scope() as session:
        audit.record(
            session,
            tenant_id=tenant_id,
            action="chat.ask",
            resource_type="knowledge_base",
            resource_id=kb.id,
            detail={"question": payload.question[:200]},
        )

    run_id, queue = await run_chat(payload.kb_id, payload.question, tenant_id)

    async def generate():
        # Drain the live queue; the writer also persists to DB, so a disconnect
        # costs nothing: the run finishes and can be replayed via GET.
        # Tell the client its run id up front so it can resume after a drop.
        yield f"event: run.started\ndata: {json.dumps({'run_id': run_id})}\n\n"
        last_seq = 0
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
            except TimeoutError:
                # The queue is a latency optimisation, not the source of truth:
                # anything it missed (slow relay, reconnect, instance switch)
                # is replayed from the table before we check for the terminal row.
                replayed = pending_events(run_id, last_seq)
                for row in replayed:
                    last_seq = row["seq"]
                    yield _sse(row)
                if replayed and replayed[-1]["type"] in {"run.failed", "run.finished"}:
                    break
                if get_run(run_id).status != "running":
                    break
                yield ": keepalive\n\n"
                continue
            if event["seq"] <= last_seq:
                continue
            last_seq = event["seq"]
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
    tenant_id: str = Depends(get_tenant),
):
    run = _owned_run(run_id, tenant_id)
    if run is None:
        raise HTTPException(404, "run not found")

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
async def get_run_detail(run_id: str, tenant_id: str = Depends(get_tenant)):
    run = _owned_run(run_id, tenant_id)
    if run is None:
        raise HTTPException(404, "run not found")
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
async def search(
    payload: RetrievalRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant),
):
    kb = db.get(KnowledgeBase, payload.kb_id)
    if kb is None or kb.tenant_id != tenant_id:
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
