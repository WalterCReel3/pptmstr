#!/usr/bin/env python3
"""
How does an agent *asking the operator something* reach us, and how is a turn staged?

Two probes in one run, because both need the same live turn.

**1. The question channel.** Dogfooding turned up a session where a question the
agent asked never became an interaction. The suspicion is structural rather than
incidental: the gate classifies every unrecognised tool as REQUIRE_APPROVAL and
renders it as approve/reject over raw JSON. A tool whose entire purpose is to ask a
question therefore arrives as "approve this?" -- answerable only as yes or no, which
is not what it asked. Channels worth distinguishing, because they would each need
different handling:

  1. A tool call (AskUserQuestion, ExitPlanMode) -> PreToolUse, and our gate sees it
  2. A Notification hook               -- the CLI signalling it wants attention
  3. A PermissionRequest hook          -- permission escalation, distinct from PreToolUse
  4. Nothing at all                    -- the worst case: the agent waits and we never know

**2. Whether a turn can be staged at all**
(planning/2026-08-13-detail-swaps-to-a-deliverable.md, step 2). That doc's
comment/deliverable split is a switch on `AssistantMessage.stop_reason`, and it says
outright that the whole model is contingent on a value nobody has ever seen from
this CLI build. The field is declared `str | None` on
`claude_agent_sdk.AssistantMessage` and populated with a plain `.get` off the wire
(the SDK's `_internal.message_parser`), so `None` is reachable without anything
being broken -- and under `None` the obvious fallback
("a message with no ToolUseBlock is the turn's last") is wrong for `max_tokens` and
`pause_turn`, which both produce text-bearing tool-free messages mid-turn. This
probe therefore reports the field's value *and its type*, and distinguishes "the CLI
sent no stop_reason" from "this SDK build has no such field" rather than collapsing
both to a falsy value.

`include_partial_messages=True` here, matching `AgentSession._options()`. It is not
cosmetic: with partial messages on, `Translator._stream` writes the prose from the
deltas and `_assistant` skips the text block it has already streamed, so the two
settings do not exercise the same path and a measurement taken under `False` says
nothing about the app.

Nothing here is fixed or decided. It captures four things, which settle in order:
whether staging is a field read, whether the deliverable text arrives free on
`ResultMessage.result`, whether obligation-only filtering does real work, and how
much the working state has to carry between assistant messages.

Usage:  .venv/bin/python scripts/verify_questions.py [--case plan|ask|tool|all]
        .venv/bin/python scripts/verify_questions.py --selftest   # no API, no tokens
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
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
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
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
    # The staging case: a turn that calls a tool and *then* answers is the minimum
    # shape that can show stop_reason discriminating a comment from a deliverable.
    # A turn with no tool call produces one assistant message and settles nothing.
    "tool": (
        "default",
        "Read the file STYLE.md in this project, then tell me in two sentences what "
        "rule section 1 lays down. Read it first -- do not answer from memory -- and "
        "do not ask me anything.",
    ),
}

# Three outcomes, not two. "the CLI sent null" is the one that sinks the staging
# model, "this build has no such attribute" is a different problem with a different
# fix, and "a message kind that has no stop reason to give" is neither and must not
# be counted as evidence about either.
_ABSENT = object()
_NA = object()


def _read(message: object, field: str) -> Any:
    return getattr(message, field, _ABSENT)


def _show(value: Any) -> str:
    if value is _NA:
        return "-"
    if value is _ABSENT:
        return "<no such field on this SDK build>"
    return f"{value!r} ({type(value).__name__})"


def observe() -> dict[str, Any]:
    return {
        "hooks": [],
        "message_kinds": Counter(),
        "system_subtypes": Counter(),
        "tool_calls": [],
        "assistant_text": [],
        "available_tools": None,
        # Arrival-ordered rows, one per message, with runs of content_block_delta
        # collapsed -- a token-per-row table is not readable and the deltas carry no
        # stop_reason anyway.
        "rows": [],
        # Arrival times of complete AssistantMessages, for the gap measurement.
        "assistant_at": [],
        "result": _ABSENT,
        "result_stop_reason": _ABSENT,
        "terminal_reason": None,
        "is_error": None,
        "started": 0.0,
        "ended": 0.0,
    }


def _blocks(message: AssistantMessage) -> tuple[list[str], str]:
    tools = [b.name for b in message.content if isinstance(b, ToolUseBlock)]
    text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
    return tools, text


def record(seen: dict[str, Any], message: object, at: float) -> None:
    """
    Fold one message into the observation. Shared by the live run and --selftest, so
    the reporting path the operator's run depends on is the one exercised offline.
    """
    kind = type(message).__name__
    seen["message_kinds"][kind] += 1
    rows = seen["rows"]

    if isinstance(message, StreamEvent):
        event_type = str(message.event.get("type", "?"))
        if event_type == "content_block_delta" and rows and rows[-1]["detail"] == event_type:
            rows[-1]["count"] += 1
            rows[-1]["at"] = at
            return
        # message_delta is where the streaming API reports the stop reason, so it is
        # a second, independent answer to the same question if the complete message
        # comes back None.
        # StreamEvent.event is the raw API event, typed `dict[str, Any]` and
        # otherwise unpromised, so every level is checked before it is indexed. A
        # probe that raises here has spent a live turn to report nothing.
        stop: Any = _NA
        carrier = {"message_delta": "delta", "message_start": "message"}.get(event_type)
        if carrier is not None:
            nested = message.event.get(carrier)
            if isinstance(nested, dict):
                stop = nested.get("stop_reason", _NA)
        rows.append(
            {"at": at, "kind": kind, "detail": event_type, "stop": stop, "count": 1, "note": ""}
        )
        return

    if isinstance(message, AssistantMessage):
        tools, text = _blocks(message)
        seen["assistant_text"].append(text)
        seen["assistant_at"].append(at)
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                seen["tool_calls"].append({"name": block.name, "input": block.input})
        note = f"{len(text)} chars"
        if tools:
            note += " + tools: " + ",".join(tools)
        rows.append(
            {
                "at": at,
                "kind": kind,
                "detail": "tool_use" if tools else "text only",
                "stop": _read(message, "stop_reason"),
                "count": 1,
                "note": note,
            }
        )
        return

    if isinstance(message, SystemMessage):
        seen["system_subtypes"][message.subtype] += 1
        if message.subtype == "init":
            seen["available_tools"] = message.data.get("tools")
        rows.append(
            {
                "at": at,
                "kind": kind,
                "detail": message.subtype,
                "stop": _NA,
                "count": 1,
                "note": "",
            }
        )
        return

    if isinstance(message, ResultMessage):
        seen["result"] = _read(message, "result")
        seen["result_stop_reason"] = _read(message, "stop_reason")
        seen["terminal_reason"] = message.terminal_reason
        seen["is_error"] = message.is_error
        rows.append(
            {
                "at": at,
                "kind": kind,
                "detail": message.subtype,
                "stop": _read(message, "stop_reason"),
                "count": 1,
                "note": f"is_error={message.is_error}",
            }
        )
        return

    rows.append({"at": at, "kind": kind, "detail": "", "stop": _NA, "count": 1, "note": ""})


async def run_case(name: str, mode: str, prompt: str) -> dict[str, Any]:
    seen = observe()

    def note_hook(hook_name: str, data: dict[str, Any]) -> None:
        entry = {"hook": hook_name}
        for key, value in data.items():
            if key in ("cwd", "transcript_path", "permission_mode", "prompt_id"):
                continue
            entry[key] = value
        seen["hooks"].append(entry)

    async def pre_tool_use(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        note_hook("PreToolUse", data)
        return {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
        }

    async def notification(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        note_hook("Notification", data)
        return {}

    async def permission_request(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        note_hook("PermissionRequest", data)
        return {}

    async def user_prompt_submit(data: dict, tool_use_id: str | None, ctx: dict) -> dict:
        note_hook("UserPromptSubmit", data)
        return {}

    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        permission_mode=mode,  # type: ignore[arg-type]
        # True, matching AgentSession._options(). Under False the complete
        # AssistantMessage is the only carrier of the prose and _assistant never
        # skips a block, which is a different code path from the app's.
        include_partial_messages=True,
        max_turns=6,
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre_tool_use], timeout=600)],
            "Notification": [HookMatcher(hooks=[notification], timeout=600)],
            "PermissionRequest": [HookMatcher(hooks=[permission_request], timeout=600)],
            "UserPromptSubmit": [HookMatcher(hooks=[user_prompt_submit], timeout=600)],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        started = time.monotonic()
        seen["started"] = started
        await client.query(prompt)
        async for message in client.receive_response():
            record(seen, message, time.monotonic() - started)
        seen["ended"] = time.monotonic() - started
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
    staging_report(seen)


def staging_report(seen: dict[str, Any]) -> None:
    """
    The four measurements step 2 of the 08-13 doc asks for, and a verdict on each.

    A verdict is computed from the thing it names or it is not printed. "staging is a
    field read" is false the moment one assistant message comes back without a
    stop_reason, and saying otherwise from a run where every message happened to be
    a tool call would be the failure STYLE.md 2.4 records.
    """
    rows: list[dict[str, Any]] = seen["rows"]

    print("\n-- messages in arrival order (delta runs collapsed) --")
    print(f"  {'t+s':>7}  {'kind':<16} {'detail':<23} {'stop_reason':<28} note")
    for row in rows:
        detail = row["detail"] + (f" x{row['count']}" if row["count"] > 1 else "")
        print(
            f"  {row['at']:>7.2f}  {row['kind']:<16} {detail:<23} "
            f"{_show(row['stop']):<28} {row['note']}".rstrip()
        )

    assistant = [r for r in rows if r["kind"] == "AssistantMessage"]
    print("\n-- 1. is staging a field read? --")
    if not assistant:
        print("  no complete AssistantMessage arrived; nothing to say")
    else:
        census: Counter[str] = Counter(_show(r["stop"]) for r in assistant)
        for value, count in census.most_common():
            print(f"  {count:>3}x  {value}")
        print("\n  stop_reason against tool presence, which is what the switch needs:")
        for row in assistant:
            print(f"    {row['detail']:<12} -> {_show(row['stop'])}")
        usable = [r for r in assistant if r["stop"] not in (None, _ABSENT)]
        if len(usable) == len(assistant):
            print("\n  VERDICT  every assistant message carried a stop_reason.")
            print("           The disposition switch can be a field read.")
        else:
            print(
                f"\n  VERDICT  {len(assistant) - len(usable)} of {len(assistant)} assistant "
                "messages carried no usable stop_reason."
            )
            print("           The staging model needs another discriminator. Note that")
            print("           'no ToolUseBlock means the turn's last' is NOT it: it")
            print("           misreads max_tokens and pause_turn as a finished answer.")
        deltas = [r for r in rows if r["detail"] == "message_delta"]
        if deltas:
            print("\n  the streaming message_delta events said, independently:")
            for row in deltas:
                print(f"    t+{row['at']:.2f}  {_show(row['stop'])}")

    print("\n-- 2. does the deliverable text arrive free on ResultMessage.result? --")
    result = seen["result"]
    last_text = seen["assistant_text"][-1] if seen["assistant_text"] else ""
    present = "present" if isinstance(result, str) else _show(result)
    print(f"  ResultMessage.result       {present}")
    print(f"  ResultMessage.stop_reason  {_show(seen['result_stop_reason'])}")
    if isinstance(result, str):
        print(
            f"  length                     {len(result)} chars (last assistant: {len(last_text)})"
        )
        print(f"  text                       {_clip(result, 300)}")
        if result == last_text:
            print("  VERDICT  verbatim copy of the final assistant text.")
        elif result.strip() == last_text.strip():
            print("  VERDICT  the final assistant text, modulo surrounding whitespace.")
        else:
            print("  VERDICT  NOT the final assistant text. Anything rendering it as the")
            print("           deliverable would be showing something else.")
    elif result is None:
        print("  VERDICT  present on the dataclass but null on the wire.")
    print("  Whatever it says, it cannot serve a sub-agent card: ResultMessage has no")
    print("  parent_tool_use_id field (claude_agent_sdk/types.py, ResultMessage), so")
    print("  nothing built on it can ever be attributed to a sub-agent's own node.")

    print("\n-- 3. did the turn end in a question or a statement? --")
    tail = last_text.strip()
    asked_by_tool = [c["name"] for c in seen["tool_calls"] if _is_a_question(c["name"], c["input"])]
    print(f"  last assistant text ends   {_clip(tail[-120:] or '(empty)', 160)}")
    print(f"  ends with '?'              {tail.endswith('?')}")
    print(f"  question-shaped tool calls {asked_by_tool or '(none)'}")
    print("  (One turn settles nothing about the ratio. The 08-13 doc's habituation")
    print("   question needs this counted across a session, not a run.)")

    print("\n-- 4. what the working state has to carry --")
    times: list[float] = seen["assistant_at"]
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    if gaps:
        print(
            f"  assistant-to-assistant     n={len(gaps)}  min={min(gaps):.2f}s  "
            f"median={statistics.median(gaps):.2f}s  max={max(gaps):.2f}s"
        )
    else:
        print("  assistant-to-assistant     only one assistant message; no gap to measure")
    if times:
        print(f"  first assistant message    t+{times[0]:.2f}s")
        print(f"  last assistant -> result   {seen['ended'] - times[-1]:.2f}s")
    print(f"  whole turn                 {seen['ended']:.2f}s")
    print(f"  terminal_reason            {seen['terminal_reason']!r}  is_error={seen['is_error']}")


def _is_a_question(tool_name: str, args: dict[str, Any]) -> bool:
    if tool_name in ("AskUserQuestion", "ExitPlanMode"):
        return True
    return "questions" in args or "plan" in args


def _clip(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def selftest() -> int:
    """
    Drive ``record`` and the report with a synthetic turn. No API, no tokens.

    The point is not that the numbers mean anything -- they are made up. It is that
    every branch the operator's one live run depends on has already been executed,
    including the outcome the 08-13 doc says would sink the staging model:
    ``stop_reason`` arriving ``None`` on a text-bearing message.
    """
    seen = observe()
    at = 0.0

    def add(message: object, step: float = 0.4) -> None:
        nonlocal at
        at += step
        record(seen, message, at)

    add(SystemMessage(subtype="init", data={"tools": ["Read", "Bash"]}))
    add(
        StreamEvent(
            uuid="u0",
            session_id="s",
            event={"type": "message_start", "message": {"stop_reason": None}},
        )
    )
    for _ in range(5):
        add(StreamEvent(uuid="u1", session_id="s", event={"type": "content_block_delta"}), 0.05)
    add(
        StreamEvent(
            uuid="u2",
            session_id="s",
            event={"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        )
    )
    # A shape the raw event type permits and this probe must survive: the API is
    # free to send a delta with nothing useful under it.
    add(StreamEvent(uuid="u3", session_id="s", event={"type": "message_delta"}), 0.05)
    add(
        AssistantMessage(
            content=[
                TextBlock(text="Reading STYLE.md first."),
                ToolUseBlock(id="t1", name="Read", input={"file_path": "STYLE.md"}),
            ],
            model="claude-sonnet-5",
            stop_reason="tool_use",
        )
    )
    # A message the CLI sent with no stop reason at all: the None outcome.
    add(
        AssistantMessage(
            content=[TextBlock(text="Still working through it.")],
            model="claude-sonnet-5",
        ),
        1.7,
    )
    add(
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="...STYLE.md...")]),
        0.3,
    )
    add(
        AssistantMessage(
            content=[TextBlock(text="Section 1 is the functional core.")],
            model="claude-sonnet-5",
            stop_reason="end_turn",
        ),
        0.9,
    )
    add(
        ResultMessage(
            subtype="success",
            duration_ms=3000,
            duration_api_ms=2500,
            is_error=False,
            num_turns=1,
            session_id="s",
            stop_reason="end_turn",
            result="Section 1 is the functional core.",
            terminal_reason="completed",
        )
    )
    seen["ended"] = at + 0.2

    # _read's guard against a field this SDK build declares and a later one might
    # not. No installed message object can produce it, so it is exercised directly
    # rather than left to be discovered forty seconds into a live turn.
    assert _show(_read(object(), "stop_reason")) == "<no such field on this SDK build>"

    print("SELFTEST -- synthetic messages, invented numbers, no API call.")
    report("selftest", seen)
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=[*CASES, "all"], default="all")
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="exercise the recording and reporting path against synthetic messages",
    )
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    names = list(CASES) if args.case == "all" else [args.case]
    for name in names:
        mode, prompt = CASES[name]
        try:
            report(name, await run_case(name, mode, prompt))
        except Exception as exc:
            print(f"\ncase {name} failed: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
