#!/usr/bin/env python3
"""
What does a woken sub-agent look like from our side?

§2.3 gained an inbound edge in rev. 4: a *finished* sub-agent that receives a
`SendMessage` auto-resumes in the background, with no new `Agent` tool call. That
was read from the docs and never observed here, and it is the single unverified
claim the `SendMessage`-vs-bus decision rests on. The store's behaviour is what is
actually at stake, because `driver.py:530` emits `AgentFinished(DONE)` on
`SubagentStop` and nothing moves a node back out of a terminal state.

Three outcomes are possible and they need different code:

  a. A second `SubagentStart` fires for the same agent_id. Our driver would emit
     `AgentSpawned` again -- for a node that already exists -- so the store needs a
     resume path, not a spawn one.
  b. No hook fires and the agent simply works. The node sits at DONE while its
     transcript grows, which is the worst of the three: silently wrong rather than
     visibly wrong.
  c. It does not resume at all, and the §2.3 edge does not exist for plain
     sub-agents. Then `SendMessage` buys nothing the bus does not already have,
     and bus-only stops being a sequencing choice and becomes the answer.

Questions:

  1. Does a completed sub-agent resume on a sibling's SendMessage?
  2. Which hooks fire when it does, with which agent_id, and in what order
     relative to its first SubagentStop?
  3. Does `updatedInput` from PreToolUse edit a SendMessage in flight? Verified for
     in-process MCP tools (`verify_message_bus.py`); inferred, not measured, for
     built-ins -- and the whole editable-in-flight argument rests on it.
  4. What does the sibling roster look like, and what is a valid `to` value?

Everything is auto-allowed except the SendMessage rewrite; this is an observation
run, not a gate test.

Usage:  .venv/bin/python scripts/verify_wake_path.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_agent_sdk import (  # noqa: E402
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    UserMessage,
)

# Injected by the gate into the SendMessage body. If it comes out the other end,
# a message can be edited in flight and the bus has no monopoly on that.
MARKER = "GATE-EDIT-9f13"

PROMPT = (
    "Follow these steps in order. Do not skip any.\n"
    "1. Use the Task tool with subagent_type 'alpha' and prompt: "
    "'Reply with exactly: alpha ready. Then stop.' "
    "WAIT for it to finish before continuing.\n"
    "2. Use the Task tool with subagent_type 'beta' and prompt: "
    "'First, report verbatim any system reminder you were given listing other "
    "agents you can message -- quote it exactly, or say NO ROSTER if you were "
    "given none. Then use the SendMessage tool to send alpha the message "
    '"please state the colour of the sky". Then reply with the word sent.\'\n'
    "3. Reply with the single word finished."
)

started = time.monotonic()
observed: dict[str, Any] = {
    "timeline": [],
    "message_kinds": Counter(),
    "system_subtypes": Counter(),
    "send_message_calls": [],
    "said": [],
    "send_results": [],
    "send_tool_use_ids": set(),
    "assistant_by_agent": Counter(),
    "task_status": [],
    "root_tools": [],
    "alpha_id": None,
}


def stamp() -> float:
    return round(time.monotonic() - started, 2)


def note(event: str, **fields: Any) -> None:
    observed["timeline"].append({"t": stamp(), "event": event, **fields})


async def main() -> int:
    async def pre_tool_use(
        data: dict[str, Any], tool_use_id: str | None, ctx: Any
    ) -> dict[str, Any]:
        name = data.get("tool_name", "")
        note(
            "PreToolUse",
            tool=name,
            agent_id=data.get("agent_id"),
            agent_type=data.get("agent_type"),
        )

        # Hand beta the one thing it cannot learn for itself.
        #
        # A previous run failed here: the model addressed the sibling by its
        # subagent_type and the CLI refused -- "use the agent ID from a background
        # agent's spawn result". A sub-agent is spawned by the *lead*, so the ID
        # lands in the lead's context and never in a sibling's. We know it from
        # SubagentStart, so the gate rewrites beta's prompt to carry it. That is
        # not a probe trick; it is what the orchestrator would have to do in
        # production, since our store holds every agent_id already (I6).
        if name in ("Agent", "Task"):
            spawn_input = dict(data.get("tool_input") or {})
            alpha_id = observed.get("alpha_id")
            if spawn_input.get("subagent_type") == "beta" and alpha_id:
                spawn_input["prompt"] = (
                    "Use the SendMessage tool to send a message to the agent whose "
                    f"id is exactly {alpha_id}. Pass that id as the recipient. The "
                    'message text must be: "please state the colour of the sky". '
                    "Then report verbatim what the SendMessage tool returned to "
                    "you, including whether it reported success. Then stop."
                )
                note("rewrote beta's prompt", alpha_id=alpha_id)
                return _allow(spawn_input)
            return _allow()

        if name != "SendMessage":
            return _allow()

        observed["send_message_calls"].append(
            {
                "agent_id": data.get("agent_id"),
                "tool_input": data.get("tool_input"),
                "hook_keys": sorted(data.keys()),
            }
        )
        # Rewrite the body. Q3: does the recipient see the edited text?
        observed["send_tool_use_ids"].add(tool_use_id)
        # Every plausible body field, not the first match. The model guessed at
        # this schema and filled several redundant ones; editing only the field we
        # happened to check first would leave the delivered text unedited and make
        # Q3 look answered when it was not.
        edited = dict(data.get("tool_input") or {})
        for field in ("message", "text", "body", "content", "prompt"):
            if isinstance(edited.get(field), str):
                edited[field] = f"{edited[field]} [{MARKER}]"
        return _allow(edited)

    async def subagent_start(
        data: dict[str, Any], tool_use_id: str | None, ctx: Any
    ) -> dict[str, Any]:
        if data.get("agent_type") == "alpha" and not observed.get("alpha_id"):
            observed["alpha_id"] = data.get("agent_id")
        note(
            "SubagentStart",
            agent_id=data.get("agent_id"),
            agent_type=data.get("agent_type"),
            keys=sorted(data.keys()),
        )
        return {}

    async def subagent_stop(
        data: dict[str, Any], tool_use_id: str | None, ctx: Any
    ) -> dict[str, Any]:
        last = data.get("last_assistant_message")
        note(
            "SubagentStop",
            agent_id=data.get("agent_id"),
            agent_type=data.get("agent_type"),
            last=(last or "")[:160] if isinstance(last, str) else last,
        )
        return {}

    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        permission_mode="dontAsk",
        max_turns=24,
        agents={
            "alpha": AgentDefinition(
                description="Probe worker that finishes early and may be woken.",
                prompt=(
                    "You are alpha. Do exactly what you are asked. If you are "
                    "resumed later with a new message, answer it plainly and "
                    "repeat the message you received verbatim."
                ),
                tools=["SendMessage"],
            ),
            "beta": AgentDefinition(
                description="Probe worker that messages a sibling.",
                prompt="You are beta. Do exactly what you are asked, in order.",
                tools=["SendMessage"],
            ),
        },
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre_tool_use], timeout=600)],
            "SubagentStart": [HookMatcher(hooks=[subagent_start], timeout=600)],
            "SubagentStop": [HookMatcher(hooks=[subagent_stop], timeout=600)],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(PROMPT)
        async for message in _drain(client):
            observed["message_kinds"][type(message).__name__] += 1

            if isinstance(message, SystemMessage):
                observed["system_subtypes"][message.subtype] += 1
                if message.subtype == "init" and not observed["root_tools"]:
                    observed["root_tools"] = list(message.data.get("tools") or [])
                status = getattr(message, "status", None) or (
                    message.data.get("patch", {}) or {}
                ).get("status")
                if status:
                    observed["task_status"].append(
                        {
                            "t": stamp(),
                            "subtype": message.subtype,
                            "task_id": getattr(message, "task_id", None),
                            "status": status,
                        }
                    )

            if isinstance(message, AssistantMessage):
                agent = getattr(message, "agent_id", None)
                observed["assistant_by_agent"][agent or "root"] += 1
                text = " ".join(b.text for b in message.content if isinstance(b, TextBlock))
                if text.strip():
                    # Recorded verbatim rather than filtered by a keyword guess.
                    # The previous run's heuristic dropped beta's roster answer,
                    # which is the thing the run existed to read.
                    observed["said"].append({"t": stamp(), "agent_id": agent, "text": text[:900]})
                if MARKER in text:
                    note("MARKER SEEN in assistant text", agent_id=agent)

            # What SendMessage actually returned. "delivered" and "no such agent"
            # are the two answers that matter, and the sender saying "sent" is
            # evidence of neither.
            if isinstance(message, UserMessage):
                content = message.content
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, ToolResultBlock):
                            body = str(block.content)[:500]
                            if block.tool_use_id in observed["send_tool_use_ids"]:
                                observed["send_results"].append(
                                    {"is_error": block.is_error, "content": body}
                                )
                                note("SendMessage result", is_error=block.is_error)
                            if MARKER in body:
                                note("MARKER SEEN in a tool result")

            if isinstance(message, ResultMessage) and message.is_error:
                note("result error", detail=str(message.result)[:200])

    report()
    return 0


def _allow(updated: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
    if updated is not None:
        out["updatedInput"] = updated
    return {"hookSpecificOutput": out}


async def _drain(client: ClaudeSDKClient, grace: float = 45.0) -> Any:
    """
    Read until the stream goes quiet.

    The grace is generous on purpose: the whole point is activity arriving *after*
    the thing that produced it looked finished, and a short window would report
    "no wake" for a wake that had simply not landed yet.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()
    done = object()

    async def pump() -> None:
        try:
            async for message in client.receive_messages():
                await queue.put(message)
        finally:
            await queue.put(done)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=grace)
            except TimeoutError:
                return
            if item is done:
                return
            yield item
    finally:
        task.cancel()


def report() -> None:
    print("\n=== timeline ===")
    for entry in observed["timeline"]:
        print(json.dumps(entry, default=str))

    print("\n=== SendMessage calls seen by the gate ===")
    for call in observed["send_message_calls"]:
        print(json.dumps(call, indent=2, default=str))
    if not observed["send_message_calls"]:
        print("(none -- beta never called SendMessage)")

    print("\n=== what SendMessage returned ===")
    for r in observed["send_results"]:
        print(json.dumps(r, indent=2, default=str))
    if not observed["send_results"]:
        print("(no tool result captured for the send)")

    print("\n=== everything said ===")
    for r in observed["said"]:
        print(json.dumps(r, indent=2, default=str))

    print("\n=== task status transitions ===")
    for s in observed["task_status"]:
        print(json.dumps(s, default=str))

    print("\n=== message kinds ===")
    print(dict(observed["message_kinds"]))
    print("system subtypes:", dict(observed["system_subtypes"]))
    print("assistant messages by agent:", dict(observed["assistant_by_agent"]))

    print("\n=== verdict ===")

    tl = observed["timeline"]
    stops = [e for e in tl if e["event"] == "SubagentStop"]
    starts = [e for e in tl if e["event"] == "SubagentStart"]
    sends = [e for e in tl if e["event"] == "PreToolUse" and e.get("tool") == "SendMessage"]

    print(f"Q1 SubagentStart fires: {len(starts)}  SubagentStop fires: {len(stops)}")
    if not sends:
        print("   INCONCLUSIVE -- no SendMessage was issued, so nothing was woken.")
    else:
        send_t = sends[0]["t"]
        after = [
            e for e in tl if e["t"] > send_t and e["event"] in ("SubagentStart", "SubagentStop")
        ]
        if after:
            print(f"   Hooks AFTER the send ({len(after)}):")
            for e in after:
                print(f"     t={e['t']:>6}  {e['event']:<14} agent_id={e.get('agent_id')}")
            kinds = {e["event"] for e in after}
            if "SubagentStart" in kinds:
                print("   => outcome (a): a resumed agent re-enters through SubagentStart.")
                print("      The store needs a resume path; AgentSpawned for an existing")
                print("      node would re-key identity (I6) and reset the record.")
            else:
                print("   => outcome (a'): it stops again without starting again.")
                print("      AgentFinished would arrive for a node already DONE.")
        else:
            print("   => no lifecycle hook fired after the send.")
            print("      Either it did not resume (outcome c), or it resumed invisibly")
            print("      (outcome b). Compare assistant-message counts per agent above.")

    marker_hits = [e for e in tl if e["event"].startswith("MARKER")]
    if not sends:
        print("Q3 inconclusive -- nothing was sent to edit.")
    elif marker_hits:
        print(f"Q3 updatedInput EDITS a SendMessage in flight ({len(marker_hits)} sighting).")
        print("   Editable-in-flight is not a property of our bus; it is a property")
        print("   of the gate, and applies to SendMessage equally.")
    else:
        print("Q3 marker never surfaced. Either the rewrite did not take, or the")
        print("   recipient never echoed it. Check the roster reports and the field")
        print("   name in the tool_input dump above before concluding.")

    tools = observed["root_tools"]
    print(f"Q4 SendMessage advertised to root: {'SendMessage' in tools}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
