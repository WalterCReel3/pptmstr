"""
The fence languages a reply pane draws as something other than code, as one table
(planning/2026-10-18-vector-displays-beside-typeset-text.md §8 slice 4).

Three readers, and the reason there is one table: **the views' dispatcher** decides
by it which fences settle into a figure or a formula (``views.settles_rendered``,
``views._captioned``, ``figures.host.is_figure_fence``), **the session prompt**
tells Claude by it what will be drawn (``prompt``, appended by
``driver.AgentSession._system_prompt``), and **the packaged skill** tells Claude
how to write each one (``skill_text``, shipped as ``plugins.figures_skill``). A
language added to one and not the others would have Claude writing fences that
show as code, or never writing ones that would draw.

The prompt is a short floor on every request; the rules for each fence are in the
skill, which a session loads when it draws (planning record §10.5).

A row whose renderer is not installed is left out of the prompt
(``available``) and stays in the dispatcher, which is what turns such a fence into
code with a reason. Installed is read from distribution metadata, never by
importing the library in this process. A mermaid row asks more, because merman can
be installed and not import (a wheel for another glibc), and a row promised then
would send every graph to a fence that is always code: ``mermaid.import_probe``,
a fresh interpreter importing it once per process, about 50-80 ms off the frame
thread (measured 2026-10-21). ``figures.host.mark`` still accepts a fence by
``mermaid.version()`` alone, since it runs on the frame thread; where merman is
installed and does not import, the render child refuses that fence with
``mermaid.missing_reason``, so the fence is code under a reason either way, and
the prompt no longer asks for it. The metadata reads together cost about 5 ms on a
cold interpreter (seven distributions, measured 2026-10-21).

``available`` blocks for that probe on its first call, so it is asked at a launch,
on the session's thread, and never from a pane.

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

    return mermaid.import_probe()


# A distribution whose presence is read the way its route reads it, where that is
# not a plain metadata read of its own.
_PROBES: dict[str, Callable[[], bool]] = {"merman": _merman}


def _present(name: str) -> bool:
    probe = _PROBES.get(name)
    return probe() if probe is not None else _installed(name)


def available(rows: Iterable[Fence] = RENDERED) -> tuple[Fence, ...]:
    """
    The rows whose renderer is installed, in table order. Reads metadata, and for
    merman whether it imports (``_merman``); imports nothing it checks in this process.
    """
    return tuple(row for row in rows if all(_present(name) for name in (*_TYPESET, *row.needs)))


SKILL = "pptmstr:pptmstr-figures"
"""
The packaged skill that carries each row's rules (``skill_text``), as the floor
names it. Namespaced, because a project skill of the same bare name also loads
and a model asked for the bare name chose the project's (notes/vector-rendering/
probe-skills.md, L4). ``plugins.SKILLS`` is held to this by a test.
"""


def prompt(rows: Iterable[Fence], *, skill: bool = True) -> str:
    """
    The always-on floor for the fences in ``rows``: those are drawn when pptmstr
    typesets replies, which the operator can switch off after the launch, no other
    fence is a figure or a formula, ``$`` in prose is not mathematics, a fence that
    cannot be drawn shows its source and why, the pane is the display, and a graph
    goes to Mermaid where Mermaid is drawn. Empty when ``rows`` is: nothing is
    drawn, so there is nothing to tell.

    The rules each fence must follow are in the skill (``skill_text``), and
    ``skill`` is whether the reader can load it: a worker whose tool list has no
    ``Skill`` cannot, and is not told to.

    The Mermaid sentence is in the floor and not only in the skill: a hand-laid
    graph is the failure that cost a session 17 tool calls (the planning record's
    §10.1), and a skill the model does not choose to load cannot prevent it.
    """
    rows = tuple(rows)
    if not rows:
        return ""
    words = {row.word for row in rows}
    names = ", ".join(f"```{row.word}" for row in rows)
    lines = [
        "## Fences that are drawn",
        "",
        f"When pptmstr typesets replies, the pane showing them draws {names} fences "
        "once each is closed; otherwise they show as written. No other fence is drawn "
        "as a figure or a formula. A fence that cannot be drawn is shown as its source "
        "under a one-line reason.",
    ]
    if "math" in words:
        lines.append(
            "$...$ and $$...$$ in prose are not rendered and show as written; "
            "put mathematics in a ```math fence."
        )
    else:
        lines.append("$...$ and $$...$$ in prose are not rendered and show as written.")
    lines.append(_DISPLAY)
    if _MERMAID_FIRST_WORDS <= words:
        lines.append(_MERMAID_FIRST)
    if skill:
        lines.append(
            f"Before drawing a diagram, chart or formula, load the {SKILL} skill: it "
            "has the rules these fences must follow, and they win over a skill's "
            "general advice."
        )
    return "\n".join(lines)


PROMPT_LIMIT = 950
"""
The most characters ``prompt`` may run to with every row in it: it rides on every
request of every session, so it is held short by a test rather than by care. The
floor measured 855 with every row (2026-10-22), so this leaves 95 of headroom.
"""


_DISPLAY = (
    "Write the fence; the pane is the display. Unless asked for an export, do not "
    "render to a file, look for a converter, or preview with other tools."
)
_MERMAID_FIRST_WORDS = {"mermaid"}
_MERMAID_FIRST = (
    "For nodes and edges (dependencies, flows, sequences, states, entities) use "
    "```mermaid, which lays them out. Never compute a graph layout by hand."
)
_COLOURS = (
    "A diagram is drawn in the pane's own colours, which follow the theme, dark or "
    "light, and scaled down to fit the pane. A colour set with style, classDef or "
    "linkStyle is drawn as written against the pane, except that text or a line too "
    "faint against what is behind it is drawn lighter or darker until it is legible."
)

_SKILL_DESCRIPTION = (
    "Rules for drawing in a pptmstr reply. Load before writing any diagram, "
    "flowchart, dependency graph, sequence or state diagram, chart, plot or "
    "mathematical formula, so it is written as a mermaid or math fence the "
    "reply pane draws."
)


def skill_text(rows: Iterable[Fence] = RENDERED) -> str:
    """
    The packaged skill's SKILL.md (``plugins.figures_skill``): every row's rules,
    Mermaid for graphs, how a diagram's colours are drawn, and the export exception.

    Every row and not the installed ones: the file ships in the package and cannot
    know what this process has, so it defers to the floor, which is built at launch
    from ``available``. tests/test_skill_ship.py holds the shipped file equal to
    this, rather than writing it at load time, because an installed package
    directory may not be writable and the plugin path has to be the shipped one.
    """
    rows = tuple(rows)
    words = {row.word for row in rows}
    lines = [
        "---",
        f"name: {SKILL.split(':', 1)[1]}",
        f"description: {_SKILL_DESCRIPTION}",
        "---",
        "",
        "# Drawing in a pptmstr reply",
        "",
        "The session prompt names the fences pptmstr draws in this session. A fence "
        "below that it does not name is shown as code, so do not write it.",
        "",
        "## The fences",
        "",
        *(f"- {row.says}" for row in rows),
        "",
        "## How to use them",
        "",
        f"- {_DISPLAY}",
    ]
    if _MERMAID_FIRST_WORDS <= words:
        lines.append(f"- {_MERMAID_FIRST}")
    if any(row.kind == "figure" for row in rows):
        lines.append(f"- {_COLOURS}")
    lines += ["- These rules win over a skill's general advice.", ""]
    return "\n".join(lines)


def session_prompt(*, skill: bool = True) -> str:
    """
    ``prompt`` for the rows installed in this process: what a launch appends.
    """
    return prompt(available(), skill=skill)
