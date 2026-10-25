"""
A ```` ```mermaid ```` fence's source to sanitised SVG: merman, a post-pass, then the
sanitiser (``sanitise``).

**merman runs only in the render child** (``render.child_main``), never on the GUI
thread: its cost per diagram was measured only on one flowchart (7.6-10 ms), and a
hostile source could cost much more. ``layout`` refuses to run anywhere else. The child
parses the sanitised SVG into a bounded display list (``displaylist.from_mermaid``) and
hands back that, which the pane draws natively (``host.FigureWidget``).

**merman is an optional extra** (``pip install 'pptmstr[mermaid]'``). Its Linux wheel is
``manylinux_2_35`` and pptmstr's floor is glibc 2.28, so a required dependency would
fail the whole install on a supported machine. The frame thread asks only whether the
distribution is installed (``version``, a metadata read with no import), and the
child imports it lazily on its first diagram. Where it is missing or does not import
the fence stays code under ``missing_reason``. A launch asks a fresh interpreter to
import it (``import_probe``) before it promises Mermaid in the session prompt.

**The post-pass is fidelity, not safety** (``visible_labels``), so it lives here and
not in the sanitiser. merman wraps each class, state and ER label in a nested
``<svg style="overflow:hidden">`` sized by its own text metrics, which assume a face
narrower than the one resvg draws in, so labels lost their first and last glyphs
(probe M1, ``notes/vector-rendering/probe-m1-r2.md``). The pass makes those boxes
overflow visibly. An edge label's background stays merman's narrower width.

**A diagram's ``%%{ ... }%%`` directives are taken out before merman reads it**
(``without_directives``), every one, wherever it stands: a model-written ``init``
is configuration of the renderer, and the renderer is pptmstr's to configure. This
closes renderer configuration, not author styling: a diagram's own ``style``,
``classDef`` and ``linkStyle`` lines are content and still paint what they name
(``displaylist.authored_colours``), under the pane's legibility guard.
Measured on merman 0.9.1, resvg-safe, 2026-10-21, on a three-node flowchart:
``theme: dark`` changes every fill and ink of the output (ten times the dark
pixels), and pptmstr's theme, not the diagram's, is what colours a figure; a
``fontFamily`` naming a face resvg lacks is carried into the SVG and drawn in the
fallback face, with the same ink; ``htmlLabels: true`` is overridden by the
pipeline (no ``<foreignObject>``); ``maxTextSize`` and ``maxEdges``, tiny or huge,
changed nothing on a small flowchart.

**A YAML front matter keeps only the keys that are content** (``without_config``):
``title`` and ``displayMode``, each with its indented lines; every other top-level
key goes, ``config`` among them, for the same reason. Measured on the same merman
and flowchart, 2026-10-21: ``config: {theme: dark}``, in flow or block style and
with its key quoted, restyles the output as the ``init`` directive does;
``config: {fontFamily: …}`` is written into the SVG; ``title`` is drawn; an
unknown key and a ``<<`` merge key change nothing. Kept by name and not dropped
by name, so a key spelled in a way merman reads and this does not is taken out,
not passed on. merman reads a front matter only from the first line, and a
directive before it would bring it there once taken out, so directives go first.

**One engine is kept for the child's life and dropped on any merman exception**
(``_engine``): a binding that raised may have been left in a state no later
diagram recovers from, and a new engine costs about 4 µs (measured 2026-10-21).
"""

from __future__ import annotations

import json
import multiprocessing
import platform
import re
import subprocess
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
_FRONT_MATTER_FENCE = "---"
# A top-level front-matter key merman reads as content, its name bare or quoted.
_CONTENT_KEY = re.compile(r"""(["']?)(?:title|displayMode)\1\s*:""")
# What ends a line of a front matter: merman takes a lone `\r` as one, and the YAML
# 1.1 breaks are counted too, so no key can start where this sees no line start.
_LINE_BREAK = re.compile("\r\n|[\n\r\x85  ]")

IMPORT_PROBE_TIMEOUT_S = 20.0
"""
How long ``import_probe`` waits for a fresh interpreter to import merman. It took
46-82 ms (measured 2026-10-21); a probe that does not answer in time counts as
merman not importing.
"""

_VERSION: list[str | None] = []
_IMPORTS: list[bool] = []
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


def import_probe() -> bool:
    """
    Whether merman imports here, asked once per process: ``False`` without asking
    where it is not installed (``version``), else whether a fresh interpreter of this
    one, on this process's ``sys.path``, imports it. That is what the render child
    does with it, and an installed merman can fail to: a wheel for another glibc, a
    broken install. The answer is kept for the process.

    **Blocks for a process start: never on the frame thread.** A launch asks it
    (``fences.available``, from the session's own thread). Not an import in this
    process, which would hold the GIL for its 24-65 ms (measured 2026-10-21) and
    keep merman in the GUI process, where it never runs.
    """
    if not _IMPORTS:
        _IMPORTS.append(version() is not None and _imports_fresh())
    return _IMPORTS[0]


def _imports_fresh() -> bool:
    code = "import json, sys; sys.path[:] = json.loads(sys.argv[1]); import merman"
    try:
        done = subprocess.run(
            [sys.executable, "-I", "-c", code, json.dumps(sys.path)],
            capture_output=True,
            timeout=IMPORT_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


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
    ``source`` without its directives (``without_directives``) and without its
    front matter's configuration (``without_config``), laid out by merman,
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
        svg = engine.render_svg(without_config(without_directives(source)), PIPELINE_OPTIONS)
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
    that was a line of its own (``\n``, ``\r\n`` or ``\r``, each of which merman
    ends a line at). An opening with no close is left as written, and so is the rest
    of a line a directive shared. Linear: each character is passed once.

    The line end goes so that a front matter under a directive is on the first line
    again, where merman and ``without_config`` read one. Behind a blank or
    whitespace-only first line merman refuses the diagram, front matter and all
    (measured on merman 0.9.1, 2026-10-21), so such a source is code, not restyled.
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
        if start == 0 or source[start - 1] in "\n\r":
            if source.startswith("\r\n", at):
                at += 2
            elif source.startswith(("\n", "\r"), at):
                at += 1
    out.append(source[at:])
    return "".join(out)


def without_config(source: str) -> str:
    """
    ``source`` with its YAML front matter, where it has one, cut to the top-level
    keys that are content (``_CONTENT_KEY``), each with the lines indented under
    it. The front matter opens on the first line and closes at the next line that
    is ``---``, either with whitespace around it (and a byte-order mark before
    the first); one with no close is left as written, and merman refuses it. A
    top-level line is one indented by no more spaces than the first written
    line inside, and a blank line goes with the key above it. The front matter
    is written anew (``_content_only``), and one left with no key is taken out
    whole: merman refuses an empty one. Linear: each character is passed once.
    """
    inside: list[str] = []
    at = 0
    for brk in _LINE_BREAK.finditer(source):
        line = source[at : brk.start()]
        opening = at == 0
        at = brk.end()
        if opening:
            if line.lstrip("﻿").strip() != _FRONT_MATTER_FENCE:
                return source
        elif line.strip() == _FRONT_MATTER_FENCE:
            return _content_only(inside) + source[at:]
        else:
            inside.append(line)
    if at and source[at:].strip() == _FRONT_MATTER_FENCE:
        return _content_only(inside)
    return source


def _content_only(inside: list[str]) -> str:
    """
    A front matter of the lines ``inside`` it, each a line without its break, cut
    to its content keys (``without_config``), its fences ``---`` alone on a line
    and every break ``\\n``: so merman closes it where this did, or before.
    """
    written = [line for line in inside if line.strip()]
    top = len(written[0]) - len(written[0].lstrip(" ")) if written else 0
    kept: list[str] = []
    keeping = False
    for line in inside:
        indent = len(line) - len(line.lstrip(" "))
        if line.strip() and indent <= top:
            keeping = _CONTENT_KEY.match(line[indent:]) is not None
        if keeping:
            kept.append(line)
    if not any(line.strip() for line in kept):
        return ""
    return "\n".join([_FRONT_MATTER_FENCE, *kept, _FRONT_MATTER_FENCE, ""])


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
    "without_config",
    "without_directives",
)
