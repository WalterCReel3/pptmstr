"""
A click on a link in a markdown view reaches the app's link policy, and nothing
literal is ever clickable.

The default handler (``typeset.follow_links``) is the whole of the wiring: no
pane passes ``on_link``, so a view that does not take the default is a pane whose
links do nothing. Each test installs a recorder in its place, and none spawns
anything; ``tests/conftest.py`` takes whatever was installed away before each test.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from pptmstr import links
from pptmstr.log import LOG
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Kind, Part, gate, themes, views
from tests.test_typeset_views import _inks
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


def _takes(view: Any, handler: Callable[[str], object]) -> bool:
    """
    Whether ``view`` follows a link through the installed ``handler``.
    """
    return isinstance(view.on_link, views._Followed) and view.on_link.handler is handler


def test_app_start_up_installs_links_follow_before_the_loop() -> None:
    """
    Read from ``app.main``'s source, as the other start-up orderings are
    (``tests/test_app.py``): reaching ``immapp.run`` needs a window.
    """
    from pptmstr import app

    main = inspect.getsource(app.main)
    assert main.count("typeset.follow_links(") == 1
    assert "typeset.follow_links(links.follow, refusal=links.followable)" in main
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
    assert _takes(_only_view(cache, "k"), followed)


def _settled(cache: typeset.ViewCache) -> Callable[[], object]:
    return lambda: typeset.markdown(cache, "k", LINKED)


def _streamed(cache: typeset.ViewCache) -> Callable[[], object]:
    handle = typeset.stream(cache, "s")
    if not handle.text:
        handle.append(LINKED)
    return handle.draw


@pytest.mark.parametrize("made", [_settled, _streamed], ids=["settled", "stream"])
def test_a_link_in_a_view_with_the_default_is_drawn_in_the_link_ink(
    seam: Any, followed: Recorder, made: Callable[[typeset.ViewCache], Callable[[], object]]
) -> None:
    """
    The library takes the link ink when its view makes its converter, in the view's
    constructor: a view handed its handler after that is clickable and set plain.
    """
    cache = typeset.ViewCache()
    assert themes.palette().link in _inks(seam, made(cache))


@pytest.mark.parametrize("made", [_settled, _streamed], ids=["settled", "stream"])
def test_a_handler_installed_after_a_view_was_drawn_inks_and_follows_its_links(
    seam: Any, made: Callable[[typeset.ViewCache], Callable[[], object]]
) -> None:
    """
    The app installs before any view exists. One installed later moves the epoch,
    so each view is made again at its next draw, with the handler from the start.
    """
    cache = typeset.ViewCache()
    assert themes.palette().link not in _inks(seam, made(cache))
    seen = Recorder()
    typeset.follow_links(seen)
    assert themes.palette().link in _inks(seam, made(cache))
    (view,) = cache.views("k") or cache.views("s")
    assert _takes(view, seen)


def test_an_explicit_on_link_wins_over_the_default(seam: Any, followed: Recorder) -> None:
    mine = Recorder()
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", LINKED, on_link=mine))
    assert _only_view(cache, "k").on_link is mine


def test_a_stream_view_takes_the_default(seam: Any, followed: Recorder) -> None:
    cache = typeset.ViewCache()
    stream = typeset.stream(cache, "s")
    stream.append(LINKED)
    frame(seam, stream.draw)
    assert _takes(stream.view, followed)


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


def test_with_no_handler_installed_a_link_is_inert(seam: Any) -> None:
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", LINKED))
    assert _only_view(cache, "k").on_link is None


def test_a_raising_handler_is_logged_and_leaves_typeset_text_on(seam: Any) -> None:
    """
    The library calls the handler inside its draw, which pptmstr makes inside
    ``_DRAWING``: what the handler raises is pptmstr's, not the library stopping.
    """
    called: list[str] = []

    def raising(destination: str) -> None:
        called.append(destination)
        raise RuntimeError("the opener broke\nover two lines")

    typeset.follow_links(raising)
    cache = typeset.ViewCache()

    def draw() -> None:
        typeset.document(cache, "k", (Part(LINKED),))

    frame(seam, draw)
    mark = len(LOG.snapshot()[0])
    _click_the_link(seam, draw, lambda: _only_view(cache, "k"))
    assert called == [ADDRESS]
    assert gate._GATE.stopping is None and typeset.can_draw()
    said = [(e.level.name, e.source, e.text) for e in LOG.snapshot()[0][mark:]]
    raised = "RuntimeError: the opener broke over two lines"
    assert said == [("ERROR", "link", f"the link handler raised on {ADDRESS!r}: {raised}")]


def _hover_the_link(im: Any, draw: Callable[[], object], view: Callable[[], Any]) -> None:
    """
    Move onto the first link ``view()`` holds and draw a frame there.
    """
    held = view()
    row = held.links[0]
    left, top, right, bottom = held.span_rects(row.block, row.start, row.end)[0]
    ox, oy = held.origin
    im.get_io().add_mouse_pos_event(ox + (left + right) / 2.0, oy + (top + bottom) / 2.0)
    frame(im, draw)


@pytest.fixture
def tooltips(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """
    What ImGui was asked to show as a tooltip, while the test runs.
    """
    shown: list[str] = []
    monkeypatch.setattr(seam, "set_tooltip", shown.append)
    yield shown
    seam.get_io().add_mouse_pos_event(-1.0, -1.0)
    frame(seam, lambda: None)


@pytest.mark.parametrize(
    ("destination", "said"),
    [
        ("pptmstr/ui/typeset/gate.py", "not opened: the destination has no scheme"),
        ("vscode://file/x", "not opened: the vscode: scheme is not followed"),
        ("https://a@b.example/", "not opened: the https: destination names a user before its host"),
    ],
)
def test_a_refused_link_under_the_mouse_says_why(
    seam: Any, tooltips: list[str], destination: str, said: str
) -> None:
    typeset.follow_links(Recorder(), refusal=links.followable)
    cache = typeset.ViewCache()

    def draw() -> None:
        typeset.document(cache, "k", (Part(f"Read [this]({destination}) first."),))

    frame(seam, draw)
    assert tooltips == []
    _hover_the_link(seam, draw, lambda: _only_view(cache, "k"))
    assert tooltips == [said]


def test_a_refused_link_in_a_stream_says_why(seam: Any, tooltips: list[str]) -> None:
    typeset.follow_links(Recorder(), refusal=links.followable)
    cache = typeset.ViewCache()
    stream = typeset.stream(cache, "s")
    stream.append("Read [this](ftp://x.org/a) first.\n\n")
    frame(seam, stream.draw)
    _hover_the_link(seam, stream.draw, lambda: stream.view)
    assert tooltips == ["not opened: the ftp: scheme is not followed"]


@pytest.mark.parametrize(
    ("refusal", "on_link", "source"),
    [
        (links.followable, None, LINKED),
        (None, None, "Read [this](ftp://x.org/a) first."),
        (links.followable, Recorder(), "Read [this](ftp://x.org/a) first."),
    ],
    ids=["followable", "no-refusal-installed", "explicit-on-link"],
)
def test_no_tooltip_where_nothing_is_refused_or_the_policy_is_not_the_apps(
    seam: Any,
    tooltips: list[str],
    refusal: Callable[[str], str | None] | None,
    on_link: Recorder | None,
    source: str,
) -> None:
    typeset.follow_links(Recorder(), refusal=refusal)
    cache = typeset.ViewCache()

    def draw() -> None:
        typeset.document(cache, "k", (Part(source),), on_link=on_link)

    frame(seam, draw)
    _hover_the_link(seam, draw, lambda: _only_view(cache, "k"))
    assert _only_view(cache, "k").hovered_link is not None
    assert tooltips == []
