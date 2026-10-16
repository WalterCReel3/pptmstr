# imscribe issue drafts, and whether pptmstr can colour code with Pygments today

**Dated:** 2026-10-15 · **Status:** drafts for U5 of `2026-10-15-ui-rework.md`. **Nothing here is
filed.** The operator posts what they agree with to github.com/WalterCReel3/imscribe ·
**Packages read and run:** imscribe 0.2.0, imtypeset 0.4.3.192900, imgui-bundle 1.92.900,
Pygments 2.21.0, all as installed in `.venv`. Paths below are under
`.venv/lib/python3.11/site-packages/`.

The feasibility section comes first because it decides whether there is a syntax-highlighting
issue to file at all.

## Feasibility: Pygments tokens through `TypesetView.set_colours`

**Answer: yes, today, with no change to imscribe or imtypeset.** Shown headless against the
installed packages; see "What ran".

### Why it works

- `TypesetView.set_colours(block, spans)` (`imscribe/ui/typeset_view.py:2574`) takes
  `(start, end, colour)` half-open ranges of `texts[block]` and an ImGui packed `u32`. They are
  drawn as `imtypeset.Mark`s with `MarkPlace.Ink` (the glyphs' own colour), so nothing is laid
  out again.
- The offsets are Python `str` indices. `imtypeset.Document.pack_marks` turns them into byte
  offsets itself (`byte_in` in `_pack_marks`), so non-ASCII code needs no conversion by the host.
- Pygments' `lexer.get_tokens_unprocessed(text)` gives `(index, tokentype, value)` with
  `index` into the string as given. It skips `get_tokens`' preprocessing (`stripnl`,
  `ensurenl`, tab expansion), which would move offsets. Use it, not `get_tokens`/`highlight`.
- imscribe's own demo tokeniser documents this exact hand-off as the host's job
  (`imscribe/ui/pages/_syntax.py:4-11`): *"`imscribe` ships no tokeniser and takes no dependency
  to get one (`docs/decisions.md`); a view is handed *ranges with inks* and never sees a token.
  … A host uses its own highlighter -- Pygments, tree-sitter, whatever its editor has -- and
  produces the same shapes."*

### What pptmstr has to get right (from reading `pptmstr/ui/typeset/views.py`)

- **The caption offset.** `_captioned` sets each fence's info string as the first line of its
  code, so a pptmstr code block's text is `info + "\n" + code`. Pygments must tokenise the text
  after `said + 1` characters (`said = segment.captions[block]`) and every offset be shifted by
  `said + 1`. The probe tokenised a view read straight from markdown, with no caption, so this
  shift is **not exercised**; it is plain arithmetic, but it is the line most likely to be wrong.
- **One `set_colours` per block replaces the block's spans.** `_ink` (settled views) already
  sends the caption's accent span and, for a literal part, a whole-block ink. Syntax spans must
  be merged into that same list — caption span, then token spans, with the block ink filling the
  gaps or left to the code role's default — not set by a second call, which would wipe the first.
- **Streaming views.** `Stream._ink` re-inks captions whenever `view.revision` changes,
  because the library drops a replaced block's inks. A growing code block is presumably replaced
  on every arrival (inferred from that docstring, not checked), so it would be re-tokenised whole
  each time. Pygments' cost on a long block at stream
  rate is **not measured**. imscribe's own note on draw cost (set_colours docstring: 1,881 inks
  on a 600-line block took a frame from 48 to 2,515 µs, before it began handing over only the
  inks in view) is about drawing, not tokenising.
- **Language names.** `pygments.lexers.get_lexer_by_name(info.split()[0])` raises
  `ClassNotFound` for an unknown name; that has to mean "no colours", not an exception in a draw.
- **Dependency.** Pygments is in `.venv` only because pytest requires it (`pip show pygments`:
  `Required-by: pytest`). U5 makes it a direct requirement in `pyproject.toml`; until then a
  runtime import is an accident of the dev environment.

### What ran

Throwaway scripts under `$TMPDIR/rn/` (not in the tree, not committed), run with `.venv/bin/python`:

1. `pyg_feasibility.py`: `imgui.create_context()`, `imscribe.typesetter.load_fonts()` (returned
   `True`), `TypesetView().set_document("intro\n\n```python\n" + CODE + "```\n")` with
   `CODE = 'def f(x):\n\t"""ünïcode é"""  # cömment\n\treturn x + 0x1F\n'` — tabs and non-ASCII
   on purpose. `block_role` gave `['PARAGRAPH', 'CODE']`; `block_text(1)` was the code, tabs kept,
   trailing newline dropped. `PythonLexer.get_tokens_unprocessed` gave 7 coloured spans; for each,
   `view.block_text(1)[s:e]` equalled the token's value (asserted). `set_colours(1, spans)`
   accepted them; `_marks_for(doc)` packed 7 marks.
2. `pyg_draw.py`: two headless ImGui frames (`renderer_has_textures` set, no backend), the view
   drawn at 700 px in a window, then the draw data's vertex colours read. **All six distinct ink
   colours appeared in the vertex buffer.** Control: the same with `clear_colours()` instead of
   `set_colours` — **none of the six appeared.** So the inks reach ImGui's draw list.
3. Not run: a real window and GPU, pptmstr's own `views.py` path (captions, `_ink` merging), a
   streaming view, timing, any language but Python.

## Draft (a): a code block, or any block, can have a background fill

> **Title:** A block style can carry a background fill, drawn under the block's text
>
> **Motivation.** A host showing agent transcripts wants code blocks and terminal-style tool
> output on a near-black band, the convention every reader of such output expects. The theme
> excludes it by design (`imscribe/theme.py:238-240`):
>
> > Deliberately out of scope here: colour (nothing in the node model carries one
> > yet; `imscribe.ui.palette` is where a view's inks are) and per-block background
> > fills.
>
> A host can draw one today, and pptmstr will: split the window's draw list into two channels,
> draw the view in channel 1, then in channel 0 fill `TypesetView.block_rect(block)`
> (`typeset_view.py:2855`) for each block whose `block_role` is `"CODE"`, then merge. Measured
> headless: the fill's rectangle matched `block_rect` and was drawn before the text in index
> order. What that leaves the host to get wrong, and what only the library can do well:
>
> 1. **No inner padding.** The rect is the block's extent; the text starts at its left edge
>    (probe: `block_rect` left = 24.0, and so did the text). `BlockStyle` has `left_indent` but no
>    right indent, and `space_above`/`space_below` are the gaps *between* blocks, so using them
>    as padding puts the inter-block gap inside the band.
> 2. **Cost and visibility.** `block_rect` is "one call into the library, which lays the block
>    out" (its docstring). The host must find which code blocks are in view itself and call it
>    for each, every frame — the clipping the view already does for inks (`_marks_for`).
> 3. **Columns and hosted blocks.** In a document shown in columns a block can be cut across two
>    columns; one rectangle is then wrong. The host cannot see the cut.
> 4. **Draw-list channels.** The workaround depends on the view not splitting channels itself
>    (ImGui does not nest `ChannelsSplit`). True of 0.2.0 by reading; not a contract.
> 5. `MarkPlace.Behind` (`imtypeset/__init__.py:274`) is not a substitute: a `Behind` mark over a
>    whole code block draws one box per line, ending where each line's text ends (probe: x edges
>    24, 96, 176, 264 for three lines at a 700 px measure). Ragged, as a selection is.
>
> **Proposed API sketch.**
>
> ```python
> @dataclass(frozen=True)
> class BlockFill:
>     colour: int            # packed u32, or a Palette role name resolved at draw
>     padding: Scaled = 0    # inside the band, on every side; text is set narrower by 2x
>     rounding: float = 0.0  # px
>
> BlockStyle(..., fill: BlockFill | None = None)   # imscribe.theme
> ```
>
> Set by role in the theme (`CODE`, and a host's own hosted/role-named blocks), drawn by the view
> under the text from the same geometry it lays out with, clipped as inks are, and split per
> column. If the theme should stay colour-free, the same could live on `Palette` keyed by role,
> with only `padding` in the theme — the padding is typographic (it changes the measure), the
> colour is not.
>
> **Possibly an imtypeset half.** If drawing belongs to the library rather than to imscribe's
> view, it needs a block-extent place for marks (a `MarkPlace.Block`: one rectangle per block
> per column, at the block's measure, not the text's) or a per-block fill in its draw. Not
> drafted separately: which side owns it is imscribe's call.
>
> **Evidence.** `imscribe/theme.py:238-240` (quoted); `imscribe/theme.py:577` `BlockStyle`
> (no fill, no right indent); `imscribe/ui/typeset_view.py:2855` `block_rect`;
> `imtypeset/__init__.py:274` `MarkPlace` (`Ink`, `Behind`, `Under`), `:454` `BlockGeometry`.

## Draft (b): syntax highlighting — not drafted

The task asked for a draft only if a gap remained after the feasibility check. None does:
`set_colours` takes host tokens in the shape Pygments gives, converts offsets itself, survives
non-ASCII and tabs, and draws (run above). imscribe's decision not to ship a tokeniser
(`_syntax.py:4-11`) is the right split for pptmstr too, which will own Pygments.

Two things that are **not** filed but worth a line if (a) is filed:

- `set_colours` sorts spans and does not reject overlaps. Overlapping spans would be handed to
  the library as overlapping `Ink` marks with an undefined winner. A host merging caption, token
  and default spans (pptmstr's case) can produce overlaps by mistake. Whether to reject or
  define "last wins" is the maintainer's call. Not tested here.
- The view docstring has no Pygments recipe, and the trap (`get_tokens` moves offsets,
  `get_tokens_unprocessed` does not) is one line to document.
