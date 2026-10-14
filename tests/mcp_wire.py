"""
The bus server driven over the wire the CLI uses, for tests.

A session's bus is the ``mcp.server.Server`` that ``create_sdk_mcp_server`` builds,
and nothing in the product calls it directly: the CLI sends JSON-RPC over the
control channel and claude_agent_sdk hands each message to an ``SdkMcpBridge``,
which runs the server over mcp's in-memory transport. These helpers send their
messages to that same bridge, so a test sees what an agent is told -- argument
validation, the result's shape, an error result -- by the path that tells it.

Not the server's handler table: how a lowlevel ``Server`` stores its handlers is
mcp's internal and differs between its majors (``request_handlers`` keyed by
request type on 1.x; constructor callbacks on 2.x), and claude-agent-sdk allows
both (``mcp<3``), so a fresh install can resolve either. ``SdkMcpBridge`` is the
SDK's own internal too, but it is the one the product goes through, it is the
place the SDK keeps its mcp-major differences, and a rename of it fails these
tests at import rather than passing them against a path no session takes.

What comes back is the wire, not mcp's models of it: the JSON the CLI reads is the
protocol's and is the same under both majors, while the pydantic models are not
(``isError`` on 1.x is ``is_error`` on 2.x, ``inputSchema`` is ``input_schema``).

Each call is one connection: ``initialize``, the call, close. A real session keeps
one connection for its life; nothing these tests assert depends on that, and a
connection per call keeps a helper usable from ``asyncio.run`` and from the
application's own loop alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from claude_agent_sdk._internal._mcp_compat import MCP_MAJOR
from claude_agent_sdk._internal.sdk_mcp_bridge import SdkMcpBridge
from mcp.server import Server

_INITIALIZE = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "pptmstr-tests", "version": "0"},
}


@dataclass(frozen=True, slots=True)
class Answer:
    """
    A ``tools/call`` result as the calling agent reads it.
    """

    is_error: bool
    content: tuple[dict[str, Any], ...]

    @property
    def text(self) -> str:
        """
        The text a bus tool answers with; every one answers with one text block.
        """
        assert len(self.content) == 1 and self.content[0].get("type") == "text", self.content
        return str(self.content[0]["text"])


async def _request(server: Server, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    The ``result`` of one request on a fresh connection; a JSON-RPC error fails.
    """
    wire = SdkMcpBridge("pptmstr", server)
    try:
        opened = await wire.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _INITIALIZE}
        )
        assert opened is not None and "result" in opened, opened
        await wire.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        answer = await wire.handle({"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
    finally:
        await wire.aclose()
    assert answer is not None and "result" in answer, f"{method} was answered {answer}"
    result: dict[str, Any] = answer["result"]
    return result


async def list_tools(server: Server) -> dict[str, dict[str, Any]]:
    """
    Each tool's ``inputSchema`` as ``tools/list`` hands it to the CLI, by name.
    """
    listed = await _request(server, "tools/list", {})
    return {tool["name"]: tool["inputSchema"] for tool in listed["tools"]}


async def call_tool(server: Server, name: str, arguments: dict[str, Any]) -> Answer:
    """
    What the calling agent is told, error results included: asserting on
    ``is_error`` is the caller's.
    """
    result = await _request(server, "tools/call", {"name": name, "arguments": arguments})
    return Answer(is_error=bool(result.get("isError", False)), content=tuple(result["content"]))


def versions_line() -> str:
    """
    The test-run header's line for the two packages the bus is served through.
    """
    return (
        f"bus: mcp {version('mcp')} (read as major {MCP_MAJOR} by the SDK), "
        f"claude-agent-sdk {version('claude-agent-sdk')}"
    )
