# Vector drawing that already exists in Dear ImGui and imgui-bundle

Research for board task `imgui-vector-survey`, 2026-10-21. No source in `pptmstr/`
was changed. The task asked for one thing before any display-list design: check
what already exists. The display list is the Python front end that turns SVG or
Mermaid into paths, fills, strokes and text runs, with a C++ back end drawing it
into `ImDrawList`.

**How each claim is marked**
- **[ran]**: executed in this worktree's `.venv` (CPython 3.11, imgui-bundle
  1.92.900, imtypeset 0.4.3.192900). Most rows come from
  `scripts/imgui_vector_probe.py`: a headless ImGui context, no GPU, with every
  figure read back from the draw list's own vertex and index buffers.
- **[read]**: I read the named file or URL myself in this task.
- **[read, delegated]**: a sub-agent I started in this task fetched the URL and
  reported it. I did not fetch it again. Check it before it carries weight.
- **[inferred]**: reasoning from the above. Nothing read states it.
- **[unverified]**: not checked.

## Summary

1. **ImDrawList 1.92.9 already fills the shapes a diagram uses.** Rects, rounded
   rects, circles, ellipses, Béziers, arcs and simple concave polygons all fill
   correctly.
   - **Holes:** it fills a polygon with a hole *if* the front end bridges the hole
     into a keyhole: 0 cells wrong out of 8,400 [ran]. With anti-aliased fill on,
     the bridge leaves a seam at **0.75 opacity** inside solid ink [ran].
   - **Self-intersection:** a self-intersecting polygon comes out as garbage. A
     pentagram gives 3 triangles, 1,237 cells inked outside the star and 2,520
     cells overdrawn [ran].
   - **Cost:** concave fill is O(N²). A 4,000-point spiky polygon takes 27–29 ms
     [ran].
   - **What the C++ side needs:** a real triangulator for even-odd and nonzero
     fills, holes and self-intersection. libtess2 is the only candidate with fill
     rules. **No new GPU path is needed.**
2. **Strokes in 1.92.9 have no join or cap control.** `AddPolyline` and
   `PathStroke` take only `closed` [ran: `ImDrawFlags_` members]. Dear ImGui
   **1.93.0** (2026-10-14) reworked stroking: miter joins, square caps,
   inside/centre/outside alignment, anti-aliased line ends [read]. imgui-bundle
   **1.93.0** shipped it today, 2026-10-21 [read]. We are pinned to 1.92.900, and
   imtypeset's wheel is ABI-pinned to that bundle, so moving to 1.93 is an
   imtypeset rebuild. That is the trade to weigh against writing our own stroker.
3. **imgui-bundle 1.93.0 also ships `imgui_bundle.rich_md` (pthom/imgui_rich_md,
   MIT).** It draws Mermaid flowchart, sequence and class diagrams natively into
   `ImDrawList`, with its own parser and layout, and no SVG or intermediate
   representation [read]. It is **not in 1.92.900**: there are no Mermaid
   strings in the installed `.so` [ran]. It is the closest prior art, and a real
   alternative for Mermaid specifically.
4. **Nothing in 1.92.900 draws SVG inline as vectors.**
   - nanovg has no SVG parser. It reaches a window only through a framebuffer
     texture, or by drawing behind every window [read: stub].
   - plutosvg is linked only for OT-SVG glyph fonts [ran].
   - ImPlot and ImPlot3D give out their draw list only inside a plot [read].
   - Everything else is raster.

   **`svg-renderers.md`'s nanovg claim holds** for the installed version.
5. **Index width is 32-bit in this build.** `imgui.INDEX_SIZE == 4` and
   `idx_buffer` is `ImVector_uint`. A 400,008-vertex polyline stayed in one draw
   command, with a maximum index of 400,007 [ran]. The 16-bit command-splitting
   concern does not apply to this wheel.
6. **The cost of crossing from Python is what matters.**
   - **Cached `ImVec2` lists:** handed back each frame, they cost **10–16 ns per
     point** [ran].
   - **Tuples:** converted on every call, they cost 150–280 ns per point.
   - **A `path_line_to` loop:** about 205 ns per point.
   - **Triangles written from Python with `prim_vtx`:** about 740 ns per input
     point (3 vertices each), which rules out a Python tessellator that writes
     vertices [ran].
7. **Text can go through imtypeset as it stands, for horizontal runs.**
   imtypeset writes ImGui atlas glyph quads at pixel-snapped positions
   [read: `src/draw.h` at the pinned commit]. A display-list text run that is
   axis-aligned and set in imscribe's faces can be a `Paragraph` drawn at a
   point [read: `Paragraph.draw`]. Rotated or transformed text cannot.

## 1. Dear ImGui's own `ImDrawList` (1.92.9 as bundled)

| Item | Finding | Basis |
|---|---|---|
| Bundled ImGui version | `imgui.get_version()` returns `1.92.9`. The bundle's 1.92.900 release notes say ImGui v1.92.9b. | [ran]; [read] api.github.com/repos/pthom/imgui_bundle/releases |
| Path API | `path_line_to`, `path_line_to_merge_duplicate`, `path_arc_to`, `path_arc_to_fast`, `path_elliptical_arc_to`, `path_bezier_cubic_curve_to`, `path_bezier_quadratic_curve_to`, `path_rect`, `path_fill_convex`, `path_fill_concave`, `path_stroke(col, thickness, flags)`, plus `prim_reserve`/`prim_vtx`/`prim_write_*` | [ran] `dir(imgui.ImDrawList)`; [read] `imgui/__init__.pyi` |
| Stroke flags in 1.92.9 | `ImDrawFlags_` has `closed` and the `round_corners_*` set only. **No join, cap or alignment flags.** | [ran] `dir(imgui.ImDrawFlags_)` |
| `AddConcavePolyFilled` / `PathFillConcave` | Added in **1.90.5**. Release note: "only simple polygons (no self-intersections, no holes) are supported." The 1.92.9 stub repeats this and adds "O(N^2) complexity". | [read] github.com/ocornut/imgui/releases/tag/v1.90.5; [read] stub |
| Hole via keyhole bridge | Outer square (100×100) plus a 40×40 hole, joined by a bridge with duplicated bridge vertices (12 points): 10 triangles, **0 cells missing, 0 inked inside the hole, 0 overdrawn** (AA fill off). Without the return trip along the bridge (10 points): 1,055 cells missing. | [ran] probe `fill` |
| Keyhole with AA fill on | Opacity on the bridge is **0.750** against 1.000 in solid ink. The fringe runs along both sides of the slit, so it shows as a visible hairline. | [ran] probe `seam` (composited from vertex alpha, not rendered on a GPU) |
| Self-intersection | Pentagram (5 points): 3 triangles. Against nonzero: 1,237 cells inked outside the fill and 2,520 cells covered twice. Against even-odd: 2,116 cells wrongly inked. **Not usable.** | [ran] probe `fill` |
| O(N²) concave cost | Convex input to concave fill: about 170 ns per point, flat up to N=4,000. A spiky star (every other vertex reflex): 0.12 ms at N=250, 1.7–1.8 ms at N=1,000, **27–29 ms at N=4,000**. | [ran] probe `throughput` |
| Winding and AA fringe | "Filled shapes must always use clockwise winding order. The anti-aliasing fringe depends on it." | [read] stub |
| Thin-stroke anti-aliasing | Vertices and triangles per segment, `AddPolyline`. **No AA:** 4 vertices, 2 triangles at every thickness. **AA lines, no texture:** 3 vertices, 4 triangles at ≤1.0; 4 vertices, 6 triangles above that, with a fringe of alpha-0 vertices. **AA lines with `use_tex` (the default style):** 2 vertices, 2 triangles at 0.5, 1, 2 and 4; at 1.5 it falls back to the geometric fringe (4/6). So integer widths use baked line textures, which need bilinear filtering per the stub. **What it looks like on screen was not measured**, because there was no GPU render. | [ran] probe `strokes`; [read] stub `anti_aliased_lines_use_tex` |
| Curve flattening | Controlled by `style.curve_tessellation_tol` (1.25 by default) and `circle_tessellation_max_error`. Both are global style settings, not per call. | [ran] style read; [read] stub |
| `ImDrawIdx` width | `INDEX_SIZE == 4`, `VERTEX_SIZE == 20`, `idx_buffer` is `ImVector_uint`. A 100,000-point polyline (400,008 vertices) added **0 draw commands**, with a maximum index of 400,007. **32-bit.** | [ran] probe `sizes` |
| 1.92 texture system | `ImTextureData`, `ImTextureRef`, `ImTextureStatus`, `BackendFlags_.renderer_has_textures`, `imgui.internal.register_user_texture(ImTextureData)`. The atlas is dynamic and the backend creates and updates textures. For vectors this matters only to the raster fallback and the glyph atlas. | [read] stubs; [ran] probe sets `renderer_has_textures` and runs NewFrame |
| What changes in 1.93.0 (2026-10-14), not bundled here | Reworked stroke expansion with "corner miter calculation". Adds `ImDrawFlags_JoinMiter`, `CapSquare`, `AALineEnds`, `StrokeInside/Center/CenterBiased/Outside/Legacy`, and `PushDrawFlag`/`PopDrawFlag`. `AddLine` loses its +0.5 offset. `ImDrawListFlags` merges into `ImDrawFlags`. `CurveTessellationTol` becomes `CurveTessellationMaxError`. Convex fill handles sharp corners and miter. **No change to concave fill or holes** in the part of the page that was readable. 1.92.8 had already reordered trailing parameters of `AddRect`, `AddPolyline` and `PathStroke`. | [read] github.com/ocornut/imgui/releases/tag/v1.93.0 (the page was read in two parts and the tail was showcase text only) |

## 2. What imgui-bundle 1.92.900 ships

Python submodules [ran `dir(imgui_bundle)`]: `hello_imgui`, `imgui`, `immapp`,
`immvision`, `implot`, `implot3d`, `imgui_node_editor`, `nanovg`, `imgui_md`,
`imgui_microtex`, `imgui_fig`, `imgui_tex_inspect`, `imguizmo`, `imspinner`,
`imgui_knobs`, `imgui_toggle`, `im_anim`, `im_cool_bar`, `imgui_color_text_edit`,
`imgui_command_palette`, `im_file_dialog`, `portable_file_dialogs`,
`imgui_explorer`.

| Component | Vector-shaped? | Usable inline in a pane as vectors? | Basis |
|---|---|---|---|
| **nanovg** | Full path API: even-odd fill, strokes, gradients, fontstash text. `nvgBeginFrame` and the `NVGcontext` symbols are linked in. | **No.** `nvg_imgui` offers only `render_nvg_to_frame_buffer`, which draws into an `NvgFramebuffer` whose `texture_id` goes to `imgui.image`, or `render_nvg_to_background`, which draws behind the whole viewport. **No SVG loader** (no `nsvg` symbols). The README says it needs a stencil buffer and is "not actively maintained". This **confirms `svg-renderers.md`**. | [read] `nanovg.pyi` `class nvg_imgui`; [ran] `nm -D`/`strings` on `_imgui_bundle…so`; [read] github.com/memononen/nanovg |
| **ImPlot** | Plots. `get_plot_draw_list()` returns an `ImDrawList`. | Only "between Begin/EndPlot". It has no primitives of its own outside a plot. Data graphs would be plots, not display-list items. | [read] `implot/__init__.pyi` |
| **ImPlot3D** | Same as ImPlot. `get_plot_draw_list`, `plot_to_pixels`. | Inside a plot only. | [read] `implot3d/__init__.pyi` |
| **imgui-node-editor** (with `imgui_canvas` inside it) | A zoomable canvas, Bézier links, `get_node_background_draw_list`, `get_hint_*_draw_list`, `flow()` animation. `ImGuiEx::Canvas` is linked in (29 strings) but exposed only through the editor (`suspend/resume_editor_canvas`). | It is an editor for interactive graphs, not a renderer for a static diagram. It is prior art for zoom and pan on an `ImDrawList` canvas. | [read] `imgui_node_editor.pyi`; [ran] `strings`, `nm -DC` |
| **plutosvg 0.0.7** | Raster SVG, linked for FreeType OT-SVG glyphs. | No (see `svg-renderers.md`). `plutovg` and `lunasvg` strings: 0. | [ran] `strings`, `nm -D` (11 `plutosvg` symbols) |
| **imgui_microtex** | LaTeX through MicroTeX and FreeType. | Raster: "render LaTeX to an RGBA pixel buffer" or a cached `TextureGpu`. | [read] `imgui_microtex.pyi` |
| **imgui_md** | Markdown. In 1.92.900 it already carries RichMd's `with_latex` option. | **No Mermaid**: no `flowchart`/`sequenceDiagram`/`classDiagram` strings in the `.so`. | [read] `imgui_md.pyi`; [ran] `strings` |
| **immvision**, **imgui_fig**, **imgui_tex_inspect** | Image viewers. `imgui_fig` renders Matplotlib through Agg offscreen. | Raster. | [read] `immvision.pyi`, `imgui_fig.py` |
| **imspinner**, **imgui_knobs**, **imgui_toggle**, **imguizmo** | Widgets drawn with `ImDrawList` primitives. | Not reusable as a drawing layer. They are examples of the same primitives. | [read] module list; drawing internals [unverified] |
| Triangulators, stroke expanders, MSDF | `earcut`, `tessNew`, `thorvg`, `msdf`, `blend2d`, `Skia`, `nsvg`: 0 strings each. `stbtt`: 59 (stb_truetype). | None shipped. | [ran] `strings`, `nm -D` |
| **imgui-bundle 1.93.0** (not installed) | Adds `imgui_bundle.rich_md` (`imgui_md` stays as an alias) with **native Mermaid**. Moves to ImGui 1.93.0. Makes hello_imgui's plutovg and plutosvg submodules. | It would be prior art for Mermaid, behind a bundle bump. | [read] api.github.com/repos/pthom/imgui_bundle/releases (published 2026-10-21T17:56Z) |

## 3. Third-party ImGui vector projects

| Project | Licence | Maintenance | Draw path | SVG coverage | Fit with the pinned wheel | Basis |
|---|---|---|---|---|---|---|
| **imgui_rich_md** (pthom) | MIT | v1.0.0 is the first release. Active as of today. | **`ImDrawList`**, "no web view". Edges are straight segments with right angles. It uses its own layout, which "will not match Mermaid's exact look". | None. Mermaid flowchart, sequence and class only. Themes and `%%{init}%%` are ignored. Other diagram types are shown as code. | In Python as `imgui_bundle.rich_md` from **1.93.0**. Not in 1.92.900. | [read] github.com/pthom/imgui_rich_md, docs/mermaid.md. That it draws with `AddConcavePolyFilled`/`AddPolyline`/`AddText` and no Bézier: [read, delegated] `mermaid_draw.cpp` |
| **ThorVG** | MIT | v1.1.3, 2026-10-15 | The CPU engine scanline-rasterises into a buffer. The GL engine's `BWTessellator` and `Stroker` write vertex and index triangle buffers. Whether those could feed `ImDrawList`: [unverified]. **No ImGui integration found.** | "Most of SVG Tiny", static | C++ only. The one Python binding, `thorvg-python` 1.1.3, is **LGPL-2.1** (ctypes over the C API). | [read, delegated] github.com/thorvg/thorvg, `tvgGlTessellator.h`, pypi.org/pypi/thorvg-python |
| **ImSvg** (boxDev2008) | MIT | 6 commits, 0 stars, pushed 2026-02 | `ImDrawList`: its own XML parser, ear clipping into `PrimReserve`, strokes through `PathStroke` | Basic. Exact coverage [unverified]. | C++ only. Too small to depend on. | [read, delegated] github.com/boxDev2008/ImSvg |
| **vg-renderer** (jdryg) | BSD-2 | No releases | bgfx GPU renderer "based on ideas from NanoVG and ImDrawList". Optional libtess2. **No polygon holes.** | None built in | C++ with bgfx: a separate GPU path, a poor fit | [read, delegated] github.com/jdryg/vg-renderer |
| **imnodes**, **ImNodeFlow** | MIT | pushed 2026-10 / 2026-05 | Node editors. Draw path [unverified]. | None | Not a renderer | [read, delegated] GitHub API |
| **Skia bridges** (imgui-skia-example, imgui-imzero-custom, MFEK imgui-skia-renderer) | Skia is BSD-3 | 2020 / 2025 / 2022 WIP | Skia renders separately. None feeds `ImDrawList`. | Skia's SVGDOM | A separate GPU context: wrong shape | [read, delegated] GitHub search; skia LICENSE |
| **Blend2D + ImGui** | — | None found | — | — | — | [read, delegated] web search |
| **MSDF for ImGui** | — | A GitHub search for "imgui msdf" returned 0 repos. An open imgui issue asks about SDF/MSDF. | — | — | Nothing to adopt. Slug-style GPU text [unverified]. | [read, delegated] GitHub search API |
| **nanosvg** (parser only) | zlib | Last commit 2026-07. README says "not actively maintained". | Outputs "a list of cubic bezier shapes" with the viewBox applied. Its own rasteriser does flat fills only. | Subset. Coverage list [unverified]. **No `<text>`** [unverified]. | C++ header. Not needed: the Python front end parses. | [read, delegated] github.com/memononen/nanosvg |

**Verdict [inferred from the rows above]:** no project draws general SVG into
`ImDrawList` with correct fills. The only serious native-vector prior art is
`imgui_rich_md`, and it covers Mermaid only, with its own look.

## 4. Triangulation and stroking libraries

| Library | Licence | Size | Holes / self-intersection | Fill rules | Basis |
|---|---|---|---|---|---|
| **earcut.hpp** | ISC | header-only | Holes: yes. It assumes "non-crossing rings… no duplicate edges", and violating that can give overlaps, gaps or triangles outside the polygon. The README itself points to libtess2 for guaranteed correctness. | **None stated.** The front end would have to resolve nonzero/even-odd into rings first. | [read] github.com/mapbox/earcut.hpp |
| **libtess2** | **SGI Free Software License B v2.0** (FSF-free, permissive, not MIT) | small C library, LOC [unverified] | Self-intersection: yes. "New vertices generated at the intersections of segments are assigned value TESS_UNDEF". Holes come from the winding rule. | `ODD`, `NONZERO`, `POSITIVE`, `NEGATIVE`, `ABS_GEQ_TWO`. Covers both SVG rules. | [read] github.com/memononen/libtess2, `Include/tesselator.h`. README: "minimally maintained" |
| **CDT** (artem-ogre) | MPL-2.0, with bundled BSD-3 and Apache-2.0-LLVM parts | header-only or compiled | Holes detected automatically. Intersecting constraints rejected by default; `TryResolve` splits them and can fail when nearly degenerate. | None stated | [read, delegated] github.com/artem-ogre/CDT. 2.0.1 released 2026-10-13 |
| **NanoVG** `expandStroke`/`expandFill` | zlib | part of `nanovg.c` | Fills use the stencil buffer ("must have stencil buffer"), so `expandFill` output is not a triangulation that `ImDrawList` could draw without a stencil [inferred from README]. Stroke expansion with joins and caps is the reusable part; its internals [unverified]. | even-odd (README) | [read] github.com/memononen/nanovg |
| **ThorVG GL** `Stroker` / `BWTessellator` | MIT | internal to the engine, not standalone | Outputs triangles. Self-intersection [unverified]. | [unverified] | [read, delegated] `tvgGlTessellator.h` |
| ThorVG SW stroker (`tvgSwStroke.cpp`) | MIT | internal | Outputs an outline (move, line, cubic) under NonZero, not triangles | NonZero | [read, delegated] |
| **Skia** `SkStroke` | BSD-3 | Pulls in Skia. Cost [unverified], large. | — | — | [read, delegated] skia LICENSE |
| **Polyline2D** (CrushedPixel) | MIT | header-only C++17 | Stroke mesh only. Overlapping segments are filled twice, so translucent strokes double up. | n/a | [read, delegated] github.com/CrushedPixel/Polyline2D |
| **ImGui 1.93 stroker** | MIT | built in | Miter joins, square caps, alignment, AA ends. No round join was named in the readable notes. | n/a | [read] ocornut/imgui v1.93.0 release |

## 5. Text: how imtypeset draws, and whether display-list text can use it

| Item | Finding | Basis |
|---|---|---|
| Pin | `imtypeset.ref` = `0bac066…`. The installed wheel's `_attestation.json` names the same commit and `imtypeset_version` 0.4.3, built against the bundle's `_imgui_bundle…so` (sha256 `56427434…`). `~/Source/imtypeset` HEAD is `4f887d3`, newer than the pin. Everything below was read at the pinned commit (`git show 0bac066:src/draw.h`). | [ran] `git cat-file`, `git log`; [read] attestation |
| How glyphs reach `ImDrawList` | Each glyph becomes the quad one `ImDrawList::AddText` would make, from ImGui's atlas at the baked pixel size, written straight into `PrimReserve`d space in batches of up to 1,024 quads. "Nothing is resampled." Positions are pixel-snapped per rigid span (`snap_px`), and the baseline is ImGui's baked ascent. There is also a second output, `draw_to_buffers` (vertices and indices in caller-owned buffers). | [read] `src/draw.h` header comment, `src/draw.cpp` `ListSink`, `BATCH_QUADS` |
| Entry points from Python | `ts.text(...)`. `Paragraph(text, runs).draw(draw_list_addr, pos, selection)` draws at a given top-left. `Paragraph.draw_to_buffers(pos, clip_rect)`. `Run(start, end, face, scale, color, flags, tag)`. `calc_text_size`, `glyph_advance`, `device_glyph_advance`, `font_metrics`. | [read] `.venv/.../imtypeset/__init__.py` (`__all__`, `Paragraph.draw`), `_capi.py` `Run` |
| Could display-list text runs use it? | **Yes, for axis-aligned runs in imscribe's faces.** Use `font_metrics` and `calc_text_size` to resolve `text-anchor` and `dominant-baseline` in the front end, then `Paragraph.draw(dl, (x, top))`. Mermaid labels and graph axes are all horizontal, so this covers them. **No:** rotated or skewed text, `textPath`, and text under a non-uniform scale. The quads are axis-aligned atlas bitmaps snapped to the pixel grid. Those runs would need glyph outlines as filled paths (fontTools, as imscribe's `backends/svg.py` does). Outlines have counters, so they need the hole-capable fill from §4, or the raster fallback. | [inferred] from the rows above |
| Zoom | Atlas glyphs are baked per pixel size, so a zoomed diagram re-asks ImGui at a new size. That works because 1.92's atlas is dynamic, but each new size bakes new glyphs. | [inferred] from `draw.h` "the size asked of ImGui is the size it bakes" |

## What this changes for the display-list design

These are [inferred] from the measurements and reads above, unless marked.

1. **The GPU side is done. The back end is a geometry library.** Dear ImGui
   already takes arbitrary triangles with 32-bit indices, so a single
   `PrimReserve` can carry a whole diagram [ran]. The C++ back end never needs
   its own GL path, FBO or stencil. Anything that needs one is ruled out: NanoVG
   fills, Skia, ThorVG's GL engine, vg-renderer.

2. **Fills:**
   - **Diagram shapes stay in Python.** For what Mermaid and graph output mostly
     contain (rects, rounded rects, circles, ellipses, arrowheads, convex and
     simple concave polygons), ImGui's own `Add*Filled` and `PathFill*` are
     correct already. Keep those as **display-list primitives**: do not flatten
     them to generic paths.
   - **Real SVG paths need a triangulator in C++.** That means `fill-rule`,
     subpaths and holes, and glyph outlines. `AddConcavePolyFilled` cannot carry
     them:
     - Self-intersection is garbage.
     - Keyhole holes leave a 0.75 seam under AA.
     - It is O(N²): 27 ms at 4,000 reflex-heavy points.
   - **Candidate: libtess2.** It is the one library with nonzero and even-odd
     rules and intersection handling. Its SGI-B licence needs a sentence in
     THIRD-PARTY. earcut is the light alternative only if the front end
     guarantees simple, non-crossing rings, which SVG from a model will not.
   - **The C++ back end must emit its own AA fringe.** libtess2 output has none,
     and ImGui's fringe on a keyhole seams. The usual approach is an outward
     1-px alpha-0 fringe on the *contour*, not on the triangles. This is the
     non-trivial part of "tessellate".

3. **Strokes: decide the ImGui version first.**
   - **On 1.92.9:** `PathStroke` has no joins, caps or dashes, so SVG
     `stroke-linejoin`/`linecap`/`miterlimit` need our own stroker.
   - **On 1.93:** ImGui provides miter joins, square caps and alignment
     [read]. Round joins and caps could be approximated by path arcs.
   - **Recommendation:** raise the **bundle and imtypeset rebuild to 1.93** as a
     separate, sized decision before writing a stroker. A stroker is real work,
     and 1.93 deletes most of it. Dashes are ours either way: split the path in
     the front end.

4. **Mermaid: compare against `rich_md` before building a Mermaid front end.**
   - **Why it matters:** it already does in C++ what the Mermaid half of this
     design proposes: parse, lay out, and draw into `ImDrawList` with theme
     colours.
   - **What it lacks:** it is behind the same 1.93 bump, covers only 3 diagram
     types, has its own look, and has no display list. So no hit-testing beyond
     what it exposes [unverified], and no SVG.
   - **The question to settle:** if we bump to 1.93 anyway, is "Mermaid through
     `rich_md`, SVG and graphs through our display list" cheaper than one
     display list for everything? It costs us layout parity with mermaid.js.
     The probe that settles it: render the same five diagrams `mermaid.md`
     uses through `rich_md` on 1.93.0. That needs an install, so it is an
     operator-run step.

5. **Python must hand over arrays, not points.**
   - **Tuples:** about 150–280 ns a point, converted every call.
   - **Cached `ImVec2` lists:** 10–16 ns a point [ran].
   - **Triangles written from Python with `prim_vtx`:** about 740 ns per input
     point [ran].
   - **So:** the display list should cross into C++ **once per change** (a
     packed buffer), and the per-frame call should be "draw display list *h* at
     *origin*, *scale*", the same shape as imtypeset's `Paragraph.draw`.
     Re-tessellation is needed only when the scale changes, since curves are
     flattened against pixel tolerance. A diagram of about 10k points then costs
     about 0.1 ms a frame to resubmit [inferred from the 10k-point
     `ImVec2`/convex rows].

6. **Text runs go through imtypeset, unchanged, when they are horizontal.**
   - **What that buys:** labels then share the prose's faces, snapping and
     atlas.
   - **Rotated text and `textPath`:** keep the raster path, or later route them
     as outline fills through the triangulator.
   - **Fit with imtypeset:** if the back end lives inside imtypeset, it already
     has the `ListSink`/`PrimReserve` machinery and the `draw_to_buffers`
     pattern to copy for fills.

7. **Unchanged from `svg-renderers.md`:** nanovg is not an inline route, and no
   shipped component parses SVG. The raster path stays the fallback for filters,
   masks, patterns, gradients and complex text.

## Not done in this task

- **No GPU render:** what thin lines and AA fills look like on screen was not
  measured. The probe reads geometry and vertex alpha only. A pixel check would
  need `scripts/with_display.sh` plus `glReadPixels`.
- **Nothing installed:** imgui-bundle 1.93.0 and `rich_md` were not installed or
  run.
- **Sizes [unverified]:** library line counts and binary sizes in §4.
- **Delegated reads not re-fetched:** rows marked [read, delegated]. They are the
  sub-agent's page reads. I re-fetched only the claims marked [read]: the
  rich_md README and mermaid.md, the bundle releases, the ImGui 1.90.5 and
  1.93.0 release notes, earcut.hpp, libtess2 and its header, and nanovg.

## Reproduce

    .venv/bin/python scripts/imgui_vector_probe.py

It runs headless in about 1.5 s, with no display. Timings are medians of 7 frames
on one machine, so read them as ratios. Output for the [ran] rows above came from
this script as it stands beside this note.

## Sources

- Venv: `imgui_bundle/imgui/__init__.pyi`, `imgui/internal.pyi`, `nanovg.pyi`,
  `implot/__init__.pyi`, `implot3d/__init__.pyi`, `imgui_node_editor.pyi`,
  `imgui_md.pyi`, `imgui_microtex.pyi`, `immvision.pyi`, `imgui_fig.py`;
  `nm -D`/`strings` on `_imgui_bundle.cpython-311-x86_64-linux-gnu.so`;
  `imtypeset/__init__.py`, `_capi.py`, `_attestation.json`.
- `~/Source/imtypeset` at `0bac06608be358aec7cf9a3ee72c6abc623146e3`:
  `src/draw.h`, `src/draw.cpp`.
- https://github.com/ocornut/imgui/releases/tag/v1.90.5 ·
  https://github.com/ocornut/imgui/releases/tag/v1.93.0
- https://api.github.com/repos/pthom/imgui_bundle/releases ·
  https://github.com/pthom/imgui_rich_md ·
  https://github.com/pthom/imgui_rich_md/blob/main/docs/mermaid.md
- https://github.com/mapbox/earcut.hpp · https://github.com/memononen/libtess2 ·
  https://github.com/memononen/libtess2/blob/master/Include/tesselator.h ·
  https://github.com/memononen/nanovg
- Delegated: github.com/thorvg/thorvg (and `tvgGlTessellator.h`, `tvgSwStroke.cpp`),
  pypi.org/pypi/thorvg-python, github.com/boxDev2008/ImSvg, github.com/jdryg/vg-renderer,
  github.com/artem-ogre/CDT, github.com/CrushedPixel/Polyline2D,
  github.com/memononen/nanosvg, GitHub search API ("imgui svg", "imgui skia",
  "imgui msdf"), skia.googlesource.com/skia LICENSE.
