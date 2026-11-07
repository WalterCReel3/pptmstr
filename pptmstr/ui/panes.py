"""
The application's docks: what each is called and which module draws it.

The one table every other list of panes is read from. ``app`` builds its windows
and both layouts under these titles, ``typeset.PANE_LABELS`` names its switches by
them, and the tests read the module column to find the panes' sources.

A title is not only a label. It is the window's ImGui id, and the saved layout
ini keys every window's dock, size and visibility on it, so changing one moves an
operator's arrangement to a window that no longer exists. A title changes here,
deliberately, or not at all.

Data only: importable by the typeset policy, which imports neither the text
library nor the toolkit.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Dock:
    """
    One docked window.

    ``module`` is the ``pptmstr.ui`` module whose ``draw`` the window calls, by
    its file's name; None for a window ``app`` draws itself. Two windows may share
    one: CONTEXT and SESSION are both the transcript pane's.
    """

    title: str
    module: str | None


FLEET = Dock("FLEET", "rail")
NEEDS_YOU = Dock("NEEDS YOU", "inbox")
CONTEXT = Dock("CONTEXT", "transcript_pane")
DETAIL = Dock("DETAIL", "detail")
BOARD = Dock("BOARD", "board_pane")
DIGEST = Dock("DIGEST", "digest_pane")
BRIEF = Dock("BRIEF", "brief_pane")
SESSION = Dock("SESSION", "transcript_pane")
HEALTH = Dock("HEALTH", "health")
LOG = Dock("LOG", None)

DOCKS: tuple[Dock, ...] = (
    FLEET,
    NEEDS_YOU,
    CONTEXT,
    DETAIL,
    BOARD,
    DIGEST,
    BRIEF,
    SESSION,
    HEALTH,
    LOG,
)
"""
Every dock, in the order ``app._panels`` builds them.
"""


def modules() -> tuple[str, ...]:
    """
    The modules that draw a dock, each once, in the order of their first dock.
    """
    return tuple(dict.fromkeys(dock.module for dock in DOCKS if dock.module is not None))
