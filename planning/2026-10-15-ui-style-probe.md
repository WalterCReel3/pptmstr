# The style probe: three looks on the same screens, for the operator to pick

**Dated:** 2026-10-15 · **Status:** a probe. The operator picked from the round-two sheets
the same day, and nothing is applied to `theme.py` yet · **Answers:**
U10 and the prototype half of U9 in `2026-10-15-ui-rework.md`, against the operator's notes
under "Cohesion and UI workshopping" in that record

## Tab text variants (task `tab-text-variants`)

After the fix-ups the operator said: "We might need to run variants on tabs again. The text
doesn't look right." They did not say what is wrong, so each row of one sheet moves one
property of the label away from today's preview.

**Images.** The sheets are gitignored, rendered with `.venv/bin/python
scripts/style_probe.py --tabs-text`, one Xvfb per theme:

- `shots/style-preview/tabs-text/dark.png`, `light.png` and `high_contrast.png` are the sheets.
- `<theme>-raw.png` is each sheet's first frame, the one the measurements are read from.

Each sheet runs under `final_for(theme)`, which is the final preview's style.

**What a cell is.** A cell has two drawn tab bars of the five dock labels (`fleet session
health detail board`):

- In the focused pane's bar, `session` is focused and `detail` is hovered.
- In an unfocused pane's bar, `session` is selected.

The geometry is the pick's: pad 10x4, gap 4, inset 1. The lane is 28 px because that is the
dock's frame height, so the tab box is 27 px. Tab widths are ImGui's dock widths: the
UI-font name, plus 2 × `frame_padding.x`, plus close-button room
(`item_inner_spacing.x` + the font size). Those reproduce the measured FLEET 78 and SESSION
94. The label is clipped to its tab, as `draw_inset_tabs` clips it.

Every label is pushed with `push_font(face, px)` at a whole pixel and drawn at
`get_font_size()`. A test holds every cell to that.

Each cell is labelled with:

- the face, the bound size, the case and the width of `session`;
- the pen y asked and drawn;
- every state's ink on its fill, with contrast;
- the measured gaps and edge share.

**How the gaps are measured.** They are read from the frame:

- A pixel counts as ink at 0.15 of the way from the tab's fill to its ink.
- Rows are taken over all five labels of the unfocused bar. None of the labels has a
  descender, so the lowest ink is the baseline.
- The x-height top is the first row of `session` with a quarter of its busiest row's ink.

The probe also predicts each gap from the bake's glyph boxes (`ImFontBaked.find_glyph`). On
all three themes the prediction matches the measurement exactly for every snapped cell.

**One thing found while building it.** `ImFont::RenderText` truncates the pen position.
Box-centring 18 px in 27 asks for a pen 4.5 px down, and the glyph quads are drawn at 4. I
read the vertices in a bare context: pens at y 25.0, 25.5 and 25.7 all put the glyph at the
same whole pixel. So today's label is already pixel-snapped, and the fuzz fixed in the fix-ups
came only from the scale. To show the alternative, row 6 moves the quads off the whole pixel
after the draw.

**The numbers.** Gaps are top to the ascender / top to the x-height / bottom, in px. Edge is
the edge pixels' share of `session`'s ink: lower is crisper, the same measure as the
fix-ups'. The numbers are dark's. Light and high_contrast measure the same gaps, and their
edge shares are within 2 points of dark's.

| row | cell | gaps | edge |
|---|---|---|---|
| reference | 18 px, Inconsolata Medium, lowercase, box-centred, snapped | 7 / 11 / 8 | 59% |
| 1 size | 15 px | 9 / 12 / 8 | 58% |
| | 16 px | 9 / 12 / 8 | 69% |
| | 17 px | 7 / 11 / 8 | 67% |
| | 18 px | 7 / 11 / 8 | 59% |
| 2 face | prose (Roboto-Regular, `gate.face_font("PROSE")`); `session` 53 px wide against 63 | 7 / 11 / 8 | 65% |
| 3 case | Title Case | 7 / 11 / 8 | 60% |
| | UPPER | 8 / 8 / 8 | 64% |
| | UPPER, tracked 1 px | 8 / 8 / 8 | 64% |
| 4 weight, colour | selected in accent | 7 / 11 / 8 | 59% |
| | unselected in mix(fill, text, 0.7) | 7 / 11 / 8 | 59% |
| | selected bold (Inconsolata Bold) | 7 / 11 / 8 | 56% |
| 5 placement | em box centred on the lane | 7 / 11 / 8 | 59% |
| | ascender..baseline centred on the tab | 7 / 11 / 8 | 59% |
| | x-height..baseline centred on the tab | 5 / 9 / 10 | 59% |
| 6 snapping | y off by 0.5 (the 4.5 box-centring asks for) | 8 / 12 / 7 | 85% |
| | x and y off by 0.5 | 8 / 12 / 7 | 99% |

ImGui has no letter-spacing, neither as a style value nor as an ImFont field. The tracked
cell draws each glyph at its own pen position. That is call-site code: a dock tab could only
have it through `draw_inset_tabs`.

**Contrast, ink on fill.** The 4.79:1 for accent on light is the one value near the AA floor.

| | dark | light | high_contrast |
|---|---|---|---|
| selected / focused, text (today) | 9.83 | 13.43 | 19.03 |
| selected / focused, accent | 7.46 | 4.79 | 12.37 |
| unselected, text_dim (today) | 5.15 | 5.78 | 11.54 |
| unselected, text at 70% | 5.91 | 5.84 | 10.02 |
| hovered, text on the hover tint | 5.65 | 10.04 | 10.72 |

**What the numbers say.**

- **The label is already balanced and snapped.** The ascender sits 7 px below the tab top and
  the baseline 8 px above the foot. Centring the ascender..baseline band instead gives the
  same pixels, and so does centring on the lane. The operator's "bottom margin balanced with
  the margin top" holds to within 1 px for labels with ascenders.
- **The lowercase body does not sit centred.** It is 11 px from the top and 8 px from the foot,
  so a word with no ascender, like `session`, sits 1.5 px low. Optical centring puts the body
  at 9 / 10, but the ascenders then sit 5 px from the top against 10 px under the baseline.
- **Unsnapped text is plainly worse:** 85% and 99% edge against 59%. Nothing should move the
  pen off a whole pixel, and nothing in the app does today.
- **The 18 px bake is heavier than 16 and 17 px,** at 59% edge against 69% and 67%. More of
  its stems are solid pixels, so it reads darker and blockier than the 16 px UI text around
  it. 18 px is also 2 px larger than the font ImGui sized the dock tab for. In the crowded
  right dock of `fixups/final/dark/focus.png`, `board` is clipped to `boar`. The sheet draws
  uncrowded bars, so it does not show that.

**Recommendation (the builder's, not the operator's).** I would make these changes:

- Draw tab text at **17 px**, the nearest whole pixel to 1.05×. It keeps the 7 / 11 / 8 gaps
  of today's 18 px, and its stems weigh like the UI font's (67% against 69%). It is one pixel
  closer to the width ImGui gave the tab, so it clips less in a crowded dock.
- **Keep** the following as they are:
  - Inconsolata Medium in lowercase. The prose face is narrower but changes the chrome's
    voice, and capitals read louder in every bar.
  - text on the selected tab. Accent falls to 4.79:1 on light.
  - text_dim on the unselected tabs. Text at 70% gains 0.06 to 0.76 on dark and light and
    loses 1.5 on high_contrast.
  - the regular weight, with no bold.
  - box centring, and ImGui's own snapping.
- Optical centring is the one placement that changes the pixels. It trades the ascender
  balance for the body's, and I would not take it without the operator pointing at that cell.

Since "doesn't look right" was not specified, the operator's pointing at a cell is the
deciding input; this recommendation is only a default.

**Tests** (`tests/test_style_probe.py`) cover:

- every cell's size is a whole pixel, and `push_font` binds exactly that size;
- each row varies only its named fields;
- the size row is 15 to 18;
- the placement arithmetic;
- the gap measurement on synthetic pixels;
- each cell's measured line quotes exactly its measured gaps;
- an unshifted label's quads are on whole pixels, and a shifted label's are moved by exactly
  its shift;
- tracking adds exactly 1 px per glyph gap.

**Run:**

- `--tabs-text` on dark, light and high_contrast, each exiting 0 from the working tree. I
  looked at all three sheets.
- `pytest tests/test_style_probe.py`: 220 passed, 1 failed. The failure is
  `test_the_middle_dropdown_is_the_base_as_it_stands`. It comes from apply-theme's
  uncommitted `popup_border_size = 0.0` in `theme.py`, not from this change.
- ruff, mypy and black on both files.

**Not run:**

- crowded dock tab bars;
- real hover;
- DPI scaling;
- the prose and bold faces in the bare-context vertex test, because the bare context has
  neither face;
- mutation testing.

## Built after the decisions, with the builders' and lead's calls (2026-10-16)

theme-fix-2 (builder-45, run; the reasoning is also in theme.py's dock_well and hint comments):
- **Dialogs get their own hint colour,** `Palette.dialog_hint`: the least step of text_dim that reads
  4.5:1 in the dialog well. It is text_dim itself on seven themes. `hint_colour(gui, *, dialog=)`
  takes the kind, and a guard test maps each field to its kind.
- **cde and win311's dock well now steps toward white, not toward text,** by 0.10 and 0.11. On
  those mid-tone themes no colour could be both 4.5:1 in the well and visibly lighter than typed
  text otherwise. The lead reads this as within "wells lighter than the pane", whose darker
  branch was for grounds at or near white. **The operator has not seen it:** on those two themes a
  dock field and a tool block now step the same way off the pane (1.14 vs about 1.28), so a block
  may read as a larger field. Renders: `shots/apply-theme/{cde,win311}/focus.png`.
- **turbo's dock well is at t=0.19,** because at 0.10 it sat 1.08:1 on the child-window panel, so
  checkboxes in DETAIL and BRIEF all but vanished.

diff-parity (builder-48, run): a ```diff fence now draws in the diff view in both panes once a turn
has ended, and diff tokens get add, remove and heading inks. The fix also made the last line of
every fence colour; before, the lexer missed it.

## Decisions answered in the operator's pane (2026-10-16)

- **Placeholder text (dq-a64871cda0f5):** the lead raised each theme's dim colour so that a hint
  reads at 4.5:1 inside the lighter wells, and offered (a) a darker well, (b) accepting
  sub-4.5 hints, or (c) a hint colour used only inside wells. Operator: "Let's go with (c)."
  So hints get their own colour, held to 4.5:1 in each theme's well. The dim colour that
  apply-theme raised goes back to its earlier value, so secondary text keeps its contrast with
  body text on every theme (on win311 the raised dim had read almost as dark as body text).
- **Text on the pixel grid (dq-b621bece1e75):** the font-raster probe measured FreeType's
  default hinting landing Inconsolata's strokes on whole pixels only at 15 and 18 px. Offered:
  the auto-hinter app-wide, or tabs at 15 or 18 px. Operator: "At this point I'm considering
  that the font just wants to be 15px. Use that in this instance and keep default rendering
  (meaning just change the size). We'll revisit". So tab text is drawn at 15 px, with default
  rasterisation, and the rest of the tab pick stands. This replaces "text 1.10×".
- **The CLI's git-status switch, wider cost (dq-7d239cda014c):** "Ratified."

## What was built from those decisions, and the lead's calls on the way (2026-10-16)

The board tasks theme-fix, tabs and tool-block carry the detail. These are the lead's
decisions, made while the operator was not asked, and each one is open to revision:
- **high_contrast keeps a popup outline as well as its frame outline.** Without it, popups sit at
  1.1:1 on black. The operator's "keep the frame on high-contrast" was about visibility on that
  theme. Every other theme has neither outline.
- **The hint colour** is a new palette field. It is pushed only around the six fields that show a
  hint, because ImGui draws hints in the same slot (`text_disabled`) as roughly 100 dim-text
  calls. A test fails if a hint field is drawn outside it.
- **Dialogs get their own hint colour** (board task theme-fix-2). On win311 the single hint is
  near-black on a white dialog field, darker than the field's caption, so an empty field reads
  filled. That defeats the reason the operator chose (c).
- **cde and win311's terminal ground** is the pane 20% toward white, not tool-block's 60%. Blocks
  sit on the log's `panel`, and 0.20 is the largest step that stays 1.05–1.3:1 off both the
  pane and the log. A cost: block buttons need their own colour (board task tool-block-fix).
- **Tab labels stay uppercase underneath; the painter draws them lowercase.** Measured: `###`
  labels make hello_imgui rebuild the saved layout on every launch. Floating-window title bars
  and the View menu still read uppercase.
- **Unselected tabs get their own ink** (board task tabs-fix). Restoring the dim colour put them
  at 4.45:1 on dark and 4.04:1 on ghost.

## After the preview (2026-10-15)

The operator looked at the preview on every theme: "This is looking fantastic although, I'm
not sure what happened but the tab text is now fuzzy. Should be a simple fix-up though."
Then: "Small button D looks great!", which is the wrapper at `frame_padding 8x4`, rounding
5, text 14 px, height 22 px. And: "Composer does get lost. Can we shade it lighter?" That
replaces the round-two fill choice for wells with a fill *lighter* than the pane, which none
of the sheets drew.

### The fix-ups (task `preview-fixups`)

Images are under `shots/style-preview/fixups/`. They are gitignored and rendered locally with
`.venv/bin/python scripts/style_probe.py --fixups`. The earlier preview is left as it was.

| image | what it is |
|---|---|
| `tab-text.png` | One tab label drawn three ways: the preview's call, the fix, and the 16 px UI font as reference. Each has its pixels at 4x (nearest neighbour) beside it, read back from `tab-text-raw.png`, with the measurement. |
| `tab-text-4x.png` | The same crops at 4x, one under another. |
| `wells-light.png` | All 9 themes against the pick as previewed and the three well steps. Each cell shows a composer, a filter showing only its hint, an unchecked checkbox, small button D and a button, with the cell's measurements. |
| `picked-wells-<t>/<theme>/{focus,settings}.png` | The real screens at each step `t` in 0.06, 0.10 and 0.15, for dark, light and high_contrast. |
| `dark/<screen>.png` | The pick's six screens on dark, with the tab fix and small button D. |

**Tab text: the cause, measured.**
- `draw_inset_tabs` drew labels with `dl.add_text(font, font_size_base * 1.10, ...)`, which is 17.6.
- ImGui 1.92.900 binds and bakes a font only at whole pixels. Measured in a bare context with
  `renderer_has_textures`: `push_font(None, 17.6)` binds 18 px, and 17.4 binds 17. The app's
  backend reports `renderer_has_textures True` and font scales of 1 (on the sheet).
- `add_text` with a size that is not baked draws the nearest bake, scaled. So 17.6 drew the
  18 px bitmap at 0.978. The glyph quads land between pixels (vertex y 107.933, 111.844 for
  a label at y 105), and the bilinear sampling smears every horizontal stroke over two rows.
- Measurement: a pixel counts as inked at 0.15 of the way from the tab fill to the label's ink,
  and as solid at 0.85.

| label | inked px | solid px | edge share |
|---|---|---|---|
| preview, focused tab | 284 | 21 | 93% |
| fix, focused tab | 270 | 111 | 59% |
| 16 px reference | 197 | 61 | 69% |

On the real dock bar, dark `focus.png` `session` tab:

| render | solid px | edge share |
|---|---|---|
| preview | 25 | 91% |
| fix | 111 | 59% |

**The call the app's tab drawing must use:** `imgui.push_font(None, style.font_size_base *
scale)`, then draw with `imgui.get_font_size()`, the bound size, and `pop_font()`. Never pass
`add_text` a size computed by hand. Pass `font_size_base` and not `get_font_size()` to
`push_font`, or the global scale factors apply twice (the binding's own note). The probe's
`tab_text_px` does exactly this, and `draw_inset_tabs` uses it.

**Side findings on the tabs:**
- "Text 1.10×" is 18 px, not 17.6. 1.15× is also 18 px.
- Round two's tabs sheet sized its text through `push_font`, so it drew both of those cells at
  18 px. That is why the sheet looked crisp and the preview did not.
- The lane at 18 px is 1 + 18 + 8 = 27, still inside the 28 px dock bar.

**Wells lighter than the pane.**
- Each step sets `Col_.frame_bg = mix(bg, text, t)`, for `t` in 0.06, 0.10 and 0.15. A dock well
  sits on `window_bg`, which is `bg`.
- On a dark palette this is lighter. On a light one, where `bg` is at or near white and nothing
  lighter is left, it is darker.
- `text` is the direction because every palette already holds it furthest from `bg`. So one
  `t` moves every well visibly off its pane, and text in the well loses contrast smoothly as
  `t` grows.
- `mix(panel, text_dim, t)` was rejected because it starts from `panel`. That is the same as
  `bg` on high_contrast but far from it on cde and win311, so the step would mean something
  different per theme.
- `frame_border_size` stays 0.
- Modal wells (settings, launcher) are `theme.dialog_colors`' `palette.well` and do not move
  with `t`, so `settings.png` changes only in its buttons and tabs.

Measured (WCAG 2.1 ratios of the 8-bit colours drawn, from `wells-light.png`). Each cell gives
the well on the pane, then text in the well:

| theme | pick (soft's fill) | t 0.06 | t 0.10 | t 0.15 |
|---|---|---|---|---|
| dark | 1.02 / 11.26 | 1.13 / 10.22 | 1.23 / 9.35 | 1.41 / 8.16 |
| light | 1.03 / 14.92 | 1.12 / 12.85 | 1.21 / 11.92 | 1.35 / 10.73 |
| high_contrast | 1.00 / 21.00 | 1.10 / 19.17 | 1.21 / 17.40 | 1.39 / 15.13 |
| ghost | 1.02 / 11.78 | 1.11 / 10.83 | 1.22 / 9.88 | 1.38 / 8.70 |
| xterm | 1.03 / 14.15 | 1.12 / 12.29 | 1.21 / 11.36 | 1.34 / 10.25 |
| cde | 1.14 / 7.42 | 1.11 / 5.86 | 1.20 / 5.43 | 1.31 / 4.98 |
| win311 | 1.18 / 8.58 | 1.13 / 6.45 | 1.22 / 5.98 | 1.35 / 5.41 |
| kitty | 1.06 / 11.21 | 1.11 / 9.52 | 1.19 / 8.88 | 1.31 / 8.09 |
| turbo | 1.07 / 14.46 | 1.06 / 14.58 | 1.14 / 13.63 | 1.26 / 12.25 |

- **Text** in the well stays at or above 4.5:1 on every theme at every step. The lowest is cde
  at 0.15, at 4.98.
- **The hint** (`text_dim`, the placeholder) does not. On win311 it is 2.97 at 0.06, 2.75 at
  0.10 and 2.49 at 0.15. On cde it is 3.57, 3.30 and 3.03. Already at the pick it is 3.95 on
  win311.
- **A button against a well** (CIE76 dE, where about 2.3 is just noticeable), lowest per step:
  kitty at 9 (0.06), kitty and light at 9 to 10 (0.10), and light at 9 (0.15).
  - On light at 0.15 the button and the well are 1.05:1 in luminance, so only their hue tells
    them apart.
  - At 0.06 cde and win311 move toward the pane rather than away from it: soft's fill was
    already 1.14 and 1.18 there.
- **Recommendation (builder's, not the operator's): t 0.10.**
  - On dark it is 1.23:1, the first step where the composer reads at a glance on
    `picked-wells-0.10/dark/focus.png`. On high_contrast it is 1.21, where the pick made the
    composer invisible.
  - 0.15 starts to read as a raised panel rather than a recessed field on dark, and costs
    win311 and cde their hint contrast.
- **Not handled:** `frame_bg_hovered` and `frame_bg_active` are still `apply_style`'s
  translucent accent over the pane. On a lighter well, hovering it may read *darker* than the
  well at rest. No image shows hover.

**Small button D is now part of `picked`.**
- `Variant.micro` makes every `imgui.small_button` call in the probe's process draw as
  wrapper D. `install_micro` patches the attribute on the `imgui` module, which every call
  site reads when it calls; `pptmstr/` is untouched.
- `dark/<screen>.png` shows it: the copy button, the board's filter chips, and the rows that
  hold them grow to 22 px.
- The wrapper sheet's "small_button today" row still draws ImGui's own small_button
  (`real_small_button`).

**What was run and what was not (fix-ups)**
- **Ran.** All of these were rendered from the working tree, which drew without error, so no
  `git archive` export was needed:
  - `--tab-text` and `--wells`
  - `--preview picked-wells-<t>` on dark, light and high_contrast, at each step
  - `--preview picked --theme dark`
  - `pytest tests/test_style_probe.py`: 190 passed
  - ruff, mypy and black on both files: clean
  - Three hand mutations, each killed by the new tests: the old tab size, a `panel`-based
    well, and an `uninstall_micro` that does not restore.
- **Not run:**
  - `--fixups` end to end. Its parts were run one by one, with the same arguments.
  - Real hover or press on a lighter well.
  - Any size but 1500x900 (the sheets are 1500x1300 and 1600x1720).
  - DPI scaling, where `font_scale_dpi` is not 1 and the bound size differs.
  - `make check` and the rest of the suite.
- **Looked at:** tab-text, wells-light, dark focus and board with D, dark and high_contrast at
  0.10. The other step screens were checked by exit code and file count only.

### Per-theme values (the operator's "keep the frame on high-contrast and fix light and xterm")

`final` is the pick with dock wells at `mix(bg, text, 0.10)` on every theme, plus the values a
theme needs of its own (`THEME_TUNING` in `scripts/style_probe.py`, built by `final_for`).

- **Images:** `shots/style-preview/fixups/final/<theme>/<screen>.png`, all six screens, for
  dark, light, xterm and high_contrast.
- **Regenerate:** `style_probe.py --final` renders them. `style_probe.py --quirks` prints the
  measured table below.

**What `apply-style` copies into `theme.py`:**

| value | dark, xterm (and the other five) | light | high_contrast | where it goes in `theme.py` |
|---|---|---|---|---|
| `style.frame_border_size` | 0 | 0 | **1** | `apply_style`, as `child_border_size` already special-cases `high_contrast`. No new field. |
| dock well `Col_.frame_bg` | `mix(bg, text, 0.10)` | **`mix(bg, text, 0.08)`** = #e3e4e8 | `mix(bg, text, 0.10)` = #1a1a1a | `apply_style` sets it from `panel_alt` today. **New field needed:** `Palette.well` is the modal well, held to its own floor, so this needs a separate role, for example `dock_well`, or a per-palette `t`. Dark: #26282f; xterm: #d4d2c8. |
| `Col_.button` / `_hovered` / `_active` | `mix(panel_alt, accent, 0.20 / 0.36 / 0.52)` (soft's) | **`mix(panel_alt, accent, 0.36 / 0.52 / 0.68)`** = #9bbfd4 / #77abc6 / #5397b9 | soft's | `apply_style` sets `panel_alt` and a translucent accent today. **New field needed:** a per-palette tint, for example `button_tint` = 0.20, or 0.36 on light. |
| tool block ground / text / dim | the mock's near-black #0b0b0c, #c7c7c7, #8c8c91 | **`mix(bg, text, 0.06)`** = #e7e8ec, `text`, `text_dim` | as dark | **New fields needed** (`terminal_bg`, `terminal_text`, `terminal_dim`); xterm takes light's derivation (#dcdad0). The real tool block is not in the app yet, so this is for whoever builds it. |

Why light's well is 0.08 and not 0.10: at 0.10 the hint (`text_dim`) in a light well is 4.38:1,
under AA. At 0.08 it is 4.55:1, and the well is still 1.17:1 off the pane, against 1.03 at the
pick.

**The quirks, measured** (`--quirks`). Each cell gives the pick, then `final` where the value
moved, then the colour drawn and its CIE76 dE.

| what | dark | light | xterm | high_contrast |
|---|---|---|---|---|
| dock well on the pane | 1.02 -> 1.23 | 1.03 -> 1.17 | 1.03 -> 1.21 | 1.00 -> 1.21 (+ white outline) |
| text in the well | 11.26 -> 9.35 | 14.92 -> 12.38 | 14.15 -> 11.36 | 21.00 -> 17.40 |
| hint in the well | 4.68 -> **3.89** | 5.48 -> 4.55 | 6.32 -> 5.07 | 11.54 -> 9.57 |
| button on the pane | 1.75 | **1.41 -> 1.78** | 1.72 | 1.72 |
| button text on the button | 6.58 | 10.26 -> 8.09 | 8.00 | 12.23 |
| hovered button on the pane | 2.54 | 1.78 -> 2.29 | 2.52 | 2.77 |
| button against the well | 1.71 -> 1.42 (dE 15) | 1.45 -> 1.53 (dE 21) | 1.77 -> 1.42 (dE 24) | 1.72 -> 1.42 (dE 20) |
| selected tab on the lane | 1.09 | 1.17 | 1.24 | 1.10 |
| unselected tab text | 4.45 | 5.78 | 6.73 | 11.54 |
| focused tab's accent rule | 7.46 | 4.79 | 8.26 | 12.37 |
| scrollbar grab on its track | 1.95 | 1.89 | 2.00 | 2.86 |
| separator on the pane | 1.55 | 1.52 | 2.37 | 21.00 |
| tool block on the pane | 1.09 | **18.05 -> 1.12** | **15.72 -> 1.12** | 1.07 |
| tool block text | 11.64 | 11.64 -> 12.85 | 11.64 -> 12.29 | 11.64 |
| tool block dim text | 5.88 | 5.88 -> 4.72 | 5.88 -> 5.48 | 5.88 |

**What read worse than dark on light and xterm, and what was done:**
- **Light's buttons** were 1.41:1 on the pane against dark's 1.75. Tuned to 1.78.
- **The tool block's near-black** was a slab on both: 18.05:1 on light and 15.72:1 on xterm.
  Tuned to a soft slab at 1.12:1, which is below the well's step so it does not read as a
  field, with the palette's own inks. The fixed greys it had would draw at 1.38:1 and 2.73:1 on that ground,
  so the inks had to change with the ground.
- **The light wells** are described above.
- **No worse than dark, so left alone:**
  - The scrollbar grab: 1.89 and 2.00 against 1.95.
  - Separators: 1.52 and 2.37 against 1.55.
  - The selected tab on its lane: 1.17 and 1.24 against 1.09.
  - On light the selected tab is *darker* than both the white lane and the pane, so it does
    not join the pane. On dark it is lighter than both and does not join either. The two are
    the same shape, so nothing was proposed.
- **Lower than dark but above 3:1 for non-text:** light's focused-tab accent rule, at 4.79
  against 7.46. Left alone.

**What the per-theme values do not settle, for the operator:**
- **Dark's hint in the well falls to 3.89:1 at t 0.10**, below AA, from 4.68. Dark's
  `text_dim` is only 4.68 on the pane itself, so any well lighter than the pane takes it under
  4.5.
  - The choices are: accept it for placeholder text; lighten dark's `text_dim` (a palette
    change, held by `tests/test_theme.py`); or keep dark's well at 0.04, where it is 1.07:1
    and close to what the operator called lost.
  - This was not chosen here.
- **On high_contrast the tool-block mock is 1.07:1 on the black pane,** and dark is 1.09, ghost
  1.04. It was not asked about and was left alone. The real block will need an outline or a
  ground off black there.
- **The caption** now takes five lines on `final` screens and covers the composer's button
  row. The buttons are measured in the table and drawn on `wells-light.png`.

**Run:**
- `--final` for the four themes, exit 0. Re-run for light after its well moved to 0.08.
- `--quirks`.
- `pytest tests/test_style_probe.py`: 212 passed.
- ruff, mypy and black.

**Looked at:** final light terminal-block, final high_contrast settings, final xterm focus.
The other final screens were checked by exit code only.

On the preview's theme findings the operator decided: "Let's keep the frame on high-contrast
and fix the light and xterm themes for the well fills (and other quirks)". So
`frame_border_size` stays 1 on `high_contrast` only, and every other theme is borderless,
with a well fill that reads. The operator confirmed again: "small_button variant D".

The tool block (`2026-10-15-tool-block-variants.md`): "'Stacked' tool block looks great for
bash!" The lead's defaults, which the operator has not ruled on:
- Stacked's layout is used for every tool, with the per-tool headers it already draws (Edit as
  a mini diff, Read as a path and line range, WebFetch as domain and prompt).
- A Bash block shows the *last* lines of its output, where a test or build says whether it
  passed. A Read block shows the first.
- Errors get stacked's red rule.
- The near-black well comes from each theme's colours, not a fixed `#0b0b0c`, because on
  light themes a fixed colour would draw a black slab.

## The operator's pick (2026-10-15)

From the round-two sheets, in the operator's words:

- **Buttons:** "imgui.button - frame_padding 9x6 rounding 5 with no outline or frame
  border". For `small_button`: "none of these look good as the text is crowded". Nothing is
  picked for it, and round two's finding stands: ImGui forces its vertical padding to 0, so
  any taller micro button is code, not a style value.
- **Tabs:** "pad 10x4 gap 4 inset 1 text 1.10x text 17.6 lane 26.6". Inset 1 is between
  the sheet's steps. The lane, 26.6, fits inside the 28 px frame height, so no
  `frame_padding.y` change is forced.
- **Lines:** "Level 1, Softs fill only (this is the one I'm least sure of)". In the dark theme
  that is `frame_border_size 0` with soft's well fill at 1.02:1 against the pane. Round two
  measured that unchecked checkboxes and radios nearly vanish at that setting (sheet 3). The
  operator chose it knowingly, and it is consistent with the buttons having no frame border,
  because `frame_border_size` is one value for both.
- **Dropdown:** "No outline, popup rounding 4 tinted arrow (soft + wells)". That combines the
  flat cell's borders with the framed cell's rounding.

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

## Preview of the pick (task `style-preview`)

The operator's pick (top of this record), rendered on round one's six screens for every
theme `theme.py` defines, beside today's style. A sheet of `small_button` wrapper variants
answers "crowded".

### What to look at

| image | what it is |
|---|---|
| `shots/style-preview/<theme>/<screen>.png` | the pick (`style_probe.PICKED`) |
| `shots/style-preview/<theme>/before-<screen>.png` | the same screen in today's style (`current`, no overrides) |
| `shots/style-preview/small-button.png` | four wrapper variants beside today's `small_button` and the picked 9x6 `button`, in four states and four pane rows (1500×1300) |

`<theme>` is each of the nine in `theme.THEMES`: `dark`, `light`, `high_contrast`, `ghost`,
`xterm`, `cde`, `win311`, `kitty`, `turbo`. `<screen>` is one of round one's six. The
screens are 1500×900, with 108 of them plus the sheet.

Each picked image states its values in the menu bar, right-aligned, and what the
shared values do in the status bar's right half. Each before image states "today's
`theme.apply_style`, no overrides". The caption is yellow on black, fixed rather than
from the palette, so it reads the same on every theme. It covers only empty bar space on
five screens. On `launcher.png` it covers the bottom 40 px of the modal, which already
overflows 900 px (round two).

Regenerate with `.venv/bin/python scripts/style_probe.py --style-preview` (optionally
`--themes dark light`). The theme is put into the settings the app reads at start
(`settings.read` is wrapped in the probe's process), so the settings dialog's combo shows
the theme that is drawn.

### What `picked` is

| pick (operator's words) | value applied | from |
|---|---|---|
| buttons: "frame_padding 9x6 rounding 5" | `frame_padding (9, 6)`, `frame_rounding 5` | soft |
| buttons: "no outline or frame border" | `frame_border_size 0` | soft |
| lines: "Level 1" | `Col_.border` = `Col_.separator` = `mix(bg, border, 1)`, the palette's border, which is what `apply_style` already sets | line level 1 |
| lines: "Softs fill only" | `frame_border_size 0`, `Col_.frame_bg = mix(bg, panel, 0.35)` | well option 2 |
| dropdown: "No outline" | `frame_border_size 0` (field), `popup_border_size 0` (popup) | flat cell |
| dropdown: "popup rounding 4" | `popup_rounding 4` | framed cell / soft |
| dropdown: "tinted arrow" | soft's `Col_.button = mix(panel_alt, accent, 0.2)` | soft |
| tabs: "pad 10x4 gap 4 inset 1 text 1.10x" | drawn tabs, `TabSpec(pad 10x4, gap 4, inset 1, scale 1.10)`, lowercase; lane 26.6 px in the 28 px bar; on a dock 8 px tab to tab, 2 px from the lane's left | between round two's steps |

The pick is `soft` plus `popup_border_size 0`, with the level-1 line colours written out.
A test (`test_the_pick_is_soft_plus_only_the_popup_outline_and_the_written_out_lines`) holds
this.

Soft's other values were not picked by name: `scrollbar_size 16`, `scrollbar_rounding 8`,
`scrollbar_padding 3`, `grab_rounding 5`, `item_spacing (8, 6)`, `tab_rounding 5` and
`Col_.scrollbar_grab`. They are kept because every round-two sheet the operator picked
from was drawn on them. **Decided by the builder in the unattended session.** The image
says so.

### What each shared value forced

- **`frame_border_size 0` is one value for three picks.** Buttons' "no outline", the
  lines' "soft's fill only" and the dropdown field's "no outline" all set it, and they
  agree, so nothing was chosen between them. What it does: no frame anywhere has an
  outline. That covers wells, the composer, check and radio boxes, combos and buttons.
- **`Col_.button` is the tinted arrow and every button's fill.** The "tinted arrow" is
  soft's button tint and cannot be scoped to the arrow (round two, sheet 4). The two picks
  agree.
- **`popup_border_size 0` and `popup_rounding 4` apply to every popup.** That means combo
  lists, menus and context menus, not only dropdowns.
- **`frame_padding 9x6` applies to every frame and sets the dock tab bar to 28 px.** The
  tab pick's lane of 26.6 px fits inside it, so nothing else grew. `small_button` still
  forces y to 0 and stays 16 px.
- **Level-1 lines change nothing.** They are the palette's `border`. With
  `frame_border_size 0`, `Col_.border` now draws only child outlines, popup outlines (none,
  at 0), the pane edges and window borders.

### What reads worse than the sheets suggested

Measured: WCAG ratios from the 8-bit colours, computed with `style_probe.contrast` in a
one-off run over every palette. The screens were looked at for `dark`, `light`,
`high_contrast` and `win311`.

| theme | soft's well fill on the pane | today's well fill on the pane | picked button fill on the pane |
|---|---|---|---|
| dark | 1.02:1 | 1.17:1 | 1.75:1 |
| light | 1.03:1 | 1.07:1 | 1.41:1 |
| **high_contrast** | **1.00:1** | 1.10:1 | 1.72:1 |
| ghost | 1.02:1 | 1.19:1 | 1.83:1 |
| xterm | 1.03:1 | 1.13:1 | 1.72:1 |
| cde | 1.14:1 | 1.76:1 | 1.34:1 |
| win311 | 1.18:1 | 1.85:1 | 1.21:1 |
| kitty | 1.06:1 | 1.09:1 | 1.17:1 |
| turbo | 1.07:1 | 1.44:1 | 1.90:1 |

- **On `high_contrast` the docks' wells are gone.** `bg` and `panel` are both black, so
  soft's fill `mix(bg, panel, 0.35)` is black on black, 1.00:1. Its border, at 21:1, was
  the only thing drawing them, and `frame_border_size 0` removes it. In
  `high_contrast/focus.png` the session composer is an empty black area under "this session
  is waiting for you". Every dock input is the same. This is worse than sheet 3 suggested,
  because sheet 3 was drawn on `dark` only.
- **On `light` the dock composer is barely there.** It is 1.03:1 on the pane
  (`light/focus.png`). Today it is outlined.
- **Modal wells survive.** `theme.dialog_colors` pushes `palette.well` over `frame_bg`,
  so inside settings and the launcher the wells are a separate fill: 1.09–1.56:1 against
  the popup, and visible on every theme looked at. In `light/launcher.png` and
  `high_contrast/launcher.png` the fields read as fields.
- **On `kitty` and `win311` buttons barely separate from the pane.** Buttons are 1.17:1
  and 1.21:1, against 1.7–1.9:1 on the dark themes. Soft's tint is 0.2 accent over
  `panel_alt`, and on those palettes the two are close in luminance.
- **Unchecked checkboxes and radios** are not on any of the six screens in a dock: the
  settings screen shows the Appearance page, which has none. So this preview neither
  confirms nor refutes the borderless-checkbox concern on `light` or `high_contrast`. By
  the table above, a borderless box on `high_contrast` is 1.00:1, which is invisible, and
  on `light` it is 1.03:1.
- **The `before-*.png` screens show ImGui's own uppercase tabs**, so each pair also
  compares those against the drawn tabs. That is intended: the drawn tabs are part of the
  pick.

The fix this points to is a value choice, not code, and it is the operator's. Options:

- exempt `high_contrast` from `frame_border_size 0` (round one's recommendation);
- or give `frame_bg` a per-theme fill that clears the pane;
- or accept invisible dock wells on that theme.

### The `small_button` wrapper (`small-button.png`)

ImGui's `small_button` forces `frame_padding.y` to 0, so no style value makes it less
crowded. Each variant is a hypothetical `widgets.small_button` that calls `imgui.button`
with its own pushed `frame_padding`, `frame_rounding` and, optionally, font size
(`style_probe.micro_button`). Adopting it means routing 42 call sites through it.

| variant | frame_padding | rounding | text | height |
|---|---|---|---|---|
| today's `small_button` (the pick) | 9 x (forced 0) | 5 | 16 px | 16 px |
| A | 6x2 | 4 | 16 px | 20 px |
| B | 8x3 | 5 | 16 px | 22 px |
| C | 6x3 | 4 | 14 px (0.875×) | 20 px |
| D | 8x4 | 5 | 14 px (0.875×) | 22 px |
| `button` (the pick) | 9x6 | 5 | 16 px | 28 px |

The variants are two heights at two text sizes, so text size and padding can be judged
apart. 14 px is a whole pixel. Heights are measured from the drawn item: each cell prints
"h N px = label", or a warning if the measurement and the label disagree. A test measures
every variant against its label.

**Row cost.** A pane row is as tall as its tallest button, not as its 16 px text line. A
row holding A or C is 20 px (+4, a pitch of 26). B or D is 22 px (+6, pitch 28), and the
9x6 button is 28 px (+12, pitch 34). The sheet measures this as the height of the whole row
in four real contexts:

- detail's "wants approval · 0s [copy]";
- the transcript's "1,204 earlier lines";
- the board's filter chips, with `done` hidden;
- a board card's "amend / close...".

A test holds every row to max(text line, button). In the app, text before a taller
button is top-aligned, because nothing calls `align_text_to_frame_padding`. The sheet
shows it that way, and the text sits visibly high beside A–D.

### What was run and what was not (preview)

Run (builder-18, 2026-10-15, through `scripts/xvfb_shot.sh`, Xvfb + llvmpipe):

- `scripts/style_probe.py --style-preview`: the sheet and 18 screen runs (9 themes ×
  before/picked), every one exit 0, giving 109 images.
- **Against `HEAD`'s `pptmstr/`, not the working tree.** While this ran, another task
  (`delete-rich`) was partway through removing `context_mode`. `app.py` called a
  `transcript_pane.mode_named` that no longer existed, and the probe's scenario still set
  `context_mode="typeset"`, so a working-tree run failed at the CONTEXT pane. I exported
  `HEAD` with `git archive` into a scratch directory under `shots/style-preview/`, laid this
  probe over it, rendered there, copied the images out and deleted the scratch. After the
  render, `_scenario`'s `context_mode="typeset"` line was dropped at `delete-rich`'s
  request (typeset is the pane's default), so the probe no longer names that field. That
  edit came after the images and was not re-rendered.
- `pytest -q tests/test_style_probe.py`: 142 passed. 46 tests are new: the pick's values
  against the operator's words, apply and revert being exact on all nine themes, level 1
  being `apply_style`'s own colours, the caption, the wrapper heights against their labels
  (6 kinds), and row heights (6 kinds × 4 contexts). The same tests passed against
  `HEAD`'s tree.
- A mutation run on the scratch copy. 10 mutations, all killed:
  - the popup border kept;
  - inset 2;
  - lines at level 3;
  - the wrapper without its padding push;
  - the wrapper without its font;
  - the height counting one pad;
  - the wrapper as `small_button`;
  - one pop short;
  - the baseline height off by 2;
  - the caption without its shared-value lines.
- `make format-file`, `ruff check` and `mypy` on `scripts/style_probe.py`.

Not run:

- looking at every one of the 109 images: `dark`, `light`, `high_contrast` and `win311`
  were looked at, the other five themes only by exit code and file count;
- an unchecked checkbox or radio in a dock on any theme: none of the six screens has one;
- real hover and press: these are drawn by pushing the state colour, as in round two;
- any size but 1500×900 (1500×1300 for the sheet), and DPI scaling;
- `make check` and the rest of the suite;
- the working tree's `pptmstr/` (see above).
