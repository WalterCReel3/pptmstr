"""
The figure render process: real children, no mocks of the process or the pipe.

The lifecycle tests run a scripted child (``_scripted_child``) in place of
``child_main``, so a slow, aborting or allocating request is one whose source says
so and no test depends on merman being installed or on how long it takes; the
real child's layouts are held in tests/test_figure_mermaid.py. Every test spawns a
process, which costs about a second.
"""

from __future__ import annotations

import ast
import json
import multiprocessing
import os
import pickle
import signal
import sys
import time
from collections.abc import Hashable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from pptmstr import drawable
from pptmstr.figures import displaylist, render
from pptmstr.figures.render import MEMORY_LIMIT_BYTES, Failed, Fonts, Laid, Renderer, Result


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


def _list_saying(text: str) -> displaylist.DisplayList:
    """
    A display list whose one run says ``text``: how a scripted answer names the
    request it answers.
    """
    run = displaylist.TextRun(text[:200], 1.0, 10.0, 14.0, 0, 0, displaylist.LABEL, False, 0.0)
    return displaylist.DisplayList(40.0, 20.0, (0.0, 0.0), (run,), (), ())


def _scripted_child(conn: Any, fonts: Fonts, memory_limit: int | None = None) -> None:
    """
    A render child that answers each request by what its source says, under the
    real child's memory limit: ``sleep:<s>`` lays out after that long, ``alloc:<n>``
    asks for ``n`` bytes and aborts if it cannot have them, as native code does,
    ``err:<reason>`` refuses, and anything else is laid out as a list saying it.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    render._limit_memory(memory_limit)
    conn.send_bytes(b'["ready", null]')
    while True:
        try:
            _, source = json.loads(conn.recv_bytes())
        except EOFError:
            return
        if source.startswith("sleep:"):
            time.sleep(float(source.removeprefix("sleep:")))
        elif source.startswith("alloc:"):
            try:
                held = bytearray(int(source.removeprefix("alloc:")))
            except MemoryError:
                os.abort()
            del held
        elif source.startswith("err:"):
            conn.send_bytes(json.dumps(["err", source.removeprefix("err:")]).encode())
            continue
        conn.send_bytes(b'["laid"]\n' + displaylist.encode(_list_saying(source)))


def scripted(**kwargs: Any) -> Renderer:
    return Renderer(fonts=lambda: Fonts((), "", ""), child=_scripted_child, **kwargs)


def said(result: Result) -> str:
    assert isinstance(result, Laid), result
    run = result.display.items[0]
    assert isinstance(run, displaylist.TextRun)
    return run.text


# The process --------------------------------------------------------------------------


def test_only_the_latest_request_for_a_key_is_answered() -> None:
    renderer = scripted()
    try:
        # The first goes to the child at once, so its answer arrives with a newer
        # request for its key waiting and is dropped; the second waits behind "busy"
        # and is replaced by the third before it goes.
        renderer.lay_out("latest", "first")
        renderer.lay_out("busy", "sleep:0.3")
        renderer.lay_out("latest", "second")
        renderer.lay_out("latest", "third")
        seen: list[Result] = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not any(r.key == "latest" for r in seen):
            seen.extend(renderer.poll())
            time.sleep(0.005)
        assert [said(r) for r in seen if r.key == "latest"] == ["third"]
        time.sleep(0.2)
        assert not [r for r in renderer.poll() if r.key == "latest"]
    finally:
        renderer.shutdown()


def test_the_same_request_in_flight_is_not_asked_twice() -> None:
    renderer = scripted()
    try:
        renderer.lay_out("once", "sleep:0.2")
        renderer.lay_out("once", "sleep:0.2")
        assert said(wait_for(renderer, "once")) == "sleep:0.2"
        time.sleep(0.4)
        assert not [r for r in renderer.poll() if r.key == "once"]
    finally:
        renderer.shutdown()


def test_a_request_over_the_time_limit_is_killed_and_reported_and_the_next_succeeds() -> None:
    renderer = scripted(timeout_s=0.3)
    try:
        renderer.lay_out("warm", "warm")
        assert said(wait_for(renderer, "warm")) == "warm"
        pid = renderer.pid
        assert pid is not None
        renderer.lay_out("slow", "sleep:60")
        result = wait_for(renderer, "slow")
        assert isinstance(result, Failed)
        assert "longer than 0.3 s" in result.reason
        assert result.transient
        assert gone(pid)
        renderer.lay_out("after", "after")
        assert said(wait_for(renderer, "after")) == "after"
        assert renderer.pid not in (None, pid)
    finally:
        renderer.shutdown()


def test_a_killed_child_is_replaced() -> None:
    renderer = scripted()
    try:
        renderer.lay_out("first", "first")
        assert isinstance(wait_for(renderer, "first"), Laid)
        idle = renderer.pid
        assert idle is not None
        os.kill(idle, signal.SIGKILL)
        assert gone(idle, polling=renderer)
        renderer.lay_out("after-idle-kill", "a")
        assert isinstance(wait_for(renderer, "after-idle-kill"), Laid)

        busy = renderer.pid
        assert busy not in (None, idle)
        renderer.lay_out("killed-mid-layout", "sleep:60")
        time.sleep(0.2)
        os.kill(busy, signal.SIGKILL)
        result = wait_for(renderer, "killed-mid-layout")
        assert isinstance(result, Failed)
        assert "killed by signal 9" in result.reason
        assert result.transient
        renderer.lay_out("after-busy-kill", "b")
        assert isinstance(wait_for(renderer, "after-busy-kill"), Laid)
        assert renderer.pid not in (None, idle, busy)
    finally:
        renderer.shutdown()


def test_lay_out_and_poll_return_at_once_while_a_child_starts_and_works() -> None:
    renderer = scripted()
    try:
        start = time.perf_counter()
        renderer.lay_out("slow", "sleep:0.5")
        asked = time.perf_counter()
        first = renderer.poll()
        polled = time.perf_counter()
        # The ask started the I/O thread, which spawns the child; neither waited.
        assert asked - start < 0.02
        assert polled - asked < 0.02
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
        assert isinstance(results[0], Laid)
        assert slowest < 0.02
    finally:
        renderer.shutdown()


def test_lay_out_and_poll_do_not_raise_on_nonsense() -> None:
    renderer = Renderer(fonts=lambda: (_ for _ in ()).throw(OSError("no fonts")))
    try:
        renderer.lay_out(["unhashable"], "x")  # type: ignore[arg-type]
        renderer.lay_out("nofonts", "x")
        reasons = [r for r in renderer.poll() if isinstance(r, Failed)]
        assert any("layout not started" in r.reason for r in reasons)
        result = wait_for(renderer, "nofonts")
        assert isinstance(result, Failed)
        assert "no fonts" in result.reason
    finally:
        renderer.shutdown()


def test_shutdown_leaves_no_child() -> None:
    renderer = scripted()
    renderer.lay_out("one", "one")
    assert isinstance(wait_for(renderer, "one"), Laid)
    pid = renderer.pid
    assert pid is not None
    renderer.lay_out("abandoned", "sleep:60")
    renderer.shutdown()
    assert gone(pid, 0.5)
    assert renderer.pid is None
    assert pid not in {child.pid for child in multiprocessing.active_children()}


def test_an_answer_the_child_gives_is_a_failure_that_is_not_transient() -> None:
    renderer = scripted()
    try:
        renderer.lay_out("refused", "err:merman: Diagram parse error")
        result = wait_for(renderer, "refused")
        assert result == Failed("refused", "merman: Diagram parse error")
        assert not result.transient
    finally:
        renderer.shutdown()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is enforced on Linux")
def test_a_child_over_the_memory_limit_is_a_transient_failure_and_is_replaced() -> None:
    renderer = scripted()
    try:
        renderer.lay_out("small", f"alloc:{MEMORY_LIMIT_BYTES >> 4}")
        assert isinstance(wait_for(renderer, "small"), Laid)
        pid = renderer.pid
        renderer.lay_out("huge", f"alloc:{MEMORY_LIMIT_BYTES}")
        result = wait_for(renderer, "huge")
        assert isinstance(result, Failed), "allocated: the child's memory is not limited"
        assert result.transient
        assert "memory limit" in result.reason
        renderer.lay_out("after", "after")
        assert isinstance(wait_for(renderer, "after"), Laid)
        assert renderer.pid not in (None, pid)
    finally:
        renderer.shutdown()


def test_a_child_that_cannot_be_started_is_reported_at_once() -> None:
    class Unpicklable(Fonts):
        def __reduce__(self) -> str:
            raise TypeError("cannot be sent to a child")

    renderer = Renderer(fonts=lambda: Unpicklable((), "", ""), start_timeout_s=30.0)
    try:
        renderer.lay_out("unstarted", "x")
        result = wait_for(renderer, "unstarted", seconds=5.0)
        assert isinstance(result, Failed)
        assert "cannot be sent" in result.reason
        assert result.transient
    finally:
        renderer.shutdown()


def test_the_real_child_refuses_what_is_not_a_layout_request() -> None:
    """
    ``child_main`` without a renderer around it: a request of another kind is
    answered with a refusal, and the child goes on answering.
    """
    ours, theirs = multiprocessing.get_context("spawn").Pipe(duplex=True)
    child = multiprocessing.get_context("spawn").Process(
        target=render.child_main, args=(theirs, Fonts((), "", ""), None), daemon=True
    )
    child.start()
    theirs.close()
    try:
        assert ours.poll(30) and json.loads(ours.recv_bytes()) == ["ready", None]
        ours.send_bytes(json.dumps(["raster", 100, "<svg/>"]).encode())
        assert ours.poll(30) and json.loads(ours.recv_bytes()) == ["err", "not a layout request"]
        ours.send_bytes(json.dumps(["mermaid", 7]).encode())
        assert ours.poll(30) and json.loads(ours.recv_bytes()) == ["err", "not a layout request"]
    finally:
        ours.close()
        child.join(5)
        if child.is_alive():
            child.kill()


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
        "ok to a layout": [b'["ok", 1, 1]'],
    }[source]


def _hostile_child(conn: Any, fonts: Fonts, memory_limit: int | None = None) -> None:
    """
    A render child that has been taken over: it says it is ready, then answers each
    request with what its source names (``_hostile_reply``).
    """
    conn.send_bytes(b'["ready", null]')
    while True:
        try:
            _, source = json.loads(conn.recv_bytes())
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
                "render reply refused: an unexpected reply to a layout request",
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
