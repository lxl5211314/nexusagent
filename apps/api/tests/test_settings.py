"""Runtime settings: the panel's toggles change behaviour and leave an audit row."""

from __future__ import annotations

import pytest

from app.core import runtime
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def restore_settings():
    settings = get_settings()
    before = {key: getattr(settings, key) for key in runtime.EDITABLE}
    path = runtime._path()
    existed = path.exists()
    saved = path.read_text("utf-8") if existed else None
    yield
    for key, value in before.items():
        setattr(settings, key, value)
    if existed and saved is not None:
        path.write_text(saved, "utf-8")
    elif path.exists():
        path.unlink()


def test_read_settings_exposes_the_toggles(client):
    body = client.get("/api/settings").json()
    assert set(body["items"]) == {"mcp_tool_router", "mcp_cite_external"}
    assert all(isinstance(value, bool) for value in body["items"].values())
    assert "configured" in body["mcp"]


def test_put_settings_applies_and_persists(client):
    response = client.put(
        "/api/settings",
        json={"mcp_tool_router": False, "mcp_cite_external": False},
        headers={"X-Tenant-Id": "ops"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == {"mcp_tool_router": False, "mcp_cite_external": False}
    assert get_settings().mcp_tool_router is False
    assert runtime.load_overrides() == {"mcp_tool_router": False, "mcp_cite_external": False}
    assert client.get("/api/settings").json()["items"]["mcp_tool_router"] is False


def test_put_settings_is_audited(client):
    client.put("/api/settings", json={"mcp_cite_external": False}, headers={"X-Tenant-Id": "ops"})
    logs = client.get("/api/audit?limit=20", headers={"X-Tenant-Id": "ops"}).json()
    rows = [item for item in logs if item["action"] == "settings.update"]
    assert rows and rows[0]["resource_id"] == "mcp_cite_external"
    assert "mcp_cite_external" in rows[0]["detail"]


def test_put_settings_rejects_unknown_keys(client):
    response = client.put("/api/settings", json={"kb_id": "x"})
    assert response.status_code == 422
