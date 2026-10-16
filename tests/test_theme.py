"""
Theme: role completeness, the proxy, and measurable legibility.

The contrast tests are the point of this file. §6.1 makes claims about a palette
being readable; colour choices get tweaked over time, and a legibility claim that
nothing checks is one that quietly stops being true.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from pptmstr import theme
from pptmstr.model import AgentState, ContextPressure, ObligationKind
from pptmstr.theme import (
    HIGH_CONTRAST,
    REQUIRED_THEMES,
    STATE_GLYPH,
    STATE_LABEL,
    THEMES,
    Color,
    Palette,
    contrast_ratio,
)

ALL_PALETTES = list(THEMES.values())
PALETTE_IDS = [p.name for p in ALL_PALETTES]


@pytest.fixture(autouse=True)
def _restore_theme():
    """Theme is module-global; a test that switches it must not leak into the next."""
    before = theme.current().name
    yield
    theme.set_theme(before)


# -- completeness --------------------------------------------------------------


def test_required_themes_exist() -> None:
    """§6.1 makes these three mandatory, not a matter of taste."""
    for name in REQUIRED_THEMES:
        assert name in THEMES


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_every_palette_defines_every_role(palette: Palette) -> None:
    """
    A role only some themes define renders as a crash or an invisible widget in the
    others -- and only on the code path that uses it, which may be rare.
    """
    for role in theme.role_names():
        value = getattr(palette, role)
        assert isinstance(value, Color), f"{palette.name}.{role} is not a Color"


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_every_state_and_pressure_resolves(palette: Palette) -> None:
    for state in AgentState:
        assert isinstance(palette.state(state), Color)
    for pressure in ContextPressure:
        assert isinstance(palette.pressure(pressure), Color)


def test_every_state_has_a_glyph_and_a_label() -> None:
    """
    The non-hue channels. If a state loses these it is legible by colour alone,
    which fails in high contrast and for a colour-deficient operator.
    """
    for state in AgentState:
        assert STATE_GLYPH[state]
        assert STATE_LABEL[state]


def test_state_labels_are_distinct() -> None:
    """Two states sharing a label would collapse the text channel for both."""
    labels = [STATE_LABEL[s] for s in AgentState]
    assert len(set(labels)) == len(labels)


# -- the proxy -----------------------------------------------------------------


def test_proxy_tracks_the_active_palette() -> None:
    """
    The bug this design exists to prevent: `from .theme import P` copies a reference,
    so a rebindable module global would leave importing modules on the old palette
    and the UI wearing two themes at once.
    """
    from pptmstr.theme import P

    theme.set_theme("dark")
    dark_bg = P.bg.u32
    theme.set_theme("light")
    assert P.bg.u32 != dark_bg
    assert P.bg.u32 == THEMES["light"].bg.u32


def test_unknown_theme_falls_back_rather_than_raising() -> None:
    """Reachable from a hand-edited settings file; should cost a theme, not a start."""
    assert theme.set_theme("no-such-theme").name == "dark"


def test_color_precomputes_both_forms() -> None:
    """
    Both forms are built once at construction. Converting per access would put
    allocation on a path taken hundreds of times a frame.
    """
    c = theme.col(0x4FC1E9)
    assert c.vec4.x == pytest.approx(0x4F / 255)
    assert c.u32 == 0xFFE9C14F  # IM_COL32 packs ABGR


# -- measurable legibility -----------------------------------------------------


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_body_text_meets_wcag_aa(palette: Palette) -> None:
    """4.5:1 is the AA floor for body text."""
    assert contrast_ratio(palette.text, palette.bg) >= 4.5
    assert contrast_ratio(palette.text, palette.panel) >= 4.5


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_dim_text_stays_legible(palette: Palette) -> None:
    """
    3:1, the large-text AA floor. text_dim carries topics and model names -- it is
    meant to recede, not to become unreadable.
    """
    assert contrast_ratio(palette.text_dim, palette.bg) >= 3.0


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_state_colours_are_legible_on_panel(palette: Palette) -> None:
    """
    Every state is drawn as tinted text, so every state colour is body text and has
    to clear the non-text-element floor against the surface it sits on.
    """
    for state in AgentState:
        ratio = contrast_ratio(palette.state(state), palette.panel)
        assert ratio >= 3.0, f"{palette.name}.{state.name} only {ratio:.2f}:1"


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_pressure_colours_are_legible(palette: Palette) -> None:
    for pressure in ContextPressure:
        ratio = contrast_ratio(palette.pressure(pressure), palette.panel)
        assert ratio >= 3.0, f"{palette.name}.{pressure.name} only {ratio:.2f}:1"


def test_high_contrast_actually_is() -> None:
    """
    Otherwise it is just another dark theme with a different name. AAA is 7:1; this
    palette should clear it comfortably rather than scrape past AA.
    """
    assert contrast_ratio(HIGH_CONTRAST.text, HIGH_CONTRAST.bg) >= 15.0
    for state in AgentState:
        ratio = contrast_ratio(HIGH_CONTRAST.state(state), HIGH_CONTRAST.bg)
        assert ratio >= 7.0, f"high_contrast.{state.name} only {ratio:.2f}:1"


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_pressure_steps_differ_in_lightness_not_only_hue(palette: Palette) -> None:
    """
    The ring's redundant channel is fill, but the three pressure colours should also
    separate by lightness -- blue/orange/red are close in luminance by default, and
    a palette where they match relies entirely on hue for anyone reading a static
    frame.
    """
    lums = [theme.relative_luminance(palette.pressure(p)) for p in ContextPressure]
    spread = max(lums) - min(lums)
    assert spread >= 0.05, f"{palette.name} pressure colours span only {spread:.3f} luminance"


# -- a modal's surface and its wells ---------------------------------------------
#
# ``theme.dialog_colors`` draws a modal on ``panel_alt`` and fills every field in it
# with ``well``. Neither surface is held by the floors above, which are taken against
# ``bg`` and ``panel``.
#
# The separation floor is measured and not chosen: the thinnest is dark, where the
# well is 1.088:1 from the surface, and kitty at 1.089. A fill is not text and has a
# border around it besides, so this is the floor under "a second colour" and not a
# legibility one -- it fails a palette whose well has been tuned into its surface.
WELL_SEPARATION = 1.08
# Under the pointer a well moves toward the accent; the thinnest step is cde's, 1.245:1.
WELL_POINTED_AT = 1.2


def _opaque(colour: imgui.ImVec4) -> Color:
    """An ``ImVec4`` as a ``Color``, for the ratios; its alpha is not carried."""
    r, g, b = (round(c * 255.0) for c in (colour.x, colour.y, colour.z))
    return theme.col((r << 16) | (g << 8) | b)


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_a_well_is_a_second_colour_on_a_modals_surface(palette: Palette) -> None:
    ratio = contrast_ratio(palette.well, palette.panel_alt)
    assert ratio >= WELL_SEPARATION, f"{palette.name}: a well is {ratio:.3f}:1 from the surface"


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_text_is_legible_on_a_modals_surface_and_in_a_well(palette: Palette) -> None:
    """
    What is typed is body text, 4.5:1. A caption on the surface is ``text_dim``,
    held to the 3:1 it is held to on ``bg``; the hint in a field is held below.
    """
    for surface in (palette.panel_alt, palette.well):
        assert contrast_ratio(palette.text, surface) >= 4.5
        assert contrast_ratio(palette.text_dim, surface) >= 3.0


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_a_well_pointed_at_is_seen_to_change_and_its_text_stays_legible(
    palette: Palette,
) -> None:
    theme.set_theme(palette.name)
    slots = dict(theme.dialog_colors())
    c = imgui.Col_
    assert _opaque(slots[int(c.frame_bg)]).u32 == palette.well.u32
    hovered = _opaque(slots[int(c.frame_bg_hovered)])
    active = _opaque(slots[int(c.frame_bg_active)])

    assert contrast_ratio(hovered, palette.well) >= WELL_POINTED_AT
    assert contrast_ratio(active, palette.well) > contrast_ratio(hovered, palette.well)
    for fill in (hovered, active):
        assert contrast_ratio(palette.text, fill) >= 4.5


def test_mixing_is_the_base_at_nothing_and_the_other_colour_at_everything() -> None:
    base, over = theme.col(0x102030), theme.col(0xF0E0D0)
    none, half, whole = (theme.mixed(base, over, alpha) for alpha in (0.0, 0.5, 1.0))
    assert (none.x, none.y, none.z) == base.rgb
    assert (whole.x, whole.y, whole.z) == pytest.approx(over.rgb)
    assert (half.x, half.y, half.z) == pytest.approx(tuple(0x80 / 255 for _ in range(3)))
    assert none.w == half.w == whole.w == 1.0


def test_a_modals_colours_are_built_once_for_a_palette_and_not_shared_between_two() -> None:
    """
    Read every frame a modal is open, so not rebuilt on each read -- and kept by
    palette, so a switch is not answered with the palette that was read first.
    """
    theme.set_theme("dark")
    dark = theme.dialog_colors()
    assert theme.dialog_colors() is dark
    theme.set_theme("light")
    light = theme.dialog_colors()
    assert light is not dark
    assert dict(light)[int(imgui.Col_.frame_bg)] is theme.LIGHT.well.vec4
    theme.set_theme("dark")
    assert theme.dialog_colors() is dark


# -- a dock's wells, buttons and tool block ----------------------------------------
#
# The operator's pick takes the outline off every frame but high_contrast's, so on a
# dock a field is told from its pane by ``dock_well`` alone, and a button by its tint.
# planning/2026-10-15-ui-style-probe.md, "Per-theme values", settles both.
#
# Every floor here is measured on the palettes as they stand, not chosen. The well's is
# turbo's, 1.136:1, the thinnest; the operator called a dock's well at 1.02:1 lost.
DOCK_WELL_SEPARATION = 1.13
# The hint in an empty field is ``Palette.hint``, held to body text's 4.5:1 in a dock's
# well and in a modal's, on every palette (the operator's "(c)": a hint colour of its own).
HINT_IN_A_WELL = 4.5
# How far a hint may be from ``text_dim`` toward ``text``, in the steps it is found by.
HINT_STEPS = 1000
# Secondary text against body text, measured on each palette's ``text_dim``: the
# hierarchy the operator kept by giving hints their own colour. A grey moved toward
# ``text`` fails here. The thinnest is cde's 1.64:1 on its mid-tone pane, and then
# high_contrast's 1.81.
DIM_UNDER_TEXT = {
    "dark": 2.40,
    "light": 2.72,
    "high_contrast": 1.81,
    "ghost": 2.79,
    "xterm": 2.24,
    "cde": 1.64,
    "win311": 2.17,
    "kitty": 2.51,
    "turbo": 2.22,
}
# The thinnest button on its pane is kitty's, 1.170:1.
BUTTON_ON_THE_PANE = 1.165
# Rest to hovered, and hovered to held: the thinnest step is kitty's, 1.228:1.
BUTTON_STEP_SEEN = 1.2
# A button's label under the pointer and while held, as the tint's steps leave it. The
# thinnest are ghost's, 4.48:1 hovered and 3.09:1 held, where today's translucent accent
# was 3.61:1 held: a held label is read for the length of a click, and is held to the
# 3:1 of large text and not body text's 4.5.
BUTTON_TEXT_HOVERED = 4.45
BUTTON_TEXT_HELD = 3.0

# How far a dock's well is from ``bg`` toward ``text``, as the record's table gives it.
DOCK_WELL_STEP = {"light": 0.08}
DOCK_WELL_STEP_ELSEWHERE = 0.10
# How far a tool block's ground is from ``bg``, and toward what, by how bright the
# pane is (``Palette.terminal_bg``); tests/test_tool_block.py holds the inks on it.
TERMINAL_STEPS = (
    (0.001, "text", 0.10),
    (0.05, "black", 0.60),
    (0.5, "white", 0.20),
    (1.01, "text", 0.06),
)


# A block is seen off its pane and is not a slab on it: tool-proofs' band. The thinnest
# is ghost's 1.07:1 and the widest cde's 1.29. The log a block is drawn in is ``panel``,
# where only the floor is held: kitty's block is 1.32:1 there and turbo's 1.50.
TERMINAL_OFF_THE_PANE = (1.05, 1.3)


def _mixed8(base: Color, over: Color, alpha: float) -> Color:
    """``theme.mixed``, as the 8-bit colour a palette literal holds."""
    return _opaque(theme.mixed(base, over, alpha))


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_a_docks_well_is_a_step_from_its_pane_toward_its_text(palette: Palette) -> None:
    step = DOCK_WELL_STEP.get(palette.name, DOCK_WELL_STEP_ELSEWHERE)
    assert palette.dock_well.u32 == _mixed8(palette.bg, palette.text, step).u32


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_a_docks_well_is_seen_on_its_pane_and_what_is_in_it_is_legible(palette: Palette) -> None:
    well = palette.dock_well
    ratio = contrast_ratio(well, palette.bg)
    assert ratio >= DOCK_WELL_SEPARATION, f"{palette.name}: the well is {ratio:.3f}:1 off the pane"
    assert contrast_ratio(palette.text, well) >= 4.5
    for surface in (palette.dock_well, palette.well):
        hint = contrast_ratio(palette.hint, surface)
        assert hint >= HINT_IN_A_WELL, f"{palette.name}: the hint is {hint:.3f}:1 in a well"


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_a_hint_is_the_least_step_from_dim_text_that_reads_in_every_well(
    palette: Palette,
) -> None:
    """
    So a hint keeps as much of the dim grey's recession as the floor allows, and is
    ``text_dim`` itself where that already reads.
    """

    def reads(colour: Color) -> bool:
        return all(
            contrast_ratio(colour, well) >= HINT_IN_A_WELL
            for well in (palette.dock_well, palette.well)
        )

    step = next(
        n
        for n in range(HINT_STEPS + 1)
        if reads(_mixed8(palette.text_dim, palette.text, n / HINT_STEPS))
    )
    assert palette.hint.u32 == _mixed8(palette.text_dim, palette.text, step / HINT_STEPS).u32


def _calls_hint_colour(expr: object) -> bool:
    """``hint_colour(...)`` or ``theme.hint_colour(...)``."""
    import ast

    if not isinstance(expr, ast.Call):
        return False
    func = expr.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return name == "hint_colour"


def _hint_fields_outside_hint_colour() -> tuple[list[str], int]:
    """
    Every ``input_text_with_hint`` call in ``pptmstr/`` that is not inside a
    ``with hint_colour(...)`` block, as ``file:line``, and how many calls there are.
    """
    import ast
    from pathlib import Path

    root = Path(theme.__file__).parent
    missed: list[str] = []
    calls = 0
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        covered: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and any(
                _calls_hint_colour(item.context_expr) for item in node.items
            ):
                covered.update(id(inner) for inner in ast.walk(node))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "input_text_with_hint"
            ):
                calls += 1
                if id(node) not in covered:
                    missed.append(f"{path.relative_to(root)}:{node.lineno}")
    return missed, calls


def test_every_field_with_a_hint_is_drawn_in_the_hint_colour() -> None:
    """
    Outside ``hint_colour`` a hint is drawn in ``text_dim``, which is under 4.5:1 in
    a well on five palettes. Found by reading the source, so a new field is held too.
    """
    missed, calls = _hint_fields_outside_hint_colour()
    assert calls >= 6
    assert missed == []


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_dim_text_keeps_its_distance_from_body_text(palette: Palette) -> None:
    ratio = contrast_ratio(palette.text, palette.text_dim)
    floor = DIM_UNDER_TEXT[palette.name]
    assert ratio >= floor, f"{palette.name}: dim text is {ratio:.3f}:1 from body text"


def test_a_field_drawn_in_the_hint_colour_leaves_dim_text_as_it_was(styled: object) -> None:
    """
    ImGui draws a hint in ``text_disabled``, which is every ``imgui.text_disabled``
    call's colour too, so the hint is pushed around a field and popped after it.
    """
    theme.set_theme("cde")
    theme.apply_style()
    slot = int(imgui.Col_.text_disabled)
    assert _slot(slot).u32 == theme.CDE.text_dim.u32
    with theme.hint_colour(imgui):
        assert _slot(slot).u32 == theme.CDE.hint.u32
    assert _slot(slot).u32 == theme.CDE.text_dim.u32


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_the_tool_blocks_ground_and_inks(palette: Palette) -> None:
    """
    The theme's own terminal, never one shared near-black: a step from the pane
    whose direction the pane's brightness picks, in the palette's own inks. On a
    light pane it is a smaller step than a dock's well, so it does not read as one.
    """
    lum = theme.relative_luminance(palette.bg)
    bound, toward, step = next(row for row in TERMINAL_STEPS if lum < row[0])
    over = {"text": palette.text, "black": theme.col(0x000000), "white": theme.col(0xFFFFFF)}
    assert palette.terminal_bg.u32 == _mixed8(palette.bg, over[toward], step).u32
    assert palette.terminal_outline is (bound == TERMINAL_STEPS[0][0])
    assert palette.terminal_text.u32 == palette.text.u32
    assert palette.terminal_dim.u32 == theme.terminal_ink(palette.text_dim, palette).u32
    off = contrast_ratio(palette.terminal_bg, palette.bg)
    low, high = TERMINAL_OFF_THE_PANE
    assert low <= off <= high, f"{palette.name}: the block is {off:.3f}:1 off the pane"
    in_the_log = contrast_ratio(palette.terminal_bg, palette.panel)
    assert in_the_log >= low, f"{palette.name}: the block is {in_the_log:.3f}:1 in the log"
    if bound == TERMINAL_STEPS[-1][0]:
        assert off < contrast_ratio(palette.dock_well, palette.bg)
    assert contrast_ratio(palette.terminal_text, palette.terminal_bg) >= theme.TERMINAL_FLOOR
    assert contrast_ratio(palette.terminal_dim, palette.terminal_bg) >= theme.TERMINAL_FLOOR


@pytest.fixture
def styled() -> Iterator[object]:
    """A context with no frame begun, for ``apply_style`` to write the style of."""
    made = imgui.create_context()
    yield made
    imgui.destroy_context(made)


def _slot(slot: int) -> Color:
    return _opaque(imgui.get_style().color_(slot))


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_the_style_is_the_operators_pick(styled: object, palette: Palette) -> None:
    theme.set_theme(palette.name)
    theme.apply_style()
    style = imgui.get_style()
    pairs = {
        "frame_padding": (9.0, 6.0),
        "item_spacing": (8.0, 6.0),
    }
    for name, (x, y) in pairs.items():
        value = getattr(style, name)
        assert (value.x, value.y) == (x, y), name
    scalars = {
        "frame_rounding": 5.0,
        "grab_rounding": 5.0,
        "tab_rounding": 5.0,
        "popup_rounding": 4.0,
        "popup_border_size": 1.0 if palette.name == "high_contrast" else 0.0,
        "scrollbar_size": 16.0,
        "scrollbar_rounding": 8.0,
        "scrollbar_padding": 3.0,
    }
    for name, value in scalars.items():
        assert getattr(style, name) == value, name


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_only_high_contrast_keeps_its_frames_and_popups_outline(
    styled: object, palette: Palette
) -> None:
    """
    The operator: "keep the frame on high-contrast". A popup there is #101010 on
    black, 1.1:1, so it keeps its outline too.
    """
    theme.set_theme(palette.name)
    theme.apply_style()
    style = imgui.get_style()
    expected = 1.0 if palette.name == "high_contrast" else 0.0
    assert style.frame_border_size == expected
    assert style.popup_border_size == expected
    if palette.name == "high_contrast":
        border = _slot(int(imgui.Col_.border))
        assert contrast_ratio(border, palette.dock_well) >= 7.0
        assert contrast_ratio(border, _slot(int(imgui.Col_.popup_bg))) >= 7.0


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_a_docks_wells_and_buttons_are_what_the_palette_says(
    styled: object, palette: Palette
) -> None:
    theme.set_theme(palette.name)
    theme.apply_style()
    c = imgui.Col_
    assert _slot(int(c.frame_bg)).u32 == palette.dock_well.u32
    tint = palette.button_tint
    rest, hovered, held = (
        _slot(int(slot)) for slot in (c.button, c.button_hovered, c.button_active)
    )
    for got, alpha in ((rest, tint), (hovered, tint + 0.16), (held, tint + 0.32)):
        assert got.u32 == _mixed8(palette.panel_alt, palette.accent, alpha).u32
    for slot in (c.button, c.button_hovered, c.button_active):
        assert imgui.get_style().color_(int(slot)).w == 1.0
    ratio = contrast_ratio(rest, palette.bg)
    assert ratio >= BUTTON_ON_THE_PANE, f"{palette.name}: a button is {ratio:.3f}:1 on the pane"
    # Each step is seen to change the button, though not always away from the pane:
    # on cde's mid-tone pane the hovered button is 1.06:1 from it, and is told from
    # the pane by its hue.
    assert contrast_ratio(hovered, rest) >= BUTTON_STEP_SEEN
    assert contrast_ratio(held, hovered) >= BUTTON_STEP_SEEN
    assert contrast_ratio(palette.text, rest) >= 4.5
    assert contrast_ratio(palette.text, hovered) >= BUTTON_TEXT_HOVERED
    assert contrast_ratio(palette.text, held) >= BUTTON_TEXT_HELD


def test_light_tints_its_buttons_further_than_the_others() -> None:
    """At the others' 0.20 light's buttons are 1.41:1 on the pane; dark's are 1.75."""
    assert theme.LIGHT.button_tint == 0.36
    assert {p.button_tint for p in ALL_PALETTES if p.name != "light"} == {0.20}


# -- the pane tabs ------------------------------------------------------------------

# How far an unselected tab's label stays from the selected tab's ``text``, so it
# reads as the secondary one. cde's ``text_dim`` is 1.64:1 from its text and is
# what its tabs draw in.
TAB_INK_FROM_TEXT = 1.5


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_an_unselected_tabs_label_reads_at_aa_on_the_tab(palette: Palette) -> None:
    """
    ``Col_.tab`` is ``panel``. Measured: dark 4.51 (its ``text_dim`` is 4.45),
    ghost 4.56 (4.04); every other palette's ``text_dim`` already reads and is used.
    """
    assert contrast_ratio(theme.tab_ink(palette), palette.panel) >= theme.TAB_INK_FLOOR == 4.5


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_an_unselected_tabs_label_is_the_least_step_from_dim_toward_text(
    palette: Palette,
) -> None:
    def reads(colour: Color) -> bool:
        return contrast_ratio(colour, palette.panel) >= theme.TAB_INK_FLOOR

    steps = theme._TAB_INK_STEPS
    step = next(
        n for n in range(steps + 1) if reads(_mixed8(palette.text_dim, palette.text, n / steps))
    )
    assert theme.tab_ink(palette).u32 == _mixed8(palette.text_dim, palette.text, step / steps).u32


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_an_unselected_tabs_label_stays_secondary_to_the_selected_ones(
    palette: Palette,
) -> None:
    ink = theme.tab_ink(palette)
    assert contrast_ratio(ink, palette.text) >= TAB_INK_FROM_TEXT
    assert contrast_ratio(palette.text, palette.panel) > contrast_ratio(ink, palette.panel)


# -- the splash's raster line --------------------------------------------------
#
# ``splash.RASTER_ROWS_PER_SECOND = 16.0`` is sound only under
#
#     rows_per_second <= band_rows * displayed_fps
#
# and at this panel's measured idle rate of 8.7fps that requires ``band_rows = 2``: one
# row allows 8.7 rows/s, so at 16 the line would advance 1.84 rows between displayed
# frames and hop down the art with a permanently unlit row inside it. That 2 counts
# ``Luminance.RASTER`` and ``Luminance.TRAIL`` as *one* moving object, which holds only
# where TRAIL is drawn far enough from DIM to be seen as part of the line rather than as
# more background. splash.py cannot check that -- it emits an ordinal and imports no
# palette by design -- so a rate constant in that file is an assertion about
# ``inbox._ink`` and about all nine palettes here, and this is where it is held.
#
# The floor is measured rather than chosen. Across every palette against both surfaces
# the shipping triple's thinnest separations are 1.227:1 (high_contrast, DIM to TRAIL,
# where text_dim is #C0C0C0 and TRAIL is white at 212/255 over black) and 1.196:1 (cde,
# TRAIL to RASTER). 1.19 is the tightest floor both clear, so a regression in the
# triple or in any palette trips this while the shipping choice passes with no room to
# spare on cde. It is why a field's hint is ``Palette.hint`` and
# not a raised ``text_dim``: on cde and win311 a grey that reads at 4.5:1 in a well
# would take DIM under this floor.
#
# It is cde that sets the floor, and cde provably cannot do better. Its entire ink range
# against its own bg spans 3.96:1 (text_dim) to 6.99:1 (text_strong), a total of
# 1.77:1 from end to end, so three evenly spaced levels are 1.33:1 per step at the very
# best -- for this triple or for any triple that could be invented on that palette.
# Going *brighter* than the bg to buy range is worse rather than better: the ramp would
# cross the background and the mid-wake rows would vanish into it. So a floor above
# 1.33 here would not be a stricter test, it would be an impossible one, and the honest
# reading of 1.19 is that on cde and win311 this is thin by construction.
SPLASH_BAND_SEPARATION = 1.19


def _as_drawn(packed: int, surface: Color) -> Color:
    """
    What the draw list actually puts on screen when it blends ``packed`` over ``surface``.

    ``inbox._ink`` hands back a packed ImU32 for all three levels, and one of them comes
    from ``theme.faded`` and so carries an alpha under 255. An alpha is not a colour
    until something has composited it, and the thing a contrast ratio has to be taken
    between is the composited result -- measuring the unblended ink would credit TRAIL
    with a separation it never has on screen. The opaque two fall through this
    unchanged, so all three take the same path and none is special-cased.
    """
    alpha = ((packed >> 24) & 0xFF) / 255.0
    # IM_COL32 packs ABGR; test_color_precomputes_both_forms pins that.
    ink = (packed & 0xFF, (packed >> 8) & 0xFF, (packed >> 16) & 0xFF)
    back = (round(c * 255.0) for c in surface.rgb)
    r, g, b = (round(alpha * i + (1.0 - alpha) * s) for i, s in zip(ink, back, strict=True))
    return theme.col((r << 16) | (g << 8) | b)


def _splash_levels(palette: Palette, surface: Color) -> dict[object, Color]:
    from pptmstr.ui import splash
    from pptmstr.ui.inbox import _ink

    theme.set_theme(palette.name)
    return {level: _as_drawn(_ink(level), surface) for level in splash.Luminance}


# Both surfaces, though the panel this draws in is a dockable window and its real
# backdrop is ``bg``. ``panel`` costs nothing to hold and is the only other surface a
# pane in this app is ever drawn on; as it happens both worst cases above are on ``bg``.
_SPLASH_SURFACES = ("bg", "panel")


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_the_splash_luminance_band_is_two_rows_on_every_palette(palette: Palette) -> None:
    """
    TRAIL has to be distinct from both its neighbours, or the raster line is one row.

    Failing this does not make the splash slightly less pretty on an unpopular palette.
    It makes ``splash.RASTER_ROWS_PER_SECOND`` exceed its own bound there, and the hole
    that constant exists to close comes back on exactly the palettes nobody looks at:
    TRAIL collapsing into DIM leaves a one-row band, and TRAIL collapsing into RASTER
    leaves a two-row bar with no leading edge, which is the same failure seen from the
    other side.
    """
    from pptmstr.ui import splash

    for surface_role in _SPLASH_SURFACES:
        surface = getattr(palette, surface_role)
        level = _splash_levels(palette, surface)
        where = f"{palette.name} on {surface_role}"

        dim_to_trail = contrast_ratio(level[splash.Luminance.DIM], level[splash.Luminance.TRAIL])
        assert dim_to_trail >= SPLASH_BAND_SEPARATION, (
            f"{where}: TRAIL is only {dim_to_trail:.3f}:1 from DIM, so the band is one "
            f"row and 16 rows/s hops"
        )

        trail_to_raster = contrast_ratio(
            level[splash.Luminance.TRAIL], level[splash.Luminance.RASTER]
        )
        assert trail_to_raster >= SPLASH_BAND_SEPARATION, (
            f"{where}: TRAIL is only {trail_to_raster:.3f}:1 from RASTER, so the line "
            f"has no leading edge"
        )


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_the_splash_trail_sits_between_the_art_and_the_raster_line(palette: Palette) -> None:
    """
    Separation alone would be satisfied by a TRAIL on the wrong side of RASTER.

    Ordering is what makes the two lit rows read as one object with a direction. It is
    stated against the surface rather than as raw luminance because half these palettes
    are light, where the strongest ink is the *darkest* -- distance from the background
    is the quantity that means the same thing in both regimes.
    """
    from pptmstr.ui import splash

    for surface_role in _SPLASH_SURFACES:
        surface = getattr(palette, surface_role)
        level = _splash_levels(palette, surface)
        steps = [contrast_ratio(level[lum], surface) for lum in splash.Luminance]
        assert steps == sorted(steps), (
            f"{palette.name} on {surface_role}: DIM/TRAIL/RASTER stand at "
            f"{steps[0]:.2f}/{steps[1]:.2f}/{steps[2]:.2f}:1 from the surface, which is "
            f"not increasing"
        )


# Deliberately not tested: WCAG contrast between two *state* colours. The metric
# does not mean what it looks like it means here -- it compares a foreground to a
# background, and since every state colour must clear 3:1 against the same panel,
# they are pushed toward similar luminance and score ~1.1:1 against each other by
# construction. A palette cannot satisfy both, and the one that matters is
# legibility against the surface. Distinguishing two states from each other is what
# STATE_GLYPH and STATE_LABEL are for, and those are checked above.


# -- widget formatting ---------------------------------------------------------


def test_short_model_drops_a_trailing_date() -> None:
    from pptmstr.ui.widgets import short_model

    assert short_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"


def test_short_model_leaves_other_ids_alone() -> None:
    """Only a trailing 8-digit run is a date stamp; anything else is information."""
    from pptmstr.ui.widgets import short_model

    assert short_model("claude-opus-5") == "claude-opus-5"
    assert short_model("claude-sonnet-5") == "claude-sonnet-5"
    assert short_model("some-model-1234") == "some-model-1234"
    assert short_model("nodashes") == "nodashes"


def test_review_state_prunes_its_diff_cache() -> None:
    """
    Split diff lines are cached per pending id; without pruning they outlive the
    approvals they belong to and the largest ones never get released.
    """
    from pptmstr.ui.review import ReviewState

    state = ReviewState()
    state.diff_lines["gone"] = ["a", "b"]
    state.diff_lines["live"] = ["c"]
    state.prune({"live"})
    assert set(state.diff_lines) == {"live"}


# -- font faces ----------------------------------------------------------------


def test_a_face_that_is_not_loaded_is_none_rather_than_an_error() -> None:
    """
    The degradation contract. push_font(None, size) means "keep the current face",
    so a renderer asking for a face nobody loaded gets body text at the right size.
    Nothing here runs a window, which is exactly the case this must survive.
    """
    assert theme.face(theme.Face.BODY) is None
    assert theme.face(theme.Face.BOLD) is None


def test_there_is_no_italic_face() -> None:
    """
    Deliberate, and not a gap to be filled: Inconsolata has no italic upstream and
    ImGui will not synthesise an oblique, so emphasis is a colour shift. Adding
    ITALIC here without vendoring a whole family gives a face that silently renders
    as regular.
    """
    assert {f.name for f in theme.Face} == {"BODY", "BOLD"}


def test_the_bold_face_ships_inside_the_package() -> None:
    """
    At the repo root it would be absent from the wheel, and the bold face would
    degrade for everyone who installed rather than cloned -- invisibly, because a
    missing face is a supported state.
    """
    vendored = theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf"
    assert vendored.is_file(), f"missing vendored face: {vendored}"
    assert (theme._ASSETS / "fonts" / "OFL.txt").is_file(), "OFL requires the licence to ship"


def test_fading_scales_only_the_alpha() -> None:
    base = theme.col(0x4FC1E9)
    assert theme.faded(base, 1.0) == base.u32
    assert theme.faded(base, 0.0) == 0x00E9C14F
    assert theme.faded(base, 0.5) >> 24 == pytest.approx(127, abs=2)


def test_fading_is_quantised_and_memoised() -> None:
    """
    The caller is an animation running per cell per frame per card. Neighbouring
    alphas must collapse onto one cached entry rather than packing a new colour each
    frame.
    """
    base = theme.col(0x4FC1E9)
    theme._FADE_CACHE.clear()
    first = theme.faded(base, 0.500)
    assert theme.faded(base, 0.505) == first
    assert len(theme._FADE_CACHE) == 1


def test_fading_out_of_range_clamps() -> None:
    base = theme.col(0x4FC1E9)
    assert theme.faded(base, -1.0) == theme.faded(base, 0.0)
    assert theme.faded(base, 5.0) == theme.faded(base, 1.0)


# -- the typeset faces ---------------------------------------------------------


def _fake_runner(monkeypatch) -> None:
    """
    Stand in for the two things ``load_fonts`` asks of a live ``hello_imgui`` runner.
    """
    import types

    from imgui_bundle import hello_imgui

    params = types.SimpleNamespace(callbacks=types.SimpleNamespace(default_icon_font=None))
    monkeypatch.setattr(hello_imgui, "add_assets_search_path", lambda path: None)
    monkeypatch.setattr(hello_imgui, "get_runner_params", lambda: params)
    monkeypatch.setattr(theme, "_FACES", {})


def test_the_typeset_faces_are_loaded_after_the_ui_font(monkeypatch) -> None:
    """
    Fonts[0] is ImGui's default. The layout library's faces include a proportional
    one; loaded first, it would become the font of every widget in the application.
    """
    from pptmstr.ui import typeset

    order: list[str] = []
    _fake_runner(monkeypatch)
    monkeypatch.setattr(
        theme, "_load_face", lambda asset, *, icons: order.append(asset.rsplit("/", 1)[-1]) or 1
    )
    monkeypatch.setattr(typeset, "load_fonts", lambda bold=None: order.append(f"typeset:{bold}"))
    theme.load_fonts()
    bold = theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf"
    assert order == ["Inconsolata-Medium.ttf", "Inconsolata-Bold.ttf", f"typeset:{bold}"]


def test_a_failure_loading_the_typeset_faces_is_not_fatal(monkeypatch) -> None:
    """
    ``load_fonts``' own contract, extended to the faces it now also loads: the app
    starts, the panes keep the renderers they had, and the menu can say why.
    """
    from pptmstr.ui import typeset

    def explode(bold=None):
        raise RuntimeError("no fontTools")

    _fake_runner(monkeypatch)
    monkeypatch.setattr(theme, "_load_face", lambda asset, *, icons: object())
    monkeypatch.setattr(typeset, "load_fonts", explode)
    try:
        theme.load_fonts()
        assert not typeset.available() and not typeset.enabled()
        assert typeset.unavailable_reason() == (
            "the typesetting library failed and is not in use (RuntimeError: no fontTools)"
        )
    finally:
        typeset.gate._GATE.failure = None


def test_without_a_ui_font_the_typeset_faces_are_not_loaded_at_all(monkeypatch) -> None:
    """
    A missing Inconsolata is survivable: ImGui's built-in font takes over. Loading
    the layout library's faces behind nothing would put Roboto in Fonts[0] instead,
    and the whole application would be set in it.
    """
    from pptmstr.ui import typeset

    called: list[object] = []
    _fake_runner(monkeypatch)
    monkeypatch.setattr(theme, "_load_face", lambda asset, *, icons: None)
    monkeypatch.setattr(typeset, "load_fonts", lambda bold=None: called.append(1))
    theme.load_fonts()
    assert called == []


def test_with_typesetting_switched_off_the_faces_are_loaded_all_the_same(monkeypatch) -> None:
    """
    The load is some twenty milliseconds, so a launch with ``Settings.typeset`` off
    pays it too, and switching the setting on mid-session is a setting and not a
    load into an atlas that is in use.
    """
    from pptmstr.ui import typeset

    calls: list[str] = []
    _fake_runner(monkeypatch)
    monkeypatch.setattr(theme, "_load_face", lambda asset, *, icons: object())
    monkeypatch.setattr(typeset, "load_fonts", lambda bold=None: calls.append(bold.name))
    monkeypatch.setattr(typeset, "wanted", lambda: False)
    theme.load_fonts()
    assert calls == ["Inconsolata-Bold.ttf"]


# -- obligations ------------------------------------------------------------------


def test_every_obligation_kind_has_a_glyph() -> None:
    """
    The inbox row indexes this inside a draw call, so a kind with no entry is a
    ``KeyError`` on the first frame such an obligation exists.
    """
    assert set(theme.OBLIGATION_GLYPH) == set(ObligationKind)
    assert all(theme.OBLIGATION_GLYPH.values())


def test_every_obligation_kind_has_a_palette_role() -> None:
    assert set(theme._OBLIGATION_ROLE) == set(ObligationKind)


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_every_obligation_kind_resolves_to_a_colour(palette: Palette) -> None:
    for kind in ObligationKind:
        assert isinstance(palette.obligation(kind), Color), kind


def test_a_decision_does_not_look_like_any_of_the_three_states_that_stop_a_node() -> None:
    """Its asker may be working, so it borrows no state's glyph."""
    glyphs = theme.OBLIGATION_GLYPH
    others = {glyphs[kind] for kind in ObligationKind if kind is not ObligationKind.DECISION}

    assert glyphs[ObligationKind.DECISION] not in others
    assert glyphs[ObligationKind.DECISION] not in STATE_GLYPH.values()
