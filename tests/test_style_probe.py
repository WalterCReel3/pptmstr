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
