"""
Text pptmstr writes to its own files and reads back, and text it runs or reuses.

The store makes session text drawable as it enters (``drawable``), and that escape
is not idempotent by design: ``\\u0000`` written out is told apart from a NUL by
writing its backslash as ``\\u005c``. So a text that leaves the store and comes
back through an intent must not be escaped again, and a text something is *run*
or *reused* with must be held as it arrived (``store._VERBATIM``) and made
drawable only where it is drawn or sent. Each file pptmstr keeps is held here to
that, one at a time:

- a board log is written from the store's board and read back into a store as it
  was written, so a second restore over the store that wrote it is no different
  board (``Unplaced.STORE_DIFFERS``), and a log holding what no board pptmstr
  wrote can hold is refused task by task, with the reason;
- a session file is written from the launch and the record, and what a recall
  reads back is what a fork or resume starts again with;
- a command's files hold what was asked and what it wrote as they were, and a
  request no build of pptmstr could have written is refused with a sentence.

The four texts each file is tried with are the ones that tell the cases apart: the
six characters ``\\u0000`` written out, a real NUL, a lone surrogate (the form a
directory that is not UTF-8 takes in ``os.getcwd``), and a bidirectional
override, which every text call takes and must be left alone.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from pptmstr import boardlog, commandbus, commandfile, fleetsync, sessionfile
from pptmstr.boardfacts import Fact, outcomes
from pptmstr.boardlog import Clock
from pptmstr.command import Command, End, Exited, Run
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    BoardRestored,
    CommandsRestored,
    ConcernPosted,
    Intent,
    SessionRecalled,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
)
from pptmstr.model import (
    AgentState,
    Concern,
    LaunchSpec,
    NodeId,
    Snapshot,
    Task,
    Unplaced,
)
from pptmstr.store import Store, _apply

S = "sess-w"
LEAD: NodeId = (S, None)
B1: NodeId = (S, "a1")

WRITTEN_OUT = "\\u0000"
NUL = "\x00"
LOW = json.loads('"\\udc80"')
RLO = "‮"

# Each text a file is tried with, by name. Between letters, so that a text that
# drops or doubles one is visibly not the text it was.
TEXTS = {
    "written-out": f"a{WRITTEN_OUT}b",
    "NUL": f"a{NUL}b",
    "U+DC80": f"a{LOW}b",
    "U+202E": f"a{RLO}b",
}
# Of those, the ones a board, which holds only what the store made drawable, can
# hold as text the agent wrote. The store holds a NUL and a lone surrogate written
# out, so the log does, and the two characters themselves are never in it.
EVERY_TEXT = pytest.mark.parametrize("given", list(TEXTS.values()), ids=list(TEXTS))


def run(snap: Snapshot, *intents: Intent, now: float = 5.0) -> tuple[Snapshot, list[Fact]]:
    """``intents`` applied in turn, and every board record they yield."""
    facts: list[Fact] = []
    for intent in intents:
        after, _ = _apply(snap, intent, now)
        facts.extend(outcomes(snap, intent, after, now))
        snap = after
    return snap, facts


# -- a board log -----------------------------------------------------------------


def team() -> Snapshot:
    snap = Snapshot.empty()
    for node, parent, role in ((LEAD, None, None), (B1, LEAD, "builder")):
        snap, _ = _apply(
            snap,
            AgentSpawned(
                node_id=node,
                parent=parent,
                task="lead the work",
                model="claude-opus-5",
                started_at=0.0,
                agent_type=role,
                cwd="/w" if parent is None else None,
            ),
            0.0,
        )
    return snap


def worked(given: str) -> tuple[Snapshot, list[Fact]]:
    """
    A board whose every text an agent wrote holds ``given``: a task declared,
    claimed and finished, and a concern.
    """
    task = Task(id="t-1", title=f"title {given}", detail=f"detail {given}", declared_at=1.0)
    concern = Concern(
        id="c-1",
        sender=B1,
        recipient=LEAD,
        subject=f"subject {given}",
        body=f"body {given}",
        posted_at=4.0,
    )
    return run(
        team(),
        TaskDeclared(task, node_id=LEAD),
        TaskClaimRequested(B1, "rq", "t-1"),
        TaskCompleted(B1, "t-1", at=3.0, result=f"result {given}"),
        ConcernPosted(B1, concern),
    )


def board_texts(snap: Snapshot) -> list[str]:
    task = snap.tasks["t-1"]
    concern = snap.concerns["c-1"]
    return [task.title, task.detail, task.result, concern.subject, concern.body]


def saved(facts: list[Fact]) -> list[str]:
    """The log ``facts`` are written to, a line to a record."""
    return [boardlog.encode(fact.on_the_wall(CLOCK)) for fact in facts]


def read_back(lines: list[str]) -> boardlog.Board:
    records = [boardlog.decode(line) for line in lines]
    assert all(isinstance(r, boardlog.Record) for r in records), records
    return boardlog.restore(records)  # type: ignore[arg-type]


CLOCK = Clock(wall=1_000.0, monotonic=10.0)


@EVERY_TEXT
def test_a_board_restored_from_its_log_holds_the_text_the_store_wrote_it_from(
    given: str,
) -> None:
    wrote, facts = worked(given)
    lines = saved(facts)

    resumed, _ = _apply(team(), BoardRestored(S, read_back(lines), CLOCK), 6.0)

    assert board_texts(resumed) == board_texts(wrote)
    # And the board it would write is the board it read: the log of what it holds is
    # the log it was restored from, line for line.
    assert [boardlog.encode(f) for f in _as_logged(resumed)] == [
        boardlog.encode(f) for f in _as_logged(wrote)
    ]


def _as_logged(snap: Snapshot) -> list[Any]:
    """
    The texts of the store's board in the records a log carries them in, with the
    times left out: what a save of this board says, and nothing a clock moves.
    """
    task = snap.tasks["t-1"]
    concern = snap.concerns["c-1"]
    return [
        boardlog.Declared(
            at=0.0, task_id=task.id, title=task.title, declared_by="lead", detail=task.detail
        ),
        boardlog.Completed(at=0.0, task_id=task.id, by="builder", result=task.result),
        boardlog.Posted(
            at=0.0,
            concern_id=concern.id,
            sender="builder",
            recipient="lead",
            subject=concern.subject,
            body=concern.body,
        ),
    ]


@EVERY_TEXT
def test_a_board_restored_over_the_store_that_wrote_it_is_the_same_board(given: str) -> None:
    """
    A resume in the window the session ran in: the store still holds the board,
    and the log's account of it must be found to be that board. One that differs
    withholds the whole restore.
    """
    wrote, facts = worked(given)

    again, _ = _apply(wrote, BoardRestored(S, read_back(saved(facts)), CLOCK), 6.0)

    report = again.restores[S]
    assert report.unplaced == ()
    assert (report.tasks, report.concerns) == (1, 1)
    assert board_texts(again) == board_texts(wrote)


@pytest.mark.parametrize("bad", [NUL, LOW], ids=["NUL", "U+DC80"])
def test_a_log_holding_what_no_store_wrote_is_refused_by_the_task_with_the_reason(
    bad: str,
) -> None:
    """
    A log pptmstr wrote holds the store's text, in which neither character is
    ever itself. One that holds either was edited or written by something else,
    and its text is not put on a board that would be drawn from it.
    """
    wrote, facts = worked("plain")
    records = [fact.on_the_wall(CLOCK) for fact in facts]
    declared = records[0]
    assert isinstance(declared, boardlog.Declared)
    records[0] = boardlog.Declared(
        at=declared.at,
        task_id=declared.task_id,
        title=declared.title,
        declared_by=declared.declared_by,
        detail=f"edited {bad} on disk",
    )
    board = boardlog.restore(records)

    resumed, _ = _apply(team(), BoardRestored(S, board, CLOCK), 6.0)

    assert "t-1" not in resumed.tasks
    (left,) = [n for n in resumed.restores[S].unplaced if n.what == "task"]
    assert left.why is Unplaced.UNDRAWABLE
    # The concern, which holds neither, is placed beside it.
    assert board_texts(wrote)[3:] == [
        resumed.concerns["c-1"].subject,
        resumed.concerns["c-1"].body,
    ]


# -- a session file --------------------------------------------------------------


def _launched(given: str, home: str) -> LaunchSpec:
    return LaunchSpec(
        task=f"task {given}",
        model="claude-opus-5",
        cwd=home,
        session_base=home,
        template="solo",
        brief=f"{home}/brief {given}",
        message=f"message {given}",
    )


def _spawned(spec: LaunchSpec, at: float) -> AgentSpawned:
    """The announce the driver makes for a launch of ``spec``."""
    return AgentSpawned(
        node_id=LEAD,
        parent=None,
        task=spec.task,
        model=spec.model,
        started_at=at,
        template=spec.template,
        brief=spec.brief,
        cwd=spec.cwd,
        session_base=spec.session_base,
    )


def _write(changes: list[fleetsync.Change], root: Path) -> None:
    for change in changes:
        keys = {**change.keys, **change.once}
        written = sessionfile.update(S, keys, root)
        assert isinstance(written, sessionfile.Written), written


def _run_once(spec: LaunchSpec, error: str, root: Path) -> None:
    """
    One run of a session, as its file is written: launched, announced, live,
    failed with ``error``.
    """
    snap = Snapshot.empty()
    derived: list[fleetsync.Change] = [
        fleetsync.launched(spec, S, spec.policy, False, 100.0),
    ]
    from pptmstr.intents import StateChanged

    for intent in (
        _spawned(spec, 1.0),
        StateChanged(LEAD, AgentState.THINKING),
        AgentFinished(LEAD, AgentState.FAILED, ended_at=3.0, error=error),
    ):
        after, _ = _apply(snap, intent, 2.0)
        derived += fleetsync.changes(snap, intent, after, 200.0)
        snap = after
    _write(derived, root)


def _recalled(root: Path) -> SessionRecalled:
    fleet = fleetsync.recall(root, list_sessions=lambda: ())
    (recalled,) = fleet.sessions
    return recalled


@EVERY_TEXT
def test_a_recalled_session_is_launched_again_with_what_it_was_launched_with(
    given: str, tmp_path: Path
) -> None:
    home = f"/w/{given}" if NUL not in given else "/w/home"
    spec = _launched(given, home)
    _run_once(spec, f"error {given}", tmp_path)

    store = Store()
    store.apply(_recalled(tmp_path))
    record = store.snapshot().nodes[LEAD]
    again = LaunchSpec.from_record(record)

    assert (again.task, again.cwd, again.session_base, again.template, again.brief) == (
        spec.task,
        spec.cwd,
        spec.session_base,
        spec.template,
        spec.brief,
    )
    assert record.recalled is not None
    # The message the resume form opens on is the one that was sent.
    assert record.recalled.last_message == spec.message
    # What the run said when it failed is in the file as it said it.
    found = sessionfile.read(S, tmp_path)
    assert isinstance(found, sessionfile.SessionFile)
    assert found.error == f"error {given}"


@EVERY_TEXT
def test_a_session_file_is_the_same_after_a_resume_that_changed_nothing(
    given: str, tmp_path: Path
) -> None:
    home = f"/w/{given}" if NUL not in given else "/w/home"
    spec = _launched(given, home)
    _run_once(spec, f"error {given}", tmp_path)
    path = sessionfile.file_path(S, tmp_path)
    assert path is not None
    before = json.loads(path.read_bytes())

    store = Store()
    store.apply(_recalled(tmp_path))
    record = store.snapshot().nodes[LEAD]
    # The resume form opens on the message the file holds, and sends it.
    assert record.recalled is not None
    resumed = dataclasses.replace(
        LaunchSpec.from_record(record), message=record.recalled.last_message, resume=S
    )
    _run_once(resumed, f"error {given}", tmp_path)
    after = json.loads(path.read_bytes())

    text_keys = ("task", "cwd", "session_base", "template", "brief", "error", "last_message")
    assert {k: after[k] for k in text_keys} == {k: before[k] for k in text_keys}


@EVERY_TEXT
def test_a_fork_of_a_live_session_starts_from_its_task_as_it_was_typed(given: str) -> None:
    store = Store()
    spec = _launched(given, "/w/home")
    store.apply(_spawned(spec, 1.0))

    forked = LaunchSpec.from_record(store.snapshot().nodes[LEAD])

    assert (forked.task, forked.template, forked.brief) == (spec.task, spec.template, spec.brief)


# -- a command's files -----------------------------------------------------------

CMD = "cmd-20261015T120000Z-0123456789ab"


def _command(asked: str, tail: str) -> Command:
    return Command(
        id=CMD,
        asker=B1,
        asked=asked,
        reason="to see",
        expects="nothing",
        asked_at=1.0,
        run=Run(text=asked, shell="/bin/bash", cwd="/w", timeout_s=60.0, started_at=2.0),
        end=End(outcome=Exited(0), at=3.0, duration_s=1.0, output_bytes=len(tail), tail=tail),
    )


def _write_command(command: Command, root: Path) -> list[bytes]:
    for write in (commandfile.write_request, commandfile.write_run, commandfile.write_end):
        assert isinstance(write(command, root), commandfile.Written)
    directory = commandfile.command_dir(S, CMD, root)
    assert directory is not None
    return [(directory / name).read_bytes() for name in sorted(p.name for p in directory.iterdir())]


@EVERY_TEXT
def test_a_restored_command_is_written_again_as_the_bytes_it_was_read_from(
    given: str, tmp_path: Path
) -> None:
    # A command with a NUL or a lone surrogate in it is never asked (the bus
    # refuses it), so the command holds the two that can be, and the output all four.
    asked = f"echo {given}" if given in (TEXTS["written-out"], TEXTS["U+202E"]) else "echo x"
    first = _write_command(_command(asked, f"out {given}\n"), tmp_path / "one")
    restored = commandfile.restore(S, tmp_path / "one")
    assert restored.unreadable == ()

    store = Store()
    store.apply(CommandsRestored(S, restored.commands))
    held = store.snapshot().commands[CMD]

    assert _write_command(held, tmp_path / "two") == first
    assert held.end is not None
    assert held.end.tail == f"out {given}\n"


@pytest.mark.parametrize("bad", [NUL, LOW], ids=["NUL", "U+DC80"])
@pytest.mark.parametrize(
    ("name", "field"),
    [
        (commandfile.REQUEST_NAME, "asked"),
        (commandfile.REQUEST_NAME, "reason"),
        (commandfile.REQUEST_NAME, "expects"),
        (commandfile.RUN_NAME, "text"),
    ],
)
def test_a_request_no_build_could_have_written_is_refused_with_a_sentence(
    name: str, field: str, bad: str, tmp_path: Path
) -> None:
    _write_command(_command("echo x", "out\n"), tmp_path)
    directory = commandfile.command_dir(S, CMD, tmp_path)
    assert directory is not None
    request = directory / name
    obj = json.loads(request.read_bytes())
    obj[field] = f"echo {bad}"
    request.chmod(0o600)
    request.write_bytes(json.dumps(obj, ensure_ascii=True).encode("ascii"))

    restored = commandfile.restore(S, tmp_path)

    assert restored.commands == ()
    ((command_id, reason),) = restored.unreadable
    assert command_id == CMD
    assert field in reason
    reason.encode("utf-8")


def test_every_reply_about_a_restored_command_can_be_sent(tmp_path: Path) -> None:
    """
    An MCP reply is serialized as UTF-8 and refuses a lone surrogate
    (``TextContent(type="text", text="\\udc80").model_dump_json()`` raises
    ``PydanticSerializationError``), so no reply may quote one: not from a
    request a file holds, and not from what a command wrote.
    """
    from mcp.types import TextContent

    _write_command(_command(f"echo {TEXTS['written-out']} {RLO}", f"a{NUL}b{LOW}c\n"), tmp_path)
    other = "cmd-20261015T120001Z-0123456789ab"
    directory = commandfile.command_dir(S, CMD, tmp_path)
    assert directory is not None
    edited = directory.parent / other
    edited.mkdir(mode=0o700)
    obj = json.loads((directory / commandfile.REQUEST_NAME).read_bytes())
    obj.update(id=other, asked=f"echo {LOW}")
    (edited / commandfile.REQUEST_NAME).write_bytes(json.dumps(obj).encode("ascii"))

    store = Store()
    store.apply(CommandsRestored(S, commandfile.restore(S, tmp_path).commands))
    shown = tuple(store.snapshot().commands.values())

    for wanted in (None, CMD, other):
        reply = commandbus.commands_text(shown, wanted, 10.0, B1, lambda node: "builder")
        TextContent(type="text", text=reply).model_dump_json()


def test_a_nul_a_command_wrote_reads_as_the_nul_wherever_its_output_is_shown(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``commandtext.visible`` writes a NUL as ``\\x00`` and a backslash as two. The
    output is the program's bytes, so it is held as they were and spelled once:
    in the row, in DETAIL's copy and in the asker's reply.
    """
    from pptmstr.ui import command_view, detail

    store = Store()
    store.apply(CommandsRestored(S, (_command("echo x", f"a{NUL}b\n"),)))
    held = store.snapshot().commands[CMD]
    assert held.end is not None

    drawn: list[str] = []
    real = imgui.text_colored

    def recording(colour: Any, text: str) -> None:
        drawn.append(text)
        real(colour, text)

    monkeypatch.setattr(imgui, "text_colored", recording)
    imgui.new_frame()
    imgui.begin("output")
    try:
        command_view.process_output(held.end.tail)
    finally:
        imgui.end()
        imgui.render()
    lines, _ = commandbus._output_lines(held.end.tail)
    copied = detail.command_text(held, "builder")

    for said in ("\n".join(drawn), "\n".join(lines), "\n".join(copied)):
        assert "a\\x00b" in said, said
        assert "\\\\" not in said, said


def test_a_reply_quoting_what_no_reply_can_carry_is_still_sent() -> None:
    """
    What the file reads do not refuse -- the operator's reason for refusing, in an
    edited ``end.json`` -- is written out at the reply's one door.
    """
    from mcp.types import TextContent

    from pptmstr.command import Refused

    refused = Command(
        id=CMD,
        asker=B1,
        asked="echo x",
        reason="to see",
        expects="nothing",
        asked_at=1.0,
        end=End(outcome=Refused(reason=f"no {LOW}"), at=3.0),
    )
    reply = commandbus.commands_text((refused,), CMD, 10.0, B1, lambda node: "builder")

    TextContent(type="text", text=reply).model_dump_json()
    assert "no \\udc80" in reply
