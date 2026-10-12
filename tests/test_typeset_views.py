"""
The seam's views, and the rig the pane tests draw with.

The helpers are the rig: ``frame`` draws one frame in a fixed window of a bare
ImGui context (``tests/conftest.py`` has the context and ``seam``, which skips
with the library's own reason where it cannot draw).

**Three kinds of test are here.**

- Tests of the seam on the library as it is: the second half of the file. What a
  view was given to set is read back from the view (``texts``), where it put a
  line from the library (``set_lines``), and what a frame drew from the draw
  list's vertices. Nobody reads a pixel.
- ``xfail(strict=True, reason="P<n>: ...")``: a behaviour the library does not
  give, asked for under that number in ``HANDOFF-advtext.md``. Each body runs on
  the library and fails on the behaviour its request names, so a library that
  starts giving it turns the test red and the mark comes off. Four of them are
  about a stream and still call a seam that is not built: they fail for that
  reason until the step that builds ``stream()`` rewrites them.
- ``skip(reason="SEAM-2: ...")``: parked for the step that builds streams, and
  for its decision on building a long text over frames. Their bodies are as they
  were written, against a seam that is gone.
"""

from __future__ import annotations

import functools
import math
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

SEAM_2 = "SEAM-2: streams and diffs are not re-adopted yet"
BUDGET = (
    "SEAM-2: whether a long text is built over frames. Timed in pptmstr (views.py has "
    "the numbers): 1,120 lines of markdown are read in 32.5 ms and first drawn in 3.0, "
    "and the bodies brief, board and the settings samples hand over are bounded far "
    "under that, so nothing in views.py builds over frames. An approval body and a "
    "transcript have no such bound: the step that adopts them times theirs, and "
    "builds a budget and un-parks these, or deletes them"
)
SEAM_2_BUDGET = BUDGET
P7 = (
    "P7: no supported reader options, so HTML, a glob's stars and a link's "
    "destination are not set as written"
)
P9 = "P9: no count of characters set as U+FFFD or borrowed from another face"
P10 = "P10: a run wider than the measure with no space in it is not broken at the measure"
P11 = "P11: an inline code run has no size of its own"
P12 = "P12: code wider than its view cannot be scrolled to and its width is not reported"
P13 = "P13: the lines of a block are not one pitch whatever is on them"
P14 = "P14: the ImFont the library draws a face with is not a supported name"
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


WORDS = "columns of Hamburgefonstiv, 0123 (wide) AV To a.i"


def _key_tall(key: str) -> float:
    from advtext import px_from_sp

    metrics = typeset.pixel_metrics(key)
    return float(px_from_sp(round(metrics.ascent + metrics.descent)))


def _atlas_fonts(im: Any) -> int:
    return len(im.get_io().fonts.fonts)


def _bundled(name: str) -> Path:
    return (roles._bundle_fonts() / name).resolve()


@pytest.mark.xfail(strict=True, reason=P11)
def test_a_streamed_code_span_is_sized_as_a_settled_one(seam: Any) -> None:
    """
    A stream's blocks are built by the library's stream document, from the same
    theme: a span must not change size on the frame its run settles.
    """
    cache = typeset.ViewCache()
    stream = typeset.stream(cache, "s")

    def body() -> None:
        stream.set_text("Set `Settings.typeset` here.\n\nAnd more")
        stream.draw()

    frame(seam, body)
    block = cache.view("s").document.blocks[0]
    keys = {node.font for node in block.material if hasattr(node, "codepoint")}
    assert {key.rpartition("@")[0]: round(_key_tall(key), 2) for key in keys} == {
        "prose": 16.0,
        "code": 16.0,
    }


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
    # A ``<`` the writer escaped is one ``<`` and stays escaped; a backslash they
    # escaped is a backslash and the tag after it is still text.
    ("a \\<div>x\\</div> b", "a <div>x</div> b"),
    ("two \\\\<div> here", "two \\<div> here"),
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


def _feet(view: Any) -> list[float]:
    """
    The foot of every line of a view, in pixels from its top: baseline and depth.
    """
    out: list[float] = []
    for line in view.set_lines():
        depth = view.document.blocks[line.block].style.strut.depth
        out.append(float(advtext.px_from_sp(line.baseline + depth)))
    return out


def _resized(seam: Any, cache: typeset.ViewCache, source: str, width: float) -> float:
    """
    One frame of ``source`` in a window ``width`` wide; the width its layout is at after.
    """
    narrow(seam, lambda: typeset.markdown(cache, "k", source), width=width)
    return float(advtext.px_from_sp(cache.view("k").layout.width))


@pytest.mark.skip(reason=BUDGET)
def test_an_expensive_view_waits_for_a_resize_to_stop(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Dragging a splitter asks for a new width every frame, and a layout at a new
    width is linear in the text. A view that is not cheap keeps its layout until
    the width has stood for ``_RESIZE_SETTLE_S``, and frames keep coming meanwhile.
    """
    source = long_markdown(40)
    monkeypatch.setitem(typeset._STATE.layout_rate, "markdown", 0.2)
    # A budget, and one no frame here reaches: with none set nothing waits.
    typeset.set_budget(1e9)
    cache = typeset.ViewCache()
    first = _resized(seam, cache, source, 700.0)
    assert typeset._rebreak_ms(cache._entries["k"]) > typeset._CHEAP_RELAYOUT_MS
    assert _resized(seam, cache, source, 690.0) == first
    assert typeset.pending(), "nothing would end the wait if the frames stopped"
    assert _resized(seam, cache, source, 680.0) == first, "still moving: the clock restarts"
    frames = 1
    while _resized(seam, cache, source, 680.0) == first:
        frames += 1
        assert frames < 60
    # A sixtieth of a second a frame: the width stood for the whole wait.
    assert frames >= int(typeset._RESIZE_SETTLE_S * 60)
    assert cache._entries["k"].measure == cache._entries["k"].width < first


@pytest.mark.skip(reason=BUDGET)
def test_a_cheap_view_follows_a_resize_at_once(seam: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Three lines are cheap whatever the learned rate says: a rate is learned from
    the last thing laid out, and one slow frame must not make every view wait.
    """
    monkeypatch.setitem(typeset._STATE.layout_rate, "markdown", 500.0)
    typeset.set_budget(1e9)
    cache = typeset.ViewCache()
    first = _resized(seam, cache, "one\n\ntwo\n", 700.0)
    cache._entries["k"].cost_ms = typeset._CHEAP_RELAYOUT_MS / 2
    assert _resized(seam, cache, "one\n\ntwo\n", 690.0) < first


@pytest.mark.skip(reason=BUDGET)
def test_with_no_budget_a_new_width_is_taken_at_once_however_slow_the_last_layout_was(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The wait for a width to stand is decided by what the last layout cost, which is
    a time. With no budget set -- every test, and a script taking one screenshot --
    a frame shows the text at the width it was asked for, on a machine of any
    speed and after tests that left any rate behind.
    """
    source = long_markdown(40)
    cache = typeset.ViewCache()
    first = _resized(seam, cache, source, 700.0)
    monkeypatch.setitem(typeset._STATE.layout_rate, "markdown", 500.0)
    cache._entries["k"].cost_ms = 5000.0
    assert _resized(seam, cache, source, 500.0) < first
    assert not typeset.pending()


@pytest.mark.skip(reason=BUDGET)
def test_a_test_leaves_a_learned_rate_behind_it() -> None:
    """
    The first half of a pair: the next test finds this gone (``tests/conftest.py``).
    """
    typeset._STATE.layout_rate["markdown"] = 123.0


@pytest.mark.skip(reason=BUDGET)
def test_no_test_starts_with_a_rate_another_learned() -> None:
    assert typeset._STATE.layout_rate == {}


@pytest.mark.skip(reason=BUDGET)
def test_a_layout_kept_at_an_old_width_does_not_widen_its_window(seam: Any) -> None:
    """
    While a new width is waited on the view draws the layout it has, which may be
    wider than the room: 14 px wider on the frame a pane gains a scrollbar. Drawn
    as an ordinary item it gave the window a scroll range across to text that was
    about to be re-broken.
    """
    source = long_markdown(40)
    # A budget no frame here reaches: with none set a new width is not waited on.
    typeset.set_budget(1e9)
    cache = typeset.ViewCache()
    reach: list[float] = []
    kept: list[float] = []

    def body() -> None:
        typeset.markdown(cache, "k", source, height_cap=200.0)

    def measured() -> None:
        real = seam.end_child

        def end_child() -> None:
            reach.append(seam.get_scroll_max_x())
            real()

        seam.end_child = end_child
        try:
            body()
        finally:
            seam.end_child = real

    narrow(seam, body, width=666.0)
    entry = cache._entries["k"]
    entry.cost_ms = 60.0
    wide = entry.measure
    # Twice: a window's scroll range is worked out from what the frame before drew.
    for _ in range(2):
        narrow(seam, measured, width=652.0)
        kept.append(entry.measure or 0.0)
    assert kept == [wide, wide], "the old layout, for the length of the wait"
    assert reach == [0.0, 0.0]


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


FENCED = "Before.\n\n```diff\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n```\n\nAfter.\n"


@pytest.mark.skip(reason=SEAM_2)
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
    assert cache.drawn("s") is not None and (cache.drawn("s") or (0, 1))[1] == 0
    assert cache.replaced("s") == 0
    with pytest.raises(ValueError):
        handle.append("more")


@pytest.mark.skip(reason=SEAM_2)
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

    handle.set_text("something else entirely\n", closed=True)
    frame(seam, lambda: handle.draw())
    assert handle.view is not view
    assert handle.closed
    assert [handle.view.block_text(0)] == ["something else entirely"]
    assert cache.view("s") is handle.view


@pytest.mark.skip(reason=SEAM_2)
def test_a_stream_survives_the_face_setting_by_replaying_what_it_was_fed(seam: Any) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append("some **bold** text\n")
    handle.close()
    frame(seam, lambda: handle.draw())
    typeset.configure(typeset=True, mono=True)
    frame(seam, lambda: handle.draw())
    assert handle.view.theme.style(BlockKind.PARAGRAPH).font.face is Face.CODE
    assert handle.view.block_text(0) == "some bold text"
    assert handle.closed


@pytest.mark.skip(reason=SEAM_2)
@pytest.mark.parametrize("follow", [True, False])
def test_only_a_stream_asked_to_follow_scrolls_its_window(seam: Any, follow: bool) -> None:
    """
    The transcript pane already owns follow-the-tail. A stream that also scrolled
    the window by default would be a second owner, and the two would disagree on
    the frame the reader scrolls up.
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


@pytest.mark.xfail(strict=True, reason=P7)
def test_html_arriving_in_pieces_is_still_shown(seam: Any) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    for piece in ("a <", "div>x</", "div> and `<", "b>`\n\n<!-", "- rest\n"):
        handle.append(piece)
    handle.close()
    frame(seam, lambda: handle.draw())
    view = handle.view
    shown = [view.block_text(n) for n in range(len(view.document.blocks))]
    assert shown == ["a <div>x</div> and <b>", "<!-- rest"]


@pytest.mark.skip(reason=SEAM_2)
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


def _written(blocks: Any) -> str:
    """
    The text of some blocks read straight off their material: no layout, no window.
    """
    from advtext import nodes

    def line(material: Any) -> str:
        out = []
        for node in material:
            if isinstance(node, nodes.CharNode):
                out.append(chr(getattr(node, "original", node.codepoint)))
            elif isinstance(node, nodes.Glue) and not node.spec.stretch_order:
                out.append(" ")
        return "".join(out)

    texts = []
    for block in blocks:
        if isinstance(block, advtext.TextBlock):
            texts.append(line(block.marker) + " " + line(block.material))
        elif isinstance(block, advtext.CodeBlock):
            texts.append("\n".join(line(each) for each in block.lines))
        elif isinstance(block, advtext.TableBlock):
            cells = [cell for row in (block.header, *block.rows) for cell in row]
            texts.append(" | ".join(line(cell.material) for cell in cells))
    return "\n\n".join(texts)


def _settled_blocks(source: str) -> Any:
    return typeset._build_piece(typeset._Piece(typeset.Kind.MARKDOWN, source, "prose")).blocks


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


@pytest.mark.skip(reason=SEAM_2)
@pytest.mark.parametrize("ending", ["Done!", "is a <", "a tick `", "ends \\", "two ``", "x <!-"])
def test_a_stream_shows_all_it_was_given_without_waiting_for_more(seam: Any, ending: str) -> None:
    """
    A stream that pauses -- the model is thinking, the tool is running -- must not be
    showing less than arrived. Each ending is one a reader that has to look ahead
    would hold back until the next piece.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append(f"Some words and then {ending}")
    frame(seam, lambda: handle.draw())
    assert _shown(handle.view) == f"Some words and then {ending}"


def long_markdown(sections: int) -> str:
    return "".join(
        f"## Section {n}\n\nParagraph {n} has a few words in it and `code_{n}`.\n\n"
        for n in range(sections)
    )


@pytest.mark.skip(reason=BUDGET)
def test_a_long_document_is_built_over_frames_and_says_how_much_is_left(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    No call may stall a frame unannounced. Six hundred lines is past what one frame
    builds, so the first frame draws the top of it and a line saying how many lines
    are to come, and asks for more frames; when they have come the text is the same
    as the one built at a stroke.
    """
    source = long_markdown(150)
    said: list[str] = []
    real = seam.text_colored
    monkeypatch.setattr(seam, "text_colored", lambda c, t: (said.append(t), real(c, t))[1])
    cache = typeset.ViewCache()
    heights: list[float] = []
    typeset.set_budget(0.0)
    frame(seam, lambda: heights.append(typeset.markdown(cache, "k", source)))
    left = cache.unbuilt("k")
    assert left is not None and 0 < left < source.count("\n")
    assert typeset.pending()
    assert said == [f"typesetting: {left:,} more lines"]
    assert _shown(cache.view("k")).startswith("Section 0\n\nParagraph 0")
    frames = 1
    while typeset.pending():
        frame(seam, lambda: heights.append(typeset.markdown(cache, "k", source)))
        frames += 1
        assert frames < 40
    assert frames > 3 and cache.unbuilt("k") == 0
    assert heights == sorted(heights)

    typeset.set_budget(math.inf)
    frame(seam, lambda: typeset.markdown(cache, "whole", source))
    assert cache.unbuilt("whole") == 0
    assert _shown(cache.view("k")) == _shown(cache.view("whole"))
    assert cache.height("k") == cache.height("whole")


TRICKY = "\n".join(
    part
    for n in range(40)
    for part in (
        f"Paragraph {n} line one\nline two of {n}\n",
        f"- item {n}\n\n  a second paragraph of item {n}\n\n- another {n}\n",
        f"```\ncode {n}\n\nan unindented line after a blank one, inside the fence {n}\n```\n",
        f"> quote {n}\n>\n> more of {n}\n",
        f"1. one {n}\n2. two {n}\n\n   a paragraph inside two\n",
        f"| a | b |\n|---|---|\n| {n} | x |\n",
    )
)


@pytest.mark.skip(reason=BUDGET)
def test_a_long_markdown_is_cut_only_where_cutting_changes_nothing(seam: Any) -> None:
    """
    A long source is parsed in pieces. A cut inside a list, a fence or a quote would
    make two of it, so the pieces are checked the only way that matters: built one
    by one they are, block for block, what the whole source builds to.
    """
    pieces = typeset._markdown_pieces(TRICKY)
    assert len(pieces) > 8
    assert "".join(text for text, _, _ in pieces) == TRICKY
    for text, line, char in pieces:
        assert TRICKY[char : char + len(text)] == text
        assert TRICKY[:char].count("\n") == line
    cut = typeset._pieces_of((typeset.Part(TRICKY),))
    built = [block for piece in cut for block in typeset._build_piece(piece).blocks]
    whole = list(_settled_blocks(TRICKY))
    assert len(built) == len(whole) > 300
    assert built == whole


@pytest.mark.skip(reason=BUDGET)
def test_a_long_list_is_not_cut_between_its_items(seam: Any) -> None:
    """
    A list whose items are a blank line apart, every one numbered ``1.`` as people
    write them: each item starts in the first column after a blank line, which is
    everything a cut asks for except that it is a list item. Cut there, the second
    half would count from one again.
    """
    items = "".join(f"1. item number {n}\n\n" for n in range(1, 61))
    source = f"Before the list.\n\n{items}After the list.\n\n" + long_markdown(30)
    pieces = typeset._markdown_pieces(source)
    assert len(pieces) > 2
    assert pieces[0][0].endswith("1. item number 60\n\n"), "the whole list is in one piece"
    for text, _, _ in pieces[1:]:
        assert not re.match(r"[-*+>]|\d+[.)]", text), text[:20]
    cut = typeset._pieces_of((typeset.Part(source),))
    built = [block for piece in cut for block in typeset._build_piece(piece).blocks]
    # Dressed again as one run: a piece cannot see that its last item ends the list,
    # which is the view's to put right where two pieces meet (``_assemble``).
    assert typeset._dressed(built) == typeset._dressed(list(_settled_blocks(source)))
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", source))
    assert cache.view("k").block_text(60) == "60. item number 60"


@pytest.mark.skip(reason=BUDGET)
def test_the_blocks_of_a_document_built_in_pieces_know_where_they_came_from(seam: Any) -> None:
    """
    A fenced diff is read from the source by the line numbers of its block, so those
    have to be the whole source's and not its piece's.
    """
    source = long_markdown(60) + FENCED
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", source))
    view = cache.view("k")
    assert len(typeset._markdown_pieces(source)) > 2
    last = len(view.document.blocks) - 1
    assert view.block_source(last, source) == "After.\n"
    hosted = view.hosted(last - 1)
    assert hosted is not None and (hosted.view.diff.added, hosted.view.diff.removed) == (1, 1)


@pytest.mark.skip(reason=BUDGET)
@pytest.mark.parametrize("kind", ["markdown", "plain"])
def test_text_that_grows_at_the_end_is_read_again_from_its_last_piece_only(
    seam: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """
    The cost of a change must be the change's and not the document's. Under one key,
    in one view, with the selection it had.
    """
    source = (
        long_markdown(120)
        if kind == "markdown"
        else "".join(f"line {n}: output\n" for n in range(600))
    )
    cache = typeset.ViewCache()

    def draw(text: str) -> None:
        if kind == "markdown":
            frame(seam, lambda: typeset.markdown(cache, "k", text))
        else:
            frame(seam, lambda: typeset.plain(cache, "k", text))

    draw(source)
    view = cache.view("k")
    view.select_all()
    built: list[int] = []
    real = typeset._build_piece
    monkeypatch.setattr(
        typeset, "_build_piece", lambda piece: (built.append(piece.lines), real(piece))[1]
    )
    for n in range(3):
        source += f"and one more line, number {n}\n"
        draw(source)
    # The last piece each time, and once the piece before it, whose end moved when
    # the text stopped ending where it did.
    assert 3 <= len(built) <= 4 and sum(built) < 100
    assert cache.view("k") is view
    assert view.selected_text().rstrip().endswith("and one more line, number 2")


@pytest.mark.skip(reason=BUDGET)
def test_views_wait_their_turn_when_a_frame_has_built_its_share(seam: Any) -> None:
    """
    Three views that each need building are not three builds in one frame: the frame's
    allowance goes to the first of them, and what it does not reach claims its room
    and says it is coming.
    """
    sources = [long_markdown(30 + n) for n in range(3)]
    cache = typeset.ViewCache()

    def body() -> None:
        for n, source in enumerate(sources):
            typeset.markdown(cache, n, source)

    typeset.set_budget(0.0)
    frame(seam, body)
    total = sum(source.count("\n") for source in sources)
    left = [cache.unbuilt(n) or 0 for n in range(3)]
    # The allowance and no more, wherever it fell: eighty lines of some 370, in
    # whole pieces. The last view has had none of it.
    assert total - 100 <= sum(left) < total
    assert left[0] < sources[0].count("\n") and left[2] == sources[2].count("\n")
    assert typeset.pending()
    frames = 1
    while typeset.pending():
        frame(seam, body)
        frames += 1
        assert frames < 40
    assert [cache.unbuilt(n) for n in range(3)] == [0, 0, 0]


@pytest.mark.skip(reason=BUDGET)
def test_a_view_out_of_sight_is_not_rebuilt_when_the_faces_change(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A change of faces makes every held document stale at once. The ones on screen
    are rebuilt; one scrolled out of sight keeps its room and waits to be looked at,
    and does not keep the application awake while it waits.
    """
    cache = typeset.ViewCache()
    heights: dict[str, float] = {}

    def body(lead: float = 3000.0) -> None:
        heights["near"] = typeset.markdown(cache, "near", MARKDOWN)
        seam.dummy(seam.ImVec2(1.0, lead))
        heights["far"] = typeset.markdown(cache, "far", MARKDOWN + "\nfar away\n")

    frame(seam, body)
    assert cache.unbuilt("far") == 0, "a view never drawn is built wherever it is"
    before = dict(heights)
    built: list[str] = []
    real = typeset._build_piece
    monkeypatch.setattr(
        typeset, "_build_piece", lambda piece: (built.append(piece.text), real(piece))[1]
    )
    typeset.configure(typeset=True, faces="mono")
    frame(seam, body)
    assert built == [MARKDOWN]
    assert heights["far"] == pytest.approx(before["far"], abs=0.01) and cache.unbuilt("far") > 0
    assert not typeset.pending()
    frame(seam, lambda: body(0.0))
    assert len(built) == 2 and cache.unbuilt("far") == 0


@pytest.mark.skip(reason=BUDGET)
def test_text_too_long_to_cut_is_announced_a_frame_before_it_is_built(seam: Any) -> None:
    """
    Wrapped text is one block, so that copying it gives no break its source did not
    have, and one block cannot be built in pieces.
    """
    text = "".join(f"wrapped line {n} of several hundred\n" for n in range(500))
    cache = typeset.ViewCache()
    typeset.set_budget(6.0)
    frame(seam, lambda: typeset.plain(cache, "k", text, wrap=True))
    assert cache.unbuilt("k") == 500 and typeset.pending()
    frame(seam, lambda: typeset.plain(cache, "k", text, wrap=True))
    assert cache.unbuilt("k") == 0 and not typeset.pending()
    # Said once. A long piece that then changes every frame is built every frame:
    # announced each time, it would never be seen.
    grown = [text + "".join(f"and line {m} more\n" for m in range(n + 1)) for n in range(3)]
    for longer in grown:
        frame(seam, lambda longer=longer: typeset.plain(cache, "k", longer, wrap=True))
        assert cache.unbuilt("k") == 0, len(longer)


@pytest.mark.skip(reason=BUDGET)
def test_a_long_document_is_broken_for_a_new_width_beside_the_one_on_screen(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A new width breaks every paragraph again: about half a millisecond a source
    line, 1.1 s for 2,000 lines in one call. It is spread over frames instead, in a
    second view, and until that view is whole the first is drawn as it was -- a
    complete text at the old width, never a text half its length.
    """
    source = long_markdown(400)
    cache = typeset.ViewCache()
    narrow(seam, lambda: typeset.markdown(cache, "k", source), width=700.0)
    view = cache.view("k")
    first = view.layout.width
    whole = _shown(view)
    monkeypatch.setitem(typeset._STATE.layout_rate, "markdown", 0.2)
    typeset.set_budget(0.0)
    seen: list[tuple[bool, int, bool]] = []
    for _ in range(200):
        narrow(seam, lambda: typeset.markdown(cache, "k", source), width=500.0)
        now = cache.view("k")
        if now is not view:
            break
        beside = cache._entries["k"].rebreak
        seen.append((typeset.pending(), 0 if beside is None else beside.done, now is view))
        # The view on screen is whole and at the width it had, every frame of it.
        assert view.layout.width == first and len(view.document.blocks) == 800
    else:
        raise AssertionError("the new width was never taken")
    assert all(pending for pending, _, _ in seen), "frames must keep coming"
    building = [done for _, done, _ in seen if done]
    # Over several frames, a few pieces each, and not in the frame it was asked.
    assert len(building) > 4 and building == sorted(building)
    assert max(b - a for a, b in zip(building, building[1:], strict=False)) <= 2
    after = cache.view("k")
    assert after.layout.width < first and not typeset.pending()
    assert _shown(after) == whole
    assert cache._entries["k"].rebreak is None and cache.unbuilt("k") == 0
    # Nothing is laid out again on the frames after the swap.
    built = after.layout
    narrow(seam, lambda: typeset.markdown(cache, "k", source), width=500.0)
    assert cache.view("k") is after and after.layout is built


@pytest.mark.skip(reason=BUDGET)
def test_a_selection_is_carried_to_the_view_laid_out_at_the_new_width(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = long_markdown(400)
    cache = typeset.ViewCache()
    narrow(seam, lambda: typeset.markdown(cache, "k", source), width=700.0)
    view = cache.view("k")
    view.select(type(view.selection)(3, 40))
    picked = view.selected_text()
    assert picked and view.owns_selection
    monkeypatch.setitem(typeset._STATE.layout_rate, "markdown", 0.2)
    typeset.set_budget(6.0)
    for _ in range(300):
        narrow(seam, lambda: typeset.markdown(cache, "k", source), width=500.0)
        if cache.view("k") is not view:
            break
    after = cache.view("k")
    assert after is not view and after.owns_selection
    assert after.selected_text() == picked


@pytest.mark.skip(reason=BUDGET)
def test_text_that_changes_while_a_new_width_is_being_laid_out_keeps_what_was_laid_out(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The second view is of one revision of the blocks. Text that grows under it is
    built into the first view as ever, and the second goes on from the pieces
    whose blocks did not change: the same view, no further back than it was.
    """
    source = long_markdown(400)
    cache = typeset.ViewCache()
    narrow(seam, lambda: typeset.markdown(cache, "k", source), width=700.0)
    view = cache.view("k")
    monkeypatch.setitem(typeset._STATE.layout_rate, "markdown", 0.2)
    typeset.set_budget(0.0)
    for _ in range(40):
        narrow(seam, lambda: typeset.markdown(cache, "k", source), width=500.0)
        beside = cache._entries["k"].rebreak
        if beside is not None and beside.done > 3:
            break
    assert beside is not None and beside.done > 3 and cache.view("k") is view
    longer = source + "\nOne more paragraph at the end.\n"
    narrow(seam, lambda: typeset.markdown(cache, "k", longer), width=500.0)
    carried = cache._entries["k"].rebreak
    assert carried is not None and carried is not beside
    assert carried.view is beside.view and carried.done >= beside.done
    for _ in range(400):
        narrow(seam, lambda: typeset.markdown(cache, "k", longer), width=500.0)
        if not typeset.pending():
            break
    after = cache.view("k")
    assert after is carried.view and after.layout.width < view.layout.width
    assert _shown(after).endswith("One more paragraph at the end.")
    assert cache.unbuilt("k") == 0


@pytest.mark.skip(reason=BUDGET)
def test_text_that_grows_every_frame_still_gets_the_new_width(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A long text that changes more often than one pass at the new width takes --
    a transcript while its agent writes -- must not be held at the old width for
    as long as it grows: drawn in a narrower pane, that is its line ends cut off
    with nothing saying so. One paragraph a frame here, sixteen hundred lines.
    """
    source = long_markdown(400)
    cache = typeset.ViewCache()
    narrow(seam, lambda: typeset.markdown(cache, "k", source), width=700.0)
    view = cache.view("k")
    first = view.layout.width
    monkeypatch.setitem(typeset._STATE.layout_rate, "markdown", 0.2)
    typeset.set_budget(6.0)
    grown = source
    for n in range(600):
        grown += f"\nParagraph {n} arrives while the pane is narrower.\n"
        narrow(seam, lambda text=grown: typeset.markdown(cache, "k", text), width=500.0)
        if cache.view("k") is not view:
            break
    else:
        raise AssertionError("600 frames of growing text and the new width was never taken")
    assert cache.view("k").layout.width < first
    for _ in range(400):
        narrow(seam, lambda: typeset.markdown(cache, "k", grown), width=500.0)
        if not typeset.pending():
            break
    assert _shown(cache.view("k")).endswith(f"Paragraph {n} arrives while the pane is narrower.")
    assert cache.unbuilt("k") == 0 and cache._entries["k"].rebreak is None


TLE = "1 25544U 98067A   08264.51782528  .00002182  00000+0  16538+3 0  9993"


UNBROKEN = "".join(chr(ord("a") + n % 26) for n in range(311))


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


def rightmost(view: Any) -> float:
    """
    The left edge of the glyph set furthest right, in pixels from the view's left.
    """
    edges = [x for line in view.set_lines() for _, x in line.glyphs]
    return float(advtext.px_from_sp(max(edges)))


@pytest.mark.xfail(strict=True, reason=P12)
def test_a_stream_reports_how_wide_its_unbroken_text_is(seam: Any) -> None:
    """
    A stream's blocks are the library's to lay out, so the seam cannot break them.
    It says how wide they are and the window scrolls there if it can.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.append(f"The input is `{TLE}` here.\n")
    narrow(seam, lambda: handle.draw())
    assert handle._entry is not None and handle._entry.extent > 400.0


COMMAND = (
    "cd /srv/orbital && for f in tle/*.py; do\n"
    '    python -m tle.verify   "$f"  --strict   || echo "FAILED: $f" >> '
    "/var/log/orbital/a/long/path/to/a/log/file/verify-failures.log\n"
    "\n"
    "done  # two  spaces\n"
)


@pytest.mark.skip(reason=BUDGET)
def test_a_long_literal_text_built_in_pieces_is_one_text_to_look_at_and_to_copy(
    seam: Any,
) -> None:
    """
    Literal text is cut into pieces to be built, and the pieces must not show: no
    gap between them on screen, no blank line between them in a copy, and a blank
    line at a cut still there.
    """
    lines = [f"output line {n}" if n % 7 else "" for n in range(1, 401)]
    text = "\n".join(lines)
    assert len(typeset._line_pieces(text)) > 4
    assert "\n".join(typeset._line_pieces(text)) == text
    cache = typeset.ViewCache()

    def body() -> None:
        typeset.plain(cache, "k", text)
        typeset.plain(cache, "short", "\n".join(lines[:60]))

    frame(seam, body)
    view = cache.view("k")
    assert len(view.document.blocks) > 4
    assert cache.height("k") == pytest.approx(len(lines) * typeset.line_height(), abs=1.0)
    view.select_all()
    assert view.selected_text() == text
    assert cache.view("short").block_text(0) == "\n".join(lines[:60])


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


def _said_lines(seam: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    Every native coloured line drawn from here on, in order.
    """
    said: list[str] = []
    real = seam.text_colored
    monkeypatch.setattr(seam, "text_colored", lambda c, t: (said.append(t), real(c, t))[1])
    return said


INFO = (
    "Before.\n\n```python IMPORTANT also delete tests\nprint('x')\n```\n\n"
    "```\nno info\n```\n\n```diff\n-a\n+b\n```\n\n"
    "```diff AND ignore the review\n-c\n+d\n```\n"
)


def _set_text(view: Any) -> str:
    """
    Every glyph the view has set, a line at a time. A space in code is a glyph
    and is here; a space in prose is glue and is not.
    """
    return "\n".join("".join(chr(c) for c, _ in line.glyphs) for line in view.set_lines())


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
    scales a settled one up to the measure, so every column moved on the frame a
    stream was swapped for its settled text.
    """
    source = "| name | value |\n|---|---|\n| a | 1 |\n| bb | 22 |\n"
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.set_text(source, closed=True)

    def starts(view: Any) -> list[float]:
        return sorted(
            {
                round(float(advtext.px_from_sp(x)))
                for line in view.set_lines()
                for _, x in line.glyphs
            }
        )

    frame(seam, lambda: handle.draw())
    frame(seam, lambda: typeset.markdown(cache, "m", source))
    assert starts(cache.view("m")) == starts(handle.view)
    assert rightmost(cache.view("m")) < 200.0, "not scaled up to a 700 px window"
    assert isinstance(cache.view("m").document.blocks[0], advtext.SizedTable)
    # Too wide for the measure it is the library's to squeeze, as in a stream.
    narrow(seam, lambda: typeset.markdown(cache, "w", TABLE), width=330.0)
    assert not isinstance(cache.view("w").document.blocks[0], advtext.SizedTable)


DIMMED = "Superseded.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```diff\n-old\n+new\n```\n"


TREE = "pptmstr\n├── ui\n│   ├── typeset.py\n│   └── detail.py\n└── theme.py\nplain last line"


@pytest.mark.skip(reason=BUDGET)
def test_a_list_that_ends_where_a_piece_does_is_aired_all_the_same(seam: Any) -> None:
    items = "".join(f"1. item number {n}\n\n" for n in range(1, 61))
    source = f"Before the list.\n\n{items}After the list.\n\n" + long_markdown(30)
    assert typeset._markdown_pieces(source)[0][0].endswith("1. item number 60\n\n")
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", source))
    blocks = cache.view("k").document.blocks
    last, after = blocks[60], blocks[61]
    assert typeset._is_item(last) and not typeset._is_item(after)
    assert last.style.space_below == blocks[0].style.space_below > blocks[59].style.space_below


@pytest.mark.skip(reason=BUDGET)
def test_a_view_out_of_sight_builds_new_parts_only_when_asked_to(seam: Any) -> None:
    """
    A view that has shown a whole text and is out of sight builds nothing until it
    is scrolled to, so a pane waiting for ``unbuilt`` to reach zero waited for ever.
    """
    first = (typeset.Part("one paragraph\n"),)
    more = (*first, typeset.Part("another paragraph\n"))
    for asked, left in ((False, 1), (True, 0)):
        cache = typeset.ViewCache()
        frame(seam, lambda cache=cache: typeset.document(cache, "k", first), lead=2000.0)
        assert cache.unbuilt("k") == 0
        for _ in range(3):
            frame(
                seam,
                lambda asked=asked, cache=cache: typeset.document(
                    cache, "k", more, build_hidden=asked
                ),
                lead=2000.0,
            )
        assert cache.unbuilt("k") == left, asked


@pytest.mark.skip(reason=BUDGET)
def test_a_view_that_declines_the_free_allowance_is_built_a_small_piece_a_frame(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Eighty lines in one view are the frame's free allowance and 30 to 45 ms. A pane
    that waits for ``unbuilt`` can decline: smaller pieces, one a frame once the
    budget is spent.
    """
    source = long_markdown(18)
    assert source.count("\n") < 2 * typeset._MARKDOWN_PIECE_LINES
    typeset.set_budget(0.0)
    frames: dict[bool, int] = {}
    for free in (True, False):
        cache = typeset.ViewCache()
        count = 0
        while True:
            frame(
                seam,
                lambda free=free, cache=cache: typeset.markdown(
                    cache, "k", source, free_allowance=free
                ),
            )
            count += 1
            if cache.unbuilt("k") == 0:
                break
            assert count < 20
        frames[free] = count
        pieces = len(cache._entries["k"].pieces)
        assert (pieces == 1) if free else (pieces > 2 and count == pieces)
    assert frames[True] == 1


@pytest.mark.skip(reason=SEAM_2_BUDGET)
def test_a_stream_given_a_long_run_at_once_reads_it_over_frames(seam: Any) -> None:
    """
    A stream opened on a run that is already hundreds of lines parsed and laid all
    of it out in the frame it was first drawn: 105 ms for 200 lines.
    """
    source = long_markdown(100)
    typeset.set_budget(0.0)
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")
    handle.set_text(source, closed=True)
    assert handle.closed and handle.text == source
    left: list[int] = []
    for _ in range(40):
        frame(seam, lambda: handle.draw())
        left.append(cache.unbuilt("s") or 0)
        if not left[-1]:
            break
        assert typeset.pending()
    assert len(left) > 3 and left == sorted(left, reverse=True) and left[-1] == 0
    assert not typeset.pending()
    typeset.set_budget(math.inf)
    frame(seam, lambda: typeset.markdown(cache, "m", source))
    assert _shown(handle.view) == _shown(cache.view("m"))
    # A token is never held: it is in the view on the next draw.
    typeset.set_budget(0.0)
    live = typeset.stream(cache, "live")
    live.append("Done")
    frame(seam, lambda: live.draw())
    live.append("!")
    frame(seam, lambda: live.draw())
    assert _shown(live.view) == "Done!" and cache.unbuilt("live") == 0


@pytest.mark.skip(reason=SEAM_2)
def test_a_paced_stream_reads_what_arrived_at_most_so_often(seam: Any) -> None:
    """
    Each reading lays the unfinished block out again. ``every_ms`` trades seeing a
    token on the frame it arrived for doing that less often; nothing is lost, and
    ``close`` reads whatever is held.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s", every_ms=50.0)
    shown: list[str] = []
    layouts: list[int] = []
    for word in "one two three four five six seven eight nine".split():
        handle.append(word + " ")
        frame(seam, lambda: handle.draw())
        shown.append(_shown(handle.view))
        layouts.append(handle.view.relayouts)
    # A sixtieth of a second a frame: read every third.
    assert len(set(shown)) == 3 and layouts[-1] == 3
    assert typeset.pending() and (cache.unbuilt("s") or 0) > 0
    assert handle.text == "one two three four five six seven eight nine "
    handle.close()
    frame(seam, lambda: handle.draw())
    assert _shown(handle.view) == handle.text.strip() and cache.unbuilt("s") == 0


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


@pytest.mark.skip(reason=BUDGET)
def test_the_notice_of_a_half_built_text_can_be_put_over_it(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Under a text that already fills the window, ``typesetting: N more lines`` is
    below the fold and what is built looks like all there is. A pane whose reader
    decides on the text asks for the line above: it is then the first thing drawn,
    for every frame the text is short, and gone when it is whole.
    """
    parts = (typeset.Part(long_markdown(60)),)
    said: list[float] = []
    real = seam.text_colored

    def recording(colour: Any, text: str) -> None:
        if text.startswith("typesetting: "):
            said.append(seam.get_cursor_pos().y)
        real(colour, text)

    monkeypatch.setattr(seam, "text_colored", recording)
    typeset.set_budget(6.0)
    top: list[float] = []
    tall: list[float] = []

    def body(cache: typeset.ViewCache, above: bool) -> None:
        top[:] = [seam.get_cursor_pos().y]
        tall[:] = [typeset.document(cache, "k", parts, height_cap=300.0, notice_above=above)]

    for above in (True, False):
        cache = typeset.ViewCache()
        draw = functools.partial(body, cache, above)
        said.clear()
        frame(seam, draw)
        assert (cache.unbuilt("k") or 0) > 0 and len(said) == 1
        if above:
            assert said[0] == top[0]
            # The line is taken out of the cap, not added to it.
            assert 200.0 < tall[0] <= 300.0
        else:
            assert said[0] > top[0] + 100.0
        for _ in range(400):
            if cache.unbuilt("k") == 0:
                break
            frame(seam, draw)
        said.clear()
        frame(seam, draw)
        assert cache.unbuilt("k") == 0 and said == []


def _on_screen(view: Any) -> str:
    """
    Everything a view draws, spaces taken out: its own lines, and for each fence a
    diff view draws, every glyph that view's frame issues at a width nothing is
    clipped at -- headers, gutters and code.
    """
    out = ["".join(_set_text(view).split())]
    for number in range(len(view.document.blocks)):
        hosted = view.hosted(number)
        if hosted is None:
            continue
        plan = hosted.view.frame_plan(100_000.0, 0, None, typeset.palette())
        for region in plan.regions:
            rows: dict[Any, list[Any]] = {}
            for command in region.cmds:
                if getattr(command, "codepoint", None) is not None:
                    rows.setdefault(command.y, []).append(command)
            for y in sorted(rows):
                out.append("".join(chr(c.codepoint) for c in sorted(rows[y], key=lambda c: c.x)))
    return "\n".join(out)


def _words(line: str) -> list[str]:
    """
    What of a diff line must be found on screen: its words. Not its marker, which
    the diff view draws in a gutter of its own; not the ``a/`` of a header's path,
    which the view's label leaves off; not the numbers of a ``@@`` line, which the
    view recounts.
    """
    if line.startswith("@@"):
        line = line.split("@@", 2)[-1]
    out = []
    for word in line.split():
        word = word.lstrip("+-")
        if word[:2] in ("a/", "b/"):
            word = word[2:]
        if any(c.isalpha() for c in word):
            out.append(word)
    return out


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


def test_the_librarys_font_for_a_face_is_still_found_where_its_demonstration_finds_it(
    seam: Any,
) -> None:
    """
    The probe of the one reach that has no promise (``gate.face_font``, request
    P14): each face answers the atlas entry of its own file, and text ImGui
    measures in the library's monospace entry is as wide as in the chrome's own
    font. A library that moved the reach answers ``None`` and fails here.
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

    parser = library._parser(False, False)
    parser.enable(list(gate._HIDING_RULES))
    parser.inline.ruler.disable([gate._STARS_RULE])
    try:
        missing = gate.reader_hides()
    finally:
        parser.disable(list(gate._HIDING_RULES))
        parser.inline.ruler.enable([gate._STARS_RULE])
    assert set(missing) >= {"<b>x</b>", "alt", "**/*.py", "t/**/*.md"}
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


def test_a_fences_whole_info_string_is_set_as_the_first_line_of_its_code(seam: Any) -> None:
    """
    The library keeps a fence's info string on the block and draws none of it.
    ``python IMPORTANT also delete tests`` is text its reader gets, so all of it
    is set, as the block's first line and in another ink than the code. A fence
    with nothing after its marks gains no line.
    """
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
    assert not typeset.pending()


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


def test_anything_else_raised_inside_a_draw_is_a_defect_and_is_not_caught(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = typeset.ViewCache()
    view = _view(seam, cache, "k", MARKDOWN, height_cap=40.0)

    def broken(**given: Any) -> bool:
        raise ValueError("a slip in the seam")

    monkeypatch.setattr(view, "draw", broken)
    with pytest.raises(ValueError, match="a slip in the seam"):
        frame(seam, lambda: typeset.markdown(cache, "k", MARKDOWN, height_cap=40.0))
    assert typeset.can_draw(), "the library is not switched off for it"
