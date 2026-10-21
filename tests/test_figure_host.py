"""
A committed ```svg fence drawn as a figure: when it is hosted, by what, at what
height, where it draws, and when its textures are let go of.

Headless: an ImGui context with no backend, so no texture can be made. The render
process is ``tests/test_figure_render.py``'s; here a recording stand-in answers for
it, and textures are objects whose release is observed. Nothing here draws in a
real window.
"""

from __future__ import annotations

import gc
import weakref
from collections.abc import Callable, Hashable, Iterator
from typing import Any

import pytest

from pptmstr import theme
from pptmstr.figures import host, render
from pptmstr.figures.sanitise import Sanitised
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Kind, Part, views

WINDOW = (700.0, 600.0)
FIG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
    '<rect width="200" height="100" fill="#c33"/></svg>'
)
OPEN = f"Intro paragraph.\n\n```svg\n{FIG}\n"


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
    yield fake
    host.release_all()


def frame(im: Any, body: Callable[[], object]) -> None:
    typeset.pre_frame()
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
def test_an_svg_fence_is_hosted_on_commit_while_the_stream_is_open(
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
        closed_line = FIG in fed and "```\n" in fed.split(FIG, 1)[1]
        hosted = figures_hosted(handle.view)
        if not closed_line:
            assert hosted == [], f"hosted while open, after {fed[-12:]!r}"
    assert not handle.closed
    (widget,) = figures_hosted(handle.view)
    assert widget.mark.sanitised.width == 200.0
    assert widget.text == f"```svg\n{FIG}\n```"


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
    assert any(text.startswith("svg\n<svg") for text in cache.texts("reply"))
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


def test_a_settled_document_hosts_its_svg_fence(figures: Renderer) -> None:
    cache = typeset.ViewCache()
    frame(_IM[0], lambda: typeset.markdown(cache, "doc", OPEN + "```\n\nAfter.\n"))
    (widget,) = figures_hosted(cache.view("doc"))
    assert widget.height_for(600.0) == 100.0
    assert widget.height_for(100.0) == 50.0


def test_a_refused_svg_stays_code_under_its_reason(figures: Renderer) -> None:
    refused = '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>'
    source = f"Before.\n\n```svg\n{refused}\n```\n\nAfter.\n"
    cache = typeset.ViewCache()
    frame(_IM[0], lambda: typeset.markdown(cache, "doc", source))
    frame(_IM[0], lambda: typeset.markdown(cache, "doc", source))
    view = cache.view("doc")
    assert not view.hosts
    texts = cache.texts("doc")
    (at,) = [n for n, text in enumerate(texts) if text.startswith("svg not drawn: ")]
    assert "DOCTYPE" in texts[at] or "declaration" in texts[at]
    assert texts[at + 1] == f"svg\n{refused}"
    warn = theme.P.warn.u32
    assert (0, len(texts[at]), warn) in view._colours.get(at, ())


def test_a_refused_svg_in_a_stream_is_code_with_its_reason_once_committed(
    figures: Renderer,
) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append("```svg\n<svg><script>x</script>\n```\n\nAfter.\n")
    for _ in range(2):
        frame(_IM[0], lambda: handle.draw())
    assert not handle.view.hosts
    assert any(text.startswith("svg not drawn: ") for text in cache.texts("reply"))


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
    figure = replace(block, text=FIG, language=host.mark("svg", FIG))

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
    assert widget.height_for(500.0) == 100.0
    frame(_IM[0], lambda: handle.draw())
    assert len(figures.asked) == 1, "the raster held for the content is drawn again"


def test_the_figure_is_drawn_at_a_whole_pixel_origin_inside_its_own_clip(
    figures: Renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from imgui_bundle import imgui

    widget = host.FigureWidget(_marked(), FIG)
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
    assert (x, y) == (40.0, 121.0)
    assert (cx0, cy0) == (40.0, 121.0)
    assert (cx1, cy1) == (190.0, 121.0 + 75.0)


def test_a_new_width_is_asked_for_only_once_it_has_held(figures: Renderer) -> None:
    widget = host.FigureWidget(_marked(), FIG)
    frame(_IM[0], lambda: widget.draw(width_px=150.0))
    assert [a[2] for a in figures.asked] == [150.0]
    figures.answer_last()
    frame(_IM[0], lambda: widget.draw(width_px=150.0))
    for width in (120.0, 121.0, 122.0):
        frame(_IM[0], lambda w=width: widget.draw(width_px=w))  # type: ignore[misc,arg-type]
    assert len(figures.asked) == 1, "a width still moving is drawn from the raster held"
    for _ in range(host.STABLE_FRAMES + 1):
        frame(_IM[0], lambda: widget.draw(width_px=122.0))
    assert [a[2] for a in figures.asked] == [150.0, 122.0]


def test_a_render_that_fails_keeps_the_fence_source_on_screen_under_its_reason(
    figures: Renderer,
) -> None:
    widget = host.FigureWidget(_marked(), FIG)
    frame(_IM[0], lambda: widget.draw(width_px=300.0))
    key = figures.asked[-1][0]
    figures.answers.append(render.Failed(key, "render took longer than 10 s and was stopped"))
    heights: list[float] = []

    def body() -> None:
        widget.draw(width_px=300.0)
        heights.append(widget.height_for(300.0))

    frame(_IM[0], body)
    frame(_IM[0], body)
    assert heights[-1] > 100.0, "the source is taller than the figure would have been"
    assert widget.text.startswith("```svg\n<svg")


# -- textures are let go of between frames ---------------------------------------------


def test_an_evicted_texture_is_parked_and_let_go_of_only_in_a_later_frame() -> None:
    textures = host._Textures(budget=100 * 100 * 4)
    first = Texture(1)
    alive = weakref.ref(first)
    textures.put(("a", 1.0, 100), host._Raster(first, 100, 100), frame=7)
    del first
    textures.put(("b", 1.0, 100), host._Raster(Texture(2), 100, 100), frame=7)
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
    one, two = host.FigureWidget(_marked(), FIG), host.FigureWidget(_marked("#36c"), FIG)
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


def _marked(fill: str = "#c33") -> host.FigureMark:
    svg = FIG.replace("#c33", fill)
    marked = host.mark("svg", svg)
    assert isinstance(marked, host.FigureMark)
    return marked


_IM: list[Any] = []


@pytest.fixture(autouse=True)
def _im(seam: Any) -> Iterator[None]:
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
