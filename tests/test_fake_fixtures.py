"""
What the ``--fake`` fleet carries.

Every pane that sets text is judged from a screenshot of this fleet, so a fixture
that quietly lost a case -- the table, the unterminated edit, the stranded row --
would make the screenshot say less without looking any different. Each test here
names one case and reads it back out of the store or the transcript the panes read,
not out of the driver's own constants.
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile

import pytest

from pptmstr import board, brief, bus, fake_driver
from pptmstr.bridge import Bridge
from pptmstr.fake_driver import FakeDriver
from pptmstr.model import (
    AgentState,
    ConcernState,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
    TaskState,
)
from pptmstr.store import Store
from pptmstr.transcript import SegmentKind, Transcript


def _seeded() -> tuple[FakeDriver, Store]:
    """The fleet as it stands on the first frame: seeded, nothing streamed yet."""
    driver = FakeDriver(Bridge())
    asyncio.run(driver._seed_tree())
    store = Store()
    store.apply_all(driver.bridge.drain())
    return driver, store


@pytest.fixture(scope="module")
def fleet() -> tuple[FakeDriver, Snapshot]:
    driver, store = _seeded()
    return driver, store.snapshot()


def _output(driver: FakeDriver, role: str) -> str:
    """The OUTPUT bytes of a cast member's transcript, which is what gets set as markdown."""
    transcript = driver.transcripts[driver.cast[role]]
    return "".join(
        transcript.read(s.start, s.end)
        for s in transcript.segments()
        if s.kind is SegmentKind.OUTPUT
    )


def _parked(snap: Snapshot, tool_name: str) -> list[PendingApproval]:
    return [p for p in snap.approvals if p.tool_name == tool_name]


# -- the report -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "needle"),
    [
        ("h1", "\n# Parser audit"),
        ("h2", "\n## What changes"),
        ("h3", "\n### The comparison"),
        ("bold", "**correct on every field it reads**"),
        ("italic", "*dropped*"),
        ("inline code", "`tle/parse.py`"),
        ("bullet, second level", "\n  - `Field.__init__`"),
        ("bullet, third level", "\n    - The exception"),
        ("ordered list", "\n1. Read"),
        ("block quote", "\n> A parser that validates"),
        ("python fence", "\n```python\ndef verify"),
        ("diff fence", "\n```diff\n--- a/tle/checksum.py"),
        ("table header", "\n| Input | Lines | Result | Notes |\n|---|---:|---|---|"),
        ("link", "[CelesTrak TLE documentation](https://"),
        ("rule", "\n---\n"),
        ("bare dunder", "(__init__, __post_init__)"),
        ("bare snake_case", "is foo_bar_baz, which"),
        ("shell variables in prose", "sees $PATH=$HOME/bin and"),
        ("image", "\n![alt text](http://example.invalid/x.png)\n"),
        ("angle brackets around a word", "Ask <lead> for the rest"),
        ("a generic type", "returns Optional<int> for"),
        ("an unclosed comment opener", "began <!--\ngenerated"),
        ("a line that is only a tag", "\n\n<details>\n\n"),
        ("arrow and tick", "parse \u2192 verify \u2192 ingest \u2713"),
        ("emoji", "\U0001f680"),
        ("cjk", "\u885b\u661f"),
    ],
)
def test_the_finished_report_has_each_markdown_case(
    fleet: tuple[FakeDriver, Snapshot], case: str, needle: str
) -> None:
    driver, _ = fleet
    assert needle in "\n" + _output(driver, "report"), case


def test_the_reports_prose_is_hard_wrapped_and_one_table_cell_is_not(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, _ = fleet
    lines = _output(driver, "report").splitlines()
    rows = [line for line in lines if line.startswith("|")]
    prose = [line for line in lines if not line.startswith("|") and "](" not in line]
    assert max(map(len, prose)) <= 80
    # Hard-wrapped rather than merely short: there are full lines to re-flow.
    assert sum(len(line) > 70 for line in prose) > 10
    assert max(map(len, rows)) > 200


def test_the_report_node_ended_its_turn_on_the_report(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, snap = fleet
    node = driver.cast["report"]
    record = snap.get(node)
    assert record is not None and record.state is AgentState.AWAITING_INPUT
    assert any(isinstance(o, QuestionPending) and o.node == node for o in snap.needs_you)
    # DETAIL's "what it said" reads this, so the document is reachable from there too.
    assert record.transcript.turn_prose().rstrip().endswith("stay with its caller?")


def test_the_report_interleaves_every_literal_kind_with_its_prose(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, _ = fleet
    kinds = [s.kind for s in driver.transcripts[driver.cast["report"]].segments()]
    for kind in (
        SegmentKind.SYSTEM,
        SegmentKind.REASONING,
        SegmentKind.TOOL_CALL,
        SegmentKind.TOOL_RESULT,
        SegmentKind.ERROR,
    ):
        assert kind in kinds, kind
    # Interleaved, not appended after: a literal segment sits between the two halves
    # of the document, which is what closes an open markdown block mid-report.
    last_two_outputs = [i for i, k in enumerate(kinds) if k is SegmentKind.OUTPUT][-2:]
    between = kinds[last_two_outputs[0] + 1 : last_two_outputs[1]]
    assert SegmentKind.TOOL_CALL in between and SegmentKind.TOOL_RESULT in between


def test_a_tool_result_carries_the_characters_markdown_would_misread(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, _ = fleet
    transcript = driver.transcripts[driver.cast["report"]]
    results = "".join(
        transcript.read(s.start, s.end)
        for s in transcript.segments()
        if s.kind is SegmentKind.TOOL_RESULT
    )
    assert "__init__" in results and "*_offsets" in results and "**/" in results
    # A listing drawn in box characters, and a line no pane is wide enough for.
    assert "\n\u2502   \u2514\u2500\u2500 catalog.html\n" in results
    assert max(map(len, results.splitlines())) > 200


def test_the_compaction_segment_is_on_the_node_the_store_counted_it_for(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, snap = fleet
    node = driver.cast["streaming"]
    kinds = [s.kind for s in driver.transcripts[node].segments()]
    assert SegmentKind.COMPACTION in kinds
    record = snap.get(node)
    assert record is not None and record.context is not None
    assert record.context.compactions == 1


# -- the stream -----------------------------------------------------------------------


def test_the_streaming_session_has_none_of_the_report_until_it_is_stepped(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, snap = fleet
    assert "Parser audit" not in _output(driver, "streaming")
    record = snap.get(driver.cast["streaming"])
    # Not parked: a parked node stays parked, and this one has to read as working.
    assert record is not None and record.state is AgentState.THINKING and not record.pending


def test_the_stream_arrives_in_pieces_and_adds_up_to_the_finished_report() -> None:
    driver, _ = _seeded()
    transcript = driver.transcripts[driver.cast["streaming"]]
    finished = driver.transcripts[driver.cast["report"]]
    lengths = []
    cut_inside_markup = False
    for _ in range(5_000):
        driver._stream_step()
        lengths.append(transcript.published_length)
        text = _output(driver, "streaming")
        # A step that stops between the two asterisks of a pair or part-way through
        # a fence marker: the half-written state a streaming renderer has to survive.
        cut_inside_markup |= text.endswith("*") and not text.endswith("**")
        cut_inside_markup |= text.endswith("``") and not text.endswith("```")
        if driver._stream_rest:
            break
    else:
        pytest.fail("the report never finished streaming")

    growth = [b - a for a, b in zip(lengths, lengths[1:], strict=False)]
    assert len(lengths) > 200
    # Prose arrives a few characters at a time; only a tool segment arrives whole.
    assert sorted(growth)[len(growth) // 2] < 24
    assert cut_inside_markup
    assert _output(driver, "streaming") == _output(driver, "report")

    def after_spawn(t: Transcript) -> list[tuple[SegmentKind, str]]:
        return [
            (s.kind, t.read(s.start, s.end))
            for s in t.segments()
            if s.kind is not SegmentKind.COMPACTION
        ][5:]

    # Same segments in the same order once it has all arrived: chunking prose must
    # not split or reorder what the finished transcript holds as one segment.
    assert after_spawn(transcript) == after_spawn(finished)


def test_the_stream_starts_again_as_a_new_turn_after_resting() -> None:
    driver, _ = _seeded()
    transcript = driver.transcripts[driver.cast["streaming"]]
    while not driver._stream_rest:
        driver._stream_step()
    done = transcript.published_length
    for _ in range(fake_driver._STREAM_REST - 1):
        driver._stream_step()
    assert transcript.published_length == done
    driver._stream_step()
    assert transcript.segments()[-1].kind is SegmentKind.SYSTEM
    driver._stream_step()
    driver._stream_step()
    # The turn boundary is what makes "what it said" the report in flight, not two.
    assert transcript.turn_prose() == ""
    assert transcript.segments()[-1].kind is SegmentKind.REASONING


def test_ticks_move_the_fleet_and_leave_every_fixture_as_it_was_seeded() -> None:
    driver, store = _seeded()
    before = store.snapshot()
    held = {role: driver.transcripts[node].published_length for role, node in driver.cast.items()}
    for _ in range(400):
        driver._tick()
    store.apply_all(driver.bridge.drain())
    snap = store.snapshot()

    assert {r: driver.transcripts[n].published_length for r, n in driver.cast.items()} == held
    # The whole of what waits on the operator, not only its size: two screenshots
    # taken at different moments have to be of the same queue.
    assert [o.key for o in snap.needs_you] == [o.key for o in before.needs_you]
    assert len(snap.approvals) == 7
    for role, node in driver.cast.items():
        was, now = before.get(node), snap.get(node)
        assert was is not None and now is not None
        assert (now.state, now.topic) == (was.state, was.topic), role

    # And something does move, or the guards above prove nothing.
    movers = [n for n in driver.nodes if n not in driver.cast.values()]
    states = {r.state for n in movers if (r := snap.get(n)) is not None}
    assert states - {AgentState.AWAITING_APPROVAL}
    assert any(driver.transcripts[n].published_length > 200 for n in movers)


# -- parked calls ---------------------------------------------------------------------


def test_the_bus_tool_names_are_the_ones_the_gate_sees() -> None:
    assert fake_driver.POST_CONCERN == bus.qualified("post_concern")
    assert fake_driver.DECLARE_TASK == bus.qualified("declare_task")


def test_a_write_of_a_new_file_is_parked_with_a_diff_that_is_all_additions(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    _, snap = fleet
    (write,) = _parked(snap, "Write")
    assert 55 <= len(str(write.raw_args["content"]).splitlines()) <= 70
    assert write.diff is not None
    body = write.diff.splitlines()
    assert body[0] == "--- /dev/null"
    assert all(line.startswith("+") for line in body[3:])
    # One line a diff view cannot show whole, among lines it can.
    widths = sorted(len(line) for line in body[3:])
    assert widths[-1] > 200 and widths[-2] < 100


def _edits(snap: Snapshot) -> tuple[PendingApproval, PendingApproval]:
    """The two parked Edits: the one whose file is not there, then the one whose is."""
    absent, in_place = sorted(
        _parked(snap, "Edit"), key=lambda p: pathlib.Path(str(p.raw_args["file_path"])).exists()
    )
    return absent, in_place


def test_an_edit_whose_file_is_absent_puts_a_removal_and_an_addition_on_one_line(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    _, snap = fleet
    edit, _ = _edits(snap)
    assert not pathlib.Path(str(edit.raw_args["file_path"])).exists()
    assert not str(edit.raw_args["old_string"]).endswith("\n")
    assert not str(edit.raw_args["new_string"]).endswith("\n")
    assert edit.diff is not None
    joined = [line for line in edit.diff.splitlines() if line.startswith("-    return")]
    assert joined == [
        '-    return sum(int(c) for c in digits) % 10+    minus = line[:68].count("-")'
    ]


def test_an_edit_whose_anchor_is_in_the_file_is_a_clean_hunk_of_that_file(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    _, snap = fleet
    _, edit = _edits(snap)
    path = pathlib.Path(str(edit.raw_args["file_path"]))
    old = str(edit.raw_args["old_string"])
    # The anchor is as unterminated as the other Edit's, and present exactly once:
    # what differs between the two fixtures is the file, not the strings.
    assert not old.endswith("\n") and path.read_text(encoding="utf-8").count(old) == 1
    assert edit.diff is not None
    lines = edit.diff.splitlines()
    assert lines[0] == f"--- {path}" and lines[1] == f"+++ {path}"
    # A hunk from inside the file, with context on both sides of the change.
    assert lines[2].startswith("@@ -") and not lines[2].startswith("@@ -1,")
    assert lines[3].startswith(" ") and lines[-1].startswith(" ")
    assert [line for line in lines[3:] if line.startswith("+")] == [
        "+    # What the agent said, as opposed to what it did.",
        '+    NARRATION = "narration"',
    ]
    assert not [line for line in lines[3:] if line.startswith("-")]


def test_a_multiedit_with_three_edits_is_parked(fleet: tuple[FakeDriver, Snapshot]) -> None:
    _, snap = fleet
    (multi,) = _parked(snap, "MultiEdit")
    assert len(multi.raw_args["edits"]) == 3
    assert multi.diff is not None
    assert [line for line in multi.diff.splitlines() if line.startswith("+++")] == [
        "+++ b/edit 1",
        "+++ b/edit 2",
        "+++ b/edit 3",
    ]


def test_a_bash_call_is_parked_with_no_diff(fleet: tuple[FakeDriver, Snapshot]) -> None:
    _, snap = fleet
    (bash,) = _parked(snap, "Bash")
    assert bash.diff is None
    assert len(str(bash.raw_args["command"])) > 90


def test_a_parked_concern_has_a_markdown_body(fleet: tuple[FakeDriver, Snapshot]) -> None:
    driver, snap = fleet
    (concern,) = _parked(snap, fake_driver.POST_CONCERN)
    assert concern.node == driver.cast["builder"]
    assert concern.diff is None
    assert set(concern.raw_args) == {"to", "subject", "body", "task_id"}
    body = str(concern.raw_args["body"])
    assert len(body.split("\n\n")) >= 5
    assert "\n- `tle/ingest.py`" in body and "**already**" in body
    # Text shaped like raw HTML, outside any code span, which a markdown renderer
    # is free to swallow and the operator still has to be able to read.
    for needle in ("Optional<int>", "ask <lead>\n", "starts <!-- generated", "\n\n<details>\n\n"):
        assert needle in body, needle


def test_a_parked_declaration_has_a_multi_paragraph_spec(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, snap = fleet
    (declared,) = _parked(snap, fake_driver.DECLARE_TASK)
    assert declared.node == driver.cast["lead"]
    assert set(declared.raw_args) == {"task_id", "title", "detail", "depends_on", "touches"}
    detail = str(declared.raw_args["detail"])
    assert len(detail.split("\n\n")) >= 5 and "\n1. What changed" in detail
    # It asks for the id the board is already waiting on, and has not been granted.
    assert declared.raw_args["task_id"] == "t9" and "t9" not in snap.tasks


# -- the board ------------------------------------------------------------------------


def test_the_board_has_a_row_in_every_state(fleet: tuple[FakeDriver, Snapshot]) -> None:
    driver, snap = fleet
    rows = {t.id: t for t in board.board_tasks(snap, driver.cast["lead"][0])}
    assert len(rows) == 8

    assert rows["t1"].state is TaskState.COMPLETED and rows["t1"].owner == "explore"
    assert rows["t2"].state is TaskState.CLAIMED and not rows["t2"].owner_gone
    assert rows["t2"].concerns
    assert rows["t3"].blocked_on == ("t2",) and not rows["t3"].missing
    assert rows["t4"].missing == ("t9",)
    assert rows["t5"].state is TaskState.CLAIMED and rows["t5"].owner_gone
    assert rows["t7"].state is TaskState.PENDING and not rows["t7"].blocked_on
    assert rows["t8"].state is TaskState.COMPLETED and rows["t8"].owner == "builder"

    # Given back by the scout, which is why it is pending with a history.
    assert snap.tasks["t7"].released_by == (driver.cast["scout"],)
    # Blocked by a dependency nobody wrote: the board added it for the shared file.
    assert rows["t6"].blocked_on == ("t2",)
    assert fake_driver._BOARD_TASKS[5].id == "t6" and not fake_driver._BOARD_TASKS[5].depends_on


def test_most_task_details_are_multi_paragraph_markdown(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, snap = fleet
    rows = board.board_tasks(snap, driver.cast["lead"][0])
    long = [t for t in rows if len(t.detail.split("\n\n")) >= 2 and "`" in t.detail]
    assert len(long) >= 6
    assert any("\n- " in t.detail for t in long) and any("\n1. " in t.detail for t in long)
    assert sum(bool(t.touches) for t in rows) >= 6
    # And one with nothing at all, which is the row the disclosure has to survive.
    assert any(not t.detail and not t.touches for t in rows)


def test_the_concern_log_has_every_state_and_direction(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, snap = fleet
    log = board.board_concerns(snap, driver.cast["lead"][0])
    delivered = [c for c in log if c.state is ConcernState.DELIVERED]
    assert len(delivered) >= 5
    assert all(len(c.body.split("\n\n")) >= 2 for c in delivered)
    assert {(c.sender, c.recipient) for c in delivered} >= {
        ("reviewer", "lead"),
        ("lead", "builder"),
        ("reviewer", "builder"),
        ("explore", "reviewer"),
    }
    assert [c.id for c in log if c.state is ConcernState.POSTED] == ["fake-c7"]
    assert [c.id for c in log if c.state is ConcernState.WITHDRAWN] == ["fake-c8"]
    assert [c.id for c in log if c.edited] == ["fake-c2"]
    assert [c.id for c in log if c.task_missing] == ["fake-c8"]
    assert any("```python" in c.body for c in delivered)


def test_one_delivered_concern_is_plain_text_with_one_fact_per_line(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, snap = fleet
    (plain,) = [c for c in board.board_concerns(snap, driver.cast["lead"][0]) if c.id == "fake-c6"]
    assert plain.state is ConcernState.DELIVERED
    head, trace = plain.body.split("\n\n")
    # Consecutive lines with no blank between them and no markdown to separate
    # them: the body a soft-break rule folds into one paragraph.
    assert head.splitlines() == [
        "ran: pytest -q tests/test_parse.py -k alpha5",
        "cwd: ~/Source/orbital",
        "exit: 1",
        "covered: no",
    ]
    assert trace.startswith("Traceback (most recent call last):\n  File ")
    assert "\n    assert parse_line_1(LINE)" in trace
    assert not any(mark in plain.body for mark in ("`", "**", "\n- ", "\n1. "))


# -- the rest -------------------------------------------------------------------------


def test_one_session_failed_and_its_error_is_in_its_transcript(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, snap = fleet
    node = driver.cast["failed"]
    (failure,) = [o for o in snap.needs_you if isinstance(o, SessionFailed)]
    assert failure.node == node and failure.error == "tool call rejected"
    last = driver.transcripts[node].segments()[-1]
    assert last.kind is SegmentKind.ERROR


def test_the_scout_delivered_a_markdown_answer(fleet: tuple[FakeDriver, Snapshot]) -> None:
    driver, snap = fleet
    scout = snap.get(driver.cast["scout"])
    assert scout is not None and scout.state is AgentState.DONE
    assert scout.deliverable is not None
    assert scout.deliverable.startswith("## t1")
    assert "\n| Field | Columns | Read as |\n" in scout.deliverable
    # The answer is also the tail of its transcript, as a sub-agent's last message is.
    assert _output(driver, "scout").endswith(scout.deliverable)
    with_one = [n for n in snap.order if (r := snap.get(n)) is not None and r.deliverable]
    assert with_one == [driver.cast["scout"]]


def test_the_brief_is_seeded_under_the_temporary_directory_with_one_entry_overturned(
    fleet: tuple[FakeDriver, Snapshot],
) -> None:
    driver, snap = fleet
    lead = snap.get(driver.cast["lead"])
    assert lead is not None and lead.brief is not None
    directory = pathlib.Path(lead.brief)
    assert pathlib.Path(tempfile.gettempdir()).resolve() in directory.resolve().parents
    entries = brief.derive(brief.read_entries(directory))
    assert [e.is_superseded for e in entries] == [True, False, False]
    assert all("`" in e.entry.body and "\n\n" in e.entry.body for e in entries)
    assert entries[0].entry.body.startswith("# The TLE parser")


def test_two_fleets_carry_the_same_text() -> None:
    def shape(driver: FakeDriver, snap: Snapshot) -> object:
        session = driver.cast["lead"][0]
        return (
            {role: driver.transcripts[node].text() for role, node in driver.cast.items()},
            [(p.tool_name, p.summary, p.diff) for p in snap.approvals],
            [(t.id, t.state, t.detail, t.blocked_on) for t in board.board_tasks(snap, session)],
            [(c.id, c.state, c.body) for c in board.board_concerns(snap, session)],
        )

    (first, first_store), (second, second_store) = _seeded(), _seeded()
    for _ in range(300):
        first._stream_step()
        second._stream_step()
    assert shape(first, first_store.snapshot()) == shape(second, second_store.snapshot())
