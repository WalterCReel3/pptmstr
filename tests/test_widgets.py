"""
The shared composer wrapper: what flags it hands ImGui.

Drawing is not what is under test -- that needs a GL context and would only assert
that ImGui works. What is worth pinning is the flag arithmetic, because it is the
part that can regress silently: a wrapper that dropped the caller's flags would
still render a perfectly good text box, just one where Tab no longer indents and
Enter no longer launches.
"""

from __future__ import annotations

from typing import Any

import pytest
from imgui_bundle import imgui

from pptmstr.ui import widgets

WORD_WRAP = int(imgui.InputTextFlags_.word_wrap)
TAB = int(imgui.InputTextFlags_.allow_tab_input)


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record what would have been passed to ImGui, and return a plausible result."""
    calls: list[dict[str, Any]] = []

    def fake(label: str, value: str, size: Any, flags: int) -> tuple[bool, str]:
        calls.append({"label": label, "value": value, "size": size, "flags": flags})
        return (False, value)

    monkeypatch.setattr(widgets.imgui, "input_text_multiline", fake)
    return calls


def test_wrap_on_sets_the_flag(captured: list[dict[str, Any]]) -> None:
    widgets.multiline_input("##a", "text", imgui.ImVec2(-1, 80), wrap=True)
    assert captured[0]["flags"] & WORD_WRAP


def test_wrap_off_leaves_it_clear(captured: list[dict[str, Any]]) -> None:
    widgets.multiline_input("##a", "text", imgui.ImVec2(-1, 80), wrap=False)
    assert not captured[0]["flags"] & WORD_WRAP


def test_caller_flags_survive_wrapping(captured: list[dict[str, Any]]) -> None:
    """
    The launcher's Enter-to-launch binding rides in on ``flags``. Wrapping augments
    that mask; it must not replace it.
    """
    widgets.multiline_input("##a", "text", imgui.ImVec2(-1, 80), wrap=True, flags=TAB)
    flags = captured[0]["flags"]
    assert flags & TAB
    assert flags & WORD_WRAP


def test_caller_flags_survive_without_wrapping(captured: list[dict[str, Any]]) -> None:
    widgets.multiline_input("##a", "text", imgui.ImVec2(-1, 80), wrap=False, flags=TAB)
    assert captured[0]["flags"] == TAB


def test_value_and_label_pass_through(captured: list[dict[str, Any]]) -> None:
    widgets.multiline_input("##reply", "half-typed", imgui.ImVec2(-8.0, 54.0), wrap=True)
    assert captured[0]["label"] == "##reply"
    assert captured[0]["value"] == "half-typed"


def test_caller_flags_are_not_mutated_across_calls(captured: list[dict[str, Any]]) -> None:
    """
    ``flags |= ...`` on a parameter rebinds rather than mutates, but the int being
    an IntFlag makes that easy to get wrong on a later edit -- and the symptom would
    be wrapping that sticks on after the menu toggles it off.
    """
    widgets.multiline_input("##a", "t", imgui.ImVec2(-1, 80), wrap=True, flags=TAB)
    widgets.multiline_input("##a", "t", imgui.ImVec2(-1, 80), wrap=False, flags=TAB)
    assert captured[1]["flags"] == TAB
