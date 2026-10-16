"""
The colours the modal forms are drawn under: one surface, and wells in a second one.

Against Dear ImGui itself, as ``tests/test_dialog_style.py`` is and with its context
and its two dialogs, because every claim here is about a colour Dear ImGui holds at
the moment something is drawn with it: what a child window would be filled with when
it begins, what a combo's frame would be, what is on the colour stack after a draw
has returned. Nothing is rendered, so nothing here says what reached the screen.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from pptmstr import theme
from pptmstr.ui import widgets
from tests import test_dialog_style
from tests.test_dialog_style import _DIALOGS, _Dialog, _frame

# The fixture of that file, under its own name here: a context styled as the
# application's, with no frame begun.
context = test_dialog_style.context

Rgba = tuple[float, float, float, float]

# The slots the scope sets. Every other one is the docks' inside it.
_NO_FILL = (imgui.Col_.child_bg, imgui.Col_.scrollbar_bg)
_WELL = (imgui.Col_.frame_bg, imgui.Col_.frame_bg_hovered, imgui.Col_.frame_bg_active)
_BUTTON = (imgui.Col_.button, imgui.Col_.button_hovered, imgui.Col_.button_active)


@pytest.fixture(autouse=True)
def _restore_theme() -> Any:
    before = theme.current().name
    yield
    theme.set_theme(before)


def _rgba(colour: imgui.ImVec4) -> Rgba:
    return (colour.x, colour.y, colour.z, colour.w)


def _held(slot: imgui.Col_) -> Rgba:
    """What the style holds for a slot now, pushes included."""
    return _rgba(imgui.get_style().color_(slot))


def _colours() -> tuple[Rgba, ...]:
    """Every colour the style holds now, in slot order."""
    return tuple(_held(imgui.Col_(slot)) for slot in range(int(imgui.Col_.count)))


def _stacks() -> tuple[int, int]:
    """How much is pushed and not yet popped: colours, then style values."""
    held = imgui.get_current_context()
    return (held.color_stack.size(), held.style_var_stack.size())


def _in_scope() -> tuple[Rgba, ...]:
    """Every colour as the style holds it inside ``widgets.dialog_style``."""
    seen: list[tuple[Rgba, ...]] = []

    def body() -> None:
        with widgets.dialog_style():
            seen.append(_colours())

    _frame(body)
    return seen[0]


# -- the scope -----------------------------------------------------------------------


def test_inside_the_scope_a_child_window_has_no_fill_of_its_own(context: None) -> None:
    """
    The docks' child windows are filled, in a colour that is not a popup's: that is
    the second surface a scrolling body puts inside a modal. Inside the scope a
    child is filled with nothing, and nor is a scrollbar's track, so the popup is
    what shows.
    """
    dock = _colours()
    assert dock[imgui.Col_.child_bg][3] == 1.0
    assert dock[imgui.Col_.child_bg] != dock[imgui.Col_.popup_bg]

    scope = _in_scope()
    for slot in _NO_FILL:
        assert scope[slot][3] == 0.0, slot
    assert scope[imgui.Col_.popup_bg] == dock[imgui.Col_.popup_bg]


def test_inside_the_scope_a_frame_is_a_well_and_not_the_surface(context: None) -> None:
    """
    The docks' frame is ``dock_well``, a step off a dock's pane, which is not the
    surface a modal's fields stand on. Inside the scope it is the palette's well,
    opaque and not the popup's own colour.
    """
    dock = _colours()
    assert dock[imgui.Col_.frame_bg] == _rgba(theme.P.dock_well.vec4)

    scope = _in_scope()
    assert scope[imgui.Col_.frame_bg] != dock[imgui.Col_.frame_bg]
    assert scope[imgui.Col_.frame_bg] == _rgba(theme.P.well.vec4)
    assert scope[imgui.Col_.frame_bg] != scope[imgui.Col_.popup_bg]


def test_a_well_pointed_at_or_held_is_a_third_and_a_fourth_colour_and_opaque(
    context: None,
) -> None:
    """
    Opaque, because what is behind a well is the surface: a translucent accent
    there is a tint of the surface, and the field loses its fill under the pointer.
    """
    dock = _colours()
    assert dock[imgui.Col_.frame_bg_hovered][3] < 1.0

    scope = _in_scope()
    well, hovered, active = (scope[slot] for slot in _WELL)
    assert len({well, hovered, active, scope[imgui.Col_.popup_bg]}) == 4
    assert hovered[3] == active[3] == 1.0
    assert hovered == _rgba(theme.mixed(theme.P.well, theme.P.accent, theme._WELL_HOVERED))
    assert active == _rgba(theme.mixed(theme.P.well, theme.P.accent, theme._WELL_ACTIVE))


def test_a_button_is_the_docks_button_inside_the_scope_and_is_not_a_well(context: None) -> None:
    dock = _colours()
    scope = _in_scope()
    for slot in _BUTTON:
        assert scope[slot] == dock[slot], slot
    assert scope[imgui.Col_.button] != scope[imgui.Col_.frame_bg]


def test_the_scope_sets_those_five_colours_and_no_other(context: None) -> None:
    """
    A list to pick from, a collapsing header, text, a border, a rule: each is what
    it is on a dock, so nothing but a surface and a well tells a modal from one.
    """
    dock = _colours()
    scope = _in_scope()
    differing = {imgui.Col_(slot) for slot in range(len(dock)) if scope[slot] != dock[slot]}
    assert differing == {*_NO_FILL, *_WELL}


@pytest.mark.parametrize("name", sorted(theme.THEMES))
def test_the_well_is_the_palettes_in_force_when_the_scope_is_entered(
    context: None, name: str
) -> None:
    """
    Per palette, and across a switch: what is pushed is built once for each and
    kept, so the one built first must not be what every later palette is given.
    """
    first = _in_scope()
    theme.set_theme(name)
    theme.apply_style()

    scope = _in_scope()
    assert scope[imgui.Col_.frame_bg] == _rgba(theme.THEMES[name].well.vec4)
    assert scope[imgui.Col_.frame_bg] != scope[imgui.Col_.popup_bg]
    if name != "dark":
        assert scope[imgui.Col_.frame_bg] != first[imgui.Col_.frame_bg]

    theme.set_theme("dark")
    theme.apply_style()
    assert _in_scope() == first


def test_leaving_the_scope_puts_the_docks_colours_back_on_every_way_out(context: None) -> None:
    """
    A return, and an exception from inside it. A colour left on the stack is every
    dock drawn with a modal's wells and with child windows that have no fill.
    """
    dock = _colours()
    pushed: list[int] = []

    def returning() -> None:
        before = _stacks()
        with widgets.dialog_style():
            pushed.append(_stacks()[0] - before[0])
            return

    def raising() -> None:
        with widgets.dialog_style():
            raise KeyError("from inside the scope")

    stacks = _stacks()
    _frame(returning)
    assert pushed == [len(_NO_FILL) + len(_WELL)]
    assert _colours() == dock
    assert _stacks() == stacks
    with pytest.raises(KeyError):
        _frame(raising)
    assert _colours() == dock
    assert _stacks() == stacks


# -- both dialogs --------------------------------------------------------------------


class _Seen:
    """What the style held when each kind of thing in a dialog was drawn."""

    def __init__(self) -> None:
        # By what was drawn: the label of a field or a button, the id of a child.
        self.fields: dict[str, tuple[Rgba, Rgba, Rgba]] = {}
        self.children: dict[str, tuple[Rgba, Rgba]] = {}
        self.buttons: dict[str, tuple[Rgba, Rgba]] = {}
        self.popups: list[Rgba] = []


_FIELDS = ("combo", "input_int", "input_text_with_hint")
_BUTTONS = ("button", "small_button")


def _watch(monkeypatch: pytest.MonkeyPatch, dialog: _Dialog, *, list_open: bool = False) -> _Seen:
    """
    Note the colours in force at every field, child window, button and popup the
    dialog draws, through the stand-in for Dear ImGui its module already draws with.
    """
    seen = _Seen()
    noting = dialog.noting

    def field(name: str) -> Callable[..., object]:
        real = getattr(noting, name)

        def noted(label: str, *args: object) -> object:
            seen.fields[label] = (_held(_WELL[0]), _held(_WELL[1]), _held(_WELL[2]))
            return real(label, *args)

        return noted

    def button(name: str) -> Callable[..., object]:
        real = getattr(noting, name)

        def noted(label: str, *args: object) -> object:
            seen.buttons[label] = (_held(imgui.Col_.button), _held(imgui.Col_.frame_bg))
            return real(label, *args)

        return noted

    def begin_child(name: str, *args: Any) -> bool:
        seen.children[name] = (_held(imgui.Col_.child_bg), _held(imgui.Col_.scrollbar_bg))
        return bool(imgui.begin_child(name, *args))

    real_begin = noting.begin_popup_modal

    def begin_popup_modal(*args: object) -> tuple[bool, bool | None]:
        seen.popups.append(_held(imgui.Col_.popup_bg))
        return real_begin(*args)

    def collapsing_header(*args: Any) -> bool:
        imgui.set_next_item_open(True)
        return bool(imgui.collapsing_header(*args))

    for name in _FIELDS:
        monkeypatch.setattr(noting, name, field(name), raising=False)
    for name in _BUTTONS:
        monkeypatch.setattr(noting, name, button(name), raising=False)
    monkeypatch.setattr(noting, "begin_child", begin_child, raising=False)
    monkeypatch.setattr(noting, "begin_popup_modal", begin_popup_modal, raising=False)
    if list_open:
        monkeypatch.setattr(noting, "collapsing_header", collapsing_header, raising=False)

    real_box = widgets.multiline_input

    def box(label: str, *args: Any, **kwargs: Any) -> tuple[bool, str]:
        seen.fields[label] = (_held(_WELL[0]), _held(_WELL[1]), _held(_WELL[2]))
        return real_box(label, *args, **kwargs)

    monkeypatch.setattr(widgets, "multiline_input", box)
    return seen


@pytest.fixture(params=sorted(_DIALOGS))
def dialog(
    request: pytest.FixtureRequest,
    context: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> _Dialog:
    made: _Dialog = _DIALOGS[request.param](monkeypatch, tmp_path)
    return made


def _wells() -> tuple[Rgba, Rgba, Rgba]:
    """A well, a well pointed at and a well held, from the palette and not the scope."""
    well, accent = theme.P.well, theme.P.accent
    return (
        _rgba(well.vec4),
        _rgba(theme.mixed(well, accent, theme._WELL_HOVERED)),
        _rgba(theme.mixed(well, accent, theme._WELL_ACTIVE)),
    )


def test_every_field_in_the_dialog_is_a_well_and_the_same_one(
    dialog: _Dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The text box, each single line, each combo and the stepper, in both dialogs
    against the one palette: so a field in one is the colour of a field in the
    other, whichever of them has a child window around it.
    """
    dock = _colours()
    seen = _watch(monkeypatch, dialog)
    dialog.frame(3)
    assert dialog.is_open()

    labels = set(seen.fields)
    assert {"gate", "model", "sub-agents"} <= labels
    assert any(label.startswith("##") for label in labels), "the text box"
    assert set(seen.fields.values()) == {_wells()}
    assert _wells()[0] != dock[imgui.Col_.popup_bg]


def test_the_dialogs_surface_is_the_popups_and_its_body_has_none_of_its_own(
    dialog: _Dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    dock = _colours()
    seen = _watch(monkeypatch, dialog)
    dialog.frame(3)

    assert set(seen.popups) == {dock[imgui.Col_.popup_bg]}
    assert seen.children, "a dialog with no child window has nothing to check here"
    for name, (fill, track) in seen.children.items():
        assert fill[3] == 0.0, name
        assert track[3] == 0.0, name


def test_the_list_of_sessions_in_the_launcher_is_the_surface_and_not_a_well(
    context: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    It is picked from and not typed in. A bordered child window, so what it is
    filled with is the child's fill: none, as the body's is.
    """
    dialog = _DIALOGS["the launcher"](monkeypatch, tmp_path)
    seen = _watch(monkeypatch, dialog, list_open=True)
    dialog.frame(3)

    assert "##sessions" in seen.children
    assert len(seen.children) >= 2
    assert {fill[3] for fill, _ in seen.children.values()} == {0.0}


def test_a_button_in_the_dialog_is_the_docks_button_and_not_a_well(
    dialog: _Dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    dock = _colours()
    seen = _watch(monkeypatch, dialog)
    dialog.frame(3)

    assert "cancel" in seen.buttons
    for label, (fill, well) in seen.buttons.items():
        assert fill == dock[imgui.Col_.button], label
        assert fill != well, label


def test_the_docks_colours_and_both_stacks_are_what_they_were_after_a_frame_with_the_dialog(
    dialog: _Dialog,
) -> None:
    dock = _colours()
    stacks = _stacks()
    dialog.frame(3)
    assert dialog.is_open()
    assert _colours() == dock
    assert _stacks() == stacks


def test_a_dialog_that_does_not_open_this_frame_still_puts_the_colours_back(
    dialog: _Dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``begin_popup_modal`` answers False and the draw returns from the middle of
    itself, with the scope entered and its colours pushed.
    """
    dock = _colours()
    stacks = _stacks()
    seen = _watch(monkeypatch, dialog)
    dialog.frame(3)
    dialog.noting.refuse_to_open = True
    entered = len(seen.popups)
    at_the_refusal: list[Rgba] = []
    real_begin = dialog.noting.begin_popup_modal

    def begin_popup_modal(*args: object) -> tuple[bool, bool | None]:
        at_the_refusal.append(_held(imgui.Col_.frame_bg))
        answer: tuple[bool, bool | None] = real_begin(*args)
        return answer

    monkeypatch.setattr(dialog.noting, "begin_popup_modal", begin_popup_modal, raising=False)
    dialog.frame()

    assert len(seen.popups) == entered + 1
    assert at_the_refusal == [_wells()[0]]
    assert not dialog.is_open()
    assert _colours() == dock
    assert _stacks() == stacks
    # And a frame later nothing is owed: an unbalanced stack asserts here.
    dialog.frame(2)
    assert _colours() == dock


def test_a_draw_that_raises_puts_the_colours_back(dialog: _Dialog) -> None:
    dock = _colours()
    dialog.frame(3)
    dialog.noting.raise_at_begin = True
    held: list[tuple[int, int]] = []

    def draw() -> None:
        before = _stacks()
        try:
            dialog._draw()
        finally:
            held.append(before)
            assert _stacks() == before

    with pytest.raises(KeyError):
        _frame(draw)
    assert held
    assert _colours() == dock
