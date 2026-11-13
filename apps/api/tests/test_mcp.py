"""MCP discovery, the stdio handshake, and the tool bridge."""

from __future__ import annotations

import sys
import textwrap

import pytest

from app.agent.tools import PermissionDenied, ToolContext, ToolRegistry
from app.mcp.catalog import MCPCatalog, register_catalog_tools
from app.mcp.client import MCPClient, MCPError
from app.mcp.config import ServerConfig, parse_servers

FAKE_SERVER = textwrap.dedent(
    '''
    import json, sys

    TOOLS = [
        {
            "name": "echo",
            "description": "echo the text back",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "write_note",
            "description": "append a note",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
            "annotations": {"readOnlyHint": False},
        },
    ]

    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if "id" not in msg:
            continue
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = msg.get("params", {})
            result = {
                "content": [{"type": "text", "text": "ok:" + params["name"]}],
                "isError": False,
            }
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    '''
)


class FakeClient:
    def __init__(self, tools, fail: bool = False) -> None:
        self.tools = tools
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def list_tools(self):
        if self.fail:
            raise MCPError("unreachable")
        return self.tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"content": [{"type": "text", "text": f"ok:{name}"}], "text": f"ok:{name}", "is_error": False}

    def close(self):
        self.closed = True


TOOLS = [
    {"name": "echo", "description": "echo", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
    {"name": "write_note", "description": "note", "inputSchema": {"type": "object"}},
]


def test_stdio_client_handshake_and_call(tmp_path):
    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    client = MCPClient(command=[sys.executable, str(script)], timeout=10)
    try:
        tools = client.list_tools()
        assert [tool["name"] for tool in tools] == ["echo", "write_note"]
        assert client.server_info["name"] == "fake"
        result = client.call_tool("echo", {"text": "hi"})
        assert result["text"] == "ok:echo"
        assert result["is_error"] is False
    finally:
        client.close()


def test_config_expands_env_and_detects_transport(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN", "secret")
    payload = {
        "servers": {
            "remote": {"url": "https://example.test/mcp", "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}},
            "local": {"type": "local", "command": ["python", "server.py"]},
            "missing": {"headers": {"Authorization": "Bearer ${NOPE}"}},
        }
    }
    servers = {server.name: server for server in parse_servers(payload)}
    assert servers["remote"].type == "remote"
    assert servers["remote"].headers["Authorization"] == "Bearer secret"
    assert servers["local"].type == "local"
    assert servers["local"].command == ["python", "server.py"]
    assert "Authorization" not in servers["missing"].headers


def test_catalog_isolates_a_dead_server():
    alive = FakeClient(TOOLS)
    dead = FakeClient([], fail=True)
    catalog = MCPCatalog(
        [ServerConfig("alive", "local"), ServerConfig("dead", "local")],
        client_factory=lambda server: alive if server.name == "alive" else dead,
    )
    tools = catalog.refresh()
    assert [tool.name for tool in tools] == ["echo", "write_note"]
    assert "unreachable" in catalog.errors()["dead"]
    assert catalog.servers()[1]["connected"] is False


def test_registry_bridge_applies_read_write_gate():
    catalog = MCPCatalog([ServerConfig("srv", "local")], client_factory=lambda _: FakeClient(TOOLS))
    registry = ToolRegistry()
    assert register_catalog_tools(registry, catalog) == 2
    assert {item["name"] for item in registry.list()} == {"mcp.srv.echo", "mcp.srv.write_note"}

    context = ToolContext(kb_id="", run_id="r", permissions={"mcp:srv"})
    result = registry.invoke("mcp.srv.echo", {"text": "hi"}, context)
    assert result.ok and result.data["text"] == "ok:echo"

    with pytest.raises(PermissionDenied):
        registry.invoke("mcp.srv.write_note", {"text": "x"}, context)

    approved = ToolContext(kb_id="", run_id="r", permissions={"mcp:srv:write"}, approval_token="t")
    assert registry.invoke("mcp.srv.write_note", {"text": "x"}, approved).ok


def test_catalog_call_rejects_unknown_server():
    catalog = MCPCatalog([], client_factory=lambda _: FakeClient([]))
    with pytest.raises(MCPError):
        catalog.call("nope", "echo", {})


def test_skills_are_read_from_disk(tmp_path, monkeypatch):
    from app.agent.skills import load_skills
    from app.core.config import get_settings

    card = tmp_path / "review" / "SKILL.md"
    card.parent.mkdir()
    card.write_text("---\nname: review\ndescription: review code\n---\n\nSteps here.\n", encoding="utf-8")

    monkeypatch.setenv("NEXUS_SKILLS_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        cards = load_skills(str(tmp_path))
        assert cards[0].name == "review"
        assert cards[0].description == "review code"
        assert cards[0].body == "Steps here."

        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        listing = client.get("/api/skills").json()["items"]
        assert listing[0]["name"] == "review"
        assert client.get("/api/skills/review").json()["body"] == "Steps here."
        assert client.get("/api/skills/missing").status_code == 404
    finally:
        get_settings.cache_clear()
        monkeypatch.delenv("NEXUS_SKILLS_DIR", raising=False)


def test_mcp_routes_report_the_catalogue(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.routes import extensions
    from app.main import app

    catalog = MCPCatalog([ServerConfig("srv", "local")], client_factory=lambda _: FakeClient(TOOLS))
    monkeypatch.setattr(extensions, "get_catalog", lambda: catalog)

    client = TestClient(app)
    tools = client.get("/api/mcp/tools").json()
    assert {item["name"] for item in tools["items"]} == {"echo", "write_note"}
    assert tools["items"][0]["permission"].startswith("mcp:srv")

    called = client.post("/api/mcp/call", json={"server": "srv", "tool": "echo", "arguments": {"text": "hi"}})
    assert called.status_code == 200
    assert called.json()["text"] == "ok:echo"

    denied = client.post("/api/mcp/call", json={"server": "srv", "tool": "write_note", "arguments": {}})
    assert denied.status_code == 403

    allowed = client.post(
        "/api/mcp/call",
        json={"server": "srv", "tool": "write_note", "arguments": {}, "approval_token": "t"},
    )
    assert allowed.status_code == 200
    assert client.post("/api/mcp/call", json={"server": "x", "tool": "y"}).status_code == 404

def test_read_only_inference_prefers_annotations():
    from app.mcp.catalog import infer_read_only

    assert infer_read_only("list_pages", "", {"readOnlyHint": True}) is True
    assert infer_read_only("close_page", "", {"readOnlyHint": False}) is False
    assert infer_read_only("search_procedures", "search", {}) is True
    assert infer_read_only("delete_document", "delete a document", {}) is False
    assert infer_read_only("mystery", "does something", {}) is True