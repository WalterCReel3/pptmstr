#!/usr/bin/env python3
"""
Probe math-probe: how imscribe 0.2.0 can set a formula from a ```` ```math ````
fence in pptmstr's views, by three routes, measured in a real GL window.

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/math_probe.py

Writes ``shots/math-probe/*.png`` and ``shots/math-probe/results.json`` and prints
each measurement. The write-up is notes/vector-rendering/probe-math.md.

**Nothing in ``pptmstr/`` is changed.** Each route is put into pptmstr's real
seam for the run only, by replacing names on its modules from here:

- ``figures.host.is_figure_fence`` also answers for ``math``, so that
  ``views._Captioned`` computes "settled" for a math fence exactly as it does
  for an svg one (committed, or the stream closed);
- ``views._captioned`` turns a settled math fence into the route's block;
- ``views._Hosts.__call__`` answers a marked math block with the route's widget.

Routes:

- ``none``  pptmstr as it is: the fence is code.
- ``b``     the fence becomes a display-formula ``MarkdownText`` block from
            ``parse_markdown("$$\\n<body>\\n$$", math=True)``, spliced into the
            document; a body the library would refuse (asked first through an
            ``imtypeset.Paragraph``) stays code under a warn line with the reason.
- ``b_raw`` the same with no asking first: what the library's own refusal does
            to a pptmstr view.
- ``a``     the fence becomes a Hosted widget holding its own ``TypesetView``
            of that one block.
- ``c``     the fence becomes a Hosted widget drawing an ``imtypeset.Paragraph``.
"""

from __future__ import annotations

import inspect
import json
import statistics
import sys
import textwrap
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402

OUT = ROOT / "shots" / "math-probe"
DOC_POS = (20.0, 20.0)

DOCUMENT = r"""# Mathematics from a fence

A shell line stays text: export $PATH=$HOME/bin, and so do prices of $5 and $10.
Inline TeX in prose stays text as well: $e^{i\pi}+1=0$ and $$x^2$$ here.

```math
\sum_{i=1}^{n} i = \frac{n(n+1)}{2}
```

A multi-line body, read as one formula:

```math
\mathbb{E}[X]
  = \sum_{k} k \, P(X = k)
  \le \sqrt{\operatorname{Var}(X) + \mu^2}
```

```math
\left( \int_0^1 f(x) \, dx \right)^2 \le \int_0^1 f(x)^2 \, dx
```

An aligned body, which the subset refuses:

```math
\begin{aligned} a &= b + c \\ d &= e \end{aligned}
```

After the refused one, $HOME is still text. A code fence is untouched:

```python
price = "$$not math$$"
```
"""

# One formula wider than a narrow pane: what the measure does to it.
LONG_FORMULA = (
    "```math\n"
    + " + ".join(f"a_{{{i}}}" for i in range(1, 16))
    + r" = \sum_{i=1}^{15} a_i"
    + "\n```\n"
)

STREAM_DOC = r"""Streaming: $PATH stays.

```math
\frac{a}{b} + \sqrt{x}
```

Text after the fence.

```math
\binom{n}{k} = \frac{n!}{k!\,(n-k)!}
```"""

# The TeX battery: (label, source). Each is asked of an imtypeset.Paragraph as one
# display formula, as route b would ask before splicing.
BATTERY: list[tuple[str, str]] = [
    ("superscript, subscript", r"x_i^2 + y^{n+1}"),
    ("prime", r"f'(x) + g''"),
    ("frac", r"\frac{a}{b}"),
    ("nested frac", r"\frac{1}{1 + \frac{1}{x}}"),
    ("binom", r"\binom{n}{k}"),
    ("sqrt", r"\sqrt{x^2 + 1}"),
    ("sqrt of a frac", r"\sqrt{\frac{a}{b}}"),
    ("sqrt with degree", r"\sqrt[3]{x}"),
    ("sum with limits", r"\sum_{i=1}^{n} i"),
    ("prod", r"\prod_{k=1}^{n} k"),
    ("int", r"\int_0^1 f(x)\,dx"),
    ("iint, oint", r"\iint_D f \, dA + \oint_C F"),
    ("lim", r"\lim_{x \to 0} \frac{\sin x}{x}"),
    ("limits/nolimits", r"\int\limits_0^1 x + \sum\nolimits_i x_i"),
    ("overline, underline", r"\overline{z} + \underline{w}"),
    ("left/right parens", r"\left( \frac{a}{b} \right)"),
    ("left/right brackets", r"\left[ x \right]"),
    ("left/right braces", r"\left\{ x \right\}"),
    ("left/right bars", r"\left| x \right|"),
    ("left/right angle", r"\left\langle x \right\rangle"),
    ("left. right|", r"\left. \frac{df}{dx} \right|_{x=0}"),
    ("bare delimiters", r"\{ x \} + |x| + \|x\| + \langle x \rangle"),
    ("lvert, lbrace", r"\lvert x \rvert + \lbrace x \rbrace"),
    (
        "lowercase greek",
        r"\alpha \beta \gamma \delta \epsilon \varepsilon \theta \lambda"
        r" \mu \pi \sigma \phi \varphi \omega",
    ),
    ("uppercase greek", r"\Gamma \Delta \Theta \Lambda \Pi \Sigma \Phi \Psi \Omega"),
    ("relations", r"a \le b \ge c \ne d \approx e \equiv f \sim g \propto h"),
    ("arrows", r"a \to b \Rightarrow c \iff d \mapsto e \leftarrow f \longrightarrow g"),
    ("sets", r"x \in A \notin B \subset C \subseteq D \cup E \cap F \emptyset"),
    ("varnothing", r"\varnothing"),
    ("logic", r"\forall x \exists y \neg p \land q \lor r"),
    ("binary ops", r"a \cdot b \times c \pm d \div e \circ f \oplus g \otimes h \ast i \star j"),
    ("misc symbols", r"\infty \partial \nabla \hbar \ell \prime \angle \perp \parallel"),
    ("dots", r"1, \ldots, n + \cdots + \dots"),
    ("vdots, ddots", r"\vdots \ddots"),
    ("text", r"x = 1 \text{ if } y > 0"),
    ("mathrm", r"\mathrm{d}x"),
    ("operatorname", r"\operatorname{argmax}_x f(x)"),
    ("named functions", r"\sin x + \cos y + \log z + \exp w + \max_i a_i + \min b"),
    ("mathbb", r"\mathbb{R}^n \mathbb{N}"),
    ("mathbf", r"\mathbf{v} \cdot \mathbf{w}"),
    ("mathcal", r"\mathcal{L}(\theta)"),
    ("explicit spaces", r"a \, b \: c \; d \quad e \qquad f \! g"),
    ("mid, colon", r"\{ x \mid x > 0 \} \colon"),
    ("braced script", r"{}^{14}C"),
    ("escaped chars", r"\% \# \_ \& \$"),
    ("unicode letters", "α + β = γ"),
    ("unicode operator", "∑ x"),
    ("degree", r"90^\circ"),
    ("pmod", r"a \equiv b \pmod{n}"),
    ("bmod", r"a \bmod n"),
    ("accent hat", r"\hat{x}"),
    ("accent vec", r"\vec{v}"),
    ("accent bar", r"\bar{x}"),
    ("accent tilde, dot", r"\tilde{x} + \dot{x}"),
    ("displaystyle", r"\displaystyle \frac{a}{b}"),
    ("mathit", r"\mathit{diff}"),
    ("boldsymbol", r"\boldsymbol{x}"),
    ("mathsf, mathtt", r"\mathsf{A} \mathtt{B}"),
    ("textbf", r"\textbf{x}"),
    ("color", r"\color{red}{x}"),
    ("big delimiters", r"\big( x \big)"),
    ("Bigg", r"\Bigg( x \Bigg)"),
    ("line break \\\\", r"a = b \\ c = d"),
    ("ampersand", r"a &= b"),
    ("aligned", r"\begin{aligned} a &= b \\ c &= d \end{aligned}"),
    ("cases", r"f(x) = \begin{cases} 1 & x > 0 \\ 0 & \text{else} \end{cases}"),
    ("pmatrix", r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}"),
    ("matrix (plain)", r"\matrix{a & b \cr c & d}"),
    ("over (infix)", r"{a \over b}"),
    ("choose (infix)", r"{n \choose k}"),
    ("newcommand", r"\newcommand{\R}{\mathbb{R}} \R"),
    ("def", r"\def\x{y} \x"),
    ("tag", r"x = 1 \tag{1}"),
    ("label", r"x = 1 \label{eq:x}"),
    ("operatorname*", r"\operatorname*{argmax}_x f"),
    ("frac shorthand", r"\frac12"),
    ("sqrt shorthand", r"\sqrt2"),
    ("dfrac, tfrac", r"\dfrac{a}{b} + \tfrac{c}{d}"),
    ("overbrace", r"\overbrace{a+b}^{n}"),
    ("stackrel", r"\stackrel{def}{=}"),
    ("unknown command", r"\foo{x}"),
    ("unbalanced brace", r"\frac{a}{b"),
    ("double superscript", r"x^2^3"),
    ("empty", r" "),
    ("tall radicand", r"\sqrt{\frac{\frac{\frac{a}{b}}{c}}{\frac{d}{\frac{e}{f}}}}"),
    ("tall left/right", r"\left( \frac{\frac{\frac{a}{b}}{c}}{\frac{d}{\frac{e}{f}}} \right)"),
]


class MathMark(str):
    """
    A math fence's mark for routes a and c: empty, as ``FigureMark`` is, with
    the body and the fence as written.
    """

    body: str
    fence: str

    def __new__(cls, body: str, fence: str) -> MathMark:
        made = super().__new__(cls, "")
        made.body = body
        made.fence = fence
        return made


STATE: dict[str, Any] = {"route": "none"}
RESULTS: dict[str, Any] = {}


def say(key: str, value: Any) -> None:
    RESULTS[key] = value
    print(f"{key}: {json.dumps(value, default=str)}", flush=True)


# -- asking the library whether it would set a formula ------------------------------


def ask(source: str, display: bool = True) -> tuple[bool, str, int]:
    """
    Whether imtypeset sets ``source`` as one formula: (set, reason, position).
    One Paragraph, one display run, laid out at 600 px with the default style.
    """
    import imtypeset

    flags = imtypeset.RunFlags.DisplayFormula if display else imtypeset.RunFlags.Formula
    with imtypeset.Paragraph() as p:
        p.set_text(source, [imtypeset.Run(0, len(source), flags=int(flags))])
        p.layout(600.0)
        found = p.formulas()
        error = p.error
    if not found:
        return False, f"no formula found (error {error.code}: {error.message})", -1
    f = found[0]
    return (not f.refused), f.reason, f.pos


# -- route b: a display-formula block ----------------------------------------------


def formula_block(body: str, theme: Any) -> Any | None:
    """
    The display-formula block of a fence body, or ``None`` when the body is not
    read as exactly one closed display formula (a ``$$`` line inside it).
    """
    from imscribe import MarkdownText, parse_markdown

    blocks = parse_markdown(f"$$\n{body}\n$$", theme, references=False, math=True).blocks
    if len(blocks) != 1 or not isinstance(blocks[0], MarkdownText):
        return None
    formulas = blocks[0].formulas
    if len(formulas) != 1 or formulas[0].state != "set":
        return None
    return blocks[0]


def is_math(said: str) -> bool:
    words = said.split(maxsplit=1)
    return bool(words) and words[0].lower() == "math"


def _replace(owner: Any, name: str, value: Any) -> None:
    """Replace a name on a class of pptmstr's for this process: the probe's patches."""
    setattr(owner, name, value)


def install() -> None:
    """Put the routes into pptmstr's seam for this process."""
    from pptmstr.figures import host as figures_host
    from pptmstr.ui.typeset import themes, views

    original_is_figure = figures_host.is_figure_fence
    original_captioned = views._captioned
    original_hosts_call = views._Hosts.__call__

    def is_figure_fence(said: str) -> bool:
        if STATE["route"] != "none" and is_math(said):
            return True
        return original_is_figure(said)

    def _captioned(
        blocks: Any, hosting: bool = False, *, plain: bool = False, figures: bool = False
    ) -> tuple[Any, ...]:
        route = STATE["route"]
        out: list[Any] = []
        for block in blocks:
            said = getattr(block, "language", "")
            if route == "none" or not figures or not said or not is_math(said):
                out += original_captioned((block,), hosting, plain=plain, figures=figures)
                continue
            fence = f"```{said}\n{block.text}\n```"
            if route in ("b", "b_raw"):
                made = formula_block(block.text, themes.document_theme())
                reason = None
                if made is None:
                    reason = "the body is not one display formula"
                elif route == "b":
                    ok, why, _ = ask(made.formulas[0].source)
                    reason = None if ok else why
                if reason is None:
                    out.append(made)
                    STATE.setdefault("spliced", 0)
                    STATE["spliced"] += 1
                    continue
                why = f"math not set: {reason}"
                out.append(replace(block, text=why, language=views._Warned(len(why))))
                out.append(replace(block, text=f"{said}\n{block.text}"))
                continue
            out.append(replace(block, language=MathMark(block.text, fence)))
        return tuple(out)

    def hosts_call(self: Any, view: Any, number: int, block: Any) -> Any:
        said = getattr(block, "language", None)
        if isinstance(said, MathMark):
            was = view.hosted(number) if hasattr(view, "hosted") else None
            kind = NestedView if STATE["route"] == "a" else ParagraphWidget
            if isinstance(was, kind) and was.mark is said:
                return was
            return kind(said)
        return original_hosts_call(self, view, number, block)

    figures_host.is_figure_fence = is_figure_fence
    views._captioned = _captioned
    _replace(views._Hosts, "__call__", hosts_call)
    STATE["original_blocks"] = views._Captioned.__dict__["blocks"]


def patch_own_line() -> str:
    """
    ``_Captioned.blocks`` reads ``answer[0].language`` of each fence's answer, and
    a ``MarkdownText`` has none. The same property with that one read made a
    ``getattr``, compiled from its own source, so route b can be measured in a
    stream past it. Answers the line replaced.
    """
    from pptmstr.ui.typeset import views

    source = textwrap.dedent(inspect.getsource(views._Captioned.__dict__["blocks"].fget))
    old = "answer[0].language != _HOSTED_INFO"
    assert old in source, "the line moved"
    source = source.replace("@property\n", "").replace(
        old, 'getattr(answer[0], "language", None) != _HOSTED_INFO'
    )
    space: dict[str, Any] = {}
    exec(compile(source, "<patched _Captioned.blocks>", "exec"), views.__dict__, space)
    _replace(views._Captioned, "blocks", property(space["blocks"]))
    return old


# -- route a: a hosted TypesetView ----------------------------------------------------


class NestedView:
    """A Hosted widget holding a TypesetView of ``$$<body>$$`` with math on."""

    def __init__(self, mark: MathMark) -> None:
        from imscribe import TypesetView

        from pptmstr.ui.typeset import themes

        self.mark = mark
        self.text = mark.fence
        STATE.setdefault("nested_made", 0)
        STATE["nested_made"] += 1
        self.view = TypesetView(theme=themes.document_theme(), palette=themes.palette())
        block = formula_block(mark.body, themes.document_theme())
        self.ok = block is not None
        self.refusal: str | None = None
        if block is not None:
            set_, why, _ = ask(block.formulas[0].source)
            if not set_:
                self.refusal = why
        self.view.set_document((block,) if block is not None else mark.fence)
        self.heights: list[tuple[float, float]] = []

    def height_for(self, width_px: float) -> float:
        h = float(self.view.layout_for(width_px))
        self.heights.append((width_px, h))
        return h

    def draw(self, *, palette: Any = None, width_px: float | None = None) -> object:
        whole = self.view.draw(palette=palette, width_px=width_px)
        STATE["nested_whole"] = bool(whole)
        return True


# -- route c: a hosted imtypeset.Paragraph -------------------------------------------


class ParagraphWidget:
    """A Hosted widget drawing one imtypeset.Paragraph holding a display formula."""

    def __init__(self, mark: MathMark) -> None:
        import imtypeset

        self.mark = mark
        self.text = mark.fence
        source = " ".join(mark.body.split())
        self.p = imtypeset.Paragraph()
        flags = int(imtypeset.RunFlags.DisplayFormula)
        self.p.set_text(source, [imtypeset.Run(0, len(source), flags=flags)])
        self.width = -1.0

    def _style(self, palette: Any) -> Any:
        import imtypeset

        from pptmstr.ui.typeset import themes

        # Default style but for alignment and ink: the face size is the
        # library's default, not the document's (route c has no theme of its own).
        style = imtypeset.Style()
        style.Align = int(imtypeset.Align.Center)
        style.Color = int((palette or themes.palette()).ink_for(theme_kind()))
        return style

    def height_for(self, width_px: float) -> float:
        if width_px != self.width:
            self.p.layout(width_px, self._style(None))
            self.width = width_px
        return float(self.p.size[1])

    def draw(self, *, palette: Any = None, width_px: float | None = None) -> object:
        from imgui_bundle import imgui

        at = imgui.get_cursor_screen_pos()
        self.p.layout(float(width_px or 600), self._style(palette))
        self.width = float(width_px or 600)
        self.p.draw(None, (at.x, at.y))
        f = self.p.formulas()
        STATE["paragraph_refused"] = [(x.refused, x.reason) for x in f]
        imgui.dummy(imgui.ImVec2(float(width_px or 600), float(self.p.size[1])))
        return True


def theme_kind() -> Any:
    from imscribe import BlockKind

    return BlockKind.PARAGRAPH


# -- GL helpers -----------------------------------------------------------------------


def grab() -> np.ndarray:
    from imgui_bundle import imgui
    from OpenGL import GL

    io = imgui.get_io()
    width = int(io.display_size.x * io.display_framebuffer_scale.x)
    height = int(io.display_size.y * io.display_framebuffer_scale.y)
    GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
    GL.glReadBuffer(GL.GL_BACK)
    data = GL.glReadPixels(0, 0, width, height, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
    raw = data if isinstance(data, bytes) else bytes(data)
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3)[::-1].copy()


def save(path: Path, rgb: np.ndarray) -> None:
    import screenshot

    rgb = np.ascontiguousarray(rgb[..., :3])
    path.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_png(path, rgb.shape[1], rgb.shape[0], [row.tobytes() for row in rgb])


# -- inspecting what a view holds ------------------------------------------------------


def classify(view: Any) -> list[str]:
    """One word per block of a library view: what it is now."""
    from imscribe import MarkdownCode, MarkdownText

    if view is None:
        return ["<no library view: the entry is ImGui's>"]
    hosted = set(view.hosts)
    out = []
    for n, block in enumerate(view.blocks):
        said = getattr(block, "language", "")
        if n in hosted:
            out.append(f"hosted:{type(view.hosted(n)).__name__}")
        elif isinstance(block, MarkdownText) and any(
            f.display and f.state == "set" for f in block.formulas
        ):
            out.append("formula")
        elif isinstance(block, MarkdownCode):
            first = block.text.partition("\n")[0]
            out.append(
                "warn"
                if isinstance(said, str) and said == "" and first.startswith("math not set")
                else f"code:{first[:12]}"
            )
        else:
            out.append(type(block).__name__)
    return out


def _raises(call: Any) -> str | None:
    try:
        call()
    except Exception as exc:  # noqa: BLE001 -- what it raises is the measurement
        return f"{type(exc).__name__}: {exc}"
    return None


def settled_view(cache: Any, key: str) -> Any:
    entry = cache._entries.get(key)
    if entry is None or not entry.segments:
        return None
    return entry.segments[0].view


# -- the run --------------------------------------------------------------------------


def main() -> int:
    from imgui_bundle import hello_imgui, imgui, immapp

    from pptmstr import theme
    from pptmstr.figures import host as figure_host
    from pptmstr.ui import typeset
    from pptmstr.ui.typeset import views

    OUT.mkdir(parents=True, exist_ok=True)
    install()
    ui: dict[str, Any] = {
        "mode": "settled",
        "doc": DOCUMENT,
        "width": 900.0,
        "height": 900.0,
        "grab": None,
        "done": False,
        "key": "doc",
        "time": None,
        "stream": None,
        "error": None,
    }
    cache_holder: dict[str, Any] = {"cache": typeset.ViewCache()}

    def frames(n: int) -> Iterator[None]:
        for _ in range(n):
            yield None

    def grab_as(name: str) -> Iterator[None]:
        ui["grab"] = name
        yield None
        while ui["grab"] is not None:
            yield None

    def fresh() -> None:
        cache_holder["cache"] = typeset.ViewCache()

    def view() -> Any:
        return settled_view(cache_holder["cache"], ui["key"])

    def dollar_check(v: Any) -> dict[str, Any]:
        texts = [str(t) for t in v.texts]
        joined = "\n".join(texts)
        return {
            "path_line_kept": "$PATH=$HOME/bin" in joined,
            "prices_kept": "$5 and $10" in joined,
            "inline_tex_kept": "$e^{i\\pi}+1=0$" in joined,
            "double_dollar_prose_kept": "$$x^2$$" in joined,
            "home_after_kept": "$HOME is still text" in joined,
            "python_fence_kept": '"$$not math$$"' in joined,
        }

    def settled_route(route: str, shot: str) -> Iterator[None]:
        STATE["route"] = route
        STATE["spliced"] = 0
        fresh()
        ui.update(mode="settled", doc=DOCUMENT, key="doc", width=900.0)
        yield from frames(6)
        v = view()
        entry = cache_holder["cache"]._entries["doc"]
        say(
            f"{route}_settled",
            {
                "blocks": classify(v),
                "whole": [bool(s.whole) for s in entry.segments],
                "unset": entry.unset,
                "dollars": dollar_check(v) if v is not None else None,
                "segment_count": len(entry.segments),
                "spliced": STATE.get("spliced"),
                "draw_error": ui["error"],
            },
        )
        yield from grab_as(f"{shot}")
        # The frame after: has the entry gone to ImGui as written?
        yield from frames(3)
        entry = cache_holder["cache"]._entries["doc"]
        say(
            f"{route}_settled_after",
            {
                "segments": len(entry.segments),
                "segment_views": [s.view is not None for s in entry.segments],
                "unset": entry.unset,
                "written": len(getattr(entry, "written", ()) or ()),
            },
        )
        if route == "b_raw":
            yield from grab_as(f"{shot}-after")

    def selection(route: str) -> Iterator[None]:
        v = view()
        if v is None:
            return
        v.select_all()
        yield from frames(2)
        text = v.selected_text()
        out: dict[str, Any] = {
            "parent_copy_has_fence_source": "```math\n\\sum_{i=1}^{n} i" in text,
            "parent_copy_has_formula_source": "\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}" in text,
            "parent_copy_excerpt": text[text.find("Mathematics") : text.find("Mathematics") + 0]
            + text[text.find("prose stays") + 0 : text.find("multi-line")],
        }
        if route == "a":
            nested = [v.hosted(n) for n in v.hosts]
            nested = [n for n in nested if isinstance(n, NestedView)]
            if nested:
                n0 = nested[0]
                n0.view.select_all()
                yield from frames(2)
                out["nested_selected"] = n0.view.selected_text()
                out["parent_selection_after_nested_select"] = v.selection
                out["parent_owns_after"] = v.owns_selection
                out["nested_owns_after"] = n0.view.owns_selection
                out["nested_heights_seen"] = n0.heights[:3] + n0.heights[-2:]
        v.clear_selection()
        say(f"{route}_selection", out)

    def refusal_reason(route: str) -> None:
        v = view()
        if v is None:
            return
        hosted = [v.hosted(n) for n in v.hosts]
        doc = v.native_document
        errors = []
        for n in range(len(v.blocks)):
            e = doc.block_error(n)
            if e.code:
                errors.append((n, int(e.code), e.message))
        say(
            f"{route}_refusal",
            {
                "document_block_errors": errors,
                "nested_refusals": [h.refusal for h in hosted if isinstance(h, NestedView)],
                "nested_whole_last": STATE.get("nested_whole"),
                "paragraph_refused": STATE.get("paragraph_refused"),
                "warn_lines": [t for t in map(str, v.texts) if t.startswith("math not set")],
            },
        )

    def streaming(route: str) -> Iterator[None]:
        STATE["route"] = route
        fresh()
        ui.update(mode="stream", key="s", width=900.0)
        s = typeset.stream(cache_holder["cache"], "s")
        ui["stream"] = s
        seen: list[tuple[int, str, list[str]]] = []
        last: list[str] | None = None
        step = 5
        for at in range(0, len(STREAM_DOC), step):
            s.append(STREAM_DOC[at : at + step])
            yield from frames(1)
            v = s.view
            if v is None:
                continue
            now = classify(v)
            if now != last:
                tail = STREAM_DOC[: at + step][-24:]
                seen.append((at + step, tail, now))
                last = now
        yield from frames(1)
        yield from grab_as(f"stream-{route}-open")
        s.close()
        yield from frames(3)
        v = s.view
        seen.append((len(STREAM_DOC), "<closed>", classify(v)))
        say(f"{route}_stream", {"transitions": seen, "error": ui["error"]})
        yield from grab_as(f"stream-{route}-closed")
        ui["stream"] = None

    def cost(route: str, n: int = 20) -> Iterator[None]:
        STATE["route"] = route
        fresh()
        fences = "\n\n".join(
            f"Paragraph {i}.\n\n```math\n\\sum_{{i=1}}^{{{i}}} \\frac{{x_i}}{{1+x_i^2}}\n```"
            for i in range(n)
        )
        ui.update(mode="settled", doc=fences, key=f"cost-{route}", width=900.0, height=4000.0)
        ui["times"] = []
        yield from frames(1)
        first = ui["times"][:1]
        yield from frames(80)
        times = ui["times"][20:]
        say(
            f"{route}_cost_{n}_fences_ms",
            {
                "first_frame": round(first[0], 2) if first else None,
                "median": round(statistics.median(times), 3),
                "p90": round(sorted(times)[int(len(times) * 0.9)], 3),
                "max": round(max(times), 3),
                "nested_views_made": STATE.get("nested_made"),
            },
        )
        ui["height"] = 900.0

    def scenario() -> Iterator[None]:
        yield from frames(4)
        say("imscribe", __import__("imscribe").__version__)
        say("can_draw", typeset.can_draw())

        # The subset.
        subset = []
        for label, src in BATTERY:
            ok, why, pos = ask(src)
            subset.append({"label": label, "source": src, "set": ok, "reason": why, "pos": pos})
        say("subset", subset)
        inline_ok = ask(r"\frac{a}{b}", display=False)
        say("inline_frac", inline_ok)

        # Baseline and each route, settled.
        for route, shot in (
            ("none", "settled-none"),
            ("b", "settled-b"),
            ("b_raw", "settled-b-raw"),
            ("a", "settled-a"),
            ("c", "settled-c"),
        ):
            yield from settled_route(route, shot)
            if route in ("b", "a", "c"):
                yield from selection(route)
                refusal_reason(route)

        # Theme switch and width, route b and a.
        for route in ("b", "a"):
            STATE["route"] = route
            fresh()
            ui.update(mode="settled", doc=DOCUMENT + "\n" + LONG_FORMULA, key="doc", width=900.0)
            yield from frames(4)
            theme.set_theme("light")
            yield from frames(4)
            yield from grab_as(f"light-{route}")
            theme.set_theme("dark")
            yield from frames(2)
            ui["width"] = 380.0
            yield from frames(4)
            v = view()
            yield from grab_as(f"narrow-{route}")
            say(
                f"{route}_narrow",
                {
                    "content_width": float(v.content_width) if v is not None else None,
                    "blocks": classify(v) if v is not None else None,
                },
            )
            ui["width"] = 900.0
            yield from frames(2)

        # Streaming, with _Captioned.blocks reading a MarkdownText's language by getattr.
        replaced = patch_own_line()
        say("patched_line", replaced)
        ui["error"] = None
        for route in ("none", "b", "a"):
            yield from streaming(route)

        # Cost per frame.
        for route in ("none", "b", "a", "c"):
            yield from cost(route)

        # Last, because it may stop the gate: route b in a stream with
        # _Captioned.blocks as pptmstr has it.
        from pptmstr.ui.typeset import gate

        _replace(views._Captioned, "blocks", STATE["original_blocks"])
        STATE["route"] = "b"
        fresh()
        ui.update(mode="stream", key="s0")
        s0 = typeset.stream(cache_holder["cache"], "s0")
        ui["stream"] = s0
        s0.append("```math\nx^2\n```\n\nafter\n")
        ui["error"] = None
        say(
            "gate_before_unpatched", {"can_draw": typeset.can_draw(), "failure": gate._GATE.failure}
        )
        yield from frames(1)
        entry = cache_holder["cache"]._entries["s0"]
        say(
            "b_stream_unpatched",
            {
                "raised_out_of_draw": ui["error"],
                "stopping": gate._GATE.stopping,
                "missing_lines": entry.missing,
                "blocks_the_view_holds": classify(s0.view),
                "captioned_blocks_raises": _raises(
                    lambda: views._Captioned.__dict__["blocks"].fget(s0._captioned)
                ),
            },
        )
        yield from frames(2)
        say(
            "gate_after_unpatched",
            {
                "can_draw": typeset.can_draw(),
                "failure": gate._GATE.failure,
                "reason": typeset.unavailable_reason(),
            },
        )
        yield from grab_as("stream-b-unpatched")
        ui["stream"] = None

        (OUT / "results.json").write_text(json.dumps(RESULTS, indent=2, default=str))
        ui["done"] = True
        yield None

    steps = scenario()

    def gui() -> None:
        viewport = imgui.get_main_viewport()
        imgui.set_next_window_pos(viewport.work_pos)
        imgui.set_next_window_size(viewport.work_size)
        flags = imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_move.value
        imgui.begin("math_probe", None, flags)
        imgui.set_cursor_screen_pos(imgui.ImVec2(*DOC_POS))
        imgui.begin_child("doc", imgui.ImVec2(float(ui["width"]) + 20, float(ui["height"])), 1)
        t0 = time.perf_counter()
        try:
            if ui["mode"] == "stream":
                if ui["stream"] is not None:
                    ui["stream"].draw()
            else:
                typeset.markdown(
                    cache_holder["cache"], ui["key"], ui["doc"], width=float(ui["width"])
                )
        except Exception as exc:  # noqa: BLE001 -- a probe records what a route raises
            ui["error"] = f"{type(exc).__name__}: {exc}"
        ui.setdefault("times", []).append((time.perf_counter() - t0) * 1000)
        imgui.end_child()
        imgui.end()

    def pre_frame() -> None:
        typeset.pre_frame()
        figure_host.pump()
        if ui["done"]:
            hello_imgui.get_runner_params().app_shall_exit = True
            return
        if ui["grab"] is None:
            try:
                next(steps)
            except StopIteration:
                ui["done"] = True

    def before_swap() -> None:
        name = ui["grab"]
        if name is None:
            return
        save(OUT / f"{name}.png", grab())
        ui["grab"] = None

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "math_probe"
    params.app_window_params.window_geometry.size = (1000, 980)
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.ini_disable = True
    params.fps_idling.enable_idling = False
    params.callbacks.load_additional_fonts = theme.load_fonts
    params.callbacks.pre_new_frame = pre_frame
    params.callbacks.show_gui = gui
    params.callbacks.before_swap = before_swap
    theme.set_theme("dark")
    typeset.configure(typeset=True, mono=False)
    immapp.run(params)
    _ = views
    return 0 if ui["done"] else 1


if __name__ == "__main__":
    sys.exit(main())
