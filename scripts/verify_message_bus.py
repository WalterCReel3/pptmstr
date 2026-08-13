#!/usr/bin/env python3
"""
Can a message bus we own know who is talking?

Design §2.7 makes an in-process MCP server the default transport for inter-agent
concerns: `post_concern(to, subject, body)` backed by our store, so a message
becomes a store object and is reviewable in flight. Reading the SDK undermines the
premise. The CLI delivers an SDK MCP call as a `mcp_message` control request
carrying only `server_name` and the raw JSON-RPC body (`_internal/query.py:499`),
and the body carries only `name` and `arguments` (`:679`). Compare `can_use_tool`
on the same switch (`:429`), which is handed `tool_use_id` *and* `agent_id`.

So the handler is caller-anonymous by construction: `post_concern` has no
trustworthy `from`, and `read_inbox()` cannot know whose inbox to read.

`PreToolUse` does know. It sees the MCP call with `agent_id` attached and it can
rewrite the arguments via `updatedInput` (§5.3). If the rewritten arguments are
what the handler actually receives, the gate can stamp an authenticated sender
onto every message and the anonymity is repaired -- by the subsystem this project
already treats as load-bearing, which is a better answer than a side channel.

That "if" is the whole probe. `updatedInput` is verified for built-in tools; that
it survives the extra hop out to the CLI and back into an in-process MCP server is
a different claim, and only the first is provable by reading.

  1. Does an SDK MCP tool call reach PreToolUse at all, and with `agent_id` set
     when the caller is a sub-agent rather than the root?
  2. Does `updatedInput` from PreToolUse reach the MCP handler? This decides
     whether §2.7's bus can authenticate a sender.
  3. What does the handler see unaided -- confirming the source read end to end.
  4. Is `SendMessage` in the advertised tool list without agent teams, for the
     root and for a sub-agent?

Everything is auto-allowed; this is an observation run, not a gate test.

Usage:  .venv/bin/python scripts/verify_message_bus.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_agent_sdk import (  # noqa: E402
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    create_sdk_mcp_server,
    tool,
)

STAMP = "_stamped_by"

PROMPT = (
    "Do these two things in order, and do not do anything else.\n"
    "1. Call the tool mcp__probe__ping with note set to exactly 'from-root'.\n"
    "2. Call the Task tool with subagent_type 'messenger' and this prompt: "
    '\'Call the tool mcp__probe__ping with note set to exactly "from-sub", then '
    "reply with the single word done.'\n"
    "Then reply with the single word finished."
)

observed: dict[str, Any] = {
    "handler_calls": [],  # what the MCP handler actually received
    "hook_calls": [],  # what PreToolUse saw for our tool
    "root_tools": [],
    "init_seen": 0,
    "result_error": None,
}


@tool("ping", "Record a note for the probe.", {"note": str})
async def ping(args: dict[str, Any]) -> dict[str, Any]:
    # The whole question: is `args` exactly what the model wrote, or what the
    # gate rewrote it to?
    observed["handler_calls"].append(dict(args))
    return {"content": [{"type": "text", "text": "recorded"}]}


async def main() -> int:
    async def pre_tool_use(
        data: dict[str, Any], tool_use_id: str | None, ctx: Any
    ) -> dict[str, Any]:
        name = data.get("tool_name", "")
        if not name.endswith("__ping"):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }

        agent_id = data.get("agent_id")
        observed["hook_calls"].append(
            {
                "tool_name": name,
                "agent_id": agent_id,
                "session_id": data.get("session_id"),
                "tool_use_id": tool_use_id,
                "tool_input": data.get("tool_input"),
                "keys": sorted(data.keys()),
            }
        )

        # Stand in for the authenticated sender a real bus would stamp.
        stamped = dict(data.get("tool_input") or {})
        stamped[STAMP] = agent_id or "root"
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": stamped,
            }
        }

    server = create_sdk_mcp_server("probe", "1.0.0", [ping])

    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        permission_mode="dontAsk",
        max_turns=16,
        mcp_servers={"probe": server},
        agents={
            "messenger": AgentDefinition(
                description="Calls the probe ping tool. Used only by this probe.",
                prompt=(
                    "You are a probe worker. Do exactly what you are asked and " "nothing more."
                ),
                tools=["mcp__probe__ping", "SendMessage"],
            )
        },
        hooks={"PreToolUse": [HookMatcher(hooks=[pre_tool_use], timeout=600)]},
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(PROMPT)
        async for message in _drain(client):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                observed["init_seen"] += 1
                if not observed["root_tools"]:
                    observed["root_tools"] = list(message.data.get("tools") or [])
            if isinstance(message, ResultMessage) and message.is_error:
                observed["result_error"] = message.result

    report()
    return 0


async def _drain(client: ClaudeSDKClient, grace: float = 25.0) -> Any:
    """Read until the stream goes quiet -- sub-agents outlive the root's result."""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    done = object()

    async def pump() -> None:
        try:
            async for message in client.receive_messages():
                await queue.put(message)
        finally:
            await queue.put(done)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=grace)
            except TimeoutError:
                return
            if item is done:
                return
            yield item
    finally:
        task.cancel()


def report() -> None:
    print("\n=== PreToolUse saw ===")
    for call in observed["hook_calls"]:
        print(json.dumps(call, indent=2, default=str))
    if not observed["hook_calls"]:
        print("(nothing -- MCP calls may not reach PreToolUse at all)")

    print("\n=== the MCP handler received ===")
    for call in observed["handler_calls"]:
        print(json.dumps(call, indent=2, default=str))
    if not observed["handler_calls"]:
        print("(nothing -- the tool was never called)")

    tools = observed["root_tools"]
    print("\n=== advertised tools (root init) ===")
    print(f"{len(tools)} tools")
    for name in tools:
        if "essage" in name or name.startswith("mcp__probe"):
            print(f"  {name}")

    print("\n=== verdict ===")

    hooks = observed["hook_calls"]
    handled = observed["handler_calls"]

    if not hooks:
        print("Q1 MCP calls do NOT reach PreToolUse -- an MCP bus is UNGATEABLE.")
    else:
        with_agent = [h for h in hooks if h["agent_id"]]
        print(f"Q1 MCP calls reach PreToolUse ({len(hooks)} seen).")
        verdict = (
            "sub-agent senders are identifiable"
            if with_agent
            else "ROOT ONLY: no sub-agent call carried an agent_id"
        )
        print(f"   agent_id present on {len(with_agent)}/{len(hooks)} -- {verdict}")

    stamped = [c for c in handled if STAMP in c]
    if not handled:
        print("Q2 inconclusive -- the handler never ran.")
    elif stamped:
        print(
            f"Q2 updatedInput REACHES the MCP handler ({len(stamped)}/{len(handled)})."
            "\n   The gate can authenticate a sender. §2.7's bus is viable as written."
        )
        for call in stamped:
            print(f"   {call.get('note')!r} stamped {call[STAMP]!r}")
    else:
        print(
            "Q2 updatedInput does NOT reach the MCP handler."
            "\n   The bus cannot authenticate a sender through the gate; §2.7 needs"
            "\n   a different mechanism or a different transport."
        )

    if handled:
        keys = sorted({k for c in handled for k in c})
        print(f"Q3 handler argument keys, all calls: {keys}")
        print("   (no session_id/agent_id/tool_use_id => source read confirmed)")

    send = [t for t in tools if "SendMessage" in t]
    print(f"Q4 SendMessage advertised to the root: {bool(send)} {send}")

    if observed["result_error"]:
        print(f"\n(run reported an error: {observed['result_error']})")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
