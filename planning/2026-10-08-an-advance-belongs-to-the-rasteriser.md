# An advance belongs to the rasteriser

**Dated:** 2026-10-08 · **Status:** a proposal for advtext's author; nothing in
`/home/wreel/Source/imgui-adv-text` was written and nothing in `pptmstr/` was changed ·
**Deliverable:** [`patches/advtext-hinting.patch`](patches/advtext-hinting.patch) ·
**Extends** [`2026-10-07-advtext-sets-the-text.md`](2026-10-07-advtext-sets-the-text.md)
§13's line "Font-file metrics against ImGui's hinted advances", which named the symptom
and not the cause.

The operator, who wrote advtext, 2026-10-08: *"I though advtext _did_ hinting."* and
then *"Is advtext leaving hinting out of scope a general weakness? We can fix that up
and then rebuild if needed."*

Symbol names, not line numbers. Every figure below was produced in the session that
wrote this, headless, on imgui-bundle 1.92.900, at density 1. §9 is what that leaves
out. Where a figure came from a throwaway probe rather than a committed script, it says
so, and the probe is in §11.

---

## 1. What was established, and what did not hold

The lead's probe (`shots/lead/hint_modes.py`) was re-run before anything was built on
it. **Its numbers reproduce exactly**: under every hinted loader mode every advance is a
whole pixel; only `font_loader_flags = 1` gives the file's (Roboto 16 px `p` 7.656
baked against 7.66; worst 0.006 px over the 47 characters); Inconsolata hinted is
+0.327 / +0.374 / +0.467 px on every glyph at 14 / 16 / 20 px.

The loader is FreeType: `io.fonts.font_loader_name` reads `FreeType (2.13.3; 2.13.3)`.
It reads `None` until a frame has run, which is one reason nobody had seen it.

Four things the task was launched believing did not survive a run. They are first
because the design depends on two of them.

1. **"LightHinting's advances are whole pixels like the default's, so its difference is
   in the outlines only."** Whole, yes. The same, no. The lead's own table has it:
   Roboto at 14 px totals 266 by default and 262 under light. Per glyph, Roboto's `m`
   at a 14 px height is 10.47 px in the file, **10 under light and 11 by default**.
   Surveyed (§11's probe): over 317 glyphs of Roboto regular at every height from 10 to
   40 px, a light advance is the file's advance rounded in all but a handful of ties
   (worst departure 0.507 px); a default advance is not (at 16 px, 61 of 317 differ
   from the rounded file advance; worst 0.91 px, bold at 24). The outlines differ as
   well: 86 of 94 ASCII glyph boxes at 16 px. So `LIGHT` is its own mode.
2. **"Its measurement script exists"**, of `docs/advance-rule.md` §7's anomaly. It does
   not. `scripts/pen_rules.py --native` reads pens from the vertex buffer; the ink
   shares in §7 (8.1% / 84.9% against 18.4% / 72.3%) came from a harness that was never
   committed (`git log -S'84.9'` finds only the commit that added the sentence). §6
   below is a reconstruction, and says so.
3. **"`fontlib_metrics` ... correct for NONE"** is true of the *advances* and is not
   enough. See §2: with hinting off and the file's metrics, the text is still drawn at
   a different width from the one it was laid out at, by the same amount as before.
4. **advtext's own instrument never showed the monospace defect, by coincidence.**
   Inconsolata's advance is exactly half an em. advtext's demo and tests size by em
   (`Theme.build()`, 16 px), where that is 8.000 px in the file and 8 in the atlas.
   pptmstr sizes by pixel height, where it is 7.63 in the file and 8 in the atlas. The
   defect was always there and the library's fixture sat on the one size that hides it.

One more, about the upstream source rather than the tree: `docs/subpixel.md` §4.3 says
`PixelSnapH` is what rounds an advance and that it is off. A summarised read of
`imgui_freetype.cpp` at v1.92.0 says the loader sets `PixelSnapH` itself when hinting
is on. Measured here, `pixel_snap_h` reads `False` on the loaded font's source under
every mode and the advances are whole anyway. I did not read the bundle's own 1.92.9b
source and cannot say which of FreeType or that option does the rounding; the patch
claims only what was measured.

---

## 2. The weakness, stated precisely

It has two halves and the lead named one.

**The first: an advance is not a property of (font, size).** advtext takes every
advance from the font file, linear in the size (`advtext.fontlib.FaceMetrics.advance`),
and `advtext/fontmetrics.py` lists hinting as out of scope: "A glyph is an outline and
an advance." Under a hinting rasteriser the advance is chosen by the hinter for the
glyph it drew. It belongs to (font, size, hinting mode). The library's layout and the
library's atlas were describing two different sets of glyphs, and
`docs/decisions.md` §4 chose that on purpose, reasoning that ImGui's advances were
*our* advances rounded and so measuring through them "rounds twice". They are not ours
rounded. They are the true advances of what is on screen.

**The second: the draw rule needs whole-pixel advances whatever the hinter does.**
`_RigidSpan.pen` rounds every gap inside a rigid span to a whole pixel and accumulates.
That is `docs/advance-rule.md`'s rule and the user chose it by eye. Its recorded cost
is that a word's width drifts from the layout's -- 0.40 px mean, 2.09 worst -- "and the
inter-word space absorbs it". That cost is not the hinter's. It is paid whenever the
layout's gaps are fractional, so turning hinting off does not remove it:

`scripts/pen_rules.py --hinting --pixel-height 16` (added by the patch), `demo/sample.md`
at 700 px, sized as a host sizes it:

| hinting | laid out from | advances off | steps off by >½ px | word width moved, mean | worst | words off by ≥1 px |
|---|---|---|---|---|---|---|
| default | the file (**advtext today**) | 197 of 207 | 793 of 3453 | 0.5222 px | 10.2274 px | 93 of 824 |
| default | the atlas (**the patch's pairing; pptmstr's seam today**) | 0 of 207 | 0 | 0.0034 px | 0.1734 px | 0 |
| default | the atlas, kerns left fractional | 0 of 207 | 0 | 0.0526 px | 0.6200 px | 0 |
| light | the file | 197 of 207 | 138 | 0.5222 px | 10.2274 px | 93 |
| light | the atlas | 0 of 207 | 0 | 0.0034 px | 0.1734 px | 0 |
| none | the file | **0 of 207** | 21 | **0.5222 px** | **10.2274 px** | **93** |
| none | the atlas | 0 of 207 | 0 | 0.5479 px | 11.3591 px | 101 |

"Advances off" is the patch's `check_advances`. "Steps" are adjacent glyphs in a span:
the drawn step against the baked advance of the left glyph plus the kern the layout
set. The 10 px is a fenced code line -- one rigid span, 7.63 laid out and 8 drawn for
every character.

At `Theme.build()`'s 16 px em the same table has 109 of 207 advances off, 53 steps off
and the documented 0.3984 / 2.0938 / 66 of 824, which is `docs/advance-rule.md` §4's
own figure reproduced.

So the answer to the operator's question: **yes, it is a general weakness, and it is
not "advtext does not hint".** Hinting is the rasteriser's and was happening all along.
The weakness is that the layout did not know, and that the one draw rule the library
ships is only consistent with a layout whose advances are whole pixels -- which a
hinted atlas supplies for free and the font file never does.

---

## 3. The design, and the alternatives

**Unhinted only.** Load with `NoHinting`, keep `fontlib_metrics`, change nothing else.
It is one flag and it makes the file true. It is refuted by row six above: the drawn
width still moves 0.52 px mean and 10 px worst, because the draw rule rounds. To be
consistent it needs fractional pens -- advtext's `exact` rule, which
`docs/advance-rule.md` §5 disqualifies at 1303 sub-pixel phases, and `docs/subpixel.md`
§4.2 records the user ruling out. It also gives up hinted outlines. Unhinted is the
right atlas for a backend that places glyphs at fractional positions; advtext's SVG
backend is one and its ImGui backend was decided not to be.

**Host-side override, as pptmstr has now.** `pptmstr/ui/typeset.py`'s `_PixelMetrics`
wraps the library's metrics and asks ImGui's baked font for each advance. It works --
it is row two. Its cost is that every host re-derives it: the size conversion
(`baked_size_px`'s arithmetic, copied), the `ImFontBaked` lifetime, the kern rule, the
invalidation on reload. The library is the party that knows all four, and "the same
resolver measures and draws" stays, in `examples/minimal.py`'s words, "the one
contract in this pipeline that nothing enforces".

**This: the mode is chosen where faces are loaded, and the loaded faces answer for
their own metrics.** In `advtext/ui/fonts.py`:

```python
class Hinting(enum.Enum):
    DEFAULT = "default"   # the loader's own flags; what every existing caller gets
    LIGHT = "light"       # ImGuiFreeTypeLoaderFlags_LightHinting, 1 << 3
    NONE = "none"         # ImGuiFreeTypeLoaderFlags_NoHinting,    1 << 0

    @property
    def loader_flags(self) -> int: ...

def atlas_loader(size_px=DEFAULT_LOAD_PX, atlas=None, *, hinting=Hinting.DEFAULT) -> Loader
def load_faces(faces=TEXT_FACES, *, loader=None, hinting=Hinting.DEFAULT) -> LoadedFaces

class LoadedFaces:
    def __init__(self, loader=None, *, hinting=Hinting.DEFAULT) -> None
    @property
    def hinting(self) -> Hinting
    def metrics(self, px_per_inch=DEFAULT_PX_PER_INCH) -> MetricsResolver

def baked_metrics(fonts: FaceFont, *, file=fontlib_metrics,
                  px_per_inch=DEFAULT_PX_PER_INCH) -> BakedMetrics
class BakedMetrics:        # the MetricsResolver; __call__(key), clear()
class BakedFaceMetrics:    # a FontMetrics; advance from the atlas, the rest from the file
```

and in `advtext/backends/imgui_draw.py`:

```python
ADVANCE_TOLERANCE_PX = 1.0 / 64.0

@dataclass(frozen=True, slots=True)
class AdvanceCheck:
    glyphs: int = 0; disagreeing: int = 0; worst_px: float = 0.0
    @property
    def agrees(self) -> bool

def check_advances(cmds, *, fonts, metrics=fontlib_metrics,
                   px_per_inch=DEFAULT_PX_PER_INCH) -> AdvanceCheck
```

`Hinting`, `baked_metrics` and `check_advances` join the facade.

`baked_metrics` is named in the library's own word for it (`baked_size_px`, `_baked`,
`ImFontBaked`) and by its source, as `fontlib_metrics` is. It takes the advance from
`ImFontBaked.get_char_advance` at the size `draw` will ask for -- the same
`baked_size_px` arithmetic, quantised by ImGui the same way -- and everything a raster
does not define from the file.

`LoadedFaces.metrics()` returns `fontlib_metrics` itself under `NONE` (the file is true
there and needs no context) and one `BakedMetrics` per `px_per_inch` under the hinted
modes. It is a method and not a property because a font key carries its size in scaled
points and the atlas is asked in pixels, so the resolver has to know the theme's
`px_per_inch`.

**Why the resolver asks the atlas rather than predicting the mode.** A wrong
declaration cannot then hurt under a hinted mode: whatever the loader really did, the
advance read is the one baked. That is also what makes it right under a loader this
enum cannot name (a host's own flags, a stb_truetype build). The one declaration taken
on trust is `NONE` with a host's own loader, and `check_advances` catches it (tested).

**Detecting a mismatched pair: a check, not a `DrawStats` field.** The task suggested
either. `DrawStats` is rebuilt by naming its three fields in `advtext/ui/bare_view.py`,
`advtext/ui/diff_view.py`, `advtext/ui/pages/columns.py` and
`advtext/ui/pages/mathtext.py`, so a fourth field would be dropped by every view that
aggregates unless all of them changed, and the patch would stop being small. It also
costs two calls for every distinct glyph on every frame to learn something that does
not change between frames: `check_advances` over `demo/sample.md` took 1 to 3 ms here
beside a 21 ms draw (one run each, with the test suite running alongside, so read it as
an order of magnitude). So it is a function a host calls once, or a test calls. The
operator may prefer the always-on form; §10.

`check_advances` forgives two differences and says so: the rasteriser's own 1/64 px
grid, and the whole-pixel size -- `_baked` draws a face laid out at 18.75 px from the
19 px raster, `docs/subpixel.md` §5.1's knowing trade, which makes every file advance
1.3% short. Without the second allowance the check would report the library's own
accepted quantisation under `NONE` and be useless for the question it is for.

---

## 4. What the patch does and does not do

Twelve files, +1,712 / -35 as `git diff --stat` counts it. A third of that is one test
file and most of the rest is prose: docstrings and four documents.

| file | change |
|---|---|
| `advtext/ui/fonts.py` | `Hinting`, the `hinting=` arguments, `LoadedFaces.hinting` / `.metrics`, `BakedMetrics`, `BakedFaceMetrics`, `baked_metrics`; a docstring section |
| `advtext/backends/imgui_draw.py` | `check_advances`, `AdvanceCheck`, `ADVANCE_TOLERANCE_PX`. **`draw` is untouched** |
| `advtext/__init__.py` | three names on the facade |
| `advtext/fontmetrics.py` | the out-of-scope line: hinting is the rasteriser's, its consequence for the advance is not out of scope |
| `tests/test_hinting.py` | new; 36 tests |
| `scripts/pen_rules.py` | `--hinting`, `--pixel-height`: the shipped rule against the atlas, a mode and a resolver at a time |
| `scripts/ink_by_path.py` | new; the instrument for §7's anomaly |
| `docs/advance-rule.md` | §2's attribution corrected, a new §2a with the tables, §7's anomaly |
| `docs/subpixel.md` | a CORRECTION block at the head of §4 |
| `docs/decisions.md` | an AMENDMENT block under §4, marked as proposed |
| `docs/guide/tex.md`, `docs/guide/future.md` | one sentence and one paragraph that said "no hinting" |

**It does not change a default.** `load_faces()` with no `hinting` makes the same
`add_font_from_file_ttf(path, size)` call it made before, with no config at all
(tested), and every `metrics=fontlib_metrics` default in the tree is as it was. No
existing test, page or example was edited.

**It does not touch `draw`, `_RigidSpan`, or the layout core.**

**It does not rewrite the other places that say stb_truetype** -- `docs/status.md`,
`docs/litmus.md`, `docs/imgui-risks.md`, and docstrings in `imgui_draw.py` and
`fontlib.py`. There the name stands for the size convention (pixel height from `hhea`),
which holds under FreeType: an unhinted bake agrees with the file at that conversion to
a hundredth of a pixel. The amendment in `docs/decisions.md` lists them.

**It does not hint mathematics' layout.** `BakedMetrics` answers `Face.MATH` from the
file. A formula is measured through `advtext.math.MathResolver`, a different seam, from
advances read together with italic corrections and variant extents only the file
states. Under a hinted mode a formula's glyphs are therefore still laid out from the
file, and `check_advances` will say so for a document with mathematics in it.

**It does not make `Hinting.NONE` consistent.** §2. The docstring says so in as many
words.

---

## 5. Kerning under hinted advances

**Decided: a pair kern is rounded to the pixel when the advance before it is a whole
pixel, and left as the file's when it is not.** (`BakedFaceMetrics.kern`.)

The reason is arithmetic about what is already drawn. `_RigidSpan.pen` sets a gap at
`snap_px(advance + kern)`. With a whole-pixel advance that is `advance + snap_px(kern)`:
the kern is rounded on screen whichever number the resolver returns. The pixels inside
the word are identical either way. What differs is what the *layout* believes the word
measures, and so where the next word starts. Measured, third row of §2's table against
the second: left fractional, the layout is 0.05 px mean and 0.62 px worst wider or
narrower than the word, and that lands in the following space; rounded, zero. Uneven
word spaces are the complaint this whole thread started from, so the kern follows the
advance onto the grid.

The condition is read from the data, not from the mode: where the baked advance is
fractional (no hinting; any mode at a `rasterizer_density` above 1) the gap is the
rounding of a fractional sum already, and rounding the kern first would round it twice.

This is what pptmstr's seam does (`_PixelMetrics.kern` rounds always, and is only used
hinted). It is also what FreeType itself does with a `kern`-table pair under hinting
(`FT_KERNING_DEFAULT` is grid-fitted) -- recalled, not checked this session, and
advtext's kerns are GPOS read by fontTools, not FreeType's.

**Gap evenness under both modes.** advtext's own instrument (`scripts/pen_rules.py`,
the part the patch does not touch) measures the rule against the *layout's* design and
reads no advance from the atlas, so the hinting mode cannot move it: 0 of 3453 gaps
more than half a pixel off, run here under the default flags, the documented figure.
The comparison that a mode can move is the one against the *atlas* (`--hinting`, at a 16 px height): 793 of 3453 off under default hinting
from the file, 21 under none from the file, 0 from the atlas under either hinted mode.

---

## 6. The anomaly in `docs/advance-rule.md` §7

*"ImGui's own text measures softer than `whole` or `advance`"*: solid 8.1% / midtone
84.9% against 18.4% / 72.3%, "yet section 2 shows ImGui's pens are all integers".

**Hinting does not explain it.** `scripts/ink_by_path.py` (new, in the patch) puts
"people" on one draw list by both paths under each mode and composites it on the CPU
from the vertex buffer and the atlas's own pixels. At the same whole pixel size the two
paths emit the same texels at whole-pixel pens and the ink is identical:

| | solid | midtone |
|---|---|---|
| default, `add_text` at 19 | 33.6% | 62.9% |
| default, `draw` at a 16 px em (drawn from the 19 px raster) | 33.6% | 62.9% |
| default, `add_text` at 16 | 38.7% | 57.4% |
| default, `draw` at a 16 px height | 38.7% | 57.4% |
| **default, `add_text` at 18.75** | **7.8%** | **85.4%** |
| none, `add_text` at 16 (5 of 6 quads off the grid) | 10.2% | 81.4% |
| none, `draw` at a 16 px height | 29.2% | 64.9% |

**A fractional size does.** 18.75 is the pixel height of a 16 px em, unrounded -- what
`baked_size_px` returns. ImGui serves it from the 19 px raster scaled by 0.987, so
every quad is resampled: `docs/subpixel.md` §2.3, the defect `_baked` fixed on
advtext's own path. A comparison row that drew ImGui's text "at the same size" as the
advtext rows would have asked for 18.75. That is the first of §7's two untested
candidates ("a baked-size mismatch") in the one form that produces the number, and
7.8% / 85.4% against the recorded 8.1% / 84.9% is close.

How far to trust that: **it is a reconstruction.** The original harness is gone, the
composite is black on white on a CPU and not a screenshot of ink on a page, and the
other half of the recorded pair does not reproduce at all (18.4% recorded, 33.6% here).
So the candidate reproduces the *soft* figure and the direction, and I cannot show it
is what happened. The second candidate, differing atlas UVs, is refuted for equal
sizes. The patch rewrites §7's entry to say this much and no more.

---

## 7. How to apply and test it

The patch is `git diff` of a copy of the library's working tree against itself. What
was copied, exactly: `git archive HEAD` at `a3c589a1ea150182cc7d7deefa0eccff680a5b7b`,
then the tree's uncommitted diff (`docs/guide/README.md`, `tests/test_docs.py`, one
line each) and the untracked `docs/postmortem-2026-10-adoption-and-demo.md`, plus the
ignored `docs/tex.web`. The patch touches none of those three files, so it applies with
or without the uncommitted work.

```sh
cd /home/wreel/Source/imgui-adv-text
git apply --check /path/to/pptmstr/planning/patches/advtext-hinting.patch
git apply         /path/to/pptmstr/planning/patches/advtext-hinting.patch
make check
.venv/bin/python scripts/pen_rules.py --hinting --pixel-height 16
.venv/bin/python scripts/ink_by_path.py
```

What was run here, and how: see §8's last table. In short, `git apply --check` against
two fresh copies (bare HEAD, and HEAD plus the uncommitted work); advtext's `ruff`,
`black --check`, `mypy` and full `pytest` inside the patched copy, with this worktree's
interpreter and `PYTHONPATH` set to the copy, `advtext.__file__` asserted to be inside
it. **`make check` itself was not run**: the library's Makefile wants its own `./.venv`
and the copy has none. The four commands it runs were run by hand.

To see the pairing in a window, which nobody has (§9):

```python
loaded = load_faces(hinting=Hinting.DEFAULT)      # or LIGHT, or NONE
pane = MarkdownPane(MARKDOWN, metrics=loaded.metrics())
```

`examples/minimal.py` builds its pane before its fonts, so that is a reordering there;
the patch leaves the example alone.

---

## 8. What pptmstr's seam deletes once it is applied

Named against `pptmstr/ui/typeset.py` as it stood when this was written. The `roles`
task was rewriting that file at the time, so the names may have moved; the shape will
not have.

- **`_PixelMetrics.advance`, `._measure`, `.kern`, `.space_width`, and the class's
  `_advances` / `_font` / `_tall` slots** -- this is `BakedFaceMetrics`. One difference
  to decide on rather than discover: for a codepoint the loaded face lacks,
  `_PixelMetrics._measure` returns the file's advance *rounded to the pixel*, and
  `BakedFaceMetrics.advance` returns it unrounded (§10, item 4).
- **`_PIXEL_METRICS`** and the three places that clear it -- `BakedMetrics` drops what
  it holds when a role's `ImFont` changes.
- **`_UNHINTED` and the two `font_loader_flags` branches** in `_atlas_loader` and
  `_remembering` -- `Hinting.NONE.loader_flags`, or `atlas_loader(hinting=...)` for the
  headless one.
- **The `METRICS` switch's `"hinted"` / `"file"` choice of class** in `pixel_metrics`
  -- `LoadedFaces(loader, hinting=...).metrics()` makes that choice.

What stays, because it is the host's policy and not the rasteriser's fact:

- **`_LineMetrics.bbox`** (no glyph measured taller than its line). It wraps whatever
  resolver the library hands back.
- **`pixel_metrics` as a name**, as the thin wrapper that applies `_LineMetrics` over
  `_fonts().metrics()`.
- `_px`, `_half_up`, `_sp_per_px` -- still used by sizing code that has nothing to do
  with advances.

Not done, and not claimed: nobody has made that change in pptmstr or run pptmstr
against a patched advtext. `./.venv` resolves the *original* advtext.

| what | where | result |
|---|---|---|
| lead's probe, re-run | worktree, original advtext | reproduces exactly |
| baseline: advtext's suite, unpatched | fresh copy at HEAD + uncommitted | 4894 passed, 7 skipped, 4 xfailed |
| advtext's suite, patched | the working copy, and again a fresh copy the patch file was applied to | 4933 passed, 7 skipped, 4 xfailed, both (39 more: 36 new, 3 from the facade's per-name test) |
| `ruff check advtext tests scripts examples` | the copy | clean |
| `black --check` same set | the copy | clean (133 files) |
| `mypy advtext` (strict) | the copy | clean (50 files) |
| `tests/test_hinting.py` alone | the copy | 36 passed |
| the same with the layout mutated to `fontlib_metrics` | the copy | 6 failed, as they should |
| `git apply --check`, HEAD | fresh copy | applies |
| `git apply --check`, HEAD + uncommitted | fresh copy | applies; applied tree is file-for-file the working copy |
| `make lint test` | this worktree | lint clean; 3340 passed, 3 xfailed -- with two other builders' uncommitted work in the tree, and nothing of this task's in it but this file and the patch |

The tool versions are the library's own: this worktree's `.venv` is a composite whose
`ruff` is a symlink into the library's venv and whose `black` 26.5.1, `mypy` 2.3.1 and
`pytest` 9.1.1 resolve from the library's site-packages.

---

## 9. What is unverified

- **No window, no GPU, no eye.** Every figure is an advance the atlas reports, a pen in
  a vertex buffer, or a CPU composite. Nobody has looked at text set with
  `loaded.metrics()` through advtext's own example or demo. pptmstr's panes are the
  nearest thing -- they have run with the equivalent pairing since `pixel_metrics` was added
  -- and that is evidence about the idea and not about this code.
- **1x only.** At `rasterizer_density = 2` a hinted advance is a whole *device* pixel:
  7.75, 7.25 for Roboto at 16 (one hand probe). `BakedFaceMetrics.kern` then leaves
  kerns fractional, and `_RigidSpan.pen` still rounds to whole *layout* pixels, which
  on a HiDPI display throws away half the grid the hinter fitted to. That is a
  pre-existing property of the draw rule and where hinting matters least; the patch
  neither fixes nor measures it.
- **A real renderer backend.** `get_char_advance` was called with no backend attached
  and, in the tests, outside a frame. Under a renderer that uploads textures it is
  unrun.
- **`hello_imgui`'s loader.** `Hinting.loader_flags` is offered for a host's own
  `ImFontConfig`; that it reaches FreeType through `hello_imgui.load_font`'s
  `FontLoadingParams.font_config` rests on the `roles` builder's use of it in
  `typeset.py`, which I read and did not run.
- **Late-loaded faces.** A face `load_pending` loads after a layout measured it changes
  that face's advances from the file's to the atlas's under a hinted mode. `BakedMetrics`
  notices; the host's `char_measure` and every laid-out view do not. The library has
  the same exposure after `register_font` and handles it by convention. Untested
  beyond `test_a_reloaded_face_is_measured_from_its_new_font`.
- **Mathematics**, per §4. No formula was laid out under a hinted resolver.
- **Other FreeType versions, other builds.** The pinned facts (`m` is 10 or 11) are
  facts about FreeType 2.13.3 and Roboto's instructions. `tests/test_hinting.py` will
  go red on a bundle that changes them, which is the intent, and whoever sees it should
  know it may be a rasteriser change and not a regression.
- **The upstream mechanism** for whole-pixel advances (§1, last paragraph).
- **`FaceFont` identity.** `BakedMetrics` compares the `ImFont` a role maps to by
  identity. `LoadedFaces` returns the same object every time. A host callable that
  builds a new Python wrapper on each call would defeat the memo -- correct, and slow.
  Noted in the docstring; not tested.

---

## 10. Decisions left to the operator

1. **The default.** The patch changes none. **I would recommend `Hinting.DEFAULT` with
   `LoadedFaces.metrics()` as what a host is shown first** -- that is, keep loading as
   now and change which resolver `examples/minimal.py`, the views and the demo use.
   Reasons, in order: it is the only pairing in §2's table where the text is drawn at
   the width it was set at; it changes no pixel of any glyph, only where words start,
   so the appearance judgement the user already made about hinted outlines stands; and
   pptmstr has run the equivalent since `pixel_metrics` was added to its seam. Against it: it reverses
   `docs/decisions.md` §4 for the advance (the amendment argues why), every one of
   advtext's recorded layout figures and photographs moves, and line breaks change.
   `LIGHT` with `metrics()` is equally consistent and keeps advances within half a pixel
   of the design where the default strays to 0.9; whether its softer horizontal fit
   looks better is exactly the judgement `docs/visual-review.md` reserves to a person.
   `NONE` I would not recommend for this backend, per §2, unless the draw rule changes
   with it.
2. **Whether the mismatch should be visible on every frame** (`DrawStats`) rather than
   on request (`check_advances`). §3.
3. **Whether `docs/decisions.md` takes the amendment.** It is written as proposed.
4. **A glyph the face lacks.** File advance unrounded (the patch) or on the pixel grid
   (pptmstr's seam). Unrounded leaves a 0.17 px residue in the one span of
   `demo/sample.md` that has such a glyph; rounded would need the resolver to decide
   the face "is on the grid" from its other glyphs.
5. **The remaining "stb_truetype" sentences.** Listed in the amendment, left alone.
6. **Whether the draw rule should become density-aware.** Outside this patch; §9.

---

## 11. The survey probe

The per-mode survey in §1 (317 glyphs, heights 10 to 40) is from a throwaway script,
not from anything in the patch; `tests/test_hinting.py` pins a subset of it. Kept here
so the figures can be re-derived. Run from the worktree root with
`./.venv/bin/python -I`.

```python
import math
from advtext import Face, fontlib, fontlib_metrics, px_from_sp, sp_from_px
from advtext.fontlib import em_size_for_pixel_height
from advtext.style import font_key
from imgui_bundle import imgui

imgui.create_context()
io = imgui.get_io()
io.display_size = imgui.ImVec2(1024.0, 768.0)
io.delta_time = 1 / 60
io.backend_flags |= imgui.BackendFlags_.renderer_has_textures.value
MODES = (("default", 0), ("NoHinting", 1), ("LightHinting", 8))
for face in (Face.PROSE, Face.PROSE_BOLD, Face.PROSE_ITALIC, Face.CODE, Face.MATH):
    fonts = {}
    for label, bits in MODES:
        cfg = imgui.ImFontConfig()
        cfg.font_loader_flags = bits
        fonts[label] = io.fonts.add_font_from_file_ttf(
            str(fontlib.imgui_font_file(face)), 16.0, cfg
        )
    cps = [c for c in range(0x21, 0x17F) if fonts["default"].is_glyph_in_font(c)]
    for size in range(10, 41):
        key = font_key(face, sp_from_px(em_size_for_pixel_height(face, float(size))))
        real = [float(px_from_sp(fontlib_metrics(key).advance(c))) for c in cps]
        row = [f"{face.name:13} {size:2}px n={len(cps)}"]
        for label, _ in MODES:
            baked = fonts[label].get_font_baked(float(size))
            adv = [float(baked.get_char_advance(c)) for c in cps]
            whole = sum(1 for a in adv if abs(a - round(a)) < 1e-3)
            err = [a - r for a, r in zip(adv, real)]
            rounded = sum(1 for a, r in zip(adv, real) if a == math.floor(r + 0.5))
            row.append(
                f"{label}: whole {whole} max|d| {max(abs(e) for e in err):.3f} "
                f"==round(file) {rounded}"
            )
        print(" | ".join(row))
```
