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
in-bucket substitution, where the raster line has reached, a readable floor under the
quote -- is a statement about two different ``now`` values that only a caller-supplied
clock lets a test make.

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
from enum import IntEnum

# -- the sweep -----------------------------------------------------------------
#
# A bright raster line runs down the art and drags a wake of substituting cells behind
# it. Two ramps, deliberately different lengths: the wake decides how many cells
# *substitute* and reaches ``WAKE_ROWS`` rows back, while the luminance highlight is two
# rows and no more. Spreading the brightness over the whole wake lights a third of the
# panel at once and loses the line, which is the thing being drawn.
#
# One step is one row of travel, so this is a speed and not a tick rate, and what bounds
# it is the rate the window is really redrawing at:
#
#     rows_per_second <= band_rows * displayed_fps
#
# A luminance band narrower than its own per-frame displacement cannot read as motion. If
# the line advances two rows between displayed frames and the band is one row tall, the
# frames light rows p and p+2 and nothing ever lights p+1 -- the line hops down the block
# with a permanent hole in it instead of sweeping.
#
# With no sessions there is nothing active, so app.py sets
# ``runner.fps_idling.enable_idling = not snap.any_active`` and the window redraws at
# ``settings.fps_idle``, 9.0 by default. The figure to bound against is 8.7 rather than
# 9.0: ``fps_idle`` is a ``glfwWaitEventsTimeout`` bound and not a clock, so an interval
# is 1/9s plus render and scheduling time, and orchestrator-design.md:534-539 measures
# the empty-fleet TRIAGE idle phase -- this panel, on screen -- at 8.7-9.0fps. Two rows
# at 8.7fps allows 17.4 rows/s. Sixteen sits under that at 1.84 rows a frame, so the band
# always abuts or overlaps where it just was and no row is skipped. The line crosses the
# 61 rows in 3.8s and the whole cycle, drain included, is 5.0625s.
#
# The bound puts a floor under ``fps_idle`` that nothing makes it respect. It is
# operator-settable and unclamped: settings.py:45 defaults it to 9.0, ``load`` checks the
# value's type without checking its range (settings.py:103), and ``--fps-idle`` takes any
# float (app.py:697,709) -- which is why orchestrator-design.md:533 marks every figure in
# that benchmark as conditional on it. Two rows at sixteen a second needs the window to
# really display 8fps, so the floor on the setting is about 8.3 once the ~4ms of render
# and scheduling behind the 8.7-against-9.0 gap is carried. At 6 the displacement is 2.67
# rows a frame, wider than the band, and the permanent hole this constant is chosen to
# avoid is back.
#
# Only the slow end needs checking, because faster frames only shrink the displacement --
# and faster is what an operator produces: hello_imgui holds full rate for a few frames
# after pointer crossing, focus or window mapping (orchestrator-design.md:566-580), which
# is exactly what someone looking at a cold-start splash generates. At 60fps the
# displacement is 0.27 rows and the line simply holds a row for several frames.
RASTER_ROWS_PER_SECOND = 16.0

# How far behind the line a cell can still substitute, in rows. Intensity is 1.0 on the
# raster row and steps down one twentieth per row behind it, reaching zero exactly here.
# A plain linear ramp, because "steps down for every line" is a statement about rows, and
# equal steps are what make the trail read as one gradient rather than as a bright clump
# with stragglers behind it.
#
# The cycle is ``rows + WAKE_ROWS`` steps rather than ``rows``: through the last
# ``WAKE_ROWS`` of it the line is off the bottom edge and only the wake drains. That beat
# is what stops the next sweep entering at the top while the previous trail is still lit
# -- two lit bands with no line between them read as noise, not as a scan.
WAKE_ROWS = 20

# Resolution of the per-step draw, and the split of the draw hash's 32 bits between the
# two questions asked of it. 1024 buckets resolve the ramp's twentieths to three decimal
# places, so a cell's firing rate is its row's intensity rather than a rounding of it.
_DRAW_BITS = 10
_DRAW_BUCKETS = 1 << _DRAW_BITS


class Luminance(IntEnum):
    """
    How bright a row is drawn, as an ordinal for the pane to map to a colour.

    An int and never a colour: this module imports no ImGui and knows no palette, which
    is what keeps the whole animation exercisable without a live frame. Named members
    rather than a bare 0/1/2 so the pane's mapping is total and reviewable -- another
    level here becomes a missing arm there rather than a silently dim row.

    Two lit levels, not twenty. The substitution wake is ``WAKE_ROWS`` deep; the
    luminance highlight is the raster row and the row it has just left. Two is not a
    matter of taste. One row fails the bound in the sweep section at
    ``RASTER_ROWS_PER_SECOND``, so it would strobe rather than travel, and it has no
    trailing side either -- at the 8 to 16 pixels a row occupies at the sizes
    ``fit_size`` returns for this pane, that reads as a hairline blinking down the panel.
    """

    DIM = 0
    TRAIL = 1
    RASTER = 2


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

# -- what protects the panel ---------------------------------------------------
#
# This is on screen for as long as the app has no sessions, so flicker is a live hazard
# rather than a theoretical one, and nothing below is a limit on how often a single cell
# may change. What carries it is rate and area, both by wide margins.
#
# *It is not carried by the modulation being small.* A pane mapping DIM and RASTER onto a
# theme's dim and strong text colours puts 0.67 of relative luminance between them on
# ``dark``, 0.76 on ``ghost`` and 0.47 on ``high_contrast``, against the 0.10 at which
# WCAG's general flash threshold starts. The highlight is supposed to read, so a large
# step is the design working -- but it means an argument from dimness would be false, and
# no plausible palette pairing rescues it.
#
# *Any one place on the panel pulses at 0.198 Hz.* ``RASTER_ROWS_PER_SECOND`` is a speed
# and not a flicker rate -- a step moves the line one row, it does not toggle anything --
# so the quantity that matters is what a fixed row sees over a cycle: DIM for nearly all
# of it, RASTER on the single step the line is on it, TRAIL on the next, then DIM again.
# One pulse, one pair of opposing transitions, once per ``rows + WAKE_ROWS`` = 81 steps,
# which at sixteen rows a second is 5.0625s. WCAG 2.3.1 permits three a second, so that
# is 15x of margin, and it is below the ~3Hz lower edge of the photosensitive band
# entirely -- the band never opens. It is frame-rate independent for the same reason: a
# faster window spreads one pulse over more frames rather than repeating it.
#
# *What modulates luminance is two rows of 61*, travelling, never the whole field.
# Nothing in this repo records a viewing distance, so the geometry has to be stated to be
# argued: on a 1920x1200 24-inch panel, 37px/cm, viewed at 60cm, a 10-degree field is
# 10.5cm and 390px across. The largest case the fitting section below records is size 16,
# where the band is 32px tall against art 576px wide, and the part of it inside that
# field is 10% -- against WCAG's 25% area threshold. At the smallest window in that same
# table, size 8, it is 3.9%. Both take the whole band rectangle as flashing, where at
# most 31% of a cell is ink.
#
# *Substitution is local, and it barely moves the area average.* Only the ``WAKE_ROWS``
# rows behind the line can change at all -- 20 of the 61-row art -- the ramp averages
# 0.525 over those, and the cycling set is ``CYCLING_FRACTION`` of the ranked cells to
# begin with, so the bound is 20/61 x 0.525 x 0.34, under 6% of the field on any one
# step, and the true figure is lower because a row's spaces never take part. A swap never
# leaves the cell's own bucket, and the buckets are measured rather than eyeballed:
# splash_art.py:66 records a 17.2% within-band ink spread, so a swap moves at most 4.6% of
# a cell's area between ink and background -- the brightest glyph in the art covers 31% of
# its cell -- for about 0.05 of relative luminance on the highest-contrast palette, half
# the 0.10 threshold. Each cell draws independently, so the signs are independent and the
# area-averaged luminance, which is the quantity WCAG measures, is essentially static.
#
# The cost, stated as a cost rather than left to be discovered. On the raster row every
# cycling cell substitutes and a cell may substitute on consecutive steps, so a cell
# being overtaken changes at up to ``RASTER_ROWS_PER_SECOND`` -- 16 Hz, inside the 15-20
# Hz region where photosensitive response peaks, for the 1.25s the wake is over it, at an
# average rate of 0.525 x 16 across that. Nothing about the sweep's own 0.198 Hz protects
# that; what does is the ~0.05 per-cell step above.
#
# That last figure is a standing bound rather than an observation: NO SUBSTITUTION
# MAY MOVE A CELL MORE THAN ONE INK BAND, with the ranking generated by
# scripts/rank_glyphs.py rather than edited. tests/test_splash_art.py's
# ``test_no_swap_changes_a_cell_by_more_than_a_sixth`` holds it at a ratio of 1.18, and
# it was written to keep the picture's shape, not for this. Widening the bands to shrink
# the literal is therefore a photosensitivity change, and this sentence is the only place
# that says so.


def _cell_hash(row: int, col: int) -> int:
    """
    A stable 32-bit mix of a cell's coordinates.

    Written out rather than delegated to ``hash()`` on purpose. CPython's ``hash`` is
    only guaranteed stable for ints within one build, and the *point* of this value is
    that a cell's membership is the same on every run and every machine -- a property
    that a test can pin only if the mixing is ours.
    """
    x = (row * 0x9E3779B1 + col * 0x85EBCA77 + 0x165667B1) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x2C1B3C6D) & 0xFFFFFFFF
    x ^= x >> 12
    x = (x * 0x297A2D39) & 0xFFFFFFFF
    x ^= x >> 15
    return x


def _draw_hash(row: int, col: int, step: int) -> int:
    """
    A stable 32-bit mix of a cell and the step it is being drawn on.

    Separate from ``_cell_hash``, and mixed with the step, because whether a member of
    the cycling set substitutes is re-decided on every step. The cheaper design gives
    each cell one fixed threshold from its own hash and compares that against its row's
    intensity, and it is worse: the same cells light first on every sweep, and the
    lowest-threshold ones are lit for the whole 1.25s the wake covers them, which is a
    field of fixed strobes rather than a wake. Drawing afresh gives every cycling cell in
    a row the same expected rate and none of them a fixed phase.

    Written out rather than delegated for the same reason ``_cell_hash`` is: the value
    has to be identical on every run and every machine, or nothing here is pinnable.

    Two disjoint bit ranges:

        0-9   the draw itself, against _DRAW_BUCKETS
        10-31 which glyph of the cell's bucket is substituted in

    Disjoint rather than convenient. Both are read from one value on one step, so shared
    bits would make the glyph a function of how narrowly the draw succeeded -- and the
    range that "succeeds" shrinks with every row further back in the wake, so the choice
    would be drawn from a narrower and differently-shaped subset of each bucket the
    dimmer the row, which is a correlation between position and glyph that no part of
    this design wants.
    """
    x = (_cell_hash(row, col) ^ ((step & 0xFFFFFFFF) * 0x27D4EB2F)) & 0xFFFFFFFF
    x ^= x >> 16
    x = (x * 0x7FEB352D) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x846CA68B) & 0xFFFFFFFF
    x ^= x >> 16
    return x


def step_index(now: float) -> int:
    """
    How many rows of travel the line has made by ``now``, unwrapped.

    Exposed because it is the cache key: two ``now`` values in the same step produce
    byte-identical frames, which is what makes ``ArtFrames`` a memo rather than an
    approximation. It counts travel from t=0 rather than giving a position on the panel,
    because the wrap to a row index needs the art's height, which ``art_frame`` is given
    and this is not.
    """
    return int(now * RASTER_ROWS_PER_SECOND)


def is_cycling(row: int, col: int) -> bool:
    """
    Whether the cell at ``(row, col)`` ever substitutes.

    A function of position alone -- not of the character there, and not of time. It
    answers whether a cell is *eligible*; the sweep decides when an eligible cell
    actually moves, and holding those apart is what lets membership be pinned by a test
    that never mentions a clock.

    ``art_frame`` calls this rather than repeating the comparison against its own hash,
    so the predicate the pane can ask and the rule the renderer follows are the same line
    of code -- an unpinned duplicate of a rule is the smell STYLE.md §3 names, and here
    the two readers are a public predicate and the frame that has to agree with it.
    """
    return _cell_hash(row, col) % _MEMBERSHIP_BUCKETS < _MEMBERSHIP_THRESHOLD


@dataclass(frozen=True, slots=True)
class ArtFrame:
    """
    One frame of the art: the lines as they are drawn, and how bright each one is.

    ``luminance[i]`` belongs to ``lines[i]``, one entry per line and never a ragged pair.
    Carried as one object for the same reason ``QuoteFrame`` carries its own lines -- the
    pane has a single thing to read and cannot pair this frame's glyphs with the previous
    frame's highlight, which on a moving line is a smear rather than a subtle error.
    """

    lines: tuple[str, ...]
    luminance: tuple[Luminance, ...]


def art_frame(
    art: Sequence[str],
    rank_of: Mapping[str, int],
    ranks: Sequence[Sequence[str]],
    now: float,
) -> ArtFrame:
    """
    ``art`` at ``now``: the raster line's row, and the cells its wake has swapped.

    The line is at ``step_index(now) % (rows + WAKE_ROWS)``, so it is legitimately at or
    past the last row for the final ``WAKE_ROWS`` steps of each cycle -- that is the
    drain, and during it nothing is highlighted and only rows near the bottom still
    substitute. A row ``d`` above the line substitutes at rate ``1 - d / WAKE_ROWS``,
    which is 1.0 on the line itself, so every cycling cell there moves.

    Whether a given cell takes its row's rate is drawn per step from ``_draw_hash``, and
    when the draw fails the cell shows *the glyph the art has there*, not the substitute
    it last showed. Holding the last one would mean knowing the most recent step at which
    the cell's draw succeeded, which is a backward scan of up to ``WAKE_ROWS`` steps per
    cell per step -- twenty times this function's hash work, which is most of a 60fps
    frame on its own, and desktop input is exactly what wakes this window to 60fps (see
    ``RASTER_ROWS_PER_SECOND``). So the sweep would judder while the operator is looking
    at it, to buy a stale glyph. One hash per cell per step is what the design affords,
    and what that buys is arguably the better read anyway: the art
    visibly heals as the wake drains behind the line, and nothing frozen is left in it.

    A cell is substituted only when all of these hold, and every failure is a no-op
    rather than an error (STYLE.md §1, "an intent for something that does not exist is
    a no-op"): the character is not a space, it has a rank, that rank indexes a real
    bucket, the cell is in the cycling set, its draw succeeds, and the glyph the draw
    selects is not itself a space. An unranked glyph is a hole in the ranking table, not
    a bug worth aborting a frame for.

    Space is guarded in both directions, even though the contract says U+0020 is absent
    from ``rank_of``. The art's shape *is* its whitespace, so a ranking table that grew
    a space entry would dissolve the image from either side -- a space overwritten with
    ink erodes the negative space, and ink replaced by a space punches holes in the
    silhouette. Both are silent failures, and the second is the one that is visible at a
    glance, which is worth one comparison per substitution to make impossible.

    A successful draw picks a glyph from the cell's bucket without regard to what is
    already there, so a cell can draw the glyph it is already showing. That is a change
    of nothing, and it is why the *visible* change rate of a row runs slightly under its
    intensity rather than equal to it.
    """
    step = step_index(now)
    raster = step % (len(art) + WAKE_ROWS)

    lines: list[str] = []
    luminance: list[Luminance] = []
    for row, line in enumerate(art):
        if row == raster:
            luminance.append(Luminance.RASTER)
        elif row == raster - 1:
            luminance.append(Luminance.TRAIL)
        else:
            luminance.append(Luminance.DIM)

        # The line leads its wake, so the rows it has passed are the ones above it and
        # every row outside the wake is left exactly as the art has it. Skipping those
        # here rather than letting each cell fail its draw is what keeps a frame to the
        # cost of ``WAKE_ROWS`` rows instead of the whole picture.
        distance = raster - row
        if not 0 <= distance < WAKE_ROWS:
            lines.append(line)
            continue
        threshold = (1.0 - distance / WAKE_ROWS) * _DRAW_BUCKETS

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
            if not is_cycling(row, col):
                continue
            drawn = _draw_hash(row, col, step)
            if drawn % _DRAW_BUCKETS >= threshold:
                continue
            substitute = bucket[(drawn >> _DRAW_BITS) % len(bucket)]
            if substitute == " ":
                continue
            if cells is None:
                cells = list(line)
            cells[col] = substitute
        lines.append(line if cells is None else "".join(cells))
    return ArtFrame(lines=tuple(lines), luminance=tuple(luminance))


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
        self._frame = ArtFrame(lines=(), luminance=())

    def frame(
        self,
        art: Sequence[str],
        rank_of: Mapping[str, int],
        ranks: Sequence[Sequence[str]],
        now: float,
    ) -> ArtFrame:
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
