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

import math
import subprocess
import sys
from pathlib import Path

import pytest
from imgui_bundle import imgui

from pptmstr import theme
from pptmstr.ui import splash

# Three buckets of decreasing size. The one-glyph bucket exists so the difference
# between "this cell is in the cycling set" and "this cell visibly changes" is
# representable: a cell can be a member and still never move.
RANKS: tuple[tuple[str, ...], ...] = (("a", "b", "c"), ("X", "Y"), ("#",))
RANK_OF: dict[str, int] = {c: i for i, bucket in enumerate(RANKS) for c in bucket}

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
STEP = 1.0 / splash.STEPS_PER_SECOND


def frames_over_steps(n: int, art: tuple[str, ...] = ART) -> list[tuple[str, ...]]:
    """One frame per substitution step, ``n`` steps starting at t=0."""
    return [splash.art_frame(art, RANK_OF_BROKEN, RANKS, i * STEP + STEP / 2) for i in range(n)]


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
    guard on only the first direction leaves the second, which is the visible half --
    under this fixture "a" is in a four-glyph bucket, so an unguarded cell holding one
    would blank out for a quarter of its cycle.
    """
    ranks_with_space = (("a", "b", "c", " "),) + RANKS[1:]
    rank_of_with_space = {c: i for i, bucket in enumerate(ranks_with_space) for c in bucket}
    # Long enough that every cycling cell has advanced through its whole bucket twice:
    # the slowest cell moves once every max(_CELL_PERIODS) steps.
    steps = 2 * max(len(b) for b in ranks_with_space) * max(splash._CELL_PERIODS)
    for i in range(steps):
        frame = splash.art_frame(ART, rank_of_with_space, ranks_with_space, i * STEP)
        for r, line in enumerate(frame):
            for c, ch in enumerate(line):
                if ART[r][c] == " ":
                    assert ch == " ", f"space at ({r},{c}) became {ch!r} at step {i}"
                else:
                    assert ch != " ", f"ink at ({r},{c}) became a space at step {i}"


def test_an_unranked_glyph_is_left_alone_rather_than_raising() -> None:
    frames = frames_over_steps(40)
    for frame in frames:
        for r, line in enumerate(frame):
            for c, ch in enumerate(line):
                if ART[r][c] == UNRANKED:
                    assert ch == UNRANKED


def test_a_rank_pointing_past_the_table_is_a_no_op_rather_than_an_index_error() -> None:
    frames = frames_over_steps(40)
    for frame in frames:
        for r, line in enumerate(frame):
            for c, ch in enumerate(line):
                if ART[r][c] == OUT_OF_RANGE:
                    assert ch == OUT_OF_RANGE


def test_a_substitute_comes_only_from_the_cells_own_bucket() -> None:
    """Brightness is preserved: nothing ever leaves the bucket it started in."""
    for frame in frames_over_steps(60):
        for r, line in enumerate(frame):
            for c, ch in enumerate(line):
                original = ART[r][c]
                bucket_index = RANK_OF.get(original)
                if bucket_index is None:
                    continue
                assert ch in RANKS[bucket_index], f"({r},{c}) {original!r} -> {ch!r}"


def test_which_cells_cycle_is_fixed_for_the_life_of_the_process() -> None:
    """
    A cell moves over time exactly when ``is_cycling`` says it does and its bucket has
    somewhere to go. Membership that varied with the step would eventually pull in
    every cell, which reads as uniform noise rather than as texture.
    """
    seen = chars_seen(frames_over_steps(120))
    for (r, c), chars in seen.items():
        original = ART[r][c]
        bucket_index = RANK_OF.get(original)
        movable = (
            bucket_index is not None and len(RANKS[bucket_index]) > 1 and splash.is_cycling(r, c)
        )
        assert (len(chars) > 1) is movable, f"({r},{c}) saw {sorted(chars)}, movable={movable}"


def test_roughly_the_configured_fraction_of_ranked_cells_cycles() -> None:
    ranked = [(r, c) for r, line in enumerate(ART) for c, ch in enumerate(line) if ch in RANK_OF]
    fraction = sum(splash.is_cycling(r, c) for r, c in ranked) / len(ranked)
    assert abs(fraction - splash.CYCLING_FRACTION) < 0.05, fraction


def test_one_bucket_shows_several_of_its_glyphs_at_a_single_instant() -> None:
    """
    A spatial claim, and only that one: at one instant the members of a bucket are not
    all displaying the same glyph, so the art reads as texture rather than as a
    repeating character. It says nothing about *when* those cells change --
    ``test_only_a_fraction_of_the_cycling_cells_change_on_any_one_step`` owns that, and
    a renderer that moved every cell on every step would pass this one.
    """
    frame = splash.art_frame(ART, RANK_OF_BROKEN, RANKS, 5 * STEP)
    bucket = RANKS[0]
    shown = {
        frame[r][c]
        for r, line in enumerate(ART)
        for c, ch in enumerate(line)
        if ch in bucket and splash.is_cycling(r, c)
    }
    assert len(shown) > 1, shown


# The cells that can visibly move: in the cycling set, ranked, and in a bucket with
# somewhere to go. The one-glyph bucket has to be excluded or a renderer would be
# penalised for cells that are physically unable to change.
MOVABLE = [
    (r, c)
    for r, line in enumerate(ART)
    for c, ch in enumerate(line)
    if ch in RANK_OF and len(RANKS[RANK_OF[ch]]) > 1 and splash.is_cycling(r, c)
]

# The whole cycling set moving together is 1.0. This is the bound that separates a
# shimmer from a full-field flicker, and it is deliberately far from both ends: the
# implementation sits near 0.3, and neither 0.5 nor 0.05 is a restatement of it.
MAX_SIMULTANEOUS_CHANGE = 0.5
MIN_SIMULTANEOUS_CHANGE = 0.05

# ...and how much that fraction may vary from step to step. What the eye integrates is
# the modulation of the change rate, not its level, so a field that moved a quarter of
# its cells on even ticks and none on odd would beat at half the tick rate while staying
# comfortably under the bound above. Measured spread is 0.05 over 60 steps and 0.07 over
# 240; deriving a cell's offset from its period's own bits takes it to 0.29.
STEADY_RATE_SPREAD = 0.15


def test_only_a_fraction_of_the_cycling_cells_change_on_any_one_step() -> None:
    """
    The temporal claim: substitutions are spread across ticks, not fired on one.

    This is the safety-relevant property of the panel, not a stylistic one. Every
    cycling cell advancing on the same tick is ~4.4k glyphs changing together at
    STEPS_PER_SECOND -- full-field flicker at ~3Hz, inside the band photosensitivity
    guidance treats as a risk, on a panel that is up for as long as the app is empty.
    Spatial variety at one instant cannot see it: with a global step every cell still
    shows a different glyph from its neighbour, and they all change at once anyway.

    Three bounds, because the level alone is not the property. The lower one is here
    because the cheapest way to pass the upper one is to stop animating, and the spread
    is here because a rate that swings between a quarter and nothing is a beat at half
    the tick rate while never exceeding the upper bound at all.
    """
    frames = frames_over_steps(60)
    fractions = [
        sum(a[r][c] != b[r][c] for r, c in MOVABLE) / len(MOVABLE)
        for a, b in zip(frames, frames[1:], strict=False)
    ]
    assert max(fractions) < MAX_SIMULTANEOUS_CHANGE, max(fractions)
    assert min(fractions) > MIN_SIMULTANEOUS_CHANGE, min(fractions)
    assert max(fractions) - min(fractions) < STEADY_RATE_SPREAD, (min(fractions), max(fractions))


def test_the_moving_set_does_not_repeat_within_the_first_sixty_ticks() -> None:
    """
    Pins the choice of pairwise-coprime periods, which is otherwise a comment.

    *Which* cells move on a tick is fixed by their periods and offsets, so a period set
    sharing a factor makes that set recur on a short cycle -- with every period equal it
    is the period itself, three ticks, one second. The change rate stays perfectly steady
    while the same cells take turns in the same order, so neither bound above sees it.
    """
    frames = frames_over_steps(61)
    moving = [
        frozenset((r, c) for r, c in MOVABLE if a[r][c] != b[r][c])
        for a, b in zip(frames, frames[1:], strict=False)
    ]
    assert len(set(moving)) == len(moving)


def test_a_cell_holds_its_glyph_for_several_steps_before_substituting() -> None:
    """
    The per-cell half of the same property, which the aggregate fraction can only imply.

    A field where every cell changed on every third step at random would keep the
    aggregate low and still give each cell a 3Hz-adjacent flicker of its own. What is
    asserted is a floor on the *gap*: no movable cell substitutes on two consecutive
    steps, so the fastest thing on screen changes at half the tick rate or slower.
    """
    frames = frames_over_steps(90)
    for r, c in MOVABLE:
        changed = [i for i in range(1, len(frames)) if frames[i][r][c] != frames[i - 1][r][c]]
        assert changed, f"({r},{c}) is movable but never moved"
        gaps = [b - a for a, b in zip(changed, changed[1:], strict=False)]
        assert all(g >= 2 for g in gaps), f"({r},{c}) substituted on consecutive steps"


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

    def counted(*args: object, **kwargs: object) -> tuple[str, ...]:
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
    assert splash.art_frame(plain, RANK_OF_BROKEN, RANKS, 3.7) == plain


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
