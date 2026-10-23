# Mermaid is drawn, not rasterised, and C++ is owed only where the draw loop measures short

**Dated:** 2026-10-22 ·
**Status:** a record of what was decided and built (§1, §2), and a recommendation
that is **not** built and not declared as tasks (§3 to §6), and the scope the
operator set aside for the next phase (§7), also not declared. Native Mermaid and the
raster removal are committed (`da1e0d8`, `a48ddb1`, `d206ca8`, `3480b34`), and
`make check` was green on `3480b34` (board task `native-gate-2`). No C++ has been
written for any of this. Nothing here has run on macOS, on a real GPU or at density
2 ·
**Origin:** the operator's launch premise of 2026-10-18 (Mermaid, script-free SVG,
and graphs from Claude's output, set beside or inside typeset text; see the
2026-10-18 record, §1), and the operator's calls of 2026-10-21 and 2026-10-22
paraphrased in §1. The subject of this record (native Mermaid, with C++ only where
the draw loop needs it) is the lead's rescoping after the probes, not an operator
statement ·
**Evidence:** `notes/vector-rendering/imgui-vector-survey.md` (task
`imgui-vector-survey`), `probe-bundle193.md` (`bundle-193-probe`, `bundle-193-fold`),
`reactivity.md` (`reactivity-measure`), `probe-displaylist.md` (`displaylist-proto`);
the results and notes of board tasks `native-mermaid`, `native-fix`,
`raster-removal`, `native-cost`, `text-caps` and `native-gate-2`; imscribe's
`docs/cpp-extension.md`; imtypeset's `docs/vector-output.md`, `docs/versioning.md`
and `docs/plan-0.5.0.md` at `~/Source/imtypeset` HEAD `4f887d3` ·
**Engages with:** [`2026-10-18-vector-displays-beside-typeset-text.md`](2026-10-18-vector-displays-beside-typeset-text.md)
§5 (the rejected "tessellating SVG into `ImDrawList`", which §1 below reverses for
Mermaid), §8 ("The svg fence is dropped") and §11 to §11.3; `pptmstr/figures/host.py`
(`FigureWidget.draw`, `_may_rebuild`, `MAX_DASHES_DRAWN`);
`pptmstr/figures/displaylist.py` (the bounds, `MAX_DRAWN_POINTS`); the three-pin
comment in `pyproject.toml`.

**Mermaid now draws natively in the pane from a bounded display list that the
render child builds; math is unchanged; the raw `svg` fence is code; the raster
stack is gone. What Python costs on the frame thread fits a frame for the diagrams
merman makes, except a large diagram's rebuild, which the one-rebuild-a-frame rule
spreads across frames instead of shortening. C++ would pay in two places only: the
per-frame draw (a replay of cached vertex buffers) and the rebuild (flattening and
dash cutting). Neither has been shown to need it on a real GPU yet. The
recommendation is to board the probes in §6 and write no C++ until one of them
names it. If one does, ask imtypeset for the replay as a minor-release addition,
not as a fourth pinned extension beside it. Where it lives is a request to
imtypeset's owner and is not decided here.**

How to read the attributions. They follow the 2026-10-18 record. **[ran]** means
the writer of this record executed it. The writer ran no measurement, only `git`,
`grep` and the reads below. **[op-run]** means an operator-run probe, quoted from
the note that cites its command id. **[read]** means the writer read the file this
session. **[relayed]** means a note's or a board task's claim that this record did
not re-check. The note or task is named. **[unverified]** means nobody checked it.
**[judgement]** is an opinion, labelled as one. Every timing below was taken on one
Linux machine under Xvfb with Mesa llvmpipe, or headless, unless it says otherwise.

---

## 1. What was decided, and why

**The operator's direction, paraphrased** [relayed: board task
`vector-backend-record`, specifications v2 and v3]:

- Managing rasters looked heavyweight if direct rendering was possible.
- A general C++ vector library was wanted, precisely to reach full SVG coverage:
  masks, arbitrary clip paths, gradients and group opacity, which `ImDrawList`
  alone does not give.
- A higher-level Python layer would do the parsing, sanitising and control, on
  the model of imscribe over imtypeset.
- Existing ImGui vector tooling was to be vetted first.
- After the vetting, the operator deferred that library: if Mermaid was well
  supported, raw SVG would be dropped until native rendering with interactive
  nodes is needed, since Mermaid and math already cover what sessions draw
  [relayed, lead]. The general library is therefore deferred, not open.

The vetting (the survey, the 1.93 probe, the display-list probe) changed what was
built. These are the decisions that followed.

- **The raw `svg` fence was dropped** (operator, 2026-10-22; the 2026-10-18
  record, §8). Mermaid and math cover what sessions draw. The first real SVG
  session showed that hand-laid-out SVG is the failure, whatever renders it.
  Without an SVG fence there is no untrusted general SVG to draw, so most of the
  coverage the general library existed for has nothing left to draw. The
  operator's own condition for its return is in §5.
- **Mermaid and math are what render.** Math goes through imscribe as before
  (2026-10-18 record, §8) and is untouched by everything here.
- **Mermaid draws natively from a bounded display list built in the render
  child** (operator, 2026-10-22, on the display-list probe; task
  `native-mermaid`). merman lays the diagram out, and pptmstr's sanitiser and
  parser turn its SVG into paths, text runs and hit regions. All of that runs in
  the render child, never on the GUI thread. The child answers with plain JSON;
  nothing on the pipe is pickled (lead decision; task `native-fix`). The I/O
  thread checks every bound and refuses a list over `MAX_DRAWN_POINTS` as "too
  costly to draw" before the frame thread sees it (task `native-cost`).
  - **Why not imgui-bundle 1.93's `rich_md`.** It lays out on the frame thread,
    and a 1 MB, 50,000-link chain stalled the UI for 65 s at 1.25 GB. It draws
    three of the five families. It cannot be measured before it draws. It cannot
    be hit-tested [op-run: `probe-bundle193.md` §1, §2].
  - **Why this reverses the 2026-10-18 record's §5.** §5 rejected tessellating
    SVG into `ImDrawList` for three reasons: no holes or self-intersection, our
    own triangulator and text layout, and Python calls per vertex per frame. For
    merman's output the first two did not arise: no filled path in any family
    has two loops, the golden test fails if one appears, and labels are axis-aligned
    [relayed: `native-fix` finding 13; `probe-displaylist.md` §4e]. The third is
    real and is what §2 measures.
- **Author colours are honoured, behind a contrast guard** (operator, 2026-10-22).
  Colours that a diagram's `style`, `classDef` and `linkStyle` lines set on the
  shapes they name are drawn as written. Everything else takes the theme's roles,
  and merman's own pastel palette is never the author's. Text under 4.5:1 against
  its ground, and an authored stroke under 3:1 against the pane, is moved by the
  least mix toward black or white. A fill is never changed (2026-10-18 record,
  §11, §11.1 finding 8) [relayed].
- **The raster stack was removed** (operator, 2026-10-22: it may go once native
  drawing was proven; task `raster-removal`). resvg_py, Pillow and numpy left
  `pyproject.toml`, and the texture cache and the light card went with them.
  fontTools stays, for `render.em_ratio` in the child. **There is no fallback
  renderer.** The reason: a fallback nothing exercises is a second renderer to
  keep correct by hand (2026-10-18 record, §11.2) [relayed]. One consequence
  matters for §5: the survey's plan kept the raster path for filters, masks,
  patterns and gradients (`imgui-vector-survey.md`, "What this changes", item
  7), and that safety valve no longer exists.

## 2. What the measurements say

The raster numbers are historical. That path is gone and cannot be re-measured
from the tree.

### Per frame, native against raster

| case | raster | native, Python draw | source |
|---|---|---|---|
| one figure on screen, GUI time | 0.02-0.03 ms | 0.21 ms (0.10-0.93) | [relayed: `reactivity.md`; `probe-displaylist.md` §4b] |
| 10 diagrams, draw per frame | 0.21 ms | 2.28 ms (2.12-4.65) | [relayed: `probe-displaylist.md` §4b] |
| 10 diagrams, ImGui render on llvmpipe | 0.40 ms | 3.04 ms (30,964 vertices) | same |
| 21-node graph, draw | 0.04 ms | 0.26 ms | same |
| a 100-node flowchart, after the dash fixes | — | 1.2-1.7 ms a frame | [relayed: `native-fix` finding 5] |
| theme switch | free | free (0 rebuilds in 10) | [relayed: both notes] |
| memory | w·h·4 per figure, 4× at density 2 | about the list's size; 0.8 MiB for a 100-node figure at 900 px | [relayed: `reactivity.md`; `host.LAYOUT_BUDGET_WORDS` docstring, read] |

Native drawing costs about ten times the raster per figure per frame. In absolute
terms it is well inside a 16.7 ms frame for the replies measured. About 0.8 µs of
it goes to each display item in Python [relayed: `probe-displaylist.md` §4b]. The
llvmpipe render column is CPU rasterisation and overstates what a GPU would cost
[relayed: same, "Open"]. No real GPU was used.

### Resize: what a rebuild costs

Geometry is built once per drawn scale and font. Curves are flattened, dashes cut,
fills wound and labels measured, then the result is held as `ImVec2` lists
(`host._Geometry`) [read].

- **Real diagrams build in 4-26 ms.** The 105-node flowchart, the largest merman
  accepts, takes 59 ms, and 35 of its 83 profiled ms is cutting 52 dotted edges
  into dashes [relayed: `native-cost`; 2026-10-18 record §11.3]. About 0.9 µs a
  point for a stroked curve, 1.8 µs for a fill.
- **A list that passes every receive check builds in under 200 ms** on this
  machine: 110-125 ms with the collector settled, 133-166 ms without
  [relayed: `native-cost`, `text-caps`]. Before the drawn-point budget it took
  3-4 s [relayed: `native-fix` finding 5].
- **Against the raster, resize is now sharp on the frame the width changes.**
  The raster took 7 frames for the first figure and 13 for four, with a texture
  minified up to 3.9× in between [relayed: `reactivity.md`]. The cost moved onto
  the frame thread [relayed: `probe-displaylist.md` §4d].
- **Collector pauses** of 29-127 ms during drags were set off by the builds'
  `ImVec2` allocations. They are now settled by `app._settle_gc` (freeze after
  start-up, re-settled after 3 s idle, at most every 300 s): worst gen-2 pause
  2.5 ms after a simulated hour [relayed: `native-cost`; headless, not a real
  session].

### The one-diagram-per-frame rebuild limit

When the width changes, at most one figure a frame rebuilds, the one that has
waited longest (`host._may_rebuild`) [read]. The others draw their last geometry
scaled to the new width, which is still vectors and never a stretched texture.
Since `native-fix`, a figure's first build also waits its turn and stands as an
outline at its real height until then [relayed].

- **What it bounds:** how many builds share a frame. Re-hosting six 100-node
  flowcharts went from 287 ms to a 42-68 ms worst frame. A drag's median frame
  went from 50 ms to 11-14 ms [relayed: `native-fix` finding 5]. During a 900 →
  300 → 900 px drag, the five families' figure time had a median of 3.2-3.4 ms a
  frame [relayed: `native-mermaid`].
- **What it does not bound:** the cost of the one figure. A 105-node flowchart's
  rebuild frame is still about 59 ms, and the 16 ms target is out of reach
  without refusing diagrams merman makes [relayed: `native-cost`]. With N
  figures changing width, the last is exact after N frames. Until then its
  stand-in is drawn at the wrong stroke width and at a rounded text size [read:
  `FigureWidget.draw`].

**What this leaves for C++** [judgement]. The per-frame draw is affordable today
and grows with the number of figures on screen. The rebuild is the one cost that
still breaks a frame for a real diagram. Both are the probe note's conclusion
[relayed: `probe-displaylist.md` §4e], now with the dash and collector parts
removed.

## 3. Where C++ would pay, and where it would live

### What the code does each frame now

[read: `FigureWidget.draw`] For every display item that survives the cull, Python
calls `add_polyline`, `add_convex_poly_filled`, `add_concave_poly_filled`,
`add_rect_filled` or `add_text` with the held `ImVec2` lists. One
`shade_verts_transform_pos` call then moves each run of vertices to the figure's
origin, so scrolling builds nothing. So the flattening is cached, but **ImGui
expands every stroke and fill into triangles again on every frame**, and Python
pays a call per item.

### The two things C++ would do

1. **Replay cached vertex buffers.** Build once per scale: the triangles,
   with the anti-aliasing fringe, that ImGui's own `Add*` calls would have made.
   Then each frame it is one `PrimReserve` and a memcpy-with-offset into the
   window's draw list, culled per item. This is the survey's "draw display list
   *h* at *origin*, *scale*" [relayed: `imgui-vector-survey.md`, "What this
   changes", item 5].
   - **Why Python cannot do it.** The binding writes one vertex at a time
     (`prim_vtx`, about 740 ns a point) and has no bulk write [relayed: survey
     §6; imscribe `docs/cpp-extension.md` §4, read]. So a replay has to write
     into `ImDrawList` from native code.
   - **What it removes:** the per-item Python call and ImGui's per-frame
     re-expansion. **What it does not remove:** the vertices themselves. The
     3.0 ms llvmpipe render of 31k vertices is the GPU's (here the CPU's) share,
     and a replay hands over the same count [inferred].
2. **Rebuild in C++.** Wang flattening, dash cutting, and stroke and fill
   expansion straight into the cached buffer. This is the part that turns a
   59 ms rebuild into something a frame can hold. It would also take the
   `ImVec2` churn that triggers the collector out of Python [inferred]. **How
   much faster is [unverified].** Nobody has split the 0.9-1.8 µs a point between
   arithmetic, `ImVec2` construction and call overhead (probe A, §6).

Text stays where it is. Labels are drawn with ImGui's `add_text` in the pane's
prose face (`typeset.imfont`), not through an imtypeset `Paragraph` as the survey
proposed [read: `FigureWidget.draw`; survey §5]. A C++ replay could carry glyph
quads too, but moving labels to imtypeset paragraphs is a separate question (§6,
probe D).

### What the survey ruled in and out, for Mermaid

- **No triangulator.** Every fill merman produces is simple. 11 concave fills in
  the state diagram went through `AddConcavePolyFilled` cleanly, and no fill has
  holes or self-intersects [relayed: `probe-displaylist.md` §4e; `native-fix`
  finding 13]. libtess2 (SGI-B licence; the only candidate with nonzero and
  even-odd rules and intersection handling) stays the plan for general SVG only
  [relayed: survey §4].
- **No custom stroker.** At merman's 1-2 px edges, joins and caps never visibly
  mattered, and dashes are cut in the front end [relayed:
  `probe-displaylist.md` §4e].
- **No new GPU path.** Dear ImGui takes arbitrary triangles with 32-bit indices
  in this wheel; one `PrimReserve` can carry a whole diagram [relayed: survey §1,
  ran there]. Anything that needs its own GL context, FBO or stencil was ruled
  out for fills: NanoVG fills, Skia, ThorVG's GL engine, vg-renderer [relayed:
  survey, "What this changes", item 1].
- **No text outlines.** No rotated or transformed text occurred [relayed:
  `probe-displaylist.md`].
- **What a C++ back end would own if it existed:** its own AA fringe on contours.
  ImGui's fringe on a keyhole bridge seams at 0.75 opacity, and a triangulator's
  output has none [relayed: survey §1, "What this changes" item 2]. For Mermaid
  alone, replaying what ImGui's own `Add*` calls produce sidesteps this
  [inferred].

### What imgui-bundle 1.93's strokes would add

[op-run: `probe-bundle193.md` §3] ImGui 1.93 bevels sharp joins by default: an
8 px hairpin's spike falls from 26 px to 9 px. It also forces miter on request,
extends square caps, aligns strokes inside, centre or outside, and feathers ends.
It has **no round joins, no round caps and no dashes**.

- **For Mermaid this is close to nothing.** Joins never mattered at 1-2 px.
  Dashes are the costly part of the worst rebuild, and 1.93 does not do them
  [judgement, from the rows above].
- **For general SVG** it would delete most of a stroker, but not round joins,
  round caps, dashes or `stroke-miterlimit` values [op-run].
- **The bump's cost is unchanged from the probe note** [relayed: §5 there]:
  - an imtypeset release on a new 193 line, which does not exist on PyPI;
  - an imscribe release naming the pair;
  - pptmstr's three pins and its lock;
  - re-photographing every pane, because `add_line` moves 0.5 px, `add_rect`
    strokes inside, and `stroke_legacy` does not restore 1.92.9's pixels;
  - re-running imscribe's text-snapping measurements.

  **Not a reason to bump** [judgement]. The probe note's own verdict was to
  decide the bump on the stroker alone, and Mermaid needs no stroker.

### Inside imtypeset, or beside it: the pin and release cost

Anything that writes into `ImDrawList` from native code is built against
imgui-bundle's binary. It joins the rule in `pyproject.toml` that the three exact
pins "move together or not at all" [read].

| | **inside imtypeset** (a request to that repository) | **beside it**: a pptmstr-owned extension |
|---|---|---|
| what exists to build on | `ListSink`, batched `PrimReserve`, and `draw_to_buffers` (vertices and indices into caller buffers) are already there [relayed: survey §5, read at `0bac066`] | nothing. pptmstr is pure Python today (hatchling, no extension module) [read] |
| release kind | a **minor** release (0.5.x). imtypeset's versioning rule forbids new names in a patch [read: `docs/versioning.md`]. 0.5.0 is being planned now, with owner questions open [read: `docs/plan-0.5.0.md`] | its own first release |
| what else moves | imscribe pins `imtypeset==0.4.3.192900` exactly [read: installed `METADATA`], so imscribe needs a release naming 0.5.x, or pptmstr overrides imscribe's metadata. Then pptmstr's pins, `imscribe.ref`, `imtypeset.ref` and the lock. A minor may change where text is set ("the changelog says what"), so pptmstr's typeset panes are re-checked against it [read: `docs/versioning.md`] | a **fourth** exact pin bound to the bundle binary, with wheels for Linux x86-64 and macOS arm64 times CPython 3.11-3.14, CI on both, and an import-time refusal of any other bundle, as imtypeset has [inferred from imtypeset's machinery, `probe-bundle193.md` §5] |
| on every imgui-bundle bump | rebuilt anyway, as today | a second native rebuild pptmstr owns |
| who decides | imtypeset's owner. Its own `docs/vector-output.md` asks "who draws the diagram: the application, or the core?" and leaves it open, assuming the application [read] | pptmstr |

**Recommendation, if a probe names C++** [judgement]: ask imtypeset for it, scoped
narrowly as a **request**, not a decision.

- **What to ask for:** a geometry replay, "draw these held vertex/index buffers
  at an origin and scale, culled by item boxes", fed by pptmstr's display list.
  Its own flattening and dash cutting are the second half of the request. This is
  drawing, not graph layout, so it stays on the "application lays out" side of the
  owner's question.
- **The reason for inside:** a fourth native pin doubles the release machinery
  for one consumer.
- **The cost of inside:** pptmstr's timing follows imtypeset's minor releases
  and imscribe's re-release.
- **A pure C geometry kernel that links nothing from ImGui** would avoid the ABI
  pin. It would still hand back points that Python must turn into `ImVec2`
  lists, so it helps only if arithmetic, not object construction, dominates the
  rebuild. Probe A decides whether it is worth anything [inferred].

**Requests to the imtypeset repository** (none of them made; each is a question for
its owner, gated on §6):

1. A held-buffer replay entry point into `ImDrawList`, as above.
2. Flattening, dash cutting and stroke and fill expansion into that buffer.
3. The **draw scale** its `docs/vector-output.md` already proposes ("a single
   number on the draw call"), if labels move to paragraphs (probe D).

## 4. Security does not move

Whatever C++ is added consumes only the display list that `validate()` has
already accepted on the I/O thread: frozen, bounded and plain data. It never
parses SVG, Mermaid or model text. merman and pptmstr's parser stay in the render
child, behind the JSON pipe, the 10 s timeout and the 1 GiB address-space limit
[relayed: 2026-10-18 record §11.1, §11.2]. A C++ back end would be fuzzed on the
list's own schema at its bounds (`MAX_DRAWN_POINTS`, `MAX_SEGS_TOTAL`,
`MAX_TEXT_RUNS` and the rest), which the Python tests already generate
[judgement; no fuzz harness exists].

## 5. What would bring native SVG back

The operator's condition [relayed: 2026-10-18 record §8]: a native SVG fence
returns only if interactive native rendering ever needs one. If it does, the
following has to be built, and none of it exists.

- **Interactive SVG with masks, arbitrary clip paths, gradients and group
  opacity.** `ImDrawList` has none of these.
  - Linear gradients could be approximated with per-vertex colour [inferred].
  - Masks, clip paths and group opacity need a stencil or offscreen layers.
  - With the raster stack gone, there is no fallback to send them to (§1).
  - General SVG fills also need libtess2 and a contour fringe (§3), and text
    outside the axis-aligned case needs glyph outlines.
- **GPU callbacks.** ImGui's draw-command callbacks can run renderer code inline,
  for stencil, shaders or layers. That is a native GPU path for every backend
  pptmstr runs: OpenGL3 now, and Metal on macOS if that backend is ever used.
  The survey ruled this shape out for fills because fills do not need it. For
  masks and clips it is the only inline route found [inferred: survey,
  "What this changes", item 1].
  - Whether hello_imgui's GL context has a stencil buffer: [unverified].
  - Whether pptmstr on macOS uses Metal or OpenGL: [unverified].
- **ThorVG** (MIT, v1.1.3) [relayed, delegated read in the survey; everything
  below is unverified]:
  - **Engines:** the CPU engine rasterises into a buffer, which is a raster
    again. The GL engine's tessellator and stroker write triangle buffers, and
    whether those could feed `ImDrawList` is unverified.
  - **Integration:** no ImGui integration was found.
  - **Python binding:** `thorvg-python` is LGPL-2.1.
  - **Coverage:** "most of SVG Tiny", static.
- **The lead's earlier framing**, recorded so it is checked rather than lost: the
  coverage gap closes on the GPU through `ImDrawList` callbacks, possibly with
  ThorVG as the engine, with C++ taking only a sanitised scene from the child. It
  is consistent with the evidence. It is not supported by any run. Every link in
  it above is [unverified] or [inferred].

## 6. Open questions

House rule: board the probe, and leave the fix undeclared until the measurement
names it.

### Probes

| probe | measures | decides |
|---|---|---|
| **A** rebuild split | The 105-node flowchart's and the 42-entity ER's rebuild, split into flattening, dash cutting, fill winding, `ImVec2` construction and label measuring | Whether a pure geometry kernel (no ImGui link) is enough, or the rebuild must write straight into the draw list (§3) |
| **B** real GPU, density 2, macOS | `FigureWidget.draw` and ImGui render time for 10 diagrams and the 21-node graph, on the operator's hardware, at density 1 and 2 | Whether the per-frame draw needs C++ at all. llvmpipe's 3.0 ms overstates a GPU's |
| **C** a long reply | A streamed reply carrying N diagrams (N up to the point a pane holds them), per-frame cost while scrolling and during a drag | The figure count at which the Python draw stops fitting a frame. It is the trigger for §3's first item |
| **D** labels as paragraphs | Labels drawn as imtypeset `Paragraph`s instead of `add_text`, cost per frame and the look beside prose. imtypeset's own figure is 1-2 ms a frame for 500 labels [relayed: `docs/vector-output.md`] | Whether labels join the typeset path, and so whether request 3 (draw scale) is needed |
| **E** stand-in look | Screenshots of a stand-in at the extremes of a drag (stroke width and text size wrong until rebuilt) | Whether the one-a-frame rule needs a different stand-in |
| **F** stencil and backends | Only if §5 is wanted: stencil availability in hello_imgui's GL context, and the macOS backend | Whether the GPU-callback route exists |

### Operator decisions

- **When native SVG comes back (§5).** The operator deferred the general C++
  vector library until native rendering with interactive nodes is needed (§1), and
  the evidence here agrees: Mermaid needs none of it. What would bring it back is
  the operator's call; it means a GPU path per backend.
- **The imgui-bundle 1.93 bump.** It is to be decided on the stroker alone, and
  Mermaid needs no stroker (§3).
- **What a click on a node does.** Open since §11 of the 2026-10-18 record.
- **Where C++ lives, if a probe names it.** pptmstr can choose between asking
  imtypeset and owning a fourth pin. Whether imtypeset takes geometry replay is
  imtypeset's owner's call (§3, requests 1-3).

### Found and not this record's to fix

These are relayed from the board.

- **Gantt does not draw.** Mermaid's own documentation example of a gantt chart
  is refused with "a coordinate is out of range" (`text-caps`).
- **Unbalanced Begin/End in the gate's output.** The gate's pytest output carries
  `[imgui-error]` lines for window 'needs you' ("Missing End()", "Missing
  PopID()"). They were not traced (`native-gate-2`).
- **Stale resvg comments.** `pptmstr/figures/sanitise.py` still cites resvg_py
  0.5.0 as the measurement source for rules now inert for the display list
  (`native-gate-2`, `raster-removal`).

## 7. Next phase

**The operator's call (2026-10-22), paraphrased** [relayed: board task
`next-phase-note`]: the items below are set aside for the next phase of figure
work and are not to be done now. This section records their scope, their
evidence and the question each leaves open. It decides none of them, and none is
declared as a task.

1. **C++ performance work.** Everything §2 to §6 recommends, held together:
   - **The six probes** of §6 (A to F). Their rule stands: no C++ until one names
     it.
   - **The draw loop and the resize rebuild.** The rebuild is the cost that still
     breaks a frame: up to 59 ms for merman's largest flowchart (105 nodes), and
     under 200 ms for the worst list that passes every receive check [relayed:
     `native-cost`, `text-caps`; §2]. The per-frame draw fits today and grows with
     the figure count (§2, probe C).
     *Open:* whether either needs C++, and if so where it lives (§3, inside
     imtypeset or beside it).
   - **Collector settling.** `app._settle_gc` took the 29-127 ms pauses down to a
     worst gen-2 pause of 2.5 ms, after a simulated hour, headless [relayed:
     `native-cost`]. *Open:* whether it holds in a real session. Moving the rebuild
     to C++ would also take most `ImVec2` churn out of Python (§3) [inferred].
   - **Labels through imtypeset** (probe D). *Open:* cost per frame and the look
     beside prose, and so whether imtypeset is asked for a draw scale (§3,
     request 3).
   - **The imgui-bundle 1.93 bump for its strokes** [op-run: `probe-bundle193.md`
     §3; §3 above]. *Open:* the bump itself, which §3 judges is to be decided on the
     stroker alone, and Mermaid needs no stroker. Its cost is three pins, two
     library releases and re-photographing every pane.
2. **What a click on a Mermaid node does.** The hover and hit-test are built. A
   click calls `host.node_clicked` with the figure's sha and the node's id, and
   that only logs (`_log_click`) [read]. *Open:* the action: select a board task,
   open a file, or something else. This is the operator's decision (§6; open since
   §11 of the 2026-10-18 record).
3. **The figure-preview tool.** Its design is settled: auto-approved on every rung
   (PD1), rendered in a second worker process of its own (PD2), and pipelined
   through that worker's queue rather than counted per session (PD3)
   [relayed: `notes/vector-rendering/preview-options.md`, "Decided, 2026-10-21";
   task `preview-eval`]. PV1 passed: in CLI 2.1.301 an image returned by an
   in-process `pptmstr` MCP tool reaches the model, which named a red square's
   colour and digit [relayed: `notes/vector-rendering/probe-pv1.md`, operator-run
   `cmd-20261021T204907Z-3eddeacc39e3`; task `preview-pv1`]. Per the operator, the
   tool now returns the native drawing, not a PNG. Nothing of the tool is built
   [read: no bus tool in `pptmstr/`]. *Open:*
   - **The CLI's saved copies.** The CLI writes every MCP image to
     `~/.claude/projects/<cwd>/<session>/tool-results/*.png`, outside pptmstr's
     cleanup, and tells the model the path. A model with `Read` can then reopen it
     outside the tool's bound [relayed: `preview-pv1`, finding 2 and builder-41's
     note]. Whether the CLI ever prunes that directory was not checked.
   - **A transcript placeholder for image blocks.** `transcript.cap_output` keeps
     the `repr` of the result, base64 included, up to 1 MiB in
     `ToolResult.output`. A real preview would put tens of KB into every tool
     entry unless an image block is replaced by a placeholder [relayed:
     `preview-pv1`, finding 3].
   - **How the native drawing reaches the model as an image** [judgement: a gap
     this record found, not a question the operator posed]. An MCP image block
     carries encoded bytes, and the raster stack that would have made them is
     removed with no fallback (§1). The design record's §10.6 in the 2026-10-18
     record still describes a PNG from the render child, and the
     fence text still tells sessions not to preview with other tools.
4. **App-wide paragraph spacing.** Separate paragraphs sit about 5 px apart, so two
   paragraphs read almost as consecutive lines [relayed: builder-53's note on
   task `math-spacing`, from `shots/math-size` and `shots/math-spacing`]. That note
   attributes the gap to imscribe's default theme, PARAGRAPH `space_below` of 0.55
   × body. The tree does not agree: pptmstr's own theme overrides the paragraph's
   `space_below` to `round(em * _PARAGRAPH_GAP_EM)`, with `_PARAGRAPH_GAP_EM =
   0.45`, in `pptmstr/ui/typeset/themes.py` (`_build`) [read]. *Open:* which
   theme the screenshots were set in, and so whether the knob is pptmstr's
   constant or imscribe's default. Then the gap wanted. Any change moves every
   typeset pane, not only figures.
5. **The "needs you" panel's ImGui errors.** The gate's pytest output carries
   `[imgui-error]` lines for window 'needs you', "Missing End()" and "Missing
   PopID()", after the test summary [relayed: `native-gate-2`]. *Open:* the cause.
   Nobody has traced it.
6. **Gantt never draws.** Mermaid's own documentation example of a gantt chart is
   refused in the render child with "mermaid not drawable: a coordinate is out of
   range", and so was every generated gantt tried, on merman 0.9.1 [relayed:
   `text-caps`]. *Open:* the cause. It may be merman's layout or pptmstr's
   coordinate bound, and nobody has investigated.
7. **The formula-size reading.** A display formula is set at 2x by default and
   steps down to fit, with 1x as the floor [relayed: `math-size`]. The operator's
   words, "twice as large as a floor", can also mean 2x as the minimum
   [relayed: builder-52's note on `math-size`]. *Open:* which the operator meant.
   A minimum of 2x would make a formula wider than the pane scroll further than
   it does today, which `math-size`'s must-not-break line forbade. Per that note,
   the change would be to `FORMULA_SCALE` and the lower clamp in
   `views.formula_scale`.
8. **The source/figure toggle.** The original plan's side-by-side source and figure
   (the 2026-10-18 record, §4, row 2: a two-cell table inside `Hosted.draw` behind a
   toggle) was never built and is not declared [read: that record, §4 and §8].
   *Open:* whether it is still wanted now that figures draw natively, and the
   selection limit §4 names: the source half is one atom to a drag.
9. **`displaylist._Sheet` splits `<style>` rules without respecting quotes.** A
   `stroke-width` hidden inside a quoted value still scales markers (builder-56,
   probe, task `colour-grammar`). It is inert as a bypass, since the sanitiser does
   not restrict `stroke-width`, and only feeds marker scale. One depth-tracking
   splitter for both readers would close it, but would also start reading nested
   blocks such as `@media` that are ignored today. *Open:* whether to read nested
   blocks at all.
