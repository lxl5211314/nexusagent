"""Turn remote MCP servers into registered tools.

Tools are discovered, not compiled in: a server that is down only loses its own
entries, and `refresh()` re-reads the catalogue. Every discovered tool enters the
same registry as `kb.search`, so permission checks and the read/write gate apply
without a special case.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agent.tools import ToolContext, ToolRegistry, ToolSpec
from app.mcp.client import MCPClient, MCPError
from app.mcp.config import ServerConfig, load_servers

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPToolInfo:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool


WRITE_VERBS = (
    "create",
    "delete",
    "update",
    "write",
    "set_",
    "annotate",
    "insert",
    "remove",
    "rename",
    "upload",
    "push",
    "comment",
    "bookmark",
    "goto",
    "click",
    "type",
    "fill",
    "navigate",
)
READ_VERBS = ("get", "list", "read", "search", "find", "inspect", "query", "describe", "current", "snapshot")


def infer_read_only(name: str, description: str, annotations: dict) -> bool:
    """Annotations win; otherwise guess from the verb, defaulting to read-only.

    Servers in the wild often omit `readOnlyHint`, so a name-based guess keeps
    the catalogue usable while the approval gate stays in front of obvious
    mutations.
    """
    if "readOnlyHint" in annotations:
        return bool(annotations["readOnlyHint"])
    if annotations.get("destructiveHint"):
        return False
    lowered = f"{name} {description}".lower()
    if any(word in lowered for word in WRITE_VERBS):
        return False
    if any(lowered.startswith(word) or f" {word}" in lowered[:24] for word in READ_VERBS):
        return True
    return True


class MCPCatalog:
    def __init__(
        self,
        servers: list[ServerConfig],
        client_factory: Callable[[ServerConfig], Any] | None = None,
    ) -> None:
        self._servers = servers
        self._factory = client_factory or _default_client
        self._clients: dict[str, Any] = {}
        self._tools: list[MCPToolInfo] = []
        self._errors: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------
    def _client(self, server: ServerConfig) -> Any:
        client = self._clients.get(server.name)
        if client is None:
            client = self._factory(server)
            self._clients[server.name] = client
        return client

    def refresh(self) -> list[MCPToolInfo]:
        tools: list[MCPToolInfo] = []
        self._errors = {}
        for server in self._servers:
            try:
                for raw in self._client(server).list_tools():
                    annotations = raw.get("annotations") or {}
                    tools.append(
                        MCPToolInfo(
                            server=server.name,
                            name=str(raw.get("name", "")),
                            description=str(raw.get("description", "") or ""),
                            input_schema=raw.get("inputSchema") or {"type": "object"},
                            read_only=infer_read_only(
                                str(raw.get("name", "")),
                                str(raw.get("description", "") or ""),
                                annotations,
                            ),
                        )
                    )
            except (MCPError, OSError, ValueError) as exc:
                log.warning("mcp server %s unavailable: %s", server.name, exc)
                self._errors[server.name] = str(exc)
                self._drop(server.name)
        self._tools = tools
        return tools

    def _drop(self, name: str) -> None:
        client = self._clients.pop(name, None)
        if client is not None:
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001 - a dead server must not break refresh
                log.debug("mcp close failed for %s: %s", name, exc)

    def close(self) -> None:
        for name in list(self._clients):
            self._drop(name)

    # -- queries -----------------------------------------------------------
    def tools(self) -> list[MCPToolInfo]:
        if not self._tools and self._servers:
            self.refresh()
        return list(self._tools)

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def servers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": server.name,
                "type": server.type,
                "connected": server.name in self._clients,
                "error": self._errors.get(server.name, ""),
            }
            for server in self._servers
        ]

    def call(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        config = next((item for item in self._servers if item.name == server), None)
        if config is None:
            raise MCPError(f"unknown MCP server: {server}")
        return self._client(config).call_tool(tool, arguments)


def _default_client(server: ServerConfig) -> MCPClient:
    return MCPClient(
        command=server.command,
        url=server.url,
        env=server.env,
        headers=server.headers,
        timeout=server.timeout,
    )


def register_catalog_tools(registry: ToolRegistry, catalog: MCPCatalog) -> int:
    """Register every discovered tool, skipping names that already exist."""
    existing = {item["name"] for item in registry.list()}
    added = 0
    for tool in catalog.tools():
        name = f"mcp.{tool.server}.{tool.name}"
        if name in existing:
            continue
        registry.register(_to_spec(catalog, tool, name))
        added += 1
    return added


def _to_spec(catalog: MCPCatalog, tool: MCPToolInfo, name: str) -> ToolSpec:
    def handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        result = catalog.call(tool.server, tool.name, args)
        if result["is_error"]:
            raise RuntimeError(result["text"][:500] or "MCP tool returned an error")
        return {"text": result["text"], "content": result["content"]}

    return ToolSpec(
        name=name,
        description=tool.description or f"{tool.name} from {tool.server}",
        input_schema=tool.input_schema,
        output_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        permission=f"mcp:{tool.server}" if tool.read_only else f"mcp:{tool.server}:write",
        side_effect="read" if tool.read_only else "write",
        handler=handler,
        timeout_s=60.0,
    )


_catalog: MCPCatalog | None = None


def get_catalog() -> MCPCatalog:
    global _catalog
    if _catalog is None:
        from app.core.config import get_settings

        settings = get_settings()
        _catalog = MCPCatalog(load_servers(settings.mcp_config, settings.mcp_timeout_s))
    return _catalog


def reset_catalog(catalog: MCPCatalog | None = None) -> None:
    global _catalog
    if _catalog is not None:
        _catalog.close()
    _catalog = catalog


_loaded = False


def ensure_loaded() -> int:
    """Register MCP tools once per process. A failing server is skipped, not fatal."""
    global _loaded
    if _loaded:
        return 0
    try:
        catalog = get_catalog()
        if not catalog.servers():
            # Configuration may appear later (a test, a restarted process); do not
            # latch the flag on an empty config or the tools never get registered.
            return 0
        from app.agent.tools import registry

        added = register_catalog_tools(registry, catalog)
        _loaded = True
        return added
    except Exception as exc:  # noqa: BLE001 - extensions must not break the agent
        log.warning("mcp discovery skipped: %s", exc)
        return 0


def reset_loaded_flag() -> None:
    global _loaded
    _loaded = False
