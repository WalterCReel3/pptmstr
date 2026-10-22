"""
A ```` ```mermaid ```` fence's source to sanitised SVG: merman, a post-pass, then the
sanitiser every ```` ```svg ```` fence passes.

**merman runs only in the render child** (``render.child_main``), never on the GUI
thread: its cost per diagram was measured only on one flowchart (7.6-10 ms), and a
hostile source could cost much more. ``layout`` refuses to run anywhere else. The child
hands back the sanitised SVG and its natural size, and from there a Mermaid figure is
drawn exactly as an svg one is (``host.FigureWidget``).

**merman is an optional extra** (``pip install 'pptmstr[mermaid]'``). Its Linux wheel is
``manylinux_2_35`` and pptmstr's floor is glibc 2.28, so a required dependency would
fail the whole install on a supported machine. The frame thread asks only whether the
distribution is installed (``version``, a metadata read with no import), and the child
imports it lazily on its first diagram. Where it is missing the fence stays code
under ``EXTRA_REASON``.

**The post-pass is fidelity, not safety** (``visible_labels``), so it lives here and
not in the sanitiser. merman wraps each class, state and ER label in a nested
``<svg style="overflow:hidden">`` sized by its own text metrics, which assume a face
narrower than the one resvg draws in, so labels lost their first and last glyphs
(probe M1, ``notes/vector-rendering/probe-m1-r2.md``). The pass makes those boxes
overflow visibly. An edge label's background stays merman's narrower width.
"""

from __future__ import annotations

import multiprocessing
import re
from collections.abc import Sequence
from importlib import metadata
from typing import Any

from .sanitise import Outcome, Refused, sanitise

DISTRIBUTION = "merman"

EXTRA_REASON = "mermaid rendering needs the optional 'mermaid' extra (merman)"
"""
Why a mermaid fence is code where merman is not installed or cannot be imported.
"""

PIPELINE_OPTIONS = '{"svg":{"pipeline":"resvg-safe"}}'
"""
merman's text-safe pipeline: labels as ``<text>``, no ``<foreignObject>`` (probe M1,
0 in five families). An unknown pipeline name is an error there, not a fallback.
"""

MAX_REASON_CHARS = 160
"""
The longest reason a failed diagram shows: merman's parse errors name the family and
what it expected, which is the useful part, and can run on for a line or more after.
"""

# A start tag of an <svg> element. `[^>]` cannot backtrack against `<svg\b`, so the
# scan is linear; a `>` inside an attribute value ends the match early, and that tag
# then keeps its overflow, which costs fidelity only: the sanitiser runs after this.
_SVG_START = re.compile(r"<svg\b[^>]*>")
_HIDDEN_STYLE = re.compile(r"overflow\s*:\s*hidden", re.IGNORECASE)
_HIDDEN_ATTR = re.compile(r"""\boverflow\s*=\s*(["'])\s*hidden\s*\1""", re.IGNORECASE)

_VERSION: list[str | None] = []
_ENGINE: list[Any] = []


def version() -> str | None:
    """
    The installed merman's version, or ``None`` where it is not installed. Read once,
    from the distribution's metadata: merman is not imported, so this is safe on the
    frame thread. It is part of a diagram's cache key, so a figure laid out by one
    merman is not shown for another.
    """
    if not _VERSION:
        try:
            _VERSION.append(metadata.version(DISTRIBUTION))
        except metadata.PackageNotFoundError:
            _VERSION.append(None)
    return _VERSION[0]


def in_render_child() -> bool:
    """
    Whether this process is a child made by ``multiprocessing``: the render child is.
    """
    return multiprocessing.parent_process() is not None


def layout(source: str) -> Outcome:
    """
    ``source`` laid out by merman, its labels let overflow (``visible_labels``), and
    sanitised. Every merman failure is a ``Refused`` with its first line.

    Raises ``RuntimeError`` outside a child process: merman runs in the render child
    and nowhere else.
    """
    if not in_render_child():
        raise RuntimeError("merman runs only in the render child, not in the GUI process")
    try:
        engine = _engine()
    except ImportError:
        return Refused(EXTRA_REASON)
    except Exception as exc:
        return Refused(error_line(exc))
    try:
        svg = engine.render_svg(source, PIPELINE_OPTIONS)
    except Exception as exc:
        return Refused(error_line(exc))
    if not isinstance(svg, str):
        return Refused(f"merman returned {type(svg).__name__}, not SVG")
    return sanitise(visible_labels(svg))


def _engine() -> Any:
    if not _ENGINE:
        import merman

        _ENGINE.append(merman.Merman())
    return _ENGINE[0]


def visible_labels(svg: str) -> str:
    """
    ``svg`` with every nested ``<svg>`` set to overflow visibly, in its ``style`` and
    in an ``overflow`` attribute. The first ``<svg>``, the root, is left as it is.
    """
    out: list[str] = []
    at = 0
    for n, match in enumerate(_SVG_START.finditer(svg)):
        if n == 0:
            continue
        tag = _HIDDEN_STYLE.sub("overflow:visible", match.group())
        tag = _HIDDEN_ATTR.sub('overflow="visible"', tag)
        out += [svg[at : match.start()], tag]
        at = match.end()
    out.append(svg[at:])
    return "".join(out)


def error_line(exc: BaseException) -> str:
    """
    The first line of what merman said, cut to ``MAX_REASON_CHARS``.

    merman's binding error carries its text as ``message``; its ``str`` is a dump of
    every field. Anything else is named with its type.
    """
    said = getattr(exc, "message", None)
    text = said if isinstance(said, str) and said.strip() else str(exc)
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first:
        first = type(exc).__name__
    elif not isinstance(said, str):
        first = f"{type(exc).__name__}: {first}"
    if len(first) > MAX_REASON_CHARS:
        first = first[: MAX_REASON_CHARS - 1].rstrip() + "…"
    return f"merman: {first}"


__all__: Sequence[str] = (
    "EXTRA_REASON",
    "PIPELINE_OPTIONS",
    "error_line",
    "layout",
    "version",
    "visible_labels",
)
