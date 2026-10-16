# Link variants: a hyperlink-style control for the tool-block footer and the (?) marks

**Dated:** 2026-10-16 · **Status:** a probe for the operator to pick from. Nothing the app
draws has changed · **Answers:** the operator's "a hyperlink style of interaction that will
take click actions ... the tool block footer (could be a hyperlink style that doesn't steal
spacing) and as the '(?)' in many dialogs"

## What to look at

All images are under `shots/link-variants/`. They are gitignored, rendered locally at
1560x1240 over the running `--fake` app with the current `theme.py`.

| image | what it is |
|---|---|
| `dark.png`, `light.png`, `high_contrast.png` | the variants: 3 colours x 3 underline modes x 4 states |
| `dark-context.png`, `light-context.png`, `high_contrast-context.png` | (a) footer, (b) marks, (c) baselines |

To regenerate, run `.venv/bin/python scripts/link_probe.py --all`. Each theme runs on its own
Xvfb through `scripts/xvfb_shot.sh`. `--describe` prints every cell's values without a
display. The tests are in `tests/test_link_probe.py`.

## What `imgui.text_link` is

The installed binding is `text_link(label: str) -> bool`, at Dear ImGui 1.92.9. What follows
was measured from its own draw-list vertices in `tests/test_link_probe.py`, and agrees with
upstream's `TextLink`:

- **Ink.** It draws in `Col_.text_link`. `theme.apply_style` never sets that slot, so on every
  theme it is ImGui's stock blue `(0.26, 0.59, 0.98)`. Every variant pushes its own ink.
- **States.** Hovered, it raises the ink's HSV value by 0.3 and turns the hue by 0.02. Held,
  it raises the value by 0.4. The underline is the label's colour with the value 0.2 lower.
  The rule knows nothing about the ground, which matters below.
- **Underline.** It is always drawn, 1 px, `floor(descent x 0.2)` above the foot of the box,
  which keeps it inside the item's own box. ImGui has no switch to turn it off.
- **Box.** The box is the label's own size: 64 x 16 px for "show all" at 16 px. There is
  **no padding**, so there is no tighter-padding cell to draw.
- **Placement.** The box is placed at the line's `curr_line_text_base_offset`, the same way
  text is.
- **Disabled.** It has no disabled look of its own. Under `begin_disabled` the style alpha
  of 0.6 is applied to the underline once and to the label **twice**, giving 0.36. That is
  because the label's colour is pushed through `GetColorU32` and `RenderText` applies the
  alpha again. A click while disabled answers False.

Hover-only and never-underlined cannot be had from `text_link`. Those cells use
`link_probe.link`, which is `TextLink` rebuilt line for line through `imgui.internal`: the
same id, box, `ButtonBehavior`, ink rule and draw order, with the underline as a parameter.
With the underline always on, its vertices, rect, id and click answers equal `text_link`'s.
The tests check this at 13, 16 and 20 px, enabled and disabled, for two inks. On the
sheet, **T** marks a cell drawn by `text_link` and **W** a cell drawn by the wrapper. Hovered
and pressed cells are painted in their state by the wrapper, because one frame can only
hover one item.

## The cells

Contrast is measured against the pane ground `bg` after compositing. Read each entry as
label / underline, with "-" where no underline is drawn. The underline only changes between
modes: always draws it in every state, hover-only draws it in hovered and pressed only,
never draws it in no state. The label is the same in all three.

**dark** (bg `#14161c`)

| colour, ink | normal | hovered | pressed | disabled |
|---|---|---|---|---|
| accent `#4fc1e9` | `#4fc1e9` 8.73 / `#3e97b6` 5.43 | `#56bfff` 8.86 / `#4599cc` 5.75 | same as hovered | `#295466` 2.20 / `#2d6378` 2.73 |
| text_dim `#7c8397` | `#7c8397` 4.78 / `#525764` 2.50 | `#bbc0e4` 10.14 / `#9195b1` 6.15 | `#d0d6fd` 12.67 / `#a6abca` 8.01 | `#393d48` 1.67 / `#393d47` 1.66 |
| text `#c9cedc` | `#c9cedc` 11.49 / `#9a9ea9` 6.75 | `#e9ecff` 15.42 / `#babdcc` 9.68 | same as hovered | `#555861` 2.54 / `#656871` 3.25 |

**light** (bg `#f4f5f8`)

| colour, ink | normal | hovered | pressed | disabled |
|---|---|---|---|---|
| accent `#0c6e9e` | `#0c6e9e` 5.15 / `#084a6b` 8.75 | `#1289eb` **3.32** / `#0e6bb8` 5.05 | `#1395ff` **2.85** / `#0f77cc` 4.25 | `#a0c4d8` 1.69 / `#668fa3` 3.20 |
| text_dim `#5e6675` | `#5e6675` 5.30 / `#353a42` 10.50 | `#9ba4c1` **2.27** / `#72798e` 3.98 | `#b0badb` **1.77** / `#878ea8` 2.98 | `#bec2c9` 1.64 / `#81858b` 3.40 |
| text `#1f232c` | `#1f232c` 14.43 / `#000000` 19.26 | `#555c79` 6.03 / `#313546` 11.15 | `#676f92` 4.52 / `#43485f` 8.26 | `#a7a9af` 2.16 / `#626263` 5.59 |

**high_contrast** (bg `#000000`)

| colour, ink | normal | hovered | pressed | disabled |
|---|---|---|---|---|
| accent `#00e5ff` | `#00e5ff` 13.65 / `#00b7cc` 8.65 | `#00c6ff` 10.52 / `#009fcc` 6.83 | same as hovered | `#00525c` 2.36 / `#006e7a` 3.51 |
| text_dim `#c0c0c0` | `#c0c0c0` 11.54 / `#8d8d8d` 6.33 | `#ffffff` 21.00 / `#cccccc` 13.08 | same as hovered | `#454545` 2.19 / `#555555` 2.82 |
| text `#ffffff` | `#ffffff` 21.00 / `#cccccc` 13.08 | **identical to normal** | identical to normal | `#5c5c5c` 3.14 / `#7a7a7a` 4.89 |

What the numbers say:

1. **On light, `text_link`'s hover rule lowers contrast.** It always moves toward brighter,
   and on a light ground brighter means closer to the ground. The accent drops from 5.15 to
   3.32 hovered and 2.85 pressed. text_dim drops to 2.27 and 1.77.
2. **On dark, the accent barely changes on hover** (8.73 to 8.86): its value is already
   0.91, so +0.3 saturates and only the 0.02 hue turn is left. Hover feedback there is
   mostly the hand cursor.
3. **On high_contrast, a text-coloured link shows no hover at all.** White is already at
   value 1, and the hue turn does nothing to a grey.
4. **text_dim's underline is 2.50:1 on dark at rest.** Where the underline is the only cue
   that it is a link, it is a faint one.
5. Disabled is 1.6 to 3.1:1 everywhere. That is what an ImGui disabled item looks like.

## In context, at the picked set (accent, underline always)

### (a) The tool-block footer

The block is a real Read of 47 lines through `tool_block.layout` and `tool_block.draw`. The
footer reads "41 more lines · show all": the count in the block's `terminal_dim`, then
"show all" as the link. Expanded, "collapse" is the link. The link's ink is
`terminal_ink(accent)`, the block's own link ink: `#4fc1e9` at 9.62:1 on dark's terminal
ground and `#00e5ff` at 11.31:1 on high_contrast's. The swap from `widgets.small_button`
is made only for that one draw call (`_links_in_blocks`), and is restored even if the draw
raises.

| | small_button today | link | saved |
|---|---|---|---|
| collapsed block | 179 px | 173 px | **6 px** |
| expanded block | 161 px | 155 px | **6 px** |

The 6 px is the 22 px button less the 16 px text line. It is the same on all three themes
(measured), and `tests/test_link_probe.py` pins it at 16 px. In a bare context at 13 px it
is 9 px, and at 20 px it is 3 px.

### (b) The marks

There are two labels, because the operator has not chosen one: "(?)" and "why?". The notice
line is drawn the way `widgets.notice` draws a quiet one, minus `small_button_line`: a link
sits on the text's own baseline, so nothing has to be set down. The launcher caption is the
gate field with `dialog_caption` and the notice under it, inside `dialog_style`.

| row | today's small_button (?) | link (?) | link why? |
|---|---|---|---|
| quiet notice on the pane | 22 px | 16 px | 16 px |
| launcher caption | 22 px | 16 px | 16 px |

### (c) Baseline

These are the link's label baseline minus its line's baseline. The row "small_button after
the link" is the other way round: the button's label baseline minus the link's.

| case | 13 px | 16 px | 20 px |
|---|---|---|---|
| after plain text | 0 | 0 | 0 |
| after frame-aligned text | 0 | 0 | 0 |
| after a field | 0 | 0 | 0 |
| after a small_button | 0 | 0 | 0 |
| small_button after the link | +5 | +2 | 0 |

The 16 px column was measured in the app on every theme. All three columns were measured in
the bare context the tests use, and the tests pin them. The last row is small-button-fix's
case where the button cannot rise above the line: it equals `max(0, 4 - lift)`, where lift
is the UI face's ascent less the 14 px face's. It only happens when a link starts a line
and a button follows it, which neither the footer nor the marks do.

## Recommendation (the builder's, not the operator's)

- **Use the accent with the underline always on, for both sites.** The ImGui default is the
  underline. The notice's own rule, "hue is never the only channel here", rules out the
  hover-only and never modes at rest. text_dim would make the underline the only cue, and
  that underline is 2.50:1 on dark.
- **Don't ship `text_link` as it is.** Its ink slot is unthemed, and its hover rule fails
  4.5:1 on light (3.32 hovered, 2.85 pressed). The wrapper this implies is
  `link_probe.link` with one change: in its hovered and pressed states, a theme-aware rule
  that moves away from the ground. That would be done in `widgets`. This probe does not
  build it.
- **Footer:** "N more lines · show all" saves 6 px per cut block at 16 px, and the count
  still reads as information, not as a control.
- **Marks:** either label sits at 0 px on every line it was measured on, and saves 6 px per
  row. On the choice of label: "why?" says what is behind it, and "(?)" is the narrower of
  the two (24 px against 32 px at 16 px, measured in Inconsolata). That choice is the operator's.
