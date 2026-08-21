# The splash cycles behind a raster line

**Dated:** 2026-08-15 · **Status:** built 2026-08-16, `1f1a1fc`; the closing note's
in-bucket substitution argument was superseded 2026-08-20 — see the second note at the
bottom

**Parked mid-flight on 2026-08-15**, three hours in, to fix
`driver.py`'s sub-agent liveness bug first — that one reports live agents as
FAILED, releases their cap slots, and can drop their results, and it surfaced
*because* this work was being run by a team. The findings below are worth more
than the code that was written against them, which is why they are here rather
than only in a stash.

**Landed 2026-08-16**, at the rate this document argued for: 16 rows a second behind
a two-row luminance band. The stash it was parked into carried the older 18 and a
comment asserting an evenness against `fps_idle` that this repo had already measured
to be absent, so it was dropped rather than reapplied — the model below is what
shipped, and no branch holds a competing version of it.

What follows is the design as it was written before the code existed. It is left
in that tense because it is a snapshot; what changed on the way in, and what did
not get answered at all, is in the closing note at the bottom.

---

## What was asked for

A bright raster line that leads the cells cycling, much faster than the current
3 substitutions a second. The cycle percentage is maximum at the raster line and
steps down for every line after it, up to 20 lines. Fewer lines modified per
frame, but a stronger read.

Then a clarification, which turned out to be the load-bearing half: **the 20-row
ramp is the cycling intensity, and the luminance is one or two rows only.** Two
ramps, different lengths, answering different questions. Spreading brightness
across the whole wake lights a third of the panel and loses the line, which is
the thing being drawn.

Visual aesthetic is the priority. CPU cost is explicitly not a concern this
round, and no measurements were asked for.

## The model

The line leads, so the wake is on the rows it has already passed — the sweep runs
top to bottom and the trail is *above* it, at lower row indices.

- One step is one row of travel, so the animation's rate is a speed rather than a
  tick rate. `step_index` stays the memo key.
- The cycle is `rows + WAKE_ROWS` steps, not `rows`. Through the last `WAKE_ROWS`
  the line is off the bottom edge and only the wake drains. That beat is what
  stops the next sweep entering at the top while the previous trail is still lit;
  two lit bands with no line between them read as noise rather than as a scan.
- `WAKE_ROWS = 20`. Intensity is `1 - d/20` for a row `d` behind the line, zero
  beyond. Linear, because "steps down for every line" is a statement about rows.
- Whether a given cell substitutes on a given step is a **per-cell, per-step hash
  draw** against its row's intensity — not a fixed per-cell threshold. A fixed
  threshold lights the same cells first on every sweep and turns the
  lowest-threshold cells into fixed strobes for the whole 1.1s the wake covers
  them. The per-step draw gives each cell an expected rate equal to its row's
  intensity, and costs one hash per cell per step, which this round can afford.
- The frame object carries the substituted lines *and* a per-row luminance
  ordinal. An int, never a colour: `splash.py` imports no ImGui and knows no
  palette, which is what keeps the animation exercisable without a live frame.

## The rate is bounded by the frame rate, and 18 was over the bound

This is the finding worth keeping. **A luminance band narrower than its own
per-frame displacement cannot read as motion.** If the line advances two rows
between displayed frames and the band is one row tall, frame N lights row *p* and
frame N+1 lights *p+2* — row *p+1* is never lit by anything. The line does not
sweep, it hops down the block with a permanent hole in it. Apparent motion breaks
down as soon as per-frame displacement exceeds the size of the moving object.

So the bound is

    rows_per_second <= band_rows * displayed_fps

and the "one or two rows" choice is not free: at the app's real idle rate, one row
is broken and two is the minimum that works.

The frame rate to bound against is **8.7fps, not 9.0**. `settings.fps_idle` is a
`glfwWaitEventsTimeout` bound rather than a clock, so the interval is 1/9s plus
render and scheduling time and is never exactly 1/9;
`orchestrator-design.md:534-539` records the empty-fleet TRIAGE idle phase at
8.7–9.0fps. A 2-row band against 8.7fps allows 17.4 rows/s, so:

**16 rows/second with a 2-row luminance band.** The band always abuts or overlaps
its own previous position, no row is ever skipped, and the sweep takes about 5s.

18 rows/s was the original choice and it was wrong twice over — 2.07 rows per
frame, which opens the hole, and justified by an "integer multiple of
`fps_idle = 9.0`, so the line advances a whole number of rows every frame"
argument that this repo's own benchmark contradicts. **The stashed code contains
that false comment.** A comment asserting an evenness the repo has already
measured to be absent is worse than no comment.

Faster frames only shrink the displacement, so the bound only ever has to be
checked against the slow end. That matters because desktop input wakes the runner
to full speed — pointer crossing, focus, window mapping, and hello_imgui holds
full rate for a few frames after input (`orchestrator-design.md:566-580`) — and an
operator looking at a cold-start splash is moving a mouse over it. At 60fps the
displacement is 0.27 rows/frame and the line simply holds a row for several
frames.

## The photosensitivity argument moved rather than disappeared

The existing code's protection is the per-cell stagger: `_CELL_PERIODS` and its
pairwise-coprime periods exist so ~4.4k glyphs do not change together at ~3Hz.
The new model deletes that mechanism, and the honest position is that what
protects the panel changed rather than that the need went away:

- **The modulation is spatially local and moving.** Only the 20 rows behind the
  line can change, the ramp averages 0.525 over them, and the cycling set is a
  third of the ranked cells — under 6% of the field substitutes on any step,
  against the whole-field third of the old model. No cell is inside the wake for
  longer than ~1.1s per ~5s cycle.
- **Ink is conserved.** Substitution never leaves a cell's measured ink bucket, so
  what modulates is glyph identity and not the area-averaged luminance of the
  field, which is the quantity the hazard is about.
- **What does modulate luminance is two rows of 61**, travelling, never the field.

Two costs, and they are costs rather than properties that were preserved: near
the line a cell can substitute on consecutive steps, which the old model
forbade outright, and at 16 steps a second the field's churn per *second* is
higher than its churn per step suggests.

**This was not settled.** A moving high-contrast edge is not obviously safer than
a distributed shimmer just because fewer cells change, and the reviewer was asked
to argue it rather than assert it. If the highlight is bright and the band is
narrow, the local contrast step is large. What would settle it is the luminance
actually chosen, on the palettes that maximise it — this stays open.

## The renderer has to stop being one call

`inbox.py:_art` draws the whole picture as one `text_unformatted("\n".join(...))`
under one pushed style colour — one colour for 61 rows, so a bright line is not
expressible. That one call has a reason: ImGui advances a multi-line block by
exactly the font size per line, which is what `splash.LINE_PITCH_EM` asserts,
where one `text()` per row would insert `style.item_spacing.y` between rows and
stand the art taller than `required_extent` promised.

The way out is the one `_quote` already takes (`inbox.py:634`): draw through the
window draw list at computed positions and claim the space with `imgui.dummy`.
One `add_text` per row at `origin.y + row * size * LINE_PITCH_EM`, one colour per
row. That moves the art's line pitch from ImGui's block layout onto
`splash.LINE_PITCH_EM`, which is the constant `fit_size` and `required_extent`
already agree on. **Unverified:** whether the last row's descender still lands
inside what `required_extent` reserved once rows are positioned by hand.

Colours must be named palette entries, not arithmetic — `theme.faded` is the
memoised escape hatch for an intermediate step. The choice has to read on all
nine palettes in `theme.THEMES`; CDE and LIGHT are the ones that break things
here, which is why the quote's contrast floor next door exists at all.

## Tests: what changes and what must not

Several tests in `tests/test_splash.py` are pinned to the old mechanism and will
fail. Most encode a property that still matters, stated in terms of a mechanism
that is gone, and the work is to tell those apart one at a time:

- The safety group — `test_only_a_fraction_of_the_cycling_cells_change_on_any_one_step`,
  `test_the_moving_set_does_not_repeat_within_the_first_sixty_ticks`, and the
  `MAX/MIN_SIMULTANEOUS_CHANGE` and `STEADY_RATE_SPREAD` constants — asks a
  question the new design answers differently. Re-pin, do not delete.
- `test_a_cell_holds_its_glyph_for_several_steps_before_substituting` is the one
  genuinely obsolete test: the new model deliberately allows consecutive-step
  substitution. Decide it explicitly rather than deleting it quietly.
- Unchanged and still to be pinned: the space guard in both directions, unranked
  glyphs, out-of-range bucket indices, in-bucket substitution, hash-seed
  independence across processes, and the memo recomputing once per step.

New properties needing cover: the wake is bounded and behind the line; intensity
is monotonic in distance, averaged over enough steps to see past the per-step
draw; every row is inside the wake at some point per cycle, so no band is frozen;
only one or two rows are ever above base luminance and the brightest is the
raster row.

`tests/test_splash_junction.py` derives its `_STEP` from `STEPS_PER_SECOND`, which
the rework replaces.

## Not looked at

`scripts/verify_splash.py` opens a live frame and takes `--capture`, and it is the
only way to settle any of the visual claims — none of them were checked against a
frame before this was parked. `scripts/bench_idle.py` measures the splash and may
be made stale by a rate change. `CYCLING_FRACTION`, `is_cycling`, the quote, and
the whole fitting section were out of scope and were not touched.

---

## Closing note, 2026-08-16

The model above shipped as described. Three things this document left open, and
where each of them ended up.

**The photosensitivity argument was made rather than asserted, and it moved into
the code.** It lives in `splash.py`'s "what protects the panel" section, because
it is an argument about constants that a reader changing them has to meet. What
carries the panel is rate and area: a fixed row pulses once per `rows + WAKE_ROWS`
steps — 0.198 Hz, below the photosensitive band entirely rather than merely inside
WCAG's allowance — and the two lit rows of 61 occupy 10% of a 10-degree field at
the largest size `fit_size` returns, against a 25% area threshold. The argument
from *dimness* is recorded there as false, which is the part worth carrying: the
DIM-to-RASTER step is 0.47–0.76 of relative luminance depending on palette, against
the 0.10 at which the general flash threshold starts, and it is supposed to read.

What stayed thin is the per-cell rate, exactly where this document guessed it would
be. A cell being overtaken can substitute on consecutive steps at 16 Hz, inside the
15–20 Hz region where photosensitive response peaks, and nothing about the sweep's
own 0.198 Hz protects it. What does is that a substitution never leaves the cell's
measured ink bucket — so it moves about 0.05 of relative luminance, half the
threshold. That is now a standing bound rather than an observation:
`tests/test_splash_art.py`'s `test_no_ink_band_is_coarser_than_the_generator_settled_on`
(named `test_no_swap_changes_a_cell_by_more_than_a_sixth` when this note was written)
holds the ratio at 1.18, and it was written to keep the picture's shape rather than
for this, so **widening the ink bands is a photosensitivity change** and `splash.py`
is the only place that says so.

**None of it was measured against a photometer or a live frame.** Every figure is
computed from the palettes and from a viewing geometry stated in `splash.py` because
this repo records none — 1920×1200 at 24 inches, viewed at 60cm. Written down so it
can be disagreed with, not because it is authoritative.

**The descender question was not settled.** Whether the last row's ink lands inside
what `required_extent` reserved, now that rows are placed by hand rather than by
ImGui's block layout, still wants `scripts/verify_splash.py` and a live frame.
`tests/test_inbox_rail.py` pins the row pitch and pins that the claimed extent is
exactly `required_extent` — that is the arithmetic, and the arithmetic was never the
part in doubt. The supporting evidence is a `calc_text_size` measurement quoted in
`_art`'s docstring showing ImGui's own multi-line block advancing by the same pitch,
which makes the hand-placed rows a reimplementation of what the one call already did
rather than a new geometry. That is an argument, not a rasterised frame.

**The luminance triple is not the obvious one, and that is now pinned next door.**
`text_dim`/`text`/`text_strong` must not ship: `text` and `text_strong` are the same
bytes on `high_contrast` and `win311`, which flattens the line into a bar, and on
`cde` and `turbo` they separate in hue alone, which `theme.py`'s first rule forbids.
TRAIL is instead RASTER's own ink at `theme.faded` step 10 of 12, forced from both
sides — step 9 collapses TRAIL into DIM on `high_contrast`, step 11 collapses it into
RASTER on `dark`. `tests/test_theme.py` holds the band across all nine palettes,
because `RASTER_ROWS_PER_SECOND = 16` is sound only while the band is two rows, and
`splash.py` emits an ordinal and imports no palette by design so it cannot check its
own precondition. On `cde` the separation is 1.196:1 and provably cannot be better:
its whole ink range spans 1.77:1, so three levels are 1.33:1 per step at best.

The test decisions went as proposed. The one genuinely obsolete test,
`test_a_cell_holds_its_glyph_for_several_steps_before_substituting`, was replaced by
its inverse rather than deleted, since consecutive-step substitution is now
deliberate. `scripts/bench_idle.py` was not re-run.

---

## Note, 2026-08-20: substitution no longer stays in the cell's bucket

Added rather than folded into the closing note above, so the argument that was made in
August is still readable in the form it was made. The body of this document is a
pre-code snapshot and was already history; what follows corrects the **closing note**,
which claims to describe the tree.

The full decision, its reasoning and its cost are in
[`2026-08-20-the-wake-picks-from-the-whole-inventory.md`](2026-08-20-the-wake-picks-from-the-whole-inventory.md).
In short: `splash.art_frame` now draws a substitute from the union of all buckets — the
whole 165-glyph inventory — because near-random glyphs in the wake are the glitch effect
wanted. It is an aesthetic call made with the photosensitivity consequence in view, and
photosensitivity was explicitly downgraded as a priority for this panel.

Five present-tense claims in the closing note are now false:

- **"It lives in `splash.py`'s 'what protects the panel' section."** That section was
  deleted. Nothing in `pptmstr/` contains the phrase; the prior text is at
  `git show 1f1a1fc:pptmstr/ui/splash.py`.
- **"A substitution never leaves the cell's measured ink bucket — so it moves about 0.05
  of relative luminance, half the threshold."** It leaves the bucket on every swap. The
  per-cell luminance step is now whatever the full inventory spans, and that span has not
  been computed.
- **"That is now a standing bound rather than an observation."**
  `tests/test_splash_art.py`'s ink-ratio assertion still holds `MAX_INK_RATIO = 1.18`, so
  the number is live, but it bounds the *generator's* buckets and no longer bounds any
  swap. It is a drift detector on `scripts/rank_glyphs.py`'s parameters.
- **"Widening the ink bands is a photosensitivity change and `splash.py` is the only
  place that says so."** False in both halves. Widening the bands cannot change what the
  pool contains, so it is not a photosensitivity change; and `splash.py` says nothing
  about photosensitivity now, while `tests/test_splash.py` and
  [`2026-08-15-an-empty-fleet-is-a-state-the-app-renders.md`](2026-08-15-an-empty-fleet-is-a-state-the-app-renders.md)
  both do.
- **"A viewing geometry stated in `splash.py`."** `splash.py` states none — no viewing
  distance, no display size, no field angle. The 1920×1200-at-24-inches-at-60cm geometry
  exists only in the paragraph above. Which does not weaken that paragraph's own point:
  it says every figure is computed and none was measured against a photometer or a live
  frame, and that remains the most accurate sentence in this note.

What survives unchanged: the rate-and-area argument for the sweep itself. A fixed row
still pulses once per `rows + WAKE_ROWS` steps at 0.198 Hz, and two lit rows of 61 are
still the only thing modulating field luminance by position. Those never depended on the
in-bucket invariant. Whether the area argument alone is sufficient without it has not
been re-examined.
