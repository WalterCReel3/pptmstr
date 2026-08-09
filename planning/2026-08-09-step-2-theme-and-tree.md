# Step 2 — theme layer + agent tree pane

**Dated:** 2026-08-09 · **Status:** not started · **Follows:** design §8 step 2, §6.1

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

## Open questions for this step

- **Where theme preference persists.** It must survive restart, so not
  `NodeViewState`. Proposal: a small settings file under `XDG_CONFIG_HOME`,
  written on change. Orbital's lesson is to use XDG from day one rather than
  repo-root-relative paths, which break silently once installed as a wheel.
- **Whether the context column is a bar or a number.** §2.4 says the actionable
  value is distance to compaction, not percent — a bar implies a budget being
  spent, which is the framing that section explicitly rejects. Leaning toward
  a number plus a pressure-coloured dot.
- **Fake driver fidelity.** Enough to exercise nesting and approval parking;
  not a simulator. It exists to make the pane's states reachable without burning
  tokens, and it should be deleted once step 3 lands rather than maintained.

## Verification

`make check` stays green. The window smoke test already proves the GL stack on
this box; step 2 adds a frame-time check that idling actually engages (§4.2) —
measure idle CPU before and after wiring `any_active`, per §8 step 5, even though
the wiring itself is step 5.
