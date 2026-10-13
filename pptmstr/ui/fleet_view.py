"""
Which sub-agents of a session are drawn, and which are folded into a count.

A filter on what is drawn and never a removal from the store. ``Snapshot.left_behind``
reads "has no record" as "never had one in this process", so taking a finished
sub-agent's record away would make it an agent of another process and hand its claims
to the session's root. Folding takes a snapshot and gives back two tuples of ids; the
snapshot is the one it was given.

**A sub-agent is folded only when nothing about it is waiting on anyone.** It has
ended, the operator owes it nothing, it holds no task, nothing is waiting in its inbox,
no answer to a decision it raised and no result of a command it asked for is
waiting for it to read, everything beneath it is folded too, and the cursor is not
on it. Each of those is read from where the store
keeps it, not from a second notion kept here:

==========================  ===========================================================
ended                       ``AgentState.is_terminal``
the operator owes it        an entry of ``Snapshot.needs_you`` whose ``node`` it is --
                            its approvals, its unacknowledged failure, and every open
                            decision it raised (``DecisionOwed.node`` is the asker)
an open command             a ``Command`` it asked that is asked or running
                            (``Snapshot.commands_awaiting``). Asked of the records as
                            well as of ``needs_you``, as ``Snapshot.detach_refusal``
                            asks: what can still run on the operator's machine is
                            not folded away on the strength of a row being drawn
holds a task                a ``Task`` in ``CLAIMED`` whose ``claimed_by`` it is
mail waiting                a ``Concern`` in ``POSTED`` addressed to it, which is what
                            ``Snapshot.inbox_of`` returns
an answer it has not read   a ``Decision`` it raised that the operator answered or
                            dismissed, with no ``first_delivered_at``
a result it has not read    a ``Command`` it asked that has ended some way other
                            than by its own withdrawal, with no
                            ``first_delivered_at``
beneath it                  ``AgentRecord.parent``, over ``Snapshot.subagents_of``
==========================  ===========================================================

**Reviving is not a case.** A woken sub-agent is ``THINKING`` (``intents.AgentResumed``),
which is not terminal, so the next call draws it. Nothing here remembers what it folded
last time, and that is what makes the answer to "does a revived sub-agent reappear" yes
without an arm to keep it true.

**A withdrawn decision is not an unread answer.** The asker withdrew it itself and
nothing stamps ``first_delivered_at`` on one, so counting it would keep every sub-agent
that ever took a question back on the card for good. This is the reading ``bus._unread``
and ``decision_view.unread`` already give. A withdrawn command is the same, and is the
reading ``command_view.delivery_line`` gives.

Imports no imgui, so the rule is testable without a GL context.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from ..command import Withdrawn as CommandWithdrawn
from ..model import Answered, ConcernState, Dismissed, NodeId, Snapshot, TaskState

__all__ = ["Folded", "fold"]


@dataclass(frozen=True, slots=True)
class Folded:
    """
    One session's sub-agents, split into the ones to draw and the ones that fold.

    ``folded`` is every sub-agent the rule folds, whether or not the fold is open:
    opening one changes what is drawn and not what is counted, so the row can say
    "3 ended" closed and "3 ended, shown" open from the same field.

    Both tuples are in tree order (``Snapshot.order``), and ``drawn`` holds a node's
    ancestors whenever it holds the node: a node is folded only when everything
    beneath it is.
    """

    drawn: tuple[NodeId, ...]
    folded: tuple[NodeId, ...]
    # Whether the operator has this root's fold open. Carried so the row's wording
    # and the drawn list cannot be computed from two different readings of it.
    opened: bool

    @property
    def count(self) -> int:
        return len(self.folded)


def fold(
    snap: Snapshot,
    root: NodeId,
    cursor: NodeId | None,
    hide_ended: bool,
    opened: Collection[NodeId] = frozenset(),
) -> Folded:
    """
    The sub-agents of the session ``root`` to draw, and the ones folded away.

    ``cursor`` is the node the operator is on, or None. A cursor on an obligation
    needs no translating: the node that obligation names is drawn for owing it.

    ``opened`` is the caller's: the roots whose fold the operator expanded. An open
    fold draws everything and still reports what would fold.

    With ``hide_ended`` off nothing folds and nothing is counted, so no row appears.

    A root the snapshot does not hold has no sub-agents, and the answer is empty.
    """
    is_open = root in opened
    members = tuple(rec.node_id for rec in snap.subagents_of(root))
    if not hide_ended or not members:
        return Folded(drawn=members, folded=(), opened=is_open)

    session = root[0]
    kept = _kept(snap, session)
    if cursor is not None:
        kept.add(cursor)

    # ``members`` is a pre-order walk, so reversed it reaches every node after all of
    # its descendants and one pass settles "everything beneath it is folded".
    unfolded: set[NodeId] = set()
    for nid in reversed(members):
        rec = snap.nodes[nid]
        if nid in unfolded or nid in kept or not rec.state.is_terminal:
            unfolded.add(nid)
            if rec.parent is not None:
                unfolded.add(rec.parent)

    folded = tuple(nid for nid in members if nid not in unfolded)
    drawn = members if is_open else tuple(nid for nid in members if nid in unfolded)
    return Folded(drawn=drawn, folded=folded, opened=is_open)


def _kept(snap: Snapshot, session: str) -> set[NodeId]:
    """
    Every node of ``session`` that something is waiting on, or that is holding
    something: owed by the operator, holding a claimed task, with mail waiting,
    with a command of its asked or running, or with an answer or a command's
    result it has not read.

    One pass over each collection rather than a question per node, because the caller
    is a draw function and a session can have many sub-agents.
    """
    kept: set[NodeId] = {o.node for o in snap.needs_you if o.node[0] == session}
    kept.update(
        t.claimed_by
        for t in snap.tasks.values()
        if t.state is TaskState.CLAIMED and t.claimed_by is not None
    )
    kept.update(c.recipient for c in snap.concerns.values() if c.state is ConcernState.POSTED)
    kept.update(
        d.asker
        for d in snap.decisions.values()
        if isinstance(d.outcome, Answered | Dismissed) and d.first_delivered_at is None
    )
    kept.update(c.asker for c in snap.commands_awaiting(session))
    kept.update(
        c.asker
        for c in snap.commands.values()
        if c.end is not None
        and not isinstance(c.end.outcome, CommandWithdrawn)
        and c.first_delivered_at is None
    )
    return kept
