"""Streaming runner: event persistence, seq continuity, DB replay (resume)."""

from __future__ import annotations

import asyncio

from app.db.base import session_scope
from app.db.models import RunEvent
from app.streaming.runner import create_run, events_after, get_run, run_chat, stream_run


def test_create_run_persists_first_event(seeded_kb):
    run_id = create_run(seeded_kb, "测试问题")
    run = get_run(run_id)
    assert run.status == "running"
    events = events_after(run_id, 0)
    assert events[0].type == "run.created"
    assert events[0].seq == 1


def test_events_are_sequenced(seeded_kb):
    run_id = create_run(seeded_kb, "问题")
    with session_scope() as session:
        session.add(RunEvent(run_id=run_id, seq=2, type="step.started", payload="{}"))
    assert events_after(run_id, 0)[1].seq == 2


def test_run_chat_full_flow(seeded_kb):
    async def _run():
        run_id, queue = await run_chat(seeded_kb, "RRF 融合公式里 k 取多少？")
        for _ in range(300):
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=0.5)
            except TimeoutError:
                if get_run(run_id).status != "running":
                    break
                continue
            if ev["type"] in ("run.finished", "run.failed"):
                break
        return run_id

    run_id = asyncio.run(_run())
    run = get_run(run_id)
    assert run.status == "completed"
    assert "[1]" in (run.answer or "")


def test_stream_run_replays_from_db(seeded_kb):
    async def _run():
        run_id, queue = await run_chat(seeded_kb, "幻觉抑制的三条防线是什么？")
        for _ in range(300):
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=0.5)
            except TimeoutError:
                if get_run(run_id).status != "running":
                    break
                continue
            if ev["type"] in ("run.finished", "run.failed"):
                break
        # Now replay from DB as if a client reconnected with Last-Event-ID=0
        replayed = [ev async for ev in stream_run(run_id, 0)]
        return run_id, replayed

    run_id, replayed = asyncio.run(_run())
    assert replayed[0]["type"] == "run.created"
    assert replayed[-1]["type"] == "run.finished"
    assert all(e["run_id"] == run_id for e in replayed)
