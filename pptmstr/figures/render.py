"""
Sanitised SVG to RGBA pixels, in a long-lived child process, without the frame waiting.

**A process, not a thread.** resvg_py 0.5.0 holds the GIL for the whole of
``svg_to_bytes``: with a render running, a 1 ms ticker thread saw a 281 ms gap
(probe R2, ``notes/vector-rendering/probe-m1-r2.md``). A thread would stall the frame
for every render. The child is started with the ``spawn`` context, because the GUI
process has a GL context and threads and ``fork`` copies both. It is started lazily,
by the first ``submit``, and lives until it hangs, dies or ``shutdown`` ends it.

**This is where a figure is built over more than one frame**, the one exception to
``views.py``'s stance that nothing is. A host submits, keeps drawing what it has, and
picks up the pixels from ``poll`` in a later frame.

**What may block, and where.** The frame thread calls ``submit`` and ``poll`` only,
and neither waits on the child: each child has one I/O thread here that starts the
process, writes requests and reads results, and that thread is the only one that
blocks on the pipe. ``poll`` reads what that thread queued, and kills a child whose
render has run past ``RENDER_TIMEOUT_S``; the next ``submit`` starts another. Neither
raises. ``shutdown`` is for application exit and does wait, boundedly.

**One request at a time, coalesced per key.** The child renders serially, so a
second request for a key whose first has not gone out replaces it: only the latest
width of a figure is wanted. A result for a key that has a newer request waiting is
dropped, not delivered.

**Fonts are mapped, or text is not drawn.** Under ``skip_system_fonts``, resvg draws
no ``<text>`` whose family resolves to nothing, and raises nothing (operator-run,
``svg-renderers.md``). Every generic family is mapped to the faces imscribe sets prose
and code in. ``serif_family`` is the one that carries a name that matches nothing
(R1c), so it is mapped too, to the prose face, because no serif ships.

**Pillow decodes in the child.** resvg_py returns only PNG (probe T1), and the frame
thread needs RGBA for a texture.
"""

from __future__ import annotations

import math
import queue
import signal
import struct
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from multiprocessing import get_context
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

from .sanitise import MAX_NATURAL_PIXELS, MAX_SIDE, Sanitised

RENDER_TIMEOUT_S = 10.0
"""
How long one render may take before its child is killed.

The largest legitimate raster is ``MAX_RASTER_PIXELS``: 1,200 rects and labels at
4096x3072 took 1.14 s here, and 249 ms at 800 px (run 2026-10-20; the probe M1
merman diagrams took 70-282 ms at 800 px). The operator's machine ran the same grid
about 1.6 times slower than this one (``probe-m1-r2.md``), so about 2 s is the worst
legitimate case, and ten seconds leaves five times that. A render past it is a hang
or hostile input; the sanitiser's element cap bounds the latter at about half a
second of ``<use>`` expansion (R1a, 0.11 ms per expanded leaf). The cost of a longer
limit is that every other figure waits behind a stuck one for that long.
"""

START_TIMEOUT_S = 20.0
"""
How long a child may take to start and load its fonts before it is killed.

Spawning a fresh interpreter and importing resvg_py, fontTools and Pillow is about a
second; a box under load, or a cold disk cache, can be several times that.
"""

MAX_RASTER_PIXELS = MAX_NATURAL_PIXELS
"""
The most pixels one raster may have: 4096x4096, 64 MiB of RGBA.

The same number the sanitiser caps natural size at. ``submit`` shrinks a request to
fit it, and the child refuses anything over it again before rendering and after,
so a raster this large never crosses the pipe.
"""

_SHUTDOWN_WAIT_S = 2.0


@dataclass(frozen=True, slots=True)
class Rendered:
    """
    A raster for ``key``: ``width`` x ``height`` straight-alpha RGBA, rows top first.

    ``px_width`` and ``scale`` are the request's, so a host can tell which drawn width
    and DPI scale this answers; ``width`` may be smaller than ``px_width * scale``
    when the request was shrunk to ``MAX_RASTER_PIXELS``.
    """

    key: Hashable
    width: int
    height: int
    rgba: bytes
    px_width: float
    scale: float


@dataclass(frozen=True, slots=True)
class Failed:
    """
    A render of ``key`` that produced nothing, with the one-line reason a host shows.
    """

    key: Hashable
    reason: str


Result = Rendered | Failed


@dataclass(frozen=True, slots=True)
class Fonts:
    """
    The font files resvg loads, and the two whose family names the generics map to.

    ``sans`` is mapped to ``font_family``, ``sans_serif_family``, ``serif_family``,
    ``cursive_family`` and ``fantasy_family``; ``mono`` to ``monospace_family``. Both
    are among ``files``.
    """

    files: tuple[str, ...]
    sans: str
    mono: str


def imscribe_fonts() -> Fonts:
    """
    The faces imscribe sets prose and code in, as its registry names them now.

    Asked of the typeset seam (``views.figure_fonts``), the one importer of the
    library, so a proportional family pptmstr registered (``gate._register``) is the
    one a figure is set in too. Read once per child, when it starts, on its I/O
    thread.
    """
    from ..ui.typeset.views import figure_fonts

    return figure_fonts()


def raster_size(sanitised: Sanitised, px_width: float, scale: float) -> tuple[int, int]:
    """
    The raster ``submit`` asks for: ``px_width * scale`` wide, at the natural aspect,
    shrunk to fit ``MAX_RASTER_PIXELS`` and ``MAX_SIDE``. At least 1x1.
    """
    aspect = sanitised.height / sanitised.width
    wanted = px_width * scale
    width = max(1, min(int(MAX_SIDE), round(wanted) if math.isfinite(wanted) else 1))
    if width * max(1, math.ceil(width * aspect)) > MAX_RASTER_PIXELS:
        width = max(1, math.floor(math.sqrt(MAX_RASTER_PIXELS / aspect)))
    return width, max(1, math.ceil(width * aspect))


@dataclass(frozen=True, slots=True)
class _Request:
    seq: int
    key: Hashable
    svg: str
    width: int
    px_width: float
    scale: float


@dataclass(eq=False)
class _Child:
    """
    One render process and the I/O thread that is the only thing to block on it.

    ``proc`` is set by that thread once the process has started. ``abandoned`` is set
    by the frame thread when it gives the child up; whichever of the two sees both
    kills the process, so a child given up while it is starting is killed too.
    """

    requests: queue.SimpleQueue[_Request | None] = field(default_factory=queue.SimpleQueue)
    proc: BaseProcess | None = None
    started_at: float = field(default_factory=time.monotonic)
    ready: bool = False
    busy_since: float | None = None
    abandoned: bool = False
    thread: threading.Thread | None = None

    def abandon(self) -> None:
        self.abandoned = True
        self.requests.put(None)
        _kill(self.proc)


# What an I/O thread hands the frame thread: the result of one request, or the
# reason its child stopped answering.
_Answer = tuple[_Child, int, Result] | tuple[_Child, None, str]


class Renderer:
    """
    Submit sanitised SVG by key; poll for pixels. Neither call blocks nor raises.

    Construct it anywhere: nothing starts until the first ``submit``. ``fonts`` is
    called on the I/O thread when a child starts, not on the frame thread.
    """

    def __init__(
        self,
        *,
        fonts: Callable[[], Fonts] = imscribe_fonts,
        timeout_s: float = RENDER_TIMEOUT_S,
        start_timeout_s: float = START_TIMEOUT_S,
    ) -> None:
        self._fonts = fonts
        self._timeout_s = timeout_s
        self._start_timeout_s = start_timeout_s
        self._answers: queue.SimpleQueue[_Answer] = queue.SimpleQueue()
        self._pending: dict[Hashable, _Request] = {}
        self._inflight: _Request | None = None
        self._child: _Child | None = None
        self._seq = 0
        self._failed: list[Failed] = []

    @property
    def pid(self) -> int | None:
        """
        The running child's process id, or ``None`` when none has started.
        """
        child = self._child
        return None if child is None or child.proc is None else child.proc.pid

    def submit(self, key: Hashable, sanitised: Sanitised, px_width: float, scale: float) -> None:
        """
        Ask for ``sanitised`` drawn ``px_width`` wide at DPI ``scale``, under ``key``.

        Replaces any request for ``key`` that has not gone to the child. One the same
        as what the child is rendering for ``key`` now is not asked twice.
        """
        try:
            width, _ = raster_size(sanitised, px_width, scale)
            flying = self._inflight
            self._pending.pop(key, None)
            if not (
                flying is not None
                and flying.key == key
                and flying.width == width
                and flying.svg == sanitised.svg
            ):
                self._seq += 1
                self._pending[key] = _Request(self._seq, key, sanitised.svg, width, px_width, scale)
            self._dispatch()
        except Exception as exc:
            self._failed.append(Failed(key, f"render not started: {_one_line(exc)}"))

    def poll(self) -> list[Result]:
        """
        Every result that has arrived since the last call, and every failure.

        Also where a child that has run past its time is killed and reported, and
        where the next waiting request goes out.
        """
        out: list[Result] = []
        try:
            out.extend(self._failed)
            self._failed.clear()
            self._collect(out)
            self._enforce_time(out, time.monotonic())
            self._dispatch()
        except Exception as exc:
            flying, self._inflight = self._inflight, None
            self._drop_child()
            if flying is not None:
                out.append(Failed(flying.key, f"renderer error: {_one_line(exc)}"))
        return out

    def shutdown(self) -> None:
        """
        Kill the child and forget every request. For application exit: it waits up to
        about two seconds for the process to be reaped. Never raises.
        """
        try:
            self._pending.clear()
            self._inflight = None
            child, self._child = self._child, None
            if child is not None:
                child.abandon()
                # The I/O thread kills and reaps the process on every way out, and
                # abandoning the child gives it one from wherever it is waiting.
                if child.thread is not None:
                    child.thread.join(_SHUTDOWN_WAIT_S)
                _kill(child.proc)
        except Exception:
            pass

    # The frame thread's side ------------------------------------------------------

    def _collect(self, out: list[Result]) -> None:
        while True:
            try:
                child, seq, answer = self._answers.get_nowait()
            except queue.Empty:
                return
            if child is not self._child:
                continue
            flying = self._inflight
            if seq is None:
                assert isinstance(answer, str)
                self._drop_child()
                if flying is not None:
                    self._inflight = None
                    out.append(Failed(flying.key, answer))
                continue
            if flying is None or seq != flying.seq:
                continue
            self._inflight = None
            if flying.key not in self._pending:
                assert not isinstance(answer, str)
                out.append(answer)

    def _enforce_time(self, out: list[Result], now: float) -> None:
        child, flying = self._child, self._inflight
        if child is None:
            return
        if flying is not None:
            if child.busy_since is not None and now - child.busy_since > self._timeout_s:
                reason = f"render took longer than {self._timeout_s:g} s and was stopped"
            elif not child.ready and now - child.started_at > self._start_timeout_s:
                reason = f"render process did not start within {self._start_timeout_s:g} s"
            else:
                return
            self._inflight = None
            self._drop_child()
            out.append(Failed(flying.key, reason))
        elif child.proc is not None and not child.proc.is_alive():
            # Died while idle: its I/O thread is waiting for a request, not reading,
            # so nothing else would notice before the next render was lost to it.
            self._drop_child()

    def _dispatch(self) -> None:
        if self._inflight is not None or not self._pending:
            return
        key = next(iter(self._pending))
        request = self._pending.pop(key)
        if self._child is None:
            self._child = self._start()
        self._inflight = request
        self._child.requests.put(request)

    def _drop_child(self) -> None:
        child, self._child = self._child, None
        if child is not None:
            child.abandon()

    def _start(self) -> _Child:
        child = _Child()
        child.thread = threading.Thread(
            target=_serve,
            args=(child, self._fonts, self._answers),
            daemon=True,
            name="figure-render-io",
        )
        child.thread.start()
        return child


# The I/O thread ---------------------------------------------------------------------


def _serve(child: _Child, fonts: Callable[[], Fonts], answers: queue.SimpleQueue[_Answer]) -> None:
    """
    Start ``child``'s process, then hand it requests and hand back what it says.

    Every way out of here puts a reason on ``answers``, unless the frame thread gave
    the child up, and leaves the process killed and reaped.
    """
    conn: Connection | None = None
    proc: BaseProcess | None = None
    reason = "render process stopped"
    try:
        ctx = get_context("spawn")
        conn, theirs = ctx.Pipe(duplex=True)
        proc = ctx.Process(
            target=child_main, args=(theirs, fonts()), daemon=True, name="figure-render"
        )
        proc.start()
        theirs.close()
        child.proc = proc
        if child.abandoned:
            return
        conn.recv()
        child.ready = True
        while (request := child.requests.get()) is not None:
            child.busy_since = time.monotonic()
            conn.send((request.width, request.svg))
            head = conn.recv()
            result: Result
            if head[0] == "ok":
                rgba = conn.recv_bytes()
                result = Rendered(
                    request.key, head[1], head[2], rgba, request.px_width, request.scale
                )
            else:
                result = Failed(request.key, str(head[1]))
            child.busy_since = None
            answers.put((child, request.seq, result))
    except EOFError:
        reason = "render process died"
    except Exception as exc:
        reason = f"render process failed: {_one_line(exc)}"
    finally:
        # On EOF the child has already exited: the kill reaches a zombie and does not
        # change the status the join then reads, which is how it died.
        _kill(proc)
        if conn is not None:
            conn.close()
        if proc is not None:
            proc.join()
            if reason == "render process died" and (proc.exitcode or 0) < 0:
                reason = f"render process killed by signal {-(proc.exitcode or 0)}"
        if not child.abandoned:
            answers.put((child, None, reason))


def _kill(proc: BaseProcess | None) -> None:
    try:
        if proc is not None and proc.exitcode is None:
            proc.kill()
    except Exception:
        pass


def _one_line(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


# The child --------------------------------------------------------------------------


def child_main(conn: Connection, fonts: Fonts) -> None:
    """
    The render process: load the fonts once, then answer requests until the pipe closes.

    It says ``("ready", None)`` or ``("ready", reason)`` once. A child that could not
    load what it needs answers every request with that reason rather than exiting,
    so a missing library is one failure per figure and not a restart per frame.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    failure: str | None = None
    options: dict[str, Any] = {}
    try:
        options = resvg_options(fonts)
        import resvg_py  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:
        failure = f"renderer unavailable: {_one_line(exc)}"
    conn.send(("ready", failure))
    while True:
        try:
            width, svg = conn.recv()
        except EOFError:
            return
        if failure is not None:
            conn.send(("err", failure))
            continue
        try:
            w, h, rgba = rasterise(svg, width, options)
        except Exception as exc:
            conn.send(("err", _one_line(exc)))
            continue
        conn.send(("ok", w, h))
        conn.send_bytes(rgba)


def family_name(path: str) -> str:
    """
    The family name resvg's font database knows a file by: the typographic family
    (name ID 16) if the file has one, else name ID 1.
    """
    from fontTools.ttLib import TTFont

    with TTFont(path, lazy=True) as font:
        names = font["name"]
        for nid in (16, 1):
            record = names.getName(nid, 3, 1) or names.getName(nid, 1, 0)
            if record is not None:
                return str(record.toUnicode())
    raise ValueError(f"{path} names no family")


def resvg_options(fonts: Fonts) -> dict[str, Any]:
    """
    ``svg_to_bytes``'s font options: only ``fonts.files``, every generic mapped.
    """
    sans, mono = family_name(fonts.sans), family_name(fonts.mono)
    return {
        "font_files": list(fonts.files),
        "skip_system_fonts": True,
        "font_family": sans,
        "sans_serif_family": sans,
        "serif_family": sans,
        "cursive_family": sans,
        "fantasy_family": sans,
        "monospace_family": mono,
    }


def render_png(svg: str, width: int, options: dict[str, Any]) -> bytes:
    """
    resvg's PNG of ``svg`` at ``width`` px, refused before rendering when the height
    the root implies would put it over ``MAX_RASTER_PIXELS``.
    """
    import resvg_py

    height = _implied_height(svg, width)
    if width < 1 or width * height > MAX_RASTER_PIXELS:
        raise ValueError(f"{width}x{height} is over the {MAX_RASTER_PIXELS}-pixel cap")
    png: bytes = resvg_py.svg_to_bytes(svg_string=svg, width=width, **options)
    return png


def rasterise(svg: str, width: int, options: dict[str, Any]) -> tuple[int, int, bytes]:
    """
    ``svg`` at ``width`` px as ``(width, height, rgba)``, the cap held on both sides of
    resvg.
    """
    from PIL import Image

    png = render_png(svg, width, options)
    if png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise ValueError("resvg returned something that is not a PNG")
    w, h = struct.unpack(">II", png[16:24])
    if w * h > MAX_RASTER_PIXELS:
        raise ValueError(f"{w}x{h} is over the {MAX_RASTER_PIXELS}-pixel cap")
    with Image.open(BytesIO(png)) as image:
        rgba = image.convert("RGBA")
        return rgba.width, rgba.height, rgba.tobytes()


def _implied_height(svg: str, width: int) -> int:
    """
    The height resvg will give ``svg`` at ``width``, from the root's ``width`` and
    ``height``, which the sanitiser sets to the natural size in px. A whole parse,
    in the child: the input is at most the sanitiser's megabyte.
    """
    root = ET.fromstring(svg)
    w0 = float(root.get("width", "nan"))
    h0 = float(root.get("height", "nan"))
    if not (math.isfinite(w0) and math.isfinite(h0) and w0 > 0 and h0 > 0):
        raise ValueError("the root has no natural size")
    return max(1, math.ceil(width * h0 / w0))


_RENDERER: Renderer | None = None


def renderer() -> Renderer:
    """
    The process's renderer, made on first use. Starting it costs nothing until a
    figure is submitted.
    """
    global _RENDERER
    if _RENDERER is None:
        _RENDERER = Renderer()
    return _RENDERER


def shutdown() -> None:
    """
    End the process's render child, if one was ever made. For application exit.
    """
    if _RENDERER is not None:
        _RENDERER.shutdown()


__all__: Sequence[str] = (
    "Failed",
    "Fonts",
    "MAX_RASTER_PIXELS",
    "RENDER_TIMEOUT_S",
    "Rendered",
    "Renderer",
    "Result",
    "rasterise",
    "renderer",
    "resvg_options",
    "shutdown",
)
