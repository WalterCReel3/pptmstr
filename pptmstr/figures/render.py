"""
Mermaid source to a bounded display list, in a long-lived child process, without
the frame waiting.

**A process, not a thread.** merman is native code, and what it and this package's
parsers read is model-written text: a crash, a hang or a runaway allocation in
either is the child's, which is killed and replaced, and never the GUI's. The child
is started with the ``spawn`` context, because the GUI process has a GL context and
threads and ``fork`` copies both. It is started lazily, by the first ``lay_out``,
and lives until it hangs, dies or ``shutdown`` ends it.

**This is where a figure is built over more than one frame**, the one exception to
``views.py``'s stance that nothing is. A host asks, keeps drawing what it has, and
picks up the display list from ``poll`` in a later frame.

**What may block, and where.** The frame thread calls ``lay_out`` and ``poll``
only, and neither waits on the child: each child has one I/O thread here that
starts the process, writes requests and reads results, and that thread is the only
one that blocks on the pipe. ``poll`` reads what that thread queued, and kills a
child whose request has run past ``RENDER_TIMEOUT_S``; the next ``lay_out`` starts
another. Neither raises. ``shutdown`` is for application exit and does wait,
boundedly.

**One request at a time, coalesced per key.** The child works serially, so a
second request for a key whose first has not gone out replaces it. A result for a
key that has a newer request waiting is dropped, not delivered.

**What the child answers.** A ```` ```mermaid ```` source is laid out by merman
(``mermaid.layout``), its SVG sanitised and parsed into what the pane draws
(``displaylist.from_mermaid``), and the child answers a ``Laid``: a bounded display
list with the diagram's natural size, as plain JSON. Nothing the child sends is
unpickled (``_serve``). The I/O thread reads it a record at a time and checks every
bound of it again (``displaylist.decode``) before the frame thread sees it. The
pane draws the list natively (``host``); nothing is rasterised anywhere.
"""

from __future__ import annotations

import json
import math
import queue
import signal
import sys
import threading
import time
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from multiprocessing import get_context
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

from .. import drawable
from . import displaylist
from .displaylist import DisplayList
from .sanitise import Sanitised

RENDER_TIMEOUT_S = 10.0
"""
How long one request may take before its child is killed.

A layout, sanitise and parse took 56-446 ms in the child here, the largest
diagrams merman or the sanitiser's size cap still admit and the stress inputs they
refuse alike (2026-10-22: a 100-node chain 229 ms, a 60-node, 120-edge graph 244
ms, a 400-node chain refused by merman's own limit after 446 ms;
``notes/vector-rendering/probe-displaylist.md`` §2 has 151-362 ms on the same
kinds). The operator's machine ran the earlier probes about 1.6 times slower than
this one (``probe-m1-r2.md``), so ten seconds is more than ten times the worst
legitimate case. A request past it is a hang or hostile input. The cost of a longer
limit is that every other figure waits behind a stuck one for that long.
"""

START_TIMEOUT_S = 20.0
"""
How long a child may take to start and read its face before it is killed.

Spawning a fresh interpreter and importing this package's figure modules is well
under a second; a box under load, or a cold disk cache, can be several times that.
merman is imported later, by the first diagram (``_laid``).
"""

MEMORY_LIMIT_BYTES = 1 << 30
"""
The address space a render child may have, on Linux, where ``RLIMIT_AS`` is enforced.

What it bounds is what the other bounds do not foresee: an allocation in merman,
which is native code, or in a parse of its output. The diagrams above peaked at
39-42 MiB resident in the child (2026-10-22), so a gibibyte of address space is far
past any legitimate layout and still stops a runaway one well short of the machine.
Past it an allocation fails: in native code the child aborts, which the I/O thread
reports as that request's failure and the next request replaces the child; in
Python it is answered as that request's error.

macOS does not enforce ``RLIMIT_AS``, so there nothing bounds a child's memory but
``RENDER_TIMEOUT_S``.
"""

CHILD_MODULES: dict[str, str] = {"fonttools": "fontTools.ttLib"}
"""
What the render child imports for any figure beyond merman, by the distribution
that provides it: fontTools reads the prose face's metrics (``em_ratio``). The
child does without it, at ``DEFAULT_EM_RATIO``, so a diagram is still drawn, its
labels sized for pptmstr's own face.
"""

DISTRIBUTIONS: tuple[str, ...] = tuple(CHILD_MODULES)
"""
Every distribution a figure needs beyond the typeset library and merman: what the
table of drawn fences asks is installed (``fences.RENDERED``).
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
class Failed:
    """
    A render of ``key`` that produced nothing, with the one-line reason a host shows.

    ``transient`` is whether the same request may succeed if asked again: the child
    died, was killed for time or did not start, which the machine's state can cause.
    An answer the child gave (merman refused the diagram, a cap was hit) is not
    transient: it would be the same again.
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


Result = Laid | Failed


@dataclass(frozen=True, slots=True)
class Fonts:
    """
    The faces figures are set in, as the typeset seam names them
    (``views.figure_fonts``). The child reads ``sans``'s metrics (``em_ratio``);
    the pane sets labels in its own prose face. ``files`` and ``mono`` are what that
    seam reports and nothing here reads them.
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


_MERMAID = "mermaid"


@dataclass(frozen=True, slots=True)
class _Request:
    """
    One request to the child: ``source`` is a ```` ```mermaid ```` fence's text.
    """

    seq: int
    key: Hashable
    source: str


@dataclass(eq=False)
class _Child:
    """
    One render process and the I/O thread that is the only thing to block on it.

    ``proc`` is set by that thread once the process has started. ``abandoned`` is set
    by the frame thread when it gives the child up; whichever of the two sees both
    kills the process, so a child given up while it is starting is killed too.

    ``ending`` is set by the I/O thread before it hands back a result after which it
    serves nothing more. The reason it stops is queued only later, on its way out,
    so the frame thread reads ``ending`` with that result to drop the child at once:
    a request dispatched to it in between would be failed for the previous request's
    reply.
    """

    requests: queue.SimpleQueue[_Request | None] = field(default_factory=queue.SimpleQueue)
    proc: BaseProcess | None = None
    started_at: float = field(default_factory=time.monotonic)
    ready: bool = False
    busy_since: float | None = None
    abandoned: bool = False
    ending: bool = False
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
    Ask for a diagram's layout by key; poll for display lists. Neither call blocks
    nor raises.

    Construct it anywhere: nothing starts until the first ``lay_out``. ``fonts`` is
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

    def lay_out(self, key: Hashable, source: str) -> None:
        """
        Ask for a ```` ```mermaid ```` fence's ``source`` laid out in the child, under
        ``key``: answered with a ``Laid``, or a ``Failed`` carrying merman's reason.
        Replaces any request for ``key`` that has not gone to the child; one the same
        as what the child is laying out for ``key`` now is not asked twice. Never
        blocks or raises.
        """
        try:
            flying = self._inflight
            self._pending.pop(key, None)
            if not (flying is not None and flying.key == key and flying.source == source):
                self._seq += 1
                self._pending[key] = _Request(self._seq, key, source)
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
            if child.ending:
                self._drop_child()
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
            conn.send_bytes(json.dumps([_MERMAID, request.source]).encode("ascii"))
            result, broken = _answer(conn, request)
            child.busy_since = None
            if broken is not None:
                # What follows on the pipe can no longer be told apart: replaced.
                child.ending = True
                reason = f"render process replaced: {broken}"
            answers.put((child, request.seq, result))
            if broken is not None:
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
    if said == "laid" and len(head) == 1:
        return received(key, blob, start), None
    why = "an unexpected reply to a layout request"
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
    The render process: read the face's metrics once, then answer requests until the
    pipe closes. Its address space is held to ``memory_limit`` first
    (``MEMORY_LIMIT_BYTES``).

    It says ``["ready", null]`` once. Every reply is one ``send_bytes`` whose first
    line is a JSON array: ``["laid"]`` and the display list's lines after it
    (``displaylist.encode``), or ``["err", reason]``.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _limit_memory(memory_limit)
    ratio = em_ratio(fonts.sans)
    _say(conn, ["ready", None])
    while True:
        try:
            request = json.loads(conn.recv_bytes())
        except EOFError:
            return
        if (
            type(request) is list
            and len(request) == 2
            and request[0] == _MERMAID
            and isinstance(request[1], str)
        ):
            _say(conn, *_laid(request[1], ratio))
        else:
            _say(conn, ["err", "not a layout request"])


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
    2026-10-22). Read in the child, once, when it starts.
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


_RENDERER: Renderer | None = None


def renderer() -> Renderer:
    """
    The process's renderer, made on first use. Starting it costs nothing until a
    diagram is asked for.
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
    "MEMORY_LIMIT_BYTES",
    "RENDER_TIMEOUT_S",
    "Renderer",
    "Result",
    "renderer",
    "shutdown",
)
