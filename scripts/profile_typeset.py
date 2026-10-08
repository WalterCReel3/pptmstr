#!/usr/bin/env python3
"""
Where a typeset frame's time and garbage go, by ``advtext`` module and function.

``scripts/bench_panes.py`` says what a *pane* costs. This says what the *library*
under it is doing, for the person who would change the library: every table is
keyed by an ``advtext`` function, and the seam's own share is reported beside it so
the two can be told apart.

**Two paths, one workload.** Each scenario runs twice:

  advtext   the library's own classes (``BareView``, ``StreamView``, ``DiffView``,
            ``build_document``, ``Diff.from_texts``), called directly
  seam      the same text through ``pptmstr.ui.typeset``

Both are given the faces, theme, metrics resolver, measure and palette pptmstr
gives the library -- taken from the seam's own state -- so the ``advtext`` rows are
the library under *this host's* settings (ragged right, first-fit, ImGui's
whole-pixel advances) and not under its defaults. The seam also changes the
library's markdown reader for the whole process (``typeset._reader``), and that
applies to both paths. What the ``seam`` rows add is what the seam does per call:
its pieces, its fitting, its dressing of blocks, its bookkeeping.

**Four passes a scenario**, each over fresh state, because the instruments
disturb each other:

  wall, collector on    ``perf_counter_ns`` round the body; ``gc.callbacks`` counts
                        collections and times their pauses
  wall, collector off   the same with ``gc.disable()``; then one ``gc.collect()``
                        whose return is how much unreachable *cyclic* garbage the
                        samples left, which is the only garbage the collector exists for
  cProfile              enabled round the body only; inflates call-heavy code, so
                        read shares, and the ``x`` figure says by how much. The
                        ``untimed`` column is time under a function that the
                        profiler gave to no function (``profiled`` says why)
  tracemalloc           ``clear_traces()`` before each body, so what is traced at
                        its end is what the body allocated *and still holds*

**What tracemalloc cannot see here** is a block allocated and freed inside one
body, except through the peak. "survivors" is therefore the replacement a body
built (a new layout standing where the old one was), not everything it touched;
constructor call counts in the profile are the other half.

**What a sample is.** ``frame`` scenarios time the body inside one headless ImGui
frame, a few hundred times. ``call`` scenarios (a first layout, a width change, a
stream closing, a diff model) rebuild their state before every sample, untimed,
and time one call. No window, no rasteriser, no GPU, no frame pacing: a figure
here is Python plus ImGui's CPU-side draw-list work.

**Read ratios.** The header prints the load average before and after. Other
processes on the machine move every absolute; compare rows of one run.

Usage:
    .venv/bin/python scripts/profile_typeset.py --out shots/profile/run.txt
    .venv/bin/python scripts/profile_typeset.py --only steady anatomy --frames 200
    .venv/bin/python scripts/profile_typeset.py --only stream --kinds paragraph
    .venv/bin/python scripts/profile_typeset.py --only stream --sizes 10 100 --frames 100
    .venv/bin/python scripts/profile_typeset.py --only stream --sizes 400 --frames 20 \
        --no-profile --no-alloc
"""

from __future__ import annotations

import argparse
import ast
import cProfile
import gc
import itertools
import linecache
import math
import os
import pstats
import re
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import advtext  # noqa: E402
import bench_panes  # noqa: E402
import imgui_bundle  # noqa: E402
from imgui_bundle import imgui  # noqa: E402

from pptmstr import fake_driver, theme  # noqa: E402
from pptmstr.approval import DiffText  # noqa: E402
from pptmstr.ui import typeset  # noqa: E402

ADVTEXT_DIR = str(Path(advtext.__file__).resolve().parent) + os.sep
PPTMSTR_DIR = str(ROOT / "pptmstr") + os.sep
THIS_FILE = str(Path(__file__).resolve())

TRACE_DEPTH = 8
SNAPSHOTS = 4
# Frames of a frame scenario the tracemalloc pass traces. Tracing every allocation
# to this depth makes a frame ten to twenty times slower, and what a frame
# allocates does not vary the way its time does.
ALLOC_FRAMES = 40
WINDOW_FLAGS = (
    imgui.WindowFlags_.no_saved_settings
    | imgui.WindowFlags_.no_title_bar
    | imgui.WindowFlags_.always_vertical_scrollbar
    | imgui.WindowFlags_.horizontal_scrollbar
)

GROUPS = ("steady", "anatomy", "first", "width", "stream", "close", "diff", "select")
KINDS = ("paragraph", "list", "fence")
# Lines the open block of a stream scenario already holds. A paragraph is laid out
# whole on every token, so its 400-line row is 0.6 s a frame and, four passes of a
# few hundred frames each, most of an hour: `--sizes` leaves it out or runs it alone.
STREAM_SIZES = (10, 100, 400)


# ---------------------------------------------------------------------------
# Fixtures: text that never repeats inside a process
# ---------------------------------------------------------------------------

_TAGS = itertools.count(1)
_LINES = itertools.count(1)
_WORDS = re.findall(r"[A-Za-z][A-Za-z'-]+", bench_panes.report_markdown())
_CODE = [line for line in fake_driver._CHECKSUM_MODULE.splitlines() if line.strip()]


def fresh_markdown(lines: int, *, whole: bool = False) -> str:
    """
    The fake fleet's report, numbered apart each time through (``bench_panes.numbered``),
    until it is ``lines`` source lines long. Cut to exactly that, or with ``whole``
    left at the end of the report it was in, so its last block is a finished one.
    """
    base = bench_panes.report_markdown().rstrip("\n") + "\n"
    out: list[str] = []
    have = 0
    while have < lines:
        text = bench_panes.numbered(base, f"#{next(_TAGS)}")
        out.append(text)
        have += text.count("\n") + 1
    joined = "\n".join(out)
    if whole:
        return joined
    return "\n".join(joined.split("\n")[:lines]) + "\n"


def prose_line() -> str:
    """Twelve words of the report and a number no other line has."""
    n = next(_LINES)
    at = (n * 7) % len(_WORDS)
    words = (_WORDS + _WORDS)[at : at + 12]
    return f"{' '.join(words)} {n}"


def open_block(kind: str, lines: int) -> tuple[str, Iterator[str]]:
    """
    Text that leaves one block of ``lines`` lines open, and four-character tokens
    that go on writing the same block for ever.

    Above the block is a heading and two short paragraphs, so the stream has
    committed blocks as well as a live one.
    """

    def line() -> str:
        if kind == "paragraph":
            return prose_line() + "\n"
        if kind == "list":
            return f"- {prose_line()}\n"
        n = next(_LINES)
        return f"{_CODE[n % len(_CODE)]}  # {n}\n"

    above = f"## Run {next(_TAGS)}\n\n{prose_line()}\n{prose_line()}\n\n{prose_line()}\n\n"
    head = above + ("```python\n" if kind == "fence" else "")
    head += "".join(line() for _ in range(lines))

    def tokens() -> Iterator[str]:
        while True:
            text = line()
            for start in range(0, len(text), 4):
                yield text[start : start + 4]

    return head, tokens()


def python_texts(lines: int, every: int, *, unique: bool) -> tuple[str, str]:
    """
    A Python file of ``lines`` lines and the same file with every ``every``-th line
    changed. ``unique`` numbers each line; without it the file is one 60-line unit
    over and over, which is the input ``difflib`` is slowest on.
    """
    before = [
        f"{_CODE[i % len(_CODE)]}  # {i}" if unique else _CODE[i % len(_CODE)] for i in range(lines)
    ]
    after = [f"{line} changed" if i % every == 0 else line for i, line in enumerate(before)]
    return "\n".join(before) + "\n", "\n".join(after) + "\n"


# ---------------------------------------------------------------------------
# The frame
# ---------------------------------------------------------------------------


class Harness:
    """
    One headless ImGui context with the seam's faces loaded, and a frame that draws
    a body in a window of the scenario's size.
    """

    def __init__(self, width: float) -> None:
        self.width = width
        self.height = 700.0
        self.context = imgui.create_context()
        io = imgui.get_io()
        io.set_ini_filename("")
        io.display_size = imgui.ImVec2(width + 40.0, 4000.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        bundled = Path(imgui_bundle.__file__).parent / "assets" / "fonts"
        vendored = Path(theme.__file__).parent / "assets" / "fonts"
        body = io.fonts.add_font_from_file_ttf(str(bundled / "Inconsolata-Medium.ttf"), 16.0)
        bold = io.fonts.add_font_from_file_ttf(str(vendored / "Inconsolata-Bold.ttf"), 16.0)
        theme._FACES[theme.Face.BODY] = body
        theme._FACES[theme.Face.BOLD] = bold
        theme.set_theme("dark")
        theme.apply_style()
        typeset.configure(typeset=True, mono=False)
        if not typeset.load_fonts(vendored / "Inconsolata-Bold.ttf"):
            raise RuntimeError(f"the seam's faces did not load: {typeset.unavailable_reason()}")
        # Everything at a stroke: a first layout is then one call on both paths,
        # and no decision is made from what an earlier layout cost.
        typeset.set_budget(math.inf)
        self._windows = itertools.count()
        self._window = ""
        self.frames = 0

    def fresh_window(self, height: float) -> None:
        self.height = height
        self._window = f"##profile-{next(self._windows)}"

    def frame(self, body: Callable[[], object]) -> None:
        typeset.pre_frame()
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(10.0, 10.0))
        imgui.set_next_window_size(imgui.ImVec2(self.width, self.height))
        imgui.begin(self._window, None, WINDOW_FLAGS)
        try:
            body()
        finally:
            imgui.end()
            imgui.render()
        self.frames += 1

    def close(self) -> None:
        typeset.reset()
        imgui.destroy_context(self.context)


# The library as pptmstr configures it. Private names of the seam, read and never
# written: the point is that both paths lay out with the same settings.


def lib_theme(name: str = "prose") -> Any:
    return typeset._theme(name)


def lib_measure() -> Any:
    return typeset._STATE.measure


def lib_fonts() -> Any:
    return typeset._fonts()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass
class Run:
    """
    One pass's state. ``body`` is what is timed; ``before`` runs ahead of every
    sample, untimed, and for a ``call`` scenario rebuilds whatever ``body`` uses up.
    """

    body: Callable[[], object]
    before: Callable[[], None] | None = None
    # Read once, after the last sample of the first pass.
    facts: Callable[[], dict[str, object]] | None = None
    # Nanoseconds the body spent in named parts of itself, where it timed them.
    parts: dict[str, list[int]] = field(default_factory=dict)

    def part(self, name: str, started: int) -> int:
        now = time.perf_counter_ns()
        self.parts.setdefault(name, []).append(now - started)
        return now


@dataclass(frozen=True)
class Scenario:
    group: str
    name: str
    size: str
    path: str
    make: Callable[[Harness], Run]
    per: str = "frame"
    samples: int = 0
    warmup: int = 20
    height: float = 700.0

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.group, self.name, self.size)


def _glyphs(stats: Any) -> dict[str, object]:
    return {"glyphs": stats.glyphs, "missing": stats.missing}


def _layout_facts(view: Any) -> dict[str, object]:
    stats = view.layout_stats
    if stats is None:
        return {}
    return {
        "units": stats.units,
        "units built": len(stats.built),
        "blocks broken": stats.blocks_broken,
    }


def steady(lines: int, path: str) -> Scenario:
    """A settled document, already laid out, drawn again with nothing changed."""

    def make(h: Harness) -> Run:
        text = fresh_markdown(600)
        last: list[Any] = [None]
        if path == "advtext":
            doc = advtext.build_document(text, lib_theme(), typeset.pixel_metrics)
            view = advtext.BareView(document=doc, theme=lib_theme(), measure=lib_measure())

            def body() -> None:
                last[0] = view.draw(fonts=lib_fonts(), palette=typeset.palette())

            return Run(body, facts=lambda: _glyphs(last[0]))
        cache = typeset.ViewCache()

        def seam_body() -> None:
            typeset.markdown(cache, "doc", text)

        def seam_facts() -> dict[str, object]:
            drawn = cache.drawn("doc") or (0, 0)
            return {"glyphs": drawn[0], "missing": drawn[1]}

        return Run(seam_body, facts=seam_facts)

    pitch = typeset.line_height(mono=False)
    return Scenario(
        "steady", "settled document, steady draw", f"{lines} lines of room", path, make,
        height=lines * pitch,
    )  # fmt: skip


def first_frame(lines: int, path: str) -> Scenario:
    """Text never seen before: parsed, laid out and one window of it drawn."""

    def make(h: Harness) -> Run:
        held: dict[str, Any] = {}
        run = Run(lambda: None)

        def before() -> None:
            held["text"] = fresh_markdown(lines)
            held["cache"] = typeset.ViewCache()

        def body() -> None:
            at = time.perf_counter_ns()
            doc = advtext.build_document(held["text"], lib_theme(), typeset.pixel_metrics)
            at = run.part("build_document", at)
            view = advtext.BareView(document=doc, theme=lib_theme(), measure=lib_measure())
            wide = max(imgui.get_content_region_avail().x, 1.0)
            view.layout_for(advtext.sp_from_px(wide, view.px_per_inch))
            at = run.part("layout_for", at)
            view.draw(fonts=lib_fonts(), palette=typeset.palette())
            run.part("draw", at)
            held["view"] = view

        def seam_body() -> None:
            typeset.markdown(held["cache"], "doc", held["text"])

        run.before = before
        run.body = body if path == "advtext" else seam_body
        if path == "advtext":
            run.facts = lambda: _layout_facts(held["view"])
        return run

    return Scenario(
        "first", "first frame of new text", f"{lines:,} lines", path, make,
        per="call", samples=8 if lines <= 100 else 4 if lines <= 1000 else 3, warmup=1,
    )  # fmt: skip


def new_width(lines: int, path: str) -> Scenario:
    """A document laid out at one width, asked for at another it has never had."""

    def make(h: Harness) -> Run:
        held: dict[str, Any] = {}
        narrower = h.width - 160.0

        def before() -> None:
            text = held["text"] = fresh_markdown(lines)
            if path == "advtext":
                doc = advtext.build_document(text, lib_theme(), typeset.pixel_metrics)
                view = advtext.BareView(document=doc, theme=lib_theme(), measure=lib_measure())
                held["view"] = view
                h.frame(lambda: view.draw(fonts=lib_fonts(), palette=typeset.palette()))
            else:
                cache = held["cache"] = typeset.ViewCache()
                h.frame(lambda: typeset.markdown(cache, "doc", text))

        def body() -> None:
            view = held["view"]
            view.layout_for(advtext.sp_from_px(narrower, view.px_per_inch))

        def seam_body() -> None:
            typeset.markdown(held["cache"], "doc", held["text"], width=narrower)

        if path == "advtext":
            return Run(body, before, facts=lambda: _layout_facts(held["view"]))
        return Run(seam_body, before)

    return Scenario(
        "width", "layout at a new width", f"{lines:,} lines", path, make,
        per="call", samples=6 if lines <= 100 else 4, warmup=1,
    )  # fmt: skip


def _stream_run(h: Harness, path: str, head: str, tokens: Iterator[str] | None) -> Run:
    """
    A stream holding ``head``, given one of ``tokens`` before every frame (or
    nothing, with ``None``), and drawn.
    """
    run = Run(lambda: None)
    last: list[Any] = [None]
    if path == "advtext":
        doc = advtext.StreamDocument(lib_theme(), typeset.pixel_metrics)
        view = advtext.StreamView(doc, lib_measure(), follow=True)
        doc.append(head)

        def body() -> None:
            # `draw` does these two itself; done first here so each has a time of
            # its own, and `draw` then finds both already done.
            at = time.perf_counter_ns()
            view.sync()
            at = run.part("sync (parse)", at)
            wide = max(imgui.get_content_region_avail().x, 1.0)
            view.layout_for(advtext.sp_from_px(wide, view.px_per_inch))
            at = run.part("layout_for", at)
            last[0] = view.draw(fonts=lib_fonts(), palette=typeset.palette())
            run.part("draw", at)

        def facts() -> dict[str, object]:
            return {
                **_glyphs(last[0]),
                **_layout_facts(view),
                "committed": len(doc.committed),
                "live": len(doc.live),
            }

        run.body, run.facts = body, facts
        if tokens is not None:
            run.before = lambda: doc.append(next(tokens))
        return run
    cache = typeset.ViewCache()
    stream = typeset.stream(cache, "run", follow=True)
    stream.append(head)

    def seam_body() -> None:
        stream.draw()

    def seam_facts() -> dict[str, object]:
        drawn = cache.drawn("run") or (0, 0)
        return {"glyphs": drawn[0], "missing": drawn[1], **_layout_facts(stream.view)}

    run.body, run.facts = seam_body, seam_facts
    if tokens is not None:
        run.before = lambda: stream.append(next(tokens))
    return run


def streaming(kind: str, lines: int, path: str, *, idle: bool) -> Scenario:
    def make(h: Harness) -> Run:
        head, tokens = open_block(kind, lines)
        return _stream_run(h, path, head, None if idle else tokens)

    what = "nothing arriving" if idle else "one token a frame"
    return Scenario("stream", f"stream, {what}", f"open {kind}, {lines} lines", path, make)


def closing(cycles: int, path: str, *, swap: bool) -> Scenario:
    """
    A stream that has shown a whole run and is then closed.

    Without ``swap`` the body is the first frame after ``close()``. With it, the
    body is the first frame of a *settled* view of the same text, which is what
    the context log replaces a closed run's stream with.
    """
    lines = fresh_markdown(97 * cycles, whole=True).count("\n") + 1

    def make(h: Harness) -> Run:
        held: dict[str, Any] = {}

        def before() -> None:
            text = held["text"] = fresh_markdown(97 * cycles, whole=True)
            if path == "advtext":
                doc = advtext.StreamDocument(lib_theme(), typeset.pixel_metrics)
                view = advtext.StreamView(doc, lib_measure(), follow=True)
                doc.append(text)
                held["doc"], held["view"] = doc, view
                draw: Callable[[], object] = lambda: view.draw(  # noqa: E731
                    fonts=lib_fonts(), palette=typeset.palette()
                )
                close: Callable[[], object] = doc.close
            else:
                cache = held["cache"] = typeset.ViewCache()
                stream = held["stream"] = typeset.stream(cache, "run", follow=True)
                stream.append(text)
                draw, close = stream.draw, stream.close
            h.frame(draw)
            h.frame(draw)
            close()
            if swap:
                h.frame(draw)

        def body() -> None:
            if not swap:
                held["view"].draw(fonts=lib_fonts(), palette=typeset.palette())
                return
            # The stream's own Document: the same block objects it has just laid out.
            settled = advtext.BareView(
                document=held["doc"].document, theme=lib_theme(), measure=lib_measure()
            )
            settled.draw(fonts=lib_fonts(), palette=typeset.palette())
            held["settled"] = settled

        def seam_body() -> None:
            if not swap:
                held["stream"].draw()
                return
            typeset.markdown(held["cache"], "settled", held["text"])

        def facts() -> dict[str, object]:
            return _layout_facts(held["settled" if swap else "view"])

        if path == "advtext":
            return Run(body, before, facts=facts)
        return Run(seam_body, before)

    name = "settled view of the closed run, first frame" if swap else "frame after close()"
    return Scenario(
        "close", name, f"{lines:,} lines", path, make, per="call", samples=5, warmup=1
    )  # fmt: skip


def diff_build(lines: int, every: int, unique: bool, path: str) -> Scenario:
    def make(h: Harness) -> Run:
        before_text, after_text = python_texts(lines, every, unique=unique)
        if path == "advtext":
            return Run(lambda: advtext.Diff.from_texts(before_text, after_text, "bench.py"))
        texts = (DiffText(before_text, after_text, "bench.py"),)
        return Run(lambda: typeset.diff_model(texts))

    kind = "numbered lines" if unique else "a 60-line unit repeated"
    return Scenario(
        "diff", "diff model", f"{lines:,} lines, every {every}th changed, {kind}", path, make,
        per="call", samples=3, warmup=0,
    )  # fmt: skip


def diff_view(rows: int, path: str, *, first: bool) -> Scenario:
    """A ``DiffView`` over a 5,000-line file with every 50th line changed."""

    def make(h: Harness) -> Run:
        before_text, after_text = python_texts(5000, 50, unique=True)
        model = advtext.Diff.from_texts(before_text, after_text, "bench.py")
        cap = rows * typeset.diff_row_height()
        held: dict[str, Any] = {}
        last: list[Any] = [None]

        def fresh() -> None:
            held["view"] = advtext.DiffView(
                model, lib_theme("literal"), metrics=typeset.pixel_metrics, height_cap=cap
            )
            held["cache"] = typeset.ViewCache()

        def body() -> None:
            last[0] = held["view"].draw(fonts=lib_fonts(), palette=typeset.palette())

        def seam_body() -> None:
            typeset.diff(held["cache"], "diff", model, height_cap=cap)

        def facts() -> dict[str, object]:
            view = held["view"] if path == "advtext" else held["cache"].view("diff")
            found: dict[str, object] = {"rows": view.row_count, "plans built": view.plans_built}
            if last[0] is not None:
                found.update(_glyphs(last[0]))
            return found

        fresh()
        return Run(body if path == "advtext" else seam_body, fresh if first else None, facts)

    if first:
        return Scenario(
            "diff", "DiffView, first frame", f"{rows} rows of room", path, make,
            per="call", samples=5, warmup=1,
        )  # fmt: skip
    return Scenario("diff", "DiffView, steady frame", f"{rows} rows of room", path, make)


def selecting(mode: str, path: str) -> Scenario:
    """
    A 1,000-line settled document in 60 lines of room, with a selection.

    ``drag``: the left button held and the mouse moved a line a frame, down the
    window and back, so the selection changes on every frame. ``all``: everything
    selected (``select_all``) and nothing moving, most of it out of sight.
    """

    def make(h: Harness) -> Run:
        text = fresh_markdown(1000)
        io = imgui.get_io()
        step = itertools.count()
        last: list[Any] = [None]
        if path == "advtext":
            doc = advtext.build_document(text, lib_theme(), typeset.pixel_metrics)
            made = advtext.BareView(document=doc, theme=lib_theme(), measure=lib_measure())

            def body() -> None:
                last[0] = made.draw(fonts=lib_fonts(), palette=typeset.palette())

            def view() -> Any:
                return made

        else:
            cache = typeset.ViewCache()

            def body() -> None:
                typeset.markdown(cache, "doc", text)

            def view() -> Any:
                return cache.view("doc")

        # A pass that ended with the button held would leave the next one's press
        # no press at all, and its drag a mouse moving over a view.
        io.add_mouse_button_event(0, False)
        h.frame(body)
        if mode == "all":
            view().select_all()

        def before() -> None:
            n = next(step)
            origin = view().origin or (20.0, 20.0)
            if n == 0:
                io.add_mouse_pos_event(origin[0] + 40.0, 30.0)
                io.add_mouse_button_event(0, True)
                return
            # A triangle wave over the height of the window, a line at a time.
            span = int(h.height - 60.0)
            at = (n * 21) % (2 * span)
            io.add_mouse_pos_event(origin[0] + 300.0, 30.0 + (at if at < span else 2 * span - at))

        def facts() -> dict[str, object]:
            chosen = view().selection
            found: dict[str, object] = {
                "selection": f"{chosen.start}..{chosen.end}",
                "selected characters": len(view().selected_text()),
            }
            return found

        return Run(body, before if mode == "drag" else None, facts)

    name = "selection dragged, a line a frame" if mode == "drag" else "everything selected, held"
    return Scenario(
        "select", name, "1,000 lines, 60 of room", path, make,
        height=60 * typeset.line_height(mono=False),
    )  # fmt: skip


def copying(path: str) -> Scenario:
    def make(h: Harness) -> Run:
        text = fresh_markdown(1000)
        if path == "advtext":
            doc = advtext.build_document(text, lib_theme(), typeset.pixel_metrics)
            view = advtext.BareView(document=doc, theme=lib_theme(), measure=lib_measure())
            h.frame(lambda: view.draw(fonts=lib_fonts(), palette=typeset.palette()))
        else:
            cache = typeset.ViewCache()
            h.frame(lambda: typeset.markdown(cache, "doc", text))
            view = cache.view("doc")
        view.select_all()
        return Run(view.selected_text, facts=lambda: {"characters": len(view.selected_text())})

    return Scenario(
        "select", "selected_text() of everything", "1,000 lines", path, make,
        per="call", samples=8, warmup=1,
    )  # fmt: skip


def scenarios(
    only: Sequence[str], kinds: Sequence[str], frames: int, sizes: Sequence[int] = STREAM_SIZES
) -> list[Scenario]:
    out: list[Scenario] = []
    paths = ("advtext", "seam")
    if "steady" in only:
        out += [steady(lines, path) for lines in (30, 60, 120) for path in paths]
    if "first" in only:
        out += [first_frame(lines, path) for lines in (100, 1000, 5000) for path in paths]
    if "width" in only:
        out += [new_width(lines, path) for lines in (100, 1000) for path in paths]
    if "stream" in only:
        for kind in kinds:
            for lines in sizes:
                for idle in (True, False):
                    out += [streaming(kind, lines, path, idle=idle) for path in paths]
    if "close" in only:
        for cycles in (1, 4):
            for swap in (False, True):
                out += [closing(cycles, path, swap=swap) for path in paths]
    if "diff" in only:
        for lines, every, unique in ((5000, 50, True), (5000, 7, True), (5000, 7, False)):
            out += [diff_build(lines, every, unique, path) for path in paths]
        out += [diff_build(20000, 50, True, path) for path in paths]
        out += [diff_view(rows, path, first=False) for rows in (30, 60) for path in paths]
        out += [diff_view(60, path, first=True) for path in paths]
    if "select" in only:
        out += [selecting(mode, path) for mode in ("drag", "all") for path in paths]
        out += [copying(path) for path in paths]
    return [
        s if s.samples else Scenario(**{**s.__dict__, "samples": frames})  # type: ignore[arg-type]
        for s in out
    ]


# ---------------------------------------------------------------------------
# The four passes
# ---------------------------------------------------------------------------


def drive(
    h: Harness,
    sc: Scenario,
    run: Run,
    timed: Callable[[Callable[[], object]], None],
    samples: int | None = None,
) -> None:
    """
    Warm up, then hand each measured sample's body to ``timed`` inside its frame.
    """

    def one(wrap: Callable[[Callable[[], object]], object]) -> None:
        if run.before is not None:
            run.before()
        h.frame(lambda: wrap(run.body))

    for _ in range(sc.warmup):
        one(lambda body: body())
    for parts in run.parts.values():
        parts.clear()
    for _ in range(sc.samples if samples is None else samples):
        one(timed)


class Collections:
    """
    Every collection the interpreter runs, by generation, with its pause and
    whether it began inside a timed body.
    """

    def __init__(self) -> None:
        self.inside = False
        self.seen: list[tuple[int, float, bool]] = []
        self._started = 0

    def __call__(self, phase: str, info: dict[str, int]) -> None:
        if phase == "start":
            self._started = time.perf_counter_ns()
            return
        pause = (time.perf_counter_ns() - self._started) / 1e6
        self.seen.append((info["generation"], pause, self.inside))


@dataclass
class Wall:
    on_ms: list[float]
    off_ms: list[float]
    # Collections that began inside a body, by generation, and those that did not.
    inside: tuple[int, int, int]
    outside: tuple[int, int, int]
    pauses_ms: list[float]
    # Unreachable objects one full collection found after the collector-off samples.
    cyclic: int
    # Growth of the young generation's count across a body, collector off: tracked
    # objects made less tracked objects freed.
    net_tracked: float
    # Tracked objects the scenario's state added to the heap, and what one full
    # collection costs with them alive.
    held: int
    full_ms: float
    facts: dict[str, object]
    parts_ms: dict[str, float]


def _tracked() -> int:
    return len(gc.get_objects())


def wall(h: Harness, sc: Scenario) -> Wall:
    # Collector on, as the application leaves it.
    gc.enable()
    gc.collect()
    base = _tracked()
    h.fresh_window(sc.height)
    run = sc.make(h)
    watch = Collections()
    on: list[int] = []

    def timed_on(body: Callable[[], object]) -> None:
        if not on:
            # The warm-up's collections are not the samples'.
            watch.seen.clear()
        watch.inside = True
        started = time.perf_counter_ns()
        body()
        on.append(time.perf_counter_ns() - started)
        watch.inside = False

    gc.callbacks.append(watch)
    try:
        drive(h, sc, run, timed_on)
    finally:
        gc.callbacks.remove(watch)
    measured = list(watch.seen)
    facts = run.facts() if run.facts is not None else {}
    parts = {name: statistics.median(ns) / 1e6 for name, ns in run.parts.items() if ns}
    gc.collect()
    held = _tracked() - base
    started = time.perf_counter_ns()
    gc.collect()
    full_ms = (time.perf_counter_ns() - started) / 1e6
    del run

    # Collector off.
    gc.collect()
    h.fresh_window(sc.height)
    run = sc.make(h)
    off: list[int] = []
    net: list[int] = []
    primed = [False]

    def timed_off(body: Callable[[], object]) -> None:
        if not primed[0]:
            # Everything before the first measured sample is collected away, so the
            # collection at the end finds only what the samples left.
            gc.enable()
            gc.collect()
            gc.disable()
            primed[0] = True
        count = gc.get_count()[0]
        started = time.perf_counter_ns()
        body()
        off.append(time.perf_counter_ns() - started)
        net.append(gc.get_count()[0] - count)

    gc.disable()
    try:
        drive(h, sc, run, timed_off)
    finally:
        gc.enable()
    cyclic = gc.collect()
    del run
    gc.collect()

    def counts(inside: bool) -> tuple[int, int, int]:
        tally = [0, 0, 0]
        for generation, _pause, was_inside in measured:
            if was_inside is inside:
                tally[generation] += 1
        return (tally[0], tally[1], tally[2])

    return Wall(
        on_ms=[ns / 1e6 for ns in on],
        off_ms=[ns / 1e6 for ns in off],
        inside=counts(True),
        outside=counts(False),
        pauses_ms=[pause for _g, pause, was_inside in measured if was_inside],
        cyclic=cyclic,
        net_tracked=statistics.median(net) if net else 0.0,
        held=held,
        full_ms=full_ms,
        facts=facts,
        parts_ms=parts,
    )


@dataclass
class Fn:
    label: str
    calls: float
    tot_ms: float
    cum_ms: float
    # Under the function and in no function's own time: see `profiled`.
    untimed_ms: float = 0.0


@dataclass
class Profiled:
    # Milliseconds a sample, under the profiler; `inflation` is that over the
    # collector-on median without it.
    total_ms: float
    inflation: float
    functions: list[Fn]
    seam: list[Fn]
    # Calls out of advtext into anything that is not advtext, by (callee, caller).
    edges: list[Fn]
    buckets: dict[str, float]


_QUALNAMES: dict[str, list[tuple[int, int, str]]] = {}


def _qualname(file: str, line: int, fallback: str) -> str:
    """
    The dotted name of the function a line of a source file is in.

    ``cProfile`` and ``tracemalloc`` both name a file and a line; a class's method is
    ``__init__`` to one and nothing to the other. Read from the source once.
    """
    spans = _QUALNAMES.get(file)
    if spans is None:
        spans = _QUALNAMES[file] = []
        try:
            tree = ast.parse(Path(file).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            tree = None

        def walk(node: ast.AST, prefix: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    name = f"{prefix}{child.name}"
                    first = min([child.lineno] + [d.lineno for d in child.decorator_list])
                    spans.append((first, child.end_lineno or child.lineno, name))
                    walk(child, f"{name}.")
                else:
                    walk(child, prefix)

        if tree is not None:
            walk(tree, "")
    best: tuple[int, int, str] | None = None
    for first, stop, name in spans:
        if first <= line <= stop and (best is None or first >= best[0]):
            best = (first, stop, name)
    if best is None:
        return fallback
    # A lambda or a comprehension is named for the function it is written in.
    return best[2] if fallback == best[2].rsplit(".", 1)[-1] else f"{best[2]}.{fallback}"


def _owner(file: str) -> str:
    if file == "~":
        return "builtin"
    if file.startswith(ADVTEXT_DIR):
        return "advtext"
    if file.startswith(PPTMSTR_DIR):
        return "seam"
    if file == THIS_FILE:
        return "bench"
    if file == "<string>":
        return "generated"
    return "other"


def _short(file: str) -> str:
    if file.startswith(ADVTEXT_DIR):
        return file[len(ADVTEXT_DIR) :]
    if file.startswith(PPTMSTR_DIR):
        return "pptmstr/" + file[len(PPTMSTR_DIR) :]
    if file.startswith("<frozen "):
        return file[8:-1]
    parts = Path(file).parts
    if "site-packages" in parts:
        return parts[parts.index("site-packages") + 1]
    return Path(file).stem


_BUILTIN = re.compile(r"<(?:built-in )?method '?([\w.]+)'?(?: of '([\w.]+)' objects)?>")


def _label(key: tuple[str, int, str]) -> str:
    file, line, name = key
    owner = _owner(file)
    if owner == "builtin":
        found = _BUILTIN.match(name)
        if not found:
            return name
        method, of = found.group(1), found.group(2)
        if of:
            return f"{of.rsplit('.', 1)[-1]}.{method}"
        return ".".join(method.rsplit(".", 2)[-2:]) if "imgui" in method else method
    if owner == "generated":
        return f"generated {name}"
    if owner in ("advtext", "seam"):
        return f"{_short(file)}:{_qualname(file, line, name)}"
    return f"{_short(file)}:{name}"


def profiled(h: Harness, sc: Scenario, unprofiled_ms: float) -> Profiled:
    """
    One pass under ``cProfile``, as own time by owner and by function.

    **Own times do not add up to the time profiled, and the difference is not
    noise.** On CPython 3.11 the profiler records no call for the constructor of
    a ``@dataclass(frozen=True, slots=True)`` -- no ``__init__`` row appears --
    and the time it takes is in its caller's cumulative figure and in *no*
    function's own (200,000 constructions in a loop: the loop's cumulative time
    less its own time is the whole of their cost, with no callee to hold it).
    So a function's ``untimed`` is its cumulative time less its own less every
    recorded callee's, and it is counted into the total and into a bucket of its
    own, because in ``_glyph`` and ``_RigidSpan.pen`` it is a quarter of the draw.
    A call into an ImGui binding is not recorded either, but that time *is* in
    its caller's own.
    """
    gc.enable()
    gc.collect()
    h.fresh_window(sc.height)
    run = sc.make(h)
    profiler = cProfile.Profile()

    def timed(body: Callable[[], object]) -> None:
        profiler.enable()
        body()
        profiler.disable()

    drive(h, sc, run, timed)
    raw: dict[Any, Any] = pstats.Stats(profiler).stats  # type: ignore[attr-defined]
    n = float(sc.samples)
    under: dict[Any, float] = {}
    for _cc, _nc, _tt, _ct, callers in raw.values():
        for caller, edge in callers.items():
            under[caller] = under.get(caller, 0.0) + edge[3]
    # A recursive function's cumulative figures count a call under itself, and the
    # subtraction means nothing for it.
    untimed = {
        key: max(0.0, ct - tt - under.get(key, 0.0)) if cc == nc and key not in callers else 0.0
        for key, (cc, nc, tt, ct, callers) in raw.items()
    }
    total = (sum(entry[2] for entry in raw.values()) + sum(untimed.values())) * 1000.0 / n
    functions: list[Fn] = []
    seam: list[Fn] = []
    edges: list[Fn] = []
    buckets: dict[str, float] = {}

    def add(bucket: str, seconds: float) -> None:
        buckets[bucket] = buckets.get(bucket, 0.0) + seconds * 1000.0 / n

    for key, (_cc, nc, tt, ct, callers) in raw.items():
        owner = _owner(key[0])
        lost = untimed[key]
        row = Fn(_label(key), nc / n, tt * 1000.0 / n, ct * 1000.0 / n, lost * 1000.0 / n)
        if owner == "advtext":
            functions.append(row)
            add(f"advtext {_short(key[0])}", tt)
            add(f"untimed under advtext {_short(key[0])}", lost)
            continue
        if owner == "seam":
            seam.append(row)
            add("seam (pptmstr/ui/typeset.py and the rest of pptmstr)", tt)
            add("untimed under the seam", lost)
        elif lost:
            add("untimed under anything else", lost)
        from_advtext = 0.0
        from_seam = 0.0
        for caller, (edge_nc, _edge_cc, edge_tt, edge_ct) in callers.items():
            whose = _owner(caller[0])
            if whose == "advtext":
                from_advtext += edge_tt
                edges.append(
                    Fn(f"{row.label}  <-  {_label(caller)}", edge_nc / n, 0.0, edge_ct * 1000.0 / n)
                )
            elif whose == "seam":
                from_seam += edge_tt
        if owner == "seam":
            continue
        if owner in ("builtin", "generated"):
            imgui_call = "imgui" in key[2]
            what = "ImGui calls" if imgui_call else "other C calls"
            if owner == "generated":
                what = "generated code (dataclass __init__, __eq__, __hash__)"
            add(f"{what} made by advtext", from_advtext)
            add("C calls and generated code under the seam", from_seam)
            add("C calls and generated code under anything else", tt - from_advtext - from_seam)
        elif owner == "bench":
            add("this script", tt)
        else:
            add(f"other Python: {_short(key[0])}", tt)
    return Profiled(
        total_ms=total,
        inflation=total / unprofiled_ms if unprofiled_ms else 0.0,
        functions=functions,
        seam=seam,
        edges=sorted(edges, key=lambda e: e.cum_ms, reverse=True),
        buckets=buckets,
    )


@dataclass
class Allocated:
    # A sample: bytes and blocks the body allocated and still held at its end, and
    # the most it held at once.
    survivors: float
    blocks: float
    peak: float
    # (where, bytes a sample, blocks a sample), by the innermost advtext line.
    lines: list[tuple[str, float, float]]
    # Bytes a sample by owner of the innermost frame that is advtext's, else the seam's.
    owners: dict[str, float]


def allocated(h: Harness, sc: Scenario) -> Allocated:
    gc.enable()
    gc.collect()
    h.fresh_window(sc.height)
    run = sc.make(h)
    survivors: list[int] = []
    peaks: list[int] = []
    taken = 0
    samples = min(sc.samples, ALLOC_FRAMES) if sc.per == "frame" else sc.samples
    every = max(1, samples // SNAPSHOTS)
    count = itertools.count()
    by_line: dict[tuple[str, int], list[float]] = {}
    owners: dict[str, float] = {}
    blocks = 0

    def timed(body: Callable[[], object]) -> None:
        nonlocal taken, blocks
        index = next(count)
        if not tracemalloc.is_tracing():
            tracemalloc.start(TRACE_DEPTH)
        tracemalloc.clear_traces()
        tracemalloc.reset_peak()
        body()
        current, peak = tracemalloc.get_traced_memory()
        survivors.append(current)
        peaks.append(peak)
        if index % every or taken >= SNAPSHOTS:
            return
        taken += 1
        snapshot = tracemalloc.take_snapshot()
        for stat in snapshot.statistics("traceback"):
            blocks += stat.count
            where: tuple[str, int] | None = None
            whose = "other"
            # Oldest frame first: the innermost advtext frame is the last one.
            for frame in reversed(stat.traceback):
                owner = _owner(frame.filename)
                if owner == "advtext":
                    where, whose = (frame.filename, frame.lineno), "advtext"
                    break
                if owner == "seam" and whose == "other":
                    whose = "seam"
            owners[whose] = owners.get(whose, 0.0) + stat.size
            if where is not None:
                slot = by_line.setdefault(where, [0.0, 0.0])
                slot[0] += stat.size
                slot[1] += stat.count

    # Uncollected cycles would be counted as held; with the collector off every
    # sample is counted the same way, and the collector's own table says how much
    # of what a body leaves is cyclic.
    gc.disable()
    try:
        drive(h, sc, run, timed, samples)
    finally:
        tracemalloc.stop()
        gc.enable()
    shots = max(taken, 1)
    lines = []
    for (file, line), (size, number) in by_line.items():
        source = " ".join(linecache.getline(file, line).split())
        if len(source) > 58:
            source = source[:55] + "..."
        where = f"{_short(file)}:{_qualname(file, line, '?').removesuffix('.?')}"
        lines.append((f"{where}  |  {source}", size / shots, number / shots))
    lines.sort(key=lambda item: item[1], reverse=True)
    del run
    gc.collect()
    return Allocated(
        survivors=statistics.median(survivors),
        blocks=blocks / shots,
        peak=statistics.median(peaks),
        lines=lines,
        owners={whose: size / shots for whose, size in owners.items()},
    )


# ---------------------------------------------------------------------------
# The anatomy of one draw
# ---------------------------------------------------------------------------


class _Recording:
    """
    The window's draw list, keeping every ``add_text`` it is asked for.
    """

    def __init__(self, real: Any) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "texts", [])
        object.__setattr__(self, "others", {})

    def add_text(self, *args: Any) -> None:
        if len(args) == 5:
            font, size, at, ink, text = args
            self.texts.append((font, float(size), float(at.x), float(at.y), ink, text))
        self._real.add_text(*args)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("add_"):
            self.others[name] = self.others.get(name, 0) + 1
        return getattr(self._real, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._real, name, value)


def _nothing(*_args: Any, **_kwargs: Any) -> None:
    return None


class _Discarding:
    """
    A draw list that takes every call and keeps nothing (``scripts/bench_draw.py``
    in the library has the argument for measuring ImGui's share this way).
    """

    flags = 0
    # Named, so the call the backend makes once a glyph is an attribute found on
    # the class and not a trip through `__getattr__`, which costs more than the
    # `add_text` it stands in for.
    add_text = staticmethod(_nothing)
    add_rect_filled = staticmethod(_nothing)

    def __getattr__(self, name: str) -> Any:
        return _nothing


@dataclass
class Anatomy:
    lines: int
    glyphs: int
    calls: int
    exact_runs: int
    exact_pairs: int
    pairs: int
    loose_runs: int
    moved_calls: int
    shift_common: int
    rows: list[tuple[str, float]]
    # Nanoseconds for one of each thing `_glyph` does once or more a glyph.
    each_ns: list[tuple[str, float]]


def _median_frames(h: Harness, frames: int, body: Callable[[], object]) -> float:
    samples: list[int] = []

    def timed() -> None:
        started = time.perf_counter_ns()
        body()
        samples.append(time.perf_counter_ns() - started)

    for _ in range(20):
        h.frame(body)
    for _ in range(frames):
        h.frame(timed)
    return statistics.median(samples) / 1e6


def _each_ns() -> list[tuple[str, float]]:
    """
    What one of each per-glyph operation costs alone, best of five timings.

    ``cProfile`` records no call for an ImGui binding or for the constructor of a
    frozen slotted dataclass (see ``profiled``), so the pieces of ``_glyph`` and
    ``_RigidSpan.pen`` are timed here one at a time. Each figure includes the
    lambda that wraps the piece: subtract the first row. The backend's private
    names are used and looked up by name: a piece that is no longer there is left
    out and the rest still print.
    """
    from advtext.backends import imgui_draw as backend

    loops = 2_000
    span_type = getattr(backend, "_RigidSpan", None)
    # A draw list and a font belong to a frame: both are fetched again after
    # every `new_frame`, and neither is asked for outside one.
    frame: dict[str, Any] = {}
    at = imgui.ImVec2(100.0, 100.0)

    def new_frame() -> None:
        imgui.new_frame()
        frame["list"] = imgui.get_background_draw_list()
        frame["font"] = imgui.get_font()

    pieces: list[tuple[str, Callable[[], object] | None]] = [
        ("an empty Python function call", _nothing),
        ("imgui.ImVec2(x, y)", lambda: imgui.ImVec2(1.0, 2.0)),
        ("ImDrawList.add_text(font, size, at, ink, one character)",
         lambda: frame["list"].add_text(frame["font"], 16.0, at, 0xFFFFFFFF, "a")),
        ("DrawStats(glyphs=, missing=, rules=)",
         lambda: backend.DrawStats(glyphs=1, missing=0, rules=0)),
        ("_RigidSpan(seven fields)",
         None if span_type is None else lambda: span_type(
             x=1, y=2, ascent=3.0, left=4.0, top=5.0, prev_x=6, prev_pen=7.0)),
        ("snap_px(value)", lambda: backend.snap_px(12.4)),
        ("px_from_sp(value, px_per_inch)", lambda: advtext.px_from_sp(655360, 96.0)),
    ]  # fmt: skip
    found: list[tuple[str, float]] = []
    for name, piece in pieces:
        if piece is None:
            continue
        best = math.inf
        try:
            # A frame a timing: `add_text` is then never timed growing a buffer
            # past what a frame of text holds, and the draw list never outgrows a
            # 16-bit index.
            for _ in range(7):
                new_frame()
                try:
                    started = time.perf_counter_ns()
                    for _ in range(loops):
                        piece()
                    best = min(best, (time.perf_counter_ns() - started) / loops)
                finally:
                    imgui.render()
        except Exception as failure:
            found.append((f"{name}: {type(failure).__name__}", math.nan))
            continue
        found.append((name, best))
    return found


def anatomy(h: Harness, lines: int, frames: int) -> Anatomy:
    """
    One settled view's draw, taken apart by what is handed to ImGui.

    The view's own ``add_text`` calls are recorded off the window's draw list and
    then issued again without the library, so "what ImGui does with them" is
    timed over the same arguments and not over a reconstruction.
    """
    h.fresh_window(lines * typeset.line_height(mono=False))
    text = fresh_markdown(600)
    doc = advtext.build_document(text, lib_theme(), typeset.pixel_metrics)
    view = advtext.BareView(document=doc, theme=lib_theme(), measure=lib_measure())
    fonts, colours = lib_fonts(), typeset.palette()
    real_list = imgui.get_window_draw_list

    def draw() -> Any:
        return view.draw(fonts=fonts, palette=colours)

    def record(scroll: float) -> tuple[list[Any], tuple[float, float]]:
        recorder: list[_Recording] = []

        def listed() -> Any:
            recorder.append(_Recording(real_list()))
            return recorder[-1]

        def body() -> None:
            imgui.set_scroll_y(scroll)
            draw()

        h.frame(body)
        imgui.get_window_draw_list = listed  # type: ignore[assignment]
        try:
            h.frame(body)
        finally:
            imgui.get_window_draw_list = real_list  # type: ignore[assignment]
        origin = view.origin or (0.0, 0.0)
        return [call for each in recorder for call in each.texts], origin

    calls, origin = record(0.0)
    moved, moved_origin = record(63.0)
    here = {(c[5], round(c[2] - origin[0], 2), round(c[3] - origin[1], 2)) for c in calls}
    there = {
        (c[5], round(c[2] - moved_origin[0], 2), round(c[3] - moved_origin[1], 2)) for c in moved
    }

    # Runs ImGui would set exactly as the library did: the same font, size, ink
    # and baseline, each glyph one of ImGui's own advances after the last.
    exact: list[list[Any]] = []
    loose: list[list[Any]] = []
    pairs = exact_pairs = 0
    advances: dict[tuple[int, float, str], float] = {}
    for call in calls:
        font, size, x, y, ink, character = call
        joined = False
        if loose:
            head = loose[-1]
            if head[0] is font and head[1] == size and head[4] == ink and head[3] == y:
                head[5] += character
                joined = True
        if not joined:
            loose.append(list(call))
        if exact:
            head = exact[-1]
            same = head[0] is font and head[1] == size and head[4] == ink and head[3] == y
            if same:
                pairs += 1
                key = (id(font), size, head[5][-1])
                step = advances.get(key)
                if step is None:
                    baked = font.get_font_baked(size)
                    step = advances[key] = float(baked.get_char_advance(ord(head[5][-1])))
                if abs(head[6] + step - x) < 0.01:
                    exact_pairs += 1
                    head[5] += character
                    head[6] = x
                    continue
        exact.append([*call, x])

    stats = draw_stats = None

    def shipped() -> None:
        nonlocal stats
        stats = draw()

    def discarded() -> None:
        nonlocal draw_stats
        imgui.get_window_draw_list = lambda: _Discarding()  # type: ignore[assignment, return-value]
        try:
            draw_stats = draw()
        finally:
            imgui.get_window_draw_list = real_list  # type: ignore[assignment]

    vec = imgui.ImVec2
    built = [(c[0], c[1], vec(c[2], c[3]), c[4], c[5]) for c in calls]
    offsets = [(c[0], c[1], c[2] - origin[0], c[3] - origin[1], c[4], c[5]) for c in calls]
    exact_built = [(c[0], c[1], vec(c[2], c[3]), c[4], c[5]) for c in exact]
    loose_built = [(c[0], c[1], vec(c[2], c[3]), c[4], c[5]) for c in loose]
    no_snap = int(imgui.ImDrawListFlags_.text_no_pixel_snap)

    def replay(prepared: list[Any]) -> Callable[[], None]:
        def body() -> None:
            imgui.set_scroll_y(0.0)
            target = real_list()
            restore = target.flags
            target.flags = restore | no_snap
            add = target.add_text
            for font, size, at, ink, characters in prepared:
                add(font, size, at, ink, characters)
            target.flags = restore
            imgui.dummy(imgui.ImVec2(10.0, 4000.0))

        return body

    def replay_offsets() -> None:
        imgui.set_scroll_y(0.0)
        target = real_list()
        restore = target.flags
        target.flags = restore | no_snap
        add = target.add_text
        left, top = origin
        for font, size, dx, dy, ink, characters in offsets:
            add(font, size, vec(left + dx, top + dy), ink, characters)
        target.flags = restore
        imgui.dummy(imgui.ImVec2(10.0, 4000.0))

    def scrolled_home(body: Callable[[], None]) -> Callable[[], None]:
        def wrapped() -> None:
            imgui.set_scroll_y(0.0)
            body()

        return wrapped

    rows = [
        ("1. BareView.draw, as shipped", _median_frames(h, frames, scrolled_home(shipped))),
        (
            "2. BareView.draw into a draw list that discards",
            _median_frames(h, frames, scrolled_home(discarded)),
        ),
        (
            "3. its add_text calls alone, one a glyph, arguments ready",
            _median_frames(h, frames, replay(built)),
        ),
        (
            "4. the same, an ImVec2 made per glyph from a kept offset",
            _median_frames(h, frames, replay_offsets),
        ),
        (
            f"5. one add_text per exact run ({len(exact):,} calls)",
            _median_frames(h, frames, replay(exact_built)),
        ),
        (
            f"6. one add_text per face-size-ink-baseline run ({len(loose):,} calls,"
            " WRONG geometry)",
            _median_frames(h, frames, replay(loose_built)),
        ),
    ]
    assert stats is not None
    return Anatomy(
        lines=lines,
        glyphs=stats.glyphs,
        calls=len(calls),
        exact_runs=len(exact),
        exact_pairs=exact_pairs,
        pairs=pairs,
        loose_runs=len(loose),
        moved_calls=len(moved),
        shift_common=len(here & there),
        rows=rows,
        each_ns=_each_ns(),
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass
class Result:
    scenario: Scenario
    wall: Wall
    profile: Profiled | None
    alloc: Allocated | None


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def _ms(value: float) -> str:
    if value >= 100:
        return f"{value:.0f}"
    return f"{value:.1f}" if value >= 10 else f"{value:.3f}"


def _bytes(value: float) -> str:
    if value >= 1 << 20:
        return f"{value / (1 << 20):.1f} MB"
    if value >= 1 << 10:
        return f"{value / (1 << 10):.1f} kB"
    return f"{value:.0f} B"


def table(header: Sequence[str], rows: Sequence[Sequence[str]], left: int = 1) -> str:
    lines = [tuple(header), *[tuple(row) for row in rows]]
    widths = [max(len(line[i]) for line in lines) for i in range(len(header))]
    out = []
    for index, line in enumerate(lines):
        cells = [
            cell.ljust(widths[i]) if i < left else cell.rjust(widths[i])
            for i, cell in enumerate(line)
        ]
        out.append("  ".join(cells).rstrip())
        if index == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def _where(sc: Scenario) -> list[str]:
    return [sc.name, sc.size, sc.path]


def render_wall(results: list[Result]) -> str:
    rows = []
    for r in results:
        w, sc = r.wall, r.scenario
        glyphs = w.facts.get("glyphs")
        each = ""
        if isinstance(glyphs, int) and glyphs and sc.per == "frame":
            each = f"{statistics.median(w.off_ms) * 1000.0 / glyphs:.2f}"
        rows.append(
            [
                *_where(sc),
                sc.per,
                str(len(w.on_ms)),
                _ms(statistics.median(w.on_ms)),
                _ms(_p95(w.on_ms)),
                _ms(max(w.on_ms)),
                _ms(statistics.median(w.off_ms)),
                _ms(_p95(w.off_ms)),
                _ms(max(w.off_ms)),
                "" if glyphs is None else f"{glyphs:,}",
                each,
            ]
        )
    header = (
        "scenario", "size", "path", "per", "n", "on med", "on p95", "on max",
        "off med", "off p95", "off max", "glyphs", "us/glyph",
    )  # fmt: skip
    return table(header, rows, left=4)


def render_collector(results: list[Result]) -> str:
    rows = []
    for r in results:
        w, sc = r.wall, r.scenario
        scale = 1000.0 / len(w.on_ms)
        rows.append(
            [
                *_where(sc),
                " / ".join(f"{count * scale:.0f}" for count in w.inside),
                " / ".join(f"{count * scale:.0f}" for count in w.outside),
                _ms(sum(w.pauses_ms) * scale),
                _ms(max(w.pauses_ms, default=0.0)),
                f"{w.cyclic / len(w.off_ms):,.0f}",
                f"{w.net_tracked:,.0f}",
                f"{w.held:,}",
                _ms(w.full_ms),
            ]
        )
    header = (
        "scenario", "size", "path", "y/m/f per 1,000, in body", "outside it",
        "pause ms per 1,000", "longest", "cyclic garbage a sample", "net tracked a sample",
        "tracked objects held", "one full collection ms",
    )  # fmt: skip
    return table(header, rows, left=3)


def render_alloc(results: list[Result]) -> str:
    rows = []
    for r in results:
        if r.alloc is None:
            continue
        a = r.alloc
        whole = sum(a.owners.values()) or 1.0
        rows.append(
            [
                *_where(r.scenario),
                _bytes(a.survivors),
                f"{a.blocks:,.0f}",
                _bytes(a.peak),
                f"{100.0 * a.owners.get('advtext', 0.0) / whole:.0f}%",
                f"{100.0 * a.owners.get('seam', 0.0) / whole:.0f}%",
            ]
        )
    header = (
        "scenario", "size", "path", "held at end, a sample", "blocks", "peak in body",
        "under advtext", "under the seam only",
    )  # fmt: skip
    return table(header, rows, left=3)


def render_pairs(results: list[Result]) -> str:
    """Each scenario's two paths side by side: what the seam's call adds."""
    by_key: dict[tuple[str, str, str], dict[str, Result]] = {}
    for r in results:
        by_key.setdefault(r.scenario.key, {})[r.scenario.path] = r
    rows = []
    for (_group, name, size), pair in by_key.items():
        if len(pair) != 2:
            continue
        lib = statistics.median(pair["advtext"].wall.off_ms)
        seam = statistics.median(pair["seam"].wall.off_ms)
        own = ""
        profile = pair["seam"].profile
        if profile is not None and profile.total_ms:
            share = sum(ms for bucket, ms in profile.buckets.items() if "seam" in bucket)
            own = f"{100.0 * share / profile.total_ms:.0f}%"
        rows.append(
            [
                name,
                size,
                _ms(lib),
                _ms(seam),
                f"{seam - lib:+.3f}",
                f"{seam / lib:.2f}" if lib else "",
                own,
            ]
        )
    header = (
        "scenario", "size", "advtext ms", "seam ms", "seam - advtext", "ratio",
        "seam's share of its own profile",
    )  # fmt: skip
    return table(header, rows, left=2)


def render_detail(r: Result, top: int) -> str:
    sc, w = r.scenario, r.wall
    out = [f"### {sc.group}: {sc.name} | {sc.size} | {sc.path}", ""]
    if w.facts:
        out.append("facts: " + ", ".join(f"{k} {v}" for k, v in w.facts.items()))
    if w.parts_ms:
        out.append(
            "parts of the body, median ms, timed without the profiler: "
            + ", ".join(f"{name} {_ms(ms)}" for name, ms in w.parts_ms.items())
        )
    p = r.profile
    if p is not None:
        out.append(
            f"under cProfile: {_ms(p.total_ms)} ms a {sc.per}, x{p.inflation:.1f} the"
            " unprofiled collector-on median"
        )
        out.append("")
        ordered = sorted(p.buckets.items(), key=lambda item: item[1], reverse=True)
        rows = [
            [name, _ms(ms), f"{100.0 * ms / p.total_ms:.1f}%"]
            for name, ms in ordered
            if ms >= 0.005 * p.total_ms
        ]
        out += [table(("own time by owner", f"ms a {sc.per}", "share"), rows), ""]

        def fn_rows(fns: list[Fn]) -> list[list[str]]:
            return [
                [
                    f.label,
                    f"{f.calls:,.1f}",
                    _ms(f.tot_ms),
                    _ms(f.untimed_ms),
                    _ms(f.cum_ms),
                    f"{100.0 * (f.tot_ms + f.untimed_ms) / p.total_ms:.1f}%",
                ]
                for f in fns
            ]

        header = (
            "function", f"calls a {sc.per}", "own ms", "untimed ms", "cumulative ms",
            "own + untimed share",
        )  # fmt: skip
        if sc.path == "advtext":
            by_cum = sorted(p.functions, key=lambda f: f.cum_ms, reverse=True)[:top]
            by_own = sorted(p.functions, key=lambda f: f.tot_ms + f.untimed_ms, reverse=True)[:top]
            out += [f"advtext, top {top} by cumulative time", table(header, fn_rows(by_cum)), ""]
            out += [
                f"advtext, top {top} by own time plus untimed",
                table(header, fn_rows(by_own)),
                "",
            ]
            edge_rows = [
                [e.label, f"{e.calls:,.1f}", _ms(e.cum_ms), f"{100.0 * e.cum_ms / p.total_ms:.1f}%"]
                for e in p.edges[:15]
            ]
            out += [
                "calls out of advtext, top 15 by time under the call",
                table(
                    ("callee  <-  advtext caller", f"calls a {sc.per}", "ms", "share"), edge_rows
                ),
                "",
            ]
        else:
            by_own = sorted(p.seam, key=lambda f: f.tot_ms, reverse=True)[:12]
            out += ["the seam's own functions, top 12 by own time", table(header, fn_rows(by_own))]
            out.append("")
    a = r.alloc
    if a is not None and a.lines and sc.path == "advtext":
        rows = [[where, _bytes(size), f"{count:,.0f}"] for where, size, count in a.lines[:12]]
        out += [
            "allocated in the body and held at its end, by innermost advtext line, top 12",
            table(("function  |  line", f"bytes a {sc.per}", "blocks"), rows),
            "",
        ]
    return "\n".join(out)


def render_anatomy(found: Anatomy) -> str:
    base = found.rows[0][1]
    rows = [
        [
            name,
            _ms(ms),
            f"{ms * 1000.0 / found.glyphs:.2f}",
            f"{100.0 * ms / base:.0f}%",
        ]
        for name, ms in found.rows
    ]
    return "\n".join(
        [
            f"### anatomy of one draw | {found.lines} lines of room | advtext",
            "",
            f"{found.glyphs:,} glyphs, {found.calls:,} add_text calls recorded."
            f" {found.exact_pairs:,} of {found.pairs:,} neighbouring glyphs on one baseline in"
            " one face, size and ink sit exactly one ImGui advance apart, which makes"
            f" {found.exact_runs:,} runs ImGui would set where the library did"
            f" ({found.calls / max(found.exact_runs, 1):.1f} glyphs a run)."
            f" Scrolled 63 px the view issued {found.moved_calls:,} glyphs, {found.shift_common:,}"
            " of them at the same offset from its origin as before, to a hundredth of a pixel.",
            "",
            table(("what", "median ms", "us a glyph", "of row 1"), rows),
            "",
            table(
                ("one of, alone", "ns"),
                [[name, f"{ns:,.0f}"] for name, ns in found.each_ns],
            ),
            "",
        ]
    )


def _load() -> str:
    one, five, fifteen = os.getloadavg()
    return f"{one:.2f} {five:.2f} {fifteen:.2f}"


def _advtext_revision() -> str:
    """The library checkout's HEAD, read from its files: no subprocess leaves this tree."""
    git = Path(ADVTEXT_DIR).parent / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = git / head[5:]
            return f"{ref.read_text(encoding='utf-8').strip()[:10]} ({head[5:]})"
        return head[:10]
    except OSError:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0].strip())
    ap.add_argument("--only", nargs="+", choices=GROUPS, default=list(GROUPS))
    ap.add_argument("--kinds", nargs="+", choices=KINDS, default=list(KINDS))
    ap.add_argument(
        "--sizes", nargs="+", type=int, default=list(STREAM_SIZES),
        help="lines in a stream scenario's open block",
    )  # fmt: skip
    ap.add_argument("--frames", type=int, default=300, help="samples for a frame scenario")
    ap.add_argument("--width", type=float, default=900.0)
    ap.add_argument("--top", type=int, default=25, help="rows of each profile table")
    ap.add_argument("--no-profile", action="store_true", help="skip the cProfile pass")
    ap.add_argument("--no-alloc", action="store_true", help="skip the tracemalloc pass")
    ap.add_argument("--out", type=Path, default=None, help="also write the tables here")
    args = ap.parse_args()
    if args.frames < 20:
        ap.error("--frames must be at least 20 for a p95 to mean anything")

    load_before = _load()
    started = time.monotonic()
    clock = [started]

    def lap() -> str:
        now = time.monotonic()
        clock[0], took = now, now - clock[0]
        return f"{took:.1f}s"

    harness = Harness(args.width)
    results: list[Result] = []
    anatomies: list[Anatomy] = []
    try:
        todo = scenarios(args.only, args.kinds, args.frames, args.sizes)
        for index, sc in enumerate(todo, 1):
            print(
                f"  .. {index}/{len(todo)} {sc.group} | {sc.name} | {sc.size} | {sc.path}",
                file=sys.stderr,
                flush=True,
            )
            lap()
            measured = wall(harness, sc)
            laps = [f"wall {lap()}"]
            profile = None
            if not args.no_profile:
                profile = profiled(harness, sc, statistics.median(measured.on_ms))
                laps.append(f"profile {lap()}")
            alloc = None if args.no_alloc else allocated(harness, sc)
            if alloc is not None:
                laps.append(f"alloc {lap()}")
            print(f"     {', '.join(laps)}", file=sys.stderr, flush=True)
            results.append(Result(sc, measured, profile, alloc))
        if "anatomy" in args.only:
            for lines in (30, 60, 120):
                print(f"  .. anatomy | {lines} lines", file=sys.stderr, flush=True)
                anatomies.append(anatomy(harness, lines, args.frames))
    finally:
        frames = harness.frames
        harness.close()

    out = [
        f"profile_typeset: pane {args.width:.0f} px wide | {args.frames} samples a frame scenario"
        f" | {frames:,} frames in {time.monotonic() - started:.0f}s",
        f"python {sys.version.split()[0]}, imgui {imgui.get_version()}, {os.cpu_count()} cpus,"
        f" gc thresholds {gc.get_threshold()}",
        f"advtext {ADVTEXT_DIR} at {_advtext_revision()}",
        f"faces: preset {typeset.DEFAULT_PRESET}; budget: unlimited (everything built at a stroke)",
        f"load average (1/5/15 min) before: {load_before} | after: {_load()}",
        "Milliseconds of CPU inside the body; headless, nothing rasterised, no frame pacing.",
        "",
    ]
    if results:
        out += [
            "## 1. Wall time, collector on and off",
            "",
            "ms a sample. `per`: frame = the body inside one frame, a few hundred of them;"
            " call = one call over state rebuilt for it.",
            "",
            render_wall(results),
            "",
            "## 2. The collector",
            "",
            "Collections that began inside a body, young / middle / full, scaled to 1,000"
            " samples; their pauses; unreachable cyclic objects a sample left with the"
            " collector off; tracked objects made less freed across a body; what the"
            " scenario's state added to the heap; and one full collection over it.",
            "",
            render_collector(results),
            "",
        ]
        if any(r.alloc is not None for r in results):
            out += [
                "## 3. Allocation (tracemalloc)",
                "",
                "What a body allocated and still held when it returned: not what it"
                " allocated and freed on the way.",
                "",
                render_alloc(results),
                "",
            ]
        out += [
            "## 4. What the seam adds",
            "",
            "Collector-off medians of the two paths. The seam's first frame, width"
            " change and diff rows also fit, dress and book-keep; its steady rows are"
            " the library's draw and a lookup. Two rows compare unequal bodies, and"
            " their difference is not the seam's cost: at a new width the seam's call"
            " draws and the library's `layout_for` does not; and the settled view of a"
            " closed run is built from the stream's blocks on the library path and"
            " from the text, parsed again, on the seam's.",
            "",
            render_pairs(results),
            "",
        ]
    if anatomies:
        out += ["## 5. One draw, taken apart", ""]
        out += [render_anatomy(found) for found in anatomies]
    if results and not args.no_profile:
        out += ["## 6. By function", ""]
        out += [render_detail(r, args.top) for r in results]
    text = "\n".join(out)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
