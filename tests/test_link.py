"""
``widgets.link``: a themed text link. Its inks in every state on every theme
(``theme.link_inks``), and against Dear ImGui itself -- a context with a display
size and no backend, driven by mouse events -- that it answers a click once, on the
id it was given, draws each state in its own ink, draws nothing of itself when
disabled but its dim label, sits on its line's baseline, and is one text line tall.
"""

from __future__ import annotations

import ctypes
import struct
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import imgui_bundle
import pytest
from imgui_bundle import imgui

from pptmstr import theme
from pptmstr.theme import THEMES, Color, LinkInks, contrast_ratio
from pptmstr.ui import tool_block, widgets

PALETTES = list(THEMES.values())
IDS = [p.name for p in PALETTES]
INCONSOLATA = Path(imgui_bundle.__file__).parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"


@pytest.fixture(autouse=True)
def _restore_theme() -> Iterator[None]:
    before = theme.current().name
    yield
    theme.set_theme(before)


# -- the inks ------------------------------------------------------------------------


def _states(inks: LinkInks) -> tuple[Color, Color, Color, Color]:
    return (inks.rest, inks.hovered, inks.pressed, inks.disabled)


def _pane(palette: theme.Palette) -> tuple[LinkInks, tuple[Color, ...]]:
    theme.set_theme(palette.name)
    return theme.link_inks(), (palette.bg, palette.panel, palette.panel_alt)


def _block(palette: theme.Palette) -> tuple[LinkInks, tuple[Color, ...]]:
    theme.set_theme(palette.name)
    return tool_block.link_inks(), (palette.terminal_bg,)


@pytest.mark.parametrize("where", [_pane, _block], ids=["pane", "block"])
@pytest.mark.parametrize("palette", PALETTES, ids=IDS)
def test_every_state_reads_on_its_surface_and_hover_and_press_move_away_from_it(
    palette: theme.Palette, where: Callable[[theme.Palette], Any]
) -> None:
    """
    ``imgui.text_link`` brightens on hover whatever the ground, so on light the
    accent went 5.15:1, 3.32 and 2.85. Here each state reads at 4.5:1 on every
    surface it stands on, and hovered and pressed are each further off it.
    """
    inks, grounds = where(palette)

    def off(colour: Color) -> float:
        return min(contrast_ratio(colour, ground) for ground in grounds)

    for state in _states(inks):
        assert off(state) >= theme.LINK_FLOOR == 4.5
    assert off(inks.rest) < off(inks.hovered) < off(inks.pressed)
    assert inks.disabled.u32 not in {inks.rest.u32, inks.hovered.u32, inks.pressed.u32}


def test_the_pane_link_is_the_accent_where_the_accent_already_reads() -> None:
    dark = theme.set_theme("dark")
    assert theme.link_inks().rest.u32 == dark.accent.u32


def test_a_state_that_would_pass_the_text_is_the_text() -> None:
    """A rise the text cannot reach stops at the text, which reads furthest off."""
    dark = theme.set_theme("dark")
    inks = theme.link_inks(dark.text, (dark.bg,), dark.text, dark.text_dim)
    assert inks.rest.u32 == inks.hovered.u32 == inks.pressed.u32 == dark.text.u32


@pytest.mark.parametrize("palette", PALETTES, ids=IDS)
def test_the_style_holds_the_link_ink_for_imguis_own(palette: theme.Palette) -> None:
    made = imgui.create_context()
    imgui.set_current_context(made)
    try:
        theme.set_theme(palette.name)
        theme.apply_style()
        got = imgui.get_style().color_(imgui.Col_.text_link)
        rest = theme.link_inks().rest.vec4
        assert (got.x, got.y, got.z, got.w) == (rest.x, rest.y, rest.z, rest.w)
    finally:
        imgui.destroy_context(made)


# -- in a frame ----------------------------------------------------------------------


@pytest.fixture(params=[16.0], ids=["16px"])
def faced(request: pytest.FixtureRequest) -> Iterator[float]:
    """A context set in the UI's face at a size, under the dock style."""
    if not INCONSOLATA.exists():
        pytest.skip(f"no {INCONSOLATA.name} in this imgui_bundle")
    made = imgui.create_context()
    imgui.set_current_context(made)
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800.0, 600.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.set_ini_filename(None)
    io.fonts.add_font_from_file_ttf(str(INCONSOLATA), request.param)
    theme.set_theme("light")
    theme.apply_style()
    yield float(request.param)
    imgui.destroy_context(made)


_VERT = struct.Struct("<ffffI")


def _colours(start: int) -> list[int]:
    """The colours of every vertex the window's draw list holds from ``start``."""
    dl = imgui.internal.get_current_window().draw_list
    n = dl.vtx_buffer.size()
    raw = (ctypes.c_char * (n * _VERT.size)).from_address(dl.vtx_buffer.data_address())
    return [v[4] for v in _VERT.iter_unpack(bytes(raw))][start:]


def _vertices() -> int:
    return int(imgui.internal.get_current_window().draw_list.vtx_buffer.size())


class _Pane:
    """One window drawn a frame at a time; ``draw`` answers what a test reads."""

    def __init__(self, draw: Callable[[], dict[str, Any]]) -> None:
        self.draw = draw
        self.seen: dict[str, Any] = {}

    def frame(self, count: int = 1) -> dict[str, Any]:
        for _ in range(count):
            imgui.new_frame()
            try:
                imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
                imgui.set_next_window_size(imgui.ImVec2(600.0, 500.0))
                imgui.begin("pane")
                self.seen = self.draw()
                imgui.end()
            finally:
                imgui.render()
        return self.seen

    def at(self) -> tuple[float, float]:
        low, high = self.seen["min"], self.seen["max"]
        return ((low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0)


def _one(label: str, **kwargs: Any) -> Callable[[], dict[str, Any]]:
    def draw() -> dict[str, Any]:
        imgui.text("before")
        start = _vertices()
        pressed = widgets.link(label, **kwargs)
        low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        return {
            "pressed": pressed,
            "id": imgui.get_item_id(),
            "colours": set(_colours(start)),
            "vertices": _vertices() - start,
            "min": (low.x, low.y),
            "max": (high.x, high.y),
        }

    return draw


def test_a_click_answers_once_on_the_id_it_was_given_and_each_state_has_its_ink(
    faced: float,
) -> None:
    pane = _Pane(_one("show all", ident="41 more lines##more"))
    inks = theme.link_inks()
    seen = pane.frame(3)
    assert seen["colours"] == {inks.rest.u32}
    io = imgui.get_io()
    io.add_mouse_pos_event(*pane.at())
    assert pane.frame(2)["colours"] == {inks.hovered.u32}
    io.add_mouse_button_event(0, True)
    assert pane.frame()["colours"] == {inks.pressed.u32}
    io.add_mouse_button_event(0, False)
    answers = [pane.frame()["pressed"] for _ in range(3)]
    assert answers == [True, False, False]


def test_its_id_is_the_ident_and_otherwise_the_label(faced: float) -> None:
    ids: dict[str, int] = {}

    def draw() -> dict[str, Any]:
        widgets.link("show all", ident="41 more lines##more")
        ids["given"] = imgui.get_item_id()
        ids["want_given"] = imgui.get_id("41 more lines##more")
        widgets.link("(?)##gate")
        ids["label"] = imgui.get_item_id()
        ids["want_label"] = imgui.get_id("(?)##gate")
        return {}

    _Pane(draw).frame(2)
    assert ids["given"] == ids["want_given"] and ids["label"] == ids["want_label"]
    assert ids["given"] != ids["label"]


def test_disabled_it_answers_nothing_and_is_its_dim_label_with_no_underline(
    faced: float,
) -> None:
    enabled = _Pane(_one("(?)##m")).frame(3)

    def draw() -> dict[str, Any]:
        imgui.begin_disabled()
        try:
            return _one("(?)##m")()
        finally:
            imgui.end_disabled()

    pane = _Pane(draw)
    seen = pane.frame(3)
    assert seen["colours"] == {theme.link_inks().disabled.u32}, "its own ink, not at an alpha"
    assert seen["vertices"] < enabled["vertices"], "no underline"
    io = imgui.get_io()
    io.add_mouse_pos_event(*pane.at())
    pane.frame(2)
    io.add_mouse_button_event(0, True)
    pane.frame()
    io.add_mouse_button_event(0, False)
    assert not any(pane.frame()["pressed"] for _ in range(3))


def _baseline() -> float:
    """The last item's, for text or a link: each is laid at its own top."""
    return float(imgui.get_item_rect_min().y) + float(imgui.get_font_baked().ascent)


_BEFORE: dict[str, Callable[[], None]] = {
    "text": lambda: imgui.text("notice"),
    "frame-aligned text": lambda: (imgui.align_text_to_frame_padding(), imgui.text("notice")),
    "a field": lambda: (imgui.set_next_item_width(200.0), imgui.input_text("##f", "find")),
    "a small button": lambda: widgets.small_button("copy"),
}


@pytest.mark.parametrize("before", list(_BEFORE))
def test_it_sits_on_its_lines_baseline(faced: float, before: str) -> None:
    def draw() -> dict[str, Any]:
        _BEFORE[before]()
        if before == "a field":
            low = float(imgui.get_item_rect_min().y)
            line = low + imgui.get_style().frame_padding.y + float(imgui.get_font_baked().ascent)
        elif before == "a small button":
            low = float(imgui.get_item_rect_min().y)
            imgui.push_font(None, widgets.SMALL_BUTTON_TEXT_PX)
            ascent = float(imgui.get_font_baked().ascent)
            imgui.pop_font()
            line = low + widgets.SMALL_BUTTON_PADDING[1] + ascent
        else:
            line = _baseline()
        imgui.same_line()
        widgets.link("(?)##m")
        return {"apart": _baseline() - line}

    assert _Pane(draw).frame(3)["apart"] == 0.0


def test_a_notices_row_with_its_mark_is_one_line_and_six_pixels_shorter(faced: float) -> None:
    """At the 16 px face: a line of 16 where the 22 px button made the row 22."""
    said = widgets.Notice(
        "nothing runs unattended", widgets.Tone.QUIET, widgets.Help("m", "t", ("p",))
    )

    def draw() -> dict[str, Any]:
        top = imgui.get_cursor_pos_y()
        widgets.notice(said)
        return {"row": imgui.get_cursor_pos_y() - top - imgui.get_style().item_spacing.y}

    row = _Pane(draw).frame(3)["row"]
    assert row == imgui.get_text_line_height() == faced
    button = widgets.SMALL_BUTTON_TEXT_PX + 2.0 * widgets.SMALL_BUTTON_PADDING[1]
    assert button - row == 6.0


def test_its_width_is_its_labels(faced: float) -> None:
    def draw() -> dict[str, Any]:
        widgets.link("(?)##m")
        low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        return {"drawn": high.x - low.x, "said": widgets.link_width("(?)##m")}

    seen = _Pane(draw).frame(2)
    assert seen["drawn"] == seen["said"] == imgui.calc_text_size("(?)").x


@pytest.mark.parametrize("more", ["41 more lines", tool_block.SHOW_ALL])
def test_a_blocks_footer_is_one_line_and_six_pixels_shorter(faced: float, more: str) -> None:
    """At the 16 px face: the footer row is 16 where the 22 px button made it 22."""

    def draw() -> dict[str, Any]:
        top = imgui.get_cursor_pos_y()
        tool_block._affordance(more)
        return {"row": imgui.get_cursor_pos_y() - top - imgui.get_style().item_spacing.y}

    row = _Pane(draw).frame(3)["row"]
    assert row == faced
    assert widgets.SMALL_BUTTON_TEXT_PX + 2.0 * widgets.SMALL_BUTTON_PADDING[1] - row == 6.0
