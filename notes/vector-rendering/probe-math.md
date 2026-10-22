# Probe: a formula from a ```` ```math ```` fence, against imscribe 0.2.0

2026-10-21. Task `math-probe`. The operator's decision: mathematics is set only
from a ```` ```math ```` fence, and `$…$` / `$$…$$` in prose stay text.

**Recommendation: route (b), a display-formula block spliced in where the fence
was, with the library asked first whether it will set the formula.** It is the
only route whose formula is part of the document: it is selected, copied and
laid out with the prose, inked by the same palette. After the first frame it costs
no more a frame than the fence it replaces. Four changes to pptmstr come with it
(below). Two of them are not optional: without the asking, one refused formula
takes the whole entry off the typeset path; and `_Captioned.blocks` as it is
quietly drops every caption in a stream.

## How it was run

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/math_probe.py

- imscribe 0.2.0 and imtypeset 0.4.3.192900 from the worktree `.venv`, Mesa
  llvmpipe under Xvfb.
- Output: `shots/math-probe/results.json` holds every number below, and the
  screenshots are `shots/math-probe/*.png`. `/shots/` is git-ignored, so
  re-running the command is how they are regenerated.
- Run five times. The last three used the script as it stands now. Timings moved
  by up to 15% between runs. Every other result was the same each time.
- The working tree had another task's uncommitted mermaid changes in
  `pptmstr/figures/host.py` and `views.py`, and `host.py` changed between the
  fourth run and the fifth. Every result but the timings was identical across
  those two runs. None of those changes touches `_Captioned.blocks`.

**Nothing under `pptmstr/` was edited.** For the length of the run the script
puts each route into pptmstr's real seam by replacing three names:

- `figures.host.is_figure_fence` also answers for `math`, so `_Captioned` decides
  "settled" for a math fence exactly as it does for svg;
- `views._captioned` turns a settled math fence into the route's block;
- `views._Hosts.__call__` answers a marked math block with the route's widget.

Drawing goes through `typeset.markdown(...)` for a settled document and
`typeset.stream(...).draw()` for a stream: the calls the panes make.

## Premises corrected by reading or running

- **`BareView` and `markdown_math` do not exist in 0.2.0.** Both
  `hasattr(imscribe, ...)` checks are False, and `imscribe.mathtext` is gone. The
  README calls them removed (section 0d of imscribe's `docs/status.md`).
- **`TypesetView(math=…)` and `MarkdownStream(theme, math=True)` turn `$` on for
  the whole text.** They are therefore usable only on a body that is nothing but
  the formula, which is what routes (a) and (b) do.
- **The parser is imtypeset's, not imscribe's.** imscribe makes a
  `RUN_DISPLAY_FORMULA` run over the source. imtypeset's `src/mathtext.cpp` (the
  pinned ref 0bac066 is the same file as HEAD) parses it, and either sets it or
  refuses it with a kind and a sentence. `imscribe/math/` is the reference core:
  it says "no parsing at all", and nothing of it reaches a window.
- **"Shows a refused formula as its source" is true of the library, and in
  pptmstr it is not where things stop.** A refusal makes `TypesetView.draw()`
  answer false. pptmstr's `_short` reads false as "set less than it was handed"
  and moves the whole entry to ImGui as written (route b_raw below).

## Route (b): a display-formula block in the fence's place

What it does: `parse_markdown("$$\n<body>\n$$", theme, references=False,
math=True)`. The result is accepted only when it is exactly one `MarkdownText`
holding one formula whose state is `"set"`. A body with a `$$` line in it reads
as something else and is refused. That block replaces the fence, and
`TypesetView` takes blocks a host made (imscribe's P8). Nothing in the document
is read with math on, only this one body.

Before splicing, the library is asked: an `imtypeset.Paragraph` with one display
run is laid out, and `formulas()[0].refused/.reason` is read. A body it refuses
stays code, captioned `math`, under a line in the warn ink:
`math not set: <reason>`. That is the same form `_captioned` gives a refused svg.

| measured | result |
|---|---|
| renders | `settled-b.png`: a sum with limits, a multi-line body and a `\left(…\right)^2` with integrals, set centred in the document's prose |
| refused | `settled-b.png`: `math not set: \begin opens or closes an environment, and there are none here`, then the fence as written |
| without asking first (`b_raw`) | `settled-b-raw.png`: the first frame's draw answers false; `entry.unset = "could not set something in view as it was asked to"` and the whole entry is ImGui's from then on |
| multi-line body | read as one formula: the reader joins the lines with spaces. `\\` and `&` are refused, so a body cannot ask for line breaks |
| `$` elsewhere | every check true in `view.texts`: `$PATH=$HOME/bin`, `$5 and $10`, `$e^{i\pi}+1=0$`, `$$x^2$$` in prose, `$HOME`, and `"$$not math$$"` inside a python fence |
| streaming | `b_stream`: code from ```` ```ma ```` until the closing line's newline arrives, then `formula`. A last fence with no newline after it is code until `close()`, then `formula`. Same commit point as svg (`stream-b-open.png`, `stream-b-closed.png`) |
| theme switch | `light-b.png`: the formula's ink changes with the prose's. The probe's window background stays dark, so this shot shows that the ink follows and says nothing about legibility |
| width | `narrow-b.png` at 380 px: formulas re-centre. **A formula wider than the measure is clipped at the pane's edge.** It does not wrap, and `content_width` stays 380, so nothing scrolls to it |
| copy | select-all copies the formula's **source with no fence and no `$$`** (`\sum_{i=1}^{n} i = \frac{n(n+1)}{2}`). In a stream, what `selected_source` gives for that block was not measured |
| cost, 20 fences, ~8 in view | median 0.251 ms a frame (code fences: 0.308); first frame 5.29 ms (code: 3.25), which includes the 20 asks |

**What (b) needs from pptmstr**, each one measured:

1. **Settling a math fence the way an svg fence settles.** `_Captioned.blocks`
   computes `settled` only when `figures_host.is_figure_fence(...)` is true, and
   `_captioned` converts only under `figures=settled`. The probe widened
   `is_figure_fence`. The real change should name the rule itself ("fences that
   become something once settled"), not make math a figure. The S1 rule holds
   without further work, because the spliced block is a new value at commit.
2. **`_Captioned.blocks` reads `answer[0].language`, and a `MarkdownText` has
   none.** The `AttributeError` comes out of a property, and `_Captioned`
   defines `__getattr__`, so Python retries `blocks` through it and gets **the
   library stream's raw blocks**. Run with the line as it is
   (`b_stream_unpatched`): no error was raised and the gate did not stop. The
   view held `["code:x^2", "MarkdownText"]`: the fence uncaptioned and
   unconverted, with no sign anything was wrong (`stream-b-unpatched.png`). The
   probe changes that one read to `getattr(answer[0], "language", None)`.
3. **The ask before the splice.** Without it, see `b_raw`. The probe asks at the
   library's default style, not at the document's pixel size. The refusals that
   depend on size (a radicand or a `\left…\right` taller than the font's largest
   variant, a glyph the face lacks) can answer differently at the document's
   size. That case then ends in the `b_raw` fallback. The ask should be made at
   the block's size (`Style.FontSize`), or `Document.block_error(n)` should be
   read after the first draw. Neither was measured.
4. **The theme the body is read with.** The probe used `document_theme()`. A
   settled part with `demote` reads with `themes.demoted(n)`, and a stream's
   blocks have to be built with the stream's own theme (`TypesetStreamView`
   refuses `set_theme`).

## Route (a): a Hosted widget holding its own TypesetView

What it does: the fence is marked (a `str` subclass, as `FigureMark` is). The
dispatcher answers it with a widget that holds
`TypesetView(theme=document_theme())` showing the one block from route (b).
`height_for` is `view.layout_for(w)` and `draw` is
`view.draw(palette=palette, width_px=w)`.

| measured | result |
|---|---|
| renders | `settled-a.png`: yes, inside the host's rectangle. A view nested in a hosted block draws |
| fonts and palette | shared: the nested view uses the same loaded faces, and the parent's `palette` reaches it through `Hosted.draw`. `light-a.png` shows its ink follow |
| layout | indented by the code block's indent (a widget hosted in a code block's place takes it). The block's vertical rhythm differs a little from (b): compare the two shots |
| `height_for` | `layout_for` answers an **estimate** before the first draw: 17 px, then 38 px from the frame after. The host's block moves once, in the frame after it first appears |
| refused | the nested library shows the source, centred, with **no reason** (`settled-a.png`). The nested `draw` answers false, and the parent's `whole` stays true because a host's answer is not counted, so nothing falls back. The reason is there to draw (the ask found it), but the widget would have to draw it itself |
| `$` elsewhere | all true |
| streaming | `a_stream`: the same transitions as (b), with `hosted:NestedView` in place of `formula` |
| width | `narrow-a.png`: the wide formula is clipped, as in (b) |
| selection and copy | the parent's select-all copies the **fence as written** (`Hosted.text`). Selecting in the nested view takes ownership: the parent's selection is cleared, and the nested copy is the formula's source |
| cost, 20 fences | median 0.861 ms a frame (3.4 times (b)), p90 1.38; first frame 8.56 ms. One `TypesetView` per fence, and a view that hosts a widget never rests |

## Route (c): what else imscribe and imtypeset offer

- **imscribe** has no formula block type and no `markdown_math` in 0.2.0. The
  only public way to make a formula is a `MarkdownText` with a formula run,
  which is route (b).
- **imtypeset** has `Paragraph(text, runs, flags)`, with a `DisplayFormula` run,
  `layout(w, style)`, `draw(draw_list, pos)` and `formulas()`, which gives
  `refused/reason/pos`. The probe drew one in a Hosted widget (`settled-c.png`).
  It renders the same as (a). Its median is 0.670 ms a frame and its first frame
  3.63 ms. On a refusal it shows the source, and the reason is readable.
  Against it:
  - pptmstr would import imtypeset directly. Today no module outside
    `pptmstr/ui/typeset` imports even imscribe.
  - The size and ink come from an `imtypeset.Style` pptmstr would have to keep
    in step with the theme. The probe used the default size.
  - There is no selection and no copy beyond `Hosted.text`.

  Its real use is the one route (b) already makes of it: **asking whether a
  formula will be set, and why not.**

## The subset, as measured

86 sources, each asked as one display formula (`subset` in `results.json`,
with every reason). For a system prompt:

**Write:**
- `x_i`, `x^2`, `x_i^{n+1}`, `f'(x)`
- `\frac{a}{b}`, `\binom{n}{k}`, `\sqrt{x}`
- `\sum`, `\prod`, `\int`, `\iint`, `\oint`, `\lim`, each with `_{…}^{…}`,
  and `\limits` / `\nolimits`
- `\left( … \right)` with `( ) [ ] \{ \} | \langle \rangle`, and `\left.` /
  `\right|`
- `\overline{…}`, `\underline{…}`
- Greek: `\alpha … \omega`, `\varepsilon`, `\varphi`, `\Gamma … \Omega`
- relations: `\le \ge \ne \approx \equiv \sim \propto \in \notin \subset
  \subseteq`
- arrows: `\to \Rightarrow \iff \mapsto \leftarrow \longrightarrow`
- operators: `\cdot \times \pm \div \circ \oplus \otimes \ast \star \cup \cap`
- logic and symbols: `\forall \exists \neg \land \lor \infty \partial \nabla
  \hbar \ell \angle \perp \parallel \emptyset \mid \colon`, and `^\circ`
- dots: `\ldots \cdots \dots \vdots \ddots`
- spaces: `\, \: \; \quad \qquad \!`
- text and letters: `\text{…}`, `\textbf{…}`, `\mathrm{…}`,
  `\operatorname{…}`, `\mathbb{…}`, `\mathbf{…}`, `\mathcal{…}`
- `\sin \cos \log \exp \max \min`
- escaped characters: `\% \# \_ \& \$`
- Unicode letters and operators typed directly (`α`, `∑`)

One formula per fence. Lines inside a fence are joined into one line.

**Avoid (refused, and shown as the fence with the reason):**
- environments and alignment: `\begin{…}` (`aligned`, `cases`, `pmatrix`),
  `\matrix`, `\\`, `&`, `\tag`, `\label`
- accents: `\hat \vec \bar \tilde \dot`
- style and font switches: `\displaystyle \dfrac \tfrac \mathit \boldsymbol
  \mathsf \mathtt \color`
- sized delimiters: `\big`, `\Bigg` (use `\left…\right`)
- `\sqrt[n]{…}`
- infix fractions: `\over \choose`
- macros: `\newcommand \def`
- `\overbrace \stackrel \pmod \bmod \varnothing \operatorname*`
- anything nested very deeply under one `\sqrt`. The battery's three-level
  fraction under a root was refused for its height at the default size

## Not run

- The `refuses=` hook of `parse_markdown`. It is the other place an ask could
  hook in. It writes `$$source$$` in the code face, not the warn-line form.
- An ask at the document's pixel size, and `Document.block_error` after a draw.
- Copy-as-source (`selected_source`) for a spliced block in a stream.
- A change of type size (the epoch). Only the palette and the width were
  changed.
- A real pointer selection. Selection was driven through `select_all`.
- Any display density but 1.
- Timing on a GPU. Every number is llvmpipe on the CPU.
