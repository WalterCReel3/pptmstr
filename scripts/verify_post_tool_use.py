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
  4. Does a call the hook allows *with `updatedInput`* -- an operator's edited
     approval, `_allow_with` in driver.py -- produce a closing hook that can be
     attributed? `_is_allow` reads it as an allow, so the bracket is left open for
     a PostToolUse that may never come, come without `agent_id`, or come under a
     different `tool_use_id`. Those are three different defects with three
     different fixes and only a run tells them apart.

The sub-agent is asked for one of each: a Read that succeeds, a Read of a path
that does not exist, a Bash call this script's own PreToolUse denies, and a Bash
echo whose command string this script's PreToolUse rewrites -- the rewritten text
appears in the tool result, so an ignored `updatedInput` is visible rather than
being mistaken for an honoured one.

SubagentStop is reported per agent as well. It is the other way a stuck bracket
gets cleared (`_subagent_stop` pops `_subagent_in_flight` outright), so a run
where the closing hook is missing but SubagentStop arrives is a different
situation from one where neither does.

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

# The edited-approval case. The sub-agent is told to echo the first string; the gate
# swaps the command for one echoing the second, so whichever appears in the tool
# result says whether the CLI honoured `updatedInput`.
EDIT_ASKED = "probe-edit-approve-original"
EDIT_SENT = "probe-edit-approve-rewritten"

PROMPT = (
    "Call the Task tool right now and wait for its result. Launch one subagent "
    "with subagent_type 'general-purpose' and exactly this prompt:\n"
    "'Do these four things in order, one tool call each, and do not stop early "
    "even if one fails -- every one of them is being measured. "
    f"(1) Read the file {ROOT}/pptmstr/log.py. "
    f"(2) Read the file {MISSING} -- it does not exist and the Read is expected "
    "to fail, that is the point, keep going afterwards. "
    "(3) Run the Bash command: echo probe-denied-call. "
    f"(4) Run the Bash command: echo {EDIT_ASKED} -- its output may differ from "
    "what you asked for, which is expected, do not retry it. "
    "Then reply with one line saying which of the four succeeded, quoting the "
    "exact output of step 4.'\n"
    "When the subagent returns, report its answer verbatim."
)

events: list[dict[str, Any]] = []

# tool_use_ids the gate answered with an allow carrying updatedInput, and the input it
# sent for each.
edited_calls: dict[str, dict[str, Any]] = {}

# Every closing hook's untruncated tool_response, kept alongside the id and tool it
# arrived under. A closing hook that comes in under a *different* tool_use_id can only
# be recognised by its payload, so the id alone is not enough to find it -- and the
# tool name is what keeps the parent's Task closer, which quotes the sub-agent's answer
# back and so contains the same markers, out of that search.
closers: list[dict[str, Any]] = []


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


def _note_closer(name: str, data: dict[str, Any]) -> None:
    closers.append(
        {
            "hook": name,
            "tool_use_id": data.get("tool_use_id"),
            "agent_id": data.get("agent_id"),
            "tool": data.get("tool_name"),
            "response": str(data.get("tool_response", "")) or str(data.get("error", "")),
        }
    )


async def main() -> int:
    async def pre_tool_use(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("PreToolUse", data)
        tool_input = data.get("tool_input") or {}
        command = str(tool_input.get("command", ""))
        is_bash = data.get("tool_name") == "Bash"
        if is_bash and "probe-denied-call" in command:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "probe deny",
                }
            }
        if is_bash and EDIT_ASKED in command:
            # The shape `_allow_with` produces for an operator-annotated approval: an
            # allow whose updatedInput replaces the whole argument mapping.
            updated = {**tool_input, "command": command.replace(EDIT_ASKED, EDIT_SENT)}
            edited_calls[str(data.get("tool_use_id", ""))] = {
                "agent_id": data.get("agent_id"),
                "updatedInput": updated,
            }
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "probe edited approval",
                    "updatedInput": updated,
                }
            }
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "probe allow",
            }
        }

    async def post_tool_use(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("PostToolUse", data)
        _note_closer("PostToolUse", data)
        return {}

    async def post_tool_use_failure(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("PostToolUseFailure", data)
        _note_closer("PostToolUseFailure", data)
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

    print("\n=== SubagentStop, per agent ===")
    started = list(dict.fromkeys(e["agent_id"] for e in events if e["hook"] == "SubagentStart"))
    stopped = {e["agent_id"] for e in events if e["hook"] == "SubagentStop"}
    for agent_id in started or sorted({str(e["agent_id"]) for e in sub}):
        print(f"  {agent_id} stopped: {'SubagentStop' if agent_id in stopped else 'NOTHING'}")

    print("\n=== verdict ===")
    post_with_agent = [e for e in events if e["hook"] == "PostToolUse" and e.get("agent_id")]
    print(f"  PostToolUse carrying agent_id: {len(post_with_agent)}")
    unmatched = [t for t in pre if t not in post and t not in fail]
    print(f"  sub-agent PreToolUse with NO closing hook: {len(unmatched)} -> {unmatched}")
    _report_edited_approval()


def _report_edited_approval() -> None:
    """
    Answer the edited-approval question outright, rather than leaving it to be
    reconstructed from the event list.

    The three candidates the planning record names are distinguished here, and they
    are not all findable by tool_use_id: a closing hook arriving under a *different*
    id is recognised only by the rewritten marker in its payload.
    """
    print("\n=== verdict: edited approval (allow + updatedInput) ===")
    if not edited_calls:
        print("  NOT EXERCISED: no Bash call carried the marker, nothing was edited")
        return

    for call_id, sent in edited_calls.items():
        print(f"  edit-approved call: tool_use_id={call_id} agent_id={sent['agent_id']}")
        print(f"  updatedInput sent:  {sent['updatedInput']}")

        by_id = [c for c in closers if c["tool_use_id"] == call_id]
        elsewhere = [
            c
            for c in closers
            if c["tool_use_id"] != call_id
            and c["tool"] == "Bash"
            and (EDIT_SENT in c["response"] or EDIT_ASKED in c["response"])
        ]

        if by_id:
            for entry in by_id:
                attribution = (
                    f"agent_id={entry['agent_id']}"
                    if entry["agent_id"]
                    else "NO agent_id (dropped at driver.py:1023)"
                )
                print(
                    f"  closing hook:       {entry['hook']} tool_use_id={call_id} -- {attribution}"
                )
        elif elsewhere:
            ids = sorted(str(c["tool_use_id"]) for c in elsewhere)
            print(f"  closing hook:       arrived under a DIFFERENT tool_use_id -> {ids}")
        else:
            print("  closing hook:       NONE arrived")

        seen = by_id + elsewhere
        if any(EDIT_SENT in c["response"] for c in seen):
            rewrite = "YES -- result carries the rewritten command"
        elif any(EDIT_ASKED in c["response"] for c in seen):
            rewrite = "NO -- result carries the ORIGINAL command, updatedInput was ignored"
        else:
            rewrite = "UNKNOWN -- no closing hook payload to read it off"
        print(f"  rewrite honoured:   {rewrite}")
        for entry in seen:
            print(f"  closer payload:     {entry['response'][:200]}")

        stopped = {e["agent_id"] for e in events if e["hook"] == "SubagentStop"}
        print(
            "  SubagentStop for that agent: "
            + (
                "arrived (clears the bracket regardless)"
                if sent["agent_id"] in stopped
                else "MISSING"
            )
        )

        if by_id and all(c["agent_id"] for c in by_id):
            finding = "closing hook arrives attributed -- edited approval does NOT lose its bracket"
        elif by_id:
            finding = "candidate 2: closing hook without agent_id"
        elif elsewhere:
            finding = "candidate 3: closing hook under a different tool_use_id"
        else:
            finding = "candidate 1: no closing hook at all"
        print(f"  finding:            {finding}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
