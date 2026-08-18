# A finished agent is revived by its own last message, and that does not explain the correlation

**Recorded 2026-08-16, rewritten 2026-08-17**, from three sub-agents of which two kept a
live `thinking` badge long after their agents had finished. One mechanism that produces
that symptom is now confirmed by replay and fixed. **The bug is not closed**, and the
single most striking thing about the incident is untouched by the fix.

The filename still carries the original title, *"An edited approval is owed a closing
hook nobody has measured"*, and the previous title named the veto. Both asserted a cause
this record no longer leads with. The name is left alone because it is the record's
identity, not its claim.

Line citations are against the working tree, which is dirty in `driver.py`, `store.py`,
`tests/test_store.py` and `scripts/verify_post_tool_use.py`.

---

## What was seen

Three sub-agents ran to completion and reported. Two kept a live `thinking` badge in
the rail long afterwards. The two were exactly the two whose `post_concern` the
operator **annotated** before delivery; the third agent's concern went through
unedited and its card settled normally.

Three for three. That is a correlation on n=3, and it is still the thing that wants
explaining. Everything below changes the explanation of the *symptom* and none of it
touches this.

## The mechanism that was confirmed

A sub-agent's final `AssistantMessage` is on stdout **before** the SubagentStop control
frame, by construction — it is the answer the stop reports. It carries no `ToolUseBlock`,
so `_assistant` (`driver.py:356`) translates it to `StateChanged(node, THINKING)` at
`driver.py:390-396`.

The two do not arrive in that order. Control frames are dispatched immediately by the
SDK's read task via `spawn_task`; ordinary messages queue in a memory object stream of
`max_buffer_size=100` (`claude_agent_sdk/_internal/query.py:140`). So the stop hook's
`AgentFinished(DONE)` (`driver.py:934`) is applied **first**, and the message that
preceded it lands **second** and overwrites it. `ended_at` reverts to `None`, the
elapsed clock restarts, `THINKING` is active so `any_active` never settles and the app
never idles.

This is a structural inversion, not a race. Nothing needs to be slow, and no closing
hook needs to go missing.

**What convicts it.** `driver.py:1491` documents this exact hazard for the **root** node,
in its own words — *"the store's arm guards on `rec.pending` and not on terminality"* —
and re-asserts the standing failure to work around it. No equivalent existed for a
sub-agent. `SubagentProgress` was already guarded on terminality (`store.py:263` in the
current tree); `StateChanged` was not. Terminality was considered for one of the two
sub-agent paths and not the other.

**The replay confirms it rather than infers it.** Against unmodified `store.py`,
`AgentFinished(CHILD, DONE, ended_at=7.0)` followed by `StateChanged(CHILD, THINKING)`
left the node active with `ended_at` reset to `None`. Seven tests red before the change,
green after.

## What was fixed, and what the fix says

`_FINAL_STATES = {DONE, CANCELLED}` (`store.py:91`), latched against message-stream
events across five arms: `StateChanged` (`:225`), `SubagentProgress` (`:263`),
`ApprovalRequested` (`:371`), `ApprovalResolved` (`:394`), with `AgentFinished` and
`AgentResumed` remaining the only intents that may move a record off a declared end.

The set is deliberately **not** `AgentState.is_terminal`. `is_terminal` asks whether
another transition is expected without the operator; this asks who is allowed to
contradict the end. FAILED is absent on purpose, which leaves
[2026-08-14-a-failed-session-can-still-be-talked-to] intact: a session that errored and
then answered is an ordinary session again, and that recovery arrives as a plain
`StateChanged` because nothing announces it.

**The argument that makes latching DONE safe rather than arbitrary is the non-obvious
part, and it is why this is worth recording at all.** A finished sub-agent genuinely can
come back — a sibling's `SendMessage` restarts it, and the CLI reports that as a second
`SubagentStart` under the original id. But `_subagent_start` translates a re-seen id to
`AgentResumed`, not `StateChanged` (`driver.py:871-879`). So the real revival path does
not run through the arm being closed. Latching DONE shuts the accidental door without
shutting the real one. That path was read, not assumed.

This is a **cause** fix for the DONE case and explicitly **not** for the class. The store
still cannot order two events by when they were *generated*, only by when they *arrived*,
and that weakness remains wherever a state is revivable — which now means FAILED.

**Ordering on evidence was considered and refuted concretely, not on cost.** The obvious
general repair is to stamp events and let the store discard a stale one. It cannot work
here: `AssistantMessage` carries no timestamp field at all
(`claude_agent_sdk/types.py:1029-1040`), and the inversion happens *inside* the SDK's
100-deep buffer before our code ever sees the message — so any stamp we applied would be
taken at dequeue time and read as current. It is recorded here so a future reader does
not reach for it a third time.

## What the fix does not explain

**This is the important section, and the reason this record is not a closure.**

The mechanism above is a per-sub-agent scheduling inversion. Nothing in it routes on
`_edited`, on payload, or on anything the operator did. No path was found by which
operator dwell widens the window: the dwell is spent in the hook task, not in the
consumer, and the buffer inversion is the same length either way.

So it produces the exact symptom and **not** the 3-for-3 correlation with the operator's
annotation. Two of three by chance on n=3 is possible and cannot be dismissed — but
"possible" is not an explanation, and the correlation has survived every revision of this
record without acquiring one.

The asymmetry is sharper than it first looks, and it is the reason confirming one
mechanism does not much reduce the standing of the other:

| | explains the stuck badge | explains the annotation correlation |
|---|---|---|
| store overwrite | **yes, verified by replay** | no, and no candidate route exists |
| call-in-flight veto | yes, unconfirmed | possibly — dwell is the one difference of a kind a delivery path could be sensitive to |

The veto story remains the only candidate that could account for both observations. It is
unrefuted, unconfirmed, and — see below — no longer answerable for this incident.

## The two mechanisms, and what would separate them

They differ in one consequence that is visible without any new instrumentation.

- **Store overwrite.** `_subagent_stop` *ran*. It emitted `AgentFinished(DONE)` at
  `driver.py:934` and discarded the agent from `_live_subagents` at `:935`. The parent
  was therefore never wedged and reached `AWAITING_INPUT` normally.
- **Call-in-flight veto.** `_subagent_stop` did *not* run. The agent stays in
  `_live_subagents`, no `AgentFinished` is ever emitted, the node simply sits at the
  state its last message left, and `_await_subagents` (`driver.py:1586`, awaited at
  `:1458`) holds the parent's turn until the veto expires at `SUBAGENT_CALL_VETO_S` —
  six hours.

So: **stuck sub-agent with a healthy parent** is the overwrite; **stuck sub-agent with a
wedged parent** is the veto.

**The fix is itself the discriminator for the next occurrence, and it costs nothing
because it is already deployed.** The latch makes the overwrite unable to produce the
symptom. If a sub-agent badge sticks again, the overwrite is excluded and the veto is
what is left. The converse is weak: the symptom never recurring is consistent with the
veto being real and rare.

## The retroactive discriminator is dead, permanently

The previous version of this record presented a retroactive discriminator as free and
applicable — ask what the parent did after that turn. **It is neither, and the reason is
structural rather than a matter of nobody having looked.**

`_await_subagents` is awaited *inside* the `ResultMessage` arm (`driver.py:1457-1458`).
The CLI has therefore already produced the turn's result before pptmstr can wedge at all.
The parent's result is on disk identically under both branches, so the artefact that
survived the incident cannot distinguish them.

Nothing else survived either. pptmstr persists nothing; `_poll_context`
(`driver.py:1739`) is a control request that leaves no transcript trace; the store is
in-memory, so the incident's records died with the process; and `Log` is a 2000-entry
in-memory ring mirrored to stdout by `print` (`log.py:37-40`).

**The one assumption this rested on has since been checked and holds.** The open question
was whether the CLI writes its result when it produces it or behind consumer
backpressure — if the latter, a wedged consumer might have left a distinguishable trace.
It does not. `claude_agent_sdk/_internal/transcript_mirror_batcher.py` states in its own
docstring that *"the local-disk transcript is already durable"* and that the batcher only
mirrors to an optional user-supplied `SessionStore`. pptmstr configures no such store
(`driver.py:1337`), so `_transcript_mirror_batcher` is `None` for us and the frames are
peeled off stdout and dropped before the buffered stream (`query.py:313-320`). Even when
a store *is* configured, the flush runs in the SDK's read task on the result frame
(`query.py:330-333`), not behind the consumer. The CLI's own transcript write is not
gated on us.

This question is now answered and closed. It cannot be answered for **this** incident by
anyone, ever.

## Half the by-eye procedure was never observable

The discriminator named two tells: the `AWAITING_INPUT` transition and a frozen context
reading. **Only the first works.**

`ContextSnapshot.polled_at` is written (`driver.py:1781`), declared (`model.py:168`), and
read by nothing in the UI — `model.py:153` promises a staleness surface that does not
exist. And an idle parent accrues no context either, so "frozen" and "idle" render as the
same unchanging number. The context reading distinguishes nothing.

The state label does work, and the procedure for the next occurrence is worth keeping:

- root card reads `YOUR TURN` with topic *waiting for you* → the parent is **idle**;
- root card reads `thinking` / `calling` / `running` with a topic naming a tool → the
  parent is **wedged**.

Two caveats, both real. A parked call on the root masks the label as `REVIEW`
(`store.py:205-227` holds a pending node in `AWAITING_APPROVAL`; `theme.py:233`). And
`_node_of` falls back to the root for any message whose sub-agent join is missing
(`driver.py:322-325`), which can paint an idle root as thinking.

## What the fix left open, and what checking it found

A **DONE row carrying a pending approval** was recorded here as a combination the latch
newly permits, with two consequences unchecked. Both were checked. One was a real defect
in this document's reasoning and one was a defect in the code's comments; neither was a
defect in the fix.

**The state does not occur.** `ApprovalRequested` is emitted at `driver.py:1242`, inside
`_park`, and the first suspension point on that path is the `await future` at `:1245` —
*after* the emit. It is a hook path, so the buffered-message inversion this whole record
is about cannot reorder it: that route needs a message-derived intent. `AgentFinished`
clears `pending`, the bridge is one queue applied in emit order, and every DONE route is
either a hook or a stream terminus that the gate emit precedes. So DONE-with-pending is
reachable in the store and not from the driver, which is a distinction the previous
version of this section did not draw.

One caveat belongs on the record rather than in a footnote: the teardown route holds
because the SDK cancels a queued hook task before its first step, which is true only
while no `session_store` is configured. Configure one and the state would occur. The
invariant is ours by construction on two routes and a vendored dependency's property on
the third.

**The rail claim in the previous version was wrong.** It said `_claim`
(`ui/rail.py:222`) ranks DONE at the bottom, so an approval would sit on a card ranked as
wanting no attention. `_claim` has exactly one caller — `rail.py:279`, inside
`_subs_signal` — and it selects the state shown beside a *collapsed group's sub-count
marker*. Card ordering, height, the amber REVIEW badge and the `needs_you` backlog do not
consult it. Rendered against a snapshot holding the combination, a collapsed session
whose DONE sub-agent holds an approval shows the badge, the taller "blocked" density
(`_density` tests `owed` before the DONE check at `rail.py:171-174`), and correct waiting
counts. The single difference is the marker text's colour. The claim was an inference
from reading the function without its callers, and it is withdrawn.

**`_needs_you`'s docstring was genuinely false and is corrected.** It asserted the three
obligation kinds are mutually exclusive *"by construction and not by luck"* on the
grounds that anything pending holds a node in `AWAITING_APPROVAL`. The latch made that
premise false. The conclusion still holds, now on stated grounds rather than the one that
stopped applying.

**One thing is left unresolved rather than settled.** The `_FINAL_STATES` guard in the
`ApprovalResolved` arm may be dead: that arm returns early when the pending id is already
gone, and `AgentFinished` clears `pending`, so an approval requested before the end and
resolved after finds nothing. If DONE-with-pending cannot occur, the latch moved dead code
from one arm to another rather than making a guard reachable — which contradicts the
reasoning recorded in the commit that made the change, and also the comment now standing
in that arm naming two live routes. Both guards are cheap and neither is wrong; the
recorded *reason* for one of them is what is in doubt. Nobody has separated it.

## What to do next, in order

1. **Log the settle loop.** One line on entry to and exit from `_await_subagents`, and
   one when the veto arm skips an agent (`driver.py:1678-1679` is a bare `continue`).
   This is promoted to first because it is now **the only thing that can ever answer the
   veto question**, the retroactive route being permanently closed. Today,
   "polled every tick and vetoed every time" and "never polled at all" produce
   byte-identical logs: of the seven `LOG.` sites in `driver.py` (`:1264`, `:1290`,
   `:1302`, `:1535`, `:1559`, `:1710`, `:1750`) only `:1710` concerns sub-agent lifetime,
   and it fires from `_settle_subagent` alone. That the incident was undiagnosable from a
   running system is the finding with the longest reach here.
2. **Settle whether the `ApprovalResolved` guard is dead**, and reword the commit that
   claimed the latch made it reachable if it is. Small, and it is the one loose thread the
   fix left behind.
3. **Move `_FINAL_STATES` to `model.py`** as `AgentState.is_final`, beside
   `_TERMINAL_STATES`. Two `AgentState` classifications in two modules answering two
   questions is the one-fact-in-several-places smell this codebase has already paid for:
   whoever adds the next terminal state edits `model.py` and has no reason to know
   `store.py` holds a second set. Note while doing it that `AgentState.CANCELLED` is never
   emitted anywhere in production — only the enum, the theme tables and the rail's
   special-cases — so `_FINAL_STATES` is effectively `{DONE}` today.
4. **Correct `driver.py:1604-1608`.** It states the SDK "reads the transport in a single
   task that dispatches control frames to the hook callbacks", and concludes there is no
   state in which the read has ended and a SubagentStop can still arrive. The installed
   SDK spawns each hook callback as an independent task rather than awaiting it. The
   conclusion may hold for another reason — the stream's end sentinel is an ordinary item,
   so control frames queue ahead of it — but the stated reason is wrong.
5. **Match the shape that actually stuck.** Park an `mcp__pptmstr__post_concern` call
   from inside a sub-agent, hold it at the gate for a realistic annotation interval,
   approve it with edits, and record whether a closing hook arrives and under what
   `agent_id` and `tool_use_id`. Undeclared added keys and operator dwell must be varied
   separately or the run cannot say which one mattered.

The probe drops to last because it is the most expensive and the least certain to
answer anything, not because the veto question stopped mattering.

Two of this record's own claims have now been withdrawn after being checked — the
gate branch in the first version, the rail ranking in the second. Both were inferences
from reading a function without reading what called it or what else produced the same
shape. Whoever works the list above should assume the same defect is still in here
somewhere and check before building on any single citation.

## Why the edit was never the discriminator at the gate

This survives from the previous version unchanged and still holds.

`_gate_tool_use` ends **every** approved call with
`_allow_with(self._stamp_bus_call(...) or decision.edited_args)`, and `_stamp_bus_call`
returns a rewritten mapping for any tool in `BUS_TOOLS`, edited or not — it has to,
because the authenticated sender can only reach an in-process MCP handler through
`updatedInput`. So all three concerns were the same shape at the gate: an `mcp__`
in-process tool, allowed with `updatedInput`, carrying `_from` and `_edited` keys the bus
schema does not declare. **If that shape were the cause, all three would have stuck.**

What differed was `_edited` being `True`, the operator's text replacing the declared
`to`/`subject`/`body`, and **how long the call sat parked**. The first two are payload,
and nothing in a hook delivery path routes on payload. Only the third is of a kind a
delivery mechanism could plausibly be sensitive to — and it is untested, with no
identified mechanism by which dwell would lose a `PostToolUse`. `PostToolUse` and
`PostToolUseFailure` are registered with no timeout, so no bound on our side is a
candidate.

The six hours are not measured from the operator's approval: `_pre_tool_use` stamps
`_subagent_in_flight` at `driver.py:996`, on the way *in*, before the gate blocks. The
operator's dwell is spent *inside* the veto window rather than added to it.

The one real measurement remains narrow. `scripts/verify_post_tool_use.py` runs an
allow-plus-`updatedInput` case and the closing hook arrives with the right `agent_id` and
`tool_use_id` — but on one sub-agent, on a `Bash` call, never parked, replacing a key the
schema already declares. The call that stuck matches none of those, and allow +
`updatedInput` is what the gate returns for every approved bus call including the
unedited one that settled fine. A probe keyed on that shape passes on both arms of the
incident and distinguishes nothing.

## Cost while the veto story stands

Unchanged, and it falls mostly on the parent rather than on the stuck cards:

| held | for | source |
|---|---|---|
| two entries in `_live_subagents` | up to `SUBAGENT_CALL_VETO_S`, six hours | `driver.py:1678` |
| the parent's turn, wedged before `AWAITING_INPUT` | the same six hours | `driver.py:1458` |
| the parent's context reading, frozen | the same six hours | `driver.py:1459` |
| whatever the pending-spawn ledger admitted that turn, against the cap | the same six hours | `driver.py:1154`, `:1481` |

All of it is process state, so a restart clears it — which is also why the evidence did
not survive.

## Verification boundary

**Confirmed by replay against unmodified `store.py`:** the overwrite. `AgentFinished(DONE)`
then `StateChanged(THINKING)` leaves the node active with `ended_at` reset. Seven tests
red before, green after; 932 tests pass and mypy is clean on the package after.

**Read against the working tree and confirmed:** the `StateChanged` translation of a
tool-free `AssistantMessage`; the SDK's 100-message buffer and the immediate dispatch of
control frames; `AssistantMessage` having no timestamp field; the root-node hazard
comment at `driver.py:1491` and the absence of a sub-agent equivalent; the `AgentResumed`
wake path at `driver.py:871-879`; the await's position inside the `ResultMessage` arm;
the two writers of `_live_subagents`; the seven `LOG.` sites and the bare `continue` on
the veto arm; `polled_at` having no reader; the `REVIEW` masking and the `_node_of`
fallback; the transcript mirror batcher's flush points and pptmstr's lack of a
`SessionStore`.

**Not run:** the app itself. Every claim about rendering — the DONE-plus-approval card,
the rail's ranking of it — is read from source, not observed.

**Not established, and not asserted above:** that the store overwrite was the mechanism
behind *this* incident rather than a second defect found while looking for it. It
produces the symptom and it was live on the day; that is as far as the evidence goes,
and it does not reach the annotation correlation.

**Unanswerable:** which mechanism the incident was. Nothing recorded whether the parent
returned to `AWAITING_INPUT`, and nothing that could have recorded it survives.

## Related

The veto exists because of
[2026-08-14-a-halt-has-to-reach-work-that-has-not-started-yet] and the liveness work
after it. Nothing here argues the veto is wrong: a call genuinely in flight must not be
settled on a clock. What is on the table is its blast radius — a veto held wrongly does
not cost one card, it costs the parent's turn.

[2026-08-14-a-failed-session-can-still-be-talked-to] is why FAILED is outside
`_FINAL_STATES`, and that decision was read before the latch was scoped rather than
rediscovered after.
