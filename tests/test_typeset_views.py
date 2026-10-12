"""
The seam's views, and the rig the pane tests draw with.

The helpers are the rig: ``frame`` draws one frame in a fixed window of a bare
ImGui context (``tests/conftest.py`` has the context and ``seam``, which skips
with the library's own reason where it cannot draw).

**Two kinds of test are here.**

- Tests of the seam on the library as it is. What a view was given to set is
  read back from the seam (``ViewCache.texts``) or the view (``texts``), where it
  put a line from the library (``set_lines``), and what a frame drew from the
  draw list's vertices. Nobody reads a pixel.
- ``xfail(strict=True, reason="P<n>: ...")``: a behaviour the library does not
  give, asked for under that number in ``HANDOFF-advtext.md``. Each body runs on
  the library and fails on the behaviour its request names, so a library that
  starts giving it turns the test red and the mark comes off.
"""

from __future__ import annotations

import os
import random
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import advtext
import pytest
from advtext import BlockKind, Diff, Face, RowKind, registered_fonts, unregister_font

from pptmstr import approval, theme
from pptmstr.model import NodeId, PendingApproval
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Kind, Part, Role, RoleStyle, Slot, gate, roles, themes, views

P7 = (
    "P7: no supported reader options, so HTML, a glob's stars and a link's "
    "destination are not set as written"
)
P9 = "P9: no count of characters set as U+FFFD or borrowed from another face"
P10 = "P10: a run wider than the measure with no space in it is not broken at the measure"
P11 = "P11: an inline code run has no size of its own"
P12 = "P12: code wider than its view cannot be scrolled to and its width is not reported"
P13 = "P13: the lines of a block are not one pitch whatever is on them"
P15 = "P15: no rule under every row of a table"
P16 = "P16: whether a diff's file header fit its view is not answered"
P17 = (
    "P17: every load of the fonts leaves seven more on the atlas, and a family "
    "chosen in a frame is drawn in the next"
)
P19 = "P19: a settled table is not held at its natural widths"
P21 = (
    "P21: monospace text set by the library does not keep its columns, nor line up "
    "with ImGui's in the same face"
)


BOLD = theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf"


WINDOW = (700.0, 600.0)


MARKDOWN = (
    "# Heading\n\nA paragraph with **bold** and `code` and an arrow → in it.\n\n"
    "- one\n- two\n\n```py\nx = 1\n```\n"
)


def frame(im: Any, body: Callable[[], None], *, lead: float = 0.0, sideways: bool = False) -> None:
    """
    One frame with ``body`` drawn in a fixed window, ``lead`` pixels below its top.

    ``sideways`` opens the window with a horizontal scrollbar, as a pane does that
    wants long code lines reachable.
    """
    typeset.pre_frame()
    im.new_frame()
    im.set_next_window_pos(im.ImVec2(10.0, 10.0))
    im.set_next_window_size(im.ImVec2(*WINDOW))
    flags = im.WindowFlags_.horizontal_scrollbar.value if sideways else 0
    im.begin("##typeset-test-sideways" if sideways else "##typeset-test", None, flags)
    if lead:
        im.dummy(im.ImVec2(1.0, lead))
    try:
        body()
    finally:
        im.end()
        im.render()


def pending(tool_name: str, raw_args: dict[str, Any], ident: str = "a1") -> PendingApproval:
    """
    A parked call as the driver builds one: the texts read once, the string from them.
    """
    texts = approval.diff_texts(tool_name, raw_args)
    return PendingApproval(
        id=ident,
        node=NodeId("n1"),
        tool_name=tool_name,
        tool_use_id="tu1",
        raw_args=raw_args,
        summary="",
        requested_at=0.0,
        diff=approval.render_texts(tool_name, texts),
        texts=texts,
    )


def rows(diff: Diff, kind: RowKind) -> list[str]:
    return [
        row.text
        for file in diff.files
        for hunk in file.hunks
        for row in hunk.rows
        if row.kind is kind
    ]


def _bundled(name: str) -> Path:
    return (roles._bundle_fonts() / name).resolve()


@pytest.mark.xfail(strict=True, reason=P11)
def test_a_streamed_code_span_is_the_chromes_height(seam: Any) -> None:
    """
    A stream's blocks are built by the library's stream, from the same theme as
    a settled view's, and its code span is the same 109 px for sixteen
    characters where the chrome's 16 px would lay them out at 122.
    """
    cache = typeset.ViewCache()
    stream = typeset.stream(cache, "s")
    stream.set_text("Set `Settings.typeset` here.\n\nAnd more")
    frame(seam, lambda: stream.draw())
    ((left, _, right, _),) = stream.view.span_rects(0, 4, 20)
    assert right - left == pytest.approx(16 * 7.626, abs=1.0)


TALL_PROSE = (
    "Ångström É (brackets) [and] {braces} | pipes gjpqy `code|[]{}` → ✓ "
    "and enough words after them that the paragraph wraps over several lines of "
    "the window, each of which has to sit one pitch under the last whatever is "
    "set in it, ÅÉ gjpqy ÅÉ gjpqy ÅÉ gjpqy ÅÉ gjpqy ÅÉ gjpqy ÅÉ gjpqy.\n"
)


TALL_TREE = "src\n├── a.py\n│   └── b.py\n└── c.py\n│ ┃ ║ █\nÅÉgjpqy|()[]{}\n"


@pytest.mark.parametrize("name", list(theme.THEMES))
def test_each_role_keeps_the_meaning_the_old_renderers_gave_it(name: str) -> None:
    p = theme.THEMES[name]
    built = typeset.build_palette(p)
    assert built.ink == p.text.u32
    assert built.rule == p.border.u32
    assert built.link == p.accent.u32
    for kind in (BlockKind.HEADING_1, BlockKind.HEADING_3, BlockKind.HEADING_6):
        assert built.ink_for(kind) == p.accent.u32
    assert built.ink_for(BlockKind.QUOTE) == p.text_dim.u32
    assert built.ink_for(BlockKind.CODE) == p.diff_context.u32
    assert built.ink_for(BlockKind.PARAGRAPH) == p.text.u32
    assert (built.diff.add, built.diff.remove) == (p.diff_add.u32, p.diff_remove.u32)
    assert (built.diff.context, built.diff.header) == (p.diff_context.u32, p.accent.u32)


INK_GROUNDS = {
    # ink: the grounds the diff view draws it on. The library's own table
    # (its tests/test_imgui_draw.py, DIFF_INK_GROUNDS), restated: nineteen pairs.
    "gutter": ("page", "add_fill", "remove_fill"),
    "add": ("add_fill", "add_emphasis"),
    "remove": ("remove_fill", "remove_emphasis"),
    "context": ("page",),
    "meta": ("page", "header_fill", "add_fill", "remove_fill"),
    "header": ("header_fill",),
    "warning": (
        "page",
        "header_fill",
        "add_fill",
        "remove_fill",
        "add_emphasis",
        "remove_emphasis",
    ),
}


UNDER_AA = {
    # The pairs under 4.5:1 in the three themes the application requires, on either
    # surface a pane has. Stated so that a palette change which adds to them fails.
    "dark": {
        ("add", "add_emphasis"),
        ("remove", "remove_fill"),
        ("remove", "remove_emphasis"),
        ("context", "page"),
    },
    "light": {
        ("add", "add_emphasis"),
        ("remove", "remove_emphasis"),
        ("header", "header_fill"),
        ("warning", "add_emphasis"),
        ("warning", "remove_emphasis"),
    },
    "high_contrast": set(),
}


def _channels(packed: int) -> tuple[float, float, float]:
    return (float(packed & 255), float(packed >> 8 & 255), float(packed >> 16 & 255))


def _over(top: int, ground: tuple[float, float, float]) -> tuple[float, float, float]:
    alpha = (top >> 24 & 255) / 255
    r, g, b = _channels(top)
    return (
        alpha * r + (1 - alpha) * ground[0],
        alpha * g + (1 - alpha) * ground[1],
        alpha * b + (1 - alpha) * ground[2],
    )


def _contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    def luminance(colour: tuple[float, float, float]) -> float:
        linear = [
            v / 255 / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4
            for v in colour
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def diff_contrasts(name: str, surface: str) -> dict[tuple[str, str], float]:
    """
    Each diff ink of a theme on each ground it is drawn on, over one of the theme's
    surfaces, the bands composited as the view draws them: a band over the surface,
    an emphasis mark over its band.
    """
    p = theme.THEMES[name]
    colours = typeset.build_palette(p).diff
    page = _channels(getattr(p, surface).u32)
    grounds = {
        "page": page,
        "header_fill": _over(colours.header_fill, page),
        "add_fill": _over(colours.add_fill, page),
        "remove_fill": _over(colours.remove_fill, page),
    }
    grounds["add_emphasis"] = _over(colours.add_emphasis, grounds["add_fill"])
    grounds["remove_emphasis"] = _over(colours.remove_emphasis, grounds["remove_fill"])
    return {
        (ink, ground): _contrast(_channels(getattr(colours, ink)), grounds[ground])
        for ink, on in INK_GROUNDS.items()
        for ground in on
    }


def test_the_contrast_arithmetic_is_the_standard_one() -> None:
    assert round(_contrast((0.0, 0.0, 0.0), (255.0, 255.0, 255.0)), 2) == 21.0
    assert round(_contrast((119.0, 119.0, 119.0), (255.0, 255.0, 255.0)), 2) == 4.48
    assert _over(0x80FFFFFF, (0.0, 0.0, 0.0))[0] == pytest.approx(128.0)


@pytest.mark.parametrize("surface", ["panel", "bg"])
@pytest.mark.parametrize("name", theme.REQUIRED_THEMES)
def test_every_diff_ink_is_three_to_one_on_its_ground_and_the_pairs_under_aa_are_named(
    name: str, surface: str
) -> None:
    """
    Nineteen pairs, computed from the palette. The library pins 4.5:1 for its own
    two palettes; inks taken from pptmstr's roles do not inherit that, and the truth
    is 3:1 everywhere with a named few under 4.5 -- ``dark``'s ``diff_remove`` is 4.3 on
    a bare panel before any band is drawn. 3:1 is WCAG's floor for large text and
    for graphics, not its floor for body text, so this pins a number and does not
    claim the word "readable".
    """
    found = diff_contrasts(name, surface)
    assert len(found) == 19
    assert {pair: round(ratio, 2) for pair, ratio in found.items() if ratio < 3.0} == {}
    under = {pair for pair, ratio in found.items() if ratio < 4.5}
    assert under <= UNDER_AA[name], sorted(under - UNDER_AA[name])


@pytest.mark.parametrize("surface", ["panel", "bg"])
@pytest.mark.parametrize("name", list(theme.THEMES))
def test_a_diff_band_costs_a_bounded_share_of_the_themes_own_contrast(
    name: str, surface: str
) -> None:
    """
    The bands are the ink's own colour, translucent, so they pull the ground toward
    the ink. How far is the alpha, and this is the bound it was chosen for, in every
    theme including the discretionary ones whose inks are faint to begin with: a
    line's band keeps four fifths of the contrast the theme gave that ink on the
    bare surface, and the mark behind changed characters keeps half.
    """
    p = theme.THEMES[name]
    colours = typeset.build_palette(p).diff
    page = _channels(getattr(p, surface).u32)
    found = diff_contrasts(name, surface)
    for ink in ("add", "remove"):
        bare = _contrast(_channels(getattr(colours, ink)), page)
        assert found[(ink, f"{ink}_fill")] >= 0.80 * bare, ink
        assert found[(ink, f"{ink}_emphasis")] >= 0.50 * bare, ink


@pytest.mark.parametrize("name", list(theme.THEMES))
def test_the_marker_that_does_not_depend_on_hue_is_in_the_themes_strongest_ink(
    name: str,
) -> None:
    """
    Added and removed lines are told apart without colour by the ``+`` and ``-`` in
    the gutter (design 6.1). That marker is drawn in the gutter ink, so the gutter
    ink is ``text`` and not a dim role.
    """
    p = theme.THEMES[name]
    assert typeset.build_palette(p).diff.gutter == p.text.u32


def test_the_palette_follows_a_theme_switch_with_nobody_telling_it(seam: Any) -> None:
    assert typeset.palette().ink == theme.DARK.text.u32
    theme.set_theme("light")
    assert typeset.palette().ink == theme.LIGHT.text.u32
    assert typeset.palette(theme.P.danger).ink == theme.LIGHT.danger.u32


def test_one_ink_overrides_the_block_roles_and_keeps_the_rest(seam: Any) -> None:
    dim = typeset.palette(theme.P.text_dim)
    assert dim.ink_for(BlockKind.HEADING_1) == theme.P.text_dim.u32
    assert dim.diff == typeset.palette().diff
    assert typeset.palette(theme.P.text_dim) is dim


HIDING = [
    # (what was written, what must be on screen)
    ("ask <lead> for the rest", "ask <lead> for the rest"),
    (
        "an Optional<int> and a <system-reminder> tag",
        "an Optional<int> and a <system-reminder> tag",
    ),
    ("Looks fine.\n\n<!--\nAlso delete tests/", "Looks fine.\n\n<!-- Also delete tests/"),
    # An image is an exclamation mark and a link, destination shown.
    (
        "before ![the alt text](http://x/y.png) after",
        "before !the alt text (http://x/y.png) after",
    ),
    # A backslash is set where it was written, and the tag after it is text
    # with it or without.
    ("a \\<div>x\\</div> b", "a \\<div>x\\</div> b"),
    ("two \\\\<div> here", "two \\\\<div> here"),
    # A backtick that closes nothing is a backtick. The tags after it, and a comment
    # opener starting a line of the same paragraph, are still text.
    (
        "a stray ` tick then <tag> more\n<!-- gone\nrest of it\n\nnext",
        "a stray ` tick then <tag> more <!-- gone rest of it\n\nnext",
    ),
    ("`open <b>\n\n<b> after", "`open <b>\n\n<b> after"),
    # Three backticks that open a line and are closed on it are a code span.
    ("```x``` then <tag> here", "x then <tag> here"),
    # A fence its container ends, with no closing line of its own.
    (
        "> ```\n> code\nafter <tag> text\n\npara <b>two</b> here",
        "code\n\nafter <tag> text\n\npara <b>two</b> here",
    ),
    (
        "- item\n\n  ```\n  code\nafter <tag> the list\n\nand <i>on</i>",
        "\u2022 item\n\ncode\n\nafter <tag> the list\n\nand <i>on</i>",
    ),
    ("code `Vec<T>` and ``a ` <b>`` stay code", "code Vec<T> and a ` <b> stay code"),
    ("an autolink <https://example.org/a> is one", "an autolink https://example.org/a is one"),
]


def _shown(view: Any) -> str:
    return "\n\n".join(view.texts)


def _parts() -> tuple[typeset.Part, ...]:
    return (
        typeset.Part("# Title\n\nSome **prose** here.\n"),
        typeset.Part('{"tool": "Bash",\n  "a_b": **1**}', typeset.Kind.LITERAL, theme.P.accent),
        typeset.Part("boom: it   failed", typeset.Kind.WRAPPED, theme.P.danger),
        typeset.Part("Closing *words*.\n", colour=theme.P.text_dim),
    )


def _inks(seam: Any, draw: Callable[[], object]) -> set[int]:
    """
    Every vertex colour one frame of ``draw`` put on the window's draw list: what
    ``draw`` added, and nothing of the window's own.
    """
    seen: set[int] = set()

    def body() -> None:
        buffer = seam.get_window_draw_list().vtx_buffer
        before = buffer.size()
        draw()
        seen.update(buffer[n].col for n in range(before, buffer.size()))

    frame(seam, body)
    return seen


def test_a_stream_grows_as_it_is_fed_and_settles_when_closed(seam: Any) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    heights: list[float] = []
    for piece in ("# Title\n\nfirst ", "paragraph\n\n", "- item\n- another\n\n```py\nx = 1\n"):
        handle.append(piece)
        frame(seam, lambda: heights.append(handle.draw()))
    assert heights == sorted(heights) and heights[0] < heights[-1]
    assert typeset.stream(cache, "s") is handle
    assert not handle.closed
    handle.close()
    frame(seam, lambda: heights.append(handle.draw()))
    assert handle.closed
    assert cache.view("s") is handle.view and cache.views("s") == (handle.view,)
    assert cache.unbuilt("s") == 0 and handle.unbuilt == 0
    assert cache.height("s") == heights[-1]
    assert cache.texts("s") == ("Title", "first paragraph", "item", "another", "py\nx = 1")
    with pytest.raises(ValueError):
        handle.append("more")


def test_a_stream_can_be_fed_before_it_is_drawn_and_has_no_view_until_then(seam: Any) -> None:
    """
    ``stream()`` asks the library for nothing, so a pane may make one and append
    to it wherever it holds its text; the library's stream and view are made by
    the draw, inside the seam's catch.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("one ")
    handle.append("two\n")
    assert handle.view is None and cache.view("s") is None and cache.texts("s") == ()
    assert handle.text == "one two\n" and not handle.following
    frame(seam, lambda: handle.draw())
    assert cache.texts("s") == ("one two",)


def test_text_that_extends_the_stream_is_appended_and_anything_else_replays(seam: Any) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.set_text("first paragraph\n\nsec")
    frame(seam, lambda: handle.draw())
    view = handle.view
    handle.set_text("first paragraph\n\nsecond one\n")
    frame(seam, lambda: handle.draw())
    assert handle.view is view, "an extension must not cost the selection"
    assert handle.text == "first paragraph\n\nsecond one\n"
    assert cache.texts("s") == ("first paragraph", "second one")

    handle.set_text("something else entirely\n", closed=True)
    frame(seam, lambda: handle.draw())
    assert handle.view is not view
    assert handle.closed
    assert cache.texts("s") == ("something else entirely",)
    assert cache.view("s") is handle.view

    # A closed stream given its own text again with more after it is read again:
    # the library's stream has been told the text ended.
    handle.set_text("something else entirely\n\nand more\n")
    frame(seam, lambda: handle.draw())
    assert not handle.closed
    assert cache.texts("s") == ("something else entirely", "and more")


def test_a_stream_survives_the_face_setting_by_replaying_what_it_was_fed(seam: Any) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("some **bold** text\n")
    handle.close()
    frame(seam, lambda: handle.draw())
    before = handle.view
    typeset.configure(typeset=True, mono=True)
    frame(seam, lambda: typeset.stream(cache, "s").draw())
    assert typeset.stream(cache, "s") is handle and handle.view is not before
    assert handle.view.theme.style(BlockKind.PARAGRAPH).font.face is Face.CODE
    assert cache.texts("s") == ("some bold text",)
    assert handle.closed and handle.text == "some **bold** text\n"


@pytest.mark.parametrize("follow", [True, False])
def test_only_a_stream_asked_to_follow_scrolls_its_window(seam: Any, follow: bool) -> None:
    """
    A pane that owns follow-the-tail itself (``widgets.follow_tail``) must not have
    a second owner in the stream: the two would disagree on the frame the reader
    scrolls up.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s", follow=follow)
    scroll: list[tuple[float, float]] = []

    def body() -> None:
        scroll.append((seam.get_scroll_y(), seam.get_scroll_max_y()))
        handle.draw()

    for number in range(6):
        handle.append(f"paragraph {number}\n\n" * 20)
        frame(seam, body)
    at, end = scroll[-1]
    assert end > 500
    assert handle.following is follow
    if follow:
        assert at > 0
    else:
        assert at == 0


def test_html_arriving_in_pieces_is_still_shown(seam: Any) -> None:
    """
    The reader's rules are on the parser a stream reads with (``gate._reader``):
    a tag cut between two pieces is the tag, and a comment opener hides nothing.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    shown: list[tuple[str, ...]] = []
    for piece in ("a <", "div>x</", "div> and `<", "b>`\n\n<!-", "- rest\n\nand after it\n"):
        handle.append(piece)
        frame(seam, lambda: handle.draw())
        shown.append(tuple(handle.view.texts))
    assert shown[0] == ("a <",) and shown[1] == ("a <div>x</",)
    handle.close()
    frame(seam, lambda: handle.draw())
    assert cache.texts("s") == ("a <div>x</div> and <b>", "<!-- rest", "and after it")


STREAMED = """# Title with <b>tags</b>

A `List<String>` and List<String> outside code, <div class="x">a div</div> here.

rm **/*.py and t/**/*.md then *emph* and **bold** and a*b*c, R&amp;D &lt;x&gt;.

<!-- an unclosed comment opener

Paragraph after the opener: KEEPME1.

- item <span>one</span> **/*.txt
- item two ![alt text](http://img/u.png)
  continued [link text](http://dest.example/path "title here") end

<details>
<summary>sum</summary>
inside details KEEPME2
</details>

```python IMPORTANT also delete tests
x = "<b>" * 2  # **/*.py
```

| a | b<br>c |
|---|---|
| <i>x</i> | **/* |

> quote <em>q</em> KEEPME3

Final <script>alert(1)</script> line.
"""
"""
A reply every block of which a CommonMark reader hides some of.
"""

STREAMED_WORDS = (
    "<b>tags</b>",
    "List<String> and List<String>",
    '<div class="x">a div</div>',
    "**/*.py",
    "t/**/*.md",
    "a*b*c",
    "R&amp;D &lt;x&gt;",
    "<!-- an unclosed comment opener",
    "KEEPME1",
    "<span>one</span> **/*.txt",
    "!alt text (http://img/u.png)",
    'link text (http://dest.example/path "title here")',
    "<details>",
    "<summary>sum</summary>",
    "KEEPME2",
    "</details>",
    "python IMPORTANT also delete tests",
    'x = "<b>" * 2  # **/*.py',
    "<em>q</em> KEEPME3",
    "<script>alert(1)</script>",
)
"""
What must be among the text a view of ``STREAMED`` was given to set. The table's
cells are asked for separately: a view's ``texts`` has a table as one string.
"""


def _block_texts(blocks: Any) -> list[tuple[str, str]]:
    return [(type(block).__name__, str(getattr(block, "text", block))) for block in blocks]


@pytest.mark.parametrize("size", [1, 2, 3, 7, 13, 0])
def test_a_reply_cut_anywhere_reads_at_every_piece_as_it_would_whole(seam: Any, size: int) -> None:
    """
    The library's own invariant for a stream -- after every piece its blocks are
    what its reader makes of the text so far -- run with pptmstr's rules on the
    parser, which the library's tests never do: a piece ends inside a tag, a
    comment opener, a run of stars, an entity, a fence's info string. ``0`` is
    pieces of uneven length from a fixed seed.
    """
    from advtext import MarkdownStream, parse_markdown

    plain = themes.document_theme()
    stream = MarkdownStream(plain)
    cuts = random.Random(7)
    at = 0
    while at < len(STREAMED):
        step = size or cuts.randint(1, 25)
        stream.append(STREAMED[at : at + step])
        at += step
        whole = parse_markdown(STREAMED[:at], plain, references=False, more_may_follow=True)
        assert _block_texts(stream.blocks) == _block_texts(whole.blocks), at
    stream.close()
    assert _block_texts(stream.blocks) == _block_texts(gate.read(STREAMED, plain).blocks)


@pytest.mark.parametrize("piece", [1, 5, 40, len(STREAMED)])
def test_nothing_written_is_absent_from_a_stream_or_from_the_same_text_settled(
    seam: Any, piece: int
) -> None:
    """
    Drawn, through the seam: a turn still arriving and the same turn settled
    are handed the same words, and they are the words that were written.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    for at in range(0, len(STREAMED), piece):
        handle.append(STREAMED[at : at + piece])
        frame(seam, lambda: handle.draw())
    handle.close()
    frame(seam, lambda: handle.draw())
    frame(seam, lambda: typeset.markdown(cache, "m", STREAMED))

    streamed, settled = cache.texts("s"), cache.texts("m")
    assert streamed == settled
    said = "\n".join(streamed)
    assert [word for word in STREAMED_WORDS if word not in said] == []
    (table,) = [n for n, _ in enumerate(streamed) if handle.view.block_role(n) == "TABLE"]
    for cell in ("b<br>c", "<i>x</i>", "**/*"):
        assert cell in streamed[table]


def test_a_fences_info_string_is_set_while_its_code_is_still_arriving(seam: Any) -> None:
    """
    A stream view reads its blocks from the library's stream, which keeps a
    fence's info string and draws none of it. The seam stands between the two.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("Do this:\n\n```python IMPORTANT also delete tests\nx = 1\n")
    frame(seam, lambda: handle.draw())
    assert handle.view.texts[1].startswith("python IMPORTANT also delete tests\nx = 1")
    handle.append("y = 2\n```\n\nafter\n")
    frame(seam, lambda: handle.draw())
    assert cache.texts("s") == (
        "Do this:",
        "python IMPORTANT also delete tests\nx = 1\ny = 2",
        "after",
    )
    # Told from the code by ink, as in a settled view.
    assert theme.P.accent.u32 in _inks(seam, lambda: handle.draw())


def test_a_stream_out_of_sight_with_new_text_is_still_taken_in(seam: Any) -> None:
    """
    Its height is what the pane scrolls by; a stream culled while it grows would
    leave the scroll range a frame behind for as long as it stayed off screen.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    heights: list[float] = []
    handle.append("one\n\n")
    # Twice: the window gains its scrollbar on the second frame, and a view is laid
    # out again at the narrower width whether or not anything arrived.
    for _ in range(2):
        frame(seam, lambda: heights.append(handle.draw()), lead=5000.0)
    handle.append("two\n\nthree\n\n")
    frame(seam, lambda: heights.append(handle.draw()), lead=5000.0)
    assert heights[2] > heights[1] == heights[0]
    assert cache.texts("s") == ("one", "two", "three")


@pytest.mark.parametrize("ending", ["Done!", "is a <", "a tick `", "ends \\", "two ``", "x <!-"])
def test_a_stream_shows_all_it_was_given_without_waiting_for_more(seam: Any, ending: str) -> None:
    """
    A stream that pauses -- the model is thinking, the tool is running -- must not be
    showing less than arrived. Each ending is one a reader that has to look ahead
    would hold back until the next piece. Read in the draw after it arrived:
    nothing paces a stream.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append(f"Some words and then {ending}")
    frame(seam, lambda: handle.draw())
    assert _shown(handle.view) == f"Some words and then {ending}"


def test_a_stream_the_library_stops_under_says_so_and_reports_its_text_missing(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("one\n\ntwo\n\nthree\n")
    frame(seam, lambda: handle.draw())
    assert cache.unbuilt("s") == 0

    def stopped(**given: Any) -> bool:
        raise advtext.TypesetUnavailable("imtypeset stopped: the library said no")

    monkeypatch.setattr(handle.view, "draw", stopped)
    said = _said(monkeypatch)
    tall: list[float] = []
    frame(seam, lambda: tall.append(handle.draw()))
    assert said == ["typeset text stopped: imtypeset stopped: the library said no"]
    assert tall[0] > 0.0
    assert cache.unbuilt("s") == handle.unbuilt == 6
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD), "a load is the way back"


FRAGMENTS = (
    "plain words ",
    "a <tag> here ",
    "<div>block</div>\n",
    "\\<esc> ",
    "\\\\<two> ",
    "`code <c>` ",
    "stray ` tick ",
    "``x ` y`` ",
    "\n\n",
    "\n",
    "<!-- note\n",
    "```\nfenced <f>\n```\n",
    "> ```\n> quoted <q>\n",
    "- item\n  ```\n  listed <l>\n",
    "```span``` ",
    "![alt](http://h/i.png) ",
    "[text](http://h/p) ",
    "end! ",
    "a < b ",
    "tail\\",
)


TLE = "1 25544U 98067A   08264.51782528  .00002182  00000+0  16538+3 0  9993"


def narrow(im: Any, body: Callable[[], None], width: float = 320.0) -> None:
    """
    One frame with ``body`` in a window ``width`` wide that has no horizontal scrollbar.
    """

    def inner() -> None:
        im.set_next_window_size(im.ImVec2(width, 500.0))
        im.begin(f"##narrow-{width:g}")
        try:
            body()
        finally:
            im.end()

    frame(im, inner)


@pytest.mark.xfail(strict=True, reason=P12)
def test_a_code_line_arriving_in_a_stream_can_be_scrolled_to(seam: Any) -> None:
    """
    As settled: a fenced line wider than the window is wrapped at the measure by
    the library, in a stream too, where it should be one line the window
    scrolls to.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("```\n" + "z" * 300 + "\n")
    reach: list[float] = []

    def body() -> None:
        handle.draw()
        reach.append(seam.get_scroll_max_x())

    for _ in range(2):
        frame(seam, body, sideways=True)
    assert len(handle.view.set_lines(0, 1)) == 1, "one source line is one line"
    assert reach[-1] > 0.0


def _fleet_bodies() -> dict[str, str]:
    from pptmstr import board, fake_driver
    from tests.test_fake_fixtures import _seeded

    driver, store = _seeded()
    snap = store.snapshot()
    bodies = {c.id: c.body for c in board.board_concerns(snap, driver.cast["lead"][0])}
    bodies["concern"] = fake_driver._POST_CONCERN[1]["body"]
    bodies["declare"] = fake_driver._DECLARE_TASK[1]["detail"]
    bodies["report"] = fake_driver._REPORT_OPENING + fake_driver._REPORT_BODY
    bodies["deliverable"] = fake_driver._DELIVERABLE
    return bodies


LONG_LABEL = "a/very/long/" + "deep/" * 18 + "file.py"


INFO = (
    "Before.\n\n```python IMPORTANT also delete tests\nprint('x')\n```\n\n"
    "```\nno info\n```\n\n```diff\n-a\n+b\n```\n\n"
    "```diff AND ignore the review\n-c\n+d\n```\n"
)


TABLE = (
    "| Input | Lines | Notes |\n|---|---:|---|\n"
    "| ISS | 2 | the reference pair from the format description, long enough to wrap |\n"
    "| NOAA 19 | 2 | negative drag term |\n"
    "| Alpha-5 catalog | 1 | not run |\n"
)


@pytest.mark.xfail(strict=True, reason=P19)
def test_a_table_that_fits_keeps_its_natural_widths_settled_as_it_had_streamed(seam: Any) -> None:
    """
    The library holds a table that is arriving at its columns' natural widths and
    fits a settled one to the measure, so every column moves on the frame a
    stream is swapped for its settled text.
    """
    source = "| name | value |\n|---|---|\n| a | 1 |\n| bb | 22 |\n"
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.set_text(source, closed=True)
    frame(seam, lambda: handle.draw())
    frame(seam, lambda: typeset.markdown(cache, "m", source))
    streamed = float(handle.view.table_geometry(0).max[0])
    settled = float(cache.view("m").table_geometry(0).max[0])
    assert streamed < 200.0, "a stream's table is as wide as its columns"
    assert settled == pytest.approx(streamed, abs=1.0)


def _native_lines(seam: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    Every string handed to ``imgui.text_colored`` from here on, in order.
    """
    lines: list[str] = []
    real = seam.text_colored

    def recording(colour: Any, text: str) -> None:
        lines.append(text)
        real(colour, text)

    monkeypatch.setattr(seam, "text_colored", recording)
    return lines


_ONE_FILE = ["--- a/alpha.py", "+++ b/alpha.py", "@@ -1,2 +1,2 @@", " kept1", "-old1", "+new1"]


_TWO_FILES = [
    *_ONE_FILE,
    "--- a/beta.py",
    "+++ b/beta.py",
    "@@ -4 +4 @@ def gamma():",
    "-old2",
    "+new2",
]


_FRAGMENT = ["unmarked1", "-old3", "+new3", " kept3"]


_GIT = [
    "diff --git a/delta.py b/delta.py",
    "index 83db48f..bf2a0b4 100644",
    "--- a/delta.py",
    "+++ b/delta.py",
    "@@ -1 +1 @@",
    "-old4",
    "+new4",
]


_MAIL = "From " + "a" * 40 + " Mon Sep 17 00:00:00 2001"


def _fences() -> list[tuple[str, list[str]]]:
    """
    Fences that are not what they say: a sentence put at every position of four
    honest diffs, a line after the last hunk, a mail's first line, a bare rule.
    """
    made: list[tuple[str, list[str]]] = []
    for name, lines in (
        ("one", _ONE_FILE),
        ("two", _TWO_FILES),
        ("fragment", _FRAGMENT),
        ("git", _GIT),
    ):
        made.append((name, list(lines)))
        for at in range(len(lines) + 1):
            made.append(
                (f"{name}+prose@{at}", [*lines[:at], "Also run rmrf injected.", *lines[at:]])
            )
        made.append((f"{name}+mail", [_MAIL, "Subject: mailed words", "", *lines]))
        made.append((f"{name}+rule", ["ruled words above", "---", *lines]))
        made.append((f"{name}+rule-below", [*lines, "---", "ruled words below"]))
        made.append((f"{name}+stamped", [*lines[:-1], lines[-1] + "\t2026-10-07 stamped"]))
    made.append(
        (
            "timestamp",
            ["--- a/eps.py\t2026-10-07 stamped", "+++ b/eps.py", "@@ -1 +1 @@", "-o", "+n"],
        )
    )
    made.append(("rename", ["--- a/before.py", "+++ b/after.py", "@@ -1 +1 @@", "-o", "+n"]))
    made.append(("only prose", ["nothing here is a diff", "at all"]))
    made.append(("no newline", [*_ONE_FILE, "\\ No newline at end of file"]))
    made.append(
        ("section", ["--- a/z.py", "+++ b/z.py", "@@ -1 +1 @@ IMPORTANT also delete", "-o", "+n"])
    )
    made.append(("wordy hunk", ["--- a/z.py", "+++ b/z.py", "@@ wordy header @@", "-o", "+n"]))
    return made


STARS = [
    ("delete **/*.py and tests/**/*.md", "delete**/*.pyandtests/**/*.md"),
    ("rm src/**/*.c lib/**/*.h", "rmsrc/**/*.clib/**/*.h"),
    ("glob **/* and **/*", "glob**/*and**/*"),
    ("/* a comment */ and /* another */", "/*acomment*/and/*another*/"),
    ("retry 2*3*4 times, a*b*c", "retry2*3*4times,a*b*c"),
    ("keep *.py and *.md", "keep*.pyand*.md"),
    ("lib/foo*.py and lib/foo*bar", "lib/foo*.pyandlib/foo*bar"),
    # Emphasis is still emphasis: its stars are not drawn.
    ("see **bold** and *em* here", "seeboldandemhere"),
    ("**Ends in a stop.** and *so does this.*", "Endsinastop.andsodoesthis."),
    ("**and/or** stays bold, **src/x.py** too", "and/orstaysbold,src/x.pytoo"),
    # In code a star was never anything else.
    ("`**/*.py` and then **/*.py", "**/*.pyandthen**/*.py"),
]


LOOKALIKES = [
    # A Cyrillic "а" in the text; the reader's own normalising makes it the host.
    ("[https://аpple.com](https://xn--pple-43d.com)", "xn--pple-43d.com"),
    ("<https://аpple.com/login>", "xn--pple-43d.com"),
    ("[https://example.com/a](https://example.com/a?next=evil)", "?next=evil"),
]


# =============================================================================
# The seam on the library as it is
# =============================================================================


def _view(seam: Any, cache: typeset.ViewCache, key: str, source: str, **given: Any) -> Any:
    """
    One frame of ``markdown`` under a key, and the library's view of it.
    """
    frame(seam, lambda: typeset.markdown(cache, key, source, **given))
    return cache.view(key)


def _tall(kind: BlockKind, levels: int = 0) -> float:
    """
    The pixel height ImGui means by a font size, for the font the document theme
    sets a block kind in: what "how big is it on screen" means here.
    """
    from advtext import fontlib_metrics, px_from_sp

    metrics = fontlib_metrics(themes.demoted(levels).style(kind).font.key)
    return float(px_from_sp(round(metrics.ascent + metrics.descent)))


def _steps(view: Any, block: int = 0) -> set[float]:
    """
    The distances between the baselines of a block's lines as the library set them.
    """
    lines = view.set_lines(block, block + 1)
    assert len(lines) > 3
    return {round(b.baseline - a.baseline, 2) for a, b in zip(lines, lines[1:], strict=False)}


def _squeezed(view: Any) -> str:
    """
    What a view was given to set, block after block, spaces taken out.
    """
    return "".join("".join(view.texts).split())


def _marked(view: Any) -> str:
    """
    What a view was given to set, a list item with its marker, a blank line
    between blocks.
    """
    return "\n\n".join(
        f"{getattr(block, 'marker', '')} {text}".strip()
        for block, text in zip(view.blocks, view.texts, strict=True)
    )


def _said(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    Every line the seam says on screen in place of text, from here on.
    """
    said: list[str] = []
    real = views._said
    monkeypatch.setattr(
        views, "_said", lambda im, text, ink=None: (said.append(text), real(im, text, ink))[1]
    )
    return said


# -- faces and sizes -----------------------------------------------------------


def test_the_bold_monospace_is_registered_and_used(seam: Any) -> None:
    assert registered_fonts()[Face.CODE_BOLD] == BOLD.resolve()
    assert themes.document_theme().faces.code_bold is Face.CODE_BOLD
    assert typeset.imfont(typeset.Weight.CODE_BOLD).get_debug_name() == BOLD.name


def test_without_a_bold_monospace_bold_keeps_the_columns(seam: Any, tmp_path: Path) -> None:
    """
    The library's stand-in for a missing bold monospace is the bold *proportional*
    file, which breaks every column under a bold run.
    """
    unregister_font(Face.CODE_BOLD)
    try:
        assert typeset.load_fonts(tmp_path / "not-there.ttf")
        assert themes.document_theme().faces.code_bold is Face.CODE
        bold, regular = (
            typeset.imfont(weight).get_debug_name()
            for weight in (typeset.Weight.CODE_BOLD, typeset.Weight.CODE)
        )
        assert bold == regular == "Inconsolata-Medium.ttf"
    finally:
        assert typeset.load_fonts(BOLD)


def test_an_arrow_in_prose_is_drawn_from_a_face_that_has_one(seam: Any) -> None:
    """
    Roboto has no arrow and no tick. The library borrows them, and says the text
    was set whole: thirteen glyphs reached the draw list, four vertices each.
    """
    view = _view(seam, typeset.ViewCache(), "k", "before → after ✓")
    assert view.stats.whole and view.stats.vertices == 4 * 13


def test_running_text_and_fenced_code_are_the_ui_fonts_sixteen_pixels(seam: Any) -> None:
    """
    Sized in ems of a Roboto body a fence would be about 14 px; it has its own
    role's size so that it reads at the size of every other piece of code.
    """
    theme_ = themes.document_theme()
    assert theme_.style(BlockKind.PARAGRAPH).font.face is Face.PROSE
    assert _tall(BlockKind.PARAGRAPH) == pytest.approx(typeset.BODY_PX, abs=0.01)
    assert _tall(BlockKind.CODE) == pytest.approx(16.0, abs=0.01)
    assert theme_.style(BlockKind.CODE).font.size > theme_.style(BlockKind.PARAGRAPH).font.size


def test_the_text_follows_the_ui_fonts_size_and_documents_are_rebuilt_for_it(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``hello_imgui`` scales the UI font for the display and the library draws at the
    size its theme names, so the theme is sized from what ImGui reports between
    frames. A document built at the old size holds the old advances: it is stale.
    """
    cache = typeset.ViewCache()
    first = _view(seam, cache, "k", MARKDOWN)
    assert typeset.body_px() == 16.0, "read from the context's own 16 px default font"
    epoch, tall = typeset._STATE.epoch, cache.height("k")

    assert _view(seam, cache, "k", MARKDOWN) is first and typeset._STATE.epoch == epoch

    monkeypatch.setattr(gate, "_live_font_size", lambda: 24.0)
    assert _view(seam, cache, "k", MARKDOWN) is not first
    assert typeset.body_px() == 24.0
    assert _tall(BlockKind.PARAGRAPH) == pytest.approx(24.0, abs=0.01)
    assert (cache.height("k") or 0.0) > 1.3 * (tall or 0.0)

    monkeypatch.undo()
    frame(seam, lambda: typeset.markdown(cache, "k", MARKDOWN))
    assert typeset.body_px() == 16.0


def test_the_operators_scale_multiplies_the_ui_fonts_size_to_a_whole_pixel(seam: Any) -> None:
    """
    A multiplier and not an override: the text still follows the display scale
    ``hello_imgui`` gives the UI font. ImGui bakes whole pixels, so the product is one.
    """
    cache = typeset.ViewCache()
    first = _view(seam, cache, "k", MARKDOWN)
    tall = cache.height("k") or 0.0

    typeset.configure(typeset=True, scale=1.125)
    assert typeset.body_px() == 18.0
    assert _view(seam, cache, "k", MARKDOWN) is not first
    assert _tall(BlockKind.PARAGRAPH) == pytest.approx(18.0, abs=0.01)
    assert (cache.height("k") or 0.0) > tall


def test_the_monospace_setting_puts_every_role_in_inconsolata(seam: Any) -> None:
    typeset.configure(typeset=True, mono=True)
    faces = {style.font.face for style in themes.document_theme().blocks.values()}
    assert faces <= {Face.CODE, Face.CODE_BOLD}
    assert all(typeset.style(role).slot is Slot.MONOSPACE for role in Role)


def test_in_monospace_prose_a_code_span_is_the_blocks_own_font(seam: Any) -> None:
    from advtext import Emphasis

    typeset.configure(typeset=True, roles={Role.PROSE: RoleStyle(Slot.MONOSPACE)})
    theme_ = themes.document_theme()
    assert theme_.style(BlockKind.PARAGRAPH).font.face is Face.CODE
    assert theme_.face_for(BlockKind.PARAGRAPH, Emphasis(code=True)) is Face.CODE
    assert theme_.face_for(BlockKind.PARAGRAPH, Emphasis(bold=True)) is Face.CODE_BOLD


def test_text_is_ragged_and_headings_are_a_panes_scale(seam: Any) -> None:
    from advtext import Align

    assert themes.document_theme().style(BlockKind.PARAGRAPH).align is Align.LEFT
    heights = [
        round(_tall(kind))
        for kind in (BlockKind.HEADING_1, BlockKind.HEADING_2, BlockKind.HEADING_3)
    ]
    assert heights == [22, 19, 17]


def test_a_role_takes_its_own_slot_and_size_inside_one_document(seam: Any) -> None:
    """
    Two families are live at once and a role chooses between them: a monospace
    heading over proportional prose, in one view.
    """
    typeset.configure(
        typeset=True,
        roles={
            Role.HEADING: RoleStyle(Slot.MONOSPACE, 1),
            Role.TABLE: RoleStyle(Slot.MONOSPACE, -1),
            Role.CODE_BLOCK: RoleStyle(Slot.MONOSPACE, 2),
        },
    )
    theme_ = themes.document_theme()
    assert theme_.style(BlockKind.PARAGRAPH).font.face is Face.PROSE
    assert theme_.style(BlockKind.HEADING_1).font.face is Face.CODE_BOLD
    assert theme_.style(BlockKind.HEADING_6).font.face is Face.CODE
    assert theme_.style(BlockKind.TABLE).font.face is Face.CODE
    assert round(_tall(BlockKind.HEADING_1)) == round(17 * 22 / 16)
    assert _tall(BlockKind.TABLE) == pytest.approx(15.0, abs=0.01)
    assert _tall(BlockKind.CODE) == pytest.approx(18.0, abs=0.01)
    assert _tall(BlockKind.PARAGRAPH) == pytest.approx(16.0, abs=0.01)

    source = (
        "# Title\n\nBody **bold** `code`.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\nx = 1\n```\n"
    )
    view = _view(seam, typeset.ViewCache(), "k", source)
    assert view.stats.whole and view.stats.vertices > 4 * 20
    # On screen and not only in the theme: the heading's line is the tallest.
    title, body = view.set_lines(0, 2)
    assert title.bottom - title.top > body.bottom - body.top


def test_a_literal_part_is_drawn_at_the_size_of_the_role_it_names(seam: Any) -> None:
    """
    An argument and a tool's output are both literal text and are sized apart.
    ImGui draws both, a line of each as tall as its font.
    """
    typeset.configure(typeset=True, roles={Role.ARGUMENT: RoleStyle(Slot.MONOSPACE, 2)})
    cache = typeset.ViewCache()

    def body() -> None:
        typeset.plain(cache, "output", "output\nlines")
        typeset.plain(cache, "value", "value\nlines", role=Role.ARGUMENT)

    frame(seam, body)
    assert (cache.height("output"), cache.height("value")) == (2 * 16.0, 2 * 18.0)
    assert typeset.line_height() == 16.0 and typeset.line_height(role=Role.ARGUMENT) == 18.0
    assert cache.views("output") == () and cache.views("value") == ()


def test_leading_is_a_table_of_whole_pixel_pitches_by_role(seam: Any) -> None:
    """
    The table is the one place line spacing is decided, in multiples of a role's
    pixel height; what a line is set at is that product taken to a whole pixel.
    Read back from where the library put the lines of plain prose.
    """
    words = "plain words " * 60
    assert typeset.LEADING[Role.PROSE] == 17 / 16
    assert typeset.pitch(Role.PROSE) == typeset.line_height(mono=False) == 17.0
    assert typeset.pitch(Role.HEADING, 22.0) == 22.0
    cache = typeset.ViewCache()
    assert _steps(_view(seam, cache, "k", words, width=300.0)) == {17.0}

    typeset.configure(typeset=True, scale=0.875)
    assert typeset.pitch(Role.PROSE) == 15.0, "14 x 17/16 is 14.875"
    assert _steps(_view(seam, cache, "k", words, width=300.0)) == {15.0}


def test_a_documents_theme_is_built_when_the_fonts_are_loaded_and_not_in_a_frame(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A theme sizes each face from its font file, and the library's first reading of
    a file is tens of milliseconds a face: a quarter of a second for a document's
    theme, which in the first frame that sets text is a frame nobody sees move.
    So the load builds it, where the operator's setting asks for typeset text.
    """
    built: list[int] = []
    real = themes._build
    monkeypatch.setattr(themes, "_build", lambda: (built.append(1), real())[1])

    assert typeset.load_fonts(BOLD)
    assert built == [1]
    frame(seam, lambda: typeset.markdown(typeset.ViewCache(), "k", MARKDOWN))
    assert built == [1], "the frame found it built"

    typeset.configure(typeset=False)
    assert typeset.load_fonts(BOLD)
    assert built == [1], "nobody asked for typeset text, so nothing is read for it"


def test_choosing_what_is_already_in_force_rebuilds_nothing(seam: Any) -> None:
    cache = typeset.ViewCache()
    first = _view(seam, cache, "k", MARKDOWN)
    epoch = typeset._STATE.epoch
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0)
    typeset.configure(typeset=True, proportional=typeset.DEFAULT_PROPORTIONAL, roles={})
    assert typeset._STATE.epoch == epoch
    assert _view(seam, cache, "k", MARKDOWN) is first


# -- a native line in a typeset face -------------------------------------------

FACE_FILES = {
    "PROSE": "Roboto-Regular.ttf",
    "PROSE_BOLD": "Roboto-Bold.ttf",
    "PROSE_ITALIC": "Roboto-RegularItalic.ttf",
    "PROSE_BOLD_ITALIC": "Roboto-BoldItalic.ttf",
    "CODE": "Inconsolata-Medium.ttf",
    "CODE_BOLD": "Inconsolata-Bold.ttf",
}


def test_the_librarys_font_for_a_face_is_the_atlas_entry_of_that_faces_file(
    seam: Any,
) -> None:
    """
    ``gate.face_font`` is the library's own answer (``typesetter.font``): each
    face answers the atlas entry of its own file, and text ImGui measures in
    the library's monospace entry is as wide as in the chrome's own font.
    """
    found = {name: typeset.face_font(name) for name in FACE_FILES}
    assert {name: font and font.get_debug_name() for name, font in found.items()} == FACE_FILES
    widths: list[float] = []

    def body() -> None:
        widths.append(seam.calc_text_size("columns 0123 (wide)").x)
        seam.push_font(found["CODE"], 16.0)
        widths.append(seam.calc_text_size("columns 0123 (wide)").x)
        seam.pop_font()

    frame(seam, body)
    assert widths[0] == widths[1] == 19 * 8.0

    typeset.unload()
    assert typeset.face_font("CODE") is None, "nothing to answer with once the library is down"


def test_a_native_line_in_a_typeset_face_is_the_texts_size(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A header drawn with ``font()`` beside typeset text has to scale with it.
    """
    sizes: list[float] = []

    def body() -> None:
        with typeset.font(typeset.Weight.BOLD):
            sizes.append(seam.get_font_size())
        with typeset.font(typeset.Weight.BOLD, 12.0):
            sizes.append(seam.get_font_size())

    monkeypatch.setattr(gate, "_live_font_size", lambda: 24.0)
    frame(seam, body)
    assert sizes == [24.0, 12.0]
    monkeypatch.undo()
    frame(seam, lambda: None)
    assert typeset.body_px() == 16.0


def test_native_lines_follow_the_label_role(seam: Any) -> None:
    def named(weight: typeset.Weight) -> str:
        return str(typeset.imfont(weight).get_debug_name())

    assert named(typeset.Weight.REGULAR) == FACE_FILES["PROSE"]
    assert named(typeset.Weight.BOLD) == FACE_FILES["PROSE_BOLD"]
    typeset.configure(typeset=True, roles={Role.LABEL: RoleStyle(Slot.MONOSPACE, 2)})
    assert not typeset.mono(), "prose is another role"
    assert named(typeset.Weight.REGULAR) == FACE_FILES["CODE"]
    assert named(typeset.Weight.BOLD) == FACE_FILES["CODE_BOLD"]
    sizes: list[float] = []

    def body() -> None:
        with typeset.font(typeset.Weight.REGULAR):
            sizes.append(seam.get_font_size())
        with typeset.font(typeset.Weight.REGULAR, 12.0):
            sizes.append(seam.get_font_size())

    frame(seam, body)
    assert sizes == [18.0, 12.0]


# -- what the library does not give, by request number --------------------------


def _steps_drawn(seam: Any, source: str) -> list[float]:
    """
    The distances between the left edges of the glyphs one frame of ``source``
    drew, in the order across the line: read from the draw list, four vertices a
    glyph. For a run of one character that is the step from each to the next.
    """
    lefts: list[float] = []
    cache = typeset.ViewCache()

    def body() -> None:
        buffer = seam.get_window_draw_list().vtx_buffer
        before = buffer.size()
        typeset.markdown(cache, "k", source)
        lefts.extend(sorted({buffer[n].pos.x for n in range(before, buffer.size(), 4)}))

    frame(seam, body)
    return [b - a for a, b in zip(lefts, lefts[1:], strict=False)]


@pytest.mark.xfail(strict=True, reason=P21)
def test_a_fenced_row_keeps_its_columns_at_the_advance_imgui_draws_the_face_at(seam: Any) -> None:
    """
    Requirements R2, R4 and R5 of ``HANDOFF-advtext.md`` (A2): forty of one
    character in a fence are forty glyphs one step apart, and the step is the
    advance ImGui has baked for the face the library drew them from, which is
    the chrome's.
    """
    baked = typeset.face_font("CODE").get_font_baked(16.0).get_char_advance(ord("m"))
    assert baked == 8.0
    steps = _steps_drawn(seam, "```\n" + "m" * 40 + "\n```\n")
    assert len(steps) == 39
    assert set(steps) == {baked}


@pytest.mark.xfail(strict=True, reason=P21)
def test_a_family_with_its_own_file_is_drawn_at_the_advances_imgui_has_for_that_file(
    seam: Any,
) -> None:
    """
    Requirement R2 for a proportional face, in the family that brings a file of
    its own: the step between two glyphs is the advance ImGui has baked for the
    file they were drawn from. The file is the family's -- that half holds -- and
    the steps are the library's own, of two values.
    """
    typeset.configure(typeset=True, proportional="droid")
    frame(seam, lambda: None)
    font = typeset.face_font("PROSE")
    assert font.get_debug_name() == "DroidSans.ttf"
    baked = font.get_font_baked(16.0).get_char_advance(ord("n"))
    steps = _steps_drawn(seam, "n" * 40)
    assert len(steps) == 39
    assert set(steps) == {baked}


@pytest.mark.xfail(strict=True, reason=P11)
def test_inline_code_is_the_chromes_height_inside_prose_of_the_same_height(seam: Any) -> None:
    """
    A theme sizes a code span by its block, and at one em Inconsolata is shorter
    than Roboto: 14.3 px beside 16. Read from where the library put the span:
    sixteen characters of Inconsolata at the chrome's 16 px are 122 px laid
    out, and at the paragraph's em they are 109.
    """
    view = _view(seam, typeset.ViewCache(), "k", "Set `Settings.typeset` here.")
    ((left, _, right, _),) = view.span_rects(0, 4, 20)
    assert right - left == pytest.approx(16 * 7.626, abs=1.0)


@pytest.mark.xfail(strict=True, reason=P11)
def test_the_inline_code_roles_size_reaches_a_code_span(seam: Any) -> None:
    """
    The operator's size for inline code is a setting with a row in the dialog.
    """
    cache = typeset.ViewCache()
    source = "Set `Settings.typeset` here."

    def wide() -> float:
        ((left, _, right, _),) = _view(seam, cache, "k", source).span_rects(0, 4, 20)
        return float(right - left)

    before = wide()
    typeset.configure(typeset=True, roles={Role.INLINE_CODE: RoleStyle(Slot.MONOSPACE, 4)})
    assert wide() > before


@pytest.mark.xfail(strict=True, reason=P13)
@pytest.mark.parametrize("multiple", [17 / 16, 1.0, 1.35])
def test_every_line_of_a_block_is_one_pitch_under_the_last_whatever_is_in_it(
    seam: Any, monkeypatch: pytest.MonkeyPatch, multiple: float
) -> None:
    """
    The hazard of a tight pitch: a line is moved down when its ink would touch
    the line above, so an accented capital, a bracket or a box-drawing glyph makes
    its own line taller than its neighbours -- a ragged rhythm in prose, and a
    ``tree`` listing whose rules do not join.
    """
    for role in (Role.PROSE, Role.CODE_BLOCK):
        monkeypatch.setitem(typeset.LEADING, role, multiple)
    roles.moved()
    try:
        want = float(round(16 * multiple))
        cache = typeset.ViewCache()
        prose = _view(seam, cache, "prose", TALL_PROSE, width=300.0)
        fence = _view(seam, cache, "fence", "```\n" + TALL_TREE + "```\n")
        assert (_steps(prose), _steps(fence)) == ({want}, {want})
    finally:
        # The table is put back before the themes built from it are let go.
        monkeypatch.undo()
        roles.moved()


@pytest.mark.xfail(strict=True, reason=P13)
def test_a_line_of_box_drawing_is_the_height_of_any_other_literal_line(seam: Any) -> None:
    """
    Inconsolata's box-drawing glyphs reach past the face's ascent and descent so
    as to meet the line above and below. Measured as drawn they make those lines
    taller, which is what stops them meeting.
    """
    view = _view(seam, typeset.ViewCache(), "k", "```\n" + TALL_TREE + "```\n")
    assert len({round(line.bottom - line.top, 2) for line in view.set_lines(0, 1)}) == 1


@pytest.mark.xfail(strict=True, reason=P13)
def test_a_capped_view_ends_on_a_whole_line(seam: Any) -> None:
    """
    A pane says "at most six lines" as six times ``line_height``. Where every
    line of a paragraph is one pitch that is the foot of its sixth line; where a
    line is taller for what is on it the cap falls through a row of glyphs.
    """
    cache = typeset.ViewCache()
    cap = 6 * typeset.line_height(mono=False)
    frame(seam, lambda: typeset.markdown(cache, "k", TALL_PROSE, width=300.0, height_cap=cap))
    feet = [round(line.bottom) for line in cache.view("k").set_lines(0, 1)]
    assert len(feet) > 6 and cap in feet


@pytest.mark.xfail(strict=True, reason=P10)
def test_what_cannot_be_broken_at_a_space_is_broken_at_the_windows_edge(seam: Any) -> None:
    """
    A word is never broken by the library, and neither is a code span. In a
    window that does not scroll sideways everything past its edge is text nobody
    can reach: a satellite's element set in a concern body, the end of a path.
    """
    source = f"see {LONG_LABEL} and `{'y' * 150}` done"
    view = _view(seam, typeset.ViewCache(), "k", source, width=300.0)
    assert max(line.right for line in view.set_lines()) <= 300.0


def test_a_heading_of_wide_characters_that_overhangs_can_be_scrolled_to(seam: Any) -> None:
    """
    A block is asked how far right it reaches only if a word of it could
    overhang, judged by its length in characters, so the judgement has to hold
    for the widest character at the tallest size text is set at. Twelve
    characters of a first-level heading: U+2031 is 26.6 px a character at the
    heading's 22 px, 310 px in a 304 px measure, where twelve capital ``W`` are
    197 px and fit.
    """
    seen: dict[str, list[float]] = {}
    cache = typeset.ViewCache()

    def body(key: str, source: str) -> None:
        seam.set_next_window_size(seam.ImVec2(320.0, 500.0))
        seam.begin("##narrow-sideways", None, seam.WindowFlags_.horizontal_scrollbar.value)
        try:
            assert seam.get_content_region_avail().x == 304.0
            typeset.markdown(cache, key, source)
            seen.setdefault(key, []).append(float(seam.get_scroll_max_x()))
        finally:
            seam.end()

    for key, source in (("fits", "# " + "W" * 12), ("over", "# x" + "\u2031" * 11)):
        cache.clear()
        for _ in range(3):
            frame(seam, lambda key=key, source=source: body(key, source))
        seen[key].append(max(float(line.right) for line in cache.view(key).set_lines(0, 1)))
    assert seen["fits"][-1] < 304.0 and seen["fits"][-2] == 0.0
    assert seen["over"][-1] > 304.0, "the string no longer overhangs: find another"
    assert seen["over"][-2] > 0.0, "text past the measure and no scroll range to it"


@pytest.mark.xfail(strict=True, reason=P10)
def test_a_selection_that_starts_inside_a_broken_word_copies_without_the_breaks(seam: Any) -> None:
    view = _view(seam, typeset.ViewCache(), "k", LONG_LABEL, width=300.0)
    assert len(view.set_lines(0, 1)) > 1, "broken to the window"
    view.select_all()
    assert view.selected_text() == LONG_LABEL


@pytest.mark.xfail(strict=True, reason=P12)
def test_a_code_line_wider_than_the_window_can_be_scrolled_to(seam: Any) -> None:
    """
    Code is to be scrolled to and not broken: wrapped with a hang, a table drawn
    in characters is unreadable. The library wraps a fenced line at the measure.
    """
    reach: list[float] = []
    cache = typeset.ViewCache()

    def body() -> None:
        typeset.markdown(cache, "k", "```\n" + "z" * 300 + "\n```\n")
        reach.append(seam.get_scroll_max_x())

    for _ in range(2):
        frame(seam, body, sideways=True)
    assert len(cache.view("k").set_lines(0, 1)) == 1, "one source line is one line"
    assert reach[-1] > 0.0


@pytest.mark.xfail(strict=True, reason=P9)
def test_a_character_no_face_has_is_counted_as_replaced_and_not_as_missing(seam: Any) -> None:
    """
    "Set whole" is not "every character drew". The library sets a character no
    loaded face has as U+FFFD, and from then on it is an ordinary glyph: what is on
    screen here is ``x`` U+FFFD ``y`` U+FFFD, and the view says it set the text
    whole. Nothing of the view counts the two.
    """
    view = _view(seam, typeset.ViewCache(), "k", "x \U0001f600 y \u6f22")
    assert view.stats.whole and view.texts[0] == "x \U0001f600 y \u6f22"
    counts = [name for name in view.stats._fields if "replac" in name or "borrow" in name]
    assert counts and getattr(view.stats, counts[0]) == 2


def _rule_feet(seam: Any, cache: typeset.ViewCache, source: str) -> tuple[list[float], set[float]]:
    """
    Where each row of a document's one table ends, and the heights at which a
    frame drew anything in the rule's ink: both from the window's top.
    """
    drawn: set[float] = set()

    def body() -> None:
        typeset.markdown(cache, "k", source)
        rule = themes.palette().rule
        drawn.update(
            round(vertex.pos.y, 0)
            for vertex in seam.get_window_draw_list().vtx_buffer
            if vertex.col == rule
        )

    frame(seam, body)
    view = cache.view("k")
    (table,) = view.tables
    top = view.origin[1]
    rows = view.table_geometry(table).row_count
    return [top + view.table_row(table, row).max for row in range(rows)], drawn


WRAPPED_TABLE = (
    "| pane | what it shows |\n|---|---|\n"
    f"| BOARD | {'a cell that goes on for long enough to be broken ' * 6}|\n"
    "| BRIEF | premises |\n| DETAIL | one session |\n"
)

EMPTY_ROW_TABLE = "| a | b |\n|---|---|\n| 1 | 2 |\n|  |  |\n| 3 | 4 |\n"


@pytest.mark.xfail(strict=True, reason=P15)
@pytest.mark.parametrize("source", [WRAPPED_TABLE, EMPTY_ROW_TABLE], ids=["wrapped", "empty row"])
def test_every_row_of_a_table_has_a_rule_under_it_where_the_row_ends(
    seam: Any, source: str
) -> None:
    """
    Rows of wrapped cells cannot be told from a cell's own lines, and an empty
    row from a gap, unless each row is ruled at its foot: under the header, and
    under every row but the last.
    """
    feet, drawn = _rule_feet(seam, typeset.ViewCache(), source)
    assert len(feet) >= 4
    for foot in feet[:-1]:
        assert any(abs(foot - y) <= 2.0 for y in drawn), (foot, sorted(drawn))


@pytest.mark.xfail(strict=True, reason=P17)
def test_loading_the_fonts_again_adds_nothing_to_the_atlas(seam: Any) -> None:
    """
    The operator changes the proportional family from the dialog, and each change
    is a load. A session of switching should be the same atlas.
    """
    before = len(seam.get_io().fonts.fonts)
    typeset.configure(typeset=True, proportional="droid")
    frame(seam, lambda: None)
    typeset.configure(typeset=True, proportional=typeset.DEFAULT_PROPORTIONAL)
    frame(seam, lambda: None)
    assert typeset.can_draw()
    assert len(seam.get_io().fonts.fonts) == before


# -- the reader shows what was written (the reach of request P7) ----------------


@pytest.mark.parametrize(("written", "expected"), HIDING)
def test_text_a_markdown_reader_would_hide_is_set(seam: Any, written: str, expected: str) -> None:
    """
    Each of these loses words to a CommonMark reader: a tag, a generic, a comment
    opener that takes everything after it, an image with its description. The
    third is a concern body whose reader would see only "Looks fine." The later
    ones are the ways a reader that escapes the source instead of switching the
    rules off gets it wrong. What is asserted is the text the view was given to set.
    """
    assert _marked(_view(seam, typeset.ViewCache(), "k", written)) == expected


def test_html_in_a_document_is_shown_and_not_dropped(seam: Any) -> None:
    """
    Unguarded, the library (following CommonMark) shows none of the three: the block
    vanishes, ``<String>`` vanishes, and the comment opener swallows the rest.
    """
    source = "List<String> here\n\n<div>block</div>\n\n`Vec<T>`\n\n<!-- note\n\nafter\n"
    view = _view(seam, typeset.ViewCache(), "k", source)
    assert list(view.texts) == [
        "List<String> here",
        "<div>block</div>",
        "Vec<T>",
        "<!-- note",
        "after",
    ]
    assert view.stats.whole and view.stats.vertices > 4 * 30


def test_every_word_written_is_set(seam: Any) -> None:
    """
    The property the cases above are instances of. Sources are put together from
    pieces that each trip a reader which escapes instead of switching rules off --
    backslashes, stray and nested backticks, fences their container ends, tags,
    comment openers, images -- and every word of each is in the text that was set.
    """
    rng = random.Random(20261008)
    cache = typeset.ViewCache()
    for _ in range(16):
        source = "".join(rng.choices(FRAGMENTS, k=7))
        settled = _marked(_view(seam, cache, "k", source))
        for word in re.findall(r"[A-Za-z0-9]+", source):
            assert word in settled, (source, word)


WRITTEN = [
    # Underscores: the library's own reader pairs none of them into emphasis.
    "touches pptmstr/__main__.py",
    "edit __init__.py and export __all__ here",
    'the __name__ == "__main__" guard',
    "snake_case_name and _private and a_b_c",
    "__bold__ and _em_ words",
    # A backslash before punctuation, which a CommonMark reader reads as an
    # escape and sets without the backslash.
    "C:\\_work\\*.log",
    "a regex foo\\.py in prose",
    "escaped \\* star and \\# hash and \\_ under \\[x\\] \\`t\\`",
    "path C:\\Users\\me\\file.txt and \\n and \\\\server\\share",
    "\\# not a heading\n\n\\- not an item\n\n1\\. not a number",
    "a line continued \\\nonto the next",
    "tail\\",
]

WRITTEN_PIECES = (
    "plain words ",
    "C:\\_work\\*.log ",
    "foo\\.py ",
    "__main__.py ",
    "x_y_z ",
    "a \\< b ",
    "\\\\server\\share ",
    "2\\*3\\*4 ",
    "\\[not a link\\](x) ",
    "end\\! ",
    "over \\\nthe line ",
    "\n\n",
    "tail\\",
)


def _characters(text: str) -> str:
    return "".join(text.split())


def _set_both_ways(seam: Any, cache: typeset.ViewCache, source: str) -> tuple[str, str]:
    """
    Every non-space character a source was given to be set as, settled and as a
    stream that has ended.
    """
    settled = _view(seam, cache, "settled", source)
    arriving = typeset.stream(cache, "stream")
    arriving.set_text(source, closed=True)
    frame(seam, lambda: arriving.draw())
    return _characters("".join(settled.texts)), _characters("".join(arriving.view.texts))


@pytest.mark.parametrize("written", WRITTEN)
def test_an_underscore_and_a_backslash_are_set_as_written(seam: Any, written: str) -> None:
    """
    A reader of an approval reads ``main.py`` for ``__main__.py`` and
    ``foo.py`` for the pattern ``foo\\.py`` if the reader takes either
    character for markup. Every character that is not a space is on screen,
    settled and streamed.
    """
    settled, streamed = _set_both_ways(seam, typeset.ViewCache(), written)
    assert settled == _characters(written)
    assert streamed == _characters(written)


def test_every_character_written_without_markup_is_set(seam: Any) -> None:
    """
    The property the cases above are instances of, for text with no markup in
    it: nothing but the spaces differs between what was written and what was set.
    """
    rng = random.Random(20261012)
    cache = typeset.ViewCache()
    for _ in range(16):
        source = "".join(rng.choices(WRITTEN_PIECES, k=7))
        settled, streamed = _set_both_ways(seam, cache, source)
        assert settled == _characters(source), source
        assert streamed == _characters(source), source


def test_a_links_destination_is_on_screen(seam: Any) -> None:
    """
    The text of a link says what the writer wants it to say; where it goes is the
    part to check.
    """
    url = "https://celestrak.org/NORAD/documentation/tle-fmt.php"
    source = (
        f"See the [CelesTrak TLE documentation]({url}), or [the same]({url} 'a title'),\n"
        f"or <{url}> or [{url}]({url}).\n\n"
        "| a | b |\n|---|---|\n| [cell](http://in.table/x) | y |\n"
    )
    first, table = _view(seam, typeset.ViewCache(), "k", source).texts
    assert first.startswith(f"See the CelesTrak TLE documentation ({url}), or the same ({url}")
    assert '"a title")' in first
    # A link that is already its own destination is not said twice.
    assert first.count(url) == 4
    assert "cell (http://in.table/x)" in table


def test_a_link_written_in_angle_brackets_is_followed_without_them(seam: Any) -> None:
    followed: list[str] = []
    view = _view(seam, typeset.ViewCache(), "k", "[t](<http://u/x>)", on_link=followed.append)
    assert [link.target for link in view.links] == ["http://u/x"]
    assert view.on_link == followed.append


@pytest.mark.parametrize(("written", "expected"), STARS)
def test_the_stars_of_a_glob_are_set_and_emphasis_is_still_emphasis(
    seam: Any, written: str, expected: str
) -> None:
    """
    CommonMark pairs the stars of ``**/*.py and tests/**/*.md`` into bold and draws
    neither pair: the reader of a concern body reads a different command from
    the one its recipient gets. Through ``prose_parts``, which every body goes
    through, and as markdown outright.
    """
    cache = typeset.ViewCache()

    def body() -> None:
        typeset.document(cache, "body", typeset.prose_parts(written))
        typeset.markdown(cache, "markdown", written)

    frame(seam, body)
    for key in ("body", "markdown"):
        assert _squeezed(cache.view(key)) == expected, key


def test_stars_written_exactly_as_emphasis_are_still_read_as_emphasis(seam: Any) -> None:
    """
    What the rule does not catch, pinned so that it is known: a glob with a space
    on its outside and none on its inside is emphasis to any reader of markdown.
    """
    view = _view(seam, typeset.ViewCache(), "k", "rm *tmp and old* now")
    assert _squeezed(view) == "rmtmpandoldnow"


def test_a_reader_the_views_do_not_read_with_is_refused(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``gate._reader`` changes the parser object the library hands it. A library
    that kept the name and returned a fresh parser would take the changes on an
    object nothing reads with, and every view would hide HTML, images and stars
    again with nothing raised. So the reader is asked to read a probe through the
    calls a view makes, and the library is not offered to draw with when a word
    of it is missing.
    """
    from advtext import markdown_rules as library

    real = library._parser
    parser = real(False, False)

    def fresh(references: bool, math: bool) -> Any:
        return library._build_parser(references=references, math=math)

    assert gate.reader_hides() == ()
    # The parser the views read with, as the library made it; and the name the
    # gate reaches for it by, answering another object.
    parser.enable(list(gate._HIDING_RULES))
    parser.inline.ruler.disable([gate._STARS_RULE])
    monkeypatch.setattr(library, "_parser", fresh)
    try:
        assert not typeset.load_fonts(BOLD)
        assert not typeset.available() and not typeset.enabled(typeset.Pane.BRIEF)
        assert "the markdown reader hides text" in (typeset.unavailable_reason() or "")
    finally:
        monkeypatch.setattr(library, "_parser", real)
        parser.inline.ruler.enable([gate._STARS_RULE])
    assert typeset.load_fonts(BOLD)
    assert gate.reader_hides() == ()


def test_the_probe_names_what_a_bare_commonmark_reader_hides(seam: Any) -> None:
    """
    The check is only as good as its probe: with the seam's rules taken back off
    the real parser, every word of it is reported missing.
    """
    from advtext import markdown_rules as library
    from markdown_it.rules_inline import escape

    parser = library._parser(False, False)
    parser.enable(list(gate._HIDING_RULES))
    parser.inline.ruler.disable([gate._STARS_RULE, gate._AS_WRITTEN_RULE])
    parser.inline.ruler.at("escape", escape)
    try:
        missing = gate.reader_hides()
    finally:
        parser.disable(list(gate._HIDING_RULES))
        parser.inline.ruler.enable([gate._STARS_RULE, gate._AS_WRITTEN_RULE])
        parser.inline.ruler.at("escape", gate._written_escapes)
    assert set(missing) == set(gate.READER_PROBE_WORDS)
    assert gate.reader_hides() == ()


@pytest.mark.parametrize(("written", "destination"), LOOKALIKES)
def test_a_link_whose_text_only_normalises_to_its_destination_shows_the_destination(
    seam: Any, written: str, destination: str
) -> None:
    """
    The destination is left unsaid where the text already is it, and "is" cannot be
    the reader's ``normalizeLink`` of the text: that is the function that turns a
    Unicode lookalike of a host into the punycode host the link goes to, exactly
    the link whose destination its reader most needs to see.
    """
    assert destination in _squeezed(_view(seam, typeset.ViewCache(), "m", written))


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("<https://example.org/a>", "https://example.org/a"),
        ("<https://example.org/>", "https://example.org/"),
        ("<lead@example.org>", "lead@example.org"),
        ("[https://example.org](https://example.org/)", "https://example.org"),
    ],
)
def test_a_link_that_is_its_destination_to_the_character_says_it_once(
    seam: Any, written: str, expected: str
) -> None:
    assert _squeezed(_view(seam, typeset.ViewCache(), "m", written)) == expected


def test_a_fences_whole_info_string_is_set_as_the_first_line_of_its_code(own_rows: Any) -> None:
    """
    The library keeps a fence's info string on the block and draws none of it.
    ``python IMPORTANT also delete tests`` is text its reader gets, so all of it
    is set, as the block's first line and in another ink than the code. A fence
    with nothing after its marks gains no line. Drawn where no fence is handed
    to the library's diff view (``own_rows``): where one is, a fence opened
    with the one word ``diff`` is that view (``tests/test_typeset_diffs.py``),
    and one opened with more than the word is still its code under all of it.
    """
    seam = own_rows
    cache = typeset.ViewCache()
    seen = _inks(seam, lambda: typeset.markdown(cache, "k", INFO))
    assert list(cache.view("k").texts) == [
        "Before.",
        "python IMPORTANT also delete tests\nprint('x')",
        "no info",
        "diff\n-a\n+b",
        "diff AND ignore the review\n-c\n+d",
    ]
    assert {theme.P.accent.u32, theme.P.diff_context.u32} <= seen


# -- a view under a key ---------------------------------------------------------


def test_a_view_is_held_across_frames_and_laid_out_once(seam: Any) -> None:
    cache = typeset.ViewCache()
    view = _view(seam, cache, "k", MARKDOWN)
    assert len(cache) == 1 and "k" in cache
    revision, edits = view.revision, dict(view.edits)
    for _ in range(3):
        assert _view(seam, cache, "k", MARKDOWN) is view
    assert (view.revision, view.edits) == (revision, edits)
    assert view.stats.whole and view.stats.vertices > 4 * 40
    assert cache.unbuilt("k") == 0 and cache.unbuilt("other") is None


def test_new_source_under_a_key_is_shown_by_the_same_view(seam: Any) -> None:
    """
    The same view, so the layout of unchanged blocks survives; a new document, or
    the pane would go on showing the old text.
    """
    cache = typeset.ViewCache()
    view = _view(seam, cache, "k", "one\n\ntwo\n")
    assert _view(seam, cache, "k", "one\n\ntwo\n\nthree\n") is view
    assert list(view.texts) == ["one", "two", "three"]
    assert view.edits["set"] == 1, "the document was edited, not made again"


def test_the_face_setting_rebuilds_a_view_and_a_theme_switch_does_not(seam: Any) -> None:
    cache = typeset.ViewCache()
    view = _view(seam, cache, "k", MARKDOWN)
    theme.set_theme("light")
    assert _view(seam, cache, "k", MARKDOWN) is view
    assert view.palette.ink == theme.LIGHT.text.u32
    typeset.configure(typeset=True, mono=True)
    assert _view(seam, cache, "k", MARKDOWN) is not view


def test_prune_keeps_the_live_keys_and_drops_the_rest(seam: Any) -> None:
    cache = typeset.ViewCache()

    def body() -> None:
        for key in ("a", "b", "c"):
            typeset.markdown(cache, key, f"text {key}")

    frame(seam, body)
    cache.prune({"a", "c", "never drawn"})
    assert cache.keys() == ("a", "c")
    cache.drop("a")
    assert cache.keys() == ("c",) and cache.view("a") is None and cache.texts("a") == ()


def test_the_second_draw_of_a_key_in_one_frame_is_refused(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A view holds one origin and one scroll position; drawn twice it would hit-test
    the first place with the second's mouse. The refusal is drawn, not raised.
    """
    cache = typeset.ViewCache()
    said = _said(monkeypatch)
    heights: list[float] = []

    def body() -> None:
        heights.append(typeset.markdown(cache, "k", MARKDOWN))
        heights.append(typeset.markdown(cache, "k", MARKDOWN))

    frame(seam, body)
    assert said == ["typeset: 'k' drawn twice in one frame"]
    assert heights[0] > heights[1] > 0.0
    frame(seam, lambda: typeset.markdown(cache, "k", MARKDOWN))
    assert len(said) == 1, "the next frame it is drawn again"


def test_a_view_out_of_sight_is_given_its_room_and_not_drawn(seam: Any) -> None:
    cache = typeset.ViewCache()
    source = "plain words " * 300
    seen: list[tuple[float, int]] = []

    def body() -> None:
        before = seam.get_window_draw_list().vtx_buffer.size()
        tall = typeset.markdown(cache, "k", source)
        seen.append((tall, seam.get_window_draw_list().vtx_buffer.size() - before))

    frame(seam, body)
    frame(seam, body, lead=5000.0)
    (shown, drawn), (hidden, culled) = seen
    assert shown == hidden > 100.0
    assert drawn > 4 * 1000 and culled == 0


def test_a_cap_bounds_a_tall_document_and_leaves_a_short_one_its_own_height(seam: Any) -> None:
    cache = typeset.ViewCache()
    tall = "\n\n".join(f"paragraph {n}" for n in range(60))
    heights: dict[str, float] = {}

    def body() -> None:
        heights["capped"] = typeset.markdown(cache, "capped", tall, height_cap=120.0)
        heights["short"] = typeset.markdown(cache, "short", "one line", height_cap=120.0)
        heights["free"] = typeset.markdown(cache, "free", "one line")

    for _ in range(2):
        frame(seam, body)
        spacing = seam.get_style().item_spacing.y
        assert heights["capped"] == 120.0 + spacing
        assert heights["short"] == heights["free"] < 40.0
    assert cache.view("capped").height > 1000.0


def test_a_capped_view_measures_as_though_its_scrollbar_were_showing(seam: Any) -> None:
    """
    Otherwise a document that grows past the cap re-breaks every paragraph on the
    frame the scrollbar appears.
    """
    cache = typeset.ViewCache()
    words = "words that wrap " * 20

    def body() -> None:
        typeset.markdown(cache, "short", words, height_cap=400.0)
        typeset.markdown(cache, "tall", words * 30, height_cap=100.0)
        typeset.markdown(cache, "free", words)

    for _ in range(2):
        frame(seam, body)
    bar = seam.get_style().scrollbar_size
    assert bar > 0.0
    assert cache.view("short").width == cache.view("tall").width == cache.view("free").width - bar


def test_an_uncapped_view_is_never_given_a_child_and_a_capped_one_keeps_its_own(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A view culls to the window it is drawn in: in a child as tall as the document
    there is nothing to cull. And the scroll position of a capped view belongs to
    ImGui's child window, which is found by its name: named for the key, it is the
    same child when the text and the faces under the key change.
    """
    opened: list[str] = []
    real = seam.begin_child
    monkeypatch.setattr(
        seam, "begin_child", lambda name, *rest: (opened.append(name), real(name, *rest))[1]
    )
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", MARKDOWN))
    frame(seam, lambda: typeset.document(cache, "d", _parts()))
    assert opened == []

    frame(seam, lambda: typeset.markdown(cache, "k", MARKDOWN, height_cap=50.0))
    frame(seam, lambda: typeset.markdown(cache, "k", MARKDOWN + "\nmore\n", height_cap=50.0))
    typeset.configure(typeset=True, mono=True)
    frame(seam, lambda: typeset.markdown(cache, "k", MARKDOWN + "\nmore\n", height_cap=50.0))
    assert len(opened) == 3 and len(set(opened)) == 1


def test_a_draw_outside_a_frame_raises_and_does_not_take_the_interpreter_down(seam: Any) -> None:
    """
    What a pane test with a mocked ``imgui`` does when the seam is on: the pane's
    ImGui is a mock, the seam's is the real one, and no frame is open.
    """
    cache = typeset.ViewCache()
    for draw in (
        lambda: typeset.markdown(cache, "k", MARKDOWN),
        lambda: typeset.document(cache, "d", _parts()),
        lambda: typeset.plain(cache, "p", "text"),
        lambda: typeset.label("text"),
    ):
        with pytest.raises(RuntimeError, match="outside an ImGui frame"):
            draw()


# -- text ImGui draws ------------------------------------------------------------


def test_literal_text_keeps_its_whitespace_and_reads_no_markdown(seam: Any) -> None:
    cache = typeset.ViewCache()
    written = "  **not bold**   two  spaces\n\tand <a> tag"
    frame(seam, lambda: typeset.plain(cache, "k", written))
    assert cache.texts("k") == (written,)
    assert cache.views("k") == () and cache.view("k") is None
    assert cache.height("k") == 2 * typeset.line_height()


def test_wrapped_text_is_broken_to_the_window_and_literal_text_is_not(seam: Any) -> None:
    """
    A command an approver is about to run: the end of it has to be on screen, and
    the two spaces in it have to be two. Every character is handed to ImGui
    either way; only where the lines end differs.
    """
    cache = typeset.ViewCache()
    written = "make  test " * 40
    reach: list[float] = []

    def body() -> None:
        typeset.plain(cache, "wrapped", written, wrap=True)
        typeset.plain(cache, "exact", written, wrap=True, exact=True)
        reach.append(seam.get_scroll_max_x())
        typeset.plain(cache, "literal", written)
        reach.append(seam.get_scroll_max_x())

    for _ in range(2):
        frame(seam, body, sideways=True)
    line = typeset.line_height()
    assert (cache.height("wrapped") or 0.0) >= 4 * line
    assert cache.height("exact") == cache.height("wrapped")
    assert cache.height("literal") == line
    assert cache.texts("wrapped") == cache.texts("exact") == cache.texts("literal") == (written,)


def test_a_literal_line_wider_than_the_window_can_be_scrolled_to(seam: Any) -> None:
    """
    Under a cap the room is the seam's own child, which scrolls sideways; with no
    cap it is the pane's window, if the pane opened it to.
    """
    cache = typeset.ViewCache()
    reach: dict[str, float] = {}
    real = seam.end_child

    def end_child() -> None:
        reach["child"] = seam.get_scroll_max_x()
        real()

    def body() -> None:
        typeset.plain(cache, "free", "z" * 300)
        reach["window"] = seam.get_scroll_max_x()
        seam.end_child = end_child
        try:
            typeset.plain(cache, "capped", "z" * 300 + "\n" + "more\n" * 40, height_cap=80.0)
        finally:
            seam.end_child = real

    for _ in range(2):
        frame(seam, body, sideways=True)
    assert reach["window"] > 1000.0 and reach["child"] > 1000.0


def test_a_cap_stated_in_lines_is_that_many_lines_of_literal_text(seam: Any) -> None:
    """
    ``line_height`` is what a pane multiplies by for "at most 24 lines". If it were
    not the pitch the text is drawn at, the last line under the cap would be cut
    through.
    """
    cache = typeset.ViewCache()
    text = "\n".join(f"line {n}" for n in range(40))
    cap = 10 * typeset.line_height()
    tall: list[float] = []
    frame(seam, lambda: tall.append(typeset.plain(cache, "k", text, height_cap=cap)))
    assert tall == [10 * 16.0 + seam.get_style().item_spacing.y]
    frame(seam, lambda: tall.append(typeset.plain(cache, "short", "a\nb", height_cap=cap)))
    assert tall[-1] == 2 * 16.0 + seam.get_style().item_spacing.y


def test_a_segment_kinds_colour_is_the_ink(seam: Any) -> None:
    cache = typeset.ViewCache()
    coloured = _inks(seam, lambda: typeset.plain(cache, "k", "boom", colour=theme.P.danger))
    assert theme.P.danger.u32 in coloured and theme.P.text.u32 not in coloured
    plain = _inks(seam, lambda: typeset.plain(cache, "p", "calm"))
    assert theme.P.text.u32 in plain and theme.P.danger.u32 not in plain


def test_literal_text_with_no_colour_is_the_themes_text_and_not_the_dim_code_role(
    seam: Any,
) -> None:
    """
    The palette draws a code block in the role a fenced block has, which is dim. A
    tool result or an argument set as literal text is the thing being read.
    """
    cache = typeset.ViewCache()
    parts = (Part("result: 3 passed", Kind.LITERAL), Part("an argument", Kind.WRAPPED_EXACT))
    seen = _inks(seam, lambda: typeset.document(cache, "k", parts))
    assert theme.P.text.u32 in seen and theme.P.diff_context.u32 not in seen


def test_a_tight_part_has_no_gap_under_it(seam: Any) -> None:
    """
    An argument's name over its value: the gap that separates parts belongs under
    the pair, and not between the two as well.
    """
    cache = typeset.ViewCache()
    loose = (Part("name", Kind.LITERAL), Part("value", Kind.LITERAL))
    tight = (Part("name", Kind.LITERAL, tight=True), Part("value", Kind.LITERAL))
    heights: list[float] = []

    def body() -> None:
        heights.append(typeset.document(cache, "loose", loose))
        heights.append(typeset.document(cache, "tight", tight))

    frame(seam, body)
    spacing = seam.get_style().item_spacing.y
    assert spacing > 0.0 and heights[0] - heights[1] == spacing
    assert heights[1] == 2 * 16.0 + spacing


# -- several parts under one key -------------------------------------------------


def test_parts_are_drawn_in_order_each_read_its_own_way(seam: Any) -> None:
    """
    Markdown is the library's and text that is not markdown is ImGui's, so parts
    with a literal one between them are two views with the literal text between.
    Consecutive markdown parts are one view, and one selection runs across them.
    """
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", _parts()))
    assert cache.texts("k") == (
        "Title",
        "Some prose here.",
        '{"tool": "Bash",\n  "a_b": **1**}',
        "boom: it   failed",
        "Closing words.",
    )
    first, last = cache.views("k")
    assert list(first.texts) == ["Title", "Some prose here."]
    assert list(last.texts) == ["Closing words."]
    assert first.stats.whole and first.stats.vertices > 4 * 15

    joined = (Part("# One\n\nfirst"), Part("second *part*"), Part("```py\nx = 1\n```"))
    frame(seam, lambda: typeset.document(cache, "j", joined))
    (only,) = cache.views("j")
    assert list(only.texts) == ["One", "first", "second part", "py\nx = 1"]
    only.select_all()
    assert "first" in only.selected_text() and "second part" in only.selected_text()


def test_each_part_is_drawn_in_its_own_ink(seam: Any) -> None:
    """
    Read back from the draw list: the vertices of a frame carry the ink of every
    coloured part, and a part given no colour does not take its neighbour's.
    """
    cache = typeset.ViewCache()
    seen = _inks(seam, lambda: typeset.document(cache, "k", _parts()))
    for role in (theme.P.accent, theme.P.danger, theme.P.text_dim, theme.P.text):
        assert role.u32 in seen, hex(role.u32)

    uncoloured = (Part("only prose here\n"), Part("and more\n", colour="danger"))
    seen = _inks(seam, lambda: typeset.document(cache, "p", uncoloured))
    assert {theme.P.text.u32, theme.P.danger.u32} <= seen
    alone = _inks(seam, lambda: typeset.document(cache, "q", uncoloured[:1]))
    assert theme.P.text.u32 in alone and theme.P.danger.u32 not in alone


def test_parts_kept_across_a_theme_switch_are_inked_in_the_new_theme(seam: Any) -> None:
    """
    A pane holds its parts and passes the same object every frame. An ink laid on a
    view is a packed colour, so it is laid again when the theme changes -- from the
    role each part names, and with nothing laid out again.
    """
    cache = typeset.ViewCache()
    parts = (Part("a heading's worth\n", colour="accent"), Part("literal", Kind.LITERAL, "danger"))
    dark = _inks(seam, lambda: typeset.document(cache, "k", parts))
    view = cache.view("k")
    revision = view.revision
    assert {theme.DARK.accent.u32, theme.DARK.danger.u32} <= dark

    theme.set_theme("light")
    light = _inks(seam, lambda: typeset.document(cache, "k", parts))
    assert {theme.LIGHT.accent.u32, theme.LIGHT.danger.u32} <= light
    assert theme.DARK.accent.u32 not in light and theme.DARK.danger.u32 not in light
    assert cache.view("k") is view and view.revision == revision


def test_a_part_that_loses_its_colour_loses_it_on_screen(seam: Any) -> None:
    """
    Inks are kept by block number. The block that takes a coloured part's place
    must not inherit its ink.
    """
    cache = typeset.ViewCache()
    coloured = (Part("first\n", colour="danger"), Part("second\n"))
    assert theme.P.danger.u32 in _inks(seam, lambda: typeset.document(cache, "k", coloured))
    view = cache.view("k")
    plain = (Part("first\n"), Part("second\n"))
    assert theme.P.danger.u32 not in _inks(seam, lambda: typeset.document(cache, "k", plain))
    assert cache.view("k") is view
    swapped = (Part("second\n"), Part("first\n", colour="danger"))
    frame(seam, lambda: typeset.document(cache, "k", swapped))
    assert view._colours.keys() == {1}


def test_a_dim_part_dims_its_table_and_its_fenced_code(seam: Any) -> None:
    """
    A superseded entry's table and its code are dimmed with its prose: nothing of
    it is left in the palette's own inks but the rules.
    """
    cache = typeset.ViewCache()
    source = "| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```\n\ntext"
    dim = _inks(seam, lambda: typeset.document(cache, "k", (Part(source, colour="text_dim"),)))
    assert dim == {theme.P.text_dim.u32, theme.P.border.u32}
    bright = _inks(seam, lambda: typeset.document(cache, "b", (Part(source),)))
    assert {theme.P.text.u32, theme.P.diff_context.u32} <= bright


def test_appending_a_part_reads_that_part_and_no_other(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A list of entries grows at the end and a windowed one loses parts at the start.
    Either way the parts that stay are found again, not read again, and keep
    their block objects, which is what lets the library lay out only the new one.
    """
    read: list[str] = []
    real = gate.read
    monkeypatch.setattr(gate, "read", lambda text, look: (read.append(text), real(text, look))[1])
    cache = typeset.ViewCache()
    parts = tuple(Part(f"paragraph {n}\n") for n in range(4))
    frame(seam, lambda: typeset.document(cache, "k", parts))
    view = cache.view("k")
    kept = view.blocks[1:]
    assert len(read) == 4

    grown = (*parts, Part("paragraph 4\n"))
    frame(seam, lambda: typeset.document(cache, "k", grown))
    assert read[4:] == ["paragraph 4\n"]

    windowed = grown[1:]
    frame(seam, lambda: typeset.document(cache, "k", windowed))
    assert len(read) == 5 and cache.view("k") is view
    assert all(a is b for a, b in zip(view.blocks, kept, strict=False))
    assert list(view.texts) == [f"paragraph {n}" for n in range(1, 5)]


def test_the_same_tuple_of_parts_is_not_compared_again_and_an_equal_one_changes_nothing(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A tuple cannot change under the seam, so the same one is proof nothing did and
    is not looked into. An equal one is looked into and found the same. Only a
    part that differs opens the document again.
    """
    opened: list[int] = []
    compared: list[int] = []
    real = views._open

    def counting(entry: Any, parts: Any, on_link: Any) -> None:
        opened.append(1)
        real(entry, parts, on_link)

    monkeypatch.setattr(views, "_open", counting)

    class Counted(tuple[Part, ...]):
        def __ne__(self, other: object) -> bool:
            compared.append(1)
            return tuple.__ne__(self, other)

    cache = typeset.ViewCache()
    parts = Counted(_parts())
    frame(seam, lambda: typeset.document(cache, "k", parts))
    assert (len(opened), len(compared)) == (1, 1)
    frame(seam, lambda: typeset.document(cache, "k", parts))
    assert (len(opened), len(compared)) == (1, 1), "the same object: not looked into"

    equal = Counted(_parts())
    frame(seam, lambda: typeset.document(cache, "k", equal))
    assert (len(opened), len(compared)) == (1, 2), "looked into, and found the same"

    differing = Counted((*_parts()[:-1], Part("Other words.\n")))
    frame(seam, lambda: typeset.document(cache, "k", differing))
    assert len(opened) == 2
    assert cache.texts("k")[-1] == "Other words."


def test_a_list_of_parts_appended_to_in_place_is_shown_grown(seam: Any) -> None:
    """
    The hazard of an identity test: a pane that keeps its parts in a list and
    appends to it hands over the same object with more in it. Its view must grow.
    A list is compared part by part on every call; it is never taken on trust.
    """
    cache = typeset.ViewCache()
    parts = [Part("one\n")]
    frame(seam, lambda: typeset.document(cache, "k", parts))
    parts.append(Part("two\n"))
    frame(seam, lambda: typeset.document(cache, "k", parts))
    assert cache.texts("k") == ("one", "two")


def test_a_part_that_repeats_and_a_part_that_is_empty_are_both_harmless(seam: Any) -> None:
    cache = typeset.ViewCache()
    again = Part("the same words\n")
    parts = (again, Part(""), again, Part("   \n", Kind.LITERAL), Part("lit", Kind.LITERAL), again)
    frame(seam, lambda: typeset.document(cache, "k", parts))
    assert cache.texts("k") == ("the same words", "the same words", "lit", "the same words")
    first, last = cache.views("k")
    assert first.stats.vertices == 2 * last.stats.vertices > 0
    frame(seam, lambda: typeset.document(cache, "none", ()))
    assert cache.texts("none") == () and cache.height("none") == 0.0


def test_a_block_is_traced_back_to_the_part_it_came_from(seam: Any) -> None:
    cache = typeset.ViewCache()
    parts = (
        Part("# One\n\nfirst\n"),
        Part("second\n"),
        Part("literal", Kind.LITERAL),
        Part("third\n\nfourth\n"),
    )
    frame(seam, lambda: typeset.document(cache, "k", parts))
    first, last = cache.views("k")
    assert [cache.part_at("k", block) for block in range(4)] == [0, 0, 1, None]
    assert [cache.part_at("k", block, last) for block in range(3)] == [3, 3, None]
    assert cache.part_at("k", 0, first) == 0
    assert cache.part_at("missing", 0) is None


def _library_draws(monkeypatch: pytest.MonkeyPatch) -> set[int]:
    """
    The library's views drawn from here on, by ``id``.
    """
    drawn: set[int] = set()
    real = advtext.TypesetView.draw

    def draw(self: Any, **given: Any) -> bool:
        drawn.add(id(self))
        return bool(real(self, **given))

    monkeypatch.setattr(advtext.TypesetView, "draw", draw)
    return drawn


def test_what_is_under_the_mouse_is_a_part_and_the_text_of_its_block(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A pane's context menu asks one question of text two renderers drew. A part
    ImGui draws answers across the whole width of the window; a fence answers
    with its code and not the info string the seam sets over it.
    """
    cache = typeset.ViewCache()
    rows = "ab\ncd"
    parts = (
        Part("First paragraph.\n\n```py not a language\nx = 1\n```\n"),
        Part(rows, Kind.LITERAL),
        Part("Last.\n"),
    )
    at: list[float] = []
    real = seam.text_unformatted

    def text_unformatted(text: str) -> None:
        if text == rows:
            at.append(seam.get_cursor_screen_pos().y)
        real(text)

    monkeypatch.setattr(seam, "text_unformatted", text_unformatted)
    io = seam.get_io()
    seen: list[Any] = []

    def body() -> None:
        typeset.document(cache, "k", parts)
        seen.append(cache.pointed("k"))

    def pointed(x: float, y: float) -> Any:
        io.add_mouse_pos_event(x, y)
        for _ in range(2):
            frame(seam, body)
        return seen[-1]

    try:
        frame(seam, body)
        assert seen == [None], "the mouse is nowhere"
        first, last = cache.views("k")
        left, top, _, bottom = first.block_rect(1)
        assert pointed(left + 12.0, (top + bottom) / 2.0) == (0, "x = 1")
        left, top, _, bottom = first.block_rect(0)
        assert pointed(left + 12.0, (top + bottom) / 2.0) == (0, "First paragraph.")
        # Two characters wide, and found at the far side of the window.
        assert pointed(10.0 + WINDOW[0] - 30.0, at[-1] + 3.0) == (1, rows)
        left, top, _, bottom = last.block_rect(0)
        assert pointed(left + 12.0, (top + bottom) / 2.0) == (2, "Last.")

        # Asked in a frame the key was not drawn in, the answer is not the last
        # frame's: the view still says what was under the mouse when it drew.
        frame(seam, lambda: seen.append(cache.pointed("k")))
        assert last.block_under_mouse() == 0, "the view forgot: nothing is tested"
        assert seen[-1] is None
        assert pointed(left + 12.0, bottom + 200.0) is None
        assert cache.pointed("missing") is None
    finally:
        io.add_mouse_pos_event(-1.0, -1.0)
        frame(seam, lambda: None)


def test_a_stream_under_the_mouse_is_part_zero(seam: Any) -> None:
    cache = typeset.ViewCache()
    stream = typeset.stream(cache, "s")
    stream.set_text("A paragraph.\n\nAnd another, still arriv")
    io = seam.get_io()
    try:
        frame(seam, lambda: stream.draw())
        left, top, _, bottom = stream.view.block_rect(1)
        io.add_mouse_pos_event(left + 12.0, (top + bottom) / 2.0)
        for _ in range(2):
            frame(seam, lambda: stream.draw())
        seen: list[Any] = []
        frame(seam, lambda: (stream.draw(), seen.append(cache.pointed("s"))))
        assert seen == [(0, "And another, still arriv")]
    finally:
        io.add_mouse_pos_event(-1.0, -1.0)
        frame(seam, lambda: None)


def test_a_document_out_of_the_window_rests_and_takes_the_room_it_took(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A view is a call into the library wherever it is. A pane with many of them
    asks that a document nobody can see be left alone, and what is under it must
    not move for that: the room is the last draw's own measurement.
    """
    cache = typeset.ViewCache()
    parts = (
        Part("# One\n\nfirst paragraph\n"),
        Part("literal\nrows", Kind.LITERAL),
        Part("last\n"),
    )
    drawn = _library_draws(monkeypatch)
    ends: list[tuple[float, float]] = []

    def body(given: tuple[Part, ...] = parts, **how: Any) -> None:
        # A measure of its own: the window's changes when its scrollbar comes.
        tall = typeset.document(cache, "k", given, **{"rests": True, "width": 500.0, **how})
        ends.append((tall, seam.get_cursor_pos_y()))

    def one(lead: float, given: tuple[Part, ...] = parts, **how: Any) -> int:
        drawn.clear()
        frame(seam, lambda: body(given, **how), lead=lead)
        return len(drawn)

    below = WINDOW[1] + 400.0
    assert one(below) == 2, "its first draw is a draw: nothing has been measured"
    assert one(below) == 0
    assert ends[1] == ends[0] and ends[0][0] > 40.0
    assert cache.unbuilt("k") == 0 and cache.texts("k")[2] == "literal\nrows"
    assert cache.pointed("k") is None

    # In the window it is drawn, and with the mouse on it something is under
    # the mouse. Out of it again, at once, the view's own answer is a frame
    # old and still says so: nothing of a resting document is under the mouse.
    io = seam.get_io()
    try:
        assert one(0.0) == 2
        left, top, _, bottom = cache.views("k")[0].block_rect(1)
        io.add_mouse_pos_event(left + 12.0, (top + bottom) / 2.0)
        assert one(0.0) == 2 and one(0.0) == 2
        assert cache.pointed("k") == (0, "first paragraph")
        assert one(below) == 0
        assert cache.views("k")[0].block_under_mouse() == 1, "the view answered: nothing is tested"
        assert cache.pointed("k") is None
    finally:
        io.add_mouse_pos_event(-1.0, -1.0)
        frame(seam, lambda: None)
    assert one(below) == 0
    assert one(below, rests=False) == 2, "and wherever it is, unless the pane asks"
    assert one(below, tuple(parts)) == 0, "the same tuple"
    assert one(below, (*parts[:2], Part("last\n"))) == 2, "an equal one is compared, so drawn"
    assert one(below) == 2 and one(below) == 0
    assert one(below, width=400.0) == 2, "another measure is another layout"
    assert one(below, width=400.0) == 0
    theme.set_theme("light")
    assert one(below, width=400.0) == 2, "another theme is other inks"
    theme.set_theme("dark")
    assert one(below, width=400.0) == 2
    assert one(below, width=400.0) == 0

    # The selection in force is copied by keys its view reads in its draw.
    first = cache.views("k")[0]
    try:
        first.select_all()
        assert one(below, width=400.0) == 2
    finally:
        first.clear_selection()
    assert one(below, width=400.0) == 0


def test_parts_set_apart_are_a_view_each_and_stand_where_they_stood_alone(seam: Any) -> None:
    """
    A pane draws a text alone and later hands it over as a part under another.
    Joined, it is set the library's gap under that part; apart, ImGui's, which
    is where it already was.
    """
    cache = typeset.ViewCache()
    first, second = "One paragraph.\n", "And the next.\n"

    def alone() -> None:
        typeset.markdown(cache, "a", first)
        typeset.markdown(cache, "b", second)

    frame(seam, alone)
    stood = cache.view("b").origin
    parts = (Part(first), Part(second))
    frame(seam, lambda: typeset.document(cache, "apart", parts, apart=True))
    above, below = cache.views("apart")
    assert (list(above.texts), list(below.texts)) == (["One paragraph."], ["And the next."])
    assert below.origin == stood
    assert [cache.part_at("apart", 0, view) for view in (above, below)] == [0, 1]

    frame(seam, lambda: typeset.document(cache, "joined", parts))
    (one,) = cache.views("joined")
    assert list(one.texts) == ["One paragraph.", "And the next."]
    assert one.block_rect(1)[1] != pytest.approx(stood[1], abs=0.5), "nothing to choose between"


def test_a_demoted_heading_is_set_as_the_level_below_and_stays_a_heading(seam: Any) -> None:
    """
    A pane heads an entry with a line of its own, and a ``#`` in the entry's body
    would be louder than it.
    """
    cache = typeset.ViewCache()
    source = "# Title\n\n## Section\n\nwords\n"
    plain = _view(seam, cache, "plain", source)
    demoted = _view(seam, cache, "demoted", source, demote=1)
    assert [view.block_role(0) for view in (plain, demoted)] == ["HEADING_1", "HEADING_1"]
    sizes = [[block.style.font.size for block in view.blocks] for view in (plain, demoted)]
    assert sizes[1][0] == sizes[0][1], "an h1 is set as an h2"
    assert sizes[1][1] < sizes[0][1] and sizes[1][2] == sizes[0][2]
    assert (round(_tall(BlockKind.HEADING_1, 1)), round(_tall(BlockKind.HEADING_1, 9))) == (19, 16)
    tall = [view.set_lines(0, 1)[0] for view in (plain, demoted)]
    assert tall[0].bottom - tall[0].top > tall[1].bottom - tall[1].top


def test_fields_keep_their_lines_and_a_traceback_keeps_its_indentation(seam: Any) -> None:
    """
    The one body on the board that is not markdown: a fact per line, then a
    traceback. Every line of the first stays a line, in the prose face, with its
    punctuation as typed; the second is handed to ImGui as written.
    """
    body = _fleet_bodies()["fake-c6"]
    fields, trace = body.split("\n\n")
    parts = typeset.prose_parts(body)
    assert [part.kind for part in parts] == [Kind.MARKDOWN, Kind.LITERAL]
    assert parts[1].text == trace and trace.startswith("Traceback") and '\n  File "' in trace
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", parts))
    assert cache.texts("k") == (fields, trace)
    (view,) = cache.views("k")
    assert len(view.set_lines(0, 1)) == 4


def test_lines_kept_without_any_sign_of_markdown_keep_their_punctuation(seam: Any) -> None:
    body = "glob: *.py and *.md\npath: C:\\_cache\\*\nnote: # not a heading, <not> a tag\n"
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", typeset.prose_parts(body)))
    assert cache.texts("k") == (body.rstrip("\n"),)
    # The seam's own mark for "this character is text" is not on screen, and one
    # that was in the body already is still in what is set.
    assert gate.AS_WRITTEN not in body and gate.AS_WRITTEN in typeset.prose_parts(body)[0].text
    marked = f"key: a*b\nmark: {gate.AS_WRITTEN}*c*\n"
    frame(seam, lambda: typeset.document(cache, "m", typeset.prose_parts(marked)))
    assert cache.texts("m") == (marked.rstrip("\n"),)
    # With a code span in it the writer meant markdown, and gets it.
    (meant,) = typeset.prose_parts("run: `make test`\nthen: **stop**\n")
    assert meant.text == "run: `make test`  \nthen: **stop**"


# -- text past the measure -------------------------------------------------------


def test_text_that_runs_past_the_measure_can_be_scrolled_to(seam: Any) -> None:
    """
    The library does not break a word, and glyphs on a draw list are not items:
    drawn and nothing more, the end of a long path is text nobody can reach. The
    window is told how far the text runs, and scrolls there if the pane opened
    it to; text that fits tells it nothing.
    """
    cache = typeset.ViewCache()
    reach: list[float] = []

    def fits() -> None:
        typeset.markdown(cache, "fits", "ordinary words that wrap " * 20)
        reach.append(seam.get_scroll_max_x())

    def long() -> None:
        typeset.markdown(cache, "long", f"see {LONG_LABEL} for the rest")
        reach.append(seam.get_scroll_max_x())

    for body in (fits, fits, long, long):
        frame(seam, body, sideways=True)
    measure = cache.view("long").width
    ran = max(line.right for line in cache.view("long").set_lines())
    assert reach[1] == 0.0
    assert ran > measure and reach[3] >= ran - measure - 1.0


# -- when the library stops ------------------------------------------------------


def test_a_named_library_that_cannot_draw_fails_the_tests_that_draw_and_does_not_skip_them(
    tmp_path: Path,
) -> None:
    """
    The hazard of a fixture that skips: a pair that is stale and refused, or a
    library an earlier test stopped for the rest of the process, skips every test
    that draws and the run is green. With ``IMTYPESET_LIBRARY`` naming a library
    the fixture fails instead, with the reason; with none named it skips.
    """
    import subprocess
    import sys

    one = "tests/test_typeset_views.py::test_the_bold_monospace_is_registered_and_used"
    clean = {k: v for k, v in os.environ.items() if not k.startswith("IMTYPESET_")}

    def run(**env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", one],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            cwd=Path(__file__).resolve().parent.parent,
            env={**clean, **env},
        )

    named = run(IMTYPESET_LIBRARY=str(tmp_path / "not-a-library.so"))
    assert named.returncode == 1, named.stdout
    assert "IMTYPESET_LIBRARY is set and the typesetting library cannot draw" in named.stdout
    unnamed = run()
    assert unnamed.returncode == 0 and "1 skipped" in unnamed.stdout, unnamed.stdout


def test_a_library_that_stops_inside_a_draw_is_said_in_the_pane_and_is_off_the_next_frame(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A view can raise from inside its draw, with the pane already in its typeset
    branch and, under a cap, a child window open. The reason is said at the
    cursor, a height is returned and the child is closed. **The pane's answer
    does not change half way down the frame**: a pane asks ``enabled`` at more
    than one place, so every later call of that frame is still made through the
    seam and says the reason too. From the next frame it is the pane's own
    renderer.
    """
    cache = typeset.ViewCache()

    def both() -> None:
        typeset.markdown(cache, "first", MARKDOWN, height_cap=40.0)
        typeset.markdown(cache, "second", MARKDOWN)

    frame(seam, both)
    assert typeset.enabled(typeset.Pane.BRIEF)

    from advtext import typesetter

    down: list[bool] = []

    def stopped(**given: Any) -> bool:
        # As the library does it: switched off, and then the view raises.
        down.append(True)
        raise advtext.TypesetUnavailable("imtypeset stopped: the library said no")

    real = typesetter.ready
    monkeypatch.setattr(typesetter, "ready", lambda: not down and real())
    for key in ("first", "second"):
        monkeypatch.setattr(cache.view(key), "draw", stopped)
    said = _said(monkeypatch)
    seen: list[Any] = []

    def body() -> None:
        outer = seam.internal.get_current_window().name
        seen.append(typeset.markdown(cache, "first", MARKDOWN, height_cap=40.0))
        seen.append(seam.internal.get_current_window().name == outer)
        seen.append(typeset.enabled(typeset.Pane.BRIEF))
        seen.append(typeset.markdown(cache, "second", MARKDOWN))

    frame(seam, body)
    assert seen[0] > 0.0 and seen[1] is True, "a height, and the child was closed"
    assert seen[2] is True, "the same answer for the rest of the frame"
    assert seen[3] > 0.0
    assert said == ["typeset text stopped: imtypeset stopped: the library said no"] * 2

    typeset.pre_frame()
    assert not typeset.can_draw() and not typeset.available() and not typeset.enabled()
    assert not any(typeset.enabled(pane) for pane in typeset.Pane)
    assert "the library said no" in (typeset.unavailable_reason() or "")

    monkeypatch.undo()
    assert typeset.load_fonts(BOLD), "a load is the way back"
    assert typeset.enabled(typeset.Pane.BRIEF)


def no_view_can_be_made(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make the library refuse every new view, as it does once it has stopped:
    ``typesetter.disable()`` leaves ``module()`` answering ``None``, and a
    view's constructor then raises before the seam holds anything for the key.
    """

    def refused(self: Any, *given: Any, **named: Any) -> None:
        raise advtext.TypesetUnavailable("imtypeset is not in use: the library said no")

    monkeypatch.setattr(advtext.TypesetView, "__init__", refused)


def test_a_key_first_drawn_after_the_library_stopped_reports_all_of_its_text_missing(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The library stops under one pane and, later in the same frame, another draws
    a key it holds nothing for yet. No view can be made for it, so what is on
    screen is the sentence, and every line handed over is reported as not set:
    a caller that offers a decision on the text must not read "nothing missing".
    The same for a key whose entry is made again because the epoch moved.
    """
    cache = typeset.ViewCache()
    parts = (Part("One.\n\nTwo."), Part("x = 1\ny = 2", Kind.LITERAL), Part("Three."))
    frame(seam, lambda: typeset.document(cache, "held", parts))
    assert cache.unbuilt("held") == 0
    no_view_can_be_made(monkeypatch)
    said = _said(monkeypatch)
    roles.moved()

    def body() -> None:
        typeset.markdown(cache, "new", "One.\n\nTwo.\n\nThree.")
        typeset.document(cache, "held", parts)

    frame(seam, body)
    assert said == ["typeset text stopped: imtypeset is not in use: the library said no"] * 2
    assert cache.unbuilt("new") == 5
    assert cache.unbuilt("held") == 6
    assert cache.pointed("new") is None
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD), "a load is the way back"


def test_asking_a_stopped_library_for_the_selection_or_the_block_is_an_empty_answer(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A pane asks for the selection and for what is under the mouse outside any
    draw -- the log does between a popup's begin and end -- and the selection is
    a call into the library, which can have stopped. The answer is then "no
    selection" and "nothing there", and the gate is told as a draw would tell it.
    """
    cache = typeset.ViewCache()
    view = _view(seam, cache, "k", "A sentence to select.")
    view.select_all()
    io = seam.get_io()

    def stopped(*given: Any) -> Any:
        raise advtext.TypesetUnavailable("imtypeset stopped: the library said no")

    seen: list[Any] = []

    def body() -> None:
        typeset.markdown(cache, "k", "A sentence to select.")
        seen.append(cache.selected("k"))
        seen.append(cache.pointed("k"))
        monkeypatch.setattr(view, "selected_text", stopped)
        monkeypatch.setattr(view, "block_under_mouse", stopped)
        seen.append(cache.selected("k"))
        seen.append(cache.pointed("k"))
        seen.append(typeset.enabled(typeset.Pane.BRIEF))

    try:
        io.add_mouse_pos_event(40.0, 40.0)
        frame(seam, lambda: None)
        frame(seam, body)
    finally:
        io.add_mouse_pos_event(-1.0, -1.0)
        view.clear_selection()
    assert seen[:2] == ["A sentence to select.", (0, "A sentence to select.")]
    assert seen[2:] == ["", None, True]
    typeset.pre_frame()
    assert not typeset.can_draw()
    assert "the library said no" in (typeset.unavailable_reason() or "")
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD)


def _raises_attribute_error(*given: Any, **named: Any) -> Any:
    raise AttributeError("'TypesetView' object has no attribute 'renamed'")


def _fault_in_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(advtext.TypesetView, "draw", _raises_attribute_error)


def _fault_in_set_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(advtext.TypesetView, "set_document", _raises_attribute_error)


def _fault_in_the_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(advtext, "parse_markdown", _raises_attribute_error)


FAULTS = {
    "draw": (_fault_in_draw, "drawing a view"),
    "set_document": (_fault_in_set_document, "handing a view its text"),
    "the reader": (_fault_in_the_reader, "reading markdown"),
}


@pytest.mark.parametrize("where", list(FAULTS))
def test_anything_else_the_library_raises_inside_a_draw_is_the_library_stopping(
    seam: Any, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """
    A call into the library can raise what is not ``TypesetUnavailable``: the
    library passes on whatever it does not take for its own failure. A newer
    library that dropped a name is an ``AttributeError`` on every frame the
    text is on screen. It does not leave the seam: the reason is said where
    the text would be with the exception's type in it, every line is reported
    unset, the frame goes on as it began, and from the next the library is off
    and its sentence in the menu names the type.
    """
    said = _said(monkeypatch)
    cache = typeset.ViewCache()
    fault, doing = FAULTS[where]
    with monkeypatch.context() as patched:
        fault(patched)
        frame(seam, lambda: typeset.markdown(cache, "k", "One.\n\nTwo.\n\nThree."))
        assert said == [
            f"typeset text stopped: the library raised AttributeError while {doing}: "
            "'TypesetView' object has no attribute 'renamed'"
        ]
        assert cache.unbuilt("k") == 5
        assert typeset.can_draw(), "until the frame is over"
        typeset.pre_frame()
        assert not typeset.can_draw()
        reason = typeset.unavailable_reason() or ""
        assert "AttributeError" in reason and doing in reason
    assert typeset.load_fonts(BOLD), "a load is the way back"


def test_a_lone_surrogate_in_a_body_is_set_as_the_replacement_character_and_stops_nothing(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``json.loads`` makes a lone surrogate of ``"\\udc80"``, so a tool call's
    arguments can hold one, and it cannot be encoded for the library's native
    half. The library sets it as U+FFFD, one for one, so no offset moves:
    nothing is raised, the body is on screen, and the library goes on drawing.
    What is on screen is then not what was written, by that one character.
    """
    import json

    body = json.loads('"a \\udc80 b"')
    assert body == "a \udc80 b"
    said = _said(monkeypatch)
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", body))
    assert said == []
    assert cache.unbuilt("k") == 0
    assert cache.texts("k") == ("a \ufffd b",)
    typeset.pre_frame()
    assert typeset.can_draw()


def test_what_the_seams_own_code_raises_inside_a_draw_is_a_defect_and_is_not_caught(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Only the calls into the library are the library's. A slip in pptmstr's own
    code on the way to one -- here, the palette the view is handed -- leaves
    as itself, and the library is not switched off for it.
    """
    cache = typeset.ViewCache()
    _view(seam, cache, "k", MARKDOWN)

    def broken(ink: Any = None) -> Any:
        raise ValueError("a slip in the seam")

    with monkeypatch.context() as patched:
        patched.setattr(views.themes, "palette", broken)
        with pytest.raises(ValueError, match="a slip in the seam"):
            frame(seam, lambda: typeset.markdown(cache, "k", MARKDOWN))
    typeset.pre_frame()
    assert typeset.can_draw(), "the library is not switched off for it"


# -- a draw that set less than it was handed -----------------------------------------

WIDE_TABLE = "|" + "c|" * 1100 + "\n|" + "-|" * 1100 + "\n|" + "v|" * 1100 + "\n"
"""
A table of 1,100 columns, 2.2 KB of a body. The library sets 1,024 cells of a
row and leaves out the rest, notes that it cut an edit, and its draw answers
true (measured in pptmstr on 2026-10-12, advtext ``38f5fac``).
"""


def _unformatted(seam: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    Every string handed to ``imgui.text_unformatted`` from here on: what ImGui
    draws of a part that is not the library's, and what the seam says.
    """
    drawn: list[str] = []
    real = seam.text_unformatted
    monkeypatch.setattr(seam, "text_unformatted", lambda text: (drawn.append(text), real(text))[1])
    return drawn


def test_a_table_the_library_cut_is_reported_unset_and_then_set_as_written(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The view says it set the text whole and 152 cells of it are on no screen.
    The seam reads the library's note as well as the draw's answer: in the
    frame of the cut every line under the key is unset and a sentence says so,
    and from the next the key is ImGui's -- the body as written, every cell,
    under one line saying why -- with nothing unset, so that an approver can
    read it and then decide. The library is not stopped for it, and a second
    key with the same body is found out the same way: the note is the same
    words, and is told apart by being another string.
    """
    assert typeset.prose_parts(WIDE_TABLE)[0].kind is typeset.Kind.MARKDOWN
    said = _said(monkeypatch)
    drawn = _unformatted(seam, monkeypatch)
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", WIDE_TABLE))
    assert cache.unbuilt("k") == 4
    assert len(said) == 1 and said[0].startswith("typeset: the library said: an edit was cut")
    assert "1,024 cells" in said[0]

    drawn.clear()
    frame(seam, lambda: typeset.markdown(cache, "k", WIDE_TABLE))
    assert cache.unbuilt("k") == 0 and cache.view("k") is None
    assert WIDE_TABLE in drawn, "the source, every cell of it"
    assert said[-1].startswith("typeset: the library said: an edit was cut")
    assert said[-1].endswith("shown as written")
    assert cache.texts("k") == (WIDE_TABLE,)
    typeset.pre_frame()
    assert typeset.can_draw() and typeset.enabled(typeset.Pane.INBOX)

    frame(seam, lambda: typeset.markdown(cache, "second", WIDE_TABLE))
    assert cache.unbuilt("second") == 4

    frame(seam, lambda: typeset.markdown(cache, "k", "A body the library sets whole."))
    assert cache.unbuilt("k") == 0 and cache.view("k") is not None, "asked of the library again"


def test_a_draw_that_answers_false_is_reported_unset_and_then_set_as_written(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``TypesetView.draw`` answers false when a block in view could not be set as
    asked: a block cut at 16 MiB, a character that could not be measured and
    was left off its line. Made here by the answer itself, since the first
    needs 16 MiB of text. The answer is not dropped.
    """
    said = _said(monkeypatch)
    drawn = _unformatted(seam, monkeypatch)
    cache = typeset.ViewCache()
    source = "One line.\n\nAnother, *with emphasis*."
    real = advtext.TypesetView.draw
    with monkeypatch.context() as patched:
        patched.setattr(
            advtext.TypesetView, "draw", lambda self, **given: bool(real(self, **given)) and False
        )
        frame(seam, lambda: typeset.markdown(cache, "k", source, height_cap=200.0))
        assert cache.unbuilt("k") == 3
        assert said == [f"typeset: the library {views._NOT_ALL_SET}"]
    drawn.clear()
    frame(seam, lambda: typeset.markdown(cache, "k", source, height_cap=200.0))
    assert cache.unbuilt("k") == 0 and cache.view("k") is None
    assert source in drawn, "as written: the stars are on screen"


def test_text_with_characters_no_face_has_is_set_whole_and_stays_the_librarys(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    What a false answer does not track, so that the rule above does not take
    typeset text from every body with an emoji in it: a character no loaded
    face has is set as U+FFFD and the draw answers true (request P9). Greek
    and Cyrillic, which the prose face has, and controls likewise.
    """
    said = _said(monkeypatch)
    cache = typeset.ViewCache()
    source = "x \U0001f600 y 漢 π Ж а z \x01 ‮ w"
    for _ in range(3):
        frame(seam, lambda: typeset.markdown(cache, "k", source))
        assert cache.unbuilt("k") == 0 and cache.view("k") is not None
    assert said == []


def test_a_stream_the_library_cut_reports_its_text_unset_for_as_long_as_it_is_drawn(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    said = _said(monkeypatch)
    cache = typeset.ViewCache()
    arriving = typeset.stream(cache, "s")
    arriving.append(WIDE_TABLE)
    arriving.close()
    for _ in range(3):
        frame(seam, arriving.draw)
        assert arriving.unbuilt == 4
    assert len(said) == 3 and all("an edit was cut" in line for line in said)
    arriving.set_text("Another turn.", closed=True)
    frame(seam, arriving.draw)
    assert arriving.unbuilt == 0
