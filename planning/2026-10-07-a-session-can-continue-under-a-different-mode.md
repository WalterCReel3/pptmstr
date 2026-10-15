# A session can continue under a different mode

**Dated:** 2026-10-07 · **Status:** a proposal to decide from; nothing built, and almost nothing
decided — see "What the operator has said" for the whole of what is · **Found by:** operator
remark during the worktree research that a setup-then-autonomous UX flow "is worth thinking about"
· **Sibling:** `planning/2026-10-07-a-session-can-run-in-its-own-worktree.md`, written in parallel
and not read while this was written; it is cited by filename and nothing here depends on its text

The operator already changes a session's mode by hand: an attended session to orient and set up,
closed, then resumed under the autonomous mode. This record asks whether that should be one action,
lays out the three mechanisms that were considered for getting an agent past the sandbox when a
step needs it, and lists what the action would have to handle. It builds nothing and settles
little.

**Three kinds of statement, kept apart on purpose.** Every numbered item carries one tag:

- **(D)** — what the operator said, in their words where the words are on record. The list is
  short, and a quoted reply is evidence of that reply and no more.
- **(R)** — a lead recommendation. Proposed, not agreed unless a (D) item says so.
- **(O)** — open. Options and costs are laid out and **no option is chosen**.

Unnumbered prose is description of the code or of an earlier record, and carries an evidence
class instead. The one exception is the section "What lead attached to it", which is lead's
reasoning and is labelled as such.

**Evidence class.** Read-derived unless it says otherwise: the code and the earlier records were
read on 2026-10-07 and are cited by symbol with the line as it stood that day. Nothing was run
for this record. Measured claims are borrowed from two probes and say so, with n and versions:
`scripts/verify_worktree_containment.py` (staged, uncommitted; output in
`/tmp/pptmstr-wt-research/containment-probe.txt`) and the SDK-surface probe
(`/tmp/pptmstr-wt-research/sdk-surface.txt`), both against git 2.39.5, `claude-agent-sdk` 0.2.164,
bundled CLI 2.1.292. **Those `/tmp` files do not survive a reboot**; the numbers this record leans
on are carried inline.

---

## What the operator has said

**D1 — The operator already does this by hand.** Their words: "I've actually done setup sessions,
closed them, and then resumed the same session in autonomous in order to keep orienting context
and initial instructions. Maybe a UX flow that makes that approach easier is worth thinking
about." The stated reason is keeping the orienting context and the initial instructions, and no
other reason was given. *This is a report of doing it, not of what resulted. Nobody has reproduced
it under observation; whether those resumed sessions were actually contained is exactly probe P2;
and the record does not know which route was used* — the launcher's resume picker with the mode
ticked is the only one the code offers (`ui/launcher.py:LauncherState.spec`, l.773:
`resume=self.picker.selected`, `policy=self.granted_policy`).

**D2 — The operator acknowledged that containment and dependency installs are in tension.** Lead
raised that a contained agent probably cannot install dependencies; the reply was "Good point
about autonomous mode". That acknowledges the point. It does not say bootstrapping is why the
operator runs setup sessions (D1 gives their reason), and it is not agreement to anything in "What
lead attached to it" below.

**D3 — The operator floated two related ideas.** Their words: "I could imagine a parallel feature
being a way to switch the mode or even have a break-glass sub-agent that is allowed out of the
sandbox but requires approvals. That would be a 'setup' or 'elevated' sub-agent."

**D4 — The operator replied "That sounds reasonable" to one lead recommendation, in three
parts.** After D3, lead laid out the three mechanisms below and recommended: ship worktrees with
the setup-session-first pattern; treat a *requestable elevated session* as the parallel feature;
avoid the per-command break-glass. **That reply covers the ordering of the three mechanisms and
nothing else**, and it is assent to a recommendation as summarised in conversation, not a ruling
made against the costs written out below. It is not agreement to the "Continue as…" action, to its
two-directional form, or to any item tagged (R) or (O) in this record.

---

## What lead attached to it

Lead's reasoning, not the operator's. Nothing in this section has been agreed.

**Lead's link — a fresh worktree that needs bootstrapping is the case that makes the flow
matter.** A contained session cannot do it, so the setup has to happen attended first. The
"cannot" is **inferred, not measured**: `sandbox.py:ALLOWED_DOMAIN` (l.40) is one host,
`api.anthropic.com`, under `network.strictAllowlist`; the containment probe measured an
off-allowlist host refused from a contained session (`git ls-remote --heads
https://github.com/…` → `deny network-outbound github.com:443 (host is not on the allow list)`,
both contained arms of runs 2 and 3, n=4 commands). **No dependency install was attempted by any
probe** (P4).

**Lead's proposal — the sibling record's missing-paths notice is the prompt for this flow.** The
notice itself is the operator's tentative suggestion there (a flag that a worktree needs
bootstrapping "might be helpful"), and how it is derived is tagged (R) in that record. That it
should lead the operator into a continue action is lead's, and is proposed in this record only.

---

## Why the seam is a relaunch and not the dial

Two things are called the mode and they change at different moments.

**Gate policy is live.** `2026-08-22` D1 built the dial over `approval.classify` rather than the
SDK's `permission_mode`. In the code that is `driver.AgentSession._policy` (l.991), written by
`set_policy` (l.1095) from the UI thread and read by `_policy_for` (l.1126) on every call.

**Containment is fixed when the CLI process starts.** `AgentSession.containment` (l.952) is the
JSON string `sandbox.containment_settings()` returns; `_options` (l.2009) passes it as
`settings=self.containment` (l.2038), which is `--settings` on the argv. There is no control
request that changes it — `2026-08-22` D6 enumerates the SDK's outgoing control subtypes and none
touches settings (enumerated against an older SDK; **not re-enumerated against 0.2.164**).
`2026-09-03` §4 makes the same point as a design fact: containment "is a property of how the CLI
subprocess is launched".

So attended → autonomous cannot be a dial move. The policy could be widened live; the sandbox
could not be added under it, and the pair is "one arrangement and not two options"
(`driver.UncontainedAutonomy`, l.202).

Three facts about the code as it stands that the task specification did not carry, and that
change what the proposal has to say:

- **Live narrowing already exists and is not the break-glass.** `ui/health.py` draws "narrow to
  strict" for any session whose live policy is not `STRICT` (l.198–204) →
  `app._revoke_policy` (l.626) → `AgentSession.revoke_policy`. On an autonomous session that
  yields `STRICT` *with the sandbox still on*: the gate asks again, and a `Bash` the operator
  approves still cannot reach the network. `LauncherState.spec`'s own comment names that
  arrangement — "a sandbox around a gate that is still asking". An operator who wants an
  autonomous session to do one unsandboxed thing gets nothing from the button that looks like it.
- **Live widening is unguarded below the UI.** `set_policy` takes any `Policy`.
  `_uncontained_autonomy` (l.2240) is consulted once, in `run` ahead of the client (l.2295), and
  never again. The only thing stopping `set_policy(Policy.AUTONOMOUS)` on an uncontained process
  is that nothing calls it: `grep` finds `revoke_policy` as the sole caller outside `driver.py`.
  A "Continue as…" implemented as a dial move would produce exactly the session
  `UncontainedAutonomy` exists to refuse, with no error.
- **The permissions are deliberately not carried by the one-click relaunch.**
  `model.LaunchSpec.from_record` (l.500) drops `containment` and `policy`, and the field comment
  says why: "a fork is the one launch path with no surface on which an operator chooses either of
  these. A value that arrives without the surface that sets it is a value nobody chose." Any
  continue action is a new surface that sets them, and inherits that argument rather than
  escaping it.

---

## The three mechanisms considered

Lead recommended an order for them and the operator's reply was "That sounds reasonable" (D4).
That is as much as holds the order in place. Each is recorded with its cost so the order can be
re-argued from the record rather than from memory.

### 1. A setup session first, then an autonomous session in the same directory

Works with what exists — this is D1. Costs: it is a manual sequence across two surfaces (close on
the session, then the launcher's picker), and the picker launches into the directory in the
launcher's cwd field rather than the one the session ran in (O1). It gives the agent no route back
out: once autonomous, a step that needs the network is a step it cannot take, and
`templates._unattended_lines` already tells a team to "record as blocked, naming what it needed,
rather than to attempt another way".

### 2. A per-command break-glass through the permission flow

The CLI's own escape: on a sandbox violation the model may retry with a parameter that asks to run
unsandboxed. `sandbox.containment_settings` disables it, and its docstring is the argument
(l.92–95):

> ``allowUnsandboxedCommands: false``. Otherwise a violation lets the model retry with
> ``dangerouslyDisableSandbox``, which routes through the permission flow -- the same flow this
> mode has auto-approving ``Bash``. Containment would be a one-parameter bypass the model can
> reach and the gate waves through.

Enabling it would need the gate to treat such a call as never auto-approvable. What reading
establishes about that, and what it does not:

- **The gate can tell which agent called.** `_gate_tool_use` reads `agent_id` off the
  `PreToolUse` payload (l.1543) and `_policy_for` already scopes on it. The task specification
  carried this as unverified; it is settled by reading.
- **Under the autonomous mode nothing parks.** A `REQUIRE_APPROVAL` under `Policy.AUTONOMOUS` is
  *denied* by `_no_reviewer_reason` (l.1651–1685: "no operator will be asked and retrying will not
  change that"), and `2026-09-03` §12 defines the mode by that. So "the gate always parks such a
  call" is not an addition to the mode; it reverses the 09-22 amendment's definition of it for one
  class of call. **The task specification understated this.**
- **Not established:** that the request reaches `PreToolUse` as a key in `tool_input` under that
  name. The name appears in this tree only in `sandbox.py`'s docstring and one test docstring;
  nothing here has observed the payload.

Costs, beyond the reversal: it is per call, so one bootstrap is many approvals; the model can
reach for it on *any* denial, including the ones containment exists to produce; a parked approval
stalls an unattended run for up to `APPROVAL_TIMEOUT_S`; and once a call is unsandboxed it has the
operator's whole machine regardless of which agent asked, so scoping the *request* to a role does
not scope the *reach*.

**R1 — do not build this.** *Lead recommendation; one of the three parts the operator's "That
sounds reasonable" was a reply to (D4).*

### 3. An elevated agent genuinely outside the sandbox

Not possible inside one session. `2026-09-03` §8b.6, verbatim: "**Per-role containment does not
exist.** Sandbox config is per-CLI-process and sub-agents share the parent's, so every role in a
team gets the same boundary — including roles whose `Role.tools` never mentions `Bash`." §12's U6
measured the sharing (a `tools=None` sub-agent confined identically to its lead, CLI 2.1.226).

The workable form is a *separate, uncontained, attended* session that a contained lead can
**request** through a bus tool, raised to the operator as a needs-you item. Costs: the largest of
the three — a new bus tool, a new obligation kind, a session launched by something other than the
launcher, and a channel for the result to come back across a boundary whose point is that things
do not cross it. It also parks at a human, so an unattended run still waits.

**R2 — a parallel feature, later, and out of this record's build scope.** *Lead recommendation;
as a direction, one of the three parts D4's reply was to.* Nothing below designs it.

---

## The proposed flow

**R3 — One action on a session: "Continue as…" a target mode.** End the current CLI process at a
turn boundary and start a new one that resumes the same session id in the same cwd under the
target policy, with containment present or absent to match. *Lead recommendation. The operator has
said a flow that makes the hand route easier "is worth thinking about" (D1) and floated switching
the mode (D3); neither is agreement to this shape.*

**R4 — One mechanism, both directions.** Setup → autonomous is D1 made into an action.
Autonomous → attended is the whole-session break-glass: the session comes back under the ordinary
gate with no sandbox, the operator approves what the bootstrap needs, and `allowUnsandboxedCommands`
is never touched. That is mechanism 2's use case answered at session grain instead of call grain.
*Lead recommendation.*

What R4 costs that R3 alone does not: the return leg hands a conversation that ran unreviewed to
a process with the whole machine. The gate is `STRICT` again and asks about every call, so the
exposure is what an operator approves on the strength of a transcript nobody watched — a human
cost, not a mechanical hole, and the one to weigh before agreeing to the second direction.

### How a mid-life change sits with `2026-08-22` D2 and its reversal

D2: "**Spawns stay parked at every preset**, or the ladder stops below a preset that would release
them", because "the blast radius is a fan-out rather than a file". `2026-09-03` §8 "Remaining
open" reversed it for the autonomous policy only, on a changed premise — "sandbox configuration is
per-CLI-process and sub-agents share the parent's, so under §8's containment each additional agent
has the same bounded reach as the first" — and says D2 "remains correct for every preset that is
not contained". §12.4 records the reversal as built.

A relaunch keeps both records true, and a dial move would not:

- Continuing *into* the autonomous mode starts a new process, so the spawn release and the sandbox
  it is premised on arrive together, checked by `UncontainedAutonomy` at start.
- Continuing *out of* it ends the process. No sub-agent spawned under the release survives into
  the uncontained session (O3), so D2's parked-spawn rule holds for everything that runs there.
- D2 was written for a ladder climbed live. Its reversal is safe only because the top rung is not
  reachable that way. **A continue action must not become the live route**, which is the second
  bullet of the previous section stated as a constraint.

What neither record anticipated: a session whose history spans both regimes. D2 and the reversal
each describe a session by its current rung; O6 is about saying so on screen.

---

## What the flow must handle

Each item is open. Where reading settled a sub-question it says so and leaves the design choice.

### O1 — Which directory the continued session runs in

The brief is recoverable only if cwd and session id are both unchanged: `brief.session_dir`
(l.115) is `<root>/<cwd-slug>/briefs/<session-id>/`. `2026-08-22` D8 states it; `2026-08-31` D6
relies on it.

**The picker launches with the launcher's cwd field, not the row's recorded cwd. Confirmed by
reading.** `LauncherState.spec` resolves `self.cwd` (l.786); `SessionPicker.select` (l.309) takes
"this launch's working directory rather than the row's" by its own docstring; `_session_row`
passes the field (l.883). `SessionRow.cwd` is never consulted on the launch path.

**The mismatch is silent, and that is measured.** A session created in a host-made linked
worktree and resumed with `cwd` set to the main checkout started, under the same session id, with
`init cwd` = the main checkout (sdk-surface.txt F2-R3; n=1; the same from an unrelated directory,
F2-R5; no model turn ran — the probe was unauthenticated, so this is the handshake and nothing
after it). Upstream documents it as intended from CLI 2.1.223: "You can resume from any working
directory." And `scan_sessions` (l.97) uses the SDK's worktree fold on purpose, so a worktree's
sessions are listed in the picker opened on the main checkout (F2.5: 5 rows with the fold, 1
without).

Put together: with worktrees in use, picking a worktree session from the launcher's default
directory resumes it *in the main checkout*, derives an empty brief directory, and — if the mode
is ticked — makes the main checkout the sandbox's writable region. `launcher.refusal_for` stops
that only when the main checkout is pptmstr's own. **This is a defect today, independent of this
proposal.** Its fix — a resumed session launches in its recorded cwd — is the part of the sibling
record's step 1 that this record shares (Sequence, step 1).

Open: whether a continue action needs the picker at all. For a session still in the tree,
`AgentRecord.cwd` holds the resolved directory (`LaunchSpec.from_record` already reads it), so a
card action has the right answer without the picker. Across a pptmstr restart there is no record
and the picker is the only route. Options: (a) the action reads the record and the picker fix is
independent hardening; (b) both routes go through one function that takes cwd from the session's
own record of itself. Not chosen. What is open is the *action's* route; that the existing picker
route must stop launching into the launcher's directory is not.

### O2 — What resume restores, and what the new launch must supply

Settled by `2026-08-31`: the conversation comes back; task text, model and template are "whatever
this launch supplied"; the brief is the exception, via cwd and id. A continue action from a live
record can supply task, model, template and brief from that record — `from_record` carries all
four — which the picker route cannot.

**The session id on this path is not an open question, and the task specification treated it as
one.** `2026-08-22` D8's "which id under `--session-id` + `--resume` + `--fork-session`" was
routed around by `2026-08-31` D3: `_options` passes `resume` alone with `session_id` omitted
(l.2030–2031), and `_check_effective_id` fails the session if the CLI reports a different id. What
remains unmeasured is narrower and is the 08-31 record's own caveat: "No live resume of a session
across a pptmstr restart was measured", and nothing has measured a resume under a *different
settings string* (probe P1).

Open: which of the launch's fields the action lets the operator change. A changed template on a
resumed conversation gives a lead a team its history never mentions. Not chosen.

### O3 — What does not survive, and whether the board does

Ending the process ends everything in it: in-flight sub-agents, and any call parked at the gate
(`SessionPool.close`, l.102, cancels the session's task). Read-derived; what the CLI flushes to
the transcript for a turn cancelled mid-call is not established.

**The board is scoped by session id, and survives only inside one pptmstr process.**
`Task.belongs_to` (`model.py` l.1229) scopes by the declarer's session id, and a resumed session
keeps its id, so tasks the earlier process declared are still on the continued session's board.
The store is in memory — no persistence was found in `store.py`, by grep, not by an exhaustive
read — so across a pptmstr restart the board is gone while the brief and the transcript are not.

Two read-derived hazards specific to relaunching the *same id inside one process*. Neither has
been executed, and both may already be reachable by the hand route in D1:

- `SessionPool._run`'s `finally` does `self._running.pop(session.node_id, None)` (l.84). The old
  and new sessions share a `node_id`. If the new one is submitted before the old one's
  cancellation has unwound, the old `finally` removes the *new* session's entry: the slot count
  is wrong and a later `close` finds no task to cancel. By hand the gap is human seconds; as one
  action it is a race unless the action waits for the old run to finish.
- `store`'s `AgentSpawned` arm assigns a fresh `AgentRecord` for the node (l.190–218) with the new
  session's empty `Transcript`. The root's on-screen history from before the switch is replaced;
  the CLI's JSONL keeps it. Tasks claimed by sub-agents of the old process stay claimed by nodes
  that will never run again.

Open: whether tasks held by dead sub-agents are released on a continue, by whom, and whether the
continued lead is told. Not chosen; P3 is what would show the present behaviour.

### O4 — The agent's expectations are stale in both directions

After setup the transcript shows installs succeeding; resumed under containment the agent will
retry them and be denied. After an autonomous stretch, resumed attended, it believes nobody is
reading.

What exists: `driver._system_prompt` builds the briefing from the live policy at launch, and
`templates._unattended_lines` (l.199) already states the autonomous constraints — writes outside
the launch directory denied, `Bash` reaching "no host but the API", blocked steps to be recorded.
Two limits, by reading: **a solo session gets none of it** (`lead_briefing` returns
`template.lead_prompt` for a template with no roles, `SOLO.lead_prompt` is `""`, and
`_system_prompt` returns `None`), and **whether a resumed conversation is given the new launch's
system prompt at all is not established** (P1).

**R5 — pptmstr authors the first message after the switch, stating the new constraints.** *Lead
recommendation.* For attended → autonomous: no network beyond the API, writes bounded to cwd, and
add files to git by path. The last is measured: `git add -A` in a contained session printed
`error: .bash_profile: can only add regular files, symbolic links or git-directories` /
`fatal: adding files failed` in both the worktree arm and the clone arm (probe run 4, n=1 per arm;
the `rc=0` on that step is `git status`'s, not `git add`'s; the unsandboxed host leg shows no such
error). The cause — non-regular stubs the sandbox places in cwd — is inferred from the `??`
listing, not established.

Open, and R5 does not answer them:

- The resume path already has a first message: `spec.task` is "sent to it as the next message"
  (`_resume_section`'s caption). Is the authored text that message, a prefix to the operator's, or
  a separate turn? For a team template with the brief field empty, `app._seed_brief` (l.570)
  appends the task text to the session's brief as an amendment (`2026-08-31` D6) — so an authored
  message sent as the task would also become a premise every worker reads. That may be wanted. It
  is a consequence either way.
- Whether a message is enough against a transcript full of contrary evidence, versus the system
  prompt, versus both. Nobody has watched a resumed contained agent meet its first denial.

### O5 — The launch hold, and the other checks that live only in the launcher

`2026-09-03` §12 "What actually gates shipping, revisited": "An engaged launch is also held while
the task box is a single line and no existing brief is named". In code,
`LauncherState.premise_is_thin` (l.701) is `False` when the brief field is non-empty, else true
when the task has no line break; `launch_hold` (l.731) asks it first.

**Does a continued session's recovered brief satisfy it? As built, only if the field is filled.**
`SessionPicker.select` computes `recovered_brief` and `_resume_section` *offers* it behind a "use
this session's brief" button (l.944–948). Unpressed, the field is empty and a one-line task is
held, even though the session has premises on disk. Whether a recovered brief *should* count is
the open part: the hold's stated reason is that the task text is the whole specification for a run
nobody will correct, and a continued session has a conversation behind it as well.

**The larger point is where these checks live.** The thin-premise hold, the own-checkout refusal
(`refusal_for`, l.359) and the version-gate hold are all methods of `LauncherState`.
`app._launch` re-checks only the CLI floor (l.501–521) and `AgentSession` only that containment is
present. A continue action that builds a `LaunchSpec` anywhere but the modal runs none of the
first two — including the one that keeps an unattended agent out of pptmstr's own checkout.
Options: (a) the action opens the launcher pre-filled, so every existing check and the D3 display
apply and the action is one click plus a confirm; (b) the checks move below the modal so any
caller meets them; (c) the action re-implements them. Not chosen. (c) is the "second spelling of
this decision" that `floor_refusal`'s docstring already apologises for once.

### O6 — Display

`2026-08-22` D3: "Displaying the dial is part of shipping the dial, not a follow-up." The current
state is displayed: the launcher's containment section, `app._policy_of` / `_relaxed_count`, and
the health pane's narrowing button all read the live policy.

What is not displayed is that a session *changed* mode. After a continue the root record is new
(O3) and reads as a session launched under its current rung. A session that spent an hour
unreviewed and is now attended looks, to the operator approving its next call, like one that was
always attended — R4's cost with nothing on screen to prompt it.

Open: where the history lives. A transcript segment at the switch is the smallest thing and is
lost with the transcript on the next continue unless it is also written somewhere durable; a
field on the record does not survive a pptmstr restart; an entry in the brief does, and is read
by workers. Not chosen.

### O7 — The autonomous requirement that cwd be a linked worktree

The sibling record proposes that under the autonomous mode cwd must be a linked worktree. **That
is a lead recommendation there, not an operator decision** — it is being relabelled so by the
correction to that record, which is the source for this statement; the sibling's text was not
re-read here. The operator's words on the subject are "I want to use worktrees for fully
autonomous", which is a wish to use them and not a refusal of the mode outside one. A session
continued in place into the autonomous mode would already be in a worktree if it was launched in
one.

The dependency runs one way: if the requirement is adopted, O1 is what makes "already in one"
true, because a continue that lands in the launcher's directory is not in the worktree. If it is
not adopted, nothing in this record changes. The containment probe's result on what a contained
worktree session can reach in the shared git directory belongs to that record and is not restated.

### O8 — Naming

Nothing is picked. The collisions, so that whatever is picked is picked against the full list:

- **continue** — the CLI's `-c, --continue` ("Continue the most recent conversation in the current
  directory") and `ClaudeAgentOptions.continue_conversation`, neither of which this is.
- **resume** — the launcher's picker, `ClaudeAgentOptions.resume`, and `AgentResumed`, which is a
  *sub-agent* waking under its old id.
- **fork** — three things, per `2026-08-22` D8 and still unrenamed per `2026-08-31`:
  `fork_session` the option, `fork_session()` the function, and the health pane's button. The
  button sits one position from where a continue control would go and does the opposite thing
  with context.
- **relaunch** — the inbox's "retry in a new session" (`actions.relaunch`).
- **mode** — `2026-08-22` D1's table already has three (SDK `permission_mode`, gate policy,
  `plan`); this codebase also says "the mode" for the autonomous policy-plus-containment pair.
  "Continue as a different mode" means the pair, which none of the three rows is.
- **setup**, **elevated** — the operator's words. "Setup" is used for two different things: a
  phase of one session ("setup sessions", D1) and the break-glass sub-agent (D3). "Elevated" is
  used for the sub-agent only.

---

## Where the task specification and the tree disagree

Collected so a reviewer can check them in one place. Each is argued where it arises.

1. *"Whether the gate can tell which agent called is unverified."* It can; `agent_id` is on the
   hook payload. (Mechanism 2.)
2. *"Using it would need the gate to always park such a call."* Under the autonomous policy the
   gate denies instead of parking, by `2026-09-03` §12's definition; parking is a reversal, not an
   addition. (Mechanism 2.)
3. *"The three-flag arbitration question in D8."* Moot since `2026-08-31` D3; the id is verified,
   not arbitrated. (O2.)
4. *"The action on the session card/menu (see 08-22 D5 for the two surfaces)."* **Neither surface
   exists.** `ui/rail.py:draw` takes `(snap, focus, state, now)` and no action bundle (l.373;
   called at `app.py` l.822); `app._menus`' Session menu holds one item, "New Task…" (l.667–673).
   The session controls that exist — interrupt, close, fork, narrow — are buttons in
   `ui/health.py` (l.181–204). (Sequence.)
5. *"The mode dial … is live."* True of narrowing from the UI. Widening has a driver method and no
   caller, and no guard. ("Why the seam is a relaunch".)

Noticed in passing and not this record's to fix: `2026-09-03` §12 says "`strict_mcp_config=True`
(§8a.4) is also unbuilt. Nothing in the tree sets it." `driver._options` sets it (l.2098).

---

## Probes owed, before the affected sections harden

Listed, not run. None is answerable by reading.

- **P1 — End-and-resume under a different settings string.** Start uncontained, complete a turn,
  end the process, resume the same id in the same cwd with `settings=containment_settings()`.
  Record: the id on `init` and on the `ResultMessage`; that the earlier turn is in context; whether
  the system prompt the resumed model answers to is the new launch's. Then the reverse direction.
  Settles O2's residue and half of O4. `scripts/verify_worker_context.py` is the established shape
  for "what does the agent actually have".
- **P2 — A resumed contained session is actually sandboxed.** Same on-disk method as
  `scripts/verify_sandbox_gate.py`: a write outside cwd and an off-allowlist fetch from the
  *resumed* session, outcomes read from the filesystem by the script and never from the model's
  account, against an unsandboxed control arm. Without it R3 rests on "`--settings` applies to a
  resumed process as it does to a fresh one", which is an assumption. **This is the one that
  gates the feature**: if it fails, the hand route in D1 has been running uncontained.
- **P3 — What the board and the tree show after a continue**, inside one pptmstr process: the
  root record, the transcript pane, tasks declared before the switch, tasks held by sub-agents
  that died with the old process, and the pool's running count if the relaunch is immediate. This
  one is a unit test over `SessionPool` and the reducer for the two hazards in O3, plus one
  driven run for the rest.
- **P4 — A bootstrap under containment**, once: `./bootstrap.sh` or a `pip install` from a
  contained session in a fresh worktree. The "cannot" in lead's link is an inference from a
  refused `git ls-remote`; lead's motivating case rests on it. The operator's own reason (D1)
  does not.

---

## Sequence

**R6 — proposed order.** *Lead recommendation throughout.*

1. A resumed session launches in its recorded cwd, not the launcher's cwd field. A defect today
   on the picker route (O1) whichever way this proposal goes. **This is the only part of the
   sibling record's step 1 that is shared**: that step also carries a visible refusal when the
   recorded worktree is gone and launch-failure reporting, which are that record's and are not
   proposed here. It does not settle O1's open half — whether the continue action goes through
   the picker at all — because the picker route exists and is wrong with or without the action.
2. P2, then P1. Cheap, and P2 decides whether there is a feature.
3. The action. On which surface is open: `2026-08-22` D5's two surfaces are unbuilt
   (disagreement 4), so it is either one more button in `ui/health.py`'s row or it waits for the
   rail menu. O5's option (a) — the action opens the launcher pre-filled — would make the surface
   question small.
4. The authored first message (R5), after O4's sub-questions are answered.
5. The history display (O6).

Later and separate: the requestable elevated session (mechanism 3, R2).

## Out of scope

- Mechanism 2, by R1.
- The design of mechanism 3.
- Whether a linked worktree is an acceptable substitute for `2026-09-03` §8's mandatory clone.
  That is the sibling record's question and the containment probe's evidence.
- Carrying policy or containment through `LaunchSpec.from_record`. The field comment's refusal
  stands; a continue is a different act from a fork and should not be built by widening the fork.
- Renaming anything in O8.

## Records this one touches

None is edited by this record.

- [`2026-08-22-session-controls-and-the-mode-dial.md`](2026-08-22-session-controls-and-the-mode-dial.md)
  — D1 (the dial is over gate policy) is the reason the seam is a relaunch; D2 and D3 are engaged
  above; D5's surfaces are found unbuilt; D8's vocabulary collisions are extended in O8.
- [`2026-08-31-sessions-can-be-resumed-and-bookmarked.md`](2026-08-31-sessions-can-be-resumed-and-bookmarked.md)
  — D2/D3 are why the session id is not an open question here; D6 is why an authored first
  message can land in the brief. Its two unresolved picker items (the `"."` default and the
  stale-directory scan) are neighbours of O1 and are not the same defect: those are about which
  sessions are *listed*, O1 is about where the picked one *runs*.
- [`2026-09-03-a-dangerously-autonomous-mode.md`](2026-09-03-a-dangerously-autonomous-mode.md) —
  §4 and §8 for containment being per launch; §8 "Remaining open" and §12.4 for the D2 reversal;
  §8b.6 for mechanism 3; §12's unattended definition for mechanism 2; one stale sentence noted
  above.
- `planning/2026-10-07-a-session-can-run-in-its-own-worktree.md` — the recorded-cwd part of its
  step 1 is shared; O7 defers to it; its missing-paths notice is named in lead's proposal above.
