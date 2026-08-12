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


# -- the activity throbber -----------------------------------------------------
#
# Only the motion is under test. Whether the cells land on the right pixels needs a
# GL context and would assert that ImGui works; what can regress silently is the
# arithmetic that decides whether anything is lit at all.


def test_a_lit_cell_is_always_on_the_field() -> None:
    for i in range(2000):
        for column, row, intensity, _head in widgets.rain_cells(i * 0.007):
            assert 0 <= column < widgets._RAIN_COLS
            assert 0 <= row < widgets._RAIN_ROWS
            assert 0.0 <= intensity <= 1.0


def test_every_column_always_has_exactly_one_head() -> None:
    """
    The whole point of the wrapped trail. If this can fail the field can go dark, and
    a throbber that stops is worse than no throbber -- it says the session died.
    """
    for i in range(4000):
        heads = [(c, r) for c, r, _i, head in widgets.rain_cells(i * 0.006) if head]
        assert sorted(c for c, _r in heads) == list(range(widgets._RAIN_COLS))


def test_intensity_falls_off_behind_the_head() -> None:
    """
    The head is the brightest cell in its column and the trail dims away from it.
    Inverting this draws a drop that climbs, which is the one thing rain must not do.
    """
    for i in range(500):
        by_column: dict[int, list[tuple[float, bool]]] = {}
        for column, _row, intensity, head in widgets.rain_cells(i * 0.013):
            by_column.setdefault(column, []).append((intensity, head))
        for lit in by_column.values():
            brightest = max(lit)
            assert brightest[1], "the brightest cell in a column must be its head"
            assert sum(1 for _i, head in lit if head) == 1


def test_the_columns_do_not_march_in_step() -> None:
    """
    Equal or harmonic periods make the three columns resolve into one moving row,
    which reads as a progress bar rather than as rain.
    """
    for a, b in ((0, 1), (0, 2), (1, 2)):
        ratio = widgets._RAIN_FALL[a] / widgets._RAIN_FALL[b]
        for harmonic in (0.5, 1.0, 1.5, 2.0):
            assert abs(ratio - harmonic) > 0.05


def test_two_cards_do_not_fall_in_lockstep() -> None:
    a = widgets.phase_seed("s1:")
    b = widgets.phase_seed("s2:")
    assert a != b
    assert widgets.rain_cells(4.0, a) != widgets.rain_cells(4.0, b)


def test_a_seed_is_stable_across_processes() -> None:
    """
    Pinned literally, not just for self-consistency: ``hash()`` would satisfy an
    equality check within one run and still be salted differently on the next.
    """
    assert widgets.phase_seed("s1:agent-a") == pytest.approx(494 / 997)
