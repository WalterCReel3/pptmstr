# Research sessions, re-read against the inbox layout

**Dated:** 2026-08-10 · **Status:** decided; supersedes the UI-dependent half of
`2026-08-09-research-session-initiation.md` · **Follows:**
`2026-08-10-layout-proposals.md`

The 2026-08-09 doc recorded its own dependency: *"Depends on: a UI revision
proposed in a separate session — may change the row-legibility fix noted below
before it's worth building. Re-read that proposal against this doc before touching
`ui/tree.py`."* That proposal has since landed as
`2026-08-10-layout-proposals.md` (recommendation: **C, built as A first**). This is
that re-read.

The initiation shapes survive unchanged. What changes is everything the older doc
said about *what the operator will see* — because it was written against seven
panes that mirror the store, and the replacement is one obligation queue.

---

## What carries over unchanged

The decision itself is layout-independent and stands:

- **Parallel independent sessions are the default.** Decomposition is the
  operator's, done before launch, because sessions share no context.
- **Coordinator is an escalation**, for a single angle that genuinely cannot be
  split up front — not a substitute for decomposing.

So does the mechanic that makes it work: `draw_launcher`'s callback is
`launch(task, model, cwd)` (`app.py:339`), so each angle carries its own model and
working directory. `--task`/`--model`/`--cwd` remain a uniform-batch shortcut, not
the only route.

---

## Four things the layout proposal changes

### 1. The row-legibility gap is resolved — and the fix was too small

The older doc found the defect (`tree.py:161` labels every root row by
`agent_type`, so four parallel angles render four rows reading **"session"**) and
held off on a fix pending this proposal. That was the right call, and for a
stronger reason than "it might get superseded": **the proposed fix was wrong in
shape.**

The old candidate was a fallback — use `rec.task` *when* `agent_type` is `None`.
The layout mock reached the same defect from the other side and stated it as a
rule (finding 1, and again at N=20 as finding 6):

> The card's first line must be the task, not the node name. … Session title
> identifies; `project / sub-agent` qualifies.

Task is not a fallback for a missing category, it is the identity. That
generalises where the fallback does not — which is exactly why finding 6 had to
be discovered a second time, on the inbox, after the card fix had already landed
in the mock.

For this codebase the two collapse conveniently: `_subagent_start` sets a
sub-agent's `task` to its `agent_type` (`driver.py:421`), so **label = task,
qualified by `agent_type`** is correct for roots and sub-agents alike, with no
branch on `parent is None`.

**Decision: land it in `tree.py` now, ahead of A.** The older doc's open question
("land now, or wait?") was posed against an in-flight proposal; now the proposal
is planned-but-not-started, and waiting means blocking every parallel research run
on a layout rebuild that has not begun. The line is throwaway either way — A
replaces this pane — but it is one line, and the use case that needs it is the one
being initiated.

While in there: `tree.py:116-119` still tells an empty pane that *"agents cannot
yet be started from here"* and prints the `--task` invocation. The LAUNCH pane
landed in `d6f0c96`. That text is now false, and it is the first thing a new
operator reads.

### 2. "Expect only `WebFetch`/`WebSearch`" understates the queue

The older doc's approval-surface analysis counts approvals. Defect 1 of the layout
proposal is that approvals are only one of three obligation kinds, and finding 5
records the same mistake being made *inside the fix for it*:

> The habit of treating an approval as the only kind of obligation is in the
> fingers.

Parallel research is the sharpest instance of that habit's cost, because of how
`2026-08-10-conversational-sessions.md` changed the lifecycle. A research session
that finishes its angle does not go `DONE` — it goes `AWAITING_INPUT` and sits
connected. So:

> **N parallel angles produce N obligations that no counter currently counts, and
> they arrive at the end, all at once, when the operator has stopped watching.**

The status bar will read "nothing awaiting review" (`app.py:255`) with four
finished research sessions waiting to be read and closed. That is defect 1
reproduced with the worst possible timing.

Corrected expectation for a parallel run: `WebFetch`/`WebSearch` (plus `Write` for
note-taking angles) *during*, then **one `AWAITING_INPUT` obligation per angle**
at the end, plus any `FAILED` angle — which today surfaces nowhere at all.

### 3. `cwd` is now the spatial key, and it does not reach the store

The older doc treats cwd as a per-angle correctness detail: *"defaults to `.`,
which is wrong unless the research is about this repo."* Under A it is more than
that — it is the axis the rail is grouped by, so the choice becomes visible
structure rather than a subprocess argument. Two consequences:

**Leaving cwd at `.` is now actively misleading, not merely wrong.** Four research
angles about unrelated subjects collapse into a single lane headed `PPTMSTR`,
sitting beside this repo's own working sessions. Pick a cwd per angle, or one
shared scratch directory for the whole run, and the rail becomes the decomposition
made visible.

**The prerequisite is understated.** The layout proposal says:

> A project key. Derive from `cwd` — enclosing git root, falling back to basename.
> Presentation-level derivation is enough; no new store entity is needed yet.

Correct about the entity, incomplete about the plumbing: `cwd` is held on
`AgentSession` (`driver.py:385`), and neither `AgentSpawned` (`intents.py:37-51`)
nor `AgentRecord` (`model.py:238-273`) carries it. Nothing on the UI thread can
see it. `scripts/mock_cards.py` renders a `project` field it fabricates
(`mock_cards.py:93`), which is why the gap did not show up in the mock. A field on
the intent and a field on the record are needed before any derivation can happen —
small, but it is store work, not presentation work, and it gates the project axis.

### 4. Reap has no surface in A, and research is the use case that needs one

The older doc's step 3 — *"close finished sessions to backfill"* — is load-bearing,
because the cap became a live-resource limit: `_run` frees a slot only when
`run()` returns, and `run()` no longer returns on its own, so queued angle 5 starts
when angle 1 is **closed**, not when it finishes (`pool.py:78-89`, `pool.py:102-116`).
Verified in the source, not assumed.

The layout proposal names *Reap* as loop step 5 and then gives it a home only in
option B, as `[close]` in the HEALTH pane. In A, an `AWAITING_INPUT` obligation's
in-place affordance is *"a composer for a question"* — which is right for steering
and wrong for research, where the dominant resolution of a finished angle is to
read it and close it, not to reply.

**Requirement this use case generates:** an `AWAITING_INPUT` row in A's inbox
expands to a composer **and** a close action. Without it, working a research
backlog under A means answering every angle in the inbox and then leaving the
pane to reap each one — and a queued angle 5 never starts, for a reason the
inbox does not show.

---

## Coordinator mode under A

Better served than parallel mode, largely by accident of A's identity scheme:

- Sub-agents are **pips on the parent's card, not cards of their own** (mock
  finding 3's density classes are per session). The rail therefore scales with
  sessions, not with agents — which is what keeps a coordinator fanning into six
  sub-agents from consuming the rail.
- Its heavier approval traffic is drip-fed into one queue with `project /
  sub-agent` qualifiers (finding 6), so the coordinator's spawns and its
  sub-agents' network calls are distinguishable without hovering anything. That is
  a real improvement on today's flat `REVIEW` list.
- "Approve all pending from this node" survives as the batching lever; the
  single-cursor rule means the node it applies to is the one under the cursor,
  with no second selection to disagree with it (defect 2).

Unchanged from the older doc: sub-agent output still does not stream (§2.5.1,
confirmed empirically), so A's context pane is blank for a running sub-agent no
matter which layout is chosen. Worth saying plainly rather than letting it read as
a layout bug.

---

## `max_budget_usd`: the older doc's open question, answered

The older doc flagged the risk and asked whether budget should be exposed
per-session from the LAUNCH pane. Still true and re-verified: `_options()` does not
pass `max_budget_usd` (`driver.py:544-560`), and no widget reads `UsageRollup`.

The layout proposal's prerequisite 4 delivers the *visibility* half — usage
surfaced next to context as a separate axis, per §2.4. That downgrades the risk
from an invisible runaway to a visible one. It does not add a stop.

On the open question: **no.** A's launcher is deliberately *"a single omnibox line,
always present, never a tab"* — a budget field is precisely the sort of thing that
turns an omnibox back into a form. A per-session budget belongs in FOCUS/HEALTH,
next to the spend figure it constrains and the interrupt button that is the manual
version of it. Wiring the option at all is still the gate for a heavy coordinator
run, and still not a gate for the parallel default.

> **Amended 2026-08-10** (`2026-08-10-launcher-as-a-modal.md`): the launcher is now
> a modal, so the "turns an omnibox back into a form" premise is gone. The
> conclusion is unchanged and now rests on the rest of the paragraph — a budget
> belongs next to the spend figure it constrains.

---

## Revised initiation checklist

**Parallel (default):**

1. Decompose the question into orthogonal angles by hand.
2. One LAUNCH submission per angle — distinct task text, **a deliberate cwd per
   angle or one shared scratch directory** (this is the rail's grouping key, not
   just where the subprocess runs), model sized to the angle.
3. Set cap ≥ angle count if all should run concurrently; otherwise let the rest
   queue and **close** finished angles to backfill — finishing does not free a slot.
4. Expect `WebFetch`/`WebSearch` (+ `Write` for note-taking angles) while running,
   then **one obligation per angle at the end** that no counter reports. Until
   `needs_you` exists, sweep the tree for `YOUR TURN` badges rather than trusting
   the status bar.

**Coordinator (escalation):**

1. Confirm the angle genuinely cannot be split up front.
2. Have it state its decomposition in prose *before* spawning — free, and
   redirectable before any spawn-approval is spent.
3. Expect drip-fed approvals and no live sub-agent output; batch per node.
4. Wire `max_budget_usd` first if the run will be long.

---

## Build order this implies

**All four landed 2026-08-10**, along with C's mode switch. The requirement in §4
is built: an `AWAITING_INPUT` row expands to a composer *and* `close`, with the
reason stated on the row. See "What was built" in `2026-08-10-layout-proposals.md`.
What follows is kept as the reasoning for the ordering.

Ordered by what unblocks a real research run soonest, not by size.

1. **Task-as-label in `tree.py`**, plus the stale empty-state text. Unblocks
   parallel runs today; throwaway when A lands, and cheap enough not to care.
2. **`cwd` onto `AgentSpawned` and `AgentRecord`.** Store work, gates the project
   axis, survives A. Nothing else in the layout plan can start the project axis
   without it.
3. **`Snapshot.review_queue` → `Snapshot.needs_you`** (layout prerequisite 1). The
   fix for the end-of-run blind spot in §2, and the thing every count must read.
4. **A, with close on `AWAITING_INPUT` rows** (§4). Not a separate item — a
   requirement A must be built with, or working a research backlog under A is
   worse than working it under the current panes.

Steps 1 and 2 are worth doing before the next parallel run. Steps 3 and 4 are the
layout plan's own order, with one requirement added.

---

## Still open

- **No real run has happened under either doc.** Both checklists remain untested.
  The end-of-run obligation pile-up in §2 is derived from the lifecycle change and
  the counter's source, not observed — it is the first thing a real run should be
  watched for.
- Whether one shared scratch cwd or one per angle reads better in the rail. Only
  a run answers this; `--sessions`-style mocking cannot, because the mock invents
  its own project field.
- `format_elapsed` (`widgets.py`) and the corrected `_waited` (`review.py`) are
  uncommitted working-tree changes. A's *"oldest 4m"* status line and the inbox
  wait column both inherit that arithmetic, so it wants committing before either
  is built on.
