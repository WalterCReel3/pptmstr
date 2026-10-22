# Preview tool: the render process (PD2) and a call bound (PD3)

Board task `preview-eval`, 2026-10-21. It weighs the two open operator decisions
in §10.6 of `planning/2026-10-18-vector-displays-beside-typeset-text.md`. Nothing
in `pptmstr/` was changed, nothing is built, and the planning record is not edited
here.

**Decided, not weighed here.** PD1, the approval rung, was decided by the operator
on 2026-10-21: `preview_figure` is auto-approved on every rung. Its stated
condition still applies. It holds while `sanitise()`, which runs in the GUI
process, stays bounded in time and memory for every input up to its byte cap. It
also holds once the tool sanitises in a child of its own. PD2 option (a) below
can provide that.

**Evidence marks.** **[ran]** means I executed it on this machine and the number
is from that run. **[read]** means I established it from the code or a record and
did not execute it. **[judgement]** is my weighing, with no measurement behind it.

## What was measured

Machine: 16 cores, 31 GiB, Linux 6.1, Python 3.11.2, the worktree's `.venv`
(resvg_py 0.5.0). Every child was started through `render.Renderer` itself
(`spawn` context, `RLIMIT_AS` 1 GiB, imscribe's fonts), the same way the pane
starts one. The probe scripts were throwaway and lived in `$TMPDIR`. They are not
in the tree.

| measurement | result | basis |
|---|---|---|
| Child start, `submit` to `ready` (6 runs) | 0.085–0.130 s, median 0.102 s | [ran] |
| Child start to the first 600 px raster (6 runs) | 0.106–0.154 s, median 0.122 s | [ran] |
| Idle child after one 600 px render: RSS / PSS / private dirty | 29 / 21 / 18 MiB (VmSize 43 MiB) | [ran] |
| The same child after one Mermaid layout (merman imported) | RSS 45 / PSS 31 / private dirty 23 MiB | [ran] |
| 1,200-label grid at 4096 px: peak (VmHWM), then RSS once idle | 225 MiB peak; back to 87 MiB, then 40 MiB after a 600 px render | [ran] |
| The same grid at 600 px, a preview's likely width | 0.28–0.32 s | [ran] |
| A small figure queued behind a 20-layer nested-opacity SVG on one child | the small figure arrived at 3.79 s, not about 0.1 s | [ran] |
| The same behind a 62-layer one (both admitted by `sanitise`) | the slow one was killed at 10.0 s, and the small one arrived at 10.13 s, after a child restart | [ran] |
| The 20-layer render on a thread in the same process, with a 1 ms ticker thread running | render took 3.74 s; the longest ticker gap was 3.74 s | [ran] |

The hostile input is plain SVG that the sanitiser admits. It is n nested
`<g opacity="0.99">` groups, each holding a full-canvas rect at
`opacity="0.5"`, on a 1600×1200 canvas. It is probe R3's opacity-layer case.

**This contradicts the record.** `START_TIMEOUT_S`'s docstring and §10.6 both
say a cold start is "about a second". Here it is a tenth of that. My runs had a
warm page cache. I did not measure a cold-cache start, and I did not measure the
operator's machine. Probe M1/R2 ran that machine about 1.6× slower, which would
still put a start near 0.2 s. So the "second ~1 s cold start" that §10.6 charges
to option (a) is likely overstated about fivefold on a warm machine [ran here;
judgement for elsewhere].

## PD2: which process renders a preview

These facts constrain every option:

- One `Bridge` and one asyncio thread serve every session in the process
  (`app.py` builds one `Bridge()`; `SessionPool` shares it) [read].
- Bus handlers run on that thread [read, `bridge.py`].
- `Renderer` is frame-thread state with no lock, and `host.pump` is its only
  poller [read, `render.py`].
- The child returns RGBA, not PNG. Every option therefore needs a PNG path in the
  child (`render_png` already exists) or a re-encode [read].

The existing ask/emit/settle crossing (`Bridge.ask`, `Bridge.settle`) is how
every bus read is already answered by the frame thread [read]. So §10.6's line
that (b) "needs a bridge path that does not exist" overstates it. The crossing
exists. What does not exist is an intent the frame thread turns into a `submit`,
and a request that `pump` holds open across the several frames a render takes
before it settles it.

| | (a) a second child for previews | (b) share the pane's child through the bridge | (c) render in-process on the bus thread |
|---|---|---|---|
| Memory | One more process: about 30 MiB RSS idle, 45 MiB once Mermaid is loaded [ran]. It is started lazily, so it costs nothing until the first preview [read]. Peaks are bounded by the 1 GiB `RLIMIT_AS` on Linux [read]; at 600 px a preview stays far below the 225 MiB a 4096 px raster reached [ran]. | No new process [read]. | No new process. But resvg's transient allocations land in the GUI process [read]. |
| Start-up | About 0.1 s for the first preview, warm [ran]. Under 0.2 s by the 1.6× factor [judgement]. | None beyond the pane's child, which may already be running [read]. | None, apart from importing resvg and merman into the GUI process once [read]. |
| Interference with on-screen figures | None through the queue: the two children are separate [read]. They share CPU (one more core at most) and memory pressure [judgement]. | Direct. One child renders serially, so a preview the sanitiser admits held a visible figure for 3.8 s, or for 10.1 s when it was killed [ran]. In-flight work cannot be pre-empted; a priority queue only fixes the order of what has not started [read]. A kill also restarts the child the pane is using [ran]. | Severe. resvg holds the GIL for the whole render: the ticker stalled for 3.74 s of a 3.74 s render [ran]. That stalls the frame loop and every session's bus handlers. No timeout can stop a thread, so `RENDER_TIMEOUT_S` does not apply [read]. |
| Code complexity | A second `Renderer` polled from an asyncio task. The one-poller invariant holds per instance [read]. It needs a PNG request kind, and an await loop or a wake-up for `poll` [judgement]. It is the smallest change to the existing invariants [judgement]. | A new intent, a pending-preview map in `pump` that spans frames, settle-on-result, abandonment at shutdown, and a third request kind in the shared queue [read]. It couples the bus to the frame loop's figure state [judgement]. | Least plumbing: call `render_png` directly. But it needs resvg, fonts and merman in the GUI process, which `render.py` exists to avoid [read]. |
| Failure isolation | A crash, abort or `RLIMIT_AS` hit kills only the preview child. The next preview restarts it in about 0.1 s [ran for restart cost]. | A preview's abort kills the pane's child, and every visible figure in flight fails as transient and re-renders [read, `_collect`; ran for the kill-and-restart path]. | None. An abort on allocation failure, or a native crash in resvg, takes down the GUI process and every session in it [read, `MEMORY_LIMIT_BYTES` docstring on abort]. |
| macOS (`RLIMIT_AS` not enforced) | Only the 10 s timeout bounds the preview child's memory. A runaway grows one disposable process [read]. | The same bound, but the runaway is the child the pane needs [read]. | Nothing bounds it: no rlimit and no timeout, inside the GUI process [read]. |
| Sanitising | Can move into the child: send raw SVG and sanitise there under the rlimit and the timeout. That makes PD1's condition structural rather than dependent on `sanitise()` staying bounded [judgement]. | Sanitises in the GUI process, as `host._svg_mark` does today [read]. | Sanitises in the GUI process [read]. |

**Recommendation for PD2: (a), with three refinements.** [judgement, on the runs
above]

1. **One preview child per process, not one per session.** All sessions share
   one asyncio thread [read]. A process-wide preview `Renderer` polled there
   therefore keeps the one-poller invariant and costs about 30–45 MiB once,
   rather than once per session.
   The cost is that previews from different sessions queue behind each other.
   That is acceptable because PD3's in-flight bound below keeps any one caller
   from holding the queue for more than one timeout.
2. **Sanitise in the preview child.** It costs a request kind. In return PD1's
   auto-approval no longer rests on the GUI-process sanitiser's bound.
3. **Return PNG from the child** (`render_png`) rather than RGBA, so nothing
   re-encodes in the GUI process.

(c) is ruled out by the 3.74 s GIL stall alone. (b) saves about 30 MiB and
0.1 s. It costs the property §10.6 called the one that must not regress: a
measured 10 s freeze of the operator's figures from one admitted preview.

## PD3: a bound on preview calls, and which roles get the tool

**What already bounds a preview, before any new bound** [read unless marked]:

- **CPU.** Each call is held to `RENDER_TIMEOUT_S` = 10 s. With one preview
  child (PD2), all previews in the process together use one core, however
  often they are called [judgement, from the serial child].
- **Context.** About 400–600 visual tokens per image (§10.6, computed). The cost
  falls in the caller's own context and is re-sent, cached, on later turns. The
  caller's context window and the operator's spend are the backstop.
- **Concurrency.** None today. The CLI can issue several tool calls from one
  assistant message. Under the CLI's concurrency, one caller could fill a shared
  preview queue [judgement; the CLI's concurrency for MCP calls was not
  measured].
- **Visibility.** The gate sees every call by name and node
  (`driver._gate_tool_use` resolves `agent_id` to a node), so a per-node count
  is available [read]. The transcript currently shows the base64 as a `repr`
  (§10.6) [read].

The recorded reasoning on boxes matters here. The comment above `classify` in
`driver._gate_tool_use` rejects a time box and a call-count box on *policy*.
They "expire for reasons the operator cannot see", and a session's work comes in
arcs (orient, act, re-orient), so a box sized to the first arc cuts off the later
ones [read]. That decision was about the approval rung ending, not about a tool
refusing, so it does not settle this. But it is the same failure mode for any
cumulative count, and it bears on which option is honest.

| option | abuse it stops | how the operator sees it | at the bound | the model's likely behaviour |
|---|---|---|---|---|
| None | Nothing beyond the timeout, the one core and the caller's context [read]. | A tool line per call [read]. | Nothing. | A layout-chasing loop runs until context or spend stops it. Each iteration is visible in the transcript but is not summarised [judgement]. |
| Count per turn | Loops within one exchange [judgement]. | A refusal line; the count resets each turn [judgement]. | Refused, with the count and the reset named. | "Turn" is not defined for a worker. A sub-agent's whole task is one tool call from its lead, so per turn becomes per session for workers and per prompt for the root [read: workers start through `Agent`; judgement: the consequence]. The bound is inconsistent across roles. |
| Count per session | Total volume [judgement]. | A refusal late in a long session, for a reason that built up out of sight. This is the failure the gate comment records [read]. | Refused for the rest of the session. The model cannot raise it, and nobody may be at the pane to do so. | Spends previews early on drafts, then has none when the final figure needs checking [judgement]. Without the number in the tool description it cannot plan [judgement]. |
| Rate (per minute) | Tight loops and burst parallel calls [judgement]. | A refusal that clears itself; rarely seen [judgement]. | Refused with "retry after N s". | Waits or moves on. It bounds the speed of a loop but not its length, so tokens still grow without limit over a long session [judgement]. |
| Token or byte budget | Matches the real cost, context tokens [judgement]. | Needs a meter to make sense [judgement]. | Refused when spent. | The model cannot predict an image's token cost before rendering, so it cannot plan against the budget. It is the per-session count's failure with a less legible unit [judgement]. |
| In-flight per caller (one at a time) plus a process-wide queue depth | One caller filling the shared preview child; parallel bursts [judgement]. | Rare refusals that clear on their own [judgement]. | Refused: "a preview of yours is still rendering" or "N previews queued; retry". | Serialises its own previews, which it would mostly do anyway [judgement]. It bounds latency and fairness, not volume. |

**Recommendation for PD3** [judgement]:

- **Bound concurrency now, not volume.** Allow one in-flight preview per node and
  a small process-wide queue (about 4). Refuse past either, with the reason in
  the refusal. This is the shape of the existing precedents. `MAX_OPEN_DECISIONS_PER_ASKER`
  (8), `MAX_OPEN_SUBAGENT_DECISIONS` (24), commandbus's `MAX_OPEN_PER_ASKER` and
  `subagent_cap` all bound what is *open*, not what has accumulated [read]. It
  answers the one abuse that is not already bounded: one caller starving the
  shared child, and through it every other session's previews.
- **Do not set a cumulative count until PV2 measures loops.** CPU is already one
  core, and tokens fall in the caller's own context. A per-session count is the
  arc failure the gate comment records, applied to an unattended session with
  nobody to lift it. If the operator wants a backstop before PV2, the least bad
  choice is a **per-node per-session count stated in the tool description**, so
  the model can plan around it. It should be generous, and the number should
  come from PV2, not from here.
- **Make use visible.** Have the transcript show a one-line summary per preview
  (size, note count), and show a per-session preview count where the status
  line counts other things. This is needed whatever bound is chosen, and §10.6
  already calls for the transcript part.

**Which roles get the tool.** Recommendation: **every role, reviewer included**
[judgement].

- `Role.tool_list` appends every bus tool to a restricted role's list
  (`BUS_TOOL_NAMES`) [read]. A new bus tool therefore reaches every role by
  default. Lead-only would need a deny in the gate, or a split list.
- The agent that writes a figure is usually a worker, not the lead. A lead-only
  tool would make the lead render its workers' figures second-hand [judgement].
- A reviewer has no shell, so it cannot run a rasteriser. Without the tool it
  can only read a figure's source. This preview is the one way it could check
  that a figure renders, and see what the sanitiser stripped [read for the
  reviewer's tools, `templates.READ_ONLY_TOOLS`; judgement for the value].
- The tool writes nothing and reaches nothing a fence cannot (§10.6). The
  per-node in-flight bound limits each role the same way, so excluding a role
  limits nothing that the bound does not already limit [judgement].

## Not run

- A cold-cache child start, and anything on the operator's machine or on macOS.
- Whether CLI 2.1.301 delivers an MCP image block to the model (PV1). Every
  option here is moot if it does not.
- Whether a model loops on previews (PV2). The PD3 count question waits on it.
- The GUI process's own RSS with the pane open, to put 30–45 MiB in proportion.
  The sandbox showed no running app.
- Whether the CLI runs parallel MCP tool calls from one message concurrently.
  The in-flight bound assumes it might.

## Decided, 2026-10-22

The operator decided PD2 and PD3:

- **PD2:** previews render in a second worker process of their own, separate from the
  figure child, so a slow or hostile preview cannot hold a figure on screen.
- **PD3:** previews are pipelined through that worker's queue rather than counted per
  session; the queue depth is a tunable, not a policy.

With PD1 (auto-approve, conditional on sanitise() staying bounded, which the worker now
makes moot by sanitising inside itself), the preview tool's design is settled. Probe PV1
-- whether the CLI hands an image result back to the model -- comes before it is built.
