"""
The rail and the queue row once an obligation need not mean its node has stopped.

An approval, a question and a failure are read off a node that is parked or dead,
and exclude each other on one node. A decision is a record its asker filed: the
asker may be working, and it sits beside any of the other three. These pin what
that changes on a card -- its height, its badge, its sub-agent count -- and that
every lookup the queue row makes for an obligation has an answer for a decision.

The cards and rows themselves are drawn by imgui and are not drawn here.

The second half is the persisted fleet on the rail: a closed card, a session off
the fleet, and ended sub-agents that are hidden. Those snapshots are built by the real
reducer and drawn through ``rail.draw`` against the stub ``test_inbox_rail`` keeps,
so what is asserted is the strings and buttons the pane submitted. No pixel of it
has been looked at.
"""

from __future__ import annotations

import itertools
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock

import pytest

from pptmstr import theme
from pptmstr.command import Command
from pptmstr.intents import (
    AgentFinished,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    SessionDetached,
    SessionRecalled,
    TaskClaimRequested,
    TaskDeclared,
    TaskReleased,
)
from pptmstr.model import (
    AgentRecord,
    AgentState,
    ApprovalNeeded,
    AskerFate,
    CommandOwed,
    Decision,
    DecisionOwed,
    EndedHow,
    NodeId,
    Obligation,
    ObligationKind,
    PendingApproval,
    QuestionPending,
    Recalled,
    SessionFailed,
    Snapshot,
    Standing,
    Task,
    TranscriptPresence,
)
from pptmstr.store import Store
from pptmstr.theme import DISCLOSURE_GLYPH, STATE_GLYPH
from pptmstr.ui import detail, inbox, rail
from pptmstr.ui.focus import FocusState, OnNode
from tests.test_inbox_rail import Drawn, _collect, _stub_imgui

ROOT: NodeId = ("s1", None)
CHILD: NodeId = ("s1", "agent-a")


def record(
    node: NodeId,
    parent: NodeId | None = None,
    *,
    state: AgentState = AgentState.THINKING,
    agent_type: str | None = None,
) -> AgentRecord:
    return AgentRecord(
        node_id=node,
        parent=parent,
        depth=0 if parent is None else 1,
        state=state,
        topic="working",
        task="reconcile disagreeing TLE sets",
        model="claude-sonnet-5",
        agent_type=agent_type,
        cwd="/x/orbital",
    )


def decision(node: NodeId = ROOT, did: str = "d-1") -> DecisionOwed:
    return DecisionOwed(
        node=node,
        since=1.0,
        summary=f"decision {did} from lead: yours to take; reversible: not stated",
        decision=Decision(
            id=did,
            asker=node,
            question="which schema?",
            blocks=(),
            if_unanswered="",
            evidence="",
            recommendation="",
            standing=Standing.OPERATORS,
            reversible=None,
            raised_at=1.0,
        ),
        asker=AskerFate.LIVE,
    )


def command(node: NodeId = ROOT, n: int = 1) -> CommandOwed:
    cid = f"cmd-20261013T12000{n}Z-{n:012x}"
    return CommandOwed(
        node=node,
        since=4.0,
        summary=f"command asked from lead: 1 line, 12 characters; {cid}",
        command=Command(
            id=cid,
            asker=node,
            asked="make install",
            reason="",
            expects="",
            asked_at=1_790_000_000.0,
        ),
        asker=AskerFate.LIVE,
    )


def approval(node: NodeId = ROOT) -> ApprovalNeeded:
    return ApprovalNeeded(
        node=node,
        since=1.0,
        summary="Write /tmp/x",
        approval=PendingApproval(
            id="p1",
            node=node,
            tool_name="Write",
            tool_use_id="tu",
            raw_args={},
            summary="Write /tmp/x",
            requested_at=1.0,
        ),
    )


def question(node: NodeId = ROOT) -> QuestionPending:
    return QuestionPending(node=node, since=2.0, summary="ended its turn")


def failure(node: NodeId = ROOT) -> SessionFailed:
    return SessionFailed(node=node, since=3.0, summary="died")


ONE_OF_EACH: dict[ObligationKind, Obligation] = {
    ObligationKind.APPROVAL: approval(),
    ObligationKind.QUESTION: question(),
    ObligationKind.FAILURE: failure(),
    ObligationKind.DECISION: decision(),
    ObligationKind.COMMAND: command(),
}


def test_this_file_has_one_of_every_kind() -> None:
    assert set(ONE_OF_EACH) == set(ObligationKind)


# -- height ------------------------------------------------------------------------


def test_a_working_session_that_owes_only_decisions_is_not_blocked() -> None:
    working = record(ROOT, state=AgentState.RUNNING_TOOL)

    assert rail._density(working, [decision(), decision(did="d-2")]) == "active"


@pytest.mark.parametrize("stops", [approval(), question(), failure()], ids=lambda o: o.kind.value)
def test_a_decision_beside_something_that_stops_the_node_leaves_it_blocked(
    stops: Obligation,
) -> None:
    assert rail._density(record(ROOT), [decision(), stops]) == "blocked"
    assert rail._density(record(ROOT), [stops]) == "blocked"


@pytest.mark.parametrize("state", [AgentState.DONE, AgentState.CANCELLED])
def test_a_finished_agent_with_an_open_decision_keeps_the_height_that_has_a_badge(
    state: AgentState,
) -> None:
    """
    The one-line height draws no badge, so an ended card is that height only when
    it owes nothing: otherwise a decision whose asker finished shows on no card.
    """
    ended = record(ROOT, state=state)

    assert rail._density(ended, []) == "ended"
    assert rail._density(ended, [decision()]) == "active"


def test_a_working_session_that_owes_only_a_command_is_not_blocked() -> None:
    """Its asker was told to carry on: nothing on the card has stopped."""
    working = record(ROOT, state=AgentState.RUNNING_TOOL)

    assert rail._density(working, [command(), command(n=2)]) == "active"
    assert rail._density(working, [command(), decision()]) == "active"


@pytest.mark.parametrize("stops", [approval(), question(), failure()], ids=lambda o: o.kind.value)
def test_a_command_beside_something_that_stops_the_node_leaves_it_blocked(
    stops: Obligation,
) -> None:
    assert rail._density(record(ROOT), [command(), stops]) == "blocked"


@pytest.mark.parametrize("state", [AgentState.DONE, AgentState.CANCELLED])
def test_a_finished_agent_with_an_open_command_is_never_the_height_with_no_badge(
    state: AgentState,
) -> None:
    """A command can be running after its asker has finished, and is cancelled from its row."""
    assert rail._density(record(ROOT, state=state), [command()]) == "active"


def test_a_sub_agent_with_only_a_command_open_is_not_counted_as_waiting() -> None:
    subs = [record(CHILD, ROOT, state=AgentState.RUNNING_TOOL, agent_type="builder")]

    working = rail._subs_signal(subs, [command(CHILD)])
    parked = rail._subs_signal(subs, [command(CHILD), approval(CHILD)])

    assert working is not None and "waiting" not in working[0]
    assert parked is not None and "1 waiting" in parked[0]


def test_commands_are_badged_per_kind_and_said_in_words() -> None:
    owed = [command(), approval(), command(n=2), decision()]

    assert rail._badges(owed) == (
        (ObligationKind.APPROVAL, 1),
        (ObligationKind.DECISION, 1),
        (ObligationKind.COMMAND, 2),
    )
    assert rail.commands_waiting(owed) == 2
    assert rail.commands_mark(2) == "2 commands waiting"
    assert rail.commands_mark(1) == "1 command waiting"


# -- the badge ---------------------------------------------------------------------


def _mixes() -> list[list[Obligation]]:
    kinds = list(ObligationKind)
    return [
        [ONE_OF_EACH[kind] for kind in chosen]
        for size in range(1, len(kinds) + 1)
        for chosen in itertools.permutations(kinds, size)
    ]


@pytest.mark.parametrize("owed", _mixes(), ids=lambda owed: "+".join(o.kind.value for o in owed))
def test_a_card_has_one_badge_for_each_kind_it_owes_and_no_other(owed: list[Obligation]) -> None:
    """
    Every mix in every order. The approval mark on a card with nothing awaiting
    approval tells the operator a call is parked where none is.
    """
    badges = rail._badges(owed)

    assert {kind for kind, _ in badges} == {o.kind for o in owed}
    assert all(count == 1 for _, count in badges)
    # Whatever order they were owed in, they are drawn in the one order.
    assert [kind for kind, _ in badges] == [k for k in rail._BADGE_ORDER if k in dict(badges)]


def test_a_parked_call_and_three_decisions_do_not_read_as_four_calls() -> None:
    owed = [decision(did="d-1"), approval(), decision(did="d-2"), decision(did="d-3")]

    assert rail._badges(owed) == ((ObligationKind.APPROVAL, 1), (ObligationKind.DECISION, 3))
    assert sum(count for _, count in rail._badges(owed)) == len(owed)


def test_a_card_that_owes_nothing_has_no_badge() -> None:
    assert rail._badges([]) == ()


def test_the_badge_order_ranks_every_kind_once() -> None:
    """A kind missing from it is an obligation that badges no card."""
    assert sorted(rail._BADGE_ORDER, key=lambda k: k.value) == sorted(
        ObligationKind, key=lambda k: k.value
    )


def test_what_stops_work_is_badged_ahead_of_a_decision() -> None:
    assert [kind for kind, _ in rail._badges([decision(), question(), failure()])] == [
        ObligationKind.FAILURE,
        ObligationKind.QUESTION,
        ObligationKind.DECISION,
    ]


def test_a_finished_agent_with_an_answer_it_has_not_read_keeps_a_height_that_can_say_so() -> None:
    """
    The agent that finished before reading its answer is the case the mark is
    for, and the one-line height draws no state line to put it on.
    """
    for state in (AgentState.DONE, AgentState.CANCELLED):
        ended = record(ROOT, state=state)
        assert rail._density(ended, []) == "ended"
        assert rail._density(ended, [], unread=1) == "active"
    assert rail._density(record(ROOT), [approval()], unread=1) == "blocked"


# -- the sub-agent count -------------------------------------------------------------


def test_a_sub_agent_with_only_a_decision_open_is_not_counted_as_waiting() -> None:
    subs = [record(CHILD, ROOT, state=AgentState.RUNNING_TOOL, agent_type="builder")]

    working = rail._subs_signal(subs, [decision(CHILD)])
    parked = rail._subs_signal(subs, [decision(CHILD), approval(CHILD)])

    assert working is not None and "waiting" not in working[0]
    assert parked is not None and "1 waiting" in parked[0]


def test_a_decision_is_still_counted_on_its_sessions_card() -> None:
    owed = (decision(CHILD), decision(ROOT, "d-2"))
    snap = Snapshot(
        seq=1,
        nodes=MappingProxyType({ROOT: record(ROOT), CHILD: record(CHILD, ROOT)}),
        order=(ROOT, CHILD),
        needs_you=owed,
        any_active=True,
    )

    assert len(rail._by_session(snap)["s1"]) == 2


# -- what the queue row and the detail header look up ----------------------------------


@pytest.mark.parametrize("known", [True, False], ids=["asker has a record", "asker has none"])
def test_every_lookup_a_row_makes_has_an_answer_for_a_decision(known: bool) -> None:
    """
    ``inbox._row`` and ``detail._header`` index three tables by kind and ask two
    functions about the obligation before they draw anything. None may raise for
    a decision, with or without a record behind its asker.
    """
    obligation = decision(CHILD)
    records = (record(ROOT), record(CHILD, ROOT, agent_type="builder")) if known else ()
    snap = Snapshot(
        seq=1,
        nodes=MappingProxyType({r.node_id: r for r in records}),
        order=tuple(r.node_id for r in records),
        needs_you=(obligation,),
        any_active=False,
    )

    assert theme.OBLIGATION_GLYPH[obligation.kind]
    assert theme.THEMES[theme.REQUIRED_THEMES[0]].obligation(obligation.kind)
    assert detail._KIND_LABEL[obligation.kind] == "wants a decision"
    assert inbox.spawn_marker(snap, obligation) is None
    title, qualifier = inbox.identity(snap, obligation)
    assert title == ("reconcile disagreeing TLE sets" if known else "session")
    assert qualifier.endswith("/ builder" if known else "/ sub-agent")
    assert obligation.key == "decision:d-1"


# -- the persisted fleet: what the pane submits -----------------------------------------

LEAD: NodeId = ("live", None)
A: NodeId = ("live", "agent-a")
B: NodeId = ("live", "agent-b")
C: NodeId = ("live", "agent-c")
OLD: NodeId = ("old", None)
# Wall-clock seconds, as a session file holds them, and the reading a frame took.
WALL = 1_800_000_000.0
# The monotonic reading the same frame took. Nothing on a closed card may use it.
MONOTONIC = 100.0


def launch(node: NodeId, parent: NodeId | None = None, *, role: str | None = None) -> AgentSpawned:
    return AgentSpawned(
        node_id=node,
        parent=parent,
        task="reconcile disagreeing TLE sets",
        model="claude-sonnet-5",
        started_at=1.0,
        agent_type=role,
    )


def recall(session: str = "old", *, task: str = "port the parser", **said: Any) -> SessionRecalled:
    return SessionRecalled(
        session_id=session, task=task, model="claude-sonnet-5", recalled=Recalled(**said)
    )


def team(*ended: NodeId) -> Store:
    """A working lead with three sub-agents, those named having finished."""
    store = Store()
    store.apply(launch(LEAD))
    for node, role in ((A, "scout"), (B, "builder"), (C, "skeptic")):
        store.apply(launch(node, LEAD, role=role))
    for node in ended:
        store.apply(AgentFinished(node, AgentState.DONE, ended_at=5.0))
    return store


def opened() -> rail.RailState:
    return rail.RailState(expanded={"live"})


class Frame:
    """
    One ``rail.draw`` against the stub: what it submitted and what it was given.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        snap: Snapshot,
        *,
        state: rail.RailState | None = None,
        cursor: NodeId | None = None,
        clicked: int | None = None,
        hovered: int | None = None,
        **kw: Any,
    ) -> None:
        fake, ring = _stub_imgui(monkeypatch, hovered=hovered, clicked=clicked, mouse=(0.0, 0.0))
        # The New Task line draws a link, which needs a real context; it is drawn
        # in one in tests/test_fleet_empty.py. Here, that it was drawn, and with what.
        self.new_task: list[object] = []
        monkeypatch.setattr(rail, "new_task_line", self.new_task.append)
        self.menus: list[NodeId] = []
        monkeypatch.setattr(
            rail.session_menu, "draw_popup", lambda _w, rec, **_kw: self.menus.append(rec.node_id)
        )
        self.focus = FocusState(target=OnNode(cursor, pinned=True) if cursor else None)
        self.target = self.focus.target
        self.state = state if state is not None else rail.RailState()
        rail.draw(snap, self.focus, self.state, MONOTONIC, MagicMock(), **kw)
        self.out: Drawn = _collect(fake, ring)
        self.fake = fake
        self.empty = [str(c.args[0]) for c in fake.text_disabled.call_args_list]

    @property
    def text(self) -> list[str]:
        return self.out.text

    def tone(self, text: str) -> Any:
        """The colour one drawn string was given."""
        return next(c.args[0] for c in self.fake.text_colored.call_args_list if c.args[1] == text)


# -- a closed card ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ended_how", "words"),
    [
        (EndedHow.CLOSED, "closed"),
        (EndedHow.SHUTDOWN, "closed"),
        (EndedHow.FAILED, "failed, then closed"),
        (None, "ended without closing"),
    ],
    ids=["closed", "shutdown", "failed", "not said"],
)
@pytest.mark.parametrize(
    "presence", [TranscriptPresence.PRESENT, TranscriptPresence.UNKNOWN], ids=lambda p: p.value
)
def test_a_closed_card_draws_its_title_and_the_files_account_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
    ended_how: EndedHow | None,
    words: str,
    presence: TranscriptPresence,
) -> None:
    """
    The whole of what the pane drew, so a spend, a state label, an elapsed time or
    a ring slipped onto the card fails here whatever it reads.
    """
    store = Store()
    store.apply(
        recall(
            ended_how=ended_how,
            last_active=WALL - 3 * 3600 - 59,
            transcript=presence,
            cost_usd=4.2,
            error="boom",
        )
    )

    frame = Frame(monkeypatch, store.snapshot(), now_wall=WALL)

    assert frame.text == [
        "UNFILED",
        "1 closed",
        STATE_GLYPH[AgentState.DONE],
        "port the parser",
        f"{words} · 3h ago",
    ]
    assert frame.out.rings == 0
    assert frame.tone(f"{words} · 3h ago") == rail.P.text_dim.vec4
    assert frame.tone(STATE_GLYPH[AgentState.DONE]) == rail.P.text_dim.vec4
    line_h = 18.0
    assert frame.out.cards == [(300.0, line_h * rail._LINES["ended"] + rail._PAD * 2)]


def test_a_closed_card_never_reads_as_a_finished_run_that_cost_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The record is DONE with zero usage because those fields cannot say nothing. A
    session launched and finished here draws both; the recalled one beside it must
    draw neither.
    """
    store = Store()
    store.apply(launch(LEAD))
    store.apply(AgentFinished(LEAD, AgentState.DONE, ended_at=5.0))
    ran_here = Frame(monkeypatch, store.snapshot(), now_wall=WALL).text
    assert "done" in ran_here and "$0.00" in ran_here

    store = Store()
    store.apply(recall(ended_how=EndedHow.CLOSED, last_active=WALL - 60))
    closed = Frame(monkeypatch, store.snapshot(), now_wall=WALL).text

    assert not any("done" in line or "$" in line for line in closed)
    assert "closed · 1m ago" in closed


@pytest.mark.parametrize(
    ("said", "now_wall"),
    [({}, WALL), ({"last_active": WALL - 60}, None), ({}, None)],
    ids=["the file has no time", "the frame was given no wall clock", "neither"],
)
def test_a_closed_card_with_no_age_to_give_says_so(
    monkeypatch: pytest.MonkeyPatch, said: dict[str, float], now_wall: float | None
) -> None:
    store = Store()
    store.apply(recall(ended_how=EndedHow.CLOSED, **said))

    frame = Frame(monkeypatch, store.snapshot(), now_wall=now_wall)

    assert frame.text[-1] == "closed · last active not known"


def test_a_closed_cards_age_is_not_measured_against_the_frames_monotonic_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``now`` is seconds since boot. A wall time subtracted from it is a large
    negative number, which a clamp would draw as "<1m ago" for a week-old session.
    """
    store = Store()
    store.apply(recall(ended_how=EndedHow.CLOSED, last_active=WALL - 7 * 86400))

    assert Frame(monkeypatch, store.snapshot(), now_wall=WALL).text[-1] == "closed · 7d ago"


@pytest.mark.parametrize("ended_how", [EndedHow.CLOSED, EndedHow.FAILED, None])
def test_a_closed_card_whose_transcript_is_gone_says_that_and_warns(
    monkeypatch: pytest.MonkeyPatch, ended_how: EndedHow | None
) -> None:
    store = Store()
    store.apply(
        recall(ended_how=ended_how, last_active=WALL - 60, transcript=TranscriptPresence.GONE)
    )

    frame = Frame(monkeypatch, store.snapshot(), now_wall=WALL)

    assert frame.text[-2:] == ["port the parser", "transcript gone — can only be removed"]
    assert frame.tone(rail.TRANSCRIPT_GONE) == rail.P.warn.vec4
    assert not any("ago" in line or "done" in line or "$" in line for line in frame.text)


def test_a_closed_card_whose_file_recorded_no_task_is_titled_by_its_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store()
    store.apply(recall("0f3a-session", task="", ended_how=EndedHow.CLOSED))

    assert "0f3a-session" in Frame(monkeypatch, store.snapshot()).text


def test_a_closed_card_is_the_one_line_height_whatever_else_is_asked() -> None:
    store = Store()
    store.apply(recall(ended_how=EndedHow.FAILED))
    rec = store.snapshot().nodes[OLD]

    assert rail._density(rec, [], unread=3) == "ended"
    assert rail._density(rec, [question(OLD)]) == "ended"


@pytest.mark.parametrize(
    ("seconds", "reads"),
    [
        (-5.0, "<1m ago"),
        (59.9, "<1m ago"),
        (60.0, "1m ago"),
        (3599.0, "59m ago"),
        (3600.0, "1h ago"),
        (86399.0, "23h ago"),
        (86400.0, "1d ago"),
        (40 * 86400.0, "40d ago"),
    ],
)
def test_an_age_is_one_unit_and_never_overstated(seconds: float, reads: str) -> None:
    assert rail._ago(seconds) == reads


def test_a_resumed_session_draws_as_a_running_one_again(monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store()
    store.apply(recall(ended_how=EndedHow.CLOSED, last_active=WALL - 60))
    store.apply(launch(OLD))

    frame = Frame(monkeypatch, store.snapshot(), now_wall=WALL)

    assert "1 session" in frame.text
    assert not any("closed" in line for line in frame.text)
    assert frame.out.rings == 1


# -- the project header -----------------------------------------------------------------


def test_the_header_counts_closed_sessions_apart_from_the_ones_run_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store()
    store.apply(launch(LEAD))
    store.apply(launch(("live-2", None)))
    assert "2 sessions" in Frame(monkeypatch, store.snapshot()).text

    store.apply(recall("old"))
    store.apply(recall("older"))
    frame = Frame(monkeypatch, store.snapshot())
    assert "2 sessions · 2 closed" in frame.text
    assert "2 sessions" not in frame.text and "4 sessions" not in frame.text

    only_closed = Store()
    only_closed.apply(recall("old"))
    assert "1 closed" in Frame(monkeypatch, only_closed.snapshot()).text


# -- off the fleet ----------------------------------------------------------------------


def test_a_session_off_the_fleet_is_not_drawn_and_not_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store()
    store.apply(launch(LEAD))
    store.apply(recall("old", task="port the parser"))
    store.apply(recall("older", task="rename the module"))
    before = Frame(monkeypatch, store.snapshot())
    assert "1 session · 2 closed" in before.text and len(before.out.cards) == 3

    store.apply(SessionDetached("old"))
    snap = store.snapshot()
    assert snap.off_fleet("old")

    frame = Frame(monkeypatch, snap)

    assert "port the parser" not in frame.text
    assert "rename the module" in frame.text
    assert "1 session · 1 closed" in frame.text
    assert len(frame.out.cards) == 2
    assert OLD not in frame.menus
    # Left out of the drawing, not out of the store.
    assert OLD in snap.nodes


def test_a_fleet_with_every_session_removed_says_it_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The snapshot still holds the record, so a pane keyed on that draws no header,
    no card and no text at all.
    """
    store = Store()
    store.apply(recall("old"))
    store.apply(SessionDetached("old"))
    snap = store.snapshot()
    assert snap.order and snap.off_fleet("old")

    frame = Frame(monkeypatch, snap)

    assert frame.empty == ["no sessions"]
    assert frame.new_task == [], "no line where there is nothing for it to do"
    assert frame.text == [] and frame.out.cards == []

    act = MagicMock()
    offered = Frame(monkeypatch, snap, new_task=act)
    assert offered.empty == ["no sessions"]
    assert offered.new_task == [act], "the New Task line, in place of 'the launcher below'"


def test_a_project_whose_sessions_were_all_removed_has_no_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store()
    store.apply(launch(LEAD))
    store.apply(SessionRecalled("old", "port the parser", "m", Recalled(), cwd="/x/elsewhere"))
    assert "ELSEWHERE" in Frame(monkeypatch, store.snapshot()).text

    store.apply(SessionDetached("old"))

    assert "ELSEWHERE" not in Frame(monkeypatch, store.snapshot()).text


def test_a_removed_session_that_comes_to_owe_is_drawn_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What is on the fleet is ``Snapshot.off_fleet``, not the flag the operator set."""
    store = Store()
    store.apply(recall("old"))
    store.apply(SessionDetached("old"))
    store.apply(launch(OLD))

    assert "reconcile disagreeing TLE sets" in Frame(monkeypatch, store.snapshot()).text


# -- hiding ended sub-agents ---------------------------------------------------------------


def without(*absent: NodeId) -> Store:
    """``team()`` as it would be had the sub-agents named never been spawned."""
    store = Store()
    store.apply(launch(LEAD))
    for node, role in ((A, "scout"), (B, "builder"), (C, "skeptic")):
        if node not in absent:
            store.apply(launch(node, LEAD, role=role))
    return store


def same_frame(one: Frame, other: Frame) -> bool:
    """Whether two frames submitted the same strings, buttons and rectangles."""
    return (
        one.text == other.text
        and one.out.cards == other.out.cards
        and one.out.origins == other.out.origins
        and one.out.fills == other.out.fills
        and one.out.marks == other.out.marks
        and one.menus == other.menus
    )


def lit(frame: Frame) -> list[float]:
    """The top edge of every card drawn with the focused outline."""
    return [m[0].y for m in frame.out.marks if m[4] == 2.0]


def test_with_the_setting_off_every_sub_agent_is_drawn(monkeypatch: pytest.MonkeyPatch) -> None:
    for store in (team(A, B), team(A, B, C)):
        frame = Frame(monkeypatch, store.snapshot(), state=opened(), hide_ended=False)

        assert len(frame.out.cards) == 4
        assert {"scout", "builder", "skeptic"} <= set(frame.text)

    assert any("3 subs" in line for line in Frame(monkeypatch, team(A, B, C).snapshot()).text)
    # Unasked is off: the pane hides nothing a caller did not ask it to.
    assert len(Frame(monkeypatch, team(A, B).snapshot(), state=opened()).out.cards) == 4


@pytest.mark.parametrize("is_open", [True, False], ids=["group open", "group collapsed"])
def test_a_hidden_sub_agent_has_no_row_and_nothing_counts_or_names_it(
    monkeypatch: pytest.MonkeyPatch, is_open: bool
) -> None:
    """
    The whole property in one comparison: the pane submits for a session with two
    hidden sub-agents exactly what it submits for a session that never had them.
    """

    def frame(store: Store) -> Frame:
        state = opened() if is_open else rail.RailState()
        return Frame(monkeypatch, store.snapshot(), state=state, hide_ended=True)

    hidden = frame(team(A, B))

    assert same_frame(hidden, frame(without(A, B)))
    assert "scout" not in hidden.text and "builder" not in hidden.text
    assert not any("ended" in line or "3 sub" in line or "2 " in line for line in hidden.text)
    assert len(hidden.out.cards) == (2 if is_open else 1)
    if not is_open:
        assert f"{DISCLOSURE_GLYPH[False]} 1 sub" in hidden.text


@pytest.mark.parametrize("is_open", [True, False], ids=["group open", "group collapsed"])
def test_a_session_whose_every_sub_agent_is_hidden_draws_as_one_that_has_none(
    monkeypatch: pytest.MonkeyPatch, is_open: bool
) -> None:
    state = opened() if is_open else rail.RailState()

    hidden = Frame(monkeypatch, team(A, B, C).snapshot(), state=state, hide_ended=True)
    alone = Frame(monkeypatch, without(A, B, C).snapshot(), state=state, hide_ended=True)

    assert same_frame(hidden, alone)
    assert len(hidden.out.cards) == 1
    assert not any("sub" in line or DISCLOSURE_GLYPH[True] in line for line in hidden.text)
    # Not closed by the draw: the operator's choice stands for when one comes back.
    assert state.expanded == ({"live"} if is_open else set())


def test_no_click_brings_a_hidden_sub_agent_back(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = team(A, B).snapshot()
    state = opened()

    for button in range(4):
        Frame(monkeypatch, snap, state=state, hide_ended=True, clicked=button)
        again = Frame(monkeypatch, snap, state=state, hide_ended=True)
        assert "scout" not in again.text and "builder" not in again.text
        assert len(again.out.cards) <= 2


def test_hiding_leaves_the_snapshot_and_the_cursor_as_they_were(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team(A, B)
    snap = store.snapshot()
    nodes, order = dict(snap.nodes), snap.order

    frame = Frame(monkeypatch, snap, state=opened(), cursor=C, hide_ended=True)

    assert store.snapshot() is snap
    assert dict(snap.nodes) == nodes and snap.order == order
    assert frame.focus.target is frame.target
    assert frame.focus.node(snap) == C


def test_an_ended_sub_agent_the_operator_owes_is_drawn(monkeypatch: pytest.MonkeyPatch) -> None:
    store = team(A, B)
    store.apply(
        ApprovalRequested(
            A,
            PendingApproval(
                id="p1",
                node=A,
                tool_name="Write",
                tool_use_id="tu",
                raw_args={},
                summary="Write x",
                requested_at=6.0,
            ),
        )
    )
    snap = store.snapshot()
    assert snap.nodes[A].state is AgentState.DONE and [o.node for o in snap.needs_you] == [A]

    frame = Frame(monkeypatch, snap, state=opened(), hide_ended=True)

    assert "scout" in frame.text and "builder" not in frame.text
    assert len(frame.out.cards) == 3
    collapsed = Frame(monkeypatch, snap, hide_ended=True)
    assert f"{DISCLOSURE_GLYPH[False]} 2 subs · 1 waiting" in collapsed.text


def test_an_ended_sub_agent_holding_a_claim_is_drawn_until_it_lets_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    store.apply(TaskDeclared(Task(id="t1", title="one"), node_id=LEAD))
    store.apply(TaskClaimRequested(B, request_id="k1", task_id="t1"))
    store.apply(AgentFinished(A, AgentState.DONE, ended_at=5.0))
    store.apply(AgentFinished(B, AgentState.DONE, ended_at=5.0))
    assert store.snapshot().tasks["t1"].claimed_by == B

    holding = Frame(monkeypatch, store.snapshot(), state=opened(), hide_ended=True)
    assert "builder" in holding.text and "scout" not in holding.text
    assert len(holding.out.cards) == 3

    store.apply(TaskReleased(B, "t1"))
    released = Frame(monkeypatch, store.snapshot(), state=opened(), hide_ended=True)
    assert "builder" not in released.text
    assert len(released.out.cards) == 2


def test_a_sub_agent_that_is_woken_is_drawn_again(monkeypatch: pytest.MonkeyPatch) -> None:
    store = team(A, B, C)
    state = opened()
    assert len(Frame(monkeypatch, store.snapshot(), state=state, hide_ended=True).out.cards) == 1

    store.apply(AgentResumed(A, at=9.0))
    frame = Frame(monkeypatch, store.snapshot(), state=state, hide_ended=True)

    # The group was open when its last member was hidden, and is open again.
    assert "scout" in frame.text
    assert "builder" not in frame.text and "skeptic" not in frame.text
    assert len(frame.out.cards) == 2
    assert f"{DISCLOSURE_GLYPH[True]}" in frame.text


def test_a_group_with_nothing_to_hide_draws_as_it_does_unasked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = team().snapshot()

    asked = Frame(monkeypatch, snap, state=opened(), hide_ended=True, now_wall=WALL)
    unasked = Frame(monkeypatch, snap, state=opened())

    assert same_frame(asked, unasked)


# -- the cursor on a sub-agent that has no card ---------------------------------------------


@pytest.mark.parametrize("is_open", [True, False], ids=["group open", "group collapsed"])
def test_the_cursor_does_not_keep_a_hidden_sub_agent_drawn_and_lights_its_lead(
    monkeypatch: pytest.MonkeyPatch, is_open: bool
) -> None:
    snap = team(A, B).snapshot()
    state = opened() if is_open else rail.RailState()

    frame = Frame(monkeypatch, snap, state=state, cursor=A, hide_ended=True)

    assert "scout" not in frame.text
    assert len(frame.out.cards) == (2 if is_open else 1)
    # One outline, and it is on the lead's card: the first one drawn.
    assert lit(frame) == [frame.out.origins[0][1]]
    assert frame.out.scrolls == 1
    # The pane moved nothing. The cursor is where the operator left it.
    assert frame.focus.target is frame.target
    assert frame.focus.node(snap) == A


def test_the_cursor_on_a_drawn_sub_agent_lights_that_card_and_not_its_lead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = team(A, B).snapshot()

    hiding = Frame(monkeypatch, snap, state=opened(), cursor=C, hide_ended=True)
    assert lit(hiding) == [hiding.out.origins[1][1]]

    # With the setting off the ended one has a card, and that card is the cursor's.
    showing = Frame(monkeypatch, snap, state=opened(), cursor=A)
    assert lit(showing) == [showing.out.origins[1][1]]


def test_a_hidden_sub_agent_under_the_cursor_lights_no_other_sessions_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team(A, B)
    store.apply(launch(("second", None)))
    store.apply(launch(("second", "agent-z"), ("second", None), role="scout"))
    state = rail.RailState(expanded={"live", "second"})

    frame = Frame(monkeypatch, store.snapshot(), state=state, cursor=A, hide_ended=True)

    assert lit(frame) == [frame.out.origins[0][1]]


def test_which_card_the_cursor_lights_in_an_open_group() -> None:
    drawn = {C}

    assert rail._lit(None, LEAD, drawn) is None
    assert rail._lit(LEAD, LEAD, drawn) == LEAD
    assert rail._lit(C, LEAD, drawn) == C
    assert rail._lit(A, LEAD, drawn) == LEAD
    assert rail._lit(OLD, LEAD, drawn) == OLD
    assert rail._lit(("old", "agent-a"), LEAD, drawn) == ("old", "agent-a")
