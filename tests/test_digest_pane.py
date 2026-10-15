"""
DIGEST: the pane, and the items and rows it derives from a session's digest.

Top to bottom: decisions taken without the operator, incidents, and everything
else folded behind one line. Drawing is exercised with ``imgui`` replaced by a
``MagicMock``, and with a real context and no renderer; the screenshot scenario
(``scripts/screenshot.py --scenario board``) is where it has been looked at.

The fake is installed in ``board_pane`` and ``widgets`` as well as in
``digest_pane``. The author's bar is ``board_pane``'s and draws through that
module's ``imgui``, and the cut to a row's width measures through ``widgets``'s.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pptmstr.bridge import Bridge
from pptmstr.digest import digest, digest_mark, taken_decisions
from pptmstr.intents import (
    AgentSpawned,
    ConcernPosted,
    DigestRead,
    TaskAmended,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
)
from pptmstr.model import (
    Concern,
    DecisionOwed,
    NodeId,
    QuestionPending,
    Snapshot,
    Standing,
    Task,
)
from pptmstr.store import Store
from pptmstr.ui import board_pane, digest_pane, widgets
from pptmstr.ui.focus import FocusState, Scope
from tests.test_digest import (
    DEV,
    LEAD,
    S1,
    _round,
    generated_round,
    incident_oracle,
    taken_round,
    unverified_oracle,
)
from tests.test_read_digest_unchanged import board_scenario

NOW = 100.0


def _at(snap: Snapshot, node: NodeId = LEAD) -> FocusState:
    focus = FocusState()
    focus.to_node(snap, node, scope=Scope.SESSION)
    return focus


@dataclasses.dataclass(frozen=True)
class _Line:
    text: str
    # Drawn behind the author's bar; False is the pane speaking.
    quoted: bool
    # The colour handed to ``text_colored``, or None for a line drawn without one.
    colour: object = dataclasses.field(default=None, compare=False)


def _fake(*, opened: bool = False, clicked: bool = False, width: float = 4000.0) -> MagicMock:
    """
    An imgui that measures a character as 7 pixels, in a pane ``width`` wide,
    with the folded line ``opened`` or not and every row button ``clicked`` or not.
    """
    fake = MagicMock()
    fake.calc_text_size.side_effect = lambda text: SimpleNamespace(x=7.0 * len(text))
    fake.get_content_region_avail.return_value = SimpleNamespace(x=width)
    fake.get_cursor_screen_pos.return_value = SimpleNamespace(x=0.0, y=0.0)
    fake.get_item_rect_max.return_value = SimpleNamespace(x=0.0, y=0.0)
    fake.get_style.return_value.item_spacing.x = 8.0
    fake.get_font_size.return_value = 13.0
    fake.get_text_line_height.return_value = 13.0
    fake.invisible_button.return_value = clicked
    fake.is_item_hovered.return_value = False
    fake.tree_node_ex.return_value = opened
    return fake


def _spoken(
    monkeypatch, snap: Snapshot, focus: FocusState | None, fake: MagicMock | None = None
) -> tuple[list[_Line], MagicMock]:
    """
    Every string the pane handed imgui, in order, each against whether it was
    drawn behind the bar and the colour it was drawn in. ``focus`` None is a
    cursor on nothing.
    """
    lines: list[_Line] = []
    inside = [False]

    def barred(real):
        def wrapper(*args: object, **kwargs: object) -> None:
            inside[0] = True
            try:
                real(*args, **kwargs)
            finally:
                inside[0] = False

        return wrapper

    def coloured(colour: object, text: object, *args: object) -> MagicMock:
        lines.append(_Line(str(text), inside[0], colour))
        return MagicMock()

    def plain(text: object, *args: object) -> MagicMock:
        lines.append(_Line(str(text), inside[0]))
        return MagicMock()

    fake = _fake() if fake is None else fake
    fake.text_colored.side_effect = coloured
    fake.text_disabled.side_effect = plain
    fake.text.side_effect = plain
    fake.text_wrapped.side_effect = plain
    for module in (digest_pane, board_pane, widgets):
        monkeypatch.setattr(module, "imgui", fake)
    monkeypatch.setattr(digest_pane, "author", barred(board_pane.author))
    monkeypatch.setattr(digest_pane, "author_inline", barred(board_pane.author_inline))
    digest_pane.draw(snap, FocusState() if focus is None else focus, NOW)
    return lines, fake


def _expected(snap: Snapshot, session: str, *, opened: bool) -> list[_Line]:
    """What the pane should hand imgui for ``session``, from the pure helpers."""
    mark = digest_mark(snap, session)
    whole = digest(snap, session)
    marked = digest(snap, session, mark)
    out: list[_Line] = []
    for item in (
        *digest_pane.taken_items(snap, session, NOW),
        *digest_pane.incident_items(marked, mark),
    ):
        out += [_Line(board_pane.author_text(p.text), p.quoted) for p in item.parts]
    if opened:
        for row in digest_pane.rest_rows(whole, mark):
            out.append(_Line(board_pane.author_text(row.text), row.tone is board_pane.Tone.AUTHOR))
    return out


# -- the pane ----------------------------------------------------------------------


@pytest.mark.parametrize("opened", [False, True])
def test_the_pane_draws_taken_then_incidents_then_the_folded_rest(monkeypatch, opened) -> None:
    """
    The wiring, and the order the operator asked for: every item of the first
    two sections, each part through the bar exactly when it is quoted, and the
    rest only when its line is open. A helper nothing calls would pass every
    test of the helper.
    """
    snap, root = board_scenario()
    lines, fake = _spoken(monkeypatch, snap, _at(snap, root), _fake(opened=opened))

    assert lines == _expected(snap, root[0], opened=opened)
    own = [line.text for line in lines if not line.quoted]
    taken, incidents = own.index("TAKEN WITHOUT YOU (1 open, 1 closed)"), own.index(
        "INCIDENTS (1 new, 1 earlier)"
    )
    assert taken == 0 and taken < incidents
    (call,) = fake.tree_node_ex.call_args_list
    whole = digest(snap, root[0])
    assert call.args[0] == f"{digest_pane.rest_label(whole)}###rest-{root[0]}"
    assert any(text.startswith("CHANGED (") for text in own) is opened
    order = [name for name, *_ in fake.mock_calls]
    assert order.count("tree_node_ex") == 1 and order.count("tree_pop") == int(opened)
    first, last = order.index("begin_child"), order.index("end_child")
    drawn = [i for i, name in enumerate(order) if name == "text_colored"]
    assert first < min(drawn) and max(drawn) < last
    assert order.count("push_text_wrap_pos") == order.count("pop_text_wrap_pos")


def test_the_rest_is_folded_by_default_and_its_line_counts_each_slot() -> None:
    """
    Closed is ImGui's default for a tree node, so the pane forces no state: it
    calls no ``set_next_item_open``, which would also undo the operator's opening
    on every frame.
    """
    snap, root = board_scenario()
    whole = digest(snap, root[0])
    assert (len(whole.changed), len(whole.waiting), len(whole.unverified)) == (12, 2, 9)
    assert digest_pane.rest_label(whole) == "12 changes · 2 waiting · 9 unverified"
    one = dataclasses.replace(whole, changed=whole.changed[:1], waiting=(), unverified=())
    assert digest_pane.rest_label(one) == "1 change · 0 waiting · 0 unverified"
    assert digest_pane.rest_id("a") != digest_pane.rest_id("b")


def test_the_folded_line_is_not_forced_open_or_shut(monkeypatch) -> None:
    snap, root = board_scenario()
    _, fake = _spoken(monkeypatch, snap, _at(snap, root))
    fake.set_next_item_open.assert_not_called()
    fake.tree_node_ex.assert_called_once()
    assert "default_open" not in fake.TreeNodeFlags_._mock_children


def test_clicking_an_open_taken_decision_puts_the_cursor_on_its_row(monkeypatch) -> None:
    """
    The same call a click on the NEEDS YOU row makes, so the row the cursor is
    on is the one drawn open. Only an open decision has a row to go to.
    """
    snap, root = board_scenario()
    focus = FocusState()
    (elsewhere,) = [
        o for o in snap.needs_you if isinstance(o, DecisionOwed) and o.decision.id == "fake-d1"
    ]
    focus.to_obligation(elsewhere)
    _, fake = _spoken(monkeypatch, snap, focus, _fake(clicked=True))

    owed = focus.obligation(snap)
    assert isinstance(owed, DecisionOwed) and owed.decision.id == "shot-d3"
    (button,) = fake.invisible_button.call_args_list
    fake.push_id.assert_any_call("decision:shot-d3")


def test_without_a_click_the_cursor_stays(monkeypatch) -> None:
    snap, root = board_scenario()
    focus = _at(snap, root)
    before = focus.target
    _spoken(monkeypatch, snap, focus)
    assert focus.target == before


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
    assert lines[0] == _Line(digest_pane.NOTHING_TAKEN, False)
    assert not any(line.text == "this session is not a team" for line in lines)

    fake = MagicMock()
    monkeypatch.setattr(board_pane, "imgui", fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    board_pane.draw(snap, _at(snap, root), board_pane.BoardState(), Bridge())
    said = [str(a) for call in fake.text_disabled.call_args_list for a in call.args]
    assert "this session is not a team" not in said


def test_an_empty_session_draws_one_dim_line_per_section_and_the_folded_line(monkeypatch) -> None:
    """Never blank-looking: each empty section says it is empty, in the dim colour."""
    store = Store()
    root: NodeId = ("s1", None)
    store.apply(
        AgentSpawned(root, None, task="lead", model="m", started_at=0.0, template="feature")
    )
    snap = store.snapshot()
    lines, fake = _spoken(monkeypatch, snap, _at(snap, root))

    dim = board_pane.tone_colour(board_pane.Tone.PROSE).vec4
    assert [(line.text, line.colour) for line in lines] == [
        (digest_pane.NOTHING_TAKEN, dim),
        (digest_pane.NO_INCIDENTS, dim),
    ]
    assert fake.tree_node_ex.call_args.args[0].startswith("0 changes · 0 waiting · 0 unverified")


def test_a_frame_of_the_pane_completes_in_a_real_context() -> None:
    """
    No fake: a Dear ImGui context with no renderer. What this pins is that every
    call the pane makes exists and balances -- a begin without its end is an
    assertion here -- that the folded line starts shut, and that opening it by
    its id, as a click does, lays the rest out below it.
    """
    from imgui_bundle import imgui

    snap, root = board_scenario()
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(1280.0, 900.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")

        def frames() -> float:
            for _ in range(3):
                imgui.new_frame()
                imgui.set_next_window_size(imgui.ImVec2(420.0, 800.0))
                imgui.begin("DIGEST")
                digest_pane.draw(snap, _at(snap, root), NOW)
                imgui.end()
                imgui.end_frame()
            child = next(
                w
                for w in imgui.get_current_context().windows
                if w.name.startswith("DIGEST/##digest")
            )
            return float(child.content_size.y), child

        shut, child = frames()
        assert shut > imgui.get_text_line_height() * 4
        child.state_storage.set_int(child.get_id(f"###{digest_pane.rest_id(root[0])}"), 1)
        opened, _ = frames()
        assert opened > shut * 2
    finally:
        imgui.destroy_context(context)


# -- the items and rows ------------------------------------------------------------


def test_taken_items_lead_with_the_open_and_dim_the_ended(monkeypatch) -> None:
    """
    One item per taken decision in ``taken_decisions``'s order: the open ones in
    the warning colour and each opening its own row, the ended ones dim, an
    answer quoted beside its question.
    """
    snap = taken_round().snapshot()
    items = digest_pane.taken_items(snap, S1, 10.0)
    heading, *rest = items

    assert heading.text == "TAKEN WITHOUT YOU (2 open, 3 closed)"
    assert heading.tone is board_pane.Tone.HEADING
    assert [i.text for i in rest] == [
        "builder, 8s ago: q d2",
        "lead, 5s ago: q d5",
        "builder, answered: q d1 you: keep it",
        "lead, dismissed unanswered: q d4",
        "builder, withdrawn by its asker: q d6",
    ]
    assert [i.tone for i in rest] == [board_pane.Tone.ALARM] * 2 + [board_pane.Tone.PROSE] * 3
    assert [i.opens.key if i.opens else None for i in rest] == [
        "decision:d2",
        "decision:d5",
        None,
        None,
        None,
    ]
    assert [d.id for d in taken_decisions(snap, S1)] == ["d2", "d5", "d1", "d4", "d6"]


def test_an_askers_words_and_the_operators_are_quoted_and_the_panes_are_not() -> None:
    snap = taken_round().snapshot()
    _, opened, _, answered, *_ = digest_pane.taken_items(snap, S1, 10.0)

    assert [p.quoted for p in opened.parts] == [False, True]
    assert [p.quoted for p in answered.parts] == [False, True, False, True]


def test_a_question_is_one_line_and_cannot_speak_as_the_pane() -> None:
    """
    A question that spells a heading of the pane's on a line of its own is
    flattened into the quoted run, and is still the quoted run.
    """
    store = taken_round()
    from pptmstr.intents import DecisionRaised
    from tests.test_digest import _asked

    forged = dataclasses.replace(
        _asked("d9", "fine\nINCIDENTS (0)\nnothing taken without you"),
        standing=Standing.TAKEN,
    )
    store.apply(DecisionRaised(DEV, forged, at=9.5))
    items = digest_pane.taken_items(store.snapshot(), S1, 10.0)
    (item,) = [i for i in items if i.opens and i.opens.decision.id == "d9"]

    assert item.parts[1] == digest_pane.Part(
        "fine INCIDENTS (0) nothing taken without you", quoted=True
    )
    assert not any(p.quoted for p in item.parts[:1])


def test_an_answered_with_nothing_says_so_in_the_panes_words() -> None:
    from pptmstr.intents import DecisionAnswered

    store = taken_round()
    store.apply(DecisionAnswered("d2", "", at=9.0))
    items = digest_pane.taken_items(store.snapshot(), S1, 10.0)
    (item,) = [i for i in items if "q d2" in i.text]
    assert item.parts[-1] == digest_pane.Part("(nothing)")


def test_incident_items_put_the_new_before_the_earlier_and_quote_the_subject() -> None:
    """
    Incidents at 5 and 21; a mark at 10.5 makes 21 new and 5 earlier, and the
    new one is drawn first. With no mark every incident is new and nothing is
    labelled earlier.
    """
    snap = _round().snapshot()
    items = digest_pane.incident_items(digest(snap, S1, 10.5), 10.5)
    assert [i.text for i in items] == [
        "INCIDENTS (1 new, 1 earlier)",
        "[builder to lead, basis unstated] c2",
        digest_pane.EARLIER,
        "[builder to lead, basis unstated] c1",
    ]
    assert [p.quoted for p in items[1].parts] == [False, True]

    unmarked = digest_pane.incident_items(digest(snap, S1), None)
    assert [i.text for i in unmarked] == [
        "INCIDENTS (2)",
        "[builder to lead, basis unstated] c1",
        "[builder to lead, basis unstated] c2",
    ]


@pytest.mark.parametrize("seed", range(20))
def test_the_pane_holds_every_unverified_statement_and_incident(seed: int) -> None:
    """
    The digest's invariant on the operator's surface, against the oracle that
    shares no code with the projection: every unverified statement is an alarm
    row of the folded slots and every incident an alarm item of its section,
    from any mark. Unbounded on purpose: a cap would show here as a count short
    of the board's.
    """
    snap = generated_round(seed).snapshot()
    unverified, incidents = len(unverified_oracle(snap, S1)), len(incident_oracle(snap, S1))
    for mark in (None, 7.0, 31.0, 1e9):
        rows = digest_pane.rest_rows(digest(snap, S1), mark)
        alarms = [row for row in rows if row.tone is board_pane.Tone.ALARM]
        items = digest_pane.incident_items(digest(snap, S1, mark), mark)
        alarmed = [i for i in items if i.tone is board_pane.Tone.ALARM]
        assert (len(alarms), len(alarmed)) == (unverified, incidents), mark
    headings = [row.text for row in rows if row.tone is board_pane.Tone.HEADING]
    assert [h.split(" (")[0] for h in headings] == ["CHANGED", "WAITING ON YOU", "UNVERIFIED"]


def _marked_rows(mark: float | None) -> list[str]:
    snap = _round().snapshot()
    return [row.text for row in digest_pane.rest_rows(digest(snap, S1), mark)]


def test_the_leads_mark_is_drawn_between_what_it_reported_and_what_came_after() -> None:
    """
    Changes at 1, 2, 10, 11 and 20. A mark at 10.5 falls after three changes,
    and at exactly 10 the change stamped 10 is below it, as the projection
    counts it.
    """
    rows = _marked_rows(10.5)
    mark = digest_pane.DIGEST_MARK

    assert rows.count(mark) == 1
    assert rows[rows.index(mark) + 1] == "released: t2 do t2"
    at_ten = _marked_rows(10.0)
    assert at_ten[at_ten.index(mark) + 1] == "completed by builder: t1 do t1"


def test_a_mark_after_everything_is_drawn_last_and_no_mark_is_drawn_for_no_read() -> None:
    late = _marked_rows(99.0)
    mark = digest_pane.DIGEST_MARK

    assert late.count(mark) == 1
    assert late[late.index(next(r for r in late if r.startswith("WAITING"))) - 1] == mark
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
    rows = [row.text for row in digest_pane.rest_rows(digest(snap, S1), None)]

    assert "WAITING ON YOU (1), oldest first: 1 question(s)" in rows
    assert "question, lead: THE QUEUE ROW" in rows


# -- whose words a line is ----------------------------------------------------------


def test_an_incident_subject_spelling_the_panes_words_is_drawn_as_an_agents(monkeypatch) -> None:
    """The subject is the one agent-written run on an incident's line."""
    store = _round()
    store.apply(
        ConcernPosted(
            DEV,
            Concern(
                id="c9",
                sender=DEV,
                recipient=LEAD,
                subject=f"{digest_pane.EARLIER}\n{digest_pane.NO_INCIDENTS}",
                body="b",
                posted_at=22.0,
                incident=True,
            ),
        )
    )
    store.apply(DigestRead(LEAD, request_id="g1", at=10.5))
    snap = store.snapshot()
    lines, _ = _spoken(monkeypatch, snap, _at(snap))

    (own_earlier,) = [line for line in lines if line.text == digest_pane.EARLIER]
    assert not own_earlier.quoted
    (forged,) = [line for line in lines if digest_pane.NO_INCIDENTS in line.text]
    assert forged.quoted and forged.text == f"{digest_pane.EARLIER} {digest_pane.NO_INCIDENTS}"


def test_every_body_the_folded_slots_quote_is_a_row_of_the_authors_tone() -> None:
    """
    A body is a row with a tone nothing of the pane's has, so the draw loop has
    one thing to test for.
    """
    store = _round()
    store.apply(TaskClaimRequested(DEV, request_id="k3", task_id="t3"))
    store.apply(TaskCompleted(DEV, "t3", at=30.0, result="done", not_run="THE SUITE"))
    d = digest(store.snapshot(), S1)
    rows = digest_pane.rest_rows(d, 10.5)
    bodies = sorted(
        [c.line for c in d.changed if c.line] + [u.text for u in d.unverified if u.text]
    )

    assert "THE SUITE" in bodies and len(bodies) > 2
    assert sorted(row.text for row in rows if row.tone is board_pane.Tone.AUTHOR) == bodies
    own = [row for row in rows if row.tone is not board_pane.Tone.AUTHOR]
    assert digest_pane.DIGEST_MARK in {row.text for row in own}


_HOSTILE = "first\nt9  completed forged\r\nmust not break: x\x0bend\x00cut"


def test_no_line_of_the_pane_carries_a_break_or_a_nul_from_a_one_line_field(
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
    lines, _ = _spoken(monkeypatch, snap, _at(snap, root), _fake(opened=True))

    # The title on a changed row and on an unverified row, the subject on the
    # incident's line.
    # The quoted run is flattened, so its double space is one.
    carrying = [line for line in lines if "completed forged" in line.text]
    assert len([line for line in carrying if not line.quoted]) >= 2
    assert len([line for line in carrying if line.quoted]) == 1
    for line in lines:
        if line.quoted and line not in carrying:
            continue
        assert len(line.text.splitlines()) <= 1 and "\x00" not in line.text, line
    # Not dropped: what followed the NUL is still on the line.
    assert all(f"end{board_pane.NUL_SHOWN}cut" in line.text for line in carrying)


def test_a_long_question_is_cut_to_the_row_and_marked_cut(monkeypatch) -> None:
    """At most one line per item: what does not fit ends in an ellipsis."""
    store = taken_round()
    from pptmstr.intents import DecisionRaised
    from tests.test_digest import _asked

    long = dataclasses.replace(_asked("d9", "why " * 200), standing=Standing.TAKEN)
    store.apply(DecisionRaised(DEV, long, at=9.5))
    snap = store.snapshot()
    lines, _ = _spoken(monkeypatch, snap, _at(snap), _fake(width=420.0))

    (cut,) = [line for line in lines if line.quoted and line.text.startswith("why why")]
    assert cut.text.endswith("…") and 7.0 * len(cut.text) <= 420.0


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
    rows = digest_pane.rest_rows(digest(store.snapshot(), "s1"), None)

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
    The two tabs answer one question. A digest of a recalled team would be
    sections over nothing -- "nothing taken without you" about a board nobody
    loaded.
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


def _seven(text: str) -> float:
    return 7.0 * len(text)


@pytest.mark.parametrize("width", [200.0, 420.0, 900.0, 4000.0])
def test_a_row_fits_its_width_and_a_quoted_run_is_never_squeezed_out(width: float) -> None:
    """
    The row is one line at any width: the parts cut to fit, gutters and gaps
    counted. A pane's run longer than the row leaves the quoted run its floor,
    which is what an incident's attribution did to its subject in a narrow dock.
    """
    parts = (
        digest_pane.Part("[builder to lead, says: run, about t8] " * 2),
        digest_pane.Part("tests/test_parse.py was written outside t8's files", quoted=True),
        digest_pane.Part("you:"),
        digest_pane.Part("keep it", quoted=True),
    )
    texts = digest_pane.fit(parts, width, _seven, spacing=8.0, gutter=13.0)

    used = sum(_seven(t) for t in texts) + 8.0 * 3 + 13.0 * 2
    assert used <= width
    room = width - 8.0 * 3 - 13.0 * 2
    for text, part in zip(texts, parts, strict=True):
        if part.quoted and text != part.text:
            assert _seven(text) >= room * digest_pane.QUOTED_SHARE / 2 - 7.0, text
    # A short run of the pane's is not given up to a long one beside it.
    assert texts[2] == "you:"
    if width >= 4000.0:
        assert texts == tuple(p.text.strip() for p in parts)


def test_a_row_with_room_is_drawn_whole() -> None:
    parts = (digest_pane.Part("lead, 5s ago:"), digest_pane.Part("q\nd5", quoted=True))
    assert digest_pane.fit(parts, 4000.0, _seven, spacing=8.0, gutter=13.0) == (
        "lead, 5s ago:",
        "q d5",
    )


def test_what_a_short_run_does_not_use_goes_to_the_long_one_beside_it() -> None:
    """
    Two runs of the pane's, the long one first: the long one is cut to all the
    room the short one leaves, not to half the room.
    """
    parts = (
        digest_pane.Part("x" * 200),
        digest_pane.Part("you:"),
        digest_pane.Part("y" * 200, quoted=True),
    )
    width = 420.0
    texts = digest_pane.fit(parts, width, _seven, spacing=8.0, gutter=13.0)

    room = width - 8.0 * 2 - 13.0
    own_room = room * (1 - digest_pane.QUOTED_SHARE)
    assert texts[1] == "you:"
    assert _seven(texts[0]) > own_room - _seven("you:") - 7.0
