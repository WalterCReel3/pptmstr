# A message has no sender until the gate gives it one

**Dated:** 2026-08-12 · **Status:** built 2026-08-12, `eccaa24` ·
**Follows:** §8 step 8, `2026-08-11-agent-teams-vs-pptmstr.md` ·
**Amends:** §2.7, which assumed the bus could identify its own callers

§2.7 names an in-process MCP server as the default transport for inter-agent
concerns — `post_concern(to, subject, body)`, `read_inbox()`, `claim_task()`,
backed by the store — because a concern that is a store object is snapshot,
rendered, and reviewable in flight. The reasoning survives. The mechanism as
written does not, and the gap is not a detail.

## The finding

An SDK MCP tool handler cannot tell who called it.

The CLI delivers an SDK MCP call as a `mcp_message` control request carrying
`server_name` and a raw JSON-RPC body (`_internal/query.py:499`); the body carries
`name` and `arguments` and nothing else (`:679`). The `can_use_tool` branch on the
same switch (`:429`) is handed `tool_use_id` *and* `agent_id`, which is what makes
the omission legible as a design of the protocol rather than an oversight.

So `post_concern` has no trustworthy `from`, and `read_inbox()` cannot know whose
inbox to read. A sender passed as an ordinary argument is model-supplied, which
means a worker can post as the lead by writing the lead's name — not as an attack,
but as the ordinary consequence of a model filling a field it was asked to fill.

`PreToolUse` does know. It sees the MCP call with `agent_id` attached, and it can
rewrite arguments via `updatedInput` (§5.3). Whether that rewrite survives the hop
out to the CLI and back into an in-process server is a different claim from
"`updatedInput` works for built-in tools", and only the first is provable by
reading. `scripts/verify_message_bus.py` settles it against CLI 2.1.226:

| question | result |
|---|---|
| Do MCP calls reach `PreToolUse`? | yes, both calls |
| Is `agent_id` set on them? | `null` for root, `a4ad0fc6d9b0cdc37` for the sub-agent — absent vs. present, exactly `NodeId`'s shape |
| Does `updatedInput` reach the MCP handler? | **yes, 2/2** — handler received `{"note": "from-sub", "_stamped_by": "a4ad0fc6d9b0cdc37"}` |
| What does the handler see unaided? | `['note']` — no `session_id`, no `agent_id`, no `tool_use_id` |
| Is `SendMessage` advertised without agent teams? | yes, in the root's 31-tool init list |

**The consequence is stronger than "it works".** The gate is not a policy layer
sitting on top of the bus; it is the bus's authentication layer. A concern has a
sender only because `PreToolUse` stamped one. Interception is therefore mandatory
and not a choice — which splits §9's open question in two, because *the hook
running* and *the operator being asked* were being treated as one decision and are
not. The first is structural. Only the second is policy.

The design consequence: `post_concern`'s wire signature takes no sender, the gate
injects one, and the handler treats an unstamped call as a bug rather than
defaulting it. A message that arrives without a stamp did not come through the
gate, and the only correct response to that is to refuse it loudly.

## What the store gains

Two cross-agent projections beside `needs_you` — which is what §2.7 calls
"`review_queue`", renamed since (`store.py:378`; the doc's `store.py:323` citation
is stale and now points at the state-clock stamp).

```python
ConcernId = str
TaskId = str

@dataclass(frozen=True, slots=True)
class Concern:
    id: ConcernId
    sender: NodeId            # stamped by the gate, never by the model
    recipient: NodeId
    subject: str
    body: str
    posted_at: float
    state: ConcernState       # POSTED -> DELIVERED | WITHDRAWN
    edited: bool              # body differs from what was posted

@dataclass(frozen=True, slots=True)
class Task:
    id: TaskId
    title: str
    detail: str
    depends_on: tuple[TaskId, ...]
    claimed_by: NodeId | None
    state: TaskState          # PENDING -> CLAIMED -> COMPLETED, all reachable
    declared_at: float
```

`Snapshot` gains `concerns: Mapping[ConcernId, Concern]` and
`tasks: Mapping[TaskId, Task]`. Both are top-level rather than per-node: a concern
has two nodes and a task has zero until claimed, so hanging either off
`AgentRecord` would mean storing it twice and keeping the copies honest.

**Claimability is derived, never stored.** A task is claimable iff it is `PENDING`
and every entry in `depends_on` is `COMPLETED`. "Automatic unblocking on
completion" then costs nothing — there is no unblocking step, because there was no
`BLOCKED` state to leave. A stored blocked-flag would be a second fact about the
same thing, which is the shape of every sync defect this codebase has already
fixed once (`needs_you` exists because "waiting on you" had two implementations).
A dependency cycle makes its members permanently unclaimable, so cycles are
rejected at declare time rather than discovered as a wedged team.

## Claiming, and the care it needs

Decided: claiming happens in-app on the serialized path. No file locks — those
solve a cross-*process* race that our single-writer store does not have. The
intent queue already orders every mutation, so two workers calling `claim_task`
concurrently are two intents, applied one after the other, and the second sees the
first's result.

The care is not in the ordering. It is that **`claim_task` and `read_inbox` return
a value**, and today `Bridge` has no crossing that does. It has `emit`
(asyncio→UI, one-way) and the approval future (parked by the gate, resolved by an
operator). A claim needs the shape of the second with the timing of the first:
answered by the UI thread as a matter of course, in the same frame, with no human
in it.

### The reducer answers, rather than the snapshot being searched

The first draft of this had `_apply` stay `(Snapshot, Intent, float) -> Snapshot`
and put the winning request id on the record — `Task.claim_id`, and a matching
`Concern.delivery_id` — so the UI thread could find its own answer by scanning the
new snapshot. Two defects follow from that, and both had already been written down
as comments defending against them, which is the tell:

- `TaskReleased` has to null `claim_id`, or a later claim reusing that id is
  answered by a stale record.
- The app loop has to answer *only* requests whose intents were in this batch.
  `ask()` registers before `emit()`, so a future can exist a frame before its
  intent arrives, and "answer everything outstanding" replies "nothing claimable"
  to a claim the store has not seen yet.

Widening the reducer dissolves both. `_apply` returns
`tuple[Snapshot, tuple[Effect, ...]]`, and `effects.py` holds a union of
`ClaimSettled` and `InboxDelivered` — the same tagged-union-plus-`match` shape as
`Intent`, in the other direction. An effect exists *because* an intent was applied,
so the batch-ordering hazard becomes unstateable rather than merely handled, and no
correlation token enters the domain model. It cost ten `return snap` sites in one
function: `apply`/`apply_all` have a single production call site (`app.py:101`) and
every test calls them for effect, so widening the return churned nothing.

Four things still have to be true, and each has a test that fails without it:

1. **Register before emitting.** The future goes in the table, *then* the intent is
   queued — the ordering `driver.py:600` already uses for approvals.
2. **Every request is answered in the frame that applies it.** `app.py` settles
   `apply_all`'s effects immediately after applying them. Nothing survives a frame.
3. **An empty answer is still an answer.** A claim against an empty or fully
   blocked board emits `ClaimSettled(task=None)` rather than no effect. Emitting
   nothing would park the asking agent forever over an ordinary condition — the
   failure the whole channel exists to make impossible.
4. **Shutdown answers them all.** `abandon_all_requests` is `fail_all_pending`'s
   counterpart, and `ask()` takes the fallback effect from the caller because only
   the caller knows which shape it awaits — a claimer released with an
   `InboxDelivered` would crash on the way out instead of winding down.

The bus crossing is kept separate from the approval table rather than merged into
it. An approval parks on a *person* and may wait hours (I8); a bus request parks on
the *frame*. Merging them would put bus traffic into `parked_count`, which is what
the lost-approval watchdog reads, and it would cry wolf on every claim.

## Settled: the operator sits on the send

Not the read. Two arguments, and the second was decisive:

**Rejection needs a channel back, and only the send has one.** Reject at read time
and the recipient is the parked party — telling it that a message it never saw was
refused is useless, while the sender, the only agent that could revise, has already
moved on. Gate the send and `permissionDecisionReason` reaches the sender in-band.

**Parking the sender costs nothing (I8), and the sender is the right one to park.**
The argument that a send-side gate stalls an agent "mid-thought over a message it
has finished thinking about" runs backwards through the parking invariant. The
sender is precisely the agent whose context holds why the message exists.

The consequence is a large deletion rather than an addition: **send-side gating
needs no new mechanism at all.** A parked `post_concern` *is* an `ApprovalNeeded` —
`PreToolUse` already fires on it (that is what stamps the sender), `PendingApproval`
already models it, `render_diff` returns None as it does for `Bash`, and
edit-then-approve already rewrites the body through `updatedInput`. So
`ObligationKind` does **not** gain a fourth member, there is no concern review pane,
and concerns join the review queue that already batches by wait time. The work is a
`classify()` disposition and a `summarize()` case. What the store records are for is
the *conversation* — who said what, what is outstanding, what has been read — which
is delivery-side and still ours.

Two follow-ons: `ConcernEdited` is redundant, since editing is the gate's job and a
second edit path would let the record disagree with the tool call that was approved
(the driver should set `Concern.edited` when `updatedInput` differed instead), and
`ConcernWithdrawn` shrinks to one real window — approved but not yet read.

## Settled: `SendMessage` is out of v1

Measured rather than reasoned (`scripts/verify_wake_path.py`, three runs). The wake
edge is real — a finished sub-agent resumes on a sibling's message — but two
findings gut the case for adopting it now:

- **It arrives as a second `SubagentStart` and corrupted the store.** Folded into
  §2.3 and fixed here as `AgentResumed`; see below. This was a live defect, not a
  step-8 one.
- **Addressing requires an agent ID a sibling cannot obtain.** The CLI refuses a
  `subagent_type` — *"use the agent ID from a background agent's spawn result"* —
  and that result lands in the lead's context. A delivery only succeeded once the
  gate rewrote the worker's spawn prompt to carry the real ID. So `SendMessage`
  does not let the model route without us; it routes only where we have already
  addressed for it, which is most of the work the bus was going to do anyway.

Still unresolved and deliberately not claimed: whether `updatedInput` edits a
`SendMessage` in flight. The successful send's recipient did not echo verbatim as
instructed, so the marker's absence is model non-compliance rather than evidence.
Verified for in-process MCP tools only.

## Fixed on the way past: the wake path corrupted a record

`driver._subagent_start` emitted `AgentSpawned` on every `SubagentStart`, and the
store's arm builds a fresh `AgentRecord`. Replayed against the real store at the
observed timings:

```
                           before wake    after wake      (after fix)
started_at                         1.0         16.59              1.0
ended_at                           9.6          None             None
state                             DONE      SPAWNING         THINKING
total_cost_usd                    0.03           0.0             0.03
transcript chars                    12             0               12
same transcript object               -         False             True
```

The transcript replacement is the serious one: readers hold
`(buffer, length_at_snapshot)` under I7, so a new buffer leaves every pane following
that node reading something nothing writes to. Fixed with a distinct `AgentResumed`
intent that moves only the liveness fields; the driver distinguishes resume from
spawn by membership of the sub-agent id set it already maintains.

## Deferred, with reasons

**Spawn ordering.** Now understood to be worse than the roster snapshot the
agent-teams doc describes. It is not ordering: it is that the *address itself* must
be plumbed through model-authored prompts by us. It binds only if `SendMessage`
comes into scope, and the bus routes on `NodeId`, so this stays deferred.

## Build order within step 8

Store side first, per §8: records, intents, the two projections, cycle rejection,
and the third crossing on `Bridge` — all unit-testable with no SDK and no UI, like
step 1. **Done:** `model.Concern`/`Task`, eight bus intents, `effects.py`,
`Snapshot.inbox_of`/`claimable_tasks`, `Bridge.ask`/`settle`/`abandon_all_requests`,
and the settle step in the frame loop. 33 new tests in `tests/test_bus.py` and
`tests/test_bridge.py`; 524 pass, mypy and ruff clean.

**Also done: the `@tool` surface.** `bus.py` builds one in-process MCP server per
session over `post_concern`, `read_inbox`, `claim_task`, `declare_task`,
`complete_task`, `release_task`; the gate stamps `_from` on every one of them
(after any operator edit, so edit-then-approve cannot reattribute a message); an
unstamped call raises rather than defaulting; and roles resolve driver-side —
`agent_type → agent_id`, first writer wins, with `lead`/`main`/`root` naming the
session — so the model addresses `"qa"` rather than an opaque id it has no way to
learn.

Verified live against the real CLI (`scripts/verify_bus_live.py`, the frame loop
with the UI removed and a stand-in operator):

```
task board    build  claimed  owner=lead  depends_on=-
              test   pending  owner=-     depends_on=build     ← correctly blocked
approvals     message lead: ordering                           ← ordinary queue row
concern       delivered, sender=(session-id, None), role: lead ← gate's stamp
read_inbox    delivered 1 concern through the effect channel
```

**Also done: work templates.** `templates.py` is SDK-free — a `Role` is a name, a
description, a prompt, an optional tool restriction; a `WorkTemplate` is roles plus
a lead prompt plus an advisory spawn order. The driver turns roles into
`AgentDefinition`s and the briefing into a `system_prompt` **append** over the
`claude_code` preset, rather than a bare string that would discard the tool
conventions and environment description the lead still needs.

Three built-ins: `solo` (no roles, and first in the list so teams stay opt-in),
`feature` (lead / builder / read-only reviewer), `research` (coordinator /
investigator / skeptic). The review roles are read-only on purpose — a reviewer
that can quietly fix what it was asked to find stops reporting it.

The briefing is **generated** from the roles rather than written out per template,
so the prose cannot name a teammate the SDK was never given. Role names are
lowercase because a role name is also its bus address.

**Addressing, revisited.** `resolve_role` maps `agent_type → agent_id`, first
writer wins, with `lead`/`main`/`root` naming the session. A role that exists in the
template but has not spawned yet gets a different refusal from an unknown name —
one is a timing problem the lead can fix by starting it, the other is a spelling
mistake. Collapsing them is what sent the wake-path probe's worker into retrying
the same wrong name.

That also shrinks the deferred spawn-order problem to almost nothing: the bus
resolves a role at the moment a concern is posted, so a worker only has to exist by
the time somebody writes to it — not, as with `SendMessage`'s roster, by the time
its correspondent started.

Then the pane. Note that "close §9 before the pane" is already satisfied: gating
the send made concerns ordinary approvals, so there is no concern review pane to
design — only a conversation view, which is a rendering question rather than an
interaction one.

## What running a real team changed

Two findings, both from `scripts/verify_bus_live.py --team` rather than from
reasoning about it.

**The first team run used the bus not at all.** The `research` template spawned both
roles, declared two tasks with a dependency edge, and had each worker claim and
complete the right one — and posted **zero concerns**. Not a defect in the bus: a
sub-agent's result already returns to the lead through the `Agent` tool, so the
model had no reason to reach for `post_concern`. A channel that duplicates a path
the model already has will not be used, and no amount of listing it in the briefing
changes that.

The fix is to say what the bus is *for* — the thing the result does not carry.
Workers must post a concern to `lead` before finishing, naming what they are least
sure about or noticed unasked; the lead must `read_inbox()` before writing its final
answer; the skeptic posts its verdict to the investigator as well as to the lead.
Both halves are pinned by tests, with the run that motivated them in the docstrings.

**The second run found a real defect, in the probe.** Asked to review this
repository, the team confirmed a mechanism and then refuted its own reachability
claim — which is the adversarial pattern working as intended. The finding that
survived:

> `verify_bus_live.py` builds `claimed` purely from task-board state and
> unconditionally prints "the claim round-tripped through the effect channel and the
> agent was told what it won" whenever any task is CLAIMED — it never checks whether
> `bridge.settle()` actually ran.

Correct, and it matters because of an asymmetry worth writing down: **the store
commits the domain change at apply time, while the answer travels separately.**
`InboxRead` marks a concern DELIVERED and `TaskClaimRequested` marks a task CLAIMED
unconditionally when the intent is applied. So a lost effect leaves the board
reading "claimed" while the agent that claimed it is eventually told, by the
teardown fallback, that there was nothing to claim. The two disagree and neither
side complains. The probe's own self-diagnostic — "check that the frame loop settles
effects in the same pass" — could never have caught it, because it read store state
that settle timing does not touch.

Two changes came out of that:

- The probe now counts what the frame loop actually settled and reads
  `bridge.asking_count` *before* `stop()`, since `abandon_all_requests` would
  otherwise erase the evidence. Its sender check was also wrong: it compared every
  sender to the *root* node, so a correctly stamped sub-agent read as a failure.
- **`_check_for_stranded_requests`** in the frame loop, the counterpart to
  `_check_for_lost_approvals`. A bus request has no surface by design — an agent
  awaiting `claim_task` is not an obligation and appears nowhere in `needs_you` — so
  a dropped effect looks like nothing at all. It should be outstanding for a
  fraction of one frame; five seconds means it was dropped.

The wiring of both watchdogs is now asserted at source level, because unwiring
either leaves all of their unit tests passing — verified by doing it. A watchdog
nothing calls is worse than none, since it reads as covered.

**A third run, and the cost signal.** The re-run with the corrected probe was
killed by a 900s ceiling before it printed a verdict. What survived its log shows
the team spawning both roles and posting a concern to the lead — so the prompt fix
holds — but the report, and the store the concerns lived in, went with the process.

Two things follow. **A three-agent team on one review question runs past fifteen
minutes**, so it is not a cheap default and the launcher is right to keep `solo`
first. And the surviving log line names a finding nobody has read:
`"verify_bus_live.py settle-ordering: UNSAFE on non-break exit paths"`. Tracing the
probe's exit paths by hand, every one of them — break, deadline, `running.done()`,
and the exception path — falls through to the `finally` that drains, applies and
settles once more, so the claim looks wrong. But it is unread rather than refuted,
and it is recorded here as unread. A probe that loses its findings when it is killed
is itself worth fixing before the next long run.

## Sources

- `scripts/verify_message_bus.py`, run 2026-08-12 against bundled CLI 2.1.226,
  `claude-agent-sdk` 0.2.134
- `claude_agent_sdk/_internal/query.py:429,499,679` — the control-request switch
