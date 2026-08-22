# Session controls are the operator's, and the mode dial is ours rather than the SDK's

**Dated:** 2026-08-22 · **Status:** proposals, none built · **Found by:** operator request,
then an option audit

The ask was mid-session affordances for `set_model`, `set_permission_mode`, `stop_task`,
`effort` and `interrupt`, plus launcher fields for `effort`, `permissionMode` and resume.
The audit that followed changed the shape of the answer: two of the eight have no
mechanism behind them, one is already built, and the highest-value item was not on the
list. This record is what to build, what not to, and what has to be measured first.

**Evidence class.** Everything below is read-derived unless it says otherwise. Nothing here
was run. Claims about what the CLI does with a flag are inference from SDK source and are
marked where they are load-bearing.

## The audience premise, because it decides the vocabulary

This tool targets operators who have used Claude Code extensively. That is a design
constraint, not a marketing note: they arrive with a working model of permission modes and
session control, and the cost of contradicting it is higher than the cost of any single
widget.

What they know about permission mode has four properties, and only the first is about
permissions:

1. It is a **trust ladder** — ask-everything, accept-edits, don't-ask.
2. It is **live and keyboard-driven**. Shift+Tab cycles it mid-session; nobody relaunches.
3. It is **persistently displayed**. Current mode is ambient state, not a dialog.
4. **Plan is not a rung on it.** It is a working state with an entry and an explicit exit.

The gate this application is built around is already a permission system, and a stricter
one than Claude Code's default. So the ladder metaphor transfers and the mechanism does
not. That asymmetry is the central decision here: **borrow the vocabulary, implement over
`classify`.**

## D1 — The mode dial writes to gate policy, not to `ClaudeAgentOptions.permission_mode`

Three distinct things wear this name and the record should keep them apart:

| name | what it is | effect here |
|---|---|---|
| SDK `permission_mode` | the CLI's own permission evaluation, hardcoded `dontAsk` in `_options()` | unproven, probably nil — see the caveat below |
| gate policy | what `approval.classify` returns | this is what actually determines what parks |
| `plan` | probably a mode of work, not a permission | see D7 |

Two arguments for building the dial over gate policy, in order of weight.

**Testability.** `approval.py` is pure by deliberate design — no SDK imports, its docstring
commits to it, and the bus-server name is spelled out as a constant pinned by a test rather
than imported, specifically so the module stays free of `claude_agent_sdk`. That purity is
the asset. `classify(name, input, policy)` is a unit test that runs in milliseconds. A
control routed through `set_permission_mode` can only be verified by running a live CLI
session against a probe harness. Same operator-visible feature; one version is verifiable in
CI and the other is not verifiable at all.

**Direction of failure.** `classify` is fail-closed by construction: an unrecognised tool
returns `REQUIRE_APPROVAL`, and the docstring argues why — "an allowlist that defaults open
stops being an allowlist the first time the SDK adds a tool." Every SDK mode above the
default widens. A dial whose failure mode is "more got through than you meant" is the wrong
shape for an application whose premise is the gate. A policy enum selects which set a tool
lands in and leaves the unknown-tool fallthrough intact.

**Do not send `set_permission_mode` at all** until a probe shows it changes something the
gate does not already cover. Sending both is two permission systems side by side, which
`planning/2026-08-11-research-phase-auto-approval.md` already rejected.

### The caveat this record must not paper over

The claim that the mode is inert here traces to a single sentence in
`orchestrator-design.md` §5.2: "`PreToolUse` runs on **every** tool call regardless of
permission mode or allowlist … it blocks even under `bypassPermissions`."

That sentence is unverified. The document marks its verified claims — the
"Verified against `claude-agent-sdk` 0.2.134 (rev. 3)" block enumerates exactly three
corrections and this is not one of them, and §5.2.1 carries a separate empirical table from
three live runs of `scripts/verify_hook_timeout.py`. In a document that scrupulous about the
difference, an unmarked load-bearing claim is a flag. §5.2.1 makes the distinction itself:
"the number is plumbed through" and "an unbounded await is honoured end to end" are
different claims and only the first is provable by reading.

What *is* readable: `_gate_tool_use` returns an explicit decision on every path — at-cap
deny, `AUTO_APPROVE` allow, `DENY`, non-interactive deny, else `_park`. No path returns `{}`.
So **if the hook is invoked, the mode never gets a say.** Whether it is invoked under every
mode is the unreadable half.

D1 does not depend on that sentence being true — the testability and fail-closed arguments
stand either way. But nothing in this record, and no future record, should cite §5.2's
sentence as settled. It is a candidate for the same empirical treatment §5.2.1 received.

## D2 — The ladder is capped below spawn-bypass

The presets are named for what a Claude Code user already knows, but the top of their ladder
does not transfer. There, "don't ask" is one agent on one machine. Here, the calls you would
stop being asked about include `Agent`/`Task`, which `classify` gates deliberately — "an
orchestrator that gates writes but not the spawning of things that write has a hole in it."

A veteran's calibration for "don't ask" is therefore wrong in the dangerous direction, because
the blast radius is a fan-out rather than a file. **Spawns stay parked at every preset**, or
the ladder stops below a preset that would release them. `STRICT` is the default and
`RESEARCH` is the first additional rung; the shape, the auto-revoke, the operator revoke from
the tree and the atomic-flag-versus-Bridge concurrency question are all already specified in
`planning/2026-08-11-research-phase-auto-approval.md` and are not re-derived here.

## D3 — Once mode is changeable it must be displayed

Claude Code shows model and mode persistently so the operator always knows what they are
driving. This application shows model on the record and has never shown mode, because mode
has been invariant.

The moment a mid-session control exists, that stops being safe. An invariant nobody displays
is fine; a variable nobody displays is the first way the UI can be silently wrong about what
a session is doing. Displaying the dial is part of shipping the dial, not a follow-up.

## D4 — `Esc` becomes interrupt

`Esc` interrupting a running agent is the deepest muscle memory the target audience has, and
repurposing it costs nothing: `Tab` already switches to TRIAGE from any layout
(`app.py:157`), and `Esc`-from-FOCUS does the identical thing (`app.py:161-164`). The
docstring "Tab triages, Enter focuses, Esc returns" describes a redundancy.

The existing precedence rule extends rather than changes — an edit in progress still wins,
for the reason already recorded: "yanking the layout out from under someone mid-diff is the
failure mode this whole two-mode design has to avoid." The ladder becomes: edit in progress,
then launcher modal, then interrupt if the focused session is working, then return to
TRIAGE.

## D5 — Two surfaces: a rail card context menu, and the Session menu

Both act on the session or agent focused in the rail. `rail.py` currently takes no action
bundle at all (`app.py:548`) and is the only pane resident in both layouts, which makes it
the right host and also means wiring it is new work rather than an addition to an existing
bundle.

Menu ordering is not a reorder of our code: `_menus` already opens with Session
(`app.py:401`). What renders ahead of it is hello_imgui's built-in View menu
(`show_menu_view = True`, `app.py:821`). Putting Session first means turning that off and
hand-rolling its docking entries, and the cost of that is the docking and layout items the
built-in provides. The application already works around this menu's existence — its own menu
is named "Text" rather than "View" to avoid two menus with one name.

## D6 — Mid-session `effort` is not shipping, because there is no mechanism

The complete set of outgoing control-request subtypes in the installed SDK, enumerated twice
independently: `initialize`, `mcp_status`, `get_context_usage`, `interrupt`,
`set_permission_mode`, `set_model`, `rewind_files`, `mcp_reconnect`, `mcp_toggle`,
`stop_task`. There is no `set_effort`. Mid-session effort is respawn-only, and respawn today
discards the conversation, so it is not a mid-session control at all.

Launcher `effort` is buildable, with three things the record should carry:

- It governs **thinking depth, not scope**. Both the SDK docstring and
  `planning/2026-08-21-the-board-takes-an-agents-word-for-what-it-will-touch.md` say so. A
  control labelled "effort" beside a task box invites "how much work should it do", which is
  wrong in the expensive direction — the operator turns it down expecting less output and
  gets the same output reasoned about less. Label it for reasoning depth.
- `--effort` is **absent from argv today**: the dataclass default is `None` and `_options()`
  never sets it. Any combo with a default starts sending a flag on every launch that
  currently sends none. That is a behaviour change, and shipping it as "just exposing an
  option" would be untrue.
- It is only meaningful because `thinking.type` is hardcoded `adaptive`. If a thinking
  control is ever exposed and set to `disabled`, the effort control silently stops doing
  anything. **The two are coupled and must not be added independently.**

Whether `effort` reaches the agent at all is unproven. The 2026-08-21 record's rule applies:
an SDK field existing is not evidence that it arrives.

## D7 — `plan` is a work mode, not a rung, and it is the one probe worth running first

The operator's position is that `plan` earns its place if it influences the session's
*behaviour*. Agreed, and that is exactly the discriminator.

`ExitPlanMode` is a **tool**. A tool appearing in the model's list is not a permission — it
is something the model sees and reasons about. If the CLI injects it under
`--permission-mode plan`, then plan mode is behavioural, no hook `allow` undoes it, and this
application can have it nearly free.

And the fit is better than free. The gate is fail-closed, so an `ExitPlanMode` call arrives
as an unrecognised tool and returns `REQUIRE_APPROVAL`. It would **park**. "The agent has
finished planning and wants to start doing" landing in the approval queue as an operator
decision is the correct shape, and the machinery for it already exists by accident.

**A prior record appears to over-generalise here, and this record disagrees with it.**
`planning/archive/2026-08-10-conversational-sessions.md` states that "`ExitPlanMode` and
`AskUserQuestion` are **not in this CLI's tool list**". That record describes two live cases,
and its observations — an agent asking clarifying questions as prose, zero tool calls — are
the `ask` case, which runs `permission_mode="default"`. `ExitPlanMode` is only injected in
plan mode, so its absence under `default` is expected and says nothing about plan. The
conclusion is stated as a property of the build where the evidence supports only a property
of that mode. The record is also dated 2026-08-10 against a CLI that has since moved to
2.1.226.

This is a disagreement with a recorded finding, not a dismissal of it: the finding may still
be right, and the run that would settle it is small.

If plan mode is behavioural, it belongs in the launcher beside the team template as a
starting state — not on the trust dial.

## D8 — Resume is a conditional go with one unreadable blocker

The material exists: the CLI writes one JSONL per session id, and this application mints the
id itself and passes it as `--session-id`. So the conversation a respawn discards is in a
file this application named.

Blocking unknown, not answerable by reading: **which session id the CLI uses when
`--session-id`, `--resume` and `--fork-session` are passed together.** `types.py` documents
the combination as legal, `_build_command` emits all three with no mutual-exclusion check,
and the arbitration is the CLI's. If the supplied id is honoured, resume keeps NodeId
stability and is a small change. If not, the fork must happen before the session is
constructed.

Four constraints the design must carry regardless:

- **A resumed session has no `AgentRecord`.** Resume restores the conversation, not the task
  text, model, template or brief. That is a different feature from what the word promises and
  the UI must not imply otherwise. The brief is the exception — `brief.session_dir` is derived
  from cwd and session id, so it is recoverable unless the id changes, which is precisely what
  forking does.
- **Attribution.** `list_sessions` cannot distinguish this application's sessions from the
  operator's own Claude Code sessions in the same repo; `SDKSessionInfo` has no producer
  field. A picker built on it renders the problem rather than solving it. `tag_session` fixes
  it prospectively, and must fire after the first message because it refuses a zero-byte file.
- **`list_sessions` is synchronous blocking I/O** — per-file open, stat and head/tail reads
  across every transcript, plus a `git worktree` subprocess. It cannot run in a draw call.
- **Three things are called fork**: `ClaudeAgentOptions.fork_session` (a bool), the top-level
  `fork_session()` function (a transcript-file copy that starts nothing), and this
  application's existing fork button (a fresh session from a record). The button discards
  context deliberately — "a session that has compacted has already lost the reasoning that got
  it here". Resume sits beside it; it does not repair it. One of the three has to be renamed.

## D9 — `stop_task` is per-sub-agent, needs a live-task table, and must not be called "task"

Four of the five requested controls are session-scoped. `stop_task` is the only per-sub-agent
one and the only one not wireable: the id arrives on `task_started` frames that
`Translator._system` already receives and reads two fields from, leaving `task_id` unread.

It is not one field read. `_system` fires for every `task_started` including background
`Bash`, so it needs the `DEFERRING_TASK_TYPES` filter; `tool_use_id` is optional, so the join
key can be absent; and a terminal state can arrive only as `task_updated`, so a clearing
branch is required or the UI shows stop buttons for dead tasks forever. That is a live-task
table. `planning/2026-08-14-a-halt-has-to-reach-work-that-has-not-started-yet.md` §3 is the
prior art and still holds.

**Three things are called "task" here**: the board's `TaskId`, the SDK's `task_id`, and
`AgentRecord.task` — the launch prompt rendered as the session headline. A control labelled
"stop task" sitting near that headline reads as "stop this session", which is the one thing it
would not do. Label it **stop sub-agent**, the word this codebase already uses everywhere else.

The host for it does not exist yet either: `health.py` renders sub-agent rows as static text
with no per-row widget. Turning that display into a list of handles is separate UI work.

## D10 — Rewind is deferred as a conceptual question, not as a backlog item

`rewind_files` exists and is gated behind `enable_file_checkpointing`, which `_options()` does
not set. A Claude Code user expects to be able to undo. It is being left out of this round
deliberately: undo in a fan-out orchestrator is not the same object as undo in a single
session, and the question of what a rewind means when several agents have written since is a
design problem rather than an unexposed option. Recorded so the absence reads as a decision.

## The item that was not asked for and outranks most of the list

**A refused control request is silent end to end.** `_session_action` discards the future from
`Bridge.submit`; `Query._send_control_request` raises when the CLI answers with an error and
again on timeout; `AgentSession.interrupt` has no handler. Nothing logs it — `asyncio` does not
warn because `run_coroutine_threadsafe`'s chaining retrieves the exception, and no exception
handler is installed on the loop.

This is five call sites rather than five controls, and it already covers `send` and `close`.
`bridge.submit`'s own docstring states the contract being violated: "the future is for
cancellation and error reporting". The precedent for the fix is in the tree — `_poll_context`
wraps `get_context_usage` in try/except and logs.

**This goes first.** Every control in this record lands on `_session_action`, so building them
first means shipping five affordances that each inherit a silent-failure mode and each look
fine when tested by hand.

## The two panes disagree about a terminated session, and neither behaviour has been argued

`health.py` disables interrupt and close on `is_terminal`. `compose.py` leaves both enabled,
directly below a line reading "it can no longer be messaged".

This looks like a `compose.py` bug and making it match `health.py` would be wrong. The driver
keeps reading after an error result — a session that errored and then answered is an ordinary
session again — so a `FAILED` record coexists with a live client holding a pool slot.
`health.py` greys out the controls for exactly that session and `_expand_failure` offers only
dismiss and relaunch, which makes `compose.py`'s always-enabled buttons the only route to stop
a session that errored mid-turn and is still consuming a slot. "Fix compose to match health"
creates an unreclaimable slot leak reachable by an ordinary API error.

Recorded as an open question rather than a fix. The right answer is probably that `is_terminal`
is the wrong predicate for these two buttons and liveness of the client is the right one.

## Probes, before the affected sections harden

Both are one run and neither is answerable by reading.

1. **Does plan mode change behaviour?** `scripts/verify_questions.py` already runs
   `permission_mode="plan"` against a hook that unconditionally allows. Two changes: stop
   filtering `permission_mode` out of the recorded hook input, and record the tool list.
   Settles D7 and, incidentally, gives the first recorded observation of the effective mode.
2. **Which session id does the CLI use** under `--session-id` + `--resume` + `--fork-session`?
   Settles D8's route. `scripts/verify_worker_context.py` is the established shape.

## Sequence

1. The Bridge error path. Everything else inherits it.
2. `Esc` to interrupt, plus the rail context menu and Session menu surfaces, carrying the
   controls that already work — interrupt, close, and `set_model` with the record and
   sub-agent inherit-fallback updated together so the UI cannot claim a model the process is
   not running.
3. The policy dial over `classify`, with its display.
4. Probe 1, then plan mode if it earns it. Probe 2, then resume.
5. The live-task table, then stop-sub-agent.

## Records this one corrects

- `planning/2026-08-10-launcher-as-a-modal.md` — surface list is stale: it says the modal
  exposes task, working directory and model, and that Enter launches. There are five fields
  and the binding was reversed by `planning/2026-08-11-prompt-boxes-send-on-ctrl-enter.md`.
  Its refusal of a launcher `permission_mode` field still stands and this record agrees with
  it, on different grounds — a toggle would not disable the gate, it would look like it does,
  which is the worse failure.
- `planning/archive/2026-08-10-conversational-sessions.md` — the `ExitPlanMode` conclusion
  appears to generalise from a run in the wrong mode. See D7.
- `orchestrator-design.md` §3.1 — the `set_permission_mode` row for per-node trust promotion
  was retired in reasoning by the 2026-08-11 record and the table was never updated.
  `driver.py`'s module docstring names the same method as one the orchestrator "needs" and
  nothing calls it.
- `AgentRecord.brief`'s comment says "nothing reads it yet". Two readers now exist. Per
  CLAUDE.md's rule on tense, that comment is history rather than a live constraint.
