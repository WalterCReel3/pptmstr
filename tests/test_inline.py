"""
Inline parsing: markdown-it-py, patched to drop underscore emphasis.

No ImGui, no live frame -- see the module docstring in pptmstr/ui/inline.py.
"""

from __future__ import annotations

from pptmstr.ui.inline import InlineToken, parse_inline


def types(text: str) -> list[str]:
    return [t.type for t in parse_inline(text)]


def test_empty_text_parses_to_nothing() -> None:
    assert parse_inline("") == ()


def test_plain_text_is_one_token() -> None:
    tokens = parse_inline("hello world")
    assert tokens == (InlineToken("text", "hello world"),)


def test_asterisk_emphasis_still_works() -> None:
    assert types("a **bold** b *em* c") == [
        "text",
        "strong_open",
        "text",
        "strong_close",
        "text",
        "em_open",
        "text",
        "em_close",
        "text",
    ]


def test_inline_code_span() -> None:
    tokens = parse_inline("call `foo()` now")
    assert InlineToken("code_inline", "foo()") in tokens


def test_link_carries_href_in_attrs() -> None:
    tokens = parse_inline("see [docs](https://example.com)")
    opens = [t for t in tokens if t.type == "link_open"]
    assert opens == [InlineToken("link_open", "", (("href", "https://example.com"),))]


# -- the underscore-emphasis override --------------------------------------------
#
# planning/2026-08-10-transcript-markdown.md, "Underscore emphasis is disabled":
# __init__ and __all__ are word-boundary delimiter runs by the letter of
# CommonMark and bold under an unpatched parser -- verified below, since a
# regression here is exactly the kind of thing that looks like a passing test
# suite until someone reads a transcript full of dunder methods.


def test_dunder_init_does_not_italicise_or_bold() -> None:
    assert parse_inline("call __init__ here") == (InlineToken("text", "call __init__ here"),)


def test_dunder_all_does_not_bold() -> None:
    assert parse_inline("see __all__ list") == (InlineToken("text", "see __all__ list"),)


def test_underscore_italic_marker_is_also_suppressed() -> None:
    """
    The stated cost of the override: single-underscore italic no longer works
    either. Pinned so the trade is visible if someone "fixes" it later.
    """
    assert parse_inline("_italic_ text") == (InlineToken("text", "_italic_ text"),)


def test_intraword_underscore_was_already_fine_without_the_override() -> None:
    """foo_bar_baz is protected by CommonMark's intraword rule regardless -- the
    override only needed to additionally cover the word-boundary case."""
    assert parse_inline("path/to/file_name.py::test_thing") == (
        InlineToken("text", "path/to/file_name.py::test_thing"),
    )


def test_asterisk_multiplication_does_not_italicise() -> None:
    """2 * 3 * 4 -- CommonMark's flanking rules already get this right; nothing
    about the underscore override should touch asterisk handling here."""
    assert parse_inline("2 * 3 * 4") == (InlineToken("text", "2 * 3 * 4"),)
