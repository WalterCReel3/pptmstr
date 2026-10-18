# Q3: what SQLite and Python document about guarantees and failure modes

Research for [`../2026-10-17-two-leads-in-one-window-share-a-channel.md`](../2026-10-17-two-leads-in-one-window-share-a-channel.md),
open question "Where the link record lives". It inspects H1 (SQLite beside boardlog) against what
the vendors say. It doesn't recommend anything.

**How to read the tags.**
- **fetched**: quoted from a page fetched on 2026-10-17, at the URL given.
- **run**: a probe executed on this machine on 2026-10-17. Scratch is in `$TMPDIR/q3/`, not committed.
- **read**: established by reading this repository.

Nothing here is **recalled**.

**WebFetch caveat.** Every "fetched" quote passed through WebFetch. WebFetch converts the page to
markdown, and then a small model extracts from it. I asked for verbatim text every time, but a
small model can still trim or splice a sentence. The quotes this document leans on are marked ⚠.
Re-open the URL before treating one of them as settled. A ⚠ quote can be load-bearing for two
reasons: it drives a row in the table at the end, or it is surprising.

---

## 1. sqlite.org

### 1.1 Atomic commit: https://www.sqlite.org/atomiccommit.html (fetched)

- The claim ⚠: "SQLite has the important property that transactions appear to be atomic even if the
  transaction is interrupted by an operating system crash or power failure." (§1)
- What the claim rests on ⚠ (§2, Hardware Assumptions):
  - "SQLite assumes that the flush or fsync will not return until all pending write operations for
    the file that is being flushed have completed."
  - "SQLite assumes that a file deletion is atomic from the point of view of a user process."
  - "SQLite does not add any redundancy to the database file for the purpose of detecting
    corruption or I/O errors. SQLite assumes that the data it reads is exactly the same data that
    it previously wrote."
  - "SQLite assumes that the operating system that it is running on works as advertised. If that
    is not quite the case, well then hopefully you will not lose power too often."
- §9, "Things That Can Go Wrong", opens: "The atomic commit mechanism in SQLite has proven to be
  robust, but it can be circumvented by a sufficiently creative adversary or a sufficiently broken
  operating system implementation."
  - 9.1: "We have received reports of implementations of both Windows network filesystems and NFS
    in which locking was subtly broken."
  - 9.2, on fsync and FlushFileBuffers: "we have received reports that neither of these interfaces
    works as advertised on many systems."
  - 9.5, on a hot journal that is deleted or renamed: "We know of no way to prevent this other than
    user education."

### 1.2 How To Corrupt An SQLite Database File: https://www.sqlite.org/howtocorrupt.html (fetched)

- **§2.2, close() drops POSIX locks** ⚠:
  - "the close() system call will cancel all POSIX advisory locks on the same file for all threads
    and all file descriptors in the process."
  - "Note that it is perfectly safe for two or more threads to access the same SQLite database file
    using the SQLite library. The unix drivers for SQLite know about the POSIX advisory locking
    quirks and work around them. This problem only arises when a thread tries to bypass the SQLite
    library and read the database file directly."
  - "Beginning with SQLite version 3.51.0 (2025-11-04), SQLite implements additional defenses
    [...] These new defenses help when the database is in WAL mode and is being accessed from
    multiple processes. But they are not a cure-all. To avoid corruptions, developers should be
    careful to never use close() on an SQLite database file while one or more database connections
    are open, even in other threads."
  - The probe in §5 reproduced this on this machine.
- **§2.3, two copies of SQLite in one process**: "A close() operation on one connection might
  unknowingly clear the locks on a different database connection, leading to database corruption."
- **§1.1, a closed descriptor reused**: SQLite 3.8.1 and later "refuse to use low-numbered file
  descriptors for database files".
- **§1.2, backups**:
  - "Systems that run automatic backups in the background might try to make a backup copy of an
    SQLite database file while it is in the middle of a transaction. The backup copy then might
    contain some old and some new content, and thus be corrupt."
  - The safe routes it names are `sqlite3_rsync`, `VACUUM INTO`, and the backup API.
  - "If the previous write transaction failed, then it is important that any rollback journal (the
    \*-journal file) or write-ahead log (the \*-wal file) be copied together with the database file
    itself."
- **§3.1, drives that lie about sync** ⚠:
  - "most consumer-grade mass storage devices lie about syncing."
  - "SQLite must believe whatever the operating system and hardware tell it about the status of
    sync requests. There is no way for SQLite to detect that either is lying".
  - "In WAL mode, the only time that a failed sync operation can cause database corruption is
    during a checkpoint operation. A sync failure during a COMMIT might result in loss of
    durability but not in a corrupt database file."
- **§3.2**: "For maximum reliability and for robustness against database corruption, SQLite should
  always be run with its default synchronous setting of FULL."
- **§8, SQLite's own bugs**: "There have been a few historical bugs in SQLite (now fixed) that could
  cause database corruption. And there may be yet a few more that remain undiscovered."
- **§8.1, the WAL-reset bug** ⚠: "When there are two or more database connections in separate
  threads or processes, both open on the same WAL-mode database, and if both connections try to
  write or run checkpoint at the same time, then there is a race condition that might corrupt the
  database file. [...] It existed in all versions of SQLite from 3.7.0 through 3.51.2." Its detail
  is in §1.3.
- **This page doesn't mention F_FULLFSYNC or macOS.** For both, see §1.6 and §3.

### 1.3 WAL: https://www.sqlite.org/wal.html (fetched)

- **Same-host requirement, network filesystems** ⚠: "All processes using a database must be on the
  same host computer; WAL does not work over a network filesystem. This is because WAL requires all
  processes to share a small amount of memory". §2.2 adds: "the use of shared memory means that all
  readers must exist on the same machine."
- **Advantages**:
  - "readers do not block writers and a writer does not block readers."
  - "WAL uses many fewer fsync() operations and is thus less vulnerable to problems on systems
    where the fsync() system call is broken."
- **Disadvantages**:
  - "There is an additional quasi-persistent "-wal" file and "-shm" shared memory file associated
    with each database, which can make SQLite less appealing for use as an application
    file-format."
  - "There is the extra operation of checkpointing which, though automatic by default, is still
    something that application developers need to be mindful of."
  - "Transactions that involve changes against multiple ATTACHed databases are atomic for each
    individual database, but are not atomic across all databases as a set."
- **Durability by `synchronous` setting**: "Writers sync the WAL on every transaction commit if
  PRAGMA synchronous is set to FULL but omit this sync if PRAGMA synchronous is set to NORMAL." With
  NORMAL, "transactions are no longer durable and might rollback following a power failure or hard
  reset."
- **Checkpoint starvation**: "if a database has many concurrent overlapping readers and there is
  always at least one active reader, then no checkpoints will be able to complete and hence the WAL
  file will grow without bound."
- **Persistence**:
  - "Unlike the other journaling modes, PRAGMA journal_mode=WAL is persistent."
  - "The WAL journal mode will be set on all connections to the same database file if it is set on
    any one connection."
- **§9, SQLITE_BUSY in WAL**: "there are some obscure cases where a query against a WAL-mode
  database can return SQLITE_BUSY, so applications should be prepared for that happenstance."
  - One case is a connection holding exclusive locking mode.
  - One case is the last connection closing: it "will acquire an exclusive lock for a short time
    while it cleans up the WAL and shared-memory files".
  - One case is recovery after a crashed last connection: "An exclusive lock is held during
    recovery."
- **§11, the WAL-Reset Bug** ⚠:
  - "The bug is likely present in all version of SQLite from 3.7.0 (2010-07-21) through 3.51.2
    (2026-01-09). It is fixed in version 3.51.3 (2026-03-13) and later. Backports of the fix are
    available for some earlier releases: 3.44.6 and 3.50.7."
  - "The bug only affects databases in WAL mode when there are two or more database connections
    open on the same file, in separate threads or processes, and when those two connections attempt
    to write or checkpoint at the same instant."
  - §11.2: "the occurrence rate of this problem in the wild appears to be less than or equal to the
    expected occurrence rate of SSD malfunctions and/or cosmic-ray hits".
  - The same section continues: "UPDATE: 2026-08-24 → Phil Eaton has devised a reproducer that does
    not involve using the special testing code hack."
  - A WebSearch summary of third-party reports (Tailscale, antithesis.com, gigazine) said the
    following, and I did not fetch them:
    - Tailscale saw "19 database corruptions in six months";
    - 3.52.0 was withdrawn.

    Treat both as unverified. Sources:
    - https://antithesis.com/blog/2026/wal-reset-bug/
    - https://www.techzine.eu/news/infrastructure/143585/old-sqlite-bug-caused-months-of-outages-at-tailscale/
  - **The SQLite on this machine is 3.40.1** (run, §5), which is inside the affected range.
  - **Revised (S2 findings 4 and 6; Q4).** Three additions; the bullets above stand.
    - *No backport here* (run). The interpreter's `_sqlite3` links the system
      `/lib/x86_64-linux-gnu/libsqlite3.so.0`, Debian package `3.40.1-2+deb12u2`. Its changelog's
      newest entry is dated 2025-08-26 (CVE-2025-6965 and a NOT NULL optimisation fix), before the
      bug was found on 2026-03-03. So the linked build does not carry the fix.
    - *The trigger needs two connections that each write or checkpoint* (fetched, wal.html §11.1,
      re-fetched 2026-10-17): a complete checkpoint, a second checkpoint starting, and "another
      database connection" committing a transaction that resets the WAL during it. A layout with
      one writing connection per database has no second connection to commit in step 3. That
      covers one window with a single writer connection. Two windows have two writers. The page
      does not say whether a read-only connection ever checkpoints, apart from the last
      connection's checkpoint on close (wal.html §3.1, §6, fetched).
    - *A Mac install does not escape the range* (fetched). `Mac/BuildScript/build-installer.py`
      on the CPython `3.13` and `3.14` branches, and at tag `v3.14.0`, bundles "SQLite 3.50.4".
      That is inside the range and below the 3.50.7 backport. Homebrew and uv-managed Pythons
      were not checked.

### 1.4 Locking, concurrency, busy handling

**https://www.sqlite.org/lockingv3.html** (fetched). It covers rollback mode only.

- "SQLite uses POSIX advisory locks to implement locking on Unix. [...] SQLite assumes that these
  system calls all work as advertised. If that is not the case, then database corruption can
  result."
- "POSIX advisory locking is known to be buggy or even unimplemented on many NFS implementations
  (including recent versions of Mac OS X) [...] Your best defense is to not use SQLite for files
  on a network filesystem."
- EXCLUSIVE lock: "Only one EXCLUSIVE lock is allowed on the file and no other locks of any kind are
  allowed to coexist with an EXCLUSIVE lock."
- PENDING lock: "No new SHARED locks are permitted against the database if a PENDING lock is
  active".
- "If a crash or power failure occurs and results in a hot journal but that journal is deleted, the
  next process to open the database will not know that it contains changes that need to be rolled
  back."

**https://www.sqlite.org/lang_transaction.html** (fetched)

- "The default transaction behavior is DEFERRED."
- "If a write statement occurs while a read transaction is active, then the read transaction is
  upgraded to a write transaction if possible. If some other database connection has already
  modified the database or is already in the process of modifying the database, then upgrading to
  a write transaction is not possible and the write statement will fail with SQLITE_BUSY."
- "The BEGIN IMMEDIATE might fail with SQLITE_BUSY if another write transaction is already active
  on another database connection."
- "EXCLUSIVE and IMMEDIATE are the same in WAL mode".

**https://www.sqlite.org/c3ref/busy_handler.html** (fetched) ⚠

- "The presence of a busy handler does not guarantee that it will be invoked when there is lock
  contention. If SQLite determines that invoking the busy handler could result in a deadlock, it
  will go ahead and return SQLITE_BUSY to the application instead of invoking the busy handler."
  The example it gives is a reader promoting to RESERVED while another connection promotes RESERVED
  to EXCLUSIVE.
- "The default busy callback is NULL."

**https://www.sqlite.org/isolation.html** (fetched)

- "all transactions in SQLite show "serializable" isolation."
- "There can only be a single writer at a time to an SQLite database."
- "In WAL mode, SQLite exhibits "snapshot isolation"."
- "there is no isolation between operations that occur within the same database connection."

### 1.5 Appropriate Uses For SQLite: https://www.sqlite.org/whentouse.html (fetched)

- "SQLite does not compete with client/server databases. SQLite competes with fopen()."
- "SQLite supports an unlimited number of simultaneous readers, but it will only allow one writer
  at any instant in time."
- The checklist: is the data separated from the application by a network, are there many
  concurrent writers, is it big data? If any is true, choose client/server. "Otherwise → choose
  SQLite!"

Supporting pages:

- **https://www.sqlite.org/useovernet.html** (fetched):
  - "network filesystem sync and locking reliability vary among implementations and installations
    [...] Rely upon it at your (and your customers') peril."
  - "SQLite is designed for situations where the data and application coexist on the same
    machine."
- **https://www.sqlite.org/appfileformat.html** (fetched). It is promotional, and says so of
  itself.
  - "Writes are serialized, but as writes normally only take milliseconds, applications simply take
    turns writing."
  - "new features can be added to an SQLite application file format simply by adding new tables to
    the schema or by adding new columns to existing tables."
  - It concedes: "command-line tools such as text editors or 'grep' or 'awk' are not useful on an
    SQLite database."

### 1.6 PRAGMAs: https://www.sqlite.org/pragma.html (fetched)

- **synchronous=FULL**: "This ensures that an operating system crash or power failure will not
  corrupt the database. FULL is the default synchronous mode for a rollback journal."
- **synchronous=NORMAL** ⚠: "WAL mode is always consistent with synchronous=NORMAL, but WAL mode
  does lose durability. A transaction committed in WAL mode with synchronous=NORMAL might roll back
  following a power loss or system crash. Transactions are durable across application crashes
  regardless of the synchronous setting or journal mode."
- **synchronous=EXTRA** ⚠:
  - "Without EXTRA, depending on the underlying filesystem, it is possible that a single
    transaction that commits right before a power loss might get rolled back upon reboot. The
    database will not go corrupt. But the last transaction might go missing".
  - "EXTRA is no different from FULL in WAL mode."
- **fullfsync** ⚠: "This flag determines whether or not the F_FULLFSYNC syncing method is used on
  systems that support it. The default value of the fullfsync flag is off. Only Mac OS X supports
  F_FULLFSYNC." `checkpoint_fullfsync` has the same off default.
- **user_version**: "an integer that is available to applications to use however they want. SQLite
  makes no use of the user-version itself." This is the documented place for a schema version
  number.
- **busy_timeout**: "Each database connection can only have a single busy handler."

### 1.7 File-format stability and long-term support

**https://www.sqlite.org/lts.html** (fetched)

- "The intent of the developers is to support SQLite through the year 2050."
- "we cannot absolutely promise that SQLite will be viable or useful that far out."
- "the developers also promise to keep the SQLite C-language API and on-disk format fully backwards
  compatible."

**https://www.sqlite.org/formatchng.html** (fetched)

- "Newer versions of SQLite can always read and/or write database files created by older versions
  of SQLite, back to version 3.0.0 (2004-06-18)."
- The other direction has a limit: "Older versions of SQLite back to version 3.0.0 can read and
  write database files created by newer versions of SQLite as long as the database does not make
  use of newer features that are unknown to that older version."
- The worked example is WAL: versions before 3.7.0 cannot open a WAL-mode file.

### 1.8 Testing: https://www.sqlite.org/testing.html (fetched, page "last updated on 2026-04-21")

- "As of version 3.42.0 (2023-05-16), the SQLite library consists of approximately 155.8 KSLOC of C
  code. By comparison, the project has 590 times as much test code and test scripts - 92053.1
  KSLOC."
- "The SQLite core, including the unix VFS, has 100% branch test coverage under TH3 in its default
  configuration [...] Extensions such as FTS3 and RTree are excluded from this analysis." The page
  also claims 100% MC/DC.
- The limits it states:
  - "It is impractical to do crash testing using real power failures, of course, and so crash
    testing is done in simulation."
  - TH3 is "proprietary".
  - useovernet.html adds: "the SQLite library is not tested in across-a-network scenarios, nor is
    that reasonably possible."
- **Set against the testing claim**: the WAL-reset bug of §1.3 lived from 3.7.0 to 3.51.2, under
  this regime. The developers "were unable to reproduce the bug organically and had to add special
  testing logic" (wal.html §11).

### 1.9 ALTER TABLE: https://www.sqlite.org/lang_altertable.html (fetched)

- "SQLite supports a limited subset of ALTER TABLE. [...] it can be renamed; a column can be
  renamed; a column can be added to it; or a column can be dropped from it."
- ADD COLUMN restrictions:
  - "The column may not have a PRIMARY KEY or UNIQUE constraint."
  - "If a NOT NULL constraint is specified, then the column must have a default value other than
    NULL."
  - The column may not have a default of CURRENT_TIMESTAMP or similar, nor "an expression in
    parentheses".
- DROP COLUMN fails if the column is a PRIMARY KEY, is UNIQUE, is indexed, is used in a foreign key,
  or appears in a trigger or view, among other cases.
- Any other change goes through the 12-step procedure: create a new table, copy the rows, drop the
  old table, rename, and rebuild indexes, triggers and views, all in one transaction.
- The reason: "SQLite [...] stores the schema in the sqlite_schema table as the original text of
  the CREATE statements".
- The page doesn't give the version that introduced RENAME COLUMN or DROP COLUMN (as fetched). I
  didn't chase those versions.

### 1.10 Rowid ordering: https://www.sqlite.org/autoinc.html (fetched) ⚠

This bears on "never a record ahead of one written before it".

- Without AUTOINCREMENT: "If you ever delete rows or if you ever create a row with the maximum
  possible ROWID, then ROWIDs from previously deleted rows might be reused when creating new rows
  and newly created ROWIDs might not be in strictly ascending order."
- With AUTOINCREMENT: "rows with automatically selected ROWIDs are guaranteed to have ROWIDs that
  have never been used before by the same table in the same database. And the automatically
  generated ROWIDs are guaranteed to be monotonically increasing."
- AUTOINCREMENT doesn't promise sequential values: "AUTOINCREMENT guarantees that automatically
  chosen ROWIDs will be increasing but not that they will be sequential." It also doesn't promise
  stable values across rollback: "ROWID values that were rolled back are ignored and can be
  reused."

---

## 2. Python `sqlite3`: 3.11 vs 3.12 vs current

The current page is https://docs.python.org/3/library/sqlite3.html. WebFetch reported that it
documents **Python 3.15.0** (fetched). I did not read the 3.13 or 3.14 pages, or any "What's New".

| | 3.11 (https://docs.python.org/3.11/library/sqlite3.html) | 3.12 (https://docs.python.org/3.12/library/sqlite3.html) | current, 3.15 |
|---|---|---|---|
| `autocommit` | **None.** I asked WebFetch the question explicitly, and it answered "No": there is no `autocommit` parameter or attribute. | "Changed in version 3.12: Added the *autocommit* parameter." The attribute: "Added in version 3.12." | As in 3.12. Also: "Changed in version 3.15: All parameters except *database* are now keyword-only." |
| default transaction control | `isolation_level` defaults to `""`, an alias for `"DEFERRED"`. | `autocommit` "currently defaults to `LEGACY_TRANSACTION_CONTROL`. The default will change to `False` in a future Python release." | Same text: still LEGACY, and still announced to change. |
| implicit BEGIN (legacy) | "new transactions are implicitly opened before `execute()` and `executemany()` executes `INSERT`, `UPDATE`, `DELETE`, or `REPLACE` statements; for other statements, no implicit transaction handling is performed." | Same, but only "If `Connection.autocommit` is set to `LEGACY_TRANSACTION_CONTROL` [...] Otherwise, `isolation_level` has no effect." | Same as 3.12. Quoted in `Cursor.execute()`. |
| `autocommit=False` | n/a | "`sqlite3` ensures that a transaction is always open, so `connect()`, `Connection.commit()`, and `Connection.rollback()` will implicitly open a new transaction [...] `sqlite3` uses `BEGIN DEFERRED` statements". The docs call it "the recommended value of `autocommit`." | Same. |
| `executescript()` | "implicitly commits any pending transaction before execution of the given SQL script, regardless of the value of `isolation_level`." | Same, under the legacy section. | Not separately read. |
| `check_same_thread` | "If `True` (default), `ProgrammingError` will be raised if the database connection is used by a thread other than the one that created it. If `False` [...] write operations may need to be serialized by the user to avoid data corruption." | Same. | Same. |
| `threadsafety` | "Changed in version 3.11: Set *threadsafety* dynamically instead of hard-coding it to `1`." | Same. | Same. |
| `timeout` | "Default five seconds." | Same. | Same. |

Other notes from the 3.12 page:

- **Context manager** (fetched): "The context manager neither implicitly opens a new transaction
  nor closes the connection."
- "An implicit rollback is performed if the database is `close()`-ed with pending changes." This is
  stated under autocommit=False.
- "Changed in version 3.12: The recommended way of controlling transactions is now via the
  `autocommit` attribute."
- Deprecated in 3.12: the default adapters and converters. The page also notes "The default
  "timestamp" converter ignores UTC offsets".

**Python 3.11 has no `autocommit`, and that matters here.** The repository floor is
`requires-python = ">=3.11"` (pyproject.toml; read). Code that must run on 3.11 has to use
`isolation_level=None` and issue BEGIN and COMMIT itself, or accept the legacy rule. Under the
legacy rule, only DML opens a transaction implicitly. A SELECT-then-INSERT is therefore not one
transaction unless the code opens one first.

---

## 3. Known failure reports outside sqlite.org

- **macOS fsync.** Every source here agrees that plain `fsync` on macOS doesn't reach stable
  storage. They disagree about which setting is then enough, because no fetched source showed
  F_FULLFSYNC behaviour under WAL+FULL.
  - **SQLite forum, "Set F_FULLSYNC on macOS?", 2022-02-23**
    (https://sqlite.org/forum/info/b94afa45dda82aae8cbf49f9d511a00b332870fc926cba18954acd889bbfb7cd;
    fetched):
    - Dan Kennedy, a SQLite developer: F_FULLFSYNC is used only "if "PRAGMA fullfsync" or "PRAGMA
      checkpoint_fullfsync" is set."
    - An anonymous reply: "And it is zero by default. Huge gotcha."
    - Simon Slavin, testing "at least five years ago": without it, power loss "sometimes led to the
      most recent transaction being lost", and "I did not find any examples of corrupt databases".
      He also says F_FULLFSYNC "slowed things down *a lot*".
  - **agwa.name, 2025-08-29** (https://www.agwa.name/blog/post/sqlite_durability; fetched). This
    is a third-party post.
    - "On macOS, fsync is nerfed to make macOS appear faster."
    - "When `journal_mode` is DELETE, you need to set `synchronous` to EXTRA to get durability."
    - "When `journal_mode` is WAL, FULL is sufficient for durability."
    - "if your application might be used on macOS, enable `fullfsync`."
  - **Unverified.** A WebSearch summary said Apple's own SQLite build substitutes F_BARRIERFSYNC.
    It cited mjtsai.com quoting Jonathan Johnson, and I did not fetch it.
  - **Not fetched.** I fetched no Apple documentation of `fsync(2)` or `F_FULLFSYNC`.
- **POSIX locks dropped by close().** howtocorrupt §2.2 documents it (quoted in §1.2), and §5
  reproduces it.
  - This is an in-process hazard: *any* code in the pptmstr process that opens and closes the
    database file outside SQLite drops SQLite's locks. Examples are a backup, a file-type sniff, or
    a hash for a manifest.
  - The fix since 3.51.0 is partial, by SQLite's own account.
  - **Revised (S2 finding 5; Q4).** It applies only to code in the *same process* that opens and
    closes one of the database's files outside SQLite. In WAL mode the file that matters is
    `-shm` (run, §5's revision). howtocorrupt §2.2 names no setting that prevents it, only the
    advice never to close() the file (the skeptic's fetch, relayed; I did not search
    pragma.html for one). So treat it as a review rule. The plan already has a candidate trigger (read, plan
    "Separate and unchanged"): `publish(name, paths, manifest)` hashes or copies agent-supplied
    paths inside the pptmstr process. Under H1 it would need to refuse the database's three files.
- **The WAL-reset bug.** It is in §1.3.

---

## 4. Append-only, event-log use of SQLite

**sqlite.org documents no event-log pattern that I found.** A `site:sqlite.org` search for
append-only and insert-only returned nothing from sqlite.org (fetched). The nearest official
material is:

- appfileformat.html's "continuous update": "changes can be written back to the disk as they occur.
  This avoids loss of work on a system crash or power failure." It also describes an undo stack
  "managed using triggers".
- autoinc.html's ordering guarantees (§1.10).
- user_version (§1.6), for a schema version.

**Third-party patterns** came from WebSearch result summaries. I did not open these pages, so they
are leads only:

- A table made append-only by `BEFORE UPDATE` and `BEFORE DELETE` triggers that `RAISE(ABORT)`.
  The noted downside is that the trigger also blocks retention cleanup, and that "This is not
  tamper evidence" (repo.hex.pm `audit_log` guide).
- "The SQLite database is a rebuildable read-optimization projection only. Any discrepancy between
  the event log and the database resolves in favor of the event log." Here SQLite is the
  projection and not the log.
- The `sqlite-history` package: trigger-captured change history (https://pypi.org/project/sqlite-history/0.1a0).

**Documented downsides that apply to an append-only workload:**

- Rowids are monotonic only under the conditions in §1.10.
- WAL grows without bound under constant readers (§1.3).
- Single writer at a time (§1.4).
- Schema growth beyond ADD COLUMN takes the 12-step rebuild (§1.9).
- The file can't be read with grep or awk (§1.5).

---

## 5. Probe on this machine (run)

The script is `$TMPDIR/q3/probe.py`. I ran it with `/usr/bin/python3 -I -u`, and also with the
repository venv's python; both report the same library.

```
delete journal_mode default: ('delete',)
delete synchronous default: (2,) fullfsync: (0,) busy_timeout: (5000,)
delete before raw close:
  child refused: database is locked
delete after raw open/read/close in holder process:
  child got write lock
wal journal_mode -> ('wal',)
wal synchronous default: (2,) fullfsync: (0,) busy_timeout: (5000,)
wal before raw close:
  child refused: database is locked
wal after raw open/read/close in holder process:
  child refused: database is locked
sqlite 3.40.1 threadsafety 3 python 3.11.2
```

**What it shows.**
- **Defaults on this machine.** Python 3.11.2 is linked to SQLite 3.40.1. The defaults are
  `journal_mode=delete` and `synchronous=FULL` (2), with `fullfsync` off. Python's `timeout=5.0`
  shows up as `busy_timeout=5000`, and `threadsafety` is 3 (serialized).
- **Rollback (DELETE) mode.** One connection held `BEGIN IMMEDIATE` with an insert pending. The
  same process then did a raw `os.open`/`os.read(16)`/`os.close` on the database file. After that,
  a *second process* was granted `BEGIN IMMEDIATE`, which it had been refused just before. That is
  howtocorrupt §2.2's hazard, reproduced.
- **WAL mode.** The same sequence did not grant the second writer. I did not establish why. One
  case on one SQLite version does not show that WAL mode is immune; §1.2 says the 3.51.0 defenses
  are "not a cure-all".
- **Revised (S2 finding 5; Q4).** WAL mode is not immune: the raw close has to hit the `-shm`
  file (run, `q2_probe.py --q4`, section Q4-B). With the holder in `BEGIN IMMEDIATE` and one row
  pending, a raw `os.open`/`os.read`/`os.close` in the holder's process on:
  - the `.db` file, or the `-wal` file: the second process was still refused;
  - the `-shm` file: the second process **was granted** and committed. The holder's `COMMIT`
    then failed with `locking protocol`, and its row was lost. The error was loud, and
    `integrity_check` was ok.

  So in WAL mode the writer's lock is held on the `-shm` file. Rollback mode in the same run is
  worse, and Q4's comparison has the detail: the second process's acknowledged commit was absent
  afterwards. Q4's `q4-comparison.md` §(a), finding 5.

**What it doesn't show.**
- It doesn't cover macOS, power loss, or the WAL-reset race.
- It doesn't cover any SQLite other than 3.40.1.
- The interpreter that pptmstr ships with is not established here. pyproject pins Python 3.11 to
  3.14 for the wheels it depends on (read). Which SQLite a macOS install would link is not known.
  - **Revised (S2 finding 6; Q4).** Partly known now. A python.org 3.13 or 3.14 build bundles
    SQLite 3.50.4 (fetched, `build-installer.py`; see §1.3's revision). `bootstrap.sh` takes the
    first `python3.14 … python3.11` on `PATH` (read), so which build that is depends on the Mac.
    What settles it on a real install: `python -c 'import sqlite3; print(sqlite3.sqlite_version)'`,
    and `otool -L` on `_sqlite3*.so`.

---

## 6. boardlog's promises × what SQLite documents

The rows are the properties in `pptmstr/boardlog.py`'s module docstring and `append` docstring
(read). The SQLite column is what the vendor documents, with its source. Nothing in this table
recommends anything.

| boardlog promises (read) | How boardlog meets it (read) | What SQLite documents for it | Source |
|---|---|---|---|
| A record appears whole or not at all ("A name therefore appears complete or not at all") | temp file, then `os.link` | Transactions "appear to be atomic even if the transaction is interrupted by an operating system crash or power failure". This rests on fsync working, file deletion being atomic, and reads returning what was written. | atomiccommit.html §1, §2 ⚠ |
| Two writers lose no record; a lost race never overwrites | `os.link` refuses an existing name in the kernel; the loser takes the next ordinal | Serializable isolation and "a single writer at a time". The second writer gets SQLITE_BUSY, and the busy handler may be skipped ("does not guarantee that it will be invoked"). A read upgraded to a write can fail with SQLITE_BUSY. Losing nothing depends on the caller retrying. | isolation.html; c3ref/busy_handler.html ⚠; lang_transaction.html |
| Two writers lose no record, *across the process boundary* | the filesystem's link semantics | POSIX advisory locks; correct only if no code in the process close()s another descriptor to the file (reproduced, §5). The WAL-reset race affects "two or more database connections [...] in separate threads or processes" up to 3.51.2. | howtocorrupt.html §2.2 ⚠, §8.1; wal.html §11 ⚠ |
| A record is never read back ahead of one written before it; no missing ordinal is filled | each append lists the directory and starts past the highest name | With AUTOINCREMENT: "guaranteed to be monotonically increasing", but "not [...] sequential", and rolled-back values "can be reused". Without it, rowids can be reused and fall out of order after a delete of the max row. | autoinc.html ⚠ |
| A log cut short is a consistent prefix | the directory listing is the prefix; an unflushed directory entry loses only the newest names | A committed transaction survives or rolls back whole. In WAL with NORMAL, "might roll back following a power loss". In DELETE without EXTRA, "the last transaction might go missing". "The database will not go corrupt." | pragma.html (synchronous) ⚠ |
| `durable=True` survives the machine, not just the process | `fsync` of the record before linking (not of the directory) | FULL ensures "an operating system crash or power failure will not corrupt the database". Durability of the last commit needs EXTRA in DELETE mode. macOS needs `fullfsync`, which is off by default. Drives that lie defeat both. | pragma.html; howtocorrupt.html §3.1 ⚠; SQLite forum 2022-02-23 |
| Damage is reported, never read as a record; later records are withheld and counted | per-record parse; `Damage(unread=…)` | "SQLite does not add any redundancy to the database file for the purpose of detecting corruption or I/O errors." "There is nothing SQLite can do to defend against" garbage written into the file. Neither page I fetched documents per-record damage isolation. | atomiccommit.html §2, §9.4 |
| The format only grows; an older build reads newer records as `Unrecognised` or ignores a key | JSON per record, new kind or new field with a non-claiming default | The file format is backwards compatible from 3.0.0, with LTS "through the year 2050". An older SQLite reads a newer file only if it uses no "newer features" (WAL needs 3.7.0 or later). ADD COLUMN is cheap but restricted (no UNIQUE or PK; NOT NULL needs a non-NULL default). Anything else is the 12-step rebuild. `user_version` is free for a schema number. | formatchng.html; lts.html; lang_altertable.html; pragma.html |
| The directory is a parameter; its key is the session id; the root is not decided | caller passes the directory | WAL: "All processes using a database must be on the same host computer; WAL does not work over a network filesystem." Rollback mode on NFS: "Your best defense is to not use SQLite for files on a network filesystem." A file shared by sessions must sit on local disk. | wal.html ⚠; lockingv3.html §6; useovernet.html |
| The log is inspectable with ordinary tools (implicit: one text file per record) | one JSON file per record | "command-line tools such as text editors or 'grep' or 'awk' are not useful on an SQLite database". A copy taken mid-transaction "might contain some old and some new content". Copies must include `-wal`/`-journal`, or go through `VACUUM INTO` or the backup API. | appfileformat.html; howtocorrupt.html §1.2 |
| Nothing reads a clock; `restore` is a pure fold | IO only in `append` and `read` | Not a storage property, and SQLite says nothing on it. | n/a |

The table measures only what each side *promises*. Whether the promises hold was established only
where §5 says "run". For boardlog, that is no more than its own tests, which I did not run for this
task.
