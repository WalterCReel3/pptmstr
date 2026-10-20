"""
A click on a link in a markdown view reaches the app's link policy, and nothing
literal is ever clickable.

The default handler (``typeset.follow_links``) is the whole of the wiring: no
pane passes ``on_link``, so a view that does not take the default is a pane whose
links do nothing. Each test installs a recorder in its place, and none spawns
anything.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from pptmstr.ui import typeset
from pptmstr.ui.typeset import Kind, Part, views
from tests.test_typeset_views import frame as _drawn_in

ADDRESS = "https://example.com/a"
LINKED = f"Read [the page]({ADDRESS}) first."


class Recorder(list[str]):
    """
    A link handler that keeps what it was called with; one object, so a view's
    ``on_link`` can be checked to be it.
    """

    def __call__(self, destination: str) -> None:
        self.append(destination)


@pytest.fixture
def followed(monkeypatch: pytest.MonkeyPatch) -> Iterator[Recorder]:
    """
    The process's link handler, for the test: the destinations it was called with.
    """
    seen = Recorder()
    monkeypatch.setattr(views, "_FOLLOW", None)
    typeset.follow_links(seen)
    yield seen


def frame(im: Any, body: Callable[[], object]) -> None:
    """
    ``tests.test_typeset_views.frame``, for a body whose answer is not wanted.
    """

    def drawn() -> None:
        body()

    _drawn_in(im, drawn)


def _only_view(cache: typeset.ViewCache, key: str) -> Any:
    made = cache.views(key)
    assert len(made) == 1
    return made[0]


def test_app_start_up_installs_links_follow_before_the_loop() -> None:
    """
    Read from ``app.main``'s source, as the other start-up orderings are
    (``tests/test_app.py``): reaching ``immapp.run`` needs a window. The tests
    there that run ``main`` stop at its first window call, before this line, so
    no later test inherits the real opener.
    """
    from pptmstr import app

    main = inspect.getsource(app.main)
    assert main.count("typeset.follow_links(") == 1
    assert "typeset.follow_links(links.follow)" in main
    assert main.index("hello_imgui.RunnerParams()") < main.index("typeset.follow_links(")
    assert main.index("typeset.follow_links(") < main.index("immapp.run(runner)")
    assert inspect.getsource(app).count("follow_links(") == 1


@pytest.mark.parametrize(
    "draw",
    [
        lambda cache: typeset.markdown(cache, "k", LINKED),
        lambda cache: typeset.document(cache, "k", (Part(LINKED),)),
        lambda cache: typeset.document(cache, "k", (Part(LINKED),), height_cap=40.0),
    ],
    ids=["markdown", "document", "capped"],
)
def test_a_settled_markdown_view_with_no_on_link_takes_the_default(
    seam: Any, followed: Recorder, draw: Callable[[typeset.ViewCache], float]
) -> None:
    cache = typeset.ViewCache()
    frame(seam, lambda: draw(cache))
    assert _only_view(cache, "k").on_link is followed


def test_a_settled_view_has_the_handler_when_its_text_is_described(
    seam: Any, followed: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The library styles links in the link ink only in a view with a handler, and
    decides when the text is described: a handler that arrives at the draw is
    clickable and drawn as plain text.
    """
    import imscribe

    described: list[object] = []

    class Recording(imscribe.TypesetView):  # type: ignore[misc]
        def set_document(self, document: object, **given: Any) -> bool:
            described.append(self.on_link)
            return bool(super().set_document(document, **given))

    monkeypatch.setattr(imscribe, "TypesetView", Recording)
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", LINKED))
    assert described == [followed]


def test_a_handler_installed_after_a_view_was_made_reaches_it_on_the_next_draw(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "_FOLLOW", None)
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", LINKED))
    assert _only_view(cache, "k").on_link is None
    seen = Recorder()
    typeset.follow_links(seen)
    frame(seam, lambda: typeset.markdown(cache, "k", LINKED))
    assert _only_view(cache, "k").on_link is seen


def test_an_explicit_on_link_wins_over_the_default(seam: Any, followed: Recorder) -> None:
    mine = Recorder()
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", LINKED, on_link=mine))
    assert _only_view(cache, "k").on_link is mine


def test_a_stream_view_has_the_default_before_its_first_text_is_described(
    seam: Any, followed: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    import imscribe

    synced: list[object] = []

    class Recording(imscribe.TypesetStreamView):  # type: ignore[misc]
        def sync(self) -> Any:
            was = self.revision
            answer = super().sync()
            if self.revision != was:
                synced.append(self.on_link)
            return answer

    monkeypatch.setattr(imscribe, "TypesetStreamView", Recording)
    cache = typeset.ViewCache()
    stream = typeset.stream(cache, "s")
    stream.append(LINKED)
    frame(seam, stream.draw)
    # Every sync that described text did so with the handler in place; the one
    # inside the view's constructor, before any text, describes nothing.
    assert synced and all(handler is followed for handler in synced)
    assert stream.view.on_link is followed


def test_a_literal_view_has_no_handler_whatever_its_caller_passed(
    columns_hold: Any, followed: Recorder
) -> None:
    mine = Recorder()
    part = Part(ADDRESS, Kind.LITERAL)
    assert views.literal_unfit(part) is None
    cache = typeset.ViewCache()
    for _ in range(2):
        frame(
            columns_hold,
            lambda: typeset.document(cache, "k", (part,), literal=True, on_link=mine),
        )
        assert _only_view(cache, "k").on_link is None


def test_a_view_of_rows_has_no_handler(columns_hold: Any, followed: Recorder) -> None:
    cache = typeset.ViewCache()
    drew: list[bool] = []
    frame(columns_hold, lambda: drew.append(views.rows(cache, "r", ((ADDRESS, ()),))))
    assert drew == [True]
    assert _only_view(cache, "r").on_link is None


def _click_the_link(im: Any, draw: Callable[[], object], view: Callable[[], Any]) -> None:
    """
    Move onto the first link ``view()`` holds, press and release on it, a frame each.
    """
    held = view()
    row = held.links[0]
    left, top, right, bottom = held.span_rects(row.block, row.start, row.end)[0]
    ox, oy = held.origin
    io = im.get_io()
    io.add_mouse_pos_event(ox + (left + right) / 2.0, oy + (top + bottom) / 2.0)
    frame(im, draw)
    io.add_mouse_button_event(0, True)
    frame(im, draw)
    io.add_mouse_button_event(0, False)
    frame(im, draw)
    io.add_mouse_pos_event(-1.0, -1.0)
    frame(im, draw)


def test_a_click_on_a_link_in_a_settled_document_calls_the_handler_and_selects_nothing(
    seam: Any, followed: Recorder
) -> None:
    cache = typeset.ViewCache()

    def draw() -> None:
        typeset.document(cache, "k", (Part(LINKED),))

    frame(seam, draw)
    _click_the_link(seam, draw, lambda: _only_view(cache, "k"))
    assert followed == [ADDRESS]
    assert _only_view(cache, "k").selection is None


def test_a_click_on_a_link_in_a_stream_calls_the_handler(seam: Any, followed: Recorder) -> None:
    cache = typeset.ViewCache()
    stream = typeset.stream(cache, "s")
    stream.append(LINKED + "\n\n")

    def draw() -> None:
        stream.draw()

    frame(seam, draw)
    _click_the_link(seam, draw, lambda: stream.view)
    assert followed == [ADDRESS]


def test_with_no_handler_installed_a_link_is_inert(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "_FOLLOW", None)
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", LINKED))
    assert _only_view(cache, "k").on_link is None
