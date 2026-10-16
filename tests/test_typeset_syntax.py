"""
Fenced code coloured by token (``pptmstr/ui/typeset/syntax.py``).

Two halves. The spans themselves, which need no library: their offsets, what a
block that is not coloured is given, and the cache. Then the same through the
seam on the library as it is: what each view was handed (``_colours``, the
view's own record of ``set_colours``), against the text the view holds, and
the inks a frame put on the draw list.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Keyword, Name, Number, Operator, String

from pptmstr import theme
from pptmstr.theme import THEMES, Palette, contrast_ratio
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Part, syntax, themes
from tests.test_typeset_views import _inks, frame

ACCENT = 0xFF0000AA
INKS = themes.SyntaxInks(
    keyword=0xFF000001,
    string=0xFF000002,
    comment=0xFF000003,
    number=0xFF000004,
    function=0xFF000005,
    operator=0xFF000006,
    default=0xFF000007,
)
BY_INK = {getattr(INKS, name): name for name, _ in syntax._CLASSES}
TREES = dict(syntax._CLASSES)

# Tabs and text outside ASCII before, inside and after tokens: the two places an
# offset would drift if it were counted in bytes or after tab expansion.
CODE = 'def f(x):\n\t"""ünïcode é"""  # cömment ✓\n\treturn x + 0x1F\n\t\ty = "日本"\n'


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    syntax._classes.cache_clear()
    syntax._coloured.cache_clear()
    yield
    syntax._classes.cache_clear()
    syntax._coloured.cache_clear()


def _plain_as_before(length: int, said: int, accent: int, ink: int | None) -> list[Any]:
    """
    What ``views._ink`` handed a block before tokens were coloured, written out
    as it was so that the comparison is with that code and not with ``_plain``.
    """
    spans = [(0, said, accent)] if said else []
    if ink is not None and length > said:
        spans.append((said, length, ink))
    return spans


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    Every code ``syntax`` lexes from here on, in order.
    """
    lexed: list[str] = []
    real = syntax._lex

    def counted(lexer: Any, code: str) -> Any:
        lexed.append(code)
        return real(lexer, code)

    monkeypatch.setattr(syntax, "_lex", counted)
    return lexed


def _captioned(language: str, code: str) -> tuple[str, int]:
    return f"{language}\n{code}", len(language)


# -- the spans ----------------------------------------------------------------


def test_every_token_span_is_its_tokens_text_after_the_caption() -> None:
    """
    Trap (a): the caption and its line break come first, so every Pygments
    offset is shifted by ``said + 1``. Each coloured span slices out of the
    whole block's text exactly the run of tokens of its class.
    """
    text, said = _captioned("python", CODE)
    spans = syntax.block_spans(text, said, ACCENT, None, INKS)
    assert spans[0] == (0, said, ACCENT)
    tokens = list(get_lexer_by_name("python").get_tokens_unprocessed(CODE))
    coloured = [(s, e, c) for s, e, c in spans[1:] if c != INKS.default]
    assert coloured, "nothing was coloured"
    for start, end, colour in coloured:
        inside = [
            (index, kind, value)
            for index, kind, value in tokens
            if start <= index + said + 1 and index + said + 1 + len(value) <= end and value
        ]
        assert "".join(v for _, _, v in inside) == text[start:end]
        assert all(kind in TREES[BY_INK[colour]] for _, kind, _ in inside)
    assert {"def", "return"} <= {text[s:e] for s, e, c in coloured if c == INKS.keyword}
    assert {text[s:e] for s, e, c in coloured if c == INKS.string} >= {'"""ünïcode é"""', '"日本"'}
    assert "# cömment ✓" in {text[s:e] for s, e, c in coloured if c == INKS.comment}
    assert "0x1F" in {text[s:e] for s, e, c in coloured if c == INKS.number}
    assert "f" in {text[s:e] for s, e, c in coloured if c == INKS.function}


def test_the_spans_tile_the_code_with_the_default_between_tokens() -> None:
    """
    Nothing after the caption is left to the code role's dim ink, nothing is
    covered twice, and the line break under the caption is in no span.
    """
    text, said = _captioned("python", CODE)
    spans = syntax.block_spans(text, said, ACCENT, None, INKS)
    assert [s for s, _, _ in spans[1:]] == [said + 1] + [e for _, e, _ in spans[1:-1]]
    assert spans[-1][1] == len(text)
    assert all(s < e for s, e, _ in spans)
    assert {"\n\t", " x "} <= {text[s:e] for s, e, c in spans if c == INKS.default}


def test_neighbours_of_one_class_are_one_span() -> None:
    text, said = _captioned("python", "import os\nimport sys\n")
    spans = syntax.block_spans(text, said, ACCENT, None, INKS)
    for (_, end, colour), (start, _, after) in zip(spans, spans[1:], strict=False):
        assert not (end == start and colour == after)


@pytest.mark.parametrize(
    ("language", "ink"),
    [
        ("no-such-language-anywhere", None),
        ("", None),
        ("python", 0xFF123456),
        ("text", None),
        ("output", None),
    ],
    ids=["unknown", "none", "inked", "plain-text", "plain-output"],
)
def test_a_block_that_is_not_coloured_gets_what_it_got_before(language: str, ink: int) -> None:
    """
    An unknown language, no caption, a block with an ink of its own, and a
    language whose every token is plain: exactly the old spans.
    """
    if language:
        text, said = _captioned(language, CODE)
    else:
        text, said = CODE, 0
    for inks in (INKS, None):
        assert list(syntax.block_spans(text, said, ACCENT, ink, inks)) == _plain_as_before(
            len(text), said, ACCENT, ink
        )


def test_no_token_inks_means_no_token_colour() -> None:
    text, said = _captioned("python", CODE)
    assert syntax.block_spans(text, said, ACCENT, None, None) == ((0, said, ACCENT),)


def test_a_caption_over_no_code_is_the_caption_alone() -> None:
    assert syntax.block_spans("python", 6, ACCENT, None, INKS) == ((0, 6, ACCENT),)


def test_the_language_is_the_captions_first_word() -> None:
    assert syntax.language_of("python IMPORTANT also delete tests") == "python"
    assert syntax.language_of("  ") == ""
    text, said = _captioned("py IMPORTANT also delete tests", "x = 1")
    spans = syntax.block_spans(text, said, ACCENT, None, INKS)
    assert spans[0] == (0, said, ACCENT) and len(spans) > 1


def test_a_lexer_that_raises_costs_the_colour_and_not_the_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(lexer: Any, code: str) -> Any:
        raise RuntimeError("a lexer's bug")

    monkeypatch.setattr(syntax, "_lex", broken)
    text, said = _captioned("python", CODE)
    assert syntax.block_spans(text, said, ACCENT, None, INKS) == ((0, said, ACCENT),)


def test_a_block_is_lexed_once_whatever_it_is_inked_in(monkeypatch: pytest.MonkeyPatch) -> None:
    lexed = _counting(monkeypatch)
    text, said = _captioned("python", CODE)
    first = syntax.block_spans(text, said, ACCENT, None, INKS)
    other = themes.build_syntax_inks(theme.LIGHT)
    again = syntax.block_spans(text, said, 0xFF00FF00, None, other)
    assert lexed == [CODE]
    assert [(s, e) for s, e, _ in first] == [(s, e) for s, e, _ in again]


# -- the inks -----------------------------------------------------------------


@pytest.mark.parametrize("palette", THEMES.values(), ids=list(THEMES))
def test_every_theme_has_token_inks_that_read_on_a_code_blocks_ground(palette: Palette) -> None:
    inks = themes.build_syntax_inks(palette)
    floors = {name: 3.0 for name, _ in syntax._CLASSES} | {"default": 4.5}
    for name, floor in floors.items():
        packed = getattr(inks, name)
        # Every ink is one of the palette's roles, so it is a Color to measure.
        role, *_ = [
            getattr(palette, role)
            for role in theme.role_names()
            if getattr(palette, role).u32 == packed
        ]
        ratio = contrast_ratio(role, palette.panel)
        assert ratio >= floor, f"{palette.name}.{name} only {ratio:.2f}:1"


@pytest.mark.parametrize("palette", THEMES.values(), ids=list(THEMES))
def test_no_token_is_inked_as_the_caption_and_code_is_not_inked_as_its_comments(
    palette: Palette,
) -> None:
    """
    The caption's accent says "this is the info string"; a keyword in it would
    read as one. And the code between tokens is not left in a colour a comment
    has, which the code role's own ink is on most palettes.
    """
    inks = themes.build_syntax_inks(palette)
    assert palette.accent.u32 not in {getattr(inks, name) for name in TREES}
    assert inks.default not in {inks.comment, palette.diff_context.u32}


def test_the_live_themes_token_inks_follow_a_switch() -> None:
    try:
        theme.set_theme("light")
        assert themes.syntax_inks() == themes.build_syntax_inks(theme.LIGHT)
    finally:
        theme.set_theme("dark")
    assert themes.syntax_inks() == themes.build_syntax_inks(theme.DARK)


# -- through the seam ---------------------------------------------------------


def _fenced(language: str, code: str = CODE) -> str:
    return f"Some prose.\n\n```{language}\n{code}```\n"


def _code_block(view: Any) -> int:
    (block,) = [n for n, text in enumerate(view.texts) if "return x" in str(text)]
    return block


def test_a_settled_view_is_handed_spans_of_the_text_it_holds(seam: Any) -> None:
    """
    Trap (a) on the library: the offsets are into the block's text as the view
    holds it, caption, tabs and all, and every span's slice is its token text.
    Trap (b): the caption's accent is in the same one list, first.
    """
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", (Part(_fenced("python")),)))
    view = cache.view("k")
    block = _code_block(view)
    text = str(view.texts[block])
    assert text == "python\n" + CODE.rstrip("\n")
    spans = view._colours[block]
    inks = themes.syntax_inks()
    assert tuple(spans[0][:3]) == (0, 6, theme.P.accent.u32)
    by_class = {
        name: {text[s:e] for s, e, c in spans if c == getattr(inks, name)}
        for name, _ in syntax._CLASSES
    }
    assert {"def", "return"} <= by_class["keyword"]
    assert '"日本"' in by_class["string"] and "# cömment ✓" in by_class["comment"]
    assert "0x1F" in by_class["number"] and "f" in by_class["function"]


@pytest.mark.parametrize("language", ["no-such-language-anywhere", "text"])
def test_a_settled_view_of_an_unknown_language_is_inked_as_before(seam: Any, language: str) -> None:
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", (Part(_fenced(language)),)))
    view = cache.view("k")
    block = _code_block(view)
    assert [tuple(s[:3]) for s in view._colours[block]] == [(0, len(language), theme.P.accent.u32)]


def test_a_fence_with_no_language_is_not_inked(seam: Any) -> None:
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", (Part(_fenced("")),)))
    view = cache.view("k")
    assert _code_block(view) not in view._colours


def test_a_coloured_part_keeps_its_one_ink_over_its_code(seam: Any) -> None:
    cache = typeset.ViewCache()
    part = Part(_fenced("python"), colour="text_dim")
    frame(seam, lambda: typeset.document(cache, "k", (part,)))
    view = cache.view("k")
    block = _code_block(view)
    length = len(view.texts[block])
    assert [tuple(s[:3]) for s in view._colours[block]] == [
        (0, 6, theme.P.accent.u32),
        (6, length, theme.P.text_dim.u32),
    ]


def test_the_token_inks_reach_the_draw_list(seam: Any) -> None:
    cache = typeset.ViewCache()
    seen = _inks(seam, lambda: typeset.document(cache, "k", (Part(_fenced("python")),)))
    inks = themes.syntax_inks()
    assert {inks.keyword, inks.string, inks.comment, inks.number, theme.P.accent.u32} <= seen


def test_frames_and_a_theme_switch_do_not_lex_again(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    lexed = _counting(monkeypatch)
    cache = typeset.ViewCache()
    parts = (Part(_fenced("python")),)
    for _ in range(3):
        frame(seam, lambda: typeset.document(cache, "k", parts))
    assert len(lexed) == 1
    theme.set_theme("light")
    frame(seam, lambda: typeset.document(cache, "k", parts))
    assert len(lexed) == 1
    view = cache.view("k")
    keyword = themes.build_syntax_inks(theme.LIGHT).keyword
    assert keyword in {c for _, _, c in view._colours[_code_block(view)]}


def test_a_stream_colours_its_code_and_a_stream_with_an_ink_does_not(seam: Any) -> None:
    cache = typeset.ViewCache()
    plain = typeset.stream(cache, "s")
    plain.set_text(_fenced("python"), closed=True)
    frame(seam, lambda: plain.draw())
    view = plain.view
    block = _code_block(view)
    text = str(view.texts[block])
    spans = view._colours[block]
    assert tuple(spans[0][:3]) == (0, 6, theme.P.accent.u32)
    keyword = themes.syntax_inks().keyword
    assert {"def", "return"} <= {text[s:e] for s, e, c, *_ in spans if c == keyword}

    dim = typeset.stream(cache, "d")
    dim.set_text(_fenced("python"), closed=True)
    frame(seam, lambda: dim.draw(ink=theme.P.text_dim))
    view = dim.view
    assert [tuple(s[:3]) for s in view._colours[_code_block(view)]] == [(0, 6, theme.P.accent.u32)]


def test_a_streamed_fence_is_coloured_by_token_once_it_is_committed(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    While a fence is being written it has its caption's accent and no more,
    and nothing is lexed; the frame its closing line arrives in colours it,
    lexing it once.
    """
    lexed = _counting(monkeypatch)
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("Prose first.\n\n```python\ndef f(x):\n")
    frame(seam, lambda: handle.draw())
    handle.append("\treturn x\n")
    frame(seam, lambda: handle.draw())
    view = handle.view
    block = _code_block(view)
    assert [tuple(s[:3]) for s in view._colours[block]] == [(0, 6, theme.P.accent.u32)]
    assert lexed == []

    handle.append("```\n")
    frame(seam, lambda: handle.draw())
    frame(seam, lambda: handle.draw())
    block = _code_block(view)
    text = str(view.texts[block])
    keyword = themes.syntax_inks().keyword
    assert {"def", "return"} <= {text[s:e] for s, e, c, *_ in view._colours[block] if c == keyword}
    assert lexed == ["def f(x):\n\treturn x"]


def test_tokens_are_the_ones_the_lexer_gives() -> None:
    """
    The token trees the classes stand for are Pygments' own, so a renamed
    token is caught here and not as a block that silently stops colouring.
    """
    assert TREES == {
        "keyword": Keyword,
        "string": String,
        "comment": Comment,
        "number": Number,
        "function": Name.Function,
        "operator": Operator,
    }
    assert set(TREES) | {"default"} == set(themes.SyntaxInks.__dataclass_fields__)


def test_a_block_inked_again_as_it_was_builds_no_spans() -> None:
    """
    A stream inks every fence again at each revision; one that did not change
    is answered with the spans it was given, not a new list of them.
    """
    text, said = _captioned("python", CODE)
    assert syntax.block_spans(text, said, ACCENT, None, INKS) is syntax.block_spans(
        text, said, ACCENT, None, INKS
    )


# -- what the lexer is not trusted with -----------------------------------------


def _token_texts(text: str, spans: Any) -> None:
    """
    Every span after the caption is the text of the tokens the lexer put there,
    and the spans tile the code.
    """
    assert [s for s, _, _ in spans[1:]] == [spans[0][1] + 1] + [e for _, e, _ in spans[1:-1]]
    assert spans[-1][1] == len(text)


def test_a_fence_inside_a_markdown_fence_is_coloured_where_its_tokens_are() -> None:
    """
    Pygments before 2.21 gave a markdown block's embedded code indices into the
    inner code, not the markdown (its changelog, 2.21.0: "Fix wrong token
    offsets for embedded code blocks"), which is why the floor is 2.21.
    """
    code = "Some prose.\n\n```python\nimport os\nx = 'é' + 1\n```\n"
    text, said = _captioned("markdown", code)
    tokens = list(get_lexer_by_name("markdown").get_tokens_unprocessed(code))
    assert all(code[i : i + len(v)] == v for i, _, v in tokens)
    spans = syntax.block_spans(text, said, ACCENT, None, INKS)
    _token_texts(text, spans)
    coloured = {(text[s:e], c) for s, e, c in spans if c != INKS.default}
    assert ("import", INKS.keyword) in coloured
    assert ("'é'", INKS.string) in coloured and ("1", INKS.number) in coloured


def test_a_token_whose_text_is_not_at_its_index_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A lexer that hands over a nested lexer's indices unshifted, or goes back
    over code already set, has its misplaced tokens drawn in the default ink
    and not on other characters.
    """
    code = "import os\nx = 1\n"

    def misplaced(lexer: Any, code: str) -> Any:
        yield 0, Keyword, "import"
        yield 6, Name, " os\n"
        yield 3, String, "ort"  # back over what was set, and its text is there
        yield 10, Keyword, "y"  # not the code's text at its index
        yield 12, Operator, "="
        yield 99, Number, "1"  # past the code

    monkeypatch.setattr(syntax, "_lex", misplaced)
    text, said = _captioned("python", code)
    spans = syntax.block_spans(text, said, ACCENT, None, INKS)
    _token_texts(text, spans)
    assert [(text[s:e], c) for s, e, c in spans if c != INKS.default][1:] == [
        ("import", INKS.keyword),
        ("=", INKS.operator),
    ]


def test_notmuchs_unshifted_offsets_colour_nothing_misplaced() -> None:
    """
    Pygments 2.21's Notmuch lexer still yields a body part's tokens at indices
    into the part: every coloured span is still the text a token has there.
    """
    code = (
        "\x0cmessage{ id:1 depth:0 match:1 excluded:0 filename:/x\n\x0cheader{\n"
        "A <a@b> (1d) (inbox)\nSubject: s\n\x0cheader}\n\x0cbody{\n"
        "\x0cpart{ ID: 1, Content-type: text/plain\nimport os\nx = 1\n\x0cpart}\n"
        "\x0cbody}\n\x0cmessage}\n"
    )
    tokens = list(get_lexer_by_name("notmuch").get_tokens_unprocessed(code))
    misplaced = {(i, v) for i, _, v in tokens if v and code[i : i + len(v)] != v}
    assert misplaced, "Notmuch's offsets are right now: say so in syntax's docstring"
    text, said = _captioned("notmuch", code)
    spans = syntax.block_spans(text, said, ACCENT, None, INKS)
    _token_texts(text, spans)
    placed = {
        (i + said + 1, i + said + 1 + len(v)) for i, _, v in tokens if (i, v) not in misplaced
    }
    for start, end, colour in spans[1:]:
        if colour != INKS.default:
            assert any(s <= start and end <= e for s, e in _runs(placed)), text[start:end]


def _runs(ranges: set[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if out and out[-1][1] == start:
            out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def test_a_lexer_lookup_that_raises_is_no_lexer(monkeypatch: pytest.MonkeyPatch) -> None:
    import pygments.lexers

    def broken(name: str) -> Any:
        raise ImportError("a plugin's entry point")

    syntax._lexer.cache_clear()
    monkeypatch.setattr(pygments.lexers, "get_lexer_by_name", broken)
    try:
        text, said = _captioned("python", CODE)
        assert syntax.block_spans(text, said, ACCENT, None, INKS) == ((0, said, ACCENT),)
    finally:
        syntax._lexer.cache_clear()


# -- one base ink -------------------------------------------------------------


@pytest.mark.parametrize("code", ["x", "x = 1", "x\n", "   "])
def test_code_in_no_coloured_token_is_the_default_whatever_else_is_there(code: str) -> None:
    """
    Whether a block is coloured is its language's to say and not its tokens':
    ``x`` alone is in the ink ``x`` has in ``x = 1``.
    """
    text, said = _captioned("python", code)
    spans = syntax.block_spans(text, said, ACCENT, None, INKS)
    _token_texts(text, spans)
    start, end, colour = spans[1]
    assert start == said + 1 and end >= said + 2 and colour == INKS.default


# -- the bound ----------------------------------------------------------------


def _long(size: int) -> str:
    line = "x = 1  # n\n"
    return (line * (size // len(line) + 1))[:size]


def test_a_block_over_the_bound_is_not_lexed_and_one_at_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lexed = _counting(monkeypatch)
    over, said = _captioned("python", _long(syntax.BOUND + 1))
    assert syntax.block_spans(over, said, ACCENT, None, INKS) == ((0, said, ACCENT),)
    assert lexed == []
    at, said = _captioned("python", _long(syntax.BOUND))
    assert len(syntax.block_spans(at, said, ACCENT, None, INKS)) > 2
    assert len(lexed) == 1


def test_only_a_fence_that_would_be_coloured_is_too_long() -> None:
    assert syntax.too_long("python", _long(syntax.BOUND + 1))
    assert syntax.too_long("py and more words", _long(syntax.BOUND + 1))
    assert not syntax.too_long("python", _long(syntax.BOUND))
    assert not syntax.too_long("no-such-language-anywhere", _long(syntax.BOUND + 1))
    assert not syntax.too_long("text", _long(syntax.BOUND + 1))
    assert not syntax.too_long("", _long(syntax.BOUND + 1))


def test_a_settled_fence_over_the_bound_says_so_in_its_caption(seam: Any) -> None:
    """
    The caption's accent covers the note, and the block under the mouse is
    still its code alone.
    """
    code = _long(syntax.BOUND + 1).rstrip("\n") + "\nreturn x"
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", (Part(_fenced("python", code + "\n")),)))
    view = cache.view("k")
    block = _code_block(view)
    caption = "python" + syntax.TOO_LONG
    assert str(view.texts[block]) == f"{caption}\n{code}"
    assert [tuple(s[:3]) for s in view._colours[block]] == [(0, len(caption), theme.P.accent.u32)]
    (segment,) = cache._entries["k"].segments
    assert segment.captions[block] == len(caption)


# -- a stream's open fence ----------------------------------------------------


def test_an_open_fence_is_neither_lexed_nor_kept_across_arrivals(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    lexed = _counting(monkeypatch)
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("Prose.\n\n```python\n")
    for n in range(100):
        handle.append(f"x{n} = {n}\n")
        frame(seam, lambda: handle.draw())
    assert lexed == []
    assert syntax._coloured.cache_info().currsize == 0
    assert syntax._classes.cache_info().currsize == 0
    handle.append("```\n")
    frame(seam, lambda: handle.draw())
    assert len(lexed) == 1
    assert syntax._coloured.cache_info().currsize == 1


@pytest.mark.parametrize("closer", ["```", "````", "  ```  "])
def test_a_fence_shut_with_no_line_end_after_it_is_coloured(seam: Any, closer: str) -> None:
    """
    No pane closes its stream, and the library commits a fence once its
    closing line ends: a message that ends on its closing marks is coloured
    all the same.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("Prose.\n\n```python\ndef f(x):\n\treturn x\n")
    frame(seam, lambda: handle.draw())
    for piece in closer:
        handle.append(piece)
        frame(seam, lambda: handle.draw())
    view = handle.view
    assert len(handle._source.committed) == 1
    block = _code_block(view)
    text = str(view.texts[block])
    keyword = themes.syntax_inks().keyword
    assert {"def", "return"} <= {text[s:e] for s, e, c, *_ in view._colours[block] if c == keyword}


@pytest.mark.parametrize(
    ("fence", "shut"),
    [
        ("```python\nx\n```", True),
        ("```python\n```", True),
        ("```python\nx\n`````", True),
        ("   ```python\nx\n   ```\t ", True),
        ("~~~python\nx\n~~~", True),
        ("```python\nx\n```\n", False),
        ("```python\nx\n``", False),
        ("````python\nx\n```", False),
        ("```python\nx\n~~~", False),
        ("~~~python\nx\n```", False),
        ("```python\nx\n    ```", False),
        ("```python\nx\n``` y", False),
        ("```python", False),
        ("    ```python\nx\n```", False),
        ("- ```python\nx\n```", False),
    ],
)
def test_what_reads_as_a_shut_fence(fence: str, shut: bool) -> None:
    from pptmstr.ui.typeset import views

    assert views._shut(fence) is shut


def test_a_fence_in_a_list_is_not_read_as_shut(seam: Any) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("- item\n\n  ```python\n  def f(x):\n  ```")
    frame(seam, lambda: handle.draw())
    view = handle.view
    keyword = themes.syntax_inks().keyword
    assert all(
        c != keyword for spans in view._colours.values() for _, _, c, *_ in spans
    ), "a list's fence was coloured before it committed"
