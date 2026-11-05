# Probe: imgui-bundle 1.93.0, rich_md's Mermaid and ImGui 1.93 stroking

Board task `bundle-193-probe`, 2026-10-21; operator-run results folded in by
`bundle-193-fold`, the same day.

Each finding is tagged:

- **[ran]**: executed here, with the command named under "Reproduce".
- **[op-run]**: executed by the operator outside the sandbox (command below);
  the numbers were then read here from its output files and screenshots.
  Nothing tagged [op-run] was re-executed by an agent.
- **[read]**: read from source or docs, with the source named. Nothing was executed.
- **[unanswered]**: the run was meant to answer it and did not. Each says why.

This worktree's `.venv` was not changed. The only use of it was to run the
probe with `python -B` (no bytecode written) against the installed 1.92.900, to
check that the script works before the operator runs it.

## Status of the run

The sandbox has no network. `scripts/bundle193_run.sh` creates two scratch
venvs under `/tmp/claude-1000/b193`: imgui-bundle 1.93.0 and 1.92.900, each
with numpy. It then runs `scripts/bundle193_probe.py` in both, headless and
under `xvfb-run`. It was handed to the operator as command
`cmd-20261021T211159Z-5539246d0379`.

**The operator ran it on 2026-10-21** (board task `bundle-193-fold`). Its
outputs, under `/tmp/claude-1000/b193/`:

- `out-v193.jsonl`: 121 `@@` lines, imgui-bundle 1.93.0 / ImGui 1.93.0.
- `out-v192.jsonl`: 36 lines, imgui-bundle 1.92.900 / ImGui 1.92.9.
- `mermaid.jsonl`: 50 lines, 1.93.0, one child process per case.
- `out-v193/` (strokes, 8 `mermaid-*` and 12 `hostile-*` PNGs) and `out-v192/`
  (strokes only; 1.92.900 has no rich_md).

None of the output contains a crash, a signal or a timeout. **Eight Mermaid
cases did not measure what they were meant to**: they exited with
`RuntimeError: IM_ASSERT( _Renderer() && "RichMd: call InitializeMarkdown
first" )` (`rich_md.cpp:2357`). That is a probe defect with a real finding
behind it; see "Robustness" and §2.

What ran earlier, on 1.92.900 from this worktree's `.venv`:

- `bundle193_probe.py all`, headless.
- `bundle193_probe.py shots` under `scripts/with_display.sh`.
- The Mermaid case runner, with a stand-in `rich_md` that draws text, under
  `with_display.sh`. This shows that the harness works. It says nothing about
  rich_md.

## 1. rich_md's Mermaid

### Diagram families

| Family (pptmstr's five) | rich_md | Basis |
|---|---|---|
| flowchart (`flowchart`/`graph`, TD TB BT LR RL) | Drawn | [read] docs/mermaid.md |
| sequence | Drawn. Any of `box`, `create`, `destroy` or participant types turns the whole diagram into code. | [read] docs/mermaid.md |
| class (`classDiagram`, `-v2` read as the base) | Drawn. `click`, `callback`, `link` and styling are ignored. | [read] docs/mermaid.md |
| **state** | **Not drawn: shown as code** | [read] docs/mermaid.md ("Everything else … shown as code") |
| **ER** | **Not drawn: shown as code** | [read] same |

So **two of the five families pptmstr's probes use fall back to a code block.**
Gantt, pie, gitGraph, mindmap and timeline also fall back.

[op-run] Consistent with that: `d:flowchart`, `d:sequence`, `d:class` and
`d:board21` drew; `d:state`, `d:er` and `h:unsupported-pie` raised the
`InitializeMarkdown` assert, which is on the code-block path (the drawn cases
never called `initialize_markdown` and did not raise). The installed
`render_mermaid` docstring says the same: "flowchart, sequence or class
diagram".

### Mermaid subset

- **Flowchart** [read]:
  - Shapes: 13 classic shapes.
  - Links: solid, dotted, thick and invisible, with `o`/`x` ends, both
    directions and labels.
  - Chains, `&` groups, and nested subgraphs with `direction`.
  - Not supported: the `A@{ shape: … }` syntax, edge ids, icons, link lengths.
  - Edges are orthogonal polylines, never curves.
- **Everywhere** [read]:
  - Parsed and ignored: `classDef`, `class`, `style`, `linkStyle`, `:::name`
    and `%%{init}%%`.
  - Labels are plain text: `<br>` and entities are decoded, while markdown and
    HTML are shown literally.
  - The renderer's own docs say this is a subset "for small and medium
    diagrams" (`docs/api.md`).

### Layout quality on the six diagrams

The six are the five from `scripts/figure_render_probe.py` and the 21-node
board graph. State and ER did not draw (above), so four were looked at.

[op-run] What the screenshots show (`out-v193/mermaid-*.png`, 15 px font,
dark style):

| Diagram | Item size (px) | What is drawn |
|---|---|---|
| flowchart | 564 × 594 | Every node and shape: rectangles, diamond, cylinder, stadium, a titled subgraph. Edge labels "Yes"/"No" sit on the edges. The loop-back edge routes round the right. Orthogonal edges, small arrowheads. |
| sequence | 771 × 442 | Four participants, top and bottom. `autonumber` circles 1 to 8, an `alt` frame with its `else` divider and guards, a note, dashed replies. Nothing missing that the source asks for. |
| class | 296 × 420 | Compartments, `«abstract»`, aggregation diamond with `1`/`*` multiplicities and the `owns` label, two inheritance triangles. |
| board21 | 1152 × 765 | All 21 nodes in 7 ranks and all 42 edges. Edges are routed into long orthogonal tracks, many down the right-hand side; about ten arrive side by side at `graph-gate`. Every edge is there; following one by eye is hard. |

Against merman's raster output (`shots/mermaid-window/3-drawn.png`,
`shots/fence-backdrop/*.png`). These shots are of **smaller, different
sources**, so this is a comparison of character, not of the same diagram:

- merman draws Mermaid's own look: curved edges, a large diamond, coloured
  participant boxes, label backgrounds. rich_md draws a compact, monochrome,
  orthogonal version in the ImGui style. Neither is wrong; only merman looks
  like what mermaid.js would show.
- On pptmstr's dark pane merman's sequence labels are dark ink on dark
  (`3-drawn.png`) and need the light backdrop (`fence-backdrop`). rich_md needs
  nothing (see "Theming").
- rich_md is crisp at any font size because it is geometry and glyphs; merman's
  is a texture at one scale.
- No merman render of board21 exists to compare with. **[unanswered]**: board21
  through merman, which is the comparison that matters for the dense case.

**The 21-node graph was reconstructed exactly.** Session 7b792e36 drew board
`399eb899` (`feat-graph-tooling`) by hand. Its call at transcript line 101
printed every task and its `depends_on`: 21 tasks and 42 edges, matching
§10.1 of the planning record. They are in the probe as `BOARD_DEPS`, and the
probe renders them as a `flowchart TD` with dependency `-->` dependent. The
board directory itself is outside this sandbox's read set. The transcript copy
is the source.

What the layout is [read, `mermaid_layout.cpp`]:

- **Algorithm:** Sugiyama-style.
  - Back edges are found by DFS and reversed.
  - Rank is the longest path from a source.
  - Barycentre ordering runs up to 8 sweeps and keeps the one with the fewest
    crossings.
  - Edges are routed into tracks.
- **Crossing count:** counted by brute force over node pairs × their
  predecessor pairs.
- **Text:** `ImGui::CalcTextSize` sizes every box with the font current when the
  diagram is drawn.
- **Edge cases:** subgraphs with no outside links are laid out in the flipped
  direction, as Mermaid does. Dense graphs "may have more edge crossings"
  (docs).

### Theming

[read] It follows ImGui colours: `ImGuiCol_Text`, `FrameBg`, `WindowBg`,
`FrameBgHovered` for notes, `TextDisabled` for `rect` frames, and alpha-scaled
`Text` for the rest. No Mermaid theme or config applies. So it follows
pptmstr's theme with no work, and it cannot be styled per diagram.

[op-run] **It follows the theme.** The commonest opaque geometry colours are
the style's own, in both styles:

| Case | Style `FrameBg` / `WindowBg` | Top geometry colours (vertex count) |
|---|---|---|
| `d:flowchart` (dark) | `#1a1a1a` / `#2e2e2e` | `#1a1a1a` (164), `#2e2e2e` (12) |
| `light:flowchart` | `#ffffff` / `#f0f0f0` | `#ffffff` (164), `#f0f0f0` (12) |

Vertex count and item size are identical in both (1484, 564 × 594): only the
colours change. `mermaid-flowchart-light.png` shows black text and strokes on
white boxes, the subgraph a shade darker than the window.

[op-run] One theming defect: **nested subgraph fills compound.** In
`hostile-nested-2000.png` each level's translucent fill stacks on its parent's,
so by about 30 levels the fill is near white and the labels sit on it as dark
chips. One level (the flowchart's subgraph) looks fine; where between 1 and
30 it stops looking fine was not judged.

### Text and fonts

[read]:

- Labels are drawn with `ImDrawList::AddText` in the **current ImGui font**.
  Abstract members use an italic face from rich_md's own font set.
- **It does not go through imtypeset.** Labels do not get imscribe's faces,
  per-word snapping or fallback chain, only whatever font is pushed.
- The size follows `PushFont(NULL, size)`.
- Layout is cached per `(source, ImFont*, font size)`.

[op-run] `font:flowchart` (24 px) is 892 × 950 against `d:flowchart`'s
564 × 594 at 15 px: × 1.58 and × 1.60, against a font ratio of 1.6. **Layout
scales with the pushed font**, so font size is a usable zoom; vertices rise
only from 1484 to 1644.

### Hover and interaction

[read]:

- `mermaid_draw.cpp` has no `IsItemHovered`, tooltip, `InvisibleButton`,
  `PushClipRect`, zoom or pan.
- It reserves its space with one `ImGui::Dummy(DiagramSize)`.
- **Nothing in a diagram is hit-testable.** A host gets one rectangle.

[op-run] `hover:flowchart` reports `item_hovered: true` on its last frame, with
1484 diagram vertices and 1560 in the frame, **identical to `d:flowchart`**:
hovering draws nothing. `item_hovered` is also true on `d:board21` and several
`h:*` cases where no hover was injected (the virtual pointer happened to lie
over them), so it says only that the one `Dummy` rectangle is hoverable. That
confirms the [read] finding and adds nothing to it.

### Per-frame cost

[read] `Render` looks the source up in a `std::map` keyed by
`(source, font, size)`:

- On a miss it parses and lays out.
- On a hit it only draws.
- Entries not drawn for 60 frames (`kEvictionFrames`) are dropped, but **only
  when a new diagram is parsed**.

**Streaming is the expensive case.** A fence whose source grows by a token per
frame is a cache miss every frame: a full parse and layout each frame, and up to
60 dead entries kept alive.

[op-run] Times are of the `render_mermaid` call alone, on the probe machine
under Xvfb (software GL does not enter into it: the call only fills the draw
list). Cold is frame 0, warm is the median from frame 10.

| Diagram | cold (ms) | warm (ms) | diagram vertices |
|---|---|---|---|
| flowchart | 0.26 | 0.029 | 1484 |
| sequence | 0.34 | 0.036 | 2961 |
| class | 0.22 | 0.021 | 926 |
| board21 | 0.30 | 0.038 | 3098 |

**At these sizes it is free.** A cold parse and layout of the 21-node graph is
0.3 ms.

`stream:board21` feeds a growing prefix, one line more per frame, and wraps
round after the 64th, so 148 frames are two passes and every frame is a cache
miss. Per-frame time rises with the prefix from 0.04 to about 0.23 ms, with a
maximum of 0.36 ms; RSS goes from 205,200 kB at frame 5 to 205,744 kB at the
last (+0.5 MB). **The streaming cost the [read] section warned of is real in
kind and negligible at this size.** Where it stops being negligible is the
hostile table below: a cache miss costs a full cold layout.

`stream:sequence` **[unanswered]**: it raised the `InitializeMarkdown` assert,
so some prefix of the sequence source took the code-block path (which frame
was not recorded; the probe emits times only on success). That is a finding in
itself: **a streamed sequence diagram passes through unparseable prefixes**,
so a streaming host would show code and diagram alternately unless it waits for
the fence to close (inferred from the assert, not watched).

### Robustness to hostile input

This matters because rich_md parses untrusted model text in-process.

[read] Parser (`mermaid_parse.cpp`):

- Hand-written scanner: no `std::regex`, no exceptions, no recursion. Nesting is
  kept on explicit vectors.
- An error is a string with a line number, and the diagram falls back to a code
  block.
- **No cap on input length, line count, node count or nesting depth.**

[read] Layout (`mermaid_layout.cpp`). **It does not bound its own work.**

- **Recursion on input depth:**
  - `rankOf` is memoised, but its recursion is as deep as the longest
    dependency chain. It is a `std::function` lambda, so the frames are large.
  - `visit` is a DFS whose recursion depth is the length of the longest path.
  - `layoutScope` and `place` recurse once per subgraph nesting level.

  A long chain or deep nesting is a stack-depth question on the UI thread.
- **Quadratic and worse loops:**
  - The crossing count runs over all node pairs × predecessor pairs, up to 8
    times.
  - Track assignment calls `above()`/`any_of` per channel.
  - Label placement tests every edge segment for each candidate.
  - Sequence layout is O(activations²) and frames × rows.

  None of these has a cap, so a large dense graph is CPU time on the frame
  thread.
- **No clamp on diagram width or height** (`FitDelta` only grows).

[op-run] The `h:*` rows each ran in a child process with a 4 GiB
address-space limit and a 90 s timeout, 15 frames each. `outcome` is `exit`,
`signal` (a crash) or `timeout`. **No case crashed and none timed out.** All
of it ran on the thread that draws the frame.

| Case | source | outcome | cold frame | warm frame | item size (px) | vertices | max RSS |
|---|---|---|---|---|---|---|---|
| chain-500 | 8.8 kB | exit 0 | 5.0 ms | 0.31 ms | 83 × 38,304 | 30k | 218 MB |
| chain-5000 | 98 kB | exit 0 | 249 ms | 3.1 ms | 90 × 382,554 | 300k | 307 MB |
| **chain-50000** | 1.1 MB | exit 0 | **65.2 s** | **29.8 ms** | 97 × 3,825,054 | 3.0M | **1.25 GB** |
| fan-2000 | 37 kB | exit 0 | 66 ms | 1.4 ms | 157,238 × 130 | 136k | 248 MB |
| dense-60x300 | 4.7 kB | exit 0 | 1.3 ms | 0.12 ms | 4,300 × 3,404 | 10k | 217 MB |
| dense-150x1200 | 20 kB | exit 0 | 6.0 ms | 0.38 ms | 18,625 × 15,425 | 37k | 220 MB |
| nested-50 | 3.3 kB | exit 0 | 0.48 ms | 0.055 ms | 1,262 × 2,380 | 4.5k | 217 MB |
| **nested-2000** | 4.0 MB | exit 0 | **3.93 s** | 5.3 ms | 48,062 × 90,130 | 164k | 279 MB |
| seq-5000 | 116 kB | exit 0 | 13 ms | 2.6 ms | 188 × 165,090 | 219k | 273 MB |
| seq-frames-2000 | 4.0 MB | exit 0 | 24 ms | 5.1 ms | 24,138 × 60,123 | 113k | 256 MB |
| label-1mb | 1.0 MB | exit 0 | 14 ms | 6.0 ms (max 15.8) | 7,000,054 × 130 | 890 | 218 MB |
| header-only | 13 B | exit 0 | 0.04 ms | 0.007 ms | 30 × 15 | 0 | 215 MB |
| class-ns-2000 | | assert | — | — | — | — | — |
| malformed-unclosed | | assert | — | — | — | — | — |
| malformed-binary | | assert | — | — | — | — | — |
| empty | | assert | — | — | — | — | — |
| unsupported-pie | | assert | — | — | — | — | — |

(The nested and `loop` sources are 4 MB because each level is indented by its
depth.) A drawn diagram's baseline RSS is about 213 MB.

What this shows:

- **Stall, not crash.** A 50,000-link chain, about 1 MB of model text, blocks
  the UI thread for **65 s** on the frame it first appears, and peaks at 1.25 GB.
  2,000 nested subgraphs block it for **3.9 s**. Cold time grows far faster
  than input: × 49 from 500 to 5,000 links and × 262 from 5,000 to 50,000.
  The 50,000 chain finished 18 s inside the 90 s timeout (71.7 s wall); a
  somewhat longer chain would have shown as `timeout`.
- **The recursion did not overflow.** The [read] concern about `rankOf`/`visit`
  recursing once per link did not produce a SIGSEGV at 50,000 deep on the main
  thread's default stack (Linux, 8 MB unless the operator's limit differs; not
  recorded). A smaller stack, as on a secondary thread, was not tried.
- **The warm cost is not bounded either.** rich_md emits the whole diagram every
  frame, with no culling to the visible region: 3.0 M vertices and 29.8 ms per
  frame for the chain, though the window shows 1000 px of a 3.8 million-px
  diagram. A 1 MB label costs 6 ms a frame to draw 890 vertices.
- **No size clamp.** Items reach 3.8 M px tall and 7 M px wide.
- **Small and moderate hostile inputs are bounded in practice**: the dense
  DAGs, 50-deep nesting, 5,000 messages and 2,000 `loop` frames all lay out in
  under 25 ms.
- **The five assert rows are [unanswered].** Each took the code-block path,
  which needs `rich_md.initialize_markdown()` first; the probe never called it.
  So whether the parser itself is bounded on malformed, binary, empty and
  2,000-deep-namespace input is not measured. What is shown is that each got
  to its error path in about 0.35 s of process wall time, start-up included,
  so none of them hung in the parser.

**Verdict on hostile input: a model can stall the UI thread with rich_md, for
over a minute with about a megabyte of plausible-looking text. Crashes were not
shown.** Any route that hands model-written Mermaid to rich_md needs a source
pre-check (size, line count, node count, nesting depth) and should not lay out
on the frame thread at all, which rich_md's API gives no way to avoid.

## 2. Using rich_md piecemeal

[read] `rich_md.render_mermaid(source: str) -> None`, in C++
`RichMd::RenderMermaid(const std::string&)`:

- It draws one diagram at the **cursor**, at its natural size, and advances the
  cursor with a `Dummy`.
- It works without the markdown widget, so it can be called inside any window
  or child, including a Hosted block's region. The host sets the cursor; to
  shrink the diagram it pushes a smaller font.
- `register_fenced_block_renderer(language, fn)` is the reverse hook: our
  renderer inside rich_md's markdown. pptmstr does not use rich_md's markdown,
  so it does not apply.

What it does **not** offer [read: `docs/api.md`, `rich_md.pyi` on `main`,
`rich_md_mermaid.h`]:

- **No measure-without-draw call.** A host cannot know the diagram's height
  before the first frame it draws it. That is the same "text below a growing
  host jumps" problem as imtypeset C2 (planning record §3.6), and here it needs a
  frame of delay or an off-screen draw.
- **No "fit into this rect" or scale parameter.** The only lever is font size.
- **No clip of its own.** A host that wants the diagram clipped to its block
  wraps it in a child window or `PushClipRect`.
- **No result value in Python.** Parse failure is drawn as code; the C++
  `Mermaid::Render` returns a `RenderResult{ok, error}`, but `RenderMermaid`,
  the public call, does not.
- A rich_md **context** owns the diagram cache. The Python surface has
  `create_context`/`set_current_context`.

[op-run] **A diagram that parses draws with no setup**: the `d:*` rows called
neither `create_context` nor `initialize_markdown` and drew. **A diagram that
does not parse raises**: the code-block fallback asserts
`_Renderer() && "RichMd: call InitializeMarkdown first"`, which the bundle
turns into a Python `RuntimeError` from inside the frame. So piecemeal use is
not quite free: a host must call `initialize_markdown()` once (what it loads and
costs was not measured) or catch
the exception on every malformed, unsupported or half-streamed fence. In a C++
build the same assert aborts, or with asserts compiled out presumably
dereferences null ([read]-level inference from the message, not tested).

[op-run] `child:board21` draws the 21-node graph inside a 360 × 220 bordered
child:

- the item is still 1152 × 765: **it does not fit the host**;
- 1826 of its 2034 vertices (90 %) lie outside the child and are clipped by
  ImGui's clip rect, not by rich_md;
- the child scrolls, with a range of 822 × 561;
- `mermaid-board21-child.png` shows the top-left corner of the graph and a
  scroll bar.

So a fixed-size host gets a scrolling viewport for free from ImGui, and
anything else (fit, zoom) only by pushing a smaller font before the call.

## 3. ImGui 1.93 strokes

[read] ocornut/imgui v1.93.0 release notes:

- **New flags:**
  - `ImDrawFlags_JoinMiter` forces miter. The default is miter that bevels when
    too sharp.
  - `ImDrawFlags_CapSquare`.
  - `ImDrawFlags_AALineEnds` (off by default; `style.AntiAliasedLineEnds`).
  - `StrokeInside`, `StrokeCenter`, `StrokeCenterBiased`, `StrokeOutside`,
    `StrokeLegacy`.
  - `PushDrawFlag`/`PopDrawFlag`.
- **No round join, no round cap and no dashes are named.** The spec asks for
  "miter and round joins"; on these notes there is no round join to draw.
- **Default changes:**
  - `AddLine` loses its +0.5 offset.
  - `AddRect`, `AddCircle`, `AddNgon`, `AddEllipse`, `AddTriangle` and
    `AddQuad` default to inside stroke.
  - `AddRectFilled` at fractional coordinates is now anti-aliased.
  - `CurveTessellationTol` becomes `CurveTessellationMaxError`.
  - `AntiAliasedLinesUseTex` is obsolete.

[ran] 1.92.900 baseline, headless (`bundle193_probe.py all`).

The zigzag has 150°, 90° and 30° turns and a hairpin. A spike is the furthest
vertex from the centre line, divided by half the thickness:

| thickness | open/closed | vtx | idx | spike | alpha-0 fringe vtx |
|---|---|---|---|---|---|
| 1.0 | open | 14 | 36 | 15.4 | 0 |
| 1.0 | closed | 14 | 42 | 23.0 | 0 |
| 1.5 | open | 28 | 108 | 8.5 | 14 |
| 3.0 | open | 14 | 36 | 8.5 | 0 |
| 8.0 | open | 14 | 36 | 6.4 | 0 |
| 8.0 | closed | 14 | 42 | 9.6 | 0 |

How to read the table:

- **1.92.900 has unlimited miters.** At the hairpin a 1 px line puts a vertex
  15× its half-width from the path. At 8 px it is 6.4× (a 26 px spike), which
  `strokes-1.92.9.png` shows.
- Integer thicknesses take the texture-AA path: 2 vertices per point, no
  geometric fringe. 1.5 px takes the geometric fringe path: 4 per point, half of
  them alpha 0.
- **No join, cap or stroke-side flag exists.** The probe reports each one
  absent.

[ran] 1.92.900 `AddLine((100,1000),(200,1000))` and
`AddRect((100,1100),(200,1150))`. These are opaque-vertex bounding boxes:

| thickness | line x-range | rect bbox |
|---|---|---|
| 1 | 100.5 to 200.5 (the +0.5 offset) | 99 to 201 × 1099 to 1151 (centred, plus the AA fringe) |
| 3 | 100.5 to 200.5 | 98 to 202 × 1098 to 1152 |

[op-run] The same probe on 1.93.0 (`out-v193.jsonl`; picture
`out-v193/strokes-1.93.0.png` beside `out-v192/strokes-1.92.9.png`). The flag
names exist: `join_miter`, `cap_square`, `aa_line_ends`, `stroke_inside`,
`stroke_center`, `stroke_center_biased`, `stroke_outside`, `stroke_legacy`, plus
`push_draw_flag`/`pop_draw_flag`. **No round join, round cap or dash name
exists anywhere** (`dash_anywhere: []`), confirming the release-notes read.

Open zigzag, spike in half-widths (and in px at 8 px):

| thickness | 1.92.9 | 1.93 default | 1.93 `join_miter` | 1.93 `stroke_legacy` |
|---|---|---|---|---|
| 1.0 | 15.4 | 4.0 | 10.2 | 10.2 |
| 1.5 | 8.5 | 3.3 | 8.5 | 8.5 |
| 3.0 | 8.5 | 2.7 | 6.8 | 6.8 |
| 8.0 | 6.4 (26 px) | 2.2 (9 px) | 5.8 (23 px) | 5.8 (23 px) |

Closed: 1.92.9 9.6 to 23.0; 1.93 default 2.2 to 3.9.

- **Joins: the default now bevels.** The hairpin is blunt in the picture, and
  the spike at 8 px drops from 26 px to 9 px. It does not fall to 1: the
  measure includes the bevel's corner and the AA texels. `join_miter` brings
  the spike back, close to but not exactly 1.92.9's values. Vertices go from 14
  to 16 open (20 closed): the bevel adds two at the sharp joins.
- **Caps: `cap_square` extends each open end by about half the thickness**
  along the end segment: the left end moves out 1.45 px at 3 px and 3.86 px at
  8 px. The right end barely moves in x because the last segment is nearly
  vertical.
- **`aa_line_ends` adds 4 vertices, all alpha 0** (two per end), and pulls the
  opaque extent in slightly (0.48 px at 1 px): the ends are feathered rather
  than cut. Closed paths are unaffected.
- **Alignment works on open paths too.** `stroke_inside` puts the stroke on one
  side: at 8 px the opaque box grows only downward (99.3 to 216.4 in y, against
  93.6 to 208.7 centred). `stroke_outside` grows the other way.
  `stroke_center_biased` equals `stroke_inside` at 1 px and `stroke_center` at
  8 px: it is a pixel-alignment rule for thin lines.
- **Textured AA at every width.** 1.92.9 drew 1.5 px with a geometric fringe
  (28 vertices, 14 at alpha 0) and integer widths with 2 per point. 1.93 draws
  all four widths with 2 per point and no alpha-0 fringe vertices, so
  fractional widths are no longer a separate path (inferred from counts; the
  UV of each vertex was not recorded in these rows).

`AddLine((100,1000),(200,1000))` and `AddRect((100,1100),(200,1150))`,
opaque-vertex bounding boxes:

| thickness | flags | line x / y | rect bbox |
|---|---|---|---|
| 1 | 1.92.9 | 100.5–200.5 / 999–1002 | 99–201 × 1099–1151 |
| 1 | 1.93 default | 100–200 / 999–1001 | **100–200 × 1100–1150** (inside) |
| 1 | 1.93 `stroke_center` | 100–200 / 999–1001 | 99–201 × 1099–1151 |
| 1 | 1.93 `stroke_legacy` | 100.5–200.5 / 999.5–1001.5 | 99.5–200.5 × 1099.5–1150.5 |
| 3 | 1.92.9 | 100.5–200.5 / 998–1003 | 98–202 × 1098–1152 |
| 3 | 1.93 default | 100–200 / 998–1002 | 100–200 × 1100–1150 |
| 3 | 1.93 `stroke_legacy` | 100.5–200.5 / 998.5–1002.5 | 98.5–201.5 × 1098.5–1151.5 |

- **The +0.5 is gone from `AddLine`, and `AddRect` strokes inside**, as the
  release notes say.
- **`stroke_legacy` is not 1.92.9.** It restores the +0.5 and the unlimited
  miter, but its boxes differ from 1.92.9's by half a pixel at every width
  tried. Setting it globally would not make pptmstr's screenshots match; the
  re-photographing in §5 is not avoidable that way.
- The rect under `stroke_center` matches 1.92.9 at 1 px and 3 px but not at
  2 px (99–201 against 98.5–201.5).

## 4. Licence, maintenance and dependencies of rich_md

[read]:

- **Licence:** MIT (`LICENSE`, `THIRD_PARTY_LICENSES.md`).
- **Always built in:**
  - md4c (MIT)
  - the imgui_md descendant (MIT)
  - stb_image (public domain or MIT)
  - Roboto (Apache 2.0)
  - Inconsolata (OFL)
- **Optional:**
  - ImGuiColorTextEdit (MIT)
  - MicroTeX (MIT)
  - FreeType (FTL or GPLv2), for LaTeX
  - libcurl, for URL images
- **The Mermaid backend has no dependency beyond Dear ImGui** (CMake
  `IMGUI_RICHMD_WITH_MERMAID`, on by default).
- **Maintenance:** one author, Pascal Thomet (pthom, also imgui-bundle's
  maintainer).
  - All 21 commits visible on the first page of the API listing are his,
    2026-10-07 to 2026-10-21. The repository shows 148 commits.
  - The tag v1.0.0 is "the first release", dated the day the bundle shipped it.
  - It has a Mermaid test corpus of about 90 `.mmd` files, including 7 `error_*`
    cases, and a `mermaid_review` image tool.
  - So it is new, actively written and single-maintainer, and it moves with the
    bundle, which ships it.

## 5. What a bump costs

### What moves together

- **`imgui-bundle==1.92.900` → `1.93.0`.** Wheels exist for cp311 to cp315,
  manylinux_2_28 x86_64 and aarch64, musllinux, macOS 14 arm64 and Windows
  [read] pypi.org/pypi/imgui-bundle/1.93.0/json.
- **`imtypeset==0.4.3.192900` → a new `0.4.x.193000` line.**
  - **No 193 line exists on PyPI.** The simple index lists only 192801 and
    192900 builds [read].
  - imtypeset refuses any other bundle at import, by version string and by a
    binary re-probe (`docs/advtext-rework-plan.md` §3.3). So the bump means:
    1. a release of imtypeset built against the 1.93.0 wheel's fork headers;
    2. a new entry in `python/host/bundle_pins.toml` (bundle commit, fork
       commit, config sha256, stand-in tag `v1.93.0-docking`);
    3. both CI legs (Linux x86_64, macOS arm64), times the CPython 3.11 to 3.14
       versions;
    4. publishing (`docs/releasing.md`).
- **`imscribe==0.2.0`** pins both exactly (README "The pins to hold together").
  It needs a release that names the new pair, or pptmstr has to override
  imscribe's metadata.
- **pptmstr's `pyproject.toml`** moves its three pins and the lock.

### What changes under the API

- **[ran] Renames: none hit pptmstr or imscribe on 1.92.900.** The `api` mode
  scanned `pptmstr/` and `~/Source/imscribe/imscribe` for every `imgui.x`,
  `imgui.x.y`, `hello_imgui.x` and `immapp.x` name: 323 distinct, 0 missing.
  That is the control.
- **[op-run] The same scan on 1.93.0: 323 distinct, 323 checked, 0 missing.**
  No name pptmstr or imscribe writes as `imgui.x`/`imgui.x.y`/`hello_imgui.x`/
  `immapp.x` is gone. The scan does not see attribute names reached through
  an object (`style.x`, `draw_list.x`); for the two 1.93 style removals,
  `curve_tessellation_tol` and `anti_aliased_lines_use_tex` (both `ABSENT` on
  1.93.0 [op-run]), a `grep` of both trees finds no use [read].
- **[op-run] The removals the scripts depend on are real on 1.93.0:**
  `ImDrawListFlags_` is absent (`draw_list_flags.present: false`) and
  `ImDrawFlags_` has `round_top` etc. and no `round_corners_*`. So the two
  probes named below break as predicted. `imgui_md` is the same module as
  `rich_md` (`imgui_md_alias.same_module: true`).
- **[read] Python breaks the bundle changelog lists,** each checked against
  both trees by `grep`:
  - `ImDrawFlags_.round_corners_*` → `round_*`: pptmstr does not use it.
    `scripts/tool_block_probe.py` uses `round_corners_top`, and that probe
    breaks.
  - `ImDrawListFlags_` merged into `ImDrawFlags_`: pptmstr does not use it.
    `scripts/imgui_vector_probe.py` reads `imgui.ImDrawListFlags_` at import and
    breaks. imscribe's `tests/test_makefile_env.py` names it.
  - `set_drag_drop_payload(type, data, sz)` removed: neither tree uses it.
  - Slider, input and colour widgets return `ImVec2`/`ImVec4` when given one:
    the `*_float2/3/4` and `color_edit` calls do not occur in either tree.
  - `TextFilter.draw` lost its width: neither tree uses it.
  - `begin_window`/`end_window` hooks removed: neither tree uses them.
  - `imgui_md` is now an alias of `rich_md`, and `render()` dedents and resolves
    transclusions: pptmstr does not use `imgui_md` (`pptmstr/ui/inline.py`
    says why).
- **[read] Visual changes with no API change.** These need re-photographing, not
  code:
  - pptmstr makes 38 calls to `add_line`, `add_rect`, `add_rect_filled`,
    `add_circle`, `add_polyline`, `add_triangle`, `add_ngon` and `add_ellipse`,
    across 12 files: `pptmstr/figures/host.py` and 11 under `pptmstr/ui/`
    (`grep`). Under 1.93:
    - `add_line` moves by 0.5 px ([op-run], §3);
    - `add_rect` strokes inside ([op-run], §3);
    - fractional `add_rect_filled` is anti-aliased ([read]; not probed).
  - [op-run] `stroke_legacy` does not restore 1.92.9's pixels (§3), so there
    is no flag that makes the bump visually free.
  - imscribe's selection and underline fills use `add_rect_filled` and
    `add_line` (`docs/imgui-risks.md`), so the same applies to them.
  - **imtypeset avoids the risk for its own rectangles.** It draws its frames as
    its own one-pixel quads "and not ImGui's AddRect and AddLine, whose geometry
    … is not the same from one version of ImGui to the next"
    (`src/draw.cpp`).
  - **But it mirrors `ImFont::RenderText` test for test** (`src/draw.cpp`). If
    1.93 changed RenderText's pixel snap (`IM_TRUNC`) or its clip tests,
    imtypeset's mirror has to follow. **Unverified either way.** The 1.93
    notes read here name no RenderText change. imscribe's text-snapping
    findings (`docs/imgui-risks.md` §2) were all measured on 1.92.900 and would
    need re-running.
- **[read] Two of imscribe's undocumented-behaviour risks are tied to the
  version** (`docs/imgui-risks.md`, "`BareView` leans on two ImGui behaviours
  nothing documents"): a button submitted on the press frame becomes active,
  and `get_hovered_id()` semantics. Both are re-tests after a bump.

## Verdict

Every finding below is tagged by how it is known. The [op-run] numbers are in
§1 to §5.

### What a bump would remove from the C++ plan

The plan is `imgui-vector-survey.md`, "What this changes for the display-list
design".

1. **The stroker, mostly.**
   - [op-run] 1.93.0 bevels sharp joins by default (an 8 px hairpin spike falls
     from 26 px to 9 px), forces miter on request, extends square caps by half
     the thickness, aligns strokes inside/centre/outside on open and closed
     paths, and feathers ends with `aa_line_ends`.
   - What would remain ours, [op-run] confirmed by the absence of any such
     flag:
     - **round joins and caps**, as arc geometry in the front end;
     - **dashes**, split in the front end, as the plan already says;
     - **`stroke-miterlimit` values** other than ImGui's built-in bevel
       threshold ([read]: the threshold is not exposed as a number).
   - [ran] 1.92.900 has unlimited miters, 6 to 23 half-widths at a hairpin.
2. **Not the Mermaid front end, and not the triangulator.**
   - [op-run] rich_md draws three of the five families pptmstr's probes use,
     and draws them well: complete, compact, and in the ImGui theme with no
     work. State and ER do not draw.
   - [op-run] It cannot be fitted to a rect (a fixed child gets a scrolling
     corner of the diagram), has no hit-testing, and [read] cannot be measured
     before it is drawn and sets text in ImGui's font rather than through
     imtypeset.
   - [op-run] **It stalls the UI thread on large input and does not crash**:
     65 s and 1.25 GB for a 1 MB, 50,000-link chain; 3.9 s for 2,000 nested
     subgraphs; and it draws every vertex every frame, 30 ms a frame for the
     chain. Small and moderate hostile inputs are bounded in practice.
   - [op-run] Its fallback for anything it cannot parse raises unless
     `initialize_markdown()` has been called, and a streamed sequence diagram
     hits that fallback on the way.
   - So it could stand in for flowchart, sequence and class drawing only behind
     a guard pptmstr would write: `initialize_markdown()` at start-up, a size
     and depth pre-check on the source, and drawing only once the fence has
     closed. Even then layout runs on the frame thread, and nothing in its API
     moves it off.
   - merman remains the route for the other families and for parity.
   - Fills, holes and glyph outlines are untouched by 1.93: libtess2 or the
     raster fallback stays the plan.

### What it would cost

- an imtypeset release on a new bundle line, with CI on both platforms
  ([read]: no 193 line on PyPI);
- an imscribe release naming the new pair;
- pptmstr's three pins and its lock;
- re-photographing every pptmstr pane, imscribe's demo and the figure host,
  because of the stroke defaults; [op-run] `stroke_legacy` does not avoid it;
- re-running imscribe's text-snapping and BareView measurements;
- deleting or porting two probes, `tool_block_probe.py` and
  `imgui_vector_probe.py` ([op-run]: the names they use are gone).

**Nothing in pptmstr's or imscribe's code fails to resolve by name**: 323 of
323 [ran on 1.92.900; op-run on 1.93.0].

### What to do

**Recommendation: size the bump as its own decision, and decide it on the
stroker alone.** rich_md is not a reason to bump. It is a reason to keep reading
its corpus and to watch whether it grows bounds, a measure call and a way to
lay out off the frame thread.

**Do not route model-written Mermaid to rich_md.** The run answered the
question the [read] section left open: a model can stall the UI for over a
minute with about a megabyte of text, so a source pre-check would be a
precondition and not an option, and even with one the layout cost lands on the
frame.

### Against the lead's proposal

The proposal: keep merman for Mermaid; drop the raw `svg` fence; make Mermaid
native later through merman's layout; bump to 1.93 only for strokes, later.

- **Keep merman for Mermaid: supported.** rich_md covers three of five
  families, stalls on large input on the frame thread, raises on its own
  fallback without setup, and cannot be fitted. pptmstr's merman route lays
  out and rasterizes in a separate process, never on the frame thread [read:
  module docstrings of `pptmstr/figures/mermaid.py` and `render.py`], so the
  stall rich_md shows lands there instead of on the UI.
- **Drop the raw `svg` fence: not tested.** Nothing in this run bears on it,
  for or against.
- **Mermaid native later, through merman's layout: supported in direction,
  not tested.** rich_md shows what native drawing buys once layout is done:
  0.02 to 0.04 ms a frame for real diagrams, theme-following for free, crisp at
  any font size. It also shows the two things that route has to do itself:
  cull to the visible region (rich_md does not, and pays 30 ms a frame for it)
  and keep layout off the frame thread (merman's layout, run in the
  background, would). Whether merman exposes its layout in a usable form was
  not looked at here.
- **Bump to 1.93 only for strokes, later: supported.** The strokes are what
  the notes said and what the plan's stroker would otherwise do, minus round
  joins, round caps, dashes and miter-limit values. "Later" is forced anyway:
  there is no imtypeset build for 1.93, and the bump re-photographs
  everything with no legacy flag to soften it.

**[unanswered], and what would answer it** (none of it changes the verdict
above):

- the five malformed/unsupported `h:*` cases and `class-ns-2000`, `d:state`,
  `d:er` and `stream:sequence` with `rich_md.initialize_markdown()` called in
  `render_case` first: whether the parser and fallback are bounded on that
  input;
- board21 rendered by merman, beside `mermaid-board21.png`;
- the stack depth at which `rankOf` overflows off the main thread.

## Reproduce

    # headless, any venv with imgui-bundle (and numpy for the windowed modes)
    PY=/tmp/claude-1000/b193/v193/bin/python
    $PY scripts/bundle193_probe.py all --out OUT --scan pptmstr ~/Source/imscribe/imscribe
    xvfb-run -a -s "-screen 0 1600x1200x24" $PY scripts/bundle193_probe.py shots --out OUT
    xvfb-run -a -s "-screen 0 1600x1200x24" $PY scripts/bundle193_probe.py mermaid --out OUT
    # everything, both versions, in scratch venvs (needs network)
    scripts/bundle193_run.sh /tmp/claude-1000/b193

Each measurement is one JSON line prefixed `@@ `. Run here on 1.92.900 from
the worktree `.venv` with `-B`: `all` headless (28 lines, exit 0), and `shots`
under `scripts/with_display.sh`. The [op-run] results come from the operator's
run of `scripts/bundle193_run.sh /tmp/claude-1000/b193`
(`cmd-20261021T211159Z-5539246d0379`), unmodified probe.

To answer the [unanswered] fallback rows, `render_case` in the probe needs
`rich_md.initialize_markdown()` before its first `render_mermaid`, then
`mermaid` mode re-run on the 1.93.0 venv.

## Sources

- github.com/pthom/imgui_rich_md:
  - `docs/mermaid.md`, `docs/api.md`, `THIRD_PARTY_LICENSES.md`
  - `imgui_rich_md/backends/mermaid/{rich_md_mermaid.h, rich_md_mermaid.cpp,
    mermaid_parse.cpp, mermaid_layout.cpp, mermaid_draw.cpp}`, read at `main`
    on 2026-10-21 through a summarising fetch: quoted fragments, not whole
    files
  - the git tree, and the commits API (first page)
- github.com/pthom/imgui_bundle: `CHANGELOG.md` (v1.93.0) and
  `bindings/imgui_bundle/rich_md.pyi` at `main`. Whether `main` equals the
  1.93.0 tag for these files is unverified; the `info` mode prints the
  installed module's surface.
- github.com/ocornut/imgui/releases/tag/v1.93.0 (first 100k characters of the
  page).
- pypi.org: `imgui-bundle/1.93.0/json`, `simple/imtypeset/`.
- Local:
  - `~/Source/imscribe/README.md` ("The pins to hold together") and
    `docs/imgui-risks.md`
  - `~/Source/imtypeset/docs/CHANGELOG.md`, `docs/releasing.md`,
    `docs/advtext-rework-plan.md` §3.3, `python/host/bundle_pins.toml`,
    `src/draw.cpp`
  - the 7b792e36 transcript, line 101
