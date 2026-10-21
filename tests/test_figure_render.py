"""
The figure render process: real children, real resvg, no mocks.

Every test that renders through ``Renderer`` spawns a process, which costs about a
second, so most share one module-scoped renderer and only the tests that kill or
time out a child make their own.
"""

from __future__ import annotations

import math
import multiprocessing
import os
import signal
import time
from collections.abc import Hashable, Iterator

import pytest

from pptmstr.figures.render import (
    MAX_RASTER_PIXELS,
    Failed,
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
