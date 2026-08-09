#!/usr/bin/env python3
"""
Does a PreToolUse hook that blocks past HookMatcher.timeout actually survive?

This is the one open question that can still force a design change. Invariant I8
says the approval gate must be able to park an agent indefinitely while a human
looks at it. Reading the SDK source proves only that the timeout number is passed
through to the CLI (_internal/query.py) and that the CLI has per-hook abort
machinery. Whether a large value is honoured end to end is a different claim, and
only a run can settle it.

Three cases, each a real agent with a real subprocess:

  short-default   hook sleeps 8s, no timeout set   -> should ALLOW (under the 60s default)
  exceeds-short   hook sleeps 6s, timeout=2s       -> should show what abort looks like
  long-timeout    hook sleeps 75s, timeout=6h      -> the one that matters

If "long-timeout" allows the tool, I8 holds as designed and the gate can simply
await. If it aborts, the gate has to be rebuilt on permissionDecision "defer" and
§5.2.1 needs rewriting.

Costs a few tokens per case: each runs one real turn against a trivial prompt.
Usage:  .venv/bin/python scripts/verify_hook_timeout.py [--case NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

# A prompt that reliably provokes exactly one tool call and nothing else.
PROMPT = "Run the bash command `echo pptmstr-probe` and then reply with just the word done."

CASES: dict[str, tuple[float, float | None]] = {
    # name: (seconds the hook blocks, HookMatcher.timeout)
    "short-default": (8.0, None),
    "exceeds-short": (6.0, 2.0),
    "long-timeout": (75.0, 6 * 60 * 60),
}


async def run_case(name: str, block_s: float, timeout_s: float | None) -> dict[str, object]:
    observed: dict[str, object] = {
        "case": name,
        "blocked_for": block_s,
        "timeout": timeout_s,
        "hook_fired": False,
        "hook_completed": False,
        "tool_ran": False,
        "result_error": None,
        "text": "",
    }

    async def gate(hook_input: dict, tool_use_id: str | None, context: dict) -> dict:
        observed["hook_fired"] = True
        started = time.monotonic()
        try:
            await asyncio.sleep(block_s)
        except asyncio.CancelledError:
            # If the CLI's abort propagates as a cancellation of our coroutine, this
            # is where it lands -- worth distinguishing from "the hook finished but
            # its answer was ignored".
            observed["hook_cancelled_after"] = round(time.monotonic() - started, 1)
            raise
        observed["hook_completed"] = True
        observed["hook_slept"] = round(time.monotonic() - started, 1)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }

    matcher = (
        HookMatcher(matcher="Bash", hooks=[gate])
        if timeout_s is None
        else HookMatcher(matcher="Bash", hooks=[gate], timeout=timeout_s)
    )
    options = ClaudeAgentOptions(
        model="claude-haiku-4-5-20251001",
        permission_mode="dontAsk",
        allowed_tools=["Bash"],
        hooks={"PreToolUse": [matcher]},
        max_turns=2,
    )

    started = time.monotonic()
    async with ClaudeSDKClient(options=options) as client:
        await client.query(PROMPT)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock) and block.name == "Bash":
                        observed["tool_ran"] = True
                    if isinstance(block, TextBlock):
                        observed["text"] += block.text
            elif isinstance(message, ResultMessage):
                observed["result_error"] = message.is_error
                observed["terminal_reason"] = message.terminal_reason
    observed["wall_s"] = round(time.monotonic() - started, 1)
    return observed


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=sorted(CASES), default=None)
    args = ap.parse_args()

    names = [args.case] if args.case else list(CASES)
    results = []
    for name in names:
        block_s, timeout_s = CASES[name]
        print(f"--- {name}: hook blocks {block_s}s, timeout={timeout_s}")
        try:
            observed = await run_case(name, block_s, timeout_s)
        except Exception as exc:
            observed = {"case": name, "exception": f"{type(exc).__name__}: {exc}"}
        results.append(observed)
        for key, value in observed.items():
            print(f"      {key}: {value}")
        print()

    print("=== verdict ===")
    for observed in results:
        if observed["case"] != "long-timeout":
            continue
        if observed.get("hook_completed") and observed.get("tool_ran"):
            print("I8 HOLDS: a 75s block under a 6h timeout completed and the tool ran.")
            print("The approval gate can await the operator directly.")
        else:
            print("I8 DOES NOT HOLD as designed: the long block did not survive.")
            print("Rebuild the gate on permissionDecision 'defer' and rewrite §5.2.1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
