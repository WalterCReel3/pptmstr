# UI saves leave the frame

**Dated:** 2026-11-09 · **Status:** design only. Nothing is built. Board task
`r5-offthread-design`, for operator decision 3 in
[decisions owed after Round 4](2026-11-09-decisions-owed-after-round-4.md). · **Evidence:** every
file:line is at **3e6c6be** (`git show 3e6c6be:path`). The interim `durable=True` for both saves
landed after 3e6c6be (0293417, another builder's); nothing below depends on it.
Probes p1, p3, p4 and p5 ran on 2026-11-09 with `.venv/bin/python -I` against the worktree, from
`$TMPDIR`, and were deleted afterwards. Their method is under "How the probes were run". Every
claim is marked **(run)** or **(read)**.

## The answer in one paragraph

Both saves go to one new `SerialWorker` held by a small shell module, `prefsync.PrefWriter`. The
UI thread submits and returns. The worker runs today's `settings.save` and
`sessions.save_overlay` with `durable=True`. Each save comes back as a value, and the frame folds
it into one `AppState` field. The status bar and LOG read that field. It is not a store intent:
settings are kept out of the Snapshot on purpose (I1). Two queued saves of one file **coalesce to
the newest**. `SerialWorker` does not coalesce, and it should not learn to. The settle skips any
item that is no longer the newest for its key. At exit, `close` waits `CLOSE_TIMEOUT`, and the
newest change per file that did not land is logged to stdout with the worker's reason. Building
this exposes one defect that exists today: a bookmark clicked while a session scan is running is
lost on the next click (p4, run). An async save widens that window from the scan's duration to
the scan plus the save (p3, run). The picker fix is the first move.

---

## 1. What each save does today (read)

### `settings.save`

- **Its one caller is `app._save`** (`app.py:4394-4407`). That is called only from
  `_apply_settings` (`app.py:4288`), and every setting change ends there: the menu, the dialog,
  `_switch_wrap`, `_switch_hide_ended`, `_set_typeset` (`app.py:4332, 4336, 4366, 4391, 4992`).
  `tests/test_app.py:3328` pins exactly one `settings_mod.save(` in `app`.
- **What it does** (`settings.py:520-562`). It re-reads the target to check `board_log`
  (`:553`), then calls `mkdir` and `ondisk.replace(..., durable=False)` (`:557-559`).
- **Two outcomes, and one is silent.**
  - *Refused*: it returns `Unsaved(path, why)` (`:554-555`) when the run's read could not tell
    what `board_log` says, or when the file there now cannot be read. `Unsaved.told` is the
    operator-facing sentence (`:507-517`).
  - *Write failed*: the `OSError` is swallowed (`:556-561`). The docstring gives the reason: "a
    read-only config directory must not take down a frame" (`:549-550`). STYLE.md §1 cites this
    as its example of a failure that "costs only a preference" (`STYLE.md:112-113`).
- **What the caller does.** `_save` latches the first `Unsaved` into `AppState.unsaved`
  (`app.py:982-985`, `:4406-4407`) and logs `refused.told` once at ERROR (`:4408`). The status
  bar keeps saying `settings not saved: <why>` for the rest of the run (`app.py:4576-4580`). The
  docstring's reason (`:4398-4403`): a refused change stays on screen, so nothing else would show
  that it is gone at the next start.
- **Rate.** Every settings control is a checkbox or a combo, with "no slider, no text field"
  (`ui/settings_dialog.py:32-35`), and `_apply_settings` does nothing for equal settings
  (`app.py:4285-4286`). So a save happens once per click and never per frame.

### `sessions.save_overlay`

- **Its one caller is `SessionPicker.toggle_bookmark`** (`ui/launcher.py:531`), from the row's
  `+`/`*` button (`:3222-3223`). `sessions.set_title` exists (`sessions.py:221`) and has **no
  caller** in `pptmstr/`. The decisions file's "a bookmark or rename click" overstates it: only
  the bookmark is wired.
- **What it does** (`sessions.py:176-201`). It writes the whole overlay the picker holds, through
  `mkdir` and `ondisk.replace(..., durable=False)`.
- **No refusal exists.** Every `OSError` is swallowed (`:196-201`), and the return is `None`.
  `toggle_bookmark` updates the row in place whether the write landed or not (`launcher.py:532-535`).
  A bookmark that failed to save looks set until the next start.

## 2. Does `SerialWorker` fit? (read; behaviour in p1, run)

**What it gives** (`serialworker.py`):

- **Order, and no coalescing.** `submit` queues a `(key, item)` pair (`:91-104`). The one thread
  settles the pairs in submission order (`:146-160`). The key is passed through to `settle` and
  `given_up` and nothing else: no dedup, no merge. "Order is the whole guarantee" (`:11`).
- **`settle` must be total, and the worker does not enforce it** (`:16-22`). An exception ends
  the thread, and every later save waits unreported until `close`.
- **`close(timeout)`** waits at most `timeout` (`CLOSE_TIMEOUT = 5.0`, `:52`). It then gives up
  everything pending: the item in flight as `IN_FLIGHT` ("may still reach disk") and the rest as
  `NOT_TAKEN_UP` (`:119-144`). It returns everything not yet drained. A save after `close` is
  reported `AFTER_CLOSE` (`:96-99`).

**Last-write-wins needs nothing beyond this.** Ordered settles of whole-file replaces leave the
newest submission on disk, provided it settles. Correctness does not need coalescing.

**Should two queued settings saves coalesce to the newest? Yes,** for two reasons that appear
only when a save is slow:

1. **The shutdown bound.** Without coalescing, N queued saves are N fsyncs. Behind writeback, one
   fsync of a 1 KB file was measured at up to 3.63 s (decisions file, decision 3, run). Three
   clicks in that state already exceed `CLOSE_TIMEOUT`. p1(a) ran the uncoalesced shape: three
   saves, the first held in flight, `close(0.2)`. It gave up all three, and **after `close`
   returned, the stale first save landed while the newest never did** (run). Coalesced, at most
   two writes are owed per file: the one in flight and the newest.
2. **Honest exit reports.** Each superseded item given up at `close` would be logged as a lost
   change, although a newer change to the same file supersedes it.

**Coalesce in the settle, not in `SerialWorker`.** The board and channel writers append records.
For them every item is a fact, and dropping one is data loss. Teaching the worker to coalesce
would put a mode into the B3 primitive that two of its three users must never switch on. The
settle can do it on its own:

- `PrefWriter` keeps `latest: dict[key, item]` under its own lock. `submit` stores the item as
  latest, then calls `SerialWorker.submit`.
- `settle(key, item)` returns `(Superseded(key),)` without I/O when `latest[key] is not item`.
  Otherwise it writes. `SerialWorker` allows the settle its own locks (`serialworker.py:21-22`).
- p1(b) ran this shape: five saves, the first held in flight while the other four arrived. It
  wrote `[1, 5]` and reported 2-4 as superseded (run).

The two keys, `"settings"` and `"sessions"`, share one worker and one thread. A slow settings
fsync can therefore delay a bookmark write. Nothing orders the two files against each other, and
one thread is enough for one click at a time. `FleetWriter` is a coalescing writer too, but its
merge is key-by-key inside a record (`fleetsync.py:601-639`), not newest-wins. B3 kept it out of
`SerialWorker` because its loop is a different algorithm. Reusing it here would be the wrong
model.

**The item** is a sum type, matched in the settle with `assert_never` (STYLE.md §1):

```python
@dataclass(frozen=True, slots=True)
class SettingsSave:
    settings: Settings
    unread: BoardLogUnread | None   # AppState.board_log_unread, captured at the click

@dataclass(frozen=True, slots=True)
class OverlaySave:
    overlay: Overlay                # a Mapping; the picker hands over a fresh dict on every toggle

PrefSave = SettingsSave | OverlaySave
```

## 3. How a refusal reaches the operator after the click

**Through an `AppState` field the frame reads, not through the store.** Settings "never enter a
Snapshot (I1)" (`settings.py:14-15`). The refusal already lives in `AppState.unsaved`
(`app.py:982-985`), and the status bar reads it there. A store intent would carry operator
preferences into the world's history, for no reader the store has.

**The shell does the I/O, and its outcome comes back as a value.** These are the shapes, and
STYLE.md §1's rule picks each one: "the caller must act on a refusal: return a union named for
the domain".

- `settings.save` returns `Unsaved | NotWritten | None`. `NotWritten(path, reason)` is new. It
  carries the `OSError` text that is swallowed today.
- `sessions.save_overlay` returns `NotWritten | None`. `NotWritten` is the same shape, defined
  once in `prefsync` or in `ondisk`. That is a naming call for the builder.
- The settle wraps the call in `except Exception`, as `FleetWriter` does
  (`fleetsync.py:843-853`), and maps whatever escapes to `NotWritten`. That makes the settle
  total, which `serialworker.py:16-22` requires.
- The report is `Saved(key) | Superseded(key) | Refused(key, Unsaved | NotWritten) | GivenUp(key,
  reason)`.

**The frame folds the reports.** It drains beside the other writers (`app.py:1131-1135`), into
a pure fold:

```python
def noted(status: PrefStatus, reports: tuple[PrefReport, ...]) -> tuple[PrefStatus, tuple[str, ...]]
```

`PrefStatus` is one frozen `AppState` field and replaces `unsaved`:

- `unsaved: Unsaved | None` keeps today's semantics. It latches at the first refusal and lasts
  the run, because a refusal for the first reason cannot clear (`settings.py:546-547`).
- `settings_unwritten: NotWritten | None` and `overlay_unwritten: NotWritten | None` are
  transient. They are set by a `NotWritten` and cleared by a later `Saved` of the same key, since
  a full disk can recover.

The fold returns the log lines to say: one at each transition from clear to set, so a
read-only directory is not logged on every click. The status bar shows each set field, beside
today's segment (`app.py:4576-4580`), in the same words. A bookmark that did not save is shown
in the status bar because the launcher is a modal that is gone by the time the report lands.

**Writing failures surface. This is a choice, and it changes STYLE.md.** Swallowing was argued
from the frame: "must not take down a frame" (`settings.py:549-550`, `sessions.py:186-187`). Once
the write is a value from a worker, reporting it takes down nothing. Decision 3 makes durability
the point of these saves, so a write that silently fails contradicts the reason for the change.
The `settings.save` bullet in STYLE.md §1 (`:112-113`) loses its example in the same commit.
`brief.write_entry` (raise) and `boardlog.append` (union) stay as they are. If the operator
prefers to keep the swallow, delete `NotWritten` and the two transient fields. Nothing else in
the design moves.

**The refusal stays in one path.** The first `Unsaved` reason, `unread` not None, is known on the
UI thread without I/O and could be answered inline. It is not. Two paths to one refusal is the
"kept true by whoever remembers" shape that STYLE.md §1 warns about. The cost: a refusal that is
certain shows one frame late.

## 4. Shutdown

`_close_prefs(state)` goes in `_shut_down` beside `_close_board_log` (`app.py:5196`). It comes
ahead of the pool's shutdown, for that function's reasons (`app.py:5232-5235`), and it raises
nothing (`:5237-5238`). It calls `writer.close()` with the default `CLOSE_TIMEOUT` (5.0 s,
`serialworker.py:52`). That lengthens the quit only when a save is outstanding, and then by at
most 5 s.

**Closed within the bound:** every queued save is written. `close` returns the last `Saved` or
`Refused` reports, and a `Refused` is logged as it would be in a frame.

**On timeout,** every `GivenUp` is reduced to the newest per key. Older ones are superseded
whether or not they land. One `LOG.error` per file follows, and it is written to stdout because
the window is gone (`log.py:54`). Two cases:

- **`NOT_TAKEN_UP`**: "the last settings change was not saved at exit: still queued; the file
  holds an earlier one".
- **`IN_FLIGHT`**: "the last settings change may not have been saved at exit: its write had
  begun". p1(a) shows why the message cannot say "saved": the in-flight write can be an *older*
  change than the newest given up (run). With coalescing, the in-flight item is usually the
  newest, but not always.

**What a timed-out in-flight write leaves.** The worker is a daemon thread
(`serialworker.py:32-34`), so interpreter exit stops it mid-`replace`. Before the rename, the old
file stays whole. After it, the new file is whole: the rename is atomic (`ondisk.py:151-155`).
Either way a `.writing-*.tmp` may be left in the config directory. `replace` unlinks its staged
file only on an exception it sees (`ondisk.py:177-180`), and a killed thread sees none. Nothing
reads that name, and nothing removes it. That is the same exposure `create_once` documents
(`ondisk.py:121-123`).

**Wording.** `IN_FLIGHT` says "its append had begun" (`serialworker.py:57-59`). That is wrong for
a replace. `PrefWriter.given_up` maps the reason constant to its own text, compared by equality,
so B3's text is untouched. Making the constants neutral ("its settle") is a separate edit. It
would touch the board and channel messages and any test that pins them.

## 5. Ordering against reads (read; the race run in p3 and p4)

**`settings.json`: no stale read during a run.**

- It is read at startup (`app.py:6073`, `_start` at `:5836-5889`).
- `settings.save` re-reads it to check `board_log` (`settings.py:553`). That re-read now runs on
  the worker, in order, after every earlier save has settled, so it reads pptmstr's own last
  write. That is no weaker than today.
- `settings_dialog.py:220` shows the path and does not read the file. A grep for `settings.read`,
  `settings.load` and `settings_path` finds no other reader in `pptmstr/`.
- The values in force are `state.settings` in memory (set at `app.py:4287`), never the file.
- **Test-only:** `tests/test_app.py` reads the file back right after a save (for example `:3375`
  and `:5180`, `settings_mod.load()`). Those reads must settle the writer first (§6, move 4).

**`sessions.json`: yes, and it loses a bookmark.**

- `scan_sessions` reads the overlay first (`launcher.py:134`) and then runs the slow listing
  (`:135`). The listing takes tens of milliseconds warm and is unbounded cold
  (`sessions.py:23-26`).
- The scan runs on a thread (`launcher.py:442-455`). `poll` **replaces** `self.overlay` and
  `self.rows` with what the scan read (`:505-506`).
- The next `toggle_bookmark` writes the whole overlay it holds (`:530-531`).
- So any bookmark clicked after the scan's read and before its `poll` is erased in memory, and
  then on disk by the next click.

| Probe | Shape | Result |
|---|---|---|
| p4 (run) | **today's synchronous save**; bookmark `a` clicked while a scan that had already read the file was still listing | after `poll`, memory `{}`; after bookmarking `b`, disk `{'b': …}` (**`a` lost**) |
| p3 (run) | **async save**; `a` clicked, its write held; a rescan started and finished; then the write released | after `poll`, memory `{}`; the held write put `a` on disk; bookmarking `b` then wrote `{'b': …}` (**`a` lost**) |

So the defect exists today. Its window is the listing's duration. An async save adds a second
way in: a scan that *starts* after the click, while the save is queued, also reads the old file.
On a busy disk that window is seconds.

**The fix is in the picker. It is pure, and it needs no ordering against the worker.** The
picker keeps `touched: dict[str, SessionMark]`, the marks this process set, and the newest one
per session. `poll` adopts `scanned_overlay | touched`, with touched winning, and re-applies the
result to the new rows (`dataclasses.replace` on `bookmarked`/`title`). That closes both entries,
whatever the timing, because a scan can never take back what this process set. It also keeps
picking up marks that another pptmstr process wrote for sessions this one never touched. A
"memory is authoritative after the first load" rule would drop those.

**Residual:** two pptmstr processes that both touch one session's mark are last-writer-wins on
that session, and each writes its whole overlay. That is true today as well, and this design
does not try to fix it.

## 6. Pins, move order, and what would refute this

### Pins to write first

| Pin | Hazard it names | Expected before the move |
|---|---|---|
| **Q1** `test_a_bookmark_clicked_during_a_scan_survives_the_scans_poll_and_the_next_click` | p4's loss. Pure: the scanner stub returns a pre-click overlay. | **fails today** (p4, run). Mutation-checked by construction. |
| **Q2** `test_a_bookmark_whose_save_is_unsettled_when_a_scan_reads_survives_it` | p3's loss, with a held save seam | fails until move 1 |
| **Q3** `test_the_frame_thread_never_fsyncs_a_preference` | Wrap `os.fsync` to record `threading.current_thread()`, then run `_apply_settings` and a bookmark toggle. No call may come from the calling thread. This is the property decision 3 asks for. | fails today: the interim fsyncs inline |
| **Q4** `test_queued_saves_of_one_file_write_the_newest_and_at_most_two` | p1(b) as a test: first save held, four more submitted | n/a until move 3 |
| **Q5** `test_a_refused_save_reaches_the_status_after_the_click_and_is_said_once` | today's `test_app.py:3338-3376` and `:862`, with a settle step in between | adapted, not new |
| **Q6** `test_a_failed_write_of_either_file_is_shown_and_cleared_by_a_later_save` | `NotWritten`, driven by patching `os.replace` on `os` (`ondisk.py:20-22` documents the patch site) | fails today: swallowed |
| **Q7** `test_close_on_timeout_names_the_newest_change_per_file_and_says_whether_it_may_land` | p1(a) as a test: the in-flight item is older than the newest | n/a until move 3 |
| **Q8** wiring | The frame drains the pref writer and `_shut_down` closes it, asserted as called (STYLE.md §2, "test the wiring"). Replaces the `count("settings_mod.save(") == 1` pin (`test_app.py:3328`) with "only `prefsync` calls `settings.save`". | n/a until move 4 |

### Move order

1. **Picker fix** (Q1, Q2): `touched` and the merge in `poll`. It is independent of everything
   else and fixes a defect that exists today, so it lands first and alone.
2. **The saves return their outcome** (Q6, synchronous form). `settings.save` returns `Unsaved |
   NotWritten | None` and `save_overlay` returns `NotWritten | None`. Both still run on the UI
   thread, with `app._save` folding `NotWritten`. STYLE.md §1's bullet is edited in this commit.
   The behaviour change is reviewable apart from the threading change.
3. **`pptmstr/prefsync.py`**: `PrefWriter` holds a `SerialWorker[str, PrefSave, PrefReport]`,
   with the coalescing settle, `given_up`, `PrefStatus` and the pure `noted` (Q4, Q7 unit). It
   imports `settings`, `sessions` and `serialworker`. No `tests/test_layers.py` rule forbids
   that (read). `serialworker` keeps importing nothing from pptmstr (`test_layers.py:1132`).
4. **Wire it.** `_start` builds `state.pref_writer`, under `--fake` too, since `_save` runs there
   today. `_save` submits. `launcher.draw` gains a `save_overlay: Callable[[Overlay], None]`
   seam beside `scanner`/`prober` (`launcher.py:3947-3962`), which reaches `toggle_bookmark`, so
   `ui/` still imports no writer. The frame drains, `_shut_down` closes, and the status bar
   reads `PrefStatus` (Q3, Q5, Q7 app-level, Q8). The tests that assert `state.unsaved` or read
   the file right after a click (`test_app.py:862, 3375, 5180-5188`, `test_launcher.py:606-639,
   1210`) get a settle helper first, like `_settle_channel_writer`'s `outstanding == 0`.
5. **Docstrings.** `settings.save`, `save_overlay` and `app._save` lose "runs on the UI thread"
   and "swallowed". `_apply_settings`'s "saved at the point of mutation" stays true. Decision 3
   in the decisions file is marked built.
6. `make check` against the round's baseline.

### What would refute this

- **A run-time reader of either file not found here.** The grep was for `settings.read|load`,
  `settings_path`, `load_overlay` and `overlay_path` over `pptmstr/` and `scripts/` (read). A
  reader through a literal `"settings.json"` built elsewhere would be missed. Plugins and the CLI
  do not read pptmstr's config (read, not exhaustively checked).
- **A submit from a thread other than the UI thread.** The coalescing relies on store-latest then
  submit being ordered per key. Both callers are draw-path code (read). A second submitting
  thread needs the pair under `PrefWriter`'s lock. The sketch puts it there anyway, so this
  refutes only a builder who drops the lock.
- **A caller that needs the outcome inline.** For example, a dialog that says "saved". None
  exists (read). If one appears, it reads `PrefStatus` a frame later or the design is wrong for
  it.
- **The frame still waits on the journal through some other write.** p5 ran a 400-550 ms fsync
  on a worker, and the main thread's longest gap *inside* the fsync was 1.6-4.6 ms (run). So
  `os.fsync` releases the GIL and the worker does not hold the frame. This design does not
  cover other UI-thread I/O, such as hello_imgui saving `imgui.ini` on its own schedule, which
  can still meet a journal commit. "The frame never waits on a disk" is not a claim this
  design makes.
- **Q1 passing on today's code.** Then p4's construction does not reach the race the way the
  picker meets it, and §5's diagnosis is wrong.

## Not run

- No code was written. `PrefWriter`, the item and report types, `noted` and the picker merge
  exist only as described. None has been through mypy.
- None of Q1-Q8 exists. Their predicted states come from p1, p3 and p4, and from reading.
- p3 and p4 drove the real `SessionPicker` and `sessions` at the worktree's state. That is
  3e6c6be for `ui/launcher.py`, and `sessions.py` carried the interim `durable=True` (then uncommitted,
  since 0293417), which does not change the race. The async save in p3 was a thread standing in for `PrefWriter`, not
  `SerialWorker`.
- p5 did not reproduce the multi-second small-file fsync. Its durable replace behind 200 MB of
  dirty pages took 8-15 ms. The GIL claim rests on the 200 MB fsync, not on that case. Nothing
  was run on macOS.
- The exit path was not run in the app: no window was opened, and `_shut_down` was not called.
  The timeout behaviour is p1(a) on `SerialWorker` alone.
- `make check` was not run for this design.

## Open

- **Keep the swallow, or surface write failures** (§3). The design surfaces them. Reversing
  that removes `NotWritten` and two fields, nothing else.
- **`prefsync` as the module name**, after `boardsync`/`fleetsync`, and where `NotWritten` lives.
- **Neutral wording for `serialworker`'s reason constants.** It is a B3 edit with test fallout,
  and this design does not need it.

## How the probes were run

All four ran as `.venv/bin/python -I <probe> <worktree>`, with the worktree put on `sys.path`.
Each wrote only under a `mkdtemp` in `$TMPDIR`, and p3 and p4 set `XDG_CONFIG_HOME` there. A
first p3 run had not set it, and its second click aimed `save_overlay` at the real
`~/.config/pptmstr/sessions.json`. The sandbox refused the write, `save_overlay` swallowed the
refusal, and afterwards no `sessions.json` or `.writing-*` file existed there (checked with
`ls`). Probe scripts and their directories were deleted afterwards.

- **p1**: `SerialWorker` at 3e6c6be.
  - (a) The settle waits on an event and then calls `ondisk.replace(durable=True)`. Three
    submits, then `close(timeout=0.2)` and the reports printed. The event is then released and
    the file read back.
  - (b) A coalescing settle (`latest[key] is item`). Five submits, the first allowed to be
    taken up before the rest, then `close(2.0)`.
- **p3**: `SessionPicker` with `sessions.save_overlay` replaced by a thread held on an event
  for the first click. Steps: `toggle_bookmark`, `request_scan` and join, `poll`, release,
  `toggle_bookmark` on the second row, `load_overlay`.
- **p4**: the scanner reads the overlay, signals, and waits for the click before returning. A
  synchronous `toggle_bookmark` happens in that gap, then `poll` and a second toggle.
- **p5**: a worker writes 200 MB and fsyncs it while the main thread loops with a 1 ms sleep,
  recording each gap. Gaps are reported over the whole run and over the fsync's interval alone.
  It ran two rounds, then one durable `replace` of 17 B on a worker behind 200 MB of dirty data.
