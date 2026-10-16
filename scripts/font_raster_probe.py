#!/usr/bin/env python3
"""
Why ImGui UI text does or does not sit on the pixel grid, measured in the running app.

The operator, on the tab text sheet: "Are imgui tabs just rendered in an awkward
way? None of these outside of 15px look like good pixel-grid alignment."

Nothing here changes what the app draws. The probe wraps ``theme.load_fonts`` in
this process only, so that after pptmstr's own fonts (and the text library's) it
loads the same Inconsolata Medium file again once per configuration, through the
same hello_imgui call pptmstr uses (``hello_imgui.load_font_ttf``), each with its
own ``ImFontConfig``. ImGui 1.92 bakes a font per size on demand, under that
font's own config, so each configuration is a separate ``ImFont`` and nothing is
reloaded: the live UI font (``Fonts[0]``) is never touched, and configuration (a)
is that font itself. A control (a0) is the file loaded by the probe with a
default config, so that a difference between (a) and the others can be told from
a difference the probe's own loading makes.

Each cell draws, at one whole-pixel size pushed with ``push_font``, a real ImGui
tab bar with a ``session`` tab, a ``menu_item`` and a ``button``, inside a child
window of its own, and reads back:

    from the draw list   the glyph quads (vertices whose uv is not the atlas's
                         white pixel), their x off the whole pixel, and the
                         texels per pixel of the ``i`` quad (the oversampling
                         actually in the atlas);
    from the frame       the ``i`` stem: the rows of the ``i``'s body where its
                         ink is narrowest, the share of their inked pixels that
                         are solid, and the column profile across them.

Row (f) draws the same words through the text library, via pptmstr's seam
(``gate.literal_view`` and ``themes.literal_style`` set at the cell's size in the
CODE face), the way the panes set literal text.

    scripts/font_raster_probe.py                 # dark and light, each on an Xvfb
    scripts/font_raster_probe.py --theme dark    # one theme on the display there is
    scripts/font_raster_probe.py --facts         # the loader and defaults, bare context
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import functools
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import style_probe as sp  # noqa: E402

OUT = ROOT / "shots" / "style-preview" / "font-raster"
THEMES = ("dark", "light")
SIZES = (15.0, 16.0, 17.0, 18.0)
ASSET = "fonts/Inconsolata-Medium.ttf"

# imgui_freetype.h's ImGuiFreeTypeLoaderFlags_ (v1.92); the binding exposes no enum.
FT_NO_HINTING = 1 << 0
FT_FORCE_AUTO_HINT = 1 << 2
FT_LIGHT_HINTING = 1 << 3
FT_MONO_HINTING = 1 << 4
FT_MONOCHROME = 1 << 7

# The words drawn in each cell, and the widget that draws them. Each has an ``i``.
LABELS = (("tab", "session"), ("menu", "Settings"), ("button", "Dismiss"))


@dataclass(frozen=True, slots=True)
class Config:
    """One rasterisation configuration: an ``ImFontConfig`` or a drawing path."""

    key: str
    title: str
    pixel_snap_h: bool = False
    oversample_h: int = 0
    loader_flags: int = 0
    # "live": the app's own UI font. "loaded": the file again under this config.
    # "typeset": the words set by the text library through the seam.
    path: str = "loaded"
    freetype_only: bool = False


CONFIGS: tuple[Config, ...] = (
    Config("a", "today: the app's UI font, theme.face(BODY)", path="live"),
    Config("a0", "control: the file again, default ImFontConfig"),
    Config("b", "pixel_snap_h True", pixel_snap_h=True),
    Config("c", "oversample_h 1", oversample_h=1),
    Config("d", "pixel_snap_h True + oversample_h 1", pixel_snap_h=True, oversample_h=1),
    Config(
        "e0",
        "FreeType NoHinting (what hinting does)",
        loader_flags=FT_NO_HINTING,
        freetype_only=True,
    ),
    Config("e1", "FreeType LightHinting", loader_flags=FT_LIGHT_HINTING, freetype_only=True),
    Config("e2", "FreeType ForceAutoHint", loader_flags=FT_FORCE_AUTO_HINT, freetype_only=True),
    Config("e3", "FreeType Monochrome", loader_flags=FT_MONOCHROME, freetype_only=True),
    Config(
        "e4",
        "FreeType Monochrome + MonoHinting",
        loader_flags=FT_MONOCHROME | FT_MONO_HINTING,
        freetype_only=True,
    ),
    Config("f", "imtypeset via the seam (literal view, CODE)", path="typeset"),
)
BY_KEY = {c.key: c for c in CONFIGS}


def font_config(config: Config) -> Any:
    """The ``ImFontConfig`` a loaded configuration's font is added with."""
    from imgui_bundle import imgui

    if config.path != "loaded":
        raise ValueError(f"{config.key} is not loaded from a config")
    cfg = imgui.ImFontConfig()
    cfg.pixel_snap_h = config.pixel_snap_h
    cfg.oversample_h = config.oversample_h
    cfg.font_loader_flags = config.loader_flags
    return cfg


def source_fields(cfg: Any) -> tuple[bool, int, int, int]:
    """``(pixel_snap_h, oversample_h, oversample_v, font_loader_flags)`` of an ImFontConfig."""
    return (
        bool(cfg.pixel_snap_h),
        int(cfg.oversample_h),
        int(cfg.oversample_v),
        int(cfg.font_loader_flags),
    )


class _Fonts:
    """The configuration fonts on the atlas, by key, once ``load_fonts`` has run."""

    by_key: dict[str, Any] = {}
    real_load: Any = None


def install() -> None:
    """
    Wrap ``theme.load_fonts`` so that, after everything pptmstr loads, each loaded
    configuration's font is added. Must run before ``app.main`` reads it.
    """
    from pptmstr import theme

    if _Fonts.real_load is not None:
        raise RuntimeError("already installed")
    real = theme.load_fonts
    _Fonts.real_load = real

    @functools.wraps(real)
    def load_fonts() -> None:
        from imgui_bundle import hello_imgui

        real()
        _Fonts.by_key = {}
        for config in CONFIGS:
            if config.path == "loaded":
                _Fonts.by_key[config.key] = hello_imgui.load_font_ttf(
                    ASSET, theme._FONT_SIZE, font_config(config)
                )

    theme.load_fonts = load_fonts  # type: ignore[assignment]


def uninstall() -> None:
    from pptmstr import theme

    if _Fonts.real_load is not None:
        theme.load_fonts = _Fonts.real_load  # type: ignore[assignment]
    _Fonts.real_load = None
    _Fonts.by_key = {}


# -- reading the draw list --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Quad:
    """One textured quad: its screen rectangle, its uv rectangle, its colour (ABGR u32)."""

    x0: float
    y0: float
    x1: float
    y1: float
    u0: float
    v0: float
    u1: float
    v1: float
    col: int


Vertex = tuple[float, float, float, float, int]


def quads_of(vertices: list[Vertex], white: tuple[float, float]) -> list[Quad]:
    """
    The textured quads among ``(x, y, u, v, col)`` vertices, in the order drawn.
    Fills and rules carry the atlas's white pixel as their uv and are left out.
    """
    textured = [v for v in vertices if abs(v[2] - white[0]) > 1e-9 or abs(v[3] - white[1]) > 1e-9]
    if len(textured) % 4:
        raise ValueError(f"{len(textured)} textured vertices is not a whole number of quads")
    out = []
    for at in range(0, len(textured), 4):
        xs, ys, us, vs, cols = zip(*textured[at : at + 4], strict=True)
        out.append(
            Quad(min(xs), min(ys), max(xs), max(ys), min(us), min(vs), max(us), max(vs), cols[0])
        )
    return out


def off_grid(quads: list[Quad]) -> float:
    """The farthest any quad's left or top edge is from a whole pixel."""
    worst = 0.0
    for q in quads:
        for v in (q.x0, q.y0):
            worst = max(worst, abs(v - round(v)))
    return worst


def texels_per_pixel(quad: Quad, tex_width: int) -> float:
    """The atlas texels across a quad per screen pixel: the horizontal oversampling."""
    wide = quad.x1 - quad.x0
    return (quad.u1 - quad.u0) * tex_width / wide if wide else 0.0


def abgr_rgb(col: int) -> tuple[int, int, int]:
    return (col & 0xFF, (col >> 8) & 0xFF, (col >> 16) & 0xFF)


def read_vertices(dl: Any, start: int, stop: int) -> list[Vertex]:
    buf = dl.vtx_buffer
    out = []
    for i in range(start, stop):
        v = buf[i]
        out.append((float(v.pos.x), float(v.pos.y), float(v.uv.x), float(v.uv.y), int(v.col)))
    return out


# -- reading the frame -------------------------------------------------------------------

Pixels = list[list[tuple[int, int, int]]]


def along(
    p: tuple[int, int, int], ground: tuple[int, int, int], ink: tuple[int, int, int]
) -> float:
    """How far ``p`` is from ``ground`` toward ``ink``, projected on the line between."""
    span = [i - g for i, g in zip(ink, ground, strict=True)]
    norm = sum(s * s for s in span)
    if norm == 0:
        raise ValueError("ink and ground are the same colour")
    return sum((c - g) * s for c, g, s in zip(p, ground, span, strict=True)) / norm


def ground_of(pixels: Pixels) -> tuple[int, int, int]:
    """The commonest colour: the fill a label sits on."""
    return collections.Counter(p for row in pixels for p in row).most_common(1)[0][0]


@dataclass(frozen=True, slots=True)
class Stem:
    """A glyph's vertical stem read from the frame."""

    rows: tuple[int, ...]  # the rows read, within the crop
    profile: tuple[float, ...]  # mean ink across those rows, per column of the crop
    inked: int
    solid: int

    @property
    def sharpness(self) -> float:
        return self.solid / self.inked if self.inked else 0.0

    def line(self) -> str:
        cols = " ".join(f"{t:.2f}" for t in self.profile if t >= 0.02)
        return f"{self.sharpness:.0%} solid ({self.solid}/{self.inked}) · {cols}"


def stem_of(pixels: Pixels, ground: tuple[int, int, int], ink: tuple[int, int, int]) -> Stem:
    """
    The stem of an ``i`` cropped with a margin: its body is the lowest run of rows
    with ink (the dot is the run above), and the stem rows are the body's rows
    where the ink is narrowest, which leaves out the arm at the x-height and the
    foot. A pixel is inked at ``INK_LO`` of the way to ``ink`` and solid at ``INK_HI``.
    """
    t = [[along(p, ground, ink) for p in row] for row in pixels]
    widths = [sum(1 for v in row if v >= sp.INK_LO) for row in t]
    inked_rows = [i for i, n in enumerate(widths) if n]
    if not inked_rows:
        raise ValueError("no ink")
    body = [inked_rows[-1]]
    for i in reversed(inked_rows[:-1]):
        if i != body[-1] - 1:
            break
        body.append(i)
    narrow = min(widths[i] for i in body)
    rows = tuple(sorted(i for i in body if widths[i] == narrow))
    cols = len(pixels[0])
    profile = tuple(
        round(max(0.0, min(1.0, sum(t[r][c] for r in rows) / len(rows))), 2) for c in range(cols)
    )
    inked = sum(1 for r in rows for v in t[r] if v >= sp.INK_LO)
    solid = sum(1 for r in rows for v in t[r] if v >= sp.INK_HI)
    return Stem(rows, profile, inked, solid)


def first_i(text: str) -> int:
    """The index among ``text``'s inked glyphs (no spaces) of its first ``i``."""
    return [c for c in text if not c.isspace()].index("i")


# -- the sheet ---------------------------------------------------------------------------


@dataclass(slots=True)
class Drawn:
    """One label as drawn: where, from what, and what it measured."""

    config: str
    px: float
    widget: str
    text: str
    quads: list[Quad] = dataclasses.field(default_factory=list)
    bake: str = ""
    note: str = ""
    # Measured from the frame.
    stem: Stem | None = None
    edge: tuple[int, int, int] = (0, 0, 0)
    pixels: Pixels = dataclasses.field(default_factory=list)

    @property
    def box(self) -> tuple[int, int, int, int]:
        x0 = min(q.x0 for q in self.quads)
        y0 = min(q.y0 for q in self.quads)
        x1 = max(q.x1 for q in self.quads)
        y1 = max(q.y1 for q in self.quads)
        return (int(x0) - 2, int(y0) - 2, int(x1 + 0.999) + 2, int(y1 + 0.999) + 2)

    def i_box(self) -> tuple[int, int, int, int]:
        q = self.quads[first_i(self.text)]
        return (int(q.x0) - 1, int(q.y0) - 1, int(q.x1 + 0.999) + 1, int(q.y1 + 0.999) + 1)

    def quads_line(self) -> str:
        n = len([c for c in self.text if not c.isspace()])
        if len(self.quads) != n:
            return f"{len(self.quads)} quads for {n} glyphs"
        off = off_grid(self.quads)
        return "quads on whole px" if off < 1e-4 else f"quads off grid by up to {off:.3f}"


class _Sheet:
    drawn: dict[tuple[str, float, str], Drawn] = {}
    measured = False
    facts: list[str] = []
    views: dict[tuple[float, str], Any] = {}
    loader: str = ""


SHEET_SIZE = (2900, 2000)
TITLE_W = 360
CELL_W = 330
CELL_H = 160
ZOOM_X = TITLE_W + CELL_W * len(SIZES) + 10


def loader_name() -> str:
    from imgui_bundle import imgui

    return str(imgui.get_io().fonts.font_loader_name or "")


def shown_configs() -> tuple[Config, ...]:
    freetype = loader_name().startswith("FreeType")
    return tuple(c for c in CONFIGS if freetype or not c.freetype_only)


def _font_for(config: Config) -> Any:
    from pptmstr import theme

    if config.path == "live":
        return theme.face(theme.Face.BODY)
    return _Fonts.by_key.get(config.key)


def _bake_line(font: Any, px: float) -> str:
    from imgui_bundle import imgui

    baked = font.get_font_baked(px)
    return (
        f"bake {baked.size:g} px · advance {baked.get_char_advance(ord('i')):g}"
        f" · density {baked.rasterizer_density:g} · {imgui.get_font_size():g} bound"
    )


def _imgui_cell(config: Config, px: float, x: float, y: float) -> None:
    """The three widgets at ``px`` in ``config``'s font, each label's quads recorded."""
    from imgui_bundle import imgui

    font = _font_for(config)
    imgui.push_font(font, px)
    try:
        dl = imgui.get_window_draw_list()
        bake = _bake_line(imgui.get_font(), px)
        for widget, text in LABELS:
            key = (config.key, px, widget)
            start = dl.vtx_buffer.size()
            if widget == "tab":
                imgui.set_cursor_screen_pos(imgui.ImVec2(x, y))
                if imgui.begin_tab_bar("tb"):
                    shown, _ = imgui.begin_tab_item(text)
                    if shown:
                        imgui.end_tab_item()
                    imgui.end_tab_bar()
            elif widget == "menu":
                imgui.set_cursor_screen_pos(imgui.ImVec2(x, y + 34.0))
                imgui.menu_item(text, "", False)
            else:
                imgui.set_cursor_screen_pos(imgui.ImVec2(x, y + 60.0))
                imgui.button(text)
            _record(key, config, px, widget, text, dl, start, bake)
    finally:
        imgui.pop_font()


def _typeset_view(px: float, text: str) -> Any:
    from imscribe import Face, MarkdownCode

    from pptmstr.ui.typeset import gate, roles, themes

    held = _Sheet.views.get((px, text))
    if held is None:
        style = themes._sized(themes.literal_style(roles.Role.LITERAL), Face.CODE, px, px)
        held = gate.literal_view(themes.document_theme())
        held.set_document([MarkdownCode(style, text)])
        _Sheet.views[(px, text)] = held
    return held


def _typeset_cell(config: Config, px: float, x: float, y: float) -> None:
    from imgui_bundle import imgui

    from pptmstr.ui.typeset import gate, themes

    if not gate.can_draw():
        imgui.set_cursor_screen_pos(imgui.ImVec2(x, y))
        imgui.text_disabled(f"library not drawing: {gate.unavailable_reason()}")
        return
    dl = imgui.get_window_draw_list()
    for i, (widget, text) in enumerate(LABELS):
        imgui.set_cursor_screen_pos(imgui.ImVec2(x + 10.0, y + 4.0 + 28.0 * i))
        start = dl.vtx_buffer.size()
        _typeset_view(px, text).draw(palette=themes.palette(), width_px=CELL_W - 30.0)
        drawn = _record((config.key, px, widget), config, px, widget, text, dl, start, "")
        if drawn is not None and drawn.quads and not drawn.bake:
            code = gate.face_font("CODE")
            q = drawn.quads[first_i(text)]
            baked = gate._bake_of(code, "i", (q.x0, q.x1, q.u0, q.v0)) if code else None
            drawn.bake = (
                "bake: not on CODE's sizes"
                if baked is None
                else f"bake {baked.size:g} px · advance {baked.get_char_advance(ord('i')):g}"
            )


def _record(
    key: tuple[str, float, str],
    config: Config,
    px: float,
    widget: str,
    text: str,
    dl: Any,
    start: int,
    bake: str,
) -> Drawn | None:
    from imgui_bundle import imgui

    if _Sheet.measured:
        return _Sheet.drawn.get(key)
    w = imgui.get_font_tex_uv_white_pixel()
    quads = quads_of(read_vertices(dl, start, dl.vtx_buffer.size()), (w.x, w.y))
    drawn = Drawn(config.key, px, widget, text, quads, bake)
    _Sheet.drawn[key] = drawn
    return drawn


def draw_sheet() -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    dl = imgui.get_window_draw_list()
    font = imgui.get_font()
    text_u32 = imgui.get_color_u32(imgui.Col_.text)
    dim_u32 = imgui.get_color_u32(imgui.Col_.text_disabled)

    def caption(x: float, y: float, s: str, colour: int = dim_u32, size: float = 13.0) -> None:
        dl.add_text(font, size, imgui.ImVec2(x, y), colour, s)

    caption(
        8,
        6,
        f"font rasterisation -- theme {theme.current().name} · loader {loader_name()} · each"
        " cell: a real tab bar, menu_item and button pushed at a whole px, read back from the"
        " frame and the draw list",
        text_u32,
        16.0,
    )
    for i, line in enumerate(_Sheet.facts[:6]):
        caption(8, 28 + 14 * i, line)
    top = 140.0
    for c, px in enumerate(SIZES):
        caption(TITLE_W + c * CELL_W, top - 18, f"{px:g} px", text_u32, 15.0)
    caption(
        ZOOM_X,
        top - 18,
        f"the 'session' tab label at {sp.ZOOM}x, nearest neighbour, 15..18 px",
        text_u32,
        15.0,
    )
    for r, config in enumerate(shown_configs()):
        y = top + r * CELL_H
        caption(8, y, f"({config.key}) {config.title}", text_u32)
        if config.path == "loaded":
            src = _Fonts.by_key.get(config.key)
            cfg_line = (
                f"snap_h {config.pixel_snap_h} · oversample_h {config.oversample_h}"
                f" · flags {config.loader_flags:#x}"
            )
            caption(8, y + 16, cfg_line if src is not None else "(font did not load)")
        for c, px in enumerate(SIZES):
            x = float(TITLE_W + c * CELL_W)
            imgui.set_cursor_screen_pos(imgui.ImVec2(x, y))
            imgui.push_id(f"{config.key}-{px:g}")
            if imgui.begin_child(
                "cell",
                imgui.ImVec2(CELL_W - 8.0, 88.0),
                0,
                imgui.WindowFlags_.no_scrollbar | imgui.WindowFlags_.no_background,
            ):
                if config.path == "typeset":
                    _typeset_cell(config, px, x + 4.0, y + 2.0)
                elif _font_for(config) is not None:
                    _imgui_cell(config, px, x + 4.0, y + 2.0)
            imgui.end_child()
            imgui.pop_id()
            _cell_caption(caption, config, px, x, y + 90.0)
        _zoom_row(dl, config, y)


def _cell_caption(caption: Any, config: Config, px: float, x: float, y: float) -> None:
    tab = _Sheet.drawn.get((config.key, px, "tab"))
    if tab is None:
        return
    caption(x, y, tab.bake[:52])
    lines = []
    for widget, _ in LABELS:
        d = _Sheet.drawn.get((config.key, px, widget))
        if d is None or d.stem is None:
            continue
        inked, _, edge = d.edge
        lines.append(f"{widget[:3]}: i {d.stem.line()}"[:52])
        lines.append(f"     {d.quads_line()} · edge {edge / inked if inked else 0:.0%}"[:52])
    for i, line in enumerate(lines[:4]):
        caption(x, y + 13 * (i + 1), line)


def _zoom_row(dl: Any, config: Config, y: float) -> None:
    from imgui_bundle import imgui

    x = float(ZOOM_X)
    for px in SIZES:
        d = _Sheet.drawn.get((config.key, px, "tab"))
        if d is None or not d.pixels:
            continue
        for py, row in enumerate(d.pixels):
            for qx, rgb in enumerate(row):
                lo = imgui.ImVec2(x + qx * sp.ZOOM, y + py * sp.ZOOM)
                dl.add_rect_filled(
                    lo, imgui.ImVec2(lo.x + sp.ZOOM, lo.y + sp.ZOOM), sp._rgb_u32(rgb)
                )
        x += len(d.pixels[0]) * sp.ZOOM + 12.0


sp._SHEET_DRAWERS["font-raster"] = draw_sheet


def measure(raw: Path) -> list[str]:
    """Read the frame back: each label's stem and edge share; the lines to print."""
    _, _, rows = sp.read_png(raw)
    lines = []
    for d in _Sheet.drawn.values():
        n = len([c for c in d.text if not c.isspace()])
        if len(d.quads) != n:
            lines.append(f"{d.config:3} {d.px:g} {d.widget:6} {d.quads_line()}: not measured")
            continue
        ink = abgr_rgb(d.quads[0].col)
        box = sp.crop(rows, d.box)
        ground = ground_of(box)
        d.pixels = box if d.widget == "tab" else []
        d.edge = sp.ink_counts(box, ground, ink)
        d.stem = stem_of(sp.crop(rows, d.i_box()), ground, ink)
        inked, _, edge = d.edge
        lines.append(
            f"{d.config:3} {d.px:4g} {d.widget:6} {d.bake[:40]:40} | i {d.stem.line()} |"
            f" {d.quads_line()} | edge {edge}/{inked} {edge / inked if inked else 0:.0%}"
        )
    _Sheet.measured = True
    return lines


def facts() -> list[str]:
    """The loader and the defaults in force, read from the live atlas."""
    from imgui_bundle import imgui

    io = imgui.get_io()
    atlas = io.fonts
    style = imgui.get_style()
    has_textures = bool(io.backend_flags & imgui.BackendFlags_.renderer_has_textures)
    out = [
        f"loader {atlas.font_loader_name!r} · atlas font_loader_flags {atlas.font_loader_flags:#x}"
        f" · renderer_has_textures {has_textures}"
        f" · texture {atlas.tex_data.width}x{atlas.tex_data.height}",
        f"style font_size_base {style.font_size_base:g} · font_scale_main {style.font_scale_main:g}"
        f" · font_scale_dpi {style.font_scale_dpi:g} · io.font_global_scale"
        f" {getattr(io, 'font_global_scale', 'n/a')}",
    ]
    for i, src in enumerate(atlas.sources):
        name = src.dst_font.get_debug_name() if src.dst_font is not None else "?"
        snap, osh, osv, flags = source_fields(src)
        out.append(
            f"source {i}: {name} size_pixels {src.size_pixels:g} merge {src.merge_mode}"
            f" · pixel_snap_h {snap} · oversample_h {osh} v {osv} · font_loader_flags {flags:#x}"
            f" · rasterizer_density {src.rasterizer_density:g}"
        )
    return out


def _scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    import screenshot

    from pptmstr import theme

    del fixtures
    yield from sp._sheet_frame(state, SHEET_SIZE)
    _Sheet.facts = facts()
    for line in _Sheet.facts:
        print(line, flush=True)
    for line in _live_bakes():
        print(line, flush=True)
    name = theme.current().name
    _Sheet.drawn, _Sheet.measured = {}, False
    sp._Shown.sheet, sp._Shown.sheet_first = "font-raster", True
    yield from screenshot._frames(12)
    yield f"{name}-raw"
    yield from screenshot._frames(2)
    assert sp._Shown.out_dir is not None
    for line in measure(sp._Shown.out_dir / f"{name}-raw.png"):
        print(line, flush=True)
    yield from screenshot._frames(5)
    yield name
    sp._Shown.sheet = None


def _live_bakes() -> list[str]:
    """For each configuration font, each size's advance and the i quad's oversampling."""
    from imgui_bundle import imgui

    tex_w = int(imgui.get_io().fonts.tex_data.width)
    out = []
    for config in CONFIGS:
        font = _font_for(config)
        if font is None or config.path == "typeset":
            continue
        for px in SIZES:
            baked = font.get_font_baked(px)
            g = baked.find_glyph(ord("i"))
            wide = float(g.x1 - g.x0)
            texels = (g.u1 - g.u0) * tex_w / wide if wide else 0.0
            out.append(
                f"bake ({config.key}) {px:g}: size {baked.size:g} advance i"
                f" {baked.get_char_advance(ord('i')):g} · i x0 {g.x0:g} x1 {g.x1:g}"
                f" · texels/px {texels:.2f}"
            )
    return out


def _run(theme_name: str, out_dir: Path) -> int:
    install()
    return sp._run_one(sp.CURRENT, out_dir, _scenario, theme_name)


def _bare_facts() -> int:
    """The loader and the ImFontConfig defaults in a bare context (no backend)."""
    import imgui_bundle
    from imgui_bundle import imgui

    imgui.create_context()
    cfg = imgui.ImFontConfig()
    print(f"ImFontConfig() defaults: {source_fields(cfg)} (pixel_snap_h, oversample_h, v, flags)")
    path = Path(imgui_bundle.__file__).parent / "assets" / ASSET
    imgui.get_io().fonts.add_font_from_file_ttf(str(path), 16.0, cfg)
    print(f"loader after a font is added: {imgui.get_io().fonts.font_loader_name!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--theme", default=None, help="one theme, on the display there is")
    ap.add_argument("--facts", action="store_true", help="bare-context loader facts")
    ap.add_argument("--out-dir", type=Path, default=OUT)
    args = ap.parse_args()
    if args.facts:
        return _bare_facts()
    if args.theme is not None:
        return _run(args.theme, args.out_dir)
    screen = f"{SHEET_SIZE[0] + 100}x{SHEET_SIZE[1] + 100}x24"
    for name in THEMES:
        started = time.monotonic()
        code = _on_xvfb(["--theme", name, "--out-dir", str(args.out_dir)], screen)
        print(f"font-raster {name}: exit {code} in {time.monotonic() - started:.0f}s", flush=True)
        if code != 0 or sp._missing(args.out_dir, (name, f"{name}-raw")):
            return code or 1
    return 0


def _on_xvfb(args: list[str], screen: str) -> int:
    import os
    import subprocess

    env = dict(os.environ, XVFB_SHOT_SCREEN=screen)
    return subprocess.run(
        [
            str(ROOT / "scripts" / "xvfb_shot.sh"),
            "--",
            str(ROOT / ".venv" / "bin" / "python"),
            str(Path(__file__).resolve()),
            *args,
        ],
        cwd=ROOT,
        env=env,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
