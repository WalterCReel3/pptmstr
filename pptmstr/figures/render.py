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

**Mermaid is laid out in the same child** (``lay_out``, ``mermaid.layout``), so merman
never runs on the frame thread either, and so is the SVG parsed into what the pane
draws (``displaylist.from_mermaid``): the child answers a ``Laid``, a bounded display
list with the diagram's natural size, and no pixels, as plain JSON: nothing the
child sends is unpickled (``_serve``). The I/O thread reads it a record at a time
and checks every bound of it again (``displaylist.decode``) before the frame thread
sees it. A layout is one more request in the same queue, under the same timeout
and the same failure kinds. It does not need the rasteriser: a child that could
not load resvg still lays diagrams out.

**The raster path stays, and Mermaid does not use it.** ``submit`` and ``rasterise``
are what slice 1's svg fence drew through; nothing submits to them now, and a
separate task removes them once native Mermaid has been reviewed.
"""

from __future__ import annotations

import importlib
import json
import math
import queue
import signal
import struct
import sys
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

from .. import drawable
from . import displaylist
from .displaylist import DisplayList
from .sanitise import MAX_NATURAL_PIXELS, Sanitised

RENDER_TIMEOUT_S = 10.0
"""
How long one render may take before its child is killed.

The largest legitimate raster is ``MAX_RASTER_PIXELS``: 1,200 rects and labels at
4096x3072 took 1.14 s here, and 249 ms at 800 px (run 2026-10-20; the probe M1
merman diagrams took 70-282 ms at 800 px). The operator's machine ran the same grid
about 1.6 times slower than this one (``probe-m1-r2.md``), so about 2 s is the worst
legitimate case, and ten seconds leaves five times that. A render past it is a hang
or hostile input. This, and not the sanitiser, is what bounds render time: the
sanitiser's caps bound how many elements resvg instantiates, but a few elements can
cost seconds (probe R3, 1600x1200: 62 full-canvas opacity layers 6 s, a 60,000-vertex
path with a mid marker 17 s, 4,000 lines dashed at 0.001 over a minute). The cost of
a longer limit is that every other figure waits behind a stuck one for that long.
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

MEMORY_LIMIT_BYTES = 1 << 30
"""
The address space a render child may have, on Linux, where ``RLIMIT_AS`` is enforced.

What it bounds is what the sanitiser does not see: references made from ``<style>``
rules, and anything resvg allocates that the caps did not foresee. A ``<pattern>``
tile, allocated at its size under every transform above it, took 1 GB under
``scale(160)`` and asked for 40 GB under ``scale(1000)`` (probe R3), and is why the
sanitiser drops ``<pattern>``: this limit caught it here and nothing would on
macOS. The largest
legitimate render, the 1,200-label grid at 4096x3072, peaked at about 300 MB of
address space, and the deepest mask chain the sanitiser admits at 692 MB at
4096x4096; a gibibyte holds both. Past it resvg's allocation fails and the child
aborts, which the I/O thread reports as the render's failure and the next request
replaces the child; an allocation that fails in Python is answered as that render's
error.

macOS does not enforce ``RLIMIT_AS``, so there nothing bounds a child's memory but
``RENDER_TIMEOUT_S``.
"""

MAX_TEXTURE_SIDE = 8192
"""
The longest side a raster may have: a conservative constant, not read from GL.

An upload over the GPU's ``GL_MAX_TEXTURE_SIZE`` does not raise: on llvmpipe a
2x32768 array gave a texture and ``GL_INVALID_VALUE`` (1281), so a blank figure
(fig-fix, Xvfb). This clamp is therefore the only guard, and reading the GL limit
needs PyOpenGL, which is a ``dev`` extra only. llvmpipe and GL 4.1 on macOS report
16384; 8192 leaves room for an older or smaller GPU. ``MAX_RASTER_PIXELS`` already
bounds the area, so this only shrinks a long raster: at the pixel cap, one more than
four times as long as it is wide; and a long figure at density 2. A figure more than
8192 times as long as it is wide cannot fit at one pixel and is refused.
"""

CHILD_MODULES: dict[str, str] = {
    "resvg_py": "resvg_py",
    "Pillow": "PIL.Image",
    "fonttools": "fontTools.ttLib",
}
"""
What the render child imports for any figure, by the distribution that provides
it: the child imports these and nothing else before it says it is ready
(``child_main``), so this table is what it needs.
"""

UPLOAD_MODULES: dict[str, str] = {"numpy": "numpy"}
"""
What the frame thread imports to make a texture of a raster (``host._upload``).
"""

DISTRIBUTIONS: tuple[str, ...] = (*CHILD_MODULES, *UPLOAD_MODULES)
"""
Every distribution a figure needs beyond the typeset library, whatever its fence:
what the table of drawn fences asks is installed (``fences.RENDERED``).
"""

MAX_REASON_CHARS = 320
"""
The longest reason a child's failure is shown with: room for a prefix of this
module's and ``mermaid.MAX_REASON_CHARS`` of merman's own. The child cuts to it
before it sends, and the I/O thread cuts again on receipt.
"""

MAX_HEAD_BYTES = 12 * MAX_REASON_CHARS + 64
"""
The longest a reply's head line may be: a reason at ``MAX_REASON_CHARS``, each
character at its longest ASCII-only JSON spelling (two ``\\uXXXX``).
"""

MAX_REPLY_BYTES = MAX_HEAD_BYTES + 1 + displaylist.MAX_WIRE_BYTES
"""
The most the I/O thread reads of one reply (``recv_bytes``' ``maxlength``): a head
and a display list at every bound. A child that announces more is not read: the
figure fails and the child is replaced.
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

    ``transient`` is whether the same request may succeed if asked again: the child
    died, was killed for time or did not start, which the machine's state can cause.
    An answer the child gave (resvg refused the document, a cap was hit, the
    renderer could not load) is not transient: it would be the same again.
    """

    key: Hashable
    reason: str
    transient: bool = False


@dataclass(frozen=True, slots=True)
class Laid:
    """
    A ```` ```mermaid ```` source laid out for ``key``: merman's SVG after
    ``mermaid.visible_labels`` and ``sanitise``, parsed into the display list the
    pane draws, which carries the natural size. Made in the child and checked
    against every bound on the I/O thread; a refusal or a list out of bounds comes
    back as a ``Failed`` that is not transient.
    """

    key: Hashable
    display: DisplayList


Result = Rendered | Laid | Failed


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
    shrunk so that it fits ``MAX_RASTER_PIXELS`` and neither side is longer than
    ``MAX_TEXTURE_SIDE``. At least 1x1.

    Only a figure more than ``MAX_TEXTURE_SIDE`` times taller than it is wide cannot
    fit at one pixel wide; its height is then over the side, and the child refuses it.
    """
    aspect = sanitised.height / sanitised.width
    wanted = px_width * scale
    width = round(wanted) if math.isfinite(wanted) else 1
    width = max(1, min(width, MAX_TEXTURE_SIDE, math.floor(MAX_TEXTURE_SIDE / aspect)))
    if width * max(1, math.ceil(width * aspect)) > MAX_RASTER_PIXELS:
        width = max(1, math.floor(math.sqrt(MAX_RASTER_PIXELS / aspect)))
    while width > 1 and math.ceil(width * aspect) > MAX_TEXTURE_SIDE:
        width -= 1  # where floor(side / aspect) * aspect rounds up past the side
    return width, max(1, math.ceil(width * aspect))


_RASTER = "raster"
_MERMAID = "mermaid"


@dataclass(frozen=True, slots=True)
class _Request:
    """
    One request to the child. ``kind`` is ``_RASTER`` (``svg`` is sanitised SVG) or
    ``_MERMAID`` (``svg`` is a diagram's source, and the sizes are unused).
    """

    seq: int
    key: Hashable
    svg: str
    width: int
    px_width: float
    scale: float
    kind: str = _RASTER


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
        memory_limit_bytes: int | None = MEMORY_LIMIT_BYTES,
        child: Callable[[Connection, Fonts, int | None], None] | None = None,
    ) -> None:
        self._fonts = fonts
        self._child_main = child_main if child is None else child
        self._memory_limit = memory_limit_bytes
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
                and flying.kind == _RASTER
                and flying.width == width
                and flying.svg == sanitised.svg
            ):
                self._seq += 1
                self._pending[key] = _Request(self._seq, key, sanitised.svg, width, px_width, scale)
            self._dispatch()
        except Exception as exc:
            self._failed.append(Failed(key, f"render not started: {_one_line(exc)}", True))

    def lay_out(self, key: Hashable, source: str) -> None:
        """
        Ask for a ```` ```mermaid ```` fence's ``source`` laid out in the child, under
        ``key``: answered with a ``Laid``, or a ``Failed`` carrying merman's reason.
        Queued, coalesced and timed as a raster request is; never blocks or raises.
        """
        try:
            flying = self._inflight
            self._pending.pop(key, None)
            if not (
                flying is not None
                and flying.key == key
                and flying.kind == _MERMAID
                and flying.svg == source
            ):
                self._seq += 1
                self._pending[key] = _Request(self._seq, key, source, 0, 0.0, 1.0, _MERMAID)
            self._dispatch()
        except Exception as exc:
            self._failed.append(Failed(key, f"layout not started: {_one_line(exc)}", True))

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
                out.append(Failed(flying.key, f"renderer error: {_one_line(exc)}", True))
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
                    out.append(Failed(flying.key, answer, True))
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
            out.append(Failed(flying.key, reason, True))
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
            args=(child, self._fonts, self._answers, self._memory_limit, self._child_main),
            daemon=True,
            name="figure-render-io",
        )
        child.thread.start()
        return child


# The I/O thread ---------------------------------------------------------------------


def _serve(
    child: _Child,
    fonts: Callable[[], Fonts],
    answers: queue.SimpleQueue[_Answer],
    memory_limit: int | None,
    target: Callable[[Connection, Fonts, int | None], None] | None = None,
) -> None:
    """
    Start ``child``'s process, then hand it requests and hand back what it says.

    Every way out of here puts a reason on ``answers``, unless the frame thread gave
    the child up, and leaves the process killed and reaped.

    **Nothing the child sends is unpickled.** It parses untrusted text through merman
    (native code) and this package's parsers, so a child that has been taken over is
    in the threat model, and unpickling its reply would hand it this process. Every
    reply is ``recv_bytes`` of plain JSON, read to at most ``MAX_REPLY_BYTES``
    (``_answer``). Requests go the other way as JSON too, so nothing on the pipe is
    pickled; only the child's start arguments are, by ``spawn``, from this side.
    """
    conn: Connection | None = None
    proc: BaseProcess | None = None
    reason = "render process stopped"
    try:
        ctx = get_context("spawn")
        conn, theirs = ctx.Pipe(duplex=True)
        proc = ctx.Process(
            target=child_main if target is None else target,
            args=(theirs, fonts(), memory_limit),
            daemon=True,
            name="figure-render",
        )
        proc.start()
        theirs.close()
        child.proc = proc
        if child.abandoned:
            return
        head, _ = _head(conn.recv_bytes(MAX_HEAD_BYTES))
        if not head or head[0] != "ready":
            raise ValueError("the render process did not say it was ready")
        child.ready = True
        while (request := child.requests.get()) is not None:
            child.busy_since = time.monotonic()
            conn.send_bytes(json.dumps([request.kind, request.width, request.svg]).encode("ascii"))
            result, broken = _answer(conn, request)
            child.busy_since = None
            answers.put((child, request.seq, result))
            if broken is not None:
                # What follows on the pipe can no longer be told apart: replaced.
                reason = f"render process replaced: {broken}"
                return
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
        # A process whose start raised has no pid, and joining it raises, which would
        # end this thread before the reason below is handed over.
        if proc is not None and proc.pid is not None:
            proc.join()
            if reason == "render process died" and (proc.exitcode or 0) < 0:
                reason = _death(-(proc.exitcode or 0), memory_limit)
        if not child.abandoned:
            answers.put((child, None, reason))


def _answer(conn: Connection, request: _Request) -> tuple[Result, str | None]:
    """
    The child's reply to ``request``, read from ``conn``: the result, and why the
    pipe can no longer be trusted to be in step, or ``None`` when it can.

    A reply that is not well formed is that figure's ``Failed``, never an exception,
    and not transient: the same child given the same request would send the same.
    Only the pipe's own failures (``EOFError``, an ``OSError`` other than an
    over-long reply) leave here, as the child's death.
    """
    key = request.key
    try:
        blob = conn.recv_bytes(MAX_REPLY_BYTES)
    except OSError as exc:
        if "bad message length" not in str(exc):
            raise
        why = f"a reply over {MAX_REPLY_BYTES} bytes"
        return Failed(key, f"render reply refused: {why}"), why
    head, start = _head(blob)
    if head is None:
        why = "a reply that is not well formed"
        return Failed(key, f"render reply refused: {why}"), why
    said = head[0]
    if said == "err" and len(head) == 2 and isinstance(head[1], str):
        return Failed(key, reason_shown(head[1])), None
    if said == "laid" and len(head) == 1 and request.kind == _MERMAID:
        return received(key, blob, start), None
    if (
        said == "ok"
        and len(head) == 3
        and request.kind == _RASTER
        and all(type(v) is int and 1 <= v <= MAX_TEXTURE_SIDE for v in head[1:])
        and head[1] * head[2] <= MAX_RASTER_PIXELS
    ):
        w, h = head[1], head[2]
        try:
            rgba = conn.recv_bytes(MAX_RASTER_PIXELS * 4)
        except OSError as exc:
            if "bad message length" not in str(exc):
                raise
            why = "a raster over the pixel cap"
            return Failed(key, f"render reply refused: {why}"), why
        if len(rgba) != w * h * 4:
            return Failed(key, f"render reply refused: {len(rgba)} bytes is not {w}x{h} RGBA"), None
        return Rendered(key, w, h, rgba, request.px_width, request.scale), None
    why = f"an unexpected reply to a {request.kind} request"
    return Failed(key, f"render reply refused: {why}"), why


def _head(blob: bytes) -> tuple[list[Any] | None, int]:
    """
    A reply's first line as a JSON array whose first element is a string, and where
    what follows it starts; ``None`` where it is not one, or is over
    ``MAX_HEAD_BYTES``. Never raises.
    """
    end = blob.find(b"\n", 0, MAX_HEAD_BYTES + 1)
    if end < 0:
        end = len(blob)
    if end > MAX_HEAD_BYTES:
        return None, 0
    try:
        head = json.loads(blob[:end])
    except Exception:
        return None, 0
    if type(head) is not list or not head or type(head[0]) is not str:
        return None, 0
    return head, end + 1


def reason_shown(reason: str) -> str:
    """
    A reason the child gave, as a host may draw it: one line, at most
    ``MAX_REASON_CHARS``, and drawable (``pptmstr.drawable``: a NUL or a lone
    surrogate written out, never handed to ImGui).
    """
    line = " ".join(reason.split())
    if len(line) > MAX_REASON_CHARS:
        line = line[: MAX_REASON_CHARS - 1].rstrip() + "…"
    return drawable.text(line)


def received(key: Hashable, blob: bytes, start: int = 0) -> Laid | Failed:
    """
    What the I/O thread makes of a display list the child sent for ``key``, from
    ``start`` in ``blob``: a ``Laid`` if it reads as plain data and every bound and
    type holds (``displaylist.decode``), else a failure that is not transient, since
    the same diagram would be sent the same again.
    """
    got = displaylist.decode(blob, start)
    if isinstance(got, DisplayList):
        return Laid(key, got)
    return Failed(key, f"display list refused: {got}")


def _death(signum: int, memory_limit: int | None) -> str:
    """
    Why a child that died by ``signum`` stopped, as a host shows it.
    """
    if signum == signal.SIGABRT and memory_limit is not None and _limits_memory():
        return (
            f"render process aborted (signal {signum}), most likely at its"
            f" {memory_limit >> 20} MiB memory limit"
        )
    return f"render process killed by signal {signum}"


def _limits_memory() -> bool:
    """
    Whether ``RLIMIT_AS`` bounds a process here: Linux enforces it, macOS does not.
    """
    return sys.platform.startswith("linux")


def _limit_memory(limit: int | None) -> None:
    """
    Hold this process's address space to ``limit`` bytes where that is enforced.
    Never raises: without the limit, the render timeout is still a bound.
    """
    if limit is None or not _limits_memory():
        return
    try:
        import resource

        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        soft = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
    except (OSError, ValueError):
        pass


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


def child_main(conn: Connection, fonts: Fonts, memory_limit: int | None = None) -> None:
    """
    The render process: load the fonts once, then answer requests until the pipe closes.

    It says ``["ready", null]`` or ``["ready", reason]`` once. A child that could not
    load what rasterising needs answers every raster request with that reason rather
    than exiting, so a missing library is one failure per figure and not a restart
    per frame; it still lays Mermaid out, which needs none of it. Its address space
    is held to ``memory_limit`` first (``MEMORY_LIMIT_BYTES``).

    Every reply is one ``send_bytes`` whose first line is a JSON array: ``["laid"]``
    and the display list's lines after it (``displaylist.encode``), ``["err",
    reason]``, or ``["ok", width, height]`` followed by a second message of RGBA.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _limit_memory(memory_limit)
    failure: str | None = None
    options: dict[str, Any] = {}
    try:
        options = resvg_options(fonts)
        for module in CHILD_MODULES.values():
            importlib.import_module(module)
    except Exception as exc:
        failure = f"renderer unavailable: {_one_line(exc)}"
    ratio = em_ratio(fonts.sans)
    _say(conn, ["ready", None if failure is None else _cut(failure)])
    while True:
        try:
            kind, width, svg = json.loads(conn.recv_bytes())
        except EOFError:
            return
        if kind == _MERMAID:
            _say(conn, *_laid(svg, ratio))
            continue
        if failure is not None:
            _say(conn, ["err", _cut(failure)])
            continue
        try:
            w, h, rgba = rasterise(svg, width, options)
        except Exception as exc:
            _say(conn, ["err", _cut(_one_line(exc))])
            continue
        _say(conn, ["ok", w, h])
        conn.send_bytes(rgba)


def _say(conn: Connection, head: list[Any], body: bytes = b"") -> None:
    line = json.dumps(head).encode("ascii")
    conn.send_bytes(line + b"\n" + body if body else line)


def _cut(reason: str) -> str:
    return reason if len(reason) <= MAX_REASON_CHARS else reason[: MAX_REASON_CHARS - 1] + "…"


def _laid(source: str, ratio: float = 1.0) -> tuple[list[Any], bytes]:
    """
    The child's answer to a mermaid request, as a head and a body: ``["laid"]`` and
    the display list on the wire, or ``["err", reason]`` and nothing. merman is
    imported here, on the first diagram, and only here.
    """
    from .displaylist import Refusal, encode, from_mermaid

    try:
        from . import mermaid

        outcome = mermaid.layout(source)
    except Exception as exc:
        return ["err", _cut(f"mermaid layout failed: {_one_line(exc)}")], b""
    if not isinstance(outcome, Sanitised):
        return ["err", _cut(outcome.reason)], b""
    try:
        return ["laid"], encode(from_mermaid(source, outcome.svg, ratio))
    except Refusal as refusal:
        return ["err", _cut(f"mermaid not drawable: {refusal}")], b""
    except Exception as exc:
        return ["err", _cut(f"mermaid not drawable: {_one_line(exc)}")], b""


DEFAULT_EM_RATIO = 2048 / 2400
"""
The em of the prose face pptmstr ships (Roboto: 2048 units to the em, hhea ascent
1900 and descent -500) per unit of ImGui font size: what ``em_ratio`` answers when
the face cannot be read.
"""


def em_ratio(path: str) -> float:
    """
    The face at ``path``'s em per unit of ImGui font size. ImGui sizes a font by its
    hhea ascent less descent, and SVG's ``font-size`` is an em, so a label set at
    merman's number would come out smaller than merman laid it out for. The same
    number imscribe's ``em_size_for_pixel_height`` gives (0.853 for Roboto, measured
    2026-10-22). Read in the child, where fontTools is loaded for resvg already.
    """
    try:
        from fontTools.ttLib import TTFont

        with TTFont(path, lazy=True) as font:
            units = float(font["head"].unitsPerEm)
            tall = float(font["hhea"].ascent - font["hhea"].descent)
        ratio = units / tall
    except Exception:
        return DEFAULT_EM_RATIO
    return ratio if math.isfinite(ratio) and 0.25 <= ratio <= 4.0 else DEFAULT_EM_RATIO


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
    the root implies would put it over ``MAX_RASTER_PIXELS`` or ``MAX_TEXTURE_SIDE``.
    """
    import resvg_py

    height = _implied_height(svg, width)
    _check_raster(width, height)
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
    _check_raster(w, h)
    with Image.open(BytesIO(png)) as image:
        rgba = image.convert("RGBA")
        return rgba.width, rgba.height, rgba.tobytes()


def _check_raster(width: int, height: int) -> None:
    if width < 1 or width * height > MAX_RASTER_PIXELS:
        raise ValueError(f"{width}x{height} is over the {MAX_RASTER_PIXELS}-pixel cap")
    if max(width, height) > MAX_TEXTURE_SIDE:
        raise ValueError(f"{width}x{height} has a side over {MAX_TEXTURE_SIDE} px")


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
    "CHILD_MODULES",
    "DISTRIBUTIONS",
    "Failed",
    "Fonts",
    "Laid",
    "MAX_RASTER_PIXELS",
    "MAX_TEXTURE_SIDE",
    "MEMORY_LIMIT_BYTES",
    "RENDER_TIMEOUT_S",
    "UPLOAD_MODULES",
    "Rendered",
    "Renderer",
    "Result",
    "rasterise",
    "renderer",
    "resvg_options",
    "shutdown",
)
