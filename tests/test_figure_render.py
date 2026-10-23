"""
The figure render process: real children, real resvg, no mocks.

Every test that renders through ``Renderer`` spawns a process, which costs about a
second, so most share one module-scoped renderer and only the tests that kill or
time out a child make their own.
"""

from __future__ import annotations

import ast
import json
import math
import multiprocessing
import os
import pickle
import signal
import sys
import time
from collections.abc import Hashable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from pptmstr import drawable
from pptmstr.figures import displaylist, render
from pptmstr.figures.render import (
    MAX_RASTER_PIXELS,
    MAX_TEXTURE_SIDE,
    MEMORY_LIMIT_BYTES,
    Failed,
    Fonts,
    Rendered,
    Renderer,
    Result,
    imscribe_fonts,
    raster_size,
    render_png,
    resvg_options,
)
from pptmstr.figures.sanitise import Sanitised, sanitise

SVG_NS = "http://www.w3.org/2000/svg"


def raw(body: str, w: int = 200, h: int = 60) -> Sanitised:
    """
    ``body`` in a root of natural size ``w`` x ``h``, not passed through the sanitiser,
    so a test can hold what the renderer does with a font list the sanitiser would
    have appended to.
    """
    svg = f'<svg xmlns="{SVG_NS}" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{body}</svg>'
    return Sanitised(svg=svg, width=float(w), height=float(h))


def sanitised(body: str, w: int = 200, h: int = 60) -> Sanitised:
    out = sanitise(raw(body, w, h).svg)
    assert isinstance(out, Sanitised), out
    return out


def grid(labels: int = 1200) -> Sanitised:
    """
    The probes' 1,200-label grid: about 1.1 s at 4096 px wide on this machine.
    """
    cells = "".join(
        f"<rect x='{i % 40 * 20}' y='{i // 40 * 20}' width='18' height='18' fill='#4a7'/>"
        f"<text x='{i % 40 * 20}' y='{i // 40 * 20 + 14}' font-size='10'>n{i}</text>"
        for i in range(labels)
    )
    return sanitised(cells, 800, 600)


def ink(result: Result) -> int:
    assert isinstance(result, Rendered), result
    alpha = result.rgba[3::4]
    return len(alpha) - alpha.count(0)


def wait_for(renderer: Renderer, key: Hashable, seconds: float = 30.0) -> Result:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for result in renderer.poll():
            if result.key == key:
                return result
        time.sleep(0.005)
    raise AssertionError(f"no result for {key!r} in {seconds} s")


def gone(pid: int, seconds: float = 5.0, polling: Renderer | None = None) -> bool:
    """
    Whether ``pid`` has exited and been reaped within ``seconds``, calling
    ``polling.poll()`` meanwhile as a frame would: a child that dies while idle is
    noticed, and reaped, there.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if polling is not None:
            polling.poll()
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(scope="module")
def shared() -> Iterator[Renderer]:
    renderer = Renderer()
    yield renderer
    renderer.shutdown()


TEXT = '<text x="10" y="40" font-size="24"{}>Figure text</text>'
GENERICS = ["sans-serif", "serif", "monospace", "cursive", "fantasy"]


# The mapping, in this process: resvg itself, without Pillow or a child ---------------


@pytest.mark.parametrize("family", [None, *GENERICS, "Arial"])
def test_the_font_options_make_resvg_draw_text_in_each_family(family: str | None) -> None:
    options = resvg_options(imscribe_fonts())
    attr = "" if family is None else f' font-family="{family}"'
    blank = render_png(raw("").svg, 200, options)
    assert render_png(raw(TEXT.format(attr)).svg, 200, options) != blank


def test_monospace_is_set_in_the_code_face_and_everything_else_in_the_prose_face() -> None:
    """
    Ink alone holds only ``serif_family``: resvg falls back to it for any family it
    cannot match, so with the others unmapped every generic but monospace still
    draws in the same face. Which face each generic lands in is what holds
    ``monospace_family``.
    """
    fonts = imscribe_fonts()
    options = resvg_options(fonts)
    code, prose = options["monospace_family"], options["sans_serif_family"]
    assert code != prose

    def png(family: str) -> bytes:
        return render_png(raw(TEXT.format(f' font-family="{family}"')).svg, 200, options)

    assert png("monospace") == png(code) != png(prose)
    for family in ("sans-serif", "serif", "cursive", "fantasy", "Arial"):
        assert png(family) == png(prose), family


def test_the_child_refuses_a_raster_over_the_cap_before_rendering() -> None:
    side = math.isqrt(MAX_RASTER_PIXELS)
    tall = raw("", 100, 200).svg
    with pytest.raises(ValueError, match="pixel cap"):
        # resvg_options is not needed: the refusal comes before resvg is called.
        render_png(tall, side, {})


def test_a_request_is_shrunk_to_the_pixel_cap_at_the_natural_aspect() -> None:
    w, h = raster_size(raw("", 400, 300), 100_000, 2.0)
    assert w * h <= MAX_RASTER_PIXELS
    assert abs(h / w - 0.75) < 0.01
    assert raster_size(raw("", 400, 300), 150, 2.0) == (300, 225)
    assert raster_size(raw("", 400, 300), float("nan"), 1.0) == (1, 1)


@pytest.mark.parametrize(
    "natural, px_width, scale",
    [
        ((2, 16384), 2.0, 1.0),
        ((1000, 16384), 800.0, 2.0),
        ((16384, 1), 16384.0, 2.0),
        ((3, 16383), 3.0, 2.0),
        ((7, 16381), 7.0, 3.0),
        ((800, 3000), 800.0, 2.0),
    ],
)
def test_a_raster_has_no_side_over_the_texture_side_at_any_density(
    natural: tuple[int, int], px_width: float, scale: float
) -> None:
    w, h = raster_size(raw("", *natural), px_width, scale)
    assert max(w, h) <= MAX_TEXTURE_SIDE
    assert w * h <= MAX_RASTER_PIXELS


def test_a_long_figure_at_density_two_is_shrunk_to_the_side() -> None:
    # 1600x26215 before the side was held after density: under the pixel cap, over
    # what llvmpipe takes (16384), and blank at upload.
    assert raster_size(raw("", 1000, 16384), 800.0, 2.0) == (500, 8192)


def test_a_one_by_16384_figure_at_density_two_is_refused_by_the_child() -> None:
    # 2x32768 before the side was held. At one pixel wide it is still 16384 tall,
    # over the side, so the child refuses it rather than make a texture that fails.
    one = raw("", 1, 16384)
    w, h = raster_size(one, 1.0, 2.0)
    assert (w, h) == (1, 16384)
    with pytest.raises(ValueError, match="side over"):
        render_png(one.svg, w, {})


# Through the child ------------------------------------------------------------------


@pytest.mark.parametrize("family", [None, *GENERICS])
def test_a_text_in_each_generic_family_produces_ink(shared: Renderer, family: str | None) -> None:
    attr = "" if family is None else f' font-family="{family}"'
    shared.submit(("generic", family), raw(TEXT.format(attr)), 200, 1.0)
    assert ink(wait_for(shared, ("generic", family))) > 100


def test_a_named_family_produces_ink_after_sanitise_and_without_its_append(
    shared: Renderer,
) -> None:
    body = TEXT.format(' font-family="Arial"')
    after = sanitised(body)
    assert "sans-serif" in after.svg
    shared.submit("named-after", after, 200, 1.0)
    # Without the sanitiser's append, it is serif_family's mapping that draws it (R1c).
    shared.submit("named-bare", raw(body), 200, 1.0)
    assert ink(wait_for(shared, "named-after")) > 100
    assert ink(wait_for(shared, "named-bare")) > 100


def test_a_render_returns_the_requested_width(shared: Renderer) -> None:
    shared.submit("width", sanitised('<rect width="200" height="60" fill="#c33"/>'), 150, 2.0)
    result = wait_for(shared, "width")
    assert isinstance(result, Rendered)
    assert (result.width, result.height) == (300, 90)
    assert len(result.rgba) == 300 * 90 * 4
    assert (result.px_width, result.scale) == (150, 2.0)
    assert result.rgba[:4] == bytes((0xCC, 0x33, 0x33, 0xFF))


def test_only_the_latest_request_for_a_key_is_answered(shared: Renderer) -> None:
    figure = sanitised('<rect width="200" height="60" fill="#36c"/>')
    busy = grid()
    # The first goes to the child at once, so its answer arrives with a newer
    # request for its key waiting and is dropped; the second waits behind "busy"
    # and is replaced by the third before it goes.
    shared.submit("latest", figure, 100, 1.0)
    shared.submit("busy", busy, 1600, 1.0)
    shared.submit("latest", figure, 120, 1.0)
    shared.submit("latest", figure, 140, 1.0)
    seen: list[Result] = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not any(r.key == "latest" for r in seen):
        seen.extend(shared.poll())
        time.sleep(0.005)
    latest = [r for r in seen if r.key == "latest"]
    assert [r.width for r in latest if isinstance(r, Rendered)] == [140]
    time.sleep(0.2)
    assert not [r for r in shared.poll() if r.key == "latest"]


def test_a_render_over_the_time_limit_is_killed_and_reported_and_the_next_succeeds() -> None:
    renderer = Renderer(timeout_s=0.3)
    try:
        renderer.submit("warm", sanitised('<rect width="10" height="10"/>'), 50, 1.0)
        assert isinstance(wait_for(renderer, "warm"), Rendered)
        pid = renderer.pid
        assert pid is not None
        renderer.submit("slow", grid(), 4096, 1.0)
        result = wait_for(renderer, "slow")
        assert isinstance(result, Failed)
        assert "longer than 0.3 s" in result.reason
        assert result.transient
        assert gone(pid)
        renderer.submit("after", sanitised('<rect width="10" height="10"/>'), 50, 1.0)
        assert isinstance(wait_for(renderer, "after"), Rendered)
        assert renderer.pid not in (None, pid)
    finally:
        renderer.shutdown()


def test_a_killed_child_is_replaced() -> None:
    renderer = Renderer()
    figure = sanitised('<rect width="10" height="10"/>')
    try:
        renderer.submit("first", figure, 50, 1.0)
        assert isinstance(wait_for(renderer, "first"), Rendered)
        idle = renderer.pid
        assert idle is not None
        os.kill(idle, signal.SIGKILL)
        assert gone(idle, polling=renderer)
        renderer.submit("after-idle-kill", figure, 50, 1.0)
        assert isinstance(wait_for(renderer, "after-idle-kill"), Rendered)

        busy = renderer.pid
        assert busy not in (None, idle)
        renderer.submit("killed-mid-render", grid(), 4096, 1.0)
        time.sleep(0.2)
        os.kill(busy, signal.SIGKILL)
        result = wait_for(renderer, "killed-mid-render")
        assert isinstance(result, Failed)
        assert "killed by signal 9" in result.reason
        assert result.transient
        renderer.submit("after-busy-kill", figure, 50, 1.0)
        assert isinstance(wait_for(renderer, "after-busy-kill"), Rendered)
        assert renderer.pid not in (None, idle, busy)
    finally:
        renderer.shutdown()


def test_submit_and_poll_return_at_once_while_a_child_starts_and_renders() -> None:
    renderer = Renderer()
    try:
        figure = grid()
        start = time.perf_counter()
        renderer.submit("slow", figure, 4096, 1.0)
        submitted = time.perf_counter()
        first = renderer.poll()
        polled = time.perf_counter()
        # The submit started the I/O thread, which spawns the child; neither waited.
        assert submitted - start < 0.02
        assert polled - submitted < 0.02
        assert first == []
        slowest = 0.0
        results: list[Result] = []
        deadline = time.monotonic() + 30
        while not results and time.monotonic() < deadline:
            t = time.perf_counter()
            results = renderer.poll()
            slowest = max(slowest, time.perf_counter() - t)
            time.sleep(0.001)
        assert [r.key for r in results] == ["slow"]
        assert isinstance(results[0], Rendered)
        # The 48 MiB of RGBA came across in the I/O thread; poll only took it.
        assert slowest < 0.02
    finally:
        renderer.shutdown()


def test_submit_and_poll_do_not_raise_on_nonsense() -> None:
    renderer = Renderer(fonts=lambda: (_ for _ in ()).throw(OSError("no fonts")))
    try:
        renderer.submit(["unhashable"], raw(""), 100, 1.0)  # type: ignore[arg-type]
        renderer.submit("nan", raw(""), float("nan"), float("inf"))
        reasons = [r for r in renderer.poll() if isinstance(r, Failed)]
        assert any("render not started" in r.reason for r in reasons)
        result = wait_for(renderer, "nan")
        assert isinstance(result, Failed)
        assert "no fonts" in result.reason
    finally:
        renderer.shutdown()


def test_shutdown_leaves_no_child() -> None:
    renderer = Renderer()
    renderer.submit("one", sanitised('<rect width="10" height="10"/>'), 50, 1.0)
    assert isinstance(wait_for(renderer, "one"), Rendered)
    pid = renderer.pid
    assert pid is not None
    renderer.submit("abandoned", grid(), 4096, 1.0)
    renderer.shutdown()
    assert gone(pid, 0.5)
    assert renderer.pid is None
    assert pid not in {child.pid for child in multiprocessing.active_children()}


def test_an_answer_the_child_gives_is_a_failure_that_is_not_transient(shared: Renderer) -> None:
    sliver = Sanitised(
        svg=f'<svg xmlns="{SVG_NS}" width="0.5" height="16384"/>', width=0.5, height=16384.0
    )
    shared.submit("sliver", sliver, 0.5, 2.0)
    result = wait_for(shared, "sliver")
    assert isinstance(result, Failed)
    assert "side over" in result.reason
    assert not result.transient


def _pattern(extra: str) -> Sanitised:
    """
    A 1600x1200 figure filled from one pattern tile, which resvg allocates whole at
    its size under every transform above it. Not sanitised: the sanitiser drops
    ``<pattern>`` for this reason, and what is held here is the child's own limit.
    """
    full = 'width="1600" height="1200"'
    return raw(
        f'<defs><pattern id="p" width="100" height="100" patternUnits="userSpaceOnUse"{extra}>'
        f'<rect width="100" height="100" fill="#c33"/></pattern></defs>'
        f'<rect {full} fill="url(#p)"/>',
        1600,
        1200,
    )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is enforced on Linux")
def test_a_render_over_the_memory_limit_is_a_transient_failure_and_the_child_is_replaced() -> None:
    # A 16384-px tile, 1 GiB of RGBA: rendered without the limit (probe R3, 1088 MB).
    assert 16384 * 16384 * 4 >= MEMORY_LIMIT_BYTES
    renderer = Renderer()
    try:
        renderer.submit("small", _pattern(""), 160, 1.0)
        assert isinstance(wait_for(renderer, "small"), Rendered)
        pid = renderer.pid
        renderer.submit("huge", _pattern(' patternTransform="scale(163.84)"'), 1600, 1.0)
        result = wait_for(renderer, "huge")
        assert isinstance(result, Failed), "rendered: the child's memory is not limited"
        assert result.transient
        assert "memory limit" in result.reason
        renderer.submit("after", _pattern(""), 160, 1.0)
        assert isinstance(wait_for(renderer, "after"), Rendered)
        assert renderer.pid not in (None, pid)
    finally:
        renderer.shutdown()


def test_a_child_that_cannot_be_started_is_reported_at_once() -> None:
    class Unpicklable(Fonts):
        def __reduce__(self) -> str:
            raise TypeError("cannot be sent to a child")

    renderer = Renderer(fonts=lambda: Unpicklable((), "", ""), start_timeout_s=30.0)
    try:
        renderer.submit("unstarted", raw(""), 100, 1.0)
        result = wait_for(renderer, "unstarted", seconds=5.0)
        assert isinstance(result, Failed)
        assert "cannot be sent" in result.reason
        assert result.transient
    finally:
        renderer.shutdown()


# -- what the child sends is plain data, read without evaluating it -----------------------

DRAWN_LIST = displaylist.DisplayList(
    40.0,
    20.0,
    (0.0, 0.0),
    (displaylist.TextRun("label", 1.0, 10.0, 14.0, 0, 0, displaylist.LABEL, False, 0.0),),
    (),
    (),
)


class _Opens:
    """
    What a pickled reply could do if the GUI process unpickled it: here, create a file.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def __reduce__(self) -> tuple[Any, ...]:
        return (open, (self.path, "w"))


def _hostile_reply(source: str) -> list[bytes]:
    """
    The messages a taken-over child sends for a request whose source is ``source``.
    """
    head = b'["laid"]\n'
    good = displaylist.encode(DRAWN_LIST)
    if source.startswith("pickle:"):
        return [pickle.dumps(("laid", _Opens(source.removeprefix("pickle:"))))]
    run = DRAWN_LIST.items[0]
    assert isinstance(run, displaylist.TextRun)
    surrogate = replace(DRAWN_LIST, items=(replace(run, text="a\udc80b"),))
    return {
        "good": [head + good],
        "not json": [b"\xff\xfe\x00 not json"],
        "head not a list": [b'{"laid": true}\n' + good],
        "nan": [head + good.replace(b"10.0,14.0", b"NaN,14.0")],
        "surrogate": [head + displaylist.encode(surrogate)],
        "bool for int": [head + good.replace(b"14.0,0,0,4", b"14.0,false,0,4")],
        "undrawable reason": [json.dumps(["err", "bad \udc80\x00 reason"]).encode()],
        "too long": [b'["laid"]\n' + b" " * 20_000],
        "ok to a layout": [b'["ok", 1, 1]', b"\x00\x00\x00\x00"],
    }[source]


def _hostile_child(conn: Any, fonts: Fonts, memory_limit: int | None = None) -> None:
    """
    A render child that has been taken over: it says it is ready, then answers each
    request with what its source names (``_hostile_reply``).
    """
    conn.send_bytes(b'["ready", null]')
    while True:
        try:
            _, _, source = json.loads(conn.recv_bytes())
        except EOFError:
            return
        for message in _hostile_reply(source):
            conn.send_bytes(message)


def test_a_reply_that_is_not_well_formed_is_that_figures_failure_and_never_unpickled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Each malformed reply comes back from ``poll`` as a ``Failed`` for its figure and
    not transient, and nothing raises. A reply the pipe can no longer be read past
    (not JSON, over the length read, a head that is not one) also replaces the
    child; one that read whole but failed its checks does not. A pickled reply
    whose unpickling would create a file creates none.
    """
    monkeypatch.setattr(render, "MAX_REPLY_BYTES", 10_000)
    marker = tmp_path / "unpickled"
    renderer = Renderer(fonts=lambda: Fonts((), "", ""), child=_hostile_child)
    try:
        renderer.lay_out("good", "good")
        assert isinstance(wait_for(renderer, "good"), render.Laid)
        expected = {
            "not json": ("render reply refused: a reply that is not well formed", True),
            f"pickle:{marker}": ("render reply refused: a reply that is not well formed", True),
            "head not a list": ("render reply refused: a reply that is not well formed", True),
            "too long": ("render reply refused: a reply over 10000 bytes", True),
            "ok to a layout": (
                "render reply refused: an unexpected reply to a mermaid request",
                True,
            ),
            "nan": ("display list refused: coordinate", False),
            "surrogate": ("display list refused: text content", False),
            "bool for int": ("display list refused: a field of the wrong type", False),
            "undrawable reason": ("bad \\udc80\\u0000 reason", False),
        }
        good = _hostile_reply("good")[0]
        assert all(_hostile_reply(source)[0] != good for source in expected)
        for source, (reason, replaced) in expected.items():
            pid = renderer.pid
            renderer.lay_out(source, source)
            got = wait_for(renderer, source)
            assert got == Failed(source, reason), source
            renderer.lay_out("good", "good")
            assert isinstance(wait_for(renderer, "good"), render.Laid), source
            assert (renderer.pid != pid) is replaced, source
        assert not marker.exists(), "the GUI process unpickled what the child sent"
    finally:
        renderer.shutdown()


def test_nothing_on_the_receiving_side_of_the_pipe_can_evaluate_what_it_reads() -> None:
    """
    The modules that read the child's replies import no pickle and call no
    ``Connection.recv`` (which unpickles), and every ``loads`` they call is JSON's.
    """
    for module in (render, displaylist):
        tree = ast.parse(Path(module.__file__ or "").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not any(
                    n.split(".")[0] in ("pickle", "marshal", "shelve", "dill") for n in names
                ), (module.__name__, names)
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute):
                    assert fn.attr != "recv", (module.__name__, node.lineno)
                    if fn.attr == "loads":
                        assert isinstance(fn.value, ast.Name) and fn.value.id == "json", (
                            module.__name__,
                            node.lineno,
                        )
                if isinstance(fn, ast.Name):
                    assert fn.id not in ("eval", "exec", "__import__"), (module.__name__, fn.id)


def test_a_reason_the_child_gives_is_one_drawable_line_within_the_bound() -> None:
    shown = render.reason_shown("first\nsecond \udc80 \x00" + "x" * 1000)
    assert shown.startswith("first second \\udc80 \\u0000xx")
    assert "\n" not in shown and shown.endswith("…")
    assert len(shown) <= render.MAX_REASON_CHARS + len("\\udc80\\u0000") - 2
    assert not drawable.undrawable(shown)
