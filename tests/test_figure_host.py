"""
A committed ```mermaid fence drawn as a figure: when it is hosted, by what, at what
height, where it draws, and when its textures are let go of; and a ```svg fence,
which is code.

Headless: an ImGui context with no backend, so no texture can be made. The render
process is ``tests/test_figure_render.py``'s; here a recording stand-in answers for
it, and textures are objects whose release is observed. Each diagram's layout is
put in the held layouts before it is drawn, as the render child's answer would put
it (``tests/test_figure_mermaid.py`` covers asking for it), so a figure here is
drawn from its first frame. Nothing here draws in a real window.
"""

from __future__ import annotations

import gc
import weakref
from collections.abc import Callable, Hashable, Iterator
from typing import Any

import pytest

from pptmstr import theme
from pptmstr.figures import host, mermaid, render
from pptmstr.figures.sanitise import Sanitised, sanitise
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
PAD = host.FIGURE_PAD


class Texture:
    """
    What ``host._upload`` makes, as far as a test can see it: an id, and whether
    anything still holds it.
    """

    def __init__(self, ident: int) -> None:
        self.ident = ident

    def texture_id(self) -> int:
        return self.ident


class Renderer:
    """
    The render process as the host sees it: what it was asked, and what it answers
    at the next poll.
    """

    def __init__(self) -> None:
        self.asked: list[tuple[Hashable, Sanitised, float, float]] = []
        self.answers: list[render.Result] = []

    def submit(self, key: Hashable, sanitised: Sanitised, px_width: float, scale: float) -> None:
        self.asked.append((key, sanitised, px_width, scale))

    def poll(self) -> list[render.Result]:
        out, self.answers = self.answers, []
        return out

    def answer_last(self) -> None:
        key, sanitised, px_width, scale = self.asked[-1]
        width, height = render.raster_size(sanitised, px_width, scale)
        self.answers.append(
            render.Rendered(key, width, height, bytes(width * height * 4), px_width, scale)
        )


@pytest.fixture
def figures(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Renderer]:
    fake = Renderer()
    made: list[int] = []

    def upload(rgba: bytes, width: int, height: int) -> Texture:
        made.append(width)
        return Texture(len(made))

    host.release_all()
    monkeypatch.setattr(host._STATE, "renderer", lambda: fake)
    monkeypatch.setattr(host._STATE, "upload", upload)
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
    assert widget.mark.sanitised is not None and widget.mark.sanitised.width == 200.0
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
    assert figures.asked == []


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
    assert figures.asked == []


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
    assert widget.height_for(600.0) == 100.0 + 2 * PAD
    assert widget.height_for(100.0) == (100.0 - 2 * PAD) / 2 + 2 * PAD


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


def test_a_figure_rehosted_after_forget_answers_its_height_at_once_and_reuses_its_texture(
    figures: Renderer,
) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    text = OPEN + "```\n\nAfter.\n"
    handle.append(text)
    frame(_IM[0], lambda: handle.draw())
    assert len(figures.asked) == 1
    figures.answer_last()
    frame(_IM[0], lambda: handle.draw())
    first = handle.view
    # Text that does not extend what is held: the stream lets go of its view.
    handle.set_text("Another start.\n\n" + text)
    frame(_IM[0], lambda: handle.draw())
    assert handle.view is not first
    (widget,) = figures_hosted(handle.view)
    assert widget.height_for(500.0) == 100.0 + 2 * PAD
    frame(_IM[0], lambda: handle.draw())
    assert len(figures.asked) == 1, "the raster held for the content is drawn again"


@pytest.mark.parametrize(
    "density, origin",
    [(1.0, (46.0, 127.0)), (1.5, (70 / 1.5, 191 / 1.5)), (2.0, (46.5, 127.0))],
)
def test_the_figure_is_drawn_at_the_nearest_device_pixel_inside_its_blocks_clip(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch, density: float, origin: tuple[float, float]
) -> None:
    """
    The image's origin, inside the card's padding, is the nearest whole pixel of
    the framebuffer, and the clip is the block's own rectangle: nothing is drawn
    above or left of where imscribe put it.
    """
    from imgui_bundle import imgui

    monkeypatch.setattr(host, "_density", lambda im: density)
    widget = host.FigureWidget(_marked(), BODY)
    seen: list[tuple[float, float, float, float, float, float]] = []

    def image(ref: Any, size: Any, *rest: Any) -> None:
        at = imgui.get_cursor_screen_pos()
        dl = imgui.get_window_draw_list()
        low, high = dl.get_clip_rect_min(), dl.get_clip_rect_max()
        seen.append((at.x, at.y, low.x, low.y, high.x, high.y))
        imgui.dummy(size)

    monkeypatch.setattr(imgui, "image", image)

    def body() -> None:
        imgui.set_cursor_screen_pos(imgui.ImVec2(40.4, 121.237))
        widget.draw(width_px=150.0)
        # The view this stands in for claims the block's room after its hosts.
        imgui.dummy(imgui.ImVec2(1.0, 1.0))

    frame(_IM[0], body)
    figures.answer_last()
    frame(_IM[0], body)
    ((x, y, cx0, cy0, cx1, cy1),) = seen
    assert (x, y) == pytest.approx(origin, abs=1e-4)
    assert (x * density, y * density) == pytest.approx(
        (round(x * density), round(y * density)), abs=1e-3
    )
    # ImGui keeps clip rectangles in float: the block's corner, unsnapped.
    assert (cx0, cy0) == pytest.approx((40.4, 121.237), abs=1e-4)
    image_h = (150.0 - 2 * PAD) / 2
    assert (cx1, cy1) == pytest.approx((40.4 + 150.0, 121.237 + image_h + 2 * PAD), abs=1e-4)


class Recording:
    """
    The window's draw list, recording what is drawn on it with the clip in force.
    """

    def __init__(self, real: Any, drawn: list[tuple[Any, ...]]) -> None:
        self._real = real
        self._drawn = drawn

    def _clip(self) -> tuple[float, float, float, float]:
        low, high = self._real.get_clip_rect_min(), self._real.get_clip_rect_max()
        return (low.x, low.y, high.x, high.y)

    def add_rect_filled(self, low: Any, high: Any, colour: int, *rest: Any) -> None:
        self._drawn.append(("card", (low.x, low.y, high.x, high.y), colour, self._clip()))
        self._real.add_rect_filled(low, high, colour, *rest)

    def add_rect(self, low: Any, high: Any, colour: int, *rest: Any) -> None:
        self._drawn.append(("outline", (low.x, low.y, high.x, high.y), colour, self._clip()))
        self._real.add_rect(low, high, colour, *rest)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@pytest.mark.parametrize("theme_name", ["dark", "light"])
def test_the_card_is_drawn_first_inside_the_clip_in_every_theme(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch, theme_name: str
) -> None:
    """
    Before the outline that stands for a figure with no pixels yet, and before the
    image once it has them: the card, the same light colour in every theme, the
    image's size and its padding, inside the block's clip.
    """
    from imgui_bundle import imgui

    theme.set_theme(theme_name)
    drawn: list[tuple[Any, ...]] = []
    real_list = imgui.get_window_draw_list

    def image(ref: Any, size: Any, *rest: Any) -> None:
        dl = real_list()
        low, high = dl.get_clip_rect_min(), dl.get_clip_rect_max()
        at = imgui.get_cursor_screen_pos()
        drawn.append(
            (
                "image",
                (at.x, at.y, at.x + size.x, at.y + size.y),
                None,
                (low.x, low.y, high.x, high.y),
            )
        )
        imgui.dummy(size)

    monkeypatch.setattr(imgui, "get_window_draw_list", lambda: Recording(real_list(), drawn))
    monkeypatch.setattr(imgui, "image", image)
    widget = host.FigureWidget(_marked(), BODY)

    def body() -> None:
        imgui.set_cursor_screen_pos(imgui.ImVec2(40.0, 120.0))
        widget.draw(width_px=150.0)
        imgui.dummy(imgui.ImVec2(1.0, 1.0))

    frame(_IM[0], body)
    figures.answer_last()
    frame(_IM[0], body)
    kinds = [item[0] for item in drawn]
    assert kinds == ["card", "outline", "card", "image"]
    w, h = 150.0 - 2 * PAD, (150.0 - 2 * PAD) / 2
    for kind, rect, colour, clip in drawn:
        x0, y0, x1, y1 = rect
        assert clip == pytest.approx((40.0, 120.0, 190.0, 120.0 + h + 2 * PAD))
        assert clip[0] <= x0 and clip[1] <= y0 and x1 <= clip[2] and y1 <= clip[3], kind
        if kind == "card":
            assert colour == theme.col(host.BACKDROP).u32
            assert rect == pytest.approx((40.0, 120.0, 40.0 + w + 2 * PAD, 120.0 + h + 2 * PAD))
        else:
            assert rect == pytest.approx((40.0 + PAD, 120.0 + PAD, 40.0 + PAD + w, 120.0 + PAD + h))


def test_the_card_s_padding_is_in_the_height_and_the_backdrop_is_near_white() -> None:
    assert host.FIGURE_PAD > 0
    widget = host.FigureWidget(_marked(), BODY)
    assert widget.height_for(600.0) == 100.0 + 2 * host.FIGURE_PAD
    channels = [(host.BACKDROP >> shift) & 0xFF for shift in (16, 8, 0)]
    assert min(channels) >= 0xF0 and max(channels) - min(channels) <= 8


def test_a_new_width_is_asked_for_only_once_it_has_held(figures: Renderer) -> None:
    widget = host.FigureWidget(_marked(), BODY)
    frame(_IM[0], lambda: widget.draw(width_px=150.0))
    assert [a[2] for a in figures.asked] == [150.0 - 2 * PAD]
    figures.answer_last()
    frame(_IM[0], lambda: widget.draw(width_px=150.0))
    for width in (120.0, 121.0, 122.0):
        frame(_IM[0], lambda w=width: widget.draw(width_px=w))  # type: ignore[misc,arg-type]
    assert len(figures.asked) == 1, "a width still moving is drawn from the raster held"
    for _ in range(host.STABLE_FRAMES + 1):
        frame(_IM[0], lambda: widget.draw(width_px=122.0))
    assert [a[2] for a in figures.asked] == [150.0 - 2 * PAD, 122.0 - 2 * PAD]


def test_a_render_that_fails_keeps_the_fence_source_on_screen_under_its_reason(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    In imscribe's order, ``height_for`` and then ``draw``: from the frame the failure
    arrives in, the height asked is the failed form's, and what is drawn is clipped
    to that rectangle, so nothing lands on the prose below.
    """
    from imgui_bundle import imgui

    widget = host.FigureWidget(_marked(body=TALL), TALL)
    frame(_IM[0], lambda: widget.draw(width_px=300.0))
    figure_height = widget.height_for(300.0)
    key = figures.asked[-1][0]
    figures.answers.append(render.Failed(key, "resvg refused the document"))
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

    widget = host.FigureWidget(_marked(), BODY)
    frame(_IM[0], lambda: widget.draw(width_px=300.0))
    figures.answers.append(render.Failed(figures.asked[-1][0], "refused"))
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
    widget = host.FigureWidget(_marked(), BODY)

    def draw() -> None:
        widget.draw(width_px=150.0)

    frame(_IM[0], draw)
    key = figures.asked[-1][0]
    figures.answers.append(render.Failed(key, "render process died", transient=True))
    for _ in range(3):
        frame(_IM[0], draw)
    assert len(figures.asked) == 1, "not asked again inside the back-off"
    assert widget.height_for(150.0) == (150.0 - 2 * PAD) / 2 + 2 * PAD, "the outline's room"
    clock.now += host.RETRY_AFTER_S
    frame(_IM[0], draw)
    frame(_IM[0], draw)
    assert len(figures.asked) == 2, "asked once more after it"
    figures.answer_last()
    frame(_IM[0], draw)
    assert widget._failure() is None
    assert len(host._STATE.textures) == 1


def test_a_second_transient_failure_is_kept(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    monkeypatch.setattr(host._STATE, "clock", clock)
    widget = host.FigureWidget(_marked(), BODY)

    def draw() -> None:
        widget.draw(width_px=150.0)

    frame(_IM[0], draw)
    key = figures.asked[-1][0]
    figures.answers.append(render.Failed(key, "render took longer than 10 s", transient=True))
    frame(_IM[0], draw)
    clock.now += host.RETRY_AFTER_S
    frame(_IM[0], draw)
    assert len(figures.asked) == 2
    figures.answers.append(render.Failed(key, "render took longer than 10 s", transient=True))
    frame(_IM[0], draw)
    assert widget._failure() == "render took longer than 10 s"
    clock.now += 10 * host.RETRY_AFTER_S
    for _ in range(3):
        frame(_IM[0], draw)
    assert len(figures.asked) == 2, "never asked again"


def test_a_failure_that_is_not_transient_is_kept_at_once(figures: Renderer) -> None:
    widget = host.FigureWidget(_marked(), BODY)
    frame(_IM[0], lambda: widget.draw(width_px=150.0))
    figures.answers.append(render.Failed(figures.asked[-1][0], "over the cap"))
    for _ in range(3):
        frame(_IM[0], lambda: widget.draw(width_px=150.0))
    assert widget._failure() == "over the cap"
    assert len(figures.asked) == 1


def test_an_upload_that_raises_fails_its_own_figure_and_not_the_others(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    one, two = host.FigureWidget(_marked(), BODY), host.FigureWidget(_marked("#36c"), BODY)

    def both() -> None:
        one.draw(width_px=150.0)
        two.draw(width_px=150.0)

    frame(_IM[0], both)
    assert len(figures.asked) == 2
    figures.answer_last()
    key, sanitised, px_width, scale = figures.asked[0]
    width, height = render.raster_size(sanitised, px_width, scale)
    figures.answers.insert(
        0, render.Rendered(key, width, height, bytes(width * height * 4), px_width, scale)
    )
    uploaded: list[int] = []

    def upload(rgba: bytes, w: int, h: int) -> Texture:
        if not uploaded:
            uploaded.append(0)
            raise RuntimeError("GL_INVALID_VALUE")
        uploaded.append(w)
        return Texture(len(uploaded))

    monkeypatch.setattr(host._STATE, "upload", upload)
    frame(_IM[0], both)
    assert one._failure() == "texture upload failed: RuntimeError: GL_INVALID_VALUE"
    assert two._failure() is None
    assert len(host._STATE.textures) == 1
    assert host._STATE.asked == {}, "neither is left waiting for an answer"


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


# -- textures are let go of between frames ---------------------------------------------


def test_an_evicted_texture_is_parked_and_let_go_of_only_in_a_later_frame() -> None:
    textures = host._Textures(budget=100 * 100 * 4)
    first = Texture(1)
    alive = weakref.ref(first)
    textures.put(("a", 1.0, 100), host._Raster(first, 100, 100), at=7)
    del first
    textures.put(("b", 1.0, 100), host._Raster(Texture(2), 100, 100), at=7)
    gc.collect()
    assert alive() is not None, "evicted in frame 7 and still held in it"
    assert textures.release(7) == 0
    gc.collect()
    assert alive() is not None
    assert textures.release(8) == 1
    gc.collect()
    assert alive() is None


def test_an_upload_that_evicts_inside_a_frame_frees_nothing_until_the_next(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = host._Textures(budget=150 * 75 * 4)
    monkeypatch.setattr(host._STATE, "textures", held)
    one, two = host.FigureWidget(_marked(), BODY), host.FigureWidget(_marked("#36c"), BODY)
    frame(_IM[0], lambda: one.draw(width_px=150.0))
    figures.answer_last()
    frame(_IM[0], lambda: one.draw(width_px=150.0))
    (first,) = [weakref.ref(r.texture) for r in held._held.values()]
    frame(_IM[0], lambda: two.draw(width_px=150.0))
    figures.answer_last()

    def evicting_frame() -> None:
        two.draw(width_px=150.0)  # its upload evicts the first figure's raster
        gc.collect()
        assert first() is not None, "freed inside the frame that may have drawn it"

    frame(_IM[0], evicting_frame)
    gc.collect()
    assert first() is not None
    frame(_IM[0], lambda: two.draw(width_px=150.0))
    gc.collect()
    assert first() is None


# -- helpers ----------------------------------------------------------------------------


def _marked(fill: str = "#c33", body: str = BODY) -> host.FigureMark:
    """
    The mark of a diagram whose layout is FIG filled with ``fill``, its layout held:
    each fill is a diagram of its own source, so of its own sha.
    """
    if fill != "#c33":
        body = f"{body}\n  style a fill:{fill}"
    marked = host.mark("mermaid", body)
    assert isinstance(marked, host.FigureMark)
    laid = sanitise(FIG.replace("#c33", fill))
    assert isinstance(laid, Sanitised)
    host._STATE.layouts.put(marked.sha, laid)
    return marked


_IM: list[Any] = []


@pytest.fixture(autouse=True)
def _im(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    The seam, and merman as installed at one version with fresh marks and layouts,
    so a mark's sha does not depend on what this machine has.
    """
    monkeypatch.setattr(mermaid, "_VERSION", ["0.9.1"])
    monkeypatch.setattr(host, "_MARKS", type(host._MARKS)())
    monkeypatch.setattr(host._STATE, "layouts", host._Layouts(host.LAYOUT_BUDGET_CHARS))
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
