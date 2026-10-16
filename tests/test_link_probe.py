"""
The link probe (scripts/link_probe.py): its ``link`` wrapper draws what
``imgui.text_link`` draws, every variant leaves the style as it found it, and
the numbers the sheets print are the ones measured here.

Drawn against a bare ImGui context in the UI's face (Inconsolata) under the
dock style; the draw list's own vertices are the evidence for colour and
geometry, so a rule recalled wrong fails here rather than on a sheet.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import imgui_bundle
import pytest
from imgui_bundle import imgui

from pptmstr import theme
from pptmstr.ui import widgets

ROOT = Path(__file__).resolve().parent.parent
INCONSOLATA = Path(imgui_bundle.__file__).parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("link_probe", ROOT / "scripts" / "link_probe.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["link_probe"] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


@pytest.fixture(params=[13.0, 16.0, 20.0], ids=lambda px: f"{px:g}px")
def faced(request: pytest.FixtureRequest) -> Iterator[float]:
    if not INCONSOLATA.exists():
        pytest.skip(f"no {INCONSOLATA.name} in this imgui_bundle")
    made = imgui.create_context()
    imgui.set_current_context(made)
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800.0, 600.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.set_ini_filename("")
    io.add_focus_event(True)
    io.fonts.add_font_from_file_ttf(str(INCONSOLATA), request.param)
    theme.set_theme("dark")
    theme.apply_style()
    yield request.param
    imgui.destroy_context(made)


@pytest.fixture
def sixteen(faced: float) -> float:
    if faced != 16.0:
        pytest.skip("drawn at 16 px only")
    return faced


Vert = tuple[float, float, int]


class _Drawn:
    def __init__(self) -> None:
        self.pressed = False
        self.ident = 0
        self.rect = (0.0, 0.0, 0.0, 0.0)
        self.verts: list[Vert] = []


def _frame(
    draw: Callable[[], bool],
    mouse: tuple[float, float] = (-100.0, -100.0),
    down: bool = False,
    disabled: bool = False,
) -> _Drawn:
    io = imgui.get_io()
    io.add_mouse_pos_event(*mouse)
    io.add_mouse_button_event(0, down)
    imgui.new_frame()
    out = _Drawn()
    try:
        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(imgui.ImVec2(500.0, 400.0))
        imgui.begin("pane")
        imgui.text("before")
        imgui.same_line()
        dl = imgui.get_window_draw_list()
        first = dl.vtx_buffer.size()
        if disabled:
            imgui.begin_disabled()
        out.pressed = draw()
        if disabled:
            imgui.end_disabled()
        out.ident = imgui.get_item_id()
        lo, hi = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        out.rect = (lo.x, lo.y, hi.x, hi.y)
        for at in range(first, dl.vtx_buffer.size()):
            v = dl.vtx_buffer[at]
            out.verts.append((round(v.pos.x, 3), round(v.pos.y, 3), int(v.col)))
        imgui.end()
    finally:
        imgui.render()
    return out


INK = (0.31, 0.76, 0.91, 1.0)
DEEP = (0.05, 0.43, 0.62, 1.0)
"""Two inks: one whose value a hover saturates, and one it does not (light's accent)."""


@pytest.fixture(params=[INK, DEEP], ids=["bright", "deep"])
def ink(request: pytest.FixtureRequest) -> tuple[float, float, float, float]:
    return request.param  # type: ignore[no-any-return]


def _real(ink: Any = INK) -> Callable[[], bool]:
    return lambda: bool(probe.real_link("show all##x", ink))


def _wrapped(underline: Any = None, shown: Any = None, ink: Any = INK) -> Callable[[], bool]:
    def draw() -> bool:
        return bool(probe.link("show all##x", ink, underline or probe.Underline.ALWAYS, shown))

    return draw


def _sequence(draw: Callable[[], bool], disabled: bool = False) -> list[_Drawn]:
    """At rest (three frames, the font settling), then hovered, held and released."""
    for _ in range(3):
        rest = _frame(draw, disabled=disabled)
    r = rest.rect
    mid = ((r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0)
    _frame(draw, mid, disabled=disabled)
    return [
        rest,
        _frame(draw, mid, disabled=disabled),
        _frame(draw, mid, True, disabled=disabled),
        _frame(draw, mid, False, disabled=disabled),
        _frame(draw, (-100.0, -100.0), disabled=disabled),
    ]


@pytest.mark.parametrize("disabled", [False, True], ids=["enabled", "disabled"])
def test_the_wrapper_draws_and_answers_what_text_link_does(
    faced: float, disabled: bool, ink: Any
) -> None:
    real = _sequence(_real(ink), disabled)
    wrapped = _sequence(_wrapped(ink=ink), disabled)
    for a, b in zip(real, wrapped, strict=True):
        assert (a.pressed, a.ident, a.rect) == (b.pressed, b.ident, b.rect)
        assert a.verts == b.verts
    assert [d.pressed for d in real] == [False, False, False, not disabled, False]


def _colours(drawn: _Drawn) -> set[int]:
    return {c for _, _, c in drawn.verts}


def _u32(c: tuple[float, float, float, float]) -> int:
    return int(imgui.get_color_u32(imgui.ImVec4(*c)))


def test_its_ink_rule_is_the_one_text_link_draws(faced: float, ink: Any) -> None:
    rest, hovered, held, _, _ = _sequence(_real(ink))
    for drawn, hot, down in ((rest, False, False), (hovered, True, False), (held, True, True)):
        text, line = probe.inks(ink, hot, down)
        assert _colours(drawn) == {_u32(text), _u32(line)}


def test_disabled_reaches_the_label_twice_and_the_underline_once(faced: float) -> None:
    drawn = _sequence(_real(), disabled=True)[0]
    alpha = imgui.get_style().disabled_alpha
    alphas = sorted({(c >> 24) & 0xFF for c in _colours(drawn)})
    assert alphas == sorted(
        {
            round(255 * alpha**probe.DISABLED_TEXT_PASSES),
            round(255 * alpha**probe.DISABLED_LINE_PASSES),
        }
    )


def _line_verts(drawn: _Drawn) -> list[Vert]:
    """The underline's: the vertices at or below the box's foot less a pixel."""
    foot = drawn.rect[3]
    _, line = probe.inks(INK, False, False)
    hot_line = probe.inks(INK, True, False)[1]
    return [v for v in drawn.verts if v[2] in (_u32(line), _u32(hot_line)) and v[1] >= foot - 2.0]


@pytest.mark.parametrize(
    ("underline", "at_rest", "hovered"),
    [("ALWAYS", True, True), ("HOVER", False, True), ("NEVER", False, False)],
)
def test_the_underline_is_drawn_as_the_variant_says(
    faced: float, underline: str, at_rest: bool, hovered: bool
) -> None:
    rest, hot, _, _, _ = _sequence(_wrapped(probe.Underline[underline]))
    assert bool(_line_verts(rest)) is at_rest
    assert bool(_line_verts(hot)) is hovered


def test_a_painted_state_changes_the_colours_and_not_the_answer(faced: float) -> None:
    pressed = _sequence(_wrapped(shown=probe.Shown.PRESSED))
    text, line = probe.inks(INK, True, True)
    assert _colours(pressed[0]) == {_u32(text), _u32(line)}
    assert [d.pressed for d in pressed] == [False, False, False, True, False]


def _stacks() -> tuple[Any, ...]:
    """Every stack a draw could leave a push on, as ``ErrorRecoveryStoreState`` sizes it."""
    state = imgui.internal.ErrorRecoveryState()
    imgui.internal.error_recovery_store_state(state)
    style = imgui.get_style()
    return (
        tuple(
            getattr(state, name)
            for name in dir(state)
            if name.startswith("size_of_") and isinstance(getattr(state, name), int)
        ),
        imgui.get_current_context().disabled_stack_size,
        float(style.alpha),
        float(imgui.get_font_size()),
        tuple(
            (c.x, c.y, c.z, c.w)
            for c in (
                style.color_(int(imgui.Col_.text)),
                style.color_(int(imgui.Col_.text_link)),
            )
        ),
    )


def test_every_variant_in_every_state_pops_exactly_what_it_pushed(faced: float) -> None:
    seen: list[tuple[Any, Any]] = []

    def draw() -> bool:
        for variant in probe.VARIANTS:
            for shown in probe.Shown:
                before = _stacks()
                probe.draw_variant(variant, shown, f"v##{variant.name}{shown.value}")
                seen.append((before, _stacks()))
        return False

    _frame(draw)
    assert len(seen) == len(probe.VARIANTS) * len(probe.Shown) == 36
    assert all(a == b for a, b in seen)


def test_the_cells_say_what_is_drawn(faced: float) -> None:
    """A cell's label colour is the draw list's, composited on the ground."""
    ground = theme.current().bg
    alpha = float(imgui.get_style().disabled_alpha)
    for variant in probe.VARIANTS:
        for shown in probe.Shown:
            c = probe.cell(variant, shown, ground, alpha)
            ink = probe.ink_of(variant.colour)
            hot = shown in (probe.Shown.HOVERED, probe.Shown.PRESSED)
            text, _ = probe.inks(ink, hot, shown is probe.Shown.PRESSED)
            if shown is not probe.Shown.DISABLED:
                assert probe.hex_of(probe.rgba(c.text)) == probe.hex_of(text)
            has_line = variant.underline is probe.Underline.ALWAYS or (
                variant.underline is probe.Underline.HOVER and hot
            )
            assert (c.line is not None) is has_line


def test_the_blocks_affordance_is_put_back_however_the_draw_ends(sixteen: float) -> None:
    real = widgets.small_button
    with pytest.raises(RuntimeError):
        with probe._links_in_blocks(INK, probe.Underline.ALWAYS):
            assert widgets.small_button is not real
            raise RuntimeError
    assert widgets.small_button is real


def _measure(draw: Callable[[], Any]) -> Any:
    seen: list[Any] = []

    def frame() -> bool:
        seen.append(draw())
        return False

    for _ in range(3):
        _frame(frame)
    return seen[-1]


@pytest.mark.parametrize("expanded", [False, True], ids=["collapsed", "expanded"])
def test_the_probes_button_footer_is_now_the_link_footer(sixteen: float, expanded: bool) -> None:
    """
    The probe's link footer swaps out ``widgets.small_button``, which the block's
    footer no longer draws, so its "today" footer is the link already and the two
    are one height.
    What the link saves over the button is pinned against the real block in
    ``tests/test_link.py::test_a_blocks_footer_is_one_line_and_six_pixels_shorter``.
    """
    faces = probe.plain_faces()
    button = _measure(lambda: probe.draw_footer(faces, as_link=False, expanded=expanded, key="a"))
    linked = _measure(lambda: probe.draw_footer(faces, as_link=True, expanded=expanded, key="b"))
    assert button == linked


def test_the_footer_label_is_the_layouts() -> None:
    from pptmstr.ui import tool_block

    block = tool_block.layout(probe.footer_entry(), live=False)
    assert block.more == f"{probe.FOOTER_LINES - tool_block.SHOWN['Read']} more lines"
    assert not block.tail


BASELINES = {
    "after plain text": 0.0,
    "after frame-aligned text": 0.0,
    "after a field": 0.0,
    "after a small_button": 0.0,
}
"""The numbers the context sheet prints for (c), at every size."""


def test_the_baselines_are_the_ones_the_sheet_prints(faced: float) -> None:
    found = _measure(lambda: probe.baselines(INK))
    assert set(found) == set(probe.BASELINE_CASES)
    for case, want in BASELINES.items():
        if want is not None:
            assert found[case] == want, case
    # Beside the button the other way round: a link at the line's top, then the
    # button, which is lowered by its pad less the faces' ascents and cannot rise.
    assert found["small_button after it"] == max(0.0, widgets.SMALL_BUTTON_PADDING[1] - _lift())


def _lift() -> float:
    """The UI face's ascent less the small button's 14 px face's."""
    values: list[float] = []

    def draw() -> bool:
        line = float(imgui.get_font_baked().ascent)
        imgui.push_font(None, widgets.SMALL_BUTTON_TEXT_PX)
        small = float(imgui.get_font_baked().ascent)
        imgui.pop_font()
        values.append(line - small)
        return False

    _frame(draw)
    return values[-1]
