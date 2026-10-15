"""
``layout_ini``: a new pane's entry in a layout that was saved before it existed.

The transform is text in, text out, and is pinned here. That the entry it writes
is what stops hello_imgui rebuilding a saved layout is a claim about hello_imgui
and is not established here: ``scripts/verify_new_pane_keeps_layout.py`` runs it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pptmstr import layout_ini
from pptmstr.layout_ini import with_window_beside

# Two layouts as hello_imgui writes them, cut down: BOARD dragged into a node of
# its own in the first, and the last tab of a shared node in the second.
SAVED = """;;; !!! This configuration is handled by HelloImGui, separated by markers like this:
           ;;;<<<INI_NAME>>>;;;

;;;<<<AppWindow>>>;;;
[WIN]
WindowPosition=0,0
;;;<<<ImGui_4042618610_TRIAGE>>>;;;
[Window][FLEET]
Pos=0,24
Size=381,950
Collapsed=0
DockId=0x00000001,0

[Window][BOARD]
Pos=383,480
Size=709,494
Collapsed=0
DockId=0x00000008,0

[Window][BRIEF]
Pos=1094,24
Size=826,454
Collapsed=0
DockId=0x00000005,2

[Window][BOARD/##board_1A2B3C4D]
IsChild=1
Size=709,400

[Docking][Data]
DockSpace       ID=0xAFC85805 Window=0x079D3A04 Pos=0,24 Size=1920,950 Split=X
  DockNode      ID=0x00000008 Parent=0x00000003 SizeRef=803,494 Selected=0x251B82DA

;;;<<<Layout_4042618610_TRIAGE>>>;;;
[Visibility]
1169283175_BOARD=true
;;;<<<SplitIds>>>;;;
{"gImGuiSplitIDs":{"ContextSpace":4}}
;;;<<<ImGui_1415732763_FOCUS>>>;;;
[Window][DETAIL]
Pos=1194,24
Size=726,567
Collapsed=0
DockId=0x00000005,0

[Window][BOARD]
Pos=1194,24
Size=726,567
Collapsed=0
DockId=0x00000005,2

[Window][BRIEF]
Pos=1194,24
Size=726,567
Collapsed=0
DockId=0x00000005,7

[Window][LOG]
Collapsed=0
DockId=0x00000006,9

[Docking][Data]
DockSpace       ID=0xAFC85805 Window=0x079D3A04 Pos=0,24 Size=1920,950 Split=X

;;;<<<Layout_1415732763_FOCUS>>>;;;
[Visibility]
1169283175_BOARD=true
"""

TRIAGE_ENTRY = "[Window][DIGEST]\nCollapsed=0\nDockId=0x00000008,1\n\n"
FOCUS_ENTRY = "[Window][DIGEST]\nCollapsed=0\nDockId=0x00000005,8\n\n"


def _without(text: str, *pieces: str) -> str:
    for piece in pieces:
        assert text.count(piece) == 1, piece
        text = text.replace(piece, "")
    return text


def test_each_saved_layout_gets_the_pane_in_its_neighbours_node_as_the_last_tab() -> None:
    """
    BOARD's node in each layout, which is not the same node in the two, and an
    order past every tab already in that node -- 7 is BRIEF's in FOCUS, and the 9
    in another node is not counted.
    """
    out = with_window_beside(SAVED, "DIGEST", "BOARD")

    assert out.count("[Window][DIGEST]") == 2
    triage, focus = out.split(";;;<<<ImGui_1415732763_FOCUS>>>;;;")
    assert "DockId=0x00000008,0\n\n" + TRIAGE_ENTRY + "[Window][BRIEF]" in triage
    assert "DockId=0x00000005,2\n\n" + FOCUS_ENTRY + "[Window][BRIEF]" in focus


def test_nothing_the_operator_saved_is_changed_or_dropped() -> None:
    """Insert-only: take the two entries back out and the file is byte for byte."""
    out = with_window_beside(SAVED, "DIGEST", "BOARD")

    assert _without(out, TRIAGE_ENTRY, FOCUS_ENTRY) == SAVED


def test_a_second_pass_adds_nothing() -> None:
    once = with_window_beside(SAVED, "DIGEST", "BOARD")

    assert with_window_beside(once, "DIGEST", "BOARD") == once


def test_a_layout_that_already_knows_the_pane_is_left_as_the_operator_has_it() -> None:
    """
    Per layout. The operator floated DIGEST in TRIAGE -- an entry with no dock --
    and has never opened FOCUS with it: TRIAGE is untouched, FOCUS gets its entry.
    """
    floated = "[Window][DIGEST]\nPos=60,60\nSize=400,400\nCollapsed=0\n\n"
    saved = SAVED.replace("[Window][BRIEF]\nPos=1094", floated + "[Window][BRIEF]\nPos=1094")
    out = with_window_beside(saved, "DIGEST", "BOARD")

    assert _without(out, FOCUS_ENTRY) == saved


def test_a_layout_saved_without_the_neighbour_is_left_alone() -> None:
    """Nothing to be beside, and hello_imgui builds that layout from code anyway."""
    saved = SAVED.replace("[Window][BOARD]\nPos=1194", "[Window][OTHER]\nPos=1194")
    out = with_window_beside(saved, "DIGEST", "BOARD")

    assert _without(out, TRIAGE_ENTRY) == saved


def test_a_neighbour_saved_floating_gives_an_entry_with_no_dock() -> None:
    """The entry is what keeps the layout; there is no node to name."""
    saved = SAVED.replace(
        "Size=709,494\nCollapsed=0\nDockId=0x00000008,0\n", "Size=709,494\nCollapsed=0\n"
    )
    out = with_window_beside(saved, "DIGEST", "BOARD")

    assert "Size=709,494\nCollapsed=0\n\n[Window][DIGEST]\nCollapsed=0\n\n[Window][BRIEF]" in out
    assert _without(out, "[Window][DIGEST]\nCollapsed=0\n\n", FOCUS_ENTRY) == saved


def test_a_child_window_of_the_neighbour_is_not_the_neighbour() -> None:
    """``[Window][BOARD/##board_...]`` is the pane's scroll child and has no dock."""
    saved = SAVED.replace(
        "[Window][BOARD]\nPos=383,480\nSize=709,494\nCollapsed=0\nDockId=0x00000008,0\n\n", ""
    )
    assert "[Window][BOARD/##board_1A2B3C4D]" in saved
    out = with_window_beside(saved, "DIGEST", "BOARD")

    assert _without(out, FOCUS_ENTRY) == saved


def test_only_the_imgui_parts_are_read() -> None:
    """A ``[Window][BOARD]`` under some other marker is not a window's settings."""
    saved = ";;;<<<Layout_1_X>>>;;;\n[Window][BOARD]\nCollapsed=0\nDockId=0x00000002,0\n\n"

    assert with_window_beside(saved, "DIGEST", "BOARD") == saved
    assert with_window_beside("", "DIGEST", "BOARD") == ""
    assert with_window_beside("no markers at all\n", "DIGEST", "BOARD") == "no markers at all\n"


def test_an_entry_that_ends_the_file_is_closed_before_the_new_one() -> None:
    saved = ";;;<<<ImGui_1_X>>>;;;\n[Window][BOARD]\nCollapsed=0\nDockId=0x00000002,4"
    out = with_window_beside(saved, "DIGEST", "BOARD")

    assert out == saved + "\n\n[Window][DIGEST]\nCollapsed=0\nDockId=0x00000002,5\n\n"
    assert with_window_beside(out, "DIGEST", "BOARD") == out


def test_a_file_with_crlf_line_ends_keeps_them() -> None:
    saved = SAVED.replace("\n", "\r\n")
    out = with_window_beside(saved, "DIGEST", "BOARD")

    assert "\n" not in out.replace("\r\n", "")
    assert out.replace("\r\n", "\n") == with_window_beside(SAVED, "DIGEST", "BOARD")


def test_the_file_is_replaced_once_and_then_left(tmp_path: Path) -> None:
    ini = tmp_path / "pptmstr.ini"
    ini.write_bytes(SAVED.encode())

    said = layout_ini.keep_layout_for(ini, "DIGEST", "BOARD")
    assert said is not None and "2 saved layout(s)" in said and str(ini) in said
    assert ini.read_bytes().decode() == with_window_beside(SAVED, "DIGEST", "BOARD")
    assert [p.name for p in tmp_path.iterdir()] == ["pptmstr.ini"]

    before = ini.stat().st_mtime_ns
    assert layout_ini.keep_layout_for(ini, "DIGEST", "BOARD") is None
    assert ini.stat().st_mtime_ns == before


def test_no_file_is_nothing_to_do_and_none_is_made(tmp_path: Path) -> None:
    """A first run: hello_imgui builds both layouts from code, DIGEST included."""
    assert layout_ini.keep_layout_for(tmp_path / "pptmstr.ini", "DIGEST", "BOARD") is None
    assert list(tmp_path.iterdir()) == []


def test_a_file_that_cannot_be_replaced_is_left_whole_and_the_failure_is_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ini = tmp_path / "pptmstr.ini"
    ini.write_bytes(SAVED.encode())

    def refuse(*args: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(layout_ini.os, "replace", refuse)
    with pytest.raises(PermissionError):
        layout_ini.keep_layout_for(ini, "DIGEST", "BOARD")

    assert ini.read_bytes().decode() == SAVED
    assert [p.name for p in tmp_path.iterdir()] == ["pptmstr.ini"]
