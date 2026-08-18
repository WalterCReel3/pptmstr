"""
BOARD: the pane, its bounds, and the labels it derives.

Most of this moved out of ``test_detail.py`` with the pane itself. What is new is
everything the row gained when it stopped sharing a surface -- the specification,
the file list, and the reason a stalled row is not moving -- plus the summary line,
which exists so a sixty-row board can be read without being read.

Drawing is exercised with ``imgui`` replaced by a ``MagicMock``: it cannot say what
anything looked like, but it can say what was drawn, in what order, and whether a
bound announced itself. The parts that need pixels are noted where they arise.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from unittest.mock import MagicMock

from pptmstr.board import BoardConcern, BoardTask
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
from pptmstr.ui import board_pane
from pptmstr.ui.focus import FocusState, Scope

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


def test_a_rows_reason_is_the_subject_of_the_concern_that_explains_it() -> None:
    row = board_task(concerns=("c1",))
    by_id = {"c1": board_concern("c1", subject="waiting on the schema decision")}
    assert board_pane.row_reasons(row, by_id) == ("waiting on the schema decision",)


def test_a_reason_with_no_subject_is_named_rather_than_blank() -> None:
    row = board_task(concerns=("c1",))
    assert board_pane.row_reasons(row, {"c1": board_concern("c1", subject="")}) == ("(no subject)",)


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
