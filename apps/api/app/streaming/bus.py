"""Event fan-out between the run writer and any HTTP connection.

Single-process deployments use ``LocalBus``: an in-memory queue per run. That is
the cheapest thing that works and needs no extra service.

The moment the API runs as more than one process, the POST that starts a run and
the GET that streams it can land on different instances, and an in-memory queue
would never be filled. ``RedisBus`` solves that: the writer publishes to a
per-run channel and every instance forwards those messages into its own queue.
The database is still the source of truth — Redis only shortens the latency
between "event written" and "event rendered".
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)


class Bus(Protocol):
    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]: ...

    def publish(self, event: dict[str, Any]) -> None: ...

    async def forward(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None: ...

    def close(self) -> None: ...


class LocalBus:
    """Per-process queues. Also the fallback when Redis is not configured."""

    name = "local"

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._queues.setdefault(run_id, []).append(queue)
        return queue

    def publish(self, event: dict[str, Any]) -> None:
        for queue in self._queues.get(event["run_id"], []):
            queue.put_nowait(event)

    async def forward(self, run_id: str, queue: asyncio.Queue) -> None:
        return  # nothing to forward: publish already hit the local queues

    def close(self) -> None:
        self._queues.clear()


class RedisBus:
    """Publish/subscribe over Redis so any instance can serve any run."""

    name = "redis"

    def __init__(self, url: str, client=None, async_client=None) -> None:
        import redis
        import redis.asyncio as aredis

        self._url = url
        self._client = client or redis.Redis.from_url(url, decode_responses=True)
        self._async = async_client or aredis.Redis.from_url(url, decode_responses=True)

    @staticmethod
    def _channel(run_id: str) -> str:
        return f"nexus:run:{run_id}"

    def subscribe(self, run_id: str) -> asyncio.Queue:
        # The queue is filled by ``forward``; Redis itself is not per-subscriber.
        return asyncio.Queue()

    def publish(self, event: dict[str, Any]) -> None:
        try:
            self._client.publish(self._channel(event["run_id"]), json.dumps(event, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - the DB copy already made the event durable
            log.warning("redis publish failed: %s", exc)

    async def forward(self, run_id: str, queue: asyncio.Queue) -> None:
        pubsub = self._async.pubsub()
        await pubsub.subscribe(self._channel(run_id))
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    queue.put_nowait(json.loads(message["data"]))
                except (ValueError, TypeError):
                    continue
        finally:
            await pubsub.unsubscribe(self._channel(run_id))
            await pubsub.aclose()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("redis close failed: %s", exc)


_bus: Bus | None = None


def get_bus() -> Bus:
    global _bus
    if _bus is None:
        settings = get_settings()
        if settings.redis_url:
            try:
                _bus = RedisBus(settings.redis_url)
            except Exception:  # noqa: BLE001 - a broken Redis must not take the API down
                _bus = LocalBus()
        else:
            _bus = LocalBus()
    return _bus


def reset_bus() -> None:
    global _bus
    if _bus is not None:
        _bus.close()
    _bus = None
