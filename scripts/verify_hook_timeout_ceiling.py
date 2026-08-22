#!/usr/bin/env python3
"""
Does the CLI honour ``HookMatcher(timeout=604800)``, or fall back to its default?

``planning/2026-08-22-an-approval-parked-overnight-is-not-a-wedged-host.md``
records the operator's decision that a parked approval must survive a weekend,
which means ``APPROVAL_TIMEOUT_S`` wants a value around 259200-604800s. The SDK
documents no maximum and enforces nothing itself -- enforcement lives inside the
bundled CLI binary, which cannot be read. That record names this probe P1.

**What is already settled, and is not re-run here.** ``scripts/verify_hook_timeout.py``
case ``long-timeout`` (75s block, ``timeout=6h``) completed with the tool run and
no error; the table is in ``orchestrator-design.md`` §5.2.1. A block of 75s is
already past the 60s default, so "no silent fallback to the default" is
established *at 6h*. What is not established is that a value two orders of
magnitude larger is treated the same way -- a binary that range-checks its input
would have had no opportunity to show it at 21600. This probe moves only that
one variable.

**What ONE run of this can settle:** that at ``timeout=604800`` the hook is not
aborted before 90s. That rules out a silent fallback to the 60s default, and to
any other ceiling below 90s, at exactly the magnitude decision 1 needs.

**What it cannot settle:** that 604800 is honoured in full. A silent clamp to
any value above 90s -- 6h, 24h, one hour -- produces an identical run. Only a
probe that blocks past the suspected cap can distinguish those, and it costs the
cap in wall clock to do it. It also says nothing about what happens on expiry;
that path is the one ``verify_hook_timeout.py`` case ``exceeds-short`` measured.

**The observable is the shell's own output, not the model's request for it.** A
``ToolUseBlock`` is the model *asking* for Bash, which is the event PreToolUse
fires on -- it appears whether or not the call survives the gate, so a verdict
resting on it would pass a run in which the hook was aborted and nothing ran.
This probe waits for a ``ToolResultBlock`` correlated to that request by
``tool_use_id`` and carrying the echoed token back. Nothing here reads the
model's text.

Two cancellation sources other than the CLI can reach the hook -- a second
firing overwriting the first's numbers, and this script's own deadline -- and
both are checked before the abort is credited to a CLI-imposed ceiling, because
that is the one verdict here that would block decision 1.

Print-only: nothing touches app state.

Costs one real turn against a trivial prompt, and takes ~100s of wall clock.
Usage:  .venv/bin/python scripts/verify_hook_timeout_ceiling.py
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
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    ResultMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

# One tool call and nothing else, so the hook fires exactly once. The echoed
# token is the probe's evidence of execution: it can only appear in a tool
# result if the shell actually ran.
ECHOED = "pptmstr-probe"
PROMPT = f"Run the bash command `echo {ECHOED}` and then reply with just the word done."

# The value decision 1 wants: seven days, in seconds.
TIMEOUT_S = 604800.0

# Past the 60s default by enough that a fallback cannot be mistaken for jitter.
BLOCK_S = 90.0

# Beyond BLOCK_S plus a turn's worth of model latency. Reaching it means the run
# hung somewhere other than the hook, which is INCONCLUSIVE rather than a cap.
DEADLINE_S = BLOCK_S + 150.0


def main() -> int:
    observed = asyncio.run(_run())
    print("\n=== observed ===")
    for key, value in observed.items():
        print(f"  {key}: {value}")
    print("\n=== verdict ===")
    _report(observed)
    return 0


async def _run() -> dict[str, Any]:
    observed: dict[str, Any] = {
        "timeout_s": TIMEOUT_S,
        "block_s": BLOCK_S,
        # A counter, not a flag: `observed` is one dict that every invocation
        # overwrites, so a second firing would silently replace the measurement
        # this probe exists to take. The verdict refuses to read a run with more
        # than one rather than reporting whichever fired last.
        "hook_fires": 0,
        "hook_completed": False,
        "hook_slept_s": None,
        "hook_cancelled_after_s": None,
        "bash_requested": False,
        "bash_executed": False,
        "result_is_error": None,
        "terminal_reason": None,
        "deadline_hit": False,
        "exception": None,
    }
    bash_use_ids: set[str] = set()

    async def gate(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        observed["hook_fires"] += 1
        started = time.monotonic()
        try:
            await asyncio.sleep(BLOCK_S)
        except asyncio.CancelledError:
            # An abort reaches us as a cancellation of this coroutine
            # (verify_hook_timeout.py, case exceeds-short), so the elapsed time
            # recorded here is the ceiling the binary actually applied.
            observed["hook_cancelled_after_s"] = round(time.monotonic() - started, 1)
            raise
        observed["hook_slept_s"] = round(time.monotonic() - started, 1)
        observed["hook_completed"] = True
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }

    options = ClaudeAgentOptions(
        model="claude-haiku-4-5-20251001",
        permission_mode="dontAsk",
        allowed_tools=["Bash"],
        hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[gate], timeout=TIMEOUT_S)]},
        max_turns=2,
    )

    started = time.monotonic()
    try:
        async with asyncio.timeout(DEADLINE_S):
            async with ClaudeSDKClient(options=options) as client:
                await client.query(PROMPT)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, ToolUseBlock) and block.name == "Bash":
                                # The model asking for Bash. This is what the hook
                                # fires on, so on its own it says nothing about
                                # whether the call survived the gate.
                                observed["bash_requested"] = True
                                bash_use_ids.add(block.id)
                    elif isinstance(message, UserMessage) and not isinstance(message.content, str):
                        for result in message.content:
                            if not isinstance(result, ToolResultBlock):
                                continue
                            if result.tool_use_id not in bash_use_ids:
                                continue
                            if not result.is_error and ECHOED in str(result.content):
                                observed["bash_executed"] = True
                    elif isinstance(message, ResultMessage):
                        observed["result_is_error"] = message.is_error
                        observed["terminal_reason"] = message.terminal_reason
    except TimeoutError:
        observed["deadline_hit"] = True
    except Exception as exc:  # noqa: BLE001 - a probe reports the failure, it does not raise it
        observed["exception"] = f"{type(exc).__name__}: {exc}"
    observed["wall_s"] = round(time.monotonic() - started, 1)
    return observed


def _report(observed: dict[str, Any]) -> None:
    cancelled_after = observed["hook_cancelled_after_s"]
    slept = observed["hook_slept_s"]

    if observed["hook_fires"] == 0:
        print("  INCONCLUSIVE: the PreToolUse hook never fired -- no ceiling was exercised.")
        print(f"  exception={observed['exception']} deadline_hit={observed['deadline_hit']}")
        return
    if observed["hook_fires"] > 1:
        print(f"  INCONCLUSIVE: the hook fired {observed['hook_fires']} times, so the recorded")
        print("  numbers are the last firing's and not the measurement. Two blocking calls was")
        print("  never the experiment; rerun for a single one.")
        return
    if observed["deadline_hit"]:
        # Ahead of the cancellation arm on purpose. This probe's own deadline is
        # a cancellation source, and attributing it to the CLI would print the
        # one verdict that blocks decision 1 on evidence the CLI never produced.
        print(f"  INCONCLUSIVE: the run hit this probe's own {DEADLINE_S:.0f}s deadline, which is")
        print("  itself a cancellation source. Nothing here can be attributed to the CLI.")
        print(f"  hook_cancelled_after_s={cancelled_after} hook_slept_s={slept}")
        return
    if cancelled_after is not None:
        print(f"  CEILING BELOW {BLOCK_S:.0f}s: the hook was aborted at {cancelled_after}s")
        print(f"  under timeout={TIMEOUT_S:.0f}, on the run's only firing and with this probe's")
        print("  own deadline not reached. The value is not honoured as written.")
        if cancelled_after <= 70.0:
            print("  That is the 60s default's neighbourhood: a silent fallback, not a cap.")
        print("  APPROVAL_TIMEOUT_S cannot be raised to this value; §5.2.1 needs revisiting.")
        return
    if not observed["hook_completed"] or slept is None:
        print("  INCONCLUSIVE: the hook fired but neither completed nor reported a cancellation.")
        print(f"  deadline_hit={observed['deadline_hit']} exception={observed['exception']}")
        return
    if slept < BLOCK_S - 1.0:
        print(f"  INCONCLUSIVE: the hook returned after only {slept}s, short of the {BLOCK_S:.0f}s")
        print("  it was told to block. The sleep, not the CLI, ended early.")
        return
    if not observed["bash_executed"]:
        print(f"  INCONCLUSIVE: the hook survived {slept}s, but no tool result carrying {ECHOED!r}")
        print(f"  came back (bash_requested={observed['bash_requested']}), so nothing establishes")
        print("  the allow was acted on. A request alone is what the hook fires on.")
        return
    if observed["result_is_error"]:
        print(f"  INCONCLUSIVE: the hook survived {slept}s and the shell ran, but the turn ended")
        print(f"  in an error (terminal_reason={observed['terminal_reason']!r}).")
        return
    print(f"  NO SILENT FALLBACK: a {slept}s block under timeout={TIMEOUT_S:.0f} completed,")
    print(f"  the shell echoed {ECHOED!r} back through a tool result, and the turn ended")
    print(f"  with terminal_reason={observed['terminal_reason']!r}.")
    print(f"  Any ceiling below {BLOCK_S:.0f}s is ruled out at this magnitude. A clamp to some")
    print("  value above it is not -- this run cannot see one.")


if __name__ == "__main__":
    raise SystemExit(main())
