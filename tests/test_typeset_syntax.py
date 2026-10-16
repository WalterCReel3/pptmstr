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
from pygments.token import Comment, Generic, Keyword, Name, Number, Operator, String

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
    add=0xFF000008,
    remove=0xFF000009,
    heading=0xFF00000A,
)
BY_INK = {getattr(INKS, name): name for name, _ in syntax._CLASSES}
TREES: dict[str, tuple[Any, ...]] = {}
for _name, _tree in syntax._CLASSES:
    TREES[_name] = (*TREES.get(_name, ()), _tree)
# The classes a theme borrows a role for as it is; the diff classes are each a
# role stepped to a floor (``themes.DIFF_TOKEN_FLOOR``) and are measured apart.
ROLE_CLASSES = ("keyword", "string", "comment", "number", "function", "operator")

# Tabs and text outside ASCII before, inside and after tokens: the two places an
# offset would drift if it were counted in bytes or after tab expansion.
CODE = 'def f(x):\n\t"""ünïcode é"""  # cömment ✓\n\treturn x + 0x1F\n\t\ty = "日本"\n'


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    syntax._LEXED.clear()
    syntax._SPANS.clear()
    yield
    syntax._LEXED.clear()
    syntax._SPANS.clear()


def _kept() -> tuple[int, int]:
    """
    How many blocks each cache holds: ``block_spans``' spans, and the lexed runs.
    """
    return len(syntax._SPANS), len(syntax._LEXED)


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
        assert all(any(kind in tree for tree in TREES[BY_INK[colour]]) for _, kind, _ in inside)
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
    floors = {name: 3.0 for name in ROLE_CLASSES} | {"default": 4.5}
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
    # Not the diff's add and remove: they are the theme's own two diff inks,
    # which its diff view sets under a header in the accent too, and on ghost
    # ``diff_add`` is the accent's colour.
    assert palette.accent.u32 not in {getattr(inks, name) for name in TREES if name in ROLE_CLASSES}
    assert palette.accent.u32 != inks.heading
    assert inks.default not in {inks.comment, palette.diff_context.u32}


def _packed(colour: int) -> tuple[int, int, int]:
    """
    An ImGui packed ink (ABGR) as its red, green and blue.
    """
    return colour & 0xFF, (colour >> 8) & 0xFF, (colour >> 16) & 0xFF


def _as_color(colour: int) -> Any:
    r, g, b = _packed(colour)
    return theme.col((r << 16) | (g << 8) | b)


DIFF_ROLES = {"add": "diff_add", "remove": "diff_remove", "heading": "state_supervising"}


@pytest.mark.parametrize("palette", THEMES.values(), ids=list(THEMES))
def test_a_diffs_lines_read_at_body_texts_floor_on_every_theme(palette: Palette) -> None:
    """
    Where a diff fence is code, its added, removed and header lines carry the
    change in ink alone, with no fill behind them: 4.5:1 on the code's ground,
    and each its own colour, apart from the text between them.
    """
    inks = themes.build_syntax_inks(palette)
    for name in DIFF_ROLES:
        ratio = contrast_ratio(_as_color(getattr(inks, name)), palette.panel)
        assert ratio >= themes.DIFF_TOKEN_FLOOR, f"{palette.name}.{name} only {ratio:.2f}:1"
    assert len({inks.add, inks.remove, inks.heading, inks.default}) == 4


@pytest.mark.parametrize("palette", THEMES.values(), ids=list(THEMES))
def test_a_diff_ink_is_its_role_where_that_reads_and_the_least_step_toward_text_where_not(
    palette: Palette,
) -> None:
    """
    The role's own colour is kept wherever it already reads, so a theme's
    diff colours are the ones its diff view uses; one under the floor is moved
    toward ``text`` no further than the floor asks: one step less would not read.
    """
    inks = themes.build_syntax_inks(palette)
    for name, role_name in DIFF_ROLES.items():
        role = getattr(palette, role_name)
        packed = getattr(inks, name)
        if contrast_ratio(role, palette.panel) >= themes.DIFF_TOKEN_FLOOR:
            assert packed == role.u32, f"{palette.name}.{name}"
            continue
        assert packed != role.u32
        steps = themes._DIFF_TOKEN_STEPS
        tried = [_step(role, palette.text, step / steps) for step in range(steps + 1)]
        reads = [contrast_ratio(t, palette.panel) >= themes.DIFF_TOKEN_FLOOR for t in tried]
        assert packed == tried[reads.index(True)].u32, f"{palette.name}.{name}"


def _step(ink: Any, toward: Any, alpha: float) -> Any:
    """
    ``ink`` moved ``alpha`` of the way to ``toward``, opaque and in whole 8-bit channels.
    """
    mixed = theme.mixed(ink, toward, alpha)
    r, g, b = (round(c * 255.0) for c in (mixed.x, mixed.y, mixed.z))
    return theme.col((r << 16) | (g << 8) | b)


def test_the_diff_inks_were_measured_under_their_roles_somewhere() -> None:
    """
    The step is not dead code: on the themes as they are, some diff role is
    under the floor and is moved (dark's ``diff_remove`` among them, 4.30:1).
    """
    moved = [
        (name, palette.name)
        for palette in THEMES.values()
        for name, role in DIFF_ROLES.items()
        if getattr(themes.build_syntax_inks(palette), name) != getattr(palette, role).u32
    ]
    assert ("remove", "dark") in moved


def test_a_diff_fence_that_stays_code_has_its_lines_in_add_remove_and_heading_inks() -> None:
    """
    What Pygments' diff lexer calls each line, in the inks of the three new
    classes, the last line too though the library holds no line end after it.
    """
    code = "diff --git a/x b/x\nindex 1..2\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n ctx\n-old\n+new"
    text, said = _captioned("diff", code)
    spans = syntax.block_spans(text, said, ACCENT, None, INKS)
    by_text = {text[s:e]: BY_INK.get(c, c) for s, e, c in spans}
    assert by_text["diff --git a/x b/x"] == "heading"
    assert by_text["index 1..2"] == "heading"
    assert by_text["@@ -1,2 +1,2 @@"] == "heading"
    assert by_text["--- a/x"] == "remove"
    assert by_text["+++ b/x"] == "add"
    assert by_text["-old"] == "remove"
    assert by_text["+new"] == "add", "the last line, with no line end after it"
    assert spans[-1][1] == len(text), "and nothing is coloured past the text"


def test_code_with_no_last_line_end_is_lexed_as_ending_on_one_and_nothing_past_it() -> None:
    """
    ``_lex`` hands the lexer the code with a line end where it has none, as
    ``get_tokens`` would, and cuts every token back to the code.
    """
    lexer = get_lexer_by_name("diff")
    tokens = list(syntax._lex(lexer, "+a\n+b"))
    assert "".join(value for _, _, value in tokens) == "+a\n+b"
    assert [(i, str(k), v) for i, k, v in tokens][-1] == (3, "Token.Generic.Inserted", "+b")
    assert list(syntax._lex(lexer, "+a\n")) == list(lexer.get_tokens_unprocessed("+a\n"))


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
        "keyword": (Keyword,),
        "string": (String,),
        "comment": (Comment,),
        "number": (Number,),
        "function": (Name.Function,),
        "operator": (Operator,),
        "add": (Generic.Inserted,),
        "remove": (Generic.Deleted,),
        "heading": (Generic.Heading, Generic.Subheading),
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
    assert _kept() == (0, 0)
    handle.append("```\n")
    frame(seam, lambda: handle.draw())
    assert len(lexed) == 1
    assert _kept() == (1, 1)


@pytest.mark.parametrize("pieces", [["```"], ["`", "`", "`"], ["````"], ["  ``", "`  "]])
def test_a_closing_line_with_no_line_end_is_not_a_close(
    seam: Any, monkeypatch: pytest.MonkeyPatch, pieces: list[str]
) -> None:
    """
    Marks that may be the start of an info string still arriving leave the
    fence open: nothing is lexed or kept, and the closing line is not in the
    block's text. The line end that closes it lexes it once.
    """
    lexed = _counting(monkeypatch)
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("Prose.\n\n```python\ndef f(x):\n\treturn x\n")
    frame(seam, lambda: handle.draw())
    for piece in pieces:
        handle.append(piece)
        frame(seam, lambda: handle.draw())
    view = handle.view
    block = _code_block(view)
    assert str(view.texts[block]) == "python\ndef f(x):\n\treturn x"
    assert [tuple(s[:3]) for s in view._colours[block]] == [(0, 6, theme.P.accent.u32)]
    assert lexed == [] and _kept() == (0, 0)
    handle.append("\n")
    frame(seam, lambda: handle.draw())
    frame(seam, lambda: handle.draw())
    text = str(view.texts[_code_block(view)])
    keyword = themes.syntax_inks().keyword
    spans = view._colours[_code_block(view)]
    assert {"def", "return"} <= {text[s:e] for s, e, c, *_ in spans if c == keyword}
    assert lexed == ["def f(x):\n\treturn x"] and _kept() == (1, 1)


@pytest.mark.parametrize("marks", [["```"], ["``", "`"], ["`", "`", "`"]])
def test_marks_inside_an_open_fence_never_lex_it(
    seam: Any, monkeypatch: pytest.MonkeyPatch, marks: list[str]
) -> None:
    """
    A ```markdown fence quoting a ```python one: the inner opener arrives as
    marks that, for a frame, are all its line holds.
    """
    lexed = _counting(monkeypatch)
    handle = typeset.stream(typeset.ViewCache(), "s")
    for piece in ["Prose.\n\n```markdown\nUse:\n", *marks, "python\nx = 1\n"]:
        handle.append(piece)
        frame(seam, lambda: handle.draw())
    assert lexed == [] and _kept() == (0, 0)
    assert len(handle._source.committed) == 1


def test_a_stream_closed_on_its_closing_marks_is_coloured(seam: Any) -> None:
    handle = typeset.stream(typeset.ViewCache(), "s")
    handle.append("Prose.\n\n```python\ndef f(x):\n\treturn x\n")
    frame(seam, lambda: handle.draw())
    handle.append("```")
    handle.close()
    frame(seam, lambda: handle.draw())
    view = handle.view
    block = _code_block(view)
    text = str(view.texts[block])
    keyword = themes.syntax_inks().keyword
    assert {"def", "return"} <= {text[s:e] for s, e, c, *_ in view._colours[block] if c == keyword}


def test_a_fence_in_a_list_is_not_coloured_before_it_closes(seam: Any) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("- item\n\n  ```python\n  def f(x):\n  ```")
    frame(seam, lambda: handle.draw())
    view = handle.view
    keyword = themes.syntax_inks().keyword
    assert all(
        c != keyword for spans in view._colours.values() for _, _, c, *_ in spans
    ), "a list's fence was coloured before it committed"


# -- what is kept ---------------------------------------------------------------


@pytest.mark.parametrize("language", ["nosuchlang", "text", "output"])
def test_a_language_that_is_not_coloured_keeps_nothing(language: str) -> None:
    text, said = _captioned(language, "x = 1\n")
    assert syntax.block_spans(text, said, ACCENT, None, INKS) == ((0, said, ACCENT),)
    assert not syntax.needs_lex(text, said, None, INKS, accent=ACCENT)
    assert _kept() == (0, 0)


def test_the_caches_drop_the_least_recently_used_by_size() -> None:
    kept = syntax._Kept(10)
    kept.put("a", 4, 1)
    kept.put("b", 4, 2)
    assert kept.get("a") == 1  # "b" is now the least recently used
    kept.put("c", 4, 3)
    assert "b" not in kept and "a" in kept and "c" in kept and kept.held == 8
    kept.put("big", 11, 4)
    assert "big" not in kept and kept.held == 8
    kept.put("a", 2, 5)
    assert kept.get("a") == 5 and kept.held == 6


def test_needs_lex_says_whether_block_spans_would_lex(monkeypatch: pytest.MonkeyPatch) -> None:
    lexed = _counting(monkeypatch)
    text, said = _captioned("python", CODE)
    assert syntax.needs_lex(text, said, None, INKS, accent=ACCENT)
    assert not syntax.needs_lex(text, said, ACCENT, INKS, accent=ACCENT)
    assert not syntax.needs_lex(text, said, None, None, accent=ACCENT)
    syntax.block_spans(text, said, ACCENT, None, INKS)
    assert len(lexed) == 1
    assert not syntax.needs_lex(text, said, None, INKS, accent=ACCENT)
    other = themes.SyntaxInks(*(ink + 16 for ink in INKS.__dict__.values()))
    assert not syntax.needs_lex(text, said, None, other, accent=ACCENT)
    syntax.block_spans(text, said, ACCENT, None, other)
    assert len(lexed) == 1


# -- what a frame spends lexing -------------------------------------------------


def _python(n: int) -> str:
    return f"def f{n}(x):\n    return x + {n}\n"


def _fences(count: int) -> str:
    return "".join(f"Fence {n}.\n\n```python\n{_python(n)}```\n\n" for n in range(count))


@pytest.fixture
def ticking(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    Every lex costs 3 ms on the budget's clock, so a frame lexes three blocks
    (0, 3 and 6 ms are under the 8 ms budget; 9 is not).
    """
    from pptmstr.ui.typeset import views

    lexed = _counting(monkeypatch)
    now = [0.0]
    real = syntax._lex

    def lex(lexer: Any, code: str) -> Any:
        now[0] += 0.003
        return real(lexer, code)

    monkeypatch.setattr(syntax, "_lex", lex)
    monkeypatch.setattr(views._LEXING, "clock", lambda: now[0])
    return lexed


def _keyworded(view: Any) -> int:
    keyword = themes.syntax_inks().keyword
    return sum(1 for spans in view._colours.values() if any(c == keyword for _, _, c, *_ in spans))


def test_a_document_of_many_fences_is_coloured_over_frames(seam: Any, ticking: list[str]) -> None:
    cache = typeset.ViewCache()
    parts = (Part(_fences(8)),)
    seen = []
    for _ in range(4):
        frame(seam, lambda: typeset.document(cache, "k", parts))
        seen.append((len(ticking), _keyworded(cache.view("k"))))
    assert seen == [(3, 3), (6, 6), (8, 8), (8, 8)]
    assert sorted(ticking) == sorted(_python(n).rstrip("\n") for n in range(8))
    assert not cache._entries["k"].pending


def test_a_block_left_for_later_draws_as_an_uncoloured_one(seam: Any, ticking: list[str]) -> None:
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", (Part(_fences(4)),)))
    view = cache.view("k")
    (left,) = [n for n, text in enumerate(view.texts) if "def f3" in str(text)]
    assert [tuple(s[:3]) for s in view._colours[left]] == [(0, 6, theme.P.accent.u32)]


def test_the_budget_is_the_frames_across_every_view(seam: Any, ticking: list[str]) -> None:
    cache = typeset.ViewCache()
    one, two = (Part(_fences(2)),), (Part(_fences(4)[len(_fences(2)) :]),)

    def both() -> None:
        typeset.document(cache, "a", one)
        typeset.document(cache, "b", two)

    frame(seam, both)
    assert len(ticking) == 3
    assert (_keyworded(cache.view("a")), _keyworded(cache.view("b"))) == (2, 1)
    frame(seam, both)
    assert len(ticking) == 4 and _keyworded(cache.view("b")) == 2


def test_a_theme_switch_while_blocks_are_left_inks_them_all(seam: Any, ticking: list[str]) -> None:
    cache = typeset.ViewCache()
    parts = (Part(_fences(5)),)
    frame(seam, lambda: typeset.document(cache, "k", parts))
    theme.set_theme("light")
    try:
        for _ in range(3):
            frame(seam, lambda: typeset.document(cache, "k", parts))
        view = cache.view("k")
        keyword = themes.build_syntax_inks(theme.LIGHT).keyword
        assert (
            sum(1 for spans in view._colours.values() if any(c == keyword for _, _, c, *_ in spans))
            == 5
        )
        assert len(ticking) == 5
    finally:
        theme.set_theme("dark")


def test_a_stream_past_the_budget_colours_its_fences_on_later_frames(
    seam: Any, ticking: list[str]
) -> None:
    handle = typeset.stream(typeset.ViewCache(), "s")
    handle.append(_fences(5))
    frame(seam, lambda: handle.draw())
    assert len(ticking) == 3 and _keyworded(handle.view) == 3
    frame(seam, lambda: handle.draw())
    assert len(ticking) == 5 and _keyworded(handle.view) == 5
    frame(seam, lambda: handle.draw())
    assert len(ticking) == 5


# -- the note on a fence too long to colour ------------------------------------


def test_a_part_with_an_ink_of_its_own_is_not_told_it_is_too_long(seam: Any) -> None:
    code = _long(syntax.BOUND + 1).rstrip("\n") + "\nreturn x"
    cache = typeset.ViewCache()
    part = Part(_fenced("python", code + "\n"), colour="text_dim")
    frame(seam, lambda: typeset.document(cache, "k", (part,)))
    view = cache.view("k")
    assert str(view.texts[_code_block(view)]) == f"python\n{code}"
    frame(seam, lambda: typeset.document(cache, "k", (Part(part.text),)))
    assert str(view.texts[_code_block(view)]).startswith("python" + syntax.TOO_LONG + "\n")


def test_a_stream_with_an_ink_of_its_own_is_not_told_it_is_too_long(seam: Any) -> None:
    code = _long(syntax.BOUND + 1).rstrip("\n") + "\nreturn x"
    cache = typeset.ViewCache()
    dim = typeset.stream(cache, "d")
    dim.set_text(_fenced("python", code + "\n"))
    frame(seam, lambda: dim.draw(ink=theme.P.text_dim))
    assert str(dim.view.texts[_code_block(dim.view)]) == f"python\n{code}"
    plain = typeset.stream(cache, "p")
    plain.set_text(_fenced("python", code + "\n"))
    frame(seam, lambda: plain.draw())
    assert str(plain.view.texts[_code_block(plain.view)]).startswith("python" + syntax.TOO_LONG)


def test_a_block_already_lexed_is_coloured_on_a_frame_whose_budget_is_spent(
    seam: Any, ticking: list[str]
) -> None:
    cache = typeset.ViewCache()
    known = (Part(_fences(2)),)
    frame(seam, lambda: typeset.document(cache, "a", known))
    assert len(ticking) == 2
    fresh = (Part(_fences(6)[len(_fences(2)) :]),)

    def both() -> None:
        typeset.document(cache, "b", fresh)
        typeset.document(cache, "c", known)

    frame(seam, both)
    assert len(ticking) == 5
    assert _keyworded(cache.view("c")) == 2


def test_a_later_frame_inks_only_the_blocks_left(
    seam: Any, ticking: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = typeset.ViewCache()
    parts = (Part(_fences(5)),)
    frame(seam, lambda: typeset.document(cache, "k", parts))
    asked: list[str] = []
    real = syntax.block_spans

    def spy(text: str, *rest: Any) -> Any:
        asked.append(text)
        return real(text, *rest)

    monkeypatch.setattr(syntax, "block_spans", spy)
    frame(seam, lambda: typeset.document(cache, "k", parts))
    assert len(asked) == 2 and all("def f3" in t or "def f4" in t for t in asked)


# -- the two caches agree --------------------------------------------------------


def test_a_block_whose_spans_are_kept_needs_no_lex_once_its_runs_are_gone() -> None:
    text, said = _captioned("python", CODE)
    syntax.block_spans(text, said, ACCENT, None, INKS)
    syntax._LEXED.clear()
    assert not syntax.needs_lex(text, said, None, INKS, accent=ACCENT)
    other = themes.SyntaxInks(*(ink + 16 for ink in INKS.__dict__.values()))
    assert syntax.needs_lex(text, said, None, other, accent=ACCENT)
    assert syntax.needs_lex(text, said, None, INKS, accent=ACCENT + 1)
    # Without the accent only the runs are asked of, and they are gone.
    assert syntax.needs_lex(text, said, None, INKS)


def test_a_streamed_fence_stays_coloured_after_its_runs_are_let_go(seam: Any) -> None:
    """
    A committed fence inked again at every revision is answered from
    ``_SPANS`` and never touches ``_LEXED``, which may let it go. On a frame
    whose budget an earlier view spent, it is coloured as it was.
    """
    from pptmstr.ui.typeset import views

    handle = typeset.stream(typeset.ViewCache(), "s")
    handle.append("Prose.\n\n```python\ndef f(x):\n    return x\n```\n\nmore ")
    frame(seam, lambda: handle.draw())
    view = handle.view
    before = view._colours[_code_block(view)]
    assert _keyworded(view) == 1
    syntax._LEXED.clear()
    views._LEXING.frame, views._LEXING.spent = seam.get_frame_count() + 1, 1.0
    handle.append("x\n")
    frame(seam, lambda: handle.draw())
    assert view._colours[_code_block(view)] == before
    assert handle._inked is not None


# -- what the caches hold ---------------------------------------------------------

_SHELL = (
    "for f in *.py; do\n"
    '  grep -n \'def \' "$f" | sort -u > "$TMP/$f.txt" && echo ok || exit 1\n'
    "done\n"
)
_JSON = "".join(f'  "key{n}": [{n}, {n}.5, "s{n}", null, true, {{"n": {n}}}],\n' for n in range(60))
_FENCES = {
    "python": ("python", CODE * 30),
    "json": ("json", "{\n" + _JSON + "}\n"),
    "bash": ("bash", _SHELL * 30),
    "one line": ("python", "x = 1\n"),
}


@pytest.mark.parametrize("cache", ["_SPANS", "_LEXED"])
@pytest.mark.parametrize("fence", list(_FENCES))
def test_what_a_cache_holds_is_at_most_what_it_weighs(
    fence: str, cache: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Filled past its limit, a cache holds no more memory, measured, than its
    weights say, and not less than half of it, for fences of many tokens and
    for one of a line: the weights are what bounds it, and not a guess. The
    other cache keeps nothing meanwhile, so that what is measured is this one.
    """
    import gc
    import tracemalloc

    limit = 2**20
    language, code = _FENCES[fence]
    syntax.block_spans(*_captioned(language, code), ACCENT, None, INKS)  # the lexer, loaded
    kept = syntax._Kept(limit)
    other = "_LEXED" if cache == "_SPANS" else "_SPANS"
    monkeypatch.setattr(syntax, cache, kept)
    monkeypatch.setattr(syntax, other, syntax._Kept(0))
    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        n = 0
        while len(kept) == n or n < 10:
            syntax.block_spans(*_captioned(language, f"{n}\n{code}"), ACCENT, None, INKS)
            n += 1
        gc.collect()
        held = tracemalloc.get_traced_memory()[0] - base
    finally:
        tracemalloc.stop()
    assert len(getattr(syntax, other)) == 0 and kept.held <= limit
    assert kept.held / 2 <= held <= kept.held, (held, kept.held)


def test_the_caches_together_are_held_under_fifty_mebibytes() -> None:
    assert syntax._SPANS.limit == syntax._LEXED.limit == syntax._KEPT
    assert 2 * syntax._KEPT <= 50 * 2**20


# -- one ink for a whole document ---------------------------------------------------


def test_markdown_in_one_ink_colours_no_token(seam: Any) -> None:
    cache = typeset.ViewCache()
    source = _fenced("python")
    frame(seam, lambda: typeset.markdown(cache, "k", source, ink=theme.P.text_dim))
    view = cache.view("k")
    assert [tuple(s[:3]) for s in view._colours[_code_block(view)]] == [(0, 6, theme.P.accent.u32)]
    frame(seam, lambda: typeset.markdown(cache, "k", source))
    assert _keyworded(view) == 1
    frame(seam, lambda: typeset.markdown(cache, "k", source, ink=theme.P.text_dim))
    assert _keyworded(view) == 0


def test_markdown_in_one_ink_is_not_told_a_fence_is_too_long(seam: Any) -> None:
    code = _long(syntax.BOUND + 1).rstrip("\n") + "\nreturn x"
    cache = typeset.ViewCache()
    source = _fenced("python", code + "\n")
    frame(seam, lambda: typeset.markdown(cache, "k", source, ink=theme.P.text_dim))
    view = cache.view("k")
    assert str(view.texts[_code_block(view)]) == f"python\n{code}"
    frame(seam, lambda: typeset.markdown(cache, "k", source))
    assert str(cache.view("k").texts[_code_block(cache.view("k"))]).startswith(
        "python" + syntax.TOO_LONG + "\n"
    )


# -- the shared ImGui context -------------------------------------------------------


def _modules_in_a_child(tmp_path: Any, modules: dict[str, str]) -> str:
    """
    Run ``modules``, written into ``tmp_path`` under their names, in a pytest
    of their own with this suite's ``conftest`` and ``tmp_path`` as its
    working directory, in name order. What it printed; it must pass.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    from tests.conftest import child_environment

    for name, source in modules.items():
        (tmp_path / name).write_text(source, encoding="utf-8")
    root = str(Path(__file__).resolve().parent.parent)
    environ = child_environment()
    environ["PYTHONPATH"] = os.pathsep.join(filter(None, [root, environ.get("PYTHONPATH")]))
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "tests.conftest", "-p", "no:cacheprovider"]
        + ["--rootdir", str(tmp_path), *sorted(modules)],
        cwd=tmp_path,
        env=environ,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0 and f"{len(modules)} passed" in done.stdout, done.stdout[-2000:]
    return done.stdout


_PLACED = "[Window][##placed]\nPos=123,45\nSize=200,100\n"

_DRAWS_THE_PLACED_WINDOW = """
def test_the_placed_window_is_where_imgui_puts_a_new_one(imgui):
    imgui.new_frame()
    imgui.begin("##placed")
    at = imgui.get_window_pos()
    imgui.end()
    imgui.render()
    assert (at.x, at.y) != (123.0, 45.0)
"""


def test_the_shared_context_reads_and_writes_no_ini_file(tmp_path: Any) -> None:
    """
    An ``imgui.ini`` in the working directory that places a window is not
    read by the context a module shares, and is not written over when the
    context is destroyed, which is when ImGui saves to one it was given.
    """
    (tmp_path / "imgui.ini").write_text(_PLACED, encoding="utf-8")
    _modules_in_a_child(tmp_path, {"test_placed.py": _DRAWS_THE_PLACED_WINDOW})
    assert (tmp_path / "imgui.ini").read_text(encoding="utf-8") == _PLACED


_SPENT_AT_ITS_END = """
from pptmstr.ui.typeset import views


def test_ends_on_a_spent_frame(imgui):
    views._LEXING.frame, views._LEXING.spent = 1, 1.0
"""

_COLOURED_ON_ITS_FIRST_FRAME = """
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Part, themes
from tests.test_typeset_views import frame


def test_coloured_on_the_first_frame(seam):
    cache = typeset.ViewCache()
    part = Part("```python\\ndef f():\\n    pass\\n```\\n")
    frame(seam, lambda: typeset.document(cache, "k", (part,)))
    keyword = themes.syntax_inks().keyword
    view = cache.view("k")
    assert any(c == keyword for spans in view._colours.values() for _, _, c, *_ in spans)
"""


def test_a_module_starts_with_the_frames_lexing_budget_unspent(tmp_path: Any) -> None:
    """
    Each module's context counts its frames from one again, so the lexing
    budget the module before left spent on frame 1 is not this module's.
    """
    _modules_in_a_child(
        tmp_path,
        {"test_a_first.py": _SPENT_AT_ITS_END, "test_b_then.py": _COLOURED_ON_ITS_FIRST_FRAME},
    )


def test_a_last_token_that_took_the_added_line_end_is_cut_back_to_the_code() -> None:
    """
    Shell's comment rule ends on its line end and takes it into the token
    (``# c\\n``); the line end ``_lex`` added is cut off it, and the comment
    on a last line is coloured to the end of the text and no further.
    """
    text, said = _captioned("bash", "echo x # c")
    spans = syntax.block_spans(text, said, ACCENT, None, INKS)
    assert (text.index("# c"), len(text), INKS.comment) == spans[-1]
