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
| 4 | The approval gate | next |
| 5 | Idling | |
| 6 | Concurrency, sub-agent tree, review queue, batching | |
| 7 | Transcript pane | |

## Getting started

```sh
./bootstrap.sh          # probe -> venv -> install -> window smoke test
make check              # lint + typecheck + tests
make run                # launch

.venv/bin/python -m pptmstr --fake              # UI only, no SDK, no cost
.venv/bin/python -m pptmstr --task "..."        # one real agent
```

Until the approval gate lands (step 4) the driver auto-allows read-only tools and
**denies everything else**, with a reason the model sees. It is safe to point at a
real repository: nothing it does can write.

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
```

Formatting is `black`; `ruff` is lint-only here, so the two can never disagree
about the same file. `mypy` runs `strict` against the 3.11 floor rather than the
local interpreter — typechecking against the oldest supported version is what
catches a 3.12-only idiom before it reaches a Debian 12 box.

Design decisions are recorded in [orchestrator-design.md](orchestrator-design.md)
with their reasoning, including the ones that were later found to be wrong. If you
think one is wrong, engage with the recorded reasoning rather than starting over.
