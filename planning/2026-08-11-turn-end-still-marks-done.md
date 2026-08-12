# A turn ending still marks the node DONE

**Dated:** 2026-08-11 · **Status:** proposed, not built · **Found by:** operator
report ("sessions marked done while still working"), confirmed by code review ·
**Regresses:** the DONE/AWAITING_INPUT split decided in
`2026-08-10-conversational-sessions.md` — that doc narrowed `DONE` to "the operator
closed the session"; `Translator._result` was never updated to match and still
implements the old one-shot meaning.

## What was observed

A session's row intermittently shows as finished — dimmed "ended" styling, reply
box disabled with "session done — it can no longer be messaged" — while the agent
is demonstrably still working: sub-agents still reporting progress, or the app
about to hand the operator back the reply box for a follow-up. It self-corrects a
moment later. The window is short but real, and it lands exactly when an operator
is most likely to act — right after an agent stops talking, which is also the
moment an operator reaches for the reply box.

## Why it happens

`AgentState.DONE`'s contract, stated on the enum itself (`model.py:43`): **"DONE
means the operator closed the session, not that a turn ended."** Two places in
`pptmstr/driver.py` don't honor it.

### 1. `Translator._result` fires DONE on every ordinary turn end

`driver.py:339-362`:

```python
def _result(self, msg: ResultMessage) -> list[Intent]:
    ...
    cancelled = msg.terminal_reason in ("aborted_streaming", "aborted_tools")
    state = AgentState.CANCELLED if cancelled else AgentState.DONE
    out.append(AgentFinished(self.node_id, state, time.monotonic()))
    return out
```

Every `ResultMessage` is a turn boundary, not a session boundary — `run()`'s own
docstring says so (`driver.py:694-703`). This method predates the conversational
rewrite (`d6f0c96`) and still carries the pre-rewrite assumption that a result
ends the session.

The corrective intent, `StateChanged(AWAITING_INPUT)`, is emitted later in
`run()`'s loop (`driver.py:724-740`) — but only after two real `await` points:

```python
for intent in translator.handle(message):     # AgentFinished(DONE) queued here, immediately
    self.bridge.emit(intent)
if isinstance(message, ResultMessage):
    if self._subagents:
        await self._await_subagents(client, translator)   # up to SUBAGENT_GRACE_S = 120s
    await self._poll_context()                             # an RPC to the CLI subprocess
    self.bridge.emit(StateChanged(self.node_id, AgentState.AWAITING_INPUT, ...))
```

`Bridge`'s queue is a plain FIFO (`bridge.py:139-163`) — this is not a reordering
race. It is a genuine gap: the UI thread drains and renders on its own frame
cadence (`app.py:100`), independently of that coroutine's progress, so any frame
landing between the two `emit()` calls renders `DONE`. Every consumer of
`AgentState.is_terminal` treats that at face value:

- `ui/compose.py:81-94` disables the reply box.
- `ui/rail.py:93-98` buckets the row as `"ended"`.
- `store.py:_needs_you` (404-415) and `ui/inbox.py:490-494` only recognize a
  waiting agent via `AWAITING_INPUT` — while the node sits at `DONE` it is in
  neither the "needs you" list nor the "still running" list.

It self-heals once `StateChanged(AWAITING_INPUT)` lands — `store.py`'s
`StateChanged` arm has no terminal guard — but for the length of that gap the UI
is actively, not just staleness-ly, wrong.

### 2. A real failure gets un-failed moments later

`driver.py:724-740`'s `if isinstance(message, ResultMessage):` block does not
check `msg.is_error`. So when `_result` correctly emits `AgentFinished(FAILED)`
for a genuine API error, the same loop iteration goes on to await
`_poll_context()` and then emits `StateChanged(AWAITING_INPUT)` regardless —
overwriting `FAILED` with a non-terminal state. Unlike defect 1 this does not
self-heal: nothing re-asserts `FAILED` afterward, so the failure is visible for at
most one frame before the row reads as an ordinary idle session. This is the same
missing-guard shape as defect 1, in the same fourteen lines, and worse: it isn't
transient, it's a silent loss of the failure signal.

### What the tests currently lock in

`tests/test_driver.py::test_success_finishes_done` (line 291) and
`::test_interrupted_turn_is_cancelled_not_done` (line 298) assert the *current*
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
  (`driver.py:746`), the operator closing the session
  (`asyncio.CancelledError`, `driver.py:749`), and a sub-agent's own stop hook
  for its own node (`driver.py:543`).

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
current codebase (it stays defined, and `ui/rail.py:96` keeps branching on it, in
case a future terminal-cancellation path — e.g. the pool hard-cancelling a wedged
session — needs it). Worth confirming that's an acceptable outcome before
building, since it is a step beyond "fix the bug as reported."

## Consequences worth stating before building

- **No store or intent-shape changes.** This is entirely a driver.py
  emission-ordering fix; `AgentFinished`, `StateChanged`, and every store arm stay
  as they are. Lower risk than it looks from the size of the write-up.
- **The `AgentFinished` store arm still has no guard** against being applied to a
  node that shouldn't be touched (unlike `StateChanged`/`SubagentProgress`, which
  both check `rec.pending` / `rec.state.is_terminal`). Not adding one is
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
