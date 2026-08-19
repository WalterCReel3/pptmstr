"""
Word-wrapping a block's inline token stream into drawable rows.

Step 5 of planning/archive/2026-08-10-transcript-markdown.md (the layout half; drawing
lives in ``rich_pane.py``). No ImGui here -- the one thing this module needs from
ImGui, "how much of this text fits in this many pixels", is taken as an injected
callable rather than called directly, which is what makes the wrapping *decisions*
unit-testable without a live frame. Everything ImGui-specific (the real
``ImFont.calc_word_wrap_position_python``, a real ``calc_text_size``) is supplied
by the caller in ``rich_pane.py``; tests supply a small fixed-width fake instead.

The row/run split mirrors why this is two passes and not one:

- **Layout** (here) decides where each row breaks. It never computes an absolute
  pixel position -- only "does this piece fit in what's left of the row".
- **Draw** (``rich_pane.py``) walks each row's runs left to right, drawing and
  advancing a cursor. It reuses the exact same ``measure_width`` this module
  calls, so layout and draw can never disagree about how wide a run is.

Emphasis is a colour shift, not a slant (planning doc's decision, "Emphasis is a
colour shift, not a slant") -- ``Run.italic`` selects a palette role, not a font.
Only ``Run.bold`` selects a different face.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .inline import InlineToken

# (text, available_width) -> break index. text[:index] fits; text[index:] is what
# is left for the next row. Must always make forward progress (index >= 1) for any
# non-empty text, even when width <= 0 -- ImGui's own helper hard-breaks an
# overlong word rather than ever returning 0, and any stand-in must too, or the
# loop below spins forever on a pathologically narrow width.
WrapAt = Callable[[str, float], int]
# text -> pixel width at the font/size live when this is called.
MeasureWidth = Callable[[str], float]

# Inline token types that toggle a style rather than emit text.
_STYLE_TOGGLES = {
    "strong_open": ("bold", True),
    "strong_close": ("bold", False),
    "em_open": ("italic", True),
    "em_close": ("italic", False),
    "link_open": ("link", True),
    "link_close": ("link", False),
}
"""
A softbreak is deliberately absent here.

CommonMark renders one as a space, and for this renderer that is not pedantry: a
model hard-wraps its prose near 80 columns, so treating those newlines as row
breaks means a pane of any other width shows the model's wrapping *and* its own on
top of it. The result reads as ragged double-wrapping rather than as paragraphs.
A hardbreak (two trailing spaces, or a backslash) is an explicit request and does
still break.
"""
_BREAKS = {"hardbreak"}


@dataclass(frozen=True, slots=True)
class Run:
    """One styled, undivided piece of text within a laid-out row."""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: bool = False


@dataclass(frozen=True, slots=True)
class Row:
    runs: tuple[Run, ...]

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


def layout_inline(
    tokens: Sequence[InlineToken],
    width: float,
    wrap_at: WrapAt,
    measure_width: MeasureWidth,
) -> tuple[Row, ...]:
    """
    Lay a token stream out into rows no wider than ``width``.

    Always returns at least one row -- including for an empty token stream (an
    empty heading, "###" with no title) -- so a caller can reserve a line of
    height for it unconditionally rather than special-casing zero rows.
    """
    rows: list[Row] = []
    current: list[Run] = []
    used = 0.0
    bold = italic = code = link = False

    def flush() -> None:
        nonlocal current, used
        rows.append(Row(tuple(current)))
        current = []
        used = 0.0

    for tok in tokens:
        if tok.type in _BREAKS:
            flush()
            continue
        toggle = _STYLE_TOGGLES.get(tok.type)
        if toggle is not None:
            attr, value = toggle
            if attr == "bold":
                bold = value
            elif attr == "italic":
                italic = value
            else:
                link = value
            continue

        if tok.type == "softbreak":
            # A space that would start a row is nothing, so a softbreak at the top
            # of a paragraph -- or immediately after a wrap -- contributes no run
            # rather than an indent.
            if not current:
                continue
            remaining = " "
        else:
            # "text", "code_inline", and anything this renderer does not
            # special-case (html_inline, ...) all fall through as literal text --
            # markdown-it has already resolved entities into token content by here.
            remaining = tok.content
        is_code = code or tok.type == "code_inline"
        while remaining:
            avail = width - used
            if avail <= 0 and current:
                flush()
                avail = width
            break_idx = wrap_at(remaining, avail)
            if break_idx <= 0:
                break_idx = 1  # guarantee progress against a misbehaving wrap_at
            if current and break_idx < len(remaining) and not remaining[break_idx].isspace():
                # The break landed inside a word, which happens when only a sliver
                # of the current row was left to offer. Give the word a fresh row
                # instead of splitting it: asking a wrap function for a break inside
                # 5px will always produce one, and it reads as an accidental hyphen.
                #
                # Guarded on ``current`` so an overlong word still makes progress --
                # on an already-empty row there is no more width to be had, and the
                # hard break is the correct answer rather than an infinite retry.
                flush()
                continue
            piece, remaining = remaining[:break_idx], remaining[break_idx:]
            if piece:
                current.append(Run(piece, bold, italic, is_code, link))
                used += measure_width(piece)
            if remaining:
                flush()
                # ImGui's word wrap breaks *before* the space it chose, leaving it
                # at the head of the remainder (verified: a break at index 27 of
                # "...are being accepted" yields the tail " accepted"). Carried
                # through, every continuation row would start one space indented.
                # Display only -- copy reads Block.lines, which stays verbatim.
                remaining = remaining.lstrip(" ")

    if current or not rows:
        flush()
    return tuple(rows)
