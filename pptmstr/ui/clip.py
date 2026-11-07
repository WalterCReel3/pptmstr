"""
The head-anchored character bound every pane uses for a body it shows in part.

Pure: one function of a string and a limit. It lives apart from the panes because
the board, the brief, the detail pane and the two views built on the board each
cut a body the same way, and the cut is one decision rather than five that agree.
The other bounds in ``pptmstr/ui`` are not this one: they cut at a line, keep the
tail, or count rows, and each says why in its own module.
"""

from __future__ import annotations


def clip(text: str, limit: int) -> tuple[str, int]:
    """
    Bound a string, reporting how much was dropped.

    Returns the count rather than appending an ellipsis, so the caller can render
    the omission as its own coloured line. A silent cap in a pane whose premise is
    that nothing is lost is worse than no pane.
    """
    if len(text) <= limit:
        return text, 0
    return text[:limit], len(text) - limit
