# The wake picks from the whole inventory

**Dated:** 2026-08-20 · **Status:** decided and built in one session; recorded while the
work was still in the tree, so no commit is quoted here ·
**Supersedes:** the in-bucket substitution invariant in
[`2026-08-15-the-splash-cycles-behind-a-raster-line.md`](2026-08-15-the-splash-cycles-behind-a-raster-line.md),
and the safety argument that rested on it

Symbol names rather than line numbers, per the 08-14 convention.

This record is in `planning/archive/` rather than `planning/` because it decides
something rather than scoping work to do, which is the split `cd1f863` established.
It scopes nothing; the code it describes already runs.

---

## What changed

`splash.art_frame` picks a substitute glyph from the union of every bucket in `RANKS`
— one flat pool — instead of from the bucket the cell's own glyph belongs to.

The pool is **165 glyphs**, verified by flattening `RANKS` in the tree rather than by
reading a comment: `len({c for b in RANKS for c in b}) == 165`, and U+0020 is not among
them. The art has 166 distinct characters because the space is one of them, which is why
both numbers are correct in the tree and neither substitutes for the other. The fourteen
buckets hold `3, 3, 3, 3, 7, 5, 3, 4, 14, 14, 27, 45, 28, 6`.

`RANKS` and `RANK_OF` now gate *eligibility* and nothing else. A cell substitutes only if
its glyph is ranked, that rank indexes a real non-empty bucket, and the cell is in the
cycling set. Which bucket never reaches the output. The partition is render-inert except
for the order it is read in: any partition of the same glyphs yields the same pool, and
only the flattening sequence — which fixes which glyph a given `(row, col, step)` shows —
depends on the bucket widths.

What "curated" is still doing, since the pool is no longer curated for *brightness*:
`scripts/rank_glyphs.py` refuses any glyph the face cannot draw, and
`scripts/verify_splash.py` checks that every codepoint in the art shares one advance
width at every size `fit_size` can return. A substitute is therefore always a glyph in
the baked atlas and never one that shifts a column. A pool of "any printable character"
would give up both, which is why the inventory stayed curated even though the ink
ranking stopped being consulted.

## Why

The operator's call, on aesthetic grounds, stated as it was made.

The ink and value matching worked too well. The raster line and the wake read correctly,
but the glitch effect was subdued: matching a cell's replacement to its own measured ink
holds the picture's brightness steady underneath the animation, and holding it steady is
what made the wake read as texture rather than as breakage. Near-random glyphs in the
wake are the effect wanted. They are the point rather than a side effect.

Asked what this does to the photosensitivity argument, the operator downgraded
photosensitivity as a priority for this panel and said the result will be judged on the
glitch aesthetic. That is a deliberate trade, made with the cost in front of the person
making it. It is recorded here so the next reader does not have to reconstruct it.

## What the trade gives up

The closing note of `2026-08-15-the-splash-cycles-behind-a-raster-line.md` records the
in-bucket invariant as the only thing holding the per-cell substitution rate under
WCAG's general flash threshold. Its argument, in its own terms: the sweep's own rate is
0.198 Hz and safe, and the two lit rows of 61 are an area argument that carries on its
own, but a cell being overtaken by the raster line can substitute on consecutive steps at
`RASTER_ROWS_PER_SECOND = 16`, inside the 15–20 Hz region where photosensitive response
peaks, and nothing about the sweep protects it. What protected it was that a swap never
left the cell's measured ink bucket, so it moved about 0.05 of relative luminance against
a 0.10 threshold — half of it.

**Those figures are computed, not measured.** That document says so itself: none of it
was checked against a photometer or a live frame, every figure is derived from the
palettes, and the viewing geometry it used is one the document supplies because the repo
records none. They are quoted here at the same weight they were written at.

A flat pool over 165 glyphs removes that bound. The per-cell luminance step is now
whatever the full inventory's span permits, and **nobody has computed or measured what
that is.** The review this session ran to establish the figure did not produce one. So the
bound is gone, and the size of what replaced it is unknown rather than known to be
acceptable — which is a different statement, and the one worth having written down.

The old bound also survives in the tree as a number with a changed meaning.
`tests/test_splash_art.py` still holds the within-bucket ink ratio at
`MAX_INK_RATIO = 1.18`. It measures the generator's output, and it is a drift detector on
`scripts/rank_glyphs.py`'s parameters. It is no longer a bound on anything drawn, because
nothing drawn consults a bucket's contents. Widening the ink bands is no longer a
photosensitivity change, because there is no longer a photosensitivity property for the
bands to carry.

`scripts/rank_glyphs.py`'s three-member floor is the same shape: `min_size = 3` was
forced by the animation, since a bucket of fewer than three left a cell with nothing to
cycle through, and it is the floor that seats a lone low-sitting mark with height-unalike
company and produces the residual 0.470 em spread. Every eligible cell now has 164
alternatives whatever its bucket holds, so the animation no longer requires the floor.
The floor is unchanged and the partition it produces is unchanged; only its reason is
gone.

## What it cost the tree

Two shapes of debt, both from the same cause: a set of comments and tests asserted an
invariant that had stopped being true.

**Five tests, all pinned to in-bucket substitution or to a mechanism it implied.** The
lead ran the suite on the working tree and got 1109 passed, 5 failed:

- `test_a_substitute_comes_only_from_the_cells_own_bucket`
- `test_which_cells_cycle_is_fixed_for_the_life_of_the_process`
- `test_the_draw_rate_falls_off_linearly_with_distance_behind_the_line`
- `test_every_cycling_cell_on_the_raster_row_is_redrawn`
- `test_no_substitution_leaves_its_ink_bucket`

in `tests/test_splash.py` and `tests/test_splash_junction.py`. Only the first and last
assert the dead invariant directly. The middle three encode properties that still matter
— membership does not vary with the step, intensity is monotonic in distance behind the
line, the raster row redraws in full — stated in terms of a bucket's size, which is the
thing that stopped mattering. They were re-pinned rather than deleted, following the
precedent the raster-line record set: tell a genuinely obsolete test from a
still-live property one at a time, and decide the obsolete one explicitly rather than
dropping it quietly.

**Comments across `pptmstr/ui/splash.py`, `pptmstr/ui/splash_art.py`,
`tests/test_splash_art.py`, `tests/test_splash.py`, `tests/test_splash_junction.py` and
`scripts/rank_glyphs.py`** described equal-ink substitution as a live constraint. Some of
it was load-bearing prose — `splash.py`'s "what protects the panel" block, which existed
so that a reader changing the constants had to meet the argument — and it is gone from
the code rather than edited, because the argument it made is not the argument that now
applies. The prior text is at `git show HEAD:pptmstr/ui/splash.py`, and the reasoning it
carried is in the raster-line record's closing note. CLAUDE.md: comments describe the
code as it is, not as it was.

The count sitting inside that debt is worth naming because it was wrong in more than one
file: several comments said 166 where the pool is 165. The art's 166 is the art's, and
`splash.py` uses 166 correctly about the codepoints sharing a single advance, where the
space is included.

## What is not established

- **The per-cell luminance span the flat pool permits.** Not computed. The old bound was
  one ink band, ratio 1.18; the full-inventory span is unknown. `scripts/rank_glyphs.py`
  does the measuring and `tests/test_splash_art.py` has the ink-area machinery, so the
  number is cheap to get. Nobody got it.
- **Whether the area argument carries on its own.** The deleted block argued that
  area-averaged luminance is essentially static because cells draw independently, so
  simultaneous swap signs cancel, and WCAG measures the area-average over a 10-degree
  field. That argument does not depend on the in-bucket invariant, and it has not been
  re-examined since the invariant went.
- **Anything about a live frame.** `scripts/verify_splash.py` opens one and takes
  `--capture`. The glitch aesthetic this change was made for has not been looked at
  through it, and neither has anything else here.
