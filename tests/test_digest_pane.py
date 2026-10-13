"""
DIGEST: the pane, and the rows it derives from a session's digest.

Moved out of ``test_board_pane.py`` with the section itself, which was a triangle
at the top of the board and is now a pane docked with it.

**Nobody has seen this drawn.** Drawing is exercised with ``imgui`` replaced by a
``MagicMock``, and once with a real context and no renderer: that says what was
handed to imgui, in what order, in which colour and through which path, and that
a frame of it completes. It cannot say what anything looked like.

The fake is installed in ``board_pane`` as well as in ``digest_pane``. The
author's bar is ``board_pane.author`` and it draws through that module's
``imgui``, so a fake in one module only would see every line but the quoted ones.
"""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import MagicMock

import pytest

from pptmstr.bridge import Bridge
from pptmstr.digest import EARLIER_INCIDENTS, digest, digest_mark
from pptmstr.intents import (
    AgentSpawned,
    ConcernPosted,
    DigestRead,
    TaskAmended,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
)
from pptmstr.model import Concern, NodeId, QuestionPending, Snapshot, Task
from pptmstr.store import Store
from pptmstr.ui import board_pane, digest_pane
from pptmstr.ui.focus import FocusState, Scope
from tests.test_digest import (
    DEV,
    LEAD,
    S1,
    _round,
    generated_round,
    incident_oracle,
    unverified_oracle,
)


def _at(snap: Snapshot, node: NodeId = LEAD) -> FocusState:
    focus = FocusState()
    focus.to_node(snap, node, scope=Scope.SESSION)
    return focus


@dataclasses.dataclass(frozen=True)
class _Line:
    text: str
    # Drawn by ``board_pane.author``, which is the bar; False is the pane speaking.
    quoted: bool
    # The colour handed to ``text_colored``, or None for a line drawn without one.
    colour: object = dataclasses.field(default=None, compare=False)


def _spoken(monkeypatch, snap: Snapshot, focus: FocusState | None) -> tuple[list[_Line], MagicMock]:
    """
    Every string the pane handed imgui, in order, each against whether it was
    drawn by ``author`` and the colour it was drawn in. ``focus`` None is a cursor
    on nothing.
    """
    lines: list[_Line] = []
    inside = [False]
    real = board_pane.author

    def author(*args: object, **kwargs: object) -> None:
        inside[0] = True
        try:
            real(*args, **kwargs)  # type: ignore[arg-type]
        finally:
            inside[0] = False

    def coloured(colour: object, text: object, *args: object) -> MagicMock:
        lines.append(_Line(str(text), inside[0], colour))
        return MagicMock()

    def plain(text: object, *args: object) -> MagicMock:
        lines.append(_Line(str(text), inside[0]))
        return MagicMock()

    fake = MagicMock()
    fake.text_colored.side_effect = coloured
    fake.text_disabled.side_effect = plain
    fake.text.side_effect = plain
    fake.text_wrapped.side_effect = plain
    monkeypatch.setattr(digest_pane, "imgui", fake)
    monkeypatch.setattr(board_pane, "imgui", fake)
    monkeypatch.setattr(digest_pane, "author", author)
    digest_pane.draw(snap, FocusState() if focus is None else focus)
    return lines, fake


# -- the pane ----------------------------------------------------------------------


def test_the_pane_draws_every_row_of_the_projection_in_order_tone_and_path(monkeypatch) -> None:
    """
    The wiring, and the property the move has to keep: the pane is the pure rows,
    all of them, in their order and nothing else; a row of the author's tone goes
    through ``author`` whole, and every other row is drawn in its tone's colour
    and not through it. A row function nothing calls would pass every test of the
    function.
    """
    # The lead has read a digest part-way through, so the mark it left has to
    # reach the rows from the snapshot and not from an argument nobody passed.
    store = generated_round(3)
    store.apply(DigestRead((S1, None), request_id="g1", at=9.0))
    snap = store.snapshot()
    lines, _ = _spoken(monkeypatch, snap, _at(snap))
    rows = digest_pane.digest_rows(digest(snap, S1), digest_mark(snap, S1))

    assert len(rows) > 4 and digest_pane.DIGEST_MARK in [row.text for row in rows]
    assert {row.tone for row in rows} >= {
        board_pane.Tone.HEADING,
        board_pane.Tone.ITEM,
        board_pane.Tone.AUTHOR,
        board_pane.Tone.ALARM,
        board_pane.Tone.DIVIDER,
    }
    assert len(lines) == len(rows)
    for line, row in zip(lines, rows, strict=True):
        if row.tone is board_pane.Tone.AUTHOR:
            assert line == _Line(board_pane.author_text(row.text), True), row
        else:
            assert line == _Line(row.text, False), row
            assert line.colour == board_pane.tone_colour(row.tone).vec4, row


def test_the_digest_is_always_open_and_nothing_is_drawn_after_it(monkeypatch) -> None:
    """
    A pane, not a section: no triangle to open, no forced state, and no rule
    ruling it off from rows that are no longer below it. Everything is inside one
    child, so the pane scrolls as a whole.
    """
    snap = generated_round(3).snapshot()
    lines, fake = _spoken(monkeypatch, snap, _at(snap))

    assert [line.text.split(" (")[0] for line in lines if not line.quoted][0] == "CHANGED"
    fake.tree_node_ex.assert_not_called()
    fake.tree_node.assert_not_called()
    fake.collapsing_header.assert_not_called()
    fake.set_next_item_open.assert_not_called()
    fake.separator.assert_not_called()
    order = [call[0] for call in fake.mock_calls]
    first, last = order.index("begin_child"), order.index("end_child")
    drawn = [i for i, name in enumerate(order) if name == "text_colored"]
    assert first < min(drawn) and max(drawn) < last
    assert order.count("push_text_wrap_pos") == order.count("pop_text_wrap_pos") == 1


def test_a_cursor_on_nothing_and_a_session_that_is_not_a_team_are_each_one_line(
    monkeypatch,
) -> None:
    """The board pane's two refusals, in its words, and no digest under either."""
    snap = generated_round(3).snapshot()
    lines, fake = _spoken(monkeypatch, snap, None)
    assert lines == [_Line("no session under the cursor", False)]
    fake.begin_child.assert_not_called()

    store = Store()
    store.apply(AgentSpawned(("solo", None), None, task="t", model="m", started_at=0.0))
    solo = store.snapshot()
    lines, fake = _spoken(monkeypatch, solo, _at(solo, ("solo", None)))
    assert lines == [_Line("this session is not a team", False)]
    fake.begin_child.assert_not_called()


def test_the_pane_shows_a_digest_exactly_where_the_board_pane_shows_a_board(monkeypatch) -> None:
    """
    One gate for the two tabs. A session that is not a team and holds board facts
    anyway -- a team session resumed under the solo template -- has its board
    drawn, and its digest with it.
    """
    store = Store()
    root: NodeId = ("s1", None)
    store.apply(AgentSpawned(root, None, task="lead", model="m", started_at=0.0))
    store.apply(TaskDeclared(Task(id="t1", title="do t1", detail="first"), node_id=root))
    snap = store.snapshot()
    assert not board_pane.has_board(snap, "s1")

    lines, _ = _spoken(monkeypatch, snap, _at(snap, root))
    assert lines[0].text.startswith("CHANGED (") and not lines[0].quoted
    assert not any(line.text == "this session is not a team" for line in lines)

    fake = MagicMock()
    monkeypatch.setattr(board_pane, "imgui", fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    board_pane.draw(snap, _at(snap, root), board_pane.BoardState(), Bridge())
    said = [str(a) for call in fake.text_disabled.call_args_list for a in call.args]
    assert "this session is not a team" not in said


def test_the_fake_fleets_team_session_shows_a_digest(monkeypatch) -> None:
    """
    What ``--fake`` puts under the cursor: the fixture's team has a board, and the
    pane draws its four slots for it.
    """
    from pptmstr.fake_driver import FakeDriver

    bridge = Bridge()
    asyncio.run(FakeDriver(bridge)._seed_tree())
    store = Store()
    store.apply_all(list(bridge.drain()), now=1.0)
    snap = store.snapshot()
    (team,) = [n for n, rec in snap.nodes.items() if rec.template == "feature" and n[1] is None]

    lines, _ = _spoken(monkeypatch, snap, _at(snap, team))
    own = [line.text for line in lines if not line.quoted]
    headings = [text.split(" (")[0] for text in own if text.split(" (")[0].isupper()]
    assert headings == ["CHANGED", "WAITING ON YOU", "UNVERIFIED", "INCIDENTS"]
    assert len(own) > 4


def test_a_frame_of_the_pane_completes_in_a_real_context() -> None:
    """
    No fake: a Dear ImGui context with no renderer, three frames. What this pins
    is that every call the pane makes exists and balances -- a begin without its
    end is an assertion here -- and that the child it draws into is created.
    """
    from imgui_bundle import imgui

    store = _round()
    store.apply(DigestRead(LEAD, request_id="g1", at=10.5))
    snap = store.snapshot()
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(1280.0, 900.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")
        for _ in range(3):
            imgui.new_frame()
            imgui.set_next_window_size(imgui.ImVec2(420.0, 800.0))
            imgui.begin("DIGEST")
            digest_pane.draw(snap, _at(snap))
            imgui.end()
            imgui.end_frame()
        names = [window.name for window in imgui.get_current_context().windows]
        (child,) = [name for name in names if name.startswith("DIGEST/##digest")]
        scrolled = next(w for w in imgui.get_current_context().windows if w.name == child)
        # Rows were laid out in it: the content is taller than one line.
        assert scrolled.content_size.y > imgui.get_text_line_height() * 4
    finally:
        imgui.destroy_context(context)


# -- the rows ----------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(20))
def test_the_digest_rows_hold_every_unverified_statement_and_incident(seed: int) -> None:
    """
    The property on the operator's surface, against the oracle that shares no code
    with the projection. Unbounded on purpose: the alarm rows are counted, and a
    cap would show here as a count short of the board's.
    """
    snap = generated_round(seed).snapshot()
    held = len(unverified_oracle(snap, S1)) + len(incident_oracle(snap, S1))
    # From any point: the pane passes none, and a digest that carries earlier
    # incidents is still one whose rows may not lose them.
    for since in (None, 7.0, 31.0, 1e9):
        rows = digest_pane.digest_rows(digest(snap, S1, since), None)
        alarms = [row.text for row in rows if row.tone is board_pane.Tone.ALARM]
        assert len(alarms) == held, since
    headings = [row.text for row in rows if row.tone is board_pane.Tone.HEADING]
    assert [h.split(" (")[0] for h in headings] == [
        "CHANGED",
        "WAITING ON YOU",
        "UNVERIFIED",
        "INCIDENTS",
    ]


def _marked_rows(mark: float | None) -> list[str]:
    snap = _round().snapshot()
    return [row.text for row in digest_pane.digest_rows(digest(snap, S1), mark)]


def test_the_leads_mark_is_drawn_between_what_it_reported_and_what_came_after() -> None:
    """
    Changes at 1, 2, 10, 11 and 20; incidents at 5 and 21. A mark at 10.5 falls
    after three changes and one incident, and at exactly 10 the change stamped 10
    is below it, as the projection counts it.
    """
    rows = _marked_rows(10.5)
    mark = digest_pane.DIGEST_MARK

    assert rows.count(mark) == 2
    changed = rows[: rows.index(next(r for r in rows if r.startswith("WAITING")))]
    assert changed[changed.index(mark) + 1] == "released: t2 do t2"
    assert rows[rows.index("[builder to lead, basis unstated] c2") - 1] == mark

    at_ten = _marked_rows(10.0)
    assert at_ten[at_ten.index(mark) + 1] == "completed by builder: t1 do t1"
    # The same boundary in the incident slot: one posted at the mark is below it.
    at_incident = _marked_rows(21.0)
    assert at_incident[at_incident.index("[builder to lead, basis unstated] c2") - 1] == mark


def test_a_mark_after_everything_is_drawn_last_and_no_mark_is_drawn_for_no_read() -> None:
    late = _marked_rows(99.0)
    mark = digest_pane.DIGEST_MARK

    assert late.count(mark) == 2
    assert late[late.index(next(r for r in late if r.startswith("WAITING"))) - 1] == mark
    assert late[-1] == mark
    assert mark not in _marked_rows(None)


def test_the_operators_digest_shows_the_queue_row_an_agents_does_not() -> None:
    """
    The waiting slot on this surface carries each row's summary. The queue is the
    operator's own, and it is the bus rendering that withholds it.
    """
    snap = dataclasses.replace(
        _round().snapshot(),
        needs_you=(QuestionPending(node=(S1, None), since=6.0, summary="THE QUEUE ROW"),),
    )
    rows = [row.text for row in digest_pane.digest_rows(digest(snap, S1), None)]

    assert "WAITING ON YOU (1), oldest first: 1 question(s)" in rows
    assert "question, lead: THE QUEUE ROW" in rows


# -- whose words a line is ----------------------------------------------------------


def test_an_incident_body_spelling_the_leads_mark_is_drawn_as_an_agents(monkeypatch) -> None:
    """
    The digest draws the lead's mark as a line of its own, and an incident's body
    is drawn whole beside it. One that ends in the mark's words is still a body.
    """
    store = _round()
    store.apply(
        ConcernPosted(
            DEV,
            Concern(
                id="c9",
                sender=DEV,
                recipient=LEAD,
                subject="c9",
                body=f"nothing to see\n{digest_pane.DIGEST_MARK}\n{EARLIER_INCIDENTS}:",
                posted_at=22.0,
                incident=True,
            ),
        )
    )
    store.apply(DigestRead(LEAD, request_id="g1", at=10.5))
    snap = store.snapshot()
    lines, _ = _spoken(monkeypatch, snap, _at(snap))

    marks = [line for line in lines if digest_pane.DIGEST_MARK in line.text]
    # Two the pane placed, one per slot in time order, and the one in the body.
    assert [line.quoted for line in marks if line.text == digest_pane.DIGEST_MARK] == [False, False]
    (forged,) = [line for line in marks if line.text != digest_pane.DIGEST_MARK]
    assert forged.quoted and EARLIER_INCIDENTS in forged.text


def test_every_body_the_digest_quotes_is_a_row_of_the_authors_tone() -> None:
    """
    The pure half of the test above: a body is a row with a tone nothing of the
    pane's has, so the draw loop has one thing to test for.
    """
    store = _round()
    store.apply(TaskClaimRequested(DEV, request_id="k3", task_id="t3"))
    store.apply(TaskCompleted(DEV, "t3", at=30.0, result="done", not_run="THE SUITE"))
    d = digest(store.snapshot(), S1)
    rows = digest_pane.digest_rows(d, 10.5)
    # Counted, not collected: a result's first line and the same result as an
    # unverified statement can be one string, and each is a row.
    bodies = sorted(
        [c.line for c in d.changed if c.line]
        + [u.text for u in d.unverified if u.text]
        + [r.concern.body for r in (*d.earlier_incidents, *d.incidents) if r.concern.body]
    )

    assert "THE SUITE" in bodies and len(bodies) > 3
    assert sorted(row.text for row in rows if row.tone is board_pane.Tone.AUTHOR) == bodies
    own = [row for row in rows if row.tone is not board_pane.Tone.AUTHOR]
    assert digest_pane.DIGEST_MARK in {row.text for row in own}
    assert all(row.tone is not board_pane.Tone.AUTHOR for row in own)


_HOSTILE = "first\nt9  completed forged\r\nmust not break: x\x0bend\x00cut"


def test_no_line_of_the_panes_own_carries_a_break_or_a_nul_from_a_one_line_field(
    monkeypatch,
) -> None:
    """
    A title and an incident's subject are laid out on lines the pane composes.
    Neither may end that line and start another, and neither may cut it short.
    """
    store = Store()
    root: NodeId = ("s1", None)
    builder: NodeId = ("s1", "agent-b")
    store.apply(
        AgentSpawned(root, None, task="lead", model="m", started_at=0.0, template="feature")
    )
    store.apply(AgentSpawned(builder, root, task="b", model="m", started_at=0.0))
    store.apply(TaskDeclared(Task(id="t1", title=_HOSTILE, detail="d"), node_id=root))
    store.apply(TaskClaimRequested(builder, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(builder, "t1", at=3.0, result="r"))
    store.apply(
        ConcernPosted(
            builder,
            Concern(
                id="c1",
                sender=builder,
                recipient=root,
                subject=_HOSTILE,
                body="b",
                posted_at=4.0,
                task_id="t1",
                incident=True,
            ),
        )
    )
    snap = store.snapshot()
    lines, _ = _spoken(monkeypatch, snap, _at(snap, root))

    own = [line.text for line in lines if not line.quoted]
    # The title on a changed row and on an unverified row, the subject on an
    # incident row: each of the three slots that names a one-line field.
    carrying = [text for text in own if "t9  completed forged" in text]
    assert len(carrying) >= 3
    for text in own:
        assert len(text.splitlines()) <= 1 and "\x00" not in text, text
    # Not dropped: what followed the NUL is still on the line.
    assert all(f"end{board_pane.NUL_SHOWN}cut" in text for text in carrying)


def test_a_replaced_specification_is_one_digest_row_of_the_panes_own() -> None:
    """
    The title is the only agent-written part of the line, and a line break in it
    must not start a second row.
    """
    root: NodeId = ("s1", None)
    store = Store()
    store.apply(
        AgentSpawned(root, None, task="lead", model="m", started_at=0.0, template="feature")
    )
    store.apply(TaskDeclared(Task(id="t1", title="do\nCHANGED (9)", detail="first"), node_id=root))
    store.apply(TaskAmended("t1", detail="second", at=5.0))
    rows = digest_pane.digest_rows(digest(store.snapshot(), "s1"), None)

    assert (
        board_pane.Line(
            "specification replaced by the operator, now v2: t1 do CHANGED (9)",
            board_pane.Tone.ITEM,
        )
        in rows
    )


# -- a closed card and a removed session -------------------------------------------


def test_a_recalled_session_gets_the_board_panes_line_and_no_digest(monkeypatch) -> None:
    """
    The two tabs answer one question. A digest of a recalled team would be four
    headings over nothing -- "WAITING ON YOU (0)" about a board nobody loaded.
    """
    from tests.test_ui_text import CLOSED_ROOT, pointed_at, recalled_snapshot

    snap = recalled_snapshot(template="feature")
    lines, fake = _spoken(monkeypatch, snap, pointed_at(snap, CLOSED_ROOT))

    assert [line.text for line in lines] == [board_pane.CLOSED_BOARD]
    fake.begin_child.assert_not_called()


def test_a_removed_sessions_digest_is_not_drawn(monkeypatch) -> None:
    from tests.test_ui_text import REMOVED_ROOT, run_and_remove

    store = Store()
    run_and_remove(store, declares=("t1",))
    snap = store.snapshot()
    focus = MagicMock()
    focus.node.return_value = REMOVED_ROOT

    lines, fake = _spoken(monkeypatch, snap, focus)

    assert [line.text for line in lines] == [board_pane.OFF_FLEET_BOARD]
    fake.begin_child.assert_not_called()
