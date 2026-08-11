# Model output renders as literal text, so structure is invisible

**Dated:** 2026-08-10 · **Status:** proposed · **Found by:** research

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

Parsed once at block finalisation, cached in the frozen `Block`.

### 5. Span layout and draw

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

## Open

- **HiDPI is deliberately naive for now.** It is unverified whether font sizing
  double-applies between `adjust_size_to_dpi` and `FontScaleDpi`;
  `dpi_font_loading_factor()` is 1.0 on this machine and X11 reports framebuffer
  scale 1, so the two cannot be distinguished here. Accepted as a known-unknown
  rather than guessed at — revisit on a 200% display. Nothing in this design
  hard-codes a scale factor, so the fix stays local to `load_fonts`.
- **Nested list depth** — flat rendering with an indent is assumed sufficient.
  Worth revisiting once real transcripts have been read in rich mode.
- **Block hit-testing vs. link clicks is unresolved and must be prototyped
  first in step 5.** An `invisible_button` over a block rect restores hover,
  context menu and a hover highlight to draw-list content, but it will eat clicks
  from links underneath. `set_next_item_allow_overlap()`
  (`imgui/__init__.pyi:2806`) is the intended lever; emitting links as real
  `text_link_open_url` widgets instead sidesteps it by giving them correct
  z-order. This does *not* block step 1, which operates on segment runs in the
  existing line-based path.
- **Whether `RICH` becomes the default mode** or stays opt-in. Defer until it can
  be looked at; `RAW` stays the default meanwhile, so a regression in rich mode
  cannot take the pane down with it.

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

Read from stubs but not executed: `ImFontConfig` field meanings, the `push_font`
pre-global-scale note. `imgui_md`'s internal layout strategy was inferred from
symbols in the stripped binary — no C++ source is vendored.
