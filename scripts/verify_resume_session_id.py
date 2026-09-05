#!/usr/bin/env python3
"""
Which session id does the CLI run under when ``--session-id``, ``--resume`` and
``--fork-session`` all reach argv together?

``planning/2026-08-22-session-controls-and-the-mode-dial.md`` D8 calls this the
blocking unknown and names it probe 2. Reading settles that the combination is
*legal* and nothing more: ``_build_command`` emits ``--resume=``, ``--session-id=``
and ``--fork-session`` from three independent blocks with no mutual-exclusion
check, and ``ClaudeAgentOptions.session_id``'s docstring says the trio is allowed
while saying nothing about which id survives. The arbitration is inside the
bundled binary. D8 routes on the answer: **supplied id honoured** and resume keeps
NodeId stability and is a small change; **not honoured** and the fork has to happen
before the session is constructed.

D8 asks it as a binary. It is not one, and forcing a third behaviour into two
buckets is how a probe produces a confident wrong answer. This script reports four:

  HONOURED   ``ID_B`` is the effective id and ``<ID_B>.jsonl`` is a new file.
  MINTED     the effective id is neither supplied id -- the CLI made its own.
  IN-PLACE   the effective id is ``ID_A``, no new transcript, and A's file grew:
             there was no fork, the session simply continued.
  REJECTED   run B never reached handshake. The CLI refused the combination.
             **This is not "not honoured".** It selects a third route D8 does not
             name, and reporting it as the second one would send the design down a
             path the run never tested.
  STALLED    run B reached handshake and then never completed a turn. Split out
             from REJECTED because "the CLI would not take these flags" and "the
             CLI took them and hung" are different facts about the design, and the
             second one settles nothing at all.

and a sixth, DISAGREEMENT, when the two readings below do not match. That is
reported as the finding rather than resolved by preferring one.

**Two readings, deliberately independent.**

  *messages*  ``ResultMessage.session_id`` -- emitted after the turn is over, so
              after arbitration has certainly happened.
  *disk*      which ``<uuid>.jsonl`` files appear under the project directory that
              were not there before run B.

The ``init`` ``SystemMessage`` is **captured and printed but never enters the
verdict.** It is emitted at handshake, which makes it the CLI acknowledging the
argv it was just handed -- the exact shape of the defect
``planning/2026-08-22-four-items-buy-back-session-time.md``'s addendum found in
``verify_hook_timeout.py``, where the verdict came off the tool-use *request* and
would have passed the run it existed to catch. A CLI that echoes ``--session-id``
at init and then arbitrates for a different id afterwards reads as HONOURED from
the init frame alone. Here that CLI shows up as an init/result mismatch, printed
under its own heading.

The transcript's *per-entry* ``sessionId`` is also not a reading.
``_internal/session_mutations.py`` rewrites it on every copied line when the SDK
forks; whether the CLI does the same is unknown, so copied history may carry run
A's id under a filename that is run B's. Filename and entry-``sessionId`` are not
the same measurement and only the filename is used.

**What would flip the verdict.** ``ID_B`` is a ``uuid4`` minted in this process a
few hundred microseconds before the run and written nowhere except run B's argv.
A CLI that ignored ``--session-id`` cannot produce a file named ``<ID_B>.jsonl``
and cannot put ``ID_B`` in a ``ResultMessage``; a CLI that honours it cannot avoid
doing both. That is the discriminator, and it is checked rather than asserted:
``ID_DECOY`` is a third ``uuid4`` minted beside the other two and **passed to
nothing**. If it turns up in any captured id or on disk, the reading is
contaminated and the run is INCONCLUSIVE instead of a verdict. Run A is the other
half -- ``session_id=ID_A`` alone, which is exactly what ``driver._options`` does
in production -- and if that id is not honoured, nothing about run B is readable
and the script stops there.

**Q2 -- did history actually arrive?** If the fork carries no conversation, Q1 is
a fact about an empty session and D8's premise is void. Two nonces go into run A,
both ``uuid4``-derived so neither is guessable:

  ALPHA  run A is asked to echo it, and run B is asked to recall it. This reads
         the model's *context*.
  BETA   named in run A's prompt and never mentioned again. This reads *disk*:
         BETA in run B's transcript is history copied into the fork, and it cannot
         be contaminated by run B's own answer because run B is never asked for it.

Every tool is denied at a ``PreToolUse`` hook, so neither nonce is reachable by
``Read``, ``Bash`` or ``Grep``. cwd is a fresh ``tempfile.mkdtemp`` and **nothing
is written into it** -- no ``CLAUDE.md``, no files at all -- so cwd contents cannot
carry a nonce either. Run B's prompt contains neither nonce, neither prefix, nor an
example of the shape. Run A's transcript is asserted to actually contain BETA
before a NONE from run B is read as "history did not arrive" rather than "there was
no history to arrive".

The hazard list proposed ``ClaudeAgentOptions.tools=[]`` as a second belt.
**Refused.** ``subprocess_cli.py`` turns it into ``--tools ""``, an argv shape
nothing here has ever run, and its failure mode is a CLI-level error -- which is
indistinguishable from REJECTED, a verdict this probe can legitimately produce. A
deny-all ``PreToolUse`` hook is the mechanism ``verify_worker_context.py`` already
proved, its deny is final, and it fires on every tool call regardless of permission
mode. Adding an untested flag on top of a proven one, directly over a real outcome,
buys nothing and can only turn a good one-shot run into a false finding.

**Q3, free.** ``<ID_A>.jsonl``'s size, mtime and digest are taken before and after
run B. Without it, "the effective id is A" is ambiguous between *no fork happened*
and *the fork wrote back into its source*.

``options.session_store`` is left unset. With it set, ``_internal/client.py``
materializes into a temp ``CLAUDE_CONFIG_DIR`` and overrides resume on a copy of
the options -- a path ``driver._options`` never takes, so the probe would measure
something production does not do. ``include_partial_messages``, ``agents``,
``mcp_servers`` and the system-prompt append are likewise left off: production sets
them, none of them can affect which id the CLI arbitrates, and each is one more way
for a nonce to reach run B or for a tool to exist.

**Bounds.** One run against one bundled CLI build on one machine settles which id
*that build* used for *this* argv ordering. It is not a property of the CLI, it is
not a promise about the next release, and it says nothing about the trio under a
``session_store``, under ``continue_conversation``, or with the ids supplied in a
different order. A HONOURED verdict also does not establish that the id is stable
across a *second* resume of the forked session -- that is a third run this probe
does not make.

Costs two real turns against trivial prompts.

Usage:  .venv/bin/python scripts/verify_resume_session_id.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
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
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    project_key_for_directory,
)
from claude_agent_sdk.types import HookInput, HookJSONOutput  # noqa: E402

# The two ids under test and the negative control. All three are minted here and
# ID_DECOY is passed to nothing, so its appearance anywhere is proof the readings
# are picking up something other than what was supplied.
ID_A = str(uuid.uuid4())
ID_B = str(uuid.uuid4())
ID_DECOY = str(uuid.uuid4())

# Prefix-free of anything in either prompt: a model cannot reconstruct one of these
# from the wording it is shown, only from the conversation it was given.
ALPHA_NONCE = f"zq{uuid.uuid4().hex[:14]}"
BETA_NONCE = f"zr{uuid.uuid4().hex[:14]}"

PROMPT_A = f"""\
Remember these two values for the rest of this conversation.

  ALPHA_VALUE = {ALPHA_NONCE}
  BETA_VALUE  = {BETA_NONCE}

Reply with exactly this line and nothing else:

  stored {ALPHA_NONCE}
"""

# Contains neither nonce, neither nonce prefix, and no example of the shape. It
# names only the label, which was chosen so that knowing it gives nothing away.
PROMPT_B = """\
Earlier in this conversation you were asked to remember a value under the label
ALPHA_VALUE. Reply with exactly this line and nothing else:

  recalled=<the value of ALPHA_VALUE>

If no such value appears anywhere in this conversation or in the context you were
given, reply with exactly this line instead:

  recalled=NONE

Answer only from what you were actually given. You have no tools -- every tool call
is denied and retrying one wastes the run. Do not guess and do not invent a value.
"""

# Long enough that a slow first write is not mistaken for a transcript that was
# never created, short enough that a genuinely absent file does not stall the run.
TRANSCRIPT_DEADLINE_S = 60.0
POLL_S = 0.25

# Denied tools can cost a retry turn even when the prompt forbids them.
MAX_TURNS = 4

# Generous for two trivial Haiku turns, and the difference between a probe that
# reports a stall and one that becomes a stall.
RUN_DEADLINE_S = 300.0


@dataclass
class Observation:
    """
    Everything one run put on the wire, kept unreduced so the verdict is computed
    from it rather than from a decision made while reading it.
    """

    label: str
    # Every session id seen anywhere in the stream, with counts. The hazard list's
    # "safer form": a single id read from a single frame cannot show a CLI that
    # changes its mind partway through, and a tally can.
    id_tally: dict[str, int] = field(default_factory=dict)
    # Kept apart from the tally because they are not equally trustworthy. `init`
    # is the handshake echo and is excluded from the verdict; `result` is emitted
    # after arbitration.
    init_ids: list[str] = field(default_factory=list)
    result_ids: list[str] = field(default_factory=list)
    text: str = ""
    is_error: bool = False
    result_text: str | None = None
    failure: str | None = None
    denied: list[str] = field(default_factory=list)

    @property
    def effective(self) -> str | None:
        """
        The id the run demonstrably ended under, or None if that is not single-valued.
        """
        distinct = set(self.result_ids)
        return distinct.pop() if len(distinct) == 1 else None


@dataclass(frozen=True)
class FileState:
    """One transcript on disk, pinned tightly enough to detect a rewrite in place."""

    size: int
    mtime_ns: int
    digest: str
    lines: int


def _projects_dir() -> Path:
    """
    Where the CLI writes transcripts.

    Mirrors ``_internal/sessions.py:_get_claude_config_home_dir``. The subprocess
    inherits this process's environment (``process_env`` merges ``os.environ`` and
    strips only ``CLAUDECODE``), so a set ``CLAUDE_CONFIG_DIR`` moves the CLI's
    writes and hardcoding ``~/.claude`` would look in the wrong place.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(override) if override else Path.home() / ".claude"
    return base / "projects"


def _snapshot(project_dir: Path) -> dict[str, FileState]:
    """Every transcript in the project directory, by session id."""
    state: dict[str, FileState] = {}
    try:
        entries = sorted(project_dir.glob("*.jsonl"))
    except OSError:
        return state
    for path in entries:
        try:
            raw = path.read_bytes()
            stat = path.stat()
        except OSError:
            continue
        state[path.stem] = FileState(
            size=len(raw),
            mtime_ns=stat.st_mtime_ns,
            digest=hashlib.sha256(raw).hexdigest()[:16],
            lines=raw.count(b"\n"),
        )
    return state


async def _await_nonce(path: Path, needle: str) -> bool:
    """
    Wait for the CLI's write of run A to land and to carry the canary.

    Both halves matter and neither implies the other: the file can exist while the
    turn's entries are still buffered, and a NONE from run B is only readable as
    "history did not arrive" once run A's history is known to contain the nonce.
    """
    end = time.monotonic() + TRANSCRIPT_DEADLINE_S
    while time.monotonic() < end:
        try:
            if needle in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
        await asyncio.sleep(POLL_S)
    return False


async def _run(
    label: str,
    prompt: str,
    *,
    model: str,
    cwd: Path,
    session_id: str,
    resume: str | None = None,
    fork_session: bool = False,
) -> Observation:
    observed = Observation(label=label)

    async def gate(
        hook_input: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        """
        The gate that makes Q2 mean something.

        Without it, a model reporting ALPHA would prove only that some file was
        readable -- not that conversation history reached its context.
        """
        observed.denied.append(str(cast(dict[str, Any], hook_input).get("tool_name") or ""))
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Denied by the probe. Answer from this conversation only. Do not retry."
                ),
            }
        }

    options = ClaudeAgentOptions(
        model=model,
        cwd=str(cwd),
        session_id=session_id,
        resume=resume,
        fork_session=fork_session,
        # driver._options's mode. The hook is what actually denies; this only
        # keeps the run from stalling on an interactive prompt.
        permission_mode="dontAsk",
        max_turns=MAX_TURNS,
        hooks={"PreToolUse": [HookMatcher(hooks=[gate], timeout=120)]},
    )

    def note(sid: object) -> None:
        if isinstance(sid, str) and sid:
            observed.id_tally[sid] = observed.id_tally.get(sid, 0) + 1

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            # `receive_response` is documented to run "indefinitely" if no
            # ResultMessage arrives. A CLI that accepts the three-flag argv and
            # then never completes a turn is a plausible way for this probe to
            # fail, and without a bound it would hang instead of reporting -- the
            # one outcome most likely to need reporting.
            async with asyncio.timeout(RUN_DEADLINE_S):
                async for message in client.receive_response():
                    note(getattr(message, "session_id", None))
                    if isinstance(message, SystemMessage):
                        # Plain SystemMessage carries no session_id attribute; the
                        # id lives in `data`. The subclasses have both, so both
                        # are read.
                        data_sid = message.data.get("session_id")
                        note(data_sid)
                        if message.subtype == "init" and isinstance(data_sid, str):
                            observed.init_ids.append(data_sid)
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                observed.text += block.text
                    if isinstance(message, ResultMessage):
                        observed.result_ids.append(message.session_id)
                        observed.is_error = message.is_error
                        observed.result_text = message.result
    except TimeoutError:
        observed.failure = f"TimeoutError: no ResultMessage within {RUN_DEADLINE_S:.0f}s"
    except Exception as exc:  # noqa: BLE001 - the reason is the measurement here
        observed.failure = f"{type(exc).__name__}: {exc}"

    return observed


async def main(model: str) -> int:
    # Empty and stays empty. cwd is what decides the project directory, and using
    # the repository would put this run in a directory holding ~100 transcripts
    # including live ones -- any "newest file" reading there is somebody else's
    # session. Writing nothing into it also removes cwd as a route to a nonce.
    workspace = Path(tempfile.mkdtemp(prefix="pptmstr-d8-"))
    project_dir = _projects_dir() / project_key_for_directory(workspace)

    run_a = await _run("A", PROMPT_A, model=model, cwd=workspace, session_id=ID_A)

    transcript_a = project_dir / f"{ID_A}.jsonl"
    beta_landed = await _await_nonce(transcript_a, BETA_NONCE)
    before = _snapshot(project_dir)

    run_b = await _run(
        "B",
        PROMPT_B,
        model=model,
        cwd=workspace,
        session_id=ID_B,
        resume=ID_A,
        fork_session=True,
    )

    # Give the CLI the same grace on B's write that A got, keyed on any new file
    # rather than on a name -- the name is precisely what is unknown. A run that
    # produced no result gets a short grace instead: it may still have written
    # something on the way out, but waiting the full window for a file that is not
    # coming only delays a verdict already decided.
    end = time.monotonic() + (TRANSCRIPT_DEADLINE_S if run_b.result_ids else 5.0)
    while time.monotonic() < end:
        after = _snapshot(project_dir)
        if set(after) - set(before) or after.get(ID_A) != before.get(ID_A):
            break
        await asyncio.sleep(POLL_S)
    after = _snapshot(project_dir)

    report(workspace, project_dir, run_a, run_b, beta_landed, before, after)
    return 0


def _transcript_text(project_dir: Path, session_id: str) -> str:
    try:
        return (project_dir / f"{session_id}.jsonl").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _classify(effective: str | None) -> str:
    if effective is None:
        return "unreadable"
    if effective == ID_B:
        return "HONOURED"
    if effective == ID_A:
        return "SOURCE-ID"
    return "MINTED"


def _classify_disk(new_ids: list[str], a_changed: bool) -> str:
    if ID_B in new_ids:
        return "HONOURED"
    if new_ids:
        return "MINTED"
    return "SOURCE-ID" if a_changed else "unreadable"


def _history_phrase(said_alpha: bool, beta_on_disk: bool | None) -> str:
    """
    Q2 in one clause, with the two readings kept apart when they conflict.

    Collapsing a contradiction into whichever answer is more cautious would report
    a settled fact where the run produced two incompatible ones, and Q2's whole job
    is to say whether Q1 is about a session with a conversation in it.
    """
    if beta_on_disk is None:
        seen = "reached" if said_alpha else "did NOT reach"
        return (
            f"{seen} the model's context (the disk half of Q2 does not apply here -- "
            "no separate transcript was written, so BETA in that file is run A's own "
            "line and says nothing about run B)"
        )
    if said_alpha and beta_on_disk:
        return "carried history, by both readings"
    if not said_alpha and not beta_on_disk:
        return "carried NO history, by both readings"
    return (
        "gives CONTRADICTORY history readings -- the model's context "
        f"{'has' if said_alpha else 'lacks'} the value while the fork's transcript "
        f"{'has' if beta_on_disk else 'lacks'} it, so Q2 is unresolved and D8's "
        "premise is not confirmed"
    )


def report(
    workspace: Path,
    project_dir: Path,
    run_a: Observation,
    run_b: Observation,
    beta_landed: bool,
    before: dict[str, FileState],
    after: dict[str, FileState],
) -> None:
    print(f"\ncwd          {workspace}   (created empty, nothing written into it)")
    print(f"project dir  {project_dir}")
    print(f"ID_A         {ID_A}   (run A's --session-id, run B's --resume)")
    print(f"ID_B         {ID_B}   (run B's --session-id)")
    print(f"ID_DECOY     {ID_DECOY}   (minted, passed to nothing)")
    print(f"ALPHA        {ALPHA_NONCE}   (run A echoes it, run B is asked to recall it)")
    print(f"BETA         {BETA_NONCE}   (run A only; never mentioned to run B)")

    for run in (run_a, run_b):
        print(f"\n=== run {run.label}: session ids on the wire ===")
        print(f"  failure      {run.failure}")
        print(f"  result ids   {run.result_ids}")
        print(f"  init ids     {run.init_ids}   (handshake echo -- NOT in the verdict)")
        print(f"  tally        {json.dumps(run.id_tally)}")
        print(f"  is_error     {run.is_error}   result={run.result_text!r}")
        print(f"  denied       {run.denied}")
        print(f"  text         {run.text.strip()[:200]!r}")

    if run_b.init_ids and run_b.effective is not None and run_b.init_ids[0] != run_b.effective:
        print("\n  !! run B's init frame and its ResultMessage name DIFFERENT ids.")
        print(f"     init={run_b.init_ids[0]} result={run_b.effective}")
        print("     The init frame is the CLI echoing the argv it was handed; a verdict")
        print("     taken from it would have read the request, not the result.")

    new_ids = sorted(set(after) - set(before))
    gone_ids = sorted(set(before) - set(after))
    a_before, a_after = before.get(ID_A), after.get(ID_A)
    a_changed = a_before != a_after

    print("\n=== disk: transcripts in the project directory ===")
    print(f"  before run B  {sorted(before)}")
    print(f"  after run B   {sorted(after)}")
    print(f"  new           {new_ids}")
    if gone_ids:
        print(f"  disappeared   {gone_ids}")

    print("\n=== Q3: did the fork mutate its source? ===")
    print(f"  {ID_A}.jsonl before  {a_before}")
    print(f"  {ID_A}.jsonl after   {a_after}")
    print(f"  changed: {a_changed}")

    said_alpha = ALPHA_NONCE in run_b.text
    # The disk half of Q2 is only a reading when run B got a transcript of its own.
    # If B ran under ID_A there was no copy, so BETA is in that file because run A
    # wrote it -- true whether or not a word of it reached run B. Counting it would
    # be reading run A twice and calling the second one evidence about run B.
    beta_on_disk: bool | None = None
    if run_b.effective is not None and run_b.effective != ID_A:
        beta_on_disk = BETA_NONCE in _transcript_text(project_dir, run_b.effective)

    print("\n=== Q2: did conversation history reach run B? ===")
    print(f"  BETA reached run A's transcript      {beta_landed}")
    print(f"  run B's reply carried ALPHA          {said_alpha}")
    print(f"  BETA in run B's own transcript       {beta_on_disk}   (None = not applicable)")
    if beta_on_disk is not None and said_alpha != beta_on_disk:
        print("  !! the model's context and the on-disk fork disagree about history.")

    # ---- void conditions, each of which makes the verdict unreadable ----
    decoy_seen = (
        ID_DECOY in run_a.id_tally
        or ID_DECOY in run_b.id_tally
        or ID_DECOY in after
        or ID_DECOY in run_b.text
    )

    print("\n=== verdict ===")
    if decoy_seen:
        print("  INCONCLUSIVE: ID_DECOY was passed to nothing and turned up anyway.")
        print("  The readings are picking up something other than what was supplied,")
        print("  so nothing below discriminates. Fix the probe before rerunning.")
        return
    if run_a.failure or run_a.is_error or run_a.effective is None:
        print("  INCONCLUSIVE: run A did not complete a clean turn, so there is no")
        print(f"  session to resume. failure={run_a.failure!r} is_error={run_a.is_error}")
        return
    if run_a.effective != ID_A:
        print("  INCONCLUSIVE: --session-id ALONE was not honoured for run A")
        print(f"  (asked {ID_A}, ran under {run_a.effective}). That is the path")
        print("  driver._options takes in production, and until it holds, nothing about")
        print("  run B is readable. This is a finding in its own right -- it would mean")
        print("  the application's own session ids are not the ones on disk.")
        return
    if ID_A not in before:
        print(f"  INCONCLUSIVE: no {ID_A}.jsonl under {project_dir} after run A.")
        print("  The disk reading has no baseline. Suspect the project-directory")
        print("  derivation before suspecting the CLI.")
        return
    if not beta_landed:
        print("  INCONCLUSIVE: run A's transcript never carried BETA, so run A has no")
        print("  history to fork. A NONE from run B would mean 'nothing was there',")
        print("  not 'the fork dropped it'.")
        return
    if run_b.failure or run_b.effective is None:
        # Split on whether the handshake completed. This uses the init frame's
        # *existence*, not the id in it -- a different claim from the one the
        # verdict refuses to take from that frame, and one it can carry.
        if run_b.init_ids:
            print("  STALLED: the CLI accepted the three-flag combination -- it reached")
            print("  handshake and emitted an init frame -- and then never completed a")
            print(f"  turn. failure={run_b.failure!r} result_ids={run_b.result_ids}")
            print("  This is NOT 'the supplied id was not honoured', and it is not a")
            print("  rejection either. Nothing about arbitration is readable from a turn")
            print("  that did not finish; rerun before reading anything into it.")
        else:
            print("  REJECTED: run B never reached handshake under the three-flag")
            print(f"  combination. failure={run_b.failure!r} result_ids={run_b.result_ids}")
            print("  Read this as the CLI refusing the combination, NOT as 'the supplied")
            print("  id was not honoured'. D8 names two routes and this is neither of")
            print("  them: a refused combination means resume cannot be built on this")
            print("  trio at all, and the design needs a shape the probe has not tested.")
        return

    by_message = _classify(run_b.effective)
    by_disk = _classify_disk(new_ids, a_changed)
    print(f"  by messages (ResultMessage.session_id={run_b.effective}):  {by_message}")
    print(f"  by disk     (new files={new_ids}, A changed={a_changed}):  {by_disk}")

    if by_message != by_disk:
        print("\n  DISAGREEMENT: the two independent readings do not match.")
        print("  This is the finding. It is not resolved here by preferring one of them,")
        print("  because which one is authoritative is exactly what is unknown -- the id")
        print("  the messages report and the id the conversation is stored under would be")
        print("  two different things, and D8's routing question then has no single")
        print("  answer. Neither of D8's two routes is selected by this run.")
        return

    if by_message == "HONOURED":
        print(f"\n  HONOURED: run B ran under the id it was given ({ID_B}), and a new")
        print(f"  transcript {ID_B}.jsonl appeared beside run A's.")
        print("  D8's first route: resume keeps NodeId stability and is a small change,")
        print("  because the application can name the forked session in advance.")
    elif by_message == "MINTED":
        print(f"\n  MINTED: run B ran under {run_b.effective}, which is neither supplied")
        print("  id. --session-id was discarded and the CLI chose for itself.")
        print("  D8's second route: the fork has to happen before the session is")
        print("  constructed, because the id cannot be chosen at construction time.")
    else:
        print(f"\n  IN-PLACE: run B ran under run A's id ({ID_A}) and no new transcript")
        print(f"  appeared; A's own file changed={a_changed}. There was no fork -- the")
        print("  session continued in its source.")
        print("  This is D8's second route with a sharper edge: not only is the supplied")
        print("  id discarded, the source session is written into, so a fork that leaves")
        print("  the original intact is not something this combination provides.")

    # Printed after the route rather than folded into it: Q1 and Q2 are separate
    # answers, and a Q2 that came back contradictory does not make Q1 wrong -- it
    # makes D8's premise, that there is a conversation to keep, the unsettled part.
    print(f"\n  Q2: the resumed conversation {_history_phrase(said_alpha, beta_on_disk)}.")

    print("\n  Bounds: one run, one bundled CLI build, one machine, one argv ordering.")
    print("  A different build may arbitrate differently and would look identical from")
    print("  here. Nothing above speaks to the trio under session_store, under")
    print("  continue_conversation, or to a second resume of the forked session.")
    print(f"\n  Transcripts left for inspection: {project_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="D8 probe 2: resume/session-id arbitration.")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.model)))
