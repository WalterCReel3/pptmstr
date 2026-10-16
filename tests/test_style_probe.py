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
