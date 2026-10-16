"""
Find in the library's views a pane holds: the matches of a needle, and the marks a view is handed.

A pane keeps the needle, which match is current and which views it marked; this
module answers what the views' texts hold and how a view's matches are marked.
``matches`` searches every view of each key, in the order given and the order
the views were drawn, ignoring case, and answers ``(key, view, match)``, a
match being the library's ``Match``: ``block``, ``start`` and ``end`` as
``str`` indices into the view's text of that block, which is what
``view.scroll_to`` and a mark take. ``marks`` is the library's own rule for
them, the current match's mark last and stronger.

**What is searched is each view's ``texts``, as the view holds them**, built
into the library's ``FindIndex`` once per call: a view's description is its
own, and an index over any other string would give offsets that mark the wrong
characters. A block whose role is CODE or TABLE is searched as code, where a
line break is not a space; anything else as prose, where it is. A table is
therefore one stretch of text and a needle can match across two cells. Text a
pane draws with ImGui has no view and is not searched.

Reached past ``imscribe.__all__``, each a name its own module lists and the
facade does not re-export:

- ``marks_for`` from ``imscribe.typeset_find``: the marks of a list of matches
  with one current. The facade's ``Finder`` makes them only for a needle over
  one document of its own; a pane's current match runs across all its views.
  The library's module docstring names it as reached from there.
- ``KIND_CODE`` and ``KIND_PARAGRAPH`` from ``imscribe.description``: the
  block kinds ``FindIndex.of`` reads to tell code from prose. The facade's
  ``BlockKind`` is the theme's and not a description's kind.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Sequence
from typing import Any

from .views import ViewCache

CODE_ROLES = ("CODE", "TABLE")
"""
The block roles searched as code: a line break in them is not a space.
"""


def matches(
    views: ViewCache, keys: Iterable[Hashable], needle: str
) -> list[tuple[Hashable, Any, Any]]:
    """
    Every match of ``needle`` in the views of ``keys``, in that order, ignoring case.

    An empty needle matches nothing, and a key with no views has none.
    """
    if not needle:
        return []
    from imscribe import FindIndex

    found: list[tuple[Hashable, Any, Any]] = []
    for key in keys:
        for view in views.views(key):
            index = FindIndex.of(_described(view))
            found += [(key, view, match) for match in index.find(needle, ignore_case=True)]
    return found


def marks(found: Sequence[Any], current: int | None) -> tuple[Any, ...]:
    """
    The marks for one view's matches, ``found[current]`` stronger, as ``set_marks`` takes them.
    """
    from imscribe.typeset_find import marks_for

    return marks_for(found, current)


def _described(view: Any) -> tuple[Any, ...]:
    """
    A view's blocks as ``FindIndex.of`` reads them: each block's text with its kind.
    """
    from imscribe import BlockDescription
    from imscribe.description import KIND_CODE, KIND_PARAGRAPH

    return tuple(
        BlockDescription(
            kind=KIND_CODE if view.block_role(number) in CODE_ROLES else KIND_PARAGRAPH,
            text=text,
        )
        for number, text in enumerate(view.texts)
    )
