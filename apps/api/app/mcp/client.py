"""A small MCP client: JSON-RPC over stdio or streamable HTTP.

MCP servers expose `tools/list` and `tools/call`. That is all the agent needs to
load a catalogue at runtime instead of hard-coding every integration: connect
lazily, ask what the server can do, and translate each tool into the same
contract the in-process tools use (name, JSON schema, read/write).
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"


class MCPError(RuntimeError):
    pass


class _StdioTransport:
    def __init__(self, command: list[str], env: dict[str, str] | None = None) -> None:
        if not command:
            raise MCPError("empty command")
        resolved = shutil.which(command[0]) or command[0]
        self._command = [resolved, *command[1:]]
        self._env = env
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()

    def start(self) -> None:
        if self._proc:
            return
        import os

        env = dict(os.environ)
        env.update(self._env or {})
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            shell=False,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def send(self, message: dict[str, Any]) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def recv(self, timeout: float) -> dict[str, Any]:
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty as exc:
            raise MCPError(f"timeout waiting for {self._command[0]}") from exc
        if line is None:
            raise MCPError(f"{self._command[0]} closed the stream")
        line = line.strip()
        if not line:
            return {}
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return {}

    def close(self) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None


class _HTTPTransport:
    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = url
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        }
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)
        self._session_id: str = ""

    def start(self) -> None:
        pass

    def send(self, message: dict[str, Any]) -> None:
        raise MCPError("HTTP transport is request/response only")

    def recv(self, timeout: float) -> dict[str, Any]:
        raise MCPError("HTTP transport is request/response only")

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = self._client.post(self._url, json=message, headers=headers)
        if response.status_code >= 400:
            raise MCPError(f"HTTP {response.status_code}: {response.text[:200]}")
        sid = response.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        ctype = response.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    payload = json.loads(line[5:].strip())
                    if payload.get("id") == message.get("id"):
                        return payload
            raise MCPError("no response event in SSE stream")
        return response.json()

    def close(self) -> None:
        self._client.close()


class MCPClient:
    def __init__(
        self,
        command: list[str] | None = None,
        url: str = "",
        env: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        transport: Any = None,
    ) -> None:
        if transport is not None:
            self._transport = transport
        elif url:
            self._transport = _HTTPTransport(url, headers=headers, timeout=timeout)
        else:
            self._transport = _StdioTransport(command or [], env=env)
        self._timeout = timeout
        self._next_id = 0
        self._started = False
        self.server_info: dict[str, Any] = {}

    def start(self) -> None:
        if self._started:
            return
        self._transport.start()
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "nexusagent", "version": "0.3.0"},
            },
        )
        self.server_info = result.get("serverInfo", {})
        self._notify("notifications/initialized", {})
        self._started = True

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if isinstance(self._transport, _HTTPTransport):
            try:
                self._transport.request(message)
            except MCPError:
                pass
        else:
            self._transport.send(message)

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        if isinstance(self._transport, _HTTPTransport):
            payload = self._transport.request(message)
        else:
            self._transport.send(message)
            deadline = time.monotonic() + self._timeout
            payload = {}
            while time.monotonic() < deadline:
                candidate = self._transport.recv(max(0.1, deadline - time.monotonic()))
                if not candidate:
                    continue
                if candidate.get("id") == self._next_id:
                    payload = candidate
                    break
            if not payload:
                raise MCPError(f"timeout on {method}")
        if "error" in payload:
            err = payload["error"]
            raise MCPError(f"{method}: {err.get('message') or err}")
        return payload.get("result", {})

    def list_tools(self) -> list[dict[str, Any]]:
        self.start()
        tools: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor") or ""
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.start()
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        text = "\n".join(
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        )
        return {
            "content": result.get("content", []),
            "text": text,
            "is_error": bool(result.get("isError")),
        }

    def close(self) -> None:
        self._transport.close()
