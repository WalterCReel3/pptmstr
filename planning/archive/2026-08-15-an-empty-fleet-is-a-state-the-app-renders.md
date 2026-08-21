# An empty fleet is a state the app renders, and the pane it renders in sets the arithmetic

**Dated:** 2026-08-15 · **Status:** built, green, recorded after the fact; the substitution
mechanism it describes was replaced 2026-08-20 — see the note at the bottom ·
**Related:** [`2026-08-10-layout-proposals.md`](../2026-08-10-layout-proposals.md) for the
TRIAGE splits this depends on;
[`2026-08-15-an-operator-instruction-the-lead-cannot-see.md`](2026-08-15-an-operator-instruction-the-lead-cannot-see.md),
which is what the same session exposed about coordination.

Symbol names rather than line numbers, per the 08-14 convention.

This directory holds scope snapshots for work *not yet started*, and this one is written
against work that is finished and passing. That is worth naming rather than glossing: the
panel was designed four times over in a single session because nothing recorded the
result of the previous three, and two of its constants are currently defended by
arguments about geometry that only parse if you know the panel used to live somewhere
else. A snapshot written late is still the cheaper of the two options left.

## What the panel is

When the application has no sessions at all, the NEEDS YOU pane draws a cold start:
one line saying what pptmstr is, a two-line quote with a highlight sweeping across it,
`Ctrl+N - start a session`, and a 61x72 block of text art whose cells cycle among
glyphs of equal ink so the silhouette holds while the texture moves.

The ordering in `inbox._splash` is the design, not a layout accident. The two lines an
operator has to *act* on are above the art, and the art is drawn last and dropped first,
because it is the only part that can fail to fit. `inbox._art` asks `splash.fit_size`
for a size and then asks `splash.required_extent` whether that size actually fits, and
returns without drawing when it does not. A clipped half-silhouette reads as a rendering
fault; nothing reads as deliberate.

**Two emptinesses live in this pane and they are not the same emptiness.** `_zero_state`
answers "a fleet exists and owes you nothing" — it lists what everyone is doing, which is
the other half of the operator's loop. `_splash` answers "there is no fleet", which has
no everyone to report on and exactly one useful thing to say, which is how to begin.

## Where it lives, and what moved it

**Host: NEEDS YOU, docked in MainDockSpace in the TRIAGE layout.** Central, default,
widest, and — the reason that actually decides it — the pane the operator is already
looking at. The cold start is the one moment the app has nothing to put in the pane whose
whole purpose is to hold the next decision, so the panel occupies the space its own
absence created rather than borrowing a side pane.

**It was in DETAIL first.** DETAIL is docked in ContextSpace, which TRIAGE narrows to
0.32 of what the rail leaves: a tall, narrow pane. NEEDS YOU is a wide one. The move
inverted which axis binds the art, and every number derived from the old pane silently
became wrong while remaining plausible — a stale comment on `MIN_FONT_SIZE`, and a test
constant pinning the fit to the narrow pane.

That history is the reason this section exists. Both of those have since been corrected,
and the correction is worth recording as a *rule* rather than as an event:

> A comment that names the current pane is a fact with an owner nobody assigned. A test
> on the crossover is a fact that fails when it stops being true.

`test_the_crossover_between_the_two_axes_is_the_arts_own_aspect_ratio` and
`test_the_needs_you_pane_is_height_bound_at_every_landscape_window` are what a third move
of this panel would break, and breaking is the wanted outcome. The pane fraction in
`tests/test_splash.py` is written as `(1 - 0.21) * (1 - 0.32)` — the two split ratios from
`app.py:613,616` — rather than as a pixel count, so moving a split shows up as a wrong
number instead of as a constant nobody re-derived.

**Still stale, and not fixed here:** the module docstrings of `pptmstr/ui/splash.py`
("the empty-DETAIL splash") and `pptmstr/ui/splash_art.py` ("The DETAIL pane shows this
when there are no sessions") both still name the old host. Those files belong to another
task; this is the record that they are wrong.

## The trigger, and why it is not the existing empty-queue path

`inbox.draw` keys the splash on `not snap.nodes`, **before** the `not snap.needs_you`
branch, with an early return. Two decisions in that sentence.

**`nodes` rather than `order`.** `store._preorder` re-attaches orphans at the root
specifically so nothing in the node table can be missing from the walk, which makes the
two lengths always equal today. `nodes` is asked anyway because it is the primary fact and
`order` is a projection of it (STYLE.md §1). If that recovery ever regressed, keying on
the projection would put a cold-start splash over a live fleet — silently, and over
exactly the orphaned sub-agent that was carrying a pending approval.

**Ordered first, and early-returning, rather than checked later.** An empty node table
implies an empty `needs_you`, so a check placed after `_zero_state` would be reachable
only by luck of ordering. The early return makes the two states mutually exclusive
structurally rather than by the two conditions happening to disagree.

**"No sessions" and "no live sessions" are different questions, and the splash answers the
first.** `_zero_state` filters to non-terminal, non-`FAILED` roots and will say "nothing is
running either" for a fleet of five crashed sessions. That is correct for `_zero_state` and
would be wrong for the splash: five crashed sessions are not an empty application. They
still need to be seen and dismissed, and a cold-start panel offering `Ctrl+N` over the top
of them would be telling the operator the app is fresh when it is wedged.

## Which axis binds — the rule, not the current answer

Width binds exactly when

```
avail_w / avail_h  <  cols * GLYPH_ADVANCE_EM / ((rows - 1) * LINE_PITCH_EM + INK_HEIGHT_EM)
```

which for the 61x72 art is `36 / 61.049 = 0.590`. **A pane wider than 0.59 of its height
is height-bound; a narrower one is width-bound.** This is a property of the art, not of the
panel's location, and it changes only when the art changes shape.

The instance that holds today, which is the part that goes stale: MainDockSpace is
`0.5372` of window width and the whole height, so substituting it in, width binds when
the *window* is narrower than `0.590 / 0.5372 = 1.10` of its height. Every landscape
window is therefore height-bound, and a portrait or square window (1024x1200, 1000x1000)
flips it back. **Both regimes are reachable**, which is why the rule is recorded as a rule.
A future reader who writes down "height binds" has written half of it, and the omitted
half is the one that produced the last correction.

`MIN_FONT_SIZE = 6.0` is a height floor in practice for that reason, reached at a pane
about 366px tall — a horizontal dock drag away, not a hypothetical. `MAX_FONT_SIZE = 48.0`
is an atlas guard rather than a design limit: ImGui 1.92 bakes a fresh entry per distinct
size, and 61 rows would need a pane over 3000px tall to reach the ceiling.

## The fitting constants, and one claim that outruns the measurement

`GLYPH_ADVANCE_EM = 0.5`, `LINE_PITCH_EM = 1.0`, `INK_HEIGHT_EM = 1.049`. None of these
are obtainable in-process — they describe how ImGui *rasterises* the face, not what the
font's nominal metrics say — so the evidence is `scripts/verify_splash.py`, which opens a
real frame and prints `ImFontBaked.get_char_advance` and `calc_text_size` per size. No test
pins them, deliberately: a test recomputing them from these literals would pin the literals
to themselves.

`fit_size` returns only **even** sizes. Quantising at all is what bounds the atlas — a size
varying continuously with the pane mints a new bake on every frame of a window drag. Even
specifically, because the baked advance is a grid-fitted step function that exceeds half
the size at 7 and 9, where `GLYPH_ADVANCE_EM` stops being an upper bound and the art
overruns its pane by a column.

**Open, and found by running the script rather than by reading it.** `verify_splash.py`'s
live output says:

```
advance: uniform across all 166 codepoints at every even size 6-48, so columns line up
         == 0.5*size exactly at 6-32
         NARROWER than 0.5*size at 34-48 (by 1px/char), so the 'exact pixel count' claim
         holds only below 34
```

`splash.py`'s comment on `GLYPH_ADVANCE_EM` claims that on the even grid
`cols * GLYPH_ADVANCE_EM * size` is "an exact pixel count rather than an estimate", with no
upper qualification. Above 32 it is an over-estimate by one pixel per column — 72px across
this art. Nothing overruns, because the constant stays an upper bound in the direction that
matters; what it costs is that the panel can decline to draw, or centre slightly off, in a
pane that would in fact have held it. Reachable only where `fit_size` returns 34 or more,
which needs about 2076px of pane height. **Small, real, and the comment should carry the
bound rather than the unqualified claim.**

A second, smaller disagreement of the same kind: `splash.py` says 1024x700 gives "roughly
550 by 634, giving size 10", while `verify_splash.py` under its own deliberately generous
chrome allowance reports `pane 550x510 -> fit_size 8`. Neither measures the chrome; they
assume different amounts of it. The pane width agrees exactly, which is the number that was
derived rather than guessed.

## How far the brightness ranking is meant to go

`scripts/rank_glyphs.py` measures outline area per glyph (`AreaPen`, absolute, over the em
square) and the vertical centre of the ink bounding box (`BoundsPen`), then partitions the
165 non-space glyphs in two stages: contiguous *ink bands* minimising the worst
within-band area **ratio** subject to a three-member floor, and then a subdivision by ink
**height** of any band spreading more than 0.30 em. Twelve bands, two of which subdivide,
giving fourteen buckets.

Measured, from a run today:

```
worst within-bucket ink ratio:          1.1724
worst within-bucket ink-height spread:  0.4700 em
smallest bucket:                        3
```

**The design decision that is being recorded, because it is recorded nowhere else:
visible artifacts are part of the aesthetic and the tolerance is loose on purpose.** The
panel is meant to read as glitchy. The residual 0.470 em of travel is a deliberate
stopping point, not an unfinished job, and the test bounding it is loose to match. Without
that written down, the next reader tightens the threshold, discovers it cannot be met, and
spends the session finding out why.

### Why it cannot be tightened, stated precisely

The often-repeated version of this argument is that U+201E (`„`) and U+201C (`“`) — all but
the same shape, `0.5315 em` apart, areas measured at `0.048336` and `0.048379`, agreeing to
within `0.088%` — cannot be separated, and that this is the floor. **The first half is
right and the second half names the wrong mechanism**, and the distinction decides whether
the ranking is reopenable.

No *area*-based band can separate that pair; a 0.088% difference is inside every candidate
ratio. That is exactly why the ink-height second stage exists, and the second stage **does**
separate them — they land in different buckets (`÷³„` and `°²”“`), which are the two height
halves of one ink band.

What is left is a different constraint. `„` sits at centre `-0.0320`; the only glyphs
sharing its ink band sit at `+0.3130` (`÷`) and `+0.4380` (`³`). It has **no height-alike
partner in its own band**, and the three-member floor — itself forced, since a bucket of
fewer than three gives a cell nothing to cycle through — seats it with them anyway. The
0.470 em is that seating, not the pair.

The same shape accounts for the other two: a five-member band (`¬…—^;`, 0.4315 em) cannot
split at all under a floor of three, and one more low-sitting mark (`¸`/`‚`) leaves band 2
at 0.4160 em.

**So the floor is `min_size = 3` meeting a lone low-sitting mark, and it moves only if one
of two things changes:** the art gains glyphs that give those marks height-alike company in
their own ink band, or the animation stops requiring three members per bucket. Neither is
worth doing for a decorative panel, which is the decision. A future attempt that only
lowers `DEFAULT_MAX_SPREAD` will find all three bands unchanged, because the threshold is a
trigger and the floor is the binding constraint.

### The pin, and the hazard it was written against

`RANKS` is checked-in data generated by a script, which is a duplicated constant, so it is
pinned twice, and the second pin is the one worth recording.

`test_the_checked_in_table_is_what_the_script_generates` loads `rank_glyphs.py` by path and
compares `RANKS` against `rank()` — **the whole pipeline in one call, not the stages
reassembled**. A pin that rebuilds the pipeline pins a copy of it, and the copy is free to
drift, which is the same hazard the pin exists to close moved one level up.

That is still not enough, and the reason generalises past this feature. `rank()`'s return
value is not what ships. **What ships is stdout** — the documented workflow is "run the
script, paste the block" — so a `main()` that assembled the table some other way would keep
the return-value pin green while emitting something that is not what is in the file. The
same is true of any change to `_literal`'s escaping or wrapping.
`test_the_generator_prints_the_table_that_is_checked_in` closes that by running `main([])`
under `capsys` and comparing the printed `_BUCKETS` block to the block in
`pptmstr/ui/splash_art.py` byte for byte.

Mutation-tested today, without editing the script, by driving `main()` down a one-stage
pipeline through its own CLI (`--max-spread 999`, which disables the ink-height
subdivision — the precise defect the pin is written against): stock `main([])` reproduces
the source block exactly, and the one-stage run prints a 17-line block against the source's
19 and fails the comparison.

**The general rule: a pin must name the artifact that ships.** Return value and stdout are
two artifacts, and pinning the first can be correct, well-named and mutation-tested while
leaving the second free.

## The space guards are unreachable, on purpose

U+0020 is in neither `RANKS` nor `RANK_OF`, deliberately: space is the background, not the
dimmest glyph. `art_frame` nonetheless guards it **in both directions** — it skips a cell
whose character is a space, and it skips a substitution that would *write* a space.

Against the shipped table neither guard can fire. Verified today: `' ' in RANK_OF` is
false, no bucket contains a space, and the art holds 2368 of them. Deleting either line
changes nothing — with the first gone, `rank_of.get(" ")` returns `None` and the very next
check continues anyway; with the second gone, no bucket can ever produce the value it
tests for. **The only mutation that exercises them is the pair: inject a space into the
table, then remove a guard.**

This is worth writing down because it is the inverse of the failure that dominated this
feature's session. Everywhere else the problem was a claim outrunning what runs. Here the
code is sound and simply unreachable from any state the current data can produce, which
makes it look exactly like dead code to the next reader with a tidy-up in mind.

**It should be kept.** STYLE.md §3 says a comment defending against a hazard usually means
the design is wrong; this is the exception, and the reason it is an exception is that the
hazard is silent and the cost is one comparison per substitution. The art's shape *is* its
whitespace. A table that grew a space entry would dissolve the silhouette from either
side — a space overwritten with ink erodes the negative space, and ink replaced by a space
punches holes in it — and the second direction is the visible half. Neither failure raises,
neither is caught by any shape assertion on the table, and both are one regeneration of
`_BUCKETS` away rather than hypothetical.

`test_a_ranked_space_neither_covers_ink_nor_gets_covered` therefore runs against a
**fixture table with a space injected**, over the real art, for long enough that every
cycling cell traverses its whole bucket twice. That the guard's test does not use the
shipped table is intended, and is the thing to check before concluding the guard is
untested: the contracted input cannot distinguish a renderer that guards from one that
merely never meets the case, so the test supplies the adversarial input instead
(STYLE.md §2, "a test's name is a claim; check the body makes it").

## The animation's two independent hazards

The panel is on screen for as long as the application has no sessions, which is unbounded.
Both of the following are fixed; the second is the one that will be reintroduced.

### 1. The whole field stepping in unison

A single global step advances every cycling cell on the same frame: roughly 4.4k glyphs
changing together at `STEPS_PER_SECOND = 3.0` is full-field flicker at ~3Hz, **inside the
band photosensitivity guidance treats as a risk**. This is a safety property of the panel,
not a stylistic one.

Fixed by giving each cell its own period from `_CELL_PERIODS = (2, 3, 5, 7)`, drawn from the
cell's position hash, so only the cells whose period divides a tick move on it. Pairwise
coprime, so *which* cells move repeats every `lcm = 210` ticks — 70 seconds — rather than
on the short cycle a shared factor would realign on. `_cell_hash` is written out rather
than delegated to `hash()` because CPython's is only stable within a build, and the point
of the value is that a test can pin it.

Spatial variety at one instant cannot detect this hazard: under a global step every cell
still shows a different glyph from its neighbour, and they all change at once anyway.

### 2. A cell's phase offset correlating with its period

This one was found only because the first was being fixed, and it is subtler in a way that
matters: **it satisfies any bound on the per-step change fraction.**

If `offset` is taken from the same hash bits as `period`, every cell of a given period
lands on the same tick of it. The field then changes in a lump about a quarter its size and
goes quiet in between. The *number* of cells moving per step averages out to the same
value; what changes is its modulation, and modulation is what the eye integrates. The
result is a visible beat at half the tick rate while a "no more than half the cycling set
moves on any step" bound passes comfortably.

Fixed by giving the four facts disjoint bit ranges of the 32-bit mix — membership 0-9,
period 10-17, offset 18-23, phase 24-31 — and by returning all four from a single `_rhythm`
call so `is_cycling` and `art_frame` cannot reach different conclusions about one cell.

Pinned by three bounds in `test_only_a_fraction_of_the_cycling_cells_change_on_any_one_step`,
and the third is the one that exists for this hazard:

| bound | value | what it catches |
|---|---|---|
| `MAX_SIMULTANEOUS_CHANGE` | 0.5 | unison stepping |
| `MIN_SIMULTANEOUS_CHANGE` | 0.05 | "passing by not animating" |
| `STEADY_RATE_SPREAD` | 0.15 | the beat |

Measured spread is 0.05 over 60 steps and 0.07 over 240; the correlated variant measures
0.29. `test_the_moving_set_does_not_repeat_within_the_first_sixty_ticks` covers the third
version of the same family — a period set sharing a factor keeps the rate perfectly steady
while the same cells take turns in the same order, which neither level bound nor the spread
bound sees.

**Why a future reader reintroduces it:** the obvious tidy-up is to reuse one hash slice for
both `period` and `offset`, or to derive the stagger from `phase`. `phase` alone picks
*what* is shown and never *when* it changes, so a stagger built out of it leaves the whole
field substituting on the same frame — hazard 1 again, wearing hazard 2's clothes. Every
level-based assertion still passes. The bit-range table in `_rhythm`'s docstring is
load-bearing and should not be described as a tidiness convention.

## Still open

- **`GLYPH_ADVANCE_EM`'s "exact pixel count" claim needs the `< 34` bound**, per the live
  run above. Comment only; the arithmetic is sound and nothing overruns.
- **Two module docstrings still name DETAIL** (`splash.py`, `splash_art.py`).
- **`inbox._ART_FRAMES` is module-level**, which is the one place the feature departs from
  `ArtFrames`' own advice that the pane should own it. The reason is recorded at the
  definition — `inbox.draw` is handed no presentation-state object, and adding a parameter
  means editing `app.py`'s call site — and the deviation is affordable because the key is a
  step index over module constants and colour is applied at draw time, so a theme switch
  cannot make an entry stale. It should be revisited if `inbox.draw` ever grows a state
  object for another reason. It should not be revisited by adding one for this.
- **`make typecheck` is `mypy pptmstr` and never sees `scripts/` or `tests/`.** Not
  specific to this feature, but this feature is why it is visible: `rank_glyphs.py` and
  `verify_splash.py` are the two most arithmetic-heavy files it added and neither is
  covered by the gate.
- **Nothing renders the splash under test with a real window.** `verify_splash.py --capture`
  is the only path that produces an empty-fleet frame at all, because `make shot` and
  `scripts/screenshot.py` both install the `FakeDriver`, whose `run()` seeds a tree.

## Verification boundary

**Executed, today, on this working tree:** `make lint` (ruff + `black --check`, 84 files,
clean), `make typecheck` (mypy, clean — but see above: it covers `pptmstr/` only, 37 source
files, and neither `scripts/` nor `tests/` is in its scope), the full suite
(`878 passed in 9.89s`), `scripts/rank_glyphs.py` (the bucket table above is from that run,
and it reproduces the checked-in `_BUCKETS` literal byte for byte), and
`scripts/verify_splash.py` against a live frame, which is where the 34-48 advance finding
comes from.

**Mutation-tested rather than reasoned about:** the stdout pin, by driving `main()` down a
one-stage pipeline through its own CLI; and the space guards' unreachability, by reading
`RANK_OF` and `RANKS` for a space key directly. The per-glyph areas and centres quoted for
U+201E, U+201C, U+00B8, U+201A, U+00F7 and U+00B3 are from `ink_areas`/`ink_centres` on the
face `theme.py` loads, not from the docstrings that also quote them.

**Read, not run:** every claim about `app.py`'s layout ratios, and the `ContextSpace`
geometry attributed to the panel's former home — DETAIL's dimensions are inferred from the
split ratios rather than measured, and the panel is not there any more to measure.

**Not established:** that the panel reads as intended to a person. Every property above is a
number. The tolerance argument in the ranking section is the operator's aesthetic judgement,
recorded as a decision, and it is not the kind of claim a test can carry.

---

## Note, 2026-08-20: cells no longer cycle among glyphs of equal ink

Added rather than folded into the text above, because the reasoning this record preserves
is worth more intact than corrected in place.

`splash.art_frame` now draws a substitute from the union of all buckets — the whole
165-glyph inventory — rather than from the cell's own measured ink bucket. The full
decision, its reasoning and its cost are in
[`2026-08-20-the-wake-picks-from-the-whole-inventory.md`](2026-08-20-the-wake-picks-from-the-whole-inventory.md).

**"What the panel is" is now false where it says the cells "cycle among glyphs of equal
ink so the silhouette holds while the texture moves."** They cycle among all of them. The
silhouette still holds, but for a different reason: every glyph in the inventory shares
one advance width, so a substitute never shifts a column, and the block stays 61x72
whatever it fills with. What no longer holds is the picture's *brightness* underneath the
animation, which was the point of matching ink and is deliberately given up.

**"How far the brightness ranking is meant to go" is misjustified rather than wrong, and
the distinction matters to anyone deciding whether to reopen it.** Its conclusion —
visible artifacts are part of the aesthetic and the tolerance is loose on purpose — is
now over-satisfied rather than merely satisfied; a flat pool is far past loose. But its
premise is gone. The residual 0.470 em of ink-height travel was the glitch budget when
substitution stayed inside a bucket; it is not the glitch budget now, because the
substitution range is no longer bounded by a bucket at all. The section's numbers are
still an accurate description of the partition `scripts/rank_glyphs.py` produces. They
are no longer a description of what the animation draws.

The floor argument moves the same way. `min_size = 3` was forced because "a bucket of
fewer than three gives a cell nothing to cycle through". Every eligible cell now has 164
alternatives whatever its bucket holds, so the animation no longer forces the floor. The
floor and the partition it produces are unchanged; only the reason is gone. A reader
weighing whether to lower `DEFAULT_MAX_SPREAD` should know that the constraint is now the
generator's own, not the renderer's.

Unaffected: the pane arithmetic, the fit and extent reasoning, the ordering argument in
`inbox._splash`, and the space guard. None of them depend on which bucket a substitute
comes from.

Separately, and not caused by this change: **"The animation's two independent hazards"
describes `_CELL_PERIODS`, which the raster-line rework removed on 2026-08-16.** That
section was already history when this note was written;
[`2026-08-15-the-splash-cycles-behind-a-raster-line.md`](2026-08-15-the-splash-cycles-behind-a-raster-line.md)
is what replaced it.
