# pptmstr — Puppet Master

A multi-agent LLM orchestrator with an immediate-mode UI. Runs N Claude agent
sessions concurrently, keeps all of them legible at a glance, and **writes nothing
without a human approving it first**.

Built on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)
for agent execution and [Dear ImGui](https://github.com/pthom/imgui_bundle)
(via `imgui-bundle` / hello_imgui) for the UI.

> The name is a Ghost in the Shell reference. The tool is the opposite of the
> premise: nothing acts on its own.

---

## What it is

An orchestration surface for people who want a lot of agent work happening at once
and are particular about what lands on disk. The design premise is that **the
operator is the bottleneck on purpose**, so the interesting engineering is in
making that bottleneck cheap:

- **Approval is a runtime state.** Every mutating tool call parks in front of you
  as a diff. You approve, reject with a reason the model sees, or *edit the
  arguments and approve the corrected version*.
- **Parked agents cost nothing.** An agent awaiting review is idle, not spinning,
  and the whole app drops to idle frame rate while it waits on you.
- **Reasoning is surfaced as it streams**, not reconstructed afterwards.
- **Context is a health signal, not a budget.** The question it answers is "is this
  session about to compact, and should I retire it?" — not "how much have I spent."
  Those are different axes and live in different widgets.

Non-goals: i18n, multi-user, remote access.

## Status

Early. Following the build order in [orchestrator-design.md](orchestrator-design.md) §8:

| Step | | |
|---|---|---|
| 1 | Store, snapshot, intents, Bridge | **done, tested** |
| 2 | Theme layer + agent tree pane against a fake driver | **done, tested** |
| 3 | One real agent over the SDK | **done, tested** |
| 4 | The approval gate | **done, tested** |
| 5 | Idling | **done, measured** |
| 6 | Concurrency, sub-agent tree, review queue, batching | **done, tested** |
| 7 | Transcript pane | **done, tested** |

## Getting started

```sh
./bootstrap.sh          # probe -> venv -> install -> window smoke test
make check              # lint + typecheck + tests
make run                # launch

.venv/bin/python -m pptmstr --fake              # UI only, no SDK, no cost
.venv/bin/python -m pptmstr --task "..."        # one real agent
.venv/bin/python -m pptmstr --task a --task b --cap 2   # concurrent, bounded
```

Reads auto-approve. Everything that writes, runs a shell, reaches the network or
spawns a sub-agent parks in the review queue until you answer it — as does any tool
this build has never heard of. With no operator attached the gate denies rather than
hanging.

The review loop is keyboard-driven: `j`/`k` to move, `a` approve, `r` reject with a
reason the agent sees, `e` edit the arguments and approve the corrected call,
`Shift+A` approve everything from the selected agent. Approving the whole queue
across every agent exists but costs a second click that states the count — it is
the one action here that can write something nobody read.

The transcript pane styles output by kind — reasoning, output, tool calls, results,
errors, compaction boundaries — with toggles for reasoning, wrapping and follow-tail,
plus a filter. Reasoning streams token by token at the root; sub-agent output does
not stream at all, and the pane says so rather than letting a quiet row read as a
stuck one.

Sub-agents appear as children in the tree, and a tool call made *inside* a
sub-agent parks against that sub-agent's row rather than its parent's. Concurrency
is bounded by `concurrency_cap`; over-cap sessions queue rather than being refused,
and the pool is shown in the status bar.

`bootstrap.sh` never installs system packages. If something system-level is
missing it prints the exact command and stops.

```sh
make probe              # stdlib-only environment diagnosis, no venv required
```

The probe is worth running first on an unfamiliar box. `imgui-bundle` ships its
own GLFW, but on Linux that build is **X11-only** and dlopens the X client
libraries at `glfwInit()` — so `pip install` always succeeds and a missing library
shows up as "the window doesn't open" with no traceback. The probe resolves all ten
by soname and prints the install line for your package manager. On a Wayland
session it needs XWayland.

Headless works: `make` routes window targets through `xvfb-run` automatically when
`DISPLAY` is unset.

## Architecture

Read [orchestrator-design.md](orchestrator-design.md) §1 (invariants) and §3
(threading) first. The short version:

```
┌─ Main thread ──────────────┐        ┌─ asyncio thread ─────────────┐
│  hello_imgui.run(...)      │        │  loop.run_forever()          │
│    ├─ drain intent queue   │        │    ├─ ClaudeSDKClient/session │
│    ├─ store.snapshot()     │        │    ├─ PreToolUse gate        │
│    ├─ build UI from snap   │        │    └─ emits intents          │
│    └─ render + idle        │        │                              │
└────────────────────────────┘        └──────────────────────────────┘
              └──────────── Bridge (two crossings) ────────────┘
```

- The **store** is the single source of truth. The UI owns no application state and
  builds every frame from exactly one snapshot, taken atomically at frame start.
- Records are immutable, so snapshotting is a reference swap rather than a deep
  copy — the difference between a Python UI that can rebuild every frame and one
  that cannot.
- **The main thread is the store's only writer.** The asyncio thread never touches
  it; it enqueues intents and the UI applies them between frames. That is why the
  store needs no lock.
- Transcripts are the one deliberate exception: append-only and internally
  synchronised, written directly by the asyncio thread, because routing a token
  stream through the intent queue would put one queue item per token on the UI
  thread's critical path.

### Layout

| Path | |
|---|---|
| [pptmstr/model.py](pptmstr/model.py) | Immutable records: `AgentRecord`, `Snapshot`, `ContextSnapshot` |
| [pptmstr/store.py](pptmstr/store.py) | Copy-on-write store; the single audit point for every mutation |
| [pptmstr/intents.py](pptmstr/intents.py) | Every way the world can change, as values |
| [pptmstr/bridge.py](pptmstr/bridge.py) | The only place threads and asyncio meet |
| [pptmstr/transcript.py](pptmstr/transcript.py) | Append-only per-agent output buffer |
| [pptmstr/pool.py](pptmstr/pool.py) | Bounded concurrency; over-cap sessions queue |
| [pptmstr/approval.py](pptmstr/approval.py) | Classification, summaries and diffs for the gate |
| [pptmstr/ui/](pptmstr/ui/) | Tree, review queue, transcript pane, shared widgets |
| [pptmstr/theme.py](pptmstr/theme.py) | Semantic colour roles; light, dark and high-contrast |
| [pptmstr/driver.py](pptmstr/driver.py) | One `ClaudeSDKClient` per session, translated into intents |
| [pptmstr/app.py](pptmstr/app.py) | Runner, docking layout, and the frame loop |
| [scripts/probe.py](scripts/probe.py) | Environment diagnosis; never installs anything |
| [scripts/screenshot.py](scripts/screenshot.py) | Renders the UI headlessly to a PNG |

## Development

```sh
make check      # lint + typecheck + test — what a CI gate would run
make format     # black, in place
make test
make bench      # idling CPU and cross-thread wake latency
make shot       # render the UI to a PNG
```

`make bench` on a Debian 12 / XWayland / Radeon box: idle costs **1.8% CPU against
10.5–13.5% at full speed**, and an agent going active takes the app back to 60fps —
which is what proves `any_active` drives idling rather than the app simply idling
all the time. Cross-thread wake latency is 69ms.

Formatting is `black`; `ruff` is lint-only here, so the two can never disagree
about the same file. `mypy` runs `strict` against the 3.11 floor rather than the
local interpreter — typechecking against the oldest supported version is what
catches a 3.12-only idiom before it reaches a Debian 12 box.

Design decisions are recorded in [orchestrator-design.md](orchestrator-design.md)
with their reasoning, including the ones that were later found to be wrong. If you
think one is wrong, engage with the recorded reasoning rather than starting over.
