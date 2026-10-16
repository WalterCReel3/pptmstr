# Everything that persists is on one screen, and what is not on it is a constant on purpose

**Dated:** 2026-10-08 ·
**Status:** built on `adv-text-experiment`, uncommitted at the time of writing ·
**Origin:** the operator, 2026-10-08 — *"I think we're now at a point where a
comprehensive settings dialog is needed. Per pane setting for the font selections is
what's driving me that direction."* Then, on scope: *"I don't want settings to get
too fiddly."* ·
**Read with:** `planning/2026-10-07-which-fonts-ship.md` §0, where the second sentence
was first said — about line spacing, and it is the reason line spacing is not below —
and `planning/2026-08-10-launcher-as-a-modal.md`, whose mechanics this reuses and in
two places departs from ·
**Measured with:** imgui-bundle 1.92.900 (Dear ImGui 1.92.9) under
`scripts/xvfb_shot.sh` (Xvfb, llvmpipe, 1500x900 window), driving the real application
with injected key and mouse events from a throwaway driver under the gitignored
`shots/dialog/` ·
**Looked at:** the PNGs named in §6

---

## 1. What "comprehensive" means here

Every field of `Settings` is a control in the dialog or is named as read-only, and
`tests/test_settings_dialog.py::test_every_persisted_field_has_one_place_in_the_dialog`
fails when a field is in neither list. That is the whole claim. It is **not** "every
constant is exposed": the two sentences from the operator pull in opposite
directions, and the line between them is *what is already persisted*. The dialog
added one setting (§3) and exposed no constant.

`Ctrl+,` or **Text ▸ Settings…** opens it. One screen, no tabs, no Apply, no cancel.

**Amended 2026-10-15:** "one screen, no tabs" no longer holds. The sections are pages
behind a left-side navigation list, at the operator's request
([`2026-10-15-ui-rework.md`](2026-10-15-ui-rework.md), "Settings Dialog"). Everything
else in this record stands: no Apply, no cancel, apply and save on click, a defaults
button per section, every field either a control or listed read-only. The same day,
`context_mode` (below) stopped being a control: the render mode is no longer chosen
(U2 of that record), the field is still loaded and saved so older files round-trip,
and nothing reads it. Reasoning is a setting, `show_reasoning`, on the Panes page.

| Section | Rows | Field |
|---|---|---|
| Appearance | theme; text size | `theme`, `typeset_scale` |
| Text | typeset text; proportional family; one row per role: face, size, a sample | `typeset`, `typeset_family`, `typeset_roles` |
| Panes | a switch per pane; where the context log starts | `typeset_panes`, `context_mode` |
| Input and sessions | wrap composers; sessions at once; sub-agents a session; idle fps | `wrap_inputs`, `concurrency_cap`, `subagent_cap`, `fps_idle` |
| MCP connectors | a list, read-only, and the path of the file to edit | `admitted_connectors` |

Each of the first four has a `defaults` button that restores that section's fields
and no other's. It is dead while the section is at its defaults, judged by what is
in force and not by how the tuple is spelled: prose set monospace and back is
`RoleChoice("prose", "proportional")`, which is not `()` and is the default.

## 2. What is deliberately a constant in code

| | Where | Why it is not a setting |
|---|---|---|
| Line spacing | `typeset.LEADING` | The operator: *"let's hide that setting internally and just make it easy to tweak via code for now. I don't want settings to get too fiddly."* |
| Whose advances lay text out | `typeset.METRICS` | Changing it reloads every face and changes what "aligned" means; it is the experiment's instrument (`scripts/compare_metrics.py`), not a preference. |
| The monospace family | Inconsolata | Nothing else monospace ships. The dialog says so beside the proportional family instead of drawing a list of one. |
| Which roles may be proportional | `typeset.SLOTS` | Code, diffs, tool output and arguments have columns that are content. Their rows show `monospace` as text with no control, and say why on hover. |
| The size steps and the text sizes | `typeset.STEPS`, `typeset.SCALES` | Whole pixels are what ImGui bakes. A list is also what makes a change one click (§4). |
| How a role's long lines meet the edge | `typeset._MULTILINE` | A property of what the text *is* (a traceback is not wrapped), not of how the operator reads. |
| The heading scale, block gaps, the frame budget | `typeset._HEADINGS`, `_*_GAP_EM`, `set_budget` | Measured values with their measurements beside them. |
| The context log's caps and windows | `transcript_pane` | Frame-cost bounds. |
| The watchdog thresholds | `app._*_S` | Not preferences. |
| Per-launch choices | the launcher | Model, team, gate policy, containment and a per-launch sub-agent cap belong to a session, which is that modal's argument and is not reopened here. |

## 3. One setting was added: where the context log starts

`Settings.context_mode` is a `RenderMode` value, or empty for "this build's"
(`transcript_pane.DEFAULT_MODE`). The task asked for "the context log's default mode"
in the Panes section and there was no such field: the mode was session state on
`TranscriptState`, chosen by the pane's radio buttons and gone at exit.

Empty rather than `"rich"` as the default, so that a build which changes
`DEFAULT_MODE` — the docstring there states the rule under which TYPESET becomes it —
reaches every file that never chose. The pane's buttons still choose for the
session; the setting is where it starts. Changing the setting clears the session's
pick, because otherwise the control would do nothing visible until the next launch
for exactly the operator who had touched the pane. `typeset` is listed but not
offered while the seam is not drawing the context log, where it would silently be
WRAP.

The pane is told the setting on every draw (`draw(start_in=)`) and keeps no copy
that can go stale; `STYLE.md` §1, "Derive; do not store".

## 4. Decisions

**Applied on the click, saved on the click, no Apply and no cancel.** The task said
not to hide the proportional family's cost behind an Apply button unless a
measurement gave a reason. None did: 268 to 302 ms for the frame of that click over
four runs, against an ordinary frame of about 50 ms under llvmpipe, and the thirty
frames after it no slower than usual (worst 52 to 61 ms). The seam's docstring has 430 to
510 ms for the same change with a 30-line document in view; the figure moves with
what is on screen. Without an Apply there is no second copy of the settings, which
is the thing that would fall out of step.

**Every control is a checkbox or a list.** No slider and no text field, so nothing
can be applied on a hover or on a drag tick, and a list reports a change only for
an entry other than the one in force (`_choice`). The three numbers — two caps and
the idle frame rate — are lists of offered values for that reason; a value the list
does not hold (hand-edited, or from `--cap`) is shown as its own entry and kept.
Thirty frames of the mouse held over the family list moved the seam's epoch by 0.

**One function applies a setting** (`app._apply_settings`). The menu's three
remaining entries and the dialog both end there. What has to happen — tell the seam,
mark the theme dirty, resize the pool on the loop, set the runner's idle rate, clear
the pane's pick — is read off the difference between the old settings and the new,
not passed in by the caller, so no caller can save a setting and forget its effect.
Equal settings do nothing: no write, no `configure`.

**The dialog is three layers** (`ui/settings_dialog.py`): a `Change` union and a
pure `changed(settings, change)`; a pure `build(settings, facts)` giving a `Model` in
which every option *carries* the change that picking it makes; and a `draw` that
renders a model and hands back what was clicked. The draw knows no setting. It does
not call `store.apply` and has no access to the store.

**The chord is `Ctrl+,` and it does not fire while an item is active.** It collides
with nothing: `review.handle_keys` and `_handle_layout_keys` both stand down for any
chord, the launcher owns `Ctrl+N`, ImGui owns `Ctrl+Tab` and the editing chords, the
composers `Ctrl+Enter`, the typeset views `Ctrl+A` and `Ctrl+C`. This departs from
the launcher on purpose. `launcher-as-a-modal` argues `route_over_active` because
`Ctrl+N` is wanted most in a reply box; a settings dialog arriving over a half-typed
reply helps nobody. `route_global` alone does **not** give that — measured: ImGui
grants a global route over an active `InputText` for a chord the field does not use
— so `handle_shortcut` asks `is_any_item_active()` first. The chord also closes the
dialog. It is not offered while the launcher is open: two modals opened from the
root replace each other, and the launcher's holds a draft.

**Escape over an open list closes the list.** `hello_imgui` runs without keyboard
navigation here (`nav_enable_keyboard` is off, read from the live window), so ImGui
does not close a combo on Escape and the dialog does it; with navigation on ImGui
closes it during `NewFrame`. The dialog therefore closes on Escape only when no
list was open at the end of the frame before, which is right under both.

**The menu keeps what was one click.** Theme stays a menu. **Text** keeps
`Wrap composers` and `Typeset text` — the second is the experiment's instrument, flip
and look — and its four submenus (Panes, Faces, Text size, Role sizes) are replaced
by `Settings…`.

## 5. Left out, and why

- **A connector editor.** `admitted_connectors` are records with an account
  identifier, and admitting one decides what every session can reach
  (`planning/2026-09-27-containment-took-the-operators-mcp-tools-with-it.md`). No
  editor exists to embed. The dialog lists name and URL — not the identifier — and
  the path of the file. `settings.py` points at `scripts/verify_mcp_status.py` for
  re-reading an identifier and **that script is not in the tree**.
- **A per-pane choice of face.** The operator's first sentence asks for it and the
  roles record (`which-fonts-ship` §0) records the answer they chose instead:
  *"Another option is to set the font per _role_ … yeah. Let's do that."* What is per
  pane is the switch.
- **A sample for a role in a face that is not loaded.** With the library absent the
  sample column is a dash: the seam raises when asked to draw with no faces.
- **`Settings…` under Session or in a menu of its own.** One entry, in the menu
  whose submenus it replaced.
- **Typing a number.** See "Every control is a checkbox or a list".

## 6. What the pictures show

All 1500x900, under `shots/` (gitignored), the real application with `--fake`.

- `settings-default.png` — the dialog over TRIAGE at 900x746. Every sample drawn.
  The diff sample is four rows tall because a diff view has a file line and a hunk
  line of its own, so that row is three times the height of the others.
- `settings-role-changed.png` — prose monospace, headings +3 px, code blocks −2 px,
  each seen in its sample with the dialog still open. A monospace sample has no
  italic: `approved` is upright, because Inconsolata ships none.
- `settings-droid.png` — the family changed. With prose monospace from the step
  before, Droid Sans is visible only in the table and row-title samples.
- `settings-context-list.png` — the "log starts in" list open in the light theme at
  125%, `typeset` greyed because CONTEXT is switched off. The open list covers the
  idle-fps control beneath it.
- `settings-light-125.png` — light theme, 125%: 777 px tall where it opened, its
  foot at 854 of 900.
- `settings-largest.png` — 125% with every role at +4 px and prose monospace, the
  tallest it gets: 845 px. It had grown past the foot of the window from where it
  was centred and was lifted to y=55 (`_kept_in_view`), so the close button is on
  screen; its last row sits over the status bar. The prose sample no longer fits
  one line and wraps.
- `settings-then-launcher.png` — `Ctrl+N` pressed over the open dialog: the launcher
  replaced it, and the settings state read closed. `Ctrl+,` over the launcher did
  nothing.
- `settings-unavailable.png` — the library blocked from importing: every typeset row
  greyed and present, the reason wrapped onto a second line beside the switch, a
  dash for each sample. The pane ticks stay ticked while greyed, which is what the
  settings say.
- `settings-closed.png` — after Escape: TRIAGE, not a layout change.

## 7. Unverified

- **No person has looked at it on a real display.** Every picture is Xvfb at 1x with
  a software rasteriser.
- **A window shorter than 845 px** with the largest text. The dialog fits 1500x900
  at every setting (§6); in a shorter window ImGui clamps it and it scrolls, with
  the title bar's close box, Escape and the chord still closing it. Reasoned, not
  seen.
- **Escape held down** to close a list. The dialog ignores key repeat for its own
  close, and no test holds a key long enough to say so.
- **`fps_idle` applied live** sets `runner.fps_idling.fps_idle`, and the test asserts
  the assignment. That `hello_imgui` reads the field every frame rather than once
  was not measured.
- **A lowered session cap** was applied (the pool read 6 after the click) with no
  session running under a real driver.
- **The chord under a window manager** that binds `Ctrl+,` itself.
- **The half-second** was timed under llvmpipe, where every frame is 50 ms.
