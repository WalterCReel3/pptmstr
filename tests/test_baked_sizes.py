"""
A font size on ImGui's atlas is never moved, so its glyphs are its own size's
(``theme.keep_baked_sizes``).

**The defect is made here, on purpose, and the tests are of the guard against
it.** Dear ImGui's FreeType loader remembers the baked size it last made current
for a font file by that size's address in the atlas's pool. When the atlas runs
out of room it discards the sizes nothing drew in two frames, and at the next
frame moves the ones behind a gap down. ``moved_glyphs`` arranges the one thing
that needs: a size nobody draws ahead of two sizes of one font made one after
the other, the first of the two the last to have had a glyph loaded. Room is
made with the call ImGui makes itself on a glyph that does not fit; the second
size lands on the address the loader remembers for the first, and every glyph
it loads afterwards is the first size's.

Nothing is read from pixels and no window is opened: a context with no backend
rasterises into an atlas like any other. A glyph is compared, by the width and
height of its bitmap and its advance, with the same character of the same font
at the same size after every baked size has been dropped and that one drawn
alone.

``test_the_wheel_still_moves_a_size_onto_a_remembered_address`` holds the
fixture to the defect: **when it fails, the pinned wheel's loader no longer has
it and the guard is there for nothing.** The tests beside it are the same
fixture with the guard in front of every frame.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pptmstr import theme
from pptmstr.ui import typeset
from pptmstr.ui.typeset import gate

FIRST = "abcdefgh"
LATER = "ABCDEFGHIJKLMNOP"
"""
The characters the second size has before room is made, and the ones it loads after.
"""

DECOY_PX = 31.0

ROBOTO = ("roboto-bold", 16.0, 19.0)
"""
The operator's report: a heading (19 px at a 16 px body) and the bold of running
text, both from the proportional bold file.
"""

CHROME = ("chrome", 13.0, 18.0)
"""
The UI font at two sizes pptmstr draws it at with no library in the process: the
small text of the rail, the inbox and the health line (``_SMALL_FONT``, 12.5,
which ImGui bakes at 13), and a label at the largest text scale offered, 1.125
of 16.
"""

Dims = tuple[float, float, float]


@pytest.fixture
def atlas() -> Iterator[Any]:
    """
    A context of this test's own, with no backend and no typesetting library:
    Inconsolata first, as the application has it, then Roboto's bold.
    """
    bundle = pytest.importorskip("imgui_bundle")
    im = bundle.imgui
    fonts = Path(bundle.__file__).parent / "assets" / "fonts"
    before = im.get_current_context()
    context = im.create_context()
    im.set_current_context(context)
    io = im.get_io()
    io.display_size = im.ImVec2(1200.0, 800.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= im.BackendFlags_.renderer_has_textures.value
    io.fonts.add_font_from_file_ttf(str(fonts / "Inconsolata-Medium.ttf"), 16.0)
    io.fonts.add_font_from_file_ttf(str(fonts / "Roboto" / "Roboto-Bold.ttf"), 16.0)
    kept = theme._KEPT.under
    theme._KEPT.under = None
    yield im
    theme._KEPT.under = kept
    im.destroy_context(context)
    if before is not None:
        im.set_current_context(before)


def font_of(im: Any, which: str) -> Any:
    fonts = im.get_io().fonts.fonts
    return fonts[0] if which == "chrome" else fonts[1]


def frame(im: Any, body: Callable[[], None], before: Callable[[], object] | None = None) -> None:
    if before is not None:
        before()
    im.new_frame()
    im.set_next_window_pos(im.ImVec2(10.0, 10.0))
    im.set_next_window_size(im.ImVec2(900.0, 700.0))
    im.begin("##baked")
    try:
        body()
    finally:
        im.end()
        im.render()


def line(im: Any, font: Any, size: float, text: str) -> None:
    im.push_font(font, size)
    im.text_unformatted(text)
    im.pop_font()


def dims(font: Any, size: float, chars: str) -> dict[str, Dims]:
    baked = font.get_font_baked(size)
    out = {}
    for char in chars:
        glyph = baked.find_glyph_no_fallback(ord(char))
        assert glyph is not None, f"{char!r} at {size:g} px was never loaded"
        out[char] = (
            float(glyph.x1 - glyph.x0),
            float(glyph.y1 - glyph.y0),
            float(glyph.advance_x),
        )
    return out


def drop_everything(im: Any) -> None:
    """
    Every baked size of every font discarded, and the frame after it over: the
    pool then holds what that frame made, from its start.
    """
    fonts = im.get_io().fonts
    for font in fonts.fonts:
        im.internal.im_font_atlas_font_discard_bakes(fonts, font, 0)
    frame(im, lambda: None)


def fresh(im: Any, font: Any, size: float, chars: str) -> dict[str, Dims]:
    """
    The glyphs of ``chars`` when the size is the only one of its font.
    """
    drop_everything(im)
    frame(im, lambda: line(im, font, size, chars))
    return dims(font, size, chars)


def moved_glyphs(
    im: Any,
    font: Any,
    remembered: float,
    moved: float,
    before: Callable[[], object] | None,
    draw: Callable[[float, str], None] | None = None,
) -> tuple[list[str], list[str]]:
    """
    Make the conditions of the defect for ``font`` and answer the characters of
    ``LATER`` at ``moved`` px whose glyph is not that size's own, and of those
    the ones that are exactly the glyph of ``remembered`` px.

    ``before`` runs ahead of every frame, where the application's guard does.
    ``draw`` sets a line at a size in ``font``; ImGui's own text by default.
    """
    if draw is None:

        def draw(size: float, text: str) -> None:
            line(im, font, size, text)

    drop_everything(im)

    def made_in_order() -> None:
        # A size nothing draws again, then the two, whoever loads their glyphs.
        line(im, None, DECOY_PX, "decoy")
        line(im, font, remembered, "a")
        line(im, font, moved, "a")

    frame(im, made_in_order, before)

    def both(small: str, large: str) -> Callable[[], None]:
        def body() -> None:
            draw(remembered, small)
            draw(moved, large)

        return body

    frame(im, both("abc", FIRST), before)
    frame(im, both("abc", FIRST), before)
    # The last glyph loaded before room is made is the remembered size's.
    frame(im, both("abcd", FIRST), before)

    def room() -> None:
        both("abcd", FIRST)()
        im.internal.im_font_atlas_texture_make_space(im.get_io().fonts)

    frame(im, room, before)
    for _ in range(3):
        frame(im, both("abcd", FIRST + LATER), before)
    got = dims(font, moved, FIRST + LATER)
    small = dims(font, remembered, "abcd")
    own = fresh(im, font, moved, FIRST + LATER)
    other = fresh(im, font, remembered, FIRST + LATER)
    assert small == {char: other[char] for char in "abcd"}, "the remembered size was itself wrong"
    wrong = [char for char in FIRST + LATER if got[char] != own[char]]
    return wrong, [char for char in wrong if got[char] == other[char]]


def held(im: Any) -> dict[int, list[tuple[float, float]]]:
    """
    The baked sizes the atlas holds, by the font's place in ``io.fonts``.
    """
    fonts = im.get_io().fonts
    return {
        index: sizes
        for index, font in enumerate(fonts.fonts)
        if (sizes := sorted(theme.baked_sizes(fonts, font)))
    }


@pytest.mark.parametrize(("which", "remembered", "moved"), [ROBOTO, CHROME])
def test_the_wheel_still_moves_a_size_onto_a_remembered_address(
    atlas: Any, which: str, remembered: float, moved: float
) -> None:
    """
    With nothing in front of the frames, every glyph the moved size loads after
    room is made is the other size's. The chrome's font is no different from
    the library's: this is ImGui's, with no typesetting library in the process.
    """
    wrong, as_other = moved_glyphs(atlas, font_of(atlas, which), remembered, moved, None)
    assert wrong == list(LATER)
    assert as_other == wrong


@pytest.mark.parametrize(("which", "remembered", "moved"), [ROBOTO, CHROME])
def test_no_glyph_is_another_sizes_while_every_size_is_kept(
    atlas: Any, which: str, remembered: float, moved: float
) -> None:
    font = font_of(atlas, which)
    wrong, _ = moved_glyphs(atlas, font, remembered, moved, theme.keep_baked_sizes)
    assert wrong == []


def test_a_kept_size_is_found_and_not_made_again(atlas: Any) -> None:
    """
    Marking a size used asks for it by its own size and density. Asked for at
    the font's current density instead, a size baked for a display twice as
    fine would not be found: each would be made again at the other density,
    and the one meant would go unmarked.
    """
    font = font_of(atlas, "roboto-bold")
    frame(atlas, lambda: None)
    font.get_font_baked(19.0, 2.0)
    font.get_font_baked(23.0)
    before = held(atlas)
    assert before[1] == [(19.0, 2.0), (23.0, 1.0)]
    counts = set()
    for _ in range(5):
        counts.add(theme.keep_baked_sizes())
        frame(atlas, lambda: None)
    assert held(atlas) == before
    assert counts == {sum(len(sizes) for sizes in before.values())}
    closest = atlas.internal.im_font_atlas_baked_get_closest_match
    fonts = atlas.get_io().fonts
    theme.keep_baked_sizes()
    assert closest(fonts, font, 19.5, 2.0).last_used_frame == atlas.get_frame_count()
    # Asked for at the other density, 19 px is not there to be preferred.
    assert closest(fonts, font, 19.5, 1.0).size == 23.0


def test_a_size_nothing_draws_is_still_there_after_room_is_made(atlas: Any) -> None:
    """
    What keeping is: ImGui discards what was not used in two frames, and with
    the guard in front of the frame there is no such size.
    """
    font = font_of(atlas, "roboto-bold")
    fonts = atlas.get_io().fonts

    def room() -> None:
        atlas.internal.im_font_atlas_texture_make_space(fonts)

    for guard, survives in ((theme.keep_baked_sizes, True), (None, False)):
        drop_everything(atlas)
        frame(atlas, lambda: line(atlas, font, 23.0, FIRST), guard)
        for _ in range(4):
            frame(atlas, lambda: None, guard)
        frame(atlas, room, guard)
        frame(atlas, lambda: None, guard)
        assert ((23.0, 1.0) in theme.baked_sizes(fonts, font)) is survives


def test_another_text_size_lets_go_of_the_sizes_before_it(atlas: Any) -> None:
    """
    Twenty sizes one after the other, as an operator stepping through the text
    scales leaves them. Kept under one generation the atlas holds them all;
    told each time that the sizes changed, it holds the one in use and no glyph
    of it is another size's.
    """
    font = font_of(atlas, "roboto-bold")

    def walk(generation_of: Callable[[int], int]) -> int:
        drop_everything(atlas)
        theme._KEPT.under = None
        for step in range(20):

            def body(size: float = 20.0 + step) -> None:
                line(atlas, font, size, FIRST)

            def guard(step: int = step) -> None:
                theme.keep_baked_sizes(generation_of(step))

            for _ in range(3):
                frame(atlas, body, guard)
        return len(held(atlas)[1])

    assert walk(lambda step: 0) == 20
    assert walk(lambda step: step) == 1
    assert dims(font, 39.0, FIRST) == fresh(atlas, font, 39.0, FIRST)


def test_the_ui_fonts_own_size_is_a_change_of_sizes_too(
    atlas: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A display of another density changes what every pane draws at, with or
    without the library to move the seam's epoch for it.
    """
    font = font_of(atlas, "roboto-bold")
    for _ in range(3):
        frame(atlas, lambda: line(atlas, font, 19.0, FIRST), lambda: theme.keep_baked_sizes(7))
    assert (19.0, 1.0) in held(atlas)[1]
    monkeypatch.setattr(atlas, "get_font_size", lambda: 32.0)
    assert theme.keep_baked_sizes(7) == 0
    assert held(atlas) == {}
    monkeypatch.undo()
    frame(atlas, lambda: line(atlas, font, 19.0, FIRST), lambda: theme.keep_baked_sizes(7))
    assert (19.0, 1.0) in held(atlas)[1]
    atlas.get_io().display_framebuffer_scale = atlas.ImVec2(2.0, 2.0)
    assert theme.keep_baked_sizes(7) == 0
    assert held(atlas) == {}


def test_nothing_is_touched_before_the_first_frame_or_without_a_context(
    atlas: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The atlas is built by the first frame; asked for a baked size before that,
    ImGui has no pool to look in.
    """
    font = font_of(atlas, "roboto-bold")
    assert atlas.get_frame_count() == 0
    assert theme.keep_baked_sizes() == 0
    assert theme._KEPT.under is None
    frame(atlas, lambda: line(atlas, font, 19.0, FIRST))
    monkeypatch.setattr(atlas, "get_current_context", lambda: None)
    assert theme.keep_baked_sizes() == 0
    monkeypatch.undo()
    assert theme.keep_baked_sizes() == 2


# -- with the typesetting library ----------------------------------------------

DOCUMENT = (
    "Two findings follow, **each under a numbered heading** as the reply had them.\n\n"
    '## 4. "Lingua franca"\n\n'
    "- **The phrase appears in no source I found.**\n\n```py\nx = 1\n```\n"
)


def seam_frame(im: Any, body: Callable[[], None], guard: bool = True) -> None:
    """
    A frame as the application begins one: the seam's turn, then the guard.
    """

    def before() -> None:
        typeset.pre_frame()
        if guard:
            theme.keep_baked_sizes(gate.epoch())

    frame(im, body, before)


def document(cache: Any) -> Callable[[], None]:
    def body() -> None:
        typeset.document(cache, "doc", (typeset.Part(DOCUMENT),), rests=True, apart=True)

    return body


def drawing(im: Any) -> None:
    assert typeset.can_draw() and gate._GATE.stopping is None, typeset.unavailable_reason()


@pytest.fixture
def kept(seam: Any) -> Iterator[Any]:
    """
    The seam, with the guard's memory this test's own and the atlas emptied of
    what the module's other tests baked.
    """
    before = theme._KEPT.under
    theme._KEPT.under = None
    drop_everything(seam)
    yield seam
    theme._KEPT.under = before


@pytest.mark.parametrize("guarded", [False, True])
def test_the_librarys_bold_at_a_headings_size_and_the_bodys(kept: Any, guarded: bool) -> None:
    """
    The fixture on the atlas entry the library draws bold with, with the
    library loaded: all of the later glyphs without the guard, none with it.
    """
    bold = gate.face_font("PROSE_BOLD")
    assert bold is not None
    guard = (lambda: theme.keep_baked_sizes(gate.epoch())) if guarded else None
    wrong, as_other = moved_glyphs(kept, bold, 16.0, 19.0, guard)
    assert wrong == ([] if guarded else list(LATER))
    assert as_other == wrong
    drawing(kept)


@pytest.mark.parametrize("guarded", [False, True])
def test_glyphs_the_library_loads_are_the_moved_sizes_too(kept: Any, guarded: bool) -> None:
    """
    The same with every glyph loaded by a draw of the library's own view and
    none by ImGui's text: bold words as a paragraph, at the pixel height of the
    body and of a heading. Whoever asks the atlas for the glyph gets the wrong one.
    """
    from advtext import DARK_PALETTE, Align, Faces, Spacing, Theme, TypesetView

    bold = gate.face_font("PROSE_BOLD")
    views: dict[tuple[float, str], Any] = {}

    def draw(size: float, text: str) -> None:
        view = views.get((size, text))
        if view is None:
            sized = Theme.at_pixel_height(
                round(size), faces=Faces(), align=Align.LEFT, spacing=Spacing.FRENCH
            )
            view = views[(size, text)] = TypesetView(
                f"**{text}**", theme=sized, palette=DARK_PALETTE
            )
        view.draw(palette=DARK_PALETTE, width_px=800.0)

    guard = (lambda: theme.keep_baked_sizes(gate.epoch())) if guarded else None
    try:
        wrong, as_other = moved_glyphs(kept, bold, 16.0, 19.0, guard, draw)
    finally:
        for view in views.values():
            view.close()
    assert wrong == ([] if guarded else list(LATER))
    assert as_other == wrong
    drawing(kept)


def test_a_document_is_drawn_from_its_own_sizes_across_a_change_of_scale(kept: Any) -> None:
    """
    The library's views through a change of the text scale: the sizes of the
    scale before leave the atlas, the document is drawn again, and every glyph
    the atlas then holds for the library's bold is its size's own.
    """
    cache = typeset.ViewCache()
    for _ in range(3):
        seam_frame(kept, document(cache))
    drawing(kept)
    at_one = held(kept)
    typeset.configure(typeset=True, scale=1.25)
    for _ in range(3):
        seam_frame(kept, document(cache))
    drawing(kept)
    at_larger = held(kept)
    assert at_larger != at_one
    assert sum(map(len, at_larger.values())) <= sum(map(len, at_one.values()))
    typeset.configure(typeset=True, scale=1.0)
    for _ in range(3):
        seam_frame(kept, document(cache))
    drawing(kept)
    assert held(kept) == at_one
    bold = gate.face_font("PROSE_BOLD")
    got = {}
    for size, _ in theme.baked_sizes(kept.get_io().fonts, bold):
        chars = "".join(
            chr(glyph.get_codepoint())
            for glyph in bold.get_font_baked(size).glyphs
            if glyph.is_visible
        )
        got[size] = (chars, dims(bold, size, chars))
    # The heading's words at its size, the strong words at the body's.
    assert set("Linguafranca") <= set(got[19.0][0]) and set("Thephrase") <= set(got[16.0][0])
    for size, (chars, found) in got.items():
        assert found == fresh(kept, bold, size, chars), f"bold at {size:g} px"
    cache.clear()


def test_a_family_loaded_again_leaves_its_old_fonts_holding_nothing(kept: Any) -> None:
    """
    Each load of the library's faces leaves the fonts of the load before on the
    atlas. Nothing draws from them again; they are not kept, they are not
    reached through a reference from before the load, and the count of sizes
    held is the same however many times the family has changed.
    """
    cache = typeset.ViewCache()
    fonts = kept.get_io().fonts
    for _ in range(3):
        seam_frame(kept, document(cache))
    first = held(kept)
    entries = len(fonts.fonts)
    counts = []
    try:
        for family in ("droid", typeset.DEFAULT_PROPORTIONAL, "droid"):
            typeset.configure(typeset=True, proportional=family)
            for _ in range(3):
                seam_frame(kept, document(cache))
            drawing(kept)
            assert len(fonts.fonts) > entries, "the load added no font: nothing was left behind"
            now = held(kept)
            assert all(index == 0 or index >= entries for index in now), now
            counts.append(sum(map(len, now.values())))
            entries = len(fonts.fonts)
    finally:
        typeset.configure(typeset=True, proportional=typeset.DEFAULT_PROPORTIONAL)
        seam_frame(kept, document(cache))
        cache.clear()
    assert counts == [sum(map(len, first.values()))] * 3
