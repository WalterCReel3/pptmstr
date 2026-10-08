#!/usr/bin/env python3
"""
The same text set three ways in one window, differing only in whose advances lay it out.

``pptmstr/ui/typeset.py`` does not measure text with ``advtext``'s own font-file
metrics (``advtext.fontlib_metrics``). It hands the library a resolver,
``pixel_metrics``, that asks ImGui's baked font for each advance. This script puts
the two side by side so a person can judge that choice, and prints what was drawn
as numbers. It argues for neither: the labels in the window say what each column
is and nothing else.

    A   advtext's ``draw``, document measured with ``advtext.fontlib_metrics``
        (the pipeline of advtext's ``examples/minimal.py``: build, compose, ship, draw)
    B   the same four calls, document measured with ``typeset.pixel_metrics``
    C   ``imgui.text_wrapped`` / ``text_unformatted`` in the same ``ImFont`` at the
        same baked size, for what the chrome around a pane looks like

A and B share one ``Theme``, one palette, one ink, one width and one atlas; the
resolver is the only argument that differs. The theme is
``Theme.at_pixel_height(size, align=LEFT, spacing=FRENCH)``: ragged right and one
space width, so that a word gap that varies does so because of rounding and not
because a line was justified or a full stop widened its space. It is the
library's theme otherwise, so in the Roboto pages fenced code is Inconsolata at
the paragraph's em and is baked a size or two under the page's (the page header
says which).

**This is the second module that imports ``advtext``**, with
``scripts/survey_fonts.py``; every pane goes through ``pptmstr.ui.typeset``. It also
reaches into the seam for two things it has no public name for (``typeset._fonts``)
so that A and B draw from the very ``ImFont``s ``pixel_metrics`` reads.

## What is measured, and from what

Nothing is measured from pixels. ``advtext.draw`` is handed a draw list that
records every ``add_text`` it is sent -- font, size, position, the list's flags at
that moment -- and passes it on to ImGui unchanged, and on the frame that is
grabbed it also reads the quad ImGui emitted for the glyph back out of the vertex
buffer. The frame measured is the frame in the PNG.

Three yardsticks appear and they are kept apart:

    file     the advance (and pair kern) in the font file, scaled: ``fontlib_metrics``
    hinted   ``ImFontBaked.get_char_advance``: what ImGui's loader says the glyph it
             rasterised advances by
    ink      ``ImFontBaked.find_glyph``'s x0/x1: the bitmap's own box. A and B draw
             the same bitmaps, so an ink gap is the one figure with no opinion in it

## Running it

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/compare_metrics.py
    .venv/bin/python scripts/compare_metrics.py --out shots/metrics/display --frames 0
    .venv/bin/python scripts/compare_metrics.py --diff shots/metrics shots/metrics/display

    .venv/bin/python scripts/compare_metrics.py --hinting

The first writes, under ``shots/metrics``, one PNG a page (a face at a size), a
4x nearest-neighbour zoom of each (every source pixel becomes a 4x4 block; no
filtering), and ``gaps.txt``. The second does the same on whatever display is
there and then stays open, with the pages on the arrow keys. The third compares
two such directories PNG by PNG, which is how to find out whether the software
rasteriser's pixels are the GPU's. The fourth needs no display: it prints where
ImGui's own ``add_text`` puts the glyphs of a word when the face is loaded with
the loader's default flags and with hinting off, which is what says whether a
whole-pixel advance is the loader's doing or ImGui's.
"""

from __future__ import annotations

import argparse
import math
import statistics
import struct
import sys
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

COLUMN_PX = 400.0
"""
The width A and B are broken to and C wraps at: about the DETAIL dock's.
"""

GUTTER_PX = 24.0
MARGIN_PX = 16.0
WINDOW = (int(3 * COLUMN_PX + 2 * GUTTER_PX + 2 * MARGIN_PX), 940)
ZOOM = 4
SETTLE_FRAMES = 8
"""
Frames a page is shown before it is measured and grabbed: the atlas bakes a size
on the first frame that draws with it.
"""

PAGES: tuple[tuple[str, float], ...] = (
    ("roboto", 14.0),
    ("roboto", 16.0),
    ("roboto", 18.0),
    ("roboto", 20.0),
    ("mono", 14.0),
    ("mono", 15.0),
    ("mono", 16.0),
    ("mono", 17.0),
    ("mono", 18.0),
    ("mono", 20.0),
)
"""
A face table and a pixel height: Roboto prose with Inconsolata code, or Inconsolata
in every role. 15 and 17 are there for the monospace face alone.
"""

PROSE = (
    "To Wave at AVATAR Wavy Types we fix five affine offices: a Tyrant Yawns, "
    "Vows Await, and LT. P. Avery fills the final waffle. Typography of ordinary "
    "running text depends on the rhythm of its word spaces, which a reader takes "
    "in without ever looking at one of them directly."
)
RHYTHM = "iiii llll mmmm WWWW"
DIGITS = "0123456789 1111 0000 8888 1,234,567.89 (2026-10-08)"
INLINE = (
    "Run `make test` and then call `typeset.pixel_metrics(key)` before `ship()` "
    "returns, with `--frames 0` to keep the window."
)
CODE_ROWS = (
    "name       size  kind",
    "alpha         1  file",
    "beta_long    42  dir",
    "iiii|llll|mmmm|WWWW|",
    "0000|1111|8888|....|",
)
MARKDOWN = "\n\n".join((PROSE, RHYTHM, DIGITS, INLINE, "```\n" + "\n".join(CODE_ROWS) + "\n```"))
CODE_KEYS = frozenset(row.strip() for row in CODE_ROWS)


@dataclass(frozen=True, slots=True)
class Sent:
    """
    One ``add_text`` as ``advtext.draw`` issued it, and what ImGui made of it.

    ``quad`` is the emitted quad's ``(left, top, right, bottom)`` from the vertex
    buffer, or ``None`` for a glyph with no ink (a space) and on a frame that is
    not being measured.
    """

    font: Any
    size: float
    x: float
    y: float
    text: str
    snap_off: bool
    quad: tuple[float, float, float, float] | None


class Recorder:
    """
    A draw list that records each ``add_text`` and hands everything on unchanged.

    ``advtext.draw`` reads and writes ``flags`` and calls ``add_text`` and
    ``add_rect_filled``; anything else goes straight to the real list.
    """

    def __init__(self, real: Any, no_snap: int, *, quads: bool) -> None:
        self._real = real
        self._no_snap = no_snap
        self._quads = quads
        self.sent: list[Sent] = []

    @property
    def flags(self) -> int:
        return int(self._real.flags)

    @flags.setter
    def flags(self, value: int) -> None:
        self._real.flags = value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def add_text(self, *args: Any) -> None:
        real = self._real
        start = len(real.vtx_buffer) if self._quads else 0
        real.add_text(*args)
        if len(args) == 5:
            font, size, position, _, text = args
        else:
            font, size = None, 0.0
            position, _, text = args
        quad = None
        if self._quads:
            buffer = real.vtx_buffer
            corners = [buffer[i].pos for i in range(start, len(buffer))]
            if corners:
                xs, ys = [c.x for c in corners], [c.y for c in corners]
                quad = (min(xs), min(ys), max(xs), max(ys))
        self.sent.append(
            Sent(
                font=font,
                size=float(size),
                x=float(position.x),
                y=float(position.y),
                text=text,
                snap_off=bool(int(real.flags) & self._no_snap),
                quad=quad,
            )
        )


@dataclass(frozen=True, slots=True)
class Placed:
    """
    A glyph of the layout paired with the ``add_text`` that drew it.

    ``laid`` is the layout's x in pixels (document space, exact); ``pen`` is where
    the draw put it on screen. ``hinted``, ``left`` and ``right`` are ImGui's baked
    advance and bitmap box for the glyph; ``font_key`` is the layout's.
    """

    char: str
    font_key: str
    laid: float
    pen: float
    hinted: float
    left: float
    right: float
    sent: Sent


@dataclass(slots=True)
class SetLine:
    """
    One line as shipped: glyphs, with each run of glue as its set width in pixels.
    """

    items: list[Placed | float] = field(default_factory=list)

    @property
    def glyphs(self) -> list[Placed]:
        return [item for item in self.items if isinstance(item, Placed)]

    @property
    def text(self) -> str:
        return "".join(i.char if isinstance(i, Placed) else " " for i in self.items).strip()

    def words(self) -> list[list[Placed]]:
        """
        The rigid spans of the line: maximal runs of glyphs with no glue between.
        """
        out: list[list[Placed]] = [[]]
        for item in self.items:
            if isinstance(item, Placed):
                out[-1].append(item)
            elif out[-1]:
                out.append([])
        return [word for word in out if word]

    def spaces(self) -> list[tuple[Placed, Placed, float]]:
        """
        Each glue between two glyphs: the glyph before, the glyph after, the set width.
        """
        out: list[tuple[Placed, Placed, float]] = []
        before: Placed | None = None
        glue = 0.0
        for item in self.items:
            if isinstance(item, Placed):
                if before is not None and glue > 0.0:
                    out.append((before, item, glue))
                before, glue = item, 0.0
            else:
                glue += item
        return out


@dataclass(slots=True)
class Column:
    """
    One of A and B on one page: its commands, its height, and the last frame's draw.
    """

    name: str
    resolver: Any
    commands: tuple[Any, ...]
    height_px: float
    px_per_inch: float
    sent: list[Sent] = field(default_factory=list)


def lay_out(name: str, theme: Any, resolver: Any) -> Column:
    """
    ``examples/minimal.py``'s parse, compose and ship, with ``resolver`` throughout.
    """
    from advtext import Point, build_document, char_measure, compose, px_from_sp, ship, sp_from_px

    ppi = theme.px_per_inch
    measure = char_measure(resolver)
    document = build_document(MARKDOWN, theme, resolver)
    box = compose(document, theme, sp_from_px(COLUMN_PX, ppi), measure)
    commands = tuple(ship(box, Point(0, box.height), measure=measure))
    return Column(name, resolver, commands, px_from_sp(box.height + box.depth, ppi), ppi)


def theme_for(face: str, size: float) -> Any:
    from advtext import Align, Face, Faces, Spacing, Theme

    faces = Faces.mono(bold=Face.CODE) if face == "mono" else Faces()
    return Theme.at_pixel_height(size, faces=faces, align=Align.LEFT, spacing=Spacing.FRENCH)


def set_lines(column: Column) -> tuple[list[SetLine], int]:
    """
    The column's commands as lines of placed glyphs, and how many glyphs were not drawn.

    A glyph the face has no raster for is sent no ``add_text``, so the commands and
    the record are walked together and matched on the character.
    """
    from advtext import px_from_sp
    from advtext.shipout import Glyph, Line, SetGlue

    ppi = column.px_per_inch
    lines: list[SetLine] = []
    current: SetLine | None = None
    sent, at, missing = column.sent, 0, 0
    for cmd in column.commands:
        if isinstance(cmd, Line) or current is None:
            current = SetLine()
            lines.append(current)
        if isinstance(cmd, SetGlue):
            current.items.append(px_from_sp(cmd.width, ppi))
        elif isinstance(cmd, Glyph):
            char = chr(cmd.codepoint)
            if at >= len(sent) or sent[at].text != char or sent[at].font is None:
                missing += 1
                continue
            record = sent[at]
            at += 1
            baked = record.font.get_font_baked(record.size)
            glyph = baked.find_glyph(cmd.codepoint)
            current.items.append(
                Placed(
                    char=char,
                    font_key=cmd.font,
                    laid=px_from_sp(cmd.x, ppi),
                    pen=record.x,
                    hinted=float(baked.get_char_advance(cmd.codepoint)),
                    left=float(glyph.x0),
                    right=float(glyph.x1),
                    sent=record,
                )
            )
    return [line for line in lines if line.glyphs], missing


def _file_step(this: Placed, following: Placed | None) -> float:
    """
    The font file's advance for a glyph, with its kern against the next, in pixels.
    """
    from advtext import fontlib_metrics, px_from_sp

    metrics = fontlib_metrics(this.font_key)
    step = metrics.advance(ord(this.char))
    if following is not None and following.font_key == this.font_key:
        step += metrics.kern(ord(this.char), ord(following.char))
    return float(px_from_sp(step))


def _fmt(values: list[float]) -> str:
    return " ".join(f"{value:g}" for value in (round(v, 2) for v in values))


def _spread(values: list[float]) -> str:
    if not values:
        return "n=0"
    deviation = statistics.pstdev(values) if len(values) > 1 else 0.0
    return (
        f"n={len(values)} min={min(values):.2f} max={max(values):.2f} "
        f"mean={statistics.fmean(values):.2f} sd={deviation:.2f}"
    )


def _gap_rows(spaces: list[tuple[Placed, Placed, float]]) -> dict[str, list[float]]:
    """
    Every space of a line four ways. See the module docstring for the yardsticks.
    """
    return {
        "laid out (glue as set)": [glue for _, _, glue in spaces],
        "drawn, by hinted adv": [b.pen - a.pen - a.hinted for a, b, _ in spaces],
        "drawn, by file adv": [b.pen - a.pen - _file_step(a, None) for a, b, _ in spaces],
        "drawn, ink to ink": [(b.pen + b.left) - (a.pen + a.right) for a, b, _ in spaces],
    }


def _native_width(imgui: Any, glyphs: list[Placed], text: str) -> float | None:
    """
    ``imgui.calc_text_size`` of a line in the line's own font, or ``None`` if it mixes two.
    """
    first = glyphs[0].sent
    if any(g.sent.font is not first.font or g.sent.size != first.size for g in glyphs):
        return None
    imgui.push_font(first.font, first.size)
    try:
        return float(imgui.calc_text_size(text).x)
    finally:
        imgui.pop_font()


def measure(column: Column, imgui: Any) -> tuple[list[str], dict[str, str]]:
    """
    The column's numbers as report lines, and the few of them the summary table carries.
    """
    lines, missing = set_lines(column)
    out: list[str] = []
    brief: dict[str, str] = {}
    glyphs = [g for line in lines for g in line.glyphs]

    # Positioning: what advtext's draw sent, and what ImGui emitted for it.
    flagged = sum(1 for g in glyphs if g.sent.snap_off)
    pens_x = sum(1 for g in glyphs if g.sent.x != math.floor(g.sent.x))
    pens_y = sum(1 for g in glyphs if g.sent.y != math.floor(g.sent.y))
    quads = [(g, g.sent.quad) for g in glyphs if g.sent.quad is not None]
    off_grid = sum(1 for _, q in quads if any(v != math.floor(v) for v in q))
    scaled = sum(1 for g, q in quads if abs((q[2] - q[0]) - (g.right - g.left)) > 1e-4)
    moved = sum(1 for g, q in quads if abs(q[0] - (g.sent.x + g.left)) > 1e-4)
    out.append(
        f"  glyphs drawn {len(glyphs)} (not in face: {missing});  add_text issued with "
        f"text_no_pixel_snap set: {flagged} of {len(glyphs)}"
    )
    out.append(f"  pens sent at a fractional x: {pens_x} of {len(glyphs)};  fractional y: {pens_y}")
    out.append(
        f"  quads in the vertex buffer: {len(quads)};  a corner off the pixel grid: {off_grid};  "
        f"width != bitmap width (resampled): {scaled};  left edge != pen + bitmap x0: {moved}"
    )
    brief["flag"] = f"{flagged}/{len(glyphs)}"
    brief["frac"] = f"{pens_x}"
    brief["resampled"] = f"{off_grid + scaled}"

    # Letter to letter, inside every rigid span of the page.
    steps = same = 0
    departures: list[float] = []
    ink: Counter[int] = Counter()
    for line in lines:
        for word in line.words():
            for this, following in zip(word, word[1:], strict=False):
                step = following.pen - this.pen
                steps += 1
                same += step == this.hinted
                departures.append(abs(step - _file_step(this, following)))
                ink[round((following.pen + following.left) - (this.pen + this.right))] += 1
    worst = max(departures, default=0.0)
    mean = statistics.fmean(departures) if departures else 0.0
    out.append(
        f"  letter steps inside spans: {steps};  equal to the hinted advance: {same};  "
        f"|step - file advance+kern| mean {mean:.3f} max {worst:.3f} px"
    )
    histogram = "  ".join(f"{gap}px:{count}" for gap, count in sorted(ink.items()))
    out.append(f"  ink gap between adjacent letters (bitmap box to box), px:count  {histogram}")
    overlap = sum(count for gap, count in ink.items() if gap < 0)
    brief["steps"] = f"{same}/{steps}"
    brief["dfile"] = f"{mean:.2f}/{worst:.2f}"
    brief["touch"] = f"{overlap}/{ink[0]}/{steps}"

    # Word spaces: the first three lines, then the whole first paragraph.
    paragraph: list[SetLine] = []
    for line in lines:
        if line.text.startswith(RHYTHM[:4]):
            break
        paragraph.append(line)
    for number, line in enumerate(paragraph[:3], start=1):
        out.append(f"  line {number}: {line.text!r}")
        rows = _gap_rows(line.spaces())
        for label, values in rows.items():
            out.append(f"    {label:<23} {_fmt(values)}")
            out.append(f"    {'':<23} {_spread(values)}")
        placed = line.glyphs
        drawn = placed[-1].pen + placed[-1].hinted - placed[0].pen
        native = _native_width(imgui, placed, line.text)
        said = "mixed faces" if native is None else f"{native:.2f}"
        out.append(f"    width drawn {drawn:.2f} px;  imgui.calc_text_size {said} px")
    every = [space for line in paragraph for space in line.spaces()]
    rows = _gap_rows(every)
    out.append(f"  first paragraph, all {len(every)} spaces on {len(paragraph)} lines:")
    for label, values in rows.items():
        out.append(f"    {label:<23} {_spread(values)}")
    for key, label in (("hint", "drawn, by hinted adv"), ("ink", "drawn, ink to ink")):
        values = rows[label]
        deviation = statistics.pstdev(values) if len(values) > 1 else 0.0
        brief[key] = f"{min(values):.1f}..{max(values):.1f} sd {deviation:.2f}" if values else "-"

    # The rhythm line, glyph by glyph.
    for line in lines:
        if line.text != RHYTHM:
            continue
        placed = line.glyphs
        first = placed[0]
        space_file = _file_step(_space_like(first), None)
        space_hint = float(first.sent.font.get_font_baked(first.sent.size).get_char_advance(32))
        file_x, hint_x, file_at, hint_at = [], [], 0.0, 0.0
        file_end = hint_end = 0.0
        for index, item in enumerate(line.items):
            if isinstance(item, Placed):
                file_x.append(file_at)
                hint_x.append(hint_at)
                following = line.items[index + 1] if index + 1 < len(line.items) else None
                file_end = file_at + _file_step(item, None)
                hint_end = hint_at + item.hinted
                file_at += _file_step(item, following if isinstance(following, Placed) else None)
                hint_at += item.hinted
            else:
                file_at += space_file
                hint_at += space_hint
        out.append(f"  {line.text!r}: x of each glyph from the first, px")
        out.append(f"    laid out          {_fmt([g.laid - first.laid for g in placed])}")
        out.append(f"    drawn (pen)       {_fmt([g.pen - first.pen for g in placed])}")
        out.append(f"    cum. file adv     {_fmt(file_x)}")
        out.append(f"    cum. hinted adv   {_fmt(hint_x)}")
        drawn = placed[-1].pen + placed[-1].hinted - first.pen
        native = _native_width(imgui, placed, line.text)
        said = "mixed faces" if native is None else f"{native:.2f}"
        out.append(
            f"    width drawn {drawn:.2f} px;  cum. file {file_end:.2f};  "
            f"cum. hinted {hint_end:.2f};  imgui.calc_text_size {said}"
        )
        brief["width"] = f"{drawn:.0f}/{said}"

    # The fenced block: every character's x from the row's first.
    rows_x: list[list[float]] = []
    for line in lines:
        if line.text not in CODE_KEYS:
            continue
        placed = line.glyphs
        xs = [g.pen - placed[0].pen for g in placed]
        rows_x.append(xs)
        baked = placed[0].sent
        out.append(
            f"  code row {line.text!r} (baked {baked.size:g} px, hinted adv "
            f"{placed[0].hinted:g}, file adv {_file_step(placed[0], None):.3f}, "
            f"row starts at screen x {placed[0].pen:g})"
        )
        out.append(f"    column x          {_fmt(xs)}")
    if rows_x:
        pitches = sorted(
            {round(b - a, 3) for xs in rows_x for a, b in zip(xs, xs[1:], strict=False)}
        )
        shortest = min(len(xs) for xs in rows_x)
        drift = max(
            max(xs[i] for xs in rows_x) - min(xs[i] for xs in rows_x) for i in range(shortest)
        )
        out.append(
            f"    distinct steps between columns: {_fmt(pitches)} px;  "
            f"largest disagreement between rows at one column: {drift:g} px"
        )
        brief["code"] = f"{_fmt(pitches)} / {drift:g}"
    return out, brief


def _space_like(glyph: Placed) -> Placed:
    """
    ``glyph`` as a space in the same face, for asking the file its space width.
    """
    return Placed(" ", glyph.font_key, 0.0, 0.0, 0.0, 0.0, 0.0, glyph.sent)


SUMMARY = (
    ("hint", "space, hinted yardstick", 26),
    ("ink", "space, ink to ink", 26),
    ("steps", "step==hinted", 13),
    ("dfile", "|step-file| mean/max", 21),
    ("touch", "box gap <0/=0/all", 19),
    ("width", "iiii.. drawn/calc", 18),
    ("code", "code steps / row drift", 23),
    ("frac", "frac pens", 10),
    ("resampled", "resampled", 10),
    ("flag", "no_snap set", 12),
)
"""
The summary table's columns: key in ``measure``'s brief, heading, width.

Spaces are those of the first paragraph, in pixels. ``step==hinted`` counts the
letter-to-letter steps equal to ImGui's advance for the letter; ``box gap`` the
adjacent letters whose bitmap boxes overlap, and abut, of all adjacent letters.
"""


def summary(rows: list[tuple[str, float, str, dict[str, str]]]) -> list[str]:
    head = f"{'face':<7}{'px':>3}  {'':<2}" + "".join(f"{title:<{w}}" for _, title, w in SUMMARY)
    out = [head, "-" * len(head)]
    for face, size, name, brief in rows:
        cells = "".join(f"{brief.get(key, '-'):<{w}}" for key, _, w in SUMMARY)
        out.append(f"{face:<7}{size:>3g}  {name:<2}{cells}")
    return out


def advances_table(fonts: Any) -> list[str]:
    """
    File advance against ImGui's, for a few glyphs of each face at each page's size.
    """
    from advtext import BlockKind, fontlib_metrics, px_from_sp

    from pptmstr.ui import typeset

    out = ["advance of one glyph, px: file / ImGui baked (get_char_advance) / pixel_metrics"]
    for face, size in PAGES:
        key = theme_for(face, size).style(BlockKind.PARAGRAPH).font.key
        file, seam = fontlib_metrics(key), typeset.pixel_metrics(key)
        baked = fonts(file.face).get_font_baked(size)
        cells = []
        for char in "i l m W a e 0 space".split():
            code = 32 if char == "space" else ord(char)
            cells.append(
                f"{char} {px_from_sp(file.advance(code)):.2f}/{baked.get_char_advance(code):g}"
                f"/{px_from_sp(seam.advance(code)):g}"
            )
        em = px_from_sp(file.em)
        out.append(
            f"  {face:<7}{size:>3g} (em {em:.3f}, baked {baked.size:g})  " + "   ".join(cells)
        )
    return out


def grab(gl: Any, imgui: Any) -> tuple[int, int, list[bytes]]:
    """
    The back buffer as top-down RGB rows. Call in ``before_swap``.
    """
    io = imgui.get_io()
    width = int(io.display_size.x * io.display_framebuffer_scale.x)
    height = int(io.display_size.y * io.display_framebuffer_scale.y)
    gl.glPixelStorei(gl.GL_PACK_ALIGNMENT, 1)
    gl.glReadBuffer(gl.GL_BACK)
    data = bytes(gl.glReadPixels(0, 0, width, height, gl.GL_RGB, gl.GL_UNSIGNED_BYTE))
    stride = width * 3
    return width, height, [data[y * stride : (y + 1) * stride] for y in range(height - 1, -1, -1)]


def zoomed(rows: list[bytes], factor: int) -> list[bytes]:
    """
    Every pixel repeated ``factor`` times each way: nearest neighbour, nothing blended.
    """
    out: list[bytes] = []
    for row in rows:
        wide = b"".join(row[x : x + 3] * factor for x in range(0, len(row), 3))
        out.extend([wide] * factor)
    return out


def read_png(path: Path) -> tuple[int, int, bytes]:
    """
    A PNG ``screenshot.write_png`` wrote, as ``(width, height, rgb)``. No other kind.
    """
    data = path.read_bytes()
    at, width, height, packed = 8, 0, 0, b""
    while at < len(data):
        (length,) = struct.unpack(">I", data[at : at + 4])
        tag, body = data[at + 4 : at + 8], data[at + 8 : at + 8 + length]
        if tag == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            packed += body
        at += 12 + length
    raw = zlib.decompress(packed)
    stride = width * 3 + 1
    if len(raw) != stride * height or any(raw[y * stride] for y in range(height)):
        raise ValueError(f"{path}: not an unfiltered 8-bit RGB PNG")
    return width, height, b"".join(raw[y * stride + 1 : (y + 1) * stride] for y in range(height))


def diff(first: Path, second: Path) -> int:
    """
    Compare the 1x PNGs two runs wrote, by name. Prints one line a page.
    """
    status = 0
    for one in sorted(first.glob("metrics-*.png")):
        if one.stem.endswith(f"-{ZOOM}x"):
            continue
        other = second / one.name
        if not other.is_file():
            print(f"{one.name}: no counterpart in {second}")
            status = 1
            continue
        (w1, h1, a), (w2, h2, b) = read_png(one), read_png(other)
        if (w1, h1) != (w2, h2):
            print(f"{one.name}: {w1}x{h1} against {w2}x{h2}; not compared")
            status = 1
            continue
        deltas = Counter(abs(x - y) for x, y in zip(a, b, strict=True))
        pixels = sum(1 for i in range(0, len(a), 3) if a[i : i + 3] != b[i : i + 3])
        worst = max(deltas)
        over = sum(count for delta, count in deltas.items() if delta > 1)
        print(
            f"{one.name}: {pixels} of {w1 * h1} pixels differ;  largest channel difference "
            f"{worst} of 255;  channel values differing by more than 1: {over}"
        )
    return status


def run(out_dir: Path, stay: bool, settle: int) -> int:
    """
    Open the window, show each page, measure and grab it, and write the files.
    """
    from imgui_bundle import hello_imgui, imgui, immapp
    from OpenGL import GL
    from screenshot import write_png

    from pptmstr import theme as ui_theme
    from pptmstr.ui import typeset

    no_snap = int(imgui.ImDrawListFlags_.text_no_pixel_snap)
    state: dict[str, Any] = {"page": 0, "shown": 0, "capturing": True, "built": None}
    report: list[str] = []
    table: list[tuple[str, float, str, dict[str, str]]] = []
    facts: dict[str, str] = {}

    def load_fonts() -> None:
        # The runner's default first, so the window's own labels keep it; then the
        # seam's load, which is what `pixel_metrics` reads its ImFonts from.
        hello_imgui.imgui_default_settings.load_default_font_with_font_awesome_icons()
        if not typeset.load_fonts(loader=typeset.hello_loader()):
            raise SystemExit(f"the faces did not load: {typeset.unavailable_reason()}")

    def columns() -> tuple[Any, Column, Column]:
        if state["built"] is None or state["built"][0] != state["page"]:
            from advtext import fontlib_metrics

            face, size = PAGES[state["page"]]
            theme = theme_for(face, size)
            state["built"] = (
                state["page"],
                theme,
                lay_out("A", theme, fontlib_metrics),
                lay_out("B", theme, typeset.pixel_metrics),
            )
        return state["built"][1:]  # type: ignore[no-any-return]

    def native(reference: list[Sent], ink: Any) -> None:
        """
        Column C: the same strings through ImGui's own text calls.

        The font and size of each part are the ones column A's draw was sent for
        it, so C is the same raster by construction. One face a paragraph: the
        inline-code line is all prose face here.
        """
        prose = reference[0]
        code = next((s for s in reversed(reference) if s.text == "|"), prose)
        imgui.push_style_color(imgui.Col_.text, ink)
        imgui.push_font(prose.font, prose.size)
        for text in (PROSE, RHYTHM, DIGITS, INLINE.replace("`", "")):
            imgui.text_wrapped(text)
            imgui.dummy(imgui.ImVec2(0.0, round(prose.size * 0.5)))
        imgui.pop_font()
        imgui.push_font(code.font, code.size)
        for row in CODE_ROWS:
            imgui.text_unformatted(row)
        imgui.pop_font()
        imgui.pop_style_color()

    def gui() -> None:
        from advtext import draw

        face, size = PAGES[state["page"]]
        theme, first, second = columns()
        palette = ui_theme.current()
        colours = typeset.palette(palette.text)
        measuring = state["capturing"] and state["shown"] == settle
        viewport = imgui.get_main_viewport()
        imgui.set_next_window_pos(viewport.pos)
        imgui.set_next_window_size(viewport.size)
        imgui.push_style_color(imgui.Col_.window_bg, palette.panel.vec4)
        imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(MARGIN_PX, MARGIN_PX))
        flags = imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_move.value
        imgui.begin("compare_metrics", None, flags)
        name = "Roboto prose, Inconsolata code" if face == "roboto" else "Inconsolata, every role"
        imgui.text_unformatted(
            f"page {state['page'] + 1}/{len(PAGES)}   {name}   {size:g} px pixel height   "
            f"column {COLUMN_PX:g} px   ragged right, one space width"
            + ("" if state["capturing"] else "   [Left/Right: page]")
        )
        captions = (
            "A  advtext draw, metrics = advtext.fontlib_metrics",
            "B  advtext draw, metrics = typeset.pixel_metrics",
            "C  imgui.text_wrapped / text_unformatted, same ImFont and size",
        )
        top = math.floor(imgui.get_cursor_screen_pos().y) + 4.0
        left = math.floor(imgui.get_window_pos().x) + MARGIN_PX
        for number, caption in enumerate(captions):
            x = left + number * (COLUMN_PX + GUTTER_PX)
            imgui.set_cursor_screen_pos(imgui.ImVec2(x, top))
            imgui.text_unformatted(caption)
            origin = imgui.ImVec2(x, top + 24.0)
            imgui.set_cursor_screen_pos(origin)
            if number < 2:
                column = (first, second)[number]
                recorder = Recorder(imgui.get_window_draw_list(), no_snap, quads=measuring)
                draw(
                    recorder,
                    column.commands,
                    origin,
                    fonts=typeset._fonts(),
                    palette=colours,
                    px_per_inch=column.px_per_inch,
                    metrics=column.resolver,
                )
                column.sent = recorder.sent
                imgui.dummy(imgui.ImVec2(COLUMN_PX, column.height_px))
            else:
                imgui.begin_child("##native", imgui.ImVec2(COLUMN_PX, 0.0))
                native(first.sent, palette.text.vec4)
                imgui.end_child()
        if measuring:
            report.append("")
            second_x = left + COLUMN_PX + GUTTER_PX
            report.append(
                f"=== {name}, {size:g} px ===  (text origin x: A {left:g}, B {second_x:g})"
            )
            for column in (first, second):
                lines, brief = measure(column, imgui)
                report.append(f"{column.name}  ({captions['AB'.index(column.name)][3:]})")
                report.extend(lines)
                table.append((face, size, column.name, brief))
            state["bottom"] = top + 24.0 + max(first.height_px, second.height_px) + MARGIN_PX
        if not state["capturing"]:
            step = imgui.is_key_pressed(imgui.Key.right_arrow) - imgui.is_key_pressed(
                imgui.Key.left_arrow
            )
            state["page"] = (state["page"] + step) % len(PAGES)
        imgui.end()
        imgui.pop_style_var()
        imgui.pop_style_color()

    def before_swap() -> None:
        if not (state["capturing"] and state["shown"] == settle):
            return
        face, size = PAGES[state["page"]]
        width, height, rows = grab(GL, imgui)
        io = imgui.get_io()
        if not facts:
            facts["renderer"] = GL.glGetString(GL.GL_RENDERER).decode()
            facts["loader"] = f"{io.fonts.font_loader_name}, flags {io.fonts.font_loader_flags}"
            scale = io.display_framebuffer_scale
            facts["framebuffer"] = f"{width}x{height}, framebuffer scale {scale.x:g}x{scale.y:g}"
            facts["font scale"] = (
                f"style.font_scale_dpi {imgui.get_style().font_scale_dpi:g}, "
                f"style.font_scale_main {imgui.get_style().font_scale_main:g}"
            )
            facts["atlas filter"] = _atlas_filter(GL, io)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"metrics-{face}-{size:g}"
        write_png(out_dir / f"{stem}.png", width, height, rows)
        scale_y = io.display_framebuffer_scale.y
        crop = min(height, int(math.ceil(state["bottom"] * scale_y)))
        write_png(
            out_dir / f"{stem}-{ZOOM}x.png", width * ZOOM, crop * ZOOM, zoomed(rows[:crop], ZOOM)
        )
        print(f"wrote {out_dir / stem}.png ({width}x{height}) and -{ZOOM}x ({crop} rows)")

    def after_swap() -> None:
        if not state["capturing"]:
            return
        state["shown"] += 1
        if state["shown"] <= settle:
            return
        state["shown"] = 0
        if state["page"] + 1 < len(PAGES):
            state["page"] += 1
            return
        state["capturing"] = False
        state["page"] = PAGES.index(("roboto", 16.0))
        text = ["compare_metrics: " + ";  ".join(f"{k}: {v}" for k, v in facts.items()), ""]
        text += advances_table(typeset._fonts())
        text += ["", *summary(table), *report]
        (out_dir / "gaps.txt").write_text("\n".join(text) + "\n")
        print("\n".join(text))
        print(f"wrote {out_dir / 'gaps.txt'}")
        if not stay:
            hello_imgui.get_runner_params().app_shall_exit = True

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "compare_metrics"
    params.app_window_params.window_geometry.size = WINDOW
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.ini_disable = True
    params.fps_idling.enable_idling = False
    params.callbacks.load_additional_fonts = load_fonts
    params.callbacks.show_gui = gui
    params.callbacks.before_swap = before_swap
    params.callbacks.after_swap = after_swap
    immapp.run(params)
    if state["capturing"]:
        print("FAIL: the window closed before every page was grabbed")
        return 1
    return 0


def _atlas_filter(gl: Any, io: Any) -> str:
    """
    The magnification filter of the font atlas texture as the GL has it, in words.
    """
    try:
        texture = int(io.fonts.tex_data.get_tex_id())
        gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
        mode = int(gl.glGetTexParameteriv(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER))
    except Exception as failure:
        return f"not read ({type(failure).__name__}: {failure})"
    names = {int(gl.GL_LINEAR): "GL_LINEAR", int(gl.GL_NEAREST): "GL_NEAREST"}
    return f"{names.get(mode, hex(mode))} (texture {texture})"


HINTING_WORDS = ("people", "difference", "iiii", "mmmm")
"""
What ``hinting`` sets natively. The first two are the words advtext's
``docs/advance-rule.md`` section 2 reads out of the vertex buffer.
"""

NO_HINTING = 1
"""
``ImGuiFreeTypeLoaderFlags_NoHinting``. The binding exposes the field and not the enum.
"""


def hinting() -> int:
    """
    Where ImGui's own ``add_text`` puts each glyph of a word, hinted and unhinted.

    No window: a frame is enough to fill a vertex buffer. Roboto and Inconsolata
    are each loaded twice at 16 px, with the loader's default flags and with
    hinting off, and each word is drawn with ``text_no_pixel_snap`` clear and
    set. Printed for each: the advances the baked font reports, the font file's,
    and the pen of every glyph (the quad's left edge less the bitmap's x0).
    """
    from advtext import Face, fontlib, fontlib_metrics, px_from_sp, sp_from_px
    from advtext.fontlib import em_size_for_pixel_height
    from advtext.style import font_key
    from imgui_bundle import imgui

    imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1024.0, 768.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures.value
    size = 16.0
    loads = []
    for face in (Face.PROSE, Face.CODE):
        for label, bits in (("default flags", 0), ("NoHinting", NO_HINTING)):
            config = imgui.ImFontConfig()
            config.font_loader_flags = bits
            path = str(fontlib.imgui_font_file(face))
            loads.append((face, label, io.fonts.add_font_from_file_ttf(path, size, config)))
    imgui.new_frame()
    draw_list = imgui.get_background_draw_list()
    no_snap = int(imgui.ImDrawListFlags_.text_no_pixel_snap)
    print(f"loader: {io.fonts.font_loader_name};  {size:g} px pixel height;  origin x = 100")
    for face, label, font in loads:
        baked = font.get_font_baked(size)
        file = fontlib_metrics(font_key(face, sp_from_px(em_size_for_pixel_height(face, size))))
        print(f"\n{face.name}, {label}")
        for word in HINTING_WORDS:
            said = [round(float(baked.get_char_advance(ord(c))), 3) for c in word]
            real = [round(float(px_from_sp(file.advance(ord(c)))), 3) for c in word]
            print(f"  {word!r}: baked advances {said}")
            print(f"  {'':<{len(word) + 3}} file advances  {real}")
            for flagged in (False, True):
                restore = draw_list.flags
                draw_list.flags = (restore | no_snap) if flagged else (restore & ~no_snap)
                start = len(draw_list.vtx_buffer)
                draw_list.add_text(font, size, imgui.ImVec2(100.0, 50.0), 0xFFFFFFFF, word)
                draw_list.flags = restore
                buffer = draw_list.vtx_buffer
                xs = [buffer[i].pos.x for i in range(start, len(buffer))]
                lefts = [min(xs[i : i + 4]) for i in range(0, len(xs) - 3, 4)]
                pens = [
                    round(left - float(baked.find_glyph(ord(c)).x0), 3)
                    for left, c in zip(lefts, word, strict=True)
                ]
                fractional = sum(1 for pen in pens if pen != math.floor(pen))
                state = "set  " if flagged else "clear"
                print(f"    no_pixel_snap {state} pens {pens}  fractional: {fractional}")
    imgui.render()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "shots" / "metrics")
    parser.add_argument(
        "--frames",
        type=int,
        default=SETTLE_FRAMES,
        help="frames a page is shown before it is grabbed; 0 grabs every page and stays open",
    )
    parser.add_argument("--diff", nargs=2, type=Path, metavar="DIR", help="compare two runs' PNGs")
    parser.add_argument(
        "--hinting", action="store_true", help="ImGui's own glyph placement, hinted and not"
    )
    args = parser.parse_args()
    if args.diff:
        return diff(*args.diff)
    if args.hinting:
        return hinting()
    if args.frames < 0:
        parser.error("--frames must not be negative")
    return run(args.out, args.frames == 0, args.frames or SETTLE_FRAMES)


if __name__ == "__main__":
    raise SystemExit(main())
