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

## The operator's sign-off (2026-10-16)

On the variants: "'Stacked' tool block looks great for bash!" On the per-tool proofs
(`shots/tool-block/proofs/dark.png`): "Those look really good." Mid-proof they asked: "WebFetch
stacked tool display should construct a URL". That is built in below. Earlier: "Errors
getting a red rule is fine and works well to my eyes" and "Theme dictates the 'terminal'
background, yess".

So the block is built as the proofs below draw it. Two things are not signed off and are the
builder's to settle and report:
- Terminal colours on the five themes the proofs did not render (ghost, cde, win311, kitty,
  turbo). The proofs' rule puts cde and win311 on the light branch, where text was calculated
  at about 2-3:1. That is not acceptable as built: text in a block reaches 4.5:1 on every
  theme.
- The "illustrative" result texts, which were not found word for word in the CLI.

## Per-tool proofs

The operator, after the variants: "Show me alternate tools and how they'll render. My thought
is bash has a certain look like others are distinct in other ways. Stacked for them but we
need to proof them." Also "Errors getting a red rule is fine and works well to my eyes" and
"Theme dictates the 'terminal' background, yess".

**What to look at:** `shots/tool-block/proofs/<theme>.png` holds every tool in its done
state, plus a second case (a long output, or the tool's other input shape).
`shots/tool-block/proofs/<theme>-states.png` holds the same tools in error, running and no
result. There are both sheets for each of dark, light, xterm and high_contrast.

**Regenerate:** `scripts/tool_block_probe.py --proofs` renders every theme, each on its own
Xvfb, about 9 s each. `--proofs --theme dark` renders one. The sheets draw under
preview-fixups' `style_probe.final_for(<theme>)`, imported from `scripts/style_probe.py`, so
the dock wells, the buttons and small button D are the final ones.

### The frame every tool shares (the stacked variant)

- **One well per call:** rounding 5, padding 10x6, item spacing 8x2.
- **Caption line:** proportional type at 13 px, which is 0.8 of the UI size rounded to a whole
  pixel, because ImGui 1.92 bakes whole pixels (preview-fixups). It reads
  `kind · details · state`. The kind word is in `accent`, except Bash, which has none and opens
  on its description. The details are in `text_dim`. The state badge is `dim` for done,
  `accent` for running, `warn` for "no result · turn ended", and `danger` for an error (Bash
  shows "exit N" there).
- **Head:** what the call asked.
- **Body:** what came back, or the call's own content for a write.
- **The expand affordance:** `imgui.small_button` (button D) labelled "N more …". It comes
  before the body when the tail is kept, and after it otherwise.
- **Error:** a 3 px `danger` rule down the left edge, the operator's pick. The CLI's
  `<tool_use_error>…</tool_use_error>` wrapper is stripped, as the CLI strips it, and the text
  is drawn in `danger`.
- **Running and no result** draw the head only. A Write or Edit still shows what it asked,
  because the content is in the input.

### The theme's well

The well is derived by `terminal_for(palette)`, which is the builder's rule. There is no fixed
colour on any theme.

| theme | rule | well | on the pane |
|---|---|---|---|
| dark (any theme with bg luminance under 0.05, not black) | `mix(bg, black, 0.60)` | #08090b | 1.10:1 |
| light, xterm (luminance 0.05 and up) | `mix(bg, text, 0.06)`, the fix-ups table's | #e7e8ec, #dcdad0 | 1.12:1 |
| high_contrast (black pane) | `mix(bg, text, 0.10)` plus a 1 px `border` outline | #1a1a1a | 1.21:1 |

**Why dark goes deeper rather than lighter:** the terminal look is a ground darker than its
surroundings. Dark's pick was a fixed #0b0b0c; `mix(bg, black, 0.6)` lands next to it and
carries the theme's hue.

**Why high_contrast gets the outline:** its pane is #000, so nothing is deeper. Fix-ups
measured the old mock there at 1.07:1, and high_contrast keeps frames.

**Inks** are the palette's `text` and `text_dim` on every theme, plus `diff_add`/`diff_remove`/
`diff_context`, `accent`, `ok`, `warn` and `danger`. Measured on each well (WCAG), with the
palette as the tree held it at render time:

| ink on the well | dark | light | xterm | high_contrast |
|---|---|---|---|---|
| text | 12.66 | 12.85 | 12.29 | 17.40 |
| dim (`text_dim`) | 6.10 | 4.72 | 5.48 | 9.57 |
| line numbers (`dim` 35% toward the well) | 3.13 | 2.49 | 2.74 | 4.78 |
| diff add / ok | 9.47 | 5.14 | 4.83 | 13.23 |
| diff remove / danger | 5.09 | 5.44 | **4.17** | 6.27 |
| diff context | 5.26 | 4.72 | 5.48 | 9.57 |
| accent | 9.62 | 4.59 | 8.34 | 11.31 |
| warn | 10.38 | 4.84 | **3.51** | 12.16 |

These fall under 4.5 and are left for the operator:
- xterm's `diff_remove`/`danger` (4.17) and `warn` (3.51) on its well. Those inks are the
  palette's, so the fix would be a palette change.
- The line numbers on every theme. They are deliberately faint, as an editor's gutter is. That
  is the builder's choice, and it can be undone by drawing them in `dim`.

### Each tool

Field names are from the Agent SDK's TypeScript reference, "Tool Input Types"
(https://code.claude.com/docs/en/agent-sdk/typescript, read 2026-10-15). They were checked
against the CLI the SDK bundles: `.venv/.../claude_agent_sdk/_bundled/claude`, version
2.1.301 (`_cli_version.py`), whose minified source holds the schemas and result texts. The
result texts below are that CLI's `mapToolResultToToolResultBlockParam` output. Where a case's
text was not found verbatim, the sheet says so under the block.

**Bash** is the terminal, and the only tool with no kind word and no proportional type below
the caption.
- **Reads:** `command`, `description`, `timeout` (ms; shown as min or s), `run_in_background`
  ("background"), and `dangerouslyDisableSandbox` ("unsandboxed", in `warn`).
- **Order:** caption (description · limit · flags · state), then `$ command`, up to 3 lines.
  Then the affordance "N earlier lines", then the **last 6** output lines in `dim`.
- **Truncation:** tail, because the line that says whether a build or test passed is last
  (builder-22's finding).
- **Error:** the CLI's "Exit code N" first line becomes the badge "exit N" and is dropped from
  the output.
- **Over the 1 MiB cap:** the dropped bytes are added to the affordance's label.

**Read** is a numbered excerpt.
- **Reads:** `file_path`, `offset`, `limit` (caption "lines a–b", "from line a", "first n
  lines" or "whole file"), and `pages`.
- **Order:** caption (read · range · N lines · state), path in mono, then the **first 6**
  lines with their numbers from the result's `cat -n` form, in a faint gutter. Then "N more
  lines".
- **Error:** "File does not exist." is in the CLI.

**Edit** is a -/+ diff in the pane's own `diff_add`/`diff_remove`/`diff_context` (`theme.py`'s
approval-gate roles).
- **Reads:** `file_path`, `old_string`, `new_string`, `replace_all` ("every occurrence").
- **Order:** caption (edit · `+a` in add · `−r` in remove · state), path, then the
  `difflib.unified_diff` of old against new with **1 line of context**. The **first 8** diff
  lines are shown, then "N more diff lines".
- **The result is not drawn when done:** "The file X has been updated successfully." adds
  nothing to the diff. "Here's the result of running cat -n", as builder-22's case had it, is
  not this CLI's text.
- **Error:** the diff stays and the CLI's message follows in `danger`, "String to replace not
  found in file.\nString: …" (verbatim).

**MultiEdit is dropped.** CLI 2.1.301 defines no tool by that name; it has constants for Edit,
Write, NotebookEdit, Glob, Grep, WebFetch, WebSearch, TodoWrite and Agent. "MultiEdit"
survives only in its permission-rule compatibility text, and the SDK reference lists no
MultiEdit input type.

**Write** is a preview marked as new.
- **Reads:** `file_path`, `content`.
- **Order:** caption (write · "new file" in add when the result is "File created successfully
  at: …", or "overwrote" in `warn` when it is "The file … has been updated successfully." ·
  size · lines · state). Then the path, then the **first 5** lines of `content`, each behind a
  `+` in `diff_add`, then "N more lines in the file".
- **Running and no result:** a running Write shows the preview without new/overwrote, since
  only the result says which.
- **Error:** "File has not been read yet. Read it first before writing to it." (verbatim).

**Grep** and **Glob** are a pattern and a compact list with a count.
- **Grep reads:** `pattern` (shown `/pattern/`), `path` ("in …"), `glob`, `type`,
  `output_mode` (shown when not the default `files_with_matches`), `-i`/`-n`/`-o`/`multiline`,
  `-A`/`-B`/`-C`/`context`, and `head_limit`.
- **Glob reads:** `pattern` and `path`.
- **Order:** caption (kind · scope · options · count in `text` · state), then the pattern in
  mono, then the **first 6** entries and "N more files/matches".
- **Grep in content mode** splits each `path:line:text` into the location in `accent` and the
  text in `dim`.
- **Counts:** the count is the result's own lines, not its "Found N files" header. The CLI's
  "No files found" becomes "no matches" in `warn`.
- **Errors:** "Path does not exist: …" (Grep; the CLI appends a cwd note and a "Did you mean"
  that the case cuts) and "Directory does not exist: …" (Glob). Both strings are in the CLI.

**WebFetch** is a constructed URL, then the prompt in prose. The operator, after the dark
sheet: "WebFetch stacked tool display should construct a URL". Lead's reading, applied here:
the whole URL, drawn as a link.
- **The URL** (`url_row`):
  - It is built from `url` with `urlsplit`. The `scheme://` is in `dim`, the host in the link
    ink (`accent`), and the path, query and fragment in `dim`.
  - The whole row is underlined in the link ink at 60%.
  - `imgui.set_item_tooltip` holds the full URL, so hovering shows it. The tooltip is spied
    for drawability in the tests; no image shows the hover.
  - A path and query longer than 56 characters lose their middle: the first third is kept,
    then "…", then the end. The host and the end of the query stay visible. A string with no
    scheme or host is drawn whole, in the link ink.
- **Reads:** `url` and `prompt` (wrapped prose in `text`). `offset` is not shown.
- **Body:** the answer as wrapped prose in `dim`, the **first 3** paragraphs, then "N more
  paragraphs".
- **The cases:**
  - Done is a short URL.
  - Second is a 124-character URL with a query string, whose result is a cross-host
    redirect.
- **A redirect, and what the result can show.** From the CLI 2.1.301 source:
  - **Same host:** the CLI follows the redirect, and the text result names no final URL.
    There is nothing to show for it.
  - **Cross host:** the CLI does not follow it. The result begins "REDIRECT DETECTED: …" and
    carries "Redirect URL (from the server's Location header — server-supplied, not
    verified): <target>". That target is drawn as a second constructed URL under "redirects
    to (server-supplied, unverified)" in `warn`, and the badge reads "redirect · not
    fetched".
  - **A target the CLI withholds** (not http(s), or over its length limit) is not parsed, and
    the block falls back to the result's prose.
- **Error:** "Request failed with status code 404" is the HTTP client's text in the CLI.

**WebSearch** is a quoted query, then titles over constructed URLs.
- **Reads:** `query` and `allowed_domains`/`blocked_domains` ("only …"/"not …").
- **Body:** parsed from the result's `Links: [{"title","url"}…]` line. The result's form,
  `Web search results for query: "…"\n\nLinks: [json]…REMINDER…`, is the CLI's.
- **Each result** is two rows: the title in prose, then its URL drawn as WebFetch's is (host
  emphasised, middle elided, underlined, tooltip). The **first 4** are shown, then "N more
  results", with the count in the caption.
- **The other result form:** when the CLI sends `search_result` blocks instead (its citations
  path), the transcript holds a repr, and this parse finds no links. That form was not drawn.
- **Error:** illustrative. The `InputValidationError:` prefix and the wrapper are the CLI's;
  the message is not quoted from it.

**Agent** (and `Task`, its accepted old name) is a description, a collapsed prompt and a
state.
- **Reads:** `description` (prose, `text`), `prompt` (one line, small, `dim`, with "N lines of
  prompt"), `subagent_type` (default "general-purpose"), `model`, `effort`, `isolation`,
  `run_in_background`.
- **Result:** a list of text blocks, held as a repr; the probe reads it back with
  `ast.literal_eval`. The trailing "agentId: … <usage>subagent_tokens: N tool_uses: N
  duration_ms: N</usage>" block becomes "N tool uses · N s" in the caption and is not drawn
  as text.
- **Report:** the **first 3** lines.
- **A background launch:** "Async agent launched successfully…" becomes the badge "launched
  · runs on", with no body.
- **Error:** illustrative.

**TodoWrite** is a checklist.
- **Reads:** `todos[]`, each `content`, `status` (`pending` | `in_progress` | `completed`) and
  `activeForm`.
- **Rows:** `[x]` in `ok` with `content` in `dim` for completed. `[>]` in `accent` with
  `activeForm` in `text` for in progress. `[ ]` with `content` for pending.
- **Caption:** "N done · N doing · N to do". **Up to 8** items are shown.
- **The result is never drawn:** it is the CLI's fixed "Todos have been modified
  successfully…".
- **Error:** the message "Content cannot be empty" is the CLI's schema text.

**NotebookEdit**:
- **Reads:** `notebook_path`, `cell_id`, `new_source` (mono, `diff_add`, **first 6** lines),
  `cell_type`, and `edit_mode` ("replace/insert/delete cell <id>"; delete shows no source).
- **Error:** "Cell type is required when using edit_mode=insert." (verbatim).

**MCP** (pptmstr's own bus) is server · tool, then the arguments.
- **Name:** split from `mcp__<server>__<tool>`. The caption is "mcp · <server>"; the tool name
  heads the block in prose.
- **Arguments:** a `key: value` list (key `dim`, value `text`, one line, 72 characters).
- **Result:** the list-of-blocks text, **first 4** lines.
- **Error:** "MCP error -32000: Connection closed" is in the CLI.
- **The post_concern case's reply** ("sent to lead") is illustrative.

**Unknown tool**, the fallback:
- **Caption:** the name in `warn` and "no block of its own".
- **Body:** the arguments as `key: value`, then the **first 4** output lines.
- **Cases:** drawn with `Skill` (`skill`, plus `args` from this session's own Skill schema).
  The error case is a made-up name, with the CLI's verbatim "Error: No such tool available:
  Frobnicate".

### What makes each distinct (builder's summary)

- **Bash:** all code face, `$` prompt, no kind word, tail cut.
- **Read:** numbered gutter.
- **Edit:** red and green rows.
- **Write:** `+` rows under "new file" or "overwrote".
- **Grep and Glob:** a pattern line over a path list with a count; content mode puts
  locations in accent.
- **WebFetch:** an underlined URL with its host in accent, then a prose question and answer.
- **WebSearch:** a quoted query and a list of titles over URLs.
- **Agent:** a prose description and a one-line prompt, with run stats in the caption.
- **TodoWrite:** checkboxes, no head.
- **NotebookEdit:** a cell verb and green source.
- **MCP:** a key: value table.
- **Unknown:** a `warn` name.

### Run, and not run

**Run:**
- `--proofs` for the four themes: exit 0, eight images.
- `--all`: exit 0. It re-rendered the six variant sheets.
- `pytest tests/test_tool_block_probe.py tests/test_typeset_gate.py`: 89 passed. 17 of these
  are new: every cell is a transcript entry, every input is inside the SDK's field set with
  its required fields, states, drawability, per-tool content, the URL's construction and the redirect target, the well's derivation, and all 8
  sheets drawn in a bare context with text calls spied.
- ruff, mypy and black on both files.
- Mutations, both killed:
  - output rows without `drawable.text`;
  - a light theme given the dark derivation, which only the well's 1.05–1.3:1 band caught.

**Not run:**
- A live CLI session's results.
  - The texts are taken from the bundled CLI's source or marked illustrative.
  - The Read `cat -n` form is from this session's own Read tool, not from the bundle.
- Hover and press of the affordance, and the URL's tooltip on hover.
- The other five themes.
- Blocks at the real session pane's width.
- The `search_result`-blocks form of WebSearch.
