"""
The fence languages a reply pane draws as something other than code, as one table
(planning/2026-10-18-vector-displays-beside-typeset-text.md §8 slice 4).

Two readers, and the reason there is one table: **the views' dispatcher** decides
by it which fences settle into a figure or a formula (``views.settles_rendered``,
``views._captioned``, ``figures.host.is_figure_fence``), and **the session prompt**
tells Claude by it what will be drawn (``prompt``, appended by
``driver.AgentSession._system_prompt``). A language added to one and not the other
would have Claude writing fences that show as code, or never writing ones that
would draw.

A row whose renderer is not installed is left out of the prompt
(``available``) and stays in the dispatcher, which is what turns such a fence into
code with a reason. Installed is read from distribution metadata, never by
importing the library: a mermaid row asks ``mermaid.version()``, the same read
``figures.host.mark`` refuses a fence by, so the two cannot answer differently in
one process. That read and the others together cost about 5 ms on a cold
interpreter (``importlib.metadata`` over seven distributions, measured
2026-10-21); ``import merman`` alone is about 19 ms, and is not done here.

``prompt`` itself is pure: it takes the rows to describe.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import metadata
from typing import Literal

from . import render

Kind = Literal["figure", "formula"]


@dataclass(frozen=True, slots=True)
class Fence:
    """
    One fence language that is drawn once it has settled.

    ``word`` is the info string's first word, compared case-folded. ``kind`` is
    which route draws it: a ``figure`` is hosted (``figures.host``), a
    ``formula`` is spliced into the document (``views._formula_block``).
    ``needs`` are the distributions its route imports beyond those every row
    needs (``_TYPESET``). ``says`` is what the prompt tells Claude about it.
    """

    word: str
    kind: Kind
    needs: tuple[str, ...]
    says: str


# Every fence is drawn inside a typeset view, so no row draws without these.
_TYPESET = ("imscribe", "imtypeset")
# What any figure imports, read from the module that imports it.
_RASTER = render.DISTRIBUTIONS

_SVG = (
    "```svg: one SVG image. It must be self-contained: no <script>, no event "
    "attributes, no <image>, no <foreignObject>, no filters or patterns, and no "
    'reference outside the document (url(#id) and href="#id" only). Give width and '
    "height, or a viewBox. End every font-family with serif, sans-serif or monospace."
)
_MERMAID = (
    "```mermaid: one Mermaid diagram. Flowchart, sequence, class, state and ER "
    "diagrams are the kinds checked."
)
# The write/avoid lists of notes/vector-rendering/probe-math.md, trimmed to what a
# formula is most likely to reach for.
_MATH = (
    "```math: one display formula in TeX, without $ or $$ around it. Lines are "
    "joined into one, so there is no line break or alignment. Use: _ and ^, \\frac, "
    "\\binom, \\sqrt{x}, \\sum \\prod \\int \\lim with limits, \\left( ... \\right), "
    "\\overline, \\underline, Greek letters, the usual relations, arrows and "
    "operators, \\text{}, \\mathrm \\mathbf \\mathbb \\mathcal \\operatorname, "
    "\\sin \\cos \\log \\exp \\max \\min. Refused: \\begin{...} environments "
    "(aligned, cases, matrices), \\\\ and &, accents (\\hat \\vec \\bar \\tilde "
    "\\dot), \\displaystyle \\dfrac \\tfrac \\mathit \\boldsymbol \\color, \\big "
    "and friends (use \\left \\right), \\sqrt[n], \\over \\choose, \\newcommand "
    "\\def."
)

RENDERED: tuple[Fence, ...] = (
    Fence("svg", "figure", _RASTER, _SVG),
    Fence("mermaid", "figure", (*_RASTER, "merman"), _MERMAID),
    Fence("math", "formula", (), _MATH),
)
"""
Every fence language drawn as something other than code, in the order the prompt
lists them.
"""

FIGURE_WORDS = frozenset(row.word for row in RENDERED if row.kind == "figure")
FORMULA_WORDS = frozenset(row.word for row in RENDERED if row.kind == "formula")
WORDS = FIGURE_WORDS | FORMULA_WORDS

PROMPT_LIMIT = 2_000
"""
The most characters ``prompt`` may run to with every row in it: it rides on every
request of every session, so it is held short by a test rather than by care.
"""


def word(said: str) -> str:
    """
    A fence's info string's first word, lower-cased: what the table is keyed by.
    """
    words = said.split(maxsplit=1)
    return words[0].lower() if words else ""


def _installed(name: str) -> bool:
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        return False
    return True


def _merman() -> bool:
    from . import mermaid

    return mermaid.version() is not None


# A distribution whose presence is read the way its route reads it, where that is
# not a plain metadata read of its own.
_PROBES: dict[str, Callable[[], bool]] = {"merman": _merman}


def _present(name: str) -> bool:
    probe = _PROBES.get(name)
    return probe() if probe is not None else _installed(name)


def available(rows: Iterable[Fence] = RENDERED) -> tuple[Fence, ...]:
    """
    The rows whose renderer is installed, in table order. Reads metadata, imports
    nothing it checks.
    """
    return tuple(row for row in rows if all(_present(name) for name in (*_TYPESET, *row.needs)))


def prompt(rows: Iterable[Fence]) -> str:
    """
    What a session is told about the fences in ``rows``: those are drawn when
    pptmstr typesets replies, which the operator can switch off after the launch,
    no other fence is a figure or a formula, ``$`` in prose is not mathematics,
    and a fence that cannot be drawn shows its source and why. Empty when
    ``rows`` is: nothing is drawn, so there is nothing to tell.
    """
    rows = tuple(rows)
    if not rows:
        return ""
    names = ", ".join(f"```{row.word}" for row in rows)
    lines = [
        "## Fences that are drawn",
        "",
        f"When pptmstr typesets replies, the pane showing them draws {names} fences "
        "once each is closed; otherwise they show as written. No other fence is drawn "
        "as a figure or a formula.",
        "",
        *(f"- {row.says}" for row in rows),
        "",
    ]
    if any(row.kind == "formula" for row in rows):
        lines.append(
            "$...$ and $$...$$ in prose are not rendered and show as written; "
            "put mathematics in a ```math fence."
        )
    else:
        lines.append("$...$ and $$...$$ in prose are not rendered and show as written.")
    lines.append("A fence that cannot be drawn is shown as its source under a one-line reason.")
    lines += ["", *_guidance({row.word for row in rows}, {row.kind for row in rows})]
    return "\n".join(lines)


FIGURE_WIDTH = 600
"""
The widest a figure is told to be, in px. Figures are scaled down to the pane and
never up (``host.FigureWidget.size_for``), and replies are drawn in SESSION and CONTEXT:
SESSION is (1 - 0.21) * (1 - 0.26) of the window (``app._focus_layout``), 599 at
the 1024-wide smallest window and 877 at the 1500-wide default, so 600 is drawn
unscaled there at every supported size and 11 px text stays 11 px. CONTEXT, about
380 at the default, scales any useful figure down regardless.
"""


def _guidance(words: set[str], kinds: set[Kind]) -> list[str]:
    """
    How to use the rows, beyond what each is: written for the failure of a session
    that rasterised to files, hand-laid a graph Mermaid would have laid out, and
    took a skill's HTML colour conventions into an SVG. Mermaid is named only when
    its row is in ``rows``.
    """
    said = [
        "Write the fence; the pane is the display. Do not render to a file, look for "
        "a converter, or preview with other tools."
    ]
    if {"mermaid", "svg"} <= words:
        said.append(
            "For nodes and edges (dependencies, flows, sequences, states, entities) use "
            "```mermaid, which lays them out; ```svg is for what Mermaid cannot draw. "
            "Never compute a graph layout by hand."
        )
    if "figure" in kinds:
        said.append(
            "Figures sit on a near-white card, scaled down to fit the pane: design for "
            "white, with literal colours, text of 11 px or more, and at most about "
            f"{FIGURE_WIDTH} px wide."
        )
    said.append("These rules win over a skill's general advice.")
    return said


def session_prompt() -> str:
    """
    ``prompt`` for the rows installed in this process: what a launch appends.
    """
    return prompt(available())
