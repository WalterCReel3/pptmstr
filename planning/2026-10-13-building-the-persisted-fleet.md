# Building the persisted fleet: steps 2–4 as a plan

**Dated:** 2026-10-13 · **Status:** a DESIGN made by reading the tree, relayed by the lead; nothing
in it was run, and its author lists what it did not read · **Decisions it builds:**
[`2026-10-13-a-fleet-that-survives-the-window.md`](2026-10-13-a-fleet-that-survives-the-window.md)

Step 1 of that record (the card's context menu and the Details modal) is built separately in
`ui/session_menu.py`, `ui/session_details.py`, `ui/rail.py`, `app.py`. Every claim below about
the code is to be checked against the file before it is built on.

## Steps

| id | What becomes true | Files written | Needs |
|---|---|---|---|
| P1 | `session.json`: decode, key-preserving merge, atomic read-modify-write that refuses an unreadable file; `root()`, `session_dir`, `board_dir`, fleet listing | `pptmstr/sessionfile.py`, `tests/test_sessionfile.py` | — |
| P2 | The store can hold a closed, never-run session and a detached one: `AgentRecord.recalled`, `AgentRecord.detached`; intents `SessionRecalled`, `SessionDetached` | `pptmstr/model.py`, `intents.py`, `store.py`, `boardfacts.py` (no-fact arms), `tests/test_store.py`, `tests/test_model.py`, `tests/test_boardfacts.py` | — |
| P3 | The suite cannot reach the real config directory; `Settings.hide_ended_subagents = True` | `tests/conftest.py`, `pptmstr/sessions.py`, `pptmstr/settings.py`, `tests/test_settings.py`, `tests/test_sessions.py` | — |
| P4 | A pure fold: which sub-agents of a root are drawn and which are counted | `pptmstr/ui/fleet_view.py`, `tests/test_fleet_view.py` | — |
| P5 | The resume form: state, `press()`, draw | `pptmstr/ui/resume_form.py`, `tests/test_resume_form.py` | — |
| P6 | Pure gate-menu rows from (live policy, contained) | `pptmstr/ui/gate_menu.py`, `tests/test_gate_menu.py` | — |
| Q1 | The board log lives at `sessions/<id>/board/`; `migrate()`; `load()` can read a legacy directory | `pptmstr/boardsync.py`, `tests/test_boardsync.py` | P1, P3 |
| Q2 | One writer thread for session files (coalescing; a per-session latch on refusal); blocking `recall()`; pure `changes(before, intent, after)` | `pptmstr/fleetsync.py`, `tests/test_fleetsync.py` | P1, P2 |
| Q3 | Readers outside the held files treat `recalled`/`detached` correctly (table below) | `pptmstr/board.py`, `ui/projects.py`, `ui/focus.py`, `ui/transcript_pane.py`, `ui/board_pane.py`, `ui/digest_pane.py`, their tests | P2 |
| R1 | The closed card's face; a fold row "N ended" that opens | `pptmstr/ui/rail.py`, `tests/test_rail.py` | step 1, P2, P4 |
| R2 | The menu gains Remove from fleet, Resume…, Gate ▸; Details shows the session-file paths | `ui/session_menu.py`, `ui/session_details.py`, tests | step 1, P2, P6 |
| R3 | Step 2 wired: migrate, recall, writes, shutdown flush, status bar | `pptmstr/app.py`, `tests/test_app.py` | step 1, Q1, Q2, Q3 |
| R4 | Steps 3 and 4 wired: detach, the form overlay, the `set_policy` action, the hide toggle | `pptmstr/app.py`, `tests/test_app.py` | R1, R2, R3, P5 |
| R5 | Picking a row in New Task hands off to the resume form | `ui/launcher.py`, `tests/test_launcher.py`, one callback in `app.py` | R4 |

P1–P6 are file-disjoint. Q1, Q2, Q3 are disjoint from each other; so are R1, R2, R3. R3 → R4 → R5
is strictly sequential (`app.py`). Signatures to fix before R1–R3: `rail.draw(..., hide_ended: bool)`
and the session-action additions `detach`, `resume`, `set_policy`.

## One property per step

- **P1.** A `session.json` that exists and cannot be read, or whose `format` is newer, is never replaced, and a write keeps every key it does not know.
- **P2.** `SessionRecalled` never replaces a record that exists; `SessionDetached` is a no-op unless the root is terminal and no obligation names its session; `AgentSpawned` clears both.
- **P3.** No test can get a path outside the suite's temp tree from `settings.config_dir()`, and no module binds that name by `from`-import (pinned by a test).
- **P4.** A sub-agent is folded only if it is terminal, no obligation names it, it holds no claimed task, has no posted mail or unread answer, has no unfolded descendant, and is not under the cursor.
- **P5.** `press()` returns a spec only when a fresh own-checkout reading grants the rung on screen, and never for a directory that is gone or an empty message.
- **P6.** A rung that `approval.requires_containment` and the session lacks is offered only as "Continue as…", never as a live act.
- **Q1.** Two directories are never merged and nothing is deleted; the writer appends only under the new path, and never for a session whose log was read from the legacy one.
- **Q2.** No file IO on the submitting thread; a session whose file was refused once is not written again this run.
- **Q3.** A recalled or detached record gets no board, no cursor by default, and no project-header count.
- **R1.** The fold changes what is drawn and nothing in the snapshot.
- **R2.** Remove from fleet is absent on a non-terminal card and greyed with the reason on one that owes.
- **R3.** A recalled card starts no process, reads no board log and adds no board-log entry until it is resumed.
- **R4.** Every resume, from any surface, reaches `app._launch` with `brief` set to the session's existing brief directory (otherwise `_seed_brief` appends the resume message to a team's brief).
- **R5.** `LauncherState.spec()` can no longer carry `resume`.

## `session.json`

`format: 1`. The number changes only on a breaking change; a newer format is displayed from the
keys this build knows and never written. Unknown keys are ignored on read and preserved on
write. A missing key takes its default. A wrong-typed `in_fleet`, or a `session_id` that differs
from the directory's name, makes the file unreadable: not shown, not written. `sessions/<id>/`
holds `session.json` and `board/`; any other entry is ignored, which is where `lock` goes later.

| Field | Type | Notes |
|---|---|---|
| `format` | int | 1 |
| `session_id` | str | must equal the directory name |
| `in_fleet` | bool | false means detached; default true |
| `created_at`, `launched_at` | float, wall seconds | first and latest launch under pptmstr |
| `last_active` | float | wall time of the root's last turn end, end, or launch |
| `ended_at`, `ended_how` | float or null; `"closed"`, `"failed"`, `"shutdown"` or null | display only; null means live or killed |
| `task` | str | first launch text, never overwritten; the card's title |
| `last_message` | str or null | latest launch text or operator send; not stored above 24,000 characters |
| `model`, `cwd` | str | |
| `session_base`, `template`, `brief` | str or null | |
| `policy` | `"strict"`, `"permissive"`, `"autonomous"` | as last granted, live changes included; any other value reads "unknown" |
| `contained` | bool | |
| `subagent_cap` | int or null | |
| `cost_usd` | float or null | an estimate, display only |
| `error` | str or null | |

"Closed" is not stored: at startup every session is closed, and a stored flag would be wrong
after a crash and under two windows.

Who writes, all through the fleet writer's queue: `_launch.start()` after `pool.submit` returns
true (launch parameters, `in_fleet=true`); the apply observer on root transitions into awaiting
input, supervising or terminal, and on `SessionDetached`; the app's send and gate actions
(`last_message`, `policy`); `_shut_down` (`ended_how="shutdown"` for live roots, then a bounded
close beside the board log's).

**Transcript gone.** `recall()` lists sessions once on its worker and gives three answers:
present, gone, or unknown when the listing raised. A gone card shows the title, "transcript gone
— can only be removed", and a menu of Details and Remove only.

**The board log's move (Q1, R3).** Synchronously in `app._start`, before the writer is built,
only when `board_log` is on and the run is not `--fake`: one listing of `boards/` and one
`os.rename(boards/<id>, sessions/<id>/board)` per directory; no file is read. Not on a worker,
because a resume pressed mid-move would start a fresh log at the new path. If both exist, the
new one is read and written and the legacy one is left in place and named in the log, Details
and the board pane. If a rename fails, the legacy directory stays; a resume restores the board
from it and runs unrecorded, with the reason. An older build run afterwards starts a log under
`boards/`, which becomes the both-exist case.

**Two windows today.** Both recall the same fleet. Writes to one file are last-writer-wins per
key and never torn. A detach in one window shows in the other at its next start. A window
resuming a session another detached re-attaches it.

## Readers of a recalled record

The record is a root in `DONE` with `recalled` set, `ended_at` from `last_active`, an empty
transcript and zero usage.

| Reader | What it must do |
|---|---|
| `store._needs_you`, `any_active`, the final-state sets | nothing: `DONE` owes nothing and idles |
| the pool's cap, `send`, `close`, `interrupt` | nothing: no session under the id |
| `app._live_sessions`, `_relaxed_count`, `_policy_of` | nothing |
| `Snapshot.left_behind`, `of_earlier_run` | nothing: no restore, no sub-agents; a resume's `AgentSpawned` replaces the record |
| `board.has_board` | **change:** false when `recalled`, else a team template draws an empty board |
| `board_pane`, `digest_pane` | **change:** say "not loaded until resumed" |
| `transcript_pane` | **change:** say so and give the path |
| `projects.roots` / `group_roots` | **change:** skip `detached`; recalled cards stay |
| `focus.settle` | **change:** never default to, or stay on, a detached node |
| `rail._density`, `_card` | **change (R1):** "closed · 3h ago"; no cost or context cell |
| the status bar's "N agents" | **change (R3):** exclude recalled; append "· M closed" |
| `health` fork, `LaunchSpec.from_record` | works: task, model, cwd, template, brief are on the record |

## The planner's decisions, each with what was rejected

1. **`DONE` plus a `recalled` field, set by a new intent.** Rejected: a new `AgentState` (it forces
   arms in the theme, the rail and every terminal set to say what `DONE` says); `AgentSpawned` +
   `AgentFinished` from disk (`AgentSpawned` replaces a record whole, so a late recall would
   destroy a live one).
2. **Detached is a flag in the store, not `AgentRemoved` and not a set in the rail's state.**
   Removal changes who counts as gone; a UI-only set lets the cursor land on an invisible
   session. Cost: a session detached in this window keeps its board in the store, so its task
   ids stay held until exit.
3. **The move is eager and synchronous; the new path wins; the legacy one is a read-only
   fallback.** Rejected: lazy per-session moves (two live layouts and a race); merging (the
   second history the writer already refuses).
4. **The message prefill comes from `session.json`'s `last_message`.** Rejected: reading the
   transcript (it parses the whole file, and user entries include tool results and injected
   text). Cost: a session never run under this build opens with an empty required box.

**Where the planner departs from the record's (R)s, for the operator:** the form's gate defaults
to strict whatever the file says, except through "Continue as autonomous…"; a session with no
`session.json` has no known team, so the form offers the choice there and says why; a session
that had failed is recalled as done, so restarting does not fill needs-you; folding makes an
open group shrink at the model's pace, which reverses `rail._group`'s recorded argument
(`planning/2026-08-13`); `j`/`k` walk needs-you, not cards, so they cannot land on a folded card.

## Not read, and guessed

Not read: `driver.py` beyond `set_policy`, `_unpaired`, `run`'s opening, `send`; `ui/health.py`,
`ui/inbox.py`, `ui/review.py`, `ui/widgets.py`, `digest.py`, `brief.py`, `bus.py`; the launcher's
containment section, version gate, draft and draw; any test but `conftest.py`. The two step-1
modules did not exist when this was written.
Guessed: no reader treats a negative or zero `started_at` specially; `recording=False` alone
stops the board observer submitting; "last invocation message" includes composer sends; the
thin-premise hold is dropped on the form; recall order is by `created_at`.

## Cannot be verified in the session that builds it (no display, read-only home)

The real rename of the operator's `~/.config/pptmstr/boards/…` into `sessions/…/board/` and its
restore; everything drawn (the closed card, the fold row, the menu, the form); cards appearing
after a real restart with no CLI process behind them; a real resume from a card, a live widen,
and "Continue as autonomous…" ending the live run.
