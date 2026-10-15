#!/usr/bin/env python3
"""
What the seam's calls cost on the library as it is: a reply arriving in pieces,
a long settled document, and a diff, each beside ImGui's own text for the same.

These are the questions that decide whether anything needs building over
frames at all. ``scripts/bench_panes.py`` asks what a *pane* costs.

**What is timed.** ``perf_counter_ns`` around the seam's call and nothing else,
in a frame of a bare ImGui context: no window, no rasteriser, the real font
files. So a figure is Python, the library's reading and layout, and draw-list
submission. It is not a frame time and says nothing about a GPU. The header
prints the load average the run started and ended under; under load the
figures roughly double, so compare rows of one run.

**The rows.**

  stream    a reply of ``--lines`` lines (the library's ``demo/sample.md``
            repeated) appended ``--piece`` characters a frame to
            ``typeset.stream`` and drawn every frame, which is the worst
            pacing: every frame reads the open tail again. ``append`` is the
            call that hands a piece over; ``frame`` the draw that reads it.
  settled   the same text whole through ``typeset.markdown``: the first draw
            (read, lay out, draw), a frame after it, and the first frame at
            another width.
  native    the same text through ``imgui.text_unformatted`` wrapped, which is
            what a pane's own renderer costs at its cheapest.
  diff      a Write of ``--diff-lines`` lines through ``typeset.diff`` (which
            a pane calls only where the library's columns hold) and as
            coloured ImGui lines, the way DETAIL's own diff is drawn.

Usage, with the library found as the application finds it (``pptmstr.pairing``):
    .venv/bin/python scripts/profile_seam.py
    .venv/bin/python scripts/profile_seam.py --lines 400 --piece 40
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import imgui_bundle  # noqa: E402
from imgui_bundle import imgui  # noqa: E402

from pptmstr import approval, pairing, theme, version  # noqa: E402
from pptmstr.ui import typeset  # noqa: E402

SAMPLE = ROOT / ".deps" / "imscribe" / "demo" / "sample.md"
WINDOW = (700.0, 600.0)


def _ms(ns: float) -> float:
    return ns / 1e6


def _load() -> str:
    return " ".join(f"{value:.2f}" for value in os.getloadavg())


def frame(body: Callable[[], None], width: float = WINDOW[0]) -> int:
    """
    One frame with ``body`` drawn in a fixed window. Nanoseconds inside ``body``.
    """
    typeset.pre_frame()
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(10.0, 10.0))
    imgui.set_next_window_size(imgui.ImVec2(width, WINDOW[1]))
    imgui.begin(
        "##profile",
        None,
        imgui.WindowFlags_.horizontal_scrollbar.value
        | imgui.WindowFlags_.always_vertical_scrollbar.value,
    )
    started = time.perf_counter_ns()
    try:
        body()
    finally:
        took = time.perf_counter_ns() - started
        imgui.end()
        imgui.render()
    return took


def spread(name: str, samples: list[int]) -> str:
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return (
        f"  {name:<44} n={len(samples):<5} median {_ms(statistics.median(samples)):7.3f}  "
        f"p95 {_ms(p95):7.3f}  max {_ms(ordered[-1]):7.3f} ms"
    )


def text_of(lines: int) -> str:
    sample = SAMPLE.read_text(encoding="utf-8").rstrip("\n").split("\n")
    out: list[str] = []
    copy = 0
    while len(out) < lines:
        copy += 1
        out += [f"## Part {copy}", ""] + sample + [""]
    return "\n".join(out[:lines]) + "\n"


def profile_stream(text: str, piece: int) -> list[str]:
    views = typeset.ViewCache()
    appends: list[int] = []
    draws: list[int] = []
    for at in range(0, len(text), piece):
        stream = typeset.stream(views, "reply")
        started = time.perf_counter_ns()
        stream.append(text[at : at + piece])
        appends.append(time.perf_counter_ns() - started)
        draws.append(frame(lambda: typeset.stream(views, "reply").draw()))
    quiet = [frame(lambda: typeset.stream(views, "reply").draw()) for _ in range(120)]
    view = views.view("reply")
    worst = max(range(len(draws)), key=lambda n: draws[n])
    out = [
        spread(f"stream: append, {piece} characters", appends),
        spread("stream: frame that reads a piece", draws),
        f"     the slowest was piece {worst + 1} of {len(draws)}, "
        f"{text.count(chr(10), 0, worst * piece) + 1} lines in",
        spread("stream: frame with nothing new", quiet),
        f"     library edits {dict(view.edits)}; blocks {len(view.texts)}",
    ]
    over = sum(1 for took in draws if took > 16_600_000)
    out.append(f"     frames that read a piece and took over 16.6 ms: {over} of {len(draws)}")
    return out


def profile_settled(text: str) -> list[str]:
    views = typeset.ViewCache()
    first = frame(lambda: typeset.markdown(views, "doc", text))
    steady = [frame(lambda: typeset.markdown(views, "doc", text)) for _ in range(120)]
    narrower = frame(lambda: typeset.markdown(views, "doc", text), width=520.0)
    after = [frame(lambda: typeset.markdown(views, "doc", text), width=520.0) for _ in range(60)]

    def native() -> None:
        imgui.push_text_wrap_pos(0.0)
        imgui.text_unformatted(text)
        imgui.pop_text_wrap_pos()

    native_first = frame(native)
    native_steady = [frame(native) for _ in range(120)]
    return [
        f"  settled: first draw (read, lay out, draw)        {_ms(first):7.3f} ms",
        spread("settled: frame after it", steady),
        f"  settled: first frame at another width            {_ms(narrower):7.3f} ms",
        spread("settled: frame after that", after),
        f"  native wrapped text: first draw                  {_ms(native_first):7.3f} ms",
        spread("native wrapped text: frame after it", native_steady),
    ]


def profile_diff(lines: int) -> list[str]:
    before = "".join(f"line {n} of the file as it was\n" for n in range(lines))
    after = "".join(
        f"line {n} of the file as it {'is now' if n % 7 == 0 else 'was'}\n" for n in range(lines)
    )
    texts = (approval.DiffText("/tmp/x.py", before, after),)
    unified = approval.render_texts("Write", texts)
    rows = unified.splitlines()
    views = typeset.ViewCache()
    first = frame(lambda: typeset.diff(views, "diff", texts))
    steady = [frame(lambda: typeset.diff(views, "diff", texts)) for _ in range(120)]

    def native() -> None:
        for row in rows:
            imgui.text_colored(theme.P.text.vec4, row or " ")

    native_first = frame(native)
    native_steady = [frame(native) for _ in range(120)]
    return [
        f"  diff of {lines} lines, {len(rows)} unified rows",
        f"  typeset.diff: first draw (model, lay out, draw)  {_ms(first):7.3f} ms",
        spread("typeset.diff: frame after it", steady),
        f"  ImGui lines: first draw                          {_ms(native_first):7.3f} ms",
        spread("ImGui lines: frame after it", native_steady),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lines", type=int, nargs="+", default=[400, 2000])
    parser.add_argument("--piece", type=int, nargs="+", default=[40, 400])
    parser.add_argument("--diff-lines", type=int, default=1200)
    args = parser.parse_args()

    pairing.adopt()
    context = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1200.0, 800.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures.value
    medium = Path(imgui_bundle.__file__).parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"
    io.fonts.add_font_from_file_ttf(str(medium), 16.0)
    theme.set_theme("dark")
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})
    if not typeset.load_fonts(theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf"):
        print(f"the library cannot draw: {typeset.unavailable_reason()}", file=sys.stderr)
        return 1
    print("profile_seam: CPU ms inside the seam's call, headless, no rasteriser")
    print(f"  load average at start: {_load()};  imgui_bundle {imgui_bundle.__version__}")
    print(f"  {version.library_line()}")
    print(f"  window {WINDOW[0]:g} x {WINDOW[1]:g}; text: {SAMPLE.relative_to(ROOT)} repeated")
    # A throwaway document first: the faces' first glyphs are baked by it and
    # not by the first row.
    warm = typeset.ViewCache()
    frame(lambda: typeset.markdown(warm, "warm", SAMPLE.read_text(encoding="utf-8")))
    for lines in args.lines:
        text = text_of(lines)
        print(f"\n{lines} lines, {len(text):,} characters")
        for piece in args.piece:
            print("\n".join(profile_stream(text, piece)))
        print("\n".join(profile_settled(text)))
    print()
    print("\n".join(profile_diff(args.diff_lines)))
    print(f"\n  load average at end: {_load()}")
    typeset.unload()
    imgui.destroy_context(context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
