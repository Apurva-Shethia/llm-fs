"""
MCP client for connecting LangGraph agents to one or more MCP servers.

Manages stdio subprocess connections and provides a synchronous API for
tool calls, resource discovery, and resource reads.

Design note — per-call connections
-----------------------------------
anyio's TaskGroup (used internally by stdio_client) must be entered *and* exited
within the **same** event loop / asyncio.run() invocation.  Keeping a session
alive across multiple asyncio.run() calls is therefore not safe.

Each public sync method on MCPClientManager opens a fresh stdio_client context,
performs exactly the requested operation, and tears the connection down — all
within a single asyncio.run().  This adds a small subprocess-startup overhead
per call (~50–150 ms) but is reliable and crash-free.

For production use where latency matters, the agent should be converted to a
fully async call-graph so that a single long-lived connection can be used per
workflow run (the async helpers are still available for that).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

load_dotenv()

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_FILESYSTEM_SERVER = str(PROJECT_ROOT / "filesystem_mcp_server.py")
DEFAULT_WEB_SEARCH_SERVER = str(PROJECT_ROOT / "web_search_mcp_server.py")


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Optional[Dict[str, str]] = None


def _parse_tool_result(result: Any) -> Dict[str, Any]:
    """Normalize MCP CallToolResult into a plain dict."""
    if result.isError:
        text = ""
        if result.content:
            text = getattr(result.content[0], "text", str(result.content[0]))
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"success": False, "error": text or "MCP tool error"}

    if not result.content:
        return {"success": True, "result": None}

    text = getattr(result.content[0], "text", "")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"success": True, "result": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"success": True, "content": text}


@asynccontextmanager
async def _open_session(config: MCPServerConfig):
    """Async context manager that yields an initialized MCP ClientSession.

    The entire stdio_client + ClientSession lifecycle is contained within this
    single async context, so it is safe to use inside one asyncio.run() call.
    """
    server_params = StdioServerParameters(
        command=config.command,
        args=config.args,
        env={**os.environ, **(config.env or {})},
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            logger.debug("MCP session opened for server: %s", config.name)
            yield session


class MCPClientSession:
    """Per-call MCP client for a single server config.

    Each method opens its own connection, performs the operation, and closes
    the connection — all within the same coroutine / event loop.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config

    async def list_tools(self) -> List[Dict[str, Any]]:
        async with _open_session(self.config) as session:
            response = await session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                }
                for tool in response.tools
            ]

    async def list_resources(self) -> List[Dict[str, Any]]:
        async with _open_session(self.config) as session:
            response = await session.list_resources()
            return [
                {"uri": resource.uri, "name": resource.name, "description": resource.description}
                for resource in response.resources
            ]

    async def read_resource(self, uri: str) -> str:
        async with _open_session(self.config) as session:
            response = await session.read_resource(uri)
            if not response.contents:
                return ""
            return getattr(response.contents[0], "text", str(response.contents[0]))

    async def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with _open_session(self.config) as session:
            result = await session.call_tool(name, arguments or {})
            return _parse_tool_result(result)

    # Kept for API compatibility — no-op because connections are per-call now.
    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass


class MCPClientManager:
    """
    Manages multiple MCP server configs and routes tool calls by server name.

    Provides a synchronous API via asyncio.run for use in LangGraph nodes.
    Each call opens a fresh connection to the MCP server subprocess.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, MCPClientSession] = {}

    def register_server(self, config: MCPServerConfig) -> None:
        self._sessions[config.name] = MCPClientSession(config)

    @staticmethod
    def _run(coro: Any) -> Any:
        """Run a coroutine synchronously, safe regardless of caller context."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — straightforward asyncio.run()
            return asyncio.run(coro)
        # Called from within an existing event loop (e.g. async LangGraph node).
        # Offload to a dedicated thread to avoid nested-loop deadlock.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        server: str = "filesystem",
    ) -> Dict[str, Any]:
        session = self._sessions.get(server)
        if session is None:
            return {"success": False, "error": f"MCP server not registered: {server}"}
        return self._run(session.call_tool(tool_name, arguments))

    def list_tools(self, server: str = "filesystem") -> List[Dict[str, Any]]:
        session = self._sessions.get(server)
        if session is None:
            return []
        return self._run(session.list_tools())

    def list_resources(self, server: str = "filesystem") -> List[Dict[str, Any]]:
        session = self._sessions.get(server)
        if session is None:
            return []
        return self._run(session.list_resources())

    def read_resource(self, uri: str, server: str = "filesystem") -> str:
        session = self._sessions.get(server)
        if session is None:
            return ""
        return self._run(session.read_resource(uri))

    def list_all_tools(self) -> Dict[str, List[Dict[str, Any]]]:
        return {name: self.list_tools(name) for name in self._sessions}

    def disconnect_all(self) -> None:
        """No-op — connections are per-call and self-closing."""
        pass


_manager: Optional[MCPClientManager] = None


def get_mcp_manager(
    enable_filesystem: bool = True,
    enable_web_search: bool = False,
) -> MCPClientManager:
    """Return a singleton MCP client manager with default server registrations."""
    global _manager
    if _manager is not None:
        return _manager

    _manager = MCPClientManager()
    python = sys.executable

    if enable_filesystem:
        _manager.register_server(
            MCPServerConfig(
                name="filesystem",
                command=python,
                args=[os.getenv("MCP_FILESYSTEM_SERVER", DEFAULT_FILESYSTEM_SERVER)],
            )
        )

    if enable_web_search:
        _manager.register_server(
            MCPServerConfig(
                name="web_search",
                command=python,
                args=[os.getenv("MCP_WEB_SEARCH_SERVER", DEFAULT_WEB_SEARCH_SERVER)],
            )
        )

    return _manager


def reset_mcp_manager() -> None:
    """Clear the singleton (connections are per-call, so no teardown needed)."""
    global _manager
    _manager = None
