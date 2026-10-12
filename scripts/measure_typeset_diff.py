#!/usr/bin/env python3
"""
Where imscribe's ``TypesetDiffView`` puts the glyphs of a diff's rows, as numbers.

The question is the one ``scripts/measure_typeset_advances.py`` asks of a fenced
block (R4 and R5 of ``HANDOFF-advtext.md``, requests P21 and P22), asked of the
view an approval's diff would be drawn with: do N characters of a row occupy N
times one whole-pixel constant on every row, is that constant ImGui's baked
advance, and does a point on a drawn glyph answer that glyph's character. It
measures and decides nothing, and says nothing about how anything looks.

## The window

That script's: the chrome's Inconsolata as ``Fonts[0]`` through hello_imgui,
then the library's fonts through pptmstr's seam (``typeset.load_fonts``), as
the application loads them. One page a size: a pixel height (``--px``, default
14, 16, 18, 20) or an em in pixels (``--em``; Inconsolata's advance is half an
em, so an even em is a whole-pixel column). A page is one diff view of one
file whose every row is added -- that script's seven code rows, the
40-character ruler and the 74-character line among them -- unified, 1100 px
wide.

**A pixel-height page is the view the panes draw**: the seam is told a text
scale and a step for ``Role.DIFF`` that come to that height, and the view is
``gate.diff_view`` of ``themes.diff_theme()``, which is what ``diffs.diff``
makes. ``--cell font`` makes the same view on the library's default cell, for
the figures the option is passed to avoid; the default is the product's
(``gate.DIFF_CELL``). An ``--em`` page is a mono theme at that em, which no
setting of the product reaches.

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
            height -- the size asked for, which is not always the size the
            library's glyphs were rasterised at -- would put the same row from
            the x of its first pen: the cumulative baked advances (that
            script's control shows ImGui's quads are there).
    hit     ``hit_test`` of the view's code document (``native_documents``,
            the second) on its row. The library answers the nearest position
            *between* characters, so two points are asked for each drawn
            glyph, a quarter and three quarters of the baked advance from its
            pen: the first must answer the position before the character and
            the second the one after (past a row's last character the
            library answers the start of the next line, which the view reads
            as the end of this one, and so does this). Where either does not,
            a press on that glyph lands on another character. The count at the centre of each
            quad is printed beside it and is not a verdict: a quad's centre is
            left or right of its cell's by the glyph's bearing, so it answers
            either neighbour on a row whose columns are exact.
    seam    ``pptmstr.ui.typeset.gate.columns`` for the page's theme: the
            answer the panes switch on, from one row set with no window. The
            two are printed side by side; this script decides nothing.
    cost    what the cell costs in glyph size, from a second view of the same
            configuration whose rows are every printable ASCII character and
            U+2502: the em asked for and the em drawn (``grid.em *
            grid.scale``), the pixel height the glyphs were rasterised at, the
            bitmap of ``0`` and of ``m`` there beside the chrome font's at the
            pixel height asked for, the row pitch beside the pitch of the
            panes' own diff rows (the chrome font's line and pptmstr's item
            spacing), and how far any quad of a row reaches past the row's top
            or bottom.

## Running it

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/measure_typeset_diff.py
    scripts/xvfb_shot.sh -- .venv/bin/python scripts/measure_typeset_diff.py --em 14,16,18,20

Exit status 1 when the library is not drawing; 2 when a page could not be read.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PAIR = ROOT / ".deps" / "imtypeset"
ASSETS = ROOT / "pptmstr" / "assets"
CHROME_PX = 16.0
SIZES = (14.0, 16.0, 18.0, 20.0)
ASCII_ROWS = (
    "".join(chr(code) for code in range(0x21, 0x50)),
    "".join(chr(code) for code in range(0x50, 0x7F)),
)
BOX_ROW = "\u2502 \u2502\u2502"
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
    whole_px = float(int(px + 0.5))
    for size in (whole_px, whole_px - 1.0, whole_px + 1.0, whole_px - 2.0, whole_px + 2.0):
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


def product_theme(px: float) -> Any:
    """
    The diff theme of the product with ``Role.DIFF`` at ``px``: a text scale
    and a role step that come to it, as the settings dialog would set them.
    """
    from pptmstr.ui import typeset
    from pptmstr.ui.typeset import roles, themes

    body = min(max(px, CHROME_PX * roles.SCALES[0]), CHROME_PX * roles.SCALES[-1])
    step = roles.RoleStyle(roles.Slot.MONOSPACE, int(px - body))
    typeset.configure(typeset=True, scale=body / CHROME_PX, roles={roles.Role.DIFF: step})
    if roles.px(roles.Role.DIFF) != px:
        raise Unread(f"no setting of the product puts a diff at {px:g} px")
    return themes.diff_theme()


def overhang(view: Any, quads: list[tuple[float, ...]], rows: tuple[str, ...]) -> list[str]:
    """
    How far the quads of each of ``rows`` reach past their row, as lines of
    the report: the row's top is the view's origin and whole pitches down.
    """
    origin = view.origin
    if origin is None:
        raise Unread("the cost view has no origin: it was not drawn")
    pitch = float(view.grid.row)
    by_row: dict[int, list[tuple[float, ...]]] = {}
    for quad in quads:
        by_row.setdefault(int(((quad[1] + quad[3]) / 2.0 - origin[1]) // pitch), []).append(quad)
    code = [row for row in range(view.row_count) if view.row_info(row).kind not in HEADER_ROWS]
    if len(code) != len(rows):
        raise Unread(f"{len(code)} rows of code for {len(rows)} lines of the cost view")
    out = []
    for row, text in zip(code, rows, strict=True):
        inked = [char for char in text if not char.isspace()]
        took = sorted(by_row.get(row, []))[-len(inked) :]
        if len(took) != len(inked):
            raise Unread(f"cost row {row}: {len(took)} quads for {len(inked)} characters")
        top = origin[1] + row * pitch
        above = max(((top - quad[1], char) for char, quad in zip(inked, took, strict=True)))
        below = max(
            ((quad[3] - (top + pitch), char) for char, quad in zip(inked, took, strict=True))
        )
        name = "U+2502" if text == BOX_ROW else f"ASCII {text[0]} to {text[-1]}"
        out.append(
            f"     {name}: reaches {max(above[0], 0.0):g} px above its row"
            + (f" ({above[1]!r})" if above[0] > 0 else "")
            + f" and {max(below[0], 0.0):g} px below"
            + (f" ({below[1]!r})" if below[0] > 0 else "")
        )
    return out


def _lengths(text: str) -> tuple[float, ...]:
    return tuple(float(each) for each in text.split(",") if each.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--px", type=_lengths, default=None, help="pixel heights, comma separated")
    ap.add_argument("--em", type=_lengths, default=(), help="em sizes in pixels, comma separated")
    ap.add_argument(
        "--cell",
        choices=("pixel", "font"),
        default=None,
        help="the cell the view is made on; default: the product's (gate.DIFF_CELL)",
    )
    args = ap.parse_args()
    if args.px is None:
        args.px = () if args.em else SIZES
    os.environ.setdefault("IMTYPESET_LIBRARY", str(PAIR / "libimtypeset_bundle.so"))
    os.environ.setdefault("IMTYPESET_PYTHON", str(PAIR / "pysnap"))

    from imscribe import (
        DARK_PALETTE,
        Align,
        Diff,
        Face,
        Faces,
        Theme,
        TypesetUnavailable,
        em_size_for_pixel_height,
        typesetter,
    )

    ts = typesetter.module()
    if ts is None:
        print(f"imtypeset is not drawing: {typesetter.reason()}", file=sys.stderr)
        return 1

    from imgui_bundle import hello_imgui, imgui, immapp
    from imscribe.description import FACE_ROLES

    from pptmstr import theme as ui_theme
    from pptmstr.ui import typeset
    from pptmstr.ui.typeset import gate

    if args.cell is not None:
        # The one name every diff view of pptmstr's is made on: the view here,
        # and the one the seam's own measurement reads.
        gate.DIFF_CELL = args.cell
    per_em = em_size_for_pixel_height(Face.CODE, 1.0)
    pages = [(f"em {em:g} px", em / per_em, False) for em in args.em]
    pages += [(f"{px:g} px", px, True) for px in args.px]
    before = uptime()
    state: dict[str, Any] = {"page": 0, "shown": 0, "view": None}
    report: list[str] = []
    failed: list[str] = []
    model = Diff.from_texts("", "\n".join(CODE_ROWS) + "\n", "probe.txt")
    cost_rows = (*ASCII_ROWS, BOX_ROW)
    cost_model = Diff.from_texts("", "\n".join(cost_rows) + "\n", "cost.txt")

    def load_fonts() -> None:
        hello_imgui.add_assets_search_path(str(ASSETS))
        hello_imgui.get_runner_params().callbacks.default_icon_font = (
            hello_imgui.DefaultIconFont.font_awesome6
        )
        state["chrome"] = hello_imgui.load_font_ttf_with_font_awesome_icons(
            "fonts/Inconsolata-Medium.ttf", CHROME_PX
        )
        typeset.load_fonts(ASSETS / "fonts" / "Inconsolata-Bold.ttf")

    def gui() -> None:
        if failed or state["page"] >= len(pages):
            hello_imgui.get_runner_params().app_shall_exit = True
            return
        name, px, product = pages[state["page"]]
        io = imgui.get_io()
        if not state.get("styled"):
            # The panes' own rows are pitched by pptmstr's item spacing.
            ui_theme.apply_style()
            state["styled"] = True
            state["own_pitch"] = float(imgui.get_text_line_height_with_spacing())
        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(io.display_size)
        flags = imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_saved_settings.value
        imgui.begin("measure", None, flags)
        try:
            if not typesetter.ready():
                failed.append(typesetter.reason() or typesetter.trouble() or "fonts not loaded")
                return
            if state["view"] is None:
                if product:
                    state["theme"] = product_theme(px)
                    sized: dict[str, Any] = {}
                else:
                    state["theme"] = Theme.at_pixel_height(px, faces=Faces.mono(), align=Align.LEFT)
                    sized = {"pixel_height": px}
                state["view"] = gate.diff_view(model, state["theme"], **sized)
                state["cost"] = gate.diff_view(cost_model, state["theme"], **sized)
                state["shown"] = 0
            view, cost = state["view"], state["cost"]
            draw_list = imgui.get_window_draw_list()
            white = imgui.get_font_tex_uv_white_pixel()
            start = draw_list.vtx_buffer.size()
            view.draw(palette=DARK_PALETTE, width_px=WIDTH_PX)
            stop = draw_list.vtx_buffer.size()
            cost.draw(palette=DARK_PALETTE, width_px=WIDTH_PX)
            cost_stop = draw_list.vtx_buffer.size()
            state["shown"] += 1
            if state["shown"] < SETTLE_FRAMES:
                return
            fonts = io.fonts.fonts
            own = fonts[ts.font_atlas_index(FACE_ROLES["CODE"])]
            pens, baked_px = code_pens(view, glyph_quads(draw_list, start, stop, white), own, px)
            cell = float(view.grid.cell)
            pitch = float(view.grid.row)
            asked_px = float(int(px + 0.5))
            baked = float(state["chrome"].get_font_baked(asked_px).get_char_advance(ord("0")))
            raster = float(own.get_font_baked(baked_px).get_char_advance(ord("0")))
            report.append(
                f"mono {name} (pixel height {px:.3f}, cell {gate.DIFF_CELL!r}"
                f"{', the diff theme of the product' if product else ''}): grid.cell {cell:.6f}, "
                f"grid.row {pitch:g}; rows drawn from the library's CODE entry baked at "
                f"{baked_px:g} px, where its advance is {raster:g}; the chrome font's advance "
                f"at {asked_px:g} px is {baked:g}"
            )
            seam = gate.columns(state["theme"])
            report.append(
                f"  the seam's answer for this theme: holds {seam.holds} (cell {seam.cell:.6f}, "
                f"baked {seam.baked:g} at {seam.size:g} px, {seam.off} of {seam.glyphs} glyphs "
                f"off their column, {seam.mishit} mishit): {seam.why}"
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
                native = chrome_pens(state["chrome"], asked_px, first, text)
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
                centred = 0
                for index, pen, quad in row:
                    x = (quad[0] + quad[2]) / 2.0 - first
                    centred += int(code.hit_test((x, middle)).position[1]) - base != index
                    left = int(code.hit_test((pen - first + 0.25 * baked, middle)).position[1])
                    right = int(code.hit_test((pen - first + 0.75 * baked, middle)).position[1])
                    if index == len(text) - 1:
                        # A point at a line's end answers the start of the next
                        # line, which the view reads as the end of this one.
                        right = min(right, base + index + 1)
                    if (left - base, right - base) != (index, index + 1):
                        wrong.append((index, left - base, right - base))
                worst = max(
                    wrong,
                    key=lambda found: abs(found[1] - found[0]) + abs(found[2] - found[0] - 1),
                    default=None,
                )
                report.append(
                    f"     hit_test a quarter and three quarters into each drawn glyph: "
                    f"{len(wrong)} of {len(row)} land on another character"
                    + (
                        f"; worst, character {worst[0]} (from 0) answers positions "
                        f"{worst[1]} and {worst[2]} for {worst[0]} and {worst[0] + 1}"
                        if worst
                        else ""
                    )
                    + f"  (at the centre of each quad, {centred} answer the position after: "
                    f"not a verdict)"
                )
            grid = view.grid
            drawn_em = float(grid.em) * float(grid.scale)
            asked_em = px * per_em
            report.append(
                f"  cost: em asked {asked_em:.3f} px, em drawn {drawn_em:.3f} "
                f"({(drawn_em / asked_em - 1.0) * 100.0:+.1f} %); glyphs rasterised at "
                f"{baked_px:g} px for {asked_px:g} asked; row pitch {pitch:g} px, the panes' own "
                f"diff rows {state['own_pitch']:g} px (chrome font at {CHROME_PX:g} px)"
            )
            theirs = state["chrome"].get_font_baked(asked_px)
            ours = own.get_font_baked(baked_px)
            for char in "0m":
                a, b = ours.find_glyph(ord(char)), theirs.find_glyph(ord(char))
                report.append(
                    f"     bitmap of {char!r}: {a.x1 - a.x0:g} x {a.y1 - a.y0:g} px drawn, "
                    f"{b.x1 - b.x0:g} x {b.y1 - b.y0:g} in the chrome font at {asked_px:g} px"
                )
            report.extend(overhang(cost, glyph_quads(draw_list, stop, cost_stop, white), cost_rows))
            report.append("")
            view.close()
            cost.close()
            state["view"] = state["cost"] = None
            state["page"] += 1
        except (Unread, TypesetUnavailable) as exc:
            failed.append(f"mono {name}: {exc}")
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
    params.callbacks.before_exit = typeset.unload
    immapp.run(params)

    print("measure_typeset_diff: positions and counts from the draw list; no pixel was read")
    print(f"  uptime before: {before}")
    print(f"  uptime after:  {uptime()}")
    print(f"  IMTYPESET_LIBRARY={os.environ['IMTYPESET_LIBRARY']}")
    print(f"  view: gate.diff_view, unified, {WIDTH_PX:g} px wide, every row added")
    print()
    print("\n".join(report))
    if failed:
        for reason in failed:
            print(f"NOT READ: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
