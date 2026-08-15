# A role runs exactly one agent, and the briefing and the bus agree on it

**Dated:** 2026-08-14 · **Status:** phases 0–4 landed, plus one this note did not
contain; 5–7 open — see [What landed](#what-landed) ·
**Follows:** [`2026-08-12-a-message-has-no-sender-until-the-gate-gives-it-one.md`](2026-08-12-a-message-has-no-sender-until-the-gate-gives-it-one.md) §"First writer wins"

The operator reports that a `feature` session declares several tasks and then
starts one builder. Not a lead that refuses to delegate — a lead that delegates
once per role and waits. The board drains sequentially through a single worker
while independent tasks sit unclaimed.

That is the designed behaviour, in two places, and only one of them is written
down as a decision.

## The briefing says "one", four times

Executed, not reconstructed — `templates.lead_briefing(templates.FEATURE)`. **This
quote is the text as it stood before phase 2; it is the evidence for the change and
is deliberately not updated. `lead_briefing` no longer produces it.**

```
- **reviewer** — Reads what the builder produced and tries to break it. Cannot edit.
- **builder** — Implements the change. Give it one task at a time.

Start them in this order when the work allows it: reviewer → builder.

Use the Agent tool with `subagent_type` set to a role name to start one.
...
Break the work into tasks and put them on the board. Start the roles you
need. Then **wait** — read your inbox, answer concerns, and let the workers
work.
```

| line | text | reading |
|---|---|---|
| `templates.py:212` → briefing `:119-120` | "Give it **one** task at a time" | singular pronoun, explicit throughput cap |
| `templates.py:130` | "set to a role name to start **one**" | one agent per role name |
| `templates.py:124` | "Start **them** in this order… reviewer → builder" | a two-item roster, instantiated once |
| `templates.py:144` | "Start the **roles** you need" | roles are types selected, not instances counted |

Zero occurrences of "several", "more than one", "in parallel", "concurrently",
or any count noun applied to a role. The only numerals are "one" — twice, both
restrictive — and "two", appearing as *"two agents editing the same file is the
failure this structure exists to avoid"*, which generalises without much strain
to *do not run two writers*. The most emphatic instruction in the document is
the bolded **wait** at `templates.py:145`.

Declare tasks → start one of each role → wait is the literal reading of the text
we hand the model. The observed behaviour is the briefing working.

**This note is its own reproduction.** The lead that produced it read the same
generated text, faced a board with three declared tasks, and started one
investigator and one skeptic.

## The bus cannot address a second builder, and that part is deliberate

`driver.py:545-552`, on the declaration of `_roles`:

> First writer wins: two sub-agents of one type would otherwise silently
> retarget a role mid-run, and a concern going to whichever twin spawned last is
> worse than a concern that consistently goes to the first.

Written with `setdefault` at `driver.py:644`, and pinned by
`tests/test_driver.py:1136-1153`,
`test_a_second_agent_of_one_role_does_not_steal_the_address`, whose body asserts
`resolve_role("builder") == (session_id, "a-1")` after two spawns and repeats
the reasoning in a comment.

For a hypothetical builder #2 the consequences are total:

- `resolve_role("builder")` (`driver.py:559-570`) returns builder #1, always.
- `role_of(builder_2)` (`:572-579`) returns `None`, so its concerns render to
  every recipient as `[from another agent]` (`bus.py:169`).
- `known_roles()` (`:581-582`) never lists it, so `role_status` (`:584-600`)
  cannot report that it exists.

Builder #2 would be write-only: able to post, able to claim, never able to be
replied to.

**So the singular briefing is not an oversight — it is consistent with the
addressing model.** The consequence for sequencing is hard: a prompt-only change
converts "one builder" into "two builders, one of them unreachable". The wording
change is worthless, and actively harmful, until `_roles` is instance-keyed.

## Nothing tells the lead the board is deep

`declare_task` returns `f"Task {task_id} is on the board."` (`bus.py:222`) — no
count. `claim_task` returns the one task or "Nothing claimable right now"
(`bus.py:187-197`). `_pick_claim` (`store.py:480-496`) computes the claimable
set and discards its cardinality, returning `min(...)`.

A single worker looping claim → complete → claim drains the board in declaration
order and the lead never receives an observation that would make a second worker
look necessary. This is not the cause; it is the reason the cause is never
self-corrected.

## Not the SDK — a negative finding that closes an option

`claude_agent_sdk/types.py:1984-1988` defines `agents` as
`dict[str, AgentDefinition]`, "programmatically define custom subagents
invokable via the Agent tool". A definition keyed by name, with no arity. It is
serialised once into the `initialize` request (`_internal/query.py:233-234`) and
nothing tracks instances. `types.py:293-301` explicitly contemplates the
opposite of a singleton: "when multiple sub-agents run in parallel their
tool-lifecycle hooks interleave over the same control channel".

The ceiling is entirely ours. There is no upstream constraint to work around.

## The cap the operator wants is real, and is not the cap we already have

`pool.py:59-72` gates on `len(self._running) < self.cap`, keyed by
`session.node_id`, and `app.py:312` is its only production caller. It bounds
root sessions. `pool.py:4-7` justifies it as RAM — "every session is a Claude
Code CLI subprocess".

Sub-agents are not subprocesses. They share the parent's `session_id`
(`pool.py:53-55`) and their hooks interleave over the same control channel (SDK
`types.py:299`). *Inference, not measured:* N sub-agents cost one node process's
CPU and heap plus N concurrent API streams, not N processes. A ceiling is still
worth having — API concurrency and token burn are real costs, and horizontal
swarm is the failure mode a dynamic count invites — but **sizing it from
`concurrency_cap`, or describing it as the same constraint, would be a category
error.**

### The count a cap can read leaks, and the leak makes it a time bomb

`self._subagents: set[str]` (`driver.py:539`) is added at `:643` and discarded at
`:687`. It is a live set, and it is the only count reachable from the gate —
`driver.py:546-548` records that the store cannot be read from the asyncio
thread.

`_await_subagents` (`driver.py:1095-1114`) exits on `TimeoutError` after
`SUBAGENT_GRACE_S = 120.0` (`:94`), logs "N sub-agent(s) stopped reporting", and
leaves the set non-empty forever. Nothing removes those ids for the session's
life. A cap computed from `len(self._subagents)` is therefore a capacity counter
that can only ever be exhausted: it under-admits, then wedges the session
permanently. `2026-08-13-sub-agents-are-invisible-while-they-work.md:45-62`
already scopes the reaping fix from the store's side; it needs a
`self._subagents.discard` alongside.

**Fix the leak before the cap, or ship a session that stops being able to
delegate after four minutes of one unreported sub-agent.**

### Two counts of "how many builders" that must be allowed to disagree

The cap reads a live set on the asyncio thread. The rail reads
`projects.subagents_of` (`projects.py:103-109`), which applies **no state
filter** — deliberately: `rail.py:426-444` argues that the append-only property
is what the entire collapse rule rests on, and that dropping terminal sub-agents
would cost it.

So the operator will see "5 subs" in the rail while the cap believes 2 are live.
**Do not reconcile them.** They answer different questions — "how many has this
session run" and "how many are running now" — and `2026-08-13-a-card-is-an-agent.md:113-116`
records the same trap ("the two rules must differ"). If a live count is wanted on
screen it is a third projection over `AgentState.is_terminal` (`model.py:74-81`),
not an edit to either existing one.

## The count on the approval row is the interactive half, and it is missing

Every spawn parks: `Agent`/`Task` are in `_REVIEW` (`approval.py:62-63`),
`driver.py:725` parks, headless denies. There is no path by which a sub-agent
starts without the operator answering for it, and that mechanism survived a
deliberate attempt to break it.

But `approval.summarize` (`approval.py:128-130`) renders `spawn builder: <desc>`
— no ordinal, no running total. Twelve spawns are twelve individually reasonable
rows and an aggregate nobody chose. Per-item consent standing in for a decision
is the failure mode CLAUDE.md's "leans on the user" names, wearing a gate as a
disguise.

`approval.py` cannot fix it: it is pure by declaration (`:4-5`), receives only
`tool_name` and `tool_input`, and has no session. The ordinal has to be rendered
by the shell from the snapshot.

**This is a precondition of raising the count, not a follow-up.** A ceiling the
operator cannot see is a number enforced against them rather than for them.

## Disk templates: reversing a deferral, not a recorded decision

An earlier position in this session was that user-editable templates should
wait, because prompts in source are diffed and pinned —
`tests/test_templates.py:194-199` asserts the reviewer and skeptic prompts
contain their adversarial words, and `:144-164` pins the read-only tool lists.
A disk template bypasses every one.

That is a real property and it argues for **load-time validation that refuses a
bad file**, not for withholding the capability. Refusing to let the operator
shape the team because they might shape it wrong is the thing CLAUDE.md exists
to prevent. The deferral is withdrawn; the validation is the work.

**What the plan must engage rather than reverse silently.** `app.py:306-308`
defends falling back to SOLO on an unknown template name: "the task the operator
typed is worth more than the team shape they mistyped". STYLE.md §3
(`:178-180`) names a default-where-refusal-is-honest as a smell. Both are in the
repo, and they are not actually in conflict: a *mistyped name at the CLI* cannot
reach that path at all (`app.py:682-687` sets `choices=templates.names()`), and a
*malformed file on disk* is a different mistake with a different fix. Same
distinction `role_status` (`driver.py:584-600`) exists to draw. Refuse the file,
name the field, keep the others; fall back to the shadowed built-in rather than
to SOLO.

Two constraints on the loader:

- `tests/test_templates.py:27-31` pins `names()[0] == "solo"` and
  `BUILT_IN[0] is SOLO`, with the comment that the launcher's default is index 0
  and a reorder silently makes every untouched launch a team. Disk loading must
  preserve that regardless of load order.
- `ui/launcher.py:189` renders the combo from `templates.names()` while `:193`
  reads the description from `templates.BUILT_IN[state.template_index]`. Two
  index paths into two objects, correct today only because `names()` derives
  from `BUILT_IN`. A registry that can be reloaded while the modal is open
  repoints `template_index` silently. Collapse them to one lookup as part of the
  phase.
- `templates.py:7-12` imports only `dataclasses`, on purpose. A stdlib loader in
  the same module keeps that; a validation dependency does not.

## `declare_task` reports success it did not have

Found while tracing the board-depth question, and independent of it.
`store.py:401-411` drops a declaration whose id already exists or whose
dependencies would cycle — the comment says so. `bus.py:222` returns "Task
{task_id} is on the board." unconditionally. A cycle-rejected declare tells the
model it succeeded, and the lead then waits on a task that is not there.

`tests/test_bus.py:187-213` pin the rejection at the reducer, and every one of
them passes, because none goes through the handler. STYLE.md §2's "test the
wiring, not only the unit", exactly.

The fix is the question shape the other bus tools already use —
`bridge.ask(request_id, fallback)` before the emit (`bridge.py:230-246` records
that the ordering is load-bearing), a `ClaimSettled`-style effect from the
reducer, `app.py:104-110` settling it. It carries all of the plumbing that a
board-depth count would need, and it is worth landing whether or not the count
ever is.

## Ordered cost

| | work | why here |
|---|---|---|
| 0 | `_subagents` leak: `_await_subagents` (`driver.py:1095-1114`) discards survivors on timeout and emits their `AgentFinished` | Standalone defect. Every later phase that counts live agents is a time bomb until it lands |
| 1 | Instance naming in `_roles` (`driver.py:553`, `:644`; readers `:559-582`). First of a type keeps the bare name, later ones get a suffix. Rewrite `tests/test_driver.py:1136-1153` to pin *both* properties | Must precede 2. Contained to `driver.py` + four `bus.py` call sites; no UI or store reads `_roles`. Must not break `_model_for_type`'s `agent_type` join (`:608-623`) |
| 2 | Briefing wording: `templates.py:130` and the `builder` description at `:212`. Nothing in `tests/test_templates.py` pins the singular, so this is text plus one new test | The behaviour change the operator asked for. Harmful before 1 |
| 3 | Ordinal and running total on the spawn approval row, rendered by the shell from the snapshot | Precondition of 4: a ceiling the operator cannot see is not "leaning on the user" |
| 4 | Live-count cap at `driver.py:711`, before `classify`, denying via `permissionDecisionReason` (`:854-856`) with the cap named | The horizontal-swarm bound. Blocked by 0 and by 1 |
| 5 | Disk templates: per-file load, project > user > built-in, refusing validation (lowercase name; unique within template; name ∉ `{lead, main, root}`; non-empty prompt; a warning when a read-only role holds a write tool) | Independent of 0-4. Largest phase. Collapse the launcher's two index paths while here |
| 6 | `declare_task` answers truthfully: question shape, accepted or rejected-with-reason | Fixes a live defect and carries 100% of the plumbing for 7 |
| 7 | Board depth in that answer | One field on an effect that exists by then. Advisory only — 2 can say "one builder per independent task" in prose with no number |
| — | Reconciling the rail's sub count with the cap's | They measure different things. A live count is a third projection or nothing |
| — | Derived fan-out (one agent per file a scout found) | `declare_task`/`depends_on`/`claim_task` already express it, and correctly decouple worker count from work count |
| — | `count: N` expanding into `builder-1..N` `AgentDefinition`s | A second addressing scheme that disagrees with 1 about what `builder-2` means, and N copies of the worker prompt in the options payload |

7 was originally sequenced third. It moved because nothing is blocked by it, its
value is advisory, and its cost is the only new intent field and new effect
variant in the plan.

## Two hazards in 7, if it is built

**The count is fleet-wide unless scoped.** `Snapshot.tasks` is one global map and
`_pick_claim` (`store.py:480-496`) applies no session filter — already recorded
as live at `ui/board.py:37-44` ("a worker in one session can claim work another
declared"). A depth computed in the reducer counts other sessions' boards.
Scoping it on `declared_by[0] == session_id` moves a judgement `board.py` makes
in presentation into the pure core. Decide it deliberately.

**The count is structurally partial.** It is computed after *this* declare, while
the board is still being built, and
`2026-08-13-a-card-is-an-agent.md:236-245` measured that a prompt cannot compel
batched tool calls — the model issued each `Task` in its own assistant message
both runs. A lead declaring five tasks gets five increasing counts, only the last
of which is true. A `board_status()` question tool may serve the need better than
a per-declare partial.

## Verification boundary

**Executed:** `templates.lead_briefing(templates.FEATURE)`, whose output is
quoted above verbatim. `tests/test_driver.py:1136-1153`, `bus.py:205-222`,
`store.py:398-412`, and `tests/test_bus.py:285-296` were read directly to confirm
the first-writer-wins pin, the unconditional success string, the silent
rejection, and the unstarted `Bridge()` in the handler test.

**Everything else is reading**, against a working tree that is dirty in
`driver.py`, `store.py`, `bus.py`, `model.py`, `intents.py` and most of `ui/` —
so citations describe the tree, not HEAD.

Unsettled, with the reason each resisted:

1. **The operator's report is uncorroborated in-repo.** No captured team run in
   `planning/` records spawn counts. This note explains why one builder is the
   expected outcome; it does not independently confirm that one builder is what
   happened. A run with the counts captured would settle it.
2. **Whether two sub-agents of one `subagent_type` both start.** Evidence is
   `scripts/verify_subagent_usage.py:52` plus the per-sub-agent usage table in
   `2026-08-13-a-card-is-an-agent.md:207-217`, which implies it without stating
   it. Phase 1 is pointless if they do not.
3. **Whether an off-template `subagent_type` spawns.** `driver.py:617-620`
   asserts it in its own docstring; that is a comment, not a measurement. If it
   does, a per-role cap is not a ceiling and phase 4 must count totals.
4. **Whether the CLI's own `Agent`/`Task` tool description grants concurrency.**
   The CLI is a node binary and is not under `.venv`; a filesystem search for it
   found nothing. If it says multiple agents may be launched in one message, then
   `templates.py:130` is *overriding* a permission the model already has rather
   than failing to grant one — which strengthens phase 2 and changes its
   character.
5. **Whether making `declare_task` question-shaped breaks
   `tests/test_bus.py:272-324`.** The test builds a bare `Bridge()` and never
   `start()`s it, its own comment at `:289` saying "the loop is not needed here";
   `bridge.loop` raises `RuntimeError("bridge not started")` at
   `bridge.py:136-139`. Believed, not run. Same for `pump`
   (`tests/test_gate.py:50-59`), which discards the effects `app.py:109-110`
   settles and would therefore hang.
6. **The per-sub-agent resource cost.** The claim that N sub-agents are one
   process plus N streams is inference from a shared `session_id` and a shared
   control channel. If it is wrong, sizing the cap from `concurrency_cap` is
   correct after all.

## Not in scope here, noted while adjacent

`2026-08-13-sub-agents-are-invisible-while-they-work.md:177-181` records a live
defect at `rail.py:315-322` — an unbounded pip run spilling at about three. The
working tree's `_subs_signal` returns one bounded string. That note needs an
amendment or the next reader fixes something already fixed.

`Role.tools` cannot express a path-scoped write, so a template whose roles have
disjoint write scopes (a reproducer that may write tests but not source) is not
buildable as configuration. The gate has `agent_id` on `PreToolUse`
(`driver.py:694-697`) and `role_of` maps it, so it is buildable as enforcement —
a separate decision, and not a template.

---

## What landed

Phases 0–4 as ordered, plus **phase 1.5**, which this note did not contain and which
turns out to be a precondition of 2. Phases 5–7 are untouched and still open.

| | outcome |
|---|---|
| 0 | `_subagents` split into `_live_subagents` and `_seen_subagents`; `_await_subagents` settles survivors to FAILED and clears the live set |
| 1 | `_roles` keyed by address; `_address_for` allocates `builder`, `builder-2`, …; `role_status` grew a third arm; `ui/board.py` replays the allocation so the operator sees the address the bus routes to |
| **1.5** | **`_last_spawn_tool_use_id` replaced by a per-role FIFO ledger — new, see below** |
| 2 | briefing rewritten; the address convention is generated from the template's own role name |
| 3 | `inbox.spawn_marker` renders the ordinal and live count on the row and in the detail header |
| 4 | cap on `len(_live_subagents) + pending ledger entries`, `Settings.subagent_cap`, refusal names the cap and the `depends_on` alternative |

### The one thing that was wrong about the plan's own sequencing

**Phase 2 cannot ship without phase 1.5, and this note does not mention the join at
all.** `_expect_spawn` recorded the Agent call's `tool_use_id` into a single slot.
Measured against the real `AgentSession`, two spawns admitted before either
`SubagentStart` — which is what "start them together" produces:

```
_spawn_tool_use == {'agent-a': 'toolu_BBB'}
```

The first spawn binds the second call's id and the second binds nothing. Downstream
of `_sync_subagent_map`, one sub-agent's words land in the other's transcript and
the other's fall back to the **root's**, because `Translator._node_of` cannot find
its `parent_tool_use_id`. `SubagentProgress` misroutes identically. Phase 2 and the
CLI's own concurrency note both make that path normal, so the wording change ships
an attribution defect without the ledger.

The fix keys the ledger on `subagent_type`, which `PreToolUse` carries and
`SubagentStart` reports back as `agent_type` — a correlation key the fallback
rejected at `2026-08-13-a-card-is-an-agent.md:253-260` did not have. Two spawns of
different roles now join exactly; twins join one-to-one in FIFO order and may be
swapped relative to their descriptions, which is unavoidable and preferable to one
binding wrongly while the other binds nothing.

### The cap as specified would have leaked

Row 4 says to count `_live_subagents` before `classify`. That is the count the burst
defeats: N Agent hooks can be admitted before any `SubagentStart`, so all N see zero
live. The cap counts admitted-but-unstarted spawns as well, which is the same ledger
1.5 introduced — the two phases share one structure.

Calls still *parked* in the gate are deliberately not counted. Arming on hook entry
is the hazard `_expect_spawn`'s docstring forbids, so admission is at approval; in
that window the operator answering each row is the bound, which is what phase 3's
ordinal exists to make visible.

Nested spawns (`Agent` called from inside a sub-agent) are not counted, and today's
behaviour is pinned by a test so the hole is visible rather than inferred. Widening
`spawn` to cover them would corrupt the join to fix the count: a nested
`SubagentStart` is not attributable to the parent's ledger entry. It wants a
separate counter and a prior decision about whether workers should fan out at all,
given `_roles` addresses only the root's children.

### Unsettled #4 is settled, and it strengthened phase 2

The CLI binary is not missing — it is at
`claude_agent_sdk/_bundled/claude`. Its strings contain:

> When you launch multiple agents for independent work, send them in a single
> message with multiple tool uses so they run concurrently.

pushed with the agent listing, gated on `isInitial && showConcurrencyNote`
(`Al()!=="pro" && xV()==="default"`). So `templates.py:130` was **overriding a
permission the model already had**, which is the reading this note predicted would
change phase 2's character. It also means the concurrent-spawn path was reachable
before phase 2 — 1.5 fixes a live defect, not a self-inflicted one.

### Also found, adjacent

`_subagents` was answering two questions that disagree. `_subagent_start` read it as
"have I seen this id" for resume detection, with a comment claiming membership is
"written on this path alone", while `_subagent_stop` discarded. Measured:
`start → stop → start` emitted a second `AgentSpawned` where the wake path requires
`AgentResumed`, rebuilding the record — zeroed usage, reset `started_at`, swapped
Transcript (I7). All three existing resume tests passed because none fired a stop
between the two starts.

### Still unsettled

1, 2, 3 and 6 stand. #5 was not reached — phase 6 is not built. Item 2 (whether two
sub-agents of one `subagent_type` both start) is still the premise phase 1 rests on
and is still uncorroborated by a live run; everything above is synthetic-hook and
unit evidence. A captured team run with the counts recorded remains the thing that
would settle it, and this repo's own lead is the natural fixture.

`ui/board.py` derives the address rather than reading it off the record, so nothing
new is stored — but the two implementations agree only while sub-agent records are
append-only. If anything ever emits `AgentRemoved`, board ordinals shift and the
driver's do not, and the address has to move onto `AgentRecord`. Pinned by a test
that drives a real session and a real store over one sequence; the board derivation
can be broken with every `driver.py` test still passing.
