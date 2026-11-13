"""Run state and chat history kept in Redis (with a DB fallback)."""

from __future__ import annotations

from typing import Any

from app.cache.store import get_cache

RUN_TTL = 3600
RECENT_LIMIT = 50


def _state_key(run_id: str) -> str:
    return f"nexus:run:{run_id}:state"


def _recent_key(tenant_id: str) -> str:
    return f"nexus:chat:{tenant_id}:recent"


def remember_run(
    run_id: str,
    *,
    tenant_id: str,
    kb_id: str,
    question: str,
    status: str,
    seq: int,
    answer: str = "",
    confidence: float = 0.0,
    refused: bool = False,
    error: str = "",
    created_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    get_cache().set_json(
        _state_key(run_id),
        {
            "id": run_id,
            "kb_id": kb_id,
            "question": question,
            "tenant_id": tenant_id,
            "status": status,
            "seq": seq,
            "answer": answer,
            "confidence": confidence,
            "refused": refused,
            "error": error,
            "created_at": created_at,
            "finished_at": finished_at,
        },
        RUN_TTL,
    )


def cached_run(run_id: str) -> dict[str, Any] | None:
    return get_cache().get_json(_state_key(run_id))


def advance_run(run_id: str, seq: int, status: str | None = None) -> None:
    """Patch the hot fields without a second DB read."""
    state = cached_run(run_id)
    if state is None:
        return
    state["seq"] = seq
    if status is not None:
        state["status"] = status
    get_cache().set_json(_state_key(run_id), state, RUN_TTL)


def forget_run(run_id: str) -> None:
    get_cache().delete(_state_key(run_id))


def remember_turn(
    *,
    tenant_id: str,
    run_id: str,
    kb_id: str,
    question: str,
    answer: str,
    refused: bool,
    confidence: float,
) -> None:
    key = _recent_key(tenant_id)
    cache = get_cache()
    turns = cache.get_json(key) or []
    turns = [t for t in turns if t.get("run_id") != run_id]
    turns.insert(
        0,
        {
            "run_id": run_id,
            "kb_id": kb_id,
            "question": question,
            "answer": answer,
            "refused": refused,
            "confidence": confidence,
        },
    )
    cache.set_json(key, turns[:RECENT_LIMIT], RUN_TTL)


def recent_turns(tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
    turns = get_cache().get_json(_recent_key(tenant_id)) or []
    return turns[:limit]
