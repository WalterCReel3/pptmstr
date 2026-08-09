#!/usr/bin/env python3
"""
How does sub-agent activity actually reach us?

Design §2.5 flags this as unresolved and says it "materially affects goal #3 for
anything below the root" -- if sub-agents only surface as complete messages, their
reasoning arrives in turn-sized lumps rather than token by token, and the tree pane
has to say so rather than pretending otherwise.

Three things need answering before the sub-agent tree can be designed, and none of
them is answerable by reading the SDK:

  1. Do SubagentStart / SubagentStop fire, and what identity do they carry?
  2. Do sub-agent tool calls reach PreToolUse with an agent_id attached? That is
     what decides whether an approval can be attributed to the right node.
  3. Do StreamEvents arrive with parent_tool_use_id set, or is sub-agent streaming
     invisible?

Everything is auto-allowed here: this is an observation run, not a gate test.

Usage:  .venv/bin/python scripts/verify_subagents.py
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    ToolUseBlock,
    UserMessage,
)

PROMPT = (
    "You MUST call the Task tool right now, and wait for its result. Launch one "
    "subagent with subagent_type 'general-purpose' and this prompt: "
    "'Read the file pptmstr/log.py and reply with the number of lines in it.' "
    "The subagent must use the Read tool -- that is what this run is testing. "
    "When it returns, report its answer."
)

observed: dict[str, Any] = {
    "hooks": [],
    "message_kinds": Counter(),
    "stream_events_total": 0,
    "stream_events_with_parent": 0,
    "stream_delta_types": Counter(),
    "assistant_with_parent": 0,
    "assistant_without_parent": 0,
    "tool_calls": [],
    "system_subtypes": Counter(),
}


def record_hook(name: str, data: dict[str, Any]) -> None:
    entry = {"hook": name}
    for key, value in data.items():
        if key in ("tool_input", "cwd", "transcript_path", "permission_mode"):
            continue
        entry[key] = value
    observed["hooks"].append(entry)


async def main() -> int:
    async def pre_tool_use(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record_hook("PreToolUse", data)
        return {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
        }

    async def subagent_start(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record_hook("SubagentStart", data)
        return {}

    async def subagent_stop(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record_hook("SubagentStop", data)
        return {}

    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        permission_mode="dontAsk",
        include_partial_messages=True,
        max_turns=12,
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre_tool_use], timeout=600)],
            "SubagentStart": [HookMatcher(hooks=[subagent_start], timeout=600)],
            "SubagentStop": [HookMatcher(hooks=[subagent_stop], timeout=600)],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(PROMPT)
        async for message in _drain(client):
            kind = type(message).__name__
            observed["message_kinds"][kind] += 1

            if isinstance(message, StreamEvent):
                observed["stream_events_total"] += 1
                if message.parent_tool_use_id:
                    observed["stream_events_with_parent"] += 1
                event = message.event
                if event.get("type") == "content_block_delta":
                    observed["stream_delta_types"][(event.get("delta") or {}).get("type")] += 1

            elif isinstance(message, AssistantMessage):
                if message.parent_tool_use_id:
                    observed["assistant_with_parent"] += 1
                else:
                    observed["assistant_without_parent"] += 1
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        observed["tool_calls"].append(
                            {
                                "name": block.name,
                                "id": block.id,
                                "parent": message.parent_tool_use_id,
                            }
                        )

            elif isinstance(message, UserMessage):
                if message.parent_tool_use_id:
                    observed["assistant_with_parent"] += 0  # counted separately below

            elif isinstance(message, SystemMessage):
                observed["system_subtypes"][message.subtype] += 1
                if message.subtype == "init":
                    observed["available_tools"] = message.data.get("tools")
                    observed["available_agents"] = message.data.get("agents")

            elif isinstance(message, ResultMessage):
                observed["result_error"] = message.is_error
                observed["terminal_reason"] = message.terminal_reason

            if type(message).__name__.startswith("Task"):
                observed.setdefault("task_messages", []).append(
                    {
                        "kind": type(message).__name__,
                        "data": str(getattr(message, "data", ""))[:200],
                    }
                )

    report()
    return 0


async def _drain(client: object, grace: float = 25.0):
    """
    Yield messages until the stream goes quiet, not merely until the first result.

    receive_response() stops at the parent's ResultMessage. A sub-agent launched as
    a background task outlives that, so stopping there would report "no sub-agent
    activity" when the truth is "we stopped listening too early".
    """
    import asyncio as _asyncio

    stream = client.receive_messages()
    while True:
        try:
            message = await _asyncio.wait_for(stream.__anext__(), timeout=grace)
        except (TimeoutError, StopAsyncIteration):
            return
        yield message


def report() -> None:
    print("\n=== hooks fired, in order ===")
    for entry in observed["hooks"]:
        bits = [f"{k}={v}" for k, v in entry.items() if v is not None and k != "hook"]
        print(f"  {entry['hook']:<14} {' '.join(bits)}")

    print("\n=== message kinds ===")
    for kind, count in observed["message_kinds"].most_common():
        print(f"  {kind:<20} {count}")

    print("\n=== system subtypes ===")
    for subtype, count in observed["system_subtypes"].most_common():
        print(f"  {subtype:<20} {count}")

    print("\n=== availability ===")
    print(f"  tools:  {observed.get('available_tools')}")
    print(f"  agents: {observed.get('available_agents')}")

    print("\n=== task messages ===")
    for entry in observed.get("task_messages", []):
        print(f"  {entry['kind']:<24} {entry['data']}")

    print("\n=== tool calls ===")
    for call in observed["tool_calls"]:
        print(f"  {call['name']:<12} id={call['id'][:16]} parent={call['parent']}")

    print("\n=== attribution ===")
    print(f"  AssistantMessage with parent_tool_use_id:    {observed['assistant_with_parent']}")
    print(f"  AssistantMessage without parent_tool_use_id: {observed['assistant_without_parent']}")
    print(f"  StreamEvent total:                           {observed['stream_events_total']}")
    print(f"  StreamEvent with parent_tool_use_id:         {observed['stream_events_with_parent']}")
    print(f"  stream delta types: {dict(observed['stream_delta_types'])}")

    print("\n=== verdict ===")
    sub_hooks = [h for h in observed["hooks"] if h["hook"] != "PreToolUse"]
    gated = [h for h in observed["hooks"] if h["hook"] == "PreToolUse" and h["agent_id"]]
    if sub_hooks:
        print("  SubagentStart/Stop DO fire -- use them as the tree's spawn/finish source.")
    else:
        print("  SubagentStart/Stop did NOT fire -- rebuild the tree from parent_tool_use_id.")
    if gated:
        print("  Sub-agent tool calls DO carry agent_id -- approvals attribute to the right node.")
    else:
        print("  Sub-agent tool calls carried NO agent_id -- approvals cannot be attributed.")
    if observed["stream_events_with_parent"]:
        print("  Sub-agent activity DOES stream -- goal #3 holds below the root.")
    else:
        print("  No StreamEvent carried parent_tool_use_id -- sub-agent output arrives")
        print("  as complete messages only. The pane must say so rather than imply live.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
