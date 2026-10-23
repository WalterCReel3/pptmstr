# Probe: imgui-bundle 1.93.0, rich_md's Mermaid and ImGui 1.93 stroking

Board task `bundle-193-probe`, 2026-10-21.

Each finding is tagged:

- **[ran]**: executed here, with the command named under "Reproduce".
- **[read]**: read from source or docs, with the source named. Nothing was executed.
- **[pending]**: needs the operator-run install, which had not run when this
  note was written (see "Status of the run").

This worktree's `.venv` was not changed. The only use of it was to run the
probe with `python -B` (no bytecode written) against the installed 1.92.900, to
check that the script works before the operator runs it.

## Status of the run

The sandbox has no network. `scripts/bundle193_run.sh` creates two scratch
venvs under `/tmp/claude-1000/b193`: imgui-bundle 1.93.0 and 1.92.900, each
with numpy. It then runs `scripts/bundle193_probe.py` in both, headless and
under `xvfb-run`. It was handed to the operator as command
`cmd-20261021T211159Z-5539246d0379`.

**When this note was written the operator had NOT run it.** Every row marked
[pending] is unmeasured. The verdict below rests on [read] evidence where it
says so.

What did run, on 1.92.900 from this worktree's `.venv`:

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
board graph. **[pending]**: `mermaid-<name>.png` and the `mermaid_case` rows of
`mermaid.jsonl`.

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

[pending] The `light:flowchart` and `d:flowchart` rows record the commonest
geometry colours beside the style's own, and `mermaid-flowchart-light.png` is
the picture.

### Text and fonts

[read]:

- Labels are drawn with `ImDrawList::AddText` in the **current ImGui font**.
  Abstract members use an italic face from rich_md's own font set.
- **It does not go through imtypeset.** Labels do not get imscribe's faces,
  per-word snapping or fallback chain, only whatever font is pushed.
- The size follows `PushFont(NULL, size)`.
- Layout is cached per `(source, ImFont*, font size)`.

[pending] `font:flowchart` (24 px) against `d:flowchart` (default) gives the
item size at each size.

### Hover and interaction

[read]:

- `mermaid_draw.cpp` has no `IsItemHovered`, tooltip, `InvisibleButton`,
  `PushClipRect`, zoom or pan.
- It reserves its space with one `ImGui::Dummy(DiagramSize)`.
- **Nothing in a diagram is hit-testable.** A host gets one rectangle.

[pending] `hover:flowchart` puts the mouse over the item and records
`item_hovered` and the frame's total vertices against `d:flowchart`.

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

[pending]:

- `frame0_ms` (cold) and `warm_median_ms` per diagram.
- `stream:board21` and `stream:sequence` (source grows a line per frame), with
  per-frame times and RSS at frame 5 and at the last frame.

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

[pending] The `h:*` rows each run in a child process with a 4 GiB
address-space limit and a 90 s timeout. `mermaid_process.outcome` is `exit`,
`signal` (a crash: a negative code, so -11 is SIGSEGV) or `timeout`. The
cases are:

- chains of 500, 5,000 and 50,000 nodes;
- a fan of 2,000;
- dense DAGs of 60×300 and 150×1,200 edges;
- subgraph nesting of 50 and 2,000;
- 5,000 sequence messages and 2,000 nested `loop` frames;
- a class name 2,000 namespaces deep;
- a 1 MB label;
- two malformed sources, an empty source, a header-only source and `pie`.

**Until these run, treat "a model can stall or crash the UI thread with
rich_md" as likely and not shown.**

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
  `create_context`/`set_current_context`, and whether `render_mermaid` needs
  one created first is [pending] (the `d:*` rows fail if it does).

[pending] `child:board21` draws the 21-node graph inside a 360×220 bordered
child and records:

- the item size against the child;
- vertices outside the child (clipped by ImGui's clip rect, not by rich_md);
- the child's scroll range.

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

[pending] The same rows on 1.93.0, under each of its stroke flags and the three
pairs (`join_miter|cap_square`, `cap_square|aa_line_ends`,
`join_miter|aa_line_ends`), and `strokes-1.93.*.png` beside
`strokes-1.92.9.png` (`out-v193/`, `out-v192/`). What to look for:

- whether the spike falls to about 1 (bevel) by default and returns under
  `join_miter`;
- whether `cap_square` extends the ends by half the thickness;
- what `aa_line_ends` adds in fringe vertices;
- whether 1 px lines still take a texture path.

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
- **[pending]** The same scan on 1.93.0 (`out-v193.jsonl`, kind `api`).
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
    - `add_line` moves by 0.5 px;
    - `add_rect` strokes inside;
    - fractional `add_rect_filled` is anti-aliased.
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

### What a bump would remove from the C++ plan

These are inferred from the reads, and the 1.93 halves are [pending] a run.
The plan is `imgui-vector-survey.md`, "What this changes for the display-list
design".

1. **The stroker, mostly.**
   - 1.93 gives joins that bevel instead of spiking, forced miter, square caps,
     stroke alignment and AA ends.
   - What would remain ours:
     - **round joins and caps**, as arc geometry in the front end;
     - **dashes**, split in the front end, as the plan already says;
     - **`stroke-miterlimit` values** other than ImGui's built-in bevel
       threshold.
   - The 1.92.900 baseline [ran] shows why a stroker is otherwise needed:
     unlimited miters, 6 to 23 half-widths at a hairpin.
2. **Not the Mermaid front end, and not the triangulator.**
   - rich_md draws three of the five families pptmstr's probes use. State and
     ER become code blocks.
   - It cannot be measured before it is drawn, cannot be fitted to a rect, has
     no hit-testing, and sets text in ImGui's font rather than through
     imtypeset.
   - Most important for a renderer of model text: it puts no bound on input
     size, layout work or recursion depth, and it runs on the UI thread
     [read].
   - So it can stand in for flowchart, sequence and class drawing only behind a
     guard pptmstr would have to write. That guard is a size and depth
     pre-check on the source, and possibly running it after the source stops
     streaming.
   - merman remains the route for the other families and for parity.
   - Fills, holes and glyph outlines are untouched by 1.93: libtess2 or the
     raster fallback stays the plan.

### What it would cost

- an imtypeset release on a new bundle line, with CI on both platforms;
- an imscribe release naming the new pair;
- pptmstr's three pins and its lock;
- re-photographing every pptmstr pane, imscribe's demo and the figure host,
  because of the stroke defaults;
- re-running imscribe's text-snapping and BareView measurements;
- deleting or porting two probes: `tool_block_probe.py` and
  `imgui_vector_probe.py`.

**Nothing found in pptmstr's or imscribe's code fails to resolve by name**
[ran on 1.92.900; pending on 1.93.0].

### What to do

**Recommendation: size the bump as its own decision, and decide it on the
stroker alone.** rich_md is not a reason to bump. It is a reason to keep reading
its corpus and to watch whether it grows bounds and a measure call.

Do not route model-written Mermaid to rich_md until the `h:*` rows show what the
chain, nesting and dense cases do. If any of them is `signal` or `timeout`, a
source pre-check is a precondition, not an option.

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
under `scripts/with_display.sh`.

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
