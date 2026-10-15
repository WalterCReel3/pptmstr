"""
Which sub-agents of a session are drawn, and which are hidden.

A filter on what is drawn and never a removal from the store. ``Snapshot.left_behind``
reads "has no record" as "never had one in this process", so taking a finished
sub-agent's record away would make it an agent of another process and hand its claims
to the session's root. ``fold`` takes a snapshot and gives back two tuples of ids; the
snapshot is the one it was given.

**A hidden sub-agent is not drawn, not counted and not named**, and nothing on the
pane brings one back: the setting (``Settings.hide_ended_subagents``) is the only
switch, and it is the whole fleet's.

**A sub-agent is hidden only when nothing about it is waiting on anyone.** It has
ended, the operator owes it nothing, it holds no task, nothing is waiting in its inbox,
no answer to a decision it raised and no result of a command it asked for is
waiting for it to read, and everything beneath it is hidden too. Each of those is read
from where the store keeps it, not from a second notion kept here:

==========================  ===========================================================
ended                       ``AgentState.is_terminal``
the operator owes it        an entry of ``Snapshot.needs_you`` whose ``node`` it is --
                            its approvals, its unacknowledged failure, and every open
                            decision it raised (``DecisionOwed.node`` is the asker)
an open command             a ``Command`` it asked that is asked or running
                            (``Snapshot.commands_awaiting``). Asked of the records as
                            well as of ``needs_you``, as ``Snapshot.detach_refusal``
                            asks: what can still run on the operator's machine is
                            not hidden on the strength of a row being drawn
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
which is not terminal, so the next call draws it. Nothing here remembers what it hid
last time, and that is what makes the answer to "does a revived sub-agent reappear" yes
without an arm to keep it true.

**The cursor is not a case either.** What is hidden is a function of the snapshot and
the setting, so a card does not come and go as the operator moves. The cursor can
rest on a hidden sub-agent -- the operator was on its card when it ended, or answered
the last thing it owed -- and it stays there: ``rail`` lights the lead's card for it,
which is the surface a sub-agent with no card has.

**A withdrawn decision is not an unread answer.** The asker withdrew it itself and
nothing stamps ``first_delivered_at`` on one, so counting it would keep every sub-agent
that ever took a question back on the card for good. This is the reading ``bus._unread``
and ``decision_view.unread`` already give. A withdrawn command is the same, and is the
reading ``command_view.delivery_line`` gives.

Imports no imgui, so the rule is testable without a GL context.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..command import Withdrawn as CommandWithdrawn
from ..model import Answered, ConcernState, Dismissed, NodeId, Snapshot, TaskState

__all__ = ["Folded", "fold"]


@dataclass(frozen=True, slots=True)
class Folded:
    """
    One session's sub-agents, split into the ones to draw and the ones hidden.

    Both tuples are in tree order (``Snapshot.order``), every sub-agent is in exactly
    one, and ``drawn`` holds a node's ancestors whenever it holds the node: a node is
    hidden only when everything beneath it is.

    The pane reads ``drawn`` alone. ``folded`` is the rule's answer said the other way
    round, which is the form a test of one clause asks for.
    """

    drawn: tuple[NodeId, ...]
    folded: tuple[NodeId, ...]


def fold(snap: Snapshot, root: NodeId, cursor: NodeId | None, hide_ended: bool) -> Folded:
    """
    The sub-agents of the session ``root`` to draw, and the ones hidden.

    ``cursor`` is not read: where the operator is does not change what is hidden (the
    module docstring says what stands in for a card the cursor has none of).

    With ``hide_ended`` off nothing is hidden.

    A root the snapshot does not hold has no sub-agents, and the answer is empty.
    """
    members = tuple(rec.node_id for rec in snap.subagents_of(root))
    if not hide_ended or not members:
        return Folded(drawn=members, folded=())

    kept = _kept(snap, root[0])

    # ``members`` is a pre-order walk, so reversed it reaches every node after all of
    # its descendants and one pass settles "everything beneath it is hidden".
    drawn: set[NodeId] = set()
    for nid in reversed(members):
        rec = snap.nodes[nid]
        if nid in drawn or nid in kept or not rec.state.is_terminal:
            drawn.add(nid)
            if rec.parent is not None:
                drawn.add(rec.parent)

    return Folded(
        drawn=tuple(nid for nid in members if nid in drawn),
        folded=tuple(nid for nid in members if nid not in drawn),
    )


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
