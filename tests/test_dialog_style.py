"""
The style the modal forms are drawn under, and that both of them are drawn under it.

Against Dear ImGui itself -- a context with a display size and no backend -- because
every claim here is about a number Dear ImGui holds: a style value at the moment a
window begins, the rectangle an item was given, the style stack after a draw has
returned. Nothing is rendered and nothing here says how the result looks.

Every frame is drawn at ``theme._FONT_SIZE``, the size the body face is loaded at,
so the pixel figures are the application's and not those of Dear ImGui's built-in
13px face.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from pptmstr import cli_version, theme
from pptmstr.approval import Policy
from pptmstr.ui import launcher, resume_form, widgets
from pptmstr.ui.launcher import LauncherState
from pptmstr.ui.resume_form import ResumeForm, ResumeForms

SESSION = "11111111-2222-3333-4444-555555555555"

Style = tuple[tuple[float, float], ...]


def _style() -> Style:
    """The four values the scope sets, as the style holds them now."""
    style = imgui.get_style()
    return tuple(
        (v.x, v.y)
        for v in (
            style.item_spacing,
            style.frame_padding,
            style.item_inner_spacing,
            style.window_padding,
        )
    )


def _meets() -> cli_version.FloorCheck:
    return cli_version.MeetsFloor("2.1.300", (2, 1, 300), cli_version.SANDBOX_FLOOR)


@pytest.fixture
def context() -> Iterator[None]:
    """A context styled as the application's, with no frame begun."""
    made = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1280.0, 900.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.set_ini_filename("")
    io.add_focus_event(True)
    theme.apply_style()
    yield
    imgui.destroy_context(made)


def _frame(body: Callable[[], None], *, size: float = theme._FONT_SIZE) -> None:
    """One frame at the root, with ``body`` drawn at ``size``."""
    imgui.new_frame()
    imgui.push_font(None, size)
    try:
        body()
    finally:
        imgui.pop_font()
    imgui.end_frame()


# -- the scope -----------------------------------------------------------------------


def test_inside_the_scope_items_have_more_room_and_frames_are_as_tall_as_the_docks(
    context: None,
) -> None:
    """
    At the size the body face is loaded at. The window's edge is the one value held
    down: an em at most, and no taller than it is wide.

    A frame's vertical padding is the one value that is not more: the docks' 9x6,
    the operator's pick, is as tall as the modal's 0.375 em, so a modal's field is
    as tall as a dock's and only wider.
    """
    seen: list[Style] = []
    dock = _style()

    def body() -> None:
        with widgets.dialog_style():
            seen.append(_style())

    _frame(body)
    spacing, frame, inner, edge = seen[0]
    dock_spacing, dock_frame, dock_inner, dock_edge = dock

    assert dock_spacing == (8.0, 6.0) and dock_frame == (9.0, 6.0)
    assert spacing[1] > dock_spacing[1]
    assert frame[1] == dock_frame[1]
    assert spacing[0] > dock_spacing[0] and frame[0] > dock_frame[0]
    assert inner[0] > dock_inner[0] and inner[1] > dock_inner[1]
    assert dock_edge[0] < edge[0] <= theme._FONT_SIZE
    assert dock_edge[1] < edge[1] <= edge[0]


def test_the_scope_is_ems_of_the_font_in_force_and_not_pixels(context: None) -> None:
    seen: dict[float, Style] = {}

    def at(size: float) -> None:
        def body() -> None:
            with widgets.dialog_style():
                seen[size] = _style()

        _frame(body, size=size)

    at(16.0)
    at(32.0)
    assert seen[32.0] == tuple((2.0 * x, 2.0 * y) for x, y in seen[16.0])


def test_leaving_the_scope_puts_the_docks_style_back_on_every_way_out(context: None) -> None:
    """
    A return, and an exception from inside it. What is left on the stack by a scope
    that pops less than it pushed is the modal's style, on every dock, until Dear
    ImGui asserts.
    """
    dock = _style()

    def raising() -> None:
        with widgets.dialog_style():
            raise KeyError("from inside the scope")

    def returning() -> None:
        with widgets.dialog_style():
            return

    _frame(returning)
    assert _style() == dock
    with pytest.raises(KeyError):
        _frame(raising)
    assert _style() == dock


def test_a_push_left_on_the_stack_is_an_assertion_in_dear_imgui(context: None) -> None:
    """
    The hazard the scope exists for, shown to be one: this is what a draw that
    pushed and returned early would do to the application.
    """
    imgui.new_frame()
    imgui.begin("a window")
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(1.0, 1.0))
    with pytest.raises(RuntimeError, match="PopStyleVar"):
        imgui.end()


# -- the gaps ------------------------------------------------------------------------


def _gaps(between: Callable[[], None], *, scoped: bool = True) -> float:
    """The room, in pixels, ``between`` leaves from one line of text to the next."""
    found: list[float] = []

    def body() -> None:
        imgui.begin("gaps")
        imgui.text("above")
        above = imgui.get_item_rect_max().y
        between()
        imgui.text("below")
        found.append(imgui.get_item_rect_min().y - above)
        imgui.end()

    def scoped_body() -> None:
        with widgets.dialog_style():
            body()

    _frame(scoped_body if scoped else body)
    return found[0]


def test_a_caption_a_field_and_a_group_are_three_distances_in_that_order(context: None) -> None:
    em = theme._FONT_SIZE
    item = _gaps(lambda: None)
    caption = _gaps(widgets.dialog_caption)
    field = _gaps(widgets.dialog_field)
    group = _gaps(lambda: widgets.dialog_group(rule=False))
    ruled = _gaps(widgets.dialog_group)

    assert item == widgets.DIALOG_ITEM_SPACING_EMS[1] * em
    assert caption == widgets._DIALOG_CAPTION_EMS * em
    assert field == widgets._DIALOG_FIELD_EMS * em
    assert group == widgets._DIALOG_GROUP_EMS * em
    assert 0.0 < caption < item < field <= group
    # The rule, and the same room on each side of it.
    assert ruled == 2.0 * group + imgui.get_style().separator_size


def test_the_gap_helpers_leave_the_item_spacing_as_they_found_it(context: None) -> None:
    after: list[Style] = []

    def body() -> None:
        with widgets.dialog_style():
            imgui.begin("gaps")
            inside = _style()
            imgui.text("above")
            widgets.dialog_caption()
            imgui.text("a caption")
            widgets.dialog_field()
            imgui.text("a field")
            widgets.dialog_group()
            imgui.text("a group")
            after.append(_style())
            assert after[0] == inside
            imgui.end()

    _frame(body)
    assert after


# -- both dialogs --------------------------------------------------------------------


class _Noting:
    """
    Dear ImGui, noting the style when the modal begins and when a button inside it
    is drawn, and the rectangle every item the dialog draws was given, in order.
    """

    _ITEMS = (
        "text",
        "text_disabled",
        "text_colored",
        "combo",
        "input_int",
        "input_text_with_hint",
        "collapsing_header",
        "tree_node",
        "small_button",
    )

    def __init__(self) -> None:
        self.internal = _NotingInternal(self)
        self.at_begin: list[Style] = []
        self.at_button: list[Style] = []
        # This frame's items: what names each, its top edge and its bottom edge.
        self.items: list[tuple[str, float, float]] = []
        self.refuse_to_open = False
        self.raise_at_begin = False

    def __getattr__(self, name: str) -> object:
        real = getattr(imgui, name)
        if name not in self._ITEMS:
            return real

        def noted(*args: object) -> object:
            answer = real(*args)
            # The label, or for a line of text the text; a coloured line's is second.
            self.note(str(args[1] if name == "text_colored" else args[0]))
            return answer

        return noted

    def note(self, name: str) -> None:
        self.items.append((name, imgui.get_item_rect_min().y, imgui.get_item_rect_max().y))

    def begin_popup_modal(self, *args: object) -> tuple[bool, bool | None]:
        self.items.clear()
        self.at_begin.append(_style())
        if self.raise_at_begin:
            raise KeyError("from inside the draw")
        if self.refuse_to_open:
            return False, None
        return imgui.begin_popup_modal(*args)  # type: ignore[arg-type]

    def button(self, label: str, size: object = None) -> bool:
        # A dialog's own buttons, each given a size.
        if size is not None:
            self.at_button.append(_style())
        pressed = bool(imgui.button(label, size))  # type: ignore[arg-type]
        self.note(label)
        return pressed

    def _at(self, name: str) -> int:
        exact = [at for at, item in enumerate(self.items) if item[0] == name]
        if exact:
            (found,) = exact
            return found
        (found,) = [at for at, item in enumerate(self.items) if item[0].startswith(name)]
        return found

    def before(self, name: str) -> float:
        """The room between the item named and the one drawn before it."""
        at = self._at(name)
        return self.items[at][1] - self.items[at - 1][2]

    def after(self, name: str) -> float:
        """The room between the item named and the one drawn after it."""
        at = self._at(name)
        return self.items[at + 1][1] - self.items[at][2]


class _NotingInternal:
    """
    ``imgui.internal``, noting the item ``widgets.small_button`` draws through it.
    That button is drawn under its own padding, not the scope's, so no style is
    noted at it.
    """

    def __init__(self, noting: _Noting) -> None:
        self.noting = noting

    def __getattr__(self, name: str) -> object:
        return getattr(imgui.internal, name)

    def button_ex(self, label: str, *args: Any) -> bool:
        pressed = bool(imgui.internal.button_ex(label, *args))
        self.noting.note(label)
        return pressed


class _Dialog:
    """One of the two dialogs, drawn at the root of a frame."""

    def __init__(
        self, title: str, noting: _Noting, draw: Callable[[], None], is_open: Callable[[], bool]
    ) -> None:
        self.title = title
        self.noting = noting
        # Whichever of the two this is.
        self.launcher: LauncherState | None = None
        self.form: ResumeForm | None = None
        self._draw = draw
        self.is_open = is_open

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            _frame(self._draw)


def _note_the_box(monkeypatch: pytest.MonkeyPatch, noting: _Noting) -> None:
    """The text box is drawn through ``widgets``, so that is where it is noted."""
    real = widgets.multiline_input

    def noted(label: str, *args: Any, **kwargs: Any) -> tuple[bool, str]:
        answer = real(label, *args, **kwargs)
        noting.note(label)
        return answer

    monkeypatch.setattr(widgets, "multiline_input", noted)


def _note_the_links(monkeypatch: pytest.MonkeyPatch, noting: _Noting) -> None:
    """
    A question mark is a ``widgets.link``, drawn through ``imgui.internal`` with no
    label a call there carries, so it is noted where it is drawn, by its label.
    """
    real = widgets.link

    def noted(label: str, *args: Any, **kwargs: Any) -> bool:
        answer = real(label, *args, **kwargs)
        noting.note(label)
        return answer

    monkeypatch.setattr(widgets, "link", noted)


def _launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, policy: Policy = Policy.STRICT
) -> _Dialog:
    noting = _Noting()
    monkeypatch.setattr(launcher, "imgui", noting)
    # The one line under a control, its question mark and the body's child window
    # are drawn by ``widgets``.
    monkeypatch.setattr(widgets, "imgui", noting)
    _note_the_box(monkeypatch, noting)
    _note_the_links(monkeypatch, noting)
    state = LauncherState(task="do the thing\nand why", cwd=str(tmp_path), policy=policy)
    worker = state.version_gate.request(prober=_meets)
    assert worker is not None
    worker.join(timeout=5.0)
    state.version_gate.poll()
    state.request_open()

    def draw() -> None:
        launcher.draw(
            state,
            running=0,
            queued=0,
            cap=4,
            launch=lambda spec, draft: None,
            wrap=True,
            scanner=lambda cwd: ((), {}),
            prober=_meets,
            subagent_cap=4,
        )

    made = _Dialog(launcher.TITLE, noting, draw, lambda: state.is_open)
    made.launcher = state
    return made


def _resume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Dialog:
    noting = _Noting()
    monkeypatch.setattr(resume_form, "imgui", noting)
    monkeypatch.setattr(widgets, "imgui", noting)
    _note_the_box(monkeypatch, noting)
    _note_the_links(monkeypatch, noting)
    work = tmp_path / "work"
    work.mkdir()
    forms = ResumeForms()
    forms.open_for(
        session_id=SESSION,
        as_policy=None,
        cwd=str(work),
        task="rebuild the parser\nand say why",
        template="review",
        policy=Policy.STRICT,
        contained=False,
        model="claude-test-model",
        subagent_cap=3,
        brief=None,
        last_message=None,
    )

    def draw() -> None:
        resume_form.draw(
            forms,
            launch=lambda press: None,
            wrap=True,
            prober=_meets,
            subagent_cap=4,
            checkout=str(tmp_path / "own"),
        )

    made = _Dialog(resume_form.TITLE, noting, draw, lambda: forms.is_open)
    made.form = forms.forms[SESSION]
    return made


_DIALOGS = {"the launcher": _launcher, "the resume form": _resume}


@pytest.fixture(params=sorted(_DIALOGS))
def dialog(
    request: pytest.FixtureRequest, context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> _Dialog:
    made: _Dialog = _DIALOGS[request.param](monkeypatch, tmp_path)
    return made


def _scope() -> Style:
    """What ``widgets.dialog_style`` sets at the body face's size."""
    seen: list[Style] = []

    def body() -> None:
        with widgets.dialog_style():
            seen.append(_style())

    _frame(body)
    return seen[0]


def test_the_dialog_begins_and_draws_its_buttons_under_the_shared_scope(dialog: _Dialog) -> None:
    """
    At the begin, which is when the window takes its padding, and at the buttons,
    which are the last things drawn: the scope holds from one to the other. Both
    dialogs are held to the one definition, so neither can drift from the other.
    """
    dock = _style()
    scope = _scope()
    assert scope != dock

    dialog.frame(3)
    assert dialog.is_open()
    assert dialog.noting.at_begin and dialog.noting.at_button
    assert set(dialog.noting.at_begin) == {scope}
    assert set(dialog.noting.at_button) == {scope}


def test_the_dialogs_window_has_the_scopes_padding_and_not_an_edge_of_its_own(
    dialog: _Dialog,
) -> None:
    dialog.frame(3)
    window = imgui.internal.find_window_by_name(dialog.title)
    assert window is not None and window.was_active
    assert (window.window_padding.x, window.window_padding.y) == _scope()[3]


def test_the_docks_style_is_what_it_was_after_a_frame_with_the_dialog_in_it(
    dialog: _Dialog,
) -> None:
    dock = _style()
    dialog.frame(3)
    assert dialog.is_open()
    assert _style() == dock


def test_a_dialog_that_does_not_open_this_frame_still_leaves_the_scope(dialog: _Dialog) -> None:
    """
    Closed from outside between two frames: ``begin_popup_modal`` answers False and
    the draw returns from the middle of itself, with the scope entered.
    """
    dock = _style()
    dialog.frame(3)
    dialog.noting.refuse_to_open = True
    entered = len(dialog.noting.at_begin)
    dialog.frame()

    assert len(dialog.noting.at_begin) == entered + 1
    assert dialog.noting.at_begin[-1] != dock
    assert not dialog.is_open()
    assert _style() == dock
    # And a frame later nothing is owed: an unbalanced stack asserts here.
    dialog.frame(2)
    assert _style() == dock


def test_a_dialog_dismissed_in_the_middle_of_a_frame_leaves_the_scope(dialog: _Dialog) -> None:
    dock = _style()
    dialog.frame(3)
    imgui.get_io().add_key_event(imgui.Key.escape, True)
    dialog.frame()
    imgui.get_io().add_key_event(imgui.Key.escape, False)
    dialog.frame(2)

    assert not dialog.is_open()
    assert _style() == dock


def test_a_draw_that_raises_leaves_the_scope(dialog: _Dialog) -> None:
    dock = _style()
    dialog.frame(3)
    dialog.noting.raise_at_begin = True
    with pytest.raises(KeyError):
        dialog.frame()
    assert dialog.noting.at_begin[-1] != dock
    assert _style() == dock


def test_a_closed_dialog_pushes_nothing(dialog: _Dialog) -> None:
    dialog.frame(3)
    imgui.get_io().add_key_event(imgui.Key.escape, True)
    dialog.frame()
    imgui.get_io().add_key_event(imgui.Key.escape, False)
    dialog.frame()
    entered = len(dialog.noting.at_begin)
    dialog.frame(3)
    assert len(dialog.noting.at_begin) == entered


# -- a label belongs to its control --------------------------------------------------


def _px(ems: float) -> float:
    return float(round(ems * theme._FONT_SIZE))


def _ruled() -> float:
    """What ``widgets.dialog_group`` leaves between two items, rule included."""
    return 2.0 * _px(widgets._DIALOG_GROUP_EMS) + imgui.get_style().separator_size


def _distances() -> tuple[float, float, float, float]:
    """A caption's, an item's, a field's and a ruled group's, smallest first."""
    caption = _px(widgets._DIALOG_CAPTION_EMS)
    item = _px(widgets.DIALOG_ITEM_SPACING_EMS[1])
    field = _px(widgets._DIALOG_FIELD_EMS)
    assert caption < item < field < _ruled()
    return caption, item, field, _ruled()


def test_the_launcher_is_five_groups_and_each_caption_is_under_its_own_control(
    context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Each dim line describes the control over it. The same gap on both sides, or a
    smaller one below, reads as a label for the next control down -- so each is a
    caption's distance under its control, and what follows it is a field's distance
    away within the group and a rule away where the next group starts.
    """
    dialog = _launcher(monkeypatch, tmp_path)
    dialog.frame(3)
    room = dialog.noting.before
    caption, item, field, ruled = _distances()
    dial = launcher.dial_text(Policy.STRICT, Policy.STRICT)

    # What it is to do.
    assert room("what should a new session do?") == caption
    # Where it runs.
    assert room("##cwd") == ruled
    assert room("working directory") == caption
    assert room("resume an existing session instead") == field
    assert room("##brief") == field
    assert room("brief directory") == caption
    # Who runs it.
    assert room("model") == ruled
    assert room("team") == item
    assert dialog.noting.after("team") == caption
    # The gate: one line under the dial, and what the rung still parks is not drawn.
    assert room("gate") == ruled
    assert room(dial.adds) == caption
    assert dial.parks not in [name for name, _, _ in dialog.noting.items]
    assert room("sub-agents") == field
    assert room("sub-agents one session may run at once") == caption
    # The buttons.
    assert room("launch") == ruled


def test_the_top_rung_adds_one_line_under_the_dial_and_no_section(
    context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The alarm is the line under the dial, on a band: the band is a caption's
    distance under the dial and the next field a field's distance under the band,
    which is the frame's padding taller than the line in it at each end. Nothing
    is ruled off under it, and the body is no taller than the display leaves it.
    """
    dialog = _launcher(monkeypatch, tmp_path, Policy.AUTONOMOUS)
    dialog.frame(4)
    room = dialog.noting.before
    caption, _, field, ruled = _distances()
    pad = _px(widgets.DIALOG_FRAME_PADDING_EMS[1])

    # Its start: this face is wider than the application's, and the line is cut
    # to the row rather than wrapped.
    assert room(launcher.LOUD_LINE[:24]) == caption + pad
    # Its foot: the band is the frame's padding under its line, and the next field
    # a field's distance under that. The question mark on the line is a link, as
    # tall as the line, and held inside the band.
    items = dialog.noting.items
    _, top, foot = items[dialog.noting._at(launcher.LOUD_LINE[:24])]
    assert items[dialog.noting._at("sub-agents")][1] - foot == pad + field
    _, mark_top, mark_foot = items[dialog.noting._at(f"{widgets.HELP_MARK}##")]
    assert top - pad <= mark_top and mark_foot <= foot + pad
    assert mark_foot - mark_top == foot - top
    assert dialog.noting.after("sub-agents one session may run at once") == ruled
    (body,) = [
        window
        for window in imgui.get_current_context().windows
        if window.name.startswith(f"{launcher.TITLE}/{launcher.BODY_ID}")
        and window.name.count("/") == 1
    ]
    assert body.scroll_max.y == 0.0
    launch_top = dialog.noting.items[dialog.noting._at("launch")][1]
    assert launch_top - (body.pos.y + body.size.y) == ruled


def test_a_failure_notice_is_set_off_from_the_launchers_fields_by_a_rule(
    context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dialog = _launcher(monkeypatch, tmp_path)
    assert dialog.launcher is not None
    dialog.launcher.report_failure(None, "no session started")
    dialog.frame(3)
    assert dialog.noting.after("dismiss this notice") == _distances()[3]
    assert dialog.noting.before(launcher._TASK_ID) == _distances()[3]


def test_the_resume_form_is_four_groups_and_each_caption_is_under_its_own_control(
    context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dialog = _resume(monkeypatch, tmp_path)
    dialog.frame(3)
    room = dialog.noting.before
    caption, item, _, ruled = _distances()
    dial = launcher.dial_text(Policy.STRICT, Policy.STRICT)

    # What is settled, then the message.
    assert room(resume_form._MESSAGE_ID) == ruled
    assert room("sent to the session as its next message") == caption
    # The gate.
    assert room("gate") == ruled
    assert room(dial.adds) == caption
    # What runs it.
    assert room("model") == ruled
    assert room("sub-agents") == item
    # The buttons, set off as the launcher's are.
    assert room("resume") == ruled


# -- one application -----------------------------------------------------------------


def _widths(dialog: _Dialog, monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """The width each combo and stepper of a dialog was drawn at, by its label."""
    noting = dialog.noting
    asked: list[float] = []
    drawn: dict[str, float] = {}

    def set_next_item_width(width: float) -> None:
        asked.append(width)
        imgui.set_next_item_width(width)

    def field(name: str) -> Callable[..., object]:
        real = getattr(noting, name)

        def noted(label: str, *args: object) -> object:
            drawn[label] = asked.pop() if asked else 0.0
            asked.clear()
            return real(label, *args)

        return noted

    monkeypatch.setattr(noting, "set_next_item_width", set_next_item_width, raising=False)
    for name in ("combo", "input_int"):
        monkeypatch.setattr(noting, name, field(name), raising=False)
    dialog.frame(3)
    return drawn


def test_every_combo_and_stepper_of_both_dialogs_is_one_width(
    context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A width nobody asked for is Dear ImGui's default, a share of the window's,
    which is how one dialog's gate came to be half as wide again as the other's.
    """
    opened = _launcher(monkeypatch, tmp_path)
    in_launcher = _widths(opened, monkeypatch)
    assert opened.launcher is not None
    opened.launcher.is_open = False
    resume = _resume(monkeypatch, tmp_path)
    assert resume.form is not None
    object.__setattr__(resume.form._facts, "template", resume_form.UnknownTeam())
    in_resume = _widths(resume, monkeypatch)

    assert set(in_launcher) == {"model", "team", "gate", "sub-agents"}
    assert set(in_resume) == {"##team", "gate", "model", "sub-agents"}
    assert set(in_launcher.values()) | set(in_resume.values()) == {widgets.DIALOG_FIELD_WIDTH}


def test_both_dialogs_are_one_width_and_as_tall_as_what_they_hold(
    dialog: _Dialog,
) -> None:
    """
    Neither is a fixed height: the window ends the window's padding under its
    buttons, and the body is as tall as what is drawn in it, so there is no empty
    band under the last control or under the buttons.
    """
    dialog.frame(4)
    window = imgui.internal.find_window_by_name(dialog.title)
    assert window is not None
    assert window.size.x == widgets.DIALOG_WIDTH
    (body,) = [
        child
        for child in imgui.get_current_context().windows
        if child.name.startswith(f"{dialog.title}/") and child.name.count("/") == 1
    ]
    assert body.scroll_max.y == 0.0
    assert body.size.y == body.content_size.y
    button = "launch" if dialog.launcher is not None else "resume"
    bottom = dialog.noting.items[dialog.noting._at(button)][2]
    assert window.pos.y + window.size.y - bottom == _px(widgets.DIALOG_WINDOW_PADDING_EMS[1])


def test_a_failure_notice_is_set_off_from_the_resume_forms_facts_by_a_rule(
    context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dialog = _resume(monkeypatch, tmp_path)
    assert dialog.form is not None
    dialog.form.failed(dialog.form.draft(), "no session started")
    dialog.frame(3)
    assert dialog.noting.after("dismiss this notice") == _distances()[3]


# -- reachable -----------------------------------------------------------------------


def test_the_resume_forms_buttons_are_inside_a_window_too_short_for_its_body(
    context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The scope makes the body taller. The body scrolls and the footer does not, so
    on a display shorter than the form the window is the display's height and the
    buttons are inside it.
    """
    imgui.get_io().display_size = imgui.ImVec2(1280.0, 420.0)
    dialog = _resume(monkeypatch, tmp_path)
    places: dict[str, float] = {}
    real_button = dialog.noting.button

    def button(label: str, size: object = None) -> bool:
        pressed = real_button(label, size)
        places[label] = imgui.get_item_rect_max().y
        return pressed

    monkeypatch.setattr(dialog.noting, "button", button, raising=False)
    dialog.frame(4)

    window = imgui.internal.find_window_by_name(resume_form.TITLE)
    assert window is not None
    assert window.pos.y >= 0.0 and window.pos.y + window.size.y <= 420.0
    assert places["resume"] <= window.pos.y + window.size.y
    assert places["cancel"] <= window.pos.y + window.size.y
