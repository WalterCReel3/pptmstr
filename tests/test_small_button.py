"""
``widgets.small_button`` is the operator's "variant D": a button under
frame_padding 8x4, rounding 5 and 14 px text, 22 px tall, and it acts exactly as
``imgui.small_button`` did -- the same id from the same label, true on the frame it
is clicked, nothing when disabled, and its label on the line's text baseline.

The calls are pinned against a recording stand-in; the rest is Dear ImGui itself,
a context with a display size and no backend, driven by mouse events.
"""

from __future__ import annotations

import ast
import itertools
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import imgui_bundle
import pytest
from imgui_bundle import imgui

from pptmstr import theme
from pptmstr.ui import widgets

PACKAGE = Path(widgets.__file__).resolve().parents[1]


# -- what it pushes and pops ---------------------------------------------------------


class _Line:
    """The window's line state ``_on_the_line`` reads and writes."""

    def __init__(self, offset: float) -> None:
        self.curr_line_text_base_offset = offset
        self.prev_line_text_base_offset = -1.0


class _Internal:
    """``imgui.internal``: the window, and ``button_ex`` answered by ``_Recording``."""

    ButtonFlagsPrivate_ = imgui.internal.ButtonFlagsPrivate_

    def __init__(self, outer: _Recording) -> None:
        self.outer = outer

    def get_current_window(self) -> Any:
        return type("Window", (), {"dc": self.outer.line})()

    def button_ex(self, *args: Any) -> object:
        return self.outer.button_ex(*args)


class _Recording:
    """
    Dear ImGui's calls the wrapper makes, in order, answering ``button_ex`` with
    ``pressed``. The line's face has a 13 px ascent, 14 px text an 11 px one.
    """

    StyleVar_ = imgui.StyleVar_

    def __init__(self, pressed: object = True, raises: bool = False) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.pressed = pressed
        self.raises = raises
        self.line = _Line(6.0)
        self.internal = _Internal(self)
        self.sizes: list[float] = [16.0]
        self.line_at_button = 0.0

    def get_font_baked(self) -> Any:
        return type("Baked", (), {"ascent": {16.0: 13.0, 14.0: 11.0}[self.sizes[-1]]})()

    def get_style(self) -> Any:
        padding = (8.0, 4.0) if len(self.sizes) > 1 else (9.0, 6.0)
        return type("Style", (), {"frame_padding": imgui.ImVec2(*padding)})()

    def ImVec2(self, x: float, y: float) -> tuple[float, float]:  # noqa: N802 -- ImGui's name
        return (x, y)

    def push_font(self, *args: Any) -> None:
        self.calls.append(("push_font", args))
        self.sizes.append(float(args[1]))

    def pop_font(self) -> None:
        self.calls.append(("pop_font", ()))
        self.sizes.pop()

    def push_style_var(self, *args: Any) -> None:
        self.calls.append(("push_style_var", args))

    def pop_style_var(self, count: int = 1) -> None:
        self.calls.append(("pop_style_var", (count,)))

    def button_ex(self, label: str, size: Any, flags: int) -> object:
        self.calls.append(("button_ex", (label, tuple(size), flags)))
        self.line_at_button = self.line.curr_line_text_base_offset
        if self.raises:
            raise KeyError("from inside the button")
        return self.pressed

    def calc_text_size(self, text: str) -> Any:
        self.calls.append(("calc_text_size", (text,)))
        return imgui.ImVec2(7.0 * len(text), 14.0)

    def pushed(self) -> int:
        """Pushes not yet popped: a font is one, a ``pop_style_var(n)`` pops n."""
        held = 0
        for name, args in self.calls:
            held += {"push_font": 1, "push_style_var": 1}.get(name, 0)
            held -= {"pop_font": 1}.get(name, 0)
            if name == "pop_style_var":
                held -= int(args[0])
        return held


PUSHES = [
    ("push_font", (None, 14.0)),
    ("push_style_var", (imgui.StyleVar_.frame_padding, (8.0, 4.0))),
    ("push_style_var", (imgui.StyleVar_.frame_rounding, 5.0)),
]


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> _Recording:
    stand_in = _Recording()
    monkeypatch.setattr(widgets, "imgui", stand_in)
    return stand_in


def test_it_draws_a_button_under_variant_ds_values_and_pops_every_one(
    recording: _Recording,
) -> None:
    assert widgets.small_button("copy##row") is True
    assert recording.calls == [
        *PUSHES,
        ("button_ex", ("copy##row", (0.0, 0.0), BASELINE)),
        ("pop_style_var", (2,)),
        ("pop_font", ()),
    ]
    assert recording.pushed() == 0


BASELINE = imgui.internal.ButtonFlagsPrivate_.align_text_base_line


def test_the_line_it_aligns_to_is_raised_by_the_faces_ascents_and_handed_on(
    recording: _Recording,
) -> None:
    """
    The flag reads the line's offset 2 px lower, the 13 - 11 ascents apart; what
    ``same_line`` hands the next item is the larger of the line's 6 and 4 - 2.
    """
    widgets.small_button("copy")
    assert recording.line_at_button == 8.0
    assert recording.line.prev_line_text_base_offset == 6.0


def test_after_a_button_that_raises_the_line_is_still_handed_on(recording: _Recording) -> None:
    recording.raises = True
    recording.line.curr_line_text_base_offset = 0.0
    with pytest.raises(KeyError):
        widgets.small_button("x")
    assert recording.line.prev_line_text_base_offset == 2.0


@pytest.mark.parametrize("answer", [False, 0, None, True, 1])
def test_the_answer_is_the_buttons_as_a_bool(recording: _Recording, answer: object) -> None:
    recording.pressed = answer
    said = widgets.small_button("x")
    assert said is bool(answer)


def test_a_button_that_raises_still_leaves_nothing_pushed(recording: _Recording) -> None:
    recording.raises = True
    with pytest.raises(KeyError):
        widgets.small_button("x")
    assert recording.pushed() == 0


def test_the_width_is_measured_at_its_text_size_and_padding(recording: _Recording) -> None:
    assert widgets.small_button_width("defaults") == 7.0 * 8 + 2 * 8.0
    assert recording.calls[0] == ("push_font", (None, 14.0))
    assert recording.pushed() == 0


def test_the_values_are_variant_ds() -> None:
    assert widgets.SMALL_BUTTON_PADDING == (8.0, 4.0)
    assert widgets.SMALL_BUTTON_ROUNDING == 5.0
    assert widgets.SMALL_BUTTON_TEXT_PX == 14.0


# -- against Dear ImGui --------------------------------------------------------------


@pytest.fixture
def context() -> Iterator[None]:
    made = imgui.create_context()
    imgui.set_current_context(made)
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800.0, 600.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.set_ini_filename("")
    io.add_focus_event(True)
    theme.apply_style()
    yield
    imgui.destroy_context(made)


class _Drawn:
    """What one frame's button reported, and the font size inside it."""

    def __init__(self) -> None:
        self.pressed = False
        self.id = 0
        self.lo = (0.0, 0.0)
        self.hi = (0.0, 0.0)
        self.font_px = 0.0


def _frame(draw: Callable[[], bool], mouse: tuple[float, float] | None, down: bool) -> _Drawn:
    io = imgui.get_io()
    if mouse is not None:
        io.add_mouse_pos_event(*mouse)
    io.add_mouse_button_event(0, down)
    imgui.new_frame()
    drawn = _Drawn()
    try:
        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(imgui.ImVec2(400.0, 300.0))
        imgui.begin("pane")
        drawn.pressed = draw()
        drawn.id = imgui.get_item_id()
        lo, hi = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        drawn.lo, drawn.hi = (lo.x, lo.y), (hi.x, hi.y)
        imgui.end()
    finally:
        imgui.render()
    return drawn


def _click(draw: Callable[[], bool]) -> list[bool]:
    """Find the button, then press and release on it: what it answered each frame."""
    at = _frame(draw, (-100.0, -100.0), False)
    middle = ((at.lo[0] + at.hi[0]) / 2, (at.lo[1] + at.hi[1]) / 2)
    return [
        _frame(draw, middle, False).pressed,
        _frame(draw, middle, True).pressed,
        _frame(draw, middle, False).pressed,
        _frame(draw, middle, False).pressed,
    ]


def test_it_is_22_px_tall_and_its_text_is_drawn_at_14(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float] = []
    real = imgui.internal.button_ex

    def button_ex(label: str, *args: Any) -> bool:
        seen.append(imgui.get_font_size())
        return bool(real(label, *args))

    monkeypatch.setattr(imgui.internal, "button_ex", button_ex)
    _frame(lambda: True, None, False)  # the first frame fills in the font's base
    drawn = _frame(lambda: widgets.small_button("copy"), None, False)
    assert drawn.hi[1] - drawn.lo[1] == 22.0
    assert seen[-1] == 14.0
    assert drawn.hi[0] - drawn.lo[0] == widgets_width_in_frame("copy")


def widgets_width_in_frame(text: str) -> float:
    """``small_button_width`` asked inside a frame, as a caller asks it."""
    imgui.new_frame()
    try:
        imgui.begin("pane")
        width = widgets.small_button_width(text)
        imgui.end()
    finally:
        imgui.render()
    return width


def test_the_style_is_what_it_was_after_it(context: None) -> None:
    style = imgui.get_style()
    before = (style.frame_padding.x, style.frame_padding.y, style.frame_rounding)

    def draw() -> bool:
        size = imgui.get_font_size()
        pressed = widgets.small_button("copy")
        assert imgui.get_font_size() == size
        return pressed

    _frame(draw, None, False)
    _frame(draw, None, False)
    assert (style.frame_padding.x, style.frame_padding.y, style.frame_rounding) == before


@pytest.mark.parametrize("label", ["copy", "copy##section-3", "amend##t1", "1,204 earlier lines"])
def test_its_id_is_the_one_imguis_small_button_gave_the_same_label(
    context: None, label: str
) -> None:
    ours = _frame(lambda: widgets.small_button(label), None, False)
    theirs = _frame(lambda: bool(imgui.small_button(label)), None, False)
    assert ours.id == theirs.id != 0


def test_a_click_is_true_on_the_release_frame_only_as_imguis_was(context: None) -> None:
    ours = _click(lambda: widgets.small_button("copy"))
    theirs = _click(lambda: bool(imgui.small_button("copy")))
    assert ours == theirs == [False, False, True, False]


def test_a_disabled_one_answers_nothing_to_a_click_as_imguis_did(context: None) -> None:
    def disabled(draw: Callable[[], bool]) -> Callable[[], bool]:
        def drawn() -> bool:
            imgui.begin_disabled()
            pressed = draw()
            imgui.end_disabled()
            return pressed

        return drawn

    ours = _click(disabled(lambda: widgets.small_button("copy")))
    theirs = _click(disabled(lambda: bool(imgui.small_button("copy"))))
    assert ours == theirs == [False, False, False, False]


# -- on the line's baseline ----------------------------------------------------------

INCONSOLATA = Path(imgui_bundle.__file__).parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"


@pytest.fixture(params=[13.0, 16.0, 20.0], ids=lambda px: f"{px:g}px")
def faced(request: pytest.FixtureRequest) -> Iterator[None]:
    """A context set in the UI's face at a size, under the dock style."""
    made = imgui.create_context()
    imgui.set_current_context(made)
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800.0, 600.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.set_ini_filename("")
    if not INCONSOLATA.exists():
        imgui.destroy_context(made)
        pytest.skip(f"no {INCONSOLATA.name} in this imgui_bundle")
    io.fonts.add_font_from_file_ttf(str(INCONSOLATA), request.param)
    theme.apply_style()
    yield
    imgui.destroy_context(made)


def _ascent(px: float | None = None) -> float:
    if px is not None:
        imgui.push_font(None, px)
    ascent = float(imgui.get_font_baked().ascent)
    if px is not None:
        imgui.pop_font()
    return ascent


def _text_baseline() -> float:
    """The last item's, for text: it is laid at its own top."""
    return float(imgui.get_item_rect_min().y) + _ascent()


def _label_baseline() -> float:
    """The last item's, for the wrapper: its label is set ``pad.y`` under its top."""
    top = float(imgui.get_item_rect_min().y)
    return top + widgets.SMALL_BUTTON_PADDING[1] + _ascent(widgets.SMALL_BUTTON_TEXT_PX)


def _lines(draw: Callable[[], dict[str, float]]) -> dict[str, float]:
    """``draw``'s measurements, on the third frame: the font's sizes settle on the first."""
    seen: dict[str, float] = {}
    for _ in range(3):
        imgui.new_frame()
        try:
            imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
            imgui.set_next_window_size(imgui.ImVec2(600.0, 500.0))
            imgui.begin("pane")
            seen = draw()
            imgui.end()
        finally:
            imgui.render()
    return seen


def test_after_text_aligned_to_the_frame_the_label_is_on_its_baseline(faced: None) -> None:
    """A help mark after a notice: the notice's text is set where a frame's would be."""

    def draw() -> dict[str, float]:
        imgui.align_text_to_frame_padding()
        imgui.text("notice")
        text = _text_baseline()
        imgui.same_line()
        widgets.small_button("(?)")
        return {"apart": _label_baseline() - text}

    assert _lines(draw)["apart"] == 0.0


def test_after_a_field_and_its_status_the_label_is_on_their_baseline(faced: None) -> None:
    """The find bar: a field, its "1/3", then the close."""

    def draw() -> dict[str, float]:
        imgui.set_next_item_width(200.0)
        imgui.input_text("##find", "find")
        field = float(imgui.get_item_rect_min().y) + imgui.get_style().frame_padding.y + _ascent()
        imgui.same_line()
        imgui.text_disabled("1/3")
        text = _text_baseline()
        imgui.same_line()
        widgets.small_button("x##find")
        return {"field": text - field, "apart": _label_baseline() - text}

    assert _lines(draw) == {"field": 0.0, "apart": 0.0}


@pytest.mark.parametrize("aligned", [False, True], ids=["line-top", "frame-aligned"])
def test_text_after_it_on_its_line_is_on_its_labels_baseline(faced: None, aligned: bool) -> None:
    def draw() -> dict[str, float]:
        if aligned:
            imgui.align_text_to_frame_padding()
            imgui.text("wants approval")
            imgui.same_line()
        widgets.small_button("copy")
        label = _label_baseline()
        imgui.same_line()
        imgui.text("after")
        return {"apart": label - _text_baseline()}

    assert _lines(draw)["apart"] == 0.0


def _lowered() -> float:
    """
    How far a button starting a line is lowered: where the line's face, set at
    the top, has its baseline under the label's at the line's top.
    """
    lift = _ascent() - _ascent(widgets.SMALL_BUTTON_TEXT_PX)
    return max(0.0, lift - widgets.SMALL_BUTTON_PADDING[1])


def test_alone_on_its_line_it_is_lowered_only_past_its_pad_and_the_next_line_clears_it(
    faced: None,
) -> None:
    def draw() -> dict[str, float]:
        top = float(imgui.get_cursor_screen_pos().y)
        widgets.small_button("copy")
        lo, hi = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        imgui.text("below")
        return {
            "lowered": float(lo.y) - top - _lowered(),
            "gap": float(imgui.get_item_rect_min().y - hi.y),
        }

    assert _lines(draw) == {"lowered": 0.0, "gap": imgui.get_style().item_spacing.y}


def test_a_lowered_one_makes_its_line_tall_enough_to_hold_it(faced: None) -> None:
    def draw() -> dict[str, float]:
        imgui.align_text_to_frame_padding()
        imgui.text("notice")
        imgui.same_line()
        widgets.small_button("(?)")
        hi = imgui.get_item_rect_max()
        imgui.text("below")
        return {"gap": float(imgui.get_item_rect_min().y - hi.y)}

    assert _lines(draw)["gap"] >= imgui.get_style().item_spacing.y


def test_after_text_at_the_lines_top_it_does_not_rise_above_the_line(faced: None) -> None:
    """
    The one line it cannot meet: text set at the line's top, unaligned, has its
    baseline above the label's whenever the 4 px pad is more than the faces'
    ascents differ. The button stays on the line rather than rising out of it.
    """

    def draw() -> dict[str, float]:
        imgui.text("wants approval")
        text_top = float(imgui.get_item_rect_min().y)
        text = _text_baseline()
        imgui.same_line()
        widgets.small_button("copy")
        lift = _ascent() - _ascent(widgets.SMALL_BUTTON_TEXT_PX)
        return {
            "top": float(imgui.get_item_rect_min().y) - text_top - _lowered(),
            "apart": _label_baseline() - text - max(0.0, widgets.SMALL_BUTTON_PADDING[1] - lift),
        }

    assert _lines(draw) == {"top": 0.0, "apart": 0.0}


def test_a_line_begun_for_one_meets_its_label_and_stays_one_button_tall(faced: None) -> None:
    def draw() -> dict[str, float]:
        top = float(imgui.get_cursor_screen_pos().y)
        widgets.small_button_line()
        imgui.text("wants approval")
        text = _text_baseline()
        imgui.same_line()
        widgets.small_button("copy")
        lo, hi = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        label = _label_baseline()
        imgui.text("below")
        return {
            "apart": label - text,
            "lowered": float(lo.y) - top - _lowered(),
            "row": float(imgui.get_item_rect_min().y) - top,
            "tall": float(hi.y - lo.y) + _lowered() + imgui.get_style().item_spacing.y,
        }

    seen = _lines(draw)
    assert seen["apart"] == 0.0 and seen["lowered"] == 0.0
    assert seen["row"] == seen["tall"]


# -- at the sites that draw one -------------------------------------------------------


class _Seen:
    """One item drawn: what drew it, its baseline, and the rows it spans."""

    def __init__(self, name: str, baseline: float) -> None:
        self.name = name
        self.baseline = baseline
        self.top = float(imgui.get_item_rect_min().y)
        self.bottom = float(imgui.get_item_rect_max().y)

    def beside(self, other: _Seen) -> bool:
        return self.top < other.bottom and other.top < self.bottom


def _baselines(monkeypatch: pytest.MonkeyPatch) -> list[_Seen]:
    """
    Every text and button a frame draws, in order, with its baseline: text is laid
    at its top, a button's label ``frame_padding.y`` under it, in the font and
    padding in force as it is drawn -- for the wrapper, its own.
    """
    seen: list[_Seen] = []

    def noted(name: str, real: Any, padded: bool) -> Callable[..., Any]:
        def drawn(*args: Any) -> Any:
            answer = real(*args)
            pad = float(imgui.get_style().frame_padding.y) if padded else 0.0
            seen.append(_Seen(name, float(imgui.get_item_rect_min().y) + pad + _ascent()))
            return answer

        return drawn

    for name in ("text", "text_disabled", "text_colored"):
        monkeypatch.setattr(imgui, name, noted(name, getattr(imgui, name), False))
    monkeypatch.setattr(imgui, "button", noted("button", imgui.button, True))
    monkeypatch.setattr(
        imgui.internal, "button_ex", noted("small_button", imgui.internal.button_ex, True)
    )
    return seen


def _detail_header() -> Callable[[], None]:
    from pptmstr.ui import detail
    from tests import test_detail

    obligation = test_detail.spawn_approval()
    snap = test_detail.snapshot(test_detail.record(), needs_you=(obligation,))
    return lambda: detail._header(snap, obligation, 10.0)


def _in_a_dialog(draw: Callable[[], None]) -> Callable[[], None]:
    def drawn() -> None:
        with widgets.dialog_style():
            draw()

    return drawn


# Each line that ends in a small button, drawn by the code that draws it. Some
# begin with plain text and are begun with ``small_button_line``; the rest follow
# a button or a frame and are on the baseline without it. A line that ends in a
# question mark ends in a link, and is measured in tests/test_help_mark.py.
_SITES: dict[str, Callable[[Path], Callable[[], None]]] = {
    "detail-header-copy": lambda _: _detail_header(),
    "settings-heading": lambda _: _in_a_dialog(_settings_heading()),
}


def _settings_heading() -> Callable[[], None]:
    from pptmstr.ui import settings_dialog

    key = "appearance"
    section = settings_dialog.Section(key, "Appearance", settings_dialog.Restored(key), False)
    return lambda: settings_dialog._heading(section, [])


@pytest.mark.parametrize("site", list(_SITES))
def test_at_every_site_each_label_is_on_its_lines_baseline(
    faced: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, site: str
) -> None:
    """Label baseline minus the baseline of what it follows on its line, at each one."""
    draw = _SITES[site](tmp_path)
    seen = _baselines(monkeypatch)

    def measured() -> dict[str, float]:
        seen.clear()
        draw()
        return {
            f"{at}: after {before.name}": item.baseline - before.baseline
            for at, (before, item) in enumerate(itertools.pairwise(seen), 1)
            if item.name == "small_button" and item.beside(before)
        }

    apart = _lines(measured)
    assert apart and set(apart.values()) == {0.0}, apart


def test_a_fact_with_no_mark_is_not_set_down(faced: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a line a button ends is begun for one: the rest stay at their line's top."""
    from pptmstr.ui import resume_form

    seen = _baselines(monkeypatch)

    def draw() -> dict[str, float]:
        seen.clear()
        top = float(imgui.get_cursor_screen_pos().y)
        resume_form._fact("session", "11111111")
        return {item.name: item.top - top for item in seen}

    assert _lines(draw) == {"text_disabled": 0.0, "text": 0.0}


# -- laid out for ---------------------------------------------------------------------


@pytest.mark.parametrize("dpi", [1.0, 1.5, 2.0])
def test_session_details_copy_column_holds_its_button_at_any_scale(
    faced: None, monkeypatch: pytest.MonkeyPatch, dpi: float
) -> None:
    """The button's right edge, against the table's: a fixed column overflows above 1."""
    from pptmstr.ui import session_details

    imgui.get_style().font_scale_dpi = dpi
    drawn: list[tuple[float, float]] = []
    real = widgets.small_button

    def small_button(label: str) -> bool:
        pressed = real(label)
        drawn.append((float(imgui.get_item_rect_min().x), float(imgui.get_item_rect_max().x)))
        return pressed

    monkeypatch.setattr(widgets, "small_button", small_button)
    rows = (session_details.Row("directory", "/home/someone", copy="/home/someone"),)

    def draw() -> dict[str, float]:
        drawn.clear()
        right = float(imgui.get_cursor_screen_pos().x + imgui.get_content_region_avail().x)
        session_details._rows(0, rows)
        ((left, edge),) = drawn
        return {"over": max(0.0, edge - right), "wide": edge - left}

    seen = _lines(draw)
    assert seen["over"] == 0.0
    assert seen["wide"] > 44.0 or dpi == 1.0


# -- nothing draws ImGui's own ------------------------------------------------------


def _direct_calls(source: str) -> list[int]:
    """Lines reading ``small_button`` off anything but ``widgets``: ImGui's own, by any alias."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == "small_button":
            if not (isinstance(node.value, ast.Name) and node.value.id == "widgets"):
                found.append(node.lineno)
        if isinstance(node, ast.ImportFrom) and node.module and "imgui" in node.module:
            if any(alias.name == "small_button" for alias in node.names):
                found.append(node.lineno)
    return found


def test_no_module_in_pptmstr_draws_imguis_small_button() -> None:
    found = {
        str(path.relative_to(PACKAGE)): lines
        for path in sorted(PACKAGE.rglob("*.py"))
        if (lines := _direct_calls(path.read_text(encoding="utf-8")))
    }
    assert found == {}


@pytest.mark.parametrize(
    "source",
    [
        "imgui.small_button('x')",
        "im.small_button('x')",
        "f = imgui.small_button",
        "from imgui_bundle.imgui import small_button",
    ],
)
def test_the_scan_finds_imguis_small_button_however_it_is_reached(source: str) -> None:
    assert _direct_calls(source) == [1]


def test_the_scan_passes_the_wrapper() -> None:
    assert _direct_calls("widgets.small_button('x')\nsmall_button('y')") == []
