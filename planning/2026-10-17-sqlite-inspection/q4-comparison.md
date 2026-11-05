# Q4: the skeptic answered, and the comparison

**Dated:** 2026-10-17 · **Task:** Q4-answer-skeptic · **Answers:** S2-sqlite-skeptic (spec v4,
findings 1–14, needs-a-run A–J) · **Builds on:** [q1-fit.md](q1-fit.md),
[q2-measure.md](q2-measure.md), [q3-sources.md](q3-sources.md) and the plan
[two leads share a channel](../2026-10-17-two-leads-in-one-window-share-a-channel.md).

**Tags.** **run**: I executed it today on this machine (Python 3.11.2, SQLite 3.40.1, ext4). The
probe runs are `q2_probe.py --q4`, sections Q4-B to Q4-J, and §(a) names the section. **read**: I
read it in this worktree. **fetched**: WebFetch on 2026-10-17. WebFetch returns a small model's
extraction, not the page itself, so a fetched quote is relayed-grade. **relayed**: another
agent's account that I did not check.

Sections (a) and (b) are evidence. (c) is judgement, and it is the only section that is.

---

## (a) Finding by finding

The verdict is on the skeptic's claim: **accepted**, **refuted**, or **partly**. "File changed"
names where a visible `Revised (S2 finding N; Q4)` note now sits. No earlier text was removed.

| # | Skeptic's claim (short) | Verdict | Evidence | Basis | File changed |
|---|---|---|---|---|---|
| 1 | H1's distinctive benefits (queries, multi-row atomicity, write serialisation, "contested" detection, format) have nothing to act on here, so the link record favours H2/H3 | **Partly.** Accepted for the narrow scope (the link record and channel decisions). It rests on a scope premise this round cannot settle, which (c) makes the condition | (a) Queries: the frame loop does no IO (`ui/brief_pane.py` module docstring, read), and a restore enters as a value intent (`app._open_board_log` → `BoardRestored`, read). (b) Atomicity: one fact is one record in every Q1 design. (c) Serialisation: one process has one writer thread under P13 for both candidates, and two writer *threads* on SQLite gained nothing on correctness (D). (d) "Contested": see finding 3 (G). (e) Format: see 9c below | read + run | none (this file) |
| 2 | H6 holds: a channel decision has an author session (its asker), and the operator's answer is a fact about the asker's decision | **Accepted** | `Decision.asker`, `Decision.session_id` returns `asker[0]`, and the docstring says "Not in the board log" (`model.py`, `Decision`). `boardfacts.outcomes` returns `()` for all five decision intents. `Snapshot.decisions_readable_by` derives readability at read time. The `commandfile` precedent is real: one directory per command, create-once files linked into place, and state read from which files exist (`commandfile.py` docstring, `_create` → `os.link`). The costs stated in (a)/(b) stand: an asker that is not resumed takes its decisions out of every peer's view, which is fail-safe | read | none |
| 3 | SQLite's locking does not solve "contested"; an expected-version append is available to both, so Q1 §4 treats them unevenly | **Accepted** | Q4-G: two processes, 500 attempts each, run twice with 0 ms and 1 ms between attempts. Files (`os.link` to exactly seen+1, `FileExistsError` = stale): 719 and 600 records, 281 and 400 stale. SQLite (`BEGIN IMMEDIATE`, compare `max(seq)`, `ROLLBACK` if it moved): 997 and 979 records, 3 and 21 stale. An earlier full `--q4` run at 0 ms gave 782 and 999 records, with 218 and 1 stale. Every run had **0 duplicates and 0 gaps**. SQLite's stale path was exercised less, because its lock serialises the writers before the check | run | q1-fit.md §4 |
| 4 | (7a) The WAL-reset range is right; its trigger is narrower than Q3's note; one window with one writer connection does not meet it | **Accepted**, with one gap | Re-fetched wal.html §11.1: a complete checkpoint, a second checkpoint starting, and "another database connection commits a transaction that resets the WAL". **A: no backport here.** The linked library is the system's `libsqlite3.so.0`, package `3.40.1-2+deb12u2`, whose newest changelog entry is 2025-08-26. The bug was found on 2026-03-03. Gap: the page does not say whether a read-only connection checkpoints, except at last close (§3.1, §6). So "a reader never checkpoints" stays unsourced. Q3's "multi-connection case" wording was in Q3's task result, not in its file | run + fetched | q3-sources.md §1.3 |
| 5 | (7b) The close() lock drop is a review rule; `publish()` is a concrete trigger; in WAL the lock lives on `-shm` (recalled) | **Accepted, and worse than stated in rollback mode** | **B**, 6 repetitions, identical every time. The holder is in `BEGIN IMMEDIATE` with a row pending, and does a raw open/read/close in its own process, then a second process tries to write. **WAL:** a close on the `.db` or the `-wal` changes nothing (the second process is still refused). A close on the **`-shm` lets the second process in**; the holder's `COMMIT` then fails with `locking protocol` and its row is lost, which is loud. **Rollback:** a close on the `.db` lets the second process in and its `COMMIT` succeeds. The holder's `COMMIT` then reports `disk I/O error`, but **the holder's row is present and the second process's acknowledged row is gone**. `integrity_check` is ok. That is a silently lost acknowledged write. The mechanism is not instrumented | run | q3-sources.md §3, §5 |
| 6 | (7c) macOS has no single linked SQLite; the python.org build may be 3.50.4 | **Accepted** | `Mac/BuildScript/build-installer.py` on CPython's `3.13` and `3.14` branches and at tag `v3.14.0`: "SQLite 3.50.4" (fetched). That is inside the WAL-reset range and below the 3.50.7 backport. `bootstrap.sh` takes the first `python3.14 … python3.11` on `PATH` (read, via Q2 §1). Homebrew and uv-managed builds were not checked | fetched + read | q3-sources.md §1.3, §5 |
| 7 | Forward-compatibility trap: when Python's `autocommit` default flips to False, floor-style code meets an open transaction at its own `BEGIN` | **Accepted on sources; runtime unrun.** Its proposed fix is **refuted as stated** | Fetched: on 3.12, `autocommit=False` "ensures a transaction is always open", and `isolation_level` "has no effect unless autocommit is set to LEGACY_TRANSACTION_CONTROL" (docs.python.org/3.12). lang_transaction: "An attempt to invoke the BEGIN command within a transaction will fail with an error". **I (mypy, run):** the skeptic's fix, `connect(..., autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL)` behind `sys.version_info`, **fails `mypy --strict` at 3.12 and 3.14**, because typeshed types the parameter as `bool`. Two variants pass at 3.11, 3.12 and 3.14 and run on 3.11: `connect(path, autocommit=True)`, and setting the attribute `conn.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL` after connect (the setter is typed `int`). Their *runtime* on 3.12+ is not run. I asked the operator to run the nested-BEGIN check on 3.12 (cmd-20261018T032801Z-025ebc50a02b), and it was refused without a reason | fetched + run (mypy) | none (recorded here) |
| 8 | Unpriced costs: (a) second technology, (b) backup, (c) tests, (d) inspection, (e) two SQLite copies | **Partly** | (a) **Overstated:** "every store … is temp file + os.link" is wrong. `settings.save`, `brief.write_entry`, `sessions.py`, `layout_ini.py` and `sessionfile.update`'s existing-file branch use `os.replace` (read, grep). What holds is that nothing in `pptmstr/` or `tests/` uses SQLite (grep: no match). (b) **Accepted** (F): a copy of the `.db` alone has 0 of 100 rows with the writer open and 0 after a SIGKILL, and 100 after a clean close. (c) **Accepted** (read conftest `_no_config_dir_under_the_real_home`: it redirects `settings.config_dir` at call time and does not reach a value bound at import). (d) **Accepted.** (e) **Refuted for this venv** (H): no `.so` in the main venv links or contains `sqlite3_open`. The interpreter's `_sqlite3` is outside the venv and links the system library | read + run | q2-measure.md §6 (b) |
| 9 | (8b) Placement: a SQLite file under `boards/` is `Left` with a false WARN; a directory under `sessions/` becomes a phantom session | **Accepted** | **J** against this tree's code, with explicit roots: `channels.db`, `-wal` and `-shm` each come back `Left(reason='it is not a directory')`. `_migrate_board_logs` turns each one into a WARN at every recorded start (read). `sessionfile.listing` skips a file under `sessions/` and lists a `channels/` directory as session `channels` (read state `Absent`) | run + read | q1-fit.md §0 |
| 9c | (Q2 9c, from the spec) An older build does not see a new table; a `facts(kind, body)` table with counted unknown kinds carries P6 over, and then SQLite buys mostly transactions and indexes | **Accepted** | Q2 §5 (run, Q2) shows the table case is silent. With `body = boardlog.encode(record)`, the codec and the fold are boardlog's. What is left is the transaction (finding 1b) and indexes (finding 1a), and neither has a use in the narrow scope | read | none |
| 10 | Q1 labels H1 and H2 unevenly on P4 and on size | **Accepted** | q1-fit §2 P4 row and §6 size table, re-read. `boardlog.restore` folds tasks and concerns only, so H2's channel fold is new code, as H1's would be | read | q1-fit.md §2, §6 |
| 11 | Q2's measurements are mostly sound, with gaps: checkpoints rarely under a kill, zero think time, busy_timeout's source, "silent" overstated | **Accepted** | **C:** 200 kills with `wal_autocheckpoint=1`, and 200 with an explicit `wal_checkpoint(FULL)` per group, of which 41 landed inside the checkpoint. Both had 0 integrity failures, 0 partial groups and 0 acknowledged groups lost. **E:** with 100 ms think time, 1000/1000 commits in WAL and in rollback, no errors, longest wait 45 ms. The 3.3 s tail and the 5 s failure are artefacts of back-to-back load | run | q2-measure.md (five lines 2, 3; §1; §3) |
| 12 | (9d) One window is one process. Locking is exercised only across windows, except when H1 opens a second connection | **Accepted, with a correction** | **D:** in one process, two writer threads with their own connections: 1000/1000 in WAL, 0 lost updates, and **a 3.4 s tail**. In rollback, 999/1000, with **one `database is locked`**. So the contention tail is not a cross-process effect. A writer thread with a whole-table reader thread: 0 partial groups seen in 13468 reads (WAL) and 205 reads (rollback, where the reader is starved) | run | q2-measure.md "What was not run" |
| 13 | No file argues for replacing boardlog or moving messages | **Accepted** | Re-read q1-fit §7 ("Noted, not evaluated") and q3-sources §6 ("Nothing in this table recommends anything") | read | none |
| 14 | No version claim wrong; `python -m sqlite3` "3.12" ambiguous | **Accepted** | What's New in 3.12, sqlite3: "Add a command-line interface" (fetched). That settles the CLI as 3.12 | fetched | q2-measure.md §6 |

**Not on the skeptic's list, found here.**
- Rollback mode's lost acknowledged write (finding 5). It makes rollback mode the *more*
  dangerous fallback from the WAL-reset bug, not the safer one.
- The tail in one process (finding 12).
- The typing problem with the proposed 3.12 fix (finding 7).

---

## (b) The comparison

Candidates are Q1's, plus one added for the wider scope only:
- **H1** SQLite beside boardlog.
- **H2** a channel log in boardlog's format, under a third root.
- **H3** an operator config file (link only).
- **H4** a copy of the link in each member's log.
- **H5** per-party directories (`requests.py`'s shape).
- **H6** decisions in the asker's log (decisions only).
- **H7** *(added)*: SQLite as a rebuildable projection of the logs, never the record. Its source
  is Q3 §4's third-party pattern, which is a WebSearch lead and was not opened. It is listed only
  because (c)'s wider scope needs a candidate that answers queries without becoming the store of
  record.

Every cell names its source. "Q1 §2" etc. are those files; letters are Q4 runs.

| | H1 SQLite | H2 channel log | H3 config file | H4 copy per member | H5 per-party dirs | H6 asker's log | H7 projection |
|---|---|---|---|---|---|---|---|
| **P1–P3** never overwrites, whole or absent, consistent prefix | **E** by transaction, if append-only (Q1 §2). Atomic under 800 kills of a transactional writer, 41 of them inside a checkpoint (Q2 §3, C) | **K** (Q1 §2) | P1 **L** (rename-over), P2 **E**, P3 n/a (Q1 §2) | **K** per log (Q1 §2) | **K** per party (Q1 §2) | **K** (Q1 §2) | n/a: holds nothing of record |
| **P4** two writers, contested marked | **E** by a new fold over a facts table (Q1 §2, revised) | **E** by a new fold (Q1 §2, revised: finding 10) | **L** (Q1 §2) | **L** across copies; E at best by a tiebreak (Q1 §2) | **E** as derive-time STALE (Q1 §2) | **K**, the decision arms' guards (Q1 §2) | n/a |
| **P5–P6** a fold over facts; format only grows, unknown → short | **E only if designed in** as `facts(kind, body)`. A table per kind is silent to an older build (Q2 §5) | **K** (Q1 §2, §0.1 run) | P5 **L**, P6 **E** (Q1 §2) | **K**, but an older build is short on every member's log (Q1 §2) | **K** (Q1 §2) | **K**, with the same older-build cost as messages (Q1 §2) | n/a; rebuilt from the logs |
| **P7–P9** stop marker, durable before named, a read never waits | P7 **E**, P8 **E** (FULL by default, Q2 §1), P9 **changed**: a reader can wait (Q1 §2). 205 reads in 20 s against a rollback writer (D) | **K** (Q1 §2) | P7 **L**, P8 **E** (Q1 §2) | **K** | P7 **E** (no marker in `requests.py`), P9 **K** (Q1 §2) | **K** | P9 as H1 |
| **P10–P13** keyed path, pure core, adopt gate, one writer thread | P11 **K** if wired as Q1 §5 says. P12 **E**, needed. P13 **E**: a new writer thread (Q1 §2) | P12 **E**, needed. P13 **K/E**: `BoardWriter`'s key is generalised (Q1 §2, §3) | P12 **E** (Q1 §2) | **K** (Q1 §2) | **E** (Q1 §2) | **K** (Q1 §2) | P11 at risk: an index in reach invites a query from a reducer arm (Q1 §5) |
| **P14** hand-repairable with pptmstr closed | **L** as stated: no `sqlite3` CLI, and no `python -m sqlite3` on 3.11 (Q2 §6, finding 14). Repair is SQL through Python | **K** | **K** | **K** | **K** | **K** | **K**: delete it and rebuild |
| **Two windows** | Serialised, not judged (Q1 §4). The expected-version check works: 0 dupes and 0 gaps (G). WAL-reset preconditions are met, at SQLite's stated rate (finding 4). The close() hazard applies in each process (B) | `os.link` keeps both writes, and the fold marks what is contested (Q1 §4). The expected-version check works: 0 dupes and 0 gaps (G) | The last rename wins, unmarked (Q1 §4) | Copies diverge; wall-clock tiebreak (Q1 §4) | A derive-time conflict (Q1 §4) | Two askers' logs, the existing case (Q1 §4) | Each window rebuilds its own |
| **One window** | One writer connection; locking unused (finding 12). A second connection on a thread brings back the 3.4 s tail under load (D) | One `BoardWriter` (Q1 §3) | One writer | Members not running get stale copies (Q1 §3) | One writer per party | Existing path (Q1 §3) | A worker thread |
| **Cost in this code** | `Fact` destination sum, a writer thread, a restore trigger and report, an adopt gate. Plus the codec, if `body` is not `boardlog.encode` (Q1 §3). Size about **M+** with the codec reused (Q1 §6, revised) | The same `Fact` sum, trigger, report and gate. `BoardWriter` generalised, a channel fold, a third root (Q1 §3). **M** (Q1 §6) | **S** (link only), and it pays in P1/P4/P5/P7 (Q1 §6) | **M**, inside the restore's strictest predicate (Q1 §6) | **M–L** (Q1 §6) | **S** on top of the shared decision work (Q1 §6) | H1's layer plus a rebuild, beside the logs |
| **Operator inspectability** | `iterdump` one-liner (Q2 §6). A copy of the `.db` alone is empty with pptmstr open or after a crash (F) | `ls` / `cat` (Q1 P14) | `cat` | `ls` / `cat` | `ls` / `cat` | `ls` / `cat` | The logs stay inspectable; the projection need not be |
| **Test and maintenance cost** | A second storage technology, the only one in the tree (grep). Silent traps (Q2 §4). Review rules: WAL with one writer connection per process; no raw open of `-shm` or `.db` in process (B); no module-level path, because of conftest (finding 8c); an `autocommit` branch on 3.12+ (finding 7) | `tests/test_boardlog.py` covers append/read/codec; the fold's tests are new (finding 10) | `sessionfile` pattern | Reconciliation tests in `_restored` | New module (Q1 §6) | Decision arms in restore (Q1 §3) | H1's rules, minus durability (it is rebuildable) |
| **macOS and 3.11 floor** | The floor pins `isolation_level=None` + explicit `BEGIN IMMEDIATE` (Q2 §1). A 3.12+ branch must use `autocommit=True` or the attribute setter to pass mypy (I). python.org 3.13/3.14 bundle 3.50.4, inside the WAL-reset range (finding 6). `fullfsync` is off by default (Q3 §1.6). Homebrew/uv unchecked | Same primitives as boardlog today, nothing new per platform (read) | As H2 | As H2 | As H2 | As H2 | As H1, with corruption costing a rebuild rather than data |

**H4's column above is Q1's.** §(b2) revises two of its cells:
- P4 "L across copies" becomes "E by one union rule under three conditions" (b2.b);
- the cost row's "reconciliation inside `_restored` … exemption from `Restore.short`" becomes
  "a link arm that leaves `short` untouched" (b2.c).

The two-windows cell is revised in q1-fit §4.

---

## (b2) H4 walked through: a copy of the link in each member's log

The lead relayed the operator's lean after S2: "Maybe the link record is just duplicated and the
scenario of needing reconciliation is an extreme edge case." This section tests that as a
hypothesis, case by case. Everything here is **read** from the tree. Any claim about code that is
not built yet is marked as a requirement, not as a fact.

**How a copy is written today** (read). `app._record_board`'s observer loops over
`boardfacts.outcomes` and calls `writer.submit(fact.session_id, …)` once per fact, in order.
A link change would yield one `Fact` per member. Two facts are skipped silently:
- a member with **no `board_logs` entry**, meaning it is not running in this process;
- a member whose entry is **not `recording`**.

`BoardWriter` has one worker thread that appends in submission order (`_run`, `_settle`). Each
append fsyncs before it links (P8). A session whose append failed is `Stopped`, and every later
record for it is refused (`_settle`). P12 refuses the first append to a log this process did not
adopt (`_append`).

### (b2.a) When can the copies diverge?

| Case | Code path (read) | How often | What diverges |
|---|---|---|---|
| **Crash between two members' appends** | One worker, appends in sequence. A SIGKILL, power loss or kernel crash after member 1's `os.link` and before member 2's leaves one copy | **Rare.** The window is one append, roughly 10 ms with an fsync per record. That is inferred from Q2 §3, run by Q2: 6.7 records per run, with kills landing 5–120 ms after ready. It recurs once per link change | One log has the change, the other does not |
| **Shutdown with records queued** | `BoardWriter.close` waits `CLOSE_TIMEOUT` (5 s), then reports what is still queued as `Unwritten` | **Rare.** It needs a wedged filesystem | As above |
| **A member's recording stopped this run** | Any failed append makes the session `Stopped`, and every later record is refused. Disk full is one cause; Q2 noted this box's disk is 99% full (relayed from Q2) | **Uncommon**, and it lasts for the rest of the process | Every link change after the stop is missing from that member's log |
| **A member running unrecorded** | `_settle_restore` refuses adoption when `Restore.adoptable` is false. That happens with any stray, unrecognised or contested record (`model.Restore.adoptable`, deliberately strict), a legacy split, or a second window. Then `recording` is false and the observer skips the member | **Not rare in one scenario:** after a newer build writes a kind an older build does not know, every such session is permanently short for that older build (P6, Q1 §0.1 run). Otherwise uncommon | Every link change while unrecorded is missing from that member's log |
| **A member not running at link or unlink time** | No `board_logs` entry, so the fact is skipped. P12 also means H4 *cannot* write to that member's log until it is resumed and adopted. That part is structural | **Ordinary, unless the UI forbids it.** The plan's linking is "select two session cards", and the fleet shows closed cards (`_recall_fleet`). Whether a closed card may be linked or unlinked is not built and not decided | The non-running member's log misses the change |
| **Two windows** | A link exists only in the store of the window that made it (plan: cross-process deferred). Two windows cannot share a link unless one session id is open in both, which is the existing `boardsync.py:66-70` case, and then both write the same log | **Rare,** and the same as today's two-windows case | Q1 §4's H4 walk ("each window runs a different member") assumes a link both windows hold, which the plan does not allow. See the revision in q1-fit §4 |
| **Unlink racing a link** | In one process, the reducer orders the two intents, and one writer thread keeps that order in every log that gets both | **Does not occur** within one process | Only as "unlink missed by one log", which is one of the rows above |

**Verdict on the operator's hypothesis: partly.**
- The failure cases (crash, shutdown, stopped) are rare.
- *Member not running* is ordinary unless the UI forbids linking and unlinking a closed card.
- *Member unrecorded* is ordinary after an older build has run.

So divergence is not an extreme edge case. The next question decides whether that matters: is
reconciliation one rule, or machinery?

### (b2.b) Can reconciliation be one deterministic rule?

The lead's rule, sharpened so that it needs no clock:

> Every link has an **id**, minted by the store when the operator links. A **link record** names
> the id, the member set and the charter. An **unlink record** names the id it ends. The link is
> live in a store when *some restored log* holds its link record and *no restored log* holds an
> unlink for that id. Re-linking mints a new id.

This is a set union with tombstones. It **needs no "later"**: an unlink kills one id for good,
and a later link has a different id. It needs neither wall time (P11: it can step) nor a sequence
across logs (P3: logs have no common order). Checked against b2.a (`inferred` from the read code
paths; nothing was built or run):

- **A copy of a link is missing** (any case): the link is still live if any copy holder's log is
  restored. If none is, the link is absent, which is safe: discovery is the link (plan item 1).
- **A copy of an unlink is missing**, and only a log without the tombstone is restored: the link
  **resurrects** in that store. What does this leak?
  - The peer's messages are filed in the peer's log (Decided 5/6), and so are the peer's
    decisions under H6. Neither is in the store unless the peer's log is restored. That log
    carries the tombstone, and the restore runs before the session says anything
    (`_open_board_log`'s ordering guarantee, read).
  - What remains is the card's channel line, and the surviving member's ability to *send* to a
    peer it was unlinked from. Such a message is held in the sender's log until the peer runs,
    and by then the tombstone is in the store.

  So it is safe **only if delivery checks that the link is live at handover, not at send**. That
  is a requirement on unbuilt code.
- **Widening** (adding a member) is monotone: a `member-added(id, session)` record unions.
  **Removing a member** is a tombstone on `(id, session)`.
- **Not monotone: editing the charter in place, or re-adding a removed member.** Either needs an
  order across logs, which H4 does not have, and a wall-clock tiebreak is P11's hazard. The rule
  holds only if **a charter is immutable per id**: an edit is an unlink plus a new link.

> **Revised (S3; Q5).** Three conditions are not enough. S3-h4-skeptic and Q5's runs add about
> five more: an orphan-tolerant link fold (F1, run), tombstones applied from refused restores and
> "unreadable means not live" (F3), an observer that defers link facts for a member mid-resume
> (Q5 run C), and a charter-edit rule (F6). Condition 1 is a shell-minted uuid, not a store-minted
> id (F7). Condition 3 holds cheaply when handover is the recipient's own read (Q5 run B). See
> [q5-link-home.md](q5-link-home.md) §(b)–(c). The text below is as Q4 wrote it.

**So reconciliation is one rule, with three conditions:**
1. ids minted by the store;
2. the charter is immutable per id;
3. delivery checks liveness at handover.

Outside those conditions it becomes machinery.

### (b2.c) Q1's flagged cost: does a second copy make a member's restore short?

Re-read `store._restored` and `model.Restore`. Today `_restored` places tasks and concerns only.
Its rules are written for ids that one session owns:
- an id another session holds is `OTHER_SESSION_HAS_ID`;
- a store item the log lacks is `STORE_ONLY`, and that refuses the whole restore.

Applied to links unchanged, both rules would fire on every second copy. The restore would be
`short` or refused, so never adoptable. Q1 §3's H4 row read it that way.

That is a consequence of **reusing the task rules**, not of the structure. A link record is a new
kind, and every candidate adds an arm for its new kinds (H2 a channel fold, H6 decision arms). An
H4 link arm that unions copies has nothing to leave unplaced:
- an identical copy is counted as held;
- a copy with a tombstone the store lacks adds the tombstone;
- a link the store holds that this log lacks is the *expected* case, not `STORE_ONLY`.

With that arm, `Restore.short` and `adoptable` are untouched, and the strict reading in
`Restore.adoptable`'s docstring is not loosened. Q1 §6 ("an exemption from `Restore.short` …
touches the most safety-critical predicate") overstates it: the arm keeps links out of
`unplaced`, and the predicate stays as it is.

**What is structural** is narrower:
- P12 means H4 cannot reach a log the process has not adopted, so a member that is not running
  or is unrecorded never gets its copy (b2.a's table).
- P6 means an older build reads the new kinds as `Unrecognised`, which makes every member's
  restore short. Messages already pay that under Decided 5.

So it is a **one-time design cost** of the new arm, plus two structural limits that the rule in
(b) already tolerates.

### (b2.d) H6 for decisions plus H4 for links: is a new root needed?

**No** (read). H4 and H6 both write through the existing `BoardWriter` into
`sessions/<id>/board/` (`sessionfile.board_dir`). Both are restored by the existing `_launch` →
`_open_board_log` → `BoardRestored` path. `boardsync.migrate` lists only the legacy `boards/`
root, and `sessionfile.listing` lists only `sessions/`. With no channel directory and no database
file, J's collision (finding 9) does not arise. It is **moot** for this pairing. The deliverables
exchange (`exchange/<channel>/`, plan) is a separate root that neither function lists. It is
unaffected either way.

---

## (c) Judgement

This section is judgement. It depends on a scope question that the operator's words leave
open, so it is given twice.

**The scope question.** The plan records the operator as: "I think sqlite might be worth
inspecting. If it's rock solid and helps with interesting datastores then why not?" The plan
then scopes this research to "a home for facts no single session owns (the link record, channel
decisions), beside boardlog". That scoping line is the lead's framing. The operator's
"interesting datastores" may mean uses beyond these facts. I do not settle which. Finding 1
depends on the answer.

### Narrow scope: the link record and channel decisions

- **Channel decisions: H6, in the asker's board log.** Finding 2 holds on reading. It needs no
  new root and no new restore trigger. Its failure when the asker is not resumed is fail-safe:
  no approval is visible, so none is acted on.
- **The link record: H4 first, H2 second, H3 third.** This is the main comparison.

  > **Revised (S3; Q5).** The ranking is now **H2(a) > H4 > H3**: a channel log, read and adopted
  > at the migrate slot. The restore trigger costs milliseconds (Q5 run E), and it has no ordering
  > question against `_recall_fleet` (S3 F5). H4's conditions grew and sit in `_restored`'s
  > refusal branch and `_record_board`'s awaiting branch (Q5 runs A and C). The reasoning and what
  > would reverse it are in [q5-link-home.md](q5-link-home.md) §(e). The table and text below are
  > as Q4 wrote them.

| | H4 copy per member | H2 channel log | H3 config file |
|---|---|---|---|
| New root | none (b2.d) | yes, a third root (J) | a file in `config_dir()` |
| Restore trigger | the existing per-session path (b2.d) | **unbuilt**: at startup or at the first member's resume, and the startup order against `_recall_fleet` is unread (Q1 §3, §6) | a startup read (Q1 §3) |
| Divergence | yes, in ordinary cases (b2.a). Resolved by one rule under three conditions (b2.b) | none: one copy | none in one window; the last rename wins across windows (Q1 §4) |
| History (unlink, widen) | as records, P4/P5 kept per log | as records, P1–P10 kept | **lost**: the file is current state (Q1 §2) |
| Older build | every member's log short (P6), as messages already are | only the channel's board short | `sessionfile`'s `FORMAT` pattern (Q1 §2, P6 **E**). An ignored key can change meaning, as with Q2 §5's revoked link that reads as live |
| Size | **M**, mostly the link arm (Q1 §6, revised by b2.c) | **M** (Q1 §6) | **S**, and it pays in P1/P4/P5/P7 |

  **Why H4 leads.** It is the only candidate with no new root and no new restore trigger. The
  restore trigger is the largest unbuilt and unexamined piece in H2. H4 also matches how
  messages and (under H6) decisions already live: in their owner's log, restored with it. Its
  cost is the rule in (b2.b), and that rule is cheap only under three conditions:
  1. store-minted link ids;
  2. a charter immutable per id;
  3. liveness checked at delivery.

  **Why H2 is the fallback.** Its single copy removes divergence, at the price of a new root
  and a new restore trigger.

  **Why H3 is last.** It is acceptable only if the link has no history. Unlink and widen are acts
  the plan names, and the (b2.b) rule needs them recorded.
- **SQLite is not needed yet.** Each thing H1 would add was checked:
  - **Contested detection:** symmetric (G).
  - **Atomicity:** unused while one fact is one record.
  - **Concurrency:** unused in one window (finding 12).
  - **Queries:** nothing on any path may run them (finding 1a).

  What H1 would add is real cost: a second technology, review rules (B, finding 7, finding 8c),
  a version-dependent corruption bug in every build checked (A, finding 6), a copy trap (F), and
  P14 lost. The rock-solid part of SQLite held up in every run: 0 corruptions and 0 partial
  groups in 800 kills of a transactional writer (Q2 §3, C). The cost is in what surrounds it.

**What would reverse the narrow judgement**, each as a check rather than a feeling:
1. **H4 → H2:** the charter has to be editable in place with its history kept, or the delivery
   path cannot check liveness at handover. Either breaks the (b2.b) rule.
2. **H4 → H2:** the UI lets the operator unlink while a member is not running, *and* a
   resurrected channel line, with sends held until the peer resumes, is judged unacceptable even
   though no peer content leaks.
3. **H2 → H4 settled for good:** the H2 restore trigger turns out to need ordering against
   `_recall_fleet` that the tree does not provide (Q1 §6, unread).
4. **Any → H1:** a channel fact that must span **several records atomically** (Q2 §3: boardlog's
   unit is one record, run), or a **consumer of a query** that cannot be a fold over `Snapshot`.
5. **Volume:** whole-reading the link records at restore becomes measurably slow. Nothing
   measured approaches that.
6. The **cross-process channel** being un-deferred. Then a link is held by two stores, and
   H4's "unlink racing a link: does not occur" no longer holds. That would need re-walking.

### Wider scope: SQLite for "interesting datastores" beyond these facts

For H1 to win under a wider scope, **all** of the following would have to be true. None is
established by this round.

1. **A named use that needs SQL.** For example: ad-hoc queries across many sessions' history,
   cross-session search, or analytics over board facts. It would run on a worker thread or in
   an operator tool, never in a reducer (Q1 §5's line, which this round did not contest).
2. **Data that is the store of record there.** Where the data can be derived from boardlog, the
   evidence points to **H7**, SQLite as a rebuildable projection. H7 keeps every boardlog
   property, because the logs stay the record. It turns corruption (A, finding 6) and the copy
   trap (F) into "rebuild the index", and it needs no `facts(kind, body)` trick, since an older
   build simply rebuilds. H1 as the *store of record* would win only for data that has no other
   home and needs SQL to be useful.
3. **The cost list above is paid once and shared.** A second technology is cheaper per use
   with several uses. This cuts towards H1 only when there are several uses, not one.
4. **A floor on the linked SQLite version,** or acceptance of SQLite's stated WAL-reset rate.
   The judgement would have to name which. On this box and on python.org macOS builds there is
   no fixed version today (A, finding 6).

If the operator confirms a wider scope with a named use, the next step is to inspect that use
against H1 and H7. The link record should still not move into SQLite on the strength of
someone else's query.

### Plan question C

Question C (Decided 3 on a lead with several channels: readable on every channel the asker is
on, or only on the one it was raised on; and who is woken). **The judgement does not depend on
it, with one format consequence:**
- Under H6, readability is derived at read time from `Snapshot` (finding 2), so either answer is
  a rule change in `decisions_readable_by`, not a storage change.
- If C chooses "only the channel it was raised on", the decision record must carry the channel
  name **from its first version**. The reason is the one Decided 5 gives: the format only grows,
  and an older build ignores a field. Here an older build would read a channel decision as an
  ordinary one, which is narrower and therefore fail-safe. So the field is cheap to add early
  and safe to add late.
- Who is woken is the wake path's concern (`app._send_wakes`, plan item 2, relayed). It reads
  the store, not the disk, so it is the same under every candidate.

---

## (d) Out of scope: replacing boardlog wholesale

Pointer only, not evaluated. Q1 §7 lists what it would involve: every `Known` kind as rows,
`restore`'s contested logic, `BoardWriter`, `migrate`, the test suite's kind counting, a migration
of every session's directory, and the hand-repair instructions. This round adds three things it
would also inherit (run):
- the close() hazard on `-shm` (B);
- the copy trap (F);
- the linked-version question (A, finding 6) for *every* session's history, not one channel's.

Decided 5 and 6 (messages in the sender's log) would need re-deciding as well.

---

## (e) What remains unsettled

- **The scope question in (c).** It is the operator's to settle. I have not raised it as a
  decision, because the lead holds the round.
- **Finding 7's runtime on 3.12+.** It is still not run. The operator refused the command
  (cmd-20261018T032801Z-025ebc50a02b, no reason given). Only 3.11 is on this box.
- **Finding 5's rollback-mode mechanism.** It is reproduced 6/6. Why the holder's row survives
  a failed `COMMIT` while the other process's acknowledged row disappears was not instrumented.
- **macOS via Homebrew or uv.** Which SQLite each links is unchecked, and so is `fullfsync`
  under WAL on APFS.
- **Power loss, kernel crash, and more than two writers.** None was run, for either side.
- **Whether a read-only connection ever checkpoints** other than at last close. wal.html does not
  say (finding 4).
- **H6's restore order.** When the asker is resumed before the link record is restored, peers
  cannot read the decision until it is (finding 2b). The restore trigger for the link (Q1 §3 (a)
  or (b)) is unbuilt, and whether `_recall_fleet` can be overtaken is unread (Q1 §6).
- **Whether the link has history.** This decides H3 against H2 (Q1 §6).
- **H4's three conditions (b2.b)** are product and design choices nobody has made:
  - whether a closed card may be linked or unlinked;
  - whether a charter is immutable per link id;
  - whether delivery checks liveness at handover.
- **The probe re-implements boardlog's append** and does not import it (Q2 §3). The G and C
  file-side results are for that re-implementation.
