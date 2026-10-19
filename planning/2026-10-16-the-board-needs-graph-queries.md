# The board needs graph queries, not a lead inferring them

**Dated:** 2026-10-16 · **Status:** proposals (1), (2), (3), (5) and (4) by the query route are
built on branch `feat-graph-tooling` (2026-10-18/19), in the working tree and not committed; the
round's gate (`make check` on the finished tree) has not run. Everything above "Decided and built,
2026-10-18" is the scope as written before that round, kept verbatim; where it disagrees with
that section, that section is the current position, and the questions still open are under "Open
after 2026-10-18". · **Found by:** the operator,
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

## Second case: the feat-inter-team-linking board, 2026-10-17/18

The board is the inter-lead channel's research and round one, about 50 tasks. These are the
operator's request ("add the evidence") and the lead's account of its own board. The instances
come from board replies and worker concerns, and nothing here was measured.

- **Failure 1 again: an edge on a shared test file.**
  - B1-tests (a mechanical `channel=None` sweep) listed `tests/test_bus.py`, which B3-provenance
    also listed.
  - The board made B1-tests wait on B3, and B3 waited, through two other tasks, on the task whose
    test failures B1-tests was meant to fix.
  - So `make check` stayed red through two builds and their reviews. The edge showed up only in
    the declare reply.
  - Scenario A2 (an edge caused by a test file only) is this case.
- **Failure 3 again: a replacement waited on the task it replaced.**
  - B1-tests-a was declared to replace B1-tests without the shared file. It overlapped B1-tests,
    so it waited on it.
  - Neither could be withdrawn by the lead, so the operator was asked to close both.
  - It came unstuck only when B3 finished on its own schedule. A4 and C1 are this case.
- **Failure 4 again: a gate's real dependencies lived in its prose.**
  - G-r1 was declared before four fix tasks it had to follow.
  - Its specification was amended five times to add a "read_task these first and stop if any is
    open" step, which the gate's holder enforced by hand. D1 is this case.
- **New: a claim gave out a task for the wrong role.**
  - `claim_task()` gives out the oldest claimable task whatever the claimant's role.
  - A reviewer, which has no shell, was handed a gate that needs one, and released it.
  - Two builders were each handed the other's task, and one of them released it.
  - The lead's workaround was `claim_task(task_id=...)` in every spawn prompt, with "release and
    report if refused".
  - None of the five proposals above covers this.
- **New: writes outside `touches` are invisible to the board.**
  - Two builders ran mutation checks in place, on files another builder held at the time.
  - One restored files from copies taken 20 seconds earlier, which could have overwritten the
    other's saves. It didn't, as it turned out.
  - `touches` is a declaration, and nothing enforces it.
  - The lead's workaround is a rule in every specification: mutation and diagnostics run on a
    scratch copy under `$TMPDIR`.

## Third case: this board, 2026-10-18

The board is the `feat-graph-tooling` round that built this record's proposals, about 20 tasks.
It ran on the build that predates them, so it had none of them. The instances are the lead's
account, read from board replies; the bracketed notes say what the board shows.

- **Failure 4: a gate's late dependencies lived in its prose.**
  - `graph-gate` was declared before `digest-withdrawn`, which it had to follow. `depends_on`
    cannot be amended, so the dependency went into its specification.
  - Its specification went to v4, three replacements: v2 added `digest-withdrawn`, v3 the
    baseline, v4 `withdraw-notice` and "any fix task the lead declares later". Every later fix
    task was covered by that catch-all and never named. The gate's dependencies lived in prose
    and a catch-all sentence, not in `depends_on`. `depends_on=["all-writers"]` is the fix, and it
    was not available to the board it was being built on.
  - [The same shape recurs on `review-fixes`: a lead note makes it wait on `fix-tail`, "listed
    here because depends_on cannot be amended".]
- **Failure 1: an edge on a shared test file, in the wrong direction.** `graph-core-fix` was
  declared touching `tests/test_boardgraph.py`, which `bus-graph-tools` also lists. The board made
  the fix wait on the bus work that needed the fix. A dry run would have shown the edge before the
  task existed.
- **Failure 3: a replacement waited on the task it replaced.**
  - `graph-core-fix-2` was declared without the shared test file, but overlapped `graph-core-fix`
    through `pptmstr/boardgraph.py`, so it waited on it.
  - Neither could be withdrawn: `withdraw_task` was being built in this round, and the running
    board is not that build.
  - The fixes moved into `bus-graph-tools` as an approved write outside its `touches`.
  - [Both placeholders now read completed, by `builder-8`, with the result "superseded by
    bus-graph-tools; nothing done", basis `read` and `not run: unstated`. A completed placeholder
    is the reading withdrawal exists to avoid: on any surface that counts completions it looks
    like finished work.]
- **New: an id held by a session that is gone.** The id `gate` was refused because a removed
  session still holds it. [The lead's run: its own `declare_task` reply read "Task gate is NOT on
  the board. That id belongs to a task of session 21fa4c3d-..., which the operator removed from
  the fleet in this window." No board row shows it.]

## Open questions

- **A file overlap could be a lock, not an edge** (raised 2026-10-18, from the second case). The
  first open question below lists "an edge … only when both tasks are claimed at once" as one
  softer rule. The second case argues for it as the model rather than as a softening.
  - Overlapping `touches` would make the board refuse a claim while another claimed task holds a
    shared file. It would not order the tasks.
  - `depends_on` would stay the only way to say one task must come first.
  - B1-tests and B3 needed only not to edit `test_bus.py` at the same moment. Neither needed the
    other's result.
  - The cost: two tasks writing one file can land in either order, so an order that matters has
    to be stated.
  - It changes what an edge means, so it should be decided before dry-run and `why_blocked`
    are built on today's edges.
- **Role-aware claims** (raised 2026-10-18). Should `claim_task()` without an id skip tasks the
  claimant's role can't do (a gate for a role with no shell), or should a task name the role
  that may claim it? The same question applies to `ready()` (5).
- **Enforcing `touches` at the gate** (raised 2026-10-18; see
  `2026-08-21-the-board-takes-an-agents-word-for-what-it-will-touch.md`). The approval gate sees
  `Edit`/`Write` paths, and could refuse a write to a file another claimed task lists. Writes
  through Bash (`cp`, `sed -i`), the path of one 2026-10-18 incident, would still go unseen.
  That makes it a partial guard, and whether a partial guard is worth having is open.
- Should the automatic file-overlap edge stay the default? It prevented real conflicts, and also
  serialised work that only shared a test file. A softer rule, an edge only on source files, or
  only when both tasks are claimed at once, would trade safety for throughput. Not decided.
- A query dependency (4) can make a gate wait forever if something keeps declaring work.
  Whether it should freeze at the moment the gate is claimed is open.
- Which of these belong in the board's tools and which in a lead-side helper is open.

## Decided and built, 2026-10-18

The round ran 2026-10-18/19 on the `feat-graph-tooling` board. Decisions are the lead's in an
unattended session unless they name an operator decision. The lead read both operator answers
in `read_decisions`; they are quoted as it read them.

- **dq-0c30121920c2**, ratified by the operator: "Keep the edges."
- **dq-d757258d9488**, ratified by the operator: "oh, definitely. The lead should have that
  power. Widen it."

"Built" below means a task on the board reports it complete. Nothing is committed.

### Withdrawal (3), C1-C4

- **Decided: withdrawn is COMPLETED plus a flag, not a new `TaskState`.** It is modelled on the
  operator close end to end. `Task.withdrawn` is set, the result is the reason behind a fixed
  label, and every dependent's `blocked_on` drops the task with no unblocking step (STYLE.md
  "Derive; do not store"). C2 and D3 follow from that without code of their own.
- **Who may withdraw:** the declarer (`Task.declared_by`, amended or not: C1). Widened by
  dq-d757258d9488: the root of the declarer's session may also withdraw once the declarer has
  stopped (of an earlier run, or a sub-agent that is terminal). It may not while the task is held
  by an agent it could not reclaim from (`TaskRefusal.WITHDRAW_HELD_LIVE`); withdrawing from
  itself is exempt, since that takes no one's claim. A non-root agent never withdraws another's
  task (C4). This overturns `withdraw-task`'s property "only the task's declarer can withdraw it".
- **C3:** a held task may be withdrawn. The claim is not taken from the holder; its later
  `complete_task` and `release_task` are refused as `WITHDRAWN` with the reason quoted, and a
  `claim_task` of a withdrawn or closed task has a refusal of its own, not "already complete".
  The holder is sent a store-filed notice naming the reason and saying its files are released,
  as an operator close does, and the notice is logged.
- **Attribution:** a reason the operator edited at the approval gate reads "as edited by the
  operator", and a lead's withdrawal reads as the lead's, on read_task, read_board, the inbox
  notice, the holder's refusal, the digest, the board pane label and the board log.
- **The log** records the reason as the store holds it (read off the landed row), not the raw
  intent, for withdrawals and operator closes alike.
- **The digest** shows a withdrawn task as withdrawn and leaves it out of UNVERIFIED entirely,
  specification included, because the declarer retracted its own specification.
- `withdraw_task` parks for review wherever a declaration parks (`approval._REVIEW`).

Built by `withdraw-task`, `withdraw-notice`, `digest-withdrawn`, `withdraw-fixes` and
`final-fixes`.

### Computed dependencies (4), D1-D3

- **Decided: the query route.** `depends_on=["all-writers"]` sets a stored declared fact,
  `Task.after_all_writers`; the set is never stored. `Task.blocked_on` adds every unfinished task
  on the gate's board that has `touches`, whenever it was declared. Reason: the set is a fact the
  board already holds (touches and states), and storing an id list duplicates it, which is the
  duplication rewritten nineteen times in the ui-rework case.
- **D4 is not built.** `depends_on` still cannot be amended.
- **D2: no freeze at claim.** Claimability is decided at the claim; a writer declared after the
  gate is claimed is reported outstanding by `blocked_on`. Pinned by a test named for it.
- **A gate with `touches` stays a writer** for other gates; only a gate with no `touches` drops
  out. This overrides `all-writers`' specification v2, which dropped every gate and so
  contradicted its own property (found by `review-all-writers`). Built by `all-writers-fix`.
- **Never forever:** a declaration that would close a loop through a gate is refused as
  `WOULD_CYCLE` in either order, with the loop named. The check runs on the task as it will land
  (normalised `touches`, state PENDING, declarer stamped, closed and withdrawn flags cleared).
- The id `all-writers` is reserved. The flag is logged and restored; an older log reads false.

Built by `all-writers`, `all-writers-fix` and `final-fixes`.

### Queries (1), (2), (5) and the edges

- **Built:** `pptmstr/boardgraph.py`, pure functions over one snapshot (`why_blocked`, `ready`,
  `open_tasks`, `dry_run_declare`), wired into the bus as `why_blocked(task_id)`, `ready()`,
  `read_board(open_only)` and `declare_task(..., dry_run)`. A dry run runs the real
  `TaskDeclared` arm on the snapshot and discards the result, and emits only a board read. The
  snapshot reaches a bus handler on `BoardDelivered.snapshot`.
- **Edges are kept** (dq-0c30121920c2): file overlap still adds a `depends_on` edge at
  declaration. The lock-instead-of-edge question stays open, and every reported edge carries the
  evidence to settle it later: `shared` (the files both tasks touch), `test_only` (every shared
  file is a test file), and in a dry run only, `auto` (the board added the edge, rather than the
  declarer naming it).
- **What `shared` means:** the files both tasks touch, whatever caused the edge. It does not say
  the edge came from overlap. `Task` does not record which of its edges were automatic, so once a
  task lands an auto edge and a named one are indistinguishable; `why_blocked` leaves `auto` as
  `None`, and only a dry run's auto edges get "narrow touches" advice.
- `why_blocked` lists at most 20 roots, nearest first, then says how many more were not listed.
  It and a dry run disclose whether an id is another session's task in this process, and a dry
  run names an off-fleet holder's session. Accepted: ids within one local process are not secret.
- `why_blocked` and `ready` are auto-approved. A `declare_task` dry run is auto-approved only for
  the JSON literal `true`; the handler accepts more spellings (`"true"`, `"yes"`, `"1"`), and
  those park.
- `ready()` ranks by how many pending tasks wait on each, directly or through others. It says
  that, not "unblocks".

Built by `graph-core` and `bus-graph-tools`, with wording by `all-writers-fix` and `fix-tail`.

### Ids the board would store as another

- **Decided, at the declaration boundary:** a `task_id` or a `depends_on` entry that
  `drawable.text` would rewrite is refused (`TaskRefusal.ESCAPED_ID`), in the store and the bus
  alike, dry run included. Then the id sent is the id stored, and every lookup, inside the
  reducer or outside it, meets. The reason is in `drawable.py`'s docstring.
- The premise behind it was too broad as first stated: lookups through the reducer
  (`claim_task`, `depends_on`) were rewritten alike and did meet. The misses were the lookups
  outside it (`why_blocked`, `read_task`, the duplicate wording) and an id copied off a board row.
- For a raw escaped `depends_on` entry the refusal is a choice, not forced: let through, it would
  be rewritten to exactly the id an old-log task was restored under, and would match.
- Ids already in an older board log restore as they are.

Built by `all-writers-fix`, `fix-tail` and `escaped-dep-wording`.

### What the scenarios mean now

- **A3 and B1.** A withdrawn task is COMPLETED, so it appears in no chain, as a hop or as a root.
  "The chain ends at the withdrawn task" no longer describes anything. A3 is pinned in
  `tests/test_boardgraph.py` as "the withdrawn task is in no chain". B1 is built with
  `delete-rich-pane` pending and unheld, the state it was in before withdrawal existed, so its
  chain ends at an unclaimed root.
- **A2** is the `test_only` mark; the `auto` mark exists only in a dry run.
- **C3** chose refusal over "recorded against the withdrawn version".
- **D2** is pinned as no freeze. **D4** is not built.
- **E3** omits withdrawn and closed tasks because both are COMPLETED.

### What was run

Each builder reports its own runs; these are their figures, not re-run here. Baseline, by the
lead on `git archive HEAD` (e57b027): 13049 passed, 14 skipped, 23 xfailed. The last full suite
reported on the board is `final-fixes`' run after its last edit: 13384 passed, 14 skipped, 23
xfailed, `make typecheck` and `make lint` clean. `escaped-dep-wording` ran `tests/test_bus.py`
only, and the board does not show whether it landed before or after that run. Every builder
mutation-tested its change on a scratch copy; one mutant survived (`final-fixes` M7, below).

Not run by any task: `make check` on the finished tree (the gate), a live CLI or agent session
(the bus tests use the in-process `_live_bus`), the approval gate's edit-then-approve path for a
parked `withdraw_task` or dry run, a real on-disk board log, and `make typecheck-all`. Every
review was read-only.

## Open after 2026-10-18

From the earlier open questions:

- **A file overlap as a lock, not an edge.** Open. Edges are kept for now; the `shared`,
  `test_only` and `auto` marks are the evidence for deciding it.
- **Role-aware claims.** Open, unchanged. `ready()` does not filter by role either.
- **Enforcing `touches` at the gate.** Open, unchanged. This round again moved writes outside
  `touches` through lead approval in notes, which the board does not see.
- **Softer automatic edges.** Not decided; the lock question above subsumes it.
- **A query dependency waiting forever / freezing at claim.** Decided: no freeze (D2). "Forever"
  is bounded only by declarations stopping.
- **Board tools or a lead-side helper.** Built as board tools; no lead-side helper was weighed,
  so this is answered by what was built rather than by a recorded decision.

Known limits and defects the round left:

1. **Old-log escaped ids.** Lookups through the reducer rewrite them again, so `claim_task`,
   `complete_task` and `withdraw_task` can miss, or hit a different task: with an escaped id and
   its once-rewritten form both in one old log, a withdrawal copied off the board withdraws the
   other (`review-fixes`, read-derived). Old logs only.
2. **Notice ids are fleet-wide.** The holder's notice is filed with `setdefault` on
   `withdrawn/<task_id>`, so a restored foreign notice under that id suppresses it, and the reply
   names no holder (`review-fixes`, read-derived). The close's `operator/closed/<id>` has the same
   gap, which predates this round.
3. **The reason's bound** is checked on the raw text in the bus and on the drawable text in the
   store, so a 4000-character reason holding escapes is refused at the edge (`review-withdraw`).
   The same shape probably holds for other bounded bus texts. Not scheduled.
4. **A claimed gate counts in the cycle check**, so a writer cannot name a held gate until it
   completes. Accepted: ignoring a held gate's writers deadlocks on release. A related limit from
   `all-writers`' result: a writer that shares a file with a held gate that declares `touches`
   gets no overlap edge (it would close a loop), so both can write the file at once. Not
   re-checked after `all-writers-fix`.
5. **Two file-writing gates cannot both be unfinished**; the later is refused as a loop until the
   earlier finishes. The lead briefing says so.
6. **A board log from a build between `all-writers` and `all-writers-fix`** that holds two
   unfinished file-writing gates restores the second unplaced (`WOULD_CYCLE`). Only builds on this
   branch wrote such logs.
7. **Role-aware claims and `touches` enforcement**, as above.
8. **The board log accepts a superset of what the store writes.** `boardlog._withdrawer` checks
   who withdrew, not whether the declarer had stopped or the holder could be woken; a log holds no
   agent records. `Reclaimed` has the same limit. Its docstring names the declarer half only.
9. **Untested races.** The `WITHDRAW_HELD_LIVE` reply names the holder only if a read after the
   refusal still shows it claimed; the mutant that drops that check survives (M7), because the
   in-process bus cannot interleave a completion there.
10. **Pending when this section was written:** `graph-gate`, which waits on this record. Every
    other task on the board, `withdraw-notice` and `withdraw-fixes` included, reads completed.

## Flagged, not resolved

- **Two gates with `touches`: the accepted case is untested.** The rule is that both cannot be
  unfinished (`final-fixes`, matching the briefing in `templates.py`). No test on the board is
  reported to pin a second gate being accepted after the first completes.
- **Skipped tests.** `bus-graph-tools`' full run on a scratch copy reports 35 skipped against the
  baseline's 14; every later full run reports 14. Nobody checked which tests skipped or why.

## Not measured

Every instance above comes from the session's own board replies and the lead's transcript. No
probe was written, and no board timing or size was measured beyond the overflow messages the
tools printed.
