"""
The splash animation core: cycling cells, the quote, and fit sizing.

Everything here runs against small hand-built fixtures rather than against
``pptmstr.ui.splash_art``. That is not only for speed -- the real ranking table is
produced by a separate task, and a test that imported it would be asserting facts
about *that* data as much as about this code. The fixtures below deliberately include
the shapes the real table promises never to contain (a ranked space, a bucket index
past the end of the table) because those are the cases where "no-op, not an error" is
a claim rather than an accident.
"""

from __future__ import annotations

import functools
import math
import subprocess
import sys
from pathlib import Path

import pytest
from imgui_bundle import imgui

from pptmstr import theme
from pptmstr.ui import splash

# Three buckets of decreasing size. They gate eligibility and nothing else: a cell
# substitutes only if its glyph is ranked, the rank indexes a bucket that exists, and that
# bucket is non-empty. What the substitute *becomes* is drawn from their union, so the
# partition itself never reaches the output.
#
# The one-glyph bucket is the case that separates those two roles. A bucket of one is a
# shape the shipped table can reach under a re-partition, and the cell holding its member
# must still cycle -- it has the whole pool to draw from and its own bucket's size is not
# consulted. A fixture whose buckets were all the same size could not tell that from a
# renderer that had gone back to drawing within the bucket.
RANKS: tuple[tuple[str, ...], ...] = (("a", "b", "c"), ("X", "Y"), ("#",))
RANK_OF: dict[str, int] = {c: i for i, bucket in enumerate(RANKS) for c in bucket}

# What a successful draw actually picks from: every glyph of every bucket, in order,
# with no bucket structure surviving into the choice. Built here the same way
# ``art_frame`` builds it so that the two cannot disagree about what the pool is -- the
# alternative is a hand-written list that stays green while the flattening changes under
# it, which pins the test file to itself rather than to the renderer.
#
# Six glyphs, against the shipped table's 165. The fixture is deliberately far smaller,
# because every rate below scales as ``1 - 1/len(POOL)`` and a six-glyph pool leaves that
# term visibly under 1.0 -- at 165 it is 0.994 and a test written against it could not
# tell "picks from the pool" from "always changes".
POOL: tuple[str, ...] = tuple(char for bucket in RANKS for char in bucket)

# The share of successful draws that are *visible*. A draw picks from POOL without
# regard to what the cell already shows, so it lands back on that glyph one time in
# ``len(POOL)`` and changes nothing. A single number and not a per-cell one, because the
# pool is the same for every cell -- which is also what makes the residuals measured
# further down pure sampling noise with no composition term in them.
#
# Every rate measured from the rendered lines below is a draw rate times this number.
# The difference is not cosmetic: on the raster row *every* cycling cell is redrawn, and
# one in six of them is redrawn with what it already had.
VISIBLE_DRAW = 1.0 - 1.0 / len(POOL)

# Not in RANK_OF at all, and mapped past the end of RANKS, respectively.
UNRANKED = "?"
OUT_OF_RANGE = "@"
RANK_OF_BROKEN: dict[str, int] = {**RANK_OF, OUT_OF_RANGE: 99}

_ALPHABET = ("a", "b", "c", "X", "Y", "#", " ", " ", UNRANKED, OUT_OF_RANGE)


def make_art(rows: int = 48, cols: int = 40) -> tuple[str, ...]:
    """
    A grid mixing every ranked bucket with spaces, an unranked glyph and a broken rank.

    Filled by a fixed arithmetic walk rather than a random module, so the fixture is
    the same on every run and a failure is reproducible from the test name alone.
    """
    return tuple(
        "".join(_ALPHABET[(r * 7 + c * 3 + (r * c) % 5) % len(_ALPHABET)] for c in range(cols))
        for r in range(rows)
    )


ART = make_art()
STEP = 1.0 / splash.RASTER_ROWS_PER_SECOND

# One sweep: the line crosses every row and then travels ``WAKE_ROWS`` further while the
# wake drains off the bottom edge. This is the window nearly every claim below is made
# over, because a shorter one leaves rows the line has not reached yet -- a cell only
# substitutes while the line is within ``WAKE_ROWS`` of its row, so a 40-step sample of a
# 48-row art reports the bottom third of the field as dead.
SWEEP_STEPS = len(ART) + splash.WAKE_ROWS

# The steps at which the whole wake lies on the art: the trailing edge has entered at the
# top and the line has not yet left the bottom. The rate of substitution rises through the
# steps before this and falls through the ones after, and that rise and fall is the sweep
# itself rather than a change of rate -- which is why the bounds that would be violated by
# it are taken over this stretch and named for it.
PLATEAU_STEPS = range(splash.WAKE_ROWS - 1, len(ART) - 1)


def frames_over_steps(n: int, art: tuple[str, ...] = ART) -> list[splash.ArtFrame]:
    """One frame per step of travel, ``n`` steps starting at t=0."""
    return [splash.art_frame(art, RANK_OF_BROKEN, RANKS, i * STEP + STEP / 2) for i in range(n)]


def lines_over_steps(n: int, art: tuple[str, ...] = ART) -> list[tuple[str, ...]]:
    """Just the glyphs of ``frames_over_steps``, for the tests that ignore luminance."""
    return [frame.lines for frame in frames_over_steps(n, art)]


def chars_seen(frames: list[tuple[str, ...]]) -> dict[tuple[int, int], set[str]]:
    """Every character each cell took across ``frames``."""
    seen: dict[tuple[int, int], set[str]] = {}
    for frame in frames:
        for r, line in enumerate(frame):
            for c, ch in enumerate(line):
                seen.setdefault((r, c), set()).add(ch)
    return seen


# -- the art -------------------------------------------------------------------


def test_the_same_instant_renders_the_same_frame_within_a_process() -> None:
    now = 12.345
    first = splash.art_frame(ART, RANK_OF_BROKEN, RANKS, now)
    # Interleave other times, so a hidden "advance on every call" counter would show.
    for other in (0.0, 99.5, 3.25):
        splash.art_frame(ART, RANK_OF_BROKEN, RANKS, other)
    assert splash.art_frame(ART, RANK_OF_BROKEN, RANKS, now) == first


def test_the_frame_does_not_depend_on_the_interpreter_hash_seed() -> None:
    """
    The cell hash is ours, not CPython's.

    ``hash()`` over anything containing a string is salted per process, so an
    implementation that reached for it would give a different cycling set on every
    launch -- membership that is stable within a run and different between runs is the
    hardest version of this defect to notice. Two child interpreters with opposite
    seeds is the only way to make that visible from a test.
    """
    root = Path(__file__).resolve().parents[1]
    program = (
        "from pptmstr.ui import splash;"
        "from tests.test_splash import ART, RANKS, RANK_OF_BROKEN;"
        "print(repr(splash.art_frame(ART, RANK_OF_BROKEN, RANKS, 7.5)))"
    )
    # The parent's own frame is the reference. Comparing the two children to each other
    # would pass for an implementation that is stable per seed but seed-dependent only
    # in bits the fixture never reaches; comparing both to a third run is the claim.
    reference = repr(splash.art_frame(ART, RANK_OF_BROKEN, RANKS, 7.5))
    for seed in ("1", "1234567"):
        proc = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
            cwd=root,
            env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"},
        )
        assert proc.stdout.strip() == reference


def test_a_ranked_space_neither_covers_ink_nor_gets_covered() -> None:
    """
    The adversarial input, not the contracted one, and in both directions.

    ``RANK_OF`` is contracted to omit U+0020, so a fixture that merely honours the
    contract cannot tell a renderer that guards against a ranked space from one that
    happens never to meet it. The art's shape *is* its negative space, and a table that
    grew a space entry dissolves that shape from either side: a space overwritten with
    ink fills the gaps, and ink replaced by a space punches holes in the silhouette. A
    guard on only the first direction leaves the second, which is the visible half.

    The fixture adds the space to a bucket, and the flat pool is what makes that one
    edit reach everywhere: the space joins a pool of seven that every cycling cell draws
    from, so an unguarded renderer punches a hole at one successful draw in seven
    anywhere on the art rather than inside a single brightness band.
    """
    ranks_with_space = (("a", "b", "c", " "),) + RANKS[1:]
    rank_of_with_space = {c: i for i, bucket in enumerate(ranks_with_space) for c in bucket}
    # Two full sweeps, so every row spends every distance in the wake twice. The window is
    # the sweep's geometry: a cell is only writable while the line is passing it, so a
    # window shorter than a sweep never puts the guard under pressure at all on the rows
    # the line has not yet reached.
    for i in range(2 * SWEEP_STEPS):
        frame = splash.art_frame(ART, rank_of_with_space, ranks_with_space, i * STEP)
        for r, line in enumerate(frame.lines):
            for c, ch in enumerate(line):
                if ART[r][c] == " ":
                    assert ch == " ", f"space at ({r},{c}) became {ch!r} at step {i}"
                else:
                    assert ch != " ", f"ink at ({r},{c}) became a space at step {i}"


def test_an_unranked_glyph_is_left_alone_rather_than_raising() -> None:
    for frame in lines_over_steps(SWEEP_STEPS):
        for r, line in enumerate(frame):
            for c, ch in enumerate(line):
                if ART[r][c] == UNRANKED:
                    assert ch == UNRANKED


def test_a_rank_pointing_past_the_table_is_a_no_op_rather_than_an_index_error() -> None:
    for frame in lines_over_steps(SWEEP_STEPS):
        for r, line in enumerate(frame):
            for c, ch in enumerate(line):
                if ART[r][c] == OUT_OF_RANGE:
                    assert ch == OUT_OF_RANGE


def test_a_substitute_comes_only_from_the_ranked_inventory() -> None:
    """
    A cell may leave its brightness band. It may not leave the curated inventory.

    The buckets are a partition of one hand-checked set of glyphs, and what that set is
    checked for is not only relative brightness: scripts/verify_splash.py holds every
    member to a single advance width at every size ``fit_size`` can return, and to
    presence in the baked atlas. A substitute from outside it is a column that no longer
    lines up, or a tofu box, and neither is recoverable by the space guards next door --
    they only see U+0020. That is the constraint the pool has to satisfy however wide it
    is, and it is the whole of what this test asserts.

    The rank is still read, because a cell whose glyph is unranked or whose rank does not
    index a live bucket is not eligible to substitute at all and keeps the art's own
    glyph. That half is owned by the two no-op tests above.
    """
    for frame in lines_over_steps(SWEEP_STEPS):
        for r, line in enumerate(frame):
            for c, ch in enumerate(line):
                original = ART[r][c]
                if RANK_OF.get(original) is None:
                    continue
                assert ch in POOL, f"({r},{c}) {original!r} -> {ch!r}"


def test_which_cells_cycle_is_fixed_for_the_life_of_the_process() -> None:
    """
    A cell moves over time exactly when ``is_cycling`` says it does and it is eligible.
    Membership that varied with the step would eventually pull in every cell, which reads
    as uniform noise rather than as texture.

    Eligible means ranked into a live bucket and in the cycling set, and that is the
    whole predicate: every eligible cell draws from the same six glyphs, so the size of
    the bucket the cell's own glyph sits in decides nothing. '#' is the case that makes
    that concrete -- it is alone in its bucket and it moves like everything else.

    The property being protected is that membership is a function of position alone, so
    the set of cells that ever move is exactly the set ``is_cycling`` names, on this step
    and on every other.

    Two sweeps, and one is thin rather than wrong. A cell is only writable for the
    ``WAKE_ROWS`` steps the line takes to cross it, and a successful draw lands back on
    the glyph it is already showing one time in ``len(POOL)``. Measured over 60 sweeps of
    this fixture: the fewest visible changes any movable cell makes in a single sweep is
    2, and over any two consecutive sweeps it is 11. One sweep would pass, but a floor of
    2 is close enough to zero that a re-partition or a smaller pool could reach it with
    nothing wrong, and the window is kept at two so the assertion fails for the reason it
    is named for.
    """
    seen = chars_seen(lines_over_steps(2 * SWEEP_STEPS))
    for (r, c), chars in seen.items():
        original = ART[r][c]
        bucket_index = RANK_OF.get(original)
        movable = bucket_index is not None and splash.is_cycling(r, c)
        assert (len(chars) > 1) is movable, f"({r},{c}) saw {sorted(chars)}, movable={movable}"


def test_roughly_the_configured_fraction_of_ranked_cells_cycles() -> None:
    ranked = [(r, c) for r, line in enumerate(ART) for c, ch in enumerate(line) if ch in RANK_OF]
    fraction = sum(splash.is_cycling(r, c) for r, c in ranked) / len(ranked)
    assert abs(fraction - splash.CYCLING_FRACTION) < 0.05, fraction


def test_the_substitutes_on_one_step_are_not_all_the_same_glyph() -> None:
    """
    A spatial claim, and only that one: the glyph a substitution lands on is a function
    of the cell as well as of the step, so the cells moving together on one step scatter
    across the pool instead of all becoming the same character. It says nothing about
    *when* those cells change --
    ``test_only_a_fraction_of_the_cycling_cells_change_on_any_one_step`` owns that, and a
    renderer that moved every cell on every step would pass this one.

    Counted over the *substitutes* and not over everything on show, which is the whole
    strength of the assertion. Every population here contains cells outside the wake
    still holding the art's own glyph, so a set of displayed characters has two members
    the moment any one cell moves, and "more than one character is visible" is true of
    every renderer that substitutes at all -- including one that drew a single glyph per
    step from the step index alone and repainted the entire wake in it, which is a solid
    band of one character marching down the panel. Dropping the unmoved cells is what
    makes that renderer fail: it leaves exactly one distinct substitute.

    Taken over the cells holding one art glyph rather than over a bucket. A bucket groups
    cells by a brightness the renderer does not consult, so it is not a population the
    claim is about. These 62 cells are identical in the source image, and what makes the
    panel read as texture is that they are not identical on screen: at the step sampled,
    5 of them have moved and they show 4 different glyphs between them.
    """
    frame = splash.art_frame(ART, RANK_OF_BROKEN, RANKS, 5 * STEP).lines
    substitutes = {
        frame[r][c]
        for r, line in enumerate(ART)
        for c, ch in enumerate(line)
        if ch == "a" and splash.is_cycling(r, c) and frame[r][c] != "a"
    }
    assert len(substitutes) > 1, substitutes


# The cells that can visibly move: in the cycling set and ranked into a live bucket. Every
# eligible cell draws from the whole of POOL, so there is no such thing here as a member
# that is physically unable to change and no cell has to be excluded to keep the fractions
# below fair. 404 cells of this fixture.
MOVABLE = [
    (r, c)
    for r, line in enumerate(ART)
    for c, ch in enumerate(line)
    if ch in RANK_OF and splash.is_cycling(r, c)
]

# What a step's change fraction should be, from the model rather than from a run. A cell
# whose row has intensity ``a`` on one step and ``b`` on the next shows the art's own glyph
# with probability ``1 - a`` and a uniform pick from POOL otherwise, and the two steps draw
# independently. Writing ``q = VISIBLE_DRAW = 1 - 1/len(POOL)``, the chance the two frames
# disagree collapses to
#
#     P(change) = q * (a + b - a*b)
#
# -- the cross terms cancel exactly, which is why the whole family of numbers below scales
# with ``q`` and nothing else about the pool matters. Checked against the renderer step by
# step over a sweep: mean absolute error 0.010, worst 0.040, which is the sampling spread of
# 404 Bernoulli cells and not a bias.
#
# The three bounds are set against measurements taken over 60 sweeps rather than over the
# two the test samples, so that a bound is not pinning which sweep it happens to look at.


# The whole cycling set moving together is 1.0, and that is what this separates the panel
# from. Measured over 60 sweeps the largest single step moves 0.3045 of MOVABLE, and the
# per-sweep maximum ranges 0.2698 to 0.3045; the two sweeps sampled here read 0.2946. The
# bound has 15% of headroom over that, and nothing sits in the gap that a tighter one would
# catch -- the nearest mutation above the design is 0.4158.
#
# What it catches, by mutation and measured rather than argued -- every mutant figure here
# is that mutant's own 60-sweep extreme: deleting the ramp and firing every row of the wake
# at full intensity reads 0.4158, and removing the wake so every cycling cell draws on every
# step reads 0.8812. The first of those is only caught because the pool is wide. A flat ramp
# moves the same cells whatever the pool is; what scales with ``VISIBLE_DRAW`` is how many
# of those moves are visible, and at a pool of two or three glyphs the same mutant lands
# under this bound. So the ramp is not left to this test -- it is pinned by
# ``test_the_draw_rate_falls_off_linearly_with_distance_behind_the_line`` -- and catching it
# here is a side effect that a narrower pool would take away again.
#
# What it does not catch: a wake that straddles the line rather than trailing it reads
# 0.2847, *under* the design's own 0.3045, so no upper bound can see it at all.
# MIN_SIMULTANEOUS_CHANGE is what carries that one, and is set where it is for that reason.
MAX_SIMULTANEOUS_CHANGE = 0.35

# The floor, and it applies only while the whole wake is on the art. Over a full sweep the
# rate legitimately reaches zero: the last WAKE_ROWS steps are the drain, when the line has
# left the bottom edge and the trail is emptying, and that beat is what stops the next
# sweep entering at the top while the previous one is still lit. Across PLATEAU_STEPS the
# geometry is constant and the measured floor over 60 sweeps is 0.1980.
#
# The value is what catches the locality mutation no upper bound can see: a wake centred on
# the line rather than trailing it -- same depth, same ramp shape, reflected -- reads a floor
# of 0.1139 over 60 sweeps and 0.1188 over the two sampled here, so 0.15 fails it either way
# while leaving the design 32% of headroom. A renderer that held each substitute for two
# steps reads 0.0149 over 60 sweeps and 0.0173 over the two sampled here.
#
# It does *not* pick up the even-step gating that STEADY_RATE_SPREAD is also blind to, and
# the reason is worth stating because the numbers nearly touch: that mutant's floor is 0.1386
# over 60 sweeps, which 0.15 would catch, but 0.1609 over the two sweeps this test samples,
# which it does not. Lengthening the sample would catch it here as well. It is left at two
# sweeps because ``test_the_moving_set_does_not_repeat_within_the_first_sixty_ticks`` already
# fails on that mutation directly, and a bound that depends on how long you look is a worse
# guard than one that does not.
MIN_SIMULTANEOUS_CHANGE = 0.15

# ...and how much the fraction may vary within that stretch. The rate rises as the wake
# enters the art and falls as it drains, once per sweep, so a spread taken over a whole
# sweep would be measuring the sweep. Over PLATEAU_STEPS the wake covers a constant 20
# rows, and what is left is the art's own row composition and the draw's noise: measured
# 0.0866 over the two sweeps sampled here, and 0.0941 for the worst two-sweep window in 60.
#
# What it catches, checked by mutation rather than argued: a renderer that re-rolled its
# draw only every other step -- the "hold the last substitute" design ``art_frame``'s
# docstring rejects -- moves the field on even steps and leaves it alone on odd ones, which
# reads 0.2748 here and 0.0149 on the floor above.
#
# What it does not catch, which matters more: substitution gated to even steps, with the
# art restored on the odd ones, is a beat at half the step rate that this statistic is blind
# to -- the restoration is itself a change, so both parities read alike and the spread comes
# out at 0.0866, no wider than the design's own 0.0941. Measured. That defect is caught by
# ``test_the_moving_set_does_not_repeat_within_the_first_sixty_ticks`` instead, and the
# reason this bound is kept anyway is that the two see different halves of the hazard.
STEADY_RATE_SPREAD = 0.15


def test_only_a_fraction_of_the_cycling_cells_change_on_any_one_step() -> None:
    """
    The temporal claim: substitutions are spread across steps, not fired on one.

    This is the safety-relevant property of the panel, not a stylistic one. Every cycling
    cell advancing on the same step is ~4.4k glyphs changing together at
    RASTER_ROWS_PER_SECOND -- full-field flicker at 16Hz, inside the band photosensitivity
    guidance treats as a risk, on a panel that is up for as long as the app is empty.
    Spatial variety at one instant cannot see it: with a global step every cell still
    shows a different glyph from its neighbour, and they all change at once anyway.

    What holds the fraction down is the wake's *locality*: only the WAKE_ROWS rows behind
    the line can change at all, and the ramp thins them further. The three bounds and what
    each is protecting are argued where they are defined; the sample is two full sweeps so
    that the maximum is taken over the stretch where the wake is fully on the art and the
    minimum is not taken over the drain, where zero is the correct answer.
    """
    frames = lines_over_steps(2 * SWEEP_STEPS + 1)
    fractions = [
        sum(a[r][c] != b[r][c] for r, c in MOVABLE) / len(MOVABLE)
        for a, b in zip(frames, frames[1:], strict=False)
    ]
    assert max(fractions) < MAX_SIMULTANEOUS_CHANGE, max(fractions)

    plateau = [fractions[sweep * SWEEP_STEPS + i] for sweep in range(2) for i in PLATEAU_STEPS]
    assert min(plateau) > MIN_SIMULTANEOUS_CHANGE, min(plateau)
    assert max(plateau) - min(plateau) < STEADY_RATE_SPREAD, (min(plateau), max(plateau))


def test_the_moving_set_does_not_repeat_within_the_first_sixty_ticks() -> None:
    """
    Pins that the draw is re-rolled per step, which is otherwise a comment.

    *Which* cells move on a step comes from ``_draw_hash``, which mixes the step index in.
    The cheaper design the module argues against -- one fixed threshold per cell, compared
    against its row's intensity -- makes this set a function of the line's position alone,
    so the same cells light first on every sweep and the lowest-threshold ones are lit for
    the whole 1.25s the wake covers them. The change rate stays perfectly steady while the
    same cells take turns in the same order, so neither bound above sees it.

    Sixty steps rather than a whole 68-step sweep, and the difference is where the claim
    stops being true: by the last few steps of the drain the wake has thinned to one or two
    movable cells and sometimes to none, and two consecutive steps moving the same single
    cell -- or no cell -- is a coincidence of a nearly empty set rather than a recurrence.
    Measured on this fixture over 60 sweeps: every one of the first sixty sets is distinct
    in every sweep, and the earliest any sweep repeats at all is at 65 steps. The five
    steps between those two numbers are the whole margin this constant has.
    """
    frames = lines_over_steps(61)
    moving = [
        frozenset((r, c) for r, c in MOVABLE if a[r][c] != b[r][c])
        for a, b in zip(frames, frames[1:], strict=False)
    ]
    assert len(set(moving)) == len(moving)


# Measured 404 of 404 movable cells in the first sweep, and 402 in the worst of 60. The
# bound is a floor on that count, not the count itself: what is being pinned is that
# consecutive-step substitution is normal near the line, and pinning it to the cell is
# pinning the hash.
#
# 380 is 94% of MOVABLE and 5.5% under the worst sweep measured, and it is a floor scaled to
# the population rather than a threshold that separates anything: the design reads 402 and
# the one mutation this test does catch reads 246, so any bound between them does the same
# work. It is set here rather than lower so that it keeps saying "normal" as the population
# moves, which is the claim the test's name makes.
CONSECUTIVE_SUBSTITUTIONS = 380


def test_a_cell_near_the_line_may_substitute_on_consecutive_steps() -> None:
    """
    Consecutive-step substitution is allowed, and that is a decision rather than a gap.

    A floor on the *gap* between one cell's substitutions is the obvious per-cell half of
    the photosensitivity argument, and this design does not have it: intensity is 1.0 on
    the raster row and 0.95 one row behind it, so a cell being overtaken by the line draws
    on both steps and very probably changes on both. It is recorded as a cost rather than
    as a property (``planning/archive/2026-08-15-the-splash-cycles-behind-a-raster-line.md``, "Two
    costs"), and what bounds the cost is the wake passing: a cell sees at most
    RASTER_ROWS_PER_SECOND changes a second for the 1.25s the wake is over it, once per
    sweep, against a field that is otherwise still.

    Asserted rather than left unsaid because the "hold the last substitute" cache
    ``art_frame``'s docstring rejects is exactly the kind of thing a well-meaning change
    reintroduces, and this test fails when it is: a renderer that re-rolls its draw only
    every other step reads 246 of the 404.

    What it does not catch, measured rather than assumed, because the obvious reading of
    the name is wrong: a genuine per-cell cooldown -- a cell whose draw succeeded last step
    is forbidden to draw on this one -- reads 401 of 404 and passes, even applied to every
    cell. The statistic is consecutive *visible change*, and when a cooldown blocks a draw
    the cell reverts to the art's own glyph, which is itself a change on both sides of the
    step. So this pins the absence of a substitute cache and not the absence of a gap rule,
    and a gap rule would have to be caught by something that can see which glyph is shown
    rather than only that it moved.
    """
    frames = lines_over_steps(SWEEP_STEPS)
    consecutive = sum(
        1
        for r, c in MOVABLE
        if any(
            frames[i][r][c] != frames[i - 1][r][c] and frames[i + 1][r][c] != frames[i][r][c]
            for i in range(1, len(frames) - 1)
        )
    )
    assert consecutive >= CONSECUTIVE_SUBSTITUTIONS, (consecutive, len(MOVABLE))


def test_the_animation_actually_advances() -> None:
    frames = frames_over_steps(12)
    assert len({f for f in frames}) > 1


def test_two_instants_inside_one_step_render_the_same_frame() -> None:
    """The step index, not ``now``, is what the frame is a function of."""
    base = 4 * STEP
    assert splash.art_frame(ART, RANK_OF_BROKEN, RANKS, base + 0.001) == splash.art_frame(
        ART, RANK_OF_BROKEN, RANKS, base + STEP * 0.9
    )


def test_the_cached_path_matches_the_pure_one_frame_for_frame() -> None:
    cache = splash.ArtFrames()
    # Sub-step increments, so most calls are hits and the boundaries are crossed.
    for i in range(400):
        now = i * STEP / 3.0
        assert cache.frame(ART, RANK_OF_BROKEN, RANKS, now) == splash.art_frame(
            ART, RANK_OF_BROKEN, RANKS, now
        )


def test_the_cache_recomputes_once_per_step_and_not_once_per_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The whole reason ``ArtFrames`` is keyed on the step index rather than on ``now``.

    Correctness alone cannot tell the two apart -- a memo keyed on the float returns
    exactly the same frames, it just never hits -- so nothing but a call count can
    distinguish a working cache from an ornamental one.
    """
    calls = 0
    real = splash.art_frame

    def counted(*args: object, **kwargs: object) -> splash.ArtFrame:
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(splash, "art_frame", counted)
    cache = splash.ArtFrames()
    steps = 8
    per_step = 5
    for i in range(steps * per_step):
        cache.frame(ART, RANK_OF_BROKEN, RANKS, i * STEP / per_step)
    assert calls == steps, f"{calls} rebuilds for {steps} steps of {per_step} frames"


def test_the_cache_does_not_freeze_on_its_first_answer() -> None:
    cache = splash.ArtFrames()
    seen = {cache.frame(ART, RANK_OF_BROKEN, RANKS, i * STEP) for i in range(12)}
    assert len(seen) > 1


def test_art_with_no_cycling_material_is_returned_unchanged() -> None:
    plain = ("   ", "???", "")
    frame = splash.art_frame(plain, RANK_OF_BROKEN, RANKS, 3.7)
    assert frame.lines == plain
    # One luminance per line and never a ragged pair, even when nothing was substituted.
    assert len(frame.luminance) == len(plain)


# -- the sweep -----------------------------------------------------------------
#
# The line leads its wake, so everything it has touched is *above* it, at lower row
# indices. The tests below are the geometry: where substitution is allowed to happen, how
# hard it happens as a function of distance, that no band of the art is left out of it,
# which rows are lit, and that the sweep repeats on the period the module says it does.


def test_nothing_substitutes_ahead_of_the_line_or_beyond_the_wake() -> None:
    """
    The wake is bounded and behind the line, which is the whole locality argument.

    Everything that protects this panel from reading as full-field flicker rests on the
    modulation being confined to WAKE_ROWS of the art's rows and moving -- so a row
    substituting ahead of the line, or one still substituting after the line has left it
    behind by more than the wake, is not a cosmetic defect but the failure of the argument
    ``MAX_SIMULTANEOUS_CHANGE`` is derived from.

    Two sweeps, so the wrap at the end of a sweep is inside the window: an implementation
    that took the distance without wrapping would be correct on the first sweep and light
    the whole art on the second.
    """
    touched: set[int] = set()
    for step, frame in enumerate(lines_over_steps(2 * SWEEP_STEPS)):
        raster = step % SWEEP_STEPS
        for row, line in enumerate(frame):
            if line == ART[row]:
                continue
            assert 0 <= raster - row < splash.WAKE_ROWS, (step, row, raster)
            touched.add(row)
    # ...and the bound is not being satisfied by an art that never substitutes at all.
    assert touched == set(range(len(ART)))


def test_every_row_is_inside_the_wake_at_some_point_in_a_sweep() -> None:
    """
    No frozen band: every row substitutes at least once per sweep.

    The complement of the test above, and the reason it is worth its own name. A bound on
    where the wake may reach is satisfied perfectly by a wake that never reaches anywhere,
    and the failure that would produce -- an off-by-one in the wrap, a ramp that reaches
    zero a row early, a row index compared against the wrong height -- leaves a stripe of
    the art visibly dead while every other test here still passes. Measured: all 48 rows
    are touched in each of the 60 sweeps sampled, and the thinnest row of this fixture has
    four movable cells.
    """
    frames = lines_over_steps(2 * SWEEP_STEPS)
    every_row = set(range(len(ART)))
    for sweep in range(2):
        window = frames[sweep * SWEEP_STEPS : (sweep + 1) * SWEEP_STEPS]
        touched = {row for frame in window for row in every_row if frame[row] != ART[row]}
        assert touched == every_row, sorted(every_row - touched)


# How many full sweeps to average over when reading the ramp off the rendered lines. The
# draw is re-rolled per cell per step, so one sweep gives each distance a single sample per
# row and the reading is noise: the ramp puts 1/WAKE_ROWS x VISIBLE_DRAW = 0.042 between one
# distance and the next, while the measured error at a distance is 0.045 at two sweeps, 0.030
# at four, 0.017 at eight, 0.010 at sixteen, 0.0088 at thirty and 0.0053 at sixty. At two
# sweeps the reading is not monotonic at all.
#
# The wider pool made this cheaper rather than dearer, and the sample is kept where it was
# anyway. The gap to resolve is what grew -- it was 0.029 when a successful draw was
# invisible 42% of the time, and the noise at a given sample size did not move -- so sixteen
# sweeps is the first that clears half the gap comfortably. Thirty is kept over sixteen
# because it costs ~1s either way and it is what leaves SURVIVAL_TOLERANCE sitting at twice
# the measurement rather than at 1.5 times it.
INTENSITY_SWEEPS = 30

# The gap between measurement and prediction that is attributed to sampling rather than to
# a wrong ramp. Measured 0.0088 at INTENSITY_SWEEPS, and it keeps falling with the sample
# (0.0053 at sixty), so this is a bound on the noise and not on the model. Held at 0.02 as
# the pool widened, which tightened it in the only terms that matter: it was 0.69 of the gap
# between adjacent distances and is now 0.48 of it.
SURVIVAL_TOLERANCE = 0.02


@functools.cache
def survival_by_distance(sweeps: int) -> tuple[float, ...]:
    """
    The share of MOVABLE cells still showing the art's own glyph, by rows behind the line.

    One entry per row of the wake, averaged over ``sweeps`` full sweeps. Every row is at
    every distance exactly once per sweep, so the population behind each entry is the same
    set of cells -- which is what lets the entries be compared to each other without the
    art's row composition entering the comparison.

    Cached because two tests read this table and building it is the most expensive thing
    in the file.
    """
    frames = lines_over_steps(sweeps * SWEEP_STEPS)
    return tuple(
        sum(
            1
            for sweep in range(sweeps)
            for r, c in MOVABLE
            if frames[sweep * SWEEP_STEPS + r + distance][r][c] == ART[r][c]
        )
        / (sweeps * len(MOVABLE))
        for distance in range(splash.WAKE_ROWS)
    )


def surviving_share(distance: int) -> float:
    """
    What ``survival_by_distance`` reads at ``distance`` if the ramp is what the module says.

    A cell ``d`` rows behind the line draws with probability ``1 - d / WAKE_ROWS``. When
    the draw fails it shows the art's own glyph; when it succeeds it picks uniformly from
    ``POOL`` and lands back on the art's glyph one time in ``len(POOL)``. So the share
    still showing the original is ``1 - intensity * VISIBLE_DRAW``, which is linear in the
    intensity and therefore in the distance.

    The flat pool is what makes the second term a single number instead of an average over
    the population. Every cell's draw is invisible with the same probability now, so this
    curve is the same for every cell in MOVABLE rather than a mean over cells whose buckets
    were different sizes -- which also means the residual below is pure sampling noise and
    carries no composition term.

    Derived from the ramp rather than from the implementation: a quadratic ramp, a wake of
    a different depth, or an intensity that did not reach zero at ``WAKE_ROWS`` all move
    this curve, and the test below reads the residual against it.
    """
    return 1.0 - (1.0 - distance / splash.WAKE_ROWS) * VISIBLE_DRAW


def test_the_draw_rate_falls_off_linearly_with_distance_behind_the_line() -> None:
    """
    The ramp, measured -- and measured on survival rather than on change.

    The obvious statistic is how often a cell changes, and it is the wrong one. A cell one
    row behind the line is compared against a glyph that was itself substituted a step ago,
    so the change rate saturates: distance 0 and distance 1 both read close to VISIBLE_DRAW
    and the first row of the ramp is nearly invisible in it. How often the art's *own* glyph
    survives has no such baseline -- it is a direct linear readout of the draw intensity, and
    it is strictly monotonic across the whole wake.

    Two assertions, because monotonicity alone is weak: a ramp of the wrong shape or the
    wrong depth is still monotonic. The residual against ``surviving_share`` is what pins
    the shape, and at INTENSITY_SWEEPS it is 0.0088 against a curve that spans 0.833.
    """
    survival = survival_by_distance(INTENSITY_SWEEPS)
    for distance in range(splash.WAKE_ROWS - 1):
        assert survival[distance] < survival[distance + 1], (distance, survival)
    worst = max((abs(survival[d] - surviving_share(d)), d) for d in range(splash.WAKE_ROWS))
    assert worst[0] < SURVIVAL_TOLERANCE, (worst, survival)


def test_every_cycling_cell_on_the_raster_row_is_redrawn() -> None:
    """
    Intensity is 1.0 on the line, and the only evidence for it is an aggregate.

    A redraw is not observable per cell. The substitute is picked from ``POOL`` without
    regard to what is there, so a cell that is redrawn with the glyph it already had is
    indistinguishable in the output from a cell that was never drawn -- on this fixture's
    six-glyph pool that is one redraw in six. Any test asserting that every cycling cell on
    the raster row *changes* fails about a sixth of the time per cell, and would have to be
    weakened until it said nothing.

    The flat pool narrows that hole without closing it, and the shipped table narrows it
    much further -- 165 glyphs makes an invisible redraw a 0.6% event -- which is exactly
    why the aggregate is still the right instrument here rather than a per-cell assertion
    that would pass on the real art and be untestable on any fixture small enough to reason
    about.

    What survives the aggregate is exact: if every cycling cell on the raster row is
    redrawn, the share still showing the art's glyph there is one over the pool size, which
    is ``1 - VISIBLE_DRAW`` = 0.167. Any cell not redrawn can only push that up. Measured
    0.163 over INTENSITY_SWEEPS.
    """
    survival = survival_by_distance(INTENSITY_SWEEPS)
    assert abs(survival[0] - (1.0 - VISIBLE_DRAW)) < SURVIVAL_TOLERANCE, survival[0]


def test_only_the_line_and_the_row_it_has_left_are_lit() -> None:
    """
    The luminance ramp is two rows, and it is not the substitution ramp.

    The wake is twenty rows deep and the highlight is two, deliberately: spreading the
    brightness over the whole wake lights a third of the panel and loses the line, which is
    the thing being drawn. So this is the property that would quietly disappear if the two
    ramps were ever unified -- the art would still cycle, still be local, still pass every
    other test in this file, and read as a glowing block instead of a scan.

    Two rows rather than one is a frame-rate bound rather than a taste: a band narrower
    than its own per-frame displacement leaves rows that nothing ever lights. That bound
    lives in ``splash.RASTER_ROWS_PER_SECOND`` and cannot be checked from here, because
    what it is a bound against is the rate the window really redraws at.
    """
    for step, frame in enumerate(frames_over_steps(2 * SWEEP_STEPS)):
        assert len(frame.luminance) == len(frame.lines)
        lit = [row for row, level in enumerate(frame.luminance) if level > splash.Luminance.DIM]
        assert len(lit) <= 2, (step, lit)
        raster = step % SWEEP_STEPS
        if raster < len(ART):
            assert frame.luminance[raster] == splash.Luminance.RASTER
            assert lit == ([raster - 1, raster] if raster else [raster]), (step, lit)
        else:
            # The drain: the line is off the bottom edge, so nothing is at full brightness.
            assert splash.Luminance.RASTER not in frame.luminance, step


def test_the_sweep_repeats_every_art_plus_wake_steps() -> None:
    """
    The period is ``rows + WAKE_ROWS`` and not ``rows``, and the difference is the drain.

    Through the last WAKE_ROWS steps the line is off the bottom edge and only the wake
    empties. That beat is load-bearing: without it the next sweep enters at the top while
    the previous trail is still lit, and two lit bands with no line between them read as
    noise rather than as a scan.

    What repeats is the geometry, not the picture. ``_draw_hash`` mixes the *unwrapped*
    step index, so the cells that substitute are drawn afresh on every sweep -- if the
    glyphs repeated too, the panel would be a 4.25s loop of one fixed pattern, which is
    what a per-cell fixed threshold would have produced and what the per-step draw exists
    to avoid. Measured on this fixture: 66 of the 68 steps render different glyphs on the
    next sweep, and the two that match are the last two, where the wake has drained and
    both frames are the untouched art.
    """
    first = frames_over_steps(SWEEP_STEPS)
    second = frames_over_steps(2 * SWEEP_STEPS)[SWEEP_STEPS:]
    for step, (a, b) in enumerate(zip(first, second, strict=True)):
        assert a.luminance == b.luminance, step
    assert sum(1 for a, b in zip(first, second, strict=True) if a.lines != b.lines) >= 60

    drained = [step for step, frame in enumerate(first) if frame.lines == ART]
    assert drained and all(step >= len(ART) for step in drained), drained


# -- the quote -----------------------------------------------------------------


QUOTE_TIMES = [i * splash.SHIMMER_PERIOD_SECONDS / 97.0 for i in range(300)]


def test_the_quote_never_changes_its_text_or_its_character_count() -> None:
    """
    Nothing the animation does can reflow the block: the pane draws the same
    characters in the same columns on every frame, and only their alpha moves.
    """
    for now in QUOTE_TIMES:
        frame = splash.quote_frame(now)
        assert frame.lines == splash.QUOTE_LINES
        assert tuple(len(row) for row in frame.alphas) == tuple(
            len(line) for line in splash.QUOTE_LINES
        )


def test_the_shimmer_only_ever_adds_light_to_the_base_alpha() -> None:
    """
    What makes QUOTE_BASE_ALPHA the worst case rather than a typical one.

    The contrast test below evaluates one alpha, which is only sound if no character at
    any instant is dimmer than it. Asserted rather than assumed because the trough is
    exactly what a sampled search can step over: a sweep that subtracted light for part
    of its cycle would show a legible quote at almost every ``now`` and fail AA in the
    gaps between the samples.
    """
    for now in QUOTE_TIMES:
        for row in splash.quote_frame(now).alphas:
            for alpha in row:
                assert splash.QUOTE_BASE_ALPHA <= alpha <= 1.0 + 1e-9, alpha


# The floor tests/test_theme.py already enforces on every palette's text/background pair,
# spelled out here rather than imported: this test's subject is the alpha multiplier, and
# a floor read from the neighbour would move if the neighbour's did.
WCAG_AA_BODY = 4.5


def _unpack(packed: int) -> tuple[float, float, float, float]:
    """
    An IM_COL32 value back to (r, g, b, a) floats. The packing is ABGR, which
    tests/test_theme.py::test_color_precomputes_both_forms pins.
    """
    return (
        (packed & 0xFF) / 255.0,
        ((packed >> 8) & 0xFF) / 255.0,
        ((packed >> 16) & 0xFF) / 255.0,
        ((packed >> 24) & 0xFF) / 255.0,
    )


def _composited(text: theme.Color, alpha: float, background: theme.Color) -> theme.Color:
    """
    What the framebuffer holds after ``text`` is drawn at ``alpha`` over ``background``.

    Runs the colour through ``theme.faded`` and unpacks what comes back, rather than
    reimplementing the twelve-step quantisation. The quantisation is the whole hazard --
    a requested 0.79 and a requested 0.87 are the same drawn colour -- so a test that
    modelled it separately would be checking its own model.

    The blend is computed on the 8-bit sRGB-encoded values, which is what a framebuffer
    that is not sRGB-capable does. Nothing in this repo asks for one: ``grep -ri srgb
    pptmstr scripts`` is empty, and both GLFW's SRGB_CAPABLE hint and GL_FRAMEBUFFER_SRGB
    default to off. That assumption carries the whole margin rather than a rounding
    error's worth of it -- blending these same alphas in linear space puts CDE at 3.37:1
    here, and no alpha below 1.0 would clear AA on it. If a framebuffer format is ever
    chosen deliberately, this is the test that has to be revisited.
    """
    r, g, b, a = _unpack(theme.faded(text, alpha))
    mixed = tuple(a * fg + (1.0 - a) * bg for fg, bg in zip((r, g, b), background.rgb, strict=True))
    return theme.Color(vec4=imgui.ImVec4(*mixed, 1.0), u32=0)


@pytest.mark.parametrize("palette", list(theme.THEMES.values()), ids=list(theme.THEMES))
def test_the_dimmest_the_quote_ever_gets_still_clears_wcag_aa(palette: theme.Palette) -> None:
    """
    The guarantee tests/test_theme.py cannot make.

    That file holds every palette's text/background pair at 4.5:1, and it is exactly
    right about the palettes -- but it sees colours, not the alpha an animation
    multiplies into them, so a splash can fail its floor without touching a colour. Every
    palette and both surfaces, because the one that decides this is CDE and the one
    everybody looks at is DARK, which passes at alphas CDE does not.
    """
    for surface in (palette.bg, palette.panel):
        ratio = theme.contrast_ratio(
            _composited(palette.text, splash.QUOTE_BASE_ALPHA, surface), surface
        )
        assert ratio >= WCAG_AA_BODY, f"{palette.name}: {ratio:.2f}:1"


def test_the_highlight_travels_off_the_ends_rather_than_pulsing_in_place() -> None:
    """
    What the brightest-character-per-frame series can see, which is the travel.

    If the sweep never left the block, the brightest character would sit at full alpha
    on every frame and this series would be flat; that it dips is the quiet stretch
    ``quote_frame`` promises. The bound is a fraction of the animation's own depth
    rather than an absolute alpha, deliberately: the depth is set by the contrast floor
    next door and can move for reasons that have nothing to do with this property, and a
    fixed bound would make this test fail for the floor's reasons instead of its own.
    """
    highs = [max(max(row) for row in splash.quote_frame(now).alphas) for now in QUOTE_TIMES]
    depth = 1.0 - splash.QUOTE_BASE_ALPHA
    assert max(highs) - min(highs) > 0.3 * depth, (max(highs), min(highs), depth)


def test_the_sweep_survives_theme_fadeds_quantisation() -> None:
    """
    The cost of the contrast floor, pinned where it can be seen.

    The sweep has only ``1 - QUOTE_BASE_ALPHA`` to work with, and ``theme.faded``
    quantises to twelve steps before anything is drawn -- so "the alphas vary" and "the
    drawn colour varies" are different claims, and raising the floor far enough collapses
    the second while leaving the first true. Three distinct packed colours is what the
    current floor leaves; the previous one left six. Below three there is no gradient at
    the leading edge and the quote blinks between two states, at which point a static
    quote would be the better trade and this test is the place that argument starts.
    """
    text = theme.THEMES["dark"].text
    drawn = {
        theme.faded(text, alpha)
        for now in QUOTE_TIMES
        for row in splash.quote_frame(now).alphas
        for alpha in row
    }
    assert len(drawn) >= 3, len(drawn)


def test_the_shimmer_sweeps_rather_than_lighting_everything_at_once() -> None:
    """At the brightest instant the highlight is local, not a global fade."""
    flat = [a for now in QUOTE_TIMES for row in splash.quote_frame(now).alphas for a in row]
    assert min(flat) < splash.QUOTE_BASE_ALPHA + 0.01
    assert max(flat) > 0.95


def test_the_quote_cycle_repeats() -> None:
    a = splash.quote_frame(2.0)
    b = splash.quote_frame(2.0 + splash.SHIMMER_PERIOD_SECONDS)
    for row_a, row_b in zip(a.alphas, b.alphas, strict=True):
        for x, y in zip(row_a, row_b, strict=True):
            assert math.isclose(x, y, abs_tol=1e-9)


# -- fitting -------------------------------------------------------------------

ROWS, COLS = 61, 72


def test_fit_size_only_ever_returns_an_even_size() -> None:
    """
    Two properties in one, and the second is the one that is easy to lose.

    Whole, because ImGui 1.92 bakes a fresh atlas entry per distinct size and a
    fractional size varying with the pane would rasterise the face again on every frame
    of a drag. *Even*, because the baked advance is half the pushed size only on even
    sizes -- at 7 and 9 it is more, and GLYPH_ADVANCE_EM stops being an upper bound
    there. An any-integer fit_size passes the first half of this and overruns the pane.
    """
    for w in range(200, 4000, 37):
        for h in range(200, 3000, 53):
            size = splash.fit_size(float(w), float(h), ROWS, COLS)
            assert size == int(size), size
            assert int(size) % 2 == 0, (w, h, size)


def test_fit_size_never_leaves_the_clamp_range() -> None:
    extremes = [-1e6, -1.0, 0.0, 1.0, 10.0, 1e3, 1e7]
    for w in extremes:
        for h in extremes:
            size = splash.fit_size(w, h, ROWS, COLS)
            assert splash.MIN_FONT_SIZE <= size <= splash.MAX_FONT_SIZE, (w, h, size)


def test_fit_size_never_shrinks_when_the_pane_grows() -> None:
    previous = 0.0
    for w in range(100, 6000, 17):
        size = splash.fit_size(float(w), 1e6, ROWS, COLS)
        assert size >= previous
        previous = size
    previous = 0.0
    for h in range(100, 6000, 17):
        size = splash.fit_size(1e6, float(h), ROWS, COLS)
        assert size >= previous
        previous = size


def test_the_size_fit_size_returns_actually_fits() -> None:
    for w in range(400, 4000, 43):
        for h in range(400, 3000, 61):
            size = splash.fit_size(float(w), float(h), ROWS, COLS)
            if size <= splash.MIN_FONT_SIZE:
                continue  # the floor is returned unfitted, on purpose
            need_w, need_h = splash.required_extent(size, ROWS, COLS)
            assert need_w <= w + 1e-9 and need_h <= h + 1e-9, (w, h, size, need_w, need_h)


def test_a_pane_a_hair_short_of_a_size_gets_the_size_below_it() -> None:
    """
    The boundary, which a stride over pane sizes only reaches by luck.

    ``fit_size`` and ``required_extent`` compute the same geometry from opposite
    directions, and the failure mode of a duplicated formula is that the two disagree by
    less than a size step -- dropping the descender term costs 0.049 em, which a sweep
    of arbitrary panes almost never lands on. Feeding back a pane half a pixel under
    each exact fit puts every test case on the boundary instead.
    """
    for size in range(int(splash.MIN_FONT_SIZE) + 2, int(splash.MAX_FONT_SIZE) + 1, 2):
        need_w, need_h = splash.required_extent(float(size), ROWS, COLS)
        assert splash.fit_size(need_w, need_h, ROWS, COLS) == size, size
        assert splash.fit_size(need_w - 0.5, need_h, ROWS, COLS) == size - 2, size
        assert splash.fit_size(need_w, need_h - 0.5, ROWS, COLS) == size - 2, size


def test_fit_size_returns_the_largest_even_size_that_fits() -> None:
    """A conservative answer would pass the test above and still be wrong."""
    for w in range(400, 4000, 43):
        for h in range(400, 3000, 61):
            size = splash.fit_size(float(w), float(h), ROWS, COLS)
            if not splash.MIN_FONT_SIZE <= size < splash.MAX_FONT_SIZE:
                continue
            need_w, need_h = splash.required_extent(size + 2.0, ROWS, COLS)
            assert need_w > w + 1e-9 or need_h > h + 1e-9, (w, h, size)


def test_a_pane_too_small_for_the_art_gets_the_floor_not_an_exception() -> None:
    size = splash.fit_size(10.0, 10.0, ROWS, COLS)
    assert size == splash.MIN_FONT_SIZE
    # ...and the caller can see for itself that the floor does not fit, which is how
    # it decides not to draw.
    need_w, need_h = splash.required_extent(size, ROWS, COLS)
    assert need_w > 10.0 and need_h > 10.0


def test_an_empty_art_block_needs_no_height() -> None:
    assert splash.required_extent(16.0, 0, 0) == (0.0, 0.0)
    assert splash.fit_size(0.0, 0.0, 0, 0) == splash.MAX_FONT_SIZE


def test_required_extent_charges_the_descender_once_not_once_per_row() -> None:
    """
    The composition of the constants, which is all this process can check.

    The constants themselves come from a live frame and are pinned by
    scripts/verify_splash.py, not here: a test that recomputed 0.5 and 1.049 from the
    module's own literals would pass for any value of them. What is checkable in
    process is how they are combined -- the 1.049 em of ink is one addition for the last
    row's descender, and charging it per row instead would over-reserve 0.049 em on
    every row, three whole rows across the 61 of the real art.
    """
    one_row = splash.required_extent(20.0, 1, 10)[1]
    three_rows = splash.required_extent(20.0, 3, 10)[1]
    assert three_rows - one_row == pytest.approx(2 * 20.0 * splash.LINE_PITCH_EM)
    assert one_row == pytest.approx(20.0 * splash.INK_HEIGHT_EM)
    # Width is a function of columns alone; rows do not enter it.
    assert splash.required_extent(20.0, 1, 10)[0] == splash.required_extent(20.0, 40, 10)[0]


# The pane that actually hosts the splash: NEEDS YOU, docked in MainDockSpace. The widths
# are (1 - 0.21) * (1 - 0.32) of the window (app.py:613,616) and the heights are the
# window less about 190px of chrome; all three pairs are what scripts/verify_splash.py
# prints from a live frame, which is the only place they can come from. Pixel counts
# rather than a formula because the chrome is measured, not derived -- but they are the
# measured pane, not a guess at one, which is what this test's predecessor got wrong when
# the splash moved out of DETAIL and its constant stayed behind.
MEASURED_PANES = {
    (1024.0, 700.0): (550.0, 510.0),
    (1500.0, 900.0): (806.0, 710.0),
    (1920.0, 1200.0): (1031.0, 1010.0),
}
SMALLEST_WINDOW = (1024.0, 700.0)
CHROME_HEIGHT = 190.0
MAIN_DOCK_WIDTH_FRACTION = (1.0 - 0.21) * (1.0 - 0.32)


def needs_you_pane(window: tuple[float, float]) -> tuple[float, float]:
    """The NEEDS YOU region for a window size, less the chrome above and below it."""
    return window[0] * MAIN_DOCK_WIDTH_FRACTION, max(0.0, window[1] - CHROME_HEIGHT)


def test_the_pane_model_matches_what_a_live_frame_measured() -> None:
    """
    The one link in this chain that a headless process cannot check for itself.

    Everything below reasons about the pane from two split ratios and a chrome height. If
    that model has drifted from the real layout, those tests still pass and say nothing.
    Pinned to a pixel: the model is only as good as its agreement with the frame.
    """
    for window, measured in MEASURED_PANES.items():
        modelled = needs_you_pane(window)
        assert modelled[0] == pytest.approx(measured[0], abs=1.0), (window, modelled)
        assert modelled[1] == pytest.approx(measured[1], abs=1.0), (window, modelled)


def test_the_art_fits_the_needs_you_pane_at_every_measured_window() -> None:
    """
    What the floor is for. The pane draws only when ``required_extent`` at the fitted
    size fits, so a floor too high for the small end is not a small splash -- it is a
    blank panel, which is the failure a test that only checked large panes cannot see.
    The smallest supported window fits at size 8, one step above the floor; a floor of 10
    would need 611px of a pane that has 510.
    """
    for avail_w, avail_h in MEASURED_PANES.values():
        size = splash.fit_size(avail_w, avail_h, ROWS, COLS)
        need_w, need_h = splash.required_extent(size, ROWS, COLS)
        assert need_w <= avail_w and need_h <= avail_h, (avail_w, avail_h, size)
    assert splash.fit_size(*MEASURED_PANES[SMALLEST_WINDOW], ROWS, COLS) == 8.0


def test_the_crossover_between_the_two_axes_is_the_arts_own_aspect_ratio() -> None:
    """
    Which axis binds is a property of the art, not of the panel it happens to live in.

    Pinned because the comment saying which one it is has already been wrong once: the
    splash moved from DETAIL, a tall narrow pane, to NEEDS YOU, a wide one, and inverted
    it. A test on the crossover survives the next move -- it fails only if the *art*
    changes shape.
    """
    aspect = (
        COLS * splash.GLYPH_ADVANCE_EM / ((ROWS - 1) * splash.LINE_PITCH_EM + splash.INK_HEIGHT_EM)
    )
    tall = splash.fit_size(1000.0 * aspect * 0.5, 1000.0, ROWS, COLS)
    wide = splash.fit_size(1000.0 * aspect * 2.0, 1000.0, ROWS, COLS)
    # Narrower than the crossover: width binds, so extra height buys nothing.
    assert tall == splash.fit_size(1000.0 * aspect * 0.5, 4000.0, ROWS, COLS)
    # Wider than it: height binds, so extra width buys nothing.
    assert wide == splash.fit_size(4000.0 * aspect * 2.0, 1000.0, ROWS, COLS)


def binding_axis(avail_w: float, avail_h: float) -> str:
    """Which of fit_size's two candidate sizes is the smaller, and so decides."""
    by_width = avail_w / (COLS * splash.GLYPH_ADVANCE_EM)
    by_height = avail_h / ((ROWS - 1) * splash.LINE_PITCH_EM + splash.INK_HEIGHT_EM)
    return "height" if by_height <= by_width else "width"


def test_the_needs_you_pane_is_height_bound_at_every_landscape_window() -> None:
    """
    The instance of the crossover that holds today, and the shape that breaks it.

    "Height binds now" is only true of the windows the app is actually used at.
    MainDockSpace is 0.5372 of the width and the whole height, so width binds again once
    the window is narrower than 0.590 / 0.5372 = 1.10 of its height -- which a portrait
    or square window reaches, and 1024x1200 below is one. Both regimes are asserted
    because a comment that named only the first would be half a rule, and the half it
    omits is the one that produced this correction.
    """
    for pane in MEASURED_PANES.values():
        assert binding_axis(*pane) == "height", pane
    for window in ((1280.0, 800.0), (2560.0, 1440.0), (3840.0, 2160.0)):
        assert binding_axis(*needs_you_pane(window)) == "height", window

    for window in ((1024.0, 1200.0), (1000.0, 1400.0)):
        assert binding_axis(*needs_you_pane(window)) == "width", window


def test_every_size_fit_size_can_return_needs_a_whole_number_of_pixels() -> None:
    """
    No fractional column, at any size this can return.

    An arithmetic property of the even grid, not a claim about the raster: a half-em
    advance times an even size is a whole number, so ``required_extent`` never asks for a
    fraction of a pixel that a caller has to decide how to round. What the raster does is
    scripts/verify_splash.py's question, and its answer is narrower than this model above
    size 32 -- the safe direction, and the reason this test says "whole" and not "exact".
    Holds for any column count, so an odd one is checked too.
    """
    for size in range(int(splash.MIN_FONT_SIZE), int(splash.MAX_FONT_SIZE) + 1, 2):
        for cols in (1, COLS, 7):
            width = splash.required_extent(float(size), 1, cols)[0]
            assert width == float(int(width)), (size, cols, width)


def test_an_odd_bound_from_a_caller_cannot_produce_an_odd_size() -> None:
    """
    The even-size guarantee is a property of the function, not of its default arguments.

    ``min_size`` and ``max_size`` are keywords, and the floor is returned unfitted
    whenever the region is too small -- so an odd floor would leave by that path
    carrying the one property the quantisation exists to provide, and the advance
    overrun would be back for panes small enough to reach it. Both bounds move inward
    onto the grid, which is also what the caller asked for: at least 7 means 8, at most
    49 means 48.
    """
    for w in (10.0, 300.0, 900.0, 4000.0):
        for h in (10.0, 300.0, 900.0, 4000.0):
            for lo, hi in ((7.0, 49.0), (9.0, 9.0), (11.0, 13.0), (5.0, 47.0)):
                size = splash.fit_size(w, h, ROWS, COLS, min_size=lo, max_size=hi)
                assert int(size) % 2 == 0 and size == int(size), (w, h, lo, hi, size)
                assert size >= lo, (w, h, lo, hi, size)
    assert splash.fit_size(1e6, 1e6, ROWS, COLS, max_size=49.0) == 48.0
    assert splash.fit_size(1e6, 1e6, ROWS, COLS, min_size=7.0, max_size=7.0) == 8.0
