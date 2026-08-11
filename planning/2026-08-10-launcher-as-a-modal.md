# The launcher becomes a modal

**Dated:** 2026-08-10 · **Status:** built · **Supersedes:** the omnibox decision in
`2026-08-10-layout-proposals.md` §"Launch is a single omnibox line, always present,
never a tab", and the reasoning that leans on it in
`2026-08-10-research-sessions-under-the-inbox.md:181-183`

Starting a session is now `Ctrl+N` — or **Session ▸ New Task…** — opening a modal
over whichever layout is up. The `LAUNCH` pane, its `OmniSpace` split, and
`compose.draw_omnibox` are gone, along with the already-dead `compose.draw_launcher`
the two docs above still cited as the live implementation.

## Why the recorded decision was overturned

The omnibox argument was sound on its own terms and is worth restating before
disagreeing with it: dispatch is the first step of the operator's loop, it had been
a background tab behind the log, and burying it again would guarantee that starting
work meant first going to find where starting work lived. Three fields and a button
do not need a form.

Two things that argument did not account for:

**The conceptual unit is intent per session, not per screen.** A task, the
directory it runs in, and the model that runs it are properties of one session. An
always-present strip implies dispatch is an ambient property of one *arrangement*
of the screen — and it behaved that way, because `_focus_layout` never included the
pane. Starting work while attending to a running session meant leaving the session
first. A modal is reachable identically from both layouts, which is what a
per-session intent actually needs. This is the argument that decided it; the space
saving is a consequence, not the reason.

**The counter was already duplicated.** The omnibox's `N/cap live` is the same two
numbers the status bar prints as `N/cap sessions`, and both were on screen at once.
The status bar keeps it and has gained the at-cap warning colour the omnibox had —
that reading is the one an operator cannot afford to skim, because at cap a launch
silently becomes a queue entry. The modal does not repeat the count; it says only
what the status bar cannot, which is what happens to *this* launch.

The "turns an omnibox back into a form" objection to a budget field
(`…under-the-inbox.md:183`) loses its premise here. It does **not** follow that a
budget field is now wanted — see below.

## What the modal exposes

Task, working directory, model. Exactly what the omnibox had, with the task box
given the room it always deserved: multi-line, `Enter` launches, `Ctrl+Enter`
breaks a line.

Deliberately **not** exposed, and not merely deferred:

- `permission_mode` — the whole approval-parking design assumes `dontAsk`. A
  launcher toggle would quietly disable the thing the application exists to do.
- `interactive=False` — real on `AgentSession`, but it *denies* tools rather than
  parking them. A footgun next to a launch button.
- `max_budget_usd` — still the open question from
  `2026-08-09-research-session-initiation.md:115`. Unchanged by this doc: it needs
  threading through `_launch` → `AgentSession.__init__` → `_options()`, which is
  driver semantics, not UI surface, and was kept out to keep this change to one
  concern. The `…under-the-inbox.md` conclusion that it belongs in FOCUS/HEALTH
  next to the spend figure it constrains still stands on its own merits.

A cwd picker over known project directories is the next thing worth building here.
`cwd` is the FLEET rail's grouping key (`ui/projects.py:83-100`), so a typo does not
fail — it quietly files the session under a project that does not exist.

## Mechanics worth not rediscovering

- **The modal draws from `post_render_dockable_windows`, not a panel.** A popup
  opened inside a docked window is scoped to that window, and the panel set differs
  between layouts, so a launcher parented to a TRIAGE pane vanishes on the switch
  to FOCUS.
- **`imgui.shortcut(chord, route_global | route_over_active)`, not the
  `want_capture_keyboard` guard.** Every other key handler here bails when a text
  field has focus, which is right for bare letters — typing "a" into a rejection
  reason must not approve anything — but it would make `Ctrl+N` dead in the reply
  box of a session that just asked for something new. This is the first use of
  ImGui's input routing in the codebase and the pattern to copy for future chords.
- **Key enum members do not OR.** `imgui.Key.mod_ctrl | imgui.Key.n` raises; the
  chord is built from `int()`.
- **`_handle_layout_keys` defers to an open modal.** `want_capture_keyboard` covers
  the common case but not a modal whose focus sits on the model combo, where `Esc`
  would otherwise dismiss the modal *and* change layout in the same frame. It reads
  last frame's `is_open`, which is the value matching the frame the operator was
  looking at when they pressed the key.
- **`modal_window_dim_bg` is black, not a palette role.** It is the one colour in
  `theme.py` that is not, because a dim has to darken in all four themes and every
  palette role available is the page colour.

## Verified

`tests/test_launcher.py` covers the state machine headlessly. The draw-time
behaviour — chord routing over an active field, `Esc` closing without changing
layout, `Enter` reaching `_launch` with the draft, the draft surviving a cancel —
was verified by injecting real key events through `io.add_key_event` against a live
window, and the modal was screenshotted in both layouts and on the `light` and
`dark` palettes.
