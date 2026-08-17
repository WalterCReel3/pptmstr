#!/usr/bin/env python3
"""
Does the closing half of the tool bracket actually fire, and for whom?

`_pre_tool_use` brackets its liveness bookkeeping around the gate decision, which
the CLI is blocked on -- so the bracket comes down as the tool *begins*, and a
sub-agent doing one long call is silent for the whole of it. Moving the closing
half into PostToolUse only works if the closing hook is reliable, and three
things decide that. None is answerable by reading the SDK, because the events are
emitted by the CLI:

  1. Does PostToolUse carry `agent_id`, and a `tool_use_id` that matches the
     PreToolUse for the same call? Attribution is the whole mechanism.
  2. Does a tool that ERRORS produce PostToolUse, PostToolUseFailure, or both?
  3. Does a call the hook DENIES produce either? This application denies by
     default, so a deny with no closing hook is a permanent false RUNNING that
     never gives its capacity slot back.

The sub-agent is asked for one of each: a Read that succeeds, a Read of a path
that does not exist, and a Bash call this script's own PreToolUse denies.

Usage:  .venv/bin/python scripts/verify_post_tool_use.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_agent_sdk import (  # noqa: E402
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
)

MISSING = "/home/wreel/Source/pptmstr/no_such_file_probe_xyz.txt"

PROMPT = (
    "Call the Task tool right now and wait for its result. Launch one subagent "
    "with subagent_type 'general-purpose' and exactly this prompt:\n"
    "'Do these three things in order, one tool call each, and do not stop early "
    "even if one fails -- every one of them is being measured. "
    f"(1) Read the file {ROOT}/pptmstr/log.py. "
    f"(2) Read the file {MISSING} -- it does not exist and the Read is expected "
    "to fail, that is the point, keep going afterwards. "
    "(3) Run the Bash command: echo probe-denied-call. "
    "Then reply with one line saying which of the three succeeded.'\n"
    "When the subagent returns, report its answer verbatim."
)

events: list[dict[str, Any]] = []


def record(name: str, data: dict[str, Any]) -> None:
    events.append(
        {
            "hook": name,
            "agent_id": data.get("agent_id"),
            "tool": data.get("tool_name"),
            "tool_use_id": data.get("tool_use_id"),
            "error": str(data.get("error", ""))[:80] or None,
            "is_interrupt": data.get("is_interrupt"),
            "response": str(data.get("tool_response", ""))[:80] or None,
        }
    )


async def main() -> int:
    async def pre_tool_use(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("PreToolUse", data)
        tool_input = data.get("tool_input") or {}
        denied = data.get("tool_name") == "Bash" and "probe-denied-call" in str(
            tool_input.get("command", "")
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny" if denied else "allow",
                "permissionDecisionReason": "probe deny" if denied else "probe allow",
            }
        }

    async def post_tool_use(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("PostToolUse", data)
        return {}

    async def post_tool_use_failure(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("PostToolUseFailure", data)
        return {}

    async def subagent_start(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("SubagentStart", data)
        return {}

    async def subagent_stop(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("SubagentStop", data)
        return {}

    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        cwd=str(ROOT),
        permission_mode="dontAsk",
        max_turns=20,
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre_tool_use], timeout=600)],
            "PostToolUse": [HookMatcher(hooks=[post_tool_use], timeout=600)],
            "PostToolUseFailure": [HookMatcher(hooks=[post_tool_use_failure], timeout=600)],
            "SubagentStart": [HookMatcher(hooks=[subagent_start], timeout=600)],
            "SubagentStop": [HookMatcher(hooks=[subagent_stop], timeout=600)],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(PROMPT)
        async for message in _drain(client):
            if isinstance(message, ResultMessage):
                events.append({"hook": "(ResultMessage)", "error": str(message.is_error)})

    report()
    return 0


async def _drain(client: Any, grace: float = 30.0):
    """
    Yield until the stream goes quiet, not merely until the first result.

    The closing hooks are the thing being measured and some of them can land after
    the parent's ResultMessage; stopping there would report "no PostToolUse" when
    the truth is that we stopped listening too early.
    """
    stream = client.receive_messages()
    while True:
        try:
            message = await asyncio.wait_for(stream.__anext__(), timeout=grace)
        except (TimeoutError, StopAsyncIteration):
            return
        yield message


def report() -> None:
    print("\n=== events, in order ===")
    for entry in events:
        bits = " ".join(f"{k}={v}" for k, v in entry.items() if v is not None and k != "hook")
        print(f"  {entry['hook']:<20} {bits}")

    sub = [e for e in events if e.get("agent_id")]
    pre = {e["tool_use_id"]: e for e in sub if e["hook"] == "PreToolUse"}
    post = {e["tool_use_id"] for e in sub if e["hook"] == "PostToolUse"}
    fail = {e["tool_use_id"] for e in sub if e["hook"] == "PostToolUseFailure"}

    print("\n=== bracket, per sub-agent tool call ===")
    for tool_use_id, entry in pre.items():
        closers = []
        if tool_use_id in post:
            closers.append("PostToolUse")
        if tool_use_id in fail:
            closers.append("PostToolUseFailure")
        print(f"  {entry['tool']:<10} {tool_use_id} closed by: {closers or 'NOTHING'}")

    print("\n=== verdict ===")
    post_with_agent = [e for e in events if e["hook"] == "PostToolUse" and e.get("agent_id")]
    print(f"  PostToolUse carrying agent_id: {len(post_with_agent)}")
    unmatched = [t for t in pre if t not in post and t not in fail]
    print(f"  sub-agent PreToolUse with NO closing hook: {len(unmatched)} -> {unmatched}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
