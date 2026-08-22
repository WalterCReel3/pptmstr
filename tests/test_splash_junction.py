"""
The animation driven by the real art and the real table, which is the one thing
neither module's own tests do.

``tests/test_splash.py`` proves ``art_frame``'s rules against a six-glyph fixture,
and ``tests/test_splash_art.py`` proves the table's shape with nothing consuming it.
Both were right to: the fixture is what let the renderer and the table be built at
the same time, and it keeps the renderer's tests readable. But it leaves every
guarantee about the *shipped* picture unproven, and the seam is where this feature's
defects have actually lived -- a bucket that could collapse to a single member under a
re-partition, and rates that only a 166-slot pool puts in their real proportions.

So nothing here builds a fixture. Every assertion below reads ``ART``, ``RANKS`` and
``RANK_OF`` as they ship, and the properties are the ones that are meaningless
against a fixture: that this image keeps its geometry, that these buckets are all live,
that these substitutions stay inside the inventory, and that this many cells is still
affordable to redraw.

The size of the fixture is the reason it cannot answer the question this file asks.
Seven slots make a substitution invisible one time in seven; the shipped pool is 166, so
it is invisible one time in 166, and the two put the measured rates far apart. Every
figure quoted in the docstrings below is measured here over the real 61x72 art and the
real table, and none of it is arithmetic carried over from the fixture or restated
from a comment elsewhere.

The font is deliberately not opened. Membership is checked against the table itself,
so this file needs no dev extra and cannot skip.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Sequence
from functools import cache

from pptmstr.ui import splash
from pptmstr.ui.splash_art import ART, RANK_OF, RANKS

# Mid-step so a rounding change in `step_index` shows up as a wrong frame rather than
# as a boundary landing one tick either side. One step is one row of travel now, so
# this is the time the raster line takes to cross a row.
_STEP = 1.0 / splash.RASTER_ROWS_PER_SECOND

# One full sweep: the line crosses every row, and then runs `WAKE_ROWS` further while
# it is off the bottom edge and only the wake drains. Derived rather than written
# down, so an art of a different height lengthens this by itself -- and it has to be
# derived from both terms, because the drain is where the field goes quiet and a
# `len(ART)` sample would cut the quiet stretch off and hide it.
FULL_CYCLE_STEPS = len(ART) + splash.WAKE_ROWS

# The whole rectangle the art occupies. This is the denominator the reach claim is
# about: what is modulating is a fraction of the *panel's area*, and every cell of that
# rectangle is eligible to modulate.
_FIELD_CELLS = sum(len(line) for line in ART)


@cache
def _frames() -> tuple[splash.ArtFrame, ...]:
    """
    One full cycle of consecutive frames over the real art.

    Cached because it is the input to four of the tests below and costs about half a
    millisecond a frame; recomputing it per test would be the slowest thing in the
    suite for no added coverage.
    """
    return tuple(
        splash.art_frame(ART, RANK_OF, RANKS, index * _STEP + _STEP / 2)
        for index in range(FULL_CYCLE_STEPS)
    )


@cache
def _animated_cells() -> tuple[tuple[int, int], ...]:
    """
    The positions a substitution can actually reach: cycling, and past the rank gate.

    The rank gate is the only thing a cell's own content enters into, and a cell holding
    nothing has no rank to be gated on -- so the population is the cycling share of the
    whole rectangle, less whatever the table fails to rank. The two conditions come from
    different places, the table and the renderer's hash, which is why this set is worth
    computing once here and is not something either module's tests can name.
    """
    return tuple(
        (row, col)
        for row, line in enumerate(ART)
        for col, char in enumerate(line)
        if (char == " " or char in RANK_OF) and splash.is_cycling(row, col)
    )


def _ink_positions(rows: Sequence[str]) -> set[tuple[int, int]]:
    return {
        (row, col) for row, line in enumerate(rows) for col, char in enumerate(line) if char != " "
    }


def test_the_animation_reaches_a_meaningful_part_of_the_real_art() -> None:
    """
    A floor under every other test in this file.

    All four of the properties below are vacuously true of an animation that touches
    nothing, and the ways this junction can fail -- a table that ranks none of the
    art's glyphs, a membership hash that excludes everything -- fail exactly that way.

    The denominator is the whole rectangle and not the inked part of it, because the wake
    acts on every cell of the rectangle. What the share should come out at is
    ``CYCLING_FRACTION``, less whatever the table fails to rank, and the band below is
    wide enough to leave the ranking table's coverage to its own tests.
    """
    inked = len(_ink_positions(ART))
    animated = len(_animated_cells())
    assert inked > 1500, inked
    assert animated > 1000, animated
    assert 0.25 < animated / _FIELD_CELLS < 0.45, (animated, _FIELD_CELLS)


def test_the_art_keeps_its_line_lengths_however_far_the_wake_erodes() -> None:
    """
    The one thing the pane's geometry rests on, and the only thing the wake may not move.

    A cell's *contents* are the wake's to change in either direction -- the erosion and
    the healing behind it are the effect. What the pane cannot survive is a line that
    grows or shrinks: the art is ragged, the block is centred from its widest row, and a
    renderer that padded rows to a rectangle or dropped a blank instead of drawing it
    would reflow the picture rather than animate it.

    Checked against ``ART`` itself rather than against the first frame, because a frame
    that reflowed identically on every step would agree with itself.
    """
    lengths = [len(line) for line in ART]
    for index, frame in enumerate(_frames()):
        assert [len(line) for line in frame.lines] == lengths, index


def test_every_cycling_cell_actually_cycles() -> None:
    """
    A cell in the cycling set that shows one glyph forever is a dead pixel sitting
    still while its neighbours move, which reads as a rendering fault.

    ``test_no_bucket_falls_below_the_generators_member_floor`` holds the buckets at three
    members or more, but that is a statement about the table alone: a bucket can be large and
    still be unreachable at a position the sweep never offers a successful draw, and
    only the composition can say.

    The sample is a *full cycle* and cannot be shorter. A cell only draws while the
    line is within ``WAKE_ROWS`` rows of it, which is 20 of the 81 steps and happens
    once per sweep, so a short sample reports most of the field as frozen when nothing
    is wrong with it.

    One cycle is enough rather than merely necessary, and that is not guaranteed by
    construction -- a cell's draw succeeds with certainty only on the step the line is on
    its own row, and even that draw can select the glyph the cell already shows -- so
    this is a fact about this art against this table, and a re-partition that shrinks a
    bucket is exactly the change that should make it fail.
    """
    frames = _frames()
    frozen = [
        (row, col)
        for row, col in _animated_cells()
        if len({frame.lines[row][col] for frame in frames}) < 2
    ]
    assert frozen == [], frozen[:10]


def test_no_substitution_leaves_the_pool() -> None:
    """
    Every glyph the animation can put on screen is one the table vouched for.

    A substitution draws from the union of the buckets, so what a cell shows is no longer
    confined to its own brightness band and this cannot assert one. What it can assert is
    the property the union still has: ``RANK_OF`` is the whole set scripts/rank_glyphs.py
    measured and scripts/verify_splash.py checked, and that check is not only about
    brightness -- it holds every member to one advance width at every size the pane can
    ask for, and to presence in the baked atlas. U+0020 is checked alongside them and
    shares that advance, which is what makes appending it to the pool cost the column
    alignment nothing. A glyph from outside is a column out of alignment or a tofu box on
    a 61x72 picture whose legibility is entirely a matter of its columns lining up.

    Against ``RANK_OF`` and not against re-measured glyph areas on purpose. The font is
    the generator's evidence and ``tests/test_splash_art.py`` pins the table to it; what
    is unproven until here is that the *renderer* stays inside the inventory it is handed.
    Going back to the font would test the ranking a second time and this not at all.

    The membership test is ``in RANK_OF`` rather than a flattening of ``RANKS`` built here,
    because the two are the same set by construction -- ``test_rank_of_agrees_with_ranks``
    in tests/test_splash_art.py is what holds them so -- and reading the mapping keeps this
    file from carrying its own copy of the pool.
    """
    pool = set(RANK_OF) | {" "}
    frames = _frames()
    for row, col in _animated_cells():
        for index, frame in enumerate(frames):
            shown = frame.lines[row][col]
            assert shown in pool, (row, col, index, ART[row][col], shown)


def test_a_frame_over_the_real_art_stays_affordable() -> None:
    """
    Cost on the real 61x72 art, which is the only size the number means anything at.

    Timed over a whole cycle rather than at an arbitrary ``now``, because per-step cost
    is not flat: only the ``WAKE_ROWS`` rows behind the line do per-cell work, so a step
    with the wake off the bottom edge is nearly free and a step over the dense middle is
    not. A single sample could report anything in that spread and call it the cost of a
    frame, which is why the bound is on the maximum.

    The per-second figure is the one worth pinning, since the rate and the per-frame cost
    move against each other. It is a ceiling: it assumes all 16 steps a second are
    computed, and at the ``fps_idle`` of 9.0 the splash idles at, the memo below serves
    the rest of the frames.

    Best-of-three per step rather than a single sample because a loaded machine makes any
    one run arbitrarily slow and the minimum is the statistic that survives that. Both
    bounds sit an order of magnitude clear, so a lost early-out or an accidental per-cell
    font lookup fails and ordinary noise does not.
    """
    per_step = []
    for step in range(FULL_CYCLE_STEPS):
        # A distinct `now` per step, and never repeated across the run, so no memo
        # anywhere can turn any of this into a cache hit.
        now = step * _STEP + _STEP / 2
        reps = []
        for _ in range(3):
            start = time.perf_counter()
            splash.art_frame(ART, RANK_OF, RANKS, now)
            reps.append(time.perf_counter() - start)
        per_step.append(min(reps))

    dearest = max(per_step)
    assert dearest < 0.011, dearest

    per_second = sum(per_step) / (FULL_CYCLE_STEPS * _STEP)
    assert per_second < 0.080, per_second

    cache_obj = splash.ArtFrames()
    cache_obj.frame(ART, RANK_OF, RANKS, 3.3)
    start = time.perf_counter()
    for _ in range(1000):
        cache_obj.frame(ART, RANK_OF, RANKS, 3.3)
    hit = (time.perf_counter() - start) / 1000

    # A hit is orders of magnitude cheaper than a step, and at 16 steps a second against
    # 60fps under pointer input most frames are hits. Two orders of magnitude is far under
    # the real ratio and still fails a cache that has stopped hitting. Compared against the
    # *mean* step rather than the dearest, because what a hit replaces is an average frame.
    assert hit < 0.001, hit
    assert hit < statistics.fmean(per_step) / 100, (hit, statistics.fmean(per_step))
