# Model output renders as literal text, so structure is invisible

**Dated:** 2026-08-10 · **Status:** steps 1–5 built; four rendering defects found
and fixed 2026-08-11 (see the last section) · **Found by:** research

## What was observed

`SegmentKind.OUTPUT` is model prose and arrives with markdown in it. The pane
renders it verbatim (`ui/transcript_pane.py:187-214`), so a fenced code block
shows its backticks, `**emphasis**` shows its asterisks, and a bulleted list is
indistinguishable from a paragraph that happens to start with a hyphen. Nothing
is *wrong* on screen; it is just harder to read than it needs to be.

Scope is **`SegmentKind.OUTPUT` only**. `TOOL_CALL`, `TOOL_RESULT`, `ERROR`,
`SYSTEM` and `COMPACTION` are already structured and stay literal — markdown
would actively damage them, since a JSON tool result is full of underscores and
asterisks that mean nothing.

## The order of work

Deliberately sequenced so the cheapest, most general thing ships first and the
expensive thing is the last commitment made.

### 1. A copy affordance, on its own

**Built on 2026-08-10.** Runs are a maximal same-kind span of lines rather than a
`Transcript` segment: `close_segment()` can split two same-kind segments mid-line
and `sync()` extends one `Line` across the split, so segment identity is not
representable at line granularity. A kind change always falls on a line boundary,
and it is also what the operator sees. Row hit-testing is on the vertical band
only, and each band claims the spacing below the row above it — an x-extent test
makes a short line unclickable, and a min/max test leaves a dead gutter between
every row. `scripts/verify_transcript_copy.py` drives both of those with synthetic
input, since neither exists outside a live frame.

Independent of markdown and useful immediately. Copying a tool result or an error
is plausibly more valuable than copying prose, and it works today against
`Line.text` with no segmenter in existence.

- `imgui.set_clipboard_text` (`imgui/__init__.pyi:3155`).
- The unit is the **segment run**, not the line and not the block — blocks do not
  exist at this step, and a whole model message is what an operator actually
  wants. Block-granular copy arrives with step 3 as a context-menu entry.
- This is also the answer to text selection. See the decision below.

### 2. Font faces, and a defect found while looking at them

**The FontAwesome defect below was fixed on 2026-08-10, separately from this
work. The rest of this section was built on 2026-08-10.** Two things the plan did
not anticipate: the repo had no assets directory at all, and the bundle ships
Inconsolata *Medium* but no Bold — so the face is vendored under `pptmstr/assets/`
rather than at the repo root, because the wheel ships `packages = ["pptmstr"]` and a
root-level folder would be missing from every installed copy while degrading
silently. `add_assets_search_path` is additive and consulted after the main folder,
so the vendored face resolves without shadowing anything the bundle provides.
`scripts/verify_fonts.py` pins the result: Inconsolata is monospaced, so Medium and
Bold have identical advances and only the ink differs (7.00 vs 8.00 px at 16px) —
a width comparison cannot tell a real bold face from a second copy of the regular.

`theme.py:29` imports `icons_fontawesome_6`, but
`load_font_ttf_with_font_awesome_icons` merges FontAwesome **4**, which stops
around U+F2E0. Two states render tofu right now:

- `ICON_FA_BRAIN` (U+F5DC) — `AgentState.THINKING` (`theme.py:158`)
- `ICON_FA_COMMENT_DOTS` (U+F4AD) — `AgentState.AWAITING_INPUT` (`theme.py:162`)

Fix, verified working:

```python
hello_imgui.get_runner_params().callbacks.default_icon_font = (
    hello_imgui.DefaultIconFont.font_awesome6
)
```

It works despite the stub comment at `hello_imgui.pyi:1886-1888` claiming it only
applies when the default font callback is in use. **This is unrelated to markdown
and should be fixed on its own.** The glyph+label redundancy (§6.1) is why it was
survivable rather than visible — the glyph channel is simply dead for two states.

Font work proper:

- **First font loaded wins, unconditionally** — `Fonts[0]` is the app default at
  `NewFrame()`. Inconsolata must be added before anything else touches the atlas.
  Verified by loading another family first and watching the whole UI change.
- `FONT_SMALL` (`theme.py:531`) is dead. Since ImGui 1.92 a face rasterises on
  demand at any size, so a second `ImFont` at 13.0 buys nothing.
- **Inconsolata has no italic and cannot have one.** Not "not bundled" — it does
  not exist upstream, and 1.92 will not synthesise an oblique.
  `rasterizer_multiply` adjusts thickness, not weight or slant. Emphasis is
  therefore rendered as a colour shift, not a slant (see the decision below), so
  the only face to add is `Inconsolata-Bold`, which does exist upstream.
- Faces degrade individually: `face()` returns `None` for a missing face, and
  `push_font(None, size)` means "keep current". The renderer works with zero new
  fonts and improves as they are added.
- Hazard: the existing `push_font(None, 12.5)` sites inherit whatever face is
  live. Every face push needs its `pop_font()` before the small-text helpers run.

### 3. Block segmentation

**Built on 2026-08-11**, in `pptmstr/ui/blocks.py`. Not wired into
`NodeTranscript`/`draw()` at the time it was built — there was no renderer yet to
consume it, and paying the parse cost for a structure nothing read would have been
waste. Wired in as part of step 5.

A block layer over the existing line cache. Both are monotone and each carries
exactly one "how far have I got" integer:

```
Transcript (bytes) --sync--> list[Line] --feed--> list[Block] + live Block
     consumed                stable/open_line        consumed_lines
```

The trick is to feed the segmenter only *stable* lines:

```python
stable = len(cache.lines) - (1 if cache.open_line else 0)
```

Every arrow here inherits **I7** (*transcripts are append-only; readers take
`(buffer, length_at_snapshot)`*) — the block layer adds no new concurrency
assumption, it re-uses the one the design already rests on. Nothing below works
if I7 is ever relaxed.

`lines[-1]` is mutable while `open_line` is set (`transcript_pane.py` rewrites it
in place on each streamed token); every other element is immutable once written.
So finalisation is a **cache-validity boundary, not a rendering boundary**: the
trailing block renders through the same parser as every finalised block, just
re-derived each frame instead of cached. One code path, so live and final
rendering cannot disagree.

Blocks: paragraph, ATX heading, fenced code, bullet item, ordered item,
blockquote (depth 1), thematic rule, **GFM table**, literal.

Three rules that are not obvious:

- **Model the list item, not the list.** A blank line does not end a list — that
  is what makes it loose — so a list-level block can never be finalised under
  streaming. Per-item blocks restore incrementality and match what the draw call
  wants anyway. Cost: tight-vs-loose spacing is unknowable, so pick one.
- **A segment-kind change force-closes the open block, including an open fence.**
  Not doing this is catastrophic: one unbalanced fence in a model message would
  style the entire rest of the session as code. A `closed: bool` lets the
  renderer show an unterminated fence honestly. Fence state does not resume
  across the boundary.
- **A table is detected when a paragraph finalises, not while it accumulates.**
  A GFM table is a header row plus a delimiter row (`|---|---:|`) plus body rows,
  and it is monotonicity-safe for the same reason setext headings are not: only a
  blank line finalises a paragraph, so the delimiter row has always arrived before
  the decision is made. Detection is a regex on the second accumulated line.

### 4. Inline parsing — `markdown-it-py`

**Built on 2026-08-11**, in `pptmstr/ui/inline.py`. Parsed once at block
finalisation, cached in the frozen `Block` — but only for blocks `feed()`
actually commits, never for `live_block()`'s re-derived tail; see that module's
docstring for why the two had to be pulled apart even though block *structure*
deliberately shares one code path between them.

`InlineToken` is a hashable, value-equal flattening of `markdown_it.token.Token`,
not the library's own type: `Token` has structural equality but is not hashable,
and `Block` is a frozen (hashable-by-default) dataclass, so a raw `Token` field
would have made `hash(block)` a landmine.

### 5. Span layout and draw

**Built on 2026-08-11.** Layout lives in `pptmstr/ui/span_layout.py` (no ImGui,
unit-tested against a fixed-width fake measurer); drawing lives in
`pptmstr/ui/rich_pane.py` (the one module in the stack that touches ImGui,
exercised against a live frame by `scripts/verify_rich_render.py`).
`RenderMode.RICH` is opt-in via a radio button next to the existing wrap
toggle, exactly as the Open section below anticipated — `RAW` stays the
default.

**Descoped rather than guessed at, both flagged in `rich_pane.py`'s own
docstring:**

- **Clickable links.** This is the "must be prototyped first" item below.
  Rather than prototype it, links render as underlined text with no click
  affordance — sidestepping the invisible-button-vs-link-widget conflict
  entirely rather than resolving it. Copy-block is how the URL gets out today.
- **Syntax highlighting** — as already anticipated, deferred to arrive with
  `TextEditor`-based code selection.
- **Per-cell table styling and delimiter-row alignment** — a cell draws as
  plain wrapped text (`imgui.text_wrapped` over the flattened token stream);
  no nested emphasis, no `:---:`-driven column alignment.
- **Heading level is not drawn visually** — no marker, no size change (headings
  render as `Face.BOLD` + `P.accent`, distinguished from plain bold prose by
  colour only). `Block.level` is still recorded, so this is a rendering gap,
  not a data-loss one.

Two things worth recording that the plan didn't anticipate:

- **`begin_popup_context_item` after `EndTable()` binds to the table as "the
  last item"**, the same as it does for an ordinary widget — this was the
  single most uncertain assumption in the whole step and it holds. Verified in
  `scripts/verify_rich_render.py`'s table stage, not inferred.
- **Bold and regular share one set of width measurements.** Verified in step
  2's `scripts/verify_fonts.py` (identical advance, 8.00px at 16px) and relied
  on here: `layout_inline` only ever measures with `Face.BODY`, even for runs
  that draw in `Face.BOLD` — a second measurement pass per style would have
  been needed on a family where that isn't true.

Draw-list based, using `ImFont.calc_word_wrap_position_python(size, text, width)`
(`imgui/__init__.pyi:11469`) — ImGui's own break-opportunity finder, callable on
an arbitrary font at an arbitrary size with nothing pushed. It handles word
boundaries, hard-breaks a word wider than the line, stops at `\n`, and returns
`len(text)` when everything fits.

## Decisions recorded

**Tables are in scope for v1.** They are a primary channel for model feedback, so
treating them as a v2 nicety would leave the most information-dense thing the
model produces rendered as pipe-delimited noise.

**Emphasis is a colour shift, not a slant.** Bold gets a real `Inconsolata-Bold`
face; italic gets a palette role. This is the terminal convention and it is the
only option that keeps the pane on Inconsolata, since no Inconsolata italic
exists. It also means only *one* font file is vendored rather than a whole
family. Consequence: bold and italic are distinguishable from body text but the
*distinction between them* is carried by colour alone in one direction — acceptable
here because emphasis is decorative in model prose, never load-bearing, unlike
the `+`/`-` diff gutter which must stay redundant.

**Hand-rolled rendering, not `imgui_md`.** `imgui_md` is installed and would be
far less code, but it requires all four faces of a family and raises
`RuntimeError` at `imgui_md_wrapper.cpp:197` if one is missing — so it cannot
accept the faked-italic decision above and *forces* a migration to a 4-face
family. It also renders headers as larger regular text rather than bold,
re-parses every visible block every frame, and exposes no rule hooks (see the
emphasis override below).

The deciding factor is tables, and it points the opposite way from expected.
`imgui_md`'s own demo documents the wart: *"the first row will impose the columns
widths. Use nbsp; to increase the columns sizes on the first row if required."*
The workaround is **editing the markdown source**, which is impossible for
generated output. ImGui's native table API sizes columns properly
(`TableFlags_.sizing_stretch_prop`, `imgui/__init__.pyi:5217`) and gives resizable
columns for free (`:5157`). So the one feature that most argued for `imgui_md` is
the one it handles worst for this input.

**Underscore emphasis is disabled.** CommonMark's intraword rule already protects
`foo_bar_baz`, but `__init__` and `__all__` are word-boundary delimiter runs by
the letter of the spec and render as bold — spec-correct, and wrong for a
transcript full of Python. Overriding the `emphasis` rule via `md.inline.ruler.at`
(public API) suppresses `_` entirely. **Cost: `_italic_` no longer italicises.**
This is a deliberate corpus-specific trade and needs a test saying so, or someone
will "fix" it.

**Copy-block replaces text selection.** Draw-list text is not selectable. Rather
than treat that as a deferred rewrite, `Block.lines` holds the **verbatim source**
— so copying yields the exact markdown the model emitted. For whole-message copy
this beats drag-selection: no partial lines, no dropped newline, and it round-trips
into an issue. What is genuinely lost is sub-paragraph selection.

**Search demotes rich mode to a line path.** `_visible()` filters at line
granularity, which shreds a fence into disconnected rows. Block-level filtering
either shows whole blocks (defeating the point on a 200-line fence) or shreds
them. Demoting is one line and is honest; it needs a hint next to the search box.

**Setext headings and link reference definitions are excluded permanently.** Not
a scope cut — they violate monotonicity. A `[a]: /url` arriving at byte 10,000
would change a block finalised at byte 100. Consequence to accept: `para\n---`
renders as paragraph + rule where CommonMark says `<h2>`. The streaming property
test below fails if anyone tries to add them, which is the correct outcome.

## Alternatives considered and rejected

**A hand-rolled inline scanner, avoiding the dependency.** Measured against
CommonMark: `foo_bar_baz` italicises `bar`; `2 * 3 * 4` italicises ` 3 `;
`path/to/file_name.py::test_thing` italicises. CommonMark gets these right via
delimiter-run flanking rules, and implementing those correctly *is* implementing
the reference algorithm. There is no cheap subset that handles the two most common
shapes in this corpus.

**`imgui_md` as a font provider only** (init it, never call `render()`). Verified
to work — `get_font`/`get_code_font` return live faces with no rendering. Rejected
because with italic faked there is exactly **one** face to add, so the benefit is
a single `load_font_ttf` call, and it would buy the all-or-nothing abort
semantics along with it.

**Vendoring a 4-face family** (JetBrains Mono was the closest fit on width; OFL,
statics committed upstream). Rejected in favour of faked italic: ~1.4 MB vendored
and a visible change to how the whole pane reads, to gain a slant that terminal
convention has never needed.

## Consequences worth stating before building

- **Estimate is ~550–650 lines of implementation plus ~400 of tests**, tables
  included (~30 in the segmenter, ~60 in the renderer — cheaper than it looks
  because ImGui computes column widths). The remaining creep vector is nested
  lists and blockquotes beyond depth 1; `level` is tracked in the dataclass so
  the renderer can indent, but correct nesting semantics are not attempted.
- **A table cell is a wrap context.** Cell width comes from ImGui after column
  sizing, so span layout inside a cell uses `get_content_region_avail().x` and
  inherits the documented one-frame lag while a column is being dragged.
- **The clipper cannot be used in rich mode**, same constraint the wrap toggle
  already documents (`transcript_pane.py:13-17`). The bound must be cumulative
  **source lines**, not a block count: a 400-block window containing one
  3000-line fence is a stall. A code block also needs an inner per-block clamp,
  or the outer bound is unenforceable.
- **A very long live paragraph is the worst case.** Inline parse is ~273 µs for
  642 chars and ~1.7 ms extrapolated at 4 KB — too much at 60 fps. Above a limit,
  render the live block plain and let styling appear when it finalises. A live
  *code fence* costs nothing, which is convenient since fences grow largest.
- **`RenderMode` replaces `TranscriptState.wrap: bool`.** Blast radius is
  `_draw_lines` plus one checkbox; nothing outside the module imports these types.
- **Syntax highlighting and code selection arrive together.** `TextEditor`
  (`imgui_color_text_edit.pyi:183`, `:778`) is read-only-capable with real
  language support, so fences could later get highlighting *and* selection in one
  widget. Deferring highlighting means fences rely on copy-block until then.

## Tests

The property the design rests on: **finalised blocks are never revised.** For
every prefix of the document, `cursor.blocks` is a prefix of the final result.
That makes byte-at-a-time streaming directly comparable to a one-shot parse, and
it doubles as the enforcement mechanism for the exclusions above.

Also worth pinning: an unbalanced fence in one message does not style the rest of
the transcript; for a transcript with no OUTPUT segments every block is `LITERAL`
and the lines round-trip unchanged (asserting "the existing path is untouched"
rather than claiming it); and the two emphasis cases the override deliberately
changes.

**Built**, across `tests/test_blocks.py`, `tests/test_inline.py` and
`tests/test_span_layout.py`: the streaming-prefix property (plain and across
mixed `SegmentKind`s), every block kind's finalisation and inline-content
stripping, the underscore-emphasis override against the unpatched parser's own
(wrong) output, and word-wrap layout (bold surviving a wrap, break tokens,
overlong-word hard-break) against a fixed-width fake measurer — deliberately not
against real ImGui metrics, so the layout *algorithm* is checkable without a live
frame. What a fake measurer cannot check — real wrap-position/text-size
semantics, draw-list output, table widget behaviour, hover and click — is
`scripts/verify_rich_render.py`'s job instead.

## Open

- **HiDPI is deliberately naive for now.** It is unverified whether font sizing
  double-applies between `adjust_size_to_dpi` and `FontScaleDpi`;
  `dpi_font_loading_factor()` is 1.0 on this machine and X11 reports framebuffer
  scale 1, so the two cannot be distinguished here. Accepted as a known-unknown
  rather than guessed at — revisit on a 200% display. Nothing in this design
  hard-codes a scale factor, so the fix stays local to `load_fonts`.
- **Nested list depth** — flat rendering with an indent is assumed sufficient.
  Worth revisiting once real transcripts have been read in rich mode.
- **Block hit-testing vs. link clicks was sidestepped, not resolved.** Built
  (2026-08-11) with an `invisible_button` per block for hover/context-menu/copy,
  and links rendering as underlined text with **no click affordance** —
  deliberately avoiding the conflict this bullet describes rather than
  prototyping a fix for it. `set_next_item_allow_overlap()`
  (`imgui/__init__.pyi:2806`) and real `text_link_open_url` widgets are both
  still live options; still open for whenever clickable links are wanted.
- **Whether `RICH` becomes the default mode.** Resolved as this bullet expected:
  `RAW` stays the default, `RICH` is opt-in via a radio button.

`orchestrator-design.md` is not amended by this document. Per the repo
convention, `planning/` holds scope snapshots for work not yet started; §2.5 and
build-order item 7 get updated when this is built, not when it is proposed.

## Verification notes

Verified by running against the installed bundle (1.92.801): the wrap-position
helper and its edge cases; `push_font` face/size semantics; first-loaded-font-wins;
`imgui_md`'s font set, its `-RegularItalic` naming, and its missing-face
`RuntimeError`; assets search paths cannot shadow bundle assets; the FontAwesome
4-vs-6 glyph coverage per icon and the fix; every CommonMark inline case quoted
above, before and after the emphasis override; the inline parse timings.

Step 5, additionally verified live via `scripts/verify_rich_render.py`: every
`BlockKind` renders without raising, including an unbalanced fence and a
still-streaming live block, across several frames; a long paragraph actually
produces more than one row at a narrow width; a block's `invisible_button`
correctly latches hover and right-click-copy puts its verbatim source on the
clipboard; and — the single riskiest assumption in the whole step —
`begin_popup_context_item` called immediately after `EndTable()` does bind to
the table as "the last item", the same as for an ordinary widget. That last one
would have failed silently (no context menu, no crash) had it been wrong, which
is exactly why it got a targeted check rather than being inferred from the
smoke test passing.

Aiming a synthetic click at a popup menu item by a fixed pixel offset from the
originating click (the pattern `scripts/verify_transcript_copy.py` uses) proved
too fragile to reuse here — cursor-warp latency on a real X11 desktop drifts the
observed mouse position by several pixels frame to frame, easily overshooting a
popup row under 20px tall. `verify_rich_render.py` instead monkeypatches
`imgui.menu_item_simple` to record where the widget actually rendered and aims
there, which is worth reusing for any future script that needs to click inside a
popup.

Read from stubs but not executed: `ImFontConfig` field meanings, the `push_font`
pre-global-scale note. `imgui_md`'s internal layout strategy was inferred from
symbols in the stripped binary — no C++ source is vendored.

## Four rendering defects, found by looking at it (2026-08-11)

None of these had a failing test, and none would ever have produced one: each
drew perfectly happily and merely looked wrong. They surfaced when DETAIL became
the renderer's second caller (2026-08-11-what-it-said-is-a-byte-tail, step 3) and
a screenshot got taken.

- **A softbreak was rendering as a row break.** CommonMark says it is a space, and
  the difference is not pedantic here: a model hard-wraps its prose near 80
  columns, so honouring those newlines printed the model's wrapping *and* the
  pane's on top of it — ragged double-wrapped paragraphs at any width. Now only a
  hardbreak breaks.

- **Every wrapped continuation row began with a space.** `calc_word_wrap_position`
  returns the index *of* the space it broke at, so the remainder carries it
  (verified live: index 27 of "…are being accepted" yields the tail " accepted").
  **The unit tests could not have caught this**: `tests/test_span_layout.py`'s
  fixed-width fake consumed the space, which was the one place it disagreed with
  the real function, and its docstring conceded the convention was "not
  ImGui-verified". The fake now matches, so the fake's own accuracy is what the
  new test protects.

- **Words split mid-line once paragraphs reflowed** — "at colum / n 68". Latent all
  along and masked by the softbreak bug: with only a sliver of row left, a wrap
  function *asked* for a break inside it will always give one. A break landing
  inside a word now takes a fresh row instead, guarded on the row being non-empty
  so an overlong word still makes progress rather than retrying forever.

- **Every line of markdown carried an extra widget gap.** `_draw_rows` advanced by
  `get_text_line_height_with_spacing()`, which adds `style.ItemSpacing.y` — the
  gap ImGui puts between *widgets*. Correct between blocks, where `_draw_one`'s
  `invisible_button` already supplies it; a widget gap inserted into a paragraph
  everywhere else. A 60-word paragraph went 244px → 196px. The link underline was
  written as `line_h - 4.0` and landed correctly only because `ItemSpacing.y`
  happened to be 4; it is now measured from the tight height.

Fences also stopped drawing their own ``` delimiters, showing the info string as a
dim label instead. `Block.lines` stays verbatim — copy-as-markdown depends on it —
so the trim happens at draw time, and `windowed()`'s per-fence cost was corrected
to match what is now actually drawn.

The lesson worth keeping: `rich_pane`'s pure helpers had **no test file at all**
before this pass, and its two genuinely wrong behaviours both lived behind a
correct-looking fake. `tests/test_rich_pane.py` now covers `fence_body`,
`windowed` and the verbatim-copy guarantee. Rendering defects need eyes on pixels;
the tests are what stop them coming back.
