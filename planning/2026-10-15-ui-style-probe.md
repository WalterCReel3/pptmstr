# The style probe: three looks on the same screens, for the operator to pick

**Dated:** 2026-10-15 · **Status:** a probe; nothing here is applied to `theme.py` · **Answers:**
U10 and the prototype half of U9 in `2026-10-15-ui-rework.md`, against the operator's notes
under "Cohesion and UI workshopping" in that record

## What to look at

`shots/style-probe/<variant>/<screen>.png` (gitignored; rendered locally). Every variant is
shot on the same six screens, in the same order, from the same `--fake` fleet:

| screen | what it is |
|---|---|
| `triage.png` | TRIAGE layout as it opens: fleet rail, NEEDS YOU with its first row open, DETAIL |
| `board.png` | TRIAGE with the cursor on the fake team and BOARD in front |
| `focus.png` | FOCUS layout on the fake report session, typeset, wheeled up to its tool calls |
| `terminal-block.png` | FOCUS with the terminal-style tool block **mock-up** drawn over SESSION |
| `settings.png` | the settings dialog open |
| `launcher.png` | the launcher (New Task) open |

Regenerate with `.venv/bin/python scripts/style_probe.py --all` (each variant on its own Xvfb
through `scripts/xvfb_shot.sh`); `--describe` prints the table below from the code.

The screenshots catch other tasks' work as it stood when they ran: the settings dialog
already shows `settings-nav`'s left navigation and the board `board-header`'s filter chips,
neither of which is this probe's.

## How a variant is applied

`scripts/style_probe.py` wraps `theme.apply_style` in its own process so the variant's values
land after it every time the app pushes its palette (a theme switch included), and wraps
`app._draw_overlays` for the two drawings no style value reaches. `theme.py` and `pptmstr/` are
not edited. Colours are written as `mix(base, over, alpha)` — `theme.mixed` over two palette
roles, opaque — so a variant follows the theme in force rather than freezing dark's values.

## The variants

### `current` — the baseline

No overrides. What `theme.apply_style` sets today, for reference: `scrollbar_size 12`,
`scrollbar_rounding 0`, `frame_rounding 2`, `grab_rounding 2`, `tab_rounding 0`,
`frame_padding (7, 4)`, `frame_border_size 1`, `item_spacing (8, 5)`, `window_padding (8, 8)`,
`button = panel_alt` (the same fill as a dock's frame well).

Images: `shots/style-probe/current/{triage,board,focus,terminal-block,settings,launcher}.png`

### `tidy` — the smallest change that answers every note

| value | current | tidy |
|---|---|---|
| `scrollbar_size` | 12 | 14 |
| `scrollbar_rounding` | 0 | 7 (fully round ends) |
| `scrollbar_padding` | 2 (ImGui default) | 2 |
| `grab_rounding` | 2 | 3 |
| `frame_rounding` | 2 | 3 |
| `frame_padding` | (7, 4) | (8, 5) |
| `tab_rounding` | 0 | 3 |
| `popup_rounding` | 0 (ImGui default) | 3 |
| `Col_.button` | `panel_alt` | `mix(panel_alt, accent, 0.16)` |
| `Col_.button_hovered` | `panel_alt` + accent 0.28 translucent | `mix(panel_alt, accent, 0.32)` |
| `Col_.button_active` | `panel_alt` + accent 0.45 translucent | `mix(panel_alt, accent, 0.48)` |

Images: `shots/style-probe/tidy/{triage,board,focus,terminal-block,settings,launcher}.png`

Tradeoffs:

- **Borders are kept.** A button is told from a well by its tinted fill alone; the outline
  the operator called out is still there. This is the variant that cannot surprise anyone.
- **Tabs grow with every frame.** A dock tab is frame-height (font + 2 × `frame_padding.y`),
  so 24 → 26 px is +8 %, short of the 10–15 % asked for, and it is paid for by every input,
  combo and button growing by the same 2 px. There is no tab-only height in ImGuiStyle.
- **Tabs are still ImGui's shape**: `tab_rounding` rounds the two upper corners only and the
  tab still sits on the lane's floor. Uppercase labels stay (see "Lowercase labels" below).

### `soft` — flat, rounder, inset tabs

| value | current | soft |
|---|---|---|
| `scrollbar_size` | 12 | 16 |
| `scrollbar_rounding` | 0 | 8 (fully round ends) |
| `scrollbar_padding` | 2 | 3 |
| `grab_rounding` | 2 | 5 |
| `frame_rounding` | 2 | 5 |
| `frame_padding` | (7, 4) | (9, 6) |
| `frame_border_size` | 1 | 0 |
| `item_spacing` | (8, 5) | (8, 6) |
| `tab_rounding` | 0 | 5 (used by the drawn tabs) |
| `popup_rounding` | 0 | 4 |
| `Col_.button` | `panel_alt` | `mix(panel_alt, accent, 0.2)` |
| `Col_.button_hovered` | as above | `mix(panel_alt, accent, 0.36)` |
| `Col_.button_active` | as above | `mix(panel_alt, accent, 0.52)` |
| `Col_.frame_bg` | `panel_alt` | `mix(bg, panel, 0.35)` (a well darker than the surface) |
| `Col_.scrollbar_grab` | `border` | `mix(bg, text_dim, 0.45)` |
| tabs | ImGui's | drawn by `draw_inset_tabs`: four rounded corners, inset 3 px into the lane, lowercase, label centred between equal top and bottom margins, a 2 px accent rule under the focused node's selected tab |

Images: `shots/style-probe/soft/{triage,board,focus,terminal-block,settings,launcher}.png`

Tradeoffs:

- **`frame_border_size 0` is global.** It takes the outline off wells as well as buttons, so
  the two are told apart by fill only: buttons lighter than the surface (accent-tinted), wells
  darker. On the docks this reads; inside a modal `theme.dialog_colors` pushes its own
  `frame_bg` (`palette.well`) over the variant's, so the dialog's wells are as today minus
  their border. Whether a borderless well still reads as a field on `light` and
  `high_contrast` was **not** looked at — only `dark` was rendered. `high_contrast` leans on
  borders by design (`theme.apply_style`'s comment on `child_border_size`), so this variant
  would want an exception there.
- **Tabs +17 %** (24 → 28 px), one pixel over the brief; (9, 5.5) would land at 27 px / 12.5 %
  but half-pixel padding puts text on half pixels. Same global cost as in `tidy`, larger:
  every frame is 4 px taller, which is the main density cost of this variant.
- **The drawn tabs are a prototype**, not a finished widget: they paint over ImGui's tab bar
  after the fact, so the close button on the selected tab is covered (it is still there to
  click, invisible), and a tab being dragged and a scrolled tab bar's arrows are not drawn.
- **16 px scrollbars** cost 4 px of text width in every scrolling pane against today.

## The terminal-style tool block — a mock-up

`terminal-block.png` in each variant is **a mock-up**: a floating window drawn by
`style_probe.draw_terminal_mockup` from fixed text, labelled as such in the image. Nothing
in it is read from a transcript; U1's structured entries (`tool-entries` on the board) are
what a real one would draw from. It shows: a near-black ground (`#0b0b0c`), a small
variable-width caption line (the description, the timeout, an exit status) at 0.8 × the UI
size in the typeset library's proportional face, the command in grey monospace after a `$`,
the output in dimmer grey cut to seven lines with an `expand (3 more lines)` button, and a
failed command marked by a 3 px `danger` rule down its left edge rather than by turning its
text red. Its rounding and button are the variant's, as a real one's would be.

## Tab shape (U9): can the dock tab bar be custom-drawn?

Read against imgui-bundle 1.92.900 as installed (`imgui_bundle/imgui/internal.pyi`):

- No style value insets a tab: `tab_rounding` is "Radius of upper corners of a tab", and a
  dock node's tab bar has no draw callback.
- What the binding does expose is where each tab is: `DockNode.tab_bar` →
  `TabBar.bar_rect`, `TabBar.tabs` (iterable `ImVector_TabItem`), `TabItem.offset`, `.width`,
  `.window`, `TabBar.selected_tab_id`, `scrolling_anim`; and `DockNode.host_window.draw_list`.
  `IsVisible`/`IsFocused` are C++ bitfields the binding does not reach; `last_frame_active` /
  `last_frame_focused` against `imgui.get_frame_count()` say the same.
- So the prototype repaints each visible node's tab bar in its **host window's** draw list
  after every window has ended (from `_draw_overlays`). Drawing there rather than on the
  foreground list keeps the host's z-order, so a modal still covers the tabs — visible in
  `soft/settings.png` and `soft/launcher.png`. ImGui still hit-tests its own tabs, which are
  exactly underneath, so clicking, dragging and the context menu work unchanged.

What adopting it would take: move `draw_inset_tabs` into `pptmstr/ui/` and call it from
`app._draw_overlays`; draw the close button and the dragged/scrolled cases (or set
`tab_close_button_min_width_*` so ImGui never shows one); per-theme lane/tab colours. The
cheaper alternative is to accept ImGui's upper-rounded tab (`tidy`). The proper fix — a
`DockNode` tab-draw hook or a `TabItemBackground` style override — would be an upstream
Dear ImGui change, and is not proposed.

## Lowercase labels (U9): does a `###` id keep saved layouts?

**Measured** (bare ImGui context, this venv): `imgui.internal.im_hash_str` gives the same id
for `SESSION`, `session###SESSION` and `###SESSION` (`0xc9ae7b06`), and for `BOARD` /
`board###BOARD` (`0x45b1d867`). A context loaded with `[Window][SESSION] Pos=321,123` and then
asked for `session###SESSION` put that window at 321,123: Dear ImGui's window settings and
dock placement, which are keyed by that hash, carry over.

**Read** (upstream hello_imgui source, `src/hello_imgui/internal/docking_details.cpp` and
`hello_imgui_ini_settings.cpp` on master, fetched 2026-10-15 — not the C++ inside the 1.92.900
wheel, which ships no sources):

- whether a layout is rebuilt from code is decided by
  `FindWindowSettingsByID(ImHashStr(label))` — the same hash, so a relabel does **not**
  rebuild the operator's arrangement;
- per-layout pane visibility is stored under `SanitizeIniNameOrCategory(label)` =
  `<ImHashStr(label)>_<alnum of label>`, i.e. `…_SESSION` becomes `…_sessionSESSION`. A pane
  the operator **closed** would come back open once after the rename;
- `focus_dockable_window(name)` and `dockable_window_of_name` compare the **whole** label
  string, so every caller in `app.py` (`focus_dockable_window(pane)`), `scripts/screenshot.py`
  and the verify scripts would need the new label, and `layout_ini.keep_layout_for` matches
  `[Window][label]` text — a window first saved under the new label is written as
  `[Window][digest###DIGEST]`, while one saved before keeps its old `[Window][DIGEST]` name.

**Not run:** a hello_imgui end-to-end over a saved ini with the labels changed (the shape of
`scripts/verify_new_pane_keeps_layout.py`). The probe shows lowercase labels by drawing them,
which changes no window name at all — the other route to U9's lowercase, at the cost of
owning the tab drawing.

## Margins: one en dash is already there

The en dash at the UI font measures 8 px (printed by every probe run), which is
`window_padding.x` today. What the operator sees as no margin is in the session pane: the
scrollback child (`app.session_pane`, `##scrollback`) has no padding of its own, so typeset
text starts on the child's left edge (`current/focus.png`). That is a fix for `pane-geometry`
(U4 owns the session pane's margins), not a style value, so no variant changes
`window_padding`.

## Comparison

| | current | tidy | soft |
|---|---|---|---|
| scrollbar | 12 px, square | 14 px, round | 16 px, round, lighter grab |
| button vs well | same fill, both outlined | tinted button, both outlined | tinted flat button, darker flat well |
| corner radius (frames) | 2 | 3 | 5 |
| tab height | 24 px | 26 px (+8 %) | 28 px (+17 %) |
| tab shape / labels | ImGui, uppercase | ImGui upper-rounded, uppercase | drawn inset, four corners, lowercase |
| density cost | — | +1 px per frame side | +2 px per frame side, +1 px per row gap, 4 px less text width |
| code to adopt | — | values in `apply_style` | values + an owned tab drawer (`draw_inset_tabs`) |
| risk on `high_contrast` | — | low | borders removed where that theme relies on them |

## Recommendation (lead/builder, not the operator's)

**`soft`'s buttons, wells and scrollbars on `tidy`'s frame padding.** The flat tinted button
against a darker well is the change that answers "buttons don't read as buttons"; the 4 px
of extra frame height in `soft` buys tab height at the price of density everywhere, which
is against the hacker/developer brief. Take the inset tab drawing only if the operator
likes it in `soft/*.png` enough to own a tab drawer — it is the one piece here that is code
rather than values. Lowercase labels: drawn, if the drawer is adopted; otherwise by `###`,
with the visibility-key and label-string caveats above handled in the same change. Keep the
borders on `high_contrast` whatever is picked.

## What was run and what was not

Run (builder-2, 2026-10-15, this worktree, Xvfb + llvmpipe through `scripts/xvfb_shot.sh`,
window 1500×900):

- `.venv/bin/python scripts/style_probe.py --all` — exit 0 for each of `current`, `tidy`,
  `soft`, six images each.
- `.venv/bin/python -m pytest -q tests/test_style_probe.py` — 13 passed; each of six
  mutations of `apply`/`revert`/`color_of` (revert skips colours, revert skips scalars,
  apply records after writing, saved keeps a reference, apply sets no colours, colour from a
  fixed palette) failed at least one test.
- the `###` hash and ini-reuse measurements above.

Not run: any theme but `dark`; any display size but 1500×900; DPI scaling; the hello_imgui
end-to-end relabel over a saved ini; the drawn tabs under a drag, a scrolled tab bar or a
floating node; the probe on a real (non-Xvfb) display.
