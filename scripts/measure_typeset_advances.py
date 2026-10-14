#!/usr/bin/env python3
"""
Where imscribe's ``TypesetView`` puts glyphs in a pptmstr-shaped window, as numbers.

``HANDOFF-advtext.md`` (A2) states six requirements of text on screen, R1 to R6.
This prints what each comes to when the text is set by the imtypeset library
through ``imscribe.TypesetView``, for Inconsolata Medium and Roboto Regular at 14,
16, 18 and 20 px. It measures and decides nothing: no verdict is printed, and
nothing here says how anything looks.

"Size" names two lengths. Dear ImGui sizes a font by its pixel HEIGHT (ascent
plus descent); imscribe sizes a face by its EM. ``--px`` pages are the first and
``--em`` pages the second: Inconsolata's advance is half an em, so at an em of
16 the file's advance is a whole 8 px where at a pixel height of 16 it is
7.626.

## The window

Fonts are loaded as ``pptmstr/theme.py`` loads them, the lines copied here
rather than imported: Inconsolata Medium with Font Awesome 6 merged as
``Fonts[0]`` through hello_imgui, the vendored Inconsolata Bold, then
``imscribe.fontlib.register_font(Face.CODE_BOLD, <the vendored bold>)`` and
``imscribe.typesetter.load_fonts()`` last in ``load_additional_fonts``.

A page is one face table at one size. The view is made to set text at that size
by its theme, and by nothing else:

    Theme.at_pixel_height(px, faces=Faces.mono() | Faces(), align=Align.LEFT,
                          spacing=Spacing.FRENCH)

An ``--em`` page asks the same constructor for the pixel height that em comes
to in the page's running face (``em / em_size_for_pixel_height(face, 1)``), so
the two kinds of page differ in the size and in nothing else.

Ragged right and one space width, so a word space that varies does so because of
how it was drawn and not because a line was justified. On a Roboto page fenced
code is Inconsolata at the paragraph's em, which is a smaller pixel height than
the page's; each page says the size its code was drawn at.

Each page draws two views, one under the other: prose (a paragraph, a rhythm
line, a line of digits) 400 px wide, and one fenced block 1000 px wide whose
last two rows are a 40-character ruler and a 74-character line of code.
Under them the same two rows go through ``ImDrawList.add_text`` in the chrome
font (``Fonts[0]``), from the x of the typeset row's first pen: at the page's
pixel height on a ``--px`` page, and on an ``--em N`` page at a pixel height of
N, which is the size a host's chrome is when it says "N".

## What is read, and from what

Nothing is read from pixels.

    drawn   the quads the view's ``draw`` added to the window's draw list
            (``vtx_buffer``, vertices whose uv is not the atlas's white pixel),
            taken in order against the characters of ``view.texts`` that have
            ink. A quad is attributed to an atlas font and a baked size by its
            uv rectangle being that ``ImFontGlyph``'s; the pen is the quad's
            left edge less the glyph's ``x0``.
    laid    ``view.span_rects(block, i, i + 1)``: the library's rectangle for
            one character, which is its layout and is not pixel-snapped.
            ``view.set_lines()`` says which line a character is on.
    baked   ``ImFontBaked.get_char_advance`` of (a) the library's own atlas
            entry for the role, at the size the quads were attributed to, and
            (b) the chrome font at the page's pixel height.

The library's atlas entry is found with ``typesetter.module().font_atlas_index``
and ``imscribe.description.FACE_ROLES``. Neither is a name in ``imscribe.__all__``;
it is how imscribe's own demo finds the font (``imscribe/ui/app.py``), and
pptmstr's request P14 asks for a supported name.

## Running it

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/measure_typeset_advances.py
    ... measure_typeset_advances.py --faces mono --em 14,16,18,20 --px 16

With no option the pages are both faces at the four pixel heights and no em
page. The header says which ``imscribe`` was imported and, where its directory
is a git checkout, the commit.

The library is found as the application finds it (``pptmstr.pairing``): an
installed imtypeset, or this checkout's built pair where none is installed or
``PPTMSTR_TYPESET_DEPS=1`` asks; the header says which.

Exit status 1, with the reason, when the library is not
drawing; 2 when a page could not be read whole.

## ``--pptmstr``: pptmstr's own fonts and role themes

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/measure_typeset_advances.py --pptmstr
    ... --pptmstr --bodies 16 --families roboto

Fonts are loaded by ``pptmstr.theme.load_fonts`` itself, and every page's
documents are themed by ``roles.configure`` and ``themes.document_theme``: a
family (prose in Roboto, or in the code face), a body size, and one step for
the code-block, literal and inline-code roles together, from -2 to +4, so code
at every pixel height from 12 to 24 over the default four bodies. Each page
reads a document as pptmstr reads one (a paragraph holding a code span, the
ruler and the long row as prose, and a fence of the code rows), a host's own
``MarkdownCode`` in ``themes.literal_style`` in a ``gate.literal_view``, and
another such block of rows holding characters the code face lacks. It prints,
per page, R2, R4, R5 and R6 of the fence and of the host block against the
chrome font at the role's pixel height, each glyph against its rectangle, a
hit test a quarter and three quarters into every glyph, the drawn pitch, the
tab row's columns, where a selection's fill is against the pens, the code
span's advance and raster, and how far the wide block's ink, lines and
``content_width`` reach.
"""

from __future__ import annotations

import argparse
import math
import os
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "pptmstr" / "assets"
CHROME_PX = 16.0
"""
The size ``pptmstr/theme.py`` loads the chrome faces at (``_FONT_SIZE``).
"""

SIZES = (14.0, 16.0, 18.0, 20.0)
FACES = ("mono", "roboto")
WINDOW = (1500, 950)
PROSE_PX = 400.0
CODE_PX = 1000.0
SETTLE_FRAMES = 4
"""
Frames a page is drawn before it is read: the atlas bakes a glyph on the first
frame that draws it, and may move the ones it has.
"""

PROSE = (
    "To Wave at AVATAR Wavy Types we fix five affine offices: a Tyrant Yawns, "
    "Vows Await, and LT. P. Avery fills the final waffle. Typography of ordinary "
    "running text depends on the rhythm of its word spaces, which a reader takes "
    "in without ever looking at one of them directly."
)
RHYTHM = "iiii llll mmmm WWWW"
DIGITS = "0123456789 1111 0000 8888 1,234,567.89 (2026-10-08)"
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
MARKS = (1, 10, 20, 30, 40, 74)
"""
The characters of a code row whose pens are put beside the chrome's, counted from 1.
"""

EPS = 1e-4


@dataclass(frozen=True, slots=True)
class Page:
    """
    One face table at one size, and which of the two lengths the size is.

    ``beside`` is the pixel height the chrome font is drawn at next to the page.
    """

    face: str
    unit: str
    size: float

    @property
    def beside(self) -> float:
        return self.size

    @property
    def label(self) -> str:
        return f"{self.face} {self.unit} {self.size:g}"


@dataclass(frozen=True, slots=True)
class Glyph:
    """
    One character with ink: where the layout has it and the quad that drew it.

    ``laid`` and ``laid_right`` are screen x of the library's rectangle for the
    character. ``pen`` is the quad's left edge less the bitmap's ``x0``;
    ``advance`` the baked advance of the atlas entry the quad came from.
    ``pen_y`` is the quad's top less the bitmap's ``y0``, the same for every
    glyph of a line.
    """

    char: str
    block: int
    index: int
    line: int
    laid: float
    laid_right: float
    quad: tuple[float, float, float, float]
    bitmap: tuple[float, float]
    pen_y: float
    advance: float
    role: str
    baked_px: float

    @property
    def pen(self) -> float:
        return self.quad[0] - self.bitmap[0]


class Unread(Exception):
    """
    A view's quads could not be paired with its characters; the message says how.
    """


def theme_for(page: Page) -> Any:
    from imscribe import Align, Faces, Spacing, Theme, em_size_for_pixel_height

    faces = Faces.mono() if page.face == "mono" else Faces()
    px = page.size
    if page.unit == "em":
        px = page.size / em_size_for_pixel_height(faces.regular, 1.0)
    return Theme.at_pixel_height(px, faces=faces, align=Align.LEFT, spacing=Spacing.FRENCH)


def expected_sizes(theme: Any) -> dict[str, float]:
    """
    The pixel height (ascent plus descent) of the paragraph's and the code block's font.
    """
    from imscribe import BlockKind, px_from_sp
    from imscribe.style import fontlib_metrics

    out = {}
    for name, kind in (("paragraph", BlockKind.PARAGRAPH), ("code", BlockKind.CODE)):
        metrics = fontlib_metrics(theme.style(kind).font.key)
        out[name] = float(px_from_sp(round(metrics.ascent + metrics.descent)))
    return out


def glyph_quads(draw_list: Any, start: int, stop: int, white: Any) -> list[tuple[float, ...]]:
    """
    The textured quads among vertices ``[start, stop)``: (left, top, right, bottom, u0, v0, u1, v1).

    Fills and rules carry the atlas's white pixel as their uv and are left out.
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


def held_bake(font: Any, size: float) -> Any:
    """
    The baked size ``font`` has at ``size`` px, or ``None``: none made, none marked used.

    ``ImFont.get_font_baked`` makes the size it is asked for and marks it used
    for the frame. Asked of every candidate size on every frame read, it keeps
    each of them from ever being discarded, and this window then cannot show
    what the application's shows when ImGui discards a size and moves the
    others (``pptmstr.theme.keep_baked_sizes``). The closest-match lookup does
    neither; asked for just over a size it answers that size where it exists.
    """
    from imgui_bundle import imgui

    atlas = imgui.get_io().fonts
    # A density no size has, so that none is preferred for its density.
    baked = imgui.internal.im_font_atlas_baked_get_closest_match(atlas, font, size + 0.01, 0.0)
    return baked if baked is not None and abs(float(baked.size) - size) < 0.005 else None


def read_view(
    view: Any, draw_list: Any, start: int, stop: int, white: Any, candidates: list[Any]
) -> list[list[Glyph]]:
    """
    The view's inked characters, one list a block, each paired with its quad.

    ``candidates`` are ``(role, ImFont, sizes)``: a block's quads are attributed
    to the font and baked size whose glyphs' uv rectangles they all carry.
    Raises ``Unread`` when the counts differ or no candidate accounts for every
    quad of a block.
    """
    origin = view.origin
    if origin is None:
        raise Unread("the view has no origin: it was not drawn")
    quads = glyph_quads(draw_list, start, stop, white)
    inked = [
        (block, index, char)
        for block, text in enumerate(view.texts)
        for index, char in enumerate(text)
        if not char.isspace()
    ]
    if len(quads) != len(inked):
        raise Unread(f"{len(quads)} glyph quads for {len(inked)} characters with ink")
    lines = view.set_lines()
    blocks: list[list[Glyph]] = []
    at = 0
    for block, _ in enumerate(view.texts):
        mine = [(index, char) for b, index, char in inked if b == block]
        took = quads[at : at + len(mine)]
        at += len(mine)
        best: tuple[int, str, Any] | None = None
        for role, font, sizes in candidates:
            for size in sizes:
                baked = held_bake(font, float(size))
                if baked is None:
                    continue
                hits = 0
                for (_, char), quad in zip(mine, took, strict=True):
                    if not baked.is_glyph_loaded(ord(char)):
                        continue
                    glyph = baked.find_glyph(ord(char))
                    uv = (glyph.u0, glyph.v0, glyph.u1, glyph.v1)
                    hits += all(abs(a - b) < 1e-7 for a, b in zip(uv, quad[4:], strict=True))
                if best is None or hits > best[0]:
                    best = (hits, role, baked)
        if best is None or best[0] != len(mine):
            hits = 0 if best is None else best[0]
            raise Unread(
                f"block {block}: no atlas entry among the library's accounts for every quad "
                f"(best {hits} of {len(mine)})"
            )
        _, role, baked = best
        placed = []
        for (index, char), quad in zip(mine, took, strict=True):
            rects = view.span_rects(block, index, index + 1)
            if len(rects) != 1:
                raise Unread(f"block {block} character {index}: {len(rects)} rectangles")
            line = next(
                (ln.line for ln in lines if ln.block == block and ln.start <= index < ln.end), -1
            )
            glyph = baked.find_glyph(ord(char))
            placed.append(
                Glyph(
                    char=char,
                    block=block,
                    index=index,
                    line=line,
                    laid=origin[0] + rects[0][0],
                    laid_right=origin[0] + rects[0][2],
                    quad=(quad[0], quad[1], quad[2], quad[3]),
                    bitmap=(float(glyph.x0), float(glyph.x1)),
                    pen_y=quad[1] - float(glyph.y0),
                    advance=float(baked.get_char_advance(ord(char))),
                    role=role,
                    baked_px=float(baked.size),
                )
            )
        blocks.append(placed)
    return blocks


def runs(glyphs: list[Glyph]) -> list[list[Glyph]]:
    """
    Maximal runs of adjacent inked characters on one line: nothing between them is a space.
    """
    out: list[list[Glyph]] = []
    for glyph in glyphs:
        last = out[-1][-1] if out else None
        if last is not None and last.line == glyph.line and last.index + 1 == glyph.index:
            out[-1].append(glyph)
        else:
            out.append([glyph])
    return out


def rows_of(glyphs: list[Glyph]) -> list[list[Glyph]]:
    """
    A block's glyphs by line, in order.
    """
    out: dict[int, list[Glyph]] = {}
    for glyph in glyphs:
        out.setdefault(glyph.line, []).append(glyph)
    return [out[line] for line in sorted(out)]


def chrome_line(
    draw_list: Any, imgui: Any, font: Any, px: float, x: float, y: float, text: str, white: Any
) -> tuple[list[float], list[float], int, int]:
    """
    One string through ``ImDrawList.add_text`` in ``font`` at ``px``.

    Answers each character's pen (``x`` plus the baked advances before it), each
    inked character's quad left edge, and two counts: the quads emitted, and
    how many have their left edge at that pen plus the bitmap's ``x0``. The pens
    are what ImGui did when the two counts are equal.
    """
    baked = font.get_font_baked(px)
    start = draw_list.vtx_buffer.size()
    draw_list.add_text(font, px, imgui.ImVec2(x, y), 0xFFE6E6E6, text)
    quads = glyph_quads(draw_list, start, draw_list.vtx_buffer.size(), white)
    pens, at = [], x
    for char in text:
        pens.append(at)
        at += float(baked.get_char_advance(ord(char)))
    wanted = [
        pen + float(baked.find_glyph(ord(char)).x0)
        for pen, char in zip(pens, text, strict=True)
        if not char.isspace()
    ]
    lefts = [quad[0] for quad in quads]
    agree = 0
    if len(lefts) == len(wanted):
        agree = sum(1 for a, b in zip(lefts, wanted, strict=True) if abs(a - b) < EPS)
    return pens, lefts, len(quads), agree


def _spread(values: list[float]) -> str:
    if not values:
        return "n=0"
    deviation = statistics.pstdev(values) if len(values) > 1 else 0.0
    return (
        f"n={len(values)} min={min(values):+.3f} max={max(values):+.3f} "
        f"mean={statistics.fmean(values):+.3f} sd={deviation:.3f}"
    )


def _tally(values: list[float]) -> str:
    if not values:
        return "none"
    counts = Counter(round(value, 3) for value in values)
    return ", ".join(f"{value:g} x{count}" for value, count in sorted(counts.items()))


def _in_order(values: list[float]) -> str:
    """
    Values in the order given, each run of equal ones as ``value xN``.
    """
    out: list[tuple[float, int]] = []
    for value in values:
        if out and out[-1][0] == value:
            out[-1] = (value, out[-1][1] + 1)
        else:
            out.append((value, 1))
    return ", ".join(f"{value:g} x{count}" for value, count in out)


def _said(run: list[Glyph]) -> str:
    return "".join(glyph.char for glyph in run)


def measure(
    px: float,
    prose: list[list[Glyph]],
    code: list[Glyph],
    chrome: Any,
    natives: dict[str, tuple[list[float], list[float], int, int]],
    controls: dict[str, tuple[list[float], list[float], int, int]],
    sizes: dict[str, float],
    code_lines: list[Any],
) -> tuple[list[str], dict[str, str]]:
    """
    A page's numbers as report lines, and the few of them the table carries.

    ``px`` is the pixel height the chrome font is drawn at beside the page.
    ``natives`` maps a code row's text to ``chrome_line``'s answer for it in the
    chrome font, ``controls`` to its answer in the library's own code entry.
    ``code_lines`` are the fenced block's ``SetLine``s.
    """
    out: list[str] = []
    brief: dict[str, str] = {}
    prose_glyphs = [glyph for block in prose for glyph in block]
    everything = prose_glyphs + code
    chrome_baked = chrome.get_font_baked(px)

    def chrome_advance(glyph: Glyph) -> float:
        return float(chrome_baked.get_char_advance(ord(glyph.char)))

    first_prose, first_code = prose_glyphs[0], code[0]
    out.append(
        f"  drawn from: prose, the library's {first_prose.role} entry baked at "
        f"{first_prose.baked_px:g} px (theme asks {sizes['paragraph']:.3f}); code, its "
        f"{first_code.role} entry baked at {first_code.baked_px:g} px (theme asks "
        f"{sizes['code']:.3f})"
    )
    zero = next(glyph for glyph in code if glyph.char == "0")
    out.append(
        f"  advance of '0' in the code block, px: laid out {zero.laid_right - zero.laid:.3f};  "
        f"the library's entry {zero.advance:g};  the chrome font at {px:g} px "
        f"{chrome_advance(zero):g}"
    )

    # Size: the fenced block's line pitch, and one glyph's bitmap beside the chrome's.
    pitches = [b.top - a.top for a, b in pairwise(code_lines)]
    heights = [line.bottom - line.top for line in code_lines]
    drawn_pitches = [b[0].pen_y - a[0].pen_y for a, b in pairwise(rows_of(code))]
    out.append(
        f"  size: fenced lines {len(code_lines)};  top to top as laid out, px: {_tally(pitches)};  "
        f"as drawn (a row's glyphs' pen y to the next row's): {_tally(drawn_pitches)};  line "
        f"height as laid out: {_tally(heights)};  the chrome font's line at {px:g} px: "
        f"{chrome_baked.size:g}"
    )
    theirs = chrome_baked.find_glyph(ord("0"))
    mine = (zero.quad[2] - zero.quad[0], zero.quad[3] - zero.quad[1])
    chromes = (float(theirs.x1 - theirs.x0), float(theirs.y1 - theirs.y0))
    out.append(
        f"  size: the bitmap of '0', width x height px: typeset {mine[0]:g} x {mine[1]:g} "
        f"(the library's entry baked at {zero.baked_px:g});  the chrome font at {px:g} px "
        f"{chromes[0]:g} x {chromes[1]:g}"
    )
    brief["size"] = (
        f"{'/'.join(f'{v:g}' for v in sorted({round(p, 3) for p in drawn_pitches}))} "
        f"{mine[0]:g}x{mine[1]:g}/{chromes[0]:g}x{chromes[1]:g}"
    )

    # R1: a spaceless run, first pen to last pen, drawn less laid out.
    for name, glyphs in (("prose", prose_glyphs), ("code", code)):
        excess = [
            ((run[-1].pen - run[0].pen) - (run[-1].laid - run[0].laid), run)
            for run in runs(glyphs)
            if len(run) > 1
        ]
        overs = [over for over, _ in excess]
        worst, widest = max(excess, key=lambda pair: abs(pair[0]))
        moved = sum(1 for over in overs if abs(over) >= 1.0)
        out.append(
            f"  R1 {name}: spaceless runs of 2+ glyphs, drawn - laid out, px: {_spread(overs)};  "
            f"off by 1 px or more: {moved};  worst {_said(widest)[:24]!r} {worst:+.3f}"
        )
        brief[f"r1 {name}"] = f"{min(overs):+.2f}..{max(overs):+.2f}"
    rows = rows_of(code)
    by_text = {row_text: row for row_text, row in zip(CODE_ROWS, rows, strict=True)}
    for name, text in (("40", RULER), ("74", LONG)):
        row = by_text[text]
        drawn = row[-1].pen - row[0].pen
        laid = row[-1].laid - row[0].laid
        native = natives[text][0]
        out.append(
            f"  R1 the {name}-character fenced line, first pen to last pen, px: drawn {drawn:g}, "
            f"laid out {laid:.3f} ({drawn - laid:+.3f});  spaceless runs on it: {len(runs(row))};  "
            f"the chrome font's {native[-1] - native[0]:g}"
        )
        brief[f"r1 {name}"] = f"{drawn:g}/{laid:.2f}/{native[-1] - native[0]:g}"
        marks = [k for k in MARKS if k <= len(text)]
        at = {glyph.index - _row_start(row, text): glyph for glyph in row}
        moved_by = [(at[k - 1].pen - row[0].pen) - (at[k - 1].laid - row[0].laid) for k in marks]
        within = [(glyph.pen - row[0].pen) - (glyph.laid - row[0].laid) for glyph in row]
        out.append(
            f"     on it, a glyph's x from the first as drawn less as laid out, px: at characters "
            f"{', '.join(str(k) for k in marks)}: {'  '.join(f'{d:+.2f}' for d in moved_by)};  "
            f"every glyph: min {min(within):+.2f} max {max(within):+.2f}"
        )
        brief[f"r1 in {name}"] = f"{min(within):+.1f}..{max(within):+.1f}"
    ruler_steps = [b.pen - a.pen for a, b in pairwise(by_text[RULER])]
    out.append(f"     the 40-character line's steps in order, px: {_in_order(ruler_steps)}")

    # R2: the step between adjacent glyphs of a run, against three yardsticks.
    for name, glyphs in (("prose", prose_glyphs), ("code", code)):
        steps = [(a, b) for run in runs(glyphs) for a, b in pairwise(run)]
        own = sum(1 for a, b in steps if abs(b.pen - a.pen - a.advance) < EPS)
        whole = sum(1 for a, b in steps if (b.pen - a.pen) == math.floor(b.pen - a.pen))
        against_laid = [(b.pen - a.pen) - (b.laid - a.laid) for a, b in steps]
        line = (
            f"  R2 {name}: steps inside runs {len(steps)};  equal to the library entry's baked "
            f"advance {own};  a whole number of pixels {whole}"
        )
        brief[f"r2a {name}"] = f"{own}/{len(steps)}"
        if all(a.role == "CODE" for a, _ in steps):
            same = sum(1 for a, b in steps if abs(b.pen - a.pen - chrome_advance(a)) < EPS)
            line += f";  equal to the chrome font's baked advance at {px:g} px {same}"
            brief[f"r2b {name}"] = f"{same}/{len(steps)}"
        else:
            line += ";  the chrome font is another face, not compared"
            brief[f"r2b {name}"] = "-"
        out.append(line)
        out.append(
            f"     step drawn - step laid out, px: {_spread(against_laid)};  steps drawn: "
            f"{_tally([b.pen - a.pen for a, b in steps])}"
        )

    # R3: word spaces of the prose view.
    spaces = [
        (a, b)
        for a, b in pairwise(prose_glyphs)
        if a.block == b.block and a.line == b.line and b.index == a.index + 2
    ]
    by_baked = [b.pen - a.pen - a.advance for a, b in spaces]
    as_laid = [b.laid - a.laid_right for a, b in spaces]
    out.append(
        f"  R3 word spaces, pen to pen less the baked advance before, px: {_spread(by_baked)}"
    )
    out.append(f"     distinct: {_tally(by_baked)}")
    out.append(f"     the same spaces as laid out, px: {_spread(as_laid)}")
    deviation = statistics.pstdev(by_baked) if len(by_baked) > 1 else 0.0
    brief["r3"] = f"{min(by_baked):g}..{max(by_baked):g} sd {deviation:.2f}"

    # R4: columns of the fenced block.
    steps_by_column = [
        b.pen - a.pen for row in rows for a, b in pairwise(row) if b.index == a.index + 1
    ]
    columns: dict[int, list[float]] = {}
    for text, row in by_text.items():
        start = _row_start(row, text)
        for glyph in row:
            columns.setdefault(glyph.index - start, []).append(glyph.pen)
    drift = max(max(pens) - min(pens) for pens in columns.values())
    where = max(columns, key=lambda column: max(columns[column]) - min(columns[column]))
    out.append(
        f"  R4 fenced rows {len(rows)};  steps between adjacent columns, px: "
        f"{_tally(steps_by_column)}"
    )
    out.append(
        f"     largest disagreement between rows at one column: {drift:g} px (column {where + 1})"
    )
    brief["r4"] = (
        f"{'/'.join(f'{v:g}' for v in sorted({round(s, 3) for s in steps_by_column}))} "
        f"drift {drift:g}"
    )

    # R5: the same rows through add_text in the chrome font, from the same x.
    for name, text in (("40", RULER), ("74", LONG)):
        placed = {glyph.index - _row_start(by_text[text], text): glyph for glyph in by_text[text]}
        pens, lefts, emitted, agree = natives[text]
        marks = [k for k in MARKS if k <= len(text)]
        deltas = [placed[k - 1].pen - pens[k - 1] for k in marks]
        inked = [glyph for _, glyph in sorted(placed.items())]
        ink = [glyph.quad[0] - left_edge for glyph, left_edge in zip(inked, lefts, strict=True)]
        out.append(
            f"  R5 the {name}-character line, typeset pen - chrome pen at characters "
            f"{', '.join(str(k) for k in marks)}: {'  '.join(f'{d:+g}' for d in deltas)} px"
        )
        out.append(
            f"     chrome: add_text at {px:g} px from x {pens[0]:g}; {agree} of {emitted} quads "
            f"are where the cumulative baked advances put them.  ink's left edge, typeset - "
            f"chrome, every glyph: {_spread(ink)}"
        )
        brief[f"r5 {name}"] = " ".join(f"{d:+g}" for d in deltas)
        own_pens, own_lefts, own_emitted, own_agree = controls[text]
        if len(own_lefts) == len(lefts):
            apart = _spread([a - b for a, b in zip(own_lefts, lefts, strict=True)])
        else:
            apart = "not compared, the counts differ"
        out.append(
            f"     control, add_text in the LIBRARY's code entry at {px:g} px from the same x: "
            f"{own_emitted} quads ({emitted} in the chrome font), {own_agree} where its baked "
            f"advances put them, last pen - chrome's last pen {own_pens[-1] - pens[-1]:+g} px;  "
            f"ink's left edge, library entry - chrome: {apart}"
        )

    # R6: the grid.
    pens_off = sum(1 for g in everything if g.pen != math.floor(g.pen))
    corners_off = sum(1 for g in everything if any(v != math.floor(v) for v in g.quad))
    resampled = sum(
        1 for g in everything if abs((g.quad[2] - g.quad[0]) - (g.bitmap[1] - g.bitmap[0])) > EPS
    )
    laid_off = sum(1 for g in everything if abs(g.laid - round(g.laid)) > EPS)
    out.append(
        f"  R6 glyphs {len(everything)};  pens at a fractional x: {pens_off};  quads with a "
        f"corner off the pixel grid: {corners_off};  quad width != bitmap width: {resampled};  "
        f"(laid-out x not a whole pixel: {laid_off})"
    )
    brief["r6"] = f"{pens_off}/{corners_off}/{resampled}"
    return out, brief


def _row_start(row: list[Glyph], text: str) -> int:
    """
    The block-text index of the row's first character.
    """
    return min(glyph.index for glyph in row) - (len(text) - len(text.lstrip()))


TABLE = (
    ("size", "pitch, '0' ts/chrome", 24),
    ("r1 prose", "R1 prose runs d-l", 19),
    ("r1 code", "R1 code runs d-l", 18),
    ("r1 40", "R1 40ch drawn/laid/chrome", 27),
    ("r1 74", "R1 74ch drawn/laid/chrome", 27),
    ("r1 in 74", "in 74ch glyph d-l", 19),
    ("r2a prose", "R2a prose", 11),
    ("r2a code", "R2a code", 10),
    ("r2b code", "R2b code", 10),
    ("r3", "R3 space px", 19),
    ("r4", "R4 steps, drift", 19),
    ("r5 40", "R5 40ch @1,10,20,30,40", 24),
    ("r5 74", "R5 74ch @1,10,20,30,40,74", 27),
    ("r6", "R6 frac/off/resampled", 21),
)
"""
The table's columns: key in ``measure``'s brief, heading, width.

The first column is the fenced block's line pitch as drawn and the bitmap of
``0`` as typeset and as the chrome font has it at the pixel height beside the
page. R1 runs are the least and greatest drawn-less-laid-out width of a spaceless
run; ``in 74ch`` the least and greatest of one glyph's x from the row's first.
R2a counts steps equal to the baked advance of the library's own atlas entry,
R2b of the chrome font at the page's pixel height. R3 is the word space by the
baked-advance yardstick. R5 is typeset pen less chrome pen.
"""


def uptime() -> str:
    try:
        return subprocess.run(
            ["uptime"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError as exc:
        return f"uptime not run: {exc}"


def provenance(package: Any) -> str:
    """
    The file ``imscribe`` was imported from, and the commit when its directory is a checkout.
    """
    where = Path(package.__file__).resolve().parent
    said = f"imscribe imported from {where}"
    if not (where.parent / ".git").exists():
        return f"{said};  not a git checkout, so no commit is read here"
    try:
        head = subprocess.run(
            ["git", "-C", str(where.parent), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        edits = subprocess.run(
            ["git", "-C", str(where.parent), "status", "--porcelain", "--", "imscribe"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
    except OSError as exc:
        return f"{said};  git not run: {exc}"
    return f"{said};  commit {head or 'not read'};  {len(edits)} path(s) edited or added under it"


def _lengths(text: str) -> tuple[float, ...]:
    return tuple(float(part) for part in text.split(",") if part.strip())


def _faces(text: str) -> tuple[str, ...]:
    faces = tuple(part.strip() for part in text.split(",") if part.strip())
    for face in faces:
        if face not in FACES:
            raise argparse.ArgumentTypeError(f"{face!r} is not one of {', '.join(FACES)}")
    return faces


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument(
        "--px",
        type=_lengths,
        default=None,
        help="pixel heights, comma separated (default 14,16,18,20; none when only --em is given)",
    )
    parser.add_argument(
        "--em", type=_lengths, default=(), help="em sizes in pixels, comma separated (default none)"
    )
    parser.add_argument("--faces", type=_faces, default=FACES, help="mono, roboto or both")
    args = parser.parse_args(argv)
    if args.px is None:
        args.px = () if args.em else SIZES
    return args


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    from pptmstr import pairing, version

    pairing.adopt()
    for mark, text in ((MARKS[-2], RULER), (MARKS[-1], LONG)):
        assert len(text) == mark, (len(text), mark)
        assert all(not text[k - 1].isspace() for k in MARKS if k <= len(text)), text

    import imscribe
    from imscribe import DARK_PALETTE, Face, TypesetUnavailable, TypesetView, fontlib, typesetter

    ts = typesetter.module()
    if ts is None:
        print(f"imtypeset is not drawing: {typesetter.reason()}", file=sys.stderr)
        return 1

    import imgui_bundle
    from imgui_bundle import hello_imgui, imgui, immapp
    from imscribe.description import FACE_ROLES

    before = uptime()
    pages = [
        Page(face, unit, size)
        for face in args.faces
        for unit, sizes in (("em", args.em), ("px", args.px))
        for size in sizes
    ]
    state: dict[str, Any] = {"page": 0, "shown": 0, "views": None, "first_draw": 0.0, "made": 0.0}
    facts: dict[str, str] = {}
    report: list[str] = []
    table: list[tuple[Page, dict[str, str]]] = []
    failed: list[str] = []

    def load_fonts() -> None:
        # pptmstr/theme.py, load_fonts and _load_face: the chrome faces first.
        hello_imgui.add_assets_search_path(str(ASSETS))
        hello_imgui.get_runner_params().callbacks.default_icon_font = (
            hello_imgui.DefaultIconFont.font_awesome6
        )
        state["chrome"] = hello_imgui.load_font_ttf_with_font_awesome_icons(
            "fonts/Inconsolata-Medium.ttf", CHROME_PX
        )
        hello_imgui.load_font_ttf("fonts/Inconsolata-Bold.ttf", CHROME_PX)
        atlas = imgui.get_io().fonts
        entries = len(atlas.fonts)
        # The vendored bold is the library's bold code face, and the library's
        # faces go on the atlas last.
        fontlib.register_font(Face.CODE_BOLD, ASSETS / "fonts" / "Inconsolata-Bold.ttf")
        started = time.perf_counter()
        ready = typesetter.load_fonts()
        took = time.perf_counter() - started
        facts["typesetter.load_fonts()"] = (
            f"answered {ready} in {took * 1000.0:.1f} ms;  trouble: "
            f"{typesetter.trouble() or 'none'}"
        )
        facts["atlas font entries"] = (
            f"{entries} before typesetter.load_fonts(), {len(atlas.fonts)} after"
        )

    def page_views(page: Page) -> dict[str, Any]:
        theme = theme_for(page)
        prose = TypesetView("\n\n".join((PROSE, RHYTHM, DIGITS)), theme=theme, palette=DARK_PALETTE)
        code = TypesetView(
            "```\n" + "\n".join(CODE_ROWS) + "\n```", theme=theme, palette=DARK_PALETTE
        )
        return {"theme": theme, "prose": prose, "code": code}

    def candidates(sizes: dict[str, float]) -> list[Any]:
        # Every size the atlas holds for the entry, and not whole pixels near the
        # theme's: a Roboto page's prose is drawn from a size that is no whole
        # pixel, and a range of whole ones found nothing of it.
        from pptmstr.theme import baked_sizes

        atlas = imgui.get_io().fonts
        fonts = atlas.fonts
        out = []
        for role in ("PROSE", "CODE"):
            index = ts.font_atlas_index(FACE_ROLES[role])
            if index is not None and 0 <= index < len(fonts):
                held = [size for size, _ in baked_sizes(atlas, fonts[index])]
                out.append((role, fonts[index], held))
        return out

    def describe_atlas() -> None:
        io = imgui.get_io()
        rows = []
        for index, font in enumerate(io.fonts.fonts):
            source = font.sources[0]
            rows.append(
                f"[{index}] {font.get_debug_name()}: {len(font.sources)} source(s), "
                f"font_loader_flags {source.font_loader_flags}, pixel_snap_h "
                f"{source.pixel_snap_h}, rasterizer_density {source.rasterizer_density:g}"
            )
        facts["atlas entries"] = "\n    " + "\n    ".join(rows)
        roles = ", ".join(
            f"{role} -> [{ts.font_atlas_index(FACE_ROLES[role])}]" for role in ("PROSE", "CODE")
        )
        facts["library roles (font_atlas_index)"] = roles
        facts["loader"] = f"{io.fonts.font_loader_name}, atlas flags {io.fonts.font_loader_flags}"
        scale = io.display_framebuffer_scale
        style = imgui.get_style()
        facts["density"] = (
            f"display {io.display_size.x:g}x{io.display_size.y:g}, framebuffer scale "
            f"{scale.x:g}x{scale.y:g}, style.font_scale_dpi {style.font_scale_dpi:g}, "
            f"style.font_scale_main {style.font_scale_main:g}, hello_imgui "
            f"dpi_window_size_factor {hello_imgui.dpi_window_size_factor():g}"
        )
        try:
            from OpenGL import GL

            facts["renderer"] = GL.glGetString(GL.GL_RENDERER).decode()
        except Exception as exc:  # noqa: BLE001 -- a fact not read is said, not fatal
            facts["renderer"] = f"not read ({type(exc).__name__})"

    def gui() -> None:
        if failed or state["page"] >= len(pages):
            hello_imgui.get_runner_params().app_shall_exit = True
            return
        page = pages[state["page"]]
        px = page.beside
        io = imgui.get_io()
        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(io.display_size)
        imgui.begin(
            "measure",
            None,
            imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_saved_settings.value,
        )
        try:
            if not typesetter.ready():
                failed.append(typesetter.reason() or typesetter.trouble() or "fonts not loaded")
                return
            if state["views"] is None:
                started = time.perf_counter()
                state["views"] = page_views(page)
                state["made"] = time.perf_counter() - started
                state["shown"] = 0
            views = state["views"]
            draw_list = imgui.get_window_draw_list()
            white = imgui.get_font_tex_uv_white_pixel()
            spans = {}
            started = time.perf_counter()
            for name, width in (("prose", PROSE_PX), ("code", CODE_PX)):
                start = draw_list.vtx_buffer.size()
                views[name].draw(palette=DARK_PALETTE, width_px=width)
                spans[name] = (start, draw_list.vtx_buffer.size())
            if state["shown"] == 0:
                state["first_draw"] = time.perf_counter() - started
            state["shown"] += 1
            if state["shown"] < SETTLE_FRAMES:
                return
            if not facts.get("renderer"):
                describe_atlas()
            sizes = expected_sizes(views["theme"])
            found = candidates(sizes)
            prose = read_view(views["prose"], draw_list, *spans["prose"], white, found)
            code = read_view(views["code"], draw_list, *spans["code"], white, found)[0]
            rows = rows_of(code)
            if len(rows) != len(CODE_ROWS):
                raise Unread(f"{len(rows)} lines for {len(CODE_ROWS)} fenced rows: a row wrapped")
            y = imgui.get_cursor_screen_pos().y + 8.0
            natives, controls = {}, {}
            own = next(font for role, font, _ in found if role == "CODE")
            for offset, text in enumerate((RULER, LONG)):
                row = rows[CODE_ROWS.index(text)]
                natives[text] = chrome_line(
                    draw_list,
                    imgui,
                    state["chrome"],
                    px,
                    row[0].pen,
                    y + offset * (px + 6.0),
                    text,
                    white,
                )
                controls[text] = chrome_line(
                    draw_list,
                    imgui,
                    own,
                    px,
                    row[0].pen,
                    y + (2 + offset) * (px + 6.0),
                    text,
                    white,
                )
            lines, brief = measure(
                px,
                prose,
                code,
                state["chrome"],
                natives,
                controls,
                sizes,
                list(views["code"].set_lines()),
            )
            report.append(
                f"{page.label}   (the chrome font beside it at {px:g} px; the page's two views "
                f"made in {state['made'] * 1000.0:.1f} ms; their first draw "
                f"{state['first_draw'] * 1000.0:.1f} ms, layout and baking included)"
            )
            report.extend(lines)
            report.append("")
            table.append((page, brief))
            for name in ("prose", "code"):
                views[name].close()
            state["views"] = None
            state["page"] += 1
        except (Unread, TypesetUnavailable) as exc:
            failed.append(f"{page.label}: {exc}")
        finally:
            imgui.end()

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "pptmstr: typeset advances"
    params.app_window_params.window_geometry.size = WINDOW
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.ini_disable = True
    params.callbacks.load_additional_fonts = load_fonts
    params.callbacks.show_gui = gui
    # imtypeset's context goes before the Dear ImGui context does.
    params.callbacks.before_exit = typesetter.unload
    immapp.run(params)
    after = uptime()

    info = ts.host_info()
    bundle = info.get("bundle", {})
    print("measure_typeset_advances: positions and counts from the draw list; no pixel was read")
    print(f"  uptime before: {before}")
    print(f"  uptime after:  {after}")
    print(f"  display: {os.environ.get('DISPLAY', 'unset')};  python {sys.version.split()[0]}")
    print(f"  {provenance(imscribe)}")
    print(f"  imgui_bundle {imgui_bundle.__version__} at {Path(imgui_bundle.__file__).parent}")
    print(f"  {version.library_line()}")
    print(
        f"  imtypeset {info.get('imtypeset_version')}, rasteriser {info.get('rasteriser')}, "
        f"built against headers {bundle.get('headers')}, accepted by {bundle.get('accepted_by')} "
        f"({bundle.get('probes_passed')} probes passed, failed: {bundle.get('probes_failed')}; "
        f"imgui_check {bundle.get('imgui_check')}, sizeof(ImGuiStyle) wheel "
        f"{bundle.get('wheel_sizeof_imguistyle')} / library "
        f"{bundle.get('library_sizeof_imguistyle')})"
    )
    for name, value in facts.items():
        print(f"  {name}: {value}")
    print(
        "  theme: Theme.at_pixel_height(px, faces=Faces.mono() | Faces(), align=Align.LEFT, "
        "spacing=Spacing.FRENCH);  on an em page px is em / em_size_for_pixel_height(face, 1)"
    )
    print(f"  views: prose {PROSE_PX:g} px wide, fenced block {CODE_PX:g} px wide")
    print()
    print("\n".join(report))
    head = f"{'page':<13}" + "".join(f"{title:<{w}}" for _, title, w in TABLE)
    print(head)
    print("-" * len(head))
    for page, brief in table:
        cells = "".join(f"{brief.get(key, '-'):<{w}}" for key, _, w in TABLE)
        print(f"{page.label:<13}{cells}")
    if failed:
        print()
        for reason in failed:
            print(f"NOT READ: {reason}", file=sys.stderr)
        return 2
    return 0


# ---------------------------------------------------------------------------
# --pptmstr: pptmstr's own fonts and role themes
# ---------------------------------------------------------------------------

PPTMSTR_BODIES = (14.0, 16.0, 18.0, 20.0)
"""
The body sizes the settings dialog offers at a 16 px UI font (``roles.SCALES``).
"""
PPTMSTR_STEPS = (-2, -1, 0, 1, 2, 3, 4)
"""
``roles.STEPS``: so a role is set at every pixel height from 12 to 24.
"""
SPAN = RULER[:16]
INLINE = f"A code span `{SPAN}` in a sentence of prose."
TAB_ROW = "\ta\tbc\tdefg\th"
HOST_ROWS = (*CODE_ROWS, TAB_ROW)
WIDE_ROWS = ("xπxЖx", "Ж" * 10, "x中x\U0001f600x", "plain")
"""
A host block's rows holding characters the code face lacks: Greek and Cyrillic,
which the prose face has, and a CJK character and an emoji, which no face has.
"""
WIDE_PX = 80.0
SELECTED = (10, 40)
"""
Characters of the 74-character host row a selection is made over: its fill is
read against the pens of the first and of the one after the last.
"""


@dataclass(frozen=True, slots=True)
class RolePage:
    """
    pptmstr's roles at one body size, with the code, literal and inline-code
    roles all at one step, and prose in its family's slot.
    """

    family: str
    body: float
    step: int

    @property
    def code_px(self) -> float:
        return self.body + self.step

    @property
    def label(self) -> str:
        return f"{self.family} {self.body:g}{self.step:+d}"


@dataclass(frozen=True, slots=True)
class Ink:
    """
    One inked character of a view and the quad that drew it, attributed to an
    atlas entry and a baked size by the quad's uv rectangle.
    """

    char: str
    block: int
    index: int
    quad: tuple[float, float, float, float]
    font: int
    baked_px: float
    pen: float
    pen_y: float
    advance: float


def white_quads(draw_list: Any, start: int, stop: int, white: Any) -> list[tuple[float, ...]]:
    """
    The fills among vertices ``[start, stop)``: quads whose uv is the white pixel.
    """
    buffer = draw_list.vtx_buffer
    flat = []
    for i in range(start, stop):
        vertex = buffer[i]
        if abs(vertex.uv.x - white.x) <= 1e-9 and abs(vertex.uv.y - white.y) <= 1e-9:
            flat.append((vertex.pos.x, vertex.pos.y))
    out = []
    for at in range(0, len(flat) - 3, 4):
        xs, ys = zip(*flat[at : at + 4], strict=True)
        out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def read_inks(
    view: Any, draw_list: Any, start: int, stop: int, white: Any, fonts: list[tuple[int, Any]]
) -> list[Ink]:
    """
    Every inked character of ``view`` with its quad, each attributed on its own:
    a paragraph holding a code span is drawn from two entries. Raises ``Unread``
    when the counts differ or a quad matches no glyph of any entry's sizes.
    """
    from imgui_bundle import imgui

    from pptmstr.theme import baked_sizes

    atlas = imgui.get_io().fonts
    quads = glyph_quads(draw_list, start, stop, white)
    inked = [
        (block, index, char)
        for block, text in enumerate(view.texts)
        for index, char in enumerate(text)
        if not char.isspace()
    ]
    if len(quads) != len(inked):
        raise Unread(f"{len(quads)} glyph quads for {len(inked)} characters with ink")
    sized = [
        (number, font, held_bake(font, size))
        for number, font in fonts
        for size, _ in baked_sizes(atlas, font)
    ]
    out = []
    for (block, index, char), quad in zip(inked, quads, strict=True):
        found = None
        for number, _, baked in sized:
            if baked is None or not baked.is_glyph_loaded(ord(char)):
                continue
            glyph = baked.find_glyph(ord(char))
            uv = (glyph.u0, glyph.v0, glyph.u1, glyph.v1)
            if all(abs(a - b) < 1e-7 for a, b in zip(uv, quad[4:], strict=True)):
                found = (number, baked, glyph)
                break
        if found is None:
            raise Unread(f"block {block} character {index} {char!r}: no glyph has its quad's uv")
        number, baked, glyph = found
        out.append(
            Ink(
                char,
                block,
                index,
                (quad[0], quad[1], quad[2], quad[3]),
                number,
                float(baked.size),
                quad[0] - float(glyph.x0),
                quad[1] - float(glyph.y0),
                float(baked.get_char_advance(ord(char))),
            )
        )
    return out


def _lines_of(view: Any, block: int) -> list[Any]:
    return [line for line in view.set_lines(block, block + 1)]


def code_numbers(
    view: Any,
    block: int,
    inks: list[Ink],
    rows: tuple[str, ...],
    chrome: Any,
    px: float,
) -> dict[str, str]:
    """
    R2, R4, R5 and R6 of one code block's rows, glyph against rectangle, hit
    tests a quarter and three quarters into every glyph, and the drawn pitch.
    ``px`` is the pixel height the chrome font is set at beside it: the role's.
    """
    origin = view.origin
    lines = _lines_of(view, block)
    text = view.texts[block]
    mine = [ink for ink in inks if ink.block == block]
    baked = chrome.get_font_baked(px)
    adv = float(baked.get_char_advance(ord("0")))
    by_line: dict[int, list[Ink]] = {}
    starts: dict[int, int] = {}
    for line in lines:
        starts[line.line] = line.start
        by_line[line.line] = [ink for ink in mine if line.start <= ink.index < line.end]
    out: dict[str, str] = {}
    out["lines"] = f"{len(lines)}/{len(rows)}"
    out["drawn px"] = "/".join(f"{v:g}" for v in sorted({ink.baked_px for ink in mine}))
    steps = [
        b.pen - a.pen
        for row in by_line.values()
        for a, b in pairwise(row)
        if b.index == a.index + 1
    ]
    out["steps"] = _tally(steps)
    out["r2 chrome"] = f"{sum(1 for s in steps if abs(s - adv) < EPS)}/{len(steps)}"
    columns: dict[int, list[float]] = {}
    for number, row in by_line.items():
        if "\t" in text[starts[number] : starts[number] + len(TAB_ROW)] and row:
            continue
        for ink in row:
            columns.setdefault(ink.index - starts[number], []).append(ink.pen)
    first = min((ink.pen for ink in mine), default=0.0)
    drift = max((max(p) - min(p) for p in columns.values()), default=0.0)
    off = sum(
        1
        for column, pens in columns.items()
        for pen in pens
        if abs(pen - first - column * adv) > 0.01
    )
    out["r4 drift"] = f"{drift:g}"
    # R5: the chrome font's pens for the same row from the same x.
    r5 = []
    for wanted in (RULER, LONG):
        number = next(
            (
                n
                for n, line in enumerate(lines)
                if text[line.start : line.end].rstrip("\n") == wanted
            ),
            None,
        )
        if number is None:
            r5.append("absent")
            continue
        row = by_line[lines[number].line]
        at = row[0].pen
        pens = []
        for char in wanted:
            pens.append(at)
            at += float(baked.get_char_advance(ord(char)))
        placed = {ink.index - lines[number].start: ink for ink in row}
        r5.append(
            " ".join(f"{placed[k - 1].pen - pens[k - 1]:+g}" for k in MARKS if k <= len(wanted))
        )
    out["r5 40"], out["r5 74"] = r5
    # Glyph against rectangle, and the hit test, in the document's coordinates.
    doc = view.native_document
    rect_off = 0.0
    mishit = 0
    missed: list[str] = []
    tops = {line.line: (line.top + line.bottom) / 2.0 for line in lines}
    for number, row in by_line.items():
        for ink in row:
            rects = view.span_rects(block, ink.index, ink.index + 1)
            if len(rects) == 1:
                rect_off = max(rect_off, abs(origin[0] + rects[0][0] - ink.pen))
            x = ink.pen - origin[0]
            y = tops[number]
            before = tuple(doc.hit_test((x + 0.25 * adv, y)).position)
            after = tuple(doc.hit_test((x + 0.75 * adv, y)).position)
            if before != (block, ink.index) or after != (block, ink.index + 1):
                mishit += 1
                if len(missed) < 4:
                    missed.append(f"{ink.char!r}@{ink.index}->{before[1]},{after[1]}")
    out["rect"] = f"{rect_off:.2f}"
    out["hits"] = f"{mishit}/{len(mine)} {' '.join(missed)}"
    out["r6"] = f"{sum(1 for ink in mine if ink.pen != math.floor(ink.pen))}"
    pens_y = [row[0].pen_y for row in by_line.values() if row]
    out["pitch"] = _tally([b - a for a, b in pairwise(pens_y)])
    out["row baselines"] = ",".join(f"{y:g}" for y in pens_y)
    out["laid baselines"] = f"origin {origin[1]:g}: " + ",".join(
        f"{line.baseline:.4f}" for line in lines
    )
    out["off"] = str(off)
    if TAB_ROW in text:
        number = next(
            n for n, line in enumerate(lines) if text[line.start : line.end].rstrip("\n") == TAB_ROW
        )
        row = by_line[lines[number].line]
        columns_in = [(ink.pen - first) / adv for ink in row]
        out["tabs"] = ",".join(f"{c:g}" for c in columns_in)
    return out


def pptmstr_main(args: argparse.Namespace) -> int:
    """
    The ``--pptmstr`` pages: pptmstr's fonts loaded by ``pptmstr.theme.load_fonts``
    (the chrome, then ``gate.load_fonts``), and pptmstr's role themes
    (``themes.document_theme`` after ``roles.configure``), at every body size the
    settings dialog offers and every step: the code, literal and inline-code
    roles from 12 to 24 px.

    Each page draws three views: a document read as pptmstr reads one (a prose
    paragraph with a code span, a paragraph of the ruler and the long row, and
    a fence of the code rows), a host-made ``MarkdownCode`` of the code rows and
    a tab row in the literal role's style under ``code_overflow="grow"`` and
    ``fixed_line_height=True``, and another such block of rows holding
    characters the code face lacks. The chrome is the application's own UI
    font at the role's pixel height.
    """
    from pptmstr import pairing, version

    pairing.adopt()
    import imgui_bundle
    import imscribe
    from imgui_bundle import hello_imgui, imgui, immapp
    from imscribe import MarkdownCode, TypesetView, typesetter

    from pptmstr import theme as ui_theme
    from pptmstr.ui.typeset import gate, roles, themes
    from pptmstr.ui.typeset.roles import Role, RoleStyle, Slot

    pages = [
        RolePage(family, body, step)
        for family in args.families
        for body in args.bodies
        for step in PPTMSTR_STEPS
    ]
    state: dict[str, Any] = {"page": 0, "shown": 0, "views": None}
    rows_out: list[tuple[RolePage, dict[str, str]]] = []
    failed: list[str] = []
    facts: dict[str, str] = {}
    before = uptime()

    def configure(page: RolePage) -> None:
        mono = Slot.MONOSPACE
        given = {
            Role.CODE_BLOCK: RoleStyle(mono, page.step),
            Role.LITERAL: RoleStyle(mono, page.step),
            Role.INLINE_CODE: RoleStyle(mono, page.step),
        }
        if page.family == "mono":
            given[Role.PROSE] = RoleStyle(mono)
        roles.configure(typeset=True, scale=page.body / CHROME_PX, roles=given)

    def literal_style(theme: Any) -> Any:
        # The product's own: what a literal part the library sets is drawn in.
        return themes.literal_style(Role.LITERAL)

    def views_for(page: RolePage) -> dict[str, Any]:
        configure(page)
        theme = themes.document_theme()
        source = "\n\n".join(
            (
                INLINE,
                f"{RULER}  \n{LONG}",
                "```\n" + "\n".join(CODE_ROWS) + "\n```",
            )
        )
        doc = TypesetView(theme=theme, palette=themes.palette())
        doc.set_document(gate.read(source, theme).blocks)
        style = literal_style(theme)
        host = TypesetView(
            theme=theme, palette=themes.palette(), code_overflow="grow", fixed_line_height=True
        )
        host.set_document([MarkdownCode(style, "\n".join(HOST_ROWS))])
        wide = TypesetView(
            theme=theme, palette=themes.palette(), code_overflow="grow", fixed_line_height=True
        )
        wide.set_document([MarkdownCode(style, "\n".join(WIDE_ROWS))])
        long = HOST_ROWS.index(LONG)
        offset = sum(len(row) + 1 for row in HOST_ROWS[:long])
        host.select(((0, offset + SELECTED[0]), (0, offset + SELECTED[1])))
        return {"theme": theme, "doc": doc, "host": host, "wide": wide}

    def gui() -> None:
        if failed or state["page"] >= len(pages):
            hello_imgui.get_runner_params().app_shall_exit = True
            return
        page = pages[state["page"]]
        io = imgui.get_io()
        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(io.display_size)
        imgui.begin(
            "measure",
            None,
            imgui.WindowFlags_.no_decoration.value
            | imgui.WindowFlags_.no_saved_settings.value
            | imgui.WindowFlags_.horizontal_scrollbar.value,
        )
        try:
            if not gate.can_draw():
                failed.append(gate.unavailable_reason() or "the library is not drawing")
                return
            if state["views"] is None:
                state["views"] = views_for(page)
                state["shown"] = 0
            views = state["views"]
            draw_list = imgui.get_window_draw_list()
            white = imgui.get_font_tex_uv_white_pixel()
            spans = {}
            for name, width in (("doc", 1200.0), ("host", 1200.0), ("wide", WIDE_PX)):
                start = draw_list.vtx_buffer.size()
                views[name].draw(palette=themes.palette(), width_px=width)
                spans[name] = (start, draw_list.vtx_buffer.size())
            state["shown"] += 1
            if state["shown"] < SETTLE_FRAMES:
                return
            atlas = io.fonts
            fonts = list(enumerate(atlas.fonts))
            chrome = ui_theme.face(ui_theme.Face.BODY)
            px = page.code_px
            code_entry = typesetter.module().font_atlas_index(
                __import__("imscribe.description", fromlist=["FACE_ROLES"]).FACE_ROLES["CODE"]
            )
            brief: dict[str, str] = {}
            # The document: inline code, then the fence.
            doc = views["doc"]
            inks = read_inks(doc, draw_list, *spans["doc"], white, fonts)
            span = [ink for ink in inks if ink.block == 0 and ink.font == code_entry]
            span_steps = [b.pen - a.pen for a, b in pairwise(span)]
            chrome_at_body = float(chrome.get_font_baked(page.body).get_char_advance(ord("0")))
            chrome_at_role = float(chrome.get_font_baked(px).get_char_advance(ord("0")))
            brief["inline"] = (
                f"{len(span)}ch {span[-1].pen + span[-1].advance - span[0].pen:g}px "
                f"steps {_tally(span_steps)} drawn "
                f"{'/'.join(f'{v:g}' for v in sorted({i.baked_px for i in span}))}; "
                f"chrome x16 @{page.body:g} {16 * chrome_at_body:g} @{px:g} {16 * chrome_at_role:g}"
            )
            fence = len(doc.texts) - 1
            numbers = code_numbers(doc, fence, inks, CODE_ROWS, chrome, px)
            brief.update({f"fence {k}": v for k, v in numbers.items()})
            # Prose rows (the ruler and the long row as a paragraph with a hard break).
            prose_rows = [ink for ink in inks if ink.block == 1]
            prose_steps = [b.pen - a.pen for a, b in pairwise(prose_rows) if b.index == a.index + 1]
            same = sum(1 for s in prose_steps if abs(s - chrome_at_body) < EPS)
            brief["prose rows chrome"] = f"{same}/{len(prose_steps)}"
            spaces = [
                b.pen - a.pen - a.advance
                for a, b in pairwise(ink for ink in inks if ink.block == 0)
                if b.index == a.index + 2
            ]
            brief["spaces"] = _tally(spaces)
            # The host block.
            host = views["host"]
            hinks = read_inks(host, draw_list, *spans["host"], white, fonts)
            numbers = code_numbers(host, 0, hinks, HOST_ROWS, chrome, px)
            brief.update({f"host {k}": v for k, v in numbers.items()})
            long = HOST_ROWS.index(LONG)
            offset = sum(len(row) + 1 for row in HOST_ROWS[:long])
            by_index = {ink.index: ink for ink in hinks}
            fills = white_quads(draw_list, *spans["host"], white)
            want = (by_index[offset + SELECTED[0]].pen, by_index[offset + SELECTED[1]].pen)
            brief["host fill"] = (
                "none"
                if not fills
                else " ".join(f"{f[0] - want[0]:+g}/{f[2] - want[1]:+g}" for f in fills)
            )
            brief["host texts"] = str(host.texts[0] == "\n".join(HOST_ROWS))
            brief["host width"] = f"{host.content_width:g}"
            # The wide block: where each row's glyphs are, against what the view claims.
            wide = views["wide"]
            wquads = glyph_quads(draw_list, *spans["wide"], white)
            wlines = _lines_of(wide, 0)
            origin = wide.origin
            text = wide.texts[0]
            inked = [i for i, c in enumerate(text) if not c.isspace()]
            row_of = {}
            for line in wlines:
                for i in range(line.start, line.end):
                    row_of[i] = line.line
            per_row: dict[int, list[float]] = {}
            right = 0.0
            for i, quad in zip(inked, wquads, strict=False):
                per_row.setdefault(row_of.get(i, -1), []).append(quad[0] - origin[0])
                right = max(right, quad[2] - origin[0])
            brief["wide quads"] = f"{len(wquads)}/{len(inked)}"
            brief["wide lefts"] = " | ".join(
                ",".join(f"{v:g}" for v in lefts) for _, lefts in sorted(per_row.items())
            )
            brief["wide right"] = (
                f"ink {right:g} lines {max(line.right for line in wlines):g} "
                f"content_width {wide.content_width:g} cell {chrome_at_role:g} "
                f"cells {max(len(r) for r in WIDE_ROWS)}"
            )
            rows_out.append((page, brief))
            for name in ("doc", "host", "wide"):
                views[name].close()
            state["views"] = None
            state["page"] += 1
            state["tries"] = 0
        except Unread as exc:
            # A glyph baked later in the frame can move the atlas under quads
            # already emitted: the page is drawn again, a few times at most.
            state["tries"] = state.get("tries", 0) + 1
            if state["tries"] > 8:
                failed.append(f"{page.label}: {exc}")
        finally:
            imgui.end()

    def load() -> None:
        ui_theme.load_fonts()
        facts["fonts"] = (
            f"pptmstr.theme.load_fonts(): {len(imgui.get_io().fonts.fonts)} atlas entries; "
            f"gate.can_draw() {gate.can_draw()}"
        )

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "pptmstr: typeset advances, pptmstr's roles"
    params.app_window_params.window_geometry.size = WINDOW
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.ini_disable = True
    params.callbacks.load_additional_fonts = load
    params.callbacks.show_gui = gui
    params.callbacks.before_exit = gate.unload
    immapp.run(params)
    after = uptime()
    print(
        "measure_typeset_advances --pptmstr: positions and counts from the draw list; no pixel read"
    )
    print(f"  uptime before: {before}")
    print(f"  uptime after:  {after}")
    print(f"  {provenance(imscribe)}")
    print(f"  imgui_bundle {imgui_bundle.__version__}")
    print(f"  {version.library_line()}")
    for name, value in facts.items():
        print(f"  {name}: {value}")
    print(
        "  pages: family, body px, and the step of the code-block, literal and inline-code "
        "roles; the chrome is pptmstr's UI font at body+step"
    )
    print()
    for page, brief in rows_out:
        print(page.label)
        for key, value in brief.items():
            print(f"  {key}: {value}")
    if failed:
        for reason in failed:
            print(f"NOT READ: {reason}", file=sys.stderr)
        return 2
    return 0


def _main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    if "--pptmstr" not in given:
        return main(given)
    parser = argparse.ArgumentParser(description="pptmstr's role themes")
    parser.add_argument("--pptmstr", action="store_true")
    parser.add_argument(
        "--bodies", type=_lengths, default=PPTMSTR_BODIES, help="body pixel heights"
    )
    parser.add_argument(
        "--families",
        type=lambda text: tuple(p.strip() for p in text.split(",") if p.strip()),
        default=("roboto", "mono"),
        help="roboto (prose proportional), mono (prose in the code face), or both",
    )
    return pptmstr_main(parser.parse_args(given))


if __name__ == "__main__":
    raise SystemExit(_main())
