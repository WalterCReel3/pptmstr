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

## Round two: variation sheets on `soft + wells`

Operator, 2026-10-15, after round one: "I really like the direction of 'soft'", and asked
for variation comparisons of tab design (interior and exterior margins, text size), the two
button sizes, line contrast for dividers and outlines, and dropdowns.

### What to look at

| image | what it is |
|---|---|
| `shots/style-probe-2/tabs.png` | sheet 1: drawn inset tabs, nine cells, each a focused pane's bar and an unfocused pane's, at the geometry a dock gives them |
| `shots/style-probe-2/buttons.png` | sheet 2: `small_button` and `button`, three steps each, with and without the outline, and soft's step with the outline at the 3:1 level; four states; a well and checkboxes beside them held at the base |
| `shots/style-probe-2/lines.png` | sheet 3: `Col_.border`/`Col_.separator` at four levels on every line element, then five well treatments at line level 1 and again at level 3 |
| `shots/style-probe-2/dropdowns.png` | sheet 4: three combo/menu-popup variants, each with a button under the same values, and one real combo popup to check the replicas against |
| `shots/style-probe-2/screens/{triage,board,focus,terminal-block,settings,launcher}.png` | round one's six screens on `soft + wells` with each dimension's middle value |
| `shots/style-probe-2/screens/diagnostics/pane-edge.png` | one frame with `Col_.separator` red and `Col_.border` green, to say which slot draws the pane edge |
| `shots/style-probe-2/screens/diagnostics/tab-gap.png` | one frame with every tab fill yellow and the lane `bg`, to measure the gap between drawn tabs on a dock |

Each sheet is one 1500×1300 frame of the running `--fake` app with a full-window probe window
over it (the window is resized with `hello_imgui.change_window_size` before the sheets, on a
1600×1400 Xvfb screen, because sheets 2 and 3 no longer fit 900 px; the screens stay 1500×900). Every cell is drawn on real ImGui widgets, under its own values: `apply` before the
cell, `revert` after it (`style_probe.laid_over`). Every label is built from the values the
cell applies (`cell_label`, `TabSpec.label`), and every contrast figure is computed from the
colour in force at the moment the cell is drawn. So a label cannot disagree with its cell.
Regenerate with `.venv/bin/python scripts/style_probe.py --round-two`, and print every value
with `--describe`.

**Side effects are on the cell.** The sheets push a cell's values around that cell only, but
theme.py would set them for the whole app. Where a cell's value changes something outside
its sheet's dimension, the cell says so in yellow ("as a style value, … is …" or "global: …").
The text comes from `style_probe.REACH`, and `changes_beyond` decides when to show it: a
value that differs from the base and is not one of the sheet's own. A test holds every cell
value to having a `REACH` entry.

### The base: `soft + wells`

**Decided by the builder in the unattended session.** The spec names "the lead's well
fix" but does not spell it out. I took the reviewer's suggestion from `rv-probe-notes`: keep
`frame_border_size 1`. So `soft + wells` is `soft` with exactly one value changed,
`frame_border_size 0 → 1`. Every other value, the colours and the drawn tabs are `soft`'s.
`tests/test_style_probe.py::test_soft_wells_is_soft_with_the_frame_outline_put_back` holds
this.

The cost is that `frame_border_size` is one value for every frame ImGui draws: wells, check
and radio boxes, combos and buttons. It cannot outline the wells and leave the buttons flat.
Sheet 2 shows how much that costs, and sheet 3 shows the alternatives.

### Sheet 1: tabs (`tabs.png`)

Each row varies one dimension and holds the other two at their middle step. The middle step
is `pad 10x4 · gap 4 · inset 2 · text 1.10×`. Definitions:

- **pad**: the distance from the text to the tab's edge, x and y. y is the same above and
  below the text.
- **gap**: the spec value `draw_inset_tabs` takes off ImGui's tab rectangle, `gap / 2` at
  each side.
- **inset**: the space from the lane's top edge to the tab.
- **lane**: inset + text + 2 × pad.y.

**What a dock actually draws, measured.** ImGui lays a dock's tab rectangles
`item_inner_spacing.x` (4, which theme.py does not set) apart: the screens run prints every
tab bar's offsets and widths, and HEALTH's six tabs are 4 px apart (`_print_tab_bars`). The
pixel row y 40 across the right dock in `screens/diagnostics/tab-gap.png` (every tab fill
yellow, the lane `bg`) has the bar at x 1222, the first drawn tab starting at 1224, and the
gaps between drawn tabs at x 1300–1307 and 1384–1391: **8 px for "gap 4"**. So on a dock:

- tab to tab = gap + `item_inner_spacing.x` (6 / 8 / 10 px for gap 2 / 4 / 6);
- lane's left edge to the first tab = gap / 2, not `inset` (1 / 2 / 3 px);
- `inset` is the top only.

The sheet now draws that geometry, and each cell says it ("as drawn, on a dock: 8 px tab to
tab …", from `TabSpec.on_dock`). Round two's first render drew `gap` and `inset` literally,
so every cell showed a tab-to-tab gap 4 px narrower than a dock draws.

One dock difference is stated on the sheet but not drawn: a dock sizes each tab itself.
Uncrowded, that is the label at the UI font + 2 × `frame_padding.x` + room for the close
button. FLEET's tab is 78 = 40 + 18 + 20 and SESSION's is 94 = 56 + 18 + 20, from the printed
widths; the 20 is inferred, not read from ImGui. ImGui shrinks the tabs when the bar is
crowded, as the right dock's are. So on a dock `pad.x` moves the text, but it does not widen
the tab. On the sheet the tab is sized from `pad`.

Each cell draws two bars. The first is a focused pane's: `session` (selected and focused),
`board` (unselected) and `detail` (hovered). The second is an unfocused pane's: `fleet`
(selected, not focused, `tab_dimmed_selected`) and `needs you` (unselected). This is how the
selected tab of every pane but the focused one reads. The tab's foot sits on the pane's top
edge.

| row | cell | text px | lane px | in a 28 px dock bar |
|---|---|---|---|---|
| interior padding | pad 8x3 · gap 4 · inset 2 · text 1.10× | 17.6 | 25.6 | fits |
| | **pad 10x4 · gap 4 · inset 2 · text 1.10×** | 17.6 | 27.6 | fits |
| | pad 12x5 · gap 4 · inset 2 · text 1.10× | 17.6 | 29.6 | needs `frame_padding.y` 6.8 |
| exterior margin | pad 10x4 · gap 2 · inset 1 · text 1.10× | 17.6 | 26.6 | fits |
| | **pad 10x4 · gap 4 · inset 2 · text 1.10×** | 17.6 | 27.6 | fits |
| | pad 10x4 · gap 6 · inset 4 · text 1.10× | 17.6 | 29.6 | needs `frame_padding.y` 6.8 |
| text size | pad 10x4 · gap 4 · inset 2 · text 1.00× | 16 | 26 | fits |
| | **pad 10x4 · gap 4 · inset 2 · text 1.10×** | 17.6 | 27.6 | fits |
| | pad 10x4 · gap 4 · inset 2 · text 1.15× | 18.4 | 28.4 | needs `frame_padding.y` 6.2 |

Colours are round one's `draw_inset_tabs` colours:

- the lane is `title_bg`;
- the selected tab is `tab_selected`, with a 2 px `check_mark` (accent) rule along its foot;
- the selected tab of an unfocused pane is `tab_dimmed_selected`;
- an unselected tab is `tab`;
- a hovered tab is `tab_hovered`, drawn over the lane;
- the pane is `window_bg`, outlined in `Col_.border`.

**Text size without a second font: yes.** Since ImGui 1.92 a face rasterises at whatever
size is pushed. `theme.py`'s `_FONT_SIZE` comment says the same. The sheet sets the size
with `push_font(None, font_size_base × scale)` to measure the text. It passes the same size
to `ImDrawList.add_text(font, size, …)` to draw it. Nothing is loaded.

**The dock bar's height is measured, not assumed.** In the screens run, every dock tab bar
was 28 px. That is the frame height, 16 + 2 × 6, and the probe prints it (`_print_tab_bars`).
A drawn tab cannot be taller than the bar ImGui laid out. So any cell that does not fit
needs `frame_padding.y` raised, and every input, combo and button would grow with it.

Tradeoffs:
- **Only 1.15× text, pad 12x5 and inset 4 push past the bar.** Each would cost the
  whole UI 1–2 px per frame. The middle cell fits in soft's frame height, with 0.4 px to
  spare.
- **ImGui still sizes each tab for its own uppercase label at 16 px.** The drawn tab only
  repaints that rectangle. Text at 1.10× and a wider pad are clipped at the tab's edge,
  not reflowed. On the screens every label fits, except where a node's bar scrolls
  (DETAIL's, as in round one). A long label would lose its last letter. Tab widths are only
  right if the window label itself changes (`###` relabel).
- **The hovered tab reads louder than the selected one.** It is the accent at 0.3 over the
  lane, while the selected tab is `panel_alt` plus a 2 px rule. Swapping the weights is a
  colour choice and was not tried.
- **The selected tab's fill is not the pane's.** It is `panel_alt` and the pane is
  `window_bg`, so the tab sits on the pane rather than flowing into it. The accent rule
  carries "selected". A flowing join would set `tab_selected` to `window_bg`, a one-value
  change this sheet does not vary.

### Sheet 2: buttons (`buttons.png`)

| size | step | values applied | outline 1: `Col_.border` on the button's fill |
|---|---|---|---|
| `small_button` | 1 | `frame_padding 6x6 · frame_rounding 3` | #323848 on #2a4354 = 1.13:1 |
| | **2 (soft)** | `frame_padding 9x6 · frame_rounding 5` | 1.13:1 |
| | 3 | `frame_padding 12x6 · frame_rounding 8` | 1.13:1 |
| `button` | 1 | `frame_padding 7x4 · frame_rounding 3` | 1.13:1 |
| | **2 (soft)** | `frame_padding 9x6 · frame_rounding 5` | 1.13:1 |
| | 3 | `frame_padding 12x7 · frame_rounding 8` | 1.13:1 |

Each step is drawn twice, and soft's step (the middle) three times:

- **outline 1**: `frame_border_size` as `soft + wells` sets it globally, at line level 1;
- **outline 1 at line level 3** (middle step only): `Col_.border` at
  `mix(bg, text_dim, 0.72)`, the screens' 3:1 level, so the cost of a raised line level on
  buttons is on the sheet: #5f6475 on #2a4354 = 1.76:1, against 1.13:1 at level 1;
- **outline 0**: `frame_border_size` pushed to 0 around each button only.

**Only the buttons vary.** The step and the line level are pushed around the values column
and the four buttons. The well and checkboxes beside them are drawn on the base in every
row, so they no longer change height from row to row as they did in the first render. As
a style value a step is global, so every row whose step is not soft's has a yellow line on
it: "global: also pads/rounds every well, combo, check/radio". The level-3 row says that
`Col_.border` is every outline (sheet 3).

**The two sizes cannot be styled apart by style values.** The sheet says this too.
`small_button` and `button` both read the one `frame_padding.x` and `frame_rounding`, and
`small_button` forces `frame_padding.y` to 0. The two tables are three steps of one global
pair of values seen on each size, not two independent choices. Giving each size its own
step needs code: a `widgets.small_button` / `widgets.button` wrapper that pushes its own
`frame_padding` and `frame_rounding` for each call, at 42 and 39 call sites. This is the same
wrapper, and the same cost, as outline 0.

Each row shows four states:

- **normal**;
- **hovered**: `Col_.button_hovered` pushed as `Col_.button`, which is the colour ImGui
  picks in that state;
- **active**: likewise, with `button_active`;
- **disabled**: `begin_disabled`.

Beside them are an input well and the `follow`/`reasoning` checkboxes, on the base.

**`small_button` ignores `frame_padding.y`.** ImGui forces it to 0. This is measured:
`test_small_button_ignores_frame_padding_y_and_button_does_not` checks the item height
against the font size. So only x and rounding can vary for the micro size. Its height is the
font's 16 px whatever the style says. The `.y` of 6 in its rows applies to the well beside
it, not the button.

**Where each size is used today** (grep of `pptmstr/`, call sites, not unique labels):

- `small_button`: 42 call sites in 11 modules. These are pane rows and inline actions: copy,
  "N earlier lines", "back to latest", find's `x`, the board's filter chips and amend/cancel,
  help marks, the launcher's rescan/bookmark/notices.
- `button`: 39 call sites in 13 modules. These are dialog and launcher footers
  (launch/cancel/close/resume/append), health actions (interrupt/close/fork), the composer
  (send/interrupt/close session), approvals (approve/reject/edit/approve all) and the
  session menu's confirmations.

Tradeoffs:
- **At today's border colour the global outline is almost invisible on a tinted button.**
  It is 1.13:1 against the button's own fill. So `soft + wells` keeps buttons looking flat
  for free, which is why outline 1 and outline 0 rows look alike.
- **Raising the line contrast changes that.** At the 3:1 line level (sheet 3, and the
  screens), the outline is #5f6475 on #2a4354 = 1.76:1, and every button visibly gains a
  border. The sheet's level-3 row shows it, and so does `screens/focus.png` (send / interrupt /
  close session).
- **The flat outline-0 button costs code, not values.** The app would need a
  `widgets.button`/`small_button` wrapper that pushes `frame_border_size 0`, and 81 call
  sites routed through it.
- **Step 3's rounding 8 on a 16 px `small_button` is nearly a pill.** Step 1's rounding 3 is
  close to today's.

### Sheet 3: line contrast (`lines.png`)

The contrast figures are WCAG 2.1 relative-luminance ratios (`style_probe.contrast`). They
are computed from the 8-bit colours ImGui packs and draws (`rgb8`: `int(v × 255 + 0.5)`).
The function is tested against black/white = 21, against WCAG's #767676/#777777-on-white
pair (4.54 / 4.48), and against `theme.contrast_ratio` for every dark role.

**Which slot draws what.** `Col_.separator` draws `separator` and `separator_text`.
`Col_.border` draws child outlines, popup outlines and frame outlines (wells, check and
radio boxes, combos, buttons). It also draws **the pane edge**: 2 px between docked panes.
This is measured from `screens/diagnostics/pane-edge.png`. The pixels at x 314–315 (FLEET |
SESSION) and x 1190–1191 (SESSION | DETAIL), y 600, are `#4ec97e`, which is `ok`, the
colour that frame gave `Col_.border`. `Col_.separator` was `danger` and does not appear
there. The sheet sets the two slots together.

| level | colour | on `window_bg` #14161c (pane) | on `child_bg` #1a1d26 | on `frame_bg` #161820 (well) | on `popup_bg` #21242f | on button fill #2a4354 |
|---|---|---|---|---|---|---|
| 1 (today; `soft + wells`) | `mix(bg, border, 1)` #323848 | 1.55:1 | 1.44:1 | 1.51:1 | 1.32:1 | 1.13:1 |
| 2 | `mix(bg, text_dim, 0.5)` #484d5a | 2.14:1 | 1.99:1 | 2.10:1 | 1.83:1 | 1.22:1 |
| **3 (screens)** | `mix(bg, text_dim, 0.72)` #5f6475 | **3.07:1** | 2.86:1 | 3.01:1 | 2.63:1 | 1.76:1 |
| 4 | `mix(bg, text_dim, 0.9)` #72788b | 4.11:1 | 3.83:1 | 4.03:1 | 3.52:1 | 2.35:1 |

The first three ratio columns are drawn on the sheet. The `popup_bg` and button-fill columns
were computed with the same functions from the same mixes, in a Python one-off, and are not
on the sheet.

**Wells.** The table is drawn twice: once with outlines at line level 1 (the base's,
#323848), and again at level 3 (the screens', #5f6475, `WELL_LEVELS`). Each cell is the line
level, then the option, laid over the base.

| option | values | fill on the pane | outline on the pane, level 1 / level 3 |
|---|---|---|---|
| border only (pane's fill) | `frame_border_size 1 · Col_.frame_bg = mix(bg, bg, 1)` | #14161c 1.00:1 | 1.55:1 / 3.07:1 |
| soft's fill only (round one) | `frame_border_size 0 · Col_.frame_bg = mix(bg, panel, 0.35)` | #161820 1.02:1 | none |
| darker fill only | `frame_border_size 0 · Col_.frame_bg = mix(bg, black, 0.5)` | #0a0b0e 1.09:1 | none |
| **both: border + darker fill** | `frame_border_size 1 · Col_.frame_bg = mix(bg, black, 0.5)` | #0a0b0e 1.09:1 | 1.55:1 / 3.07:1 |
| the base: border + soft's fill | `frame_border_size 1 · Col_.frame_bg = mix(bg, panel, 0.35)` | #161820 1.02:1 | 1.55:1 / 3.07:1 |

In the first render, "both" was the base: an outline with soft's fill. That looked the same
as border-only, and the whole table was drawn only at level 1. Now "both" is the outline
with the darker fill, and the base has its own column.

Both fill-only cells carry a yellow line: `frame_border_size 0`, as a style value, is every
frame's outline, buttons included. So "fill only" would also take every button's outline off.

Tradeoffs:
- **On `dark`, a fill cannot make a well.** The pane is near-black (#14161c).
  - soft's fill is 1.02:1 against it.
  - Half-way to black is 1.09:1.
  - Today's `panel_alt` frame fill is 1.17:1.
  - In the fill-only columns the unchecked checkbox and the unselected radio are not
    there at all. That is the reviewer's finding, now quantified. Only an outline
    separates a well from the pane on this palette.
- **WCAG 2.1 SC 1.4.11 asks 3:1 for the visual boundary that identifies a control.**
  - Today's border is 1.55:1.
  - Level 3 is the first at or over 3:1 on the pane (3.07:1). A test holds the middle to
    being that level.
  - Level 3 is 2.86:1 on a child's fill, and 2.63:1 against a popup's fill. So 3:1 is met
    on the pane and not inside children or popups.
- **One slot, many lines.** Raising `Col_.border` for the wells also raises:
  - child outlines;
  - the 2 px pane edges;
  - popup outlines;
  - every button's outline (sheet 2).

  `Col_.separator` is independent. A split, with separators lower and `Col_.border` higher,
  is two values, not code.
- **Level 4 makes pane edges the loudest line on the screen** (4.11:1 at 2 px). Level 3
  already makes them prominent in `screens/*.png`.

### Sheet 4: dropdowns (`dropdowns.png`)

| variant | values applied |
|---|---|
| flat | `frame_border_size 0 · popup_border_size 0 · popup_rounding 0`; the arrow keeps soft's tinted `Col_.button` |
| **framed** (= `soft + wells` as it stands) | `frame_border_size 1 · popup_border_size 1 · popup_rounding 4`; tinted arrow |
| lifted | `frame_border_size 1 · popup_border_size 0 · popup_rounding 8 · Col_.button = mix(bg, panel, 0.35) · Col_.popup_bg = mix(panel_alt, text, 0.08)` |

Each column draws four things, using real names:

- the `theme` combo, closed, showing "Dark" from `theme.THEMES`;
- a `button` ("launch") and a `small_button` ("copy") under the same values, so a value that
  recolours or re-outlines every button shows on the cell;
- the `model` combo, open, listing `model_catalog.FALLBACK_MODELS`;
- the menu bar with Session open over its popup. The popup has a heading, Interrupt, a
  disabled Fork, Details, Health, Close, a "Gate" heading with a note, a rule, and a ticked
  "Show ended sub-agents".

**The open popups are replicas.** ImGui keeps one popup stack, so three variants cannot
each have a popup open in one frame. A replica is a child window given the values ImGui
gives a popup window:

- `popup_bg` for its fill;
- `popup_rounding` and `popup_border_size` for its corners and outline;
- for the combo, the frame's horizontal padding.

The combo field over a replica has its arrow in `button_hovered`, which is what an open
combo draws. The fourth column is ImGui's own `begin_combo` popup on the framed variant. It
was opened with `internal.open_popup_ex` on the combo's popup id
(`im_hash_str("##ComboPopup", 0, id)`), so the replica can be checked against it. They
match:

- item pitch is 22 px in both;
- the text's left edge lines up with the field's text in both;
- the popup's width is the field's width in both.

The vertical placement was measured down one pixel column through each. In both, the
field's bottom outline and the popup's top outline are adjacent rows: y 246–247 for the
real popup and y 308–309 for the replica. The popup's fill then starts on the next row. In
both, the selected row's 22 px highlight starts 48 px into the fill, and the bottom outline
is 118 px below the fill's first row. The
menu popup and the menu bar have no real counterpart on the sheet.

**The combo arrow has no slot of its own.** Read from `imgui_widgets.cpp` (`BeginCombo`,
1.92): `bg_col = GetColorU32((popup_open || hovered) ? ImGuiCol_ButtonHovered :
ImGuiCol_Button)`. `ComboFlags_` has `no_arrow_button` and nothing that recolours the arrow.
So it cannot be scoped to the arrow by a style value:

- **lifted** sets `Col_.button`, which is every button's and `small_button`'s fill. Its cell
  says so in yellow, and its "launch"/"copy" show the result: buttons in the well colour,
  hard to tell from a well.
- **flat** sets `frame_border_size 0`, which also takes the outline off every well, check
  box and button. Its cell says so too.
- **framed** is the base and changes nothing outside dropdowns.
- In every variant, including lifted, an open combo's arrow is `button_hovered` (tinted).
  So "arrow in the field's fill" holds only while the combo is closed.

Keeping lifted's look without its side effect needs code: a combo wrapper that pushes
`Col_.button` around `begin_combo`, or `no_arrow_button` with an arrow glyph drawn by hand.

Tradeoffs:
- **Flat repeats the wells problem.** The field is fill-only at 1.02:1, and the popup
  (`panel_alt`, 1.17:1 on the pane) has no edge where it overlaps a pane.
- **Lifted relies on the popup's fill to separate it.** That fill is #2e323d, 1.41:1 on the
  pane. This reads over the pane in the image. Over a `panel_alt` region it would read less,
  and that was not drawn. The untinted arrow makes the combo look like a text field with a
  glyph. As a style value it also makes every button the well colour (above).
- **Framed** is the base, so it costs nothing beyond `soft + wells`. Its popup outline
  follows `Col_.border`, so it changes with the line level.

### The screens on the middle values

`screens/*.png` are round one's six screens under `soft-wells-middle`:

- `soft + wells`;
- `Col_.border` and `Col_.separator` at level 3, `mix(bg, text_dim, 0.72)`;
- the drawn tabs at `pad 10x4 · gap 4 · inset 2 · text 1.10×`;
- buttons and dropdowns at their middle values, which are the base's own.

"Middle" for lines was not obvious, because there are four levels. I took level 3 because it
is the first at WCAG's 3:1. That choice is mine; the spec did not make it. In a dock, the
tabs take ImGui's tab rectangle. The drawn shape has gap 4 and a 2 px top inset, with its
foot on the pane. The text is at 1.10× and 10 px in. The vertical pad is what the 28 px bar
leaves, about 4 px.

`draw_inset_tabs(None)` still draws round one's shape. Round one's `soft` was rendered with
the script as committed and with this one, into a scratch directory under
`shots/style-probe-2/` that was then removed. The tab-bar rows (y 28–56) were byte-identical
on triage, board, focus and settings. The 11 other differing rows hold clock-driven text.

### Recommendation (builder's, not the operator's)

- **Base: `soft + wells`.** The outline is the only thing that makes a well, checkbox or
  radio visible on `dark` (sheet 3).
- **Lines: split the two slots.**
  - `Col_.border` at level 3 (3.07:1). It is what identifies wells and check/radio boxes,
    which is the case 1.4.11 covers.
  - `Col_.separator` at level 2 (2.14:1). Dividers are decoration, and at 3:1 they compete
    with the content.
  - Accept that buttons then show an outline (1.76:1 on their fill). The alternative is a
    button wrapper across 81 call sites. That is a decision for the operator and not
    something a style value can do.
- **Tabs: the middle cell** (`pad 10x4 · gap 4 · inset 2 · text 1.10×`, which on a dock
  is 8 px tab to tab and 2 px in from the lane's left). It is the largest
  text and padding that fit soft's 28 px bar without growing every frame. 1.15× text costs
  every frame 1 px.
- **Buttons: soft's own values** (`frame_padding 9x6`, rounding 5). `small_button` cannot
  get taller by style.
- **Dropdowns: framed.** It is consistent with the wells and costs nothing. Revisit "lifted"
  only on a theme where the popup fill can carry the edge.

Before any of this goes into `theme.py`, render `light` and `high_contrast`. Neither was
looked at. `high_contrast` sets `child_border_size 2` and relies on borders by design.

### Changes since the first render (task `style-probe-2-fix`)

The first render was reviewed (`rv-style-probe-2`). These changes answer that review:

- **Sheet 3, wells.** "both" is now outline + the darker fill. The base (outline + soft's
  fill) has its own fifth column. The table is drawn at line level 1 and again at level 3,
  so border-only, fill-only and both are each seen at the level the recommendation uses.
- **Sheet 4, dropdowns.** There is no arrow-only slot in ImGui 1.92, so lifted's
  `Col_.button` cannot be scoped. Instead, every column shows a button and a `small_button`
  under its values, and each cell states in yellow what it changes outside dropdowns: lifted
  recolours every button, and flat takes every frame's outline off.
- **Sheet 2, buttons.** The varied style is pushed around the buttons only. The well and
  checkboxes are held at the base in every row. Each non-soft step says it is global. The
  sheet and this record say that the two sizes cannot be styled apart by style values, and
  what that would need. Soft's step has a third row with the outline at the 3:1 level.
- **Sheet 1, tabs.** The gap between drawn tabs on a dock was measured at gap + 4 px. The
  cells now draw and label the dock geometry. The unfocused-pane state
  (`tab_dimmed_selected`) is a second bar in every cell.
- **All sheets** are 1500×1300, because sheets 2 and 3 no longer fit 900 px. The screens
  are unchanged in size. A new diagnostic is `screens/diagnostics/tab-gap.png`.
- **Every cell that sets a value beyond its sheet's dimension says so** (`REACH`,
  `changes_beyond`).

What did not change: the base, the line levels and their contrast figures, the tab, button
and dropdown values, the screens' middle values, and round one's images.

### What was run and what was not (round two)

Run (builder-16, 2026-10-15, this worktree, Xvfb + llvmpipe through `scripts/xvfb_shot.sh`):

- `.venv/bin/python scripts/style_probe.py --round-two`: sheets exit 0 (4 images,
  1500×900), screens exit 0 (6 images plus the diagnostic).
- `.venv/bin/python -m pytest -q tests/test_style_probe.py`: 74 passed.
- Mutation test on a copy of the tree under `$TMPDIR`. Each of 15 mutations failed at least
  one test, and the unmutated copy passed:
  - revert skips colours;
  - `soft + wells` keeps border 0;
  - the middle drops the separator;
  - contrast without the 0.05 flare;
  - luminance without gamma;
  - `rgb8` truncates;
  - the middle line at level 4;
  - `laid_over` without `finally`;
  - fixed colours ignored;
  - `cell_label` omits colours;
  - the lane counts one padding;
  - the ratio taken on the well instead of the pane;
  - the middle tab at 1.15×;
  - the small step off soft's;
  - apply records after writing.
- `make format-file`, `ruff check` and `mypy` on `scripts/style_probe.py` and
  `tests/test_style_probe.py`: clean.
- The round-one tab regression comparison above.
- The pane-edge pixel measurement above.

Not run:

- any theme but `dark`;
- any size but 1500×900;
- DPI scaling;
- a real hover or press: hovered and active are the colours pushed;
- a real open menu popup: replica only, while the combo replica was checked against a real
  one;
- the drawn tabs on a dock with a long label, a drag, a scrolled bar or a floating node;
- `make check` and the rest of the suite.

Run for `style-probe-2-fix` (builder, 2026-10-15, same route):

- `.venv/bin/python scripts/style_probe.py --round-two`: sheets exit 0 (4 images,
  1500×1300), screens exit 0 (6 images plus two diagnostics). Round one's
  `shots/style-probe/` was not written.
- The tab-gap pixel row and the printed tab offsets above.
- `.venv/bin/python -m pytest -q tests/test_style_probe.py`: 96 passed.
- A mutation run on a copy under `$TMPDIR` of the new code, with 7 mutations. Each one was
  killed, and the unmutated copy passed. The mutations:
  - "both" back to soft's fill;
  - the dock gap without `item_inner_spacing`;
  - the wells at level 2;
  - `changes_beyond` blind to colours;
  - `changes_beyond` ignoring the sheet's own values;
  - no dimmed-selected tab;
  - `reach` dropping values.
- `make format-file`, `ruff check` and `mypy` on the script and the test file.

Not run in that pass:

- the 15 round-two mutations from builder-16's run;
- any theme but `dark`;
- a real hover or press;
- the dock's own tab width, drawn on the sheet: it is stated, not drawn, and the 20 px
  close-button room is inferred from two printed widths;
- `make check`.
