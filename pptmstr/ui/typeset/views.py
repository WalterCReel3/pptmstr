"""
What a pane holds between frames, and the calls that set text at the cursor.

``ViewCache`` is the holder: one per pane, keyed by what is shown, pruned by the
pane with the keys still live. **The panes hold the views**; there is no table
in this package keyed by approval or node.

**No call here draws yet.** ``markdown``, ``document``, ``plain``, ``stream``,
``label``, ``font`` and the two line heights are the names the panes' typeset
branches call, with the arguments they pass, and each raises ``NotAdopted``.
None is reached: a pane asks ``roles.enabled(pane)`` first, and that is false for
every pane ``roles.ADOPTED`` does not name. A pane that calls one without
asking is told so by the exception.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Hashable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from ...theme import Color
from .parts import Part
from .roles import Role, Weight


class NotAdopted(RuntimeError):
    """
    A drawing call of the seam that is not built on the library yet was made.

    A pane reaches one only by not asking ``enabled(pane)`` first.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"typeset.{name} draws nothing yet; ask typeset.enabled(pane) before drawing"
        )


@dataclass
class _Entry:
    """
    One held view and the epoch it was made under.
    """

    kind: str
    epoch: int
    view: Any
    # The height the view claimed when it was last drawn.
    height: float | None = None


class ViewCache:
    """
    The views one pane holds between frames, by a key of the pane's choosing.

    Imperative shell, like ``review.ReviewState``: presentation state the store does
    not own. One per pane. A key is anything hashable that names *what is shown* --
    an approval id, ``(node, "deliverable")`` -- and the pane prunes with the set of
    keys still live, the way ``ReviewState.prune`` does.
    """

    def __init__(self) -> None:
        self._entries: dict[Hashable, _Entry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def keys(self) -> tuple[Hashable, ...]:
        return tuple(self._entries)

    def prune(self, live: Collection[Hashable]) -> None:
        """
        Drop every view whose key is not in ``live``.
        """
        for stale in [key for key in self._entries if key not in live]:
            del self._entries[stale]

    def drop(self, key: Hashable) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def view(self, key: Hashable) -> Any:
        """
        The library's own view object for a key, or ``None``.
        """
        entry = self._entries.get(key)
        return None if entry is None else entry.view

    def height(self, key: Hashable) -> float | None:
        """
        The height the view claimed when it was last drawn, or ``None``.
        """
        entry = self._entries.get(key)
        return None if entry is None else entry.height

    def unbuilt(self, key: Hashable) -> int | None:
        """
        How many source lines of the view are waiting to be typeset, or ``None``
        for no view. A view that is held is whole.
        """
        return None if key not in self._entries else 0

    def part_at(self, key: Hashable, block: int) -> int | None:
        """
        Which part of a ``document`` a block of its view came from, or ``None``.
        """
        return None


class Stream(Protocol):
    """
    Markdown that is still arriving, as a pane holds it: what ``stream()`` answers.
    """

    @property
    def text(self) -> str: ...

    @property
    def closed(self) -> bool: ...

    @property
    def view(self) -> Any: ...

    @property
    def following(self) -> bool: ...

    @property
    def unbuilt(self) -> int: ...

    def follow_tail(self) -> None: ...

    def append(self, text: str) -> None: ...

    def close(self) -> None: ...

    def set_text(self, text: str, *, closed: bool = False) -> None: ...

    def draw(self, *, width: float | None = None, ink: Color | None = None) -> float: ...


def pending() -> bool:
    """
    Whether a view on screen is short of its text, so the application's loop
    should not idle. No view is: nothing is built over more than one frame.
    """
    return False


def markdown(
    cache: ViewCache,
    key: Hashable,
    source: str,
    *,
    width: float | None = None,
    height_cap: float | None = None,
    ink: Color | None = None,
    on_link: Callable[[str], None] | None = None,
    demote: int = 0,
    build_hidden: bool = False,
    free_allowance: bool = True,
) -> float:
    """
    A markdown document at the cursor: selectable, culled, as wide as the window.
    Returns the height it claimed.
    """
    raise NotAdopted("markdown")


def document(
    cache: ViewCache,
    key: Hashable,
    parts: Sequence[Part],
    *,
    width: float | None = None,
    height_cap: float | None = None,
    on_link: Callable[[str], None] | None = None,
    build_hidden: bool = False,
    free_allowance: bool = True,
    notice_above: bool = False,
) -> float:
    """
    Several parts as **one** document in one view, with one selection across them.
    """
    raise NotAdopted("document")


def plain(
    cache: ViewCache,
    key: Hashable,
    text: str,
    *,
    mono: bool = True,
    wrap: bool = False,
    exact: bool = False,
    colour: Color | None = None,
    width: float | None = None,
    height_cap: float | None = None,
    role: Role = Role.LITERAL,
) -> float:
    """
    Text that is not markdown, set exactly as written: an asterisk is an asterisk.
    """
    raise NotAdopted("plain")


def stream(
    cache: ViewCache,
    key: Hashable,
    *,
    follow: bool = False,
    every_ms: float = 0.0,
    free_allowance: bool = True,
) -> Stream:
    """
    The stream held under a key, made on first asking.
    """
    raise NotAdopted("stream")


def line_height(*, mono: bool = True, role: Role | None = None) -> float:
    """
    The pitch of one line of a role's text, in pixels: to size a cap.
    """
    raise NotAdopted("line_height")


def diff_row_height() -> float:
    """
    The height of one row of a ``diff()``, in pixels: to size a cap.
    """
    raise NotAdopted("diff_row_height")


@contextmanager
def font(weight: Weight, size: float | None = None) -> Iterator[None]:
    """
    Push a typeset face for ordinary ImGui widgets: a table cell, a header line.
    """
    raise NotAdopted("font")
    yield


def label(
    text: str,
    *,
    weight: Weight = Weight.BOLD,
    colour: Color | None = None,
    size: float | None = None,
) -> None:
    """
    One unwrapped line in a typeset face: a row title, a key in a key-value block.
    """
    raise NotAdopted("label")
