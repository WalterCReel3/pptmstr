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
from pptmstr.ui import inbox, rail

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


def test_a_mixed_badge_does_not_claim_one_kind() -> None:
    mixed = [approval(ROOT), QuestionPending(node=ROOT, since=2.0, summary="x")]
    assert rail._badge_kind(mixed) is ObligationKind.APPROVAL
    assert rail._badge_kind([SessionFailed(ROOT, 1.0, "died")]) is ObligationKind.FAILURE


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
    assert text[at : at + 6] == ["scout", "1m40s", "thinking", "working", "sonnet-5", "$0.00"]


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


# -- the cold-start splash is wired to the empty fleet, not to the empty queue ------
#
# Three states reach this pane and only one of them is the splash. The splash core
# has its own tests and every one of them passes with `_splash` called from nowhere
# at all, so what is checked here is the branch: which of the three renderings
# `draw` actually reaches. STYLE.md §2, "test the wiring, not only the unit".


def _splash_branch(monkeypatch, snap: Snapshot) -> list[str]:
    """
    Which of ``draw``'s three exits ran, with the pixels stubbed out.

    Every branch is stubbed, including the splash, so the answer is the branch taken
    and not a side effect of one of them happening to draw something recognisable.
    """
    from unittest.mock import MagicMock

    taken: list[str] = []
    monkeypatch.setattr(inbox, "imgui", MagicMock())
    monkeypatch.setattr(inbox, "_splash", lambda now: taken.append("splash"))
    monkeypatch.setattr(inbox, "_zero_state", lambda snap, now: taken.append("zero"))
    monkeypatch.setattr(inbox, "_row", lambda *a, **k: taken.append("row"))
    monkeypatch.setattr(inbox, "_batch_controls", MagicMock())

    focus = MagicMock()
    focus.obligation.return_value = None
    inbox.draw(snap, focus, MagicMock(), MagicMock(), MagicMock(), MagicMock(), 100.0)
    return taken


def test_an_empty_fleet_gets_the_splash(monkeypatch) -> None:
    """Cold start: nothing has ever run, so there is no "everyone" to report on."""
    assert _splash_branch(monkeypatch, Snapshot.empty()) == ["splash"]


def test_a_fleet_that_owes_nothing_keeps_its_own_empty_state(monkeypatch) -> None:
    """
    The defect this ordering exists to prevent: the splash reappearing over a running
    fleet the moment its queue drains. ``_zero_state`` is a considered treatment of a
    *different* emptiness and the splash must not clobber it.
    """
    assert _splash_branch(monkeypatch, snapshot(record(ROOT))) == ["zero"]


def test_a_fleet_with_obligations_gets_neither(monkeypatch) -> None:
    snap = snapshot(record(ROOT), needs_you=(approval(ROOT),))
    assert _splash_branch(monkeypatch, snap) == ["row"]


def test_the_two_empty_states_cannot_both_render(monkeypatch) -> None:
    """
    Structural, not incidental. An empty ``order`` implies an empty ``needs_you``, so
    both conditions are true at a cold start and only the ordering of the returns
    decides that one rendering happens rather than two.
    """
    taken = _splash_branch(monkeypatch, Snapshot.empty())
    assert taken == ["splash"]
    assert "zero" not in taken


def test_the_splash_asks_fit_size_rather_than_choosing_a_size(monkeypatch) -> None:
    """
    The pane must not hardcode a size. It was specified against a floor that has since
    moved from 10 to 6, and a constant copied out of splash.py at the time would still
    render -- just never at the sizes the new floor was introduced to reach.
    """
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_content_region_avail.return_value = _ImVec2(900.0, 700.0)
    fake.get_cursor_pos_x.return_value = 0.0
    monkeypatch.setattr(inbox, "imgui", fake)

    seen: list[tuple[float, float, int, int]] = []

    def spy(avail_w, avail_h, rows, cols, **kw):
        seen.append((avail_w, avail_h, rows, cols))
        return 8.0

    monkeypatch.setattr(inbox.splash, "fit_size", spy)
    inbox._art(3.0)

    assert seen == [(900.0, 700.0, inbox._ART_ROWS, inbox._ART_COLS)]
    # And the size it answered is the size that was pushed, not one of its own.
    assert fake.push_font.call_args.args[1] == 8.0


def test_the_art_is_skipped_when_it_does_not_fit(monkeypatch) -> None:
    """
    ``fit_size`` clamps up to its floor instead of reporting failure, so a region
    smaller than the floor gets an answer that does not fit. A clipped half-silhouette
    reads as a rendering fault; nothing reads as deliberate.
    """
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_content_region_avail.return_value = _ImVec2(40.0, 30.0)
    fake.get_cursor_pos_x.return_value = 0.0
    monkeypatch.setattr(inbox, "imgui", fake)

    inbox._art(3.0)

    fake.push_font.assert_not_called()
    fake.text_unformatted.assert_not_called()


def test_the_art_is_drawn_as_one_item_so_the_rows_are_not_spaced_apart(monkeypatch) -> None:
    """
    ImGui advances a multi-line block by the font size per line, which is what
    splash.LINE_PITCH_EM measures. One ``text_unformatted`` per row would insert
    style.item_spacing.y between them and stand the art 60 spacings taller than
    ``required_extent`` promised -- overflowing the region that was just checked.
    """
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_content_region_avail.return_value = _ImVec2(2000.0, 2000.0)
    fake.get_cursor_pos_x.return_value = 0.0
    monkeypatch.setattr(inbox, "imgui", fake)

    inbox._art(3.0)

    assert fake.text_unformatted.call_count == 1
    drawn = fake.text_unformatted.call_args.args[0]
    assert drawn.count("\n") == inbox._ART_ROWS - 1
    assert len(drawn.split("\n")) == inbox._ART_ROWS


def test_the_hint_names_the_chord_rather_than_only_colouring_it(monkeypatch) -> None:
    """
    STYLE.md §1: hue is never the only channel. On ``high_contrast`` the accent and
    the body text collapse toward each other, and a hint discoverable only by being a
    different colour is no hint at all there.
    """
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_content_region_avail.return_value = _ImVec2(10.0, 10.0)
    fake.get_cursor_screen_pos.return_value = _ImVec2(0.0, 0.0)
    fake.calc_text_size.return_value = _ImVec2(7.0, 14.0)
    fake.get_text_line_height_with_spacing.return_value = 18.0
    fake.get_cursor_pos_x.return_value = 0.0
    monkeypatch.setattr(inbox, "imgui", fake)

    inbox._splash(3.0)

    coloured = [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]
    disabled = [str(c.args[0]) for c in fake.text_disabled.call_args_list if c.args]
    assert "Ctrl+N" in coloured
    assert any("start a session" in line for line in disabled)


def test_the_quote_never_drops_a_character_below_the_readable_floor(monkeypatch) -> None:
    """
    The pane must draw every character the frame carries. A renderer that skipped the
    dim end of the sweep would leave holes in the prose at exactly the moment the
    highlight is elsewhere.
    """
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_cursor_screen_pos.return_value = _ImVec2(0.0, 0.0)
    fake.calc_text_size.return_value = _ImVec2(7.0, 14.0)
    fake.get_text_line_height_with_spacing.return_value = 18.0
    draw_list = MagicMock()
    fake.get_window_draw_list.return_value = draw_list
    monkeypatch.setattr(inbox, "imgui", fake)

    inbox._quote(3.0)

    from pptmstr.ui.splash import QUOTE_LINES

    expected = sum(len(line.replace(" ", "")) for line in QUOTE_LINES)
    assert draw_list.add_text.call_count == expected


def test_a_fleet_of_crashed_sessions_is_not_an_empty_application(monkeypatch) -> None:
    """
    The case the trigger is keyed on ``nodes`` for. Every session terminal is not the
    same state as no session ever having existed: these are exactly the sessions an
    operator still has to see and dismiss, and a splash drawn over them would hide
    the wreckage behind an invitation to start something new.

    Note this is a different question from the one ``_zero_state`` asks internally --
    it computes a *live* set that excludes terminal and FAILED nodes, so both of
    these snapshots look alike to it and neither may reach the splash.
    """
    crashed = snapshot(
        record(ROOT, state=AgentState.FAILED),
        record(OTHER, state=AgentState.FAILED),
    )
    assert _splash_branch(monkeypatch, crashed) == ["zero"]


def test_the_trigger_asks_the_node_table_not_the_walk(monkeypatch) -> None:
    """
    ``store._preorder`` re-attaches orphans at the root so the walk can never be
    shorter than the node table, which makes ``order`` and ``nodes`` equivalent
    today. This pins the choice against that recovery regressing: a node table with
    something in it is a fleet, whatever the walk managed to reach.
    """
    import dataclasses

    populated = snapshot(record(ROOT))
    lost_the_walk = dataclasses.replace(populated, order=())

    assert lost_the_walk.nodes
    assert _splash_branch(monkeypatch, lost_the_walk) == ["zero"]
