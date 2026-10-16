"""
The menu bar: Session, View, Text and Theme, in that order, all of them ours.

Split as ``settings_dialog`` is. ``build`` turns ``Facts`` -- the settings, the
layout on screen, the dockable windows, the card the cursor is on -- into
``Menu`` values in which every item *carries* the ``Act`` that choosing it asks
for. ``draw`` renders them and hands back the one chosen; ``app._perform`` is on
the other end and is the one place an act is carried out. Nothing here touches
the application.

**A chord shown is the chord that acts.** An item's hint is ``chord_label`` of the
very constant its handler reads -- ``launcher.CHORD``, ``settings_dialog.CHORD``,
``app.LAYOUT_KEYS`` -- and never a string typed beside it. ``tests/test_main_menu.py``
presses each hinted chord through its real handler.

**The focused card's actions are in Session**, the ones its own menu offers
(``session_menu.entries``), refused where that menu refuses them and chosen
through ``session_menu.choose``, so the two cannot drift apart.

**View is ours, not hello_imgui's**, so the order is ours. It keeps the two
layouts with their keys, the layout reset, a switch per pane and the status bar.
Left out of what hello_imgui's View had: "View All" and "Hide All", the FPS
submenu (the status bar's frame rate and its idling switch, which
``Settings.fps_idle`` governs), and its theme submenu, which was already off.

**Labels are short** (``LABEL_BUDGET``) and say nothing about themselves: why an
item cannot be chosen is said on hover, and what an item is for is the label. An
item that costs something when chosen says so on hover too (``Item.tip``): hover
text is not the menu's body, which stays one short label per line.

**A heading reads as a heading.** The card's title and its groups ("Gate") are
drawn as separator text, so the only grey lines that look like items are items
that are refused.

**An open menu holds the bare keys** (``Drawn.open``), as the card's own popup
does: the application reads it through ``MenuState.claim_keys``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import assert_never

from imgui_bundle import imgui

from ..approval import Policy
from ..model import AgentRecord, NodeId
from ..settings import Settings
from ..theme import REQUIRED_THEMES, THEMES
from . import launcher, session_menu, settings_dialog, widgets
from .transcript_pane import FIND_CHORD

LABEL_BUDGET = 24
"""
The longest an item's label may be, in characters. The longest label the card
menus already use is 22 ("Retry in a new session"), and the Session menu shows
those labels as they are; at the UI font's 8 px a character, 24 is a menu under
200 px before its hints, which leaves the menu narrower than the fleet pane it
opens over.
"""


# ---------------------------------------------------------------------------
# What an item asks for
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NewTask:
    pass


@dataclass(frozen=True, slots=True)
class OpenSettings:
    pass


@dataclass(frozen=True, slots=True)
class SwitchLayout:
    name: str


@dataclass(frozen=True, slots=True)
class ResetLayout:
    pass


@dataclass(frozen=True, slots=True)
class ShowWindow:
    label: str
    visible: bool


@dataclass(frozen=True, slots=True)
class ShowStatusBar:
    visible: bool


@dataclass(frozen=True, slots=True)
class CardChoice:
    """
    One of the focused card's own items, as ``session_menu.choose`` takes it.
    """

    node: NodeId
    action: session_menu.Action
    rung: Policy | None = None


@dataclass(frozen=True, slots=True)
class ShowEndedSubagents:
    shown: bool


@dataclass(frozen=True, slots=True)
class FindText:
    pass


@dataclass(frozen=True, slots=True)
class SetTheme:
    name: str


@dataclass(frozen=True, slots=True)
class WrapComposers:
    on: bool


@dataclass(frozen=True, slots=True)
class TypesetText:
    on: bool


Act = (
    NewTask
    | OpenSettings
    | SwitchLayout
    | ResetLayout
    | ShowWindow
    | ShowStatusBar
    | CardChoice
    | ShowEndedSubagents
    | FindText
    | SetTheme
    | WrapComposers
    | TypesetText
)


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Item:
    """
    One line that can be chosen. ``hint`` is the chord that does the same, or
    empty. ``ticked`` is a switch's state, or ``None`` for an item that is not
    one. ``refused`` is why it cannot be chosen now, and ``tip`` what choosing it
    would cost; either is said on hover, the refusal first.
    """

    label: str
    act: Act
    hint: str = ""
    ticked: bool | None = None
    refused: str | None = None
    grouped: bool = False
    tip: str | None = None

    @property
    def enabled(self) -> bool:
        return self.refused is None

    @property
    def hover(self) -> str | None:
        """What the item says when the pointer is over it."""
        return self.refused if self.refused is not None else self.tip


@dataclass(frozen=True, slots=True)
class Note:
    """Something the menu says and nobody chooses."""

    text: str
    grouped: bool = False


@dataclass(frozen=True, slots=True)
class Heading:
    """The name of what the items below belong to: a card, a group of its items."""

    text: str


@dataclass(frozen=True, slots=True)
class Rule:
    """A separator."""


Row = Item | Heading | Note | Rule


@dataclass(frozen=True, slots=True)
class Menu:
    label: str
    rows: tuple[Row, ...]


@dataclass(frozen=True, slots=True)
class Window:
    """A dockable window the View menu can show and hide."""

    label: str
    visible: bool


@dataclass(frozen=True, slots=True)
class Card:
    """
    The card the Session menu is for, and the menu its own popup would offer.
    ``gone`` is why none of its items can be chosen any more -- the card left the
    fleet while the menu was open over it -- or None.
    """

    record: AgentRecord
    entries: tuple[session_menu.Entry, ...]
    gone: str | None = None


@dataclass(frozen=True, slots=True)
class Latch:
    """
    The card the menu bar was opened for, kept while any of its menus is open:
    the cursor can move under an open menu with no key pressed (work arriving in
    another session), and a menu that followed it would act on a card nobody
    opened it for. ``card`` is None when it was opened with no card focused.
    """

    card: Card | None


@dataclass(frozen=True, slots=True)
class Facts:
    """
    Everything ``build`` reads. ``layouts`` is each layout's name with the key
    that switches to it (``app.LAYOUT_KEYS``); ``layout`` is the one on screen.
    ``typeset_reason`` is the sentence the Text menu's switch says on hover.
    ``find_refused`` is why no session pane can take a find now, or None.
    """

    settings: Settings
    layout: str
    layouts: tuple[tuple[str, int], ...]
    windows: tuple[Window, ...] = ()
    status_bar: bool = True
    card: Card | None = None
    typeset_available: bool = True
    typeset_obtainable: bool = True
    typeset_reason: str | None = None
    find_refused: str | None = None


SESSION = "Session"
NO_CARD = "no card has the cursor"
CARD_GONE = "this card is no longer on the fleet"
TYPESET_UNOFFERED = "the faces are not there to draw with"
FIND_NO_LOG = "this session's log is not loaded"
FIND_NO_PANE = "no session pane is shown"
FIND_NOT_SHOWN = "the panes now show another card's log"
DISCRETIONARY = "* discretionary"
_HEADING_CHARS = 32


_MODS = (
    (imgui.Key.mod_ctrl, "Ctrl"),
    (imgui.Key.mod_shift, "Shift"),
    (imgui.Key.mod_alt, "Alt"),
    (imgui.Key.mod_super, "Super"),
)
_KEY_NAMES = {
    imgui.Key.comma: ",",
    imgui.Key.escape: "Esc",
    imgui.Key.enter: "Enter",
    imgui.Key.tab: "Tab",
}


def chord_label(chord: int) -> str:
    """
    A chord as a menu shows it: ``Ctrl+N``, ``Ctrl+,``, ``Tab``. Read off the bits
    of the chord itself, so it is whatever the handler is given.
    """
    mask = 0
    names = []
    for mod, name in _MODS:
        mask |= int(mod)
        if chord & int(mod):
            names.append(name)
    key = imgui.Key(chord & ~mask)
    spelled = _KEY_NAMES.get(key)
    if spelled is None:
        name = str(key.name)
        spelled = name.upper() if len(name) == 1 else name.replace("_", " ").title()
    return "+".join([*names, spelled])


def build(facts: Facts) -> tuple[Menu, ...]:
    """
    The menu bar for these facts, left to right.
    """
    return (_session(facts), _view(facts), _text(facts), _theme(facts))


def _session(facts: Facts) -> Menu:
    rows: list[Row] = [
        Item("New task…", NewTask(), chord_label(launcher.CHORD)),
        Item(
            "Find",
            FindText(),
            chord_label(FIND_CHORD),
            refused=NO_CARD if facts.card is None else facts.find_refused,
        ),
    ]
    rows += _card(facts.card)
    shown = not facts.settings.hide_ended_subagents
    # Worded as the settings dialog's switch is, which is the same switch.
    rows += [Rule(), Item("Show ended sub-agents", ShowEndedSubagents(not shown), ticked=shown)]
    return Menu(SESSION, tuple(rows))


def _card(card: Card | None) -> list[Row]:
    """
    The card's own items, under its title. Its headings and lines are notes; an
    item it refuses is refused here, with its reason in few words, and every item
    is refused once the card is gone.
    """
    if card is None:
        return [Rule(), Note(NO_CARD)]
    node = card.record.node_id
    title = widgets.one_line(session_menu._headline(card.record))
    if len(title) > _HEADING_CHARS:
        title = title[: _HEADING_CHARS - 1] + "…"
    # A heading draws its own rule, so none goes above it.
    out: list[Row] = [Heading(title)]
    for entry in card.entries:
        match entry:
            case session_menu.Rule():
                out.append(Rule())
            case session_menu.Heading(label=label):
                out.append(Heading(label))
            case session_menu.Line():
                out.append(Note(entry.text, grouped=entry.grouped))
            case session_menu.Item():
                out.append(
                    Item(
                        entry.label,
                        CardChoice(node, entry.action, entry.rung),
                        refused=(
                            card.gone
                            if card.gone is not None
                            else (
                                None
                                if entry.refused is None
                                else session_menu.few_words(entry.refused)
                            )
                        ),
                        grouped=entry.grouped,
                    )
                )
            case _:
                assert_never(entry)
    return out


def _view(facts: Facts) -> Menu:
    rows: list[Row] = [
        Item(name.title(), SwitchLayout(name), chord_label(key), ticked=facts.layout == name)
        for name, key in facts.layouts
    ]
    rows += [Item("Reset layout", ResetLayout())]
    if facts.windows:
        rows.append(Rule())
        rows += [
            Item(window.label, ShowWindow(window.label, not window.visible), ticked=window.visible)
            for window in facts.windows
        ]
    rows += [
        Rule(),
        Item("Status bar", ShowStatusBar(not facts.status_bar), ticked=facts.status_bar),
    ]
    return Menu("View", tuple(rows))


def _text(facts: Facts) -> Menu:
    settings = facts.settings
    offered = facts.typeset_available or facts.typeset_obtainable
    return Menu(
        "Text",
        (
            Item(
                "Wrap composers",
                WrapComposers(not settings.wrap_inputs),
                ticked=settings.wrap_inputs,
            ),
            Item(
                "Typeset text",
                TypesetText(not settings.typeset),
                ticked=settings.typeset and facts.typeset_available,
                # Not offered while there is nothing to draw with: a ticked switch
                # that changes nothing on screen would read as a broken one.
                refused=None if offered else (facts.typeset_reason or TYPESET_UNOFFERED),
                # Offered, it still says what the click costs: the faces' load,
                # the diff renderer's state.
                tip=facts.typeset_reason,
            ),
            Rule(),
            # Everything else about text is in the dialog, with the rest of what
            # persists. The two switches above stay because each is one click here.
            Item("Settings…", OpenSettings(), chord_label(settings_dialog.CHORD)),
        ),
    )


def _theme(facts: Facts) -> Menu:
    rows: list[Row] = []
    for name, palette in THEMES.items():
        label = palette.display_name if name in REQUIRED_THEMES else f"{palette.display_name} *"
        rows.append(Item(label, SetTheme(name), ticked=facts.settings.theme == name))
    rows += [Rule(), Note(DISCRETIONARY)]
    return Menu("Theme", tuple(rows))


def labels(menus: tuple[Menu, ...]) -> list[str]:
    """Every item's label, for the budget's test."""
    return [row.label for menu in menus for row in menu.rows if isinstance(row, Item)]


# ---------------------------------------------------------------------------
# Dear ImGui
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Drawn:
    """
    What a frame's menu bar came to: the act chosen, if any, and the menu that
    was open, if any.
    """

    chosen: Act | None = None
    open: str | None = None


def opening(menus: tuple[Menu, ...]) -> bool:
    """
    Whether one of ``menus`` is open in ImGui's popup stack though none drew
    open this frame. The pointer moving along an open bar to a menu drawn
    earlier in it -- View back to Session -- closes the later one and yields a
    frame before the earlier one opens (``BeginMenuEx``); the bar is open
    throughout. Called inside the menu bar, where ``begin_menu`` hashed the labels.
    """
    return any(imgui.is_popup_open(menu.label) for menu in menus)


def draw(menus: tuple[Menu, ...], why: Callable[[str], None] | None = None) -> Drawn:
    """
    Draw the menus inside the menu bar and say what was chosen and which was open.

    The act is returned rather than carried out, so that it is carried out after
    every menu is ended: a lever that raised inside a begin/end pair would leave
    the frame unbalanced.
    """
    say = _tooltip if why is None else why
    chosen: Act | None = None
    opened: str | None = None
    for menu in menus:
        if not imgui.begin_menu(menu.label):
            continue
        opened = menu.label
        for number, row in enumerate(menu.rows):
            match row:
                case Rule():
                    imgui.separator()
                case Heading(text=text):
                    if "##" in text:
                        _separator_heading(text)
                    else:
                        imgui.separator_text(text)
                case Note(text=text, grouped=grouped):
                    if grouped:
                        imgui.indent()
                    imgui.text_disabled(text)
                    if grouped:
                        imgui.unindent()
                case Item():
                    if row.grouped:
                        imgui.indent()
                    clicked, _ = imgui.menu_item(
                        f"{row.label}##{menu.label}-{number}",
                        row.hint,
                        bool(row.ticked),
                        row.enabled,
                    )
                    if row.hover is not None:
                        say(row.hover)
                    if row.grouped:
                        imgui.unindent()
                    if clicked and row.enabled:
                        chosen = row.act
                case _:
                    assert_never(row)
        imgui.end_menu()
    return Drawn(chosen, opened)


def _separator_heading(text: str) -> None:
    """
    ``imgui.separator_text`` for a heading with ``##`` in it: the card's title is
    the operator's task text, and Dear ImGui ends a separator's label at its first
    ``##`` with no way to escape one (the binding's ``separator_text_ex`` takes
    its end as a second string, not a place in the first). The same steps as
    ``ImGui::SeparatorTextEx`` in 1.92, but drawing the text whole with
    ``ImDrawList::AddText``, which reads no ``##``; ``tests/test_main_menu.py``
    holds its vertices equal to ``separator_text``'s for a label without one.
    The title is cut to ``_HEADING_CHARS`` before it gets here, and the menu is
    sized to its widest row, so the ellipsis SeparatorTextEx would draw is not
    reached.
    """
    style = imgui.get_style()
    window = imgui.internal.get_current_window()
    size = imgui.calc_text_size(text)
    pos = imgui.get_cursor_screen_pos()
    pad = style.separator_text_padding
    thickness = style.separator_text_border_size
    least = imgui.ImVec2(size.x + pad.x * 2, max(size.y + pad.y * 2, thickness))
    box = imgui.internal.ImRect(pos, imgui.ImVec2(window.work_rect.max.x, pos.y + least.y))
    baseline = math.trunc((box.get_height() - size.y) * style.separator_text_align.y + 0.99999)
    imgui.internal.item_size(least, baseline)
    if not imgui.internal.item_add(box, 0) or size.x <= 0:
        return
    left, right = pos.x, box.max.x
    rule_y = math.trunc((box.min.y + box.max.y) * 0.5 + 0.99999)
    room = max(0.0, right - left - pad.x * 2)
    text_x = pos.x + pad.x + (room - size.x) * style.separator_text_align.x
    colour = imgui.get_color_u32(imgui.Col_.separator)
    draw = imgui.get_window_draw_list()
    before, after = text_x - style.item_spacing.x, text_x + size.x + style.item_spacing.x
    if before > left and thickness > 0:
        draw.add_line(imgui.ImVec2(left, rule_y), imgui.ImVec2(before, rule_y), colour, thickness)
    if right > after and thickness > 0:
        draw.add_line(imgui.ImVec2(after, rule_y), imgui.ImVec2(right, rule_y), colour, thickness)
    draw.add_text(
        imgui.ImVec2(text_x, pos.y + baseline), imgui.get_color_u32(imgui.Col_.text), text
    )


def _tooltip(text: str) -> None:
    if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
        imgui.set_tooltip(text)
