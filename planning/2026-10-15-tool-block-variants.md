# Tool block variants: four ways to draw a call, from real entries

**Dated:** 2026-10-15 · **Status:** a probe for the operator to pick from. Nothing the app
draws has changed · **Answers:** the operator's "the tool block may need some variants as
well", against their Session Pane notes in `2026-10-15-ui-rework.md`

## What to look at

All images are under `shots/tool-block/`. They are gitignored and rendered locally at 1500x1400, dark theme.

| image | what it is |
|---|---|
| `stacked.png` | variant **stacked**, 10 cases |
| `one-line.png` | variant **one-line**, the same 10 cases |
| `header-on-pane.png` | variant **header-on-pane**, the same 10 cases |
| `gutter.png` | variant **gutter**, the same 10 cases |
| `viewer-inpane.png` | output viewer **mock 1**: the capped log expanded in place |
| `viewer-window.png` | output viewer **mock 2**: a separate top-level window, drawn as a frame |

To regenerate, run `.venv/bin/python scripts/tool_block_probe.py --all`. It runs on its own Xvfb
through `scripts/xvfb_shot.sh`. `--describe` prints the variant table from the code.

Every sheet states its own values: the variant's fields, the picked style in force, and the
fonts that drew it. Under each block is its case name, its measured height, and the entry's
facts: `tool_use_id`, `name`, input keys, output lines, bytes kept and dropped, and `is_error`.

## Where the blocks come from

`build_cases` writes ten calls into one `Transcript` through `append_tool_call(id, name, input)`
and `append_tool_result(id, name, content, is_error=...)`. Those are the driver translator's
calls, so `content` is what the SDK gives: a string, or a list of blocks for the MCP case. Each
block is built from the `ToolEntry` that `Transcript.tool_entry(id)` returns. The test also
finds each entry through `tool_entries(call_start, call_end)`, the lookup a renderer makes.

| case | entry |
|---|---|
| bash-short | `Bash` with `description` and `timeout` (120000); 3 lines of output, one of which holds a lone surrogate (`caf\udc80.txt`) |
| bash-long | `Bash`, `pytest -v` output: 47 lines |
| bash-capped | `Bash`, a 1.3 MB log: the entry keeps 1,048,576 B and records 268,314 B dropped |
| bash-error | `Bash`, `is_error=True`, the content starts `Exit code 1` |
| edit | `Edit` with `old_string`/`new_string` (two lines added) |
| read | `Read` with `offset` 1 and `limit` 12; one line holds a NUL |
| webfetch | `WebFetch` with `url` and `prompt` |
| mcp | `mcp__pptmstr__read_board`, result a list of text blocks, so `output` is its `repr` |
| running | `Bash`, no result, `live=True` |
| no-result | `Bash`, no result, `live=False` (the turn ended) |

**Verbatim in, drawable out.** `build` passes every string it takes from an entry through
`drawable.text` (`_d`). Every text the drawing code hands to ImGui goes through `_say`, which
raises on a NUL or a lone surrogate. The test wraps ImGui's text calls and draws all six sheets
in a bare context, so a raw verbatim field fails there.

**`result is None` is not "running".** The entry cannot tell a live call from one whose turn was
interrupted, or one whose result landed in a sub-agent's buffer (rv-tool-entries, finding 2). The
probe takes `live` from the case. A real renderer has to read it from the node's state.

## What every variant shares

These follow the operator's notes and are the same in all four:

- **No `Bash ->` prefix.** A Bash block's caption is its `description`, then `timeout N min`,
  then `background` when set. Its command line is the `command`, set in monospace. A command over
  three lines shows three and says how many more.
- **Per-tool intros.**
  - Edit: caption `edit · +A -R`, the path as the command line, and a mini diff (`difflib`,
    no context, `-` in `diff_remove`, `+` in `diff_add`) cut at the variant's line count.
    The CLI's "has been updated" result is collapsed to one line, because the diff already says
    what was done.
  - Read: `read · lines a–b` (or `from line a` / `whole file`), with the path as the command
    line.
  - WebFetch: `fetch · <domain>`, the URL as the command line, and the prompt as a wrapped
    proportional line under it.
  - MCP: `mcp · <server>`, with the tool name and its JSON arguments as the command line.
- **State** is a coloured word after the caption: `running` (accent), `done` (text_dim),
  `error · exit N` (danger; N is read from a leading `Exit code N` line, which is then not
  repeated in the output), and `no result · turn ended` (warn).
- **Terminal text:** output in grey monospace (0.55), the command in a lighter grey (0.78),
  tabs expanded to 8, no wrapping. A drawn line is cut at 400 characters.
- **Truncation** always gives a count: `N more lines`, or `N earlier lines` for the tail
  variants. When the entry dropped bytes at the cap, it adds `· X B past the 1 MiB cap`.
- **Fonts:** the caption is in the typeset library's PROSE face at 0.8x the UI size (12.8 px).
  Monospace is the library's CODE face (Inconsolata). Both are reached through
  `pptmstr.ui.typeset.font`, never `imscribe` directly. Without the library, both fall back to
  the UI font.
- **The picked style** is applied in the probe's process over `theme.apply_style`: soft's values,
  `frame_padding 9x6`, `frame_rounding 5`, `frame_border_size 0`, `popup_border_size 0`,
  `popup_rounding 4`. The sheet lists every value. These are this script's own copy (see "Not
  run").

## The variants

Heights are measured: `get_item_rect_max().y` of the block's group, minus its top, on the last
frame drawn. At the UI font 16 px with the probe's item spacing of 2, a monospace line costs
18 px.

| case | stacked | one-line | header-on-pane | gutter |
|---|---:|---:|---:|---:|
| bash-short (3 lines) | 97 | 78 | 97 | 70 |
| bash-long / capped / error / read | 181 | 114 | 205 (error 169) | 88 |
| edit | 127 | 96 | 115 | 88 |
| webfetch | 95 | 76 | 95 | 68 |
| mcp | 61 | 42 | 61 | 34 |
| running / no result | 43 | 24 | 31 | 16 |
| **all 10** | **1190** | **796** | **1214** | **644** |

### `stacked`

Round one's mock-up made real.

- **Values:** header stacked (caption over `$ command`); well `#0b0b0c`, padding 10x6,
  rounding 5; output inside the well; first 6 lines, then `imgui.button` at the pick's 9x6;
  error is a 3 px `danger` rule down the left edge.
- **Reads:** the closest to "a terminal on near-black". The caption and command separate
  cleanly, and the picked button is a real target.
- **Costs:** 181 px for any block that truncates. Ten calls of that kind fill more than a
  1000 px pane. The error rule is easy to see, but `exit 1` is in 12.8 px type.

### `one-line`

- **Values:** header one-line (`command` then the caption after it); well `#0b0b0c`, padding
  8x4, rounding 3; output inside; **tail cut**: `N earlier lines` as an `imgui.text_link`, then
  the last 4 lines; error tints the well 14 % toward `danger`.
- **Reads:** the tail cut is what makes pytest and build output readable. The verdict line
  (`1 failed, 1 passed`, `42 passed`) is the one shown. The tint makes an error the most
  visible of all four from a distance.
- **Costs:** 114 px per truncating block. A long command pushes the caption off the right edge,
  where it is clipped and not wrapped (`.venv/bin/python -m pytest -v ...` nearly does at half
  width). A link is a smaller target than a button.

### `header-on-pane`

- **Values:** header stacked on the pane's fill; only the output sits in the `#0b0b0c` well
  (padding 10x6, rounding 5); first 8 lines, then `imgui.small_button`; error is the 3 px rule
  on the well.
- **Reads:** the clearest split between "what was asked" and "what came back", and the most
  output visible before expanding.
- **Costs:** the tallest, at 205 px. `small_button` is the crowded micro button the operator
  already rejected. With nothing in the well, the header floats loose on the pane (see the
  running and no-result cases).

### `gutter`

- **Values:** header one-line; no well, everything on the pane's own fill; output indented
  12 px behind a 2 px rule in `mix(bg, text_dim, 0.5)`; **tail cut**, `N earlier lines` link,
  then the last 3 lines; error turns the rule `danger`.
- **Reads:** the cheapest, at 88 px truncated and 16 px for a bare call, about the cost of
  today's two lines. It reads like a log, not like a terminal.
- **Costs:** it drops the operator's "pure or near black". The error is a 2 px red line plus a
  12.8 px word, which is the weakest error signal of the four.

## How errors read, ranked

1. **one-line**'s tint is visible at a glance across a busy pane.
2. **stacked** and **header-on-pane** use a 3 px rule plus a red word.
3. **gutter**'s 2 px rule is easy to miss.

None of them turns the output text red. Round one rejected that, because it makes the output
harder to read.

## The output viewer (mocks)

- **In-pane** (`viewer-inpane.png`): the block's affordance swaps the cut output for a
  14-line scrolling well. It has line numbers, a `find` field, `wrap`, `copy all`, `save…` and
  `collapse`, and the blocks around it stay in place. When the entry dropped bytes, the viewer
  says so in `warn`: copy and save give only the kept 1 MiB. The cost is that the pane's
  scrolling now contains a second scroller. The wheel over the well scrolls the well, not the
  pane, which is the usual nested-scroll trap.
- **Separate window** (`viewer-window.png`): a **mock frame**. No OS window is opened. It is one
  window per expanded output, titled by the command, with the same toolbar and 36 rows. The
  session pane does not move. The cost is real work. It needs ImGui multi-viewport (hello_imgui
  supports it, but this app has never turned it on) or a second native window, plus a policy
  for when those windows close (a session ends, the app quits).

## Recommendation (builder's, not the operator's)

**`stacked`'s header and well, with `one-line`'s tail cut and tint**, at 4 shown lines:

- the stacked header follows the operator's note to the letter (smaller variable-width
  caption above a monospace command);
- the tail shows the verdict line of tests and builds;
- the tint makes errors findable;
- 4 lines bring a truncating block to about 145 px. That is an estimate from the 18 px
  monospace pitch, not measured.

The expand affordance should be the picked `imgui.button`, not `small_button`, until the
`small_button` wrapper question (style-preview's sheet) is settled.

For the viewer, **in-pane first**: it is a contained change. The separate window should wait
until something other than output needs multi-viewport.

The head-vs-tail choice should probably be per tool, not per variant. A Read wants its head and a
Bash its tail. The probe does not show a per-tool cut.

## Findings for whoever builds the renderer

- **MCP results as `repr` read badly.** `[{'type': 'text', 'text': 'board: 4 tasks\n- ...`
  draws as one line with literal `\n`. Every MCP call (pptmstr's own bus tools included) shows
  this. The renderer needs the block structure kept (tool-entries' open decision, round-one
  item 7).
- **`Exit code N` is parsed from the output text.** That is the CLI's present format, read from
  this repo's knowledge of it and not checked against a live CLI here. If the format changes,
  the badge says only `error` and the line stays in the output.
- **A line held in an entry can be the whole 1 MiB** (minified files, an MCP `repr`). The probe
  cuts a drawn line at 400 characters. A renderer must too, or measure 1 MiB per frame.
- **Splitting the capped output into lines** costs one `splitlines` over 1 MiB per build. The
  probe rebuilds every frame for simplicity. A renderer should cache per `(tool_use_id, result
  start)`.

## What was run and what was not

Run:

- `scripts/tool_block_probe.py --all` exited 0 and wrote all six images; the heights above
  are its printout.
- `pytest tests/test_tool_block_probe.py tests/test_typeset_gate.py`: 71 passed.
- black, ruff and mypy are clean on the script and its test.
- Two mutations were each killed by the tests:
  - output lines built without `_d`;
  - `_say` disabled, with the viewer drawing raw lines.

Not run:

- Light and high-contrast themes. The near-black well is fixed and does not follow the theme,
  so in light the blocks will be black slabs on a light pane. Nobody has looked at that.
- The blocks have not been drawn inside the real SESSION pane (with typeset prose around them,
  at the pane's real width). They are drawn on a sheet over the app.
- The style-preview task's `picked` variant did not exist in `scripts/style_probe.py` when this
  ran. The pick's values are copied into `PICKED_SCALARS`/`PICKED_COLORS` here, and should be
  replaced by an import once it lands.
- Hover and click states of the affordances. A link, a button and a small_button are shown at
  rest only.
- A real CLI session's tool results. The cases are hand-written in the shapes the driver
  receives.
