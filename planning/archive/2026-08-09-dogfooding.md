# Dogfooding notes

**Dated:** 2026-08-09 · **Status:** open · **Follows:** the §8 build order being complete

All seven build-order steps are done. The next backlog comes from using the thing
rather than from guessing, so this file is where friction gets written down as it
happens instead of being remembered later.

## Driving it today

```sh
make run                                   # empty window, nothing to do (see below)
.venv/bin/python -m pptmstr --task "..."   # the real entry point
.venv/bin/python -m pptmstr --task a --task b --cap 2
.venv/bin/python -m pptmstr --fake         # UI only, no SDK, no cost
.venv/bin/python -m pptmstr --task "..." --template research   # a team (step 8)
```

`Ctrl+N` opens the launcher, which now carries a **team** combo: `solo` (the
default, and unchanged behaviour), `feature`, `research`. `--fake` does not
exercise the bus, so anything team-shaped costs real tokens — and a three-agent
team on one review question has been measured past fifteen minutes.

`j`/`k` move, `a` approve, `r` reject with a reason, `e` edit arguments then
approve, `Shift+A` approve all from the selected agent.

## Known sharp edges — already understood, no need to rediscover

These are gaps I know about. Report them only if the *severity* surprises you;
that judgement is the useful signal, not the existence.

- **No way to start an agent from the UI.** `--task` at launch is the only route,
  so adding work means restarting. `make run` with no arguments gives an empty
  window with nothing to do.
- **No way to stop one.** `SessionPool.interrupt()` exists and nothing calls it.
  A misbehaving agent can only be stopped by killing the window, which takes its
  siblings with it. This is the one I would call a safety gap rather than a
  missing feature — mitigate with a low `--cap` and by reading diffs before
  approving.
- **No trust promotion.** §5.4 lists per-node "trust this tool class" as day-one
  batching; it was scoped out of step 4 and never came back. Long sessions
  re-approve the same class of call repeatedly.
- **No fork/retire.** §2.4 makes "fork this session" the offered action when a
  session compacts. The ring will tell you to retire a session and give you no way
  to do it.
- **Cost is collected and never shown.** `UsageRollup` accumulates on every
  message; no widget reads it. `max_budget_usd` is unwired, so there is no hard
  stop on spend.
- **Sub-agent output is not live.** Confirmed upstream behaviour, not a bug here
  (§2.5.1). The pane says so on sub-agent rows.
- **A team's board and its concerns are drawn nowhere.** Both projections are in
  the snapshot and no pane reads them, so an operator approves a message between
  two agents without seeing the work either of them holds. Roles do appear as
  sub-agent rows, and every concern appears as an approval row, so the *review*
  path is fully exercisable — it is the context around it that is missing.
  `2026-08-12-the-board-has-no-surface.md`.
- **The inbox is not actually ordered oldest-first.** Approvals sort after every
  question and failure regardless of age, because two clocks are compared.
  Predates teams; `2026-08-12-needs-you-sorts-two-different-clocks.md`.
- **No way to stop a team.** The existing "no way to stop one" gap is worse here:
  a lead you cannot stop can spawn workers you also cannot stop.

## The question a team run is worth doing to answer

**Does gating every inter-agent message make the operator a bottleneck on
*conversation* rather than on writes?** §9 raised it, step 8 could not settle it by
argument, and it needs no new UI to answer — concerns already arrive as ordinary
approval rows. The parking invariant says a parked agent costs nothing; it says
nothing about the operator's attention costing nothing, and this is the run that
finds out.

Two smaller ones, both cheap to judge from the same session:

- **Does the lead actually wait?** The briefing tells it to delegate and
  synthesise. If it implements the work itself while a worker does the same thing,
  that is a prompt defect and a cheap fix.
- **Are the forced concerns worth reading?** Workers are now required to post one
  before finishing. That may produce a genuine "here is what I am unsure about", or
  dutiful noise — which would be worse than the silence it replaced.

## Open questions the design still carries

- Keystroke-to-frame latency is reasoned, not measured — no injection tooling on
  the build box. It matters most when typing a rejection reason with everything
  parked, so it is worth a subjective verdict from real use even without numbers.
- Whether `ResultMessage.total_cost_usd` is per-turn or cumulative. The driver
  deltas against the last value, which is correct either way, but the ambiguity is
  unresolved and would show up as visibly wrong cost once cost is displayed.

## Friction log

Append as it happens. Raw is fine — the point is to capture it before it gets
rationalised into a feature request.

| date | what happened | what it cost | guess at the fix |
|---|---|---|---|
| 2026-08-09 | Session appeared stuck at "thinking" with no indication it wanted anything. It was parked on a Bash approval the whole time. | A real session abandoned as hung. First bug dogfooding found, and it made the product's central feature invisible. | Fixed: a node with a pending approval can no longer be moved out of AWAITING_APPROVAL by a late StateChanged. See below. |

| 2026-08-10 | An agent asked clarifying questions and the session just went DONE. The question was never surfaced as something to answer. | A planning session lost; looked like the agent had finished when it was waiting. | Structural, not cosmetic: sessions are one-shot and there is no reply channel. See planning/archive/2026-08-10-conversational-sessions.md |

| 2026-08-10 | Three "blocked on approval that is not in the queue" warnings in one session. Agent management felt unreliable. | Agents wedged mid-session; the watchdog was the only reason it was noticed at all. | Root cause: the store held one pending approval per node, the gate parks one per tool call, and a turn can contain several. Fixed by making it a collection. |

### The pattern behind three separate bugs

All three losses of sync had the same shape: **two sides of a boundary modelled the
same fact with different cardinality or different authority, and nothing compared
them.**

- The gate parked N futures per node; the store had one slot. Last write won,
  the rest were orphaned. (2026-08-10)
- The store said a node was `CALLING_TOOL`; the gate said it was parked. Whichever
  intent arrived last was believed. (2026-08-09)
- The gate parked a future for a node the store had never heard of, and the store
  dropped the intent as noise. (2026-08-09)

The lesson is not "be more careful with state". It is that **a blocking action has
two representations -- the thing that is blocked, and the thing the operator can
act on -- and they must be reconciled rather than kept in step by hand.** The
watchdog comparing `Bridge.parked_count` to `len(review_queue)` is what turned the
third instance from a silent hang into a report, and it is what caught this one.
Where an invariant spans a boundary, check it at runtime; do not assume the code
on both sides agrees.

### Why that one was invisible

The CLI dispatches the `PreToolUse` hook *before* it delivers the
`AssistantMessage` carrying the `ToolUseBlock`. So the gate parked the node
correctly, and a `StateChanged(CALLING_TOOL)` for the very same tool call landed
immediately afterwards and overwrote `AWAITING_APPROVAL`.

Two consequences, and the second is why it read as a hang rather than a glitch:

- The tree row said "thinking"/"calling" instead of REVIEW, so the one thing the
  operator needed to know was the one thing not shown.
- Those are *active* states, so `any_active` stayed true, the app never idled, and
  the status bar said "running". Every signal agreed on the wrong story.

The review queue did still list the item — so the information existed, just not
anywhere the eye was drawn. Worth remembering when judging where signals belong.

Fix: `pending` is the authority. While it is set, the state is AWAITING_APPROVAL
and nothing else can say otherwise; topics still update, since naming the call
under review is useful rather than misleading. The same guard covers
`SubagentProgress`, which had the identical hazard for parked sub-agents.

## Debt to clear regardless of what dogfooding says

- `pptmstr/fake_driver.py` was meant to be deleted when step 3 landed and has been
  grown instead. It is now load-bearing for screenshots and benchmarks, which is
  exactly the outcome the original note warned against. Either delete it or
  promote it deliberately to a test fixture and say so.
- No CI. `make check` is the gate and nothing runs it automatically.
- The sub-agent `tool_use_id` ↔ `agent_id` join is by adjacency and can mis-pair
  under parallel spawns from one parent (§2.5.1). Enrichment only, so the cost is
  a topic under the wrong sibling — but it is unverified under actual parallelism.
