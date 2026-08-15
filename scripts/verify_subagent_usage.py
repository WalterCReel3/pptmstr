#!/usr/bin/env python3
"""
Four questions a sub-agent card rests on, none answerable by reading.

`verify_subagents.py` established that sub-agents surface at all and carry an
agent_id on their hooks. It spawns one, and never looks at token usage. The card
design in planning/2026-08-13-a-card-is-an-agent.md needs three further facts, and
2026-08-13-detail-swaps-to-a-deliverable.md a fourth:

  1. Does the CLI populate `usage` on a sub-agent's AssistantMessage? Every
     per-sub-agent spend figure rides on this. test_driver.py proves only the
     routing, using a synthetic message whose usage the test supplies -- if the
     wire leaves it empty, a sub-agent card shows a permanent $0.00.
  2. Are two Agent/Task PreToolUse hooks for one turn dispatched concurrently, or
     is each spawn serialised to completion? driver.py joins tool_use_id to
     agent_id through a single slot, so concurrent dispatch means one sub-agent is
     billed another's tokens. Interleaved hook order is the whole answer.
  3. Does SubagentStopHookInput still carry `last_assistant_message`? driver.py
     reads it and guards on truthiness, so a dropped field loses a sub-agent's
     answer in silence. The installed SDK's TypedDict does not declare it.
  4. Is that answer *already* in the sub-agent's own Transcript by the time the
     hook hands it over? The deliverable is deliberately not appended there on the
     grounds that `_assistant` has already routed it, and if that is wrong CONTEXT
     shows an empty pane for a sub-agent that answered. Question 1's counts cannot
     settle it: they count messages, and a message carrying only a ToolUseBlock
     carries usage too. This needs text blocks, and the handed-over string checked
     against them.

Everything is auto-allowed: an observation run, not a gate test. Two sub-agents are
requested in one turn deliberately -- one would answer question 1 and neither of
the others.

Usage:  .venv/bin/python scripts/verify_subagent_usage.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import Counter, defaultdict
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
    SystemMessage,
    TextBlock,
)

PROMPT = (
    "Call the Task tool TWICE IN THE SAME MESSAGE, so both subagents run at once. "
    "Do not wait for the first before launching the second. "
    "Both must use subagent_type 'general-purpose'. "
    "Give the first this prompt: 'Read pptmstr/log.py and reply with its line count.' "
    "Give the second this prompt: 'Read pptmstr/theme.py and reply with its line count.' "
    "Report both answers when they return."
)

START = time.monotonic()

# Hook events in arrival order with a timestamp. The ORDER is the finding for
# question 2: PreToolUse(Agent) x2 before any SubagentStart means the CLI dispatched
# them concurrently and driver.py's single-slot join loses one.
events: list[dict[str, Any]] = []

# usage per parent_tool_use_id. None is the root. Question 1 is answered by whether
# any non-None key accumulates a non-zero input/output count.
usage_by_parent: dict[Any, Counter] = defaultdict(Counter)
messages_by_parent: Counter = Counter()

# Question 4: the text each parent_tool_use_id actually delivered, and how many
# messages carried any. usage_by_parent cannot answer this -- every AssistantMessage
# carries usage, including one whose whole content is a ToolUseBlock, so a count of
# messages is consistent with a sub-agent that said nothing at all. The decision that
# rests on it (whether a sub-agent's deliverable is already in its own Transcript, and
# so must not be appended a second time) needs text blocks, not messages.
text_by_parent: dict[Any, list[str]] = defaultdict(list)
text_blocks_by_parent: Counter = Counter()

observed: dict[str, Any] = {
    "subagent_stop_keys": set(),
    "subagent_start_keys": set(),
    "last_assistant_message_seen": False,
    "agent_transcript_paths": [],
    # Every answer handed over on a stop hook, compared against the stream at
    # report time rather than here: the sub-agent's last message is not guaranteed
    # to have been drained by the moment its hook fires.
    "stop_answers": [],
}


def _stamp(name: str, data: dict[str, Any]) -> None:
    entry: dict[str, Any] = {"at": round(time.monotonic() - START, 3), "hook": name}
    for key, value in data.items():
        if key in ("tool_input", "cwd", "transcript_path", "permission_mode"):
            continue
        if key == "last_assistant_message":
            entry[key] = f"<{len(str(value))} chars>" if value else repr(value)
            continue
        entry[key] = value
    events.append(entry)


async def main() -> int:
    async def pre_tool_use(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        name = data.get("tool_name")
        if name in ("Agent", "Task"):
            # The second callback parameter is the one driver.py ignores. If the CLI
            # fills it on SubagentStart it is a better join key than the adjacency
            # heuristic, so it is recorded on both sides.
            _stamp("PreToolUse", {**data, "callback_tool_use_id": tool_use_id})
        return {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
        }

    async def subagent_start(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        observed["subagent_start_keys"].update(data.keys())
        _stamp("SubagentStart", {**data, "callback_tool_use_id": tool_use_id})
        return {}

    async def subagent_stop(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        observed["subagent_stop_keys"].update(data.keys())
        if data.get("last_assistant_message"):
            observed["last_assistant_message_seen"] = True
        observed["stop_answers"].append(str(data.get("last_assistant_message") or ""))
        if data.get("agent_transcript_path"):
            observed["agent_transcript_paths"].append(data["agent_transcript_path"])
        _stamp("SubagentStop", {**data, "callback_tool_use_id": tool_use_id})
        return {}

    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        permission_mode="dontAsk",
        max_turns=16,
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre_tool_use], timeout=600)],
            "SubagentStart": [HookMatcher(hooks=[subagent_start], timeout=600)],
            "SubagentStop": [HookMatcher(hooks=[subagent_stop], timeout=600)],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(PROMPT)
        async for message in _drain(client):
            if isinstance(message, AssistantMessage):
                parent = message.parent_tool_use_id
                messages_by_parent[parent] += 1
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_by_parent[parent].append(block.text)
                        text_blocks_by_parent[parent] += 1
                # Which spawns shared one assistant message. Without this, serialised
                # hook order is ambiguous: it reads the same whether the CLI
                # serialises two blocks from one message or the model simply took two
                # turns, and only the former refutes the cross-attribution defect.
                spawn_ids = [
                    b.id for b in message.content if getattr(b, "name", None) in ("Agent", "Task")
                ]
                if spawn_ids:
                    observed.setdefault("spawn_blocks_per_message", []).append(spawn_ids)
                if message.usage:
                    u = message.usage
                    for field in (
                        "input_tokens",
                        "output_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    ):
                        value = u.get(field) if isinstance(u, dict) else getattr(u, field, None)
                        if value:
                            usage_by_parent[parent][field] += value
                    usage_by_parent[parent]["messages_with_usage"] += 1
            elif isinstance(message, SystemMessage):
                if message.subtype == "init":
                    observed["available_agents"] = message.data.get("agents")
            elif isinstance(message, ResultMessage):
                observed["result_error"] = message.is_error
                observed["total_cost_usd"] = getattr(message, "total_cost_usd", None)

    report()
    return 0


async def _drain(client: object, grace: float = 25.0):
    """
    Yield until the stream goes quiet. See verify_subagents.py for why the parent's
    ResultMessage is the wrong stopping point.
    """
    stream = client.receive_messages()
    while True:
        try:
            message = await asyncio.wait_for(stream.__anext__(), timeout=grace)
        except (TimeoutError, StopAsyncIteration):
            return
        yield message


def report() -> None:
    print("\n=== Q2: hook order (concurrent dispatch?) ===")
    for entry in events:
        bits = [f"{entry['at']:>7.3f}", entry["hook"]]
        for key in ("tool_name", "agent_id", "agent_type", "tool_use_id", "callback_tool_use_id"):
            if entry.get(key):
                bits.append(f"{key}={entry[key]}")
        print("  " + "  ".join(str(b) for b in bits))

    grouping = observed.get("spawn_blocks_per_message", [])
    print(f"\n  spawn tool_use blocks grouped by assistant message: {grouping}")
    batched = any(len(group) >= 2 for group in grouping)
    if not batched:
        print(
            "  -> the model issued each spawn in its OWN message. Serialised hook "
            "order proves nothing here; the concurrent case was never presented."
        )

    spawns = [e for e in events if e["hook"] == "PreToolUse"]
    first_start = next((i for i, e in enumerate(events) if e["hook"] == "SubagentStart"), None)
    if len(spawns) >= 2 and first_start is not None and batched:
        before = sum(1 for e in events[:first_start] if e["hook"] == "PreToolUse")
        verdict = "CONCURRENT" if before >= 2 else "SERIALISED"
        print(
            f"\n  -> {before} Agent PreToolUse hook(s) fired before the first "
            f"SubagentStart: {verdict}"
        )
        if verdict == "CONCURRENT":
            print(
                "     driver.py's _last_spawn_tool_use_id is overwritten -- one "
                "sub-agent binds the wrong tool_use_id and the other binds none."
            )
    else:
        print("\n  -> inconclusive: the model did not launch two sub-agents in one turn.")

    print("\n=== Q1: usage per parent_tool_use_id ===")
    if not usage_by_parent:
        print("  no usage on any message at all")
    for parent, counts in usage_by_parent.items():
        label = "ROOT" if parent is None else f"subagent(parent={parent})"
        print(f"  {label}: {dict(counts)}")
    print(f"  messages seen per parent: {dict(messages_by_parent)}")
    sub_keys = [k for k in usage_by_parent if k is not None]
    if not sub_keys:
        print(
            "  -> NO sub-agent message carried usage. Per-sub-agent spend is not "
            "available from the wire; a card's spend slot would read $0.00."
        )
    else:
        print("  -> sub-agent usage IS populated. Per-node spend is real data.")

    print("\n=== Q3: SubagentStop payload ===")
    print(f"  SubagentStart keys: {sorted(observed['subagent_start_keys'])}")
    print(f"  SubagentStop  keys: {sorted(observed['subagent_stop_keys'])}")
    print(
        f"  last_assistant_message non-empty at least once: "
        f"{observed['last_assistant_message_seen']}"
    )
    if not observed["last_assistant_message_seen"]:
        print(
            "  -> driver.py:621 reads a field the wire does not fill. Sub-agent "
            "results are lost silently, because :622 guards on truthiness."
        )
    print(f"  agent_transcript_path values: {observed['agent_transcript_paths']}")

    print("\n=== Q4: is a sub-agent's answer already in its own transcript? ===")
    sub_text = {k: "".join(v) for k, v in text_by_parent.items() if k is not None}
    for parent, text in sub_text.items():
        print(
            f"  subagent(parent={parent}): {text_blocks_by_parent[parent]} text "
            f"block(s), {len(text)} chars"
        )
    if not sub_text:
        print("  no sub-agent message carried a TextBlock at all.")
        print(
            "  -> every routed message was a tool call, so driver.py's _assistant put "
            "nothing\n     in that node's Transcript. last_assistant_message is the "
            "only copy of the\n     answer, and the deliverable MUST also be appended "
            "to the transcript or\n     CONTEXT shows an empty pane for a sub-agent "
            "that answered."
        )
    for answer in observed["stop_answers"]:
        if not answer:
            continue
        holders = [p for p, text in sub_text.items() if answer.strip() in text]
        print(f"\n  last_assistant_message ({len(answer)} chars) is in the stream: {bool(holders)}")
        if holders:
            print(
                "  -> already routed into that node's Transcript. Appending the "
                "deliverable\n     as well would print the answer twice in CONTEXT."
            )
        else:
            print(
                "  -> the stream never carried this text. The transcript does not hold "
                "the\n     answer, and refusing the append loses it."
            )

    print("\n=== run ===")
    print(f"  error={observed.get('result_error')} " f"cost={observed.get('total_cost_usd')}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
