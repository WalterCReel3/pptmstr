"""
The settings dialog: everything the operator can persist, on one screen.

A modal over whichever layout is up, opened by ``Ctrl+,`` or **Text > Settings...**.
It exists because the choices outgrew a menu -- a face and a size for each of nine
roles, a switch per pane -- and it is deliberately *not* a preferences wizard: one
screen, no tabs, no Apply. Every field of ``Settings`` is either a control here or
is named as read-only (``READ_ONLY``), and a test holds that, so a field added later
cannot be persisted from nowhere.

Three layers, and the split is what makes the dialog testable without a window:

- ``Change`` and ``changed`` are the pure core: what a control asks for, as a value,
  and the settings that result. Nothing here saves, and nothing tells the seam.
- ``build`` turns ``Settings`` and a few ``Facts`` about this process (are the faces
  loaded, which families can be drawn) into a ``Model``: every row, its options, the
  one in force, and why a row is disabled when it is. Each option *carries* the
  ``Change`` that choosing it makes, so the draw has no knowledge of any setting.
- ``draw`` renders a ``Model`` and hands the ``Change`` of whatever was clicked to
  the callback it was given. ``app._apply_settings`` is on the other end of it, and
  is the one place a setting is saved and its consequences carried out.

**There is no cancel.** A change is applied and saved on the click that makes it, so
closing the dialog has nothing to keep or discard and no second copy of the
settings to fall out of step with the first. The way back is the control itself,
or a section's ``defaults`` button.

**A change is at most one click.** Every control is a checkbox or a combo -- no
slider, no text field -- so nothing is applied on a hover or a drag tick, and a
combo reports a change only for an entry other than the one in force. That
matters for one control: a change of the proportional family re-registers a font
with the layout library, which re-reads every face's metrics (about half a
second, once; ``typeset``'s docstring has the measurement), and the line beside it
says so.

What is deliberately not here is in
``planning/2026-10-08-settings-have-one-place.md``: line spacing, the metrics path,
caps and wrap modes are constants in code.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import assert_never

from imgui_bundle import imgui

from .. import settings as settings_mod
from .. import theme as theme_mod
from ..settings import Settings
from . import transcript_pane, typeset

TITLE = "Settings"

# Ctrl+comma, the chord most editors open their settings with. Built from int() for
# the reason ``launcher.CHORD`` is: key members do not OR as enums.
CHORD = int(imgui.Key.mod_ctrl) | int(imgui.Key.comma)
CHORD_LABEL = "Ctrl+,"

_WIDTH = 900.0


# ---------------------------------------------------------------------------
# The pure core: what a control asks for, and the settings that result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldSet:
    """
    One scalar field of ``Settings`` given a value.
    """

    field: str
    value: object


@dataclass(frozen=True, slots=True)
class RoleSet:
    """
    One role's face slot or size step chosen; the half left ``None`` is kept.
    """

    role: str
    slot: str | None = None
    step: int | None = None


@dataclass(frozen=True, slots=True)
class PaneSet:
    """
    One pane's typesetting switched.
    """

    pane: str
    on: bool


@dataclass(frozen=True, slots=True)
class Restored:
    """
    A section's fields put back to what ``Settings()`` has.
    """

    section: str


Change = FieldSet | RoleSet | PaneSet | Restored

APPEARANCE = "appearance"
TEXT = "text"
PANES = "panes"
SESSIONS = "sessions"

SECTIONS: dict[str, tuple[str, ...]] = {
    APPEARANCE: ("theme", "typeset_scale"),
    TEXT: ("typeset", "typeset_family", "typeset_roles"),
    PANES: ("typeset_panes", "context_mode"),
    SESSIONS: ("wrap_inputs", "concurrency_cap", "subagent_cap", "fps_idle"),
}
"""
Which fields of ``Settings`` each section owns: what its ``defaults`` button restores
and what its rows edit. Every field is in exactly one section or in ``READ_ONLY``.
"""

READ_ONLY: tuple[str, ...] = ("admitted_connectors",)
"""
Fields the dialog shows and does not edit. MCP connectors are records -- a name, a
URL and an account identifier -- and admitting one decides what every session can
reach, so it stays a deliberate hand-edit of the file; the dialog says where.
"""

_SCALARS = frozenset(name for owned in SECTIONS.values() for name in owned) - {
    "typeset_roles",
    "typeset_panes",
}
"""
The fields a ``FieldSet`` may name. The role choices and the pane switches are
structured and have changes of their own.
"""


def changed(settings: Settings, change: Change) -> Settings:
    """
    ``settings`` with one change made. Pure and total: a change naming a field or
    a section this build does not have is the settings unchanged, not an error.
    """
    match change:
        case FieldSet(field=name, value=value):
            if name not in _SCALARS:
                return settings
            return settings.merged(**{name: value})
        case RoleSet(role=role, slot=slot, step=step):
            return settings.with_role(role, slot=slot, step=step)
        case PaneSet(pane=pane, on=on):
            return settings.with_pane(pane, on)
        case Restored(section=section):
            base = Settings()
            return replace(
                settings, **{name: getattr(base, name) for name in SECTIONS.get(section, ())}
            )
        case _:
            assert_never(change)


# ---------------------------------------------------------------------------
# The model: rows, options, what is in force, and why a row is disabled
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Facts:
    """
    What the model needs to know that ``Settings`` does not say: the state of this
    process. Passed in, so the model is built without a window or a font.

    ``reason`` is ``typeset.unavailable_reason()``: why text cannot be typeset, or
    what switching it on will cost while the faces are still to load, or ``None``.
    ``unusable`` names the proportional families whose file is not on the atlas.
    """

    available: bool = True
    obtainable: bool = True
    reason: str | None = None
    unusable: frozenset[str] = frozenset()
    path: str = ""


def facts() -> Facts:
    """
    The facts as they stand now, read from the seam.
    """
    return Facts(
        available=typeset.available(),
        obtainable=typeset.obtainable(),
        reason=typeset.unavailable_reason(),
        unusable=frozenset(each.name for each, usable in typeset.proportionals() if not usable),
        path=str(settings_mod.settings_path()),
    )


@dataclass(frozen=True, slots=True)
class Option:
    """
    One entry of a choice, with the change that picking it makes. ``usable`` false
    is an entry shown and not offered: a family whose file did not load.
    """

    label: str
    change: Change
    usable: bool = True


@dataclass(frozen=True, slots=True)
class Choice:
    """
    A row that is one of several: ``options[current]`` is what is in force.
    ``disabled`` is why it cannot be changed now, or ``None`` when it can.
    """

    key: str
    label: str
    options: tuple[Option, ...]
    current: int
    note: str = ""
    disabled: str | None = None


@dataclass(frozen=True, slots=True)
class Toggle:
    """
    A row that is on or off, with the change a click makes.
    """

    key: str
    label: str
    on: bool
    flip: Change
    note: str = ""
    disabled: str | None = None


@dataclass(frozen=True, slots=True)
class RoleRow:
    """
    One role of typeset text: which family it is set in, and its size.
    """

    role: str
    label: str
    face: Choice
    size: Choice


@dataclass(frozen=True, slots=True)
class Section:
    """
    A section's heading and its ``defaults`` button, which is dead while the
    section already is at its defaults.
    """

    key: str
    title: str
    restore: Restored
    at_default: bool


@dataclass(frozen=True, slots=True)
class Model:
    """
    The whole dialog as values. ``draw`` renders exactly this.
    """

    appearance: Section
    theme: Choice
    scale: Choice
    text: Section
    typeset: Toggle
    family: Choice
    monospace: str
    roles: tuple[RoleRow, ...]
    # Whether a sample of each role can be set through the seam this frame.
    previews: bool
    panes: Section
    pane_switches: tuple[Toggle, ...]
    context_mode: Choice
    sessions: Section
    wrap: Toggle
    caps: tuple[Choice, ...]
    connectors: tuple[str, ...]
    connectors_note: str


TEXT_OFF = "typeset text is switched off"
FAMILY_NOTE = "a change costs half a second, once"
MONOSPACE = "Inconsolata"
MONOSPACE_NOTE = "the only monospace that ships"
ONE_SLOT = "its columns are content: monospace only"
SCALE_NOTE = "typeset text only; the chrome follows the display"
_CAPS: tuple[tuple[str, str, tuple[float, ...], str], ...] = (
    (
        "concurrency_cap",
        "sessions at once",
        (1, 2, 3, 4, 6, 8, 12, 16),
        "raising it starts queued sessions; lowering it stops nothing that is running",
    ),
    (
        "subagent_cap",
        "sub-agents a session",
        (0, 2, 4, 6, 8, 12, 16),
        "read when a session launches; the launcher can override it for one launch",
    ),
    (
        "fps_idle",
        "idle fps",
        (3.0, 5.0, 9.0, 15.0, 30.0),
        "the frame rate while every agent is idle or parked on you",
    ),
)
"""
The numeric settings, each as the values offered and what the number does. A list
and not a field to type in: a combo changes on a click and on nothing else. A
value that is not in its list -- hand-edited, or given on the command line -- is
shown and kept (``_ladder``).
"""


def _ladder(values: tuple[float, ...], held: float) -> tuple[tuple[float, ...], int]:
    """
    ``values`` with ``held`` among them, in order, and where it is.
    """
    for index, value in enumerate(values):
        if abs(value - held) < 1e-6:
            return values, index
    rungs = tuple(sorted((*values, held)))
    return rungs, rungs.index(held)


def _section(key: str, title: str, at_default: bool) -> Section:
    return Section(key, title, Restored(key), at_default)


def _plain_default(settings: Settings, section: str) -> bool:
    base = Settings()
    return all(getattr(settings, name) == getattr(base, name) for name in SECTIONS[section])


def _styles(settings: Settings) -> dict[typeset.Role, typeset.RoleStyle]:
    """
    Every role's style as the seam reads these settings: a role or a slot this
    build does not know dropped, a step clamped.
    """
    return typeset.checked(
        typeset.roles_from((each.role, each.slot, each.step) for each in settings.typeset_roles)
    )


def _step_label(step: int) -> str:
    return f"{step:+d} px" if step else "body"


def _role_row(role: typeset.Role, held: typeset.RoleStyle, disabled: str | None) -> RoleRow:
    offered = typeset.SLOTS[role]
    face = Choice(
        key=f"face-{role.value}",
        label="",
        options=tuple(Option(slot.value, RoleSet(role.value, slot=slot.value)) for slot in offered),
        current=offered.index(held.slot),
        disabled=ONE_SLOT if len(offered) < 2 else disabled,
    )
    size = Choice(
        key=f"size-{role.value}",
        label="",
        options=tuple(
            Option(_step_label(step), RoleSet(role.value, step=step)) for step in typeset.STEPS
        ),
        current=typeset.STEPS.index(held.step),
        disabled=disabled,
    )
    return RoleRow(role.value, typeset.ROLE_LABELS[role], face, size)


def build(settings: Settings, known: Facts | None = None) -> Model:
    """
    The dialog for these settings in a process these facts describe.

    What a row shows is what is **in force**, as the seam would read the settings:
    a family this build cannot draw shows the default it falls back to, a step out
    of range shows the end of the range. The settings keep what they say until the
    operator picks something.
    """
    known = Facts() if known is None else known
    base = Settings()
    # Why the rows about typeset text cannot be changed: the faces are not there,
    # or they are and the operator has typesetting off.
    text_off = (
        (known.reason or "the faces are not loaded")
        if not known.available
        else None if settings.typeset else TEXT_OFF
    )

    themes = tuple(theme_mod.THEMES)
    theme = Choice(
        key="theme",
        label="theme",
        options=tuple(
            Option(theme_mod.THEMES[name].display_name, FieldSet("theme", name)) for name in themes
        ),
        # An unknown name is drawn as dark (``theme.set_theme``), so that is what it shows.
        current=themes.index(settings.theme) if settings.theme in themes else themes.index("dark"),
    )

    low, high = typeset.SCALES[0], typeset.SCALES[-1]
    held = settings.typeset_scale
    scales, at = _ladder(
        typeset.SCALES, min(max(held, low), high) if math.isfinite(held) else base.typeset_scale
    )
    scale = Choice(
        key="scale",
        label="text size",
        options=tuple(
            Option(f"{each * 100:g}%", FieldSet("typeset_scale", each)) for each in scales
        ),
        current=at,
        note=SCALE_NOTE,
        disabled=text_off,
    )

    master = Toggle(
        key="typeset",
        label="typeset text",
        on=settings.typeset and known.available,
        flip=FieldSet("typeset", not settings.typeset),
        note=known.reason or "",
        disabled=None if known.obtainable else (known.reason or "the faces are not loaded"),
    )

    families = typeset.PROPORTIONALS
    in_force = next(
        (
            index
            for index, each in enumerate(families)
            if each.name == settings.typeset_family and each.name not in known.unusable
        ),
        0,
    )
    family = Choice(
        key="family",
        label="proportional",
        options=tuple(
            Option(
                each.label, FieldSet("typeset_family", each.name), each.name not in known.unusable
            )
            for each in families
        ),
        current=in_force,
        note=FAMILY_NOTE,
        disabled=text_off,
    )

    styles = _styles(settings)
    roles = tuple(_role_row(role, styles[role], text_off) for role in typeset.Role)

    switched = typeset.panes_from(settings.typeset_panes)
    pane_switches = tuple(
        Toggle(
            key=f"pane-{pane.value}",
            label=typeset.PANE_LABELS[pane],
            on=switched.get(pane, True),
            flip=PaneSet(pane.value, not switched.get(pane, True)),
            disabled=text_off,
        )
        for pane in typeset.Pane
    )
    # TYPESET needs the seam drawing the context log; without it the pane falls
    # back to wrapped lines, so it is not offered as where to start.
    context_set = text_off is None and switched.get(typeset.Pane.CONTEXT, True)
    start = transcript_pane.mode_named(settings.context_mode)
    modes = (None, *transcript_pane.RenderMode)
    context_mode = Choice(
        key="context-mode",
        label="log starts in",
        options=tuple(
            Option(
                (
                    f"this build's ({transcript_pane.DEFAULT_MODE.value})"
                    if mode is None
                    else mode.value
                ),
                FieldSet("context_mode", "" if mode is None else mode.value),
                mode is not transcript_pane.RenderMode.TYPESET or context_set,
            )
            for mode in modes
        ),
        current=modes.index(start),
    )

    caps = []
    for name, label, offered, note in _CAPS:
        rungs, where = _ladder(offered, getattr(settings, name))
        kind = type(getattr(base, name))
        caps.append(
            Choice(
                key=name,
                label=label,
                options=tuple(Option(f"{each:g}", FieldSet(name, kind(each))) for each in rungs),
                current=where,
                note=note,
            )
        )

    return Model(
        appearance=_section(
            APPEARANCE,
            "APPEARANCE",
            themes[theme.current] == base.theme and abs(scales[at] - base.typeset_scale) < 1e-6,
        ),
        theme=theme,
        scale=scale,
        text=_section(
            TEXT,
            "TEXT",
            settings.typeset == base.typeset
            and settings.typeset_family == base.typeset_family
            and styles == typeset.DEFAULTS,
        ),
        typeset=master,
        family=family,
        monospace=MONOSPACE,
        roles=roles,
        previews=text_off is None,
        panes=_section(
            PANES,
            "PANES",
            all(each.on for each in pane_switches) and settings.context_mode == base.context_mode,
        ),
        pane_switches=pane_switches,
        context_mode=context_mode,
        sessions=_section(SESSIONS, "INPUT AND SESSIONS", _plain_default(settings, SESSIONS)),
        wrap=Toggle(
            key="wrap",
            label="wrap composers",
            on=settings.wrap_inputs,
            flip=FieldSet("wrap_inputs", not settings.wrap_inputs),
        ),
        caps=tuple(caps),
        connectors=tuple(f"{each.name}  {each.url}" for each in settings.admitted_connectors),
        connectors_note=f"edit admitted_connectors in {known.path or 'settings.json'}",
    )


# ---------------------------------------------------------------------------
# The shell: state, the chord, and the draw
# ---------------------------------------------------------------------------


@dataclass
class DialogState:
    """
    Presentation state for the modal. Holds no setting: the settings are the
    application's, and the dialog is drawn from them every frame.
    """

    # Read by the layout keys, which run before any drawing and so see last
    # frame's value: the one on screen when the key was pressed.
    is_open: bool = False
    _open_requested: bool = False
    _close_requested: bool = False
    # Whether a combo's list was open over the modal when the last frame ended.
    # Escape with one open closes the list and not the dialog.
    _list_was_open: bool = False
    # Where to put the window next frame so its last row is on screen, or None
    # to leave it where the operator has it. See ``_kept_in_view``.
    _lifted: tuple[float, float] | None = None
    # The role samples. One view per role, each drawn once a frame.
    previews: typeset.ViewCache = field(default_factory=typeset.ViewCache)

    def request_open(self) -> None:
        """
        Ask for the modal the next time it draws. Safe when it is already open.
        """
        if not self.is_open:
            self._open_requested = True

    def request_close(self) -> None:
        if self.is_open:
            self._close_requested = True

    def toggle(self) -> None:
        if self.is_open:
            self.request_close()
        else:
            self.request_open()


def handle_shortcut(state: DialogState, *, blocked: bool = False) -> None:
    """
    ``Ctrl+,`` opens the dialog, and closes it again.

    **Not from inside a text field**, unlike ``launcher.handle_shortcut``. Ctrl+N is
    wanted most in a reply box; nobody is helped by a settings dialog arriving over
    a half-typed reply. ``route_global`` alone does not give that -- an active
    ``InputText`` claims only the chords it uses itself and this is not one of
    them, so the route is granted over it -- which is why the active item is
    asked about first. Any active item, and not ``want_text_input``: that flag is
    the frame before's, and a drag in progress is no better a moment.

    ``blocked`` is the caller saying another modal is up: two modals opened from
    the root replace each other, and the launcher holds a draft worth more than
    this dialog's convenience.

    Must be called inside a frame, as the launcher's is.
    """
    if blocked or imgui.is_any_item_active():
        return
    if imgui.shortcut(CHORD, imgui.InputFlags_.route_global):
        state.toggle()


SAMPLES: dict[typeset.Role, str] = {
    typeset.Role.PROSE: "A parked call waits until it is **read** and *approved*.",
    typeset.Role.HEADING: "## What the gate parks",
    typeset.Role.INLINE_CODE: "run `make test` before `make lint`",
    typeset.Role.CODE_BLOCK: "```\nfor node in order: emit(node)\n```",
    typeset.Role.DIFF: (
        "```diff\n--- a/pool.py\n+++ b/pool.py\n@@ -1 +1 @@\n-cap = 4\n+cap = 8\n```"
    ),
    typeset.Role.LITERAL: "$ make test  ... 3347 passed in 41.2s",
    typeset.Role.ARGUMENT: 'file_path: "pptmstr/app.py"',
    typeset.Role.TABLE: "| pane | set by |\n|---|---|\n| BOARD | the seam |",
    typeset.Role.LABEL: "A board row's title",
}
"""
What each role's row shows of itself, small enough that nine of them fit a screen.
ASCII only: the label is a native line in one font with no fallback.
"""

_ARGUMENT = (
    typeset.Part(SAMPLES[typeset.Role.ARGUMENT], typeset.Kind.LITERAL, role=typeset.Role.ARGUMENT),
)


def _preview(cache: typeset.ViewCache, role: typeset.Role) -> None:
    """
    One role's sample, set through the seam in the cell at the cursor.

    Every role in the catalogue has an arm, so a role added to the seam is a type
    error here and not a row with no sample.
    """
    key = ("sample", role.value)
    match role:
        case (
            typeset.Role.PROSE
            | typeset.Role.HEADING
            | typeset.Role.INLINE_CODE
            | typeset.Role.CODE_BLOCK
            | typeset.Role.DIFF
            | typeset.Role.TABLE
        ):
            typeset.markdown(cache, key, SAMPLES[role])
        case typeset.Role.LITERAL:
            typeset.plain(cache, key, SAMPLES[role])
        case typeset.Role.ARGUMENT:
            typeset.document(cache, key, _ARGUMENT)
        case typeset.Role.LABEL:
            typeset.label(SAMPLES[role], weight=typeset.Weight.REGULAR)
        case _:
            assert_never(role)


def _why(reason: str | None) -> None:
    """
    The last control says this when the mouse is over it: why it is disabled, or
    what its number does.
    """
    if reason and imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
        imgui.set_tooltip(reason)


def _toggle(toggle: Toggle, picked: list[Change]) -> None:
    dead = toggle.disabled is not None
    if dead:
        imgui.begin_disabled()
    clicked, _ = imgui.checkbox(f"{toggle.label}##{toggle.key}", toggle.on)
    if dead:
        imgui.end_disabled()
        _why(toggle.disabled)
    elif clicked:
        picked.append(toggle.flip)


def _choice(choice: Choice, picked: list[Change], width: float) -> None:
    """
    A labelled combo. A click on the entry in force is not a change, and an
    entry that is not usable cannot be clicked.
    """
    if choice.label:
        imgui.align_text_to_frame_padding()
        imgui.text_unformatted(choice.label)
        imgui.same_line()
    dead = choice.disabled is not None
    if dead:
        imgui.begin_disabled()
    imgui.set_next_item_width(width)
    if imgui.begin_combo(f"##{choice.key}", choice.options[choice.current].label):
        # Stated here and not left to keyboard navigation, which the runner may
        # not have switched on: Escape over an open list closes the list.
        if imgui.is_key_pressed(imgui.Key.escape):
            imgui.close_current_popup()
        for index, option in enumerate(choice.options):
            flags = 0 if option.usable else int(imgui.SelectableFlags_.disabled)
            clicked, _ = imgui.selectable(option.label, index == choice.current, flags)
            if clicked and index != choice.current:
                picked.append(option.change)
            if index == choice.current:
                imgui.set_item_default_focus()
        imgui.end_combo()
    if dead:
        imgui.end_disabled()
        _why(choice.disabled)


def _note(text: str) -> None:
    """
    Words beside the last control, wrapped at the window's edge: a reason the
    library gives for not importing is as long as it likes.
    """
    if text:
        imgui.same_line()
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(text)
        imgui.pop_text_wrap_pos()


def _title(text: str) -> None:
    imgui.text_colored(theme_mod.P.accent.vec4, text)


def _heading(section: Section, picked: list[Change]) -> None:
    """
    A section's title, with its ``defaults`` button at the right edge of the line.
    """
    _title(section.title)
    style = imgui.get_style()
    wide = imgui.calc_text_size("defaults").x + 2 * style.frame_padding.x
    imgui.same_line()
    imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x - wide)
    if section.at_default:
        imgui.begin_disabled()
    if imgui.small_button(f"defaults##{section.key}"):
        picked.append(section.restore)
    if section.at_default:
        imgui.end_disabled()
    imgui.separator()


def _roles(model: Model, cache: typeset.ViewCache, picked: list[Change]) -> None:
    fixed = imgui.TableColumnFlags_.width_fixed
    if not imgui.begin_table("##roles", 4, imgui.TableFlags_.sizing_fixed_fit):
        return
    imgui.table_setup_column("role", fixed, 110.0)
    imgui.table_setup_column("face", fixed, 140.0)
    imgui.table_setup_column("size", fixed, 80.0)
    imgui.table_setup_column("sample", imgui.TableColumnFlags_.width_stretch)
    imgui.table_next_row()
    for title in ("role", "face", "size", "sample"):
        imgui.table_next_column()
        imgui.text_disabled(title)
    for row in model.roles:
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.align_text_to_frame_padding()
        imgui.text_unformatted(row.label)
        imgui.table_next_column()
        if len(row.face.options) < 2:
            # Nothing to choose, so no control: the one family it can be, as text.
            imgui.align_text_to_frame_padding()
            imgui.text_disabled(row.face.options[0].label)
            _why(row.face.disabled)
        else:
            _choice(row.face, picked, -1.0)
        imgui.table_next_column()
        _choice(row.size, picked, -1.0)
        imgui.table_next_column()
        if model.previews:
            _preview(cache, typeset.Role(row.role))
        else:
            imgui.align_text_to_frame_padding()
            imgui.text_disabled("-")
    imgui.end_table()


_GAP = 24.0


def _body(model: Model, cache: typeset.ViewCache, picked: list[Change]) -> None:
    _heading(model.appearance, picked)
    _choice(model.theme, picked, 190.0)
    imgui.same_line(0.0, _GAP)
    _choice(model.scale, picked, 90.0)
    _note(model.scale.note)

    imgui.spacing()
    _heading(model.text, picked)
    _toggle(model.typeset, picked)
    _note(model.typeset.note)
    _choice(model.family, picked, 270.0)
    _note(model.family.note)
    imgui.same_line(0.0, _GAP)
    imgui.text_unformatted("monospace")
    imgui.same_line()
    imgui.text_disabled(model.monospace)
    _why(MONOSPACE_NOTE)
    _roles(model, cache, picked)

    imgui.spacing()
    _heading(model.panes, picked)
    imgui.align_text_to_frame_padding()
    imgui.text_unformatted("typeset in")
    for switch in model.pane_switches:
        imgui.same_line()
        _toggle(switch, picked)
    imgui.same_line(0.0, _GAP)
    _choice(model.context_mode, picked, 210.0)

    imgui.spacing()
    _heading(model.sessions, picked)
    _toggle(model.wrap, picked)
    for cap in model.caps:
        imgui.same_line(0.0, _GAP)
        _choice(cap, picked, 70.0)
        _why(cap.note)

    imgui.spacing()
    _title("MCP CONNECTORS")
    imgui.same_line()
    imgui.text_disabled("read-only here")
    imgui.separator()
    for line in model.connectors:
        imgui.text_unformatted(line)
    none = "" if model.connectors else "none admitted - "
    imgui.text_disabled(f"{none}{model.connectors_note}")


def _kept_in_view(viewport: imgui.Viewport) -> tuple[float, float] | None:
    """
    Where the current window has to move so that its foot is above the status bar,
    or ``None`` when it already is.

    The window is centred once, when it opens, and is the operator's to drag after
    that. It also grows: a larger text size or a role stepped up makes every
    sample taller, and a window that grew downward from where it was centred puts
    its last rows, the close button among them, off the bottom of the screen.
    """
    pos, size = imgui.get_window_pos(), imgui.get_window_size()
    top = viewport.work_pos.y
    foot = top + viewport.work_size.y
    if pos.y + size.y <= foot or pos.y <= top:
        return None
    return (pos.x, max(top, foot - size.y))


def draw(
    state: DialogState,
    settings: Settings,
    apply: Callable[[Change], None],
    *,
    known: Callable[[], Facts] = facts,
) -> None:
    """
    Draw the modal if it has been asked for, and hand ``apply`` what was clicked.

    At root level -- ``post_render_dockable_windows`` -- as the launcher is and for
    its reason: a popup opened inside a docked window belongs to that window.

    ``apply`` is called after the popup is ended and at most once a frame: a click
    is one control, and the settings the rest of this frame's rows were built from
    stay the ones they were drawn with.
    """
    if state._open_requested:
        state._open_requested = False
        state.is_open = True
        state._list_was_open = False
        imgui.open_popup(TITLE)
    if not state.is_open:
        return

    model = build(settings, known())
    viewport = imgui.get_main_viewport()
    if state._lifted is not None:
        imgui.set_next_window_pos(imgui.ImVec2(*state._lifted), imgui.Cond_.always)
        state._lifted = None
    else:
        imgui.set_next_window_pos(
            viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5)
        )
    # A width and not ``always_auto_resize``: the samples are as wide as the room
    # they are given, and a window sized by its content would have none to give.
    imgui.set_next_window_size(imgui.ImVec2(_WIDTH, 0.0), imgui.Cond_.always)
    opened, _ = imgui.begin_popup_modal(
        TITLE, True, imgui.WindowFlags_.no_resize | imgui.WindowFlags_.no_saved_settings
    )
    if not opened:
        # Closed by its own close button, or replaced by another modal opened
        # from the root. Resync so the layout keys stop deferring to it.
        state.is_open = False
        state._close_requested = False
        return

    state._lifted = _kept_in_view(viewport)
    picked: list[Change] = []
    _body(model, state.previews, picked)

    imgui.separator()
    close = imgui.button("close", imgui.ImVec2(90.0, 0.0))
    imgui.same_line()
    imgui.text_disabled(f"changes are applied and saved as they are made - Esc or {CHORD_LABEL}")

    # Not on a repeat: Escape held to close a list would go on to close the dialog.
    escaped = imgui.is_key_pressed(imgui.Key.escape, False) and not state._list_was_open
    state._list_was_open = imgui.is_popup_open("", imgui.PopupFlags_.any_popup_id)
    if close or escaped or state._close_requested:
        state.is_open = False
        state._close_requested = False
        imgui.close_current_popup()
    imgui.end_popup()

    for change in picked[:1]:
        apply(change)
