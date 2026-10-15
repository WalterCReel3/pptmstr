"""
Session text made drawable: the mapping itself, and the three places it is applied.

The mapping has to be **one to one** before it is anything else. It is applied to
what an approver reads and to identifiers alike, so two strings that differ must
still differ afterwards, and a command holding a code unit must not read as the
command that spells that code unit's escape.

The places are where text enters what pptmstr holds -- the reducer, the transcript,
the log -- and each is tested through its own door, because a function nothing
calls is covered and does nothing.
"""

from __future__ import annotations

import ast
import dataclasses
import io
import itertools
import json
import random
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import SDKSessionInfo

from pptmstr import drawable, sessions, store
from pptmstr.approval import DiffText
from pptmstr.command import Command, End, Exited, Refused, Run
from pptmstr.effects import CommandToRun
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    CommandApproved,
    CommandEnded,
    CommandRefused,
    CommandRequested,
    CommandsRestored,
    ConcernPosted,
    SessionRecalled,
    SubagentDelivered,
    TaskClaimRequested,
    TaskDeclared,
)
from pptmstr.log import Log
from pptmstr.model import AgentState, Concern, NodeId, PendingApproval, Recalled, Task
from pptmstr.store import Store
from pptmstr.transcript import SegmentKind, Transcript
from pptmstr.ui import review

ROOT: NodeId = ("s1", None)
CHILD: NodeId = ("s1", "a1")

LOW = json.loads('"\\udc80"')
HIGH = json.loads('"\\ud800"')
PACKAGE = Path(drawable.__file__).parent


def takes(text: str) -> bool:
    """
    Whether every text call takes all of ``text``: it has a UTF-8 encoding, which
    is what the ImGui binding makes of its argument, and no NUL to end it early.
    """
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return "\x00" not in text


# -- the mapping ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    ["", "plain", "naïve → text", "\U0001f600 astral", "a\\nb", "C:\\users\\x", "\\x00", "\\u00e9"],
)
def test_text_that_every_call_takes_is_returned_as_the_same_object(given: str) -> None:
    assert drawable.text(given) is given


@pytest.mark.parametrize(
    ("given", "written"),
    [
        (f"a{LOW}b", "a\\udc80b"),
        (f"a{HIGH}b", "a\\ud800b"),
        ("a\x00b", "a\\u0000b"),
        ("\udfff", "\\udfff"),
        (f"{HIGH}{LOW}", "\\ud800\\udc80"),
        (f"é{LOW}\U0001f600", "é\\udc80\U0001f600"),
    ],
)
def test_a_code_unit_no_text_call_takes_is_written_as_its_json_escape(
    given: str, written: str
) -> None:
    assert not takes(given)
    assert drawable.text(given) == written
    assert takes(written)
    # The form JSON has for the same code unit, which the arguments editor shows.
    # A high surrogate next to a low one is the exception, and cannot arrive: JSON
    # reads that pair as the one character it encodes.
    if HIGH + LOW not in given:
        assert json.loads(f'"{written}"') == given
    assert drawable.original(written) == given


@pytest.mark.parametrize(
    ("given", "written"),
    [
        ("\\udc80", "\\u005cudc80"),
        ("\\uDC80", "\\u005cuDC80"),
        ("\\ud800", "\\u005cud800"),
        ("\\u0000", "\\u005cu0000"),
        ("\\u005c", "\\u005cu005c"),
        ("\\u005C", "\\u005cu005C"),
        ("\\\\udc80", "\\\\u005cudc80"),
    ],
)
def test_the_escape_of_one_written_out_is_not_drawn_as_the_code_unit(
    given: str, written: str
) -> None:
    """
    A backslash that would begin a sequence this module writes is written
    ``\\u005c``, in either case of hex: a reader is not asked to know that only
    lower case is ever produced.
    """
    assert takes(given)
    assert drawable.text(given) == written
    assert drawable.original(written) == given


def test_a_backslash_that_spells_no_escape_is_left_alone() -> None:
    for given in ("\\u00e9", "\\u2192", "\\udc8", "\\ud7ff", "\\ue000", "\\Udc80", "\\u 0000"):
        assert drawable.text(given) is given, given


def test_the_mapping_is_one_to_one() -> None:
    """
    Every string over the characters the mapping turns on, to six long, and a
    sample of longer ones: each is recovered from what it was written as, so no
    two are written alike, and what is written is always drawable.
    """
    alphabets = (
        ["\\", "u", "0", "5", "c", "\x00"],
        ["\\", "u", "d", "8", "C", LOW],
        ["\\", "u", "D", "0", HIGH, "a"],
    )
    seen = 0
    for alphabet in alphabets:
        for length in range(7):
            for chars in itertools.product(alphabet, repeat=length):
                given = "".join(chars)
                written = drawable.text(given)
                assert takes(written), repr(given)
                assert drawable.original(written) == given, repr(given)
                seen += 1
    rng = random.Random(20261012)
    pool = ["\\", "\\u", "u", "0", "00", "5", "c", "C", "d", "D", "8", "f", "\x00", LOW, HIGH, "é"]
    for _ in range(20_000):
        given = "".join(rng.choice(pool) for _ in range(rng.randint(7, 24)))
        written = drawable.text(given)
        assert takes(written) and drawable.original(written) == given, repr(given)
        seen += 1
    assert seen > 180_000


@dataclass(frozen=True, slots=True)
class _Inner:
    name: str
    count: int = 0


@dataclass(frozen=True, slots=True)
class _Outer:
    title: str
    inner: _Inner
    paths: tuple[str, ...] = ()
    args: dict[str, Any] | None = None
    kept: str = ""


def test_shown_reaches_every_string_a_record_holds_and_copies_only_what_changed() -> None:
    clean_inner = _Inner("fine")
    clean = _Outer("t", clean_inner, ("a", "b"), {"k": ["v", 1, None]})
    assert drawable.shown(clean) is clean

    dirty = _Outer(
        f"t{LOW}",
        _Inner(f"n{HIGH}", 3),
        ("a", "b\x00"),
        {f"k{LOW}": ["v", {"deep": f"x{LOW}"}], "n": 7},
        kept=f"as{LOW}is",
    )
    made = drawable.shown(dirty, {_Outer: frozenset({"kept"})})
    assert made == _Outer(
        "t\\udc80",
        _Inner("n\\ud800", 3),
        ("a", "b\\u0000"),
        {"k\\udc80": ["v", {"deep": "x\\udc80"}], "n": 7},
        kept=f"as{LOW}is",
    )
    # One field dirty: the others are the objects they were.
    half = _Outer(f"t{LOW}", clean_inner, clean.paths, clean.args)
    made = drawable.shown(half)
    assert made.inner is clean_inner and made.paths is clean.paths and made.args is clean.args


def test_what_is_not_text_or_a_holder_of_it_is_returned_as_it_is() -> None:
    transcript = Transcript()
    for value in (None, 3, 2.5, True, AgentState.DONE, transcript, b"\x00"):
        assert drawable.shown(value) is value


# -- the reducer's door -------------------------------------------------------------


def spawn(node: NodeId, parent: NodeId | None = None, **more: Any) -> AgentSpawned:
    return AgentSpawned(
        node_id=node, parent=parent, task="do a thing", model="m", started_at=0.0, **more
    )


def parked(args: dict[str, Any], *, tool: str = "Bash", diff: str | None = None) -> PendingApproval:
    return PendingApproval(
        id="p1",
        node=CHILD,
        tool_name=tool,
        tool_use_id="tu-1",
        raw_args=args,
        summary=f"{tool} {next(iter(args.values()))}",
        requested_at=1.0,
        diff=diff,
        texts=None if diff is None else (DiffText(None, f"x{LOW}y\n", f"made{LOW}.py"),),
    )


def strings(value: Any) -> list[str]:
    """
    Every string a record holds, to any depth, verbatim fields included.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, tuple | list | frozenset | set):
        return [text for item in value for text in strings(item)]
    if isinstance(value, Mapping):
        return [text for pair in value.items() for text in strings(pair)]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return [
            text
            for field in dataclasses.fields(value)
            for text in strings(getattr(value, field.name))
        ]
    return []


def test_a_parked_call_is_drawable_in_the_store_and_its_arguments_are_as_sent() -> None:
    command = f"printf 'x{LOW}y' > out"
    args = {"command": command, "description": "writes\x00a file"}
    held = Store()
    held.apply(spawn(ROOT))
    held.apply(spawn(CHILD, ROOT))
    held.apply(ApprovalRequested(CHILD, parked(args, diff=f"+x{LOW}y\n")))

    (pending,) = held.snapshot().approvals
    # As sent: the same mapping, holding the same strings.
    assert pending.raw_args is args and pending.raw_args["command"] is command
    # As drawn.
    assert pending.args == {
        "command": "printf 'x\\udc80y' > out",
        "description": "writes\\u0000a file",
    }
    assert pending.summary == "Bash printf 'x\\udc80y' > out"
    assert pending.diff == "+x\\udc80y\n"
    assert pending.texts == (DiffText(None, "x\\udc80y\n", "made\\udc80.py"),)
    (obligation,) = held.snapshot().needs_you
    assert obligation.summary == pending.summary


def test_a_call_that_was_drawable_enters_the_store_as_the_object_it_was() -> None:
    """
    Nearly every call. The panes key their views on the identity of what they
    are handed, so a copy here would be a view laid out twice.
    """
    call = parked({"command": "ls -la"})
    held = Store()
    held.apply(spawn(ROOT))
    held.apply(spawn(CHILD, ROOT))
    held.apply(ApprovalRequested(CHILD, call))

    (pending,) = held.snapshot().approvals
    assert pending is call and pending.shown_args is None and pending.args is call.raw_args


def test_the_drawable_arguments_are_one_mapping_for_as_long_as_the_call_is_parked() -> None:
    held = Store()
    held.apply(spawn(ROOT))
    held.apply(spawn(CHILD, ROOT))
    held.apply(ApprovalRequested(CHILD, parked({"command": f"x{LOW}"})))
    first = held.snapshot().approvals[0].args
    held.apply(spawn(("s2", None)))
    assert held.snapshot().approvals[0].args is first


def test_what_a_session_wrote_is_drawable_wherever_the_store_keeps_it() -> None:
    held = Store()
    held.apply(spawn(ROOT, cwd="/x/proj"))
    held.apply(
        AgentSpawned(
            node_id=CHILD,
            parent=ROOT,
            task=f"audit{LOW}",
            model="m",
            started_at=0.0,
            agent_type=f"builder{HIGH}",
            topic="starting\x00",
        )
    )
    held.apply(
        ConcernPosted(
            CHILD,
            Concern(
                id="c1",
                sender=CHILD,
                recipient=ROOT,
                subject=f"s{LOW}",
                body=f"b{LOW}\x00",
                posted_at=1.0,
            ),
        )
    )
    held.apply(TaskDeclared(Task(id=f"T{LOW}", title=f"t{HIGH}", touches=(f"a{LOW}.py",)), ROOT))
    held.apply(SubagentDelivered(CHILD, f"done{LOW}"))
    held.apply(AgentFinished(CHILD, AgentState.FAILED, 2.0, error=f"boom{LOW}"))

    snap = held.snapshot()
    # A task is launched again from and a failure written back to a session file
    # from, so both are as they arrived (``store._VERBATIM``) and are left out here.
    child = snap.nodes[CHILD]
    assert (child.task, child.error) == (f"audit{LOW}", f"boom{LOW}")
    kept = {f"audit{LOW}", f"boom{LOW}"}
    found = [
        text
        for record in (*snap.nodes.values(), *snap.concerns.values(), *snap.tasks.values())
        for text in strings(record)
        if text not in kept
    ]
    found += [text for obligation in snap.needs_you for text in strings(obligation)]
    assert not [text for text in found if not takes(text)]
    # The control: the walk above did read the fields that were dirty, and the
    # failure the queue draws is the drawable form of the one the record holds.
    for written in ("builder\\ud800", "s\\udc80", "b\\udc80\\u0000", "boom\\udc80"):
        assert written in found, written
    assert "T\\udc80" in snap.tasks and snap.tasks["T\\udc80"].title == "t\\ud800"


def test_an_identifier_a_model_wrote_still_finds_what_it_named() -> None:
    """
    A task id is written by a model twice, to declare and to claim, and both reach
    the store by the same door: rewritten alike, they still meet.
    """
    held = Store()
    held.apply(spawn(ROOT))
    held.apply(spawn(CHILD, ROOT))
    held.apply(TaskDeclared(Task(id=f"T{LOW}", title="work"), ROOT))
    held.apply(TaskDeclared(Task(id="T\\udc80", title="spelled out"), ROOT))
    held.apply(TaskClaimRequested(CHILD, request_id="k1", task_id=f"T{LOW}"))

    tasks = held.snapshot().tasks
    assert sorted(tasks) == ["T\\u005cudc80", "T\\udc80"]
    assert tasks["T\\udc80"].title == "work" and tasks["T\\udc80"].claimed_by == CHILD
    assert tasks["T\\u005cudc80"].claimed_by is None


def test_a_directory_is_kept_as_it_was_given() -> None:
    """
    A session is launched, forked and measured in ``cwd``, and a name that is not
    UTF-8 reaches Python as lone surrogates (``surrogateescape``): written out, it
    would name a directory that does not exist.
    """
    held = Store()
    held.apply(spawn(ROOT, cwd=f"/x/pro{LOW}j", session_base=f"/x/pro{LOW}j"))
    record = held.snapshot().nodes[ROOT]
    assert record.cwd == f"/x/pro{LOW}j" and record.session_base == f"/x/pro{LOW}j"


def test_a_recalled_sessions_directory_is_kept_as_its_file_gave_it() -> None:
    """Its resume is launched there (``app._open_resume`` reads ``AgentRecord.cwd``)."""
    held = Store()
    held.apply(
        SessionRecalled(
            session_id="s9",
            task="port the parser",
            model="m",
            recalled=Recalled(),
            cwd=f"/x/pro{LOW}j",
            session_base=f"/x/pro{LOW}j",
        )
    )
    (record,) = held.snapshot().nodes.values()
    assert record.cwd == f"/x/pro{LOW}j" and record.session_base == f"/x/pro{LOW}j"


def _command(n: int, asked: str = "make", **fields: Any) -> Command:
    return Command(
        **{
            "id": f"cmd-20261013T000000Z-{n:012x}",
            "asker": CHILD,
            "asked": asked,
            "reason": "the build needs it",
            "expects": "a wheel under dist/",
            "asked_at": 1.0,
            **fields,
        }
    )


def test_a_command_that_spells_an_escape_is_held_and_run_as_it_was_asked() -> None:
    """
    ``drawable`` writes a backslash that spells ``\\u0000`` as ``\\u005c``, so a
    command passed through it would be shown, drafted and run as another command.
    The asker's string is what the run's draft opens on, and the approved string
    is what the shell is handed.
    """
    asked = r"printf '\u0000\\udc80'"
    held = Store()
    held.apply(spawn(ROOT))
    held.apply(spawn(CHILD, ROOT))
    command = _command(1, asked, reason=r"it spells \u0000", expects=r"\ printed")
    held.apply(CommandRequested(CHILD, command, "rq", 1.0))

    kept = held.snapshot().commands[command.id]
    assert (kept.asked, kept.reason, kept.expects) == (asked, command.reason, command.expects)

    edited = r"printf '\ud800'"
    cwd = f"/x/pro{LOW}j"
    effects = held.apply(CommandApproved(command.id, edited, "/bin/sh", cwd, 600, 2.0))

    (order,) = (effect for effect in effects if isinstance(effect, CommandToRun))
    assert order.command.run is not None
    assert (order.command.run.text, order.command.run.shell, order.command.run.cwd) == (
        edited,
        "/bin/sh",
        cwd,
    )
    assert order.command.asked == asked
    assert held.snapshot().commands[command.id].run == order.command.run


@pytest.mark.parametrize(("field", "refusal"), [("asked", "nul"), ("reason", "reason_nul")])
def test_a_command_holding_a_nul_is_refused_and_not_written_into_one_that_is_not(
    field: str, refusal: str
) -> None:
    """The refusal reads the asker's own string; an escaped one would pass it."""
    held = Store()
    held.apply(spawn(ROOT))
    held.apply(spawn(CHILD, ROOT))
    command = _command(1, **{"asked": "make", field: "a\x00b"})
    effects = held.apply(CommandRequested(CHILD, command, "rq", 1.0))

    assert held.snapshot().commands == {}
    assert [getattr(e.refusal, "value", None) for e in effects] == [refusal]


def test_a_restored_command_says_what_was_asked_and_run_as_its_file_does() -> None:
    """A closed command read back from its session's files is the same record."""
    held = Store()
    held.apply(spawn(ROOT))
    run = Run(
        text=r"printf '\u0000'", shell="/bin/sh", cwd=f"/x/pro{LOW}j", timeout_s=600, started_at=2.0
    )
    command = dataclasses.replace(
        _command(1, r"printf '\u0000'", asker=ROOT),
        run=run,
        end=End(outcome=Exited(code=0), at=3.0),
    )
    held.apply(CommandsRestored("s1", (command,)))

    kept = held.snapshot().commands[command.id]
    assert (kept.asked, kept.run) == (command.asked, run)


def test_the_operators_reason_for_refusing_a_command_is_kept_as_typed() -> None:
    """It is handed to the asker as the operator's words (``commandbus``)."""
    held = Store()
    held.apply(spawn(ROOT))
    held.apply(spawn(CHILD, ROOT))
    command = _command(1)
    held.apply(CommandRequested(CHILD, command, "rq", 1.0))
    typed = r"not \u0000 again"
    held.apply(CommandRefused(command.id, typed, 2.0))

    end = held.snapshot().commands[command.id].end
    assert end is not None and end.outcome == Refused(reason=typed)


def test_what_a_command_wrote_is_held_as_it_wrote_it() -> None:
    """
    Output is never run, and is still exempt: it is shown and sent through
    ``commandtext.visible``, whose ``\\x00`` is the NUL only when it is handed
    the NUL, and a ``\\u0000`` the store wrote would read there as a backslash.
    """
    held = Store()
    held.apply(spawn(ROOT))
    held.apply(spawn(CHILD, ROOT))
    command = _command(1, "make")
    held.apply(CommandRequested(CHILD, command, "rq", 1.0))
    held.apply(CommandApproved(command.id, "make", "/bin/sh", "/w", 600, 2.0))
    end = End(outcome=Exited(code=0), at=3.0, output_bytes=6, tail=f"a\x00b{LOW}")
    held.apply(CommandEnded(command.id, end))

    finished = held.snapshot().commands[command.id].end
    assert finished is not None and finished.tail == f"a\x00b{LOW}"


def test_a_path_measured_from_an_approved_call_is_drawable_in_the_ledger() -> None:
    """
    The reducer reads the arguments of a call it resolves and keeps the path it
    finds, which the board then draws.
    """
    for edited in (None, {"file_path": f"other{LOW}.py"}):
        held = Store()
        held.apply(spawn(ROOT))
        held.apply(spawn(CHILD, ROOT))
        held.apply(TaskDeclared(Task(id="t1", title="work"), ROOT))
        held.apply(TaskClaimRequested(CHILD, request_id="k1", task_id="t1"))
        call = parked({"file_path": f"made{LOW}.py", "content": "x\n"}, tool="Write", diff="+x\n")
        held.apply(ApprovalRequested(CHILD, call))
        held.apply(ApprovalResolved(CHILD, "p1", approved=True, edited_args=edited))

        (path,) = held.snapshot().tasks["t1"].writes.paths
        assert path == ("made\\udc80.py" if edited is None else "other\\udc80.py")


def test_every_exemption_names_a_field_its_record_has() -> None:
    for record, names in store._VERBATIM.items():
        assert names <= {field.name for field in dataclasses.fields(record)}, record


@pytest.mark.parametrize("held", ["\x00", LOW, HIGH, "\\u0000", "\t‮"])
def test_the_command_a_pane_draws_from_the_arguments_as_sent_is_printable_ascii(
    held: str,
) -> None:
    held_store = Store()
    held_store.apply(spawn(ROOT))
    held_store.apply(spawn(CHILD, ROOT))
    held_store.apply(ApprovalRequested(CHILD, parked({"command": f"ls {held}\nrm x"})))
    (pending,) = held_store.snapshot().nodes[CHILD].pending
    shown = review.shown_command(pending)

    assert shown is not None and takes(shown)
    assert all(" " <= char <= "~" or char == "\n" for char in shown), shown
    assert review.shown_command(parked({"file_path": "a"}, tool="Write")) is None


def test_no_pane_reads_the_arguments_as_sent_except_to_open_the_editor_on_them() -> None:
    """
    ``raw_args`` can hold what no text call takes, and nothing but this stands
    between a new line of pane code and a queue that does not draw. The one reader
    is the editor's draft, which is JSON and is what approve sends back. The
    other is a Bash command as every approval surface draws it, which is
    ``commandtext.visible`` of the command sent: printable ASCII and newline only,
    and drawn, never sent.
    """
    readers: list[str] = []
    for path in sorted((PACKAGE / "ui").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Attribute) and node.attr == "raw_args":
                    readers.append(f"{path.relative_to(PACKAGE)}:{function.name}")
    assert readers == ["ui/review.py:pretty_args", "ui/review.py:shown_command"]


# -- the transcript's door ----------------------------------------------------------


@pytest.mark.parametrize("kind", [SegmentKind.OUTPUT, SegmentKind.TOOL_RESULT, SegmentKind.ERROR])
def test_a_transcript_takes_what_has_no_utf8_and_reads_it_back_written_out(
    kind: SegmentKind,
) -> None:
    transcript = Transcript()
    transcript.append(kind, f"before{LOW}")
    transcript.append(kind, f"mid\x00dle{HIGH}after")

    assert transcript.text() == "before\\udc80mid\\u0000dle\\ud800after"
    assert transcript.tail(64) == transcript.text()
    if kind is SegmentKind.OUTPUT:
        assert transcript.turn_prose() == transcript.text()
    (segment,) = transcript.segments()
    assert segment.length == transcript.published_length


def test_what_a_segment_is_keyed_on_is_drawable_too() -> None:
    transcript = Transcript()
    transcript.append(SegmentKind.TOOL_CALL, "call", meta=(("tool", f"Ba{LOW}sh"),))
    (segment,) = transcript.segments()
    assert segment.meta == (("tool", "Ba\\udc80sh"),)


# -- the listing's door -------------------------------------------------------------


def test_a_session_on_disk_is_listed_by_words_that_can_be_drawn_and_resumed_by_its_own() -> None:
    """
    The resume list is read from transcripts the store never held. What names a
    session to the eye is written out; what it is resumed by is not touched.
    """
    info = SDKSessionInfo(
        session_id="abc",
        summary=f"fix{LOW}",
        last_modified=1,
        file_size=None,
        custom_title=None,
        first_prompt=f"please\x00 fix{HIGH}",
        git_branch=f"b{LOW}",
        cwd=f"/x/pro{LOW}j",
        tag=None,
        created_at=None,
    )
    (row,) = sessions.merge_overlay([info], {})
    assert (row.summary, row.first_prompt, row.git_branch, row.tag) == (
        "fix\\udc80",
        "please\\u0000 fix\\ud800",
        "b\\udc80",
        None,
    )
    assert takes(row.label)
    assert row.session_id == "abc" and row.cwd == f"/x/pro{LOW}j"


# -- the log's door -----------------------------------------------------------------


def test_a_log_line_quoting_a_session_is_kept_and_printed_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The gate logs a call's summary on the driver's thread between the operator's
    approve and its answer to the CLI, and a strict UTF-8 stream refuses a lone
    surrogate.
    """
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="utf-8", errors="strict"))
    log = Log()
    log.info("gate", f"approved printf 'x{LOW}y'\x00")
    sys.stdout.flush()

    (entry,) = log.snapshot()[0]
    assert entry.text == "approved printf 'x\\udc80y'\\u0000"
    assert raw.getvalue().decode("utf-8") == "[I] gate: approved printf 'x\\udc80y'\\u0000\n"
