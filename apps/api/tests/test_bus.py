"""The Redis bus: publish on one instance, receive on another."""

from __future__ import annotations

import asyncio

import fakeredis
import fakeredis.aioredis
import pytest

from app.core.config import get_settings
from app.streaming.bus import RedisBus, get_bus, reset_bus


@pytest.fixture
def fake_server():
    return fakeredis.FakeServer()


def _bus(fake_server) -> RedisBus:
    return RedisBus(
        "redis://fake/0",
        client=fakeredis.FakeRedis(server=fake_server, decode_responses=True),
        async_client=fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True),
    )


def test_factory_selects_redis_when_configured(monkeypatch):
    monkeypatch.setenv("NEXUS_REDIS_URL", "redis://127.0.0.1:6379/0")
    get_settings.cache_clear()
    reset_bus()
    try:
        assert get_bus().name == "redis"
    finally:
        reset_bus()
        monkeypatch.delenv("NEXUS_REDIS_URL")
        get_settings.cache_clear()


def test_factory_falls_back_to_local(monkeypatch):
    monkeypatch.delenv("NEXUS_REDIS_URL", raising=False)
    get_settings.cache_clear()
    reset_bus()
    try:
        assert get_bus().name == "local"
    finally:
        reset_bus()
        get_settings.cache_clear()


async def test_event_published_on_one_instance_reaches_another(fake_server):
    publisher = _bus(fake_server)
    subscriber = _bus(fake_server)
    queue = subscriber.subscribe("run-a")
    task = asyncio.create_task(subscriber.forward("run-a", queue))
    await asyncio.sleep(0.1)

    publisher.publish({"run_id": "run-a", "seq": 7, "type": "token", "payload": {"text": "hi"}})

    received = await asyncio.wait_for(queue.get(), timeout=3)
    assert received["seq"] == 7
    assert received["payload"]["text"] == "hi"

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_forward_ignores_other_runs(fake_server):
    publisher = _bus(fake_server)
    subscriber = _bus(fake_server)
    queue = subscriber.subscribe("run-b")
    task = asyncio.create_task(subscriber.forward("run-b", queue))
    await asyncio.sleep(0.1)

    publisher.publish({"run_id": "run-c", "seq": 1, "type": "token", "payload": {}})
    publisher.publish({"run_id": "run-b", "seq": 2, "type": "token", "payload": {}})

    received = await asyncio.wait_for(queue.get(), timeout=3)
    assert received["run_id"] == "run-b"
    assert queue.empty()

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_publish_failure_does_not_raise():
    class Broken:
        def publish(self, *args, **kwargs):
            raise ConnectionError("down")

        def close(self):
            pass

    bus = RedisBus("redis://fake/0", client=Broken(), async_client=None)
    bus.publish({"run_id": "r", "seq": 1, "type": "token", "payload": {}})
