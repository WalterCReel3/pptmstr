"""
The working tree, as the filesystem has it.

Everything here touches disk, so nothing here may be called from the reducer. The
split is the same one ``sessions.py`` makes and for the same reason: the blocking
lives behind one name, and what a caller does with the answer stays pure.

**Why the base is a value and not a lookup.** ``relative_write`` puts a written path
into the units a declaration is written in, and ``bus.declare_task`` tells every lead
those units are relative to the directory the session was launched in. Resolving that
directory at comparison time would need the filesystem inside ``store._apply``, which
does no IO; so it is resolved once, at launch, and carried on the record. Carrying the
answer is also what freezes the units for the session's lifetime: a symlink on the
operator's path re-pointed mid-run would otherwise re-base writes recorded before it
moved, and a session's units would change underneath a comparison already made.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["lies_inside_checkout", "session_base", "source_tree_of"]


def _resolved(raw: str) -> Path | None:
    """
    ``raw`` as an absolute path with its symlinks followed, or None when the
    filesystem cannot say where it is.

    Relative paths resolve against this process's directory, which is also what the
    SDK does with ``ClaudeAgentOptions.cwd`` -- so "." here names the same place it
    names for the agent. A component that does not exist is appended rather than
    refused, which is what lets a directory the operator has not created yet still be
    placed.

    None means a symlink loop, or a ``~user`` with no home: the two ways a typed path
    leaves this with no location at all, as distinct from a location nothing is at
    yet. Each caller decides what an unlocatable path is worth, and they do not agree.
    """
    try:
        return Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def session_base(cwd: str) -> str:
    """
    The base a session launched in ``cwd`` measures its writes against: that
    directory, resolved, and nothing inferred from it.

    **The directory the operator specified is the base.** Pointing a session at the
    root of the work is the operator's job and the convention they already work to,
    and they have already told us where it is. Deriving some enclosing directory
    instead is a guess that can silently disagree with the value they gave -- and the
    disagreement is invisible, because both answers are plausible directories and
    neither the units nor the instruction naming them are on screen. Not every project
    is a checkout of anything, so there is no second base to prefer even in principle.

    Returns a resolved absolute path, so the caller can compare it against an absolute
    write path by prefix without resolving anything itself.

    Total: an unresolvable path comes back as the string it arrived as, which is the
    only answer left and is better than raising on a launch.
    """
    path = _resolved(cwd)
    if path is None:
        # Unresolvable: the string is still the best answer available, and returning
        # it keeps this total.
        return cwd
    return str(path)


# The file a source checkout of a Python project has at its root and an installed one
# does not: ``pyproject.toml`` is the build definition, so it is in every checkout by
# construction and ``site-packages`` has no reason to hold one.
_SOURCE_MARKER = "pyproject.toml"


def source_tree_of(package: str) -> str:
    """
    The tree the package directory ``package`` is running out of -- its checkout root
    when it is running from source, the package directory itself when it is installed.

    This is how pptmstr locates its OWN source so that ``lies_inside_checkout`` can
    refuse to hand it to an agent that writes unattended, and the two failure
    directions do not cost the same. Too wide costs a refusal the operator can see and
    work around. Too narrow silently leaves everything *beside* the package directory
    -- ``planning/``, ``scripts/``, ``CLAUDE.md``, the tests that pin the gate --
    writable by an agent nobody is watching. So the parent is taken whenever it looks
    like a checkout at all, and the marker is the cheapest fact that distinguishes the
    two layouts.

    **An installed package has no checkout and the package directory is the honest
    answer for it.** Its parent is ``site-packages``, a directory of other projects
    rather than a tree of this one; what is being protected is the ``approval.py``
    inside the package, and that is covered either way.

    ``package`` is a directory, not a file, and the answer is resolved absolute so it
    can be passed straight to ``lies_inside_checkout`` as the checkout side.
    """
    here = _resolved(package)
    if here is None:
        return package
    parent = here.parent
    return str(parent if (parent / _SOURCE_MARKER).is_file() else here)


def lies_inside_checkout(candidate: str, checkout: str) -> bool:
    """
    Whether ``candidate`` has to be treated as lying inside the ``checkout`` tree.

    The sandbox's writable region *is* the session's cwd, and its protected paths
    cover ``.claude/**``, ``.mcp.json``, ``.git/hooks`` and ``.git/config`` -- not
    ordinary project source. ``LaunchSpec.cwd`` defaults to ``"."``, so the default
    launch would make pptmstr's own source the region an unattended agent may write,
    and ``approval.py`` for the next launch is inside it. This is the question that
    notices. What is done about a yes -- refuse the mode, or clone first -- is the
    caller's and is not decided here.

    **Both sides are resolved through the filesystem before they are compared.** The
    hazard is a spelling that does not look like the checkout: ``"."``, any relative
    path, or a symlink that reaches in. Comparing the typed forms answers no to all
    three, and a no is the answer that costs containment.

    **The checkout root itself counts as inside it**, because that is the default
    launch and the case that matters most.

    **Components, not characters.** ``/src/pptmstr-scratch`` has ``/src/pptmstr`` as a
    string prefix and is a different tree; ``is_relative_to`` compares path components,
    so a sibling named after the checkout is not swept up with it.

    **A path that cannot be located at all answers yes.** A symlink loop or a ``~user``
    with no home leaves this unable to say where the directory is, and the two answers
    are not symmetric: a yes costs a refusal the operator can see and correct, a no
    hands a directory nobody could locate to an agent that writes without being asked.
    The same holds when ``checkout`` is the unresolvable side. This is the opposite
    choice from ``session_base``, which falls back to the string it was given -- there
    the fallback picks units for a measurement, here it decides whether source is
    exposed.

    Total. No clock, no subprocess, no state; the only IO is the resolution itself.
    """
    here = _resolved(candidate)
    root = _resolved(checkout)
    if here is None or root is None:
        return True
    return here.is_relative_to(root)
