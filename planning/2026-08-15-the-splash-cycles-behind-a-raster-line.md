# The splash cycles behind a raster line

**Parked mid-flight on 2026-08-15**, three hours in, to fix
`driver.py`'s sub-agent liveness bug first — that one reports live agents as
FAILED, releases their cap slots, and can drop their results, and it surfaced
*because* this work was being run by a team. The findings below are worth more
than the code that was written against them, which is why they are here rather
than only in a stash.

The in-flight code is `git stash@{0}` on branch `checkpoint-fleet-board-splash`.
It does not import — `NamedTuple` had been dropped from the imports in favour of
`IntEnum` while `_Rhythm` still used it — so it was stashed rather than left in
the tree, because a package that will not import cannot launch the app the driver
fix has to be tested against. **It also carries the wrong rate and the wrong
reason for it.** See "The rate is bounded by the frame rate" below before
reapplying any of it.

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
