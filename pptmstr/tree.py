"""
The working tree, as the filesystem has it.

Everything here touches disk, so nothing here may be called from the reducer. The
split is the same one ``sessions.py`` makes and for the same reason: the blocking
lives behind one name, and what a caller does with the answer stays pure.

**Why the repository root is a value and not a lookup.** ``relative_write`` puts a
written path into the units a declaration is written in, and ``bus.declare_task``
tells every lead those units are repository-relative. Deriving the root at
comparison time would need the filesystem inside ``store._apply``, which does no IO;
so it is resolved once, at launch, and carried on the record. That also freezes it
for the session's lifetime, which is the property that matters: a ``.git`` created
later would otherwise re-base writes recorded before it existed, and a session's
units would change underneath a comparison already made.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["repo_root"]


def repo_root(cwd: str) -> str:
    """
    The repository directory enclosing ``cwd``, or ``cwd`` itself when none does.

    **Falls back to the directory rather than to None**, because the fallback is
    what makes adopting this a no-op. A session launched at a repository root gets
    that root either way, and a session launched outside a repository has exactly one
    base anybody could mean -- there is no second unit for it to disagree with, and
    making that whole class of session unmeasurable would buy nothing.

    ``.git`` is tested with ``exists()`` and not ``is_dir()``: in a worktree or a
    submodule it is a *file* holding a gitdir pointer, and reading those as unenclosed
    would put every worktree in its own units instead of its repository's.

    Returns a resolved absolute path, so the caller can compare it against an absolute
    write path by prefix without resolving anything itself.
    """
    try:
        # Relative paths resolve against this process's directory, which is also what
        # the SDK does with ``ClaudeAgentOptions.cwd`` -- so "." here names the same
        # place it names for the agent.
        path = Path(cwd).expanduser().resolve()
    except (OSError, RuntimeError):
        # Unresolvable: a symlink loop, or a parent that vanished between the
        # operator typing it and this running. The string is still the best answer
        # available, and returning it keeps this total.
        return cwd

    for candidate in (path, *path.parents):
        try:
            if (candidate / ".git").exists():
                return str(candidate)
        except OSError:
            # A directory we cannot stat is not a repository root as far as we can
            # tell. Keep walking rather than abandoning the whole derivation.
            continue
    return str(path)
