#!/usr/bin/env python3
"""
Probe T1 (planning/2026-10-18-vector-displays-beside-typeset-text.md §7): an
RGBA texture drawn by a host inside a real imscribe ``TypesetView`` in a real GL
context, what clips it, when it is freed, whether it has mipmaps, how legible a
figure rasterised larger and minified by the GPU is, and what the numpy-free
``image_and_size_from_encoded_data`` path can and cannot free.

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/figure_probe.py

Writes ``shots/figure-probe/*.png`` (screenshots, crops and rasters) and
``shots/figure-probe/results.json``, and prints each measurement. The write-up
is notes/vector-rendering/probe-t1.md. Nothing in ``pptmstr/`` is changed: the
probe uses pptmstr's fonts, theme and document theme, and its own ``host_for``.

Every GL call that creates, reads or frees a texture is made from
``pre_new_frame``, between frames, except the one step that asks what happens
when a texture is freed between ``imgui.image`` and the frame's render.
"""

from __future__ import annotations

import gc
import json
import math
import os
import statistics
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402
import resvg_py  # noqa: E402

OUT = ROOT / "shots" / "figure-probe"
DOC_POS = (40.0, 40.0)
DOC_SIZE = (900.0, 470.0)
FIGURE_W = 640  # the flowchart's natural width, and the width it is drawn at
# Integer ratios, and two that are not: 4.57 is a 2925 px raster shown at 640.
SCALES: tuple[float, ...] = (1, 2, 2.5, 3, 4, 4.57)
MAGENTA = (255, 0, 255)

DOCUMENT = """\
# A figure in a reply

The paragraph above the figure is ordinary prose set by the library. It runs to
a few lines so that the figure sits some way down the page, and so that a figure
drawn taller than the rectangle the view gave it would overlap prose that is
known to be here.

```figure
flowchart
```

The paragraph below the figure is prose as well. When the child window is
scrolled so that the figure is half out of view at its top, what is left of the
figure must stay inside the window, and nothing of it may land on this text.

A second paragraph below, so that there is enough of the document to scroll.
It says nothing in particular. Lorem ipsum dolor sit amet, consectetur
adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna
aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris.

A third paragraph. Duis aute irure dolor in reprehenderit in voluptate velit
esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat
non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.

A fourth paragraph, so that the document is taller than its window and can be
scrolled until the figure is half above the top edge. Sed ut perspiciatis unde
omnis iste natus error sit voluptatem accusantium doloremque laudantium, totam
rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto.

A fifth paragraph. Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut
odit aut fugit, sed quia consequuntur magni dolores eos qui ratione voluptatem
sequi nesciunt. Neque porro quisquam est, qui dolorem ipsum quia dolor sit amet.
"""


# -- the diagram -------------------------------------------------------------------


def flowchart_svg() -> str:
    """
    A flowchart-like diagram at 640 user units wide: rounded boxes and a diamond
    with 1 px strokes, 1 px connectors with arrowheads, 11 px and 10 px labels in
    the generic sans-serif family. White ground, so ink is measurable.
    """
    boxes = [
        (20, 20, 130, 40, "Receive reply", 11),
        (190, 20, 130, 40, "Parse markdown", 11),
        (360, 20, 130, 40, "Find fences", 11),
        (20, 140, 130, 40, "Sanitise SVG", 11),
        (360, 140, 130, 40, "Render raster", 11),
        (520, 140, 100, 40, "Upload texture", 10),
        (20, 260, 130, 40, "Warn and keep code", 10),
        (360, 260, 130, 40, "Draw in host", 11),
        (520, 260, 100, 40, "Evict (LRU)", 10),
    ]
    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="340" viewBox="0 0 640 340">',
        "<defs><marker id='a' viewBox='0 0 8 8' refX='8' refY='4' markerWidth='8' "
        "markerHeight='8' orient='auto'><path d='M0,0 L8,4 L0,8 z' fill='#333'/></marker></defs>",
        "<rect width='640' height='340' fill='#ffffff'/>",
    ]
    for x, y, w, h, label, size in boxes:
        out.append(
            f"<rect x='{x}.5' y='{y}.5' width='{w}' height='{h}' rx='5' fill='#eef2ff' "
            "stroke='#333' stroke-width='1'/>"
        )
        out.append(
            f"<text x='{x + w / 2}' y='{y + h / 2 + size / 3}' font-family='sans-serif' "
            f"font-size='{size}' text-anchor='middle' fill='#222'>{label}</text>"
        )
    # The decision diamond.
    out.append(
        "<path d='M255.5,120.5 L305.5,160.5 L255.5,200.5 L205.5,160.5 z' fill='#fff7e6' "
        "stroke='#333' stroke-width='1'/>"
    )
    out.append(
        "<text x='255.5' y='164' font-family='sans-serif' font-size='10' "
        "text-anchor='middle' fill='#222'>safe?</text>"
    )
    lines = [
        (150, 40, 190, 40),
        (320, 40, 360, 40),
        (425, 60, 425, 100),
        (425, 100, 85, 100),
        (85, 100, 85, 140),
        (150, 160, 205, 160),
        (305, 160, 360, 160),
        (490, 160, 520, 160),
        (255, 200, 255, 280),
        (255, 280, 150, 280),
        (570, 180, 570, 220),
        (570, 220, 425, 220),
        (425, 220, 425, 260),
        (490, 280, 520, 280),
    ]
    for x1, y1, x2, y2 in lines:
        out.append(
            f"<line x1='{x1}.5' y1='{y1}.5' x2='{x2}.5' y2='{y2}.5' stroke='#333' "
            "stroke-width='1' marker-end='url(#a)'/>"
        )
    out.append(
        "<text x='262' y='240' font-family='sans-serif' font-size='10' fill='#222'>no</text>"
    )
    out.append(
        "<text x='318' y='154' font-family='sans-serif' font-size='10' fill='#222'>yes</text>"
    )
    out.append(
        "<text x='20' y='330' font-family='sans-serif' font-size='10' fill='#555'>"
        "Figure 1. A reply's fence becomes a figure; 10 px caption, 1 px rules.</text>"
    )
    out.append("</svg>")
    return "\n".join(out)


def rasterise(svg: str, width: int) -> bytes:
    """resvg at ``width`` px, every generic family mapped to the prose face (svg-renderers.md)."""
    from imscribe import Face
    from imscribe.fontfiles import font_file

    files = [str(font_file(Face.PROSE)), str(font_file(Face.PROSE_BOLD)), str(font_file(Face.CODE))]
    family = "Roboto"
    return bytes(
        resvg_py.svg_to_bytes(
            svg_string=svg,
            width=width,
            font_files=files,
            skip_system_fonts=True,
            font_family=family,
            sans_serif_family=family,
            serif_family=family,
            monospace_family="Inconsolata Medium",
        )
    )


# -- GL helpers ----------------------------------------------------------------------


def gl() -> Any:
    from OpenGL import GL

    return GL


def is_texture(name: int) -> bool:
    return bool(gl().glIsTexture(int(name)))


def read_texture(name: int, width: int, height: int) -> np.ndarray:
    """A texture's level 0 as an HxWx4 uint8 array (rows top-down as uploaded)."""
    GL = gl()
    previous = GL.glGetIntegerv(GL.GL_TEXTURE_BINDING_2D)
    GL.glBindTexture(GL.GL_TEXTURE_2D, int(name))
    GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
    data = GL.glGetTexImage(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
    GL.glBindTexture(GL.GL_TEXTURE_2D, int(previous))
    raw = data if isinstance(data, bytes) else bytes(data)
    return np.frombuffer(raw, np.uint8).reshape(height, width, 4).copy()


def texture_params(name: int) -> dict[str, Any]:
    """What GL says about a texture's filtering and its mip levels."""
    GL = gl()
    names = {
        int(GL.GL_NEAREST): "NEAREST",
        int(GL.GL_LINEAR): "LINEAR",
        int(GL.GL_NEAREST_MIPMAP_NEAREST): "NEAREST_MIPMAP_NEAREST",
        int(GL.GL_LINEAR_MIPMAP_NEAREST): "LINEAR_MIPMAP_NEAREST",
        int(GL.GL_NEAREST_MIPMAP_LINEAR): "NEAREST_MIPMAP_LINEAR",
        int(GL.GL_LINEAR_MIPMAP_LINEAR): "LINEAR_MIPMAP_LINEAR",
    }
    previous = GL.glGetIntegerv(GL.GL_TEXTURE_BINDING_2D)
    GL.glBindTexture(GL.GL_TEXTURE_2D, int(name))

    def param(p: Any) -> int:
        return int(np.asarray(GL.glGetTexParameteriv(GL.GL_TEXTURE_2D, p)).ravel()[0])

    def level(n: int) -> tuple[int, int]:
        w = GL.glGetTexLevelParameteriv(GL.GL_TEXTURE_2D, n, GL.GL_TEXTURE_WIDTH)
        h = GL.glGetTexLevelParameteriv(GL.GL_TEXTURE_2D, n, GL.GL_TEXTURE_HEIGHT)
        return int(np.asarray(w).ravel()[0]), int(np.asarray(h).ravel()[0])

    found = {
        "min_filter": names.get(param(GL.GL_TEXTURE_MIN_FILTER), "?"),
        "mag_filter": names.get(param(GL.GL_TEXTURE_MAG_FILTER), "?"),
        "base_level": param(GL.GL_TEXTURE_BASE_LEVEL),
        "max_level": param(GL.GL_TEXTURE_MAX_LEVEL),
        "level0": level(0),
        "level1": level(1),
        "level2": level(2),
    }
    GL.glBindTexture(GL.GL_TEXTURE_2D, int(previous))
    return found


def grab() -> np.ndarray:
    """The back buffer as an HxWx3 uint8 array, top row first."""
    from imgui_bundle import imgui

    GL = gl()
    io = imgui.get_io()
    width = int(io.display_size.x * io.display_framebuffer_scale.x)
    height = int(io.display_size.y * io.display_framebuffer_scale.y)
    GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
    GL.glReadBuffer(GL.GL_BACK)
    data = GL.glReadPixels(0, 0, width, height, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
    raw = data if isinstance(data, bytes) else bytes(data)
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3)[::-1].copy()


def save(path: Path, rgb: np.ndarray) -> None:
    import screenshot

    rgb = np.ascontiguousarray(rgb[..., :3])
    path.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_png(path, rgb.shape[1], rgb.shape[0], [row.tobytes() for row in rgb])


def rss_mb() -> float:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20


def luminance(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb[..., :3].astype(np.float64)
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def box_down(rgba: np.ndarray, k: int, height: int, width: int) -> np.ndarray:
    """
    Area-average a raster k times larger down to ``height`` x ``width``: what a
    mip chain approximates.
    """
    cropped = rgba[: height * k, : width * k, :3].astype(np.float64)
    return np.asarray(cropped.reshape(height, k, width, k, 3).mean(axis=(1, 3)))


# -- the host ------------------------------------------------------------------------


class Shown:
    """What the figure host draws this frame: a texture name and its pixel size."""

    def __init__(self, name: int, width: int, height: int, keep: object = None) -> None:
        self.name, self.width, self.height, self.keep = name, width, height, keep


class FigureHost:
    """
    A ``Hosted`` widget: ``height_for`` from the texture's aspect, ``draw`` an
    ``imgui.image`` at the cursor, optionally taller than its rectangle and
    optionally inside a clip rect of its own. Records where it drew.
    """

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.text = "flowchart"

    def _size(self, width_px: float) -> tuple[float, float]:
        shown: Shown | None = self.state["shown"]
        if shown is None:
            return min(width_px, FIGURE_W), 40.0
        w = min(width_px, float(self.state["draw_w"]))
        return w, round(w * shown.height / shown.width)

    def height_for(self, width_px: float) -> float:
        return self._size(width_px)[1]

    def draw(self, *, palette: Any = None, width_px: float | None = None) -> object:
        from imgui_bundle import imgui

        shown: Shown | None = self.state["shown"]
        at = imgui.get_cursor_screen_pos()
        w, h = self._size(width_px or FIGURE_W)
        self.state["host_rect"] = (at.x, at.y, w, h)
        if shown is None:
            imgui.dummy(imgui.ImVec2(w, h))
            return None
        # Texels land on pixels only at a whole-pixel origin.
        x, y = (math.floor(at.x), math.floor(at.y)) if self.state["floor"] else (at.x, at.y)
        imgui.set_cursor_screen_pos(imgui.ImVec2(x, y))
        over = float(self.state["overflow"])
        clip = bool(self.state["clip"])
        if clip:
            imgui.push_clip_rect(imgui.ImVec2(x, y), imgui.ImVec2(x + w, y + h), True)
        imgui.image(imgui.ImTextureRef(shown.name), imgui.ImVec2(w, h + over))
        if clip:
            imgui.pop_clip_rect()
        if self.state.get("drop_after_image"):
            # The hazard: the last reference goes between image() and render.
            self.state["shown"] = None
            shown.keep = None
            gc.collect()
            self.state["dropped_name_alive_in_frame"] = is_texture(shown.name)
        return None


# -- the run -------------------------------------------------------------------------


def main() -> int:
    from imgui_bundle import hello_imgui, imgui, immapp
    from imscribe import TypesetView

    from pptmstr import theme
    from pptmstr.ui import typeset
    from pptmstr.ui.typeset import themes

    OUT.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {
        "shown": None,
        "draw_w": FIGURE_W,
        "overflow": 0,
        "clip": False,
        "floor": True,
        "scroll": 0.0,
        "frame": 0,
        "host_rect": None,
        "child_rect": None,
        "grab": None,
        "done": False,
    }
    results: dict[str, Any] = {}
    host = FigureHost(state)
    asked: list[tuple[int, str | None]] = []
    holder: dict[str, Any] = {"view": None}

    def host_for(view: Any, number: int, block: Any) -> Any:
        said = getattr(block, "language", None)
        asked.append((number, said))
        return host if said == "figure" else None

    def say(key: str, value: Any) -> None:
        results[key] = value
        print(f"{key}: {json.dumps(value, default=str)}", flush=True)

    svg = flowchart_svg()

    # -- steps: each runs between frames (pre_new_frame) and may ask for a grab --

    def frames(n: int) -> Iterator[None]:
        for _ in range(n):
            yield None

    def grab_as(name: str) -> Iterator[None]:
        state["grab"] = name
        yield None
        while state["grab"] is not None:
            yield None

    def scenario() -> Iterator[None]:
        yield from frames(10)
        say("asked_host_for", asked[:])
        say("imgui_bundle", __import__("imgui_bundle").__version__)
        say("gl_renderer", gl().glGetString(gl().GL_RENDERER).decode())
        say("gl_version", gl().glGetString(gl().GL_VERSION).decode())

        # Q1: a pattern texture in the host.
        pattern = np.zeros((300, 600, 4), np.uint8)
        pattern[...] = (*MAGENTA, 255)
        pattern[0, :] = pattern[-1, :] = pattern[:, 0] = pattern[:, -1] = (0, 255, 0, 255)
        tex = hello_imgui.create_texture_gpu_from_rgba_data(pattern)
        say(
            "q1_texture",
            {
                "id": int(tex.texture_id()),
                "w": tex.width,
                "h": tex.height,
                "id_type": type(tex.texture_id()).__name__,
            },
        )
        state["shown"] = Shown(int(tex.texture_id()), tex.width, tex.height, tex)
        state["draw_w"] = 600
        del tex
        yield from frames(5)
        yield from grab_as("q1")
        q1_rect = state["q1_host_rect"]

        # Q2: scrolled half out; then drawn taller than its rectangle; then clipped.
        child_top = state["child_rect"][1]
        local_top = q1_rect[1] - child_top
        state["scroll"] = local_top + q1_rect[3] / 2
        for name, over, clip in (("q2a", 0, False), ("q2b", 60, False), ("q2c", 60, True)):
            state["overflow"], state["clip"] = over, clip
            yield from frames(4)
            yield from grab_as(name)
        state["overflow"], state["clip"], state["scroll"] = 0, False, 0.0
        yield from frames(4)

        # Q3: lifetime. Create and drop, between frames.
        shown = state["shown"]
        state["shown"] = None
        del shown
        gc.collect()
        yield from frames(2)
        t = hello_imgui.create_texture_gpu_from_rgba_data(pattern)
        single = int(t.texture_id())
        alive = is_texture(single)
        del t
        gc.collect()
        say(
            "q3_single",
            {"id": single, "alive_while_held": alive, "alive_after_drop": is_texture(single)},
        )

        big = np.full((1500, 2000, 4), 200, np.uint8)
        mid = np.full((600, 800, 4), 200, np.uint8)
        GL = gl()
        timings: dict[str, Any] = {}
        for label, arr in (("800x600", mid), ("2000x1500", big)):
            runs = []
            for _ in range(12):
                GL.glFinish()
                t0 = time.perf_counter()
                t = hello_imgui.create_texture_gpu_from_rgba_data(arr)
                GL.glFinish()
                runs.append((time.perf_counter() - t0) * 1000)
                del t
            timings[label] = {
                "median_ms": round(statistics.median(runs), 2),
                "min_ms": round(min(runs), 2),
                "max_ms": round(max(runs), 2),
            }
        say("q3_upload_ms", timings)
        yield None

        before = rss_mb()
        ids = []
        for _ in range(60):
            t = hello_imgui.create_texture_gpu_from_rgba_data(big)
            ids.append(int(t.texture_id()))
            del t
        GL.glFinish()
        after_cycles = rss_mb()
        held = [hello_imgui.create_texture_gpu_from_rgba_data(big) for _ in range(20)]
        GL.glFinish()
        after_hold = rss_mb()
        held_ids = [int(h.texture_id()) for h in held]
        del held
        gc.collect()
        GL.glFinish()
        after_release = rss_mb()
        say(
            "q3_cycles",
            {
                "cycles_2000x1500": 60,
                "distinct_ids_over_cycles": len(set(ids)),
                "ids_first_last": (ids[0], ids[-1]),
                "rss_before_mb": round(before, 1),
                "rss_after_60_create_drop_mb": round(after_cycles, 1),
                "rss_holding_20_mb": round(after_hold, 1),
                "rss_after_releasing_20_mb": round(after_release, 1),
                "held_ids_alive_after_release": sum(is_texture(i) for i in held_ids),
                "expected_bytes_per_texture_mb": round(2000 * 1500 * 4 / 2**20, 1),
            },
        )
        yield None

        # Q3 hazard: the last reference dropped between imgui.image and render.
        t = hello_imgui.create_texture_gpu_from_rgba_data(pattern)
        state["shown"] = Shown(int(t.texture_id()), t.width, t.height, t)
        hazard_id = int(t.texture_id())
        del t
        yield from frames(3)
        state["drop_after_image"] = True
        yield from grab_as("q3_hazard")
        state["drop_after_image"] = False
        say(
            "q3_hazard",
            {
                "id": hazard_id,
                "alive_inside_frame_after_drop": state.get("dropped_name_alive_in_frame"),
            },
        )
        yield from frames(3)

        # Q4: filtering and mips, on both paths.
        t = hello_imgui.create_texture_gpu_from_rgba_data(pattern)
        say("q4_texture_gpu", texture_params(int(t.texture_id())))
        del t
        png = rasterise(svg, FIGURE_W)
        enc = hello_imgui.image_and_size_from_encoded_data(png, "q4-probe")
        say("q4_encoded", texture_params(int(enc.texture_id)))

        # Q5: legibility of k x rasters minified to the drawn width.
        rasters: dict[float, np.ndarray] = {}
        raster_ms: dict[float, float] = {}
        for k in SCALES:
            t0 = time.perf_counter()
            png = rasterise(svg, round(FIGURE_W * k))
            raster_ms[k] = round((time.perf_counter() - t0) * 1000, 1)
            # resvg gives PNG only; decode it through the encoded-data path and
            # read the texture back, since the venv has no PNG decoder.
            e = hello_imgui.image_and_size_from_encoded_data(png, f"q5-{k}")
            w, h = int(e.size.x), int(e.size.y)
            rasters[k] = read_texture(int(e.texture_id), w, h)
            save(OUT / f"raster-{k}x.png", rasters[k])
        say("q5_raster_sizes", {k: list(r.shape[:2]) for k, r in rasters.items()})
        say("q5_resvg_ms", raster_ms)
        hello_imgui.free_image_cache()
        textures = {k: hello_imgui.create_texture_gpu_from_rgba_data(r) for k, r in rasters.items()}
        state["draw_w"] = FIGURE_W
        crops: dict[float, np.ndarray] = {}
        for k in SCALES:
            tk = textures[k]
            state["shown"] = Shown(int(tk.texture_id()), tk.width, tk.height, tk)
            yield from frames(4)
            yield from grab_as(f"q5_{k}x")
            crops[k] = state[f"q5_{k}x_crop"]
        # The 1x raster at the host's cursor as the view gives it, unfloored.
        tk = textures[1]
        state["shown"] = Shown(int(tk.texture_id()), tk.width, tk.height, tk)
        state["floor"] = False
        yield from frames(4)
        yield from grab_as("q5_1x_unfloored")
        state["floor"] = True
        unfloored = state["q5_1x_unfloored_crop"]
        state["shown"] = None
        yield None
        del textures
        gc.collect()

        ref = rasters[1]
        hh, ww = crops[1].shape[:2]
        ref_l = luminance(ref[:hh, :ww])
        ink = ref_l < 110
        legibility: dict[str, Any] = {"ink_pixels_in_1x": int(ink.sum()), "drawn": [ww, hh]}
        for k in SCALES:
            cap_l = luminance(crops[k])
            kept = float(((cap_l < 110) & ink).sum() / ink.sum())
            darkness = float(np.mean((255 - cap_l[ink]) / np.maximum(1.0, 255 - ref_l[ink])))
            lost = int(((cap_l > 200) & (ref_l < 100)).sum())
            mae_ref = float(np.mean(np.abs(cap_l - ref_l)))
            entry = {
                "ink_kept_lt110": round(kept, 3),
                "ink_darkness_retained": round(darkness, 3),
                "ink_px_lost_to_white": lost,
                "mae_vs_1x_raster": round(mae_ref, 2),
            }
            if k > 1 and float(k).is_integer():
                box = box_down(rasters[k], int(k), hh, ww)
                box_l = luminance(box)
                entry["box_filter_ink_kept_lt110"] = round(
                    float(((box_l < 110) & ink).sum() / ink.sum()), 3
                )
                entry["box_filter_darkness_retained"] = round(
                    float(np.mean((255 - box_l[ink]) / np.maximum(1.0, 255 - ref_l[ink]))), 3
                )
                entry["mae_gpu_vs_box_filter"] = round(float(np.mean(np.abs(cap_l - box_l))), 2)
                save(OUT / f"box-{k}x.png", np.clip(box, 0, 255).astype(np.uint8))
            legibility[f"{k}x"] = entry
        # One strip of 10 px labels from each, nearest-neighbour 3x, for the eye:
        # 1x, then each GPU-minified ratio, then the CPU box filter of 4x.
        rows, cols = slice(262, 300), slice(10, 640)
        strips = [crops[k][rows, cols, :3] for k in SCALES]
        strips.append(np.clip(box_down(rasters[4], 4, hh, ww), 0, 255).astype(np.uint8)[rows, cols])
        gap = np.full((3, strips[0].shape[1], 3), (255, 0, 0), np.uint8)
        sheet = np.concatenate([part for s in strips for part in (s, gap)][:-1])
        save(OUT / "q5-labels-3x-zoom.png", sheet.repeat(3, axis=0).repeat(3, axis=1))
        strip_ref = ref_l[rows, cols]
        labels = [f"{k}x" for k in SCALES] + ["box_filter_4x"]
        for key, s in zip(labels, strips, strict=True):
            legibility.setdefault(key, {})["label_strip_mae_vs_1x"] = round(
                float(np.mean(np.abs(luminance(s) - strip_ref))), 2
            )
        un_l = luminance(unfloored)
        legibility["1x_at_fractional_origin"] = {
            "host_origin": [round(v, 3) for v in state["q5_1x_unfloored_rect"][:2]],
            "ink_kept_lt110": round(float(((un_l < 110) & ink).sum() / ink.sum()), 3),
            "mae_vs_1x_raster": round(float(np.mean(np.abs(un_l - ref_l))), 2),
        }
        say("q5_legibility", legibility)

        # Q6: the numpy-free path.
        png = rasterise(svg, FIGURE_W)
        a1 = hello_imgui.image_and_size_from_encoded_data(png, "fig-a")
        a2 = hello_imgui.image_and_size_from_encoded_data(png, "fig-a")
        b1 = hello_imgui.image_and_size_from_encoded_data(png, "fig-b")
        n1 = hello_imgui.image_and_size_from_encoded_data(png, "")
        n2 = hello_imgui.image_and_size_from_encoded_data(png, "")
        ids6 = {
            "a1": int(a1.texture_id),
            "a2": int(a2.texture_id),
            "b1": int(b1.texture_id),
            "nokey1": int(n1.texture_id),
            "nokey2": int(n2.texture_id),
        }
        size6 = [a1.size.x, a1.size.y]
        state["shown"] = Shown(ids6["a1"], int(size6[0]), int(size6[1]))
        yield from frames(4)
        yield from grab_as("q6")
        q6_crop = state["q6_crop"]
        state["shown"] = Shown(ids6["nokey1"], int(size6[0]), int(size6[1]))
        yield from frames(2)
        yield from grab_as("q6_nokey")
        q6_nokey_crop = state["q6_nokey_crop"]
        state["shown"] = None
        yield None
        alive_before = {k: is_texture(v) for k, v in ids6.items()}
        hello_imgui.free_image_cache()
        alive_after = {k: is_texture(v) for k, v in ids6.items()}
        a3 = hello_imgui.image_and_size_from_encoded_data(png, "fig-a")
        say(
            "q6_encoded",
            {
                "size": size6,
                "ids": ids6,
                "alive_before_free_image_cache": alive_before,
                "alive_after_free_image_cache": alive_after,
                "fig-a_after_free_new_id": int(a3.texture_id),
                "nokey_drawn_mean_luminance": round(float(luminance(q6_nokey_crop).mean()), 1),
                "drawn_matches_1x_raster_mae": round(
                    float(
                        np.mean(
                            np.abs(
                                luminance(q6_crop) - ref_l[: q6_crop.shape[0], : q6_crop.shape[1]]
                            )
                        )
                    ),
                    2,
                ),
            },
        )
        hello_imgui.free_image_cache()

        # Q1 and Q2 checks from the grabs.
        say("q1_q2", state["checks"])
        say("gl_errors_before_each_grab", state.get("gl_errors"))
        (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))
        state["done"] = True
        yield None

    steps = scenario()

    def check_grab(name: str, img: np.ndarray) -> None:
        rect = state["host_rect"]
        child = state["child_rect"]
        x, y, w, h = rect
        magenta = (img[..., 0] > 240) & (img[..., 1] < 20) & (img[..., 2] > 240)
        ys, xs = np.nonzero(magenta)
        cx0, cy0, cx1, cy1 = child
        fx0, fy0 = math.floor(x), math.floor(y)
        in_host = np.zeros_like(magenta)
        in_host[max(0, fy0) : fy0 + int(h), max(0, fx0) : fx0 + int(w)] = True
        in_child = np.zeros_like(magenta)
        in_child[int(cy0) : int(cy1), int(cx0) : int(cx1)] = True
        entry = {
            "host_rect": [round(v, 2) for v in rect],
            "child_clip": [round(v, 1) for v in child],
            "magenta_px": int(magenta.sum()),
            "magenta_bbox": (
                None
                if not len(xs)
                else [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
            ),
            "magenta_outside_host_rect": int((magenta & ~in_host).sum()),
            "magenta_outside_child": int((magenta & ~in_child).sum()),
            "scroll_y_and_max": [round(v, 1) for v in state["scroll_seen"]],
        }
        state.setdefault("checks", {})[name] = entry
        if name == "q1":
            state["q1_host_rect"] = rect

    def gui() -> None:
        viewport = imgui.get_main_viewport()
        imgui.set_next_window_pos(viewport.work_pos)
        imgui.set_next_window_size(viewport.work_size)
        flags = imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_move.value
        imgui.begin("figure_probe", None, flags)
        imgui.set_cursor_screen_pos(imgui.ImVec2(*DOC_POS))
        border = imgui.ChildFlags_.borders.value
        imgui.begin_child(
            "doc",
            imgui.ImVec2(*DOC_SIZE),
            border,
            imgui.WindowFlags_.always_vertical_scrollbar.value,
        )
        imgui.set_scroll_y(float(state["scroll"]))
        state["scroll_seen"] = (imgui.get_scroll_y(), imgui.get_scroll_max_y())
        dl = imgui.get_window_draw_list()
        lo, hi = dl.get_clip_rect_min(), dl.get_clip_rect_max()
        state["child_rect"] = (lo.x, lo.y, hi.x, hi.y)
        view = holder["view"]
        if view is None:
            view = TypesetView(
                theme=themes.document_theme(), palette=themes.palette(), host_for=host_for
            )
            view.set_document(DOCUMENT)
            holder["view"] = view
        view.draw()
        imgui.end_child()
        imgui.end()

    def pre_frame() -> None:
        typeset.pre_frame()
        if state["done"]:
            hello_imgui.get_runner_params().app_shall_exit = True
            return
        if state["grab"] is None:
            try:
                next(steps)
            except StopIteration:
                state["done"] = True

    def before_swap() -> None:
        name = state["grab"]
        if name is None:
            return
        # GL errors the frame's render left behind, read before PyOpenGL's own
        # error check on the grab's first call would raise one of them.
        errors = []
        while (code := int(gl().glGetError())) != 0:
            errors.append(code)
        state.setdefault("gl_errors", {})[name] = errors
        img = grab()
        save(OUT / f"{name}.png", img)
        if name.startswith("q5") or name.startswith("q6"):
            x, y, w, h = state["host_rect"]
            x0, y0 = math.floor(x), math.floor(y)
            crop = img[y0 : y0 + int(h), x0 : x0 + int(w)]
            state[f"{name}_crop"] = crop
            state[f"{name}_rect"] = state["host_rect"]
            save(OUT / f"{name}-crop.png", crop)
        else:
            check_grab(name, img)
        state["grab"] = None

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "figure_probe"
    params.app_window_params.window_geometry.size = (1000, 680)
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.ini_disable = True
    params.fps_idling.enable_idling = False
    params.callbacks.load_additional_fonts = theme.load_fonts
    params.callbacks.pre_new_frame = pre_frame
    params.callbacks.show_gui = gui
    params.callbacks.before_swap = before_swap
    theme.set_theme("dark")
    typeset.configure(typeset=True, mono=False)
    immapp.run(params)
    return 0 if state["done"] and "q1_q2" in results else 1


if __name__ == "__main__":
    sys.exit(main())
