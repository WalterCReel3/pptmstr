"""
The menu bar: its model per state, its hints against the handlers that act on them,
and what each item does once chosen.

The model is values (``main_menu.build``), so most of this needs no ImGui. The hints
do: the claim is that the chord a menu shows is the chord that acts, and the only
evidence of that is pressing the chord the menu shows, spelled back into keys by
this file, through the handler the application runs, in a real frame.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui

from pptmstr import app
from pptmstr import settings as settings_mod
from pptmstr.approval import Policy
from pptmstr.intents import AgentFinished, AgentSpawned, StateChanged
from pptmstr.model import AgentState, NodeId
from pptmstr.settings import Settings
from pptmstr.store import Store
from pptmstr.ui import launcher, main_menu, session_menu, settings_dialog, transcript_pane
from pptmstr.ui.main_menu import (
    CardChoice,
    Facts,
    Item,
    Menu,
    Note,
    OpenSettings,
    SwitchLayout,
    build,
)

ROOT: NodeId = ("s1", None)
SUB: NodeId = ("s1", "agent-b")


def store(state: AgentState = AgentState.THINKING) -> Store:
    held = Store()
    for node, parent in ((ROOT, None), (SUB, ROOT)):
        held.apply(
            AgentSpawned(
                node_id=node,
                parent=parent,
                task="rotate the signing key",
                model="claude-opus-5",
                started_at=0.0,
                agent_type=None if parent is None else "builder",
                cwd=None if parent else "/x/keys",
                policy=None if parent else Policy.PERMISSIVE,
            ),
            now=1.0,
        )
        held.apply(StateChanged(node, AgentState.THINKING), now=1.0)
    if state.is_terminal:
        held.apply(AgentFinished(ROOT, state, 2.0, error="the CLI exited 1"), now=2.0)
    return held


def facts(card: main_menu.Card | None = None, **given: Any) -> Facts:
    return Facts(
        settings=given.pop("settings", Settings()),
        layout=given.pop("layout", app.TRIAGE),
        layouts=tuple(app.LAYOUT_KEYS.items()),
        card=card,
        **given,
    )


def card_of(
    node: NodeId, state: AgentState = AgentState.THINKING, policy: Policy | None = Policy.PERMISSIVE
) -> main_menu.Card:
    record = store(state).snapshot().get(node)
    assert record is not None
    return main_menu.Card(record, session_menu.entries(record, policy))


def menu(menus: tuple[Menu, ...], label: str) -> Menu:
    return next(each for each in menus if each.label == label)


def items(of: Menu) -> list[Item]:
    return [row for row in of.rows if isinstance(row, Item)]


def item(of: Menu, label: str) -> Item:
    return next(each for each in items(of) if each.label == label)


# -- the model -------------------------------------------------------------------------


def test_the_bar_is_ours_and_in_our_order() -> None:
    assert [each.label for each in build(facts())] == ["Session", "View", "Text", "Theme"]


def test_with_no_card_focused_session_says_so_and_offers_no_card_item() -> None:
    session = menu(build(facts()), "Session")
    assert Note(main_menu.NO_CARD) in session.rows
    assert not [each for each in items(session) if isinstance(each.act, CardChoice)]
    assert [each.label for each in items(session)] == [
        "New task…",
        "Find",
        "Show ended sub-agents",
    ]
    assert item(session, "Find").refused == main_menu.NO_CARD, "find has nothing to look in"


@pytest.mark.parametrize(
    ("node", "state"),
    [(ROOT, AgentState.THINKING), (ROOT, AgentState.FAILED), (SUB, AgentState.THINKING)],
    ids=["working root", "failed root", "sub-agent"],
)
def test_the_focused_cards_items_are_its_own_menus_items_refused_as_it_refuses_them(
    node: NodeId, state: AgentState
) -> None:
    """
    Not a copy of the card's list: the card's own entries, whatever they are for
    this card, each choosing the card's action with its rung.
    """
    card = card_of(node, state)
    session = menu(build(facts(card)), "Session")
    offered = [each for each in card.entries if isinstance(each, session_menu.Item)]
    shown = [each for each in items(session) if isinstance(each.act, CardChoice)]

    assert [each.label for each in shown] == [each.label for each in offered]
    for drawn, own in zip(shown, offered, strict=True):
        assert drawn.act == CardChoice(node, own.action, own.rung)
        assert (drawn.refused is None) is own.enabled
        if own.refused is not None:
            assert drawn.refused == session_menu.few_words(own.refused)
        assert drawn.grouped is own.grouped
    assert Note(main_menu.NO_CARD) not in session.rows
    headings = [each.text for each in session.rows if isinstance(each, main_menu.Heading)]
    own_headings = [each.label for each in card.entries if isinstance(each, session_menu.Heading)]
    assert headings == [session_menu._headline(card.record), *own_headings], "headings, not notes"
    assert not {each.text for each in session.rows if isinstance(each, Note)} & set(headings)


def test_find_is_offered_for_a_card_a_log_pane_can_take_and_refused_with_why_otherwise() -> None:
    session = menu(build(facts(card_of(ROOT))), "Session")
    assert item(session, "Find").enabled and item(session, "Find").act == main_menu.FindText()
    refused = menu(build(facts(card_of(ROOT), find_refused=main_menu.FIND_NO_PANE)), "Session")
    assert item(refused, "Find").refused == main_menu.FIND_NO_PANE


def test_view_has_the_layouts_with_their_keys_the_panes_and_the_status_bar() -> None:
    view = menu(
        build(
            facts(
                layout=app.FOCUS,
                windows=(main_menu.Window("FLEET", True), main_menu.Window("LOG", False)),
                status_bar=False,
            )
        ),
        "View",
    )
    assert [each.label for each in items(view)] == [
        "Triage",
        "Focus",
        "Reset layout",
        "FLEET",
        "LOG",
        "Status bar",
    ]
    assert item(view, "Focus").ticked and not item(view, "Triage").ticked
    assert item(view, "Triage").act == SwitchLayout(app.TRIAGE)
    assert item(view, "LOG").act == main_menu.ShowWindow("LOG", True)
    assert item(view, "FLEET").act == main_menu.ShowWindow("FLEET", False)
    assert item(view, "Status bar").act == main_menu.ShowStatusBar(True)


@pytest.mark.parametrize(
    ("available", "obtainable", "reason", "offered"),
    [
        (True, True, None, True),
        (False, True, "faces load when this is switched on: about a second", True),
        (False, False, "the text library did not import (ImportError: gone)", False),
    ],
)
def test_the_typeset_switch_is_offered_only_when_it_can_work_and_says_why_when_it_cannot(
    available: bool, obtainable: bool, reason: str | None, offered: bool
) -> None:
    text = menu(
        build(
            facts(
                settings=Settings(typeset=False),
                typeset_available=available,
                typeset_obtainable=obtainable,
                typeset_reason=reason,
            )
        ),
        "Text",
    )
    switch = item(text, "Typeset text")
    assert switch.enabled is offered and not switch.ticked
    assert switch.refused == (None if offered else reason)
    assert switch.hover == reason, "offered or not, the switch says what it costs on hover"
    assert switch.act == main_menu.TypesetText(True)
    assert [each.label for each in items(text)] == [
        "Wrap composers",
        "Typeset text",
        "Settings…",
    ], "the rest of text is the dialog's"


def test_every_label_is_short_and_a_second_step_ends_in_the_ellipsis() -> None:
    """
    Over every state that changes what is listed: no card, each kind of card,
    every theme.
    """
    seen: list[str] = []
    for card in (
        None,
        card_of(ROOT),
        card_of(ROOT, AgentState.FAILED, None),
        card_of(ROOT, AgentState.DONE, None),
        card_of(SUB),
    ):
        menus = build(facts(card, windows=(main_menu.Window("NEEDS YOU", True),)))
        seen += main_menu.labels(menus)
    assert max(len(label) for label in seen) <= main_menu.LABEL_BUDGET, max(seen, key=len)
    assert not [label for label in seen if "..." in label]
    assert {"New task…", "Settings…"} <= set(seen)


# -- the hints and the handlers -----------------------------------------------------------


def test_a_hint_is_the_name_of_the_constant_its_handler_reads() -> None:
    menus = build(facts())
    assert item(menu(menus, "Session"), "New task…").hint == "Ctrl+N"
    assert item(menu(menus, "Text"), "Settings…").hint == "Ctrl+,"
    assert item(menu(menus, "View"), "Triage").hint == "Tab"
    assert item(menu(menus, "View"), "Focus").hint == "Enter"
    assert item(menu(menus, "Session"), "Find").hint == "Ctrl+F"
    assert main_menu.chord_label(main_menu.FIND_CHORD) == transcript_pane.FIND_LABEL
    assert main_menu.chord_label(launcher.CHORD) == "Ctrl+N"
    assert main_menu.chord_label(settings_dialog.CHORD) == settings_dialog.CHORD_LABEL


@pytest.mark.parametrize(
    ("mods", "key", "label"),
    [
        (("Ctrl",), imgui.Key.n, "Ctrl+N"),
        (("Ctrl", "Shift"), imgui.Key.k, "Ctrl+Shift+K"),
        (("Alt",), imgui.Key.f5, "Alt+F5"),
        ((), imgui.Key.m, "M"),
    ],
)
def test_a_key_is_spelled_as_imgui_names_it(
    context: None, mods: tuple[str, ...], key: imgui.Key, label: str
) -> None:
    chord = int(key)
    for name in mods:
        chord |= int(_SPELLED[name])
    assert main_menu.chord_label(chord) == label == "+".join([*mods, imgui.get_key_name(key)])


_SPELLED = {
    "Ctrl": imgui.Key.mod_ctrl,
    "Shift": imgui.Key.mod_shift,
    "Alt": imgui.Key.mod_alt,
    ",": imgui.Key.comma,
    "Tab": imgui.Key.tab,
    "Enter": imgui.Key.enter,
}


def keys_of(hint: str) -> list[imgui.Key]:
    """A hint as an operator reads it, back into the keys they would press."""
    return [_SPELLED.get(part) or imgui.Key[part.lower()] for part in hint.split("+")]


@pytest.fixture
def context() -> Iterator[None]:
    made = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(800.0, 600.0)
    io.delta_time = 1 / 60
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.fonts.add_font_default()
    try:
        yield
    finally:
        imgui.destroy_context(made)


class _Layouts:
    """Stands in for ``hello_imgui``: the layout shown, and every switch asked for."""

    def __init__(self, shown: str) -> None:
        self.shown = shown
        self.switched: list[str] = []

    def current_layout_name(self) -> str:
        return self.shown

    def switch_layout(self, name: str) -> None:
        self.switched.append(name)
        self.shown = name


def pressed(keys: list[imgui.Key], body: Callable[[], None]) -> None:
    """
    A frame running ``body``, then the keys down for one and up again for one: a
    shortcut's route is granted from a frame drawn before the keys go down.
    """
    io = imgui.get_io()

    def frame() -> None:
        imgui.new_frame()
        imgui.begin("##menu-test")
        body()
        imgui.end()
        imgui.render()

    frame()
    for key in keys:
        io.add_key_event(key, True)
    frame()
    for key in reversed(keys):
        io.add_key_event(key, False)
    frame()


def test_pressing_the_hint_of_new_task_and_settings_does_what_choosing_them_does(
    context: None,
) -> None:
    menus = build(facts())
    state = app.AppState(store=Store(), bridge=MagicMock(), settings=Settings())

    pressed(
        keys_of(item(menu(menus, "Session"), "New task…").hint),
        lambda: launcher.handle_shortcut(state.launcher),
    )
    assert state.launcher._open_requested

    pressed(
        keys_of(item(menu(menus, "Text"), "Settings…").hint),
        lambda: settings_dialog.handle_shortcut(state.settings_dialog),
    )
    assert state.settings_dialog._open_requested


def test_pressing_the_hint_of_find_opens_the_panes_find_row_as_choosing_it_does(
    context: None,
) -> None:
    """
    Through the pane's own handler, in a window that has the focus as the pane
    has it; and the menu's act opens the same row.
    """
    hint = item(menu(build(facts(card_of(ROOT))), "Session"), "Find").hint
    keyed = transcript_pane.FindBar()
    pressed(keys_of(hint), lambda: transcript_pane._find_keys(keyed))
    assert keyed.open and keyed.focus

    state = app.AppState(store=Store(), bridge=MagicMock(), settings=Settings())
    app._perform(state, main_menu.FindText())
    chosen = state.transcripts.find
    assert (chosen.open, chosen.focus, chosen.reveal) == (keyed.open, keyed.focus, keyed.reveal)


@pytest.mark.parametrize(("start", "label"), [(app.FOCUS, "Triage"), (app.TRIAGE, "Focus")])
def test_pressing_a_layouts_hint_switches_to_that_layout(
    context: None, monkeypatch: pytest.MonkeyPatch, start: str, label: str
) -> None:
    layouts = _Layouts(start)
    monkeypatch.setattr(app, "hello_imgui", layouts)
    state = app.AppState(store=Store(), bridge=MagicMock(), settings=Settings())
    state.frame_snap = state.store.snapshot()
    chosen = item(menu(build(facts(layout=start)), "View"), label)

    pressed(keys_of(chosen.hint), lambda: app._handle_layout_keys(state))

    assert isinstance(chosen.act, SwitchLayout)
    assert layouts.switched == [chosen.act.name]


# -- what choosing an item does --------------------------------------------------------------


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> list[Settings]:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    record: list[Settings] = []
    monkeypatch.setattr(
        settings_mod, "save", lambda settings, path=None, *, unread: record.append(settings)
    )
    return record


@pytest.mark.parametrize(
    ("menu_label", "label", "expected"),
    [
        ("Text", "Wrap composers", Settings(wrap_inputs=False)),
        ("Theme", "Light", Settings(theme="light")),
        ("Session", "Show ended sub-agents", Settings(hide_ended_subagents=False)),
    ],
)
def test_what_was_one_click_in_a_menu_still_is(
    saved: list[Settings], menu_label: str, label: str, expected: Settings
) -> None:
    from pptmstr import theme

    state = app.AppState(store=Store(), bridge=MagicMock(), settings=Settings())
    try:
        app._perform(state, item(menu(build(facts()), menu_label), label).act)
        assert state.settings == expected and saved == [expected]
    finally:
        theme.set_theme("dark")


def test_settings_and_new_task_ask_for_their_modals_and_change_nothing(
    saved: list[Settings],
) -> None:
    state = app.AppState(store=Store(), bridge=MagicMock(), settings=Settings())
    menus = build(facts())
    app._perform(state, item(menu(menus, "Text"), "Settings…").act)
    app._perform(state, item(menu(menus, "Session"), "New task…").act)
    assert state.settings_dialog._open_requested and state.launcher._open_requested
    assert saved == [] and state.settings == Settings()
    assert item(menu(menus, "Text"), "Settings…").act == OpenSettings()


def test_a_layout_chosen_is_switched_to_at_the_next_frame() -> None:
    state = app.AppState(store=Store(), bridge=MagicMock(), settings=Settings())
    app._perform(state, item(menu(build(facts()), "View"), "Focus").act)
    assert state.pending_layout == app.FOCUS


def test_a_card_item_chosen_is_the_cards_own_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Through ``session_menu.choose``, as the card's popup does: Details asks for
    the details of that card, and an item the card refuses does nothing.
    """
    held = store()
    state = app.AppState(store=held, bridge=MagicMock(), settings=Settings())
    state.frame_snap = held.snapshot()
    chosen: list[tuple[Any, ...]] = []
    real = session_menu.choose

    def choose(action: Any, rec: Any, policy: Any, cards: Any, actions: Any, **kw: Any) -> None:
        chosen.append((action, rec.node_id, kw.get("rung")))
        real(action, rec, policy, cards, actions, **kw)

    monkeypatch.setattr(session_menu, "choose", choose)
    app._perform(state, CardChoice(ROOT, session_menu.Action.DETAILS))
    assert chosen == [(session_menu.Action.DETAILS, ROOT, None)]
    assert state.cards.details.node == ROOT

    app._perform(state, CardChoice(("gone", None), session_menu.Action.DETAILS))
    assert len(chosen) == 1, "a card the snapshot no longer has is nothing to choose for"


# -- the draw ----------------------------------------------------------------------------------


class _Bar:
    """The ImGui calls ``main_menu.draw`` makes, with one item that reports a click."""

    def __init__(self, click: str) -> None:
        self.click = click
        self.items: dict[str, tuple[str, bool, bool]] = {}
        self.headings: list[str] = []
        self.notes: list[str] = []

    def begin_menu(self, label: str, enabled: bool = True) -> bool:
        return True

    def end_menu(self) -> None:
        pass

    def menu_item(
        self, label: str, shortcut: str, selected: bool, enabled: bool = True
    ) -> tuple[bool, bool]:
        shown = label.split("##")[0]
        self.items[shown] = (shortcut, selected, enabled)
        return (shown == self.click, selected)

    def separator(self) -> None:
        pass

    def separator_text(self, text: str) -> None:
        self.headings.append(text)

    def text_disabled(self, text: str) -> None:
        self.notes.append(text)

    def indent(self) -> None:
        pass

    def unindent(self) -> None:
        pass


@pytest.mark.parametrize("click", ["Settings…", "Interrupt", "Light"])
def test_the_draw_hands_back_what_was_chosen_and_draws_each_item_as_modelled(
    monkeypatch: pytest.MonkeyPatch, click: str
) -> None:
    menus = build(facts(card_of(ROOT)))
    bar = _Bar(click)
    monkeypatch.setattr(main_menu, "imgui", bar)
    said: list[str] = []

    drawn = main_menu.draw(menus, why=said.append)

    every = {row.label: row for each in menus for row in items(each)}
    assert drawn.chosen == every[click].act
    for label, row in every.items():
        assert bar.items[label] == (row.hint, bool(row.ticked), row.enabled), label
    assert said == [row.hover for row in every.values() if row.hover is not None]
    rows = [row for each in menus for row in each.rows]
    assert bar.headings == [row.text for row in rows if isinstance(row, main_menu.Heading)]
    assert bar.notes == [row.text for row in rows if isinstance(row, Note)]
    assert bar.headings and not set(bar.headings) & set(bar.notes), "a heading is not grey"


def test_a_refused_item_is_never_handed_back(monkeypatch: pytest.MonkeyPatch) -> None:
    class Clicking(_Bar):
        def menu_item(
            self, label: str, shortcut: str, selected: bool, enabled: bool = True
        ) -> tuple[bool, bool]:
            super().menu_item(label, shortcut, selected, enabled)
            return (label.split("##")[0] == self.click, selected)

    menus = build(facts(typeset_available=False, typeset_obtainable=False))
    bar = Clicking("Typeset text")
    monkeypatch.setattr(main_menu, "imgui", bar)
    assert main_menu.draw(menus, why=lambda text: None).chosen is None


@pytest.mark.parametrize("shown", ["View", None])
def test_the_draw_says_which_menu_was_open(
    monkeypatch: pytest.MonkeyPatch, shown: str | None
) -> None:
    class Opening(_Bar):
        def begin_menu(self, label: str, enabled: bool = True) -> bool:
            return label == shown

    menus = build(facts())
    monkeypatch.setattr(main_menu, "imgui", Opening(""))
    assert main_menu.draw(menus, why=lambda text: None) == main_menu.Drawn(None, shown)


# -- what the bar is built from -----------------------------------------------------------


class _Runner:
    """``hello_imgui``'s runner parameters, as far as the menu bar reads them."""

    def __init__(self) -> None:
        def window(label: str, visible: bool, listed: bool = True) -> Any:
            made = MagicMock()
            made.label, made.is_visible = label, visible
            made.include_in_view_menu, made.can_be_closed = listed, True
            return made

        self.docking_params = MagicMock()
        self.docking_params.dockable_windows = [
            window("FLEET", True),
            window("LOG", False),
            window("hidden from the menu", True, listed=False),
        ]
        self.docking_params.layout_reset = False
        self.imgui_window_params = MagicMock()
        self.imgui_window_params.show_status_bar = True


class _Hello(_Layouts):
    def __init__(self, shown: str) -> None:
        super().__init__(shown)
        self.runner = _Runner()

    def get_runner_params(self) -> _Runner:
        return self.runner


def test_the_bar_is_built_from_the_runner_the_seam_and_the_card_the_cursor_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptmstr.ui import typeset
    from pptmstr.ui.focus import OnNode

    hello = _Hello(app.FOCUS)
    monkeypatch.setattr(app, "hello_imgui", hello)
    monkeypatch.setattr(typeset, "available", lambda: False)
    monkeypatch.setattr(typeset, "obtainable", lambda: False)
    monkeypatch.setattr(typeset, "unavailable_reason", lambda: "the seam's own sentence")
    held = store()
    state = app.AppState(store=held, bridge=MagicMock(), settings=Settings())
    state.frame_snap = held.snapshot()

    told = app._menu_facts(state)
    assert told.layout == app.FOCUS and told.layouts == tuple(app.LAYOUT_KEYS.items())
    assert told.windows == (main_menu.Window("FLEET", True), main_menu.Window("LOG", False))
    assert told.card is None, "nothing has the cursor"
    assert (told.typeset_available, told.typeset_obtainable) == (False, False)
    assert told.typeset_reason == "the seam's own sentence"

    state.focus.target = OnNode(SUB, pinned=True)
    card = app._menu_facts(state).card
    assert card is not None and card.record.node_id == SUB
    assert card.entries == session_menu.entries(
        card.record, None, app._card_wiring(state).facts(SUB)
    )


def test_the_runner_switches_are_carried_out_on_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    hello = _Hello(app.TRIAGE)
    monkeypatch.setattr(app, "hello_imgui", hello)
    state = app.AppState(store=Store(), bridge=MagicMock(), settings=Settings())

    app._perform(state, main_menu.ShowWindow("LOG", True))
    app._perform(state, main_menu.ShowStatusBar(False))
    app._perform(state, main_menu.ResetLayout())

    windows = {each.label: each.is_visible for each in hello.runner.docking_params.dockable_windows}
    assert windows["LOG"] is True and windows["FLEET"] is True
    assert hello.runner.imgui_window_params.show_status_bar is False
    assert hello.runner.docking_params.layout_reset is True


# -- an open menu holds the keys -------------------------------------------------------------

A: NodeId = ("s-a", None)
B: NodeId = ("s-b", None)


def two_failed_cards() -> Store:
    """Two cards in NEEDS YOU, A above B: a failure is owed, so j/k walk them."""
    held = Store()
    for at, node in enumerate((A, B)):
        held.apply(
            AgentSpawned(
                node_id=node,
                parent=None,
                task=f"task {node[0]}",
                model="claude-opus-5",
                started_at=float(at),
                cwd="/x",
                policy=Policy.PERMISSIVE,
            ),
            now=1.0 + at,
        )
        held.apply(AgentFinished(node, AgentState.FAILED, 3.0 + at, error="exit 1"), now=3.0 + at)
    return held


def test_an_open_session_menu_holds_the_bare_keys_so_it_stays_the_cards_it_opened_for(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Opened over B, the Session menu lists B's items. A ``k`` (or ``j``) pressed
    while it is open is the menu's and not the cursor's, as it is for the card's
    own popup: otherwise the menu rebuilds for A under the pointer and the next
    click acts on a card nobody opened it for. Driven as the frame runs it: the
    keys read before the frame, then the bar.
    """
    monkeypatch.setattr(app, "hello_imgui", _Hello(app.TRIAGE))
    held = two_failed_cards()
    state = app.AppState(store=held, bridge=MagicMock(), settings=Settings())
    state.frame_snap = held.snapshot()
    state.focus.move(state.frame_snap, 1)
    state.focus.move(state.frame_snap, 1)
    assert state.focus.node(state.frame_snap) == B, "B is below A, so k would reach A"
    built: list[NodeId | None] = []
    real_build = main_menu.build

    def build(facts: Facts) -> tuple[Menu, ...]:
        built.append(None if facts.card is None else facts.card.record.node_id)
        return real_build(facts)

    monkeypatch.setattr(main_menu, "build", build)
    opened: list[str] = []
    real_begin = imgui.begin_menu

    def begin_menu(label: str, enabled: bool = True) -> bool:
        shown = bool(real_begin(label, enabled))
        if shown:
            opened.append(label)
        return shown

    monkeypatch.setattr(main_menu.imgui, "begin_menu", begin_menu)
    io = imgui.get_io()

    def frame() -> None:
        app._handle_keys(state)
        imgui.new_frame()
        if imgui.begin_main_menu_bar():
            app._menus(state)
            imgui.end_main_menu_bar()
        imgui.render()

    frame()
    io.add_mouse_pos_event(16.0, 6.0)
    frame()
    io.add_mouse_button_event(0, True)
    frame()
    io.add_mouse_button_event(0, False)
    frame()
    opened.clear()
    frame()
    assert opened == ["Session"], "the Session menu is open"
    built.clear()

    for key in (imgui.Key.k, imgui.Key.j, imgui.Key.k):
        io.add_key_event(key, True)
        frame()
        io.add_key_event(key, False)
        frame()

    assert state.focus.node(state.frame_snap) == B, "the cursor did not move under the menu"
    assert built and set(built) == {B}, "and every frame's Session menu was B's"
    assert opened.count("Session") == 1 + 2 * 3, "open throughout"


def two_running_cards() -> Store:
    """Two sessions mid-turn, A above B, nothing owed: Interrupt is offered on both."""
    held = Store()
    for at, node in enumerate((A, B)):
        held.apply(
            AgentSpawned(
                node_id=node,
                parent=None,
                task=f"task {node[0]}",
                model="claude-opus-5",
                started_at=float(at),
                cwd="/x",
                policy=Policy.PERMISSIVE,
            ),
            now=1.0 + at,
        )
        held.apply(StateChanged(node, AgentState.THINKING), now=1.0 + at)
    return held


def parked(node: NodeId) -> Any:
    from pptmstr.intents import ApprovalRequested
    from pptmstr.model import PendingApproval

    return ApprovalRequested(
        node,
        PendingApproval(
            id=f"p-{node[0]}",
            node=node,
            tool_name="Bash",
            tool_use_id=f"t-{node[0]}",
            raw_args={"command": "ls"},
            summary="ls",
            requested_at=10.0,
        ),
    )


class _OpenSession:
    """
    A real frame loop with the Session menu opened by a mouse click, as the
    application runs it: the store's news, the cursor settled, the keys, the bar.
    Records every build's card and every card choice carried out, and clicks the
    item ``click`` names on the frame it is set.
    """

    def __init__(self, held: Store, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(app, "hello_imgui", _Hello(app.TRIAGE))
        monkeypatch.setattr(app, "_policy_of", lambda state, node: Policy.PERMISSIVE)
        self.held = held
        self.state = app.AppState(store=held, bridge=MagicMock(), settings=Settings())
        self.built: list[NodeId | None] = []
        self.chosen: list[tuple[session_menu.Action, NodeId]] = []
        self.offered: dict[str, bool] = {}
        self.click: str | None = None
        real_build = main_menu.build
        real_item = imgui.menu_item

        def build(facts: Facts) -> tuple[Menu, ...]:
            self.built.append(None if facts.card is None else facts.card.record.node_id)
            return real_build(facts)

        def menu_item(label: str, hint: str, ticked: bool, enabled: bool = True) -> Any:
            clicked, now = real_item(label, hint, ticked, enabled)
            shown = label.split("##")[0]
            self.offered[shown] = enabled
            return (clicked or shown == self.click, now)

        def choose(action: Any, rec: Any, *args: Any, **kw: Any) -> None:
            self.chosen.append((action, rec.node_id))

        self.at: dict[str, tuple[float, float]] = {}
        self.shown: str | None = None
        real_begin = imgui.begin_menu

        def begin_menu(label: str, enabled: bool = True) -> bool:
            shown = bool(real_begin(label, enabled))
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            self.at[label] = ((low.x + high.x) / 2, (low.y + high.y) / 2)
            if shown:
                self.shown = label
            return shown

        monkeypatch.setattr(main_menu, "build", build)
        monkeypatch.setattr(imgui, "menu_item", menu_item)
        monkeypatch.setattr(imgui, "begin_menu", begin_menu)
        monkeypatch.setattr(session_menu, "choose", choose)

    def hover(self, label: str) -> None:
        """The pointer over a menu's name, and two frames for the bar to follow it."""
        imgui.get_io().add_mouse_pos_event(*self.at[label])
        self.frame()
        self.frame()

    def frame(self) -> None:
        state = self.state
        state.frame_snap = self.held.snapshot()
        state.focus.settle(state.frame_snap)
        app._handle_keys(state)
        self.shown = None
        imgui.new_frame()
        if imgui.begin_main_menu_bar():
            app._menus(state)
            imgui.end_main_menu_bar()
        imgui.render()
        self.click = None

    def open(self) -> None:
        io = imgui.get_io()
        self.frame()
        io.add_mouse_pos_event(16.0, 6.0)
        self.frame()
        io.add_mouse_button_event(0, True)
        self.frame()
        io.add_mouse_button_event(0, False)
        self.frame()
        self.built.clear()


def test_an_open_session_menu_acts_on_its_card_when_work_elsewhere_moves_the_cursor(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Opened over A with nothing owed, the Session menu is A's. B then asks for an
    approval, and the cursor -- nothing pinned it -- goes to B's request with no
    key pressed. The open menu stays A's, and Interrupt clicked in it interrupts
    A, not the session that happened to ask for something under it.
    """
    from pptmstr.ui.focus import OnNode

    held = two_running_cards()
    run = _OpenSession(held, monkeypatch)
    run.state.focus.target = OnNode(A)
    run.open()
    run.frame()
    assert run.built and set(run.built) == {A}, "opened over A"

    held.apply(parked(B), now=10.0)
    run.frame()
    run.frame()
    snap = run.state.frame_snap
    assert snap is not None and run.state.focus.node(snap) == B, "the queue moved the cursor"
    assert set(run.built) == {A}, "every frame's Session menu was A's"

    run.click = "Interrupt"
    run.frame()
    assert run.chosen == [(session_menu.Action.INTERRUPT, A)]


@pytest.mark.parametrize("how", ["removed", "off the fleet"])
def test_an_open_session_menu_refuses_its_cards_items_once_that_card_is_gone(
    context: None, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """
    Opened over A, and A leaves while it is open -- its record taken away, or its
    session off the fleet: the menu does not become the next card's. It keeps
    A's items, every one refused with why, so nothing is chosen for a card
    nobody opened it for.
    """
    from pptmstr.intents import AgentRemoved
    from pptmstr.model import Snapshot
    from pptmstr.ui.focus import OnNode

    held = two_running_cards()
    run = _OpenSession(held, monkeypatch)
    run.state.focus.target = OnNode(A, pinned=True)
    run.open()
    run.frame()
    assert run.offered["Interrupt"] is True

    if how == "removed":
        held.apply(AgentRemoved(A), now=10.0)
    else:
        monkeypatch.setattr(Snapshot, "off_fleet", lambda snap, session: session == A[0])
    run.offered.clear()
    run.frame()
    run.frame()
    assert set(run.built) == {A}
    card_items = {"Interrupt", "Fork", "Details", "Health"}
    assert card_items <= run.offered.keys()
    assert not any(run.offered[label] for label in card_items), "refused"
    assert run.offered["Find"] is False

    run.click = "Interrupt"
    run.frame()
    assert run.chosen == []

    facts = app._menu_facts(run.state)
    assert facts.card is not None and facts.card.gone == main_menu.CARD_GONE


def test_a_closed_session_menu_opens_again_for_the_card_the_cursor_is_on_then(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The latch is the opening's: closed and opened again, it is the cursor's card."""
    from pptmstr.ui.focus import OnNode

    held = two_running_cards()
    run = _OpenSession(held, monkeypatch)
    run.state.focus.target = OnNode(A, pinned=True)
    run.open()
    run.frame()
    assert run.state.menu_latch is not None, "open, and latched"
    io = imgui.get_io()
    io.add_mouse_pos_event(400.0, 400.0)
    run.frame()
    io.add_mouse_button_event(0, True)
    run.frame()
    io.add_mouse_button_event(0, False)
    run.frame()
    assert run.state.menu_latch is None, "closed by a click away from it"

    run.state.focus.target = OnNode(B, pinned=True)
    run.open()
    run.frame()
    assert set(run.built) == {B}


def test_session_left_for_another_menu_and_back_is_still_the_card_it_opened_for(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Opened over A, B asks for an approval and the unpinned cursor goes to it;
    the pointer then slides along the bar to View and back to Session. The bar
    never closed, so Session is still A's and Interrupt in it interrupts A.
    """
    from pptmstr.ui.focus import OnNode

    held = two_running_cards()
    run = _OpenSession(held, monkeypatch)
    run.state.focus.target = OnNode(A)
    run.open()
    run.frame()
    held.apply(parked(B), now=10.0)
    run.frame()
    snap = run.state.frame_snap
    assert snap is not None and run.state.focus.node(snap) == B, "the queue moved the cursor"

    run.hover("View")
    assert run.shown == "View" and run.state.menu_latch is not None, "the bar is still open"
    run.hover("Session")
    run.built.clear()
    run.frame()
    assert run.shown == "Session" and set(run.built) == {A}

    run.click = "Interrupt"
    run.frame()
    assert run.chosen == [(session_menu.Action.INTERRUPT, A)]


def test_a_bar_opened_on_another_menu_holds_the_card_the_cursor_was_on_then(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The bar opened by a click on View over A; the cursor moves to B under it;
    the pointer slides to Session. That Session is the bar's opening's, so A's.
    """
    from pptmstr.ui.focus import OnNode

    held = two_running_cards()
    run = _OpenSession(held, monkeypatch)
    run.state.focus.target = OnNode(A)
    io = imgui.get_io()
    run.frame()
    io.add_mouse_pos_event(*run.at["View"])
    run.frame()
    io.add_mouse_button_event(0, True)
    run.frame()
    io.add_mouse_button_event(0, False)
    run.frame()
    assert run.shown == "View" and run.state.menu_latch is not None, "opened on View, latched"
    run.state.focus.target = OnNode(B)
    run.frame()
    run.frame()
    run.hover("Session")
    run.built.clear()
    run.frame()
    assert run.shown == "Session" and set(run.built) == {A}


def test_find_is_refused_where_no_pane_draws_the_focused_cards_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A log pane shown (CONTEXT or SESSION) and a log it draws: offered. Neither
    shown, or a session recalled from its file, which the panes draw as one
    closed line: refused, with why.
    """
    from pptmstr.intents import SessionRecalled
    from pptmstr.model import Recalled
    from pptmstr.ui.focus import OnNode

    hello = _Hello(app.TRIAGE)
    monkeypatch.setattr(app, "hello_imgui", hello)
    held = store()
    held.apply(SessionRecalled("s-closed", "an earlier task", "claude-opus-5", Recalled()), now=5.0)
    state = app.AppState(store=held, bridge=MagicMock(), settings=Settings())
    state.frame_snap = held.snapshot()
    state.focus.target = OnNode(ROOT, pinned=True)
    windows = hello.runner.docking_params.dockable_windows

    assert app._menu_facts(state).find_refused == main_menu.FIND_NO_PANE
    for label in app._LOG_PANES:
        pane = MagicMock()
        pane.label, pane.is_visible = label, False
        pane.include_in_view_menu = pane.can_be_closed = True
        windows.append(pane)
        assert app._menu_facts(state).find_refused == main_menu.FIND_NO_PANE, "hidden"
        pane.is_visible = True
        assert app._menu_facts(state).find_refused is None, label
        pane.is_visible = False

    windows[-1].is_visible = True
    state.focus.target = OnNode(("s-closed", None), pinned=True)
    assert app._menu_facts(state).find_refused == main_menu.FIND_NO_LOG


def test_find_in_an_open_bar_is_refused_while_the_panes_show_another_cards_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Latched to A with a log pane shown: Find is offered while the cursor is on A.
    Once the cursor is on B the panes draw B's log, and a find there is not A's,
    so it is refused with why rather than opened over B.
    """
    from pptmstr.ui.focus import OnNode

    hello = _Hello(app.TRIAGE)
    monkeypatch.setattr(app, "hello_imgui", hello)
    monkeypatch.setattr(app, "_policy_of", lambda state, node: Policy.PERMISSIVE)
    pane = MagicMock()
    pane.label, pane.is_visible = "CONTEXT", True
    pane.include_in_view_menu = pane.can_be_closed = True
    hello.runner.docking_params.dockable_windows.append(pane)
    held = two_running_cards()
    state = app.AppState(store=held, bridge=MagicMock(), settings=Settings())
    state.frame_snap = held.snapshot()
    state.focus.target = OnNode(A, pinned=True)
    state.menu_latch = main_menu.Latch(app._menu_facts(state).card)

    facts = app._menu_facts(state)
    assert facts.card is not None and facts.card.record.node_id == A
    assert facts.find_refused is None, "the panes show A's log"
    state.focus.target = OnNode(B, pinned=True)
    facts = app._menu_facts(state)
    assert facts.card is not None and facts.card.record.node_id == A
    assert facts.find_refused == main_menu.FIND_NOT_SHOWN
    assert item(menu(main_menu.build(facts), main_menu.SESSION), "Find").refused == (
        main_menu.FIND_NOT_SHOWN
    )


# -- a heading with "##" in it ------------------------------------------------------------


def vertices(draw: Callable[[str], None], text: str) -> list[tuple[float, float, int]]:
    """What ``draw(text)`` puts in a fixed window, followed by a line of text."""
    for _ in range(3):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(imgui.ImVec2(300.0, 100.0))
        imgui.begin("##heading", None, imgui.WindowFlags_.no_decoration)
        draw(text)
        imgui.text("below")
        imgui.end()
        imgui.render()
    listed = imgui.get_draw_data().cmd_lists[0]
    return [(v.pos.x, v.pos.y, v.col) for v in listed.vtx_buffer]


@pytest.mark.parametrize("align", [0.0, 0.5, 1.0])
def test_a_heading_drawn_whole_is_drawn_as_separator_text_draws_one(
    context: None, align: float
) -> None:
    """
    The heading drawn for a title with ``##`` in it is ImGui's separator text,
    vertex for vertex, wherever the theme aligns its label -- and the line after
    it starts where it would.
    """
    imgui.get_style().separator_text_align = imgui.ImVec2(align, 0.5)
    text = "fix XX headings"
    assert vertices(main_menu._separator_heading, text) == vertices(imgui.separator_text, text)


def test_a_title_with_hashes_in_it_is_drawn_whole(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Dear ImGui ends a separator's label at "##": "fix ## headings" draws as
    "fix ". The card's title is the operator's text, so it is drawn whole.
    """
    whole = vertices(imgui.separator_text, "fix XX headings")
    assert len(vertices(imgui.separator_text, "fix ## headings")) < len(whole), "ImGui cuts"
    assert len(vertices(main_menu._separator_heading, "fix ## headings")) == len(whole)

    drawn: list[str] = []
    bar = _Bar("")
    monkeypatch.setattr(main_menu, "imgui", bar)
    monkeypatch.setattr(main_menu, "_separator_heading", drawn.append)
    menus = (Menu("Session", (main_menu.Heading("fix ## headings"), main_menu.Heading("Gate"))),)
    main_menu.draw(menus, why=lambda text: None)
    assert drawn == ["fix ## headings"] and bar.headings == ["Gate"]
