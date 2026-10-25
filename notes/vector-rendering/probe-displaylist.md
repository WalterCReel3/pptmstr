# Probe: Mermaid drawn natively from a bounded display list

Board task `displaylist-proto` (specification v3), 2026-10-22. This is the first
step of native Mermaid: the diagram is drawn in the pane from merman's layout,
with no raster. Nothing in `pptmstr/` was changed. The probe replaces
`figures.host.figure_for` in its own process only, so a mermaid fence in a pane
marked native is drawn by `NativeFigure`, and one in any other pane is drawn by
today's `FigureWidget`.

How each claim is tagged:

- **[ran]**: executed here; the command is under "Reproduce".
- **[read]**: read from source or the installed package. Nothing was executed.
- **[relayed]**: a number taken from another note, named where it is used.
- **[inferred]**: reasoning from the above.

## Summary

1. **merman exposes layout, but not enough of it to draw from** [ran].
   - `Merman.layout_json` gives node boxes, edge points, edge-label boxes and
     cluster boxes for all five families. `edge_geometry_json` adds edge
     polylines for flowcharts only (the other families return `"edges": []`).
   - Neither gives shapes (diamond, cylinder, rounded state), text runs, markers,
     or the sequence diagram's lifelines, frames and notes. Drawing from them
     would mean writing merman's SVG renderer again, once per family.
   - **Source chosen:** the resvg-safe SVG pptmstr already makes
     (`mermaid.layout`: directives and config out, `visible_labels`, `sanitise`),
     parsed in the child into the display list. The child returns the list; the
     GUI process never sees SVG.
2. **Fidelity is close to the raster on all five families and on the 21-node
   graph, in both themes, with no card** [ran; screenshots read].
   - Every node, edge, marker, label, cluster, note and frame of the raster
     appears natively.
   - Hit regions cover all of it: 42 of 42 board edges, and every edge of the
     five families, attach to a node at both ends.
   - The residual defects are listed under "Fidelity".
3. **Per-frame cost from Python is about ten times the raster's** [ran]:

   | case | native, Python draw | raster |
   |---|---|---|
   | 10 diagrams | 0.21 ms per figure, 2.3 ms per frame | 0.02 ms per figure, 0.21 ms per frame |
   | 21-node graph | 0.26 ms | 0.04 ms |

   ImGui's render of 31k vertices adds 3.0 ms on llvmpipe, against 0.4 ms for
   the rasters. Both fit a 16.7 ms frame. This is the number a C++ draw loop
   would remove.
4. **Height is known on the frame the child answers, and does not change after
   that** [ran]. There is one jump, from `PLACEHOLDER_HEIGHT` (120) to the real
   height, 2–4 frames after commit, which is today's first jump. The raster's
   later image arrival is gone: geometry is drawn on the same frame the height
   arrives.
5. **Resize is sharp on every frame, and a theme switch is free** [ran]:
   - **Resize:** 0 frames not sharp across six drags, against reactivity.md's 7
     frames for the first figure and 13 for four [relayed]. The price is a
     geometry rebuild on each frame the width changes: 3–16 ms for five figures
     in Python.
   - **Theme switch:** 0 rebuilds and 0 frames without geometry in 10 switches.
     Colours are roles looked up each frame.
6. **What still needs C++** [inferred from the runs]: the per-frame draw loop,
   and the rebuild on resize. No triangulator was needed: none of the six
   diagrams had a self-intersecting fill or a fill with holes.
7. **Recommendation: native Mermaid can replace the raster path for these five
   families.** It would do so with four open items, set out under
   "Recommendation":
   - the author-colour decision;
   - the badge and cylinder defects;
   - a golden test pinned to the merman version;
   - the draw and rebuild loops moved to C++ before large replies stream.

## 1. merman 0.9.1's layout output

[read] `Merman` (and `MermanEngine`) in `.venv/.../merman/merman_uniffi.py` has:
`layout_json`, `edge_geometry_json`, `svg_plan_json`, `parse_json`,
`analysis_facts_json`, `render_svg`, and the raster and ASCII renderers.

[ran] What each returns, per family (`$TMPDIR` dump, then read):

| | flowchart | sequence | class | state | ER |
|---|---|---|---|---|---|
| `layout_json` top key | `FlowchartV2` | `SequenceDiagram` | `ClassDiagramV2` | `StateDiagramV2` | `ErDiagram` |
| nodes: `id x y width height is_cluster` | 10 | 9 | 4 | 11 | 4 |
| edges: `from to points label start/end_marker stroke_dasharray` | 9 | 12 | 3 | 11 | 3 |
| clusters: box, title, `title_label` box | 1 | 0 | 0 | 2 | (no key) |
| `edge_geometry_json` edges | 9 polylines | `[]` | `[]` | `[]` | `[]` |
| time, `layout_json` | 5.8 ms | 1.9 | 2.1 | 4.3 | 2.4 |

What is missing:

- The node's **shape** is only in `semantic.nodes[].shape`.
- **Label text** is only in `semantic`, per family.
- Class members, ER attribute rows, sequence lifelines, `alt` frames, notes and
  autonumbers have no geometry at all.
- Coordinates do not equal the SVG's: node `A` is at x 309.6 in `layout_json`,
  and its edge leaves at x 287.6 in the SVG.

So `layout_json` is a hit-test source, not a drawing source.

[ran] The resvg-safe SVG carries everything the parser needs, through stable
hooks:

- `g.node[id]`;
- `g[data-et=participant][data-id]`;
- `path[data-edge][data-id]` and `line.messageLine0/1`;
- `g.edgeLabel g.label[data-id]`;
- `g.cluster[id]`;
- `marker-start`/`marker-end`;
- labels as `<use href="#merman-foreignobject-label-N">` into `<defs>`.

**Layout stays in the render child.** `child_layout` runs `mermaid.layout`,
then `from_svg`, in a spawned process. `mermaid.layout` refuses anywhere else.

## 2. The display list and its bounds

The definitions are in `scripts/displaylist_proto.py`, under "The schema and
its bounds". It is plain frozen dataclasses, pickled across the pipe.

- **`PathItem`**:
  - `role`, `fill`, `fill_rule` (nonzero or even-odd), `stroke`, `width`,
    `join`, `cap` and `dash`;
  - `ops`: one character per segment, `M L C Q Z`;
  - `xy`: absolute coordinates in diagram units;
  - `bbox`, plus `node` and `edge`, the owner indices used for highlight.
  - Arcs, `H/V`, `S/T` and every shape element (rect with `rx`, circle,
    ellipse, line, polyline, polygon) are normalised to these five ops in the
    child.
- **`TextRun`**:
  - `text`, `x`, `y` and `size` (an em, as SVG means it);
  - `anchor` (start, middle or end) and `baseline` (alphabetic or central);
  - `role`, `bold`;
  - `box`: the width merman laid the label out in;
  - owners, as above.
  - Axis-aligned only. A run under rotation or skew is dropped and counted;
    none occurred.
- **`HitNode`**: `id`, `box`, and up to 16 `attach` boxes (a participant's
  lifeline, for example).
- **`HitEdge`**: `id`, a `points` polyline, and `ends`, the node indices at
  each end, or -1.
- **Roles:** node, cluster, edge, marker, label, edge_label, note, frame,
  lifeline, solid, badge_text and hollow.
  - The theme maps each role to a fill and a stroke (`inks`), once per
    palette.
  - The SVG's own colours are never used.

**Bounds.** Every count and number has one. Past a bound the diagram is
refused, except where noted:

| bound | value |
|---|---|
| natural width, height | (0, 16 384], the sanitiser's `MAX_SIDE` |
| any coordinate after transforms | [−16 384, 32 768], finite |
| display items (paths + runs, marker instances included) | 10 000 |
| segments per path / per diagram | 4 096 / 100 000 |
| subpaths per path | 256 |
| text runs / characters per diagram | 5 000 / 200 000 |
| characters per run | 512: **cut with an ellipsis**, not refused |
| font size | [1, 256] em px, clamped |
| stroke width | [0, 64], clamped |
| dash entries / each entry | ≤ 8 / [0.5, 4 096] |
| hit nodes / edges | 2 000 / 5 000 |
| points per edge polyline | 256, decimated keeping both ends |
| id length | 128, cut |
| `<use>`/marker nesting | 4 |
| GUI: curve steps / points per item / dash pieces per path | 64 / 16 384 / 2 048 (past that, solid) |

`validate` checks them all again in the GUI process: 0.06–0.5 ms per diagram
[ran]. The self-test shows a refusal for an out-of-range coordinate and for
2 001 nodes, a cut for a 5 000-character run, and `validate` catching a
999-wide stroke [ran].

**What actually bounds a diagram today is upstream of these** [ran, `dump`].
merman's own resource limits and the sanitiser's size cap refused every large
input before the display list saw it:

| input | outcome | child time |
|---|---|---|
| chain-100 | 402 items, 1 105 segments, 168×8 561 | 151 ms |
| chain-200 | refused: natural size 168×17 061 over the cap | 217 ms |
| chain-400, chain-500 | merman: `svg_fallback_selector_index` exceeded | 315–362 ms |
| chain-2000, fan-400, fan-1000, dense-100x250 | merman: `max_layout_work_units` exceeded | 9–63 ms |
| fan-200 | refused: 2 340×16 976 over the cap | 285 ms |
| dense-60x120 | 358 items, 2 677 segments, 3 553×2 846 | 174 ms |
| a 20 000-character label | refused: 168×28 139 over the cap | 192 ms |

So the largest diagram that reached the display list in this run had about
400 items. The schema's bounds sit an order of magnitude above that, as a
second line.

## 3. Drawing it

`NativeFigure` subclasses `host.FigureWidget`, keeping the same `height_for` and
`draw(palette, width_px)` surface, so imscribe hosts it like any figure.

- **Geometry cache, per (scale, font):** curves are flattened by Wang's bound at
  0.25 px. Each fill and stroke becomes a cached `ImVec2` list in pixels from
  the figure's corner. Dashes are split here. Fills go through
  `add_convex_poly_filled`, or `add_concave_poly_filled` when not convex
  (11 concave fills in the state diagram, 0 elsewhere). Winding is set
  clockwise for ImGui's fringe.
- **Per frame:**
  1. Cull every item by its box against the draw list's clip rect, intersected
     with the block.
  2. Draw each geometry run at (0, 0).
  3. Move it to the figure's origin with
     `imgui.internal.shade_verts_transform_pos` (cos 1, sin 0): one C++ pass
     over the run's vertices. The cached lists therefore survive scrolling.
     Text is drawn in screen space, because `AddText` culls lines against the
     clip rect at the position it is given.
- **Labels:**
  - **Face and size:** the pane's prose face, `typeset.imfont(REGULAR/BOLD)`,
    at `size × scale / em_ratio`. `em_ratio` is
    `imscribe.em_size_for_pixel_height(PROSE, 1)` = 0.853: SVG's `font-size`
    is an em, and ImGui sizes by ascent plus descent.
  - **Rounding:** sizes are rounded to whole pixels and positions snapped.
  - **Overflow:** a label may run 15% past merman's box before it is set
    smaller.
- **Hover:** an `invisible_button` over the figure. The smallest hit box under
  the pointer wins (a node over its cluster). Its strokes, and every edge with
  it at an end (with that edge's markers), are drawn in `theme.P.focus` at
  +1.5 px.
- **Click:** `is_item_clicked` on a hovered node calls
  `on_node_click(sha, node_id)`, a stub that records and prints.

Four API facts found by running, each of which cost a failed run [ran]:

- **`ImDrawFlags_Closed` is `1 << 9` in 1.92.9, not 1** (the stub says so since
  1.92.8). A literal 1 asserts "Did you swap 'thickness' and 'flags'?". Use
  `imgui.ImDrawFlags_.closed.value`.
- **`add_polyline` is `(points, col, thickness, flags)`** in this binding.
- **`ImFont.calc_text_size_a` is not bound.** Measuring goes through
  `push_font(face, size)` and `calc_text_size`.
- **`typeset.imfont()` returns a new Python wrapper on every call.** Keying
  the cache on `id(font)` rebuilt the geometry on every frame; `font.font_id`
  is stable.

## 4. Measurements

**Setup:**

- **Machine and run:** `XVFB_SHOT_SCREEN=1600x1400x24`, Mesa llvmpipe, on the
  same machine and harness shape as reactivity.md. Real host, real render
  child for the raster column, `typeset.markdown`, a 1590×1390 window, density
  1.
- **Display-list child:** the probe's own spawned child
  (`ProcessPoolExecutor`), not pptmstr's render child.
- **Samples:** steady frames are 140 after a 10-frame settle.
- **What "native draw" times:** `NativeFigure.draw` (cull, Python calls,
  vertex move). "Render" is from the end of the GUI pass to `before_swap`.

### (a) Fidelity, side by side with today's raster

Shots: `shots/displaylist/fid-{flowchart,sequence,class,state,er,board21}-{dark,light}.png`,
with the raster on the left and native on the right. Read for this note
[ran]:

- **Everything structural matches**:
  - **flowchart:** diamond, cylinder, stadium, subgraph with its title, edge
    labels with a knockout, rounded corners on orthogonal edges, arrowheads;
  - **sequence:** participants top and bottom, lifelines, solid and dashed
    messages with arrowheads, the `alt` frame and its divider and label tab,
    the note;
  - **class:** compartments, «abstract», bold names, multiplicities, a hollow
    aggregation diamond, hollow inheritance triangles;
  - **state:** start and end states, the composite `Running` with its title
    bar, the dashed note edge, the note;
  - **ER:** attribute tables and crow's-foot markers (open, as merman draws
    them);
  - **board21:** 21 nodes and 42 edges with merman's line hops.
- **Better than the raster:**
  - no light card in the dark theme;
  - crisp at every width;
  - labels in the pane's own face.
- **Different by decision:** colours are the theme's roles. merman's per-node
  pastel palette (pink, teal and orange participants and entities) is not
  drawn. **A diagram's own `style`/`classDef`/`linkStyle` colours are dropped
  too** (`mermaid.py` treats them as content). Whether to honour them is the
  operator's call; see "Open".
- **Defects left in:**
  - **Sequence autonumber badges are about half the raster's size**, and their
    numbers, drawn in the background ink, are hard to read. The marker sits on
    a line with `stroke-width="0"` under `markerUnits=strokeWidth`. The probe
    draws it at unit scale; resvg draws it larger. Why was not found.
  - **The flowchart cylinder's body is not filled** (outline only).
  - **Labels run past merman's boxes:**

    | | flowchart | class | state | ER | board21 | sequence |
    |---|---|---|---|---|---|---|
    | overflow | 4 | 14 | 7 | 28 | 1 | 0 |
    | set smaller | 1 | 2 | 2 | 5 | 1 | 0 |

    "Set smaller" is past the 15% allowance. merman measures with its own
    metrics; the raster lets labels overflow as well (`visible_labels`).
  - **Overlapping edge labels overlap**, as they do in the raster (state
    `pause()`/`resume()`).

### (b) Per-frame cost

| case | GUI ms | native draw ms / frame | per figure | vertices | ImGui render ms | frame ms |
|---|---|---|---|---|---|---|
| K=0, empty panes | 0.21 | — | — | — | 0.16 | 17.1 |
| **10 diagrams, raster** (5 columns × 2) | 0.82 | 0.21 (raster draw) | 0.02 | — | 0.40 | 27.9 |
| **10 diagrams, native** | 2.99 (2.75–5.92) | **2.28** (2.12–4.65) | **0.21** (0.10–0.93) | 30 964 | **3.04** | 36.1 |
| **board21, raster** | 0.28 | 0.04 | 0.04 | — | 0.21 | 25.8 |
| **board21, native** | 0.51 | **0.26** (0.23–0.47) | 0.26 | 4 928 | 0.84 | 23.2 |

[ran]: medians, with min–max where given. The 10-diagram native case draws
2 886 items, about 0.8 µs per item in Python. reactivity.md's bar is
0.03 ms GUI and 0.02 ms render per figure [relayed]. The raster column here
reproduced it (0.02 ms).

**Culling** [ran]: board21 in a 420 px pane, scrolled to top, middle and
bottom:

| scroll | items drawn of 147 | vertices | ms |
|---|---|---|---|
| top | 63 | 2 860 | 0.19 |
| middle | 96 | 4 074 | 0.20 |
| bottom | 94 | 3 320 | 0.15 |

`cull-board21-mid.png` shows only the visible band. The box test itself is
still a Python pass over every item.

### (c) Height before the first draw

Three trials with a fresh flowchart each, text above and below [ran]:

| | trial 1 | trial 2 | trial 3 |
|---|---|---|---|
| height, frame 0 | 120 (placeholder) | 120 | 120 |
| frame it became 881 | 4 | 3 | 3 |
| first frame with geometry | the same frame | the same frame | the same frame |
| child round trip | 95 ms | 80 ms | 72 ms |
| height changes after that | none | none | none |

**One jump remains, placeholder to real, as today.** The raster path's
further wait for the image (147 ms warm for this flowchart in reactivity.md
[relayed]) is gone. Removing the jump itself would need either holding the
fence until its layout arrives or a synchronous layout. Neither was tried.

### (d) Resize and theme switch

Resize: one pane holding the five families, dragged 900→300→760→900 px, in one
frame and over 30 frames [ran]:

| drag | frames not sharp | geometry rebuilds | native ms on rebuild frames (median, max) |
|---|---|---|---|
| 1-frame | 0, 0, 0 | 1 each | 9.6, 7.9, 3.0 |
| 30-frame | 0, 0, 0 | 30 each | 3.4 (max 14.8), 6.3 (16.3), 3.2 (6.4) |

Against reactivity.md [relayed]:

- **First figure sharp:** 7 frames (123 ms) today; here, the frame the width
  changed.
- **Four figures sharp:** 13 frames today; here, the same frame.
- **Drawn meanwhile:** today, a texture minified up to 3.9×; here, nothing
  stale.

The cost moved onto the frame thread: **3–16 ms per drag frame for five
diagrams**, in Python flattening and `ImVec2` building. On a 60 Hz display
that drops frames during a drag. `resize-300-native.png` is the 300 px state.

**Theme:** 10 switches [ran].

- **Rebuilds:** 0.
- **Frames without geometry:** 0.
- **Native draw on the six frames after each switch:** 0.8–2.3 ms, the same
  as steady state.
- **`apply_style`:** 0.1–0.2 ms.

`theme-five-{dark,light}.png` and `hover-board21-{dark,light}.png` show both
themes. Today's theme switch is free as well [relayed]; the difference is that
native figures change with the theme instead of standing on a fixed card.

**Hover and click** [ran]: the pointer was put on board21's node of highest
degree, `flowchart-t3-3` (`bus-graph-tools`, 9 edges).

- **Hover:** `hovered` reported it, with 9 edges lit.
- **Click:** recorded `(figure sha, "flowchart-t3-3")`.
- **Shots:** `hover-board21-dark.png` and `-light.png` show the node and its 9
  edges and arrowheads in the focus colour.

### (e) What still needs C++

[inferred from the measurements above]:

1. **The per-frame draw loop.** It costs 0.21–0.26 ms per diagram in Python.
   That is under budget for ten diagrams, but it is ten times the raster's,
   and it grows with what is on screen. Of the survey's plan, this is the
   loop: "draw display list *h* at *origin*, *scale*". The cache plus
   `shade_verts_transform_pos` shape shown here is exactly what C++ would own.
2. **The rebuild on scale change.** At 3–16 ms per frame for five diagrams in
   Python, it is the one measured cost that breaks a frame. Without C++, the
   stopgaps are:
   - rebuild at most one figure per frame;
   - or keep the old geometry and rebuild only once the width settles. That
     reintroduces staleness, though vector and not a minified texture.
3. **Not needed for these five families:**
   - **A triangulator.** No fill had holes or self-intersections; 11 simple
     concave fills went through `AddConcavePolyFilled` cleanly.
   - **A stroker.** Joins and caps never visibly mattered at merman's 1–2 px
     edges, and dashes split fine in the front end.
   - Both stay open for general SVG and for graphs (the survey's plan stands).
4. **Not needed: text outlines.** No rotated or transformed text occurred
   (`dropped: {}` in all six).

## Recommendation

**Native Mermaid can replace the raster path for flowchart, sequence, class,
state and ER.** The run shows:

- the same structure as the raster;
- no card, and theme-following;
- sharp at every width with no debounce;
- one height jump instead of a jump plus a late image;
- hit-testing on every node and edge;
- bounded by merman and the sanitiser before the display list is reached.

The cost is CPU on the frame thread: about 0.2 ms per diagram per frame, and
3–16 ms per resize frame, in Python.

Before it replaces the raster stack:

1. **Operator decision: author colours.** Roles only, as probed, or honour
   `style`/`classDef`/`linkStyle` fills (mapped toward the theme or taken as
   written). The parser would have to read those colours; today it is
   deliberately CSS-blind.
2. **Fix the two drawing defects**: the badge scale and the cylinder fill.
3. **Pin it with a golden test.** The parser keys on merman 0.9.1's class
   names (`messageLine1`, `edge-pattern-dashed`, `actor-line`, `aggregation`,
   ...) and on its stylesheet's dash and marker rules, read here from its CSS.
   A merman upgrade can move them silently. The test: the six diagrams'
   display-list counts per role, and edge attachment.
4. **Move the draw loop and the rebuild into C++**, as the survey planned,
   before long replies stream many diagrams. Or, as a Python stopgap, cap
   rebuilds at one figure per frame.
5. **Integrate:**
   - `from_svg` runs in `render.child_main` after `mermaid.layout`;
   - the raster request for mermaid goes away;
   - `FigureWidget` becomes the display-list widget for mermaid. The raw `svg`
     fence is being dropped separately (`drop-svg-fence`).

## Open, and not run

- **Density 2** (Retina) was not run. Pixel-snapping and font rounding are in
  pixels, so they should hold [inferred]; vertex counts do not change with
  density.
- **A real GPU** was not used. Per-vertex render cost on llvmpipe (3.0 ms for
  31k vertices) is CPU rasterisation and overstates a GPU's.
- **Cold start:** the display-list child started cold for the first fidelity
  case only. Cold and warm were not separated as reactivity.md did. `dump` gives
  warm round trips of 12–48 ms for the six diagrams [ran].
- **Streaming:** a fence growing token by token was not tried. Every fence here
  was committed whole.
- **Scrolling:** the cost of the origin moving every frame was exercised only
  through `cull`; `shade_verts_transform_pos` makes it a vertex pass.
- **Not compared:** `rich_md`, beyond what `probe-bundle193.md` already says.
- **`ATTACH_SLOP`:** 24 units is tuned on these diagrams. merman stops a class
  diagram's edge 17 units short of its node, for the marker. A denser graph
  could attach an edge end to the wrong node.

## Reproduce

    .venv/bin/python -B scripts/displaylist_proto.py selftest
    .venv/bin/python -B scripts/displaylist_proto.py dump      # writes shots/displaylist/dump.json
    XVFB_SHOT_SCREEN=1600x1400x24 scripts/xvfb_shot.sh -- \
        .venv/bin/python -B scripts/displaylist_proto.py run   # writes results.json and the PNGs

All three were run on the final script, exit 0. The window run took 1 470
frames, with no ImGui assertion and no exception in its log. `/shots/` is
gitignored, so the PNGs and JSON are not in the tree.
