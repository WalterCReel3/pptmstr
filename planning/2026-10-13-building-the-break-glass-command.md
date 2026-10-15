# Building the break-glass command: the steps

**Dated:** 2026-10-13 · **Status:** built, uncommitted, twice reviewed and twice fixed, seen drawn by nobody — see
"Built, and what two reviews changed"; the plan below was made by reading and is kept as written · **Decisions it builds:**
[`2026-10-13-a-break-glass-command-the-operator-runs.md`](2026-10-13-a-break-glass-command-the-operator-runs.md)
· **Nearest precedent in the tree:** decisions (`model.Decision`, the decision arms in
`store.py`, `bus.raise_decision`, `ui/decision_view.py`)

Every claim about the code is to be checked against the file before it is built on.

## Steps

| id | What becomes true | Files written | After |
|---|---|---|---|
| C1 | The record exists as a pure value | `pptmstr/command.py`, `tests/test_command.py` | — |
| C2 | A command string can be shown injectively and broken into segments | `pptmstr/commandtext.py`, `tests/test_commandtext.py` | — |
| C3 | The on-disk format reads and writes | `pptmstr/commandfile.py`, `tests/test_commandfile.py` | C1 |
| C4 | The runner runs, bounds, kills and closes | `pptmstr/commandrun.py`, `tests/test_commandrun.py` | C1, C3 |
| C5 | The reducer holds commands; no obligation kind yet | `model.py` (`Snapshot.commands`, `commands_readable_by`), `intents.py`, `effects.py`, `store.py`, `boardfacts.py`, `fleetsync.py`, their tests | C1 |
| C6 | The row's words and widgets exist, with no caller | `pptmstr/ui/command_view.py`, `tests/test_command_view.py` | C1, C2 |
| C7 | The three tools' handlers and reply text exist, unregistered | `pptmstr/commandbus.py`, `tests/test_commandbus.py` | C2, C5 |
| C8 | `ObligationKind.COMMAND` exists and every surface draws it | `model.py`, `store.py`, `theme.py`, `digest.py`, `ui/inbox.py`, `ui/detail.py`, `ui/rail.py`, `ui/fleet_view.py`, `app.py` (a state field and pass-through only), their tests | C5, C6 |
| C9 | The tools are offered, to contained sessions only | `bus.py`, `approval.py`, their tests | C7, C8 |
| C10 | The shell runs what the store orders, restores, and shuts down | `app.py`, `tests/test_app.py` | C4, C8 |
| C11 | Agents and the operator are told | `templates.py`, `tests/test_templates.py`, `README.md` | C9, C10 |

C1 and C2 start together; then C3→C4, C5, C6 and C7 overlap. C5→C8→C10 is the contended chain.
Each new intent needs a case and a generator entry in both `boardfacts` and `fleetsync`.

## One property per step

- **C1.** `edited` is derived (the string run differs from the string asked), and the state is derived from which of `run` and `end` are set.
- **C2.** `visible()` is injective and emits only printable ASCII and newline; the segments plus the gaps between them reproduce the command exactly.
- **C3.** Every file is created once, with exclusive create; nothing is overwritten or deleted.
- **C4.** The shell's `-c` argument is the approved string unchanged, and nothing starts before the record of the run is durable.
- **C5.** Only an intent from the operator starts, refuses or ends a command; the string that runs is the one on that intent; a second run in one session is a no-op.
- **C6.** The confirm view renders the frozen draft through `visible()`, and the second press sends that same object.
- **C7.** No reply line carrying command, reason or output lacks a mark, and the worst-case reply fits the bus's board-read bound.
- **C8.** No key acts on a command row, and the rail does not read a command as "blocked".
- **C9.** A session with no containment never has the tool.
- **C10.** Every order to run ends in exactly one end record, including with no runner (`--fake`), a dead thread, and shutdown.
- **C11.** The briefing names the tool only where the tool exists.

## The record, its states, the disk

`command.Command`: `id` minted by the handler (`cmd-<utc>-<hex>`); `asker`, `asked`, `reason`,
`expects`, `asked_at` from the request; `run: Run | None` (`text, shell, cwd, timeout_s,
started_at`); `stop_asked_at`; `end: End | None` (`outcome, at, duration_s, output_bytes, tail`);
`first_delivered_at`.

Outcomes: `Exited(code)`, `TimedOut`, `Cancelled(by: operator | shutdown)`, `NotStarted(reason)`,
`Refused(reason)`, `Withdrawn`, `Lapsed`, `EndUnknown`. States: asked → running → ended. Asked
and running owe a row (running, because cancel must be reachable even when the asker is gone).
Ended owes none; it is listed in DETAIL with "not yet read by <role>".

Intents: `CommandRequested`, `CommandApproved(id, run, timeout_s)`, `CommandRefused`,
`CommandWithdrawn`, `CommandStopAsked`, `CommandEnded`, `CommandsRead`, `CommandsRestored`.
Effects: `CommandSettled`, `CommandsDelivered` answer the bus; `CommandToRun`, `CommandToStop`
are orders the shell routes to the runner.

Disk: `sessions/<sid>/commands/<id>/` holds `request.json`, `run.json`, `end.json`, `output.log`
(at most the first 8 MiB) and `output.tail` (the last 64 KiB, only when cut). Each JSON carries
`format: 1`; unknown keys ignored; a newer format shown and never touched. The run thread writes
`run.json` and `end.json` itself, so shutdown needs no frame. Restore runs on a startup worker
and emits `CommandsRestored`: request-only becomes `Lapsed`, run-without-end becomes
`EndUnknown`; **nothing is restored open, so no file can produce a runnable row**.

Bounds (proposals): command 2,000 characters; reason and expects 1,000 each; tail 4,000
characters or 60 lines; 3 open per asker; 8 across a session's sub-agents.

Tools: `request_command(command, reason, expects)` refuses, in words: empty; over length; a NUL
or lone surrogate; **a first character `-` or `+`** (a shell would read the string as options);
caps reached. `read_commands(command_id?)`: a list shows no output; by id, everything.
`withdraw_command(command_id)` only while asked. All three unparked; the row is the review.

Marks: the asked text is `| ` (an agent wrote it); an edited run string is `> ` (the operator
wrote it); output gets a third mark and its own legend — bytes a program wrote, which may be
text from files an agent controls, and are not instructions.

Runner: `Popen([shell, "-l", "-c", text], stdin=DEVNULL, stdout=PIPE, stderr=STDOUT,
start_new_session=True, cwd=…, env=…)` — an argument list, so no quoting layer stands between
the approved string and the shell. The shell is `$SHELL` if absolute and executable, else the
password entry's, else Run is not offered and the row says why; a non-POSIX shell runs it anyway
and the row names the shell and says the breakdown is bash's reading. The environment is
pptmstr's minus `VIRTUAL_ENV` and its `bin` on `PATH` (a login shell does not remove these by
itself). A reader writes output up to the cap, then discards and counts. Timeout or cancel:
`killpg` TERM, two seconds, KILL. After the shell exits the pipe is drained for a second and
closed, so the runner never waits on an escaped child. Shutdown kills every group and records
`Cancelled(shutdown)`. Closing the session does not kill; the row stays.

**Changed after the reviews of the built runner (2026-10-13), by the lead's ruling and the
reviewers' findings:** when the shell exits by itself, the rest of its process group is ended
too — TERM, the grace, KILL — so "ended" means nothing of the command is left, and a command
meant to leave something running has to say so (`setsid`) in text the operator reads; the end
records how many leftover processes were ended. The grace after TERM is the whole group's, read
from `/proc`, not the shell's. The timeout and the kill live on a thread that output handling
cannot block. `started.json` records pid, process group and start time, so a command that
outlived a pptmstr that died is shown at the next start as still running and unsupervised, with
the line that kills it, and holds its session's one slot until it is gone. `BASH_ENV`, `ENV` and
every non-absolute `PATH` entry are removed from the environment. Not covered: a pptmstr killed
outright still orphans the command until the next start; no `/proc`, no survivor lookup; two
windows on one session (the second would call the first's running command unsupervised).

**Stated plainly:** a child that calls `setsid` survives all of this; a pptmstr killed outright
leaves the command running with no timeout, and the restore shows `EndUnknown`.

## Who wrote each text, who reads it, what could be tried

| Text | Written by → read by | What a contained agent could try | Answer, and what is left |
|---|---|---|---|
| Command | agent → operator, then the shell | hide bytes (bidi, zero-width, look-alikes, `\r`, trailing space); hide a second command; forge pane lines; a leading `-` read as options | `visible()` escapes everything outside 0x20–0x7E and doubles the backslash; its own bordered block; newlines marked; the record is immutable; leading `-`/`+` refused at request and at edit |
| Breakdown | pptmstr → operator | substitution, `&`, heredoc, compound commands, `eval`, `sh -c`, aliases | whole segments shown, never first words alone; flags from a quote-blind scan. **Advisory: it weakens the gate if the operator reads it instead of the block** |
| Reason, expects | agent → operator | persuade; mimic the standing line | the author bar; bounded; the row's summary built from structure only. Persuasion remains |
| Edit | operator → shell | the edit box shows raw text, so invisible characters stay invisible; the asker withdraws mid-edit | the first press freezes the draft and re-renders it through `visible()` with a fresh breakdown; a withdrawal makes the approval a no-op |
| Output | process → asker, its root, the operator | inject via file content; a worker reaches its lead; fill the disk; hang | a third mark and legend; escaped; tail only; the list shows none; 8 MiB cap; the pipe closed after exit. The worker-to-lead channel through approved output remains |
| Files | pptmstr → pptmstr | forge a record | restore never yields an open row. Unmeasured: that the sandbox cannot write `~/.config/pptmstr` |
| Process | shell → machine | daemonise; outlive the timeout or the quit | group kill; escapees survive, and the row and README say so |

## The planner's decisions, each with what was rejected

1. **Write-once files, the state derived from which exist** — against one rewritten record,
   which needs ordering across two threads and an overwrite rule.
2. **The directory named by id** — against a running number, which needs a listing and races
   across two windows.
3. **Closing the session leaves a running command; quitting kills it** — against killing on
   both. A half-finished install on close is the worse surprise.
4. **Run and cancel reach the runner as effects of operator intents** — against a pane callback
   straight to the runner. One path, in store order, testable with no runner.

## Recommendations on what the record leaves open

- **Who is offered the tool:** contained sessions only, keyed on the session's containment and
  not on its gate, so a contained session narrowed to strict keeps it. Offered to an uncontained
  session it would bypass the operator's CLI deny rules for something `Bash` already does.
- **Timeout:** ten minutes by default; the operator picks 2m, 10m, 30m or 2h at the row. A
  timeout is its own outcome, worded "produced no exit within N; anything that prompts waits
  forever here".
- **The digest's waiting slot:** yes, kind and asker only.
- **Grants:** not now; the run record carries how it was approved, so a grant is additive. When
  built: exact bytes, bound to the directory, typed at launch, never loaded from a file.
- **The whole-session break-glass:** leave unbuilt; count requests per session while dogfooding.

Two corrections to the record: the shell splitter is in `shellscan.py` and never fails — it
returns substrings and does not see substitutions, `&`, heredocs or compound commands, so "the
programs it invokes" overclaims and the row should say "segments, as bash would sequence them";
and a login shell does not remove pptmstr's virtualenv from the environment.

## Not read, and guessed

Not read: `bridge.py`; `pool.py` beyond two signatures; the bodies of `digest.py`,
`boardfacts.py`, `rail.py`, `fleet_view.py`, `fake_driver.py`; the bash differential script;
README; any test; most of `driver.py`. Guessed: `Bridge.emit` is safe from a runner thread; bash
treats a leading-`-` command string as options (C4 should pin it against `/bin/bash`); ImGui
keyboard navigation is off (if on, Space can press Run and the buttons must take mouse clicks
only); the sandbox can read `~/.config`.

## Cannot be verified in a session with no display and no live model

Anything drawn; a model calling the tool; the gate stamping the new names through the CLI; the
sandbox reading the log path; the operator's real profile (the runner's tests use a temporary
fake shell that records its arguments and then runs `/bin/sh -c`, so no profile is read, the
argument is asserted byte-exact, and group kill, timeout and escape run for real); the quit
path with a real window.

## Built, and what two reviews changed (2026-10-13, the lead's record)

Steps C1–C11 are built on `enhanced-sessions`, uncommitted. Two read-only reviews of the built
feature both answered "would you let a contained autonomous team use this on your own machine?"
with **no**. Two fix rounds followed. `make check` on the still tree after the second: lint and
mypy clean, **9067 passed, 3 xfailed**. Every finding was reproduced by a failing test before it
was fixed. **Nothing was drawn on a display, no model has called the tools, and no real login
profile was read.** The fix rounds had not been re-read by a reviewer when this was written.

Decisions made while fixing, each the lead's or a builder's and none the operator's:

- **A command row that leaves under the cursor is replaced by nothing.** A reviewer showed an
  asker could ask A and B, withdraw A while the operator read it, and have B open expanded in
  A's place; both presses then land on B. `FocusState` now rests on `OnDeparted(left, index)`:
  no row expanded, a line naming the command that left, moved off only by `j`/`k`/click. **It
  holds only when a command is on either side of the change.** The every-kind rule was built
  and narrowed, because twelve tests in `tests/test_review.py` assert the old landing as their
  premise. An approval can still slide under the cursor when its neighbour is settled by its
  agent; `review.Hold` covers typed keys there and nothing covers a mouse press already on its
  way. Cost of the every-kind rule, reasoned and not used: a run of approvals becomes
  `a`/`j`/`a`/`j`. **(O)** the operator's to choose.
- **A command is at most 8 lines and has no blank line** (`MAX_COMMAND_LINES`, `BLANK_LINE`),
  beside the 2,000-character bound. Rejected: a clipper — the defect was reading, not drawing.
  Not closed: 2,000 characters on one line of ~165 `&&` segments is accepted.
- **The row names what repository code this command runs.** Six flags (`git`, `make`, package
  scripts, a Python build, an interpreter, a relative path) drawn above the breakdown, because
  for the tool's advertised uses the string does not decide what runs — hooks, `setup.py`,
  `package.json` and the Makefile do, and the session can write all of them. Advisory, with
  known misses (`FOO=1 bin/run`, `env bin/run`, cargo, go, docker). This narrows the finding; it
  does not answer it. Nothing lists what the session wrote since launch.
- **Emptiness of the process group is read from `/proc`**, not `killpg(pgid, 0)`, which succeeds
  on a group that is one unreaped zombie — and the shell is kept unreaped to pin the group id.
- **A command found running at restore is ended-unknown with a survivor, never runnable, and
  pptmstr does not kill it.** `started.json` (pid, group, start time, boot id) identifies it;
  the row gives the `ps` and `kill` lines and says another window may hold it. Linux only.
  `PR_SET_PDEATHSIG` rejected: it reaches the shell alone and orphans what the shell started.
- **The environment loses `BASH_ENV`, `ENV`, `VIRTUAL_ENV` and every non-absolute `PATH` entry**,
  each change listed on the row. Wider than the finding (`bin`, `node_modules/.bin` are the
  same hazard as an empty entry).
- **Reason and expected-change text is held to the command's rule on lone surrogates and NULs**;
  Dear ImGui's text call raises on a lone surrogate (measured in a headless frame).
- **A changed command is not returned to the asker as "text the operator typed"**: the heading
  says the operator approved every line and did not write every line.
- **Restricted roles get the tools** (a reviewer may ask). Not put to the operator.

Owed: a confirmation at quit that names a running command (needs a window to build honestly);
a contained session launched without a team gets no briefing section; `SIGKILL`, OOM or a
native crash of pptmstr orphans a running command until the next start.

## The second review of the runner, and its fixes (2026-10-13)

A re-read of the fixed runner still answered "no, narrowly": a stop could report an end with
part of the command alive. Seven findings, each reproduced against real processes before it was
fixed (`/proc` failures and pid reuse against a faked `/proc`). The builder's `make check`:
9119 passed, 3 xfailed. Signal handling has not met a real window.

- **A command is its shell's session, not its process group.** coreutils `timeout` and `set -m`
  jobs leave the group and stay in the session, so a stop of `timeout 30m ./deploy.sh` reported
  "cancelled" with the deploy running. Wind-down, the leftover count, the survivor lookup and
  `abandon` read `session_members`. A `setsid` escapee remains the tested limit.
- **Gone, cannot identify and could not look are three answers** (`Survivor | Gone |
  Unidentified | NotLooked`). A `/proc` failure no longer reads as gone and leaves the row and
  the hold. A session whose shell is gone is `Unidentified`: no kill line is printed for it,
  because pid reuse could make that line name a stranger. **(O)** `Unidentified` also owes no
  row and does not hold the session, so "shell killed, children alive" is an alarm on the
  record and a second command may run beside it; the builder is least sure of this one. An
  environment mark (`PPTMSTR_COMMAND=<id>`) would identify such a session and was not added,
  since it changes what the command runs in.
- **`gone.json`** is written once when a survivor is seen gone, so it is not looked for again.
- **`PATH` and four library paths lose entries that resolve inside the session's directory**,
  as well as non-absolute ones; an empty result falls back to `os.defpath` (bash 5.2 runs `./x`
  for `x` under an empty or missing `PATH`, measured). Only the cwd counts as session-writable.
- **The first handled signal resets all three to default**, SIGINT included; a second kills
  pptmstr with commands possibly alive.
- With no `/proc`, every command ends "NOT CONFIRMED DEAD".

## The second review of the row, and its fixes (2026-10-13)

A re-read of the fixed row answered yes for an operator attending and no for one half-attending:
a command still opened itself after an approval emptied the queue, and one `j` from the departed
mark put the next command where the withdrawn one had been. Eight findings, each reproduced
before it was fixed. `make check` by the lead on the still tree afterwards: **9190 passed,
3 xfailed**. Nothing was seen drawn; the row heights below are counted, not looked at.

- **A command row is closed until the operator opens that row** — a click on it, or `o` with the
  cursor on it. The lead's decision, replacing the cursor patchwork. Open is state keyed by the
  command's id and dropped when the cursor leaves, and the check is in `command_view.draw_row`,
  so DETAIL's copy is closed too. The first press is dead for 0.6 s after opening. Cost: one
  more action per command. `o` is deliberately absent from `review.SHORTCUTS`; a test pins that
  no other shortcut uses the letter.
- **`OnDeparted` stays, only for a command leaving from under the cursor.** Every press on a
  command is a mouse click, and a successor opening in place would put its buttons under a
  click in flight.
- **What must be read is bounded, not only what is typed:** 2,016 characters as drawn
  (escapes counted), 16 spaces in a row, 12 segments as `shellscan` splits them, 8 lines each
  for reason and expected change. Refused as a result: more than ~250 non-ASCII characters, a
  13-step chain. `command.py` now imports `commandtext` and `shellscan` so the bound measures
  the string as the row draws it; the import-isolation test was relaxed to exactly those two.
  The block is at most 36 rows at 100 columns by arithmetic. **The run buttons are still below
  the block, the breakdown and the asker's texts** — screens down for a maximal command.
- **The tree flags know the session's directory** and see through prefix words (`env`, `nohup`,
  `xargs`, `sudo`, `FOO=1`); more tools are named. Still advisory: no symlink or `cd` awareness.
- **`NEW` means asked within 5 s**, not moved.
- An armed first press does not outlive one frame in which the queue was not drawn.

Not covered: a click in flight when rows shift opens whichever row is now under it (it opens,
it does not run); these two fix rounds are again read by nobody but their builders.

## A hold the operator can end (2026-10-13)

A reviewer found, and a builder confirmed, that a command found alive at restart held its
session with no way out but its processes going away; a command meant to leave a server
running held its session for good. **Every hold a command puts on a session now has an
operator action that ends it:** *release session* on the survivor row, two presses (the
run's pattern: arm, confirm, mouse release only, `ARM_DELAY_S`), which signals nothing.

- **Representation:** `EndUnknown(released=Released(at, alive))` in place of the survivor,
  through the existing `CommandEnded` arm that already lets a look replace a survivor's end.
  No new Outcome and no new Intent. The reducer refuses a release for a command that is not
  unsupervised. Rejected: a new `CommandReleased` intent (two more exhaustive arms in
  `boardfacts`/`fleetsync` for a fact that is an end), and keeping the survivor beside a
  released flag (every `EndUnknown(survivor=left)` match would read it as still holding).
- **What is recorded is what the operator was shown:** the last look's count, not a fresh
  look at the press, which could find something other than what was decided on.
- **On disk:** `released.json` (at, alive, the `ps` line), write-once, refused without
  `started.json` or beside `end.json`, read into the outcome at restore, so it is not
  looked for again and the row does not come back. A file that could not be written is
  logged and the session is released in this window anyway; the next start shows the row.
- **The row** says the hold and both ways out in one line (`inbox.WAYS_OUT`); what a release
  does is behind its question mark. The asker's reply says the operator released its session
  with N processes still running and that other commands can run again.
