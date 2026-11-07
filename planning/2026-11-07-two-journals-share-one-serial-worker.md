# Two journals share one serial worker

**Dated:** 2026-11-07 · **Status:** design only. Nothing is built. This is B3 of
[the refactor plan](2026-11-06-modules-are-named-for-what-they-require.md), round 3 (board task
`r3-b3`). · **Asked by:** the operator, through the round-3 build rounds
(premises `001-build-rounds.md`). · **Evidence:** difflib over both classes at **66af79e**, run
2026-11-07 with `.venv/bin/python -I` (the scripts were in `$TMPDIR` and are not kept; the method
is under "How the diff was taken"). Probe p09 (`2026-11-06-refactor-graph/probes/p09_writer_close.py`,
run at b72a48a) executed the one behavioural difference. Everything else is **read**.

## The answer in one paragraph

`BoardWriter` and `ChannelWriter` share a serial loop of five members (`submit`, `drain`, `close`,
`_run` and the queue state). After names are aligned, those members differ in one way that matters:
which reason `close` gives the oldest pending item. Everything else in either class is domain
logic, and the two classes share none of it. So the shared piece is a
**serial worker**: one thread that settles items in submission order, reports each one, and closes
within a bound. It knows nothing about disks, logs or records. It goes in `pptmstr/serialworker.py`
and imports nothing from pptmstr. Each writer **holds** one; neither subclasses it. The writer
passes in two callables and a thread name: `settle(key, item) → reports`, and
`given_up(key, item, reason) → report`. Channels adopts board's precise in-flight close.
`FleetWriter` and `CommandFiles` stay out: their loops are different algorithms, not variants of
this one.

## (1) The diff, member by member (run, 66af79e)

Docstrings were stripped, the code was dedented, and each pair was compared with
`difflib.SequenceMatcher` and `unified_diff`:

| Member | Ratio | Changed lines | What differs |
|---|---|---|---|
| `drain` | 1.000 | 0 | Identical. |
| `close` | 0.737 | 10 | Names, payload type, **the position-0 reason** (D1, D2). |
| `submit` | 0.583 | 10 | Names, payload type, thread name (D4, D5). |
| `_run` | 0.485 | 17 | Loop form (no difference), `_in_flight` (D1), `continue` vs `return` after an abandoned settle (D3). |
| `__init__` | 0.303 | 23 | Domain state (D6, D7). The queue state is the same apart from `_in_flight`. |
| `_settle` | 0.000 | 28 | Wholly domain-specific (D8, D9). |
| `_append` | 0.000 | 43 | Wholly domain-specific. |

Board only: `directory`, `adopt`, `outstanding`, `_mark`. Channel only: `refusal`, `_sweep`,
`_record`, `_links`, `_read`.

### Every semantic difference, named

- **D1. The position-0 reason at `close`.** This is the one difference in behaviour.
  `boardsync.py:615` gives "had begun … may still reach disk" only when
  `position == 0 and self._in_flight`. `channels.py:1048` gives "may have begun … may still reach
  disk" for position 0 **unconditionally**.
  - Board's answer is exact because of the lock. `_run` checks `_abandoned` and sets
    `_in_flight = True` in one critical section. `close` sets `_abandoned` and reads `_in_flight`
    in another. So if `close` sees `_in_flight` False, the worker will see `_abandoned` and return
    without settling the item. "Not written" is then a fact.
  - Channels reports an item the worker never picked up as one that may reach disk. p09 executed
    this. With the worker held before its first `get`, board said `'still queued when the writer
    closed; not written'` and channels said `'its append may have begun when the writer closed; it
    may still reach disk'`.
  - The window is real but narrow in production. It is open between `submit` and the worker
    reaching its lock, and between one item's settle and the next `get`. A five-second
    `CLOSE_TIMEOUT` closes it in practice. A wedged first item does not open it: that item *is* in
    flight.
- **D2. The wording for the in-flight item.** Board says "its append had begun and had not
  returned when the writer closed; it may still reach disk". Channels says "its append may have
  begun when the writer closed; it may still reach disk". Only the wording differs.
- **D3. After a settle that finished once the writer was already abandoned.** Board `continue`s:
  the next `get` returns close's `None`, or a queued item that the abandoned check then ends on.
  Channels `return`s. No report differs either way, because `close` has already reported the item,
  and both discard the settle's reports. Board's worker may live one more `get`, and it leaves
  `_in_flight` True, which nothing reads after abandonment.
- **D4. Types.** Board uses `Outcome(session_id, record, Unwritten(...))` with
  `Report = Outcome | Stopped`. Channels uses `Outcome(link, fact, NotAppended(...))` with
  `Report = Outcome | Swept`. The keys are `str` and `LinkKey`.
- **D5. The thread name:** `"boardsync"` and `"channels"`.
- **D6. Who reads `_stopped`.**
  - Board's `_stopped: dict[str, Stopped]` belongs to the worker alone.
  - Channels' `_stopped: dict[LinkKey, str]` is written by the worker and read by `refusal` from any
    thread, so it is held under `_changed`, the queue's lock.
  - Board's `_admitted` is also under `_changed`. `adopt` writes it from the owner's thread, and
    `_append`/`_mark` read it on the worker. `_append`'s read, list and re-check sequence
    (boardsync.py:723-734) needs to be atomic only against `adopt`. Nothing reads `_admitted`
    together with the queue state.
- **D7. Surface.** Board has `outstanding`; channels has no equivalent. `tests/test_app.py:9798`
  takes `writer._changed` and reads `writer._pending` to get the same answer.
- **D8. Where exceptions are caught.**
  - Board catches around `_append`, in `_settle`, and separately in `_mark`. `_mark` calls
    `self.directory` before its `try`; `sessionfile.board_dir` does no IO.
  - Channels catches inside `_append`, `_links` and `_read`.
  - In both, `settle` is total for every exception the IO can raise. In channels, `_sweep` calls
    the pure `restore` and `owes_close` outside any guard (read). If one raised, the worker would
    die.
- **D9. The stop marker.**
  - Board's marker is a separate `Stopped` report. It is not attempted (`None`) for a log this
    writer was never admitted to.
  - Channels' marker is `Outcome.marker`, and is always attempted.
  - Both are inside `settle` and stay domain logic.
- **Same in both:** `drain`. `close`'s wait (`wait_for(not pending, max(timeout, 0))`) and its
  second-call behaviour (nothing waited for, only reports since). `submit` after close (a
  "submitted after the writer was closed" report, nothing queued). Starting the worker lazily on
  the first `submit`. A daemon thread. Discarding the reports of an abandoned settle.

### A gap the diff exposes, unchanged by this design

An item that is in flight at `close` loses whatever else its settle reports. For board that is the
`Stopped`; for channels it is a `Swept` and its `SweptClose`s. Its `Outcome` is reported by `close`
("may still reach disk"). The stop marker it may then append, and the earlier links a sweep may
close, are not reported in this process. This is true on both sides today, and B3 neither fixes nor
worsens it. It is named here so that a test of the shared worker does not claim otherwise.

## (2) The shared worker

**Module:** `pptmstr/serialworker.py`. **Name:** `SerialWorker`, for what it requires. It requires a
total `settle` and a `given_up` constructor, and nothing else: no path, no log, no record type. It
is not called a writer, because it writes nothing. The refuted "four-way `SerialWriter`" was a
writer that took container, settle, latch, retry and close policy as parameters. This worker takes
none of those: it is the one loop that two writers already share line for line.

```python
K = TypeVar("K"); T = TypeVar("T"); R = TypeVar("R")

# How long ``close`` waits for queued items unless told otherwise. An item is an
# append, which takes milliseconds, so this bounds a wedged filesystem and is not a budget.
CLOSE_TIMEOUT = 5.0

class SerialWorker(Generic[K, T, R]):
    def __init__(
        self,
        name: str,                                  # the thread's name
        settle: Callable[[K, T], tuple[R, ...]],    # total: raises nothing
        given_up: Callable[[K, T, str], R],         # the report for an item not settled
    ) -> None: ...
    def submit(self, key: K, item: T) -> None: ...
    def drain(self) -> tuple[R, ...]: ...
    @property
    def outstanding(self) -> int: ...
    def close(self, timeout: float = CLOSE_TIMEOUT) -> tuple[R, ...]: ...
```

- **The body is board's.** It has `_queue`, `_pending`, `_reports`, `_worker`, `_in_flight`,
  `_closed` and `_abandoned` under one `Condition`. `close` is board's precise close. `_run` returns
  instead of continuing after an abandoned settle (D3): the shorter form, with the same reports.
  Its three reasons are the module's constants, in board's wording: "submitted after the writer was
  closed", "its append had begun and had not returned when the writer closed; it may still reach
  disk", and "still queued when the writer closed; not written".
- **Python 3.11 is the floor** (`pyproject.toml`), so this uses `TypeVar` + `Generic`, not PEP 695
  syntax. Nothing in `pptmstr/` uses `Generic` today, so this is the first. mypy checks
  `SerialWorker[str, Record, Report]` at each construction.
- **`settle` must be total, and the worker does not enforce it.** A catch-all in `_run` would have
  to report something. For channels, a fact reported as failed without stopping its link would let
  later facts append past the gap, which is the history `ChannelWriter`'s stop rule exists to
  prevent. So the guards stay where each domain's reasoning is (D8), and the module docstring states
  the requirement. Whether `_sweep`'s unguarded pure calls deserve a guard is left open below.
- **Each side keeps its records.** `given_up` is the only place the worker makes a report:
  - board: `lambda s, r, why: Outcome(s, r, Unwritten(why))`;
  - channels: `lambda l, f, why: Outcome(l, f, NotAppended(why))`.

  `Report` stays a closed union in each module, and `_read_channel_reports` keeps its
  `assert_never`. The worker only passes `R` through.
- **Composition, not a base class.** STYLE §1 rules out base classes for variants, and there is a
  concrete reason here as well. Today both domains put domain state under the queue's lock (D6).
  That works, but it lets a settle hold the lock that `close` waits on. With composition:
  - each writer gets its own `threading.Lock`: board's `_admission` for `_admitted`, channels'
    `_stopping` for `_stopped`;
  - the worker never calls `settle` with its own lock held, and the domain locks never wrap a
    worker call, so there is no lock order to get wrong;
  - nothing reads domain state together with queue state (read; D6), so splitting the lock breaks
    no invariant.
- **What each writer keeps.**
  - `BoardWriter`: `root`, `legacy`, `directory`, `adopt`, `_settle`, `_mark` and `_append`. It
    gains `self._serial = SerialWorker("boardsync", self._settle, _given_up)`, and its `submit`,
    `drain`, `close` and `outstanding` become one-line delegations that keep their docstrings.
  - `ChannelWriter`: `log`, `refusal`, `_settle`, `_sweep`, `_record`, `_append`, `_links` and
    `_read`. It gains `outstanding` by delegation.
- **`MARKER_REASON_CHARS` belongs to the record it bounds, not to the worker.** The worker never
  writes a marker. The constant moves to `boardlog`, beside `RecordingStopped` (boardlog.py:628).
  `boardsync._mark`, `channels._record` and `app.py:1944` import it from there. If B1 lands first,
  it goes wherever B1 puts `RecordingStopped`. **`CLOSE_TIMEOUT`** moves to `serialworker` as the
  default of `close`. Both restatements are deleted (`channels.py:148`, `:151`; `boardsync.py:160`,
  `:164`). Plan rule 1 forbids re-exports, so nothing re-exports them.
- **Size, honestly.** The roughly 55 code lines of loop in each writer, docstrings aside, become
  about 90 lines of module including its docstrings, plus four delegations per writer. The tree
  shrinks a little. The gain is one close that is right on both sides, and one place to pin it, not
  fewer lines.

### The alternative considered

T2 allows copies that are pinned by a test. That would mean adding `_in_flight` to `ChannelWriter`
and pinning the two loops equal with an AST test, modulo names. It was rejected:

- the pin would have to normalise names, payload constructors and thread names, so it would be a
  hand-written equivalence that is weaker than one definition;
- B1 will want a third journal writer to use the same loop.

If the generic typing turns out awkward under mypy in practice, this is the fallback, and it still
needs pins P2 and P3 below.

## (3) What changes for channels

- **The change in behaviour.** An item still pending at `close` that the worker had **not** taken up
  is now reported "still queued when the writer closed; not written", not "may have begun … may
  still reach disk". This is now a true statement: the lock argument in D1 guarantees the worker
  will not append it.
- **The wording.** The in-flight item reads board's wording (D2).
- **Who sees it:** nothing branches on the text (grep of `pptmstr/` and `tests/`, read). At shutdown
  `_close_channel_log` passes no state, so the only change is the LOG line at exit. During a run
  `close` is not called.
- **A LinkCreated mid-sweep.** If close lands while a `LinkCreated` is in its sweep, the fact itself
  has not been appended yet, but it will be: `_settle` does not re-check abandonment between
  `_sweep` and `_record`. "Had begun … may still reach disk" is therefore true of it. "Its append"
  is loose for a fact whose sweep has begun and whose own append has not. That is accepted rather
  than given a fourth reason. The worker cannot see inside `settle`, and the claim it makes, that
  the item may still land, is the one that matters.
- **The test that pins it: P2.**
  `test_channels::test_a_fact_the_worker_never_took_up_is_reported_not_written_and_never_lands`.
  Hold the worker before its first `get`, by replacing the worker's queue with one whose `get`
  blocks on an event, as p09 did. Submit two facts, `close(timeout=0.05)`, then release the worker.
  Assert:
  - both outcomes are `NotAppended` with "not written";
  - after the worker exits, `log.read(link).facts == ()`.

  Today it fails: p09 executed the position-0 reason this test rejects. After the change it passes.
  Mutation check: delete `and self._in_flight` from `SerialWorker.close` and the test must fail
  again.

## (4) FleetWriter and CommandFiles, re-checked (run, 66af79e)

Line ratios against `BoardWriter`, docstrings stripped:

| Member | FleetWriter | CommandFiles |
|---|---|---|
| `submit` | 0.261 (12 → 34 lines) | 0.167 (`_submit`) |
| `close` | 0.357 (19 → 37) | 0.071 (19 → 9) |
| `_run` | 0.209 (20 → 47) | 0.125 (`_work`) |
| `drain` | 1.000 | 0.000 |
| `__init__` | 0.233 | 0.148 |

Neither joins, because each is a different algorithm and not this one with a parameter changed:

- **FleetWriter coalesces.** Its pending store is `dict[str, _Item]`, merged per session, not a
  FIFO.
  - It has no `SimpleQueue`. The worker waits on the condition and chooses its next item.
  - It retries dropped `in_fleet` keys from `_owed`, interleaved with new work.
  - It holds work back (`_held`) without reporting it.
  - It ends on a `_finished` latch that `close` waits for, not on an empty pending queue.
  - `submit` returns a sequence number and drops silently for an unadmitted session.
  - `_in_flight` holds the item itself, because the in-flight item is no longer in `_pending`.

  Fitting it would mean parameters for the container, the choice of next item, the retry source,
  the hold, the end condition and the return of `submit`. That is the five-parameter `SerialWriter`
  the plan refuted, rebuilt.
- **CommandFiles has no pending set and no abandonment.**
  - `close` puts the sentinel and `join`s, and returns a `bool`.
  - Jobs queued at `close` keep running after a timed-out join, and nothing reports them as given
    up.
  - Reports go through a second `SimpleQueue` that the runner's callback also feeds (`report`).
  - The job carries its own write function.

  Joining would change what it promises at close: from "keep writing" to "give up and say so". That
  is a decision about `request.json`/`end.json` durability, not a refactor, and it is not B3's to
  make.
- `drain` is identical in FleetWriter as well (ratio 1.000). Five identical lines are below the
  threshold where sharing pays: T2 is satisfied by leaving them alone.

## (5) Migration

All of this is on cold files apart from the two `app.py` docstrings and one `test_app` helper.
Follow the plan's churn rule: rebase onto main first and land in one commit with no shims.

**Pins written first, against today's code.** Each passes today unless marked. Each is
mutation-checked against the **current** line (STYLE §2).

| Pin | File | Asserts | Today |
|---|---|---|---|
| P1 | `test_boardsync` | A record the worker never took up (queue held, as p09) is reported "not written", and is absent from the log after release. | passes; fails if `and self._in_flight` is deleted |
| P2 | `test_channels` | As (3) above. | **fails** (the strengthening) |
| P3 | `test_channels` | `close` while `log.append` is wedged on fact 1: fact 0 kept, fact 1 "may still reach disk", the rest "not written", none of the rest in the log after release. This is the channel twin of `test_close_gives_up_at_its_bound_…` (test_boardsync.py:761). | passes; channels has no close-path test today |
| P4 | `test_channels` | The stop marker's reason is `reason[:MARKER_REASON_CHARS]` for a failure reason longer than that. This is the twin of test_boardsync.py:449. | passes; channels' bound is unpinned today |
| P5 | `test_channels` | A fact submitted after close is reported `NotAppended("submitted after …")` and not appended. | passes; untested today |
| P6 | `test_channels` | Closing an unused writer starts no thread and creates nothing under the log's root. | passes |

**Steps.**

1. Add `pptmstr/serialworker.py` and `tests/test_serialworker.py`. The generic tests use a `settle`
   that blocks on an event and are written against the worker itself:
   - order;
   - `drain` never waits;
   - the bound on `close`;
   - the in-flight and not-taken-up windows;
   - a second `close`;
   - `submit` after close;
   - no thread when unused;
   - an abandoned settle's reports are discarded (this pins the gap above as current behaviour,
     with a name that says so);
   - `outstanding`.

   Add a rule 7 to `tests/test_layers.py`: `serialworker` imports nothing from pptmstr, the same
   form as rule 6 (`naming`).
2. `BoardWriter` holds a `SerialWorker`. Delete its queue state, `submit`/`drain`/`close`/`_run`
   bodies and `CLOSE_TIMEOUT`, and add the `_admission` lock. Patch sites:
   `tests/test_boardsync.py:807` and `:832` change from `writer._worker` to
   `writer._serial._worker`. P1's held queue changes from `writer._queue` to
   `writer._serial._queue`. Run `test_boardsync` and `test_app`.
3. `ChannelWriter` holds a `SerialWorker`, with the `_stopping` lock and `outstanding`. Delete its
   loop and both restated constants. P2 now passes. Patch sites:
   - `tests/test_app.py:9798-9799` (`_settle_channel_writer`) changes from
     `writer._changed`/`writer._pending` to `writer.outstanding == 0`;
   - P2/P6 point at `writer._serial`.
4. Move `MARKER_REASON_CHARS` to `boardlog`. Edit `boardsync._mark`, `channels._record`,
   `app.py:1944` and `tests/test_boardsync.py:449`.
5. Docstrings and plans:
   - `app.py:5216` and `:5230` cite `serialworker.CLOSE_TIMEOUT`.
   - `store.py:3418` cites `ChannelWriter._sweep`, which stays where it is: no change.
   - Under D-b, append a rename table to `planning/2026-10-17-sqlite-inspection/q1-fit.md` (P13
     cites `BoardWriter.submit`/`_run` at boardsync.py:557-573, 650-669) and to `q4-comparison.md`
     (`BoardWriter.close` waits `CLOSE_TIMEOUT`). `channel-topology/r1-code-cost.md` cites only
     `BoardWriter.submit`, which stays.
6. `make check` against the round's baseline.

**`tests/conftest.py` does not move.** Its writer-related patches (`conftest.py:605-607`:
`boardsync.legacy_root`, `channels.root`, `sessionfile._under`) are on module functions that B3
leaves where they are. No test monkeypatches either writer class, and no dotted-string patch names
one (grep, read).

## (6) What would refute this, and what was not run

**Refuted if:**

- **P2 passes against today's channels.** Then the held-queue construction does not open the
  window, and the claimed difference is not what the test sees.
- **Splitting the lock breaks something.** Any code path is found to read `_admitted` or channels'
  `_stopped` together with `_pending`, `_closed` or `_abandoned`, and to rely on one lock for both.
  Only the paths in D6 were read for this.
- **Channels needs a hook inside the worker.** For example, to re-check abandonment between
  `_sweep` and `_record`. That would be a third parameter, and the next would be the fourth. If it
  comes to that, take the pinned-copies fallback.
- **mypy cannot type the composition** without casts at the call sites. Then the fallback again.
- **A consumer depends on channels' "may have begun" text.** None was found by grep, but
  `_read_outcome` and the notices it builds were read, not run.

**Not run:**

- No code was written. `SerialWorker` exists only as the sketch above, and its typing has not been
  through mypy.
- None of P1-P6 exists. Their pass and fail states are predicted from p09's executed output (taken
  at b72a48a; the `close` diff at 66af79e reproduces p09's line for line) and from reading.
- The lock-split argument (D6) and the D3 "no report differs" claim are read, not executed.
- The FleetWriter and CommandFiles verdicts rest on the ratios above and on reading their loops.
  No adapter was attempted.
- `make check` was not run for this design.

## Open

- **Should `_sweep`'s pure calls be guarded?** `restore` and `owes_close` run unguarded on the
  channel worker. If one raised, the worker would die and every later fact would queue unreported
  until `close`. It is not B3's to decide. It is noted because B3 makes "`settle` is total" a
  written requirement, and this is the one place that requirement is met by reading rather than by
  a guard.
- **The in-flight item's other reports are lost** (see "A gap the diff exposes"). Fixing it would
  mean `close` waiting for, or a later `drain` collecting, the abandoned settle's reports. That
  changes what `close` returns and is a separate decision.

## How the diff was taken

`git show 66af79e:pptmstr/{boardsync,channels,fleetsync,app}.py`. Each class's
`FunctionDef`s were found with `ast`, their docstrings were cut by line range, each body was
dedented to its own base, and every pair was run through `difflib.SequenceMatcher(None, a, b).ratio()`
and `unified_diff`. "Changed lines" counts `+`/`-` lines, excluding the headers. This is p09's
method extended to every shared member and to the two other writers.
