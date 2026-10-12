"""
The settings dialog: its model without a window, and its modal in a real frame.

Two halves, as the module has. The model is values -- ``build(Settings, Facts)`` --
so what a row offers, what it shows as in force and why it is disabled are tested
with no ImGui at all. The modal reads the keyboard and the mouse from ImGui's own
IO, so those tests run a headless context and inject events, as
``tests/test_review_keys.py`` does: a stand-in ``imgui`` would only assert what the
stand-in was told to say about a chord, a popup or a click.

**No test here can write the operator's settings file.** ``settings.save`` is
replaced by a recorder for every test, and ``XDG_CONFIG_HOME`` points at the test's
own temporary directory underneath it, so a path that reached the real ``save``
would still land there.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from pptmstr import app, theme
from pptmstr import settings as settings_mod
from pptmstr.bridge import Bridge
from pptmstr.settings import McpConnector, RoleChoice, Settings
from pptmstr.store import Store
from pptmstr.ui import settings_dialog as dialog
from pptmstr.ui import transcript_pane, typeset
from pptmstr.ui.settings_dialog import (
    Choice,
    Facts,
    FieldSet,
    Model,
    PaneSet,
    Restored,
    RoleSet,
    Toggle,
    build,
    changed,
)
from pptmstr.ui.typeset import views
from tests.conftest import needs_the_library

BOLD = theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf"
GONE = "the text library did not import (ImportError: gone)"


@pytest.fixture(autouse=True)
def saved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Settings]:
    """
    Every save a test causes, and nothing on disk outside its own directory.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    record: list[Settings] = []
    monkeypatch.setattr(settings_mod, "save", lambda settings, path=None: record.append(settings))
    return record


def choices(model: Model) -> list[Choice]:
    out = [model.theme, model.scale, model.family, model.context_mode, *model.caps]
    for row in model.roles:
        out += [row.face, row.size]
    return out


def toggles(model: Model) -> list[Toggle]:
    return [model.typeset, model.wrap, *model.pane_switches]


def choice(model: Model, key: str) -> Choice:
    return next(each for each in choices(model) if each.key == key)


def shown(model: Model, key: str) -> str:
    held = choice(model, key)
    return held.options[held.current].label


def pick(settings: Settings, key: str, label: str) -> Settings:
    """
    The settings after choosing the entry ``label`` of the row ``key``.
    """
    held = choice(build(settings), key)
    option = next(each for each in held.options if each.label == label)
    return changed(settings, option.change)


# -- the pure core -----------------------------------------------------------------


def test_every_persisted_field_has_one_place_in_the_dialog() -> None:
    """
    "Comprehensive" is this: a field added to ``Settings`` and to no section is a
    preference that can be saved from nowhere in the window.
    """
    placed = [name for owned in dialog.SECTIONS.values() for name in owned]
    placed += list(dialog.READ_ONLY)
    assert sorted(placed) == sorted(each.name for each in fields(Settings))
    assert len(placed) == len(set(placed)), "a field two sections restore"


def test_the_defaults_are_every_row_at_its_default_and_nothing_to_restore() -> None:
    model = build(Settings())

    assert shown(model, "theme") == "Dark"
    assert shown(model, "scale") == "100%"
    assert shown(model, "family") == "Roboto"
    assert model.typeset.on and model.typeset.disabled is None and model.typeset.note == ""
    assert model.wrap.on
    assert [each.label for each in model.pane_switches] == [
        "DETAIL",
        "NEEDS YOU",
        "CONTEXT",
        "BOARD",
        "BRIEF",
    ]
    assert all(each.on and each.disabled is None for each in model.pane_switches)
    assert shown(model, "context-mode") == f"this build's ({transcript_pane.DEFAULT_MODE.value})"
    assert [shown(model, each.key) for each in model.caps] == ["4", "8", "9"]
    assert model.previews
    assert all(
        section.at_default
        for section in (model.appearance, model.text, model.panes, model.sessions)
    )
    assert model.connectors == ()


def test_the_role_table_is_the_seams_catalogue_with_what_each_role_may_be_given() -> None:
    """
    One row a role, in the seam's order. A role whose columns are content is shown
    its one family and offered no other, with the reason; every role has every size.
    """
    model = build(Settings())

    assert [row.role for row in model.roles] == [role.value for role in typeset.Role]
    assert [row.label for row in model.roles] == [
        typeset.ROLE_LABELS[role] for role in typeset.Role
    ]
    for row in model.roles:
        role = typeset.Role(row.role)
        assert [each.label for each in row.face.options] == [
            slot.value for slot in typeset.SLOTS[role]
        ]
        assert row.face.options[row.face.current].label == typeset.DEFAULTS[role].slot.value
        assert [each.label for each in row.size.options] == [
            "-2 px",
            "-1 px",
            "body",
            "+1 px",
            "+2 px",
            "+3 px",
            "+4 px",
        ]
        assert row.size.options[row.size.current].label == "body"
        assert row.size.disabled is None
    two = {row.role for row in model.roles if row.face.disabled is None}
    assert two == {"prose", "heading", "table", "label"}
    assert {row.face.disabled for row in model.roles if row.role not in two} == {dialog.ONE_SLOT}


def test_choosing_any_entry_of_any_row_puts_that_entry_in_force() -> None:
    """
    Every option carries the change that picking it makes. Walked whole: a row
    whose change named the wrong field would show the old entry after the click.
    """
    start = Settings()
    walked = 0
    for held in choices(build(start)):
        for index, option in enumerate(held.options):
            after = build(changed(start, option.change))
            assert choice(after, held.key).current == index, (held.key, option.label)
            walked += 1
    # 9 themes, 4 sizes, 2 families, 5 modes, 8 + 7 + 5 caps, 9 roles of 7 sizes,
    # and a face a slot: the walk covered the dialog and not an empty list.
    assert walked == (
        len(theme.THEMES)
        + len(typeset.SCALES)
        + len(typeset.PROPORTIONALS)
        + len(transcript_pane.RenderMode)
        + 1
        + 8
        + 7
        + 5
        + len(typeset.Role) * len(typeset.STEPS)
        + sum(len(slots) for slots in typeset.SLOTS.values())
    )


def test_a_click_on_any_switch_flips_that_switch_and_no_other() -> None:
    start = Settings()
    before = {each.key: each.on for each in toggles(build(start))}
    for held in toggles(build(start)):
        after = {each.key: each.on for each in toggles(build(changed(start, held.flip)))}
        # Typesetting switched off takes nothing else's tick with it: the pane
        # switches keep what they say and are disabled instead.
        assert after == {**before, held.key: not before[held.key]}, held.key


def test_a_change_is_the_setting_the_seam_and_the_file_read(tmp_path: Path) -> None:
    """
    The values, spelled as the file keeps them: what ``changed`` returns is what is
    saved, so a change that built a tuple where ``load`` builds a string would
    round-trip as a different setting.
    """
    got = Settings()
    got = pick(got, "theme", "Light")
    got = pick(got, "scale", "125%")
    got = pick(got, "family", "Droid Sans (Roboto emphasis)")
    got = pick(got, "face-prose", "monospace")
    got = pick(got, "size-prose", "+2 px")
    got = pick(got, "size-diff", "-1 px")
    got = pick(got, "context-mode", "raw")
    got = pick(got, "concurrency_cap", "6")
    got = pick(got, "subagent_cap", "0")
    got = pick(got, "fps_idle", "15")
    got = changed(got, PaneSet("board", False))
    got = changed(got, FieldSet("wrap_inputs", False))

    assert got == Settings(
        theme="light",
        typeset_scale=1.25,
        typeset_family="droid",
        typeset_roles=(RoleChoice("diff", None, -1), RoleChoice("prose", "monospace", 2)),
        typeset_panes=(("board", False),),
        context_mode="raw",
        concurrency_cap=6,
        subagent_cap=0,
        fps_idle=15.0,
        wrap_inputs=False,
    )
    assert type(got.concurrency_cap) is int and type(got.fps_idle) is float
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings_mod.encoded(got)))
    assert settings_mod.load(path) == got


def test_a_sections_defaults_restore_that_section_and_leave_the_rest() -> None:
    held = (
        Settings(theme="light", typeset_scale=1.25, wrap_inputs=False, concurrency_cap=2)
        .with_role("prose", slot="monospace")
        .with_pane("board", False)
    )
    held = changed(held, FieldSet("context_mode", "raw"))
    model = build(held)
    assert not any(
        section.at_default
        for section in (model.appearance, model.text, model.panes, model.sessions)
    )

    text = changed(held, model.text.restore)
    assert text.typeset_roles == () and text.typeset_family == "roboto" and text.typeset
    assert (text.theme, text.typeset_scale, text.typeset_panes) == (
        "light",
        1.25,
        (("board", False),),
    )
    assert build(text).text.at_default and not build(text).appearance.at_default

    looks = changed(held, model.appearance.restore)
    assert (looks.theme, looks.typeset_scale) == ("dark", 1.0)
    assert looks.typeset_roles == held.typeset_roles

    panes = changed(held, model.panes.restore)
    assert panes.typeset_panes == () and panes.context_mode == ""
    assert panes.wrap_inputs is False

    rest = changed(held, model.sessions.restore)
    assert (rest.wrap_inputs, rest.concurrency_cap) == (True, Settings().concurrency_cap)
    assert rest.theme == "light"


def test_a_role_put_back_by_hand_is_a_section_at_its_defaults() -> None:
    """
    ``with_role`` keeps a choice it was given, so prose set monospace and back is
    ``RoleChoice("prose", "proportional")`` and not an empty tuple. The button is
    dead by what is in force and not by how the tuple happens to be spelled.
    """
    back = Settings().with_role("prose", slot="monospace").with_role("prose", slot="proportional")
    assert back.typeset_roles != ()
    assert build(back).text.at_default
    assert build(Settings().with_pane("board", False).with_pane("board", True)).panes.at_default


def test_a_change_this_build_cannot_place_is_the_settings_unchanged() -> None:
    """
    The reducer's rule: an intent for something that does not exist is a no-op.
    A ``FieldSet`` may not reach a structured field either, where it would put a
    string in a tuple's place.
    """
    start = Settings().with_role("prose", step=1)
    for stray in (
        FieldSet("no_such_field", 1),
        FieldSet("typeset_roles", "monospace"),
        FieldSet("typeset_panes", True),
        FieldSet("admitted_connectors", ()),
        Restored("no-such-section"),
    ):
        assert changed(start, stray) == start, stray


# -- what the process cannot do ------------------------------------------------------


def test_text_that_cannot_be_typeset_is_every_row_disabled_with_the_reason_and_none_hidden() -> (
    None
):
    """
    With the library absent the section is still all there: the operator sees
    what they would have, and the one sentence that says why they do not.
    """
    there = build(Settings())
    model = build(Settings(), Facts(available=False, obtainable=False, reason=GONE))

    assert [each.key for each in choices(model)] == [each.key for each in choices(there)]
    assert [row.role for row in model.roles] == [row.role for row in there.roles]
    assert (model.typeset.on, model.typeset.disabled, model.typeset.note) == (False, GONE, GONE)
    assert model.family.disabled == GONE and model.scale.disabled == GONE
    assert all(each.disabled == GONE for each in model.pane_switches)
    assert all(row.size.disabled == GONE for row in model.roles)
    assert {row.face.disabled for row in model.roles} == {GONE, dialog.ONE_SLOT}
    assert not model.previews, "a sample set through a seam with no faces raises"
    # What is not about typeset text is untouched.
    assert model.theme.disabled is None and model.wrap.disabled is None
    assert all(each.disabled is None for each in model.caps)
    assert model.context_mode.disabled is None
    typeset_entry = next(each for each in model.context_mode.options if each.label == "typeset")
    assert not typeset_entry.usable
    assert all(each.usable for each in model.context_mode.options if each.label != "typeset")


def test_faces_still_to_load_leave_the_switch_live_and_say_what_it_costs() -> None:
    cost = "faces load when this is switched on: about a second"
    model = build(Settings(typeset=False), Facts(available=False, obtainable=True, reason=cost))

    assert (model.typeset.on, model.typeset.disabled, model.typeset.note) == (False, None, cost)
    assert model.typeset.flip == FieldSet("typeset", True)
    assert model.family.disabled == cost and not model.previews


def test_typesetting_switched_off_disables_its_rows_and_keeps_the_switch() -> None:
    model = build(Settings(typeset=False).with_pane("brief", False))

    assert not model.typeset.on and model.typeset.disabled is None
    assert model.family.disabled == dialog.TEXT_OFF and model.scale.disabled == dialog.TEXT_OFF
    assert all(each.disabled == dialog.TEXT_OFF for each in model.pane_switches)
    assert [each.on for each in model.pane_switches] == [True, True, True, True, False]
    assert not model.previews


def test_a_family_whose_file_did_not_load_is_shown_and_cannot_be_chosen() -> None:
    model = build(Settings(typeset_family="droid"), Facts(unusable=frozenset({"droid"})))

    assert [(each.label, each.usable) for each in model.family.options] == [
        ("Roboto", True),
        ("Droid Sans (Roboto emphasis)", False),
    ]
    assert shown(model, "family") == "Roboto", "what is in force, as the seam falls back"


def test_the_typeset_log_is_not_offered_to_start_in_while_its_pane_is_switched_off() -> None:
    off = build(Settings().with_pane("context", False))
    entry = next(each for each in off.context_mode.options if each.label == "typeset")
    assert not entry.usable
    on = build(Settings().with_pane("board", False))
    assert next(each for each in on.context_mode.options if each.label == "typeset").usable


# -- settings this build did not write -------------------------------------------------


@pytest.mark.parametrize(
    ("old", "faces", "sizes", "family"),
    [
        (
            {"typeset_faces": "mono"},
            {"prose": "monospace", "heading": "monospace", "table": "monospace"},
            {},
            "Roboto",
        ),
        ({"typeset_mono": True}, {"prose": "monospace", "label": "monospace"}, {}, "Roboto"),
        (
            {"typeset_faces": "roboto-large"},
            {"prose": "proportional"},
            {"prose": "+2 px"},
            "Roboto",
        ),
        ({"typeset_faces": "droid"}, {}, {}, "Droid Sans (Roboto emphasis)"),
    ],
    ids=["mono-preset", "mono-switch", "roboto-large", "droid"],
)
def test_a_file_from_before_roles_opens_as_the_roles_it_meant(
    tmp_path: Path,
    old: dict[str, object],
    faces: dict[str, str],
    sizes: dict[str, str],
    family: str,
) -> None:
    """
    The operator's file predates this dialog. What it named as a preset is shown
    row by row, and the Text section knows it is not at its defaults.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "light", "typeset": True, **old}))
    loaded = settings_mod.load(path)
    model = build(loaded)

    assert shown(model, "theme") == "Light" and shown(model, "family") == family
    for role, face in faces.items():
        assert shown(model, f"face-{role}") == face, role
    for role, size in sizes.items():
        assert shown(model, f"size-{role}") == size, role
    assert shown(model, "face-diff") == "monospace" and shown(model, "size-diff") == "body"
    assert not model.text.at_default
    assert build(changed(loaded, model.text.restore)).text.at_default


def test_a_value_the_lists_do_not_offer_is_shown_as_it_is_and_kept() -> None:
    """
    A hand-edited cap, a scale from the command line, a family and a mode from a
    later build. The row shows what is in force; nothing is rewritten until a pick.
    """
    odd = Settings(
        theme="solarized",
        typeset_scale=1.1,
        typeset_family="comic",
        concurrency_cap=5,
        fps_idle=12.5,
        context_mode="hologram",
        typeset_roles=(RoleChoice("prose", "cursive", 9), RoleChoice("ghost", "monospace", 1)),
        typeset_panes=(("holodeck", False),),
    )
    model = build(odd)

    assert shown(model, "theme") == "Dark", "an unknown theme is drawn as dark"
    assert [each.label for each in model.scale.options] == [
        "87.5%",
        "100%",
        "110%",
        "112.5%",
        "125%",
    ]
    assert shown(model, "scale") == "110%"
    assert shown(model, "family") == "Roboto"
    assert shown(model, "concurrency_cap") == "5" and shown(model, "fps_idle") == "12.5"
    assert shown(model, "context-mode").startswith("this build's")
    assert shown(model, "face-prose") == "proportional" and shown(model, "size-prose") == "+4 px"
    assert all(each.on for each in model.pane_switches)
    assert not model.appearance.at_default and not model.text.at_default

    out_of_range = build(Settings(typeset_scale=9.0))
    assert shown(out_of_range, "scale") == "125%", "as the seam clamps it"
    assert shown(build(Settings(typeset_scale=float("nan"))), "scale") == "100%"


def test_admitted_connectors_are_listed_and_the_dialog_says_where_they_are_edited() -> None:
    held = Settings(
        admitted_connectors=(McpConnector("linear", "https://mcp.linear.app/mcp", "mcpsrv_1"),)
    )
    model = build(held, Facts(path="/home/op/.config/pptmstr/settings.json"))

    assert model.connectors == ("linear  https://mcp.linear.app/mcp",)
    assert "mcpsrv_1" not in "".join(model.connectors), "an account id is not for a screen"
    assert "admitted_connectors" in model.connectors_note
    assert "/home/op/.config/pptmstr/settings.json" in model.connectors_note
    # No change the model offers reaches them.
    offered = [option.change for each in choices(model) for option in each.options]
    offered += [each.flip for each in toggles(model)]
    offered += [model.text.restore, model.panes.restore, model.sessions.restore]
    assert all(
        changed(held, each).admitted_connectors == held.admitted_connectors for each in offered
    )


# -- a change reaches the settings, the file and the seam ------------------------------


def state_for(settings: Settings | None = None) -> app.AppState:
    held = Settings() if settings is None else settings
    app._configure_typeset(held)
    return app.AppState(store=Store(), bridge=Bridge(), settings=held)


def apply_to(state: app.AppState) -> Any:
    """
    The callback ``app._draw_overlays`` gives the dialog, built the same way.
    """
    return lambda change: app._apply_settings(state, changed(state.settings, change))


def test_a_role_changed_in_the_dialog_is_saved_and_set_by_the_seam_at_once(
    saved: list[Settings],
) -> None:
    state = state_for()
    apply = apply_to(state)
    epoch = typeset._STATE.epoch
    model = build(state.settings)

    face = choice(model, "face-prose")
    apply(next(each.change for each in face.options if each.label == "monospace"))
    assert state.settings.typeset_roles == (RoleChoice("prose", "monospace", None),)
    assert saved == [state.settings]
    assert typeset.chosen(typeset.Role.PROSE).slot is typeset.Slot.MONOSPACE
    assert typeset._STATE.epoch == epoch + 1, "documents in the other face are stale"

    size = choice(build(state.settings), "size-heading")
    apply(next(each.change for each in size.options if each.label == "+3 px"))
    assert typeset.chosen(typeset.Role.HEADING) == typeset.RoleStyle(typeset.Slot.PROPORTIONAL, 3)
    assert typeset.chosen(typeset.Role.PROSE).slot is typeset.Slot.MONOSPACE, "and prose kept"
    assert len(saved) == 2 and typeset._STATE.epoch == epoch + 2

    apply(PaneSet("board", False))
    assert not typeset.pane_on(typeset.Pane.BOARD) and typeset.pane_on(typeset.Pane.DETAIL)
    assert typeset._STATE.epoch == epoch + 2, "a pane switched off rebuilds nothing"

    apply(build(state.settings).text.restore)
    assert typeset.chosen(typeset.Role.PROSE) == typeset.DEFAULTS[typeset.Role.PROSE]
    assert typeset.chosen(typeset.Role.HEADING) == typeset.DEFAULTS[typeset.Role.HEADING]
    assert not typeset.pane_on(typeset.Pane.BOARD), "the panes are another section's"


def test_the_seam_is_told_once_for_a_change_and_not_at_all_for_none(
    monkeypatch: pytest.MonkeyPatch, saved: list[Settings]
) -> None:
    """
    A change of family is a font registration and half a second. It is paid on
    the click that changes it: not again for the same entry, and not for a change
    to something the seam does not read.
    """
    state = state_for()
    apply = apply_to(state)
    told: list[dict[str, Any]] = []
    real = typeset.configure

    def configure(**kwargs: Any) -> None:
        told.append(kwargs)
        real(**kwargs)

    monkeypatch.setattr(typeset, "configure", configure)

    apply(FieldSet("typeset_family", "droid"))
    assert [each["proportional"] for each in told] == ["droid"] and len(saved) == 1

    apply(FieldSet("typeset_family", "droid"))
    apply(Restored("no-such-section"))
    assert len(told) == 1 and len(saved) == 1, "settings that did not move do nothing"

    apply(FieldSet("wrap_inputs", False))
    apply(FieldSet("theme", "light"))
    apply(FieldSet("subagent_cap", 2))
    assert len(told) == 1 and len(saved) == 4, "saved, and no business of the seam's"
    theme.set_theme("dark")


def test_the_note_under_the_master_switch_says_who_draws_a_diff_where_text_is_typeset() -> None:
    """
    One note, one line: why text is not typeset, and where it is, who draws a
    diff and by what measurement. An operator cannot tell either from the text.
    """
    measured = "diffs are the library's: it keeps a 8 px column: laid out, drawn and hit over a row"
    assert build(Settings(), Facts(diffs=measured)).typeset.note == measured
    assert build(Settings(), Facts()).typeset.note == ""
    gone = build(Settings(), Facts(available=False, obtainable=False, reason=GONE, diffs=measured))
    assert gone.typeset.note == GONE, "why there is no typeset text comes first"


# -- the modal, in a real frame ---------------------------------------------------------

CTRL = imgui.Key.mod_ctrl
COMMA = imgui.Key.comma
ESCAPE = imgui.Key.escape
DISPLAY = (1500.0, 900.0)
# The menu bar and the status bar, generously: under Xvfb the dock space the modal
# has to fit is 870 px of the 900.
CHROME = 60.0


class Rig:
    """
    The dialog drawn a frame at a time in a headless context, with real key and
    mouse events and where each control landed.

    ``rects`` is every checkbox, combo, button and list entry the last frame drew,
    by label, so a test clicks where the control is and not where it guesses.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, settings: Settings | None = None) -> None:
        self.state = state_for(settings)
        self.dialog = self.state.settings_dialog
        self.changes: list[dialog.Change] = []
        self.rects: dict[str, tuple[float, float]] = {}
        self.typing = False
        self.typed = ""
        self.blocked = False
        self.known: Any = dialog.facts
        self._focus_input = False

        def centre() -> tuple[float, float]:
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            return ((low.x + high.x) / 2, (low.y + high.y) / 2)

        def noting(name: str, opens: bool = False) -> None:
            real = getattr(imgui, name)

            def wrapped(label: str, *args: Any, **kwargs: Any) -> Any:
                out = real(label, *args, **kwargs)
                # A combo that opened has begun its list's window: the last item
                # is no longer the combo.
                if not (opens and out):
                    self.rects[label] = centre()
                return out

            monkeypatch.setattr(imgui, name, wrapped)

        noting("checkbox")
        noting("small_button")
        noting("button")
        noting("selectable")
        noting("begin_combo", opens=True)

    def apply(self, change: dialog.Change) -> None:
        self.changes.append(change)
        apply_to(self.state)(change)

    def frame(self) -> None:
        typeset.pre_frame()
        imgui.new_frame()
        self.rects.clear()
        try:
            if self.typing:
                imgui.set_next_window_pos(imgui.ImVec2(10.0, 10.0))
                imgui.begin("##reply")
                if self._focus_input:
                    imgui.set_keyboard_focus_here()
                    self._focus_input = False
                _, self.typed = imgui.input_text("##box", self.typed)
                self.input_active = imgui.is_item_active()
                imgui.end()
            dialog.handle_shortcut(self.dialog, blocked=self.blocked)
            dialog.draw(self.dialog, self.state.settings, self.apply, known=self.known)
            self.listed = imgui.is_popup_open(
                "", imgui.PopupFlags_.any_popup_id | imgui.PopupFlags_.any_popup_level
            )
            self.captured = imgui.get_io().want_capture_keyboard
        finally:
            imgui.render()

    def frames(self, count: int) -> None:
        for _ in range(count):
            self.frame()

    def press(self, *keys: imgui.Key) -> None:
        """
        ``keys`` down for a frame and up again, with a frame for ImGui to see it.
        """
        io = imgui.get_io()
        for key in keys:
            io.add_key_event(key, True)
        self.frame()
        for key in reversed(keys):
            io.add_key_event(key, False)
        self.frame()

    def click(self, label: str) -> None:
        io = imgui.get_io()
        assert label in self.rects, (label, sorted(self.rects))
        io.add_mouse_pos_event(*self.rects[label])
        self.frame()
        io.add_mouse_button_event(0, True)
        self.frame()
        io.add_mouse_button_event(0, False)
        self.frames(2)

    def type_in_a_box(self) -> None:
        self.typing = True
        self._focus_input = True
        self.frames(2)
        assert self.input_active and imgui.is_any_item_active()

    def open(self) -> None:
        self.dialog.request_open()
        self.frames(3)
        assert self.dialog.is_open

    def window(self) -> Any:
        return imgui.internal.find_window_by_name(dialog.TITLE)


@pytest.fixture(scope="module")
def context() -> Iterator[None]:
    """
    One headless context for the module with the UI font, and the library's
    fonts where there is a library: the samples need them.
    """
    import imgui_bundle

    made = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(*DISPLAY)
    io.delta_time = 1 / 60
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    medium = Path(imgui_bundle.__file__).parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"
    io.fonts.add_font_from_file_ttf(str(medium), 16.0)
    theme.set_theme("dark")
    # The application's paddings and spacings: what the dialog measures against.
    theme.apply_style()
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})
    typeset.load_fonts(BOLD)
    try:
        yield
    finally:
        typeset.unload()
        imgui.destroy_context(made)


@pytest.fixture
def typeset_live(context: None) -> None:
    """
    For a test of the rows about typeset text and of the samples: they are live
    only where the library can draw. Skips or fails where it cannot, as ``seam``
    does (``tests/conftest.py``).
    """
    if not typeset.available():
        needs_the_library(typeset.unavailable_reason())


@pytest.fixture
def rig(context: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[Rig]:
    """
    A rig over defaults, with the keyboard navigation the application runs with
    (``hello_imgui`` switches it on) and the dialog closed again afterwards: an open
    popup belongs to the context, which outlives the test.
    """
    theme.set_theme("dark")
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})
    io = imgui.get_io()
    io.config_flags |= imgui.ConfigFlags_.nav_enable_keyboard
    made = Rig(monkeypatch)
    made.frames(2)
    try:
        yield made
    finally:
        io.add_mouse_pos_event(-1.0, -1.0)
        io.add_mouse_button_event(0, False)
        for key in (COMMA, CTRL, ESCAPE):
            io.add_key_event(key, False)
        made.typing = False
        made.blocked = False
        for _ in range(4):
            made.dialog.request_close()
            made.press(ESCAPE)
        assert not made.dialog.is_open and not made.listed
        theme.set_theme("dark")
        typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})


def test_the_chord_opens_the_dialog_and_the_chord_closes_it(rig: Rig) -> None:
    assert not rig.dialog.is_open and not rig.listed

    rig.press(CTRL, COMMA)
    rig.frame()
    assert rig.dialog.is_open and rig.listed
    assert rig.captured, "the bare-letter review keys stand down under the modal"

    rig.press(CTRL, COMMA)
    rig.frame()
    assert not rig.dialog.is_open and not rig.listed
    assert rig.changes == [], "opening and closing changes nothing"


def test_a_comma_alone_or_under_another_modifier_does_not_open_it(rig: Rig) -> None:
    for keys in ((COMMA,), (imgui.Key.mod_shift, COMMA), (imgui.Key.mod_alt, COMMA)):
        rig.press(*keys)
        rig.frame()
        assert not rig.dialog.is_open, keys


def test_the_chord_does_not_fire_while_the_operator_is_typing(rig: Rig) -> None:
    """
    ImGui grants a global route over an active text field for any chord the field
    does not use itself, so without the guard the dialog opens over the reply
    being typed. The chord arriving at all is the first test's business.
    """
    rig.type_in_a_box()

    rig.press(CTRL, COMMA)
    rig.frame()

    assert rig.input_active, "the field kept the keyboard"
    assert not rig.dialog.is_open and not rig.dialog._open_requested


def test_the_chord_does_not_open_it_over_another_modal(rig: Rig) -> None:
    rig.blocked = True

    rig.press(CTRL, COMMA)
    rig.frame()

    assert not rig.dialog.is_open


def test_escape_closes_the_dialog_and_keeps_what_was_changed(
    rig: Rig, saved: list[Settings]
) -> None:
    rig.open()
    rig.click("wrap composers##wrap")
    assert rig.state.settings.wrap_inputs is False

    rig.press(ESCAPE)
    rig.frame()

    assert not rig.dialog.is_open and not rig.listed
    assert rig.state.settings.wrap_inputs is False and saved[-1].wrap_inputs is False


def test_the_close_button_closes_it(rig: Rig) -> None:
    rig.open()
    rig.click("close")
    assert not rig.dialog.is_open and not rig.listed


@pytest.mark.parametrize("navigation", [True, False], ids=["keyboard-nav", "no-keyboard-nav"])
def test_escape_over_an_open_list_closes_the_list_and_not_the_dialog(
    rig: Rig, navigation: bool
) -> None:
    """
    The Escape that backs out of a combo is not the one that leaves the dialog.
    Both ways ImGui can be configured: with keyboard navigation it closes the list
    itself before the dialog draws, and without it nothing does but the dialog.
    """
    io = imgui.get_io()
    if not navigation:
        io.config_flags &= ~imgui.ConfigFlags_.nav_enable_keyboard
    rig.open()
    rig.click("##theme")
    assert "Light" in rig.rects, "the list is open"

    rig.press(ESCAPE)
    rig.frame()
    assert "Light" not in rig.rects, "the list closed"
    assert rig.dialog.is_open and rig.listed, "and the dialog did not"
    assert rig.changes == []

    rig.press(ESCAPE)
    rig.frame()
    assert not rig.dialog.is_open


@pytest.mark.usefixtures("typeset_live")
def test_ticking_a_box_in_the_open_dialog_changes_that_setting_once(
    rig: Rig, saved: list[Settings]
) -> None:
    rig.open()

    rig.click("BOARD##pane-board")

    assert rig.changes == [PaneSet("board", False)]
    assert rig.state.settings.typeset_panes == (("board", False),)
    assert saved == [rig.state.settings]
    assert not typeset.pane_on(typeset.Pane.BOARD)
    # Held under the mouse for a while: a click is one change, not one a frame.
    rig.frames(5)
    assert len(rig.changes) == 1 and len(saved) == 1


@pytest.mark.usefixtures("typeset_live")
def test_picking_from_a_list_changes_that_setting_and_the_entry_in_force_changes_nothing(
    rig: Rig, saved: list[Settings]
) -> None:
    rig.open()
    epoch = typeset._STATE.epoch

    rig.click("##face-prose")
    rig.click("proportional")
    assert rig.changes == [] and saved == [], "the entry already in force"
    assert typeset._STATE.epoch == epoch

    rig.click("##face-prose")
    rig.click("monospace")
    assert rig.changes == [RoleSet("prose", slot="monospace")]
    assert typeset.mono() and typeset._STATE.epoch == epoch + 1
    assert len(saved) == 1
    rig.frames(5)
    assert len(rig.changes) == 1 and typeset._STATE.epoch == epoch + 1


def test_a_disabled_row_takes_no_click(rig: Rig, saved: list[Settings]) -> None:
    rig.state.settings = Settings(typeset=False)
    app._configure_typeset(rig.state.settings)
    rig.open()

    rig.click("BOARD##pane-board")
    rig.click("##family")

    assert rig.changes == [] and saved == []
    assert "Roboto" not in rig.rects, "its list did not open"


def test_an_entry_that_cannot_be_had_is_listed_and_takes_no_click(
    rig: Rig, saved: list[Settings]
) -> None:
    """
    With CONTEXT switched off the log cannot start typeset. The entry stays in the
    list, so the operator sees it exists, and a click on it is not a choice.
    """
    rig.state.settings = Settings().with_pane("context", False)
    app._configure_typeset(rig.state.settings)
    rig.open()

    rig.click("##context-mode")
    assert "typeset" in rig.rects
    rig.click("typeset")
    assert rig.changes == [] and saved == []

    rig.click("raw")
    assert rig.changes == [FieldSet("context_mode", "raw")]


def test_the_title_bars_close_box_closes_it_and_the_state_follows(rig: Rig) -> None:
    """
    ImGui closes the popup itself on its close box, and when another modal opened
    from the root replaces this one. Nothing in ``draw`` ran the close, so the
    state has to notice: left reading open, the layout keys defer for good to a
    dialog that is not there and the chord can no longer open it.
    """
    rig.open()
    window = rig.window()
    rig.rects["the close box"] = (window.pos.x + window.size.x - 14.0, window.pos.y + 11.0)

    rig.click("the close box")

    assert not rig.listed, "the click landed on the box and the popup is gone"
    assert not rig.dialog.is_open
    rig.press(CTRL, COMMA)
    rig.frame()
    assert rig.dialog.is_open and rig.listed, "and it opens again"


@pytest.mark.usefixtures("typeset_live")
def test_the_defaults_button_is_dead_at_the_defaults_and_restores_otherwise(rig: Rig) -> None:
    rig.open()
    rig.click("defaults##text")
    assert rig.changes == []

    rig.click("##size-prose")
    rig.click("+2 px")
    rig.click("defaults##text")

    assert rig.changes == [RoleSet("prose", step=2), Restored(dialog.TEXT)]
    assert typeset.chosen(typeset.Role.PROSE) == typeset.DEFAULTS[typeset.Role.PROSE]


@pytest.mark.usefixtures("typeset_live")
def test_every_role_shows_a_sample_and_none_is_refused(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The samples are the seam's own calls. A role the library sets has a view, and
    its glyphs reached the draw list with nothing left unset; a role ImGui draws
    (literal text, an argument) has no view and still claimed its lines; and a
    diff is the library's diff view, as a pane draws one. What the
    seam says on screen in place of text -- a key drawn twice in a frame, a
    library that stopped -- is said through one function, and it said nothing.
    """
    said: list[str] = []
    real = views._said
    monkeypatch.setattr(
        views, "_said", lambda im, text, ink=None: (said.append(text), real(im, text, ink))[1]
    )
    rig.open()
    rig.frames(3)

    cache = rig.dialog.previews
    shown = [role for role in typeset.Role if role is not typeset.Role.LABEL]
    assert set(cache.keys()) == {("sample", role.value) for role in shown}
    for role in shown:
        key = ("sample", role.value)
        assert (cache.height(key) or 0.0) > 0.0 and cache.unbuilt(key) == 0, role
        if role in (typeset.Role.LITERAL, typeset.Role.ARGUMENT):
            assert cache.views(key) == (), role
            continue
        (view,) = cache.views(key)
        if role is typeset.Role.DIFF:
            assert view.origin is not None and view.row_count > 0, "the diff view, drawn"
            continue
        assert view.stats.whole and view.stats.vertices > 0, (role, view.stats)
    assert said == []
    assert typeset.can_draw()


@pytest.mark.usefixtures("typeset_live")
def test_the_facts_carry_the_seams_sentence_about_diffs(rig: Rig) -> None:
    known = dialog.facts()
    assert known.reason is None
    assert known.diffs == typeset.diff_renderer()
    assert (known.diffs or "").startswith("diffs are the library's: it keeps a 8 px column")
    rig.open()
    rig.frames(2)
    assert build(rig.state.settings, known).typeset.note == known.diffs


@pytest.mark.usefixtures("typeset_live")
def test_the_diff_sample_is_the_librarys_diff_view_and_the_note_says_so(rig: Rig) -> None:
    """
    The sample follows what a pane does: the library that is running keeps a
    column by the seam's own measurement, with nothing forced, so the sample
    is the diff view a pane would draw, on the cell that was measured. In two
    columns, which is a row shorter than unified (``_diff_sample`` says what
    that buys), and nothing of it is cut: it is narrower than its cell.
    """
    import imscribe

    from pptmstr.ui.typeset import diffs

    assert diffs.ADOPT == "measured" and diffs.columns().holds and typeset.diff_adopted()
    rig.open()
    rig.frames(3)

    cache = rig.dialog.previews
    key = ("sample", "diff")
    (view,) = cache.views(key)
    assert isinstance(view, imscribe.TypesetDiffView)
    assert (view.diff.added, view.diff.removed) == (1, 1) and view.origin is not None
    assert view.cell == "pixel" and view.grid.cell == 8.0
    assert view.layout is imscribe.DiffLayout.SIDE_BY_SIDE
    assert [view.row_info(row).kind for row in range(view.row_count)] == ["file", "hunk", "row"]
    (header,) = view.headers()
    assert header.fits and view.content_width <= (header.available_px or 0.0)
    assert cache.unbuilt(key) == 0
    assert (dialog.facts().diffs or "").startswith("diffs are the library's: it keeps a 8 px")
    window = rig.window()
    assert window.scroll_max.y == 0 and window.size.y <= DISPLAY[1] - CHROME


@pytest.mark.usefixtures("typeset_live")
def test_where_no_diff_is_the_librarys_the_diff_sample_is_its_rows_as_text(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The other answer, by the override: the sample is the rows as ImGui's text
    at the role's size, with no view, and the note says who draws a diff.
    """
    from pptmstr.ui.typeset import diffs

    monkeypatch.setattr(diffs, "ADOPT", "never")
    rig.open()
    rig.frames(3)
    cache = rig.dialog.previews
    key = ("sample", "diff")
    assert cache.views(key) == () and (cache.height(key) or 0.0) > 0.0
    assert dialog.facts().diffs == "diffs.ADOPT is 'never': diffs are pptmstr's own rows"


@pytest.mark.usefixtures("typeset_live")
def test_a_role_made_larger_is_seen_in_its_sample_before_the_dialog_is_closed(rig: Rig) -> None:
    rig.open()
    rig.frames(2)
    cache = rig.dialog.previews
    key = ("sample", "prose")
    before = cache.height(key)
    other = cache.height(("sample", "code-block"))
    assert before is not None and other is not None

    rig.click("##size-prose")
    rig.click("+4 px")
    rig.frames(2)

    after = cache.height(key)
    assert after is not None and after > before, "the prose sample is set 4 px larger"
    assert cache.height(("sample", "code-block")) == other, "and no other role's moved"
    assert rig.dialog.is_open


def test_text_that_cannot_be_typeset_draws_the_whole_dialog_with_no_sample(rig: Rig) -> None:
    """
    The seam raises when asked to draw with no faces. The dialog must still open:
    it is where the operator is told why there is no typeset text.
    """
    rig.known = lambda: Facts(available=False, obtainable=False, reason=GONE)
    rig.open()
    rig.frames(2)

    assert rig.dialog.is_open
    assert len(rig.dialog.previews) == 0
    rig.click("typeset text##typeset")
    rig.click("BOARD##pane-board")
    assert rig.changes == []
    rig.click("wrap composers##wrap")
    assert rig.changes == [FieldSet("wrap_inputs", False)], "the rest of it works"


def test_the_dialog_is_one_screen_at_the_applications_window_size(rig: Rig) -> None:
    """
    No tabs and no scrolling is a promise about 1500 by 900, the size the window
    opens at, less the menu bar and the status bar the runner keeps (``CHROME``).
    Measured with the real faces and the application's style: a row that grows
    breaks it here, and so does a section added without making room.
    """
    rig.open()
    rig.frames(2)

    window = rig.window()
    assert window is not None
    assert window.size.x == dialog._WIDTH
    assert window.size.y <= DISPLAY[1] - CHROME, window.size.y
    centred = (DISPLAY[1] - window.size.y) / 2
    assert abs(window.pos.y - centred) <= 1, "a dialog that fits stays where it opened"
    assert window.scroll_max.y == 0, "it scrolls: the last rows are out of sight"
    assert "close" in rig.rects and rig.rects["close"][1] < window.pos.y + window.size.y


@pytest.mark.usefixtures("typeset_live")
def test_a_dialog_that_grows_past_the_foot_of_the_screen_is_moved_back_onto_it(rig: Rig) -> None:
    """
    Opened and centred at the defaults, then given the largest text there is: 20 px
    running text with every role four more. Every sample is taller, the window
    grows downward from where it was centred, and without the move its close
    button is under the status bar.
    """
    rig.open()
    opened_at = rig.window().pos.y
    # Prose monospace as well: its sample is then too wide for one line.
    largest = Settings(typeset_scale=1.25).with_role("prose", slot="monospace")
    for role in typeset.Role:
        largest = largest.with_role(role.value, step=4)
    rig.state.settings = largest
    app._configure_typeset(largest)
    rig.frames(4)

    window = rig.window()
    assert opened_at + window.size.y > DISPLAY[1], "it would have run off the foot"
    assert window.pos.y >= 0
    assert window.pos.y + window.size.y == DISPLAY[1], "lifted to the foot and no further"
    assert window.scroll_max.y == 0
    assert rig.rects["close"][1] < DISPLAY[1]
