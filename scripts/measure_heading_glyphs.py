#!/usr/bin/env python3
"""
Which atlas entry, baked size and bitmap each glyph of a markdown heading comes from, as numbers.

The question is the operator's report of 2026-10-12, "odd sizing issues in the
headers of markdown in CONTEXT": are the glyphs of one heading drawn from more
than one size or face, are they one size and placed unevenly, or is a glyph's
bitmap not the one its atlas entry bakes at that size? This prints, for every
glyph of two headings, what the draw list and the atlas say, and decides
nothing about how anything looks.

## The window

The real runner with ``pptmstr.theme.load_fonts`` as its font callback and
``typeset.pre_frame`` before every frame, as ``pptmstr/app.py`` wires them. The
text is drawn in a child window with the flags of the context log's
(``transcript_pane._draw_typeset``).

## The scenes

A scene is one path, one way of writing the heading, one step of the heading
role and one proportional family.

    paths   settled  ``typeset.document(cache, key, (Part(text),), rests=True,
                     apart=True)``: a closed run of the log
                     (``transcript_pane._draw_generation``).
            stream   ``typeset.stream(cache, key)``, appended to a piece a
                     frame and drawn with ``draw(ink=None)``: the log's open
                     run (``transcript_pane._draw_run``). Read on every frame.
            direct   ``advtext.TypesetView`` with ``Theme.at_pixel_height`` and
                     no pptmstr theme: the heading's words as a bold paragraph
                     at the pixel height the seam's heading asked for, and the
                     same markdown under the library's own heading scale.
            native   no library at all: ``imgui.push_font`` of the atlas entry
                     the library's bold face is, at the heading's and at the
                     body's pixel height, and ``imgui.text_unformatted``. The
                     words arrive as a stream's do.
    forms   h1..h4   ``# 3. Training data and API stability`` and so on
            bold     ``**3. Training data and API stability**``
            item     ``3. **Training data and API stability**``

## Room made in the atlas

ImGui packs a glyph into the atlas texture the first time it is drawn. When one
does not fit it discards every baked size no text used in the last two frames
and repacks (``ImFontAtlasTextureMakeSpace``), and at the next frame closes the
gaps in its pool of baked sizes. ``--decoys N`` and ``--room`` make that happen
at a known place in a stream or a native scene:

    --decoys N      before the scene, N frames each drawing one word in the
                    chrome's font at a size nothing else uses, then frames
                    without it: N baked sizes ImGui may discard.
    --room forced   when the text before the second heading has arrived, one
                    call of ``imgui.internal.im_font_atlas_texture_make_space``,
                    the function ImGui calls itself on a glyph that did not fit.
    --room filled   at the same place, a line of the chrome's font at a new
                    large size every frame until the atlas texture is another
                    one: no call ImGui would not have made.
    Which count of decoys changes a glyph is not fixed: it is the count that
    moves a baked size into the place in ImGui's pool that its rasteriser
    last made current, so it goes with every other size the process has baked
    and left unused by then. Run 0, 1 and 2, each in its own process; the
    line "before room is made, the atlas holds" says what there was to discard.

    --keep          before every frame, after the seam's turn,
                    ``pptmstr.theme.keep_baked_sizes`` as ``pptmstr/app.py``
                    calls it: every baked size the atlas holds is marked used,
                    so ImGui discards none. Without it this window is the
                    application less that one call.

## What is read, and from what

Nothing is read from pixels.

    quads   the textured quads the draw added to the child's draw list whose
            vertical centre is inside the heading's line (``view.span_rects``),
            left to right, against the heading's characters that have ink. A
            list marker is on the same line and is left of them.
    entry   a quad is attributed by its uv rectangle being an ``ImFontGlyph``'s
            of one atlas font at one baked size, among every baked size the
            atlas holds (``bakes_of``, which makes none and marks none used).
    bitmap  the uv rectangle times the atlas texture's size: texels.
    fresh   the same character's bitmap and advance in the same entry at the
            same size in ANOTHER process that has just loaded the fonts and
            bakes each size and loads its glyphs before it makes the next
            (``--reference``). A bitmap that is not its fresh one is counted,
            with the sizes whose fresh bitmap it is.
    pen     the quad's left edge less the glyph's ``x0``, and its top less ``y0``.
    step    the next glyph's pen less this one's, beside this glyph's baked
            advance and the library's own rectangle for the character
            (``view.span_rects``), which is its layout before any snapping.
    asked   the pixel height (ascent plus descent) of the block's font in the
            theme the view was given.

A scene is read on its first frame and again ``SETTLE_FRAMES`` later, and the
two are compared; a stream on every frame while it arrives.

## Running it

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/measure_heading_glyphs.py
    ... measure_heading_glyphs.py --paths stream --forms h2 --steps 0 \\
            --families roboto --decoys 1 --room forced --tables all

``IMTYPESET_LIBRARY`` and ``IMTYPESET_PYTHON`` are taken from the environment
(``make typeset-env`` prints them) and default to the pair under
``.deps/imtypeset``. Scenes run in one process in the order printed, so a glyph
a later scene draws may have been baked by an earlier one: ``--paths``,
``--forms``, ``--steps`` and ``--families`` choose what a fresh process sees
first. Exit status 1 when the library is not drawing, 2 when a scene could not
be read.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PAIR = ROOT / ".deps" / "imtypeset"
sys.path.insert(0, str(ROOT))

WINDOW = (1200, 900)
SETTLE_FRAMES = 4
REFERENCE_SIZES = range(8, 41)
"""
The whole pixel sizes a fresh process bakes each font at, to say what a glyph's
bitmap is when nothing came before it.
"""

DECOY_PX = 31.0
"""
The first size a decoy is drawn at; each further one is a pixel more.
"""

FILL_PX = 44.0
"""
The first size ``--room filled`` draws the chrome's font at; two pixels more a frame.
"""

FILL_FRAMES = 60
ASCII = "".join(chr(code) for code in range(33, 127))

HEADS = ("3. Training data and API stability", '4. "Lingua franca"')
"""
The two headings of the operator's screenshots, as text.
"""

INTRO = "Two findings follow, **each under a numbered heading** as the reply had them."
BODIES = (
    "- **Outdated Rust APIs are a measured problem.** One paper (verified, abstract) "
    "reports 56.1% success on API tasks before retrieval and more after.\n"
    "- A second item, so that the list is a list.",
    "- **The phrase appears in no source I found.**\n"
    "- **Rust is mid-table for agent use.** amplifying (verified; public GitHub, "
    "February 2025 to August).",
)

FORMS = ("h1", "h2", "h3", "h4", "bold", "item")
PATHS = ("settled", "stream", "direct", "native")
FAMILIES = ("roboto", "droid")
ROOMS = ("none", "forced", "filled")
EPS = 1e-3


@dataclass(frozen=True, slots=True)
class Scene:
    path: str
    form: str
    step: int
    family: str
    piece: int

    @property
    def label(self) -> str:
        said = f"{self.path:<7} {self.form:<4} step {self.step:+d} {self.family}"
        if self.path in ("stream", "native"):
            said += f" piece {'token' if self.piece == 0 else self.piece}"
        return said


@dataclass(frozen=True, slots=True)
class Quad:
    """
    One character of a heading and the quad that drew it.

    ``entry`` is the atlas font's index and ``baked`` its size, or ``-1`` and
    ``0`` for a uv rectangle no loaded glyph carries. ``box`` is the glyph's own
    width and height (``x1 - x0``, ``y1 - y0``). ``laid`` and ``laid_right`` are
    screen x of the library's rectangle for the character, and of the quad
    itself where no library set it.
    """

    char: str
    index: int
    entry: int
    name: str
    baked: float
    codepoint: int
    bitmap: tuple[float, float]
    box: tuple[float, float]
    quad: tuple[float, float, float, float]
    pen: tuple[float, float]
    advance: float
    laid: float
    laid_right: float

    @property
    def size(self) -> tuple[float, float]:
        return (self.quad[2] - self.quad[0], self.quad[3] - self.quad[1])

    @property
    def identity(self) -> tuple[Any, ...]:
        """
        What the glyph was drawn from and how large, without where.
        """
        return (self.char, self.entry, self.baked, self.codepoint, self.size)


Fresh = dict[tuple[str, str, int, str], tuple[float, float, float]]
"""
A fresh process's glyphs: (family, the atlas font's name, baked size, character) to
(bitmap width, bitmap height, advance).
"""


class Unread(Exception):
    """
    A heading's quads could not be paired with its characters; the message says how.
    """


def heading_line(form: str, head: str) -> str:
    number, _, words = head.partition(" ")
    if form == "bold":
        return f"**{head}**"
    if form == "item":
        return f"{number} **{words}**"
    return f"{'#' * int(form[1:])} {head}"


def markdown_for(form: str) -> str:
    """
    The document of a scene: a paragraph, then each heading over its list.
    """
    parts = [INTRO]
    for head, body in zip(HEADS, BODIES, strict=True):
        parts += [heading_line(form, head), body]
    return "\n\n".join(parts) + "\n"


def pieces_of(text: str, piece: int) -> list[str]:
    """
    The text as it arrives: ``piece`` characters a frame, or for 0 a run of up to
    five characters with the space before it, which is about a token.
    """
    if piece > 0:
        return [text[at : at + piece] for at in range(0, len(text), piece)]
    return re.findall(r"\s*\S{1,5}|\s+$", text)


def pieces_before(pieces: list[str], length: int) -> int:
    """
    How many of the pieces end at or before ``length`` characters of the text.
    """
    count, at = 0, 0
    for piece in pieces:
        at += len(piece)
        if at > length:
            break
        count += 1
    return count


def textured_quads(draw_list: Any, start: int, stop: int, white: Any) -> list[tuple[float, ...]]:
    """
    The textured quads among vertices ``[start, stop)``: (left, top, right, bottom, u0, v0, u1, v1).
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


def _uv_key(u0: float, v0: float, u1: float, v1: float) -> tuple[int, int, int, int]:
    return (round(u0 * 1e6), round(v0 * 1e6), round(u1 * 1e6), round(v1 * 1e6))


def bakes_of(imgui: Any, font: Any) -> list[Any]:
    """
    Every baked size the atlas holds for ``font``, largest first, and none made.

    ``ImFont.get_font_baked`` makes the size it is asked for and marks it used
    this frame, and either would change what is being measured: which sizes
    ImGui discards, and which one its rasteriser last made current. The
    closest-match lookup does neither. Asked for a size just under one that
    exists it answers the next one down, or the same one when there is none.
    """
    atlas = imgui.get_io().fonts
    closest = imgui.internal.im_font_atlas_baked_get_closest_match
    out: list[Any] = []
    ask = 1.0e6
    while True:
        baked = closest(atlas, font, ask, 1.0)
        if baked is None or (out and float(baked.size) >= float(out[-1].size)):
            return out
        out.append(baked)
        ask = float(baked.size) - 0.01


Glyphs = dict[tuple[int, int, int, int], list[tuple[int, str, Any, Any]]]


def atlas_glyphs(imgui: Any) -> Glyphs:
    """
    Every loaded glyph of every baked size of every atlas font, by its uv
    rectangle: (font index, font name, the baked font, the glyph).
    """
    out: Glyphs = {}
    for index, font in enumerate(imgui.get_io().fonts.fonts):
        for baked in bakes_of(imgui, font):
            for glyph in baked.glyphs:
                if not glyph.is_visible:
                    continue
                key = _uv_key(glyph.u0, glyph.v0, glyph.u1, glyph.v1)
                out.setdefault(key, []).append((index, font.get_debug_name(), baked, glyph))
    return out


def held(imgui: Any) -> str:
    """
    The baked sizes the atlas holds, a font an entry: ``[3] 16(41) 19(22)`` is
    entry 3 at 16 px with 41 glyphs loaded and at 19 px with 22.
    """
    said = []
    for index, font in enumerate(imgui.get_io().fonts.fonts):
        bakes = bakes_of(imgui, font)
        if bakes:
            sizes = " ".join(f"{b.size:g}({len(b.glyphs)})" for b in reversed(bakes))
            said.append(f"[{index}] {sizes}")
    return ";  ".join(said)


def texture(imgui: Any) -> tuple[int, int, int]:
    """
    The atlas texture: its id among the atlas's textures, its width and its height.
    """
    data = imgui.get_io().fonts.tex_data
    return (int(data.unique_id), int(data.width), int(data.height))


def heading_blocks(view: Any) -> dict[int, int]:
    """
    For each of ``HEADS`` the view shows any of: its block.

    A block is a heading's when its text is a prefix of the heading, or of the
    heading without its number, which is how a stream has it while it arrives
    and how a list item has it.
    """
    found: dict[int, int] = {}
    for block, text in enumerate(view.texts):
        if not text.strip():
            continue
        for which, head in enumerate(HEADS):
            if which in found:
                continue
            if head.startswith(text) or head.partition(" ")[2].startswith(text):
                found[which] = block
    return found


def _attributed(
    char: str,
    index: int,
    quad: tuple[float, ...],
    glyphs: Glyphs,
    size: tuple[int, int],
    laid: tuple[float, float] | None,
) -> Quad:
    bitmap = ((quad[6] - quad[4]) * size[0], (quad[7] - quad[5]) * size[1])
    hits = glyphs.get(_uv_key(*quad[4:]), [])
    # The same rectangle under two names would be two glyphs sharing texels;
    # the one for this character is taken where there is one.
    hit = next((h for h in hits if h[3].get_codepoint() == ord(char)), hits[0] if hits else None)
    if hit is None:
        entry, name, tall, codepoint = -1, "?", 0.0, 0
        box, pen, advance = (0.0, 0.0), (quad[0], quad[1]), 0.0
    else:
        entry, name, baked, glyph = hit
        tall = float(baked.size)
        codepoint = int(glyph.get_codepoint())
        box = (float(glyph.x1 - glyph.x0), float(glyph.y1 - glyph.y0))
        pen = (quad[0] - float(glyph.x0), quad[1] - float(glyph.y0))
        advance = float(glyph.advance_x)
    return Quad(
        char=char,
        index=index,
        entry=entry,
        name=name,
        baked=tall,
        codepoint=codepoint,
        bitmap=bitmap,
        box=box,
        quad=(quad[0], quad[1], quad[2], quad[3]),
        pen=pen,
        advance=advance,
        laid=pen[0] if laid is None else laid[0],
        laid_right=pen[0] + advance if laid is None else laid[1],
    )


def read_heading(
    view: Any, block: int, quads: list[tuple[float, ...]], glyphs: Glyphs, size: tuple[int, int]
) -> list[Quad]:
    """
    The inked characters of block ``block`` paired with the quads on its line.

    Raises ``Unread`` when the block is on more than one line or the line holds
    fewer quads than the block has inked characters.
    """
    origin = view.origin
    if origin is None:
        raise Unread("the view has no origin: it was not drawn")
    text = view.texts[block]
    rects = view.span_rects(block, 0, len(text))
    if len(rects) != 1:
        raise Unread(f"block {block} {text!r} is on {len(rects)} lines")
    top, bottom = origin[1] + rects[0][1], origin[1] + rects[0][3]
    on_line = sorted(
        (quad for quad in quads if top <= (quad[1] + quad[3]) / 2.0 <= bottom),
        key=lambda quad: quad[0],
    )
    inked = [(index, char) for index, char in enumerate(text) if not char.isspace()]
    if len(on_line) < len(inked):
        raise Unread(
            f"block {block} {text!r}: {len(on_line)} quads on its line for {len(inked)} "
            f"characters with ink"
        )
    # A list marker is drawn on the item's line, left of its text.
    mine = on_line[len(on_line) - len(inked) :]
    out = []
    for (index, char), quad in zip(inked, mine, strict=True):
        one = view.span_rects(block, index, index + 1)
        if len(one) != 1:
            raise Unread(f"block {block} character {index}: {len(one)} rectangles")
        laid = (origin[0] + one[0][0], origin[0] + one[0][2])
        out.append(_attributed(char, index, quad, glyphs, size, laid))
    return out


def read_line(
    text: str, quads: list[tuple[float, ...]], glyphs: Glyphs, size: tuple[int, int]
) -> list[Quad]:
    """
    The inked characters of one line ImGui drew, paired with its quads in order.
    """
    inked = [(index, char) for index, char in enumerate(text) if not char.isspace()]
    if len(quads) != len(inked):
        raise Unread(f"{text!r}: {len(quads)} quads for {len(inked)} characters with ink")
    return [
        _attributed(char, index, quad, glyphs, size, None)
        for (index, char), quad in zip(inked, quads, strict=True)
    ]


def asked_px(theme: Any, role: str, bold: bool) -> float:
    """
    The pixel height (ascent plus descent) of the font a block of ``role`` is set
    in under ``theme``; of its bold face for a paragraph or an item whose words
    are strong.
    """
    from dataclasses import replace

    from advtext import BlockKind, Face, px_from_sp
    from advtext.style import fontlib_metrics

    font = theme.blocks[BlockKind[role]].font
    if bold and font.face is Face.PROSE:
        font = replace(font, face=Face.PROSE_BOLD)
    metrics = fontlib_metrics(font.key)
    return float(px_from_sp(round(metrics.ascent + metrics.descent)))


def stale(quad: Quad, family: str, fresh: Fresh) -> tuple[str, ...] | None:
    """
    ``None`` when the glyph's bitmap and advance are its entry's at its size in
    a fresh process, or when the fresh process has no such glyph. Otherwise the
    sizes of that entry whose fresh glyph it is, which may be none.
    """
    mine = (quad.box[0], quad.box[1], quad.advance)
    theirs = fresh.get((family, quad.name, round(quad.baked), quad.char))
    if theirs is None or all(abs(a - b) < EPS for a, b in zip(mine, theirs, strict=True)):
        return None
    return tuple(
        f"{size}"
        for (fam, name, size, char), dims in sorted(fresh.items())
        if fam == family
        and name == quad.name
        and char == quad.char
        and all(abs(a - b) < EPS for a, b in zip(mine, dims, strict=True))
    )


def table(quads: list[Quad], family: str, fresh: Fresh) -> list[str]:
    """
    One line a glyph: where it came from, how large, its fresh bitmap, where its
    pen is, and the step to the next glyph beside the baked advance and the
    laid-out one.
    """
    out = [
        "      ch  entry                    baked  bitmap   fresh    quad     adv  fresh   "
        "pen x    pen y    step  laid-step  pen-laid"
    ]
    for at, quad in enumerate(quads):
        after = quads[at + 1] if at + 1 < len(quads) else None
        adjacent = after is not None and after.index == quad.index + 1
        step = f"{after.pen[0] - quad.pen[0]:6.2f}" if after is not None and adjacent else "     -"
        laid = f"{after.laid - quad.laid:8.3f}" if after is not None and adjacent else "       -"
        theirs = fresh.get((family, quad.name, round(quad.baked), quad.char))
        was = f"{theirs[0]:g}x{theirs[1]:g}" if theirs else "?"
        was_adv = f"{theirs[2]:5.2f}" if theirs else "    ?"
        found = stale(quad, family, fresh)
        note = "" if found is None else f"  <- the fresh bitmap at {'/'.join(found) or 'no size'}"
        out.append(
            f"      {quad.char!r:<4}[{quad.entry}] {quad.name[:20]:<20} {quad.baked:5g}  "
            f"{f'{quad.bitmap[0]:g}x{quad.bitmap[1]:g}':<8} {was:<8} "
            f"{f'{quad.size[0]:g}x{quad.size[1]:g}':<8} {quad.advance:5.2f} {was_adv} "
            f"{quad.pen[0]:8.2f} {quad.pen[1]:8.2f} {step}  {laid}  "
            f"{quad.pen[0] - quad.laid:+8.3f}{note}"
        )
    return out


def summary(quads: list[Quad], family: str, fresh: Fresh) -> str:
    """
    A heading's glyphs in one line: the entries and sizes present, and the counts
    that would differ if its glyphs were not all drawn one way.
    """
    sources = Counter((quad.entry, quad.baked) for quad in quads)
    said = ", ".join(f"[{entry}]@{size:g} x{count}" for (entry, size), count in sources.items())
    resampled = [
        quad.char
        for quad in quads
        if abs(quad.size[0] - quad.bitmap[0]) > EPS or abs(quad.size[1] - quad.bitmap[1]) > EPS
    ]
    wrong = [(quad, found) for quad in quads if (found := stale(quad, family, fresh)) is not None]
    unknown = sum(
        1 for quad in quads if (family, quad.name, round(quad.baked), quad.char) not in fresh
    )
    as_at = Counter("/".join(found) or "no size" for _, found in wrong)
    pens_y = sorted({round(quad.pen[1], 3) for quad in quads})
    off_grid = sum(1 for quad in quads if any(abs(v - round(v)) > EPS for v in quad.quad))
    steps = [
        (a, b.pen[0] - a.pen[0] - a.advance)
        for a, b in zip(quads, quads[1:], strict=False)
        if b.index == a.index + 1
    ]
    by_advance = Counter(round(over, 2) for _, over in steps)
    drift = [quad.pen[0] - quad.laid for quad in quads]
    return (
        f"sources {said};  BITMAP NOT THE FRESH ONE: {len(wrong)} of {len(quads)} "
        f"{''.join(quad.char for quad, _ in wrong)!r}"
        + (
            f" (the fresh bitmap at: "
            f"{', '.join(f'{size} px x{count}' for size, count in as_at.items())})"
            if wrong
            else ""
        )
        + f";  with no fresh glyph to compare: {unknown};  "
        f"quad != bitmap: {len(resampled)};  pen y {'/'.join(f'{y:g}' for y in pens_y)};  "
        f"corners off the grid: {off_grid};  step - baked advance, px: "
        f"{', '.join(f'{over:+g} x{count}' for over, count in sorted(by_advance.items()))};  "
        f"pen - laid x: {min(drift):+.2f}..{max(drift):+.2f}"
    )


def uptime() -> str:
    try:
        return subprocess.run(
            ["uptime"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError as exc:
        return f"uptime not run: {exc}"


def _ints(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(",") if part.strip())


def _among(allowed: tuple[str, ...]) -> Any:
    def parse(text: str) -> tuple[str, ...]:
        chosen = tuple(part.strip() for part in text.split(",") if part.strip())
        for each in chosen:
            if each not in allowed:
                raise argparse.ArgumentTypeError(f"{each!r} is not one of {', '.join(allowed)}")
        return chosen

    return parse


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("--paths", type=_among(PATHS), default=("settled", "stream", "direct"))
    parser.add_argument("--forms", type=_among(FORMS), default=FORMS)
    parser.add_argument("--families", type=_among(FAMILIES), default=FAMILIES)
    parser.add_argument(
        "--steps", type=_ints, default=(-2, -1, 0, 1, 2, 3, 4), help="heading role steps"
    )
    parser.add_argument(
        "--pieces",
        type=_ints,
        default=(0,),
        help="characters a frame a stream is fed; 0 is a token (default 0)",
    )
    parser.add_argument("--decoys", type=int, default=0, help="baked sizes left unused first")
    parser.add_argument("--room", choices=ROOMS, default="none", help="see the module docstring")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="before each frame, the application's guard: theme.keep_baked_sizes",
    )
    parser.add_argument(
        "--tables",
        default="0",
        help="heading steps whose scenes print every glyph, comma separated, or 'all' or 'none'",
    )
    parser.add_argument("--width", type=float, default=900.0, help="the child window's width")
    parser.add_argument(
        "--reference",
        default=None,
        help="print the glyphs of the atlas fonts of these names, separated by '|', as a "
        "fresh process bakes them, and exit",
    )
    return parser.parse_args(argv)


def run_window(title: str, gui: Any, pre_frame: Any) -> None:
    """
    The runner as ``pptmstr/app.py`` sets it up for fonts and the seam, around ``gui``.
    """
    from imgui_bundle import hello_imgui, immapp

    from pptmstr import theme
    from pptmstr.ui import typeset

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = title
    params.app_window_params.window_geometry.size = WINDOW
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.ini_disable = True
    params.fps_idling.enable_idling = False
    params.callbacks.load_additional_fonts = theme.load_fonts
    params.callbacks.pre_new_frame = pre_frame
    params.callbacks.show_gui = gui
    # imtypeset's context goes before the Dear ImGui context does.
    params.callbacks.before_exit = typeset.unload
    theme.set_theme("dark")
    immapp.run(params)


def reference(names: tuple[str, ...], family: str) -> int:
    """
    Print, as one line of JSON after ``REFERENCE``, each entry's glyphs for
    ``ASCII`` at ``REFERENCE_SIZES``: a size is baked and its glyphs loaded
    before the next size is made, all in one frame.
    """
    from imgui_bundle import hello_imgui, imgui

    from pptmstr.ui import typeset

    out: dict[str, list[float]] = {}
    state = {"frame": 0}

    def gui() -> None:
        state["frame"] += 1
        if state["frame"] < 3:
            return
        if not out:
            fonts = imgui.get_io().fonts.fonts
            for name in names:
                font = next((f for f in fonts if f.get_debug_name() == name), None)
                if font is None:
                    continue
                for size in REFERENCE_SIZES:
                    baked = font.get_font_baked(float(size))
                    for char in ASCII:
                        glyph = baked.find_glyph_no_fallback(ord(char))
                        if glyph is not None:
                            out[f"{size}:{char}:{name}"] = [
                                float(glyph.x1 - glyph.x0),
                                float(glyph.y1 - glyph.y0),
                                float(glyph.advance_x),
                            ]
        hello_imgui.get_runner_params().app_shall_exit = True

    typeset.configure(typeset=True, scale=1.0, proportional=family, roles={})
    run_window("pptmstr: heading glyphs, reference", gui, typeset.pre_frame)
    print("REFERENCE " + json.dumps(out))
    return 0 if out else 1


def fresh_glyphs(names: set[str], families: tuple[str, ...]) -> tuple[Fresh, list[str]]:
    """
    Ask a new process a family for the named fonts' fresh glyphs. The second answer
    is what went wrong, a line a family.
    """
    fresh: Fresh = {}
    trouble = []
    for family in families:
        done = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--reference",
                "|".join(sorted(names)),
                "--families",
                family,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        line = next((ln for ln in done.stdout.splitlines() if ln.startswith("REFERENCE ")), None)
        if line is None:
            trouble.append(f"{family}: exit {done.returncode}: {done.stderr.strip()[-300:]}")
            continue
        for key, dims in json.loads(line.removeprefix("REFERENCE ")).items():
            size, char, name = key.split(":", 2)
            fresh[(family, name, int(size), char)] = (dims[0], dims[1], dims[2])
    return fresh, trouble


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    os.environ.setdefault("IMTYPESET_LIBRARY", str(PAIR / "libimtypeset_bundle.so"))
    os.environ.setdefault("IMTYPESET_PYTHON", str(PAIR / "pysnap"))
    if args.reference is not None:
        return reference(tuple(args.reference.split("|")), args.families[0])

    import imgui_bundle
    from imgui_bundle import hello_imgui, imgui

    from pptmstr import theme
    from pptmstr.ui import typeset
    from pptmstr.ui.typeset import gate, themes

    scenes = [
        Scene(path, form, step, family, piece if path in ("stream", "native") else 0)
        for family in args.families
        for step in args.steps
        for form in args.forms
        for path in args.paths
        for piece in (args.pieces if path in ("stream", "native") else (0,))
    ]
    if args.tables == "all":
        tabled = set(args.steps)
    elif args.tables == "none":
        tabled = set()
    else:
        tabled = set(_ints(args.tables))

    before = uptime()
    state: dict[str, Any] = {"at": 0, "frame": 0, "phase": "wait", "made": None}
    facts: dict[str, str] = {}
    failed: list[str] = []
    results: list[tuple[Scene, dict[Any, Any], list[str]]] = []

    def setup(scene: Scene) -> None:
        typeset.configure(
            typeset=True,
            scale=1.0,
            proportional=scene.family,
            roles={typeset.Role.HEADING: typeset.RoleStyle(typeset.Slot.PROPORTIONAL, scene.step)},
        )

    def pre_frame() -> None:
        if state["phase"] == "wait" and state["at"] < len(scenes):
            setup(scenes[state["at"]])
        typeset.pre_frame()
        if args.keep:
            theme.keep_baked_sizes(gate.epoch())

    def entry_of(font: Any) -> int | None:
        if font is None:
            return None
        fonts = imgui.get_io().fonts.fonts
        return next((i for i, each in enumerate(fonts) if each.font_id == font.font_id), None)

    def describe() -> None:
        io = imgui.get_io()
        rows = []
        for index, font in enumerate(io.fonts.fonts):
            source = font.sources[0]
            rows.append(
                f"[{index}] {font.get_debug_name()}: {len(font.sources)} source(s), "
                f"pixel_snap_h {source.pixel_snap_h}, rasterizer_density "
                f"{source.rasterizer_density:g}, flags {font.flags}"
            )
        facts["atlas entries"] = "\n    " + "\n    ".join(rows)
        facts["library faces"] = ", ".join(
            f"{face} -> [{entry_of(gate.face_font(face))}]"
            for face in ("PROSE", "PROSE_BOLD", "PROSE_ITALIC", "CODE", "CODE_BOLD")
        )
        facts["loader"] = f"{io.fonts.font_loader_name}, atlas flags {io.fonts.font_loader_flags}"
        scale = io.display_framebuffer_scale
        facts["density"] = (
            f"display {io.display_size.x:g}x{io.display_size.y:g}, framebuffer scale "
            f"{scale.x:g}x{scale.y:g}, font size {imgui.get_font_size():g}, "
            f"style.font_scale_dpi {imgui.get_style().font_scale_dpi:g}"
        )
        try:
            from OpenGL import GL

            facts["renderer"] = GL.glGetString(GL.GL_RENDERER).decode()
        except Exception as exc:  # noqa: BLE001 -- a fact not read is said, not fatal
            facts["renderer"] = f"not read ({type(exc).__name__})"

    def heading_role(scene: Scene) -> str:
        return f"HEADING_{scene.form[1:]}" if scene.form.startswith("h") else "PARAGRAPH"

    def make(scene: Scene) -> dict[str, Any]:
        """
        What a scene draws and what was seen of it so far.
        """
        source = markdown_for(scene.form)
        made: dict[str, Any] = {
            "cache": typeset.ViewCache(),
            "source": source,
            "seen": {},
            "notes": [],
            "roomed": args.room == "none",
            "textures": [texture(imgui)],
        }
        if scene.path == "stream":
            made["pieces"] = pieces_of(source, scene.piece)
            made["fed"] = 0
            second = source.index(heading_line(scene.form, HEADS[1]))
            made["room_at"] = pieces_before(made["pieces"], second)
        elif scene.path == "native":
            # The same words without their marks, a line a block.
            lines = [INTRO.replace("*", "")]
            for head, body in zip(HEADS, BODIES, strict=True):
                lines += [head, body.replace("*", "").replace("\n", " ")]
            made["lines"] = lines
            plain = "\n".join(lines)
            made["pieces"] = pieces_of(plain, scene.piece)
            made["fed"] = 0
            made["room_at"] = pieces_before(made["pieces"], plain.index(HEADS[1]))
            made["tall"] = round(asked_px(themes.document_theme(), heading_role(scene), bold=True))
        else:
            made["roomed"] = True
        if scene.path == "direct":
            from advtext import DARK_PALETTE, Align, Faces, Spacing, Theme, TypesetView

            # The seam's own theme is read for the size to ask of the library
            # without it: the heading level's, or the paragraph's for a form
            # that is not a heading.
            tall = round(asked_px(themes.document_theme(), heading_role(scene), bold=True))
            prose_px = typeset.px(typeset.Role.PROSE)
            bold = Theme.at_pixel_height(
                tall, faces=Faces(), align=Align.LEFT, spacing=Spacing.FRENCH
            )
            own = Theme.at_pixel_height(
                prose_px, faces=Faces(), align=Align.LEFT, spacing=Spacing.FRENCH
            )
            made["direct"] = {
                f"bold paragraph, Theme.at_pixel_height({tall})": (
                    TypesetView(markdown_for("bold"), theme=bold, palette=DARK_PALETTE),
                    bold,
                ),
                f"this markdown, Theme.at_pixel_height({prose_px:g})": (
                    TypesetView(source, theme=own, palette=DARK_PALETTE),
                    own,
                ),
            }
        return made

    def draw_native(scene: Scene, made: dict[str, Any], feed: bool) -> dict[int, tuple[int, int]]:
        """
        The scene's words with no library: each heading in the bold entry at the
        heading's size, each list in it at the body's. Answers each heading on
        show's range of vertices in the draw list.
        """
        if feed and made["fed"] < len(made["pieces"]):
            made["fed"] += 1
        left = len("".join(made["pieces"][: made["fed"]]))
        bold = gate.face_font("PROSE_BOLD")
        draw_list = imgui.get_window_draw_list()
        spans: dict[int, tuple[int, int]] = {}
        lines = made["lines"]
        for at, line in enumerate(lines):
            shown = line[: max(0, left)]
            # The line and the newline after it.
            left -= len(line) + 1
            if not shown.strip():
                continue
            is_head = at % 2 == 1
            tall = float(made["tall"]) if is_head else typeset.px(typeset.Role.PROSE)
            imgui.push_font(bold, tall)
            start = draw_list.vtx_buffer.size()
            imgui.text_unformatted(shown)
            if is_head:
                spans[at // 2] = (start, draw_list.vtx_buffer.size())
                made.setdefault("shown", {})[at // 2] = shown
            imgui.pop_font()
        return spans

    def draw(scene: Scene, made: dict[str, Any], feed: bool) -> dict[str, tuple[Any, Any]]:
        """
        Draw the scene at the cursor. Each view drawn, by name, with its theme.
        """
        cache = made["cache"]
        if scene.path == "settled":
            parts = made.setdefault("parts", (typeset.Part(made["source"]),))
            typeset.document(cache, "closed", parts, rests=True, apart=True)
            shown = cache.views("closed")
            return {"": (shown[0], themes.document_theme())} if shown else {}
        if scene.path == "stream":
            stream = typeset.stream(cache, "open")
            if feed and made["fed"] < len(made["pieces"]):
                stream.append(made["pieces"][made["fed"]])
                made["fed"] += 1
            stream.draw(ink=None)
            return {"": (stream.view, themes.document_theme())} if stream.view is not None else {}
        from advtext import DARK_PALETTE

        for view, _ in made["direct"].values():
            view.draw(palette=DARK_PALETTE, width_px=args.width - 40.0)
        return dict(made["direct"])

    def read(
        views: dict[str, tuple[Any, Any]], quads: Any
    ) -> dict[tuple[str, int], tuple[list[Quad], float, str]]:
        """
        Every heading on show, by view name and which heading: its glyphs, the
        pixel height its theme asks, and its block's role.
        """
        glyphs = atlas_glyphs(imgui)
        size = texture(imgui)[1:]
        out = {}
        for name, (view, view_theme) in views.items():
            for which, block in heading_blocks(view).items():
                role = view.block_role(block)
                strong = not role.startswith("HEADING")
                got = read_heading(view, block, quads, glyphs, size)
                out[(name, which)] = (got, asked_px(view_theme, role, strong), role)
        return out

    def read_native(
        made: dict[str, Any], spans: dict[int, tuple[int, int]], draw_list: Any
    ) -> dict[tuple[str, int], tuple[list[Quad], float, str]]:
        glyphs = atlas_glyphs(imgui)
        size = texture(imgui)[1:]
        white = imgui.get_font_tex_uv_white_pixel()
        out = {}
        for which, (start, stop) in spans.items():
            quads = textured_quads(draw_list, start, stop, white)
            got = read_line(made["shown"][which], quads, glyphs, size)
            out[("push_font + text_unformatted", which)] = (got, float(made["tall"]), "no library")
        return out

    def make_room(made: dict[str, Any], frame: int) -> bool:
        """
        One frame of making room at the place the scene asked for it. Whether
        it is done.
        """
        note = made["notes"]
        if "room_from" not in made:
            made["room_from"] = (frame, texture(imgui))
            note.append(f"frame {frame}, before room is made, the atlas holds: {held(imgui)}")
        if args.room == "forced":
            imgui.internal.im_font_atlas_texture_make_space(imgui.get_io().fonts)
            note.append(
                f"frame {frame}: im_font_atlas_texture_make_space called;  texture "
                f"{made['room_from'][1]} -> {texture(imgui)}"
            )
            return True
        # Filled: the chrome's font at a size nothing has used, every glyph of it.
        tries = frame - made["room_from"][0]
        if texture(imgui) != made["room_from"][1] or tries >= FILL_FRAMES:
            note.append(
                f"frame {frame}: after {tries} frames of the chrome's font at "
                f"{FILL_PX:g} px and up, texture {made['room_from'][1]} -> {texture(imgui)}"
            )
            return True
        imgui.push_font(None, FILL_PX + 2.0 * tries)
        imgui.text_unformatted(ASCII)
        imgui.pop_font()
        return False

    def finish(scene: Scene, made: dict[str, Any]) -> None:
        notes = list(made["notes"])
        changes = [b for a, b in pairwise(made["textures"]) if a != b]
        notes.append(
            f"atlas texture (id, width, height) {made['textures'][0]} at the scene's start, "
            f"{len(changes)} change(s) during it{': ' + str(changes) if changes else ''}"
        )
        notes.append(f"at the scene's end the atlas holds: {held(imgui)}")
        if not made["seen"]:
            failed.append(f"{scene.label}: no heading was found in any view")
        results.append((scene, made["seen"], notes))
        made["cache"].clear()
        for view, _ in made.get("direct", {}).values():
            view.close()

    def gui() -> None:
        if failed or state["at"] >= len(scenes):
            hello_imgui.get_runner_params().app_shall_exit = True
            return
        scene = scenes[state["at"]]
        state["frame"] += 1
        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(imgui.get_io().display_size)
        imgui.begin(
            "measure",
            None,
            imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_saved_settings.value,
        )
        if state["phase"] == "wait":
            # One frame for the fonts of a family just chosen to be loaded
            # between frames and for the theme to follow the UI font's size,
            # then the decoys, each a frame, and frames without them.
            at = state["frame"] - 2
            if 0 <= at < args.decoys:
                imgui.push_font(None, DECOY_PX + at)
                imgui.text_unformatted("decoy")
                imgui.pop_font()
            if at >= args.decoys + (3 if args.decoys else 0):
                state.update(phase="draw", frame=0)
            imgui.end()
            return
        flags = (
            imgui.WindowFlags_.always_vertical_scrollbar.value
            | imgui.WindowFlags_.horizontal_scrollbar.value
        )
        done = False
        try:
            if not typeset.enabled(typeset.Pane.CONTEXT):
                failed.append(typeset.unavailable_reason() or "typeset text is not enabled")
                return
            if not facts:
                describe()
            if state["made"] is None:
                state["made"] = make(scene)
            made = state["made"]
            if imgui.begin_child("##transcript", imgui.ImVec2(args.width, 0), 0, flags):
                draw_list = imgui.get_window_draw_list()
                start = draw_list.vtx_buffer.size()
                making = not made["roomed"] and made["fed"] >= made["room_at"]
                if scene.path == "native":
                    spans = draw_native(scene, made, feed=not making)
                    views = {}
                else:
                    views = draw(scene, made, feed=not making)
                if not typeset.can_draw() or gate._GATE.stopping is not None:
                    failed.append(f"{scene.label}: {typeset.unavailable_reason()}")
                    return
                arriving = "pieces" in made and made["fed"] < len(made["pieces"])
                if not arriving and not making:
                    made["settled"] = made.get("settled", 0) + 1
                if arriving or making or made.get("settled", 0) in (1, SETTLE_FRAMES):
                    if scene.path == "native":
                        found = read_native(made, spans, draw_list)
                    else:
                        quads = textured_quads(
                            draw_list,
                            start,
                            draw_list.vtx_buffer.size(),
                            imgui.get_font_tex_uv_white_pixel(),
                        )
                        found = read(views, quads)
                    for key, got in found.items():
                        made["seen"].setdefault(key, []).append((state["frame"], got))
                if making:
                    made["roomed"] = make_room(made, state["frame"])
                if made["textures"][-1] != texture(imgui):
                    made["textures"].append(texture(imgui))
                done = made.get("settled", 0) >= SETTLE_FRAMES
        except Unread as exc:
            failed.append(f"{scene.label}, frame {state['frame']}: {exc}")
        finally:
            imgui.end_child()
            imgui.end()
        if done:
            finish(scene, state["made"])
            state.update(made=None, phase="wait", frame=0, at=state["at"] + 1)

    typeset.configure(typeset=True)
    run_window("pptmstr: heading glyphs", gui, pre_frame)
    after = uptime()

    names = {
        quad.name
        for _, seen, _ in results
        for frames in seen.values()
        for _, (got, _, _) in frames
        for quad in got
        if quad.entry >= 0
    }
    fresh, trouble = fresh_glyphs(names, args.families) if names else ({}, [])
    after_reference = uptime()

    import advtext
    from advtext import typesetter

    print(
        "measure_heading_glyphs: sources, sizes, bitmaps and pens from the draw list and the "
        "atlas; no pixel was read"
    )
    print(f"  uptime before: {before}")
    print(f"  uptime after:  {after}")
    print(f"  uptime after the fresh processes: {after_reference}")
    print(f"  display: {os.environ.get('DISPLAY', 'unset')};  python {sys.version.split()[0]}")
    print(f"  advtext imported from {Path(advtext.__file__).resolve().parent}")
    print(f"  imgui_bundle {imgui_bundle.__version__}, Dear ImGui {imgui.get_version()}")
    print(f"  IMTYPESET_LIBRARY={os.environ['IMTYPESET_LIBRARY']}")
    ts = typesetter.module()
    if ts is not None:
        info = ts.host_info()
        print(f"  imtypeset {info.get('imtypeset_version')}, rasteriser {info.get('rasteriser')}")
    built = PAIR / "BUILT-FROM"
    if built.is_file():
        for line in built.read_text(encoding="utf-8").splitlines():
            print(f"    BUILT-FROM {line}")
    for name, value in facts.items():
        print(f"  {name}: {value}")
    print(
        f"  child window {args.width:g} px wide;  decoys {args.decoys};  room {args.room};  "
        f"keep {args.keep};  "
        f"scenes in this order: {len(scenes)}"
    )
    print(
        f"  fresh glyphs: {len(fresh)} read from {len(args.families)} other process(es) for "
        f"{sorted(names)} at {REFERENCE_SIZES.start}..{REFERENCE_SIZES.stop - 1} px"
    )
    for line in trouble:
        print(f"  FRESH GLYPHS NOT READ: {line}")
    print()

    mixed: list[str] = []
    wrong: list[str] = []
    for scene, seen, notes in results:
        print(scene.label)
        for note in notes:
            print(f"   {note}")
        for (name, which), frames in seen.items():
            first_frame = frames[0][0]
            last_frame, (last, asked, role) = frames[-1]
            where = f" [{name}]" if name else ""
            print(
                f"   {HEADS[which]!r}{where}: {role}, asks {asked:.3f} px;  {len(last)} glyphs "
                f"read on frames {first_frame}..{last_frame} ({len(frames)} readings)"
            )
            print(f"      {summary(last, scene.family, fresh)}")
            # A character's glyph over the frames it was read on: what it was
            # drawn from, and its pen from the heading's first.
            identities: dict[int, set[Any]] = {}
            offsets: dict[int, set[float]] = {}
            # A reading with a quad no loaded glyph's uv matches says nothing
            # of where its glyphs came from, and is counted and left out.
            lost = [frame for frame, (got, _, _) in frames if any(q.entry < 0 for q in got)]
            for frame, (got, _, _) in frames:
                if frame in lost:
                    continue
                for quad in got:
                    identities.setdefault(quad.index, set()).add(quad.identity)
                    offsets.setdefault(quad.index, set()).add(round(quad.pen[0] - got[0].pen[0], 2))
            changed = sorted(index for index, ids in identities.items() if len(ids) > 1)
            moved = sorted(index for index, xs in offsets.items() if len(xs) > 1)
            by_index = {quad.index: quad.char for quad in last}
            print(
                f"      over the readings: with a quad whose uv is no loaded glyph's: {len(lost)}"
                f"{' (frames ' + ', '.join(str(f) for f in lost[:6]) + ')' if lost else ''};  "
                f"in the others, characters whose entry, baked size or quad size "
                f"differed between frames: {len(changed)} "
                f"{''.join(by_index.get(i, '?') for i in changed)!r};  whose pen moved against "
                f"the heading's first: {len(moved)} "
                f"{''.join(by_index.get(i, '?') for i in moved)!r}"
            )
            for frame, (got, _, _) in frames:
                if len({(quad.entry, quad.baked) for quad in got}) > 1:
                    mixed.append(f"{scene.label}, {HEADS[which]!r}{where}, frame {frame}")
                    break
            stale_now = [quad.char for quad in last if stale(quad, scene.family, fresh) is not None]
            if stale_now:
                wrong.append(
                    f"{scene.label}, {HEADS[which]!r}{where}: {len(stale_now)} of {len(last)} "
                    f"{''.join(stale_now)!r}"
                )
            if scene.step in tabled:
                print("\n".join(table(last, scene.family, fresh)))
        print()
    print(
        f"headings with more than one (atlas entry, baked size) among their glyphs, on any "
        f"frame read: {len(mixed)}"
    )
    for line in mixed:
        print(f"  {line}")
    print(
        f"headings with a glyph whose bitmap is not its entry's at its size in a fresh "
        f"process, at the last reading: {len(wrong)}"
    )
    for line in wrong:
        print(f"  {line}")
    if failed:
        for reason in failed:
            print(f"NOT READ: {reason}", file=sys.stderr)
        return 1 if not facts else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
