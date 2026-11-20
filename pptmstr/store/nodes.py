"""
The agent tree: what a step does to the node records outside the arms that
name a node, and the order the tree is drawn in.

Pure: no SDK, no clock, no IO. Nothing outside ``pptmstr.store`` imports it
(``tests/test_layers.py``); ``store._apply`` is the step it is part of.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType

from ..command import Command, CommandId
from ..model import FINAL_STATES, AgentRecord, Decision, DecisionId, NodeId, Snapshot

# The ends an ordinary status update cannot undo. ``model.FINAL_STATES`` says
# which and why; ``_apply``'s node arms and ``owing._asker_fate`` ask it under
# this name.
_FINAL_STATES = FINAL_STATES


def _no_longer_recalled(
    nodes: Mapping[NodeId, AgentRecord],
    decisions: Mapping[DecisionId, Decision],
    commands: Mapping[CommandId, Command],
) -> list[NodeId]:
    """
    The recalled records something is owed for: one carrying an approval, or the
    asker of an open decision or an open command.

    Each stops being recalled. An approval is a call some process is parked on
    and a decision or a command is something some process asked, so the record
    is no longer one that nothing here stands behind, and a card that says it
    has no process while it asks for a review is the worse of the two wrong
    cards. None is dropped: an approval never is.

    These are the obligations a record in DONE can come to have, which is why
    they are the ones looked for. Asked once per step and not in the arms
    that add them, for the reason ``_back_on_fleet`` gives.
    """
    recalled = [nid for nid, rec in nodes.items() if rec.recalled is not None]
    if not recalled:
        return []
    asking = {d.asker for d in decisions.values() if d.is_open}
    asking |= {c.asker for c in commands.values() if c.is_open}
    return [nid for nid in recalled if nodes[nid].pending or nid in asking]


def _back_on_fleet(world: Snapshot) -> Snapshot:
    """
    ``world`` with ``detached`` cleared on every root whose session may no longer
    be off the fleet (``Snapshot.detach_refusal``).

    A step over the built snapshot for the reason ``state_since`` is stamped in
    one place: a session comes back by an agent of it running again, by an
    approval or a decision landing on it, by a new sub-agent, and an arm that
    has not been written yet cannot forget to clear a flag it never reads.

    ``Snapshot.off_fleet`` would already answer False for such a session. The
    flag is cleared as well so that the session does not go back off the fleet
    by itself when the agent that revived it ends: it left because the operator
    said so about a session that was over, and that is no longer the session
    they said it about.
    """
    back = [
        nid
        for nid, rec in world.nodes.items()
        if rec.detached and world.detach_refusal(nid[0]) is not None
    ]
    if not back:
        return world
    nodes = dict(world.nodes)
    for nid in back:
        nodes[nid] = nodes[nid].with_(detached=False)
    return dataclasses.replace(world, nodes=MappingProxyType(nodes))


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
    # Recalled roots after the ones this process launched, each group in its own
    # insertion order (``Snapshot.order``). A recalled root has no children.
    roots = children.get(None, [])
    roots.sort(key=lambda nid: nodes[nid].recalled is not None)

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
