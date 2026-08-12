"""
Span layout: wrapping an inline token stream into rows.

No ImGui, no live frame -- see the module docstring in pptmstr/ui/span_layout.py
for why the wrap/measure functions are injected rather than called directly. The
fake below is a fixed-width stand-in with a documented (not ImGui-verified)
space-handling convention: it exists to make the *layout decisions* -- does this
wrap at all, does bold survive across a wrap, does a break token force a new row
-- checkable without a live frame. The real ImGui wrap function is exercised
separately, in a live frame, by scripts/verify_rich_render.py.
"""

from __future__ import annotations

from collections.abc import Callable

from pptmstr.ui.inline import InlineToken
from pptmstr.ui.span_layout import Row, Run, WrapAt, layout_inline


def fixed_width(char_w: float = 1.0) -> tuple[WrapAt, Callable[[str], float]]:
    """
    A monospace fake: one unit of width per character.

    Convention, matching ImGui's: break at the last space at or before the
    character budget and return the index *of* that space, so it stays at the head
    of the remainder rather than being consumed. No good space in budget:
    hard-break at the budget.

    This used to consume the space, which was the one place the fake disagreed with
    the real ``calc_word_wrap_position`` -- and it made the leading-space-on-every-
    continuation-row defect structurally invisible to these tests. Verified against
    the live function: breaking "...are being accepted today" at index 27 yields
    the tail " accepted today", space included.
    """

    def measure_width(text: str) -> float:
        return len(text) * char_w

    def wrap_at(text: str, width: float) -> int:
        if not text:
            return 0
        budget = max(1, int(width // char_w)) if width > 0 else 1
        if budget >= len(text):
            return len(text)
        space_at = text.rfind(" ", 0, budget + 1)
        if space_at <= 0:
            return budget
        return space_at

    return wrap_at, measure_width


def text_tokens(*pairs: tuple[str, str]) -> list[InlineToken]:
    return [InlineToken(t, c) for t, c in pairs]


def row_texts(rows: tuple[Row, ...]) -> list[str]:
    return [r.text for r in rows]


# -- basic wrapping ---------------------------------------------------------------


def test_short_text_is_one_row() -> None:
    wrap_at, measure = fixed_width()
    rows = layout_inline(text_tokens(("text", "hello world")), 80.0, wrap_at, measure)
    assert row_texts(rows) == ["hello world"]


def test_long_text_wraps_at_word_boundaries() -> None:
    wrap_at, measure = fixed_width()
    rows = layout_inline(text_tokens(("text", "one two three four")), 8.0, wrap_at, measure)
    # Exact split depends on the fake's budget; assert the invariants that matter:
    # every row fits, and the words survive in order. Concatenation no longer
    # round-trips *characters* -- the space a row was broken at is dropped rather
    # than indenting the next row -- so the comparison is by word.
    assert all(len(r.text) <= 8 or " " not in r.text for r in rows)
    assert " ".join(row_texts(rows)).split() == "one two three four".split()


def test_empty_token_stream_still_returns_one_row() -> None:
    wrap_at, measure = fixed_width()
    rows = layout_inline([], 80.0, wrap_at, measure)
    assert rows == (Row(()),)


def test_overlong_word_hard_breaks() -> None:
    """A single word wider than the whole line must still make progress, never
    stall the layout waiting for a space that will never come."""
    wrap_at, measure = fixed_width()
    rows = layout_inline(text_tokens(("text", "supercalifragilistic")), 5.0, wrap_at, measure)
    assert len(rows) > 1
    assert "".join(row_texts(rows)) == "supercalifragilistic"
    assert all(len(r.text) <= 5 for r in rows)


# -- break tokens -------------------------------------------------------------------


def test_softbreak_becomes_a_space_and_reflows() -> None:
    """
    CommonMark renders a softbreak as a space, and here that is load-bearing rather
    than pedantic: a model hard-wraps near 80 columns, so honouring those newlines
    would print the model's wrapping *and* the pane's on top of it.
    """
    wrap_at, measure = fixed_width()
    tokens = text_tokens(("text", "one"), ("softbreak", ""), ("text", "two"))
    rows = layout_inline(tokens, 80.0, wrap_at, measure)
    assert row_texts(rows) == ["one two"]


def test_softbreak_at_the_start_of_a_row_contributes_nothing() -> None:
    """A space that would open a row is an indent, not a word gap."""
    wrap_at, measure = fixed_width()
    tokens = text_tokens(("softbreak", ""), ("text", "one"))
    rows = layout_inline(tokens, 80.0, wrap_at, measure)
    assert row_texts(rows) == ["one"]


def test_a_wrapped_row_does_not_start_with_the_space_it_broke_at() -> None:
    """
    The defect this fake used to hide. ImGui's wrap position points *at* the space,
    so an untrimmed remainder indents every continuation row by one character --
    visible on any paragraph long enough to wrap.
    """
    wrap_at, measure = fixed_width()
    rows = layout_inline(text_tokens(("text", "alpha beta gamma delta")), 11.0, wrap_at, measure)
    assert len(rows) > 1
    assert all(not r.text.startswith(" ") for r in rows)


def test_hardbreak_forces_a_new_row() -> None:
    wrap_at, measure = fixed_width()
    tokens = text_tokens(("text", "one"), ("hardbreak", ""), ("text", "two"))
    rows = layout_inline(tokens, 80.0, wrap_at, measure)
    assert row_texts(rows) == ["one", "two"]


# -- style tracking -----------------------------------------------------------------


def test_bold_span_is_flagged_and_survives_a_wrap() -> None:
    wrap_at, measure = fixed_width()
    tokens = [
        InlineToken("text", "a "),
        InlineToken("strong_open"),
        InlineToken("text", "bold text here"),
        InlineToken("strong_close"),
        InlineToken("text", " b"),
    ]
    rows = layout_inline(tokens, 6.0, wrap_at, measure)
    bold_runs = [r for row in rows for r in row.runs if r.bold]
    assert bold_runs  # at least one bold run survived the wrap
    # By word, not by character: a space consumed at a wrap point is gone from the
    # laid-out rows on purpose. Nothing copies from rows -- copy reads Block.lines.
    assert " ".join(r.text for row in rows for r in row.runs).split() == [
        "a",
        "bold",
        "text",
        "here",
        "b",
    ]
    assert all(not r.bold for row in rows for r in row.runs if r.text in ("a ", " b"))


def test_em_sets_italic_not_a_font_flag() -> None:
    """Planning doc: emphasis is a colour shift (Run.italic selects a palette
    role), never a slant -- there is no font-selection field for it here."""
    wrap_at, measure = fixed_width()
    tokens = [InlineToken("em_open"), InlineToken("text", "em"), InlineToken("em_close")]
    rows = layout_inline(tokens, 80.0, wrap_at, measure)
    (run,) = rows[0].runs
    assert run == Run("em", italic=True)


def test_code_inline_is_flagged() -> None:
    wrap_at, measure = fixed_width()
    rows = layout_inline(text_tokens(("code_inline", "x = 1")), 80.0, wrap_at, measure)
    (run,) = rows[0].runs
    assert run == Run("x = 1", code=True)


def test_link_open_close_flags_the_enclosed_text() -> None:
    wrap_at, measure = fixed_width()
    tokens = [
        InlineToken("text", "see "),
        InlineToken("link_open", "", (("href", "http://x"),)),
        InlineToken("text", "docs"),
        InlineToken("link_close"),
        InlineToken("text", " here"),
    ]
    rows = layout_inline(tokens, 80.0, wrap_at, measure)
    runs = rows[0].runs
    assert [r.link for r in runs] == [False, True, False]
    assert [r.text for r in runs] == ["see ", "docs", " here"]


def test_style_toggles_do_not_emit_empty_runs() -> None:
    wrap_at, measure = fixed_width()
    tokens = [InlineToken("strong_open"), InlineToken("strong_close")]
    rows = layout_inline(tokens, 80.0, wrap_at, measure)
    assert rows == (Row(()),)


def test_a_word_is_not_split_just_because_the_row_ran_out() -> None:
    """
    Latent until paragraphs began reflowing: with only a sliver of row left, a wrap
    function asked to break within it will always oblige, and the split reads as an
    accidental hyphen ("at colum / n 68"). The word should take a fresh row.
    """
    wrap_at, measure = fixed_width()
    tokens = text_tokens(("text", "at "), ("text", "column"))
    rows = layout_inline(tokens, 8.0, wrap_at, measure)
    # rstrip: a token that fits whole keeps its own trailing space, which draws as
    # nothing. What matters is that "column" is intact and on its own row.
    assert [t.rstrip() for t in row_texts(rows)] == ["at", "column"]


def test_an_overlong_word_still_hard_breaks_on_an_empty_row() -> None:
    """The guard above must not turn 'give the word its own row' into an infinite
    retry when a whole row is still not enough."""
    wrap_at, measure = fixed_width()
    tokens = text_tokens(("text", "hi "), ("text", "supercalifragilistic"))
    rows = layout_inline(tokens, 6.0, wrap_at, measure)
    assert row_texts(rows)[0].rstrip() == "hi"
    assert "".join(row_texts(rows)[1:]) == "supercalifragilistic"
    assert all(len(r.text) <= 6 for r in rows)
