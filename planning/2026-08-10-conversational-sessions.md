# Sessions are one-shot, and questions fall through the gap

**Dated:** 2026-08-10 · **Status:** proposed, not started · **Found by:** dogfooding

## What was observed

`scripts/verify_questions.py`, two live cases. Asked an agent to ask clarifying
questions before doing anything:

- It asked them **as prose** in an `AssistantMessage`.
- **Zero tool calls.** No hook fired except `UserPromptSubmit`.
- Then a `ResultMessage` — the turn ended normally, not in error.

`ExitPlanMode` and `AskUserQuestion` are **not in this CLI's tool list**, so there
is no structured-question channel to render. Prose is the channel.

## Why nothing surfaced

Three defects, in increasing order of how much they matter.

1. **A question is indistinguishable from an answer.** Both end the turn with a
   `ResultMessage`, so the node goes `DONE`. The operator sees a completed agent
   that is in fact waiting on them.

2. **There is no reply channel.** `driver.py` calls `client.query(...)` exactly
   once and never again. Even having noticed the question, nothing can answer it.

3. **The session is gone by then.** The message loop `break`s at the first
   `ResultMessage`, which exits the `async with` and disconnects the client. So
   the ability to reply is lost in principle, not merely unimplemented — a reply
   box bolted on later would have nothing to talk to.

The transcript pane does show the question text. That is the same lesson as the
`AWAITING_APPROVAL` clobber: the information existed, and no signal pointed at it.

## Proposed change

**Sessions become conversational rather than one-shot.** This is a lifecycle
change, not a widget.

- **Keep the client connected after a `ResultMessage`.** Stop breaking out of the
  `async with`; keep reading and let the session sit connected.
- **New state `AWAITING_INPUT`** — turn complete, session live, ready for more.
  It is an *idle* state like `AWAITING_APPROVAL`, so a session waiting on the
  operator still costs nothing (I8's reasoning applies unchanged).
- **`DONE` narrows to "the operator closed this session"**, which is what a
  terminal state should mean. Today it means "finished a turn", which is why a
  paused conversation looks finished.
- **`AgentSession.send(text)`** calls `client.query(text)` on the live client and
  the existing loop picks the response up. The Bridge already carries this shape.
- **A reply box** in the detail pane for the selected node, enabled when the
  session is connected.

## Consequences worth stating before building

- **Sessions stop ending on their own.** A subprocess per session now lives until
  the operator closes it, so the concurrency cap stops being an admission-rate
  limit and becomes a live-resource limit. Closing a session has to become a
  first-class action, or the pool fills with finished conversations.
- **`SessionPool` slot accounting changes.** Today a slot frees when `run()`
  returns; with sessions staying alive it must free on explicit close.
- **This subsumes part of "make it drivable."** Reply, close, and interrupt are
  the same missing capability seen from three sides, and interrupt is already
  wired in the pool with no caller.

## Alternative considered and rejected

Detect a trailing question heuristically (question mark, phrasing) and flip the
row to a "needs you" state while still disconnecting. Cheaper, and wrong: it
guesses at intent, it cannot be answered without reconnecting anyway, and it would
be confidently wrong on any turn that ends with a rhetorical question. The problem
is that the session ended, not that the question was unlabelled.
