"""
The rich renderer's pure decisions: what a fence actually shows, and what the
history window keeps.

Drawing itself needs a live frame and is covered by scripts/verify_rich_render.py.
What is checkable here is everything that decides *what* gets drawn -- which is
also where a silent regression would hide, since a fence that renders its own
delimiters or a window that mis-counts a block still draws perfectly happily.
"""

from __future__ import annotations

from pptmstr.transcript import SegmentKind
from pptmstr.ui.blocks import Block, BlockKind
from pptmstr.ui.rich_pane import block_copy_text, fence_body, windowed


def fence(*lines: str, closed: bool = True, lang: str = "") -> Block:
    return Block(
        kind=BlockKind.FENCE,
        segment_kind=SegmentKind.OUTPUT,
        lines=lines,
        closed=closed,
        meta=(("lang", lang),) if lang else (),
    )


def paragraph(*lines: str) -> Block:
    return Block(
        kind=BlockKind.PARAGRAPH,
        segment_kind=SegmentKind.OUTPUT,
        lines=lines,
        closed=True,
    )


# -- fence_body -----------------------------------------------------------------


def test_fence_body_drops_both_delimiters() -> None:
    block = fence("```python", "x = 1", "y = 2", "```", lang="python")
    assert fence_body(block) == ("x = 1", "y = 2")


def test_fence_body_keeps_the_last_line_when_the_fence_never_closed() -> None:
    """An unclosed fence has an opening delimiter and no closing one, so trimming a
    tail would eat a line of the code itself."""
    block = fence("```python", "x = 1", "y = 2", closed=False, lang="python")
    assert fence_body(block) == ("x = 1", "y = 2")


def test_fence_body_of_an_opening_delimiter_alone_is_empty() -> None:
    assert fence_body(fence("```", closed=False)) == ()


def test_fence_body_of_an_empty_closed_fence_is_empty() -> None:
    assert fence_body(fence("```", "```")) == ()


def test_block_lines_stay_verbatim_for_copy() -> None:
    """``fence_body`` is a drawing decision only -- copy-as-markdown must still get
    back exactly what the model emitted, delimiters included."""
    block = fence("```python", "x = 1", "```", lang="python")
    assert block_copy_text([block]) == "```python\nx = 1\n```"


# -- windowed -------------------------------------------------------------------


def test_windowed_counts_a_fence_by_what_is_drawn_not_by_its_lines() -> None:
    """
    The delimiters are not drawn and a language label is, so a fence's cost is its
    body plus one -- not len(lines). A mismatch here silently over- or under-fills
    the window.
    """
    big = fence("```py", *[f"line {i}" for i in range(40)], "```", lang="py")
    # body 40 + 1 label = 41; lines would have been 42.
    kept = windowed([paragraph("a")] * 1000 + [big])
    assert big in kept


def test_windowed_always_keeps_the_newest_block() -> None:
    huge = paragraph(*[f"line {i}" for i in range(5000)])
    assert windowed([huge]) == [huge]


def test_windowed_drops_the_oldest_first() -> None:
    blocks = [paragraph(f"p{i}") for i in range(2000)]
    kept = windowed(blocks)
    assert kept[-1] is blocks[-1]
    assert len(kept) < len(blocks)
