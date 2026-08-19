# Multi-Agent Orchestrator — Coarse Design (rev. 4)

Design spec for an LLM multi-agent coordinator with an immediate-mode UI, built on the **Claude Agent SDK**. Written to be handed to an implementing agent.

**Stack:** Python · `claude-agent-sdk` for agent execution · `imgui-bundle` (Dear ImGui + hello_imgui) for UI · personal/small-team tool, dev audience.

Read §1 (Invariants) and §3 (Threading) first. §3 is the load-bearing decision in this revision.

> **Changed in rev. 4:** the harness now ships a feature that overlaps this project — **agent teams** — and the response is recorded rather than left implicit. It is not adopted, for one structural reason (§0, "What you are deliberately not adopting"), and its primitives are consumed individually instead. Agents can now talk to each other: §2.7 is new and adds a wake path into the state machine (§2.3). I8 also gains a name — **the parking invariant** (§1) — because the agent-teams decision turns on it and "I8" is not a phrase anyone reasons in. Everything else is unchanged from rev. 3.

> **Changed in rev. 3:** theming moves from non-goal to first-class feature (§6.1). Light, dark, and high-contrast are required; state must be legible without relying on hue. Everything else is unchanged from rev. 2.

> **Changed in rev. 2:** agent execution is no longer hand-rolled — it delegates to the Agent SDK. The approval gate becomes an async SDK callback rather than a custom executor block. The threading model is rewritten to reconcile asyncio (SDK) with a main-thread render loop (ImGui). Context-budget tracking moves from "read it from the API" to "compute it yourself." See §10 for the full diff against rev. 1.

---

## 0. Goals

1. Run N agent sessions concurrently and keep all of them legible at a glance.
2. **Nothing is written until a human approves it.** Approval is a first-class runtime state.
3. Reasoning is surfaced as it streams, not reconstructed after the fact.
4. Runtime facts — context budget, model, sub-agent tree, state — always current, never stale.
5. Idle when nothing is running. Per-frame rebuild in Python is expensive enough that this is load-bearing.

6. **Legible in any lighting and to any operator.** Light, dark, and high-contrast
   themes are required, and agent state must be readable without depending on hue
   (§6.1).

Non-goals: i18n, multi-user, remote access.

### What you are *not* building

The SDK owns these. Do not reimplement them:

- the agent loop, tool dispatch, and retries
- context compaction and prompt caching
- subprocess and session lifecycle
- the permission evaluation pipeline (you supply a callback, not a policy engine)

You are building: the orchestration UI, the approval interaction, the cross-session state projection, the tree/rollup views, and the inter-agent message bus (§2.7).

### What you are deliberately *not* adopting (rev. 4)

The list above is things the SDK does *for* you. This is a different category: a
thing the harness does *instead of* you, incompatibly.

**Agent teams** (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) is a lead session that
spawns named teammates, a shared task list they claim work from, and a mailbox they
message each other through. That is the coordination half of this project, shipped.
Six of the seven things it does are things this project does or intends to.

It is not used, for one reason: **teammates run as separate Claude Code processes
outside our driver and inherit the lead's permission mode wholesale.** Per-teammate
modes cannot be set at spawn; if the lead runs `--dangerously-skip-permissions`,
every teammate does. Their permission prompts surface in the lead session as
ordinary approve/deny prompts — no diff, no queue, no edit-then-approve. Our gate is
an in-process `PreToolUse` callback (§5); a teammate process loads settings-file
hooks instead, so a teammate's `Edit` has no path through the `asyncio.Future` the
operator's decision arrives on. That await *is* the gate — it is what makes **the
parking invariant** (I8, §1: parking is unbounded and free) true — and an agent
whose tool calls never reach it is ungated by construction.

The consequence worth internalising: adopting agent teams would not merely fail to
provide the parking invariant, it would **remove** it. So this is a fork, not a
deferred feature — there is no later state in which we take the task list now and get
the gate back after.

What that leaves is the honest division of labour this project is for. Agent teams
solved coordination. Nobody has solved control: the review queue, the editable
approval, and a parked agent costing nothing. **Effort belongs on the uncontested
half**, which is also the argument for §2.7 routing messages through our own store
rather than leaning on the harness's channel — the routing is a means, the
reviewability is the product.

Its primitives are consumed individually instead (§2.7). Full reasoning and the
list of what was taken: `planning/archive/2026-08-11-agent-teams-vs-pptmstr.md`.

---

## 1. Invariants

| # | Invariant | Why |
|---|---|---|
| I1 | **The store is the single source of truth.** The UI owns no application state. | The IMGUI thesis. A UI-side copy means state sync. |
| I2 | **The UI builds from exactly one snapshot per frame,** taken atomically at frame start. | Otherwise you render torn state — a header saying 5 agents above a tree with 4. |
| I3 | **Snapshotting is O(1).** Records are immutable; the store swaps references. | Deep-copying the world at 60fps in Python is a performance disaster. |
| I4 | **The UI never mutates the store directly.** It appends `Intent` objects; intents are applied between frames. | Keeps mutation off the build path; every state change is auditable. |
| I5 | **The UI owns the main thread. The SDK owns a dedicated asyncio thread.** They communicate through exactly two primitives (§3). | ImGui contexts aren't thread-safe; the SDK is asyncio-native. Neither will yield. |
| I6 | **Every agent node has a stable ID derived from SDK identifiers,** never from list position. | Dear ImGui keys widget state by hashed label. Key off index and reordering scrambles hover, focus, and scroll. |
| I7 | **Transcripts are append-only.** Readers take `(buffer, length_at_snapshot)`. | Immutable-record CoW gives O(n²) rebuilds on token streams. Append-only sidesteps it and is lock-free for readers. |
| I8 · **the parking invariant** | **Parking is unbounded and free.** The approval gate must be able to block indefinitely, and blocking must not stall the UI or any other agent. | You are the bottleneck by design. A parked agent must cost nothing. |

**On the name (rev. 4).** I8 earns a handle because it is cited more than any other
invariant and it is the one a reader is most likely to violate by accident. Call it
**the parking invariant**; the repo already speaks the word (`park`, `unpark`,
`parked` throughout `tests/test_store.py` and the inbox pane). The number stays as
the anchor — `I1`–`I8` are referenced from code comments and tests, and renaming one
of eight breaks the scheme — so both forms are correct: the name in prose, `I8` where
terseness wins.

---

## 2. Mapping SDK concepts onto the store

### 2.1 Identity and the tree

The SDK hands you the tree; don't invent one.

| Store field | SDK source |
|---|---|
| `session_id` | `AssistantMessage.session_id` — stable per session, one subprocess each |
| `agent_id`, `agent_type` | present on subagent messages and hook inputs |
| `parent_tool_use_id` | the `Agent` tool call that spawned this subagent — **follow these to rebuild nesting** |
| `message_id`, `uuid` | per-message identity, useful for transcript segment keys |

```python
NodeId = tuple[str, str | None]   # (session_id, agent_id) — I6 widget key basis
```

Root sessions have `agent_id is None`. Build the tree by following `parent_tool_use_id` back to the `ToolUseBlock` that created the child.

### 2.2 Agent record (immutable)

```python
@dataclass(frozen=True, slots=True)
class AgentRecord:
    node_id: NodeId
    parent: NodeId | None
    depth: int

    state: AgentState
    topic: str                     # "thinking topic", <= 60 chars, see 2.5
    task: str

    model: str                     # from AssistantMessage.model
    usage: UsageRollup             # see 2.4
    pending: PendingApproval | None

    transcript: TranscriptHandle
    started_at: float
    ended_at: float | None
    error: str | None
```

### 2.3 State machine

```
SPAWNING → THINKING ⇄ CALLING_TOOL → AWAITING_APPROVAL → RUNNING_TOOL → THINKING
                                    ↘ (auto-approved) ↗
  → DONE | FAILED | CANCELLED | RATE_LIMITED
```

```python
ACTIVE_STATES = {THINKING, CALLING_TOOL, RUNNING_TOOL}
IDLE_STATES   = {SPAWNING, AWAITING_APPROVAL, DONE, FAILED, CANCELLED, RATE_LIMITED}
```

`ACTIVE_STATES` is the **idle predicate** for the render loop (§4.2). `AWAITING_APPROVAL` being idle is the point of the parking invariant (I8) — agents parked on your review cost nothing, and the whole app drops to idle FPS while it waits on you.

`RATE_LIMITED` is driven by `RateLimitEvent`, which the SDK emits on rate-limit status changes. Surface it; it's the difference between "stuck" and "backing off."

**Rev. 4 — terminal is not always terminal.** Once agents can message each other
(§2.7), a *finished* sub-agent that receives a `SendMessage` **auto-resumes in the
background**, with no new `Agent` tool call. A node can therefore leave a
terminal-looking state without an intent from us and without the spawn path running.
Two consequences: the state machine has an inbound edge into `THINKING` that
originates from a sibling rather than from the operator or the parent, and any UI
affordance that treats a finished row as disposable (prune, collapse, hide) is
wrong for a node whose siblings still hold its name. An agent stopped via the SDK's
`stop_task` is exempt — the send is refused rather than delivered — while one stopped
by the model's own `TaskStop` still wakes.

> **Measured, and it arrives as a second `SubagentStart`** (`scripts/verify_wake_path.py`,
> CLI 2.1.226). The resume reports through the *same hook as a spawn*, carrying the
> agent's original `agent_id` — observed 7s after that agent's own `task_notification:
> completed`, with a matching second terminal notification for the same `task_id` when
> it finished again. `SendMessage` returned
> `{"success": true, "message": "…had no active task; resumed from transcript in the
> background…", "resumedAgentId": …}`.
>
> **So the hook is ambiguous and the store must disambiguate it.** Emitting
> `AgentSpawned` on the second start rebuilds the record: usage zeroed, `started_at`
> reset, `ended_at` cleared *and the `Transcript` object replaced* — which orphans the
> `(buffer, length_at_snapshot)` handle every reader holds under I7, leaving panes
> following that node reading a buffer nothing writes to. This was a live defect
> reachable without any of §2.7 being built, because a lead already has `SendMessage`
> and already receives sub-agent IDs from background spawn results.
>
> The fix is a distinct `AgentResumed` intent: only the liveness fields move
> (`state → THINKING`, `ended_at → None`, `error → None`), while `transcript`, `usage`,
> `started_at`, `parent` and `depth` are untouched. The driver tells the two apart by
> membership of the set it already keeps of sub-agent IDs it has seen.

**Addressing is by agent ID, and a sibling cannot obtain one (rev. 4, measured).**
`SendMessage` refuses a `subagent_type` — *"No agent named 'alpha' is reachable.
Check the spelling, or use the agent ID from a background agent's spawn result."*
The ID appears in the **lead's** context, never in a sibling's, so worker-to-worker
messaging only happens if the orchestrator plumbs the ID into the worker's prompt
(via `updatedInput` on the `Agent` call, which is how the probe achieved a delivery).
That materially weakens the case for `SendMessage` as the channel the model routes
over *without us*: it cannot address anyone we have not already addressed for it.
The bus of §2.7 has no such problem, since it routes on `NodeId` (I6).

### 2.4 Usage, and context as a *health* signal

Two different things get conflated under "budget," and this design keeps them apart
because they drive different decisions:

- **Cost** (`UsageRollup`, `max_budget_usd`) — money. Cumulative, monotonic,
  and genuinely a budget. Interesting at the end of a run.
- **Context occupancy** (`ContextSnapshot`) — **session health, not spend.** It is
  read to answer "is this session about to be compacted, and should I start a fresh
  one instead?" Compaction is the event that degrades an agent: it silently discards
  the reasoning that got it here, and what comes after is measurably worse at tasks
  that depended on it. The number exists to let the operator *pre-empt* that, not to
  ration anything.

Do not present them together. A single "budget" widget mixing dollars and tokens
would imply the operator should economise on context, and the correct response to
high context is nearly the opposite — retire the session while it is still good,
rather than squeeze more turns out of it.

The actionable reference point is therefore `autoCompactThreshold`, not `maxTokens`.
"78% of the window" is trivia; "about 12k tokens before this session compacts" is a
decision. Where the two disagree, show the distance to compaction.

Every message carries a usage block:

```python
@dataclass(frozen=True, slots=True)
class UsageRollup:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    total_cost_usd: float          # SDK's client-side estimate, not authoritative billing
    context: ContextSnapshot | None   # polled, see below — None until first poll
```

> ~~**Constraint discovered in rev. 2:** the SDK does not expose remaining context window... You must maintain a `{model: context_window}` table yourself.~~
>
> **Retracted in rev. 3 — this was wrong.** `ClaudeSDKClient.get_context_usage()`
> returns `ContextUsageResponse`, which carries `totalTokens`, `maxTokens` (effective,
> already reduced by the autocompact buffer), `rawMaxTokens`, a computed `percentage`,
> `isAutoCompactEnabled`, and `autoCompactThreshold` — the same data behind the CLI's
> `/context`. **Do not build the `{model: context_window}` table.** Drop `context_window`
> from `UsageRollup` and carry the response's own numbers.
>
> One caveat survives the retraction: it is a **control request, not a push**. You
> call it, so it is polled state — poll on a slow timer (seconds, not frames) and
> cache into the store like any other fact.

**Compaction is observable, and that is the point.** Rev. 2 said it "fires invisibly."
It does not: `PreCompact` is a hook event carrying `trigger: "manual" | "auto"`.
Register it. That gives, per session:

- a **count** of compactions so far, and when the last one fired;
- the ability to mark the exact transcript offset where context was discarded, so
  the operator can see that an agent's answer came from *after* it lost its reasoning;
- a non-monotonic drop in occupancy that can be *labelled* as a compaction rather
  than looking like a glitch in the bar.

"This session has compacted twice" is the single most useful thing this subsystem can
say. It is a much stronger retire-and-restart signal than any occupancy percentage,
because it reports damage already done rather than damage predicted.

**Derived signal.** Rather than making every panel do arithmetic on raw token counts,
the store carries a coarse `ContextPressure` — `NOMINAL` / `NEARING_COMPACTION` /
`COMPACTED` — computed once where the poll lands. Panels branch on that; the raw
numbers stay available for the detail pane. This keeps the "should I start a new
session?" judgement in one place instead of spread across the UI, and it is the only
form in which the signal belongs in a per-frame render path.

**Policy stays advisory.** Warn, surface, and make "fork this session" a one-click
action; do not auto-retire a session. The operator decides — that is the premise of
the whole tool. `max_budget_usd` remains the only hard stop, and it is about money.

Use `max_budget_usd` in `ClaudeAgentOptions` for a real hard stop; it's enforced by the SDK.

### 2.5 Transcript and streaming

Set `include_partial_messages=True` to get `StreamEvent` alongside complete messages.

```python
class SegmentKind(Enum):
    REASONING, OUTPUT, TOOL_CALL, TOOL_RESULT, ERROR, SYSTEM

@dataclass(frozen=True, slots=True)
class Segment:
    kind: SegmentKind
    start: int; end: int          # offsets into the append-only buffer
    meta: dict

class Transcript:
    """Writer: the SDK consumer task. Readers: UI, lock-free."""
    _buf: bytearray               # append-only
    _segments: list[Segment]
    _published_len: int           # advance LAST, after bytes land
```

Feed it from `StreamEvent.event`:
- `content_block_delta` / `text_delta` → append to `OUTPUT`
- `content_block_delta` / `input_json_delta` → append to `TOOL_CALL` (partial tool args)
- `content_block_start` / `_stop` → open and close segments

> ~~**Two verification gaps to close during step 3.**~~ **Both closed by running it** —
> `scripts/verify_subagents.py`, live sub-agent spawns against the real CLI.
>
> **(a) Reasoning streams.** `ThinkingBlock(thinking, signature)` is real, and
> `thinking_delta` arrives as a `content_block_delta`. Goal #3 holds at the root.
>
> **(b) Sub-agent output does *not* stream. The rev. 2 worry was correct.** Across
> runs, **zero** `StreamEvent`s carried `parent_tool_use_id`. Sub-agent content
> arrives only as complete `AssistantMessage`s. **Goal #3 does not hold below the
> root, and the pane must say so rather than implying live output.**

### 2.5.1 How sub-agents actually arrive (rev. 3, measured)

Enough of this is surprising that it is worth stating flatly.

**The spawn tool is called `Agent` at the hook, while the tool list advertises
`Task`.** Both names must be classified or the gate has a hole exactly where it
matters most. Confirmed: `PreToolUse tool_name=Agent`.

**Sub-agents run as background tasks that outlive the turn that spawned them.**
`receive_response()` returns at the *parent's* `ResultMessage`, which is before the
sub-agent finishes — a consumer that stops there reports "no sub-agent activity"
when the truth is that it stopped listening too early. That was the first run's
false negative. Alongside this come `TaskStartedMessage` / `TaskProgressMessage` /
`TaskUpdatedMessage` / `TaskNotificationMessage`.

**There are two identifiers for one sub-agent, and no direct join:**

| carries | identifier |
|---|---|
| `SubagentStart` / `SubagentStop`, and `PreToolUse` *inside* the sub-agent | `agent_id` |
| `AssistantMessage`, `StreamEvent` | `parent_tool_use_id` (the `Agent` call's id) |
| `TaskStarted` / `TaskProgress` / `TaskNotification` | `task_id` **and** `tool_use_id` |

`SubagentStart` gives no `tool_use_id`; the `Agent` `PreToolUse` gives no `agent_id`.

**So `NodeId` for a sub-agent is `(session_id, agent_id)`.** That is the identifier
the *approval* path carries, and approvals are the product's premise — a hook firing
inside a sub-agent reports `agent_id`, so a parked write is always attributed to the
right node. The other identifier is joined by adjacency (`PreToolUse[Agent]` is
immediately followed by `SubagentStart`), and that join is used **only for
enrichment** — routing a progress description or output text. If it mis-pairs under
parallel spawns from one parent, the cost is a topic under the wrong sibling, never
a misattributed approval.

**Two gifts worth taking.** `TaskProgressMessage.description` is a
ready-made thinking topic for the sub-agent node ("Reading pptmstr/log.py") — free,
current, and exactly what §2.6 asks for. And `SubagentStop` carries
`last_assistant_message` plus `agent_transcript_path`, so the sub-agent's result and
its full history are available without reconstructing either — consistent with §9,
where the SDK's own JSONL is the record and ours is a view.

### 2.6 Thinking topic

- **Default:** the orchestrator derives it mechanically from the current activity — `"reading src/store.py"`, `"running pytest"`, `"waiting on approval"`. Free, always present, never stale. Tool name plus the salient argument gets you most of the way.
- **Override:** register a `set_topic` custom tool (in-process, via the `@tool` decorator — no extra subprocess) so an agent can say something better when it wants to.

Never derive the topic via a summarization call. It's a per-frame-visible field; it must be free.

### 2.7 Inter-agent messaging (rev. 4)

A work template is a set of roles that need to pass concerns between them — a QA
agent telling a feature worker what it broke, a build specialist reporting back to a
lead. There are two transports for that, and the choice between them is not
either/or.

**The bus we own — default.** An in-process MCP server (`create_sdk_mcp_server` +
the `@tool` decorator, same mechanism as `set_topic` in §2.6) exposing
`post_concern(to, subject, body)`, `read_inbox()`, and `claim_task()`, backed by the
store. This is the default because of what it buys, which is the whole thesis of the
project applied to messages: **a concern becomes a store object, so it is snapshot
(I2), rendered in a pane, and reviewable in flight** — a concern can be read,
rejected with a reason, or *edited and then delivered*, exactly like a diff. Nothing
in the harness offers that, because nothing in the harness models a message as
anything but text in transit.

**`SendMessage` — for the cases where the model should route without us.** A built-in
tool, not gated behind agent teams; only the structured team-protocol messages
(`shutdown_request`, `plan_approval_response`) need teams. Three behaviours are
load-bearing:

- **The sibling roster is a spawn-time snapshot.** A sub-agent whose tool list
  includes `SendMessage` starts with a system reminder listing `main` and every
  other *named* agent, each a valid `to` value. It appears only when at least one
  other agent has a name, and agents named later are invisible to it. **Spawn order
  is therefore part of a work template's definition**, not an implementation detail:
  workers that must address each other have to exist before the ones addressing them.
- **Delivery wakes a finished agent** (§2.3).
- **Names can be reused; IDs cannot.** The CLI refuses a send when a name now
  refers to a newer agent than the one it reached earlier in the conversation. Since
  `NodeId` is `(session_id, agent_id)` and already ID-based (I6), our store is on the
  right side of this — but any operator-facing "message this agent" affordance must
  address by `NodeId`, never by the display name.

**Across sessions, not just within one.** Cross-session messaging lets independent
sessions message each other by name over a per-session Unix socket, never through
Anthropic servers when both are local. Its path is exported to hooks and Bash as
`CLAUDE_CODE_MESSAGING_SOCKET`, and it is the **only documented host → session
injection path** — there is no SDK method to send a message to a specific agent, so
`ClaudeSDKClient` control methods (§3.1) stop at `interrupt` / `set_model` /
`stop_task`. This is the transport that matches the pool spanning projects, which
agent teams (one team per session, scoped to one working directory) does not.
Constraints: plain text only, macOS/Linux, first-party API only, inbound gating via
`crossSessionInbound`, a 50-message cap, and repeat throttling.

**Taken from agent teams without adopting it** (§0): the task model — dependency
edges, a pending task with unresolved dependencies being unclaimable, automatic
unblocking on completion, and **file-locked claiming** so two workers cannot take the
same item. That lands beside `review_queue` as a second cross-agent projection. Also
worth copying: their mailbox drops malformed entries and still delivers the valid
ones, rather than failing the whole read.

**Open:** whether `post_concern` and `SendMessage` are themselves approval-gated
(§9). They are tool calls, so `PreToolUse` sees them either way.

---

## 3. Threading — the central decision

**The collision:** hello_imgui's runner is a blocking main-thread loop. The Agent SDK is asyncio-native and warns against naive mixing with threads. Both want to own control flow.

**The resolution: asyncio in a dedicated thread, UI on the main thread, exactly two crossing primitives.**

```
┌─ Main thread ──────────────┐     ┌─ asyncio thread ─────────────┐
│  hello_imgui.run(...)      │     │  loop.run_forever()          │
│    ├─ drain intent queue   │     │    ├─ query() task per agent │
│    ├─ store.snapshot()     │     │    ├─ PreToolUse hooks       │
│    ├─ build UI from snap   │     │    └─ writes to store        │
│    └─ render + idle        │     │                              │
└────────────────────────────┘     └──────────────────────────────┘
         │                                        ▲
         │  loop.call_soon_threadsafe(...)        │
         └────────────────────────────────────────┘
         ▲                                        │
         │  queue.Queue (thread-safe, UI-bound)   │
         └────────────────────────────────────────┘
```

The SDK's "don't mix threading" warning is about naively calling async code from threads. `loop.call_soon_threadsafe` and `asyncio.run_coroutine_threadsafe` exist precisely for this boundary and are the supported way to cross it. Keep the crossing to those two call sites and nothing else.

```python
class Bridge:
    """The ONLY place threads and asyncio meet."""
    loop: asyncio.AbstractEventLoop      # owned by the asyncio thread
    to_ui: queue.Queue[Intent]           # asyncio → main (thread-safe by construction)

    def resolve(self, fut: asyncio.Future, value) -> None:
        """Called from the UI thread to complete an awaited approval."""
        self.loop.call_soon_threadsafe(fut.set_result, value)

    def submit(self, coro) -> concurrent.futures.Future:
        """Called from the UI thread to start work (spawn agent, cancel, etc.)."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)
```

**Alternative considered:** run everything on one thread with `FpsIdlingMode.EarlyReturn`, which hello_imgui documents as intended for "event-driven or real-time applications, including Jupyter/async usage." Elegant, and imgui-bundle publishes an async-support guide. But it makes UI responsiveness a function of how well the event loop is behaving, and one badly-behaved await stutters your frames. Prefer the two-thread model; revisit if the bridge becomes painful.

Note that **each session spawns a Claude Code CLI subprocess.** N concurrent agents means N subprocesses, so concurrency is RAM-bound, not GIL-bound. Budget accordingly and cap the pool.

### 3.1 `ClaudeSDKClient`, not `query()` (rev. 3)

Rev. 2 said "`query()` per session." That is the wrong half of the API. `query()` is
the one-shot form; **`ClaudeSDKClient` is a connected session**, and every control
operation this orchestrator needs hangs off the client and is unreachable from
`query()`:

| Need | Client method | Otherwise |
|---|---|---|
| Context budget (§2.4) | `get_context_usage()` | you're back to guessing the window |
| Cancellation (§9) | `interrupt()` | kill the subprocess and lose the session |
| Per-node trust promotion (§5.4) | `set_permission_mode()` | restart the session to change mode |
| Model switch mid-session | `set_model()` | restart |
| Streaming | `receive_messages()` | — (both forms stream) |

It is also an async context manager (`__aenter__`/`__aexit__`) with an explicit
`connect()`/`disconnect()`, which gives the asyncio thread a real lifecycle to own
per node — one client per session, held for the session's life, closed on teardown.

The cost is that the session is now a long-lived object rather than an iterator you
drain, so the asyncio thread owns a `dict[NodeId, ClaudeSDKClient]` and its teardown
ordering becomes something to get right rather than something that falls out. Worth
it — three of the five rows above are features rev. 2 listed as open questions.

---

## 4. Frame loop and idling

### 4.1 Loop

```python
def gui():                                     # called by hello_imgui per frame
    global frame
    frame += 1

    while True:                                # I4: apply before snapshot
        try: store.apply(bridge.to_ui.get_nowait())
        except queue.Empty: break

    snap = store.snapshot()                    # I2, I3: once, atomic, O(1)
    view.prune(snap, frame)

    draw_agent_tree(snap, view, bridge)
    draw_detail_pane(snap, view, bridge)
    draw_review_queue(snap, view, bridge)      # §5.4
    draw_status_bar(snap)

    runner_params.fps_idling.enable_idling = not snap.any_active
```

### 4.2 Idling

```python
params.fps_idling.fps_idle = 9.0
params.fps_idling.enable_idling = True
```

hello_imgui already implements the wait-with-timeout machinery (`glfwWaitEventsTimeout` / `SDL_WaitEventTimeout`) plus a full-speed window after the last input. Layer your predicate on top: `enable_idling = not snapshot.any_active`.

Full speed while anything is `THINKING`/`CALLING_TOOL`/`RUNNING_TOOL`; ~9fps when everything is done or awaiting you. Keep hello_imgui's input-driven wake — it handles "the operator is interacting," which is orthogonal.

**Cross-thread wake:** when the asyncio thread pushes an intent while the UI is asleep in a wait-with-timeout, the UI won't notice until the timeout expires (≤111ms at 9fps). Acceptable. If it feels laggy, raise `fps_idle` before reaching for a custom wake.

### 4.3 Measured (rev. 3, step 5)

`scripts/bench_idle.py`, four phases in one process and one GL context so the
comparison is not across runs. Debian 12 / XWayland / Radeon, `fps_idle = 9`
(`settings.fps_idle`, operator-settable, so every figure below is conditional on
it). Twelve runs per layout, `--no-wake`, medians with ranges:

| phase | FOCUS — no splash | TRIAGE — splash | fps |
|---|---|---|---|
| full-speed — idling forced off, empty fleet | 13.3 (12.0–14.7) | 19.9 (17.6–21.0) | 60.8 |
| idle — empty fleet | **1.7** (1.5–2.0) | **4.7** (3.7–5.0) | 8.7–9.0 |
| idle+fleet — one session `AWAITING_APPROVAL` | **2.0** (1.9–2.2) | **2.1** (1.9–2.3) | 9.0 |
| active — one agent `THINKING` | 16.8 (14.8–18.6) | 16.0 (13.7–18.6) | 59.5 |

**The layout is now part of the measurement, and that is new.** The empty-fleet
splash lives in NEEDS YOU, which exists only in TRIAGE, so the first two rows
describe two different programs. `bench_idle.py` therefore takes `--layout` and
prints it; before this it inherited whatever layout was last remembered, which
meant consecutive runs could measure different things and the difference looked
like noise.

**The splash costs 2.6× on the cold-start screen and nothing anywhere else.** Row
two says 1.7% → 4.7%; row three says 2.0% → 2.1%. Seeding a single parked session
is enough to make the two layouts agree to within the noise, because a non-empty
fleet is exactly the condition under which the splash stops drawing. So the figure
worth quoting for "the app is resting" is **2.0%**, unchanged by this feature, and
the 4.7% is a cold-start-only cost that ends the moment the operator presses
Ctrl+N. Row one moves for the same reason and is the more alarming number, but it
is also the least real: with an empty fleet `any_active` is false, so full speed
only happens inside hello_imgui's post-input window — an operator actively moving
the mouse over an application they have not started anything in.

Full speed is vsync-bound at ~60fps, so the low-teens figure is the floor for a
continuously redrawing window rather than anything this app is doing wrong. Quote
the absolute rather than the ratio: the ratio moves purely because the full-speed
baseline is noisy.

**Correction to the previous revision.** It attributed the occasional
idle phase at 13.6fps to the benchmark's own wake probe — "the wake probe emits an
intent mid-phase, which wakes the UI and buys hello_imgui's full-speed-after-input
window." That explanation is wrong. With `--no-wake` the probe does not run at all
and the anomaly persists: 3 of 12 TRIAGE and 4 of 12 FOCUS idle phases still came
in above 11fps. The cause is hello_imgui's input-driven wake responding to
environmental input on a live display — window mapping, focus, pointer crossing —
which §4.2 keeps deliberately and calls orthogonal. Two things support this over
the old reading: it is layout-independent and probe-independent, and it clusters in
the *earlier* phase. The `idle+fleet` phase runs one slot later in the same process
and was contaminated 0/12 times in FOCUS against 4/12 for the phase before it,
which is what settling after the window opens looks like and is not what a
mid-phase probe would produce. Runs above 11fps are dropped rather than averaged;
the honest reading is still "9fps except when something wakes it, which is the
design working", but the something is the desktop, not the benchmark.

The last row is the point of the experiment. The ones above it only show that
idling *works*; an app that idles all the time would score beautifully on them and
be useless. The `active` row is what proves `any_active` is the thing driving it —
the predicate returns to full speed through its real input path (an `AgentSpawned`
+ `StateChanged` pair from the asyncio thread), not by being poked directly. The
`idle+fleet` row is the other half of that same argument, and it is why the phase
was added: it seeds a session through the same path but parks it
`AWAITING_APPROVAL`, so a predicate that keyed off "are there any agents" rather
than "is any agent working" would show full speed there and does not.

**Cross-thread wake latency: 69ms**, against the ≤111ms §4.2 predicted. That is the
"an agent finished while you were reading something else" case, and it is also the
lower bound on how fast an approval can visibly resolve while the app is resting.

**Not measured: keystroke-to-frame latency.** No input-injection tooling on this box
(`xdotool` absent), so this stays reasoned rather than verified. The mechanism is
that `glfwWaitEventsTimeout` returns on any event, so `fps_idle` bounds the idle
*timeout* and not input latency, and hello_imgui additionally holds full speed for a
few frames after input — meaning typing a rejection reason should run at full rate
rather than at 9fps. Worth confirming on a box with `xdotool` before treating it as
settled, because it lands on the interaction that matters most (§5.3's reason field).

---

## 5. The approval gate

The most important subsystem. Build it first after the store.

### 5.1 Mechanism

Use a **`PreToolUse` hook** with `permission_mode="dontAsk"`:

```python
options = ClaudeAgentOptions(
    permission_mode="dontAsk",              # deny anything not pre-approved
    hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[approval_gate],
                                      timeout=APPROVAL_TIMEOUT_S)]},
    include_partial_messages=True,
)
```

> **Verified against `claude-agent-sdk` 0.2.134 (rev. 3).** Three corrections to what
> rev. 2 assumed from secondary research. All read from `claude_agent_sdk/types.py`
> and `_internal/query.py` in the installed wheel.
>
> 1. **`hooks` maps an event to a list of `HookMatcher`, not to bare callables.**
>    `dict[HookEvent, list[HookMatcher]]`. `matcher=None` means "every tool".
> 2. **The permission decision is nested, not top-level.** The hook returns
>    `SyncHookJSONOutput`, and the decision goes inside `hookSpecificOutput`, which
>    must carry its own `hookEventName`. Returning `{"permissionDecision": "allow"}`
>    at the top level — as rev. 2 showed — is silently not a decision at all. See §5.2.
> 3. **`HookMatcher.timeout` defaults to 60 seconds and is enforced by the CLI, not
>    by the SDK.** This is the one that matters. See §5.2.1.

### 5.2.1 The timeout is the real constraint on the parking invariant

`HookMatcher.timeout` is passed through to the CLI (`_internal/query.py:224`) and
only when explicitly set; otherwise the CLI applies its own 60s default. On expiry
the CLI aborts that hook — its strings are `"PreToolUse hook timed out (per-hook
abort)"` and `"hook callback timed out after <n>ms"`. The Python side awaits the
callback with no timeout of its own, so **nothing on our side of the boundary
observes or prevents this.**

The parking invariant says the gate must be able to block indefinitely. That is not free, and rev. 2
was wrong to assume it was: a default-configured gate aborts every review that takes
more than a minute, which for a tool whose entire premise is "the operator is the
bottleneck" is a constant failure, not an edge case. Walking away for coffee would
corrupt a run.

So: **set `timeout` explicitly and set it large** (order of hours — it is a
backstop against a wedged UI, not a review deadline).

> **Verified empirically at step 3, ahead of schedule** — `scripts/verify_hook_timeout.py`,
> three real sessions against the live CLI. This needed a run rather than a source
> read: "the number is plumbed through" and "an unbounded await is honoured end to
> end" are different claims and only the first is provable by reading.
>
> | case | hook blocks | `timeout` | outcome |
> |---|---|---|---|
> | `exceeds-short` | 6s | 2s | **aborted at exactly 2.0s.** The hook coroutine is *cancelled*, so `asyncio.CancelledError` lands inside the gate. `ResultMessage.is_error` true. |
> | `long-timeout` | 75s | 6h | **completed.** Tool ran, `terminal_reason: "completed"`, no error. |
>
> **The parking invariant holds as designed. The gate can await the operator directly** and does not
> need `defer`. Two details worth keeping: the abort arrives as a cancellation of
> our own coroutine, which means the gate can catch it and resolve the pending
> approval rather than leaking a future nobody completes; and the timeout is
> honoured to the second, so it is a real backstop rather than a hint.

**Why not build on `defer` in the first place?** `"defer"` is a real decision value,
and `ResultMessage` carries a `DeferredToolUse` describing what was parked. But per
its own docstring, deferring *stops the run* and hands the tool call back to the
caller to resume. That trades a parked-but-live agent for a stopped one that must be
restarted — it loses the streaming session and turns "parked costs nothing" into
"parked costs a resume." Await-in-hook keeps the agent alive and is the right default;
`defer` is the contingency if the timeout cannot be raised far enough.

`PreToolUse` runs on **every** tool call regardless of permission mode or allowlist, runs *before* `can_use_tool`, and a deny from it is final — it blocks even under `bypassPermissions`. `can_use_tool` only fires when permission evaluation reaches a prompt state, which makes it easy to accidentally bypass. For a gate whose entire premise is "nothing gets through unreviewed," `PreToolUse` is the correct primitive.

### 5.2 The gate itself

The hook is async, so it can simply await your UI. This is the whole thing:

The callback signature is `(input, tool_use_id, context)` — the middle argument is
the tool-use ID, not a signal. `HookContext.signal` exists but is documented as
always `None`, so there is no abort channel to lean on.

```python
def _allow(updated: dict | None = None) -> SyncHookJSONOutput:
    out: PreToolUseHookSpecificOutput = {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
    }
    if updated is not None:
        out["updatedInput"] = updated       # §5.3 edit-then-approve
    return {"hookSpecificOutput": out}


def _deny(reason: str) -> SyncHookJSONOutput:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


async def approval_gate(hook_input, tool_use_id, context) -> SyncHookJSONOutput:
    tool_name = hook_input["tool_name"]
    tool_input = hook_input["tool_input"]
    node = node_id_from(hook_input)          # session_id + agent_id

    if classify(tool_name, tool_input) is Disposition.AUTO_APPROVE:
        return _allow()

    fut = asyncio.get_running_loop().create_future()
    pending = PendingApproval(
        id=new_id(), node=node,
        tool_name=tool_name, raw_args=tool_input,
        summary=summarize(tool_name, tool_input),
        diff=render_diff(tool_name, tool_input),   # unified diff for file ops
        requested_at=time.time(), future=fut,
    )
    store.set_pending(node, pending)          # → state becomes AWAITING_APPROVAL
    bridge.to_ui.put(PendingAdded(pending.id))

    decision = await fut                      # blocks this agent only. I8.

    store.clear_pending(node)
    if decision.approved:
        return _allow(decision.edited_args)
    return _deny(decision.reason or "Rejected by operator")
```

The UI side is one line: `bridge.resolve(pending.future, Decision(approved=True))`.

Because the await parks only that agent's task, other agents keep running and the app stays idle-able. That is the parking invariant satisfied structurally rather than by convention.

### 5.3 Two capabilities worth taking

`PreToolUse` can return `updatedInput`, which means:

- **Edit-then-approve.** You can fix a wrong path or tighten a shell command in the UI and approve the corrected version, instead of rejecting and waiting for a retry. For a "persnickety about changes" workflow this is the highest-leverage feature in the whole tool.
- **Rejection with a reason.** `permissionDecisionReason` is shown to the model. A rejection that explains itself is worth far more than a bare denial. Make the reason field a first-class input, not an afterthought.

### 5.4 Classification and batching

```python
class Disposition(Enum):
    AUTO_APPROVE, REQUIRE_APPROVAL, DENY

def classify(tool_name: str, tool_input: dict) -> Disposition: ...
```

Defaults: reads/searches/lists auto-approve; `Write`, `Edit`, `Bash`, git operations, and network mutations require approval; **anything unrecognized requires approval** (fail closed).

Batching is day-one scope, not a later nicety. Running several agents and approving one write at a time makes you the bottleneck, which defeats the tool's premise:

- approve / reject a single pending item
- approve all pending from one node
- approve all pending globally
- a **review queue** showing every pending diff across all agents in one scrollable place
- per-node "trust this tool class for this session," which promotes to `AUTO_APPROVE` for that node only

---

## 6. UI-side state

Presentation state only, keyed by `NodeId`, pruned when nodes vanish — Fleury's `last_frame_touched` pattern:

```python
@dataclass
class NodeViewState:
    expanded: bool = True
    transcript_scroll: float = 0.0
    follow_tail: bool = True          # auto-scroll until the user scrolls up
    reasoning_visible: bool = True
    last_frame_touched: int = 0
```

This does not violate I1. Scroll position and expansion are properties of *looking at* the thing, not of the thing.

### 6.1 Theming (rev. 3 — promoted from non-goal)

Theme is presentation state like the rest of §6: it never enters the store, and no
record carries a colour. But unlike scroll position it must **survive restart**, so
it lives in a small settings file, not in `NodeViewState`.

**Required set:** `dark` (default), `light`, `high_contrast`. Plus discretionary
ones — this is a Ghost in the Shell reference, so at least one that leans into it.
The required three are not decoration: this tool is stared at for long stretches,
and a dark-only UI in a bright room is a real cost.

**Colour is a semantic layer, never a literal.** Panels reference roles — `P.state_awaiting`,
`P.state_failed`, `P.diff_add` — not `#e0af68`. A theme is a table of roles; adding
one is filling in a table, not auditing every draw call. Roles worth naming up front,
because they are the ones a theme can get wrong in a way that costs you something:
per-`AgentState` colour, diff add/remove/context, and the approval-gate accents
(pending / approved / rejected).

**Two constraints that are load-bearing rather than cosmetic:**

1. **Never encode meaning in hue alone.** Every agent state carries an icon and a
   text label alongside its colour, and diffs keep their `+`/`-` gutter regardless
   of background. High-contrast mode collapses the palette toward two or three
   values, so any signal that exists only as hue disappears there — and the same
   signal is already invisible to a red/green-deficient operator in the other themes.
   Getting this right is what makes `high_contrast` a theme switch rather than a
   parallel UI.
2. **Theme lookup happens on the per-frame build path.** `P.accent` is read hundreds
   of times a frame. It must be an attribute read on an already-constructed object —
   no dict lookup by string, no colour arithmetic, no allocation. Precompute the
   packed `ImU32` forms once per theme change, not per access.

**Implementation note (from `../orbital`, which hit this):** do not expose the active
palette as a rebindable module global. `from .theme import P` copies the *reference*
into the importing module at import time, so rebinding `theme.P` on a theme switch
updates `theme`'s namespace and nothing else — the switch then half-works in a way
that is very hard to see. Use a `__getattr__` proxy that resolves to the current
palette on every access.

Style itself (`ImGuiStyle`: rounding, padding, border sizes) is global mutable state
that persists across frames — set it once on theme change, never per frame.

### 6.2 Motion (rev. 4)

A card is a still image, so a session that is working and a session whose subprocess
has wedged render identically. Elapsed time answers it eventually and only if you
watch the digits. The rail therefore carries one animated mark — three columns of
falling cells beside the state glyph, in the state's own colour — whose entire job
is to say *still moving*.

Three constraints, and the first is the one that decides where motion is allowed at
all:

1. **Motion is drawn only for `AgentState.is_active`.** Those are exactly the states
   that hold `enable_idling = False` (§4.2), so an animation can never be on screen
   while the loop is throttled. This is not a coincidence to be maintained by hand —
   it is why the predicate is `is_active` and not `state is THINKING`. A throbber on
   a session parked at `AWAITING_APPROVAL` would animate at 9fps, which reads as
   stuttering rather than as waiting, *and* would spend the CPU I8 says a parked
   session must not.
2. **Motion is never the only channel** (§6.1.1). The glyph, the label and the hue
   already say *thinking*; motion only adds *still going*. An operator who cannot
   perceive it loses nothing that is not said elsewhere.
3. **It gets one bounded exception to "no colour arithmetic on the build path"**
   (§6.1.2), because a fading trail is alpha that varies per cell per frame and
   cannot be precomputed at theme change. `theme.faded()` quantises alpha to twelve
   steps and memoises on `(packed colour, step)`, so the steady state is a dict hit
   and the cache is bounded by palettes × steps. Anything else wanting per-frame
   colour maths should go through it rather than open a second exception.

The trail wraps around the top of its column rather than falling off the bottom and
leaving the column empty until the next drop. That makes continuity structural —
every column has exactly one head every frame — rather than a property of how the
three periods happen to line up. The first version did the latter and went fully
dark for 0.18s roughly every other minute, which is long enough to read as a stall:
the precise message the mark exists to disprove.

---

## 7. Known traps

1. **Don't put the agent list in an auto-sized `ImGuiTable`.** `AutoFitQueue = (1 << 3) - 1` means three frames of column re-fitting every time rows change, and your rows change constantly. Use `ImGuiTableColumnFlags_WidthFixed`.
2. **Widget IDs from `NodeId`, never index.** `f"{label}##{session_id}:{agent_id}"`. See I6.
3. **Cross the thread boundary only through `Bridge`.** Two methods, two call sites. Every ad-hoc crossing is a future heisenbug.
4. **Snapshot once per frame.** Calling `store.snapshot()` twice in a frame is a bug even when it looks harmless.
5. **Fail closed on classification.** Unrecognized tool ⇒ requires approval.
6. **Context-budget bars are estimates.** No remaining-window API; compaction fires invisibly. Label it as approximate in the UI rather than implying precision.
7. **Subprocess count is your real concurrency ceiling.** One per session. Cap the pool and surface the count.
8. **Never `from .theme import P` where `P` is a rebindable module global.** The import copies the reference; rebinding on theme switch updates nothing in the importer. Proxy object, always. See §6.1.
9. **Verify model ID strings at build time.** Current families are Claude Fable 5, Opus 5, Sonnet 5, and Haiku 4.5 (`claude-fable-5`, `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`). I saw a conflicting identifier during research; check the model-config docs rather than trusting any string in this document.

---

## 8. Build order

1. **Store + snapshot + intents + `Bridge`.** No UI, no SDK. Unit-test CoW swap, snapshot atomicity, intent ordering, and `call_soon_threadsafe` round-tripping.
2. **Theme layer + agent tree pane** against a fake driver. Fixed columns: state, topic, model, context bar, elapsed. No SDK yet. Build the role table and the palette proxy *before* the first panel — semantic roles are cheap to write down first and expensive to retrofit, since retrofitting means auditing every draw call. Ship `dark`, `light`, and `high_contrast` together from the start; a second theme is what proves the first one contains no literals, and `high_contrast` is what proves state is legible without hue.
3. **One real agent.** Single `query()`, all tools auto-approved, `include_partial_messages=True`. Confirm the store stays honest under real streaming. **Close the two §2.5 verification gaps here** — reasoning block types, and whether subagent streaming is exposed.
4. **The approval gate.** `PreToolUse` + `dontAsk` + the future-await pattern. Single approve/reject only. This is the core feature; take the time.
5. **Idling.** Wire `any_active` to `enable_idling`. Measure idle CPU before and after.
6. **Concurrency.** N sessions, subagent tree from `parent_tool_use_id`, review queue, batch approval, edit-then-approve, rejection reasons.
7. **Transcript pane.** Segment styling, reasoning toggle, follow-tail, search. Expect to iterate; this is the part fighting the library.
8. **Work templates and the message bus (rev. 4).** A template is a named set of `AgentDefinition` roles plus a lead prompt and a spawn order (§2.7). Land the store side first — concerns as records, the task projection with dependencies and locked claiming — then the `@tool` surface, then the pane. Close the §9 gating question *before* the pane, since whether a concern parks in the review queue decides what the pane is for.

---

## 9. Open decisions

- ~~**Persistence.**~~ **Settled in rev. 3, before step 3 as required.** Lean on the SDK's own JSONL entirely; do not duplicate a log.

  The consequence worth stating: **`Transcript` is a view, not the record.** It is an in-memory render buffer for the pane, and it does not need to survive a restart — the SDK writes the authoritative per-session JSONL and hands us its `transcript_path` on every hook input. Persisting our own copy would create a second system of record that can disagree with the first.

  What we persist is therefore just **session IDs**. Two things fall out of that:

  - **We mint the session ID rather than learning it.** `ClaudeAgentOptions.session_id` accepts a caller-supplied UUID. This matters more than it looks: without it, `AgentSpawned` would have to invent a placeholder `NodeId` and re-key once the first message arrived — and since `NodeId` is the widget-key basis (I6), that re-key lands precisely when the agent starts streaming, scrambling hover and scroll at the worst moment. Minting it up front makes identity known before the first byte.
  - **"Retire this session" becomes a real action, not advice.** §2.4 says the answer to a compacted session is to start a fresh one. `resume=<id>` with `fork_session=True` is exactly that, and it is only available because we kept the ID.
- **Cancellation semantics.** Does cancelling a parent kill its subagents? The subprocess model gives a natural boundary. Rev. 3 adds a second, softer lever: `ClaudeSDKClient.interrupt()` stops work without tearing down the session, and `stop_task(task_id)` targets one task. So the choice is now three-way — interrupt (recoverable), disconnect (session ends), or kill — and "cancel" in the UI should say which one it means.

  **Rev. 4 adds a fourth axis: whether the thing we stopped can be woken by a
  sibling.** `stop_task` makes an agent immune to `SendMessage` (the send is
  refused); the model's own `TaskStop` does not. "Stopped" in the UI must therefore
  distinguish *paused and reachable* from *stopped and deaf*, or the operator will
  stop an agent and watch a teammate restart it.
- ~~**Context-budget policy.**~~ **Settled in rev. 3 (§2.4):** context is a session-health signal, not a budget. Advisory only, surfaced as `ContextPressure` plus an observed compaction count, with "fork this session" as the offered action. Money is a separate axis with its own hard stop (`max_budget_usd`).
- **Whether spawning a subagent is itself approval-gated.** Probably yes given your stated preferences — the `Agent` tool call is visible to `PreToolUse` like any other.
- **Where a team's board and its concerns are drawn (new, step 8).** Both projections exist in the snapshot and nothing reads them, so a team is half-observable: the operator approves a message between two agents without seeing the work either holds. `planning/archive/2026-08-12-the-board-has-no-surface.md`.
- **Concurrency cap.** Subprocess-bound. Pick a number, surface it, make it configurable.
- ~~**Whether inter-agent messages are approval-gated (rev. 4, §2.7).**~~ **Settled at step 8, and the question turned out to be two questions.** Interception is not optional: an in-process MCP handler is told only a tool name and its arguments, so `PreToolUse` stamping the sender is the only thing that gives a concern a `from` at all (§2.7). The gate is the bus's authentication layer, and that half is structural.

  Only *the operator being asked* was ever policy, and it lands on the **send**. Rejection needs a channel back and only the sender has one — telling a recipient that a message it never saw was refused helps nobody, while `permissionDecisionReason` reaches the agent that wrote it and could revise it. The "gate on effect" split this entry proposed is unnecessary.

  The bottleneck worry stands and is now answerable by use rather than by argument: concerns arrive as ordinary approval rows, so a dogfooding run measures it directly. Note that gating the send *reduced* scope — `ObligationKind` gains no fourth member and there is no concern review pane, because a parked `post_concern` is a `PendingApproval` like any other.
- **Whether a settings-file hook inside a teammate process can reach our driver (rev. 4).** Assumed no, and the §0 decision rests on it. If it can, a bridged gate becomes conceivable and the agent-teams fork reopens. This is the single finding that would reverse a recorded decision, which is reason enough to close it deliberately rather than by accident.

---

## 10. Revision history

### Diff from rev. 3

| Area | rev. 3 | rev. 4 |
|---|---|---|
| Agent teams | not considered | **explicitly not adopted (§0)** — teammates are processes outside our driver, so adopting removes the parking invariant rather than merely lacking it |
| I8 | referred to by number | **named: the parking invariant (§1)** — restated consequence-first as "parking is unbounded and free"; the number stays as the anchor |
| Inter-agent communication | none; sub-agents report to the parent only | **§2.7** — an in-process MCP bus we own, plus `SendMessage` where the model should route unattended |
| Concerns between agents | — | **store objects: snapshot, rendered, reviewable and editable in flight** |
| Terminal states | terminal | **`SendMessage` wakes a finished sub-agent (§2.3)** — an inbound edge that originates from a sibling |
| Stop semantics | three-way: interrupt / disconnect / kill | **four-way** — plus whether the stopped agent is still reachable by a sibling (§9) |
| Spawn order | an implementation detail | **part of a work template's definition** — the sibling roster is a spawn-time snapshot (§2.7) |
| Task list | `review_queue` only | **second projection: dependencies, unclaimable-while-blocked, file-locked claiming** (taken from agent teams) |
| Cross-project reach | pool of independent roots | **cross-session messaging by name; `CLAUDE_CODE_MESSAGING_SOCKET` is the only host → session injection path** |
| Build order | 7 steps | **step 8: work templates and the message bus** |

### Diff from rev. 2

| Area | rev. 2 | rev. 3 |
|---|---|---|
| Theming | explicit non-goal | **first-class feature; light / dark / high-contrast required (§6.1)** |
| Colour in panels | unspecified | **semantic roles only; no literals below the theme module** |
| State encoding | colour | **colour + icon + label, so hue is never the only channel** |
| Build order | tree pane at step 2 | **theme layer lands with the tree pane, before the first panel** |
| Transport | `query()` per session | **`ClaudeSDKClient` per session (§3.1)** — control methods are unreachable from `query()` |
| Context budget | self-supplied `{model: window}` table | **`get_context_usage()`; rev. 2's constraint was wrong (§2.4)** |
| Context framing | a budget, shown next to cost | **session-health signal; separate from money, measured against the compaction threshold (§2.4)** |
| Compaction | "fires invisibly" | **observable via the `PreCompact` hook; counted per session (§2.4)** |
| Hook output shape | top-level `permissionDecision` | **nested under `hookSpecificOutput` (§5.2)** |
| Hook registration | `{"PreToolUse": [callback]}` | **`{"PreToolUse": [HookMatcher(...)]}`** |
| Approval blocking | assumed free | **bounded by `HookMatcher.timeout`, 60s default, CLI-enforced (§5.2.1)** |

### Diff from rev. 1

| Area | rev. 1 | rev. 2 |
|---|---|---|
| Agent execution | hand-rolled executor + tool dispatch | `claude-agent-sdk` `query()` per session |
| Approval mechanism | custom `threading.Event` block in the executor | async `PreToolUse` hook awaiting an `asyncio.Future` |
| Approval capabilities | approve / reject | approve / reject / **edit-then-approve** / **reason-on-reject** |
| Threading | thread pool + main-thread UI | **asyncio thread + main-thread UI, two-primitive bridge** |
| Tree structure | hand-maintained `parent_id` | derived from `parent_tool_use_id` / `agent_id` |
| Usage tracking | self-reported | SDK `usage` per message; **context window self-supplied** |
| Concurrency limit | thread-pool sized | **subprocess/RAM bound** |
| New states | — | `RATE_LIMITED` |

---

## 11. Sources

**Claude Agent SDK** (gathered via research; verify against docs at build time):
[overview](https://code.claude.com/docs/en/agent-sdk/overview) · [Python reference](https://code.claude.com/docs/en/agent-sdk/python) · [permissions](https://code.claude.com/docs/en/agent-sdk/permissions) · [hooks](https://code.claude.com/docs/en/agent-sdk/hooks) · [streaming output](https://code.claude.com/docs/en/agent-sdk/streaming-output) · [cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking) · [subagents](https://code.claude.com/docs/en/agent-sdk/subagents) · [sessions](https://code.claude.com/docs/en/agent-sdk/sessions) · [model config](https://code.claude.com/docs/en/model-config)

**Claude Code harness** (read 2026-08-11 against the bundled CLI **2.1.226**, which clears every version gate below — 2.1.178 teams-as-documented, 2.1.198 background sub-agents by default, 2.1.199 name-collision check, 2.1.206 sibling roster, 2.1.224 cross-session messaging):
[agent teams](https://code.claude.com/docs/en/agent-teams) · [sub-agents](https://code.claude.com/docs/en/sub-agents) · [cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging) · [tools reference](https://code.claude.com/docs/en/tools-reference) · [SDK custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools)

Two facts from that read constrain the design rather than merely informing it: `TeammateIdle` / `TaskCreated` / `TaskCompleted` are **TypeScript-only** hooks, so a Python host cannot observe team lifecycle even from outside; and there is **no SDK API to send a message to a specific agent**, which is what makes the messaging socket the only injection path (§2.7).

**UI constraints:**
- Table 3-frame auto-fit: `imgui_tables.cpp`, `column->AutoFitQueue = column->CannotSkipItemsQueue = (1 << 3) - 1; // Fit for three frames`
- Idling machinery and defaults: [hello_imgui](https://github.com/pthom/hello_imgui) `runner_params.h`, `abstract_runner.cpp`
- Python `fps_idling` bindings: [Dear ImGui Bundle — App Runners](https://pthom.github.io/imgui_bundle/core-libs/hello-imgui-immapp/)
- 3-frames-after-input guidance: [ocornut, PR #2749](https://github.com/ocornut/imgui/pull/2749#issuecomment-524543790)
- Widget keying and prune-by-frame-touched: [Fleury, UI Part 2](https://www.rfleury.com/p/ui-part-2-build-it-every-frame-immediate)

**Closed in rev. 3** — read from `claude-agent-sdk` 0.2.134 as installed, not from docs:
- `PreToolUse` hook input/output schema — §5.2. Rev. 2's shape was wrong in two ways.
- Hook timeout semantics — §5.2.1. The finding that changed a design invariant.
- Context window exposure — §2.4. Rev. 2's stated constraint does not exist.
- `ThinkingBlock` is a real exported type, so `REASONING` segments have a concrete source (§2.5).
- Subagent attribution: `agent_id`/`agent_type` arrive on tool-lifecycle hook inputs, and the SDK's own comment says parallel subagents interleave over one control channel and `agent_id` is "the only reliable way to attribute each one" — direct confirmation of I6 and of `NodeId`. There are also `SubagentStart`/`SubagentStop` hooks carrying `agent_id`, which is a cleaner tree source than reconstructing from `parent_tool_use_id`.

**Also closed at step 3, by running it:**
- Hook timeout semantics end to end (§5.2.1). The parking invariant holds; the gate awaits directly.
- `ThinkingBlock(thinking, signature)` is a real content block, and `thinking_delta` arrives as a `content_block_delta` on `StreamEvent`, so reasoning streams token by token.
- `ClaudeAgentOptions.session_id` accepts a caller-minted UUID, which is what makes `NodeId` stable from spawn (§9).

**Still unverified — close before relying on:**
- Whether subagent activity streams as `StreamEvent` or only as complete messages (§2.5). Not reachable until step 6 spawns one.
- Whether `ResultMessage.total_cost_usd` is per-turn or cumulative across turns on one client. The driver deltas against the last seen value, which is correct either way, but the ambiguity is real and unresolved.
- Model ID strings (§7, trap 9).
- hello_imgui "cannot run GUI from separate Python thread" issue — title seen, thread not read. I5 holds regardless.

**Opened in rev. 4 — close before step 8:**
- Whether a settings-file hook in a teammate process can reach our driver (§9). The one finding that would reverse a recorded decision.
- What an auto-resumed sub-agent looks like from our side: whether its activity arrives with `agent_id` set as usual, and whether the wake re-enters `THINKING` through a store path that already exists (§2.3). Extend `scripts/verify_subagents.py`; do not write a new probe.
- Whether cross-session messaging is actually enabled in our configuration — it depends on feature-flag evaluation, which `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_TELEMETRY`, `DO_NOT_TRACK`, and `DISABLE_GROWTHBOOK` each turn off. `/list-agents` in a plain CLI session is the one-command check.
