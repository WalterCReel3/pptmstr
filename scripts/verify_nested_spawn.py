#!/usr/bin/env python3
"""
Can a sub-agent spawn, and does the cap see it if it does?

`driver._gate_tool_use` computes `spawn = tool_name in ("Agent", "Task") and not
agent_id`, so the at-cap deny below it never applies to a spawn issued *by* a
sub-agent. `planning/2026-08-14-a-role-runs-one-agent.md` records that exclusion as
deliberate -- *"Nested spawns are not counted... Widening `spawn` to cover them would
corrupt the join"* -- and `tests/test_gate.py::test_a_spawn_from_inside_a_subagent_is_not_counted`
pins it, on the justification that *"it still parks, so the operator remains the bound
on that branch."*

`planning/2026-09-03-a-dangerously-autonomous-mode.md` §8 deletes that justification
by auto-approving spawns, which promotes `subagent_cap` to the only volume control.
Two things nothing in this tree measures then decide whether that control works, and
they compose:

  1. Does the CLI grant `Task` to a sub-agent at all? `templates.Role.tools=None`
     means "inherit everything the session has", and the shipped `feature` template's
     `builder` role sets no tools -- but whether the CLI hands the tool over is a CLI
     behaviour, not a pptmstr one. **If it does not, the whole defect is unreachable
     in shipped configuration** and the cap work drops in priority.
  2. If it does: does `SubagentStart` fire for the nested agent?
     `_outstanding_subagents()` is `len(_live_subagents) + pending`, and
     `_live_subagents` is populated only in `_subagent_start`. If a nested agent never
     arrives there, then splitting the spawn flag so the cap counts nested spawns
     bounds the *burst* and not the *population* -- the admission is counted, the
     occupancy is not -- and §8's claim that the cap is the volume control stays false
     after the fix lands.

Four outcomes, and they want different work:

  NO NESTED TOOL      the sub-agent has no Task/Agent. Defect unreachable; close it.
  SPAWNS, COUNTED     nested spawn happens AND SubagentStart fires for it. Splitting
                      the flag makes the cap a real population bound. Do that work.
  SPAWNS, UNCOUNTED   nested spawn happens and no SubagentStart follows. Splitting the
                      flag is not sufficient; the cap needs its own counter, which is
                      the "separate counter" 08-14 said this wants.
  INDETERMINATE       the lead never spawned, or the sub-agent never tried. Measures
                      nothing -- reported as such rather than as an absence.

The lead is given a team whose `builder` mirrors the shipped role exactly: `tools=None`,
which is what `templates.Role.tool_list()` returns for it and what `driver._team()`
passes to `AgentDefinition`. Reproducing the shipped shape is the point; a probe that
grants tools explicitly would answer a question nobody asked.

Nothing here is denied. The hook allows everything and only observes, because the
question is what the CLI dispatches, not what pptmstr does with it.

Usage:  .venv/bin/python scripts/verify_nested_spawn.py
"""

from __future__ import annotations

import asyncio
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
)

SPAWN_TOOLS = ("Task", "Agent")

# The nested worker's prompt is quoted inside the builder's, which is quoted inside
# the lead's. Kept as one string so the nesting is legible rather than assembled.
NESTED_ASK = (
    "You are a builder. Do exactly this and nothing else.\n"
    "Try to launch ONE subagent of your own, using the Task tool, with "
    "subagent_type 'general-purpose' and the prompt 'Reply with the single word "
    "NESTED and stop.'\n"
    "If you do not have a Task tool available, say exactly: NO TASK TOOL "
    "AVAILABLE -- and then stop, do not attempt any other tool.\n"
    "Report either the subagent's reply or that sentence."
)

PROMPT = (
    "Call the Task tool right now and wait for its result. Launch one subagent with "
    "subagent_type 'builder' and exactly this prompt:\n"
    f"'{NESTED_ASK}'\n"
    "When it returns, report its answer verbatim."
)

TEAM = {
    "builder": AgentDefinition(
        description="Builds things. Mirrors the shipped feature template's builder.",
        prompt="You are a builder.",
        # None on purpose: `templates.Role.tool_list()` returns None for the shipped
        # `builder`, and driver._team() passes that through. Granting tools here would
        # measure a configuration nobody runs.
        tools=None,
    )
}

events: list[dict[str, Any]] = []


def record(hook: str, data: dict[str, Any]) -> None:
    events.append(
        {
            "hook": hook,
            "tool": data.get("tool_name"),
            "agent_id": data.get("agent_id", "<<absent>>"),
            "subagent_type": str((data.get("tool_input") or {}).get("subagent_type", "")) or None,
        }
    )


async def main() -> int:
    async def pre(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("PreToolUse", data)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "probe allow",
            }
        }

    async def started(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("SubagentStart", data)
        return {}

    async def stopped(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("SubagentStop", data)
        return {}

    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        cwd=str(ROOT),
        permission_mode="dontAsk",
        max_turns=30,
        agents=TEAM,
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre], timeout=600)],
            "SubagentStart": [HookMatcher(hooks=[started], timeout=600)],
            "SubagentStop": [HookMatcher(hooks=[stopped], timeout=600)],
        },
    )

    answer = ""
    async with ClaudeSDKClient(options=options) as client:
        await client.query(PROMPT)
        stream = client.receive_messages()
        while True:
            try:
                message = await asyncio.wait_for(stream.__anext__(), timeout=180.0)
            except (TimeoutError, StopAsyncIteration):
                break
            if isinstance(message, ResultMessage):
                answer = str(getattr(message, "result", ""))[:800]
                break

    report(answer)
    return 0


def report(answer: str) -> None:
    print("\n=== events, in order ===")
    for entry in events:
        bits = " ".join(f"{k}={v}" for k, v in entry.items() if v is not None and k != "hook")
        print(f"  {entry['hook']:<15} {bits}")

    spawn_calls = [e for e in events if e["hook"] == "PreToolUse" and e["tool"] in SPAWN_TOOLS]
    root_spawns = [e for e in spawn_calls if e["agent_id"] in ("<<absent>>", None, "")]
    nested_spawns = [e for e in spawn_calls if e["agent_id"] not in ("<<absent>>", None, "")]
    starts = [e for e in events if e["hook"] == "SubagentStart"]

    print("\n=== counts ===")
    print(f"  spawn calls from the ROOT (cap applies):        {len(root_spawns)}")
    print(f"  spawn calls from a SUB-AGENT (cap skipped):     {len(nested_spawns)}")
    print(f"  SubagentStart events:                           {len(starts)}")
    print(f"\n  lead's answer: {answer[:400]}")

    print("\n=== verdict ===")
    if not root_spawns:
        print("  INDETERMINATE -- the lead never spawned. Nothing about nesting is measured.")
        return

    if not nested_spawns:
        said_no_tool = "NO TASK TOOL AVAILABLE" in answer.upper()
        print("  NO NESTED TOOL -- the sub-agent issued no spawn call.")
        print(
            "  corroborated by the agent's own report: "
            + ("yes" if said_no_tool else "NO -- it may simply have declined")
        )
        if not said_no_tool:
            print("  Treat as INDETERMINATE rather than as absence: a model that chose")
            print("  not to call a tool it has looks identical here to one that lacks it.")
        else:
            print("  Defect 2 of the 09-04 record is unreachable in shipped configuration.")
            print("  The cap predicate still excludes nested spawns; nothing can issue one.")
        return

    # A nested spawn happened. The remaining question is whether it occupies a slot.
    print(f"  SPAWNS -- a sub-agent issued {len(nested_spawns)} spawn call(s), and")
    print("  driver.py's `and not agent_id` means the at-cap deny did not apply to them.")

    if len(starts) > len(root_spawns):
        print("\n  COUNTED -- SubagentStart fired more often than the root spawned, so a")
        print("  nested agent does reach `_live_subagents` and does occupy a slot.")
        print("  Splitting the spawn flag makes the cap a real population bound.")
    else:
        print("\n  UNCOUNTED -- SubagentStart did not fire for the nested agent.")
        print("  `_outstanding_subagents` counts `_live_subagents` plus pending, so a")
        print("  nested agent occupies no slot after admission. Splitting the flag would")
        print("  bound the burst and not the population, and the cap would still not be")
        print("  the volume control the mode's decision assumes. This wants the separate")
        print("  counter 2026-08-14 said it wants.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
