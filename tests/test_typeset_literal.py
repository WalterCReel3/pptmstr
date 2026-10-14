"""
Literal text set by the library: a host's own code block, behind a measurement.

``views.literal_unfit`` and ``gate.literal_columns`` decide; a caller asks with
``document(literal=True)`` or ``plain(literal=True)``. Read back from the view
(``texts``, a copy), from the draw list's vertices and from where the library
put each line (``set_lines``); nobody reads a pixel.

The library as it is keeps the columns at every size the product sets, so the
branch where the measurement says no is stood in (``NO_COLUMN``), as the diff
tests stand it in.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from imscribe import MarkdownCode

from pptmstr import drawable, theme
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Kind, Part, Role, RoleStyle, Slot, gate, roles, themes, views
from tests.conftest import NO_COLUMN
from tests.test_typeset_views import TALL_TREE, _inks, frame

ROOT = Path(__file__).resolve().parent.parent

WRITTEN = [
    "\tindented\twith\ttabs",
    "two  spaces   and    four",
    "*not emphasis* and **not bold**",
    "snake_case_name and _x_",
    "C:\\path\\to\\file and a \\* escape and a trailing \\",
    "<div>&amp; List<String> <!-- an unclosed comment",
    "# not a heading\n- not a list\n1. nor this\n> nor a quote",
    "```\nnot a fence\n```",
    "| not | a | table |\n|---|---|---|",
    "[not a link](https://example.com) and ![nor an image](x.png)",
    "$ rm **/*.py && echo `date` ~ ^ %",
    drawable.text("ls\x00; rm -rf x and a \udc80 here"),
    "\n leading blank line, then\n\n\n   three blank lines above",
]
"""
Text a reader of markdown would read as something else, or white space a text
block would fold: each must reach the screen as it is. The NUL and the lone
surrogate are as ``drawable`` writes them where session text enters.
"""

#: (body pixel height, step) pairs that set the literal role at 12 to 24 px.
EVERY_HEIGHT = (
    (14.0, -2),
    (14.0, -1),
    *((16.0, step) for step in roles.STEPS),
    (20.0, 1),
    (20.0, 2),
    (20.0, 3),
    (20.0, 4),
)


def _at(body: float, step: int) -> None:
    typeset.configure(
        typeset=True,
        scale=body / 16.0,
        roles={Role.LITERAL: RoleStyle(Slot.MONOSPACE, step)},
    )


def _glyphs(seam: Any, draw: Any) -> list[tuple[float, float]]:
    """
    The (left, top) of each textured quad one frame of ``draw`` added.
    """
    found: list[tuple[float, float]] = []

    def body() -> None:
        buffer = seam.get_window_draw_list().vtx_buffer
        white = seam.get_font_tex_uv_white_pixel()
        before = buffer.size()
        draw()
        corners = [
            buffer[n]
            for n in range(before, buffer.size())
            if (buffer[n].uv.x, buffer[n].uv.y) != (white.x, white.y)
        ]
        for at in range(0, len(corners) - 3, 4):
            quad = corners[at : at + 4]
            found.append((min(v.pos.x for v in quad), min(v.pos.y for v in quad)))

    frame(seam, body)
    return found


def test_literal_text_a_caller_asks_for_is_one_host_code_block_of_the_librarys(seam: Any) -> None:
    cache = typeset.ViewCache()
    written = "  **not bold**   two  spaces\n\tand <a> tag"
    frame(seam, lambda: typeset.plain(cache, "k", written, literal=True))
    (view,) = cache.views("k")
    (block,) = view.blocks
    assert isinstance(block, MarkdownCode) and block.text == written
    assert block.style == themes.literal_style(Role.LITERAL)
    assert cache.texts("k") == (written,) and cache.unbuilt("k") == 0


def test_without_being_asked_literal_text_stays_imgui_s(seam: Any) -> None:
    cache = typeset.ViewCache()
    parts = (Part("# prose"), Part("tool\noutput", Kind.LITERAL))
    frame(seam, lambda: typeset.document(cache, "k", parts))
    (only,) = cache.views("k")
    assert all(
        not isinstance(block, MarkdownCode) or "tool" not in block.text for block in only.blocks
    )
    assert cache.texts("k")[-1] == "tool\noutput"


@pytest.mark.parametrize("written", WRITTEN)
def test_every_character_of_literal_text_is_set_and_copied_as_written(
    seam: Any, written: str
) -> None:
    """
    Nothing in literal text is read: what the view holds is the text, a copy
    of all of it is the text, and the frame drew one glyph for every character
    with ink, in a code block of the library's.
    """
    cache = typeset.ViewCache()
    glyphs = _glyphs(seam, lambda: typeset.plain(cache, "k", written, literal=True))
    glyphs = _glyphs(seam, lambda: typeset.plain(cache, "k", written, literal=True))
    (view,) = cache.views("k")
    assert view.texts == [written] or tuple(view.texts) == (written,)
    view.select_all()
    assert view.selected_text() == written
    assert len(glyphs) == sum(1 for char in written if not char.isspace())
    assert len(view.set_lines(0, 1)) == written.count("\n") + 1


@pytest.mark.parametrize(("body", "step"), EVERY_HEIGHT)
def test_literal_text_keeps_its_columns_at_every_height_the_product_sets(
    seam: Any, body: float, step: int
) -> None:
    """
    R2, R4 and R5 of ``HANDOFF-advtext.md`` for the literal path, as the gate
    measures them through the view it draws literal text with: every glyph of
    a row on its index times ImGui's baked advance, hit where it is drawn, and
    a tab to a whole column.
    """
    _at(body, step)
    assert roles.px(Role.LITERAL) == body + step
    measured = gate.literal_columns(themes.document_theme(), themes.literal_style(Role.LITERAL))
    assert measured.holds, measured
    assert measured.size == body + step
    assert measured.baked == typeset.face_font("CODE").get_font_baked(body + step).get_char_advance(
        ord("0")
    )
    assert measured.reach > 10_000


@pytest.mark.parametrize(("body", "step"), EVERY_HEIGHT)
def test_every_line_of_literal_text_is_one_line_under_the_last_whatever_is_in_it_and_wherever(
    seam: Any, body: float, step: int
) -> None:
    """
    P13 for the literal path: ``fixed_line_height``, with the line off a
    half-pixel baseline (``themes.literal_line``). Box drawing reaches past the face's ascent and
    descent; as drawn, every row's glyphs are one line under the row above,
    and that holds wherever down the window the block is, since a baseline on
    a half pixel is rounded by where it is on the screen.
    """
    _at(body, step)
    pitch = themes.literal_line(Role.LITERAL)
    assert pitch == roles.px(Role.LITERAL) + (1.0 if roles.px(Role.LITERAL) in (14, 18, 22) else 0)
    rows = ["x" + row for row in TALL_TREE.rstrip("\n").split("\n")]
    text = "\n".join(rows)
    for down in (0.0, 101.0, 233.0, 347.0):
        cache = typeset.ViewCache()

        def draw(down: float = down, cache: typeset.ViewCache = cache) -> None:
            if down:
                seam.dummy(seam.ImVec2(1.0, down))
            typeset.plain(cache, "k", text, literal=True)

        glyphs = _glyphs(seam, draw)
        (view,) = cache.views("k")
        tops = [line.top for line in view.set_lines(0, 1)]
        assert len(tops) == len(rows)
        assert {round(b - a, 4) for a, b in zip(tops, tops[1:], strict=False)} == {pitch}
        # As drawn: the ``x`` that starts each row, one line under the one above.
        starts, at = [], 0
        for row in rows:
            starts.append(glyphs[at][1])
            at += sum(1 for char in row if not char.isspace())
        assert at == len(glyphs)
        assert {b - a for a, b in zip(starts, starts[1:], strict=False)} == {pitch}, down
        claimed = cache.height("k") or 0.0
        assert len(rows) * pitch <= claimed <= len(rows) * pitch + 1.0


def test_a_literal_line_wider_than_the_window_is_one_line_and_can_be_scrolled_to(
    seam: Any,
) -> None:
    """
    P12 for the literal path: ``code_overflow="grow"``. One source line is one
    line however long, and the window's scroll range reaches its end.
    """
    cache = typeset.ViewCache()
    reach: list[float] = []

    def body() -> None:
        typeset.plain(cache, "k", "z" * 300 + "\nshort", literal=True)
        reach.append(seam.get_scroll_max_x() + seam.get_window_width())

    for _ in range(2):
        frame(seam, body, sideways=True)
    (view,) = cache.views("k")
    assert len(view.set_lines(0, 1)) == 2
    cell = typeset.face_font("CODE").get_font_baked(16.0).get_char_advance(ord("z"))
    assert view.content_width == pytest.approx(300 * cell, abs=0.5)
    assert reach[-1] >= 300 * cell


def test_the_end_of_a_literal_line_of_letters_the_code_face_lacks_can_be_reached(
    seam: Any,
) -> None:
    """
    The library sets a Greek or Cyrillic letter in two cells. A diff view
    counts one a character and its last column cannot be scrolled to (A7); a
    code block's width is what it drew: ten ``Ж`` are twenty cells wide.
    """
    cache = typeset.ViewCache()
    for _ in range(2):
        frame(seam, lambda: typeset.plain(cache, "k", "\u0416" * 10 + "\nx", literal=True))
    (view,) = cache.views("k")
    assert gate.code_lacks("\u0416") == ("\u0416",)
    cell = typeset.face_font("CODE").get_font_baked(16.0).get_char_advance(ord("x"))
    right = max(line.right for line in view.set_lines(0, 1))
    assert right == pytest.approx(20 * cell, abs=0.5)
    assert view.width >= right or view.content_width >= right


UNFIT = [
    (Part("x\x1b[31mred\x1b[0m", Kind.LITERAL), "control"),
    (Part("progress\r100%", Kind.LITERAL), "control"),
    (Part("a\u202eb", Kind.LITERAL), "control"),
    (Part("zero\u200bwidth", Kind.LITERAL), "control"),
    (Part("ends\n", Kind.LITERAL), "line break"),
    (Part("broken at spaces", Kind.WRAPPED), "broken"),
    (Part("broken anywhere", Kind.WRAPPED_EXACT), "broken"),
]


@pytest.mark.parametrize(("part", "why"), UNFIT)
def test_literal_text_the_library_would_not_set_as_written_stays_imgui_s(
    seam: Any, part: Part, why: str
) -> None:
    """
    Measured: the library sets ESC, a carriage return, a bidirectional or a
    zero-width character as nothing or as a line break, and a trailing line
    break as nothing; it hangs a wrapped code line. Such a part is ImGui's,
    as it was.
    """
    said = views.literal_unfit(part)
    assert said is not None and why in said
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", (part,), literal=True))
    assert cache.views("k") == () and cache.texts("k") == (part.text,)


def test_literal_text_is_imgui_s_where_the_measurement_says_no(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "literal_columns", lambda theme, style: NO_COLUMN)
    part = Part("columns\nare content", Kind.LITERAL)
    assert "7.626" in (views.literal_unfit(part) or "")
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", (part,), literal=True))
    assert cache.views("k") == ()


def test_a_literal_line_longer_than_the_measured_reach_is_imgui_s(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    measured = gate.literal_columns(themes.document_theme(), themes.literal_style(Role.LITERAL))
    short = gate.Columns(True, measured.why, 8.0001, 8.0, 16.0, 156, 0, 0, True)
    assert short.reach == 5000
    monkeypatch.setattr(gate, "literal_columns", lambda theme, style: short)
    assert views.literal_unfit(Part("x" * 2500, Kind.LITERAL)) is None
    assert "longer" in (views.literal_unfit(Part("x" * 2501, Kind.LITERAL)) or "")


def test_literal_text_with_no_colour_is_the_text_ink_and_a_colour_is_its_own(seam: Any) -> None:
    cache = typeset.ViewCache()
    seen = _inks(seam, lambda: typeset.plain(cache, "k", "3 passed\nok", literal=True))
    assert cache.views("k")
    assert theme.P.text.u32 in seen and theme.P.diff_context.u32 not in seen
    red = _inks(
        seam, lambda: typeset.plain(cache, "r", "boom\nhere", colour=theme.P.danger, literal=True)
    )
    assert theme.P.danger.u32 in red and theme.P.text.u32 not in red


def test_a_literal_part_stands_where_imgui_s_line_of_it_stood(seam: Any) -> None:
    """
    No space above the block and no indent: the first glyph's left edge is
    the cursor's x plus the glyph's bearing, as ImGui draws it, and a tight
    part claims its lines and nothing under them.
    """
    cache = typeset.ViewCache()
    heights: dict[str, float] = {}

    def draw(literal: bool) -> None:
        heights[str(literal)] = typeset.plain(cache, str(literal), "0123\n4567", literal=literal)

    imgui_left = _glyphs(seam, lambda: draw(False))[0]
    library_left = _glyphs(seam, lambda: draw(True))[0]
    assert cache.views("True") and not cache.views("False")
    # Across, where ImGui put it; down, the library sets a baseline at three
    # quarters of the line, which is not where ImGui's ascent puts it.
    assert library_left[0] == imgui_left[0]
    assert abs(library_left[1] - imgui_left[1]) <= 2.0
    assert heights["True"] == heights["False"] == 2 * typeset.line_height()


def test_a_literal_view_is_kept_while_its_text_is_and_given_new_text_in_place(seam: Any) -> None:
    cache = typeset.ViewCache()
    parts = (Part("# head"), Part("one\ntwo", Kind.LITERAL))
    frame(seam, lambda: typeset.document(cache, "k", parts, literal=True))
    first = cache.views("k")
    frame(seam, lambda: typeset.document(cache, "k", parts, literal=True))
    assert cache.views("k") == first
    grown = (Part("# head"), Part("one\ntwo\nthree", Kind.LITERAL))
    frame(seam, lambda: typeset.document(cache, "k", grown, literal=True))
    assert cache.views("k")[1] is first[1]
    assert cache.texts("k")[-1] == "one\ntwo\nthree"


def test_a_literal_part_under_the_mouse_is_its_part_and_its_text(seam: Any) -> None:
    cache = typeset.ViewCache()
    parts = (Part("prose"), Part("literal\nlines", Kind.LITERAL))
    frame(seam, lambda: typeset.document(cache, "k", parts, literal=True))
    view = cache.views("k")[1]
    rect = view.block_rect(0)
    assert rect is not None
    seam.get_io().add_mouse_pos_event(rect[0] + 4.0, rect[1] + 4.0)
    found: list[Any] = []
    frame(
        seam,
        lambda: (
            typeset.document(cache, "k", parts, literal=True),
            found.append(cache.pointed("k")),
        ),
    )
    assert found[-1] == (1, "literal\nlines")


def _literal_callers(path: Path) -> set[str]:
    """
    The functions of a module that pass ``literal=`` to the seam, by name.
    """
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for call in ast.walk(function):
            if isinstance(call, ast.Call) and any(k.arg == "literal" for k in call.keywords):
                out.add(function.name)
    return out


def test_no_approval_surface_asks_for_literal_text_to_be_the_librarys() -> None:
    """
    An approval's arguments stay ImGui's in DETAIL and the inbox: the parts of
    the same roles that are not an approval's are the only ones that ask. Read
    from the sources, so a new caller is a decision somebody has to make here.
    """
    ui = ROOT / "pptmstr" / "ui"
    assert _literal_callers(ui / "inbox.py") == set()
    assert _literal_callers(ui / "detail.py") == {"_failure"}
    assert _literal_callers(ui / "transcript_pane.py") == {"_draw_generation", "_draw_run"}


def test_a_literal_view_that_sets_less_than_it_was_handed_is_unset_and_then_imgui_s(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The hold every caller that decides on text reads (``ViewCache.unbuilt``)
    is the same on this path: a draw that answers false reports every line
    unset in its frame, and from the next the part is ImGui's, as written.
    """
    import imscribe

    cache = typeset.ViewCache()
    written = "row one\nrow *two*\nrow three"
    real = imscribe.TypesetView.draw
    with monkeypatch.context() as patched:
        patched.setattr(
            imscribe.TypesetView, "draw", lambda self, **given: bool(real(self, **given)) and False
        )
        frame(seam, lambda: typeset.plain(cache, "k", written, literal=True))
        assert cache.unbuilt("k") == 3
    frame(seam, lambda: typeset.plain(cache, "k", written, literal=True))
    assert cache.unbuilt("k") == 0 and cache.views("k") == ()
    assert cache.texts("k") == (written,)


def test_a_library_that_raises_under_a_literal_view_is_the_library_stopping(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import imscribe

    def renamed(self: Any, **given: Any) -> bool:
        raise AttributeError("'TypesetView' object has no attribute 'renamed'")

    cache = typeset.ViewCache()
    with monkeypatch.context() as patched:
        patched.setattr(imscribe.TypesetView, "draw", renamed)
        frame(seam, lambda: typeset.plain(cache, "k", "one\ntwo", literal=True))
        assert cache.unbuilt("k") == 2
        typeset.pre_frame()
        assert not typeset.can_draw()
    assert typeset.load_fonts(theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf")


def _column_of_last(seam: Any, text: str, literal: bool) -> tuple[bool, float]:
    """
    Where the last inked character of ``text`` is drawn, in cells from where
    an ``a`` alone is drawn by the same renderer; and whether the library drew.
    """
    cell = typeset.face_font("CODE").get_font_baked(16.0).get_char_advance(ord("0"))
    cache, alone = typeset.ViewCache(), typeset.ViewCache()
    for _ in range(2):
        drawn = _glyphs(seam, lambda: typeset.plain(cache, "k", text, literal=literal))
        start = _glyphs(seam, lambda: typeset.plain(alone, "k", "a", literal=literal))
    assert cache.texts("k") == (text,)
    return bool(cache.views("k")), (drawn[-1][0] - start[0][0]) / cell


TABS = [
    ("ab\tc", 4),
    ("\tx", 4),
    ("abcd\te", 8),
    ("é\tz", 4),
    ("abć\td", 4),
    ("a\tb\tc", 8),
    ("abc\td\nabcde\tf", 8),
]


@pytest.mark.parametrize(("written", "column"), TABS)
def test_a_tab_puts_the_next_character_on_the_same_stop_in_either_renderer(
    seam: Any, written: str, column: int
) -> None:
    """
    The library sets a tab to the next stop of four cells, as a terminal does;
    ImGui draws one as four spaces wherever it is. Literal text ImGui draws is
    given its tabs as spaces to the stop, so the log (the library) and DETAIL's
    arguments (ImGui) line the same output up the same; what is held is the
    text as written.
    """
    assert views.literal_unfit(Part(written, Kind.LITERAL)) is None
    library, there = _column_of_last(seam, written, literal=True)
    imgui, here = _column_of_last(seam, written, literal=False)
    assert library and not imgui
    assert there == here == column


FIT_AS_WRITTEN = ["a b", "a b", "a b", "a　b", "aㅤb", "áb"]


@pytest.mark.parametrize("written", FIT_AS_WRITTEN)
def test_spaces_and_marks_the_library_sets_in_imgui_s_columns_are_the_library_s(
    seam: Any, written: str
) -> None:
    """
    Measured: a no-break, an em, a narrow no-break or an ideographic space, a
    Hangul filler and a combining mark the code face has are set by the library
    in the column ImGui draws them in, and copied as written.
    """
    assert views.literal_unfit(Part(written, Kind.LITERAL)) is None
    library, there = _column_of_last(seam, written, literal=True)
    imgui, here = _column_of_last(seam, written, literal=False)
    assert library and there == here
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.plain(cache, "k", written, literal=True))
    (view,) = cache.views("k")
    view.select_all()
    assert view.selected_text() == written


@pytest.mark.parametrize("mark", ["️", "︀", "\U000e0100", "͏", "᠋", "⃝"])
def test_a_mark_the_code_face_lacks_is_imgui_s(seam: Any, mark: str) -> None:
    """
    Measured: the library sets each as nothing and in no cell, where ImGui
    draws its fallback glyph a cell wide; such a part stays ImGui's.
    """
    part = Part(f"a{mark}b", Kind.LITERAL)
    said = views.literal_unfit(part)
    assert said is not None and "combining mark" in said
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.document(cache, "k", (part,), literal=True))
    assert cache.views("k") == ()


@pytest.mark.parametrize("written", ["out\n   ", "out\n \t ", "out\nx  "])
def test_a_last_line_of_spaces_is_set_and_copied_as_written(seam: Any, written: str) -> None:
    cache = typeset.ViewCache()
    for _ in range(2):
        frame(seam, lambda: typeset.plain(cache, "k", written, literal=True))
    (view,) = cache.views("k")
    assert len(view.set_lines(0, 1)) == 2
    view.select_all()
    assert view.selected_text() == written
    assert cache.height("k") == 2 * themes.literal_line(Role.LITERAL)


def test_a_fractional_ui_font_size_reaches_the_seam_as_whole_pixels(seam: Any) -> None:
    """
    ``themes.literal_line`` tests a pixel height for 4k + 2 and is given
    ImGui's font size unrounded at a scale of 1. ImGui 1.92 draws a font at a
    whole pixel size, and reports that: a base of 17.6 px is 18.
    """
    style = seam.get_style()
    base = style.font_size_base
    try:
        style.font_size_base = 17.6
        for _ in range(2):
            frame(seam, lambda: None)
        assert typeset.roles.body_px() == 18.0
        assert themes.literal_line(Role.LITERAL) == 19.0
    finally:
        style.font_size_base = base
        for _ in range(2):
            frame(seam, lambda: None)


def test_a_literal_line_of_fifteen_thousand_characters_keeps_its_grid_to_its_end(
    seam: Any,
) -> None:
    """
    Measured at 16 px: scrolled to the end of a 15,000-character line, every
    glyph drawn is on a whole cell and the last is on column 14,999, and the
    width the view claims is the line's to within a quarter of a pixel.
    """
    count = 15_000
    line = ("0123456789" * (count // 10))[:count]
    text = line + "\nshort"
    assert views.literal_unfit(Part(text, Kind.LITERAL)) is None
    cell = typeset.face_font("CODE").get_font_baked(16.0).get_char_advance(ord("0"))
    cache = typeset.ViewCache()
    origin: list[float] = []
    lefts: list[float] = []

    def body(scroll: float | None) -> None:
        if scroll is not None:
            seam.set_scroll_x(scroll)
        buffer = seam.get_window_draw_list().vtx_buffer
        white = seam.get_font_tex_uv_white_pixel()
        before = buffer.size()
        origin.append(float(seam.get_cursor_screen_pos().x))
        typeset.plain(cache, "k", text, literal=True)
        origin.append(float(seam.get_scroll_max_x()))
        corners = [
            buffer[n]
            for n in range(before, buffer.size())
            if (buffer[n].uv.x, buffer[n].uv.y) != (white.x, white.y)
        ]
        lefts[:] = [
            min(v.pos.x for v in corners[at : at + 4]) for at in range(0, len(corners) - 3, 4)
        ]

    for _ in range(2):
        frame(seam, lambda: body(None), sideways=True)
    (view,) = cache.views("k")
    assert view.content_width == pytest.approx(count * cell, abs=0.25)
    end = origin[-1]
    for _ in range(2):
        frame(seam, lambda: body(end), sideways=True)
    columns = [(x - origin[-2]) / cell for x in lefts]
    assert columns and all(abs(c - round(c)) <= 0.125 for c in columns)
    assert round(columns[-1]) == count - 1
