# The board log is one journal of two, and the store keeps the half that reads no disk

**Dated:** 2026-11-07 · **Status:** design only; nothing is built and nothing is committed. This
is B1 of `2026-11-06-modules-are-named-for-what-they-require.md`, Round 3 (board task `r3-b1`). ·
**Asked by:** the lead, through the round-3 build rounds (premises `001-build-rounds.md`, relayed;
this worker was told not to open `~/.config/pptmstr` and did not read the brief). · **Evidence:**
`2026-11-07-journal-substrate/readback.py` with its prototype `proto_journal.py`, run at
**66af79e** on Python 3.11.2 with `.venv/bin/python`; its output is `results.txt` and the bytes
HEAD wrote are kept in `golden.json`, all beside this file. Four mutations of the measurement
were run to show it can fail (below).

A claim marked *run* was executed; everything else is **read**. File:line references are at
66af79e and each names its symbol.

## The answer in one paragraph

Do not move what the store needs; move what the store must not reach. The store imports five
names from `boardlog` (store.py:37), and all five are values a pure fold returns. What makes
`boardlog` a disk reader is a separate, smaller half: the directory, the append and the read.
That half, together with the record envelope it shares with channels, becomes `pptmstr/journal/`
(`codec.py` pure, `files.py` disk). `boardlog` keeps the board's record kinds, its kind table and
`restore`, and becomes pure. **store.py, intents.py, boardfacts.py and driver.py do not change.**
The one interface change is that `decode` and `read` take the kind table as a parameter, where
today they close over a module global. That is what lets channels stop decoding board kinds it
never wanted. The format does not change. Measured: logs HEAD wrote, board and channel, read back
through the proposed path to equal records, equal damage and unchanged files, and records written
again are byte-for-byte HEAD's (run, all 42 checks).

## (1) What is substrate and what is the board

Substrate is what a channel log uses or would need if it were not a board. Board is what only
the board's fold gives meaning to.

| Name | boardlog.py | Goes to | Why |
|---|---|---|---|
| `_ORDINAL_WIDTH`, `_RECORD` | :139, :142 | `journal/files` | the file name format of every journal |
| `MAX_RECORD_BYTES` | :149 | `journal/files` | a bound on one file, enforced by `append` and `read`; channels uses it (channels.py:660) |
| `_FIELD_TYPES` | :155 | `journal/codec` | the shapes `encode`/`decode` know |
| `RecordingStopped` | :628 | `journal/codec` | "means the same thing in either log" (channels.py:46-47); every kind table holds it |
| `Unrecognised` | :657 | `journal/codec` | built only by `decode`; channels tunnels through it today |
| `Malformed`, `encode`, `decode`, `_as` | :716, :722, :746, :792 | `journal/codec` | the envelope (`kind`, `at`, terminator, ASCII) and the field shapes, for any kind |
| `_MAX_COMPONENT`, `session_dir` | :832, :835 | `journal/files` | a session's directory under a root; brief, sessionfile, boardsync and ui use it |
| `_name` → `name`, `_ordinals`, `_first_gap` | :867, :871, :885 | `journal/files` | `name` goes public, because channels already calls it (channels.py:632) |
| `Unlisted`, `next_ordinal` | :891, :903 | `journal/files` | listing |
| `Written`, `Unwritten`, `append` | :926, :949, :961 | `journal/files` | the linked append |
| `Damage`, `Log` | :1031, :1047 | `journal/codec` | plain values. They go in the pure module because `Board.damage` holds a `Damage` and `restore_log` takes a `Log`; in `files`, the board half would import the disk half |
| `read` | :1054 | `journal/files` | the ordered, strict read |
| Record kinds `Declared` … `AnswerDelivered` | :159-:611 | stay | board facts |
| `Known`, `Record`, `_KINDS` → `KINDS`, `_HINTS` | :677, :700, :702, :704 | stay | the board's closed vocabulary. `KINDS` becomes a `journal.codec.Kinds`, and `_HINTS` folds into it |
| `Party`, `_party`, `_named` | :1106, :1109, :1121 | stay | board parties |
| `TaskVersion`, `RestoredTask`, `RestoredConcern`, `RestoredDecision`, `Board` | :1160, :1186, :1287, :1315, :1344 | stay | what the store reads |
| `restore_log`, `restore` and its helpers | :1423, :1434-:1925 | stay | the fold |
| `Clock` | :1931 | **stays**, a departure from the plan's B1 list | see below |

**`Clock` stays in `boardlog`.** The plan lists it as substrate. By requirement it is: it needs
nothing of the board. But no journal other than the board converts times, since channels has no
`Clock` use (grep, read). It is imported from `boardlog` by app.py (:1861, :1884, :2724, :2747),
boardfacts.py:54, intents.py:33 and eight test files. Moving it would edit the hottest file in the
repo for no new consumer. It moves when a second journal needs it. That is a judgment call; the
lead may overrule it, and the cost of doing so is listed under (3).

## (2) What the store needs from boardlog, and the split

`grep -n boardlog pptmstr/store.py` (run) gives two hits:
- store.py:37 `from .boardlog import Board, Party, RestoredConcern, RestoredDecision, RestoredTask`
- store.py:377 is a comment.

The uses are `Party` (:4076, :4342, :4505, :4609), `Board` (:4305 `_damage_text`), and the three
`Restored*` types as the parameters of the restore arms (:4340, :4503, :4607). **The store
needs the fold's output and nothing else**: no read, no append, no codec. The need is real. The
store turns a restored board into its own records, and those types are that contract.

The test_layers exception exists because those five names sit in a module that also calls
`os.listdir`, `os.fdopen` and, through `ondisk`, `os.open`. The scan of eager imports from
`tests/test_layers.py` (run) finds four disk readers reachable from store:

    pptmstr.approval <- pptmstr.store
    pptmstr.boardlog <- pptmstr.store
    pptmstr.ondisk   <- pptmstr.boardlog <- pptmstr.store
    pptmstr.settings <- pptmstr.approval <- pptmstr.store

`intents` also imports `boardlog` eagerly (intents.py:33, `Board, Clock`), so store reaches the
disk through it too. Today's rule checks only direct edges, so it does not see this.

**The split.** Take the disk out of `boardlog` rather than the five names out of it. After the
move `boardlog` imports `model`, `naming` and `journal.codec`, and nothing that reads disk (read:
`session_dir`'s `os` use goes with it, and `codec` needs only `json`, `math` and `dataclasses`).
Then:
- `STORE_DISK_EXCEPTIONS["pptmstr.boardlog"]` goes stale, and `test_store_imports_no_module_that_reads_the_disk`
  fails until the entry is deleted. The existing test enforces the removal.
- `pptmstr.boardlog` and `pptmstr.journal.codec` join `PURE`, each with the words of its new
  docstring. `boardlog.py:101`'s "Nothing here is in the snapshot, and nothing here reads a clock"
  becomes the claim.
- **Recommended in the same commit:** make rule 2 follow eager imports transitively. The
  `approval` entry and a `settings` entry reached through it would be the only exceptions, and both
  belong to B5. Without this, `store → intents → boardlog` is a path the test cannot see. Today it
  is a hole; after B1 it would be a guard.

The `approval` exception stays. Its pure half is B5's, as the plan's Open says.

## (3) Layout, moves and patch sites

    pptmstr/journal/__init__.py   docstring only: the package map (T6). No re-exports (Rule 1).
    pptmstr/journal/codec.py      pure: RecordingStopped, Unrecognised, Malformed, Kinds, kinds(),
                                  encode, decode(text, kinds), _as, _FIELD_TYPES, Damage, Log[R]
    pptmstr/journal/files.py      disk: MAX_RECORD_BYTES, name, _ordinals, _first_gap,
                                  session_dir, Unlisted, next_ordinal, Written, Unwritten,
                                  append, read(directory, kinds) -> Log[R]
    pptmstr/boardlog.py           pure: the board's kinds, KINDS = codec.kinds(*get_args(Known)),
                                  Record, Party, Restored*, Board, restore, restore_log, Clock

**Why the table is a parameter and not a registry.** `decode` today looks kinds up in the module
global `_KINDS` (boardlog.py:774). A registry that each journal adds to would be mutable import-time
state, and its outcome would depend on import order. Two journals with two closed vocabularies are
two values. `Kinds` is `by_name` plus `hints`, and `kinds(*classes)` builds it and always adds
`RecordingStopped`. The import-time checks at boardlog.py:703-709 (unique names, known field shapes)
move into `kinds()` unchanged. `Log` and `Kinds` become `Generic[R]` (3.11 floor, so `TypeVar`
rather than PEP 695). `decode` keeps `type[Any]` internally, as `_KINDS` does today.

**Channels: tunnelling stays, the board table goes.** `FileChannels.read` calls
`files.read(directory, codec.kinds())`, a table holding only `RecordingStopped`. Every link record
then arrives as `Unrecognised`, as it does today, and `channels.decode` lifts it. Three things
follow:
- the board-record branch in `channels.decode` (:269-270) becomes unreachable and is deleted;
- channels no longer imports a single board name;
- the no-shared-kinds assert (:228-230) moves to a test that imports both. That property still
  matters (a misplaced record stays `Unrecognised` in either log), but checking it is no reason
  for channels to depend on the board.

The alternative is to register `LinkCreated` and `LinkClosed` in channels' own `Kinds` and delete
channels' copy of the codec (`Malformed` :235, `decode` :258, `_as` :290-:308). That is the better end state
under T2. It is **not** part of B1, because it changes refusal words that tests pin. For example, a
link record with no `charter` reads today as `link-created.charter: not text` (run), and under the
board's decoder it would read `link-created: no charter`. It needs its own task and its own test.

**Move list: production.** Each site calls through the module object (`files.append(...)`), never
a from-import of a function, because 20 monkeypatch sites patch module attributes (below).

| File | Sites | Change |
|---|---|---|
| `boardlog.py` | whole module | drop the moved names. Import `from .journal import codec` and spell `codec.RecordingStopped` / `codec.Unrecognised` in `Record`, so that `boardlog.RecordingStopped` stops resolving and nothing reaches substrate through the board |
| `boardsync.py` | :154-155, :243, :252, :301, :308, :706, :720, :729, :745; doc :20, :67, :94, :116, :194, :474, :491 | `files.read(d, boardlog.KINDS)`, `files.append`, `files.next_ordinal`, `files.session_dir`, `files.Unwritten/Written/Unlisted`, `codec.Log` |
| `channels.py` | :80-84, :156, :228-230, :241-270, :521, :595-596, :608, :632, :660; doc :40-47, :246-247, :293, :525, :537, :610, :649 | as above; `Unlisted` re-export (:84) now from `journal.files`; it predates B1 and is not patched |
| `brief.py` | :46, :144; doc :138, :204 | `files.session_dir` |
| `sessionfile.py` | :70, :347, :434; doc :201, :345 | `files.session_dir` |
| `ui/session_details.py` | :41, :264, :270, :279 | `files.session_dir`, `files.next_ordinal` |
| `app.py` | :1943, :1949, :1956, :1987, :5254; doc :1930 | `codec.RecordingStopped`, `files.Written/Unwritten`. **`Board`, `Clock` and `Party` stay**, so six lines change in the hottest file |
| doc only | commandfile.py:160, approval.py:596, bus.py:267-284, model.py:2357/:3941/:3946, driver.py:1253, intents.py:839, settings.py:324-330 (a key spelling, **not** a citation: leave it) | per D-b |
| unchanged | store.py, intents.py (imports), boardfacts.py, driver.py, model.py | — |

If the lead overrules on `Clock`, add these: app.py :1861, :1884, :2724, :2747; boardfacts.py:54;
intents.py:33; and the eight test files that from-import it.

**Move list: tests.**
- **Patch sites (Rule 1), all moving to `journal.files` or `codec`:**
  - `test_boardlog.py:575` and `:883` (`MAX_RECORD_BYTES` → `files`);
  - `test_app.py:2885` (loop over `append/read/next_ordinal`), `:3191`, `:4459`;
  - `test_boardsync.py:140`, `:166`, `:167`, `:191`, `:237`, `:278`, `:441`, `:470`, `:491`, `:576`, `:651`,
    `:772`;
  - `test_boardsync.py:1462`, which patches `read, next_ordinal, decode, restore_log`. It becomes
    `files.read`, `files.next_ordinal`, `codec.decode` and `boardlog.restore_log`, and `files.read`
    must call `codec.decode` as an attribute or that patch does not intercept.
- **`_KINDS` patches** (`test_boardlog.py:1291`, `:2907`; `test_peer_concerns.py:311`;
  `test_peer_tools.py:1665`) simulate an older build by patching the table and then calling
  `boardlog.decode`. With the table a parameter they need no patch:
  `codec.decode(text, codec.Kinds(older, boardlog.KINDS.hints))`. `:1291` removes
  `RecordingStopped`, which `kinds()` always adds, so it builds `Kinds` directly.
- **Dotted-string patches:** none on `boardlog` (grep for `"pptmstr.boardlog"`, run; the only hit
  is test_layers.py:491's exception key).
- **Name uses** (counts, run):
  - `test_boardlog` (138 tests) uses `read` 36, `encode` 29, `append` 25, `decode` 22, `Log` 11,
    `next_ordinal` 9 and `session_dir` 4, plus the from-import at :29.
  - Split it. A test that touches only substrate names goes to a new `tests/test_journal.py`;
    one that restores stays.
  - `test_processes_appending_at_once_lose_nothing` (:796) finds the repo root as
    `Path(boardlog.__file__).parents[1]`; from `journal/files.py` that is `parents[2]`.
  - Every other file listed by `grep -rl boardlog tests` edits only its `encode/decode/append/read/
    Malformed/Unrecognised/RecordingStopped/Written/Unwritten/Unlisted/Log/Damage` spellings.
    `test_channels.py:21` from-imports three of them.
- **`test_layers.py`:** delete the `boardlog` exception (:491). Add `boardlog` and `journal.codec`
  to `PURE`, and optionally make rule 2 transitive (above).
- **Live planning files** citing `boardlog` get a rename table, per D-b. There are 20 outside
  the archive (grep, run); `sqlite-inspection/q1-fit.md` has 41 hits.

**Order against the sibling designs.**
- B2 (`2026-11-07-an-atomic-write-is-create-once-or-replace.md`, step 6) puts `boardlog.append`
  onto `ondisk.create_once` *before* B1. That order is fine. `append` moves with its body
  unchanged whatever that body is, and `golden.json` checks that its bytes did not change.
- B3 (`2026-11-07-two-journals-share-one-serial-worker.md:173`) moves `MARKER_REASON_CHARS`
  "beside `RecordingStopped`". After B1 that is `journal/codec`.
- B1 itself is cold apart from its six app.py lines, so it can go early in the round.

## (4) The measurement

The substrate cannot exist without a code change, so `proto_journal.py` stands in for it. It holds
only the two functions whose bodies change, `decode(text, kinds)` and `read(directory, kinds)`.
Everything that moves unchanged is imported from HEAD's `boardlog`, because a move does not alter a
body. The writer used throughout is therefore HEAD's.

`readback.py` (run, 66af79e, `results.txt`: **42 ok, 0 failed**):

1. **The bodies are HEAD's.** After four stated substitutions (`kinds.by_name.get(` ↔
   `_KINDS.get(`, `kinds.hints[cls]` ↔ `_HINTS[cls]`, `decode(text, kinds)` ↔ `decode(text)`,
   `name(` ↔ `_name(`), the AST of each prototype body unparses identical to HEAD's. The proposed
   channel read is HEAD's `FileChannels.read` body with the same swaps.
2. **A board log HEAD wrote:** 133 records, 23,538 bytes. That is every one of the 21 board kinds
   six times, plus `RecordingStopped` and an `Unrecognised`. The values include a lone surrogate, a
   NUL, astral and non-ASCII text, `\u0000` written out, `2**63`, `1e-300`, negative times and
   empty tuples. The proposed read equals HEAD's read, records and damage alike. Reading changed no
   file. Every record re-encodes to its file's bytes. A log appended again from the proposed read
   is byte-for-byte HEAD's, names and bytes. The bytes are stored in `golden.json`.
3. **Hand-written files no current append produces:** keys out of order, extra keys, an integer
   time, defaulted fields left out, raw non-ASCII. Equal.
4. **Damage, 17 cases, equal to the word.** The cases: torn, a missing ordinal, not JSON, not an
   object, no kind, a bad or overflowing time, a wrong field shape, a missing required field, not
   UTF-8, nesting past recursion, over the size limit, a symlink, a directory and a FIFO under a
   record's name, temp and foreign names beside the records, an unlistable directory.
5. **A channel log HEAD wrote through `FileChannels.append`:** 19 facts. The proposed read
   equals HEAD's `Read`, each file is the fact's encoding byte for byte, and `golden.json` holds
   it. `judge` agrees. Four damaged channel logs give equal `broken` text.
6. **The one predicted difference, pinned.** A board record in a channel's directory is carried
   as `Unrecognised` either way, and `judge` agrees. HEAD's text is a *re-encoding*:
   `{"kind": "task-claimed", "at": 2.0, …, "by_id": []}`. The proposed text is the file's own:
   `{"at": 2, …, "extra": 1}`. Only the in-memory `Unrecognised.text` changes, never a file, and
   the proposal is the one that matches what the docstring promises (boardlog.py:666-667, "the
   record's JSON as it was read").

**Mutations (run), to show the measurement can fail:**
- a kind table without `RecordingStopped`: parts 2, 3 and 4 fail;
- an `encode` that sorts keys: only `golden[board]` fails. Without the golden, a writer compared
  with itself would pass. That is why `golden.json` exists;
- `Malformed` reasons upper-cased: part 4 fails;
- channels read under the board's table, i.e. HEAD's behaviour: part 6 fails.

**What the real check is.** After the build, `readback.py` imports `pptmstr.journal.codec` and
`pptmstr.journal.files` in place of `proto_journal`. Part 1 is then moot, and the rest runs
against `golden.json`, which holds HEAD's bytes. Then run `--existing DIR` over a **copy** of the
operator's config directory: `boards/`, the sessions' `board/` directories and `channels/`. That
is the condition's "existing logs", and it was **not run**.

## (5) What would refute this

- After the build, `readback.py` against `golden.json` reports any mismatch, or `--existing` on
  the operator's logs reports a log whose proposed read differs from HEAD's.
- mypy rejects `Generic` `Log`/`Kinds` in a way that forces casts at the call sites. mypy was not
  run on the prototype, which is typed loosely (`Record = Any`).
- A consumer depends on channels re-encoding a board record (part 6). None was found by grep, but
  that is a read, not a run.
- The transitive scan shows `boardlog` still reaching a disk reader after the move. For example,
  `model` gaining an eager import of one would put the store exception back by another route.
- A `boardlog.X` patch site the greps above missed. A missed site means a test patches a module
  attribute the code no longer calls, so the test passes while asserting nothing (STYLE §2, "test
  the wiring").

## Not run

- The build itself. `make check`, mypy, ruff and black on any real `pptmstr/journal/`.
- `readback.py --existing` on any real log. The operator's logs live under `~/.config/pptmstr`,
  which this worker was told not to touch.
- mypy on `proto_journal.py`/`readback.py`. Ruff reports style findings in `readback.py`
  (line length, an import order, `zip` without `strict`), none of which affects a result.
- Python other than 3.11.2, and macOS.
- The import-closure scan read the working tree. Its `store.py` held another worker's uncommitted
  edit, which changes only stdlib imports (`git diff`, run), so the closure is HEAD's. Every
  store.py line cited above was checked against `git show HEAD:pptmstr/store.py`.
- The premises brief was not read (see the header). The authorisation is relayed from the lead.
