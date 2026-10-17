"""
The pure decisions behind the two new panes.

Only the parts that can be checked without a GL context: what a row calls itself,
how tall a card is, and what a badge counts. These are exactly the rules that were
got wrong once already and fixed by looking at pixels, so they are the ones worth
holding in place with something cheaper than a screenshot.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, NamedTuple

import pytest
from imgui_bundle import imgui as _real_imgui

from pptmstr.approval import summarize
from pptmstr.model import (
    AgentRecord,
    AgentState,
    ApprovalNeeded,
    NodeId,
    ObligationKind,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
)
from pptmstr.theme import DISCLOSURE_GLYPH
from pptmstr.ui import decision_view, inbox, rail, widgets

_ImVec2 = _real_imgui.ImVec2

ROOT: NodeId = ("s1", None)
CHILD: NodeId = ("s1", "agent-a")
OTHER: NodeId = ("s2", None)


def record(
    node: NodeId,
    parent: NodeId | None = None,
    *,
    task: str = "reconcile disagreeing TLE sets",
    state: AgentState = AgentState.THINKING,
    agent_type: str | None = None,
    cwd: str | None = "/x/orbital",
) -> AgentRecord:
    return AgentRecord(
        node_id=node,
        parent=parent,
        depth=0 if parent is None else 1,
        state=state,
        topic="working",
        task=task,
        model="claude-sonnet-5",
        agent_type=agent_type,
        cwd=cwd,
    )


def snapshot(*records: AgentRecord, needs_you: tuple[object, ...] = ()) -> Snapshot:
    return Snapshot(
        seq=1,
        nodes=MappingProxyType({r.node_id: r for r in records}),
        order=tuple(r.node_id for r in records),
        needs_you=needs_you,  # type: ignore[arg-type]
        any_active=True,
    )


def approval(
    node: NodeId, pid: str = "p1", *, at: float = 1.0, tool: str = "Write"
) -> ApprovalNeeded:
    return ApprovalNeeded(
        node=node,
        since=at,
        summary=f"{tool} /tmp/x",
        approval=PendingApproval(
            id=pid,
            node=node,
            tool_name=tool,
            tool_use_id=f"tu-{pid}",
            raw_args={},
            summary=f"{tool} /tmp/x",
            requested_at=at,
        ),
    )


# -- identity ------------------------------------------------------------------


def test_a_row_is_titled_by_the_session_task() -> None:
    """
    Every root is called "session". At twenty sessions the old queue read "session"
    on six rows of eight, and the fix that landed for the rail's cards did not carry
    over on its own because the inbox had only been looked at with seven.
    """
    snap = snapshot(record(ROOT), needs_you=(approval(ROOT),))
    title, qualifier = inbox.identity(snap, approval(ROOT))
    assert title == "reconcile disagreeing TLE sets"
    assert qualifier == "orbital"


def test_a_sub_agents_call_is_titled_by_its_session_and_qualified_by_itself() -> None:
    """
    The approval belongs to the sub-agent, but a sub-agent is only findable through
    the session that spawned it. Session title identifies; project / sub-agent
    qualifies.
    """
    snap = snapshot(record(ROOT), record(CHILD, ROOT, agent_type="code-reviewer"))
    title, qualifier = inbox.identity(snap, approval(CHILD))
    assert title == "reconcile disagreeing TLE sets"
    assert qualifier == "orbital / code-reviewer"


# -- the spawn count on an approval row ----------------------------------------
#
# Every spawn parks, so the operator answers each one individually. Without a
# running total twelve individually reasonable yesses add up to a fleet nobody
# chose -- and `approval.summarize` cannot supply the number, because it is pure and
# is handed a tool name and its arguments and nothing else.


def spawn(
    node: NodeId,
    pid: str = "p1",
    *,
    at: float = 1.0,
    subtype: str = "builder",
    tool: str = "Agent",
) -> ApprovalNeeded:
    """A parked spawn, summarised the way the gate really summarises one."""
    args: dict[str, Any] = {"subagent_type": subtype, "description": "write the parser"}
    return ApprovalNeeded(
        node=node,
        since=at,
        summary=summarize(tool, args),
        approval=PendingApproval(
            id=pid,
            node=node,
            tool_name=tool,
            tool_use_id=f"tu-{pid}",
            raw_args=args,
            summary=summarize(tool, args),
            requested_at=at,
        ),
    )


def test_a_first_spawn_into_an_empty_session_is_the_first() -> None:
    parked = spawn(ROOT)
    snap = snapshot(record(ROOT), needs_you=(parked,))
    assert inbox.spawn_marker(snap, parked) == "1st sub-agent · 0 running"


def test_a_spawn_into_a_session_that_already_has_sub_agents_counts_them() -> None:
    parked = spawn(ROOT, "p9", at=9.0)
    snap = snapshot(
        record(ROOT),
        record(("s1", "a"), ROOT, agent_type="builder"),
        record(("s1", "b"), ROOT, agent_type="skeptic"),
        needs_you=(parked,),
    )
    assert inbox.spawn_marker(snap, parked) == "3rd sub-agent · 2 running"


def test_the_ordinal_counts_what_the_session_has_run_and_the_live_count_does_not() -> None:
    """
    Nothing reaps a terminal sub-agent, so the fleet list only grows. The two
    numbers therefore have to be allowed to disagree: "this would be the fourth" is
    a fact about what has been consented to, "0 running" is a fact about right now.
    """
    parked = spawn(ROOT, "p9", at=9.0)
    snap = snapshot(
        record(ROOT),
        record(("s1", "a"), ROOT, state=AgentState.DONE),
        record(("s1", "b"), ROOT, state=AgentState.FAILED),
        record(("s1", "c"), ROOT, state=AgentState.CANCELLED),
        needs_you=(parked,),
    )
    assert inbox.spawn_marker(snap, parked) == "4th sub-agent · 0 running"


def test_two_spawns_queued_at_once_do_not_claim_the_same_ordinal() -> None:
    """
    The hazard the ordinal is derived the hard way for. A lead issues three Agent
    calls in one breath and all three park; none of them has an ``AgentRecord`` yet,
    because the record is minted on approval. Counting records alone would render
    all three as the first sub-agent, which is precisely the aggregate the marker
    exists to make visible.
    """
    first, second, third = (
        spawn(ROOT, "p1", at=1.0),
        spawn(ROOT, "p2", at=2.0),
        spawn(ROOT, "p3", at=3.0),
    )
    snap = snapshot(record(ROOT), needs_you=(first, second, third))

    markers = [inbox.spawn_marker(snap, o) for o in (first, second, third)]
    assert markers == [
        "1st sub-agent · 0 running",
        "2nd sub-agent · 0 running",
        "3rd sub-agent · 0 running",
    ]


def test_a_queued_spawn_is_not_counted_as_running() -> None:
    """It is the thing being asked about. Consent is not a start."""
    first, second = spawn(ROOT, "p1", at=1.0), spawn(ROOT, "p2", at=2.0)
    snap = snapshot(record(ROOT), needs_you=(first, second))
    assert inbox.spawn_marker(snap, second) == "2nd sub-agent · 0 running"


def test_another_sessions_fleet_does_not_move_this_ones_count() -> None:
    """The store holds every session at once; a fleet-wide count would be wrong."""
    mine = spawn(ROOT, "p2", at=2.0)
    theirs = spawn(OTHER, "p1", at=1.0)
    snap = snapshot(
        record(ROOT),
        record(OTHER),
        record(("s2", "a"), OTHER, agent_type="builder"),
        record(("s2", "b"), OTHER, agent_type="skeptic"),
        needs_you=(theirs, mine),
    )
    assert inbox.spawn_marker(snap, mine) == "1st sub-agent · 0 running"
    assert inbox.spawn_marker(snap, theirs) == "3rd sub-agent · 2 running"


def test_a_sub_agent_that_spawns_counts_into_its_sessions_fleet() -> None:
    """
    The scope is the session, not the parent. A sub-agent's own spawn adds to the
    same fleet the operator is being asked to bound.
    """
    parked = spawn(CHILD, "p2", at=2.0)
    snap = snapshot(record(ROOT), record(CHILD, ROOT, agent_type="lead"), needs_you=(parked,))
    assert inbox.spawn_marker(snap, parked) == "2nd sub-agent · 1 running"


def test_a_non_spawn_obligation_has_no_marker() -> None:
    """Every other row renders exactly what it rendered before."""
    write = approval(ROOT)
    question = QuestionPending(node=ROOT, since=2.0, summary="ended its turn")
    failure = SessionFailed(node=ROOT, since=3.0, summary="died", error="died")
    snap = snapshot(record(ROOT), needs_you=(write, question, failure))

    assert inbox.spawn_marker(snap, write) is None
    assert inbox.spawn_marker(snap, question) is None
    assert inbox.spawn_marker(snap, failure) is None


def test_a_non_spawn_obligation_does_not_move_a_spawns_ordinal() -> None:
    """
    Having no marker of its own is half the property; the other half is not
    inflating someone else's number.

    "Ahead of this one" is a position in ``snap.approvals``, which holds every
    parked call and not only the spawns -- so a session that has queued a Write and
    a Bash ahead of its first ``Agent`` would render it as the third sub-agent. That
    is the failure mode the marker exists to prevent, produced by the marker: a
    total nobody can check, on the one row asking the operator to bound a fleet.
    """
    write = approval(ROOT, "p1", at=1.0)
    bash = approval(ROOT, "p2", at=2.0, tool="Bash")
    parked = spawn(ROOT, "p3", at=3.0)
    snap = snapshot(record(ROOT), needs_you=(write, bash, parked))

    assert inbox.spawn_marker(snap, parked) == "1st sub-agent · 0 running"


def test_both_spellings_of_a_spawn_are_counted() -> None:
    """
    ``Agent`` and ``Task`` are one tool with two names and ``approval._REVIEW``
    holds both. A marker that knew only one would be absent for half the spawns and
    would mis-ordinal the other half.
    """
    as_task = spawn(ROOT, "p1", tool="Task")
    after = spawn(ROOT, "p2", at=2.0)
    snap = snapshot(record(ROOT), needs_you=(as_task, after))

    assert inbox.spawn_marker(snap, as_task) == "1st sub-agent · 0 running"
    assert inbox.spawn_marker(snap, after) == "2nd sub-agent · 0 running"


def test_every_tool_the_marker_counts_is_one_that_parks() -> None:
    """
    ``_SPAWN_TOOLS`` spells the two names rather than importing them, because
    ``approval.py`` has no spawn set to import -- it has a *review* set, and these
    are two of its members. The duplication is deliberate and the comment on it says
    so, which by STYLE.md §3 is exactly the kind that has to be pinned or the two
    copies drift silently.

    Both directions of drift are quiet. A name here that is not in ``_REVIEW`` is a
    spawn the marker counts and the gate never parks, so the operator is charged for
    an agent they were never asked about; ``_REVIEW`` losing a spelling leaves
    ``classify`` still requiring approval by its unknown-tool fallback, so nothing
    downstream notices either. It is pinned in this file rather than in
    ``test_approval.py`` because the copy is the thing that can be wrong: ``_REVIEW``
    is the original, and it is this module's premise that depends on it.
    """
    from pptmstr.approval import _REVIEW

    assert inbox._SPAWN_TOOLS <= _REVIEW


def test_the_ordinal_reads_as_english_past_ten() -> None:
    """11th, 12th and 13th are the three the last digit gets wrong."""
    assert [inbox._ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101)] == [
        "1st",
        "2nd",
        "3rd",
        "4th",
        "11th",
        "12th",
        "13th",
        "21st",
        "22nd",
        "101st",
    ]


# -- the marker reaches the row ------------------------------------------------


def _stub_row_imgui(monkeypatch):
    """
    An imgui with real numbers where ``_row`` does arithmetic on what it returns.

    Same trick the rail's stub uses and for the same reason: the layout has to run
    as written rather than collapse into mocks that compare equal to everything.
    ``ellipsis`` is replaced by identity so a column budget cannot silently eat the
    string a test is looking for.
    """
    from unittest.mock import MagicMock

    from imgui_bundle import imgui as real_imgui

    fake = MagicMock()
    fake.ImVec2 = real_imgui.ImVec2
    fake.get_cursor_screen_pos.side_effect = lambda: real_imgui.ImVec2(0.0, 0.0)
    fake.get_content_region_avail.return_value = real_imgui.ImVec2(800.0, 400.0)
    fake.get_text_line_height_with_spacing.return_value = 18.0
    fake.calc_text_size.side_effect = lambda t: real_imgui.ImVec2(7.0 * len(str(t)), 14.0)
    monkeypatch.setattr(inbox, "imgui", fake)
    monkeypatch.setattr(inbox, "ellipsis", lambda text, width: text)
    return fake


def _row_text(monkeypatch, snap: Snapshot, obligation: Any) -> list[str]:
    """Every string ``_row`` drew, in draw order."""
    from unittest.mock import MagicMock

    fake = _stub_row_imgui(monkeypatch)
    inbox._row(
        snap,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        obligation=obligation,
        expanded=False,
        now=100.0,
    )
    return [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]


def test_the_row_draws_the_count_beside_the_summary(monkeypatch) -> None:
    """
    A helper nothing calls is worse than no helper: the number's whole value is in
    being on the row the operator answers.
    """
    parked = spawn(ROOT, "p2", at=2.0)
    snap = snapshot(
        record(ROOT),
        record(("s1", "a"), ROOT, agent_type="builder"),
        needs_you=(parked,),
    )
    drawn = _row_text(monkeypatch, snap, parked)
    assert "2nd sub-agent · 1 running" in drawn
    # And the summary is still there, after it rather than instead of it.
    assert drawn.index("2nd sub-agent · 1 running") < drawn.index("spawn builder: write the parser")


def test_a_non_spawn_row_draws_what_it_drew_before(monkeypatch) -> None:
    write = approval(ROOT)
    snap = snapshot(record(ROOT), needs_you=(write,))
    assert not any("sub-agent" in line for line in _row_text(monkeypatch, snap, write))


def test_the_summary_keeps_the_width_the_marker_did_not_take(monkeypatch) -> None:
    """
    The row's columns are fixed zones so that a long field never shifts another one.
    The marker lives inside the summary zone, so the summary starts further right by
    exactly the marker's width and its budget shrinks by the same amount -- nothing
    outside the zone moves.
    """
    parked = spawn(ROOT)
    snap = snapshot(record(ROOT), needs_you=(parked,))
    fake = _stub_row_imgui(monkeypatch)
    monkeypatch.setattr(inbox, "ellipsis", lambda text, width: f"{width:.0f}")

    from unittest.mock import MagicMock

    inbox._row(
        snap,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        obligation=parked,
        expanded=False,
        now=100.0,
    )
    marker = "1st sub-agent · 0 running"
    budget = [c.args[1] for c in fake.text_colored.call_args_list if len(c.args) > 1][-1]
    assert float(budget) == 800.0 - inbox._X_SUMMARY - 7.0 * len(marker) - 10.0 - 12.0


# -- card density --------------------------------------------------------------


def test_height_tracks_obligation_not_terminality() -> None:
    """
    Three classes, not two.

    Splitting on terminality put working-but-not-blocking sessions -- the majority
    at twenty sessions, and the ones least likely to be acted on -- at the same
    height as a blocked one, filling the rail with the cards that matter least.
    """
    blocked = record(ROOT, state=AgentState.AWAITING_APPROVAL)
    active = record(ROOT, state=AgentState.THINKING)
    ended = record(ROOT, state=AgentState.DONE)

    assert rail._density(blocked, [approval(ROOT)]) == "blocked"
    assert rail._density(active, []) == "active"
    assert rail._density(ended, []) == "ended"
    assert rail._LINES["blocked"] > rail._LINES["active"] > rail._LINES["ended"]


def test_a_failed_session_is_blocked_not_ended() -> None:
    """A crash is an obligation, so its card gets the height to say so."""
    failed = record(ROOT, state=AgentState.FAILED)
    owed = [SessionFailed(node=ROOT, since=1.0, summary="died", error="died")]
    assert rail._density(failed, owed) == "blocked"


def test_no_height_class_is_reachable_only_with_sub_agents() -> None:
    """
    Collapsed height must not step at the 0->1 sub-agent boundary, or a card
    appearing under one session moves every card below it across every project --
    at the model's pace rather than the operator's.
    """
    blocked = record(ROOT, state=AgentState.AWAITING_APPROVAL)
    working = record(ROOT, state=AgentState.THINKING)
    ended = record(ROOT, state=AgentState.DONE)
    for rec, owed in ((blocked, [approval(ROOT)]), (working, []), (ended, [])):
        assert rail._density(rec, owed) in rail._LINES
    assert set(rail._LINES) == {"blocked", "active", "ended"}


# -- counting ------------------------------------------------------------------


def test_a_card_counts_every_kind_its_session_owes() -> None:
    """
    The habit that reproduced defect 1 inside the fix for defect 1: a project
    header summed approvals and read "6 sessions" with no waiting count while that
    project held a question and a crashed session.
    """
    owed = (
        approval(CHILD),
        QuestionPending(node=OTHER, since=2.0, summary="ended its turn"),
        SessionFailed(node=ROOT, since=3.0, summary="died"),
    )
    snap = snapshot(record(ROOT), record(CHILD, ROOT), record(OTHER), needs_you=owed)

    by_session = rail._by_session(snap)
    # The sub-agent's approval and the root's failure both belong to session s1.
    assert len(by_session["s1"]) == 2
    assert len(by_session["s2"]) == 1


def test_a_mixed_card_has_a_badge_for_each_kind_with_its_own_count() -> None:
    mixed = [approval(ROOT), QuestionPending(node=ROOT, since=2.0, summary="x")]
    assert rail._badges(mixed) == ((ObligationKind.APPROVAL, 1), (ObligationKind.QUESTION, 1))
    assert rail._badges([SessionFailed(ROOT, 1.0, "died")]) == ((ObligationKind.FAILURE, 1),)


# -- the sub-agent marker ------------------------------------------------------


def sub(node: NodeId, *, state: AgentState = AgentState.THINKING, kind: str = "builder"):
    return record(node, ROOT, state=state, agent_type=kind, task=kind)


def test_a_lone_sub_agent_is_counted_in_the_singular() -> None:
    signal = rail._subs_signal([sub(CHILD)], [])
    assert signal is not None
    assert signal[0] == f"{DISCLOSURE_GLYPH[False]} 1 sub"


def test_the_marker_names_the_state_with_the_strongest_claim() -> None:
    """
    A parked sub-agent under three working ones is the whole reason the marker is
    coloured at all -- averaging or taking the first would hide it.
    """
    subs = [
        sub(("s1", "a"), state=AgentState.THINKING),
        sub(("s1", "b"), state=AgentState.AWAITING_APPROVAL),
        sub(("s1", "c"), state=AgentState.RUNNING_TOOL),
    ]
    text, worst = rail._subs_signal(subs, [approval(("s1", "b"))])  # type: ignore[misc]
    assert worst is AgentState.AWAITING_APPROVAL
    assert text == f"{DISCLOSURE_GLYPH[False]} 3 subs · 1 waiting"


def test_a_finished_sub_agent_does_not_outrank_a_live_one() -> None:
    """
    Nothing reaps terminal sub-agents, so a session's list only grows. If DONE
    outranked THINKING every long-lived session would end up marked as finished.
    """
    subs = [sub(("s1", "a"), state=AgentState.DONE), sub(("s1", "b"), state=AgentState.THINKING)]
    assert rail._subs_signal(subs, [])[1] is AgentState.THINKING  # type: ignore[index]


def test_a_session_with_no_sub_agents_has_no_marker() -> None:
    assert rail._subs_signal([], []) is None


def test_the_markers_width_does_not_grow_with_the_fleet() -> None:
    """
    The bound the pip row did not have: one unwrapped run of labels spilled past
    the card border at three sub-agents, while every other string on a card is
    measured against a budget.
    """
    ten = [sub(("s1", f"a{i}")) for i in range(10)]
    hundred = [sub(("s1", f"a{i}")) for i in range(100)]
    assert len(rail._subs_signal(hundred, [])[0]) - len(rail._subs_signal(ten, [])[0]) == 1  # type: ignore[index]


# -- the draw path -------------------------------------------------------------
#
# A card stands for an agent and a session is a group of cards, so these drive
# ``_group``: what it draws is a stack of siblings, and the questions worth asking
# are how many cards there are, how tall the stack is, and which one a click hit.


class Drawn(NamedTuple):
    """
    What one ``_group`` call did.

    ``cards`` is the size passed to each ``invisible_button`` and ``origins`` is
    where the cursor was when it was submitted, both in draw order -- so between them
    they are the card count and the whole stack's geometry. ``fills`` and ``marks``
    are the filled and stroked rectangles, which is where the bracket and the focus
    outline land.
    """

    cards: list[tuple[float, float]]
    origins: list[tuple[float, float]]
    fills: list[tuple]
    marks: list[tuple]
    rings: int
    scrolls: int
    text: list[str]


def _stub_imgui(
    monkeypatch, *, hovered: int | None, clicked: int | None, mouse: tuple[float, float]
):
    """
    An imgui with real numbers where the pane does arithmetic on what it returns, so
    the layout runs as written rather than collapsing into mock objects that compare
    equal to everything. No GL context is needed for that.

    The cursor is **stateful** and text advances it: ``set_cursor_screen_pos`` moves
    it, ``get_cursor_screen_pos`` reports it, and a drawn string pushes it right by
    its own width. That is what makes a stack of cards stack rather than pile up at
    one origin, and what makes "this string got a smaller budget because the one
    before it got wider" a thing a test can see rather than a thing only a capture
    can. Strings measure at a flat 7px per character, so the numbers are not the
    font's -- what they preserve is the ordering, which is what the arithmetic here
    is for.

    ``hovered`` and ``clicked`` are card *indices*, not flags, because ImGui reports
    at most one hovered item and the whole point of sibling cards is that the click
    goes to exactly one of them.

    Geometry the callers rely on: a line is 18px, the group starts at (10, 20), the
    pane is 300px wide, and every measured string is 40px.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from imgui_bundle import imgui as real_imgui

    at = {"x": 10.0, "y": 20.0}
    nth = {"n": -1}
    placed: list[tuple[float, float]] = []

    def _set(v) -> None:
        at["x"], at["y"] = v.x, v.y

    def _button(*_args, **_kw) -> bool:
        nth["n"] += 1
        placed.append((at["x"], at["y"]))
        return False

    fake = MagicMock()
    fake.ImVec2 = real_imgui.ImVec2
    fake.get_cursor_screen_pos.side_effect = lambda: real_imgui.ImVec2(at["x"], at["y"])
    fake.set_cursor_screen_pos.side_effect = _set
    fake.invisible_button.side_effect = _button
    fake.is_item_clicked.side_effect = lambda: clicked is not None and nth["n"] == clicked
    fake.is_item_hovered.side_effect = lambda: nth["n"] in (hovered, clicked)
    fake.get_text_line_height_with_spacing.return_value = 18.0
    fake.get_content_region_avail.return_value = real_imgui.ImVec2(300.0, 400.0)
    fake.get_mouse_pos.return_value = real_imgui.ImVec2(*mouse)
    fake.calc_text_size.side_effect = lambda t: real_imgui.ImVec2(7.0 * len(str(t)), 14.0)

    def _text(_colour, body) -> None:
        at["x"] += 7.0 * len(str(body))

    def _same_line(*args) -> None:
        at["x"] += args[1] if len(args) > 1 else 8.0

    fake.text_colored.side_effect = _text
    fake.same_line.side_effect = _same_line
    fake.get_style.return_value = SimpleNamespace(item_spacing=SimpleNamespace(x=8.0))
    fake.placed = placed
    ring = MagicMock()
    monkeypatch.setattr(rail, "imgui", fake)
    monkeypatch.setattr(rail, "ellipsis", lambda text, width: text)
    monkeypatch.setattr(rail, "context_cell", ring)
    monkeypatch.setattr(rail, "activity_rain", MagicMock())
    return fake, ring


def _collect(fake, ring) -> Drawn:
    draw = fake.get_window_draw_list()
    return Drawn(
        cards=[(c.args[1].x, c.args[1].y) for c in fake.invisible_button.call_args_list],
        origins=list(fake.placed),
        fills=[c.args for c in draw.add_rect_filled.call_args_list],
        marks=[c.args for c in draw.add_rect.call_args_list],
        rings=ring.call_count,
        scrolls=fake.set_scroll_here_y.call_count,
        text=[str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1],
    )


def _draw_group(
    monkeypatch,
    snap: Snapshot,
    rec: AgentRecord,
    owed: list,
    *,
    state: rail.RailState | None = None,
    focus: Any = None,
    hovered: int | None = None,
    clicked: int | None = None,
    mouse: tuple[float, float] = (0.0, 0.0),
    cursor: NodeId | None = None,
) -> Drawn:
    """Run ``_group`` against the stub and hand back what it did."""
    from unittest.mock import MagicMock

    fake, ring = _stub_imgui(monkeypatch, hovered=hovered, clicked=clicked, mouse=mouse)
    rail._group(
        snap,
        focus if focus is not None else MagicMock(),
        state if state is not None else rail.RailState(),
        rec=rec,
        owed=owed,
        cursor=cursor,
        now=100.0,
    )
    return _collect(fake, ring)


def _group_text(monkeypatch, snap: Snapshot, rec: AgentRecord, owed: list, **kw) -> list[str]:
    return _draw_group(monkeypatch, snap, rec, owed, **kw).text


def _fleet(root_state: AgentState, *, owed: tuple = ()) -> tuple[Snapshot, AgentRecord]:
    root = record(ROOT, state=root_state)
    subs = [sub(("s1", "a")), sub(("s1", "b")), sub(("s1", "c"))]
    return snapshot(root, *subs, needs_you=owed), root


def test_a_working_session_says_it_has_sub_agents(monkeypatch) -> None:
    """
    The reported symptom: a root that is *working* with three live sub-agents drew
    nothing at all, because the active branch returned before the pip block.
    """
    snap, root = _fleet(AgentState.THINKING)
    assert any("3 subs" in line for line in _group_text(monkeypatch, snap, root, []))


def test_a_blocked_session_says_it_has_sub_agents(monkeypatch) -> None:
    snap, root = _fleet(AgentState.AWAITING_APPROVAL, owed=(approval(ROOT),))
    lines = _group_text(monkeypatch, snap, root, [approval(ROOT)])
    assert any("3 subs" in line for line in lines)


def test_an_ended_session_says_it_has_sub_agents(monkeypatch) -> None:
    """
    Terminal is where the fleet is least visible and most worth a count: nothing
    reaps sub-agents, so this is the card that says what the session leaves behind.
    """
    snap, root = _fleet(AgentState.DONE)
    assert any("3 subs" in line for line in _group_text(monkeypatch, snap, root, []))


def test_a_session_without_sub_agents_says_nothing_about_them(monkeypatch) -> None:
    snap = snapshot(record(ROOT, state=AgentState.THINKING))
    assert not any("sub" in line for line in _group_text(monkeypatch, snap, snap.nodes[ROOT], []))


# -- the group -----------------------------------------------------------------

ROLES = ("scout", "builder", "skeptic")
WORKING = (AgentState.THINKING,) * 3


def _expandable(
    states: tuple[AgentState, ...] = WORKING, *, needs_you: tuple = ()
) -> tuple[Snapshot, AgentRecord]:
    root = record(ROOT, state=AgentState.THINKING)
    subs = [sub(("s1", r), state=st, kind=r) for r, st in zip(ROLES, states, strict=True)]
    return snapshot(root, *subs, needs_you=needs_you), root


def _open() -> rail.RailState:
    return rail.RailState(expanded={"s1"})


def test_a_new_session_is_collapsed(monkeypatch) -> None:
    """
    Collapsed is the default, and collapsed means one card. Expanded-by-default
    re-flows the map every time the model spawns something, without being asked.
    """
    snap, root = _expandable()
    out = _draw_group(monkeypatch, snap, root, [], state=rail.RailState())
    assert len(out.cards) == 1
    assert any(f"{DISCLOSURE_GLYPH[False]} 3 subs" in line for line in out.text)
    assert not any(line in ROLES for line in out.text)


def test_an_open_group_draws_a_card_per_sub_agent(monkeypatch) -> None:
    """A sub-agent is an agent, so it gets a card, not a row inside its parent."""
    snap, root = _expandable()
    out = _draw_group(monkeypatch, snap, root, [], state=_open())
    assert len(out.cards) == 4
    # Open, the marker is the caret alone -- the count is redundant with the cards.
    assert DISCLOSURE_GLYPH[True] in out.text
    assert not any("subs" in line for line in out.text)
    assert [line for line in out.text if line in ROLES] == list(ROLES)


def test_an_open_group_still_draws_its_finished_sub_agents(monkeypatch) -> None:
    """
    The trap this design is most exposed to. Nothing reaps a terminal sub-agent and
    ``subagents_of`` applies no state filter, so the member list is append-only --
    which is what makes the group's height monotone in count. Drop the finished ones
    and the group shrinks every time one ends, putting the motion the collapse rule
    exists to remove back inside the group.
    """
    snap, root = _expandable((AgentState.DONE, AgentState.CANCELLED, AgentState.THINKING))
    out = _draw_group(monkeypatch, snap, root, [], state=_open())
    assert len(out.cards) == 4
    assert [line for line in out.text if line in ROLES] == list(ROLES)


def test_a_sub_agent_card_is_indented_inside_its_group(monkeypatch) -> None:
    """
    Containment is structural, not just a line: the members are narrower than the
    root by the indent, and every member by the same amount -- an indent applied
    relative to the previous card would walk the group off the right of the pane.
    """
    snap, root = _expandable()
    out = _draw_group(monkeypatch, snap, root, [], state=_open())

    left = [x for x, _ in out.origins]
    assert left[0] == 10.0
    # Every member at the same inset, not each one inset from the last.
    assert left[1:] == [10.0 + rail._GROUP_INDENT] * 3
    assert [w for w, _ in out.cards][1:] == [300.0 - rail._GROUP_INDENT] * 3


def test_an_open_group_is_bracketed_from_its_root_to_its_last_member(monkeypatch) -> None:
    snap, root = _expandable()
    out = _draw_group(monkeypatch, snap, root, [], state=_open())

    rules = [f for f in out.fills if f[1].x - f[0].x == rail._RULE_W]
    assert len(rules) == 1
    lo, hi = rules[0][0], rules[0][1]
    # The root card is 2 lines + padding; each member the same, drawn under it.
    root_h = 18.0 * rail._LINES["active"] + rail._PAD * 2
    assert lo.y == 20.0 + root_h
    assert hi.y == 20.0 + root_h * 4
    assert lo.x < 10.0 + rail._GROUP_INDENT, "the rule must sit in the gutter, not on a card"


def test_a_collapsed_group_has_no_bracket(monkeypatch) -> None:
    snap, root = _expandable()
    out = _draw_group(monkeypatch, snap, root, [], state=rail.RailState())
    assert not [f for f in out.fills if f[1].x - f[0].x == rail._RULE_W]


def test_a_collapsed_groups_height_does_not_move_with_the_fleet(monkeypatch) -> None:
    """
    A card below stays where it was. A collapsed height that stepped with the fleet
    would move every card under it, across project boundaries, at the model's pace.
    """
    heights = set()
    for n in (0, 1, 3):
        root = record(ROOT, state=AgentState.THINKING)
        subs = [sub(("s1", f"a{i}")) for i in range(n)]
        out = _draw_group(monkeypatch, snapshot(root, *subs), root, [], state=rail.RailState())
        assert len(out.cards) == 1
        heights.add(out.cards[0][1])
    assert len(heights) == 1


def test_an_open_groups_height_is_the_sum_of_its_members(monkeypatch) -> None:
    """
    Group height is the sum of its cards, so it is monotone in sub-agent *count* and
    not in sub-agent *state* -- a member parking on an approval grows its own card by
    a line. That is accepted: ``_LINES`` already moves cards on every state change a
    root makes, and this only reaches a group the operator opened.
    """
    snap, root = _expandable((AgentState.AWAITING_APPROVAL, AgentState.THINKING, AgentState.DONE))
    owed = [approval(("s1", "scout"))]
    out = _draw_group(monkeypatch, snap, root, owed, state=_open())

    def box(density: str) -> float:
        return 18.0 * rail._LINES[density] + rail._PAD * 2

    # The root is *active*, not blocked: open, the member that owns the approval has
    # a card, and that card takes the badge and the blocked height. Height tracks
    # obligation, and the obligation is rendered where it lives.
    assert [h for _, h in out.cards] == [
        box("active"),
        box("blocked"),
        box("active"),
        box("ended"),
    ]


def test_a_collapsed_root_is_sized_and_badged_for_its_whole_session(monkeypatch) -> None:
    """
    The other half of the same rule, and the reason it is scoped rather than simply
    root-only. Collapsed, the root's card is the session's only surface: a badge that
    counted only the root's own calls would take the outstanding-call count off the
    rail. The marker cannot stand in for it -- it counts *agents* where the badge
    counts *obligations*, so one sub-agent on three calls reads "1 waiting".
    """
    snap, root = _expandable((AgentState.AWAITING_APPROVAL, AgentState.THINKING, AgentState.DONE))
    owed = [approval(("s1", "scout"), "p1"), approval(("s1", "scout"), "p2")]
    out = _draw_group(monkeypatch, snap, root, owed, state=rail.RailState())

    assert len(out.cards) == 1
    assert out.cards[0][1] == 18.0 * rail._LINES["blocked"] + rail._PAD * 2
    # Two obligations, one waiting agent. Both numbers on screen, and different.
    assert any(line.endswith(" 2") for line in out.text), "the badge lost the call count"
    assert any("1 waiting" in line for line in out.text)


def test_a_group_grows_when_a_sub_agent_arrives_and_never_shrinks(monkeypatch) -> None:
    root = record(ROOT, state=AgentState.THINKING)
    one = snapshot(root, sub(("s1", "a")))
    then_finished = snapshot(root, sub(("s1", "a"), state=AgentState.DONE), sub(("s1", "b")))

    before = _draw_group(monkeypatch, one, root, [], state=_open()).cards
    after = _draw_group(monkeypatch, then_finished, root, [], state=_open()).cards
    assert len(after) > len(before)


def test_nothing_closes_a_group_when_its_sub_agents_finish(monkeypatch) -> None:
    """
    Auto-collapsing on a drained fleet is model-paced motion wearing a tidiness
    costume -- the same failure ``OnNode.pinned`` is split to prevent.
    """
    state = rail.RailState()
    state.toggle("s1")
    snap, root = _expandable((AgentState.DONE,) * 3)
    out = _draw_group(monkeypatch, snap, root, [], state=state)
    assert state.expanded == {"s1"}
    assert len(out.cards) == 4


def test_a_sub_agent_card_carries_no_context_ring(monkeypatch) -> None:
    """
    A sub-agent has no client to poll, so ``context_cell(None)`` would draw an empty
    ring reading "0% used, healthy" under a tooltip promising a value that never
    arrives. The only ring in the group is the session's own.
    """
    snap, root = _expandable()
    assert _draw_group(monkeypatch, snap, root, [], state=_open()).rings == 1


def test_a_sub_agent_card_carries_its_role_state_topic_model_and_spend(monkeypatch) -> None:
    """
    The slot table from the note, which was a card's worth of content all along --
    and the reason model had nowhere to go while this was a one-line row. Elapsed
    rides along because a card has a slot for it; the ring does not, and its absence
    is asserted separately.
    """
    snap, root = _expandable()
    text = _group_text(monkeypatch, snap, root, [], state=_open())
    at = text.index("scout")
    assert text[at : at + 6] == ["scout", "1m40s", "thinking", "working", "sonnet-5", "0 tok"]


def test_a_sub_agent_card_is_titled_by_its_role_not_its_task(monkeypatch) -> None:
    """
    ``agent_type`` is what the operator spawned and what siblings differ by. The
    driver sets ``task`` to the same string today; reading the role says which fact
    is meant rather than resting on the two staying equal.
    """
    root = record(ROOT, state=AgentState.THINKING)
    odd = record(("s1", "x"), ROOT, state=AgentState.THINKING, agent_type="skeptic", task="a task")
    text = _group_text(monkeypatch, snapshot(root, odd), root, [], state=_open())
    assert "skeptic" in text
    assert "a task" not in text


def test_only_the_root_card_carries_the_disclosure(monkeypatch) -> None:
    """A group opens; a card does not, so a card that is not a group has no marker."""
    snap, root = _expandable()
    text = _group_text(monkeypatch, snap, root, [], state=_open())
    assert sum(1 for line in text if line == DISCLOSURE_GLYPH[True]) == 1

    closed = _group_text(monkeypatch, snap, root, [], state=rail.RailState())
    assert sum(1 for line in closed if "subs" in line) == 1


def test_the_disclosure_opens_and_closes_the_same_session() -> None:
    state = rail.RailState()
    state.toggle("s1")
    assert state.expanded == {"s1"}
    state.toggle("s1")
    assert state.expanded == set()


# -- clicks --------------------------------------------------------------------


def _marker_box(monkeypatch, snap, root, owed, *, state) -> tuple[float, float, float, float]:
    """
    The hit box the marker reported, taken from the code rather than recomputed.

    A test that recomputes the layout to find the target is a second implementation
    of it, and it agrees with the first right up until the layout changes -- which is
    exactly when the click needs checking. This asks.
    """
    seen: list = []
    real = rail._draw_subs
    monkeypatch.setattr(rail, "_draw_subs", lambda sig: seen.append(real(sig)) or seen[-1])
    _draw_group(monkeypatch, snap, root, owed, state=state)
    return next(b for b in seen if b is not None)


def _centre(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def test_clicking_the_disclosure_opens_the_group_and_does_not_move_the_cursor(monkeypatch) -> None:
    """
    The disclosure is the one target that shares the root card's button, so the two
    gestures are told apart by where the pointer is. A disclosure that also selected
    would be one click doing two things, and the second is the one nobody asked for.
    """
    from unittest.mock import MagicMock

    snap, root = _expandable()
    state, focus = rail.RailState(), MagicMock()
    box = _marker_box(monkeypatch, snap, root, [], state=state)
    _draw_group(
        monkeypatch, snap, root, [], state=state, focus=focus, clicked=0, mouse=_centre(box)
    )
    assert state.expanded == {"s1"}
    focus.to_node.assert_not_called()


def test_the_open_carets_hit_box_stays_a_target_worth_aiming_at(monkeypatch) -> None:
    """
    Open, the drawn marker is one caret a few pixels wide -- and it is the only
    affordance that closes the group. A box measured straight off that string is not
    something anyone can hit.

    It grows leftwards, into the state label. Both neighbours resolve a click as
    "select this card" so an overshoot either way is harmless in itself, but the
    topic is what the freed width was just handed to, and a toggle fired by someone
    aiming at it would undo the reading this change exists to restore.
    """
    snap, root = _expandable()
    box = _marker_box(monkeypatch, snap, root, [], state=_open())
    closed = _marker_box(monkeypatch, snap, root, [], state=rail.RailState())

    assert box[2] - box[0] >= rail._HIT_MIN_W
    # Both markers are drawn from the same point on the line, so the two boxes say
    # which way the floor grew: the caret's own right edge is left of the count's,
    # and the box reaches further left than the count's ever did.
    assert box[2] < closed[2], "the open marker is not actually narrower"
    assert box[0] < closed[0], "the floor grew into the topic instead of away from it"

    state = _open()
    _click(monkeypatch, snap, root, [], state=state, card=0, mouse=_centre(box))
    assert state.expanded == set(), "the caret's own centre did not close the group"


def _click(monkeypatch, snap, root, owed, *, state, card: int, mouse=(0.0, 0.0)):
    """
    Click one card and hand back the **real** cursor it produced.

    A ``MagicMock`` focus would record the arguments and prove nothing: the defect
    this shape exists to catch is a click whose *resolution* names a different agent
    from the one clicked, which every assertion on a call argument passes straight
    over.
    """
    from pptmstr.ui.focus import FocusState

    focus = FocusState()
    _draw_group(monkeypatch, snap, root, owed, state=state, focus=focus, clicked=card, mouse=mouse)
    return focus


def _lit(monkeypatch, snap, root, owed, *, state, cursor) -> list[float]:
    return _focused(_draw_group(monkeypatch, snap, root, owed, state=state, cursor=cursor))


def test_clicking_a_card_in_an_open_group_selects_and_lights_that_card(monkeypatch) -> None:
    """
    The whole loop: click a card, resolve the cursor, draw again, and check the card
    that lights is the card that was clicked.

    The defect this replaces was exactly a break in that loop. The highlight was
    expansion-aware and the click was not, so clicking an open root resolved
    session-wide, landed on a *member's* obligation, lit the member's card and
    scrolled away from the one under the pointer. One scope value now drives both, so
    they cannot disagree -- and this walks every card in the group rather than the
    root alone, because the next drift would be just as invisible.
    """
    snap, root = _expandable(needs_you=(approval(("s1", "scout")),))
    owed = list(snap.needs_you)
    members = [ROOT, ("s1", "scout"), ("s1", "builder"), ("s1", "skeptic")]

    def top(i: int) -> float:
        boxes = [18.0 * rail._LINES[d] + rail._PAD * 2 for d in ("active", "blocked", "active")]
        return 20.0 + sum(boxes[:i])

    for i, node in enumerate(members):
        state = _open()
        focus = _click(monkeypatch, snap, root, owed, state=state, card=i)
        assert focus.node(snap) == node, f"card {i} selected {focus.node(snap)}"
        assert _lit(monkeypatch, snap, root, owed, state=state, cursor=node) == [top(i)]


def test_clicking_a_collapsed_root_still_reaches_its_sub_agents_parked_call(monkeypatch) -> None:
    """
    Collapsed, the root's card *is* the session, so the click has to reach the member
    that is actually waiting -- the common case, and the reason the scope is a scope
    rather than a switch to root-only. The same card still lights, because the same
    scope decides both.
    """
    snap, root = _expandable(needs_you=(approval(("s1", "scout")),))
    owed = list(snap.needs_you)
    state = rail.RailState()

    focus = _click(monkeypatch, snap, root, owed, state=state, card=0)
    assert focus.node(snap) == ("s1", "scout")
    assert _lit(monkeypatch, snap, root, owed, state=state, cursor=("s1", "scout")) == [20.0]


def test_a_sub_agent_card_has_no_disclosure_to_click(monkeypatch) -> None:
    """
    A pointer sitting where the root's marker is must not toggle the group from a
    member's card. Nothing but the root passes a signal, so there is no box to hit.
    """
    snap, root = _expandable()
    box = _marker_box(monkeypatch, snap, root, [], state=_open())

    state = _open()
    focus = _click(monkeypatch, snap, root, [], state=state, card=1, mouse=_centre(box))
    assert state.expanded == {"s1"}, "a member's card closed its own group"
    assert focus.node(snap) == ("s1", "scout")


def test_a_session_with_no_sub_agents_cannot_be_open(monkeypatch) -> None:
    snap = snapshot(record(ROOT, state=AgentState.THINKING))
    out = _draw_group(monkeypatch, snap, snap.nodes[ROOT], [], state=_open())
    assert len(out.cards) == 1
    assert not any("sub" in line for line in out.text)


def test_expansion_state_is_dropped_for_sessions_the_snapshot_no_longer_has() -> None:
    state = rail.RailState(expanded={"s1", "s2"})
    state.prune(snapshot(record(OTHER)))
    assert state.expanded == {"s2"}


def test_the_rail_prunes_before_it_gives_up_on_an_empty_snapshot(monkeypatch) -> None:
    """
    An empty snapshot is the one case where every id in the set is stale, and it is
    also the one the pane returns early on. Pruning after that return would leak
    exactly the ids that can never be pruned any other way.
    """
    from unittest.mock import MagicMock

    monkeypatch.setattr(rail, "imgui", MagicMock())
    state = rail.RailState(expanded={"s1"})
    rail.draw(snapshot(), MagicMock(), state, 100.0)
    assert state.expanded == set()


# -- the cursor on the rail ----------------------------------------------------


def _focused(out: Drawn) -> list[float]:
    """The y of every card outlined as focused. Focus is 2px, everything else 1px."""
    return [m[0].y for m in out.marks if m[4] == 2.0]


def test_a_closed_group_lights_its_root_for_a_cursor_on_a_sub_agent(monkeypatch) -> None:
    """
    Closed, the root's card is the only surface the session has. A cursor on a
    sub-agent must land somewhere, or the pane meant to say "here is where this came
    from" says nothing -- the failure the scroll-to-focus line also exists for.
    """
    snap, root = _expandable()
    out = _draw_group(monkeypatch, snap, root, [], state=rail.RailState(), cursor=("s1", "builder"))
    assert _focused(out) == [20.0]


def test_an_open_group_lights_only_the_sub_agents_own_card(monkeypatch) -> None:
    """
    Two lit cards would be two selections again, which is the defect the single
    cursor exists to remove. Selecting one member rather than another changes DETAIL
    and TRANSCRIPT, so exactly one card must say so.
    """
    snap, root = _expandable()
    out = _draw_group(monkeypatch, snap, root, [], state=_open(), cursor=("s1", "builder"))
    box = 18.0 * rail._LINES["active"] + rail._PAD * 2
    assert _focused(out) == [20.0 + box * 2]


def test_an_open_group_still_lights_its_root_for_a_cursor_on_the_root(monkeypatch) -> None:
    snap, root = _expandable()
    out = _draw_group(monkeypatch, snap, root, [], state=_open(), cursor=ROOT)
    assert _focused(out) == [20.0]


def test_the_scroll_pin_is_armed_only_when_the_cursor_moves() -> None:
    """
    ``set_scroll_here_y`` re-pins every frame it is called, so a standing flag snaps
    the view back before a wheel event could be noticed -- ``follow_tail`` records the
    same failure. Load-bearing now rather than a nicety: a full card is several times
    taller than the marker it replaces, so an open group of five overflows the pane.
    """
    state = rail.RailState()
    state.arm_scroll(ROOT)
    assert state.scroll_to_focus

    state.scroll_to_focus = False
    state.arm_scroll(ROOT)
    assert not state.scroll_to_focus

    state.arm_scroll(("s1", "builder"))
    assert state.scroll_to_focus, "moving between cards of one group still moves"


def test_a_focused_card_spends_the_pin_and_then_lets_go(monkeypatch) -> None:
    snap, root = _expandable()
    state = _open()
    state.arm_scroll(ROOT)

    assert _draw_group(monkeypatch, snap, root, [], state=state, cursor=ROOT).scrolls == 1
    assert _draw_group(monkeypatch, snap, root, [], state=state, cursor=ROOT).scrolls == 0


def test_the_rail_re_arms_the_pin_when_the_cursor_moves_between_cards(monkeypatch) -> None:
    """
    The wiring: ``arm_scroll`` has to be called from the frame loop, and a pin
    nothing arms is a pin that fires once at launch and never again.
    """

    from pptmstr.ui.focus import FocusState, OnNode

    snap, _ = _expandable()
    state = _open()
    state.arm_scroll(ROOT)
    state.scroll_to_focus = False

    _stub_imgui(monkeypatch, hovered=None, clicked=None, mouse=(0.0, 0.0))
    rail.draw(snap, FocusState(target=OnNode(("s1", "builder"), pinned=True)), state, 100.0)

    assert state._scrolled_to == ("s1", "builder")


def test_the_disclosure_glyphs_come_from_the_icon_font() -> None:
    """
    Inconsolata carries no geometric-shapes block, so ``▸`` and ``▾`` both
    render as the same fallback box: a disclosure that cannot say which way it
    points. No unit test sees that -- both are one character and both measure the
    same -- and only a capture did. FontAwesome is merged into the body face and is
    the one of the two guaranteed to have a caret.

    If the merge itself fails the pair degrades to tofu together, which is the same
    trade ``STATE_GLYPH`` makes; here the redundant channel is the member cards,
    which are on screen exactly when the group is open.
    """
    assert DISCLOSURE_GLYPH[True] != DISCLOSURE_GLYPH[False]
    for glyph in DISCLOSURE_GLYPH.values():
        assert all(0xE000 <= ord(c) <= 0xF8FF for c in glyph), "not an icon-font codepoint"


def test_a_sub_agent_recovered_without_a_parent_still_gets_no_context_ring(monkeypatch) -> None:
    """
    ``parent`` answers "is it attached"; ``node_id[1]`` answers "is it a sub-agent",
    and the store's recovered-placeholder path makes those different questions. An
    approval arriving for an agent whose spawn hook never fired mints a record for
    that node, and leaves ``parent`` None when the root is not in the table yet.

    Keyed on ``parent``, such a record is taken for a root and handed a ring reading
    "0% used, healthy" for a context that will never be polled -- on the one card
    already flagged as recovered from a failure.
    """
    ghost = record(("s1", "ghost"), None, state=AgentState.AWAITING_APPROVAL, agent_type="Explore")
    assert ghost.parent is None and ghost.node_id[1] is not None

    snap = snapshot(ghost, needs_you=(approval(("s1", "ghost")),))
    out = _draw_group(monkeypatch, snap, ghost, [approval(("s1", "ghost"))])
    assert out.rings == 0
    assert "Explore" in out.text

    # And once it is working rather than parked, where the ring's absence is what
    # makes room for the model rather than being free.
    live = record(("s1", "ghost"), None, state=AgentState.THINKING, agent_type="Explore")
    working = _draw_group(monkeypatch, snapshot(live), live, [])
    assert working.rings == 0
    assert "sonnet-5" in working.text


def test_the_open_marker_is_narrower_than_the_collapsed_one(monkeypatch) -> None:
    """
    Measured in the font it is drawn in, because the topic's budget is charged
    against this number. Report the collapsed width while open and the topic is told
    it has less room than it does, which is the direction that overruns the border.
    """
    _stub_imgui(monkeypatch, hovered=None, clicked=None, mouse=(0.0, 0.0))
    subs = [sub(("s1", "a")), sub(("s1", "b"))]
    owed = [approval(("s1", "a"))]

    closed = rail._subs_signal(subs, owed, expanded=False)
    opened = rail._subs_signal(subs, owed, expanded=True)
    assert closed is not None and opened is not None
    assert closed[0] == f"{DISCLOSURE_GLYPH[False]} 2 subs · 1 waiting"
    assert opened[0] == DISCLOSURE_GLYPH[True]
    assert rail._subs_width(opened) < rail._subs_width(closed)


def _topic_budget(monkeypatch, snap, root, *, state) -> float:
    """What ``ellipsis`` was told the root's topic could have."""
    from unittest.mock import MagicMock

    budgets: dict[str, float] = {}
    _stub_imgui(monkeypatch, hovered=None, clicked=None, mouse=(0.0, 0.0))
    monkeypatch.setattr(rail, "ellipsis", lambda text, width: budgets.setdefault(text, width))
    rail._group(snap, MagicMock(), state, rec=root, owed=[], cursor=None, now=100.0)
    return budgets[root.topic]


def test_opening_a_group_gives_the_markers_freed_width_to_the_topic(monkeypatch) -> None:
    """
    The point of shortening the marker. ``delegating to 2 sub-agents`` is the one
    string on an open root saying what it is doing, and it was being crushed to three
    characters and an ellipsis by a count the cards underneath already carried.

    Still measured, not merely roomier: it is the field that gives way, it just has
    more to give way from.
    """
    import dataclasses

    plain, _ = _expandable()
    root = dataclasses.replace(plain.nodes[ROOT], topic="delegating to 2 sub-agents")
    snap = snapshot(root, *[plain.nodes[("s1", r)] for r in ROLES])

    closed = _topic_budget(monkeypatch, snap, root, state=rail.RailState())
    opened = _topic_budget(monkeypatch, snap, root, state=_open())

    assert opened > closed
    # It is the marker's width that moved, so the topic gains what the marker lost.
    _stub_imgui(monkeypatch, hovered=None, clicked=None, mouse=(0.0, 0.0))
    subs = [plain.nodes[("s1", r)] for r in ROLES]
    lost = rail._subs_width(rail._subs_signal(subs, [], expanded=False)) - rail._subs_width(
        rail._subs_signal(subs, [], expanded=True)
    )
    assert opened - closed == lost


# -- what the surfaces say about a lead that is only waiting on its workers -------


def test_every_state_has_an_attention_rank() -> None:
    """
    ``_claim`` is read inside a draw call. A member it does not handle raises there
    and takes the frame down rather than the card, which is why it is a ``match``
    ending in ``assert_never`` -- but a total function is only half of it, and mypy
    checks that half. This checks the other: that every member actually resolves.
    """
    for state in AgentState:
        assert isinstance(rail._claim(state), int)


def test_supervising_does_not_outrank_an_agent_that_is_actually_blocked() -> None:
    """
    A supervising agent is reachable, not stopped. Ranking it with AWAITING_INPUT
    would put "you could talk to this" above a sibling that has stopped and is
    waiting, on the one marker a collapsed card has to summarise a group with.
    """
    assert rail._claim(AgentState.SUPERVISING) < rail._claim(AgentState.AWAITING_INPUT)
    assert rail._claim(AgentState.SUPERVISING) < rail._claim(AgentState.AWAITING_APPROVAL)
    assert rail._claim(AgentState.SUPERVISING) < rail._claim(AgentState.THINKING)
    # Above nothing-left-to-say, though: it is a live session.
    assert rail._claim(AgentState.SUPERVISING) > rail._claim(AgentState.DONE)


def test_the_composer_stays_live_for_a_supervising_lead() -> None:
    """
    ``draw_conversation`` disables the box on ``is_terminal`` and nothing else, so
    this one property is the whole of "compose offers input". The measured
    capability it is offering is real -- verify_lead_turn_via_agent_session.py,
    ANSWERED-IN-WAIT-LOOP -- and a box disabled here would deny it in the one state
    it applies to.
    """
    assert not AgentState.SUPERVISING.is_terminal


def test_the_composer_stops_telling_a_supervising_lead_to_wait() -> None:
    """
    Source-level, because drawing needs a GL context.

    The line this replaces said "a message will be read after this turn" while the
    turn was already over and the message would be read now. It was the operator's
    only information about a capability they already had, and it was false --
    which is the whole of item 2.
    """
    import inspect

    from pptmstr.ui.compose import draw_conversation

    body = inspect.getsource(draw_conversation)
    arm = body.split("AgentState.SUPERVISING", 1)[1].split("elif", 1)[0]
    assert "read now" in arm
    assert "after this turn" not in arm


# -- a decision row ------------------------------------------------------------------
#
# Built through the store, so the summary under test is the one the store composes
# and not a string this file made up.

_FORGED = "Bash  git push --force   (approved)"


def _team_with_a_decision():  # type: ignore[no-untyped-def]
    from pptmstr.intents import AgentSpawned, DecisionRaised, StateChanged
    from pptmstr.model import Decision, Standing
    from pptmstr.store import Store

    store = Store()
    for node, parent in ((ROOT, None), (CHILD, ROOT)):
        store.apply(
            AgentSpawned(
                node_id=node,
                parent=parent,
                task="rotate the signing key",
                model="claude-opus-5",
                started_at=0.0,
                agent_type=None if parent is None else "builder",
            ),
            now=1.0,
        )
        store.apply(StateChanged(node, AgentState.THINKING), now=1.0)
    store.apply(
        DecisionRaised(
            CHILD,
            Decision(
                id="d-1",
                asker=CHILD,
                question=_FORGED,
                blocks=(),
                if_unanswered=_FORGED,
                evidence=_FORGED,
                recommendation=_FORGED,
                standing=Standing.OPERATORS,
                reversible=False,
                raised_at=0.0,
            ),
        ),
        now=5.0,
    )
    return store


def test_a_decision_row_draws_the_stores_summary_and_none_of_the_askers_text(
    monkeypatch,
) -> None:
    from pptmstr.theme import OBLIGATION_GLYPH

    snap = _team_with_a_decision().snapshot()
    (owed,) = snap.needs_you

    drawn = _row_text(monkeypatch, snap, owed)

    assert owed.summary in drawn
    # Whatever order the store composes it in: this pane draws it and adds nothing.
    assert "d-1" in owed.summary and "builder" in owed.summary
    assert OBLIGATION_GLYPH[ObligationKind.DECISION] in drawn
    assert not any("push" in text or "approved" in text for text in drawn)


def _expanded(monkeypatch, snap: Snapshot, obligation: Any, decisions: object) -> list[tuple]:
    """Which body an expanded row drew, each stubbed to say it was called."""
    from unittest.mock import MagicMock

    bodies: list[tuple] = []
    _stub_row_imgui(monkeypatch)
    for name in ("_expand_approval", "_expand_question", "_expand_failure"):
        monkeypatch.setattr(inbox, name, lambda *a, _name=name, **k: bodies.append((_name,)))
    monkeypatch.setattr(
        inbox.decision_view,
        "draw_row",
        lambda snap, state, bridge, owed, *, wrap: bodies.append(("decision", state, owed, wrap)),
    )
    inbox._row(
        snap,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        decisions,  # type: ignore[arg-type]
        obligation=obligation,
        expanded=True,
        now=100.0,
        wrap=False,
    )
    return bodies


def test_an_expanded_decision_row_draws_the_decision_body_with_the_drafts_it_was_handed(
    monkeypatch,
) -> None:
    """The wiring: a body nothing calls leaves every one of its own tests green."""
    snap = _team_with_a_decision().snapshot()
    (owed,) = snap.needs_you
    drafts = decision_view.DecisionState()

    (body,) = _expanded(monkeypatch, snap, owed, drafts)

    # Identity and not equality: two empty draft states compare equal, and a fresh
    # one handed to the row each frame is the box that forgets.
    assert body[0] == "decision" and body[1] is drafts
    assert body[2:] == (owed, False)


def test_an_expanded_approval_row_does_not_draw_a_decision_body(monkeypatch) -> None:
    parked = approval(ROOT)
    snap = snapshot(record(ROOT), needs_you=(parked,))

    assert _expanded(monkeypatch, snap, parked, decision_view.DecisionState()) == [
        ("_expand_approval",)
    ]


def test_the_inbox_keeps_a_draft_across_frames_and_rows_and_drops_it_once_answered(
    monkeypatch,
) -> None:
    """
    ``inbox.draw`` is what calls ``attend``. Unhooked, an answered decision's draft
    stays for the life of the process and an armed send outlives its row.
    """
    from unittest.mock import MagicMock

    from pptmstr.intents import DecisionAnswered
    from pptmstr.ui.focus import FocusState

    store = _team_with_a_decision()
    drafts = decision_view.DecisionState(
        drafts={"d-1": "  half an answer "}, confirming=("d-1", decision_view.Act.SEND)
    )
    monkeypatch.setattr(inbox, "imgui", MagicMock())
    monkeypatch.setattr(inbox, "_header", MagicMock())
    monkeypatch.setattr(inbox, "_row", MagicMock())
    monkeypatch.setattr(inbox, "_zero_state", MagicMock())
    monkeypatch.setattr(inbox, "_batch_controls", MagicMock())

    def frame(focus: FocusState) -> None:
        inbox.draw(
            store.snapshot(),
            focus,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            100.0,
            decisions=drafts,
        )

    on_it = FocusState()
    on_it.settle(store.snapshot())
    frame(on_it)
    frame(on_it)
    assert drafts.drafts == {"d-1": "  half an answer "}
    assert drafts.confirming == ("d-1", decision_view.Act.SEND)

    # The cursor leaves the row: the draft stays, the armed send does not.
    elsewhere = FocusState()
    frame(elsewhere)
    assert drafts.drafts == {"d-1": "  half an answer "} and drafts.confirming is None

    store.apply(DecisionAnswered("d-1", "  half an answer "), now=9.0)
    frame(on_it)
    assert drafts.drafts == {}


@pytest.mark.parametrize("shift", [False, True])
def test_the_approval_keys_do_nothing_on_a_decision_row(monkeypatch, shift: bool) -> None:
    """
    ``a`` on a decision must not approve, answer or dismiss anything -- and with
    an approval waiting beside it, must not approve *that* either.
    """
    from unittest.mock import MagicMock

    from pptmstr.intents import ApprovalRequested
    from pptmstr.ui import review
    from pptmstr.ui.focus import FocusState, OnObligation

    store = _team_with_a_decision()
    store.apply(ApprovalRequested(ROOT, approval(ROOT, at=6.0).approval), now=6.0)
    snap = store.snapshot()
    assert {o.kind for o in snap.needs_you} == {ObligationKind.DECISION, ObligationKind.APPROVAL}
    owed = next(o for o in snap.needs_you if o.kind is ObligationKind.DECISION)
    focus = FocusState(target=OnObligation(owed.key))

    fake = MagicMock()
    fake.Key = _real_imgui.Key
    fake.get_io.return_value.want_capture_keyboard = False
    fake.get_io.return_value.want_text_input = False
    fake.is_mouse_clicked.return_value = False
    fake.get_io.return_value.key_shift = shift
    # A mock's attributes are truthy: unset, every key would read as a chord.
    fake.get_io.return_value.key_ctrl = False
    fake.get_io.return_value.key_alt = False
    fake.get_io.return_value.key_super = False
    action_keys = {_real_imgui.Key.a, _real_imgui.Key.r, _real_imgui.Key.e}
    fake.is_key_pressed.side_effect = lambda key: key in action_keys
    monkeypatch.setattr(review, "imgui", fake)
    bridge = MagicMock()
    state = review.ReviewState()

    review.handle_keys(snap, focus, state, bridge)

    assert bridge.mock_calls == []
    assert state.editing is None and not state.focus_reason and state.edits == {}
    assert focus.target == OnObligation(owed.key)

    # The same keys on the approval beside it do act, so the fake is pressing them.
    parked = next(o for o in snap.needs_you if o.kind is ObligationKind.APPROVAL)
    review.handle_keys(snap, FocusState(target=OnObligation(parked.key)), state, bridge)
    assert bridge.resolve.call_count >= 1 or state.editing is not None


# -- a card's badges, and an answer nobody has read -------------------------------------


def _a_decision(node: NodeId, did: str, **fields: Any) -> Any:
    from pptmstr.model import Decision, DecisionOwed, Standing

    base: dict[str, Any] = dict(
        id=did,
        asker=node,
        question="q",
        blocks=(),
        if_unanswered="",
        evidence="",
        recommendation="",
        standing=Standing.OPERATORS,
        reversible=None,
        raised_at=1.0,
    )
    made = Decision(**{**base, **fields})
    from pptmstr.model import AskerFate

    return DecisionOwed(
        node=node, since=1.0, summary="a decision", decision=made, asker=AskerFate.LIVE
    )


def test_one_parked_call_and_three_decisions_draw_as_two_badges_not_as_four_calls(
    monkeypatch,
) -> None:
    from pptmstr.theme import OBLIGATION_GLYPH

    owed = [approval(ROOT), *(_a_decision(ROOT, f"d-{n}") for n in range(3))]
    root = record(ROOT, state=AgentState.AWAITING_APPROVAL)
    out = _draw_group(monkeypatch, snapshot(root, needs_you=tuple(owed)), root, owed)

    assert f"{OBLIGATION_GLYPH[ObligationKind.APPROVAL]} 1" in out.text
    assert f"{OBLIGATION_GLYPH[ObligationKind.DECISION]} 3" in out.text
    assert not any(line.endswith(" 4") for line in out.text)
    # Side by side, the call first, and neither drawn over the other.
    badges = [fill for fill in out.fills if len(fill) == 4 and fill[3] == 2.0]
    assert len(badges) == 2
    assert badges[0][1].x < badges[1][0].x
    assert badges[1][1].x <= 10.0 + 300.0


def _answered(snap: Snapshot, node: NodeId, did: str, *, read: bool = False) -> Snapshot:
    import dataclasses

    from pptmstr.model import Answered

    made = dataclasses.replace(
        _a_decision(node, did).decision,
        outcome=Answered("no", 5.0),
        first_delivered_at=9.0 if read else None,
    )
    return dataclasses.replace(snap, decisions={**snap.decisions, did: made})


def test_an_answer_its_asker_has_not_read_is_said_on_that_agents_card(monkeypatch) -> None:
    """
    Nothing wakes an agent for an answer. This is the one place that says it is
    sitting there without the operator opening anything.
    """
    snap, root = _expandable((AgentState.DONE, AgentState.THINKING, AgentState.THINKING))
    snap = _answered(snap, ("s1", ROLES[0]), "d-1")

    shut = _group_text(monkeypatch, snap, root, [])
    assert shut.count("1 answer unread") == 1

    opened = _draw_group(monkeypatch, snap, root, [], state=_open())
    assert opened.text.count("1 answer unread") == 1, "on the asker's card and not the root's too"


def test_a_finished_session_with_an_unread_answer_is_not_drawn_at_the_height_with_no_room(
    monkeypatch,
) -> None:
    root = record(ROOT, state=AgentState.DONE)
    quiet = _draw_group(monkeypatch, snapshot(root), root, [])
    waiting = _draw_group(monkeypatch, _answered(snapshot(root), ROOT, "d-1"), root, [])

    assert "1 answer unread" not in quiet.text
    assert quiet.cards[0][1] == 18.0 * rail._LINES["ended"] + rail._PAD * 2
    assert "1 answer unread" in waiting.text
    assert waiting.cards[0][1] == 18.0 * rail._LINES["active"] + rail._PAD * 2


def test_an_answer_that_has_been_read_or_belongs_to_another_session_marks_no_card(
    monkeypatch,
) -> None:
    root = record(ROOT)
    snap = _answered(_answered(snapshot(root, record(OTHER)), ROOT, "d-1", read=True), OTHER, "d-2")

    assert not any("unread" in line for line in _group_text(monkeypatch, snap, root, []))


# -- free text in a one-line slot ------------------------------------------------
#
# A task is typed into a multi-line composer and a topic or a summary is whatever an
# agent said. ImGui draws a line break as one, and a card's lines are placed by
# cursor arithmetic: the second line of a title lands on the state line under it.
# These walk the draws themselves, with the real ``ellipsis`` put back -- the stubs
# above replace it with identity, and it is what the one-line rule rides on.

THREE_LINE_TASK = "Where are we at with this?\n\nBTW this session is also dogfooding"
BROKEN_TOPIC = "supervising\nits sub-agents\x00 now"


def _real_ellipsis(monkeypatch, module, fake) -> None:
    monkeypatch.setattr(widgets, "imgui", fake)
    monkeypatch.setattr(module, "ellipsis", widgets.ellipsis)


def _broken(node: NodeId, parent: NodeId | None = None, **fields: Any) -> AgentRecord:
    import dataclasses

    return dataclasses.replace(
        record(node, parent, task=THREE_LINE_TASK, **fields), topic=BROKEN_TOPIC
    )


def _card_text(monkeypatch, snap: Snapshot, rec: AgentRecord, owed: list, **kw: Any) -> list[str]:
    from unittest.mock import MagicMock

    fake, ring = _stub_imgui(monkeypatch, hovered=None, clicked=None, mouse=(0.0, 0.0))
    _real_ellipsis(monkeypatch, rail, fake)
    rail._group(
        snap,
        MagicMock(),
        kw.get("state", rail.RailState()),
        rec=rec,
        owed=owed,
        cursor=None,
        now=100.0,
    )
    return _collect(fake, ring).text


@pytest.mark.parametrize(
    ("state", "owes", "density"),
    [
        (AgentState.THINKING, False, "active"),
        (AgentState.FAILED, True, "blocked"),
        (AgentState.DONE, False, "ended"),
    ],
)
def test_a_card_draws_a_three_line_task_and_a_broken_topic_on_one_line_each(
    monkeypatch, state: AgentState, owes: bool, density: str
) -> None:
    root = _broken(ROOT, state=state)
    owed = [SessionFailed(node=ROOT, since=1.0, summary="died", error="died")] if owes else []
    assert rail._density(root, owed) == density, "the three layouts a card has"

    drawn = _card_text(monkeypatch, snapshot(root, needs_you=tuple(owed)), root, owed)

    assert any(line.startswith("Where are we") for line in drawn), "the title was drawn"
    assert density == "ended" or any(line.startswith("supervising its") for line in drawn)
    assert [line for line in drawn if "\n" in line or "\x00" in line] == []
    assert not any(line.startswith("BTW") for line in drawn), "the task's third line, alone"


def test_a_sub_agent_card_titled_by_its_task_is_one_line_too(monkeypatch) -> None:
    """A sub-agent with no role falls back to its task (``session_menu.card_title``)."""
    root = record(ROOT)
    child = _broken(CHILD, ROOT)
    assert child.agent_type is None

    drawn = _card_text(monkeypatch, snapshot(root, child), root, [], state=_open())

    assert sum(line.startswith("Where are we") for line in drawn) == 1
    assert [line for line in drawn if "\n" in line or "\x00" in line] == []


def test_an_inbox_row_draws_its_title_qualifier_and_summary_on_one_line_each(
    monkeypatch,
) -> None:
    """
    The row is one line high and the expanded body is placed under it by the
    cursor, so a second line of any of the three is drawn over that body.
    """
    from unittest.mock import MagicMock

    failure = SessionFailed(
        node=CHILD, since=3.0, summary="its session died:\nCLIJSONDecodeError", error="died"
    )
    snap = snapshot(
        _broken(ROOT), record(CHILD, ROOT, agent_type="build\ner"), needs_you=(failure,)
    )
    fake = _stub_row_imgui(monkeypatch)
    _real_ellipsis(monkeypatch, inbox, fake)
    inbox._row(
        snap,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        obligation=failure,
        expanded=False,
        now=100.0,
    )
    drawn = [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]

    assert any(line.startswith("Where are we") for line in drawn)
    assert "orbital / build er" in drawn
    assert "its session died: CLIJSONDecodeError" in drawn
    assert [line for line in drawn if "\n" in line] == []


def test_the_empty_queue_lists_each_session_on_one_line(monkeypatch) -> None:
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.calc_text_size.side_effect = lambda t: _ImVec2(7.0 * len(str(t)), 14.0)
    monkeypatch.setattr(inbox, "imgui", fake)
    _real_ellipsis(monkeypatch, inbox, fake)

    inbox._zero_state(snapshot(_broken(ROOT)), 100.0)
    drawn = [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]

    assert any(line.startswith("Where are we") for line in drawn)
    assert f"- supervising its sub-agents{widgets.NUL_SHOWN} now" in drawn
    assert [line for line in drawn if "\n" in line or "\x00" in line] == []


# -- the header's key hint at the pane's width -------------------------------------


def _header_rects(
    width: float, obligation: Any, *, held: bool
) -> tuple[list[tuple[str, float, float, float, float]], float]:
    """
    ``inbox._header`` drawn in a real window ``width`` wide: each string with the
    rectangle ImGui gave it, and the x the window's content ends at.

    A real context because the claim is about where ImGui puts wrapped text, which
    no stub of it can answer. No GL: laying out and measuring need the font atlas's
    metrics and not its texture.
    """
    context = _real_imgui.create_context()
    try:
        io = _real_imgui.get_io()
        io.display_size = _ImVec2(1600.0, 900.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= _real_imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")
        rects: list[tuple[str, float, float, float, float]] = []

        class Noting:
            def __getattr__(self, name: str) -> object:
                return getattr(_real_imgui, name)

            @staticmethod
            def _note(text: str) -> None:
                low, high = _real_imgui.get_item_rect_min(), _real_imgui.get_item_rect_max()
                rects.append((text, low.x, low.y, high.x, high.y))

            def text_colored(self, colour: Any, text: str) -> None:
                _real_imgui.text_colored(colour, text)
                self._note(text)

            def text_disabled(self, text: str) -> None:
                _real_imgui.text_disabled(text)
                self._note(text)

        snap = snapshot(record(ROOT), needs_you=(obligation,))
        edge = 0.0
        saved = inbox.imgui
        inbox.imgui = Noting()  # type: ignore[assignment]
        try:
            # Twice: a window's size is settled on the frame after it first appears.
            for _ in range(2):
                rects.clear()
                _real_imgui.new_frame()
                _real_imgui.set_next_window_pos(_ImVec2(0.0, 0.0))
                _real_imgui.set_next_window_size(_ImVec2(width, 600.0))
                _real_imgui.begin("needs you")
                edge = _real_imgui.get_cursor_screen_pos().x + (
                    _real_imgui.get_content_region_avail().x
                )
                inbox._header(snap, obligation, 100.0, held=held)
                _real_imgui.end()
                _real_imgui.end_frame()
        finally:
            inbox.imgui = saved
        return rects, edge
    finally:
        _real_imgui.destroy_context(context)


_FAILED = SessionFailed(node=ROOT, since=3.0, summary="died", error="died")


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("width", [120.0, 260.0, 420.0, 700.0, 1500.0])
def test_no_part_of_the_header_is_drawn_past_the_panes_edge(width: float, held: bool) -> None:
    """
    The hint for a failed row ran off the pane's edge mid-word, with nothing to say
    there was more of it. Widths here are in the context's built-in font and not
    the app's, so which of them wrap is not the point: none may overrun.
    """
    rects, edge = _header_rects(width, _FAILED, held=held)
    hint = inbox.KEYS_HELD if held else inbox.keys_hint(_FAILED)

    assert hint in rects[-1][0], "the whole sentence is handed to ImGui"
    assert rects[-1][3] <= edge + 0.5


@pytest.mark.parametrize("held", [False, True])
def test_a_key_hint_that_does_not_fit_beside_the_count_wraps_under_it(held: bool) -> None:
    rects, _ = _header_rects(260.0, _FAILED, held=held)
    count, hinted = rects[0], rects[-1]

    assert not hinted[0].startswith("·"), "nothing precedes it on its line to separate it from"
    assert hinted[2] >= count[4], "under the count's line"
    assert hinted[1] == count[1], "from the pane's left edge"
    assert hinted[4] - hinted[2] > (count[4] - count[2]) * 1.5, "on more than one line"


def test_the_key_hint_rides_the_counts_line_when_the_pane_has_room() -> None:
    rects, edge = _header_rects(1500.0, _FAILED, held=False)
    count, hinted = rects[0], rects[-1]

    assert hinted[0] == f"· {inbox.header_hint(_FAILED)}"
    assert hinted[2] == count[2] and hinted[4] == count[4], "one line, the count's"
    assert hinted[3] <= edge


# -- closed cards and removed sessions are not "a fleet", and are not counted --------
#
# Snapshots from the reducer (``tests/test_ui_text``).


def _reduced(*, ran: bool = False, closed: bool = False, removed: bool = False) -> Snapshot:
    from pptmstr.store import Store
    from tests.test_ui_text import recall, run, run_and_remove

    store = Store()
    if removed:
        run_and_remove(store)
    if closed:
        recall(store)
    if ran:
        run(store)
    return store.snapshot()


def test_the_run_total_counts_what_ran_here_and_is_on_the_fleet() -> None:
    """
    A closed card is not an agent of this run, and neither it nor a removed
    session is in the count. The spend is everything this process spent, the
    removed session's 5.00 included, and the line says the two differ.
    """
    from tests.test_ui_text import RAN_ROOT, REMOVED_ROOT

    snap = _reduced(ran=True, closed=True, removed=True)
    assert len(snap.nodes) == 4

    assert [rec.node_id for rec in inbox.ran_here(snap)] == [RAN_ROOT]
    assert inbox.run_total(snap) == "1 agents this run, 2 more removed from the fleet · $7.00"
    assert REMOVED_ROOT in snap.nodes

    plain = _reduced(ran=True, closed=True)
    assert inbox.run_total(plain) == "1 agents this run · $2.00"


def test_the_empty_queue_draws_the_run_total_and_lists_no_closed_card(monkeypatch) -> None:
    """The wiring: ``_zero_state`` draws ``run_total`` and lists from the fleet."""
    from unittest.mock import MagicMock

    fake = MagicMock()
    monkeypatch.setattr(inbox, "imgui", fake)
    monkeypatch.setattr(inbox, "ellipsis", lambda text, width: text)
    snap = _reduced(ran=True, closed=True, removed=True)

    inbox._zero_state(snap, 100.0)
    drawn = [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]

    assert inbox.run_total(snap) in drawn
    assert "the task of sess-ran" in drawn
    assert not any("port the" in line or "sess-removed" in line for line in drawn)
    assert not any("4 agents" in line for line in drawn)


def test_the_header_names_the_key_that_opens_the_sessions_controls_and_it_is_the_apps_key() -> None:
    """
    A shortcut nobody is told of is one nobody presses. The header says it after
    the open row's own keys, on every kind of row, and not while the keys are
    off; and the letter it names is the one the frame listens for.
    """
    from imgui_bundle import imgui

    from pptmstr import app

    assert app.CARD_MENU_KEY == imgui.Key.m
    assert inbox.MENU_KEY_HINT.startswith("m ")
    for current in (None, _FAILED):
        said = inbox.header_hint(current)
        assert said == f"{inbox.keys_hint(current)} · {inbox.MENU_KEY_HINT}"
    rects, _ = _header_rects(1500.0, _FAILED, held=False)
    assert inbox.MENU_KEY_HINT in rects[-1][0]
    rects, _ = _header_rects(1500.0, _FAILED, held=True)
    assert inbox.MENU_KEY_HINT not in rects[-1][0], "a key that is off is not offered"


# -- a command in the expanded row ---------------------------------------------


def _bash_approval(command: str, **more: Any) -> ApprovalNeeded:
    args = {"command": command, **more}
    return ApprovalNeeded(
        node=("s1",),
        since=1.0,
        summary=summarize("Bash", args),
        approval=PendingApproval(
            id="p1",
            node=("s1",),
            tool_name="Bash",
            tool_use_id="tu-p1",
            raw_args=args,
            summary=summarize("Bash", args),
            requested_at=1.0,
        ),
    )


def test_a_command_is_one_row_for_every_line_of_it() -> None:
    """
    The expanded row is where the three decisions are, so it is a rendering
    the operator approves from. One ``repr`` row puts the second command of
    `ls # '` + newline + `touch pwned #'` wherever the first ends, in a body
    that does not wrap.

    Any other string is itself (``inbox.argument_text``), as the typeset branch
    sets it: a ``repr`` escapes its quotes, and a break in a description changes
    nothing that runs.
    """
    rows = inbox.argument_rows(
        "Bash", {"command": "ls # '\ntouch pwned #'", "description": "list\nthem"}
    )
    assert rows == [
        "  command =",
        "    ls # '\\n",
        "    touch pwned #'",
        "  description = list\nthem",
    ]
    assert inbox.argument_rows("Bash", {"command": "pytest -q"}) == ["  command =", "    pytest -q"]
    # Only the Bash command: a tool that merely has an argument of that name
    # keeps the one-item form every other argument has.
    assert inbox.argument_rows("Mystery", {"command": "a\nb"}) == ["  command = a\nb"]
    assert inbox.argument_rows("Bash", {}) == []


def test_the_expanded_approval_draws_those_rows_and_no_other_form_of_the_command(
    monkeypatch,
) -> None:
    """
    Through ``_expand_approval`` against a recording imgui: what reaches the
    screen is ``argument_rows``, each on a call of its own, and the body is as
    tall as the rows and no taller than the diff's cap.
    """
    from unittest.mock import MagicMock

    from pptmstr.ui import review

    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_text_line_height_with_spacing.return_value = 10.0
    # The body is sized by what each row wraps to: one line each, at this width.
    fake.get_text_line_height.return_value = 10.0
    fake.get_content_region_avail.return_value = _ImVec2(400.0, 400.0)
    fake.get_style.return_value.item_spacing = _ImVec2(0.0, 0.0)
    fake.get_style.return_value.window_padding = _ImVec2(0.0, 5.0)
    fake.calc_text_size.return_value = _ImVec2(100.0, 10.0)
    fake.input_text_with_hint.return_value = (False, "")
    fake.button.return_value = False
    monkeypatch.setattr(inbox, "imgui", fake)

    command = "ls # '\ntouch pwned #'"
    parked = _bash_approval(command)
    inbox._expand_approval(review.ReviewState(), MagicMock(), parked)

    drawn = [str(call.args[1]) for call in fake.text_colored.call_args_list]
    rows = inbox.argument_rows("Bash", parked.approval.raw_args)
    assert drawn[1 : 1 + len(rows)] == rows
    assert "    touch pwned #'" in drawn
    assert not any("touch" in text and "ls" in text for text in drawn), drawn
    assert not any("\n" in text for text in drawn), drawn
    size = fake.begin_child.call_args.args[1]
    assert size.y == 10.0 * (len(rows) + 1) + 10.0

    fake.reset_mock()
    long = _bash_approval("\n".join(f"echo {n}" for n in range(60)))
    inbox._expand_approval(review.ReviewState(), MagicMock(), long)
    assert fake.begin_child.call_args.args[1].y == 240.0
    drawn = [str(call.args[1]) for call in fake.text_colored.call_args_list]
    assert "    echo 59" in drawn, "a line past the cap was not drawn for the child to scroll to"
