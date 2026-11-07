# The commands say what they record from a pure function, as the other journals do

**Dated:** 2026-11-07 · **Status:** design for B4's second half, from
`2026-11-06-modules-are-named-for-what-they-require.md`. Nothing is built. One question is
for the operator (see "D-c, read against this"). · **Asked by:** board task `r3-b4`, under
the build rounds the operator authorised (premise 001). · **Evidence:** read at 66af79e,
plus one probe and six mutation runs (marked *run*). The probe and the mutation copy are in
`$TMPDIR/b4/` and are not in the repository.

The spec asked for "the H3 discussion". No section of the 2026-11-06 plan, or of any file
under `planning/`, is labelled H3 (grep). It is read here as the `###` heading that holds
B4, "Survives with conditions".

## The answer in one paragraph

`app._record_commands` diffs two snapshots on purpose, and that is the right shape for
commands. Unlike a board fact, each record it queues is fully determined by one command
record's change. A diff also sees every arm that touches `commands`, without needing a case
for it. That guarantee is *stronger* than the one a closed match gives. So B4 does not give
commands a fourth `outcomes` with a closed match. It moves the diff into `command.py` as
`owed_records(before, after, *, restoring)`. The function is typed on the weakest thing it
needs: two command maps and one bit. `app` keeps the one `isinstance`, the
`CommandFiles` calls, every `AppState` mutation and the failure handling. The observable
writes are unchanged on every path that does not raise (run: 21 steps × 2, 0 differences).

## 1. What it reads and writes at 66af79e

`_record_commands(state)` (app.py:575-620) returns an `Observer`. `_observer` (app.py:1395-1418)
calls it fourth, after the board, channels and fleet halves. Each half has its own handler, so
none of them can fail another.

**Reads**
- `before.commands is after.commands`. If the two are the same object, it returns at once
  and touches nothing else.
- `state.command_files`. It reads this once per step and checks only whether it is `None`.
- `isinstance(intent, CommandsRestored)`. Nothing else is read from the intent.
- `after.commands`, in insertion order, with `before.commands.get(id)` for each entry.
- For each command: `.state`, `.end` and `.run`. `now` is never read.

**Writes, per command in `after.commands`, in that order**

| Condition | Write |
|---|---|
| New in `after`, and `intent` is `CommandsRestored` | `state.command_restored.add(id)`. Happens whether `files` is set or not. |
| New in `after`, not a restore, `files` is set, state is `ASKED` | `state.command_pending.add(id)`, then `files.request(command)`, which queues `request.json`. |
| In both, `files` is set, `was.end is None`, `command.end` is set, `command.run is None` | `files.end(command)`, which queues `end.json`. In practice this is a refusal or a withdrawal. |

**On any exception**
- If the reason differs from the last one, it sets `state.command_observer_failed = reason`,
  then logs `LOG.error("command", ...)` and `LOG.debug(traceback)`.
- It never raises. `_record_board`'s docstring says why: `apply_all` would abandon the batch.

**What it does not write**
- `run.json`, `started.json`, and the `end.json` that follows a run. The runner writes all
  three.
- The `end.json` for `NotStarted`. `_end_unstarted` (app.py:442-459) queues that one directly,
  with `run=None` substituted. In the store, that command keeps its run, so the observer's
  `run is None` test leaves it alone.

The task text (relayed) was right on all four points. It left out one write:
`command_observer_failed`.

## 2. Is the snapshot diff deliberate? Yes, and it should stay

The reasoning is stated in the code (app.py:581-586):

> Derived from the two snapshots and not from the intent, so a request the store refused
> writes nothing: a command that appeared is asked, and gets its `request.json`; one that
> gained an end with no run was refused or withdrawn, and gets its `end.json`.

That reason alone does not pick a diff over a match. `boardfacts` also avoids writing for a
refused intent, by reading `after` inside each case. Two other arguments do pick the diff.

**The diff can name the record. For boards it cannot.** `boardfacts` (boardfacts.py:5-10)
uses a match because "a difference between two snapshots cannot name a record on its own":
whether a release or a reclaim happened, and who acted, are on the intent. Commands are
different:
- Both records the observer writes take only the `Command`
  (`_Write = Callable[[Command, Path | None], ...]`, app.py:263).
- Which record is owed follows from the `(run, end)` pair. `Command`'s docstring
  (command.py:845-852) says that pair "is the whole of the state".
- So for commands, the intent carries nothing the records need. The one exception is
  provenance: whether a record came from a file.

**The diff closes the hazard that the closed match only fences.** `boardfacts`' safety
argument (boardfacts.py:13-19) is that "an arm of the reducer that changed a board fact with
no case here would persist nothing while every test of the reducer passed". A closed match
turns that into a type error *when the intent is added*. After that, it relies on someone
putting the intent in the right arm. A diff over `commands` cannot miss such an arm: every
arm that rebinds `commands` (store.py:1484-1614 today; `commands=` appears nowhere else in
`_apply`) is observed whether or not anyone thought of it. A closed match over all 57
members of `Intent`, the 49 that are not command intents returning `()`, would replace that
guarantee with a weaker one.

`fleetsync` already mixes the two shapes. It uses a closed match for runs, which an intent
names, and a diff for fleet membership, with the comment "the comparison is the definition"
(fleetsync.py, in `changes`). Commands are the case where the comparison is the definition
for every record.

**The one bit that does come from the intent stays explicit.** One alternative was rejected:
deriving `restoring` as "appeared and not ASKED". That rests on two reducer invariants:
- `_with_restored` refuses open commands (store.py:3727-3731);
- `CommandRequested` always builds `ASKED` (store.py:1491-1501).

Neither is a statement of provenance. Provenance is the intent's to state.

### D-c, read against this

D-c says "every `outcomes` is a closed match" (2026-11-06, Decided). B4's own text says to
move `_record_commands` "beside the other three `outcomes`", so the plan's author expected a
fourth `outcomes`.

This design does not build one:
- `owed_records` is not an Intent→facts map.
- It is not named `outcomes`.
- It does not take an `Intent`.

Read literally, D-c does not reach it. But if D-c was meant as "every observer half is a
closed match on `Intent`", this design contradicts it, and that is the operator's call. It is
**flagged, not settled**. The cost of the other reading is a match that lists all 57
members to compute one boolean. That list would need editing on every new `Intent`, and it
would replace a guarantee by construction with one by review.

## 3. Home, name and shape

```python
# pptmstr/command.py
@dataclass(frozen=True, slots=True)
class RequestOwed:      # request.json: a command asked in this window
    command: Command

@dataclass(frozen=True, slots=True)
class EndOwed:          # end.json: ended with no run (refused, withdrawn)
    command: Command

@dataclass(frozen=True, slots=True)
class FromFile:         # appeared from a session's files; nothing is written
    command_id: CommandId

Owed = RequestOwed | EndOwed | FromFile

def owed_records(
    before: Mapping[CommandId, Command],
    after: Mapping[CommandId, Command],
    *,
    restoring: bool,
) -> tuple[Owed, ...]: ...
```

**Why `command.py`.** The naming test asks what a module requires. This function requires
only `Command` values.
- `command.py` already claims "Nothing here performs IO, reads a clock, or draws"
  (command.py:5). It imports only `commandtext` and `shellscan`, both pure, so the function
  adds no import beyond `collections.abc.Mapping`.
- `commandfile` would file it under a stronger requirement (disk). It would also make it
  unimportable by anything held to the store's no-disk rule.
- A new `commandsteps.py` would add a file for about 25 lines. The command\* family is split
  by layer, and this function belongs to the pure layer, which already exists.
- `command.py` is cold (plan rule 4), so the move adds no contention.

**Why the parameters are the maps, not the snapshots.** This follows EoP's "weakest
requirement". It also lets the unit tests build inputs that the reducer never produces
today, and two of the guards can only be pinned with such inputs (see M2 and M3 below).

**The shell that stays in `app._record_commands`.** The name and the docstring's "It does
not raise" paragraph stay. The "Derived from the two snapshots" paragraph moves to
`owed_records`.

```python
def observe(before, intent, after, now):
    if after.commands is before.commands:
        return
    try:
        files = state.command_files
        restoring = isinstance(intent, CommandsRestored)
        for owed in command.owed_records(before.commands, after.commands, restoring=restoring):
            match owed:
                case FromFile(command_id):
                    state.command_restored.add(command_id)
                case RequestOwed(asked):
                    if files is not None:
                        state.command_pending.add(asked.id)
                        files.request(asked)
                case EndOwed(ended):
                    if files is not None:
                        files.end(ended)
                case _:
                    assert_never(owed)
    except Exception as exc:  # unchanged
        ...
```

`owed_records` returns `RequestOwed` whether `files` is set or not. Only the shell knows
whether a writer exists. That matches today's code: with no writer, `command_pending` is not
marked and nothing is queued.

**The one difference, by construction.** Today, derivation and writing are interleaved. With
the new shape, derivation finishes before the first write. So an exception raised *while
deriving* now leaves no writes for the step, where today it leaves the writes queued so far.
Two facts limit this:
- Derivation reads attributes of frozen records and does mapping lookups. Nothing in it
  raises today (read).
- Today's raising paths are in the writes: `files.request` → `_submit` → `Thread.start`.
  Those stay in the fold, in the same order, so their prefix behaviour is unchanged.

`fleetsync.changes` and `channels.outcomes` both return tuples that a fold consumes, so this
matches them. If the lead holds the must-not-break to the letter on the derive-raise path too,
make `owed_records` a generator. That is a one-word change. I recommend the tuple.

### Pins, written first

Each pin is written against 66af79e and must pass there, before any line moves.

| # | Pin | File | Status today |
|---|---|---|---|
| P1 | `"pptmstr.command"` enters `PURE`, with the claim "Nothing here performs IO, reads a clock, or draws" (command.py:5). The move then cannot bring a clock or IO into `command.py`. | `tests/test_layers.py` | Not listed today; only `commandtext` is. A grep finds no `time.` or `open(` in command.py. |
| P2 | The observer's failure path, through `apply_intents`. A `command_files` whose `request` raises the same error on two steps gives the following: `command_observer_failed` is set, `LOG.error` fires once, both intents are applied, and the fleet half still submits. Name it after the hazard: `test_a_command_record_that_cannot_be_queued_is_said_once_and_the_batch_is_kept`. | `tests/test_app.py` | **Untested.** No test names `command_observer_failed` (grep). |

These are written with the function, in the same commit, in `tests/test_command.py`. Each
case is named after what it refuses:
- the same mapping object gives `()`;
- an appeared `ASKED` command gives `RequestOwed`;
- an appeared closed command with `restoring=False` gives `()`. This is the M3 survivor below;
- an appeared command of any state with `restoring=True` gives `FromFile`, and never
  `RequestOwed`;
- an end gained with no run gives `EndOwed`;
- an end gained after a run gives `()`;
- an end *replaced* on a command with no run gives `()`. This is the M2 survivor below;
- a command that is in `before` but not in `after` gives `()`;
- several appearances come back in `after`'s insertion order.

**The end-to-end tests that already pin the writes** (run, mutation on a scratch copy of 66af79e,
`tests/test_app.py` + `tests/test_command_rows.py -k "command or restore"`, 188 tests):

| Mutation of today's `_record_commands` | Killed by |
|---|---|
| M1: drop `command.run is None`, so an end after a run also gets `end.json` | `test_an_approved_command_reaches_the_shell_byte_for_byte_in_the_sessions_directory` |
| M2: drop `was.end is None` | **survives**: the reducer never replaces an end on a command with no run |
| M3: drop the `ASKED` test | **survives**: the reducer only adds an unasked command through a restore |
| M4: never treat a step as a restore | `test_a_start_reads_each_recalled_sessions_commands_back_...`, `test_a_resume_reads_the_sessions_commands_back_...` |
| M5: never mark `command_pending` | six tests, among them `test_a_command_whose_request_never_reached_the_disk_is_not_sent_to_run` |
| M6: drop the identity short-cut | survives, and is equivalent: it saves only the scan |

M2 and M3 are guards against reducer states that do not occur today. Only the pure unit
tests can construct those states, which is part of the case for the move. After the build,
re-run M1–M5 against `owed_records`. M2 and M3 must then fail in `test_command.py`.

Also unchanged, and they must pass without edits:
- `test_a_refusal_and_a_withdrawal_are_each_written_as_an_end_with_no_run` (test_app.py:7859);
- `test_a_request_the_store_refuses_writes_no_file` (:8236);
- `test_a_command_asked_in_this_window_is_not_marked_as_read_back_from_a_file` (:8225);
- the restore assertion in `test_command_rows.py:1376`.

### Move list (one commit)

1. `pptmstr/command.py`: add `RequestOwed`, `EndOwed`, `FromFile`, `Owed`, `owed_records`, and
   the `Mapping` import. The derivation's docstring comes from app.py:581-586 and cites
   boardfacts.py:5-10 for the contrast.
2. `pptmstr/app.py`: replace `_record_commands`' loop with the fold above, and add the four
   names to the `from .command import (...)` block at app.py:60. `CommandState` and
   `CommandsRestored` stay imported, because other code uses them.
3. `tests/test_command.py`: add the unit cases above.
4. `tests/test_layers.py` (P1) and `tests/test_app.py` (P2). These land first, or at the top of
   the same commit.
5. `planning/2026-11-06-modules-are-named-for-what-they-require.md`. D-b applies to live plans.
   - Line 166 lists `app._record_commands` among the `outcomes` implementations.
   - Line 171 says it "diffs snapshots ... and mutates `AppState` while it observes". Both
     need correcting: the diff is now `command.owed_records`, and the `AppState` mutation
     stays in `app` on purpose.
   - Add a line under a Round 3 outcomes heading.
   - No rename table is needed. `_record_commands` keeps its name and module; only its body
     changes.

**Patch sites: none.**
- No test monkeypatches `_record_commands`, `_observer`, or anything that moves (grep of
  `tests/` and `scripts/`).
- The one nearby patch, `monkeypatch.setattr(commandfile, "write_request", ...)` at
  test_app.py:~7712, targets `commandfile` and is untouched.
- No shim is needed, because no name moves (plan rule 1).
- `CommandFiles` stays in `app.py`. Moving the writer is a separate move into the thread
  layer, and it touches `app.py`, the hottest file. This task did not ask for it.

## 4. What would refute this

- **A command record whose content or name depends on who acted, or on what was asked rather
  than on what the store settled.** One example: a future "re-ask" that ends a command and
  reopens it under the same id. At that point the intent decides the record, and a closed
  match is right, as it is for `boardfacts`.
- **A second intent that brings commands in from a file.** `restoring` is one `isinstance`.
  A new restore-like intent would be read as "asked in this window". Its closed records would
  still get no `request.json`, because of the `ASKED` test, but `command_restored` would miss
  them, and their rows would claim the asker may not have read the result. This is the
  strongest argument for the closed match. It is the residual risk this design accepts, and
  the operator may weigh it differently (see D-c).
- **The probe's equivalence breaking on sequences it did not try.**
  - It applied one intent per `apply_all` call. The observer is called per step either way.
  - It did not exercise `SessionRecalled`, sub-agent askers, or the unsupervised "looked
    again" end.
- **P1 failing at 66af79e.** That would mean `command.py` is not pure as its docstring
  claims, and it would then be the wrong home.

## What was run, and what was not

**Run**
- `$TMPDIR/b4/probe_b4.py`. It reimplements `owed_records` and the fold, and runs them beside
  the real `app._record_commands` on one real `Store`. It drove 21 steps covering:
  - asked, refused-by-store, duplicate id, refused, refused again, withdrawn;
  - approved, stop asked, ended with `Exited`, read, ended `NotStarted`;
  - two restores, the second a no-op;
  - withdrawn after a restore.

  It ran with `files` set and with `None`. It compared the calls on a recording `CommandFiles`,
  plus `command_pending` and `command_restored`, after every step. Result: **0 differences in
  42 step comparisons**, and 8 calls queued. A copy of the probe with `command.run is None`
  removed reported 2 differences, so the probe can see a divergence. The final store held
  each command in the state the steps intended.
- **The six mutations above**, on a scratch copy of `pptmstr/` and `tests/`, with
  `.venv/bin/python -m pytest`. The unmutated baseline gave 53 passed for `test_app -k
  "command or restore"`. Each mutated run covered 188 tests in `test_app` + `test_command_rows`.

**Not run**
- No code under `pptmstr/`, `tests/` or `scripts/` was changed, so `owed_records` does not
  exist yet. The probe tested a reimplementation, not the code that will be committed.
- P1 and P2 are not written.
- `make check` was not run, because nothing was changed.
- The mutation runs covered only the `-k "command or restore"` subset of two files.
- No second Python version, and no macOS.
