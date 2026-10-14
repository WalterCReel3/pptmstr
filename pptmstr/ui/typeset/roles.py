"""
What the operator may choose about typeset text, and what is in force: pptmstr's own policy.

The role catalogue, the two family slots, the size steps and scales, the pane
switches, and the settings' strings read into them. **Nothing here imports the
text library**: it is what pptmstr decides before any library is asked, it is
what the settings dialog lists when the library is absent, and a test reads the
imports to keep it so.

``enabled(pane)`` is the one question a pane asks before it draws. It is a
product of four facts, three of them here -- the master switch, the pane's own
switch, and whether the pane's typeset branch is built on the library at all
(``ADOPTED``) -- and one that is the library's: whether it can draw, which
``gate`` answers and hands in through ``bind``. Handed in rather than imported
so that this module stays free of the library and of ImGui.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

BODY_PX = 16.0
"""
The pixel height of running text until a frame says otherwise: ``theme._FONT_SIZE``.

The live value is ``body_px()``: the size ImGui draws the UI font at inside a
frame, taken between frames (``gate.pre_frame``).
"""


class Weight(enum.Enum):
    """
    The faces a native ImGui line can be set in, by what they are for.

    The prose weights follow ``Role.LABEL``: where that role is monospace they are
    Inconsolata, and there is no monospace italic, so ``ITALIC`` is upright there.
    """

    REGULAR = "regular"
    BOLD = "bold"
    ITALIC = "italic"
    BOLD_ITALIC = "bold-italic"
    CODE = "code"
    CODE_BOLD = "code-bold"


class Role(enum.Enum):
    """
    What a run of typeset text is, which is what its face and size are chosen by.

    The catalogue is what the seam and the panes tell apart, and no finer:

    - ``PROSE``: paragraphs, list items and quotes of a markdown body, and plain
      text set in the prose face (``plain(mono=False)``).
    - ``HEADING``: the six heading levels, which keep their scale between them.
    - ``INLINE_CODE``: a code span inside prose, a heading or a table cell.
    - ``CODE_BLOCK``: a fenced block.
    - ``DIFF``: the rows of ``diff()``, and of a diff fenced in a document.
    - ``LITERAL``: text that is not markdown -- tool calls, results, errors,
      tracebacks: every ``Kind`` but ``MARKDOWN`` unless the part says otherwise.
    - ``ARGUMENT``: a parked call's argument names and values (``Part.role``).
    - ``TABLE``: the cells of a markdown table.
    - ``LABEL``: a native ImGui line in a typeset face (``label()``, ``font()``): a
      board row's title, a subject.
    """

    PROSE = "prose"
    HEADING = "heading"
    INLINE_CODE = "inline-code"
    CODE_BLOCK = "code-block"
    DIFF = "diff"
    LITERAL = "literal"
    ARGUMENT = "argument"
    TABLE = "table"
    LABEL = "label"


ROLE_LABELS: dict[Role, str] = {
    Role.PROSE: "Prose",
    Role.HEADING: "Headings",
    Role.INLINE_CODE: "Inline code",
    Role.CODE_BLOCK: "Code blocks",
    Role.DIFF: "Diffs",
    Role.LITERAL: "Tool output",
    Role.ARGUMENT: "Arguments",
    Role.TABLE: "Tables",
    Role.LABEL: "Row titles",
}
"""
What the settings dialog calls each role.
"""


class Slot(enum.Enum):
    """
    Which of the two live families a role is set in.

    Two, because the library names font files by face -- four proportional
    members, two monospace, one for mathematics -- and keeps one file per member
    for the whole process. So at any moment one family fills
    the proportional members (``proportional()``) and one the monospace pair
    (Inconsolata, the only monospace that ships), and what a role chooses is which
    of the two it uses. A theme sets a face per block kind and one view takes one
    theme, so the choice is free per role inside a single document.
    """

    PROPORTIONAL = "proportional"
    MONOSPACE = "monospace"


@dataclass(frozen=True, slots=True)
class RoleStyle:
    """
    What the operator may choose for one role: a family slot, and a size.

    ``step`` is whole pixels on top of the body size (``body_px()``), which is the
    UI font's times ``scale()``: 0 is the chrome's own height, in either family.
    Whole pixels because that is what ImGui bakes.
    """

    slot: Slot
    step: int = 0


STEPS: tuple[int, ...] = (-2, -1, 0, 1, 2, 3, 4)
"""
The size steps offered, in pixels relative to the body. ``configure`` clamps to the
first and the last.
"""

DEFAULTS: dict[Role, RoleStyle] = {
    Role.PROSE: RoleStyle(Slot.PROPORTIONAL),
    Role.HEADING: RoleStyle(Slot.PROPORTIONAL),
    Role.INLINE_CODE: RoleStyle(Slot.MONOSPACE),
    Role.CODE_BLOCK: RoleStyle(Slot.MONOSPACE),
    Role.DIFF: RoleStyle(Slot.MONOSPACE),
    Role.LITERAL: RoleStyle(Slot.MONOSPACE),
    Role.ARGUMENT: RoleStyle(Slot.MONOSPACE),
    Role.TABLE: RoleStyle(Slot.PROPORTIONAL),
    Role.LABEL: RoleStyle(Slot.PROPORTIONAL),
}
"""
Every role's style until the operator says otherwise.

Inline code at step 0 is Inconsolata at the chrome's own pixel height inside prose
of the same height, so a code span is the size of the same word in the chrome.
"""

SLOTS: dict[Role, tuple[Slot, ...]] = {
    Role.PROSE: (Slot.PROPORTIONAL, Slot.MONOSPACE),
    Role.HEADING: (Slot.PROPORTIONAL, Slot.MONOSPACE),
    Role.INLINE_CODE: (Slot.MONOSPACE,),
    Role.CODE_BLOCK: (Slot.MONOSPACE,),
    Role.DIFF: (Slot.MONOSPACE,),
    Role.LITERAL: (Slot.MONOSPACE,),
    Role.ARGUMENT: (Slot.MONOSPACE,),
    Role.TABLE: (Slot.PROPORTIONAL, Slot.MONOSPACE),
    Role.LABEL: (Slot.PROPORTIONAL, Slot.MONOSPACE),
}
"""
The slots a role may be given. Code, diffs, literal text and arguments are
monospace by nature -- their columns are content -- and are offered nothing else.
"""

LEADING: dict[Role, float] = {
    Role.PROSE: 17 / 16,
    Role.HEADING: 1.0,
    Role.CODE_BLOCK: 17 / 16,
    Role.DIFF: 18 / 16,
    Role.LITERAL: 17 / 16,
    Role.ARGUMENT: 17 / 16,
    Role.TABLE: 17 / 16,
}
"""
Line spacing, per role: the distance from one baseline to the next as a multiple
of the role's own **pixel height**. THE place to change it; there is no setting.

``17 / 16`` reads "17 px a line at a 16 px size". The product is taken to a whole
pixel (``pitch()``), so every line of a block is the same height. For scale: ImGui
sets wrapped text at 1.0 (16 px at 16), and at 1.0 a line's descenders and the
next line's ascenders touch but do not overlap -- the faces are exactly their
pixel height tall. A multiple of the pixel height and not of the em, because the
em at one height differs by face (Roboto 13.65 px, Inconsolata 15.25 px at 16).

A heading's is of its own size (22 px at h1), which is where a wrapped heading's
lines go. A diff's rows are the library's grid and take the number rounded up.
Inline code has no line of its own, and a label is ImGui's line: neither is here.
Literal text and arguments are set a line of their pixel height whoever sets
them (``themes.literal_line`` has the exception and why), so their entries are
what a pitch of theirs would be and set no line on screen.
"""


class Pane(enum.Enum):
    """
    The panes that can set their text through this module, each switched on its own.
    """

    DETAIL = "detail"
    INBOX = "inbox"
    CONTEXT = "context"
    BOARD = "board"
    BRIEF = "brief"


PANE_LABELS: dict[Pane, str] = {
    Pane.DETAIL: "DETAIL",
    Pane.INBOX: "NEEDS YOU",
    Pane.CONTEXT: "CONTEXT",
    Pane.BOARD: "BOARD",
    Pane.BRIEF: "BRIEF",
}
"""
What the settings dialog calls each pane: the titles the panes carry.
"""


@dataclass(frozen=True, slots=True)
class Proportional:
    """
    A family for the proportional slot, from fonts that already ship.

    ``file`` is a font in the imgui-bundle wheel, relative to its fonts directory,
    to set upright text in instead of the library's Roboto. It replaces one of
    the library's four proportional faces and no other, which is the honest shape
    of a family that ships in one style: bold, italic and bold italic -- so every
    heading -- stay Roboto, and such a family is two families in one paragraph.
    """

    name: str
    label: str
    file: str | None = None


DEFAULT_PROPORTIONAL = "roboto"

PROPORTIONALS: tuple[Proportional, ...] = (
    Proportional(DEFAULT_PROPORTIONAL, "Roboto"),
    Proportional("droid", "Droid Sans (Roboto emphasis)", file="DroidSans.ttf"),
)
"""
What may fill the proportional slot, in the order offered. The first is the default.
"""

DEFAULT_PRESET = "roboto"
"""
The name the face presets gave the defaults, kept as a spelling ``configure(faces=)``
and a saved ``typeset_faces`` still understand (``legacy()``).
"""

_LEGACY: dict[str, tuple[str, dict[Role, RoleStyle]]] = {
    DEFAULT_PRESET: (DEFAULT_PROPORTIONAL, {}),
    "mono": (
        DEFAULT_PROPORTIONAL,
        {role: RoleStyle(Slot.MONOSPACE) for role, offered in SLOTS.items() if len(offered) > 1},
    ),
    "roboto-large": (DEFAULT_PROPORTIONAL, {Role.PROSE: RoleStyle(Slot.PROPORTIONAL, 2)}),
    "droid": ("droid", {}),
}
"""
What each face preset of the build before roles means now: a family for the
proportional slot, and the roles that differ from ``DEFAULTS``.

``roboto-large`` was prose at 18 px beside 16 px chrome, which is two steps.
"""

SCALES: tuple[float, ...] = (0.875, 1.0, 1.125, 1.25)
"""
The text sizes the settings dialog offers, as multiples of the UI font's: 14, 16, 18 and 20 px
at a 16 px UI font. ``configure`` takes any value between the first and the last.
"""


@dataclass
class _State:
    """
    What the seam holds for the whole process: the settings it was told.

    Module state because the things in it are facts about the application -- one
    operator preference, one UI font size -- and not about any pane.
    """

    typeset: bool = True
    # What the operator chose: a style for every role, the family in the
    # proportional slot, and the panes switched off. `proportional()` is what is
    # in force, which differs when the family named has no file to load.
    roles: dict[Role, RoleStyle] = field(default_factory=lambda: dict(DEFAULTS))
    family: str = DEFAULT_PROPORTIONAL
    panes_off: frozenset[Pane] = frozenset()
    scale: float = 1.0
    # The size ImGui draws the UI font at inside a frame; see `gate.pre_frame`.
    live_px: float = BODY_PX
    # Goes up whenever a document built before would be wrong now: a role's
    # style, the family or the scale changed, the UI font's size did, or the
    # fonts were loaded again (`moved`).
    # Views compare it with the one they were built under.
    epoch: int = 0

    @property
    def body_px(self) -> float:
        """
        The pixel height of running text: the UI font's, times the operator's scale.
        """
        if self.scale == 1.0:
            return self.live_px
        return max(1.0, _half_up(self.live_px * self.scale))


_STATE = _State()


def configure(
    *,
    typeset: bool,
    scale: float | None = None,
    proportional: str | None = None,
    roles: Mapping[Role, RoleStyle] | None = None,
    panes: Mapping[Pane, bool] | None = None,
    mono: bool | None = None,
    faces: str | None = None,
) -> None:
    """
    Tell the seam what the operator's settings say. Call it on any change.

    ``roles`` is every role that differs from ``DEFAULTS``; a role it leaves out is
    at its default, so ``{}`` is the defaults. A slot the role is not offered
    (``SLOTS``) is read as the role's default slot and a step is clamped to
    ``STEPS``. ``proportional`` names the family in the proportional slot; one this
    build does not know is kept as written and the default is used
    (``proportional()``). ``panes`` says which panes are switched on; one it leaves
    out is on.

    ``faces`` and ``mono`` are the same choices spelled as the presets and the
    switch they were before there were roles (``legacy``): ``faces`` sets the family
    and the roles together, ``mono=True`` is ``faces="mono"`` and ``mono=False`` the
    defaults. ``roles`` and ``proportional`` win over ``faces``, which wins over
    ``mono``. A call that gives none of them, or no ``scale``, or no ``panes``,
    leaves that setting as it was.

    A change of a role, of the family or of the scale means every document was
    built in the wrong faces or at the wrong size, so the epoch moves and each
    view is rebuilt the next time it is drawn. Switching a pane moves nothing.
    Callable before the fonts are loaded, and from inside a frame: nothing is
    loaded or registered here. A family whose file the library has not been
    given yet is given to it between frames (``gate.pre_frame``).
    """
    _STATE.typeset = typeset
    before = _in_force()
    if panes is not None:
        _STATE.panes_off = frozenset(pane for pane, on in panes.items() if not on)
    old = faces if faces is not None else None if mono is None else "mono" if mono else ""
    if old is not None:
        family, differing = legacy(old)
        _STATE.family, _STATE.roles = family, checked(differing)
    if proportional is not None:
        _STATE.family = proportional
    if roles is not None:
        _STATE.roles = checked(roles)
    if scale is not None:
        wanted = float(scale)
        # A hand-edited NaN compares false with everything and would pass a clamp.
        _STATE.scale = min(max(wanted, SCALES[0]), SCALES[-1]) if math.isfinite(wanted) else 1.0
    if _in_force() != before:
        moved()


def moved() -> None:
    """
    Say that a document built before now would be wrong: the epoch goes up.
    """
    _STATE.epoch += 1


def follow(size: float) -> None:
    """
    Take the size ImGui draws the UI font at inside a frame (``gate.pre_frame``).

    A change moves the epoch: a document holds advances for the size it was
    built at.
    """
    if abs(size - _STATE.live_px) > 0.01:
        _STATE.live_px = size
        moved()


def checked(differing: Mapping[Role, RoleStyle]) -> dict[Role, RoleStyle]:
    """
    ``DEFAULTS`` with ``differing`` laid over it, each style made one its role allows:
    every role's style as ``configure(roles=differing)`` would leave it. Pure, so a
    caller holding settings and no seam -- the settings dialog's model -- reads the
    same answer the seam will give.
    """
    out = dict(DEFAULTS)
    for role, given in differing.items():
        slot = given.slot if given.slot in SLOTS[role] else DEFAULTS[role].slot
        step = given.step if math.isfinite(given.step) else 0
        out[role] = RoleStyle(slot, int(min(max(step, STEPS[0]), STEPS[-1])))
    return out


def _in_force() -> tuple[object, ...]:
    """
    Everything a built document depends on, to tell a change that needs a rebuild.
    """
    return (tuple(style(role) for role in Role), proportional(), _STATE.body_px)


def legacy(name: str) -> tuple[str, dict[Role, RoleStyle]]:
    """
    What a face preset's name means now: the proportional family, and the roles
    that differ from ``DEFAULTS``. A name that was never a preset is the defaults.

    ``settings.load`` spells the same table for a saved ``typeset_faces``, without
    importing this module; a test holds the two together.
    """
    family, differing = _LEGACY.get(name, _LEGACY[DEFAULT_PRESET])
    return family, dict(differing)


def roles_from(entries: Iterable[tuple[str, str | None, int | None]]) -> dict[Role, RoleStyle]:
    """
    Role styles from how settings keep them: ``(role, slot, step)`` by value, with
    ``None`` for "the default". A role or a slot this build does not know is left
    out, so a file from a later build still loads.
    """
    out: dict[Role, RoleStyle] = {}
    for name, slot, step in entries:
        try:
            role = Role(name)
        except ValueError:
            continue
        base = DEFAULTS[role]
        try:
            chosen = base.slot if slot is None else Slot(slot)
        except ValueError:
            chosen = base.slot
        out[role] = RoleStyle(chosen, base.step if step is None else step)
    return out


def panes_from(entries: Iterable[tuple[str, bool]]) -> dict[Pane, bool]:
    """
    Pane switches from how settings keep them, by value; an unknown pane is left out.
    """
    out: dict[Pane, bool] = {}
    for name, on in entries:
        try:
            out[Pane(name)] = bool(on)
        except ValueError:
            continue
    return out


def chosen(role: Role) -> RoleStyle:
    """
    The style the operator's settings give a role, whether or not it can be had.
    """
    return _STATE.roles[role]


def style(role: Role) -> RoleStyle:
    """
    The style a role is set in. The one chosen: a face the library was given no
    file for is set by the library in the document's face, and nothing here is
    told which.
    """
    return _STATE.roles[role]


def px(role: Role) -> float:
    """
    The pixel height a role's text is set at: the body's, plus the role's step.
    """
    return max(1.0, _STATE.body_px + style(role).step)


def pitch(role: Role, tall: float | None = None) -> float:
    """
    A role's line pitch in pixels, whole: ``LEADING`` times the role's pixel height.

    ``tall`` is the height to take it of, for a heading level's own size.
    """
    return max(1.0, _half_up((px(role) if tall is None else tall) * LEADING[role]))


def proportionals() -> tuple[tuple[Proportional, bool], ...]:
    """
    Every family for the proportional slot with whether it can be chosen now, in
    the order offered.

    One whose file is not there cannot: there would be nothing to register.
    """
    return tuple((each, _usable(each)) for each in PROPORTIONALS)


def proportional() -> Proportional:
    """
    The family in the proportional slot: the one the settings name, or the default.

    The default when the name is not one of ``PROPORTIONALS`` -- a settings file
    from a later build -- and when the family's own file is not in the wheel.
    """
    for each in PROPORTIONALS:
        if each.name == _STATE.family and _usable(each):
            return each
    return PROPORTIONALS[0]


def scale() -> float:
    """
    The operator's multiplier on the UI font's size, as ``configure`` clamped it.
    """
    return _STATE.scale


def _usable(which: Proportional) -> bool:
    path = prose_path(which)
    return path is None or path.is_file()


@cache
def prose_path(which: Proportional) -> Path | None:
    """
    The file a family sets upright text in, or ``None`` for the library's own.

    Kept, because ``proportional()`` is asked whenever the settings are compared
    and resolving a path is a system call.
    """
    if which.file is None:
        return None
    return (_bundle_fonts() / which.file).resolve()


def _bundle_fonts() -> Path:
    import imgui_bundle

    return Path(imgui_bundle.__file__).parent / "assets" / "fonts"


ADOPTED: frozenset[Pane] = frozenset(Pane)
"""
The panes whose typeset branch is built on the library as it is now. ``enabled``
is false for every other pane whatever the switches and the library say, so a
pane not named here draws with its own renderer.

A pane is added by the change that builds its branch and shows it by a run.
BRIEF and BOARD were, on 2026-10-12: the application under Xvfb with the fake
fleet, each pane's cache holding the library's views of what it showed. DETAIL
and INBOX were the same day, for their markdown, and an approval's diff is the
library's only where the library that is running keeps a column
(``diffs.adopted``). CONTEXT was the same day, for the markdown runs of the
log's TYPESET mode. The log's literal runs and DETAIL's error were on
2026-10-13, where the library keeps their columns (``views.literal_unfit``);
an approval's arguments are ImGui's in both panes. That is every pane, and the set stays: a pane
added to ``Pane`` is typeset from the change that names it here.
"""


def _cannot() -> bool:
    return False


_can_draw: Callable[[], bool] = _cannot


def bind(can_draw: Callable[[], bool]) -> None:
    """
    Take the library's half of ``enabled``: whether it can draw in this frame.

    ``gate`` calls this when it is imported, with its ``can_draw``. Until then
    the answer is no, which is the answer for a process that never loads the gate.
    """
    global _can_draw
    _can_draw = can_draw


def enabled(pane: Pane | None = None) -> bool:
    """
    Whether a pane should set its text through this package this frame.

    A pane asks for itself: ``enabled(Pane.BOARD)``. False when the operator turned
    typesetting off, false when that pane is switched off (``pane_on``), false
    when the pane's typeset branch is not built on the library (``ADOPTED``), and
    false when the library cannot draw -- absent, refused, its fonts not loaded,
    or stopped. A pane that asks this and takes the other branch is unchanged.

    With no pane it is whether *any* pane could be: the master switch, the
    library, and some pane's branch built on it. Never true where every pane's
    answer is false for a reason that is not the pane's own switch.
    """
    if pane is None:
        return bool(ADOPTED) and _STATE.typeset and _can_draw()
    if pane not in ADOPTED or pane in _STATE.panes_off:
        return False
    return _STATE.typeset and _can_draw()


def pane_on(pane: Pane) -> bool:
    """
    Whether the operator's settings have a pane switched on, whatever the master
    switch and the faces say.
    """
    return pane not in _STATE.panes_off


def wanted() -> bool:
    """
    Whether the operator's setting asks for typeset text, loaded or not.
    """
    return _STATE.typeset


def mono() -> bool:
    """
    Whether prose is being set in the monospace face: ``Role.PROSE`` is monospace.
    """
    return style(Role.PROSE).slot is Slot.MONOSPACE


def body_px() -> float:
    """
    The pixel height running text is set at: the UI font's, times ``scale()``.
    """
    return _STATE.body_px


def _half_up(value: float) -> float:
    """
    ImGui's ``IM_ROUND``, which is not Python's ``round`` at a half.
    """
    return float(math.floor(value + 0.5))
