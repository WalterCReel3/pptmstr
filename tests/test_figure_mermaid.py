"""
A committed ```mermaid fence drawn as a figure: laid out by merman in the render
child, opened by the post-pass, passed through the svg sanitiser, and hosted as an
svg figure is.

Tests marked ``needs_merman`` run real merman in a real render child and skip where
the optional ``mermaid`` extra is not installed. The rest hold the host and the
child's protocol with merman absent, faked, or made unimportable, so they run on
every install.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pptmstr.figures import host, mermaid, render
from pptmstr.figures.render import Failed, Laid, Rendered, Renderer, Result
from pptmstr.figures.sanitise import Sanitised, sanitise
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


def ink(result: Result) -> int:
    assert isinstance(result, Rendered), result
    alpha = result.rgba[3::4]
    return len(alpha) - alpha.count(0)


@needs_merman
@pytest.mark.parametrize("family", list(DIAGRAMS))
def test_each_family_is_laid_out_in_the_child_and_renders_ink(child: Renderer, family: str) -> None:
    child.lay_out(("layout", family), DIAGRAMS[family])
    laid = wait_for(child, ("layout", family))
    assert isinstance(laid, Laid), laid
    natural = laid.sanitised
    assert natural.width > 0 and natural.height > 0
    assert "foreignObject" not in natural.svg
    assert sanitise(natural.svg) == natural, "what the child hands back is sanitised"
    child.submit(("raster", family), natural, natural.width, 1.0)
    pixels = wait_for(child, ("raster", family))
    assert isinstance(pixels, Rendered)
    assert ink(pixels) > 0.02 * pixels.width * pixels.height


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


def test_a_layout_and_a_raster_for_the_same_figure_are_separate_requests() -> None:
    renderer = Renderer()
    try:
        renderer.lay_out(("layout", "a"), "not mermaid at all")
        renderer.submit(("a", 1.0), LAID, 300.0, 1.0)
        first = wait_for(renderer, ("layout", "a"))
        second = wait_for(renderer, ("a", 1.0))
    finally:
        renderer.shutdown()
    assert isinstance(first, Failed)
    assert isinstance(second, Rendered)


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
    fake.answer_rasters()
    frame(seam, lambda: handle.draw())
    assert reached == []
    assert [kind for kind, *_ in fake.asked] == ["layout", "raster"]
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
    merman sizes each label's nested ``<svg>`` by a face narrower than the one resvg
    draws with, and clips it there (probe M1). After the post-pass no nested box
    clips, and the same diagram draws more ink: the glyphs that were cut off.
    """
    import merman  # this process is a test, not the GUI: merman may run here

    emitted = merman.Merman().render_svg(DIAGRAMS[family], mermaid.PIPELINE_OPTIONS)
    import numpy as np

    nested = emitted.count("<svg") - 1
    assert nested > 0 and "overflow:hidden" in emitted.split(">", 1)[1]
    opened = mermaid.visible_labels(emitted)
    assert opened.count("overflow:visible") >= nested
    assert "overflow:hidden" not in opened.split(">", 1)[1]

    before, after = sanitise(emitted), sanitise(opened)
    assert isinstance(before, Sanitised) and isinstance(after, Sanitised)
    options = render.resvg_options(render.imscribe_fonts())
    width = round(before.width)

    def text_ink(svg: str) -> int:
        """
        Opaque dark pixels: the labels' glyphs, not the light fills of the boxes.
        """
        w, h, rgba = render.rasterise(svg, width, options)
        px = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
        return int(((px[..., 3] > 128) & (px[..., :3].max(axis=2) < 110)).sum())

    clipped, whole = text_ink(before.svg), text_ink(after.svg)
    # Measured 2026-10-21 on these diagrams: 4-9% more (class 1148 -> 1255).
    assert whole > clipped * 1.015, f"the opened labels drew no more ink: {clipped} -> {whole}"


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


class Texture:
    def __init__(self, ident: int) -> None:
        self.ident = ident

    def texture_id(self) -> int:
        return self.ident


class FakeRenderer:
    """
    The render process as the host sees it, layouts included: what it was asked, and
    what it answers at the next poll.
    """

    def __init__(self) -> None:
        self.asked: list[tuple[str, Hashable, Any]] = []
        self.answers: list[Result] = []

    def submit(self, key: Hashable, sanitised: Sanitised, px_width: float, scale: float) -> None:
        self.asked.append(("raster", key, (sanitised, px_width, scale)))

    def lay_out(self, key: Hashable, source: str) -> None:
        self.asked.append(("layout", key, source))

    def poll(self) -> list[Result]:
        out, self.answers = self.answers, []
        return out

    def layouts(self) -> list[tuple[Hashable, str]]:
        return [(key, source) for kind, key, source in self.asked if kind == "layout"]

    def answer_layouts(self, laid: Sanitised = LAID) -> None:
        for key, _ in self.layouts():
            self.answers.append(Laid(key, laid))

    def answer_rasters(self) -> None:
        for kind, key, args in self.asked:
            if kind == "raster":
                sanitised, px_width, scale = args
                w, h = render.raster_size(sanitised, px_width, scale)
                self.answers.append(Rendered(key, w, h, bytes(w * h * 4), px_width, scale))


@pytest.fixture
def fake(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRenderer]:
    renderer = FakeRenderer()
    made: list[int] = []

    def upload(rgba: bytes, width: int, height: int) -> Texture:
        made.append(width)
        return Texture(len(made))

    host.release_all()
    monkeypatch.setattr(host, "_MARKS", OrderedDict())
    monkeypatch.setattr(mermaid, "_VERSION", ["0.9.1"])
    monkeypatch.setattr(host._STATE, "renderer", lambda: renderer)
    monkeypatch.setattr(host._STATE, "upload", upload)
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
    assert not [kind for kind, *_ in fake.asked if kind == "raster"], "no raster before layout"
    fake.answer_layouts()
    frame(seam, lambda: typeset.markdown(cache, "doc", OPEN + "```\n\nAfter.\n"))
    assert widget.height_for(600.0) == 150.0
    assert widget.height_for(150.0) == 75.0
    rasters = [args for kind, _, args in fake.asked if kind == "raster"]
    assert rasters and rasters[0][0] == LAID, "the laid-out SVG goes the svg path's way"


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
    fake.answer_rasters()
    frame(seam, lambda: handle.draw())
    first = handle.view
    asked = len(fake.asked)
    handle.set_text("Another start.\n\n" + text)
    frame(seam, lambda: handle.draw())
    assert handle.view is not first
    (widget,) = hosted(handle.view)
    assert widget.height_for(500.0) == 150.0, "no placeholder: the layout is held"
    frame(seam, lambda: handle.draw())
    assert len(fake.asked) == asked, "neither laid out nor rasterised again"


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


@pytest.mark.parametrize("name", ["blocks", "committed", "sources", "revision"])
def test_an_attribute_error_inside_a_captioned_property_is_raised_not_forwarded(
    fake: FakeRenderer, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """
    Python falls back to ``__getattr__`` when a property raises ``AttributeError``.
    Forwarded, that answered the stream's raw blocks (no figure mark, no caption) for
    a defect in ``_captioned``; it has to surface as an error instead.
    """
    from pptmstr.ui.typeset import views

    library = Library()
    captioned = views._Captioned(library)
    captioned.blocks  # noqa: B018 -- fills what committed and sources read

    def broken(*args: Any) -> Any:
        raise AttributeError("a defect inside the property")

    class Unreadable:
        def __iter__(self) -> Any:
            raise AttributeError("a defect inside the property")

    if name == "blocks":
        monkeypatch.setattr(host, "is_figure_fence", broken)
    elif name == "committed":
        del library.committed
    elif name == "sources":
        monkeypatch.setattr(captioned, "_mine", Unreadable())
    else:
        monkeypatch.delattr(Library, "revision")
    with pytest.raises(RuntimeError, match=f"_Captioned.{name} raised AttributeError") as err:
        getattr(captioned, name)
    assert isinstance(err.value.__cause__, AttributeError)


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
