#!/usr/bin/env python3
"""
The same text set four ways in one window, and the same text at three line pitches.

``pptmstr/ui/typeset.py`` does not measure text with ``advtext``'s own font-file
metrics (``advtext.fontlib_metrics``). It hands the library a resolver,
``pixel_metrics``, that asks ImGui's baked font for each advance, and it sets each
role's lines a whole number of pixels apart. This script puts those choices beside
their alternatives so a person can judge them, and prints what was drawn as
numbers. It argues for none: the labels in the window say what each column is and
nothing else.

## The metrics pages: whose advances, and which raster

    A   advtext's ``draw``, document measured with ``advtext.fontlib_metrics``,
        drawn from the hinted faces (the pipeline of advtext's
        ``examples/minimal.py``: build, compose, ship, draw)
    B   the same four calls, document measured with ``typeset.pixel_metrics``,
        hinted faces: what the seam does under ``typeset.METRICS = "hinted"``
    D   A's layout to the scaled point (``advtext.fontlib_metrics``), drawn from the
        same font files loaded a second time with hinting off, by advtext's
        ``draw`` untouched: what ``typeset.METRICS = "file"`` asks of the library
    C   ``imgui.text_wrapped`` / ``text_unformatted`` in A's ``ImFont`` at the
        same baked size, for what the chrome around a pane looks like

They stand in that order, left to right. A, B and D share one ``Theme``, one
palette, one ink, one width and one atlas. The theme is
``Theme.at_pixel_height(size, align=LEFT, spacing=FRENCH)``: ragged right and one
space width, so that a word gap that varies does so because of rounding and not
because a line was justified or a full stop widened its space. It is the
library's theme otherwise, so in the Roboto pages fenced code is Inconsolata at
the paragraph's em and is baked a size or two under the page's (each ``code row``
line of ``gaps.txt`` says which).

D does not go through ``typeset.METRICS``, which is one value a process: the
script loads the unhinted set itself (``ImFontConfig.font_loader_flags = 1``) and
hands ``draw`` a ``fonts`` callable that answers with it, so hinted and unhinted
are in one frame. It refuses to run if ``typeset.METRICS`` is not ``"hinted"``,
because then A, B and C would not be what their captions say.

A Inconsolata page also sets one line of code in D directly over the same string
through ``ImDrawList.add_text`` in the hinted face (C's raster), from one x, so
that drift between typeset code and chrome text shows as a sheared column.

## The leading pages: one pipeline, three pitches

``metrics-leading-<face>-<size>`` is column B's pipeline alone (hinted faces,
``typeset.pixel_metrics``, advtext's ``draw``), three times, with ImGui beside it:

    own   the library theme as it comes: the paragraph's em x 1.35 between
          baselines, a code block's em x 1.25
    17    ``interline.baselineskip.width`` of the paragraph and code styles replaced
          with 17 px, a hair over (``math.ceil(px * sp_per_px)``), as the seam's
          ``_fixed`` does for a text block
    16    the same with 16 px, which is ImGui's own pitch for wrapped text
    C     ``imgui.text_wrapped`` and one multi-line ``text_unformatted``

Column A is not on these pages. Each part of the text (a paragraph, a paragraph
of accented capitals, brackets and descenders, a fenced block with a ``tree``
listing) is a document of its own, stacked with a fixed gap, so that every
baseline step measured is one inside a block. ``pixel_metrics`` measures no glyph
taller than the strut of the tightest ``typeset.LEADING`` (``_LineMetrics.bbox``);
to show what that buys, ``gaps.txt`` also gives the steps the same themes come to
when laid out with ``fontlib_metrics``, which does not. That layout is not drawn.

**This is the second module that imports ``advtext``**, with
``scripts/survey_fonts.py``; every pane goes through ``pptmstr.ui.typeset``. It also
reaches into the seam for something it has no public name for (``typeset._fonts``)
so that A and B draw from the very ``ImFont``s ``pixel_metrics`` reads.

## What is measured, and from what

Nothing is measured from pixels. ``advtext.draw`` is handed a draw list that
records every ``add_text`` it is sent -- font, size, position, the list's flags at
that moment -- and passes it on to ImGui unchanged, and on the frame that is
grabbed it also reads the quad ImGui emitted for the glyph back out of the vertex
buffer. The frame measured is the frame in the PNG.

Four yardsticks appear and they are kept apart:

    file     the advance (and pair kern), and the box, in the font file, scaled:
             ``fontlib_metrics``
    laid     what the column's own resolver told the layout: the file's in A and
             D, ImGui's hinted advance in B
    baked    ``ImFontBaked.get_char_advance`` of the ``ImFont`` the glyph was drawn
             from: the hinter's whole pixel in A and B, the file's again in D
    ink      ``ImFontBaked.find_glyph``'s x0/x1 and the emitted quad: the bitmap's
             own box. A and B draw the same bitmaps, so an ink gap between them
             is the one figure with no opinion in it; D's bitmaps are others

On the leading pages a collision between two lines is given twice: by the file's
boxes on the baselines as drawn, and by the rows the emitted quads cover. A quad
is the bitmap's box, antialiased edge rows included, so it can reach a row past
the outline.

## Running it

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/compare_metrics.py
    .venv/bin/python scripts/compare_metrics.py --out shots/metrics/display --frames 0
    .venv/bin/python scripts/compare_metrics.py --diff shots/metrics shots/metrics/display

    .venv/bin/python scripts/compare_metrics.py --hinting

The first writes, under ``shots/metrics``, one PNG a page (``metrics-<face>-<size>``
and ``metrics-leading-<face>-<size>``), a 4x nearest-neighbour zoom of each (every
source pixel becomes a 4x4 block; no filtering), and ``gaps.txt``. The second does
the same on whatever display is there and then stays open, with the pages on the
arrow keys. The third compares two such directories PNG by PNG, which is how to
find out whether the software rasteriser's pixels are the GPU's. The fourth needs
no display: it prints where ImGui's own ``add_text`` puts the glyphs of a word
when the face is loaded with the loader's default flags and with hinting off,
which is what says whether a whole-pixel advance is the loader's doing or ImGui's.
"""

from __future__ import annotations

import argparse
import math
import statistics
import struct
import sys
import zlib
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

COLUMN_PX = 400.0
"""
The width every typeset column is broken to and C wraps at: about the DETAIL dock's.
"""

COLUMNS = 4
GUTTER_PX = 24.0
MARGIN_PX = 16.0
WINDOW = (int(COLUMNS * COLUMN_PX + (COLUMNS - 1) * GUTTER_PX + 2 * MARGIN_PX), 940)
ZOOM = 4
RESIZE_FRAMES = 5
"""
Frames the first page waits for the window to become ``WINDOW`` wide after it is
asked to: the runner opens a window no wider than the screen, and the Xvfb screen
of ``scripts/xvfb_shot.sh`` is narrower than four columns.
"""

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
The metrics pages. A face table and a pixel height: Roboto prose with Inconsolata
code, or Inconsolata in every role. 15 and 17 are there for the monospace face alone.
"""

LEADING_PAGES: tuple[tuple[str, float], ...] = (("roboto", 16.0), ("mono", 16.0))
"""
The leading pages: the same face tables, at the size the app's body text is.
"""

LEADING_PITCHES: tuple[float | None, ...] = (None, 17.0, 16.0)
"""
The line pitches of a leading page, in pixels. ``None`` is the theme's own.
"""

PANE_PX = 600.0
"""
The height "lines that fit" is counted in on the leading pages: a tall dock's.
"""

PART_GAP_PX = 12.0
"""
Between the parts of a leading column, each of which is a document of its own.
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
INLINE_PLAIN = INLINE.replace("`", "")
RULER = "1234567890" * 4
"""
A code row long enough to read a column pitch off, and to have a 40th character.
"""

CODE_ROWS = (
    "name       size  kind",
    "alpha         1  file",
    "beta_long    42  dir",
    "iiii|llll|mmmm|WWWW|",
    "0000|1111|8888|....|",
    RULER,
)
MARKDOWN = "\n\n".join((PROSE, RHYTHM, DIGITS, INLINE, "```\n" + "\n".join(CODE_ROWS) + "\n```"))
CODE_KEYS = frozenset(row.strip() for row in CODE_ROWS)

STACK_ROW = "    return float(baked.get_char_advance(code))  # the 40th is the second ("
"""
The line of code a monospace page sets in D over C. Its 40th character has ink.
"""

EXTREMES = " ".join(("ÅÉ[gjpqy]", "{ÅÉ}(gjpqy)", "|ÅÉ|gjpqy") * 7)
"""
Accented capitals, brackets and descenders, enough of them to wrap: every line
has the face's tallest and deepest ink, so each lands over the other.
"""

TREE_ROWS = (
    "src",
    "├── pptmstr",
    "│   ├── app.py",
    "│   └── ui",
    "│       └── typeset.py",
    "└── tests",
    'x = {"gjpqy": [ÅÉ]}  # ÅÉ (gjpqy)',
    'y = {"gjpqy": [ÅÉ]}  # ÅÉ (gjpqy)',
)
"""
A ``tree`` listing, whose rules are drawn to join the row above and below, and
two rows of code with accents under descenders.
"""

LEADING_PARTS: tuple[tuple[str, str], ...] = (
    ("prose", PROSE),
    ("extremes", EXTREMES),
    ("code", "```\n" + "\n".join(TREE_ROWS) + "\n```"),
)
"""
What a leading column sets, top to bottom: a name for the report, and markdown.
"""

RULE_DOWN = "│├"
RULE_UP = "│├└"
"""
Box-drawing characters with a stroke to the row below, and to the row above.
"""

RULES = "│├└─"
"""
Every box-drawing character of the listing.
"""

CAPTIONS = {
    "A": "advtext draw, fontlib_metrics, hinted faces",
    "B": "advtext draw, typeset.pixel_metrics, hinted faces",
    "D": "advtext draw, fontlib_metrics, faces loaded unhinted",
    "C": "imgui.text_wrapped / text_unformatted, hinted faces",
    "C leading": "imgui.text_wrapped / text_unformatted: ImGui's pitch",
}
"""
What the window says over each column, and ``gaps.txt`` beside its name.
"""

SHEETS: tuple[tuple[str, str, float], ...] = (
    *(("metrics", face, size) for face, size in PAGES),
    *(("leading", face, size) for face, size in LEADING_PAGES),
)
"""
Every page in the order shown: its kind, its face table, its pixel height.
"""


def _stem(kind: str, face: str, size: float) -> str:
    """
    A page's file name, less the extension.
    """
    return f"metrics-leading-{face}-{size:g}" if kind == "leading" else f"metrics-{face}-{size:g}"


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

    ``laid`` and ``laid_y`` are the layout's x and baseline in pixels (document
    space, exact); ``pen`` is where the draw put it on screen. ``baked``, ``left``
    and ``right`` are the advance and the bitmap box ImGui has for the glyph in
    the ``ImFont`` it was drawn from, and ``ascent`` that font's at the size, so
    ``sent.y + ascent`` is the baseline as drawn. ``font_key`` is the layout's.
    """

    char: str
    font_key: str
    laid: float
    laid_y: float
    pen: float
    baked: float
    left: float
    right: float
    ascent: float
    sent: Sent

    @property
    def baseline(self) -> float:
        return self.sent.y + self.ascent


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
    One document as one typeset column draws it: commands, height, the last frame's draw.

    ``fonts`` is the ``FaceFont`` handed to ``draw``: the seam's hinted set, or the
    unhinted one.
    """

    name: str
    caption: str
    resolver: Any
    fonts: Any
    commands: tuple[Any, ...]
    height_px: float
    px_per_inch: float
    sent: list[Sent] = field(default_factory=list)


@dataclass(slots=True)
class Pitched:
    """
    One column of a leading page: the parts of the text, each set at one pitch.

    ``asked`` is the pitch in pixels, ``None`` for the theme's own. ``loose`` is,
    for each part, the baselines of the same theme laid out with
    ``fontlib_metrics``, in pixels: the layout that does not clamp a glyph's box.
    """

    name: str
    caption: str
    asked: float | None
    theme: Any
    parts: list[tuple[str, Column]]
    loose: list[list[float]]


def lay_out(
    name: str, caption: str, theme: Any, resolver: Any, fonts: Any, markdown: str = MARKDOWN
) -> Column:
    """
    ``examples/minimal.py``'s parse, compose and ship, with ``resolver`` throughout.
    """
    from advtext import Point, build_document, char_measure, compose, px_from_sp, ship, sp_from_px

    ppi = theme.px_per_inch
    measure = char_measure(resolver)
    document = build_document(markdown, theme, resolver)
    box = compose(document, theme, sp_from_px(COLUMN_PX, ppi), measure)
    commands = tuple(ship(box, Point(0, box.height), measure=measure))
    height = px_from_sp(box.height + box.depth, ppi)
    return Column(name, caption, resolver, fonts, commands, height, ppi)


def theme_for(face: str, size: float) -> Any:
    from advtext import Align, Face, Faces, Spacing, Theme

    faces = Faces.mono(bold=Face.CODE) if face == "mono" else Faces()
    return Theme.at_pixel_height(size, faces=faces, align=Align.LEFT, spacing=Spacing.FRENCH)


def with_pitch(theme: Any, pitch: float | None) -> Any:
    """
    ``theme`` with its paragraph and code baselines ``pitch`` pixels apart.

    The seam's ``_fixed`` for a text block: a whole pixel is not a whole number of
    scaled points, and the skip is taken a hair over it. ``None`` is the theme.
    """
    from advtext import BlockKind, sp_from_px

    if pitch is None:
        return theme
    width = math.ceil(pitch * float(sp_from_px(4096.0, theme.px_per_inch)) / 4096.0)
    blocks = dict(theme.blocks)
    for kind in (BlockKind.PARAGRAPH, BlockKind.CODE):
        skip = blocks[kind].interline
        blocks[kind] = replace(
            blocks[kind],
            interline=replace(skip, baselineskip=replace(skip.baselineskip, width=width)),
        )
    return replace(theme, blocks=blocks)


def flush_code(theme: Any) -> Any:
    """
    ``theme`` with a code block at the column's left edge and nothing above it.
    """
    from advtext import BlockKind

    blocks = dict(theme.blocks)
    blocks[BlockKind.CODE] = replace(blocks[BlockKind.CODE], left_indent=0, space_above=0)
    return replace(theme, blocks=blocks)


def own_pitch(theme: Any) -> tuple[float, float]:
    """
    The baseline distance a theme asks for in a paragraph and in a code block, px.
    """
    from advtext import BlockKind, px_from_sp

    ppi = theme.px_per_inch
    return tuple(  # type: ignore[return-value]
        float(px_from_sp(theme.style(kind).interline.baselineskip.width, ppi))
        for kind in (BlockKind.PARAGRAPH, BlockKind.CODE)
    )


def pitched(face: str, size: float, pitch: float | None, resolver: Any, fonts: Any) -> Pitched:
    """
    A leading column: every part laid out at ``pitch`` with ``resolver``.
    """
    from advtext import fontlib_metrics, px_from_sp
    from advtext.shipout import Line

    theme = with_pitch(theme_for(face, size), pitch)
    prose, code = own_pitch(theme)
    if pitch is None:
        name = "own"
        caption = f"B, the theme's own pitch: {prose:.2f} px prose, {code:.2f} px code"
    else:
        name = f"{pitch:g}"
        caption = f"B, {pitch:g} px between baselines in prose and in code"
    parts, loose = [], []
    for part, markdown in LEADING_PARTS:
        parts.append((part, lay_out(name, caption, theme, resolver, fonts, markdown)))
        unclamped = lay_out(name, caption, theme, fontlib_metrics, fonts, markdown)
        loose.append(
            [
                float(px_from_sp(cmd.y, theme.px_per_inch))
                for cmd in unclamped.commands
                if isinstance(cmd, Line)
            ]
        )
    return Pitched(name, caption, pitch, theme, parts, loose)


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
                    laid_y=px_from_sp(cmd.y, ppi),
                    pen=record.x,
                    baked=float(baked.get_char_advance(cmd.codepoint)),
                    left=float(glyph.x0),
                    right=float(glyph.x1),
                    ascent=float(baked.ascent),
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


def _laid_step(column: Column, glyph: Placed) -> float:
    """
    The advance the column's own resolver gave the layout for a glyph, in pixels.
    """
    from advtext import px_from_sp

    advance = column.resolver(glyph.font_key).advance(ord(glyph.char))
    return float(px_from_sp(advance, column.px_per_inch))


@cache
def _file_ink(font_key: str, char: str) -> tuple[float, float]:
    """
    How far the font file's outline reaches above and below the baseline, in pixels.
    """
    from advtext import fontlib_metrics, px_from_sp

    box = fontlib_metrics(font_key).bbox(ord(char))
    return float(px_from_sp(box.y_max)), float(px_from_sp(-box.y_min))


def _fmt(values: list[float]) -> str:
    return " ".join(f"{value:g}" for value in (round(v, 2) for v in values))


def _tally(values: list[float]) -> str:
    """
    Distinct values to the hundredth with how many of each, most frequent first.
    """
    if not values:
        return "none"
    counts = Counter(round(value, 2) for value in values)
    return ", ".join(f"{value:g} x{count}" for value, count in counts.most_common())


def _spread(values: list[float]) -> str:
    if not values:
        return "n=0"
    deviation = statistics.pstdev(values) if len(values) > 1 else 0.0
    return (
        f"n={len(values)} min={min(values):.2f} max={max(values):.2f} "
        f"mean={statistics.fmean(values):.2f} sd={deviation:.2f}"
    )


def _said(word: list[Placed]) -> str:
    return "".join(glyph.char for glyph in word)


def _gap_rows(spaces: list[tuple[Placed, Placed, float]]) -> dict[str, list[float]]:
    """
    Every space of a line four ways. See the module docstring for the yardsticks.
    """
    return {
        "laid out (glue as set)": [glue for _, _, glue in spaces],
        "drawn, by baked adv": [b.pen - a.pen - a.baked for a, b, _ in spaces],
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


def _widths(column: Column, glyphs: list[Placed]) -> tuple[float, float]:
    """
    A run of glyphs from first pen to the end of the last advance: as drawn, as laid out.
    """
    first, last = glyphs[0], glyphs[-1]
    drawn = last.pen + last.baked - first.pen
    return drawn, last.laid + _laid_step(column, last) - first.laid


def measure(column: Column, imgui: Any, chrome: Any) -> tuple[list[str], dict[str, str]]:
    """
    The column's numbers as report lines, and the few of them the summary table carries.

    ``chrome`` is the hinted ``FaceFont``: what a fenced row's pitch is put beside.
    """
    from advtext import Face

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
    steps = same = whole = 0
    departures: list[float] = []
    signed: list[float] = []
    ink: Counter[int] = Counter()
    excess: list[tuple[float, list[Placed]]] = []
    for line in lines:
        for word in line.words():
            if len(word) > 1:
                over = (word[-1].pen - word[0].pen) - (word[-1].laid - word[0].laid)
                excess.append((over, word))
            for this, following in zip(word, word[1:], strict=False):
                step = following.pen - this.pen
                steps += 1
                same += abs(step - this.baked) < 1e-4
                whole += step == math.floor(step)
                signed.append(step - _file_step(this, following))
                departures.append(abs(signed[-1]))
                ink[round((following.pen + following.left) - (this.pen + this.right))] += 1
    worst = max(departures, default=0.0)
    mean = statistics.fmean(departures) if departures else 0.0
    out.append(
        f"  letter steps inside spans: {steps};  a whole number of pixels: {whole};  equal to "
        f"the baked advance of the font drawn from: {same}"
    )
    out.append(
        f"  step - file advance+kern, px: |mean| {mean:.3f} max {worst:.3f};  signed mean "
        f"{statistics.fmean(signed) if signed else 0.0:+.3f}"
    )
    histogram = "  ".join(f"{gap}px:{count}" for gap, count in sorted(ink.items()))
    out.append(f"  ink gap between adjacent letters (bitmap box to box), px:count  {histogram}")
    overlap = sum(count for gap, count in ink.items() if gap < 0)
    brief["steps"] = f"{same}/{steps}"
    brief["dfile"] = f"{mean:.2f}/{worst:.2f}"
    brief["touch"] = f"{overlap}/{ink[0]}/{steps}"

    # A rigid span as drawn against as laid out, and what is left of each space.
    if excess:
        overs = [over for over, _ in excess]
        most, widest = max(excess, key=lambda pair: pair[0])
        laid = widest[-1].laid - widest[0].laid
        out.append(
            f"  rigid spans of 2+ glyphs: {len(excess)};  first pen to last pen, drawn - laid "
            f"out, px: {_spread(overs)}"
        )
        out.append(
            f"    widest of it: {_said(widest)!r} laid out {laid:.2f} px, drawn "
            f"{laid + most:.2f} px ({most:+.2f} px, {100.0 * most / laid if laid else 0.0:+.1f}%)"
        )
        brief["wide"] = f"{statistics.fmean(overs):+.2f}/{most:+.2f}"
    ending = {id(word[-1]): word for line in lines for word in line.words()}
    every_space = [space for line in lines for space in line.spaces()]
    if every_space:
        left_over = [(b.pen - a.pen - a.baked, a) for a, b, _ in every_space]
        thin = sum(1 for gap, _ in left_over if gap < 1.0)
        least, after = min(left_over, key=lambda pair: pair[0])
        out.append(
            f"  spaces on the page: {len(every_space)};  drawn under 1 px wide by the baked "
            f"advance: {thin};  narrowest {least:.2f} px, after {_said(ending[id(after)])!r}"
        )
    spans = [(_widths(column, line.glyphs), line) for line in lines]
    differences = [drawn - laid for (drawn, laid), _ in spans]
    out.append(
        f"  whole lines, first pen to end of last advance, drawn - laid out, px: "
        f"{_spread(differences)}"
    )

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
        drawn, laid = _widths(column, placed)
        native = _native_width(imgui, placed, line.text)
        said = "mixed faces" if native is None else f"{native:.2f}"
        out.append(
            f"    width drawn {drawn:.2f} px;  laid out {laid:.2f} px;  "
            f"imgui.calc_text_size in the font drawn from {said} px"
        )
    every = [space for line in paragraph for space in line.spaces()]
    rows = _gap_rows(every)
    out.append(f"  first paragraph, all {len(every)} spaces on {len(paragraph)} lines:")
    for label, values in rows.items():
        out.append(f"    {label:<23} {_spread(values)}")
        if label != "laid out (glue as set)":
            out.append(f"    {'':<23} distinct: {_tally(values)}")
    for key, label in (("hint", "drawn, by baked adv"), ("ink", "drawn, ink to ink")):
        values = rows[label]
        deviation = statistics.pstdev(values) if len(values) > 1 else 0.0
        brief[key] = f"{min(values):.1f}..{max(values):.1f} sd {deviation:.2f}" if values else "-"

    # The paragraph with code spans in it: each space, named by the span before it.
    spanned = [line for line in lines if line.text and line.text in INLINE_PLAIN]
    if not spanned:
        out.append("  inline-code paragraph: no line of it was recognised")
    for line in spanned:
        out.append(f"  inline-code line {line.text!r}")
        out.append("    space after each span: laid out / drawn by baked adv / ink to ink, px")
        cells = [
            f"{_said(ending[id(a)])!r} {glue:.2f}/{b.pen - a.pen - a.baked:.2f}"
            f"/{(b.pen + b.left) - (a.pen + a.right):g}"
            for a, b, glue in line.spaces()
        ]
        out.append("    " + "   ".join(cells))

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
                hint_end = hint_at + item.baked
                file_at += _file_step(item, following if isinstance(following, Placed) else None)
                hint_at += item.baked
            else:
                file_at += space_file
                hint_at += space_hint
        out.append(f"  {line.text!r}: x of each glyph from the first, px")
        out.append(f"    laid out          {_fmt([g.laid - first.laid for g in placed])}")
        out.append(f"    drawn (pen)       {_fmt([g.pen - first.pen for g in placed])}")
        out.append(f"    cum. file adv     {_fmt(file_x)}")
        out.append(f"    cum. baked adv    {_fmt(hint_x)}")
        drawn, laid = _widths(column, placed)
        native = _native_width(imgui, placed, line.text)
        said = "mixed faces" if native is None else f"{native:.2f}"
        out.append(
            f"    width drawn {drawn:.2f} px;  laid out {laid:.2f};  cum. file {file_end:.2f};  "
            f"cum. baked {hint_end:.2f};  imgui.calc_text_size {said}"
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
            f"  code row {line.text!r} (baked {baked.size:g} px, baked adv "
            f"{placed[0].baked:g}, file adv {_file_step(placed[0], None):.3f}, "
            f"row starts at screen x {placed[0].pen:g}, rigid spans on the row: "
            f"{len(line.words())})"
        )
        out.append(f"    column x          {_fmt(xs)}")
        if line.text != RULER:
            continue
        count = len(placed) - 1
        drawn_pitch = xs[-1] / count
        laid_pitch = (placed[-1].laid - placed[0].laid) / count
        hinted = chrome(Face.CODE).get_font_baked(baked.size)
        native_pitch = float(hinted.get_char_advance(ord("0")))
        out.append(
            f"    pitch over {count} steps: drawn {drawn_pitch:.3f} px a character, laid out "
            f"{laid_pitch:.3f};  the chrome's hinted Inconsolata at {baked.size:g} px (C): "
            f"{native_pitch:g}"
        )
        out.append(
            f"    characters in {COLUMN_PX:g} px: drawn {math.floor(COLUMN_PX / drawn_pitch)}, "
            f"as the layout counts {math.floor(COLUMN_PX / laid_pitch)}, "
            f"C {math.floor(COLUMN_PX / native_pitch)}"
        )
        out.append(
            f"    the 40th character from the row's first: drawn {xs[39]:g} px, laid out "
            f"{placed[39].laid - placed[0].laid:.2f}, C {39 * native_pitch:g}"
        )
        brief["pitch"] = (
            f"{drawn_pitch:.2f}/{laid_pitch:.2f}/{native_pitch:g} "
            f"{math.floor(COLUMN_PX / drawn_pitch)}/{math.floor(COLUMN_PX / laid_pitch)}"
            f"/{math.floor(COLUMN_PX / native_pitch)}"
        )
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
    return Placed(
        char=" ",
        font_key=glyph.font_key,
        laid=0.0,
        laid_y=0.0,
        pen=0.0,
        baked=0.0,
        left=0.0,
        right=0.0,
        ascent=0.0,
        sent=glyph.sent,
    )


def native_line(
    draw_list: Any, imgui: Any, font: Any, size: float, x: float, y: float, colour: int, text: str
) -> tuple[list[float], int, int]:
    """
    One string through ``ImDrawList.add_text``, and the pen of each character.

    The pens are ``x`` plus the baked advances before the character. Returned with
    them: how many glyph quads the call emitted, and how many of those have their
    left edge where that pen and the bitmap's x0 put it. The two counts are equal
    when the cumulative advances are what ImGui did.
    """
    baked = font.get_font_baked(size)
    start = len(draw_list.vtx_buffer)
    draw_list.add_text(font, size, imgui.ImVec2(x, y), colour, text)
    pens, at = [], x
    for char in text:
        pens.append(at)
        at += float(baked.get_char_advance(ord(char)))
    buffer = draw_list.vtx_buffer
    xs = [buffer[i].pos.x for i in range(start, len(buffer))]
    lefts = [min(xs[i : i + 4]) for i in range(0, len(xs) - 3, 4)]
    inked = [
        pen + float(baked.find_glyph(ord(char)).x0)
        for pen, char in zip(pens, text, strict=True)
        if char != " "
    ]
    agree = 0
    if len(lefts) == len(inked):
        agree = sum(1 for left, want in zip(lefts, inked, strict=True) if abs(left - want) < 1e-4)
    return pens, len(lefts), agree


def measure_stack(
    column: Column, native: tuple[list[float], int, int], chrome: Any
) -> tuple[list[str], dict[str, str]]:
    """
    The line of code set in D over the same string in the hinted face: pen against pen.
    """
    from advtext import Face

    lines, _ = set_lines(column)
    pens, quads, agree = native
    if not lines or len(lines[0].glyphs) != len(STACK_ROW) or len(pens) != len(STACK_ROW):
        return ["  D over C: the line was not drawn whole; nothing compared"], {}
    line = lines[0]
    placed = line.glyphs
    size = placed[0].sent.size
    hinted = chrome(Face.CODE).get_font_baked(size)
    steps = [b.pen - a.pen for a, b in pairwise(placed)]
    out = [
        f"  D over C, one line of code from one x ({pens[0]:g}): {STACK_ROW!r}",
        f"    D: {len(placed)} glyphs in {len(line.words())} rigid span(s), baked adv "
        f"{placed[0].baked:g} px, steps drawn {_tally(steps)}",
        f"    C: ImDrawList.add_text, hinted Inconsolata at {size:g} px, adv "
        f"{float(hinted.get_char_advance(ord('0'))):g} px;  {agree} of {quads} glyph quads are "
        f"where the cumulative baked advances put them",
    ]
    marks = [0, 9, 19, 29, 39, len(placed) - 1]
    out.append("    character:        " + "  ".join(f"{i + 1:>7}" for i in marks))
    out.append(
        "    D pen - C pen, px " + "  ".join(f"{placed[i].pen - pens[i]:>7.2f}" for i in marks)
    )
    out.append(
        "    D drawn - D laid  "
        + "  ".join(
            f"{(placed[i].pen - placed[0].pen) - (placed[i].laid - placed[0].laid):>7.2f}"
            for i in marks
        )
    )
    forty = placed[39]
    left = float(hinted.find_glyph(ord(forty.char)).x0)
    out.append(
        f"    the 40th character {forty.char!r}: D pen {forty.pen:g}, C pen {pens[39]:g} "
        f"({forty.pen - pens[39]:+.2f} px);  ink's left edge D {forty.pen + forty.left:g}, "
        f"C {pens[39] + left:g} ({forty.pen + forty.left - pens[39] - left:+.2f} px);  the layout "
        f"has it {forty.laid - placed[0].laid:.2f} px from the first, the draw "
        f"{forty.pen - placed[0].pen:g}"
    )
    return out, {"drift": f"{forty.pen - pens[39]:+.2f}"}


def measure_leading(stack: Pitched) -> tuple[list[str], dict[str, dict[str, str]]]:
    """
    A leading column's numbers: per part, the steps set, what fits, what collides.
    """
    out: list[str] = [f"{stack.name}  ({stack.caption})"]
    briefs: dict[str, dict[str, str]] = {}
    for (part, column), loose in zip(stack.parts, stack.loose, strict=True):
        lines, missing = set_lines(column)
        brief = briefs[part] = {}
        if len(lines) < 2:
            out.append(f"  {part}: fewer than two lines were drawn; nothing measured")
            continue
        first = lines[0].glyphs[0]
        laid = [b.glyphs[0].laid_y - a.glyphs[0].laid_y for a, b in pairwise(lines)]
        drawn = [b.glyphs[0].baseline - a.glyphs[0].baseline for a, b in pairwise(lines)]
        tops = sum(1 for line in lines for g in line.glyphs if g.sent.y != math.floor(g.sent.y))
        out.append(
            f"  {part}: {len(lines)} lines, baked {first.sent.size:g} px, block "
            f"{column.height_px:.2f} px tall (not in face: {missing};  glyph tops at a "
            f"fractional y: {tops})"
        )
        out.append(f"    baseline to baseline, laid out, px:  {_tally(laid)}")
        equal = "all equal" if len({round(step, 2) for step in drawn}) == 1 else "NOT all equal"
        out.append(f"    baseline to baseline, drawn, px:     {_tally(drawn)}   [{equal}]")
        free = [b - a for a, b in pairwise(loose)]
        out.append(f"    same theme laid out with fontlib_metrics (not drawn), px: {_tally(free)}")
        mean = statistics.fmean(drawn)
        fit = math.floor(PANE_PX / mean)
        out.append(f"    lines in {PANE_PX:g} px at the mean drawn step {mean:.2f} px: {fit}")
        brief["steps"] = _tally(drawn)
        brief["loose"] = _tally(free)
        brief["fit"] = f"{fit}"

        # Line n+1 against line n: the file's outlines on the drawn baselines, and
        # the rows the emitted quads cover. Again without the rules where there
        # are any, which are drawn to overlap and would hide a letter that does.
        ruled = any(g.char in RULES for line in lines for g in line.glyphs)
        for label, skip in (("", ""), (", the rules left out", RULES))[: 2 if ruled else 1]:
            by_file: list[tuple[float, str, str]] = []
            by_quad: list[float] = []
            for upper, lower in pairwise(lines):
                over_ink = [g for g in upper.glyphs if g.char not in skip]
                under_ink = [g for g in lower.glyphs if g.char not in skip]
                if not over_ink or not under_ink:
                    continue
                deep = max(over_ink, key=lambda g: g.baseline + _file_ink(g.font_key, g.char)[1])
                tall = min(under_ink, key=lambda g: g.baseline - _file_ink(g.font_key, g.char)[0])
                foot = deep.baseline + _file_ink(deep.font_key, deep.char)[1]
                head = tall.baseline - _file_ink(tall.font_key, tall.char)[0]
                by_file.append((foot - head, deep.char, tall.char))
                feet = [g.sent.quad[3] for g in over_ink if g.sent.quad is not None]
                heads = [g.sent.quad[1] for g in under_ink if g.sent.quad is not None]
                if feet and heads:
                    by_quad.append(max(feet) - min(heads))
            hits = sum(1 for over, _, _ in by_file if over > 1e-6)
            over, deep_char, tall_char = max(by_file)
            out.append(
                f"    line n+1 has ink above the foot of line n{label}, by the file's boxes: "
                f"{hits} of {len(by_file)} pairs;  most: {over:+.2f} px ({tall_char!r} under "
                f"{deep_char!r};  + is overlap, - is clear air)"
            )
            rows = sum(1 for value in by_quad if value > 0)
            out.append(
                f"    the same by the quads emitted (bitmap boxes): {rows} of {len(by_quad)} "
                f"pairs share a row;  rows shared, per pair: {_tally(by_quad)}"
            )
            brief["file"] = f"{hits}/{len(by_file)} {over:+.2f}"
            brief["quad"] = f"{rows}/{len(by_quad)} {max(by_quad, default=0.0):+g}"

        # The tree's rules: each stroke that should meet the one under it.
        joins: dict[str, list[tuple[float, float | None]]] = {}
        for upper, lower in pairwise(lines):
            for above, below in zip(upper.glyphs, lower.glyphs, strict=False):
                if above.char not in RULE_DOWN or below.char not in RULE_UP:
                    continue
                foot = above.baseline + _file_ink(above.font_key, above.char)[1]
                head = below.baseline - _file_ink(below.font_key, below.char)[0]
                quad = None
                if above.sent.quad is not None and below.sent.quad is not None:
                    quad = below.sent.quad[1] - above.sent.quad[3]
                joins.setdefault(f"{above.char} over {below.char}", []).append((head - foot, quad))
        for pair, gaps in joins.items():
            file_gaps = [gap for gap, _ in gaps]
            quad_gaps = [gap for _, gap in gaps if gap is not None]
            out.append(
                f"    {pair} ({len(gaps)}): by the file's boxes {_joined(file_gaps)};  by the "
                f"quads emitted {_joined(quad_gaps)}"
            )
        bars = [gap for gap, _ in joins.get("│ over │", [])]
        bar_rows = [gap for _, gap in joins.get("│ over │", []) if gap is not None]
        if bars:
            brief["tree"] = f"{_joined(bars)}; quads {_joined(bar_rows)}"
    return out, briefs


def _joined(gaps: list[float]) -> str:
    """
    The gap between a stroke and the one under it, in words: apart, meeting, overlapping.
    """
    if not gaps:
        return "not read"
    said = []
    for gap, count in Counter(round(gap, 2) for gap in gaps).most_common():
        if gap > 0:
            said.append(f"apart by {gap:g} px x{count}")
        elif gap == 0:
            said.append(f"meet x{count}")
        else:
            said.append(f"overlap by {-gap:g} px x{count}")
    return ", ".join(said)


SUMMARY = (
    ("hint", "space, baked-adv yardstick", 28),
    ("ink", "space, ink to ink", 26),
    ("steps", "step==baked", 13),
    ("dfile", "|step-file| mean/max", 21),
    ("wide", "span drawn-laid mean/max", 25),
    ("touch", "box gap <0/=0/all", 19),
    ("width", "iiii.. drawn/calc", 18),
    ("code", "code steps / row drift", 23),
    ("pitch", "ruler px/char D/laid/C, fit", 30),
    ("drift", "40th D-C", 10),
    ("frac", "frac pens", 10),
    ("resampled", "resampled", 10),
    ("flag", "no_snap set", 12),
)
"""
The summary table's columns: key in ``measure``'s brief, heading, width.

Spaces are those of the first paragraph, in pixels. ``step==baked`` counts the
letter-to-letter steps equal to the advance ImGui has for the letter in the font
it was drawn from; ``span drawn-laid`` is a rigid span's first-pen-to-last-pen
width as drawn less as laid out; ``box gap`` the adjacent letters whose bitmap
boxes overlap, and abut, of all adjacent letters; ``ruler`` the fenced block's
40-character row: pixels a character as drawn, as laid out and in the chrome's
hinted face, then the characters of each that fit the column; ``40th`` how far
the 40th character of the line set in D over C is from C's, on a monospace page.
"""

LEADING_SUMMARY = (
    ("steps", "baseline steps drawn, px", 28),
    ("loose", "laid out by fontlib_metrics", 30),
    ("fit", f"lines/{PANE_PX:g}px", 13),
    ("file", "collide, file box: pairs, most", 32),
    ("quad", "quads: pairs, most rows", 25),
    ("tree", "│ over │", 40),
)
"""
The leading table's columns: key in ``measure_leading``'s brief, heading, width.

A collision is line n+1 with ink above the foot of line n: the pairs of lines
where it happens of all pairs, and the most overlap (negative: clear air). In the
code part it is counted with the listing's rules left out. ``│ over │`` is the
gap between one rule and the one under it, by the file's boxes and by the quads.
"""


def summary(rows: list[tuple[str, float, str, dict[str, str]]]) -> list[str]:
    head = f"{'face':<7}{'px':>3}  {'':<2}" + "".join(f"{title:<{w}}" for _, title, w in SUMMARY)
    out = [head, "-" * len(head)]
    for face, size, name, brief in rows:
        cells = "".join(f"{brief.get(key, '-'):<{w}}" for key, _, w in SUMMARY)
        out.append(f"{face:<7}{size:>3g}  {name:<2}{cells}")
    return out


def leading_summary(rows: list[tuple[str, float, str, str, dict[str, str]]]) -> list[str]:
    head = f"{'face':<7}{'px':>3}  {'pitch':<6}{'part':<10}" + "".join(
        f"{title:<{w}}" for _, title, w in LEADING_SUMMARY
    )
    out = [head, "-" * len(head)]
    for face, size, pitch, part, brief in rows:
        cells = "".join(f"{brief.get(key, '-'):<{w}}" for key, _, w in LEADING_SUMMARY)
        out.append(f"{face:<7}{size:>3g}  {pitch:<6}{part:<10}{cells}")
    return out


def advances_table(fonts: Any, unhinted: Any) -> list[str]:
    """
    File advance against ImGui's, for a few glyphs of each face at each page's size.
    """
    from advtext import BlockKind, fontlib_metrics, px_from_sp

    from pptmstr.ui import typeset

    out = [
        "advance of one glyph, px: file / ImGui baked, hinted faces (get_char_advance) / "
        "pixel_metrics / ImGui baked, the faces loaded with hinting off"
    ]
    for face, size in PAGES:
        key = theme_for(face, size).style(BlockKind.PARAGRAPH).font.key
        file, seam = fontlib_metrics(key), typeset.pixel_metrics(key)
        baked = fonts(file.face).get_font_baked(size)
        plain = unhinted(file.face).get_font_baked(size)
        cells = []
        for char in "i l m W a e 0 space".split():
            code = 32 if char == "space" else ord(char)
            cells.append(
                f"{char} {px_from_sp(file.advance(code)):.2f}/{baked.get_char_advance(code):g}"
                f"/{px_from_sp(seam.advance(code)):g}/{plain.get_char_advance(code):.2f}"
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


def measure_native(seen: list[tuple[str, float, float, Sent]]) -> tuple[list[str], dict[str, Any]]:
    """
    Column C of a leading page: what ImGui's own pitch comes to, from its item sizes.

    ``seen`` is, for each part, its name, the height of the item ImGui made of it,
    ``get_text_line_height`` in its font, and a glyph the first typeset column sent
    in that font. ImGui's breaks are not read, so a collision is given for the
    part's characters at large: its deepest bitmap against its tallest, one line
    apart. That is exact where every line has both, which is the second part.
    """
    out = ["C  (imgui.text_wrapped and one multi-line text_unformatted: ImGui's own pitch)"]
    briefs: dict[str, dict[str, str]] = {}
    texts = {"prose": PROSE, "extremes": EXTREMES, "code": "".join(TREE_ROWS)}
    for part, height, pitch, sent in seen:
        baked = sent.font.get_font_baked(sent.size)
        boxes = {c: baked.find_glyph(ord(c)) for c in set(texts[part]) if c != " "}
        deep = max(boxes, key=lambda c: float(boxes[c].y1))
        tall = min(boxes, key=lambda c: float(boxes[c].y0))
        shared = float(boxes[deep].y1) - (pitch + float(boxes[tall].y0))
        fit = math.floor(PANE_PX / pitch)
        out.append(
            f"  {part}: item {height:g} px tall, baked {sent.size:g} px, line height {pitch:g} px: "
            f"{height / pitch:g} lines;  lines in {PANE_PX:g} px: {fit}"
        )
        out.append(
            f"    deepest bitmap {deep!r} ends at row {float(boxes[deep].y1):g} of its line, "
            f"tallest {tall!r} starts at row {float(boxes[tall].y0):g}: one under the other "
            f"they share {shared:+g} rows (+ is overlap)"
        )
        brief = briefs[part] = {
            "steps": f"{pitch:g} (ImGui's line height)",
            "fit": f"{fit}",
            "quad": f"bitmaps {shared:+g}",
        }
        if "│" in boxes:
            bar = boxes["│"]
            gap = pitch + float(bar.y0) - float(bar.y1)
            out.append(
                f"    │ over │: its bitmap covers rows {float(bar.y0):g}..{float(bar.y1):g} of "
                f"a {pitch:g} px line: {_joined([gap])}"
            )
            brief["tree"] = f"bitmaps {_joined([gap])}"
    return out, briefs


def run(out_dir: Path, stay: bool, settle: int) -> int:
    """
    Open the window, show each page, measure and grab it, and write the files.
    """
    from advtext import Face, draw, fontlib_metrics
    from advtext.ui.fonts import TEXT_FACES, load_faces
    from imgui_bundle import hello_imgui, imgui, immapp
    from OpenGL import GL
    from screenshot import write_png

    from pptmstr import theme as ui_theme
    from pptmstr.ui import typeset

    no_snap = int(imgui.ImDrawListFlags_.text_no_pixel_snap)
    state: dict[str, Any] = {"page": 0, "shown": 0, "capturing": True, "built": None}
    state["resize"] = RESIZE_FRAMES
    report: list[str] = []
    table: list[tuple[str, float, str, dict[str, str]]] = []
    leading_table: list[tuple[str, float, str, str, dict[str, str]]] = []
    facts: dict[str, str] = {}

    def load_fonts() -> None:
        # The runner's default first, so the window's own labels keep it; then the
        # seam's load, which is what `pixel_metrics` reads its ImFonts from; then
        # the same files again with hinting off, for column D.
        hello_imgui.imgui_default_settings.load_default_font_with_font_awesome_icons()
        if typeset.METRICS != "hinted":
            raise SystemExit(
                f"typeset.METRICS is {typeset.METRICS!r}: columns A, B and C are captioned "
                "as hinted and would not be"
            )
        if not typeset.load_fonts(loader=typeset.hello_loader()):
            raise SystemExit(f"the faces did not load: {typeset.unavailable_reason()}")
        params = hello_imgui.FontLoadingParams()
        params.inside_assets = False
        params.font_config.font_loader_flags = NO_HINTING
        plain = load_faces(
            TEXT_FACES,
            loader=lambda path: hello_imgui.load_font(str(path), typeset.BODY_PX, params),
        )
        if plain.failed:
            raise SystemExit(f"faces did not load unhinted: {[f.name for f in plain.failed]}")
        state["unhinted"] = plain

    def built() -> tuple[list[Any], Column | None]:
        if state["built"] is None or state["built"][0] != state["page"]:
            kind, face, size = SHEETS[state["page"]]
            hinted, plain = typeset._fonts(), state["unhinted"]
            if kind == "leading":
                made: list[Any] = [
                    pitched(face, size, pitch, typeset.pixel_metrics, hinted)
                    for pitch in LEADING_PITCHES
                ]
                state["built"] = (state["page"], made, None)
            else:
                theme = theme_for(face, size)
                made = [
                    lay_out("A", CAPTIONS["A"], theme, fontlib_metrics, hinted),
                    lay_out("B", CAPTIONS["B"], theme, typeset.pixel_metrics, hinted),
                    lay_out("D", CAPTIONS["D"], theme, fontlib_metrics, plain),
                ]
                stack = None
                if face == "mono":
                    fence = "```\n" + STACK_ROW + "\n```"
                    stack = lay_out("D", "", flush_code(theme), fontlib_metrics, plain, fence)
                state["built"] = (state["page"], made, stack)
        return state["built"][1], state["built"][2]

    def typeset_column(column: Column, origin: Any, colours: Any, measuring: bool) -> None:
        recorder = Recorder(imgui.get_window_draw_list(), no_snap, quads=measuring)
        draw(
            recorder,
            column.commands,
            origin,
            fonts=column.fonts,
            palette=colours,
            px_per_inch=column.px_per_inch,
            metrics=column.resolver,
        )
        column.sent = recorder.sent

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
        for text in (PROSE, RHYTHM, DIGITS, INLINE_PLAIN):
            imgui.text_wrapped(text)
            imgui.dummy(imgui.ImVec2(0.0, round(prose.size * 0.5)))
        imgui.pop_font()
        imgui.push_font(code.font, code.size)
        for row in CODE_ROWS:
            imgui.text_unformatted(row)
        imgui.pop_font()
        imgui.pop_style_color()

    def native_leading(reference: Pitched, ink: Any) -> list[tuple[str, float, float, Sent]]:
        """
        Column C of a leading page: each part as one ImGui item, in the font and
        size the first typeset column was sent for it. Returns what ``measure_native``
        reads.
        """
        seen = []
        imgui.push_style_color(imgui.Col_.text, ink)
        imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(0.0, 0.0))
        for (part, column), (_, markdown) in zip(reference.parts, LEADING_PARTS, strict=True):
            first = column.sent[0]
            imgui.push_font(first.font, first.size)
            if part == "code":
                imgui.text_unformatted("\n".join(TREE_ROWS))
            else:
                imgui.text_wrapped(markdown)
            height = float(imgui.get_item_rect_size().y)
            seen.append((part, height, float(imgui.get_text_line_height()), first))
            imgui.pop_font()
            imgui.dummy(imgui.ImVec2(0.0, PART_GAP_PX))
        imgui.pop_style_var()
        imgui.pop_style_color()
        return seen

    def metrics_page(face: str, size: float, name: str, left: float, measuring: bool) -> None:
        columns, stack = built()
        palette = ui_theme.current()
        colours = typeset.palette(palette.text)
        chrome = typeset._fonts()
        pitch = COLUMN_PX + GUTTER_PX
        if stack is not None:
            y = math.floor(imgui.get_cursor_screen_pos().y) + 4.0
            imgui.set_cursor_screen_pos(imgui.ImVec2(left, y))
            imgui.text_unformatted(
                "one line of code from one x: D (upper), and the hinted face through "
                "ImDrawList.add_text, which is C's raster (lower)"
            )
            typeset_column(stack, imgui.ImVec2(left, y + 22.0), colours, measuring)
            row = stack.sent[0]
            lower = row.y + row.size + 1.0
            state["stack"] = native_line(
                imgui.get_window_draw_list(),
                imgui,
                chrome(Face.CODE),
                row.size,
                left,
                lower,
                colours.ink,
                STACK_ROW,
            )
            imgui.set_cursor_screen_pos(imgui.ImVec2(left, lower + row.size + 6.0))
        top = math.floor(imgui.get_cursor_screen_pos().y) + 4.0
        for number, column in enumerate(columns):
            x = left + number * pitch
            imgui.set_cursor_screen_pos(imgui.ImVec2(x, top))
            imgui.text_unformatted(f"{column.name}  {column.caption}")
            origin = imgui.ImVec2(x, top + 24.0)
            imgui.set_cursor_screen_pos(origin)
            typeset_column(column, origin, colours, measuring)
            imgui.dummy(imgui.ImVec2(COLUMN_PX, column.height_px))
        x = left + len(columns) * pitch
        imgui.set_cursor_screen_pos(imgui.ImVec2(x, top))
        imgui.text_unformatted(f"C  {CAPTIONS['C']}")
        imgui.set_cursor_screen_pos(imgui.ImVec2(x, top + 24.0))
        imgui.begin_child("##native", imgui.ImVec2(COLUMN_PX, 0.0))
        native(columns[0].sent, palette.text.vec4)
        imgui.end_child()
        if not measuring:
            return
        origins = ", ".join(f"{c.name} {left + n * pitch:g}" for n, c in enumerate(columns))
        report.append("")
        report.append(f"=== {name}, {size:g} px ===  (text origin x: {origins})")
        for column in columns:
            lines, brief = measure(column, imgui, chrome)
            report.append(f"{column.name}  ({column.caption})")
            report.extend(lines)
            if column.name == "D":
                pairs = list(zip(column.sent, columns[0].sent, strict=False))
                shift = columns.index(column) * pitch
                off_x = sum(1 for mine, other in pairs if mine.x - shift != other.x)
                rows = Counter(round(mine.y - other.y, 2) for mine, other in pairs)
                report.append(
                    f"  against column A's add_text calls, one for one ({len(pairs)}): at another "
                    f"x from its column's origin: {off_x};  y less A's, px:count  "
                    + "  ".join(f"{dy:g}:{count}" for dy, count in sorted(rows.items()))
                )
            if stack is not None and column.name == "D":
                more, drift = measure_stack(stack, state["stack"], chrome)
                report.extend(more)
                brief.update(drift)
            table.append((face, size, column.name, brief))
        state["bottom"] = top + 24.0 + max(c.height_px for c in columns) + MARGIN_PX

    def leading_page(face: str, size: float, name: str, left: float, measuring: bool) -> None:
        stacks, _ = built()
        palette = ui_theme.current()
        colours = typeset.palette(palette.text)
        pitch = COLUMN_PX + GUTTER_PX
        top = math.floor(imgui.get_cursor_screen_pos().y) + 4.0
        bottom = top
        for number, stack in enumerate(stacks):
            x = left + number * pitch
            imgui.set_cursor_screen_pos(imgui.ImVec2(x, top))
            imgui.text_unformatted(f"{stack.name}   {stack.caption}")
            y = top + 24.0
            for _, column in stack.parts:
                origin = imgui.ImVec2(x, y)
                imgui.set_cursor_screen_pos(origin)
                typeset_column(column, origin, colours, measuring)
                imgui.dummy(imgui.ImVec2(COLUMN_PX, column.height_px))
                y += math.ceil(column.height_px) + PART_GAP_PX
            bottom = max(bottom, y)
        x = left + len(stacks) * pitch
        imgui.set_cursor_screen_pos(imgui.ImVec2(x, top))
        imgui.text_unformatted(f"C   {CAPTIONS['C leading']}")
        imgui.set_cursor_screen_pos(imgui.ImVec2(x, top + 24.0))
        imgui.begin_child("##native-leading", imgui.ImVec2(COLUMN_PX, 0.0))
        seen = native_leading(stacks[0], palette.text.vec4)
        imgui.end_child()
        if not measuring:
            return
        report.append("")
        report.append(
            f"=== leading: {name}, {size:g} px ===  (column B's pipeline at each pitch; "
            f"parts {PART_GAP_PX:g} px apart; column {COLUMN_PX:g} px)"
        )
        for stack in stacks:
            lines, briefs = measure_leading(stack)
            report.extend(lines)
            for part, brief in briefs.items():
                leading_table.append((face, size, stack.name, part, brief))
        lines, briefs = measure_native(seen)
        report.extend(lines)
        for part, brief in briefs.items():
            leading_table.append((face, size, "C", part, brief))
        state["bottom"] = bottom + MARGIN_PX

    def gui() -> None:
        kind, face, size = SHEETS[state["page"]]
        if state["resize"] and imgui.get_io().display_size.x < WINDOW[0]:
            if state["resize"] == RESIZE_FRAMES:
                hello_imgui.change_window_size(WINDOW)
            state["resize"] -= 1
            state["shown"] = -1
        measuring = state["capturing"] and state["shown"] == settle
        palette = ui_theme.current()
        viewport = imgui.get_main_viewport()
        imgui.set_next_window_pos(viewport.pos)
        imgui.set_next_window_size(viewport.size)
        imgui.push_style_color(imgui.Col_.window_bg, palette.panel.vec4)
        imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(MARGIN_PX, MARGIN_PX))
        flags = imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_move.value
        imgui.begin("compare_metrics", None, flags)
        name = "Roboto prose, Inconsolata code" if face == "roboto" else "Inconsolata, every role"
        said = (
            "leading: column B's pipeline (advtext draw, typeset.pixel_metrics, hinted faces) "
            "at three line pitches, then ImGui's own;  column A is on the metrics pages"
            if kind == "leading"
            else "ragged right, one space width"
        )
        imgui.text_unformatted(
            f"page {state['page'] + 1}/{len(SHEETS)}   {name}   {size:g} px pixel height   "
            f"column {COLUMN_PX:g} px   {said}"
            + ("" if state["capturing"] else "   [Left/Right: page]")
        )
        left = math.floor(imgui.get_window_pos().x) + MARGIN_PX
        page = leading_page if kind == "leading" else metrics_page
        page(face, size, name, left, measuring)
        if not state["capturing"]:
            step = imgui.is_key_pressed(imgui.Key.right_arrow) - imgui.is_key_pressed(
                imgui.Key.left_arrow
            )
            state["page"] = (state["page"] + step) % len(SHEETS)
        imgui.end()
        imgui.pop_style_var()
        imgui.pop_style_color()

    def before_swap() -> None:
        if not (state["capturing"] and state["shown"] == settle):
            return
        width, height, rows = grab(GL, imgui)
        io = imgui.get_io()
        if not facts:
            facts["renderer"] = GL.glGetString(GL.GL_RENDERER).decode()
            facts["loader"] = f"{io.fonts.font_loader_name}, flags {io.fonts.font_loader_flags}"
            scale = io.display_framebuffer_scale
            facts["framebuffer"] = f"{width}x{height}, framebuffer scale {scale.x:g}x{scale.y:g}"
            if io.display_size.x < WINDOW[0]:
                facts[
                    "framebuffer"
                ] += f" (NARROWER than the {WINDOW[0]} px the columns need: the last is cut)"
            facts["font scale"] = (
                f"style.font_scale_dpi {imgui.get_style().font_scale_dpi:g}, "
                f"style.font_scale_main {imgui.get_style().font_scale_main:g}"
            )
            facts["atlas filter"] = _atlas_filter(GL, io)
            facts["typeset.METRICS"] = typeset.METRICS
            facts["column D's faces"] = (
                f"{len(state['unhinted'].fonts)} roles loaded again with "
                f"ImFontConfig.font_loader_flags = {NO_HINTING}"
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = _stem(*SHEETS[state["page"]])
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
        if state["page"] + 1 < len(SHEETS):
            state["page"] += 1
            return
        state["capturing"] = False
        state["page"] = SHEETS.index(("metrics", "roboto", 16.0))
        text = ["compare_metrics: " + ";  ".join(f"{k}: {v}" for k, v in facts.items()), ""]
        text += advances_table(typeset._fonts(), state["unhinted"])
        text += ["", *summary(table), "", *leading_summary(leading_table), *report]
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
