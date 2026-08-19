# A card is an agent, and a session is what collapses

**Dated:** 2026-08-13 · **Status:** built 2026-08-15, `380dfe1` ·
**Supersedes:** §5 of [`2026-08-13-sub-agents-are-invisible-while-they-work.md`](../2026-08-13-sub-agents-are-invisible-while-they-work.md)

The earlier note argued against free-standing sub-agent cards on three grounds: that
the rail's value is that position never moves, that a sub-agent card would be
one-third blank and one-third wrong, and that the dossier already lives in HEALTH.

**Two of the three do not hold.** The blank/wrong table was wrong in two of its three
rows, and both errors were defects mistaken for structural absences — which inverts the
argument, because a card is precisely what would have made those defects visible. The
stability argument survives in weaker form and is answered by a collapse rule.

The reframe that does the work: **a card stands for an agent, not a session.** A
session is then not a card but a *group* — always top-level, always present, collapsed
by default.

## What a sub-agent card can actually carry

| slot | status | evidence |
|---|---|---|
| role label | available | `agent_type` on the record; `role_of` for the lead |
| state badge | available | `state` on the record |
| spend | **measured: real data**, misrouted only by §3 | `UsageAccrued(node, …)` `driver.py:258`, accrued `store.py:229`; wire confirmed below |
| model | **one-line defect** | `Role.model` exists (`templates.py:62`) and is passed as `role.model or "inherit"` (`driver.py:772`), but `AgentSpawned` hard-codes `model=self.model` (`driver.py:604`) |
| topic | available and correct | `SubagentProgress`; store guards it against overwriting a parked state (`store.py:204-221`) |
| context ring | **not obtainable** | below |

Four of the five capabilities the proposal wants are available today or one line away.
The earlier note called model and spend structural absences. They are not; the model
field is *positively wrong* rather than merely empty, and per-node usage is real data.

## Context is the one slot a card cannot deliver by rendering harder

Fresh read of the installed SDK (bundled CLI 2.1.226, `_cli_version.py:3`):

- `get_context_usage()` takes no arguments — no agent, no scope (`client.py:510`).
- It is one control request with no scoping field (`_internal/query.py:772-774`).
- The response is session-scoped by its own docstrings — `apiUsage` is "for the
  session" (`types.py:788-789`, `:824-825`).
- `SubagentStartHookInput` carries `agent_id` and `agent_type`, nothing else
  (`types.py:383-388`).

There is one `ClaudeSDKClient` per session (`driver.py:971`), one CLI process per
client, one context window per process. **A sub-agent has no client to ask.**

**A trap worth recording, because it will be walked into.** `client.py:526` advertises
`'agents': Per-agent token breakdown`, which reads exactly like the wanted thing. It is
not: the TypedDict defines it as the cost of the *agent definitions loaded into this
context* (`types.py:797-798`), sibling to `memoryFiles` and `mcpTools`. Implementing
from that docstring ships a plausible wrong number.

### What the card renders in that slot

**Omit the ring; do not draw a hollow one.** `context_cell(None)` draws an empty ring,
`"--"`, and the tooltip "context not yet polled" (`widgets.py:317-318`, `:353-355`). On
a sub-agent that tooltip is a lie — "not yet" promises a value that will never arrive —
and an empty ring reads as "0% used, healthy", the opposite of a gap.

Spend the freed width on **topic**, which is per-sub-agent, correct, and is the field
that actually answers "what is `investigator` doing right now".

If a real reading is wanted later there is exactly one sound path: the sub-agent's own
JSONL. `SubagentStop` hands over `agent_transcript_path` (`types.py:362`, read by
nothing in pptmstr) and the SDK exports `list_subagents` / `get_subagent_messages`
(`_internal/sessions.py:1281`, `:1323`), whose raw message dicts carry `usage`. That is
a new filesystem ingestion path with a poller and an offset cursor, off the frame path
— correct by construction rather than by join, and a larger commitment than any
rendering change here. Deriving occupancy from the accumulating `UsageAccrued` instead
is unsound: occupancy is a last-value not a sum (`store.py:233`, `model.py:102-111`),
and the ring needs a threshold that only holds when the sub-agent runs the parent's
model — which `Role.model` exists to make false.

## The stability argument, corrected, and the collapse rule that answers it

The earlier note claimed the rail's position never moves. **In its own committed code
that is already false as a pixel claim.** `_LINES` has four classes and cards change
class constantly: `active` 2.0 → `blocked` 3.0 when an approval parks → `ended` 1.0 when
it finishes (`rail.py:49`, `:93-98`). Every transition moves every card below it,
across project boundaries. There is also no `ListClipper` in `rail.py` — the only two in
the codebase are `inbox.py:274` and `transcript_pane.py:334` — so the fixed-height rule
buys the vertical budget, not a clipper.

What actually holds is **ordering**, and that motion is paced by the operator's own
queue rather than by the model. That is the real invariant, and the collapse rule
preserves it exactly: it makes model-paced motion opt-in per session.

### The rule

- **Collapse state lives in `RailState`** (`rail.py:59-63`) as `expanded: set[str]`
  keyed by session id — only sessions expand. Presentation state, never the store
  (STYLE.md; precedent `detail.py:126`, `transcript_pane.py:164`). Prune ids absent
  from `snap` at the top of `draw()`.
- **New sessions default to collapsed.** Expanded-by-default re-flows the map at the
  model's pace without being asked, which is the thing being removed.
- **Never auto-collapse when sub-agents end.** Same reasoning as `OnNode.pinned`
  (`focus.py:44-49`): yanking someone off what they just opened is model-paced motion
  wearing a tidiness costume.

### The condition, which contradicts the earlier note

Collapsed height must not depend on sub-agent count. Today it does at the 0→1 boundary
(`blocked_subs` 3.8 vs `blocked` 3.0). Fold that class away.

**Expanded height must be sized from `len(projects.subagents_of(...))`, which is
append-only** (`projects.py:103-109` applies no state filter, and nothing reaps terminal
subs). That makes expanded height *monotone* — it steps at most `cap` times over a
session's life and never shrinks, and order within the expansion never changes because
`snap.order` is append-only.

If the expansion instead renders only non-terminal subs — **which is exactly what §5
step 2 of the earlier note recommends for the pip row** — the expanded card oscillates
at sub-agent lifetimes and the stability problem returns *inside* the card. Filtering
inside a fixed-height pip row is free; filtering inside a variable-height card costs
stability. **The two rules must differ**, and the earlier note records only one.

Note the coupling this creates: the monotonicity argument holds only while nothing
reaps terminal sub-agents. If §2 of the earlier note were ever implemented as
`AgentRemoved` rather than `AgentFinished`, this stability property collapses with it —
a second, independent reason `intents.py:190-198` should be obeyed.

## Selection, costed

**Sub-agent selection already happens today, and it already renders blank.** Cards are
not required to reach it: `focus.obligation` → `focus.node` returns the obligation's own
node (`focus.py:98-100`), obligations carry sub-agent node ids (`driver.py:566-568`,
used at `:643`), and `app.py:501-503` hands that node to `transcript_pane.draw`, which
reads `record.transcript` (`transcript_pane.py:226-234`).

Every sub-agent record holds a fresh empty `Transcript` minted by the store
(`store.py:177`, because `AgentSpawned` passes none at `driver.py:598-608`), while
`Translator._assistant` writes text, reasoning and tool calls into `self.transcript`
**unconditionally** (`driver.py:243-254`) — the session's — even though it computes the
correct node for the intents at `:229`.

`intents.py:61-65` names this exact hazard as one the design avoids:

> letting the store build its own would give the UI an empty buffer while the driver
> filled an orphan

The sub-agent path walks into it. Two live consequences, today, with no card design at
all:

1. Selecting a sub-agent's parked approval in the inbox **blanks the TRANSCRIPT pane**
   with no message saying why.
2. A root's transcript is an unmarked interleaving of its own words and its
   sub-agents'. Composed with §3 of the earlier note, this is much of why a team run
   reads as "the root did everything".

**Fix:** a `dict[NodeId, Transcript]` on `Translator` (beside `driver.py:172`), routing
in `_assistant`/`_user`/`_stream`, a `Transcript` minted in `_subagent_start` and passed
on `AgentSpawned`, republished alongside `_sync_subagent_map` (`driver.py:938-943`) —
the mechanism the root already uses at `driver.py:840`. Roughly 30 lines in `driver.py`,
none in `store.py`.

This is **work that should happen regardless of the card design**, so it is not a cost
of the enhancement. It is an existing defect the enhancement exposes.

The rest of selection is small. `focus.to_node` matches on session id (`focus.py:189`),
justified at `:186-188` by "a card stands for a whole session" — the premise this note
changes. New rule: `node[1] is None` → session-wide (unchanged); otherwise that node's
own obligations, else pin. About four lines plus a docstring rewrite.
`tests/test_focus.py:203-218` stays green and the new branch is uncovered, so it needs a
test. **The session card must keep session-wide matching**, or clicking a parent whose
sub-agent is parked stops reaching the parked call — the common case per
`rail.py:210-213`. DETAIL degrades correctly rather than wrongly: the approval branch
works, `_board` keys on session, and `_nothing_selected` (`detail.py:213-243`) renders
an empty narration.

## What the collapsed row shows

The earlier note's §1 fix becomes a **prerequisite, not an alternative**. If collapsed
is the default and collapsed suppresses any sub-agent signal for working sessions
(`rail.py:284-286` returns before `:310`), the fleet is invisible exactly as reported
*and* nothing tells the operator which card is worth expanding — the affordance would
have no signal pointing at it.

Prefer **a count plus the worst state colour** (`▸ 3 subs · 1 waiting`) over the pip
row. It is genuinely fixed-width and immune to the spill the pip row has today
(`rail.py:315-322` is one unwrapped `same_line()` run with no `ellipsis`, no clip and no
width bound, while every other string on a card is measured). It loses per-sub state,
which is now correct: once expansion exists, the collapsed card's job changes from
"what is each one doing" to "is there anything in here".

`scripts/mock_cards.py` must be fixed in the same change or have its card path deleted
in favour of importing the real one.

## Ordered cost

| | work | why here |
|---|---|---|
| 0 | Per-node transcript routing in `driver.py` (~30 lines) | Live defect; blocks any honest sub-agent selection |
| 1 | Collapsed-row signal for working sessions, bounded | Prerequisite: without it the collapsed default is blind |
| 2 | `Role.model` on `AgentSpawned` (`driver.py:604`) | One line; the record currently lies |
| 3 | `RailState.expanded`, collapse rule, monotone expansion sizing | The card design proper |
| 4 | `focus.to_node` exact-node branch + test | ~4 lines |
| 5 | §3 `tool_use_id` join | Spend and topic on every card ride on it |
| — | Per-sub-agent context ring | Not buildable from the API. Omit the slot |

## Measured

`scripts/verify_subagent_usage.py`, two live runs (sonnet-5, `$0.34` and `$0.21`).
Two of three questions are settled; the third resisted, and the reason is itself a
finding.

**Sub-agent `usage` IS populated on the wire — question settled, spend is real data.**
Both runs, both sub-agents, every message:

```
subagent(parent=toolu_014nzPS…): {input_tokens: 4, output_tokens: 21,
                                  cache_creation: 17608, cache_read: 16020,
                                  messages_with_usage: 2}
```

So per-sub-agent spend is data, not plumbing, and the card's spend slot is sound the
moment the §3 join is. This was the premise everything else rested on.

**`last_assistant_message` IS present and non-empty — `driver.py:621` is fine.** The
installed SDK's `SubagentStopHookInput` TypedDict (`types.py:356-363`) is simply
incomplete; the wire carries fourteen keys where the type declares five, including
`effort`, `background_tasks` and `session_crons`. **The TypedDict is not a reliable
inventory of the payload** — worth remembering the next time one is read as a
capability list, which is how the context-slot question nearly went wrong.

**`agent_transcript_path` is real and points at a live per-sub-agent JSONL:**

```
~/.claude/projects/<project>/<sessionId>/subagents/agent-<agentId>.jsonl
```

That upgrades the context/transcript ingestion path from "the SDK exports something
that looks usable" to a confirmed file per sub-agent, arriving on every `SubagentStop`
and read by nothing in pptmstr.

**Concurrent spawn dispatch: still unmeasured, twice.** Both runs printed
`SERIALISED`, and both times that verdict was an artifact — the model issued each
`Task` in its *own* assistant message:

```
spawn tool_use blocks grouped by assistant message:
  [['toolu_014nzPS3ZYQSjy2MjwRhcaBK'], ['toolu_01QFAwjpV2SJEVDz5Ccy5zVv']]
  -> the model issued each spawn in its OWN message. Serialised hook order proves
     nothing here; the concurrent case was never presented.
```

The first run lacked this check and its `SERIALISED` line was believed for as long as
it took to add one. **A prompt cannot reliably compel batched tool calls**, so
settling §3's cross-attribution needs either a captured real team run (this repo's own
lead spawns roles and is the natural fixture) or a synthetic two-block message
injected at the translator. Until then §3 has one defect that is certain (the
deny-path stale slot, which needs no concurrency) and one that remains a hypothesis.

**A negative finding that closes an option.** The previously-suggested fallback join
key — the second callback parameter the driver ignores at `driver.py:570-571` — does
not work. On `PreToolUse` it equals the spawn's `tool_use_id`, but on `SubagentStart`
it is an unrelated UUID:

```
PreToolUse     tool_use_id=toolu_014nzPS…  callback_tool_use_id=toolu_014nzPS…
SubagentStart  agent_id=a7fd576f01af18593  callback_tool_use_id=624da603-5f31-…
```

It is a hook-invocation id, not the spawn's. Joining on `agent_type` remains the only
alternative to the adjacency slot.

## Verification boundary

**Executed:** `scripts/verify_subagent_usage.py`, twice, live. It settles sub-agent
`usage`, `last_assistant_message`, `agent_transcript_path`, the full `SubagentStop` key
set, and the callback-parameter join. It did **not** settle concurrent spawn dispatch,
and says so in its own output rather than leaving the reader to infer it.

**Everything else is reading.** The SDK claims are fresh source reads of
`.venv/lib/python3.11/site-packages/claude_agent_sdk/`; the pptmstr claims are reads of
the working tree. `driver.py`, `store.py`'s `AgentSpawned` arm, `focus.py`, `rail.py`,
`projects.py`, `widgets.py`, `intents.py` and `app.py` were each opened at the cited
lines. `store.py` and `model.py` are dirty, so their citations describe the working
tree rather than HEAD.
