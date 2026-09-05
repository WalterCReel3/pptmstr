#!/usr/bin/env python3
"""
Does a ROOT-session tool call get a closing hook, and can it be paired with its opening one?

`driver.AgentSession._post_tool_use` returns `{}` when `agent_id` is absent, and
`_pre_tool_use` returns straight to the gate without opening a bracket in the same
case -- both deliberately, on the stated basis that *"agent_id is present only when
the call comes from inside a sub-agent, and it is the only reliable attribution when
several run in parallel."* So pptmstr does not bracket root calls, and reading the
source cannot tell you whether that is because the CLI sends nothing or because
pptmstr drops what it sends. Those are different facts with different consequences,
and only a run separates them.

**Why it matters now.** The git-derived divergence sensor proposed in
`planning/2026-09-03-a-dangerously-autonomous-mode.md` §2 samples the working tree
when the set of open write-capable calls goes empty, and attributes the delta to the
node that just closed. A first dangerously-autonomous run is a solo session -- all
root calls. If the CLI dispatches no closing hook for those, the trigger never fires
and the design collapses to periodic-and-unattributed. If it dispatches one that
pptmstr merely ignores, the sensor can hook it directly and the design stands, but it
cannot reuse `_subagent_in_flight` and must keep its own bracket.

Three outcomes, not two, and the probe must separate them:

  NOT DISPATCHED       no closing hook for a root call. The trigger has no signal.
  DISPATCHED, ANONYMOUS  a closing hook arrives with no `agent_id`. pptmstr's handler
                       drops it at its first line; the sensor can still use it, and
                       absence of `agent_id` IS the root node's identity -- NodeId is
                       `(session_id, None)` -- rather than a missing attribution.
  DISPATCHED, ATTRIBUTED  it carries an `agent_id`, which would contradict the comment
                       in `_pre_tool_use` and mean root calls have been bracketable
                       all along.

Pairing is measured separately from arrival, because a closing hook whose
`tool_use_id` does not match its opening one is useless to a window-close trigger even
though it exists.

Arm SUB is the control. The claim under test is about root calls *specifically*, and
"no closing hook arrived" is only evidence if closing hooks arrive at all in the same
run -- otherwise a broken harness reads exactly like the finding.

Three calls per arm: a Read that succeeds, a Bash that succeeds, and a Bash that exits
non-zero. The failing one is included because `_post_tool_use`'s own docstring records
that a failed tool fires PostToolUseFailure and not PostToolUse, so an arm watching
only the latter would under-report by a third.

Usage:  .venv/bin/python scripts/verify_root_tool_bracket.py
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

WORK = (
    "Do these three things in order, one tool call each, and do not stop early even "
    "if one fails -- every one of them is being measured, and a failure is expected.\n"
    f"  (1) Read the file {ROOT}/pptmstr/log.py\n"
    "  (2) Run the Bash command: echo probe-root-ok\n"
    "  (3) Run the Bash command: sh -c 'exit 3'\n"
    "Then reply with one line naming which succeeded."
)

ROOT_PROMPT = WORK

SUB_PROMPT = (
    "Call the Task tool right now and wait for its result. Launch one subagent with "
    "subagent_type 'general-purpose' and exactly this prompt:\n'" + WORK + "'\n"
    "When the subagent returns, report its answer verbatim."
)


class Arm:
    def __init__(self, name: str) -> None:
        self.name = name
        self.events: list[dict[str, Any]] = []

    def add(self, hook: str, data: dict[str, Any]) -> None:
        self.events.append(
            {
                "hook": hook,
                "tool": data.get("tool_name"),
                "tool_use_id": data.get("tool_use_id"),
                # Distinguished from "" so "absent" and "present but empty" do not merge.
                "agent_id": data.get("agent_id", "<<absent>>"),
            }
        )

    def calls(self, *, root: bool) -> list[dict[str, Any]]:
        """
        Events belonging to root-session calls, or to sub-agent calls.

        The parent's own `Task` call in arm SUB is a root call and is excluded from
        both: it is the spawn, not the work, and counting it would put a root call in
        the arm that exists to measure sub-agent ones.
        """
        out = []
        for entry in self.events:
            is_root = entry["agent_id"] in ("<<absent>>", None, "")
            if is_root != root:
                continue
            if entry["tool"] in ("Task", "Agent"):
                continue
            out.append(entry)
        return out


async def run_arm(name: str, prompt: str) -> Arm:
    arm = Arm(name)

    async def pre(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        arm.add("PreToolUse", data)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "probe allow",
            }
        }

    async def post(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        arm.add("PostToolUse", data)
        return {}

    async def post_fail(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        arm.add("PostToolUseFailure", data)
        return {}

    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        cwd=str(ROOT),
        permission_mode="dontAsk",
        max_turns=20,
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre], timeout=600)],
            "PostToolUse": [HookMatcher(hooks=[post], timeout=600)],
            "PostToolUseFailure": [HookMatcher(hooks=[post_fail], timeout=600)],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        stream = client.receive_messages()
        while True:
            try:
                message = await asyncio.wait_for(stream.__anext__(), timeout=90.0)
            except (TimeoutError, StopAsyncIteration):
                break
            # Do not stop at the first ResultMessage: closing hooks can land after it,
            # and stopping there would report "no closing hook" for one that was simply
            # not waited for. Drain to quiet instead.
            if isinstance(message, ResultMessage):
                continue
    return arm


def describe(arm: Arm, *, root: bool) -> dict[str, Any]:
    kind = "ROOT-session" if root else "SUB-agent"
    calls = arm.calls(root=root)
    opens = {e["tool_use_id"]: e for e in calls if e["hook"] == "PreToolUse"}
    closes = [e for e in calls if e["hook"] != "PreToolUse"]

    print(f"\n  {kind} calls in arm {arm.name}:")
    if not calls:
        print("    (none)")
    for entry in calls:
        print(
            f"    {entry['hook']:<21} tool={str(entry['tool']):<8} "
            f"agent_id={entry['agent_id']!r} id={entry['tool_use_id']}"
        )

    closed_ids = {e["tool_use_id"] for e in closes}
    unpaired = [i for i in opens if i not in closed_ids]
    attributed = [e for e in closes if e["agent_id"] not in ("<<absent>>", None, "")]

    return {
        "opens": len(opens),
        "closes": len(closes),
        "unpaired": unpaired,
        "attributed_closes": len(attributed),
    }


def verdict(root_stats: dict[str, Any], sub_stats: dict[str, Any]) -> None:
    print("\n=== verdict ===")

    if sub_stats["closes"] == 0:
        print("  HARNESS SUSPECT -- no closing hook arrived for a SUB-agent call either.")
        print("  The control arm did not fire, so a root-side absence proves nothing.")
        return

    if root_stats["opens"] == 0:
        print("  NOT EXERCISED -- no root-session tool call was made; nothing to measure.")
        return

    if root_stats["closes"] == 0:
        outcome = "NOT DISPATCHED"
        meaning = (
            "the CLI sends no closing hook for a root call. The git sensor's\n"
            "  window-close trigger has no signal in a solo session and the design\n"
            "  collapses to periodic-and-unattributed."
        )
    elif root_stats["attributed_closes"] == 0:
        outcome = "DISPATCHED, ANONYMOUS"
        meaning = (
            "the closing hook arrives with no agent_id. pptmstr drops it at\n"
            "  _post_tool_use's first line -- deliberately -- but the git sensor can hook\n"
            "  it directly. Absence of agent_id IS the root node's identity, since NodeId\n"
            "  is (session_id, None). The sensor must keep its own bracket rather than\n"
            "  reusing _subagent_in_flight."
        )
    else:
        outcome = "DISPATCHED, ATTRIBUTED"
        meaning = (
            "the closing hook carries an agent_id for a ROOT call, which\n"
            "  contradicts _pre_tool_use's comment that agent_id is present only for\n"
            "  sub-agent calls. That comment would need correcting."
        )

    print(f"  Root-session closing hooks: {outcome}")
    print(f"  {meaning}")
    print(
        f"\n  pairing: {root_stats['opens']} root PreToolUse, {root_stats['closes']} closing, "
        f"unpaired by tool_use_id: {root_stats['unpaired'] or 'none'}"
    )
    if root_stats["unpaired"]:
        print("  A closing hook that cannot be paired is useless to a window-close")
        print("  trigger even though it exists. This is the finding, not the count.")
    print(
        f"  control (SUB-agent): {sub_stats['opens']} open, {sub_stats['closes']} closing, "
        f"{sub_stats['attributed_closes']} carrying agent_id"
    )


async def main() -> int:
    root_arm = await run_arm("ROOT / no sub-agent", ROOT_PROMPT)
    print(f"\n=== arm {root_arm.name} ===")
    root_stats = describe(root_arm, root=True)

    sub_arm = await run_arm("SUB / one sub-agent", SUB_PROMPT)
    print(f"\n=== arm {sub_arm.name} ===")
    sub_stats = describe(sub_arm, root=False)

    verdict(root_stats, sub_stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
