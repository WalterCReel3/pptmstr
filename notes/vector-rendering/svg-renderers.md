# Rendering script-free SVG inside the imgui-bundle app

Research for board task `svg`, 2026-10-18. No source was changed. This is the
rendering backbone for `mermaid.md` and `data-graphs.md` in this directory:
both of them end in SVG, and this note says how that SVG becomes pixels in an
imscribe pane.

**How each claim is marked.**
- **[ran]**: a command was executed against the project venv
  (`/home/wreel/Source/pptmstr/.venv`, CPython 3.11.2, imgui-bundle 1.92.900).
- **[read]**: established by reading the named file or URL.
- **[inferred]**: reasoning from those, with nothing read that states it.
- **[unverified]**: recalled or plausible, and not checked.

## Summary

- **imgui-bundle ships no SVG renderer that Python can use.** plutosvg 0.0.7
  is linked in for OpenType SVG glyphs, and 11 of its symbols are even
  exported. The plutovg surface functions you would need to read or free its
  output are not exported. nanovg is a path API with no SVG parser.
  Everything else in the bundle draws raster images or polygons.
- **Recommendation: rasterise to a texture, behind a one-function seam.**
  `rasterise(sanitised_svg, px_width, font_files) -> png | rgba`. Start with
  **resvg** (`resvg_py` 0.5.0). Keep **novasvg** 0.11.3 as the measured
  alternative: it was about 8.5× faster on a text-heavy diagram and draws
  `<foreignObject>`, but it is days old on PyPI.
- **Before either renderer: our own allow-list sanitiser.** It is required.
  The operator-run probe showed **both renderers read local files from disk**
  through `<image href="/abs/path">`.
- **Fonts:** load imscribe's own font files (`font_files=`,
  `skip_system_fonts=True`) **and map every generic family to them.**
  Measured: without the mapping resvg draws no `<text>` at all, and reports no
  error.
- **Texture:** upload with `hello_imgui.create_texture_gpu_from_rgba_data`,
  which needs **numpy**. The cost is two new runtime dependencies (`resvg_py`,
  `numpy`) plus a sanitiser we own and test. The no-numpy fallback is
  `hello_imgui.image_and_size_from_encoded_data(png, key)`. It has a real
  defect for this use: its cache cannot drop one entry.
- **Render cost (measured): resvg takes about 445 ms** for 1,200 rects and
  1,200 labels at 800×600, and 680 ms at 2×. novasvg takes 52 ms. Rendering
  has to happen off the GUI thread whichever renderer is used.
- **Tessellating into `ImDrawList` is not viable as the backbone.** `ImDrawList`
  fills only simple polygons: no holes and no self-intersection. Every glyph
  with a counter (o, e, 8) and every even-odd path breaks that. There is also
  no maintained pure-Python SVG parser with text layout to feed it.

## A. What imgui-bundle 1.92.900 already ships

| Candidate | What is there | Usable from Python for SVG? | Basis |
|---|---|---|---|
| **plutosvg** (0.0.7) | Linked into `_imgui_bundle.cpython-311-x86_64-linux-gnu.so` as the FreeType OT-SVG hook (`plutosvg_ft_svg_hooks`). Exported dynamic symbols: `plutosvg_document_load_from_data`, `_render`, `_render_to_surface`, `_extents`, `_destroy`, `_get_width/height`, `plutosvg_version_string`. `ctypes` call: `plutosvg_version_string()` returns `b'0.0.7'`. | **No.** None of the `plutovg_*` symbols are exported (`nm -D` count 0; `ctypes` getattr fails for `plutovg_surface_create`, `plutovg_surface_get_data` and `plutovg_surface_destroy`). `render_to_surface` returns a `plutovg_surface_t*` that cannot be read except by hard-coding the struct layout, and cannot be freed. That is ABI-fragile and leaks. There are no Python bindings. Whether the macOS wheel exports the same symbols is unknown. Upstream does not list its SVG feature set, and `<text>` support is not stated. It is designed for "SVG documents embedded in OpenType fonts". | [ran] `nm -D`, `strings`, `ctypes` on the .so. [read] github.com/sammycage/plutosvg (MIT; lists Dear ImGui as a user). macOS: [unverified] |
| **lunasvg** | Absent: no `lunasvg` strings in the .so. | No | [ran] `strings` |
| **nanosvg** | Absent: no `nsvg` symbols. | No | [ran] `nm -D`, `strings` |
| **nanovg** (`imgui_bundle.nanovg`) | A full path API: `move_to`, `bezier_to`, `arc_to`, `path_winding` (holes), `fill`, `stroke`, gradients and fontstash text (`create_font`, `text`, `text_box`). `nvg_imgui.NvgFramebuffer` and `render_nvg_to_frame_buffer(vg, fb, draw_fn)` render into a texture whose `texture_id` goes to `imgui.image`. `render_nvg_to_background` draws only behind every window. | **No SVG parser.** It is a backend we would have to feed from our own parser. To appear inline in a pane it still goes through a framebuffer **texture**. In this stack, "vector stays vector" through nanovg is a texture re-rendered on zoom, the same lifecycle as resvg's raster. | [read] `imgui_bundle/nanovg.pyi` (`class nvg_imgui`, `NvgFramebuffer`) |
| **`ImDrawList` path API** | `path_line_to`, `path_bezier_cubic_curve_to`, `path_bezier_quadratic_curve_to`, `path_elliptical_arc_to`, `path_fill_convex`, `path_fill_concave`, `add_concave_poly_filled`, `path_stroke`, `add_image`. | Only for shapes we generate ourselves. The stub says: "Only simple polygons are supported by filling functions (no self-intersections, no holes)" and "Concave polygon fill ... has O(N^2) complexity". | [read] `imgui_bundle/imgui/__init__.pyi`, class `ImDrawList` |
| **hello_imgui image loading** | `image_and_size_from_encoded_data(data: bytes, cache_key="")` decodes PNG/JPEG/BMP/GIF. `free_image_cache()` clears **the whole** cache it shares with `image_from_asset` and imgui_md. | Displays a PNG that something else rendered. SVG is not among the listed formats. | [read] `hello_imgui.pyi` |
| **`hello_imgui.TextureGpu`** | `create_texture_gpu_from_rgba_data(rgba: np.ndarray) -> TextureGpu`: an RAII handle whose GPU texture is freed when the last Python reference drops. It is backend-agnostic (OpenGL, Metal and others) and must be created on the GUI thread. | It is the right owner for a texture we rasterise, but it **requires numpy**, which is not installed. | [read] `hello_imgui.pyi`. [ran] `import numpy` gives ModuleNotFoundError in the project venv |
| **`imgui.ImTextureData` + `imgui.internal.register_user_texture`** | The ImGui 1.92 path for user textures managed by the backend. | Pixel access is `get_pixels_array()`. [ran] It raises `TypeError: could not export nanobind::ndarray: ... No module named 'numpy'`, so this path also needs numpy. | [ran] |
| **immvision** | Image display widget | Takes numpy arrays. Its value over `TextureGpu` here is zoom and pan inspection, which is not needed. | [read] stub list. [inferred] |
| **imgui_md** | Has an image loader (`_imgui_md_image_loader.py`) that **downloads http(s) image URLs** in a thread pool. | pptmstr does not use imgui_md (`pptmstr/ui/inline.py` says so). Noted because it shows that the bundle's own markdown would fetch from the network. | [read] |
| **imscribe `backends/svg.py`** | An SVG **writer**: glyphs become `<path>` through fontTools `SVGPathPen`. | Output only. It is relevant to option C below: it shows imscribe can already turn its faces into outline paths. | [read] the module docstring |
| **PyOpenGL** | In the venv as the `dev` extra (`pyopengl>=3.1`), used by scripts for `glReadPixels`. | It could upload textures with `glTexImage2D` from `bytes`, so no numpy is needed. It is a dev extra, not a runtime dependency, and works on the OpenGL backend only. | [read] `pyproject.toml` |

**Conclusion for A [inferred from the rows above]:** nothing shipped will parse
and render an SVG for us. What *is* shipped and worth using is the texture
plumbing: `TextureGpu` (with numpy) or the encoded-data cache (without it).

## B. Options that rasterise to a texture

| Library | License | Wheels for Linux x86-64 glibc≥2.28 and macOS arm64, CPython 3.11–3.14 | `<text>` / fonts | `<foreignObject>` | Script / external fetch | Maintenance | Basis |
|---|---|---|---|---|---|---|---|
| **resvg** via **`resvg_py` 0.5.0** | Binding: MIT. resvg: MIT OR Apache-2.0. | **Yes.** `cp310-abi3` wheels for `manylinux_2_17_x86_64` and `macosx_11_0_arm64` cover 3.11 and later; there is also `cp314t`. | Yes, with its own text stack (no system libraries). `font_files`, `font_dirs`, `skip_system_fonts`, `font_family`, `sans_serif_family` and the other family options are exposed. | **Not in usvg's element enum**, so the element is not drawn [read]. **Measured: an XHTML label in it draws nothing**, so a Mermaid HTML label comes out blank [ran]. | **Script:** not in the element enum. resvg's README excludes "`script`, `view` or `cursor` elements, no events". **Network:** "usvg doesn't do any network requests". **Local files: the default `ImageHrefResolver` opens string hrefs as file paths, absolute paths included.** `resvg_py` exposes no way to replace the resolver. **Measured: `<image href="/abs/path.png">` and a relative href under `resources_dir` are read from disk and drawn; `file://` and `http://` are ignored** [ran]. The sanitiser has to remove them (see D). | resvg: linebender, about 1,600 tests in its suite. `resvg_py`: one maintainer, 27 stars, 342 commits, "feature complete" per its author. Thin. | [read] pypi.org/project/resvg-py, resvg-py.readthedocs.io/en/latest/api.html, github.com/linebender/resvg README, github.com/baseplate-admin/resvg-py, docs.rs/usvg `ImageHrefResolver`, usvg `parser/svgtree/names.rs` |
| **novasvg 0.11.3** (lunasvg-derived C++) | MIT | **Yes.** `cp311-cp311` and `cp312-abi3` for `manylinux_2_27/2_28_x86_64` and `macosx_10_15_universal2`. | Yes. The font stack resolves registered, then system, then substitutes, with an embedded Latin DejaVu subset as the last resort. Font registration is documented in C++. Whether Python exposes it is [unverified]. | **Draws `<foreignObject>` text with word wrap.** This is the only candidate that does, and it matters for Mermaid's default `htmlLabels`. | Script: not mentioned. `<image>`: base64 data URIs are documented. Whether it opens file paths is [unverified]. | Very young: 0.11.1 was uploaded 2026-10-17. One maintainer (mohammadraziei). It churns fast. | [read] pypi.org/simple/novasvg, pypi.org/pypi/novasvg/json |
| **CairoSVG** | LGPL-3.0 | Pure Python, but needs **system libcairo** through cairocffi. That is a non-Python runtime requirement of the same kind `scripts/probe.py` already has to chase for X11. | "Text and features related to fonts are poorly supported". There is no `@font-face`, and it uses system fonts through FreeType. | [unverified] | Scripting "not and will not be implemented". Before 2.7.0 it fetched external URLs by default (CVE-2023-27586, SSRF, CVSS 9.9). From 2.7.0 that is opt-in through `unsafe` / `url_fetcher`. defusedxml guards XXE unless `unsafe`. | Maintained (Kozea) | [read] cairosvg.org/documentation, osv.dev CVE-2023-27586 |
| **skia-python 144.0.post2** | BSD-3 | Yes: cp311–cp314 for manylinux x86_64 and macOS arm64, about 12–14 MB each. | `skia.SVGDOM` exists. How far its text goes is [unverified]. A 2023 report found the binding "stuck at m87" and lacking `renderNode`, but 144 is far newer. | [unverified] | [unverified] | Active | [read] pypi.org/project/skia-python, lists.aau.at mpeg-otspec 2023-07 |
| **pyvips** (`pyvips[binary]`) | pyvips: MIT. libvips: LGPL. librsvg: LGPL. | Binary wheels exist for Linux and macOS. **Whether they include librsvg `svgload` is [unverified]**: the README says only that it is "missing features like PDF load". | librsvg has good text (Pango). | librsvg ignores it [unverified]. | librsvg loads no scripts. Its external resource policy is [unverified]. | Active | [read] github.com/libvips/pyvips |
| **PySide6 / QtSvg** | LGPL-3 / GPL-2 / commercial | Wheels exist [unverified sizes; on the order of 100+ MB, recalled]. | Qt's own. Scope is "SVG 1.2 Tiny static" [unverified, recalled]. | No [unverified] | `QtSvg::Option` flags exist from Qt 6.7. Their contents are [unverified]. | Active | [read] doc.qt.io qtsvg-index, qsvgrenderer |

### The three features Mermaid output depends on

The Mermaid task's operator-run probe (relayed by the lead, not checked here)
found that merman 0.9.1 and mermaidx 0.10.0 emit node labels only as
`<foreignObject>`, while mmdr 0.4.0 emits `<text>`. All four packages use
`<marker>`, and most use a `<style>` block.

| Renderer | `<foreignObject>` | CSS `<style>` | `<marker>` | Basis |
|---|---|---|---|---|
| resvg 0.4x | **No**: not in usvg's element enum | **Yes, minimal.** It goes through simplecss ("CSS 2.1 parser and selector", at-rules skipped). The resvg test-suite table gives about 88% for the style section and 100% for style attributes. "Extended CSS" is listed as not implemented. | **Yes**: about 98% of the test-suite section | [read] names.rs, docs.rs/crate/simplecss, linebender.org/resvg-test-suite/svg-support-table.html (0.40.0 column) |
| novasvg 0.11.3 | **Yes, text with word wrap only.** No HTML layout. | [unverified] Upstream lunasvg 3 has CSS selectors, recalled. | [unverified] lunasvg has markers, recalled. | [read] pypi.org/pypi/novasvg/json |
| CairoSVG | [unverified] | Yes: "styling works through both XML attributes and CSS" | [unverified] | [read] cairosvg.org/documentation |
| skia SVGDOM, librsvg, QtSvg | [unverified] for all three | [unverified] | [unverified] | none |
| `ImDrawList` (our own parser) | Ours to write | Ours to write | Ours to write | [inferred] |

The pure-Python parsers, for the tessellation route:

| Library | Notes | Basis |
|---|---|---|
| **svgelements 1.9.6** | MIT, single file, no required dependencies. Parses to path segments and transforms. **The last release was 2023-08-17.** It does no text layout. | [read] pypi.org/project/svgelements |

**Why resvg over novasvg [inferred].** resvg has the larger conformance suite.
Its security posture is documented: no network, no script, and a known and
fixable local-file behaviour. resvg also has the larger user base. The thin
part is the `resvg_py` binding, and an exact pin covers that.

novasvg's `<foreignObject>` support is a real advantage. The Mermaid note,
though, already finds routes that emit `<text>` (`mermaid.md`: merman
`resvg_safe`, `htmlLabels:false`). A renderer one day old on PyPI should not
be the backbone. Keep novasvg as the named alternative if the Mermaid route
ends up needing `<foreignObject>`.

## C. Tessellating to `ImDrawList` versus rasterising to a texture

| | Tessellate to `ImDrawList` | Rasterise to a texture (resvg) |
|---|---|---|
| Crisp at any zoom | Yes, by construction | Only if re-rasterised when the zoom, DPI or pane width changes. The output is cached by `(svg hash, pixel width, dpi)`. |
| Fill correctness | **Breaks on holes and self-intersection** (stub, verified). Real SVG needs nonzero and even-odd fill with subpaths. We would have to write a triangulator, for example ear-clipping with hole bridging. | resvg's anti-aliased scanline fill handles both rules. |
| Text inside the SVG | We would lay out `<text>` and `<tspan>` ourselves (anchor, baseline, `dy`), then either draw with ImGui fonts (no rotation; ImGui text sits on the pixel grid, see `notes/2026-10-16-imgui-text-and-the-pixel-grid.md`) or emit outlines with fontTools, which have holes (row above). | resvg lays out the text. **Font matching:** pass imscribe's own files with `font_files=[imscribe.fontfiles.font_file(Face.X) ...]` and `skip_system_fonts=True`. The diagram is then set in the faces the surrounding prose uses, and the result is deterministic across machines [inferred from the API]. The fonts imgui-bundle ships are under `imgui_bundle/assets/fonts` (DroidSans, Roboto, Inconsolata-Medium) [ran `ls`]. |
| Cost per frame | Python loops over every vertex every frame. ImDrawList is immediate, so nothing is retained across frames. For a diagram of about 1,000 elements that is thousands of Python→C++ calls per frame [inferred]. Caching the tessellation still means re-submitting it every frame. | One `imgui.image` call per frame. The cost comes only when something changes. The render for a change is not cheap: **446 ms (resvg) or 52 ms (novasvg)** for 1,200 rects and 1,200 labels at 800×600 [ran, appendix]. |
| GPU lifetime | None | `TextureGpu` is freed when its last Python reference drops. Hold it in the pane's view state and drop it on eviction. The encoded-data route **cannot evict one entry**: `free_image_cache()` drops everything. In pptmstr nothing else uses that cache today ([ran] grep finds no `image_from_asset`, `imgui_md` or `free_image_cache` in `pptmstr/`), so that is coupling and not yet a bug. What an empty `cache_key` does with the texture is [unverified]. |
| Dependencies | svgelements (stale) plus our own triangulator and text layout | `resvg_py` plus numpy, or nothing extra on the encoded-data route |
| Scripting surface | None, since we parse | None from resvg, plus the local-file fix in D |

**Texture API, concretely [read from stubs, not run, because there is no display].**
1. `png = resvg_py.svg_to_bytes(svg_string=..., width=px_w, font_files=[...], skip_system_fonts=True)`
2. Either decode the PNG to RGBA (stdlib `zlib` suffices for resvg's 8-bit
   non-interlaced RGBA output [ran: IHDR depth 8, colour type 6, interlace 0])
   and then call `numpy.frombuffer(...).reshape(h, w, 4)` and
   `tex = hello_imgui.create_texture_gpu_from_rgba_data(arr)`, finally
   `imgui.image(imgui.ImTextureRef(tex.texture_id()), size)`;
3. or call `hello_imgui.image_and_size_from_encoded_data(png, cache_key)` with
   no numpy.

Both must run on the GUI thread. The rasterisation itself can run on a worker
thread, since it is pure CPU. Whether `resvg_py` releases the GIL is
[unverified].

`ImTextureRef` construction from an int id in this binding is [unverified]. The
`imgui.image` signature has to be checked when the code is written.

## D. Sanitisation policy

The input is untrusted model output, so the sanitiser is an **allow-list**
pass we own. It does not lean on any renderer's ignorance of an element.

1. **Reject any `<!DOCTYPE`** outright, before parsing. That closes XXE and
   entity expansion (billion laughs) whatever parser runs. resvg itself
   rejected a 10^7 billion-laughs document at once ("possible entity reference
   loop") [ran]. Our own parse in step 6 runs first, though, and it is the one
   this rule protects.
2. **Element allow-list:** `svg g defs symbol use path rect circle ellipse
   line polyline polygon text tspan textPath linearGradient radialGradient
   stop clipPath mask marker pattern style title desc`. Rewrite
   `foreignObject` to `<text>` (see below). Drop everything else,
   **including `script`, `image`, `a`, `iframe`, animation elements and
   `feImage`**. Whether to allow `filter` is open: blur is a
   CPU-cost vector, and resvg has guards against loops and recursion but none
   stated against cost.
3. **Attributes:** drop every `on*` attribute. Allow `href` / `xlink:href`
   **only as a same-document fragment (`#id`)**, which covers `use`,
   `textPath` and gradients. That removes resvg's local-file read (`<image>`
   is gone, and `use` to an external file is unsupported by resvg anyway
   [read: unsupported.md]).
4. **CSS:** in `style=` attributes and in `<style>`, strip `url(` values that
   are not `url(#id)`, and strip `@import`. Mermaid relies on `<style>` with
   selectors (`mermaid.md`), so `<style>` stays, filtered.
5. **Size limits:** cap the input bytes, the element count, and the output
   pixel dimensions before calling the renderer. resvg sizes the canvas from
   width and height, so a `width="100000"` would allocate a huge buffer.
6. Parse with stdlib `xml.etree.ElementTree` after the DOCTYPE rejection, then
   re-serialise. Nothing fetches during parsing because there is no DTD
   [inferred]. The DOCTYPE check has to be case-insensitive and has to look
   past any XML declaration and comments.

### `<foreignObject>`: rewrite it to `<text>`, do not strip it

Stripping `<foreignObject>` (step 2) would draw Mermaid boxes with no labels,
whenever the Mermaid route emits HTML labels (merman and mermaidx do). Passing
it through means passing XHTML from the model into a renderer. The two fit
together if the sanitiser **rewrites** each `<foreignObject>` into SVG text
instead of deleting it [inferred design, not built or run]:

- Take the text content of its XHTML subtree. Treat `<br>` and block elements
  (`div`, `p`) as line breaks. Discard every element, attribute and style
  inside it.
- Emit `<text x="x+width/2" y="y+height/2" text-anchor="middle"
  dominant-baseline="central">` with one `<tspan x=… dy=…>` per line. Copy the
  parent's `transform` and the `class` on the `foreignObject` (Mermaid hangs
  its label colour and font on it). resvg supports both `dominant-baseline`
  (100%) and `text-anchor` (92%) [read: support table].
- What it loses: wrapping that the HTML would have done inside a fixed width,
  inline markup (bold, links, KaTeX), and exact vertical metrics. Labels are
  approximate. That is the same trade merman's `resvg_safe` makes ("approximate
  `<text>`", per `mermaid.md`).

Doing the conversion in our sanitiser, and not relying on a Mermaid package to
do it, means it does not depend on whether merman's Python binding exposes
`resvg_safe` (unknown, per `mermaid.md`). It also covers any Mermaid package
we switch to later.

The alternative is to keep a **text-only `<foreignObject>` subset** (`div`,
`span`, `p`, `br`, text, no attributes) for novasvg, which draws it natively
with word wrap. It wraps better and costs us novasvg as the backbone. It also
still needs the same XHTML filtering, so it saves no sanitiser work.
Recommendation: rewrite to `<text>` and stay on resvg. Revisit if label
wrapping proves bad on real diagrams. The shape the rewrite produces (centred
`<text>` inside a translated `<g>`) drew in both renderers [ran]. The rewrite
itself has not been written.

What resvg already ignores, so the sanitiser is defence in depth there:
`<script>` [ran], `http://` and `file://` image hrefs [ran], `foreignObject`
[ran], and events [read]. What neither renderer ignores: absolute file
hrefs, plus relative ones in resvg [ran]. Neither binding can turn that off,
so step 3 is required, not optional.

## Recommendation, and its real cost

**Use resvg (`resvg_py==0.5.0`, exact pin) behind our own sanitiser, behind
a one-function renderer seam that novasvg also fits.**
- Rasterise on a worker, to RGBA at the pane's pixel width times the DPI
  scale.
- Pass imscribe's font files and map every generic family to them.
- Upload on the GUI thread through `hello_imgui.TextureGpu` (add `numpy`).
- Cache by `(sha of sanitised svg, pixel width, dpi)`.
- Re-rasterise when the pane width or DPI changes. Debounce that during a
  resize, and show the old texture scaled in the meantime.

The cost:
- **Two new runtime dependencies.** `resvg_py` has one maintainer; pin it
  exactly and treat it as replaceable, since its whole surface is one
  function. numpy is about 15–20 MB per platform wheel [unverified size].
  Without numpy, the encoded-data route works today with one global cache;
  that is acceptable for a first cut, and the cache coupling should be written
  down where it is used.
- **A sanitiser we own**, with tests: each hostile case in the appendix probe
  becomes a test.
- **Zoom is not free, and resvg's text is slow.** Every width change
  re-renders: 446 ms at 1× and 680 ms at 2× for a 1,200-label diagram, against
  52 ms for novasvg [ran]. Typical Mermaid output is far smaller. Time real
  diagrams before building the worker and debounce machinery, because if they
  come in under a frame budget much of that machinery is unnecessary
  [inferred].
- **`<foreignObject>` labels become approximate.** The sanitiser rewrites
  them to centred `<text>`, so HTML wrapping and inline markup are lost. The
  other ways to get exact labels are a Mermaid route that emits `<text>`
  (mmdr), or switching to novasvg and accepting a very young library.
- Not a cost: no change to the three-pin unit. resvg is independent of
  imgui-bundle's ABI [inferred: it returns bytes and links nothing of ours].

## Measured: the probe below, run by the operator

The operator ran the probe twice, from a scratch venv (`resvg_py 0.5.0`
bundling resvg **0.48.1**, and `novasvg 0.11.3`) on their Linux machine. Both
runs exited 0.

- **Run 1** (`cmd-20261019T024102Z-10c3434e7ded`) ran an earlier version of the
  probe. It had no generic-family mapping and no `style`, `marker` or
  rewritten-`foreignObject` cases.
- **Run 2** (`cmd-20261019T024223Z-405ad323ce85`) is the script below as it
  now stands. The table is run 2. Run 1 agreed on every row both runs share,
  except the timings, explained below.

"DREW" means the output differed from the same SVG without the construct.

| Construct | resvg | novasvg |
|---|---|---|
| `<image href="/abs/path.png">` | **DREW: reads local files** | **DREW: reads local files** |
| `<image xlink:href="file:///abs">` | ignored | ignored |
| `<image href="secret.png">` relative, `resources_dir` set | **DREW** | ignored |
| `<image href="http://…">` | ignored | ignored |
| `<script>` | ignored | ignored |
| `<foreignObject>` with an XHTML div | ignored | **DREW** |
| `<text>`, `font_files` only, generics **unmapped** | **ignored: no text drawn, no error** | n/a |
| `<text>`, `font_files` + generics mapped to "Droid Sans" | DREW | DREW (with novasvg's own font lookup) |
| `<text>`, system fonts | DREW | n/a |
| `foreignObject` rewritten to centred `<text>` in a translated `<g>` (D) | DREW | DREW |
| `<style>` class rule overriding a presentation attribute | DREW | DREW |
| `<marker>` with `marker-end` | DREW | DREW |
| Billion laughs (10^7 expansion) | **rejected at once**: `ValueError a possible entity reference loop is detected` | not run (the probe has no novasvg case) |
| PNG output | 8-bit RGBA (colour type 6), not interlaced, so stdlib `zlib` can decode it | n/a |
| 1,200 rects + 1,200 `<text>`, 800×600 | **446 ms** with one font, generics mapped; 441 ms with system fonts; **680 ms at `zoom=2`** | **52 ms** (47 ms in run 1) |

What the runs change:
- **The local-file read is confirmed for both renderers.** Sanitiser step 3
  (fragment-only hrefs, no `<image>`) is required, not defence in depth.
  novasvg's docs mention only data URIs, but it opens absolute paths.
- **Generic families must be mapped.** With only `font_files` and
  `skip_system_fonts`, resvg drew no `<text>` for `font-family="sans-serif"`
  [ran]. It very likely drew none for the unspecified default either: that
  explains run 1's 82 ms [inferred]. Set `font_family`,
  `sans_serif_family`, `serif_family` and `monospace_family` to the family
  name of the loaded face. Otherwise every label is silently blank. A test
  should hold this.
- **Correction to run 1.** Run 1 timed "one font" at 82 ms and system fonts at
  586 ms. That 82 ms was fast *because no text was drawn*. With text drawn,
  one font and system fonts cost the same (446 against 441 ms). Text
  rendering, not font discovery, is what costs time in resvg here. The
  difference is about 360 ms for 1,200 labels, which is about 0.3 ms a label
  [inferred from the two runs].
- **novasvg is about 8.5× faster** on this text-heavy case and draws
  `<foreignObject>`. That is a real argument for it. What still decides for
  resvg is maturity: one long-lived project against a package days old on
  PyPI. A typical Mermaid diagram has tens of labels, not 1,200, so it should
  take tens of ms in resvg [inferred, assuming the cost is linear]. Keep the
  renderer behind a one-function seam so that swapping it is a one-line
  change once real diagrams have been timed.
- **Render off the GUI thread.** Hundreds of ms cannot run inside a frame.
  Rasterise on a worker, keep showing the previous texture scaled until the
  new one lands, and debounce resizes [inferred]. Whether `resvg_py` releases
  the GIL while it renders is still [unverified]. If it does not, a worker
  thread will stall frames, and a process pool is the fallback.

```python
import os, struct, sys, tempfile, time, zlib

D = tempfile.mkdtemp()
FONT = sys.argv[1]


def png(w, h):
    raw = b"".join(b"\0" + b"\xff\0\0\xff" * w for _ in range(h))
    c = lambda k, b: struct.pack(">I", len(b)) + k + b + struct.pack(">I", zlib.crc32(k + b))
    return (b"\x89PNG\r\n\x1a\n" + c(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + c(b"IDAT", zlib.compress(raw)) + c(b"IEND", b""))


SEC = os.path.join(D, "secret.png")
open(SEC, "wb").write(png(20, 20))


def doc(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
            f' width="200" height="100"><rect width="200" height="100" fill="#fff"/>{body}</svg>')


CASES = {
    "abs-path image": f'<image width="20" height="20" href="{SEC}"/>',
    "file:// image": f'<image width="20" height="20" xlink:href="file://{SEC}"/>',
    "relative image (resources_dir=D)": '<image width="20" height="20" href="secret.png"/>',
    "http image": '<image width="20" height="20" href="http://127.0.0.1:9/x.png"/>',
    "script": "<script>document.write(1)</script>",
    "foreignObject": '<foreignObject width="200" height="30"><div xmlns="http://www.w3.org/1999/xhtml">HTML LABEL</div></foreignObject>',
    "text": '<text x="0" y="50" font-family="sans-serif" font-size="20">Text ok</text>',
    "fO rewritten to text": '<g transform="translate(10,10)"><text x="90" y="15" text-anchor="middle" dominant-baseline="central">HTML LABEL</text></g>',
    "style class": '<style>.n rect{fill:#f00}</style><g class="n"><rect width="20" height="20" fill="#fff"/></g>',
    "marker": '<defs><marker id="m" markerWidth="10" markerHeight="10" refX="5" refY="5"><circle cx="5" cy="5" r="5"/></marker></defs><path d="M20 80 L180 80" stroke="#fff" marker-end="url(#m)"/>',
}
LAUGHS = '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a0 "aaaaaaaaaa">' + "".join(
    f'<!ENTITY a{i} "{("&a%d;" % (i - 1)) * 10}">' for i in range(1, 8)
) + ']><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text>&a7;</text></svg>'
BIG = "<svg xmlns='http://www.w3.org/2000/svg' width='800' height='600'>" + "".join(
    f"<rect x='{i % 40 * 20}' y='{i // 40 * 20}' width='18' height='18' rx='4' fill='#4a7'/>"
    f"<text x='{i % 40 * 20}' y='{i // 40 * 20 + 14}' font-size='10'>n{i}</text>" for i in range(1200)
) + "</svg>"

import resvg_py

print("resvg_py", resvg_py.__version__, "resvg", resvg_py.__resvg_version__)
FAM = sys.argv[2] if len(sys.argv) > 2 else "Droid Sans"
BARE = dict(font_files=[FONT], skip_system_fonts=True, resources_dir=D)
KW = dict(BARE, font_family=FAM, sans_serif_family=FAM, serif_family=FAM, monospace_family=FAM)
base = resvg_py.svg_to_bytes(svg_string=doc(""), **KW)
w, h, depth, ctype, _, _, il = struct.unpack(">IIBBBBB", base[16:29])
print(f"  png IHDR {w}x{h} depth={depth} colortype={ctype} interlace={il}")
for label, kw in (("one font, generics unmapped", BARE), ("one font, generics mapped", KW), ("system fonts", dict(resources_dir=D))):
    b = resvg_py.svg_to_bytes(svg_string=doc(""), **kw)
    out = resvg_py.svg_to_bytes(svg_string=doc(CASES["text"]), **kw)
    print(f"  text [{label}]: {'DREW' if out != b else 'ignored'}")
for name, body in CASES.items():
    out = resvg_py.svg_to_bytes(svg_string=doc(body), **KW)
    print(f"  {name}: {'DREW' if out != base else 'ignored'}")
t = time.perf_counter()
try:
    resvg_py.svg_to_bytes(svg_string=LAUGHS, **KW)
    print(f"  billion laughs (10^7): rendered in {time.perf_counter() - t:.3f}s")
except Exception as e:
    print(f"  billion laughs: rejected in {time.perf_counter() - t:.3f}s: {type(e).__name__} {str(e)[:100]}")
for label, kw in (("system fonts", {}), ("one font", KW)):
    t = time.perf_counter()
    resvg_py.svg_to_bytes(svg_string=BIG, **kw)
    print(f"  1200 rect+text 800x600 [{label}]: {1000 * (time.perf_counter() - t):.1f} ms")
t = time.perf_counter()
resvg_py.svg_to_bytes(svg_string=BIG, zoom=2.0, **KW)
print(f"  same, zoom=2, one font: {1000 * (time.perf_counter() - t):.1f} ms")

import novasvg

print("novasvg", getattr(novasvg, "__version__", "?"))
print("  module:", [a for a in dir(novasvg) if not a.startswith("_")])
print("  Document:", [a for a in dir(novasvg.Document) if not a.startswith("_")])
load = getattr(novasvg.Document, "load_from_data", None)
if load:
    def nova(svg):
        p = os.path.join(D, "n.png")
        load(svg).render_to_bitmap().write_to_png(p)
        return open(p, "rb").read()
    nbase = nova(doc(""))
    for name, body in CASES.items():
        try:
            print(f"  {name}: {'DREW' if nova(doc(body)) != nbase else 'ignored'}")
        except Exception as e:
            print(f"  {name}: error {type(e).__name__} {e}")
    t = time.perf_counter()
    nova(BIG)
    print(f"  1200 rect+text 800x600: {1000 * (time.perf_counter() - t):.1f} ms")
```

## Sources

- Project venv, [ran]: `nm -D`, `strings` and `ctypes` against
  `imgui_bundle/_imgui_bundle.cpython-311-x86_64-linux-gnu.so`;
  `ImTextureData.get_pixels_array()`; `import numpy`.
- Stubs, [read]: `imgui_bundle/nanovg.pyi`, `hello_imgui.pyi`,
  `imgui/__init__.pyi`, `imgui/internal.pyi`, `imgui_md.pyi`,
  `_imgui_md_image_loader.py`. Also `imscribe/backends/svg.py`,
  `imscribe/fontfiles.py`, and this repo's `pyproject.toml`.
- https://pypi.org/project/resvg-py/ ·
  https://resvg-py.readthedocs.io/en/latest/api.html ·
  https://github.com/baseplate-admin/resvg-py
- https://github.com/linebender/resvg ·
  https://github.com/linebender/resvg/blob/main/docs/unsupported.md ·
  https://docs.rs/usvg/latest/usvg/struct.ImageHrefResolver.html ·
  https://docs.rs/usvg/latest/usvg/struct.Options.html ·
  https://raw.githubusercontent.com/linebender/resvg/main/crates/usvg/src/parser/svgtree/names.rs
- https://pypi.org/simple/novasvg/ · https://pypi.org/pypi/novasvg/json
- https://cairosvg.org/documentation/ · https://osv.dev/vulnerability/CVE-2023-27586
- https://pypi.org/project/skia-python/ ·
  https://lists.aau.at/pipermail/mpeg-otspec/2023-July/002980.html
- https://github.com/libvips/pyvips
- https://doc.qt.io/qt-6/qtsvg-index.html · https://doc.qt.io/qt-6/qsvgrenderer.html
- https://github.com/sammycage/plutosvg
- https://pypi.org/project/svgelements/
