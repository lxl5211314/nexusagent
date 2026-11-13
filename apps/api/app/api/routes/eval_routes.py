from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import EvalRunRequest
from app.core.tenant import get_tenant
from app.db.base import get_db
from app.db.models import KnowledgeBase
from app.eval.runner import run_eval
from app.services import audit

router = APIRouter(prefix="/eval", tags=["eval"])


@router.post("/runs")
async def start_eval(
    payload: EvalRunRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant),
):
    kb = db.get(KnowledgeBase, payload.kb_id)
    if kb is None or kb.tenant_id != tenant_id:
        raise HTTPException(404, "knowledge base not found")

    audit.record(
        db,
        tenant_id=tenant_id,
        action="eval.run",
        resource_type="knowledge_base",
        resource_id=kb.id,
        detail={"dataset": payload.dataset, "limit": payload.limit},
    )
    db.commit()

    try:
        return run_eval(payload.kb_id, payload.dataset, payload.limit, tenant_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
