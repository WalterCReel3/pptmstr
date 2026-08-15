# pptmstr — Puppet Master

**Run a fleet of Claude agents. Hold every string.**

<!-- HERO: TRIAGE layout, full window, a frame composed to fill it — fleet rail
     with several healthy sessions and one team, a populated review queue, DETAIL
     showing an expanded diff. No failure state front and centre.
     ![The TRIAGE layout: fleet rail, review queue, detail pane](docs/images/hero.png) -->

A multi-agent orchestrator with an immediate-mode UI. It runs N Claude agent
sessions concurrently, keeps every one of them legible at a glance, and lets
nothing reach your disk that you did not read first.

Reasoning, cost, context health and the next tool call are on screen while they
happen. Every write parks in front of you as a diff — approve it, reject it with a
reason the model sees, or edit the arguments and approve the corrected call.

Built on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)
for agent execution and [Dear ImGui](https://github.com/pthom/imgui_bundle)
(via `imgui-bundle` / hello_imgui) for the UI.

---

## Nothing is hidden, on purpose

Most agent tools spend their design budget on looking effortless: a spinner, a
progress line, a diff at the end. That is the right call for a product that wants
to feel like magic and the wrong one for work you are accountable for. At minute
forty of a session going sideways, "working…" tells you nothing, and a diff that
lands at the end lands long after intervening would have been cheap.

pptmstr goes the other way. What the fleet is doing is on screen while it is doing
it:

- **Reasoning streams as it is produced**, at the root, rather than being
  reconstructed once the turn ends. It arrives as a *summary* — current models never
  return the raw chain of thought — and the pane says so instead of implying more.
- **Every mutating call is shown before it runs**, as a diff, with the arguments
  editable in place.
- **Context is a health signal, not a budget.** The question it answers is "is this
  session about to compact, and should I retire it?" — not "how much have I spent."
  Those are different axes and live in different widgets.
- **Sub-agents are a visible tree** inside their session's card, and a call made
  *inside* a sub-agent parks against that sub-agent rather than its parent.
- **Agent-to-agent messages pass the same gate as writes**, so a concern can be read,
  rejected with a reason, or edited before it is delivered.

Density is not the same as a wall of numbers, and the layouts carry that weight:
TRIAGE ranks what is waiting on you, FOCUS drops to a single session. Panes are
expected to say what they *cannot* show — sub-agent output does not stream, so the
transcript says so rather than letting a quiet row read as a stuck one.

The design premise is that **the operator is the bottleneck on purpose**, so the
interesting engineering is in making that bottleneck cheap. Approval is a runtime
state, not a prompt: an agent awaiting review is idle rather than spinning, and the
whole app drops to idle frame rate while it waits on you.

Non-goals: i18n, multi-user, remote access.

<!-- GATE: the review queue with one call expanded — an Edit diff, the reject and
     edit affordances visible, the keyboard hint line legible.
     ![A tool call parked in the review queue as a diff](docs/images/gate.png) -->

## What works today

The build order in [orchestrator-design.md](orchestrator-design.md) §8 is finished:

| Step | | |
|---|---|---|
| 1 | Store, snapshot, intents, Bridge | **done, tested** |
| 2 | Theme layer + agent tree pane against a fake driver | **done, tested** |
| 3 | One real agent over the SDK | **done, tested** |
| 4 | The approval gate | **done, tested** |
| 5 | Idling | **done, measured** |
| 6 | Concurrency, sub-agent tree, review queue, batching | **done, tested** |
| 7 | Transcript pane | **done, tested** |
| 8 | Work templates + inter-agent message bus | **done, tested, verified live** |

What comes next comes from using the thing, and lives in [planning/](planning/) as
dated scope snapshots. Currently open, roughly in the order they cost something:

| | |
|---|---|
| [The board has no surface](planning/2026-08-12-the-board-has-no-surface.md) | a team's tasks and concerns are in the store and drawn nowhere |
| [`needs_you` sorts two clocks](planning/2026-08-12-needs-you-sorts-two-different-clocks.md) | the inbox is not oldest-first; approvals always sort last |
| [The README shows nothing](planning/2026-08-13-the-readme-cannot-show-what-it-sells.md) | captures are not reproducible, so the screenshots above are placeholders |
| [Dogfooding notes](planning/2026-08-09-dogfooding.md) | the friction log, and the question a team run is worth doing to answer |

Read [STYLE.md](STYLE.md) before adding a record, an intent, or a projection.

## Getting started

```sh
./bootstrap.sh          # probe -> venv -> install -> window smoke test
make check              # lint + typecheck + tests
make run                # launch

.venv/bin/python -m pptmstr                     # start empty, launch from the UI
.venv/bin/python -m pptmstr --task "..." --cwd ~/some/project
.venv/bin/python -m pptmstr --task a --task b --cap 2   # concurrent, bounded
.venv/bin/python -m pptmstr --fake              # UI only, no SDK, no cost
```

Reads auto-approve. Everything that writes, runs a shell, reaches the network or
spawns a sub-agent parks in the review queue until you answer it — as does any tool
this build has never heard of. With no operator attached the gate denies rather than
hanging.

Start a session with `Ctrl+N`, or **Session ▸ New Task…**, from either layout. Each
carries its own working directory and model, so one window can drive work across
several projects. `Ctrl+Enter` launches; `Enter` breaks a line in the task box;
`Esc` dismisses without discarding the draft.

The review loop is keyboard-driven: `j`/`k` to move, `a` approve, `r` reject with a
reason the agent sees, `e` edit the arguments and approve the corrected call,
`Shift+A` approve everything from the selected agent. Approving the whole queue
across every agent exists but costs a second click that states the count — it is the
one action here that can write something nobody read.

### Sessions are conversations, not one-shot runs

A finished turn leaves the session connected and marks it `YOUR TURN` — send another
prompt, interrupt the current turn, or close the session to reclaim its subprocess.
An agent that asks a question is therefore something you can answer rather than
something that looks finished.

<!-- CONVERSATION: FOCUS layout on the AWAITING_INPUT session — the transcript
     ending in the agent's question, the prompt box below it.
     ![An agent asks a question and waits for an answer](docs/images/conversation.png) -->

Every prompt box in the application sends on `Ctrl+Enter` and takes `Enter` as a
newline — a prompt is routinely several lines, and the key that would send it
half-written is the one pressed most often.

The transcript pane styles output by kind — reasoning, output, tool calls, results,
errors, compaction boundaries — with toggles for reasoning, wrapping and follow-tail,
plus a filter. Concurrency is bounded by `concurrency_cap`; over-cap sessions queue
rather than being refused, and the pool is shown in the status bar.

## Teams

A session can run as a **team**: a lead plus named worker roles, chosen in the
launcher or with `--template`. `solo` is the default and behaves exactly as a session
did before teams existed.

| | |
|---|---|
| `solo` | one agent, no team |
| `feature` | lead plans, `builder` implements, read-only `reviewer` attacks it |
| `research` | coordinator frames, `investigator` builds the case, `skeptic` refutes it |

Agents coordinate over an **in-process message bus** rather than the harness's own
channel, because that is what makes a message reviewable: `post_concern` parks in the
review queue like any write, so a concern can be read, rejected with a reason, or
edited before it is delivered. A shared task board carries dependency edges — blocked
work becomes claimable the moment its dependencies complete, and workers claim rather
than being assigned.

The sender on a concern is **stamped by the approval gate**, never taken from the
tool's arguments. An in-process MCP handler is told only the tool name and its
arguments — no session, no agent — so a sender the model wrote would be a sender the
model chose. The gate is the only participant that knows, which makes it the bus's
authentication layer as well as its review point.

Review roles are given read-only tools on purpose. A reviewer that can quietly fix
what it was asked to find stops reporting it.

## Environment

`bootstrap.sh` never installs system packages. If something system-level is missing
it prints the exact command and stops.

```sh
make probe              # stdlib-only environment diagnosis, no venv required
```

The probe is worth running first on an unfamiliar box. `imgui-bundle` ships its own
GLFW, but on Linux that build is **X11-only** and dlopens the X client libraries at
`glfwInit()` — so `pip install` always succeeds and a missing library shows up as
"the window doesn't open" with no traceback. The probe resolves all ten by soname and
prints the install line for your package manager. On a Wayland session it needs
XWayland.

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
| [pptmstr/ui/](pptmstr/ui/) | Fleet rail, inbox, detail, health, transcript pane, launcher, shared widgets |
| [pptmstr/theme.py](pptmstr/theme.py) | Semantic colour roles; light, dark, high-contrast, and six discretionary |
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

`make bench` on a Debian 12 / XWayland / Radeon box, at the default `fps_idle` of
9.0 (`settings.py`; settable in the settings file or with `--fps-idle`): a session
parked and waiting on you costs **2.0% CPU against 13.3% at full speed**, and an
agent going active takes the app back to 60fps — which is what proves `any_active`
drives idling rather than the app simply idling all the time. Cross-thread wake
latency is 69ms.

The **cold-start screen costs more**, and it is the one number here with a
condition attached. With no sessions at all, the NEEDS YOU pane fills with an
animated splash, and resting on it costs 4.7% rather than 2.1%. That is the whole
of the difference — seed a single parked session and TRIAGE measures 2.1% against
FOCUS's 2.0%, so the animation costs nothing once you have started anything. It is
a cost you pay only on an empty application, and only until you press Ctrl+N.
Numbers from `scripts/bench_idle.py`, twelve runs per layout; see
[orchestrator-design.md](orchestrator-design.md) §4.3 for the full table and the
conditions.

Formatting is `black`; `ruff` is lint-only here, so the two can never disagree about
the same file. `mypy` runs `strict` against the 3.11 floor rather than the local
interpreter — typechecking against the oldest supported version is what catches a
3.12-only idiom before it reaches a Debian 12 box.

Design decisions are recorded in [orchestrator-design.md](orchestrator-design.md)
with their reasoning, including the ones that were later found to be wrong. If you
think one is wrong, engage with the recorded reasoning rather than starting over.
