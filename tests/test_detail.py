"""
The pure decisions behind the DETAIL pane.

Only the parts checkable without a GL context: how an argument value is turned
into text, that a bound reports what it dropped instead of swallowing it, and that
the clipboard rendering carries the whole call rather than the row's clipped
summary. The drawing itself needs pixels.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from unittest.mock import MagicMock

from pptmstr.approval import summarize
from pptmstr.board import BoardConcern, BoardTask
from pptmstr.model import (
    AgentRecord,
    AgentState,
    ApprovalNeeded,
    Concern,
    ConcernState,
    NodeId,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
    Task,
    TaskState,
)
from pptmstr.transcript import SegmentKind
from pptmstr.ui import detail, inbox, review
from pptmstr.ui.blocks import BlockKind
from pptmstr.ui.focus import FocusState, Scope

ROOT: NodeId = ("s1", None)


def record(
    node: NodeId = ROOT,
    *,
    task: str = "audit the TLE parser",
    parent: NodeId | None = None,
    state: AgentState = AgentState.AWAITING_APPROVAL,
) -> AgentRecord:
    return AgentRecord(
        node_id=node,
        parent=parent,
        depth=0 if parent is None else 1,
        state=state,
        topic="working",
        task=task,
        model="claude-sonnet-5",
        cwd="/x/orbital",
    )


def snapshot(*records: AgentRecord, needs_you: tuple[object, ...] = ()) -> Snapshot:
    return Snapshot(
        seq=1,
        nodes=MappingProxyType({r.node_id: r for r in records}),
        order=tuple(r.node_id for r in records),
        needs_you=needs_you,  # type: ignore[arg-type]
        any_active=False,
    )


def approval(args: dict[str, object], *, diff: str | None = None) -> ApprovalNeeded:
    tool = "Bash" if "command" in args else "Write"
    return ApprovalNeeded(
        node=ROOT,
        since=1.0,
        summary=summarize(tool, args),
        approval=PendingApproval(
            id="p1",
            node=ROOT,
            tool_name=tool,
            tool_use_id="tu-p1",
            raw_args=args,
            summary=summarize(tool, args),
            requested_at=1.0,
            diff=diff,
        ),
    )


# -- value rendering -----------------------------------------------------------


def test_a_string_argument_keeps_its_newlines() -> None:
    """
    repr would render a 200-line Write as one line of backslash-n, which is the
    loss this pane exists to undo, reintroduced by the formatting.
    """
    assert detail.render_value("a\nb") == "a\nb"


def test_a_non_string_argument_keeps_its_quoting() -> None:
    """A nested dict of edits and the bare word None must not look alike."""
    assert detail.render_value(None) == "None"
    assert detail.render_value(["x"]) == "['x']"


# -- bounds --------------------------------------------------------------------


def test_a_bound_reports_what_it_dropped() -> None:
    text, omitted = detail.clip("abcdef", 4)
    assert text == "abcd"
    assert omitted == 2


def test_a_string_within_the_bound_is_untouched() -> None:
    assert detail.clip("abc", 4) == ("abc", 0)


# -- the clipboard rendering ---------------------------------------------------


def test_the_full_call_survives_a_summary_that_did_not() -> None:
    """
    The reason this pane is not just a wider inbox row: ``summarize`` clips to 90
    characters *before the store sees the string*, so for a Bash call no window
    width recovers the command. It has to be re-read from raw_args.
    """
    command = (
        "for f in $(git ls-files '*.py'); do python -m compileall -q \"$f\"; done  " + "# " * 40
    )
    obligation = approval({"command": command})

    assert obligation.summary.endswith("...")
    assert command not in obligation.summary

    text = detail.plain_text(snapshot(record(), needs_you=(obligation,)), obligation)
    assert command in text


def test_the_clipboard_carries_identity_and_diff() -> None:
    obligation = approval({"file_path": "/tmp/x"}, diff="--- a/x\n+++ b/x\n+hello\n")
    text = detail.plain_text(snapshot(record(), needs_you=(obligation,)), obligation)
    assert "audit the TLE parser" in text
    assert "orbital" in text
    assert "+hello" in text


def test_a_question_renders_the_turn_not_the_row_summary() -> None:
    node = record()
    node.transcript.append(SegmentKind.OUTPUT, "here is what I found, at length")
    obligation = QuestionPending(node=ROOT, since=1.0, summary="ended its turn")
    text = detail.plain_text(snapshot(node, needs_you=(obligation,)), obligation)
    assert "here is what I found, at length" in text


def test_a_question_renders_the_prose_not_the_machinery() -> None:
    """
    "What it said" used to be a kind-blind byte tail, so a question sitting behind
    a large tool result showed the tool result under a heading promising the
    agent's own words. See planning/2026-08-11-what-it-said-is-a-byte-tail.md.
    """
    node = record()
    node.transcript.append(SegmentKind.SYSTEM, "\n> audit the TLE parser\n")
    node.transcript.append(SegmentKind.REASONING, "they probably mean the checksum\n")
    node.transcript.append(SegmentKind.TOOL_CALL, "Grep(pattern=checksum)\n")
    node.transcript.append(SegmentKind.TOOL_RESULT, "Grep -> " + "hit\n" * 400)
    node.transcript.append(SegmentKind.OUTPUT, "Fix the parser, or only report it?")

    obligation = QuestionPending(node=ROOT, since=1.0, summary="ended its turn")
    text = detail.plain_text(snapshot(node, needs_you=(obligation,)), obligation)

    assert "Fix the parser, or only report it?" in text
    assert "hit" not in text
    assert "Grep(" not in text
    assert "they probably mean the checksum" not in text


def test_a_question_shows_only_the_current_turn() -> None:
    node = record()
    node.transcript.append(SegmentKind.SYSTEM, "\n> first task\n")
    node.transcript.append(SegmentKind.OUTPUT, "answered the first one")
    node.transcript.append(SegmentKind.SYSTEM, "\n> now the other one\n")
    node.transcript.append(SegmentKind.OUTPUT, "and here is the second answer")

    obligation = QuestionPending(node=ROOT, since=1.0, summary="ended its turn")
    text = detail.plain_text(snapshot(node, needs_you=(obligation,)), obligation)

    assert "and here is the second answer" in text
    assert "answered the first one" not in text


def test_a_failure_renders_its_error() -> None:
    obligation = SessionFailed(node=ROOT, since=1.0, summary="died", error="Traceback: boom")
    text = detail.plain_text(snapshot(record(), needs_you=(obligation,)), obligation)
    assert "Traceback: boom" in text


def test_every_obligation_kind_has_a_label() -> None:
    """
    A pane that goes blank on two of the three kinds is defect 1 in miniature --
    the habit of treating an approval as the only kind of obligation.
    """
    from pptmstr.model import ObligationKind

    assert set(detail._KIND_LABEL) == set(ObligationKind)


# -- turn prose as blocks -------------------------------------------------------


def test_prose_blocks_are_the_markdown_structure_of_the_turn() -> None:
    node = record()
    node.transcript.append(SegmentKind.SYSTEM, "\n> audit the TLE parser\n")
    node.transcript.append(
        SegmentKind.OUTPUT,
        "## What I found\n\nThe checksum is never validated.\n\n- line 40\n",
    )
    pane = detail.DetailState()

    blocks = pane.prose_blocks(ROOT, node.transcript)

    assert [b.kind for b in blocks] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.BULLET_ITEM,
    ]


def test_prose_blocks_include_the_final_unterminated_paragraph() -> None:
    """
    The question is the last thing in the turn and has no trailing blank line, so
    without ``BlockCursor.finish`` it is the one block that never arrives.
    """
    node = record()
    node.transcript.append(SegmentKind.OUTPUT, "Checked it.\n\nFix it, or only report it?")
    pane = detail.DetailState()

    blocks = pane.prose_blocks(ROOT, node.transcript)

    assert blocks[-1].lines == ("Fix it, or only report it?",)


def test_prose_blocks_exclude_the_machinery() -> None:
    node = record()
    node.transcript.append(SegmentKind.SYSTEM, "\n> audit the TLE parser\n")
    node.transcript.append(SegmentKind.REASONING, "they probably mean the checksum\n")
    node.transcript.append(SegmentKind.TOOL_RESULT, "Grep -> " + "hit\n" * 400)
    node.transcript.append(SegmentKind.OUTPUT, "Fix the parser, or only report it?")
    pane = detail.DetailState()

    blocks = pane.prose_blocks(ROOT, node.transcript)

    assert [b.segment_kind for b in blocks] == [SegmentKind.OUTPUT]
    assert blocks[0].lines == ("Fix the parser, or only report it?",)


def test_prose_blocks_are_parsed_once_while_the_turn_is_settled() -> None:
    """A finished turn stops moving, so a pane left open on it must not re-parse
    every frame -- the memo returns the same tuple, not an equal one."""
    node = record()
    node.transcript.append(SegmentKind.OUTPUT, "Fix it, or only report it?")
    pane = detail.DetailState()

    first = pane.prose_blocks(ROOT, node.transcript)
    second = pane.prose_blocks(ROOT, node.transcript)

    assert first is second


def test_prose_blocks_reparse_when_the_transcript_grows() -> None:
    node = record()
    node.transcript.append(SegmentKind.OUTPUT, "Working on it.\n\n")
    pane = detail.DetailState()
    before = pane.prose_blocks(ROOT, node.transcript)

    node.transcript.append(SegmentKind.OUTPUT, "Fix it, or only report it?")
    after = pane.prose_blocks(ROOT, node.transcript)

    assert after is not before
    assert after[-1].lines == ("Fix it, or only report it?",)


def test_prose_blocks_reparse_when_the_cursor_moves_to_another_node() -> None:
    """The memo is one entry keyed by node as well as length: two nodes whose
    transcripts happen to be the same length must not share a parse."""
    other: NodeId = ("s2", None)
    first_node = record()
    first_node.transcript.append(SegmentKind.OUTPUT, "aaaa")
    second_node = record(other)
    second_node.transcript.append(SegmentKind.OUTPUT, "bbbb")
    pane = detail.DetailState()

    pane.prose_blocks(ROOT, first_node.transcript)
    blocks = pane.prose_blocks(other, second_node.transcript)

    assert blocks[0].lines == ("bbbb",)


def test_prose_blocks_are_empty_when_the_turn_said_nothing() -> None:
    """The empty case has to stay distinguishable, since it is what selects the
    'no output on this turn' placeholder over the renderer."""
    node = record()
    node.transcript.append(SegmentKind.SYSTEM, "\n> go\n")
    node.transcript.append(SegmentKind.TOOL_CALL, "Grep(pattern=checksum)\n")
    pane = detail.DetailState()

    assert pane.prose_blocks(ROOT, node.transcript) == ()


# -- narration bounding ---------------------------------------------------------


def test_narration_tail_keeps_the_end_not_the_start() -> None:
    """
    Head-anchored is right for ``clip``; narration is the opposite case. A turn in
    progress is watched, and what it is saying now is at the bottom.
    """
    prose = "\n".join(str(i) for i in range(10))
    text, dropped = detail.narration_tail(prose, 3)
    assert text == "7\n8\n9"
    assert dropped == 7


def test_narration_tail_under_the_limit_is_unchanged() -> None:
    text, dropped = detail.narration_tail("a\nb", 10)
    assert (text, dropped) == ("a\nb", 0)


def test_narration_tail_at_exactly_the_limit_drops_nothing() -> None:
    text, dropped = detail.narration_tail("a\nb\nc", 3)
    assert (text, dropped) == ("a\nb\nc", 0)


def test_narration_tail_reports_what_it_dropped() -> None:
    """The count is what the pane renders; a silent tail in a surface whose premise
    is 'nothing is lost' is the thing this pane exists to not do."""
    prose = "\n".join(str(i) for i in range(500))
    _, dropped = detail.narration_tail(prose, detail._NARRATION_LINES)
    assert dropped == 500 - detail._NARRATION_LINES


def test_narration_tail_on_empty_prose() -> None:
    assert detail.narration_tail("", 10) == ("", 0)


# -- the session board ------------------------------------------------------------


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


def test_an_unclaimed_task_names_no_owner() -> None:
    assert detail.owner_label(board_task()) == ""


def test_a_claimed_task_names_its_role() -> None:
    assert detail.owner_label(board_task(owner="builder")) == "[builder]"


def test_an_owner_that_stopped_is_not_shown_as_an_ordinary_owner() -> None:
    """
    Nothing releases a task when its worker dies, so the row stays CLAIMED
    forever. Rendering it as a plain owner reports progress that has stopped.
    """
    row = board_task(state=TaskState.CLAIMED, owner="builder", owner_gone=True)
    assert detail.owner_label(row) == "[builder, stopped]"


def test_a_task_blocked_on_nothing_says_nothing() -> None:
    assert detail.blocked_label(board_task()) == ""


def test_a_blocked_task_names_what_it_waits_on() -> None:
    assert detail.blocked_label(board_task(blocked_on=("t1", "t2"))) == "blocked on t1, t2"


def test_a_dependency_that_was_never_declared_is_marked_not_just_listed() -> None:
    """
    `declare_task` accepts a depends_on naming an id that does not exist and
    answers "on the board"; `is_claimable` treats it as unsatisfied, so the task
    is unclaimable forever. This cell is the operator's only sight of it, and
    "wait" and "go fix the typo" are different actions.
    """
    row = board_task(blocked_on=("t1", "t9"), missing=("t9",))
    assert detail.blocked_label(row) == "blocked on t1, t9 (never declared)"


def test_the_blocked_cell_is_bounded_and_says_so() -> None:
    deps = tuple(f"t{i}" for i in range(20))
    label = detail.blocked_label(board_task(blocked_on=deps), limit=3)
    assert label == "blocked on t0, t1, t2, and 17 more"


def test_the_blocked_cell_at_exactly_the_limit_announces_nothing() -> None:
    label = detail.blocked_label(board_task(blocked_on=("t0", "t1", "t2")), limit=3)
    assert label == "blocked on t0, t1, t2"


def test_a_waiting_concern_reads_as_waiting() -> None:
    label = detail.concern_label(board_concern())
    assert label == "reviewer -> lead  (waiting)"


def test_a_delivered_concern_says_it_was_delivered() -> None:
    label = detail.concern_label(board_concern(state=ConcernState.DELIVERED))
    assert label == "reviewer -> lead  (delivered)"


def test_a_concern_the_operator_rewrote_says_so() -> None:
    """
    The audit trail is the whole reason concerns are store objects. Once the
    approval is gone, nothing else in the UI records that what the recipient was
    told differs from what the sender wrote.
    """
    label = detail.concern_label(board_concern(state=ConcernState.DELIVERED, edited=True))
    assert label == "reviewer -> lead  (delivered, edited by you)"


def test_every_concern_state_has_a_label() -> None:
    """A new ConcernState member must not render as a KeyError mid-frame."""
    for state in ConcernState:
        assert detail.concern_label(board_concern(state=state))


def test_rows_within_the_bound_are_untouched() -> None:
    rows = (1, 2, 3)
    assert detail.bound_rows(rows, 10) == (rows, 0)
    assert detail.bound_rows(rows, 3) == (rows, 0)


def test_tasks_keep_the_head_because_dependencies_point_backwards() -> None:
    kept, dropped = detail.bound_rows(tuple(range(10)), 3)
    assert (kept, dropped) == ((0, 1, 2), 7)


def test_concerns_keep_the_tail_because_a_conversation_is_watched_at_its_end() -> None:
    kept, dropped = detail.bound_rows(tuple(range(10)), 3, tail=True)
    assert (kept, dropped) == ((7, 8, 9), 7)


def test_the_board_bounds_are_real_numbers_not_placeholders() -> None:
    # Pins the bound to something the pane actually enforces; a board of 300 tasks
    # must not silently show 60.
    assert detail.bound_rows(tuple(range(300)), detail._MAX_BOARD_TASKS)[1] == 300 - 60
    assert detail.bound_rows(tuple(range(300)), detail._MAX_BOARD_CONCERNS)[1] == 300 - 40


# -- the spawn count in the header ------------------------------------------------
#
# The inbox row and this pane must not be able to disagree about how many sub-agents
# a yes would make, so both read `inbox.spawn_marker`. What is worth pinning here is
# that this pane reads it at all -- a second implementation, or none, is the defect.


def spawn_approval(pid: str = "p1", *, at: float = 1.0) -> ApprovalNeeded:
    args: dict[str, object] = {"subagent_type": "builder", "description": "write the parser"}
    return ApprovalNeeded(
        node=ROOT,
        since=at,
        summary=summarize("Agent", args),
        approval=PendingApproval(
            id=pid,
            node=ROOT,
            tool_name="Agent",
            tool_use_id=f"tu-{pid}",
            raw_args=args,
            summary=summarize("Agent", args),
            requested_at=at,
        ),
    )


def _header_text(monkeypatch, snap: Snapshot, obligation: object) -> list[str]:
    """Every string ``_header`` drew, with the pixels stubbed out."""
    fake = MagicMock()
    monkeypatch.setattr(detail, "imgui", fake)
    detail._header(snap, obligation, 10.0)  # type: ignore[arg-type]
    return [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]


def test_the_header_carries_the_same_count_the_row_does(monkeypatch) -> None:
    parked = spawn_approval("p2", at=2.0)
    snap = snapshot(
        record(),
        record(("s1", "a"), parent=ROOT, state=AgentState.THINKING),
        needs_you=(parked,),
    )
    assert inbox.spawn_marker(snap, parked) == "2nd sub-agent · 1 running"
    assert "2nd sub-agent · 1 running" in _header_text(monkeypatch, snap, parked)

    # And it is that function the header called, not a second copy of the rule that
    # happens to agree here. Asserting the string alone cannot tell the two apart:
    # an inline duplicate that counted fleet-wide, or dropped the queued-ahead term,
    # renders the same text in a one-session scenario with one settled spawn -- and
    # a different one in every case the ordinal was derived the hard way for. A
    # sentinel nothing else could produce is what makes the claim checkable.
    monkeypatch.setattr(inbox, "spawn_marker", lambda _snap, _obligation: "17th sub-agent · 4 rng")
    assert "17th sub-agent · 4 rng" in _header_text(monkeypatch, snap, parked)


def test_a_non_spawn_header_says_nothing_about_sub_agents(monkeypatch) -> None:
    obligation = approval({"command": "rm -rf /tmp/x"})
    snap = snapshot(record(), needs_you=(obligation,))
    assert not any("sub-agent" in line for line in _header_text(monkeypatch, snap, obligation))


# -- the board is wired into both exits of draw -----------------------------------
#
# `draw` returns early when nothing is under the cursor, so a board rendered in one
# branch only is invisible in exactly half the states -- and the half it would be
# missing from is the one the whole feature is for: approving a message between two
# agents without being able to see the work either of them holds. The unit tests
# above all pass with `_board` called from nowhere at all.


def _drive_draw(monkeypatch, snap: Snapshot, focus_state: FocusState) -> list[str]:
    """Call draw with the pixels stubbed out, recording the sessions _board got."""
    seen: list[str] = []

    monkeypatch.setattr(detail, "imgui", MagicMock())
    monkeypatch.setattr(detail, "_board", lambda _snap, session: seen.append(session))
    # Everything else in the two branches draws; none of it is under test here.
    for name in ("_header", "_approval", "_question", "_failure", "_narration", "_section"):
        monkeypatch.setattr(detail, name, MagicMock())

    detail.draw(snap, focus_state, review.ReviewState(), detail.DetailState(), 10.0)
    return seen


def test_the_board_is_drawn_when_an_obligation_is_selected(monkeypatch) -> None:
    """The case the planning doc opens with: the operator is deciding on a message."""
    owed = approval({"command": "rm -rf /tmp/x"})
    snap = snapshot(record(), needs_you=(owed,))
    focus_state = FocusState()
    focus_state.to_obligation(owed)

    assert _drive_draw(monkeypatch, snap, focus_state) == [ROOT[0]]


def test_the_board_is_drawn_when_nothing_is_selected(monkeypatch) -> None:
    """Or an idle team's board would be invisible."""
    snap = snapshot(record())
    focus_state = FocusState()
    focus_state.to_node(snap, ROOT, scope=Scope.SESSION)

    assert _drive_draw(monkeypatch, snap, focus_state) == [ROOT[0]]


def test_no_board_is_drawn_when_the_cursor_resolves_to_nothing(monkeypatch) -> None:
    """An empty snapshot has no session to scope a board to, and must not invent one."""
    assert _drive_draw(monkeypatch, Snapshot.empty(), FocusState()) == []


def _board_sections(monkeypatch, snap: Snapshot, session: str) -> list[str]:
    """The headings _board emitted, with the pixels stubbed out."""
    seen: list[str] = []
    monkeypatch.setattr(detail, "imgui", MagicMock())
    monkeypatch.setattr(detail, "_section", lambda label: seen.append(label))
    monkeypatch.setattr(detail, "_task_row", MagicMock())
    detail._board(snap, session)
    return seen


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


def test_a_solo_session_grows_no_headings(monkeypatch) -> None:
    """
    Absent, not empty. Most sessions are solo and would otherwise carry two
    permanently empty headings forever.
    """
    snap = board_snapshot(template="solo")
    assert _board_sections(monkeypatch, snap, "s1") == []


def test_a_session_with_no_template_at_all_grows_no_headings(monkeypatch) -> None:
    """Records predating the field, and the fake driver's sub-agents."""
    assert _board_sections(monkeypatch, board_snapshot(template=None), "s1") == []


def test_a_team_with_an_empty_board_still_gets_the_heading(monkeypatch) -> None:
    """
    The reason the template is stored rather than the board's emptiness derived:
    a lead that has not declared a task yet and a lead that never will are
    different states, and only one of them is worth waiting on.
    """
    assert _board_sections(monkeypatch, board_snapshot(), "s1") == ["board"]


def test_an_empty_team_board_says_so_rather_than_showing_nothing(monkeypatch) -> None:
    lines = _board_text(monkeypatch, board_snapshot(), "s1")
    assert any("no tasks declared yet" in line for line in lines)


def test_a_session_with_tasks_gets_a_board_heading(monkeypatch) -> None:
    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=ROOT))
    assert _board_sections(monkeypatch, snap, "s1") == ["board"]


def test_another_sessions_tasks_are_not_listed(monkeypatch) -> None:
    """The scoping decision, at the level the operator sees it."""
    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=("s2", None)))
    lines = _board_text(monkeypatch, snap, "s1")

    assert not any("t1" in line for line in lines)
    assert any("no tasks declared yet" in line for line in lines)


def test_a_team_with_only_concerns_gets_both_headings(monkeypatch) -> None:
    posted = Concern(
        id="c1",
        sender=("s1", "agent-qa"),
        recipient=ROOT,
        subject="the retry loop never terminates",
        body="...",
        posted_at=1.0,
    )
    snap = board_snapshot(concerns=(posted,))
    assert _board_sections(monkeypatch, snap, "s1") == ["board", "concerns"]


def test_a_team_with_no_concerns_grows_no_concern_heading(monkeypatch) -> None:
    snap = board_snapshot(Task(id="t1", title="do t1", declared_by=ROOT))
    assert "concerns" not in _board_sections(monkeypatch, snap, "s1")


def _board_text(monkeypatch, snap: Snapshot, session: str) -> list[str]:
    """Every string _board passed to imgui, so a bound's announcement is checkable."""
    fake = MagicMock()
    monkeypatch.setattr(detail, "imgui", fake)
    monkeypatch.setattr(detail, "_section", MagicMock())
    detail._board(snap, session)
    return [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]


def test_a_bounded_board_says_how_many_tasks_it_did_not_show(monkeypatch) -> None:
    """
    The rule the rest of this pane is built on: bounded by count, and says so when
    it bites. A board of 300 tasks must not silently show 60.
    """
    tasks = [
        Task(id=f"t{i}", title=f"do t{i}", declared_at=float(i), declared_by=ROOT)
        for i in range(300)
    ]
    lines = _board_text(monkeypatch, board_snapshot(*tasks), "s1")

    dropped = 300 - detail._MAX_BOARD_TASKS
    assert any(f"{dropped} more task(s) not shown" in line for line in lines)


def test_a_board_within_its_bound_announces_nothing(monkeypatch) -> None:
    tasks = [Task(id="t1", title="do t1", declared_by=ROOT)]
    lines = _board_text(monkeypatch, board_snapshot(*tasks), "s1")

    assert not any("not shown" in line for line in lines)


def test_a_bounded_concern_log_keeps_the_newest_not_the_oldest(monkeypatch) -> None:
    """
    A conversation is watched at its end. Head-anchoring here would drop exactly
    the messages still worth acting on and keep the ones already dealt with.
    """
    concerns = tuple(
        Concern(
            id=f"c{i}",
            sender=("s1", "agent-qa"),
            recipient=ROOT,
            subject=f"subject {i}",
            body="...",
            posted_at=float(i),
        )
        for i in range(100)
    )
    lines = _board_text(monkeypatch, board_snapshot(concerns=concerns), "s1")

    assert any("subject 99" in line for line in lines)
    assert not any("subject 0 " in line or line == "subject 0" for line in lines)
    assert any("earlier concern(s) not shown" in line for line in lines)


def test_the_board_is_drawn_before_the_narration(monkeypatch) -> None:
    """
    `_narration` opens a child window with no explicit size, so it consumes the
    whole remaining content region. Anything drawn after it is placed below the
    bottom of the pane and cannot be scrolled to -- the board rendered, in the
    right session, and was invisible. Caught by a screenshot, not by a test, so
    the order is pinned here.
    """
    order: list[str] = []
    monkeypatch.setattr(detail, "imgui", MagicMock())
    monkeypatch.setattr(detail, "_section", MagicMock())
    monkeypatch.setattr(detail, "_board", lambda *_: order.append("board"))
    monkeypatch.setattr(detail, "_narration", lambda *a, **k: order.append("narration"))

    snap = snapshot(record())
    focus_state = FocusState()
    focus_state.to_node(snap, ROOT, scope=Scope.SESSION)
    detail.draw(snap, focus_state, review.ReviewState(), detail.DetailState(), 10.0)

    assert order == ["board", "narration"]


# -- a sub-agent's deliverable ----------------------------------------------------


def _settled(text: str | None, *, state: AgentState = AgentState.DONE) -> AgentRecord:
    node: NodeId = ("s1", "agent-a")
    return dataclasses.replace(record(node), state=state, deliverable=text)


def test_a_deliverable_is_rendered_as_markdown_blocks() -> None:
    """
    The deliverable register: a settled answer read as prose, not a wrapped tail.
    Rendering it through the block parser is what makes a heading a heading.
    """
    pane = detail.DetailState()
    blocks = pane.deliverable_blocks(("s1", "agent-a"), "## What I found\n\nsixty places\n")

    assert [b.kind for b in blocks] == [BlockKind.HEADING, BlockKind.PARAGRAPH]


def test_a_deliverable_is_parsed_once_per_answer() -> None:
    pane = detail.DetailState()
    node: NodeId = ("s1", "agent-a")
    first = pane.deliverable_blocks(node, "the answer")

    assert pane.deliverable_blocks(node, "the answer") is first


def test_a_replacement_answer_of_the_same_length_is_reparsed() -> None:
    """
    A deliverable is replaced whole, so length is not identity -- unlike the
    append-only transcript the neighbouring memo keys on. A sub-agent woken by a
    sibling's message answers again, and an answer that happens to be the same
    length as the one before it must not render the one before it.
    """
    pane = detail.DetailState()
    node: NodeId = ("s1", "agent-a")
    first, second = "first pass", "later pass"
    assert len(first) == len(second)

    pane.deliverable_blocks(node, first)
    blocks = pane.deliverable_blocks(node, second)

    assert blocks[0].lines == (second,)


def test_a_longer_answer_is_reparsed_too() -> None:
    pane = detail.DetailState()
    node: NodeId = ("s1", "agent-a")
    pane.deliverable_blocks(node, "first pass")
    blocks = pane.deliverable_blocks(node, "a longer second pass")

    assert blocks[0].lines == ("a longer second pass",)


def test_two_nodes_do_not_share_one_parse() -> None:
    """Same memo shape as prose_blocks, same hazard: two answers of equal length
    for different sub-agents must not be confused for one."""
    pane = detail.DetailState()
    pane.deliverable_blocks(("s1", "agent-a"), "aaaa")
    blocks = pane.deliverable_blocks(("s1", "agent-b"), "bbbb")

    assert blocks[0].lines == ("bbbb",)


def test_the_deliverable_and_the_prose_memos_do_not_evict_each_other() -> None:
    """One slot for both would re-parse on every frame that drew a node whose
    transcript and answer are the same length."""
    pane = detail.DetailState()
    node = record(("s1", "agent-a"))
    node.transcript.append(SegmentKind.OUTPUT, "abcd")

    prose = pane.prose_blocks(node.node_id, node.transcript)
    pane.deliverable_blocks(node.node_id, "wxyz")

    assert pane.prose_blocks(node.node_id, node.transcript) is prose


def _drive_unselected(monkeypatch, rec: AgentRecord) -> list[str]:
    """Which body `_nothing_selected` drew for a node the cursor is pinned to."""
    drawn: list[str] = []

    monkeypatch.setattr(detail, "imgui", MagicMock())
    monkeypatch.setattr(detail, "_board", MagicMock())
    monkeypatch.setattr(detail, "_header", MagicMock())
    monkeypatch.setattr(detail, "_section", lambda label: drawn.append(f"section:{label}"))
    monkeypatch.setattr(detail, "_narration", lambda *a, **k: drawn.append("narration"))
    monkeypatch.setattr(detail, "_deliverable", lambda *a, **k: drawn.append("deliverable"))

    snap = snapshot(rec)
    focus_state = FocusState()
    focus_state.to_node(snap, rec.node_id, scope=Scope.AGENT)
    detail.draw(snap, focus_state, review.ReviewState(), detail.DetailState(), 10.0)
    return drawn


def test_a_finished_subagents_answer_replaces_the_narration(monkeypatch) -> None:
    """
    Both render the same words -- the sub-agent's own text reaches its transcript
    through the message stream as well -- so drawing both would print the answer
    twice, in two registers, in one pane.
    """
    assert _drive_unselected(monkeypatch, _settled("## What I found\n\nsixty places")) == [
        "section:what it delivered",
        "deliverable",
    ]


def test_a_working_subagent_still_narrates(monkeypatch) -> None:
    """A resumed sub-agent keeps the answer it gave last time. Showing it while the
    node works again would put a stale answer over live output."""
    rec = _settled("an earlier answer", state=AgentState.THINKING)

    assert _drive_unselected(monkeypatch, rec) == ["section:what it is saying", "narration"]


def test_a_node_that_delivered_nothing_narrates(monkeypatch) -> None:
    assert _drive_unselected(monkeypatch, _settled(None)) == ["section:what it said", "narration"]
