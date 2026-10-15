"""
What a reducer step means for a session's file, the thread that writes the files,
and the read of the fleet at startup.

``changes`` is held to the reducer itself: generated sequences of every intent
the union has go through ``store._apply``, the derived changes are folded into a
dict per session, and after every step the dict is compared with what the
snapshot says. The writer is run against real files under ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import pytest

from pptmstr import boardlog, fleetsync, sessionfile
from pptmstr.approval import Policy
from pptmstr.boardlog import Clock
from pptmstr.command import Command, End, Exited, Lapsed
from pptmstr.fleetsync import Change, FleetWriter, Latched, Outcome, Spent, changes
from pptmstr.intents import (
    AgentFinished,
    AgentRemoved,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    BoardRead,
    BoardRestored,
    CommandApproved,
    CommandEnded,
    CommandRefused,
    CommandRequested,
    CommandsRead,
    CommandsRestored,
    CommandStopAsked,
    CommandWithdrawn,
    CompactionObserved,
    ConcernEdited,
    ConcernPosted,
    ConcernWithdrawn,
    ContextPolled,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionsRead,
    DecisionWithdrawn,
    DigestRead,
    FailedSessionClosed,
    FailureAcknowledged,
    InboxRead,
    Intent,
    SessionDetached,
    SessionRecalled,
    StateChanged,
    SubagentDelivered,
    SubagentProgress,
    TaskAmended,
    TaskClaimRequested,
    TaskClosed,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
    TaskVersionHanded,
    TopicChanged,
    UsageAccrued,
)
from pptmstr.model import (
    AgentState,
    Concern,
    ContextSnapshot,
    Decision,
    EndedHow,
    LaunchSpec,
    NodeId,
    PendingApproval,
    Recalled,
    Snapshot,
    Standing,
    Task,
    TranscriptPresence,
    UsageRollup,
)
from pptmstr.store import _apply

A, B, C = "sess-a", "sess-b", "sess-closed"
ROOT_A: NodeId = (A, None)
ROOT_B: NodeId = (B, None)
ROOT_C: NodeId = (C, None)
SUBS: tuple[NodeId, ...] = ((A, "a1"), (A, "a2"), (B, "b1"), (C, "c1"))
ROOTS: tuple[NodeId, ...] = (ROOT_A, ROOT_B, ROOT_C)
EVERYONE: tuple[NodeId, ...] = ROOTS + SUBS


def spawn(node: NodeId, at: float, policy: Policy = Policy.PERMISSIVE) -> AgentSpawned:
    root = node[1] is None
    return AgentSpawned(
        node_id=node,
        parent=None if root else (node[0], None),
        task="do a thing",
        model="claude-opus-5",
        started_at=at,
        cwd="/w" if root else None,
        template="team" if root else None,
        brief="/briefs/x" if root else None,
        policy=policy if root else None,
    )


def recalled(session_id: str, **known: Any) -> SessionRecalled:
    return SessionRecalled(session_id, "an earlier task", "claude-opus-5", Recalled(**known))


def apply(snap: Snapshot, *intents: Intent, now: float = 1.0) -> Snapshot:
    for intent in intents:
        snap, _ = _apply(snap, intent, now)
    return snap


def derive(snap: Snapshot, intent: Intent, wall: float = 500.0) -> tuple[Snapshot, dict[str, Any]]:
    """
    One step and what it implies for each session, as ``{session id: Change}``.
    """
    after, _ = _apply(snap, intent, 1.0)
    return after, {c.session_id: c for c in changes(snap, intent, after, wall)}


# -- one of every intent ---------------------------------------------------------

Maker = Callable[[random.Random, Snapshot, float], Intent]

_STATES = tuple(AgentState)
_ENDS = (AgentState.DONE, AgentState.DONE, AgentState.DONE, AgentState.FAILED, AgentState.CANCELLED)
_DECISIONS = tuple(f"d-{n}" for n in range(1, 5))


def _who(r: random.Random) -> NodeId:
    return r.choice(EVERYONE)


def _root(r: random.Random) -> NodeId:
    return r.choice(ROOTS)


def _pending(r: random.Random, node: NodeId, now: float) -> PendingApproval:
    return PendingApproval(
        id=f"p-{r.randrange(3)}",
        node=node,
        tool_name="Bash",
        tool_use_id="tu",
        raw_args={"command": "ls"},
        summary="Bash",
        requested_at=now,
    )


def _resolved(r: random.Random, snap: Snapshot) -> ApprovalResolved:
    parked = [(node, p.id) for node, rec in snap.nodes.items() for p in rec.pending]
    node, pending_id = r.choice(parked) if parked and r.random() < 0.9 else (_who(r), "p-none")
    return ApprovalResolved(node, pending_id, approved=r.random() < 0.7)


_COMMAND_IDS = tuple(f"cmd-20261013T000000Z-{n:012x}" for n in range(1, 5))


def _command(r: random.Random) -> Command:
    return Command(
        id=r.choice(_COMMAND_IDS),
        asker=_who(r),
        asked="make install",
        reason="the build needs it",
        expects="a wheel under dist/",
        asked_at=0.0,
    )


def _decision(r: random.Random) -> Decision:
    return Decision(
        id=r.choice(_DECISIONS),
        asker=_who(r),
        question="which?",
        blocks=(),
        if_unanswered="",
        evidence="",
        recommendation="",
        standing=r.choice(tuple(Standing)),
        reversible=None,
        raised_at=0.0,
    )


def _concern(r: random.Random, now: float) -> Concern:
    return Concern(
        id=f"c-{r.randrange(6)}",
        sender=_who(r),
        recipient=_who(r),
        subject="subject",
        body="body",
        posted_at=now,
    )


def _context() -> ContextSnapshot:
    return ContextSnapshot(
        used_tokens=10,
        max_tokens=180_000,
        raw_max_tokens=200_000,
        percentage=0.1,
        auto_compact_enabled=True,
        auto_compact_threshold=100_000,
        model="claude-opus-5",
        polled_at=1.0,
    )


def _spawned(r: random.Random, now: float) -> AgentSpawned:
    return spawn(r.choice((ROOT_A, ROOT_B, *SUBS, (A, f"late-{r.randrange(2)}"))), now)


_MAKERS: dict[type, Maker] = {
    AgentSpawned: lambda r, s, now: _spawned(r, now),
    FailureAcknowledged: lambda r, s, now: FailureAcknowledged(_who(r)),
    StateChanged: lambda r, s, now: StateChanged(_who(r), r.choice(_STATES)),
    TopicChanged: lambda r, s, now: TopicChanged(_who(r), "reading"),
    UsageAccrued: lambda r, s, now: UsageAccrued(_who(r), UsageRollup(total_cost_usd=0.25)),
    ContextPolled: lambda r, s, now: ContextPolled(_who(r), _context()),
    CompactionObserved: lambda r, s, now: CompactionObserved(_who(r), now, "auto"),
    ApprovalRequested: lambda r, s, now: (
        lambda node: ApprovalRequested(node, _pending(r, node, now))
    )(_who(r)),
    ApprovalResolved: lambda r, s, now: _resolved(r, s),
    AgentFinished: lambda r, s, now: AgentFinished(
        _who(r), state := r.choice(_ENDS), now, "it broke" if state is AgentState.FAILED else None
    ),
    SubagentProgress: lambda r, s, now: SubagentProgress(_who(r), "working"),
    SubagentDelivered: lambda r, s, now: SubagentDelivered(_who(r), "the answer"),
    AgentResumed: lambda r, s, now: AgentResumed(_who(r), now),
    # A sub-agent only. Nothing in pptmstr emits a removal, and one of a root
    # leaves a session with no record for its file to be compared with.
    AgentRemoved: lambda r, s, now: AgentRemoved(r.choice(SUBS)),
    ConcernPosted: lambda r, s, now: ConcernPosted(_who(r), _concern(r, now)),
    InboxRead: lambda r, s, now: InboxRead(_who(r), "rq", now),
    ConcernEdited: lambda r, s, now: ConcernEdited(f"c-{r.randrange(6)}", "rewritten"),
    ConcernWithdrawn: lambda r, s, now: ConcernWithdrawn(f"c-{r.randrange(6)}"),
    TaskAmended: lambda r, s, now: TaskAmended(f"t-{r.randrange(4)}", "amended"),
    TaskVersionHanded: lambda r, s, now: TaskVersionHanded(_who(r), f"t-{r.randrange(4)}", 1),
    TaskDeclared: lambda r, s, now: TaskDeclared(
        Task(id=f"t-{r.randrange(4)}", title="T", declared_at=now), node_id=_root(r)
    ),
    TaskClaimRequested: lambda r, s, now: TaskClaimRequested(_who(r), "rq"),
    TaskCompleted: lambda r, s, now: TaskCompleted(_who(r), f"t-{r.randrange(4)}", now),
    TaskReleased: lambda r, s, now: TaskReleased(_who(r), f"t-{r.randrange(4)}"),
    TaskClosed: lambda r, s, now: TaskClosed(f"t-{r.randrange(4)}"),
    BoardRead: lambda r, s, now: BoardRead(_who(r), "rq"),
    DigestRead: lambda r, s, now: DigestRead(_who(r), "rq", now),
    BoardRestored: lambda r, s, now: BoardRestored(
        r.choice((A, B, C)), boardlog.Board(tasks=(), concerns=()), Clock(wall=now, monotonic=now)
    ),
    DecisionRaised: lambda r, s, now: DecisionRaised(_who(r), _decision(r), "rq"),
    DecisionAnswered: lambda r, s, now: DecisionAnswered(r.choice(_DECISIONS), "yes"),
    DecisionDismissed: lambda r, s, now: DecisionDismissed(r.choice(_DECISIONS)),
    DecisionWithdrawn: lambda r, s, now: DecisionWithdrawn(_who(r), r.choice(_DECISIONS)),
    DecisionsRead: lambda r, s, now: DecisionsRead(_who(r), "rq", now),
    CommandRequested: lambda r, s, now: CommandRequested(_who(r), _command(r), "rq", now),
    CommandApproved: lambda r, s, now: CommandApproved(
        r.choice(_COMMAND_IDS), "make install", "/bin/sh", "/w", 600, now
    ),
    CommandRefused: lambda r, s, now: CommandRefused(r.choice(_COMMAND_IDS), "no", now),
    CommandWithdrawn: lambda r, s, now: CommandWithdrawn(
        _who(r), r.choice(_COMMAND_IDS), "rq", now
    ),
    CommandStopAsked: lambda r, s, now: CommandStopAsked(r.choice(_COMMAND_IDS), now),
    CommandEnded: lambda r, s, now: CommandEnded(
        r.choice(_COMMAND_IDS), End(outcome=Exited(code=0), at=now)
    ),
    CommandsRead: lambda r, s, now: CommandsRead(_who(r), "rq", now, r.choice(_COMMAND_IDS)),
    CommandsRestored: lambda r, s, now: CommandsRestored(
        (session := r.choice((A, B, C))),
        (
            dataclasses.replace(
                _command(r), asker=(session, None), end=End(outcome=Lapsed(), at=now)
            ),
        ),
    ),
    SessionRecalled: lambda r, s, now: recalled(r.choice((A, C, C))),
    SessionDetached: lambda r, s, now: SessionDetached(r.choice((A, B, C))),
    FailedSessionClosed: lambda r, s, now: FailedSessionClosed(
        (r.choice((A, B, C)), None), run_held=r.random() < 0.3
    ),
}

# What a session's life is made of, drawn far more often than the rest: ends and
# removals have to line up before a session can leave the fleet at all.
_LIFE: tuple[type, ...] = (
    AgentSpawned,
    StateChanged,
    StateChanged,
    AgentFinished,
    AgentFinished,
    AgentFinished,
    AgentFinished,
    SessionDetached,
    SessionDetached,
    SessionDetached,
    SessionRecalled,
    FailedSessionClosed,
    FailedSessionClosed,
    UsageAccrued,
    AgentResumed,
    ApprovalRequested,
    ApprovalResolved,
    ApprovalResolved,
    DecisionRaised,
    DecisionAnswered,
    CommandRequested,
    CommandRefused,
    FailureAcknowledged,
)

SEEDS = range(60)
LENGTH = 300


@dataclass(frozen=True)
class Step:
    number: int
    intent: Intent
    before: Snapshot
    after: Snapshot
    wall: float
    derived: tuple[Change, ...]


def steps(seed: int, length: int = LENGTH) -> Iterator[Step]:
    rng = random.Random(seed)
    snap = Snapshot.empty()
    everything = list(_MAKERS)
    for number in range(length):
        now = 1.0 + number
        wall = 1_000_000.0 + number
        kind = rng.choice(_LIFE) if rng.random() < 0.8 else rng.choice(everything)
        intent = _MAKERS[kind](rng, snap, now)
        after, _ = _apply(snap, intent, now)
        yield Step(number, intent, snap, after, wall, changes(snap, intent, after, wall))
        snap = after


def _expected_end(state: AgentState) -> str | None:
    if not state.is_terminal:
        return None
    return "failed" if state is AgentState.FAILED else "closed"


# -- the property ----------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_the_changes_folded_step_by_step_say_what_the_snapshot_says(seed: int) -> None:
    files: dict[str, dict[str, object]] = {}
    ran_here: set[str] = set()
    reached_the_cli: set[str] = set()
    spent: dict[str, Spent] = {}
    for step in steps(seed):
        where = f"seed {seed} step {step.number}: {step.intent!r}"
        assert len({c.session_id for c in step.derived}) == len(step.derived), where
        for change in step.derived:
            file = files.setdefault(change.session_id, {})
            earlier = file.get("last_active")
            file.update(change.keys)
            if earlier is not None:
                assert file["last_active"] >= earlier, where  # type: ignore[operator]
            assert file.get("ended_how") in (None, "closed", "failed"), where
            # The team and the brief are set only where a file has none, and
            # only by a root's announce, from the record it built. The gate and
            # the model are not derived at all: they are a start's to say.
            if change.once:
                assert isinstance(step.intent, AgentSpawned), where
                announced = step.after.nodes[step.intent.node_id]
                assert dict(change.once) == {
                    key: value
                    for key, value in (
                        ("template", announced.template),
                        ("brief", announced.brief),
                    )
                    if value
                }, where
            assert not {"policy", "model", "template", "brief"} & set(change.keys), where
            if change.started:
                reached_the_cli.add(change.session_id)
            if change.spent is not None:
                last = spent.get(change.session_id)
                if last is not None:
                    assert change.spent.run >= last.run, where
                    if change.spent.run == last.run:
                        assert change.spent.usd >= last.usd, where
                spent[change.session_id] = change.spent
            root = step.after.nodes[(change.session_id, None)]
            if root.recalled is not None:
                assert set(change.keys) <= {"in_fleet"}, where
                assert change.spent is None and not change.started, where
        if isinstance(step.intent, AgentSpawned) and step.intent.node_id[1] is None:
            ran_here.add(step.intent.node_id[0])

        for node, root in step.after.nodes.items():
            if node[1] is not None:
                continue
            file = files.get(node[0], {})
            assert file.get("in_fleet", True) == (not step.after.off_fleet(node[0])), where
            if node[0] not in ran_here:
                continue
            assert root.recalled is None, where
            assert file["ended_how"] == _expected_end(root.state), where
            assert (file["ended_at"] is None) == (not root.state.is_terminal), where
            if not root.state.is_terminal and root.state is not AgentState.SPAWNING:
                assert node[0] in reached_the_cli, where
            last = spent.get(node[0])
            if last is not None and last.run == root.started_at:
                assert last.usd <= root.usage.total_cost_usd, where


def test_the_generator_reaches_the_cases_the_property_is_for() -> None:
    seen: set[str] = set()
    for seed in SEEDS:
        off: set[str] = set()
        for step in steps(seed):
            for change in step.derived:
                root = step.after.nodes[(change.session_id, None)]
                if change.keys.get("in_fleet") is False:
                    off.add(change.session_id)
                    seen.add("removed a recalled card" if root.recalled else "removed")
                if change.keys.get("in_fleet") is True and change.session_id in off:
                    off.discard(change.session_id)
                    seen.add(
                        "back by an announce"
                        if isinstance(step.intent, AgentSpawned) and step.intent.node_id[1] is None
                        else "back by something else"
                    )
                if change.keys.get("ended_how") in ("closed", "failed"):
                    seen.add(str(change.keys["ended_how"]))
                was = step.before.nodes.get((change.session_id, None))
                if (
                    was is not None
                    and was.state.is_terminal
                    and "ended_how" in change.keys
                    and change.keys["ended_how"] is None
                    and not isinstance(step.intent, AgentSpawned)
                ):
                    seen.add("alive again")
                if change.started:
                    seen.add("started")
                if change.spent is not None and change.spent.usd > 0:
                    seen.add("spent")
    assert seen == {
        "removed",
        "removed a recalled card",
        "back by an announce",
        "back by something else",
        "closed",
        "failed",
        "alive again",
        "started",
        "spent",
    }


def test_every_intent_is_generated_so_a_new_one_cannot_go_underived_unnoticed() -> None:
    """
    An intent added to the union and not to ``_MAKERS`` fails here, which is what
    makes the property above a statement about every intent there is.
    """
    assert set(_MAKERS) == set(get_args(Intent))
    produced = {type(step.intent) for seed in range(12) for step in steps(seed)}
    assert produced == set(_MAKERS)


# -- F3, item by item: what changes() derives ------------------------------------


def test_recalling_sessions_derives_nothing() -> None:
    """
    F3a. A recall takes a session from no record to a root in DONE, which two
    snapshots cannot tell from an end.
    """
    snap = Snapshot.empty()
    derived: list[Change] = []
    for n in range(40):
        intent = recalled(f"closed-{n}", ended_how=EndedHow.SHUTDOWN, last_active=10.0)
        after, _ = _apply(snap, intent, 1.0)
        derived.extend(changes(snap, intent, after, 999.0))
        snap = after
    assert len(snap.nodes) == 40
    assert derived == []


def test_nothing_is_derived_about_the_run_of_a_recalled_record() -> None:
    snap = apply(Snapshot.empty(), recalled(C))
    for intent in (
        StateChanged(ROOT_C, AgentState.AWAITING_INPUT),
        AgentFinished(ROOT_C, AgentState.FAILED, 2.0, "late"),
        AgentResumed(ROOT_C, 2.0),
        UsageAccrued(ROOT_C, UsageRollup(total_cost_usd=3.0)),
    ):
        snap, derived = derive(snap, intent)
        assert derived == {}


def test_removing_a_recalled_card_is_written_and_says_only_that() -> None:
    snap = apply(Snapshot.empty(), recalled(C))
    snap, derived = derive(snap, SessionDetached(C))
    assert snap.off_fleet(C)
    assert derived == {C: Change(C, {"in_fleet": False})}
    # A second removal changes nothing and derives nothing.
    snap, derived = derive(snap, SessionDetached(C))
    assert derived == {}


def test_a_refused_removal_derives_nothing() -> None:
    snap = apply(Snapshot.empty(), spawn(ROOT_A, 1.0), StateChanged(ROOT_A, AgentState.THINKING))
    snap, derived = derive(snap, SessionDetached(A))
    assert not snap.off_fleet(A)
    assert derived == {}


def _removed() -> Snapshot:
    snap = apply(
        Snapshot.empty(),
        spawn(ROOT_A, 1.0),
        spawn((A, "a1"), 1.0),
        AgentFinished((A, "a1"), AgentState.DONE, 2.0),
        AgentFinished(ROOT_A, AgentState.DONE, 2.0),
        SessionDetached(A),
    )
    assert snap.off_fleet(A)
    return snap


@pytest.mark.parametrize(
    "revival",
    [
        spawn((A, "a9"), 5.0),
        AgentResumed((A, "a1"), 5.0),
        AgentFinished((A, "a1"), AgentState.FAILED, 5.0, "late"),
        DecisionRaised(
            (A, "a1"),
            Decision(
                id="d-1",
                asker=(A, "a1"),
                question="which?",
                blocks=(),
                if_unanswered="",
                evidence="",
                recommendation="",
                standing=Standing.BLOCKING if hasattr(Standing, "BLOCKING") else tuple(Standing)[0],
                reversible=None,
                raised_at=0.0,
            ),
        ),
        CommandRequested(
            (A, "a1"),
            Command(
                id=_COMMAND_IDS[0],
                asker=(A, "a1"),
                asked="make install",
                reason="",
                expects="",
                asked_at=0.0,
            ),
            None,
            5.0,
        ),
    ],
    ids=[
        "a new sub-agent",
        "a woken sub-agent",
        "a sub-agent that failed late",
        "a decision",
        "a command",
    ],
)
def test_a_session_that_comes_back_is_written_back_on_the_fleet(revival: Intent) -> None:
    """
    F3. None of these names the root or is a removal, and each is a session the
    operator can see again: a file left saying false loses it at the next start.
    """
    snap, derived = derive(_removed(), revival)
    assert not snap.off_fleet(A)
    assert derived[A].keys["in_fleet"] is True


def test_an_announce_clears_the_end_and_puts_the_session_on_the_fleet() -> None:
    """
    F3c. Derived at the announce, so it is in store order behind the end of the
    run it replaces whichever thread wrote the launch first.
    """
    snap, derived = derive(_removed(), spawn(ROOT_A, 9.0, Policy.AUTONOMOUS), wall=700.0)
    keys = derived[A].keys
    assert keys["in_fleet"] is True
    assert keys["ended_at"] is None and keys["ended_how"] is None and keys["error"] is None
    assert keys["last_active"] == 700.0
    assert keys["cwd"] == "/w"
    # Facts about a run that reached the CLI, which an announce is not.
    assert "policy" not in keys and "model" not in keys
    # Facts about the session, which the file keeps if it has them.
    assert "template" not in keys and "brief" not in keys
    assert derived[A].once == {"template": "team", "brief": "/briefs/x"}
    assert derived[A].started is False


def test_an_announce_with_no_team_or_brief_offers_neither() -> None:
    """
    A None is not a value to set where the file has none: it would be one.
    """
    bare = AgentSpawned(ROOT_A, None, "do a thing", "claude-opus-5", 1.0)
    _, derived = derive(Snapshot.empty(), bare)
    assert derived[A].once == {}


def test_a_resume_announced_as_another_team_does_not_make_the_file_forget_its_own(
    tmp_path: Path,
) -> None:
    """
    F4c. A team session resumed from a surface that did not know its team is
    announced solo with no brief. Written as it is announced, the file would
    say solo from then on, and the next resume would start it with no board.
    """
    writer = FleetWriter(tmp_path)
    writer.admit(A)
    snap = Snapshot.empty()
    resumed = AgentSpawned(ROOT_A, None, "do a thing", "claude-opus-5", 5.0, template="solo")
    for wall, intent in (
        (11.0, spawn(ROOT_A, 1.0)),
        (12.0, StateChanged(ROOT_A, AgentState.THINKING)),
        (13.0, AgentFinished(ROOT_A, AgentState.DONE, 2.0)),
        (14.0, resumed),
        (15.0, StateChanged(ROOT_A, AgentState.THINKING)),
    ):
        after, _ = _apply(snap, intent, wall)
        for change in changes(snap, intent, after, wall):
            writer.submit(change)
        snap = after
    settled(writer)
    held = on_disk(tmp_path, A)
    assert (held["template"], held["brief"]) == ("team", "/briefs/x")
    assert snap.nodes[ROOT_A].template == "solo", "the run itself is what was asked for"


def test_closing_a_failed_session_is_an_end_somebody_asked_for() -> None:
    """
    The store's own close of a failed root with no run (``FailedSessionClosed``):
    the file says closed. A refused one changes no state and derives nothing.

    **And it says nothing else.** The run ended when it failed. Written as an
    end at the click, the close would move ``ended_at`` and ``last_active`` to
    the click -- the card to the top of the next start's order -- and null the
    failure's text, which is the only record of why the session is closed.
    """
    live = apply(Snapshot.empty(), spawn(ROOT_A, 1.0), StateChanged(ROOT_A, AgentState.THINKING))
    failed, as_it_failed = derive(
        live, AgentFinished(ROOT_A, AgentState.FAILED, 2.0, "boom"), wall=700.0
    )
    assert as_it_failed[A].keys == {
        "last_active": 700.0,
        "ended_at": 700.0,
        "ended_how": "failed",
        "error": "boom",
    }
    _, derived = derive(failed, FailedSessionClosed(ROOT_A, run_held=False), wall=900.0)
    assert derived[A].keys == {"ended_how": "closed"}
    assert not derived[A].started
    _, derived = derive(failed, FailedSessionClosed(ROOT_A, run_held=True), wall=900.0)
    assert derived == {}


def test_an_end_is_closed_when_asked_for_and_failed_when_not() -> None:
    live = apply(Snapshot.empty(), spawn(ROOT_A, 1.0), StateChanged(ROOT_A, AgentState.THINKING))
    _, derived = derive(live, AgentFinished(ROOT_A, AgentState.DONE, 2.0), wall=800.0)
    assert derived[A].keys == {
        "last_active": 800.0,
        "ended_at": 800.0,
        "ended_how": "closed",
        "error": None,
    }
    failed, derived = derive(live, AgentFinished(ROOT_A, AgentState.FAILED, 2.0, "x" * 9_000))
    assert derived[A].keys["ended_how"] == "failed"
    assert derived[A].keys["error"] == "x" * fleetsync.MAX_ERROR_CHARS
    # Closing the failed card is an end somebody asked for.
    _, derived = derive(failed, AgentFinished(ROOT_A, AgentState.DONE, 3.0))
    assert derived[A].keys["ended_how"] == "closed"
    # And one that answered instead has no end.
    _, derived = derive(failed, StateChanged(ROOT_A, AgentState.THINKING))
    assert derived[A].keys == {"ended_at": None, "ended_how": None}
    assert derived[A].started


def test_a_turn_end_is_activity_and_a_step_inside_a_turn_is_not() -> None:
    snap = apply(Snapshot.empty(), spawn(ROOT_A, 1.0))
    snap, derived = derive(snap, StateChanged(ROOT_A, AgentState.THINKING))
    assert derived == {A: Change(A, started=True)}
    snap, derived = derive(snap, StateChanged(ROOT_A, AgentState.CALLING_TOOL))
    assert derived == {}
    snap = apply(snap, UsageAccrued(ROOT_A, UsageRollup(total_cost_usd=1.5)))
    for state in (AgentState.SUPERVISING, AgentState.AWAITING_INPUT):
        snap, derived = derive(snap, StateChanged(ROOT_A, state), wall=600.0)
        assert derived == {A: Change(A, {"last_active": 600.0}, spent=Spent(1.0, 1.5))}
    # A sub-agent's turn is not the session's.
    snap = apply(snap, spawn((A, "a1"), 2.0))
    _, derived = derive(snap, StateChanged((A, "a1"), AgentState.AWAITING_INPUT))
    assert derived == {}


def test_a_launch_that_is_refused_before_the_cli_is_never_started() -> None:
    """
    F3f. The driver ends a refused session from SPAWNING, and that is not a run
    that reached the CLI.
    """
    snap = apply(Snapshot.empty(), spawn(ROOT_A, 1.0))
    _, derived = derive(snap, AgentFinished(ROOT_A, AgentState.FAILED, 2.0, "uncontained"))
    assert derived[A].started is False
    assert derived[A].keys["ended_how"] == "failed"


def test_changes_never_says_shutdown() -> None:
    said = {
        change.keys.get("ended_how")
        for seed in range(10)
        for step in steps(seed)
        for change in step.derived
    }
    assert said <= {None, "closed", "failed"}


# -- what the shell says for itself ----------------------------------------------


def test_a_launch_carries_what_the_store_is_not_told() -> None:
    spec = LaunchSpec(task="build it", model="m", cwd="/w", subagent_cap=None)
    change = fleetsync.launched(spec, A, Policy.AUTONOMOUS, True, 50.0, subagent_cap=4)
    assert change.keys == {
        "in_fleet": True,
        "ended_at": None,
        "ended_how": None,
        "error": None,
        "launched_at": 50.0,
        "last_active": 50.0,
        "last_message": "build it",
        "model": "m",
        "policy": Policy.AUTONOMOUS,
        "contained": True,
        "subagent_cap": 4,
        "task": "build it",
    }
    assert change.once == {"created_at": 50.0}
    assert not change.started


def test_a_resume_does_not_offer_its_message_as_the_cards_title() -> None:
    spec = LaunchSpec(task="ok, carry on", model="m", resume=A, subagent_cap=2)
    change = fleetsync.launched(spec, A, Policy.STRICT, False, 50.0)
    assert "task" not in change.keys
    assert change.keys["last_message"] == "ok, carry on"
    assert change.keys["subagent_cap"] == 2


def test_a_resume_with_a_message_records_the_message_and_never_the_task() -> None:
    """
    What the resume form builds: the session's own task as ``task`` and the typed
    text as ``message``. The file's last message is the text that was sent, and
    the task is not offered as a title either -- the file keeps its first.
    """
    spec = LaunchSpec(task="build the parser", model="m", resume=A, message="carry on")
    change = fleetsync.launched(spec, A, Policy.STRICT, False, 50.0)
    assert "task" not in change.keys
    assert change.keys["last_message"] == "carry on"
    too_long = "x" * (sessionfile.MAX_LAST_MESSAGE + 1)
    spec = LaunchSpec(task="build the parser", model="m", resume=A, message=too_long)
    assert fleetsync.launched(spec, A, Policy.STRICT, False, 1.0).keys["last_message"] is None


def test_an_over_long_message_clears_the_stored_one() -> None:
    """
    F3d. ``sessionfile.update`` would skip it and keep the previous message.
    """
    at_most = "x" * sessionfile.MAX_LAST_MESSAGE
    assert fleetsync.sent(A, at_most, 1.0).keys["last_message"] == at_most
    assert fleetsync.sent(A, at_most + "x", 1.0).keys["last_message"] is None
    spec = LaunchSpec(task=at_most + "x", model="m")
    assert fleetsync.launched(spec, A, Policy.STRICT, False, 1.0).keys["last_message"] is None


def test_shutdown_is_said_of_the_sessions_named_and_only_by_the_shell() -> None:
    assert fleetsync.shutdown([A, B], 77.0) == (
        Change(A, {"ended_how": "shutdown", "ended_at": 77.0, "last_active": 77.0}),
        Change(B, {"ended_how": "shutdown", "ended_at": 77.0, "last_active": 77.0}),
    )
    assert fleetsync.gate_changed(A, Policy.STRICT) == Change(A, {"policy": Policy.STRICT})


# -- the writer ------------------------------------------------------------------


def settled(writer: FleetWriter, timeout: float = 5.0) -> tuple[fleetsync.Report, ...]:
    """
    Wait for every queued change to have been tried, and return the reports.
    """
    deadline = time.monotonic() + timeout
    while writer.outstanding:
        assert time.monotonic() < deadline, "the writer did not settle"
        time.sleep(0.002)
    return writer.drain()


def on_disk(root: Path, session_id: str) -> dict[str, Any]:
    with open(root / session_id / sessionfile.FILE_NAME, encoding="utf-8") as handle:
        held: dict[str, Any] = json.load(handle)
    return held


def has_file(root: Path, session_id: str) -> bool:
    return (root / session_id / sessionfile.FILE_NAME).exists()


def running(root: Path, session_id: str = A) -> FleetWriter:
    """
    A writer with ``session_id`` admitted and its file created by a started run.
    """
    writer = FleetWriter(root)
    writer.admit(session_id)
    writer.submit(Change(session_id, {"model": "m"}, started=True))
    assert all(isinstance(r, Outcome) and r.written for r in settled(writer))
    return writer


def test_no_file_is_touched_on_the_thread_that_submits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    touched: list[tuple[str, int]] = []
    real_update, real_read = sessionfile.update, sessionfile.read

    def update(*args: Any, **kwargs: Any) -> Any:
        touched.append(("update", threading.get_ident()))
        return real_update(*args, **kwargs)

    def read(*args: Any, **kwargs: Any) -> Any:
        touched.append(("read", threading.get_ident()))
        return real_read(*args, **kwargs)

    monkeypatch.setattr(sessionfile, "update", update)
    monkeypatch.setattr(sessionfile, "read", read)
    writer = FleetWriter(tmp_path)
    writer.admit(A)
    spec = LaunchSpec(task="build it", model="m")
    writer.submit(fleetsync.launched(spec, A, Policy.STRICT, False, 1.0))
    writer.submit(Change(A, {"last_active": 2.0}, spent=Spent(1.0, 2.0), started=True))
    settled(writer)
    writer.submit(Change(A, {"in_fleet": False}))
    writer.close()
    assert {kind for kind, _ in touched} == {"update", "read"}
    assert threading.get_ident() not in {thread for _, thread in touched}
    assert len({thread for _, thread in touched}) == 1
    assert on_disk(tmp_path, A)["in_fleet"] is False


class Wedge:
    """
    ``sessionfile.update`` held inside its first call until ``release`` is set.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[dict[str, object]] = []
        real = sessionfile.update

        def update(session_id: str, keys: Any, root: Any = None) -> Any:
            self.calls.append(dict(keys))
            if len(self.calls) == 1:
                self.entered.set()
                self.release.wait(10.0)
            return real(session_id, keys, root)

        monkeypatch.setattr(sessionfile, "update", update)


def test_changes_to_one_session_are_one_write_however_many(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wedge = Wedge(monkeypatch)
    writer = FleetWriter(tmp_path)
    writer.admit(A)
    writer.admit(B)
    writer.submit(Change(A, {"model": "first"}, started=True))
    assert wedge.entered.wait(5.0)
    for n in range(500):
        writer.submit(Change(A, {"last_active": float(n), "model": f"m{n}"}))
        writer.submit(Change(B, {"last_active": float(n)}, started=True))
        # One write in flight, and at most one item per session behind it.
        assert writer.outstanding <= 3
    wedge.release.set()
    reports = settled(writer)
    assert len(wedge.calls) == 3
    assert [r.through for r in reports if isinstance(r, Outcome)] == [1, 501, 500]
    assert on_disk(tmp_path, A)["model"] == "m499"
    assert on_disk(tmp_path, A)["last_active"] == 499.0
    assert on_disk(tmp_path, B)["last_active"] == 499.0


def test_two_threads_submitting_leave_the_change_with_the_later_number(
    tmp_path: Path,
) -> None:
    writer = running(tmp_path)
    numbered: dict[int, str] = {}
    guard = threading.Lock()
    go = threading.Barrier(2)

    def submit(name: str) -> None:
        go.wait()
        for n in range(400):
            text = f"{name}-{n}"
            sequence = writer.submit(Change(A, {"last_message": text}))
            with guard:
                numbered[sequence] = text

    threads = [threading.Thread(target=submit, args=(name,)) for name in ("draw", "loop")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    settled(writer)
    assert sorted(numbered) == list(range(2, 802))
    assert on_disk(tmp_path, A)["last_message"] == numbered[801]


def _closed_then_relaunched() -> tuple[list[Change], Change]:
    """
    The observer's changes for a run that ends and is announced again, in store
    order, and the launch write the other thread makes somewhere among them.
    """
    snap = apply(Snapshot.empty(), spawn(ROOT_A, 1.0), StateChanged(ROOT_A, AgentState.THINKING))
    observed: list[Change] = []
    for intent in (
        AgentFinished(ROOT_A, AgentState.DONE, 2.0),
        SessionDetached(A),
        spawn(ROOT_A, 3.0),
    ):
        after, _ = _apply(snap, intent, 1.0)
        observed.extend(changes(snap, intent, after, 100.0))
        snap = after
    launch = fleetsync.launched(
        LaunchSpec(task="again", model="m", resume=A), A, Policy.STRICT, False, 99.0
    )
    return observed, launch


@pytest.mark.parametrize("position", range(4))
def test_a_relaunch_is_never_left_closed_or_off_the_fleet_wherever_its_launch_write_lands(
    tmp_path: Path, position: int
) -> None:
    """
    F3c. The launch is written from the asyncio thread and the old run's end and
    a removal from the observer's, in either order.
    """
    observed, launch = _closed_then_relaunched()
    assert [c.keys.get("ended_how", "-") for c in observed] == ["closed", "-", None]
    assert [c.keys.get("in_fleet", "-") for c in observed] == ["-", False, True]
    sequence = [*observed]
    sequence.insert(position, launch)
    writer = running(tmp_path)
    for change in sequence:
        writer.submit(change)
        if position % 2:
            settled(writer)
    writer.close()
    held = on_disk(tmp_path, A)
    assert held["ended_how"] is None and held["ended_at"] is None
    assert held["in_fleet"] is True


def test_a_session_never_admitted_gets_no_file_and_no_report(tmp_path: Path) -> None:
    """
    F3g. Every session of the scripted driver, and any id a stray intent names.
    """
    writer = FleetWriter(tmp_path)
    assert writer.submit(Change(A, {"model": "m", "last_active": 5.0}, started=True)) == 0
    assert writer.submit(fleetsync.sent(A, "hello", 6.0)) == 0
    assert writer.outstanding == 0
    assert writer.close() == ()
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []
    # What it was last seen doing is still answered, for Details.
    assert writer.last_active(A) == 6.0
    assert writer.last_active(B) is None


def test_a_session_whose_run_never_reached_the_cli_gets_no_file(tmp_path: Path) -> None:
    """
    F3f. Admitted, launched, announced, refused: nothing is created, so nothing
    is recalled as a card whose transcript is gone.
    """
    writer = FleetWriter(tmp_path)
    writer.admit(A)
    snap = Snapshot.empty()
    writer.submit(
        fleetsync.launched(LaunchSpec(task="t", model="m"), A, Policy.AUTONOMOUS, False, 1.0)
    )
    for intent in (spawn(ROOT_A, 1.0), AgentFinished(ROOT_A, AgentState.FAILED, 2.0, "refused")):
        after, _ = _apply(snap, intent, 1.0)
        for change in changes(snap, intent, after, 2.0):
            writer.submit(change)
        snap = after
        assert settled(writer) == ()
    assert writer.close() == ()
    assert not has_file(tmp_path, A)
    assert fleetsync.recall(tmp_path, lambda: []).sessions == ()


def test_everything_held_for_a_session_is_written_once_its_run_reaches_the_cli(
    tmp_path: Path,
) -> None:
    writer = FleetWriter(tmp_path)
    writer.admit(A)
    snap = Snapshot.empty()
    spec = LaunchSpec(task="build it", model="m")
    writer.submit(fleetsync.launched(spec, A, Policy.PERMISSIVE, True, 10.0, subagent_cap=3))
    for wall, intent in (
        (11.0, spawn(ROOT_A, 1.0)),
        (12.0, StateChanged(ROOT_A, AgentState.THINKING)),
    ):
        assert not has_file(tmp_path, A)
        after, _ = _apply(snap, intent, 1.0)
        for change in changes(snap, intent, after, wall):
            writer.submit(change)
        snap = after
        settled(writer)
    held = on_disk(tmp_path, A)
    assert held == {
        "format": sessionfile.FORMAT,
        "session_id": A,
        "in_fleet": True,
        "created_at": 10.0,
        "launched_at": 10.0,
        "last_active": 11.0,
        "ended_at": None,
        "ended_how": None,
        "error": None,
        "task": "build it",
        "last_message": "build it",
        "model": "m",
        "cwd": "/w",
        "session_base": None,
        "template": "team",
        "brief": "/briefs/x",
        "policy": "permissive",
        "contained": True,
        "subagent_cap": 3,
    }
    # The evidence is remembered: a later change needs none of its own.
    writer.submit(fleetsync.sent(A, "and then", 13.0))
    settled(writer)
    assert on_disk(tmp_path, A)["last_message"] == "and then"


def test_a_refused_resume_of_a_session_that_has_a_file_is_still_written(tmp_path: Path) -> None:
    sessionfile.update(A, {"task": "first", "ended_how": "closed", "in_fleet": False}, tmp_path)
    writer = FleetWriter(tmp_path)
    writer.admit(A)
    spec = LaunchSpec(task="again", model="m", resume=A)
    writer.submit(fleetsync.launched(spec, A, Policy.STRICT, False, 20.0))
    settled(writer)
    held = on_disk(tmp_path, A)
    assert (held["in_fleet"], held["ended_how"], held["task"]) == (True, None, "first")
    assert held["last_message"] == "again"


def test_created_at_and_the_title_are_the_first_launchs(tmp_path: Path) -> None:
    writer = FleetWriter(tmp_path)
    writer.admit(A)
    first = fleetsync.launched(LaunchSpec(task="first", model="m"), A, Policy.STRICT, False, 10.0)
    writer.submit(Change(A, first.keys, first.once, started=True))
    settled(writer)
    writer.submit(
        fleetsync.launched(LaunchSpec(task="second", model="m"), A, Policy.STRICT, False, 20.0)
    )
    settled(writer)
    held = on_disk(tmp_path, A)
    assert (held["created_at"], held["launched_at"]) == (10.0, 20.0)
    assert (held["task"], held["last_message"]) == ("first", "second")


def test_an_over_long_message_does_not_leave_the_previous_one_to_be_offered(
    tmp_path: Path,
) -> None:
    """
    F3d, to disk: the drop-the-table message is not what a resume form prefills
    after a 30,000-character paste was the last thing sent.
    """
    writer = running(tmp_path)
    writer.submit(fleetsync.sent(A, "drop the old table and re-run the migration", 1.0))
    settled(writer)
    assert on_disk(tmp_path, A)["last_message"].startswith("drop the old table")
    writer.submit(fleetsync.sent(A, "log line\n" * 4_000, 2.0))
    reports = settled(writer)
    assert [r.written for r in reports if isinstance(r, Outcome)] == [True]
    assert on_disk(tmp_path, A)["last_message"] is None
    assert on_disk(tmp_path, A)["last_active"] == 2.0


def test_last_active_never_goes_back_when_the_wall_clock_does(tmp_path: Path) -> None:
    writer = running(tmp_path)
    writer.submit(Change(A, {"last_active": 100.0}))
    settled(writer)
    writer.submit(Change(A, {"last_active": 40.0, "model": "later"}))
    settled(writer)
    held = on_disk(tmp_path, A)
    assert (held["last_active"], held["model"]) == (100.0, "later")
    assert writer.last_active(A) == 100.0
    writer.submit(Change(A, {"last_active": 101.0}))
    assert writer.last_active(A) == 101.0
    writer.close()


def test_cost_is_what_the_file_held_plus_what_the_run_spent_and_never_falls(
    tmp_path: Path,
) -> None:
    """
    F3i. A resumed session's record starts at zero.
    """
    writer = running(tmp_path)

    def spend(run: float, usd: float) -> object:
        writer.submit(Change(A, {"last_active": run}, spent=Spent(run, usd)))
        settled(writer)
        return on_disk(tmp_path, A).get("cost_usd")

    assert spend(1.0, 0.0) is None
    assert spend(1.0, 2.0) == 2.0
    assert spend(1.0, 5.0) == 5.0
    # Announced again in this window: a new run, counted from what is there.
    assert spend(7.0, 0.0) == 5.0
    assert spend(7.0, 1.5) == 6.5
    # The earlier run's figure arriving behind its successor is not added again.
    assert spend(1.0, 5.5) == 6.5
    assert spend(7.0, 2.0) == 7.0
    writer.close()

    # After a restart: another process, the same file.
    later = FleetWriter(tmp_path)
    later.admit(A)
    later.submit(Change(A, {"last_active": 9.0}, spent=Spent(0.5, 0.25)))
    settled(later)
    assert on_disk(tmp_path, A)["cost_usd"] == 7.25
    # Another window wrote more in the meantime: never below what the file holds.
    sessionfile.update(A, {"cost_usd": 20.0}, tmp_path)
    later.submit(Change(A, {"last_active": 10.0}, spent=Spent(0.5, 0.5)))
    settled(later)
    assert on_disk(tmp_path, A)["cost_usd"] == 20.0
    later.close()


def test_two_runs_merged_into_one_write_are_both_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = running(tmp_path)
    wedge = Wedge(monkeypatch)
    writer.submit(Change(A, {"model": "wedged"}))
    assert wedge.entered.wait(5.0)
    writer.submit(Change(A, {"last_active": 1.0}, spent=Spent(1.0, 4.0)))
    writer.submit(Change(A, {"last_active": 2.0}, spent=Spent(7.0, 1.0)))
    wedge.release.set()
    settled(writer)
    assert on_disk(tmp_path, A)["cost_usd"] == 5.0


@pytest.mark.parametrize(
    "text",
    ["{ not json", json.dumps({"format": sessionfile.FORMAT + 1, "in_fleet": True, "task": "t"})],
    ids=["unreadable", "a newer build's"],
)
def test_a_file_that_is_not_this_builds_is_latched_and_never_written(
    tmp_path: Path, text: str
) -> None:
    """
    F3e, the half that latches.
    """
    path = tmp_path / A / sessionfile.FILE_NAME
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    writer = FleetWriter(tmp_path)
    writer.admit(A)
    writer.submit(Change(A, {"in_fleet": False}, started=True))
    first = settled(writer)
    assert [type(r) for r in first] == [Outcome, Latched]
    assert isinstance(first[0], Outcome) and first[0].lost_fleet
    writer.submit(Change(A, {"last_active": 3.0}))
    writer.submit(Change(A, {"in_fleet": True}))
    later = settled(writer)
    # Reported once as latched; each later loss is still an outcome.
    assert [type(r) for r in later] == [Outcome]
    assert isinstance(later[0], Outcome) and later[0].lost_fleet and not later[0].written
    writer.close()
    assert path.read_text(encoding="utf-8") == text


def test_a_file_that_becomes_unreadable_under_a_running_session_is_latched(
    tmp_path: Path,
) -> None:
    writer = running(tmp_path)
    path = tmp_path / A / sessionfile.FILE_NAME
    path.write_text("[]", encoding="utf-8")
    writer.submit(Change(A, {"last_active": 3.0}))
    assert [type(r) for r in settled(writer)] == [Outcome, Latched]
    writer.close()
    assert path.read_text(encoding="utf-8") == "[]"


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a directory without the bit")
def test_a_failure_of_the_moment_does_not_stop_the_sessions_later_writes(tmp_path: Path) -> None:
    """
    F3e, the half that must not latch: the board log's rule would drop every
    later change, a removal from the fleet among them.
    """
    writer = running(tmp_path)
    directory = tmp_path / A
    directory.chmod(0o500)
    try:
        writer.submit(Change(A, {"last_active": 3.0}))
        failed = settled(writer)
    finally:
        directory.chmod(0o700)
    assert [type(r) for r in failed] == [Outcome]
    assert isinstance(failed[0], Outcome) and not failed[0].written and not failed[0].lost_fleet
    writer.submit(Change(A, {"in_fleet": False}))
    after = settled(writer)
    assert [r.written for r in after if isinstance(r, Outcome)] == [True]
    assert on_disk(tmp_path, A)["in_fleet"] is False
    writer.close()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a directory without the bit")
@pytest.mark.parametrize("by", ["another session's change", "close"])
def test_a_dropped_removal_is_reported_and_tried_again(tmp_path: Path, by: str) -> None:
    """
    F3e. A removed session is closed: it has no next change of its own to carry
    the removal, and without it the card is back at the next start.
    """
    writer = running(tmp_path)
    writer.admit(B)
    directory = tmp_path / A
    directory.chmod(0o500)
    try:
        writer.submit(Change(A, {"in_fleet": False}))
        failed = settled(writer)
    finally:
        directory.chmod(0o700)
    assert [isinstance(r, Outcome) and r.lost_fleet for r in failed] == [True]
    assert on_disk(tmp_path, A).get("in_fleet", True) is True
    if by == "close":
        reports = writer.close()
    else:
        writer.submit(Change(B, {"model": "m"}, started=True))
        deadline = time.monotonic() + 5.0
        while on_disk(tmp_path, A).get("in_fleet", True) and time.monotonic() < deadline:
            time.sleep(0.002)
        # Before the close, which would try it again by itself.
        assert on_disk(tmp_path, A)["in_fleet"] is False
        reports = settled(writer) + writer.close()
    assert on_disk(tmp_path, A)["in_fleet"] is False
    assert [
        r.session_id
        for r in reports
        if isinstance(r, Outcome) and r.written and r.keys == {"in_fleet": False}
    ] == [A]


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a directory without the bit")
def test_a_file_that_could_not_be_created_is_created_whole_at_the_next_change(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o500)
    writer = FleetWriter(tmp_path)
    writer.admit(A)
    try:
        writer.submit(Change(A, {"model": "m", "task": "the title"}, started=True))
        failed = settled(writer)
    finally:
        tmp_path.chmod(0o700)
    assert [isinstance(r, Outcome) and not r.written for r in failed] == [True]
    writer.submit(Change(A, {"last_active": 4.0}))
    settled(writer)
    held = on_disk(tmp_path, A)
    assert (held["model"], held["task"], held["last_active"]) == ("m", "the title", 4.0)
    writer.close()


def test_close_is_bounded_when_a_write_never_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = running(tmp_path)
    writer.admit(B)
    wedge = Wedge(monkeypatch)
    writer.submit(Change(A, {"in_fleet": False}))
    assert wedge.entered.wait(5.0)
    writer.submit(Change(B, {"model": "m"}, started=True))
    began = time.monotonic()
    reports = writer.close(timeout=0.2)
    assert time.monotonic() - began < 3.0
    assert [(r.session_id, r.written) for r in reports if isinstance(r, Outcome)] == [
        (A, False),
        (B, False),
    ]
    reasons = [r.result.reason for r in reports if isinstance(r, Outcome) and not r.written]  # type: ignore[union-attr]
    assert "may still reach disk" in reasons[0] and "still queued" in reasons[1]
    assert isinstance(reports[0], Outcome) and reports[0].lost_fleet
    # Nothing further is written, and a later submission says so.
    assert writer.submit(Change(B, {"model": "late"}, started=True)) == 0
    late = writer.close()
    assert [isinstance(r, Outcome) and "closed" in r.result.reason for r in late] == [True]  # type: ignore[union-attr]
    wedge.release.set()
    time.sleep(0.05)
    assert not has_file(tmp_path, B)


def test_close_with_nothing_submitted_returns_at_once(tmp_path: Path) -> None:
    began = time.monotonic()
    assert FleetWriter(tmp_path).close() == ()
    assert time.monotonic() - began < 1.0


def test_a_write_that_raises_is_an_outcome_and_the_worker_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = running(tmp_path)
    real = sessionfile.update
    monkeypatch.setattr(sessionfile, "update", lambda *a, **k: 1 / 0)
    writer.submit(Change(A, {"model": "x"}))
    raised = settled(writer)
    assert [type(r) for r in raised] == [Outcome]
    assert "ZeroDivisionError" in str(raised[0])
    monkeypatch.setattr(sessionfile, "update", real)
    writer.submit(Change(A, {"model": "y"}))
    settled(writer)
    assert on_disk(tmp_path, A)["model"] == "y"
    writer.close()


def test_the_default_root_is_asked_at_each_write_and_is_the_redirected_one(
    tmp_path: Path,
) -> None:
    writer = FleetWriter()
    writer.admit(A)
    writer.submit(Change(A, {"model": "m"}, started=True))
    settled(writer)
    writer.close()
    path = sessionfile.file_path(A)
    assert path is not None and path.exists()
    assert sessionfile.root() in path.parents


# -- the fleet at startup --------------------------------------------------------


@dataclass(frozen=True)
class Row:
    session_id: str
    label: str = "the CLI's line"


def _raises() -> list[Row]:
    raise OSError("the transcript tree cannot be read")


def test_recall_says_whether_each_transcript_is_there(tmp_path: Path) -> None:
    for session_id in ("here", "gone"):
        sessionfile.update(session_id, {"task": session_id, "last_active": 1.0}, tmp_path)
    fleet = fleetsync.recall(tmp_path, lambda: [Row("here"), Row("another")])  # type: ignore[arg-type,list-item]
    assert {s.session_id: s.recalled.transcript for s in fleet.sessions} == {
        "here": TranscriptPresence.PRESENT,
        "gone": TranscriptPresence.GONE,
    }
    assert fleet.unlisted is None

    unknown = fleetsync.recall(tmp_path, _raises)  # type: ignore[arg-type]
    assert {s.recalled.transcript for s in unknown.sessions} == {TranscriptPresence.UNKNOWN}
    assert len(unknown.sessions) == 2
    assert unknown.unlisted == "OSError: the transcript tree cannot be read"


def test_recall_tells_a_killed_session_from_a_closed_one(tmp_path: Path) -> None:
    """
    F3h. A file with no end recorded is a session that was live when pptmstr
    last wrote it.
    """
    sessionfile.update("closed", {"ended_how": "closed", "ended_at": 5.0}, tmp_path)
    sessionfile.update("failed", {"ended_how": "failed", "error": "it broke"}, tmp_path)
    sessionfile.update("quit", {"ended_how": "shutdown"}, tmp_path)
    sessionfile.update("killed", {"last_active": 9.0}, tmp_path)
    fleet = fleetsync.recall(tmp_path, lambda: [])
    how = {s.session_id: s.recalled.ended_how for s in fleet.sessions}
    assert how == {
        "closed": EndedHow.CLOSED,
        "failed": EndedHow.FAILED,
        "quit": EndedHow.SHUTDOWN,
        "killed": None,
    }
    assert fleet.killed == ("killed",)
    assert {s.session_id: s.recalled.error for s in fleet.sessions}["failed"] == "it broke"


def test_recall_names_what_it_does_not_recall(tmp_path: Path) -> None:
    sessionfile.update("kept", {"task": "kept"}, tmp_path)
    sessionfile.update("removed", {"in_fleet": False}, tmp_path)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / sessionfile.FILE_NAME).write_text("{", encoding="utf-8")
    # Only a moved board log: a session pptmstr has no file about.
    (tmp_path / "board-only" / "board").mkdir(parents=True)
    fleet = fleetsync.recall(tmp_path, lambda: [])
    assert [s.session_id for s in fleet.sessions] == ["kept"]
    assert fleet.detached == ("removed",)
    assert [session_id for session_id, _ in fleet.unreadable] == ["broken"]
    assert "not JSON" in fleet.unreadable[0][1]
    assert fleet.problem is None
    # Nothing was written by looking.
    assert (tmp_path / "broken" / sessionfile.FILE_NAME).read_text(encoding="utf-8") == "{"
    assert not has_file(tmp_path, "board-only")


def test_recall_is_most_recently_active_first(tmp_path: Path) -> None:
    sessionfile.update("old", {"last_active": 10.0}, tmp_path)
    sessionfile.update("new", {"last_active": 30.0}, tmp_path)
    sessionfile.update("launched-only", {"launched_at": 20.0}, tmp_path)
    sessionfile.update("b-undated", {}, tmp_path)
    sessionfile.update("a-undated", {}, tmp_path)
    fleet = fleetsync.recall(tmp_path, lambda: [])
    assert [s.session_id for s in fleet.sessions] == [
        "new",
        "launched-only",
        "old",
        "a-undated",
        "b-undated",
    ]


def test_recall_carries_what_the_file_said_and_claims_nothing_else(tmp_path: Path) -> None:
    sessionfile.update(
        "full",
        {
            "task": "the title",
            "model": "claude-opus-5",
            "cwd": "/w",
            "session_base": "/w/base",
            "template": "team",
            "brief": "/briefs/x",
            "policy": Policy.AUTONOMOUS,
            "contained": True,
            "created_at": 1.0,
            "last_active": 2.0,
            "cost_usd": 3.5,
            "ended_how": "closed",
        },
        tmp_path,
    )
    path = tmp_path / "bare" / sessionfile.FILE_NAME
    path.parent.mkdir()
    path.write_text(json.dumps({"policy": "a rung from the future"}), encoding="utf-8")
    fleet = fleetsync.recall(tmp_path, lambda: [Row("bare"), Row("full")])  # type: ignore[arg-type,list-item]
    by_id = {s.session_id: s for s in fleet.sessions}
    assert by_id["full"] == SessionRecalled(
        session_id="full",
        task="the title",
        model="claude-opus-5",
        recalled=Recalled(
            transcript=TranscriptPresence.PRESENT,
            created_at=1.0,
            last_active=2.0,
            ended_how=EndedHow.CLOSED,
            policy=Policy.AUTONOMOUS,
            contained=True,
            cost_usd=3.5,
            task_recorded=True,
        ),
        cwd="/w",
        session_base="/w/base",
        template="team",
        brief="/briefs/x",
    )
    bare = by_id["bare"]
    # A rung this build does not know is no rung, and a file with no title is
    # titled from the CLI's line for the session.
    assert bare.recalled.policy is None
    assert bare.task == "the CLI's line"
    assert (bare.cwd, bare.model, bare.recalled.last_active) == (None, "", None)


def test_what_recall_returns_is_applied_as_closed_cards_and_derives_no_write(
    tmp_path: Path,
) -> None:
    for n in range(5):
        sessionfile.update(f"s{n}", {"task": f"t{n}", "last_active": float(n)}, tmp_path)
    before = {n: (tmp_path / f"s{n}" / sessionfile.FILE_NAME).read_bytes() for n in range(5)}
    fleet = fleetsync.recall(tmp_path, lambda: [])
    writer = FleetWriter(tmp_path)
    snap = Snapshot.empty()
    for intent in fleet.sessions:
        writer.admit(intent.session_id)
        after, _ = _apply(snap, intent, 1.0)
        for change in changes(snap, intent, after, 999.0):
            writer.submit(change)
        snap = after
    assert writer.close() == ()
    assert [node[0] for node in snap.order] == ["s4", "s3", "s2", "s1", "s0"]
    assert all(rec.recalled is not None for rec in snap.nodes.values())
    assert before == {
        n: (tmp_path / f"s{n}" / sessionfile.FILE_NAME).read_bytes() for n in range(5)
    }


def test_recall_carries_what_the_resume_form_opens_with_and_whether_the_title_is_a_task(
    tmp_path: Path,
) -> None:
    """
    The form is filled from the record and reads no file on the draw thread, so
    the recall is what has to carry the last message and the cap. And a file
    with no task -- a session first run by an older build -- is titled from the
    CLI's line, which the record must say is a title and not a task.
    """
    sessionfile.update(
        "full",
        {"task": "build it", "last_message": "and then the tests", "subagent_cap": 3},
        tmp_path,
    )
    sessionfile.update("older", {"last_active": 1.0}, tmp_path)
    sessionfile.update("bare", {"last_active": 2.0}, tmp_path)
    fleet = fleetsync.recall(tmp_path, lambda: [Row("full"), Row("older")])  # type: ignore[arg-type,list-item]
    by_id = {s.session_id: s for s in fleet.sessions}

    full = by_id["full"].recalled
    assert (full.last_message, full.subagent_cap, full.task_recorded) == (
        "and then the tests",
        3,
        True,
    )
    assert by_id["full"].task == "build it"
    older = by_id["older"]
    assert older.task == "the CLI's line" and older.recalled.task_recorded is False
    assert (older.recalled.last_message, older.recalled.subagent_cap) == (None, None)
    assert by_id["bare"].task == "" and by_id["bare"].recalled.task_recorded is False
