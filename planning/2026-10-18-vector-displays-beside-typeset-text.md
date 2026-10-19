# A figure in a reply is a hosted block beside the diff fences, drawn from SVG that pptmstr sanitised first

**Dated:** 2026-10-18 ·
**Status:** scope for work not started. Research only: nothing in `pptmstr/` or in
imscribe changed, and no figure has been drawn in a pptmstr window. ·
**Origin:** the operator, at launch, 2026-10-18 (§1) ·
**Evidence:** four research notes in `notes/vector-rendering/`
(`imscribe-seams.md`, `svg-renderers.md`, `mermaid.md`, `data-graphs.md`), the
results and notes of board tasks `seams`, `svg`, `mermaid`, `datagraphs`, four
operator-run probes named where they are used, the `review` task's findings, and
probe S1, run in pptmstr's venv during the revision (§3.6) ·
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
  github.com/Latias94/merman `docs/bindings/OPTIONS_JSON.md` on `main`] This
  was read on `main` and was **not run against 0.9.1** [unverified for the
  pinned version].

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
   path. M1's `runtime_catalog_json()` check is now a confirmation only.
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
- an ```` ```svg ```` fence, where the SVG is the input;
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
  [read].
- **Copy and find:** a copy gives `Hosted.text`. Set it to the fence verbatim,
  info string included, so a copied reply still renders elsewhere
  [judgement]. Find does not see a hosted figure [read]. That is accepted for
  now.
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
  `svg-renderers.md`]. The design below rasterises off the frame thread, so
  the 446 ms worst case costs latency, not frames.
- **foreignObject is not decisive either.** Both Mermaid routes below arrive
  as `<text>`, and the sanitiser rewrites any foreignObject that remains.
- **What would flip it:**
  - R1 shows real diagrams cost more than ~100 ms in resvg;
  - or the Mermaid route ends up being mmdr, which installs novasvg anyway
    (§2, item 2). Then one renderer is better than two, and novasvg replaces
    resvg behind the seam.

  [judgement]

**Mermaid: merman, called directly, with `svg.pipeline = "resvg-safe"`, as a
lazily imported optional extra.**
- merman is the engine either way: browserless, JS-free, tracks Mermaid 12.x,
  all 37 families admitted [relayed, `mermaid.md`]. Raw merman took 7.6 ms on
  one flowchart, and mmdr's merman backend 10.2 ms [op-run].
- The text-safe pipeline is documented as `resvg-safe` [read, `main`] and
  targets resvg/usvg consumers by name, which is the pairing above.
- **Its cost is the glibc floor.** merman's Linux wheel is
  `manylinux_2_35`, and pptmstr's floor is 2.28 [read]. On Linux with glibc
  2.28–2.34 the extra will not install, and a ```` ```mermaid ```` fence shows
  as code with a one-line reason. That is the same fallback as a render error.
  macOS arm64 is covered [read].
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
  local build close it. If probe M1 shows that merman 0.9.1's Python binding
  does not accept `resvg-safe`, use mmdr instead; whether novasvg then also
  replaces resvg behind the seam is R1's to decide.
- **Rejected for Mermaid:**
  - mmdc, which needs Node 22 and Chromium;
  - mermaid.ink and Kroki, which send the source off the machine;
  - mermaidx, at about 0.7 s per diagram even when warm [op-run].

  Costs are in §5.

### 3.4 Sanitisation: every SVG, every route

The input is model output, and an engine's output from model input is treated
the same way. The policy is the allow-list in `svg-renderers.md` §D, adopted
whole.

1. Reject any DOCTYPE. The check is case-insensitive and looks past the XML
   declaration and comments.
2. Allow these elements only:
   - structure: `svg g defs symbol use`;
   - shapes: `path rect circle ellipse line polyline polygon`;
   - text: `text tspan textPath`;
   - paint servers and masking: `linearGradient radialGradient stop clipPath mask pattern`;
   - also `marker style title desc`.

   `<foreignObject>` is **rewritten to centred `<text>`**, never passed through.
   `script`, `image`, `a`, `iframe`, animation elements and `feImage` are
   dropped.
3. Drop every `on*` attribute. `href` and `xlink:href` stay **only as `#id`**.
   **Any other attribute whose value contains `url(`** (`fill`, `stroke`,
   `clip-path`, `mask`, `marker-*`, `filter`, `style`) is dropped unless every
   `url(` in it is `url(#id)`. Steps 3 and 4 as `svg-renderers.md` §D wrote
   them covered only hrefs and CSS; presentation attributes are neither.
   Whether resvg would fetch through them is [unverified]; the rule does not
   depend on it.
4. In CSS (`<style>` and `style=`), **drop the whole declaration** (or the
   whole `<style>` element) that contains a backslash, `@import`, or a
   `url(` that is not `url(#id)`; do not strip substrings. A CSS escape makes
   `u\72l(` a url token under the CSS Syntax spec, so substring stripping can
   be evaded. (The review's second example, `ur/**/l(`, is not one: a comment
   splits it into two tokens. The rule covers it either way.) [unverified:
   recalled from CSS Syntax Level 3, not re-read; whether simplecss honours
   escapes is also unverified]
5. Cap the input bytes, the element count and the output pixels before
   rendering. **The element cap counts the tree after `<use>` expansion**:
   the sanitiser resolves each `<use>` to its target and sums the expanded
   size, or refuses a `<use>` whose target contains a `<use>`. A source-element
   cap alone does not bound nested `g`/`use` chains, which multiply.
6. Parse with stdlib `xml.etree`, after step 1, and re-serialise.
7. **Every `font-family` ends in a generic family.** The sanitiser appends
   `sans-serif` to a list that has none (`font-family="Arial"` is common in
   hand-written SVG). Under `skip_system_fonts`, a named family with no
   generic very probably draws nothing and raises nothing, as the unmapped
   generics did [inferred from the op-run result below; unverified for named
   families].

This is mandatory and not defence in depth, for one measured reason: **both
renderers read `<image href="/abs/path">` from disk and draw it**. resvg also
reads relative hrefs [op-run, `cmd-20261019T024223Z-405ad323ce85`]. Neither
binding can turn that off [relayed].

Mermaid source can itself carry links and images in labels [unverified for
merman], which is one more reason the sanitiser sits after the engine and not
only on ```` ```svg ```` fences.

`filter` is left out of the allow-list until someone measures its cost
[judgement; `svg-renderers.md` leaves it open].

**Fonts.** Pass imscribe's own font files with `skip_system_fonts=True`, **and
map `font_family`, `sans_serif_family`, `serif_family` and `monospace_family`**.
Without the mapping, resvg draws no text and raises nothing [op-run]. A test has
to hold this. The test renders a `<text>` with each generic family, **and with
a named family that is not installed (`font-family="Arial"`) after the
sanitiser**, and asserts that each output differs from the same SVG without
the text.

Not run here, because resvg_py is not in pptmstr's venv and this work installs
nothing: the `<use>` fan-out cost, external `url()` in presentation attributes,
and a named family under `skip_system_fonts` against resvg. They join R1's run
(§7).

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
- The texture upload runs on the GUI thread [relayed: `TextureGpu` "must be
  created on the GUI thread"].
- This is the first thing that breaks `views.py`'s "Nothing is built over more
  than one frame" stance. The figure module states that exception in its own
  docstring.
- Whether `resvg_py` releases the GIL is [unverified] (probe R2). If it does
  not, the worker is a process, not a thread.

**Height.** The height comes from the sanitised SVG, not from the raster, and
it does not wait for the worker:
- An ```` ```svg ```` fence's height is known **synchronously** in
  `host_for`: the sanitiser is pure and cheap, and the aspect ratio is read
  from its `viewBox` or `width`/`height`. `height_for(w) = min(w,
  natural_w) * h/w0`. No placeholder, so no jump beyond the one at commit.
- Mermaid's height is known only after layout, so its host answers a
  placeholder first and its height once the SVG exists: one more jump.
- **"Natural size" needs a rule** for SVG with `width="100%"` or a `viewBox`
  only (Mermaid emits the former by default, `style="max-width: ...px"` with
  it [unverified for merman]): read the `max-width` if present, else take
  `viewBox` user units as px, else refuse with a reason.
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
because it is a function of width either way. The earlier "raster once at
natural size × DPI, then scale down" is withdrawn: GPU minification without
mipmaps aliases rather than softens (a 3200 px texture shown at 700 px is
about 4.6× reduction, enough to break 1 px strokes and 10 px labels), and
whether `create_texture_gpu_from_rgba_data` makes mipmaps is [unverified];
wide Mermaid output at natural × 2 (6000×4000 is about 100 MB RGBA) would hit
the pixel cap anyway. Downscaled legibility at 2×, 3× and 4× is added to T1,
which can reinstate the simpler design if it measures acceptable.

**Textures.**
- `hello_imgui.create_texture_gpu_from_rgba_data` returns an RAII
  `TextureGpu` that is freed when its last reference drops. It needs **numpy**
  [relayed; numpy is not installed in the venv: `svg-renderers.md`, ran; and
  re-checked 2026-10-18: `import numpy` raises `ModuleNotFoundError`, run].
- The numpy-free `image_and_size_from_encoded_data` cache can only be cleared
  whole [relayed].
- **Decision: numpy and `TextureGpu`, held by the content cache above**, which
  is bounded (LRU by bytes) and so frees textures nobody shows. With
  imgui-bundle's one shared image cache, every figure in every pane would live
  until the cache is cleared [judgement].
- No texture path has been executed by anyone (no display was available)
  [relayed]. Probe T1 is the first thing slice 1 does.

**Fallback.** A failure leaves the source visible as the code block, with a
one-line reason in the warn ink, the way `_Warned` leads a diff. Failures
include a sanitiser refusal, a render error, a missing extra, an unsupported
Mermaid family, and an out-of-subset plot spec. The source is the content, and
it is never dropped [relayed from all three producer notes; adopted].

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
  with a vendored merman pptmstr cannot pin. This is the closest call in the
  record, and M1 can reverse it.
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
| `resvg_py==0.5.0` (resvg 0.48.1) | 1 | `manylinux_2_17` abi3 [relayed] | `macosx_11_0_arm64` [relayed] | not measured | MIT / MIT-or-Apache [relayed] | binding: one maintainer [relayed] |
| `numpy` | 1 | [unverified: floor of the current wheel] | [unverified] | ~15–20 MB [unverified] | BSD | mature |
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
[relayed from all four notes].

## 7. Open questions that need a measurement

The house rule: board the probe, and leave the fix undeclared until the
measurement names it. Each probe below names what it decides.

| probe | measures | decides |
|---|---|---|
| **T1** texture | a `Hosted` in a real pptmstr window uploads an RGBA buffer through `TextureGpu` and draws it with `imgui.image` clipped to its rectangle, and the texture is freed when the content cache evicts it; `ImTextureRef` from the id; **legibility of a figure rasterised at 2×, 3× and 4× the drawn width and minified by the GPU**, and whether the texture has mipmaps | whether §3.6's texture decision stands, and whether raster-once-and-downscale can replace raster-at-drawn-width; nothing in slice 1 is built until it runs |
| ~~S1~~ commit re-ask | **Run 2026-10-18 (§3.6).** A host that declines an open fence is never asked again; a `_Captioned` that marks the fence only once committed is asked once, at commit, in all six chunkings | settled: committed-fence through a commit-dependent mark (fix a) [ran] |
| **M1** merman binding | merman 0.9.1: does `options_json='{"svg":{"pipeline":"resvg-safe"}}'` emit 0 foreignObject on a flowchart, a sequence diagram and a class diagram; what `runtime_catalog_json()` says about PNG | merman vs mmdr+novasvg (§3.3) |
| **R1** real diagrams | 20 Mermaid diagrams Claude actually wrote, all families it used, through resvg and novasvg after the sanitiser, timed and looked at; plus three hostile cases against resvg: nested `g`/`use` fan-out (time and memory), external `url()` in `fill`/`clip-path`/`mask`/`marker-*`/`filter` (any read or fetch), and `font-family="Arial"` under `skip_system_fonts` with and without the appended generic | resvg vs novasvg behind the seam; whether the worker can be a plain thread; whether §3.4 steps 3, 5 and 7 are needed as written |
| **R2** GIL | whether `resvg_py.svg_to_bytes` releases the GIL (a frame loop stalls or does not while it renders on a thread) | thread vs process worker |
| **F1** foreignObject rewrite | the §3.4 rewrite applied to merman `parity` output and to mermaidx output, drawn in resvg and compared with novasvg's native foreignObject | whether rewritten labels are acceptable or the text-only foreignObject subset is needed |
| **P2** what Claude emits | about 20 "chart of…/diagram of…" prompts with and without the session's fence declaration; count fence languages [relayed design, `data-graphs.md`] | Vega-Lite subset vs CSV-with-info-string for plots; whether plots are worth slice 3 at all |
| **P3** ImPlot in a page | `begin_plot` issued at a host's cursor mid-page: clipping, scroll, the wheel with `no_inputs` | whether slice 3's host can draw ImPlot inline or needs a child window |
| **G1** glibc | which Linux machines pptmstr actually runs on fall in 2.28–2.34 | how much merman's floor costs |

Not probes, but unread: merman's THIRD_PARTY_NOTICES (licence review before
shipping); whether merman labels can carry links or images that survive into
the SVG.

## 8. Staged order of work (not declared as tasks)

Smallest useful slice first. Each slice ends with the figure visible in a real
window, not only with passing tests, because no figure has been seen drawn
(§2; STYLE.md §2).

0. **Probe T1.** It gates the shape of everything after it. S1 has run
   (§3.6). M1 and R2 can run in parallel.
1. **```` ```svg ```` fences.**
   - **The sanitiser** comes first, as a pure module with no ImGui import, in
     the functional style of STYLE.md §1. It takes text and returns sanitised
     text or a refusal reason. Each hostile case from `svg-renderers.md`'s
     probe becomes a named test, and each test is mutation-tested:
     absolute and relative `<image>`, `file://`, `http`, `<script>`, `on*`, a
     DOCTYPE, billion laughs, CSS `url()`, oversize, foreignObject → text.
   - **The renderer seam** on resvg, plus the generic-family and
     named-family font tests (§3.4). Sanitiser tests also cover `<use>`
     fan-out, `url()` in presentation attributes, and a CSS escape
     (`u\72l(`).
   - **The figure host:**
     - a figure mark in `_Captioned`, put on only once the fence has
       committed, with its own flag separate from `hosting`;
     - a dispatcher composed with `_Fences` at the two fence-carrying
       `host_for` sites (`Stream._ink`, `_open`);
     - the height from the sanitised SVG in `host_for`, no placeholder;
     - a content-keyed, bounded cache of size and texture outside the
       `Hosted`;
     - a worker, a `TextureGpu` and its own clip rect;
     - `text` set to the fence verbatim;
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
   - Dependencies: `resvg_py`, `numpy`.
2. **```` ```mermaid ````** through merman `resvg-safe`, or mmdr + novasvg if
   M1 says so. Into the same host, with the same sanitiser. It is an optional
   extra, lazily imported, and a missing extra is the fallback with its
   reason. Then the source/figure toggle (§4, row 2).
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
5. **On demand only:** DOT through pygraphviz; full Vega-Lite through
   vl-convert behind the URL pre-filter.

Upstream asks, filed when slice 1 exists, not before:
- imscribe: `_draw_hosts` clips each host;
- imtypeset: C2.

## 9. Preconditions and risks this work inherits and does not fix

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
