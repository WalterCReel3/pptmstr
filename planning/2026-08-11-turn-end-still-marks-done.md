# A turn ending still marks the node DONE

**Dated:** 2026-08-11 · **Status:** built 2026-08-14 (see *What was built* below) ·
**Found by:** operator report ("sessions marked done while still working"),
confirmed by code review · **Regresses:** the DONE/AWAITING_INPUT split decided in
`2026-08-10-conversational-sessions.md` — that doc narrowed `DONE` to "the operator
closed the session"; `Translator._result` was never updated to match and still
implements the old one-shot meaning.

Line numbers below are against the working tree of 2026-08-14. They have drifted
30–300 lines since this was written, and a stale number reads as verified when it
is not — recheck by symbol name if they no longer land.

## What was observed

A session's row intermittently shows as finished — dimmed "ended" styling, reply
box disabled with "session done — it can no longer be messaged" — while the agent
is demonstrably still working: sub-agents still reporting progress, or the app
about to hand the operator back the reply box for a follow-up. It self-corrects a
moment later. The window is short but real, and it lands exactly when an operator
is most likely to act — right after an agent stops talking, which is also the
moment an operator reaches for the reply box.

## Why it happens

`AgentState.DONE`'s contract, stated on the enum itself (`model.AgentState`): **"DONE
means the operator closed the session, not that a turn ended."** Two places in
`pptmstr/driver.py` don't honor it.

### 1. `Translator._result` fires DONE on every ordinary turn end

`Translator._result`, as it stood before the fix:

```python
def _result(self, msg: ResultMessage) -> list[Intent]:
    ...
    cancelled = msg.terminal_reason in ("aborted_streaming", "aborted_tools")
    state = AgentState.CANCELLED if cancelled else AgentState.DONE
    out.append(AgentFinished(self.node_id, state, time.monotonic()))
    return out
```

Every `ResultMessage` is a turn boundary, not a session boundary — `run()`'s own
docstring says so (`AgentSession.run`). This method predates the conversational
rewrite (`d6f0c96`) and still carries the pre-rewrite assumption that a result
ends the session.

The corrective intent, `StateChanged(AWAITING_INPUT)`, is emitted later in
`run()`'s message loop — but only after two real `await` points:

```python
for intent in translator.handle(message):     # AgentFinished(DONE) queued here, immediately
    self.bridge.emit(intent)
if isinstance(message, ResultMessage):
    if self._subagents:
        await self._await_subagents(client, translator)   # up to SUBAGENT_GRACE_S = 120s
    await self._poll_context()                             # an RPC to the CLI subprocess
    self.bridge.emit(StateChanged(self.node_id, AgentState.AWAITING_INPUT, ...))
```

`Bridge`'s queue is a plain FIFO (`Bridge.to_ui`, written by `emit` and taken by
`drain`) — this is not a reordering race. It is a genuine gap: the UI thread drains
and renders on its own frame cadence (`app`'s frame loop), independently of
that coroutine's progress, so any frame landing between the two `emit()` calls
renders `DONE`. Every consumer of `AgentState.is_terminal` treats that at face
value:

- `ui/compose.py` disables the reply box on `state.is_terminal`.
- `ui/rail._density` buckets the row as `"ended"`.
- `store._needs_you` recognizes a waiting agent only via
  `AWAITING_INPUT` — while the node sits at `DONE` it produces no obligation, so it
  is in neither the "needs you" list the inbox renders (`ui/inbox`) nor the
  "still running" list.

It self-heals once `StateChanged(AWAITING_INPUT)` lands — `store.py`'s
`StateChanged` arm guards only on `rec.pending`, so a terminal state is no obstacle
— but for the length of that gap the UI is actively, not just staleness-ly, wrong.

### 2. A real failure gets un-failed moments later

`run()`'s `if isinstance(message, ResultMessage):` block does not
check `msg.is_error`. So when `_result` correctly emits `AgentFinished(FAILED)`
for a genuine API error, the same loop iteration goes on to await
`_poll_context()` and then emits `StateChanged(AWAITING_INPUT)` regardless —
overwriting `FAILED` with a non-terminal state. Unlike defect 1 this does not
self-heal: nothing re-asserts `FAILED` afterward, so the failure is visible for at
most one frame before the row reads as an ordinary idle session. This is the same
missing-guard shape as defect 1, in the same fourteen lines, and worse: it isn't
transient, it's a silent loss of the failure signal.

### What the tests currently lock in

`tests/test_driver.py::test_success_finishes_done` and
`::test_interrupted_turn_is_cancelled_not_done` asserted the pre-fix
behavior of `_result` at the unit level — a successful result produces
`AgentFinished(DONE)`, an interrupted one produces `AgentFinished(CANCELLED)`.
Both need to change as part of this fix, not just the integration-level behavior
in `run()`. The interrupted-turn test's docstring is worth keeping in spirit even
though its assertion changes: *"a cancelled agent is not a completed one, and
conflating them would tell the operator their interrupt did nothing"* — the fix
below preserves that guarantee through the topic text instead of through a
terminal state.

## Proposed change

Move the decision of *what a `ResultMessage` means for this node's state* fully
into `run()`, which is the only place that knows, after the awaits, whether the
turn merely ended or the session did. `Translator._result` stops deciding
`AgentFinished` for anything except a genuine error:

- **`_result`**: keep `AgentFinished(FAILED, ...)` for `msg.is_error`, unchanged.
  For every other case (ordinary completion or an interrupted turn), emit no
  state intent at all — only the usage delta. `AgentState.CANCELLED` is dropped
  from this method entirely (see the open question below).

- **`run()`**'s post-`ResultMessage` block becomes conditional on the outcome,
  since it already holds `message`:

  ```python
  if isinstance(message, ResultMessage) and not message.is_error:
      if self._subagents:
          await self._await_subagents(client, translator)
      await self._poll_context()
      topic = "waiting for you"
      if message.terminal_reason in ("aborted_streaming", "aborted_tools"):
          topic = "interrupted - waiting for you"
      self.bridge.emit(StateChanged(self.node_id, AgentState.AWAITING_INPUT, topic=topic))
  ```

  An error result now leaves `FAILED` standing — nothing downstream touches it
  until the operator acts or another message arrives.

- **`AgentFinished(DONE)`** is reserved for the three call sites that are already
  correct and untouched by this change: the message stream closing on its own
  (the end of `run()`'s `async for`), the operator closing the session
  (`run()`'s `asyncio.CancelledError` arm), and a sub-agent's own stop hook for
  its own node (`AgentSession._subagent_stop`).

- **Tests**: rewrite `test_success_finishes_done` to assert `_result` emits *no*
  `AgentFinished` for an ordinary completion (only `UsageAccrued`), and
  `test_interrupted_turn_is_cancelled_not_done` similarly, renamed to reflect
  that the interrupt signal now lives in the topic `run()` attaches to
  `AWAITING_INPUT`, not in a distinct terminal state. Add a `test_driver.py` (or
  a new integration-level) case around `run()` itself covering the error path,
  since that is where defect 2 lives and nothing currently exercises the
  interaction between `_result`'s `AgentFinished` and the loop's own
  `StateChanged` in the same turn.

## Open question: does an interrupted turn still deserve `CANCELLED`?

Dropping `AgentState.CANCELLED` from the success/interrupted branch of `_result`
is a real behavior change, not just a bug fix, and needs a decision before
building:

`ui/compose.py`'s own text calls interrupt "the recoverable lever" — the session
is meant to stay live and messageable afterward, which today's `CANCELLED` (a
`_TERMINAL_STATES` member) actively defeats, in exactly the same way `DONE` does
for an ordinary turn. Recommendation: an interrupted turn should land on
`AWAITING_INPUT` with a topic that says so, not on a terminal state — the
operator's confirmation that the interrupt landed comes from the topic text and
the transcript's own record of the interruption, not from the row looking
finished.

If that's accepted, `AgentState.CANCELLED` becomes reachable nowhere in the
current codebase (it stays defined, and `ui/rail`'s `_density` and `_claim` keep branching
on it, in case a future terminal-cancellation path — e.g. the pool hard-cancelling
a wedged session — needs it). Worth confirming that's an acceptable outcome before
building, since it is a step beyond "fix the bug as reported."

**Decided 2026-08-14: accepted.** An interrupted turn lands on `AWAITING_INPUT`
with `model.INTERRUPTED_TOPIC`, and `CANCELLED` is emitted nowhere.

## Consequences worth stating before building

- **No store or intent-shape changes.** This is entirely a driver.py
  emission-ordering fix; `AgentFinished`, `StateChanged`, and every store arm stay
  as they are. Lower risk than it looks from the size of the write-up.
- **The `AgentFinished` store arm still has no guard** against being applied to a
  node that shouldn't be touched. Neither does `StateChanged`, which guards on
  `rec.pending` alone — a terminal state does not stop it. (`SubagentProgress` is
  the one arm that checks `rec.state.is_terminal`.) Not adding one is
  deliberate: a guard there would paper over a future instance of this same class
  of bug (wrong intent, right shape) instead of surfacing it. The fix belongs at
  the emission site, which is where the meaning of the message is actually known.
- **`SUBAGENT_GRACE_S` (120s) no longer matters for this defect** once `_result`
  stops emitting a terminal state — the parent row will correctly read as active
  for the whole time it's awaiting sub-agents, with no separate fix needed there.

## Verification plan

- `tests/test_driver.py`: rewrite the two tests named above; add a case
  asserting an error `ResultMessage` leaves `FAILED` standing through a
  simulated subsequent loop iteration (or, if that's awkward at the `Translator`
  unit level, an integration test against `AgentSession.run()` with a fake
  client that yields an error result followed by nothing).
- `tests/test_store.py`: no changes expected — `AgentFinished`/`StateChanged`
  application is unchanged; only what the driver emits changes.
- Manual: dogfood a session that spawns a sub-agent and watch the parent row
  through the sub-agent's lifetime — this is the reproduction with the widest
  window (up to 120s under the current code) and the easiest to confirm by eye.
- `mypy`, `ruff`, `black`, full suite green before calling this built.

## What was built (2026-08-14)

The proposal above, plus five things it did not cover. Each is here because the
proposal was wrong or silent about it, not because the scope grew.

- **`Translator._result`** emits `AgentFinished(FAILED)` for `msg.is_error` and no
  state intent otherwise. `AgentState.CANCELLED` is gone from the method.
- **`AWAITING_TOPIC` / `INTERRUPTED_TOPIC` live in `model.py`**, next to the states
  they accompany, because two components need the same spelling: `run()` writes the
  topic and `_needs_you` reads it.
- **The interrupt reaches the inbox, not only the row.** The proposal rested the
  whole interrupt signal on the topic, and the rail does render `rec.topic` — but
  `_needs_you` built `QuestionPending` with a hardcoded summary, so in the list the
  operator actually works from an interrupted turn was indistinguishable from an
  ordinary one. It now reads `"interrupted - reply or close"`. This does not
  reintroduce the guess the surrounding comment refuses: `terminal_reason` is the
  CLI reporting what it did, not an inference about what the agent said.
- **The stream close re-asserts a standing failure.** The proposal listed
  `AgentFinished(DONE)` there as "already correct and untouched". It is not: the CLI
  closing the stream behind an error result turned a standing `FAILED` into `DONE`
  and the node produced no obligation at all. `run()` holds the failing
  `AgentFinished` — read per result, not latched, so a session that errors and then
  takes a good turn can still close normally — and re-emits **that same intent** at
  the close.

  Re-emitting rather than suppressing the emit is the whole of the fix, and the
  first attempt got it wrong. Between the error and the close, any `StateChanged`
  for the node moves the record off `FAILED`: the store's arm guards on
  `rec.pending` and not on terminality, and both `Translator._rate_limit` and
  `_assistant` emit one for the root. Suppressing left a session whose subprocess
  was gone reading as `RATE_LIMITED` — non-terminal, no obligation, reply box
  enabled — or as `THINKING`, which is active, so `any_active` never settles and the
  app never idles again. Re-emitting the original intent rather than a fresh one
  also keeps `ended_at` at the moment the session died, which is what orders the
  inbox.
- **A recovery clears `ended_at` and `acknowledged`.** `StateChanged` moving a
  record off a terminal state now clears both. `ended_at` stops and dims the elapsed
  clock, so a recovered session read as finished in the rail and in HEALTH while the
  inbox asked for a reply. `acknowledged` is worse than cosmetic: it suppresses the
  `SessionFailed` obligation, so a stale `True` carried through a recovery means the
  *next* failure asks for nothing. `error` deliberately survives — nothing reads it
  except through an obligation gated on `FAILED`, and it is the only structured copy
  of what went wrong.
- **The `asyncio.CancelledError` arm asks whether the teardown was asked for.**
  `SessionPool.close` and `SessionPool.shutdown` both set
  `AgentSession.teardown_requested` before they cancel, and only that means DONE —
  in which case DONE deliberately wins over a standing `FAILED`, because ending a
  session *is* the dismissal and leaving `FAILED` would keep a dismissed session in
  the "needs you" list with nothing left to act on.

  The flag says "this cancellation was asked for", not "the operator clicked close",
  and the difference was argued and settled: closing one session and quitting the
  application are the same statement *for a session*, which is exactly the contract
  `AgentState.DONE` carries on the enum. Shutdown is also the one cancellation where
  who asked is known with certainty, so reporting it as a failure would be a false
  failure signal — the class of defect this doc exists to remove.

  An earlier draft asserted that `Pool.close` and `Pool.shutdown` were the only
  cancellers. They are not: `Bridge.stop` stops the loop, and the loop thread's own
  `finally` cancels every remaining task, so a session can be cancelled without the
  pool being asked at all; and the SDK is built on anyio task groups whose cancelled
  exception class on asyncio *is* `asyncio.CancelledError`, which makes a transport
  teardown surfacing into our `async for` plausible. Reporting either as DONE is the
  same silent loss of a failure signal, one arm over — so a cancel nobody asked for
  re-asserts a standing failure, or reports one if there is none. Emitting *nothing*
  there was considered and rejected: it leaves the record wherever its last
  `StateChanged` put it, which is the stranded-session defect above in the other arm.

Tests are integration-level against `AgentSession.run()` with a fake client, and
every assertion is on an `AgentRecord` in a real `Store` rather than on the drained
intents. That distinction is the point: "no `StateChanged(AWAITING_INPUT)` was
emitted" passes while a later `AgentFinished(DONE)` clobbers `FAILED` anyway. The
fake drains the bridge into the store after each message, standing in for the UI
thread's own cadence, which is what makes the mid-stream state observable at all.

### Known limits, not yet addressed

**The interrupt fact rides on a topic string.** `run()` writes `INTERRUPTED_TOPIC`
and `store._needs_you` compares against the same constant, so no drift between the
two is possible and no duplicate of a derivable fact is stored. The narrow limit is
that any later `TopicChanged` for a node sitting in `AWAITING_INPUT` erases "you
interrupted this". Today the only emitter that could is `_rate_limit`'s
`allowed_warning`, which cannot fire while a turn is over — so this is safe now and
not safe by construction. Decided to keep as it stands: the structural alternative
is a boolean on `StateChanged` and on `AgentRecord` duplicating what the topic
already says.

**The reply box.** `FAILED` now stands where it used to be overwritten, and it is in
`_TERMINAL_STATES`, so `ui/compose.py` disables the reply box on it. After a
transient API error the session is unmessageable even though `self._client` is
still set and `run()` is still iterating — the defect this doc fixes was
accidentally keeping it recoverable. Deliberately not fixed here, and now written
up on its own: `2026-08-14-a-failed-session-can-still-be-talked-to.md`.
