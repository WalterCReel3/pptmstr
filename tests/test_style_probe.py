"""
The style probe's variants: that the table is one the planning record can quote
and the probe can apply, and that a variant laid over the application's style and
taken off again leaves exactly what ``theme.apply_style`` set.

Against a bare ImGui context (the ``imgui`` fixture). Nothing is rendered; the
claims are about the numbers ImGuiStyle holds.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pptmstr import theme

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "style_probe", ROOT / "scripts" / "style_probe.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["style_probe"] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def _whole_style(im: Any) -> dict[str, Any]:
    """
    Every number ImGuiStyle holds that Python can read, and every colour: what
    "the style" means for an exact comparison.
    """
    style = im.get_style()
    held: dict[str, Any] = {}
    for name in dir(style):
        if name.startswith("_"):
            continue
        value = getattr(style, name)
        if isinstance(value, (bool, int, float)):
            held[name] = value
        elif isinstance(value, im.ImVec2):
            held[name] = (value.x, value.y)
    for idx in range(int(im.Col_.count)):
        c = style.color_(idx)
        held[f"Col_{idx}"] = (c.x, c.y, c.z, c.w)
    return held


@pytest.fixture
def styled(imgui: Any) -> Iterator[Any]:
    """The application's style in force, on the dark palette, and put back after."""
    theme.set_theme("dark")
    theme.apply_style()
    yield imgui
    theme.set_theme("dark")
    theme.apply_style()


# -- the table -----------------------------------------------------------------------


def test_the_baseline_is_first_and_overrides_nothing() -> None:
    first = probe.VARIANTS[0]
    assert first.name == "current"
    assert first.scalars == () and first.colors == () and not first.inset_tabs


def test_there_are_two_or_three_variants_beside_the_baseline_with_distinct_names() -> None:
    names = [v.name for v in probe.VARIANTS]
    assert len(names) == len(set(names))
    assert 2 <= len(names) - 1 <= 3
    assert probe.BY_NAME == {v.name: v for v in probe.VARIANTS}


def test_every_override_names_a_style_value_of_its_kind_once(imgui: Any) -> None:
    style = imgui.get_style()
    for variant in probe.VARIANTS:
        names = [name for name, _ in variant.scalars]
        assert len(names) == len(set(names)), variant.name
        for name, value in variant.scalars:
            held = getattr(style, name)
            if isinstance(value, tuple):
                assert isinstance(held, imgui.ImVec2), (variant.name, name)
                assert len(value) == 2
            else:
                assert isinstance(held, float) and not isinstance(held, bool), (variant.name, name)
                assert value >= 0.0


def test_every_colour_names_a_slot_once_and_palette_roles(imgui: Any) -> None:
    roles = {f.name for f in dataclasses.fields(theme.Palette)}
    for variant in probe.VARIANTS:
        slots = [slot for slot, _ in variant.colors]
        assert len(slots) == len(set(slots)), variant.name
        for slot, mix in variant.colors:
            assert hasattr(imgui.Col_, slot), (variant.name, slot)
            assert {mix.base, mix.over} <= roles, (variant.name, slot, mix)
            assert 0.0 < mix.alpha <= 1.0, (variant.name, slot, mix)


def test_the_screens_are_named_once_each() -> None:
    assert len(probe.SCREENS) == len(set(probe.SCREENS))
    assert {"triage", "focus", "board", "settings", "launcher", "terminal-block"} <= set(
        probe.SCREENS
    )


# -- apply and revert ----------------------------------------------------------------


@pytest.mark.parametrize("name", [v.name for v in probe.VARIANTS])
def test_apply_then_revert_leaves_the_style_apply_style_set(styled: Any, name: str) -> None:
    before = _whole_style(styled)
    saved = probe.apply(probe.BY_NAME[name])
    probe.revert(saved)
    assert _whole_style(styled) == before


@pytest.mark.parametrize("name", [v.name for v in probe.VARIANTS[1:]])
def test_apply_sets_every_value_the_variant_names(styled: Any, name: str) -> None:
    """
    Without this, the revert test above holds for an ``apply`` that does nothing.
    """
    variant = probe.BY_NAME[name]
    before = _whole_style(styled)
    saved = probe.apply(variant)
    try:
        after = _whole_style(styled)
        assert after != before
        for attr, value in variant.scalars:
            assert after[attr] == pytest.approx(value), attr
        for slot, mix in variant.colors:
            want = probe.color_of(mix)
            idx = int(getattr(styled.Col_, slot))
            assert after[f"Col_{idx}"] == pytest.approx((want.x, want.y, want.z, want.w)), slot
    finally:
        probe.revert(saved)


def test_a_variant_applied_over_another_reverts_in_order(styled: Any) -> None:
    before = _whole_style(styled)
    first = probe.apply(probe.BY_NAME["tidy"])
    second = probe.apply(probe.BY_NAME["soft"])
    probe.revert(second)
    probe.revert(first)
    assert _whole_style(styled) == before


def test_a_variant_colour_follows_the_palette_in_force(styled: Any) -> None:
    mix = dict(probe.BY_NAME["soft"].colors)["button"]
    dark = probe.color_of(mix)
    theme.set_theme("light")
    light = probe.color_of(mix)
    assert (dark.x, dark.y, dark.z) != (light.x, light.y, light.z)


def test_describe_quotes_every_value(imgui: Any) -> None:
    for variant in probe.VARIANTS:
        lines = probe.describe(variant)
        assert len(lines) == max(1, len(variant.scalars) + len(variant.colors) + variant.inset_tabs)


# -- round two: soft + wells, the sheets' cells, the middle values ----------------------

ROUND_TWO_AND_CELLS = [*probe.ROUND_TWO, *probe.CELLS]
ROLES_AND_FIXED = {f.name for f in dataclasses.fields(theme.Palette)} | set(probe.FIXED)


def test_round_two_names_are_distinct_from_each_other_and_from_round_one() -> None:
    names = [v.name for v in (*probe.VARIANTS, *ROUND_TWO_AND_CELLS)]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("variant", ROUND_TWO_AND_CELLS, ids=lambda v: v.name)
def test_a_round_two_value_names_a_style_value_of_its_kind_once(imgui: Any, variant: Any) -> None:
    style = imgui.get_style()
    names = [name for name, _ in variant.scalars]
    assert len(names) == len(set(names))
    for name, value in variant.scalars:
        held = getattr(style, name)
        if isinstance(value, tuple):
            assert isinstance(held, imgui.ImVec2), name
        else:
            assert isinstance(held, float) and value >= 0.0, name
    slots = [slot for slot, _ in variant.colors]
    assert len(slots) == len(set(slots))
    for slot, mix in variant.colors:
        assert hasattr(imgui.Col_, slot), slot
        assert {mix.base, mix.over} <= ROLES_AND_FIXED, mix
        assert 0.0 < mix.alpha <= 1.0, mix


def test_soft_wells_is_soft_with_the_frame_outline_put_back() -> None:
    soft, wells = dict(probe.SOFT.scalars), dict(probe.SOFT_WELLS.scalars)
    assert soft["frame_border_size"] == 0.0 and wells["frame_border_size"] == 1.0
    del soft["frame_border_size"], wells["frame_border_size"]
    assert wells == soft
    assert probe.SOFT_WELLS.colors == probe.SOFT.colors
    assert probe.SOFT_WELLS.inset_tabs and probe.SOFT_WELLS.tabs is None


def test_the_middle_screens_differ_from_the_base_only_in_lines_and_tab_geometry() -> None:
    middle = probe.MIDDLE
    assert middle.scalars == probe.SOFT_WELLS.scalars
    extra = dict(middle.colors)
    for slot, mix in probe.SOFT_WELLS.colors:
        assert extra.pop(slot) == mix
    assert extra == {"border": probe.LINE_MIDDLE, "separator": probe.LINE_MIDDLE}
    assert middle.tabs == probe.TAB_MIDDLE


def test_each_tab_row_varies_one_dimension_around_the_middle() -> None:
    rows = dict(probe.TAB_ROWS)
    for specs in rows.values():
        assert len(specs) == 3 and specs[1] == probe.TAB_MIDDLE
    pads, margins, sizes = rows.values()
    assert {(s.gap, s.inset, s.scale) for s in pads} == {(4.0, 2.0, 1.10)}
    assert [s.pad for s in pads] == list(probe.TAB_PADS)
    assert {(s.pad, s.scale) for s in margins} == {((10.0, 4.0), 1.10)}
    assert [(s.gap, s.inset) for s in margins] == list(probe.TAB_MARGINS)
    assert {(s.pad, s.gap, s.inset) for s in sizes} == {((10.0, 4.0), 4.0, 2.0)}
    assert [s.scale for s in sizes] == list(probe.TAB_SCALES)


def test_a_lane_is_the_inset_over_the_text_and_both_paddings() -> None:
    spec = probe.TabSpec(pad=(10.0, 4.0), gap=4.0, inset=2.0, scale=1.10)
    assert spec.lane(16.0) == pytest.approx(2.0 + 17.6 + 8.0)
    assert spec.label() == "pad 10x4 · gap 4 · inset 2 · text 1.10×"


def test_the_middle_tab_fits_the_dock_bar_the_screens_are_drawn_with() -> None:
    """
    A dock's tab bar is the UI font plus twice frame_padding.y tall (measured: 28 px
    on the screens), and a drawn lane taller than that is cut.
    """
    pad_y = dict(probe.MIDDLE.scalars)["frame_padding"][1]
    assert probe.TAB_MIDDLE.lane(16.0) <= 16.0 + 2.0 * pad_y


def test_the_middle_button_steps_are_softs_own_values() -> None:
    soft = dict(probe.SOFT.scalars)
    for steps in probe.BUTTON_STEPS.values():
        assert len(steps) == 3
        middle = dict(steps[1].scalars)
        assert middle["frame_padding"] == soft["frame_padding"]
        assert middle["frame_rounding"] == soft["frame_rounding"]


def test_the_middle_dropdown_is_the_base_as_it_stands(styled: Any) -> None:
    probe.apply(probe.SOFT_WELLS)
    style = styled.get_style()
    for name, value in probe.DROPDOWNS[1].scalars:
        assert getattr(style, name) == pytest.approx(value), name
    assert probe.DROPDOWNS[1].colors == ()


@pytest.mark.parametrize("variant", probe.ROUND_TWO, ids=lambda v: v.name)
def test_a_round_two_base_applies_and_reverts_to_what_apply_style_set(
    styled: Any, variant: Any
) -> None:
    before = _whole_style(styled)
    saved = probe.apply(variant)
    after = _whole_style(styled)
    for attr, value in variant.scalars:
        assert after[attr] == pytest.approx(value), attr
    for slot, mix in variant.colors:
        want = probe.color_of(mix)
        idx = int(getattr(styled.Col_, slot))
        assert after[f"Col_{idx}"] == pytest.approx((want.x, want.y, want.z, want.w)), slot
    probe.revert(saved)
    assert _whole_style(styled) == before


@pytest.mark.parametrize("cell", probe.CELLS, ids=lambda v: v.name)
def test_a_cell_laid_over_soft_wells_comes_off_to_exactly_apply_styles_state(
    styled: Any, cell: Any
) -> None:
    pristine = _whole_style(styled)
    base = probe.apply(probe.SOFT_WELLS)
    under = _whole_style(styled)
    with probe.laid_over(cell):
        inside = _whole_style(styled)
        for attr, value in cell.scalars:
            assert inside[attr] == pytest.approx(value), attr
        for slot, mix in cell.colors:
            want = probe.color_of(mix)
            idx = int(getattr(styled.Col_, slot))
            assert inside[f"Col_{idx}"] == pytest.approx((want.x, want.y, want.z, want.w)), slot
    assert _whole_style(styled) == under
    probe.revert(base)
    assert _whole_style(styled) == pristine


def test_a_cell_comes_off_when_its_body_raises(styled: Any) -> None:
    before = _whole_style(styled)
    with pytest.raises(RuntimeError), probe.laid_over(probe.WELL_OPTIONS[2]):
        raise RuntimeError("a widget raised")
    assert _whole_style(styled) == before


def test_a_black_mix_darkens_toward_black(styled: Any) -> None:
    dark = probe.color_of(probe.Mix("bg", "black", 0.5))
    bg = theme.current().bg.vec4
    assert (dark.x, dark.y, dark.z) == pytest.approx((bg.x / 2, bg.y / 2, bg.z / 2))


def test_cell_labels_quote_every_value() -> None:
    for cell in probe.CELLS:
        label = probe.cell_label(cell)
        for name, _ in cell.scalars:
            assert name in label, (cell.name, name)
        for slot, mix in cell.colors:
            assert f"Col_.{slot} = {mix}" in label, (cell.name, slot)
    assert probe.cell_label(probe.BUTTON_STEPS["button"][0]) == (
        "frame_padding 7x4 · frame_rounding 3"
    )


def test_describe_quotes_the_middle_tab_geometry() -> None:
    (tabs,) = [line for line in probe.describe(probe.MIDDLE) if "inset tabs" in line]
    assert probe.TAB_MIDDLE.label() in tabs


# -- contrast ------------------------------------------------------------------------


def test_contrast_of_black_on_white_is_21_and_of_a_colour_on_itself_is_1() -> None:
    assert probe.contrast((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert probe.contrast((255, 255, 255), (0, 0, 0)) == pytest.approx(21.0)
    assert probe.contrast((20, 22, 28), (20, 22, 28)) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("ink", "ground", "ratio"),
    [
        # WCAG's own worked figures: #767676 is the lightest grey at 4.5:1 on white,
        # #777777 the darkest that falls short; mid grey #808080 is 3.95:1 on white.
        ((0x76, 0x76, 0x76), (255, 255, 255), 4.54),
        ((0x77, 0x77, 0x77), (255, 255, 255), 4.48),
        ((0x80, 0x80, 0x80), (255, 255, 255), 3.95),
        ((255, 0, 0), (255, 255, 255), 4.0),
        ((0, 0, 255), (0, 0, 0), 2.44),
    ],
)
def test_contrast_matches_known_wcag_pairs(
    ink: tuple[int, int, int], ground: tuple[int, int, int], ratio: float
) -> None:
    assert probe.contrast(ink, ground) == pytest.approx(ratio, abs=0.005)


def test_contrast_agrees_with_the_themes_own_on_every_dark_role(imgui: Any) -> None:
    palette = theme.THEMES["dark"]
    bg = palette.bg
    for role in theme.role_names():
        colour = getattr(palette, role)
        assert probe.contrast(probe.rgb8(colour.vec4), probe.rgb8(bg.vec4)) == pytest.approx(
            theme.contrast_ratio(colour, bg), rel=1e-6
        ), role


def test_rgb8_rounds_as_imgui_packs() -> None:
    class V:
        def __init__(self, x: float, y: float, z: float) -> None:
            self.x, self.y, self.z = x, y, z

    assert probe.rgb8(V(0.5, 0.0, 1.0)) == (128, 0, 255)
    assert probe.rgb8(V(1.5, -0.5, 0.2)) == (255, 0, 51)


def test_the_line_levels_rise_and_the_middle_is_the_first_at_3_to_1(styled: Any) -> None:
    ground = probe.rgb8(theme.current().bg.vec4)
    ratios = [probe.contrast(probe.rgb8(probe.color_of(m)), ground) for m in probe.LINE_LEVELS]
    assert ratios == sorted(ratios) and len(set(ratios)) == len(ratios)
    first = next(n for n, r in enumerate(ratios) if r >= 3.0)
    assert probe.LINE_LEVELS[first] == probe.LINE_MIDDLE


def test_the_ratio_shown_is_the_colour_in_force_on_the_pane(styled: Any) -> None:
    with probe.laid_over(probe.LINE_CELLS[2]):
        ink = probe.rgb8(probe.color_of(probe.LINE_MIDDLE))
        ground = probe.rgb8(theme.current().bg.vec4)
        shown = probe.ratio_on_pane("border")
    assert shown == f"{probe.hex8(ink)} {probe.contrast(ink, ground):.2f}:1 on {probe.hex8(ground)}"


# -- what the buttons sheet says of ImGui ------------------------------------------------


def test_small_button_ignores_frame_padding_y_and_button_does_not(styled: Any) -> None:
    im = styled
    with probe.laid_over(probe.BUTTON_STEPS["button"][2]):
        im.new_frame()
        im.begin("probe")
        im.small_button("copy")
        small = im.get_item_rect_size().y
        im.button("launch")
        big = im.get_item_rect_size().y
        size = im.get_font_size()
        pad_y = im.get_style().frame_padding.y
        im.end()
        im.render()
    assert small == pytest.approx(size)
    assert big == pytest.approx(size + 2.0 * pad_y)


# -- round two, after review: wells at two levels, side effects on the cell, dock gaps ----


def test_the_well_options_show_border_only_fill_only_and_both_with_the_darker_fill() -> None:
    darker = probe.Mix("bg", "black", 0.5)
    by_name = {o.name: (dict(o.scalars), dict(o.colors)) for o in probe.WELL_OPTIONS}
    assert by_name["well-border-only"][0]["frame_border_size"] == 1.0
    assert by_name["well-darker-fill"] == ({"frame_border_size": 0.0}, {"frame_bg": darker})
    assert by_name["well-both"] == ({"frame_border_size": 1.0}, {"frame_bg": darker})
    # The base's own treatment is its own column, not "both".
    base = dict(probe.SOFT_WELLS.colors)["frame_bg"]
    assert by_name["well-base"] == ({"frame_border_size": 1.0}, {"frame_bg": base})


def test_the_wells_are_drawn_at_the_bases_line_level_and_at_the_middles() -> None:
    levels = [probe.LINE_LEVELS[n] for n in probe.WELL_LEVELS]
    assert levels == [probe.LINE_LEVELS[0], probe.LINE_MIDDLE]


@pytest.mark.parametrize("option", probe.WELL_OPTIONS, ids=lambda v: v.name)
@pytest.mark.parametrize("level", probe.WELL_LEVELS)
def test_a_well_cell_at_a_line_level_comes_off_to_the_base(
    styled: Any, option: Any, level: int
) -> None:
    base = probe.apply(probe.SOFT_WELLS)
    under = _whole_style(styled)
    with probe.laid_over(probe.LINE_CELLS[level]), probe.laid_over(option):
        inside = _whole_style(styled)
        border = probe.color_of(probe.LINE_LEVELS[level])
        idx = int(styled.Col_.border)
        assert inside[f"Col_{idx}"] == pytest.approx((border.x, border.y, border.z, border.w))
    assert _whole_style(styled) == under
    probe.revert(base)


def test_a_button_row_at_the_3_to_1_level_comes_off_to_the_base(styled: Any) -> None:
    base = probe.apply(probe.SOFT_WELLS)
    under = _whole_style(styled)
    for step in probe.BUTTON_STEPS["button"]:
        with probe.laid_over(step), probe.laid_over(probe.LINE_CELLS[2]):
            pass
    assert _whole_style(styled) == under
    probe.revert(base)


def test_every_value_a_cell_sets_has_a_stated_reach() -> None:
    for cell in probe.CELLS:
        lines = probe.reach(cell)
        assert len(lines) == len(cell.scalars) + len(cell.colors), cell.name


def test_changes_beyond_names_what_a_cell_changes_outside_its_sheet(styled: Any) -> None:
    probe.apply(probe.SOFT_WELLS)
    flat, framed, lifted = probe.DROPDOWNS
    within = probe.DROPDOWN_DIMENSION
    assert probe.changes_beyond(framed, within) == []
    assert [line.split(" is ")[0] for line in probe.changes_beyond(flat, within)] == [
        "as a style value, frame_border_size"
    ]
    # The arrow has no slot of its own: lifted's arrow colour is every button's fill.
    assert [line.split(" is ")[0] for line in probe.changes_beyond(lifted, within)] == [
        "as a style value, Col_.button"
    ]
    assert "every button" in probe.REACH["Col_.button"]


def test_changes_beyond_marks_every_button_step_but_softs_own(styled: Any) -> None:
    probe.apply(probe.SOFT_WELLS)
    for steps in probe.BUTTON_STEPS.values():
        marked = [bool(probe.changes_beyond(step, ())) for step in steps]
        assert marked == [True, False, True]


def test_a_dock_draws_tabs_gap_plus_inner_spacing_apart_and_gap_half_in() -> None:
    spec = probe.TabSpec(pad=(10.0, 4.0), gap=6.0, inset=4.0, scale=1.10)
    assert spec.on_dock(4.0) == (10.0, 3.0, 4.0)
    assert spec.dock_label(4.0).startswith("as drawn, on a dock: 10 px tab to tab")


def test_the_tab_cells_show_the_selected_but_unfocused_state() -> None:
    states = {state for _, state in (*probe.TAB_SAMPLE, *probe.TAB_SAMPLE_UNFOCUSED)}
    assert states == set(probe._TAB_STATES)
    assert probe._TAB_STATES["selected"][0] == "tab_dimmed_selected"


@pytest.mark.parametrize("variant", [probe.DIAGNOSTIC, probe.TAB_DIAGNOSTIC], ids=lambda v: v.name)
def test_a_diagnostic_frame_comes_off_to_exactly_what_it_was_laid_over(
    styled: Any, variant: Any
) -> None:
    before = _whole_style(styled)
    with probe.laid_over(variant):
        pass
    assert _whole_style(styled) == before


# -- the preview of the operator's pick ---------------------------------------------------


def test_the_pick_holds_exactly_the_values_the_operator_named() -> None:
    scalars = dict(probe.PICKED.scalars)
    colors = dict(probe.PICKED.colors)
    # buttons: "frame_padding 9x6 rounding 5 with no outline or frame border"
    assert scalars["frame_padding"] == (9.0, 6.0)
    assert scalars["frame_rounding"] == 5.0
    assert scalars["frame_border_size"] == 0.0
    # lines: "Level 1, Softs fill only" -- level 1 on both line slots, soft's well fill
    assert colors["border"] == colors["separator"] == probe.LINE_LEVELS[0]
    assert colors["frame_bg"] == dict(probe.WELL_OPTIONS[1].colors)["frame_bg"]
    assert dict(probe.WELL_OPTIONS[1].scalars)["frame_border_size"] == 0.0
    # dropdown: "No outline, popup rounding 4 tinted arrow"
    assert scalars["popup_border_size"] == 0.0
    assert scalars["popup_rounding"] == 4.0
    assert colors["button"] == dict(probe.SOFT.colors)["button"]
    # tabs: "pad 10x4 gap 4 inset 1 text 1.10x text 17.6 lane 26.6"
    assert probe.PICKED.inset_tabs
    assert probe.PICKED.tabs == probe.TabSpec(pad=(10.0, 4.0), gap=4.0, inset=1.0, scale=1.10)
    assert probe.PICKED.tabs.lane(16.0) == pytest.approx(26.6)


def test_the_pick_is_soft_plus_only_the_popup_outline_and_the_written_out_lines() -> None:
    soft = dict(probe.SOFT.scalars)
    picked = dict(probe.PICKED.scalars)
    assert {k: v for k, v in picked.items() if k != "popup_border_size"} == soft
    extra = [c for c in probe.PICKED.colors if c not in probe.SOFT.colors]
    assert extra == [("border", probe.LINE_LEVELS[0]), ("separator", probe.LINE_LEVELS[0])]


@pytest.mark.parametrize("name", list(theme.THEMES))
def test_the_pick_applies_and_reverts_exactly_on_every_theme(imgui: Any, name: str) -> None:
    theme.set_theme(name)
    theme.apply_style()
    try:
        before = _whole_style(imgui)
        saved = probe.apply(probe.PICKED)
        after = _whole_style(imgui)
        for attr, value in probe.PICKED.scalars:
            assert after[attr] == pytest.approx(value), attr
        for slot, mix in probe.PICKED.colors:
            want = probe.color_of(mix)
            idx = int(getattr(imgui.Col_, slot))
            assert after[f"Col_{idx}"] == pytest.approx((want.x, want.y, want.z, want.w)), slot
        probe.revert(saved)
        assert _whole_style(imgui) == before
    finally:
        theme.set_theme("dark")
        theme.apply_style()


def test_level_one_lines_are_what_apply_style_already_sets(styled: Any) -> None:
    for slot in ("border", "separator"):
        held = styled.get_style().color_(int(getattr(styled.Col_, slot)))
        assert probe.rgb8(held) == probe.rgb8(probe.color_of(probe.LINE_LEVELS[0])), slot


def test_the_picked_caption_quotes_every_picked_value() -> None:
    head, *shared = probe.picked_caption("light", 4.0)
    for part in (
        "theme light",
        "frame_padding 9x6",
        "rounding 5",
        "border 0",
        "lines level 1",
        "popup border 0 rounding 4",
        probe.PICKED_TABS.label(),
        "dock: 8 px apart",
    ):
        assert part in head, part
    assert shared == list(probe.PICKED_SHARED)
    assert "frame_border_size 0 is ONE value" in shared[0]


def test_describe_lists_the_pick() -> None:
    lines = probe.describe(probe.PICKED)
    assert "popup_border_size = 0.0" in lines
    assert any(probe.PICKED_TABS.label() in line for line in lines)


# -- the small_button wrapper sheet ---------------------------------------------------------


def test_every_wrapper_has_vertical_padding_and_a_distinct_name() -> None:
    assert len(probe.MICROS) == 4
    assert len({m.name for m in probe.MICROS}) == 4
    assert all(m.pad[1] > 0 for m in probe.MICROS)
    # A scaled font at a whole pixel, so the text is not drawn on half pixels.
    assert all((16.0 * m.scale).is_integer() for m in probe.MICROS)


def _measure(im: Any, draw: Any) -> list[float]:
    """Heights of each item ``draw`` reports, inside one frame under the pick."""
    heights: list[float] = []
    with probe.laid_over(probe.PICKED):
        im.new_frame()
        im.begin("probe")
        draw(heights)
        im.end()
        im.render()
    return heights


@pytest.mark.parametrize("kind", probe.MICRO_ROWS, ids=lambda k: getattr(k, "name", str(k)))
def test_a_wrapper_cells_measured_height_matches_its_label(styled: Any, kind: Any) -> None:
    im = styled

    def draw(out: list[float]) -> None:
        probe._press_for(kind)("copy")
        out.append(im.get_item_rect_size().y)
        out.extend([im.get_style().font_size_base, im.get_style().frame_padding.y])

    height, font_px, pad_y = _measure(im, draw)
    assert height == pytest.approx(probe.expected_height(kind, font_px, pad_y))
    if isinstance(kind, probe.Micro):
        assert f"-> {height:g} px" in kind.label(font_px)


@pytest.mark.parametrize("kind", probe.MICRO_ROWS, ids=lambda k: getattr(k, "name", str(k)))
@pytest.mark.parametrize("context", probe.MICRO_CONTEXTS, ids=lambda c: c[0])
def test_a_pane_row_is_as_tall_as_its_tallest_button(styled: Any, kind: Any, context: Any) -> None:
    im = styled
    _, _, row, _ = context

    def draw(out: list[float]) -> None:
        out.append(probe.measured_row(row, probe._press_for(kind), "k"))
        out.extend([im.get_style().font_size_base, im.get_style().frame_padding.y])

    height, font_px, pad_y = _measure(im, draw)
    assert height == pytest.approx(max(font_px, probe.expected_height(kind, font_px, pad_y)))


def test_the_wrapper_pops_everything_it_pushes(styled: Any) -> None:
    im = styled

    def draw(out: list[float]) -> None:
        before = (im.get_font_size(), tuple(im.get_style().frame_padding))
        for micro in probe.MICROS:
            probe.micro_button(f"copy##{micro.name}", micro)
        after = (im.get_font_size(), tuple(im.get_style().frame_padding))
        out.append(float(before == after))

    assert _measure(im, draw) == [1.0]


# -- after the preview: the tab label at its baked size ----------------------------------


def _glyph_ys(im: Any, size: float) -> list[float]:
    """The y of every vertex ``_paint_tab``'s label puts on a draw list, at ``size``."""
    dl = im.get_foreground_draw_list()
    font = im.get_font()
    a, b = im.ImVec2(10.0, 21.0), im.ImVec2(100.0, 48.0)
    start = dl.vtx_buffer.size()
    # _paint_tab draws the tab's rounded rectangle, then the label; the rectangle's
    # vertex count is taken by drawing it again alone.
    probe._paint_tab(dl, font, size, a, b, "unselected", "session", 10.0)
    rect = dl.vtx_buffer.size()
    dl.add_rect_filled(a, b, 0xFF000000, im.get_style().tab_rounding)
    rect = dl.vtx_buffer.size() - rect
    ys = [dl.vtx_buffer[i].pos.y for i in range(start + rect, start + rect + 4 * len("session"))]
    return ys


def test_a_tab_label_is_drawn_at_the_size_imgui_bakes(styled: Any) -> None:
    im = styled
    im.new_frame()
    try:
        px = probe.tab_text_px(probe.PICKED_TABS.scale)
        asked = im.get_style().font_size_base * probe.PICKED_TABS.scale
        # ImGui binds a whole pixel, and has a bake of exactly that size.
        assert px == round(asked) == 18.0
        assert im.get_font().get_font_baked(px).size == px
        # At that size every glyph quad is on whole pixels: the bake drawn one to one.
        assert all(float(y).is_integer() for y in _glyph_ys(im, px))
        # At the size the preview asked for, the 18 px bake is scaled and the quads
        # land between pixels: the fuzz the operator saw.
        assert im.get_font().get_font_baked(asked).size == px
        assert not all(float(y).is_integer() for y in _glyph_ys(im, asked))
    finally:
        im.render()


def test_draw_inset_tabs_hands_paint_tab_the_baked_size(
    styled: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    im = styled
    io = im.get_io()
    flags = io.config_flags
    io.config_flags |= im.ConfigFlags_.docking_enable.value
    sizes: list[float] = []
    monkeypatch.setattr(probe, "_paint_tab", lambda dl, font, size, *rest: sizes.append(size))
    try:
        for frame in range(4):
            im.new_frame()
            dock = im.dock_space_over_viewport()
            for name in ("SESSION", "BOARD"):
                im.set_next_window_dock_id(dock)
                im.begin(name)
                im.end()
            if frame == 3:
                probe.draw_inset_tabs(probe.PICKED_TABS)
            im.render()
    finally:
        io.config_flags = flags
    assert sizes, "no dock tab bar was drawn over"
    assert set(sizes) == {18.0}


# -- after the preview: the frame read back ----------------------------------------------


def test_read_png_reads_back_what_write_png_wrote(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import screenshot

    rows = [b"".join(bytes([x, y, 7]) for x in range(3)) for y in range(2)]
    out = tmp_path / "a.png"
    screenshot.write_png(out, 3, 2, rows)
    assert probe.read_png(out) == (3, 2, rows)
    assert probe.crop(rows, (1, 1, 3, 2)) == [[(1, 1, 7), (2, 1, 7)]]


def test_read_png_refuses_what_it_would_misread(tmp_path: Path) -> None:
    out = tmp_path / "a.png"
    out.write_bytes(b"not a png")
    with pytest.raises(ValueError, match="not a PNG"):
        probe.read_png(out)


def test_ink_counts_split_ink_into_solid_and_edge() -> None:
    ground, ink = (0, 0, 0), (200, 200, 200)
    pixels = [[(0, 0, 0), (20, 20, 20), (40, 40, 40), (100, 100, 100), (180, 180, 180), ink]]
    # 0.0 and 0.1 are ground; 0.2 and 0.5 are edge; 0.9 and 1.0 are solid.
    assert probe.ink_counts(pixels, ground, ink) == (4, 2, 2)
    with pytest.raises(ValueError):
        probe.ink_counts(pixels, ink, ink)


def test_zoomed_repeats_each_pixel_k_by_k() -> None:
    assert probe.zoomed([[(1, 2, 3), (4, 5, 6)]], 2) == [bytes([1, 2, 3] * 2 + [4, 5, 6] * 2)] * 2


# -- after the preview: wells lighter than the pane --------------------------------------


def test_the_well_steps_differ_from_the_pick_only_in_the_dock_well_fill() -> None:
    assert len(probe.LIGHT_WELLS) == 3
    assert list(probe.WELL_STEPS) == sorted(probe.WELL_STEPS)
    for t, variant in zip(probe.WELL_STEPS, probe.LIGHT_WELLS, strict=True):
        assert variant.scalars == probe.PICKED.scalars
        assert dict(variant.scalars)["frame_border_size"] == 0.0
        assert (variant.tabs, variant.micro, variant.inset_tabs) == (
            probe.PICKED.tabs,
            probe.PICKED.micro,
            probe.PICKED.inset_tabs,
        )
        changed = {s: m for s, m in variant.colors if dict(probe.PICKED.colors)[s] != m}
        assert changed == {"frame_bg": probe.Mix("bg", "text", t)}


@pytest.mark.parametrize("variant", probe.LIGHT_WELLS, ids=lambda v: v.name)
@pytest.mark.parametrize("name", list(theme.THEMES))
def test_a_well_step_applies_and_reverts_exactly_on_every_theme(
    imgui: Any, name: str, variant: Any
) -> None:
    theme.set_theme(name)
    theme.apply_style()
    try:
        before = _whole_style(imgui)
        saved = probe.apply(variant)
        want = probe.color_of(dict(variant.colors)["frame_bg"])
        held = imgui.get_style().color_(int(imgui.Col_.frame_bg))
        assert (held.x, held.y, held.z, held.w) == pytest.approx((want.x, want.y, want.z, 1.0))
        probe.revert(saved)
        assert _whole_style(imgui) == before
    finally:
        theme.set_theme("dark")
        theme.apply_style()


@pytest.mark.parametrize("name", list(theme.THEMES))
def test_each_step_moves_the_well_further_off_the_pane_and_keeps_text_readable(
    imgui: Any, name: str
) -> None:
    theme.set_theme(name)
    theme.apply_style()
    try:
        with probe.laid_over(probe.PICKED):
            pick = probe.read_well()
        readings = []
        for variant in probe.LIGHT_WELLS:
            with probe.laid_over(variant):
                readings.append(probe.read_well())
    finally:
        theme.set_theme("dark")
        theme.apply_style()
    ratios = [r.well_on_pane for r in readings]
    assert ratios == sorted(ratios) and ratios[0] > 1.0
    # Every step is further off the pane than the fill the operator found lost, except
    # where that fill was itself further (cde, win311, whose panel sits far off bg).
    assert ratios[-1] > pick.well_on_pane
    for r in readings:
        # WCAG AA for body text, the floor the palettes' own text is held to.
        assert r.text_in_well >= 4.5, r.line()
        # A button is still told from a well: past CIE76's just-noticeable 2.3.
        assert r.button_from_well > 2.3, r.line()


def test_delta_e_is_zero_for_a_colour_and_a_hundred_for_black_on_white() -> None:
    assert probe.delta_e((10, 20, 30), (10, 20, 30)) == 0.0
    assert probe.delta_e((0, 0, 0), (255, 255, 255)) == pytest.approx(100.0, abs=0.01)


def test_the_wells_caption_names_the_step_and_its_measurements(styled: Any) -> None:
    with probe.laid_over(probe.LIGHT_WELLS[1]):
        line = probe.wells_caption(probe.WELL_STEPS[1])
        reading = probe.read_well()
    assert "mix(bg, text, 0.1)" in line
    assert f"{reading.well_on_pane:.2f}:1" in line and f"{reading.text_in_well:.2f}:1" in line


# -- after the preview: small button D ----------------------------------------------------


def test_the_pick_draws_small_button_as_wrapper_d() -> None:
    d = probe.PICKED.micro
    assert d is probe.MICROS[3] and d.name == "D"
    assert (d.pad, d.rounding, 16.0 * d.scale, d.height(16.0)) == ((8.0, 4.0), 5.0, 14.0, 22.0)


def test_install_micro_stands_in_for_small_button_and_uninstall_puts_imgui_back(
    styled: Any,
) -> None:
    im = styled
    real = im.small_button
    probe.install_micro(probe.MICROS[3])
    try:
        assert im.small_button is not real
        assert probe.real_small_button() is real
        heights = _measure(
            im, lambda out: (im.small_button("copy"), out.append(im.get_item_rect_size().y))
        )
        assert heights == [22.0]
    finally:
        probe.uninstall_micro()
    assert im.small_button is real
    assert probe.real_small_button() is real


def test_the_picked_caption_says_the_size_the_tab_text_is_drawn_at() -> None:
    head, *_ = probe.picked_caption("dark", 4.0, 18.0)
    assert "text 1.10× drawn at 18 px" in head
    assert "drawn at" not in probe.picked_caption("dark", 4.0)[0]


# -- after the preview: the per-theme values -----------------------------------------------


@pytest.mark.parametrize("name", list(theme.THEMES))
def test_the_final_variant_applies_and_reverts_exactly_on_every_theme(
    imgui: Any, name: str
) -> None:
    theme.set_theme(name)
    theme.apply_style()
    try:
        variant = probe.final_for(name)
        before = _whole_style(imgui)
        saved = probe.apply(variant)
        for attr, value in variant.scalars:
            assert _whole_style(imgui)[attr] == pytest.approx(value), attr
        probe.revert(saved)
        assert _whole_style(imgui) == before
    finally:
        theme.set_theme("dark")
        theme.apply_style()


@pytest.mark.parametrize("name", list(theme.THEMES))
def test_only_high_contrast_keeps_the_frame_and_every_well_steps_toward_text(name: str) -> None:
    variant = probe.final_for(name)
    border = dict(variant.scalars)["frame_border_size"]
    assert border == (1.0 if name == "high_contrast" else 0.0)
    well = dict(variant.colors)["frame_bg"]
    assert (well.base, well.over) == ("bg", "text")
    assert well.alpha == (0.08 if name == "light" else probe.FINAL_WELL_T)
    # Nothing but the tuning moves the final variant off the chosen well step.
    step = probe.light_wells(probe.FINAL_WELL_T)
    tuning = probe.THEME_TUNING.get(name)
    tuned = {k for k, _ in tuning.scalars} | {k for k, _ in tuning.colors} if tuning else set()
    for key, value in variant.scalars:
        assert key in tuned or value == dict(step.scalars)[key], key
    for key, value in variant.colors:
        assert key in tuned or value == dict(step.colors)[key], key


def _quirks(im: Any, name: str, variant: Any) -> dict[str, float]:
    theme.set_theme(name)
    theme.apply_style()
    try:
        return {what: ratio for what, _, _, ratio, _ in probe.quirk_rows(variant)}
    finally:
        theme.set_theme("dark")
        theme.apply_style()


@pytest.mark.parametrize("name", ["light", "xterm"])
def test_light_and_xterm_read_at_least_as_dark_does_where_they_were_tuned(
    imgui: Any, name: str
) -> None:
    dark = _quirks(imgui, "dark", probe.final_for("dark"))
    final = _quirks(imgui, name, probe.final_for(name))
    picked = _quirks(imgui, name, probe.PICKED)
    assert final["dock well on the pane"] > 1.15 > picked["dock well on the pane"]
    assert final["text in the well"] >= 4.5 and final["hint in the well"] >= 4.5
    assert final["button on the pane"] >= dark["button on the pane"] - 0.05
    assert final["button text on the button"] >= 4.5
    # The tool block is off the pane, short of the well's step so it is not taken
    # for a field, and its inks read on it. The near-black it replaces was a slab.
    assert picked["tool block on the pane"] > 15.0
    assert 1.05 < final["tool block on the pane"] < final["dock well on the pane"]
    assert final["tool block text"] >= 4.5 and final["tool block dim text"] >= 4.5


def test_the_quirk_table_has_a_row_per_quirk_and_a_column_per_theme(imgui: Any) -> None:
    lines = probe.quirk_table(("dark", "light"))
    assert len(lines) == 2 + len(probe.QUIRKS)
    assert all(line.count("|") == 4 for line in lines)


def test_the_final_caption_names_every_tuned_value() -> None:
    line = probe.final_caption("light")
    assert "Col_.button = mix(panel_alt, accent, 0.36)" in line
    assert "tool block = mix(bg, text, 0.06)" in line
    assert "frame_border_size 1" in probe.final_caption("high_contrast")
    assert "no values of its own" in probe.final_caption("dark")


# -- after the fix-ups: the tab text variants ---------------------------------------------


def test_every_tab_text_cell_is_bound_at_the_whole_pixel_it_names(styled: Any) -> None:
    from pptmstr.ui.typeset import gate

    im = styled
    im.new_frame()
    try:
        for cell in probe.TEXT_CELLS:
            assert float(cell.px).is_integer(), cell.name
            im.push_font(None, gate.pushed(cell.px))
            bound = im.get_font_size()
            im.pop_font()
            assert bound == cell.px, cell.name
    finally:
        im.render()


def test_each_tab_text_row_varies_only_its_named_fields() -> None:
    fields = [f.name for f in dataclasses.fields(probe.TextCell) if f.name != "name"]
    ref = probe.TEXT_REFERENCE
    names = [c.name for c in probe.TEXT_CELLS]
    assert len(names) == len(set(names))
    assert set(probe.TEXT_ZOOMED) <= set(names)
    for title, varies, cells in probe.TEXT_ROWS:
        assert cells, title
        for cell in cells:
            moved = {f for f in fields if getattr(cell, f) != getattr(ref, f)}
            assert moved <= set(varies), (cell.name, moved)
            assert moved or cell.px == ref.px, cell.name


def test_the_size_row_is_15_to_18_whole_pixels() -> None:
    sizes = [c.px for c in probe.TEXT_ROWS[0][2]]
    assert sizes == [15.0, 16.0, 17.0, 18.0]


def test_placed_y_centres_each_band_on_the_tab() -> None:
    # 18 px Inconsolata as measured: ascender top 4, x-height top 7, baseline 15.
    metrics = (18.0, 4.0, 7.0, 15.0)
    assert probe.placed_y("box", 100.0, 27.0, 99.0, metrics) == 104.5
    assert probe.placed_y("lane", 100.0, 27.0, 99.0, metrics) == 104.0
    # The ascender..baseline band is 11 px: 8 above it and 8 below in 27.
    assert probe.placed_y("baseline", 100.0, 27.0, 99.0, metrics) == 104.0
    # The x-height band is 8 px: 9.5 above and below.
    assert probe.placed_y("optical", 100.0, 27.0, 99.0, metrics) == 102.5
    with pytest.raises(ValueError):
        probe.placed_y("nowhere", 100.0, 27.0, 99.0, metrics)


def _tab_pixels(height: int, ink_rows: dict[int, int], width: int = 20) -> Any:
    """A tab of ``height`` rows, row ``r`` inked over its first ``ink_rows[r]`` pixels."""
    ground, ink = (0, 0, 0), (200, 200, 200)
    return [
        [ink if x < ink_rows.get(y, 0) else ground for x in range(width)] for y in range(height)
    ]


def test_measure_gaps_reads_ascender_x_height_and_baseline() -> None:
    ground, ink = (0, 0, 0), (200, 200, 200)
    # 'session': a 2 px dot at row 8, its body rows 11..18; 'board': a stem from row 7.
    session = _tab_pixels(27, {8: 2, **{r: 16 for r in range(11, 19)}})
    board = _tab_pixels(27, {**{r: 2 for r in range(7, 11)}, **{r: 14 for r in range(11, 19)}})
    gaps = probe.measure_gaps([(board, ground, ink), (session, ground, ink)], 1)
    assert gaps == probe.Gaps(top=7, x_top=11, bottom=8)
    with pytest.raises(ValueError):
        probe.measure_gaps([(_tab_pixels(27, {}), ground, ink)], 0)


def test_a_cells_measured_line_quotes_exactly_its_measured_gaps() -> None:
    run = probe._TextRun(probe.TEXT_REFERENCE)
    assert "read back" in probe._measured_line(run)
    run.gaps = probe.Gaps(7, 11, 8)
    run.predicted = probe.Gaps(7, 11, 8)
    run.counts = (270, 111, 159)
    line = probe._measured_line(run)
    assert run.gaps.line() in line
    assert "top 7 px (ascender) / 11 px (x-height) · bottom 8 px" in line
    assert "111 solid of 270" in line and "glyph boxes agree" in line
    run.predicted = probe.Gaps(6, 11, 8)
    assert "glyph boxes say top 6 px" in probe._measured_line(run)


def test_wrapped_breaks_only_at_separators_and_keeps_every_part() -> None:
    text = "alpha · beta · gamma delta · e"
    lines = probe.wrapped(text, 15)
    assert lines == ["alpha · beta", "gamma delta · e"]
    assert " · ".join(lines) == text


def _label_vertices(im: Any, cell: Any) -> tuple[Any, list[tuple[float, float]]]:
    dl = im.get_foreground_draw_list()
    a, b = im.ImVec2(10.0, 21.0), im.ImVec2(100.0, 48.0)
    start = dl.vtx_buffer.size()
    label = probe.draw_tab_label(dl, cell, a, b, 20.0, "selected", "session")
    pos = [
        (dl.vtx_buffer[i].pos.x, dl.vtx_buffer[i].pos.y) for i in range(start, dl.vtx_buffer.size())
    ]
    return label, pos


def test_a_tab_label_is_drawn_at_its_bound_size_on_whole_pixels_unless_shifted(
    styled: Any,
) -> None:
    im = styled
    im.new_frame()
    try:
        for cell in probe.TEXT_CELLS:
            if cell.face == "prose" or cell.bold_selected:
                continue  # the bare context has neither face; the UI font stands in
            label, pos = _label_vertices(im, cell)
            assert label.px == cell.px
            # ImGui truncates the pen; a shifted cell's quads are moved by exactly its shift.
            assert label.pen_drawn == float(int(label.pen_asked)) + cell.shift[1]
            dx, dy = cell.shift
            assert pos, cell.name
            assert all(
                float(x - dx).is_integer() and float(y - dy).is_integer() for x, y in pos
            ), cell.name
            if cell.shift != (0.0, 0.0):
                assert not all(float(y).is_integer() for _, y in pos)
    finally:
        im.render()


def test_tracked_capitals_add_the_tracking_between_glyphs(styled: Any) -> None:
    im = styled
    im.new_frame()
    try:
        upper = next(c for c in probe.TEXT_CELLS if c.case == "upper")
        tracked = next(c for c in probe.TEXT_CELLS if c.case == "tracked")
        plain, _ = _label_vertices(im, upper)
        spaced, _ = _label_vertices(im, tracked)
        assert spaced.width - plain.width == probe.TRACKING * (len("session") - 1)
    finally:
        im.render()
