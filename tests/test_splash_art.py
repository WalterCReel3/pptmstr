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
    Substitution draws from the art's own alphabet, which is what keeps the
    "every character renders in this face" check a property of one inventory
    rather than two.
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


def test_every_bucket_can_actually_change_a_cell() -> None:
    """
    Non-empty is not enough. A bucket of one holds a cell frozen for the whole
    animation while everything around it moves, which reads as a rendering fault
    rather than a still point.
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
    two interleave: bucket 3's dimmest glyph is dimmer than bucket 2's, and
    ``max(dim) < min(bright)`` is false of a *correct* table. What the ordering was
    ever for survives -- a cell is only ever offered glyphs within one swap's worth
    of its own ink -- and this is that claim stated so overlap inside one band passes
    and overlap across two bands does not.

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


def test_no_swap_changes_a_cell_by_more_than_a_sixth() -> None:
    """
    The claim the bucket count was chosen for. 1.18 rather than the measured
    1.1724 so that a font revision moving a glyph slightly does not fail this,
    while a re-bucketing that gave up the property does.
    """
    rank_glyphs = _ranker()

    areas = rank_glyphs.ink_areas(sorted(set(RANK_OF)))
    for bucket in RANKS:
        span = max(areas[c] for c in bucket) / min(areas[c] for c in bucket)
        assert span <= MAX_INK_RATIO, (span, "".join(bucket))


def test_no_swap_moves_a_cell_s_ink_across_half_the_em() -> None:
    """
    Equal ink area is not equal ink position, and the eye tracks position.

    A cedilla and a tilde carry the same ink and sit 0.726 em apart, so a bucket
    holding both offers a cell a swap that keeps its weight and hops most of the
    cell's height. That is worst in the sparse speckle over the negative space,
    where the moving glyph has empty background to move against.

    The bound is deliberately loose. Travel of this kind is partly wanted -- the
    panel reads as glitchy on purpose -- so this fails a table that reintroduces a
    hop like the cedilla's, not one that leaves a visible one. Three buckets stay
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
