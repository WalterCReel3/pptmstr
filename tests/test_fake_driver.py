"""
The fixture ``--fake`` runs on, where it is the only thing that puts a state on
screen. A surface drawn only against this fixture is drawn against whatever this
seeds, so what it seeds is pinned.
"""

from __future__ import annotations

import asyncio

from pptmstr.bridge import Bridge
from pptmstr.fake_driver import FakeDriver
from pptmstr.intents import DecisionAnswered, DecisionDismissed
from pptmstr.model import DecisionOwed, Snapshot, Standing
from pptmstr.store import Store
from pptmstr.transcript import SegmentKind, Transcript


def _seeded() -> tuple[Snapshot, list[object]]:
    """The store after the fixture's opening tree, and every intent that built it."""
    bridge = Bridge()
    asyncio.run(FakeDriver(bridge)._seed_tree())
    intents = list(bridge.drain())
    store = Store()
    store.apply_all(intents, now=1.0)
    return store.snapshot(), list(intents)


def test_the_fixture_seeds_one_open_decision_and_one_answered() -> None:
    """
    Both lives of a decision: the open one is in the operator's queue with every
    field its row is composed from, and the answered one has left the queue and
    holds an answer nobody has read back yet.
    """
    snap, _ = _seeded()

    by_state = {d.is_open: d for d in snap.decisions.values()}
    assert len(snap.decisions) == 2 and set(by_state) == {True, False}
    waiting, answered = by_state[True], by_state[False]

    owed = [o for o in snap.needs_you if isinstance(o, DecisionOwed)]
    assert [o.decision.id for o in owed] == [waiting.id]
    assert (waiting.standing, waiting.reversible) == (Standing.OPERATORS, False)
    assert waiting.asker[1] is not None and waiting.session_id == answered.session_id
    # It names a task that is on the same session's board.
    assert "t2" in waiting.blocks and snap.tasks["t2"].belongs_to(waiting.session_id)

    assert (answered.standing, answered.reversible) == (Standing.TAKEN, None)
    assert answered.asker[1] is None
    assert answered.answer is not None and "\n" in answered.answer
    assert answered.first_delivered_at is None


def test_both_seeded_decisions_keep_their_evidence_apart_from_their_recommendation() -> None:
    snap, _ = _seeded()

    for decision in snap.decisions.values():
        assert decision.evidence.strip() and decision.recommendation.strip()
        assert decision.evidence != decision.recommendation
        assert decision.recommendation not in decision.evidence


def test_the_fixtures_answer_is_the_operators_and_would_not_land_as_an_agents() -> None:
    """
    The fixture stands in for the person at the pane, so its answer names no
    node. The counterweight: the same answer naming the asker changes nothing,
    which is what keeps this the only way the fixture could have answered.
    """
    snap, intents = _seeded()

    ended = [i for i in intents if isinstance(i, DecisionAnswered | DecisionDismissed)]
    assert [(i.decision_id, i.node_id) for i in ended] == [("fake-d2", None)]

    store = Store()
    as_an_agent = [
        (
            DecisionAnswered(i.decision_id, i.answer, node_id=snap.decisions["fake-d2"].asker)
            if isinstance(i, DecisionAnswered)
            else i
        )
        for i in intents
    ]
    store.apply_all(as_an_agent, now=1.0)  # type: ignore[arg-type]
    assert store.snapshot().decisions["fake-d2"].is_open


# The report's tool lines as the fixture wrote them before they were entries --
# literals, so a fixture screenshot cannot change by a byte without this failing.
_REPORT_TOOL_LINES = (
    (SegmentKind.TOOL_CALL, "\nRead(file_path=tle/parse.py)\n"),
    (
        SegmentKind.TOOL_RESULT,
        'Read -> {"lines": 212, "symbols": ["__init__", "parse_line_1", "parse_line_2", '
        '"_field"], "globs": ["tle/*_offsets.py", "tests/**/test_*.py"], "imports": '
        '["dataclasses", "datetime", "tle.offsets._FIELD_OFFSETS"], "first_line": '
        '"1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927"}\n',
    ),
    (SegmentKind.TOOL_CALL, "\nBash(command=tree tle tests)\n"),
    (
        SegmentKind.TOOL_RESULT,
        "Bash -> tle\n"
        "├── __init__.py\n"
        "├── offsets.py\n"
        "└── parse.py\n"
        "tests\n"
        "├── data\n"
        "│   └── catalog.html\n"
        "├── helpers.py\n"
        "└── test_parse.py\n",
    ),
    (SegmentKind.TOOL_CALL, "\nBash(command=pytest -q tests/test_parse.py)\n"),
    (
        SegmentKind.ERROR,
        "Bash -> exit 1\n"
        "FAILED tests/test_parse.py::test_checksum_counts_minus - AssertionError: 4 != 5\n"
        "1 failed, 37 passed in 0.41s\n",
    ),
    (SegmentKind.TOOL_CALL, "\nGrep(pattern=foo_bar_baz, path=tle)\n"),
    (
        SegmentKind.TOOL_RESULT,
        "Grep -> tests/helpers.py:41: foo_bar_baz = Field.__new__(Field)  # *not* validated\n",
    ),
)


def _tool_lines(transcript: Transcript) -> list[tuple[SegmentKind, str]]:
    tool_kinds = {SegmentKind.TOOL_CALL, SegmentKind.TOOL_RESULT, SegmentKind.ERROR}
    return [
        (s.kind, transcript.read(s.start, s.end))
        for s in transcript.segments()
        if s.kind in tool_kinds
    ]


def test_the_seeded_report_writes_its_tool_lines_as_it_always_did() -> None:
    driver = FakeDriver(Bridge())
    asyncio.run(driver._seed_tree())
    transcript = driver.transcripts[driver.cast["report"]]
    # After the opening lines every spawned session is given.
    assert _tool_lines(transcript)[-len(_REPORT_TOOL_LINES) :] == list(_REPORT_TOOL_LINES)


def test_the_seeded_report_holds_its_tool_calls_as_entries() -> None:
    """A probe or screenshot of the coming tool block needs entries to draw from."""
    driver = FakeDriver(Bridge())
    asyncio.run(driver._seed_tree())
    transcript = driver.transcripts[driver.cast["report"]]
    entries = transcript.tool_entries(0, transcript.published_length)
    assert [(e.name, e.result is not None) for e in entries] == [
        ("Read", True),
        ("Bash", True),
        ("Bash", True),
        ("Grep", True),
    ]
    pytest_run = entries[2]
    assert pytest_run.input == {"command": "pytest -q tests/test_parse.py"}
    assert pytest_run.result is not None and pytest_run.result.is_error


def test_the_streamed_report_writes_the_same_tool_lines_and_entries() -> None:
    driver = FakeDriver(Bridge())
    asyncio.run(driver._seed_tree())
    node = driver.cast["streaming"]
    transcript = driver.transcripts[node]
    before = transcript.published_length
    while not driver._stream_rest:
        driver._stream_step()
    lines = [
        (s.kind, transcript.read(max(s.start, before), s.end))
        for s in transcript.segments()
        if s.end > before
        and s.kind in {SegmentKind.TOOL_CALL, SegmentKind.TOOL_RESULT, SegmentKind.ERROR}
    ]
    assert lines == list(_REPORT_TOOL_LINES)
    entries = transcript.tool_entries(before, transcript.published_length)
    assert len(entries) == 4 and all(e.result is not None for e in entries)
