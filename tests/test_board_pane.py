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

import pytest
from imgui_bundle import icons_fontawesome_6 as fa

from pptmstr.board import BoardConcern, BoardTask, board_concerns
from pptmstr.bridge import Bridge
from pptmstr.model import (
    AgentRecord,
    AgentState,
    Concern,
    ConcernState,
    NodeId,
    Snapshot,
    Task,
    TaskState,
)
from pptmstr.ui import board_pane, typeset
from pptmstr.ui.focus import FocusState, Scope
from pptmstr.ui.typeset import Kind, Part
from tests import test_typeset as rig

# The rig's fixtures, under the names pytest finds them by.
imgui = rig.imgui
seam = rig.seam


@pytest.fixture(autouse=True)
def old_renderer(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The seam off for every test that did not ask for it.

    The faces stay loaded from the first typeset test to the end of the module, and
    a mocked ``imgui`` has no frame for the seam to draw in.
    """
    if "seam" not in request.fixturenames:
        monkeypatch.setattr(typeset, "enabled", lambda: False)


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


def at_root(snap: Snapshot) -> FocusState:
    focus = FocusState()
    focus.to_node(snap, ROOT, scope=Scope.SESSION)
    return focus


def drawn(monkeypatch, snap: Snapshot, focus: FocusState | None = None) -> list[str]:
    """Every string the pane passed to imgui, so a bound's announcement is checkable."""
    fake = MagicMock()
    monkeypatch.setattr(board_pane, "imgui", fake)
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
    monkeypatch.setattr(board_pane, "imgui", MagicMock())
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
    assert label == "reviewer -> lead  (waiting)"


def test_a_delivered_concern_says_it_was_delivered() -> None:
    label = board_pane.concern_label(board_concern(state=ConcernState.DELIVERED))
    assert label == "reviewer -> lead  (delivered)"


def test_a_concern_the_operator_rewrote_says_so() -> None:
    """
    What the recipient was told differs from what the sender wrote, and nothing
    else in the UI records that once the approval is gone.
    """
    label = board_pane.concern_label(board_concern(state=ConcernState.DELIVERED, edited=True))
    assert label == "reviewer -> lead  (delivered, edited by you)"


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
    monkeypatch.setattr(board_pane, "imgui", fake)
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
    monkeypatch.setattr(board_pane, "imgui", fake)
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
    monkeypatch.setattr(board_pane, "imgui", fake)
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
    monkeypatch.setattr(board_pane, "imgui", fake)
    monkeypatch.setattr(board_pane, "section", MagicMock())
    monkeypatch.setattr(
        board_pane, "multiline_input", lambda *a, **k: (False, pane.drafts.get("t1", ""))
    )
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
    monkeypatch.setattr(board_pane, "imgui", fake)
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
        return rig._shown(self.pane.views.view(key))

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
    glyphs, _ = frames.pane.views.drawn(("s1", "spec", "t1")) or (0, 0)
    assert glyphs > 20


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
    assert "reason: checksum ignored" in strings
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
    assert f"{frames.people(snap)} · delivered, edited by you · about t1" in opened


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
    built: list[object] = []
    build_piece = typeset._build_piece
    monkeypatch.setattr(typeset, "_build_piece", lambda p: built.append(p) or build_piece(p))

    assert "do t1" not in frames.draw(snap, "t1", focus=at(snap, "s2"))
    assert frames.pane.views.view(("s1", "spec", "t1")) is view
    frames.draw(snap, "t1", focus=FocusState())
    assert "do t1" in frames.draw(snap, "t1")

    assert frames.pane.views.view(("s1", "spec", "t1")) is view
    assert frames.pane.parts[("s1", "spec", "t1")][1] is parts
    assert built == []
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

    assert f"{frames.people(snap)} · waiting" in opened


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
