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

## Test scenarios, drawn from the case study

Each scenario is a small board built in a test: tasks with ids, `touches` and `depends_on`,
some claimed or completed. Each names the query it exercises and what must come back. The
fixtures copy the shape of the ui-rework failure that motivated them. They are outlines for the
unit tests, not the tests themselves.

### A. Dry-run declare (from failure 1)

- **A1, a shared source file.** `theme-fix-2` touches `launcher.py` and is pending, behind
  `baseline-sites` (claimed). A dry run of `launcher-place` touching `launcher.py` returns the
  added edge `launcher-place → theme-fix-2`, the chain `theme-fix-2 → baseline-sites`, and
  claimable=false. The board is unchanged afterwards: same task count, same edges.
- **A2, a shared test file only.** `font-raster-probe` touches `tests/test_style_probe.py`, as
  does a pending `theme-fix`. The dry run reports the edge and marks it as caused by a test file
  only, so the lead can narrow `touches` (the open question on softer edges).
- **A3, a chain through a withdrawn task.** `composer-margins` touches `app.py`; `empty-bookmarks`
  touches `app.py` and is withdrawn. A dry run shows the chain ending at the withdrawn task, and
  says so, not merely "pending".
- **A4, a self-overlap.** Declaring `composer-margins-2` with the same files as the superseded
  `composer-margins`: the dry run shows the new task would wait on the one it replaces.
- **A5, no overlap.** A probe touching only new files (`scripts/link_probe.py`) returns no edges
  and claimable=true.

### B. `why_blocked` (from failure 2)

- **B1, a three-deep chain.** turn-boundary waits on theme-fix-2, which waits on baseline-sites,
  which waits on delete-rich-pane, which is withdrawn and unheld. `why_blocked(turn-boundary)`
  returns that path in order, each with its state and holder, and ends at the withdrawn root.
- **B2, several roots.** `detail-actions` waits on history-to-digest, wake-asker and
  detail-header, which each have their own blockers. The answer lists every unfinished root once
  (a set, not a path per edge), with the shortest path to each.
- **B3, a claimable task.** It returns "claimable", with no chain.
- **B4, a dependency that is held, not pending.** The chain stops at the claimed task and names
  its holder (`builder-45`), since that is the actionable fact.
- **B5, scale.** A 70-task board answers without returning the board. The reply's size is bounded
  by the chain, not by the board (a test on reply length).

### C. Withdrawn tasks (from failure 3)

- **C1, the declarer withdraws.** The lead declares, amends, then calls
  `withdraw_task(delete-rich-pane, reason)`. It succeeds although the lead amended it, the task
  reads withdrawn with the reason, and every task blocked only by it becomes claimable.
- **C2, a withdrawn dependency of a gate.** A gate's dependency set includes a withdrawn task:
  the gate is not held by it.
- **C3, withdraw while held.** A task held by `builder-49` is withdrawn by the lead. The holder is
  told, through a concern or the next read, and its later `complete_task` is refused or recorded
  against the withdrawn version, which the test pins.
- **C4, not the declarer.** A builder cannot withdraw a task it did not declare.

### D. Computed dependencies (from failure 4)

- **D1, a gate on every writer.** `gate-2` with `depends_on="all-writers"`, declared first. Five
  tasks declared after it, each touching files. The gate waits on all five, and on a sixth
  declared later, without an amend.
- **D2, a freeze at claim** (the open question). Once the gate is claimed, a task declared
  afterwards does not join its set. Or it does, whichever is decided, and the test pins it.
- **D3, withdrawn tasks drop out.** A withdrawn writer is not in the gate's set (C2 again, through
  a query).
- **D4, amending `depends_on`, if that route is chosen.** An amend that would create a cycle
  (`a → b`, then amend `b` to depend on `a`) is refused with the cycle named.

### E. Ready view (from failure 5)

- **E1, ready set.** On the ui-rework shape mid-session, `ready()` returns exactly the tasks whose
  dependencies are all complete, and none held.
- **E2, critical-path rank.** Among two ready tasks, the one that unblocks more pending work ranks
  first: `theme-fix-2`, which unblocked six tasks, above `diff-parity`, which unblocked none.
- **E3, `read_board(open_only=true)`.** Completed and withdrawn tasks are omitted, and its size is
  bounded by the open tasks.

### F. Properties across all queries

- **F1, the queries are pure.** No query, dry runs included, changes the board, and each is
  answered from one snapshot.
- **F2, edges derived, not stored.** An edge from file overlap is a function of `touches` and
  states. Completing one task removes its edges without a separate unblocking step (STYLE.md
  "Derive; do not store", as `TaskState` already does).
- **F3, ids not found.** Each query given an unknown id says it is unknown. It does not answer as
  if the task were blocked or ready. (STYLE.md: an error that does not tell the two mistakes
  apart.)

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
