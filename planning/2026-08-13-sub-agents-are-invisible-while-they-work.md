# Sub-agents are invisible while they work, and unreaped when they stop

**Dated:** 2026-08-13 · **Status:** open, not started ·
**Investigated:** lead + investigator + skeptic, dogfooded on this repo

The operator's report was two claims: that the FLEET rail should promote sub-agents
to full cards joined to a parent by a heavy left rule, and that "bugs are related to
sub-agent management and state." The second is right. The first is a reasonable
response to a **rendering defect** that makes the current design look like it does
nothing, and the redesign should not be judged until that defect is fixed.

The two are not independent, which is the finding that orders the work: a sub-agent
whose progress was routed to the wrong node sits at `SPAWNING` forever, and the pane
that would show it skips its own pip row. **Store first, rail second.**

## 1. The rail cannot draw a pip for a session that owes nothing

Confirmed by execution, not by reading:

```
_density(state=THINKING, owed=[], has_subs=True) -> 'active'
```

`_density` (`pptmstr/ui/rail.py:93-98`) returns `"blocked_subs"` only when the session
has sub-agents **and** owes the operator something. The `"active"` branch returns at
`rail.py:284-286` and `"ended"` at `:230-232`, both before the `if subs:` pip block at
`:310-323`. So a root that is *working* with three live sub-agents draws no pips at
all. Sub-agents appear on the rail only at the instant one of them parks on the
operator.

That is the operator's stated symptom, mechanically, and it is control flow in one
function — not evidence that pips are the wrong representation.

`tests/test_inbox_rail.py:136-138` does not catch it: it passes an obligation in and
asserts on `_LINES`, so it tests the height table rather than the draw path.

**The fix must land twice.** `scripts/mock_cards.py` does not import `pptmstr.ui.rail`
at all — it is a parallel reimplementation (`draw_card`, `:518`) with its own density
table at `:531`, and it carries the same defect (active returns `:628-630`, pip block
`:655`). The fixture that would have shown this reproduces it instead. Its
`--view empty` path (`:1061-1070`) rewrites every sub to `RUNNING_TOOL` and clears
`blocked` — an existing checked-in view that renders the bug, captured by someone who
did not notice the pips had gone.

## 2. Nothing terminates a sub-agent whose parent stopped listening

`_await_subagents` (`driver.py:945-961`) returns after `SUBAGENT_GRACE_S = 120` with
`self._subagents` non-empty, logs `"N sub-agent(s) stopped reporting"`, and **emits no
terminal intent**. Session teardown names only the root (`driver.py:898-915`). So those
nodes keep whatever non-terminal state they held, forever.

`any_active` scans all nodes (`store.py:470`) and gates idling (`app.py:750-751`), so
one orphan pins `enable_idling = False`: the app spins at full frame rate with nothing
visible running. Composed with §1, that is the whole reported experience — *awareness
lost, and the machine still working*.

**The fix is `AgentFinished` for the survivors, not `AgentRemoved`.** `AgentRemoved`
is emitted nowhere in the application (only `scripts/bench_idle.py:148-150`), and
`intents.py:190-198` records that as deliberate: a finished node can be woken by a
sibling, so pruning an id another agent still holds leaves resumed work with nowhere
to land. Emitting it here would regress the wake path. The node should end, not vanish.

## 3. The `tool_use_id` join binds the wrong sub-agent, and needs no parallelism to do it

Two attribution paths exist and only one is sound. Approvals read `agent_id` straight
off the hook (`driver.py:643-647`, `_node_for` at `:566-568`) and are fine. Progress,
message attribution and therefore `UsageAccrued` route through `subagent_by_tool_use`
(`driver.py:385-397`, `:196-207`, `:257-258`), built from a **single-slot** heuristic:

```python
if tool_name in ("Agent", "Task") and not agent_id:      # driver.py:646-647
    self._last_spawn_tool_use_id = str(data.get("tool_use_id", "")) or None
```

The comment at `driver.py:500-506` justifies this by adjacency — a `PreToolUse` for
`Agent` is immediately followed by `SubagentStart`. **That is false by construction.**
`Agent`/`Task` are in `_REVIEW` (`approval.py:62-63`, `:96-99`), so the gate sits in
that gap with `APPROVAL_TIMEOUT_S = 6 * 60 * 60` (`driver.py:78`) and a human in it.

The slot is written at `:646` *before* `classify` at `:649`, and is cleared only at
`:592`. So the deny return (`:745`) and the cancel re-raise (`:719`) both leave it set.
And the binding block at `:590-592` runs **before** the `if resumed:` early return at
`:594`, so a *resumed* sub-agent re-binds `_spawn_tool_use[agent_id]` to whatever stale
id is sitting there. A denied Agent call followed by a wake attaches a dead
`tool_use_id` to a live sub-agent, whose real messages then miss the map and fall back
to the root — the defect `tests/test_driver.py:511-515` already records as observed
live, reached by a path that requires no concurrency at all.

Under genuinely parallel spawns it is worse: one sub-agent is billed another's tokens
and narrates its work. That case still rests on one unmeasured premise — whether the
CLI dispatches two `Agent` `PreToolUse` hooks concurrently. `model.py:364-368` measures
this for `PreToolUse` generally ("three at once, measured") but not for `Agent`, which
is special precisely because it parks.

**Measured since, by `scripts/verify_subagent_usage.py`** — see
[`2026-08-13-a-card-is-an-agent.md`](2026-08-13-a-card-is-an-agent.md) for the output:

- The second callback parameter (`driver.py:570-571`) is **not** a usable join key. It
  equals the spawn's `tool_use_id` on `PreToolUse` but is an unrelated UUID on
  `SubagentStart` — a hook-invocation id, not the spawn's. That option is closed.
- Concurrent dispatch is **still unmeasured after two runs**: the model issued each
  `Task` in its own assistant message both times, so the concurrent case was never
  presented and the `SERIALISED` verdict both runs printed is an artifact. A prompt
  cannot reliably compel batched tool calls; this needs a captured real team run or a
  synthetic two-block message at the translator.

So the deny-path/resume stale-slot defect above is **certain and needs no
concurrency**; the cross-attribution case remains a hypothesis. Joining on `agent_type`
is the only remaining alternative to the adjacency slot — exact except for two
sub-agents of the same type, already the documented ambiguity in `_roles`
(`driver.py:511-515`).

**Test coverage:** the *missing* binding is documented (`tests/test_driver.py:536-546`).
The *wrong* binding is covered by nothing; no test drives `_pre_tool_use` for an Agent
call, so the join is never exercised.

## 4. `Bridge.park()` has no unpark, and it spends the watchdog

Only `resolve()` (`bridge.py:213`) and `fail_all_pending()` (`:294`) remove from
`_pending`. The gate's cancel path (`driver.py:711-719`) emits `ApprovalResolved` and
re-raises without touching the table, so `parked_count` stays permanently above
`len(snap.approvals)` — exactly the inequality `_check_for_lost_approvals` treats as a
hang (`app.py:254-275`). It logs an error and latches the red banner
(`app.py:413-419`) about agents that are fine.

The cost is not the false alarm. `app.py:259-262` never resets, so the one watchdog
guarding a genuinely invisible hang is **dead for the rest of the run**. Trigger is
rarer than it first looks — teardown goes through `fail_all_pending` — so the live
path is the 6-hour timeout. `tests/test_gate.py:193-209` walks it and asserts the store
half only: one assertion short.

Not sub-agent-specific (a root's cancelled approval does it too), but reached mostly
through sub-agents, since `rail.py:210-213` records that a sub-agent parked on approval
under a thinking root is the common case.

## 5. On the proposal: keep the pips, fix the draw, take the left rule

> **Superseded on the same day by
> [`2026-08-13-a-card-is-an-agent.md`](2026-08-13-a-card-is-an-agent.md).** The
> operator's reply reframed the card as an *agent* rather than a session and answered
> the stability objection with a collapse rule. Two of the three "structurally wrong"
> slots below turned out to be defects rather than absences. §§1-4 above stand
> unchanged and are still the work to do first; this section is kept because the
> arguments that survived are load-bearing in the successor, and because the two rows
> it got wrong are the reason the successor exists.

Free-standing cards per sub-agent is the one variant to argue against, and the reason
is not cost.

The rail's whole claim on screen space is that **position never moves** — "stable
spatial order... never re-sorted. A card grid earns its space only if position is
stable enough to build muscle memory" (`rail.py:5-9`, enforced in
`projects.group_roots`, pinned by `tests/test_projects.py:112-130`). Sub-agents are the
least stable objects in the model: several per turn, lifetimes of seconds. A card each
means the map re-flows constantly and **every card below the insertion point moves,
including cards in unrelated projects.** A pip re-flows inside one fixed-height row and
nothing below it moves. That is a structural difference, not one of degree.

Three of a full card's slots are also structurally wrong for a sub-agent:

| slot | why it cannot work |
|---|---|
| context ring | `ContextPolled` fires only for `self.node_id` (`driver.py:966-983`) — always `None` |
| model | hard-coded to `self.model` at spawn (`:604`) while `:772` lets a role carry its own — can be positively wrong |
| spend | rides the join in §3 — wrong exactly in the multi-sub case the proposal serves |

A card would render a confident dossier that is one-third blank and one-third
unreliable.

**Not** an argument against the proposal, and recorded because it was raised and
refuted: the "concurrency_cap defaults to 4, so this is only ~8 extra cards" line does
not hold. The cap bounds concurrent root sessions, not sub-agents (over-cap sessions
queue, `README:109`) and is settable (`app.py:703`); `projects.subagents_of`
(`projects.py:103-109`) applies **no state filter** and nothing reaps terminal subs, so
the count grows monotonically for a session's life.

That same fact is a live defect in the pip row today: `rail.py:315-322` is one
unwrapped run of `same_line()` labels with no `ellipsis`, no clip, and no width bound,
while every other string on a card is measured (`:239`, `:278`, `:294`). At a 21%-width
rail (`app.py:604`, `:639`) roughly two or three pips fit before spilling past the card
border.

### What to do

1. **Draw the pip row whenever a session has sub-agents**, not only when it owes
   something. Prefer putting presence on the existing active line (`rail.py:267-283`)
   over a fourth `active_subs` density class: at scale in a team run,
   active-with-subs is the *modal* card, and giving the modal card more height inverts
   the vertical-budget rule the density table exists to enforce (`rail.py:44-48`).
   Assert on the draw path, not on `_LINES`.
2. **Bound the row**: show non-terminal subs, `ellipsis` each label, end with a `+N` /
   `n done` tail.
3. **Take the operator's heavy left rule** — as a 2px bar beside the pip row *inside*
   the card, where the row is already indented (`rail.py:312`). It delivers "these
   belong to this parent" and moves nothing.
4. **Fix `mock_cards.py` in the same change**, or delete its card path in favour of
   importing the real one. A fixture that reproduces the defect it exists to catch is
   worse than no fixture.

### What this does not give, stated plainly

A sub-agent still gets no topic, spend or context of its own on the rail. If the
requirement is "what is `investigator` doing *right now*, without selecting anything",
pips cannot carry it. The honest alternative is then a **fixed-height sub-agent strip
inside the parent card** — one line per sub, capped with a `+N` tail — which costs
vertical budget but still never moves the map.

### Why not the detail pane

`2026-08-12-the-board-has-no-surface.md` argues team state belongs in DETAIL, and that
holds for the board. It does not answer this. Its own baseline is stale twice over: its
opening grep now returns five hits (`pptmstr/ui/board.py:107,113,116,126,154`), and its
"roles appear through the tree that already existed" is not true — there is no tree,
`rail.py:17` records that the widget was dropped, and the surviving substitute is the
pips that §1 suppresses.

More decisively, `health.py:108-118` already lists every sub-agent for the selected
session, and `detail.py` and `board.py` are likewise single-session surfaces. All three
require having already selected the session to learn it has live sub-agents — which
presupposes the awareness the operator says is missing. The rail is the only always-on,
all-sessions surface, so it is the right pane.

## Order of work

1. §4 — the leaked `_pending` entry, because it silently disables the alarm for §2.
2. §2 — `AgentFinished` for grace-window survivors; unpins idling.
3. §1 — draw the pip row, with a draw-path assertion, in both `rail.py` and the fixture.
4. §3 — log the join first and settle the concurrency premise before changing the key.
5. §5 — revisit the strip-vs-pips question only after §1 lands, with a real run on screen.

## Verification boundary

The density result in §1 and the 598-passing suite were executed. **Everything else in
this note is from reading**, by agents with no shell: no test was written to reproduce
§2, §3 or §4, and `mock_cards.py` was not run. `bus.py`, `model.py`, `store.py` and
`tests/test_bus.py` are dirty, so §2's store citations describe the working tree;
`driver.py`, `bridge.py`, `app.py`, `approval.py`, `rail.py`, `projects.py`, `focus.py`,
`health.py` and `mock_cards.py` are unmodified and those citations are against committed
code.
