"""Tenant isolation, audit trail and forward migration."""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.core.tenant import normalize_tenant
from app.db.base import engine, init_db, session_scope
from app.db.models import AuditLog


def _hdr(tenant: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant}


def test_normalize_tenant_rejects_unsafe_values():
    assert normalize_tenant(None) == "local"
    assert normalize_tenant("") == "local"
    assert normalize_tenant("acme") == "acme"
    assert normalize_tenant("acme-eu.1") == "acme-eu.1"
    assert normalize_tenant("bad tenant!") == "bad-tenant-"
    # sanitized value would start with "-", which is not a legal tenant id
    assert normalize_tenant('"; DROP TABLE runs; --') == "local"
    assert normalize_tenant("!!!") == "local"
    assert len(normalize_tenant("x" * 100)) == 32


def test_whoami_echoes_resolved_tenant(client):
    assert client.get("/api/whoami", headers=_hdr("acme")).json() == {"tenant_id": "acme"}
    assert client.get("/api/whoami").json() == {"tenant_id": "local"}


def test_kb_is_invisible_across_tenants(client):
    created = client.post("/api/kb", json={"name": "acme kb"}, headers=_hdr("acme"))
    assert created.status_code == 201
    kb_id = created.json()["id"]

    # owner sees it
    assert [kb["id"] for kb in client.get("/api/kb", headers=_hdr("acme")).json()] == [kb_id]
    # stranger does not, and a direct id probe is a 404 rather than a 403
    assert client.get("/api/kb", headers=_hdr("globex")).json() == []
    assert client.get(f"/api/kb/{kb_id}", headers=_hdr("globex")).status_code == 404
    assert client.get(f"/api/kb/{kb_id}/documents", headers=_hdr("globex")).status_code == 404
    assert client.delete(f"/api/kb/{kb_id}", headers=_hdr("globex")).status_code == 404


def test_upload_chat_and_audit_are_tenant_scoped(client):
    kb_id = client.post("/api/kb", json={"name": "audited"}, headers=_hdr("acme")).json()["id"]
    up = client.post(
        f"/api/kb/{kb_id}/documents",
        files={"file": ("note.md", b"# note\n\nRRF fuses ranked lists.", "text/markdown")},
        headers=_hdr("acme"),
    )
    assert up.status_code == 201

    resp = client.post(
        "/api/chat/stream", json={"kb_id": kb_id, "question": "RRF 是什么？"}, headers=_hdr("acme")
    )
    assert resp.status_code == 200

    rows = client.get("/api/audit", headers=_hdr("acme")).json()
    actions = [row["action"] for row in rows]
    assert "kb.create" in actions
    assert "document.upload" in actions
    assert "chat.ask" in actions
    assert all(row["tenant_id"] == "acme" for row in rows)

    assert client.get("/api/audit", headers=_hdr("globex")).json() == []


def test_audit_rows_are_append_only_and_ordered(client):
    kb_id = client.post("/api/kb", json={"name": "order"}, headers=_hdr("acme")).json()["id"]
    client.delete(f"/api/kb/{kb_id}", headers=_hdr("acme"))

    rows = client.get("/api/audit?limit=5", headers=_hdr("acme")).json()
    assert rows[0]["action"] == "kb.delete"  # newest first
    with session_scope() as session:
        stored = session.query(AuditLog).filter(AuditLog.resource_id == kb_id).all()
    assert {row.action for row in stored} == {"kb.create", "kb.delete"}


def test_init_db_adds_tenant_columns_to_legacy_schema():
    # Simulate an older database: drop the column, then re-run the bootstrap.
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_runs_tenant_id"))
        conn.execute(text("ALTER TABLE runs DROP COLUMN tenant_id"))

    columns = {c["name"] for c in inspect(engine).get_columns("runs")}
    assert "tenant_id" not in columns

    init_db()
    init_db()  # idempotent

    columns = {c["name"] for c in inspect(engine).get_columns("runs")}
    assert "tenant_id" in columns
