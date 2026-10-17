# Tool blocks and selection: can the library carry per-row ink?

**Dated:** 2026-10-17 · **Status:** probe at c866b5c, imscribe 0.2.0, imtypeset
0.4.3.192900 · **Board task:** `tool-block-select`

**This is not a planning record.** It answers step 1 of the task: whether the typeset
seam can set a tool block's command and output as a selectable view that keeps the
block's inks (the error red, the dim lines, the stacked caption inks).

## Answer: yes, per character range

`imscribe.TypesetView.set_colours(block, spans)` takes half-open `(start, end, ImU32)`
ranges of one block's text and draws them as marks in the ink's place, with nothing
laid out again. A range can be a whole line or a piece of one, so the library offers
**per-span** ink. That covers per-row ink, and it also covers a row of mixed inks: an
Edit diff's sign and its text, or Read's dim line numbers before text in the text ink.

The seam already uses this. `views._ink` lays a literal part's colour and the syntax
lexer's tokens over a code block through `set_colours`. What it does not have is a way
for a caller to hand in its own spans.

## The proof

The proof is a headless frame, the `seam` fixture of `tests/conftest.py`, drawing one
`gate.literal_view` that holds one `MarkdownCode` in `themes.literal_style(LITERAL)`.
The block holds three lines:

    $ make test
    FAILED tests/x.py::boom
    3 passed, 1 failed

It gets four ranges: `$ ` accent, the rest of line 0 terminal dim, line 1 danger,
line 2 terminal text. The probe then did two things:

- **Inks.** It took the vertex colours drawn between each line's top and bottom
  (`set_lines`). Row 0 had exactly {terminal dim, accent}, row 1 exactly {danger}, and
  row 2 exactly {terminal text}. No colour came from the library's code style, and no
  ground was painted under the rows.
- **A drag.** It sent ImGui's own input: mouse to column 7 of line 1, press, a frame,
  mouse to column 14, release. The view's selection was then `((0, 19), (0, 26))` and
  `selected_text()` was `'tests/x'`, exactly the characters dragged over. A
  programmatic `select(((0, 36), (0, 44)))` read back as `'3 passed'`.

The probe lives on as tests in `tests/test_tool_block.py`. They drive the blocks
themselves, not a hand-made view.

## What the build found

Ink was the easy part. The hard part was making the view stand exactly where ImGui's
lines stood. `typeset.views.rows` does that, and here is what it took:

- **One code block per row.** A single block holding every row would need the row
  pitch (16 px + 2 px item spacing) as its leading. The library puts a baseline three
  quarters of the way down a line. At 18 px that is a half pixel, and rows came out a
  pixel off at some screen heights (measured at leads 0 to 333). Each row is therefore
  its own block, with `space_below` making up the pitch. That is ImGui's line plus
  the item spacing, less what `literal_line` adds at 4k+2 heights.
- **An empty row is handed over as one space.** The library sets an empty block as
  nothing: no height and no place in a copy. A blank output line would collapse, and a
  copy across it would lose the line break. The view's `copy_as` and
  `ViewCache.selected` take the space back out, so a copy matches the drawn text.
- **The library's glyphs sit higher than ImGui's.** ImGui puts the baseline the code
  face's ascent below the line's top. The library puts it at 3/4 of its line. The
  difference is 1 px at 12, 13, 14, 17 and 18 px and 2 px at every other height from 12
  to 24. The view is drawn that much lower (`views._drop`).
- **The window's extent is ImGui's.** Two things make the view's rectangle wrong for
  sizing the window. It is lower and taller than ImGui's lines would be. And out of
  sight it rests at the measure it was last drawn at, which is wider than the window
  once the log's scrollbar takes room. Left alone, the first made a block whose last row
  is a view 2 px taller. The second gave an out-of-sight block a horizontal scrollbar,
  16 px taller; that turned up in the app, in the "report" session's tree block. So
  `views._stand` puts the window's furthest and ideal extents back. A zero-sized item at
  the foot of ImGui's lines then does ImGui's arithmetic. This writes
  `ImGuiWindow.DC`, as `widgets.small_button` already does.

Tests check each of these against ImGui's own drawing of the same block:

- every glyph quad's position and ink;
- the block's rectangle;
- the cursor it leaves;
- the height it keeps while out of sight.

Shots of the four root sessions of `--fake`, before and after, are in
`ui-rework/shots/tool-block-select/`. They differ only in the clock, the spinners and
the streaming session's newer text. `after-selected.png` shows a selection over a
block's row.

## What still holds

These are constraints the library does not lift. The build keeps them.

- **Only rows the library would set as written.** The text has to pass
  `views.literal_unfit`: no control or invisible character, no trailing newline, no
  line longer than the measured reach, and no combining mark the code face lacks.
  A row that fails stays ImGui's, as it is today.
- **Mono rows only.** A row with a prose span or a `tip` span (a tooltip link) is not
  code. It stays an ImGui row, so the selectable runs are consecutive mono rows.
- **The gate.** With typeset off, or the library stopped, a block is drawn exactly as
  it is now.
- **One selection per view.** Each run of rows is its own view, so a drag stops at the
  run's edges, as it already does between the log's views. The head and the body are
  separate runs.
- **TYPESET only, and only at the code face's own size.** The pane hands a block the
  library only when Tool output's size (`Role.LITERAL`) equals the size its rows are
  pushed at. That is the default. An operator who sets a step on that role gets
  ImGui's rows, which look as before and are not selectable.
- **Two panes on one node in a frame.** The second draw of a block in a frame is ImGui's
  rows. A view draws once a frame.
- **Ctrl+C out of sight.** A view's keys are read in its own draw. A selection in a block
  whose generation has scrolled out of sight is still copied by the log's menu, but not
  by Ctrl+C until the block is drawn again.
