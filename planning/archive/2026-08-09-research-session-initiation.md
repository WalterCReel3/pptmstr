# Research session initiation: parallel by default, coordinator on escalation

**Dated:** 2026-08-09 · **Status:** initiation shapes stand; everything UI-facing is
superseded by `2026-08-10-research-sessions-under-the-inbox.md`

The UI revision this doc was waiting on landed as `2026-08-10-layout-proposals.md`,
and the re-read it asked for is the successor doc above. Read that one for the
row-label decision, the corrected approval-surface expectation, the cwd/project
consequence, and the revised checklists. What is below is kept as the dated
record of why parallel is the default — that part is unchanged.

## Decision

Two initiation shapes for a multi-agent research task, chosen by whether one angle
is too broad for a single linear session:

- **Parallel independent sessions (default).** The operator decomposes the research
  question into orthogonal angles by hand and launches one root session per angle.
  No shared context between them; no `Task`/`Agent` spawning.
- **Coordinator (escalation, not a substitute for decomposition).** One session,
  prompted to use `Task` to fan out into sub-agents, reserved for a single angle
  that's genuinely too deep or broad to answer linearly.

Both are expressible today with no new code — this doc records the shape and the
mechanics it leans on, not a build plan.

## Why parallel is the default

Sessions don't share context, so overlapping angles across parallel sessions just
duplicate subprocess cost and approval traffic — decomposition has to happen before
launch, by the operator, not be delegated to a model that can't see the other
sessions. A coordinator *can* do that decomposition itself, but at a real cost: every
`Task`/`Agent` spawn is approval-gated by design (`approval.py` `_REVIEW`, fail-closed
per §5.4), sub-agent output never streams (§2.5.1, confirmed empirically, not just
documented), and the coordinator's own context fills with sub-agent summaries. Use it
when the question can't be split up front, not as the default because it feels more
automated.

## Mechanics this plan relies on (confirmed by reading, not assumed)

- **LAUNCH pane is already per-session.** `draw_launcher`'s callback is
  `launch(task, model, cwd)` (`app.py:339`) — each submission can carry its own
  model and working directory. The CLI's `--task`/`--model`/`--cwd` flags are a
  uniform-batch shortcut for the case where every angle shares both; they are not
  the only route and don't need to be extended for heterogeneous angles.
- **Queued sessions are visible, not dropped.** `SessionPool.submit()` calls
  `session.announce()` before checking for a free slot (`pool.py:65-71`), so an
  angle launched past `cap` shows up in the tree as queued rather than appearing to
  have been ignored.
- **Cap is a live-concurrency limit, not an admission throttle**, as of the
  conversational-sessions change (`2026-08-10-conversational-sessions.md`). A
  session that finishes a turn goes to `AWAITING_INPUT` and keeps its slot until
  `close()`d. Queued angle 5 will not start because angle 1 finished — it starts
  when angle 1 is explicitly closed. Initiation therefore includes a close-out step,
  not just launch-and-wait.
- **Approval surface differs sharply by mode.** Parallel sessions with no `Task`
  usage only ever park on `WebFetch`/`WebSearch` (and `Write` if a session is told
  to save notes) — predictable and light. A coordinator adds one approval per
  sub-agent spawn plus that sub-agent's own network calls, batchable via "approve
  all pending from this node" but still the heavier queue of the two.

## Gap found while grounding this: row legibility

`ui/tree.py:161` labels every root row `rec.agent_type or "session"` — a category,
not the task. The actual task text is only shown in a hover tooltip
(`tree.py:170`); the visible topic column is the mechanically tool-derived one
(§2.6 — "reading X", "WebSearch: Y"), not the research question. Run four parallel
research angles and the tree shows four rows all labeled **"session"**,
indistinguishable without hovering each one — which undercuts design goal #1
("legible at a glance") in exactly this use case, not as a hypothetical.

Candidate fix: fall back the row label to a clipped `rec.task` when `agent_type` is
`None` (root sessions only; sub-agents already get a real `agent_type` from the SDK).
Small and contained — but **holding off** until the in-flight UI revision lands,
since it may touch the same row-rendering path and there's no reason to land a fix
that gets immediately superseded.

## Known risk, not blocking

`max_budget_usd` is never passed in `AgentSession._options()` (`driver.py:545`) and
cost is collected but not shown anywhere in the UI (dogfooding note, confirmed by
reading, not just recalled). Low-stakes for a handful of parallel sessions; the
scenario where this actually bites is a coordinator fanning into several sub-agents
each doing repeated search with no visibility and no hard stop. Worth wiring before
a heavy coordinator run, not before this doc's default (parallel) mode.

## Initiation checklist

**Parallel:**
1. Decompose the question into orthogonal angles by hand.
2. One LAUNCH submission per angle — distinct task text (the only reliable
   at-a-glance identifier until the row-label gap above is resolved), cwd chosen
   deliberately (defaults to `.`, which is wrong unless the research is about this
   repo), model sized to the angle's difficulty.
3. Set cap ≥ angle count if all should run concurrently; otherwise let the rest
   queue and close finished sessions to backfill.
4. Expect only `WebFetch`/`WebSearch` (+ `Write` for note-taking sessions) in the
   review queue.

**Coordinator:**
1. Confirm the angle actually can't be split up front — this is the bar for
   escalating, not "it involves a lot of searching."
2. Prompt the coordinator to state its decomposition in prose *before* spawning
   anything — free (no tool call, no approval), and lets the operator redirect
   before any spawn-approval is spent.
3. Expect drip-fed, heavier approval traffic and no live sub-agent output; batch
   "approve all from this node" rather than approving spawns one at a time.
4. Consider wiring `max_budget_usd` first if the run is expected to be long.

## Open questions

- Row-label fix: land now, or wait for the in-flight UI revision? Leaning wait —
  see Status above. **Answered: land now, in a different shape.** The successor
  doc has the reasoning.
- Whether `max_budget_usd` should be exposed per-session from the LAUNCH pane
  rather than hard-coded, once it's wired at all. **Answered: no** — see the
  successor doc.
- No real run has happened yet under this plan. Treat the checklists above as
  untested until one has.
