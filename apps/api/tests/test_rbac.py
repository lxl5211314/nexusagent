"""Role checks at the HTTP edge: the gateway stamps the role, the API enforces it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.rbac import Identity, normalize_role
from app.main import app


def _client(role: str, tenant: str = "rbac") -> TestClient:
    return TestClient(app, headers={"X-User-Id": "u1", "X-User-Role": role, "X-Tenant-Id": tenant})


def test_unknown_role_falls_back_to_viewer() -> None:
    assert normalize_role("root") == "viewer"
    assert normalize_role(None) == "viewer"
    assert normalize_role(" ADMIN ") == "admin"


def test_identity_permissions_are_role_scoped() -> None:
    viewer = Identity("t", "u", "viewer")
    assert viewer.can("kb:read")
    assert not viewer.can("kb:write")
    assert not viewer.can("audit:read")

    editor = Identity("t", "u", "editor")
    assert editor.can("doc:write")
    assert not editor.can("settings:write")


def test_viewer_reads_but_cannot_write() -> None:
    admin = _client("admin")
    kb_id = admin.post("/api/kb", json={"name": "rbac-kb"}).json()["id"]

    viewer = _client("viewer")
    assert viewer.get("/api/kb").status_code == 200
    assert viewer.get(f"/api/kb/{kb_id}").status_code == 200

    denied = viewer.post("/api/kb", json={"name": "nope"})
    assert denied.status_code == 403
    assert "kb:write" in denied.json()["detail"]


def test_editor_writes_but_cannot_change_settings() -> None:
    editor = _client("editor")
    assert editor.post("/api/kb", json={"name": "editor-kb"}).status_code == 201

    denied = editor.put("/api/settings", json={"mcp_cite_external": False})
    assert denied.status_code == 403
    assert "settings:write" in denied.json()["detail"]


def test_only_admin_reads_the_audit_trail() -> None:
    assert _client("viewer").get("/api/audit").status_code == 403
    assert _client("editor").get("/api/audit").status_code == 403
    assert _client("admin").get("/api/audit").status_code == 200


def test_audit_records_who_did_it() -> None:
    admin = _client("admin", tenant="audit-actor")
    admin.post("/api/kb", json={"name": "who"})

    rows = admin.get("/api/audit?limit=5").json()
    assert rows and rows[0]["action"] == "kb.create"
    assert rows[0]["actor"] == "u1"


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_mcp_call_needs_the_permission(role: str) -> None:
    # viewer has no mcp:call at all; editor has it, so the request reaches the
    # catalogue and fails there instead of at the gate.
    response = _client(role, tenant="rbac-mcp").post(
        "/api/mcp/call", json={"server": "nope", "tool": "nope", "arguments": {}}
    )
    assert response.status_code == (403 if role == "viewer" else 404)
