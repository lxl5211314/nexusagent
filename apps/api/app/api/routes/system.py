"""Backend health surface: which stores the process is actually talking to."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.rag.embedding import get_embedder
from app.rag.stores import get_graph_store, get_vector_store
from app.storage import get_object_store

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/backends")
def backends() -> dict:
    settings = get_settings()
    return {
        "vector": _health(get_vector_store, settings.vector_backend),
        "graph": _health(get_graph_store, settings.graph_backend),
        "objects": {"backend": settings.object_backend, "ok": _objects_ok()},
        "ocr": {"backend": settings.ocr_backend, "ok": True},
        "llm": settings.llm_provider,
        "embedding": {"provider": settings.embedding_provider, "dim": get_embedder().dim},
    }


def _objects_ok() -> bool:
    try:
        return bool(get_object_store().health())
    except Exception:  # noqa: BLE001 - same rule: report, do not raise
        return False


def _health(factory, backend: str) -> dict:
    try:
        return factory().health()
    except Exception as exc:  # noqa: BLE001 - a dead store must not 500 the probe
        return {"backend": backend, "ok": False, "error": str(exc)}
