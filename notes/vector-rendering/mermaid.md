# Rendering Mermaid without a browser or network

Research note for the `mermaid` board task, written 2026-10-18. Every claim
below cites a page read in this session (2026-10-18). Anything not read is
marked **unverified**. One measurement was run by the operator (see
"Measurement"). Apart from that, nothing was installed or executed, so the
statements about SVG features in the options table rest on documentation.
The measurement corrects them for merman and mmdr.

## The constraint

Mermaid's reference implementation lays diagrams out from text sizes it gets
from the browser (`getBBox`). jsdom and svgdom do no layout, so Mermaid cannot
run on them unaided. Projects that run upstream Mermaid without a browser add
text measurement themselves: SebastianJS through node-canvas, mermaidx
through a bridge to a native SVG engine. pptmstr has no Node dependency
(`pyproject.toml`, at the `claude-agent-sdk` pin), and the goal is to keep it
that way.

Sources: <https://lightrun.com/answers/mermaid-js-mermaid-mermaid-without-browser>,
<https://github.com/creadri/sebastianjs> (search-result summaries only, not
read in full).

## Options

| Route | How it renders | Coverage / fidelity | SVG it emits | Packaging for pptmstr | License | Maturity (read 2026-10-18) |
|---|---|---|---|---|---|---|
| **mermaid-cli (`mmdc`)** | Upstream Mermaid in headless Chromium via Puppeteer | Reference implementation | `<foreignObject>` HTML labels by default, `<style>` CSS, markers | `@mermaid-js/mermaid-cli` 12.0.0 needs Node >= 22.13.0 and has Puppeteer ^25 as a peer dependency. Puppeteer downloads a pinned Chromium. **Size and cold-start latency for current versions are unverified.** The only figures found are old Docker images of about 310–340 MB compressed. | MIT | Official |
| **mermaid.ink** (hosted) | Puppeteer and headless Chrome (repo topics and env vars) | Upstream | Same as mmdc | HTTP call. **Sends diagram source off-machine in the URL.** | MIT | Self-hostable (Docker), but that brings back the Chromium cost |
| **Kroki** (hosted) | Gateway plus the `yuzutech/kroki-mermaid` companion container. The rendering engine is **unverified** (probably Chromium). | Upstream | Same as mmdc (**unverified**) | HTTP GET (source deflate+base64 in the URL) or POST. **Sends source off-machine.** The public site has no privacy statement. | MIT | Self-hostable, but needs a container stack |
| **merman** (Rust, Python bindings) | Headless Rust reimplementation that tracks upstream (dev target Mermaid 12.2.0; published 0.9.x targets 12.1.0) | All 37 families admitted. Parity is checked **structurally against the DOM, not pixel by pixel**. 8 families (state, sequence, pie, packet, timeline, kanban, gantt, block) only reach the "structure" level. Architecture layout is approximate. Text measurement is deterministic and font-agnostic by default, and a host can plug in a measurer. | The default parity path emits `<style>`, markers and **`<foreignObject>` HTML labels**. An explicit `resvg_safe()` pipeline turns common foreignObject labels into approximate `<text>` and strips active content and unsupported CSS. The raster outputs all go through it. The Python option name for `resvg_safe` is **unverified**; the PyPI page names only a `"pipeline":"readable"` example. | PyPI `merman` 0.9.1 (2026-10-18): wheels for manylinux_2_35 x86_64 (8.3 MB), macOS 11 arm64, and win_amd64. No runtime dependencies. **No Linux aarch64 wheel and no wheel for glibc < 2.35.** Synchronous API. | MIT OR Apache-2.0. Default Rust features pull in EPL-2.0 ELK, and math pulls in OFL fonts (THIRD_PARTY_NOTICES). Which of these the wheel ships is **unverified**. | 611 stars, 7.7k commits. Zed uses it as its Mermaid backend. Python bindings are self-described "experimental". |
| **mmdr** (PyPI, MohammadRaziei) | Python extension with two Rust backends: merman (default) and mermaid-rs-renderer | Merman backend: 17 named types. mermaid-rs-renderer backend: 11 (no block, architecture, kanban, c4, sankey or packet). | Depends on backend. Its own SVG features were **unverified**. | PyPI 0.4.0 (2026-10-17): abi3 wheels for manylinux_2_17 x86_64 and aarch64, macOS x86_64 and arm64, and win_amd64. Bundles novasvg for rasterizing. | MIT | Third-party wrapper, a day old at its latest release |
| **mermaid-rs-renderer (`mmdr` crate/CLI)** | Native Rust parser, layout and SVG renderer | 23 types listed. Self-described as "active early development; may not yet match mermaid-cli in all cases". | Not documented. Uses system fonts through a font cache. | crates.io, brew, scoop, AUR and nix. No first-party Python wheels (mmdr above wraps it). | MIT | 1.8k stars; benchmarks dated Feb 2026 |
| **sebastian (`seb`)** | Rust port of Mermaid 11.15.0 renderers | Claims **byte-identical** SVG to mmdc for most types (flowchart 544/553 fixtures, sequence 37/37, and 11 others). Mindmap, architecture, requirement and C4 are approximate. Byte-exactness needs the system Trebuchet MS font; without it, embedded OFL fonts are used. | `htmlLabels:true` (default) gives `<foreignObject>`. `htmlLabels:false` gives native `<text>`/`<tspan>` that resvg can draw. | Rust crate, CLI and wasm. **No Python bindings.** | MIT | 17 stars, 129 commits. Young. |
| **mmd** (grahambrooks) | Pure Rust | Only flowchart, gitGraph and C4Context. Unsupported syntax is a parse error that names the line. | Native `<text>`, presentation attributes, **no CSS cascade** | Rust crate and CLI; no Python | MIT | 0 stars, 18 commits |
| **mermaidx** (embedded JS) | Upstream Mermaid **v11** (minor version not stated) runs inside QuickJS-ng in-process. `getBBox` and `getComputedTextLength` are bridged to novasvg's font stack, with bundled DejaVu Sans. An optional V8 backend (mini-racer) uses a precomputed glyph-advance table. | Claims everything v11 supports. Known issues are **unverified**. Bundles v11 while upstream mmdc is on 12, so it lags upstream. | **`<foreignObject>` HTML labels** (`htmlLabels:true`). novasvg paints them and does word wrapping only; it has no full HTML/CSS layout. Whether `htmlLabels:false` works is **unverified**. | `mermaidx` 0.10.0 pure wheel (1.8 MB). Depends on `quickjs-ng`, `novasvg` (MIT, header-only C++ SVG engine derived from lunasvg, with wheels) and `termaid`. Latency is **unverified**. Concurrency goes through processes. | MIT (font license **unverified**) | 81 stars. Rewritten in 0.6 and renamed in 0.8. |

This is the answer to spec item 4 (embedded JS engine). mermaidx is the
Mermaid counterpart of vl-convert's design. vl-convert runs Vega in V8 through
`deno_runtime` and takes font metrics from resvg
(<https://pypi.org/project/vl-convert-python/>, search summary). mermaidx does
the same with QuickJS and novasvg. Writing our own is not worth it: one now
exists, and the hard part is the text-measurement bridge, which it has
already built.

## What the SVG renderer has to handle

This constrains `notes/vector-rendering/svg-renderers.md`:

- **Every route that is faithful to upstream emits `<foreignObject>` HTML
  labels by default.** That covers mmdc, mermaid.ink, Kroki, merman's parity
  path, mermaidx, and sebastian with its defaults. An SVG renderer without
  foreignObject support (resvg, lunasvg, and most native ones; for resvg this
  follows merman's and sebastian's documentation) draws these diagrams with
  **no labels**.
- Three routes remove the problem at the source: merman `resvg_safe()`
  (approximate `<text>`), sebastian `htmlLabels:false`, and mmd (always
  `<text>`). Upstream Mermaid has a root-level `htmlLabels` option, and the
  per-diagram `flowchart.htmlLabels` is deprecated
  (<https://mermaid.js.org/config/schema-docs/config.html>). Its default value
  was not stated on the page read.
- Some routes need **`<style>` with class selectors**: merman parity,
  mermaidx, and upstream theming. novasvg supports this; mmd avoids it.
- Arrowheads need **`<marker>`**: all routes.

## Text measurement and font coupling (spec item 5)

The SVG fixes label boxes at the size the layout engine measured. If the
viewer draws with a different font, labels overflow or float in their boxes.
Upstream's default font is `"trebuchet ms", verdana, arial, sans-serif`
(config schema page). Each route measures text its own way:

- merman: deterministic and font-agnostic by default; a host can supply a
  `MermanTextMeasurer`.
- mermaidx: measures with bundled DejaVu Sans, then paints with the same
  engine.
- sebastian: Trebuchet metrics, falling back to embedded OFL fonts.
- mmd: shapes text with real fonts; a host can replace them through a
  `Measure` trait.

For pptmstr the coupling problem goes away **only if the code that lays out
the diagram and the code that paints it agree on one font**. There are two
ways to get that:

1. Render the diagram to a raster with the same library that laid it out
   (merman PNG, mermaidx PNG, or novasvg), and show it as a texture.
2. Plug pptmstr's own font metrics into merman's measurer (mmd offers the
   same hook), so the vector drawing matches the UI font.

Option 1 is the cheap and correct first step. Option 2 is the upgrade if
crisp vector text at any zoom matters.

**Fallback.** Show the fenced source as an ordinary code block, with a
one-line error above it. Use merman's parse error, or mmd's construct and line
error. Treat an unsupported diagram family as an error in the same way. merman
exposes `diagram_family_capabilities()` to check support at runtime. Never
drop the source, because it is the content.

## Alternatives Claude could be steered toward (spec item 6)

- **D2** (MPL-2.0, Go; bundled Go ports of dagre and ELK; "can run entirely
  server-side") has no official Python binding, so it would mean a sidecar Go
  binary. Whether its SVG uses `<text>` is **unverified**.
- **Graphviz DOT**: its native renderers and `<text>` output were **not
  researched this session**.
- **PlantUML**: **not researched**. It needs a JVM, which rules it out as a
  default.

None of these changes the Mermaid decision. Claude writes Mermaid without
being asked, and the operator named Mermaid. Steering Claude to another
language is a prompt change, not a renderer change.

## Recommendation

**Use merman through its own PyPI wheel as the default route, lazily
imported and an optional extra like imtypeset. Rasterize its `resvg_safe`
output (or its PNG path) for display. The fallback is source plus error.**

Why merman:

- It is the only option that is Python-installable, has no runtime
  dependencies, is browserless and JS-free, covers every family, and actively
  tracks the current upstream (12.x).
- It has a real user (Zed) and a structural-parity test regime.
- It offers both a `<text>`-only pipeline and a pluggable text measurer, so it
  does not force the SVG renderer to support foreignObject.

mermaidx is the **fallback candidate** if merman's fidelity proves poor on
Claude's actual diagrams. It runs real Mermaid code, so its semantics are
exact, but it is pinned to v11, needs a JS engine, and its output needs
foreignObject support. mmdc and the hosted routes are **rejected as
defaults**. mmdc costs Node 22, Chromium and a cold start. The hosted routes
send content off-machine and need a network, which contradicts the offline
desktop app.

### What this costs

- **Platform gap.** merman 0.9.1 has wheels only for manylinux_2_35 x86_64,
  macOS arm64 and Windows. That is narrower than imtypeset's envelope on
  Linux: glibc 2.28 is the floor in `pyproject.toml`, and 2.35 is stricter.
  imtypeset has no Linux aarch64 wheel either, so merman's missing aarch64
  wheel loses nothing new; the glibc 2.28–2.34 band is the real gap. The
  `mmdr` wheel (manylinux_2_17) wraps merman and would close it, but it is a
  third-party wrapper a day old at its latest release. Choosing between them
  is a packaging decision for the synthesis task.
- **Fidelity is approximate.** Parity is structural, not pixel-level. The
  `resvg_safe` text fallbacks are explicitly approximate, and 8 families only
  reach the "structure" level.
- **About 8 MB of wheel size.**
- **Young, fast-moving dependencies.** merman 0.9.1, mmdr 0.4.0 and
  mermaidx 0.10.0 were all released on 2026-10-17 or 2026-10-18. Exact pins
  are needed, held to the version as imtypeset's are.
- **License review is needed.** Read merman's THIRD_PARTY_NOTICES (EPL-2.0
  ELK) before shipping.

## Measurement (operator-run, 2026-10-18)

Command `cmd-20261019T023453Z-cee5264c75b3` built a scratch venv with merman
0.9.1, mmdr 0.4.0 and mermaidx 0.10.0 on the operator's Linux x86-64 machine.
It rendered one 4-node flowchart with each, from the default Python call, and
counted SVG elements. Exit 0, 11.8 s including the pip install. The venv
(all three packages plus pip) was 90 MB.

| Call | ms | SVG bytes | foreignObject | text | style | marker |
|---|---|---|---|---|---|---|
| `merman.Merman().render_svg(S, None)` | 7.6 | 32483 | 6 | 0 | 1 | 12 |
| `mmdr.render(S).svg()` (merman backend) | 10.2 | 33601 | **0** | **6** | 1 | 12 |
| `mmdr.render(S, backend="mermaid-rs-renderer").svg()` | 162.8 | 4947 | 0 | 6 | 0 | 2 |
| `mermaidx.render(S).svg()` | 692.9 | 13850 | 7 | 0 | 1 | 12 |
| same, second call with a different source | 694.3 | 13850 | 7 | 0 | 1 | 12 |

What this settles, for one diagram only:

- merman's default Python SVG emits **foreignObject labels** (confirmed).
- mmdr's merman backend emits **`<text>` labels by default**: same engine,
  and apparently a text-safe pipeline applied. Which pipeline mmdr applies was
  not read from its source. Through mmdr, an SVG renderer needs no
  foreignObject support, only `<style>` and `<marker>`.
- mermaidx costs about **0.7 s per diagram, and a second call is no faster**.
  That is too slow to re-render while a reply streams.
- mermaid-rs-renderer's 163 ms is probably the font-database load its README
  mentions. Not investigated.

Not measured: the other diagram families, how the output looks when drawn,
cold import time, and per-package disk size.

### Effect on the recommendation

The engine choice stays merman. The packaging choice now leans to **`mmdr`
(merman backend)**: it has the wider wheel set (manylinux_2_17), gives
`<text>` output out of the box, and its latency is comparable. The open
question for synthesis is whether to depend on a day-old third-party
wrapper, or on merman directly and find the Python spelling of
`resvg_safe`.
