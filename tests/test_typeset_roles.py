"""
pptmstr's own policy about typeset text: roles, slots, sizes, panes, the settings read.

None of it needs the library or ImGui. ``enabled`` is a product of the
operator's switches, the panes whose typeset branch is built (``roles.ADOPTED``),
and whether the library can draw, which the gate hands in; ``policy`` below hands
in a library that can, with every pane adopted, so that the switches are what a
test reads.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from pptmstr.ui import typeset
from pptmstr.ui.typeset import Kind, Pane, Role, RoleStyle, Slot, roles
from tests.test_typeset_views import _bundled


@pytest.fixture
def seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A library that can draw and every pane adopted, with the settings at their
    defaults: what a test then reads is the operator's switches and nothing else.

    Named for the fixture it stands in for (``tests/conftest.py``), which needs
    the library; nothing in this file does.
    """
    monkeypatch.setattr(roles, "_can_draw", lambda: True)
    monkeypatch.setattr(roles, "ADOPTED", frozenset(Pane))
    monkeypatch.setattr(roles._STATE, "live_px", roles.BODY_PX)
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})


def test_every_role_has_a_default_among_the_slots_it_is_offered() -> None:
    """
    The catalogue is one enum and three tables keyed by it. A role missing from one
    is a ``KeyError`` on the frame that first sets that kind of text.
    """
    assert set(typeset.DEFAULTS) == set(typeset.SLOTS) == set(Role)
    for role in Role:
        assert typeset.DEFAULTS[role].slot in typeset.SLOTS[role], role
        assert typeset.DEFAULTS[role].step in typeset.STEPS, role
    # Inline code is a run in somebody else's line and a label is ImGui's line.
    assert set(typeset.LEADING) == set(Role) - {Role.INLINE_CODE, Role.LABEL}
    for role in (Role.CODE_BLOCK, Role.DIFF, Role.LITERAL, Role.ARGUMENT, Role.INLINE_CODE):
        assert typeset.SLOTS[role] == (Slot.MONOSPACE,), "its columns are content"
    assert {typeset.multiline_kind(role) for role in Role} <= {Kind.LITERAL, Kind.WRAPPED_EXACT}


def test_the_default_family_is_first_and_none_names_a_font_that_does_not_ship() -> None:
    """
    The operator's condition for choosing faces in this version. A family's file is
    one in the imgui-bundle wheel; nothing here may point at the system's fonts.
    """
    names = [family.name for family in typeset.PROPORTIONALS]
    assert names[0] == typeset.DEFAULT_PROPORTIONAL and len(set(names)) == len(names)
    for family in typeset.PROPORTIONALS:
        if family.file is not None:
            assert not Path(family.file).is_absolute()
            assert _bundled(family.file).is_file(), family.name
            assert roles._bundle_fonts().resolve() in _bundled(family.file).parents


def test_the_old_monospace_switch_and_the_preset_names_still_choose(seam: Any) -> None:
    """
    ``configure(mono=)`` and ``configure(faces=)`` are what five panes' tests and
    three scripts call, and what a saved ``typeset_faces`` is read as.
    """
    typeset.configure(typeset=True, mono=True)
    assert typeset.mono() and typeset.style(Role.HEADING).slot is Slot.MONOSPACE
    typeset.configure(typeset=True, mono=False)
    assert not typeset.mono() and typeset._STATE.roles == typeset.DEFAULTS
    typeset.configure(typeset=True, mono=False, faces="mono")
    assert typeset.mono(), "a named preset wins over the old switch"
    typeset.configure(typeset=False)
    assert typeset.mono(), "a call that names no faces changes none"
    typeset.configure(typeset=True, faces="mono", roles={})
    assert not typeset.mono(), "the roles win over a preset's name"
    typeset.configure(typeset=True, faces="roboto-large")
    assert typeset.px(Role.PROSE) == 18.0 and typeset.px(Role.INLINE_CODE) == 16.0
    typeset.configure(typeset=True, faces="droid")
    assert typeset.proportional().name == "droid" and typeset._STATE.roles == typeset.DEFAULTS


def test_a_preset_name_that_never_was_is_the_defaults_and_rebuilds_nothing(seam: Any) -> None:
    epoch = typeset._STATE.epoch
    typeset.configure(typeset=True, faces="comic-sans")
    assert typeset._STATE.roles == typeset.DEFAULTS
    assert typeset.proportional().name == typeset.DEFAULT_PROPORTIONAL
    assert typeset._STATE.epoch == epoch
    assert typeset.enabled()


def test_a_role_is_refused_a_slot_it_is_not_offered_and_a_step_out_of_range(seam: Any) -> None:
    """
    A settings file is hand-editable. Proportional code would lose its columns, and
    a step of 40 is a typo, not a size.
    """
    typeset.configure(
        typeset=True,
        roles={
            Role.CODE_BLOCK: RoleStyle(Slot.PROPORTIONAL, 40),
            Role.DIFF: RoleStyle(Slot.PROPORTIONAL, -40),
            Role.PROSE: RoleStyle(Slot.MONOSPACE, 1),
        },
    )
    assert typeset.style(Role.CODE_BLOCK) == RoleStyle(Slot.MONOSPACE, typeset.STEPS[-1])
    assert typeset.style(Role.DIFF) == RoleStyle(Slot.MONOSPACE, typeset.STEPS[0])
    assert typeset.style(Role.PROSE) == RoleStyle(Slot.MONOSPACE, 1)
    assert typeset.style(Role.TABLE) == typeset.DEFAULTS[Role.TABLE], "left out is the default"


def test_settings_entries_become_role_styles_and_unknown_ones_are_left_out() -> None:
    got = typeset.roles_from(
        [
            ("prose", "monospace", 2),
            ("heading", None, 1),
            ("table", "monospace", None),
            ("label", "cursive", None),
            ("marginalia", "monospace", 0),
        ]
    )
    assert got == {
        Role.PROSE: RoleStyle(Slot.MONOSPACE, 2),
        Role.HEADING: RoleStyle(Slot.PROPORTIONAL, 1),
        Role.TABLE: RoleStyle(Slot.MONOSPACE, 0),
        Role.LABEL: RoleStyle(Slot.PROPORTIONAL, 0),
    }
    assert typeset.panes_from([("board", False), ("attic", False), ("brief", True)]) == {
        Pane.BOARD: False,
        Pane.BRIEF: True,
    }


def test_a_pane_is_switched_off_alone_and_the_master_switch_is_over_all(seam: Any) -> None:
    """
    Each pane asks for itself. Switching one off is not a change of faces: nothing
    is rebuilt, and the others' views are the views they had.
    """
    assert all(typeset.enabled(pane) for pane in Pane) and typeset.enabled()
    epoch = typeset._STATE.epoch
    typeset.configure(typeset=True, panes={Pane.CONTEXT: False, Pane.BOARD: True})
    assert not typeset.enabled(Pane.CONTEXT) and not typeset.pane_on(Pane.CONTEXT)
    assert all(typeset.enabled(pane) for pane in Pane if pane is not Pane.CONTEXT)
    assert typeset.enabled(), "the master switch is still on"
    assert typeset._STATE.epoch == epoch

    typeset.configure(typeset=True)
    assert not typeset.enabled(Pane.CONTEXT), "a call that names no panes changes none"
    typeset.configure(typeset=False)
    assert not any(typeset.enabled(pane) for pane in Pane)
    assert typeset.pane_on(Pane.BOARD) and not typeset.pane_on(Pane.CONTEXT)
    typeset.configure(typeset=True, panes={})
    assert all(typeset.enabled(pane) for pane in Pane), "a pane left out is on"


@pytest.mark.parametrize(
    ("module", "pane"),
    [
        ("detail", Pane.DETAIL),
        ("inbox", Pane.INBOX),
        ("transcript_pane", Pane.CONTEXT),
        ("board_pane", Pane.BOARD),
        ("brief_pane", Pane.BRIEF),
    ],
)
def test_each_pane_asks_whether_it_is_switched_on_for_itself(module: str, pane: Pane) -> None:
    """
    A pane's switch is only a switch if the pane asks about it. One that asked the
    master switch, or about its neighbour, would pass every test of the seam and of
    its own two renderers. Read off the source: every call a pane makes to
    ``typeset.enabled`` names that pane.
    """
    source = (Path(typeset.__file__).parent.parent / f"{module}.py").read_text(encoding="utf-8")
    asked = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "typeset.enabled"
    ]
    assert asked, module
    assert set(asked) == {f"typeset.enabled(typeset.Pane.{pane.name})"}, module


@pytest.mark.parametrize(
    ("asked", "got"),
    [(0.1, 0.875), (9.0, 1.25), (float("nan"), 1.0), (float("inf"), 1.0), (1.125, 1.125)],
)
def test_a_scale_out_of_range_is_clamped_and_one_that_is_no_number_is_one(
    seam: Any, asked: float, got: float
) -> None:
    typeset.configure(typeset=True, scale=asked)
    assert typeset.scale() == got
    assert typeset.body_px() == round(16.0 * got)


def test_a_pane_that_is_not_adopted_is_not_typeset_whatever_is_switched_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A pane's typeset branch calls drawing code that is not built on the library.
    Until the pane is named in ``ADOPTED`` its switch, the master switch and a
    library that can draw are together not enough.
    """
    monkeypatch.setattr(roles, "_can_draw", lambda: True)
    typeset.configure(typeset=True, panes={pane: True for pane in Pane})
    monkeypatch.setattr(roles, "ADOPTED", frozenset())
    assert not any(typeset.enabled(pane) for pane in Pane)
    assert not typeset.enabled(), "asked of no pane, it is not true where every pane's is false"
    monkeypatch.setattr(roles, "ADOPTED", frozenset({Pane.BRIEF}))
    assert [pane for pane in Pane if typeset.enabled(pane)] == [Pane.BRIEF]
    assert typeset.enabled()


def test_a_library_that_cannot_draw_switches_every_pane_off(
    seam: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert all(typeset.enabled(pane) for pane in Pane)
    monkeypatch.setattr(roles, "_can_draw", lambda: False)
    assert not typeset.enabled() and not any(typeset.enabled(pane) for pane in Pane)
    assert typeset.wanted() and all(typeset.pane_on(pane) for pane in Pane)


def test_the_gate_is_what_answers_whether_the_library_can_draw() -> None:
    """
    ``enabled`` is only as true as what it was handed. Importing the package hands
    it the gate's ``available``.
    """
    from pptmstr.ui.typeset import gate

    assert roles._can_draw is gate.can_draw


def test_a_family_whose_file_is_not_there_cannot_be_chosen(
    seam: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The default is used and the menu is told; what the operator asked for is kept.
    """
    monkeypatch.setattr(roles, "_bundle_fonts", lambda: tmp_path)
    roles.prose_path.cache_clear()
    try:
        typeset.configure(typeset=True, proportional="droid")
        assert {family.name: usable for family, usable in typeset.proportionals()} == {
            "roboto": True,
            "droid": False,
        }
        assert typeset.proportional().name == typeset.DEFAULT_PROPORTIONAL
        assert typeset._STATE.family == "droid"
    finally:
        roles.prose_path.cache_clear()


def test_a_scale_multiplies_the_ui_fonts_size_to_a_whole_pixel_and_moves_the_epoch(
    seam: None,
) -> None:
    """
    A multiplier and not an override: the text still follows the display scale
    ``hello_imgui`` gives the UI font. ImGui bakes whole pixels, so the product is one.
    """
    epoch = typeset._STATE.epoch
    typeset.configure(typeset=True, scale=1.25)
    assert typeset.scale() == 1.25 and typeset.body_px() == 20.0
    assert typeset._STATE.epoch == epoch + 1
    roles.follow(17.0)
    assert typeset.body_px() == 21.0, "17 x 1.25 is 21.25"
    assert typeset._STATE.epoch == epoch + 2
    typeset.configure(typeset=True, scale=1.0)
    assert typeset.body_px() == 17.0, "at 1 the UI font's size is used as reported"
    roles.follow(17.0)
    assert typeset._STATE.epoch == epoch + 3, "the size it already has moves nothing"


def test_a_roles_pitch_is_its_leading_times_its_height_to_a_whole_pixel(seam: None) -> None:
    assert typeset.LEADING[Role.PROSE] == 17 / 16
    assert typeset.pitch(Role.PROSE) == typeset.pitch(Role.LITERAL) == 17.0
    assert typeset.pitch(Role.HEADING, 22.0) == 22.0
    assert typeset.pitch(Role.DIFF) == 18.0
    typeset.configure(typeset=True, scale=0.875)
    assert typeset.pitch(Role.PROSE) == 15.0, "14 x 17/16 is 14.875"
    assert typeset.pitch(Role.DIFF) == 16.0


def test_choosing_what_is_already_in_force_moves_nothing(seam: None) -> None:
    chosen = {Role.HEADING: RoleStyle(Slot.MONOSPACE, 1)}
    typeset.configure(typeset=True, proportional="droid", roles=chosen)
    epoch = typeset._STATE.epoch
    typeset.configure(typeset=True, proportional="droid", roles=dict(chosen), scale=1.0)
    assert typeset._STATE.epoch == epoch
