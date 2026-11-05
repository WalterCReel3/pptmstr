# Where a vector figure can sit in imscribe's layout and draw path

Research for board task `seams`, 2026-10-18. Host side only: what in imscribe
(and pptmstr's wrapper) would carry a non-glyph figure. How an SVG or a Mermaid
source becomes pixels is the `svg` / `mermaid` tasks' question, not this one's.

**Trees read.** imscribe at `~/Source/imscribe`, HEAD `64b752c`. pptmstr pins
`af06f77` (`imscribe.ref`); `git diff --stat af06f77 HEAD` touches only
`ASSIGNMENTS.md`, `HANDOFF-pptmstr.md` and `docs/status.md`, so every source
citation below holds for the pinned commit. imtypeset at `~/Source/imtypeset`,
HEAD `4f887d3`; the pin is `0bac066` (`imtypeset.ref`), and
`git diff --stat 0bac066 HEAD -- include` is empty, so the header citations
hold for the pin. pptmstr citations are this worktree (`feat-vector-rendering`).

**Basis.** Everything is read unless it says *run* or *inferred*. Two things
were run, both in `$TMPDIR` with `~/Source/pptmstr/.venv` (imscribe 0.2.0
installed): a parse/stream probe (section 1, 4), and
`pytest tests/test_typeset_hosts.py` in imscribe's own venv: **27 passed, 4
errored** -- the four `test_in_a_window_*` tests could not run here because the
`window` fixture writes into `~/Source/imscribe/artifacts/`
(`tests/conftest.py:510`), which is read-only to this session. So the hosted
widget has **not** been seen drawing in a real window by this research.

---

## 0. A correction to the task's framing first

The task names `nodes.py`, `pack.py`, `shipout.py`, `selection.py` and
`backends/` as the box model and draw path. **They are not on pptmstr's path.**
They are the "reference core", kept only for the SVG litmus renderer
(`make svg`):

- `imscribe/__init__.py:16-31`: "Until tranche 5 of the swap (2026-10-11) there
  was one: a Python box-and-glue layout core ... The views and the backend are
  deleted. What is left of the core is what the SVG litmus renderer needs ...
  No module of the typeset layer imports it".
- `imscribe/typesetter.py` (module docstring, "The description"): the old
  `Converter` that walked node material "is deleted with the last caller of
  that path".

What pptmstr actually uses (`pptmstr/ui/typeset/views.py`, `gate.py`):
`parse_markdown` / `MarkdownStream` (`imscribe/typeset_markdown.py`) produce
`MarkdownText` / `MarkdownCode` / `MarkdownRule` / `MarkdownTable`; a
`TypesetView` / `TypesetStreamView` (`imscribe/ui/typeset_view.py`,
`typeset_stream_view.py`) describes them as `BlockDescription`s
(`imscribe/description.py`) and hands them to imtypeset (C++), which lays out
and draws. The reference core is answered briefly in section 2 for
completeness, but **the seam is in the typeset layer, and it already exists**
(section 2, "Host blocks").

Also stale and worth knowing: `imscribe/backends/__init__.py` still describes
an `imgui_draw` backend "and is the real target"; that module is gone (only
`svg.py` is in `imscribe/backends/`).

## 1. How markdown becomes blocks; fences and images today

**Parser.** markdown-it-py, configured in `imscribe/markdown_rules.py` (module
docstring lines 1-29: one parser shared by both readers). The live reader is
`imscribe/typeset_markdown.py` ("Markdown straight to what imtypeset takes:
text and runs, and no node on the way", line 2). `imscribe/markdown.py` is the
reference core's reader.

**A fenced block.** The token `fence` / `code_block` goes to
`_Reader._code(source, language)` (`typeset_markdown.py:1062-1072`), which makes
one `MarkdownCode(style, text, language, left_indent)`; `language` is the info
string, stripped (`typeset_markdown.py:2486` for the stream path; old core:
`markdown.py:1103-1104`, `916-929`). **Nothing in the reader branches on the
language.** With `ReaderOptions.show_fence_info` the info string becomes an
extra `MarkdownText` of kind CODE in front (`typeset_markdown.py:525-545`);
pptmstr does not use that option, it captions fences itself (`_captioned`,
`pptmstr/ui/typeset/views.py:1129-1166`: the info string is set as the code's
first line).

Run (probe, default `Theme.build()`, imscribe's own parser without pptmstr's
reader changes):

    "```mermaid\ngraph TD; A-->B\n```"  -> MarkdownCode language='mermaid' text='graph TD; A-->B'
    "```svg\n<svg ...></svg>\n```"      -> MarkdownCode language='svg'     text="<svg ...></svg>"
    "Img: ![alt](x.svg) after."         -> MarkdownText text='Img:  after.'   (image dropped)

So a ```` ```mermaid ```` or ```` ```svg ```` fence arrives at the view as an
ordinary `MarkdownCode` whose `language` names it, with its body verbatim. That
is exactly the shape the existing diff host keys on (section 2).

**An image `![](...)`.**
- imscribe default: dropped with its alt text (`typeset_markdown.py:993-995`;
  `markdown.py:158-162`: "Deliberately out of scope: images ... dropped
  silently because rendering them is a different project").
- `ReaderOptions.images_as_text`: the image rule is not run and the source
  reads as `!` plus a link (`typeset_markdown.py:490-497`).
- **pptmstr**: does neither through `ReaderOptions`; it disables the `image`
  rule on imscribe's shared private parser (`_HIDING_RULES`,
  `pptmstr/ui/typeset/gate.py:149`; `_reader`, `gate.py:550-600`), so in
  pptmstr an image is `!alt` + a link whose destination is shown
  (`_show_destinations`). The stated reason (`gate.py:150-153`, `_reader`
  docstring) is that the reader must show what was written -- an approval's
  reader must see what the recipient gets.
- There is **no image block shape**: an image is an *inline* token inside a
  paragraph's inline children. Rendering one would need either an inline
  object in the text (which imtypeset does not have, section 2) or a reader
  change that promotes a paragraph holding only an image to a block of its
  own. Both are imscribe changes, and the second contradicts pptmstr's
  show-what-was-written rule for exactly the text it would replace.

## 2. The box model: can a fixed-size opaque box live in the flow?

### Typeset layer (what pptmstr draws with): block-level yes, inline no

**Host blocks exist and are the precedent.** imtypeset's header:

> `ImTypesetBlockKind_Host = 5`: "A rectangle Extent tall that the library
> leaves empty, for a widget of the host's: GetDocumentBlockGeometry() says
> where it is. Its text is what a copy that covers it gives."
> -- `~/Source/imtypeset/include/imtypeset.h:730-733`; `Extent` "Host: its
> height in pixels" (`:917`); `SetDocumentBlockExtent` for "a host widget that
> grew" (`:2137-2138`).

imscribe exposes it as `host_for` on `TypesetView` / `TypesetStreamView`
(`imscribe/ui/typeset_view.py:542-558`):

- `HostFor = Callable[[TypesetView, int, object], Hosted | None]`
  (`typeset_view.py:705`), asked once per block object when it is described
  (`_ask_hosts`, `typeset_view.py:1443-1480`): a block it answers for becomes a
  `KIND_HOST` `BlockDescription` whose `text` is the widget's `text`, initial
  extent `_HOST_GUESS_PX = 16.0` (`:680`).
- `Hosted` protocol (`typeset_view.py:683-702`): `text` (what a copy gives),
  `height_for(width_px) -> float` (asked **every draw**, with the rectangle's
  width), `draw(*, palette, width_px)` (called with ImGui's cursor at the
  rectangle's top-left), optional `handle_keys()`.
- Width is the full measure minus the block's indent (`_host_wants`,
  `typeset_view.py:3025-3035`; `_find_hosts`, `:3319-3347`, gives
  `(left, top, max(width_px, left+1), bottom)`). Height is the widget's own
  choice, so **an aspect-preserving figure is natural**: `height_for(w) = w *
  h0 / w0`, capped.
- A height change costs one `set_block_extent` (`typeset_view.py:248-249`; the call, `:3083`).

**A shipped user of it**: `TypesetDiffFences` (`imscribe/ui/typeset_hosts.py`)
draws ```` ```diff ```` / ```` ```patch ```` fences as a `TypesetDiffView`. It
keys on `block.language`'s first word (`accepts`, `typeset_hosts.py:240-252`;
`FENCE_LANGUAGES`, `:66`), reads the fence from the source map, and grows a
line at a time. pptmstr wraps it (`_Fences`, `pptmstr/ui/typeset/views.py:1245-1292`;
`gate.diff_fences`, `gate.py:906-917`). **A figure fence is the same shape of
thing with a different widget inside.**

**Inline (in a line of text): no.** imtypeset's run flags are `NoBreak`,
`Formula`, `DisplayFormula` (`imtypeset.h:483-493`); there is no inline object
of host-given size. A formula is the only non-glyph inline box, and it is the
library's own (set from the MATH font, `imtypeset.h:204-208`), not a host
rectangle. An inline figure would be an imtypeset C++ change (a run that is an
opaque box of given width/height/depth plus a geometry query for it), then an
imscribe description change, then a pin bump of all three (section 8).
**Inferred**: of the asks in this note it is the costliest by a wide margin.

A Host block cannot sit beside text (no float, no wrap-around): it is a
rectangle across the column, stacked like any block. A quote's or list's
indent applies (`left_indent`).

### Reference core (not on pptmstr's path), for the record

`nodes.Box` has fixed `width/height/depth` and arbitrary contents
(`nodes.py:258-288`), and may stand in an hlist or a vlist; `nodes.Rule` is a
sized solid box (`nodes.py:290-315`). Precedents: `shipout.atom_box` wraps a
formula so it "ships as one item" (`shipout.py:312-326`); `HostBlock`
(`imscribe/ui/composition.py:1464-1505`) reserved an empty vbox for a host
widget in the deleted `BareView`. So the old core *could* carry an inline
figure box; the live library cannot. None of this reaches a pptmstr pane.

## 3. The draw path, and where a figure's draw calls go

**Both languages, in sequence, into the same window draw list.**
`TypesetView.draw` (`typeset_view.py:3110-3185`):

1. lays out and draws the text natively: `doc.draw(None, (x, y), ...)`
   (`:3149`) -> imtypeset's ctypes `Document.draw` ->
   `ImTypeset_DrawDocument(handle, draw_list=None => current window's, ...)`
   (`~/Source/imtypeset/python/imtypeset/__init__.py:2689-2711`). Glyph
   vertices are written into the `ImDrawList` by C++, not by Python.
2. finds which hosted rectangles meet the clip rect (`_find_hosts`, culling at
   `typeset_view.py:3337-3343`);
3. then, **from Python**, `_draw_hosts` (`:3349-3371`) sets the cursor to each
   visible host's top-left and calls `hosted.draw(palette=, width_px=)`;
   out-of-view hosts get `handle_keys()` instead. The cursor is restored after.

So `imgui.image(texture_ref, size)` or `imgui.get_window_draw_list().add_*`
path calls for a figure are issued **inside `Hosted.draw`**, in Python, after
the text of the same view. No imscribe change is needed to issue them.

**Clipping and scroll.**
- The view culls: a host is drawn only while its rectangle meets the window's
  clip rect (`typeset_view.py:3337-3343`); scrolling is the enclosing ImGui
  window's, so a figure scrolls with the text for free.
- `_draw_hosts` pushes **no** clip rect for the host's rectangle (read: no
  `push_clip_rect` in `:3349-3371`). *Inferred*: a figure that can overdraw
  (a path whose stroke extends past its bounds, a texture drawn larger than
  the rectangle) must clip itself (`draw_list.push_clip_rect(min, max, True)`)
  or use a child window, as `TypesetDiffView` does (`begin_child`,
  `typeset_diff_view.py:2663`; `push_clip_rect`, `:2750`, `:2761`).
- Mouse over a host is the host's: "the view makes no hit test there and begins
  no selection. A drag begun in the text goes on across it"
  (`typeset_hosts.py:37-38`). A figure that pans/zooms owns its input there.
- Known defect, stated by imscribe: "text in the window is not held in place
  when a widget above the window changes height (the library estimates what it
  has not laid out; its team has that as request C2)" (`typeset_hosts.py:45-47`).
  *Inferred consequence*: a figure whose height is unknown until an
  asynchronous render finishes (Mermaid through a subprocess, say) will jump
  the text below it when it resolves. Mitigation is host-side: answer a
  stable placeholder height (from the source's declared size, or a fixed
  aspect) until the render lands, and change it at most once.

**Textures** (*inferred*, not read in this task): ImGui 1.92's
`ImGuiBackendFlags_RendererHasTextures` is mentioned by imtypeset
(`imtypeset.h:188-192`) for its own atlas; how a Python host creates and frees
a texture for a figure under imgui-bundle 1.92.900 is the `svg` task's to
establish.

## 4. Streaming

**What imscribe does with a fence still arriving** (`MarkdownStream`,
`typeset_markdown.py:2209-2260`): the tail is re-parsed up to its last newline;
a **top-level fence** is followed a line at a time and "commits the moment its
closing line ends" (`:2244-2246`). A fence inside a list item or quote is read
whole with its container while open and commits later (`:2253-2258`). Run
(probe, pieces appended one at a time):

    'Hi.\n\n```mermaid\n'  blocks 2 committed 1
    'graph TD\n'           blocks 2 committed 1
    'A-->B\n'              blocks 2 committed 1
    '```'                  blocks 2 committed 1    <- closing marks, no newline yet
    '\n'                   blocks 2 committed 2    <- committed
    'more'                 blocks 3 committed 2

A `TypesetStreamView` asks `host_for` again when a block it answered for
commits (`typeset_view.py:546-553`; `typeset_stream_view.py:415-423`:
`settled = stream.closed or block < self._live_from`), so a host learns of the
close without polling.

**Only a block it answered for.** A host that declined the open fence is
never asked again: the committed fence is the same object as its last live
form, so `_kept` keeps it and nothing re-describes it. Run on 2026-10-18 in
pptmstr's venv (imscribe 0.2.0) over pptmstr's `_Captioned`; the command,
output and the two fixes that do re-ask are in
`planning/2026-10-18-vector-displays-beside-typeset-text.md` §3.6 (probe S1).

**What pptmstr already decided for fenced diffs**, verbatim
(`pptmstr/ui/typeset/views.py:56-67`):

> "**An open stream does not host one**: whether a fence still arriving is an
> honest diff changes with each line of it, and a block that turned from code
> into a diff view and back under a reader is worse than one that is code
> until its turn settles. **A closed stream does** (``Stream._ink``)"

and in `Stream._ink` (`views.py:740-753`): `view.host_for = _fences() if
captioned.hosting else None`, with `hosting = self._closed and diffs.adopted()`.

**What a figure needs** (inferred from the above):
- Render only from a *committed* fence (or a closed stream). Until then it is
  code under its caption, as today -- a half-written Mermaid source does not
  parse, and a half-written SVG is not well-formed, so a live render would
  flicker between figure and error exactly as the diff rationale describes.
  Committed-fence is earlier than pptmstr's diff rule (closed stream) and is
  the right trigger for a figure: unlike a diff, a figure's source is fixed
  once its fence closes. Whether to take that earlier point or reuse the
  closed-stream rule is a choice for whoever builds it; the library supports
  both.
- Re-layout cost: switching a block from code to host is one
  `replace_blocks` of that block and everything after it in the view
  (`_ask_hosts` + "The block and everything after it are then described
  again", `typeset_view.py:546-553`); a later height change is one
  `set_block_extent`. Per-frame cost is one `block_geometry` per hosted block
  in view (`typeset_view.py:233-235`). Nothing here is figure-specific.
- The render itself (Mermaid layout, SVG tessellation/rasterisation) must not
  run inside `height_for`/`draw` on the frame thread if it can take more than
  a frame -- `views.py:110` "Nothing is built over more than one frame" records
  pptmstr's present stance that a text is read and laid out in the call that
  first draws it; a figure renderer may be the first thing that breaks that.

## 5. Selection, copy and find

- **Selection/copy**: a host block "is one atom, as a table is: a drag takes
  none of it above its vertical middle and all of it below, and a copy that
  takes it has its `text`, a blank line apart from the text either side"
  (`typeset_hosts.py:39-41`; `Hosted.text`, `typeset_view.py:690-692`). So
  **copy yields whatever `text` the widget answers** -- the fence's source is
  the obvious answer, and a figure host decides whether that includes the
  ```` ```mermaid ```` lines. `view.source_of(n)` / `block_source(n)` still
  name the fence verbatim (`typeset_hosts.py:42-43`).
- **Find**: "A Host block is not searched, with text or without"
  (`imscribe/typeset_find.py:49-50`, enforced at `:181-190`). A figure's
  source is therefore invisible to find once hosted. pptmstr's find
  (`pptmstr/ui/typeset/find.py`) builds on `FindIndex` and inherits that.
  Labels inside a diagram are not searchable either way unless the host
  indexes them itself.
- `selection.py` is the reference core's (section 0) and not involved.

## 6. Side-by-side

- **imscribe "columns" are not panes.** `TypesetView.set_columns(n, height=|
  balance=)` flows ONE document through newspaper columns
  (`typeset_view.py:468-540`; `imtypeset.h:1399-1421`). It cannot put "source"
  in one column and "figure" in the other.
- **Side-by-side inside a hosted widget** has a precedent:
  `TypesetDiffView` lays two sides out in its own rectangle
  (`DiffLayout.SIDE_BY_SIDE`, `typeset_diff_view.py:443`). A figure's
  `Hosted.draw` may equally draw a two-cell `imgui.begin_table` with the
  source (another `TypesetView` holding one `MarkdownCode`, or ImGui text) on
  one side and the figure on the other; its `height_for` is then the max of
  the two. *Inferred*: that is the "source | rendered" layout, entirely
  pptmstr's, at the cost that the source half is then not part of the
  document's selection (it is inside an atom).
- **"Text | figure" with prose flowing beside the figure** (a float) needs a
  layout feature imtypeset does not have (section 2). Not available.
- **Pane level**: pptmstr panes are hello_imgui docked windows
  (`MainDockSpace`, `pptmstr/ui/splash.py:6-8`); a separate "figures" pane
  beside a transcript is a pptmstr UI concern with no library involvement.

## 7. Existing mentions, verbatim, with location

No pptmstr `planning/` or `planning/archive/` file mentions Mermaid, SVG
rendering or inline figures (grep `-i 'mermaid|\bsvg\b'` over both: only
unrelated uses of "figure"). imscribe:

- The operator's words, recorded at the rename (`docs/decisions.md:1636-1638`,
  also `HANDOFF-pptmstr.md:1567-1569`, `docs/status.md:272-273`,
  `HANDOFF-imtypeset.md:514-515`): "Being evocative is the win as it allows
  future extensions that _may_ include editing or other display aspects
  (mermaid diagrams for one)."
- `HANDOFF-imtypeset.md:57-58`: "Earlier, on output that is not ImGui:
  \"Zoomable vector image may be important if we ever decide to render
  mermaid diagrams inline.\""
- `HANDOFF-imtypeset.md:188` (H14): "The lead's reading, an agent's: a reason
  not to close the door on vector output of what the core sets; not a reason
  to keep advtext's litmus renderer, which draws the reference core's layout."
- `ASSIGNMENTS.md:300-306`: "**The SVG litmus renderer** (`make svg`). ...
  Both teams recommend it leaves imscribe and becomes tests in imtypeset,
  rendered as PNG. You said vector output \"may be important if we ever decide
  to render mermaid diagrams inline\"; that renderer draws the old core's
  layout and could not draw a diagram, so keeping it does not serve that.
  Keep, or let it go?" -- unchecked, i.e. open.
- `OPEN-DECISIONS.md:16-18` (**untracked file**, `git status` shows `??`; "the
  owner's, as of 2026-10-15"): "**The SVG litmus chain** (`make svg`):
  twenty-two modules of the old core remain only for it. Keep or remove.
  Recommendation: remove; it cannot draw a mermaid diagram, which was the
  reason to keep vector output."
- `docs/decisions.md:1706`: "**The SVG litmus chain stays for 0.2.0** (the
  owner's open decision) | removing twenty-two modules is not release-day
  work".
- Images: `markdown.py:158-162` (quoted in section 1); `HANDOFF-pptmstr.md:454-461`
  and `docs/decisions.md:1431` on `images_as_text` ("**Among its least sure**").
- The remaining grep hits (`fontlib.py`, `fontfiles.py`, `primitives.py`,
  `theme.py`, `ui/palette.py`, `docs/litmus.md`, `docs/imgui-risks.md`,
  `README.md`, `docs/guide/idea.md`) are the SVG *litmus output* backend or
  SVG-in-OpenType font tables, not figures. `imscribe/backends/svg.py`
  *writes* SVG (glyph outlines via fontTools `SVGPathPen`, lines 1-40); it
  reads none.

**Reading of the record** (mine, an agent's): the operator's interest in
inline Mermaid is on record twice; the only concrete vector artefact in
imscribe (the litmus SVG writer) has been judged by both teams as *not*
serving it; nothing has been decided about how a diagram would be drawn.

## 8. Ownership, and the three pins

`pyproject.toml:20-52`: `imgui-bundle==1.92.900`, `imscribe==0.2.0`,
`imtypeset==0.4.3.192900; <marker>` -- "THREE EXACT PINS THAT ARE ONE UNIT ...
They move together or not at all". imscribe is a separate repo released to
PyPI; pptmstr consumes a release (or a ref through `make deps`, `imscribe.ref`
header).

| change | where | pin consequence |
|---|---|---|
| A `HostFor` for ```` ```svg ```` / ```` ```mermaid ```` fences, its `Hosted` widget, the renderer, caching, placeholder height | **pptmstr** (`pptmstr/ui/typeset/`), beside `_Fences` | none: `host_for`, `Hosted` and `KIND_HOST` are in imscribe 0.2.0's public facade (`__init__.py`, "views: ... a widget inside a document (`HostFor`, `Hosted`)") |
| Composing several hosts on one view (diffs AND figures) | **pptmstr**: `view.host_for` is a single callable (`typeset_view.py:1024`); pptmstr sets it in three places (`views.py:753`, `:924`, `:1076`/`:1987`) and would make it a dispatcher | none |
| A figure host in imscribe for other hosts (a `TypesetFigureFences` beside `TypesetDiffFences`) | imscribe, optional | imscribe release; imtypeset and imgui-bundle unchanged *if* it adds no native dependency |
| An image block (`![](x.svg)` alone in a paragraph -> its own block) or `ReaderOptions` for it | imscribe reader | imscribe release; and pptmstr's `_HIDING_RULES` deliberately shows images as text, so it would have to opt in |
| Host rect clipping done by the view rather than the widget | imscribe (`_draw_hosts`) | imscribe release; small |
| Text held in place when a host above changes height (imtypeset "C2") | imtypeset | all three pins |
| An inline figure in a line of text, or text flowing round a figure | imtypeset C++ + imscribe description | all three pins; largest |

**Inferred**: a renderer pulled in for SVG or Mermaid (a Python package, a
native library, or a subprocess) is a new pptmstr dependency whatever the
seam; if it ships native code built against ImGui it would join the pinned
unit, and if it only produces geometry or RGBA it would not. That is the `svg`
and `mermaid` tasks' finding to make.

## Seams, ranked by how little they disturb

1. **A pptmstr `HostFor` for figure fences, composed with `_Fences`.**
   `pptmstr/ui/typeset/views.py` (`_captioned`, `_Fences`, the three
   `view.host_for =` sites). Keys on `MarkdownCode.language` first word;
   hosts only committed fences (or only closed streams, matching the diff
   rule); `text` = fence source so copy gives it back; `height_for` from the
   figure's aspect, stable placeholder until rendered; `draw` clips to its
   own rectangle and issues `imgui.image` / draw-list paths. No imscribe or
   imtypeset change, no pin move. Costs accepted: not searchable, one atom
   to selection, full-measure block only.
2. **A "source | figure" toggle or split inside that same widget**
   (`begin_table` in `Hosted.draw`, precedent `TypesetDiffView`'s side by
   side). pptmstr only.
3. **Upstream the generic part to imscribe** as a figure-fences host beside
   `TypesetDiffFences`, and have `_draw_hosts` clip each host to its
   rectangle. One imscribe release, pins move only on imscribe's line if it
   adds no native dependency.
4. **An image block in the reader** (paragraph holding only an image ->
   block), so `![](x.svg)` could be hosted the same way. imscribe reader
   change, and it overturns pptmstr's recorded show-what-was-written rule for
   images (`gate.py:149-153`) -- needs a decision, not just code.
5. **imtypeset C2** (hold text in place when a host above grows) -- worth
   asking for if figures are common, since async renders change height.
   All three pins.
6. **Inline / floated figures** -- a new run kind or float in imtypeset.
   All three pins; not justified by anything in the brief.
