"""
The font rasterisation probe: that each configuration lands on the atlas as it
says and leaves the application's own font and loader as they were, and that the
readers it measures with -- the vertex reader and the stem reader -- read
synthetic quads and pixels correctly.

Against a bare ImGui context (the ``imgui`` fixture). Nothing is rendered.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pptmstr import theme

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "font_raster_probe", ROOT / "scripts" / "font_raster_probe.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["font_raster_probe"] = module
    spec.loader.exec_module(module)
    return module


probe = _load()
WHITE = (0.01, 0.02)


def _quad(x: float, y: float, w: float, h: float, u: float = 0.5) -> list[Any]:
    """Four vertices of a textured quad, in ImGui's PrimRectUV order."""
    col = 0xFF102030
    return [
        (x, y, u, 0.1, col),
        (x + w, y, u + 0.1, 0.1, col),
        (x + w, y + h, u + 0.1, 0.2, col),
        (x, y + h, u, 0.2, col),
    ]


def _fill(x: float, y: float) -> list[Any]:
    return [(x, y, *WHITE, 0xFFFFFFFF) for _ in range(4)]


# -- the vertex reader -----------------------------------------------------------------


def test_quads_of_skips_fills_and_keeps_order() -> None:
    vertices = _fill(0, 0) + _quad(10, 20, 6, 9) + _fill(5, 5) + _quad(17.5, 20, 6, 9, 0.7)
    quads = probe.quads_of(vertices, WHITE)
    assert [(q.x0, q.y0, q.x1, q.y1) for q in quads] == [(10, 20, 16, 29), (17.5, 20, 23.5, 29)]
    assert quads[1].u0 == pytest.approx(0.7)
    assert probe.abgr_rgb(quads[0].col) == (0x30, 0x20, 0x10)


def test_quads_of_refuses_a_partial_quad() -> None:
    with pytest.raises(ValueError):
        probe.quads_of(_quad(0, 0, 1, 1)[:3], WHITE)


def test_off_grid_reads_the_farthest_edge() -> None:
    whole = probe.quads_of(_quad(10, 20, 6, 9) + _quad(18, 20, 6, 9), WHITE)
    assert probe.off_grid(whole) == 0.0
    shifted = probe.quads_of(_quad(10, 20, 6, 9) + _quad(17.625, 20.25, 6, 9), WHITE)
    assert probe.off_grid(shifted) == pytest.approx(0.375)


def test_texels_per_pixel_is_the_oversampling() -> None:
    (q,) = probe.quads_of(_quad(0, 0, 8, 10), WHITE)
    # 0.1 of a 160-texel atlas over 8 px: 2 texels a pixel.
    assert probe.texels_per_pixel(q, 160) == pytest.approx(2.0)


# -- the stem reader -------------------------------------------------------------------

G, K = (0, 0, 0), (200, 200, 200)


def _mix(t: float) -> tuple[int, int, int]:
    return tuple(int(g + (k - g) * t) for g, k in zip(G, K, strict=True))  # type: ignore[return-value]


def _i(stem: list[float], width: int = 7) -> list[list[tuple[int, int, int]]]:
    """An ``i``: a dot, a gap, an arm at the x-height, the stem, a foot."""
    blank = [G] * width
    dot = [G, G, K, K, G, G, G][:width]
    arm = [G, K, K, K, G, G, G][:width]
    foot = [G, K, K, K, K, K, G][:width]
    body = [_mix(t) for t in stem] + [G] * (width - len(stem))
    return [blank, dot, blank, arm, body, body, body, body, foot, blank]


def test_a_stem_on_whole_pixels_is_solid() -> None:
    stem = probe.stem_of(_i([0, 0, 1, 1]), G, K)
    assert stem.rows == (4, 5, 6, 7)
    assert stem.sharpness == 1.0
    assert stem.profile[:4] == (0.0, 0.0, 1.0, 1.0)


def test_a_straddling_stem_is_never_solid() -> None:
    stem = probe.stem_of(_i([0, 0, 0.83, 0.46]), G, K)
    assert stem.solid == 0 and stem.inked == 8
    assert stem.profile[2:4] == pytest.approx((0.83, 0.46), abs=0.01)


def test_the_stem_reader_needs_ink() -> None:
    with pytest.raises(ValueError):
        probe.stem_of([[G] * 3] * 3, G, K)


def test_first_i_counts_inked_glyphs_only() -> None:
    assert probe.first_i("session") == 4
    assert probe.first_i("a i") == 1


# -- the configurations ----------------------------------------------------------------


def test_every_label_has_an_i_to_read() -> None:
    assert all("i" in text for _, text in probe.LABELS)


@pytest.mark.parametrize("config", [c for c in probe.CONFIGS if c.path == "loaded"])
def test_each_configuration_lands_on_the_atlas_and_the_ui_font_is_untouched(
    imgui: Any, config: Any
) -> None:
    atlas = imgui.get_io().fonts
    before = [probe.source_fields(s) for s in atlas.sources]
    loader = atlas.font_loader_name
    import imgui_bundle

    medium = Path(imgui_bundle.__file__).parent / "assets" / probe.ASSET
    font = atlas.add_font_from_file_ttf(str(medium), 16.0, probe.font_config(config))
    try:
        added = atlas.sources[len(before)]
        assert probe.source_fields(added) == (
            config.pixel_snap_h,
            config.oversample_h,
            0,
            config.loader_flags,
        )
        assert [probe.source_fields(atlas.sources[i]) for i in range(len(before))] == before
        assert atlas.font_loader_name == loader
    finally:
        atlas.remove_font(font)
    assert [probe.source_fields(s) for s in atlas.sources] == before


def test_live_and_typeset_have_no_config() -> None:
    for key in ("a", "f"):
        with pytest.raises(ValueError):
            probe.font_config(probe.BY_KEY[key])


def test_install_loads_each_configuration_after_the_app_and_uninstall_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from imgui_bundle import hello_imgui

    calls: list[str] = []
    monkeypatch.setattr(theme, "load_fonts", lambda: calls.append("app"))
    real = theme.load_fonts

    def load_font_ttf(asset: str, size: float, cfg: Any) -> str:
        calls.append(f"{asset} {size:g} {probe.source_fields(cfg)}")
        return f"font {len(calls)}"

    monkeypatch.setattr(hello_imgui, "load_font_ttf", load_font_ttf)
    probe.install()
    try:
        assert theme.load_fonts is not real
        with pytest.raises(RuntimeError):
            probe.install()
        theme.load_fonts()
        loaded = [c for c in probe.CONFIGS if c.path == "loaded"]
        assert calls[0] == "app"
        assert calls[1:] == [
            f"{probe.ASSET} {theme._FONT_SIZE:g}"
            f" {(c.pixel_snap_h, c.oversample_h, 0, c.loader_flags)}"
            for c in loaded
        ]
        assert set(probe._Fonts.by_key) == {c.key for c in loaded}
    finally:
        probe.uninstall()
    assert theme.load_fonts is real
    assert probe._Fonts.by_key == {}
