#!/usr/bin/env python3
"""
Where advtext's ``TypesetDiffView`` puts the glyphs of a diff's rows, as numbers.

The question is the one ``scripts/measure_typeset_advances.py`` asks of a fenced
block (R4 and R5 of ``HANDOFF-advtext.md``, requests P21 and P22), asked of the
view an approval's diff would be drawn with: do N characters of a row occupy N
times one whole-pixel constant on every row, is that constant ImGui's baked
advance, and does a point on a drawn glyph answer that glyph's character. It
measures and decides nothing, and says nothing about how anything looks.

## The window

That script's: the chrome's Inconsolata as ``Fonts[0]`` through hello_imgui,
then ``typesetter.load_fonts()``. One page a pixel height (14, 16, 18, 20). A
page is one ``TypesetDiffView`` of one file whose every row is added --
that script's seven code rows, the 40-character ruler and the 74-character
line among them -- unified, 1100 px wide, sized with ``pixel_height``.

## What is read, and from what

Nothing is read from pixels.

    drawn   the textured quads the view's ``draw`` added to the window's draw
            list. A quad belongs to the display row its vertical centre falls
            in (the view's origin and ``grid.row``); the last N quads of a row
            from the left are its code, N being the row's characters with ink,
            and the ones before them its gutter. The pen is the quad's left
            edge less the glyph's ``x0`` in the library's own atlas entry.
    cell    ``view.grid.cell``: the advance the view lays a column out at.
    chrome  where ``ImDrawList.add_text`` in ``Fonts[0]`` at the page's pixel
            height would put the same row from the x of its first pen: the
            cumulative baked advances (that script's control shows ImGui's
            quads are there).
    hit     ``hit_test`` of the view's code document (``native_documents``,
            the second), at the centre of a drawn glyph's quad on its row: the
            character it answers, less the one answered at the row's first
            glyph. Where that is not the glyph's own index, a press on a glyph
            lands on another character.

## Running it

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/measure_typeset_diff.py

Exit status 1 when the library is not drawing; 2 when a page could not be read.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PAIR = ROOT / ".deps" / "imtypeset"
ASSETS = ROOT / "pptmstr" / "assets"
CHROME_PX = 16.0
SIZES = (14.0, 16.0, 18.0, 20.0)
RULER = "1234567890" * 4
LONG = "total = float(baked.get_char_advance(code)) + kern_px  # 40th is in 'kern'"
CODE_ROWS = (
    "name       size  kind",
    "alpha         1  file",
    "beta_long    42  dir",
    "iiii|llll|mmmm|WWWW|",
    "0000|1111|8888|....|",
    RULER,
    LONG,
)
"""
The rows of ``scripts/measure_typeset_advances.py``'s fenced block, copied: that
script is another task's to edit, and the two answers are compared by row.
"""

WIDTH_PX = 1100.0
SETTLE_FRAMES = 4
MARKS = (10, 20, 40, 74)
HEADER_ROWS = ("file", "gap", "hunk", "fold", "raw")


class Unread(Exception):
    """
    The view's quads could not be paired with its characters; the message says how.
    """


def uptime() -> str:
    try:
        return subprocess.run(
            ["uptime"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError as exc:
        return f"uptime not run: {exc}"


def glyph_quads(draw_list: Any, start: int, stop: int, white: Any) -> list[tuple[float, ...]]:
    """
    The textured quads among vertices ``[start, stop)``, each as
    (left, top, right, bottom, u0, v0, u1, v1).
    """
    buffer = draw_list.vtx_buffer
    textured = []
    for i in range(start, stop):
        vertex = buffer[i]
        if abs(vertex.uv.x - white.x) > 1e-9 or abs(vertex.uv.y - white.y) > 1e-9:
            textured.append((vertex.pos.x, vertex.pos.y, vertex.uv.x, vertex.uv.y))
    if len(textured) % 4:
        raise Unread(f"{len(textured)} textured vertices is not a whole number of quads")
    out: list[tuple[float, ...]] = []
    for at in range(0, len(textured), 4):
        xs, ys, us, vs = zip(*textured[at : at + 4], strict=True)
        out.append((min(xs), min(ys), max(xs), max(ys), min(us), min(vs), max(us), max(vs)))
    return out


def chrome_pens(font: Any, px: float, x: float, text: str) -> list[float]:
    """
    Each character's pen through ``ImDrawList.add_text`` in ``font`` at ``px``
    from ``x``: the cumulative baked advances, which the other script's control
    shows is where ImGui puts them.
    """
    baked = font.get_font_baked(px)
    pens, at = [], x
    for char in text:
        pens.append(at)
        at += float(baked.get_char_advance(ord(char)))
    return pens


def _tally(values: list[float]) -> str:
    counts = Counter(round(value, 3) for value in values)
    return ", ".join(f"{value:g} x{count}" for value, count in sorted(counts.items()))


def code_pens(
    view: Any, quads: list[tuple[float, ...]], font: Any, px: float
) -> tuple[dict[str, list[tuple[int, float, tuple[float, ...]]]], float]:
    """
    Each code row's inked characters as ``(index, pen, quad)``, by the row's
    text, and the baked size the quads were found at.
    """
    origin = view.origin
    if origin is None:
        raise Unread("the diff view has no origin: it was not drawn")
    pitch = float(view.grid.row)
    by_row: dict[int, list[tuple[float, ...]]] = {}
    for quad in quads:
        by_row.setdefault(int(((quad[1] + quad[3]) / 2.0 - origin[1]) // pitch), []).append(quad)
    rows = [row for row in range(view.row_count) if view.row_info(row).kind not in HEADER_ROWS]
    if len(rows) != len(CODE_ROWS):
        raise Unread(f"{len(rows)} rows of code for {len(CODE_ROWS)} lines")
    for size in (px, px - 1.0, px + 1.0, px - 2.0, px + 2.0):
        baked = font.get_font_baked(float(size))
        out: dict[str, list[tuple[int, float, tuple[float, ...]]]] = {}
        whole = True
        for row, text in zip(rows, CODE_ROWS, strict=True):
            inked = [(index, char) for index, char in enumerate(text) if not char.isspace()]
            took = sorted(by_row.get(row, []))[-len(inked) :]
            if len(took) != len(inked):
                raise Unread(f"row {row}: {len(took)} quads for {len(inked)} characters with ink")
            placed = []
            for (index, char), quad in zip(inked, took, strict=True):
                if not baked.is_glyph_loaded(ord(char)):
                    whole = False
                    break
                glyph = baked.find_glyph(ord(char))
                uv = (glyph.u0, glyph.v0, glyph.u1, glyph.v1)
                if any(abs(a - b) > 1e-7 for a, b in zip(uv, quad[4:], strict=True)):
                    whole = False
                    break
                placed.append((index, quad[0] - float(glyph.x0), quad))
            if not whole:
                break
            out[text] = placed
        if whole:
            return out, float(size)
    raise Unread("no baked size of the library's code entry accounts for every quad of the rows")


def main() -> int:
    os.environ.setdefault("IMTYPESET_LIBRARY", str(PAIR / "libimtypeset_bundle.so"))
    os.environ.setdefault("IMTYPESET_PYTHON", str(PAIR / "pysnap"))

    from advtext import (
        DARK_PALETTE,
        Align,
        Diff,
        Face,
        Faces,
        Theme,
        TypesetDiffView,
        TypesetUnavailable,
        register_font,
        typesetter,
    )

    ts = typesetter.module()
    if ts is None:
        print(f"imtypeset is not drawing: {typesetter.reason()}", file=sys.stderr)
        return 1

    from advtext.description import FACE_ROLES
    from imgui_bundle import hello_imgui, imgui, immapp

    before = uptime()
    state: dict[str, Any] = {"page": 0, "shown": 0, "view": None}
    report: list[str] = []
    failed: list[str] = []
    model = Diff.from_texts("", "\n".join(CODE_ROWS) + "\n", "probe.txt")

    def load_fonts() -> None:
        hello_imgui.add_assets_search_path(str(ASSETS))
        hello_imgui.get_runner_params().callbacks.default_icon_font = (
            hello_imgui.DefaultIconFont.font_awesome6
        )
        state["chrome"] = hello_imgui.load_font_ttf_with_font_awesome_icons(
            "fonts/Inconsolata-Medium.ttf", CHROME_PX
        )
        register_font(Face.CODE_BOLD, ASSETS / "fonts" / "Inconsolata-Bold.ttf")
        typesetter.load_fonts()

    def gui() -> None:
        if failed or state["page"] >= len(SIZES):
            hello_imgui.get_runner_params().app_shall_exit = True
            return
        px = SIZES[state["page"]]
        io = imgui.get_io()
        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(io.display_size)
        flags = imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_saved_settings.value
        imgui.begin("measure", None, flags)
        try:
            if not typesetter.ready():
                failed.append(typesetter.reason() or typesetter.trouble() or "fonts not loaded")
                return
            if state["view"] is None:
                state["view"] = TypesetDiffView(
                    model,
                    Theme.at_pixel_height(px, faces=Faces.mono(), align=Align.LEFT),
                    pixel_height=px,
                    palette=DARK_PALETTE,
                )
                state["shown"] = 0
            view = state["view"]
            draw_list = imgui.get_window_draw_list()
            white = imgui.get_font_tex_uv_white_pixel()
            start = draw_list.vtx_buffer.size()
            view.draw(palette=DARK_PALETTE, width_px=WIDTH_PX)
            stop = draw_list.vtx_buffer.size()
            state["shown"] += 1
            if state["shown"] < SETTLE_FRAMES:
                return
            fonts = io.fonts.fonts
            own = fonts[ts.font_atlas_index(FACE_ROLES["CODE"])]
            pens, baked_px = code_pens(view, glyph_quads(draw_list, start, stop, white), own, px)
            cell = float(view.grid.cell)
            pitch = float(view.grid.row)
            baked = float(state["chrome"].get_font_baked(px).get_char_advance(ord("0")))
            report.append(
                f"mono {px:g} px: grid.cell {cell:.3f}, grid.row {pitch:g}; rows drawn from the "
                f"library's CODE entry baked at {baked_px:g} px; the chrome font's advance at "
                f"{px:g} px is {baked:g}"
            )
            steps = [
                b[1] - a[1] for row in pens.values() for a, b in pairwise(row) if b[0] == a[0] + 1
            ]
            columns: dict[int, list[float]] = {}
            for row in pens.values():
                for index, pen, _ in row:
                    columns.setdefault(index, []).append(pen)
            drift = {column: max(at) - min(at) for column, at in columns.items()}
            where = max(drift, key=lambda column: drift[column])
            firsts = {round(row[0][1], 3) for row in pens.values() if row[0][0] == 0}
            report.append(f"  steps between adjacent columns, px: {_tally(steps)}")
            report.append(
                f"  largest disagreement between rows at one column: {drift[where]:g} px "
                f"(column {where + 1});  rows start at x {sorted(firsts)}"
            )
            code = view.native_documents[1]
            for text in (RULER, LONG):
                row = pens[text]
                at = {index: pen for index, pen, _ in row}
                first = row[0][1]
                native = chrome_pens(state["chrome"], px, first, text)
                marks = [k for k in MARKS if k <= len(text)]
                report.append(
                    f"  the {len(text)}-character row: first pen to last pen "
                    f"{row[-1][1] - first:g} px drawn, "
                    f"{(len(text) - 1) * cell:.2f} at the grid's cell, "
                    f"{native[-1] - native[0]:g} in the chrome font"
                )
                report.append(
                    f"     typeset pen - chrome pen at characters "
                    f"{', '.join(str(k) for k in marks)}: "
                    f"{'  '.join(f'{at[k - 1] - native[k - 1]:+g}' for k in marks)} px"
                )
                off = [pen - (first + index * cell) for index, pen, _ in row]
                report.append(
                    f"     a glyph's pen less its cell (first pen + index x cell), px: min "
                    f"{min(off):+.2f} max {max(off):+.2f}"
                )
                # The code document is drawn with its left edge at the row's
                # first pen when the first character is at column 0.
                middle = (row[0][2][1] + row[0][2][3]) / 2.0 - view.origin[1]
                base = int(code.hit_test((0.5, middle)).position[1])
                wrong = []
                for index, _, quad in row:
                    x = (quad[0] + quad[2]) / 2.0 - first
                    hit = int(code.hit_test((x, middle)).position[1]) - base
                    if hit != index:
                        wrong.append((index, hit))
                worst = max(wrong, key=lambda pair: abs(pair[1] - pair[0]), default=None)
                report.append(
                    f"     hit_test at the centre of each drawn glyph: {len(wrong)} of {len(row)} "
                    f"answer another character"
                    + (
                        f"; worst, glyph {worst[0] + 1} answers character {worst[1] + 1}"
                        if worst
                        else ""
                    )
                )
            report.append("")
            view.close()
            state["view"] = None
            state["page"] += 1
        except (Unread, TypesetUnavailable) as exc:
            failed.append(f"mono {px:g} px: {exc}")
        finally:
            imgui.end()

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "pptmstr: typeset diff rows"
    params.app_window_params.window_geometry.size = (1500, 950)
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.ini_disable = True
    params.callbacks.load_additional_fonts = load_fonts
    params.callbacks.show_gui = gui
    params.callbacks.before_exit = typesetter.unload
    immapp.run(params)

    print("measure_typeset_diff: positions and counts from the draw list; no pixel was read")
    print(f"  uptime before: {before}")
    print(f"  uptime after:  {uptime()}")
    print(f"  IMTYPESET_LIBRARY={os.environ['IMTYPESET_LIBRARY']}")
    print(f"  view: TypesetDiffView, unified, {WIDTH_PX:g} px wide, every row added")
    print()
    print("\n".join(report))
    if failed:
        for reason in failed:
            print(f"NOT READ: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
