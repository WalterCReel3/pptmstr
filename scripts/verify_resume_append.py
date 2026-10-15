#!/usr/bin/env python3
"""
Does a resumed session receive the resuming launch's system-prompt append?

`scripts/verify_resume_containment.py` asks which launch's system prompt a resumed
model answers to and reads the answer VOID whenever the first launch's canary could
have reached the reply through history -- which is every run, since the CLI records
the system prompt in the transcript. Its one live run saw only the first launch's
canary in both directions. This asks the half of that question that history cannot
answer for: **is the resuming launch's own append in front of the resumed model?**

**The marker that decides is one history cannot hold.** Every launch's append carries
a `PROBE_MARKER=` value minted for that launch. The resuming launch's value is in its
append and nowhere else: not in either user prompt, not in the first launch's append,
not in the transcript the resume loads. So if it is in the resumed reply, the append
was received; the first launch's marker being there as well says nothing either way
and is not read.

**What stands between its absence and the words NOT RECEIVED.**

  control    the fresh launch is asked the same question and must report its own
             marker. If it does not, the model does not answer this question from
             its system prompt at all and a missing marker means nothing: VOID.
  decoy      a marker minted and passed to nothing. In any reply, the model is
             inventing values: VOID.
  identity   the resumed launch must end under the first launch's id. A CLI that
             quietly started a new session would be a fresh launch, which receives
             its append by construction: VOID.
  sent       the resuming process's own argv is read from /proc while it runs. If
             the marker is not on it -- and the same reading found the fresh
             launch's marker on the fresh process's, which is what shows the reader
             can see one -- the CLI was never handed the append and the reading is
             NOT SENT: a fault on this side of the process boundary, not the CLI's.
             An argv that could not be read is printed and does not gate.

**A positive control for the method itself.** `claude_agent_sdk` 0.2.164 documents a
`snapshot` key on the system-prompt preset: omitted, "every later request, including
after resume, sends the recorded prompt, so changing `append` has no effect"; False,
"the prompt is rebuilt on every request". That is a reading of the SDK's types and
not a measurement. Two arms pass `snapshot: False` -- on the resuming launch alone,
and on both launches. If either reads RECEIVED, the question and the reading can see
a received append on a resume, which is what makes NOT RECEIVED in the default arms
a finding about the default rather than about this script.

**The arms.**

  forward   SDK direct, no snapshot key.   fresh uncontained -> resumed CONTAINED
  reverse   SDK direct, no snapshot key.   fresh CONTAINED   -> resumed uncontained
  resume    SDK direct, snapshot False on the resuming launch only
  both      SDK direct, snapshot False on both launches
  driver    through `pptmstr.driver.AgentSession.run` on a real `Bridge`, which is
            how pptmstr launches and resumes. The append is the briefing
            `templates.lead_briefing` builds from a template, here one with no roles
            whose `lead_prompt` carries the marker. The snapshot key is whatever
            `AgentSession._system_prompt` set, read back from it and printed.

**Two arms ask the opposite question: is the RECORDED append gone when the resuming
launch builds none?** A session started with a briefing and resumed solo.

  solo-default  SDK direct. The resuming launch passes `system_prompt=None`, which
                is what a pptmstr launch with no briefing passed before it set the
                snapshot key on a resume.
  driver-solo   through `AgentSession.run`: fresh with the marker template, resumed
                with no template.

Their reading is the first launch's marker in the resumed reply, and it is weaker
than the other arms' in both directions. Present, it may have come from history --
the first reply is in the transcript the resume loads -- although the prompt tells
the model not to repeat it. Absent, it is an absence. What the pair can show is a
difference between the two under the same question, and one run of each is all the
difference is.

The first four build `ClaudeAgentOptions` as `scripts/verify_resume_containment.py`
does, which is `AgentSession._options` less `agents`, `mcp_servers`,
`strict_mcp_config`, `thinking` and partial messages; forward and reverse are that
script's two directions. Their hook denies every tool call. The driver arm changes
nothing about the launch: the options, the hooks and the identity check are the
driver's own, and a tool call the model makes there is parked at the real gate and
denied by this script standing in for the operator.

**What a verdict here is about.** One run of each arm, one model (Haiku), one bundled
CLI build, a one-line reply and no tool use, a resume in the same cwd with no
compaction in between. RECEIVED and NOT RECEIVED are about whether the marker text
reached the model's reply; nothing here reads the request the CLI sent. The
transcript is read for how many `prompt_snapshot` records it holds before and after
the resume and whether the new marker is anywhere in it, and both are printed as
observations: absence from a transcript is not absence from a request.

**Status: RUN AGAINST LIVE SESSIONS, EACH ARM ONCE PER DRIVER STATE**, 2026-10-12,
Haiku on CLI 2.1.292 with SDK 0.2.164, from a shell that is not sandboxed. Two runs:
the first five arms with a driver that set no snapshot key, then `driver` and the two
solo arms with a driver that sets it on a resume. Every launch completed its turn,
every control landed and every argv reading found its marker. The verdicts are in
each run's own output and are not restated here. The four SDK-direct append arms
have one run; `driver` has one under each driver.

Usage:  .venv/bin/python scripts/verify_resume_append.py [--base DIR] [--keep]
            [--model ID] [--arms forward,reverse,...] [--spend-cap USD]

`--base` is the directory the fixture directory is created in (default: the system
temp directory). `--spend-cap` stops further arms from launching once the measured
spend has reached it; an arm already running is not interrupted.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

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
    TextBlock,
    project_key_for_directory,
)
from claude_agent_sdk.types import SystemPromptPreset  # noqa: E402

from pptmstr.bridge import Bridge, Decision  # noqa: E402
from pptmstr.cli_version import read_cli_version, resolve_cli_path  # noqa: E402
from pptmstr.driver import AgentSession  # noqa: E402
from pptmstr.intents import AgentFinished, StateChanged, UsageAccrued  # noqa: E402
from pptmstr.model import AgentState, ApprovalNeeded  # noqa: E402
from pptmstr.sandbox import containment_settings  # noqa: E402
from pptmstr.store import Store  # noqa: E402
from pptmstr.templates import SOLO, WorkTemplate  # noqa: E402
from pptmstr.transcript import SegmentKind  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
ARMS = ("forward", "reverse", "resume", "both", "driver", "solo-default", "driver-solo")
# The arms whose resuming launch builds no append, read by `recorded_verdict`.
SOLO_ARMS = ("solo-default", "driver-solo")
SPEND_CAP_USD = 0.80

TRANSCRIPT_DEADLINE_S = 60.0
POLL_S = 0.25
RUN_DEADLINE_S = 180.0
# One reply, and room for a tool call the hook denies.
MAX_TURNS = 4

MARKER = re.compile(r"PM-[0-9a-f]{14}")

RECEIVED = "RECEIVED"
NOT_RECEIVED = "NOT RECEIVED"
NOT_SENT = "NOT SENT"
VOID = "VOID"
STILL_REPORTED = "STILL REPORTED"
NOT_REPORTED = "NOT REPORTED"


def mint() -> str:
    return f"PM-{uuid.uuid4().hex[:14]}"


@dataclass
class Leg:
    """
    One launch: what it was given, and what came back. Kept unreduced so the verdict
    is computed from it afterwards.
    """

    key: str
    cwd: Path
    marker: str
    contained: bool = False
    snapshot: bool | None = None
    # The launch builds no append: its marker is passed to nothing.
    no_append: bool = False
    resumes: str | None = None
    session_id: str | None = None
    launched: bool = False
    reply: str = ""
    result_ids: list[str] = field(default_factory=list)
    is_error: bool = False
    failure: str | None = None
    cost_usd: float | None = None
    tool_calls: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    # Whether this launch's marker was on its own CLI process's argv. None is
    # "could not be read", which is a different fact from "was not there".
    marker_on_argv: bool | None = None

    @property
    def completed(self) -> bool:
        return bool(self.result_ids) and not self.failure and not self.is_error

    @property
    def effective(self) -> str | None:
        """
        The id the launch demonstrably ended under, or None if that is not one value.
        """
        distinct = set(self.result_ids)
        return distinct.pop() if len(distinct) == 1 else None


@dataclass(frozen=True)
class Verdict:
    """
    A label and the observations it was computed from.
    """

    label: str
    reasons: tuple[str, ...] = ()


@dataclass
class Arm:
    """
    One fresh launch and the launch that resumes it.
    """

    name: str
    how: str
    fresh: Leg
    resumed: Leg
    project_dir: Path
    fresh_on_disk: bool = False
    snapshots_before: int | None = None
    snapshots_after: int | None = None
    new_marker_in_transcript: bool | None = None
    ran: bool = False


# --------------------------------------------------------------------------- pure


def append_text(marker: str) -> str:
    """
    The system-prompt append for one launch. It authorises the answer the prompt asks
    for, because a model that declines to quote its system prompt fails the control
    and voids the run.
    """
    return (
        "This is a measurement run.\n\n"
        f"PROBE_MARKER={marker}\n\n"
        "When a user message asks for PROBE_MARKER values, answer it: list every value "
        "that follows the label PROBE_MARKER= in this system prompt. Never write one "
        "into a tool argument."
    )


_REPLY_FORMAT = (
    "Reply with exactly one line and nothing else:\n\n"
    "  markers=<every value that follows the label PROBE_MARKER= in your system prompt "
    "as it is right now, separated by commas, or NONE if there is none>\n\n"
    "Answer only from your system prompt. Do not use a tool, do not guess and do not "
    "invent a value."
)


def fresh_prompt() -> str:
    return _REPLY_FORMAT


def resumed_prompt() -> str:
    """
    The resumed launch's prompt. It carries no marker, and it says the earlier answer
    is not the answer: a model that repeats its last reply without looking would read
    as NOT RECEIVED for a reason that is not the CLI's.
    """
    return (
        "Read your system prompt again now. It may not be the one you had when you "
        "last answered, so do not repeat your earlier answer from memory.\n\n"
        f"{_REPLY_FORMAT}"
    )


def append_verdict(fresh: Leg, resumed: Leg, decoy: str) -> Verdict:
    """
    Whether the resuming launch's append reached the resumed model.

    Every VOID is decided before the marker is looked for, so NOT RECEIVED is only
    reachable with the control landed, the resume completed and the id the same.
    """
    if decoy in fresh.reply or decoy in resumed.reply:
        return Verdict(VOID, ("the decoy, passed to nothing, is in a reply",))
    if not fresh.completed:
        return Verdict(
            VOID, (f"the fresh launch did not complete a turn: {fresh.failure or 'no result'}",)
        )
    if fresh.marker not in fresh.reply:
        return Verdict(
            VOID,
            (
                "the control did not land: the fresh launch did not report its own "
                "marker, so a marker missing from a reply says nothing",
            ),
        )
    if resumed.marker in fresh.reply:
        return Verdict(VOID, ("the resuming launch's marker is in the FRESH reply",))
    if not resumed.completed:
        return Verdict(
            VOID,
            (f"the resumed launch did not complete a turn: {resumed.failure or 'no result'}",),
        )
    if fresh.session_id is None or resumed.effective != fresh.session_id:
        return Verdict(
            VOID,
            (
                f"not shown to be the same session: asked to resume {fresh.session_id}, "
                f"ended under {resumed.effective}",
            ),
        )
    seen = sorted(set(MARKER.findall(resumed.reply)))
    old = "also lists" if fresh.marker in seen else "does not list"
    if resumed.marker in seen:
        return Verdict(
            RECEIVED,
            (
                "the resumed reply carries the resuming launch's marker, which is in "
                "that launch's append and nowhere else",
                f"it {old} the first launch's marker",
            ),
        )
    # The fresh launch's own reading is what shows the argv reader can see a marker
    # at all; without it a "not there" is not distinguished from "cannot look".
    if resumed.marker_on_argv is False and fresh.marker_on_argv is True:
        return Verdict(
            NOT_SENT,
            (
                "the resuming launch's marker was not on its CLI process's argv, so the "
                "CLI was never handed that append",
            ),
        )
    sent = (
        "that marker was on the resuming process's argv"
        if resumed.marker_on_argv
        else "whether that marker was on the resuming process's argv is not established"
    )
    return Verdict(
        NOT_RECEIVED,
        (
            "the control landed, the resume completed under the same id, and the "
            "resuming launch's marker is not in the resumed reply",
            sent,
            f"the resumed reply {old} the first launch's marker (markers in it: {seen or 'none'})",
        ),
    )


def recorded_verdict(fresh: Leg, resumed: Leg, decoy: str) -> Verdict:
    """
    Whether the first launch's append is still what a resumed model reports when the
    resuming launch built none.

    The same VOIDs as ``append_verdict``, plus one: the resuming leg's marker was
    passed to nothing, so in a reply it is an invented value exactly as the decoy is.
    """
    if decoy in fresh.reply or decoy in resumed.reply or resumed.marker in resumed.reply:
        return Verdict(VOID, ("a marker passed to nothing is in a reply",))
    if not fresh.completed:
        return Verdict(
            VOID, (f"the fresh launch did not complete a turn: {fresh.failure or 'no result'}",)
        )
    if fresh.marker not in fresh.reply:
        return Verdict(
            VOID, ("the control did not land: the fresh launch did not report its marker",)
        )
    if not resumed.completed:
        return Verdict(
            VOID,
            (f"the resumed launch did not complete a turn: {resumed.failure or 'no result'}",),
        )
    if fresh.session_id is None or resumed.effective != fresh.session_id:
        return Verdict(
            VOID,
            (
                f"not shown to be the same session: asked to resume {fresh.session_id}, "
                f"ended under {resumed.effective}",
            ),
        )
    if fresh.marker in resumed.reply:
        return Verdict(
            STILL_REPORTED,
            (
                "the resumed reply lists the first launch's marker, and the resuming "
                "launch built no append",
                "the marker is also in the history the resume loaded, so this does not "
                "by itself show the recorded prompt was sent",
            ),
        )
    return Verdict(
        NOT_REPORTED,
        (
            "the control landed, the resume completed under the same id, and the first "
            "launch's marker is not in the resumed reply",
            "an absence: it does not by itself show the recorded prompt was not sent",
        ),
    )


def method_verdict(verdicts: dict[str, Verdict]) -> Verdict:
    """
    Whether this run showed that the question can see a received append on a resume.
    """
    shown = [name for name, verdict in verdicts.items() if verdict.label == RECEIVED]
    if shown:
        return Verdict(
            "SHOWN", (f"a resumed launch reported its own marker in: {', '.join(shown)}",)
        )
    return Verdict(
        "NOT SHOWN",
        (
            "no arm of this run read RECEIVED, so nothing here shows the question can "
            "detect an append a resumed session did receive",
        ),
    )


# ---------------------------------------------------------------------- imperative


def _projects_dir() -> Path:
    """
    Where the CLI writes transcripts. The subprocess inherits this environment, so a
    set `CLAUDE_CONFIG_DIR` moves its writes.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(override) if override else Path.home() / ".claude") / "projects"


def _transcript_text(project_dir: Path, session_id: str) -> str:
    try:
        return (project_dir / f"{session_id}.jsonl").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _snapshot_records(text: str) -> int:
    return text.count('"type":"prompt_snapshot"')


def _await_on_disk(project_dir: Path, session_id: str, needle: str) -> bool:
    end = time.monotonic() + TRANSCRIPT_DEADLINE_S
    while time.monotonic() < end:
        if needle in _transcript_text(project_dir, session_id):
            return True
        time.sleep(POLL_S)
    return False


def _marker_on_child_argv(marker: str) -> bool | None:
    """
    Whether any live child of this process has ``marker`` on its command line, read
    from /proc. None when no child's command line could be read at all.
    """
    me = str(os.getpid())
    read_any = False
    try:
        entries = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        return None
    for pid in entries:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            # The parent pid is the second field after the parenthesised name, which
            # can itself hold spaces and parentheses.
            if stat.rsplit(")", 1)[1].split()[1] != me:
                continue
            argv = Path(f"/proc/{pid}/cmdline").read_bytes()
        except (OSError, IndexError):
            continue
        if not argv:
            continue
        read_any = True
        if marker.encode() in argv:
            return True
    return False if read_any else None


def _system_prompt(leg: Leg) -> SystemPromptPreset | None:
    if leg.no_append:
        return None
    preset: SystemPromptPreset = {
        "type": "preset",
        "preset": "claude_code",
        "append": append_text(leg.marker),
    }
    if leg.snapshot is not None:
        preset["snapshot"] = leg.snapshot
    return preset


async def run_sdk(leg: Leg, prompt: str, model: str) -> None:
    async def deny(
        hook_input: HookInput, tool_use_id: str | None, ctx: HookContext
    ) -> HookJSONOutput:
        data = cast(dict[str, Any], hook_input)
        leg.tool_calls.append(str(data.get("tool_name")))
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Denied by the probe: answer without a tool.",
            }
        }

    options = ClaudeAgentOptions(
        model=model,
        cwd=str(leg.cwd),
        # Exactly one of the two, as `AgentSession._options` builds it.
        resume=leg.resumes,
        session_id=None if leg.resumes is not None else leg.session_id,
        system_prompt=_system_prompt(leg),
        settings=containment_settings() if leg.contained else None,
        stderr=lambda line: leg.stderr.append(line),
        permission_mode="dontAsk",
        max_turns=MAX_TURNS,
        hooks={"PreToolUse": [HookMatcher(hooks=[deny], timeout=600)]},
    )
    leg.launched = True
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            leg.marker_on_argv = _marker_on_child_argv(leg.marker)
            async with asyncio.timeout(RUN_DEADLINE_S):
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                leg.reply += block.text
                    if isinstance(message, ResultMessage):
                        leg.result_ids.append(message.session_id)
                        leg.is_error = message.is_error
                        leg.cost_usd = message.total_cost_usd
                        if message.result and message.result.strip() not in leg.reply:
                            leg.reply += f"\n{message.result}"
    except TimeoutError:
        leg.failure = f"TimeoutError: no ResultMessage within {RUN_DEADLINE_S:.0f}s"
    except Exception as exc:  # noqa: BLE001 -- the failure IS the observation
        leg.failure = f"{type(exc).__name__}: {exc}"[:400]


def run_driver(leg: Leg, prompt: str, model: str) -> None:
    """
    One launch through ``AgentSession.run`` on its own ``Bridge``, with the frame
    loop of ``app.py`` less the UI: drain, apply, settle.

    The turn is over at the root's first ``AWAITING_INPUT``. The reply is the root
    transcript's OUTPUT segments, which is what the operator would have been shown.
    A session that ends first has failed, and what it ended with is the failure.
    """
    bridge = Bridge()
    bridge.start()
    store = Store()
    session = AgentSession(
        bridge,
        task=prompt,
        model=model,
        cwd=str(leg.cwd),
        # No roles, so no team and no agents: the briefing is this text and nothing
        # else, and it reaches the CLI through `AgentSession._system_prompt`.
        template=(
            SOLO
            if leg.no_append
            else WorkTemplate(
                name="probe",
                description="resume-append probe",
                lead_prompt=append_text(leg.marker),
            )
        ),
        resume=leg.resumes,
    )
    # Read from what the driver hands the SDK, so the printed key is the driver's
    # and not this script's account of it.
    handed = session._system_prompt()
    leg.snapshot = cast(dict[str, Any], handed).get("snapshot") if handed is not None else None
    if leg.resumes is None:
        leg.session_id = session.session_id
    session.announce()
    running = bridge.submit(session.run())
    leg.launched = True
    cost = 0.0
    turn_over = False
    denied: set[str] = set()
    deadline = time.monotonic() + RUN_DEADLINE_S
    try:
        while time.monotonic() < deadline and not turn_over:
            intents = bridge.drain()
            for intent in intents:
                if isinstance(intent, UsageAccrued) and intent.node_id == session.node_id:
                    cost += intent.delta.total_cost_usd
                if isinstance(intent, StateChanged) and intent.node_id == session.node_id:
                    if intent.state is AgentState.AWAITING_INPUT:
                        turn_over = True
                if isinstance(intent, AgentFinished) and intent.node_id == session.node_id:
                    leg.failure = f"the session ended in {intent.state.value}: {intent.error}"
            for effect in store.apply_all(intents):
                bridge.settle(effect)
            for obligation in store.snapshot().needs_you:
                if isinstance(obligation, ApprovalNeeded) and obligation.approval.id not in denied:
                    denied.add(obligation.approval.id)
                    leg.tool_calls.append(obligation.summary)
                    bridge.resolve(
                        obligation.approval.id,
                        Decision(approved=False, reason="the probe answers without a tool"),
                    )
            if leg.marker_on_argv is not True:
                # Until it is seen, and not once: between fork and exec the child's
                # command line is still this script's.
                seen = _marker_on_child_argv(leg.marker)
                leg.marker_on_argv = seen if seen is not None else leg.marker_on_argv
            if running.done() or leg.failure:
                break
            time.sleep(0.01)
        if not turn_over and leg.failure is None:
            leg.failure = (
                "the run ended before a turn did"
                if running.done()
                else f"no AWAITING_INPUT within {RUN_DEADLINE_S:.0f}s"
            )
        transcript = session.transcript
        leg.reply = "".join(
            transcript.read(seg.start, seg.end)
            for seg in transcript.segments()
            if seg.kind is SegmentKind.OUTPUT
        )
        if turn_over:
            # ``_check_identity`` raises on a resumed id that is not the one asked
            # for, which ends the run in FAILED before any turn. A turn that ended
            # therefore ended under the session's own id; it is recorded as that and
            # not as a second, independent reading of the id.
            leg.result_ids.append(session.session_id)
        leg.cost_usd = cost
    finally:
        # Asked-for teardown, so the run's cancellation reports DONE and the CLI
        # process is gone before the next launch reads the transcript.
        session.teardown_requested = True
        for effect in store.apply_all(bridge.drain()):
            bridge.settle(effect)
        bridge.stop()


def build_arm(name: str, base: Path) -> Arm:
    cwd = base / f"{name}-cwd"
    cwd.mkdir()
    first, second = f"{name}-1", f"{name}-2"
    session_id = str(uuid.uuid4())
    fresh = Leg(first, cwd, mint(), session_id=session_id)
    resumed = Leg(second, cwd, mint(), resumes=session_id)
    how = "SDK direct, no snapshot key"
    match name:
        case "forward":
            resumed.contained = True
            how += "; fresh uncontained -> resumed CONTAINED"
        case "reverse":
            fresh.contained = True
            how += "; fresh CONTAINED -> resumed uncontained"
        case "resume":
            resumed.snapshot = False
            how = "SDK direct, snapshot False on the resuming launch only; both uncontained"
        case "both":
            fresh.snapshot = resumed.snapshot = False
            how = "SDK direct, snapshot False on both launches; both uncontained"
        case "driver":
            # The driver mints the id; it is read back after the fresh launch.
            fresh.session_id = None
            resumed.resumes = None
            how = "through pptmstr.driver.AgentSession.run; both uncontained"
        case "solo-default":
            resumed.no_append = True
            how = "SDK direct; the resuming launch passes system_prompt=None; both uncontained"
        case "driver-solo":
            fresh.session_id = None
            resumed.resumes = None
            resumed.no_append = True
            how = (
                "through pptmstr.driver.AgentSession.run; the resuming launch has no "
                "template; both uncontained"
            )
        case _:
            raise SystemExit(f"unknown arm {name!r}")
    return Arm(name, how, fresh, resumed, _projects_dir() / project_key_for_directory(cwd))


def run_arm(arm: Arm, model: str) -> None:
    fresh, resumed = arm.fresh, arm.resumed
    first, second = fresh_prompt(), resumed_prompt()
    for marker in (fresh.marker, resumed.marker):
        if marker in first or marker in second:
            raise SystemExit("probe defect: a prompt carries a marker")
    arm.ran = True
    if arm.name.startswith("driver"):
        run_driver(fresh, first, model)
        resumed.resumes = fresh.session_id
    else:
        asyncio.run(run_sdk(fresh, first, model))
    asked = fresh.session_id or ""
    # The first process is gone by here. Its reply holds its marker when the control
    # landed, so this is also the wait for the turn to be on disk.
    arm.fresh_on_disk = bool(asked) and _await_on_disk(arm.project_dir, asked, fresh.marker)
    before = _transcript_text(arm.project_dir, asked)
    arm.snapshots_before = _snapshot_records(before)
    if resumed.marker in before:
        raise SystemExit("probe defect: the resuming marker is on disk before the resume")
    if not asked:
        resumed.failure = "the fresh launch left no session id to resume"
        return
    if arm.name.startswith("driver"):
        run_driver(resumed, second, model)
    else:
        asyncio.run(run_sdk(resumed, second, model))
    # A little time for the resumed turn to be flushed; the readings below are
    # observations and do not gate anything.
    end = time.monotonic() + 10.0
    after = _transcript_text(arm.project_dir, asked)
    while time.monotonic() < end and len(after) == len(before):
        time.sleep(POLL_S)
        after = _transcript_text(arm.project_dir, asked)
    arm.snapshots_after = _snapshot_records(after)
    arm.new_marker_in_transcript = resumed.marker in after


# -------------------------------------------------------------------------- report


def _argv_line(leg: Leg) -> str:
    return {True: "yes", False: "NO", None: "could not be read"}[leg.marker_on_argv]


def describe(leg: Leg, what: str) -> None:
    print(f"  [{leg.key}] {what}")
    if not leg.launched:
        print("      not launched")
        return
    snapshot = "omitted" if leg.snapshot is None else str(leg.snapshot)
    print(
        f"      marker {leg.marker}   contained={leg.contained}   snapshot key: {snapshot}"
        f"   session {leg.session_id or leg.resumes}"
    )
    print(f"      ended under {leg.result_ids or 'NO RESULT'}   is_error={leg.is_error}")
    if leg.no_append:
        print("      builds no append: its marker is passed to nothing")
    else:
        print(f"      own marker on the CLI process's argv: {_argv_line(leg)}")
    print(f"      reply: {leg.reply.strip()[:300]!r}")
    if leg.tool_calls:
        print(f"      tool calls (all denied): {leg.tool_calls}")
    if leg.failure:
        print(f"      FAILED: {leg.failure}")
    if leg.cost_usd is not None:
        print(f"      cost reported: {leg.cost_usd:.6f} USD")
    for line in leg.stderr[-3:]:
        print(f"      stderr: {line[:200]}")


def report(arms: list[Arm], decoy: str, skipped: list[str]) -> None:
    print(f"\n{'=' * 78}\nRESULT -- n = 1 run of each arm\n{'=' * 78}")
    verdicts: dict[str, Verdict] = {}
    for arm in arms:
        print(f"\n{arm.name} -- {arm.how}")
        describe(arm.fresh, "fresh")
        describe(arm.resumed, "resumes it")
        control = "LANDED" if arm.fresh.marker in arm.fresh.reply else "DID NOT LAND"
        print(f"  control (the fresh launch reports its own marker): {control}")
        print(f"  fresh turn on disk before the resume: {arm.fresh_on_disk}")
        print(
            "  observed on disk, not part of the verdict: prompt_snapshot records "
            f"{arm.snapshots_before} before the resume, {arm.snapshots_after} after; "
            f"resuming marker anywhere in the transcript: {arm.new_marker_in_transcript}"
        )
        if arm.name in SOLO_ARMS:
            verdict = recorded_verdict(arm.fresh, arm.resumed, decoy)
            print(f"  RECORDED APPEND AFTER A SOLO RESUME [{arm.name}]: {verdict.label}")
        else:
            verdict = append_verdict(arm.fresh, arm.resumed, decoy)
            print(f"  APPEND ON RESUME [{arm.name}]: {verdict.label}")
        verdicts[arm.name] = verdict
        for reason in verdict.reasons:
            print(f"       - {reason}")
    for name in skipped:
        print(f"\n{name} -- NOT RUN: the spend cap was reached before it launched")

    print("\nsummary")
    for name, verdict in verdicts.items():
        print(f"  {name:12} {verdict.label}")
    asked = {name: v for name, v in verdicts.items() if name not in SOLO_ARMS}
    if asked:
        method = method_verdict(asked)
        print(f"  the question can see a received append on a resume: {method.label}")
        for reason in method.reasons:
            print(f"       - {reason}")
    legs = [leg for arm in arms for leg in (arm.fresh, arm.resumed)]
    spent = sum(leg.cost_usd or 0.0 for leg in legs)
    launched = sum(1 for leg in legs if leg.launched)
    print(
        f"\ncost reported: {spent:.4f} USD over {launched} launches, summed per launch. "
        "Whether a resumed process reports its own spend or a running total for the "
        "session is not established here, so treat this as an upper bound."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--base", type=Path, default=None)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--spend-cap", type=float, default=SPEND_CAP_USD)
    args = parser.parse_args()

    names = [name for name in args.arms.split(",") if name]
    for name in names:
        if name not in ARMS:
            parser.error(f"unknown arm {name!r}; the arms are {', '.join(ARMS)}")

    if args.base is not None:
        args.base.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="pptmstr-resume-append-", dir=args.base)).resolve()
    if base.is_relative_to(ROOT):
        raise SystemExit(f"the fixture {base} is inside this checkout; pass --base elsewhere")

    cli = resolve_cli_path()
    print(f"CLI spawned: {cli}   version: {read_cli_version(cli)}")
    print(f"model: {args.model}   fixture: {base}")

    decoy = mint()
    arms: list[Arm] = []
    skipped: list[str] = []
    try:
        for name in names:
            spent = sum(leg.cost_usd or 0.0 for arm in arms for leg in (arm.fresh, arm.resumed))
            if spent >= args.spend_cap:
                skipped.append(name)
                continue
            arm = build_arm(name, base)
            arms.append(arm)
            print(f"running {name} ...", flush=True)
            run_arm(arm, args.model)
        report(arms, decoy, skipped)
    finally:
        if args.keep:
            print(f"\nfixture kept at {base}")
        else:
            shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
