"""
The records a reducer step yields, and that restoring them is the board the store
holds.

The property is run over generated sequences of every intent the union has, driven
through ``store._apply`` itself: operator, declarer, holder, bystander, a node with
no record and another session's agents all act, mostly in orders the reducer
refuses. What is compared is identity -- a restored party against the ``NodeId``
the store holds -- so two agents that were called the same thing, or nothing, do
not compare equal by being rendered.
"""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, get_args

import pytest

from pptmstr import boardfacts, boardlog
from pptmstr.board import FOREIGN, UNKNOWN, role_name
from pptmstr.boardfacts import Fact, outcomes
from pptmstr.boardlog import Clock, RestoredConcern, RestoredTask
from pptmstr.command import Command, End, Exited, Lapsed
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
    EARLIER_RUN,
    AgentState,
    ApprovedWrites,
    Basis,
    Concern,
    ConcernState,
    ContextSnapshot,
    Decision,
    NodeId,
    PendingApproval,
    Recalled,
    Snapshot,
    Standing,
    Task,
    TaskState,
    UsageRollup,
)
from pptmstr.store import _apply

S, Z = "s", "z"
LEAD: NodeId = (S, None)
B1: NodeId = (S, "a1")
B2: NodeId = (S, "a2")
REVIEWER: NodeId = (S, "a3")
# Never spawned: the board has no name for either, and they are two agents.
GHOST: NodeId = (S, "ghost-1")
GHOST_2: NodeId = (S, "ghost-2")
Z_LEAD: NodeId = (Z, None)
Z_BUILDER: NodeId = (Z, "z1")

_SPAWNED: tuple[tuple[NodeId, NodeId | None, str | None], ...] = (
    (LEAD, None, None),
    (B1, LEAD, "builder"),
    (B2, LEAD, "builder"),
    (REVIEWER, LEAD, "reviewer"),
    (Z_LEAD, None, None),
    (Z_BUILDER, Z_LEAD, "builder"),
)
_AGENTS: tuple[NodeId, ...] = (LEAD, B1, B2, REVIEWER, GHOST, GHOST_2, Z_LEAD, Z_BUILDER)
_TASK_IDS = tuple(f"t-{n}" for n in range(1, 25))
# Few, so an amendment repeats the standing text about as often as it changes it.
_DETAILS = ("", "first", "second")
_PROPERTIES = ("", "never short a fact", "  spaced   out  ")
_RESULTS = ("", "   ", "3/3 pass", "two\nlines")
_NOT_RUN = (None, "", "  ", "the cross presets")
_BASES = (None, Basis.RUN, Basis.READ, Basis.RELAYED)


def spawn(node: NodeId, parent: NodeId | None, agent_type: str | None, at: float) -> AgentSpawned:
    return AgentSpawned(
        node_id=node,
        parent=parent,
        task="do a thing",
        model="claude-opus-5",
        started_at=at,
        agent_type=agent_type,
        cwd="/w" if parent is None else None,
    )


def started() -> Snapshot:
    snap = Snapshot.empty()
    for node, parent, agent_type in _SPAWNED:
        snap, _ = _apply(snap, spawn(node, parent, agent_type, 0.0), 0.0)
    return snap


# -- one of every intent ---------------------------------------------------------

Maker = Callable[[random.Random, Snapshot, float], Intent]


def _task_id(r: random.Random) -> str:
    return r.choice(_TASK_IDS)


def _task(r: random.Random, now: float) -> Task:
    # Sometimes already claimed and finished, which a declaration must not be:
    # the reducer blanks it, and the record has to be of what landed.
    dirty = r.random() < 0.2
    return Task(
        id=_task_id(r),
        title=r.choice(("T", "")),
        detail=r.choice(_DETAILS),
        depends_on=tuple(r.sample(_TASK_IDS, r.choice((0, 0, 0, 1)))),
        touches=tuple(
            r.sample(("a.py", "./a.py", "pkg/b.py", "c.py", "d.py"), r.choice((0, 0, 1)))
        ),
        declared_at=now,
        basis=r.choice(_BASES),
        property=r.choice(_PROPERTIES),
        state=TaskState.COMPLETED if dirty else TaskState.PENDING,
        claimed_by=B1 if dirty else None,
        result="smuggled" if dirty else "",
        held_by=(B2,) if dirty else (),
    )


def _concern(r: random.Random, now: float) -> Concern:
    state = r.choice((ConcernState.POSTED,) * 6 + (ConcernState.DELIVERED, ConcernState.WITHDRAWN))
    return Concern(
        id=f"c-{r.randrange(30)}",
        sender=r.choice(_AGENTS),
        recipient=r.choice(_AGENTS),
        subject="subject",
        body=r.choice(("as posted", "")),
        posted_at=now,
        state=state,
        edited=state is not ConcernState.POSTED and r.random() < 0.5,
        delivered_at=now if state is ConcernState.DELIVERED else None,
        task_id=r.choice((None, *_TASK_IDS)),
        basis=r.choice(_BASES),
        incident=r.random() < 0.3,
    )


_DIFF = "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n keep\n-old\n+new\n+more\n"


def _pending(r: random.Random, node: NodeId, now: float) -> PendingApproval:
    tool = r.choice(("Write", "Edit", "Bash", "Bash"))
    args: dict[str, Any] = (
        {"command": "ls"}
        if tool == "Bash"
        else {"file_path": r.choice(("/w/a.py", "/w/pkg/b.py", "/etc/hosts", "c.py"))}
    )
    return PendingApproval(
        id=f"p-{r.randrange(4)}",
        node=node,
        tool_name=tool,
        tool_use_id="tu",
        raw_args=args,
        summary=tool,
        requested_at=now,
        diff=r.choice((None, _DIFF)),
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


def _who(r: random.Random) -> NodeId:
    return r.choice(_AGENTS)


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


_DECISION_IDS = tuple(f"d-{n}" for n in range(1, 7))


def _decision(r: random.Random) -> Decision:
    # ``blocks`` names tasks, so a decision's row is built over a board with
    # those tasks on it, held and finished and released.
    return Decision(
        id=r.choice(_DECISION_IDS),
        asker=_who(r),
        question="which?",
        blocks=tuple(r.sample(_TASK_IDS, 2)),
        if_unanswered="",
        evidence="",
        recommendation="",
        standing=r.choice(tuple(Standing)),
        reversible=r.choice((True, False, None)),
        raised_at=0.0,
    )


def _maybe_at(r: random.Random, now: float) -> float | None:
    return r.choice((None, now - 0.25))


# How often a maker that can aim does: at the holder of an open task, at a task
# that is there to claim, at a call that is parked. The rest of the time it acts
# as anyone on anything, which is mostly what the reducer refuses -- and both
# halves are the input: the property is about what was accepted, and it is only
# worth something if the refused step beside it yields nothing.
_AIMED = 0.6


def _holding(r: random.Random, snap: Snapshot) -> tuple[NodeId, str]:
    """A holder and the task it holds open, when aiming and there is one."""
    open_ = [
        (t.claimed_by, t.id)
        for t in snap.tasks.values()
        if t.state is TaskState.CLAIMED and t.claimed_by is not None
    ]
    if open_ and r.random() < _AIMED:
        return r.choice(open_)
    return _who(r), _task_id(r)


def _claim(r: random.Random, snap: Snapshot) -> TaskClaimRequested:
    pending = [t.id for t in snap.tasks.values() if t.state is TaskState.PENDING]
    task_id = r.choice(pending) if pending and r.random() < _AIMED else _task_id(r)
    return TaskClaimRequested(_who(r), "rq", r.choice((None, task_id, task_id)))


def _handed(r: random.Random, snap: Snapshot) -> TaskVersionHanded:
    # A holder whose specification was replaced under it is the one case a
    # showing changes anything, so it is looked for first.
    stale = [
        t
        for t in snap.tasks.values()
        if t.state is TaskState.CLAIMED
        and t.claimed_by is not None
        and (t.claimed_version or 0) < t.version
    ]
    if stale and r.random() < _AIMED:
        shown = r.choice(stale)
        assert shown.claimed_by is not None
        node, task_id = shown.claimed_by, shown.id
    else:
        node, task_id = _holding(r, snap)
    standing = snap.tasks[task_id].version if task_id in snap.tasks else 1
    # Around the version that stands: behind it, it, and one that does not exist.
    return TaskVersionHanded(node, task_id, r.randrange(max(0, standing - 2), standing + 2))


def _amended(r: random.Random, snap: Snapshot, now: float) -> TaskAmended:
    known = list(snap.tasks.values())
    amender: NodeId | None
    if known and r.random() < _AIMED:
        # Half the time under a holder, which is the amendment the version stamps
        # are for.
        open_ = [t for t in known if t.state is TaskState.CLAIMED]
        target = r.choice(open_ if open_ and r.random() < 0.5 else known)
        task_id, amender = target.id, r.choice((None, target.declared_by))
    else:
        task_id, amender = _task_id(r), r.choice((None, *_AGENTS))
    return TaskAmended(
        task_id,
        r.choice(_DETAILS),
        node_id=amender,
        property=r.choice((None, None, *_PROPERTIES)),
        at=_maybe_at(r, now),
        basis=r.choice(_BASES),
    )


def _completed(r: random.Random, snap: Snapshot, now: float) -> TaskCompleted:
    node, task_id = _holding(r, snap)
    return TaskCompleted(
        node,
        task_id,
        now,
        result=r.choice(_RESULTS),
        basis=r.choice(_BASES),
        not_run=r.choice(_NOT_RUN),
    )


def _spawned(r: random.Random, now: float) -> AgentSpawned:
    # Mostly a new sub-agent. Sometimes the session's root announced again, which
    # is the session started again in one window: every sub-agent that has ended
    # is then of an earlier run, and its claims are the root's to give back.
    if r.random() < 0.3:
        return spawn(LEAD, None, None, now)
    return spawn((S, f"late-{r.randrange(3)}"), LEAD, "builder", now)


_REASONS = ("", "  ", "superseded by t-9", "two\nlines")


def _closed(r: random.Random, snap: Snapshot, now: float) -> TaskClosed:
    unfinished = [t.id for t in snap.tasks.values() if t.state is not TaskState.COMPLETED]
    task_id = r.choice(unfinished) if unfinished and r.random() < _AIMED else _task_id(r)
    return TaskClosed(task_id, r.choice(_REASONS), at=_maybe_at(r, now))


def _released(r: random.Random, snap: Snapshot, now: float) -> TaskReleased:
    # A claim whose holder is of an earlier run of the session is the one its
    # root may give back, so the root is aimed at those first.
    stranded = [
        t
        for t in snap.tasks.values()
        if t.state is TaskState.CLAIMED
        and t.claimed_by is not None
        and t.declared_by is not None
        and snap.of_earlier_run(t.claimed_by)
    ]
    if stranded and r.random() < _AIMED:
        taken = r.choice(stranded)
        assert taken.declared_by is not None
        return TaskReleased((taken.declared_by[0], None), taken.id, at=_maybe_at(r, now))
    node, task_id = _holding(r, snap)
    return TaskReleased(node, task_id, at=_maybe_at(r, now))


def _restored(r: random.Random, snap: Snapshot, now: float) -> BoardRestored:
    """
    A session's board handed back to the store that holds it.

    Mostly the board as held, which is what a log restores to when every fact was
    derived -- the property this file is about -- and which the store must find
    already there. Sometimes that board short one task, which is a log behind the
    store and must be refused whole. Never a board with a fact the store lacks:
    a restored board comes out of the log, and one that did not would put a fact
    in the store that no record holds.
    """
    session_id = r.choice((S, S, Z))
    tasks, concerns = held(snap, session_id)
    kept = list(tasks.values())
    if kept and r.random() < 0.3:
        kept.pop(r.randrange(len(kept)))
    board = boardlog.Board(tasks=tuple(kept), concerns=tuple(concerns.values()))
    return BoardRestored(session_id, board, Clock(wall=now, monotonic=now))


def _requested(r: random.Random, snap: Snapshot, now: float) -> ApprovalRequested:
    node, _ = _holding(r, snap)
    return ApprovalRequested(node, _pending(r, node, now))


def _resolved(r: random.Random, snap: Snapshot) -> ApprovalResolved:
    """
    Mostly a decision on a call that is parked, since only that can measure a
    write; sometimes one on nothing, which is a double click.
    """
    parked = [(node, p.id) for node, record in snap.nodes.items() for p in record.pending]
    node, pending_id = r.choice(parked) if parked and r.random() < 0.9 else (_who(r), "p-stale")
    return ApprovalResolved(
        node,
        pending_id,
        approved=r.random() < 0.8,
        edited_args=r.choice((None, None, None, {"file_path": "/w/edited.py"})),
    )


def _edited(r: random.Random, snap: Snapshot) -> ConcernEdited:
    return ConcernEdited(_concern_id(r, snap), r.choice(("as posted", "rewritten")))


def _concern_id(r: random.Random, snap: Snapshot) -> str:
    known = list(snap.concerns)
    return r.choice(known) if known and r.random() < _AIMED else "c-missing"


_MAKERS: dict[type, Maker] = {
    AgentSpawned: lambda r, s, now: _spawned(r, now),
    FailureAcknowledged: lambda r, s, now: FailureAcknowledged(_who(r)),
    StateChanged: lambda r, s, now: StateChanged(_who(r), AgentState.THINKING),
    TopicChanged: lambda r, s, now: TopicChanged(_who(r), "reading"),
    UsageAccrued: lambda r, s, now: UsageAccrued(_who(r), UsageRollup(input_tokens=1)),
    ContextPolled: lambda r, s, now: ContextPolled(_who(r), _context()),
    CompactionObserved: lambda r, s, now: CompactionObserved(_who(r), now, "auto"),
    ApprovalRequested: _requested,
    ApprovalResolved: lambda r, s, now: _resolved(r, s),
    AgentFinished: lambda r, s, now: AgentFinished(_who(r), AgentState.DONE, now),
    SubagentProgress: lambda r, s, now: SubagentProgress(_who(r), "working"),
    SubagentDelivered: lambda r, s, now: SubagentDelivered(_who(r), "the answer"),
    AgentResumed: lambda r, s, now: AgentResumed(_who(r), now),
    AgentRemoved: lambda r, s, now: AgentRemoved(r.choice((B2, REVIEWER, Z_BUILDER, GHOST))),
    ConcernPosted: lambda r, s, now: ConcernPosted(_who(r), _concern(r, now)),
    InboxRead: lambda r, s, now: InboxRead(_who(r), "rq", now),
    ConcernEdited: lambda r, s, now: _edited(r, s),
    ConcernWithdrawn: lambda r, s, now: ConcernWithdrawn(_concern_id(r, s)),
    TaskAmended: _amended,
    TaskVersionHanded: lambda r, s, now: _handed(r, s),
    TaskDeclared: lambda r, s, now: TaskDeclared(
        _task(r, now), node_id=r.choice((None, LEAD, LEAD, LEAD, B1, GHOST, Z_LEAD))
    ),
    TaskClaimRequested: lambda r, s, now: _claim(r, s),
    TaskCompleted: _completed,
    TaskClosed: _closed,
    TaskReleased: _released,
    BoardRead: lambda r, s, now: BoardRead(_who(r), "rq"),
    DigestRead: lambda r, s, now: DigestRead(_who(r), "rq", now),
    BoardRestored: _restored,
    DecisionRaised: lambda r, s, now: DecisionRaised(_who(r), _decision(r), "rq"),
    DecisionAnswered: lambda r, s, now: DecisionAnswered(
        r.choice(_DECISION_IDS), "yes", node_id=r.choice((None, None, _who(r)))
    ),
    DecisionDismissed: lambda r, s, now: DecisionDismissed(r.choice(_DECISION_IDS)),
    DecisionWithdrawn: lambda r, s, now: DecisionWithdrawn(_who(r), r.choice(_DECISION_IDS)),
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
        (session := r.choice((S, Z, "s-closed"))),
        (
            dataclasses.replace(
                _command(r), asker=(session, None), end=End(outcome=Lapsed(), at=now)
            ),
        ),
    ),
    # A session nothing ran here, and the two that did: a recall over a session
    # the store has changes nothing, and a removal is honoured only for one that
    # is over.
    SessionRecalled: lambda r, s, now: SessionRecalled(
        r.choice((S, Z, "s-closed")), "an earlier task", "claude-opus-5", Recalled()
    ),
    SessionDetached: lambda r, s, now: SessionDetached(r.choice((S, Z, "s-closed"))),
    FailedSessionClosed: lambda r, s, now: FailedSessionClosed(
        (r.choice((S, Z, "s-closed")), None), run_held=r.random() < 0.3
    ),
}

# The intents a board fact can come from. Drawn more often than the rest, and the
# ones that move a task along more often again, so a run has work on it that is
# claimed, amended under its holder, shown, finished, given back and written about.
_BOARD: tuple[type, ...] = (
    TaskDeclared,
    TaskDeclared,
    TaskClaimRequested,
    TaskClaimRequested,
    TaskClaimRequested,
    TaskCompleted,
    TaskReleased,
    TaskReleased,
    TaskAmended,
    TaskAmended,
    TaskVersionHanded,
    TaskClosed,
    ConcernPosted,
    InboxRead,
    ConcernEdited,
    ConcernWithdrawn,
    ApprovalRequested,
    ApprovalResolved,
    ApprovalResolved,
)
# Prunes a record, after which the node is one the board has no name for. Rare,
# or every run ends with nobody on it.
_RARE: tuple[type, ...] = (AgentRemoved,)

SEEDS = range(80)
LENGTH = 200


@dataclass(frozen=True)
class Step:
    """One generated intent applied: where it left the store and what it yielded."""

    number: int
    intent: Intent
    after: Snapshot
    facts: tuple[Fact, ...]


def steps(seed: int, length: int = LENGTH) -> Iterator[Step]:
    """
    ``length`` generated intents applied one by one through ``store._apply``.
    """
    rng = random.Random(seed)
    snap = started()
    everything = [kind for kind in _MAKERS if kind not in _RARE]
    for number in range(length):
        now = 1.0 + number
        roll = rng.random()
        if roll < 0.01:
            kind = rng.choice(_RARE)
        elif roll < 0.8:
            kind = rng.choice(_BOARD)
        else:
            kind = rng.choice(everything)
        intent = _MAKERS[kind](rng, snap, now)
        after, _ = _apply(snap, intent, now)
        yield Step(number, intent, after, outcomes(snap, intent, after, now))
        snap = after


def run(seed: int) -> tuple[Snapshot, list[Fact]]:
    """Where a whole generated run left the store, and every fact it yielded."""
    snap = started()
    facts: list[Fact] = []
    for step in steps(seed):
        snap = step.after
        facts.extend(step.facts)
    return snap, facts


# -- the store's board, in the shape a restore gives ----------------------------------


def _text(basis: Basis | None) -> str | None:
    return None if basis is None else basis.value


def as_restored(task: Task) -> RestoredTask:
    """
    What restoring a faithful log of ``task`` gives: every field the store holds,
    parties as the ``NodeId`` they are.
    """
    versions: list[boardlog.TaskVersion] = []
    author, at = task.declared_by, task.declared_at
    for prior in task.superseded:
        versions.append(
            boardlog.TaskVersion(at, prior.detail, author, _text(prior.basis), prior.property)
        )
        author, at = prior.replaced_by, prior.replaced_at
    versions.append(boardlog.TaskVersion(at, task.detail, author, _text(task.basis), task.property))
    assert task.declared_by is not None
    return RestoredTask(
        id=task.id,
        title=task.title,
        declared_by=task.declared_by,
        versions=tuple(versions),
        depends_on=task.depends_on,
        touches=task.touches,
        state=task.state,
        holder=task.claimed_by,
        released_by=task.released_by,
        completed_at=task.completed_at,
        result=task.result,
        result_basis=_text(task.result_basis),
        claimed_version=task.claimed_version,
        held_by=task.held_by,
        writes=task.writes,
        not_run=task.not_run,
        released_at=task.released_at,
        closed_by_operator=task.closed_by_operator,
    )


def as_restored_concern(concern: Concern) -> RestoredConcern:
    return RestoredConcern(
        id=concern.id,
        sender=concern.sender,
        recipient=concern.recipient,
        subject=concern.subject,
        body=concern.body,
        posted_at=concern.posted_at,
        task_id=concern.task_id,
        basis=_text(concern.basis),
        state=concern.state,
        edited=concern.edited,
        delivered_at=concern.delivered_at,
        incident=concern.incident,
    )


def held(
    snap: Snapshot, session_id: str
) -> tuple[dict[str, RestoredTask], dict[str, RestoredConcern]]:
    tasks = {t.id: as_restored(t) for t in snap.tasks.values() if t.belongs_to(session_id)}
    concerns = {
        c.id: as_restored_concern(c) for c in snap.concerns.values() if c.sender[0] == session_id
    }
    return tasks, concerns


def logged(facts: list[Fact], session_id: str) -> boardlog.Board:
    return boardlog.restore(f.record for f in facts if f.session_id == session_id)


# -- the property ------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_restoring_the_records_derived_step_by_step_is_the_board_the_store_holds(
    seed: int,
) -> None:
    """
    After every step that yielded a fact, and at the end, for every session: the
    log restores whole -- nothing stray, nothing one reducer could not have
    appended -- and each task and concern on that session's board equals the
    store's, field for field and node for node.
    """
    facts: list[Fact] = []
    for step in steps(seed):
        facts.extend(step.facts)
        if not step.facts and step.number != LENGTH - 1:
            continue
        for session_id in (S, Z):
            board = logged(facts, session_id)
            assert not board.short, (step.number, step.intent, board.stray, board.contested)
            tasks, concerns = held(step.after, session_id)
            assert {t.id: t for t in board.tasks} == tasks, (step.number, step.intent)
            assert {c.id: c for c in board.concerns} == concerns, (step.number, step.intent)


@pytest.mark.parametrize("seed", range(10))
def test_a_step_that_yielded_nothing_left_both_boards_as_they_were(seed: int) -> None:
    """
    The property is checked where a fact was derived. This is the other half: a
    step with no fact changed no task and no concern on a board, so there was
    nothing a record should have said.
    """
    snap = started()
    for step in steps(seed):
        if not step.facts:
            for session_id in (S, Z):
                assert held(step.after, session_id) == held(snap, session_id), step.intent
        snap = step.after


def test_every_intent_is_generated_so_a_new_one_cannot_go_unlogged_unnoticed() -> None:
    """
    An intent added to the union and not to ``_MAKERS`` fails here. That is the
    moment to decide whether it changes a board fact, and to give it a case in
    ``boardfacts.outcomes`` and a place in ``_BOARD`` if it does.
    """
    assert set(_MAKERS) == set(get_args(Intent))

    produced = {type(step.intent) for seed in range(8) for step in steps(seed)}
    assert produced == set(_MAKERS)


def test_the_generator_reaches_every_record_kind_a_reducer_step_can_yield() -> None:
    """
    The property covers a kind only if a run derives it.
    """
    derived = {type(f.record) for seed in SEEDS for f in run(seed)[1]}

    assert derived == set(get_args(boardlog.Known))


def test_the_generator_reaches_the_cases_the_property_is_for() -> None:
    records = [f.record for seed in SEEDS for f in run(seed)[1]]
    snaps = [run(seed)[0] for seed in range(20)]
    tasks = [t for snap in snaps for t in snap.tasks.values()]

    amended = [r for r in records if isinstance(r, boardlog.Amended)]
    assert any(r.by is None and not r.by_id for r in amended), "the operator"
    assert any(r.by_id for r in amended), "the declarer"
    writes = [r for r in records if isinstance(r, boardlog.WritesAccrued)]
    assert any(r.paths for r in writes) and any(r.unplaced for r in writes)
    assert any(r.lines_added and not r.paths and not r.unplaced for r in writes)
    assert any(len(r.concern_ids) > 1 for r in records if isinstance(r, boardlog.Delivered))
    assert any(t.superseded and t.state is TaskState.COMPLETED for t in tasks)
    assert any(
        t.claimed_version is not None and t.claimed_version < t.version for t in tasks
    ), "a holder working to a text that has since been replaced"
    assert any(t.released_by for t in tasks) and any(len(t.held_by) > 1 for t in tasks)
    assert any(t.declared_by == GHOST for t in tasks), "a declarer the board cannot name"
    assert {f.session_id for seed in range(20) for f in run(seed)[1]} == {S, Z}
    closed = [t for t in tasks if t.closed_by_operator]
    assert any(t.held_by for t in closed), "closed under a holder"
    assert any(not t.held_by for t in closed), "closed before anybody took it"
    assert any(
        isinstance(step.intent, TaskReleased)
        and any(isinstance(f.record, boardlog.Reclaimed) for f in step.facts)
        and step.intent.node_id == LEAD
        and any(
            (S, *f.record.holder_id[1:]) in step.after.nodes
            for f in step.facts
            if isinstance(f.record, boardlog.Reclaimed) and len(f.record.holder_id) == 2
        )
        for seed in SEEDS
        for step in steps(seed)
    ), "a reclaim from a holder this process has a record of: a same-window earlier run"


@pytest.mark.parametrize("seed", range(20))
def test_no_address_written_is_a_thing_a_board_says_about_a_node(seed: int) -> None:
    """
    ``(unknown)`` and ``builder, another session`` are what a board prints for a
    node it cannot place. Written as an address either would restore as a name.
    """
    for fact in run(seed)[1]:
        for address, node in boardlog._named(fact.record):
            assert address is None or not any(
                said in address for said in (UNKNOWN, FOREIGN, EARLIER_RUN)
            )
            # Derived records always say which node, the operator's amendment aside.
            assert node or (isinstance(fact.record, boardlog.Amended) and address is None)


# -- one step at a time ---------------------------------------------------------------


def apply(snap: Snapshot, *intents: Intent, now: float = 5.0) -> tuple[Snapshot, list[Fact]]:
    facts: list[Fact] = []
    for intent in intents:
        after, _ = _apply(snap, intent, now)
        facts.extend(outcomes(snap, intent, after, now))
        snap = after
    return snap, facts


def declare(task_id: str = "t-1", by: NodeId = LEAD, **fields: Any) -> TaskDeclared:
    return TaskDeclared(Task(id=task_id, title="T", declared_at=2.0, **fields), node_id=by)


def claim(node: NodeId, task_id: str | None = "t-1") -> TaskClaimRequested:
    return TaskClaimRequested(node, "rq", task_id)


def test_a_declaration_is_recorded_as_it_landed_and_not_as_it_was_sent() -> None:
    snap, _ = apply(started(), declare("t-0", touches=("a.py",)))
    sent = declare(touches=("./a.py",), property="  never   short ", basis=Basis.READ)

    snap, facts = apply(snap, sent)

    (fact,) = facts
    assert fact == Fact(
        S,
        boardlog.Declared(
            at=2.0,
            task_id="t-1",
            title="T",
            declared_by="lead",
            # The store's own dependency, for the file both tasks write.
            depends_on=("t-0",),
            touches=("a.py",),
            basis="read",
            property="never short",
            declared_by_id=(S,),
        ),
    )


def test_a_sub_agent_is_written_under_its_address_and_its_node() -> None:
    _, facts = apply(started(), declare(), claim(B2))

    assert facts[1].record == boardlog.Claimed(
        at=5.0, task_id="t-1", by="builder-2", by_id=(S, "a2")
    )


def test_a_bare_claim_records_the_task_it_won() -> None:
    snap, _ = apply(started(), declare("t-1"), declare("t-2"), claim(B1, "t-1"))

    _, facts = apply(snap, claim(B2, None))

    assert [
        (f.record.task_id, f.record.by_id) for f in facts if isinstance(f.record, boardlog.Claimed)
    ] == [("t-2", (S, "a2"))]


@pytest.mark.parametrize(
    "refused",
    [
        declare("t-1", detail="again"),
        claim(B2),
        claim(Z_BUILDER),
        TaskCompleted(B2, "t-1", 6.0, result="not mine"),
        TaskReleased(B2, "t-1"),
        TaskReleased(B1, "missing"),
        TaskAmended("t-1", "theirs", node_id=B2),
        TaskAmended("missing", "x"),
        TaskVersionHanded(B2, "t-1", 1),
        TaskVersionHanded(B1, "t-1", 7),
        ConcernEdited("missing", "x"),
        ConcernWithdrawn("missing"),
        InboxRead(B2, "rq", 6.0),
        ApprovalResolved(B1, "no-such-approval", approved=True),
    ],
)
def test_an_intent_the_reducer_refused_yields_nothing(refused: Intent) -> None:
    snap, _ = apply(started(), declare(), claim(B1))

    after, facts = apply(snap, refused)

    assert facts == []
    assert after.tasks == snap.tasks and after.concerns == snap.concerns


def test_an_approval_that_measured_nothing_replaces_the_task_and_yields_no_record() -> None:
    """
    An approved ``Bash`` call adds an empty measurement to the task its caller
    holds. The task is a new object and an equal one, and a record for it would be
    a file for every approved command.
    """
    snap, _ = apply(started(), declare(), claim(B1))
    parked = PendingApproval("p", B1, "Bash", "tu", {"command": "ls"}, "Bash", 1.0)
    snap, _ = apply(snap, ApprovalRequested(B1, parked))

    after, facts = apply(snap, ApprovalResolved(B1, "p", approved=True))

    assert after.tasks["t-1"] is not snap.tasks["t-1"], "the arm no longer replaces it"
    assert after.tasks["t-1"] == snap.tasks["t-1"]
    assert facts == []


def test_a_measured_write_is_recorded_as_what_it_added() -> None:
    snap, _ = apply(started(), declare(), claim(B1))
    first = PendingApproval("p1", B1, "Write", "tu", {"file_path": "/w/a.py"}, "Write", 1.0, _DIFF)
    again = PendingApproval("p2", B1, "Write", "tu", {"file_path": "/w/a.py"}, "Write", 1.0, _DIFF)

    snap, one = apply(snap, ApprovalRequested(B1, first), ApprovalResolved(B1, "p1", approved=True))
    snap, two = apply(snap, ApprovalRequested(B1, again), ApprovalResolved(B1, "p2", approved=True))

    assert [f.record for f in one] == [
        boardlog.WritesAccrued(
            at=5.0, task_id="t-1", paths=("a.py",), lines_added=2, lines_removed=1
        )
    ]
    # The same file again is no new path, and the lines are this write's alone.
    assert [f.record for f in two] == [
        boardlog.WritesAccrued(at=5.0, task_id="t-1", lines_added=2, lines_removed=1)
    ]
    assert snap.tasks["t-1"].writes == ApprovedWrites(("a.py",), (), 4, 2)


def test_an_amendment_that_changes_nothing_at_all_yields_nothing() -> None:
    snap, _ = apply(started(), declare(detail="as it stands", property="P"))

    after, facts = apply(snap, TaskAmended("t-1", "as it stands", property="P"))

    assert after.tasks["t-1"] is not snap.tasks["t-1"], "the arm no longer replaces it"
    assert facts == []


def test_an_amendment_is_recorded_with_the_specification_it_left() -> None:
    snap, _ = apply(started(), declare(detail="first", property="P", basis=Basis.RUN))

    _, reworded = apply(
        snap, TaskAmended("t-1", "second", node_id=LEAD, at=4.5, basis=Basis.RELAYED)
    )
    _, reheld = apply(snap, TaskAmended("t-1", "first", property=" Q ", basis=Basis.RELAYED))

    assert [f.record for f in reworded] == [
        boardlog.Amended(
            at=4.5,
            task_id="t-1",
            detail="second",
            by="lead",
            property="P",
            basis="relayed",
            by_id=(S,),
        )
    ]
    # The text was not replaced, so its author's standing is the one that stands.
    assert [f.record for f in reheld] == [
        boardlog.Amended(at=5.0, task_id="t-1", detail="first", property="Q", basis="run")
    ]


def test_a_version_shown_to_the_holder_is_recorded_and_one_that_moved_nothing_is_not() -> None:
    snap, _ = apply(started(), declare(detail="first"), claim(B1), TaskAmended("t-1", "second"))

    after, shown = apply(snap, TaskVersionHanded(B1, "t-1", 2))
    _, again = apply(after, TaskVersionHanded(B1, "t-1", 2))

    assert [f.record for f in shown] == [
        boardlog.Handed(at=5.0, task_id="t-1", by="builder", version=2, by_id=(S, "a1"))
    ]
    assert again == []


def test_a_completion_is_recorded_as_the_store_kept_it() -> None:
    snap, _ = apply(started(), declare(), claim(B1))

    _, facts = apply(snap, TaskCompleted(B1, "t-1", 6.5, result="   ", basis=Basis.RUN, not_run=""))

    # A blank result is none, and with none there is nothing for a basis or a
    # stated "nothing was left unrun" to be about.
    assert [f.record for f in facts] == [
        boardlog.Completed(at=6.5, task_id="t-1", by="builder", by_id=(S, "a1"))
    ]


def test_a_release_is_stamped_with_the_emitters_clock_or_the_frames() -> None:
    snap, _ = apply(started(), declare(), claim(B1))

    _, stamped = apply(snap, TaskReleased(B1, "t-1", at=4.75))
    _, unstamped = apply(snap, TaskReleased(B1, "t-1"))

    assert [f.record for f in stamped] == [
        boardlog.Released(at=4.75, task_id="t-1", by="builder", by_id=(S, "a1"))
    ]
    assert [f.record.at for f in unstamped] == [5.0]


def test_a_concern_filed_already_settled_is_recorded_with_what_became_of_it() -> None:
    """
    The store files the record it is handed. One handed over rewritten and
    delivered is on the board that way, so the log has to say so too.
    """
    settled = Concern(
        "c-1",
        B1,
        LEAD,
        "subject",
        "rewritten",
        posted_at=3.0,
        state=ConcernState.DELIVERED,
        edited=True,
        delivered_at=3.5,
    )

    snap, facts = apply(started(), ConcernPosted(B1, settled))

    assert [type(f.record) for f in facts] == [boardlog.Posted, boardlog.Edited, boardlog.Delivered]
    assert logged(facts, S).concerns == (as_restored_concern(snap.concerns["c-1"]),)


def test_an_edit_and_a_withdrawal_are_recorded_once_and_in_the_senders_log() -> None:
    concern = Concern("c-1", Z_BUILDER, LEAD, "subject", "as posted", posted_at=3.0)
    snap, _ = apply(started(), ConcernPosted(Z_BUILDER, concern))

    snap, edited = apply(snap, ConcernEdited("c-1", "rewritten"))
    snap, same = apply(snap, ConcernEdited("c-1", "rewritten"))
    snap, stopped = apply(snap, ConcernWithdrawn("c-1"))
    _, late = apply(snap, ConcernEdited("c-1", "too late"), ConcernWithdrawn("c-1"))

    assert edited == [Fact(Z, boardlog.Edited(5.0, "c-1", "rewritten"))]
    assert same == []
    assert stopped == [Fact(Z, boardlog.Withdrawn(5.0, "c-1"))]
    assert late == []


def test_two_nodes_the_board_has_no_name_for_are_written_as_two_parties() -> None:
    snap, facts = apply(
        started(), declare("t-1"), declare("t-2"), claim(GHOST, "t-1"), claim(GHOST_2, "t-2")
    )

    claims = [f.record for f in facts if isinstance(f.record, boardlog.Claimed)]
    assert [(c.by, c.by_id) for c in claims] == [("", (S, "ghost-1")), ("", (S, "ghost-2"))]
    first, second = logged(facts, S).tasks
    assert (first.holder, second.holder) == (GHOST, GHOST_2)
    # And the second cannot give back what the first holds.
    _, refused = apply(snap, TaskReleased(GHOST_2, "t-1"))
    assert refused == []


def test_another_sessions_agent_is_written_as_its_own_sessions_and_restores_as_itself() -> None:
    concern = Concern("c-1", B1, Z_LEAD, "subject", "body", posted_at=3.0)

    _, facts = apply(started(), ConcernPosted(B1, concern))

    (fact,) = facts
    assert fact.session_id == S
    assert isinstance(fact.record, boardlog.Posted)
    assert (fact.record.recipient, fact.record.recipient_id) == ("lead", (Z,))
    (restored,) = logged(facts, S).concerns
    assert (restored.sender, restored.recipient) == (B1, Z_LEAD)
    assert restored.recipient != LEAD


def test_one_read_is_recorded_in_the_log_of_each_session_it_took_mail_from() -> None:
    mine = Concern("c-1", B1, LEAD, "subject", "body", posted_at=3.0)
    also = Concern("c-2", B2, LEAD, "subject", "body", posted_at=3.5)
    theirs = Concern("c-3", Z_BUILDER, LEAD, "subject", "body", posted_at=4.0)
    snap, _ = apply(
        started(),
        ConcernPosted(B1, mine),
        ConcernPosted(B2, also),
        ConcernPosted(Z_BUILDER, theirs),
    )

    _, facts = apply(snap, InboxRead(LEAD, "rq", 6.0))

    assert facts == [
        Fact(S, boardlog.Delivered(6.0, ("c-1", "c-2"))),
        Fact(Z, boardlog.Delivered(6.0, ("c-3",))),
    ]


def test_a_task_nobody_declared_is_on_no_board_and_yields_nothing() -> None:
    snap, facts = apply(started(), declare(by=None), TaskAmended("t-1", "the operator's"))  # type: ignore[arg-type]

    assert snap.tasks["t-1"].detail == "the operator's"
    assert facts == []


def test_a_claim_given_up_by_somebody_other_than_its_holder_is_a_reclaim() -> None:
    """
    Through the reducer: the board is restored into a process where the builder
    that held the task has no record, and the session's root gives the task back.
    The record names both, and restoring the log gives the task the store holds --
    pending, and declined by the root and not by the builder.
    """
    first, facts = apply(started(), declare(), claim(B1))
    board = boardlog.restore(fact.record for fact in facts)
    snap, _ = _apply(Snapshot.empty(), spawn(LEAD, None, None, 6.0), 6.0)
    snap, _ = _apply(snap, BoardRestored(S, board, Clock(wall=7.0, monotonic=7.0)), 7.0)
    assert snap.tasks["t-1"].claimed_by == B1 == first.tasks["t-1"].claimed_by

    after, derived = apply(snap, TaskReleased(LEAD, "t-1", at=9.0), now=9.0)

    assert derived == [
        Fact(
            S,
            boardlog.Reclaimed(
                at=9.0,
                task_id="t-1",
                by="lead",
                holder="builder",
                by_id=(S,),
                holder_id=(S, "a1"),
            ),
        ),
    ]
    assert after.tasks["t-1"].released_by == (LEAD,)
    relogged = logged([*facts, *derived], S)
    assert not relogged.short
    assert relogged.tasks == (as_restored(after.tasks["t-1"]),)


def test_a_reclaim_from_an_earlier_run_in_this_window_names_the_holder_by_its_address() -> None:
    """
    No restore: the session is started again in one window, so the builder that
    held the task still has a record and the board calls it ``builder, from an
    earlier run``. The record is written with the address alone -- the qualifier
    is a reading of one moment, and restored as a name it would be an agent
    nobody ever was -- and the log restores to the board the store holds.
    """
    snap, facts = apply(started(), declare(), claim(B1))
    snap, _ = apply(snap, AgentFinished(B1, AgentState.DONE, 6.0), spawn(LEAD, None, None, 7.0))
    assert role_name(snap, B1, S) == f"builder, {EARLIER_RUN}"

    after, derived = apply(snap, TaskReleased(LEAD, "t-1", at=9.0), now=9.0)

    assert derived == [
        Fact(
            S,
            boardlog.Reclaimed(
                at=9.0,
                task_id="t-1",
                by="lead",
                holder="builder",
                by_id=(S,),
                holder_id=(S, "a1"),
            ),
        ),
    ]
    relogged = logged([*facts, *derived], S)
    assert not relogged.short
    assert relogged.tasks == (as_restored(after.tasks["t-1"]),)


def test_a_close_is_recorded_as_the_operators_and_restores_to_the_task_the_store_holds() -> None:
    """
    Its own record kind, carrying the reason as typed. Restoring the log gives a
    finished task nobody holds whose result is the store's composition -- and a
    ``Completed`` could not have: one reducer appends that only for the holder.
    """
    snap, facts = apply(started(), declare(), claim(B1))

    after, derived = apply(snap, TaskClosed("t-1", " no longer wanted\n", at=8.5), now=9.0)

    (notice,) = after.concerns.values()
    assert derived == [
        Fact(S, boardlog.Closed(at=8.5, task_id="t-1", reason=" no longer wanted\n")),
        # What the holder was sent, in the same log and after the close it reports.
        # The operator has no address on a board, and the id says who it was.
        Fact(
            S,
            boardlog.Posted(
                at=8.5,
                concern_id=notice.id,
                sender="",
                recipient="builder",
                subject=notice.subject,
                body=" no longer wanted\n",
                task_id="t-1",
                sender_id=(S, "operator/"),
                recipient_id=B1,
            ),
        ),
    ]
    relogged = logged([*facts, *derived], S)
    assert not relogged.short
    assert relogged.tasks == (as_restored(after.tasks["t-1"]),)
    assert relogged.tasks[0].closed_by_operator and relogged.tasks[0].holder is None
    (kept,) = relogged.concerns
    assert (kept.id, kept.sender, kept.body) == (notice.id, notice.sender, notice.body)

    _, again = apply(after, TaskClosed("t-1", "a second reason"), TaskClosed("missing", "x"))
    assert again == []


def test_the_operators_notice_is_the_operators_on_the_board_before_and_after_a_restart() -> None:
    """
    The sender of the notice is a stand-in with no record and no address, which
    is every mark of a node the board cannot name -- and, once a restored log
    names it, of an agent of an earlier run. The board calls it the operator
    in both processes, and the party the log restores is the same stand-in.
    """
    from pptmstr.board import OPERATOR, board_concerns, restored_table
    from pptmstr.model import is_operator, operator_party

    snap, facts = apply(started(), declare(), claim(B1))
    after, derived = apply(snap, TaskClosed("t-1", "not wanted", at=8.5), now=9.0)

    (live,) = board_concerns(after, S)
    assert (live.sender, live.recipient) == (OPERATOR, "builder")

    fresh, _ = _apply(Snapshot.empty(), spawn(LEAD, None, None, 6.0), 6.0)
    fresh = restoring(fresh, S, logged([*facts, *derived], S))

    (kept,) = fresh.concerns.values()
    assert kept.sender == operator_party(S) and is_operator(kept.sender)
    # What would have named it an unnameable agent of an earlier run: the
    # restore lists it among the log's parties, under no address.
    assert fresh.left_behind(kept.sender) == "" and fresh.of_earlier_run(kept.sender)
    (row,) = board_concerns(fresh, S)
    assert row.sender == OPERATOR
    assert row.recipient == "builder, from an earlier run"
    assert role_name(fresh, kept.sender, S) == OPERATOR
    # And it reserves no address a new agent could be refused.
    assert restored_table(fresh, S) == {"builder": "a1"}


def test_a_close_nobody_held_the_task_for_is_one_record() -> None:
    snap, _ = apply(started(), declare())

    _, derived = apply(snap, TaskClosed("t-1", "not wanted", at=8.5), now=9.0)

    assert derived == [Fact(S, boardlog.Closed(at=8.5, task_id="t-1", reason="not wanted"))]


def test_a_party_the_log_knows_by_address_alone_is_written_back_by_address_alone() -> None:
    """
    The store stands a node in for such a party. A record that carried the
    stand-in as an id would name a different party from the one the rest of the
    log names, and the reclaim that ends the contest would itself be contested.
    """
    old = [
        boardlog.Declared(at=1.0, task_id="t-1", title="T", declared_by="lead"),
        boardlog.Claimed(at=2.0, task_id="t-1", by="builder-2"),
    ]
    snap, _ = _apply(Snapshot.empty(), spawn(LEAD, None, None, 6.0), 6.0)
    restored = BoardRestored(S, boardlog.restore(old), Clock(wall=7.0, monotonic=7.0))
    snap, _ = _apply(snap, restored, 7.0)

    _, derived = apply(snap, TaskReleased(LEAD, "t-1", at=9.0), now=9.0)

    (fact,) = derived
    assert isinstance(fact.record, boardlog.Reclaimed)
    assert (fact.record.holder, fact.record.holder_id) == ("builder-2", ())
    relogged = boardlog.restore([*old, fact.record])
    assert not relogged.short
    assert relogged.tasks[0].state is TaskState.PENDING


# -- the next process ----------------------------------------------------------------

# The writer's reading of both clocks, taken after a run, and the reading of the
# process that restores: later on the wall, earlier since its own boot. Every
# figure is a multiple of a quarter, so the two conversions are exact and a
# restored time is the first process's stamp moved by ``_SHIFT``.
_WROTE = Clock(wall=10_000.0, monotonic=LENGTH + 8.0)
_READ = Clock(wall=10_512.0, monotonic=64.0)
_SHIFT = (_READ.monotonic - _READ.wall) - (_WROTE.monotonic - _WROTE.wall)


def on_disk(facts: list[Fact], session_id: str) -> boardlog.Board:
    """The board a log of ``facts`` restores to, with its times on the wall clock."""
    return boardlog.restore(f.on_the_wall(_WROTE) for f in facts if f.session_id == session_id)


def _later(at: float | None) -> float | None:
    return None if at is None else at + _SHIFT


def moved(task: Task) -> Task:
    """``task`` as the restoring process holds it: every time on its clock."""
    declared = _later(task.declared_at)
    assert declared is not None
    return dataclasses.replace(
        task,
        declared_at=declared,
        completed_at=_later(task.completed_at),
        released_at=_later(task.released_at),
        superseded=tuple(
            dataclasses.replace(prior, replaced_at=prior.replaced_at + _SHIFT)
            for prior in task.superseded
        ),
    )


def moved_concern(concern: Concern) -> Concern:
    return dataclasses.replace(
        concern,
        posted_at=concern.posted_at + _SHIFT,
        delivered_at=_later(concern.delivered_at),
    )


def boards(snap: Snapshot) -> tuple[dict[str, Task], dict[str, Concern]]:
    """Every task and concern that is on some session's board."""
    return (
        {t.id: t for t in snap.tasks.values() if t.declared_by is not None},
        dict(snap.concerns),
    )


def restoring(snap: Snapshot, session_id: str, board: boardlog.Board) -> Snapshot:
    after, effects = _apply(snap, BoardRestored(session_id, board, _READ), _READ.monotonic)
    assert effects == ()
    return after


@pytest.mark.parametrize("seed", range(40))
def test_the_driver_and_the_board_are_handed_one_table_of_a_logs_addresses(seed: int) -> None:
    """
    A log's addresses reach a resumed session twice: ``app._addresses_of`` reads
    the log for the driver, and the board reads back the ``Restore.parties`` the
    store made of the same log. Nothing makes those one value but this -- and an
    address in one and not the other is a name the driver steps over and the
    board hands out, or the reverse.
    """
    from pptmstr import app, board

    _, facts = run(seed)
    seen = 0
    for session_id in (S, Z):
        log = on_disk(facts, session_id)
        snap = restoring(Snapshot.empty(), session_id, log)
        table = app._addresses_of(log, session_id)
        assert board.restored_table(snap, session_id) == table
        # Every sub-agent this session's log names by id is in it once.
        ids = [node[1] for node, _ in log.addresses if node[0] == session_id and node[1]]
        assert sorted(i for i in table.values() if i is not None) == sorted(ids)
        seen += len(table)
    assert seen


@pytest.mark.parametrize("seed", range(40))
def test_a_log_restored_into_another_process_is_the_board_the_first_one_held(seed: int) -> None:
    """
    The whole path: a run's facts, written on the wall clock, read back and
    handed to an empty store. Every task and concern of both sessions is there,
    node for node, with its times moved onto the new clock; the report says it is
    whole and may be appended behind; and handing either board over again is not
    a step at all.
    """
    first, facts = run(seed)
    snap = Snapshot.empty()
    for session_id in (S, Z):
        board = on_disk(facts, session_id)
        snap = restoring(snap, session_id, board)
        report = snap.restores[session_id]
        assert report.unplaced == () and report.adoptable and not report.short
        assert (report.tasks, report.concerns) == (len(board.tasks), len(board.concerns))
        assert report.at == _READ.monotonic

    tasks, concerns = boards(first)
    assert boards(snap)[0] == {task_id: moved(task) for task_id, task in tasks.items()}
    assert boards(snap)[1] == {cid: moved_concern(c) for cid, c in concerns.items()}

    for session_id in (S, Z):
        assert restoring(snap, session_id, on_disk(facts, session_id)) is snap


@pytest.mark.parametrize("seed", range(40))
def test_a_log_read_again_on_a_later_clock_is_still_the_same_board(seed: int) -> None:
    """
    A second reading of one log rebuilds every time from a different pair of
    clocks. That is not a different board: nothing is placed, nothing is refused,
    and the instant the restore is dated from does not move.
    """
    _, facts = run(seed)
    board = on_disk(facts, S)
    snap = restoring(Snapshot.empty(), S, board)

    later = Clock(wall=_READ.wall + 33.3, monotonic=_READ.monotonic + 33.1)
    again, _ = _apply(snap, BoardRestored(S, board, later), later.monotonic)

    assert again is snap


def test_a_board_from_part_of_a_log_meets_the_rest_whole_or_not_at_all() -> None:
    """
    A store that holds the board of a log's first half is handed the board of
    all of it, and the other way round. Either every fact the second board holds
    is then in the store, or the store is exactly as it was and the report says
    the restore was refused -- never some of one board laid over the other.
    """
    refused = 0
    for seed in range(40):
        _, facts = run(seed)
        half = facts[: len(facts) // 2]
        for first_facts, then_facts in ((half, facts), (facts, half)):
            first, then = on_disk(first_facts, S), on_disk(then_facts, S)
            snap = restoring(Snapshot.empty(), S, first)
            after = restoring(snap, S, then)
            report = after.restores[S]
            if report.refused:
                refused += 1
                assert not report.adoptable
                assert dict(after.tasks) == dict(snap.tasks)
                assert all(after.tasks[k] is snap.tasks[k] for k in snap.tasks)
                assert dict(after.concerns) == dict(snap.concerns)
                assert report.at == snap.restores[S].at
                assert report.parties == snap.restores[S].parties
            else:
                assert boards(after) == boards(restoring(Snapshot.empty(), S, then))
    assert refused > 40, "a log that moved on is the ordinary case and must be reached"


@pytest.mark.parametrize("seed", range(20))
def test_a_store_holding_some_of_a_board_exactly_is_given_the_rest(seed: int) -> None:
    """
    Some of the log's tasks and concerns are already in the store as the log has
    them, and nothing of the session's is there that the log lacks. That is not a
    different board: what is missing is placed, what is there is left as the
    object it was, and the result is the board whole.
    """
    _, facts = run(seed)
    board = on_disk(facts, S)
    rng = random.Random(seed)
    some = dataclasses.replace(
        board,
        tasks=tuple(t for t in board.tasks if rng.random() < 0.5),
        concerns=tuple(c for c in board.concerns if rng.random() < 0.5),
    )
    assert some != board
    snap = restoring(Snapshot.empty(), S, some)

    after = restoring(snap, S, board)

    report = after.restores[S]
    assert not report.refused and report.unplaced == ()
    assert (report.tasks, report.concerns) == (len(board.tasks), len(board.concerns))
    assert boards(after) == boards(restoring(Snapshot.empty(), S, board))
    assert all(after.tasks[k] is snap.tasks[k] for k in snap.tasks)


@pytest.mark.parametrize("seed", range(10))
def test_a_board_the_store_reached_is_never_one_the_restore_calls_incoherent(seed: int) -> None:
    """
    The restore refuses a task no step of the store produces. This is the other
    side: after every step of a run, the board the store holds, handed back, is
    found there whole -- so nothing one reducer did is turned away as impossible.
    """
    for step in steps(seed):
        for session_id in (S, Z):
            tasks, concerns = held(step.after, session_id)
            board = boardlog.Board(tasks=tuple(tasks.values()), concerns=tuple(concerns.values()))
            clock = Clock(wall=500.0, monotonic=500.0)
            after, _ = _apply(step.after, BoardRestored(session_id, board, clock), 500.0)
            report = after.restores[session_id]
            assert report.unplaced == (), (step.number, step.intent, report.unplaced)
            assert (report.tasks, report.concerns) == (len(tasks), len(concerns))
            assert after.tasks == step.after.tasks and after.concerns == step.after.concerns


# -- what a case may look at -----------------------------------------------------------

_BOARD_INTENTS = frozenset(_BOARD) - {ApprovalRequested}


class _Untouchable:
    """A snapshot no case may read: any attribute is a failure."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"a case that yields nothing read Snapshot.{name}")


@pytest.mark.parametrize("kind", sorted(set(_MAKERS) - _BOARD_INTENTS, key=lambda kind: str(kind)))
def test_an_intent_that_is_not_about_a_board_is_answered_without_reading_one(kind: type) -> None:
    """
    These arrive at the rate of tokens and tool calls. A case that looked across
    the tasks for each would cost a scan that grows for the life of the process.
    """
    intent = _MAKERS[kind](random.Random(0), started(), 1.0)
    untouchable: Any = _Untouchable()

    assert outcomes(untouchable, intent, untouchable, 1.0) == ()


def test_the_intents_answered_without_a_board_are_the_ones_no_run_derives_a_fact_from() -> None:
    """
    The other half of the test above: an intent counted as not about a board,
    which a run then derives a record from, is in the wrong set.
    """
    yielding = {type(step.intent) for seed in range(20) for step in steps(seed) if step.facts}

    assert yielding == _BOARD_INTENTS


# -- times ---------------------------------------------------------------------------


def test_a_fact_is_given_to_a_log_on_the_wall_clock_and_restores_to_the_stores_stamp() -> None:
    _, facts = apply(started(), declare(), now=40.0)
    clock = Clock(wall=1_760_000_000.0, monotonic=50.0)

    (record,) = (fact.on_the_wall(clock) for fact in facts)

    # Declared at 2.0 on a clock now reading 50.0: 48 seconds ago.
    assert record.at == 1_760_000_000.0 - 48.0
    assert clock.monotonic_at(record.at) == 2.0
    assert dataclasses.replace(record, at=2.0) == facts[0].record


def test_the_module_writes_no_address_of_its_own() -> None:
    """The names it writes are the board's, asked of each node's own session."""
    assert boardfacts._address(started(), None) == ""
    assert boardfacts._address(started(), GHOST) == ""
    assert boardfacts._address(started(), Z_BUILDER) == "builder"
    assert boardfacts._address(started(), LEAD) == "lead"


def test_a_decision_is_in_no_log_whatever_becomes_of_it() -> None:
    """
    Not a board fact: nothing is derived when one is raised, answered, dismissed,
    withdrawn or read, though each of those changed the store.
    """
    snap = started()
    # Blocking nothing: a decision that names a task is refused unless the task
    # is on its asker's board, and this board is empty.
    raised = DecisionRaised(B1, dataclasses.replace(_decision(random.Random(0)), blocks=()), "rq")
    did = raised.decision.id
    for intent in (
        raised,
        DecisionsRead(B1, "rq", 2.0),
        DecisionWithdrawn(B1, did),
        DecisionRaised(B1, dataclasses.replace(raised.decision, id="d-b")),
        DecisionAnswered("d-b", "yes"),
        DecisionsRead(B1, "rq", 3.0),
        DecisionRaised(B1, dataclasses.replace(raised.decision, id="d-c")),
        DecisionDismissed("d-c"),
    ):
        after, _ = _apply(snap, intent, 2.0)
        assert after.decisions != snap.decisions or isinstance(intent, DecisionsRead), intent
        assert outcomes(snap, intent, after, 2.0) == (), intent
        snap = after
