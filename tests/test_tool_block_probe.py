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
