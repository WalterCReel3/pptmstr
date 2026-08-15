"""
The animation driven by the real art and the real table, which is the one thing
neither module's own tests do.

``tests/test_splash.py`` proves ``art_frame``'s rules against a three-glyph fixture,
and ``tests/test_splash_art.py`` proves the table's shape with nothing consuming it.
Both were right to: the fixture is what let the renderer and the table be built at
the same time, and it keeps the renderer's tests readable. But it leaves every
guarantee about the *shipped* picture unproven, and the seam is where this feature's
defects have actually lived -- a space guard that ran in one direction only, and a
bucket that could collapse to a single member under a re-partition.

So nothing here builds a fixture. Every assertion below reads ``ART``, ``RANKS`` and
``RANK_OF`` as they ship, and the properties are the ones that are meaningless
against a fixture: that this image keeps its shape, that these buckets are all live,
that these substitutions hold their brightness, and that this many cells is still
affordable to redraw.

The font is deliberately not opened. Brightness is checked against the table's own
buckets, so this file needs no dev extra and cannot skip.
"""

from __future__ import annotations

import time
from functools import cache
from itertools import pairwise

from pptmstr.ui import splash
from pptmstr.ui.splash_art import ART, RANK_OF, RANKS

# Mid-step so a rounding change in `step_index` shows up as a wrong frame rather than
# as a boundary landing one tick either side.
_STEP = 1.0 / splash.STEPS_PER_SECOND

# Enough steps for the slowest cell to be offered every glyph in the largest bucket.
# A cell advances one glyph every `period` ticks, so the longest cycle in the field is
# the slowest period times the biggest bucket. Derived rather than written down: a
# re-partition that adds a longer bucket lengthens this by itself, and a test that
# covered "a full cycle" only under the old table would quietly stop doing so.
FULL_CYCLE_STEPS = max(splash._CELL_PERIODS) * max(len(bucket) for bucket in RANKS)


@cache
def _frames() -> tuple[tuple[str, ...], ...]:
    """
    One full cycle of consecutive frames over the real art.

    Cached because it is the input to four of the tests below and costs a few
    milliseconds a frame; recomputing it per test would be the slowest thing in the
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


def _ink_positions(rows: tuple[str, ...]) -> set[tuple[int, int]]:
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
        assert [len(line) for line in frame] == [len(line) for line in ART], index
        assert _ink_positions(frame) == original, index


def test_every_cycling_cell_actually_cycles() -> None:
    """
    A cell in the cycling set that shows one glyph forever is a dead pixel sitting
    still while its neighbours move, which reads as a rendering fault.

    ``test_every_bucket_can_actually_change_a_cell`` holds the buckets at three members
    or more, but that is a statement about the table alone: a bucket can be large and
    still be unreachable at a position whose phase and period conspire, and only the
    composition can say. Over a full cycle every one of the 658 animated cells is
    measured to show at least two distinct glyphs.
    """
    frames = _frames()
    frozen = [
        (row, col)
        for row, col in _animated_cells()
        if len({frame[row][col] for frame in frames}) < 2
    ]
    assert frozen == [], frozen[:10]


def test_no_substitution_leaves_its_ink_bucket() -> None:
    """
    Brightness preserved end to end, over the real table rather than a fixture whose
    buckets were chosen to make the assertion easy.

    Against ``RANK_OF`` and not against re-measured glyph areas on purpose. The font is
    the generator's evidence and ``tests/test_splash_art.py`` pins the table to it; what
    is unproven until here is that the *renderer* honours the buckets it is handed. Going
    back to the font would test the ranking a second time and this not at all.
    """
    frames = _frames()
    for row, col in _animated_cells():
        band = RANK_OF[ART[row][col]]
        for index, frame in enumerate(frames):
            shown = frame[row][col]
            assert RANK_OF.get(shown) == band, (row, col, index, ART[row][col], shown)


def test_only_part_of_the_field_changes_on_any_one_step() -> None:
    """
    The temporal stagger on the shipped image, where the cell count makes it matter.

    The upper bound is the safety property: every cycling cell moving on the same tick
    is ~658 glyphs changing together at 3Hz on a panel that stays up for as long as the
    app has no sessions, which ``splash.py`` documents as inside the band
    photosensitivity guidance treats as a risk. The lower bound is the other half and is
    the reason this is not just ``< 1.0`` -- a field that lumps and then goes quiet beats
    at half the tick rate while its *average* rate looks fine.

    Measured over a full cycle: 0.2690 minimum, 0.3374 maximum, 0.2973 mean. The bounds
    are 0.15 and 0.50, wide enough to survive a re-partition that shifts bucket sizes and
    narrow enough that lockstep (1.0) and a stalled field (0.0) both fail.
    """
    frames = _frames()
    cells = _animated_cells()
    fractions = [
        sum(1 for row, col in cells if before[row][col] != after[row][col]) / len(cells)
        for before, after in pairwise(frames)
    ]
    assert max(fractions) <= 0.50, max(fractions)
    assert min(fractions) >= 0.15, min(fractions)


def test_a_frame_over_the_real_art_stays_affordable() -> None:
    """
    Cost on the real 61x72 art, which is the only size the number means anything at.

    On this machine (Python 3.11, Debian 12): 3.96 ms uncached for one frame, best of
    five, and 0.27 us on a cache hit averaged over a thousand. An earlier measurement in
    this project recorded 1.45 ms and 7.4 us; both differ from these, so the pair of
    numbers is worth reading as "what moved" rather than as a target.

    Best-of-five rather than a single sample because a loaded machine makes any one run
    arbitrarily slow, and the minimum is the statistic that survives that. The bound is
    ten times the measurement: an order-of-magnitude regression -- the shape a lost cache
    or an accidental per-cell font lookup would take -- fails, and ordinary noise does
    not. A tighter bound here would buy a flaky test rather than more information.

    The app idles at ``settings.fps_idle`` (9.0) whenever the splash is up, so the frame
    budget is 111 ms and even the uncached path spends under 4% of it.
    """
    samples = []
    for index in range(5):
        # A different `now` each time so no memo anywhere can turn this into a hit.
        start = time.perf_counter()
        splash.art_frame(ART, RANK_OF, RANKS, 3.3 + index)
        samples.append(time.perf_counter() - start)
    uncached = min(samples)
    assert uncached < 0.040, uncached

    cache_obj = splash.ArtFrames()
    cache_obj.frame(ART, RANK_OF, RANKS, 3.3)
    start = time.perf_counter()
    for _ in range(1000):
        cache_obj.frame(ART, RANK_OF, RANKS, 3.3)
    hit = (time.perf_counter() - start) / 1000

    assert hit < 0.001, hit
    # The memo has to be worth holding. Two orders of magnitude is far under the ~14000x
    # measured and still fails a cache that has stopped hitting.
    assert hit < uncached / 100, (hit, uncached)
