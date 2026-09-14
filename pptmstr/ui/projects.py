"""
The project axis: grouping sessions by the directory they run in.

Presentation, not store. ``AgentRecord`` carries ``cwd`` -- the fact -- and this
module decides what a "project" is, which is a display judgement that a store has
no business freezing. Inventing a ``Project`` record before the grouping has proved
useful would be backwards; if it earns one later, this is the code that gets
replaced, not the schema.

Imports no imgui, so the derivation is testable without a GL context.
"""

from __future__ import annotations

from pathlib import Path

from .. import tree
from ..model import NodeId, Snapshot

# cwd string -> project name. Unbounded in principle and tiny in practice: one entry
# per distinct working directory the operator has launched into.
#
# It exists because the derivation stats the filesystem and the callers are draw
# functions. Walking to a git root once per card per frame would put filesystem I/O
# on the 60fps path to answer a question whose answer does not change.
_CACHE: dict[str, str] = {}

# What a session with no directory of its own is filed under. Reached by the fake
# driver and by tests; a real session always carries the launcher's cwd.
UNFILED = "unfiled"


def project_key(cwd: str | None) -> str:
    """
    The project a working directory belongs to.

    The enclosing git root's name, because that is the unit an operator thinks in --
    ``~/Source/orbital/tools/parsers`` and ``~/Source/orbital`` are one project, and
    grouping them apart would split a repo across two lanes for no reason the
    operator can see. Falls back to the directory's own name when nothing encloses
    it, which is the right answer for a scratch directory.

    Cheap after the first call for a given directory; see ``_CACHE``.
    """
    if cwd is None:
        return UNFILED
    cached = _CACHE.get(cwd)
    if cached is None:
        cached = _derive(cwd)
        _CACHE[cwd] = cached
    return cached


def _derive(cwd: str) -> str:
    """
    The display name for the directory ``tree.repo_root`` files this cwd under.

    The walk itself is not here. It decides which directory a session's writes are
    measured relative to, which is a store-facing fact rather than a display
    judgement, and two walks that answered differently would put a session in one
    project on screen and another in its units.
    """
    root = tree.repo_root(cwd)
    return Path(root).name or root


def roots(snap: Snapshot) -> list[NodeId]:
    """Root sessions, in spawn order. Sub-agents are pips on a card, never cards."""
    return [nid for nid in snap.order if snap.nodes[nid].parent is None]


def group_roots(snap: Snapshot) -> list[tuple[str, list[NodeId]]]:
    """
    Root sessions grouped by project, both axes in stable spatial order.

    Projects appear in the order their first session was launched, and sessions
    within a project in spawn order. **Neither ever re-sorts.** The rail and the
    inbox want two different orderings over the same set -- the inbox is urgency
    order and reorders constantly -- and a card grid only earns its space if
    position is stable enough to build muscle memory. Sorting the rail by urgency
    too would produce motion instead of a map, and leave two inboxes with the worse
    one on the left.

    Urgency rides on a card as a badge. Never as position.
    """
    groups: dict[str, list[NodeId]] = {}
    for nid in roots(snap):
        groups.setdefault(project_key(snap.nodes[nid].cwd), []).append(nid)
    return list(groups.items())
