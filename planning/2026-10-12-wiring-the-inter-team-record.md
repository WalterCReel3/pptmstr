# Wiring plan for the inter-team features (planner's design, relayed by lead)

Status: a DESIGN, from reading only. Nothing in it was run. Spot-checked by lead: settings.config_dir()
exists (pptmstr/settings.py:116); model.Task has no version/history/amendment stamp; approval.classify
falls through to REQUIRE_APPROVAL for an unlisted tool; intents.TaskAmended's docstring says only the
operator may emit it. Everything else is the planner's claim: check it against the file before building on it.

Source of the features: ~/Source/imgui-adv-text/docs/postmortem-2026-10-swap-and-three-sessions.md,
'## For pptmstr'. Open operator questions Q1-Q10 and the lead's recommendation for each:
planning/2026-10-12-session-control-and-the-inter-team-record-in-one-sequence.md, 'To answer'.
NONE has been answered; each step bakes its question in the cheapest-to-overturn form.

## Where code may live
- A reducer arm: only its BODY may live outside store.py. The `case` line and the Intent member stay,
  because the match is the audit point and the exhaustiveness check.
- A bus tool: stays in bus.py (_quoted, _said, _named, _QUOTE_LEGEND, _sender, _park are private there;
  three tests pin the tool-name lists: tests/test_bus.py ~1084 and ~1095, tests/test_templates.py ~324).

## Steps
| id | Becomes true | Files written | Needs | Q baked; overturn cost |
|---|---|---|---|---|
| S1 | An amendment is a version; the declarer (never the holder) may amend; the digest reports "specification replaced" | pptmstr/{model,intents,store,board,digest}.py; bus.py and ui/board_pane.py ONLY where mypy's assert_never points; tests/test_{model,store,board,digest,boardlog,bus}.py | - | Q2: one guard in the TaskAmended arm. No format. |
| S2 | amend_task tool, parked like a declaration; board reads show the version and which version a result was written against | pptmstr/{bus,approval,templates}.py, ui/board_pane.py; tests/test_{bus,approval,templates,board_pane,driver}.py | S1 | Q2: one name in three lists plus one handler |
| S3 | One applied intent yields its board records (pure); a restored board enters the store once; the root may release a gone holder's claim | new pptmstr/boardfacts.py, tests/test_boardfacts.py; pptmstr/{boardlog,intents,store,model,board}.py; tests/test_{boardlog,store,board}.py | S1 | Q6: one condition in _ownership_refusal, PLUS a `Reclaimed` record kind that stays in the log format |
| S4 | Ordered off-thread appends; state root; refuses to append to a log this process did not restore | new pptmstr/boardsync.py, tests/test_boardsync.py; tests/conftest.py | - | Q5: one function (state_root); existing logs need a manual move |
| S5 | A resumed session has its board before its first bus call; outcomes appended every frame; failures shown | pptmstr/app.py, ui/board_pane.py, scripts/screenshot.py; tests/test_{app,board_pane}.py; settings.py + its test for the switch | S2,S3,S4 | Q7: one condition (spec.resume). Q5: a settings switch |
| S6 | Decisions exist in the store and in needs_you | pptmstr/{model,intents,effects,store,digest,theme}.py, ui/detail.py (_KIND_LABEL only); tests/test_{model,store,needs_you,digest,detail,theme}.py | S3 | Q4: nothing; the answer lives on the record |
| S7 | raise_decision and read_decisions tools; answer box and decision body in NEEDS YOU / DETAIL | new pptmstr/ui/decision_view.py, tests/test_decision_view.py; pptmstr/{bus,approval,templates,fake_driver}.py, ui/{inbox,detail}.py, scripts/screenshot.py; tests/test_{bus,approval,templates,inbox_rail,detail}.py | S6,S2,S5 | Q8: one name moved between _BUS_AUTO and _REVIEW |

Rounds, file-disjoint within each: {S1, S4} -> {S2, S3} -> {S5, S6} -> {S7}.

## Mechanics
S1: Task gains `superseded: tuple[TaskVersion, ...]` (prior texts only), `amended_at`, `amended_by`,
`completed_version`. The version number is derived: len(superseded)+1. TaskAmended gains request_id, at,
basis. Two new TaskRefusal members (not-the-declarer; amender-holds-it) so the two mistakes get two messages.
tests/test_boardlog.py has a test (~line 1726) that fails when a Task field is neither restored nor listed
as left out — S1's four fields must be handled there.
S2: amend_task goes in approval._REVIEW with a summarize row. An operator edit of a parked amendment drops
the basis, as declare_task does via EDITED_KEY.
S3: boardfacts.outcomes(before, intent, after, now) diffs by object identity over tasks and concerns and
skips BoardRestored. Store.apply_all gains observe=. The BoardRestored arm is a no-op when the session
already has board facts. Restored non-root parties become (session_id, "<marker><address>") and
board._role_within_session renders the address back; with no record _is_gone is true so the existing
owner_gone alarm fires. A restored "lead" maps to the resumed root. Records carry monotonic `at` until the
shell converts them; add Clock.wall_at.
S4: one single-worker executor per process. Root: settings.config_dir()/"boards". tests/conftest.py gets
an autouse redirect modelled on the brief one (the sandbox's read-only $HOME would otherwise hide a suite
that writes there).
S5: on spec.resume, asyncio.to_thread(boardsync.load) and emit BoardRestored BEFORE pool.submit; queue
order is the guarantee. Drain the writer in _shut_down. No writer under --fake. Board.short and Unwritten
show as one board-pane line.
S6: Decision fields: blocks, if_unanswered, evidence, recommendation, standing (operator's /
taken-wants-ratification), reversible (tri-state), answer, answered_at, delivered_at. DecisionOwed joins
Obligation. ui/inbox.py ~323 and ui/detail.py ~217/~503 match Obligation with no assert_never and
theme.py ~211-229 are dicts: a fourth kind compiles and draws nothing until S7 adds the arms.
S7: both tools in _BUS_AUTO (the text reaches only the operator; the pane is the review). Tool output uses
bus._said for the question and bus._quoted for bodies. The operator's answer needs its own legend line
(_QUOTE_LEGEND says "text an agent wrote"). The pane uses board_pane._author / author_text.

## Format positions
boardlog: additive only. Unknown kinds are carried as Unrecognised, unknown keys ignored, missing keys
defaulted. `Reclaimed` reads in an older build as `short`, not as damage. tests/test_boardlog.py's
known-cost restart test stays true for logs with no Reclaimed; S3 adds the test where a reclaim ends the contest.

## Non-negotiable properties
S1: A specification is replaced only by the operator or by the agent that declared the task and does not
hold it, and every text the task has had stays readable in order, with a completion naming the version it
was written against.
S2: An agent's amendment reaches the board only through the approval a declaration would have met on the
same rung, and no reply shows amended text unquoted or without its version.
S3: For every intent sequence the reducer accepts, restoring the records derived intent by intent equals
the board the store holds, and applying a restored board twice changes nothing. The generator must fail
when an Intent member is missing from it.
S4: Records reach disk in submission order, never on the calling thread, and a record that did not land is
a reported value, never a silent drop.
S5: A session launched with resume holds its logged board before any of its bus intents is applied, a
session launched without it reads no log, and no frame performs log IO. An unhooked observer must fail a test.
S6: A decision is in needs_you exactly until the operator answers it, the answer is stored byte-for-byte,
and no agent-emitted intent can set or alter one.
S7: Only the asking agent is handed the answer, verbatim and once, and the recommendation is never drawn
or returned in the same block as the evidence.

## The planner's decisions, each with what was rejected
1. Outcomes are derived by DIFFING snapshots, not emitted by each arm as an Effect (STYLE's "return it").
   Why: an arm that forgets to emit persists nothing while every test passes. Cost: an O(tasks+concerns)
   identity scan per intent on the draw thread, and an observe parameter on Store.apply_all.
2. Decisions are a fourth Obligation answered in NEEDS YOU / DETAIL, not a new dock pane. Why: STYLE's
   needs_you exists because "waiting on you" once had two implementations.
3. Restored claims stay CLAIMED by a gone holder; the root releases via the existing release_task, logged
   as a new Reclaimed kind. Rejected: auto-release at restore (answers Q6 more strongly than recommended);
   logging Released(by=holder) (false provenance).
4. Decision answers are delivered by read_decisions only, not by a wake. Cost: an answered decision can
   sit unread; the pane shows that state.

## What the planner did not read / guessed
Not read: driver.py, pool.py (grep only); requests.py from ~857; brief.py; ui/brief_pane.py, focus.py,
review.py; bodies of ui/inbox.py, ui/detail.py, fake_driver.py, screenshot.py; most tests.
Guesses: a marker prefix exists that cannot collide with a CLI agent_id; settings.load tolerates a new
key; theme.py's dicts are pinned by a test; screenshot.py runs headless in this sandbox;
bus._change_lines and board_pane.digest_rows need a touch for the new ChangeKind; a contained session
cannot write ~/.config/pptmstr (unmeasured).

## Left out, with the reason
- D, the request channel: requests.py's digest is unversioned, so the first real record freezes today's
  key set as a permanent v0; Q3's address scheme lands in directory names and digest-covered fields; it
  needs two live sessions to verify anything. Also Q8 ("answering a request runs without sign-off")
  contradicts Q1 for answers.
- Decision persistence in the board log: after A has run for real.
- Concern not-work: needs a Concern field, a record kind, and concern ids surfaced in two tool outputs.
- Brief amendment with acknowledgement, per-builder isolation, the out-of-sandbox runner.

## Lead's rulings on the planner's flags (decisions taken without the operator; each is one line to overturn)
- Flag 1 (S5 starts writing every team session's specifications, results and concern bodies under the
  operator's ~/.config/pptmstr/boards with Q5 unanswered): build it behind Settings.board_log, DEFAULT ON
  — the operator asked for a board that survives the lead, and a default-off switch ships a feature nobody
  exercises. The switch, the directory and the fact that nothing ever deletes a log must each be stated
  where the operator will read them (board pane line + README/settings docstring).
- Flag 2 (Reclaimed is a permanent format member): accepted.

## After the plan review (rv-plan, basis read): what changed

The reviewer's eight findings are kept whole at `$TMPDIR/es-plan/rv-plan-findings.txt` for the
session that built this; the rulings below are the lead's and are not ratified.

- **S1/S2 (P1).** A completion records the version its holder was last *handed*, not the current
  one. The task carries that version. `complete_task` by a stale holder lands at once against
  the handed version and its reply says the specification was replaced; it is not turned away
  (a turned-away completion drops the result it carried, and a long specification can be
  recorded as shown when the reply was unreadable — the second review's finding).
- **S1 (P2).** "The declarer, never the holder" is checked across time: an agent that has ever
  held a task may not amend it, and an agent that has amended a task may not claim it. The
  operator is exempt.
- **S3 (P3, P6).** S3 is split. **S3a**: `boardfacts.outcomes` dispatches on the intent, closed
  with `assert_never`, with snapshot identity as a prefilter only; records carry the agent id
  beside the address. **S3b**: the restore arm, which reports what it placed and what it could
  not; the root's reclaim, which lives in the release arm only (the shared ownership check would
  also have let the root *complete* a gone holder's task); a restored party with no live record
  is rendered "…, before the restart", never as a bare role name.
- **S4 (P4).** The first record that does not land closes that session's log for the process.
  `load` admits nothing; a separate call after the store has accepted the board does.
- **S5 (P5, P8).** Task ids are fleet-global, so a restored id that collides with a live task is
  skipped and reported, and the restore is shown as short. A restore into a solo launch, and a
  restore for a launch that then starts nothing, each get a stated answer. The driver is told the
  restored address table so a new agent is never handed a restored address. `README` says where
  logs go and that nothing deletes them.
- **S6/S7 (P7).** `read_decisions` returns an answered decision every time, whole; delivery is a
  timestamp, not a gate. A decision has an operator dismissal and an asker withdrawal. Its queue
  row is composed by the store from structure only; the question appears behind the author bar.
  Open decisions per session are capped. The two theme dictionaries are pinned to
  `ObligationKind`.

Build order as revised: {S1, S4} → {S3a, S2} → {S3b} → {S5, S6} → {S7}, a reviewer after each
round, a gate at the end.

**Cannot be verified in the session that builds this** (sandboxed: no display, no model
session): anything drawn; a real resume, which is what shows the session id is reused and the
root's bus calls arrive after the restore; whether a sub-agent woken after a restart reports its
original agent id; a write under the real `~/.config`.

---

## Built (third run, 2026-10-12 into 10-13, sandboxed)

**D7.** "Continue wiring up features. Continue with features identified in the postmortem."

Written by the lead from the workers' results. "Run" is each worker's own statement. The session
was sandboxed: writes only under the checkout and a temp directory, `$HOME` read-only, network
to `api.anthropic.com` only, **no display and no live model session** (all measured by a builder:
`xvfb-run` cannot take its lock, `AF_UNIX` sockets are refused). So in this section **nothing
drawn has been seen by anyone and no model has called any new tool**. Every step was read by a
reviewer who could not run code; every finding was reproduced by a builder before it was fixed.
Five review rounds, the plan review included.

### What is now true

| Feature (postmortem) | Now | Where |
|---|---|---|
| An amendment is a version | The agent that declared a task may amend it if it has never held it; an amender may not then claim it; every prior text is kept in order with who replaced it; a result is recorded against the version its author was *handed*. `amend_task(task_id, detail?, property?, withdraw_property?, basis?)`, approved exactly as `declare_task` is. A stale holder's completion lands at once against the handed version and is told the specification changed. `read_task(part=)` returns one long text whole. | `model.Task.superseded/claimed_version/held_by`, `store._amender_refusal`, `_claim_refusal`, `bus.amend_task` |
| The board survives the lead | Every board outcome is derived from the reducer step that made it and appended, off the draw thread, to `~/.config/pptmstr/boards/<session id>/`. A session launched with `resume` has its board back before its first bus call. Nothing is loaded for a session this window already records. | `boardfacts.outcomes`, `boardsync.BoardWriter`, `intents.BoardRestored`, `store._restored`, `app._launch` |
| …and says when it does not | A restored id that another live session holds is left out and named; a board the store already holds differently is refused whole; a log that stopped being written carries a marker and restores as short; a short log is never appended to again by a window that restored it, and the pane says the session is running unrecorded and why. | `model.Restore`, `boardlog.RecordingStopped`, the board pane's recording lines |
| One name per agent across restarts | One function decides every address from the restored table and spawn order; the driver, every bus read, the pane and the log take it from there. A party with no live record reads "…, from an earlier run". | `board.address_for` |
| No task is stranded | The session's root may release a claim held by an agent of an earlier run — another process's, or this window's before the session was started again. **The operator can close any unfinished task** from the board pane; it reads "closed by the operator", never as an agent's completion, and frees what waited on it. | `Snapshot.of_earlier_run`, `store._may_reclaim`, `intents.TaskClosed` |
| Decisions awaiting the person | `raise_decision` (question, standing — yours to take or already taken and wants ratifying —, what happens if unanswered: all required; the task ids it blocks; evidence and recommendation as separate texts; reversible yes/no/unstated). A fourth kind of needs-you row whose summary the store composes from structure only. The operator answers in the row: two presses, final, stored byte for byte, at most 4,000 characters; or dismisses. `read_decisions(id)` returns the answer whole every time; the session's root can read, not answer or withdraw, its workers' decisions. The asker can withdraw. | `model.Decision`, `store` decision arms, `bus.raise_decision/read_decisions/withdraw_decision`, `ui/decision_view.py` |
| Why a claim won nothing | `claim_task` says which of seven reasons. | `effects.ClaimSettled.refusal` |

Also fixed because this work met it, each reproduced first: the store's own automatic
dependency could close a cycle that left two tasks unclaimable for good; **a text box vanishing
under the typist turned the next keystroke into an approval shortcut** (`a` approved the next
call unread) — this predates the branch and held for the rejection-reason box, the argument
editor and the reply box as well as the new answer box; Escape in the reply box and the answer
box deleted the draft; three launcher frame tests started the model-catalog probe with the
operator's real credential and opened a connection to `api.anthropic.com`; text accepted by the
bus had no bound, so an ordinary board could exceed the log's record limit.

### Not built from the plan, and why

- **The request channel between sessions** (the plan's D). Its on-disk digest is unversioned, so
  the first real record freezes the format; the address scheme (Q3) lands in directory names;
  nothing about it can be verified without two live sessions.
- **Decisions in the board log.** They do not survive a restart of pptmstr; `read_decisions`
  says so for an id it does not know.
- **Marking a concern not-work; the amendable brief with acknowledgement; per-builder
  isolation; the out-of-sandbox runner.** Untouched.
- **Tasks keyed by (session, id).** Ids are still fleet-wide. A dependency on an id only another
  session holds reads as missing and cannot be declared; the exit is the operator's close.

### Decisions taken without the operator in this run

None of Q1–Q10 was answered. Each row is in the tree.

| (L) | To overturn |
|---|---|
| Q2: the declarer may amend, if it never held the task; an amender may not claim | one condition each in `store._amender_refusal`, `_claim_refusal`; but the rule is also in `boardlog._one_writers`, so logs written under it restore as contested if only the store changes |
| Q5: logs under `~/.config/pptmstr/boards`, `Settings.board_log` **default on**, file-only; nothing ever deletes a log | `boardsync.state_root`; one default in `settings.py` |
| A settings file that cannot be read, or a `board_log` that is not a boolean, means *not* recorded | `settings.read` |
| Q6: the root may release an earlier run's claim; it rides the auto-approved `release_task` on every rung; `Reclaimed` is a permanent record kind | `store._may_reclaim`; the kind stays |
| Q7: a log is read only on `resume`, and only for a session this window does not already record | one condition in `app._launch` |
| A restored board is adopted for appending only if nothing was short or left out | `model.Restore.adoptable`: drop `log_short` from one line |
| The first record that does not land stops that session's log for the process | `boardsync.BoardWriter._settle` |
| A dependency resolves only on the task's own board — **reverses a pinned test** (`test_a_cross_session_dependency_does_not_invent_a_blocker`) | `model.Task.dependency` |
| The operator may close any task; a closed row is off the unverified ledger | `intents.TaskClosed`; new record kind `task-closed` |
| Q8: the three decision tools run without sign-off on every rung | three names in `approval._BUS_AUTO` |
| Q4: the answer goes to the asker; the session root may read it; answering wakes nobody | `Snapshot.decisions_readable_by` |
| An answer is final (a second is a no-op), cannot be empty, and is at most 4,000 characters; decision texts at most 1,000 | `store._ended_by_operator`; `model.MAX_ANSWER_CHARS`, `MAX_DECISION_TEXT_CHARS` |
| 8 open decisions per asker; 24 across a session's sub-agents | two constants in `model.py` |
| **After typing in any text box, no shortcut key acts until Escape or a click** — `j/k/a/r/e`, and Enter/Tab/Esc for layout | `ui/review.Hold`; this changes how the queue feels and nobody has used it |
| Bus text bounds: 24,000 characters for a specification, result, not-run list or concern body; 500 for a title or subject; an over-long result is cut and says so, never refused | constants at the top of `bus.py` |

### Cannot be known from this run

- Anything drawn: the decision row and its answer box, the glyph and colour chosen for it, the
  closed-decisions list in DETAIL, the rail's per-kind badges and "N answers unread", the board
  pane's recording lines, the close control, the held-keys notice in the inbox header.
- A real resume: that the CLI reuses the session id the log was written under (the driver
  checks it; unexercised), that the root's first bus call arrives after the restore, and
  whether a sub-agent woken after a restart reports its original agent id — the address rule
  is built to be right either way.
- Whether a model uses any of it sensibly: reads the `| ` and `> ` marks, calls
  `read_decisions(id)` after a bare `read_decisions()`, raises a decision instead of a concern.
- A write under the real `~/.config`; the credential fix (the sandbox denies the credential
  file, so the socket cannot appear here). Outside the sandbox:
  `pytest tests/test_app.py tests/test_pool.py -W error::ResourceWarning`.

### Added after that table was written (the last two review rounds)

- **A run that ends by any path settles its sub-agents first** (`AgentSession._settle_subagents_at_run_end`):
  a close, a relaunch over a live run, a cancel, a closed stream, a raised read. Before this a
  builder that was mid-work when its session ended stayed "spawning" in the store for the life of
  the process, and the next lead could not release its claim. Reproduced on five ends with the
  real session and pool over a fake client. Whether a live CLI also sends its own stop for those
  agents at teardown is not established; the fix is indifferent to it.
- **Closing a held task tells its holder**: a notice from the operator in its inbox with the
  reason; its later `complete_task` or `release_task` is told the operator closed it and that the
  result was not recorded. The holder is not stopped, and the pane's sentence says so.
- **A second keystroke race, also older than the branch**: `r` and then a reason beginning with
  `a` on the next frame approved the call being rejected; after `e` it did so at any gap. The
  hold now begins at the keypress that opens a box. After `e`, Escape is pressed twice to cancel.
- **The settings switch**: a misspelled key (`board-log`, `boardlog`, `Board_Log`) means not
  recorded; pptmstr refuses to save over a settings file it could not read and says so in the
  status bar.
- **(L)** `raise_decision` is unparked on every rung and, since the session root reads its
  workers' decisions, is a text channel from worker to lead on rungs that park `post_concern`
  (`approval.py` states it).

### The gate, with nothing else writing

Run by the session's last builder, in the sandbox: `make check` three times after the last edit,
each exit 0 — ruff clean, black clean over 148 files, mypy clean over 53 source files, pytest
**5955 passed, 3 xfailed**, identical each time; once with `XDG_CONFIG_HOME` unset and once set
to an empty directory, in which nothing was created. An audit-hook run recorded no write outside
the checkout and the temp directory. mypy is clean on the four probe scripts this branch added;
`scripts/screenshot.py` has the one error it had at `HEAD`.

What that green does not cover: it ran with `$HOME` read-only and one reachable host. Sixteen
driver tests reach the SDK's `tag_session`, which **lists the operator's real
`~/.claude/projects`** and tries an append-open of a fresh id in each directory; nothing can be
written, but no fixture covers it. Not fixed.

**Read by no reviewer:** the last fix round (`fixg-last-findings`), the integration joins
(`fixf-integration-seams`), the gate's three joins, and this record.

### Left open, each reported by the worker who met it

- A sub-agent started by an ending run *after* the relaunch's announce reads as current-run.
- A bare `read_decisions()` shows no answer; a model must call again with the id. Unwatched.
- The operator's stand-in party is listed among a restored board's agents (harmless today).
- A mouse click in the single frame a text box vanishes counts as leaving it.
- A restored board is never adopted if anything was short: one stray record ends a session's
  recording for every window that restores it.
- `board._addresses` names three cases where the driver and the board could still disagree
  (a restore refused whole; a relaunch over a run still ending; `AgentRemoved`, which nothing
  emits). The fix for all three is carrying the address on `AgentSpawned`.
- A claim refused "not on your board" cannot say whether another session holds the id.
- Command-line `--theme`, `--cap`, `--fps-idle` are persisted by the next save (older than this).
- `store._owing_decisions` costs about 275 µs per applied intent with 16 decisions open, against
  19 µs with none (measured once); most of it is `board.role_name`.

### Incidents

- The lead read its inbox only when a worker finished, so three builders' "may I edit one line
  outside my task?" went unanswered; each worked around it, one by leaving a patch the lead
  applied, one by leaving the tree red for the next task.
- The lead ruled that a stale holder's completion be turned away once, and reversed it two
  rounds later when both reviewers and the builder reported it drops the result.
- The board's notes were cut to zero characters once it passed about forty tasks; results were
  relayed through files in the temp directory from then on.
- A builder ran scratch scripts from the shared temp directory's root, where another session's
  `bisect.py` shadowed the standard library and launched a headless browser until a timeout
  killed it. Nothing in the checkout was touched.
- Agent addresses on the bus and on the board disagreed all session in the app hosting it
  (`builder-22` on the board, unroutable): the defect `board.address_for` fixes, seen live.
