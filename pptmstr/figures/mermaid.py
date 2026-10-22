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

**A diagram's ``%%{ ... }%%`` directives are taken out before merman reads it**
(``without_directives``), every one, wherever it stands: a model-written ``init``
is configuration of the renderer, and the figure is pptmstr's to configure.
Measured on merman 0.9.1, resvg-safe, 2026-10-21, on a three-node flowchart:
``theme: dark`` changes every fill and ink of the output (ten times the dark
pixels), where the host draws every figure on a light card (``host.BACKDROP``); a
``fontFamily`` naming a face resvg lacks is carried into the SVG and drawn in the
fallback face, with the same ink; ``htmlLabels: true`` is overridden by the
pipeline (no ``<foreignObject>``); ``maxTextSize`` and ``maxEdges``, tiny or huge,
changed nothing on a small flowchart. A YAML front-matter ``config:`` block reaches
merman the same way and is not taken out.

**One engine is kept for the child's life and dropped on any merman exception**
(``_engine``): a binding that raised may have been left in a state no later
diagram recovers from, and a new engine costs about 4 µs (measured 2026-10-21).
"""

from __future__ import annotations

import multiprocessing
import platform
import re
import sys
from collections.abc import Sequence
from importlib import metadata
from typing import Any

from .sanitise import Outcome, Refused, sanitise

DISTRIBUTION = "merman"

EXTRA_REASON = "mermaid rendering needs the optional 'mermaid' extra (merman)"
"""
Why a mermaid fence is code where merman is not installed or cannot be imported,
on a machine merman has a wheel for (``missing_reason``).
"""

WHEEL_GLIBC = (2, 35)
"""
The oldest glibc merman 0.9.1's one Linux wheel runs on (``manylinux_2_35_x86_64``).
Its other wheels are macOS arm64 and Windows x86-64.
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
# One attribute of a start tag, its value quoted or bare. Each alternative is a run
# of one class, so the scan is linear.
_ATTRIBUTE = re.compile(r"""([^\s=/>"']+)(\s*=\s*)("[^"]*"|'[^']*'|[^\s"'>]+)""")
# The `overflow` property inside a style value, and not `text-overflow` or
# `overflow-x`.
_HIDDEN_PROPERTY = re.compile(r"(?<![\w-])overflow\s*:\s*hidden(?![\w-])", re.IGNORECASE)
_DIRECTIVE_OPEN = "%%{"
_DIRECTIVE_CLOSE = "}%%"

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


def missing_reason(
    system: str = sys.platform,
    libc: tuple[str, str] | None = None,
    machine: str | None = None,
) -> str:
    """
    Why a mermaid fence is code where merman cannot be imported: that this platform
    has no merman wheel where that is so (Linux below glibc 2.35, or not x86-64),
    else ``EXTRA_REASON``. Reads the platform only, never imports merman.
    """
    if not system.startswith("linux"):
        return EXTRA_REASON
    lib, version = platform.libc_ver() if libc is None else libc
    arch = platform.machine() if machine is None else machine
    if arch not in ("x86_64", "amd64", "AMD64"):
        return (
            f"mermaid rendering is unavailable on this platform: merman has no Linux {arch} wheel"
        )
    if lib == "glibc":
        try:
            found = tuple(int(part) for part in version.split(".")[:2])
        except ValueError:
            return EXTRA_REASON
        if found < WHEEL_GLIBC:
            need = ".".join(map(str, WHEEL_GLIBC))
            return (
                "mermaid rendering is unavailable on this platform: merman needs "
                f"glibc {need} or later, and this is {version}"
            )
    return EXTRA_REASON


def in_render_child() -> bool:
    """
    Whether this process is a child made by ``multiprocessing``: the render child is.
    """
    return multiprocessing.parent_process() is not None


def layout(source: str) -> Outcome:
    """
    ``source`` without its directives (``without_directives``), laid out by merman,
    its labels let overflow (``visible_labels``), and sanitised. Every merman failure
    is a ``Refused`` with its first line, and drops the engine (``_engine``).

    Raises ``RuntimeError`` outside a child process: merman runs in the render child
    and nowhere else.
    """
    if not in_render_child():
        raise RuntimeError("merman runs only in the render child, not in the GUI process")
    try:
        engine = _engine()
    except ImportError:
        return Refused(missing_reason())
    except Exception as exc:
        _ENGINE.clear()
        return Refused(error_line(exc))
    try:
        svg = engine.render_svg(without_directives(source), PIPELINE_OPTIONS)
    except Exception as exc:
        _ENGINE.clear()
        return Refused(error_line(exc))
    if not isinstance(svg, str):
        return Refused(f"merman returned {type(svg).__name__}, not SVG")
    return sanitise(visible_labels(svg))


def _engine() -> Any:
    """
    The engine this child lays out with, made on first use and after any merman
    exception has dropped it (``layout``).
    """
    if not _ENGINE:
        import merman

        _ENGINE.append(merman.Merman())
    return _ENGINE[0]


def without_directives(source: str) -> str:
    """
    ``source`` with every ``%%{ ... }%%`` directive taken out, from its opening
    ``%%{`` to the first ``}%%`` after it, across lines, with the line end after one
    that was a line of its own. An opening with no close is left as written, and so
    is the rest of a line a directive shared. Linear: each character is passed once.
    """
    out: list[str] = []
    at = 0
    while (start := source.find(_DIRECTIVE_OPEN, at)) != -1:
        end = source.find(_DIRECTIVE_CLOSE, start + len(_DIRECTIVE_OPEN))
        if end == -1:
            break
        out.append(source[at:start])
        at = end + len(_DIRECTIVE_CLOSE)
        # A directive that was a line of its own leaves no blank line behind.
        if (start == 0 or source[start - 1] == "\n") and source.startswith("\n", at):
            at += 1
    out.append(source[at:])
    return "".join(out)


def visible_labels(svg: str) -> str:
    """
    ``svg`` with every nested ``<svg>`` set to overflow visibly: the ``overflow``
    property of its ``style`` attribute and its ``overflow`` attribute, and nothing
    else of the tag. The first ``<svg>``, the root, is left as it is.
    """
    out: list[str] = []
    at = 0
    for n, match in enumerate(_SVG_START.finditer(svg)):
        if n == 0:
            continue
        out += [svg[at : match.start()], _ATTRIBUTE.sub(_opened, match.group())]
        at = match.end()
    out.append(svg[at:])
    return "".join(out)


def _opened(attribute: re.Match[str]) -> str:
    name, equals, value = attribute.groups()
    quote = value[0] if value[:1] in ("'", '"') else ""
    inner = value[1:-1] if quote else value
    if name.lower() == "style":
        inner = _HIDDEN_PROPERTY.sub("overflow:visible", inner)
    elif name.lower() == "overflow" and inner.strip().lower() == "hidden":
        inner = "visible"
    else:
        return attribute.group()
    return f"{name}{equals}{quote}{inner}{quote}"


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
    "WHEEL_GLIBC",
    "error_line",
    "layout",
    "missing_reason",
    "version",
    "visible_labels",
    "without_directives",
)
