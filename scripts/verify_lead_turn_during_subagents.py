#!/usr/bin/env python3
"""
Can a lead take a turn while it is purely waiting on sub-agents?

``driver.py:1461-1471`` reads the stream with ``receive_messages()`` on the stated
grounds that a sub-agent outlives the parent's ResultMessage, and ``send``
(``driver.py:1583-1600``) claims a prompt may be written to stdin while the loop
reads stdout. Both are about *our* side of the pipe. Whether the CLI dispatches a
user message that arrives while a background task is outstanding, or buffers it
until the task completes, is not answerable by reading either one.

The operator's observation -- leads sitting in ``THINKING`` during a fan-out --
cannot settle it, because that is the state the code produces either way:
``_result`` emits no state intent (``driver.py:527-532``) and ``send`` emits
THINKING *before* it calls ``query`` (``:1597-1600``).

**The observable is ordering, not state.** A sub-agent is parked in a 45s sleep.
Once it has started, a second prompt goes in asking for one distinctive word. The
question is whether that word comes back before SubagentStop or only after it.

  answered  -> root text carrying the canary arrives while the sub-agent is live
  buffered  -> it arrives only after SubagentStop, or not at all

Everything is auto-allowed: this is an observation run, not a gate test.

Usage:  .venv/bin/python scripts/verify_lead_turn_during_subagents.py
"""

from __future__ import annotations

import asyncio
import sys
import time
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
    ToolUseBlock,
)

CANARY = "BANANA"

# The sub-agent must outlive the root's turn by a wide, *deterministic* margin. A
# sleep does that at near-zero token cost; a real task would make the window a
# function of how fast the model happens to be.
SPAWN_PROMPT = (
    "Call the Task tool exactly once, right now, with subagent_type "
    "'general-purpose' and this prompt: 'Run the bash command `sleep 45` in the "
    "FOREGROUND and wait for it to finish -- do not use run_in_background, do not "
    "append an ampersand, do not poll. When it returns, reply with the word SLEPT "
    "and nothing else.' Launch it in the background and do NOT wait for its result "
    "-- end your turn as soon as the tool call is accepted. Say nothing else."
)

FOLLOWUP_PROMPT = (
    f"Reply with exactly the word {CANARY} and nothing else. "
    "Do not call any tool. Do not mention the subagent."
)

t0 = time.monotonic()
events: list[tuple[float, str, str]] = []


def mark(kind: str, detail: str = "") -> None:
    events.append((time.monotonic() - t0, kind, detail))


async def main() -> int:
    subagent_started = asyncio.Event()
    subagent_stopped = asyncio.Event()

    async def pre_tool_use(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        mark("PreToolUse", str(data.get("tool_name")))
        return {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
        }

    async def subagent_start(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        mark("SubagentStart", str(data.get("agent_type", "")))
        subagent_started.set()
        return {}

    async def subagent_stop(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        mark("SubagentStop", str(data.get("agent_id", "")))
        subagent_stopped.set()
        return {}

    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        permission_mode="dontAsk",
        max_turns=12,
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre_tool_use], timeout=600)],
            "SubagentStart": [HookMatcher(hooks=[subagent_start], timeout=600)],
            "SubagentStop": [HookMatcher(hooks=[subagent_stop], timeout=600)],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        mark("send", "spawn prompt")
        await client.query(SPAWN_PROMPT)

        async def followup() -> None:
            """
            Send the second prompt once the sub-agent is demonstrably live.

            Gated on SubagentStart rather than on a timer: a prompt sent before the
            spawn lands would be an ordinary queued turn and would prove nothing.
            The extra second is slack for the root's own ResultMessage, so the send
            lands in the window this run is about -- the lead purely waiting.
            """
            try:
                await asyncio.wait_for(subagent_started.wait(), timeout=120)
            except TimeoutError:
                mark("followup", "ABORTED: no SubagentStart within 120s")
                return
            await asyncio.sleep(1.0)
            if subagent_stopped.is_set():
                mark("followup", "ABORTED: sub-agent already stopped, no window")
                return
            mark("send", "followup prompt (sub-agent live)")
            await client.query(FOLLOWUP_PROMPT)

        pump = asyncio.ensure_future(followup())
        try:
            async for message in _drain(client, grace=90.0):
                parent = getattr(message, "parent_tool_use_id", None)
                whose = "sub" if parent else "root"

                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text = block.text.strip().replace("\n", " ")[:80]
                            if text:
                                mark(f"text/{whose}", text)
                        elif isinstance(block, ToolUseBlock):
                            mark(f"tool/{whose}", block.name)
                elif isinstance(message, ResultMessage):
                    mark(f"Result/{whose}", message.terminal_reason or "")
                elif isinstance(message, SystemMessage):
                    if message.subtype in ("task_started", "task_progress", "task_updated"):
                        what = str(message.data.get("description", ""))[:60]
                        mark(f"system/{message.subtype}", what)
        finally:
            pump.cancel()

    report()
    return 0


async def _drain(client: Any, grace: float):
    """
    Yield until the stream goes quiet, not until the first result.

    Same reason ``verify_subagents.py`` gives: a background sub-agent outlives the
    parent's ResultMessage, so stopping there would report the absence of exactly
    the traffic this run exists to observe.
    """
    stream = client.receive_messages()
    while True:
        try:
            message = await asyncio.wait_for(stream.__anext__(), timeout=grace)
        except (TimeoutError, StopAsyncIteration):
            return
        yield message


def report() -> None:
    print("\n=== timeline (seconds from connect) ===")
    for at, kind, detail in events:
        print(f"  {at:7.2f}  {kind:<22} {detail}")

    stop_at = next((at for at, k, _ in events if k == "SubagentStop"), None)
    canary_at = next(
        (at for at, k, d in events if k == "text/root" and CANARY in d),
        None,
    )

    print("\n=== verdict ===")
    if canary_at is None:
        print(f"  INCONCLUSIVE: the canary {CANARY!r} never came back on the root.")
        print("  Either the follow-up was dropped entirely or the run ended early.")
    elif stop_at is None:
        print("  INCONCLUSIVE: SubagentStop never fired, so there is no window to be inside of.")
    elif canary_at < stop_at:
        print(f"  ANSWERED: canary at {canary_at:.2f}s, SubagentStop at {stop_at:.2f}s.")
        print("  The lead took a turn while it was purely waiting on a sub-agent.")
    else:
        print(f"  BUFFERED: SubagentStop at {stop_at:.2f}s, canary at {canary_at:.2f}s.")
        print("  The prompt was held until the sub-agent finished.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
