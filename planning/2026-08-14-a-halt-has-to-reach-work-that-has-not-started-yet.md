# A halt has to reach work that has not started yet

**Dated:** 2026-08-14 · **Status:** open, not started ·
**Extends:** §9 "Cancellation semantics" of [`orchestrator-design.md`](../orchestrator-design.md)

§9 records three levers — interrupt, disconnect, kill — and a fourth axis, whether
the stopped thing is still reachable by a sibling (`orchestrator-design.md:862-868`).
All four answer *"stop this node"*. The operator watching a team go the wrong way is
asking a different question: **stop everything, including the parts that have not
begun.** That is not a fourth verb on the same scale. Every one of §9's levers takes
a `node_id`; the thing being asked for takes none, and its hardest layer is the one
where there is no node yet.

The scale still holds and halt belongs on it, between interrupt and shutdown:

| lever | scope | recoverable | reaches work not yet created |
|---|---|---|---|
| interrupt (`pool.py:118-127`) | one node's current turn | yes | no |
| **halt** | the fleet | **yes — that is the point** | **must** |
| disconnect (`pool.py:102-116`) | one node's session | no | no |
| shutdown (`pool.py:129-137`) | the fleet | no | vacuously — nothing survives |

Today the only fan-out is `shutdown`, which clears the queue, cancels every task and
leaves the UI with no world. It is the right shape and the wrong terminal state.

---

## The five layers, and what exists

| | layer | what stops it today | verdict |
|---|---|---|---|
| 1 | roots running (`pool._running`) | `interrupt` / `close`, one node at a time | **exists, per node.** A fan-out over them is unsound — §"Fan-out" below |
| 2 | sessions queued (`pool._waiting`) | **nothing.** `close()` does not reach `_waiting` at all | **does not exist**, and the nearest thing is a measured defect |
| 3 | sub-agents inside a session | `client.interrupt()`, unscoped; **the gate, which does reach them** | **partial.** No per-sub-agent handle is held; the SDK's one handle is on the wire and discarded |
| 4 | a tool call parked at the gate | nothing needed | **exists, by doing nothing** (I8) |
| 5 | board tasks, claimed or not | nothing | **does not need to exist**, if halt refuses the right tool |

Three of the five rows are surprising. They are argued below in that order.

---

## 2. The queue does not race with a halt — it already loses to `close()`

The obvious hazard is a race: cancel a running session, `_run`'s `finally` frees the
slot and calls `_drain()` (`pool.py:78-89`), and a queued session starts *behind* a
fan-out that has already passed. That race is real. It is also not the worst thing
in this layer.

`close()` pops from `_running` and from `sessions`, and never touches `_waiting`
(`pool.py:112-116`). A queued session is announced into the store at submit time
(`pool.py:67`, `driver.py:926-948`), so it has a card, and the card's close action is
wired (`app.py:488`). Closing it therefore does nothing observable — and then it
starts anyway.

Measured against the working tree, not reasoned about:

```python
pool = SessionPool(bridge=None, cap=1)
pool.submit(a); pool.submit(b)          # b queues
await pool.close(b.node_id)             # operator closes the QUEUED session
await pool.close(a.node_id)             # the running one ends, freeing the slot
```

```
B started after being closed : True
B in pool._running            : True
pool.session_for(B)           : None
interrupt(B) reached it       : False
```

So a closed queued session starts, consumes a slot against the cap, spends money —
and because `close()` removed it from `sessions`, `session_for` returns `None`
(`pool.py:48-55`), which makes `interrupt` and `send` silently no-op on it forever
(`pool.py:96-100`, `:118-127`). Only `close` and `shutdown`, which read `_running`,
can still reach it. It is a session the operator has already dismissed, running
unreachable.

This is a live defect independent of halt, and it is the reason the fan-out design
is not merely inelegant: **a halt assembled out of `close()` would leak in exactly
this shape, once per queued session, and the leak is invisible.**

## 3. Sub-agents: no handle is held, and one is being thrown away

Fresh read of the installed SDK (bundled CLI 2.1.226, `_cli_version.py:3`).

**`interrupt()` is unscoped.** `ClaudeSDKClient.interrupt` (`client.py:317-321`)
forwards to one control request with no agent, task or scope field
(`_internal/query.py:776-778`) — structurally identical to `get_context_usage`
(`:772-774`), which the card note already established is session-scoped. There is one
client per session (`driver.py:967`) and one CLI process per client. **A sub-agent has
no client to interrupt**, exactly as it has none to poll.

**`stop_task(task_id)` is the SDK's only per-sub-agent handle**, and we discard the
id. It is a control request like the others (`client.py:454-475`,
`_internal/query.py:841-852`). The id arrives on `task_started` / `task_progress` /
`task_notification`, all of which carry `task_id` *and* `tool_use_id`
(`types.py:1083-1140`); §2.5.1 already records that pairing
(`orchestrator-design.md:338`), and `verify_wake_path.py` observed a real sub-agent's
`task_notification: completed` under a stable `task_id`
(`orchestrator-design.md:173-179`). The SDK's own lifecycle ledger tracks precisely
these: `DEFERRING_TASK_TYPES = {"local_agent", "local_workflow"}`
(`_internal/query.py:56`, used at `:900-902`) — delegated agent work, not background
shells.

`driver._system` receives that frame and reads two fields out of it —
`tool_use_id` and `description` (`driver.py:415-417`). `msg.data` is the raw payload
(`_internal/message_parser.py:213-222` passes `data=data`), so `data["task_id"]` is
sitting there unread. Storing it beside `_spawn_tool_use` (`driver.py:529`) is one
dict write. **That is the "one line away" row in the table**, and it is the only way
this codebase could ever name a single sub-agent to stop.

**But the gate already reaches every sub-agent, with no handle at all.** `PreToolUse`
fires *inside* a sub-agent and reports `agent_id` (`driver.py:694-697`, §2.5.1 at
`orchestrator-design.md:336`) — that is the whole reason a sub-agent's write can be
attributed correctly. A refusal at `driver.py:703` therefore applies to roots and
sub-agents identically, without knowing that sub-agents exist. Compare the alternative:
`stop_task` needs the id joined, the join needs the frame, the frame needs the sub-agent
to have started, and a sub-agent that has *not yet* started has no id to stop.

That asymmetry is the argument of this note in miniature. **A handle can only stop
what already has one.**

## 4. A parked approval is already halted; the question is what the gate says next

`_park` awaits a future the Bridge holds (`driver.py:801-805`, `bridge.py:193-202`).
Nothing polls, nothing spins, the node is `AWAITING_APPROVAL` and deliberately not
active (`model.py:62-72`). Parking is unbounded and free — I8, and the reason this
layer needs no mechanism: **a halt that lands on a parked call should do nothing to
it.**

The three things that could be done to it are all worse:

- `bridge.fail_all_pending` (`bridge.py:290-300`) rejects. A rejection is a *decision*
  fed back to the model as `permissionDecisionReason` (`driver.py:846-848`), and a
  model told "rejected" reasons about what to try instead. Halt would produce the one
  outcome it exists to prevent: more model output in the wrong direction.
- Cancelling the session's task does reach the parked coroutine — verified by reading:
  `run()`'s `async with` unwinds through `disconnect()` → `Query.close()`
  (`client.py:612-631`), whose `_close_impl` cancels `_child_tasks`
  (`_internal/query.py:998-999`), and hook callbacks *are* child tasks
  (`:255-271`). `_park`'s `CancelledError` arm then resolves the row as rejected
  (`driver.py:806-812`). Correct for a close; wrong for a halt.
- `permissionDecision: "defer"` is real and tempting: the run stops and the deferred
  call rides back on the result (`types.py:416-423`, `:1189-1200`, `:1245`). **There is
  no resume API for it.** Nothing in the SDK takes a `DeferredToolUse` back; resuming
  would mean prompting the model to reissue the call, which is a new turn. Defer stops
  cleanly and loses the parked call as an executable thing.

One bound worth stating because it is not visible from the gate: the hook timeout is
six hours (`driver.py:74-78`, `:917`). A halt held longer than that degrades into a
rejection through the cancellation path above. That is acceptable and it should be
written down rather than discovered.

What the gate must change is only its answer to *new* calls, and it should not answer
them all the same way:

| tool | on halt | why |
|---|---|---|
| `Task` / `Agent` | refuse | spawning is already gated (`approval.py:62-63`); a halt that lets a new sub-agent start is not a halt |
| writes, `Bash`, network | refuse | the effects halt exists to stop |
| `mcp__pptmstr__claim_task` | **refuse** | taking *new* work off the board is the layer-5 hazard, and this is where it lives |
| `mcp__pptmstr__complete_task`, `release_task` | **allow** | records work already done; refusing them is how a claim gets stranded |
| `post_concern` | refuse | it changes what another agent does next (`approval.py:64-73`) |

## 5. The board needs no halt of its own

The requirement — a halted agent must not find its claim stolen or its task silently
dropped — is satisfied by *not acting*, provided the gate refuses `claim_task`.

Tasks are frozen records in one global map (`model.py:488-514`), and every transition
is guarded on the claimer: `TaskCompleted` and `TaskReleased` both check
`state is CLAIMED and claimed_by == intent.node_id` (`store.py:423-437`). Nothing in
the reducer reclaims, expires or times out a claim; there is no reaper. A CLAIMED task
belonging to a halted agent stays CLAIMED for as long as the halt lasts, and
`_pick_claim` will not hand it to anyone else because it is not PENDING
(`store.py:479-495`, `model.py:516-526`). Declared-but-unclaimed tasks stay claimable
and, with the gate refusing `claim_task`, unclaimed.

`TaskState` has three members and deliberately no fourth for "held by something that
is not running" (`model.py:467-486`) — that is derived, because the claimer's state is
in the same snapshot. A `HALTED` task state would be the stored duplicate that comment
exists to forbid.

**One consequence to accept rather than fix:** while halted, the board reads as
work-in-progress that nobody is doing. That is true, and the fix is the banner, not a
state.

---

## Fan-out or latch

**Latch.** The fan-out loses on three independent counts, any one of which is
sufficient.

**It cannot cover layer 2 or layer 5 at all.** Both are about work that does not exist
yet — a queued session with no client, a task nobody has claimed. There is nothing to
send a cancel *to*. A fan-out is a loop over handles; these layers are defined by the
absence of one.

**It is not idempotent against its own side effects.** `close()` frees a slot and
calls `_drain()` (`pool.py:116`); `_run`'s `finally` does the same (`pool.py:82-85`).
A loop over `_running` that cancels as it goes is racing a function whose job is to
start more work, and the loop cannot win because `_drain` runs on the same thread
between the loop's awaits.

**It is slow in exactly the case it is needed.** Every control request is a round trip
bounded at 60 s, and a timeout raises (`_internal/query.py:546-591`, `:577`,
`:588-591`). `_session_action` submits and discards the future (`app.py:318-323`), so
a fan-out over a wedged session fails silently and nothing on screen says so.

The latch is one boolean, and the entry points that must read it are fewer than they
look:

| entry point | thread | covers |
|---|---|---|
| `pool._drain` (`pool.py:87`) | asyncio | layer 2 |
| `pool.submit` (`pool.py:59`) | asyncio | new launches (`app.py:301-315`) |
| `AgentSession._pre_tool_use`, before `classify` (`driver.py:703`) | asyncio | layers 3, 4, 5, spawn, and the whole bus |
| `AgentSession.send` (`driver.py:1026`) | asyncio | the operator's own compose box, which should refuse rather than queue text into a halted session |

**Spawn is not a fifth check.** `Task` and `Agent` are already in `_REVIEW`
(`approval.py:62-63`), so they arrive at the same gate as everything else. So does
every bus tool, including the auto-approved ones — `_pre_tool_use` runs before
`classify` decides anything (`driver.py:703-709`). One check at `driver.py:703` is the
single highest-leverage line in this design.

### What the latch costs

The stated cost — "every entry point has to remember to check it, and a missed check
is a silent leak" — is real but small at four sites, and it is testable in the way
STYLE.md §2 requires: unwire the check and assert the leak, the same treatment
`_check_for_stranded_requests` needed.

The cost that is *not* obvious, and is the one worth arguing about:

**The latch cannot live in the store.** The store is confined to the UI thread
(`store.py:4`). All four entry points above run on the asyncio thread. There is no
crossing that answers a synchronous question from asyncio — the third crossing
(`bridge.ask`, `bridge.py:230-246`) parks and waits for a frame, which would put an
intent round trip on every tool call and answer it from a snapshot at least one frame
stale. Worse, it would make the halt depend on the frame loop still running, and "the
UI is wedged" is a state in which the operator most wants the halt to hold.

So the latch belongs on **`Bridge`** — the one object every session already holds
(`driver.py:510`) and the one STYLE.md already exempts from the functional core
because *it is the thread boundary*. A `threading.Event` there is readable from both
sides without a lock.

**And it needs no copy in the store.** The status bar already reads live shell state
outside the snapshot — `bridge.parked_count` at `app.py:419` and `pool.running_count`
/ `queued_count` at `app.py:425-426`. A halt banner reading `bridge` directly is that
same precedent, and it removes the second fact before anyone has to keep it true.

**Halt must not become an `AgentState` member.** `theme.py` keys three dicts on it
(`:169-180`, `:191-202`, `:229-240`) and twelve call sites index them directly —
`widgets.py:102-104`, `rail.py:617`, `:634`, `:685`, `:707`, `health.py:76-78`,
`:114-118`, `inbox.py:490`, `:508`. A dict literal gets no exhaustiveness check from
mypy, so a new member is a `KeyError` inside the frame loop rather than a type error.
Independently of that: halt is one fleet-wide fact, so a per-node copy of it is the
derived-duplicate smell (STYLE.md §1), and one banner says it once.

---

## Reversibility, layer by layer

Halt is recoverable or it is shutdown with extra steps. What survives:

| layer | survives the halt | lost | on release |
|---|---|---|---|
| 1 roots | session, client, context, transcript | nothing, if halt does not interrupt | latch clears; the next tool call is gated normally |
| 2 queued | everything — never started | nothing | `_drain()` runs and starts them |
| 3 sub-agents | the record, transcript, usage | **in-flight sub-agent work** — see below | nothing resumes on its own |
| 4 parked approval | the row and the future, up to 6 h | the call, past 6 h | the operator answers it as usual |
| 5 board | claims, states, dependencies | nothing | `claim_task` is answered again |

**Layer 3 is where "recoverable" is weakest, and it should be said rather than
papered over.** A sub-agent whose tool calls are refused does not pause — it finishes
its turn and reports through `SubagentStop` (`driver.py:665-680`). On release it is
gone. There is no resume for a sub-agent except a sibling's `SendMessage`, which
arrives as a second `SubagentStart` and is why `AgentResumed` exists
(`intents.py:168-188`, measured at `orchestrator-design.md:173-192`). So halt returns
a *team* to its lead, not to its workers. For the stated use — the operator halts,
gives guidance, resumes — that is arguably right: guidance goes to the lead, and the
lead re-delegates. It is still a loss, and the note that claims otherwise would be
lying.

**Whether halt should also interrupt is a second press, not part of the first.** If
halt only latches, a root finishes its current turn as text — no tool runs, but tokens
burn. If halt also fans out `interrupt()`, the turn is cancelled and
`terminal_reason` becomes `aborted_streaming` (`types.py:1252-1261`), which
`driver._result` maps to `AgentState.CANCELLED` (`driver.py:386-390`) with a comment
saying the UI must not conflate "finished" with "you stopped it".

**It is conflated today, four lines later.** The same `ResultMessage` falls through to
`driver.py:987-999`, which emits `StateChanged(AWAITING_INPUT)` unconditionally. The
store's `StateChanged` arm is guarded on `pending` but not on terminality
(`store.py:182-203`), so it overwrites `CANCELLED` — while `ended_at`, set by
`AgentFinished` (`store.py:317-326`), stays. The record ends up `AWAITING_INPUT` with
an `ended_at`, which produces a `QuestionPending` reading *"ended its turn - reply or
close"* (`store.py:594-605`) and a stopped elapsed clock (`rail.py:661`,
`widgets.py:419-432`, `health.py:80`). Both intents drain in one frame, so `CANCELLED`
is very likely never rendered at all.

That is reachable today by pressing interrupt on any session, with no halt built. It
means **§9's recoverable lever currently has no honest surface**, and any halt that
fans out over it inherits the defect at fleet scale on the first press. Fixing it is a
prerequisite, not a nicety.

> **Amendment, 2026-08-14 — cost item 1 is satisfied, and `CANCELLED` is not the
> carrier.** `2026-08-11-turn-end-still-marks-done.md` has landed, and it fixed this
> at the *emission* site rather than by guarding the store: `_result` emits no state
> intent for an interrupted turn at all, and `run()` emits
> `StateChanged(AWAITING_INPUT, topic=INTERRUPTED_TOPIC)`. There is nothing left for
> the store's unguarded `StateChanged` arm to overwrite, so cost item 1 needs no
> further work — **do not build the terminal guard on top of this.**
>
> Two consequences for the rest of this doc. `AgentState.CANCELLED` is now emitted
> nowhere, so an interrupted turn is not terminal and the recoverable lever stays
> recoverable — which is what §9 wanted and what a terminal state defeated. And
> because no `AgentFinished` is emitted, `ended_at` is never set on an interrupt, so
> the stopped-elapsed-clock symptom described above goes with it. The interrupt is
> carried by the topic (`rail.py`) and by the obligation summary
> *"interrupted - reply or close"* (`store._needs_you`), not by a state.

### What the operator sees in between

Nothing is designed for this and the slot exists: the status bar already carries the
lost-approval line and the pool counters (`app.py:413-436`). A halt banner there,
loud, naming the two exits (release, or shut down) is the whole surface. The rail
needs no change — cards keep their real states, which is what makes "resume" mean
something.

---

## Verification boundary

**Executed:** the `pool.close()`-on-a-queued-session probe above, against the working
tree, three times (the first two runs shared one `asyncio.Future` between both stubs,
which made A's cancellation cancel B's await and produced a wrong reading for
`_running` — the corrected run gives each stub its own future). It settles that a
closed queued session starts, holds a slot, and is unreachable by `interrupt` and
`send`.

**Everything else is reading.** SDK claims are fresh reads of
`.venv/lib/python3.11/site-packages/claude_agent_sdk/`; pptmstr claims are reads of
the working tree, which is dirty in `store.py`, `model.py`, `driver.py` and `bus.py`,
so those citations describe the tree rather than HEAD.

**Four things cannot be settled without running, and are not asserted above:**

1. **Whether `client.interrupt()` cancels in-flight sub-agent tasks or only the
   parent's turn.** The SDK sends one unscoped control request and the behaviour is
   entirely in the CLI, which ships as a 297 MB compiled binary at
   `_bundled/claude`, not as source. `_track_task_lifecycle`'s own docstring says
   agent tasks routinely outlive the turn that spawned them
   (`_internal/query.py:865-872`), which makes "the turn aborts, therefore its
   sub-agents abort" a guess rather than an inference.
2. **Whether `interrupt()` even completes while the session is parked at the gate.**
   It is a 60 s-bounded round trip (`_internal/query.py:577`) sent to a CLI that is
   simultaneously awaiting our `hook_callback` response for the parked call. The SDK
   handles hooks and control requests in separate detached tasks (`:255-271`);
   nothing establishes that the CLI does. If it does not, the fleet-wide interrupt
   deadlocks for a minute per parked session, which is the common case.
3. **Whether `permissionDecision: "defer"` returned from inside a sub-agent stops
   that sub-agent's run or the whole session's.** `types.py:1193-1195` says "the run
   stops" without saying whose.
4. **§9's claim that a `stop_task`ed agent is immune to `SendMessage`**
   (`orchestrator-design.md:169-171`) is recorded as fact and is **not measured
   here** — `scripts/verify_wake_path.py` never calls `stop_task`. It measured the
   wake, not the exemption. Any halt design that reached for `stop_task` would be
   resting its "stopped and deaf" guarantee on an untested sentence.

## Ordered cost

| | work | why here |
|---|---|---|
| 0 | `close()` must remove from `_waiting` (`pool.py:112-116`) | Live defect, measured. Any fan-out or latch built on top of it inherits an unreachable running session |
| 1 | ~~Stop `StateChanged(AWAITING_INPUT)` overwriting a terminal state~~ **Done 2026-08-14**, at the emission site — see the amendment above. No terminal guard in the store, and `CANCELLED` is no longer the carrier | §9's recoverable lever has no honest surface until this lands |
| 2 | `Bridge` latch + banner (`app.py:421-436`) | The lever itself; no store change |
| 3 | Four checks: `_drain`, `submit`, `_pre_tool_use` before `classify`, `send` | Mutation-test each by unwiring it |
| 4 | Gate's per-tool halt policy (refuse `claim_task`, allow `complete_task`/`release_task`) | The whole of layer 5 |
| 5 | Keep `task_id` from `task_started` (`driver.py:415`) | One dict write; the only per-sub-agent handle that exists. Not needed by this design — needed by any design that wants to stop *one* sub-agent |
| — | A `HALTED` `AgentState`, a `HALTED` `TaskState`, a snapshot copy of the latch | All three are derived duplicates. Do not add them |
