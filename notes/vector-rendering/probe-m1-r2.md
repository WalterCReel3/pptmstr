# Probes M1, R1-hostile and R2: merman's resvg-safe output, resvg against hostile SVG, and the GIL

Board task `fig-probe-mr`. Questions from
`planning/2026-10-18-vector-displays-beside-typeset-text.md` §7; the planning
record is not edited here.

**Setup.** This worktree's `.venv`, with merman 0.9.1, resvg_py 0.5.0 (bundling
resvg 0.48.1) and numpy 2.4.6, installed by the operator
(`cmd-20261020T142812Z-84f75126fefc`). Linux, Python 3.11. Fonts: imscribe's
own files (`Face.PROSE`, `PROSE_BOLD`, `PROSE_ITALIC`: Roboto; `Face.CODE`:
Inconsolata) with `skip_system_fonts=True`. `font_family`, `sans_serif_family`,
`serif_family`, `cursive_family` and `fantasy_family` are mapped to `Roboto`,
and `monospace_family` to `Inconsolata`. Family names were read from the font
files' `name` tables.

**Command.** Every result below comes from

```
.venv/bin/python scripts/figure_render_probe.py all     # or m1 | r1a | r1b | r1c | r2
```

which exited 0 on 2026-10-20. The quoted output is from that run. SVGs and
PNGs go to `shots/figure-probe/`, which `/shots/` in `.gitignore` covers. No
sanitiser runs anywhere in the script: resvg gets the SVG exactly as written.

**Timings are from this machine.** The 1,200-label grid took 271–368 ms here
across runs; the operator's run of the same grid took 446 ms
(`svg-renderers.md`). Compare ratios, not absolute times, across the two
machines.

## Summary: what each result decides

| probe | result | decides |
|---|---|---|
| M1 | merman 0.9.1 accepts `{"svg":{"pipeline":"resvg-safe"}}` as written and emits **0 `<foreignObject>`** in all five families. An unknown pipeline name is an error, not a silent fallback. The native wheel has **no PNG capability** | **merman stays; mmdr is not needed** on the binding question (§3.3). pptmstr rasterises the SVG itself, as planned |
| M1, look | Labels are present and placed in all five. **In class, state and ER they are clipped**: merman wraps each label in a nested `<svg style="overflow:hidden">` sized to its own text metrics, and Roboto is wider than those metrics. Rewriting `overflow:hidden` to `visible` fixes every label | new: the sanitiser (or a post-pass) has to rewrite nested-`<svg>` overflow, or merman has to measure text with the font resvg draws in. §3.4 does not say this |
| M1, filter | merman emits `<filter>`/`<feDropShadow>` referenced by `filter:url(#…)` in all five. §3.4 as written drops the element and keeps the `url(#id)` reference, and resvg then **does not draw any element that refers to the missing filter**: every node box and participant box disappears | **§3.4 step 2 as written breaks every merman diagram.** Either the reference goes with the element, or `filter`/`feDropShadow` join the allow-list |
| M1, time | resvg at natural size: **69–112 ms** per diagram (as emitted), **36–66 ms** with the filters removed. At `width=800`: 70–282 ms | resvg sits on the ~100 ms line §3.3 set as its flip condition. **The drop-shadow filter is about half the cost.** The full R1 (20 real diagrams, novasvg alongside) still decides resvg against novasvg |
| R1a | Nested `<use>` fan-out: usvg refuses at its own node limit (`nodes limit reached`, about 0.3 s, 147 MB). Below the limit, **a 3.7 KB input took 7.2 s and 450 MB** (fan 40, depth 3, 64,000 leaves) | **§3.4 step 5 is needed as written.** resvg's own limit bounds memory, but not latency |
| R1b | `url()` to a file (absolute path, `file://`, relative under `resources_dir`) or to `http://127.0.0.1` in `fill`, `clip-path`, `mask`, `filter`, `style="fill:…"` and `marker-start`: **no open, no connect**. resvg treats each one as no value at all. The positive controls saw both an open and a connect | **§3.4 step 3's `url()` rule is not needed for safety against resvg.** Keep it: it is cheap, it is renderer-independent, and novasvg is untested here |
| R1c | Under `skip_system_fonts` with all families mapped, `font-family="Arial"` draws. Unmapped, it draws nothing, even as `"Arial, sans-serif"`. **The fallback for an unmatched family is `serif_family`**: mapping only that one makes `Arial` draw; mapping only `sans_serif_family` does not | **§3.4 step 7 is not needed for text to draw.** Mapping `serif_family` is what keeps unmatched names from going blank. Appending `sans-serif` still decides *which* face a name falls to once `serif_family` maps to a real serif |
| R2 | **resvg_py holds the GIL** while it renders. With a render running, a 1 ms ticker thread got 11 ticks in 3 s and a 281 ms max gap, one tick per render. Without a render: 2,517 ticks and a 1.65 ms max gap | **The worker must be a process, not a thread**, unless the binding releases the GIL |

## M1: merman's binding and resvg-safe pipeline

**API, as installed.** `merman.Merman().render_svg(source: str,
options_json: Optional[str]) -> str`, read from
`site-packages/merman/merman_uniffi.py` (UniFFI, binding API v7).
`runtime_catalog_json()` exists under that name. The `.so` contains the
strings `unsupported svg.pipeline:` and `violates the resvg-safe contract`.

**Output, abridged:**

```
merman 0.9.1 resvg_py 0.5.0 resvg 0.48.1
runtime_catalog capabilities.output_ids: ['ascii', 'svg']
runtime_catalog output_contracts ids: ['ascii', 'svg']
'png' anywhere in catalog JSON: False
render_png: Binding operation `png` requires capability `png`, which is not exposed by target `native`
unknown pipeline: unsupported svg.pipeline: no-such

[flowchart]
  merman default       3.2 ms   81681 B  {'foreignObject': 12, 'text': 0, 'style': 1, 'marker': 12}
  merman resvg-safe   14.0 ms   97501 B  {'foreignObject': 0, 'text': 14, 'style': 1, 'marker': 12}
  root: {'width': '100%', 'height': None, 'viewBox': '4 4 462 860', 'style': 'max-width:462px'}
  resvg natural    111.9 ms  -> (462, 860)
  resvg width=800  278.3 ms  -> (800, 1490)
  generics unmapped vs mapped differ: True
  element set: 171 elements, 12 <use> (0 chained), ['circle', 'defs', 'feDropShadow', 'filter', 'g', 'marker', 'path', 'polygon', 'rect', 'style', 'svg', 'text', 'use']
  nested <svg>: 12, overflow:hidden 0, overflow:visible 13, <filter> 2, filter:url refs 5
  variant overflow-visible                    110.1 ms  differs from as-emitted: False
  variant filter-elements-dropped-refs-kept    45.7 ms  differs from as-emitted: True
  variant no-filter                            53.5 ms  differs from as-emitted: True
[sequence]
  merman default       1.9 ms   29904 B  {'foreignObject': 0, 'text': 28, 'style': 1, 'marker': 8}
  merman resvg-safe    4.2 ms   29519 B  {'foreignObject': 0, 'text': 28, 'style': 1, 'marker': 8}
  root: {'width': '100%', 'height': None, 'viewBox': '-50 -10 1060 706', 'style': 'max-width:1060px'}
  resvg natural    100.0 ms  -> (1060, 706)
  resvg width=800   70.3 ms  -> (800, 533)
  element set: 126 elements, 0 <use> (0 chained), [... 'line', 'symbol', 'tspan' ...]
  nested <svg>: 0, overflow:hidden 0, overflow:visible 0, <filter> 1, filter:url refs 12
  variant filter-elements-dropped-refs-kept    63.5 ms  differs from as-emitted: True
  variant no-filter                            65.9 ms  differs from as-emitted: True
[class]
  merman default       2.2 ms   29936 B  {'foreignObject': 17, 'text': 0, 'style': 1, 'marker': 20}
  merman resvg-safe   10.8 ms   52182 B  {'foreignObject': 0, 'text': 17, 'style': 1, 'marker': 20}
  root: {'width': '100%', 'height': None, 'viewBox': '4 4 337.65749999999997 657', 'style': 'max-width:337.657px'}
  resvg natural     69.5 ms  -> (338, 657)
  resvg width=800  280.4 ms  -> (800, 1556)
  nested <svg>: 17, overflow:hidden 17, overflow:visible 1, <filter> 2, filter:url refs 5
  variant overflow-visible                     66.2 ms  differs from as-emitted: True
  variant no-filter                            36.0 ms  differs from as-emitted: True
[state]
  merman default       2.9 ms   47472 B  {'foreignObject': 14, 'text': 0, 'style': 1, 'marker': 2}
  merman resvg-safe   12.3 ms   67151 B  {'foreignObject': 0, 'text': 15, 'style': 1, 'marker': 2}
  root: {'width': '100%', 'height': None, 'viewBox': '4 4 415.1333333333333 885', 'style': 'max-width:415.133px'}
  resvg natural     84.3 ms  -> (415, 885)
  resvg width=800  248.2 ms  -> (800, 1707)
  nested <svg>: 14, overflow:hidden 14, overflow:visible 0, <filter> 2, filter:url refs 9
  variant no-filter                            47.3 ms  differs from as-emitted: True
[er]
  merman default       2.9 ms   59970 B  {'foreignObject': 55, 'text': 0, 'style': 1, 'marker': 8}
  merman resvg-safe   20.1 ms  104875 B  {'foreignObject': 0, 'text': 38, 'style': 1, 'marker': 8}
  root: {'width': '100%', 'height': None, 'viewBox': '4 4 402.14 695', 'style': 'max-width:402.14px'}
  resvg natural     84.9 ms  -> (402, 695)
  resvg width=800  255.3 ms  -> (800, 1384)
  element set: 460 elements, 38 <use> (0 chained), [...]
  nested <svg>: 38, overflow:hidden 38, overflow:visible 0, <filter> 2, filter:url refs 5
  variant overflow-visible                     80.7 ms  differs from as-emitted: True
  variant no-filter                            46.0 ms  differs from as-emitted: True
```

The diagram sources are in `DIAGRAMS` in the script. They are written the way
Claude usually writes them: quoted labels with punctuation, edge labels, a
subgraph, `alt`/`else` and a note, `<<abstract>>` and generics,
cardinalities, a composite state with a note, ER attribute blocks with
PK/FK/UK. They are five diagrams written for this probe, not the 20
real ones R1 calls for.

**Looked at** with the Read tool (`shots/figure-probe/m1-*-natural.png`,
`m1-sequence-width800.png`, and the `-overflow-visible` and
`-filter-elements-dropped-refs-kept` variants):
- **flowchart, sequence:** every label is present, legible and placed inside
  its shape. Edge labels sit on their edges.
- **class, state, ER:** every label is placed, but **many lose their first
  and last glyphs**. Examples: `Animal` shows as `Anima`, `CUSTOMER` as
  `USTOMEF`, `pause()` as `pause(`, and `customer_id` as `ustomer_ic`. The
  cause is in the SVG: each label is a `<use>` of a `<g>` holding a nested
  `<svg width=W style="overflow:hidden">`, where W is merman's own width for
  the text. merman's measurer assumes its default face, `"Recursive Variable"`,
  and Roboto at 14 px is wider. Rewriting `overflow:hidden` to
  `overflow:visible` gives fully legible labels in all three
  (`m1-{class,state,er}-overflow-visible.png`). The residue is cosmetic: an
  edge label's grey background is still merman's narrower width, so in the
  state diagram `pause()` and `resume()` now overrun their backgrounds and
  nearly touch. Flowchart labels already use `overflow:visible`, which is why
  they are whole.
- **sequence:** the note "The token expires after 15 minutes" draws its text
  but no visible box, both as emitted and with filters removed. Not
  investigated.
- **filter references kept, filter elements dropped:** in the flowchart,
  every node shape and the subgraph box vanish and only text and edges
  remain. In the sequence diagram, every participant box vanishes. This is
  SVG's rule, which resvg follows: an element whose `filter` points at a
  missing element is not rendered. R1b below shows the same thing on a bare
  rect.

**Natural size (§3.6).** Every merman root has `width="100%"`, **no
`height`**, a `viewBox`, and `style="max-width:Npx"`, where N equals the
viewBox width. The default pipeline writes `max-width: 360px` (with a space)
and resvg-safe writes `max-width:360px`, so a parser has to allow both. resvg
given no `width` renders at the viewBox size: 462×860 for a 462×860 viewBox.
The §3.6 rule ("read the `max-width` if present, else take `viewBox` user
units as px") holds for merman, and either source gives the same number. The
sanitiser has to keep the root `style` (step 4 does, since it has no `url(`),
or the size has to be read before sanitising.

**Element set against §3.4's allow-list.** merman's resvg-safe output uses
`svg` nested (allowed, since `svg` is on the list), `symbol`, `use`, `line`,
`tspan` (allowed), and `filter` and `feDropShadow` (**not** allowed). No
`<use>` targets an element that itself contains a `<use>`, so step 5's
refuse-chained-`use` rule does not reject merman output.

**Pipeline cost.** resvg-safe costs merman 4–21 ms against 2–3 ms for the
default pipeline. It is small next to the rasterisation.

**resvg cost.** At natural size, resvg takes 69–112 ms. With the drop-shadow
filter removed, it takes 36–66 ms. `width=800` up-scales the three narrow
diagrams (338–462 px viewBox) to about 3–5× the pixels, and then costs
248–280 ms. The cost tracks raster area more than label count. The flip
condition in §3.3 ("real diagrams cost more than ~100 ms") is straddled:
flowchart and sequence are at or just over 100 ms as emitted, and none is
over it once the filter is gone. [inferred: the drop shadow is a full-group
raster effect, so its cost scales with area]

**Generic mapping.** For every diagram, the output with generics unmapped
differs from the output with them mapped. merman's font stack is
`"Recursive Variable",arial,sans-serif`, so it ends in a generic, and
unmapped, nothing would draw (R1c).

## R1a: nested `<g>`/`<use>` fan-out

`fanout_svg(depth, fan)`: `L0` is one rect and one text, `Lk` is a `<g>` of
`fan` `<use href="#L(k-1)">`, and the root uses `L(depth)`. Each case runs
in a child process under `RLIMIT_AS = 2 GiB` and a 30 s timeout. The child
reports `ru_maxrss`.

```
each case in a child: RLIMIT_AS=2048 MiB, timeout 30s
  fan=10 depth=1 leaves=          10  rc=0 {"bytes": 495, "s": 0.003, "maxrss_mb": 53.6, "status": "ok"}
  fan=10 depth=2 leaves=         100  rc=0 {"bytes": 800, "s": 0.013, "maxrss_mb": 54.0, "status": "ok"}
  fan=10 depth=3 leaves=       1,000  rc=0 {"bytes": 1105, "s": 0.118, "maxrss_mb": 59.1, "status": "ok"}
  fan=10 depth=4 leaves=      10,000  rc=0 {"bytes": 1410, "s": 1.276, "maxrss_mb": 116.0, "status": "ok"}
  fan=10 depth=5 leaves=     100,000  rc=0 {"bytes": 1715, "s": 0.304, "maxrss_mb": 147.9, "status": "ValueError: SVG data parsing failed cause nodes limit reached"}
  fan=10 depth=6..8                   (same ValueError, 0.30-0.37 s, ~147 MB)
  fan=20 depth=3 leaves=       8,000  rc=0 {"bytes": 1975, "s": 0.925, "maxrss_mb": 104.4, "status": "ok"}
  fan=30 depth=3 leaves=      27,000  rc=0 {"bytes": 2845, "s": 3.065, "maxrss_mb": 221.4, "status": "ok"}
  fan=40 depth=3 leaves=      64,000  rc=0 {"bytes": 3715, "s": 7.241, "maxrss_mb": 450.5, "status": "ok"}
  fan=50 depth=3 leaves=     125,000  rc=0 {"bytes": 4615, "s": 0.299, "maxrss_mb": 147.0, "status": "ValueError: SVG data parsing failed cause nodes limit reached"}
```

**Conclusion.** usvg has its own expanded-node limit, which fails fast
(about 0.3 s) somewhere between 64,000 and 100,000 leaves of this shape. It
is a `ValueError`, catchable from Python. So the multiplication cannot
exhaust memory. Below the limit, though, cost grows roughly linearly with
expanded leaves, at about 0.11 ms each: **7.2 s and 450 MB from a 3.7 KB
input.** On a worker that is a frozen figure for seconds and half a GB
resident. **§3.4 step 5's post-expansion element cap is needed.** A cap in
the low thousands of expanded elements admits every merman diagram here (the
largest, ER, is 460 elements and has no chained `<use>`).

## R1b: `url()` pointing outside the document

The targets are a real `ext.svg` (holding `#g` and `#g2`) and `ext.png` in a
fresh `$TMPDIR` directory, which is also `resources_dir`. An inotify watch
(`IN_OPEN|IN_ACCESS`, via ctypes) is on that directory, and a TCP listener on
`127.0.0.1:<port>` records every connect. Each attribute is tried with four
targets: an absolute path, `file://`, `http://127.0.0.1:<port>/ext.svg#g`,
and `ext.svg#g` relative. The output compares each render with the same rect
with the attribute absent, and with `url(#nonexistent)`.

```
  fill: url(#nonexistent) renders as blank
  fill        abs       ok as-if-attribute-absent  fs=[] net=[]
  fill        file://   ok as-if-attribute-absent  fs=[] net=[]
  fill        http://   ok as-if-attribute-absent  fs=[] net=[]
  fill        relative  ok as-if-attribute-absent  fs=[] net=[]
  clip-path: url(#nonexistent) renders as attribute-absent
  clip-path   abs|file://|http://|relative  ok as-if-attribute-absent  fs=[] net=[]
  mask: url(#nonexistent) renders as attribute-absent
  mask        abs|file://|http://|relative  ok as-if-attribute-absent  fs=[] net=[]
  filter: url(#nonexistent) renders as blank
  filter      abs|file://|http://|relative  ok as-if-attribute-absent  fs=[] net=[]
  style fill: url(#nonexistent) renders as blank
  style fill  abs|file://|http://|relative  ok as-if-attribute-absent  fs=[] net=[]
  marker-start abs|file://|http://|relative       fs=[] net=[]
  CONTROL <image href=ext.png> fs=['ext.png:OPEN', 'ext.png:ACCESS']
  CONTROL socket connect net=['GET /control HTTP/1.0']
```

(Rows that are identical across the four targets are folded here. The
script prints each one.)

**Conclusion.**
- resvg 0.48.1 opens no file and makes no connection for a non-fragment
  `url()` in any of these attributes.
- It parses such a value as invalid and renders as if the attribute were
  absent: a `fill` falls back to black, and a `filter` is ignored.
- The watch and the listener do work: the `<image href>` control produced an
  open, and the self-connect produced a request line.
- So step 3's `url()` rule is defence in depth against resvg, not a measured
  necessity. It is still cheap, and it is the only protection if novasvg ever
  sits behind the seam, which this probe did not test.

The same run shows the trap M1 hit: a **fragment** `url(#missing)` in
`fill`, `style` fill or `filter` makes the element vanish. Any sanitiser
that drops a referenced element must also drop the references to it.

`strace` is not installed here, so the watch is inotify on the target
directory only. A read of some other path would not show up. Every target in
the probe lived in the watched directory.

## R1c: a named family under `skip_system_fonts`

```
generics mapped to 'Roboto'/'Inconsolata'; skip_system_fonts=True; font_files=4 imscribe faces
  font-family='sans-serif'             DREW
  font-family='Arial'                  DREW
  font-family='Arial, sans-serif'      DREW
  font-family="'Arial', sans-serif"    DREW
  font-family='NoSuchFace'             DREW
  font-family='monospace'              DREW
  font-family='Courier New'            DREW
  font-family='Roboto'                 DREW
  and with generics unmapped (font_files + skip_system_fonts only):
  font-family='Arial'                  NOTHING DRAWN
  font-family='Arial, sans-serif'      NOTHING DRAWN
  font-family='Roboto'                 DREW
  and with one family option mapped at a time:
  only font_family       font-family='Arial'                NOTHING DRAWN
  only font_family       font-family='Arial, sans-serif'    NOTHING DRAWN
  only font_family       font-family='sans-serif'           NOTHING DRAWN
  only sans_serif_family font-family='Arial'                NOTHING DRAWN
  only sans_serif_family font-family='Arial, sans-serif'    DREW
  only sans_serif_family font-family='sans-serif'           DREW
  only serif_family      font-family='Arial'                DREW
  only serif_family      font-family='Arial, sans-serif'    DREW
  only serif_family      font-family='sans-serif'           DREW
```

**Conclusion.**
- §3.4 step 7 inferred that an unmatched named family draws nothing. That is
  true only when `serif_family` is not mapped. resvg falls back to the
  `serif` generic for a family list that matches nothing, so with
  `serif_family` mapped, `Arial` draws.
- `font_family` alone rescues nothing, which the planning record did not
  predict.
- The load-bearing control is therefore **map `serif_family`**, and §3.4's
  "map all four" already does that.
- Step 7 (append `sans-serif`) is not needed for text to appear. It still
  chooses the face: once `serif_family` maps to a real serif, `Arial` with no
  generic would draw in the serif. Keep it for that reason, not as a
  blank-text guard.
- The §3.4 test should assert the named-family case with only the mapping
  and without step 7, so that it holds the mapping and not the
  append.

## R2: does `svg_to_bytes` release the GIL?

A ticker thread sleeps 1 ms in a loop for 3 s and records the gap between
wake-ups. In the second half, another thread renders the 1,200-rect,
1,200-label grid from `svg-renderers.md` back to back with `MAPPED` fonts.

```
switchinterval 0.005
  no render:   ticks= 2517 max gap    1.65 ms  p99   1.47 ms
  with render: ticks=   11 max gap  280.76 ms  p99 280.76 ms  renders=12 each 271-323 ms
```

**Conclusion.** The ticker ran once per render: 11 ticks for 12 renders, and
the max gap equals one render. `resvg_py` 0.5.0 holds the GIL for the whole
of `svg_to_bytes`. A worker thread would stall the frame loop for the full
render, 70–280 ms for the merman diagrams above. **The rasteriser has to run
in a process**, for example a single long-lived worker process fed sanitised
SVG strings that returns PNG/RGBA bytes. The alternative is a binding that
releases the GIL (a patch to resvg_py's `py.allow_threads`, or another
binding), and §3.3's seam allows that. [inferred: no such binding was looked
for]

## Not run

- novasvg: not installed in this venv. R1's resvg-against-novasvg half is
  untouched.
- The 20 real Claude-written diagrams R1 calls for. The five here were
  written for the probe.
- merman's `host-text-measurement` constructor service, which might make
  merman size labels with Roboto's metrics and so remove the clipping at
  source. Seen in `runtime_catalog_json()` and not exercised.
- The macOS arm64 wheel. Every result is from Linux x86_64.
