# A figure in a reply is a hosted block beside the diff fences, drawn from SVG that pptmstr sanitised first

**Dated:** 2026-10-18 ·
**Status:** slice 1 (```` ```svg ```` fences, §8) is built, at commits
`912471d`, `0a542d1` and `6a285ea`, and the operator checked it in the running app
on Linux on 2026-10-21 (§8, slice 1). The ```` ```svg ```` fence itself was dropped
by the operator on 2026-10-22 (§8, "The svg fence is dropped"); slice 1's sanitiser,
render child and raster path stay, because Mermaid's SVG goes through them. Slice 2 (```` ```mermaid ````, `0e219b2`) is
built except its source/figure toggle, which is not declared; the
```` ```math ```` fence (`93345b4`) and slice 4, the session declaration
(`36fb336`), are built; the review of those three (task `fence-review`) and
its fixes, the figure card among them (task `fence-fix`, 2026-10-21), are in
the tree and not committed. Slices 3 and 5 are not started. Probes T1,
M1, R2 and R1's hostile half ran on 2026-10-20 and their decisions are written in
below. Mermaid is now drawn natively from a bounded display list, with no raster
and no card, and a diagram's own colours drawn under a legibility guard (§11,
task `native-mermaid`, 2026-10-22, committed `da1e0d8`), and its review's
findings addressed (§11.1, task `native-fix`, in the tree and not committed): the
child's reply is plain JSON, never unpickled. Nothing has run
on macOS or at a density above the operator's own ·
**Origin:** the operator, at launch, 2026-10-18 (§1) ·
**Evidence:** four research notes in `notes/vector-rendering/`
(`imscribe-seams.md`, `svg-renderers.md`, `mermaid.md`, `data-graphs.md`), the
results and notes of board tasks `seams`, `svg`, `mermaid`, `datagraphs`, four
operator-run probes named where they are used, the `review` task's findings,
probe S1, run in pptmstr's venv during the revision (§3.6), and the probe notes
`probe-t1.md` (task `fig-probe-t1`) and `probe-m1-r2.md` (task `fig-probe-mr`),
cited below as **[probe T1]**, **[probe M1]**, **[probe R1-hostile]** and
**[probe R2]**: run by builders in this worktree's `.venv`, relayed here from
their notes and not re-run by the writer of this revision. The decisions taken
on them are the lead's, 2026-10-20. **[probe R3]** is
`scripts/figure_render_probe.py r3` and the measurements beside it, run by the
`fig-fix` builder on 2026-10-20 against slice 1's sanitiser and render child on
Linux x86-64; nothing marked R3 ran on macOS ·
**Engages with:** `pptmstr/ui/typeset/views.py` (the module docstring's diff-fence
rule, `_captioned`, `_Fences`), `pptmstr/ui/typeset/gate.py` (`_HIDING_RULES`,
`_reader`), and the three-pin comment in `pyproject.toml`.

**One direction is recommended. It is a pptmstr-only change: a second `HostFor`
beside `_Fences` answers for ```` ```svg ```` and ```` ```mermaid ```` fences.
It hands every SVG, whoever produced it, to an allow-list sanitiser that pptmstr
owns, rasterises the result at the drawn width behind a one-function renderer
seam, and draws the texture in the host's rectangle. The figure mark goes on a
fence only once it has committed: imscribe asks a host again only about a block
whose value changed (§3.6, probe S1, run). Data plots are drawn natively
through ImPlot, which is already in the wheel, and they do not go through SVG.
No pin moves. A figure is a full-width block. "Side by side" means source beside
figure inside that block, not prose flowing around the figure (§4).**

How to read the attributions. **[ran]** means the writer of this record executed it.
**[op-run]** means an operator-run probe, quoted from a note that cites the command
id. **[read]** means the writer read the file or URL this session. **[relayed]**
means a note's or a worker's claim that this record did not re-check. **[unverified]**
means nobody checked it. **[judgement]** is an opinion, labelled as one. The notes
carry finer citations. This record cites the note when it relays one.

---

## 1. What was asked

The launch premise, verbatim:

> One very important aspect of documentation in markdown is the support of Mermaid
> diagrams. I would like you to research the ability to embed vector displays
> directly side-by-side/embedded with imscribe text/markdown. Ideally any new
> functionality could support simple SVG (no scripting) and mermaid and may even be
> flexible enough to display general graphs or data representations from Claude
> output.

The premise names three inputs: script-free SVG, Mermaid, and general graphs or
data from Claude's output. It names two placements: embedded in the text, and side
by side with it. That is all a person said. The session ran unattended, and every
decision below was an agent's.

## 2. What the tree already has, and where the notes were wrong about it

### Checked this session

The spot-checks behind the recommendation:

- **The seam exists in the pinned imscribe.** `Hosted` and `HostFor` are at
  `imscribe/ui/typeset_view.py:683-706`. `_draw_hosts` (`:3349-3371`) sets the
  cursor and calls `hosted.draw` from Python, and pushes no clip rect. [read,
  ~/Source/imscribe at `64b752c`; `imscribe-seams.md` reports that the pinned
  `af06f77` differs only in three docs]
- **A top-level fence commits when its closing line ends**
  (`imscribe/typeset_markdown.py:2244-2246`). A fence inside a list or quote is
  read whole with its container (`:2253-2258`). [read] The seams note also ran
  this. [relayed]
- **The text-jump defect is imscribe's own, stated.** "text in the window is not
  held in place when a widget above the window changes height ... request C2"
  (`imscribe/ui/typeset_hosts.py:45-47`). [read]
- **A Host block is not searched** (`imscribe/typeset_find.py:49-50`). [read]
- **pptmstr's diff rule waits for the stream to close.** The rule is in
  `pptmstr/ui/typeset/views.py:60-67`, and `Stream._ink` sets
  `captioned.hosting = self._closed and diffs.adopted()` and then
  `view.host_for = _fences() if captioned.hosting else None`. [read]
- **pptmstr shows `![](...)` as text on purpose.** `_HIDING_RULES` names
  `"image"` (`gate.py:149`), and `_reader` disables it on
  `markdown_rules._parser(False, False)` (`gate.py:586`). [read]
- **The pins and the glibc floor.** `imgui-bundle==1.92.900`, `imscribe==0.2.0`,
  `imtypeset==0.4.3.192900; <marker>` are "one unit". imtypeset has wheels for
  "Linux x86-64 with glibc 2.28 or later and on macOS 14 or later on arm64"
  (`pyproject.toml`, the comment over the three pins). [read]
- **pptmstr runs `immapp.run(runner)` with no add-ons argument**
  (`pptmstr/app.py`, `main`). [read] So no ImPlot context exists today. That
  matters in §3.5.
- **Wheels on PyPI.** merman 0.9.1 has `py3-none-manylinux_2_35_x86_64`,
  `macosx_11_0_arm64` and `win_amd64`, all uploaded 2026-10-18. mmdr 0.4.0 has
  `cp39-abi3` wheels for `manylinux_2_17` x86_64 and aarch64 and for macOS
  x86_64 and arm64, and **requires `novasvg>=0.11`**. [read, pypi.org/pypi/merman/json
  and pypi.org/pypi/mmdr/json]
- **merman's Python spelling of the text-safe pipeline** is
  `options_json='{"svg":{"pipeline":"resvg-safe"}}'`, hyphenated. The accepted
  values are `parity` (the default, which "can include `<foreignObject>` HTML
  labels"), `resvg-safe` ("Converts HTML labels, applies compatibility cleanup,
  and validates the result") and `readable`. [read,
  github.com/Latias94/merman `docs/bindings/OPTIONS_JSON.md` on `main`]. **Run
  against 0.9.1: accepted exactly as spelled**, through
  `merman.Merman().render_svg(source, options_json)`; an unknown pipeline name
  raises `unsupported svg.pipeline` rather than falling back [probe M1].

### Where this record disagrees with the notes

1. **A figure fence does not reach a pptmstr view as the seams note's probe
   saw it.** That probe used imscribe's parser without pptmstr's reader. In
   pptmstr, `_captioned` (`views.py`) rewrites every fenced block that is not
   hosted to `text=f"{said}{note}\n{block.text}"`: the info string becomes the
   first line of the code. A hosted diff escapes this only because `_captioned`
   marks it `language=_HOSTED_INFO` and keeps the text as read. [read]
   The caption alone would not force the design: `replace` keeps
   `language=said`, and the caption is exactly one line (the info string plus
   at most `syntax.TOO_LONG`, which has no newline), so a host keyed on the
   first word of `language` could take `text.partition("\n")[2]` [read,
   `views.py` `_captioned`; `syntax.py` `TOO_LONG`].
   **Consequence:** the figure is still marked inside `_Captioned`, with a mark
   of its own, for two other reasons:
   - the mark has to change the block's value when the fence commits, or the
     stream view never asks the host again (§3.6, probe S1) [ran];
   - `_Captioned`'s `own`/`sources` mapping is where a block that is not the
     stream's own is accounted for.

   **The figure mark must not ride `hosting`.** `hosting` is
   `diffs.adopted()` (and closed, in a stream), which is False under
   `ADOPT=="never"` or when `gate.can_draw()` is False, and `_open` and
   `Stream._ink` both set `host_for = None` when it is False [read]. A figure
   gets its own flag and its own reason to install the dispatcher.
   The seams note's "seam #1 is pptmstr-only" still holds.
2. **mmdr carries novasvg with it.** `mermaid.md` says mmdr "bundles novasvg".
   PyPI says it *requires* `novasvg>=0.11` as a separate distribution. [read]
   So choosing mmdr for Mermaid installs novasvg anyway, and adding resvg would
   make two SVG renderers. `svg-renderers.md` weighs resvg against novasvg
   without knowing this. It shifts the weight in §5.
3. **merman's PNG path is not in the default wheel.** `mermaid.md`
   recommends "its `resvg_safe` output (or its PNG path)". merman's PyPI page
   says "The default wheel supports SVG, ASCII, semantic/layout operations,
   analysis, validation, and document analysis" (no PNG), and that a missing
   capability "raises `MermanError.Binding` with `MISSING_CAPABILITY`" [read,
   pypi.org/pypi/merman/json, 2026-10-18]. The record does not rely on the PNG
   path. **Confirmed:** `runtime_catalog_json()` lists output ids `ascii` and
   `svg` only, and `render_png` raises "requires capability `png`, which is not
   exposed by target `native`" [probe M1].
4. **The notes disagree on whether resvg can carry Mermaid.** `mermaid.md`
   says an SVG renderer without foreignObject "draws these diagrams with no
   labels". `svg-renderers.md` resolves this by rewriting foreignObject to
   `<text>` in the sanitiser. Both are right about different inputs. Raw merman
   output has 6 foreignObject and 0 text; mmdr's has 0 and 6 [op-run,
   `cmd-20261019T023453Z-cee5264c75b3`, `mermaid.md`]. And the rewrite has
   never been written or run on a real Mermaid SVG [relayed, `svg` result,
   not_run]. §3.3 takes both routes to `<text>` and keeps the rewrite as the
   backstop.
5. **`svg-renderers.md` keeps run 1's void 82 ms timing in a paragraph that
   says it is void.** builder-2 kept it as evidence of the silent no-text
   failure. This record uses only run 2's figures:
   - resvg: 441–446 ms at 1×, 680 ms at `zoom=2`
   - novasvg: 52 ms

   These are for 1,200 rects and 1,200 labels at 800×600 [op-run,
   `cmd-20261019T024223Z-405ad323ce85`].

## 3. The recommended architecture

### 3.1 The intermediate: SVG for diagrams, draw calls for data

**SVG is the intermediate for anything that has to be laid out as a picture:**
- an ```` ```svg ```` fence, where the SVG is the input (dropped as a fence on
  2026-10-22, §8: SVG stays the intermediate, and Claude no longer writes it);
- Mermaid, where every Python route emits SVG [op-run, `mermaid.md`];
- later, DOT through pygraphviz and full Vega-Lite through vl-convert, both of
  which emit SVG with `<text>` labels. For vl-convert that is [op-run,
  `cmd-20261019T023541Z-d3030d576b1f`]; for DOT it is [relayed, unverified].

One sanitiser and one renderer then serve every producer. That is the
argument for SVG: the safety policy is written once, at the point every route
passes through.

**Data plots do not go through SVG.** They go through ImPlot, which is already
in imgui-bundle 1.92.900 and takes stdlib `array.array`, so it needs no numpy
[relayed: builder-4 ran it headless, `data-graphs.md` §A]. Its labels are
ImGui's font, so they match the pane at any DPI, and it has no texture to manage.
Sending plots through SVG would buy uniformity at the price of vl-convert's
84 MB [op-run, P1] and of font matching.

What unifies the two is the **host**, not the format. One figure host, keyed by
fence language, chooses a *producer*:
- SVG → sanitise → rasterise → texture;
- or plot spec → ImPlot calls.

[judgement]

### 3.2 Where the figure enters, and what lives where

- **Entry:** a block-level `Hosted` in a pptmstr view, through the `host_for`
  that `_Fences` already occupies.
  - `view.host_for` is one callable, so it becomes a dispatcher over
    {diff, figure}. `host_for` is assigned at four sites in `views.py`, but
    only two carry markdown fences: `Stream._ink` and `_open`. The other two,
    `_literal_segment` and the rows view, show a bare `MarkdownCode` with no
    language and stay `host_for = None` [read].
  - `_Captioned` gains a figure mark beside `_HOSTED_INFO`. The mark is put
    on only once the fence has committed (§3.6), and it has its own flag,
    separate from `hosting` (§2, item 1).
- **Layout consequence:** the figure takes the full measure less the block's
  indent, and its height is its own: `height_for(w) = min(w, natural_w) * h/w0`
  [relayed, `imscribe-seams.md` §2].
- **Draw:** `Hosted.draw` issues `imgui.image(...)` or ImPlot calls at the
  cursor. It must push its own clip rect, because `_draw_hosts` pushes none
  [read], and it snaps its origin to whole pixels first. Both are measured
  host constraints, listed with the rest in §3.6 "Host constraints"
  [probe T1].
- **Copy and find:** a copy gives `Hosted.text`: the fence as Markdown,
  info string included, so a copied reply still renders elsewhere
  [judgement]. It is reconstructed rather than verbatim (`host.fence_text`):
  backtick marks, three or one more than the longest run of backticks in the
  text, around the info string and the text, so it reads back as the same
  fence whatever marks it was written with (decided at `fig-host`). Find does
  not see a hosted figure [read]. That is accepted for now.
- **imscribe vs pptmstr:** all of the above is pptmstr's (`pptmstr/ui/typeset/`
  plus a new figure module). Nothing in the first three slices needs an imscribe
  release. Two upstream asks are worth filing but are not preconditions:
  - `_draw_hosts` clipping each host to its rectangle, which is one imscribe
    release;
  - imtypeset C2, holding text in place when a host above grows, which moves
    all three pins.

  Upstreaming a generic figure host to imscribe (seam 3 in `imscribe-seams.md`)
  is deferred until pptmstr's version has been used [judgement].
- **Not to plan against:** imscribe's reference core (`nodes`, `pack`,
  `shipout`, `selection`) and the `imgui_draw` backend that
  `imscribe/backends/__init__.py` still describes. Neither is on pptmstr's
  path; the backend module is deleted [relayed, `imscribe-seams.md` §0].

### 3.3 The renderer, and the Mermaid route

**The renderer: resvg (`resvg_py`, exact pin) behind
`rasterise(sanitised_svg: str, px_width: int, fonts) -> RGBA`, with novasvg as
the named alternative that fits the same seam.** This is a decision taken on
judgement, and the record says what overturns it (probe R1, §7).

Why resvg, weighed against what the operator-run probes showed:
- **Maturity.** resvg's conformance suite is about 1,600 tests [relayed]. Its
  security posture is documented: no network, no script [relayed, resvg
  README]. Its local-file read through `<image href>` is a known behaviour
  and was measured [op-run]. novasvg has been on PyPI since 0.0.1
  (2026-02-08), but went 0.10.0 → 0.11.1 between 2026-10-14 and 2026-10-17
  [read, pypi.org/pypi/novasvg/json].
- **Speed is novasvg's case, and it is not yet decisive.** novasvg was 8.5×
  faster on the synthetic grid. A typical Mermaid diagram has tens of labels,
  which extrapolates to tens of ms in resvg [inferred, assuming linear cost:
  `svg-renderers.md`]. Measured on merman output, it was 69–112 ms, cost
  tracking area more than labels (the decision below). The design below rasterises off the frame thread, so
  the 446 ms worst case costs latency, not frames.
- **foreignObject is not decisive either.** Both Mermaid routes below arrive
  as `<text>`, and the sanitiser rewrites any foreignObject that remains.
- **What would flip it:**
  - R1 shows real diagrams cost more than ~100 ms in resvg;
  - or the Mermaid route ends up being mmdr, which installs novasvg anyway
    (§2, item 2). Then one renderer is better than two, and novasvg replaces
    resvg behind the seam.

  [judgement]

**Decision, 2026-10-20: resvg stays.** Neither flip condition has fired.
- M1 keeps merman, so mmdr does not bring novasvg in (below).
- On five merman `resvg-safe` diagrams (flowchart, sequence, class, state, ER)
  at natural size, resvg took **69–112 ms as emitted and 36–66 ms with the
  drop-shadow filter removed** [probe M1]. The filter is about half the cost.
  §3.4 keeps `<filter>` out of the allow-list, so what resvg is given is the
  36–66 ms case, under the ~100 ms line.

What this does not show, so the condition stays live:
- It is **this machine**, which ran the 1,200-label grid in 271–368 ms against
  the operator's 446 ms; the operator's box is probably slower [probe M1].
- It is **natural size at DPI scale 1**. The cost "tracks raster area more than
  label count" [probe M1, inferred there]: at `width=800` the same diagrams took
  70–282 ms as emitted. A DPI scale of 2 quadruples the area; that was not run.
- The five diagrams were written for the probe, not the 20 real ones R1 asks
  for, and **novasvg was not run** (not installed). R1's comparison half is
  still open (§7).

**Mermaid: merman, called directly, with `svg.pipeline = "resvg-safe"`, as a
lazily imported optional extra.**
- merman is the engine either way: browserless, JS-free, tracks Mermaid 12.x,
  all 37 families admitted [relayed, `mermaid.md`]. Raw merman took 7.6 ms on
  one flowchart, and mmdr's merman backend 10.2 ms [op-run].
- The text-safe pipeline is documented as `resvg-safe` [read, `main`] and
  targets resvg/usvg consumers by name, which is the pairing above.
- **M1, run on 0.9.1:** `resvg-safe` is accepted as spelled and emits **0
  `<foreignObject>`** in all five families tried, with 14/28/17/15/38 `<text>`
  in flowchart/sequence/class/state/ER. It costs merman 4–21 ms against 2–3 ms
  for the default pipeline. Labels are present and placed in all five
  [probe M1]. Two things in that output need handling downstream:
  - every family uses a `filter:url(#…)` drop shadow, which §3.4 step 8 deals
    with;
  - in class, state and ER, labels lose their first and last glyphs. Each
    label is a nested `<svg style="overflow:hidden">` sized to merman's own
    text metrics (its default face), and Roboto is wider. Rewriting the
    overflow to `visible` gave whole labels in all three; an edge label's
    background stays merman's narrower width [probe M1]. **Decision: a Mermaid
    post-pass in slice 2 sets that overflow visible.** It is fidelity, not
    safety, so it is not the sanitiser's job. The named alternative is merman's
    host-text-measurement service, which might size labels with the face resvg
    draws in; it was seen in `runtime_catalog_json()` and **not tried**.
  - A sequence-diagram note drew its text but no visible box; not
    investigated [probe M1].
- **A diagram's `%%{ … }%%` directives are taken out before merman reads it**
  (`mermaid.without_directives`, decision 2026-10-21, lead, at fence-fix):
  every one, wherever it stands, across lines, in a linear scan. A
  model-written `init` configures the renderer, and pptmstr configures the
  renderer. What this closes is renderer configuration (theme, fonts, label
  mode, limits), not author styling: a diagram's own `style`, `classDef` and
  `linkStyle` lines are content and still paint what they name, dark included
  [read, guide-review finding 11]. Measured on merman 0.9.1, `resvg-safe`, on a three-node flowchart,
  rasterised by resvg with pptmstr's fonts [ran, fence-fix]:
  - `theme: dark` changes every fill and ink: ten times the dark pixels
    (3,053 to 32,783), on the light card every figure stands on (§3.6);
  - a `fontFamily` naming a face resvg lacks, in `init` or in
    `themeVariables`, is written into the SVG and drawn in the fallback face
    with the same ink (3,053 dark pixels either way): no label is lost;
  - `htmlLabels: true` is overridden by the pipeline (0 `<foreignObject>`);
  - `maxTextSize` (10 and 9×10⁸) and `maxEdges` (9×10⁸) changed nothing on
    that diagram. Whether a raised limit lets a large diagram run longer was
    not measured; the child's timeout bounds it either way.

- **A YAML front matter keeps only its content keys** (`mermaid.without_config`,
  decision 2026-10-21, lead, at fence-fix-2), for the same reason: `title` and
  `displayMode`, each with the lines indented under it; every other top-level
  key is taken out, `config` among them, and a front matter left with no key
  goes whole, since merman refuses an empty one. Kept by name rather than
  dropped by name, so a key written in a form merman reads and this pass does
  not recognise is removed rather than passed on. Measured on merman 0.9.1,
  `resvg-safe`, the same flowchart [ran, fence-fix-2]:
  - `config: {theme: dark}` restyles the output exactly as the dark `init`
    does, in flow or block style and with the key quoted; `config.fontFamily`
    is written into the SVG; `title` is drawn; an unknown key and a `<<` merge
    key change nothing;
  - merman reads a front matter only from the first line: after a byte-order
    mark, after a lone `\r` line end, and with the whole source indented, but
    not after a blank line or a `%%` comment. It closes at the next `---` at
    column 0 and not at an indented one;
  - so the pass runs after the directives come out (a directive line before a
    front matter would otherwise bring it to the first line unread), counts a
    lone `\r` and the YAML 1.1 breaks as line ends, and writes the front matter
    anew with `---` fences alone on their lines and `\n` breaks, so merman
    closes it where the pass did or earlier, never later. Its tests are
    `tests/test_figure_mermaid.py::test_front_matter_keeps_only_its_content_keys`
    and the real-child `..._front_matter_config_lays_out_as_the_diagram_without_it`;
  - a directive line takes its line end with it, `\r\n` and a lone `\r` as
    well as `\n` (guide-fix), so a front matter under it is on the first line
    again. Behind a blank or whitespace-only first line merman refuses the
    whole diagram (three sources, merman 0.9.1, 2026-10-21) [ran, guide-fix],
    so what was left there was code, not a dark figure.
- **One engine is kept for the child's life and dropped on any merman
  exception** (decision 2026-10-21, lead, at fence-fix): a binding that raised
  may be left in a state no later diagram recovers from, and a new
  `merman.Merman()` costs about 4 µs [ran]. Whether a panic does poison it was
  not measured; a stand-in engine holds the rule.
- **Its cost is the glibc floor.** merman's Linux wheel is
  `manylinux_2_35`, and pptmstr's floor is 2.28 [read]. On Linux with glibc
  2.28–2.34 the extra will not install, and a ```` ```mermaid ```` fence shows
  as code with a one-line reason. That is the same fallback as a render error.
  There the reason says mermaid is unavailable on this platform, and not that
  an extra is missing (`mermaid.missing_reason`, from `platform.libc_ver()`
  and the machine), since no wheel exists to install; on Linux other than
  x86-64 too. macOS arm64 is covered [read].
- **The gap may not be real.** mmdr's `manylinux_2_17` wheel builds merman's
  engine (mmdr's PyPI description: "`merman` *(default)*"), so merman's 2.35
  floor is probably its build host and not a requirement of the code
  [inferred]. **Missed alternative, now the first thing to try:** ask merman
  upstream for `manylinux_2_28` wheels, or build merman from git for pptmstr's
  floor. G1 still says how much the gap costs meanwhile. RHEL/Alma/Rocky 9
  and Amazon Linux 2023 ship glibc 2.34, inside the gap [unverified: recalled,
  not checked].
- **mmdr is the alternative that closes the gap today.** It has
  manylinux_2_17 wheels and emits `<text>` by default [op-run]. Against it:
  - its default backend *is* merman [read, pypi.org/pypi/mmdr/json], so
    taking mmdr does not replace merman's maintainer; it adds a second party
    (MohammadRaziei, who also maintains novasvg [relayed: `mermaid.md`,
    `svg-renderers.md`]) on top, with a vendored copy of merman whose version
    pptmstr cannot pin;
  - it `requires_dist` `novasvg>=0.11` [read], so novasvg is installed even if
    resvg draws mmdr's `<text>` output. mmdr does not force novasvg as the
    *renderer*, only as an installed package;
  - mmdr 0.4.0 (uploaded 2026-10-17 02:22) predates novasvg 0.11.1
    (2026-10-17 03:13) and pins only a floor, so a fresh install gets a
    novasvg mmdr was not built against [read, PyPI JSON].

  The record takes merman and pays the glibc gap until upstream wheels or a
  local build close it. **M1 settled the binding question for merman**: 0.9.1
  accepts `resvg-safe`, so mmdr is not needed for it [probe M1]. mmdr remains
  the named way to close the glibc gap, which M1 did not touch (G1).
- **Rejected for Mermaid:**
  - mmdc, which needs Node 22 and Chromium;
  - mermaid.ink and Kroki, which send the source off the machine;
  - mermaidx, at about 0.7 s per diagram even when warm [op-run].

  Costs are in §5.

### 3.4 Sanitisation: every SVG, every route

The input is model output, and an engine's output from model input is treated
the same way. The policy is the allow-list in `svg-renderers.md` §D, adopted
whole except for `pattern`, which was later dropped (step 2).

1. Reject any DOCTYPE. The check is case-insensitive and looks past the XML
   declaration and comments.
2. Allow these elements only:
   - structure: `svg g defs symbol use`;
   - shapes: `path rect circle ellipse line polyline polygon`;
   - text: `text tspan textPath`;
   - paint servers and masking: `linearGradient radialGradient stop clipPath mask`;
   - also `marker style title desc`.

   **`pattern` was on this list and is dropped** (decision 2026-10-20, lead,
   at fig-fix). resvg allocates a pattern tile whole, at its size under every
   transform above it, including a `<g transform>` on the shape it fills, so no
   check on the pattern alone bounds it: a 100-unit tile under `scale(160)`
   took 1 GB, and under `scale(1000)` asked for 40 GB [probe R3]. On Linux the
   render child's `RLIMIT_AS` catches that. **macOS does not enforce
   `RLIMIT_AS`, and that is the deciding cost**: there nothing but the render
   timeout would bound it. No merman output in M1 uses a pattern, and a fill
   that named one takes its fallback or `none` through step 8.

   `<foreignObject>` is **rewritten to centred `<text>`**, never passed through.
   `script`, `image`, `a`, `iframe`, animation elements and `feImage` are
   dropped.
3. Drop every `on*` attribute. `href` and `xlink:href` stay **only as `#id`**,
   and `xlink:href` is written out as plain `href` (SVG 2), so the output needs
   no xlink namespace. A kept plain `href` wins over `xlink:href` in either
   order; where the plain one is dropped (not a `#id`), the `xlink:href` is
   kept, where SVG 2 would draw nothing: a fidelity difference only.
   **Measured 2026-10-20 (fig-fix):** resvg_py 0.5.0 draws the same ink for a
   `<use>` and a gradient's `href` after the rewrite as for `xlink:href` before
   it. **The `filter` property is dropped** wherever it is set, attribute or
   CSS (decision 2026-10-20, fig-fix): with `<filter>` gone it can only name
   the wrong element or be a filter function, and resvg draws those;
   `filter="blur(200px)"` on 50 full-canvas rects took 44 s at 1600×1200
   [probe R3, `scripts/figure_render_probe.py r3`].
   **Any other attribute whose value contains `url(`** (`fill`, `stroke`,
   `clip-path`, `mask`, `marker-*`, `filter`, `style`) is dropped unless every
   `url(` in it is `url(#id)`. Steps 3 and 4 as `svg-renderers.md` §D wrote
   them covered only hrefs and CSS; presentation attributes are neither.
   **Measured: resvg 0.48.1 opened no file and made no connection** for an
   absolute-path, `file://`, `http://127.0.0.1` or relative `url()` in `fill`,
   `clip-path`, `mask`, `filter`, `style` fill or `marker-start`; it renders as
   if the attribute were absent [probe R1-hostile]. The watch was inotify on
   the target directory plus a local listener (strace is not installed), so a
   read of some other path would not have shown. **So this rule is a backstop
   for resvg, and it is kept**: it is cheap, renderer-independent, and the only
   protection if novasvg ever sits behind the seam, which was not tested.
4. In CSS (`<style>` and `style=`), **drop the whole declaration** (or the
   whole `<style>` element) that contains a backslash, `@import`, or a
   `url(` that is not `url(#id)`; do not strip substrings. A CSS escape makes
   `u\72l(` a url token under the CSS Syntax spec, so substring stripping can
   be evaded. (The review's second example, `ur/**/l(`, is not one: a comment
   splits it into two tokens. The rule covers it either way.) [unverified:
   recalled from CSS Syntax Level 3, not re-read; whether simplecss honours
   escapes is also unverified]
5. Cap the input bytes, the element count and the output pixels before
   rendering. **The element cap counts the tree after `<use>` expansion**.
   **Built (fig-sanitise):** every same-document reference, `href` and
   `url(#id)` in any attribute, adds the expanded size of its target, once per
   referencing element, summed over a memoised walk; a reference cycle is
   refused; nested `<use>` is not refused, because icon sets and Mermaid's
   markers reference definitions that reference others. A source-element cap
   alone does not bound nested `g`/`use` chains, which multiply. Added at
   fig-fix (2026-10-20), each from a measurement:
   - **an input element cap** (`MAX_INPUT_ELEMENTS`, 20,000, before
     anything is dropped), because the cleaning walks are Python on the frame
     thread: a megabyte of `<g/>` took 1.4 s;
   - **a layer chain cap** (`MAX_LAYER_CHAIN`, 4): masks and clip paths
     inside one another by nesting or by `url(#id)`. resvg holds a
     canvas-sized pixmap for each level: a mask chain 68 deep, the deepest the
     element cap admits (a chain in `<defs>` is counted quadratically), took
     655 MB and 11.8 s at 1600×1200; at 4096×4096 a chain of four peaked at
     692 MB of address space and eight at 1.28 GB [probe R3];
   - every pattern in the sanitiser is linear: three `fullmatch` patterns with
     `\s*` either side of a lazy or optional group took 8–26 s on a
     100,000-space run and did not finish in five minutes at the byte cap.
     Measured worst at the cap after the change is about 0.4 s
     (`tests/test_figure_sanitise.py`, bound 1 s).

   **What the caps do not bound, and what does** [probe R3, 1600×1200]:
   - *Render time.* A full-canvas layer costs about 0.1 s nested or not (62
     nested opacity groups 5.6 s, 62 siblings 6.2 s, 500 siblings 50 s);
     `marker-mid` on a 60,000-vertex path 17 s; 4,000 lines dashed at 0.001 over
     a minute. References made from `<style>` rules and a marker's repetition
     per vertex are not counted at all. `RENDER_TIMEOUT_S` (10 s) is the bound
     for all of these, and a render killed for time is retried once and then
     kept as failed (§3.6 "Fallback").
   - *Memory.* The pattern tile, the one allocation measured to grow without
     a bound the caps can see, is gone with `<pattern>` (step 2). What is left
     (references from `<style>` rules, anything unforeseen) is bounded by the
     render child's address-space limit, 1 GiB with `RLIMIT_AS`
     (`render.MEMORY_LIMIT_BYTES`): resvg's allocation fails, the child
     aborts, the I/O thread reports that as the render's failure and the next
     request starts a new child [ran, `test_figure_render.py`, with an
     unsanitised pattern]. The largest legitimate render, the 1,200-label grid
     at 4096×3072, peaked at about 240–310 MB of address space. **macOS does
     not enforce `RLIMIT_AS`**: there the timeout is the only bound on what is
     left.
   **Measured, and needed as written:** usvg refuses at its own node limit
   (somewhere between 64,000 and 100,000 leaves of the probe's shape, in about
   0.3 s), but below it **a 3.7 KB input took 7.2 s and 450 MB** (fan 40,
   depth 3) [probe R1-hostile]. resvg's limit bounds memory, not latency. The
   largest merman diagram tried (ER) was 460 elements with no chained `<use>`,
   so a cap in the low thousands of expanded elements admits it [probe M1].
6. Parse with stdlib `xml.etree`, after step 1, and re-serialise.
7. **Every `font-family` ends in a generic family.** The sanitiser appends
   `sans-serif` to a list that has none (`font-family="Arial"` is common in
   hand-written SVG). **This step decides the face, not whether text draws.**
   Measured under `skip_system_fonts`: with the generics mapped, `Arial`,
   `NoSuchFace` and `Courier New` all draw; resvg falls back to
   `serif_family` for a list that matches nothing, so mapping `serif_family`
   alone makes `Arial` draw, while mapping only `font_family` or only
   `sans_serif_family` does not [probe R1-hostile]. The step is kept so that a
   bare name falls to the sans face rather than to whatever `serif_family`
   maps.
8. **Remove every reference whose target did not survive.** As a final pass
   over the output tree, a `url(#id)` in an attribute or CSS declaration, or an
   `href="#id"`, whose `id` is not in the output is removed: the attribute or
   declaration goes, the element stays. Without it, step 2 erases every
   merman diagram's shapes: merman's `resvg-safe` output puts
   `filter:url(#…)` drop shadows on node and participant shapes, step 2 drops
   `<filter>`, and resvg does not draw an element whose `filter` (or `fill`)
   points at a missing id. Every node box and participant box vanished, leaving
   text and edges [probe M1; probe R1-hostile shows the same on a bare rect].
   Decision 2026-10-20, lead; passed to `fig-sanitise`. **Built with the
   lead's refinement:** in `fill` and `stroke` the value becomes its fallback
   (`url(#gone) red` is `red`) or `none`, which is what SVG 2 makes of a
   missing paint server; removing the attribute would have meant the default
   black. Anywhere else the attribute or declaration is removed.
   **A target of the wrong kind is dangling too** (fig-fix, 2026-10-20):
   `fill`/`stroke` must name a gradient, `clip-path` a `clipPath`,
   `mask` a `mask`, `marker-*` a `marker`. Measured on resvg_py 0.5.0: a
   `filter`, `clip-path` or `mask` naming a `<rect>` erases the shape, as a
   dangling one does; `fill` naming one paints nothing, ignoring its fallback;
   a marker naming one draws no marker. An `href` needs only its target to
   exist.

This is mandatory and not defence in depth, for one measured reason: **both
renderers read `<image href="/abs/path">` from disk and draw it**. resvg also
reads relative hrefs [op-run, `cmd-20261019T024223Z-405ad323ce85`]. Neither
binding can turn that off [relayed].

Mermaid source can itself carry links and images in labels [unverified for
merman], which is one more reason the sanitiser sits after the engine and not
only on ```` ```svg ```` fences.

**`filter` stays out of the allow-list, and so does the `filter` property.**
Its cost is measured in two places: merman's drop shadow is about half of
resvg's time on merman output (69–112 ms with it, 36–66 ms without) [probe
M1], and a `blur()` filter function on a full 1600×1200 canvas is about 0.9 s
[probe R3]. Its safety was not measured. With step 3 and step 8, a diagram
loses the shadow and keeps the shape.

**Fonts.** Pass imscribe's own font files with `skip_system_fonts=True`, **and
map `font_family`, `sans_serif_family`, `serif_family` and `monospace_family`**.
Without the mapping, resvg draws no text and raises nothing [op-run]. Of the
four, **`serif_family` is the load-bearing one for names that match nothing**
(step 7) [probe R1-hostile]. A test has to hold this. The test renders a
`<text>` with each generic family, **and with a named family that is not
installed (`font-family="Arial"`) without step 7's append**, so that it holds
the mapping and not the append, and asserts that each output differs from the
same SVG without the text [the without-append form is `probe-m1-r2.md`'s
recommendation].

The `<use>` fan-out, external `url()` in presentation attributes and a named
family under `skip_system_fonts` were run against resvg_py 0.5.0 in R1's
hostile half (steps 3, 5 and 7 above). Nothing here was run against novasvg.

**No figure route fetches anything.** vl-convert, if it ever ships (§3.5), sits
behind two layers:
- a pre-filter that rejects any spec with a `url` key anywhere;
- `allowed_base_urls=[]`, which was measured to refuse `data.url` [op-run, P1].

Image-mark URLs, fonts and plugins in vl-convert were not tested [relayed].

### 3.5 Data plots

**Native ImPlot driven by a declared Vega-Lite subset**:
- inline `values` only;
- marks `line point bar area rule arc`;
- `x`/`y`/`color` encodings.

The subset maps onto `plot_line`, `plot_scatter`, `plot_bars`, `plot_shaded`,
`plot_inf_lines` and `plot_pie_chart` [relayed, `data-graphs.md` §A]. A spec
outside the subset shows its source with the reason.

**This rests on an unmeasured judgement**: that Claude writes Vega-Lite
fluently and will use it when the session says it renders. Nobody measured
that. Probe P2 is unrun [relayed, builder-4]. The named fallback is CSV in a
fence with a chart hint in the info string (```` ```csv chart=line ````). It is
a smaller interpreter, and it is an invented convention. P2 decides between
them.

Two preconditions, both carried into the staged plan (§8):
- **An ImPlot call with no ImPlot context segfaults** (exit 139) and does not
  raise [relayed: builder-4 ran it, `data-graphs.md` §A].
- pptmstr creates no ImPlot context today (§2). The slice that adds ImPlot
  creates the context once at start-up, through hello_imgui's add-ons or
  `implot.create_context()`; which spelling is [unverified]. It also carries a
  test that makes the no-context path unreachable. A segfault cannot be caught
  in-process, so the test has to run the plot host in a subprocess, or assert
  the guard structurally [judgement].
- An inline plot takes `Flags_.no_inputs`, so the mouse wheel scrolls the
  transcript and does not zoom the plot [relayed; judgement].
- Whether `begin_plot` behaves when issued at a host's cursor mid-page has not
  been run (probe P3).

DOT through pygraphviz, and full Vega-Lite through vl-convert, are **on demand
only**. Neither has a measured demand. vl-convert is 84 MB installed on Linux
[op-run].

### 3.6 Streaming, and when a figure switches from code to rendered

**Decision: a figure renders from a *committed* fence, not from a closed
stream. Until it commits, it is code under its caption, as today.**

Two rules were on offer, and the record takes the first:
- committed fence, which imscribe supports [read];
- closed stream, which is pptmstr's rule for diffs [read].

Reasoning:
- pptmstr's diff rule exists because "whether a fence still arriving is an
  honest diff changes with each line of it" (`views.py:60-63`). That hazard is
  a property of an *open* fence. A committed fence's text is final, so the
  figure it becomes will not turn back into code.
- Waiting for the whole turn would leave a diagram as source while the reply
  under it goes on streaming for as long as the turn lasts. That is the case
  the operator is most likely to watch.

[judgement, built on the two read rules]

**The trigger has to be a change of value. Probe S1, run.** imscribe asks
`host_for` once per block *object*, and asks again only about "a block it
answered for" whose source span or settled flag moved
(`typeset_view.py:546-553`; `TypesetView._kept` walks `self._hosts` only;
`TypesetStreamView._asked_as` adds `settled`). The committed fence is the
**same object** as its last live form, so a host that declined the open fence
is never asked again: the figure would never render in a `Stream`, and closing
the stream does not help, because `hosting` changes only diff fences' value.

The probe, run 2026-10-18 by the writer of this revision in pptmstr's venv
(imscribe 0.2.0, imtypeset 0.4.3.192900, the pinned versions), with the
suite's own `seam` fixture. `MarkdownStream(themes.document_theme())` behind
pptmstr's real `views._Captioned`, a `TypesetStreamView` over it, `sync()`
after each `append`. The host answers only `svg` fences; policy
`decline-until-settled` answers once `number < len(view.stream.committed)` or
the stream is closed; `answer-open-too` always answers; `fix-a` is a
`_Captioned` subclass that sets `language` to a figure mark only on a
committed (or closed) fence, and the host answers only the mark. Each call
logs `(number, committed, closed, answered)`. [ran]

    cd <worktree> && /home/wreel/Source/pptmstr/.venv/bin/python -m pytest \
        -p tests.conftest $TMPDIR/s1/test_s1_probe.py -s -q -p no:cacheprovider --rootdir=.

    after "Intro paragraph.\n\n```svg\n<svg .../>\n": committed=1 live=1 same_obj_as_prev=False
    after '```'  : committed=1 live=1 same_obj_as_prev=True equal_to_prev=True
    after '\n'   : committed=2 live=0 same_obj_as_prev=True equal_to_prev=True
    after '\nAfter the fence.\n': committed=2 live=1 same_obj_as_prev=True equal_to_prev=True
    decline-until-settled close-marks-then-newline  calls=[(1, False, False, False)]  hosted_at_end=[]
    decline-until-settled close-line-whole          calls=[(1, False, False, False)]  hosted_at_end=[]
    decline-until-settled close-line-with-next-para calls=[(1, False, False, False)]  hosted_at_end=[]
    decline-until-settled char-by-char              calls=68 x (1, False, False, False)  hosted_at_end=[]
    decline-until-settled whole-at-once             calls=[(1, True, False, True)]   hosted_at_end=[1]
    decline-until-settled ends-on-marks-then-close() calls=[(1, False, False, False)] hosted_at_end=[]
    answer-open-too close-marks-then-newline  calls=[(1,F,F,T), (1,F,F,T), (1,True,False,True)] hosted_at_end=[1]
    answer-open-too close-line-whole          calls=[(1,F,F,T), (1,True,False,True)]            hosted_at_end=[1]
    answer-open-too close-line-with-next-para calls=[(1,F,F,T), (1,True,False,True)]            hosted_at_end=[1]
    answer-open-too char-by-char              calls=70 x (1,F,F,T), then (1,True,False,True)  hosted_at_end=[1]
    answer-open-too ends-on-marks-then-close() calls=[(1,F,F,T), (1,F,F,T), (1,True,True,True)] hosted_at_end=[1]
    fix-a (all six chunkings)                 calls=[(1, True, False)] once; after close() (1, True, True)  hosted_at_end=[1]
    3 passed

(Lines condensed: `F`/`T` are False/True, `<svg .../>` is the probe's
one-element SVG. The probe file is in `$TMPDIR` and is not committed.)
In the char-by-char feed the open fence is asked many times, once for each
read at which the live block is a new value; none of those is after the
commit, so a host that declines while open still never sees it settled.
`whole-at-once` is hosted under every policy because the fence has already
committed when it is first asked: a stream replayed whole after `_forget`
works, and only a fence watched arriving fails. That is why the defect would
not show in a test that feeds the stream in one piece.

**Decision: fix (a).** `_Captioned` puts the figure mark on a fence only once
`at < len(stream.committed)` or the stream is closed, so the block's value
changes at commit and the view describes it again and asks the host. Its
per-block cache key becomes `(plain, hosting, settled)`. Fix (b), answering
the open fence with a widget that draws the code until it settles (as
`TypesetDiffFences` does through `_asked_as`'s settled flag), also works
[ran, above] but means the figure host re-implements the captioned, lexed
code block that pptmstr already draws. (a) keeps code drawn as code [judgement].

What it costs, and what the builder has to get right:
- `Stream._ink` sets `host_for` only on a closed stream today. It has to set
  the dispatcher on an open stream as well. **No second diff rule is needed
  inside the dispatcher**: `_captioned` emits `_HOSTED_INFO` only when
  `hosting`, which in a stream is closed-and-adopted, and `_Fences` answers
  nothing else [read]. The existing test that an open stream hosts no diff
  stays and now covers the dispatcher.
- A fence inside a list item or quote commits only with its container
  [read]. It renders later, and that is accepted.
- At commit the code block (possibly long) is replaced by the figure's
  height; the text below moves then (imtypeset C2).

**Render trigger and threading.**
- The producer runs on a worker, never in `height_for` or `draw`. Mermaid
  layout plus rasterisation is 10 ms to several hundred ms [op-run, both
  probes].
- **The rasteriser is a long-lived child process that loads the fonts once,
  not a thread.** R2: `resvg_py` 0.5.0 holds the GIL for the whole of
  `svg_to_bytes`. A 1 ms ticker thread got 11 ticks in 3 s with a 281 ms
  maximum gap while renders ran, against 2,517 ticks and a 1.65 ms gap idle
  [probe R2]. A thread would stall the frame for the whole render. Long-lived
  rather than one process per figure, so that start-up and font loading are
  paid once [judgement; spawn cost was not measured]. A binding that releases
  the GIL would allow a thread again; none was looked for [probe R2].
- The process returns pixels; the texture upload runs on the GUI thread
  [relayed: `TextureGpu` "must be created on the GUI thread"]. Upload measured
  0.11 ms for 800×600 and 2.12 ms for 2000×1500, median, on llvmpipe, where an
  upload is a memcpy; a hardware driver was not tried [probe T1].
- This is the first thing that breaks `views.py`'s "Nothing is built over more
  than one frame" stance. The figure module states that exception in its own
  docstring.

**Height.** The height comes from the sanitised SVG, not from the raster, and
it does not wait for the worker:
- An ```` ```svg ```` fence's height is known **synchronously** in
  `host_for`: the sanitiser is pure and cheap, and the aspect ratio is read
  from its `viewBox` or `width`/`height`. `height_for(w) = min(w,
  natural_w) * h/w0`. No placeholder, so no jump beyond the one at commit.
- Mermaid's height is known only after layout, so its host answers a
  placeholder first and its height once the SVG exists: one more jump.
- **Built (mermaid-fence, fence-fix): two requests, a layout and then the
  unchanged raster submit.** `Renderer.lay_out(("layout", sha), source)` asks
  the child to run merman, the post-pass and the sanitiser and answer a
  `Laid`: the sanitised SVG and its natural size, and no pixels. The figure
  is then rasterised by the same `submit` an svg figure uses. Two requests
  rather than one that returns the size with the RGBA, because the raster's
  width is a function of the natural size (no wider than it, at the drawn
  width times the density), which is not known until layout, and because it
  keeps one raster path for both routes. The placeholder is
  `host.PLACEHOLDER_HEIGHT`, 120 px of image.
- **The held layouts are an LRU by content** (`host._Layouts`: by the mark's
  sha of source and merman version, refreshed on every read, bounded at 64 Mi
  characters of SVG), **and a mark keeps its own layout once it has seen it**
  (`FigureMark.sanitised`). `height_for` asks for the layout every frame, on
  screen or not, so with more figures than a bound and nothing pinned each
  answer evicted another figure's and they flipped between the placeholder
  and their height without end (`fence-review` #2; 300 diagrams in a test).
  The bound now serves re-hosting, and a layout evicted before its figure next
  reads it (more than the bound arriving in one pump) is asked for once more,
  not without end.
- **Every figure stands on a light card** (decision 2026-10-21, lead, at
  `fig-backdrop`, built at fence-fix). `host.FigureWidget.draw` paints a
  rounded rectangle in `host.BACKDROP` (`0xFAFAF7`, a named constant: no
  palette entry is near-white in every theme) inside the figure's own clip,
  before the image or the outline that stands for it, `host.FIGURE_PAD` (6 px)
  wider than the image on each side. The padding counts in `height_for`, so
  `height_for(w) = min(w - 2p, natural_w) * h/w0 + 2p`, and in the
  placeholder's height. Reason: SVG and Mermaid are authored for a white
  page, and merman's dark ink was lost on the dark pane
  (`shots/mermaid-window/3-drawn.png`); one card fixes both routes.
  **Rejected:** merman's `dark` theme, which fixes Mermaid alone and makes
  a figure's pixels depend on the app's theme. **Cost:** a bright card in the
  dark theme. A failed figure has no card, and a ```` ```math ```` formula has
  none: it is text inked by the theme. Checked in a real window under Xvfb in
  both themes, the light one with its ImGui style applied, with an svg fence, a
  flowchart, a sequence diagram and a math fence
  (`shots/fence-backdrop/check.py`, git-ignored): every label, arrow and edge
  of both diagrams and the svg's dark strokes read clearly on the card in both
  themes; the formula reads in the theme's ink in both [ran, fence-fix].
  **Superseded for Mermaid on 2026-10-22 (§11):** a diagram is drawn natively
  on the pane, in the theme's colours, with no card and no padding. The card's
  code stays, unused, in `host.RasterFigure` until a separate task removes it.
  *(Removed, §11.2.)*
- **Known flaw: an edge label's background keeps merman's narrower width.**
  The post-pass opens the nested label boxes, not the rectangles merman draws
  behind edge labels, which are sized by its own text metrics; a label in a
  face wider than merman's runs past its background, as "pause()" and
  "resume()" touch the box edges in the state diagram [probe M1; relayed].
- **"Natural size" needs a rule** for SVG with `width="100%"` or a `viewBox`
  only: read the `max-width` if present, else take `viewBox` user units as
  px, else refuse with a reason. **The rule holds for merman** [probe M1]:
  every root in five families was `width="100%"`, no `height`,
  `style="max-width:Npx"` and a `viewBox` whose width is N, so both sources
  give the same number. The default pipeline writes `max-width: 360px` and
  `resvg-safe` writes `max-width:360px`, so **the parser accepts `max-width:`
  with or without a space**. The root `style` must survive the sanitiser (step
  4 keeps it, having no `url(`), or the size is read before sanitising.
- **The size is cached by content, outside the `Hosted`**: a module-level,
  bounded map from `(sha of sanitised SVG, DPI scale, raster width)` to the
  natural size and the texture. **A figure does not jump only once without
  it.** `Stream._forget` (an epoch change, a face or
  size change, or `set_text` that does not extend) closes the view and makes a
  new one with no prior widget, and a settled entry is made again on an epoch
  change (`views.py`, `ViewCache` and the stream entry) [read]. Without the
  cache every figure re-renders and jumps again each time. With it, a
  re-hosted figure answers its height in `host_for` and does not jump.
- A transcript reopened and scrolled mid-way still sees each Mermaid figure
  above the viewport change height when its worker finishes, the first time
  it is rendered in the process [inferred]. imtypeset C2 is the real fix and
  moves all three pins.

**Raster size.** Rasterise at the **drawn** width × DPI scale, capped by the
output-pixel cap (§3.4 step 5), and re-rasterise when the drawn width has been
stable for a few frames; the height does not change when the raster does,
because it is a function of width either way. **This stands after T1, and
raster-once-and-minify is not reinstated** (decision 2026-10-20):
- **There are no mipmaps.** Both texture paths create textures with MIN and
  MAG filters `GL_LINEAR` and no level 1, read from GL state on the OpenGL
  backend; other backends and hello_imgui's source were not checked
  [probe T1].
- **10 px labels break up at 3× and above.** One flowchart-like diagram,
  rasterised at k× the drawn width and minified by the GPU on llvmpipe at DPI
  1: exactly 2× matched a CPU box filter; at 3×, 4× and 4.57× the label
  strip's error was 2.6–3.3 times the box filter's, and in
  `shots/figure-probe/q5-labels-3x-zoom.png` "Warn" closes toward "Wam" and
  "LRU" breaks into "I.RU" [probe T1]. They stay readable at 1:1 but look
  like a broken font. The lead looked at that sheet and agrees.
- The fear that 1 px strokes would break **did not hold**: no stroke pixel
  was lost at any ratio, because the strokes are in user units and scale with
  the raster. A non-scaling hairline was not tested [probe T1].
- Exactly 2× is safe but is not a design, since the ratio moves with every
  pane resize. Showing the previous texture minified while a re-raster is
  pending is acceptable as a transient up to about 2.5× [probe T1's
  recommendation, adopted as judgement].
- Mipmaps through PyOpenGL (`glGenerateMipmap` plus
  `GL_LINEAR_MIPMAP_LINEAR`) are an unmeasured alternative; PyOpenGL is a
  `dev` extra and it does not reach a non-GL backend [probe T1].

Wide Mermaid output at natural × 2 (6000×4000 is about 100 MB RGBA) would hit
the pixel cap anyway.

**Textures. Decision stands (2026-10-20): `TextureGpu` held by the bounded
content cache above.**
- `hello_imgui.create_texture_gpu_from_rgba_data` takes an H×W×4 uint8
  **numpy** array and returns an RAII `TextureGpu`. **Dropping the last
  reference frees the GL texture at once** (`glIsTexture`), and 60
  create/drop cycles at 2000×1500 left RSS flat [probe T1].
- **resvg_py returns only PNG** (`svg_to_bytes` is its one entry point), with
  adaptive row filters including Paeth, so this path needs a decoder [probe
  T1]. **Decision: Pillow decodes in the render process**, which then hands
  RGBA to the GUI thread. Rejected:
  - a hand-written decoder: Paeth is sequential per pixel, so a numpy decoder
    still loops per pixel in Python; its cost was not measured [judgement on
    T1's reading];
  - the numpy-free `image_and_size_from_encoded_data` path: it needs a
    non-empty `cache_key` (with `""` it returns an id already deleted, and
    drawing it is GL error 1282), and its cache is shared and clears only
    whole, via `free_image_cache()` [probe T1].
- The cache is bounded (LRU by bytes) and so frees textures nobody shows
  [judgement].

**Host constraints**, each measured in T1 on llvmpipe at DPI 1 [probe T1]:
- **Snap the host origin to whole pixels.** The view hands the host a
  fractional origin (y = 121.237). Drawn there unfloored, the 1× raster lost
  41% of its dark ink to a sub-pixel smear (MAE 6.22), about seven times worse
  than any minification measured.
- **Always push its own clip rect** (`push_clip_rect(rect, True)`). Partly
  scrolled out, the child window's clip already cuts the figure; but an image
  drawn 60 px taller than `height_for` overprinted 34,684 px of the prose
  below, and the host's own clip rect contained it to 0.
- **Free textures only outside a frame** (in `pre_new_frame`, or park an
  evicted one for a frame). Dropping a `TextureGpu` between `imgui.image` and
  the frame's render gave `GL_INVALID_OPERATION` (1282) and a blank figure. An
  LRU that evicts during `draw` because a new figure arrived would hit this.
  **Built (fig-fix, 2026-10-20):** `host.pump` runs from the app's
  `pre_new_frame` (`app.begin_frame`), every frame whether or not a figure is
  on screen. It takes the renderer's results, which is also where a render
  past its time is killed, uploads them, and lets go of what the previous
  pump parked. No figure draw pumps, so nothing is uploaded, evicted or freed
  inside a frame, and a hostile render is stopped with nothing on screen.
- **Snap to the nearest device pixel, clip to the block** (fig-fix,
  2026-10-20). The origin is rounded to the nearest whole pixel of the
  framebuffer (`display_framebuffer_scale`), not floored in ImGui units, so
  it is a whole device pixel at a fractional scale too. The clip rect is the
  block's own rectangle, unsnapped, so nothing is drawn above or left of it.
  Only density 1 was looked at on screen.
- **No raster side over 8192** (`render.MAX_TEXTURE_SIDE`, decision
  2026-10-20, lead, at fig-fix): the raster is shrunk to hold both sides after
  density, and the child refuses one that cannot (a figure more than 8192
  times as long as it is wide). **A conservative constant, not read from GL.**
  An upload over `GL_MAX_TEXTURE_SIZE` does not raise: on llvmpipe a 2×32768
  array returned a texture and left `GL_INVALID_VALUE` (1281), so a blank
  figure [ran, Xvfb]. The clamp is therefore the only guard, and reading the
  limit needs PyOpenGL, a `dev` extra. llvmpipe reports 16384 [ran], as GL 4.1
  on macOS does [unverified]; 8192 leaves room for an older or smaller GPU.
  The pixel cap already bounds area, so this shrinks only long rasters. An
  upload that does raise is that figure's failure in `pump`, without losing
  the other results.
- **The spelling is `imgui.image(imgui.ImTextureRef(tex.texture_id()),
  size)`**: on `TextureGpu`, `texture_id` is a method; on `ImageAndSize` it is
  an attribute.

**Fallback.** A failure leaves the source visible as the code block, with a
one-line reason in the warn ink, the way `_Warned` leads a diff. Failures
include a sanitiser refusal, a render error, a missing extra, an unsupported
Mermaid family, and an out-of-subset plot spec. The source is the content, and
it is never dropped [relayed from all three producer notes; adopted].
**Built (fig-fix, 2026-10-20):**
- A render failure is one of two kinds (`render.Failed.transient`). An answer
  the child gave (resvg refused it, a pixel or side cap, the renderer could
  not load) is kept for the content and never asked again. One the machine
  may have caused (the child died or aborted at its memory limit, was killed
  for time, did not start) is asked once more after `host.RETRY_AFTER_S`
  (5 s), with the outline at the figure's height meanwhile; a second is kept.
- The failed form is drawn on the draw list, clipped to the rectangle
  `height_for` gave, with both heights measured once per width and font
  size. A 1 MiB source cost 4.5 ms a frame to measure and 4.5 ms more to
  draw as a text item [ran, headless ImGui]; the draw list's text stops at the
  bottom of the clip.

## 4. Side by side

The operator asked for "side-by-side/embedded". What imtypeset can do decides
what each reading costs:

| reading | how | cost |
|---|---|---|
| **Embedded**: a figure in the flow, between paragraphs | the hosted block (§3.2) | none beyond §3; **recommended** |
| **Source beside figure** in one block | a two-cell `begin_table` inside `Hosted.draw`, behind a toggle, as `TypesetDiffView` lays out its side-by-side diff [relayed, `imscribe-seams.md` §6] | pptmstr only. The source half is inside an atom, so a drag selects it whole or not at all. **Recommended as the second step of slice 1 or 2** |
| **Prose flowing beside a figure**, a float | a new run kind or float in imtypeset, then imscribe, then a pin bump | all three pins, and the largest change on the list [relayed]. **Not recommended**: nothing in the premise needs it beyond what the row above gives |
| **An inline figure** in a line of text | the same imtypeset change | the same |
| **A figures pane** docked beside the transcript | a hello_imgui docked window | pptmstr only, and it loses the figure's place in the text. Deferred, not rejected [judgement] |

imscribe's `set_columns` flows one document through newspaper columns. It cannot
put text in one column and a figure in the other [relayed].

## 5. Alternatives rejected, each with its cost

- **Tessellating SVG into `ImDrawList`** (vector at any zoom).
  - `ImDrawList` fills only simple polygons: no holes and no
    self-intersection [relayed: stub].
  - It would need our own triangulator and our own SVG text layout, on top of
    svgelements, whose last release was 2023.
  - It would cost Python calls per vertex per frame.

  Its cost is a renderer we would write. Rejected.
- **plutosvg, which is already linked into imgui-bundle.** Its surface
  functions are not exported, so the output can be neither read nor freed
  [relayed; builder-2 ran it]. Rejected.
- **CairoSVG.** It needs system libcairo, its text support is poor, and it had
  a 2023 SSRF CVE (fixed by default from 2.7.0) [relayed]. Rejected.
- **skia-python, pyvips/librsvg, QtSvg.** They are larger, and their behaviour
  on foreignObject, style and markers was never checked [relayed]. Not
  rejected on evidence, just not needed.
- **mmdc, mermaid.ink, Kroki, mermaidx** for Mermaid (§3.3):
  - mmdc costs Node 22 and Chromium;
  - mermaid.ink and Kroki cost privacy and the offline guarantee;
  - mermaidx costs 0.7 s a diagram plus a JS engine [op-run].
- **mmdr plus novasvg as the first choice.** It covers the glibc gap. The cost
  of rejecting it is the glibc gap, which upstream `manylinux_2_28` wheels or
  a local merman build may close (§3.3). The cost of taking it is merman
  anyway (mmdr's default backend) plus a second party's wrapper and novasvg,
  with a vendored merman pptmstr cannot pin. This was the closest call in the
  record. M1 did not reverse it: merman 0.9.1 accepts `resvg-safe` [probe M1].
  G1 can still make the glibc gap the deciding cost.
- **Plots through SVG (vl-convert as the default).** 84 MB, a JS engine, and a
  network-fetch surface to police. Kept on demand.
- **Rendering `![](x.svg)` images.** **Declined by the operator, 2026-10-20:
  not now.** pptmstr shows images as text so that "the reader must show what
  was written" (`gate.py:150-153`) [read], and that rule stands. Rendering
  images would also need an imscribe reader change to make an image a block of
  its own [relayed]. A figure fence needs neither.

## 6. Dependency cost against the pins and the two platforms

None of these joins the three-pin unit. Each returns bytes or draws through
imgui-bundle's own Python API, and none links against imgui-bundle's ABI
[inferred, `svg-renderers.md` §Recommendation].

| dependency | slice | Linux x86-64 (floor glibc 2.28) | macOS arm64 (14+) | size | licence | age |
|---|---|---|---|---|---|---|
| `resvg_py==0.5.0` (resvg 0.48.1) | 1 | `manylinux_2_17` abi3 [relayed]; installed and run in the worktree `.venv` [probes] | `macosx_11_0_arm64` [relayed] | not measured | MIT / MIT-or-Apache [relayed] | binding: one maintainer [relayed] |
| `numpy` (pin from the install; the probes ran 2.4.6) | 1 | [unverified: floor of the current wheel] | [unverified] | ~15–20 MB [unverified] | BSD | mature |
| `Pillow==13.0.0` | 1, render process only | wheels for cp311–cp314 on manylinux_2_28 x86-64 [relayed: PyPI, checked by the lead and reviewer-3] | macOS 11 arm64 wheels [relayed: PyPI] | 22 MB installed on Linux (`PIL` 7.3 MB + `pillow.libs` 15 MB) [ran: du] | MIT-CMU [relayed: PyPI] | mature |
| `merman==0.9.1` (optional extra) | 2 | **`manylinux_2_35` only: no wheel for glibc 2.28–2.34** [read] | `macosx_11_0_arm64` [read] | 8.3 MB wheel [relayed] | MIT-or-Apache; EPL-2.0 ELK and OFL fonts per THIRD_PARTY_NOTICES, unread [relayed] | 2026-10-18; Python bindings "experimental" [relayed] |
| *alt:* `mmdr==0.4.0` + `novasvg` | 2 | `manylinux_2_17` + `manylinux_2_27/2_28` [read; relayed] | yes [read; relayed] | venv with merman, mmdr, mermaidx: 90 MB [op-run] | MIT | days old |
| ImPlot | 3 | in the wheel [relayed, ran] | in the wheel [unverified on macOS] | 0 | MIT | — |
| `pygraphviz` | on demand | `manylinux_2_28`, 5.5 MB [relayed] | 1.4 MB; whether it bundles Graphviz is [unverified] | | BSD-3 + EPL-2.0 Graphviz | |
| `vl-convert-python` | on demand | `manylinux_2_17`, 33.5 MB, 84 MB installed [relayed; op-run] | 29.7 MB [relayed] | | BSD-3 | |

The *latest releases* of the Python Mermaid packages are days old (merman
0.9.1 on 2026-10-18, mmdr 0.4.0 on 2026-10-17), but neither package is: merman
was first on PyPI on 2026-06-08 and mmdr on 2026-06-28 [read, PyPI JSON,
2026-10-18]. Exact pins are mandatory, and every bump is reviewed
like an imtypeset bump. **Nothing above has been installed or run on macOS**
[relayed from all four notes, and from both probe notes].

Slice 1's dependencies are `resvg_py`, `numpy` and `Pillow`, each pinned
exactly to what slice 1 installs. numpy is the cost of `TextureGpu`'s array
argument; Pillow is the cost of resvg_py returning only PNG (§3.6
"Textures"). The previous table named numpy alone, which T1 showed was short
by a decoder.

## 7. Open questions that need a measurement

The house rule: board the probe, and leave the fix undeclared until the
measurement names it. Each probe below names what it decides.

| probe | measures | decides |
|---|---|---|
| ~~T1~~ texture | **Run 2026-10-20** (`probe-t1.md`), in an imscribe `TypesetView` with pptmstr's theme on Xvfb/llvmpipe at DPI 1, not in the app. Texture lands in the host's rectangle; freed on last reference; no mipmaps; 3× and above breaks 10 px labels; host origin is fractional; freeing mid-frame is a GL error; resvg_py needs a PNG decoder | settled (§3.6): `TextureGpu` + bounded content cache stands, raster at the drawn width stands, Pillow decodes, four host constraints. Not run: hardware GPU, macOS, DPI ≠ 1, non-scaling hairlines, mipmaps via PyOpenGL |
| ~~S1~~ commit re-ask | **Run 2026-10-18 (§3.6).** A host that declines an open fence is never asked again; a `_Captioned` that marks the fence only once committed is asked once, at commit, in all six chunkings | settled: committed-fence through a commit-dependent mark (fix a) [ran] |
| ~~M1~~ merman binding | **Run 2026-10-20** (`probe-m1-r2.md`) on five families: `resvg-safe` accepted as spelled, 0 foreignObject, no PNG capability, roots fit the natural-size rule; drop-shadow filters on every family; labels clipped in class/state/ER | settled (§3.3): merman stays, mmdr not needed on the binding question; §3.4 step 8 added; slice 2 overflow post-pass. Not run: merman's host-text-measurement service, macOS |
| **R1** real diagrams | 20 Mermaid diagrams Claude actually wrote, all families it used, through resvg and novasvg after the sanitiser, timed and looked at. **The hostile half ran 2026-10-20** (`probe-m1-r2.md`): fan-out 7.2 s / 450 MB from 3.7 KB below usvg's limit; no open or connect through external `url()`; an unknown family falls back to `serif_family` | hostile half settled (§3.4): steps 3 (backstop, kept), 5 (needed as written) and 7 (decides the face) stand. **Still open:** the 20 real diagrams and novasvg, i.e. resvg vs novasvg on cost (§3.3) |
| ~~R2~~ GIL | **Run 2026-10-20:** `svg_to_bytes` holds the GIL; 281 ms tick gap against 1.65 ms idle | settled (§3.6): a long-lived child process, not a thread |
| ~~R3~~ cost inside the caps | **Run 2026-10-20** (`figure_render_probe.py r3`, fig-fix), Linux only: mask and clip chains hold a canvas pixmap a level; a pattern tile is allocated whole under its transforms (1 GB, 40 GB asked); layers, mid markers and fine dashes cost seconds from a few elements; `blur()` 0.9 s a canvas | settled (§3.4 steps 2 and 5): `<pattern>` dropped, layer chain cap 4, input element cap, `filter` property dropped, `RLIMIT_AS` 1 GiB on Linux, the timeout for time. **Open:** what is left of memory on macOS, where `RLIMIT_AS` is not enforced |
| **F1** foreignObject rewrite | the §3.4 rewrite applied to merman `parity` output and to mermaidx output, drawn in resvg and compared with novasvg's native foreignObject | whether rewritten labels are acceptable or the text-only foreignObject subset is needed |
| **P2** what Claude emits | about 20 "chart of…/diagram of…" prompts with and without the session's fence declaration; count fence languages [relayed design, `data-graphs.md`] | Vega-Lite subset vs CSV-with-info-string for plots; whether plots are worth slice 3 at all |
| **P3** ImPlot in a page | `begin_plot` issued at a host's cursor mid-page: clipping, scroll, the wheel with `no_inputs` | whether slice 3's host can draw ImPlot inline or needs a child window |
| **G1** glibc | which Linux machines pptmstr actually runs on fall in 2.28–2.34 | how much merman's floor costs |

Not probes, but unread: merman's THIRD_PARTY_NOTICES (licence review before
shipping); whether merman labels can carry links or images that survive into
the SVG.

## 8. Staged order of work (not declared as tasks)

Smallest useful slice first. Each slice ends with the figure visible in a real
window, not only with passing tests (STYLE.md §2).

**Mathematics comes in as a fence, by the operator's call, 2026-10-21.** The
operator found that nothing tells a session what renders, and that pptmstr reads
markdown with mathematics off (`gate._reader` on `markdown_rules._parser(False,
False)`; the lead's call in planning/2026-10-07-advtext-sets-the-text.md, "Math off
because `$PATH=$HOME` is a shell line"). Of four options -- leave it off, a
```` ```math ```` fence, display `$$` only, full `$…$` -- the operator chose the
fence, for now. Inline `$…$` and `$$` in prose stay text.
A ```` ```math ```` fence is the same shape as `svg` and `mermaid`, so it joins the
same dispatcher and the same table of what renders, and the route by which imscribe
sets it is probe `math-probe`'s to name. The table also feeds the session prompt
(§8 slice 4), which slice 1 shipped without.

**The svg fence is dropped, by the operator's call, 2026-10-22.** Mermaid and
mathematics cover what sessions draw. The first real SVG session (§10) showed that
hand-written SVG is the trap: a model laying out a picture by hand is the failure,
whatever the fence. Mermaid through merman covers all five diagram families that
were checked (flowchart, sequence, class, state, ER), each laid out by merman and
bounded and isolated in the render child. A native SVG fence comes back only if
interactive native rendering ever needs one.
- **What changed** (task `drop-svg-fence`): the `svg` row is gone from
  `figures.fences.RENDERED`, so the dispatcher, the session floor and the shipped
  skill all name ```` ```mermaid ```` and ```` ```math ```` and nothing else. A
  ```` ```svg ```` fence is ordinary code under its caption, as any unlisted
  language is: never marked, never hosted, no reason line. `host.mark` no longer
  sanitises a fence; every figure mark carries a Mermaid source. The floor and the
  skill lost the SVG-only advice (the element rules, the font-family generics, the
  600 px width, literal colours and the 11 px text size, which a Mermaid author
  cannot set); the card line now says a diagram's styled colours are read against
  white. The floor measured 855 characters with every row, and `PROMPT_LIMIT`
  went from 1,000 to 950.
- **What stays, until native Mermaid replaces them:** the sanitiser with its
  general SVG rules (§3.4), the render child, the raster path, the held layouts and
  the card. merman's output is SVG, and it passes the same post-pass, `sanitise()`,
  raster and card it did before.

**The math fence as built** (`93345b4`, task `math-fence`, builder-23;
[relayed] from its result unless marked):
- **Route (b):** a settled fence's body alone is read with mathematics on, as
  `$$` + body + `$$` (`gate.formula`), and the block is spliced into the
  document where the fence was (`views._formula_block`). Nothing else is ever
  read with mathematics on, so `$` in prose is text.
- **The pre-check:** the library is asked first whether it will set the
  formula, by laying out the block's text and runs as an imtypeset paragraph
  in `typesetter.base_style(theme)`, the style its document is set in. A
  refusal is answered there, and the fence stays code under
  `math not set: <reason>`.
- **The guard after the draw** (`views._formulas_refused`): a formula the view
  refuses all the same is read from `Document.block_error` and kept as refused
  (`gate.refused`), so that fence is code from the next reading and the entry
  is not sent to ImGui. Refusals are held apart from the asking's answers
  (`gate._REFUSED`), so reading other formulas cannot evict one and send the
  fence back to be refused again (fence-fix; `fence-review` #7a) [ran].
- **Copy gives the fence:** a selection touching a formula copies that
  formula's whole fence as written, wherever in it the selection starts or
  ends (fence-fix; `fence-review` #6) [ran]; a selection with no formula is
  the library's copy exactly.
- **No size-dependent refusal:** 2,336 radical, `\left…\right` and fraction
  shapes, each asked at 59 em sizes from 6 to 64.5 px, gave the same answer at
  every size. The size-change path is held by a stand-in refusal.
- **A formula wider than the pane scrolls sideways**, as a long code line
  does: `views._reach` counts formula blocks, so the window is told how far it
  reaches. It is not clipped; `math-probe`'s "cut at the edge" was `_reach`
  not counting it.
- **Known limit:** a math fence inside a list item or a quote is set at the
  left margin of its document, without the item's indent or the quote's rule.
  The body is read alone, and the fence's code block carries an indent that
  includes the code inset and no quote depth, so the container cannot be
  rebuilt cleanly from it (fence-fix; `fence-review` #10) [ran: the probe of
  `parse_markdown` that showed it].

**The formula's size** (task `math-size`, 2026-10-22). The operator found in the
running app that formulas rendered correctly but small for how legible math
glyphs are on screen, and that they could be twice as large as a floor while
still taking the formula's own size into account. Decided:

- **Twice the text's size, stepped down, never below 1.** A display formula is
  set at `views.FORMULA_SCALE` (2) times its text's size. It steps down, in
  eighths, to fit the measure and to stay within `views.FORMULA_LINES` (5)
  lines of its theme's paragraphs. It never goes below 1, the size it had
  before. A formula wider than the measure at 1 is set at 1 and scrolls
  sideways exactly as before, so nothing needs more scrolling than it did.
  This reads the operator's "as a floor" as the default rather than a minimum,
  because a minimum of 2 would make a wide formula scroll further than it does
  today.
- **The mechanism is the run's scale, not a theme.** imscribe 0.2.0 has no
  math size or display scale in its theme or `ReaderOptions`. Its
  `RunDescription.scale` reaches imtypeset's `Run.scale`, and the library sets
  a display formula at it linearly: `\sum_{i=1}^{n} i = \frac{n(n+1)}{2}` is
  95.43 x 37.66 px at 1 and 190.86 x 75.31 px at 2 [ran]. So the formula
  block's runs carry the scale. No theme is derived and the library is not
  edited. The prose around the formula keeps its own runs, and the scale
  follows the operator's size setting, because it multiplies the theme's
  paragraph size.
- **Measured once, sized at draw.** `gate.formula_size` lays the formula out
  once at 1 and keeps its width, its height and the theme's line pitch. The
  scale is arithmetic on those numbers and the draw's measure
  (`views.formula_scale`). A document re-hands its blocks to its views only
  when a scale changes (`_draw`), and a stream re-hands them when its
  `revision` changes (`_Captioned`). Neither path reads any text again.
- **Asked at the size drawn.** `gate.formula(body, theme, scale)` asks the
  library about the block at the scale it will be drawn at. A formula refused
  at a scale above 1 is set at 1, the size it was already asked at. A refusal
  by the view after the draw (`_formulas_refused`) is read from the block the
  view held at its drawn size. It is kept for the formula at every size, so
  the fence is then code. No size-dependent refusal is known (above), so no
  smaller size is tried.
- **Why 5 lines.** This comes from screenshots under Xvfb in both themes, at
  760 and 420 px, with prose around the formulas [ran,
  `shots/math-size/shoot.py`, git-ignored]. The sum above is 2.2 lines at 1 and
  is set at 2. `\sum_{k=1}^{n} \frac{\sum_{j=1}^{k} a_j}{\frac{b_k}{c_k}}` is
  2.7 lines at 1 and is set at 1.75, about five lines of the prose around it.
  An eight-term polynomial, 349.5 px wide at 1, is set at 2 in 760 px and at
  1.125 in 420 px, and fits both.
- **In the running app, 2026-10-22.** The operator ran a reply of physics and
  discrete-mathematics formulas (Lorentz factor, Maxwell, Schrödinger,
  Heisenberg, binomial theorem, Pascal's rule) on Linux and found sub- and
  superscripts clear rather than lost to blur; the lead read the screenshot,
  in which nested radicals, `\hbar^2/2m`, stretched delimiters and `\binom`
  render cleanly in both the session and detail panes, with even spacing around
  each formula [operator, ran; relayed by the lead].

**Slice 1 in the running app, 2026-10-21.** The operator ran `make run` on
Linux x86-64 and checked it against a scripted reply (two screenshots, read by
the lead):
- A1–A7: a fence is code while it streams, then becomes a figure when its fence
  closes; prose, code and an unknown named family are drawn in their faces; the
  text below is not overprinted; the diff fence still waits for the reply to end;
  a figure in a list item renders with its container; edges are crisp.
- B1–B7: resize, half-scrolled clipping, copy, theme switch, font size, and
  returning to a session.
- C1–C4: a DOCTYPE and non-XML stay code with their reason; a hostile SVG is
  drawn with its image, script and dangling filter gone; a 10×4000 figure draws
  as a strip.
- D1: no render child is left after quitting, across several runs.

[operator, ran; relayed by the lead]. C5, responsiveness while figures render,
is left to longer dogfooding. Not checked there: macOS, a second density, and
whether the `---`/`+++` lines drawn above a hosted diff are as they were before
the figure dispatcher.

0. **Probes.** S1 (2026-10-18), T1, M1, R2 and R1's hostile half
   (2026-10-20) have run, and their decisions are in §3.3, §3.4 and §3.6.
1. **```` ```svg ```` fences.**
   - **The sanitiser** comes first, as a pure module with no ImGui import, in
     the functional style of STYLE.md §1. It takes text and returns sanitised
     text or a refusal reason. Each hostile case from `svg-renderers.md`'s
     probe becomes a named test, and each test is mutation-tested:
     absolute and relative `<image>`, `file://`, `http`, `<script>`, `on*`, a
     DOCTYPE, billion laughs, CSS `url()`, oversize, foreignObject → text.
     **The dangling-reference pass (§3.4 step 8)** has its own tests: a rect
     with `filter="url(#gone)"`, and the same as a CSS `filter:` declaration,
     survive with the reference removed.
   - **The renderer seam** on resvg, in **a long-lived child process** that
     loads the fonts once, rasterises with resvg and **decodes the PNG with
     Pillow**, returning RGBA (§3.6). Plus the generic-family and
     named-family font tests (§3.4), the named family without step 7's append.
     Sanitiser tests also cover `<use>` fan-out, `url()` in presentation
     attributes, and a CSS escape (`u\72l(`).
   - **The figure host:**
     - a figure mark in `_Captioned`, put on only once the fence has
       committed, with its own flag separate from `hosting`;
     - a dispatcher composed with `_Fences` at the two fence-carrying
       `host_for` sites (`Stream._ink`, `_open`);
     - the height from the sanitised SVG in `host_for`, no placeholder;
     - a content-keyed, bounded cache of size and texture outside the
       `Hosted`;
     - the render process as its worker, and a `TextureGpu` uploaded on the
       GUI thread;
     - the host constraints of §3.6: the origin snapped to whole pixels, its
       own clip rect always pushed, textures freed only outside a frame, and
       `imgui.ImTextureRef(tex.texture_id())`;
     - `text` set to the fence as Markdown, reconstructed (§3.2);
     - the code-plus-reason fallback.
   - Tests:
     - the diff rule is unchanged (closed stream only), with the dispatcher
       installed on an open stream;
     - a figure fence is hosted on commit while the stream is open, fed in
       the S1 chunkings (closing marks then newline; closing line with the
       next paragraph; char by char; ending on the marks then `close()`);
     - a figure re-hosted after `Stream._forget` answers its height at once;
     - wiring: the dispatcher is what `Stream._ink` installs (STYLE.md §2,
       "Test the wiring").
   - Dependencies: `resvg_py`, `numpy`, `Pillow`, exact pins from the install
     (§6).
2. **```` ```mermaid ````** through merman `resvg-safe` (M1 kept merman).
   Into the same host, with the same sanitiser, after a Mermaid post-pass that
   sets the nested label `<svg>`s' overflow visible (§3.3). It is an optional
   extra, lazily imported, and a missing extra is the fallback with its
   reason. Then the source/figure toggle (§4, row 2).
   **Built (`0e219b2`), except the toggle, which is not declared as a task.**
   The two-request protocol, the held layouts and the card are in §3.6; the
   directives, the engine and the platform's reason are in §3.3. The post-pass
   touches only the `overflow` property of a nested `<svg>`'s `style` and its
   `overflow` attribute (fence-fix; `fence-review` #9).
3. **Plots** (only if P2 justifies it):
   - create an ImPlot context at start-up;
   - add a test that makes a context-less plot call unreachable (§3.5),
     written before any plot call is;
   - write the Vega-Lite-subset or CSV interpreter, whichever P2 picks;
   - draw with `no_inputs` inline.
4. **The session declaration** that tells Claude which fences render, and its
   limits. It is one table that both the prompt text and the host's dispatch
   read [relayed design, `data-graphs.md` §D]. It ships with slice 1 for `svg`
   and grows with each slice.
   **Built (`36fb336`, task `fence-prompt`, builder-25; amended at fence-fix).**
   - **One table:** `figures.fences.RENDERED`, rows `svg` and `mermaid`
     (figures) and `math` (a formula); the `svg` row was removed on 2026-10-22
     (above, "The svg fence is dropped"). The dispatcher (`host.is_figure_fence`,
     `views.is_math_fence`, `views.settles_rendered`) and the prompt
     (`fences.prompt`) read it and nothing else; a test adds a word to the
     table and finds the dispatcher settling it. A row whose renderer is not
     installed is left out of the prompt, read from metadata without importing
     it; the mermaid row asks `mermaid.version()`, the read `host.mark` refuses
     by. What a figure needs is read from `render.DISTRIBUTIONS`, the table the
     render child imports from (fence-fix; `fence-review` #12).
   - **Who is told:** every launch, solo or team, fresh or resumed
     (`driver.AgentSession._system_prompt`, appended after the lead's briefing;
     a resume sets `snapshot: False`), and every worker, at the end of its
     `AgentDefinition` prompt (`_team`), since a worker's output is drawn in the
     same panes (fence-fix, lead decision).
   - **It is hedged** (fence-fix, lead decision): the fences are drawn "when
     pptmstr typesets replies", since the operator can switch typeset text off
     after the launch and the prompt is not rebuilt for that; and it says no
     other fence is a figure or a formula, rather than "any other fence shows
     as code", which an honest ```` ```diff ```` fence does not.
   - **The cost:** 855 characters on every launch with every renderer
     installed, held under `fences.PROMPT_LIMIT` (950) by a test, since the
     drawing rules moved into the shipped `pptmstr:pptmstr-figures` skill (§10.5)
     and the svg fence was dropped (2026-10-22). A solo launch passes an append
     where it once passed none, which changes its argv.
   - builder-25 edited `pptmstr/figures/host.py` (its own word set, a second
     table) and four tests in `tests/test_driver.py` (the old solo and resume
     prompt shapes) outside its task's writes, to make the table single; posted
     as an incident and accepted by the lead [relayed].
   - The exact text, every renderer installed [ran, lead, `fences.session_prompt()`
     after task `drop-svg-fence`]:

    ## Fences that are drawn

    When pptmstr typesets replies, the pane showing them draws ```mermaid, ```math fences once each is closed; otherwise they show as written. No other fence is drawn as a figure or a formula. A fence that cannot be drawn is shown as its source under a one-line reason.
    $...$ and $$...$$ in prose are not rendered and show as written; put mathematics in a ```math fence.
    Write the fence; the pane is the display. Unless asked for an export, do not render to a file, look for a converter, or preview with other tools.
    For nodes and edges (dependencies, flows, sequences, states, entities) use ```mermaid, which lays them out. Never compute a graph layout by hand.
    Before drawing a diagram, chart or formula, load the pptmstr:pptmstr-figures skill: it has the rules these fences must follow, and they win over a skill's general advice.

5. **On demand only:** DOT through pygraphviz; full Vega-Lite through
   vl-convert behind the URL pre-filter.

Upstream asks, filed when slice 1 exists, not before:
- imscribe: `_draw_hosts` clips each host;
- imtypeset: C2.

## 9. Preconditions and risks this work inherits and does not fix

- **pptmstr's code inside a library boundary catches its own exceptions**
  (decision 2026-10-21, lead, at fence-fix, reversing the earlier "RuntimeError
  from `_Captioned`"). `gate.into_library` takes anything raised inside it for
  the library stopping, and the stop holds for every pane in the process until
  a font load clears it (`pre_frame` makes it the gate's failure). Verified at
  fence-fix [ran, against the code before the fix]: a raising
  `_Captioned._blocks`, a raising formula pre-check and a raising copy each
  stopped the library app-wide. So every pptmstr callback or wrapped piece of
  pptmstr work that runs inside one catches at its own edge, lets
  `TypesetUnavailable` through, logs the exception as pptmstr's defect at
  ERROR with its traceback (`gate.own_defect`, once a message), and degrades
  its own text alone: a stream's fences as the library read them, with a warn
  line under it (`_Captioned.raw`); a formula as code under
  `math not set: pptmstr error`; a copy as the library's `selected_text()`;
  the guard as "not a formula", which sends that entry alone to ImGui. The
  link handler (`views._Followed`) was the first of these. A new callback
  into pptmstr from inside the library follows the same rule.

  **The rule is about whose code raised, not about what was being attempted**
  (decision 2026-10-21, lead, at fence-fix-2). A call into the native module
  or a read of a library view or document inside such a callback is still the
  library's, so it is wrapped narrowly in `into_library` and a failure there
  stops the library as it does everywhere else: the formula pre-check's
  `ts.Paragraph`, `layout` and `formulas()` (`gate._laid_formula`, inside
  `_refusal`), and the copy's reads of `selection`, `blocks`, `texts`,
  `separator`, `selected_text()` and `native_document.range_text`
  (`views._copy_with_fences`, `_range_text`). Only pptmstr's logic between
  those calls is caught at pptmstr's edge (`_refusal_said`, the formula
  indexing of the copy). The copy's fallback to `selected_text()` runs only
  after pptmstr's own defect; after a library failure the failure propagates
  as one and the copy is empty [ran, fence-fix-2: a raising `Paragraph` and a
  raising `range_text` each stop the library, and each test fails against the
  code before this change].

- **pptmstr mutates imscribe's private shared parser.** `gate._reader`
  disables rules and pushes its own onto `markdown_rules._parser(False, False)`
  (`gate.py:550-600`, the parser at `:586`) [read]. imscribe's `ReaderOptions`
  docstring warns that changing the shared object "changes the reading of
  everything else in its process" [relayed, builder]. A figure fence does not
  touch the parser. Any future image route would, so it inherits this hazard.
  Moving pptmstr onto `ReaderOptions` is its own task and is not this work's.
- **Text below a growing host jumps** (imtypeset C2, §3.6). An `svg` figure
  moves the text below once, at commit; a Mermaid figure once more, when its
  first render in the process finishes. The content cache stops a re-hosted
  figure jumping again. Not removed.
- **Figures are invisible to find**, and to selection they are one atom
  [read].
- **The untracked dotfiles at the worktree root** (`.bashrc`, `.profile`,
  `.claude/` and others) are sandbox masks, not work. Every builder reported
  them. They are not this record's subject, and a gate that runs
  `git status --porcelain` will see them [relayed].

## 10. What the first real SVG session taught

Board task `fence-lessons`, 2026-10-21. The source is operator session
`7b792e36`, the CLI transcript
`~/.claude/projects/-home-wreel--pptmstr-worktrees-pptmstr-feat-vector-rendering/7b792e36-238f-4764-b541-b4feae743401.jsonl`.
It ran on Opus 5 through pptmstr's gate, before the fence prompt existed
(§8 slice 4 was not yet built). "L*n*" below is a 1-based line of that file
(`sed -n '<n>p'`). Everything in this section was read from the transcript by
the writer of this section, unless it is marked [ran] or [relayed].

### 10.1 What it was asked, and what it did

**Asked** (L3), paraphrased: as an exercise, find a pptmstr board declared in
an earlier session and display its dependency graph as SVG.

**What it did, in order.** 28 tool calls over about 8½ minutes, $1.95 (L237).
- *The data* took the first 11 calls. It found the board logs under
  `~/.config/pptmstr/sessions/*/board`, picked `399eb899` (`feat-graph-tooling`,
  21 tasks, 42 `depends_on` edges) and rebuilt its tasks (L99).
- *The dataviz skill.* Call 12 loaded the bundled `dataviz` skill (L100). The
  skill's body (L103) is a procedure for HTML/SVG charts: pick the form, assign
  colour by job, run `node scripts/validate_palette.js`, add a hover layer "by
  default", and build components "in plain HTML". Call 13 read its
  `references/palette.md` (L112), which states the palette as CSS custom
  properties (`--surface-1`, `--series-1`, …, light and dark).
- *The rasteriser hunt*, calls 14–16. It looked for `rsvg-convert`, `inkscape`,
  `chromium`, `google-chrome` and ImageMagick (L118), then `cairosvg` and
  ImageMagick's SVG delegates (L127), then `playwright`, `firefox`,
  `wkhtmltoimage`, `cutycapt`, Pillow/matplotlib/graphviz and `dot` (L135).
  The operator refused that call at the gate, pointing it at the ```` ```svg ````
  fence (L136).
- *The hand-built layout engine*, calls 17–28:
  - a layering script (L144);
  - six calls to get the dataviz skill's Node palette validator running from
    `/tmp` (L153–L183). It passed three hues, with a contrast WARN;
  - an edge-span census (L189);
  - a 160-line generator with lane routing for long edges. It wrote a 21,083-
    character SVG to `shots/board-graph-tooling.svg`, 730×756 (L195);
  - an ImageMagick `convert` of that file to PNG, also refused (L204–L205);
  - a geometry checker over its own layout, which reported "GEOMETRY
    PROBLEMS: 30", mostly lanes crossing boxes (L211–L212);
  - a second, simpler generator with barycentric ordering and straight cubic
    edges (L221), whose 10,802-character, 744×622 output became the reply.
- *The reply* carried the ```` ```svg ```` fence, two observations about the
  graph, and a pointer to the file in `shots/` (L232).

**The pull of the dataviz skill, as the transcript shows it.** The session
invoked the skill on its own, before deciding anything about the output, and
then spent six calls satisfying its "run the script, don't reason about
Delta E" step. The final SVG used the skill's *values* as literal hex (surface
`#fcfcfb`, ink `#0b0b0b`, series `#2a78d6`/`#eb6834`/`#1baf7a`), not its CSS
custom properties. **This session did not write `var()`**: neither generator
contains it, and neither does the reply [ran: a search of L195, L221 and
L232]. The `var()` hazard below is what the skill's convention *would* produce.
It was measured separately and was not observed here.

**What it never considered.** Mermaid is not mentioned anywhere in the
session. merman was installed (slice 2, `0e219b2`). Nothing told the session
that a ```` ```mermaid ```` fence renders, so the layout a 21-node dependency
graph needed was hand-computed twice.

### 10.2 The lead's measurement: `var()` renders black

[relayed from the `fence-var` specification]: a `<style>` declaring
`.viz{--c:#e8590c}` with `.a{fill:var(--c)}` passed `sanitise()` unchanged,
and resvg 0.48 painted the rect `(0,0,0,255)`. A literal `#e8590c` beside it
rendered correctly. This is a silent wrong rendering, not a refusal.

### 10.3 Its final SVG, through the real pipeline

[ran, 2026-10-21, this section's writer]. The reply's fence body (L232) was put
through `sanitise.sanitise` and `render.render_png` with `imscribe_fonts()`:
- `Sanitised`, 744×622, `notes` empty. The only change is serialisation
  (attribute order, `<rect … />`), 10,802 → 10,838 characters. Sanitise
  3.5 ms.
- resvg: 62 ms at 744 px and 49 ms at 600 px (best of 5). The PNG at 600 px
  is 46,429 bytes.
- **It renders clean, and its layout is still wrong.** Sampling each of the 26
  cubic edges at 99 interior points against the 24 positioned `<rect>`s
  (21 nodes and 3 legend swatches, each inset 1 px) finds 9 edge-over-box
  crossings on 6 edges. Looking at
  the 600 px PNG confirms it: edges from `withdraw-task` and `all-writers` run
  through `review-all-writers`, `review-bus-tools` and `withdraw-fixes` on
  their way to `record`. The gate passed it because nothing in the gate judges
  layout.
- At 744 px it is wider than `fences.FIGURE_WIDTH` (600), so in the SESSION
  pane at the smallest window it would be scaled to about 80 %, with its
  11 px subtitle at about 9 px (from `fence-guidance`'s width reasoning, not
  measured in the app).

### 10.4 What changed because of it

Both landed in `fae81fe`.
- **`fence-guidance`** (builder-28) [relayed from its result; the text
  re-printed by this section's writer, 1,968 characters, every renderer
  installed]. `fences._guidance` adds four lines, built from the same table:
  - "Write the fence; the pane is the display. Do not render to a file, look
    for a converter, or preview with other tools." This answers the
    rasteriser hunt. guide-fix scoped it to "Unless asked for an export, do
    not render to a file, …", because an operator who asks for a PNG or a
    file should not be contradicted by the system text.
  - Only when both the mermaid and svg rows are present: "For nodes and edges
    (dependencies, flows, sequences, states, entities) use ```mermaid, which
    lays them out; ```svg is for what Mermaid cannot draw. Never compute a
    graph layout by hand." This answers the layout engine. Without merman the
    text does not say "Mermaid" at all.
  - When a figure row is present: "Figures sit on a near-white card, scaled
    down to fit the pane: design for white, with literal colours, text of
    11 px or more, and at most about 600 px wide." The 600, not the 720 the
    task suggested, is `FIGURE_WIDTH`. SESSION in FOCUS is 0.585 of the
    window: 599 at the 1024 minimum, so 600 is never scaled there. **For
    Mermaid output the 600 is unmeasured**: the session does not choose
    merman's width, and nobody has rendered a 21-node dependency graph as
    Mermaid to see its natural width, or the text size once it is scaled to
    600. One such render answers it.
  - "These rules win over a skill's general advice." This answers the pull of
    dataviz.
  - Headroom under `PROMPT_LIMIT` (2,000) was 32 characters; after guide-fix's
    export clause it is 4 (1,996 with every row) [ran].
- **`fence-var`** (builder-29) [relayed; the measured case re-run by this
  section's writer]. Sanitiser step 9 resolves `--name` declarations
  document-wide, last wins, and substitutes `var()` everywhere. An unresolved
  one becomes `none` in fill/stroke and is dropped elsewhere. Resolved values
  are re-checked against the url()/escape/@import rules. It is bounded by
  `MAX_VAR_DEPTH` 8, a byte budget and `MAX_VAR_VALUES` 5,000 (ratified by the
  lead on its 1.1 s measurement). Failures are lines in a new
  `Sanitised.notes`. Re-run [ran]: `.a{fill:var(--c)}` paints
  `(232,89,12,255)`, `fill="var(--gone)"` paints nothing, and `notes` reads
  `fill: var(--gone) is not declared; painted none`. At the lead's direction,
  step 3's unsafe-`url()` paint drop now applies step 8's rule, so it no
  longer paints black either.
- **Not changed, and not tested:** whether the new text stops a session from
  rasterising or hand-laying. No session has run with it (`fence-guidance`'s
  not-run list). The next real drawing session is that test.

### 10.5 Skills shipped with the application: decided and built

**The operator's requirement, 2026-10-21:** pptmstr's own skills and plugins
ship as part of the application, need no preparation in any project, and
coexist with the user's and the project's existing configuration and skills
directories.

**The lead's reading** [relayed]: claude-agent-sdk 0.2.168's
`ClaudeAgentOptions` has `plugins=[{"type": "local", "path": …}]` and
`skills=`, and pptmstr passes neither. The idea: move the fence guidance's
bulk (the TeX use/avoid list, the SVG design rules, when to choose Mermaid)
into a packaged skill that loads when a session draws, and keep a short
always-on floor.

**Probe `skill-probe` has run** (builder-30; `notes/vector-rendering/probe-skills.md`;
two live runs by the operator, `cmd-20261021T171105Z-ba3cb4bb3e64` and
`cmd-20261021T171530Z-e3ca78309651`) [relayed from its note and result]:
- `plugins=` lists the skill as `pptmstr:<name>`, and haiku invoked it on
  drawing prompts (n=2). The user's synced skills and the bundled ones still
  load beside it when `setting_sources` is left at its default.
- `skills=[…]` *restricts* the model's listing, and any value of it makes the
  SDK pass `--setting-sources=user,project`, which silently drops
  `.claude/settings.local.json`. That breaks the second half of the
  requirement, so `skills` stays unset.
- A same-named project skill also loads, and in one run the model chose it
  over pptmstr's. The floor must name the namespaced skill.
- The wheel and the sdist both carry `pptmstr/plugins/pptmstr/` (including
  `.claude-plugin/`) with no configuration change, and `importlib.resources`
  resolves an absolute path a session loads from. The path must be absolute.
- **`approval.classify` parks `Skill` on every rung**, so a shipped skill is
  refused headless and under `AUTONOMOUS` until the gate admits it.
- `AgentDefinition.skills` preloads the body into a worker. A worker with a
  tool list and no `Skill` gets nothing. A worker that inherits its tools can
  call it, and the hook attributes the call.
- **The recommendation** is to ship `pptmstr/plugins/pptmstr/`, pass it via
  `plugins=`, leave `skills` and `setting_sources` unset, admit
  `pptmstr:`-prefixed `Skill` calls in `classify`, and keep a floor naming
  `pptmstr:figures`. The stub's always-on cost is about 43 tokens, against
  today's 1,968 characters.

**Why the floor keeps two rules.** The packaged skill and the floor answer
different failures from 10.1. The floor has to carry what must never be
missed: "the pane is the display" and "prefer Mermaid for graphs". Those two
cost this session 17 tool calls, and a skill the model does not choose to load
cannot prevent them. The design rules and the TeX list could move, and did.

**Decided (the operator, 2026-10-21):** admit only `pptmstr:`-prefixed
`Skill` calls, on every policy.

**Built** (task `skill-ship`, builder-37) [ran: `make check`, 14,059 passed]:
- **The plugin.** It is `pptmstr/plugins/pptmstr/`: a manifest named
  `pptmstr` and one skill, `skills/pptmstr-figures/SKILL.md`, listed as
  `pptmstr:pptmstr-figures`. It holds skills and nothing else, and
  `tests/test_skill_ship.py` holds it to that: no MCP servers, hooks, LSP
  servers, monitors, commands, agents or `bin/`. `plugins.root()` resolves
  it through `importlib.resources.as_file` to an absolute path.
- **One table, three readers.** `fences.skill_text()` builds SKILL.md from
  `fences.RENDERED`. It carries each row's rules (the SVG rules, the Mermaid
  kinds, the TeX use and refuse lists), the Mermaid-over-SVG choice, the card
  advice, and "the pane is the display" with its export exception.
- **SKILL.md is checked into the tree, and a test holds it equal to
  `skill_text()`.** It is not generated at load or at build. An installed
  package directory may not be writable, and generating it elsewhere would
  make the loaded path differ from the shipped one. Generating it at build
  would need a hatch hook. A checked-in file also shows up in a diff when the
  table changes. The test's docstring names the one-line refresh.
- **The floor** (`fences.prompt`) is 903 characters with every row, down
  from 1,996. `PROMPT_LIMIT` is now 1,000, which leaves 97 of headroom. The
  floor names:
  - the fences that render;
  - that no other fence is drawn;
  - that a fence that fails shows its source;
  - that `$` in prose is not mathematics;
  - the pane is the display, with the export exception;
  - Mermaid for nodes and edges, when both figure rows are installed;
  - "Before drawing a diagram, chart or formula, load the
    pptmstr:pptmstr-figures skill". The rules win over a skill's general
    advice.

  It still leaves out a row whose renderer is missing.
- **The gate.** `classify` admits `Skill` on every rung when
  `tool_input["skill"]` (where the probe's live runs recorded it) equals a
  name in `plugins.SKILLS`. A test holds that set to the plugin's `skills/`
  directory and to `fences.SKILL`. The comparison is equality, not prefix:
  `pptmstr:../x`, `pptmstr: x`, `/pptmstr:…`, a padded name, a list and a
  missing key all park, as every other skill does. All are tested on all
  three rungs.
- **The driver.** `_options` passes `plugins=[plugins.config()]` on every
  launch: fresh, resumed, solo, team, contained, and in another cwd.
  `skills` and `setting_sources` stay `None`, and the argv carries
  `--plugin-dir` with no `--setting-sources` or `--allowedTools`.
- **Workers with a tool list (the read-only reviewer roles) are not given
  `Skill`, and their floor leaves out the skill sentence.**
  - Adding `Skill` is safe at the gate, which admits only the shipped names.
    But it would let those roles load a user's or a project's skill on one
    approval, and that skill's `` !`cmd` `` lines run as it loads.
  - The SDK calls `Skill` in `tools` deprecated, and that form is unrun.
  - `AgentDefinition.skills` would preload the whole body, which is the cost
    the skill exists to avoid.
  - These roles review; they do not draw. A worker that inherits its tools
    loads the skill on demand (probe L7).
- 10.6's "4 characters of headroom" is now 97, and its PD4 line about
  `fences._guidance` now applies to `fences._DISPLAY`, which both the floor
  and the skill use.

**Not run, or open:**
- **The live check through pptmstr's own driver passed** [op-run,
  `cmd-20261021T204608Z-3e9b7a20a69e`, 2026-10-21, 7.6 s].
  `scripts/verify_skill_ship_live.py` launched one haiku `AgentSession` with
  the real options (`plugins=` the absolute package path, `skills` and
  `setting_sources` unset) and a drawing prompt. The session called
  `Skill(skill="pptmstr:pptmstr-figures")` before drawing, the gate's
  disposition was `auto_approve` on STRICT, nothing parked, and the reply was a
  ```` ```mermaid ```` flowchart (`shots/skill-ship-live/result.json`). One
  run, one model: it shows the path works, not how reliably a model loads the
  skill.
- **Unrun, from the probe's not-run list:** any model but haiku; whether the
  `PreToolUse` hook sees a skill's `` !`cmd` `` lines; whether
  `strict_mcp_config` keeps out a plugin's own MCP servers. The shipped plugin
  has none, and the test holds it there.
- **Behaviour risk, accepted with the design.** The rules reach a session only
  if it loads the skill (n=2 on haiku in the probe). The floor carries the two
  rules whose miss cost the most.

### 10.6 Scope, not built: a figure-preview bus tool

**The idea.** A bus tool, say `preview_figure(fence)`, takes the fence's
language and body. It runs the real sanitiser and the render child, and
returns the PNG plus a list of what the sanitiser stripped or refused. A
session can then see its figure through the gate before the operator does.
In 7b792e36 that would have answered "does it render" without a rasteriser
hunt, and shown the edge crossings the session's own geometry checker went
looking for.

It is in tension with the fence text as built: "do not … preview with other
tools". If the tool is built, that line has to name it as the exception, and
`PROMPT_LIMIT` has 4 characters of headroom (10.4), so something else in the
text has to give.

Answers from the code, each marked:

**Can a bus tool return an image the model sees?** It can, up to the CLI.
- The bus is an in-process SDK server (`bus.build_server`,
  `create_sdk_mcp_server`). Today every handler returns `bus._text`.
- The SDK's `_convert_tool_content` passes
  `{"type": "image", "data": <base64>, "mimeType": "image/png"}` through
  unchanged [read, `claude_agent_sdk/__init__.py`].
- Whether CLI 2.1.301 forwards an MCP image block to the model as an image is
  **not verified**. That is probe **PV1** below.
- The model can judge such an image. The writer of this section read the
  600 px PNG of 10.3 back as an image and saw the crossings (n=1, through
  `Read`, not through MCP).

**Which approval rung?** As things stand, none admits it.
`mcp__pptmstr__preview_figure` is in none of `_AUTO`, `_BUS_AUTO` or
`_REVIEW`, so `classify` reaches the fail-closed arm and it parks on every
policy [read, `approval.classify`]. Of the existing sets, it fits `_AUTO`'s
stated test best: "Cheap, reversible, and they do not leave the box". It
writes nothing and reaches no network: the sanitiser refuses `<image>` and
every non-fragment `url()`, and the child is resource-limited. Its one effect
outside the model's context is CPU in the render child. **But an svg fence is
not sanitised in the child**: `host._svg_mark` calls `sanitise()` in the GUI
process, on the frame thread, which has no `RLIMIT_AS`. An auto-approved tool
a session can call in a loop is only as safe there as `sanitise()` is bounded
in time and memory on every input up to its byte cap. Until guide-fix, a
100 KB custom property used 60,000 times in one `<style>` reached 4 GiB and
`MemoryError` in 2.4 s [ran, guide-fix]; with every substitution charged to
the output it is refused at 31 MB peak RSS in 0.08 s [ran, guide-fix]. `_BUS_AUTO`'s
rationale ("coordination that reads or reserves") does not describe it.
**Operator decision:** auto-approve on every rung, in `_AUTO` or a set of its
own with that reason. The alternative is parking it, which turns every
preview into an operator click and defeats the point.

**Cost.**
- *Tokens.* An image costs ⌈w/28⌉×⌈h/28⌉ visual tokens (Anthropic vision docs,
  "Resolution and token cost", read 2026-10-21). The 10.3 figure is 621 tokens
  at 744×622 and 396 at 600×502 [computed]. Rendering previews at
  `FIGURE_WIDTH` caps a typical figure at about 400–600 tokens. The image
  stays in the history and is re-sent, cached, on every later turn. Above 20
  images in one request the API applies a stricter per-image limit (2000 px
  per side), which 600-wide previews stay under.
- *Render time.* 49–62 ms for the 10.3 figure [ran]. M1's merman diagrams were
  70–282 ms at 800 px and the worst legitimate case is about 2 s
  (`render.RENDER_TIMEOUT_S` docstring) [read]. The ceiling is the 10 s
  timeout. A child cold start is about a second (`START_TIMEOUT_S` docstring).
- *Bytes.* The base64 PNG is about 62 KB at 600 px [ran].

**Can it share the render child without affecting the frame loop?** Not as
`Renderer` is written [read, `render.py`]:
- `Renderer` is frame-thread state: `_pending`, `_inflight` and `_child`,
  with no lock. "The frame thread calls `submit` and `poll` only".
  `host.pump` is its one poller.
- Bus handlers run on the `pptmstr-asyncio` thread (`bridge.py`). Calling
  `render.renderer().submit` from there would race the frame thread.
- The child renders one request at a time. A preview queued on it waits
  behind the visible figures, and they wait behind it. A hostile or slow
  preview holds every figure in the pane for up to `RENDER_TIMEOUT_S`.
- The child returns RGBA (`rasterise`), not PNG. A preview would re-encode
  it, or need a third request kind beside `_RASTER` and `_MERMAID`.

Two designs, and which one is a decision:
- (a) **A second `Renderer` owned by the bus side**, polled from the asyncio
  loop, with its own child. It is fully isolated from the frame loop, so a
  bad preview cannot stall a visible figure. It costs a second process, a
  second ~1 s cold start, and a second `RLIMIT_AS` budget (1 GiB address
  space on Linux; unbounded on macOS, where only the timeout holds).
- (b) **Route through the frame thread**: the handler emits an intent, `pump`
  submits, and a future is resolved with the result. It needs one child, but
  it couples previews to the visible figures' queue and needs a bridge
  path that does not exist.
- The writer recommends (a). It keeps `render.py`'s one-poller invariant
  intact, and the frame loop is the property that must not regress.
  **Operator decision**, on the cost of the second process.

**What "what the sanitiser stripped" can say today.** Less than the idea
needs [read, `sanitise.py`]. `Sanitised.notes` carries only step 9
(unresolved `var()`) and step 3's repainted unsafe paints. The sanitiser's
docstring already names this tool as their consumer. Elements and attributes
dropped by steps 2–5 (`<script>`, `on*`, `<image>`, `<pattern>`, filters,
external `url()`) leave no note. A preview that shows a picture with something
silently missing and an empty list is the failure 10.2 was. So the tool
needs the sanitiser to record every drop, capped as `notes` is. That is a
change to a pure function with a timing bound, and it needs its own task. The
Mermaid route also drops notes: `render._serve` rebuilds `Laid` as
`Sanitised(svg, w, h)`.

**Can it be abused?**
- *Reach.* It reaches no further than a fence already does. The same
  sanitiser, the same child, the same caps, timeout and memory limit. A
  hostile SVG that would hang the pane hangs the preview's child instead,
  under design (a).
- *Volume.* Each call is up to 10 s of a CPU and about 400–600 tokens of the
  caller's own context. Nothing stops a loop of previews except the model's
  budget. **Probe or decision:** a per-session rate or count bound. The bus
  store's caps on open decisions and commands are the precedent.
- *Content.* The image is a picture of text the session itself wrote, so it
  is no new injection channel. A figure can show text, so a preview cannot
  smuggle in anything a fence could not already show the operator.
- *Workers.* Every session's bus carries it (`driver._mcp_servers`), so
  workers could call it. Whether reviewer roles should have it is a role
  decision.

**How the operator sees that it was used.** Today, badly [read]:
- The call appears as a tool line with its input, the whole fence
  (`transcript.append_tool_call`).
- The result goes through `transcript.cap_output`, which `repr`s non-string
  content. An image result would be drawn as a list of dicts holding about
  62 KB of base64, under the 1 MiB `OUTPUT_CAP`, so not cut.
- The tool needs the transcript to recognise an image block and show a one-
  line summary ("preview 600×502, 2 notes"), or the figure itself through the
  existing host. That is also how an operator would see what the model saw.
- The gate's hook sees every call by name, so a count is available if a
  status line wants one.

**Open, named; nothing is declared.**

| id | kind | question | decides |
|---|---|---|---|
| PV1 | probe | Does CLI 2.1.301 hand an SDK-server image block to the model as an image? One live call returns a known PNG (e.g. a coloured square with a word), and the model is asked what it shows. Then the `ToolResultBlock.content` shape pptmstr receives is recorded. | whether the tool is possible at all; what the transcript must special-case |
| PV2 | probe | Does a session with the tool, asked for a dependency graph, use it, and does it then fix crossings or swap to Mermaid? A few runs with and without it, opus and haiku. | whether the tool is worth its tokens next to the 10.4 text |
| PV3 | task, once PV1 passes | The sanitiser records every drop as a note, capped, inside the timing bound; the Mermaid route keeps notes across the child. | whether "what was stripped" is truthful |
| PD1 | operator decision | The rung: auto-approve on every rung (recommended, `_AUTO`'s test) or park. **Conditional:** the recommendation holds only while `sanitise()`, which runs in the GUI process, is bounded in time and memory for every input up to the byte cap (guide-fix item 1, now fixed and tested), or once the tool sanitises in its own child. A later sanitiser change that loses that bound withdraws the recommendation. | `approval` sets |
| PD2 | operator decision | A second render child for previews (recommended) or routing through the frame thread. | process count, memory budget |
| PD3 | operator decision | A per-session bound on preview calls, and which roles are offered the tool. | abuse bound |
| PD4 | lead decision | The fence text's "preview with other tools" line, and the 4-character headroom, if the tool lands. Ties to 10.5's floor/skill split. | `fences._guidance` |

---

## 11. Mermaid drawn natively, in the theme's colours and the author's

**Decided by the operator, 2026-10-22** [relayed from the board's
`displaylist-proto` and `native-mermaid` specifications]:

- Mermaid is drawn natively in the pane from merman's layout, with no
  rasterising, so that a diagram is crisp at every width, follows the theme and
  can be hit-tested. The raster stack (the render child's raster, resvg,
  Pillow, numpy, the texture cache and the light card) may go once this is
  proven.
- **Author colours are honoured.** The colours a diagram's own `style`,
  `classDef` and `linkStyle` lines set are drawn. Everything else takes the
  theme's roles, so a diagram that sets no colours follows the theme entirely.

**Why, as the record reads it.** The probe (`notes/vector-rendering/probe-displaylist.md`)
showed the five families and the 21-node graph drawn natively with the same
structure as the raster, sharp on every resize frame, free on a theme switch,
and hit-testable. The light card it replaces was the cost §3.6 named: a bright
rectangle in the dark theme. Honouring the author's colours keeps what
`mermaid.py` already held, that those lines are content and not renderer
configuration (directives and front-matter `config` stay stripped); drawing
only roles would have dropped them silently. merman's own pastel palette is not
the author's and is not drawn.

**What was built** [ran: tests and a real window, below]:

- `pptmstr/figures/displaylist.py`: the schema (frozen dataclasses), every
  bound from the probe note's table, the SVG parser, the author-colour reader,
  the legibility functions and the pure geometry helpers. No ImGui.
- `render._laid` runs merman, the post-pass, `sanitise()` and then
  `displaylist.from_mermaid` in the render child, and answers the display list
  (its natural size inside it), not SVG or pixels. The I/O thread re-checks
  every bound and type (`render.received`, `displaylist.validate`) before the
  frame thread sees it; a list out of bounds is a kept failure, shown as the
  fence's source under its reason. Mermaid no longer depends on the child
  having loaded resvg. *(native-fix: the list crosses as plain JSON and is read
  without unpickling, §11.1.)*
- `host.FigureWidget` draws the list in the existing `Hosted` block: polylines
  and convex or concave fills on the window's draw list, labels in the pane's
  prose face (`typeset.imfont`), culled against the clip. Label sizes are set
  for the face's em: the child reads the prose face's hhea metrics with
  fontTools (`render.em_ratio`, 0.853 for Roboto, the number imscribe gives).
- **Height.** The placeholder-then-height rule and the held layouts are kept;
  the height is the list's natural aspect at the drawn width, with no card
  padding. A re-hosted figure answers its height at once.
- **Hover and click.** The node under the pointer (smallest box wins; native-fix:
  its shapes' boxes, not its whole extent, §11.1) has its
  outline and every edge that ends on it drawn in the focus colour. A click
  calls `host.node_clicked(sha, node_id)`, which only logs. **What a click
  does is a later operator decision.** Hover is tested without an ImGui item,
  so the pane's own text selection still gets the drag.
- **Resize.** Geometry is built once per drawn scale. When the width changes,
  at most one figure a frame rebuilds, the one that has waited longest; the
  others draw their last geometry scaled to the new width (a vertex pass,
  still vectors). A figure drawn for the first time (scrolled into view) is
  built that frame and takes the frame's turn, because it has nothing to stand
  in for it. *(Superseded by native-fix: a first build waits its turn too, §11.1.)*
- **The raster path stays in the tree, unused for Mermaid** (`host.RasterFigure`,
  `_Textures`, `render.submit`, `rasterise`): a separate task removes it after
  review. The fence table's `needs` for mermaid still names the raster
  distributions; that goes with it. *(Removed, both, §11.2.)*

**How the author's colours are told from merman's.** By the source, not the
SVG. merman writes its own palette into the same markup, inline as well as in
its stylesheet, and an author's `style` comes out as an inline declaration,
sometimes with `!important` and sometimes without (class diagrams). So a
colour is the author's only where both hold: an inline `style` declaration
sets it on a node's, cluster's or edge's shape, or on a label's text, and the
source's `style`/`classDef`/`linkStyle` lines name that colour for that
property (`fill`, `stroke`, or `color` for text). *(Superseded by native-fix:
the statement must name the shape, §11.1.)* An arrowhead takes its
edge's colour. Tested on synthetic SVG and on merman 0.9.1's own output: no
style lines leaves every item on roles, in all five families; `STYLED` puts
colours on exactly the two nodes, the edge, its marker and the two labels it
names (`tests/test_figure_displaylist.py`).

**The legibility guard.** Against the colour actually behind the figure (the
window's, or a child window's own background):

- An author's text colour, or the theme's text over an author's fill, under
  **4.5:1** against its ground (the author's fill, else the pane; native-fix:
  else the theme's fill of the node it sits in, §11.1) is moved to
  the nearest colour that reaches it: mixed toward white or black, whichever
  needs less, by the least amount. 4.5:1 is WCAG's AA minimum for normal-size
  text. The 3:1 WCAG allows "large" text was not chosen, because diagram labels
  are set at 12 to 16 px and are often scaled down to fit, well under WCAG's
  large size. 4.5:1 is always reachable, since black or white reaches 4.58:1
  against any colour.
- An author's stroke (and an arrowhead, which is its line's colour) under
  **3:1** against the pane is moved the same way. 3:1 is WCAG 2.1's minimum
  for graphical objects that carry meaning (1.4.11).
- **A fill is never changed.** Each adjustment is logged once per figure and
  theme.

**The probe's two defects are fixed:**

- **Badges.** A sequence-number badge rides on a zero-length line whose
  attribute says stroke width 0 and whose stylesheet (`#merman line`) says 2,
  and the marker is in stroke-width units. The parser now reads `stroke-width`
  from the stylesheet's simplest rules (root id plus one class or one element)
  to scale markers as resvg does, so the badge is 24 units across, not 12.
- **The cylinder** is one open path with no fill attribute, which merman's
  stylesheet fills. An open node path is now filled unless it says
  `fill="none"`. It is filled as two loops (top ellipse, then body), because as
  one polygon it touches itself.

**Pinned.** `tests/test_figure_displaylist.py` holds per-role primitive counts,
node ids, edge ends and label texts for the five families and the 21-node
graph, from merman 0.9.1, and fails under any other version with a message to
re-baseline after a window check. The numbers came from this parser and only
detect change. The screenshots below are what said the drawing is right.

**Real-window check, 2026-10-22** [ran]:

- **How it ran:** Xvfb, 1600x1400, llvmpipe, through the production path
  (`typeset.markdown`, the views' dispatcher, `figures.host`, the real render
  child). The driver is `shots/native-mermaid/check.py`, git-ignored as
  `shots/fence-backdrop/check.py` was. 1 922 frames, exit 0, no ImGui
  assertion in the log.
- **Shots, all read:** `shots/native-mermaid/{flowchart,sequence,class,state,er,board21}-{dark,light}.png`,
  `clash-{dark,light}.png`, `hover-board21-{dark,light}.png`,
  `resize-mid-drag.png` and `resize-300.png`.
- **Families:** every family and the 21-node graph drew in both themes with no
  failure. Badges are at the raster's size, the cylinder is filled, and edge
  labels' knockouts match the pane.
- **Authored colours:** a diagram whose classDef, style and linkStyle are dark
  on the dark theme had its two strokes and two labels moved, and the log said
  so. In the light theme the yellow linkStyle edge and its arrowhead were moved
  to olive. Authored fills were drawn as written in both themes.
- **Hover:** the graph's node of highest degree (`flowchart-t3-3`, 9 edges)
  lit with its edges. A click reported that id.
- **Resize:** five families dragged 900 to 300 to 900 px over 30 frames each.
  At most one rebuild a frame. Two frames carried a rebuild plus a first build
  of a figure entering view. No frame drew a figure without geometry.
  Figure time per frame had a median of 3.2 to 3.4 ms. The worst frame was one
  29.6 ms rebuild (not explained; glyph baking for a new size is the likeliest
  cause, unconfirmed; native-fix measured the garbage collector instead, §11.1).
  A theme switch rebuilt nothing.

**Not run, and open:**

- Density 2, a real GPU, and macOS.
- A fence growing token by token (every fence here was committed whole).
- Cold start of the child, separated from warm.
- What a click on a node should do: operator decision.
- Removing the raster path, and the mermaid row's `needs`: a separate task.
  `render.em_ratio` reads font metrics with fontTools, which the raster path
  also loads. Removing fontTools with it would need another way to read the
  prose face's em. *(Done, fontTools kept, §11.2.)*
- The per-frame draw loop and the rebuild remain Python, as the probe measured
  them. C++ for them is still the survey's plan.

### 11.1 The native-Mermaid review, and what changed (task `native-fix`, 2026-10-22)

The review (task `native-review`) raised sixteen findings, from reading only. Each
was checked against the tree and against merman 0.9.1's real output, and measured
where it needed it [ran: tests, `shots/native-fix/` drivers under Xvfb, all
git-ignored scratch]. Two lead decisions are folded in.

**The child's reply is plain data (lead decision, 2026-10-22).** The render child
parses untrusted text through merman (native code) and pptmstr's own parsers, so a
child that has been taken over is in the threat model, and unpickling its reply
would hand it the GUI process. Every reply is now `recv_bytes` of JSON, read to at
most `render.MAX_REPLY_BYTES` (a head line, then the display list a record a line,
`displaylist.encode`/`decode`). Decoding evaluates nothing: each field is taken
only as the one JSON type it may be, then `validate()` runs. A reply that is not
well formed is that figure's failure, never an exception; one the pipe cannot be
read past also replaces the child. Requests go to the child as JSON too, so
nothing on the pipe is pickled; only the child's start arguments are, by
`spawn`, from the trusted side. **Why a record a line and not one document:** a
list at every bound is 64 MB of JSON, and one `json.loads` holds the GIL for the
whole parse, 0.97 s here, the frame stalled throughout. Parsed a record at a time,
with a record over `MAX_LINE_BYTES` refused unparsed, the worst gap a 1 ms ticker
thread saw was 8-13 ms.

**Dispositions**, the reviewer's numbering:

1. *Sequence hover hit the participant's whole column.* Held, confirmed in a
   window before the fix (the note lit API Gateway and its messages; a message
   label lit Auth Service). Hover now tests a node's shapes' boxes
   (`displaylist.node_at`, `HitNode.attach`); its extent stays what edges attach
   to. After: neither lights anything; an actor still lights its participant.
2. *Undrawable text past `validate`.* Held. `validate` refuses a lone surrogate or
   any C0 control in a run, an id or a stat; the parser writes the controls an
   attribute's character reference can carry as spaces. A child's failure
   reason is one line, at most `render.MAX_REASON_CHARS`, made drawable.
3. *The pickle plan's gaps.* Held: `recv_bytes` has a maxlength; decoding was
   measured and is line-wise (above); JSON's `NaN` and `Infinity` are refused by
   `validate`'s finite checks, tested.
4. *The guard's surface.* (a) Did not hold on merman 0.9.1: `style CUSTOMER
   fill:#003` fills every attribute row too, so a label's ground is right;
   pinned by a test. (b) Held: an author's ink in a node the author did not fill
   is now measured against the theme's fill of that node's shape.
5. *Frame cost.* Held, and larger than read. Six 100-node flowcharts re-hosted
   built four figures in one frame, 287 ms; one such figure was 23,587 ops (each
   dash its own) and 9 ms a frame. Now a first build waits its turn (worst
   frame 42-68 ms), a stroke's dashes are one op culled once, a dash pattern is
   never cut finer than a 4 px period on screen, a dash that would pass its
   budget is refused from the line's length, and a figure's dashes are capped at
   `host.MAX_DASHES_DRAWN`: the figure is 598 ops and 1.2-1.7 ms a frame, and a
   drag's median frame went from 50 to 11-14 ms. **Still open:** a list at every
   bound, which only a taken-over child can send (merman refuses a flowchart past
   about 110 nodes), takes 3-4 s to build on the frame thread, nearly all of it
   flattening the 6.4 million points the bounds allow. Closing it means a
   drawn-point budget checked on receipt, or lower bounds: a decision about what
   diagrams may be refused, left to the lead.
6. *Thick strokes culled at the clip edge.* Held. The cull now widens the view by
   the widest stroke's reach plus the highlight, in screen pixels at any stand-in
   stretch.
7. *The golden misses renamed classes.* Held for dotted, thick, dashed and
   note-edge patterns: a pinned test now asserts each edge's dash and width.
   The silent skip does not hold as a defect: where merman is not installed a
   mermaid fence is code, so there is nothing for the golden to protect, and
   pytest counts the skip.
8. *Colours matched by value.* Held, and scoped (lead decision: if merman writes
   the names). merman 0.9.1 writes classDef names on a node's `g` (`node default
   hot`) in flowchart, class, state and ER, the source id in its id
   (`flowchart-A-0`, `classId-`, `state-`, `entity-`), a subgraph as its own id,
   and edges in the order `linkStyle` counts. A colour is the author's only where
   a statement naming that shape set it; `classDef ink color:#28253D` (merman's
   own label ink) now paints one label, not all of them.
9. *Alpha dropped.* Held. Alpha 0 is no paint; any other alpha under 1 is drawn
   in the theme's role, since how it looks depends on the pane behind it and the
   guard measures opaque colours. (merman does not apply `fill:rgba(...)` in a
   style statement at all: it splits declarations at its commas.)
10. *`parse_colour` raised on `1e999`.* Held; such a colour is no colour.
11. *Author styling missed.* `A-->B; style A ...` (merman applies it) and
    `background-color` read as `color`: held, fixed. A sequence `rect`/`box`
    colour: holds as described, and is consistent with the operator's decision
    (only `style`, `classDef`, `linkStyle` are the author's); merman paints them
    first, under the messages.
12. *Set order in `_Sheet.width`.* Held in mechanism (four families have elements
    with two ruled classes, equal widths today); the later rule now wins, as in
    CSS.
13. *Fill rule and holes.* Does not occur: no filled path in any family has two
    closed loops; the golden now fails if one does.
14. *`fences.RENDERED` needs the raster distributions for Mermaid.* Held; left
    to `raster-removal` (lead decision), whose specification does not name it.
15. *Memory figures undercount.* Held: a word of `weight` costs 34-43 bytes
    held, not 8 (tracemalloc), and a list at every bound weighs 3.3 million
    words, not 400 thousand. `displaylist.WORD_BYTES` is 40, pinned by a test, and
    the budget is 64 MiB as held. A 100-node figure's geometry is about 0.8 MiB.
16. *Stand-in text bakes a size a frame.* The mechanism is real and bounded:
    sizes are whole pixels, each baked once, 0.3 ms for 24 glyphs. The 29.6 ms
    rebuild is better explained by the cyclic garbage collector: gen-2 pauses of
    29-99 ms during a drag, set off by the builds' allocations. Tuning the
    collector is app-wide and not done here.

### 11.2 The raster path is removed (task `raster-removal`, 2026-10-22)

Native drawing made the raster path dead code, and it is gone. **Why now and not
kept as a fallback:** nothing hosted it after §11, it carried three exact-pinned
dependencies (resvg_py, Pillow, numpy) into every install, and the fence table
asked for all three before it would promise a mermaid fence, so a machine without
them lost a fence that never used them (native-review finding 14). A fallback
nothing exercises is a second renderer to keep correct by hand.

**Removed:**

- The render child rasterises nothing. `render.submit`, `raster_size`,
  `rasterise`, `render_png`, `resvg_options`, `family_name`, `Rendered`, the
  `["ok", w, h]` reply and its second message of RGBA, `MAX_RASTER_PIXELS`,
  `MAX_TEXTURE_SIDE` and `UPLOAD_MODULES`. A request is `["mermaid", source]` and
  the only answers are `["laid"]` with a display list or `["err", reason]`; a
  request of any other shape is refused and the child goes on (tested against
  the real `child_main`, which crashed on one before).
- In the host: `RasterFigure`, the card (`FIGURE_PAD`, `BACKDROP`), the texture
  cache (`_Textures`, `_Raster`, `TEXTURE_BUDGET_BYTES`), the parking of evicted
  textures between frames, `_upload`, `STABLE_FRAMES`, and the raster half of
  the retry bookkeeping: a layout's one retry is now kept by its sha alone.
- `app._before_exit` no longer releases figures: they hold no GL object. The
  render child is still ended in `_shut_down`.
- From `pyproject.toml`: `resvg_py`, `Pillow`, `numpy`. **fontTools stays:**
  `render.em_ratio` reads the prose face's em in the child, and imscribe needs
  it anyway. The mermaid row now needs `fonttools` and `merman` beyond the
  typeset library (`render.DISTRIBUTIONS`, `fences._FIGURE`). Without fontTools
  the child still lays a diagram out, at `DEFAULT_EM_RATIO`.

**Kept:**

- The render child, its timeout, its memory limit, its plain-JSON pipe. Its
  reasons were rewritten for what it now isolates: merman, which is native code,
  and the parse of its output. Measured here in spawned children (2026-10-22):
  layout, sanitise and parse took 56-446 ms for a 100-node chain, a 60-node
  120-edge graph, and the stress inputs merman or the size cap refuse, at 39-42
  MiB peak RSS. The 10 s timeout and the 1 GiB address-space limit stand; both
  are now far above any legitimate layout [ran].
- The sanitiser, every rule. Its caps and natural size still bound the parse and
  refuse the largest layouts (a 200-leaf fan at 2,340x16,976 and a 20,000-
  character label are refused there, measured above). Some rules guarded only
  what resvg painted: the `rebeccapurple` rewrite, the black-paint fallbacks of
  its steps 3, 8 and 9, the font-family fallback of step 7. The display list
  takes an SVG colour only where the diagram's source authored it
  (`displaylist._Builder._authored`) and sets every label in the pane's face, so
  those rules are inert for it, and harmless. They are kept, not removed: their
  removal changes a security module's output with nothing left to measure it
  against, for no gain. The sanitiser's docstring says so.
- Its tests that measured resvg's pixels now hold the sanitiser's output
  instead (the fill or stop colour it writes) or, for the kept-colour grammar,
  that every colour it keeps is one `displaylist.parse_colour` reads. The test
  that resvg paints a substituted `var()` is gone; the one beside it holds the
  substitution.
- `scripts/displaylist_proto.py`'s `run` mode compared native drawing with the
  raster widget and imported resvg_py and numpy through `scripts/figure_probe.py`.
  It is marked historical: it says so and exits, its code kept as the record of
  how §11's window numbers were taken. `selftest` and `dump` still run.
  `scripts/figure_probe.py` and `scripts/figure_render_probe.py` are the probes
  T1, M1, R1 and R2 this record cites; they import resvg_py, numpy and Pillow and
  are left as they are, historical, rerunnable only with those installed by hand.

### 11.3 What a list may cost to build, collector pauses, and `linkStyle` order (task `native-cost`, 2026-10-22)

**A drawn-point budget, checked on receipt.** A list at every earlier bound took 3-4
s to build on the frame thread (§11.1, finding 5). Nearly all of that was flattening
curves, and only a taken-over child could send such a list. `validate` now counts
`displaylist.drawn_points` on the I/O thread: one point for each move, line and
close, plus each curve's Wang step count. It counts at the largest scale the pane
draws a figure at, one pixel a unit, or more where the natural width is under a
unit. A list over `MAX_DRAWN_POINTS` = 32,768 is refused as "too costly to draw"
before the frame thread sees it. The count and the flattening share their step
functions, and a property test holds the count at or above what `flatten_units`
makes at every scale up to that one.

- **What merman produces** [ran, `shots/native-cost/merman_max.py`, merman 0.9.1].
  The table gives the largest diagram of each family's generated shape that merman
  and the sanitiser accept, found by bisection to within a few, with the
  diagram's drawn points and segments.

  | Diagram | Points | Segments | Refused by |
  |---|---|---|---|
  | 42-entity ER | 17,777 | 6,488 | sanitiser's element cap |
  | 16 wide entities | 13,252 | | |
  | 95-class | 10,592 | 3,472 | |
  | 170-state nested | 7,763 | 4,123 | |
  | 155-state | 7,170 | | |
  | 105-node flowchart | 6,268 | 3,550 | size cap |
  | 36-node dense flowchart | 4,548 | | merman's `max_layout_work_units` |
  | 125-message sequence | 1,296 | | size cap |

  Every one is refused one or a few sizes larger. The budget is 1.8 times the
  largest; segments peak at 6,488, and an edge's hit line at 145 points.
- **Build cost** [ran, `shots/native-cost/test_build_cost.py`, headless ImGui
  with the library's fonts]. About 0.9 us a point for a stroked curve and 1.8 us
  for a filled shape. The real diagrams above take 4-26 ms to build. The 105-node
  flowchart is the exception, at 59 ms, and its points are not why: 35 of its 83
  profiled ms is cutting its 52 dotted edges into dashes, which
  `host.MAX_DASHES_DRAWN` bounds separately.
- **Why not the 16 ms frame target.** The lead asked for a build under about
  16 ms. merman's own largest ER diagram takes 25 ms, and its largest flowchart
  59 ms. A budget that held the frame would refuse diagrams merman makes, which
  the task forbids. The build is already spread one figure a frame
  (`host._may_rebuild`). The budget bounds what a hostile list can cost.
- **What a list passing every receive check costs now** [ran,
  `shots/native-cost/test_at_bounds.py`]. The worst case pairs filled shapes at
  the point budget, dashed, with 5,000 text runs: 121 ms with the collector
  settled, 166 ms without. Stroked curves at the budget with 5,000 runs take
  about 60 ms. The stated budget is **under 200 ms for any list that passes the
  receive checks, on this machine**, down from 3-4 s. The figures here were
  taken at 5,000 runs; the text caps were lowered after (task `text-caps`,
  below).
- **The text caps** (task `text-caps`, 2026-10-22). The 309 runs and 2,745
  characters above come from the generators' short labels ("Node 5 label"),
  so they bound shape counts and not text. [ran, `shots/native-cost/text_max.py`,
  merman 0.9.1] Bisecting 15 label-heavy shapes found the maxima below, each
  refused one or a few sizes larger.

  | Diagram | Runs | Characters | Refused by |
  |---|---|---|---|
  | 1,472-slice pie, 60-character labels | 1,473 | 97,015 | coordinate bound |
  | 96-node flowchart, 200-character labels | 1,172 | 18,087 | size cap |
  | 78-event timeline | 816 | 12,447 | size cap |
  | 48-state diagram with descriptions | 685 | 11,815 | size cap |
  | 384-slice pie, 500-character labels | 385 | 194,083 | `MAX_TEXT_CHARS` |
  | 74-message sequence, `<br/>` lines | 378 | 22,834 | size cap |

  An ER diagram of four entities with 20 commented fields is already 4,100
  characters. So the cap the lead proposed (1,024 runs, 8,192 characters) would
  refuse ordinary diagrams, and it was not adopted.
  **Lowered:** `MAX_TEXT_RUNS` from 5,000 to 3,072, twice the pie.
  **Kept:** `MAX_TEXT_CHARS` at 200,000. Characters follow label length, which
  only the sanitiser's 1 MiB input cap bounds, and a long-label pie already meets
  this cap at 400 slices. Lowering it would refuse more diagrams merman makes.
  **What it saved** [ran, `shots/native-cost/test_text_cost.py`, the two caps
  interleaved six times in one process, collector frozen]. Text costs about 2 us
  a run and 0.04 us a character. The worst list builds in 113 ms instead of 117
  ms for filled shapes with dashes, and 54.5 ms instead of 59 ms for stroked
  curves. That is 3-8 %, not the half that was estimated. That estimate compared
  0 runs with 5,000 across separate runs, where noise between runs was ±10 ms
  (`test_at_bounds.py` at 3,072 runs ran 110-125 ms frozen and 133-153 ms not).
  The stated budget of under 200 ms is unchanged.
  **Not fixed, found on the way:** Mermaid's own documentation example of a
  `gantt` chart is refused as "a coordinate is out of range", and so is every
  generated gantt tried. No gantt source tried here draws.
- **Lowered:** `MAX_SEGS_TOTAL` from 100,000 to 16,384, which is 2.5 times the
  largest real 6,488. The wire bound falls from about 100 MB to about 90 MB.
- **Kept:** `MAX_HIT_POINTS` stays at 256. The longest real hit line is 145
  points, so 256 already gives 1.8x headroom. A lower cap would thin real
  edges' lines, which changes what merman's diagrams make, and would save only
  wire bytes: the pane does not read an edge's points.

**Collector pauses.** During a drag, every build allocates tens of thousands of
`ImVec2`. That sets off gen-2 collections, each of which walks every tracked
object in the process. Measured over a heap of 650,000 objects (a session's
transcripts, simulated) with two of the largest diagrams rebuilt across 600
frames [ran, `shots/native-cost/gc_drag.py`]:

| Policy | Gen-2 pauses during the drag |
|---|---|
| Default | 19, median 106 ms, worst 127 ms; worst frame 169 ms |
| `gc.freeze()` | 57, each under 1 ms; worst frame 52 ms |
| Raised thresholds (50,000/50/100) | none in the window |

Raised thresholds only postpone the full pause to a later frame, so they were not
adopted. **Adopted:** `app._settle_gc`, which runs unfreeze, collect, then freeze.
It runs once in `post_init`, after start-up. After that it runs from the frame loop
(`_keep_gc_settled`) whenever input has been quiet for `GC_QUIET_S` (3 s) and the
last settle is at least `GC_SETTLE_EVERY_S` (300 s) old. The full collection
therefore lands while nobody is touching the pane, and what a session builds up
after start-up is frozen too. Unfreezing first frees a frozen object that has
since become cyclic garbage, so memory does not grow across settlings.

**An hour of reply churn, simulated** [ran, `shots/native-cost/gc_hour.py`]. A
frame every 0.5 s for 3,600 s, a reply with a flowchart every 2 s kept in a
session list, the oldest views dropped, and input in bursts.

| Policy | Resident memory, start to end | Settles | Worst gen-2 pause in a drag after the hour |
|---|---|---|---|
| Default | 124 to 131 MB | | 60 ms |
| Freeze once | 124 to 131 MB | | 2.9 ms |
| The adopted policy | 124 to 130 MB | 11 | 2.5 ms |

Tracked objects stay at about 3,600 under the adopted policy. The growth in
memory is the same under every policy: it is the session list itself.

**Not run:**

- A real window. Every number here is headless ImGui with the library's fonts, or
  the scratch drivers above.
- A real multi-hour session.
- Whether a settle that lands just as the operator starts a drag shows; it costs
  one full collection, about 0.15 ms per thousand tracked objects.

**`linkStyle` on chained and `&` syntax.** I verified this against merman 0.9.1
and found it correct. No change was needed.

- merman counts `A-->B-->C` as A-B then B-C.
- It counts `A & B --> C & D` as A-C, A-D, B-C, B-D, each start with each end in
  turn, as Mermaid does.
- A mix of plain, chained and `&` statements counts in written order.
- The display list's edge order, and so `linkStyle N`, matches merman's own
  styled SVG in every case [ran, `shots/native-cost/linkstyle.py`].

These four cases are now a golden test
(`test_link_style_n_paints_the_nth_edge_mermaid_counts`).
