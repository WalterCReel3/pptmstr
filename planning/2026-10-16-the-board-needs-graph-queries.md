# The board needs graph queries, not a lead inferring them

**Dated:** 2026-10-16 · **Status:** scope for work not started · **Found by:** the operator,
watching a lead run the ui-rework board: "Do you need graph tools to help with the board? I'm
wondering if it's being resolved the hard way through inference." The answer is yes, and this
records what went wrong and what would have prevented it. **Evidence:** the ui-rework session's
board (about 70 tasks over two days) and `notes/2026-10-16-lead-handoff.md`.

## What the board does today (read from its tool descriptions; not re-measured here)

- `declare_task(..., depends_on, touches)` adds an edge on its own to every unfinished task that
  writes one of the same files. It reports those edges in the declare reply, after the task
  already exists.
- `depends_on` cannot be amended. `amend_task` replaces the detail and the property only.
- A task's declarer cannot claim it once it has amended it. A task is completed only by
  whoever holds it, or closed by the operator from the board pane.
- `read_board` returns every task, cut to fit, and on a large board the reply overflowed
  (60-100 KB) and had to be saved to a file and searched.

## What went wrong, with the instances

1. **Edges the lead could not see until after they existed.** The lead declared tasks, and only
   the reply showed they now waited on chains of unrelated work through shared files.
   - `ini-isolation` was added to `detail-header`;
   - `empty-bookmarks` to `composer-margins`;
   - `composer-margins` to `composer-margins-2`;
   - `delete-rich-pane`, `baseline-sites` and `theme-fix-2` to `turn-boundary`.
   Each time the fix was another declaration or amendment, made after the fact.
2. **"Why is this blocked?" took the whole board.** The question had no direct answer. The lead
   saved the overflowing `read_board` to disk and grepped it, then traced the chain by hand.
   Builders could not trace it either: three builders were started for tasks that turned out not
   to be claimable, and one polled a blocked task on a timer.
3. **Withdrawn tasks went on blocking.** `delete-rich-pane` was withdrawn on the operator's word,
   but stayed pending and held baseline-sites, theme-fix-2, turn-boundary, ini-isolation, the
   margins and the gate. The lead had amended it, so it could not claim it to close it. A builder
   could close it only once its own dependencies finished. The operator was asked to close it
   several times. `empty-bookmarks`, `composer-margins` and `font-raster-probe` did the same.
4. **The gate's dependencies were kept by hand.** `gate-2` was declared early, its real
   dependency list grew to about twenty tasks declared after it, and since `depends_on` cannot be
   amended, the list lived in the gate's prose. That prose was rewritten nineteen times, and was
   wrong at least once (it named a withdrawn task as required).
5. **No ready view.** Before starting each builder, the lead inferred what was claimable now and
   which chain mattered most. Several builders were handed ordered lists with "if claimable"
   clauses, a sign the board did not answer the question.

## What would have prevented each (proposals; none built, none decided)

- **(1) `declare_task(..., dry_run=true)`.** Returns the edges the board would add and the full
  transitive chain, without creating anything. The lead can then narrow `touches` or split the
  task before it exists.
- **(2) `why_blocked(task_id)`.** Returns the chain from a task to the unfinished tasks it
  ultimately waits on, each with its holder and state. It costs one call instead of a board
  read, and builders can use it too.
- **(3) Withdrawn as a state, not a spec text.** `withdraw_task(task_id, reason)`, available to
  the declarer, which completes it as withdrawn and releases its edges. Or a withdrawn task stops
  counting as a blocker. Either way the operator is not the only one who can unstick the board.
- **(4) Dependencies the board computes.** Either `amend_task` may change `depends_on`, with the
  board re-checking for cycles, or a task may depend on a query, such as "every task, declared
  before or after me, that touches a file". A gate becomes `depends_on="all-writers"` and stays
  exact.
- **(5) `read_board(open_only=true)` and `ready()`.** The open tasks only, and the claimable ones
  ranked by how much they unblock (the critical path). It removes the inference before every
  spawn.

## Open questions

- Should the automatic file-overlap edge stay the default? It prevented real conflicts, and also
  serialised work that only shared a test file. A softer rule, an edge only on source files, or
  only when both tasks are claimed at once, would trade safety for throughput. Not decided.
- A query dependency (4) can make a gate wait forever if something keeps declaring work.
  Whether it should freeze at the moment the gate is claimed is open.
- Which of these belong in the board's tools and which in a lead-side helper is open.

## Not measured

Every instance above comes from the session's own board replies and the lead's transcript. No
probe was written, and no board timing or size was measured beyond the overflow messages the
tools printed.
