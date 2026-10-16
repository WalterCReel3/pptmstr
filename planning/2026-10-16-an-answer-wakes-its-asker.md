# An answer wakes the session that asked for it

**Dated:** 2026-10-16 · **Status:** decided, not built (board task wake-asker) · **Amends:**
decision 4 of [`2026-10-12-wiring-the-inter-team-record.md`](2026-10-12-wiring-the-inter-team-record.md)
("Decision answers are delivered by read_decisions only, not by a wake. Cost: an answered
decision can sit unread; the pane shows that state.") and settles the open question in
[`2026-10-13-a-break-glass-command-the-operator-runs.md`](2026-10-13-a-break-glass-command-the-operator-runs.md)
l.126 ("Whether the asker's lead is told…").

## Why

The cost decision 4 accepted showed up on 2026-10-16. The operator answered decisions and ran
commands, and the lead did not read the answers for minutes to hours, because nothing told it.
The operator: "I think you need to develop triggers on when I've answered decisions. You keep
thinking I haven't answered when I have." And: "We need an actual trigger or event to notify the
session somehow."

## What was found (read, by an Explore agent; nothing run)

- DecisionAnswered, DecisionDismissed and CommandEnded change the store and emit no effect
  (`store.py` ~l.1129, 1139, 1258-1283; `_ended_by_operator`'s docstring: "Nobody is waiting on
  this intent to be told").
- The only way text enters a session mid-run is `AgentSession.send` (`driver.py` ~l.3016), used
  today only by the operator's message box.
- An idle session stays connected in AWAITING_INPUT. `AskerFate.IDLE` already says: "Nothing wakes
  it when a decision is answered, so the answer sits unread until the operator sends that session
  a prompt."
- A query sent mid-turn runs as its own turn: measured n=2 on CLI 2.1.226
  (`2026-08-21-a-lead-is-already-free-while-its-workers-run.md`).
- No hook injects context into a session today.

## The decision

Offered: (a) wake the asker with a message through the send path, (b) a notice on the next
tool result mid-turn, (c) both. The operator: "(a) Seems just more straightforward to me."

So when the operator answers or dismisses a decision, or a command they ran ends, pptmstr sends
the session that asked a short message saying so and naming the id. The message is pptmstr's,
never the operator's. It is drawn and recorded as pptmstr's, so neither the agent nor a reader of
the transcript can take it for the operator's words.

**The lead's defaults, not ruled on by the operator, and open to revision:**
- **Events:** a decision answered or dismissed, and a command ended. Not concerns: waking for a
  concern would let agents wake agents.
- **Who is woken:** the asking session. A sub-agent's decision or command wakes its lead, because
  sub-agents already relay through their lead.
- **A concern to the lead also wakes the lead** (added later the same day, the lead's call).
  Builders' questions to the lead waited 15-75 minutes unread, because nothing woke the lead
  between operator prompts. Only a worker's concern addressed to its own session's lead wakes,
  and concerns between workers do not, so agents cannot wake each other in a loop.
- **No asker to wake:** if the session has no live client (closed, failed or ended), nothing is
  sent. The pane still shows the answered state, as it does today.
- **Each wake costs a turn.** A burst of answers coalesces into one message, not one per answer.

The tool texts that say "nothing tells you" (`bus.py`, `commandbus.py`; about twelve places) are
reworded to say what is now true.
