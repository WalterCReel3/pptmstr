"""
A committed ```math fence set as a display formula: when it turns, what it is read
with, what a refusal leaves on screen, and what a copy of it gives.

Headless: an ImGui context with no backend, with the library's fonts loaded
(``seam``). The real window is not here; the task's result says where it was
looked at.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from pptmstr import theme
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Part, gate, themes, views

BOLD = theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf"
WINDOW = (700.0, 600.0)
SUM = r"\sum_{i=1}^{n} i = \frac{n(n+1)}{2}"
ALIGNED = r"\begin{aligned} a &= b \\ c &= d \end{aligned}"
OPEN = f"Intro with $PATH=$HOME.\n\n```math\n{SUM}\n"

_IM: list[Any] = []


@pytest.fixture(autouse=True)
def _im(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    The seam, and ``gate.formula``'s answers held for this test alone: a test that
    stands in for the library's asking must not leave its answers to the next.
    """
    monkeypatch.setattr(gate, "_FORMULAS", OrderedDict())
    monkeypatch.setattr(gate, "_REFUSED", OrderedDict())
    monkeypatch.setattr(gate, "_SIZES", OrderedDict())
    monkeypatch.setattr(views, "_SIZED", {})
    _IM[:] = [seam]
    yield
    _IM.clear()


def frame(body: Callable[[], object], width: float = WINDOW[0]) -> None:
    im = _IM[0]
    typeset.pre_frame()
    im.new_frame()
    im.set_next_window_pos(im.ImVec2(10.0, 10.0))
    im.set_next_window_size(im.ImVec2(width, WINDOW[1]))
    im.begin("##math-test", None, im.WindowFlags_.horizontal_scrollbar.value)
    try:
        body()
    finally:
        im.end()
        im.render()


def formulas(view: Any) -> list[Any]:
    return [block for block in view.blocks if views._is_formula(block)]


def kinds(view: Any) -> list[str]:
    """
    One word a block: ``formula``, ``code:<caption>``, ``warn`` or ``text``.
    """
    out = []
    for block, text in zip(view.blocks, view.texts, strict=True):
        said = getattr(block, "language", None)
        if views._is_formula(block):
            out.append("formula")
        elif isinstance(said, views._Warned):
            out.append("warn")
        elif said is not None:
            out.append(f"code:{str(text).partition(chr(10))[0]}")
        else:
            out.append("text")
    return out


def no_formula_but_the_fences(view: Any) -> None:
    """
    Nothing but a spliced fence was read with mathematics on: every other block has
    no formula in it, so every ``$`` there is text.
    """
    for block in view.blocks:
        if not views._is_formula(block):
            assert not getattr(block, "formulas", ()), block


# -- when a fence turns ----------------------------------------------------------------


CHUNKINGS: dict[str, list[str]] = {
    "close-marks-then-newline": [OPEN, "```", "\n"],
    "close-line-with-next-para": [OPEN, "```\n\nAfter the fence.\n"],
    "char-by-char": list(OPEN + "```\n\nAfter the fence.\n"),
}


@pytest.mark.parametrize("name", CHUNKINGS)
def test_a_math_fence_is_a_formula_from_its_commit_and_code_while_open(name: str) -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    fed = ""
    for piece in CHUNKINGS[name]:
        handle.append(piece)
        fed += piece
        frame(lambda: handle.draw())
        closed_line = "```\n" in fed.split("```math\n", 1)[-1] if "```math\n" in fed else False
        if not closed_line:
            assert formulas(handle.view) == [], f"a formula while open, after {fed[-12:]!r}"
    assert not handle.closed
    (made,) = formulas(handle.view)
    assert made.formulas[0].source == SUM
    assert handle.unbuilt == 0
    no_formula_but_the_fences(handle.view)


def test_a_fence_that_ends_on_its_marks_is_a_formula_once_the_stream_closes() -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    for piece in (OPEN, "```"):
        handle.append(piece)
        frame(lambda: handle.draw())
        assert formulas(handle.view) == []
    handle.close()
    frame(lambda: handle.draw())
    assert len(formulas(handle.view)) == 1


def test_an_open_math_fence_is_code_under_its_caption() -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(OPEN + r"+ \frac{1}{2}" + "\n")
    for _ in range(3):
        frame(lambda: handle.draw())
    assert "code:math" in kinds(handle.view)
    assert formulas(handle.view) == []


def test_only_a_rendered_fence_changes_its_answer_at_commit() -> None:
    """
    S1: a stream's fence is the same block object before and after its commit, so a
    math fence's answer has to change there, and a python fence's must not.
    """
    from imscribe import parse_markdown

    blocks = parse_markdown(
        f"```python\nx = 1\n```\n\n```math\n{SUM}\n```\n", themes.document_theme()
    ).blocks

    class Library:
        revision = 0
        committed: tuple[Any, ...] = ()
        sources: tuple[Any, ...] = ()
        theme = themes.document_theme()

        def __init__(self) -> None:
            self.blocks = blocks

    library = Library()
    captioned = views._Captioned(library)
    before = captioned.blocks
    assert not views._is_formula(before[-1])
    library.committed = tuple(blocks)
    after = captioned.blocks
    assert after[0] is before[0], "the python fence keeps its answer"
    assert views._is_formula(after[-1])
    assert len(captioned.sources) == len(after)


def test_the_rule_names_the_rendered_fences() -> None:
    for said in ("math", "Math", "math display", "mermaid"):
        assert views.settles_rendered(said), said
    for said in ("python", "maths", "latex", "tex", ""):
        assert not views.settles_rendered(said), said
    assert views.is_math_fence("math") and not views.is_math_fence("svg")


# -- `$` in prose is text --------------------------------------------------------------


PROSE = (
    "Set $x$ here, and export $PATH=$HOME/bin; $5 or $10; and $$y^2$$ inline.\n\n"
    f"```math\n{SUM}\n```\n\n"
    "After it, $HOME and $x$ again.\n"
)


def test_dollars_in_prose_are_text_in_a_settled_reply_with_a_math_fence() -> None:
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", PROSE))
    view = cache.view("doc")
    assert kinds(view) == ["text", "formula", "text"]
    texts = cache.texts("doc")
    assert texts[0] == "Set $x$ here, and export $PATH=$HOME/bin; $5 or $10; and $$y^2$$ inline."
    assert texts[2] == "After it, $HOME and $x$ again."
    no_formula_but_the_fences(view)


def test_dollars_in_prose_are_text_in_a_stream_with_a_math_fence() -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(PROSE)
    for _ in range(2):
        frame(lambda: handle.draw())
    assert kinds(handle.view) == ["text", "formula", "text"]
    assert "$PATH=$HOME/bin" in cache.texts("reply")[0]
    no_formula_but_the_fences(handle.view)


def test_a_math_fence_inside_another_fence_is_not_read() -> None:
    source = "````markdown\n```math\nx^2\n```\n````\n"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    assert formulas(cache.view("doc")) == []


# -- a refusal -------------------------------------------------------------------------


REFUSED = f"Before.\n\n```math\n{ALIGNED}\n```\n\nAfter $PATH.\n"


def test_a_refused_formula_stays_code_under_its_reason_in_the_warn_ink() -> None:
    cache = typeset.ViewCache()
    for _ in range(2):
        frame(lambda: typeset.markdown(cache, "doc", REFUSED))
    view = cache.view("doc")
    assert kinds(view) == ["text", "warn", "code:math", "text"]
    texts = cache.texts("doc")
    assert (
        texts[1] == "math not set: \\begin opens or closes an environment, and there are none here"
    )
    assert texts[2] == f"math\n{ALIGNED}"
    assert (0, len(texts[1]), theme.P.warn.u32) in view._colours.get(1, ())
    assert cache.unbuilt("doc") == 0


@pytest.mark.parametrize(
    "body",
    ["", "x\n$$\ny", "\\frac{a}{b"],
    ids=["empty", "a-dollar-line", "unbalanced"],
)
def test_a_body_that_is_not_one_formula_is_code_with_a_reason(body: str) -> None:
    source = f"```math\n{body}\n```\n"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    texts = cache.texts("doc")
    assert texts[0].startswith("math not set: ")
    assert formulas(cache.view("doc")) == []
    assert cache._entries["doc"].unset is None


def test_one_refused_formula_does_not_send_the_entry_to_imgui() -> None:
    """
    The pair the guard exists for: a refused formula and a good one in one reply.
    Every frame the entry is the library's, the good formula is set, and nothing is
    reported unset.
    """
    source = f"```math\n{ALIGNED}\n```\n\n```math\n{SUM}\n```\n"
    cache = typeset.ViewCache()
    for _ in range(3):
        frame(lambda: typeset.markdown(cache, "doc", source))
        entry = cache._entries["doc"]
        assert entry.unset is None
        assert cache.unbuilt("doc") == 0
        assert all(segment.view is not None for segment in entry.segments)
    assert len(formulas(cache.view("doc"))) == 1


def test_a_formula_the_view_refuses_after_the_ask_said_yes_becomes_code_not_the_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The guard behind the asking: with the ask standing aside, the library meets the
    refused formula in the view, its draw answers false, and the frame after has that
    fence as code under the library's reason, the rest still the library's. The one
    frame it happened in is reported unset, as any draw that set less is.
    """
    monkeypatch.setattr(gate, "_refusal", lambda block, theme: None)
    source = f"Price $PATH.\n\n```math\n{ALIGNED}\n```\n\n```math\n{SUM}\n```\n"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    entry = cache._entries["doc"]
    assert entry.unset is None, "the entry was sent to ImGui as written"
    assert cache.unbuilt("doc") == source.count("\n") + 1
    for _ in range(2):
        frame(lambda: typeset.markdown(cache, "doc", source))
        assert entry.unset is None
        assert cache.unbuilt("doc") == 0
    view = cache.view("doc")
    assert kinds(view) == ["text", "warn", "code:math", "formula"]
    assert "\\begin opens or closes an environment" in cache.texts("doc")[1]


def test_the_guard_holds_in_a_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "_refusal", lambda block, theme: None)
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(f"Price $PATH.\n\n```math\n{ALIGNED}\n```\n\nMore.\n")
    frame(lambda: handle.draw())
    for _ in range(2):
        frame(lambda: handle.draw())
        assert handle.unbuilt == 0
        assert handle._unset is None
    assert kinds(handle.view) == ["text", "warn", "code:math", "text"]


def test_without_the_guard_the_whole_entry_would_go(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    What the guard and the ask stand against, pinned: with both out of the way the
    entry is ImGui's as written. If this stops holding, the library no longer
    answers false for a refused formula and the guard reads nothing.
    """
    monkeypatch.setattr(gate, "_refusal", lambda block, theme: None)
    monkeypatch.setattr(views, "_formulas_refused", lambda entry: False)
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", f"```math\n{ALIGNED}\n```\n"))
    assert cache._entries["doc"].unset is not None


# -- the size it is asked at -----------------------------------------------------------


def _paragraph_px(theme_: Any) -> float:
    from imscribe import BlockKind

    return float(theme_.style(BlockKind.PARAGRAPH).font.size)


def test_the_ask_is_made_in_the_theme_the_document_is_set_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[Any] = []
    real = gate._refusal

    def recording(block: Any, theme_: Any) -> str | None:
        asked.append(theme_)
        return real(block, theme_)

    monkeypatch.setattr(gate, "_refusal", recording)
    cache = typeset.ViewCache()
    parts = (Part(f"# Head\n\n```math\n{SUM}\n```\n", demote=2),)
    frame(lambda: typeset.document(cache, "doc", parts))
    assert asked and all(t is themes.demoted(2) for t in asked)
    asked.clear()
    handle = typeset.stream(cache, "reply")
    handle.append(f"```math\n{SUM}\n```\n")
    frame(lambda: handle.draw())
    assert asked and all(t is handle._source.theme for t in asked)


def test_a_formula_refused_only_at_a_larger_size_is_caught_when_the_size_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    No formula refused at one size and set at another was found (the result of
    task math-fence says what was searched), so the library's answer is stood in for
    by one that refuses above a size. The ask is made again when the size changes,
    and what it says then is what is drawn: the entry stays the library's.
    """
    small = _paragraph_px(themes.document_theme())
    real = gate._refusal

    def by_size(block: Any, theme_: Any) -> str | None:
        if _paragraph_px(theme_) > small * 1.2:
            return "too tall at this size"
        return real(block, theme_)

    monkeypatch.setattr(gate, "_refusal", by_size)
    source = f"```math\n{SUM}\n```\n"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    assert kinds(cache.view("doc")) == ["formula"]
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.5, panes={})
    for _ in range(2):
        frame(lambda: typeset.markdown(cache, "doc", source))
    assert kinds(cache.view("doc")) == ["warn", "code:math"]
    assert cache.texts("doc")[0] == "math not set: too tall at this size"
    assert cache._entries["doc"].unset is None


def test_the_battery_s_refusals_do_not_depend_on_size() -> None:
    """
    What the claim above rests on, for the refusals the probe found and the shapes
    nearest them: the same answer at every size from 8 to 40 px.
    """
    ts = gate._typesetter().module()
    sources = [
        SUM,
        ALIGNED,
        r"\sqrt{\frac{\frac{\frac{a}{b}}{c}}{\frac{d}{\frac{e}{f}}}}",
        r"\sqrt{\frac{\frac{a}{b}}{\frac{c}{d}}}",
        r"\left( \frac{\frac{\frac{a}{b}}{c}}{\frac{d}{\frac{e}{f}}} \right)",
        r"\hat{x}",
    ]
    for source in sources:
        answers = set()
        for px in range(8, 41, 4):
            style = gate._typesetter().base_style(themes.document_theme())
            style.EmSize = float(px)
            run = ts.Run(0, len(source), flags=int(ts.RunFlags.DisplayFormula))
            with ts.Paragraph() as paragraph:
                paragraph.set_text(source, [run])
                paragraph.layout(gate._ASKED_WIDTH_PX, style)
                answers.add(paragraph.formulas()[0].refused)
        assert len(answers) == 1, source


# -- theme, width and copy -------------------------------------------------------------


def test_a_theme_switch_re_inks_the_formula() -> None:
    cache = typeset.ViewCache()
    source = f"```math\n{SUM}\n```\n"
    frame(lambda: typeset.markdown(cache, "doc", source))
    dark = cache.view("doc").palette
    theme.set_theme("light")
    frame(lambda: typeset.markdown(cache, "doc", source))
    light = cache.view("doc").palette
    assert light == themes.palette() and light != dark
    assert light.ink != dark.ink
    assert len(formulas(cache.view("doc"))) == 1


def test_a_formula_wider_than_the_measure_is_reached_by_the_window() -> None:
    """
    The library sets a display formula past the measure and does not break it; the
    window is told how far it reaches, so it scrolls to it. Short words, so nothing
    but the block being a formula asks for the reach.
    """
    wide = " + ".join(f"a_{i}" for i in range(1, 9)) + " = b"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", f"```math\n{wide}\n```\n"), width=200.0)
    frame(lambda: typeset.markdown(cache, "doc", f"```math\n{wide}\n```\n"), width=200.0)
    (segment,) = cache._entries["doc"].segments
    assert segment.reach is not None
    _, measure, reach = segment.reach
    assert reach > measure


def test_select_all_copies_each_formula_as_its_fence() -> None:
    source = f"Before $PATH.\n\n```math\n{SUM}\n```\n\nAfter.\n"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    view = cache.view("doc")
    assert view.copy_as is views._formula_copy
    view.select_all()
    assert cache.selected("doc") == f"Before $PATH.\n\n```math\n{SUM}\n```\n\nAfter."


def test_a_multi_line_body_copies_as_written() -> None:
    body = "a\n  + b\n  + c"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", f"```math\n{body}\n```\n"))
    view = cache.view("doc")
    view.select(((0, 0), (0, len(view.texts[0]))))
    assert cache.selected("doc") == f"```math\n{body}\n```"


def test_a_selection_that_starts_past_a_formula_copies_as_the_library_does() -> None:
    source = f"```math\n{SUM}\n```\n\nAfter.\n"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    view = cache.view("doc")
    view.select(((1, 0), (1, 5)))
    assert cache.selected("doc") == view.selected_text() == "After"


def test_a_stream_copies_its_formula_as_its_fence() -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(f"```math\n{SUM}\n```\n\nAfter.\n")
    frame(lambda: handle.draw())
    handle.view.select_all()
    assert cache.selected("reply") == f"```math\n{SUM}\n```\n\nAfter."
    # The block stands for the stream's own fence, so the source map is the fence's.
    assert handle.view.selected_source() == f"```math\n{SUM}\n```\n\nAfter.\n"


# -- the wiring ------------------------------------------------------------------------


def test_every_markdown_view_is_given_the_formula_copy() -> None:
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", "Plain.\n"))
    assert cache.view("doc").copy_as is views._formula_copy
    handle = typeset.stream(cache, "reply")
    handle.append("Plain.\n")
    frame(lambda: handle.draw())
    assert handle.view.copy_as is views._formula_copy
    cache.view("doc").select_all()
    assert cache.selected("doc") == "Plain."


def test_a_formula_block_reads_without_language_through_captioned() -> None:
    """
    A formula block has no ``language``, and ``_Captioned.blocks`` reads every
    fence's answer: a math fence in a stream is set as a formula, with no defect
    logged and the stream not answered raw.
    """
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(f"```math\n{SUM}\n```\n")
    frame(lambda: handle.draw())
    assert gate.can_draw()
    assert not hasattr(formulas(handle.view)[0], "language")
    assert handle._captioned is not None and not handle._captioned.raw


# -- pptmstr's own code inside a call into the library --------------------------------


def _errors(monkeypatch: pytest.MonkeyPatch) -> Callable[[], list[str]]:
    """
    What is logged at ERROR from here on, every defect message new to the gate.
    """
    from pptmstr.log import LOG, Level

    monkeypatch.setattr(gate, "_DEFECTS_SAID", set())
    _, start = LOG.snapshot()

    def read() -> list[str]:
        entries, now = LOG.snapshot()
        fresh = entries[len(entries) - (now - start) :] if now > start else []
        return [entry.text for entry in fresh if entry.level is Level.ERROR]

    return read


def _library_on() -> bool:
    return gate._GATE.failure is None and gate._GATE.stopping is None and gate.can_draw()


def test_a_raising_captioned_stream_is_its_own_reply_s_code_and_no_other_pane_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The library reads a stream's blocks from inside ``view.sync()``; a defect in
    pptmstr's captioning there was the library stopping in every pane. Now that
    stream draws its fences as the library read them, says so, and is logged.
    """
    errors = _errors(monkeypatch)

    def broken(self: Any) -> Any:
        raise AttributeError("a defect in pptmstr's captioning")

    said: list[str] = []
    real_said = views._said

    def recording(im: Any, text: str, ink: Any = None) -> float:
        said.append(text)
        return real_said(im, text, ink)

    monkeypatch.setattr(views._Captioned, "_blocks", broken)
    monkeypatch.setattr(views, "_said", recording)
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(f"Intro.\n\n```math\n{SUM}\n```\n\nAfter.\n")
    other = f"Another pane.\n\n```math\n{SUM}\n```\n"

    def both() -> None:
        handle.draw()
        typeset.markdown(cache, "doc", other)

    for _ in range(3):
        frame(both)
        assert _library_on()
    assert len(formulas(cache.view("doc"))) == 1, "the other pane still sets its formula"
    assert formulas(handle.view) == [] and handle.unbuilt == 0
    assert views._RAW_STREAM in said
    (error,) = errors()
    assert error.startswith(
        "pptmstr's own code raised AttributeError while captioning a stream's blocks"
    )
    assert "Traceback" in error


def test_a_raising_pre_check_makes_that_formula_code_and_leaves_the_library_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = _errors(monkeypatch)

    def broken(first: Any, error: str) -> str | None:
        raise ValueError("a defect in the pre-check")

    monkeypatch.setattr(gate, "_refusal_said", broken)
    source = f"Before.\n\n```math\n{SUM}\n```\n"
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(source)

    def panes() -> None:
        typeset.markdown(cache, "doc", source)
        handle.draw()
        typeset.markdown(cache, "plain", "Plain *text*.\n")

    for _ in range(3):
        frame(panes)
        assert _library_on()
    assert kinds(cache.view("doc")) == ["text", "warn", "code:math"]
    assert cache.texts("doc")[1] == f"math not set: {gate.OWN_ERROR}"
    assert kinds(handle.view) == ["text", "warn", "code:math"]
    assert cache._entries["doc"].unset is None
    assert cache.view("plain") is not None
    (error,) = errors()
    assert "ValueError while asking whether a formula will be set" in error
    assert "Traceback" in error


def test_a_raising_copy_gives_the_library_s_copy_and_leaves_the_library_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = _errors(monkeypatch)
    source = f"Before.\n\n```math\n{SUM}\n```\n"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    view = cache.view("doc")
    view.select_all()

    def broken(block: Any) -> bool:
        raise IndexError("a defect in the copy")

    with monkeypatch.context() as patched:
        patched.setattr(views, "_is_formula", broken)
        copied = cache.selected("doc")
    assert copied == view.selected_text() != ""
    frame(lambda: (typeset.markdown(cache, "doc", source), typeset.markdown(cache, "o", "O.\n")))
    assert _library_on()
    assert cache.view("o") is not None
    (error,) = errors()
    assert "IndexError while copying a selection" in error and "Traceback" in error


def _stops_the_library(doing: str) -> None:
    """
    The library stopped for what it raised while ``doing``: from the next frame it
    is off, and its sentence names the call. A load of the fonts brings it back.
    """
    typeset.pre_frame()
    assert not typeset.can_draw()
    reason = typeset.unavailable_reason() or ""
    assert f"the library raised RuntimeError while {doing}" in reason
    assert typeset.load_fonts(BOLD), "a load is the way back"


def test_a_raising_native_call_in_the_pre_check_stops_the_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The pre-check's calls into the native module are the library's: what one of
    them raises stops the library, as a call into it does anywhere, and is not
    taken for pptmstr's defect.
    """
    errors = _errors(monkeypatch)
    native = gate._typesetter().module()

    def broken(*given: Any) -> Any:
        raise RuntimeError("the native module failed")

    with monkeypatch.context() as patched:
        patched.setattr(native, "Paragraph", broken)
        frame(lambda: typeset.markdown(typeset.ViewCache(), "doc", f"```math\n{SUM}\n```\n"))
    assert not any("pptmstr's own code" in error for error in errors())
    _stops_the_library("asking whether a formula will be set")


@pytest.mark.parametrize("call", ["module", "reason"])
def test_a_raising_typesetter_call_in_the_pre_check_stops_the_library(
    monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    """
    ``typesetter.module()`` and ``typesetter.reason()`` are imscribe's, and the
    pre-check asks them: what they raise is the library's, not pptmstr's defect.
    """
    from imscribe import TypesetUnavailable

    errors = _errors(monkeypatch)
    typesetter = gate._typesetter()

    def broken(*given: Any) -> Any:
        raise RuntimeError("the typesetter failed")

    with monkeypatch.context() as patched:
        if call == "reason":
            patched.setattr(typesetter, "module", lambda: None)
        patched.setattr(typesetter, call, broken)
        with pytest.raises(TypesetUnavailable):
            gate._refusal(object(), themes.document_theme())
    assert not any("pptmstr's own code" in error for error in errors())
    _stops_the_library("asking whether a formula will be set")


def test_a_raising_library_read_in_the_copy_stops_the_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The copy's reads of the view and its document are calls into the library:
    what one raises stops the library, the copy is empty, and the view is not
    asked again for its plain copy.
    """
    errors = _errors(monkeypatch)
    source = f"Before.\n\n```math\n{SUM}\n```\n\nAfter.\n"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    view = cache.view("doc")
    view.select_all()
    plain: list[int] = []
    document = view.native_document

    class Broken:
        def range_text(self, *given: Any) -> str:
            raise RuntimeError("the native document failed")

    with monkeypatch.context() as patched:
        patched.setattr(type(view), "native_document", property(lambda self: Broken()))
        real = view.selected_text
        patched.setattr(view, "selected_text", lambda: plain.append(1) or real())
        assert cache.selected("doc") == ""
    assert plain == [], "a library failure does not fall back to the plain copy"
    assert view.native_document is document
    assert not any("pptmstr's own code" in error for error in errors())
    _stops_the_library("copying a selection")


def test_a_copy_from_a_view_with_no_document_is_the_library_s_and_leaves_it_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A view whose document was let go of (``close``) keeps its selection, and
    ``native_document`` is then ``None``. That is pptmstr's state, not the library
    failing: the copy is the library's own, which makes the document again, and
    the library stays on for every pane.
    """
    errors = _errors(monkeypatch)
    source = f"Before.\n\n```math\n{SUM}\n```\n\nAfter.\n"
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    view = cache.view("doc")
    view.select_all()
    view.close()
    assert view.native_document is None and view.selection is not None
    assert cache.selected("doc") == view.selected_text() != ""
    frame(lambda: typeset.markdown(cache, "other", "Other.\n"))
    assert _library_on()
    assert errors() == []


def test_the_formula_check_skips_a_view_with_no_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = _errors(monkeypatch)
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", f"```math\n{SUM}\n```\n"))
    cache.view("doc").close()
    assert views._refused_formulas(cache._entries["doc"]) is False
    frame(lambda: typeset.markdown(cache, "other", "Other.\n"))
    assert _library_on()
    assert errors() == []


def test_a_raising_guard_sends_its_entry_alone_to_imgui(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = _errors(monkeypatch)
    monkeypatch.setattr(gate, "_refusal", lambda block, theme_: None)

    def broken(entry: Any) -> bool:
        raise KeyError("a defect in the guard")

    monkeypatch.setattr(views, "_refused_formulas", broken)
    cache = typeset.ViewCache()
    for _ in range(2):
        frame(
            lambda: (
                typeset.markdown(cache, "doc", f"```math\n{ALIGNED}\n```\n"),
                typeset.markdown(cache, "other", "Other.\n"),
            )
        )
    assert _library_on()
    assert cache._entries["doc"].unset is not None
    assert cache._entries["other"].unset is None
    (error,) = errors()
    assert "KeyError while reading which formulas a view refused" in error


# -- copy: a selection touching a formula gives the whole fence -------------------------


def test_a_selection_that_starts_inside_a_formula_copies_its_whole_fence() -> None:
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", f"```math\n{SUM}\n```\n\nAfter.\n"))
    view = cache.view("doc")
    view.select(((0, 3), (1, 5)))
    assert cache.selected("doc") == f"```math\n{SUM}\n```\n\nAfter"


def test_a_selection_that_ends_inside_a_formula_copies_its_whole_fence() -> None:
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", f"Before.\n\n```math\n{SUM}\n```\n"))
    view = cache.view("doc")
    view.select(((0, 2), (1, 3)))
    assert cache.selected("doc") == f"fore.\n\n```math\n{SUM}\n```"


def test_a_selection_inside_one_formula_copies_its_whole_fence() -> None:
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", f"```math\n{SUM}\n```\n"))
    cache.view("doc").select(((0, 2), (0, 6)))
    assert cache.selected("doc") == f"```math\n{SUM}\n```"


# -- the guard cannot loop -------------------------------------------------------------


def test_a_refusal_after_the_ask_outlives_any_number_of_other_formulas_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A refusal the view made is held apart from the asking's answers, so reading
    more formulas than those hold cannot evict it and send the fence back to the
    view to be refused again.
    """
    monkeypatch.setattr(gate, "_refusal", lambda block, theme_: None)
    source = f"```math\n{ALIGNED}\n```\n"
    cache = typeset.ViewCache()
    for _ in range(2):
        frame(lambda: typeset.markdown(cache, "doc", source))
    assert kinds(cache.view("doc")) == ["warn", "code:math"]
    theme_ = themes.document_theme()
    for n in range(gate._FORMULAS_KEPT + 50):
        gate.formula(f"x_{{{n}}}", theme_)
    block, why = gate.formula(ALIGNED, theme_)
    assert block is None and why


def test_a_formula_in_a_view_carries_the_theme_its_view_was_made_in() -> None:
    """
    ``_Formula.theme`` does not count in its equality, so a view handed an equal
    block keeps the one it had. That cannot carry an old theme: a theme is made
    once an epoch, and a change of epoch makes every view again.
    """
    cache = typeset.ViewCache()
    source = f"```math\n{SUM}\n```\n"
    handle = typeset.stream(cache, "reply")
    handle.append(source)
    frame(lambda: (typeset.markdown(cache, "doc", source), handle.draw()))
    before = formulas(cache.view("doc"))[0].theme
    assert before is themes.document_theme()
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.5, panes={})
    for _ in range(2):
        frame(lambda: (typeset.markdown(cache, "doc", source), handle.draw()))
    after = formulas(cache.view("doc"))[0].theme
    assert after is themes.document_theme() and after is not before
    assert formulas(handle.view)[0].theme is handle._source.theme


# -- the size a formula is set at ------------------------------------------------------

TALL = r"\sum_{k=1}^{n} \frac{\sum_{j=1}^{k} a_j}{\frac{b_k}{c_k}}"


def test_the_scale_is_two_stepped_down_for_width_and_height_and_never_below_one() -> None:
    line = 17.0
    assert views.formula_scale((100.0, 20.0, line), None) == views.FORMULA_SCALE == 2.0
    assert views.formula_scale((100.0, 20.0, line), 1000.0) == 2.0
    # Fits at 1.5 exactly, and at 1.4 rounds down to a step that still fits.
    assert views.formula_scale((100.0, 20.0, line), 150.0) == 1.5
    assert views.formula_scale((100.0, 20.0, line), 140.0) == 1.375
    assert 1.375 * 100.0 <= 140.0
    # Wider than the measure at 1: today's size, which scrolls as it does today.
    assert views.formula_scale((300.0, 20.0, line), 200.0) == 1.0
    # Taller than the cap at 2, and taller than the cap at 1.
    tall = views.FORMULA_LINES * line / 1.6
    assert views.formula_scale((50.0, tall, line), None) == 1.5
    assert views.formula_scale((50.0, views.FORMULA_LINES * line * 2, line), None) == 1.0
    # Not measured: today's size.
    assert views.formula_scale((0.0, 0.0, 0.0), 1000.0) == 1.0


def _drawn(view: Any) -> list[tuple[Any, float, float]]:
    """
    Each formula of the view, with how far right and how tall its set lines are.
    """
    out = []
    for at, block in enumerate(view.blocks):
        if views._is_formula(block):
            lines = view.set_lines(at, at + 1)
            right = max(float(line.right) for line in lines)
            out.append(
                (
                    block,
                    right,
                    max(float(line.bottom) for line in lines)
                    - min(float(line.top) for line in lines),
                )
            )
    return out


def test_a_short_formula_is_set_at_twice_today_s_size() -> None:
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", f"Before.\n\n```math\n{SUM}\n```\n"))
    ((block, _, tall),) = _drawn(cache.view("doc"))
    assert block.scale == 2.0
    assert all(run.scale == 2.0 for run in block.runs)
    wide_1, tall_1, _ = block.size
    assert tall == pytest.approx(2.0 * tall_1, rel=0.05)


def test_a_formula_wider_than_the_measure_at_two_steps_down_to_fit_and_back_up() -> None:
    """
    The window narrows past the formula's width at 2: it is set again at the largest
    step that fits, the part is not read again, and it never runs past the measure.
    Wider again, it is 2 again.
    """
    source = f"Before.\n\n```math\n{SUM}\n```\n"
    cache = typeset.ViewCache()
    parts = (Part(source),)
    frame(lambda: typeset.document(cache, "doc", parts), width=700.0)
    entry = cache._entries["doc"]
    read = dict(entry.read)
    ((block, _, _),) = _drawn(cache.view("doc"))
    wide_1 = block.size[0]
    assert block.scale == 2.0
    narrow = 1.5 * wide_1 + 40.0
    frame(lambda: typeset.document(cache, "doc", parts), width=narrow)
    ((block, right, _),) = _drawn(cache.view("doc"))
    measure = entry.measure
    assert measure is not None and 1.0 < block.scale < 2.0
    assert block.scale * wide_1 <= measure
    assert right <= measure + 0.5
    assert entry.read.keys() == read.keys()
    assert all(entry.read[key] is read[key] for key in read), "the part was read again"
    assert cache.unbuilt("doc") == 0
    frame(lambda: typeset.document(cache, "doc", parts), width=700.0)
    ((block, _, _),) = _drawn(cache.view("doc"))
    assert block.scale == 2.0


def test_a_formula_wider_than_the_measure_at_one_is_set_at_one_and_reaches_as_today() -> None:
    wide = " + ".join(f"a_{i}" for i in range(1, 9)) + " = b"
    source = f"```math\n{wide}\n```\n"
    cache = typeset.ViewCache()
    for _ in range(2):
        frame(lambda: typeset.markdown(cache, "doc", source), width=200.0)
    ((block, right, _),) = _drawn(cache.view("doc"))
    assert block.scale == 1.0
    (segment,) = cache._entries["doc"].segments
    assert segment.reach is not None
    assert segment.reach[2] == pytest.approx(block.size[0], abs=1.0)
    assert right == pytest.approx(block.size[0], abs=1.0)


def test_a_tall_formula_is_capped_in_height() -> None:
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", f"```math\n{TALL}\n```\n"))
    ((block, _, tall),) = _drawn(cache.view("doc"))
    _, tall_1, line = block.size
    assert tall_1 * 2.0 > views.FORMULA_LINES * line, "the formula is not tall enough to cap"
    assert 1.0 < block.scale < 2.0
    assert block.scale * tall_1 <= views.FORMULA_LINES * line
    assert tall <= views.FORMULA_LINES * line + 2.0


def test_the_pre_check_is_asked_at_the_size_drawn(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[float] = []
    real = gate._refusal

    def recording(block: Any, theme_: Any) -> str | None:
        asked.append(block.runs[0].scale)
        return real(block, theme_)

    monkeypatch.setattr(gate, "_refusal", recording)
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", f"```math\n{SUM}\n```\n"))
    ((block, _, _),) = _drawn(cache.view("doc"))
    assert block.scale == 2.0 and block.runs[0].scale in asked


def test_a_formula_the_pre_check_refuses_at_two_is_set_at_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    No formula is known that the library refuses at 2 and sets at 1, so the library's
    answer is stood in for. The formula is then set at 1, which was asked, and is not
    code; the entry stays the library's.
    """
    real = gate._refusal

    def above_one(block: Any, theme_: Any) -> str | None:
        if any(run.scale > 1.0 for run in block.runs):
            return "too large at this size"
        return real(block, theme_)

    monkeypatch.setattr(gate, "_refusal", above_one)
    cache = typeset.ViewCache()
    for _ in range(2):
        frame(lambda: typeset.markdown(cache, "doc", f"```math\n{SUM}\n```\n"))
    assert kinds(cache.view("doc")) == ["formula"]
    ((block, _, _),) = _drawn(cache.view("doc"))
    assert block.scale == 1.0
    assert cache._entries["doc"].unset is None and cache.unbuilt("doc") == 0


def test_the_guard_reads_the_formula_at_the_size_drawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    With the ask standing aside, the view meets the refused formula at 2, the size it
    was handed, and the guard keeps it: the fence is code, at every size.
    """
    monkeypatch.setattr(gate, "_refusal", lambda block, theme_: None)
    kept: list[float] = []
    real = gate.refused

    def recording(body: str, theme_: Any, reason: str) -> None:
        view = cache.view("doc")
        kept.extend(b.scale for b in view.blocks if views._is_formula(b) and b.body == body)
        real(body, theme_, reason)

    monkeypatch.setattr(gate, "refused", recording)
    cache = typeset.ViewCache()
    for _ in range(2):
        frame(lambda: typeset.markdown(cache, "doc", f"```math\n{ALIGNED}\n```\n"))
    assert kept == [2.0]
    assert kinds(cache.view("doc")) == ["warn", "code:math"]
    assert gate.formula(ALIGNED, themes.document_theme(), 1.0)[0] is None


def test_a_stream_sizes_its_formula_and_resizes_it_with_the_measure() -> None:
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "reply")
    handle.append(f"Before.\n\n```math\n{SUM}\n```\n")
    frame(lambda: handle.draw(), width=700.0)
    ((block, _, _),) = _drawn(handle.view)
    assert block.scale == 2.0
    narrow = 1.5 * block.size[0] + 40.0
    frame(lambda: handle.draw(), width=narrow)
    ((block, right, _),) = _drawn(handle.view)
    assert 1.0 < block.scale < 2.0 and right <= narrow + 0.5
    assert handle.unbuilt == 0


def test_prose_code_and_mermaid_blocks_are_not_resized() -> None:
    """
    Every block that is not a formula is handed to the view as it was read: the
    same object, so nothing about it changed.
    """
    source = (
        f"Price $PATH.\n\n```python\nx = 1\n```\n\n```mermaid\ngraph TD; A-->B\n```\n\n"
        f"```math\n{SUM}\n```\n"
    )
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    entry = cache._entries["doc"]
    (read,) = entry.read.values()
    handed = cache.view("doc").blocks
    assert len(read) == len(handed)
    for was, now in zip(read, handed, strict=True):
        if views._is_formula(now):
            assert now.scale == 2.0 and was.scale == 1.0
        else:
            assert now is was
            assert all(run.scale == 0.0 for run in getattr(now, "runs", ()))


# -- the space around a formula -----------------------------------------------------


def test_a_formula_s_air_is_half_a_line_at_one_and_grows_with_its_scale() -> None:
    from imscribe import BlockKind

    paragraph = themes.document_theme().blocks[BlockKind.PARAGRAPH]
    at_1 = views.formula_style(paragraph, 1.0)
    at_2 = views.formula_style(paragraph, 2.0)
    assert at_1.space_above == round(0.5 * paragraph.leading)
    assert at_2.space_above == round(0.75 * paragraph.leading)
    for style in (at_1, at_2):
        assert style.space_below == paragraph.space_below + style.space_above
        assert (style.font, style.leading, style.align, style.left_indent) == (
            paragraph.font,
            paragraph.leading,
            paragraph.align,
            paragraph.left_indent,
        )


def test_a_drawn_formula_carries_its_air_and_prose_keeps_the_theme_s_style() -> None:
    from imscribe import BlockKind

    cache = typeset.ViewCache()
    source = f"Before.\n\n```math\n{SUM}\n```\n\nAfter.\n"
    frame(lambda: typeset.markdown(cache, "doc", source))
    view = cache.view("doc")
    paragraph = themes.document_theme().blocks[BlockKind.PARAGRAPH]
    (block,) = formulas(view)
    assert block.scale == 2.0
    assert block.style.space_above == round(0.75 * block.style.leading) > 0
    for other in view.blocks:
        if not views._is_formula(other):
            assert other.style == paragraph


def _gaps(source: str) -> tuple[float, float, float]:
    """
    How far the formula's set lines stand from the prose line over it and the one
    under it, and the height claimed on the first frame and on the second.
    """
    cache = typeset.ViewCache()
    frame(lambda: typeset.markdown(cache, "doc", source))
    first = cache.height("doc")
    frame(lambda: typeset.markdown(cache, "doc", source))
    assert cache.height("doc") == first, "the height changed after the first draw"
    view = cache.view("doc")
    (at,) = [i for i, block in enumerate(view.blocks) if views._is_formula(block)]
    above = max(float(line.bottom) for line in view.set_lines(at - 1, at))
    lines = view.set_lines(at, at + 1)
    top = min(float(line.top) for line in lines)
    bottom = max(float(line.bottom) for line in lines)
    below = min(float(line.top) for line in view.set_lines(at + 1, at + 2))
    assert first is not None
    return top - above, below - bottom, first


def test_the_air_is_on_screen_either_side_and_counted_before_the_first_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The same document with no air and with it: the formula stands its air further
    from the prose on each side, and the height claimed on the first frame, which
    the second keeps, is taller by both.
    """
    from imscribe import px_from_sp

    source = f"Before.\n\n```math\n{SUM}\n```\n\nAfter.\n"
    monkeypatch.setattr(views, "FORMULA_AIR", 0.0)
    monkeypatch.setattr(views, "FORMULA_AIR_PER_SCALE", 0.0)
    over_0, under_0, tall_0 = _gaps(source)
    monkeypatch.undo()
    monkeypatch.setattr(views, "_SIZED", {})
    over, under, tall = _gaps(source)
    theme_ = themes.document_theme()
    from imscribe import BlockKind

    leading = theme_.blocks[BlockKind.PARAGRAPH].leading
    air = px_from_sp(round(0.75 * leading), theme_.px_per_inch)
    assert air > 5.0
    assert over - over_0 == pytest.approx(air, abs=1.0)
    assert under - under_0 == pytest.approx(air, abs=1.0)
    assert tall - tall_0 == pytest.approx(2 * air, abs=2.0)
