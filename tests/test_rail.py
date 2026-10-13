"""
The rail and the queue row once an obligation need not mean its node has stopped.

An approval, a question and a failure are read off a node that is parked or dead,
and exclude each other on one node. A decision is a record its asker filed: the
asker may be working, and it sits beside any of the other three. These pin what
that changes on a card -- its height, its badge, its sub-agent count -- and that
every lookup the queue row makes for an obligation has an answer for a decision.

The cards and rows themselves are drawn by imgui and are not drawn here.

The second half is the persisted fleet on the rail: a closed card, a session off
the fleet, and the fold of ended sub-agents. Those snapshots are built by the real
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
    SessionDetached,
    SessionRecalled,
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
    TranscriptPresence,
)
from pptmstr.store import Store
from pptmstr.theme import DISCLOSURE_GLYPH, STATE_GLYPH
from pptmstr.ui import detail, fleet_view, inbox, rail
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


def fold_rows(frame: Frame) -> list[str]:
    return [line for line in frame.text if "ended" in line]


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

    assert "1 sessions" in frame.text
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
    assert "1 sessions · 2 closed" in before.text and len(before.out.cards) == 3

    store.apply(SessionDetached("old"))
    snap = store.snapshot()
    assert snap.off_fleet("old")

    frame = Frame(monkeypatch, snap)

    assert "port the parser" not in frame.text
    assert "rename the module" in frame.text
    assert "1 sessions · 1 closed" in frame.text
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

    assert frame.empty == ["no sessions", "start one from the launcher below."]
    assert frame.text == [] and frame.out.cards == []


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


# -- the fold ---------------------------------------------------------------------------


def test_nothing_is_folded_unless_the_caller_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = team(A, B).snapshot()

    frame = Frame(monkeypatch, snap, state=opened())

    assert len(frame.out.cards) == 4
    assert fold_rows(frame) == []
    assert {"scout", "builder", "skeptic"} <= set(frame.text)


def test_ended_sub_agents_fold_into_one_row_that_counts_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team(A, B)
    snap = store.snapshot()

    frame = Frame(monkeypatch, snap, state=opened(), hide_ended=True)

    assert "skeptic" in frame.text
    assert "scout" not in frame.text and "builder" not in frame.text
    assert fold_rows(frame) == [f"{DISCLOSURE_GLYPH[False]} 2 ended"]
    # The root, the one still working, and the row.
    assert len(frame.out.cards) == 3
    # Drawn after the members and at their indent, inside the bracket.
    assert frame.out.origins[2][0] == frame.out.origins[1][0] > frame.out.origins[0][0]
    assert frame.out.origins[2][1] > frame.out.origins[1][1]


def test_the_fold_leaves_the_snapshot_and_the_cursor_as_they_were(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team(A, B)
    snap = store.snapshot()
    nodes, order = dict(snap.nodes), snap.order

    frame = Frame(monkeypatch, snap, state=opened(), cursor=C, hide_ended=True, clicked=2)

    assert frame.state.unfolded == {LEAD}, "the click did not reach the row"
    assert store.snapshot() is snap
    assert dict(snap.nodes) == nodes and snap.order == order
    assert frame.focus.target is frame.target
    assert frame.focus.node(snap) == C


def test_the_fold_row_opens_and_stays_open_across_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = team(A, B, C).snapshot()
    state = opened()

    closed = Frame(monkeypatch, snap, state=state, hide_ended=True)
    assert len(closed.out.cards) == 2
    assert fold_rows(closed) == [f"{DISCLOSURE_GLYPH[False]} 3 ended"]

    Frame(monkeypatch, snap, state=state, hide_ended=True, clicked=1)
    assert state.unfolded == {LEAD}

    for _ in range(2):
        shown = Frame(monkeypatch, snap, state=state, hide_ended=True)
        assert state.unfolded == {LEAD}
        assert {"scout", "builder", "skeptic"} <= set(shown.text)
        assert fold_rows(shown) == [f"{DISCLOSURE_GLYPH[True]} 3 ended, shown"]
        assert len(shown.out.cards) == 5

    # The row is the last button now that the members are drawn above it.
    Frame(monkeypatch, snap, state=state, hide_ended=True, clicked=4)
    assert state.unfolded == set()
    assert len(Frame(monkeypatch, snap, state=state, hide_ended=True).out.cards) == 2


def test_the_fold_row_is_not_a_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Clicked and hovered -- which the stub also reports as a right button released
    over it -- it selects nothing, opens no menu and is never the menu key's card.
    """
    snap = team(A, B).snapshot()

    frame = Frame(monkeypatch, snap, state=opened(), cursor=C, hide_ended=True, clicked=2)

    assert frame.menus == [LEAD, C]
    assert frame.focus.target is frame.target
    # One focused outline, and it is the cursor's card, not the row.
    assert [m[0].y for m in frame.out.marks if m[4] == 2.0] == [frame.out.origins[1][1]]
    assert len(frame.out.marks) == 2


def test_a_sub_agent_that_is_woken_is_drawn_again(monkeypatch: pytest.MonkeyPatch) -> None:
    store = team(A, B)
    state = opened()
    assert "scout" not in Frame(monkeypatch, store.snapshot(), state=state, hide_ended=True).text

    store.apply(AgentResumed(A, at=9.0))
    frame = Frame(monkeypatch, store.snapshot(), state=state, hide_ended=True)

    assert "scout" in frame.text
    assert fold_rows(frame) == [f"{DISCLOSURE_GLYPH[False]} 1 ended"]


def test_the_card_under_the_cursor_is_not_folded_until_the_cursor_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = team(A, B).snapshot()
    state = opened()

    on_it = Frame(monkeypatch, snap, state=state, cursor=A, hide_ended=True)
    assert "scout" in on_it.text
    assert fold_rows(on_it) == [f"{DISCLOSURE_GLYPH[False]} 1 ended"]

    left = Frame(monkeypatch, snap, state=state, cursor=C, hide_ended=True)
    assert "scout" not in left.text
    assert fold_rows(left) == [f"{DISCLOSURE_GLYPH[False]} 2 ended"]


def test_a_collapsed_group_has_no_fold_row_and_still_counts_every_sub_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = team(A, B).snapshot()

    frame = Frame(monkeypatch, snap, hide_ended=True)

    assert len(frame.out.cards) == 1
    assert fold_rows(frame) == []
    assert any("3 subs" in line for line in frame.text)


def test_a_group_with_nothing_to_fold_has_no_row_and_draws_as_it_does_unasked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = team().snapshot()

    asked = Frame(monkeypatch, snap, state=opened(), hide_ended=True, now_wall=WALL)
    unasked = Frame(monkeypatch, snap, state=opened())

    assert asked.text == unasked.text
    assert asked.out.cards == unasked.out.cards and asked.out.origins == unasked.out.origins
    assert asked.out.fills == unasked.out.fills and asked.out.marks == unasked.out.marks


def test_the_bracket_runs_to_the_bottom_of_the_fold_row(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = team(A, B, C).snapshot()

    frame = Frame(monkeypatch, snap, state=opened(), hide_ended=True)

    row_top = frame.out.origins[1][1]
    bracket = frame.out.fills[-1]
    assert bracket[1].y == row_top + 18.0


def test_the_row_reads_what_the_fold_counted() -> None:
    snap = team(A, B).snapshot()

    assert rail.fold_label(fleet_view.fold(snap, LEAD, None, True)) == "2 ended"
    assert rail.fold_label(fleet_view.fold(snap, LEAD, None, True, {LEAD})) == "2 ended, shown"


def test_an_opened_fold_is_forgotten_with_its_session() -> None:
    state = rail.RailState(unfolded={LEAD, OLD})
    store = Store()
    store.apply(launch(LEAD))

    state.prune(store.snapshot())

    assert state.unfolded == {LEAD}
