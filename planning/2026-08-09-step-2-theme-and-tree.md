# Step 2 — theme layer + agent tree pane

**Dated:** 2026-08-09 · **Status:** decisions settled, not yet started · **Follows:** design §8 step 2, §6.1

Step 1 (store, snapshot, intents, Bridge) is done and tested. This is the next
increment: the first pixels, driven by a fake driver rather than the SDK.

## Scope

1. **`pptmstr/theme.py`** — the role table and palette proxy, before any panel
   exists. Three themes shipped together: `dark`, `light`, `high_contrast`.
2. **`pptmstr/ui/tree.py`** — the agent tree pane. Fixed-width columns: state,
   topic, model, context, elapsed.
3. **`pptmstr/app.py`** — RunnerParams, docking layout, the §4.1 frame loop.
4. **A fake driver** — emits intents on a timer to exercise state transitions,
   sub-agent nesting, and approvals without touching the SDK.

## Why the theme comes first

Retrofitting semantic colour means auditing every draw call. Writing the role
table first costs an afternoon; adding it after three panels exist costs a day and
misses cases. Shipping `light` and `high_contrast` alongside `dark` is what proves
the table contains no literals — one theme cannot demonstrate that, because a
literal and a role look identical until something switches.

## Decisions already recorded (do not re-litigate)

- Colour is a semantic layer. Panels reference roles (`P.state_awaiting`), never
  hex. §6.1.
- Hue is never the only channel. Every state carries an icon and a text label;
  diffs keep their `+`/`-` gutter. This is what makes `high_contrast` a theme
  switch rather than a second UI. §6.1.
- The palette is reached through a `__getattr__` proxy, never a rebindable module
  global — `from .theme import P` copies the reference and the switch half-works.
  §6.1, trap 8.
- `ImGuiStyle` is set on theme change, not per frame.
- Widget IDs come from `NodeId`, never row index. I6, trap 2.
- Fixed-width table columns, not auto-fit: auto-fit re-fits over three frames
  every time rows change, and these rows change constantly. Trap 1.

## Settled 2026-08-09

**Theme preference persists under `XDG_CONFIG_HOME`**, in our own settings file
rather than piggybacking hello_imgui's ini. That ini is hello_imgui's format for
hello_imgui's concerns (docking layout, window geometry), and theme will not be
the only thing needing persistence — the concurrency cap and per-node trust lists
land in the same file later. Repo-relative paths are the thing being avoided:
they break silently once installed as a wheel and stay invisible for as long as
every launch happens from the source tree.

**The context column is plain text, read as a compaction countdown without
labelling it as one.** A bar implies a budget being spent, which §2.4 rejects.
The number is small and falling; the label is not spent on saying so. Discovery
goes in a hover tooltip, not the column.

**Shorthand, where width is tight: a ring throbber** — a grey ring that fills as
headroom is consumed, colour tracking blue → orange → red.

This already satisfies §6.1's "never hue alone": the fill fraction is a second,
independent channel, which matters most for the orange/red pair since that is the
distinction that collapses under red-deficiency. Do not add a variant where the
ring is a fixed-size coloured dot — that drops the redundant channel and is
exactly the failure §6.1 exists to prevent.

### Two states the ring must not misreport

Both come out of `ContextSnapshot`, and both would otherwise render as a
confident, wrong number.

1. **No threshold to count down to.** `tokens_until_compaction` returns `None`
   when autocompact is off or the CLI reported no threshold, and `model.py`
   already forbids falling back to `max_tokens` — that silently answers a
   different question. So the column needs a defined alternate state, not a
   default: show occupancy explicitly marked as such (a hollow ring, not a
   partly-filled one), so it can never be misread as headroom.

2. **After compaction the countdown resets, but the damage does not.** A freshly
   compacted session has a nearly empty window, so the ring would fill green-blue
   and read as healthy at the exact moment `ContextPressure` is stickily
   `COMPACTED`. Ring fill and pressure colour are measuring different things and
   will contradict each other here.

   Resolution: the ring reports current headroom honestly, and compaction count
   rides alongside it as a persistent mark (`×2`). Both facts stay visible and
   neither is overwritten. Per §2.4 the count is the stronger retire signal, so it
   is the one that must not be erased by a healthy-looking ring.

**Fake driver fidelity** — enough to exercise nesting and approval parking; not a
simulator. It exists to make the pane's states reachable without burning tokens,
and it gets deleted when step 3 lands rather than maintained.

## Verification

`make check` stays green. The window smoke test already proves the GL stack on
this box; step 2 adds a frame-time check that idling actually engages (§4.2) —
measure idle CPU before and after wiring `any_active`, per §8 step 5, even though
the wiring itself is step 5.
