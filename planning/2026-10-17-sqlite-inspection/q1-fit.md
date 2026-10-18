# Q1: where facts no session owns could live, held to boardlog's recorded properties

**Dated:** 2026-10-17 · **Task:** Q1-fit · **Tree:** worktree `feat-inter-team-linking` at
`e57b027`, plus the staged planning files · **Status:** research, no recommendation.

**Tags.** `[read]` I read it in the tree. `[run]` a scratch script under `$TMPDIR` produced it
(§0.1 has the script and its output). `[recalled]` from memory, carries no weight, and is
Q2's or Q3's to measure or source. `[inferred]` is my reasoning from `[read]` facts.
Citations are `file:line` at `e57b027` with the symbol name, because line numbers drift.

**Scope.** This file is about fit with this code only. SQLite's own behaviour (locking, WAL,
durability settings, crash behaviour, the 3.11 floor, macOS) belongs to Q2 (measured) and Q3
(sourced). Where a cell below depends on that behaviour, it says "Q2/Q3" and is not settled
here.

---

## 0. Findings that bear on the premises

Three things the spec or plan takes as given that the tree qualifies. None of them favours
one candidate.

1. **H2's suggested path `boards/channels/<name>/` would be moved by the legacy migration
   on the next start** `[run]`. `boards/` is `boardsync.legacy_root()` (`boardsync.py:167-174`),
   and `boardsync.migrate` renames *every* entry under it whose name passes the session-id
   rule into `sessions/<name>/board` and leaves a symlink behind (`boardsync.py:394-454`,
   `_carry`). `app.main` runs it on every start with recording on (`app.py:5127`). In the
   probe, `channels` was carried as a session id and the channel log ended up at
   `sessions/channels/board/brand-ui/00000000.json`. `sessions/` is no better: it is the
   fleet's root, one directory per session id (`sessionfile.py:12-17`, `sessionfile.root`).
   So whatever on-disk home H1, H2, H3 or H5 takes needs a **third root** under
   `settings.config_dir()`, beside `boards/`, `briefs/` and `sessions/`, not inside either
   of the first or the last. This changes H2's path. It does not change H2's argument.

   **Revised (S2 finding 9; Q4).** Files are affected as well as directories (run,
   `q2_probe.py --q4`, section Q4-J, against this tree's `boardsync` and `sessionfile` with
   both roots passed explicitly):
   - `channels.db`, `channels.db-wal` and `channels.db-shm` under `boards/` each pass the
     session-id rule. `migrate` returns `Left(…, "it is not a directory")` for each, and
     `app._migrate_board_logs` turns every `Left` into a WARN at each recorded start saying a
     board log "was left at the old path" (`app.py`, `_migrate_board_logs`, read).
   - Under `sessions/`, `sessionfile.listing` skips a *file* `channels.db`, and lists a
     *directory* `channels/` as a session.

   So H1's file is under the same rule: it goes in `config_dir()` itself or in a third root, and
   not under `boards/` or `sessions/`.

2. **A channel decision has an owner in the tree as it stands: its asker** `[read]`.
   `Decision.asker` is the node the gate authenticated, and `Decision.session_id` returns
   `asker[0]` (`model.py:594-649`). The spec groups channel decisions with the link record as
   facts that "belong to NO single session". That is true of *who may read one* (Decided 3).
   It is not true of *who raised it*. Messages have the same shape: one writer, several
   readers, and Decided 5/6 filed them in the writer's log. So "file a channel decision in
   the asker's log, like a message" is a candidate the spec's framing leaves out. I add it
   as **H6** (§2). It covers decisions only. The link record is the operator's act (plan,
   proposal item 1) and has no asker, so H6 does not reach it.

3. **No decision is logged today, under any candidate** `[read]`, which R1 §0 fact 6 already
   recorded. `boardfacts.outcomes` returns `()` for all five decision intents
   (`boardfacts.py:164-175`), and `model.Decision`'s docstring says "Not in the board log. A
   decision does not survive a restart of pptmstr" (`model.py:629`). `boardlog`'s docstring
   names "a decision raised and answered" as an expected kind not yet defined
   (`boardlog.py:67-71`). Every candidate therefore pays for a decision record and its
   restore. The candidates differ only in where those go.

### 0.1 The probe behind finding 1 and P6

`$TMPDIR/q1/probe.py` (scratch, not kept in the tree). It was run with the main checkout's
venv (`/home/wreel/Source/pptmstr/.venv/bin/python -I`), with `PYTHONDONTWRITEBYTECODE=1`
and this worktree on `sys.path`. Output, verbatim apart from the temp prefix:

```
append to channel dir: Written(ordinal=0, path=.../boards/channels/brand-ui/00000000.json, gap=None)
migrate: Moved Moved(session_id='channels', source=.../boards/channels, target=.../sessions/channels/board, unlinked=None)
legacy now: ['channels']
sessions now: ['channels', 'channels/board', 'channels/board/brand-ui', 'channels/board/brand-ui/00000000.json']
unknown kind -> unrecognised: ['channel-linked'] short: True
```

The second half writes a record of kind `channel-linked` into a session log that this build
does not define. `restore_log` carries it as `Unrecognised` and the board is `short`. This is
the P6 behaviour on which H4's older-build cost (§2) depends.

---

## 1. The properties the log promises

Taken from `boardlog`'s module docstring and from `brief.py`'s argument, which it cites. Two
properties come from `boardsync` (P12, P13) and are marked as such. They are what the
*wiring* promises around the log, and every candidate needs an answer to them too. Each was
checked against the code it describes `[read]`.

| # | Property | Where stated | Where enforced |
|---|---|---|---|
| P1 | **An append never overwrites a record, whoever else is writing.** `os.link` refuses a name that exists, atomically. The loser takes the next ordinal. (`brief.write_entry`'s `exists()`-then-`os.replace` is named as the check-then-act this rules out.) | `boardlog.py:15-26`; `brief.py:16-22` | `boardlog.append`, `boardlog.py:780-786`; contrast `brief.py:220-235` |
| P2 | **A record is whole or absent.** It is written to a temp file and linked complete. The newline terminator means no proper prefix decodes, so a torn file is told from a whole one. A write over `PIPE_BUF` is not atomic, so one growing file is ruled out. | `boardlog.py:15-17`, `491-505`; `brief.py:16-22` | `boardlog.encode`/`decode`, `boardlog.py:506-526`; `append`, `boardlog.py:773-789` |
| P3 | **What is read is a consistent prefix.** The directory listing is the prefix. A read stops at the first missing or undecodable ordinal and applies nothing after it. An append never fills a gap. | `boardlog.py:16-17`, `23-26`; `brief.py:21-22` | `boardlog.read`, `boardlog.py:846-888`; `append`, `boardlog.py:736-744`, `770-772` |
| P4 | **Two writers lose no record, and their records are not one history.** A record no single reducer could have appended marks its task or concern `contested`. That record and every later one about the same thing are kept unapplied. *Stated limit:* interleavings that are each allowed in turn are not detected ("needs the writer in the record"). | `boardlog.py:28-36` | `boardlog.restore`, `boardlog.py:1161-1180`; `_one_writers`, `boardlog.py:1343-1386` |
| P5 | **The log holds applied outcomes, not requests, and a restore is a fold over facts that cannot be refused.** It is pure and total. | `boardlog.py:4-13` | `boardlog.restore`, `boardlog.py:1161-1167`; `boardfacts.outcomes` derives records from `(before, intent, after)`, `boardfacts.py:136-143` |
| P6 | **The format only grows.** A field is added with a default that claims nothing, and a fact is added as a new kind. An older build reads an unknown kind as `Unrecognised`, which makes the board `short`, and ignores an unknown key. | `boardlog.py:58-61`, `432-449` | `decode`, `boardlog.py:543-556`; `Board.short`, `boardlog.py:1129-1147`; §0.1 `[run]` |
| P7 | **A log says when it is only a prefix.** `RecordingStopped` is the writer's statement about the log, not a board fact, and makes the board `short`. *Stated limit:* a stop that also refuses the marker leaves nothing behind. | `boardlog.py:73-77`, `403-424`; `boardsync.py:87-121` | `BoardWriter._mark`, `boardsync.py:693-708` |
| P8 | **Durable before named.** `fsync` runs before the link, and the directory entry is not flushed: losing the newest names leaves a consistent prefix. | `boardlog.py:749-755` | `append`, `boardlog.py:775-779` |
| P9 | **A read is bounded, never raises and never waits.** `MAX_RECORD_BYTES`, non-blocking open, regular files only. | `boardlog.py:128-133`, `819-830`, `863-865` | `_file_bytes`, `boardlog.py:831-843` |
| P10 | **The key is the session id, and the directory is a parameter.** An id that is not exactly one directory name gets no directory. | `boardlog.py:38-41`, `600-615` | `session_dir`, `boardlog.py:616-629` |
| P11 | **Nothing is in the snapshot, and nothing reads a clock.** IO is the shell's, `restore` is pure, and times are wall-clock on disk and rebuilt as monotonic per restore. | `boardlog.py:94-96`, `1472-1486` | `Clock`, `boardlog.py:1491-1518` |
| P12 | *(boardsync)* **Nothing is appended behind a history the store does not hold.** The first record goes only to a log that was `adopt`ed or is empty. A short restore is never adoptable. | `boardsync.py:123-138`; `model.py:2627-2640` | `BoardWriter._append`, `boardsync.py:710-745`; `app._settle_restore`, `app.py:1347-1376` |
| P13 | *(boardsync)* **One writer thread per process, records in submission order, never on the draw thread.** *Stated limit:* the guarantee ends at the process. | `boardsync.py:58-70` | `BoardWriter.submit`/`_run`, `boardsync.py:557-573`, `650-669` |
| P14 | *(boardsync, operator)* **The log can be repaired by hand with pptmstr closed.** Each record is one small JSON file, numbered. Nothing in pptmstr deletes a log. | `boardsync.py:44-56`; `brief.py:53-56` | — (a property of the format) |

---

## 2. Each candidate against each property

Legend: **K** keeps the property as it is, because the candidate uses `boardlog` itself or its
primitive. **E** replaces it with an equivalent the candidate must supply, and the cell names
what that takes. **L** loses it. **n/a** means it does not apply. "Q2/Q3" means the cell depends
on SQLite behaviour this file does not settle.

Candidates as specified, plus two I added:
- **H1** SQLite file beside boardlog, for unowned facts only.
- **H2** a channel-keyed log in boardlog's format, with its own restore. Its path is moved to a
  third root by §0 finding 1, e.g. `config_dir()/channels/<name>/`.
- **H3** operator configuration: a `channels.json`-like file for the link, decisions elsewhere.
- **H4** a copy of the link in each member's session log, with a reconciliation rule.
- **H5** *(added)* `requests.py`'s shape: under a channel root, one directory per party, each
  party writing only its own, and state derived on read across all of them
  (`requests.py:3-20`). This is the tree's existing answer to "records several parties write
  and several read". It sits between H2 and H4.
- **H6** *(added, decisions only)* a channel decision filed in its asker's own board log, as
  Decided 5 files a message. It is restored when the asker resumes, and every member reads it
  once it is in the store. It has to be paired with one of H1–H5 for the link record.

H3 has no decisions half of its own: "decisions elsewhere" means one of the other columns. Its
column below is for the link only.

| | H1 SQLite | H2 channel log | H3 config file (link) | H4 copy per member | H5 per-party dirs | H6 decisions in asker's log |
|---|---|---|---|---|---|---|
| P1 never overwrites | **E**, by transaction. Only if the schema is append-only (insert facts). A current-state table with `UPDATE` is last-writer-wins, which is **L** for history. Q2/Q3 for atomicity. | **K** | **L**. One file is rewritten whole. `settings.save` and `sessionfile.update` are rename-over with last-writer-wins between windows (`settings.py:495-532`; `sessionfile.py:42-51`) | **K** (each copy is a boardlog record) | **K**: `os.link` per party (`requests.py:22-30`), and no two parties share a name | **K** |
| P2 whole or absent | **E**, by transaction. Q2/Q3 | **K** | **E**: temp file then rename, as `settings.save` does | **K** | **K** | **K** |
| P3 consistent prefix | **E**: a read transaction is a snapshot. A gap cannot occur, so "stop at the first gap" has no analogue. Q2/Q3 | **K** | **n/a**: one document, no sequence | **K** per log. *Across* members' logs there is no common prefix (§4) | **K** per party, not across parties (`requests.py` derives per-party state and does not order across parties) | **K** |
| P4 two writers, contested marked | **E only if built**: an append-only facts table plus a fold that asks `_one_writers`-style questions keeps it. Constraints in SQL that refuse an impossible fact amount to a *second reducer*, which `boardlog.restore` explicitly avoids being (`boardlog.py:1177-1180`). A state table loses it: **L** | **K** for records about one channel, provided the channel fold asks the guards. New guards are needed for the link: who may widen the set, and which close is final | **L**: the later rename wins whole and nothing marks it (`sessionfile.py:42-49` states this for session files) | **L** across copies: two copies that disagree are two histories, and no record says which is current. The reconciliation rule *is* the replacement, so **E** at best, and only as good as its tiebreak | **K** within a party. Contest across parties becomes derive-time conflict (`requests.py:49-56`: acts carry the digest of what they were made against, so a stale act is reported `STALE`). **E** | **K**: one asker, one log, and the guards are the decision arms' (`store.py`, `DecisionRaised` onward) |
| P5 outcomes, fold over facts | **E** with append-only facts. **L** with a state table, which stores the result and not the facts (STYLE §1 "Derive; do not store", `STYLE.md:55-69`) | **K** | **L** for history: the file *is* current state. Acceptable only if the link has no history worth folding, which is not established (who widened the set, and when, is history) | **K** per copy | **K** | **K** |
| P6 format only grows; unknown → Unrecognised → short | **E only if designed in**. A `(kind, json)` facts table carries it over unchanged. A table per kind does not: an older build simply never selects the new table, so the fact is invisible *and nothing is marked short*. That is **L** unless the build checks a schema version and treats newer as short. The "older build" for H1 also includes every build before H1, which never opens the file at all. That is harmless, because nothing else depends on the file. Q2/Q3 for `user_version` | **K**. Unknown kinds land in the *channel's* board, not a session's | **E**: `sessionfile`'s pattern (`FORMAT`, unknown keys kept, a newer format never written; `sessionfile.py:19-25`) | **K mechanically, with a cost**: an older build reading a member's log meets the link kind as `Unrecognised`, so that **member's session restore is short and never adoptable** (`model.py:2627-2640`; §0.1 `[run]`). An older build then runs that lead unrecorded for good. Messages under Decided 5 already pay this, so H4 adds no *new* member to the set of affected logs, only more kinds in them | **K** | **K**, with the same older-build cost as H4 but only in askers' logs, which Decided 5 already exposes for messages |
| P7 says when it is a prefix | **E**: a "stopped" row is needed. Its stated limit is the same: a stop that also refuses the marker leaves nothing | **K** (`RecordingStopped` and `BoardWriter._mark` reused) | **L**: a file that failed to save is the previous file, and nothing says so. `settings.save` swallows write failures (`settings.py:523-524`, and the `except OSError: pass` that ends it) | **K** | **E**: `requests.py` has no stop marker (`[read]`, `requests.py:3-80`, grepped) | **K** |
| P8 durable before named | **E**, Q2/Q3: depends on the synchronous and journal settings | **K** | **E**: `sessionfile.update` flushes, `settings.save` does not (no `fsync` before its `os.replace`, `settings.py:529-539`) | **K** | **K** if `durable`-style flushing is used. Not checked in `requests.py` | **K** |
| P9 bounded, non-raising, non-waiting read | **E**, partly Q2/Q3. A reader *can* wait (busy timeout, lock) by design, which is a different contract from P9's "never waits" | **K** | **E** (`sessionfile._file_bytes`, `sessionfile.py:402`) | **K** | **K** (`requests.py:58-63`) | **K** |
| P10 keyed, path validated | **E**: the key is a column, and no path is built from it. Path traversal is not a concern | **K**: `boardlog.session_dir` is root-generic and validates any name (`boardlog.py:600-629`), so it serves channel names as written | **n/a** | **K** | **K** (`requests.address_refusal`, `requests.py:460`) | **K** |
| P11 pure core, wall clock on disk | **K if wired as §5 says** | **K** | **K** | **K** | **K** | **K** |
| P12 adopt before append | **E, needed**: a channel-scoped "the store holds what the file says" gate. Without it, a reducer that never loaded the channel writes behind it, which for a state table is a silent overwrite | **E, needed**: the same gate keyed by channel. `BoardWriter.adopt` is keyed by session id (`boardsync.py:582-597`) | **E, needed** for the same reason | **K** per member log, with an asymmetry: a member not running in this process has no `board_logs` entry, so the observer drops its copy (`app.py:1320-1322`). Its copy goes stale. That is §4's problem, not P12's | **E** | **K**: it is the asker's session gate, unchanged |
| P13 one writer thread, order | **E**: a new writer thread, or a connection owned by one thread. Whether `sqlite3` connections may cross threads is Q2/Q3 `[recalled: check_same_thread defaults on]` | **K** if the same `BoardWriter` is reused with a channel-keyed directory. **E** if a second writer is used. Order across the two logs (session and channel) is not one order either way | **E** | **K** | **E** | **K** |
| P14 hand-repairable | **L in the form P14 states**. No `sqlite3` CLI is on this machine's `PATH` (`command -v sqlite3` printed nothing, `[run]`). Inspection needs Python or an install. "Move the other directory out of both roots" (`boardsync.py:44-51`) has no one-file analogue for rows | **K** | **K**: it is meant for an operator to read | **K** | **K** | **K** |

**Revised (S2 finding 10; Q4).** The P4 row marks the two candidates unevenly. H2's cell reads
**K** "provided the channel fold asks the guards", and H1's reads "E only if built". Both need the
same new work: a channel fold, plus the link guards (who may widen the set, which close is
final). P1–P3 and P5–P10 are inherited from `boardlog` by H2. P4 is not, because
`boardlog.restore` folds tasks and concerns only (§3, H2 row). The fair reading of P4 is **E,
by a new fold**, for both H1 (with a facts table) and H2. The row's cells are left as written above.

**What the table says, without ranking.** H2, H4 and H6 keep P1–P10 by construction, because
they *are* boardlog. Their costs sit elsewhere: H2 in P12 and in a new restore path, H4 in
reconciliation (P4 across copies) and in a stale copy whenever a member is not running, H6 in
reaching only decisions. H1 can supply equivalents for P1–P9, but only if it is designed as an
append-only fact log with a kind column and a stop row. Designed as current state, it loses P4,
P5 and P6's "short" signal. H1 also changes P9's never-waits contract and P14's
hand-repairability. H3 loses P1, P4, P5 and P7 for the link, which is acceptable only if the link
is configuration with no history. The plan makes linking an operator act with a charter (proposal
item 1). It does not say whether widening or unlinking is history the board must keep. That is an
open question, not a finding.

---

## 3. What each candidate needs in this code

The shared seams, all `[read]`:

- **Where facts are derived:** `boardfacts.outcomes(before, intent, after, now)`
  (`boardfacts.py:136`). It yields `Fact(session_id, record)` (`boardfacts.py:115-133`). The
  destination type is a session id and nothing else, and `_about` and `_filed` pick the
  declarer's or sender's session (`boardfacts.py:416-458`).
- **Where facts are written:** the observer composed in `app._observer` (`app.py:1190-1211`),
  in three halves: board (`_record_board`, `app.py:1289-1344`), fleet and commands. Each half
  catches its own failures. A channel half would be a fourth.
- **Where a log is restored:** `app._launch` reads a session's log only when
  `spec.resume is not None` and the id has no `board_logs` entry (`app.py:2459-2464`), on a
  worker thread, *before* the session exists. `_open_board_log` (`app.py:2201-2285`) emits
  `BoardRestored` right after `pool.submit`, with no `await` between, so the restore is ahead of
  everything the session says (`app.py:2208-2213`). `_settle_restore` (`app.py:1347-1376`) then
  calls `writer.adopt` on the step that applied the restore, and only if
  `Restore.adoptable` (`model.py:2627-2640`).
- **What the store keeps of a restore:** `Snapshot.restores: Mapping[str, Restore]`
  (`model.py:2708`). It is keyed by session id, and `Restore` counts tasks and concerns
  (`model.py:2562-2640`). `store._restored` places by id and refuses an id another session's
  board holds (`store.py:2592-2700`; `Unplaced.OTHER_SESSION_HAS_ID`, `model.py:2518`).

**The structural point for every candidate except H4 and H6** `[inferred]`: a fact with no
owning session has no trigger in `_launch`, because restoring there is a per-session act gated
on `spec.resume`. Two places could restore one, and both are unbuilt:
(a) at startup, beside `_recall_fleet` (`app.py:5157-5160`), which already puts closed cards
back from disk on a daemon thread; or
(b) on the first member's resume, inside `_launch`'s worker read.
With (a), whether a launch can overtake the channel restore is not established, because the
fleet recall thread is not ordered against launches by anything I found. With (b), a channel with
no member resumed is not in the store. The plan's card line, which shows a peer is not running
(Decided 6), then has nothing to draw from for a channel whose members are all closed cards.

| | Derive (`boardfacts`) | Write | Restore trigger and report | Store/model |
|---|---|---|---|---|
| **H1** | `Fact` gains a destination other than a session id (a sum: `SessionLog \| ChannelStore`), or a parallel `channel_outcomes` | A new writer thread owning the connection, in a fourth observer half. `BoardWriter` is not reused: it calls `boardlog.append` (`boardsync.py:745`) | (a) or (b). Read on a worker, then an intent `ChannelsRestored(channels, clock)`. Needs a channel-keyed `Restore`-like report and an adopt equivalent (P12) | `Snapshot.channels` (link records), `Decision.channel`, a `ChannelRestore` map |
| **H2** | Same `Fact` widening | `BoardWriter` reused with a channel-keyed directory. `submit(session_id, …)` and `directory()` are typed and named by session (`boardsync.py:557`, `575-580`), and `_append`'s legacy-root check (`boardsync.py:714-723`) is session-specific. So it is a generalised key, or a second writer class over `boardlog.append` | (a) or (b). `boardlog.read` + a channel fold. `boardlog.restore` folds tasks and concerns only (`boardlog.py:1203-1320`), so a channel's link and decision kinds need their own fold, or new arms in it and new fields on `Board` | As H1 |
| **H3** | Link changes derive a config write, not a `Fact`. Decisions go to whichever other column is paired | `settings.save`-like rename-over, or `sessionfile.update`-like merge. **Not inside `settings.json`**: `tests/test_settings_dialog.py:99` fails for any `Settings` field without a dialog place, and `settings.py:13-15` keeps settings out of the snapshot (I1), while the link must be in the store (plan, "held in the store") | Startup read (a), which is natural for config | `Snapshot.channels` |
| **H4** | `outcomes` yields one `Fact` per member for each link change. `Fact` stays session-keyed, so there is no type change | The existing `BoardWriter`. A member with no `board_logs` entry, i.e. not running in this process, is skipped (`app.py:1320-1322`), so its copy is stale until a later change reaches it | Inside `_launch` per member, unchanged. The **new piece is placement**: the second member's copy meets the link already in the store. Today's rules would count it if equal, and if different the restore is refused or the item unplaced (`store.py:2601-2617`). Either makes `Restore.short` true and the member **never adoptable**. The reconciliation rule must therefore sit in `_restored`, with the channel exempt from `short`, which loosens a rule that `model.py:2627-2640` calls deliberately strict | Link in the store keyed by channel. `_restored` learns a channel arm |
| **H5** | `Fact` widened to (channel, party) | A writer over `requests.py`-style per-party directories, or `boardlog.append` into `channels/<name>/<party>/` | (a) or (b), with a derive across party directories | As H1 |
| **H6** | `boardfacts.outcomes`'s decision case returns records (`boardfacts.py:164-175`) to the asker's session. `Fact` is unchanged | The existing `BoardWriter` | The existing `_launch` and `BoardRestored` path. `boardlog.restore` and `store._restored` gain decision arms. `Restore` gains a decision count | `Decision.channel`, and a third branch in `decisions_readable_by` (`model.py:2798-2817`), which R1 already sized |

---

## 4. Two windows, both members of one channel

Two pptmstr processes, each with its own reducer, and both write the link or a decision. The
plan defers cross-process work (plan, "Deferred") but the operator's own run had two windows
(plan, "Why"). `boardsync.py:66-70` and `boardlog.py:28-36` say what boardlog does today:
records interleave, none is lost, and impossible ones are marked contested.

- **H1.** SQLite serialises the two writers' transactions. Q2/Q3 own whether that holds here, on
  this filesystem, with these settings. Serialised is not consistent: each reducer granted from
  its own store, and SQLite orders the two answers without judging them. With an append-only
  facts table and a fold, the result is boardlog's P4: both kept, and contested only if the fold
  asks. With a state table, the later `UPDATE` wins and the earlier is gone without a mark. SQL
  constraints could refuse the second write. That would put reducer rules in a second place, and
  the second window would see a failed write it has to turn into "your store is stale". No
  existing path in the shell does that.
- **H2.** Two `BoardWriter`s append to one channel directory. `os.link` keeps both (P1), and
  the channel fold marks what one reducer could not have appended (P4). This is exactly what
  `boardsync.py:66-70` describes for a session id open in two windows. *Unless* the second
  window first fails the P12 gate: a window that has not adopted the channel log refuses its
  first record (`boardsync.py:123-138` behaviour, if reused). That stops its recording of the
  channel for the run, which is the safe outcome and a loud one.
- **H3.** Last rename wins whole (`sessionfile.py:42-49`). One window's link change is lost
  with no mark. Decisions follow whichever other column they are paired with.
- **H4.** Window 1 writes the link change into the logs of the members *it* is running. Window 2
  does the same for its members. If each window runs a different member, each member's log has
  only its own window's change. The copies disagree, and the reconciliation rule decides. With
  no writer identity in the record (`boardlog.py:33-36`), "latest wall time wins" is the only
  tiebreak available, and P11 says wall time can step (`boardlog.py:1495-1505`).
  **Revised (lead's H4 walk; Q4).** This assumes both windows hold the same link. Under the plan
  they cannot: a link lives in the store of the window where the operator made it, and
  cross-process channels are deferred. The only two-window case left is one session id open in
  both, which is today's `boardsync.py:66-70` case. H4's real divergence is a member that the
  linking window is not running, or not recording. With store-minted link ids and unlink
  tombstones, no tiebreak is needed (`q4-comparison.md` §(b2)).
- **H5.** Each window writes only its own party's directory. Two windows *on the same party*
  are `requests.py`'s one remaining race, closed by `os.link` (`requests.py:14-20`). Two parties
  disagreeing is a derive-time conflict, reported and not merged.
- **H6.** A decision lives in its asker's log. Two windows both raising decisions write two
  askers' logs, or one asker's log from two windows, which is the existing
  `boardsync.py:66-70` case. A decision *answered* in window 1 is not seen by window 2's store
  until window 2 restores that log. That holds for every candidate: no candidate here pushes a
  change into another process's store, because there is no cross-process notification
  (`requests.py` is unwired; plan, "Deferred").

**Revised (S2 finding 3; Q4).** The H1 bullet calls a refusal in SQL "a second reducer". That
is right for a *constraint that judges the fact*. It does not cover the narrower check that
detects a stale store: "append only if the last record is the one I saw". This is an
expected-version append, and it is open to both candidates on equal terms (run, `q2_probe.py
--q4`, section Q4-G, two processes, 500 attempts each):
- H2: `os.link` to exactly one past the last ordinal seen, with `FileExistsError` taken as stale;
- H1: `BEGIN IMMEDIATE`, compare `max(seq)` with the last seen, and `ROLLBACK` if it differs.

Neither run lost a record, duplicated a sequence number or left a gap. Q4's comparison has the
counts. So the two-windows case gives H1 no edge on "contested": SQLite's lock orders the two
windows and does not judge them, and the check that detects staleness costs the same in both.

Not established by reading: whether a second window restoring a channel *while the first is
writing to it* reads a consistent view in H1 (Q2/Q3) or a consistent prefix in H2 (P3 says
yes).

---

## 5. STYLE.md's functional core and H1

`STYLE.md:41-53`: the reducer is pure and total, reads no clock, performs no IO. Exceptions are
listed with reasons (`STYLE.md:91-101`).

H1 can be persistence only, with the reducer untouched in kind `[inferred from read]`. That
holds if it is wired exactly as boardlog is:
1. **Derive** is pure: `boardfacts.outcomes` (or a sibling) maps `(before, intent, after)` to
   values (`boardfacts.py:1-41`).
2. **Write** is in an observer half on its own thread, never in `_apply`. `Store.apply_all`
   calls the observer after each intent (`store.py:239-258`), and the existing halves never
   feed back into the store.
3. **Read** is on a worker. The result enters as an intent carrying a value (the
   `BoardRestored(session_id, board, clock)` pattern, `app.py:2283`), and the reducer arm
   places it as `store._restored` does (`store.py:2592`).

**What would break it** `[inferred]`: reading the database from a reducer arm, for example "is
this session a member?" answered by a query instead of by `Snapshot`; and letting a SQL
constraint's refusal decide an outcome, so that the database rather than `_apply` settles
whether a link change happened. Both are easy with a database in reach and impossible with a
directory of files, which is a difference in temptation and not in capability. Nothing in H1 *requires*
either. `Snapshot.channels` is the membership answer, as `Snapshot.tasks` is for tasks. The
same holds for H2–H6, which do not touch the core's shape either.

---

## 6. Rough size, and what reading could not establish

Sizes are my judgement from reading `[inferred]`, the same scale R1 used. They cover the
*unowned-fact home only*, on top of the work every candidate shares (§0 finding 3: decision
records and restore; `Decision.channel`; `decisions_readable_by`; `Snapshot.channels`).

| | Size | Where it goes |
|---|---|---|
| H1 | **L** | A new module (schema, codec with kind column, stop row, version check), a writer thread, a `Fact` destination sum, a channel restore trigger and report, an adopt gate, and tests for each property in §2 rebuilt from scratch. `tests/test_boardlog.py` covers none of it |
| H2 | **M** | Generalise `BoardWriter`'s key, a channel fold beside `boardlog.restore` (new kinds), `Fact` destination sum, a restore trigger and report, and a third root. P1–P10 and their tests come free |
| H3 | **S** for the link | A file, a startup read, a rename-over write. Pays with P1/P4/P5/P7 (§2), and decisions still need another column |
| H4 | **M** | No new writer or root, but a reconciliation rule inside `store._restored` and an exemption from `Restore.short` for channel items, which touches the most safety-critical predicate on the restore path. A stale copy whenever a member isn't running |
| H5 | **M–L** | `requests.py` exists but is unwired, and its record types are request-specific (`requests.py:239-330`), so this is a new module in its shape |
| H6 | **S** on top of the shared decision work (decisions only) | Mostly the shared work: once decisions are logged at all, filing a channel decision in the asker's log is the default and adds little. The link still needs H1–H5 |

**Revised (S2 finding 10; Q4).** H2's "P1–P10 and their tests come free" holds for append,
read and the codec. It does not hold for the fold. H2's channel fold and its tests are new
(§2's P4 revision). H1's "tests … rebuilt from scratch" assumes a new codec. A `facts(kind,
body)` table whose `body` is `boardlog.encode(record)` reuses the codec and the same fold. The gap
is then the SQLite I/O layer, its writer thread and its tests, plus the costs Q2–Q4 found. Read
it as **H1 = H2 + an SQLite layer**, sizes roughly **M+ against M**, not L against M. The table
above is left as written.

**Not established by reading:**
- Everything SQLite does under concurrency, crash and these filesystems: Q2/Q3.
- Whether `_recall_fleet`'s thread can be overtaken by a launch, which matters for a startup
  channel restore (§3 (a)). I read its docstring (`app.py:4988-5013`) and the start site, not the
  launch ordering against it.
- Whether the link has history worth keeping (widening, unlinking, charter edits). This decides
  whether H3's losses are real. It is a product question in the plan, not in the code.
- Whether an older build in use matters in practice. P6's cost for H4/H6 assumes one is run
  against a newer config directory (`[inferred]`).
- No candidate was built. The probe in §0.1 exercises today's `boardlog`/`boardsync` only.

---

## 7. Out of scope: replacing boardlog wholesale with SQLite

Noted, not evaluated. It would involve, at least `[read]` for what exists:
- every `Known` kind as rows (`boardlog.py:454-469`);
- `restore`'s contested logic re-expressed or kept as a fold over rows (`boardlog.py:1161-1466`);
- `BoardWriter` rewritten around a connection (`boardsync.py:524-745`);
- `migrate` and the legacy and two-histories handling redone (`boardsync.py:1-56`,
  `394-467`);
- `tests/test_boardlog.py`'s kind and field counting rebuilt;
- a migration of every existing session's directory of JSON files;
- the hand-repair instructions in `boardsync.py:44-51` rewritten for a tool that is not on this
  machine's `PATH`.

Brief logs (`brief.py`) and request records (`requests.py`) share the same file primitive and
would be a separate decision.
