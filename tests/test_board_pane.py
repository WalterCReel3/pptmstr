"""
BOARD: the pane, its bounds, and the labels it derives.

Most of this moved out of ``test_detail.py`` with the pane itself. What is new is
everything the row gained when it stopped sharing a surface -- the specification,
the file list, and the reason a stalled row is not moving -- plus the summary line,
which exists so a sixty-row board can be read without being read.

Drawing is exercised with ``imgui`` replaced by a ``MagicMock``: it cannot say what
anything looked like, but it can say what was drawn, in what order, and whether a
bound announced itself. The parts that need pixels are noted where they arise.

The typeset path is at the end and runs in real ImGui frames on the rig in
``test_typeset``. Every test above it runs with the seam off, whatever an earlier
test left loaded.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock

import imscribe
import pytest
from imgui_bundle import icons_fontawesome_6 as fa

from pptmstr.board import BoardConcern, BoardTask, board_concerns, board_tasks
from pptmstr.bridge import Bridge
from pptmstr.model import (
    AgentRecord,
    AgentState,
    Basis,
    Concern,
    ConcernState,
    NodeId,
    Snapshot,
    Task,
    TaskState,
)
from pptmstr.theme import P
from pptmstr.ui import board_pane, typeset, widgets
from pptmstr.ui.focus import FocusState, Scope
from pptmstr.ui.typeset import Kind, Part
from tests import test_typeset_views as rig


@pytest.fixture(autouse=True)
def old_renderer(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The seam off for every test that did not ask for it.

    The faces stay loaded from the first typeset test to the end of the module, and
    a mocked ``imgui`` has no frame for the seam to draw in.
    """
    if "seam" not in request.fixturenames:
        monkeypatch.setattr(typeset, "enabled", lambda pane=None: False)


ROOT: NodeId = ("s1", None)
QA: NodeId = ("s1", "agent-qa")


def record(node: NodeId = ROOT, *, template: str | None = "feature") -> AgentRecord:
    return AgentRecord(
        node_id=node,
        parent=None if node[1] is None else (node[0], None),
        depth=0 if node[1] is None else 1,
        state=AgentState.AWAITING_APPROVAL,
        topic="working",
        task="the operator's whole prompt",
        model="claude-sonnet-5",
        cwd="/x/orbital",
        template=template,
    )


def board_task(
    tid: str = "t1",
    *,
    title: str = "do t1",
    detail: str = "",
    state: TaskState = TaskState.PENDING,
    owner: str | None = None,
    owner_gone: bool = False,
    blocked_on: tuple[str, ...] = (),
    missing: tuple[str, ...] = (),
    touches: tuple[str, ...] = (),
    concerns: tuple[str, ...] = (),
    result: str = "",
) -> BoardTask:
    return BoardTask(
        id=tid,
        title=title,
        detail=detail,
        state=state,
        owner=owner,
        owner_gone=owner_gone,
        blocked_on=blocked_on,
        missing=missing,
        touches=touches,
        concerns=concerns,
        result=result,
    )


def board_concern(
    cid: str = "c1",
    *,
    sender: str = "reviewer",
    recipient: str = "lead",
    subject: str = "the retry loop never terminates",
    body: str = "third call onward, the backoff never resets",
    state: ConcernState = ConcernState.POSTED,
    edited: bool = False,
    task_id: str | None = None,
    task_missing: bool = False,
) -> BoardConcern:
    return BoardConcern(
        id=cid,
        sender=sender,
        recipient=recipient,
        subject=subject,
        body=body,
        state=state,
        edited=edited,
        task_id=task_id,
        task_missing=task_missing,
    )


def board_snapshot(
    *tasks: Task, concerns: tuple[Concern, ...] = (), template: str | None = "feature"
) -> Snapshot:
    """A session launched under a team template, unless told otherwise."""
    root = dataclasses.replace(record(), template=template)
    return Snapshot(
        seq=1,
        nodes=MappingProxyType({ROOT: root}),
        order=(ROOT,),
        needs_you=(),
        any_active=False,
        tasks=MappingProxyType({t.id: t for t in tasks}),
        concerns=MappingProxyType({c.id: c for c in concerns}),
    )


def _fake_imgui(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:
    """
    ``fake`` for Dear ImGui in the pane, and its ``small_button`` for the wrapper the
    pane draws its small buttons with, so a press and a label are the fake's.
    """
    monkeypatch.setattr(board_pane, "imgui", fake)
    monkeypatch.setattr(widgets, "small_button", fake.small_button)
    monkeypatch.setattr(
        widgets,
        "small_button_width",
        lambda text: float(fake.calc_text_size(text).x) + 2 * widgets.SMALL_BUTTON_PADDING[0],
    )


def at_root(snap: Snapshot) -> FocusState:
    focus = FocusState()
    focus.to_node(snap, ROOT, scope=Scope.SESSION)
    return focus


def drawn(monkeypatch, snap: Snapshot, focus: FocusState | None = None) -> list[str]:
    """Every string the pane passed to imgui, so a bound's announcement is checkable."""
    fake = MagicMock()
    # Every task row opens: what a row draws is asserted here.
    fake.tree_node_ex.return_value = True
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    board_pane.draw(
        snap, at_root(snap) if focus is None else focus, board_pane.BoardState(), Bridge()
    )
    coloured = [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]
    disabled = [str(a) for call in fake.text_disabled.call_args_list for a in call.args]
    nodes = [str(call.args[0]) for call in fake.tree_node_ex.call_args_list if call.args]
    return coloured + disabled + nodes


def headings(monkeypatch, snap: Snapshot) -> list[str]:
    """The section headings the pane emitted."""
    seen: list[str] = []
    _fake_imgui(monkeypatch, MagicMock())
    monkeypatch.setattr(board_pane, "section", lambda label: seen.append(label))
    board_pane.draw(snap, at_root(snap), board_pane.BoardState(), Bridge())
    return seen


# -- who holds a task -------------------------------------------------------------


def test_an_unclaimed_task_names_no_owner() -> None:
    assert board_pane.owner_label(board_task()) == ""


def test_a_claimed_task_names_its_role() -> None:
    assert board_pane.owner_label(board_task(owner="builder")) == "[builder]"


def test_an_owner_that_stopped_is_not_shown_as_an_ordinary_owner() -> None:
    """
    The store has no arm that releases work when its worker dies, so the row reads
    CLAIMED forever. "[builder]" would report progress that has stopped.
    """
    row = board_task(owner="builder", owner_gone=True, state=TaskState.CLAIMED)
    assert board_pane.owner_label(row) == "[builder, stopped]"


# -- what a task waits on ---------------------------------------------------------


def test_a_task_blocked_on_nothing_says_nothing() -> None:
    assert board_pane.blocked_label(board_task()) == ""


def test_a_blocked_task_names_what_it_waits_on() -> None:
    assert board_pane.blocked_label(board_task(blocked_on=("t1", "t2"))) == "blocked on t1, t2"


def test_a_dependency_that_was_never_declared_is_marked_not_just_listed() -> None:
    """
    "blocked on t9" is something to wait out; "blocked on t9 (never declared)" is
    something to go and fix, and nothing else in the session says which it is.
    """
    row = board_task(blocked_on=("t1", "t9"), missing=("t9",))
    assert board_pane.blocked_label(row) == "blocked on t1, t9 (never declared)"


def test_the_blocked_cell_is_bounded_and_says_so() -> None:
    deps = tuple(f"t{i}" for i in range(10))
    label = board_pane.blocked_label(board_task(blocked_on=deps), limit=3)
    assert label == "blocked on t0, t1, t2, and 7 more"


def test_the_blocked_cell_at_exactly_the_limit_announces_nothing() -> None:
    label = board_pane.blocked_label(board_task(blocked_on=("t0", "t1", "t2")), limit=3)
    assert "more" not in label


# -- the files a task claimed -----------------------------------------------------


def test_a_task_that_named_no_files_says_nothing() -> None:
    assert board_pane.touches_label(board_task()) == ""


def test_a_task_names_the_files_it_will_write() -> None:
    row = board_task(touches=("pptmstr/store.py", "pptmstr/model.py"))
    assert board_pane.touches_label(row) == "writes pptmstr/store.py, pptmstr/model.py"


def test_the_file_list_is_bounded_and_says_so() -> None:
    """
    Long lists are real: a terminal gate task depends on every task that touches a
    file, and this is the cell that would otherwise wrap into a paragraph.
    """
    files = tuple(f"f{i}.py" for i in range(10))
    label = board_pane.touches_label(board_task(touches=files), limit=3)
    assert label == "writes f0.py, f1.py, f2.py, and 7 more"


# -- the reason a row is not moving -----------------------------------------------


def test_a_row_with_no_concern_has_no_reason() -> None:
    assert board_pane.row_reasons(board_task(), {}) == ()


def test_a_rows_reason_carries_the_finding_not_just_its_label() -> None:
    """
    The row hands back the concern rather than its subject, because a subject is a
    label and the reader needs the finding -- the same text `read_board` gives an
    agent, so operator and team are not reading two different boards.
    """
    row = board_task(concerns=("c1",))
    by_id = {
        "c1": board_concern(
            "c1", subject="waiting on the schema decision", body="holding until the store lands"
        )
    }

    (found,) = board_pane.row_reasons(row, by_id)

    assert (found.subject, found.body) == (
        "waiting on the schema decision",
        "holding until the store lands",
    )


def test_a_reason_whose_concern_is_not_in_the_log_is_skipped() -> None:
    """
    Both projections are built from one snapshot in one frame, so a miss is not a
    state either can reach. Skipped rather than rendered as a blank line anyway.
    """
    assert board_pane.row_reasons(board_task(concerns=("c1",)), {}) == ()


# -- the summary line -------------------------------------------------------------


def test_an_empty_board_counts_to_nothing() -> None:
    counts = board_pane.counts(())
    assert (counts.total, counts.claimed, counts.stranded) == (0, 0, 0)


def test_the_three_states_sum_to_the_total() -> None:
    """
    The property that makes the line readable. `stranded` and `undeclared` overlap
    the state buckets deliberately -- a stranded row is also a claimed one -- so
    they must not be subtracted out of them.
    """
    rows = (
        board_task("t1", state=TaskState.PENDING),
        board_task("t2", state=TaskState.CLAIMED, owner="builder"),
        board_task("t3", state=TaskState.CLAIMED, owner="builder", owner_gone=True),
        board_task("t4", state=TaskState.COMPLETED, owner="builder"),
    )
    c = board_pane.counts(rows)
    assert c.pending + c.claimed + c.completed == c.total == 4
    assert (c.claimed, c.stranded) == (2, 1)


def test_a_state_with_nothing_in_it_is_left_out_of_the_summary() -> None:
    """
    A fixed set of fields makes the reader parse three numbers to learn there is
    nothing to see.
    """
    label = board_pane.summary_label(board_pane.counts((board_task("t1"),)))
    assert label == "1 task(s) · 1 pending"
    assert "claimed" not in label and "done" not in label


def test_a_healthy_board_raises_no_alarm() -> None:
    rows = (board_task("t1"), board_task("t2", state=TaskState.COMPLETED))
    assert board_pane.alarm_label(board_pane.counts(rows)) == ""


def test_a_stranded_row_is_counted_where_the_warning_colour_can_reach_it() -> None:
    rows = (board_task("t1", owner="builder", owner_gone=True, state=TaskState.CLAIMED),)
    assert "1 stranded" in board_pane.alarm_label(board_pane.counts(rows))


def test_a_dependency_that_was_never_declared_reaches_the_summary() -> None:
    """
    Unclaimable forever, and nothing clears it. A board can look busy while every
    row on it is waiting on a task that does not exist.
    """
    rows = (board_task("t1", blocked_on=("t9",), missing=("t9",)),)
    assert "1 blocked on a task nobody declared" in board_pane.alarm_label(board_pane.counts(rows))


def test_the_summary_stays_short_enough_to_be_a_summary() -> None:
    """
    It sits above the scroll in a pane both layouts dock narrow -- 0.32 of the width
    in TRIAGE, 0.26 in FOCUS, which is roughly seventy monospace characters. The two
    lines wrap independently, so the budget is per line rather than for the pair. The
    row carries the full sentence about an undeclared dependency; a summary that
    wraps to three lines has stopped being one.
    """
    rows = (
        board_task("t1", owner="builder", owner_gone=True, state=TaskState.CLAIMED),
        board_task("t2", blocked_on=("t9",), missing=("t9",)),
    )
    c = board_pane.counts(rows)
    assert len(board_pane.summary_label(c)) < 64
    assert len(board_pane.alarm_label(c)) < 64


def test_one_reason_is_not_pluralised() -> None:
    assert board_pane.reasons_marker(("a",)) == "1 concern"
    assert board_pane.reasons_marker(("a", "b")) == "2 concerns"


# -- the concern log --------------------------------------------------------------


def test_a_waiting_concern_reads_as_waiting() -> None:
    label = board_pane.concern_label(board_concern())
    assert label == "reviewer -> lead  (waiting; basis unstated)"


def test_a_delivered_concern_says_it_was_delivered() -> None:
    label = board_pane.concern_label(board_concern(state=ConcernState.DELIVERED))
    assert label == "reviewer -> lead  (delivered; basis unstated)"


def test_a_concern_the_operator_rewrote_says_so() -> None:
    """
    What the recipient was told differs from what the sender wrote, and nothing
    else in the UI records that once the approval is gone.
    """
    label = board_pane.concern_label(board_concern(state=ConcernState.DELIVERED, edited=True))
    assert label == "reviewer -> lead  (delivered, edited by you; basis unstated)"


def test_waiting_mail_to_a_party_that_is_not_running_says_so_in_place_of_waiting() -> None:
    from pptmstr.board import UNDELIVERABLE

    stuck = dataclasses.replace(board_concern(), undeliverable=True)

    assert board_pane.concern_label(stuck) == (
        f"reviewer -> lead  ({UNDELIVERABLE}; basis unstated)"
    )
    assert "waiting" not in board_pane.concern_label(stuck)


def _concern_colours(monkeypatch, *, undeliverable: bool) -> dict[str, object]:
    """The colour each line of the concern log was drawn in."""
    fake = MagicMock()
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    row = dataclasses.replace(board_concern(), undeliverable=undeliverable)
    board_pane._concerns((row,))
    return {str(call.args[1]): call.args[0] for call in fake.text_colored.call_args_list}


def test_an_undeliverable_concern_is_drawn_in_the_warning_colour_and_a_waiting_one_is_not(
    monkeypatch,
) -> None:
    from pptmstr.board import UNDELIVERABLE

    stuck = _concern_colours(monkeypatch, undeliverable=True)
    waiting = _concern_colours(monkeypatch, undeliverable=False)

    assert stuck[f"reviewer -> lead  ({UNDELIVERABLE}; basis unstated)"] == P.warn.vec4
    assert waiting["reviewer -> lead  (waiting; basis unstated)"] == P.text_dim.vec4
    assert P.warn.vec4 != P.text_dim.vec4


def test_a_concern_the_board_marks_undeliverable_reaches_the_pane_marked(monkeypatch) -> None:
    """
    Through ``board_concerns`` and ``draw``, so the mark is the board's own and
    not one this file set: mail posted to a party a restored log named and
    nothing in this process answers to.
    """
    from pptmstr.board import UNDELIVERABLE, board_concerns
    from pptmstr.model import Restore

    gone: NodeId = ("s1", "agent-old")
    note = Concern(id="c1", sender=ROOT, recipient=gone, subject="for you", body="b", posted_at=1.0)
    snap = dataclasses.replace(
        board_snapshot(concerns=(note,)),
        restores=MappingProxyType(
            {"s1": Restore("s1", at=1.0, parties=MappingProxyType({gone: "builder-2"}))}
        ),
    )
    (row,) = board_concerns(snap, "s1")
    assert row.undeliverable

    lines = drawn(monkeypatch, snap)

    assert any(UNDELIVERABLE in line for line in lines)
    assert not any("(waiting" in line for line in lines)


def test_every_concern_state_has_a_label() -> None:
    for state in ConcernState:
        assert board_pane.concern_label(board_concern(state=state))


def test_a_concern_about_nothing_names_no_task() -> None:
    assert board_pane.about_label(board_concern()) == ""


def test_a_concern_names_the_task_it_is_about() -> None:
    assert board_pane.about_label(board_concern(task_id="t1")) == "about t1"


def test_a_concern_about_a_task_that_does_not_exist_says_so() -> None:
    """
    The store accepts the message and does not validate the id, so this is the only
    place the typo becomes visible.
    """
    label = board_pane.about_label(board_concern(task_id="t9", task_missing=True))
    assert label == "about t9 (never declared)"


# -- bounds -----------------------------------------------------------------------


def test_rows_within_the_bound_are_untouched() -> None:
    rows = (1, 2, 3)
    assert board_pane.bound_rows(rows, 10) == (rows, 0)
    assert board_pane.bound_rows(rows, 3) == (rows, 0)


def test_tasks_keep_the_head_because_dependencies_point_backwards() -> None:
    kept, dropped = board_pane.bound_rows(tuple(range(10)), 3)
    assert (kept, dropped) == ((0, 1, 2), 7)


def test_concerns_keep_the_tail_because_a_conversation_is_watched_at_its_end() -> None:
    kept, dropped = board_pane.bound_rows(tuple(range(10)), 3, tail=True)
    assert (kept, dropped) == ((7, 8, 9), 7)


def test_a_clipped_specification_reports_what_it_dropped() -> None:
    assert board_pane.clip("abcdef", 4) == ("abcd", 2)
    assert board_pane.clip("abc", 4) == ("abc", 0)


def test_the_board_bounds_are_real_numbers_not_placeholders() -> None:
    assert board_pane.bound_rows(tuple(range(300)), board_pane._MAX_BOARD_TASKS)[1] == 300 - 120
    assert board_pane.bound_rows(tuple(range(300)), board_pane._MAX_BOARD_CONCERNS)[1] == 300 - 80


# -- absence, and what the pane says instead --------------------------------------


def test_a_solo_session_says_it_is_not_a_team(monkeypatch) -> None:
    """
    Absent, not empty. Most sessions are solo and would otherwise carry a
    permanently empty table.
    """
    lines = drawn(monkeypatch, board_snapshot(template="solo"))
    assert any("not a team" in line for line in lines)
    assert headings(monkeypatch, board_snapshot(template="solo")) == []


def test_a_session_with_no_template_at_all_is_not_a_team(monkeypatch) -> None:
    """Records predating the field, and the fake driver's sub-agents."""
    assert any("not a team" in line for line in drawn(monkeypatch, board_snapshot(template=None)))


def test_a_cursor_on_nothing_says_so_rather_than_going_blank(monkeypatch) -> None:
    """An empty snapshot has no session to scope a board to, and must not invent one."""
    fake = MagicMock()
    _fake_imgui(monkeypatch, fake)
    board_pane.draw(Snapshot.empty(), FocusState(), board_pane.BoardState(), Bridge())
    assert any("no session" in str(c.args[0]) for c in fake.text_disabled.call_args_list)


def test_an_empty_team_board_says_so_rather_than_showing_nothing(monkeypatch) -> None:
    """
    A lead that has not declared a task yet and a lead that never will are different
    states, and only one of them is worth waiting on.
    """
    assert any("no tasks declared yet" in line for line in drawn(monkeypatch, board_snapshot()))


def test_another_sessions_tasks_are_not_listed(monkeypatch) -> None:
    """The scoping decision, at the level the operator sees it."""
    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=("s2", None)))
    lines = drawn(monkeypatch, snap)

    assert not any("t1" in line for line in lines)
    assert any("no tasks declared yet" in line for line in lines)


def test_a_team_with_concerns_gets_a_concern_heading(monkeypatch) -> None:
    posted = Concern(
        id="c1",
        sender=QA,
        recipient=ROOT,
        subject="the retry loop never terminates",
        body="...",
        posted_at=1.0,
    )
    assert headings(monkeypatch, board_snapshot(concerns=(posted,))) == ["concerns"]


def test_a_team_with_no_concerns_grows_no_concern_heading(monkeypatch) -> None:
    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=ROOT))
    assert headings(monkeypatch, snap) == []


# -- what reaches the screen ------------------------------------------------------


def test_a_declared_task_reaches_the_pane(monkeypatch) -> None:
    snap = board_snapshot(Task(id="t1", title="wire the reducer", declared_by=ROOT))
    lines = drawn(monkeypatch, snap)

    assert any("wire the reducer" in line for line in lines)
    assert any("t1" in line for line in lines)


def test_the_summary_reaches_the_pane_before_the_rows(monkeypatch) -> None:
    """
    Outside the scrolling child, so it does not scroll away from a long board --
    the line exists to be read without reading the table under it.
    """
    order: list[str] = []
    fake = MagicMock()
    fake.begin_child.side_effect = lambda *_a, **_k: order.append("child") or True
    fake.text_colored.side_effect = lambda *a, **_k: order.append(str(a[1]))
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())

    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=ROOT))
    board_pane.draw(snap, at_root(snap), board_pane.BoardState(), Bridge())

    assert order[0].startswith("1 task(s)")
    assert "child" in order[1:]


def test_a_bounded_board_says_how_many_tasks_it_did_not_show(monkeypatch) -> None:
    """
    The rule the rest of this pane is built on: bounded by count, and says so when
    it bites. A board of 300 tasks must not silently show 120.
    """
    tasks = [
        Task(id=f"t{i}", title=f"do t{i}", declared_at=float(i), declared_by=ROOT)
        for i in range(300)
    ]
    lines = drawn(monkeypatch, board_snapshot(*tasks))

    dropped = 300 - board_pane._MAX_BOARD_TASKS
    assert any(f"{dropped} more task(s) not shown" in line for line in lines)


def test_a_board_within_its_bound_announces_nothing(monkeypatch) -> None:
    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=ROOT))
    assert not any("not shown" in line for line in drawn(monkeypatch, snap))


def test_a_bounded_concern_log_keeps_the_newest_not_the_oldest(monkeypatch) -> None:
    """
    A conversation is watched at its end. Head-anchoring here would drop exactly
    the messages still worth acting on and keep the ones already dealt with.
    """
    concerns = tuple(
        Concern(
            id=f"c{i}",
            sender=QA,
            recipient=ROOT,
            subject=f"subject {i}",
            body="...",
            posted_at=float(i),
        )
        for i in range(100)
    )
    lines = drawn(monkeypatch, board_snapshot(concerns=concerns))

    assert any("subject 99" in line for line in lines)
    assert not any("subject 0 " in line or line == "subject 0" for line in lines)
    assert any("earlier concern(s) not shown" in line for line in lines)


_LEAF = 2


def _tree_flags(monkeypatch, snap: Snapshot) -> int:
    """
    The flags the one task row was drawn with.

    The flag constants are given real, distinct bits here: a bare ``MagicMock``
    returns the same truthy object for every attribute, so ``int()`` collapses them
    all to 1 and a bit test on them cannot fail.
    """
    fake = MagicMock()
    fake.TreeNodeFlags_.span_avail_width = 1
    fake.TreeNodeFlags_.leaf = _LEAF
    fake.TreeNodeFlags_.no_tree_push_on_open = 4
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    board_pane.draw(snap, at_root(snap), board_pane.BoardState(), Bridge())

    (call,) = fake.tree_node_ex.call_args_list
    return int(call.args[1])


def test_a_row_with_nothing_to_show_still_opens(monkeypatch) -> None:
    """
    It was a leaf until the body gained the amend control. A task with no
    specification is the one most likely to need one written, so a leaf flag here
    would withhold the affordance from exactly the rows that need it -- which a
    screenshot caught and the earlier version of this test asserted as correct.

    Checked through the flags because the arrow itself is pixels.
    """
    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=ROOT))
    assert not _tree_flags(monkeypatch, snap) & _LEAF


def test_a_row_with_a_specification_offers_to_open(monkeypatch) -> None:
    snap = board_snapshot(
        Task(id="t1", title="do t1", detail="the whole specification", declared_by=ROOT)
    )
    assert not _tree_flags(monkeypatch, snap) & _LEAF


def test_a_row_with_only_a_file_list_offers_to_open(monkeypatch) -> None:
    """
    Three things can be disclosed and any one of them is enough. Testing only
    `detail` would pass with the other two dropped from the condition.
    """
    snap = board_snapshot(
        Task(id="t1", title="do t1", touches=("pptmstr/store.py",), declared_by=ROOT)
    )
    assert not _tree_flags(monkeypatch, snap) & _LEAF


# -- the operator amends a spec (row 6) -------------------------------------------


def _amend_run(monkeypatch, snap: Snapshot, pane: board_pane.BoardState, clicks: set[str]):
    """
    Draw with every button named in `clicks` reporting pressed, and hand back the
    intents the pane emitted.
    """
    emitted: list[object] = []

    class _Bridge:
        def emit(self, intent: object) -> None:
            emitted.append(intent)

    fake = MagicMock()
    fake.small_button.side_effect = lambda label, *a, **k: label in clicks
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    # Each box hands back the text it was drawn with: nothing was typed this frame.
    monkeypatch.setattr(board_pane, "multiline_input", lambda label, value, *a, **k: (False, value))
    board_pane.draw(snap, at_root(snap), pane, _Bridge())  # type: ignore[arg-type]
    return emitted


def _spec_snapshot() -> Snapshot:
    return board_snapshot(
        Task(id="t1", title="do t1", detail="put the art in DETAIL", declared_by=ROOT)
    )


def test_opening_the_editor_seeds_the_draft_from_the_current_spec(monkeypatch) -> None:
    """
    An amendment is almost always an edit of what is there, not a replacement typed
    from nothing. Starting empty would make every correction a retype.
    """
    pane = board_pane.BoardState()
    _amend_run(monkeypatch, _spec_snapshot(), pane, {"amend##t1"})

    assert pane.editing == "t1"
    assert pane.drafts["t1"] == "put the art in DETAIL"


def test_amending_emits_the_operators_intent(monkeypatch) -> None:
    pane = board_pane.BoardState(editing="t1", drafts={"t1": "put the art in NEEDS YOU"})
    emitted = _amend_run(monkeypatch, _spec_snapshot(), pane, {"amend##apply-t1"})

    assert emitted == [
        board_pane.TaskAmended(task_id="t1", detail="put the art in NEEDS YOU", node_id=None)
    ]
    # node_id None is what marks it the operator's, and the store reads that as the
    # authority to rewrite a spec an agent is bound by.
    assert emitted[0].node_id is None  # type: ignore[attr-defined]


def test_amending_closes_the_editor_and_drops_the_draft(monkeypatch) -> None:
    pane = board_pane.BoardState(editing="t1", drafts={"t1": "new"})
    _amend_run(monkeypatch, _spec_snapshot(), pane, {"amend##apply-t1"})

    assert pane.editing is None
    assert "t1" not in pane.drafts


def test_cancelling_emits_nothing_and_keeps_no_draft(monkeypatch) -> None:
    """
    A half-written correction that survives is one an operator can send later
    believing they finished it.
    """
    pane = board_pane.BoardState(editing="t1", drafts={"t1": "half a thought"})
    emitted = _amend_run(monkeypatch, _spec_snapshot(), pane, {"cancel##t1"})

    assert emitted == []
    assert pane.editing is None
    assert "t1" not in pane.drafts


def test_drawing_without_touching_a_button_amends_nothing(monkeypatch) -> None:
    """The editor is open; typing into it must not apply on every frame."""
    pane = board_pane.BoardState(editing="t1", drafts={"t1": "mid-sentence"})
    emitted = _amend_run(monkeypatch, _spec_snapshot(), pane, set())

    assert emitted == []
    assert pane.editing == "t1"


def test_a_draft_for_a_task_that_left_the_board_is_dropped(monkeypatch) -> None:
    """Otherwise drafts accumulate for the life of the process."""
    pane = board_pane.BoardState(editing="gone", drafts={"gone": "x"})
    _amend_run(monkeypatch, _spec_snapshot(), pane, set())

    assert pane.drafts == {}
    assert pane.editing is None


def test_a_task_with_no_spec_can_still_be_given_one(monkeypatch) -> None:
    """
    The defect the screenshot found: `t4` and `t5` in the fake fixture had no
    detail, no files and no concerns, so they were leaves with no amend control --
    and a task with no specification is precisely the one an operator needs to
    write one for.
    """
    pane = board_pane.BoardState()
    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=ROOT))
    _amend_run(monkeypatch, snap, pane, {"amend##t1"})

    assert pane.editing == "t1"
    assert pane.drafts["t1"] == ""


def test_the_operator_reads_the_finding_the_agents_read(monkeypatch) -> None:
    """
    The pane and `read_board` share one projection; rendering only the subject here
    while the tool renders the body would put the operator and the team back on two
    different boards -- the failure row 2 was organised around.
    """
    posted = Concern(
        id="c1",
        sender=QA,
        recipient=ROOT,
        subject="checksum ignored in sixty places",
        body="tle/parse.py never validates before use",
        posted_at=1.0,
        task_id="t1",
    )
    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=ROOT), concerns=(posted,))

    fake = MagicMock()
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    monkeypatch.setattr(board_pane, "multiline_input", lambda *a, **k: (False, ""))
    board_pane.draw(snap, at_root(snap), board_pane.BoardState(), Bridge())
    lines = [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]

    assert any("checksum ignored in sixty places" in line for line in lines)
    assert any("tle/parse.py never validates before use" in line for line in lines)


# -- how a body is read ------------------------------------------------------------


def test_a_body_is_read_by_the_seams_rule_and_no_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The wiring. Whether a body is markdown is ``typeset.prose_parts``' to say, so that
    a concern reads the same here, in DETAIL and in the inbox; this pane hands it the
    text it kept and adds nothing but the note of what it dropped.
    """
    seen: list[str] = []
    answer = (Part("as the seam read it", Kind.LITERAL),)
    monkeypatch.setattr(typeset, "prose_parts", lambda text: seen.append(text) or answer)

    assert board_pane.clipped_parts("first line\nsecond line", 40) is answer
    assert seen == ["first line\nsecond line"]
    clipped = board_pane.clipped_parts("first line\nsecond line", 12)
    assert seen[1:] == ["first line"]
    assert clipped[:-1] == answer and clipped[-1].colour == "text_dim"


def test_one_fact_a_line_and_a_pasted_traceback_keep_their_lines() -> None:
    """
    The fixture concern ``fake-c6``, which is not markdown. As markdown its four
    facts are one paragraph and its traceback loses the indentation that pairs a
    frame with its line.
    """
    body = rig._fleet_bodies()["fake-c6"]
    facts, trace = body.split("\n\n")
    assert len(body) < board_pane._MAX_CONCERN_CHARS

    first, second = board_pane.clipped_parts(body, board_pane._MAX_CONCERN_CHARS)

    assert first.kind is Kind.MARKDOWN
    # Each fact ends in markdown's hard break, bar the last.
    assert [line.endswith("  ") for line in first.text.split("\n")] == [True, True, True, False]
    assert second == Part(trace, Kind.LITERAL)
    assert facts.startswith("ran: pytest") and '\n  File "tests/test_parse.py"' in trace


def test_a_markdown_body_is_one_part_and_is_not_rewritten() -> None:
    body = (
        "`tle/parse.py` computes the checksum and never compares it.\n\n"
        "1. inside the parser, or\n2. beside it.\n\n"
        "```python\nwhile True:\n\n    retry()\n```\n"
    )
    (part,) = board_pane.clipped_parts(body, board_pane._MAX_CONCERN_CHARS)
    assert part.text is body
    assert part.kind is Kind.MARKDOWN


def test_an_empty_body_is_no_parts() -> None:
    assert board_pane.clipped_parts(" \n\n", 40) == ()


def test_the_cut_falls_back_to_the_end_of_a_line() -> None:
    text = "first line\nsecond `li" + "ne` continues"
    assert board_pane.clip_lines(text, 21) == ("first line", len(text) - 10)
    assert board_pane.clip_lines(text, len(text)) == (text, 0)


def test_one_long_line_is_cut_where_the_limit_falls() -> None:
    assert board_pane.clip_lines("x" * 50, 20) == ("x" * 20, 30)
    # A newline too far back would give away most of what the bound allows.
    assert board_pane.clip_lines("ab\n" + "x" * 50, 20) == ("ab\n" + "x" * 17, 33)


def test_a_clipped_body_says_so_as_its_last_part() -> None:
    parts = board_pane.clipped_parts("word " * 100, 40)
    assert parts[-1] == Part("... 460 more character(s) not shown", Kind.WRAPPED, "text_dim")
    assert len(parts[0].text) == 40
    assert board_pane.clipped_parts("short", 40) == (Part("short"),)


# -- state as a glyph ----------------------------------------------------------------


def test_each_condition_a_row_can_be_in_has_its_glyph() -> None:
    glyph = board_pane.task_glyph
    assert glyph(board_task(state=TaskState.COMPLETED, owner="b")) == (fa.ICON_FA_CHECK, "ok")
    assert glyph(board_task(state=TaskState.CLAIMED, owner="b")) == (
        fa.ICON_FA_GEARS,
        "state_running",
    )
    assert glyph(board_task()) == (fa.ICON_FA_CIRCLE, "text_dim")
    assert glyph(board_task(blocked_on=("t0",))) == (fa.ICON_FA_LOCK, "text_dim")


def test_the_two_conditions_nothing_clears_share_the_warning() -> None:
    stuck = (fa.ICON_FA_TRIANGLE_EXCLAMATION, "warn")
    stranded = board_task(state=TaskState.CLAIMED, owner="b", owner_gone=True)
    undeclared = board_task(blocked_on=("t9",), missing=("t9",))
    assert board_pane.task_glyph(stranded) == stuck
    assert board_pane.task_glyph(undeclared) == stuck


def test_a_finished_task_is_finished_whatever_became_of_its_owner() -> None:
    done = board_task(state=TaskState.COMPLETED, owner="b", owner_gone=True)
    assert board_pane.task_glyph(done)[0] == fa.ICON_FA_CHECK


def test_a_closed_concern_says_a_word_only_when_something_happened() -> None:
    mark = board_pane.concern_mark
    assert mark(board_concern(state=ConcernState.DELIVERED)) == ""
    assert mark(board_concern(state=ConcernState.POSTED)) == "waiting"
    assert mark(board_concern(state=ConcernState.WITHDRAWN)) == "withdrawn"
    assert mark(board_concern(state=ConcernState.DELIVERED, edited=True)) == "edited"
    assert mark(board_concern(state=ConcernState.POSTED, edited=True)) == "waiting, edited"


def test_an_edited_concern_is_inked_apart_from_one_delivered_as_written() -> None:
    plain = board_pane.concern_glyph(board_concern(state=ConcernState.DELIVERED))
    edited = board_pane.concern_glyph(board_concern(state=ConcernState.DELIVERED, edited=True))
    assert plain == (fa.ICON_FA_CHECK, "text_dim")
    assert edited == (fa.ICON_FA_CHECK, "accent")


# -- the typeset path, in real frames ----------------------------------------------

LONG = "a title that goes on " * 12
SPEC = "Builder task. `tle/parse.py` only.\n\n- one\n- two\n"


def concern(
    cid: str, *, subject: str = "why", body: str = "", task_id: str | None = None
) -> Concern:
    return Concern(
        id=cid,
        sender=QA,
        recipient=ROOT,
        subject=subject,
        body=body,
        posted_at=1.0,
        task_id=task_id,
    )


class Frames:
    """
    The pane drawn in real frames, with chosen rows held open and every native
    string and every row's height written down.
    """

    def __init__(self, im: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.im = im
        self.pane = board_pane.BoardState()
        self.opened: Collection[str] = ()
        self.strings: list[str] = []
        self.heights: dict[str, float] = {}

        tree_node_ex = im.tree_node_ex
        text_colored = im.text_colored
        task_row = board_pane._typeset_task_row
        concern_row = board_pane._typeset_concern_row

        def tree(label: str, *args: Any) -> bool:
            im.set_next_item_open(label.split("###")[-1] in self.opened)
            return bool(tree_node_ex(label, *args))

        def text(colour: Any, string: str) -> None:
            self.strings.append(string)
            text_colored(colour, string)

        def measured(inner: Any) -> Any:
            def row(first: Any, *args: Any) -> None:
                before = im.get_cursor_pos_y()
                inner(first, *args)
                self.heights[first.id] = im.get_cursor_pos_y() - before

            return row

        monkeypatch.setattr(im, "tree_node_ex", tree)
        monkeypatch.setattr(im, "text_colored", text)
        monkeypatch.setattr(board_pane, "_typeset_task_row", measured(task_row))
        monkeypatch.setattr(board_pane, "_typeset_concern_row", measured(concern_row))

    def draw(
        self,
        snap: Snapshot,
        *opened: str,
        focus: FocusState | None = None,
        width: float | None = None,
    ) -> list[str]:
        """
        One frame. ``width`` docks the pane that wide, as a layout does.
        """
        self.opened = opened
        self.strings.clear()
        where = at_root(snap) if focus is None else focus
        im = self.im

        def body() -> None:
            if width is None:
                board_pane.draw(snap, where, self.pane, Bridge())
                return
            im.begin_child("##docked", im.ImVec2(width, 0.0))
            board_pane.draw(snap, where, self.pane, Bridge())
            im.end_child()

        rig.frame(im, body)
        return list(self.strings)

    def shown(self, key: object) -> str:
        return "\n\n".join(self.pane.views.texts(key))

    def people(self, snap: Snapshot) -> str:
        (row,) = board_concerns(snap, "s1")
        return board_pane.participants_label(row)

    def line(self) -> float:
        im = self.im
        im.new_frame()
        height = float(im.get_text_line_height_with_spacing())
        im.render()
        return height


@pytest.fixture
def frames(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Frames:
    return Frames(seam, monkeypatch)


def test_with_the_seam_off_the_pane_makes_no_view(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    typeset.configure(typeset=False, mono=False)
    pane = board_pane.BoardState()
    snap = board_snapshot(Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT))
    seen: list[str] = []
    monkeypatch.setattr(board_pane, "_task_row", lambda row, *_: seen.append(row.id))

    rig.frame(seam, lambda: board_pane.draw(snap, at_root(snap), pane, Bridge()))

    assert seen == ["t1"]
    assert len(pane.views) == 0


def test_a_closed_board_is_rows_and_no_views(frames: Frames) -> None:
    snap = board_snapshot(
        Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT),
        concerns=(concern("c1", body="a finding", task_id="t1"),),
    )
    strings = frames.draw(snap)

    assert len(frames.pane.views) == 0
    assert "do t1" in strings
    assert "s1 -> agent-qa" not in strings  # roles, not node ids
    assert any("->" in s for s in strings)
    assert "why" in strings


def test_state_is_a_native_glyph_and_then_its_word(frames: Frames) -> None:
    """
    The glyph goes through ImGui in the UI font. In a typeset view it would be set
    in a face without the icons, as U+FFFD.
    """
    snap = board_snapshot(Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT))
    strings = frames.draw(snap, "t1")

    at = strings.index(fa.ICON_FA_CIRCLE)
    assert strings[at + 1 : at + 4] == ["pending", "t1", "do t1"]
    assert fa.ICON_FA_CIRCLE not in frames.shown(("s1", "spec", "t1"))


def test_an_opened_task_sets_its_specification_as_markdown(frames: Frames) -> None:
    snap = board_snapshot(Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT))
    strings = frames.draw(snap, "t1")

    assert frames.pane.views.keys() == (("s1", "spec", "t1"),)
    shown = frames.shown(("s1", "spec", "t1"))
    assert "Builder task. tle/parse.py only." in shown
    assert "`" not in shown
    assert not any("Builder task" in s for s in strings)
    # Four vertices a glyph: the text reached the draw list.
    (view,) = frames.pane.views.views(("s1", "spec", "t1"))
    assert view.stats.vertices > 4 * 20


def test_a_concern_is_drawn_under_its_task_and_in_the_log_as_two_views(frames: Frames) -> None:
    snap = board_snapshot(
        Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT),
        concerns=(concern("c1", subject="checksum ignored", body="the *finding*", task_id="t1"),),
    )
    strings = frames.draw(snap, "t1", "concern-c1")

    assert set(frames.pane.views.keys()) == {
        ("s1", "spec", "t1"),
        ("s1", "reason", "t1", "c1"),
        ("s1", "concern", "c1"),
    }
    assert not any("drawn twice" in s for s in strings)
    assert "reason (basis unstated): checksum ignored" in strings
    assert frames.shown(("s1", "reason", "t1", "c1")) == "the finding"
    assert frames.shown(("s1", "concern", "c1")) == "the finding"


def test_a_pasted_traceback_reaches_the_view_line_for_line(frames: Frames) -> None:
    """
    ``fake-c6`` as the pane sets it: what is on screen is what was written, a fact a
    line with its punctuation as typed and the traceback with its indentation.
    """
    body = rig._fleet_bodies()["fake-c6"]
    snap = board_snapshot(
        Task(id="t1", title="do t1", declared_by=ROOT),
        concerns=(concern("c6", body=body),),
    )
    frames.draw(snap, "concern-c6")

    assert frames.shown(("s1", "concern", "c6")) == body


def test_a_closed_row_is_one_line_however_long_its_title(frames: Frames) -> None:
    """
    The density rule. The renderer before this one wraps a long title onto as many
    lines as it takes.
    """
    snap = board_snapshot(
        Task(id="t1", title=LONG, detail=SPEC, declared_by=ROOT),
        Task(id="t2", title="short", declared_by=ROOT),
        concerns=(concern("c1", subject=LONG, body="a finding\n\nin two paragraphs"),),
    )
    strings = frames.draw(snap)

    line = frames.line()
    assert frames.heights == {"t1": line, "t2": line, "c1": line}
    assert LONG not in strings
    assert sum(1 for s in strings if s.endswith("…") and LONG.startswith(s[:-1])) == 2


def test_a_closed_row_out_of_sight_keeps_its_line_and_draws_nothing(frames: Frames) -> None:
    tasks = [Task(id=f"t{n}", title=f"title {n}", declared_by=ROOT) for n in range(100)]
    strings = frames.draw(board_snapshot(*tasks))

    assert "title 0" in strings
    assert "title 99" not in strings
    assert set(frames.heights.values()) == {frames.line()}


def test_an_opened_row_gives_back_the_title_it_cut(frames: Frames) -> None:
    snap = board_snapshot(
        Task(id="t1", title=LONG, declared_by=ROOT),
        Task(id="t2", title="short", declared_by=ROOT),
        concerns=(concern("c1", subject=LONG), concern("c2", subject="brief")),
    )
    strings = frames.draw(snap, "t1", "t2", "concern-c1", "concern-c2")

    assert strings.count(LONG) == 2
    assert strings.count("short") == 1
    assert strings.count("brief") == 1


def test_a_closed_row_keeps_its_second_line_for_what_is_scanned_for(frames: Frames) -> None:
    held = dataclasses.replace(
        Task(id="t1", title="do t1", declared_by=ROOT),
        state=TaskState.CLAIMED,
        claimed_by=QA,
    )
    snap = board_snapshot(held, Task(id="t2", title="do t2", depends_on=("t9",), declared_by=ROOT))
    strings = frames.draw(snap)

    line = frames.line()
    assert frames.heights == {"t1": 2 * line, "t2": 2 * line}
    assert any(s.startswith("[") and "stopped" in s for s in strings)
    assert "blocked on t9 (never declared)" in strings
    assert fa.ICON_FA_TRIANGLE_EXCLAMATION in strings


def test_a_concern_about_a_task_nobody_declared_says_so_while_closed(frames: Frames) -> None:
    snap = board_snapshot(
        Task(id="t1", title="do t1", declared_by=ROOT),
        concerns=(concern("c1", task_id="t12"), concern("c2", task_id="t1")),
    )
    strings = frames.draw(snap)

    assert "about t12 (never declared)" in strings
    assert "about t1" not in strings
    assert frames.heights["c1"] == 2 * frames.heights["c2"]


def test_an_opened_concern_says_where_it_got_to_and_what_it_is_about(frames: Frames) -> None:
    edited = dataclasses.replace(
        concern("c1", body="said", task_id="t1"), state=ConcernState.DELIVERED, edited=True
    )
    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=ROOT), concerns=(edited,))

    assert "edited" in frames.draw(snap)
    opened = frames.draw(snap, "concern-c1")
    said = board_pane.basis_label(edited.basis)
    assert f"{frames.people(snap)} · delivered, edited by you · {said} · about t1" in opened


def test_a_view_is_held_while_its_row_is_open_and_let_go_when_it_closes(frames: Frames) -> None:
    snap = board_snapshot(Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT))
    frames.draw(snap, "t1")
    view = frames.pane.views.view(("s1", "spec", "t1"))
    parts = frames.pane.parts[("s1", "spec", "t1")][1]

    frames.draw(snap, "t1")
    assert frames.pane.views.view(("s1", "spec", "t1")) is view
    assert frames.pane.parts[("s1", "spec", "t1")][1] is parts

    frames.draw(snap)
    assert len(frames.pane.views) == 0
    assert frames.pane.parts == {}


def test_an_amended_specification_is_what_the_view_shows_next(frames: Frames) -> None:
    before = board_snapshot(Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT))
    after = board_snapshot(Task(id="t1", title="do t1", detail="Rewritten.", declared_by=ROOT))
    frames.draw(before, "t1")
    frames.draw(after, "t1")

    assert frames.shown(("s1", "spec", "t1")) == "Rewritten."


OTHER: NodeId = ("s2", None)


def sessions(*tasks: Task, **templates: str) -> Snapshot:
    """
    ``board_snapshot`` with a root per keyword: ``s2="solo"`` beside ``s1``, a team.
    """
    snap = board_snapshot(*tasks)
    roots = {(sid, None): record((sid, None), template=name) for sid, name in templates.items()}
    nodes = {**snap.nodes, **roots}
    return dataclasses.replace(snap, nodes=MappingProxyType(nodes), order=tuple(nodes))


def at(snap: Snapshot, session_id: str) -> FocusState:
    focus = FocusState()
    focus.to_node(snap, (session_id, None), scope=Scope.SESSION)
    return focus


def test_a_cursor_that_passes_over_a_session_with_no_board_lays_nothing_out_again(
    frames: Frames, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    j and k through a queue that mixes teams with solo sessions. The frame the
    cursor comes back on shows the views it left, and builds no text.
    """
    snap = sessions(Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT), s2="solo")
    frames.draw(snap, "t1")
    view = frames.pane.views.view(("s1", "spec", "t1"))
    parts = frames.pane.parts[("s1", "spec", "t1")][1]
    read: list[str] = []
    parse = imscribe.parse_markdown
    monkeypatch.setattr(
        imscribe,
        "parse_markdown",
        lambda text, *rest, **kw: read.append(text) or parse(text, *rest, **kw),
    )
    revision = view.revision

    assert "do t1" not in frames.draw(snap, "t1", focus=at(snap, "s2"))
    assert frames.pane.views.view(("s1", "spec", "t1")) is view
    frames.draw(snap, "t1", focus=FocusState())
    assert "do t1" in frames.draw(snap, "t1")

    assert frames.pane.views.view(("s1", "spec", "t1")) is view
    assert frames.pane.parts[("s1", "spec", "t1")][1] is parts
    assert read == [] and view.revision == revision
    assert "Builder task." in frames.shown(("s1", "spec", "t1"))


def test_a_row_closed_under_the_cursor_is_let_go_and_another_teams_is_not(frames: Frames) -> None:
    snap = sessions(
        Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT),
        Task(id="t2", title="do t2", detail="Theirs.", declared_by=OTHER),
        s2="feature",
    )
    frames.draw(snap, "t1")
    frames.draw(snap, "t2", focus=at(snap, "s2"))
    assert set(frames.pane.views.keys()) == {("s1", "spec", "t1"), ("s2", "spec", "t2")}

    frames.draw(snap, focus=at(snap, "s2"))

    assert frames.pane.views.keys() == (("s1", "spec", "t1"),)
    assert set(frames.pane.parts) == {("s1", "spec", "t1")}


def test_views_are_let_go_when_their_session_stops_being_a_team(frames: Frames) -> None:
    snap = board_snapshot(Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT))
    frames.draw(snap, "t1")
    assert len(frames.pane.views) == 1

    solo = board_snapshot(template="solo")
    frames.draw(solo, "t1")
    assert len(frames.pane.views) == 0
    assert frames.pane.parts == {} and frames.pane.held == []


def test_views_are_let_go_when_their_session_leaves_the_snapshot(frames: Frames) -> None:
    """
    The rule that survives a cursor move has to end somewhere: a session that is
    gone is one nobody can come back to.
    """
    snap = sessions(Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT), s2="solo")
    frames.draw(snap, "t1")
    gone = dataclasses.replace(
        snap, nodes=MappingProxyType({OTHER: snap.nodes[OTHER]}), order=(OTHER,)
    )

    frames.draw(gone, "t1", focus=at(gone, "s2"))

    assert len(frames.pane.views) == 0
    assert frames.pane.parts == {} and frames.pane.held == []


def test_the_teams_whose_views_are_held_are_the_ones_shown_latest(frames: Frames) -> None:
    count = board_pane._HELD_BOARDS + 2
    tasks = [
        Task(id=f"t{n}", title=f"do t{n}", detail=SPEC, declared_by=(f"s{n}", None))
        for n in range(1, count + 1)
    ]
    snap = sessions(*tasks, **{f"s{n}": "feature" for n in range(2, count + 1)})
    for n in range(1, count + 1):
        frames.draw(snap, f"t{n}", focus=at(snap, f"s{n}"))
    # Back to one of the kept: it becomes the latest, and nothing is dropped for it.
    frames.draw(snap, "t3", focus=at(snap, "s3"))

    kept = [f"s{n}" for n in range(4, count + 1)] + ["s3"]
    assert frames.pane.held == kept
    assert {key[0] for key in frames.pane.views.keys()} == set(kept)
    assert set(frames.pane.parts) == set(frames.pane.views.keys())


# -- who a concern's line names --------------------------------------------------


def test_the_participants_column_gives_way_to_the_subject() -> None:
    share = board_pane._PEOPLE_SHARE
    cell = board_pane.people_cell
    assert cell(120.0, 55.0, 120.0 / share) == (board_pane._People.BOTH, 120.0)
    assert cell(120.0, 55.0, 120.0 / share - 1.0) == (board_pane._People.SENDER, 55.0)
    assert cell(120.0, 55.0, 55.0 / share - 1.0) == (board_pane._People.NOBODY, 0.0)


def test_a_narrow_pane_names_the_sender_and_gives_the_subject_the_rest(frames: Frames) -> None:
    """
    FOCUS docks the pane about this wide. The line keeps its sender and loses its
    recipient, whole and not cut to "revi…", and the subject has the room.
    """
    subject = "checksum ignored"
    snap = board_snapshot(
        Task(id="t1", title="do t1", declared_by=ROOT),
        concerns=(concern("c1", subject=subject, body="a finding"),),
    )
    both = frames.people(snap)
    sender = both.split(" -> ")[0]

    wide = frames.draw(snap)
    assert both in wide and sender not in wide

    narrow = frames.draw(snap, width=300.0)
    assert sender in narrow and both not in narrow
    assert not any(s.endswith("…") for s in narrow), narrow
    assert subject in narrow
    assert frames.heights["c1"] == frames.line()


def test_an_opened_concern_names_both_ends_however_narrow_the_pane(frames: Frames) -> None:
    snap = board_snapshot(
        Task(id="t1", title="do t1", declared_by=ROOT),
        concerns=(concern("c1", body="a finding"),),
    )
    opened = frames.draw(snap, "concern-c1", width=120.0)

    said = board_pane.basis_label(None)
    assert f"{frames.people(snap)} · waiting · {said}" in opened


def test_the_bound_on_a_specification_is_said_inside_its_view(frames: Frames) -> None:
    long = "one line of the specification\n" * 400
    snap = board_snapshot(Task(id="t1", title="do t1", detail=long, declared_by=ROOT))
    frames.draw(snap, "t1")

    kept = board_pane._MAX_DETAIL_CHARS // 30 * 30
    shown = frames.shown(("s1", "spec", "t1"))
    assert f"... {len(long) - kept + 1} more character(s) not shown" in shown
    assert shown.count("one line of the specification") == kept // 30


def test_a_long_specification_scrolls_in_its_own_room(frames: Frames) -> None:
    """
    The cap. Without it a view is as tall as its text and the rest of the board is
    that far away.
    """
    long = "\n\n".join(f"paragraph {n} of the specification" for n in range(80))
    snap = board_snapshot(Task(id="t1", title="do t1", detail=long, declared_by=ROOT))
    frames.draw(snap, "t1")

    cap = board_pane._SPEC_LINES * typeset.line_height(mono=False)
    assert frames.heights["t1"] < cap + 4 * frames.line()
    assert (frames.pane.views.height(("s1", "spec", "t1")) or 0.0) > cap


def test_the_editor_takes_the_place_of_the_typeset_specification(
    frames: Frames, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap = board_snapshot(Task(id="t1", title="do t1", detail=SPEC, declared_by=ROOT))
    edited: list[str] = []
    monkeypatch.setattr(board_pane, "_amend_editor", lambda row, *_: edited.append(row.id))
    frames.pane.editing = "t1"
    frames.draw(snap, "t1")

    assert edited == ["t1"]
    assert len(frames.pane.views) == 0


# -- what a finished task reported --------------------------------------------------


def _finished(result: str) -> Snapshot:
    return board_snapshot(
        Task(
            id="t1",
            title="do t1",
            detail="the specification",
            state=TaskState.COMPLETED,
            claimed_by=QA,
            declared_by=ROOT,
            result=result,
        )
    )


def test_a_result_is_introduced_as_its_claimers_account() -> None:
    row = board_task(owner="builder-2", result="ran it")
    assert board_pane.result_heading(row) == "result, as builder-2 reported it (basis unstated):"
    assert board_pane.result_heading(board_task(result="ran it")) == "result (basis unstated):"


def test_the_operator_reads_the_result_the_agents_read(monkeypatch) -> None:
    """
    From the record to the screen, not from a hand-built row: a result the store
    holds and the pane does not draw is one the operator -- the first person who
    could ever review it -- never sees.
    """
    lines = drawn(monkeypatch, _finished("ran 12 tests; lint NOT run"))

    assert "ran 12 tests; lint NOT run" in lines
    assert any(line.startswith("result, as ") for line in lines)
    # Ahead of the specification: a finished row is opened for what happened.
    assert lines.index("ran 12 tests; lint NOT run") < lines.index("the specification")


def test_a_task_with_no_result_draws_no_result_heading(monkeypatch) -> None:
    lines = drawn(monkeypatch, _finished(""))

    assert "the specification" in lines
    assert not any(line.startswith("result") for line in lines)


def test_a_long_result_is_bounded_in_the_pane_and_says_so(monkeypatch) -> None:
    lines = drawn(monkeypatch, _finished("r" * (board_pane._MAX_RESULT_CHARS + 7)))

    assert "r" * board_pane._MAX_RESULT_CHARS in lines
    assert "... 7 more character(s) not shown" in lines


def test_a_result_stays_on_screen_while_the_specification_is_being_amended(monkeypatch) -> None:
    """
    The editor replaces the specification and nothing else. An operator rewriting
    what was asked is usually doing it because of what was reported.
    """
    fake = MagicMock()
    fake.small_button.return_value = False
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    monkeypatch.setattr(board_pane, "multiline_input", lambda *a, **k: (False, "draft"))
    snap = _finished("what was reported")
    pane = board_pane.BoardState(editing="t1", drafts={"t1": "draft"})
    board_pane.draw(snap, at_root(snap), pane, Bridge())
    lines = [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]

    assert "what was reported" in lines
    assert "the specification" not in lines


# -- how an author says it knows ----------------------------------------------------


def test_every_text_on_a_row_is_drawn_beside_what_its_author_said_of_it(monkeypatch) -> None:
    """
    From the records to the screen, with a different member on each of the three
    texts so a heading reading the wrong field cannot pass: the declarer relayed
    the specification, the claimer ran the result, the reviewer read the finding.
    """
    from pptmstr.model import Basis

    task = dataclasses.replace(
        _finished("what was reported").tasks["t1"],
        basis=Basis.RELAYED,
        result_basis=Basis.RUN,
    )
    note = Concern(
        id="c1",
        sender=QA,
        recipient=ROOT,
        subject="the finding",
        body="its reasoning",
        posted_at=1.0,
        task_id="t1",
        basis=Basis.READ,
    )
    lines = drawn(monkeypatch, board_snapshot(task, concerns=(note,)))

    assert "specification (says: relayed):" in lines
    assert lines.index("specification (says: relayed):") + 1 == lines.index("the specification")
    assert any(line.endswith("reported it (says: run):") for line in lines)
    assert "reason (says: read): the finding" in lines
    assert any(line.endswith("(waiting; says: read)") for line in lines)


def test_an_unmarked_row_is_drawn_as_unstated_and_never_as_a_member(monkeypatch) -> None:
    note = Concern(
        id="c1",
        sender=QA,
        recipient=ROOT,
        subject="the finding",
        body="its reasoning",
        posted_at=1.0,
        task_id="t1",
    )
    lines = drawn(monkeypatch, board_snapshot(_finished("r").tasks["t1"], concerns=(note,)))

    assert "specification (basis unstated):" in lines
    assert "reason (basis unstated): the finding" in lines
    assert sum("basis unstated" in line for line in lines) == 4
    assert not any("says:" in line for line in lines)


# -- a property, what was not run, and an incident ----------------------------------

_HELD = "A row is never dropped to make the board fit."


def test_a_property_is_drawn_under_the_title_of_a_row_nobody_opened(monkeypatch) -> None:
    """
    On the closed row, whole, and first among its qualifiers. The operator ranks a
    row's notes against it, and behind the triangle it would be read only by
    someone who already knew to look.
    """
    held = "p" * 300
    snap = board_snapshot(
        Task(id="t1", title="do t1", detail="the specification", declared_by=ROOT, property=held)
    )
    fake = MagicMock()
    fake.tree_node_ex.return_value = False
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    board_pane.draw(snap, at_root(snap), board_pane.BoardState(), Bridge())
    lines = [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]

    assert lines.index("do t1") + 1 == lines.index(f"must not break: {held}")
    assert "the specification" not in lines


def test_a_row_with_no_property_draws_no_line_for_one(monkeypatch) -> None:
    lines = drawn(monkeypatch, _spec_snapshot())
    assert not any("must not break" in line for line in lines)


def _row_lines(monkeypatch, task: Task, *, opened: bool) -> list[str]:
    """What one row draws with its triangle open or, as it ships, closed."""
    snap = board_snapshot(task)
    fake = MagicMock()
    fake.tree_node_ex.return_value = opened
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    board_pane.draw(snap, at_root(snap), board_pane.BoardState(), Bridge())
    return [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]


_STANDINGS = [
    # result, its basis, not_run -> what the closed row says of each
    ("ran it", Basis.RUN, None, "result says: run", "not run: unstated"),
    ("ran it", None, "", "result basis unstated", "says nothing was left unrun"),
    ("ran it", Basis.READ, "the probe", "result says: read", "says what was not run"),
    ("", None, None, "no result reported", "not run: unstated"),
    ("", None, "the probe", "no result reported", "says what was not run"),
]


@pytest.mark.parametrize(("result", "basis", "not_run", "said", "unrun"), _STANDINGS)
def test_a_finished_row_nobody_opened_says_its_basis_and_where_it_stands_on_not_run(
    monkeypatch, result: str, basis: Basis | None, not_run: str | None, said: str, unrun: str
) -> None:
    """
    Rows ship closed, so the closed row is the one the operator reads. Silence on
    it is read as "run": nobody said otherwise. Drawn through ``draw`` with the
    triangle reporting closed, and the opened body's own lines must be absent or
    this is asserting the wrong row.
    """
    task = Task(
        id="t1",
        title="do t1",
        detail="the specification",
        state=TaskState.COMPLETED,
        claimed_by=QA,
        declared_by=ROOT,
        result=result,
        result_basis=basis,
        not_run=not_run,
    )
    lines = _row_lines(monkeypatch, task, opened=False)

    assert "the specification" not in lines
    assert not any(line.startswith("result, as ") for line in lines)
    assert f"{said} · {unrun}" in lines


def test_a_finished_row_with_nothing_else_under_its_title_still_says_where_it_stands(
    monkeypatch,
) -> None:
    """No owner, no property, no dependency, no note: the standing is the only line."""
    task = Task(id="t1", title="do t1", state=TaskState.COMPLETED, declared_by=ROOT)
    lines = _row_lines(monkeypatch, task, opened=False)

    assert lines[lines.index("do t1") + 1] == "no result reported · not run: unstated"


@pytest.mark.parametrize("state", [TaskState.PENDING, TaskState.CLAIMED])
def test_a_row_that_is_not_finished_says_nothing_about_a_result_it_does_not_have(
    monkeypatch, state: TaskState
) -> None:
    task = Task(id="t1", title="do t1", state=state, claimed_by=QA, declared_by=ROOT)
    for opened in (False, True):
        lines = _row_lines(monkeypatch, task, opened=opened)
        assert not any("not run" in line or "result" in line for line in lines)


@pytest.mark.parametrize(("result", "basis", "not_run", "said", "unrun"), _STANDINGS)
def test_an_opened_finished_row_carries_the_same_standing_in_its_headings(
    monkeypatch, result: str, basis: Basis | None, not_run: str | None, said: str, unrun: str
) -> None:
    """
    Opening a row must not lose what the closed row said, and must not say it
    twice: the headings over the result and the list carry it there.
    """
    task = Task(
        id="t1",
        title="do t1",
        state=TaskState.COMPLETED,
        claimed_by=QA,
        declared_by=ROOT,
        result=result,
        result_basis=basis,
        not_run=not_run,
    )
    lines = _row_lines(monkeypatch, task, opened=True)

    assert f"{said} · {unrun}" not in lines
    if result:
        said_of = said.removeprefix("result ")
        assert any(line.startswith("result, as ") and f"({said_of})" in line for line in lines)
    if not_run:
        assert any(line.startswith("not run, as ") for line in lines)
        assert not_run in lines
    else:
        assert unrun in lines


def _row_colours(
    monkeypatch, task: Task, *, opened: bool, concerns: tuple[Concern, ...] = ()
) -> dict[str, object]:
    """Each line one row draws, against the colour it was drawn in."""
    snap = board_snapshot(task, concerns=concerns)
    fake = MagicMock()
    fake.tree_node_ex.return_value = opened
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    board_pane.draw(snap, at_root(snap), board_pane.BoardState(), Bridge())
    return {str(call.args[1]): call.args[0] for call in fake.text_colored.call_args_list}


def _done(**said: object) -> Task:
    return Task(
        id="t1",
        title="do t1",
        state=TaskState.COMPLETED,
        claimed_by=QA,
        declared_by=ROOT,
        **said,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("result", "basis", "not_run", "reassuring"),
    [
        ("ran it", Basis.RUN, "", True),
        ("ran it", Basis.READ, "", True),
        # The row the screenshot caught: nothing left unrun, by a claimer that did
        # not say how it knows.
        ("ran it", None, "", False),
        ("ran it", Basis.RUN, None, False),
        ("ran it", Basis.RUN, "the probe", False),
        ("ran it", None, None, False),
        ("", None, None, False),
        ("", None, "the probe", False),
    ],
)
def test_a_closed_rows_standing_is_reassuring_only_when_both_halves_are_stated(
    monkeypatch, result: str, basis: Basis | None, not_run: str | None, reassuring: bool
) -> None:
    """
    One line, two statements, one colour. Dim is what this pane draws a line in
    when nothing on it wants the operator, so it is earned by both halves: a
    stated basis, and a stated "nothing was left unrun". An unstated half in that
    colour reads as settled on the strength of the other.
    """
    task = _done(result=result, result_basis=basis, not_run=not_run)
    lines = _row_colours(monkeypatch, task, opened=False)

    # The summary above the rows is joined the same way, and is not a row's line.
    (standing,) = [line for line in lines if " · " in line and "task(s)" not in line]
    assert lines[standing] == (P.text_dim.vec4 if reassuring else P.warn.vec4)
    # A basis and "nothing left unrun" with no result under them: the reducer does
    # not store this, and a row built another way must not read as settled either.
    hollow = dataclasses.replace(
        board_task(state=TaskState.COMPLETED), result_basis=Basis.RUN, not_run=""
    )
    assert board_pane.settled(hollow) is False


@pytest.mark.parametrize(
    ("stated", "spec", "result"),
    [
        (Basis.RUN, P.text_dim.vec4, P.accent.vec4),
        (Basis.RELAYED, P.text_dim.vec4, P.accent.vec4),
        (None, P.warn.vec4, P.warn.vec4),
    ],
)
def test_an_opened_rows_headings_draw_an_unstated_basis_as_a_warning(
    monkeypatch, stated: Basis | None, spec: object, result: object
) -> None:
    """The same rule behind the triangle, on each heading that carries a basis."""
    task = _done(detail="the spec", basis=stated, result="ran it", result_basis=stated)
    said = "basis unstated" if stated is None else f"says: {stated.value}"
    lines = _row_colours(monkeypatch, task, opened=True)

    assert lines[f"specification ({said}):"] == spec
    (heading,) = [line for line in lines if line.startswith("result, as ")]
    assert heading.endswith(f"({said}):") and lines[heading] == result


@pytest.mark.parametrize(
    ("basis", "incident", "colour"),
    [
        (Basis.RUN, False, P.accent.vec4),
        (None, False, P.warn.vec4),
        # The colour an incident has in the log below, on the row it explains too.
        (Basis.RUN, True, P.warn.vec4),
        (None, True, P.warn.vec4),
    ],
)
def test_a_reason_is_drawn_as_a_warning_when_it_is_an_incident_or_states_no_basis(
    monkeypatch, basis: Basis | None, incident: bool, colour: object
) -> None:
    note = Concern(
        id="c1",
        sender=QA,
        recipient=ROOT,
        subject="wrote outside touches",
        body="store.py",
        posted_at=1.0,
        task_id="t1",
        basis=basis,
        incident=incident,
    )
    lines = _row_colours(monkeypatch, _done(result="r"), opened=True, concerns=(note,))

    said = "basis unstated" if basis is None else f"says: {basis.value}"
    mark = "INCIDENT " if incident else ""
    assert lines[f"reason ({said}): {mark}wrote outside touches"] == colour
    # The log's own lines for the same concern, which is where the colour comes
    # from: the marker is the pane's and the subject is the sender's.
    assert lines["wrote outside touches"] == (P.warn.vec4 if incident else P.text.vec4)
    assert lines.get("INCIDENT") == (P.warn.vec4 if incident else None)


def test_an_opened_finished_row_with_no_result_says_so_as_the_closed_row_does(
    monkeypatch,
) -> None:
    """
    The opened row is the fuller reading, so it may not say less: a finished row
    nobody reported on draws that, above where it stands on what was not run.
    """
    closed = _row_colours(monkeypatch, _done(), opened=False)
    opened = _row_colours(monkeypatch, _done(), opened=True)

    assert "no result reported · not run: unstated" in closed
    lines = list(opened)
    assert lines.index("no result reported") + 1 == lines.index("not run: unstated")
    assert opened["no result reported"] == P.warn.vec4
    # Not on a row with a result, and not on a row that is not finished.
    assert "no result reported" not in _row_colours(monkeypatch, _done(result="r"), opened=True)
    waiting = Task(id="t1", title="do t1", declared_by=ROOT)
    assert "no result reported" not in _row_colours(monkeypatch, waiting, opened=True)


@pytest.mark.parametrize(
    ("basis", "line", "colour"),
    [
        (Basis.READ, "no specification text (says: read)", P.text_dim.vec4),
        (None, None, None),
    ],
)
def test_a_basis_declared_with_no_specification_text_is_still_shown(
    monkeypatch, basis: Basis | None, line: str | None, colour: object
) -> None:
    """
    A declarer can state how it knows and write no detail. The basis is stored, so
    a pane that hangs the marker off the text shows it nowhere. A row with neither
    draws no line for a specification it does not have.
    """
    task = Task(id="t1", title="do t1", declared_by=ROOT, basis=basis)
    lines = _row_colours(monkeypatch, task, opened=True)

    if line is None:
        assert not any("specification" in drawn_line for drawn_line in lines)
    else:
        assert lines[line] == colour


def test_the_unrun_heading_names_each_standing_and_is_empty_for_an_unfinished_row() -> None:
    """
    The heading an opened finished row draws, over its three standings and the row
    with no result at all -- and nothing for a row that is not finished, where
    "not run" would be a statement about work nobody has claimed to have done.
    """
    done = TaskState.COMPLETED

    def row(not_run: str | None, *, state: TaskState = done, result: str = "r") -> BoardTask:
        return dataclasses.replace(
            board_task(owner="builder", state=state, result=result), not_run=not_run
        )

    assert board_pane.unrun_heading(row(None)) == "not run: unstated"
    assert board_pane.unrun_heading(row(None, result="")) == "not run: unstated"
    assert board_pane.unrun_heading(row("")) == "says nothing was left unrun"
    assert board_pane.unrun_heading(row("the probe")) == "not run, as builder states it:"
    assert board_pane.unrun_heading(row(None, state=TaskState.CLAIMED, result="")) == ""
    assert board_pane.unrun_heading(row(None, state=TaskState.PENDING, result="")) == ""


def test_the_operator_is_shown_what_a_claimer_did_not_run(monkeypatch) -> None:
    """
    From the record to the screen: an unstated row draws the absence, and a listed
    one draws the claimer's list under its heading and does not also draw the
    absence.
    """
    unsaid = drawn(monkeypatch, _finished("ran it"))
    listed = drawn(
        monkeypatch,
        board_snapshot(dataclasses.replace(_finished("ran it").tasks["t1"], not_run="the probe")),
    )

    assert "not run: unstated" in unsaid
    assert "not run: unstated" not in listed
    heading = next(i for i, line in enumerate(listed) if line.startswith("not run, as "))
    assert listed[heading + 1] == "the probe"


def _noted(*, incident: bool) -> Snapshot:
    note = Concern(
        id="c1",
        sender=QA,
        recipient=ROOT,
        subject="wrote outside touches",
        body="store.py",
        posted_at=1.0,
        task_id="t1",
        incident=incident,
    )
    return board_snapshot(_finished("r").tasks["t1"], concerns=(note,))


def test_an_incident_is_marked_on_the_row_it_explains_and_in_the_log(monkeypatch) -> None:
    marked = drawn(monkeypatch, _noted(incident=True))
    plain = drawn(monkeypatch, _noted(incident=False))

    assert "reason (basis unstated): INCIDENT wrote outside touches" in marked
    # In the log the marker is a line of the pane's own, over the sender's subject.
    assert marked[marked.index("wrote outside touches") - 1] == "INCIDENT"
    assert "reason (basis unstated): wrote outside touches" in plain
    assert "wrote outside touches" in plain
    assert not any("incident" in line.lower() for line in plain)


def _held_snapshot() -> Snapshot:
    return board_snapshot(
        Task(id="t1", title="do t1", detail="the spec", declared_by=ROOT, property=_HELD)
    )


def test_opening_the_editor_seeds_the_property_draft_from_the_declared_one(monkeypatch) -> None:
    pane = board_pane.BoardState()
    _amend_run(monkeypatch, _held_snapshot(), pane, {"amend##t1"})

    assert pane.property_drafts["t1"] == _HELD


def test_amending_only_the_specification_sends_no_property(monkeypatch) -> None:
    """
    The property box was drawn holding the declared sentence and was not touched,
    so the amendment says nothing about it and the reducer leaves it standing.
    Sending it back unchanged would work today and would make every amendment a
    rewrite of a sentence the operator never looked at.
    """
    pane = board_pane.BoardState(editing="t1", drafts={"t1": "a new spec"})
    emitted = _amend_run(monkeypatch, _held_snapshot(), pane, {"amend##apply-t1"})

    assert emitted == [board_pane.TaskAmended(task_id="t1", detail="a new spec", property=None)]


def test_amending_only_the_property_leaves_the_declarers_basis_on_the_board(monkeypatch) -> None:
    """
    From the button to the record. The editor sends the specification box as it
    was drawn, so what keeps the basis is the reducer seeing the same words -- and
    a rewritten specification from the same editor still drops it.
    """
    from pptmstr.intents import TaskDeclared
    from pptmstr.store import Store

    def amended(**drafts: str) -> Task:
        store = Store()
        store.apply(
            TaskDeclared(
                Task(
                    id="t1", title="do t1", detail="the spec", basis=Basis.RELAYED, property=_HELD
                ),
                node_id=ROOT,
            )
        )
        pane = board_pane.BoardState(
            editing="t1",
            drafts={"t1": drafts.get("detail", "the spec")},
            property_drafts={"t1": drafts.get("held_to", _HELD)},
        )
        snap = dataclasses.replace(board_snapshot(), tasks=store.snapshot().tasks)
        for intent in _amend_run(monkeypatch, snap, pane, {"amend##apply-t1"}):
            store.apply(intent)
        return store.snapshot().tasks["t1"]

    only_property = amended(held_to="A different sentence.")
    assert (only_property.property, only_property.basis) == (
        "A different sentence.",
        Basis.RELAYED,
    )
    assert amended().basis is Basis.RELAYED
    assert amended(detail="a new spec").basis is None


def test_a_rewritten_property_is_sent_as_one_line_and_an_emptied_one_withdraws_it(
    monkeypatch,
) -> None:
    for typed, sent in (("  never lose\n a claim. ", "never lose a claim."), ("  ", "")):
        pane = board_pane.BoardState(
            editing="t1", drafts={"t1": "the spec"}, property_drafts={"t1": typed}
        )
        emitted = _amend_run(monkeypatch, _held_snapshot(), pane, {"amend##apply-t1"})

        assert emitted == [board_pane.TaskAmended(task_id="t1", detail="the spec", property=sent)]
        assert "t1" not in pane.property_drafts


def test_a_property_past_the_limit_is_not_applied_and_the_editor_says_why(monkeypatch) -> None:
    """
    The reducer refuses nothing, and every surface shows a property whole, so the
    editor is the only bound on one the operator writes. Nothing is emitted, the
    draft is kept, and the reason is on screen.
    """
    from pptmstr.model import MAX_PROPERTY_CHARS

    typed = "x" * (MAX_PROPERTY_CHARS + 5)
    pane = board_pane.BoardState(
        editing="t1", drafts={"t1": "the spec"}, property_drafts={"t1": typed}
    )
    emitted = _amend_run(monkeypatch, _held_snapshot(), pane, {"amend##apply-t1"})
    fake_lines = [
        str(a) for call in board_pane.imgui.text_colored.call_args_list for a in call.args[1:]
    ]

    assert emitted == []
    assert pane.editing == "t1" and pane.property_drafts["t1"] == typed
    assert any(f"5 over the {MAX_PROPERTY_CHARS}" in line for line in fake_lines)
    assert board_pane.property_overrun_label("x" * MAX_PROPERTY_CHARS) == ""


def test_a_draft_property_does_not_outlive_its_task() -> None:
    pane = board_pane.BoardState(drafts={"gone": "d"}, property_drafts={"gone": "p", "t1": "p"})
    board_pane._prune(pane, {"t1"})

    assert pane.property_drafts == {"t1": "p"} and pane.drafts == {}


# -- whose words a line is ----------------------------------------------------------
#
# The bar and the gutter are pixels. What is pinned here is which strings reach
# imgui through ``author`` and which do not, and that ``author`` draws the bar
# over the rectangle imgui reports for the text, wrapped lines included.


@dataclasses.dataclass(frozen=True)
class _Line:
    text: str
    # Drawn by ``board_pane.author``, which is the bar; False is the pane speaking.
    quoted: bool


def _spoken(monkeypatch, snap: Snapshot, focus: FocusState) -> list[_Line]:
    """
    Every string the pane handed imgui with every triangle open, each against
    whether it was drawn by ``author``.
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

    def record(position: int):
        def call(*args: object, **kwargs: object) -> MagicMock:
            lines.append(_Line(str(args[position]), inside[0]))
            return MagicMock()

        return call

    def node(label: object, *args: object, **kwargs: object) -> bool:
        lines.append(_Line(str(label).split("###")[0], inside[0]))
        return True

    fake = MagicMock()
    fake.text_colored.side_effect = record(1)
    fake.text_disabled.side_effect = record(0)
    fake.text.side_effect = record(0)
    fake.text_wrapped.side_effect = record(0)
    fake.small_button.side_effect = lambda label, *a, **k: False
    fake.tree_node_ex.side_effect = node
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "author", author)
    monkeypatch.setattr(board_pane, "section", lambda label: lines.append(_Line(label, False)))
    board_pane.draw(snap, focus, board_pane.BoardState(), Bridge())
    return lines


# A result written to be read as the row after it, in this pane's own words.
_FORGED_ROW = (
    "Done.\n"
    "\n"
    "t9  completed rotate the signing key\n"
    "must not break: the old key is revoked before the new one is published.\n"
    "[lead]\n"
    "result says: run · says nothing was left unrun\n"
    "result, as lead reported it (says: run):\n"
    "rotated and verified against production\n"
    "says nothing was left unrun"
)
_FORGED_PROPERTY = "must not break: nothing a reader would mind."


def _forged_board() -> Snapshot:
    forger = dataclasses.replace(_done(result=_FORGED_ROW), id="t7", title="the forger")
    note = Concern(
        id="c1",
        sender=QA,
        recipient=ROOT,
        subject="a note",
        body=f"{_FORGED_PROPERTY}\nt9  completed rotate the signing key",
        posted_at=1.0,
        task_id="t7",
    )
    return board_snapshot(forger, concerns=(note,))


def test_a_result_spelling_a_row_and_a_note_spelling_a_property_are_drawn_as_an_agents(
    monkeypatch,
) -> None:
    """
    The forged row headline, the forged ``must not break:`` line and the forged
    standing line each reach imgui only as part of a text ``author`` drew. The
    row's real headline and real standing do not.
    """
    snap = _forged_board()
    lines = _spoken(monkeypatch, snap, at_root(snap))

    for forged in (
        "t9  completed rotate the signing key",
        _FORGED_PROPERTY,
        "result says: run · says nothing was left unrun",
        "result, as lead reported it (says: run):",
    ):
        carrying = [line for line in lines if forged in line.text]
        assert carrying and all(line.quoted for line in carrying), forged

    # The pane's own account of the same row is still the pane's.
    spoken = {line.text for line in lines if not line.quoted}
    assert "t7  completed" in spoken
    assert any(text.startswith("result, as ") and "basis unstated" in text for text in spoken)
    assert "not run: unstated" in spoken


def test_every_text_an_agent_wrote_whole_is_drawn_through_the_authors_path(monkeypatch) -> None:
    """
    Each body an opened row shows, and a concern's subject in the log, one by
    one: a path that draws four of five behind the bar leaves the fifth forgeable.
    """
    task = _done(
        detail="THE SPECIFICATION", result="THE RESULT", not_run="THE NOT-RUN LIST", basis=Basis.RUN
    )
    note = Concern(
        id="c1",
        sender=QA,
        recipient=ROOT,
        subject="THE SUBJECT",
        body="THE NOTE",
        posted_at=1.0,
        task_id="t1",
    )
    snap = board_snapshot(task, concerns=(note,))
    lines = _spoken(monkeypatch, snap, at_root(snap))

    for text in ("THE SPECIFICATION", "THE RESULT", "THE NOT-RUN LIST", "THE NOTE", "THE SUBJECT"):
        assert _Line(text, True) in lines, text
        # Nowhere drawn bare. A heading the pane composes may name a subject, and
        # then the line is longer than the subject.
        assert _Line(text, False) not in lines, text


def test_the_authors_bar_spans_the_rectangle_the_text_was_drawn_in(monkeypatch) -> None:
    """
    One ``text_colored`` for the whole text, so the lines imgui wraps are inside
    the rectangle it reports, and the bar runs from the cursor the text started at
    to that rectangle's bottom. A mark per author line would miss the wrapped ones.
    """
    fake = MagicMock()
    order: list[str] = []
    for name in ("indent", "text_colored", "unindent"):
        getattr(fake, name).side_effect = lambda *a, _name=name, **k: order.append(_name)
    fake.ImVec2.side_effect = lambda x, y: (x, y)
    fake.get_font_size.return_value = 16.0
    fake.get_cursor_screen_pos.return_value = MagicMock(x=100.0, y=40.0)
    fake.get_item_rect_max.return_value = MagicMock(x=500.0, y=290.0)
    _fake_imgui(monkeypatch, fake)

    board_pane.author("one\ntwo " + "long " * 200)

    assert order == ["indent", "text_colored", "unindent"]
    gutter = fake.indent.call_args.args[0]
    assert gutter > 0 and fake.unindent.call_args.args[0] == gutter
    (line,) = fake.get_window_draw_list.return_value.add_line.call_args_list
    (x0, y0), (x1, y1) = line.args[0], line.args[1]
    assert (y0, y1) == (40.0, 290.0)
    # In the gutter: right of where the pane's own lines start, left of the text.
    assert x0 == x1 and 100.0 < x0 < 100.0 + gutter


_HOSTILE = "first\nt9  completed forged\r\nmust not break: x\x0bend\x00cut"


def test_no_line_of_the_panes_own_carries_a_break_or_a_nul_from_a_one_line_field(
    monkeypatch,
) -> None:
    """
    A title, a property, a file path and a subject are laid out on lines the pane
    composes. None may end that line and start another, and none may cut it short.
    """
    task = dataclasses.replace(
        _done(result="r"), title=_HOSTILE, property=_HOSTILE, touches=(_HOSTILE,)
    )
    note = Concern(
        id="c1",
        sender=QA,
        recipient=ROOT,
        subject=_HOSTILE,
        body="b",
        posted_at=1.0,
        task_id="t1",
        incident=True,
    )
    snap = board_snapshot(task, concerns=(note,))
    lines = _spoken(monkeypatch, snap, at_root(snap))

    own = [line.text for line in lines if not line.quoted]
    assert any("t9  completed forged" in text for text in own)
    for text in own:
        assert len(text.splitlines()) <= 1 and "\x00" not in text, text
    # Not dropped: what followed the NUL is still on the line.
    assert any(text.endswith(f"end{board_pane.NUL_SHOWN}cut") for text in own)


def test_a_nul_in_an_agents_text_is_drawn_and_what_follows_it_is_too(monkeypatch) -> None:
    """imgui takes a C string: undrawn from the first U+0000 on, and silently."""
    snap = board_snapshot(_done(result="abc\x00def\nsecond line"))
    lines = _spoken(monkeypatch, snap, at_root(snap))

    assert _Line(f"abc{board_pane.NUL_SHOWN}def\nsecond line", True) in lines
    assert not any("\x00" in line.text for line in lines)


def test_the_panes_one_line_rule_is_the_one_the_board_tool_uses() -> None:
    """
    ``one_line`` is spelled twice, here and in the bus, so the UI does not import
    the SDK. The two must call the same things a line break.
    """
    from pptmstr import bus

    for text in (
        "plain",
        "a\nb",
        "a\r\nb",
        "a\rb",
        "a\x0bb\x0cc",
        "a\x1cb\x1dc\x1ed",
        "a\x85b\u2028c\u2029d",
        "trailing\n",
        "\n\nleading",
        "",
    ):
        assert board_pane.one_line(text) == bus._one_line(text), repr(text)


def test_amending_a_second_row_abandons_the_draft_left_on_the_first(monkeypatch) -> None:
    """
    One editor. A draft left behind when the operator moved to another row would
    come back, unasked, the next time the first row is amended.
    """
    snap = board_snapshot(
        Task(id="t1", title="do t1", detail="the first spec", declared_by=ROOT),
        Task(id="t2", title="do t2", detail="the second spec", declared_by=ROOT),
    )
    pane = board_pane.BoardState(
        editing="t1", drafts={"t1": "half a thought"}, property_drafts={"t1": "half a sentence"}
    )

    _amend_run(monkeypatch, snap, pane, {"amend##t2"})
    assert pane.editing == "t2"
    assert pane.drafts == {"t2": "the second spec"} and pane.property_drafts == {"t2": ""}

    _amend_run(monkeypatch, snap, pane, {"amend##t1"})
    assert pane.editing == "t1" and pane.drafts == {"t1": "the first spec"}


# -- a replaced specification, on the row --------------------------------------------
#
# Verified through the labels and the recorded draw calls only: nothing drawn by
# these lines has been seen on a screen.


def _replaced(**said: object) -> Task:
    from pptmstr.model import TaskVersion

    return Task(
        id="t1",
        title="do t1",
        detail="second",
        declared_by=ROOT,
        claimed_by=QA,
        held_by=(QA,),
        claimed_version=1,
        superseded=(TaskVersion("first", "", None, replaced_at=1.0, replaced_by=None),),
        **said,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("opened", [True, False])
@pytest.mark.parametrize(
    ("state", "stale"),
    [
        (TaskState.CLAIMED, "its holder was handed v1 and has not been shown v2"),
        (TaskState.COMPLETED, "result written against v1; the specification is now v2"),
    ],
)
def test_a_row_draws_its_version_and_what_is_behind_it_open_or_closed(
    monkeypatch, opened: bool, state: TaskState, stale: str
) -> None:
    """
    Both are facts about the row and not a reading of its text, so neither waits
    behind the triangle. The stale line is in the warning colour: it is the row an
    operator should not read as settled.
    """
    colours = _row_colours(monkeypatch, _replaced(state=state), opened=opened)

    assert colours["specification v2, replaced by the operator"] == P.text_dim.vec4
    assert colours[stale] == P.warn.vec4


@pytest.mark.parametrize("opened", [True, False])
def test_a_row_on_its_declared_specification_draws_no_version_line(
    monkeypatch, opened: bool
) -> None:
    task = Task(id="t1", title="do t1", detail="first", declared_by=ROOT, claimed_by=QA)
    lines = _row_lines(
        monkeypatch, dataclasses.replace(task, state=TaskState.CLAIMED), opened=opened
    )

    assert not any("specification v" in line or "handed" in line for line in lines)


def test_a_holder_shown_the_current_specification_draws_the_version_and_no_warning(
    monkeypatch,
) -> None:
    current = dataclasses.replace(_replaced(state=TaskState.CLAIMED), claimed_version=2)
    lines = _row_lines(monkeypatch, current, opened=False)

    assert "specification v2, replaced by the operator" in lines
    assert not any("handed" in line or "written against" in line for line in lines)


# -- what the board's log vouches for ------------------------------------------------


def _restore(**fields: object):  # type: ignore[no-untyped-def]
    from pptmstr.model import Restore

    return Restore(session_id="s1", **{"at": 5.0, "tasks": 2, "concerns": 1, **fields})  # type: ignore[arg-type]


def _held() -> Task:
    return Task(id="t1", title="held from before", declared_at=1.0, declared_by=ROOT)


def _log(**fields: object) -> board_pane.LogState:
    return board_pane.LogState(directory="/cfg/boards/s1", **fields)  # type: ignore[arg-type]


def test_a_session_nothing_was_restored_for_has_no_restore_line() -> None:
    assert board_pane.restore_line(None, _log()) is None
    assert board_pane.restore_line(None, None) is None


def test_a_whole_restore_says_what_came_back_and_how_recent_the_log_was() -> None:
    line = board_pane.restore_line(_restore(), _log(newest=1_760_000_000.0))

    assert line is not None and line.tone is board_pane.Tone.PROSE
    assert "restored from a log" in line.text
    assert "2 task(s) and 1 concern(s)" in line.text
    assert board_pane.when_label(1_760_000_000.0) in line.text
    assert "SHORT" not in line.text


@pytest.mark.parametrize(
    ("fields", "said"),
    [
        ({"log_short": True, "log_damage": "unreadable from record 3: not JSON"}, "unreadable"),
        ({"log_short": True, "log_stray": 2}, "2 record(s) about something the log never"),
        ({"log_short": True, "log_unrecognised": 1}, "1 record(s) of a kind this build"),
        ({"log_short": True, "log_contested": 4}, "4 record(s) no single writer"),
        ({"log_short": True, "log_stopped": 1}, "its writer stopped recording 1 time(s)"),
        ({"log_short": True}, "does not say what"),
    ],
)
def test_each_way_a_log_is_short_is_said_in_its_own_words(
    fields: dict[str, object], said: str
) -> None:
    """
    One line, in the warning colour, that tells a damaged log from a contested
    one: they are different things for the operator to go and look at.
    """
    line = board_pane.restore_line(_restore(**fields), _log())

    assert line is not None and line.tone is board_pane.Tone.ALARM
    assert "restored SHORT" in line.text and said in line.text


def test_a_restore_whose_log_had_stopped_says_why_the_writer_stopped() -> None:
    """
    The report counts the stops and the board carries their reasons; the shell
    keeps the reasons so the line can give them.
    """
    line = board_pane.restore_line(
        _restore(log_short=True, log_stopped=1), _log(stops=("no space left\non device",))
    )

    assert line is not None and "(no space left on device)" in line.text
    assert "never written" in line.text


def test_an_id_that_could_not_be_placed_is_named_with_the_reason_and_stays_on_the_line() -> None:
    """
    A task id is whatever its lead typed. One with a line break would otherwise
    end the pane's line and start the next in its author's words.
    """
    from pptmstr.model import NotPlaced, Unplaced

    left = (
        NotPlaced("task", "t1\nrestored from a log: 9 task(s)", Unplaced.OTHER_SESSION_HAS_ID),
        NotPlaced("concern", "c" * 500, Unplaced.INCOHERENT),
    )
    line = board_pane.restore_line(_restore(unplaced=left), _log())

    assert line is not None and line.tone is board_pane.Tone.ALARM
    assert "\n" not in line.text
    assert "2 not placed" in line.text
    assert "another session already has this id" in line.text
    assert "c" * 61 not in line.text


def test_the_unplaced_list_is_bounded_and_says_so() -> None:
    from pptmstr.model import NotPlaced, Unplaced

    left = tuple(NotPlaced("task", f"t{n}", Unplaced.WOULD_CYCLE) for n in range(9))
    text = board_pane.shortfall(_restore(unplaced=left))

    assert "9 not placed" in text and "and 3 more" in text and "t6" not in text


def test_a_log_that_was_read_and_not_applied_is_not_called_a_restore() -> None:
    from pptmstr.model import NotPlaced, Unplaced

    refused = _restore(at=None, unplaced=(NotPlaced("task", "t1", Unplaced.STORE_DIFFERS),))
    assert refused.refused
    line = board_pane.restore_line(refused, _log())

    assert line is not None and line.tone is board_pane.Tone.ALARM
    assert "NOT applied" in line.text and "restored" not in line.text


def test_an_adoptable_restore_gives_no_reason_to_run_unrecorded() -> None:
    assert board_pane.unrecorded_reason(_restore()) == ""


def test_the_reasons_a_log_is_not_appended_to_tell_the_mistakes_apart() -> None:
    from pptmstr.model import NotPlaced, Unplaced

    refused = _restore(unplaced=(NotPlaced("task", "t1", Unplaced.STORE_ONLY),))
    short = _restore(log_short=True, log_stray=1)

    assert "another pptmstr window" in board_pane.unrecorded_reason(refused)
    assert "short log" in board_pane.unrecorded_reason(short)
    assert "another pptmstr window" not in board_pane.unrecorded_reason(short)
    assert board_pane.unrecorded_reason(None) != ""


def test_a_recorded_board_says_where_and_that_nothing_deletes_it() -> None:
    line = board_pane.recording_line(None, _log())

    assert line is not None and line.tone is board_pane.Tone.PROSE
    assert "/cfg/boards/s1" in line.text and "until you delete it" in line.text


def test_a_board_that_stopped_being_recorded_says_why_and_what_the_log_will_claim() -> None:
    stopped = _log(recording=False, stopped=True, why_not="OSError: disk full", unkept=3)

    said = board_pane.recording_line(None, dataclasses.replace(stopped, marked=True))
    unsaid = board_pane.recording_line(None, dataclasses.replace(stopped, marked=False))

    assert said is not None and unsaid is not None
    assert said.tone is board_pane.Tone.ALARM
    assert "no longer being recorded: OSError: disk full" in said.text
    assert "3 record(s)" in said.text and "will restore as short" in said.text
    assert "does NOT say so" in unsaid.text and "as though it were whole" in unsaid.text


def test_a_session_never_adopted_is_said_to_be_running_unrecorded_and_why() -> None:
    line = board_pane.recording_line(None, _log(recording=False, why_not="its log is short"))

    assert line is not None and line.tone is board_pane.Tone.ALARM
    assert "running UNRECORDED: its log is short" in line.text
    assert "will survive a restart" in line.text


def test_a_board_not_recorded_by_choice_says_so_without_alarm() -> None:
    line = board_pane.recording_line("switched off: board_log in settings.json", _log())

    assert line is not None and line.tone is board_pane.Tone.PROSE
    assert line.text == "not recorded (switched off: board_log in settings.json)"


def test_a_board_not_recorded_because_the_switch_could_not_be_read_is_an_alarm() -> None:
    """
    The same outcome as the choice above and the opposite standing: nobody asked
    for it. Read before ``off`` and before a log entry, so neither can say
    "switched off" or "recorded" over it.
    """
    line = board_pane.recording_line(
        "switched off: board_log in settings.json", _log(), "settings could not be read (/c/s.json)"
    )

    assert line is not None and line.tone is board_pane.Tone.ALARM
    assert line.text == "not recorded: settings could not be read (/c/s.json)"


def test_the_pane_draws_the_unread_reason_for_any_session(monkeypatch) -> None:
    logging = board_pane.Logging(unread="board_log in settings.json is not true or false")

    lines = _drawn_with(monkeypatch, board_snapshot(_held()), logging)

    assert "not recorded: board_log in settings.json is not true or false" in lines


def test_a_session_with_no_log_entry_is_not_passed_off_as_recorded() -> None:
    line = board_pane.recording_line(None, None)

    assert line is not None and line.tone is board_pane.Tone.ALARM
    assert "not being recorded" in line.text


def test_nothing_is_said_about_recording_while_a_restore_is_still_being_applied() -> None:
    assert board_pane.recording_line(None, _log(recording=False, awaiting=True)) is None


def test_records_behind_a_break_are_counted_on_a_board_still_being_recorded() -> None:
    line = board_pane.recording_line(None, _log(unkept=2))

    assert line is not None and line.tone is board_pane.Tone.ALARM
    assert "2 record(s)" in line.text and "will not be read back" in line.text


def _drawn_with(monkeypatch, snap: Snapshot, logging: board_pane.Logging | None) -> list[str]:
    fake = MagicMock()
    fake.tree_node_ex.return_value = False
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    board_pane.draw(snap, at_root(snap), board_pane.BoardState(), Bridge(), logging)
    return [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]] + [
        str(a) for call in fake.text_disabled.call_args_list for a in call.args
    ]


def test_the_pane_draws_both_lines_for_the_session_under_the_cursor(monkeypatch) -> None:
    """
    The wiring: the labels above are worth nothing if ``draw`` does not ask for
    them, or asks about another session's entry.
    """
    snap = dataclasses.replace(
        board_snapshot(_held()),
        restores=MappingProxyType({"s1": _restore(log_short=True, log_stray=1)}),
    )
    logging = board_pane.Logging(
        sessions={
            "s1": _log(recording=False, why_not="its log is short"),
            "s2": _log(recording=False, stopped=True, why_not="somebody else's"),
        }
    )

    lines = _drawn_with(monkeypatch, snap, logging)

    assert any("restored SHORT" in line for line in lines)
    assert any("running UNRECORDED: its log is short" in line for line in lines)
    assert not any("somebody else's" in line for line in lines)


def test_a_caller_with_nothing_to_say_about_logs_draws_no_recording_line(monkeypatch) -> None:
    lines = _drawn_with(monkeypatch, board_snapshot(_held()), None)

    assert not any("record" in line for line in lines)


def test_a_solo_session_holding_board_facts_has_them_drawn_and_says_why(monkeypatch) -> None:
    """
    A team session resumed under the solo template. Its restored claim is in the
    store and holds its id; "this session is not a team" over it would hide a
    held task behind a statement about the launcher.
    """
    snap = board_snapshot(_held(), template="solo")

    lines = _drawn_with(monkeypatch, snap, None)

    assert not any(line == "this session is not a team" for line in lines)
    assert board_pane.NOT_A_TEAM_WITH_A_BOARD in lines
    assert board_pane.shows_board(False, 1, 0) and board_pane.shows_board(False, 0, 1)
    assert board_pane.shows_board(True, 0, 0) and not board_pane.shows_board(False, 0, 0)


# -- the operator closes a task -----------------------------------------------------


def _close_run(monkeypatch, snap: Snapshot, pane: board_pane.BoardState, clicks: set[str]):
    """
    ``_amend_run`` for the close box, handing back what was emitted and what was drawn.
    """
    emitted: list[object] = []

    class _Bridge:
        def emit(self, intent: object) -> None:
            emitted.append(intent)

    fake = MagicMock()
    fake.small_button.side_effect = lambda label, *a, **k: label in clicks
    boxes: list[str] = []

    def box(label: str, value: str, *a: object, **k: object) -> tuple[bool, str]:
        boxes.append(label)
        return False, value

    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    monkeypatch.setattr(board_pane, "multiline_input", box)
    board_pane.draw(snap, at_root(snap), pane, _Bridge())  # type: ignore[arg-type]
    buttons = [str(call.args[0]) for call in fake.small_button.call_args_list]
    said = [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]
    return emitted, buttons, boxes, said


def _claimed_snapshot() -> Snapshot:
    return board_snapshot(
        Task(
            id="t1",
            title="do t1",
            detail="the spec",
            declared_by=ROOT,
            state=TaskState.CLAIMED,
            claimed_by=QA,
        )
    )


@pytest.mark.parametrize("state", [TaskState.PENDING, TaskState.CLAIMED])
def test_an_unfinished_row_offers_to_close_and_a_finished_one_does_not(
    monkeypatch, state: TaskState
) -> None:
    open_row = board_snapshot(Task(id="t1", title="x", declared_by=ROOT, state=state))
    done = board_snapshot(
        Task(id="t1", title="x", declared_by=ROOT, state=TaskState.COMPLETED, completed_at=1.0)
    )

    _, offered, _, _ = _close_run(monkeypatch, open_row, board_pane.BoardState(), set())
    _, finished, _, _ = _close_run(monkeypatch, done, board_pane.BoardState(), set())

    assert "close...##close-t1" in offered
    assert not any("close" in label for label in finished)
    assert board_pane.closable(board_task(state=state))
    assert not board_pane.closable(board_task(state=TaskState.COMPLETED))


def test_opening_the_close_box_closes_nothing_and_abandons_an_amendment(monkeypatch) -> None:
    """
    One editor at a time, both ways. The first press only opens a box, and it
    says what closing means before a reason is typed.
    """
    pane = board_pane.BoardState(editing="t1", drafts={"t1": "half an amendment"})
    pane.editing = None  # the amend editor is not open; its draft is what must not survive

    emitted, _, _, _ = _close_run(monkeypatch, _claimed_snapshot(), pane, {"close...##close-t1"})
    _, _, boxes, said = _close_run(monkeypatch, _claimed_snapshot(), pane, set())

    assert emitted == []
    assert (pane.closing, pane.close_confirming) == ("t1", False)
    assert pane.drafts == {} and pane.property_drafts == {}
    assert boxes == ["##close-reason-t1"], "and not the amend boxes"
    assert board_pane.CLOSE_MEANS in said

    _close_run(monkeypatch, _claimed_snapshot(), pane, {"cancel##close-cancel-t1"})
    _amend_run(monkeypatch, _claimed_snapshot(), pane, {"amend##t1"})
    pane.close_reasons["t1"] = "left over"
    assert pane.editing == "t1"
    _close_run(monkeypatch, _claimed_snapshot(), pane, {"cancel##t1"})
    assert pane.close_reasons == {"t1": "left over"}, "cancelling an amendment is not a close"


def test_closing_takes_two_presses_and_sends_the_reason_as_typed(monkeypatch) -> None:
    """
    The first press asks; it emits nothing and takes the box away, so the reason
    on screen is the reason that will be sent. The second names the task and
    emits the operator's intent -- no node, the reason untouched.
    """
    reason = "  superseded\nby t9 "
    pane = board_pane.BoardState(closing="t1", close_reasons={"t1": reason})

    first, _, _, _ = _close_run(monkeypatch, _claimed_snapshot(), pane, {"close##close-apply-t1"})
    assert first == [] and pane.close_confirming and pane.closing == "t1"

    idle, buttons, boxes, _ = _close_run(monkeypatch, _claimed_snapshot(), pane, set())
    assert idle == [] and boxes == []
    assert "close t1 for good##close-confirm-t1" in buttons
    assert "close##close-apply-t1" not in buttons

    second, _, _, _ = _close_run(
        monkeypatch, _claimed_snapshot(), pane, {"close t1 for good##close-confirm-t1"}
    )
    assert second == [board_pane.TaskClosed(task_id="t1", reason=reason)]
    assert second[0].node_id is None  # type: ignore[attr-defined]
    assert (pane.closing, pane.close_reasons, pane.close_confirming) == (None, {}, False)


def test_the_close_box_promises_only_what_a_close_does() -> None:
    """
    The sentence the operator reads while deciding whether closing a HELD task
    is safe. The arm finishes the row, lets go of what waited and mails the
    holder; it interrupts nobody, and the box may not say or imply that it does.
    """
    said = board_pane.CLOSE_MEANS

    for claim in ("taken off", "is stopped.", "stops the agent", "released from"):
        assert claim not in said
    for fact in (
        "finished as closed by the operator",
        "cannot be reopened",
        "stops waiting",
        "NOT stopped",
        "reads its inbox",
        "may go on writing those files",
    ):
        assert fact in said


@pytest.mark.parametrize("confirming", [False, True], ids=["first press", "second press"])
def test_a_reason_over_the_bound_is_closed_by_neither_press_and_the_box_says_why(
    monkeypatch, confirming: bool
) -> None:
    """
    The reducer does not apply such a close and has nobody to tell, so a press
    that emitted it would look like a close that silently did nothing.
    """
    from pptmstr.model import MAX_CLOSE_REASON_CHARS

    reason = "x" * (MAX_CLOSE_REASON_CHARS + 7)
    pane = board_pane.BoardState(
        closing="t1", close_reasons={"t1": reason}, close_confirming=confirming
    )
    press = "close t1 for good##close-confirm-t1" if confirming else "close##close-apply-t1"

    emitted, buttons, _, said = _close_run(monkeypatch, _claimed_snapshot(), pane, {press})

    assert press in buttons, "the press was offered, so it is the guard that held"
    assert emitted == []
    assert (pane.closing, pane.close_confirming) == ("t1", confirming)
    assert board_pane.close_reason_overrun_label(reason) in said
    assert "7 over" in board_pane.close_reason_overrun_label(reason)
    assert board_pane.close_reason_overrun_label("x" * MAX_CLOSE_REASON_CHARS) == ""


def test_the_first_press_alone_never_closes_however_often_it_is_drawn(monkeypatch) -> None:
    pane = board_pane.BoardState(closing="t1", close_reasons={"t1": "x"})

    for _ in range(3):
        emitted, _, _, _ = _close_run(
            monkeypatch, _claimed_snapshot(), pane, {"close##close-apply-t1"}
        )
        assert emitted == []


@pytest.mark.parametrize(
    "click", ["keep editing##close-back-t1", "cancel##close-cancel-t1"], ids=["back", "cancel"]
)
def test_backing_out_after_the_first_press_closes_nothing(monkeypatch, click: str) -> None:
    pane = board_pane.BoardState(closing="t1", close_reasons={"t1": "x"}, close_confirming=True)

    emitted, _, _, _ = _close_run(monkeypatch, _claimed_snapshot(), pane, {click})

    assert emitted == [] and not pane.close_confirming
    if click.startswith("keep"):
        assert (pane.closing, pane.close_reasons) == ("t1", {"t1": "x"})
    else:
        assert (pane.closing, pane.close_reasons) == (None, {})


@pytest.mark.parametrize("confirming", [False, True], ids=["typing", "one press in"])
@pytest.mark.parametrize("fate", ["finished", "gone"])
def test_a_row_that_finishes_or_leaves_under_the_close_box_takes_the_box_with_it(
    monkeypatch, confirming: bool, fate: str
) -> None:
    """
    The holder completes the task while the operator is typing why to close it.
    The box is for a task that can no longer be closed: it goes, the reason goes
    with it, and a press already aimed at either button reaches nothing -- not
    this row, and not the next one a close box is opened on.
    """
    done = Task(
        id="t1",
        title="do t1",
        declared_by=ROOT,
        state=TaskState.COMPLETED,
        claimed_by=QA,
        completed_at=2.0,
        result="3/3 pass",
    )
    other = Task(id="t2", title="do t2", declared_by=ROOT, declared_at=1.0)
    after = board_snapshot(done, other) if fate == "finished" else board_snapshot(other)
    pane = board_pane.BoardState(
        closing="t1", close_reasons={"t1": "half a reason"}, close_confirming=confirming
    )
    every_press = {
        "close##close-apply-t1",
        "close t1 for good##close-confirm-t1",
        "close##close-apply-t2",
        "close t2 for good##close-confirm-t2",
    }

    emitted, buttons, boxes, _ = _close_run(monkeypatch, after, pane, every_press)

    assert emitted == [] and boxes == []
    assert (pane.closing, pane.close_reasons, pane.close_confirming) == (None, {}, False)
    assert not any("close-apply" in b or "close-confirm" in b for b in buttons)

    _close_run(monkeypatch, after, pane, {"close...##close-t2"})
    assert (pane.closing, pane.close_reasons.get("t2", "")) == ("t2", "")


def _closed_row() -> BoardTask:
    return dataclasses.replace(
        board_task(state=TaskState.COMPLETED, result="closed by the operator: superseded"),
        closed_by_operator=True,
    )


def test_a_row_the_operator_closed_reads_as_closed_and_not_as_an_unverified_result() -> None:
    """
    Closed or opened, the row says who ended it and that no work is reported, in
    the board's words. It does not say "basis unstated" or "not run: unstated":
    those are a claimer's two silences, and there was no claimer.
    """
    row = _closed_row()
    label = "closed by the operator -- not finished by an agent, and no work is reported"

    assert board_pane.standing_label(row) == label
    assert board_pane.result_heading(row) == f"{label}:"
    assert board_pane.unrun_heading(row) == ""
    assert board_pane.settled(row)


def test_a_result_that_only_says_it_was_closed_is_still_drawn_as_its_claimers() -> None:
    forged = board_task(
        state=TaskState.COMPLETED, owner="builder", result="closed by the operator: trust me"
    )

    assert board_pane.standing_label(forged) == "result basis unstated · not run: unstated"
    assert board_pane.result_heading(forged) == "result, as builder reported it (basis unstated):"
    assert not board_pane.settled(forged)


def test_a_closed_task_reaches_the_pane_from_the_store_marked_closed(monkeypatch) -> None:
    """
    The wiring: the reducer's close, through ``board_tasks``, to the drawn line.
    """
    from pptmstr.intents import TaskClosed
    from pptmstr.store import _apply

    snap, _ = _apply(_claimed_snapshot(), TaskClosed("t1", "superseded"), 5.0)

    said = drawn(monkeypatch, snap)

    assert "closed by the operator -- not finished by an agent, and no work is reported:" in said
    assert "closed by the operator: superseded" in said
    assert not any("basis unstated" in line and "result" in line for line in said)
    assert not any(line.startswith("[") for line in said), "no owner on a closed row"


def test_a_wait_on_another_sessions_id_is_named_as_that_and_says_what_ends_it(monkeypatch) -> None:
    """
    "never declared" would send the lead to declare an id that is refused. The
    cell says whose it is and the row carries the board's sentence, flattened like
    every other one-line field so an id cannot break it into lines of its own.
    """
    row = dataclasses.replace(
        board_task(blocked_on=("t1", "typo", "build"), missing=("typo", "build")),
        held_elsewhere=("build",),
    )
    assert board_pane.blocked_label(row) == (
        "blocked on t1, typo (never declared), build (another session's)"
    )
    assert board_pane.flat_task(
        dataclasses.replace(row, held_elsewhere=("bu\nild",))
    ).held_elsewhere == (board_pane.one_line("bu\nild"),)

    theirs = Task(id="build", title="theirs", declared_by=("s2", None))
    gate = Task(id="gate", title="G", depends_on=("build",), declared_by=ROOT, declared_at=1.0)
    said = drawn(monkeypatch, board_snapshot(theirs, gate))

    assert (
        "waits on build, which another session in this window holds -- it cannot be declared "
        "here; ask the operator to close this task and declare it again, under a new id, "
        "without that dependency"
    ) in said
    assert "blocked on build (another session's)" in said


# -- Dear ImGui itself ----------------------------------------------------------------
#
# A context with a display size and no backend, as ``tests/test_decision_view.py``
# does it: nothing is rendered and nothing here says how the box looks. What it
# shows and a recording imgui cannot is what the widget does with typed text, and
# what the keyboard is doing on the frame the box goes away.


class _CloseFrames:
    """
    The whole pane drawn in real frames over a store, with every row opened.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pptmstr.intents import AgentSpawned, TaskClaimRequested, TaskDeclared
        from pptmstr.store import Store

        self.store = Store()
        for node, parent, agent_type in ((ROOT, None, None), (QA, ROOT, "builder")):
            self.store.apply(
                AgentSpawned(
                    node_id=node,
                    parent=parent,
                    task="",
                    model="m",
                    started_at=0.0,
                    agent_type=agent_type,
                    template="feature" if parent is None else None,
                )
            )
        self.store.apply(TaskDeclared(Task(id="t1", title="do t1"), ROOT))
        self.store.apply(TaskDeclared(Task(id="t2", title="do t2", declared_at=1.0), ROOT))
        self.store.apply(TaskClaimRequested(QA, request_id="k", task_id="t1"))
        self.pane = board_pane.BoardState()
        self.sent: list[object] = []
        self.at: dict[str, tuple[float, float]] = {}
        self._monkeypatch = monkeypatch

    def emit(self, intent: object) -> None:
        self.sent.append(intent)

    def __enter__(self) -> _CloseFrames:
        from imgui_bundle import imgui

        self.imgui = imgui
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 900.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        at = self.at

        def note(label: str) -> None:
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            at[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)

        class Noting:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            def tree_node_ex(self, label: str, flags: int = 0) -> bool:
                # Rows ship closed; every one is opened.
                imgui.set_next_item_open(True)
                return bool(imgui.tree_node_ex(label, flags))

        real_box = board_pane.multiline_input

        def box(label: str, value: str, size: object, *, wrap: bool, flags: int = 0):  # type: ignore[no-untyped-def]
            result = real_box(label, value, size, wrap=wrap, flags=flags)  # type: ignore[arg-type]
            note(label)
            return result

        real_small_button = widgets.small_button

        def small_button(label: str) -> bool:
            pressed = real_small_button(label)
            note(label)
            return pressed

        self._monkeypatch.setattr(board_pane, "imgui", Noting())
        self._monkeypatch.setattr(widgets, "small_button", small_button)
        self._monkeypatch.setattr(board_pane, "multiline_input", box)
        return self

    def __exit__(self, *exc: object) -> None:
        self.imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        imgui = self.imgui
        for _ in range(count):
            self.store.apply_all(self.sent, now=50.0)  # type: ignore[arg-type]
            self.sent.clear()
            snap = self.store.snapshot()
            self.at.clear()
            imgui.new_frame()
            imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
            imgui.set_next_window_size(imgui.ImVec2(1000.0, 800.0))
            imgui.begin("board")
            board_pane.draw(snap, at_root(snap), self.pane, self)  # type: ignore[arg-type]
            imgui.end()
            imgui.end_frame()

    def click(self, label: str) -> None:
        x, y = self.at[label]
        self.io.add_mouse_pos_event(x, y)
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(3)

    def type(self, text: str) -> None:
        self.io.add_input_characters_utf8(text)
        self.frame(2)

    def enter(self) -> None:
        self.io.add_key_event(self.imgui.Key.enter, True)
        self.frame()
        self.io.add_key_event(self.imgui.Key.enter, False)
        self.frame()


@pytest.fixture
def close_frames(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    with _CloseFrames(monkeypatch) as real:
        real.frame(3)
        yield real


def test_in_dear_imgui_the_reason_typed_is_the_reason_stored_behind_the_label(
    close_frames: _CloseFrames,
) -> None:
    """
    Through the real widget and the real reducer: what was typed, blanks and the
    line break included, is what the board holds behind "closed by the operator".
    """
    frames = close_frames
    frames.click("close...##close-t1")
    assert frames.pane.closing == "t1"
    frames.click("##close-reason-t1")
    frames.type("  superseded ")
    frames.enter()
    frames.type("by t9  ")

    frames.click("close##close-apply-t1")
    assert frames.pane.close_confirming
    assert frames.store.snapshot().tasks["t1"].state is TaskState.CLAIMED
    assert "##close-reason-t1" not in frames.at, "the box is gone while the second press waits"

    frames.click("close t1 for good##close-confirm-t1")

    task = frames.store.snapshot().tasks["t1"]
    assert task.state is TaskState.COMPLETED and task.closed_by_operator
    assert task.result == "closed by the operator:   superseded \nby t9  "
    assert task.claimed_by is None
    assert frames.pane.closing is None and frames.pane.close_reasons == {}
    assert not any(label.startswith("close") and "t1" in label for label in frames.at)
    assert "close...##close-t2" in frames.at


def test_in_dear_imgui_a_row_finished_under_the_typist_leaves_keys_that_act_on_nothing(
    close_frames: _CloseFrames,
) -> None:
    """
    The holder completes ``t1`` while the operator is mid-sentence in its close
    box. The box is gone on the next frame and the keyboard is free -- which is
    the moment a letter of the sentence would otherwise be a shortcut. Nothing
    here turns the rest of the sentence into anything: no intent is emitted, no
    box takes it, the other row is untouched, and the application's one rule for
    a vanished text box (``review.Hold``) reads this box like any other and holds
    the keys.
    """
    from pptmstr.intents import TaskCompleted
    from pptmstr.ui.review import Hold

    frames = close_frames
    hold = Hold()
    frames.click("close...##close-t1")
    frames.click("##close-reason-t1")
    frames.type("because the hold")
    assert frames.io.want_text_input
    assert not hold.step(text_input=bool(frames.io.want_text_input), gesture=False)

    frames.store.apply(TaskCompleted(QA, "t1", at=40.0, result="3/3 pass"))
    frames.frame(3)
    assert (frames.pane.closing, frames.pane.close_reasons) == (None, {})
    assert not frames.io.want_text_input

    frames.type("er will not finish a j k")
    frames.enter()
    frames.frame(3)

    assert not hold.step(text_input=bool(frames.io.want_text_input), gesture=False)
    assert hold.held, "the keys are dead until Escape or a click, as after any text box"
    assert frames.sent == []
    snap = frames.store.snapshot()
    assert snap.tasks["t1"].result == "3/3 pass" and not snap.tasks["t1"].closed_by_operator
    assert snap.tasks["t2"].state is TaskState.PENDING
    assert (frames.pane.closing, frames.pane.editing, frames.pane.close_reasons) == (None, None, {})
    assert not any("close" in label and "t1" in label for label in frames.at)


def test_in_dear_imgui_typing_after_the_first_press_changes_nothing_that_is_sent(
    close_frames: _CloseFrames,
) -> None:
    frames = close_frames
    frames.click("close...##close-t2")
    frames.click("##close-reason-t2")
    frames.type("not wanted")
    frames.click("close##close-apply-t2")

    frames.type(" -- and a late addition")
    frames.frame(3)
    assert frames.pane.close_reasons == {"t2": "not wanted"}

    frames.click("keep editing##close-back-t2")
    assert "##close-reason-t2" in frames.at and not frames.pane.close_confirming
    frames.click("close##close-apply-t2")
    frames.click("close t2 for good##close-confirm-t2")

    assert frames.store.snapshot().tasks["t2"].result == "closed by the operator: not wanted"


# -- the digest is not this pane's ---------------------------------------------------


@pytest.mark.parametrize("seed", [3, 7])
def test_the_board_pane_draws_no_line_of_the_digest(monkeypatch, seed: int) -> None:
    """
    The digest is the DIGEST pane's. With every triangle open, nothing this pane
    hands imgui is a line the digest composes -- a heading, the lead's mark, an
    item or an alarm row -- and no triangle is the digest's. The bodies the digest
    quotes are not counted: a result and a note are on the board's own rows too.
    """
    from pptmstr.digest import digest, digest_mark
    from pptmstr.intents import DigestRead
    from pptmstr.ui import digest_pane
    from tests.test_digest import S1, generated_round

    store = generated_round(seed)
    store.apply(DigestRead((S1, None), request_id="g1", at=9.0))
    snap = store.snapshot()
    focus = FocusState()
    focus.to_node(snap, (S1, None), scope=Scope.SESSION)
    spoken = {line.text for line in _spoken(monkeypatch, snap, focus)}

    mark = digest_mark(snap, S1)
    rows = digest_pane.rest_rows(digest(snap, S1), mark)
    composed = {row.text for row in rows if row.tone is not board_pane.Tone.AUTHOR}
    composed |= {item.text for item in digest_pane.incident_items(digest(snap, S1, mark), mark)}
    assert len(composed) > 6 and digest_pane.DIGEST_MARK in composed
    assert composed & spoken == set()
    assert not any(text.startswith(("digest", "CHANGED", "WAITING ON YOU")) for text in spoken)
    # The board itself was drawn: this is not a pane that said "not a team".
    assert any(text.endswith("  completed") for text in spoken)
    assert not hasattr(board_pane, "rest_rows") and not hasattr(board_pane, "_digest")


# -- a closed card shows no board, and a removed session's is not drawn --------------
#
# Snapshots from the reducer (``tests/test_ui_text``).


def test_a_recalled_team_session_says_its_board_is_not_loaded(monkeypatch) -> None:
    """
    Not "not a team", which is what the pane says of a session with no board and
    is false of this one, and not an empty board with "no tasks declared yet".
    """
    from tests.test_ui_text import CLOSED_ROOT, pointed_at, recalled_snapshot

    snap = recalled_snapshot(template="feature")
    lines = drawn(monkeypatch, snap, pointed_at(snap, CLOSED_ROOT))

    assert lines == [board_pane.CLOSED_BOARD]
    assert headings(monkeypatch, snap) == []


def test_a_recalled_session_with_no_transcript_is_not_promised_a_resume(monkeypatch) -> None:
    from pptmstr.model import TranscriptPresence
    from tests.test_ui_text import CLOSED_ROOT, FROM_FILE, pointed_at, recalled_snapshot

    gone = dataclasses.replace(FROM_FILE, transcript=TranscriptPresence.GONE)
    snap = recalled_snapshot(gone)
    (line,) = drawn(monkeypatch, snap, pointed_at(snap, CLOSED_ROOT))

    assert line.startswith("closed — ") and "gone" in line and "until" not in line


def test_a_removed_sessions_board_is_not_drawn_as_a_launch_that_is_not_a_team(monkeypatch) -> None:
    """
    Its tasks stay in the store, holding their ids, and ``has_board`` is false for
    it. Without the line of its own those two together are the solo-resume case:
    the rows, under "this launch is not a team". The cursor does not rest on a
    removed session, so the node is handed over the way a caller that skipped
    ``FocusState.node`` would.
    """
    from pptmstr.store import Store
    from tests.test_ui_text import REMOVED, REMOVED_ROOT, run_and_remove

    store = Store()
    run_and_remove(store, declares=("t1",))
    snap = store.snapshot()
    assert "t1" in snap.tasks
    assert board_pane.absent_line(snap, REMOVED) == board_pane.OFF_FLEET_BOARD

    focus = MagicMock()
    focus.node.return_value = REMOVED_ROOT
    lines = drawn(monkeypatch, snap, focus)

    assert lines == [board_pane.OFF_FLEET_BOARD]


def test_a_session_on_the_fleet_has_no_line_in_place_of_its_board() -> None:
    from pptmstr.store import Store
    from tests.test_ui_text import RAN, run

    store = Store()
    run(store)

    assert board_pane.absent_line(store.snapshot(), RAN) is None
    assert board_pane.absent_line(store.snapshot(), "no-such-session") is None


def _blocked_by_a_removed_session(monkeypatch) -> tuple[Snapshot, list[str]]:
    """
    A session on the fleet whose restore left out ``t1`` because the removed
    session's board has it. The report is written by hand -- it is what
    ``store._restore`` records -- and whether its holder is off the fleet is the
    reducer's.
    """
    from pptmstr.model import NotPlaced, Unplaced
    from pptmstr.store import Store
    from tests.test_ui_text import RAN, RAN_ROOT, REMOVED, pointed_at, run, run_and_remove

    store = Store()
    run_and_remove(store, declares=("t1",))
    run(store)
    left = (
        NotPlaced("task", "t1", Unplaced.OTHER_SESSION_HAS_ID, holder=REMOVED),
        NotPlaced("task", "t2", Unplaced.OTHER_SESSION_HAS_ID, holder=RAN),
        NotPlaced("task", "t3", Unplaced.OTHER_SESSION_HAS_ID),
    )
    snap = dataclasses.replace(
        store.snapshot(),
        restores=MappingProxyType(
            {RAN: dataclasses.replace(_restore(unplaced=left), session_id=RAN)}
        ),
    )
    return snap, drawn(monkeypatch, snap, pointed_at(snap, RAN_ROOT))


def test_an_id_held_by_a_removed_session_is_named_as_that(monkeypatch) -> None:
    """
    "Another session already has this id" sends the operator to look for a card,
    and this session has none. Only the holder that is off the fleet is called
    removed: one on the fleet, and one the report does not name, keep the reason's
    own words.
    """
    snap, lines = _blocked_by_a_removed_session(monkeypatch)
    (line,) = [text for text in lines if "not placed" in text]

    assert "task t1: held by a session you removed from the fleet (sess-removed)" in line
    assert "task t2: another session already has this id" in line
    assert "task t3: another session already has this id" in line


def test_whether_a_holder_is_removed_is_asked_when_the_line_is_drawn() -> None:
    from pptmstr.model import NotPlaced, Unplaced

    left = NotPlaced("task", "t1", Unplaced.OTHER_SESSION_HAS_ID, holder="s\n9" + "x" * 90)

    assert board_pane.unplaced_reason(left) == Unplaced.OTHER_SESSION_HAS_ID.value
    assert board_pane.unplaced_reason(left, lambda sid: False) == left.why.value
    removed = board_pane.unplaced_reason(left, lambda sid: sid == left.holder)
    # A session id is a string from a file: one line, and bounded.
    assert removed.startswith("held by a session you removed from the fleet (s 9xxx")
    assert "\n" not in removed and len(removed) < 120
    # Only this reason has a holder to be removed.
    other = NotPlaced("task", "t1", Unplaced.INCOHERENT, holder="s9")
    assert board_pane.unplaced_reason(other, lambda sid: True) == Unplaced.INCOHERENT.value


# -- one board under either renderer --------------------------------------------


def _a_board_with_everything_a_row_says() -> Snapshot:
    """
    A finished task with a property, a result, a not-run list and a specification
    of stated bases, an open one beside it, and an incident filed against the
    first: every line ``_task_row`` draws that only a finished or noted row has.
    """
    done = Task(
        id="t1",
        title="do t1",
        detail="the specification",
        state=TaskState.COMPLETED,
        claimed_by=QA,
        declared_by=ROOT,
        basis=Basis.RELAYED,
        property="never drop a row",
        result="what was reported",
        result_basis=Basis.RUN,
        not_run="the probe",
    )
    still = Task(id="t2", title="do t2", detail="more", declared_by=ROOT)
    note = Concern(
        id="c1",
        sender=QA,
        recipient=ROOT,
        subject="wrote outside touches",
        body="store.py",
        posted_at=1.0,
        task_id="t1",
        basis=Basis.READ,
        incident=True,
    )
    return board_snapshot(done, still, concerns=(note,))


@pytest.mark.parametrize("set_by_library", [False, True], ids=["native", "typeset"])
def test_a_row_says_the_same_things_and_offers_the_same_controls_under_either_renderer(
    frames: Frames, monkeypatch: pytest.MonkeyPatch, set_by_library: bool
) -> None:
    """
    The typeset branch is drawn wherever the library can draw, so a line or a
    control only the native row has is one the operator loses for the library
    being there: a result, what was not run, the basis of each text, an incident,
    and closing a task.
    """
    typeset.configure(typeset=set_by_library, mono=False)
    assert typeset.enabled(typeset.Pane.BOARD) is set_by_library
    buttons: list[str] = []
    small_button = widgets.small_button

    def pressed(label: str) -> bool:
        buttons.append(label)
        return bool(small_button(label))

    monkeypatch.setattr(widgets, "small_button", pressed)
    snap = _a_board_with_everything_a_row_says()
    done, still = board_tasks(snap, "s1")

    closed = frames.draw(snap)
    assert board_pane.property_label("never drop a row") in closed
    assert board_pane.standing_label(done) in closed

    del buttons[:]
    opened = frames.draw(snap, "t1", "t2", "concern-c1")
    heading = board_pane.result_heading(done)
    assert heading.endswith("(says: run):")
    for line in (
        board_pane.property_label("never drop a row"),
        "reason (says: read): INCIDENT wrote outside touches",
        heading,
        board_pane.unrun_heading(done),
        "specification (says: relayed):",
        "INCIDENT",
    ):
        assert line in opened, line
    assert board_pane.standing_label(done) not in opened
    bodies = ("what was reported", "the probe", "the specification", "store.py")
    if set_by_library:
        shown = {frames.shown(key) for key in frames.pane.views.keys()}
        assert set(bodies) <= shown, shown
    else:
        assert set(bodies) <= set(opened)
    assert f"amend##{done.id}" in buttons and f"amend##{still.id}" in buttons
    # Closing is offered on the row that is not finished, and only there.
    assert f"{board_pane.CLOSE}##close-{still.id}" in buttons
    assert f"{board_pane.CLOSE}##close-{done.id}" not in buttons


# -- the header keeps only alarms (U8) ------------------------------------------------


def _header_and_body(
    monkeypatch,
    snap: Snapshot,
    logging: board_pane.Logging | None = None,
    pane: board_pane.BoardState | None = None,
    pressed: Collection[str] = (),
) -> tuple[list[str], list[str]]:
    """
    What the pane drew above its scroll and what it drew inside it, split at the
    ``##board`` child: text and tree-node labels, in order.
    """
    fake = MagicMock()
    fake.tree_node_ex.return_value = False
    fake.small_button.side_effect = lambda label, *a, **k: label in pressed
    _fake_imgui(monkeypatch, fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    board_pane.draw(
        snap, at_root(snap), board_pane.BoardState() if pane is None else pane, Bridge(), logging
    )
    header: list[str] = []
    body: list[str] = []
    into = header
    for name, args, _ in fake.mock_calls:
        if name == "begin_child" and args and args[0] == "##board":
            into = body
        elif name == "text_colored":
            into.append(str(args[1]))
        elif name == "tree_node_ex":
            into.append(str(args[0]))
    return header, body


def _restored(**fields: object) -> Snapshot:
    return dataclasses.replace(
        board_snapshot(_held()), restores=MappingProxyType({"s1": _restore(**fields)})
    )


def test_an_alarm_about_the_log_stays_above_the_scroll(monkeypatch) -> None:
    """
    The property the task must not break: a line saying the board shown is not
    the whole board is drawn before the rows' child window begins.
    """
    snap = _restored(log_short=True, log_stray=1)
    logging = board_pane.Logging(sessions={"s1": _log(unkept=2)})

    header, body = _header_and_body(monkeypatch, snap, logging)

    assert any("restored SHORT" in line for line in header)
    assert any("will not be read back" in line for line in header)
    assert not any("restored SHORT" in line or "read back" in line for line in body)


@pytest.mark.parametrize(
    "logging",
    [
        board_pane.Logging(sessions={"s1": _log()}),
        board_pane.Logging(off="switched off: board_log in settings.json"),
    ],
)
def test_a_routine_account_of_the_log_is_not_drawn_on_the_board(
    monkeypatch, logging: board_pane.Logging
) -> None:
    header, body = _header_and_body(monkeypatch, _restored(), logging)
    routine = board_pane.log_lines(_restored(), logging, "s1")

    assert routine and all(line.tone is board_pane.Tone.PROSE for line in routine)
    for line in routine:
        assert line.text not in header + body


def test_the_lines_about_the_log_are_one_derivation_for_both_surfaces() -> None:
    """
    ``log_lines`` is what the header filters and what Details reads through the same
    two functions: each line is theirs, word for word, routine and alarm alike.
    """
    snap = _restored(log_short=True)
    logging = board_pane.Logging(sessions={"s1": _log()})
    lines = board_pane.log_lines(snap, logging, "s1")

    assert lines == (
        board_pane.restore_line(snap.restores["s1"], _log(), snap.off_fleet),
        board_pane.recording_line(None, _log()),
    )
    assert [line.tone for line in lines] == [board_pane.Tone.ALARM, board_pane.Tone.PROSE]
    assert board_pane.log_lines(board_snapshot(_held()), None, "s1") == ()


# -- the state filter (U8) ------------------------------------------------------------


def test_each_row_lands_in_the_kind_its_state_and_its_wait_say() -> None:
    kind = board_pane.row_kind
    assert kind(board_task()) is board_pane.RowKind.OPEN
    assert kind(board_task(blocked_on=("t0",))) is board_pane.RowKind.WAITING
    assert kind(board_task(state=TaskState.CLAIMED, owner="qa")) is board_pane.RowKind.HELD
    assert kind(board_task(state=TaskState.COMPLETED)) is board_pane.RowKind.DONE


def test_a_row_open_on_the_board_is_one_the_store_would_hand_out() -> None:
    """
    OPEN is ``Task.is_claimable`` read through the projection, not a second rule:
    for every row of a board, the two agree.
    """
    snap = _filter_board()
    for row in board_tasks(snap, "s1"):
        claimable = snap.tasks[row.id].is_claimable(snap.tasks)
        assert (board_pane.row_kind(row) is board_pane.RowKind.OPEN) is claimable, row.id


def _filter_board() -> Snapshot:
    return board_snapshot(
        Task(id="t-open", title="open title", declared_at=1.0, declared_by=ROOT),
        Task(
            id="t-wait",
            title="wait title",
            depends_on=("t-held",),
            declared_at=2.0,
            declared_by=ROOT,
        ),
        Task(
            id="t-held",
            title="held title",
            state=TaskState.CLAIMED,
            claimed_by=ROOT,
            declared_at=3.0,
            declared_by=ROOT,
        ),
        Task(
            id="t-done",
            title="done title",
            state=TaskState.COMPLETED,
            claimed_by=ROOT,
            declared_at=4.0,
            declared_by=ROOT,
        ),
    )


def _ids(drawn_lines: list[str]) -> set[str]:
    return {line.split("###")[-1] for line in drawn_lines if "###" in line}


def test_with_nothing_hidden_every_row_is_drawn_and_no_count_is_said(monkeypatch) -> None:
    header, body = _header_and_body(monkeypatch, _filter_board())

    assert _ids(body) == {"t-open", "t-wait", "t-held", "t-done"}
    assert not any(line.startswith("showing ") for line in header)


def test_a_hidden_kind_leaves_the_rows_and_the_summary_still_counts_them(monkeypatch) -> None:
    snap = _filter_board()
    pane = board_pane.BoardState(hidden={board_pane.RowKind.DONE, board_pane.RowKind.WAITING})

    header, body = _header_and_body(monkeypatch, snap, pane=pane)

    assert _ids(body) == {"t-open", "t-held"}
    whole = board_pane.summary_label(board_pane.counts(board_tasks(snap, "s1")))
    assert whole == "4 task(s) · 2 pending · 1 claimed · 1 done"
    assert whole in header
    assert "showing 2 of 4" in header


def test_pressing_a_chip_hides_its_kind_from_the_next_frame(monkeypatch) -> None:
    snap = _filter_board()
    pane = board_pane.BoardState()

    _, pressed_on = _header_and_body(monkeypatch, snap, pane=pane, pressed={"done##board-filter"})
    assert pane.hidden == {board_pane.RowKind.DONE}
    assert "t-done" in _ids(pressed_on), "the frame draws the rows it began with"

    _, after = _header_and_body(monkeypatch, snap, pane=pane)
    assert _ids(after) == {"t-open", "t-wait", "t-held"}

    _header_and_body(monkeypatch, snap, pane=pane, pressed={"done##board-filter"})
    assert pane.hidden == set(), "a second press shows it again"


def test_the_filter_hides_no_concern(monkeypatch) -> None:
    snap = board_snapshot(
        Task(id="t1", title="x", state=TaskState.COMPLETED, declared_by=ROOT),
        concerns=(concern("c1", subject="about the done one", task_id="t1"),),
    )
    pane = board_pane.BoardState(hidden=set(board_pane.RowKind))

    _, body = _header_and_body(monkeypatch, snap, pane=pane)

    assert _ids(body) == set()
    assert any("->" in line for line in body)


def test_the_typeset_board_honours_the_filter_and_counts_the_whole_board(
    frames: Frames,
) -> None:
    snap = _filter_board()
    frames.pane.hidden = {board_pane.RowKind.HELD}

    strings = frames.draw(snap)

    assert "held title" not in strings
    assert {"open title", "wait title", "done title"} <= set(strings)
    assert "4 task(s) · 2 pending · 1 claimed · 1 done" in strings
    assert "showing 3 of 4" in strings

    frames.pane.hidden = set()
    assert "held title" in frames.draw(snap)
