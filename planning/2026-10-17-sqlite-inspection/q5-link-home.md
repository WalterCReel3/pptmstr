# Q5: where the link record lives, settled against S3

**Dated:** 2026-10-18 · **Task:** Q5-settle-h4 (spec v3) · **Answers:** S3-h4-skeptic (findings
F1–F10, needs-a-run A–E) · **Revises:** [q4-comparison.md](q4-comparison.md) §(b2.b) and §(c),
where a visible "Revised (S3; Q5)" note now points here. Nothing in q4 was removed.

**Scope.** Only where the link record lives (H4 a copy per member's log, H2 a channel log, H3 a
config file). Messages stay where Decided 5 and 6 put them, filed in the sender's log, whatever
Decided 8 settles about their type. Where a candidate makes message delivery harder, this says so.

**Tags.** **run**: executed today with `q5_probe.py`. That means this worktree's `pptmstr`
modules, `/home/wreel/Source/pptmstr/.venv/bin/python` (3.11), and logs on disk under `$TMPDIR`
through the real `BoardWriter`, `boardsync.find`, `Store`, `app._open_board_log` and
`app._record_board`. **read**: read in this worktree. **inferred**: reasoning about code that is
not built. Two full runs at 500 trials gave the numbers below. Both ended `ALL ASSERTIONS HELD`.

> **Revised (G3; Q5-ranges), 2026-10-18.** B and E now report six and five probe runs as samples,
> not as ranges: the original two, G3's re-run, and three more under Q5-ranges, each at 500
> trials and each ending `ALL ASSERTIONS HELD`. The three Q5-ranges runs were launched from this
> worktree as cwd with no `PYTHONPATH`, as that task specified. Run that way, a script imports
> `pptmstr` from the venv's install, `/home/wreel/Source/pptmstr/pptmstr`, not this worktree's.
> The two trees were byte-identical (`diff -rq`, both at `e57b027`) when the runs were made, so
> the numbers stand for this worktree's code. Strictly, though, they were run against the main
> checkout's copy of it. The usage line in `q5_probe.py`, which sets `PYTHONPATH=<worktree>`, is
> the invocation that pins the worktree.

> **Revised (Q5-ranges-C).** This document states no invocation of its own for the original two
> runs; the one to use is the probe's usage line, `PYTHONDONTWRITEBYTECODE=1
> PYTHONPATH=<worktree> /home/wreel/Source/pptmstr/.venv/bin/python q5_probe.py`, which the
> Q5-ranges-C runs used (import path confirmed as this worktree's `pptmstr`).

**On the lean.** The operator leaned towards H4, and S3 argued against it. Neither is used as
evidence here. Q4's note that its draft moved to H4 after the lean was relayed is the reason this
re-walk exists. It is not counted against H4 either.

---

## (a) The runs

**A: a record about something its log never introduced (run).** There were three orphans, one
for each family the fold matches on:
- `Claimed` for a task never declared;
- `Edited` for a concern never posted;
- `Delivered` naming a concern never posted.

Each was written through `BoardWriter` and restored through `boardsync.find` → `BoardRestored`
into a fresh `Store`. Every case gave the same result:
- `stray=1`, `Board.short=True`;
- `Restore.log_short=True`, `adoptable=False`;
- `board_pane.unrecorded_reason` gives "its log holds more than was restored…".

A later append by a writer that had not adopted the log was refused (`kept=False`, P12). A
second restore into a fresh store was unadoptable again. **So one orphan record makes a member
unadoptable in every later process.** The F1 mechanism is confirmed with today's kinds.

Separately, the same log restored twice leaves `restores` and `tasks` unchanged. That is the
`report == prior` → None path that S3 F10 names as a trap.

**B: announce and restore in separate batches (run).** `_launch` was mirrored from
`pool.submit` on: `AgentSpawned` emitted as `announce` does, then the real `_open_board_log`.
This ran against a draw thread that drains and calls `apply_all` under the real `_record_board`
in a tight loop. Results:
- **The two landed in separate batches in 150/500 and 128/500 resumes.** Each time, a snapshot
  existed with the root present and no `restores[sid]`.
- In a forced split, the `board_logs` entry did not exist yet after the announce batch.
  `_open_board_log` writes it just before it emits.
- Every resumed session still ended adopted.

The real frame loop drains once per frame, not in a tight loop, so 25–30% is the harness's rate,
not the app's. The point is that **the split is reachable**: no lock spans the two emits.

> **Revised (G3; Q5-ranges).** "25–30%" was two samples, not a bound, and a later run fell
> outside it. Separate batches, out of 500 resumes per run (n = 6 runs):
>
> | Run | Original 1 | Original 2 | G3 | Q5-ranges 1 | Q5-ranges 2 | Q5-ranges 3 |
> |---|---|---|---|---|---|---|
> | Split | 150 | 128 | 114 | 115 | 122 | 124 |
>
> The lowest seen was 114/500 (22.8%) and the highest 150/500 (30%). That is the spread across
> six runs, and a seventh may land outside it. In every Q5-ranges run, the count of "a snapshot
> with the root present and no restore report" equalled the split count. G3's figure is relayed
> from the task specification; its log was not read. **No conclusion changes.** The rate is
> still the harness's, not the app's. What the argument rests on is that the split is reachable,
> and all six runs reached it.

Whether handover is pushed or pulled, as of today:
- **Read:** a cross-session concern wakes nobody (`store._concern_wake` is worker-to-own-lead
  only). Delivery is the recipient's own `InboxRead`, an intent that `bus` emits through the same
  FIFO (`bus.py` `bridge.emit(InboxRead(...))`). A recipient's bus handler cannot run until the
  asyncio loop yields, and by then `BoardRestored` is already queued ahead of it
  (`_open_board_log` docstring). **So a pull handover always sees the recipient's restored board,
  tombstones included.**
- **Read and run:** the plan says delivery wakes the recipient (proposal item 2). A wake today
  carries no content. The probe printed "a worker sent you a concern. read_inbox() returns it."
  It is sent no earlier than `WAKE_WINDOW_S` (2 s) after it is raised, by `_send_wakes`, which
  runs after that frame's `apply_all` (app.py frame order, read).

**C: how operator acts enter the store (read + run).**
- **Read:** `ui/` emits operator intents with `bridge.emit` from the draw thread
  (`decision_view`, `board_pane`), and `app` does the same (`SessionDetached`,
  `FailureAcknowledged`). They share the one FIFO with the asyncio thread, and nothing orders
  them against a resume's two emits.
- **Run:** an operator intent was emitted from a second thread while a resume ran. Today no
  operator intent yields a fact for *another* session's log. So `boardfacts.outcomes` was wrapped
  to add one fact for the resumed member, as an H4 link record would. That wrapper is the only
  stand-in in the probe.
- **Forced order** (announce, act, restore): the member ends `recording=False`, with "its board
  changed before its log had been applied".
- **Raced, 500 trials, twice.** The barrier deliberately puts the act on top of the resume, so
  these are rates of the outcome given an overlap, not of an overlap:

| Outcome | Run 1 | Run 2 |
|---|---|---|
| Fact written | 397 | 386 |
| **Unrecorded** for the process | **101** | **111** |
| **Fact skipped, then adopted without it** | **3** | **2** |

> **Revised (Q5-ranges-C), 2026-10-18.** The table above is two samples, and the "≈20%" and
> "≈0.5%" that §(b) F9 drew from it read as bounds. Every C sample seen, out of 500 raced resumes
> per run (n = 8 runs, each ending `ALL ASSERTIONS HELD`):
>
> | Outcome | Run 1 | Run 2 | Q5-ranges 1 | Q5-ranges 2 | Q5-ranges 3 | Q5-ranges-C 1 | Q5-ranges-C 2 | Q5-ranges-C 3 | Min–max seen |
> |---|---|---|---|---|---|---|---|---|---|
> | Fact written | 397 | 386 | (421) | (394) | (397) | 390 | 412 | 406 | 386–421 |
> | **Unrecorded** | 101 | 111 | 77 | 104 | 99 | 108 | 84 | 91 | **77–111 (15.4–22.2%)** |
> | **Skipped, then adopted** | 3 | 2 | 2 | 2 | 4 | 2 | 4 | 3 | **2–4 (0.4–0.8%)** |
>
> Q5-ranges' unrecorded and skipped counts are relayed from that task's result; its log was not
> read here, and its written counts were not recorded, so the bracketed ones are 500 minus the
> other two (the probe asserts the three sum to the trials). The Q5-ranges-C runs were made with
> `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<this worktree>` and the venv's python, at `e57b027`;
> each log opens with `pptmstr.__file__` = this worktree's `pptmstr/__init__.py`. Logs are under
> `$TMPDIR/q5c-run{1,2,3}.log` for that session only. An eighth or ninth run may land outside
> these spreads. **No conclusion changes.** Both outcomes were reached in all eight runs, and the
> rates remain the harness's rates given a forced overlap, not the app's.

The third outcome is new. It happens when the act is applied before `_open_board_log` has
written the entry. The member is adopted, its log silently lacks the link record, and nothing
marks it.

**E: price of a synchronous channel-log read at the migrate slot (run, warm page cache).**
`boardlog.read` + `restore_log` per directory, 7 runs each, two runs of the probe:

| Channel logs | 10 records each | 200 records each |
|---|---|---|
| 1 | ~2 ms | ~18 ms |
| 10 | 8–10 ms | 147–191 ms |
| 100 | 76–94 ms | 870–1020 ms |

> **Revised (G3; Q5-ranges).** The table above is two samples, not a bound. The record does not
> say whether each cell spans the two probe runs' medians or the 7 repetitions within them. G3
> relays a run 15–25% faster than every cell, with no per-cell values on record. Below are the
> per-probe-run **medians of 7 repetitions** from the three Q5-ranges runs, in ms, with the
> min–max of all seen samples. "Seen" covers the original cells' endpoints and these three
> medians. G3's values were not recorded, so they are not in it:
>
> | Channel logs × records | Q5-ranges 1 | Q5-ranges 2 | Q5-ranges 3 | Min–max seen (n = 5 runs) |
> |---|---|---|---|---|
> | 1 × 10 | 1.92 | 1.91 | 1.75 | 1.75 to ~2 |
> | 10 × 10 | 6.47 | 6.45 | 6.28 | 6.28–10 |
> | 100 × 10 | 65.27 | 65.63 | 60.66 | 60.66–94 |
> | 1 × 200 | 11.85 | 12.07 | 12.02 | 11.85 to ~18 |
> | 10 × 200 | 118.21 | 120.58 | 121.33 | 118.21–191 |
> | 100 × 200 | 709.88 | 715.71 | 719.64 | 709.88–1020 |
>
> Within each Q5-ranges run, single repetitions ranged wider than the medians do. The 10 × 10
> cell's slowest repetition was 18.81–20.36 ms in each of the three runs, one slow repetition in
> seven. Every other cell's slowest repetition stayed within 14% of its median. Logs are under
> `$TMPDIR/q5-run{1,2,3}.log` for this session only. Where the original cell gives a range, the
> Q5-ranges medians sit about 14–35% below its low end, about what G3 saw. The 1 × 10 cell's
> "~2 ms" gives no range to compare. The machine and its load are the obvious difference, and neither was
> controlled. **No conclusion changes.** Faster samples only strengthen "single-digit
> milliseconds" for a handful of sub-10-record logs. Even the slowest repetition seen at 10 × 10
> (~20 ms) does not make E's startup cost matter at the migrate slot. This is still warm cache
> only. Cold cache, the reversal check in §(e) 1, is still not measured.

A link log holds a link, an unlink, perhaps a member change: under 10 records. Pairs-only, one
operator: a handful of channels. So H2(a) costs **single-digit milliseconds** at startup, warm.
Cold cache, and a slow or hung mount, are **not measured**. `_migrate_board_logs` already accepts
the hung-mount case and logs a line before it.

**D was not run.** No H4 or H2 prototype was built. F3, F4 and F6 stay inferred.

---

## (b) Finding by finding

| # | S3's claim (short) | Verdict | Evidence | Basis |
|---|---|---|---|---|
| F1 | An orphan record goes to `stray`, makes `Board.short`, and the member is never adoptable again; under H4 orphans are ordinary | **Accepted** for the mechanism; **partly** for the examples | A (run): three orphan kinds, permanently unadoptable. Refuted sub-example: a member's own channel *message* naming X is not stray. Stray is "about a thing the log never introduced", and a message is introduced by its own `Posted`. So H4's fold must treat an unlink, or a member change, of an unknown link id as normal, and a repeated identical link record as held, not `contested`. That is a design requirement of the new arm, and H2 never needs it | run + read |
| F2 | Announce and restore can land in separate batches; handover on "B became live" leaks | **Partly** | B (run): the split is reachable. But today's handover is pull, and a pull is FIFO-after the restore (read). A push that carries no content, as today's wake does (run), leaks nothing; at worst it is a spurious wake. So 3′ reduces to: **the handover decision is made on the recipient's own read intent, and any push carries no peer content.** That is the cheap form. A reducer arm that delivers on `AgentSpawned` would be unsafe | run + read |
| F3 | A tombstone must never be withheld: refused, unreadable and damaged restores resurrect a link | **Accepted** (inferred, rare) | `_restored`'s `if differing:` reverts every placement (read). `_open_board_log`'s `_Unreadable` arm emits no `BoardRestored` (read). `boardlog.read` stops at damage (P3). H4 needs an asymmetry: tombstones applied from a refused restore, and a member whose log was unreadable treated as not live for delivery. **The second is a liveness check at handover that depends on restore state, which makes delivery harder under H4.** H2 has one log, loaded or not as a unit | read |
| F4 | One window and a closed card give two live links, with different ids, for one pair | **Accepted** (inferred; D not run) | It follows from (b2.b)'s rule as written: the ids are independent and the union keeps both. Harmful even under pairs-only, for S3's three reasons. It is removed under H4 only by the closed-card rule for *link* (§(d)). Under H2, the one log shows X before any relink, so the reducer can refuse a second live link for the pair | read |
| F5 | H2's demotion rested on unread code; the migrate slot removes the ordering question; H4 shares the "closed cards draw nothing" cost | **Accepted**, and priced | `_start` steps 1–3 and `_migrate_board_logs` "synchronous, once, before the window opens" (read). A channel restore emitted there is first in the FIFO, ahead of every recall card and launch, so no ordering against `_recall_fleet` exists. E (run): single-digit ms for realistic counts, warm. H4 indeed reads no log for a closed card (`_recall_fleet`: "a card is all this makes of a session"), so it is **worse**: under H4 a closed pair's link is invisible until a member resumes, while under H2(a) it is in the store from the first frame | run + read |
| F6 | Charter edit as unlink + link drops held messages; and with a closed member it splits | **Accepted** for H4 (inferred) | Liveness per id plus immutable charter per id means a message held on X fails its check after the edit. Decided 4's NEEDS YOU backstop does not cover an undelivered message on a dead id (plan, read). H4 needs either a monotone `supersedes(X)` on the new link, so held messages on X carry over, or edits only with both members recording. H2 orders records in one log, so an in-place `charter-changed` is safe and held messages survive | read |
| F7 | "Store-minted" is misnamed; ids are shell-minted uuids | **Accepted** | `bus.py`: `f"c-{uuid.uuid4().hex[:12]}"` and siblings (read). Correct condition: shell-minted uuid, reducer refuses a link id it holds or has tombstoned. Same under H2 | read |
| F8 | Link needs both members running and recording; unlink whenever visible; backfill on co-residence | **Accepted, with two additions** | §(d) below. Addition 1 (read): `board_logs` is shell state the reducer cannot see. So the rule is a UI-side precondition, checked at draw time. A member recording at draw time is still recording at apply time: within a process the flag only goes false through `Stopped`. Addition 2 (run, C): "unlink whenever visible" still lets an unlink's fact reach an awaiting member, which then runs unrecorded or silently misses the tombstone. So the observer must defer link facts for an awaiting member, not unrecord it | run + read |
| F9 | Frequency table corrections; (v) the `entry.awaiting` branch becomes reachable | **Accepted**; (v) **confirmed by run** | C: reachable, with two outcomes, unrecorded (≈20% of overlaps) and the new silent skip (≈0.5%). **Revised (Q5-ranges-C):** those were two samples, not bounds. Over n = 8 runs of 500, unrecorded was 77–111 (15.4–22.2%) and the silent skip 2–4 (0.4–0.8%), both reached in every run; per-run values under table C. The "older build" correction: an older build has no link UI, so it makes logs short and does not split copies (read) | run + read |
| F10 | Neither `NOT_THIS_SESSIONS` nor `OTHER_SESSION_HAS_ID` can fire on a new link kind; the plan overstates; `report == prior` trap | **Accepted** | Both are produced only in the task and concern loops of `_restored` (read). A (run): a repeated identical restore is a no-op, so a tombstone-only restore is lost unless links are in the comparison | run + read |

**Not on S3's list, found here.**
- **The silent skip** (C). Under H4, a link fact applied before a resuming member has a
  `board_logs` entry is dropped. The member is then adopted, and its log silently lacks the
  record. No restore will ever mark it. It is a divergence that nothing reports.
- **H4 cannot record a link to a closed card at all.** P12 refuses an append to a log the
  process has not adopted (A, run: `kept=False`). This is the structural reason the closed-card
  rule is needed under H4, and why it is not needed under H2.

---

## (c) H4 vs H2 vs H3, for the link record only

Every condition each needs to be safe. A condition marked **new** is code no candidate has today.

| | H4 copy per member | H2 channel log (a: read at startup) | H3 config file |
|---|---|---|---|
| ids | shell-minted uuid; reducer refuses a held or tombstoned id (F7) | the same | the same |
| Fold | **new** link arm. Orphan unlink and member records are not stray (F1, run). An identical repeat is held, not contested. A link map is in `_restored`'s report comparison (F10) | **new** channel fold over one log, with the existing stray and contested rules unchanged | no fold; current state only |
| Restore | tombstones applied even from a **refused** restore (F3); this changes `_restored`'s `if differing:` branch, the safety-critical one | **new** startup read and adopt at the migrate slot. E: ms. No ordering question (F5) | a startup read |
| Writer | unchanged `BoardWriter`. **But** `_record_board` must defer, not unrecord, a link fact for an `awaiting` member, and must not drop one for a member with no entry yet (C, run) | `BoardWriter` keyed by channel as well as session (q1 §3) | `os.replace` (P1 **L**) |
| Root | none | a third root under `config_dir()`, not under `sessions/` (q4 J) | a file |
| Closed cards | **link** only with both members recording (F4, F6), or the duplicate-pair and split-edit cases come back. Unlink must be allowed (GONE peers, F8) | **no rule needed for safety**: the channel log is adopted whether members run or not | none |
| Charter edit | `supersedes(X)` or both-recording (F6) | an in-place record, ordered by the one log | overwrite; no history |
| Delivery | liveness at the recipient's read (F2, cheap). A member whose log was unreadable or damaged is **not live** (F3), a restore-state dependency at handover | liveness at the recipient's read: one lookup, loaded before any session runs | the same as H2, without history |
| Healing | backfill on co-residence (F8), optional | nothing to heal | nothing |
| Two windows | today's same-session-id case | both windows adopt the channel log, so the next restore reads contested (F5). Rare for this operator | the last rename wins, unmarked |
| Older build | every member's log short (P6) | only the channel log short | ignores unknown keys (q4 §c) |
| Closed pair visible at start | no (F5) | yes | yes |

**S3's copy-repair for H4** ("backfill on co-residence"): when a `BoardRestored` brings in a link
or tombstone that an adopted member's log lacks, `outcomes()` writes it to that member through
the existing writer. This is sound as far as it goes (read). It heals every divergence once both
members share a process after the change, and it uses only the existing writer. It does not heal:
- a member that is never co-resident with the holder of the missing record (a GONE peer);
- the C silent skip, unless backfill also runs on the member's own restore.

So it reduces how long divergence lasts. It removes none of F1, F3, F4 or F6's conditions: each of
those concerns what a store believes *before* the two logs meet.

**H3 stays last**, for q4's reason: unlink and widen are named acts, and a file holding current
state loses them. Nothing here changes that.

---

## (d) The closed-card rule S3 recommends

> **Link** only with both members running and recording. **Unlink** whenever the link is visible.
> The tombstone goes to every recording member.

Its consequences under each candidate:
- **H4.** The rule is required, not optional. Without it, F4's duplicate live pair and F6's split
  edit happen in ordinary use. With it:
  - the operator must resume a closed peer to link to it;
  - an unlink with a closed member still writes the tombstone to one log only, so F2's 3′ and
    F3's asymmetries stay required;
  - an unlink landing on a member mid-resume hits C's race, so the observer change stays
    required.

  It is checkable cheaply on the draw thread, from `board_logs[sid].recording` (read). Forbidding
  unlink with a closed card as well would remove the tombstone split, but would make a link to a
  GONE peer irremovable (F8). So H4 cannot shed F3 by rule.
- **H2.** Not needed for safety. The channel log is adopted at startup, so a link or unlink with
  both cards closed is written to the one log. It can still be adopted as UX, for example to
  refuse linking a GONE session. Nothing depends on it.
- **H3.** Not needed. Not relevant to safety.

---

## (e) Judgement

This section is judgement. Sections (a)–(d) are evidence.

**The link record belongs in a channel log, H2(a), read and adopted at the migrate slot.** H4 is
second and H3 third. This reverses q4 §(c)'s H4 > H2.

The reasons, each from the evidence above:
1. **Q4's deciding cost against H2 is gone.** The restore trigger has a synchronous slot ahead of
   every launch and recall, so no ordering question exists (F5, read). It costs milliseconds for
   realistic counts (E, run).
2. **H4's conditions grew from three to about eight, and they sit in the strictest code.** The
   new ones are:
   - the orphan-tolerant fold (F1, run);
   - tombstones from refused restores, in `_restored`'s refusal branch (F3);
   - "unreadable means not live" at handover (F3);
   - the observer's awaiting branch and its silent skip (C, run);
   - a charter-edit rule (F6);
   - a UI precondition the reducer cannot enforce (F8).

   Each is buildable. Together they put the link's correctness inside `Restore.adoptable`'s
   inputs and `_record_board`'s ordering, the two places whose own docstrings say they are strict on
   purpose. H2's conditions are additive: a new fold, a new root, a generalised writer
   key and a startup read. None of them changes an existing safety predicate.
3. **Delivery is simpler under H2.** Liveness is one lookup in a log loaded before any session
   runs. Under H4 it depends on which members' logs were restored and how (F3).
4. **The closed-card case favours H2 in UX as well as safety.** Under H4 a link to a closed card
   cannot be recorded (P12, run). Under H2 it can, and a closed pair's channel line is drawable
   from the first frame (F5).
5. **What H4 still wins:** no new root, no generalised writer, and the record lives with its
   session's directory. Size is roughly equal (q1 §6 **M** each, read). Neither is measured.

**The runs refute S3 in one place:** F2 is not a leak under pull delivery. Today's handover is
the recipient's own read, FIFO-after its restore, and today's wakes carry no content. The cheap
form of 3′ is a design rule ("decide handover on the recipient's read; push only a pointer"), not
a liveness check against restore state. S3's other findings stand or are strengthened. F9(v) is
now a run, and C added the silent skip.

**What would reverse this,** each as a check:
1. **Cold-cache or many-channel startup.** A cold read of the realistic channel count, or a count
   of hundreds, measured slow at the migrate slot. Then H2(b), loading a channel at its first
   member's resume, has to be priced against H4. H2(b) brings back an ordering question.
2. **The link must travel with a session's directory**, for example a session exported or moved
   as one folder with its links intact. That is H4's one structural advantage.
3. **The operator forbids unlinking a closed card and accepts an irremovable link to a GONE
   peer.** That drops F3's tombstone asymmetry and the C unlink race. H4 is then down to the fold,
   the link-side observer race and F6, and its no-new-root advantage could carry it. This needs
   re-walking, not assuming.
4. **Two windows on one channel become ordinary.** Under H2 that is the contested-log case on
   every restore; under H4 it stays today's same-session case.
5. **The cross-process channel is un-deferred** (q4 reversal 6). Both candidates need re-walking.
6. **A D prototype contradicts F4 or F6.** For example, the reducer can refuse a relink while the
   peer's log is unrestored, without H2's single log. That would remove two of H4's conditions.

---

## (f) Not run

- **D** (a prototype of H4 or H2, and the F3, F4 and F6 scenarios). Those three stay inferred.
- **E cold cache.** The page cache was not dropped; that needs root. No slow or hung mount was
  tried.
- **B and C against the real frame loop and pool.** The harness drains in a tight loop and calls
  `_open_board_log` directly with a stand-in `state`. `pool.submit`'s `announce` is mirrored, not
  called. The rates are the harness's.
- **C's fact** is injected by wrapping `boardfacts.outcomes`. No real link kind exists.
- **No wake on a peer message** exists to test. The push side of F2 is read from today's wake
  path only.
