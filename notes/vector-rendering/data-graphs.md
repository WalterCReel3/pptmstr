# General graphs and data plots from Claude output

Research note for board task `datagraphs`, 2026-10-18. Scope: what draws
"general graphs or data representations" that Claude emits, inline with
imscribe-typeset markdown in pptmstr (imgui-bundle==1.92.900, CPython >=3.11,
Linux x86-64 glibc>=2.28 and macOS arm64). SVG rendering itself is
`svg-renderers.md`; Mermaid is `mermaid.md`; where a figure sits in imscribe's
layout is `imscribe-seams.md`. This note hands those tasks either draw calls
(family A) or an SVG string (family B).

Every claim carries its source: **[run]** a command run in this session (the
script is described alongside), **[read]** a file or URL read in this session,
**[unverified]** neither, **[judgement]** an opinion, labelled as one.

## Summary

- **ImPlot, ImPlot3D and imgui-node-editor are already in the installed wheel
  and drive from the standard library alone.** ImPlot's Python bindings type
  their data as nanobind `ndarray`, but numpy is *not* installed in the
  project venv and is not a required dependency of imgui-bundle; a stdlib
  `array.array` satisfies the binding and a headless frame produced geometry.
  So a native plot costs zero new dependencies. [run]
- **What drives a native plot is the open question, not the drawing.** No
  format Claude emits unprompted maps onto ImPlot. The least-invention input is
  a *Vega-Lite subset* interpreted natively (a format Claude has seen widely),
  not a pptmstr-invented JSON. [judgement, reasoning below]
- **The declarative-to-SVG routes all cost a large or system dependency.**
  vl-convert-python ~30 MB wheel, 84 MB installed, fetches any `data.url` by
  default and refuses it with `allowed_base_urls=[]` (measured); pygraphviz now
  ships wheels bundling Graphviz (EPL-2.0 libraries); D2 is a Go binary
  (MPL-2.0) reachable only through a third-party wrapper; Plotly static export
  needs a separately installed Chrome. [read]
- **Recommendation:** stage it. First slice: no data-plot renderer at all —
  Mermaid (which has pie and xychart diagram types as well as flowcharts —
  [unverified] here; `mermaid.md` covers which of them a chosen renderer
  supports) plus script-free SVG. Second slice, if wanted: native
  ImPlot driven by a declared Vega-Lite subset (inline `values` only, a handful
  of marks), zero dependencies. Graph layout (DOT) is the one thing no shipped
  component can do; defer it until a measurement shows demand. vl-convert is
  the fallback only if full Vega-Lite fidelity is demanded, and then only behind
  the URL policy below.

## A. Native immediate-mode, as shipped in imgui-bundle 1.92.900

Installed package: `/home/wreel/Source/pptmstr/.venv/lib/python3.11/site-packages/imgui_bundle`,
`pip show imgui-bundle` → 1.92.900; package directory 44 MB. [run]

`imgui_bundle.has_submodule(name)` is `True` for `implot`, `implot3d`,
`imgui_node_editor`, `nanovg`, `immvision`, `imgui_md`, `imgui_microtex`,
`imgui_tex_inspect`, `imguizmo`. [run]

### ImPlot (2-D)

- 20 `plot_*` functions: `plot_line`, `plot_scatter`, `plot_bars`,
  `plot_bar_groups`, `plot_stairs`, `plot_shaded`, `plot_stems`,
  `plot_error_bars`, `plot_histogram`, `plot_histogram_2d`, `plot_heatmap`,
  `plot_pie_chart`, `plot_digital`, `plot_inf_lines`, `plot_text`,
  `plot_image`, `plot_polygon`, `plot_bubbles`, `plot_dummy`,
  `plot_to_pixels`. [run: `dir(implot)`]
- Signature shape: `plot_line(label_id: str, xs: ndarray[writable=False],
  ys: ndarray[writable=False], spec: implot.Spec | None = None)`. [run: `__doc__`]
- **numpy is not needed.** `import numpy` fails in the project venv [run];
  imgui-bundle's METADATA lists numpy only under extras `matplotlib` and
  `full` [read: `imgui_bundle-1.92.900.dist-info/METADATA`]. A Python `list`
  is refused with `TypeError: incompatible function arguments` [run], but a
  stdlib `array.array` (typecodes `d`, `f`, `i` tried) is accepted through the
  buffer protocol. Probe: `imgui.create_context()`, `implot.create_context()`,
  `io.display_size=(800,600)`, `io.backend_flags |= renderer_has_textures`, two
  frames of `begin_plot` / `plot_line` / `plot_bars` / `plot_scatter` /
  `end_plot`, then `imgui.render()`: frame 1 reported `total_vtx_count 564`,
  exit 0. [run: `$TMPDIR/implot_probe.py`, not committed]
- Calling `plot_line` with no ImPlot context segfaults rather than raising
  (exit 139). Any plotting path must guarantee the context exists; a bad call
  is a process crash, not an exception. [run]
- Interactivity is on by default (pan, zoom, context menus). In a scrolling
  transcript the mouse wheel is contested between the pane's scroll and the
  plot's zoom. `Flags_.no_inputs`, `Flags_.no_menus`, `Flags_.canvas_only` and
  `AxisFlags_.lock` exist to switch that off [read: `implot/__init__.pyi`
  `class Flags_`, `class AxisFlags_`]. [judgement] Default to `no_inputs`
  inline; offer interaction in a detached/expanded view.
- Cost per frame: it is retessellated every frame it is visible (564 vertices
  for three trivial series above). Fine for tens of plots of thousands of
  points; a transcript must not draw off-screen plots — that is the seams
  task's culling question. [judgement on scale; vertex count run]
- Text in plots is ImGui's font, so labels match the pane's font family and
  DPI without the font-matching problem SVG has (`svg-renderers.md` §C).
  [judgement; follows from ImPlot drawing through ImDrawList]
- Whether `begin_plot` (which opens an ImGui child item) can be issued at a
  position inside an imscribe-drawn page is a question for `imscribe-seams.md`
  §3. Not checked here.

### ImPlot3D

`plot_line`, `plot_scatter`, `plot_surface`, `plot_mesh`, `plot_triangle`,
`plot_quad`, `plot_text`, `plot_image`. [run: `dir(implot3d)`] Same array
convention assumed, not probed. [unverified] [judgement] Not a first-slice
need.

### imgui-node-editor

Present [run], but it is an *editor*: nodes are placed with
`set_node_position(node_id, editor_position)` [read: `imgui_node_editor.pyi`]
and grep finds no layout function [run: `grep layout`]. It cannot lay out a
graph Claude describes; it could only *display* a graph whose coordinates came
from a layout engine (Graphviz). [judgement] Heavyweight for read-only display;
plain ImDrawList boxes and splines from a layout would be simpler.

### What input drives it

Nothing Claude emits unprompted names ImPlot calls. Options:

1. **pptmstr-invented JSON** (`{"kind":"line","x":[...],"y":[...]}`).
   Smallest interpreter, but Claude has never seen it: it must be taught in
   every system prompt and will drift. [judgement]
2. **CSV in a fenced block plus a chart hint in the info string**
   (```` ```csv chart=line ````). Claude writes CSV well; the info-string
   convention is invented. imscribe already parses the info string into
   `MarkdownCode.language` and can show it (`show_fence_info`) [read:
   `~/Source/imscribe/imscribe/typeset_markdown.py` `_code`,
   `ReaderOptions.show_fence_info`]. [judgement on Claude's fluency]
3. **A Vega-Lite subset interpreted natively**: accept ```` ```vega-lite ````
   whose `data` is inline `values`, `mark` in {line, point, bar, area, rule,
   arc}, `encoding.x/y/color` with `quantitative`/`ordinal`/`nominal`/
   `temporal`. Map to `plot_line`/`plot_scatter`/`plot_bars`/`plot_shaded`/
   `plot_inf_lines`/`plot_pie_chart`. Out-of-subset specs fall back to showing
   the source (or, later, to vl-convert). Claude has seen Vega-Lite (it is
   Altair's output format and documented at length publicly) [judgement — no
   source measured Claude's fluency]. **Recommended**: no invented format, no
   dependency, and the same fence would render in full elsewhere.

Cost of 3: an interpreter of a few hundred lines with tests; the subset
boundary must be explicit and documented so "renders here" is predictable.
[judgement]

## B. Declarative spec → SVG

These produce SVG for whatever `svg-renderers.md` picks, so they inherit its
text/font limits: Vega and Graphviz SVG carry labels as `<text>`.
[unverified for this exact claim per tool; a probe below checks vl-convert]

| Route | Dependency | Wheels for the targets | License | Network / safety |
|---|---|---|---|---|
| Vega-Lite via `vl-convert-python` 1.9.0.post1 (2026-01-21; 2.0.0rc7 pre-release) | V8 via `deno_runtime` crate, bundled; no Node, no browser | abi3 cp37+: manylinux glibc 2.17 x86-64 33.5 MB, macOS 11 arm64 29.7 MB | BSD-3-Clause | `allowed_base_urls` "Default allows any base URL"; local files blocked by default (main-branch README) |
| Graphviz DOT via `graphviz` 0.21 | System `dot` on PATH | pure Python, but useless without the system binary | MIT | Runs a subprocess |
| Graphviz DOT via `pygraphviz` 2.0.4 (2026-10-15) | Bundles Graphviz libraries and CLI tools in the wheel | cp310-abi3: manylinux_2_28 x86-64 5.5 MB, macOS 11 arm64 1.4 MB | BSD-3-Clause; bundled Graphviz EPL-2.0 | Local; no network known |
| D2 | Go binary; official route is `go install` or release binaries; `d2-python-wrapper` (third party) bundles them | wrapper's wheels **unverified** (PyPI page failed to load) | MPL-2.0 | "does not use an internet connection after installation, except to check for version updates" |
| Plotly static export via `kaleido` ≥1.0 | "As of version 1.0.0, Kaleido requires Chrome to be installed"; `kaleido_get_chrome` downloads one | n/a | MIT | A browser is the dependency; rejected |

Sources [read]: <https://pypi.org/project/vl-convert-python/>,
<https://github.com/vega/vl-convert> (README on main; note main is the 2.0
line), <https://raw.githubusercontent.com/vega/vl-convert/v1.9.0/vl-convert-python/vl_convert.pyi>,
<https://raw.githubusercontent.com/vega/vl-convert/v1.9.0/vl-convert-python/src/lib.rs>,
<https://pypi.org/project/graphviz/>, <https://pypi.org/project/pygraphviz/>,
<https://github.com/terrastruct/d2>, <https://github.com/plotly/Kaleido>;
`d2-python-wrapper` only via a search snippet of
<https://diegocarrasco.com/d2-python-wrapper-introduction/>.

### vl-convert and URLs in model output

- `vegalite_to_svg(vl_spec, vl_version=None, config=None, theme=None,
  show_warnings=None, allowed_base_urls: list[str] | None = None, ...)`;
  docstring: "List of allowed base URLs for external data requests. Default
  allows any base URL". [read: v1.9.0 `vl_convert.pyi`]
- The PyO3 layer passes `Option<Vec<String>>` through unchanged [read: v1.9.0
  `src/lib.rs`]. Measured (P1, operator-run): with `allowed_base_urls=[]`, and
  with a list naming another host, a spec whose `data.url` is
  `https://vega.github.io/vega-datasets/data/cars.json` raises `ValueError …
  External data url not allowed`; with `None` it fetches over the network and
  renders 402 marks. So `[]` is deny-all **for `data.url`**. Image-mark URLs,
  Google Fonts and plugins (which the README says have separate controls) were
  not tested. [run by operator]
- The README (main branch) says remote data, images, Google Fonts and plugins
  can be fetched "when the input or configuration requests them", and relative
  data paths resolve to the vega-datasets CDN by default. [read] So a
  model-written spec can make the app issue HTTP requests unless policy stops
  it. Model output is untrusted input; that is an exfiltration channel
  (a spec whose `data.url` carries conversation text in the query string).
  [judgement]
- Policy, independent of how `[]` behaves: reject before conversion any spec
  that contains a `url` key anywhere (data, image marks, `lookup` from data) or
  a `data.name` the app does not supply, *and* pass `allowed_base_urls=[]`.
  Two layers, because the second is measured only for `data.url`.
  [judgement]
- Wheel ~30 MB per platform; installed, `vl_convert` is **84 MB** on Linux
  x86-64 (P1, `du -sh`) against the 44 MB imgui-bundle directory. [run by
  operator; macOS size not measured]
- Its SVG carries labels as `<text>` and no `<foreignObject>` (P1, inline spec
  and URL spec both). [run by operator]

### Graphviz

The one capability nothing shipped provides is *graph layout* (nodes and
edges placed automatically). Mermaid's flowchart covers most of what Claude
draws as a graph (`mermaid.md`). If DOT is wanted, `pygraphviz` wheels now
make it installable without a system package [read], and its layout could
feed either SVG (`-Tsvg`) or native drawing (`-Tjson`/`plain` coordinates →
ImDrawList), the latter avoiding SVG text-font mismatch. [judgement] The
macOS arm64 wheel is 1.4 MB against 5.5 MB on Linux; whether it truly bundles
the Graphviz libraries rather than linking a Homebrew install is
**[unverified]** — the page's general statement is the only source.
EPL-2.0 is a weak copyleft on the bundled Graphviz; pptmstr (MIT) depending on
it as an unmodified wheel is the ordinary case, not a relicensing. [judgement,
not legal advice]

## C. What Claude emits unprompted

Sourced:

- GitHub renders exactly four diagram fence languages: `mermaid`, `geojson`,
  `topojson`, `stl`. [read: <https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams>]
- VS Code's built-in markdown preview renders `mermaid` fences and names no
  other diagram format. [read: <https://code.visualstudio.com/docs/languages/markdown>]
- Anthropic's help article on artifacts lists "diagrams and flowcharts,
  dashboards" without naming formats [read:
  <https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them>];
  third-party guides say artifacts render Mermaid, SVG and React [read:
  search results, e.g. <https://www.zapier.com/blog/claude-artifacts>] —
  secondary sources, treat as **[unverified]**.

Judgement (no measurement of Claude's output was made):

- In plain markdown with no rendering promised, Claude's habitual figure
  formats are, roughly in order: ASCII/box-drawing diagrams and markdown
  tables (the safe default when it does not know what renders), `mermaid`
  fences (because GitHub and VS Code render them), and code that *would*
  produce a chart (matplotlib, Plotly) when asked for a plot. Vega-Lite JSON
  and DOT appear when asked for by name, rarely unprompted.
- Matplotlib code is not a display format: rendering it means executing model
  code. Out of scope; show it as code. [judgement, and a hard line]
- Markdown tables already render through imscribe; a table is the zero-cost
  "data representation" and stays the fallback.

Probe P2 below turns this judgement into a measurement.

## D. Steering the format (interface only)

- pptmstr declares, per session, the fence languages that render: e.g.
  `mermaid`, `svg`, and (if built) `vega-lite` with its subset. That
  declaration goes in the system prompt appended for the session, or a skill
  the session loads; one source of truth in pptmstr that both the prompt text
  and the renderer's dispatch table read, so they cannot disagree. [judgement]
- The text tells Claude: which fence names render, the limits (inline data
  only, no URLs, which marks), and that anything else is shown as source. It
  does not tell Claude to prefer figures; the operator's CLAUDE.md favours
  terse text.
- A fence that fails to render shows its source plus a one-line reason, never
  a blank box, so a rejected spec (e.g. one with a URL) is visible to the
  reader. [judgement]

## Probes (measurements that would change this note)

- **P1 — vl-convert URL policy and size.** Requested of the operator as
  `cmd-20261019T023541Z-d3030d576b1f`: throwaway venv in `/tmp/claude-1000/vlc`,
  install vl-convert-python 1.9.0.post1, print installed size, run
  `/tmp/claude-1000/vlc_probe.py` (inline spec; `data.url` with
  `allowed_base_urls=[]`, with another host, with `None`). Settles whether `[]`
  is deny-all and whether output uses `<text>`/`<foreignObject>`. **Ran**,
  exit 0, 10.7 s: results folded into §B above. Still open: image-mark URLs,
  fonts, plugins; macOS arm64.
- **P2 — what Claude emits.** Run ~20 prompts asking for "a chart of …" /
  "diagram of …" through pptmstr with and without the fence declaration of §D;
  count fence languages. Not run.
- **P3 — ImPlot inside an imscribe page.** Whether `begin_plot` can be issued
  at a figure's position mid-page with correct clipping and scroll; belongs to
  the seams task's draw-path answer. Not run.

## Cost of the recommendation

| Slice | New dependency | Work |
|---|---|---|
| 0: tables + Mermaid + SVG (other notes) | per `svg-renderers.md`/`mermaid.md` | — |
| 1: native ImPlot from a Vega-Lite subset | none | interpreter + tests; `no_inputs` inline; source fallback |
| 2 (on demand): DOT via pygraphviz | pygraphviz wheel, bundled Graphviz (EPL-2.0) | layout → SVG or native |
| 3 (on demand): full Vega-Lite via vl-convert | ~30 MB wheel, embedded V8 | URL pre-filter + `allowed_base_urls`; P1 first |
