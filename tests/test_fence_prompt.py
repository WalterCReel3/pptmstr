"""
The table of drawn fences (``figures.fences``): the dispatcher and the session
prompt read the same rows, a row whose renderer is missing is not promised, and
every launch carries the text through ``AgentSession._system_prompt``.

The prompt's TeX lists are held to the library here too: a construct it tells
Claude to use is one ``gate.formula`` sets, and one it calls refused is refused.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from pptmstr.bridge import Bridge
from pptmstr.driver import AgentSession
from pptmstr.figures import fences, host, mermaid, sanitise
from pptmstr.templates import FEATURE, lead_briefing, worker_prompt
from pptmstr.ui.typeset import views

RESUMED = "11111111-2222-3333-4444-555555555555"

# Info strings the dispatcher is asked about: every row as written and shouted,
# with an attribute after it, and words that are near a row and are not one.
NOT_ROWS = ("", "python", "svgx", "maths", "mermaid-js", "latex", "tex", "dot", "diff")


def _candidates() -> list[str]:
    out = list(NOT_ROWS)
    for row in fences.RENDERED:
        out += [row.word, row.word.upper(), f"{row.word} title=x"]
    return out


# -- one table ---------------------------------------------------------------------


def test_the_dispatcher_settles_exactly_the_tables_languages() -> None:
    settled = {fences.word(said) for said in _candidates() if views.settles_rendered(said)}
    assert settled == {row.word for row in fences.RENDERED}
    for said in _candidates():
        kind = next((r.kind for r in fences.RENDERED if r.word == fences.word(said)), None)
        assert host.is_figure_fence(said) == (kind == "figure"), said
        assert views.is_math_fence(said) == (kind == "formula"), said


def test_the_dispatcher_reads_the_table_and_not_a_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A word put in the table is settled by the dispatcher at once: what makes "one
    table" a property of the wiring and not of two lists that agree today.
    """
    assert not views.settles_rendered("dot") and not views.settles_rendered("tex")
    monkeypatch.setattr(fences, "FIGURE_WORDS", fences.FIGURE_WORDS | {"dot"})
    monkeypatch.setattr(fences, "FORMULA_WORDS", fences.FORMULA_WORDS | {"tex"})
    assert host.is_figure_fence("dot") and views.settles_rendered("dot")
    assert views.is_math_fence("tex") and views.settles_rendered("tex")


def test_the_word_sets_are_the_rows() -> None:
    assert fences.WORDS == {row.word for row in fences.RENDERED}
    assert len(fences.WORDS) == len(fences.RENDERED), "a word is in one row"
    assert all(row.word == row.word.lower() for row in fences.RENDERED)


# -- what is promised --------------------------------------------------------------


def _missing(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    real = fences._present
    monkeypatch.setattr(fences, "_present", lambda name: name not in names and real(name))


def test_with_everything_installed_every_row_is_named() -> None:
    text = fences.session_prompt()
    if fences.available() != fences.RENDERED:
        pytest.skip(f"not every renderer is installed: {fences.available()}")
    for row in fences.RENDERED:
        assert f"```{row.word}" in text
        assert row.says in text


@pytest.mark.parametrize("row", fences.RENDERED, ids=lambda row: row.word)
def test_a_row_whose_renderer_is_missing_is_not_named(
    row: fences.Fence, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = row.needs[-1] if row.needs else "imtypeset"
    _missing(monkeypatch, gone)
    named = fences.available()
    assert row not in named
    text = fences.session_prompt()
    assert f"```{row.word}" not in text
    for other in named:
        assert f"```{other.word}" in text and other.says in text


def test_mermaid_is_promised_by_the_read_mark_refuses_by(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    No merman: the prompt leaves mermaid out, and a mermaid fence is code with the
    extra's reason. Both from ``mermaid.version()``, so they cannot disagree.
    """
    monkeypatch.setattr(mermaid, "_VERSION", [None])
    monkeypatch.setattr(host, "_MARKS", type(host._MARKS)())
    assert "mermaid" not in fences.session_prompt()
    refused = host.mark("mermaid", "flowchart LR\n a --> b\n")
    assert isinstance(refused, sanitise.Refused) and refused.reason == mermaid.EXTRA_REASON

    monkeypatch.setattr(mermaid, "_VERSION", ["0.9.1"])
    others = ("imscribe", "imtypeset", *fences._RASTER)
    assert ("```mermaid" in fences.session_prompt()) == all(map(fences._installed, others))


def test_without_the_library_nothing_is_promised(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Every fence is drawn inside a typeset view, so no typeset library is no rows,
    and no text at all.
    """
    _missing(monkeypatch, "imscribe")
    assert fences.available() == ()
    assert fences.session_prompt() == ""


def test_math_missing_drops_the_pointer_to_a_math_fence() -> None:
    figures_only = tuple(row for row in fences.RENDERED if row.kind == "figure")
    text = fences.prompt(figures_only)
    assert "```math" not in text
    assert "$...$" in text, "prose $ is still said not to render"


def test_reading_what_is_installed_imports_no_renderer() -> None:
    """
    Run fresh, so nothing an earlier test imported is counted: ``available`` reads
    metadata and imports neither merman nor resvg.
    """
    code = (
        "import sys\n"
        "from pptmstr.figures import fences\n"
        "fences.session_prompt()\n"
        "names = ('merman', 'resvg_py', 'imscribe', 'imtypeset')\n"
        "print(sorted(m for m in names if m in sys.modules))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert done.stdout.strip() == "[]"


def test_the_text_says_what_the_task_asks_and_stays_short() -> None:
    text = fences.prompt(fences.RENDERED)
    assert len(text) <= fences.PROMPT_LIMIT, len(text)
    assert "When pptmstr typesets replies, the pane showing them draws" in text
    assert "otherwise they show as written" in text
    assert "No other fence is drawn as a figure or a formula." in text
    assert "Any other fence shows as code" not in text, "an honest diff fence is a diff view"
    assert "$...$ and $$...$$ in prose are not rendered" in text
    assert "shown as its source under a one-line reason" in text
    assert "End every font-family with serif, sans-serif or monospace." in text
    assert "<script>" in text and "<image>" in text
    assert fences.prompt(()) == ""


MERMAID_FIRST = "use ```mermaid, which lays them out"


def test_the_guidance_says_the_pane_is_the_display_and_how_to_design_for_it() -> None:
    text = fences.prompt(fences.RENDERED)
    assert "the pane is the display" in text
    assert "near-white card" in text and "literal colours" in text
    assert f"at most about {fences.FIGURE_WIDTH} px wide" in text
    assert "win over a skill's general advice" in text
    assert MERMAID_FIRST in text and "Never compute a graph layout by hand" in text


@pytest.mark.parametrize(
    "words", [("svg", "math"), ("svg",), ("math",)], ids=["svg+math", "svg", "math"]
)
def test_without_the_mermaid_row_the_text_says_nothing_of_mermaid(words: tuple[str, ...]) -> None:
    rows = tuple(row for row in fences.RENDERED if row.word in words)
    text = fences.prompt(rows)
    assert "mermaid" not in text.lower()
    for row in rows:
        assert f"```{row.word}" in text and row.says in text
    assert len(text) <= fences.PROMPT_LIMIT
    assert ("near-white card" in text) == ("svg" in words), "card advice is for figures"
    assert "the pane is the display" in text


def test_the_mermaid_preference_follows_the_installed_mermaid_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mermaid, "_VERSION", [None])
    assert MERMAID_FIRST not in fences.session_prompt()
    monkeypatch.setattr(mermaid, "_VERSION", ["0.9.1"])
    with_it = fences.session_prompt()
    has_row = any(row.word == "mermaid" for row in fences.available())
    assert (MERMAID_FIRST in with_it) == has_row


# -- every launch carries it -------------------------------------------------------


def _append(session: AgentSession) -> tuple[str, Any]:
    prompt = session._system_prompt()
    assert isinstance(prompt, dict), prompt
    assert prompt["type"] == "preset"
    return str(prompt["append"]), prompt.get("snapshot", "omitted")


@pytest.fixture
def text() -> str:
    made = fences.session_prompt()
    if not made:
        pytest.skip("no fence renderer is installed here")
    return made


def test_a_fresh_solo_session_carries_the_text(text: str) -> None:
    append, snapshot = _append(AgentSession(Bridge(), task="t"))
    assert append == text
    assert snapshot == "omitted"


def test_a_team_session_carries_the_briefing_then_the_text(text: str) -> None:
    session = AgentSession(Bridge(), task="t", template=FEATURE)
    append, snapshot = _append(session)
    assert append == f"{lead_briefing(FEATURE, session.policy)}\n\n{text}"
    assert snapshot == "omitted"


def test_every_worker_is_told_what_the_pane_draws(text: str) -> None:
    """
    A worker's output is drawn in the same panes, and a sub-agent does not read the
    lead's system prompt: its own definition carries the text, after its role's.
    """
    session = AgentSession(Bridge(), task="t", template=FEATURE)
    team = session._team()
    assert team and set(team) == {role.name for role in FEATURE.roles}
    for role in FEATURE.roles:
        own = worker_prompt(role, session.brief, session.policy, session.contained)
        assert team[role.name].prompt == f"{own}\n\n{text}"


def test_a_worker_is_told_nothing_more_when_nothing_is_drawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fences, "session_prompt", lambda: "")
    session = AgentSession(Bridge(), task="t", template=FEATURE)
    team = session._team()
    assert team
    for role in FEATURE.roles:
        own = worker_prompt(role, session.brief, session.policy, session.contained)
        assert team[role.name].prompt == own


@pytest.mark.parametrize("template", [None, FEATURE], ids=["solo", "team"])
def test_a_resumed_session_carries_the_same_text_as_a_fresh_one(text: str, template: Any) -> None:
    extra = {} if template is None else {"template": template}
    fresh, _ = _append(AgentSession(Bridge(), task="t", **extra))
    resumed, snapshot = _append(AgentSession(Bridge(), task="t", resume=RESUMED, **extra))
    assert resumed == fresh
    assert resumed.endswith(text)
    assert snapshot is False


def test_with_nothing_drawn_a_solo_launch_has_no_append(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fences, "session_prompt", lambda: "")
    assert AgentSession(Bridge(), task="t")._system_prompt() is None
    resumed = AgentSession(Bridge(), task="t", resume=RESUMED)._system_prompt()
    assert resumed == {"type": "custom", "prompt": "", "snapshot": False}


# -- the text is true --------------------------------------------------------------

# One formula for each thing the prompt says to use, and each it says is refused.
SETS = (
    r"x_i^{n+1} + f'(x)",
    r"\frac{a}{b} + \binom{n}{k} + \sqrt{x}",
    r"\sum_{i=1}^{n} i + \prod_{k} k + \int_0^1 x + \lim_{x \to 0} x",
    r"\left( \frac{a}{b} \right)^2",
    r"\overline{z} + \underline{w}",
    r"\alpha + \omega + \Gamma \le \ge \ne \approx \in",
    r"a \to b \Rightarrow c \cdot d \times e \pm f",
    r"\text{if } x \mathrm{d}x + \mathbf{v} + \mathbb{R} + \mathcal{L} + \operatorname{tr} A",
    r"\sin x + \cos x + \log x + \exp x + \max x + \min x",
)
REFUSED = (
    r"\begin{aligned} a &= b \end{aligned}",
    r"\begin{cases} a \end{cases}",
    r"a \\ b",
    r"a & b",
    r"\hat{x}",
    r"\vec{x}",
    r"\bar{x}",
    r"\tilde{x}",
    r"\dot{x}",
    r"\displaystyle x",
    r"\dfrac{a}{b}",
    r"\tfrac{a}{b}",
    r"\mathit{x}",
    r"\boldsymbol{x}",
    r"\color{red} x",
    r"\big( x \big)",
    r"\sqrt[3]{x}",
    r"a \over b",
    r"n \choose k",
    r"\newcommand{\x}{y} \x",
    r"\def\x{y} \x",
)


@pytest.mark.parametrize("body", SETS)
def test_what_the_prompt_says_to_use_is_set(body: str, seam: Any) -> None:
    from pptmstr.ui.typeset import gate, themes

    block, why = gate.formula(body, themes.document_theme())
    assert block is not None, why


@pytest.mark.parametrize("body", REFUSED)
def test_what_the_prompt_calls_refused_is_refused(body: str, seam: Any) -> None:
    from pptmstr.ui.typeset import gate, themes

    block, why = gate.formula(body, themes.document_theme())
    assert block is None and why


def test_an_svg_written_as_the_prompt_asks_passes_the_sanitiser() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40">'
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#36c"/></linearGradient></defs>'
        '<rect width="120" height="40" fill="url(#g)"/>'
        '<text x="10" y="25" font-family="Inter, sans-serif">label</text></svg>'
    )
    out = sanitise.sanitise(svg)
    assert isinstance(out, sanitise.Sanitised), out
    assert 'font-family="Inter, sans-serif"' in out.svg
