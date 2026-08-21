"""
The splash asset and its brightness table, pinned against drift.

Two things can rot here independently, and neither one announces itself. The art
is a data file that has to arrive byte-for-byte or the picture is wrong in ways
no exception reports; the rank table is a generated literal that stops describing
the font the moment either side is edited by hand. So the file is pinned by
digest, and the buckets are recomputed from Inconsolata-Medium and compared.

The load-bearing asymmetry is that ``RANK_OF`` is *derived* from ``RANKS``. That
makes "the keys are the union of the buckets" true by construction, and a test
asserting it can only fail if someone later writes the mapping out by hand. What
derivation does **not** protect is a character appearing in two buckets: the dict
comprehension takes the last one and reports nothing. That case gets its own test.
"""

from __future__ import annotations

import hashlib
import importlib.util
from collections import Counter
from functools import cache
from pathlib import Path
from types import ModuleType

import pytest

from pptmstr.ui.splash_art import ART, RANK_OF, RANKS

ROOT = Path(__file__).resolve().parent.parent
ASSET = ROOT / "pptmstr" / "ui" / "splash_art.txt"
SPLASH_ART_PY = ROOT / "pptmstr" / "ui" / "splash_art.py"

# The art as authored. A change to any of these is a change to the picture, and it
# invalidates the rank table with it -- the inventory is what the buckets cover.
ART_SHA256 = "1854122c578b13cb53dc47c590475b5efe7e1a0abb0778a4fa5dd135db811d1f"
ART_ROWS = 61
ART_WIDTH = 72
ART_DISTINCT = 166  # 165 glyphs plus U+0020


def test_the_asset_is_the_art_that_was_authored() -> None:
    """
    Byte-for-byte, not "close enough".

    A digest rather than a shape check because the failure being guarded against
    is silent: an editor that strips trailing whitespace, normalises the
    CP1252-range punctuation, or rewrites U+00AD leaves a file of the same row
    count and the same width that draws a different picture.
    """
    assert hashlib.sha256(ASSET.read_bytes()).hexdigest() == ART_SHA256


def test_the_rows_load_without_being_reflowed() -> None:
    assert len(ART) == ART_ROWS
    assert max(len(row) for row in ART) == ART_WIDTH
    # Ragged on purpose: no row was padded out to the width, and none carries
    # trailing space that a renderer would have to strip.
    assert any(len(row) < ART_WIDTH for row in ART)
    assert all(row == row.rstrip() for row in ART)
    assert not any("\n" in row or "\r" in row for row in ART)
    assert len(set("".join(ART))) == ART_DISTINCT


def test_every_glyph_in_the_art_has_somewhere_to_go() -> None:
    """
    A cell whose character is not in the table cannot animate, and nothing at
    runtime would say so -- it would just sit still while its neighbours moved.
    """
    inventory = set("".join(ART)) - {" "}
    assert inventory <= set(RANK_OF), sorted(inventory - set(RANK_OF))


def test_the_table_invents_no_glyph_the_art_does_not_use() -> None:
    """
    The one thing keeping the substitution pool inside the font-validated set.

    ``splash.py`` flattens ``RANKS`` into a pool and draws from the whole of it, so
    every key of ``RANK_OF`` is a glyph that can land in any animating cell. That
    makes this equality, not the inclusion its sibling above checks, the boundary:
    a glyph added to the table that is not in the art has never been through
    ``rank_glyphs.ink_areas`` refusing an uncmapped codepoint or through
    ``scripts/verify_splash.py`` checking the advance, and it would substitute
    itself into cells as a missing-glyph box or a sheared column.
    """
    inventory = set("".join(ART)) - {" "}
    assert set(RANK_OF) == inventory, sorted(set(RANK_OF) - inventory)


def test_the_background_is_never_substituted() -> None:
    """
    U+0020 in a bucket would let a swap put ink into the negative space, and the
    silhouette is made *of* the negative space.
    """
    assert " " not in RANK_OF
    assert all(" " not in bucket for bucket in RANKS)
    assert "\n" not in RANK_OF


def test_no_glyph_is_in_two_buckets() -> None:
    """
    The case ``RANK_OF``'s derivation hides: a duplicate makes the mapping depend
    on bucket order, so one of the two buckets can offer a glyph that reports a
    rank belonging to the other.
    """
    counts = Counter(char for bucket in RANKS for char in bucket)
    assert [char for char, n in counts.items() if n > 1] == []


def test_no_bucket_is_empty() -> None:
    """An empty bucket is an index a renderer can land on with nothing to draw."""
    assert all(len(bucket) > 0 for bucket in RANKS)


def test_no_bucket_falls_below_the_generators_member_floor() -> None:
    """
    A pin on ``rank_glyphs.DEFAULT_MIN_SIZE = 3``, stated where a reader lowering that
    floor will look for it.

    Substitution draws from all fourteen buckets at once, so a bucket of one freezes
    nothing and this is not a claim about what a cell can become. What the floor decides
    is how far the *subdivision* stage can cut: at a floor of 2 the same twelve ink bands
    emit 16 buckets and at 1 they emit 25. The ratio is unaffected -- 1.1724 at all three
    -- so neither of the two bounds below catches it.

    Redundant today and kept deliberately. ``test_the_substitution_pool_flattens_in_the
    _order_that_ships`` also fires at both lowered floors, because 16 and 25 buckets each
    flatten to a different pool than the checked-in 14 do. This states the floor directly
    rather than as a digest mismatch, so it says *which* parameter moved; the digest can
    only say that something did.
    """
    assert min(len(bucket) for bucket in RANKS) >= 3


def test_rank_of_agrees_with_ranks() -> None:
    """
    True by construction today. It is here for the day someone writes the mapping
    out to save an import -- that is when the two can disagree.
    """
    assert set(RANK_OF) == {char for bucket in RANKS for char in bucket}
    for index, bucket in enumerate(RANKS):
        for char in bucket:
            assert RANK_OF[char] == index


def test_every_bucket_entry_is_a_single_character() -> None:
    """
    ``tuple("ab")`` and ``("ab",)`` are both tuples of str and mypy cannot tell
    them apart, so a bucket written with a comma by mistake typechecks and then
    substitutes two cells' worth of text into one cell.
    """
    assert all(len(char) == 1 for bucket in RANKS for char in bucket)


# The pool `splash.art_frame` indexes into: every bucket flattened, in order. It is the
# whole of what the partition still contributes to the picture, so it is pinned by digest
# on its own rather than left to follow from the table's shape.
POOL_SHA256 = "0db05cf62232fa8d797b7c507f7f18c5585adc9df1ab5c4de748c2f01365a268"
POOL_SIZE = 165


def test_the_substitution_pool_flattens_in_the_order_that_ships() -> None:
    """
    The only pin on the sequence the animation is actually made of.

    Since a substitution draws from the union of the buckets, the partition is inert at
    render time *except* for the order it flattens to -- and ``splash.art_frame`` indexes
    that flattening directly, so it fixes which glyph every cell shows on every step.
    ``test_the_checked_in_table_is_what_the_script_generates`` cannot hold it: that one
    regenerates from whatever the generator's defaults currently say, so changing a
    default and pasting the new table keeps it green by construction. This reads the
    checked-in literal against a constant, which is what closes that loop.

    Measured by regenerating at each setting and rendering a full 81-step cycle against
    the real art. ``DEFAULT_BUCKETS = 10`` still emits fourteen buckets -- so a bucket
    count assertion passes it -- and changes 75 of the 81 frames. Of the 40 parameter
    settings that emit exactly fourteen buckets, 31 reorder the pool. Those are what this
    catches and nothing else here does.

    Deliberately blind to a re-bucketing that preserves the order, which is a property
    and not a gap. ``DEFAULT_BUCKETS = 13`` emits fifteen buckets at the same member
    floor and flattens to this identical string; rendering both tables over a full cycle
    gives 0 differing frames of 81. An ``assert len(RANKS) == 14`` would fire there, and
    it would be firing on a change no viewer can see. The bucket count reaches the
    renderer only as the bounds check on ``rank_of``'s value, which a self-consistent
    table passes at any count, so the count is not pinned.

    Opens no font, so unlike the pins below this cannot skip. Where the dev extra is
    absent it is the only thing holding the table to anything at all.
    """
    pool = "".join(char for bucket in RANKS for char in bucket)
    # Checked before the digest so a table that changed size reports its size rather than
    # two hex strings that differ from the first byte.
    assert len(pool) == POOL_SIZE, len(pool)
    assert hashlib.sha256(pool.encode("utf-8")).hexdigest() == POOL_SHA256


# -- the pin against the font ------------------------------------------------------
#
# Recomputes the table from Inconsolata-Medium and checks the checked-in literal
# still describes it. These are the only tests that can catch the generated table
# drifting from the generator, which is the hazard a hand-editable literal carries.
# fontTools is in the `dev` extra rather than a runtime dependency, so they skip
# where it is absent instead of failing.

# Both measured, both quoted with headroom so a font revision that nudges a glyph
# does not fail them while a re-bucketing that gave up the property does. The
# measurements are `worst within-bucket ink ratio` (1.1724) and `worst within-bucket
# ink-height spread` (0.4700 em) in the report `scripts/rank_glyphs.py` prints.
MAX_INK_RATIO = 1.18
MAX_INK_HEIGHT_SPREAD = 0.48


@cache
def _ranker() -> ModuleType:
    """
    ``scripts/rank_glyphs.py`` as a module, loaded by path.

    By path rather than by putting ``scripts/`` on ``sys.path``: the directory
    holds a dozen flat module names (``probe``, ``screenshot``) that would then
    shadow anything similarly named, and a test suite that changes import
    resolution for every later test is a hard failure to trace back.
    """
    pytest.importorskip("fontTools", reason="the dev extra is not installed")
    source = ROOT / "scripts" / "rank_glyphs.py"
    # Checked rather than left to the loader: the skip above is the one thing between
    # these tests and never running, so the next reason they could stop running has to
    # say so out loud rather than arriving as a bare FileNotFoundError.
    assert source.is_file(), f"the generator these tests pin against is missing: {source}"
    spec = importlib.util.spec_from_file_location("pptmstr_test_rank_glyphs", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_bucket_is_much_brighter_than_a_later_one() -> None:
    """
    What stands in for a total dim-to-bright ordering, which two buckets do not have.

    A bucket subdivided by ink height shares its ink band with its neighbour, so the
    two interleave: buckets 2 and 3 do, and buckets 6 and 7, and ``max(dim) <
    min(bright)`` is false of a *correct* table. So the claim is stated to pass
    overlap inside one band and fail overlap across two, which is what a table
    emitted in band order looks like and what a shuffled one does not.

    The order is what is being pinned, not a rendering guarantee. Substitution reads
    the buckets as one flat pool, so nothing offers a cell glyphs near its own ink
    any more; what the emission order still decides is the pool's order, and through
    it every frame.

    A tie at a cut point passes here too. ``grave`` and ``acute`` have identical
    area, so a strict comparison would fail a table that split them correctly.
    """
    rank_glyphs = _ranker()

    areas = rank_glyphs.ink_areas(sorted(set(RANK_OF)))
    for index, dim in enumerate(RANKS):
        brightest = max(areas[c] for c in dim)
        for offset, bright in enumerate(RANKS[index + 1 :], start=index + 1):
            dimmest = min(areas[c] for c in bright)
            assert brightest <= MAX_INK_RATIO * dimmest, (index, offset)
            # Buckets that genuinely interleave are the halves of one ink band, and
            # those are emitted consecutively. Any other pair is a shuffled table.
            if brightest > dimmest:
                assert offset == index + 1, (index, offset)


def test_no_ink_band_is_coarser_than_the_generator_settled_on() -> None:
    """
    A drift detector on ``rank_glyphs.DEFAULT_BUCKETS``, from the quality side.

    It no longer describes a swap: substitution draws from all fourteen buckets, so a
    cell can go from the dimmest glyph in the art to the brightest. What it still does is
    fail when the band count *drops*, and it is worth having because
    ``test_the_checked_in_table_is_what_the_script_generates`` regenerates the table from
    whatever the defaults say and stays green through exactly that change.

    What it protects is the table's description of the inventory, not the picture. The
    render-time consequence of a re-banding is held next door by
    ``test_the_substitution_pool_flattens_in_the_order_that_ships``, and the two are
    genuinely different questions: 13 bands reads 1.1655 here and passes, and is also
    invisible to the digest because it flattens to the same pool -- measured at 0 of 81
    frames differing. A band count that passes both has changed neither how evenly the
    bands describe the ink nor anything drawn.

    One-sided, and the gap is on the record rather than papered over. Measured against
    the checked-in art: 11 bands gives ratio 1.2265 and spread 0.7260, so both bounds
    fire; 14 gives 1.1655 and 0.7260, so the sibling below fires; 13 gives 1.1655 and
    0.4700 with no bucket under three members, so nothing in this file fails it.

    1.18 rather than the measured 1.1724 so a font revision moving a glyph slightly
    does not fail it. That is under a percent of headroom, which is deliberate -- a
    bound that only fires on a large change is not a detector -- and is why a real font
    bump is expected to arrive here first.
    """
    rank_glyphs = _ranker()

    areas = rank_glyphs.ink_areas(sorted(set(RANK_OF)))
    for bucket in RANKS:
        span = max(areas[c] for c in bucket) / min(areas[c] for c in bucket)
        assert span <= MAX_INK_RATIO, (span, "".join(bucket))


def test_no_bucket_spreads_its_ink_across_half_the_em() -> None:
    """
    The drift detector on ``rank_glyphs.DEFAULT_MAX_SPREAD``, and on the band count
    from the side its sibling above misses.

    Like that one it has stopped describing a swap -- the flat pool will move a cell's
    ink anywhere in the em -- and like it, what it still catches is a regenerated table
    that ``test_the_checked_in_table_is_what_the_script_generates`` would wave through.
    It is the only test here that fires on 14 bands (spread 0.7260), and the only one
    that fires when ``DEFAULT_MAX_SPREAD`` is raised far enough to switch the
    subdivision stage off -- at 0.80 the table drops to 12 buckets and spreads 0.7260.

    The measurement behind the number: a cedilla and a tilde carry the same ink and sit
    0.726 em apart, so a band holding both is uniform in weight while spanning most of
    the cell in position.

    The bound is deliberately loose. Spread of this kind is partly wanted -- the panel
    reads as glitchy on purpose -- so this fails a table that reintroduces a band as
    wide as the cedilla's, not one that leaves a visible spread. Three buckets stay
    above the 0.30 em the generator subdivides at, and the floor of three members is
    why, in two different ways: one band holds five glyphs and so cannot be cut in
    two at all, and the two bands that can be cut each keep a low-sitting mark --
    U+00B8 with U+201A in one, U+201E in the other -- that has no height-alike
    partner to sit with. They measure 0.4160, 0.4315 and 0.4700 em, which
    ``scripts/rank_glyphs.py`` prints per bucket as ``dy=`` and the worst of as
    "worst within-bucket ink-height spread".
    """
    rank_glyphs = _ranker()

    centres = rank_glyphs.ink_centres(sorted(set(RANK_OF)))
    for bucket in RANKS:
        heights = [centres[c] for c in bucket]
        spread = max(heights) - min(heights)
        assert spread <= MAX_INK_HEIGHT_SPREAD, (spread, "".join(bucket))


def test_the_checked_in_table_is_what_the_script_generates() -> None:
    """
    The pin proper: same font, same art, same parameters, same partition. Without
    this the literal is a duplicated constant with nothing holding it to its
    source (STYLE.md §3).
    """
    rank_glyphs = _ranker()

    # `rank()` and not the stages behind it: reassembling the pipeline here would
    # pin the literal to a *copy* of the generator, free to stop matching the one
    # the script runs.
    buckets, _, _ = rank_glyphs.rank()
    regenerated = tuple(tuple(char for char, _ in bucket) for bucket in buckets)
    assert regenerated == RANKS


def _bucket_block(lines: list[str]) -> list[str]:
    """
    The ``_BUCKETS`` assignment, from its opening line to its closing paren.

    One function used on both sides of the comparison below, so neither text gets
    sliced by a rule the other does not follow. It starts at the assignment rather
    than above it because the module carries hand-written comment lines there that
    the generator does not emit, and it ends at a *bare* ``)``: the one wrapped
    bucket closes with an indented ``),``, so the first unindented paren is the
    end of the tuple.
    """
    start = next(i for i, line in enumerate(lines) if line.startswith("_BUCKETS"))
    end = next(i for i, line in enumerate(lines[start:], start) if line == ")")
    return lines[start : end + 1]


def test_the_generator_prints_the_table_that_is_checked_in(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Against ``main()``, not against ``rank()``, because stdout is what gets pasted.

    The pin above compares ``rank()``'s return value to ``RANKS``, which leaves the
    entry point itself unpinned: a ``main()`` that assembled the table some other
    way, or an escaping or wrapping change in ``_literal``, would keep every test
    passing while the documented "run the script, paste the block" workflow emitted
    something that is not what is in the file. That is the defect this table already
    shipped once, so the check is on the bytes a maintainer actually copies.
    """
    rank_glyphs = _ranker()

    assert rank_glyphs.main([]) == 0
    printed = _bucket_block(capsys.readouterr().out.split("\n"))
    source = _bucket_block(SPLASH_ART_PY.read_text(encoding="utf-8").split("\n"))

    assert printed == source
