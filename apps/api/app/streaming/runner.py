"""Run orchestration: persist events, expose them to SSE, drive the graph.

The design point here is that the *database is the event source*. Every event is
written to ``run_events`` before it is pushed to any subscriber, so a client that
disconnects and reconnects (``Last-Event-ID`` / ``after_seq``) can always replay
from the DB, across process restarts, without a sticky session.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.agent import graph as agent_graph
from app.db.base import session_scope
from app.db.models import Run, RunEvent


def create_run(kb_id: str, question: str) -> str:
    with session_scope() as session:
        run = Run(kb_id=kb_id, question=question, status="running")
        session.add(run)
        session.flush()
        run_id = run.id
        session.add(
            RunEvent(
                run_id=run_id,
                seq=1,
                type="run.created",
                payload=json.dumps({"kb_id": kb_id, "question": question}),
            )
        )
        run.seq = 1
        session.flush()
        return run_id


class EventWriter:
    """Persist + fan out events in order. ``seq`` is monotonic per run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        # create_run already persisted seq=1 (run.created); continue from there
        with session_scope() as session:
            run = session.get(Run, run_id)
            self._seq = run.seq if run is not None else 0

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def emit(self, type_: str, payload: dict[str, Any]) -> None:
        self._seq += 1
        event = {
            "run_id": self.run_id,
            "seq": self._seq,
            "type": type_,
            "payload": payload,
            "at": datetime.now(UTC).isoformat(),
        }
        with session_scope() as session:
            session.add(
                RunEvent(
                    run_id=self.run_id,
                    seq=self._seq,
                    type=type_,
                    payload=json.dumps(payload, ensure_ascii=False),
                )
            )
            session.merge(Run(id=self.run_id, seq=self._seq))
        for queue in self._subscribers:
            queue.put_nowait(event)


async def run_chat(kb_id: str, question: str) -> tuple[str, asyncio.Queue[dict[str, Any]]]:
    """Start a run and return (run_id, queue) for streaming."""
    run_id = create_run(kb_id, question)
    writer = EventWriter(run_id)
    queue = writer.subscribe()

    async def _execute() -> None:
        try:
            compiled = agent_graph.build_graph()
            result = await compiled.ainvoke(
                {"question": question, "kb_id": kb_id, "run_id": run_id, "attempts": 0},
                config={"configurable": {"emit": writer.emit}},
            )
            finalize_run(run_id, result)
            writer.emit("run.finished", {"status": "completed"})
        except Exception as exc:  # noqa: BLE001 - persist failure, never crash the loop
            with session_scope() as session:
                run = session.get(Run, run_id)
                run.status = "failed"
                run.error = str(exc)[:2000]
                run.finished_at = datetime.now(UTC)
            writer.emit("run.failed", {"error": str(exc)[:500]})

    asyncio.get_running_loop().create_task(_execute())
    return run_id, queue


def finalize_run(run_id: str, result: dict[str, Any]) -> None:
    with session_scope() as session:
        run = session.get(Run, run_id)
        run.status = "completed"
        run.answer = result.get("answer", "")
        run.confidence = result.get("confidence", 0.0)
        run.refused = bool(result.get("refused", False))
        run.finished_at = datetime.now(UTC)


def get_run(run_id: str) -> Run:
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise LookupError(f"run not found: {run_id}")
        return run


def events_after(run_id: str, after_seq: int, limit: int = 200) -> list[RunEvent]:
    with session_scope() as session:
        rows = (
            session.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
                .order_by(RunEvent.seq)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return rows


async def stream_run(run_id: str, after_seq: int = 0) -> AsyncIterator[dict[str, Any]]:
    """Yield events from DB, polling while the run is active. Safe across processes."""
    while True:
        rows = events_after(run_id, after_seq)
        for row in rows:
            after_seq = row.seq
            yield {
                "run_id": run_id,
                "seq": row.seq,
                "type": row.type,
                "payload": json.loads(row.payload),
                "at": row.created_at.isoformat() if row.created_at else "",
            }
        run = get_run(run_id)
        if run.status != "running":
            return
        await asyncio.sleep(0.25)
