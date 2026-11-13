"""Cache layer.

Redis is a latency optimisation, never the source of truth: run state and chat
turns also live in SQLite/Postgres, so a cold cache only costs a query. That is
why every write here is best-effort — a cache failure must not fail a run.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from app.core.config import get_settings


class Cache(Protocol):
    name: str

    def get_json(self, key: str) -> Any | None: ...

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None: ...

    def delete(self, key: str) -> None: ...

    def close(self) -> None: ...


class LocalCache:
    """Process-local fallback, so a single-node deployment needs no Redis."""

    name = "local"

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}

    def get_json(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, payload = item
        if expires_at and expires_at < time.time():
            self._data.pop(key, None)
            return None
        return json.loads(payload)

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        expires_at = time.time() + ttl if ttl else 0.0
        self._data[key] = (expires_at, json.dumps(value, ensure_ascii=False))

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def close(self) -> None:
        self._data.clear()


class RedisCache:
    name = "redis"

    def __init__(self, url: str, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            import redis

            self._client = redis.Redis.from_url(url, decode_responses=True)

    def get_json(self, key: str) -> Any | None:
        try:
            raw = self._client.get(key)
        except Exception:  # noqa: BLE001 - cache miss is not an error
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        try:
            if ttl:
                self._client.set(key, payload, ex=ttl)
            else:
                self._client.set(key, payload)
        except Exception:  # noqa: BLE001 - see module docstring
            return

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception:  # noqa: BLE001
            return

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            return


_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        settings = get_settings()
        _cache = RedisCache(settings.redis_url) if settings.redis_url else LocalCache()
    return _cache


def reset_cache() -> None:
    global _cache
    if _cache is not None:
        _cache.close()
    _cache = None
