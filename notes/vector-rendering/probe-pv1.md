# Probe PV1: does the CLI hand an MCP image result back to the model?

Board task `preview-pv1`, 2026-10-21. It gates the `preview_figure` tool settled in
[preview-options.md](preview-options.md) ("Decided"): every option there is moot if
an image returned by a pptmstr bus tool never reaches the model.

Nothing in `pptmstr/` was written. No fix is declared.

**Status: PV1 ANSWERED: yes, the model sees the image** [op-run,
`cmd-20261021T204907Z-3eddeacc39e3`, 2026-10-21, results in
`/tmp/probe-pv1-run3/results.json`, recorded by the lead]. Run 3 below supersedes the
"pending" findings further down; Run 2 was inconclusive because the probe's gate
denied `ToolSearch` (see "Run 2").

## How it is run

Script: `scripts/probe_pv1_image_tool.py`. The tool is built the way
`bus.build_server` builds pptmstr's: an `@tool` handler in
`create_sdk_mcp_server("pptmstr", "1.0.0", ...)`, passed as `mcp_servers`, with
pptmstr's session shape (`permission_mode="dontAsk"`, `strict_mcp_config=True`, a
`PreToolUse` gate allowing only `mcp__pptmstr__show_figure` and `ToolSearch`).
Model `haiku`, `max_turns=6`, cwd the output directory so no project `CLAUDE.md` inflates the counts.

The image is a 64x64 RGB PNG built with the stdlib (212 bytes, 284 base64 chars):
red background (`#dc1414`), white digit 7. Its text sibling block says only
"Figure attached.", naming neither colour nor digit.

Two live scenarios:

- `control`: the tool returns text only ("No figure is attached to this result.").
- `image`: the tool returns `[text, image]`.

The prompt asks for one line, `COLOUR=<..> DIGIT=<..>`, or `COLOUR=none DIGIT=none`
if no image arrived. The control fixes the token baseline and shows the model does
not guess "red 7" without the image.

- **Run 1 (sandbox, offline):**
  `.venv/bin/python scripts/probe_pv1_image_tool.py --out "$TMPDIR/probe-pv1"`.
  Ran. Builds the PNG, calls the handler in-process, and puts its result through
  the SDK's own `_convert_tool_content`.
- **Run 2 (operator, live, inconclusive):** `--live --out /tmp/probe-pv1`,
  `cmd-20261021T204748Z-b5e2d0c3c473`. The gate then denied `ToolSearch`; see "Run 2".
- **Run 3 (operator, live, the answer):**
  `timeout 300 .venv/bin/python scripts/probe_pv1_image_tool.py --live --out /tmp/probe-pv1-run3`,
  `cmd-20261021T204907Z-3eddeacc39e3`, exit 0. See "Run 3".

claude-agent-sdk 0.2.168. The CLI version is recorded from the live `init` frame.

## Pre-live reasoning (sections 1, 2 and 4 are answered by Run 3)

### 1. Does the model receive the image? (answered: yes, Run 3)

What the offline run shows: the SDK side does not drop it.
`claude_agent_sdk._convert_tool_content` passes `{"type": "image", "data",
"mimeType"}` through unchanged (text and image pass, binary resources and unknown
types are dropped with a warning). The handler's image arrives at the SDK-to-CLI
boundary byte-identical (`is_png_b64=True`). Whether the CLI forwards it to the API
as an image block is what Run 2 measures. The `image` scenario must answer red and 7
and the `control` must answer none.

### 2. The `ToolResultBlock.content` shape the driver gets (answered: Run 3)

The SDK types it `str | list[dict[str, Any]] | None` and copies the CLI's
`tool_result.content` verbatim (`message_parser`, `case "tool_result"`). Whether the
CLI returns the image block in it, and in which spelling (MCP's `mimeType`/`data`,
or the API's `source: {type: base64, media_type, data}`), is not knowable offline.
Run 2 records it with base64 elided, along with `UserMessage.tool_use_result`.

### 3. How the transcript renders it today: base64 text, if the CLI echoes it (read + run)

`Driver._user` passes `block.content` to `Transcript.append_tool_result`, which
calls `cap_output(content)`. A non-str is `repr()`'d and capped at `OUTPUT_CAP`
(1 MiB). So a list holding an image block becomes Python-repr text with the whole
base64 string in it:

- `ToolResult.output` (the stored entry) keeps it in full up to 1 MiB. For the
  probe PNG that is 386 chars, all of the base64 included (Run 1, computed on the
  SDK-converted blocks as a stand-in for the CLI's reply).
- The visible transcript line is `tool_result_line`, which `_compact_result` cuts
  at 300 chars. So the pane shows `mcp__pptmstr__show_figure -> [{'type': 'text',
  ... 'data': 'iVBORw0KGgo...` and then `...`.

A realistic preview, say a 40 KB PNG, puts about 54 KB of base64 into each
`ToolEntry` and into anything that reads `ToolResult.output`. The 300-char line hides
this. The stored entry does not. The preview tool will want `_user` (or
`cap_output`) to replace an image block with a placeholder such as
`<image/png, N bytes>`. That is not declared here: Run 2 names the shape the
replacement has to match.

### 4. Token usage before and after (answered: Run 3)

Run 2 records per-`AssistantMessage` usage (`input_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`) and the
session's `ResultMessage.usage`/`model_usage` for both scenarios. The cost of the
image is the second message's input in `image` minus the same in `control`. API
image pricing is roughly width×height/750 tokens, so a 64x64 image should cost
tens of tokens. That is a recalled figure, unverified here.

## Run 2 (operator, first live attempt): inconclusive, probe defect

`cmd-20261021T204748Z-b5e2d0c3c473`, exit 0 in 14.6 s, `/tmp/probe-pv1/results.json`.
CLI `2.1.301`, model `claude-haiku-5-5`, server `pptmstr` `connected`.

- **CLI 2.1.301 defers in-process MCP tools behind `ToolSearch`.** In both
  scenarios the model first called `ToolSearch("select:mcp__pptmstr__show_figure")`.
  The probe's gate allowed only the MCP tool, so it denied that call. pptmstr's own
  gate auto-approves `ToolSearch` (`approval._AUTO`). The probe defect was the
  gate's, not pptmstr's.
- `control` got through anyway: the model called the tool after the denial and
  answered `COLOUR=none DIGIT=none`, so it does not guess. `image` retried
  `ToolSearch` twice, never reached the tool, and answered none. **No image was
  delivered, so this run says nothing about PV1's main question.**
- **Content shape for an MCP text result:** `ToolResultBlock.content` is a
  `list` of `{"type": "text", "text": ...}` dicts, and `UserMessage.tool_use_result`
  is the same list. The transcript renders it as
  `mcp__pptmstr__show_figure -> [{'type': 'text', 'text': 'No figure is attached to this result.'}]`,
  the Python repr of the list. A denied call's content is a plain `str`
  (`PreToolUse:ToolSearch hook error: probe-pv1`, `is_error=True`).
- Tokens: the cached prefix is about 17.8k tokens either way, and `control` cost
  $0.0044. Not comparable as image before/after, because no image was sent.

Fix: the gate allows `ToolSearch` as well, and `max_turns` is 6. Re-run as
`cmd-20261021T204907Z-3eddeacc39e3`, output to `/tmp/probe-pv1-run3`.

## Run 3 (operator, 2026-10-21): PV1 answered

Two haiku sessions through an in-process MCP tool built like `bus.build_server`,
with the probe's gate allowing `ToolSearch` (CLI 2.1.301 defers in-process MCP tools
behind it). Both sessions called `ToolSearch` then `mcp__pptmstr__show_figure`.

| scenario | tool returns | model's answer |
|---|---|---|
| control | text only, "No figure is attached to this result." | `COLOUR=none DIGIT=none` |
| image | text plus a 64x64 PNG of a red square with the digit 7 | `COLOUR=red DIGIT=7` |

1. **The model receives the image.** It named the colour and the digit it could only
   have read from the picture; the control, given no image, did not guess.
2. **What the driver gets back** (`ToolResultBlock.content`): API-shaped, not
   MCP-shaped -- `[{"type": "text", ...}, {"type": "image", "source": {"type":
   "base64", "media_type": "image/png", "data": ...}}, {"type": "text", "text":
   "[Image: source: <a path under the operator's ~/.claude> ...]"}]`. The CLI also
   writes the image to a file under `~/.claude` and appends a text line naming it.
3. **How pptmstr's transcript renders it today:** the repr of that list, base64
   included (`transcript_has_base64: true`); the visible line is cut at 300
   characters but `cap_output` keeps up to 1 MiB in the entry. The preview build must
   substitute a placeholder for image blocks.
4. **Cost:** the image scenario cost $0.00105 against $0.00228 for the control (the
   control's extra is cache creation, not the image). The tool result added 76 input
   tokens without the image and 205 with it; about 50-60 of the difference is the
   CLI's path text, so the 64x64 image itself is roughly 70-80 tokens (builder-41's
   split, an estimate). A 600 px preview is estimated at about 400 tokens (§10.6), not
   measured here.

Not run: any model but haiku; an image at preview size; whether the file the CLI
writes under `~/.claude` accumulates per preview (it will, once per call, unless the
CLI prunes it -- unmeasured).

### Run 3 measured detail (builder)

From `/tmp/probe-pv1-run3/results.json` and the CLI's own session files:

- **The file is the image, byte for byte.** The CLI wrote
  `~/.claude/projects/-tmp-probe-pv1-run3/<session>/tool-results/mcp-pptmstr-blob-<n>-<id>.png`.
  `cmp` against the probe's PNG: identical. The appended text block is exactly
  `[Image: source: <that absolute path>]`. The model is therefore told a local
  path, and a session with `Read` could re-open it outside the preview tool.
- **The base64 is also in the session JSONL** under `~/.claude/projects/<cwd>/`
  (grep for the PNG header matched). Every preview grows that file as well as
  leaving a `.png`.
- **Tokens.** Input per API call, as `cache_creation + cache_read + input` from
  `AssistantMessage.usage`:

  | call | control | image |
  |---|---|---|
  | 1, emits `ToolSearch` | 17,772 | 17,772 |
  | 2, emits the tool call | 18,202 | 18,184 |
  | 3, the answer, after the tool result | 18,278 (+76) | 18,389 (+205) |

  The tool result cost about 130 more input tokens with the image than without.
  That figure includes the CLI's ~157-char path block, perhaps 50–60 tokens, so
  the 64x64 PNG is on the order of 70–80 tokens. The split is not measured. Call 2
  differs by 18 tokens between scenarios whose inputs are identical up to there,
  so differences of that size are noise. This is tighter than "a few hundred at
  most" above, and the measurement is this table.
- `ToolSearch`'s result is `[{"type": "tool_reference", "tool_name": ...}]`, with
  `tool_use_result.total_deferred_tools: 15`. Every session's first preview pays a
  `ToolSearch` round trip unless the tool is made non-deferred.
