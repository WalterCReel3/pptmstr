# A lead takes a turn while it is purely waiting on sub-agents

**Dated:** 2026-08-21 · **Status:** measured; no work follows for the question asked ·
**Follows:** [`2026-08-14-a-role-runs-one-agent.md`](2026-08-14-a-role-runs-one-agent.md)

**Scope: can a lead take a turn while it is purely waiting on sub-agents?**
Nothing else. What this ran into and did not pursue is listed at the bottom.

**Yes, measured.** Two runs of
[`scripts/verify_lead_turn_during_subagents.py`](../scripts/verify_lead_turn_during_subagents.py).
The capability needs no code change. The reason it looked absent is that
`THINKING` is what the code displays either way, so the thing the operator was
looking at could not have shown the answer.

---

## The measurement

The observable had to be **ordering, not state**. A lead in `THINKING` during a
fan-out is what this code produces whether or not it can answer: `_result` emits
no state intent (`driver.py:527-532`), so entering the wait leaves the node on
its last `StateChanged`; and `send` emits `THINKING` with topic *"reading your
message"* at `driver.py:1597-1599` **before** it calls `query` at `:1600`, so the
topic moves whether or not the CLI ever acts. Neither the state nor the topic is
evidence.

So: park a sub-agent in a sleep, wait for `SubagentStart`, send a second prompt
asking for one distinctive word, and ask whether that word comes back before
`SubagentStop`.

Run 2, seconds from connect:

```
 4.00  SubagentStart
 5.00  send            followup prompt (sub-agent live)
 5.22  Result/root     <- turn 1 ends
 6.64  text/root       BANANA
 6.66  Result/root     <- turn 2 complete
25.16  SubagentStop
```

A complete root turn began and finished 18.5s before the sub-agent stopped. Run 1
agreed — canary at 6.10s, `SubagentStop` at 9.89s — on a narrower window, because
that run's sub-agent backgrounded its own sleep; the second run closed that and
the margin widened rather than moving.

Two details the runs settle beyond the bare yes:

- **The prompt landed mid-turn and was still answered as its own turn.** The send
  at 5.00 precedes the root's own `ResultMessage` at 5.22. The CLI neither drops
  it nor folds it into the turn in flight.
- **It is not buffered against the background task.** The answer arrives 18.5s
  before the task it was supposedly waiting behind.

## What this settles, and the part it does not

Settled: **the CLI dispatches a user message that arrives while a background task
is outstanding.** That was the uninspected link — everything readable in this
repository and the SDK is about our side of the pipe, and none of it could
decide this.

Not settled by these runs: **the probe drives `ClaudeSDKClient` directly, not
`AgentSession`.** It has its own simplified drain loop and never enters
`_await_subagents` (`driver.py:1621-1688`). The claim that pptmstr's wait loop
forwards such a turn is still read-derived — from the fact that it dispatches
everything it reads through `translator.handle` and `bridge.emit` (`:1669-1671`)
and that its exit condition is `_live_subagents`, which a root turn does not
touch. That reading is now the only unmeasured step, and it is a much smaller one
than the question started with: the transport is proven, and what remains is
whether our own loop drops something on the floor.

Closing it means the same probe run through `AgentSession` against a real
`Bridge`, watching for the canary's segments in the root `Transcript`. Worth
doing before anything is built on this; not worth doing to answer the question
that was asked, which is answered.

## Verification boundary

**Executed:** `scripts/verify_lead_turn_during_subagents.py`, twice, timelines
above. `make check` — ruff and mypy clean; one splash test failed, in splash work
already dirty in the tree before this and untouched by it.

*Correction, 2026-08-31:* the test named here was
`tests/test_splash_junction.py::test_only_part_of_the_field_changes_on_any_one_step`
failing at `0.0692 > 0.06`. It was **deleted in `6a2b950`** on 2026-08-22 and the
name appears in no `.py` file in the tree. Its successor,
`tests/test_splash.py::test_only_a_fraction_of_the_cycling_cells_change_on_any_one_step`,
bounds the change fraction at `MAX_SIMULTANEOUS_CHANGE = 0.35` over
`len(MOVABLE)` — a different denominator, and panel-area flicker is bounded
structurally by `WAKE_ROWS` rather than by the 6% figure. Nothing enforces 0.06.
The point this section was making — that the failure belonged to the splash work
and not to this measurement — is unaffected.

**Read, not run:** every `driver.py`, `store.py` and `ui/compose.py` citation in
this record.

**Conditions:** `model="claude-sonnet-5"`, `permission_mode="dontAsk"`, all hooks
auto-allow. pptmstr parks `Agent` at the gate instead; that changes when a
sub-agent starts, not whether a later prompt is dispatched.

## Not in scope

Met while running this, deliberately not pursued:

- **`THINKING` is a display artifact here, and the surfaces inherit it.**
  `compose.py:84` tells the operator a message "will be read after this turn" when
  the turn is over and the message will be read now, and `_needs_you` cannot list
  the lead at all (`store.py:877` reads `AWAITING_INPUT` alone). The capability
  exists; nothing tells the operator it does. That is a separate decision about
  what the state model should be able to say.
- **`SubagentStart` fired twice for one `agent_id` in both runs** — a premature
  stop followed by a resume. That is the `start → stop → start` path the 08-14
  note recorded as rebuilding the record, and it means `_live_subagents` empties
  and refills during a single logical sub-agent. It does not touch this verdict:
  the canary landed inside the first window in both runs.
- Fan-out depth, `subagent_cap`, board depth.

## Provenance of this text (added 2026-08-22)

Everything above this section was written by session `7f0b40c2` on 2026-08-21.
Its two attempts to land it (15:30Z, retried 21:30Z) each died after exactly six
hours on a silent `PreToolUse` hook timeout — the host app was dead — and the
text was recovered verbatim on 2026-08-22 from the transcript's `Write` inputs.
The two attempts differ only in a line wrap; this is the later one.

It replaces the pre-probe draft that occupied this path (*"A lead is already
free while its workers run"*, status "nothing built", premise marked
unobserved — true when written, measured false the same afternoon). That
draft's full text remains recoverable from the same transcript (the successful
`Write` at 14:59Z), including its ordered-cost list and gate-shape reasoning,
which this record deliberately drops as out of scope.

Review history: the operator refused two pre-measurement rewrites of this
record (15:03Z, 15:05Z — "Hold on the edit. I want to research that now"). The
measurement above is that research. This measured version never reached
operator review in its own session; landing it was approved in the 2026-08-22
session that recovered it.

Restored with flags, not settled:

- The `AgentSession`-level probe this record names as its own precondition has
  still not run.
- Byte-identity of the on-disk probe script with what run 2 executed was
  read-matched against the transcript, not verified.
- The evidence is n=2, one day, one CLI version (2.1.226), timed by the probe's
  own clock.
