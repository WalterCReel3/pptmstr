"""
The animation driven by the real art and the real table, which is the one thing
neither module's own tests do.

``tests/test_splash.py`` proves ``art_frame``'s rules against a six-glyph fixture,
and ``tests/test_splash_art.py`` proves the table's shape with nothing consuming it.
Both were right to: the fixture is what let the renderer and the table be built at
the same time, and it keeps the renderer's tests readable. But it leaves every
guarantee about the *shipped* picture unproven, and the seam is where this feature's
defects have actually lived -- a space guard that ran in one direction only, and a
bucket that could collapse to a single member under a re-partition.

So nothing here builds a fixture. Every assertion below reads ``ART``, ``RANKS`` and
``RANK_OF`` as they ship, and the properties are the ones that are meaningless
against a fixture: that this image keeps its shape, that these buckets are all live,
that these substitutions stay inside the inventory, and that this many cells is still
affordable to redraw.

The size of the fixture is the reason it cannot answer the question this file asks.
Six glyphs make a substitution invisible one time in six; the shipped pool is 165, so
it is invisible one time in 165, and the two put the measured rates 43% apart. Every
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
from itertools import pairwise

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

# The whole rectangle the art occupies, spaces included. This is the denominator the
# safety claim is about: what is modulating is a fraction of the *panel's area*, and a
# cell that holds a space is panel area that never changes.
_FIELD_CELLS = sum(len(line) for line in ART)

# How many sweeps the safety bound is measured over. Ten because that is where the
# per-step maximum stops climbing -- see that test's docstring for the series -- and
# because at about 50 ms a sweep to generate and compare, it is the point where more
# cycles start costing seconds and reporting the same number.
_SAFETY_CYCLES = 10


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
    The positions a substitution can actually reach: inked, ranked, and cycling.

    All three conditions come from different places -- the art, the table and the
    renderer's hash -- which is exactly why this set is worth computing once here and
    is not something either module's tests can name.
    """
    return tuple(
        (row, col)
        for row, line in enumerate(ART)
        for col, char in enumerate(line)
        if char != " " and char in RANK_OF and splash.is_cycling(row, col)
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
    Measured: 658 of the 1869 inked cells, which is 35.2% and matches
    ``CYCLING_FRACTION`` of 0.34 to within the granularity of one hash bucket.
    """
    inked = len(_ink_positions(ART))
    animated = len(_animated_cells())
    assert inked > 1500, inked
    assert animated > 500, animated
    assert 0.25 < animated / inked < 0.45, (animated, inked)


def test_the_silhouette_never_changes_shape() -> None:
    """
    The property the whole effect rests on: the picture is made of its negative space,
    so the animation may change what is drawn and never *where*.

    Checked against ``ART`` itself rather than against the first frame, because a frame
    that eroded the silhouette identically on every step would agree with itself. Row
    lengths are asserted too -- the art is ragged, and a renderer that padded rows to a
    rectangle would keep every ink position and still change the image.

    This is what a one-directional space guard fails. Ink replaced by a space punches
    holes in the silhouette, and no exception reports it.
    """
    original = _ink_positions(ART)
    for index, frame in enumerate(_frames()):
        assert [len(line) for line in frame.lines] == [len(line) for line in ART], index
        assert _ink_positions(frame.lines) == original, index


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
    is wrong with it. Measured over the real art: a 5-step sample calls 94.5% of the
    658 animated cells frozen, 24 steps calls 67.3% frozen, 60 steps still calls 8
    cells frozen, and the full 81 steps calls none.

    One cycle is enough rather than merely necessary, and that is measured rather than
    argued: 0 of 658 cells is frozen over one cycle, at every one of the 81 phase
    offsets a cycle can start on. It is not guaranteed by construction -- a cell's
    draw succeeds with certainty only on the step the line is on its own row, and even
    that draw can select the glyph the cell already shows -- so this is a fact about
    this art against this table, and a re-partition that shrinks a bucket is exactly
    the change that should make it fail.
    """
    frames = _frames()
    frozen = [
        (row, col)
        for row, col in _animated_cells()
        if len({frame.lines[row][col] for frame in frames}) < 2
    ]
    assert frozen == [], frozen[:10]


def test_no_substitution_leaves_the_ranked_inventory() -> None:
    """
    Every glyph the animation can put on screen is one the table vouched for.

    A substitution draws from the union of the buckets, so what a cell shows is no longer
    confined to its own brightness band and this cannot assert one. What it can assert is
    the property the union still has: ``RANK_OF`` is the whole set scripts/rank_glyphs.py
    measured and scripts/verify_splash.py checked, and that check is not only about
    brightness -- it holds every member to one advance width at every size the pane can
    ask for, and to presence in the baked atlas. A glyph from outside it is a column out
    of alignment or a tofu box on a 61x72 picture whose legibility is entirely a matter of
    its columns lining up.

    Against ``RANK_OF`` and not against re-measured glyph areas on purpose. The font is
    the generator's evidence and ``tests/test_splash_art.py`` pins the table to it; what
    is unproven until here is that the *renderer* stays inside the inventory it is handed.
    Going back to the font would test the ranking a second time and this not at all.

    The membership test is ``in RANK_OF`` rather than a flattening of ``RANKS`` built here,
    because the two are the same set by construction -- ``test_rank_of_agrees_with_ranks``
    in tests/test_splash_art.py is what holds them so -- and reading the mapping keeps this
    file from carrying its own copy of the pool.
    """
    frames = _frames()
    for row, col in _animated_cells():
        for index, frame in enumerate(frames):
            shown = frame.lines[row][col]
            assert shown in RANK_OF, (row, col, index, ART[row][col], shown)


def test_only_part_of_the_field_changes_on_any_one_step() -> None:
    """
    How much of the shipped image moves at once, where the cell count makes it matter.

    The design's own arithmetic for this is 20 wake rows of 61, a ramp averaging 0.525
    over them, and a cycling set that is 0.34 of the ranked cells: under 6% of the field
    on any one step. Measured against all 4237 cells of the rectangle the art occupies:

        showing a substitute      max 4.15% of the field, mean 2.00%
        changed since last step   max 5.40% of the field, mean 2.73%

    Both are under 6%, so the arithmetic holds on the real art -- but the headroom on the
    second is 10%, which is not the comfortable margin it reads as. Two separate reasons
    it runs high, and they compound. The calculation predicts the first quantity while
    what a viewer integrates is the second: a step modulates a cell both when a substitute
    appears and when it reverts, so the change figure sits above the substitution figure
    rather than below it. And the ramp term is the *draw* rate, of which only ``1 - 1/165``
    is visible -- a discount of 0.6%, where a substitution confined to one bucket of the
    partition would have discounted it by about 7%.

    The bound asserted is 6% because 6% is the claim. It is deliberately not the
    measurement plus a margin: if a re-partition or a wider wake pushes this over, what has
    failed is the arithmetic above, and that should surface here rather than be absorbed.
    There is no flake risk in a tight bound -- every hash in this animation is written out
    in ``splash.py`` precisely so the sequence is identical on every run and machine, so
    these numbers are exact rather than sampled.

    ``_SAFETY_CYCLES`` sweeps rather than one, because one is flattering. One cycle passes
    the wake over each row exactly once, so it draws each cell at each distance once and
    its maximum is a maximum over 81 steps rather than over 810. The measured peak climbs
    until about the tenth sweep and then stops: 5.05% over one cycle, 5.17% over five,
    5.40% over ten, and no higher at a hundred.

    The lower bound is not a bound over the whole cycle, because the quiet stretch *is* the
    design. Through the last ``WAKE_ROWS`` steps the line is off the bottom and only the
    wake drains, and the measured change count reaches 0 of 658 cells at raster rows 78 and
    79. So the floor is asserted where it means something -- while the wake is fully on the
    art, raster rows 20 to 60 -- and there the change rate stays between 17.8% and 34.8% of
    the cycling set over 100 cycles. Over the whole cycle no positive floor passes at all,
    which is why one is not asserted there.
    """
    cells = _animated_cells()
    # One flat run rather than a cycle at a time, so the step from the last row of the
    # drain back to the top of the next sweep is measured like any other. It is the one
    # step a per-cycle loop would skip, and it is a transition between the emptiest wake
    # and the newest one -- exactly where a design that restarted badly would show it.
    frames = tuple(
        splash.art_frame(ART, RANK_OF, RANKS, step * _STEP + _STEP / 2)
        for step in range(_SAFETY_CYCLES * FULL_CYCLE_STEPS + 1)
    )

    substituted = [
        sum(1 for row, col in cells if frame.lines[row][col] != ART[row][col]) / _FIELD_CELLS
        for frame in frames
    ]
    assert max(substituted) <= 0.06, max(substituted)

    changed = [
        sum(1 for row, col in cells if before.lines[row][col] != after.lines[row][col])
        for before, after in pairwise(frames)
    ]
    assert max(changed) / _FIELD_CELLS <= 0.06, max(changed) / _FIELD_CELLS

    # `changed[i]` is the step from raster row i to i + 1 of its own sweep, so both
    # endpoints have the full wake on the art exactly while that row runs from WAKE_ROWS
    # to len(ART) - 2.
    steady = [
        count / len(cells)
        for index, count in enumerate(changed)
        if splash.WAKE_ROWS <= index % FULL_CYCLE_STEPS <= len(ART) - 2
    ]
    assert min(steady) >= 0.10, min(steady)
    assert max(steady) <= 0.50, max(steady)


def test_a_frame_over_the_real_art_stays_affordable() -> None:
    """
    Cost on the real 61x72 art, which is the only size the number means anything at.

    Timed over a whole cycle rather than at an arbitrary ``now``, because per-step cost
    is no longer flat: only the ``WAKE_ROWS`` rows behind the line do per-cell work, so a
    step with the wake off the bottom edge is nearly free and a step over the dense
    middle is not. On this machine (Python 3.11, Debian 12), best of three at each of the
    81 steps, over six runs: 0.010 ms at the cheapest step, 0.80 to 1.09 ms at the
    dearest, 0.49 to 0.51 ms mean. A hundredfold spread within one cycle is why the bound
    is on the maximum over the cycle -- a single sample at an arbitrary ``now`` could have
    reported any number in that range and called it the cost of a frame.

    Per frame it is *cheaper* than what it replaced -- the same measurement against the
    previous full-field implementation on this machine gives 2.47 to 2.72 ms mean, since
    only 20 rows of 61 now do per-cell work -- but the rate went from 3 steps a second to
    16, so the per-second figure is the one worth pinning: 7.8 to 8.2 ms of CPU per second
    of animation, against 7.4 to 8.2 ms/s for the old model. Five times cheaper per frame
    at five times the rate is a wash, and the rework is not the cost regression the rate
    change looks like from the per-frame number alone.

    That per-second figure is a ceiling. It assumes every one of the 16 steps a second is
    computed, which needs the window redrawing at 16fps or better; at the ``fps_idle`` of
    9.0 the splash actually idles at, only 9 steps a second are ever asked for, the memo
    below serves the rest of the frames, and the real figure is nearer 4.5 ms/s.

    Best-of-three per step rather than a single sample because a loaded machine makes any
    one run arbitrarily slow and the minimum is the statistic that survives that. Both
    bounds are ten times the measurement: an order-of-magnitude regression -- the shape a
    lost early-out or an accidental per-cell font lookup would take -- fails, and ordinary
    noise does not.
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

    # 0.18 us measured, against a 0.50 ms mean step: the memo is worth holding by about
    # 2700x, and it earns more than it used to -- at 16 steps a second and 60fps under
    # pointer input, three frames in four are hits. Two orders of magnitude is far under
    # the measured ratio and still fails a cache that has stopped hitting, and it is
    # compared against the *mean* step rather than the dearest because what a hit
    # replaces is an average frame.
    assert hit < 0.001, hit
    assert hit < statistics.fmean(per_step) / 100, (hit, statistics.fmean(per_step))
