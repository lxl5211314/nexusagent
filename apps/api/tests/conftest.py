"""Shared test fixtures: isolated temp DB, seeded KB, HTTP client.

The temp DB path is set via env *before* any app import so the module-level
engine binds to it. pytest imports conftest before test modules, so this works.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# MUST run before importing app.* (engine is module-level, bound at import time).
_TMPDIR = tempfile.mkdtemp(prefix="nexusagent-test-")
os.environ.setdefault("NEXUS_DATABASE_URL", f"sqlite:///{(Path(_TMPDIR) / 'test.db').as_posix()}")
os.environ.setdefault("NEXUS_DATA_DIR", _TMPDIR)
os.environ.setdefault("NEXUS_LLM_PROVIDER", "mock")
os.environ.setdefault("NEXUS_EMBEDDING_PROVIDER", "hash")

import pytest

from app.db.base import init_db
from app.rag.index import invalidate

init_db()


@pytest.fixture()
def session():
    """Plain ORM session on the test DB (for store round-trip checks)."""
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def seeded_kb():
    """Seed sample docs into the test DB; return kb_id."""
    from app.seed import seed_sample

    kb_id = seed_sample()
    yield kb_id
    invalidate(kb_id)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
