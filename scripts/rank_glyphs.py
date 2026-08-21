#!/usr/bin/env python3
"""
Rank the splash art's glyphs by how much ink each one puts in a cell.

The splash animation swaps a cell's character for any other glyph in the art's
inventory, without regard to brightness -- ``splash.py`` reads ``RANKS`` as one
flat pool, and the glitch that produces is the effect it is going for. What this
script still supplies is the inventory itself and a measured ink ordering of the
art's 165 non-space glyphs, and an ordering nobody measured is an ordering
somebody guessed.

**What the table is load-bearing for, now that substitution ignores it.** Three
things. It is the eligibility list: a cell whose character is absent from it never
animates. It refuses any glyph the face cannot draw, which is what keeps every
substitute a glyph that exists in the baked atlas. And the *order* the buckets are
emitted in fixes the flat pool's order, which fixes which glyph each cell shows on
each step -- so re-running this script with different parameters changes every
frame of the animation even though it cannot change the pool's contents.

**What this measures.** The signed area enclosed by the glyph's outline
(``fontTools.pens.areaPen.AreaPen``), absolute value, divided by the em square.
Counters carry the opposite winding to the outer contour, so they subtract:
``O`` comes out as -0.23817 + 0.11460 = -0.12357 em^2, not 0.238. The face is
Inconsolata-Medium from the imgui-bundle wheel -- the same file ``theme.py``
loads as ``Face.BODY`` -- whose advance is 500/1000 em for every glyph here, so
coverage *of the cell* is exactly twice these numbers and the ranking is the
same either way.

**What it approximates, and how badly.** Outline area is a proxy for rasterized
ink, not a measurement of it. fontTools has no rasterizer and nothing in this
environment does, so this is the honest ceiling rather than a shortcut:

* *Overlapping contours are counted twice.* Of the 45 composite glyphs in the
  inventory, exactly two have components whose bounding boxes intersect at all
  --- ``C`` with its cedilla in ``Ccedilla`` and ``ccedilla`` --- and those
  boxes meet only in a 228x10 and a 228x11 unit sliver at the baseline. So the
  worst overstatement any glyph here can carry is 0.0023 em^2 for ``C`` and
  0.0025 for ``c`` -- 2.0% and 2.6% of their own areas, against a bucket that
  spans 17.2%. Self-overlap within a single contour set is not checked and is
  assumed absent, as it is in any overlap-removed release build.
* *Antialiasing is not modelled.* At 16px a hairline stroke lights more pixels,
  more dimly, than its outline area predicts, so thin glyphs read slightly
  brighter on screen than they rank here.
* *Area says nothing about where the ink sits.* ``„`` and ``“`` are all but the
  same shape 0.5315 em apart -- 0.2840 and 0.2830 em tall, areas agreeing to
  within 0.088% -- so no area-based metric can ever separate them. A band that
  groups them is uniform in weight and spread across most of the cell in
  position, which is what the second stage of the partition below exists to
  bound.

**The partition, in two stages.** *Ink bands* are contiguous runs of the
area-sorted order chosen to minimise the *worst* within-band ink ratio, subject
to a floor on band size -- solved exactly by binary search over the candidate
ratios with a DP feasibility check, not by cutting the range into equal parts.
Equal-width cuts put ``pilcrow`` alone in the top band and equal-frequency cuts
put a 2.4x ink spread in the bottom one, so neither describes the inventory: one
band of the twelve would carry a single glyph and one would carry a span the
other eleven are chosen to avoid.

A band wider than ``DEFAULT_MAX_SPREAD`` in ink height is then *subdivided* by
the vertical centre of the ink bounding box, by the same exact method against a
difference rather than a ratio. Sub-buckets of one band overlap in area and are
therefore **not** separated dim-to-bright from each other -- only the bands are.
That is the property the second stage trades away, and ``RANKS`` does not claim
it; what survives is that no bucket is more than one band's ink ratio brighter
than any later bucket.

The floor binds, and the threshold is a target rather than a promise. Three of
the twelve bands measure above it and none can be brought under: a band of five
cannot split at all with a floor of three members, and the two that can still
carry one low-sitting mark (U+00B8, U+201A, U+201E) with no height-alike partner
to sit with. Their spreads land at 0.416, 0.4315 and 0.470 em, which the
per-bucket report prints on every run. That is a deliberate stop rather than an
unfinished one: the panel is meant to read as glitchy, so a band left wide in ink
height is within tolerance and the test bounding it is loose to match.

Usage:  .venv/bin/python scripts/rank_glyphs.py
        .venv/bin/python scripts/rank_glyphs.py --buckets 12 --min-size 3

Needs the ``dev`` extra (fontTools). Prints a per-glyph table, a per-bucket
report, and the literal to paste into ``pptmstr/ui/splash_art.py``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ART_PATH = ROOT / "pptmstr" / "ui" / "splash_art.txt"

# Twelve buckets over 165 glyphs, and the number is measured rather than chosen:
# with a floor of three members it is the knee of the curve. Eleven buckets admit
# a 22.7% within-bucket ink spread, twelve admit 17.2%, and thirteen only buys
# 16.6% while adding another three-member bucket. A floor of four is not
# reachable at any useful ratio -- the four dimmest glyphs already span 47.7%
# between them -- so three it is.
#
# The floor also decides how finely the subdivision stage can cut, and that is what
# ``tests/test_splash_art.py`` pins it by: the same twelve bands emit 14 buckets at a
# floor of 3, 16 at 2 and 25 at 1, all three at the identical 1.1724 ratio.
DEFAULT_BUCKETS = 12
DEFAULT_MIN_SIZE = 3

# The ink-height spread, in em, above which a band gets subdivided. 0.30 sits in a
# gap the measurement leaves rather than at a round number: of the twelve bands,
# nine spread 0.2775 em or less and three spread 0.4315 or more, so any threshold
# between those two selects the same three bands. It is a trigger, not a
# guarantee -- see the module docstring for why the floor stops all three short.
DEFAULT_MAX_SPREAD = 0.30


def font_path() -> Path:
    """
    Inconsolata-Medium as ``theme.load_fonts`` resolves it.

    ``theme.py`` asks hello_imgui for the relative asset ``fonts/Inconsolata-Medium.ttf``,
    which it finds in the wheel's own assets folder. Reaching for the same file by
    absolute path is what makes this script measure the face the UI actually draws
    with rather than a same-named copy.
    """
    import imgui_bundle

    return (
        Path(imgui_bundle.__file__).resolve().parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"
    )


def art_glyphs(path: Path = ART_PATH) -> list[str]:
    """Every distinct character in the art except the newline and the space."""
    text = path.read_text(encoding="utf-8")
    return sorted(set(text) - {"\n", " "})


def ink_areas(chars: list[str], path: Path | None = None) -> dict[str, float]:
    """
    Outline area per character, as a fraction of the em square.

    Raises on a character the face cannot draw. A missing glyph would otherwise
    fall out of the table and out of the animation with no sign that it had.
    """
    from fontTools.pens.areaPen import AreaPen
    from fontTools.ttLib import TTFont

    font = TTFont(path or font_path())
    upem = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()

    areas: dict[str, float] = {}
    for char in chars:
        name = cmap.get(ord(char))
        if name is None:
            raise SystemExit(f"U+{ord(char):04X} ({char!r}) is not in the face's cmap")
        pen = AreaPen(glyphs)
        glyphs[name].draw(pen)
        areas[char] = abs(pen.value) / float(upem * upem)
    return areas


def ink_centres(chars: list[str], path: Path | None = None) -> dict[str, float]:
    """
    The vertical centre of each character's ink bounding box, in em above the
    baseline.

    ``BoundsPen`` rather than ``ControlBoundsPen`` for the tight outline bounds and
    not the control-point hull, which overshoots a curve. On *this* face the choice
    is not visible: the two agree to 0.0000 em on all 165 glyphs, Inconsolata
    carrying on-curve points at its vertical extrema. It is the pen that does not
    depend on that staying true of the next face, and swapping it changes nothing
    the pin in ``tests/test_splash_art.py`` would report.

    Raises on a character that draws nothing. Every glyph here is inked, so an
    empty outline means the cmap lookup found the wrong one, and returning some
    default centre for it would place it in a height group by accident.
    """
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.ttLib import TTFont

    font = TTFont(path or font_path())
    upem = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()

    centres: dict[str, float] = {}
    for char in chars:
        name = cmap.get(ord(char))
        if name is None:
            raise SystemExit(f"U+{ord(char):04X} ({char!r}) is not in the face's cmap")
        pen = BoundsPen(glyphs)
        glyphs[name].draw(pen)
        if pen.bounds is None:
            raise SystemExit(f"U+{ord(char):04X} ({char!r}) draws no ink")
        _, y_min, _, y_max = pen.bounds
        centres[char] = (y_min + y_max) / 2.0 / float(upem)
    return centres


def _cut(
    values: list[float], fits: Callable[[float, float], bool], buckets: int, min_size: int
) -> list[int] | None:
    """
    Cut points for ``buckets`` contiguous runs of ``values`` (sorted ascending)
    with every run at least ``min_size`` long and accepted by ``fits``.

    ``fits(lowest, highest)`` decides whether one run is tight enough. It must be
    monotone -- false for a run makes it false for every longer run starting at the
    same place -- because the scan breaks at the first refusal rather than testing
    the rest. Both callers satisfy that: ``values`` is ascending, so extending a run
    can only raise ``highest``.

    Returns the run boundaries, or None when no such partition exists.
    """
    n = len(values)
    # layers[b][end] = the start index of the b-th run that ends at `end`
    layers: list[dict[int, int]] = [{0: 0}]
    for _ in range(buckets):
        nxt: dict[int, int] = {}
        for start in layers[-1]:
            for end in range(start + min_size, n + 1):
                if not fits(values[start], values[end - 1]):
                    break
                nxt.setdefault(end, start)
        layers.append(nxt)

    if n not in layers[buckets]:
        return None
    cuts = [n]
    at = n
    for b in range(buckets, 0, -1):
        at = layers[b][at]
        cuts.append(at)
    return cuts[::-1]


def _tightest_cut(
    values: list[float],
    candidates: list[float],
    fits_within: Callable[[float], Callable[[float, float], bool]],
    buckets: int,
    min_size: int,
) -> list[int] | None:
    """
    The cut into ``buckets`` runs whose worst run is tightest, found by binary
    search over ``candidates`` (sorted ascending).

    Feasibility is monotone in the bound -- a partition good enough at one bound is
    good enough at every larger one -- which is what makes the search exact rather
    than a heuristic. Drawing the bound from the candidate set rather than from a
    real interval is what makes it terminate on the optimum instead of near it.

    Returns None when no partition exists at even the loosest candidate.
    """
    if _cut(values, fits_within(candidates[-1]), buckets, min_size) is None:
        return None
    lo, hi = 0, len(candidates) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if _cut(values, fits_within(candidates[mid]), buckets, min_size) is not None:
            hi = mid
        else:
            lo = mid + 1
    return _cut(values, fits_within(candidates[lo]), buckets, min_size)


def partition(
    ranked: list[tuple[str, float]], buckets: int, min_size: int
) -> list[list[tuple[str, float]]]:
    """
    Split the sorted glyphs into buckets minimising the worst within-bucket ratio.

    The objective is a *ratio* rather than a difference because brightness
    discrimination is roughly proportional: 0.011 against 0.017 em^2 is as far
    apart to the eye as 0.090 against 0.140, and an additive objective would
    spend all its resolution at the bright end where the eye needs least. That is
    a statement about the measurement, not about the animation -- the renderer
    does not read these bands, and what the choice buys now is a partition whose
    bands describe the inventory evenly enough to be worth pinning.
    """
    values = [area for _, area in ranked]
    candidates = sorted(
        {values[j] / values[i] for i in range(len(values)) for j in range(i, len(values))}
    )
    cuts = _tightest_cut(
        values, candidates, lambda ratio: lambda lo, hi: hi <= lo * ratio, buckets, min_size
    )
    if cuts is None:
        raise SystemExit(f"no partition into {buckets} buckets of at least {min_size} exists")
    return [ranked[cuts[k] : cuts[k + 1]] for k in range(buckets)]


def subdivide(
    band: list[tuple[str, float]],
    centres: Mapping[str, float],
    min_size: int,
    max_spread: float,
) -> list[list[tuple[str, float]]]:
    """
    Split one ink band into sub-buckets of similar ink height, low to high.

    A band under ``max_spread`` is returned whole: subdividing a band whose glyphs
    already sit at the same height buys nothing and costs a bucket. Otherwise the
    band is cut into as few sub-buckets as reach the tightest achievable worst
    spread, which for the three bands that trigger this is two.

    Members keep their area order inside each sub-bucket, so a printed bucket still
    reads dim to bright even though the sub-buckets themselves no longer do.
    """
    order = sorted(band, key=lambda kv: (centres[kv[0]], ord(kv[0])))
    values = [centres[char] for char, _ in order]
    if values[-1] - values[0] <= max_spread:
        return [list(band)]

    best: list[list[tuple[str, float]]] | None = None
    best_worst = values[-1] - values[0]
    candidates = sorted(
        {values[j] - values[i] for i in range(len(values)) for j in range(i, len(values))}
    )
    for runs in range(2, len(order) // min_size + 1):
        cuts = _tightest_cut(
            values, candidates, lambda spread: lambda lo, hi: hi - lo <= spread, runs, min_size
        )
        if cuts is None:
            continue
        groups = [order[cuts[k] : cuts[k + 1]] for k in range(runs)]
        # `order` is ascending in centre, so each group's own ends are its extremes.
        worst = max(centres[g[-1][0]] - centres[g[0][0]] for g in groups)
        if worst < best_worst:
            best, best_worst = groups, worst

    if best is None:
        return [list(band)]
    area_of = {char: area for char, area in band}
    return [sorted(g, key=lambda kv: (area_of[kv[0]], ord(kv[0]))) for g in best]


def rank(
    buckets: int = DEFAULT_BUCKETS,
    min_size: int = DEFAULT_MIN_SIZE,
    max_spread: float = DEFAULT_MAX_SPREAD,
) -> tuple[list[list[tuple[str, float]]], dict[str, float], dict[str, float]]:
    """
    The whole pipeline: the art's glyphs, measured, banded by ink and subdivided
    by ink height. Returns the buckets with the two measurements behind them.

    One function rather than a sequence of calls each caller repeats, because the
    other caller is the test that pins the checked-in table to this script. A pin
    that reassembles the pipeline itself pins a copy of it, and the copy is free to
    stop matching -- which is the same duplicated-constant hazard the pin exists to
    close, moved one level up.
    """
    chars = art_glyphs()
    areas = ink_areas(chars)
    centres = ink_centres(chars)
    # Ties broken by codepoint so the table is reproducible: `grave` and `acute`
    # have identical area, and a set-ordered tie would rewrite the literal on
    # every run for no reason.
    ranked = sorted(areas.items(), key=lambda kv: (kv[1], ord(kv[0])))
    bands = partition(ranked, buckets, min_size)
    return (
        [sub for band in bands for sub in subdivide(band, centres, min_size, max_spread)],
        areas,
        centres,
    )


def _tokens(chars: list[str]) -> list[str]:
    """
    Each character as it should appear inside a Python string literal, with
    everything outside printable ASCII escaped.

    The inventory contains U+00AD, which is *invisible* in an editor, and 21
    CP1252-range punctuation glyphs whose ASCII lookalikes are one keystroke away
    (U+2019 for an apostrophe, U+2013 for a hyphen). Escaping is what makes the
    checked-in table something a diff can show and an editor cannot quietly
    corrupt.
    """
    out = []
    for char in chars:
        point = ord(char)
        if char in ('"', "\\"):
            out.append("\\" + char)
        elif 0x20 < point < 0x7F:
            out.append(char)
        elif point <= 0xFF:
            out.append(f"\\x{point:02x}")
        else:
            out.append(f"\\u{point:04x}")
    return out


def _literal(chars: list[str], indent: str, width: int = 96) -> list[str]:
    """
    A bucket as source lines, broken at escape boundaries to clear the line limit.

    Wrapping has to be done here rather than left to the formatter: black does not
    split a long string literal, and the widest bucket escapes out to nearly twice
    the 100-column limit.
    """
    tokens = _tokens(chars)
    room = width - len(indent) - 2
    lines: list[str] = []
    current = ""
    for token in tokens:
        if current and len(current) + len(token) > room:
            lines.append(current)
            current = ""
        current += token
    lines.append(current)

    if len(lines) == 1:
        return [f'{indent}"{lines[0]}",']
    body = [f'{indent}    "{line}"' for line in lines]
    return [f"{indent}(", *body, f"{indent}),"]


def main(argv: Sequence[str] | None = None) -> int:
    """
    Print the report and the literal to paste into ``pptmstr/ui/splash_art.py``.

    ``argv`` is a parameter only so a test can invoke this the way a person does.
    Left to read ``sys.argv``, the entry point is unreachable from pytest -- whose
    own arguments argparse would try to parse -- and the checked-in table would
    stay pinned to ``rank()`` while what a maintainer actually copies is stdout.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--buckets", type=int, default=DEFAULT_BUCKETS)
    ap.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE)
    ap.add_argument("--max-spread", type=float, default=DEFAULT_MAX_SPREAD)
    args = ap.parse_args(argv)

    buckets, areas, centres = rank(args.buckets, args.min_size, args.max_spread)
    ranked = sorted(areas.items(), key=lambda kv: (kv[1], ord(kv[0])))

    print(f"font: {font_path()}")
    print("metrics: |AreaPen outline area| / em^2, ink-bbox centre in em")
    print(f"         ({len(ranked)} glyphs from {ART_PATH.name})")
    print()
    for char, area in ranked:
        print(f"  U+{ord(char):04X}  {char}  area={area:.5f}  centre={centres[char]:+.4f}")

    print()
    print(f"{len(buckets)} buckets, dim -> bright by ink band:")
    worst_ratio = 0.0
    worst_spread = 0.0
    for index, bucket in enumerate(buckets):
        span = bucket[-1][1] / bucket[0][1]
        heights = [centres[char] for char, _ in bucket]
        spread = max(heights) - min(heights)
        worst_ratio = max(worst_ratio, span)
        worst_spread = max(worst_spread, spread)
        members = "".join(char for char, _ in bucket)
        print(
            f"  {index:2d}  n={len(bucket):2d}  "
            f"{bucket[0][1]:.5f}..{bucket[-1][1]:.5f}  x{span:.3f}  "
            f"dy={spread:.4f}  {members}"
        )
    print()
    print(f"worst within-bucket ink ratio: {worst_ratio:.4f}")
    print(f"worst within-bucket ink-height spread: {worst_spread:.4f} em")
    print(f"smallest bucket: {min(len(b) for b in buckets)}")
    print()
    print("-- paste into pptmstr/ui/splash_art.py " + "-" * 40)
    print("_BUCKETS: tuple[str, ...] = (")
    for bucket in buckets:
        for line in _literal([char for char, _ in bucket], indent="    "):
            print(line)
    print(")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
