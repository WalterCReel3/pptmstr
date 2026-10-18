# Q2: what this interpreter's sqlite3 does, measured

**Dated:** 2026-10-17 · **Part of:** the SQLite inspection (Q1–Q3) for the facts no single
session owns ([two leads share a channel](../2026-10-17-two-leads-in-one-window-share-a-channel.md)).
**Script:** [`q2_probe.py`](q2_probe.py), stdlib only, everything under a fresh `$TMPDIR` directory
that it removes. **Reproduce:**

    cd planning/2026-10-17-sqlite-inspection
    /home/wreel/Source/pptmstr/.venv/bin/python q2_probe.py --mypy /home/wreel/Source/pptmstr/.venv/bin/mypy

Q4 added the skeptic's runs as sections Q4-B to Q4-J (S2's needs-a-run list), about 255 s.
`--tree` is the pptmstr tree that Q4-J imports:

    PYTHONDONTWRITEBYTECODE=1 /home/wreel/Source/pptmstr/.venv/bin/python q2_probe.py --q4 \
        --mypy /home/wreel/Source/pptmstr/.venv/bin/mypy --tree ../..

Tags: **run** = printed by the probe (two full runs, 172 s and 176 s; the numbers below are the
second, after `black`. The first run agreed on every zero and every 500, and differed on the
rollback-mode counts: deferred had 8 errors, not 28, and `busy_timeout=0` split 494/506, not 0/500). **read** = from a
file in this tree. **recalled** = from memory, not checked here.

## The answer in five lines

1. A transaction opened with `BEGIN IMMEDIATE` on a connection with `isolation_level=None` was
   all-or-nothing under 400 SIGKILLs (WAL and rollback journal). `integrity_check` was clean every time,
   and no acknowledged group was ever lost (run).
2. Every way of getting that wrong is **silent**: Python's default transaction control lost 500 of
   1000 updates and raised nothing, `with conn:` under `isolation_level=None` is not a transaction, and
   `executescript` commits whatever is pending (run).
   **Revised (S2 finding 11; Q4).** "Every way" overstates it. `BEGIN DEFERRED` in WAL raised 500
   `database is locked` errors (§2), which is loud. The silent ones are the three named here.
3. `busy_timeout` does not make two writers safe on its own. With `BEGIN DEFERRED` in WAL the loser
   fails at once, and `busy_timeout` is never consulted. With `BEGIN IMMEDIATE`, one writer still waited
   3.3 s, and once waited 5 s and failed, while the other's transactions took about 12 ms (run).
   **Revised (S2 findings 9a, 11, 12; Q4).** This is a worst case, from back-to-back
   transactions with no pause. With 100 ms of think time between transactions, two processes
   ran 1000/1000 in both modes, with no error and a longest wait of 45 ms (run, `--q4`
   section E). The tail is not a cross-process effect: two *threads* in one process, each with
   its own connection, showed the same pattern, 3.4 s in WAL and one 5 s failure in rollback
   (run, section D).
4. An older reader keeps working against a grown schema and **misses things without saying so**,
   unless it checks `PRAGMA user_version` itself. That is the opposite of boardlog's `Unrecognised`,
   which makes the board `short` without being asked (run).
5. There is no `sqlite3` CLI here and no `python -m sqlite3` on 3.11. `Connection.iterdump()` is the
   stdlib dump. A WAL database is not one file: copying the `.db` alone lost every row (run).
   **Revised (S2 finding 8b; Q4).** That was measured with `wal_autocheckpoint=0`. With the
   default autocheckpoint (run, section F, 100 one-row commits), a copy of the `.db` alone:
   - with the writer open: 0 rows;
   - after a SIGKILL, with the `-wal` left behind: 0 rows;
   - after a clean close: all 100. The last connection's close checkpointed and removed the
     `-wal`.

   So the trap is a copy taken while pptmstr runs (a backup tool or `cp -r`), or after a crash
   without the `-wal`.

## Where it ran

- **Filesystem (run):** `$TMPDIR` (`/tmp/claude-1000`), `~/.config/pptmstr` and this worktree are
  all **ext4 on the same device** (`/dev/mapper/oliver--vg-root`). So the fsync and locking behaviour
  measured here is what `~/.config/pptmstr` would get on this machine. The disk is 99% full (8.5 GB
  free), which did not stop any run.
- **Not covered:** macOS APFS, where whether `fsync` reaches the platter depends on
  `F_FULLFSYNC` / `PRAGMA fullfsync` (recalled); network filesystems, where WAL's shared memory is
  documented as unsupported (recalled); tmpfs.
- **SIGKILL is a process crash, not a power loss.** The page cache survives it. Section 3 shows what
  survives a killed writer. It says nothing about what an `fsync` guarantees after a power cut, for
  either SQLite or boardlog.

## 1. Versions

| | value | how |
|---|---|---|
| this tree's interpreter | CPython 3.11.2, `/home/wreel/Source/pptmstr/.venv/bin/python` | run |
| SQLite library | 3.40.1, `THREADSAFE=1`, `sqlite3.threadsafety` 3, FTS3/4/5 compiled in | run |
| defaults on a new file | `journal_mode=delete`, `synchronous=2` (FULL, also for WAL), `busy_timeout` 5000 ms | run |
| *Revised (S2 finding 11; Q4)* | The 5000 ms is Python's `connect(timeout=5.0)`, not SQLite's. SQLite's own default is no busy handler: "The default busy callback is NULL" (busy_handler.html, fetched by Q3) | fetched |
| other Pythons on this box | only `/usr/bin/python3.11` | run (`ls`) |
| project floor | `requires-python = ">=3.11"`, `[tool.mypy] python_version = "3.11"` (Debian 12's system Python) | read, `pyproject.toml` |
| release / mac install | CPython 3.11 to 3.14 on Linux x86-64 and macOS 14+ arm64. `bootstrap.sh` takes the first of `python3.14 … python3.11 python3`, or `--python PATH` | read, mac-install plan §7.3, `bootstrap.sh` |

So the floor is 3.11, while a Mac install may run on 3.14, with whatever SQLite that build bundles. That
version was **not measured**. Only 3.11 / 3.40.1 ran here.

**What exists on 3.11 (run):** `isolation_level=None`, explicit `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`
through `execute`, `in_transaction`, `set_trace_callback`, `backup`, `iterdump`, and `PRAGMA
user_version`. Everything the probe uses passes **`mypy --python-version 3.11 --strict`** (exit 0).
That is the check that was made: typeshed's 3.11 stubs, not a run on another interpreter.
`Connection.autocommit` and `sqlite3.LEGACY_TRANSACTION_CONTROL` are absent at runtime. The same
mypy rejects `sqlite3.connect(..., autocommit=False)` with `Unexpected keyword argument
"autocommit"`, so the 3.12 PEP 249-conformant switch cannot be used at the floor.

**Revised (G2; Q2-selfcheck-fix).** The exit 0 above predates Q4-J. Q4-J's `import pptmstr`
made the same check exit 1 with `import-untyped` (run): pptmstr has no `py.typed`, and the venv's
editable install points at `/home/wreel/Source/pptmstr`, not this tree. The probe's own check now
puts this tree on `MYPYPATH`, so mypy analyses this tree's pptmstr as source. It also passes
`--no-warn-unused-ignores`, which is `pyproject.toml`'s setting: without it, `--strict` reports two
of pptmstr's own ignores in `drawable.py` (run). With both, section 1 prints exit 0 again (run,
2026-10-18, section 1 alone). The 3.12-only snippet's check is unchanged and still exits 1 with the
same `call-overload` errors. No measured value changed.

**A typing snag found on the way (run, first run):** typeshed types `isolation_level` as
`Literal['DEFERRED', 'EXCLUSIVE', 'IMMEDIATE'] | None`. Passing `""`, the runtime default, fails
`--strict` with `arg-type`. Code that wants the legacy mode has to omit the argument rather than
spell it.

## 2. Two writer processes, one database

Two OS processes started together, 500 transactions each. Each transaction reads a counter, inserts
a row, and writes `counter = read + 1` computed in Python. So a transaction that is not isolated loses
an update and raises nothing. "Lost writes" = acknowledged commits missing afterwards. "Lost updates" =
rows minus counter.

| journal | how the transaction opens | busy_timeout | acked / 1000 | lost writes | lost updates | `database is locked` | longest wait |
|---|---|---|---|---|---|---|---|
| WAL | `BEGIN IMMEDIATE` | 5000 | 1000 | 0 | 0 | 0 | 3334 ms in BEGIN (p99 0.04) |
| rollback | `BEGIN IMMEDIATE` | 5000 | 999 | 0 | 0 | **1, after waiting 5006 ms** | 1831 ms in BEGIN (p99 0.08) |
| WAL | `BEGIN DEFERRED` | 5000 | 500 ([500, 0]) | 0 | 0 | 500, each within 0.5 ms | none |
| rollback | `BEGIN DEFERRED` | 5000 | 972 | 0 | 0 | 28 (one after 5006 ms) | — |
| WAL | Python default, no BEGIN | 5000 | 1000 | 0 | **500** | 0 | 2637 ms |
| WAL | `BEGIN IMMEDIATE` | 0 | 500 ([500, 0]) | 0 | 0 | 500 | — |
| rollback | `BEGIN IMMEDIATE` | 0 | 500 ([0, 500]) | 0 | 0 | 500 | — |

Commit latency p50 was about 5–7 ms in WAL and about 12 ms in rollback mode, all with `synchronous=FULL`
(run).

What it says:
- **No acknowledged write was ever lost** in any configuration (run, both runs).
- **`BEGIN IMMEDIATE` plus a busy timeout is the only setup that was correct and mostly error-free.**
  "Mostly" matters: in rollback mode, one transaction in 1000 waited the whole 5 s and failed, in both
  runs. Busy waiters lost the lock to a writer looping without pause. The tail in WAL was 3.3 s. The
  mechanism is SQLite's busy handler sleeping on a back-off schedule while the holder re-takes the lock
  (recalled, not instrumented). A caller still has to handle `database is locked`, and must not be the
  UI thread.
- **Deferred + WAL + read-then-write**: the second writer's upgrade fails immediately every time. Its
  500 failures took at most 0.5 ms each, so `busy_timeout` was not consulted (run). Retrying in a loop
  would be the caller's job.
- **Python's default transaction control silently lost half the updates** (run, 500 in both runs).
  The SELECT ran outside any transaction (section 4 shows no BEGIN is sent before a SELECT), so two
  writers read the same value.
- The `busy_timeout=0` rows split 500/0 because the loser burned through its 500 attempts in
  microseconds while the winner held the lock. They show the error appears without a timeout. They
  are not a fairness measurement.

The operator's constraint is that both teams usually sit in **one process**. That makes the
two-process case the two-windows case: rarer, but it is the case boardlog was built for (read,
`boardlog.py` module docstring). Threads in one process were not measured.

## 3. SIGKILL in the middle of a multi-row write

200 kills per variant. Each kill came 5–120 ms after the writer reported ready, from the same seeded
schedule for every variant. After each kill the database or directory was reopened and checked. A
group is 20 rows (SQLite) or 20 records (files). The SQLite writer sleeps 1 ms per row, so kills land
inside the open transaction and not only in COMMIT.

| variant | where kills landed | integrity_check bad | checks showing a partial group | acked groups lost | other |
|---|---|---|---|---|---|
| SQLite WAL, one `BEGIN IMMEDIATE` per group | 141 in group, 59 in COMMIT | 0 | **0 / 200** | 0 | a kill in COMMIT left the group whole 48×, absent 11× |
| SQLite rollback, one `BEGIN IMMEDIATE` per group | 113 in group, 87 in COMMIT | 0 | **0 / 200** | 0 | a kill in COMMIT left it absent 87×; a non-empty `-journal` was present after every kill, and the reopened database never showed its rows (the rollback is inferred from that) |
| SQLite WAL, autocommit per INSERT (wrong setup) | 200 in group | 0 | **200 / 200** | 0 | |
| files: tmp + fsync + `os.link` per record (boardlog's mechanism, re-implemented in the probe) | 200 in group | — | **200 / 200** | 0 (vacuous: see below) | torn record files 0; `.record-*.tmp` left behind 170 |

- **A SQLite transaction is the unit that is atomic, and only when one is actually open** (run). The
  autocommit variant is the same library, and it showed a partial group after every kill.
- **boardlog's unit is the record** (run: zero torn files in 200 kills). A fact that spans several
  records is not atomic. That is no defect while one fact is one record, which is how boardlog is
  written. It does matter if a channel fact like "link established + first decision" were ever split
  into two records.
- The file writer never finished a 20-record group within 120 ms (6.7 records per run, each with an
  fsync), so "acked groups lost 0" proves nothing for files. The partial-group count is the result.
- **Revised (S2 finding 11; Q4).** Checkpoints were almost never under a kill here: the default
  autocheckpoint is 1000 pages and a group is about two. Section C of `--q4` put them there,
  with 200 kills per variant in WAL (run):
  - `wal_autocheckpoint=1`: 84 kills landed in COMMIT, which then also checkpoints, and 116
    inside the group;
  - an explicit `wal_checkpoint(FULL)` after every group, bracketed in the log: 41 kills
    landed inside the checkpoint.

  Both variants had 0 `integrity_check` failures, 0 partial groups and 0 acknowledged groups
  lost. Every group killed inside its checkpoint was present whole. With 0 in 200, the
  per-kill rate is below about 1.5% at 95% confidence. That is a bound, not a proof.
- The 170 leftover temp files match `boardlog.append`'s docstring: a killed writer leaves its temp
  file, and nothing removes it (read + run).
- None of this was compared to the real `boardlog.append`. The probe re-implements its steps (list,
  mkstemp, write, fsync, link, move up on `FileExistsError`, unlink) so that it stays stdlib-only and
  imports nothing from `pptmstr/`.

## 4. Python's transaction control on 3.11

All run, with statements captured by `set_trace_callback`:

- Default `isolation_level` is `''`. A **SELECT sends no BEGIN** (`in_transaction=False`). An
  **INSERT is preceded by an implicit `BEGIN `** (deferred). `CREATE TABLE` sends no BEGIN and leaves
  no transaction open.
- Two INSERTs and then an exception; how many rows another connection sees:

| setup | rows visible | atomic? |
|---|---|---|
| default `isolation_level`, `with conn:` | 0 | yes |
| `isolation_level=None`, `with conn:` | 2 | **no**: `with` only commits or rolls back; it never begins |
| `isolation_level=None`, explicit `BEGIN IMMEDIATE` … `ROLLBACK` | 0 | yes |
| default `isolation_level`, `with conn:` around `executescript` | 2 | **no**: `executescript` committed the pending INSERT first |

The default mode is atomic for a write-only body, and section 2 shows it is not isolated for
read-then-write. The setup that was both atomic and isolated is `isolation_level=None` with an
explicit `BEGIN IMMEDIATE`, wrapped in your own context manager.

## 5. Schema growth against an older reader

A v1 build made `links(id, a, b)`. A v2 build added `revoked_at` and a `decisions` table, and set
`user_version = 2`. Then v1 code ran against it (run):

- An explicit-column SELECT kept working, and **reported the revoked link L2 as live**. Nothing raised.
- `SELECT *` into a 3-tuple failed with `ValueError: too many values to unpack`.
- The `decisions` table is invisible to v1, and nothing says so. **`PRAGMA user_version` read 2
  was the only signal**, and v1 sees it only if it was written to look.
- A v1 positional `INSERT … VALUES (?,?,?)` failed (`table links has 4 columns but 3 values`). A
  named-column INSERT worked and stored `revoked_at = NULL`. A v1 writer can therefore create a
  link a v2 reader takes as never revoked, which is the intended default here. It would not be if
  the new column narrowed meaning.

Compared with boardlog (read, `boardlog.py` docstring: "an older build ignores the key; an unknown
kind is `Unrecognised` and makes the board short"): the **column** case matches "ignores the key",
including the risk that an ignored field changes meaning. The **table** case does not match
`Unrecognised`. SQLite has no record a reader is forced to walk past, so a new kind of fact in a new
table is silently absent, not counted as short. H1 would need the equivalent built by hand: a version
check on open, or a single `facts(kind, body)` table whose unknown kinds the reader counts, which is
boardlog's format inside SQLite.

## 6. Inspectability

- `sqlite3` CLI: **not installed** (run, `shutil.which`). `python -m sqlite3` does not exist on 3.11
  (run, no `sqlite3.__main__`; recalled: added in 3.12).
  **Revised (S2 finding 14; Q4).** No longer recalled. What's New in Python 3.12, under sqlite3:
  "Add a command-line interface" (fetched, docs.python.org/3/whatsnew/3.12.html).
- The stdlib way: `Connection.iterdump()` yields SQL text (run: the schema-growth database dumped
  as 8 statements). One line:
  `python -c "import sqlite3,sys; print('\n'.join(sqlite3.connect(sys.argv[1]).iterdump()))" DB`
- **A WAL database is three files.** With 100 committed rows not yet checkpointed and the writer
  open, a copy of the `.db` file alone had **no table at all**, while `Connection.backup()` had 100
  rows (run). Anyone who copies or attaches "the database" by hand loses committed facts. boardlog's
  directory has no such trap: `cat` and `cp -r` see what was written.

## What was not run

- Any Python but 3.11.2, and any SQLite but 3.40.1. The 3.12+ `autocommit` API was only shown to be
  absent at the floor. macOS, APFS, network filesystems, tmpfs.
- Power loss or a kernel crash: only SIGKILL. Durability across a machine failure is unmeasured for
  both candidates.
- Threads, or several connections in one process, which is the operator's usual layout. Only
  separate processes ran.
  **Revised (S2 finding 12; Q4).** Now run (`--q4` section D):
  - two writer threads, each with its own connection: 0 lost writes and 0 lost updates in both
    modes;
  - a writer thread with a whole-table reader thread: no read ever saw a partial group (13468
    reads in WAL, 205 in rollback, where the writer blocks the reader).
- More than two writers, and writers with a pause between transactions. The starvation tail was
  measured only under back-to-back contention.
  **Revised (S2 finding 11; Q4).** The pause is now run: see the revision under "five lines",
  item 3. More than two writers is still not run.
- The real `pptmstr.boardlog.append`: it was re-implemented, not imported.
- The 5 s busy failure in rollback mode appeared once in each of two runs. Two occurrences say it
  happens. They do not give its rate.
