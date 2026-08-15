# A failed session can still be talked to, and the reply box does not know

**Dated:** 2026-08-14 · **Status:** proposed, not built · **Found by:** review of
`2026-08-11-turn-end-still-marks-done`, raised three times during that work and
deferred each time · **Caused by:** that doc's fix, which is correct and should not
be reverted.

Symbol names, not line numbers. The tree moved 30–300 lines under the 08-11 doc's
own citations twice in three days, and a stale number reads as verified.

## What changed, and what it exposed

Before the terminal-state fix, `Translator._result` emitted `AgentFinished(FAILED)`
on an API error and `run()`'s loop then emitted `StateChanged(AWAITING_INPUT)`
regardless, overwriting it. That was a silent loss of the failure signal and it is
now fixed: an error result leaves `FAILED` standing, and the stream close
re-asserts it.

`FAILED` is in `model._TERMINAL_STATES`. `ui/compose.py` reads
`record.state.is_terminal` twice — once to print *"session failed - it can no longer
be messaged"*, and once to wrap the reply box and the send button in
`begin_disabled`. So the fix has a cost it did not have before, because before it
the state never survived long enough to be read:

**After a transient API error the session is unmessageable, and it is not gone.**
`AgentSession._client` is cleared only in `run()`'s `finally`, and after an error
result `run()` keeps iterating — it no longer breaks out. So the client is live,
the subprocess is alive, and `AgentSession.send` would take a prompt. The pane says
otherwise.

A 529 is the case that matters. It is transient by definition, it is what a rate
storm produces, and the operator's correct response — say something and carry on —
is the one the UI has taken away.

## The predicate is doing two jobs

```python
@property
def is_terminal(self) -> bool:
    """Whether no further transition is expected without operator action."""
```

That is one claim. The reply box needs a different one: **is there a subprocess on
the other end of this box.** The two agree for `DONE` and `CANCELLED` and disagree
for `FAILED`, which is exactly the state this work made durable.

The disagreement is not a naming problem. "No further transition is expected" is
true of a failed session — nothing will happen unless the operator acts — and
typing into the reply box *is* the operator acting. The predicate is right; the
caller is asking the wrong question with it.

So the fix is not to widen `is_terminal`, and it is not to take `FAILED` out of
`_TERMINAL_STATES` — the rail's "ended" bucket, the elapsed clock and the failure
obligation all want `FAILED` terminal and are all correct today. It is to give the
compose pane the predicate it actually needs.

## What that predicate would have to be derived from

`AgentRecord` cannot answer "is the subprocess alive" today, and the honest
candidates are not equivalent:

- **`state is not DONE and state is not CANCELLED`.** Cheap, derives from what is
  already there, and wrong in one direction: a session that failed because the
  transport died is as unmessageable as a closed one, and this would offer a box
  that silently no-ops (`AgentSession.send` logs a warning and returns when
  `_client` is `None`). That warning goes to the log, not the pane, so the operator
  sees a message they typed vanish.
- **A fact from the driver.** `run()` knows whether it is still iterating; nothing
  in the store does. This is the accurate answer and it costs an intent and a field,
  which is the shape §1 of `STYLE.md` argues against unless the fact is genuinely
  not derivable. It is genuinely not derivable — liveness of a subprocess is not a
  function of any other field in the snapshot — so this is the exception rather than
  a violation of the rule.

Not resolved here. The second is more likely right, but it should be decided
against a measurement (below) rather than by argument.

## Three more things in the same area, all reachable today

**A cancel nobody asked for now lands on `FAILED` too.** `run()`'s
`asyncio.CancelledError` arm reports `AgentFinished(FAILED, error="the session was
cancelled without being closed")` when `AgentSession.teardown_requested` is unset —
a transport teardown, or any canceller that did not go through `SessionPool`. That
is the right report, and it widens this gap: the row is unmessageable *and* in this
case correctly so, since the client really is gone. Whatever predicate replaces
`is_terminal` here has to distinguish these two failures, which is the argument
against the cheap version above.

**`FailureAcknowledged` does not restore the box.** Its store arm sets
`acknowledged=True` and nothing else. `acknowledged` gates only the `SessionFailed`
obligation in `store._needs_you`, so dismissing a failure from the inbox removes the
row and leaves the session exactly as unmessageable as it was. The dismiss button
(`app.py`'s `InboxActions.dismiss`) reads as "I have dealt with this", and what it
does is stop reminding the operator about a session they still cannot use.

**Replying does recover it, and leaves a trace.** If the box were enabled,
`AgentSession.send` emits `StateChanged(THINKING, topic="reading your message")`,
and the store's arm — which guards on `rec.pending`, not on terminality — applies
it, clearing `ended_at` and `acknowledged` on the way out of the terminal state.
The session recovers correctly. But `error` deliberately survives, so a live record
carries the text of a failure it has already come back from.

That is invisible today: nothing renders `AgentRecord.error` directly. Every reader
goes through `SessionFailed.error`, which `_needs_you` only builds while the state
is `FAILED`. It is a trap for the next surface rather than a present defect — the
first pane that shows "last error" on a record will show a stale one — and it is
recorded here because the reasoning for keeping `error` (it is the only structured
copy of what went wrong) is sound and should not be re-litigated by whoever trips
over it.

## What would settle the design

One measurement, and it is small: **after an error `ResultMessage`, does the CLI
accept another `query()` on the same client?**

Everything above assumes it does. The evidence for that assumption is structural —
`_client` is still set, `run()` is still iterating, `send` would not refuse — and
nobody has sent a prompt down a client that has just reported an API error. If the
CLI has torn the session down behind the error, then the pane's current text is
accurate, `FAILED` should stay unmessageable, and the whole of this document is
about a message that should be reworded rather than a box that should be enabled.

`scripts/verify_questions.py` is the natural place: force an error turn (an
oversized request, or a deliberate rate-limit), then query again on the same client
and record whether a second `ResultMessage` comes back. Until that runs, the shape
of the fix is not decided, only the defect.

## Not doing

- **Widening `is_terminal`.** Three other consumers depend on its current meaning
  and are right to.
- **Removing `FAILED` from `_TERMINAL_STATES`.** Same reason, more bluntly.
- **Reverting any part of `2026-08-11-turn-end-still-marks-done`.** The state
  surviving is the fix. This is its cost, stated so it is paid deliberately.
