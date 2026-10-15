"""
The board records one reducer step produced. Pure: two snapshots and the intent
between them.

**The intent decides which record; the snapshot after says what is in it.** A
difference between two snapshots cannot name a record on its own -- a task that
went from held to pending was released or was reclaimed depending on who asked,
and who amended, who finished and when are on the intent and nowhere in the
difference. So ``outcomes`` is a ``match`` over ``Intent``, and each case reads
what the reducer settled off ``after``: the normalised property, the result with
its blank decided, the dependencies the store added.

**The safety argument is that the match is closed.** Every member of ``Intent``
has a case, the ones that are not about a board are listed by name and return
nothing, and the default is ``assert_never``. An intent added to the union with no
case here is a type error at that line, and ``tests/test_boardfacts.py`` fails
when its generator does not build the new member. An arm of the reducer that
changed a board fact with no case here would persist nothing while every test of
the reducer passed, which is the failure this shape is for.

**Nothing is derived from a step that changed nothing.** A refused intent leaves
the task or concern it named as the same object, and that is asked first. It is
asked first and not only: an arm can replace a record with an equal one -- an
approved ``Bash`` call adds an empty measurement to the task its caller holds --
and a record for that is a file per call that says nothing. So the fields are
compared after the identity.

**No case scans the board for an intent that arrives at a high rate.** The cases
that return nothing look at nothing. A claim that names no task, an inbox read and
a resolved approval each look across the tasks or the concerns once, as the
reducer arm they follow does.

**A fact goes to one session's log**, and ``Fact`` says which: a task's is its
declarer's, the session whose board shows it (``Task.belongs_to``), and a
concern's is its sender's (``board.board_concerns``). A task with no declarer is
on no board and has no log to go to, so nothing is derived for it.

**Times are the store's.** A record built here carries the monotonic stamp the
reducer used, so that it can be compared with the snapshot it came from.
``Fact.on_the_wall`` is the record a log is given.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeVar, assert_never

from . import boardlog
from .board import address_in_session
from .boardlog import Clock, Record
from .intents import (
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
from .model import (
    Basis,
    Concern,
    ConcernState,
    NodeId,
    Snapshot,
    Task,
    TaskState,
    is_address_only,
)

_T = TypeVar("_T", Task, Concern)


@dataclass(frozen=True, slots=True)
class Fact:
    """
    One record and the session whose log it belongs in.

    ``record.at`` is on the store's monotonic clock. It is not what a log holds:
    ``on_the_wall`` is.
    """

    session_id: str
    record: Record

    def on_the_wall(self, clock: Clock) -> Record:
        """
        The record as a log is given it, with its time on the wall clock.

        ``clock`` is one reading of both clocks, taken no earlier than the stamp
        it converts -- once per frame serves every fact the frame produced.
        """
        return dataclasses.replace(self.record, at=clock.wall_at(self.record.at))


def outcomes(before: Snapshot, intent: Intent, after: Snapshot, now: float) -> tuple[Fact, ...]:
    """
    The board facts ``intent`` produced when the reducer took ``before`` to
    ``after`` at ``now``, in the order a log should hold them.

    ``now`` is the instant ``store._apply`` was given: an arm that stamps a time
    the intent did not carry stamps that one, and so does this.
    """
    match intent:
        case (
            AgentSpawned()
            | FailureAcknowledged()
            | StateChanged()
            | TopicChanged()
            | UsageAccrued()
            | ContextPolled()
            | CompactionObserved()
            | ApprovalRequested()
            | AgentFinished()
            | SubagentProgress()
            | SubagentDelivered()
            | AgentResumed()
            | AgentRemoved()
            | BoardRead()
            | DigestRead()
        ):
            return ()

        case (
            DecisionRaised()
            | DecisionAnswered()
            | DecisionDismissed()
            | DecisionWithdrawn()
            | DecisionsRead()
        ):
            # A decision is not a board fact and is in no log: it does not
            # survive a restart of pptmstr.
            return ()

        case (
            CommandRequested()
            | CommandApproved()
            | CommandRefused()
            | CommandWithdrawn()
            | CommandStopAsked()
            | CommandEnded()
            | CommandsRead()
            | CommandsRestored()
        ):
            # A command is not a board fact. Its record is its own files,
            # under the session's ``commands/``.
            return ()

        case SessionRecalled() | SessionDetached() | FailedSessionClosed():
            # None of them touches a board. A recalled session's board is not
            # read until it is resumed, and one taken off the fleet, or closed
            # after it failed, keeps its board as it stands.
            return ()

        case BoardRestored():
            # A restored board came out of the log, and deriving records from it
            # would append the log to itself.
            return ()

        case TaskDeclared():
            landed = after.tasks.get(intent.task.id)
            if landed is None or intent.task.id in before.tasks:
                return ()
            return _about(
                landed,
                boardlog.Declared(
                    at=landed.declared_at,
                    task_id=landed.id,
                    title=landed.title,
                    declared_by=_address(after, landed.declared_by),
                    detail=landed.detail,
                    depends_on=landed.depends_on,
                    touches=landed.touches,
                    basis=_stated(landed.basis),
                    property=landed.property,
                    declared_by_id=_node(landed.declared_by),
                ),
            )

        case TaskClaimRequested():
            # A claim that names no task is answered with whichever was oldest,
            # so the one it won is found by what changed.
            if intent.task_id is not None:
                candidates = [after.tasks.get(intent.task_id)]
            else:
                candidates = list(after.tasks.values())
            for won in candidates:
                if won is None or won is before.tasks.get(won.id):
                    continue
                return _about(
                    won,
                    boardlog.Claimed(
                        at=now,
                        task_id=won.id,
                        by=_address(after, intent.node_id),
                        by_id=_node(intent.node_id),
                    ),
                )
            return ()

        case TaskVersionHanded():
            shown = _changed(before.tasks, after.tasks, intent.task_id)
            if shown is None or shown.claimed_version is None:
                return ()
            return _about(
                shown,
                boardlog.Handed(
                    at=now,
                    task_id=shown.id,
                    by=_address(after, intent.node_id),
                    version=shown.claimed_version,
                    by_id=_node(intent.node_id),
                ),
            )

        case TaskCompleted():
            done = _changed(before.tasks, after.tasks, intent.task_id)
            if done is None or done.completed_at is None:
                return ()
            return _about(
                done,
                boardlog.Completed(
                    at=done.completed_at,
                    task_id=done.id,
                    by=_address(after, intent.node_id),
                    result=done.result,
                    basis=_stated(done.result_basis),
                    not_run=done.not_run,
                    by_id=_node(intent.node_id),
                ),
            )

        case TaskClosed():
            closed = _changed(before.tasks, after.tasks, intent.task_id)
            if closed is None or closed.completed_at is None:
                return ()
            # The arm files one concern with a close under a holder, the
            # operator's notice to that holder, and it is found by what the step
            # added: at most one, and after the close it reports.
            notices = tuple(
                fact
                for concern_id, told in after.concerns.items()
                if concern_id not in before.concerns
                for fact in _filed(after, told)
            )
            return (
                *_about(
                    closed,
                    boardlog.Closed(
                        at=closed.completed_at, task_id=closed.id, reason=intent.reason
                    ),
                ),
                *notices,
            )

        case TaskReleased():
            back = _changed(before.tasks, after.tasks, intent.task_id)
            was = before.tasks.get(intent.task_id)
            holder = None if was is None else was.claimed_by
            if back is None or holder is None or back.released_at is None:
                return ()
            if holder == intent.node_id:
                return _about(
                    back,
                    boardlog.Released(
                        at=back.released_at,
                        task_id=back.id,
                        by=_address(after, intent.node_id),
                        by_id=_node(intent.node_id),
                    ),
                )
            # Given up by somebody other than its holder. Naming the holder as the
            # one who released would be a record of something it did not do.
            return _about(
                back,
                boardlog.Reclaimed(
                    at=back.released_at,
                    task_id=back.id,
                    by=_address(after, intent.node_id),
                    holder=_address(after, holder),
                    by_id=_node(intent.node_id),
                    holder_id=_node(holder),
                ),
            )

        case TaskAmended():
            amended = _changed(before.tasks, after.tasks, intent.task_id)
            if amended is None:
                return ()
            return _about(
                amended,
                boardlog.Amended(
                    at=now if intent.at is None else intent.at,
                    task_id=amended.id,
                    detail=amended.detail,
                    by=None if intent.node_id is None else _address(after, intent.node_id),
                    property=amended.property,
                    basis=_stated(amended.basis),
                    by_id=() if intent.node_id is None else _node(intent.node_id),
                ),
            )

        case ApprovalResolved():
            # The arm measures the write against the one task the caller holds
            # open, and replaces that task whether or not there was anything to
            # add. Which task it was is found the way the arm found it.
            if not intent.approved:
                return ()
            for held in after.tasks.values():
                if held.state is not TaskState.CLAIMED or held.claimed_by != intent.node_id:
                    continue
                prior = before.tasks.get(held.id)
                if prior is None or held is prior or held.writes == prior.writes:
                    continue
                return _about(
                    held,
                    boardlog.WritesAccrued(
                        at=now,
                        task_id=held.id,
                        paths=_added(prior.writes.paths, held.writes.paths),
                        unplaced=_added(prior.writes.unplaced, held.writes.unplaced),
                        lines_added=held.writes.lines_added - prior.writes.lines_added,
                        lines_removed=held.writes.lines_removed - prior.writes.lines_removed,
                    ),
                )
            return ()

        case ConcernPosted():
            filed = after.concerns.get(intent.concern.id)
            if filed is None or intent.concern.id in before.concerns:
                return ()
            return _filed(after, filed)

        case ConcernEdited():
            edited = _changed(before.concerns, after.concerns, intent.concern_id)
            if edited is None:
                return ()
            return (Fact(edited.sender[0], boardlog.Edited(now, edited.id, edited.body)),)

        case ConcernWithdrawn():
            stopped = _changed(before.concerns, after.concerns, intent.concern_id)
            if stopped is None:
                return ()
            return (Fact(stopped.sender[0], boardlog.Withdrawn(now, stopped.id)),)

        case InboxRead():
            # One read can hand over concerns from more than one sender's session,
            # and each session's log is told about its own.
            handed: dict[str, list[str]] = {}
            for waiting in before.inbox_of(intent.node_id):
                delivered = after.concerns.get(waiting.id)
                if delivered is not None and delivered is not waiting:
                    handed.setdefault(delivered.sender[0], []).append(delivered.id)
            return tuple(
                Fact(session_id, boardlog.Delivered(intent.at, tuple(ids)))
                for session_id, ids in handed.items()
            )

        case _:
            assert_never(intent)


def _changed(before: Mapping[str, _T], after: Mapping[str, _T], key: str) -> _T | None:
    """
    The task or concern under ``key`` as the step left it, when the step changed
    it. None when it did not: the thing is absent, is the same object, or was
    replaced by an equal one.
    """
    was = before.get(key)
    now = after.get(key)
    if now is None or now is was or now == was:
        return None
    return now


def _about(task: Task, record: Record) -> tuple[Fact, ...]:
    """
    ``record`` for the log of the session ``task`` is on the board of, or nothing
    for a task that is on no board.
    """
    if task.declared_by is None:
        return ()
    return (Fact(task.declared_by[0], record),)


def _filed(snap: Snapshot, concern: Concern) -> tuple[Fact, ...]:
    """
    A concern as the store filed it.

    The store files the record it is handed, and the bus hands it one that is
    waiting and untouched. A caller that hands over one already rewritten, handed
    on or stopped has that filed too, so what became of it is derived with it and
    a restore reads the concern the store holds.
    """
    session_id = concern.sender[0]
    records: list[Record] = [
        boardlog.Posted(
            at=concern.posted_at,
            concern_id=concern.id,
            sender=_address(snap, concern.sender),
            recipient=_address(snap, concern.recipient),
            subject=concern.subject,
            body=concern.body,
            task_id=concern.task_id,
            basis=_stated(concern.basis),
            incident=concern.incident,
            sender_id=_node(concern.sender),
            recipient_id=_node(concern.recipient),
        )
    ]
    if concern.edited:
        records.append(boardlog.Edited(concern.posted_at, concern.id, concern.body))
    if concern.state is ConcernState.WITHDRAWN:
        records.append(boardlog.Withdrawn(concern.posted_at, concern.id))
    elif concern.state is ConcernState.DELIVERED and concern.delivered_at is not None:
        records.append(boardlog.Delivered(concern.delivered_at, (concern.id,)))
    return tuple(Fact(session_id, record) for record in records)


def _node(node: NodeId | None) -> tuple[str, ...]:
    """
    A node as a record's ``*_id`` field holds one: the session alone for its root,
    the session and the agent id for a sub-agent, nothing for no node.

    Nothing, too, for a party the store took from a log that named it by address
    alone. Its node is a stand-in the store made up, and a record carrying it
    would be about a different party from the one every earlier record in the
    same log names.
    """
    if node is None or is_address_only(node):
        return ()
    return (node[0],) if node[1] is None else (node[0], node[1])


def _address(snap: Snapshot, node: NodeId | None) -> str:
    """
    What ``node``'s own session calls it, or the empty string when nothing there
    answers to it.

    The address alone (``board.address_in_session``), so the answer never
    carries a qualifier a board puts after a name -- for another session's
    agent, for one of an earlier run -- nor the board's word for a node with no
    record: each is something a board says about a node, and written as an
    address it would be read back as a name some agent has.

    A party a restored log names is written under the address that log had for
    it, which is a name an agent did have.
    """
    return "" if node is None else address_in_session(snap, node)


def _stated(basis: Basis | None) -> str | None:
    return None if basis is None else basis.value


def _added(had: tuple[str, ...], has: tuple[str, ...]) -> tuple[str, ...]:
    """The paths in ``has`` that ``had`` did not hold, in the order they were added."""
    return tuple(path for path in has if path not in had)
