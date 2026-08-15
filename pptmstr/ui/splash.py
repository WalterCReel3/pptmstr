"""
The animation core for the cold-start splash: cycling text art, a breathing quote, and
the sizing arithmetic that decides whether either of them fits.

The panel is NEEDS YOU, docked in MainDockSpace, and it shows this only when there are
no sessions at all -- an empty queue with a live fleet is a different pane. Which host
it is matters to every number in the fitting section, because MainDockSpace is wide and
short where DETAIL was narrow and tall, and that inverts which axis binds.

No ImGui here, and no clock read. ``now`` is a parameter for the same reason the
reducer takes one (STYLE.md §1): a core that reaches for ``time.monotonic()`` cannot
be asked what it renders at t=4.5s, and every guarantee below -- stable membership,
in-bucket substitution, a readable floor under the quote -- is a statement about two
different ``now`` values that only a caller-supplied clock lets a test make.

The art data itself lives in ``splash_art.py``; this module never imports it. It takes
``art``/``ranks``/``rank_of`` as arguments so the behaviour is exercisable against
small fixtures rather than against 61x72 of real glyphs.

Everything here is pure. The one piece of mutable state, ``ArtFrames``, is a
caller-held memo and is documented as such where it is defined.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

# -- rate ----------------------------------------------------------------------
#
# With no sessions there is nothing active, so app.py sets
# ``runner.fps_idling.enable_idling = not snap.any_active`` and the window redraws at
# ``settings.fps_idle`` -- 9.0 by default. The splash is therefore animating at ~9fps,
# not 60, and a rate chosen against 60 would read as a strobe.
#
# This is the tick rate of the whole field, not the rate any one cell changes at: a cell
# substitutes once every ``_CELL_PERIODS`` ticks of its own, so the fastest cell moves at
# 1.5Hz and the slowest at under 0.5Hz. Three ticks a second puts three idle frames on
# the shortest substitution, which is slow enough to read as a deliberate flicker rather
# than as tearing. Do not raise this to "smooth it out" -- at 9fps anything above ~4
# aliases against the frame rate and the art crawls.
STEPS_PER_SECOND = 3.0

# -- the cycling set -----------------------------------------------------------
#
# What fraction of the ranked, non-space cells take part. Membership is decided once
# per cell position from a hash and never changes: a set that is re-rolled per frame
# reads as uniform noise over the whole image, because every cell is eventually in it.
# Holding a third of the cells still-and-cycling and the rest simply still is what
# makes the movement look like texture rather than static.
CYCLING_FRACTION = 0.34

# Resolution of the membership test. 1024 buckets is far finer than the ~4.4k cells
# need and keeps CYCLING_FRACTION honest to two decimal places.
_MEMBERSHIP_BUCKETS = 1024
_MEMBERSHIP_THRESHOLD = CYCLING_FRACTION * _MEMBERSHIP_BUCKETS

# -- the stagger ---------------------------------------------------------------
#
# How many ticks apart one cell's substitutions are. A cell draws its period and its
# alignment within that period from its own hash, so on any tick only the cells whose
# period divides that tick move -- about three in ten of the cycling set, and roughly the
# same three in ten on every tick, which is a field that shimmers rather than one that
# blinks.
#
# This is the load-bearing part of the animation, not a refinement of it. A single global
# step advances every cycling cell on the same frame: a thousand glyphs changing together
# at STEPS_PER_SECOND is full-field flicker at ~3Hz, which is inside the band
# photosensitivity guidance treats as a risk, on a panel that is on screen for as long as
# the app has no sessions. The tests in the "only a fraction of the cycling cells change"
# group hold both the fraction and its steadiness down, and they are not cosmetic.
#
# Pairwise coprime, so which cells move repeats only every lcm(2,3,5,7) = 210 ticks --
# 70 seconds -- rather than on the short cycle a set sharing a factor would realign on.
_CELL_PERIODS: tuple[int, ...] = (2, 3, 5, 7)


def _cell_hash(row: int, col: int) -> int:
    """
    A stable 32-bit mix of a cell's coordinates.

    Written out rather than delegated to ``hash()`` on purpose. CPython's ``hash`` is
    only guaranteed stable for ints within one build, and the *point* of this value is
    that a cell's membership and phase are the same on every run and every machine --
    a property that a test can pin only if the mixing is ours.
    """
    x = (row * 0x9E3779B1 + col * 0x85EBCA77 + 0x165667B1) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x2C1B3C6D) & 0xFFFFFFFF
    x ^= x >> 12
    x = (x * 0x297A2D39) & 0xFFFFFFFF
    x ^= x >> 15
    return x


def step_index(now: float) -> int:
    """
    The substitution step ``now`` falls in.

    Exposed because it is the cache key: two ``now`` values in the same step produce
    byte-identical frames, which is what makes ``ArtFrames`` a memo rather than an
    approximation.
    """
    return int(now * STEPS_PER_SECOND)


class _Rhythm(NamedTuple):
    """
    Everything a cell's *position* decides about its animation.

    ``offset`` is which tick within ``period`` the cell moves on, and ``phase`` is which
    glyph of its bucket it starts from. The two are separate axes: ``phase`` alone picks
    what is shown and never when it changes, so a stagger built out of it leaves the
    whole field substituting on the same frame.
    """

    cycling: bool
    period: int
    offset: int
    phase: int


def _rhythm(row: int, col: int) -> _Rhythm:
    """
    A cell's four animation facts, from one hash and one call.

    One call so ``is_cycling`` and ``art_frame`` cannot come to different conclusions
    about the same cell -- an unpinned duplicate of a rule is the smell STYLE.md §3
    names, and here the two readers are a public predicate and the renderer that has to
    agree with it.

    The four fields take disjoint bits of the 32:

        0-9   membership, against _MEMBERSHIP_BUCKETS
        10-17 which period the cell keeps
        18-23 where in that period it lands
        24-31 which glyph of its bucket it starts on

    Disjoint rather than convenient, and it is ``offset`` that makes this matter rather
    than a general tidiness argument. Taking the offset from the period's own bits sends
    every cell of a period to the same tick of it -- the field then changes in a lump a
    quarter its size and goes quiet in between, which is a beat at half the tick rate
    even though no more cells are moving overall. The bound in
    ``test_only_a_fraction_of_the_cycling_cells_change_on_any_one_step`` is on the
    *steadiness* of the rate for that reason, and it is what pins these bit ranges.
    """
    h = _cell_hash(row, col)
    period = _CELL_PERIODS[(h >> 10 & 0xFF) % len(_CELL_PERIODS)]
    return _Rhythm(
        cycling=h % _MEMBERSHIP_BUCKETS < _MEMBERSHIP_THRESHOLD,
        period=period,
        offset=(h >> 18 & 0x3F) % period,
        phase=h >> 24,
    )


def is_cycling(row: int, col: int) -> bool:
    """
    Whether the cell at ``(row, col)`` ever substitutes.

    A function of position alone -- not of the character there, and not of time.
    """
    return _rhythm(row, col).cycling


def art_frame(
    art: Sequence[str],
    rank_of: Mapping[str, int],
    ranks: Sequence[Sequence[str]],
    now: float,
) -> tuple[str, ...]:
    """
    ``art`` with the cycling cells swapped for other glyphs of the same brightness.

    A cell is substituted only when all of these hold, and every failure is a no-op
    rather than an error (STYLE.md §1, "an intent for something that does not exist is
    a no-op"): the character is not a space, it has a rank, that rank indexes a real
    bucket, the cell is in the cycling set, and the glyph its rhythm selects is not
    itself a space. An unranked glyph is a hole in the ranking table, not a bug worth
    aborting a frame for.

    Space is guarded in both directions, even though the contract says U+0020 is absent
    from ``rank_of``. The art's shape *is* its whitespace, so a ranking table that grew
    a space entry would dissolve the image from either side -- a space overwritten with
    ink erodes the negative space, and ink replaced by a space punches holes in the
    silhouette. Both are silent failures, and the second is the one that is visible at a
    glance, which is worth one comparison per substitution to make impossible.

    A cell advances only on ticks its own period divides, so what changes between two
    consecutive steps is a fraction of the cycling set rather than all of it. See
    ``_CELL_PERIODS``.
    """
    step = step_index(now)
    out: list[str] = []
    for row, line in enumerate(art):
        cells: list[str] | None = None
        for col, ch in enumerate(line):
            if ch == " ":
                continue
            bucket_index = rank_of.get(ch)
            if bucket_index is None or not 0 <= bucket_index < len(ranks):
                continue
            bucket = ranks[bucket_index]
            if not bucket:
                continue
            rhythm = _rhythm(row, col)
            if not rhythm.cycling:
                continue
            tick = (step + rhythm.offset) // rhythm.period
            substitute = bucket[(tick + rhythm.phase) % len(bucket)]
            if substitute == " ":
                continue
            if cells is None:
                cells = list(line)
            cells[col] = substitute
        out.append(line if cells is None else "".join(cells))
    return tuple(out)


class ArtFrames:
    """
    A one-entry memo over ``art_frame``, held by the pane rather than by this module.

    Keyed on the *step index*, never on ``now``: a key derived from a float would miss
    on every frame while producing an identical result, which is the worst of both. A
    step key means a hit is exact, and ``test_the_cached_path_matches_the_pure_one``
    pins that.

    Mutable, and deliberately so -- the same exception the store and the transcript
    take in STYLE.md §1. Keeping it out of ``art_frame`` keeps the pure function pure
    and keeps the cache's lifetime tied to the pane that owns it, so a theme switch or
    a pane teardown drops it without a global to invalidate.
    """

    __slots__ = ("_step", "_frame")

    def __init__(self) -> None:
        self._step: int | None = None
        self._frame: tuple[str, ...] = ()

    def frame(
        self,
        art: Sequence[str],
        rank_of: Mapping[str, int],
        ranks: Sequence[Sequence[str]],
        now: float,
    ) -> tuple[str, ...]:
        step = step_index(now)
        if step != self._step:
            self._frame = art_frame(art, rank_of, ranks, now)
            self._step = step
        return self._frame


# -- the quote -----------------------------------------------------------------

QUOTE_LINES: tuple[str, ...] = (
    "All things change in a dynamic environment.",
    "Your effort to remain what you are is what limits you.",
)

# The animation is per-character *alpha*, not per-character substitution. A splash
# that swapped glyphs in prose would change the thing the reader is mid-sentence
# through; alpha leaves the text and its metrics untouched, so nothing can reflow, no
# column can shift, and the two lines occupy the same pixels on every frame no matter
# what the animation is doing. It also costs the pane nothing new: theme.faded()
# already quantises alpha to twelve steps and memoises the packed colour, so a
# per-character tint is a dict hit per character.
#
# The floor is what makes "light" a constraint instead of a mood: no character is ever
# dimmer than this, so the quote is body text in every frame rather than only in the
# bright half of the sweep. It is a contrast number, not a taste one. tests/test_theme.py
# holds every palette's text/background pair at WCAG AA (4.5:1), and an alpha multiplier
# composites straight into that ratio -- so an alpha the theme tests never see is a way
# to fail their guarantee without touching a palette.
#
# 10/12 rather than a rounder number because theme.faded quantises to twelve steps: the
# value has to sit *on* a step, or the number picked here and the number drawn differ.
# Step 10 is the first that clears 4.5:1 on every palette against both the window
# background and the panel; step 9 leaves CDE at 4.49:1, which is the palette that
# decides this and is nobody's default. The cost is real and is the reason this is worth
# a comment: the sweep now runs across three quantisation steps instead of six, so it is
# half the gesture it was. A legible static quote would still beat an illegible animated
# one, and this is the version that is both.
QUOTE_BASE_ALPHA = 10.0 / 12.0

# A single highlight sweeps the two lines as one run of characters, line 1 then line 2,
# so it reads as one gesture crossing the block rather than two lines blinking. Eleven
# seconds is a little under nine characters a second: slow enough to be peripheral, and
# at 9fps still ~10 frames per character so the leading edge does not jump.
SHIMMER_PERIOD_SECONDS = 11.0

# Standard deviation of the highlight, in characters. Narrower than this and the sweep
# reads as a single blinking letter at 9fps.
SHIMMER_WIDTH_CHARS = 5.0


@dataclass(frozen=True, slots=True)
class QuoteFrame:
    """
    The quote and one alpha per character of it.

    ``lines[i][j]`` is drawn at ``alphas[i][j]``. ``lines`` is always ``QUOTE_LINES``
    -- it is carried here so the pane has a single object to read and cannot pair this
    frame's alphas with a different line list.
    """

    lines: tuple[str, ...]
    alphas: tuple[tuple[float, ...], ...]


def quote_frame(now: float) -> QuoteFrame:
    """
    The quote with a gaussian highlight sweeping across it.

    The sweep starts and ends off the ends of the text, so there is a quiet stretch of
    each cycle where the whole quote sits at the base alpha.
    """
    total = sum(len(line) for line in QUOTE_LINES)
    # Travel from one highlight-width before the first character to one after the
    # last, so the leading and trailing edges arrive and leave rather than popping in.
    travel = total + 2.0 * SHIMMER_WIDTH_CHARS
    phase = (now % SHIMMER_PERIOD_SECONDS) / SHIMMER_PERIOD_SECONDS
    pos = phase * travel - SHIMMER_WIDTH_CHARS

    alphas: list[tuple[float, ...]] = []
    index = 0
    for line in QUOTE_LINES:
        row: list[float] = []
        for _ in line:
            offset = (index - pos) / SHIMMER_WIDTH_CHARS
            peak = math.exp(-0.5 * offset * offset)
            row.append(QUOTE_BASE_ALPHA + (1.0 - QUOTE_BASE_ALPHA) * peak)
            index += 1
        alphas.append(tuple(row))
    return QuoteFrame(lines=QUOTE_LINES, alphas=tuple(alphas))


# -- fitting -------------------------------------------------------------------
#
# The two ImGui-side numbers below describe the face theme.py loads (Inconsolata-Medium,
# via hello_imgui) as ImGui *rasterises* it, which is not what the font's nominal metrics
# say. Nothing in this process can obtain them -- they need a live frame -- so the
# evidence is scripts/verify_splash.py, which opens one and prints
# ImFontBaked.get_char_advance and calc_text_size per size. No test in this repo pins
# them, and a test that recomputed them from these literals would only be pinning the
# literals to themselves.
#
# Width: an upper bound on the baked advance, and *only* a bound -- the run reports it as
# exactly 0.5*size at even sizes 6 to 32 and a pixel per character narrower from 34 to 48.
# Narrower is the safe direction (required_extent over-reserves and the art centres up to
# half a column off), so what this constant has to be is never-exceeded, which the script
# checks at every size fit_size can return.
#
# The recorded limit of over-reserving: above size 32 the estimate is 72px wide of the
# 61x72 art, one pixel a column, so fit_size can decline a pane the art would have fitted
# by up to that much. Size 34 needs 34 * 61.049 = 2076px of pane height before height --
# the binding axis here -- selects it at all, so nothing under a 2266px window reaches the
# inexact range. Recorded rather than corrected: making the constant a step function of
# size would put a table in the way of two arithmetic lines to buy back 72px on a display
# nobody has yet.
#
# The property the art's legibility rests on is the other one that run checks: all 166
# codepoints share a single advance at each size. Columns line up because of that, not
# because the advance matches any formula.
#
# fit_size quantises to even sizes and the script only probes even sizes, so the odd ones
# are outside both. That is deliberate on both sides -- an odd size is unreachable -- but
# it does mean this repo has no measurement of the odd-size behaviour and nothing here
# should be read as one.
GLYPH_ADVANCE_EM = 0.5

# Height: ImGui's line pitch is the pushed size -- calc_text_size on n lines returns n
# times the size, and get_text_line_height returns the size itself. This is *not* the
# font's own ascent-descent (1.049 em); ImGui normalises to make the pitch come out at
# the size.
LINE_PITCH_EM = 1.0

# ...which leaves the last row's descender hanging below the nominal box, because the
# ink really is 1.049 em tall (Inconsolata hhea: ascent 859, descent -190, unitsPerEm
# 1000). Only the final row can be clipped by it, so it is a one-off addition to the
# height rather than a per-row factor.
INK_HEIGHT_EM = 1.049

# Which axis binds is a property of the art, not of the pane, and it is worth writing it
# that way: this comment has already been wrong once, when the splash moved from DETAIL to
# NEEDS YOU and inverted it. Width binds exactly when
#
#     avail_w / avail_h  <  cols * GLYPH_ADVANCE_EM / ((rows - 1) * LINE_PITCH_EM + INK_HEIGHT_EM)
#
# which for the 61x72 art is 36 / 61.049 = 0.590. A pane wider than 0.59 of its height is
# height-bound; a narrower one is width-bound. The crossover is pinned by a test, so
# moving the panel again is a failure rather than a stale sentence.
#
# Where the splash sits today: NEEDS YOU is docked in MainDockSpace, which TRIAGE leaves
# at (1 - 0.21) * (1 - 0.32) = 0.5372 of window width (app.py:613,616), and about 190px
# of the height goes to chrome. scripts/verify_splash.py measures the pane at three window
# sizes and prints both candidate sizes:
#
#     1024x700  -> pane 550x510   by_width 15.28  by_height  8.35  -> 8
#     1500x900  -> pane 806x710   by_width 22.38  by_height 11.63  -> 10
#     1920x1200 -> pane 1031x1010 by_width 28.65  by_height 16.54  -> 16
#
# Height binds at all three and by a factor approaching two, so every landscape window is
# height-bound; substituting the split ratios back, width only binds once the window is
# narrower than 1.10 of its usable height, which a portrait one reaches. Both regimes are
# reachable, which is why the rule above is written as a rule rather than as "height
# binds".
#
# So height is what the floor is really about, and 8 at the smallest supported window is
# what makes 6 a working floor rather than a cautious one: a floor of 10 needs 611px of
# pane and the smallest window has 510, so the panel would draw nothing at all there. It
# is one dock drag from the floor, not a hypothetical. Both bounds are even, so they sit
# on the grid fit_size quantises to. The ceiling is a guard on the atlas rather than a
# design limit: ImGui 1.92 bakes a fresh entry per distinct size, and 61 rows would need
# a pane over 3000px tall to reach it.
MIN_FONT_SIZE = 6.0
MAX_FONT_SIZE = 48.0


def required_extent(size: float, rows: int, cols: int) -> tuple[float, float]:
    """
    The pixels ``rows`` x ``cols`` of Inconsolata occupies at ``size``.

    The pane's "is there room for this at all" test, and the inverse of ``fit_size``:
    both are written against the same three constants so they cannot disagree about
    what fitting means.
    """
    width = cols * GLYPH_ADVANCE_EM * size
    if rows <= 0:
        return width, 0.0
    return width, ((rows - 1) * LINE_PITCH_EM + INK_HEIGHT_EM) * size


def fit_size(
    avail_w: float,
    avail_h: float,
    rows: int,
    cols: int,
    *,
    min_size: float = MIN_FONT_SIZE,
    max_size: float = MAX_FONT_SIZE,
) -> float:
    """
    The largest *even* font size at which ``rows`` x ``cols`` fits the region.

    Even rather than merely whole. ``GLYPH_ADVANCE_EM`` is checked as an upper bound only
    at even sizes -- that is the whole set scripts/verify_splash.py probes, because it is
    the whole set this returns -- so an odd answer would be a size the bound has never
    been established for, on the axis where exceeding it overruns the pane by a column.
    Restricting the answer keeps the guarantee and the evidence over the same set.

    Quantising at all -- to anything -- is what bounds the atlas: ImGui 1.92 rasterises a
    face on demand at each distinct size it is asked for, so a size that varies
    continuously with the pane mints a new bake on every frame of a window drag.

    Returns ``min_size`` when the region is too small rather than raising or returning
    zero. The caller has to decide *not to draw* in that case, which it does by asking
    ``required_extent`` whether the answer actually fits -- a splash is decoration, and
    a pane that is too short for it should show nothing, not a crash and not a clipped
    half-image.

    Only the floor needs pulling onto the even grid. An odd ``min_size`` would otherwise
    leave by the return-the-floor path -- the one path that bypasses the quantisation --
    carrying the single property this function exists to provide, and the advance overrun
    would be back for panes small enough to reach it. It is raised rather than lowered
    because ``min_size=7`` states a limit: never below 7, so 8. An odd ``max_size`` needs
    nothing, since the answer is floored to the grid anyway and 49 already yields 48.
    """
    floor = 2.0 * math.ceil(min_size / 2.0)
    by_width = max_size if cols <= 0 else avail_w / (cols * GLYPH_ADVANCE_EM)
    by_height = max_size if rows <= 0 else avail_h / ((rows - 1) * LINE_PITCH_EM + INK_HEIGHT_EM)
    # Clamped before the quantisation, not after, so an enormous pane cannot hand
    # math.floor an infinity.
    fitted = min(by_width, by_height, max_size)
    return max(floor, 2.0 * float(math.floor(fitted / 2.0)))
