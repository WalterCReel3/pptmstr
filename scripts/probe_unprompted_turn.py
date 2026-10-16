#!/usr/bin/env python3
"""
Does the CLI ever start a root turn that no ``query`` asked for?

rv-tool-block-fix finding 2. pptmstr opens a turn only on the SYSTEM line
``AgentSession.send`` writes, and closes one on each ResultMessage. A root turn the
CLI starts by itself -- when a background Bash finishes, a background sub-agent
returns, or a Monitor reports -- writes no SYSTEM line, so its running tool calls
read NO_RESULT. Whether such turns exist is what this measures. The SDK's own types
suggest they do (``ResultMessage.origin`` is documented as telling "the result of
its own prompt" from "results of injected turns such as background-task
notifications"), and that is a reading, which is why this is a run.

**The observable is accounting, not narration.** Every query the probe sends is
recorded before ``client.query`` is awaited, which is the order ``send`` uses. Every
message ``receive_messages`` yields is recorded with its type, whether it is the
root's (``parent_tool_use_id`` is None), its ``origin`` kind and its time.
``classify`` then pairs results with queries in order:

  prompted     a root ResultMessage while a query is outstanding, origin human/absent
  injected     a root ResultMessage whose origin names something other than a human
  unpaired     a root ResultMessage with nothing outstanding and no injected origin
  unprompted   root activity (an AssistantMessage, a root UserMessage, or a root
  start        stream ``message_start``) while no query is outstanding

Any of the last three is a turn pptmstr's turn-boundary would not have opened.

Arms, each on a fresh client in a fresh scratch cwd:

  control        one prompted turn. The positive control for the recording path:
                 it must classify as one query, one prompted result, nothing else.
  bg-bash        Bash with ``run_in_background`` that touches a marker 15 s after
                 the turn ends. Listened to until the marker exists plus a grace
                 period, then a prompted follow-up.
  bg-agent       an Agent call with ``run_in_background`` whose sub-agent sleeps
                 and touches a marker. Same listening rule.
  monitor        the Monitor tool over a command that prints twice, seconds apart.
  queued         a second query sent while the first turn's Bash is running:
                 does each queued prompt get its own ResultMessage?
  interrupt      a second query sent while the first is running, then
                 ``interrupt()``: what results arrive, and does the queued prompt
                 still run (its own marker says, not the model)?

**A "no" counts only beside its controls.** For the three background arms the
verdict is ``none-seen`` only when (a) the background work is observed finishing
(its marker exists) while the reader is still listening, and (b) the follow-up
prompted turn sent after the window classifies as prompted -- the reader is alive
and would have recorded a turn. Otherwise it is ``inconclusive``. An arm whose tool
was never called (no PreToolUse for it) is inconclusive as well.

**Options are pptmstr's own**, built by ``AgentSession._options()`` for a session in
the scratch cwd, with three departures, each because a probe has no operator:

- ``hooks`` are replaced by recording hooks on the same events. PreToolUse allows
  every tool but the bus's own (``mcp__pptmstr__*``), which it denies: the bus's
  handlers await a store that is not running here, and a call to one would hang
  the arm. Nothing in a hook's answer can start a turn.
- ``env`` keeps ``CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1`` and adds
  ``CLAUDE_CONFIG_DIR`` naming a scratch directory, so the CLI neither reads nor
  writes the operator's ``~/.claude``. It therefore finds no stored login, and
  authenticates only through the environment (``ANTHROPIC_API_KEY`` or
  ``CLAUDE_CODE_OAUTH_TOKEN``); the probe refuses to start without one of them
  set, and reads neither's value.
  ``--operator-login`` leaves ``CLAUDE_CONFIG_DIR`` unset instead, for an operator
  whose login is stored there and not in the environment. That run is not
  isolated from ``~/.claude``: the CLI reads its user settings there (a user-tier
  hook would then fire in the probe's session too) and writes each arm's session
  transcript under its ``projects/`` directory, keyed by the scratch cwd. It is
  opt-in, named on the command line the operator reads before running it.
- ``stderr`` goes to a list instead of a transcript, and is printed only as a
  count of lines.

Uncontained (``containment=None``): the CLI sandbox does not decide whether a turn
starts, and an uncontained launch needs no bubblewrap.

**Bounds.** One run is one CLI build, one model and one machine. A ``none-seen`` is
about these three triggers as prompted here; it is not a proof that no trigger
exists.

Usage:  .venv/bin/python scripts/probe_unprompted_turn.py [--arms a,b] [--repeat N]
            [--model ID] [--self-check] [--dry-run] [--base DIR] [--operator-login]

    --self-check  classify a built-in recorded log and print it; starts no CLI.
    --dry-run     build the options and print what each arm would start.
    --repeat N    run each selected arm N times (default 1).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent

# Origin kinds that mean "the consumer's own prompt". Anything else -- including a
# kind newer than this list -- is an injected turn, which is the reading the SDK
# asks for ("treat anything unrecognized as 'not human'").
HUMAN_ORIGINS = frozenset({"", "human"})

AUTH_VARIABLES = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
BUS_PREFIX = "mcp__pptmstr__"

# --------------------------------------------------------------------------- record


@dataclass(frozen=True, slots=True)
class Event:
    """
    One line of an arm's log. ``t`` is seconds since the arm started.

    kind is "query", "interrupt", "message", "hook" or "mark". For a message,
    ``type`` is the SDK class name and ``subtype`` its subtype (a SystemMessage's
    subtype, a stream event's ``type``, a result's subtype); for a hook, ``type``
    is the hook event and ``subtype`` the tool name.
    """

    t: float
    kind: str
    type: str = ""
    subtype: str = ""
    root: bool = True
    origin: str = ""
    terminal_reason: str = ""
    tools: tuple[str, ...] = ()
    label: str = ""

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @staticmethod
    def from_json(line: str) -> Event:
        raw = json.loads(line)
        raw["tools"] = tuple(raw.get("tools", ()))
        return Event(**raw)


def _origin_kind(origin: object) -> str:
    if isinstance(origin, dict) and isinstance(origin.get("kind"), str):
        return str(origin["kind"])
    return ""


def describe(message: object, t: float) -> Event | None:
    """
    The log line for one message ``receive_messages`` yielded, or None for a stream
    event that is not the start of a message (the token deltas, which say nothing
    about turns and would be most of the log).

    Read by attribute name rather than by importing the SDK's classes, so a log can
    be classified where the SDK is absent; the class name is still the type.
    """
    name = type(message).__name__
    parent = getattr(message, "parent_tool_use_id", None)
    root = parent is None
    if name == "StreamEvent":
        event = getattr(message, "event", {}) or {}
        kind = str(event.get("type", ""))
        if kind != "message_start":
            return None
        return Event(t, "message", name, kind, root=root)
    if name == "AssistantMessage":
        tools = tuple(
            str(getattr(block, "name", ""))
            for block in getattr(message, "content", ()) or ()
            if type(block).__name__ == "ToolUseBlock"
        )
        stop = getattr(message, "stop_reason", None) or ""
        return Event(t, "message", name, str(stop), root=root, tools=tools)
    if name == "UserMessage":
        return Event(
            t, "message", name, root=root, origin=_origin_kind(getattr(message, "origin", None))
        )
    if name == "ResultMessage":
        return Event(
            t,
            "message",
            name,
            str(getattr(message, "subtype", "")),
            root=True,
            origin=_origin_kind(getattr(message, "origin", None)),
            terminal_reason=str(getattr(message, "terminal_reason", None) or ""),
        )
    subtype = getattr(message, "subtype", None)
    label = ""
    status = getattr(message, "status", None)
    if status:
        label = str(status)
    return Event(t, "message", name, str(subtype or ""), root=root, label=label)


# ------------------------------------------------------------------------- classify


@dataclass(frozen=True, slots=True)
class Turns:
    """
    How an arm's root turns pair with its queries. Times are ``Event.t``.
    """

    queries: int
    prompted: tuple[float, ...]
    injected: tuple[tuple[float, str], ...]
    unpaired: tuple[float, ...]
    unprompted_starts: tuple[float, ...]
    outstanding: int

    @property
    def unprompted(self) -> bool:
        return bool(self.injected or self.unpaired or self.unprompted_starts)


ROOT_ACTIVITY = frozenset({"AssistantMessage", "UserMessage", "StreamEvent"})


def classify(events: Iterable[Event]) -> Turns:
    """
    Pair root results with queries in order, and find root activity nothing asked
    for. Pure: the same log always gives the same answer.

    A root turn whose result carries an injected origin is injected whatever the
    count says -- the CLI may run one while a prompt of ours is still waiting, and
    pairing by count alone would hand that prompt the injected turn's result.
    """
    queries = 0
    outstanding = 0
    prompted: list[float] = []
    injected: list[tuple[float, str]] = []
    unpaired: list[float] = []
    starts: list[float] = []
    open_unprompted = False
    for event in events:
        if event.kind == "query":
            queries += 1
            outstanding += 1
            continue
        if event.kind != "message" or not event.root:
            continue
        if event.type == "ResultMessage":
            if event.origin not in HUMAN_ORIGINS:
                injected.append((event.t, event.origin))
            elif outstanding > 0:
                outstanding -= 1
                prompted.append(event.t)
            else:
                unpaired.append(event.t)
            open_unprompted = False
            continue
        if event.type in ROOT_ACTIVITY and outstanding == 0 and not open_unprompted:
            starts.append(event.t)
            open_unprompted = True
    return Turns(
        queries=queries,
        prompted=tuple(prompted),
        injected=tuple(injected),
        unpaired=tuple(unpaired),
        unprompted_starts=tuple(starts),
        outstanding=outstanding,
    )


def verdict(turns: Turns, *, tool_called: bool, work_done: bool, control: Turns | None) -> str:
    """
    One background arm's answer. UNPROMPTED stands on its own; ``none-seen`` needs
    the tool to have been called, the work to have finished while the reader
    listened, and the follow-up prompted turn (``control``, classified from the
    events after the window) to have read as exactly one prompted turn.
    """
    if turns.unprompted:
        return "UNPROMPTED"
    if not tool_called:
        return "inconclusive: the tool was never called"
    if not work_done:
        return "inconclusive: the background work was not seen to finish"
    if control is None or control.queries != 1 or len(control.prompted) != 1:
        return "inconclusive: the follow-up prompted turn was not recorded as one"
    return "none-seen"


def table(events: Sequence[Event]) -> list[str]:
    """
    The log as rows, each message timed from the last query and the last result
    before it, and flagged where ``classify`` would flag it.
    """
    turns = classify(events)
    flagged = set(turns.unpaired) | set(turns.unprompted_starts) | {t for t, _ in turns.injected}
    rows = []
    last_query: float | None = None
    last_result: float | None = None

    def since(mark: float | None, t: float) -> str:
        return "-" if mark is None else f"{t - mark:+.1f}"

    for e in events:
        flag = "<-- not asked for" if e.kind == "message" and e.root and e.t in flagged else ""
        extra = " ".join(
            x
            for x in (
                e.subtype,
                "" if e.root else "(sub)",
                f"origin={e.origin}" if e.origin else "",
                f"reason={e.terminal_reason}" if e.terminal_reason else "",
                f"tools={','.join(e.tools)}" if e.tools else "",
                e.label,
            )
            if x
        )
        rows.append(
            f"  {e.t:7.2f}  q{since(last_query, e.t):>7}  r{since(last_result, e.t):>7}  "
            f"{e.kind:<9} {e.type:<24} {extra} {flag}".rstrip()
        )
        if e.kind == "query":
            last_query = e.t
        if e.kind == "message" and e.type == "ResultMessage":
            last_result = e.t
    return rows


# ------------------------------------------------------------------------- live run


@dataclass
class Recorder:
    """
    The arm's log as it is written. One reader task appends messages; the arm's own
    coroutine appends queries, interrupts and marks; hooks append from the SDK's
    control-channel task. All of these run on one event loop, so no lock.
    """

    start: float = field(default_factory=time.monotonic)
    events: list[Event] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    cli_version: str = ""
    stderr: list[str] = field(default_factory=list)

    def now(self) -> float:
        return round(time.monotonic() - self.start, 3)

    def add(self, event: Event) -> None:
        self.events.append(event)
        self.changed.set()

    async def until(self, predicate: Callable[[], bool], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not predicate():
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=min(left, 1.0))
            except TimeoutError:
                pass
        return True

    def results(self) -> int:
        return sum(1 for e in self.events if e.kind == "message" and e.type == "ResultMessage")

    def called(self, *tools: str) -> bool:
        return any(
            e.kind == "hook" and e.type == "PreToolUse" and e.subtype in tools for e in self.events
        )


def recording_hooks(rec: Recorder) -> dict[str, Any]:
    """
    Hooks on the events ``_options`` registers, which record and answer nothing but
    PreToolUse's allow (or deny, for the bus).
    """
    from claude_agent_sdk import HookMatcher

    def hook(event: str) -> Callable[..., Any]:
        async def record(data: Any, tool_use_id: Any, context: Any) -> dict[str, Any]:
            tool = str(data.get("tool_name", "") or data.get("agent_type", ""))
            rec.add(Event(rec.now(), "hook", event, tool, root=not data.get("agent_id")))
            if event != "PreToolUse":
                return {}
            denied = tool.startswith(BUS_PREFIX)
            out: dict[str, Any] = {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny" if denied else "allow",
            }
            if denied:
                out["permissionDecisionReason"] = "The bus is not running in this probe."
            return {"hookSpecificOutput": out}

        return record

    events = (
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PreCompact",
        "SubagentStart",
        "SubagentStop",
    )
    return {e: [HookMatcher(hooks=[hook(e)])] for e in events}


def probe_options(cwd: Path, config_dir: Path | None, model: str, rec: Recorder) -> Any:
    """
    ``AgentSession._options()`` for a session in ``cwd``, with the departures the
    module docstring lists and nothing else changed. ``config_dir`` None is
    ``--operator-login``: ``CLAUDE_CONFIG_DIR`` is left unset.
    """
    sys.path.insert(0, str(ROOT))
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    session = AgentSession(Bridge(), task="probe", cwd=str(cwd), model=model)
    options = session._options()
    return dataclasses.replace(
        options,
        hooks=cast(Any, recording_hooks(rec)),
        env=(
            dict(options.env)
            if config_dir is None
            else {**options.env, "CLAUDE_CONFIG_DIR": str(config_dir)}
        ),
        stderr=rec.stderr.append,
    )


async def _read(client: Any, rec: Recorder) -> None:
    async for message in client.receive_messages():
        if type(message).__name__ == "SystemMessage" and getattr(message, "subtype", "") == "init":
            rec.cli_version = str(getattr(message, "data", {}).get("claude_code_version", ""))
        event = describe(message, rec.now())
        if event is not None:
            rec.add(event)


async def _send(client: Any, rec: Recorder, text: str, label: str) -> None:
    rec.add(Event(rec.now(), "query", label=label))
    await client.query(text)


async def _pause(seconds: float) -> None:
    """
    Every fixed wait an arm makes, in one place so a test can take the waits out.
    """
    await asyncio.sleep(seconds)


ONE_WORD = "Reply with the single word OK and nothing else. Use no tools."

GRACE_S = 45.0
TURN_S = 120.0


@dataclass(frozen=True)
class ArmResult:
    name: str
    verdict: str
    events: tuple[Event, ...]
    detail: str = ""


async def _background_arm(
    client: Any,
    rec: Recorder,
    name: str,
    prompt: str,
    tools: tuple[str, ...],
    marker: Path,
    wait_s: float,
) -> ArmResult:
    await _send(client, rec, prompt, "start")
    await rec.until(lambda: rec.results() >= 1, TURN_S)
    finished = await rec.until(marker.exists, wait_s)
    if finished:
        rec.add(Event(rec.now(), "mark", label="marker seen"))
        await _pause(GRACE_S)
    window = len(rec.events)
    rec.add(Event(rec.now(), "mark", label="window closed"))
    before = rec.results()
    await _send(client, rec, ONE_WORD, "follow-up")
    await rec.until(lambda: rec.results() > before, TURN_S)
    await _pause(3.0)
    events = tuple(rec.events)
    turns = classify(events[:window])
    control = classify(e for e in events[window:] if e.kind != "mark")
    # The whole log, not the window alone: an injected turn that the CLI holds back
    # until the next prompt shows up after the window and is still not asked for.
    whole = classify(events)
    v = verdict(
        whole if whole.unprompted else turns,
        tool_called=rec.called(*tools),
        work_done=finished,
        control=control,
    )
    return ArmResult(name, v, events)


async def arm_control(client: Any, rec: Recorder, scratch: Path) -> ArmResult:
    await _send(client, rec, ONE_WORD, "control")
    await rec.until(lambda: rec.results() >= 1, TURN_S)
    await _pause(5.0)
    turns = classify(rec.events)
    ok = turns.queries == 1 and len(turns.prompted) == 1 and not turns.unprompted
    return ArmResult(
        "control", "recorded as one prompted turn" if ok else "FAILED", tuple(rec.events)
    )


async def arm_bg_bash(client: Any, rec: Recorder, scratch: Path) -> ArmResult:
    marker = scratch / "bg-bash.done"
    prompt = (
        "Call the Bash tool exactly once, with run_in_background set to true, to run "
        f"this command: `sleep 15; touch {marker}; echo BGDONE`. Do not wait for it, "
        "do not poll it, and call no other tool. As soon as the Bash tool returns, "
        "reply with the single word STARTED and end your turn."
    )
    return await _background_arm(client, rec, "bg-bash", prompt, ("Bash",), marker, 60.0)


async def arm_bg_agent(client: Any, rec: Recorder, scratch: Path) -> ArmResult:
    marker = scratch / "bg-agent.done"
    prompt = (
        "Call the Agent tool exactly once, with run_in_background set to true and "
        "subagent_type 'general-purpose', with this prompt: 'Run the bash command "
        f"`sleep 15 && touch {marker}` in the foreground, wait for it, then reply "
        "DONE.' Do not wait for the agent and call no other tool. As soon as the "
        "Agent tool returns, reply with the single word STARTED and end your turn."
    )
    return await _background_arm(client, rec, "bg-agent", prompt, ("Agent", "Task"), marker, 120.0)


async def arm_monitor(client: Any, rec: Recorder, scratch: Path) -> ArmResult:
    marker = scratch / "monitor.done"
    prompt = (
        "Call the Monitor tool exactly once to watch the output of this command: "
        f"`sleep 8; echo TICK; sleep 8; echo TOCK; touch {marker}`. Call no other "
        "tool. As soon as the Monitor tool returns, reply with the single word "
        "STARTED and end your turn."
    )
    return await _background_arm(client, rec, "monitor", prompt, ("Monitor",), marker, 60.0)


def _long_bash(marker: Path) -> str:
    return (
        "Call the Bash tool exactly once, in the foreground (not in the background), "
        f"to run `sleep 12; touch {marker}`, wait for it, then reply with the single "
        "word FIRST."
    )


def _touch(marker: Path) -> str:
    return (
        f"Call the Bash tool exactly once to run `touch {marker}`, then reply with the "
        "single word SECOND."
    )


async def arm_queued(client: Any, rec: Recorder, scratch: Path) -> ArmResult:
    first, second = scratch / "queued-1.done", scratch / "queued-2.done"
    await _send(client, rec, _long_bash(first), "first")
    started = await rec.until(lambda: rec.called("Bash"), TURN_S)
    await _send(client, rec, _touch(second), "second (while the first's Bash runs)")
    await rec.until(lambda: rec.results() >= 2, TURN_S)
    await _pause(10.0)
    turns = classify(rec.events)
    v = (
        f"{turns.queries} queries -> {len(turns.prompted)} prompted results, "
        f"{len(turns.injected)} injected, {len(turns.unpaired)} unpaired, "
        f"{turns.outstanding} never answered"
    )
    detail = (
        f"first's Bash seen before the second query: {started}; markers: first "
        f"{first.exists()}, second {second.exists()}"
    )
    return ArmResult("queued", v, tuple(rec.events), detail)


async def arm_interrupt(client: Any, rec: Recorder, scratch: Path) -> ArmResult:
    first, second = scratch / "interrupt-1.done", scratch / "interrupt-2.done"
    await _send(client, rec, _long_bash(first), "first")
    started = await rec.until(lambda: rec.called("Bash"), TURN_S)
    await _send(client, rec, _touch(second), "second (queued)")
    await _pause(1.0)
    rec.add(Event(rec.now(), "interrupt"))
    await client.interrupt()
    await _pause(30.0)
    before = rec.results()
    await _send(client, rec, ONE_WORD, "follow-up")
    await rec.until(lambda: rec.results() > before, TURN_S)
    await _pause(3.0)
    turns = classify(rec.events)
    reasons = [
        e.terminal_reason or "-"
        for e in rec.events
        if e.kind == "message" and e.type == "ResultMessage"
    ]
    v = (
        f"{turns.queries} queries -> {len(turns.prompted)} prompted results, "
        f"{len(turns.injected)} injected, {len(turns.unpaired)} unpaired, "
        f"{turns.outstanding} never answered; terminal reasons {reasons}"
    )
    detail = (
        f"first's Bash seen before the interrupt: {started}; markers: first "
        f"{first.exists()}, second (queued prompt ran) {second.exists()}"
    )
    return ArmResult("interrupt", v, tuple(rec.events), detail)


ARMS: dict[str, Callable[[Any, Recorder, Path], Any]] = {
    "control": arm_control,
    "bg-bash": arm_bg_bash,
    "bg-agent": arm_bg_agent,
    "monitor": arm_monitor,
    "queued": arm_queued,
    "interrupt": arm_interrupt,
}


async def run_arm(
    name: str, base: Path, model: str, operator_login: bool
) -> tuple[ArmResult, Recorder]:
    from claude_agent_sdk import ClaudeSDKClient

    scratch = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=base))
    cwd, config = scratch / "cwd", scratch / "config"
    cwd.mkdir()
    config.mkdir()
    rec = Recorder()
    options = probe_options(cwd, None if operator_login else config, model, rec)
    async with ClaudeSDKClient(options=options) as client:
        reader = asyncio.ensure_future(_read(client, rec))
        try:
            result = await ARMS[name](client, rec, cwd)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
    (scratch / "events.jsonl").write_text("".join(e.to_json() + "\n" for e in rec.events))
    return result, rec


# ------------------------------------------------------------------------ self-check

# A recorded shape, written out by hand from the SDK's own documented fields: a
# prompted turn that starts a background Bash, then the CLI's notification and an
# injected turn with no query before it, then the follow-up. ``--self-check`` and
# the unit tests classify this; it is not a measurement.
SAMPLE: tuple[Event, ...] = (
    Event(0.0, "query", label="start"),
    Event(0.4, "message", "SystemMessage", "init"),
    Event(1.9, "message", "StreamEvent", "message_start"),
    Event(2.6, "message", "AssistantMessage", "tool_use", tools=("Bash",)),
    Event(2.7, "hook", "PreToolUse", "Bash"),
    Event(2.9, "message", "UserMessage"),
    Event(3.8, "message", "AssistantMessage", "end_turn"),
    Event(3.9, "message", "ResultMessage", "success", terminal_reason="completed"),
    Event(18.9, "message", "TaskNotificationMessage", "task_notification", label="completed"),
    Event(19.0, "message", "UserMessage", origin="task-notification"),
    Event(20.4, "message", "StreamEvent", "message_start"),
    Event(21.0, "message", "AssistantMessage", "end_turn"),
    Event(21.1, "message", "ResultMessage", "success", origin="task-notification"),
    Event(63.9, "mark", label="window closed"),
    Event(64.0, "query", label="follow-up"),
    Event(65.5, "message", "AssistantMessage", "end_turn"),
    Event(65.6, "message", "ResultMessage", "success", terminal_reason="completed"),
)


def self_check() -> int:
    print("self-check: the built-in sample, classified (no CLI started)")
    for row in table(SAMPLE):
        print(row)
    turns = classify(SAMPLE)
    print(f"  -> {turns}")
    expected = (
        turns.queries == 2
        and turns.prompted == (3.9, 65.6)
        and turns.injected == ((21.1, "task-notification"),)
        and turns.unprompted_starts == (19.0,)
        and turns.outstanding == 0
    )
    print("self-check:", "ok" if expected else "FAILED")
    return 0 if expected else 1


# ------------------------------------------------------------------------------ main


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default="")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--base", default="")
    parser.add_argument("--operator-login", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        return self_check()
    arms = [a for a in args.arms.split(",") if a]
    unknown = [a for a in arms if a not in ARMS]
    if unknown:
        parser.error(f"unknown arms {unknown}; known: {list(ARMS)}")
    model = args.model
    if not model:
        sys.path.insert(0, str(ROOT))
        from pptmstr.model_catalog import DEFAULT_MODEL

        model = DEFAULT_MODEL
    base = Path(args.base) if args.base else Path(tempfile.mkdtemp(prefix="unprompted-turn-"))
    base.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        rec = Recorder()
        config = None if args.operator_login else base / "config"
        options = probe_options(base / "cwd", config, model, rec)
        print(f"model {options.model}; cwd {options.cwd}; env {options.env}")
        print(
            f"permission_mode {options.permission_mode}; partial {options.include_partial_messages}"
        )
        print(f"hooks {sorted(options.hooks)}; mcp {sorted(options.mcp_servers)}")
        print(f"arms {arms} x {args.repeat}; no CLI started")
        return 0

    present = [v for v in AUTH_VARIABLES if os.environ.get(v)]
    if not present and not args.operator_login:
        print(
            "refused: neither ANTHROPIC_API_KEY nor CLAUDE_CODE_OAUTH_TOKEN is set. The "
            "CLI runs with a scratch CLAUDE_CONFIG_DIR, so it cannot use a stored login; "
            "--operator-login uses the operator's own (see the module docstring)."
        )
        return 2

    summary: list[str] = []
    for n in range(1, args.repeat + 1):
        for name in arms:
            try:
                result, rec = asyncio.run(run_arm(name, base, model, args.operator_login))
            except Exception as exc:  # noqa: BLE001 - one arm's failure is its row
                summary.append(
                    f"{name} #{n}: inconclusive: the arm raised {type(exc).__name__}: {exc}"
                )
                continue
            print(
                f"\n== {name} #{n}  (CLI {rec.cli_version or '?'}, {len(rec.stderr)} stderr lines)"
            )
            for row in table(result.events):
                print(row)
            line = f"{name} #{n}: {result.verdict}"
            if result.detail:
                line += f"  [{result.detail}]"
            summary.append(line)
    print(f"\nlogs under {base}")
    print(f"model {model}")
    print("config", "the operator's own (--operator-login)" if args.operator_login else "scratch")
    print("SUMMARY")
    for line in summary:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
