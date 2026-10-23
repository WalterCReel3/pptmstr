"""
A committed ```mermaid fence drawn as a figure: laid out by merman in the render
child, opened by the post-pass, passed through the svg sanitiser, parsed there into
a display list, and hosted as a figure the pane draws natively.

Tests marked ``needs_merman`` run real merman in a real render child and skip where
the optional ``mermaid`` extra is not installed. The rest hold the host and the
child's protocol with merman absent, faked, or made unimportable, so they run on
every install.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pptmstr.figures import displaylist, host, mermaid, render
from pptmstr.figures.render import Failed, Laid, Renderer, Result
from pptmstr.figures.sanitise import Refused, Sanitised, sanitise
from pptmstr.ui import typeset

needs_merman = pytest.mark.skipif(
    mermaid.version() is None, reason="the optional 'mermaid' extra (merman) is not installed"
)

WINDOW = (700.0, 600.0)

DIAGRAMS: dict[str, str] = {
    "flowchart": """flowchart TD
    A[User submits form] --> B{Valid input?}
    B -->|Yes| C[Save to database]
    B -->|No| D[Show validation errors]
    D --> A
""",
    "sequence": """sequenceDiagram
    participant C as Client
    participant G as API Gateway
    C->>G: POST /login {user, password}
    G-->>C: 200 + JWT
    Note over C,G: The token expires after 15 minutes
""",
    "class": """classDiagram
    class Animal {
        <<abstract>>
        +String name
        +makeSound()* void
    }
    class Dog {
        +String breed
        +fetch(item: Ball) bool
    }
    Animal <|-- Dog
""",
    "state": """stateDiagram-v2
    [*] --> Idle
    Idle --> Running : start()
    Running --> Paused : pause()
    Paused --> Running : resume()
    Running --> [*] : complete
""",
    "er": """erDiagram
    CUSTOMER ||--o{ ORDER : places
    CUSTOMER {
        int id PK
        string name
        string email UK
    }
    ORDER {
        int id PK
        int customer_id FK
    }
""",
}
CLIPPED = ("class", "state", "er")
BROKEN = "flowchart LR\n    A --> \n"

BODY = DIAGRAMS["flowchart"].rstrip("\n")
OPEN = f"Intro paragraph.\n\n```mermaid\n{BODY}\n"
LAID = Sanitised(
    svg=(
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="150" '
        'viewBox="0 0 300 150"><rect width="300" height="150" fill="#36c"/></svg>'
    ),
    width=300.0,
    height=150.0,
)
LAID_LIST = displaylist.from_svg(LAID.svg)


# -- the render child: real merman ------------------------------------------------------


def wait_for(renderer: Renderer, key: Hashable, seconds: float = 30.0) -> Result:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for result in renderer.poll():
            if result.key == key:
                return result
        time.sleep(0.005)
    raise AssertionError(f"no result for {key!r} in {seconds} s")


@pytest.fixture(scope="module")
def child() -> Iterator[Renderer]:
    renderer = Renderer()
    yield renderer
    renderer.shutdown()


@needs_merman
@pytest.mark.parametrize("family", list(DIAGRAMS))
def test_each_family_is_laid_out_in_the_child_as_a_bounded_display_list(
    child: Renderer, family: str
) -> None:
    """
    What crosses the pipe is a display list within every bound, with nodes, labels
    and the label size set for the pane's prose face (Roboto's em is 0.853 of its
    ImGui size, so merman's 14 px em is about 16.4); never SVG or pixels.
    """
    child.lay_out(("layout", family), DIAGRAMS[family])
    laid = wait_for(child, ("layout", family))
    assert isinstance(laid, Laid), laid
    display = laid.display
    assert isinstance(display, displaylist.DisplayList)
    assert displaylist.validate(display) is None
    assert display.width > 0 and display.height > 0
    assert display.nodes and display.items
    runs = [i for i in display.items if isinstance(i, displaylist.TextRun)]
    assert runs and all(isinstance(r.text, str) for r in runs)
    sizes = {round(r.size, 1) for r in runs if r.role == displaylist.LABEL}
    assert 16.4 in sizes or 16.0 in sizes or any(14.0 < s < 20.0 for s in sizes), sizes


@needs_merman
def test_a_syntax_error_is_a_failure_with_mermans_first_line(child: Renderer) -> None:
    child.lay_out(("layout", "broken"), BROKEN)
    failed = wait_for(child, ("layout", "broken"))
    assert isinstance(failed, Failed)
    assert not failed.transient, "merman's answer would be the same again"
    assert failed.reason.startswith("merman: Diagram parse error")
    assert "\n" not in failed.reason and len(failed.reason) <= len("merman: ") + 160


def test_a_child_where_merman_cannot_be_imported_answers_with_the_extras_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A ``merman`` package that raises on import, put first on the path: the spawned
    child takes the parent's ``sys.path``, so it imports this one, installed or not.
    """
    shadow = tmp_path / "merman"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise ImportError('made unimportable by the test')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    renderer = Renderer()
    try:
        renderer.lay_out(("layout", "x"), BODY)
        failed = wait_for(renderer, ("layout", "x"))
    finally:
        renderer.shutdown()
    assert isinstance(failed, Failed)
    assert failed.reason == mermaid.EXTRA_REASON
    assert not failed.transient


# -- merman runs in the render child and nowhere else -----------------------------------


def test_layout_refuses_to_run_outside_a_child_process() -> None:
    with pytest.raises(RuntimeError, match="render child"):
        mermaid.layout(BODY)


def test_the_host_never_calls_merman_on_the_frame_thread(
    fake: FakeRenderer, monkeypatch: pytest.MonkeyPatch, seam: Any
) -> None:
    """
    A whole stream, from an open fence to a laid-out, drawn figure, with every entry
    to merman made to fail the test if it is reached. Only the fake renderer's
    ``lay_out`` is asked, and ``merman`` is not imported here.
    """
    imported_before = "merman" in sys.modules
    reached: list[str] = []

    def trap(name: str) -> Callable[..., Any]:
        def called(*args: Any, **kwargs: Any) -> Any:
            reached.append(f"{name} on {threading.current_thread().name}")
            raise AssertionError(name)

        return called

    monkeypatch.setattr(mermaid, "layout", trap("layout"))
    monkeypatch.setattr(mermaid, "_engine", trap("_engine"))
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    for piece in [OPEN, "```\n\nAfter.\n"]:
        handle.append(piece)
        frame(seam, lambda: handle.draw())
    fake.answer_layouts()
    frame(seam, lambda: handle.draw())
    frame(seam, lambda: handle.draw())
    assert reached == []
    assert [kind for kind, *_ in fake.asked] == ["layout"]
    assert ("merman" in sys.modules) == imported_before


# -- the post-pass ------------------------------------------------------------------------

NESTED = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100%" style="max-width:200px;'
    'overflow:hidden" viewBox="0 0 200 50">'
    '<g><svg x="0" width="20" height="21" style="overflow:hidden;opacity:1">'
    '<text y="15">a long label</text></svg>'
    '<svg width="20" height="21" overflow="hidden"><text y="15">another</text></svg>'
    '<svg width="20" height="21" style="overflow : HIDDEN"><text>third</text></svg>'
    "</g></svg>"
)


def test_the_post_pass_lets_every_nested_svg_overflow_and_leaves_the_root() -> None:
    out = mermaid.visible_labels(NESTED)
    root, *nested = out.split("<svg")[1:]
    assert "overflow:hidden" in root, "the root is not the post-pass's"
    assert len(nested) == 3
    for tag in nested:
        head = tag.split(">", 1)[0].lower()
        assert "hidden" not in head
        assert "overflow:visible" in head or 'overflow="visible"' in head
    assert "opacity:1" in out, "the rest of the style is kept"
    assert mermaid.visible_labels(out) == out


def test_the_post_pass_is_linear_on_a_long_unclosed_tag() -> None:
    hostile = "<svg>" + "<svg " + "overflow " * 200_000
    started = time.perf_counter()
    mermaid.visible_labels(hostile)
    assert time.perf_counter() - started < 1.0


@needs_merman
@pytest.mark.parametrize("family", CLIPPED)
def test_the_post_pass_keeps_labels_inside_their_boxes(family: str) -> None:
    """
    merman sizes each label's nested ``<svg>`` by its own metrics, narrower than the
    pane's face, and clips it there (probe M1). After the post-pass no nested box
    clips, so a label set wider than merman measured it still shows whole.
    """
    import merman  # this process is a test, not the GUI: merman may run here

    emitted = merman.Merman().render_svg(DIAGRAMS[family], mermaid.PIPELINE_OPTIONS)
    nested = emitted.count("<svg") - 1
    assert nested > 0 and "overflow:hidden" in emitted.split(">", 1)[1]
    opened = mermaid.visible_labels(emitted)
    assert opened.count("overflow:visible") >= nested
    assert "overflow:hidden" not in opened.split(">", 1)[1]
    assert isinstance(sanitise(opened), Sanitised)


def test_a_merman_error_is_its_first_line_cut_short() -> None:
    class Binding(Exception):
        message = "Diagram parse error (flowchart-v2): unexpected end\nmore detail\n"

    assert mermaid.error_line(Binding("code=5, message=...")) == (
        "merman: Diagram parse error (flowchart-v2): unexpected end"
    )
    long = mermaid.error_line(ValueError("x" * 500))
    assert long.startswith("merman: ValueError: x") and long.endswith("…")
    assert len(long) == len("merman: ") + mermaid.MAX_REASON_CHARS
    assert mermaid.error_line(RuntimeError()) == "merman: RuntimeError"


# -- the host -----------------------------------------------------------------------------


class FakeRenderer:
    """
    The render process as the host sees it, layouts included: what it was asked, and
    what it answers at the next poll.
    """

    def __init__(self) -> None:
        self.asked: list[tuple[str, Hashable, Any]] = []
        self.answers: list[Result] = []

    def lay_out(self, key: Hashable, source: str) -> None:
        self.asked.append(("layout", key, source))

    def poll(self) -> list[Result]:
        out, self.answers = self.answers, []
        return out

    def layouts(self) -> list[tuple[Hashable, str]]:
        return [(key, source) for kind, key, source in self.asked if kind == "layout"]

    def answer_layouts(self, laid: displaylist.DisplayList = LAID_LIST) -> None:
        for key, _ in self.layouts():
            self.answers.append(Laid(key, laid))


@pytest.fixture
def fake(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRenderer]:
    renderer = FakeRenderer()
    host.release_all()
    monkeypatch.setattr(host, "_MARKS", OrderedDict())
    monkeypatch.setattr(mermaid, "_VERSION", ["0.9.1"])
    monkeypatch.setattr(host._STATE, "renderer", lambda: renderer)
    yield renderer
    host.release_all()


def frame(im: Any, body: Callable[[], object]) -> None:
    typeset.pre_frame()
    host.pump()
    im.new_frame()
    im.set_next_window_pos(im.ImVec2(10.0, 10.0))
    im.set_next_window_size(im.ImVec2(*WINDOW))
    im.begin("##mermaid-test")
    try:
        body()
    finally:
        im.end()
        im.render()


def hosted(view: Any) -> list[host.FigureWidget]:
    return [w for n in view.hosts if isinstance(w := view.hosted(n), host.FigureWidget)]


CHUNKINGS: dict[str, list[str]] = {
    "close-marks-then-newline": [OPEN, "```", "\n"],
    "close-line-with-next-para": [OPEN, "```\n\nAfter the fence.\n"],
    "char-by-char": list(OPEN + "```\n\nAfter the fence.\n"),
}


@pytest.mark.parametrize("name", CHUNKINGS)
def test_a_mermaid_fence_is_code_while_open_and_hosted_at_commit(
    fake: FakeRenderer, seam: Any, name: str
) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    fed = ""
    for piece in CHUNKINGS[name]:
        handle.append(piece)
        fed += piece
        frame(seam, lambda: handle.draw())
        if "```\n" not in fed.split(BODY, 1)[-1] or BODY not in fed:
            assert hosted(handle.view) == [], f"hosted while open, after {fed[-12:]!r}"
            assert fake.asked == []
    assert not handle.closed
    (widget,) = hosted(handle.view)
    assert widget.mark.source == BODY
    assert widget.text == f"```mermaid\n{BODY}\n```"
    assert len(fake.layouts()) == 1, "laid out once, from the committed fence"


def test_a_fence_that_ends_on_its_marks_is_hosted_once_the_stream_closes(
    fake: FakeRenderer, seam: Any
) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    for piece in (OPEN, "```"):
        handle.append(piece)
        frame(seam, lambda: handle.draw())
        assert hosted(handle.view) == []
    handle.close()
    frame(seam, lambda: handle.draw())
    assert len(hosted(handle.view)) == 1


def test_an_open_mermaid_fence_is_code_under_its_caption(fake: FakeRenderer, seam: Any) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(OPEN + "    more --> to_come\n")
    for _ in range(3):
        frame(seam, lambda: handle.draw())
    assert not handle.view.hosts
    assert any(text.startswith("mermaid\nflowchart TD") for text in cache.texts("reply"))
    assert fake.asked == []


def test_the_placeholder_stands_until_the_layout_and_then_the_real_height(
    fake: FakeRenderer, seam: Any
) -> None:
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "doc", OPEN + "```\n\nAfter.\n"))
    (widget,) = hosted(cache.view("doc"))
    assert widget.height_for(600.0) == host.PLACEHOLDER_HEIGHT
    frame(seam, lambda: typeset.markdown(cache, "doc", OPEN + "```\n\nAfter.\n"))
    assert len(fake.layouts()) == 1, "asked once, however often its height is"
    fake.answer_layouts()
    frame(seam, lambda: typeset.markdown(cache, "doc", OPEN + "```\n\nAfter.\n"))
    assert widget.height_for(600.0) == 150.0
    assert widget.height_for(150.0) == 75.0
    assert widget.drawn[1] > 0, "drawn the frame its list arrived"


def test_a_laid_out_figure_rehosted_answers_its_height_at_once(
    fake: FakeRenderer, seam: Any
) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    text = OPEN + "```\n\nAfter.\n"
    handle.append(text)
    frame(seam, lambda: handle.draw())
    fake.answer_layouts()
    frame(seam, lambda: handle.draw())
    first = handle.view
    asked = len(fake.asked)
    handle.set_text("Another start.\n\n" + text)
    frame(seam, lambda: handle.draw())
    assert handle.view is not first
    (widget,) = hosted(handle.view)
    assert widget.height_for(500.0) == 150.0, "the layout is held"
    frame(seam, lambda: handle.draw())
    assert len(fake.asked) == asked == 1, "not laid out again"


def test_a_layout_is_keyed_by_the_merman_version(
    fake: FakeRenderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    one = host.mark("mermaid", BODY)
    monkeypatch.setattr(host, "_MARKS", OrderedDict())
    monkeypatch.setattr(mermaid, "_VERSION", ["0.9.2"])
    two = host.mark("mermaid", BODY)
    assert isinstance(one, host.FigureMark) and isinstance(two, host.FigureMark)
    assert one.sha != two.sha


def test_without_merman_a_mermaid_fence_stays_code_with_the_extras_reason(
    fake: FakeRenderer, seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mermaid, "_VERSION", [None])
    source = f"Before.\n\n```mermaid\n{BODY}\n```\n\nAfter.\n"
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "doc", source))
    frame(seam, lambda: typeset.markdown(cache, "doc", source))
    assert not cache.view("doc").hosts
    texts = cache.texts("doc")
    (at,) = [n for n, t in enumerate(texts) if t.startswith("mermaid not drawn: ")]
    assert texts[at] == f"mermaid not drawn: {mermaid.EXTRA_REASON}"
    assert texts[at + 1] == f"mermaid\n{BODY}"
    assert fake.asked == []


def test_a_layout_failure_keeps_the_source_on_screen_under_mermans_reason(
    fake: FakeRenderer, seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = typeset.ViewCache()
    source = OPEN + "```\n\nAfter.\n"
    frame(seam, lambda: typeset.markdown(cache, "doc", source))
    (key, _), *_ = fake.layouts()
    reason = "merman: Diagram parse error (flowchart-v2): unexpected end of input"
    fake.answers.append(Failed(key, reason))
    drawn: list[str] = []
    real = host.FigureWidget._draw_failed

    def spy(self: Any, im: Any, said: str, *rest: Any) -> None:
        drawn.append(said)
        real(self, im, said, *rest)

    frame(seam, lambda: typeset.markdown(cache, "doc", source))
    (widget,) = hosted(cache.view("doc"))
    monkeypatch.setattr(host.FigureWidget, "_draw_failed", spy)
    frame(seam, lambda: widget.draw(width_px=500.0))
    assert drawn == [f"mermaid not drawn: {reason}"]
    assert widget.height_for(500.0) > 0
    assert len(fake.layouts()) == 1, "an answer merman gave is not asked again"


def test_a_layout_that_timed_out_is_asked_once_more_after_the_back_off(
    fake: FakeRenderer, seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [100.0]
    monkeypatch.setattr(host._STATE, "clock", lambda: now[0])
    mark = host.mark("mermaid", BODY)
    assert isinstance(mark, host.FigureMark)
    widget = host.FigureWidget(mark, BODY)
    key = ("layout", mark.sha)
    timed_out = "render took longer than 10 s and was stopped"

    def ask() -> None:
        frame(seam, lambda: widget.draw(width_px=500.0))

    ask()
    fake.answers.append(Failed(key, timed_out, True))
    ask()
    ask()
    assert len(fake.layouts()) == 1, "not while the back-off runs"
    assert widget.height_for(500.0) == host.PLACEHOLDER_HEIGHT
    now[0] += host.RETRY_AFTER_S + 0.1
    ask()
    assert len(fake.layouts()) == 2
    fake.answers.append(Failed(key, timed_out, True))
    ask()
    now[0] += host.RETRY_AFTER_S + 0.1
    ask()
    assert len(fake.layouts()) == 2, "a second transient failure is kept"
    assert host._STATE.failures[mark.sha] == timed_out


# -- the captioned stream does not hide its own errors ------------------------------------


class Library:
    """
    A library stream as ``views._Captioned`` reads it, with one fence.
    """

    revision = 0
    theme = "the stream's theme"
    live_theme = "the stream's live theme"
    source = "the stream's source"
    sources: tuple[Any, ...] = ()

    def __init__(self) -> None:
        from imscribe import parse_markdown

        from pptmstr.ui.typeset import themes

        self.blocks = parse_markdown(f"```mermaid\n{BODY}\n```\n", themes.document_theme()).blocks
        self.committed = tuple(self.blocks)


@pytest.mark.parametrize("name", ["blocks", "committed", "sources"])
def test_a_defect_inside_a_captioned_property_answers_the_streams_own_and_is_logged(
    fake: FakeRenderer, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """
    The library reads these from inside ``view.sync()``, where an exception would
    stop it in every pane. An ``AttributeError`` is the hard case: Python would fall
    back to ``__getattr__`` with it. Caught at the property's edge instead, logged
    as pptmstr's, and the stream's own answer from then on.
    """
    from pptmstr.log import LOG, Level
    from pptmstr.ui.typeset import gate, views

    monkeypatch.setattr(gate, "_DEFECTS_SAID", set())
    library = Library()
    captioned = views._Captioned(library)
    captioned.blocks  # noqa: B018 -- fills what committed and sources read
    before = captioned.revision

    class Unreadable:
        def __iter__(self) -> Any:
            raise AttributeError("a defect inside the property")

    def broken(*args: Any) -> Any:
        raise AttributeError("a defect inside the property")

    if name == "blocks":
        monkeypatch.setattr(host, "is_figure_fence", broken)
    elif name == "committed":
        monkeypatch.setattr(captioned, "_from", Unreadable())
    else:
        monkeypatch.setattr(captioned, "_mine", Unreadable())
    assert getattr(captioned, name) == tuple(getattr(library, name))
    assert captioned.raw and captioned.revision != before, "the view reads its blocks again"
    assert captioned.blocks == tuple(library.blocks)
    entries, _ = LOG.snapshot()
    said = [e.text for e in entries[-5:] if e.level is Level.ERROR]
    assert any(
        text.startswith(
            f"pptmstr's own code raised AttributeError while captioning a stream's {name}"
        )
        and "Traceback" in text
        for text in said
    ), said


def test_the_captioned_stream_forwards_only_what_the_stream_view_reads(fake: FakeRenderer) -> None:
    from pptmstr.ui.typeset import views

    library = Library()
    captioned = views._Captioned(library)
    assert captioned.theme == library.theme
    assert captioned.live_theme == library.live_theme
    assert captioned.source == library.source
    with pytest.raises(AttributeError, match="does not forward 'append'"):
        captioned.append  # noqa: B018
    (block,) = captioned.blocks
    assert isinstance(block.language, host.FigureMark), "a committed mermaid fence is marked"


# -- directives, the engine, the platform ----------------------------------------------

INIT = "%%{init: {'theme': 'dark', 'fontFamily': 'NoSuchFace'}}%%\n"


def test_every_directive_is_taken_out_and_nothing_else() -> None:
    source = f"{INIT}flowchart LR\n    %%{{wrap}}%% a --> b\n    %% a comment\n"
    assert mermaid.without_directives(source) == ("flowchart LR\n     a --> b\n    %% a comment\n")
    across = "%%{init: {\n  'theme': 'dark'\n}}%%\nflowchart LR\n  a --> b\n"
    assert mermaid.without_directives(across) == "flowchart LR\n  a --> b\n"
    unclosed = "flowchart LR\n  a --> b %%{init: {'theme': 'dark'\n"
    assert mermaid.without_directives(unclosed) == unclosed
    assert mermaid.without_directives(BODY) == BODY


@pytest.mark.parametrize("end", ["\n", "\r\n", "\r"])
def test_a_directive_line_takes_its_line_end_so_a_front_matter_under_it_is_read(
    end: str,
) -> None:
    source = end.join(["%%{init: {}}%%", "---", "config: {theme: dark}", "---", BODY])
    assert mermaid.without_config(mermaid.without_directives(source)) == BODY


def test_taking_directives_out_is_linear_on_hostile_input() -> None:
    hostile = "%%{" * 200_000
    started = time.perf_counter()
    assert mermaid.without_directives(hostile) == hostile
    mermaid.without_directives("%%{x}%%" * 200_000)
    assert time.perf_counter() - started < 1.0


class Engine:
    """
    A stand-in for ``merman.Merman``: what it was handed, and a first engine that
    raises on every diagram, as a binding left broken by a panic would.
    """

    made: list[Engine] = []

    def __init__(self) -> None:
        Engine.made.append(self)
        self.broken = len(Engine.made) == 1
        self.given: list[str] = []

    def render_svg(self, source: str, options: str) -> str:
        self.given.append(source)
        if self.broken:
            raise RuntimeError("the binding panicked")
        return LAID.svg


@pytest.fixture
def stand_in(monkeypatch: pytest.MonkeyPatch) -> type[Engine]:
    module = types.ModuleType("merman")
    module.Merman = Engine  # type: ignore[attr-defined]
    Engine.made = []
    monkeypatch.setitem(sys.modules, "merman", module)
    monkeypatch.setattr(mermaid, "_ENGINE", [])
    monkeypatch.setattr(mermaid, "in_render_child", lambda: True)
    return Engine


def test_a_merman_exception_drops_the_engine_and_the_next_diagram_has_a_new_one(
    stand_in: type[Engine],
) -> None:
    first = mermaid.layout(BODY)
    assert isinstance(first, Refused) and "the binding panicked" in first.reason
    assert mermaid._ENGINE == []
    second = mermaid.layout(BODY)
    assert isinstance(second, Sanitised)
    assert len(stand_in.made) == 2
    assert isinstance(mermaid.layout(BODY), Sanitised)
    assert len(stand_in.made) == 2, "an engine that answered is kept"


def test_merman_is_handed_the_diagram_without_its_directives(stand_in: type[Engine]) -> None:
    mermaid.layout(BODY)
    mermaid.layout(INIT + BODY)
    assert stand_in.made[-1].given == [BODY]


@needs_merman
def test_a_dark_init_directive_lays_out_as_the_diagram_without_it(child: Renderer) -> None:
    child.lay_out(("layout", "plain"), BODY)
    child.lay_out(("layout", "init"), INIT + BODY)
    plain, init = wait_for(child, ("layout", "plain")), wait_for(child, ("layout", "init"))
    assert isinstance(plain, Laid) and isinstance(init, Laid)
    assert init.display == plain.display


DARK_CONFIG = "---\nconfig: {theme: dark}\n---\n"
TITLED = "---\ntitle: Pipeline stages\nconfig:\n  theme: dark\n  fontFamily: NoSuchFace\n---\n"


@pytest.mark.parametrize(
    "source, kept",
    [
        (DARK_CONFIG + BODY, BODY),
        ("---\nconfig:\n  theme: dark\n---\n" + BODY, BODY),
        ('---\n"config": {theme: dark}\n---\n' + BODY, BODY),
        ("---\n<<: {config: {theme: dark}}\n---\n" + BODY, BODY),
        (TITLED + BODY, "---\ntitle: Pipeline stages\n---\n" + BODY),
        (
            "---\nconfig: {theme: dark}\ntitle: |\n  two\n\n  lines\ndisplayMode: compact\n---\n"
            + BODY,
            "---\ntitle: |\n  two\n\n  lines\ndisplayMode: compact\n---\n" + BODY,
        ),
        # Indented whole, which merman reads; a byte-order mark; lone and paired `\r`.
        (
            "  ---\n  title: T\n  config: {theme: dark}\n  ---\n  " + BODY,
            "---\n  title: T\n---\n  " + BODY,
        ),
        ("﻿" + DARK_CONFIG + BODY, BODY),
        ("---\rconfig: {theme: dark}\r---\r" + BODY, BODY),
        (
            "---\r\ntitle: T\r\nconfig: {theme: dark}\r\n---\r\n" + BODY,
            "---\ntitle: T\n---\n" + BODY,
        ),
        # A key can start on no line this does not see.
        ("---\ntitle: T config: {theme: dark}\n---\n" + BODY, "---\ntitle: T\n---\n" + BODY),
        # A line that is not at the top level by spaces is not a content key.
        (
            "---\ntitle: T\n config: {theme: dark}\n\tconfig: x\n---\n" + BODY,
            "---\ntitle: T\n---\n" + BODY,
        ),
        # The first `---` closes it, indented or not, and is written where merman closes.
        (
            "---\ntitle: a\n  ---\nconfig: {theme: dark}\n---\n" + BODY,
            "---\ntitle: a\n---\nconfig: {theme: dark}\n---\n" + BODY,
        ),
        ("---\nconfig: {theme: dark}\n---", ""),
    ],
)
def test_front_matter_keeps_only_its_content_keys(source: str, kept: str) -> None:
    assert mermaid.without_config(source) == kept


@pytest.mark.parametrize(
    "source",
    [
        BODY,
        "---\nconfig: {theme: dark}\n" + BODY,  # no close: merman refuses it
        "\n" + DARK_CONFIG + BODY,  # not on the first line: merman refuses it
        "flowchart LR\n---\nconfig: {theme: dark}\n---\n",
        "",
        "---",
    ],
)
def test_a_source_with_no_front_matter_is_left_as_written(source: str) -> None:
    assert mermaid.without_config(source) == source


def test_taking_config_out_is_linear_on_hostile_input() -> None:
    started = time.perf_counter()
    mermaid.without_config("---\n" + "config: x\n" * 200_000)
    mermaid.without_config("---\n" + "  " * 200_000 + "\n---\n")
    assert time.perf_counter() - started < 1.0


def test_merman_is_handed_the_front_matter_without_its_config(stand_in: type[Engine]) -> None:
    mermaid.layout(INIT + TITLED + BODY)
    assert stand_in.made[-1].given == ["---\ntitle: Pipeline stages\n---\n" + BODY]


@needs_merman
def test_a_dark_front_matter_config_lays_out_as_the_diagram_without_it(child: Renderer) -> None:
    child.lay_out(("layout", "plain"), BODY)
    child.lay_out(("layout", "config"), DARK_CONFIG + BODY)
    child.lay_out(("layout", "after-init"), INIT + DARK_CONFIG + BODY)
    child.lay_out(("layout", "titled"), TITLED + BODY)
    plain = wait_for(child, ("layout", "plain"))
    config = wait_for(child, ("layout", "config"))
    after_init = wait_for(child, ("layout", "after-init"))
    titled = wait_for(child, ("layout", "titled"))
    assert isinstance(plain, Laid) and isinstance(config, Laid) and isinstance(after_init, Laid)
    assert config.display == plain.display == after_init.display
    assert isinstance(titled, Laid)

    def texts(laid: Laid) -> list[str]:
        return [i.text for i in laid.display.items if isinstance(i, displaylist.TextRun)]

    assert "Pipeline stages" in texts(titled) and "Pipeline stages" not in texts(plain)
    assert "NoSuchFace" not in repr(titled.display), "a display list names no face"


@pytest.mark.parametrize(
    "system, libc, machine, unavailable",
    [
        ("linux", ("glibc", "2.31"), "x86_64", True),
        ("linux", ("glibc", "2.34"), "x86_64", True),
        ("linux", ("glibc", "2.35"), "x86_64", False),
        ("linux", ("glibc", "2.36"), "x86_64", False),
        ("linux", ("glibc", "2.36"), "aarch64", True),
        ("linux", ("", ""), "x86_64", False),
        ("darwin", ("", ""), "arm64", False),
    ],
)
def test_where_merman_has_no_wheel_the_reason_says_so_and_names_no_extra(
    system: str, libc: tuple[str, str], machine: str, unavailable: bool
) -> None:
    reason = mermaid.missing_reason(system, libc, machine)
    if unavailable:
        assert reason.startswith("mermaid rendering is unavailable on this platform")
        assert "extra" not in reason
    else:
        assert reason == mermaid.EXTRA_REASON


def test_a_fence_without_merman_is_refused_with_the_platform_s_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host, "_MARKS", OrderedDict())
    monkeypatch.setattr(mermaid, "_VERSION", [None])
    monkeypatch.setattr(mermaid.platform, "libc_ver", lambda: ("glibc", "2.31"))
    monkeypatch.setattr(mermaid.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mermaid.sys, "platform", "linux")
    refused = host.mark("mermaid", BODY)
    assert isinstance(refused, Refused)
    assert refused.reason == mermaid.missing_reason("linux", ("glibc", "2.31"), "x86_64")


# -- the post-pass touches overflow only -----------------------------------------------


def test_the_post_pass_opens_overflow_and_leaves_every_other_attribute() -> None:
    svg = (
        '<svg width="9"><svg data-note="overflow:hidden" aria-label=\'overflow="hidden"\' '
        'style="text-overflow:hidden;overflow:hidden;overflow-x:hidden" '
        'text-overflow="hidden" overflow = "hidden"><text>x</text></svg></svg>'
    )
    out = mermaid.visible_labels(svg)
    nested = out.split("<svg", 2)[2].split(">", 1)[0]
    assert 'data-note="overflow:hidden"' in nested
    assert "aria-label='overflow=\"hidden\"'" in nested
    assert 'style="text-overflow:hidden;overflow:visible;overflow-x:hidden"' in nested
    assert 'text-overflow="hidden"' in nested
    assert 'overflow = "visible"' in nested


# -- the layouts held ------------------------------------------------------------------


def test_more_diagrams_than_the_held_layouts_keep_their_heights(
    fake: FakeRenderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Every figure asks for its layout from ``height_for``, every frame, on screen or
    not. With more of them than the held layouts can hold (bounded here at the 128
    the bound once was, so the test does not need megabytes), none may lose its own
    to the others, or it stands at the placeholder and asks again without end.
    """
    monkeypatch.setattr(host._STATE, "layouts", host._Layouts(128 * LAID_LIST.weight()))
    count = 300
    widgets = []
    for n in range(count):
        source = f"flowchart LR\n    a{n} --> b{n}\n"
        mark = host.mark("mermaid", source)
        assert isinstance(mark, host.FigureMark)
        widgets.append(host.FigureWidget(mark, source))
    for widget in widgets:
        widget.height_for(600.0)
    asked = fake.layouts()
    assert len(asked) == count
    # The child answers one layout at a time, so a pump takes a few, and every
    # figure's height is asked between pumps, as each frame asks it.
    for at in range(0, count, 25):
        fake.answers += [Laid(key, LAID_LIST) for key, _ in asked[at : at + 25]]
        host.pump()
        for widget in widgets:
            widget.height_for(600.0)
    held = 150.0
    for _ in range(3):
        assert [w.height_for(600.0) for w in widgets] == [held] * count
        host.pump()
    assert len(fake.layouts()) == count, "none was asked for again"


def test_the_held_layouts_evict_the_least_recently_read() -> None:
    one = LAID_LIST
    layouts = host._Layouts(budget=2 * one.weight())
    layouts.put("a", one)
    layouts.put("b", one)
    assert layouts.get("a") is one
    layouts.put("c", one)
    assert "a" in layouts and "c" in layouts and "b" not in layouts


# -- one retry for each request ---------------------------------------------------------


def test_a_retried_layout_is_drawn_when_it_arrives(
    fake: FakeRenderer, seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [100.0]
    monkeypatch.setattr(host._STATE, "clock", lambda: now[0])
    mark = host.mark("mermaid", BODY)
    assert isinstance(mark, host.FigureMark)
    widget = host.FigureWidget(mark, BODY)

    def ask() -> None:
        frame(seam, lambda: widget.draw(width_px=500.0))

    ask()
    fake.answers.append(Failed(("layout", mark.sha), "render process died", True))
    ask()
    now[0] += host.RETRY_AFTER_S + 0.1
    ask()
    assert len(fake.layouts()) == 2, "the layout's one retry"
    fake.answer_layouts()
    ask()
    assert widget._failure() is None and widget.drawn[1] > 0
    assert [kind for kind, *_ in fake.asked] == ["layout", "layout"]


def test_a_display_list_out_of_bounds_from_the_child_is_a_failure_and_not_drawn() -> None:
    """
    The I/O thread checks what the child sent before the frame thread sees it: a
    list past a bound, or not a list at all, is the figure's failure, kept.
    """
    wire = displaylist.encode(LAID_LIST)
    assert render.received("k", wire) == Laid("k", LAID_LIST)
    flat = displaylist.DisplayList(0.0, 10.0, (0.0, 0.0), (), (), ())
    for sent, why in (
        (displaylist.encode(flat), "size"),
        (b"<svg/>", "malformed (JSONDecodeError)"),
        (b"", "malformed (JSONDecodeError)"),
    ):
        got = render.received("k", sent)
        assert got == Failed("k", f"display list refused: {why}")
        assert not got.transient


# -- what any figure needs is read from what imports it ----------------------------------


def test_the_table_of_fences_needs_what_the_render_modules_import() -> None:
    from importlib import metadata

    from pptmstr.figures import fences

    assert fences._FIGURE is render.DISTRIBUTIONS
    for row in fences.RENDERED:
        if row.kind == "figure":
            assert set(render.DISTRIBUTIONS) | {"merman"} == set(row.needs)
    provides = metadata.packages_distributions()
    for distribution, module in render.CHILD_MODULES.items():
        top = module.split(".")[0]
        assert distribution.lower() in {d.lower() for d in provides.get(top, [])}, module


def test_no_figure_module_imports_a_rasteriser_or_an_array_library() -> None:
    """
    Figures are drawn natively: nothing under ``pptmstr/figures`` imports resvg,
    Pillow or numpy, at the top of a module or inside a function.
    """
    import ast

    import pptmstr.figures as figures

    folder = Path(figures.__file__).parent
    for path in sorted(folder.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in ("resvg_py", "PIL", "numpy"), (path.name, name)
