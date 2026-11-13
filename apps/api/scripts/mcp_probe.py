"""List what an MCP server exposes, then call one harmless tool.

    python scripts/mcp_probe.py path/to/mcp.servers.json [server] [tool] [json-args]

Useful when wiring a new server: it prints the catalogue exactly as the agent
sees it (name, side effect, schema keys) and fails loudly if the handshake breaks.
"""

from __future__ import annotations

import json
import sys

from app.mcp.catalog import MCPCatalog
from app.mcp.config import load_servers


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: mcp_probe.py <config.json> [server] [tool] [json-args]")
        return 2
    servers = load_servers(sys.argv[1], default_timeout=60.0)
    catalog = MCPCatalog(servers)
    try:
        for server in catalog.servers():
            print(f"server {server['name']} ({server['type']})")
        tools = catalog.refresh()
        for tool in tools:
            keys = ",".join((tool.input_schema.get("properties") or {}).keys())
            effect = "read" if tool.read_only else "write"
            print(f"  {tool.server}.{tool.name} [{effect}] ({keys})")
        print(f"tools={len(tools)} errors={catalog.errors()}")
        if len(sys.argv) >= 4:
            args = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
            result = catalog.call(sys.argv[2], sys.argv[3], args)
            print(f"call ok={not result['is_error']} text={result['text'][:400]}")
    finally:
        catalog.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
