"""Run state and chat turns cached in Redis, with a process-local fallback."""

from __future__ import annotations

import fakeredis
import pytest

from app.cache import (
    advance_run,
    cached_run,
    get_cache,
    recent_turns,
    remember_run,
    remember_turn,
    reset_cache,
)
from app.cache.store import RedisCache
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    monkeypatch.setattr(get_settings(), "redis_url", "")
    reset_cache()
    yield
    reset_cache()


def test_defaults_to_process_local():
    assert get_cache().name == "local"


def test_redis_backend_roundtrip_and_ttl():
    client = fakeredis.FakeRedis(decode_responses=True)
    cache = RedisCache("redis://fake/0", client=client)

    cache.set_json("k", {"a": 1}, ttl=60)
    assert cache.get_json("k") == {"a": 1}
    assert 0 < client.ttl("k") <= 60

    cache.delete("k")
    assert cache.get_json("k") is None


def test_unreachable_redis_reads_as_a_miss():
    class Broken:
        def get(self, *args, **kwargs):
            raise ConnectionError("down")

        def close(self):
            pass

    cache = RedisCache("redis://fake/0", client=Broken())
    assert cache.get_json("anything") is None


def test_advance_run_patches_hot_fields_only():
    remember_run("r1", tenant_id="t", kb_id="k", question="q", status="running", seq=2)

    advance_run("r1", 5)
    assert cached_run("r1")["seq"] == 5

    advance_run("r1", 6, "completed")
    state = cached_run("r1")
    assert state["seq"] == 6
    assert state["status"] == "completed"
    assert state["question"] == "q"


def test_advance_ignores_unknown_runs():
    advance_run("never-created", 3)
    assert cached_run("never-created") is None


def test_turns_are_newest_first_and_deduped():
    for i in (1, 2):
        remember_turn(
            tenant_id="t",
            run_id=f"r{i}",
            kb_id="k",
            question=f"q{i}",
            answer="a",
            refused=False,
            confidence=0.5,
        )
    remember_turn(
        tenant_id="t",
        run_id="r1",
        kb_id="k",
        question="q1",
        answer="a2",
        refused=False,
        confidence=0.6,
    )

    turns = recent_turns("t")
    assert [t["run_id"] for t in turns] == ["r1", "r2"]
    assert turns[0]["answer"] == "a2"


def test_run_detail_is_served_from_cache(client):
    remember_run(
        "cached-run",
        tenant_id="local",
        kb_id="k",
        question="q",
        status="completed",
        seq=9,
        answer="from cache",
        confidence=0.7,
    )

    response = client.get("/api/runs/cached-run")

    assert response.status_code == 200
    assert response.json()["answer"] == "from cache"
    assert response.json()["seq"] == 9


def test_cache_does_not_leak_across_tenants(client):
    remember_run(
        "cached-run",
        tenant_id="acme",
        kb_id="k",
        question="q",
        status="completed",
        seq=1,
    )

    assert client.get("/api/runs/cached-run", headers={"X-Tenant-Id": "globex"}).status_code == 404


def test_history_endpoint_returns_cached_turns(client):
    remember_turn(
        tenant_id="local",
        run_id="r1",
        kb_id="k",
        question="RRF 怎么融合？",
        answer="按排名倒数加权。",
        refused=False,
        confidence=0.6,
    )

    response = client.get("/api/chat/history")

    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["question"] == "RRF 怎么融合？"
