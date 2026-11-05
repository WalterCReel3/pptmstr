"""
A committed ```mermaid fence drawn as a figure: when it is hosted, by what, at what
height, how it draws natively (no card, no texture, the theme's colours and the
author's, culled, hovered, rebuilt one figure a frame), what a layout that fails
leaves on screen; and a ```svg fence, which is code.

Headless: an ImGui context with no backend. The render process is
``tests/test_figure_render.py``'s; here a recording stand-in answers for it. Each
diagram's display list
is put in the held layouts before it is drawn, as the render child's answer would
put it (``tests/test_figure_mermaid.py`` covers asking for it), so a figure here is
drawn from its first frame. Nothing here draws in a real window.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator
from dataclasses import replace
from typing import Any

import pytest

from pptmstr import theme
from pptmstr.figures import displaylist, host, mermaid, render
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Kind, Part, views

WINDOW = (700.0, 600.0)
FIG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
    '<rect width="200" height="100" fill="#c33"/></svg>'
)
# A diagram's source and the layout put in for it: FIG, 200 by 100.
BODY = "flowchart LR\n  a --> b"
FENCE = f"```mermaid\n{BODY}\n```"
# A source whose lines, shown under a failure's reason, stand taller than the figure.
TALL = BODY + "".join(f"\n  n{i} --> n{i + 1}" for i in range(12))
OPEN = f"Intro paragraph.\n\n```mermaid\n{BODY}\n"


class Renderer:
    """
    The render process as the host sees it: what it was asked, and what it answers
    at the next poll.
    """

    def __init__(self) -> None:
        self.layouts: list[tuple[Hashable, str]] = []
        self.answers: list[render.Result] = []

    def lay_out(self, key: Hashable, source: str) -> None:
        self.layouts.append((key, source))

    def poll(self) -> list[render.Result]:
        out, self.answers = self.answers, []
        return out

    def answer_last(self) -> None:
        self.answers.append(render.Laid(self.layouts[-1][0], displaylist.from_svg(FIG)))


@pytest.fixture
def figures(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Renderer]:
    fake = Renderer()
    host.release_all()
    monkeypatch.setattr(host._STATE, "renderer", lambda: fake)
    _marked()
    yield fake
    host.release_all()


def frame(im: Any, body: Callable[[], object]) -> None:
    """
    One frame as the app runs it: the seam's turn and the figures' pump before
    ``new_frame`` (``app.begin_frame``), then ``body`` in a window.
    """
    typeset.pre_frame()
    host.pump()
    im.new_frame()
    im.set_next_window_pos(im.ImVec2(10.0, 10.0))
    im.set_next_window_size(im.ImVec2(*WINDOW))
    im.begin("##figure-test")
    try:
        body()
    finally:
        im.end()
        im.render()


def figures_hosted(view: Any) -> list[host.FigureWidget]:
    return [w for n in view.hosts if isinstance(w := view.hosted(n), host.FigureWidget)]


# -- when a fence is hosted ------------------------------------------------------------


CHUNKINGS: dict[str, list[str]] = {
    "close-marks-then-newline": [OPEN, "```", "\n"],
    "close-line-with-next-para": [OPEN, "```\n\nAfter the fence.\n"],
    "char-by-char": list(OPEN + "```\n\nAfter the fence.\n"),
}


@pytest.mark.parametrize("name", CHUNKINGS)
def test_a_mermaid_fence_is_hosted_on_commit_while_the_stream_is_open(
    figures: Renderer, name: str
) -> None:
    """
    The fence is code under its caption for as long as it is open, at every frame,
    and a figure from the frame its closing line ends in, with the stream still
    open.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    fed = ""
    for piece in CHUNKINGS[name]:
        handle.append(piece)
        fed += piece
        frame(_IM[0], lambda: handle.draw())
        closed_line = BODY in fed and "```\n" in fed.split(BODY, 1)[1]
        hosted = figures_hosted(handle.view)
        if not closed_line:
            assert hosted == [], f"hosted while open, after {fed[-12:]!r}"
    assert not handle.closed
    (widget,) = figures_hosted(handle.view)
    assert widget.mark.display is not None and widget.mark.display.width == 200.0
    assert widget.text == FENCE


SVG_OPEN = f"Intro paragraph.\n\n```svg\n{FIG}\n"
SVG_CHUNKINGS: dict[str, list[str]] = {
    "close-marks-then-newline": [SVG_OPEN, "```", "\n"],
    "close-line-with-next-para": [SVG_OPEN, "```\n\nAfter the fence.\n"],
    "char-by-char": list(SVG_OPEN + "```\n\nAfter the fence.\n"),
}


@pytest.mark.parametrize("name", SVG_CHUNKINGS)
def test_an_svg_fence_is_code_under_its_caption_at_every_frame_and_once_closed(
    figures: Renderer, name: str
) -> None:
    """
    No row of the table names svg (planning record §8): open, committed and closed,
    it is a code block captioned svg, never marked, never hosted, with no line
    saying it was not drawn.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    for piece in SVG_CHUNKINGS[name]:
        handle.append(piece)
        frame(_IM[0], lambda: handle.draw())
        assert not handle.view.hosts
    handle.close()
    frame(_IM[0], lambda: handle.draw())
    assert not handle.view.hosts
    texts = cache.texts("reply")
    assert f"svg\n{FIG}" in texts
    assert not any("not drawn" in text for text in texts)
    assert figures.layouts == []


def test_a_settled_svg_fence_is_code_and_never_marked(figures: Renderer) -> None:
    source = f"Before.\n\n```svg\n{FIG}\n```\n\nAfter.\n"
    cache = typeset.ViewCache()
    frame(_IM[0], lambda: typeset.markdown(cache, "doc", source))
    frame(_IM[0], lambda: typeset.markdown(cache, "doc", source))
    assert not cache.view("doc").hosts
    texts = cache.texts("doc")
    assert f"svg\n{FIG}" in texts
    assert not any("not drawn" in text for text in texts)
    assert not host.is_figure_fence("svg") and not views.settles_rendered("svg")


def test_a_fence_that_ends_on_its_marks_is_hosted_once_the_stream_closes(
    figures: Renderer,
) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    for piece in (OPEN, "```"):
        handle.append(piece)
        frame(_IM[0], lambda: handle.draw())
        assert figures_hosted(handle.view) == []
    handle.close()
    frame(_IM[0], lambda: handle.draw())
    assert len(figures_hosted(handle.view)) == 1


def test_an_open_fence_is_code_under_its_caption(figures: Renderer) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(OPEN + "<!-- more to come -->\n")
    for _ in range(3):
        frame(_IM[0], lambda: handle.draw())
    assert not handle.view.hosts
    assert any(text.startswith("mermaid\nflowchart") for text in cache.texts("reply"))
    assert figures.layouts == []


def test_an_open_stream_still_hosts_no_diff_beside_a_committed_figure(
    figures: Renderer, columns_hold: Any
) -> None:
    diff = "```diff\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n```\n\n"
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(diff + OPEN + "```\n\nAfter.\n")
    for _ in range(2):
        frame(_IM[0], lambda: handle.draw())
    view = handle.view
    assert len(figures_hosted(view)) == 1
    assert len(view.hosts) == 1, "the diff fence stays code while the stream is open"
    handle.close()
    for _ in range(2):
        frame(_IM[0], lambda: handle.draw())
    assert len(handle.view.hosts) == 2


def test_a_settled_document_hosts_its_mermaid_fence(figures: Renderer) -> None:
    cache = typeset.ViewCache()
    frame(_IM[0], lambda: typeset.markdown(cache, "doc", OPEN + "```\n\nAfter.\n"))
    (widget,) = figures_hosted(cache.view("doc"))
    assert widget.height_for(600.0) == 100.0, "its natural height: no card, no margin"
    assert widget.height_for(100.0) == 50.0


def _no_merman(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mermaid, "_VERSION", [None])
    monkeypatch.setattr(host, "_MARKS", type(host._MARKS)())


def test_a_refused_mermaid_fence_stays_code_under_its_reason(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_merman(monkeypatch)
    source = f"Before.\n\n```mermaid\n{BODY}\n```\n\nAfter.\n"
    cache = typeset.ViewCache()
    frame(_IM[0], lambda: typeset.markdown(cache, "doc", source))
    frame(_IM[0], lambda: typeset.markdown(cache, "doc", source))
    view = cache.view("doc")
    assert not view.hosts
    texts = cache.texts("doc")
    (at,) = [n for n, text in enumerate(texts) if text.startswith("mermaid not drawn: ")]
    assert texts[at] == f"mermaid not drawn: {mermaid.missing_reason()}"
    assert texts[at + 1] == f"mermaid\n{BODY}"
    warn = theme.P.warn.u32
    assert (0, len(texts[at]), warn) in view._colours.get(at, ())


def test_a_refused_mermaid_fence_in_a_stream_is_code_with_its_reason_once_committed(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_merman(monkeypatch)
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(f"```mermaid\n{BODY}\n```\n\nAfter.\n")
    for _ in range(2):
        frame(_IM[0], lambda: handle.draw())
    assert not handle.view.hosts
    assert any(text.startswith("mermaid not drawn: ") for text in cache.texts("reply"))


# -- the dispatcher is what the fence-carrying sites install ----------------------------


def test_a_stream_and_a_document_are_given_the_dispatcher_and_a_literal_view_is_not(
    figures: Renderer,
) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append("Open.\n")
    frame(_IM[0], lambda: handle.draw())
    assert isinstance(handle.view.host_for, views._Hosts)
    assert handle.view.host_for.diffs is False
    frame(_IM[0], lambda: typeset.markdown(cache, "doc", "Settled.\n"))
    assert isinstance(cache.view("doc").host_for, views._Hosts)
    literal = [Part("x = 1\n", Kind.LITERAL)]
    frame(_IM[0], lambda: typeset.document(cache, "lit", literal, literal=True))
    assert all(v.host_for is None for v in cache.views("lit"))


def test_the_dispatcher_hosts_a_diff_only_where_diffs_are_hosted(figures: Renderer) -> None:
    from dataclasses import replace

    from imscribe import parse_markdown

    from pptmstr.ui.typeset import themes

    blocks: tuple[Any, ...] = parse_markdown(
        "```diff\n-a\n+b\n```\n", themes.document_theme()
    ).blocks
    (block,) = blocks
    marked = replace(block, language=views._HOSTED_INFO)
    assert views._hosts(False)(None, 0, marked) is None
    figure = replace(block, text=BODY, language=_marked())

    class View:
        def hosted(self, number: int) -> None:
            return None

    assert isinstance(views._hosts(False)(View(), 0, figure), host.FigureWidget)
    assert isinstance(views._hosts(True)(View(), 0, figure), host.FigureWidget)


# -- height, re-hosting, drawing --------------------------------------------------------


def test_a_figure_rehosted_after_forget_answers_its_height_at_once_and_asks_for_nothing(
    figures: Renderer,
) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    text = OPEN + "```\n\nAfter.\n"
    handle.append(text)
    frame(_IM[0], lambda: handle.draw())
    first = handle.view
    (widget,) = figures_hosted(first)
    assert widget.drawn[1] > 0, "drawn natively from its first frame"
    # Text that does not extend what is held: the stream lets go of its view.
    handle.set_text("Another start.\n\n" + text)
    frame(_IM[0], lambda: handle.draw())
    assert handle.view is not first
    (widget,) = figures_hosted(handle.view)
    assert widget.height_for(500.0) == 100.0
    frame(_IM[0], lambda: handle.draw())
    assert figures.layouts == [], "the layout is held"


@pytest.mark.parametrize(
    "density, origin",
    [(1.0, (40.0, 121.0)), (1.5, (61 / 1.5, 182 / 1.5)), (2.0, (40.5, 121.0))],
)
def test_a_figure_is_drawn_at_the_nearest_device_pixel_inside_its_blocks_clip(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch, density: float, origin: tuple[float, float]
) -> None:
    """
    The geometry is moved to the nearest whole pixel of the framebuffer, and the
    clip is the block's own rectangle, unsnapped: nothing is drawn above or left of
    where imscribe put it.
    """
    from imgui_bundle import imgui
    from imgui_bundle.imgui import internal

    monkeypatch.setattr(host, "_density", lambda im: density)
    widget = host.FigureWidget(_marked(), BODY)
    seen: list[tuple[float, float, float, float, float, float]] = []
    shade = internal.shade_verts_transform_pos

    def moved(dl: Any, v0: int, v1: int, pivot: Any, k: float, a: float, to: Any) -> None:
        low, high = dl.get_clip_rect_min(), dl.get_clip_rect_max()
        seen.append((to.x, to.y, low.x, low.y, high.x, high.y))
        shade(dl, v0, v1, pivot, k, a, to)

    monkeypatch.setattr(internal, "shade_verts_transform_pos", moved)

    def body() -> None:
        imgui.set_cursor_screen_pos(imgui.ImVec2(40.4, 121.237))
        widget.draw(width_px=150.0)
        # The view this stands in for claims the block's room after its hosts.
        imgui.dummy(imgui.ImVec2(1.0, 1.0))

    frame(_IM[0], body)
    assert seen
    for x, y, cx0, cy0, cx1, cy1 in seen:
        assert (x, y) == pytest.approx(origin, abs=1e-4)
        assert (x * density, y * density) == pytest.approx(
            (round(x * density), round(y * density)), abs=1e-3
        )
        # ImGui keeps clip rectangles in float: the block's corner, unsnapped.
        assert (cx0, cy0) == pytest.approx((40.4, 121.237), abs=1e-4)
        assert (cx1, cy1) == pytest.approx((40.4 + 150.0, 121.237 + 75.0), abs=1e-4)


def test_a_layout_that_fails_keeps_the_fence_source_on_screen_under_its_reason(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    In imscribe's order, ``height_for`` and then ``draw``: from the frame the failure
    arrives in, the height asked is the failed form's, and what is drawn is clipped
    to that rectangle, so nothing lands on the prose below.
    """
    from imgui_bundle import imgui

    widget = _unlaid(TALL)
    frame(_IM[0], lambda: widget.draw(width_px=300.0))
    figure_height = widget.height_for(300.0)
    key = figures.layouts[-1][0]
    figures.answers.append(render.Failed(key, "merman: Diagram parse error"))
    clips: list[tuple[float, float, float, float]] = []
    push = imgui.push_clip_rect

    def recorded(low: Any, high: Any, intersect: bool) -> None:
        clips.append((low.x, low.y, high.x, high.y))
        push(low, high, intersect)

    monkeypatch.setattr(imgui, "push_clip_rect", recorded)
    heights: list[float] = []
    at: list[tuple[float, float]] = []

    def body() -> None:
        heights.append(widget.height_for(300.0))
        here = imgui.get_cursor_screen_pos()
        at.append((here.x, here.y))
        widget.draw(width_px=300.0)

    frame(_IM[0], body)
    assert heights[0] > figure_height, "the frame the failure lands in lays out the source"
    x, y = at[0]
    assert clips == [(x, y, x + 300.0, y + heights[0])]
    frame(_IM[0], body)
    assert heights[1] == heights[0]
    assert widget.text == f"```mermaid\n{TALL}\n```"


def test_the_failed_forms_height_is_measured_once_for_a_width(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from imgui_bundle import imgui

    widget = _unlaid()
    frame(_IM[0], lambda: widget.draw(width_px=300.0))
    figures.answers.append(render.Failed(figures.layouts[-1][0], "refused"))
    measured: list[str] = []
    calc = imgui.calc_text_size

    def counting(text: str, *rest: Any) -> Any:
        measured.append(text)
        return calc(text, *rest)

    monkeypatch.setattr(imgui, "calc_text_size", counting)
    for _ in range(4):
        frame(_IM[0], lambda: (widget.height_for(300.0), widget.draw(width_px=300.0)))
    assert measured.count(widget.text) == 1
    frame(_IM[0], lambda: widget.height_for(250.0))
    assert measured.count(widget.text) == 2


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_transient_failure_is_asked_again_once_after_the_back_off(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    monkeypatch.setattr(host._STATE, "clock", clock)
    widget = _unlaid()

    def draw() -> None:
        widget.draw(width_px=150.0)

    frame(_IM[0], draw)
    key = figures.layouts[-1][0]
    figures.answers.append(render.Failed(key, "render process died", transient=True))
    for _ in range(3):
        frame(_IM[0], draw)
    assert len(figures.layouts) == 1, "not asked again inside the back-off"
    assert widget.height_for(150.0) == host.PLACEHOLDER_HEIGHT, "the placeholder's room"
    clock.now += host.RETRY_AFTER_S
    frame(_IM[0], draw)
    frame(_IM[0], draw)
    assert len(figures.layouts) == 2, "asked once more after it"
    figures.answer_last()
    frame(_IM[0], draw)
    assert widget._failure() is None
    assert widget.height_for(150.0) == 75.0, "drawn from the layout that arrived"


def test_a_second_transient_failure_is_kept(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    monkeypatch.setattr(host._STATE, "clock", clock)
    widget = _unlaid()

    def draw() -> None:
        widget.draw(width_px=150.0)

    frame(_IM[0], draw)
    key = figures.layouts[-1][0]
    figures.answers.append(render.Failed(key, "render took longer than 10 s", transient=True))
    frame(_IM[0], draw)
    clock.now += host.RETRY_AFTER_S
    frame(_IM[0], draw)
    assert len(figures.layouts) == 2
    figures.answers.append(render.Failed(key, "render took longer than 10 s", transient=True))
    frame(_IM[0], draw)
    assert widget._failure() == "render took longer than 10 s"
    clock.now += 10 * host.RETRY_AFTER_S
    for _ in range(3):
        frame(_IM[0], draw)
    assert len(figures.layouts) == 2, "never asked again"


def test_a_failure_that_is_not_transient_is_kept_at_once(figures: Renderer) -> None:
    widget = _unlaid()
    frame(_IM[0], lambda: widget.draw(width_px=150.0))
    figures.answers.append(render.Failed(figures.layouts[-1][0], "over the cap"))
    for _ in range(3):
        frame(_IM[0], lambda: widget.draw(width_px=150.0))
    assert widget._failure() == "over the cap"
    assert len(figures.layouts) == 1


def test_a_result_for_a_key_the_host_did_not_ask_with_is_ignored(figures: Renderer) -> None:
    widget = _unlaid()
    frame(_IM[0], lambda: widget.draw(width_px=150.0))
    (key, _), sha = figures.layouts[-1], widget.mark.sha
    figures.answers += [
        render.Failed(sha, "a bare sha"),
        render.Failed(("raster", sha), "another kind"),
        render.Failed((key,), "a short key"),
    ]
    frame(_IM[0], lambda: widget.draw(width_px=150.0))
    assert widget._failure() is None and sha in host._STATE.laying


def test_the_pump_takes_results_with_no_figure_on_screen(figures: Renderer) -> None:
    polled: list[int] = []
    poll = figures.poll

    def counted() -> list[render.Result]:
        polled.append(1)
        return poll()

    figures.poll = counted  # type: ignore[method-assign]
    for _ in range(3):
        frame(_IM[0], lambda: None)
    assert len(polled) == 3


def test_figure_widget_draw_does_not_pump(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    widget = host.FigureWidget(_marked(), BODY)
    calls: list[int] = []
    monkeypatch.setattr(host, "pump", lambda: calls.append(1))
    _IM[0].new_frame()
    try:
        widget.draw(width_px=150.0)
    finally:
        _IM[0].end_frame()
    assert calls == []


def test_only_a_figure_fence_changes_its_answer_at_commit(figures: Renderer) -> None:
    """
    A stream's code fence is the same block object before and after commit (S1),
    and is answered with the same block too, so the view does not describe it
    again; a mermaid fence is answered anew, as a figure.
    """
    from imscribe import parse_markdown

    from pptmstr.ui.typeset import themes

    blocks = parse_markdown(f"```python\nx = 1\n```\n\n{FENCE}\n", themes.document_theme()).blocks

    class Library:
        revision = 0
        committed: tuple[Any, ...] = ()
        sources: tuple[Any, ...] = ()

        def __init__(self) -> None:
            self.blocks = blocks

    library = Library()
    captioned = views._Captioned(library)
    before = captioned.blocks
    library.committed = tuple(blocks)
    after = captioned.blocks
    assert after[0] is before[0], "the python fence keeps its answer"
    assert after[-1] is not before[-1]
    assert isinstance(after[-1].language, host.FigureMark)


# -- drawn natively ---------------------------------------------------------------------

# Two nodes and an edge between them, in merman's markup: the author's fill on A and
# a label in A in the theme's own ink, B in roles only.
NODES = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
    '<g class="node" id="merman-n-A" transform="translate(10,30)">'
    '<rect width="60" height="40" style="fill:#ff9966 !important"/>'
    '<text x="30" y="20" text-anchor="middle" dominant-baseline="central">Alpha</text></g>'
    '<g class="node" id="merman-n-B" transform="translate(130,30)">'
    '<rect width="60" height="40"/></g>'
    '<path class="flowchart-link" data-id="L_A_B" d="M70 50 L130 50"/></svg>'
)


def _nodes() -> displaylist.DisplayList:
    painted = displaylist.Paints(fill=frozenset({0xFF9966}))
    return displaylist.from_svg(NODES, displaylist.Authored(ids={"n-A": painted}))


class Drawn:
    """
    The window's draw list, recording each primitive with its colour and the clip in
    force; anything else goes to the real list.
    """

    def __init__(self, real: Any) -> None:
        self._real = real
        self.calls: list[tuple[str, int, tuple[float, float, float, float]]] = []

    def _note(self, kind: str, colour: int) -> None:
        low, high = self._real.get_clip_rect_min(), self._real.get_clip_rect_max()
        self.calls.append((kind, int(colour), (low.x, low.y, high.x, high.y)))

    def add_convex_poly_filled(self, points: Any, colour: int) -> None:
        self._note("fill", colour)
        self._real.add_convex_poly_filled(points, colour)

    def add_concave_poly_filled(self, points: Any, colour: int) -> None:
        self._note("fill", colour)
        self._real.add_concave_poly_filled(points, colour)

    def add_polyline(self, points: Any, colour: int, *rest: Any) -> None:
        self._note("line", colour)
        self._real.add_polyline(points, colour, *rest)

    def add_rect_filled(self, low: Any, high: Any, colour: int, *rest: Any) -> None:
        self._note("rect", colour)
        self._real.add_rect_filled(low, high, colour, *rest)

    def add_text(self, font: Any, size: float, at: Any, colour: int, text: str) -> None:
        self._note(f"text {text}", colour)
        self._real.add_text(font, size, at, colour, text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@pytest.fixture
def drawn(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Drawn]]:
    """
    Every draw list a figure asks for, recorded; and no image may be drawn.
    """
    from imgui_bundle import imgui
    from imgui_bundle.imgui import internal

    lists: list[Drawn] = []
    real = imgui.get_window_draw_list
    shade = internal.shade_verts_transform_pos

    def recorded() -> Drawn:
        lists.append(Drawn(real()))
        return lists[-1]

    def moved(dl: Any, *rest: Any) -> None:
        shade(getattr(dl, "_real", dl), *rest)

    def image(*args: Any) -> None:
        raise AssertionError("a native figure draws no texture")

    monkeypatch.setattr(imgui, "get_window_draw_list", recorded)
    monkeypatch.setattr(internal, "shade_verts_transform_pos", moved)
    monkeypatch.setattr(imgui, "image", image)
    yield lists


def _packed(rgb: int) -> int:
    return int(theme.col(rgb).u32)


@pytest.mark.parametrize("theme_name", ["dark", "light"])
def test_a_figure_is_drawn_on_the_pane_in_the_themes_colours_and_the_authors(
    figures: Renderer, drawn: list[Drawn], theme_name: str
) -> None:
    """
    No card and no texture: B's fill is the theme's node colour over the pane, A's
    the author's as written, in either theme; A's label in the theme's ink, which is
    made legible on the author's orange where it is not; all inside the block.
    """
    from imgui_bundle import imgui

    theme.set_theme(theme_name)
    widget = host.FigureWidget(_marked(display=_nodes()), BODY)

    def body() -> None:
        imgui.set_cursor_screen_pos(imgui.ImVec2(40.0, 120.0))
        widget.draw(width_px=300.0)
        imgui.dummy(imgui.ImVec2(1.0, 1.0))

    frame(_IM[0], body)
    calls = [c for d in drawn for c in d.calls]
    assert not any(kind == "rect" for kind, *_ in calls), "no card, no knockout"
    fills = [colour for kind, colour, _ in calls if kind == "fill"]
    palette = theme.current()
    assert _packed(0xFF9966) in fills, "the author's fill, as written"
    others = [c for c in fills if c != _packed(0xFF9966)]
    assert len(others) == 1
    (label,) = [colour for kind, colour, _ in calls if kind == "text Alpha"]
    assert displaylist.contrast(_rgb_of(label), 0xFF9966) >= displaylist.TEXT_CONTRAST
    assert {clip for *_, clip in calls} == {(40.0, 120.0, 340.0, 220.0)}
    assert widget.drawn[0] == widget.drawn[1] > 0
    assert palette.name == theme_name


def _rgb_of(packed: int) -> int:
    """
    ``IM_COL32`` (ABGR in a little-endian word) back to ``0xRRGGBB``.
    """
    return ((packed & 0xFF) << 16) | (packed & 0xFF00) | ((packed >> 16) & 0xFF)


def test_role_colours_change_with_the_theme_and_the_figure_is_not_rebuilt(
    figures: Renderer, drawn: list[Drawn]
) -> None:
    widget = host.FigureWidget(_marked(display=_nodes()), BODY)
    seen: dict[str, set[int]] = {}
    for name in ("dark", "light", "dark"):
        theme.set_theme(name)
        drawn.clear()
        frame(_IM[0], lambda: widget.draw(width_px=300.0))
        seen.setdefault(name, set()).update(
            colour for d in drawn for kind, colour, _ in d.calls if kind == "line"
        )
    assert seen["dark"] and seen["light"] and seen["dark"].isdisjoint(seen["light"])
    assert widget.rebuilds == 1, "a theme switch looks colours up; geometry stands"


def test_an_illegible_author_colour_is_adjusted_and_logged_once_per_theme(
    figures: Renderer, drawn: list[Drawn]
) -> None:
    from pptmstr.log import LOG

    theme.set_theme("dark")
    widget = host.FigureWidget(_marked(display=_nodes()), BODY)
    before = LOG.snapshot()[1]
    for _ in range(3):
        frame(_IM[0], lambda: widget.draw(width_px=300.0))
    entries, after = LOG.snapshot()
    said = [e.text for e in entries[-(after - before) :]] if after > before else []
    noted = [t for t in said if "made legible on the dark theme" in t]
    assert len(noted) == 1, said
    assert "text #" in noted[0] and "contrast" in noted[0]


def test_only_what_is_inside_the_clip_is_drawn(figures: Renderer, drawn: list[Drawn]) -> None:
    from imgui_bundle import imgui

    widget = host.FigureWidget(_marked(display=_nodes()), BODY)

    def body() -> None:
        imgui.push_clip_rect(imgui.ImVec2(0.0, 0.0), imgui.ImVec2(110.0, 1000.0), True)
        imgui.set_cursor_screen_pos(imgui.ImVec2(20.0, 20.0))
        widget.draw(width_px=200.0)
        imgui.dummy(imgui.ImVec2(1.0, 1.0))
        imgui.pop_clip_rect()

    frame(_IM[0], body)
    shown, built = widget.drawn
    assert 0 < shown < built, "B, at x 150-210, is off the clip's right edge"


def test_hover_lights_a_node_and_its_edges_and_a_click_reports_the_node(
    figures: Renderer, drawn: list[Drawn], monkeypatch: pytest.MonkeyPatch
) -> None:
    from imgui_bundle import imgui

    clicked: list[tuple[str, str]] = []
    monkeypatch.setattr(host, "node_clicked", lambda sha, node: clicked.append((sha, node)))
    mark = _marked(display=_nodes())
    widget = host.FigureWidget(mark, BODY)

    def body() -> None:
        imgui.set_cursor_screen_pos(imgui.ImVec2(40.0, 120.0))
        widget.draw(width_px=200.0)
        imgui.dummy(imgui.ImVec2(1.0, 1.0))

    io = imgui.get_io()
    frame(_IM[0], body)
    assert widget.hovered is None
    io.add_mouse_pos_event(40.0 + 160.0, 120.0 + 50.0)  # B's middle
    frame(_IM[0], body)
    frame(_IM[0], body)
    assert widget.hovered == "n-B"
    focus = theme.P.focus.u32
    lit = [kind for d in drawn[-1:] for kind, colour, _ in d.calls if colour == focus]
    assert lit.count("line") == 2, "B's outline and the edge into it"
    io.add_mouse_button_event(0, True)
    frame(_IM[0], body)
    io.add_mouse_button_event(0, False)
    frame(_IM[0], body)
    assert clicked == [(mark.sha, "n-B")]
    io.add_mouse_pos_event(-100.0, -100.0)
    frame(_IM[0], body)
    assert widget.hovered is None


def _stroked(width: float, dash: tuple[float, ...] = ()) -> displaylist.DisplayList:
    """
    One unfilled 90x80 rectangle at x 100-190 of a 200x100 diagram, stroked ``width``.
    """
    rect = displaylist.PathItem(
        displaylist.NODE,
        False,
        0,
        True,
        width,
        0,
        0,
        dash,
        "MLLLZ",
        (100.0, 10.0, 190.0, 10.0, 190.0, 90.0, 100.0, 90.0),
        (100.0, 10.0, 190.0, 90.0),
    )
    return displaylist.DisplayList(200.0, 100.0, (0.0, 0.0), (rect,), (), ())


@pytest.mark.parametrize("k_width", [200.0, 100.0])
def test_a_thick_stroke_whose_centre_line_is_past_the_clip_still_draws_its_near_half(
    figures: Renderer, drawn: list[Drawn], k_width: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A 20-px outline whose left edge's centre line is 8 px past the clip's right side
    reaches 2 px into it, at the figure's own scale and while it stands in, stretched
    to half, for a narrower one (where those 8 px are 16 of its own).
    """
    from imgui_bundle import imgui

    widget = host.FigureWidget(_marked("#7", display=_stroked(20.0)), BODY)

    def body(width: float) -> None:
        right = 20.0 + width / 2 - 8.0
        imgui.push_clip_rect(imgui.ImVec2(0.0, 0.0), imgui.ImVec2(right, 1000.0), True)
        imgui.set_cursor_screen_pos(imgui.ImVec2(20.0, 20.0))
        widget.draw(width_px=width)
        imgui.dummy(imgui.ImVec2(1.0, 1.0))
        imgui.pop_clip_rect()

    frame(_IM[0], lambda: body(200.0))
    if k_width != 200.0:
        monkeypatch.setattr(host, "_may_rebuild", lambda who, frame: False)
        frame(_IM[0], lambda: body(k_width))
        assert widget.rebuilds == 1, "it stands in at the old scale"
    assert widget.drawn[0] == 1, "the outline is culled though half of it is on screen"


def test_a_label_is_guarded_against_the_fill_of_the_node_it_sits_in(
    figures: Renderer, drawn: list[Drawn]
) -> None:
    """
    The geometry says what is behind each label: the theme's fill of its node's
    shape where the author gave none, the pane for a label in no node, and nothing
    of the theme's where the author's fill is (``ground_rgb`` carries that).
    """
    dl = _nodes()  # A is filled by the author, B by the theme
    label = next(i for i in dl.items if isinstance(i, displaylist.TextRun))
    in_b = replace(label, node=1, ink_rgb=0x777777, ground_rgb=displaylist.NO_COLOUR)
    loose = replace(label, node=-1, ink_rgb=0x777777, ground_rgb=displaylist.NO_COLOUR)
    display = replace(dl, items=(*dl.items, in_b, loose))
    widget = host.FigureWidget(_marked("#10", display=display), BODY)
    frame(_IM[0], lambda: widget.draw(width_px=200.0))
    geo = widget._geometry
    assert geo is not None
    inks = [p for p in geo.paints if p[0] == host._INK]
    assert inks == [
        (host._INK, displaylist.LABEL, label.ink_rgb, 0xFF9966, -1),
        (host._INK, displaylist.LABEL, 0x777777, displaylist.NO_COLOUR, displaylist.NODE),
        (host._INK, displaylist.LABEL, 0x777777, displaylist.NO_COLOUR, -1),
    ]


def test_a_dashed_stroke_is_one_op_and_never_cut_finer_than_the_period_on_screen(
    figures: Renderer, drawn: list[Drawn]
) -> None:
    widget = host.FigureWidget(_marked("#8", display=_stroked(1.0, (3.0, 3.0))), BODY)
    frame(_IM[0], lambda: widget.draw(width_px=20.0))
    assert widget.drawn[1] == 1, "the dashes of one stroke are one op"
    calls = sum(1 for kind, *_ in drawn[-1].calls if kind == "line")
    perimeter = 2 * (90.0 + 80.0) * 0.1
    assert calls <= perimeter / host.MIN_DASH_PERIOD_PX + 2


def test_dashes_past_the_figures_budget_are_drawn_solid(
    figures: Renderer, drawn: list[Drawn], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(host, "MAX_DASHES_DRAWN", 60)
    one = _stroked(1.0, (3.0, 3.0)).items[0]
    assert isinstance(one, displaylist.PathItem)
    two = replace(one, xy=tuple(v + 1.0 for v in one.xy))
    display = displaylist.DisplayList(200.0, 100.0, (0.0, 0.0), (one, two), (), ())
    widget = host.FigureWidget(_marked("#9", display=display), BODY)
    frame(_IM[0], lambda: widget.draw(width_px=200.0))
    geo = widget._geometry
    assert geo is not None
    assert [len(op[3]) for op in geo.ops] == [57, 1], "the first cut, the second solid"


def test_a_dash_pattern_is_stretched_to_the_least_period_with_its_proportions() -> None:
    assert host.on_screen_dash((), 0.1) == ()
    assert host.on_screen_dash((3.0, 3.0), 1.0) == (3.0, 3.0)
    assert host.on_screen_dash((3.0, 3.0), 0.1) == pytest.approx((2.0, 2.0))
    assert host.on_screen_dash((1.0, 3.0), 0.5) == pytest.approx((1.0, 3.0))
    odd = host.on_screen_dash((1.0,), 0.5)  # an odd list repeats: a period of 2 x 0.5
    assert odd == pytest.approx((2.0,))


def test_hover_rests_on_a_nodes_shapes_and_not_on_the_column_between_them(
    figures: Renderer, drawn: list[Drawn]
) -> None:
    """
    A sequence participant is two actor boxes, top and bottom, and its extent runs
    between them over every message and note in its column; the pointer in that
    column, off both boxes, is not on the participant.
    """
    from imgui_bundle import imgui

    top, bottom = (0.0, 0.0, 200.0, 20.0), (0.0, 180.0, 200.0, 200.0)
    actor = displaylist.PathItem(
        displaylist.NODE,
        True,
        0,
        True,
        1.0,
        0,
        0,
        (),
        "MLLLZ",
        (0.0, 0.0, 200.0, 0.0, 200.0, 20.0, 0.0, 20.0),
        top,
        0,
    )
    display = displaylist.DisplayList(
        200.0,
        200.0,
        (0.0, 0.0),
        (
            actor,
            replace(actor, xy=(0.0, 180.0, 200.0, 180.0, 200.0, 200.0, 0.0, 200.0), bbox=bottom),
        ),
        (displaylist.HitNode("A", (0.0, 0.0, 200.0, 200.0), (top, bottom)),),
        (),
    )
    widget = host.FigureWidget(_marked(display=display), BODY)

    def body() -> None:
        imgui.set_cursor_screen_pos(imgui.ImVec2(40.0, 120.0))
        widget.draw(width_px=200.0)
        imgui.dummy(imgui.ImVec2(1.0, 1.0))

    io = imgui.get_io()
    for (x, y), want in (((100.0, 100.0), None), ((100.0, 10.0), "A"), ((100.0, 190.0), "A")):
        io.add_mouse_pos_event(40.0 + x, 120.0 + y)
        frame(_IM[0], body)
        frame(_IM[0], body)
        assert widget.hovered == want, (x, y)
    io.add_mouse_pos_event(-100.0, -100.0)
    frame(_IM[0], body)


def test_a_width_change_rebuilds_one_figure_a_frame_and_the_others_draw_meanwhile(
    figures: Renderer, drawn: list[Drawn]
) -> None:
    widgets = [
        host.FigureWidget(_marked(fill, display=_nodes()), BODY) for fill in ("#1", "#2", "#3")
    ]

    def at(width: float) -> None:
        for w in widgets:
            w.draw(width_px=width)

    for _ in widgets:
        frame(_IM[0], lambda: at(200.0))
    assert [w.rebuilds for w in widgets] == [1, 1, 1]
    counts = []
    for width in (150.0, 150.0, 150.0, 150.0):
        frame(_IM[0], lambda width=width: at(width))  # type: ignore[misc]
        counts.append([w.rebuilds for w in widgets])
        assert all(w.drawn[0] > 0 for w in widgets), "a figure waiting its turn still draws"
    assert counts == [[2, 1, 1], [2, 2, 1], [2, 2, 2], [2, 2, 2]]


def test_figures_shown_at_once_are_built_one_a_frame_at_their_height_meanwhile(
    figures: Renderer, drawn: list[Drawn]
) -> None:
    """
    A view made again has every figure on it without geometry: they are built one a
    frame, as a resize rebuilds them, and each one waiting already stands at its
    real height, as an outline.
    """
    widgets = [
        host.FigureWidget(_marked(fill, display=_nodes()), BODY) for fill in ("#1", "#2", "#3")
    ]
    tall = widgets[0].height_for(200.0)
    assert tall != host.PLACEHOLDER_HEIGHT
    counts = []
    for _ in range(4):
        frame(_IM[0], lambda: [w.draw(width_px=200.0) for w in widgets])
        counts.append([w.rebuilds for w in widgets])
        assert all(w.height_for(200.0) == tall for w in widgets)
    assert counts == [[1, 0, 0], [1, 1, 0], [1, 1, 1], [1, 1, 1]]


def test_a_drag_does_not_starve_the_figures_further_down(
    figures: Renderer, drawn: list[Drawn]
) -> None:
    """
    The width changes every frame: the figure that has waited longest goes next, so
    the first one drawn does not take every turn.
    """
    widgets = [
        host.FigureWidget(_marked(fill, display=_nodes()), BODY) for fill in ("#1", "#2", "#3")
    ]
    for _ in widgets:
        frame(_IM[0], lambda: [w.draw(width_px=200.0) for w in widgets])
    for step in range(1, 7):
        frame(_IM[0], lambda step=step: [w.draw(width_px=200.0 - step) for w in widgets])  # type: ignore[misc]
    assert [w.rebuilds for w in widgets] == [3, 3, 3]


def test_a_figure_not_yet_laid_out_stands_at_the_placeholder_and_asks_once(
    figures: Renderer, drawn: list[Drawn]
) -> None:
    mark = host.mark("mermaid", "flowchart LR\n  x --> y")
    assert isinstance(mark, host.FigureMark)
    widget = host.FigureWidget(mark, "flowchart LR\n  x --> y")
    for _ in range(3):
        assert widget.height_for(300.0) == host.PLACEHOLDER_HEIGHT
        frame(_IM[0], lambda: widget.draw(width_px=300.0))
    assert figures.layouts == [(("layout", mark.sha), mark.source)]
    assert widget.drawn == (0, 0)


# -- helpers ----------------------------------------------------------------------------


def _marked(
    fill: str = "#c33", body: str = BODY, display: displaylist.DisplayList | None = None
) -> host.FigureMark:
    """
    The mark of a diagram whose display list is FIG's (or ``display``), held: each
    fill is a diagram of its own source, so of its own sha.
    """
    if fill != "#c33":
        body = f"{body}\n  style a fill:{fill}"
    marked = host.mark("mermaid", body)
    assert isinstance(marked, host.FigureMark)
    if display is None:
        display = displaylist.from_svg(FIG.replace("#c33", fill))
    host._STATE.layouts.put(marked.sha, display)
    return marked


def _unlaid(body: str = "flowchart LR\n  p --> q") -> host.FigureWidget:
    """
    A figure whose layout is not held, so it asks for one when it is first drawn.
    """
    marked = host.mark("mermaid", body)
    assert isinstance(marked, host.FigureMark)
    assert marked.display is None
    return host.FigureWidget(marked, body)


_IM: list[Any] = []


@pytest.fixture(autouse=True)
def _im(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    The seam, and merman as installed at one version with fresh marks and layouts,
    so a mark's sha does not depend on what this machine has.
    """
    monkeypatch.setattr(mermaid, "_VERSION", ["0.9.1"])
    monkeypatch.setattr(host, "_MARKS", type(host._MARKS)())
    monkeypatch.setattr(host._STATE, "layouts", host._Layouts(host.LAYOUT_BUDGET_WORDS))
    _IM[:] = [seam]
    yield
    _IM.clear()


def test_a_figure_is_set_in_the_files_the_library_sets_prose_and_code_in() -> None:
    """
    ``views.figure_fonts`` names the library's default files itself, so that only
    the seam reaches the library; this holds that answer to the library's own.
    """
    from imscribe import Face
    from imscribe.fontfiles import font_file

    fonts = views.figure_fonts()
    faces = (Face.PROSE, Face.PROSE_BOLD, Face.PROSE_ITALIC, Face.PROSE_BOLD_ITALIC, Face.CODE)
    assert fonts.files == tuple(dict.fromkeys(str(font_file(face)) for face in faces))
    assert (fonts.sans, fonts.mono) == (str(font_file(Face.PROSE)), str(font_file(Face.CODE)))
