#!/usr/bin/env python3
"""
How does an agent *asking the operator something* reach us?

Dogfooding turned up a session where a question the agent asked never became an
interaction. The suspicion is structural rather than incidental: the gate
classifies every unrecognised tool as REQUIRE_APPROVAL and renders it as
approve/reject over raw JSON. A tool whose entire purpose is to ask a question
therefore arrives as "approve this?" -- answerable only as yes or no, which is not
what it asked.

This probe does not fix anything. It captures which channel a question actually
arrives on, and shows what an operator would currently be shown for it, so the fix
can be designed against evidence.

Channels worth distinguishing, because they would each need different handling:

  1. A tool call (AskUserQuestion, ExitPlanMode) -> PreToolUse, and our gate sees it
  2. A Notification hook               -- the CLI signalling it wants attention
  3. A PermissionRequest hook          -- permission escalation, distinct from PreToolUse
  4. Nothing at all                    -- the worst case: the agent waits and we never know

Usage:  .venv/bin/python scripts/verify_questions.py [--case plan|ask|both]
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from pptmstr.approval import classify, render_diff, summarize  # noqa: E402

CASES: dict[str, tuple[str, str]] = {
    # name: (permission_mode, prompt)
    "plan": (
        "plan",
        "Plan how to add a status-bar clock to this project. Do not write anything; "
        "produce a plan and present it for approval.",
    ),
    "ask": (
        "default",
        "I want to add a cache to this project but I have not told you where it should "
        "live or what it should store. Before doing anything at all, ask me the "
        "clarifying questions you need answered. Do not guess and do not proceed.",
    ),
}


def observe() -> dict[str, Any]:
    return {
        "hooks": [],
        "message_kinds": Counter(),
        "system_subtypes": Counter(),
        "tool_calls": [],
        "assistant_text": [],
        "available_tools": None,
    }


async def run_case(name: str, mode: str, prompt: str) -> dict[str, Any]:
    seen = observe()

    def record(hook_name: str, data: dict[str, Any]) -> None:
        entry = {"hook": hook_name}
        for key, value in data.items():
            if key in ("cwd", "transcript_path", "permission_mode", "prompt_id"):
                continue
            entry[key] = value
        seen["hooks"].append(entry)

    async def pre_tool_use(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("PreToolUse", data)
        return {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
        }

    async def notification(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("Notification", data)
        return {}

    async def permission_request(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("PermissionRequest", data)
        return {}

    async def user_prompt_submit(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        record("UserPromptSubmit", data)
        return {}

    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        permission_mode=mode,  # type: ignore[arg-type]
        include_partial_messages=False,
        max_turns=6,
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre_tool_use], timeout=600)],
            "Notification": [HookMatcher(hooks=[notification], timeout=600)],
            "PermissionRequest": [HookMatcher(hooks=[permission_request], timeout=600)],
            "UserPromptSubmit": [HookMatcher(hooks=[user_prompt_submit], timeout=600)],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            seen["message_kinds"][type(message).__name__] += 1
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        seen["tool_calls"].append({"name": block.name, "input": block.input})
                    elif isinstance(block, TextBlock):
                        seen["assistant_text"].append(block.text)
            elif isinstance(message, SystemMessage):
                seen["system_subtypes"][message.subtype] += 1
                if message.subtype == "init":
                    seen["available_tools"] = message.data.get("tools")
            elif isinstance(message, ResultMessage):
                seen["terminal_reason"] = message.terminal_reason
    return seen


def report(name: str, seen: dict[str, Any]) -> None:
    print(f"\n{'=' * 70}\ncase: {name}\n{'=' * 70}")

    print("\n-- hooks fired --")
    for entry in seen["hooks"] or [{"hook": "(none)"}]:
        hook = entry.pop("hook", "?")
        detail = " ".join(f"{k}={_clip(v)}" for k, v in entry.items())
        print(f"  {hook:<18} {detail}")

    print("\n-- message kinds --")
    for kind, count in seen["message_kinds"].most_common():
        print(f"  {kind:<24} {count}")

    print("\n-- tool calls, and what the operator would be shown --")
    if not seen["tool_calls"]:
        print("  (none)")
    for call in seen["tool_calls"]:
        name_, args = call["name"], call["input"]
        disposition = classify(name_, args)
        print(f"\n  tool        {name_}")
        print(f"  gate        {disposition.name}")
        print(f"  summary     {summarize(name_, args)}")
        print(f"  diff        {'yes' if render_diff(name_, args) else 'none'}")
        print(f"  raw args    {_clip(json.dumps(args), 400)}")
        if _is_a_question(name_, args):
            print("  VERDICT     this tool ASKS THE OPERATOR SOMETHING.")
            print("              Rendered as approve/reject it cannot be answered,")
            print("              only permitted -- which is not what it asked.")

    if seen["assistant_text"]:
        print("\n-- assistant text (a question asked in prose reaches no UI at all) --")
        for text in seen["assistant_text"][-2:]:
            print(f"  {_clip(text, 300)}")

    print(f"\n-- available tools --\n  {seen['available_tools']}")


def _is_a_question(tool_name: str, args: dict[str, Any]) -> bool:
    if tool_name in ("AskUserQuestion", "ExitPlanMode"):
        return True
    return "questions" in args or "plan" in args


def _clip(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=[*CASES, "both"], default="both")
    args = ap.parse_args()

    names = list(CASES) if args.case == "both" else [args.case]
    for name in names:
        mode, prompt = CASES[name]
        try:
            report(name, await run_case(name, mode, prompt))
        except Exception as exc:
            print(f"\ncase {name} failed: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
