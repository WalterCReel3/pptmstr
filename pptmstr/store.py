"""
The store: single source of truth (I1).

Confined to the main/UI thread. It holds one ``Snapshot`` reference; applying an
intent builds a replacement and swaps it. ``snapshot()`` is therefore an attribute
read (I3), which is what lets the UI take a consistent view once per frame (I2)
without deep-copying the world at frame rate.

Cost model, stated plainly because it is the load-bearing tradeoff here: applying
one intent is O(nodes), since it copies the node dict. Taking a snapshot is O(1).
That is the right way round -- snapshots happen every frame, intents happen when
something actually changes -- and it holds for the tens-of-agents scale this tool
targets. It would need revisiting in the thousands, where a persistent map would
earn its complexity; at this scale it would only add indirection.

Tree order is recomputed only when the shape of the tree changes, not on every
field update, because a pre-order walk is the one genuinely superlinear thing in
here.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import assert_never

from .intents import (
    AgentFinished,
    AgentRemoved,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    CompactionObserved,
    ContextPolled,
    Intent,
    StateChanged,
    TopicChanged,
    UsageAccrued,
)
from .model import AgentRecord, AgentState, NodeId, PendingApproval, Snapshot


class Store:
    """
    Not thread-safe, and deliberately so.

    Making it thread-safe would invite writes from the asyncio thread, which is
    exactly the design this project rejected (see intents.py). A lock here would
    make the wrong thing possible rather than making anything safe.
    """

    __slots__ = ("_snapshot",)

    def __init__(self) -> None:
        self._snapshot = Snapshot.empty()

    def snapshot(self) -> Snapshot:
        """
        O(1). Call exactly once per frame (§4.1) -- calling it twice is a bug even
        when it looks harmless, because the two results can differ and the frame
        then renders torn state.
        """
        return self._snapshot

    def apply(self, intent: Intent) -> None:
        """Apply one intent, producing a new snapshot."""
        self._snapshot = _apply(self._snapshot, intent)

    def apply_all(self, intents: Iterable[Intent]) -> None:
        """
        Apply a batch, rebuilding derived state once at the end.

        Draining a frame's worth of intents this way keeps the per-intent
        bookkeeping from running N times when one pass would do.
        """
        snap = self._snapshot
        for intent in intents:
            snap = _apply(snap, intent)
        self._snapshot = snap


def _apply(snap: Snapshot, intent: Intent) -> Snapshot:
    """
    Pure: old snapshot plus intent gives new snapshot.

    A free function rather than a method so it can be exercised without a Store, and
    so the match below is the single audit point for every mutation in the system.
    """
    nodes = dict(snap.nodes)
    reorder = False

    match intent:
        case AgentSpawned():
            parent_rec = nodes.get(intent.parent) if intent.parent else None
            depth = parent_rec.depth + 1 if parent_rec else 0
            nodes[intent.node_id] = AgentRecord(
                node_id=intent.node_id,
                parent=intent.parent,
                depth=depth,
                state=AgentState.SPAWNING,
                topic=intent.topic,
                task=intent.task,
                model=intent.model,
                agent_type=intent.agent_type,
                started_at=intent.started_at,
            )
            reorder = True

        case StateChanged():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap
            changes: dict[str, object] = {"state": intent.state}
            if intent.topic is not None:
                changes["topic"] = intent.topic
            nodes[intent.node_id] = rec.with_(**changes)

        case TopicChanged():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap
            nodes[intent.node_id] = rec.with_(topic=intent.topic)

        case UsageAccrued():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap
            nodes[intent.node_id] = rec.with_(usage=rec.usage.plus(intent.delta))

        case ContextPolled():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap
            # A poll reports occupancy; it knows nothing about compaction history.
            # Carrying the previous counts forward is what keeps COMPACTED sticky --
            # letting a fresh poll reset them would erase the strongest retire signal
            # the moment the bar dropped.
            prev = rec.context
            ctx = intent.context
            if prev is not None and prev.compactions:
                ctx = ctx.with_compaction_history(prev.compactions, prev.last_compaction_at)
            nodes[intent.node_id] = rec.with_(context=ctx)

        case CompactionObserved():
            rec = nodes.get(intent.node_id)
            if rec is None or rec.context is None:
                # Nothing polled yet, so there is no occupancy record to annotate.
                # Dropping the event loses the count; that is preferable to
                # fabricating a ContextSnapshot whose token numbers would be
                # invented. The next poll establishes the baseline.
                return snap
            nodes[intent.node_id] = rec.with_(
                context=rec.context.with_compaction_history(rec.context.compactions + 1, intent.at)
            )

        case ApprovalRequested():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap
            nodes[intent.node_id] = rec.with_(
                pending=intent.pending, state=AgentState.AWAITING_APPROVAL
            )

        case ApprovalResolved():
            rec = nodes.get(intent.node_id)
            if rec is None or rec.pending is None:
                return snap
            # Guard against a stale resolution: two approve clicks in the same frame,
            # or a decision arriving for an approval that has already been superseded.
            if rec.pending.id != intent.pending_id:
                return snap
            nodes[intent.node_id] = rec.with_(
                pending=None,
                state=AgentState.RUNNING_TOOL if intent.approved else AgentState.THINKING,
            )

        case AgentFinished():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap
            nodes[intent.node_id] = rec.with_(
                state=intent.state,
                ended_at=intent.ended_at,
                error=intent.error,
                pending=None,
            )

        case AgentRemoved():
            if intent.node_id not in nodes:
                return snap
            for nid in _subtree(snap, intent.node_id):
                nodes.pop(nid, None)
            reorder = True

        case _:
            # Not reachable at runtime; it is here for mypy, which reports an
            # unhandled Intent member as a type error at this line. That turns
            # "someone added an intent and forgot to handle it" -- a silently
            # ignored state change, and a miserable thing to debug from the UI --
            # into a failed typecheck.
            assert_never(intent)

    order = _preorder(nodes) if reorder else snap.order
    return Snapshot(
        seq=snap.seq + 1,
        nodes=MappingProxyType(nodes),
        order=order,
        review_queue=_review_queue(nodes, order),
        any_active=any(r.state.is_active for r in nodes.values()),
    )


def _subtree(snap: Snapshot, root: NodeId) -> tuple[NodeId, ...]:
    """``root`` and every descendant."""
    out = [root]
    frontier = [root]
    while frontier:
        current = frontier.pop()
        for nid, rec in snap.nodes.items():
            if rec.parent == current:
                out.append(nid)
                frontier.append(nid)
    return tuple(out)


def _preorder(nodes: dict[NodeId, AgentRecord]) -> tuple[NodeId, ...]:
    """
    Parents immediately followed by their descendants.

    Siblings keep insertion order, which is spawn order -- stable across frames, so
    a newly spawned sub-agent appears beneath its siblings rather than shuffling the
    rows above it and scrambling the widget state keyed to them (I6).
    """
    children: dict[NodeId | None, list[NodeId]] = {}
    for nid, rec in nodes.items():
        children.setdefault(rec.parent, []).append(nid)

    out: list[NodeId] = []

    def walk(parent: NodeId | None) -> None:
        for nid in children.get(parent, ()):
            out.append(nid)
            walk(nid)

    walk(None)

    # An orphan -- a sub-agent whose parent has been removed, or whose spawn intent
    # arrived before its parent's -- would otherwise vanish from the tree while
    # still sitting in the node table, taking any pending approval with it.
    # Surfacing it at the root is worse-looking and better-behaved than losing it.
    if len(out) != len(nodes):
        seen = set(out)
        out.extend(nid for nid in nodes if nid not in seen)
    return tuple(out)


def _review_queue(
    nodes: dict[NodeId, AgentRecord], order: tuple[NodeId, ...]
) -> tuple[PendingApproval, ...]:
    """
    Every pending approval, oldest request first.

    Ordered by request time rather than tree position so the queue reads as a work
    list -- the operator works the backlog, and the agent that has been blocked
    longest is the one costing the most.
    """
    pend = [nodes[nid].pending for nid in order if nid in nodes and nodes[nid].pending]
    return tuple(sorted((p for p in pend if p is not None), key=lambda p: p.requested_at))
