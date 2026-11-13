from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.schemas import EvalRunRequest
from app.db.base import session_scope
from app.db.models import KnowledgeBase
from app.eval.runner import run_eval

router = APIRouter(prefix="/eval", tags=["eval"])


@router.post("/runs")
async def start_eval(payload: EvalRunRequest):
    with session_scope() as session:
        if session.get(KnowledgeBase, payload.kb_id) is None:
            raise HTTPException(404, "knowledge base not found")
    try:
        return run_eval(payload.kb_id, payload.dataset, payload.limit)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
