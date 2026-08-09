# Multi-Agent Orchestrator — Coarse Design (rev. 3)

Design spec for an LLM multi-agent coordinator with an immediate-mode UI, built on the **Claude Agent SDK**. Written to be handed to an implementing agent.

**Stack:** Python · `claude-agent-sdk` for agent execution · `imgui-bundle` (Dear ImGui + hello_imgui) for UI · personal/small-team tool, dev audience.

Read §1 (Invariants) and §3 (Threading) first. §3 is the load-bearing decision in this revision.

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

You are building: the orchestration UI, the approval interaction, the cross-session state projection, and the tree/rollup views.

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
| I8 | **The approval gate must be able to block indefinitely** without stalling the UI or other agents. | You are the bottleneck by design. A parked agent must cost nothing. |

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

`ACTIVE_STATES` is the **idle predicate** for the render loop (§4.2). `AWAITING_APPROVAL` being idle is the point of I8 — agents parked on your review cost nothing, and the whole app drops to idle FPS while it waits on you.

`RATE_LIMITED` is driven by `RateLimitEvent`, which the SDK emits on rate-limit status changes. Surface it; it's the difference between "stuck" and "backing off."

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

> **Two verification gaps to close during step 3 of §8.** (a) The exact event/type names for **extended-thinking / reasoning** deltas were not confirmed — check the `ContentBlock` union and `ThinkingBlock` in the Python SDK reference before building `REASONING` segment handling. (b) **Subagent streaming is reportedly not exposed as raw `StreamEvent`s** — only complete messages, with `StreamEvent.parent_tool_use_id` always null. If that holds, subagent transcripts arrive in turn-sized chunks, not token-by-token. Confirm early: it materially affects goal #3 for anything below the root.

### 2.6 Thinking topic

- **Default:** the orchestrator derives it mechanically from the current activity — `"reading src/store.py"`, `"running pytest"`, `"waiting on approval"`. Free, always present, never stale. Tool name plus the salient argument gets you most of the way.
- **Override:** register a `set_topic` custom tool (in-process, via the `@tool` decorator — no extra subprocess) so an agent can say something better when it wants to.

Never derive the topic via a summarization call. It's a per-frame-visible field; it must be free.

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

### 5.2.1 The timeout is the real constraint on I8

`HookMatcher.timeout` is passed through to the CLI (`_internal/query.py:224`) and
only when explicitly set; otherwise the CLI applies its own 60s default. On expiry
the CLI aborts that hook — its strings are `"PreToolUse hook timed out (per-hook
abort)"` and `"hook callback timed out after <n>ms"`. The Python side awaits the
callback with no timeout of its own, so **nothing on our side of the boundary
observes or prevents this.**

I8 says the gate must be able to block indefinitely. That is not free, and rev. 2
was wrong to assume it was: a default-configured gate aborts every review that takes
more than a minute, which for a tool whose entire premise is "the operator is the
bottleneck" is a constant failure, not an edge case. Walking away for coffee would
corrupt a run.

So: **set `timeout` explicitly and set it large** (order of hours — it is a
backstop against a wedged UI, not a review deadline). Then verify empirically at
build step 4 that a hook awaiting past the deadline actually survives, because
"the number is plumbed through" and "an unbounded await is honoured end to end"
are different claims and only the first is proven by reading the source. If the CLI
turns out to clamp it, the fallback is `permissionDecision: "defer"` (below), and
I8 gets rewritten rather than quietly missed.

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

Because the await parks only that agent's task, other agents keep running and the app stays idle-able. That is I8 satisfied structurally rather than by convention.

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

---

## 9. Open decisions

- **Persistence.** The SDK already writes JSONL transcripts per session and supports `resume` and `fork_session`. Cheapest path: store session IDs and lean on the SDK's own files rather than duplicating a log. Decide before step 3 — retrofitting is worse than building it in.
- **Cancellation semantics.** Does cancelling a parent kill its subagents? The subprocess model gives a natural boundary. Rev. 3 adds a second, softer lever: `ClaudeSDKClient.interrupt()` stops work without tearing down the session, and `stop_task(task_id)` targets one task. So the choice is now three-way — interrupt (recoverable), disconnect (session ends), or kill — and "cancel" in the UI should say which one it means.
- ~~**Context-budget policy.**~~ **Settled in rev. 3 (§2.4):** context is a session-health signal, not a budget. Advisory only, surfaced as `ContextPressure` plus an observed compaction count, with "fork this session" as the offered action. Money is a separate axis with its own hard stop (`max_budget_usd`).
- **Whether spawning a subagent is itself approval-gated.** Probably yes given your stated preferences — the `Agent` tool call is visible to `PreToolUse` like any other.
- **Concurrency cap.** Subprocess-bound. Pick a number, surface it, make it configurable.

---

## 10. Revision history

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

**Still unverified — close before relying on:**
- Whether an unbounded hook await survives end to end (§5.2.1). Source says the number is plumbed through; only a run proves the CLI honours it.
- Whether subagent activity streams as `StreamEvent` or only as complete messages (§2.5).
- Model ID strings (§7, trap 9).
- hello_imgui "cannot run GUI from separate Python thread" issue — title seen, thread not read. I5 holds regardless.
