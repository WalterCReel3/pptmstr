"""
The tool-block probe: its cases are real ``ToolEntry`` records written the way the
driver writes them, and every string it draws has been through ``drawable``.

The drawing is exercised against a bare ImGui context (the ``imgui`` fixture) with
ImGui's text calls wrapped, so a verbatim field that reached one raw would fail
here rather than at draw time.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pptmstr import drawable, theme
from pptmstr.transcript import ToolEntry

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "tool_block_probe", ROOT / "scripts" / "tool_block_probe.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["tool_block_probe"] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


@pytest.fixture(scope="module")
def built() -> tuple[Any, dict[str, Any]]:
    transcript, cases = probe.build_cases()
    return transcript, {case.name: case for case in cases}


def test_every_case_is_the_entry_the_transcript_holds(built: tuple[Any, dict[str, Any]]) -> None:
    transcript, cases = built
    for case in cases.values():
        assert isinstance(case.entry, ToolEntry)
        assert transcript.tool_entry(case.entry.tool_use_id) is case.entry
        assert case.entry.call is not None and case.entry.input is not None
        # The call line is where tool_entries finds it, as a renderer would.
        start, end = case.entry.call
        assert case.entry in transcript.tool_entries(start, end)


def test_the_cases_cover_what_the_task_names(built: tuple[Any, dict[str, Any]]) -> None:
    _, cases = built
    short = cases["bash-short"].entry
    assert short.name == "Bash" and {"description", "timeout"} <= set(short.input)
    assert short.result is not None and not short.result.is_error
    assert len(cases["bash-long"].entry.result.output.splitlines()) > probe.STACKED.shown
    capped = cases["bash-capped"].entry.result
    assert capped.truncated and capped.dropped_bytes > 0
    assert cases["bash-error"].entry.result.is_error
    edit = cases["edit"].entry
    assert edit.name == "Edit" and {"old_string", "new_string"} <= set(edit.input)
    assert cases["read"].entry.name == "Read"
    fetch = cases["webfetch"].entry
    assert fetch.name == "WebFetch" and {"url", "prompt"} <= set(fetch.input)
    mcp = cases["mcp"].entry
    assert mcp.name.startswith("mcp__")
    # A list of content blocks is held as its repr (tool-entries' decision).
    assert mcp.result.output.startswith("[{'type': 'text'")
    assert cases["running"].entry.result is None and cases["running"].live
    assert cases["no-result"].entry.result is None and not cases["no-result"].live


def test_state_is_not_running_just_because_the_result_is_none(
    built: tuple[Any, dict[str, Any]],
) -> None:
    _, cases = built
    pending = cases["running"].entry
    assert probe.state_of(pending, live=True) == "running"
    assert probe.state_of(pending, live=False) == "no result"
    assert probe.state_of(cases["bash-error"].entry, live=False) == "error"
    assert probe.state_of(cases["bash-short"].entry, live=True) == "done"


@pytest.mark.parametrize("style", probe.STYLES, ids=lambda s: s.name)
def test_every_block_string_is_drawable(built: tuple[Any, dict[str, Any]], style: Any) -> None:
    _, cases = built
    for case in cases.values():
        block = probe.build(case.entry, case.live, style.shown, style.cut)
        for text in probe.block_strings(block):
            assert not drawable.undrawable(text), (case.name, text)
    # The verbatim surrogate and NUL arrive written out, not dropped.
    short = probe.build(cases["bash-short"].entry, False, style.shown, style.cut)
    read = probe.build(cases["read"].entry, False, 99, "head")
    assert any("caf\\udc80" in line for line in short.lines)
    assert any("\\u0000" in line for line in read.lines)
    assert "\udc80" in cases["bash-short"].entry.result.output  # the entry stays verbatim


@pytest.mark.parametrize("cut", ["head", "tail"])
def test_hidden_lines_are_counted_from_the_entry(
    built: tuple[Any, dict[str, Any]], cut: str
) -> None:
    _, cases = built
    entry = cases["bash-long"].entry
    total = len(entry.result.output.splitlines())
    block = probe.build(entry, False, 4, cut)
    assert block.total == total and len(block.lines) == 4 and block.hidden == total - 4
    lines = entry.result.output.splitlines()
    assert block.lines == tuple(lines[:4] if cut == "head" else lines[-4:])
    assert block.more_label.startswith(f"{total - 4:,} {'more' if cut == 'head' else 'earlier'}")


def test_the_cap_is_said_and_the_exit_code_read(built: tuple[Any, dict[str, Any]]) -> None:
    _, cases = built
    capped = probe.build(cases["bash-capped"].entry, False, 6)
    assert f"{cases['bash-capped'].entry.result.dropped_bytes:,} B past the 1 MiB cap" in (
        capped.more_label
    )
    error = probe.build(cases["bash-error"].entry, False, 6)
    assert error.badge == "error · exit 1"
    assert not error.lines[0].startswith("Exit code")


def test_edit_draws_a_diff_of_its_strings(built: tuple[Any, dict[str, Any]]) -> None:
    _, cases = built
    block = probe.build(cases["edit"].entry, False, 6)
    assert [ink for ink, _, _ in block.intro] == ["add", "add"]
    assert block.intro[0][1] == '+        elif ch == "-":'
    assert "+2 -0" in block.caption


def test_say_refuses_what_a_text_call_cannot_take() -> None:
    with pytest.raises(ValueError):
        probe._say("a\udc80")
    with pytest.raises(ValueError):
        probe._say("a\x00b")
    assert probe._say("a\\udc80") == "a\\udc80"


_TEXT_CALLS = (
    "text",
    "text_colored",
    "text_wrapped",
    "text_unformatted",
    "button",
    "small_button",
    "text_link",
    "input_text_with_hint",
    "set_item_tooltip",
)


@pytest.fixture
def drawn(imgui: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """ImGui's text calls, wrapped to record every string and refuse an undrawable one."""
    seen: list[str] = []
    for name in _TEXT_CALLS:
        real = getattr(imgui, name)

        def spy(*args: Any, _real: Any = real, **kwargs: Any) -> Any:
            for arg in (*args, *kwargs.values()):
                if isinstance(arg, str):
                    assert not drawable.undrawable(arg), arg
                    seen.append(arg)
            return _real(*args, **kwargs)

        monkeypatch.setattr(imgui, name, spy)
    theme.set_theme("dark")
    yield seen


def _frame(im: Any, body: Any) -> None:
    im.new_frame()
    try:
        body()
    finally:
        im.render()


@pytest.mark.parametrize("sheet", probe.SHEETS)
def test_every_sheet_draws_only_drawable_text(imgui: Any, drawn: list[str], sheet: str) -> None:
    _frame(imgui, lambda: probe.draw_sheet(sheet, True))
    assert drawn
    if sheet in probe.BY_NAME:
        assert any("caf\\udc80" in text for text in drawn)


# -- per-tool proofs ----------------------------------------------------------------

# The input fields each tool's proof may read, from the Agent SDK TypeScript
# reference's "Tool Input Types" (code.claude.com/docs/en/agent-sdk/typescript),
# checked against the CLI 2.1.301 bundled in .venv. A proof case built on a field
# not listed here is a guessed shape.
SDK_FIELDS: dict[str, set[str]] = {
    "Bash": {"command", "timeout", "description", "run_in_background", "dangerouslyDisableSandbox"},
    "Read": {"file_path", "offset", "limit", "pages", "allow_large"},
    "Edit": {"file_path", "old_string", "new_string", "replace_all"},
    "Write": {"file_path", "content"},
    "Glob": {"pattern", "path"},
    "Grep": {
        "pattern",
        "path",
        "glob",
        "type",
        "output_mode",
        "-i",
        "-o",
        "-n",
        "-B",
        "-A",
        "-C",
        "context",
        "head_limit",
        "offset",
        "multiline",
    },
    "NotebookEdit": {"notebook_path", "cell_id", "new_source", "cell_type", "edit_mode"},
    "TodoWrite": {"todos"},
    "WebFetch": {"url", "prompt", "offset"},
    "WebSearch": {"query", "allowed_domains", "blocked_domains"},
    "Agent": {
        "description",
        "prompt",
        "subagent_type",
        "model",
        "effort",
        "run_in_background",
        "name",
        "team_name",
        "mode",
        "isolation",
    },
}
REQUIRED: dict[str, set[str]] = {
    "Bash": {"command"},
    "Read": {"file_path"},
    "Edit": {"file_path", "old_string", "new_string"},
    "Write": {"file_path", "content"},
    "Glob": {"pattern"},
    "Grep": {"pattern"},
    "NotebookEdit": {"notebook_path", "new_source"},
    "TodoWrite": {"todos"},
    "WebFetch": {"url", "prompt"},
    "WebSearch": {"query"},
    "Agent": {"description", "prompt"},
}


@pytest.fixture(scope="module")
def proofs() -> tuple[Any, tuple[Any, ...]]:
    built: tuple[Any, tuple[Any, ...]] = probe.build_proof_cases()
    return built


def test_every_proof_is_the_entry_the_transcript_holds(proofs: tuple[Any, tuple[Any, ...]]) -> None:
    transcript, cases = proofs
    assert {(c.tool, c.column) for c in cases} == {
        (tool, col) for tool in probe.PROOF_TOOLS for col in probe.PROOF_COLUMNS
    }
    for case in cases:
        assert isinstance(case.entry, ToolEntry)
        assert transcript.tool_entry(case.entry.tool_use_id) is case.entry
        start, end = case.entry.call
        assert case.entry in transcript.tool_entries(start, end)


def test_every_proof_input_is_the_sdk_shape(proofs: tuple[Any, tuple[Any, ...]]) -> None:
    _, cases = proofs
    for case in cases:
        name, args = case.entry.name, case.entry.input
        if name in SDK_FIELDS:
            assert set(args) <= SDK_FIELDS[name], (name, set(args) - SDK_FIELDS[name])
            assert REQUIRED[name] <= set(args), (name, REQUIRED[name] - set(args))
        if name == "TodoWrite":
            for todo in args["todos"]:
                assert set(todo) == {"content", "status", "activeForm"}
                assert todo["status"] in ("pending", "in_progress", "completed")
    # MultiEdit is not a tool in this CLI; Task is Agent's old name.
    assert "MultiEdit" not in probe.PROOF_TOOLS


def test_each_column_is_the_state_it_is_drawn_as(proofs: tuple[Any, tuple[Any, ...]]) -> None:
    _, cases = proofs
    want = {"done": "done", "second": "done", "error": "error", "running": "running"}
    for case in cases:
        state = probe.proof(case.entry, case.live).state
        assert state == want.get(case.column, "no result"), (case.tool, case.column, state)


def test_every_proof_string_is_drawable(proofs: tuple[Any, tuple[Any, ...]]) -> None:
    _, cases = proofs
    for case in cases:
        p = probe.proof(case.entry, case.live)
        for text in probe.proof_strings(p):
            assert isinstance(text, str) and not drawable.undrawable(text), (case.tool, text)
        for row in (*p.head, *p.body):
            for face, ink, _ in row:
                assert face in ("mono", "prose", "caption", "tip") and ink in probe.INKS
    bash = next(c for c in cases if (c.tool, c.column) == ("Bash", "done"))
    assert any("caf\\udc80" in s for s in probe.proof_strings(probe.proof(bash.entry, False)))


def _proof(cases: tuple[Any, ...], tool: str, column: str) -> Any:
    case = next(c for c in cases if (c.tool, c.column) == (tool, column))
    return probe.proof(case.entry, case.live)


def test_bash_keeps_the_tail_and_the_others_the_head(proofs: tuple[Any, tuple[Any, ...]]) -> None:
    _, cases = proofs
    bash = _proof(cases, "Bash", "second")
    assert bash.tail and bash.more.startswith("41 earlier lines")
    assert "42 passed" in bash.body[-1][0][2]
    assert bash.head[0][0][2].startswith("$ ")
    read = _proof(cases, "Read", "second")
    assert not read.tail and read.body[0][0][2].strip() == "1"


def test_error_text_is_unwrapped_and_the_exit_code_read(
    proofs: tuple[Any, tuple[Any, ...]],
) -> None:
    _, cases = proofs
    for tool in probe.PROOF_TOOLS:
        p = _proof(cases, tool, "error")
        assert not any("tool_use_error" in s for s in probe.proof_strings(p)), tool
        assert p.caption[-1][1] == "danger"
    assert _proof(cases, "Bash", "error").caption[-1][2] == "exit 1"


def test_per_tool_content(proofs: tuple[Any, tuple[Any, ...]]) -> None:
    _, cases = proofs
    edit = _proof(cases, "Edit", "done")
    assert [ink for row in edit.body for _, ink, _ in row] == ["ctx", "add", "add", "ctx"]
    assert ("caption", "add", "new file") in _proof(cases, "Write", "done").caption
    assert ("caption", "warn", "overwrote") in _proof(cases, "Write", "second").caption
    grep = _proof(cases, "Grep", "second")
    assert grep.body[0][0] == ("mono", "accent", "/home/wreel/src/tle/tle/parse.py:1")
    assert ("caption", "text", "6 results") in _proof(cases, "WebSearch", "done").caption
    todo = _proof(cases, "TodoWrite", "done")
    assert [row[0][2] for row in todo.body] == ["[x] ", "[x] ", "[>] ", "[ ] ", "[ ] "]
    agent = _proof(cases, "Agent", "done")
    assert ("caption", "dim", "9 tool uses · 41 s") in agent.caption
    assert not any("agentId" in s for s in probe.proof_strings(agent))
    mcp = _proof(cases, "mcp__pptmstr__read_board", "second")
    assert mcp.head[0][0][2] == "post_concern" and mcp.head[1][0][2] == "to: "


def test_the_well_comes_from_the_theme() -> None:
    seen = set()
    try:
        for name in probe.PROOF_THEMES:
            palette = theme.set_theme(name)
            term = probe.terminal_for(palette)
            seen.add(probe._hex(term.ground))
            assert term.text is not None and probe._hex(term.text) == probe._hex(palette.text.vec4)
            assert (term.outline is not None) == (name == "high_contrast")
            # A step off the pane, not a slab: the fix-ups table's 1.07-1.21 band.
            lo, hi = sorted((probe._lum(term.ground), probe._lum(palette.bg.vec4)))
            assert 1.05 <= (hi + 0.05) / (lo + 0.05) <= 1.3, name
    finally:
        theme.set_theme("dark")
    assert len(seen) == len(probe.PROOF_THEMES)
    assert "#0b0b0c" not in seen


@pytest.mark.parametrize("suffix", list(probe.PROOF_SHEETS))
@pytest.mark.parametrize("name", probe.PROOF_THEMES)
def test_every_proof_sheet_draws_only_drawable_text(
    imgui: Any, drawn: list[str], suffix: str, name: str
) -> None:
    theme.set_theme(name)
    try:
        _frame(imgui, lambda: probe.draw_proof_sheet(suffix, True))
    finally:
        theme.set_theme("dark")
    assert drawn
    if suffix == "":
        assert any("caf\\udc80" in text for text in drawn)


def test_a_url_is_constructed_whole_with_its_host_kept(proofs: tuple[Any, tuple[Any, ...]]) -> None:
    _, cases = proofs
    short = _proof(cases, "WebFetch", "done").head[0]
    assert [text for _, _, text in short[:3]] == [
        "https://",
        "celestrak.org",
        "/columns/v04n03/#FAQ04",
    ]
    assert short[1][1] == "link" and short[-1] == (
        "tip",
        "link",
        "https://celestrak.org/columns/v04n03/#FAQ04",
    )
    long = _proof(cases, "WebFetch", "second")
    row = long.head[0]
    assert (
        row[1][2] == "celestrak.org" and "…" in row[2][2] and row[2][2].endswith("campaign=2026-10")
    )
    assert len(row[2][2]) == probe.URL_REST
    assert row[-1][2] == probe._LONG_URL
    # The cross-host redirect's target is in the result, said as unverified.
    assert long.caption[-1] == ("caption", "dim", "redirect · not fetched")
    assert long.body[1][-1][2] == probe._REDIRECTED
    search = _proof(cases, "WebSearch", "done")
    assert [span[0] for span in search.body[1]] == ["mono", "mono", "mono", "tip"]
