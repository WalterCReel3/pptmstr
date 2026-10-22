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
    for said in ("math", "Math", "math display", "svg", "mermaid"):
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

    def broken(block: Any, theme_: Any) -> str | None:
        raise ValueError("a defect in the pre-check")

    monkeypatch.setattr(gate, "_refusal", broken)
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
