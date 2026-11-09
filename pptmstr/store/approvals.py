"""
A parked call's row: how one waiting on a peer is described, and which tasks
an approved write lands on.

Pure: no SDK, no clock, no IO. Nothing outside ``pptmstr.store`` imports it
(``tests/test_layers.py``); ``store._apply`` is the step it is part of.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType

from ..approval import Summary, peer_channel, summary_of
from ..channels.records import WITHDRAW_PEER_TOOL, PeerRefusal
from ..effects import peer_forms, peer_identity
from ..model import AgentRecord, NodeId, PendingApproval, Snapshot, Task, TaskId, TaskState


def _peer_row(snap: Snapshot, pending: PendingApproval) -> PendingApproval:
    """
    ``pending`` with its queue row naming the session it would reach now, when
    it is a call that reaches another team's lead (``approval.peer_channel``),
    and as it was otherwise.

    The driver summarises a call before the store has seen it, and only the
    store knows whom a channel's name reaches, so the row is finished here: when
    the call parks, and again whenever a link changes (``_peer_rows``). The
    session is named as every lead is told of it (``effects.peer_text``): its
    id's head, its card's title and its project.

    A link that went live after the call parked is not one the call can reach:
    ``requested_at`` is the call's stamp (``driver._park``), and ``PeerSent`` or
    ``DecisionRaised`` refuses it (``Snapshot.linked_after``), so the row says
    that and names nobody. An ``re`` the send would be refused for is not said
    here: whether the store holds the message it cites changes on a resume, and
    the row is rewritten only when a link changes.

    The session is said in the longest of its descriptions that leaves the
    call's subject whole on the row (``effects.peer_forms``, ``summary_of``).

    A withdrawal names a message and no channel, so its link is the one the
    store finds that message on (``Snapshot.parked_link``), and the row names
    that link and the message's recipient. A message this call's agent did
    not send to another team's lead is said to be refused, with no link: the
    row is never put on a link the call does not act across.
    """
    if pending.tool_name == WITHDRAW_PEER_TOOL:
        return _withdrawal_row(snap, pending)
    channel = peer_channel(pending.tool_name, pending.raw_args)
    if channel is None:
        return pending
    route = snap.peer_route(pending.node, channel)
    reaches: str | tuple[str, ...]
    if isinstance(route, PeerRefusal):
        reaches = "it reaches no lead now"
    elif snap.linked_after(route[0], pending.requested_at):
        reaches = "will be refused: the link is newer than this call"
    else:
        reaches = peer_forms(peer_identity(snap, route[1][0]))
    return _summarised(pending, summary_of(pending.tool_name, pending.raw_args, reaches=reaches))


def _withdrawal_row(snap: Snapshot, pending: PendingApproval) -> PendingApproval:
    """
    ``_peer_row`` for a parked ``withdraw_peer_message``: the link the
    withdrawn message went over and the lead it went to, from the store.
    """
    named = str(pending.raw_args.get("message_id") or "").strip()
    message = snap.concerns.get(named)
    across: str | None = None
    reaches: str | tuple[str, ...]
    if message is None or message.sender != pending.node or not message.is_peer:
        reaches = "will be refused: this lead sent no such message to another team"
    else:
        across = message.channel
        if snap.parked_link(pending) is None:
            reaches = "it reaches no lead now"
        else:
            reaches = peer_forms(peer_identity(snap, message.recipient[0]))
    return _summarised(
        pending,
        summary_of(pending.tool_name, pending.raw_args, reaches=reaches, across=across),
    )


def _summarised(pending: PendingApproval, summary: Summary) -> PendingApproval:
    """
    ``pending`` with ``summary`` as its row, and the span in it of the lead's
    description (``PendingApproval.summary_peer``), as ``summary_of`` composed
    them.
    """
    if summary.text == pending.summary and summary.peer == pending.summary_peer:
        return pending
    return dataclasses.replace(pending, summary=summary.text, summary_peer=summary.peer)


def _peer_rows(snap: Snapshot, nodes: dict[NodeId, AgentRecord]) -> dict[NodeId, AgentRecord]:
    """
    ``nodes`` with every parked call that reaches another team's lead
    re-summarised against ``snap``'s links and records (``_peer_row``). Asked
    on every step that changes a link, so a row approved after the operator
    closed a name and linked it again names the session it goes to -- and
    ``PeerSent`` refuses it then if that link is newer than the call
    (``Snapshot.linked_after``) -- and on every step that changes how a lead
    is described (``_redescribed``), so the row names a card as the leads'
    own lines do.
    """
    for node_id, rec in list(nodes.items()):
        rows = tuple(_peer_row(snap, p) for p in rec.pending)
        if any(a is not b for a, b in zip(rows, rec.pending, strict=True)):
            nodes[node_id] = rec.with_(pending=rows)
    return nodes


def _redescribed(was: Snapshot, nodes: Mapping[NodeId, AgentRecord]) -> bool:
    """
    Whether a step that leaves ``nodes`` changed the description of some lead
    (``effects.peer_identity``): a root record added or dropped, or one whose
    description differs. False while nothing is parked, since only a parked
    row holds a description.

    The description is asked only of roots whose record object the step
    replaced, so a step that touches no root costs one pass over the records.
    """
    if not any(rec.pending for rec in nodes.values()):
        return False
    if any(n[1] is None and n not in nodes for n in was.nodes):
        return True
    now = dataclasses.replace(was, nodes=MappingProxyType(nodes))
    return any(
        node[1] is None
        and was.nodes.get(node) is not rec
        and peer_identity(was, node[0]) != peer_identity(now, node[0])
        for node, rec in nodes.items()
    )


def _claimed_tasks(tasks: dict[TaskId, Task], node_id: NodeId) -> tuple[Task, ...]:
    """
    Every task ``node_id`` is holding open. Empty and many are both ordinary.

    **Holding none is not an error.** A lead editing a file outside any claimed task
    is the common case, and there is nothing here to hang a measurement on; inventing
    a task to carry it would put a row on the board that no agent declared.

    CLAIMED only. ``TaskCompleted`` keeps ``claimed_by`` -- who did the work is worth
    reading after the fact, and ``BoardTask.owner_gone`` relies on it -- so the state
    rather than the owner is what says the work is still open. Filtering on
    ``claimed_by`` alone would return the node's first-ever task, completed, forever,
    since ``tasks`` is in declaration order.

    **Every match is returned rather than one being chosen.** Nothing in the snapshot
    says which of two open tasks a given write was for, and picking by declaration
    order would put a made-up attribution into the one series this measurement exists
    to produce. The caller records the ambiguity instead
    (``Snapshot.unattributed_writes``); how often it fires is itself a reading, since
    "one task at a time" is prose in ``templates.py`` that ``_pick_claim`` does not
    enforce.
    """
    return tuple(
        t for t in tasks.values() if t.state is TaskState.CLAIMED and t.claimed_by == node_id
    )
