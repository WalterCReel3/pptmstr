# An atomic write is create-once or replace, and the rest is the caller's

**Dated:** 2026-11-07 · **Status:** design only; nothing is built. This is B2 of
`2026-11-06-modules-are-named-for-what-they-require.md`, Round 3. · **Asked by:** the lead,
board task `r3-b2`. · **Evidence:**
- `2026-11-07-atomic-writes/copies.py` diffs the eight copies by AST at 66af79e and tables
  what varies. Its output is `copies.txt` beside it.
- `2026-11-07-atomic-writes/probes.py` runs four probes in `$TMPDIR`. Its output is
  `probes.txt` beside it.

A line marked *run* was executed. Everything else is **read**. File:line references are at
66af79e, and each also names its symbol. The modules the probes exercise (`brief`, `layout_ini`,
`settings`) had no changes against 66af79e when they ran. Another worker's uncommitted edits to
`store.py` and `test_store.py` were in the tree, and the probes do not reach them.

## The answer in one paragraph

There are two concepts, and they need fewer parameters than B2 listed.
- **`create_once`** stages bytes once under a temp name, then links them to the first free name
  in a sequence the caller gives.
- **`replace`** stages bytes and renames them over one name.

Naming collapses into one parameter, an iterable of names:
- a fixed name is a one-element tuple;
- a counter is a generator;
- refusing on collision and retrying on collision are the caller catching `FileExistsError`
  when the names run out.

**Directory fsync is not a parameter.** It is a third function, `sync_directory`, that
`commandfile` calls after the link, and it already calls it on its own for a parent directory.
**Line endings are not a parameter** once the primitives take bytes (run, q4). **The temp prefix
is not a parameter**, because no reader tells one prefix from another.

What does vary for real:
- **file fsync**: `replace` has two production values, and `create_once` has one plus a
  test-only `False`;
- **the failure shape**: it stays with each caller, as D-a assigns it.

The home is **`ondisk.py`**. One copy stays separate: **`requests._append`**, whose record holds
its own number.

## (1) The copies, diffed (run: `copies.py 66af79e`)

| Copy | Naming | File fsync | Dir fsync | On collision | Failure shape (D-a) | Text → bytes | Temp name | Cleanup |
|---|---|---|---|---|---|---|---|---|
| `boardlog.append` (:961) | counter from a listing, `_name(n)` | if `durable` (default True) | no, argued | `n += 1`, unbounded | union `Written \| Unwritten`, catches `OSError, ValueError` | bytes | `.record-*.tmp` | `finally` |
| `brief.write_entry` (:196) | counter from **reading** every entry; name = `n` + kind | yes | no, argued | `n += 1`, kind re-derived | raises | text `w`, utf-8 | `.entry-*.md` | `finally` |
| `commandfile._create` (:791) | fixed name | yes | **yes**, `_sync_dir` (:777), best effort | `Unwritten(ALREADY_RECORDED)` | union; other `OSError` raises to the public writers, which catch `_WRITE_ERRORS` | bytes | `.{name}-*.tmp` | `finally` |
| `requests._append` (:1142) | counter from a listing per series; **data is a function of `n`** | yes | no | `n += 1`, re-encodes and re-stages | raises `_Unwritable` / `OSError`; the public functions map them | text `w`, ascii | `.writing-*.json` | `finally`, per attempt |
| `sessionfile._update` (:520) | fixed name | yes | no, argued | link if the name was absent, `continue` (re-read, merge, ≤3 tries); `os.replace` otherwise | union, via `update` (:478) | bytes | `.session-*.tmp` | `finally` |
| `sessions.save_overlay` (:179) | fixed name | **no** | no | n/a | swallows `OSError` | text `w`, utf-8, `json.dump` | `.sessions-*.json` | `except BaseException` |
| `settings.save` (:520) | fixed name | **no** | no | n/a | `Unsaved` for a refusal; a failed write is swallowed | text `w`, utf-8, `json.dump` | `.settings-*.json` | `except BaseException` |
| `layout_ini.keep_layout_for` (:113) | fixed name | **no** | no | n/a | raises; `app` decides to start anyway | text `w`, utf-8, `newline=""` | `imgui.ini.*.tmp` | `except BaseException` |

AST similarity of each core to `boardlog.append`'s, with locals alpha-renamed: brief 0.56,
sessionfile 0.55, commandfile 0.40, requests 0.31, and the three replaces 0.21–0.22. The unified
diffs in `copies.txt` show the same thing line by line. The create-once cores differ only in:
- the fsync guard;
- the naming expression;
- what the `FileExistsError` arm does.

The replace cores differ from each other only in the encoder and `newline=""`.

What the table means, with each claim's basis:

- **Naming is one parameter.** Every naming the copies use is a sequence of candidate names. A
  fixed name is a sequence of one. For a counter, the caller's own grammar computes the start
  (read). Brief's name per ordinal depends on `kind` and the ordinal, and a generator expresses
  that.
- **Line endings vanish under a bytes interface** (run, q4). For a CRLF ini, `layout_ini`
  writes exactly `text.encode("utf-8")`, where `text` was read with `newline=""`. For `settings`,
  `json.dump` plus `"\n"` through a text handle is byte-equal to
  `(json.dumps(...) + "\n").encode("utf-8")`. Brief, sessions and requests write text mode on
  POSIX, where `"\n"` is not translated (read). `newline=""` is a constraint on `layout_ini`'s
  *read*, and it stays there.
- **The temp prefix is not read by anything** (read). Every reader filters by its own grammar:
  - boardlog `_RECORD` (:142), `^[0-9]{8}…\.json$`;
  - brief `_ENTRY` (:57), `^\d{3}-…\.md$`;
  - requests `_NAME` (:117), with `fullmatch`;
  - commandfile and sessionfile read fixed names only.

  A dot-led `.tmp` name fails all of them. The prefixes differ for no reason any code states.
- **The failure shapes are the D-a shapes**, and every one of them is built *around* the
  write, not inside it. Each caller catches what the write raises and maps it. A primitive that
  raises therefore lets each caller keep its shape unchanged.
- **Cleanup differs, and for a reason the copies don't state.** After a link the temp name still
  exists, so `finally: unlink` is right. After a rename the temp name is free, and a `finally`
  unlink would delete whatever took the name next. That is why the replaces unlink only on
  failure. The primitives keep this split.
- **Mode** (run, q3). `settings.save` changed a 0644 `settings.json` to 0600: `mkstemp` makes
  0600 and the rename carries it. No copy varies this, so it is not a parameter. It is a live
  fact, and `replace`'s docstring should state it.

### Two defects the diff turned up

- **`brief.write_entry` can give two entries one ordinal** (run, q1). It finds its start by
  `read_entries`, and that function skips an entry it cannot read (`except OSError: continue`,
  brief.py:306). Make `001-correction.md` unreadable and the next default write lands as
  `001-amendment.md` beside it. Its docstring (:211-213) promises the opposite: "an entry never
  lands below one that was there when its write began".

  The same start makes one non-UTF-8 entry block every later write. `write_entry` raises
  `UnicodeDecodeError` (run, q2), a `ValueError` its docstring (:227) does not name. `boardlog`
  and `requests` start from a listing and have neither defect.

  The fix is to start from a listing that matches `_ENTRY`, as `count_entries` already does. Pin
  it with a failing test first. That is its own commit, before brief adopts `create_once`.
- **Two docstrings describe brief's old write** (read).
  - `brief.py:16-21` (module) says each entry is "renamed over its target --
    ``settings.save``'s primitive". Since 7165768 that is false.
  - `boardlog.py:18-21` says `brief.write_entry` "guards with ``exists()`` before
    ``os.replace``". That is no longer true, and the sentence is now history (CLAUDE.md).

  Each is rewritten in the commit that moves its module onto the primitive.

## (2) The primitives, in `ondisk.py`

```python
@dataclass(frozen=True, slots=True)
class Linked:
    path: Path
    passed: int  # names in the sequence that were taken before this one


def create_once(directory: Path, names: Iterable[str], data: bytes, *, durable: bool) -> Linked:
    ...  # FileExistsError when every name is taken; OSError otherwise; the temp is always gone


def replace(target: Path, data: bytes, *, durable: bool) -> None:
    ...  # OSError, with the old file whole and no temp left; the result is mode 0600


def sync_directory(directory: Path) -> None:
    ...  # best effort; never raises
```

How each copy calls them (read; nothing here is built):

| Copy | Call |
|---|---|
| boardlog | `create_once(d, (_name(n) for n in count(start)), data, durable=durable)`, then `ordinal = start + linked.passed` |
| brief | `create_once(d, (f"{n:03d}-{kind or (PREMISES if n == 0 else AMENDMENT)}.md" for n in count(start)), data, durable=True)` |
| commandfile | `create_once(d, (name,), data, durable=True)`, with `FileExistsError` → `Unwritten(ALREADY_RECORDED)`, then `sync_directory(d)`. `write_request` keeps its own `sync_directory(d.parent)` |
| sessionfile | absent: `create_once(d, (FILE_NAME,), data, durable=True)`, with `FileExistsError` → `continue`. Present: `replace(path, data, durable=True)` |
| sessions, settings | `replace(target, data, durable=False)`, inside their existing `except OSError` |
| layout_ini | `replace(path, patched.encode("utf-8"), durable=False)` |

Why these parameters and no others (EoP: "limiting ourselves to a single variety of interface
would be a false economy", and its converse):

- **`names: Iterable[str]`** carries the naming and the collision policy at once. Refuse,
  retry-with-merge and bump are what the caller does with a one-name sequence or an endless one.
  Separate `counter=` / `on_collision=` parameters would encode three policies the caller already
  expresses in two lines. The sequence is consumed lazily, so brief's kind is derived per ordinal
  as it is today.
- **`durable` on `replace`** is real production variation. `sessionfile` fsyncs. `settings`
  and `sessions` run on the UI thread (their docstrings: theme change, bookmark click). Nothing
  argues the absence of fsync in `settings`, `sessions` or `layout_ini`; it is unstated, not
  decided. The parameter is keyword-only **with no default**, so each caller states its choice
  and the migration does not change it. Whether those three should be durable is an open
  question; see Open.
- **`durable` on `create_once`** has one production value, True. `False` is passed only by
  tests (eight sites: `test_boardlog` ×6, `test_boardsync:1534`, `test_provenance:68`), through
  `boardlog.append`'s public `durable`. It stays because `boardlog.append`'s signature has it,
  and its docstring argues the cost (milliseconds per record). Dropping it is a separate
  decision, and the price is eight test edits.
- **No `sync_directory=` flag.** Only `commandfile` syncs a directory. It does so twice, and
  once on a directory it did not write a file into (`write_request` syncs `directory.parent`).
  The dir sync is a separate step that follows a successful link, so making it a function the
  caller sequences is exact. A flag would cover one of the two uses.
- **No temp prefix.** The reason is above. The primitives write `.writing-*.tmp`, as
  `ondisk.TEMP_PREFIX`. The nine tests that plant a leftover temp under an old prefix switch to
  that constant (see pins). Otherwise they would keep passing while no longer planting what the
  writer leaves, which is STYLE §2's "a test's name is a claim".
- **No `mkdir`.** Modes and policy differ: `commandfile` makes 0700 and only `write_request`
  creates. `layout_ini` never creates. The others use `parents=True`. That stays with the
  caller.
- **Raising, not a union.** D-a assigns the shape by what *the caller* must do. These
  primitives' callers are the eight shell functions, and each already maps a raise to its
  shape. A union here would be the generic wrapper STYLE §1 refuses.

**Is `ondisk.py` the right home? Yes.** Its requirement is the same: `os` and `tempfile`, no
snapshot, no thread, no ImGui (T3). Its docstring already rests on the writes: every record it
reads "is written by linking or renaming a regular file into place". Today that sentence is a
claim about other modules. With both writes in the file, it becomes the contract between
producer and consumer in one place. The docstring widens from "one bounded read" to "the three
disk primitives every record goes through", and it stays imported by name
(`from .ondisk import …`), as `read_regular` is. No module moves, so D-b needs no rename table.

### Reasoning that moves into `ondisk`, and reasoning that stays

Everything below was read at 66af79e and is carried as reasoning, not quoted as history.

Into `create_once`:
- `os.link` fails on a name that exists, atomically, in the kernel, whatever is under it (a
  file, a link, a FIFO). A check before a rename cannot give that guarantee (boardlog :966-968,
  commandfile :795-796, brief :204-207).
- A name appears complete or not at all. A writer that dies before the link spends no name
  (requests module :24-29).
- `durable` is the difference between surviving the process and surviving the machine. Without
  it a power loss can leave a name over a short file (boardlog :984-989; the requests comment at
  :1187-1189).
- A temp file a killed process leaves behind is never removed here, because another writer's
  write in flight has the same shape (boardlog :991-996, brief :223-225).
- The local-filesystem assumption: a network filesystem can report a link as failed after it
  landed, and the record is then written twice (requests module :32-36). This now holds for
  every adopter, not only requests.
- The cost is a filesystem with hard links (requests :29-30).

Into `replace`:
- The temp file goes in the target's directory because rename(2) is atomic only within a
  filesystem (settings :526-529, sessions :183-185).
- An interrupted write leaves the previous file whole, not truncated (all three).
- The result is mode 0600 (q3).
- The temp file is unlinked only on failure (§1).

Into `sync_directory`:
- Best effort. The name is already there and cannot be taken back, so a failure here is not a
  failure to write (commandfile :779-781).

Stays with each caller, because it argues a choice the caller made:
- `boardlog`: never fill a missing ordinal; start past the highest name listed. Records over
  `MAX_RECORD_BYTES` are refused. The directory is not flushed: losing the newest names leaves a
  consistent prefix. `Unwritten` is a value because the caller is on the frame path.
- `brief`: kind is part of what collides. Raise, not swallow, because premises that fail to
  persist are invisible. The directory is not flushed.
- `commandfile`: every file is created once and nothing is overwritten, because these records
  say what ran outside the sandbox. Directories are 0700. Hence the dir sync.
- `sessionfile`: link when absent, so a name free at read time is never taken from whatever
  appeared since. Last writer wins otherwise. The directory is not flushed.
- `settings`, `sessions`: swallow, because the UI thread must not lose a frame. `settings`
  keeps its two `Unsaved` refusals.
- `layout_ini`: `newline=""` on the read, insert-only, raise and let `app` decide.

## (3) Migration order, pins first, patch sites

Churn is commits touching the file up to 66af79e (run, `git log`); `churn.csv` was taken at
b72a48a and is one round older. The order is by churn, coldest first. Each step is one commit,
with `make check` against the Round 2 result.

| # | Step | Commits | Why here |
|---|---|---|---|
| 0 | Pins only (below), plus `ondisk.create_once` / `replace` / `sync_directory` with their own tests | ondisk 1 | Proves the primitives before anyone uses them |
| 1 | `layout_ini` → `replace(durable=False)` | 1 | Coldest. Raises, so the neutral shape. An existing failure test (`test_layout_ini:214`) |
| 2 | `sessionfile` → both primitives | 2 | Next coldest, and the only caller of both: it proves they compose |
| 3 | `commandfile` → `create_once` + `sync_directory`; `_create` and `_sync_dir` go | 4 | The one dir-sync caller; needs pin P2 |
| 4 | `sessions` → `replace(durable=False)` | 4 | Cold. Needs pin P3 |
| 5a | `brief` start from a listing (defect fix, failing test first) | 5 | Its own commit, so the behaviour change is not hidden in a move |
| 5b | `brief` → `create_once`; module docstring rewritten | | |
| 6 | `boardlog` → `create_once`; :18-21 rewritten | 8 | Hot, and B1 (journal) will move it again. Do this before B1, so B1 moves a module that is already short |
| 7 | `settings` → `replace(durable=False)` | 16 (9 in 30 d) | Hottest. Last |

**Pins to write first, each mutation-tested against the current line (STYLE §2):**

- **P1. Directory fsync in `commandfile`** (read: no test greps for `O_DIRECTORY`, `_sync_dir`
  or `fsync` in `tests/test_commandfile.py`). Patch `os.fsync` and record which descriptors are
  directories. Assert:
  - each `_create` syncs its directory after the link;
  - `write_request` also syncs the parent;
  - a sync that raises still returns `Written`.
- **P2. File fsync before the link in `commandfile`.** Same gap. The other create-once callers
  already have one: `test_boardlog:911`, `test_brief:454`, `test_requests:54`,
  `test_sessionfile:844,867`.
- **P3. A failed replace leaves the old file whole and no temp** for `settings` and `sessions`.
  Their `test_save_is_atomic_and_leaves_no_temp_files` (test_settings:159, test_sessions:178)
  only checks after a *successful* save. `test_layout_ini:214` is the model.
- **P4. The temp prefix.** The nine planting sites are:
  - `test_boardlog:1020,1124`;
  - `test_brief:213,514`;
  - `test_sessionfile:742`;
  - `test_requests:459`;
  - `test_boardsync:698,1102,1291`.

  Each switches to `ondisk.TEMP_PREFIX` in the commit that moves its module. The planted name
  must still be shaped like a real leftover.
- **P5. `test_layers` keeps seeing the writers.** Its disk scan matches direct calls
  (`_DISK_READS`, test_layers:455). Every adopter keeps at least one disk call outside its writer
  (run, an AST scan of the eight modules: boardlog `os.listdir` ×2, brief 3, commandfile 5,
  requests 1, sessionfile 1, sessions 1, settings 1, layout_ini's read stays in
  `keep_layout_for`). So `disk_readers()` does not change. It is cheap to make that structural
  anyway: add `read_regular`, `create_once` and `ondisk.replace` as dotted names to the scan. Bare
  `?.replace` would match `str.replace`. This also closes the Round 2 follow-up about
  `read_regular`.
- **P6. The q1 defect**, as a failing test before step 5a.

**Patch sites that move: none** (run: grep of `tests/` for `setattr` on `os`, `tempfile` or
`<module>.os` with `link`, `fsync`, `replace`, `mkstemp`, `fdopen`, `unlink` or `open`). There
are 23 sites across:
- test_sessionfile ×8, test_brief ×5, test_requests ×5, test_boardlog ×2;
- test_layout_ini ×1, test_app ×1, test_boardsync ×1. The boardsync one is `os.open` at :1460,
  not a write.

Every one patches the `os` module object itself. `layout_ini.os` *is* `os`
(test_layout_ini:223, test_app:4238), so a patch reaches `ondisk` unchanged. Nothing patches
`tempfile.mkstemp`, `commandfile._create` or `_sync_dir`. **The condition is that `ondisk` calls
`os.link`, `os.fsync` and `os.replace` through the module attribute**, never
`from os import link`. A test should hold that, or the next reader will "tidy" it. Two patches
become inert but harmless: `test_brief:383` patches `os.replace`, which brief no longer calls.

## (4) Where a copy stays separate

- **`requests._append`.** The record holds its own number (`make(serial)`), so the bytes
  change with every name tried, and each attempt re-encodes, re-checks the size and re-stages.
  That is a different algorithm from stage-once-link-many. `names` would have to become
  `Iterable[tuple[str, bytes]]`, a generalisation with one client, and that client is unwired
  (plan B2). It stays as it is. If it is ever wired, revisit with that pair form and its own
  read-back proof. Its docstring reasoning is already copied into `create_once` above, and it
  keeps its own.
- **`commandfile.open_log`.** It does an `O_EXCL` create of a *stream* that a running process
  writes into. There is no whole data to stage. It shares the create-once guarantee, not the
  algorithm.
- **`sessionfile`'s read-merge-retry loop.** Only the two writes move. The loop is the
  session file's policy.
- **`boardsync._carry`'s `os.rename`.** It moves a directory during legacy migration. It is not
  a write of bytes.
- **Each counter's start.** boardlog's listing, brief's (after 5a) and requests' per-series
  listing each use their own name grammar. A shared "next ordinal" would need the grammar as a
  parameter, which is the parameter blow-up the plan already refutes for `bound()` and the
  task-graph walk.

## (5) What would refute this, and what was not run

**Refuted if any of these holds:**
- After any step, `make check` differs from the round's baseline in anything but the new pins.
- A reader is found that distinguishes temp prefixes, or tooling outside `pptmstr/` that globs
  for them. Only `pptmstr/` and `tests/` were searched.
- A caller is found that needs to stage once and link under *different* data. That would put
  `requests` back in.
- `ondisk` turns out to need anything beyond `os`/`tempfile`/`pathlib`/`dataclasses`/
  `itertools`, which would break T3 for its readers.
- A patch site is found that binds `os.link` / `os.fsync` / `os.replace` at import, in
  `pptmstr/` or `tests/`. None was found (`from os import` and `from tempfile import` grep empty
  in `pptmstr/`).
- On macOS, `os.link` into an APFS directory does not fail with `FileExistsError` on a taken
  name. Not run there.

**Not run:**
- No primitive was written. The call shapes in §2 were never executed. `Linked.passed` arithmetic
  for boardlog is reasoned, not tested.
- The power-loss claims, including whether `replace(durable=False)` can leave an empty
  `settings.json` or `imgui.ini`. They are read from the docstrings and from general rename(2)
  semantics. That ext4's `auto_da_alloc` usually masks this is relayed knowledge, not measured.
- No probe ran on macOS or a second Python. The probes ran as a non-root user (q1 depends on
  that).
- `make check`, mypy and black were not run: no code under `pptmstr/` or `tests/` changed.
  `copies.py` and `probes.py` were not type-checked.
- The temp-prefix claim covers readers in `pptmstr/` only.
- The 23-site patch grep matched `setattr(<os-ish>, "<name>")` forms. A `monkeypatch.setattr`
  with a dotted string target for these names would be missed. None was seen in
  `test_dotted_patches.csv`'s domain, but that CSV was not re-run at 66af79e.

## Open

- **Should `settings`, `sessions` and `layout_ini` fsync?** Their silence is not a decision.
  - `layout_ini`'s docstring (:122-123) says a crash "leaves the old file whole". That holds for
    a process crash. For a power loss it holds only if the filesystem orders the rename after
    the data.
  - The cost of fsync is a disk flush on the UI thread for `settings` and `sessions`.
  - The migration keeps `durable=False`, so the question stays visible at three call sites. It
    is the operator's call.
- **Should `replace` keep the target's mode?** Today every replace produces 0600 (q3). That is
  harmless for pptmstr's own files, and nothing argues it either way.
- **Drop `create_once`'s `durable`?** Its only `False` callers are tests.
