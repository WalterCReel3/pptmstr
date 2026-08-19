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
    try:
        # Relative paths resolve against this process's directory, which is also what
        # the SDK does with ``ClaudeAgentOptions.cwd`` -- so "." here names the same
        # place it names for the agent.
        path = Path(cwd).expanduser().resolve()
    except (OSError, RuntimeError):
        # Unresolvable (a broken symlink loop, a vanished parent). The string still
        # has a last component and that is better than reporting nothing.
        return Path(cwd).name or cwd

    for candidate in (path, *path.parents):
        try:
            # Not is_dir(): a worktree or submodule checkout has .git as a *file*
            # holding a gitdir pointer, and treating those as unenclosed would file
            # every worktree under its own name instead of its repo's.
            if (candidate / ".git").exists():
                return candidate.name or str(candidate)
        except OSError:
            # A directory we cannot stat is not a git root as far as we can tell.
            # Keep walking rather than aborting the whole derivation.
            continue
    return path.name or str(path)


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
