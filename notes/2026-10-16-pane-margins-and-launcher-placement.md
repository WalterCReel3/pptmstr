# The session pane's margins and the launcher's placement

**Dated:** 2026-10-16 · **Status:** measurement at HEAD 4d94a32; the proposed fixes
are the builder's and nothing here is decided · **Probe:** `scripts/probe_pane_layout.py`
· **Board task:** `layout-probe`; the fixes are `pane-margins` and a launcher task to be
declared

**This is not a planning record.** It answers two things the operator said:

1. "In the session pane there is left padding or margin where as on the right there is
   none. Split the space that was allocated on the left to be balanced on the right as
   well." (seen in `shots/tool-block-app/`)
2. "default placement of the launcher dialog doesn't seem calculated or _is_ calculated
   but placing the dialog too low on the screen."

If a fix below is adopted, the decision belongs in `planning/`.

## How it was measured

`--fake`, dark theme, FOCUS, headless under `scripts/xvfb_shot.sh` (Xvfb, llvmpipe, 1x).
The window was resized in-process with `hello_imgui.change_window_size`. There were
three runs, at sizes 1500x900 → 1920x1080, 1920x1080 → 1280x800, and 1500x1600:

    XVFB_SHOT_SCREEN=2000x1200x24 scripts/xvfb_shot.sh -- .venv/bin/python scripts/probe_pane_layout.py 1500x900 1920x1080
    XVFB_SHOT_SCREEN=2000x1200x24 scripts/xvfb_shot.sh -- .venv/bin/python scripts/probe_pane_layout.py 1920x1080 1280x800
    XVFB_SHOT_SCREEN=2000x1700x24 scripts/xvfb_shot.sh -- .venv/bin/python scripts/probe_pane_layout.py 1500x1600

1500x1600 is there because no root session in the fake fleet both fits its log and
draws a tool block at the other sizes. At 1500x1600, "streaming" does.

The probe takes each number from two independent places:

- **ImGui's state that frame.** Each window under SESSION gives its rect, inner rect,
  work rect and scrollbar. Each window's draw list gives the x extent of its vertices,
  with glyph quads and shapes kept apart.
- **The PNG of that same frame.** The probe scans it band by band for the first and
  last pixel off the pane's ground. The bands are the typeset text, each tool block,
  the controls/find row and the composer.

The two sources agree to within the half pixel of an antialiased edge. The images and
`measurements-*.json` / `raw-*.json` are in `shots/layout-probe/`, which is gitignored.

## 1. The session pane's side margins

### Numbers

These are px, in the order left | right. Each gap runs from an edge to the first or last
ink. The style is the same at every size: font and body 16 px, `window_padding` 8,
`scrollbar_size` 16, `margin_px(16)` = 8, and `log_sides(16, 16)` = (16, 0).

| at 1500x900 (pane x 316–1190) | from the pane's edge | from `##scrollback` | ink to the log's gutter |
|---|---|---|---|
| tool block, bar shown | 24 \| 24 | 16 \| 16 | **0** |
| tool block, bar hidden (1500x1600) | 24 \| 24 | 16 \| 16 | 0, and the gutter is empty |
| typeset text, bar shown (ragged; its measure ends where a block does) | 24 \| 24–34 | 16 \| 16–26 | 0–10 |
| typeset text, bar hidden | 24 \| — | 16 \| — | (a short log) |
| copy / find row | **8** \| — | **0** \| — | — |
| composer box | **8** \| 9 | **0** \| 1 | (it is not in the log) |

**Geometry at 1500x900:**

- `##scrollback` runs from x 324 to 1182.
- `##transcript` has the same outer rect, and its inner rect ends at 1166, the gutter's
  left edge.
- Every block's rect is 340–1166.
- The log's draw list puts glyphs from 340 to 1162 and shapes from 340 to 1166.

**At other sizes:** 1920x1080 (pane 316–1610) and 1280x800 (pane 316–970) give the same
offsets on both sides, because only the right edge moves. The block is 340–1586 and
340–946. At 1280x800 a typeset line reaches 945, so it touches the bar.

**Bar shown and hidden** have identical rects. `_begin_log` always reserves the gutter
(`always_vertical_scrollbar`) and only makes the bar's ink transparent when the log does
not overflow (`TranscriptState.overflowing`). The one thing that changes is whether the
16 px gutter beside the content holds a bar or is empty.

### Where each left offset comes from

From the pane's edge to the log's first ink is 24 px: 8 from the pane plus 16 from the log.

1. **8 px: SESSION's `window_padding.x`.** This is `style.window_padding = (8, 8)` in
   `theme.py`. It is the only offset the copy/find row and the composer get.
2. **0 px: `##scrollback`.** It is an unbordered child, and ImGui gives an unbordered
   child no padding (`app.session_pane`).
3. **16 px: `transcript_pane.log_sides`.** It returns
   `left = max(margin_px(body), scrollbar_size) = max(8, 16) = 16` and
   `right = left - gutter = 0`. `_begin_log` applies `right` as the child's
   `WindowPadding.x` on both sides, which is 0, and `left - right` = 16 as an
   `imgui.indent`.
4. **0 px: the tool block's own offset.** `tool_block.draw` begins its child at the
   cursor with width 0, which means up to the work rect's right edge. Its `_PADDING`
   (10, 6) is inside its box. The 339 seen in find frames is the find mark's 2 px
   outline (`_ROUNDING`, `add_rect`), drawn 1.5 px outside the block.
5. **pane-fix's "12/12" is now 16/16.** pane-fix measured 12/12 with `scrollbar_size`
   12. The picked style (`theme.py`) set it to 16, and `log_sides` follows the gutter,
   so the left margin grew with it. The left margin is not the en dash
   (`margin_px` = 8). It is the gutter's width.

### Cause: what the operator is seeing

The visible margins are 16 | 0, or 16 | 0 + bar. `log_sides` counts the reserved gutter
as the right-hand margin, so the content's right padding is `16 - 16` = 0.

- **With the bar drawn**, which is every log that overflows (all of
  `shots/tool-block-app/`), a block's right edge and the text measure sit flush against
  the scrollbar. On the left there are 16 empty pixels.
- **Only on a log short enough to hide the bar** do the two sides read balanced, at
  16 | 16, the second 16 being an empty gutter.

That second state is what pane-fix item 7 was asked for ("make the visible margins equal
(the gutter counts toward the right margin)"). The operator is now looking at the first
state.

### Proposed fix (builder's)

Split the 16 px: **8 | 8 against the bar.** That is the en dash either side, with the
gutter outside the right one. In `transcript_pane.log_sides`:

```python
side = margin_px(text_px)
return side, side
```

In `_begin_log`, the `imgui.indent(left - right)` branch then never runs and goes. Push
`WindowPadding = (margin, 0)` as before.

- **The measure does not change.** It is 340–1166 now and would be 332–1158, 826 px
  both ways. So nothing reflows, and pane-geometry's property holds: the gutter is
  still always reserved.
- **Bar shown:** 8 | 8 | bar.
- **Bar hidden:** 8 | 8 + an empty 16 = 24.

**The cost is a conflict, which needs ruling on.** No layout that keeps the gutter
reserved is balanced in both states. Balancing against the empty gutter as well would
mean moving the column when the bar appears, and that changes the measure. So this
fix undoes pane-fix item 7's short-log balance in favour of the common case, a log
that overflows. The other option is 16 | 16 + bar: the measure shrinks by 16 px and a
short log reads 16 | 32.

**Tests that pin today's rule and would change:**

- `tests/test_session_pane_geometry.py::test_the_sides_are_the_margin_or_the_gutter_whichever_is_wider`
- `::test_the_wrapped_log_has_the_same_margin_either_side`, which asserts
  `max(margin, gutter)`
- `::test_the_typeset_log_has_the_margin_at_its_text_size_and_keeps_its_width`, read for
  the gutter case

**Out of scope, noticed:**

- The copy/find row and the composer start at the scrollback's edge (324). The log
  text starts 16 px further in today and would start 8 px further in after the fix.
  Aligning them would be a separate change in `_draw_controls` and `compose`.
- At 1280x800 the composer's hint line ("interrupt stops this turn; close ends the
  session") is wider than the pane and is clipped at its edge: SESSION's draw list
  has glyphs to 969 in a pane ending at 970.

## 2. The launcher's default placement

### Where its position is set

`launcher._modal` sets it:

- `imgui.set_next_window_pos(viewport.get_center(), Cond_.appearing, pivot (0.5, 0.5))`
  centres it on the **whole viewport**, on the frame it appears.
- On later frames, `modal_origin` moves it only if it has left the **work area**. It is
  moved to the nearest position inside (`Cond_.always`).
- `set_next_window_size_constraints` fixes the width at `_WIDTH` and caps the height
  at `work_size.y`.
- The window is `always_auto_resize`, and its body is `widgets.begin_dialog_body`, an
  `auto_resize_y` child.

### Numbers

All values are px, read every frame from `imgui.internal.find_window_by_name(TITLE)`
and the main viewport. Work area at every size: `work_pos.y` = 28 (under the menu
bar), `work_size.y` = height − 28. The work area runs to the viewport's bottom, so it
includes the status bar.

| case | frame 1 (appearing, hidden) | frame 2 | frame 3 | settled rect y | above / below in work area |
|---|---|---|---|---|---|
| first open, 1500x900 | 52 tall, body 4 | 109 tall, centred 395–504 | 731 tall, 395–1126 (off screen) | **169–900** | 141 / **0** |
| first open, 1920x1080 | 52, body 4 | 109, 485–594 | 731, 485–1216 | **349–1080** | 321 / **0** |
| first open, 1500x1600 | 52, body 4 | 109 (body already 626), 745–854 | 731, 745–1476 | **745–1476** | 717 / 124 (centre 1110 vs work centre 814) |
| second open, 1920x1080 | 52 (at the old position) | 731, 174–905 | — | 174–905 | 146 / 175 |
| second open, 1280x800 | 52 | 731, 34–765 | — | 34–765 | 6 / 35 |

**When the window is resized while the modal is open:**

| resize | modal before | modal after |
|---|---|---|
| 1500x900 → 1920x1080 | 169–900 | 169–900, not re-centred (180 below) |
| 1920x1080 → 1500x900 | 174–905 | 169–900, `modal_origin` pushed it inside, bottom-pinned |
| 1920x1080 → 1280x800 | 349–1080 | 69–800, bottom-pinned |
| 1500x1600 → 1280x800 | 745–1476 | 69–800, bottom-pinned |
| 1280x800 → 1920x1080 | 34–765 | 34–765, not re-centred |

The modal is 731 px tall and 620 px wide at every size measured. Its height is not
"past 900 px" any more, as round one found. The body is bounded by `dialog_body_room`.

### Cause

**The centring is applied on the frame the modal is 109 px tall, and it grows to 731
the frame after.**

1. Its body is an `auto_resize_y` child. On the first open in a process, the child does
   not exist yet, so on the appearing frame it measures 4 px.
2. The modal's auto-fit reads the body one frame behind: 109 on frame 2, even though the
   body is already 626 that frame.
3. `Cond_.appearing` with pivot 0.5 centres that 109 px window: at 1500x900, around 450.
4. On frame 3 the window grows 622 px downward from that top edge.
5. At 1500x900 and 1920x1080 the window now runs off the bottom. `modal_origin`
   (`launcher._modal`, via `modal_origin`) moves it back up only as far as the work
   area's bottom. The settled placement is therefore **bottom-pinned**: 0 px below and
   141 (or 321) above, over the status bar.
6. Where it still fits (1500x1600), it simply sits 296 px below centre.

On a later open in the same process, the body child keeps its size, so frame 2 is
already 731 and the modal is centred. That is why the placement looks uncalculated: it
depends on whether this is the first open.

**Two smaller offsets:**

- The centre used is the viewport's, not the work area's. With the 28 px menu bar, a
  correctly sized modal sits 14 px high (e.g. 6 above / 35 below at 1280x800).
- The work area includes the status bar, so a bottom-pinned modal covers it.

**The height does not force the low placement.** 731 is less than every work height
measured (772, 872, 1052 and 1572), so a centred 731 px modal fits at all four sizes.
Height would force the modal to the top of the work area only below about 760 px of
window height.

**It is never re-centred when the window size changes.** It is only kept inside the
work area. Shrinking leaves it bottom-pinned; growing leaves it where it was.

### Proposed fix (builder's)

This is all in `launcher._modal`:

1. **Centre on the work area.** Use
   `viewport.work_pos + viewport.work_size * 0.5` in place of `viewport.get_center()`.
2. **Re-apply the centring with `Cond_.always` until the modal's size has settled.**
   That means on every frame from appearing until the window's size is the same as on
   the previous frame, so that frames 2 and 3 above are both centred. A small field on
   `LauncherState` holds the last size seen and whether it has settled. It is reset
   where the modal is opened (`request_open` / `open_popup`). After it settles,
   today's behaviour holds: later growth (an opened session list) goes downward and
   `modal_origin` keeps it inside.
3. **Optional, separate:** have `modal_origin`'s area exclude the status bar's height,
   so a modal that has to be pinned does not cover it.

Two alternatives were rejected:

- Pre-sizing the body child on the appearing frame depends on an ImGui auto-fit
  detail, which would have to be re-measured on each ImGui bump.
- Re-centring on every size change would also move the modal while the operator
  opens the session list, which is a visible jump under the pointer.

A test can drive `_modal` headless for three frames from a fresh context and assert
that the modal's vertical centre is the work area's within 1 px on the frame after
it settles. That test is the one to write first.

## What was not measured

- No real display, DPI scale other than 1, or theme other than dark.
- The launcher was not dragged by the operator before the window was resized.
- WRAP and RAW modes were not shot. They go through the same `_begin_log`, so they
  have the same sides by reading.
- `##tool` children were told apart from stale ones by `active` and `hidden`. ImGui
  keeps a window that was not drawn, and the probe filters those out.
