"""
The last hop of a launch: what the operator chose at the dial reaches the session.

``ui/launcher`` builds a ``LaunchSpec`` carrying ``containment`` and ``policy``, and
``model``/``driver`` both have somewhere to put them. Between those two facts sits
``app._launch``, which is the only code that turns one into the other -- and a field
it forgets is a field nothing complains about. The session starts; it just starts
uncontained, under the strict gate, while the modal that asked for the mode is gone.

The refusal here is the other half. ``planning/2026-09-03-a-dangerously-autonomous-mode.md``
§8c measured that the CLI accepts an unrecognised settings key *silently*, so a CLI
too old for the sandbox keys does not fail the launch -- it runs it with the keys
ignored. Reading the version and refusing below the floor is the only thing between a
version shortfall and a session the operator believes is contained.
"""

from __future__ import annotations

import functools
import os
import signal
import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest

from pptmstr import cli_version
from pptmstr.approval import Policy
from pptmstr.bridge import Bridge
from pptmstr.cli_version import BelowFloor, FloorCheck, MeetsFloor, Unreadable
from pptmstr.driver import AgentSession
from pptmstr.log import LOG, Level
from pptmstr.model import BEFORE_RESTART, LaunchSpec
from pptmstr.settings import RoleChoice, Settings, legacy_faces
from pptmstr.store import Store
from pptmstr.ui.launcher import FailedLaunch

# Stands in for what `sandbox.containment_settings()` returns. Its content does not
# matter to `_launch` -- it is opaque text that must arrive unchanged -- and a literal
# keeps these tests from depending on the sandbox module's current spelling.
CONTAINMENT = '{"sandbox": {"network": {"strictAllowlist": true}}}'

_MEETS = MeetsFloor("2.1.251", (2, 1, 251), cli_version.SANDBOX_FLOOR)
_BELOW = BelowFloor("2.1.9", (2, 1, 9), cli_version.SANDBOX_FLOOR)
_UNREADABLE = Unreadable("claude --version exited 127", cli_version.SANDBOX_FLOOR)


class _Outcome:
    """
    What one run of ``_launch`` did: what reached the pool, and what was measured.
    """

    def __init__(self) -> None:
        self.sessions: list[AgentSession] = []
        # One entry per call to the version check: the path it was asked about and
        # the thread it ran on. Empty means it was never consulted.
        self.checked_paths: list[str | None] = []
        self.checked_on: list[threading.Thread] = []
        self.pool_thread: threading.Thread | None = None


def _run_launch(
    spec: LaunchSpec,
    monkeypatch: pytest.MonkeyPatch,
    *,
    check: FloorCheck | None = None,
    settings: Settings | None = None,
) -> _Outcome:
    """
    Drive ``_launch`` against a pool that only records, with the version read faked.

    ``check`` is what the stand-in returns. ``None`` means the run is not expected to
    consult it at all, and the stand-in raises if it is called -- which is what makes
    "an uncontained launch does not pay for the subprocess" a testable claim rather
    than an assertion about code nobody ran.

    ``bridge.stop`` is the synchronisation: it drains outstanding loop tasks before
    it tears the loop down, so ``go`` has run to completion by the time this returns
    and an empty ``sessions`` means refused rather than not-yet.
    """
    from pptmstr.app import AppState, _launch

    out = _Outcome()

    def fake_check(path: str | None = None, **kwargs: object) -> FloorCheck:
        out.checked_paths.append(path)
        out.checked_on.append(threading.current_thread())
        if check is None:
            raise AssertionError("the CLI version was read for a launch that asked for no mode")
        return check

    monkeypatch.setattr(cli_version, "check_installed_cli", fake_check)

    class _RecordingPool:
        def submit(self, session: AgentSession) -> bool:
            out.pool_thread = threading.current_thread()
            out.sessions.append(session)
            return True

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=settings or Settings())
        state.pool = _RecordingPool()  # type: ignore[assignment]
        _launch(state, spec)
    finally:
        bridge.stop()
    return out


def _spec(**kw: object) -> LaunchSpec:
    return LaunchSpec(task="do a thing", model="claude-sonnet-5", cwd="/tmp", **kw)  # type: ignore[arg-type]


# -- the mode reaches the session --------------------------------------------------


def test_the_mode_chosen_at_the_dial_reaches_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Both halves, in one assertion each, because they are only useful together.

    ``LauncherState.spec`` sets containment and policy as a pair -- the policy
    releases ``Bash`` from the gate and the containment is the only thing bounding
    what a released ``Bash`` reaches. A ``_launch`` that carried one and dropped the
    other would start either an unattended agent with the whole machine or a sandbox
    around a gate that is still asking, and neither reads as wrong from any surface.
    """
    out = _run_launch(
        _spec(containment=CONTAINMENT, policy=Policy.AUTONOMOUS), monkeypatch, check=_MEETS
    )

    assert [s.containment for s in out.sessions] == [CONTAINMENT]
    assert [s.policy for s in out.sessions] == [Policy.AUTONOMOUS]


def test_a_spec_that_asked_for_nothing_starts_the_session_it_always_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The defaults are the whole of the old behaviour, so they are worth pinning
    separately: a pass-through that inverted a condition would show up here and
    nowhere else.
    """
    out = _run_launch(_spec(), monkeypatch)

    assert [s.containment for s in out.sessions] == [None]
    assert [s.policy for s in out.sessions] == [Policy.STRICT]


# -- the per-launch sub-agent cap --------------------------------------------------


def test_a_launch_that_named_no_cap_gets_the_operators_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The field's default is the whole of the old behaviour: every spec built before it
    existed, and every one ``from_record`` builds today, carries ``None``.
    """
    out = _run_launch(_spec(), monkeypatch, settings=Settings(subagent_cap=6))

    assert [s.subagent_cap for s in out.sessions] == [6]


def test_a_launch_that_named_a_cap_overrides_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The operator asked for an override at launch, and this is the only hop that can
    grant one -- ``AgentSession.subagent_cap`` is set once, at construction.
    """
    out = _run_launch(_spec(subagent_cap=2), monkeypatch, settings=Settings(subagent_cap=6))

    assert [s.subagent_cap for s in out.sessions] == [2]


def test_a_launch_capped_at_zero_may_not_spawn_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The value the obvious implementation eats.

    ``spec.subagent_cap or settings.subagent_cap`` reads ``0`` as "unset" and hands
    back the setting, which turns a session the operator said may not spawn into one
    that may spawn six times -- under a policy where every spawn is auto-approved and
    nobody is asked. It is the one substitution in this file that widens a bound
    rather than narrowing it, and it typechecks.
    """
    out = _run_launch(_spec(subagent_cap=0), monkeypatch, settings=Settings(subagent_cap=6))

    assert [s.subagent_cap for s in out.sessions] == [0]


def test_the_modal_is_drawn_against_the_setting_it_offers_to_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The number on screen is the one in ``Settings``, and nothing else can supply it.

    ``launcher._cap_line`` refuses to substitute ``DEFAULT_SUBAGENT_CAP`` for an
    unknown setting -- deliberately, because a stand-in is wrong for exactly the
    operator who changed it -- so the section renders without a figure and the
    override box does not render at all unless this argument arrives. The unit tests
    on ``draw`` pass the value in themselves and would keep passing with the call site
    handing it nothing, which is STYLE.md §2's watchdog-nothing-calls shape.
    """
    from pptmstr import app
    from pptmstr.ui import launcher, settings_dialog

    seen: list[object] = []
    monkeypatch.setattr(
        launcher, "draw", lambda state, **kw: seen.append(kw.get("subagent_cap", "ABSENT"))
    )
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
    _no_settings_dialog(monkeypatch)
    monkeypatch.setattr(settings_dialog, "handle_shortcut", lambda state, **kw: None)
    monkeypatch.setattr(settings_dialog, "draw", lambda state, settings, apply: None)

    class _IdlePool:
        running_count = 0
        queued_count = 0
        cap = 4

    bridge = Bridge()
    bridge.start()
    try:
        state = app.AppState(store=Store(), bridge=bridge, settings=Settings(subagent_cap=6))
        state.pool = _IdlePool()  # type: ignore[assignment]
        app._draw_overlays(state)
    finally:
        bridge.stop()

    assert seen == [6]


# -- the floor refusal -------------------------------------------------------------


def test_an_uncontained_launch_never_reads_the_cli_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The ordinary launch is the common one and must neither pay for a subprocess nor
    be refusable by one. ``check`` is left None here, so the stand-in raises if the
    guard is ever widened to run unconditionally -- the exception is swallowed by the
    loop task, but the session never reaches the pool and the run reads as refused.
    """
    out = _run_launch(_spec(), monkeypatch)

    assert out.checked_paths == []
    assert len(out.sessions) == 1


def test_a_cli_below_the_floor_refuses_a_contained_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    §8c: the key is accepted silently, so the old CLI runs the session with the
    containment ignored. Starting it anyway is the one outcome this cannot have.
    """
    out = _run_launch(
        _spec(containment=CONTAINMENT, policy=Policy.AUTONOMOUS), monkeypatch, check=_BELOW
    )

    assert out.checked_paths == [None]
    assert out.sessions == []


def test_a_cli_whose_version_cannot_be_read_refuses_a_contained_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``Unreadable`` is a refusal and not a pass. ``cli_version`` keeps "could not
    tell" distinct from "checked and fine" precisely so this call can refuse on it;
    collapsing them here would throw away the module's whole design and reproduce the
    SDK's ``except Exception: pass``, which §8 cites as the behaviour not to copy.
    """
    out = _run_launch(
        _spec(containment=CONTAINMENT, policy=Policy.AUTONOMOUS), monkeypatch, check=_UNREADABLE
    )

    assert out.checked_paths == [None]
    assert out.sessions == []


def test_the_refusal_says_which_of_the_two_failures_it_was() -> None:
    """
    A refusal that read the same for both would send an operator to upgrade a CLI
    that is fine, or to debug a PATH that is not the problem. The pure half is tested
    directly because that is the half that can be.
    """
    from pptmstr.app import _containment_refusal

    assert _containment_refusal(_MEETS) is None

    below = _containment_refusal(_BELOW)
    assert below is not None and "2.1.9" in below and cli_version.SANDBOX_FLOOR in below

    unreadable = _containment_refusal(_UNREADABLE)
    assert unreadable is not None and "exited 127" in unreadable
    assert "2.1.9" not in unreadable


# -- where the blocking read runs --------------------------------------------------


def test_the_version_read_runs_off_both_the_frame_loop_and_the_asyncio_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``check_installed_cli`` spawns ``claude --version`` and waits up to ten seconds.

    On the UI thread that is a visible stall -- I7, and the reason ``ui/launcher``
    scans sessions on a worker. On the asyncio loop it is worse in a quieter way: the
    loop hosts every live session, the gate's parked futures and the watchdogs that
    report on them, so ten seconds there is ten seconds in which no approval can be
    answered and nothing says why.

    The two threads named here are the ones it must not be. ``pool.submit`` runs
    inside the launch coroutine, so ``pool_thread`` *is* the loop thread -- which is
    what lets this compare against it without reaching into ``Bridge``.
    """
    out = _run_launch(
        _spec(containment=CONTAINMENT, policy=Policy.AUTONOMOUS), monkeypatch, check=_MEETS
    )

    assert len(out.checked_on) == 1
    assert out.pool_thread is not None
    assert out.checked_on[0] is not out.pool_thread
    assert out.checked_on[0] is not threading.current_thread()


# -- the typeset seam is told what the operator set --------------------------------


@pytest.fixture(autouse=True)
def _no_tab_paint(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Stand in for ``_draw_overlays``' paint of the pane tabs in every test here: it
    reads ImGui's dock nodes, and no test in this file drives a dock. Its wiring is
    ``test_the_overlays_paint_the_pane_tabs_first``.
    """
    from pptmstr.ui import tabs

    monkeypatch.setattr(tabs, "draw", lambda: None)


def test_the_overlays_paint_the_pane_tabs_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The tabs are painted ahead of every modal, so each modal is a later window and
    covers them.
    """
    from pptmstr import app
    from pptmstr.ui import launcher, tabs

    calls: list[str] = []
    monkeypatch.setattr(tabs, "draw", lambda: calls.append("tabs"))

    def stop(state: object) -> None:
        calls.append("launcher shortcut")
        raise _Reached

    monkeypatch.setattr(launcher, "handle_shortcut", stop)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    with pytest.raises(_Reached):
        app._draw_overlays(state)
    assert calls == ["tabs", "launcher shortcut"]


def _no_settings_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Stand in for the settings dialog's two calls in ``_draw_overlays``, beside a
    test's stand-in launcher: each asks ImGui for its frame, and a test that
    drives the overlays with no ImGui context has none to give.
    """
    from pptmstr.ui import settings_dialog

    monkeypatch.setattr(settings_dialog, "handle_shortcut", lambda state, *, blocked=False: None)
    monkeypatch.setattr(settings_dialog, "draw", lambda state, settings, apply: None)


class _Reached(Exception):
    """
    Raised by a stand-in to stop a function at the call under test.
    """


def _main_up_to_the_window(
    monkeypatch: pytest.MonkeyPatch, found: Settings, argv: list[str]
) -> object:
    """
    ``app.main`` run with ``found`` as what the settings file said, as far as the
    first thing it does towards a window, which is where the settings are in
    force. ``_start`` is stood in for: what it builds is its own tests'
    business, and here only the settings it is handed come back out of it.
    """
    from pptmstr import app
    from pptmstr import settings as settings_mod
    from pptmstr.settings import Loaded

    started: list[object] = []

    def start(args: object, loaded: Loaded) -> object:
        state = types.SimpleNamespace(settings=loaded.settings)
        started.append(state)
        return state

    def window() -> None:
        raise _Reached

    monkeypatch.setattr(settings_mod, "read", lambda: Loaded(found))
    monkeypatch.setattr(app, "_start", start)
    monkeypatch.setattr(app, "hello_imgui", types.SimpleNamespace(RunnerParams=window))
    with pytest.raises(_Reached):
        app.main(argv)
    assert len(started) == 1
    return started[0]


@pytest.fixture
def typeset_state() -> object:
    """
    The seam's module state, put back afterwards: it outlives a test otherwise.
    """
    from pptmstr.ui import typeset

    state = typeset._STATE
    before = (state.typeset, state.scale, state.family, dict(state.roles), state.panes_off)
    yield state
    typeset.configure(
        typeset=before[0],
        scale=before[1],
        proportional=before[2],
        roles=before[3],
        panes={pane: pane not in before[4] for pane in typeset.Pane},
    )


def test_the_persisted_text_settings_reach_the_seam_before_the_first_frame(
    monkeypatch: pytest.MonkeyPatch, typeset_state: object
) -> None:
    """
    ``Settings.typeset`` is the switch between two renderers. Loaded and never handed
    to the seam, the menu would show it off while every pane drew through it.
    """
    from pptmstr.ui import typeset

    typeset.configure(typeset=True, mono=False, scale=1.0, panes={})
    _main_up_to_the_window(
        monkeypatch,
        Settings(typeset=False, typeset_scale=1.25, typeset_family="droid", theme="dark")
        .with_role("prose", slot="monospace")
        .with_role("code-block", step=2)
        .with_pane("context", False),
        [],
    )
    assert (typeset._STATE.typeset, typeset._STATE.family) == (False, "droid")
    assert typeset.mono() and typeset.scale() == 1.25
    assert typeset.chosen(typeset.Role.CODE_BLOCK).step == 2
    assert not typeset.pane_on(typeset.Pane.CONTEXT) and typeset.pane_on(typeset.Pane.BOARD)


def test_a_text_switch_is_saved_and_takes_effect_without_a_restart(
    monkeypatch: pytest.MonkeyPatch, typeset_state: object
) -> None:
    """
    The comparison this setting exists for is made by flipping it and looking. A
    switch that only took effect at the next launch would be comparing two launches.
    """
    from pptmstr import app
    from pptmstr import settings as settings_mod
    from pptmstr.ui import typeset

    saved: list[Settings] = []
    monkeypatch.setattr(
        settings_mod, "save", lambda settings, path=None, *, unread: saved.append(settings)
    )
    typeset.configure(typeset=True, mono=False)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    epoch = typeset._STATE.epoch

    app._switch_typeset(state, typeset_on=False)
    assert state.settings.typeset is False and saved[-1].typeset is False
    assert typeset._STATE.typeset is False
    assert typeset._STATE.epoch == epoch, "turning it off rebuilds nothing"

    app._switch_typeset(state, role=("prose", "monospace", None))
    mono = (RoleChoice("prose", "monospace", None),)
    assert state.settings.typeset_roles == mono and saved[-1].typeset_roles == mono
    assert state.settings.typeset is False, "one switch must not move the other"
    assert typeset.mono() is True
    assert typeset._STATE.epoch == epoch + 1, "documents built in the other faces are stale"

    app._switch_typeset(state, scale=1.25)
    assert state.settings.typeset_scale == 1.25 and saved[-1].typeset_scale == 1.25
    assert state.settings.typeset_roles == mono, "nor the size the faces"
    assert typeset.scale() == 1.25
    assert typeset._STATE.epoch == epoch + 2, "documents built at the other size are stale"

    app._switch_typeset(state, role=("prose", None, 2))
    assert saved[-1].typeset_roles == (RoleChoice("prose", "monospace", 2),)
    assert typeset.chosen(typeset.Role.PROSE) == typeset.RoleStyle(typeset.Slot.MONOSPACE, 2)
    assert typeset._STATE.epoch == epoch + 3

    app._switch_typeset(state, pane=("context", False))
    assert saved[-1].typeset_panes == (("context", False),)
    assert not typeset.pane_on(typeset.Pane.CONTEXT)
    assert typeset._STATE.epoch == epoch + 3, "a pane switched off rebuilds nothing"


class _Menu:
    """
    The ImGui calls ``main_menu.draw`` makes, with one entry that reports a click.

    A disabled entry cannot be clicked, as in ImGui. Every item is hovered, so
    ``tips`` holds what each one says on hover.
    """

    DRAWS = (
        "begin_menu",
        "end_menu",
        "menu_item",
        "is_item_hovered",
        "set_tooltip",
        "separator",
        "separator_text",
        "text_disabled",
        "indent",
        "unindent",
    )

    def __init__(self, click: str) -> None:
        self.click = click
        self.menus: list[str] = []
        self.items: dict[str, tuple[bool, bool]] = {}
        self.tips: dict[str, str] = {}
        self._last = ""

    def begin_menu(self, label: str, enabled: bool = True) -> bool:
        self.menus.append(label)
        return enabled

    def end_menu(self) -> None:
        pass

    def menu_item(
        self, label: str, shortcut: str, selected: bool, enabled: bool = True
    ) -> tuple[bool, bool]:
        self._last = label.split("##")[0]
        self.items[self._last] = (selected, enabled)
        return (enabled and self._last == self.click, selected)

    def is_item_hovered(self, flags: int = 0) -> bool:
        return True

    def set_tooltip(self, text: str) -> None:
        self.tips[self._last] = text

    def separator(self) -> None:
        pass

    def separator_text(self, text: str) -> None:
        pass

    def text_disabled(self, text: str) -> None:
        pass

    def indent(self) -> None:
        pass

    def unindent(self) -> None:
        pass


def _runner() -> types.SimpleNamespace:
    """``hello_imgui`` as far as the menu bar reads it: no panes, the status bar on."""
    from pptmstr import app

    params = types.SimpleNamespace(
        docking_params=types.SimpleNamespace(dockable_windows=[], layout_reset=False),
        imgui_window_params=types.SimpleNamespace(show_status_bar=True),
    )
    return types.SimpleNamespace(
        get_runner_params=lambda: params, current_layout_name=lambda: app.TRIAGE
    )


def _draw_menus(monkeypatch: pytest.MonkeyPatch, state: Any, click: str) -> _Menu:
    """
    ``app._menus`` run once, as the frame runs it, against a stand-in ImGui with
    one entry clicked: the facts read, the bar built and drawn, the choice carried out.
    """
    from pptmstr import app
    from pptmstr.ui import main_menu

    menu = _Menu(click)
    for name in _Menu.DRAWS:
        monkeypatch.setattr(main_menu.imgui, name, getattr(menu, name))
    monkeypatch.setattr(app, "hello_imgui", _runner())
    app._menus(state)
    return menu


def _text_menu(
    monkeypatch: pytest.MonkeyPatch, click: str, settings: Settings | None = None
) -> tuple[_Menu, Any, list[Settings]]:
    """
    ``app._menus`` drawn once against a stand-in ImGui, with one entry clicked.
    """
    from pptmstr import app
    from pptmstr import settings as settings_mod
    from pptmstr.ui import typeset

    saved: list[Settings] = []
    monkeypatch.setattr(
        settings_mod, "save", lambda settings, path=None, *, unread: saved.append(settings)
    )
    monkeypatch.setattr(typeset, "available", lambda: True)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=settings or Settings())
    app._configure_typeset(state.settings)
    menu = _draw_menus(monkeypatch, state, click)
    return menu, state, saved


def test_the_text_menu_keeps_its_two_switches_and_opens_the_dialog_for_the_rest(
    monkeypatch: pytest.MonkeyPatch, typeset_state: object
) -> None:
    """
    The family, the roles, the panes and the sizes are the dialog's. A menu that
    still offered them would be a second place to change one setting; a menu with
    no way into the dialog would leave the chord as the only door, and a chord
    with no menu entry is one nobody who did not read the README presses.
    """
    from pptmstr.ui import settings_dialog

    menu, state, saved = _text_menu(monkeypatch, "Settings…")

    assert menu.menus == ["Session", "View", "Text", "Theme"]
    text = list(menu.items)
    assert text[text.index("Wrap composers") :][:3] == [
        "Wrap composers",
        "Typeset text",
        "Settings…",
    ]
    assert state.settings_dialog._open_requested, "the entry asks for the modal"
    assert not state.settings_dialog.is_open, "which is opened by the frame that draws it"
    assert saved == [] and state.settings == Settings(), "and changes nothing by itself"
    assert settings_dialog.CHORD_LABEL == "Ctrl+,"


@pytest.mark.parametrize(
    ("click", "expected"),
    [
        ("Wrap composers", Settings(wrap_inputs=False)),
        ("Typeset text", Settings(typeset=False)),
        ("Light", Settings(theme="light")),
    ],
)
def test_what_was_one_click_in_a_menu_still_is(
    monkeypatch: pytest.MonkeyPatch, typeset_state: object, click: str, expected: Settings
) -> None:
    from pptmstr import theme

    menu, state, saved = _text_menu(monkeypatch, click)
    try:
        assert state.settings == expected and saved == [expected]
        assert not state.settings_dialog._open_requested
    finally:
        theme.set_theme("dark")


@pytest.mark.parametrize(
    ("available", "obtainable", "reason", "offered"),
    [
        (True, True, None, True),
        (False, True, "faces load when this is switched on: about a second", True),
        (False, False, "the text library did not import (ImportError: gone)", False),
    ],
)
def test_the_typeset_entry_is_offered_only_when_it_can_work_and_says_why_when_it_cannot(
    monkeypatch: pytest.MonkeyPatch,
    typeset_state: object,
    available: bool,
    obtainable: bool,
    reason: str | None,
    offered: bool,
) -> None:
    """
    With the library absent the application still starts, and the one place the
    operator would look for typeset text says why it is not there. With the faces
    put off until asked for, the entry can be clicked and says what the click costs.
    """
    from pptmstr import app
    from pptmstr import settings as settings_mod
    from pptmstr.ui import typeset

    saved: list[Settings] = []
    monkeypatch.setattr(
        settings_mod, "save", lambda settings, path=None, *, unread: saved.append(settings)
    )
    monkeypatch.setattr(typeset, "available", lambda: available)
    monkeypatch.setattr(typeset, "obtainable", lambda: obtainable)
    monkeypatch.setattr(typeset, "unavailable_reason", lambda: reason)
    monkeypatch.setattr(typeset, "diff_renderer", lambda: None)
    monkeypatch.setattr(typeset, "configure", lambda **kwargs: None)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings(typeset=False))
    menu = _draw_menus(monkeypatch, state, "Typeset text")
    assert menu.items["Typeset text"] == (False, offered)
    assert state.settings.typeset is offered, "a click on an entry that is offered is taken"
    assert menu.tips.get("Typeset text") == reason


def test_switched_off_the_text_menu_says_so_and_not_that_nothing_is_installed(
    monkeypatch: pytest.MonkeyPatch, typeset_state: object
) -> None:
    """
    Under ``IMSCRIBE_NO_TYPESET`` imscribe answers "not installed
    (IMTYPESET_LIBRARY is not set)" with a library installed. The menu's
    sentence is the seam's, unstubbed, from the library's answer as it gives
    it there: switched off.
    """
    from imscribe import typesetter

    from pptmstr import app, pairing
    from pptmstr.ui import typeset
    from pptmstr.ui.typeset import gate

    monkeypatch.setenv(pairing.DISABLE_VARIABLE, "1")
    monkeypatch.setattr(typesetter, "kind", lambda: typesetter.Kind.NOT_INSTALLED)
    monkeypatch.setattr(typesetter, "reason", lambda: "IMTYPESET_LIBRARY is not set")
    monkeypatch.setattr(typesetter, "module", lambda: None)
    for name, value in (("asked", True), ("loaded", False), ("accepted", False)):
        monkeypatch.setattr(gate._GATE, name, value)
    monkeypatch.setattr(gate._GATE, "failure", None)
    monkeypatch.setattr(typeset, "configure", lambda **kwargs: None)
    menu = _draw_menus(
        monkeypatch, app.AppState(store=Store(), bridge=Bridge(), settings=Settings()), ""
    )
    assert menu.items["Typeset text"] == (False, False)
    assert menu.tips["Typeset text"] == "typeset text is switched off (IMSCRIBE_NO_TYPESET=1)"


def test_the_overlays_give_the_dialog_the_settings_in_force_and_the_one_way_to_change_them(
    monkeypatch: pytest.MonkeyPatch, typeset_state: object
) -> None:
    """
    The dialog draws what it is handed and changes nothing itself. Unwired -- the
    draw never called, or called with a callback that drops the change -- every
    test of its model still passes and the window has no settings dialog.
    """
    from pptmstr import app
    from pptmstr import settings as settings_mod
    from pptmstr.ui import launcher, settings_dialog, typeset

    drawn: list[tuple[object, Settings, Any]] = []
    asked: list[tuple[object, bool]] = []
    saved: list[Settings] = []
    monkeypatch.setattr(
        settings_mod, "save", lambda settings, path=None, *, unread: saved.append(settings)
    )
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
    _no_settings_dialog(monkeypatch)
    monkeypatch.setattr(
        settings_dialog,
        "handle_shortcut",
        lambda state, *, blocked=False: asked.append((state, blocked)),
    )
    monkeypatch.setattr(
        settings_dialog,
        "draw",
        lambda state, settings, apply: drawn.append((state, settings, apply)),
    )
    held = Settings(theme="light")
    state = app.AppState(store=Store(), bridge=Bridge(), settings=held)
    app._configure_typeset(held)

    # No pool: the dialog needs none, and is drawn all the same.
    app._draw_overlays(state)
    assert asked == [(state.settings_dialog, False)]
    assert [(each[0], each[1]) for each in drawn] == [(state.settings_dialog, held)]

    drawn[0][2](settings_dialog.RoleSet("prose", slot="monospace"))
    assert state.settings == held.with_role("prose", slot="monospace")
    assert saved == [state.settings]
    assert typeset.mono(), "and the seam was told"

    # With the launcher up the chord is not offered the keyboard.
    state.launcher.is_open = True
    app._draw_overlays(state)
    assert asked[-1] == (state.settings_dialog, True)


def test_the_settings_dialog_is_one_of_the_modals_nothing_opens_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Dear ImGui holds one popup per level at the root, so opening a second closes
    the first. The dialog is one of the modals ``_form_up`` names -- a resume's
    refusal waits behind it as it waits behind New Task -- and its chord is not
    offered over a card's Details or the "Not done" notice, as it is not over the
    launcher. The dialog being up is no block on the chord: that closes it.
    """
    from pptmstr import app
    from pptmstr.ui import launcher, settings_dialog

    asked: list[bool] = []
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
    monkeypatch.setattr(
        settings_dialog, "handle_shortcut", lambda state, *, blocked=False: asked.append(blocked)
    )
    monkeypatch.setattr(settings_dialog, "draw", lambda state, settings, apply: None)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())

    assert not app._form_up(state)
    state.settings_dialog.is_open = True
    assert app._form_up(state)
    app._draw_overlays(state)
    state.settings_dialog.is_open = False

    state.cards.details.node = ("s-1", None)
    app._draw_overlays(state)
    state.cards.details.node = None

    state.resume.is_open, state.resume.showing = True, "s-1"
    app._draw_overlays(state)
    state.resume.is_open, state.resume.showing = False, None

    app._draw_overlays(state)
    assert asked == [False, True, True, False]


def test_a_setting_changed_from_the_menu_is_not_saved_over_a_file_that_could_not_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The menu, the dialog and the fleet's switch all end in ``_apply_settings``,
    and that ends in ``_save``: so the reason a read left behind reaches every one
    of them, and a fail-safe ``board_log`` is never written back from any.
    """
    from pptmstr import app
    from pptmstr import settings as settings_mod

    target = tmp_path / "settings.json"
    target.write_text('{"board_log": "false"}', encoding="utf-8")
    monkeypatch.setattr(settings_mod, "settings_path", lambda: target)
    found = settings_mod.read(target)
    assert found.board_log_unread is settings_mod.BoardLogUnread.VALUE
    state = app.AppState(
        store=Store(),
        bridge=Bridge(),
        settings=found.settings,
        board_log_unread=found.board_log_unread,
    )

    app._switch_hide_ended(state, False)
    app._switch_wrap(state, False)

    assert not state.settings.hide_ended_subagents and not state.settings.wrap_inputs
    assert state.unsaved is not None and state.unsaved.why is settings_mod.BoardLogUnread.VALUE
    assert target.read_text(encoding="utf-8") == '{"board_log": "false"}'


def test_a_setting_that_changes_is_carried_out_where_it_takes_effect(
    monkeypatch: pytest.MonkeyPatch, typeset_state: object
) -> None:
    """
    Saved is not applied. Each setting with something to tell -- the palette, the
    pool, the runner's idle rate, the context log -- is told by the one function
    every change goes through, in the frame the change is made.
    """
    from pptmstr import app, theme
    from pptmstr import settings as settings_mod
    from pptmstr.ui import transcript_pane

    saved: list[Settings] = []
    monkeypatch.setattr(
        settings_mod, "save", lambda settings, path=None, *, unread: saved.append(settings)
    )

    class _Pool:
        cap = 4
        resized_on: threading.Thread | None = None

        def set_cap(self, cap: int) -> None:
            self.cap = cap
            self.resized_on = threading.current_thread()

    bridge = Bridge()
    bridge.start()
    try:
        state = app.AppState(store=Store(), bridge=bridge, settings=Settings())
        pool = _Pool()
        state.pool = pool  # type: ignore[assignment]
        state.runner = types.SimpleNamespace(  # type: ignore[assignment]
            fps_idling=types.SimpleNamespace(fps_idle=9.0),
            imgui_window_params=types.SimpleNamespace(background_color=None),
        )
        state.theme_dirty = False
        state.transcripts.mode = transcript_pane.RenderMode.RAW

        app._apply_settings(state, state.settings.merged(theme="light"))
        assert theme.current() is theme.THEMES["light"] and state.theme_dirty
        assert state.transcripts.mode is transcript_pane.RenderMode.RAW

        app._apply_settings(state, state.settings.merged(fps_idle=15.0))
        assert state.runner is not None and state.runner.fps_idling.fps_idle == 15.0

        app._apply_settings(state, state.settings.merged(concurrency_cap=6))
        # A round trip through the loop: the resize is queued behind nothing else.
        bridge.submit(_nothing()).result(timeout=5)
        assert pool.cap == 6
        assert pool.resized_on is not None and pool.resized_on is not threading.current_thread()

        assert len(saved) == 3
        assert saved[-1] == Settings(theme="light", fps_idle=15.0, concurrency_cap=6)

        # Nothing moved: nothing written, nothing told.
        state.theme_dirty = False
        app._apply_settings(state, state.settings.merged(theme="light"))
        assert len(saved) == 3 and not state.theme_dirty
    finally:
        bridge.stop()
        theme.set_theme("dark")


async def _nothing() -> None:
    return None


def test_escape_over_the_settings_dialog_does_not_also_leave_the_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The dialog closes on Escape, and FOCUS returns to TRIAGE on Escape. With its
    focus on a combo the modal has not captured the keyboard, so the layout keys
    have to be told it is up, as they are told about the launcher.
    """
    from imgui_bundle import imgui

    from pptmstr import app

    switched: list[str] = []
    monkeypatch.setattr(
        app,
        "hello_imgui",
        types.SimpleNamespace(current_layout_name=lambda: app.FOCUS, switch_layout=switched.append),
    )

    def escape(*, dialog_open: bool) -> list[str]:
        del switched[:]
        context = imgui.create_context()
        try:
            io = imgui.get_io()
            io.set_ini_filename("")
            io.display_size = imgui.ImVec2(800, 600)
            io.delta_time = 1 / 60
            io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
            io.add_key_event(imgui.Key.escape, True)
            imgui.new_frame()
            try:
                assert imgui.is_key_pressed(imgui.Key.escape) and not io.want_capture_keyboard
                state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
                state.frame_snap = state.store.snapshot()
                state.settings_dialog.is_open = dialog_open
                app._handle_layout_keys(state)
            finally:
                imgui.end_frame()
        finally:
            imgui.destroy_context(context)
        return list(switched)

    assert escape(dialog_open=False) == [app.TRIAGE]
    assert escape(dialog_open=True) == []


def _escape_over_the_find_row(
    monkeypatch: pytest.MonkeyPatch, *, row_open: bool, editing: bool, held: int = 0
) -> tuple[list[str], tuple[list[str], str | None, dict[str, str]]]:
    """
    Escape pressed over the SESSION log, in the order the runner keeps: the pane
    draws in one frame, and the frame's keys are read before the next frame starts
    (``pre_new_frame``, ``app._handle_keys``), by when a row the Escape closed is
    already closed. ``row_open`` opens the find row first, its field given up as a
    click in the log gives it up; ``editing`` has the operator part way through
    editing a parked call's arguments; ``held`` keeps the key down that many
    frames past the press, long enough for ImGui to repeat it. A second press,
    after the release, follows. Returns every layout switched to, and the layouts
    switched to, the edit open and the edits kept as the first press was released.
    """
    from imgui_bundle import imgui

    from pptmstr import app
    from pptmstr.intents import AgentSpawned, ApprovalRequested
    from pptmstr.model import PendingApproval
    from pptmstr.transcript import SegmentKind, Transcript
    from pptmstr.ui import transcript_pane, typeset
    from pptmstr.ui.focus import OnObligation

    monkeypatch.setattr(typeset, "enabled", lambda pane=None: False)
    switched: list[str] = []
    monkeypatch.setattr(
        app,
        "hello_imgui",
        types.SimpleNamespace(current_layout_name=lambda: app.FOCUS, switch_layout=switched.append),
    )
    node = ("s1", None)
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "".join(f"line {n} checksum\n" for n in range(20)))
    store = Store()
    store.apply(
        AgentSpawned(
            node_id=node,
            parent=None,
            task="t",
            model="m",
            started_at=0.0,
            cwd="/x",
            transcript=transcript,
        )
    )
    state = app.AppState(store=store, bridge=Bridge(), settings=Settings())
    if editing:
        parked = PendingApproval(
            id="p1",
            node=node,
            tool_name="Write",
            tool_use_id="tu-p1",
            raw_args={"path": "a"},
            summary="Write a",
            requested_at=1.0,
        )
        store.apply(ApprovalRequested(node, parked), now=1.0)
        state.focus.target = OnObligation(f"approval:{parked.id}")
        state.review.editing = parked.id
        state.review.edits[parked.id] = '{"path": "edited"}'
    state.frame_snap = store.snapshot()
    bar = state.transcripts.find

    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.set_ini_filename("")
        io.display_size = imgui.ImVec2(800, 600)
        io.delta_time = 1 / 60
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.fonts.add_font_default()

        repeats = 0

        def frame() -> None:
            nonlocal repeats
            imgui.new_frame()
            imgui.set_next_window_focus()
            imgui.begin("##pane")
            transcript_pane.draw(state.frame_snap, state.transcripts, node)
            imgui.end()
            imgui.render()
            repeats += bool(imgui.is_key_pressed(imgui.Key.escape))
            app._handle_keys(state)

        for _ in range(2):
            frame()
        if row_open:
            bar.open, bar.needle = True, "checksum"
            frame()
            assert bar.open and not bar.active and not io.want_capture_keyboard
        io.add_key_event(imgui.Key.escape, True)
        frame()
        assert not bar.open
        repeats = 0
        for _ in range(held):
            frame()
        # The hold is long enough that the key repeated: the case is reached.
        assert bool(repeats) == bool(held)
        io.add_key_event(imgui.Key.escape, False)
        frame()
        first = (list(switched), state.review.editing, dict(state.review.edits))
        io.add_key_event(imgui.Key.escape, True)
        frame()
        io.add_key_event(imgui.Key.escape, False)
        frame()
    finally:
        imgui.destroy_context(context)
    return switched, first


@pytest.mark.parametrize("row_open", [False, True], ids=["no find row", "find row open"])
def test_the_escape_that_closes_the_find_row_does_not_also_leave_focus(
    monkeypatch: pytest.MonkeyPatch, row_open: bool
) -> None:
    """
    The Escape that closes the row is the row's, and FOCUS stays; the next one,
    with no row to close, returns to TRIAGE. With no row open the first Escape
    returns at once, which is what shows the probe can see a switch at all.
    """
    from pptmstr import app

    switched, first = _escape_over_the_find_row(monkeypatch, row_open=row_open, editing=False)
    assert first[0] == ([] if row_open else [app.TRIAGE])
    assert switched[:1] == [app.TRIAGE]


@pytest.mark.parametrize("row_open", [False, True], ids=["no find row", "find row open"])
def test_the_escape_that_closes_the_find_row_keeps_the_argument_edit(
    monkeypatch: pytest.MonkeyPatch, row_open: bool
) -> None:
    """
    An argument edit is cancelled by Escape, and the operator's edited text with
    it. The Escape that closes the find row is aimed at the row: the edit stays
    open with what was typed in it. With no row open the same Escape cancels the
    edit, which shows the probe reaches ``review.handle_keys`` at all.
    """
    _, (_, editing, edits) = _escape_over_the_find_row(monkeypatch, row_open=row_open, editing=True)
    if row_open:
        assert editing == "p1"
        assert edits == {"p1": '{"path": "edited"}'}
    else:
        assert editing is None and edits == {}


def test_a_held_escape_that_closed_the_find_row_does_not_leave_focus_on_its_repeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The pane reads Escape without repeat and the layout keys with it, so a press
    the row took that is held past ``key_repeat_delay`` would return to TRIAGE on
    a repeat frame. The press is the row's until it is released; the press after
    the release is not, and returns.
    """
    from pptmstr import app

    switched, first = _escape_over_the_find_row(monkeypatch, row_open=True, editing=False, held=30)
    assert first[0] == []
    assert switched == [app.TRIAGE]


def test_a_chord_over_a_typeset_view_does_not_change_the_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Ctrl+Tab is ImGui's window switcher and Ctrl+Enter sends a prompt. A typeset view
    submits no item, so nothing has captured the keyboard while the mouse is over
    one, and the capture guard alone lets both through to Tab and Enter here. Run in
    a real frame: what ImGui says was pressed, and held with it, is the thing tested.
    """
    from imgui_bundle import imgui

    from pptmstr import app

    switched: list[str] = []
    layout = types.SimpleNamespace(
        current_layout_name=lambda: app.TRIAGE if not switched else switched[-1],
        switch_layout=switched.append,
    )
    monkeypatch.setattr(app, "hello_imgui", layout)

    def press(key: imgui.Key, *mods: imgui.Key) -> list[str]:
        del switched[:]
        context = imgui.create_context()
        try:
            io = imgui.get_io()
            io.set_ini_filename("")
            io.display_size = imgui.ImVec2(800, 600)
            io.delta_time = 1 / 60
            io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
            for mod in mods:
                io.add_key_event(mod, True)
            io.add_key_event(key, True)
            imgui.new_frame()
            try:
                assert imgui.is_key_pressed(key) and not io.want_capture_keyboard
                state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
                state.frame_snap = state.store.snapshot()
                app._handle_layout_keys(state)
            finally:
                imgui.end_frame()
        finally:
            imgui.destroy_context(context)
        return list(switched)

    assert press(imgui.Key.enter) == [app.FOCUS]
    for mod in (imgui.Key.mod_ctrl, imgui.Key.mod_alt, imgui.Key.mod_super):
        assert press(imgui.Key.enter, mod) == [], mod
        assert press(imgui.Key.tab, mod) == [], mod
    # Shift is not a chord here: Shift+Enter is still Enter.
    assert press(imgui.Key.enter, imgui.Key.mod_shift) == [app.FOCUS]


def test_the_frame_loop_gives_the_seam_its_turn_between_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``typeset.pre_frame`` loads a face a draw asked for and reads the UI font's size,
    and both have to happen before ``new_frame``. Nothing else calls it: unwired, the
    text never follows a display scale and every unit test of the seam passes.
    """
    from pptmstr import app
    from pptmstr.ui import typeset

    def reached() -> None:
        raise _Reached

    monkeypatch.setattr(typeset, "pre_frame", reached)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    state.theme_dirty = False
    with pytest.raises(_Reached):
        app.begin_frame(state)


def test_the_frame_loop_keeps_the_baked_sizes_once_the_seam_has_had_its_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``theme.keep_baked_sizes`` is worth nothing uncalled: ImGui then discards a
    size nobody drew, moves the others, and a heading's letters come out at
    another size, with every test of the guard itself passing. After the
    seam's turn, because that is where a family is loaded again and the epoch
    moves, and it is the epoch as it then stands that says the old sizes go.
    """
    from pptmstr import app, theme
    from pptmstr.ui import typeset

    calls: list[object] = []

    def seam() -> None:
        typeset.roles.moved()
        calls.append("seam")

    def keep(generation: object = None) -> int:
        calls.append(generation)
        raise _Reached

    monkeypatch.setattr(typeset, "pre_frame", seam)
    monkeypatch.setattr(theme, "keep_baked_sizes", keep)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    state.theme_dirty = False
    logged: list[str] = []
    monkeypatch.setattr(app.LOG, "error", lambda where, said: logged.append(said))
    monkeypatch.setattr(app.LOG, "debug", lambda where, said: None)
    # The keys read ImGui's input, and there is no ImGui context here.
    monkeypatch.setattr(app, "_handle_keys", lambda state: None)
    app._PANEL_ERRORS.pop("baked sizes", None)
    try:
        app.begin_frame(state)
        app.begin_frame(state)
    finally:
        app._PANEL_ERRORS.pop("baked sizes", None)
    assert calls == ["seam", typeset.gate.epoch() - 1, "seam", typeset.gate.epoch()]
    # A guard that raises costs its own work and a line in the log, once: the
    # intents behind it are applied and the frame has its snapshot.
    assert logged == ["baked sizes: _Reached: "]
    assert state.frame == 2 and state.frame_snap is not None


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--typeset", "on"], (True, "mono", 1.0)),
        (["--typeset", "on", "--prose", "mono"], (True, "mono", 1.0)),
        (["--prose", "proportional"], (False, "roboto", 1.0)),
        (["--faces", "roboto-large"], (False, "roboto-large", 1.0)),
        (["--prose", "mono", "--faces", "droid"], (False, "droid", 1.0)),
        (["--text-scale", "1.25"], (False, "mono", 1.25)),
        ([], (False, "mono", 1.0)),
    ],
)
def test_the_renderer_can_be_chosen_for_one_run_from_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
    typeset_state: object,
    argv: list[str],
    expected: tuple[bool, str, float],
) -> None:
    """
    A screenshot run has a throwaway HOME, so the persisted setting is never the
    operator's. The on/off pictures the experiment is judged by are taken with
    these flags, over a saved setting of off and monospace.
    """
    from pptmstr import settings as settings_mod
    from pptmstr.ui import typeset

    saved: list[Settings] = []
    monkeypatch.setattr(
        settings_mod, "save", lambda settings, path=None, *, unread: saved.append(settings)
    )
    _main_up_to_the_window(
        monkeypatch, Settings(typeset=False).merged(**legacy_faces("mono")), argv
    )
    on, faces, scale = expected
    family, roles = typeset.legacy(faces)
    assert (typeset._STATE.typeset, typeset._STATE.scale) == (on, scale)
    assert (typeset._STATE.family, typeset._STATE.roles) == (family, typeset.checked(roles))
    assert saved == [], "an override for one run must not become the saved setting"


# -- a launch reports back ---------------------------------------------------------
#
# The decision is made on the asyncio thread after the modal has closed. These drive
# the real crossing -- a started ``Bridge``, the coroutine ``_launch`` submits, and the
# reap the frame runs -- because the claim is that the answer *arrives*, and a refusal
# computed correctly on a thread nothing reads from is a refusal nobody is told.


def _failed_tasks(failures: list[FailedLaunch]) -> list[str | None]:
    """
    The task text each failed launch was pressed from, None for one with no draft.
    """
    return [None if f.draft is None else f.draft.task for f in failures]


def _app_log_since(mark: int) -> list[tuple[Level, str]]:
    """
    What ``app`` has logged since ``mark``, which is ``LOG.snapshot()[1]`` taken
    earlier: the revision counts appends, and the ring is far longer than a test.
    """
    entries, revision = LOG.snapshot()
    recent = entries[len(entries) - (revision - mark) :] if revision > mark else []
    return [(e.level, e.text) for e in recent if e.source == "app"]


class _Launching:
    """
    An ``AppState`` on a live bridge with a pool that only records, for the length of
    a ``with``. Unlike ``_run_launch`` the bridge stays up after the launch, since what
    is under test happens on the draw thread afterwards.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, check: FloorCheck = _MEETS) -> None:
        from pptmstr import app

        self.app = app
        self.sessions: list[AgentSession] = []
        # Set to let a version read through. Starts set; a test clears it to hold a
        # launch in flight.
        self.release = threading.Event()
        self.release.set()

        def fake_check(path: str | None = None, **kwargs: object) -> FloorCheck:
            assert self.release.wait(timeout=5.0)
            return check

        monkeypatch.setattr(cli_version, "check_installed_cli", fake_check)
        outer = self

        class _RecordingPool:
            # What ``_draw_overlays`` reads off the pool to draw the modal against.
            running_count = 0
            queued_count = 0
            cap = 4

            def submit(self, session: AgentSession) -> bool:
                outer.sessions.append(session)
                return True

        self.bridge = Bridge()
        self.state = app.AppState(store=Store(), bridge=self.bridge, settings=Settings())
        self.state.pool = _RecordingPool()  # type: ignore[assignment]

    def __enter__(self) -> _Launching:
        self.bridge.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release.set()
        self.bridge.stop()

    def launch(self, task: str, *, contained: bool) -> None:
        """
        What the modal's press does: a spec, and the draft it was built from.
        """
        self.state.launcher.task = task
        draft = self.state.launcher.draft()
        self.state.launcher.launched()
        extra: dict[str, object] = (
            {"containment": CONTAINMENT, "policy": Policy.AUTONOMOUS} if contained else {}
        )
        spec = LaunchSpec(task=task, model="claude-sonnet-5", cwd="/tmp", **extra)  # type: ignore[arg-type]
        self.app._launch(self.state, spec, draft)

    def settle(self) -> None:
        """
        Wait for every launch in flight to be decided, without reading any of them.
        """
        for entry in self.state.launches:
            entry.future.exception(timeout=5.0)


def test_a_refusal_decided_on_the_loop_reaches_the_launcher_through_the_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The wiring. ``_draw_overlays`` is what the runner calls each frame, and it is
    driven here rather than ``_reap_launches``: a reap nothing calls leaves every test
    of the reap passing and the refusal exactly where it was, in the log.
    """
    from pptmstr.ui import launcher

    drawn: list[tuple[str, int, bool]] = []
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
    _no_settings_dialog(monkeypatch)
    monkeypatch.setattr(
        launcher,
        "draw",
        lambda state, **kw: drawn.append((state.task, len(state.failures), state._open_requested)),
    )

    with _Launching(monkeypatch, check=_BELOW) as run:
        run.launch("contain this\nfor these reasons", contained=True)
        assert run.state.launcher.task == ""
        run.settle()
        run.app._draw_overlays(run.state)

    assert run.sessions == []
    # By the time the launcher drew, on that same frame, it had the draft and the
    # reason and had been asked to open.
    assert drawn == [("contain this\nfor these reasons", 1, True)]
    failure = run.state.launcher.failures[0]
    assert "2.1.9" in failure.reason and cli_version.SANDBOX_FLOOR in failure.reason
    assert run.state.launches == []


def test_a_launch_that_started_reports_nothing_to_the_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _Launching(monkeypatch) as run:
        run.launch("do a thing", contained=False)
        run.settle()
        run.app._reap_launches(run.state)

    assert len(run.sessions) == 1
    assert run.state.launcher.failures == []
    assert run.state.launcher.task == ""
    assert not run.state.launcher._open_requested
    assert run.state.launches == []


def test_a_launch_still_being_decided_is_neither_reported_nor_forgotten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The frame runs many times between the press and the answer. A reap that dropped
    what it could not read yet would lose the failure; one that reported it would
    hand back the draft of a launch that may still start.
    """
    with _Launching(monkeypatch, check=_BELOW) as run:
        run.release.clear()
        run.launch("contain this\nfor these reasons", contained=True)

        run.app._reap_launches(run.state)
        assert len(run.state.launches) == 1
        assert run.state.launcher.failures == []

        run.release.set()
        run.settle()
        run.app._reap_launches(run.state)

    assert run.state.launches == []
    assert _failed_tasks(run.state.launcher.failures) == ["contain this\nfor these reasons"]


def test_two_launches_in_flight_report_to_their_own_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    One refused and one started, pressed in that order and decided in the other: the
    contained one waits on its version read while the plain one goes straight through.
    The failure must carry the refused launch's draft and not the later one's.
    """
    with _Launching(monkeypatch, check=_BELOW) as run:
        run.release.clear()
        run.launch("the contained one\nwith its reasons", contained=True)
        run.launch("the plain one", contained=False)
        run.state.launches[1].future.exception(timeout=5.0)
        run.app._reap_launches(run.state)
        assert [s.task for s in run.sessions] == ["the plain one"]
        assert run.state.launcher.failures == []

        run.release.set()
        run.settle()
        run.app._reap_launches(run.state)

    assert _failed_tasks(run.state.launcher.failures) == ["the contained one\nwith its reasons"]
    assert run.state.launcher.task == "the contained one\nwith its reasons"
    assert [s.task for s in run.sessions] == ["the plain one"]


def test_an_exception_on_the_launch_path_is_a_failure_with_the_draft_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Not a refusal anybody wrote, and the same loss if it is dropped: no session, a
    closed modal and an empty task box.
    """
    from pptmstr import app

    def explode(*args: object, **kwargs: object) -> AgentSession:
        raise OSError("worktree path is not writable")

    with _Launching(monkeypatch) as run:
        monkeypatch.setattr(app, "AgentSession", explode)
        run.launch("do a thing", contained=False)
        run.settle()
        run.app._reap_launches(run.state)

    assert run.sessions == []
    [failure] = run.state.launcher.failures
    assert failure.reason == "OSError: worktree path is not writable"
    assert run.state.launcher.task == "do a thing"


def test_a_programming_error_on_the_launch_path_leaves_a_traceback_in_the_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A keyword ``AgentSession`` does not take is a bug in ``_launch``, and its message
    alone does not say where. The log ring is where ``guarded`` puts a panel's
    traceback, at DEBUG, and the launch path's goes to the same place; what the
    operator is shown beside the draft stays the one line.
    """
    from pptmstr import app

    def a_constructor_with_a_renamed_keyword(*args: object, **kwargs: object) -> AgentSession:
        raise TypeError("AgentSession.__init__() got an unexpected keyword argument 'brief'")

    mark = LOG.snapshot()[1]
    with _Launching(monkeypatch) as run:
        monkeypatch.setattr(app, "AgentSession", a_constructor_with_a_renamed_keyword)
        run.launch("do a thing", contained=False)
        run.settle()
        run.app._reap_launches(run.state)

    logged = _app_log_since(mark)
    tracebacks = [text for level, text in logged if level is Level.DEBUG]
    assert len(tracebacks) == 1
    assert tracebacks[0].startswith("Traceback (most recent call last):")
    assert "a_constructor_with_a_renamed_keyword" in tracebacks[0]
    # The frame's name is in the traceback and nowhere the operator is asked to read.
    errors = [text for level, text in logged if level is Level.ERROR]
    assert len(errors) == 1
    assert "a_constructor_with_a_renamed_keyword" not in errors[0] and "\n" not in errors[0]
    [failure] = run.state.launcher.failures
    assert failure.reason == (
        "TypeError: AgentSession.__init__() got an unexpected keyword argument 'brief'"
    )


def test_a_refused_relaunch_is_reported_under_its_own_name_and_over_nobodys_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A relaunch, a fork, the command line: no modal was pressed, so there is no draft
    to hand back and the operator may be halfway through typing something unrelated.
    The failure still has to arrive, named for what it was, and leave that text alone.
    """
    with _Launching(monkeypatch, check=_BELOW) as run:
        run.state.launcher.task = "something unrelated, half typed"
        run.app._launch(
            run.state,
            _spec(containment=CONTAINMENT, policy=Policy.AUTONOMOUS),
            origin="relaunch",
        )
        run.settle()
        run.app._reap_launches(run.state)

    assert run.sessions == []
    [failure] = run.state.launcher.failures
    assert failure.draft is None and failure.origin == "relaunch"
    assert "2.1.9" in failure.reason
    assert run.state.launcher.task == "something unrelated, half typed"
    assert run.state.launcher._open_requested


def test_a_launch_appended_while_the_frame_is_reading_is_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The command line's tasks append to the in-flight list from the asyncio thread
    while the draw thread reads it. The interleaving is constructed rather than
    raced: a launch is added from inside the reap, at the point a report is made,
    which is after the reap has taken its view of the list.
    """
    with _Launching(monkeypatch, check=_BELOW) as run:
        run.launch("the first\nwith its reasons", contained=True)
        run.settle()

        report = run.state.launcher.report_failure
        added: list[object] = []

        def report_and_launch(*args: object, **kwargs: object) -> None:
            report(*args, **kwargs)  # type: ignore[arg-type]
            if not added:
                run.app._launch(run.state, _spec(), origin="command-line task")
                added.extend(run.state.launches)

        monkeypatch.setattr(run.state.launcher, "report_failure", report_and_launch)
        run.app._reap_launches(run.state)

        # Still in the list, so the next frame reads it: a reap that rebuilt the list
        # from its own view would have left it out.
        assert len(added) == 1
        assert run.state.launches == added

        run.settle()
        run.app._reap_launches(run.state)
        assert run.state.launches == []
        assert [s.task for s in run.sessions] == ["do a thing"]


def test_a_modal_launch_with_no_pool_is_read_by_a_frame_that_draws_no_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``_draw_overlays`` returns ahead of ``launcher.draw`` when there is no pool, so
    nothing here reaches the screen and this does not claim it does. What it pins is
    that the frame still reads the launch: the failure is in the launcher's list with
    its draft handed back, where the first frame that has a pool draws it, and the
    log says why in the meantime.
    """
    from pptmstr.ui import launcher

    drawn: list[object] = []
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
    _no_settings_dialog(monkeypatch)
    monkeypatch.setattr(launcher, "draw", lambda state, **kw: drawn.append(state))

    mark = LOG.snapshot()[1]
    with _Launching(monkeypatch) as run:
        run.state.pool = None
        run.launch("do a thing", contained=False)
        run.app._draw_overlays(run.state)

    assert drawn == []
    assert run.state.launches == []
    [failure] = run.state.launcher.failures
    assert "no session pool" in failure.reason
    assert run.state.launcher.task == "do a thing"
    assert [level for level, text in _app_log_since(mark) if "no session pool" in text] == [
        Level.ERROR
    ]


def test_two_refusals_read_on_one_frame_are_not_given_each_others_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Both decided before the frame looks, so one reap hands back two drafts. Each
    failure has to arrive with the text it was pressed from, in the order pressed.
    """
    with _Launching(monkeypatch, check=_BELOW) as run:
        run.launch("the first\nwith its reasons", contained=True)
        run.launch("the second\nwith its reasons", contained=True)
        run.settle()
        run.app._reap_launches(run.state)

    assert _failed_tasks(run.state.launcher.failures) == [
        "the first\nwith its reasons",
        "the second\nwith its reasons",
    ]
    assert run.state.launcher.task == "the first\nwith its reasons"


def test_an_exception_on_a_launch_with_no_draft_is_still_said_somewhere(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    ``_launch`` keeps every future, a draftless launch's included, so there are two
    places this is said: the log, on the loop, and the launcher's failures once the
    frame reaps it -- with no draft to hand back and the exception's type in the
    reason. An exception left on an unread future is a relaunch that did nothing and
    said nothing.
    """
    from pptmstr import app

    def explode(*args: object, **kwargs: object) -> AgentSession:
        raise OSError("worktree path is not writable")

    with _Launching(monkeypatch) as run:
        monkeypatch.setattr(app, "AgentSession", explode)
        run.app._launch(run.state, _spec())
        run.settle()
        assert "OSError: worktree path is not writable" in capsys.readouterr().out
        run.app._reap_launches(run.state)

    assert run.sessions == []
    assert run.state.launches == []
    [failure] = run.state.launcher.failures
    assert failure.draft is None and failure.origin == "launch"
    assert failure.reason == "OSError: worktree path is not writable"


# -- quitting ----------------------------------------------------------------------


def test_a_launch_that_reaches_a_pool_already_shutting_down_is_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A contained launch awaits a version read before it reaches the pool, so one
    pressed just before the window closed arrives after ``shutdown`` has begun. The
    real pool is used: what is under test is that its answer becomes the launch's.
    """
    from pptmstr.pool import SessionPool

    with _Launching(monkeypatch) as run:
        pool = SessionPool(run.bridge)
        run.state.pool = pool
        run.release.clear()
        run.launch("contain this", contained=True)
        assert run.bridge.submit(pool.shutdown()).result(timeout=5.0) == ()
        run.release.set()
        run.settle()
        reason = run.app._why_not_started(run.state.launches[0].future)

    assert reason is not None and "shutting down" in reason
    assert pool.sessions == {}
    assert pool.running_count == 0


def test_the_log_says_launched_only_for_a_launch_the_pool_took(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The same press against a pool that takes it and a pool that is shut. The line
    saying a launch happened is in the first log and not in the second, where a
    reader would otherwise meet "launched" and then "started nothing" for one press.
    """
    from pptmstr.pool import SessionPool

    mark = LOG.snapshot()[1]
    with _Launching(monkeypatch) as run:
        run.launch("contain this", contained=True)
        run.settle()
        assert run.app._why_not_started(run.state.launches[0].future) is None
    taken = [text for _, text in _app_log_since(mark)]

    mark = LOG.snapshot()[1]
    with _Launching(monkeypatch) as run:
        pool = SessionPool(run.bridge)
        run.state.pool = pool
        run.release.clear()
        run.launch("contain this", contained=True)
        assert run.bridge.submit(pool.shutdown()).result(timeout=5.0) == ()
        run.release.set()
        run.settle()
        assert run.app._why_not_started(run.state.launches[0].future) is not None
    refused = [text for _, text in _app_log_since(mark)]

    assert [text for text in taken if text.startswith("launched in ")] == [
        "launched in /tmp as solo: contain this"
    ]
    assert not [text for text in refused if text.startswith(("launched", "resumed"))]
    assert [text for text in refused if "started nothing" in text]


def test_a_team_launch_the_pool_refused_leaves_the_brief_it_seeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The brief is written before the pool answers, because the announce inside
    ``submit`` carries its path. So a refusal leaves one entry behind for a session
    that never ran, and this is the size of that: one directory, one entry.
    """
    from pptmstr import brief as brief_mod
    from pptmstr.pool import SessionPool

    monkeypatch.setattr(brief_mod, "default_root", lambda: tmp_path)
    with _Launching(monkeypatch) as run:
        pool = SessionPool(run.bridge)
        run.state.pool = pool
        run.release.clear()
        run.app._launch(
            run.state,
            _spec(containment=CONTAINMENT, policy=Policy.AUTONOMOUS, template="feature"),
        )
        assert run.bridge.submit(pool.shutdown()).result(timeout=5.0) == ()
        run.release.set()
        run.settle()
        assert run.app._why_not_started(run.state.launches[0].future) is not None

    assert pool.sessions == {}
    seeded = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(seeded) == 1
    assert "do a thing" in seeded[0].read_text()


class _QuittingBridge:
    """
    A bridge that records how ``_shut_down`` used it and runs nothing.
    """

    def __init__(self, outcome: BaseException | None) -> None:
        self.outcome = outcome
        self.waited: list[float | None] = []
        self.stopped = 0

    def submit(self, coro):  # type: ignore[no-untyped-def]
        coro.close()
        return self

    def result(self, timeout: float | None = None) -> None:
        self.waited.append(timeout)
        if self.outcome is not None:
            raise self.outcome

    def stop(self) -> None:
        self.stopped += 1


def _quit(outcome: BaseException | None) -> tuple[_QuittingBridge, BaseException | None]:
    from pptmstr import app
    from pptmstr.pool import SessionPool

    bridge = _QuittingBridge(outcome)
    state = app.AppState(store=Store(), bridge=bridge, settings=Settings())  # type: ignore[arg-type]
    state.pool = SessionPool(bridge, shutdown_grace_s=7.0, shutdown_reap_s=2.0)  # type: ignore[arg-type]
    raised: BaseException | None = None
    try:
        app._shut_down(state)
    except BaseException as exc:  # noqa: BLE001 - the test reads what came out
        raised = exc
    return bridge, raised


def test_quitting_waits_for_the_pool_longer_than_the_pool_waits_for_its_runs() -> None:
    """
    The pool gives up on a run that will not end and says which. A draw thread that
    stopped waiting first would report a timeout that names nothing.
    """
    from pptmstr import app

    bridge, raised = _quit(None)
    assert raised is None
    assert bridge.waited == [9.0 + app._SHUTDOWN_MARGIN_S]
    assert app._SHUTDOWN_MARGIN_S > 0
    assert bridge.stopped == 1


@pytest.mark.parametrize("outcome", [TimeoutError("still waiting"), RuntimeError("boom")])
def test_the_bridge_is_stopped_when_the_pools_shutdown_does_not_return_cleanly(
    outcome: BaseException,
) -> None:
    """
    ``Bridge.stop`` releases the parked approvals and cancels what is left on the
    loop. A shutdown that timed out is the one that left something there.
    """
    bridge, raised = _quit(outcome)
    assert raised is outcome
    assert bridge.stopped == 1


# -- the directory is asked about again where the session is started ---------------


def test_a_contained_launch_into_pptmstrs_own_checkout_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The launcher asks whether the directory is pptmstr's own source when the button
    is pressed, and the version read is awaited between that and the session. A spec
    that arrives here contained and pointing inside the checkout -- because the path
    moved in between -- must not become an unattended session writing ``approval.py``.

    Refused after the version read and not instead of it, so the question is asked
    as late as this function can ask it.
    """
    from pptmstr.ui import launcher

    inside = str(Path(launcher.__file__).resolve().parent)
    spec = LaunchSpec(
        task="do a thing\nfor a reason",
        model="claude-sonnet-5",
        cwd=inside,
        containment=CONTAINMENT,
        policy=Policy.AUTONOMOUS,
    )
    mark = LOG.snapshot()[1]
    with _Launching(monkeypatch) as run:
        run.release.clear()
        run.app._launch(run.state, spec, run.state.launcher.draft())
        asked: list[str] = []
        real = launcher.refusal_for

        def noting(cwd: str, **kwargs: object) -> str | None:
            asked.append(cwd)
            return real(cwd, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(launcher, "refusal_for", noting)
        # Nothing has asked while the version read is still out.
        assert asked == []
        run.release.set()
        run.settle()
        run.app._reap_launches(run.state)

    assert asked == [inside]
    assert run.sessions == []
    [failure] = run.state.launcher.failures
    assert inside in failure.reason and "own checkout" in failure.reason
    assert [level for level, text in _app_log_since(mark) if "own checkout" in text] == [
        Level.ERROR
    ]


def test_an_ordinary_launch_into_pptmstrs_own_checkout_is_not_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The refusal is the top rung's. Working on pptmstr from pptmstr under the gate is
    the ordinary case, and the default directory is this checkout.
    """
    from pptmstr.ui import launcher

    inside = str(Path(launcher.__file__).resolve().parent)
    out = _run_launch(LaunchSpec(task="t", model="claude-sonnet-5", cwd=inside), monkeypatch)

    assert [s.cwd for s in out.sessions] == [inside]


# -- the launcher is told which sessions are live ------------------------------------


def _roots_and_a_worker() -> Store:
    """
    Two sessions that have not ended and one that has, with a sub-agent under one
    of each.
    """
    from pptmstr.intents import AgentSpawned, StateChanged
    from pptmstr.model import AgentState

    store = Store()
    for session in ("live", "waiting", "closed"):
        store.apply(AgentSpawned((session, None), None, "t", "m", 0.0), now=0.0)
    # The second worker is the one that tells a root from a node: its session has
    # ended and it has not, which is how a closed session's sub-agents are left.
    for session in ("live", "closed"):
        store.apply(AgentSpawned((session, "worker"), (session, None), "t", "m", 0.0), now=0.0)
    store.apply(StateChanged(("waiting", None), AgentState.AWAITING_INPUT), now=0.0)
    store.apply(StateChanged(("closed", None), AgentState.DONE), now=0.0)
    return store


def test_the_live_sessions_are_the_roots_that_have_not_ended() -> None:
    """
    A session id, once per session: a sub-agent shares its root's id and is not a
    session, and a root that has ended has no run for a resume to end.
    """
    from pptmstr.app import _live_sessions

    snap = _roots_and_a_worker().snapshot()
    assert not snap.nodes[("closed", "worker")].state.is_terminal

    assert _live_sessions(snap) == frozenset({"live", "waiting"})
    assert _live_sessions(None) == frozenset()


def test_the_frame_hands_the_launcher_the_live_sessions_while_it_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The wiring. A projection nothing passes to the draw leaves its own test green
    and the modal silent about the session it is about to end.
    """
    from pptmstr.ui import launcher

    handed: list[object] = []
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
    _no_settings_dialog(monkeypatch)
    monkeypatch.setattr(launcher, "draw", lambda state, **kw: handed.append(kw.get("live")))

    with _Launching(monkeypatch) as run:
        run.state.store = _roots_and_a_worker()
        run.state.frame_snap = run.state.store.snapshot()
        run.app._draw_overlays(run.state)
        run.state.launcher.is_open = True
        run.app._draw_overlays(run.state)

    assert handed == [frozenset(), frozenset({"live", "waiting"})]


# -- the board survives a resume -----------------------------------------------------
#
# What is real here: the Bridge and its loop thread, the Store, the BoardWriter and
# the files it appends under ``tmp_path``, and ``app._launch``/``app.apply_intents``.
# What is not: the pool and the session's run. The pool below announces as the real
# one does and then starts a task that emits the session's bus intents, which is
# the earliest a real session could say anything.


class _Process:
    """
    One pptmstr process as far as a board is concerned: an ``AppState`` with its
    own store, bridge and writer over a shared log root, for the length of a
    ``with``. Two of them, one after the other, are a restart.

    ``says`` maps a launched session to the bus intents its run emits. ``takes``
    False is a pool that has begun shutting down.

    ``state`` is one ``app._start`` built, taken as it is but for its pool: the
    real one would start a CLI. ``legacy`` is a legacy log root for the writer.
    """

    def __init__(
        self,
        root: Path | None,
        says: object = None,
        *,
        takes: bool = True,
        settings: Settings | None = None,
        state: object = None,
        legacy: Path | None = None,
    ) -> None:
        from pptmstr import app, boardsync

        self.app = app
        if state is not None:
            self.state = state  # type: ignore[assignment]
            self.bridge = self.state.bridge
        else:
            self.bridge = Bridge()
            self.state = app.AppState(
                store=Store(), bridge=self.bridge, settings=settings or Settings()
            )
        if root is not None:
            self.state.board_writer = boardsync.BoardWriter(root, legacy)
        self.sessions: dict[tuple[str, str | None], AgentSession] = {}
        # The session ids the pool says a run is attached to (``has_run``): one
        # it took, until a test says the run is over (``_ends`` does).
        self.running: set[str] = set()
        # Every node the pool was asked to close.
        self.closed: list[tuple[str, str | None]] = []
        # Every intent in the order a frame was handed it.
        self.applied: list[object] = []
        outer = self
        says_ = says

        class _Pool:
            def session_for(self, node: tuple[str, str | None]) -> AgentSession | None:
                return outer.sessions.get((node[0], None))

            def has_run(self, node: tuple[str, str | None]) -> bool:
                return node[0] in outer.running

            async def close(self, node: tuple[str, str | None]) -> None:
                outer.closed.append(node)

            def submit(self, session: AgentSession) -> bool:
                import asyncio

                if not takes:
                    return False
                outer.sessions[session.node_id] = session
                outer.running.add(session.session_id)
                session.announce()

                async def run() -> None:
                    for intent in says_(session) if says_ is not None else ():  # type: ignore[operator]
                        outer.bridge.emit(intent)

                asyncio.get_running_loop().create_task(run())
                return True

        self.state.pool = _Pool()  # type: ignore[assignment]
        drain = self.bridge.drain

        def noting(max_items: int = 10_000) -> list[object]:
            taken = drain(max_items)
            outer.applied.extend(taken)
            return taken  # type: ignore[return-value]

        self.bridge.drain = noting  # type: ignore[method-assign,assignment]

    def __enter__(self) -> _Process:
        self.bridge.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self.state.board_writer is not None:
            self.state.board_writer.close()
        self.state.fleet_writer.close()
        self.bridge.stop()

    def launch(self, *, resume: str | None = None, template: str = "feature") -> None:
        spec = LaunchSpec(
            task="build the thing",
            model="claude-sonnet-5",
            cwd="/tmp",
            template=template,
            resume=resume,
        )
        self.app._launch(self.state, spec)
        for entry in self.state.launches:
            entry.future.exception(timeout=5.0)
        # ``run`` is a task the launch started; one more trip round the loop and
        # everything it emits is in the queue.
        self.bridge.submit(_nothing()).result(timeout=5.0)

    def frame(self) -> None:
        """What ``begin_frame`` does to the store, and then the writer caught up."""
        import time

        self.state.frame_now = time.monotonic()
        self.app.apply_intents(self.state)
        writer = self.state.board_writer
        if writer is not None:
            deadline = time.monotonic() + 5.0
            while writer.outstanding and time.monotonic() < deadline:
                time.sleep(0.005)
            assert writer.outstanding == 0
            self.app._read_board_reports(self.state, writer.drain())
        fleet = self.state.fleet_writer
        deadline = time.monotonic() + 5.0
        while fleet.outstanding and time.monotonic() < deadline:
            time.sleep(0.005)
        assert fleet.outstanding == 0

    @property
    def session_id(self) -> str:
        [node] = self.sessions
        return node[0]

    def log(self, session_id: str | None = None):  # type: ignore[no-untyped-def]
        return self.state.board_logs.get(session_id or self.session_id)


async def _nothing() -> None:
    import asyncio

    await asyncio.sleep(0)


_BUILDER = "agent-0b1"


def _a_working_team(session: AgentSession) -> list[object]:
    """
    A lead that declares two tasks and posts a concern, and a builder that takes
    one of them and finishes it.
    """
    import time

    from pptmstr.intents import (
        AgentSpawned,
        ConcernPosted,
        TaskClaimRequested,
        TaskCompleted,
        TaskDeclared,
    )
    from pptmstr.model import Basis, Concern, Task

    root = session.node_id
    builder = (root[0], _BUILDER)
    now = time.monotonic()
    return [
        TaskDeclared(Task(id="t-1", title="one", detail="first spec", declared_at=now), root),
        TaskDeclared(Task(id="t-2", title="two", detail="second spec", declared_at=now), root),
        AgentSpawned(builder, root, "build", "claude-sonnet-5", now, agent_type="builder"),
        TaskClaimRequested(builder, "rq-1", "t-1"),
        TaskClaimRequested(builder, "rq-2", "t-2"),
        TaskCompleted(builder, "t-1", now, result="done", basis=Basis.RUN),
        ConcernPosted(
            builder,
            Concern(
                id="c-1",
                sender=builder,
                recipient=root,
                subject="least sure",
                body="the cross presets",
                posted_at=now,
            ),
        ),
    ]


def _one_more_task(task_id: str = "t-3"):  # type: ignore[no-untyped-def]
    def says(session: AgentSession) -> list[object]:
        import time

        from pptmstr.intents import TaskDeclared
        from pptmstr.model import Task

        return [
            TaskDeclared(
                Task(id=task_id, title="later", declared_at=time.monotonic()), session.node_id
            )
        ]

    return says


def _board_of(store: Store, session_id: str) -> dict[str, object]:
    """
    A session's board with every party as a NodeId and every time left out: a
    restored time is rebuilt from a clock reading and is not the one first stamped.
    """
    snap = store.snapshot()
    tasks = {
        t.id: (t.state, t.detail, t.declared_by, t.claimed_by, t.held_by, t.result, t.result_basis)
        for t in snap.tasks.values()
        if t.belongs_to(session_id)
    }
    concerns = {
        c.id: (c.sender, c.recipient, c.subject, c.body, c.state)
        for c in snap.concerns.values()
        if c.sender[0] == session_id
    }
    return {"tasks": tasks, "concerns": concerns}


def _first_run(root: Path) -> tuple[str, dict[str, object]]:
    """A team session run to a board with work on it, and the process ended."""
    with _Process(root, _a_working_team) as first:
        first.launch()
        first.frame()
        session_id = first.session_id
        board = _board_of(first.state.store, session_id)
        assert first.log().recording
    assert len(board["tasks"]) == 2 and len(board["concerns"]) == 1  # type: ignore[arg-type]
    return session_id, board


def test_a_resumed_session_has_its_logged_board_before_its_own_first_bus_intent(
    tmp_path: Path,
) -> None:
    """
    The property, end to end across a restart: the second process's store holds
    the first's board, party for party, and the restore was applied ahead of the
    first thing the resumed session said.
    """
    from pptmstr.intents import BoardRestored, TaskDeclared
    from pptmstr.model import TaskState

    session_id, before = _first_run(tmp_path)

    with _Process(tmp_path, _one_more_task()) as second:
        second.launch(resume=session_id)
        second.frame()
        after = _board_of(second.state.store, session_id)
        kinds = [type(intent) for intent in second.applied]
        report = second.state.store.snapshot().restores[session_id]
        log = second.log()

    assert kinds.index(BoardRestored) < kinds.index(TaskDeclared)
    restored = {k: v for k, v in after["tasks"].items() if k != "t-3"}  # type: ignore[attr-defined]
    assert restored == before["tasks"]
    assert after["concerns"] == before["concerns"]
    # By NodeId and not by name: the builder is the agent it was.
    assert restored["t-2"][3] == (session_id, _BUILDER)
    assert restored["t-1"][0] is TaskState.COMPLETED
    assert "t-3" in after["tasks"]  # type: ignore[operator]
    assert report.adoptable and not report.short
    assert log.recording and not log.awaiting and log.newest is not None


def test_an_await_between_the_pools_answer_and_the_restore_would_let_the_session_speak_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Why the ordering test above can fail. The restore is emitted with no await
    after ``pool.submit``; were one added, the session's run -- already a task --
    would emit first. Shown by putting the yield there.
    """
    import asyncio

    from pptmstr import app
    from pptmstr.intents import BoardRestored, TaskDeclared

    session_id, _ = _first_run(tmp_path)
    real = app._open_board_log
    late: list[object] = []

    def deferred(state: object, sid: str, logged: object) -> None:
        asyncio.get_running_loop().call_soon(lambda: late.append(real(state, sid, logged)))  # type: ignore[arg-type]

    monkeypatch.setattr(app, "_open_board_log", deferred)
    with _Process(tmp_path, _one_more_task()) as second:
        second.launch(resume=session_id)
        second.bridge.submit(_nothing()).result(timeout=5.0)
        second.frame()
        kinds = [type(intent) for intent in second.applied]

    assert kinds.index(TaskDeclared) < kinds.index(BoardRestored)


def test_what_a_resumed_session_does_next_is_appended_behind_the_log_it_restored(
    tmp_path: Path,
) -> None:
    """
    Adoption. The third process reads one history: both runs' tasks, nothing
    contested, nothing stray.
    """
    from pptmstr import boardsync

    session_id, _ = _first_run(tmp_path)
    with _Process(tmp_path, _one_more_task()) as second:
        second.launch(resume=session_id)
        second.frame()

    board = boardsync.load(session_id, tmp_path)
    assert isinstance(board, boardsync.Board)
    assert [t.id for t in board.tasks] == ["t-1", "t-2", "t-3"]
    assert not board.short


def test_unhooking_the_observer_leaves_the_log_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The wiring. With the observer in place the run's facts are files; with
    ``apply_all`` called as it was before there was one, nothing is.
    """
    from pptmstr import boardsync

    session_id, _ = _first_run(tmp_path / "hooked")
    assert isinstance(boardsync.load(session_id, tmp_path / "hooked"), boardsync.Board)

    real = Store.apply_all

    def unobserved(self: Store, intents: object, now: float | None = None, observe: object = None):  # type: ignore[no-untyped-def]
        return real(self, intents, now)  # type: ignore[arg-type]

    monkeypatch.setattr(Store, "apply_all", unobserved)
    with _Process(tmp_path / "unhooked", _a_working_team) as run:
        run.launch()
        run.frame()
        session_id = run.session_id
    assert isinstance(boardsync.load(session_id, tmp_path / "unhooked"), boardsync.NoLog)


def test_the_frame_is_the_one_caller_of_apply_intents() -> None:
    """
    ``begin_frame`` needs a GL context and is not run here, so that it applies
    through the observed path is read off its source: it calls ``apply_intents``
    and never ``apply_all`` itself.
    """
    import inspect

    from pptmstr import app

    source = inspect.getsource(app.begin_frame)
    assert "apply_intents(state)" in source
    assert "apply_all" not in source


def test_no_log_io_runs_on_the_thread_that_applies_the_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Every append, listing and read of a log, across a run and a resume, with the
    thread it ran on. None is the thread that calls ``apply_intents`` -- here the
    test's own -- and the read is not on the bridge's loop either.
    """
    from pptmstr import boardlog

    seen: list[tuple[str, threading.Thread]] = []
    for name in ("append", "read", "next_ordinal"):
        real = getattr(boardlog, name)

        def noting(*args: object, _real=real, _name=name, **kwargs: object):  # type: ignore[no-untyped-def]
            seen.append((_name, threading.current_thread()))
            return _real(*args, **kwargs)

        monkeypatch.setattr(boardlog, name, noting)

    session_id, _ = _first_run(tmp_path)
    with _Process(tmp_path, _one_more_task()) as second:
        loop_thread = second.bridge._thread
        second.launch(resume=session_id)
        second.frame()
    # ``_first_run`` and the assertions in it read nothing; ``boardsync.load`` is
    # only called by the launch.
    frame_thread = threading.current_thread()
    assert {name for name, _ in seen} >= {"append", "read"}
    assert all(thread is not frame_thread for _, thread in seen)
    assert all(thread is not loop_thread for name, thread in seen if name == "read")


def test_a_record_reaches_the_log_on_the_wall_clock_and_not_the_stores(
    tmp_path: Path,
) -> None:
    """
    ``Fact.record`` carries the store's monotonic stamp, which counts from boot,
    and nothing in the types stops it being appended. Every time in the log is
    within a minute of now on the wall clock.
    """
    import time

    from pptmstr import boardlog

    session_id, _ = _first_run(tmp_path)
    log = boardlog.read(tmp_path / session_id / "board")
    assert len(log.records) >= 6
    now = time.time()
    # Not a vacuous comparison on a machine that booted less than a minute
    # before the epoch.
    assert abs(now - time.monotonic()) > 3600
    assert all(abs(now - record.at) < 60 for record in log.records)  # type: ignore[union-attr]


def test_a_session_that_does_not_resume_reads_no_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptmstr import boardsync

    finds: list[str] = []
    real = boardsync.find

    def noting(sid: str, root: Path | None = None, legacy: Path | None = None) -> object:
        finds.append(sid)
        return real(sid, root, legacy)

    monkeypatch.setattr(boardsync, "find", noting)
    with _Process(tmp_path, _a_working_team) as run:
        run.launch()
        run.frame()
        assert run.log().recording
        session_id = run.session_id
    assert finds == []
    # The patch is on the name a resume reads through, or the list above is empty
    # whatever a launch does.
    with _Process(tmp_path, _one_more_task()) as second:
        second.launch(resume=session_id)
        second.frame()
    assert finds == [session_id]


def test_a_resume_with_no_log_adopts_an_empty_one_and_says_nothing(tmp_path: Path) -> None:
    """
    The session is recorded from its first fact, and no restore is reported:
    there was nothing to restore, and the pane has no line for that.
    """
    from pptmstr import boardsync
    from pptmstr.intents import BoardRestored
    from pptmstr.ui import board_pane

    with _Process(tmp_path, _one_more_task("t-1")) as run:
        run.launch(resume="never-logged")
        run.frame()
        log = run.log("never-logged")
        restores = run.state.store.snapshot().restores
        applied = [type(i) for i in run.applied]

    assert BoardRestored not in applied and restores == {}
    assert log.recording and log.unkept == 0
    assert board_pane.restore_line(None, log) is None
    board = boardsync.load("never-logged", tmp_path)
    assert isinstance(board, boardsync.Board) and [t.id for t in board.tasks] == ["t-1"]


def test_a_damaged_log_is_restored_short_and_the_session_runs_unrecorded(
    tmp_path: Path,
) -> None:
    """
    The readable prefix is on the board, the pane says the restore is short and
    that the session is unrecorded, and not one file is added to the log.
    """
    from pptmstr.ui import board_pane

    session_id, _ = _first_run(tmp_path)
    directory = tmp_path / session_id / "board"
    files = sorted(directory.iterdir())
    files[3].write_text("{not a record")
    before = sorted(p.name for p in directory.iterdir())

    with _Process(tmp_path, _one_more_task()) as second:
        second.launch(resume=session_id)
        second.frame()
        snap = second.state.store.snapshot()
        report, log = snap.restores[session_id], second.log()
        declared = "t-3" in snap.tasks

    assert report.short and not report.adoptable and report.log_damage is not None
    assert declared
    assert not log.recording and not log.stopped
    assert sorted(p.name for p in directory.iterdir()) == before
    assert [p.name for p in (tmp_path / session_id).iterdir()] == ["board"]
    restored = board_pane.restore_line(report, log)
    recording = board_pane.recording_line(None, log)
    assert restored is not None and recording is not None
    assert "SHORT" in restored.text and "unreadable" in restored.text
    assert restored.tone is board_pane.Tone.ALARM
    assert "UNRECORDED" in recording.text and recording.tone is board_pane.Tone.ALARM


def test_a_restored_id_another_live_session_holds_is_left_out_and_shown(
    tmp_path: Path,
) -> None:
    """
    Task ids are fleet-wide. The other session's ``t-1`` is untouched, the
    restore names it, and the resumed session is not appended to.
    """
    from pptmstr.intents import AgentSpawned, TaskDeclared
    from pptmstr.model import Task, Unplaced
    from pptmstr.ui import board_pane

    session_id, _ = _first_run(tmp_path)
    with _Process(tmp_path) as second:
        other = ("other-session", None)
        second.state.store.apply(AgentSpawned(other, None, "t", "m", 0.0), now=0.0)
        second.state.store.apply(
            TaskDeclared(Task(id="t-1", title="theirs", declared_at=0.0), other), now=0.0
        )
        second.launch(resume=session_id)
        second.frame()
        snap = second.state.store.snapshot()
        report, log = snap.restores[session_id], second.log()

    assert snap.tasks["t-1"].declared_by == other
    assert [(left.id, left.why) for left in report.unplaced] == [
        ("t-1", Unplaced.OTHER_SESSION_HAS_ID)
    ]
    assert not log.recording
    line = board_pane.restore_line(report, log)
    assert line is not None and "t-1" in line.text and "another session" in line.text


def test_a_launch_the_pool_refuses_restores_nothing(tmp_path: Path) -> None:
    """
    No board with no row: the store holds none of the session's tasks, so its
    ids are free, and nothing was opened for it.
    """
    session_id, _ = _first_run(tmp_path)
    with _Process(tmp_path, takes=False) as second:
        second.launch(resume=session_id)
        second.frame()
        snap = second.state.store.snapshot()
        logs = dict(second.state.board_logs)

    assert snap.tasks == {} and snap.concerns == {} and snap.restores == {}
    assert logs == {}


def test_a_session_that_is_taken_and_then_fails_to_start_keeps_its_board_on_its_row(
    tmp_path: Path,
) -> None:
    """
    The other two ways a launch starts nothing happen after the pool's answer:
    ``AgentSession.run`` refuses the directory, or the CLI does not connect.
    Both end a row that was announced, so the restored board is not invisible --
    it is the board of a failed session the operator can see and relaunch.
    """
    from pptmstr.board import has_board
    from pptmstr.intents import AgentFinished
    from pptmstr.model import AgentState

    session_id, before = _first_run(tmp_path)

    def fails(session: AgentSession) -> list[object]:
        return [AgentFinished(session.node_id, AgentState.FAILED, 1.0, error="could not connect")]

    with _Process(tmp_path, fails) as second:
        second.launch(resume=session_id)
        second.frame()
        snap = second.state.store.snapshot()
        after = _board_of(second.state.store, session_id)

    assert snap.nodes[(session_id, None)].state.is_terminal
    assert has_board(snap, session_id)
    assert after == before


def test_relaunching_a_session_this_process_records_reads_no_log_and_keeps_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The supersede case. The store is ahead of the disk, so the log is not read
    back: no restore is applied for the store to refuse, the log is not stopped,
    and what the second run does lands behind what the first did.
    """
    from pptmstr import boardsync
    from pptmstr.intents import BoardRestored

    loads: list[str] = []
    real = boardsync.load
    monkeypatch.setattr(
        boardsync, "load", lambda sid, root=None: (loads.append(sid), real(sid, root))[1]
    )
    said = iter([_a_working_team, _one_more_task()])
    with _Process(tmp_path, lambda session: next(said)(session)) as run:
        run.launch()
        run.frame()
        session_id = run.session_id
        run.launch(resume=session_id)
        run.frame()
        log = run.log(session_id)
        restores = run.state.store.snapshot().restores
        applied = [type(i) for i in run.applied]

    assert loads == [] and BoardRestored not in applied and restores == {}
    assert log.recording and not log.stopped and log.unkept == 0
    board = boardsync.load(session_id, tmp_path)
    assert isinstance(board, boardsync.Board)
    assert [t.id for t in board.tasks] == ["t-1", "t-2", "t-3"] and not board.short


def test_a_session_this_process_does_not_record_whose_board_differs_runs_unrecorded(
    tmp_path: Path,
) -> None:
    """
    The same refusal from the store, where the shell is NOT superseding a
    session it records: the store already holds a board for the id that the log
    does not describe, which is the second-window case. Nothing is appended and
    the pane says which mistake it is.
    """
    from pptmstr.intents import AgentSpawned, TaskDeclared
    from pptmstr.model import Task
    from pptmstr.ui import board_pane

    session_id, _ = _first_run(tmp_path)
    before = sorted(p.name for p in (tmp_path / session_id).iterdir())
    with _Process(tmp_path, _one_more_task()) as second:
        root = (session_id, None)
        second.state.store.apply(AgentSpawned(root, None, "t", "m", 0.0), now=0.0)
        second.state.store.apply(
            TaskDeclared(Task(id="elsewhere", title="x", declared_at=0.0), root), now=0.0
        )
        second.launch(resume=session_id)
        second.frame()
        report = second.state.store.snapshot().restores[session_id]
        log = second.log()

    assert report.refused
    assert not log.recording and "another pptmstr window" in log.why_not
    assert sorted(p.name for p in (tmp_path / session_id).iterdir()) == before
    line = board_pane.restore_line(report, log)
    assert line is not None and "NOT applied" in line.text


def test_a_team_session_resumed_solo_still_has_its_board_drawn(tmp_path: Path) -> None:
    """
    ``has_board`` reads the template, and the template is this launch's. The
    restored facts are what the pane asks about instead.
    """
    from pptmstr.board import board_tasks, has_board
    from pptmstr.ui import board_pane

    session_id, _ = _first_run(tmp_path)
    with _Process(tmp_path) as second:
        second.launch(resume=session_id, template="solo")
        second.frame()
        snap = second.state.store.snapshot()

    assert not has_board(snap, session_id)
    rows = board_tasks(snap, session_id)
    assert len(rows) == 2
    assert board_pane.shows_board(False, len(rows), 0)


def test_a_record_that_does_not_land_stops_the_session_and_is_kept_as_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The writer reports a stop once and its list is emptied by the read, so the
    shell keeps it: frames later the entry still says stopped, why, and how many
    records are not in the log.
    """
    from pptmstr import boardlog
    from pptmstr.ui import board_pane

    real = boardlog.append
    calls = [0]

    def failing(directory: Path, record: object, **kwargs: object):  # type: ignore[no-untyped-def]
        calls[0] += 1
        if calls[0] == 3:
            return boardlog.Unwritten("no space left on device")
        return real(directory, record, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(boardlog, "append", failing)
    with _Process(tmp_path, _a_working_team) as run:
        run.launch()
        run.frame()
        run.frame()
        run.frame()
        log = run.log()

    assert not log.recording and log.stopped and log.marked is True
    assert "no space left" in log.why_not and log.unkept >= 2
    line = board_pane.recording_line(None, log)
    assert line is not None and "no longer being recorded" in line.text
    assert "no space left" in line.text and line.tone is board_pane.Tone.ALARM


def test_an_observer_that_cannot_derive_a_fact_does_not_lose_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``Store.apply_all`` abandons a batch whose observer raises, and the intents
    are already out of the queue. So the observer does not raise: the store
    still takes the batch, and the logs are closed and told so.
    """
    from pptmstr import boardfacts, boardsync
    from pptmstr.intents import TaskDeclared
    from pptmstr.model import Task

    with _Process(tmp_path, _a_working_team) as run:
        run.launch()
        run.frame()
        session_id = run.session_id

        def broken(*args: object) -> tuple[()]:
            raise RuntimeError("no case for this")

        monkeypatch.setattr(boardfacts, "outcomes", broken)
        run.bridge.emit(
            TaskDeclared(Task(id="t-3", title="later", declared_at=0.0), (session_id, None))
        )
        run.frame()
        run.frame()
        held = "t-3" in run.state.store.snapshot().tasks
        log = run.log(session_id)

    assert held
    assert not log.recording and log.stopped and log.marked is True
    board = boardsync.load(session_id, tmp_path)
    assert isinstance(board, boardsync.Board) and board.short and len(board.stopped) == 1


def test_with_the_switch_off_nothing_is_read_written_or_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``app._start`` builds a writer only when the setting is on; with none, a team run
    and a resume touch no file and the pane says the boards are not recorded.

    Both roots a log could be under are this test's: the legacy ``boards/`` and
    ``sessions/``, where a session's directory would hold its ``board/``. Neither
    is made. The sessions here never connect, so no session file makes the second.
    """
    from pptmstr import app, boardsync, sessionfile
    from pptmstr.ui import board_pane

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert boardsync.legacy_root() == tmp_path / "pptmstr" / "boards"
    assert sessionfile.root() == tmp_path / "pptmstr" / "sessions"
    for name in ("find", "load", "migrate"):
        monkeypatch.setattr(
            boardsync,
            name,
            lambda *a, _name=name, **k: pytest.fail(f"boardsync.{_name} with the switch off"),
        )
    off = Settings(board_log=False)
    with _Process(None, _a_working_team, settings=off) as run:
        run.launch()
        run.frame()
        run.launch(resume=run.session_id)
        run.frame()
        logging = app.board_logging(run.state)
        logs = dict(run.state.board_logs)

    assert not boardsync.legacy_root().exists() and not sessionfile.root().exists()
    assert logs == {}
    line = board_pane.recording_line(logging.off, None)
    assert line is not None and "switched off" in line.text and "board_log" in line.text


def _settings_log_since(mark: int) -> list[tuple[Level, str]]:
    """``_app_log_since`` for what ``app._save`` logs, which is under "settings"."""
    entries, revision = LOG.snapshot()
    recent = entries[len(entries) - (revision - mark) :] if revision > mark else []
    return [(e.level, e.text) for e in recent if e.source == "settings"]


def _args(**given: object) -> object:
    """The command line ``app._start`` reads, with nothing asked for."""
    import argparse

    asked: dict[str, object] = {
        "theme": None,
        "fps_idle": None,
        "cap": None,
        "fake": False,
        "layout": None,
    }
    return argparse.Namespace(**{**asked, **given})


def test_a_writer_is_built_only_when_the_switch_is_on_and_never_under_fake() -> None:
    from pptmstr import app
    from pptmstr.settings import Loaded

    on, off = Loaded(Settings()), Loaded(Settings(board_log=False))
    recorded = app._start(_args(), on, list_sessions=tuple)  # type: ignore[arg-type]
    try:
        assert recorded.board_writer is not None
    finally:
        app._close_board_log(recorded)
    assert app._start(_args(fake=True), on).board_writer is None  # type: ignore[arg-type]
    assert app._start(_args(), off, list_sessions=tuple).board_writer is None  # type: ignore[arg-type]


def test_main_starts_from_what_read_found_and_not_from_the_values_alone() -> None:
    """
    Read off ``main``'s source, which opens a window and cannot be run here. The
    reason a read leaves behind reaches the pane and the save only if ``main``
    hands ``_start`` the whole of what ``read`` returned.
    """
    import inspect

    from pptmstr import app

    source = inspect.getsource(app.main)
    assert "state = _start(args, settings_mod.read())" in source
    assert "settings_mod.load(" not in inspect.getsource(app)
    assert inspect.getsource(app).count("settings_mod.save(") == 1


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ('{"theme": "dark", "board_log": true,}', "settings could not be read"),
        ('{"board_log": "true"}', "board_log in settings.json is not true or false"),
    ],
)
def test_a_settings_file_that_could_not_be_read_is_said_in_alarm_and_never_written_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, reason: str
) -> None:
    """
    The hazard end to end. The operator never switched recording off; a broken
    file did. So no board is recorded, the pane says that in the alarm tone with
    the reason and the file -- not "switched off", which is a choice -- and a
    change of preference afterwards leaves the file byte for byte, is said once
    in the log, and stays said on the state the status bar reads.
    """
    from pptmstr import app
    from pptmstr import settings as settings_mod
    from pptmstr.ui import board_pane

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    file = settings_mod.settings_path()
    file.parent.mkdir(parents=True)
    file.write_text(text)

    state = app._start(_args(), settings_mod.read(), list_sessions=tuple)  # type: ignore[arg-type]

    assert state.board_writer is None
    logging = app.board_logging(state)
    line = board_pane.recording_line(logging.off, None, logging.unread)
    assert line is not None and line.tone is board_pane.Tone.ALARM
    assert line.text == f"not recorded: {reason} ({file})"
    assert "switched off" not in line.text

    mark = LOG.snapshot()[1]
    app._switch_wrap(state, not state.settings.wrap_inputs)
    app._switch_wrap(state, not state.settings.wrap_inputs)

    assert file.read_text() == text
    assert [p.name for p in file.parent.iterdir()] == [file.name]
    errors = [said for level, said in _settings_log_since(mark) if level is Level.ERROR]
    assert len(errors) == 1
    assert str(file) in errors[0] and reason in errors[0]
    assert state.unsaved == settings_mod.Unsaved(file, state.board_log_unread)  # type: ignore[arg-type]

    # Repaired while the run goes on: the file is readable now and says on. What
    # the run holds is still the fail-safe, and saving it would switch recording
    # off in a file that has just been made to say the opposite.
    repaired = '{"board_log": true}'
    file.write_text(repaired)
    app._switch_wrap(state, not state.settings.wrap_inputs)
    assert file.read_text() == repaired
    assert len([s for lv, s in _settings_log_since(mark) if lv is Level.ERROR]) == 1


def test_a_readable_settings_file_is_saved_and_nothing_is_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptmstr import app
    from pptmstr import settings as settings_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    state = app._start(_args(fake=True), settings_mod.read())  # type: ignore[arg-type]
    wrap = not state.settings.wrap_inputs
    mark = LOG.snapshot()[1]

    app._switch_wrap(state, wrap)

    assert settings_mod.load().wrap_inputs is wrap
    assert state.unsaved is None
    assert not [said for level, said in _settings_log_since(mark) if level is Level.ERROR]
    assert "fake driver" in (app.board_logging(state).off or "")


def test_the_addresses_of_a_logged_board_reach_the_resumed_session(tmp_path: Path) -> None:
    """
    The first process's builder was ``builder``. The resumed session is built
    with that, so its first new builder is not a second one.
    """
    session_id, _ = _first_run(tmp_path)
    with _Process(tmp_path) as second:
        second.launch(resume=session_id)
        session = second.sessions[(session_id, None)]

    assert session._restored == {"builder": _BUILDER}
    assert session._address_for("agent-new", "builder") == "builder-2"
    assert session._address_for(_BUILDER, "builder") == "builder"


# -- one name per agent, across restarts ------------------------------------------


def _start_builder(run: _Process, session_id: str, agent_id: str) -> tuple[str, str | None]:
    """
    A builder started through the driver's own hook -- the allocation mail is
    routed by -- and its spawn applied, so the board has the record to name.
    """
    session = run.sessions[(session_id, None)]
    hook = {"hook_event_name": "SubagentStart", "agent_id": agent_id, "agent_type": "builder"}
    run.bridge.submit(session._subagent_start(hook, None, None)).result(timeout=5.0)  # type: ignore[arg-type]
    run.frame()
    return (session_id, agent_id)


def _named_alike(run: _Process, session_id: str) -> dict[tuple[str, str | None], str]:
    """
    Every sub-agent this process has a record for, under its address -- having
    checked that the driver, the board and the log's record agree on it and that
    the address routes back to the node.
    """
    from pptmstr import board, boardfacts

    session = run.sessions[(session_id, None)]
    snap = run.state.store.snapshot()
    names: dict[tuple[str, str | None], str] = {}
    for record in snap.subagents_of((session_id, None)):
        node = record.node_id
        routed = session.role_of(node)
        assert routed is not None
        assert board.role_name(snap, node, session_id) == routed
        assert boardfacts._address(snap, node) == routed
        assert session.resolve_role(routed) == node
        names[node] = routed
    assert len(set(names.values())) == len(names)
    return names


def _claims(run: _Process, node: tuple[str, str | None], task_id: str) -> None:
    """``node`` declares and takes a task, which is what puts its address in the log."""
    import time

    from pptmstr.intents import TaskClaimRequested, TaskDeclared
    from pptmstr.model import Task

    root = (node[0], None)
    run.bridge.emit(
        TaskDeclared(Task(id=task_id, title="more", declared_at=time.monotonic()), root)
    )
    run.bridge.emit(TaskClaimRequested(node, f"rq-{task_id}", task_id))
    run.frame()


def test_an_agent_has_one_name_on_every_surface_across_two_restarts(tmp_path: Path) -> None:
    """
    The first process's builder was ``builder``. In the second, a new builder is
    ``builder-2`` to the driver that routes its mail, and has to be that on the
    board every agent reads and in the record the log keeps -- or the third
    process is told two agents answered to ``builder`` and reserves the name of
    neither's successor.
    """
    from pptmstr import board

    session_id, _ = _first_run(tmp_path)
    old = (session_id, _BUILDER)

    with _Process(tmp_path) as second:
        second.launch(resume=session_id)
        second.frame()
        new = _start_builder(second, session_id, "agent-new-1")
        _claims(second, new, "t-4")
        assert _named_alike(second, session_id) == {new: "builder-2"}
        snap = second.state.store.snapshot()
        assert board.role_name(snap, old, session_id) == f"builder, {BEFORE_RESTART}"

    with _Process(tmp_path) as third:
        third.launch(resume=session_id)
        third.frame()
        session = third.sessions[(session_id, None)]
        assert session._restored == {"builder": _BUILDER, "builder-2": "agent-new-1"}
        newest = _start_builder(third, session_id, "agent-new-2")
        assert _named_alike(third, session_id) == {newest: "builder-3"}
        snap = third.state.store.snapshot()
        assert board.role_name(snap, new, session_id) == f"builder-2, {BEFORE_RESTART}"
        assert board.role_name(snap, old, session_id) == f"builder, {BEFORE_RESTART}"


def _logged_team():  # type: ignore[no-untyped-def]
    """
    A log an earlier process left: ``builder`` and ``builder-2`` by id, each
    holding a task, and a ``reviewer`` an older log knew by address alone.
    """
    from pptmstr import boardlog

    sid = "s-logged"

    def declared(task_id: str, at: float) -> boardlog.Declared:
        return boardlog.Declared(
            at=at, task_id=task_id, title=task_id, declared_by="lead", declared_by_id=(sid,)
        )

    return sid, boardlog.restore(
        [
            declared("t1", 4_900.0),
            boardlog.Claimed(at=4_910.0, task_id="t1", by="builder", by_id=(sid, "old-1")),
            declared("t2", 4_920.0),
            boardlog.Claimed(at=4_930.0, task_id="t2", by="builder-2", by_id=(sid, "old-2")),
            declared("t3", 4_940.0),
            boardlog.Claimed(at=4_950.0, task_id="t3", by="reviewer", by_id=()),
        ]
    )


_STARTS = (("old-2", "builder"), ("new-1", "builder"), ("new-2", "reviewer"), ("new-3", "builder"))


@pytest.mark.parametrize("order", list(__import__("itertools").permutations(_STARTS)))
def test_the_driver_the_board_and_the_log_name_every_agent_alike_in_any_start_order(
    order: tuple[tuple[str, str], ...],
) -> None:
    """
    The property, without a process around it: a session constructed with what
    ``_addresses_of`` makes of a log, the store handed the same log, and four
    agents started in every order -- one of them an earlier agent woken under
    the id it had. After each start, every agent with a record has one address:
    the one its mail is routed by, the one the board shows, the one a log record
    is written under; it routes back to that agent; and no two agents share one.

    Both answers to the unmeasured question are in it. ``old-2`` comes back
    under its id and is ``builder-2`` again wherever it starts; ``old-1`` never
    does, so ``builder`` stays the earlier agent's and whatever the CLI calls a
    woken ``old-1`` is a new agent with a new name.
    """
    import asyncio

    from pptmstr import app, board, boardfacts, boardlog
    from pptmstr.intents import BoardRestored
    from pptmstr.templates import FEATURE

    sid, logged = _logged_team()
    bridge = Bridge()
    session = AgentSession(
        bridge,
        "t",
        template=FEATURE,
        resume=sid,
        restored_addresses=app._addresses_of(logged, sid),
    )
    store = Store()
    session.announce()
    for intent in bridge.drain():
        store.apply(intent)
    clock = boardlog.Clock(wall=5_000.0, monotonic=100.0)
    store.apply(BoardRestored(sid, logged, clock), now=clock.monotonic)
    assert board.restored_table(store.snapshot(), sid) == session._restored
    assert session._restored == {"builder": "old-1", "builder-2": "old-2", "reviewer": None}

    for agent_id, agent_type in order:
        hook = {"hook_event_name": "SubagentStart", "agent_id": agent_id, "agent_type": agent_type}
        asyncio.run(session._subagent_start(hook, None, None))  # type: ignore[arg-type]
        for intent in bridge.drain():
            store.apply(intent)
        snap = store.snapshot()
        names = {}
        for record in snap.subagents_of((sid, None)):
            node = record.node_id
            names[node[1]] = session.role_of(node)
            assert board.role_name(snap, node, sid) == names[node[1]]
            assert boardfacts._address(snap, node) == names[node[1]]
            assert session.resolve_role(names[node[1]] or "") == node
        assert len(set(names.values())) == len(names)
        assert not set(names.values()) & {"builder", "reviewer"}
        if "old-2" in names:
            assert names["old-2"] == "builder-2"
        # The agent that has not come back is named alike too, and unroutably.
        gone = f"builder, {BEFORE_RESTART}"
        assert session.role_of((sid, "old-1")) == board.role_name(snap, (sid, "old-1"), sid) == gone
        assert session.resolve_role(gone) is None and session.resolve_role("builder") is None

    assert set(names.values()) == {"builder-2", "builder-3", "builder-4", "reviewer-2"}


def _end_run_for_a_relaunch(run: _Process, session: AgentSession) -> None:
    """
    What the driver leaves of a run that is relaunched over: the pool asks for
    the teardown, ``AgentSession.run`` settles the sub-agents it still had and
    then ends the root, and the successor is announced only after both.
    """
    import time

    from pptmstr.intents import AgentFinished
    from pptmstr.model import AgentState

    session.teardown_requested = True
    session._settle_subagents_at_run_end("its session was cancelled without being closed")
    run.bridge.emit(AgentFinished(session.node_id, AgentState.DONE, time.monotonic()))
    run.frame()


def test_a_session_relaunched_in_the_same_window_carries_on_from_its_own_table(
    tmp_path: Path,
) -> None:
    """
    No restart: the store still holds the first run's builder, settled when
    that run ended, and once the session is announced again the board calls it
    ``builder, from an earlier run``. The relaunched session is a new driver,
    and one that began an empty table would call its first builder ``builder``
    -- the earlier agent's name -- while the board called the new one
    ``builder-2``.
    """
    from pptmstr import board
    from pptmstr.model import AgentState

    with _Process(tmp_path) as run:
        run.launch()
        run.frame()
        session_id = run.session_id
        first = _start_builder(run, session_id, "agent-a")
        assert _named_alike(run, session_id) == {first: "builder"}
        earlier = run.sessions[(session_id, None)]

        _end_run_for_a_relaunch(run, earlier)
        ended = run.state.store.snapshot()
        assert ended.nodes[first].state is AgentState.CANCELLED
        # Ended, and still of the run the store has for the session: nothing is
        # of an earlier run until the session is announced again.
        assert board.role_name(ended, first, session_id) == "builder"

        run.launch(resume=session_id)
        run.frame()
        relaunched = run.sessions[(session_id, None)]
        assert relaunched is not earlier and run.state.ran[session_id] is relaunched
        assert relaunched._restored == {"builder": "agent-a"}
        second = _start_builder(run, session_id, "agent-b")
        snap = run.state.store.snapshot()

        assert (
            relaunched.role_of(second) == board.role_name(snap, second, session_id) == "builder-2"
        )
        assert relaunched.resolve_role("builder-2") == second
        gone = f"builder, {BEFORE_RESTART}"
        assert snap.of_earlier_run(first) and not snap.of_earlier_run(second)
        assert relaunched.role_of(first) == board.role_name(snap, first, session_id) == gone
        assert relaunched.resolve_role("builder") is None and relaunched.resolve_role(gone) is None

        # Woken by the relaunched run under the id it had: the same name again,
        # and the qualifier comes off.
        _start_builder(run, session_id, "agent-a")
        assert _named_alike(run, session_id) == {first: "builder", second: "builder-2"}

        # And a third run starts from the second's table, not the first's.
        _end_run_for_a_relaunch(run, relaunched)
        run.launch(resume=session_id)
        assert run.sessions[(session_id, None)]._restored == {
            "builder": "agent-a",
            "builder-2": "agent-b",
        }


def test_a_launch_the_pool_refused_leaves_no_table_behind(tmp_path: Path) -> None:
    with _Process(tmp_path, takes=False) as run:
        run.launch()

        assert run.state.ran == {}


def test_a_session_whose_board_log_could_not_be_opened_is_reported_as_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The pool has taken the session by the time its log is opened. An exception
    there is a session running unrecorded, and reporting it as a launch that
    failed before it started would hand the operator their draft back beside a
    row that is working.
    """
    from pptmstr import app

    def raises(*_a: object) -> None:
        raise OSError("no such device")

    monkeypatch.setattr(app, "_open_board_log", raises)
    mark = LOG.snapshot()[1]
    with _Process(tmp_path, _a_working_team) as run:
        run.launch()
        [launch] = run.state.launches
        outcome = launch.future.result(timeout=5.0)
        run.frame()
        session_id = run.session_id
        log = run.log()
        tasks = [t for t in run.state.store.snapshot().tasks.values() if t.belongs_to(session_id)]

    assert isinstance(outcome, app.LaunchStarted) and app._why_not_started(launch.future) is None
    assert len(tasks) == 2
    assert log is not None and not log.recording and not log.awaiting
    assert log.why_not == "its log could not be opened (OSError: no such device)"
    entries, revision = LOG.snapshot()
    lines = [e.text for e in entries[len(entries) - (revision - mark) :]]
    assert not any("failed before it started" in line for line in lines)
    assert any("is running unrecorded" in line and session_id in line for line in lines)
    assert not (tmp_path / session_id).exists()


def test_quitting_closes_the_writer_before_the_pool_and_logs_what_did_not_land(
    tmp_path: Path,
) -> None:
    from pptmstr import app, boardlog, boardsync

    order: list[str] = []
    record = boardlog.RecordingStopped(at=0.0, reason="x")

    class _Writer:
        def close(self) -> tuple[boardsync.Report, ...]:
            order.append("writer")
            return (
                boardsync.Outcome("s-1", record, boardlog.Unwritten("still queued; not written")),
            )

    class _Pool:
        shutdown_bound_s = 1.0

        async def shutdown(self) -> tuple[()]:
            return ()

    class _Bridge(_QuittingBridge):
        def submit(self, coro):  # type: ignore[no-untyped-def]
            order.append("pool")
            return super().submit(coro)

    bridge = _Bridge(None)
    state = app.AppState(store=Store(), bridge=bridge, settings=Settings())  # type: ignore[arg-type]
    state.pool = _Pool()  # type: ignore[assignment]
    state.board_writer = _Writer()  # type: ignore[assignment]
    mark = LOG.snapshot()[1]
    app._shut_down(state)

    assert order == ["writer", "pool"] and bridge.stopped == 1
    entries, revision = LOG.snapshot()
    said = [e.text for e in entries[len(entries) - (revision - mark) :] if e.source == "board"]
    assert len(said) == 1 and "s-1" in said[0] and "still queued" in said[0]


def test_a_writer_that_will_not_close_does_not_stop_the_pool_being_shut_down() -> None:
    from pptmstr import app

    class _Writer:
        def close(self) -> tuple[()]:
            raise RuntimeError("wedged")

    bridge, _ = _quit(None)
    state = app.AppState(store=Store(), bridge=bridge, settings=Settings())  # type: ignore[arg-type]
    from pptmstr.pool import SessionPool

    state.pool = SessionPool(bridge, shutdown_grace_s=7.0, shutdown_reap_s=2.0)  # type: ignore[arg-type]
    state.board_writer = _Writer()  # type: ignore[assignment]
    waited = len(bridge.waited)
    app._shut_down(state)
    assert len(bridge.waited) == waited + 1 and bridge.stopped == 2


def test_a_session_minted_by_this_launch_is_adopted_and_not_left_to_a_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    How "new" is known: the id was made for this launch, so no log can be under
    it. The writer would otherwise decide by listing the directory, and a listing
    that fails for one moment refuses the session for the life of the process.
    """
    from pptmstr import boardlog, boardsync

    monkeypatch.setattr(
        boardlog, "next_ordinal", lambda directory: boardlog.Unlisted("EIO for one moment")
    )
    with _Process(tmp_path, _a_working_team) as run:
        run.launch()
        run.frame()
        log, session_id = run.log(), run.session_id

    assert log.recording and not log.stopped and log.unkept == 0
    assert isinstance(boardsync.load(session_id, tmp_path), boardsync.Board)


def test_relaunching_a_session_whose_recording_stopped_does_not_call_it_recorded_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The writer never restarts a stopped session, so an entry rebuilt at the
    relaunch would say "recorded" over a log nothing is being added to.
    """
    from pptmstr import boardlog

    monkeypatch.setattr(
        boardlog, "append", lambda directory, record, **kw: boardlog.Unwritten("disk full")
    )
    said = iter([_a_working_team, _one_more_task()])
    with _Process(tmp_path, lambda session: next(said)(session)) as run:
        run.launch()
        run.frame()
        session_id = run.session_id
        stopped = run.log(session_id)
        run.launch(resume=session_id)
        run.frame()
        after = run.log(session_id)

    assert stopped.stopped and not stopped.recording
    assert after.stopped and not after.recording and "disk full" in after.why_not


def test_a_log_that_cannot_be_read_starts_the_session_unrecorded_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The launch is worth more than its log: the session starts, nothing is
    restored, nothing is appended, and the pane gives the reason.
    """
    from pptmstr import boardsync
    from pptmstr.ui import board_pane

    session_id, _ = _first_run(tmp_path)
    directory = tmp_path / session_id / "board"
    before = sorted(p.name for p in directory.iterdir())

    def unreadable(sid: str, root: Path | None = None, legacy: Path | None = None) -> object:
        raise PermissionError("boards is not yours")

    monkeypatch.setattr(boardsync, "find", unreadable)
    with _Process(tmp_path, _one_more_task()) as second:
        second.launch(resume=session_id)
        second.frame()
        started = (session_id, None) in second.sessions
        declared = "t-3" in second.state.store.snapshot().tasks
        log = second.log(session_id)

    assert started and declared
    assert not log.recording and "boards is not yours" in log.why_not
    assert sorted(p.name for p in directory.iterdir()) == before
    line = board_pane.recording_line(None, log)
    assert line is not None and "UNRECORDED" in line.text


def test_a_session_whose_id_is_no_directory_name_is_unrecorded_from_the_start(
    tmp_path: Path,
) -> None:
    with _Process(tmp_path, _one_more_task("t-1")) as run:
        run.launch(resume="..", template="solo")
        run.frame()
        log = run.log("..")

    assert not log.recording and "not one directory name" in log.why_not
    assert list(tmp_path.iterdir()) == []


def test_a_restored_session_resumed_again_after_one_more_step_is_not_read_back_or_refused(
    tmp_path: Path,
) -> None:
    """
    Restore, one more intent applied, resume again in the same process. The
    store is now ahead of whatever the disk held when it was read, and a second
    reading would differ from it by that step -- or by a record still queued --
    which the store refuses whole. So there is no second reading: one restore
    is applied, it stays adoptable, and all three runs are one history on disk.
    """
    from pptmstr import boardsync
    from pptmstr.intents import BoardRestored

    session_id, _ = _first_run(tmp_path)
    said = iter([_one_more_task("t-3"), _one_more_task("t-4")])
    with _Process(tmp_path, lambda session: next(said)(session)) as second:
        second.launch(resume=session_id)
        second.frame()
        second.launch(resume=session_id)
        second.frame()
        report = second.state.store.snapshot().restores[session_id]
        log = second.log(session_id)
        restores = [i for i in second.applied if isinstance(i, BoardRestored)]
        relaunched = second.sessions[(session_id, None)]

    assert len(restores) == 1
    # Read back once, and the run that did not read it is still built with what
    # the log said: its first builder is not the earlier process's ``builder``.
    assert relaunched._address_for("agent-new", "builder") == "builder-2"
    assert report.adoptable and not report.refused
    assert log.recording and log.unkept == 0
    board = boardsync.load(session_id, tmp_path)
    assert isinstance(board, boardsync.Board)
    assert [t.id for t in board.tasks] == ["t-1", "t-2", "t-3", "t-4"] and not board.short


# -- the decision drafts ------------------------------------------------------------


def test_the_inbox_and_detail_are_handed_the_same_decision_drafts_every_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The wiring. The answer box is typed at the inbox row and DETAIL reports the
    draft: two objects, or a fresh one per frame, is a box that forgets what was
    typed and a pane that says nothing was.
    """
    from unittest.mock import MagicMock

    from pptmstr import app
    from pptmstr.ui import detail, inbox

    handed: list[tuple[str, object]] = []
    monkeypatch.setattr(inbox, "draw", lambda *a, **kw: handed.append(("inbox", kw["decisions"])))
    monkeypatch.setattr(detail, "draw", lambda *a, **kw: handed.append(("detail", a[5])))
    # Both panes report how each draw came out against ImGui's frame count
    # (``app._reviewing``), and there is no ImGui context here to count frames.
    monkeypatch.setattr(app.imgui, "get_frame_count", lambda: 1)

    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    state.pool = MagicMock()
    state.frame_snap = state.store.snapshot()
    panels = app._panels(state)
    for _ in range(2):
        panels["NEEDS YOU"]()
        panels["DETAIL"]()

    assert [name for name, _ in handed] == ["inbox", "detail", "inbox", "detail"]
    assert all(drafts is state.decisions for _, drafts in handed)


# -- the layout keys and the hold ---------------------------------------------------


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


class _KeyFrames:
    """
    ``app._handle_keys`` and one text box, in frames of a real Dear ImGui context.

    The box is drawn while ``box`` is set and takes the keyboard on the first frame
    it is drawn, so clearing ``box`` is a text box removed from under its typist.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, shown: str) -> None:
        from unittest.mock import MagicMock

        from pptmstr import app

        self._app = app
        self.layouts = _Layouts(shown)
        monkeypatch.setattr(app, "hello_imgui", self.layouts)
        self.state = app.AppState(store=Store(), bridge=MagicMock(), settings=Settings())
        self.state.frame_snap = self.state.store.snapshot()
        self.box = False
        self._focused = False

    def __enter__(self) -> _KeyFrames:
        from imgui_bundle import imgui

        self._imgui = imgui
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(800.0, 600.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        self.frame(2)
        return self

    def __exit__(self, *exc: object) -> None:
        self._imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        imgui = self._imgui
        for _ in range(count):
            imgui.new_frame()
            self._app._handle_keys(self.state)
            imgui.begin("pane")
            if self.box:
                if not self._focused:
                    imgui.set_keyboard_focus_here()
                    self._focused = True
                imgui.input_text("##box", "")
            else:
                self._focused = False
            imgui.end()
            imgui.end_frame()

    def key(self, key: object, char: str = "") -> None:
        self.io.add_key_event(key, True)  # type: ignore[arg-type]
        if char:
            self.io.add_input_characters_utf8(char)
        self.frame()
        self.io.add_key_event(key, False)  # type: ignore[arg-type]
        self.frame()

    def type_then_lose_the_box(self) -> None:
        self.box = True
        self.frame(3)
        assert self.io.want_text_input, "the box did take the keyboard"
        self.io.add_input_characters_utf8("yes, but")
        self.frame(2)
        self.box = False
        self.frame(3)
        assert not self.io.want_capture_keyboard, "the keyboard is free again"
        assert self.state.review.hold.held


def _layout_key(name: str) -> object:
    from imgui_bundle import imgui

    return {"tab": imgui.Key.tab, "enter": imgui.Key.enter, "escape": imgui.Key.escape}[name]


LAYOUT_KEYS = [
    ("TRIAGE", "enter", "FOCUS"),
    ("FOCUS", "tab", "TRIAGE"),
    ("FOCUS", "escape", "TRIAGE"),
]


@pytest.mark.parametrize(("shown", "key", "to"), LAYOUT_KEYS)
def test_the_harness_presses_layout_keys_the_handler_can_see(
    monkeypatch: pytest.MonkeyPatch, shown: str, key: str, to: str
) -> None:
    """Without this every 'the layout stayed' below could be a key never sent."""
    with _KeyFrames(monkeypatch, shown) as frames:
        frames.key(_layout_key(key))

        assert frames.layouts.switched == [to]


@pytest.mark.parametrize(("shown", "key", "to"), LAYOUT_KEYS)
def test_a_key_typed_after_a_text_box_vanishes_does_not_switch_layout(
    monkeypatch: pytest.MonkeyPatch, shown: str, key: str, to: str
) -> None:
    """
    The box went while the operator was mid-sentence. Enter and Tab are the next
    keys of that sentence; Escape is the operator saying they have stopped, and
    the frame that hears it acts on nothing.
    """
    with _KeyFrames(monkeypatch, shown) as frames:
        frames.type_then_lose_the_box()

        frames.key(_layout_key(key))

        assert frames.layouts.switched == []


@pytest.mark.parametrize(("shown", "key", "to"), LAYOUT_KEYS)
def test_the_layout_keys_answer_again_once_escape_has_ended_the_hold(
    monkeypatch: pytest.MonkeyPatch, shown: str, key: str, to: str
) -> None:
    with _KeyFrames(monkeypatch, shown) as frames:
        frames.type_then_lose_the_box()
        frames.key(_layout_key("escape"))
        assert frames.layouts.switched == [] and not frames.state.review.hold.held

        frames.key(_layout_key(key))

        assert frames.layouts.switched == [to]


def test_a_key_typed_into_a_box_that_is_still_there_does_not_switch_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _KeyFrames(monkeypatch, "TRIAGE") as frames:
        frames.box = True
        frames.frame(3)

        frames.key(_layout_key("enter"))

        assert frames.layouts.switched == []


def test_the_frame_reads_its_bare_keys_through_the_one_function_that_asks_the_hold() -> None:
    """
    ``begin_frame`` needs a GL context and is not run here, so the wiring is read
    off its source: a second call to either handler from the frame would be a
    layout key the hold was never asked about, or a hold stepped twice.
    """
    import inspect

    from pptmstr import app

    source = inspect.getsource(app.begin_frame)
    assert "_handle_keys(state)" in source
    assert "_handle_layout_keys" not in source and "review.handle_keys" not in source


# -- the DIGEST pane, docked with BOARD ---------------------------------------------
#
# Read off the docking parameters and the ini text. Whether hello_imgui then puts the
# tab where these say is its behaviour: ``scripts/verify_new_pane_keeps_layout.py``.


def _stub_layouts() -> list[object]:
    from pptmstr import app

    panels = {
        label: (lambda: None)
        for label in (
            "FLEET",
            "NEEDS YOU",
            "CONTEXT",
            "DETAIL",
            "BOARD",
            "DIGEST",
            "BRIEF",
            "SESSION",
            "HEALTH",
            "LOG",
        )
    }
    return [app._triage_layout(panels), app._focus_layout(panels)]


def test_both_layouts_dock_digest_in_boards_space_as_the_tab_after_it() -> None:
    layouts = _stub_layouts()

    assert [layout.layout_name for layout in layouts] == ["TRIAGE", "FOCUS"]  # type: ignore[attr-defined]
    for layout in layouts:
        windows = [(w.label, w.dock_space_name) for w in layout.dockable_windows]  # type: ignore[attr-defined]
        labels = [label for label, _ in windows]
        assert labels.count("DIGEST") == 1 and labels.count("BOARD") == 1
        at = labels.index("BOARD")
        assert labels[at + 1] == "DIGEST", layout.layout_name  # type: ignore[attr-defined]
        assert windows[at + 1][1] == windows[at][1] != ""


def test_the_digest_tab_draws_the_digest_pane_for_the_frames_snapshot_and_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The wiring: the function registered under DIGEST is the pane's draw, handed
    the frame's one snapshot, the one cursor, the frame's clock, and the inbox's
    own drafts and command rows for the history it draws; it draws nothing
    before a snapshot exists. The board tab is handed no digest to draw.
    """
    from pptmstr import app
    from pptmstr.ui import board_pane, digest_pane

    handed: list[tuple[object, ...]] = []
    monkeypatch.setattr(digest_pane, "draw", lambda *a: handed.append(("digest", *a)))
    monkeypatch.setattr(board_pane, "draw", lambda *a: handed.append(("board", *a)))

    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    panels = app._panels(state)
    panels["DIGEST"]()
    assert handed == []

    state.frame_snap = state.store.snapshot()
    panels["DIGEST"]()
    assert handed == [
        ("digest", state.frame_snap, state.focus, state.frame_now, state.decisions, None)
    ]
    assert handed[0][4] is state.decisions
    panels["BOARD"]()
    assert handed[-1][0] == "board" and len(handed) == 2


def test_main_gives_a_saved_layout_its_digest_entry_before_the_runner_reads_the_file() -> None:
    """
    Read off ``main``'s source, which opens a window and cannot be run here. The
    entry has to be in the file before ``immapp.run`` loads it, and against the
    file the runner will load and not a path spelled a second time.
    """
    import inspect

    from pptmstr import app

    source = inspect.getsource(app.main)
    located = source.index("ini = hello_imgui.ini_settings_location(runner)")
    kept = source.index("_keep_saved_layouts(Path(ini))")
    assert source.index("runner.ini_folder_type =") < located < kept < source.index("immapp.run(")
    assert inspect.getsource(app).count("_keep_saved_layouts(") == 2


def test_a_layout_saved_before_digest_existed_gets_it_in_boards_node(tmp_path: Path) -> None:
    from pptmstr import app
    from tests.test_layout_ini import FOCUS_ENTRY, SAVED, TRIAGE_ENTRY

    ini = tmp_path / "pptmstr.ini"
    ini.write_bytes(SAVED.encode())
    mark = LOG.snapshot()[1]
    app._keep_saved_layouts(ini)

    text = ini.read_bytes().decode()
    assert TRIAGE_ENTRY in text and FOCUS_ENTRY in text
    assert any(
        "DIGEST added beside BOARD in 2 saved layout(s)" in line for _, line in _app_log_since(mark)
    )

    # The next launch has nothing to add and says nothing.
    mark = LOG.snapshot()[1]
    app._keep_saved_layouts(ini)
    assert ini.read_bytes().decode() == text
    assert not any("DIGEST" in line for _, line in _app_log_since(mark))


def test_a_saved_layout_that_cannot_be_rewritten_is_said_and_the_app_still_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptmstr import app, layout_ini
    from tests.test_layout_ini import SAVED

    ini = tmp_path / "pptmstr.ini"
    ini.write_bytes(SAVED.encode())

    def refuse(*args: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(layout_ini.os, "replace", refuse)
    mark = LOG.snapshot()[1]
    app._keep_saved_layouts(ini)

    assert ini.read_bytes().decode() == SAVED
    assert any(
        "could not be given an entry for DIGEST" in line and "PermissionError" in line
        for _, line in _app_log_since(mark)
    )


# -- the persisted fleet --------------------------------------------------------------
#
# Real here: ``Bridge``, ``Store``, ``FleetWriter``, ``BoardWriter``, ``sessionfile``
# and the files they make under the test's own configuration directory, and
# ``app._start`` / ``app._launch`` / ``app.apply_intents``. Not real: the pool's run
# (``_Process``) and the lister of the CLI's transcripts, which is handed in. The
# default lister reads the operator's real ``~/.claude/projects``, so no ``_start``
# in this file is called without one unless it is the fake driver's, which lists
# nothing.


def _fleet_log_since(mark: int, *sources: str) -> list[tuple[Level, str]]:
    entries, revision = LOG.snapshot()
    recent = entries[len(entries) - (revision - mark) :] if revision > mark else []
    return [(e.level, e.text) for e in recent if e.source in (sources or ("fleet", "board"))]


def _row(session_id: str, cwd: str = "/tmp") -> object:
    from pptmstr.sessions import SessionRow

    return SessionRow(
        session_id=session_id,
        cwd=cwd,
        git_branch=None,
        summary="build the thing",
        first_prompt=None,
        created_at=None,
        last_modified=0,
        tag=None,
    )


def _goes_live(run: _Process, session_id: str | None = None) -> None:
    """
    The root leaves SPAWNING, which the driver says only with a connected
    client, and the session says its run has started (``AgentSession.run`` does
    both when the opening text has been handed over).
    """
    from pptmstr.intents import StateChanged
    from pptmstr.model import AgentState

    node = (session_id or run.session_id, None)
    run.bridge.emit(StateChanged(node, AgentState.THINKING))
    run.sessions[node]._say_started()
    run.frame()


def _ends(run: _Process, session_id: str | None = None) -> None:
    import time

    from pptmstr.intents import AgentFinished
    from pptmstr.model import AgentState

    run.running.discard(session_id or run.session_id)
    run.bridge.emit(
        AgentFinished((session_id or run.session_id, None), AgentState.DONE, time.monotonic())
    )
    run.frame()


def _ran_and_closed(says: object = _a_working_team) -> str:
    """
    'Process one': a team session launched, gone live, worked and closed, with a
    board writer and a fleet writer on the configuration directory's own roots.
    Reads nothing back.
    """
    from pptmstr import sessionfile

    with _Process(sessionfile.root(), None) as first:
        first.launch()
        first.frame()
        session_id = first.session_id
        _goes_live(first)
        for intent in says(first.sessions[(session_id, None)]):  # type: ignore[operator]
            first.bridge.emit(intent)
        first.frame()
        _ends(first)
    return session_id


def _started(monkeypatch: pytest.MonkeyPatch, lister: object, **args: object):  # type: ignore[no-untyped-def]
    """
    'Process two' as far as ``app._start`` takes it, with the recall worker's
    answer in and its cards applied.
    """
    import time

    from pptmstr import app
    from pptmstr.settings import Loaded

    state = app._start(_args(**args), Loaded(Settings()), list_sessions=lister)  # type: ignore[arg-type]
    deadline = time.monotonic() + 5.0
    while state.fleet is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.fleet is not None
    _frames(state)
    return state


def _frames(state: object, count: int = 2) -> None:
    import time

    from pptmstr import app

    for _ in range(count):
        state.frame_now = time.monotonic()  # type: ignore[attr-defined]
        app.apply_intents(state)  # type: ignore[arg-type]
        state.frame_snap = state.store.snapshot()  # type: ignore[attr-defined]


def _tree(base: Path) -> dict[str, bytes | str | None]:
    """Every path under ``base``, with a file's bytes and a link's target."""
    import os

    found: dict[str, bytes | str | None] = {}
    for path in sorted(base.rglob("*")):
        name = str(path.relative_to(base))
        if path.is_symlink():
            found[name] = os.readlink(path)
        else:
            found[name] = path.read_bytes() if path.is_file() else None
    return found


def test_a_session_gets_a_file_only_once_it_is_live_and_the_file_says_what_it_ran_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The writes, in the order a session makes them true. Announced and taken by
    the pool is not a file: a launch that never connects leaves nothing. Live is.
    What the store is not told -- the message, the rung, the containment, the cap
    -- is in it beside what the announce carried.
    """
    from pptmstr import sessionfile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(sessionfile.root(), None) as run:
        run.launch()
        run.frame()
        run.frame()
        session_id = run.session_id
        file = sessionfile.file_path(session_id)
        assert file is not None and not file.exists()
        assert not sessionfile.root().exists()

        _goes_live(run)
        live = sessionfile.read(session_id)
        assert isinstance(live, sessionfile.SessionFile)
        assert live.in_fleet and live.ended_how is None and live.ended_at is None
        assert live.task == "build the thing" and live.last_message == "build the thing"
        assert live.policy is Policy.STRICT and live.contained is False
        assert live.subagent_cap == Settings().subagent_cap
        assert live.cwd == "/tmp" and live.template == "feature"
        assert live.created_at is not None and live.launched_at is not None
        assert run.state.fleet_writer.last_active(session_id) == live.last_active

        _ends(run)
        ended = sessionfile.read(session_id)
    assert isinstance(ended, sessionfile.SessionFile)
    assert ended.ended_how == "closed" and ended.ended_at is not None and ended.in_fleet


def test_the_writer_is_told_of_a_session_before_the_pool_is_handed_it(tmp_path: Path) -> None:
    """
    The pool announces, and the announce is applied on another thread: a change
    derived from it for a session the writer has not been told of is dropped
    without a word. So the telling is ahead of the hand-over, asked here at the
    moment of the hand-over.
    """
    from pptmstr import fleetsync

    with _Process(tmp_path, None) as run:
        pool, told = run.state.pool, []
        real = pool.submit  # type: ignore[union-attr]

        def asking(session: AgentSession) -> bool:
            told.append(run.state.fleet_writer.submit(fleetsync.Change(session.session_id)))
            return real(session)

        pool.submit = asking  # type: ignore[union-attr,method-assign]
        run.launch()
    assert told and told[0] != 0


def test_a_session_run_here_comes_back_after_a_restart_as_a_closed_card_and_nothing_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The property, across a restart. The second process has a card for the
    session and that is all it has: no session in the pool, no entry for its
    board log, no board in the store, its log neither listed nor read, and its
    session file byte for byte what the first process left -- a recall is not an
    end, and a card that rewrote its own file would read "just now" at every start.
    """
    from pptmstr import boardlog, boardsync, sessionfile
    from pptmstr.model import AgentState, EndedHow, TranscriptPresence

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id = _ran_and_closed()
    file = sessionfile.file_path(session_id)
    board = sessionfile.board_dir(session_id)
    assert file is not None and board is not None
    assert len(list(board.iterdir())) >= 6
    before = _tree(tmp_path)

    def unread(*a: object, **k: object) -> object:
        raise AssertionError("a closed card's board log was read")

    for module, name in ((boardsync, "find"), (boardsync, "load"), (boardlog, "read")):
        monkeypatch.setattr(module, name, unread)
    monkeypatch.setattr(boardlog, "next_ordinal", unread)

    state = _started(monkeypatch, lambda: [_row(session_id)])
    _frames(state, 3)
    try:
        snap = state.store.snapshot()
        card = snap.nodes[(session_id, None)]
        assert card.recalled is not None and card.state is AgentState.DONE
        assert card.recalled.transcript is TranscriptPresence.PRESENT
        assert card.recalled.ended_how is EndedHow.CLOSED
        assert card.task == "build the thing" and card.template == "feature"
        assert list(snap.nodes) == [(session_id, None)]

        assert state.pool.sessions == {} and state.pool.running_count == 0
        assert state.pool.queued_count == 0 and state.ran == {}
        assert state.board_logs == {} and state.restored_addresses == {}
        assert snap.restores == {} and not snap.tasks and not snap.concerns
        assert state.launches == []
        assert state.fleet_writer.outstanding == 0
        assert state.fleet_writer.last_active(session_id) is None
        assert state.closed_paths[session_id].endswith(f"{session_id}.jsonl")
        assert state.fleet.sessions[0].session_id == session_id
    finally:
        state.fleet_writer.close()
        state.board_writer.close()
    assert _tree(tmp_path) == before


def test_resuming_a_closed_card_reads_its_log_then_and_goes_on_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    What the card must not have spoiled. An entry in ``board_logs`` for a
    recalled session would make the resume skip the read and start the team on
    an empty board the writer then appends behind.
    """
    from pptmstr import boardsync, sessionfile
    from pptmstr.intents import BoardRestored

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id = _ran_and_closed()
    state = _started(monkeypatch, lambda: [_row(session_id)])
    assert state.store.snapshot().nodes[(session_id, None)].recalled is not None

    with _Process(None, _one_more_task(), state=state) as second:
        second.launch(resume=session_id)
        second.frame()
        _goes_live(second, session_id)
        snap = state.store.snapshot()
        log = second.log(session_id)
        restores = [i for i in second.applied if isinstance(i, BoardRestored)]

    assert snap.nodes[(session_id, None)].recalled is None
    assert {t.id for t in snap.tasks.values()} == {"t-1", "t-2", "t-3"}
    assert len(restores) == 1 and log.recording and not log.awaiting
    board = boardsync.load(session_id)
    assert isinstance(board, boardsync.Board)
    assert [t.id for t in board.tasks] == ["t-1", "t-2", "t-3"] and not board.short
    again = sessionfile.read(session_id)
    assert isinstance(again, sessionfile.SessionFile)
    assert again.in_fleet and again.ended_how is None and again.ended_at is None


def test_under_the_fake_driver_nothing_is_moved_recalled_or_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A file written for a scripted session would come back at the next real start
    as a card for a session the CLI never had. So: no move of the board logs, no
    recall worker, no card, and a session taken all the way to its end leaves the
    configuration directory exactly as it was -- while "last active" still
    answers, because that is asked of the writer and not of a file.
    """
    import shutil

    from pptmstr import app, boardsync, fleetsync, sessionfile
    from pptmstr.settings import Loaded

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    earlier = _ran_and_closed()
    old = boardsync.legacy_root() / "an-old-log"
    shutil.copytree(sessionfile.board_dir(earlier), old)  # type: ignore[arg-type]
    before = _tree(tmp_path)

    def never(*a: object, **k: object) -> object:
        raise AssertionError("the fake driver's start touched the fleet on disk")

    monkeypatch.setattr(boardsync, "migrate", never)
    monkeypatch.setattr(fleetsync, "recall", never)
    state = app._start(_args(fake=True), Loaded(Settings()))  # type: ignore[arg-type]

    assert state.fake and state.board_writer is None and state.migration is None
    assert not [t for t in threading.enumerate() if t.name == "fleet-recall"]
    with _Process(None, _a_working_team, state=state) as run:
        run.launch()
        run.frame()
        scripted = run.session_id
        _goes_live(run)
        _ends(run)
        app._close_fleet(state)
        seen = state.fleet_writer.last_active(scripted)
        nodes = state.store.snapshot().nodes

    assert state.fleet is None and (earlier, None) not in nodes
    assert seen is not None
    assert _tree(tmp_path) == before


def test_a_session_file_is_written_with_board_logging_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The observer is built whether or not a board is recorded: with no board
    writer there was once no observer at all.
    """
    from pptmstr import sessionfile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None, settings=Settings(board_log=False)) as run:
        assert run.state.board_writer is None
        run.launch()
        run.frame()
        _goes_live(run)
        _ends(run)
        found = sessionfile.read(run.session_id)
    assert isinstance(found, sessionfile.SessionFile) and found.ended_how == "closed"


def test_a_session_file_change_that_cannot_be_derived_loses_neither_the_batch_nor_the_board_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The fleet half of the observer has its own handler. Sharing the board
    half's would end every board log for the run over a session file; raising
    would make ``Store.apply_all`` abandon a batch that is already out of the queue.
    """
    from pptmstr import boardsync, fleetsync
    from pptmstr.intents import TaskDeclared
    from pptmstr.model import Task

    with _Process(tmp_path, _a_working_team) as run:
        run.launch()
        run.frame()
        session_id = run.session_id

        def broken(*args: object) -> tuple[()]:
            raise RuntimeError("no arm for this")

        monkeypatch.setattr(fleetsync, "changes", broken)
        mark = LOG.snapshot()[1]
        for task_id in ("t-3", "t-4"):
            run.bridge.emit(
                TaskDeclared(Task(id=task_id, title="later", declared_at=0.0), (session_id, None))
            )
            run.frame()
        held = set(run.state.store.snapshot().tasks)
        log = run.log(session_id)

    assert {"t-3", "t-4"} <= held
    assert log.recording and not log.stopped
    board = boardsync.load(session_id, tmp_path)
    assert isinstance(board, boardsync.Board) and not board.short
    assert [t.id for t in board.tasks] == ["t-1", "t-2", "t-3", "t-4"]
    errors = [text for level, text in _fleet_log_since(mark, "fleet") if level is Level.ERROR]
    assert len(errors) == 1 and "no arm for this" in errors[0]


def test_a_board_fact_that_cannot_be_derived_does_not_stop_the_session_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptmstr import boardfacts, sessionfile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(sessionfile.root(), None) as run:
        run.launch()
        run.frame()

        def broken(*args: object) -> tuple[()]:
            raise RuntimeError("no case for this")

        monkeypatch.setattr(boardfacts, "outcomes", broken)
        _goes_live(run)
        run.frame()
        log = run.log()
        found = sessionfile.read(run.session_id)

    assert log.stopped and not log.recording
    assert isinstance(found, sessionfile.SessionFile) and found.in_fleet


def test_no_session_file_is_read_or_written_on_the_thread_that_draws_and_the_move_runs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Every read, write and listing of a session file across a run, a restart and
    a resume, with the thread it ran on. None is the thread that applies the
    frames -- here the test's own. The move of the board logs is the one piece
    of startup IO that is on it, by design, and it happens inside ``_start`` and
    never again.
    """
    from pptmstr import boardsync, sessionfile

    seen: list[tuple[str, threading.Thread]] = []
    for module, name in (
        (sessionfile, "update"),
        (sessionfile, "read"),
        (sessionfile, "listing"),
        (boardsync, "migrate"),
    ):
        real = getattr(module, name)

        def noting(*args: object, _real=real, _name=name, **kwargs: object):  # type: ignore[no-untyped-def]
            seen.append((_name, threading.current_thread()))
            return _real(*args, **kwargs)

        monkeypatch.setattr(module, name, noting)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id = _ran_and_closed()
    before_start = len(seen)
    state = _started(monkeypatch, lambda: [_row(session_id)])
    with _Process(None, _one_more_task(), state=state) as second:
        loop_thread = second.bridge._thread
        second.launch(resume=session_id)
        second.frame()
        _goes_live(second, session_id)
        _ends(second, session_id)

    frame_thread = threading.current_thread()
    names = [name for name, _ in seen]
    assert {"update", "read", "listing", "migrate"} <= set(names)
    assert all(t is not frame_thread for name, t in seen if name != "migrate")
    assert all(t is not loop_thread for name, t in seen if name != "migrate")
    assert names.count("migrate") == 1 and names.index("migrate") == before_start


def _two_histories_and_a_moved_log(tmp_path: Path) -> tuple[str, str]:
    """
    Two sessions' logs as an older build left them under ``boards/``, one of
    which also has a log under ``sessions/``: the ids of the one that will move
    and the one that has two histories.
    """
    import shutil

    from pptmstr import boardsync, sessionfile

    scratch = tmp_path / "made"
    moved, _ = _first_run(scratch)
    twice, _ = _first_run(scratch)
    legacy = boardsync.legacy_root()
    shutil.copytree(scratch / moved / "board", legacy / moved)
    shutil.copytree(scratch / twice / "board", legacy / twice)
    shutil.copytree(scratch / twice / "board", sessionfile.board_dir(twice))  # type: ignore[arg-type]
    return moved, twice


def test_everything_startup_could_not_do_is_said_once_and_stays_on_the_status_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Three shortfalls at one start: a log with two histories, left where it was;
    a session file that cannot be read; a listing of the CLI's transcripts that
    raised. Each is one line in the log however many frames follow, and all
    three are in the one segment the status bar keeps. The log that could be
    moved was, and a link is at its old name.
    """
    from pptmstr import app, boardsync, sessionfile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    moved, twice = _two_histories_and_a_moved_log(tmp_path)
    bad = sessionfile.file_path("s-bad")
    assert bad is not None
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json")

    def raising() -> object:
        raise OSError("projects is not readable")

    mark = LOG.snapshot()[1]
    state = _started(monkeypatch, raising)
    _frames(state, 5)
    said = [text for level, text in _fleet_log_since(mark) if level is not Level.INFO]

    assert len([s for s in said if twice in s and "two places" in s]) == 1
    assert len([s for s in said if "s-bad" in s and "cannot be read" in s]) == 1
    assert len([s for s in said if "projects is not readable" in s]) == 1
    assert len(said) == 3
    assert state.migration is not None and [left.name for left in state.migration.left] == [twice]
    new = sessionfile.board_dir(moved)
    assert new is not None and new.is_dir() and (boardsync.legacy_root() / moved).is_symlink()
    assert (boardsync.legacy_root() / twice).is_dir()
    assert not (boardsync.legacy_root() / twice).is_symlink()
    assert bad.read_text() == "{not json"
    assert app.startup_shortfalls(state.migration, state.fleet) == (
        "at startup: 1 board log in two places, 1 session file unreadable,"
        " transcripts not listed (see LOG)"
    )
    state.fleet_writer.close()
    state.board_writer.close()


def test_the_status_bar_segment_names_each_kind_of_shortfall_and_is_absent_without_one() -> None:
    from pptmstr import app, boardsync, fleetsync

    here = Path("/x")
    assert app.startup_shortfalls(None, None) is None
    assert app.startup_shortfalls(boardsync.Migration(), fleetsync.Fleet()) is None
    clean = boardsync.Migration(
        (boardsync.Moved("a", here, here), boardsync.Linked("b", here, here))
    )
    assert app.startup_shortfalls(clean, fleetsync.Fleet(detached=("d",), killed=("k",))) is None
    migration = boardsync.Migration(
        (
            boardsync.Left("a", here, "EACCES"),
            boardsync.Left("b", here, "EACCES"),
            boardsync.Left("c", here, "already there", twice=True),
            boardsync.Moved("d", here, here, unlinked="EPERM"),
        ),
    )
    fleet = fleetsync.Fleet(
        unreadable=(("s", "bad"), ("t", "bad")), problem="EIO", unlisted="OSError"
    )
    assert app.startup_shortfalls(migration, fleet) == (
        "at startup: 2 board logs left at the old path, 1 board log in two places,"
        " 1 moved board log not linked, 2 session files unreadable,"
        " session files not all read, transcripts not listed (see LOG)"
    )
    assert app.startup_shortfalls(boardsync.Migration(unlisted="EIO"), None) == (
        "at startup: old board logs not listed (see LOG)"
    )


def test_a_session_with_two_histories_is_restored_from_its_own_and_runs_unrecorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``Found.unrecorded``, end to end from the move. The store restores the half
    under ``sessions/`` whole and would let it be adopted; it is not, the pane's
    line names both directories and what the move said, and neither gains a file.
    The log that was moved is resumed and recorded as any other.
    """
    from pptmstr import boardsync, sessionfile
    from pptmstr.ui import board_pane

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    moved, twice = _two_histories_and_a_moved_log(tmp_path)
    state = _started(monkeypatch, tuple)
    before = _tree(tmp_path / "pptmstr")

    with _Process(None, _one_more_task(), state=state) as run:
        run.launch(resume=twice)
        run.frame()
        snap = state.store.snapshot()
        log, report = run.log(twice), snap.restores[twice]
        declared = "t-3" in snap.tasks
        assert _tree(tmp_path / "pptmstr") == before

    # In a window of its own: task ids are fleet-wide, and these two logs were
    # made by the same script.
    again = _started(monkeypatch, tuple)
    with _Process(None, _one_more_task(), state=again) as run:
        run.launch(resume=moved)
        run.frame()
        other = run.log(moved)

    own, old = sessionfile.board_dir(twice), boardsync.legacy_root() / twice
    assert report.adoptable and declared
    assert not log.recording and not log.awaiting and not log.stopped
    assert log.directory == str(own)
    assert str(own) in log.why_not and str(old) in log.why_not and "two places" in log.why_not
    assert "Log left at the old path when pptmstr started" in log.why_not
    line = board_pane.recording_line(None, log)
    assert line is not None and "UNRECORDED" in line.text and str(old) in line.text
    assert other.recording and other.why_not == ""
    assert other.directory == str(sessionfile.board_dir(moved))


def test_a_log_only_at_the_legacy_path_is_restored_from_there_and_never_appended_to(
    tmp_path: Path,
) -> None:
    """
    A rename that failed, or a start with board logging off: the log is where an
    older build left it. The board comes back from it, the session runs
    unrecorded with the reason, and no record is written at either path.
    """
    import shutil

    session_id, earlier = _first_run(tmp_path / "made")
    sessions, legacy = tmp_path / "sessions", tmp_path / "boards"
    shutil.copytree(tmp_path / "made" / session_id / "board", legacy / session_id)
    before = _tree(legacy)

    with _Process(sessions, _one_more_task(), legacy=legacy) as second:
        second.launch(resume=session_id)
        second.frame()
        second.frame()
        snap = second.state.store.snapshot()
        log, report = second.log(session_id), snap.restores[session_id]
        after = _board_of(second.state.store, session_id)

    restored = {k: v for k, v in after["tasks"].items() if k != "t-3"}  # type: ignore[attr-defined]
    assert restored == earlier["tasks"] and "t-3" in after["tasks"]  # type: ignore[operator]
    assert report.adoptable
    assert not log.recording and not log.awaiting
    assert "legacy path" in log.why_not and str(legacy / session_id) in log.why_not
    assert log.directory == str(sessions / session_id / "board")
    assert _tree(legacy) == before and not sessions.exists()


def _fleet_store() -> Store:
    """
    One session running with a sub-agent, one closed card, and one closed card
    the operator removed.
    """
    from pptmstr.intents import AgentSpawned, SessionDetached, SessionRecalled
    from pptmstr.model import Recalled

    store = Store()
    store.apply_all(
        [
            SessionRecalled("s-closed", "an old task", "m", Recalled()),
            SessionRecalled("s-removed", "an older task", "m", Recalled()),
            SessionDetached("s-removed"),
            AgentSpawned(("s-live", None), None, "now", "m", 1.0),
            AgentSpawned(("s-live", "a1"), ("s-live", None), "sub", "m", 1.0),
        ],
        now=1.0,
    )
    assert store.snapshot().off_fleet("s-removed")
    return store


def test_the_status_bar_counts_what_ran_here_and_the_closed_cards_apart() -> None:
    from pptmstr import app
    from pptmstr.intents import SessionRecalled
    from pptmstr.model import Recalled

    counted = app.fleet_counts(_fleet_store().snapshot())
    assert counted == app.FleetCounts("2 agents · 1 closed", True)

    only_closed = Store()
    only_closed.apply_all([SessionRecalled("s-closed", "t", "m", Recalled())], now=1.0)
    assert app.fleet_counts(only_closed.snapshot()) == app.FleetCounts("0 agents · 1 closed", False)
    assert app.fleet_counts(Store().snapshot()) == app.FleetCounts("0 agents", False)
    source = __import__("inspect").getsource(app._status_bar)
    assert "counted.focusable" in source and "len(snap.nodes)" not in source
    assert "startup_shortfalls(" in source
    for given in ("state.migration", "state.fleet", "state.recall_failed", "shortfalls"):
        assert given in source


def test_a_host_stall_is_not_written_into_a_closed_cards_transcript() -> None:
    from pptmstr import app

    snap = _fleet_store().snapshot()
    assert app._stalled_transcripts(snap) == (snap.nodes[("s-live", None)].transcript,)


def test_a_refused_removal_is_kept_for_the_surface_that_asked_and_never_handed_to_the_bridge() -> (
    None
):
    """
    ``SessionDetachSettled`` answers the operator, and no agent is parked on it.
    The refusal is kept by session; an ask that is then taken drops it.
    """
    import time

    from pptmstr import app, effects
    from pptmstr.intents import AgentFinished, AgentSpawned, SessionDetached
    from pptmstr.model import AgentState, DetachRefusal

    bridge = Bridge()
    handed: list[object] = []
    bridge.settle = lambda effect: handed.append(effect) or False  # type: ignore[method-assign,assignment]
    state = app.AppState(store=Store(), bridge=bridge, settings=Settings())
    root = ("s-1", None)
    bridge.emit(AgentSpawned(root, None, "t", "m", time.monotonic()))
    bridge.emit(SessionDetached("s-1"))
    _frames(state, 1)
    assert state.detach_refused == {"s-1": DetachRefusal.NOT_ENDED}

    bridge.emit(AgentFinished(root, AgentState.DONE, time.monotonic()))
    bridge.emit(SessionDetached("s-1"))
    _frames(state, 1)
    assert state.detach_refused == {} and state.store.snapshot().off_fleet("s-1")
    assert not [e for e in handed if isinstance(e, effects.SessionDetachSettled)]
    state.fleet_writer.close()


def test_a_message_sent_to_a_live_session_is_the_files_last_message_and_no_other_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The composer's send, through the one function both composers are handed.
    Noted only where the pool says a client took the text. Not for a session
    that has not connected, nor for one that has ended, nor -- the case no
    snapshot can see -- for a relaunch held behind a run that is still ending,
    whose row reads live while the pool drops what is typed into it. A resume
    form prefilled with any of those would offer a message the session never had.
    """
    from pptmstr import app, sessionfile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    sent: list[tuple[object, str]] = []
    taken = {"while it was live"}

    async def send(node: object, text: str) -> bool:
        sent.append((node, text))
        return text in taken

    def typed(run: _Process, text: str) -> str | None:
        run.state.frame_snap = run.state.store.snapshot()
        app._send(run.state, (run.session_id, None), text)
        run.bridge.submit(_nothing()).result(timeout=5.0)
        run.frame()
        found = sessionfile.read(run.session_id)
        return found.last_message if isinstance(found, sessionfile.SessionFile) else None

    with _Process(None, None) as run:
        run.state.pool.send = send  # type: ignore[union-attr,method-assign]
        run.launch()
        run.frame()
        typed(run, "before it connected")
        _goes_live(run)
        assert typed(run, "while it was live") == "while it was live"
        assert run.state.last_sent[run.session_id] == "while it was live"
        active = run.state.fleet_writer.last_active(run.session_id)
        # The row still reads live, and the pool dropped the text.
        assert not run.state.store.snapshot().nodes[(run.session_id, None)].state.is_terminal
        assert typed(run, "held behind an ending run") == "while it was live"
        assert run.state.fleet_writer.last_active(run.session_id) == active
        _ends(run)
        assert typed(run, "after it ended") == "while it was live"
        assert run.state.last_sent[run.session_id] == "while it was live"
        final = sessionfile.read(run.session_id)

    assert [text for _, text in sent] == [
        "before it connected",
        "while it was live",
        "held behind an ending run",
        "after it ended",
    ]
    assert isinstance(final, sessionfile.SessionFile) and final.ended_how == "closed"
    assert active is not None and final.last_active is not None and final.last_active >= active
    panels = __import__("inspect").getsource(app._panels)
    assert panels.count("_send(state, ") == 2
    assert "p.send(" not in panels


def test_quitting_says_which_sessions_were_live_and_closes_the_fleet_writer_before_the_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A session the pool ends at quit is not observed ending: no frame applies it.
    So the shell says it, of the roots that had not ended, and waits for the
    write ahead of the pool's shutdown.
    """
    from pptmstr import app, sessionfile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    order: list[str] = []
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        live = run.session_id
        run.sessions.clear()
        run.launch()
        run.frame()
        [other] = [node[0] for node in run.sessions]
        _goes_live(run, other)
        _ends(run, other)

        writer = run.state.fleet_writer
        close = writer.close
        writer.close = lambda *a, **k: (order.append("fleet"), close(*a, **k))[1]  # type: ignore[method-assign]

        class _Pool:
            shutdown_bound_s = 1.0

            async def shutdown(self) -> tuple[()]:
                order.append("pool")
                return ()

        run.state.pool = _Pool()  # type: ignore[assignment]
        app._shut_down(run.state)
        at_quit = list(order)

    quit_live, closed = sessionfile.read(live), sessionfile.read(other)
    assert at_quit == ["fleet", "pool"]
    assert isinstance(quit_live, sessionfile.SessionFile) and quit_live.ended_how == "shutdown"
    assert quit_live.ended_at is not None
    assert isinstance(closed, sessionfile.SessionFile) and closed.ended_how == "closed"


def test_a_fleet_writer_that_will_not_close_does_not_stop_the_pool_being_shut_down() -> None:
    from pptmstr import app

    class _Writer:
        def submit(self, change: object) -> int:
            return 0

        def close(self) -> tuple[()]:
            raise RuntimeError("wedged")

    bridge, _ = _quit(None)
    state = app.AppState(store=Store(), bridge=bridge, settings=Settings())  # type: ignore[arg-type]
    from pptmstr.pool import SessionPool

    state.pool = SessionPool(bridge, shutdown_grace_s=7.0, shutdown_reap_s=2.0)  # type: ignore[arg-type]
    state.fleet_writer = _Writer()  # type: ignore[assignment]
    waited = len(bridge.waited)
    app._shut_down(state)
    assert len(bridge.waited) == waited + 1 and bridge.stopped == 2


def test_the_panes_are_handed_the_frames_wall_clock_the_fold_setting_and_a_closed_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The wiring. The wall clock is the frame's one reading and not the monotonic
    one beside it; the path a closed card's transcript pane draws is looked up
    and not worked out, at both of the pane's call sites.
    """
    import inspect

    from imgui_bundle import imgui

    from pptmstr import app
    from pptmstr.ui import compose, health, rail, transcript_pane
    from pptmstr.ui.focus import Scope

    store = _fleet_store()
    state = app.AppState(
        store=store, bridge=Bridge(), settings=Settings(hide_ended_subagents=False)
    )
    state.frame_snap = store.snapshot()
    state.frame_now, state.frame_wall = 1.0, 2.0
    state.closed_paths = {"s-closed": "/projects/key/s-closed.jsonl"}
    state.focus.to_node(state.frame_snap, ("s-closed", None), scope=Scope.SESSION)
    handed: dict[str, list[object]] = {"rail": [], "health": [], "transcript": []}
    monkeypatch.setattr(rail, "draw", lambda *a, **kw: handed["rail"].append(kw))
    monkeypatch.setattr(health, "draw", lambda *a, **kw: handed["health"].append(kw))
    monkeypatch.setattr(transcript_pane, "draw", lambda *a, **kw: handed["transcript"].append(a))
    monkeypatch.setattr(compose, "draw_conversation", lambda *a, **kw: None)
    monkeypatch.setattr(app.imgui, "begin_child", lambda *a, **kw: True)
    monkeypatch.setattr(app.imgui, "end_child", lambda: None)
    monkeypatch.setattr(app.imgui, "separator", lambda: None)
    monkeypatch.setattr(app.imgui, "get_content_region_avail", lambda: imgui.ImVec2(400.0, 400.0))

    panels = app._panels(state)
    for name in ("FLEET", "HEALTH", "CONTEXT", "SESSION"):
        panels[name]()
    state.settings = state.settings.merged(hide_ended_subagents=True)
    panels["FLEET"]()

    assert [{k: kw[k] for k in ("hide_ended", "now_wall")} for kw in handed["rail"]] == [
        {"hide_ended": False, "now_wall": 2.0},
        {"hide_ended": True, "now_wall": 2.0},
    ]
    # And the menu's New Task, for the fleet's own line (tests/test_fleet_empty.py).
    assert all(callable(kw["new_task"]) for kw in handed["rail"])
    assert handed["health"] == [{"now_wall": 2.0}]
    assert [a[3] for a in handed["transcript"]] == ["/projects/key/s-closed.jsonl"] * 2  # type: ignore[index]
    assert "state.frame_wall = time.time()" in inspect.getsource(app.begin_frame)
    state.fleet_writer.close()


def test_details_is_handed_when_the_writer_last_saw_a_session_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptmstr import app, fleetsync
    from pptmstr.ui import session_details, session_menu

    store = _fleet_store()
    state = app.AppState(store=store, bridge=Bridge(), settings=Settings())
    state.frame_snap = store.snapshot()
    state.fleet_writer.submit(fleetsync.sent("s-live", "hello", 1234.5))
    handed: list[dict[str, object]] = []
    monkeypatch.setattr(session_menu, "draw_confirm", lambda *a, **kw: None)
    monkeypatch.setattr(session_details, "look", lambda *a, **kw: session_details.Looked())
    monkeypatch.setattr(session_details, "view", lambda *a, **kw: handed.append(kw))
    monkeypatch.setattr(session_details, "draw", lambda *a, **kw: None)

    for node in (("s-live", None), ("s-live", "a1"), ("s-closed", None)):
        state.cards.details.ask(node)
        app._draw_card_overlays(state)

    assert [kw["last_active"] for kw in handed] == [1234.5, None, None]
    state.fleet_writer.close()


def test_the_fold_setting_is_a_menu_item_that_persists_and_is_refused_like_any_other_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptmstr import app
    from pptmstr import settings as settings_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    state = app._start(_args(fake=True), settings_mod.read())  # type: ignore[arg-type]
    assert state.settings.hide_ended_subagents is True

    app._switch_hide_ended(state, False)
    assert state.settings.hide_ended_subagents is False
    assert settings_mod.load().hide_ended_subagents is False

    file = settings_mod.settings_path()
    broken = '{"board_log": "true"}'
    file.write_text(broken)
    unread = app._start(_args(), settings_mod.read(), list_sessions=tuple)  # type: ignore[arg-type]
    app._switch_hide_ended(unread, False)
    assert unread.settings.hide_ended_subagents is False
    assert file.read_text() == broken and unread.unsaved is not None

    menu = _draw_menus(monkeypatch, state, "Show ended sub-agents")
    assert menu.items["Show ended sub-agents"] == (True, True), "ticked while they are shown"
    assert state.settings.hide_ended_subagents is True, "and a click hides them"
    assert settings_mod.load().hide_ended_subagents is True
    menu = _draw_menus(monkeypatch, state, "")
    assert menu.items["Show ended sub-agents"] == (False, True)


def test_main_starts_with_the_real_lister_and_start_moves_before_it_builds_or_recalls() -> None:
    """
    Read off the source, which opens a window. ``main`` hands ``_start`` no
    lister, so the recall asks the CLI's own; and in ``_start`` the move is
    ahead of the state, the board writer and the recall worker, in that order.
    """
    import inspect

    from pptmstr import app

    assert "state = _start(args, settings_mod.read())" in inspect.getsource(app.main)
    source = inspect.getsource(app._start)
    body = source[source.index('"""', source.index('"""') + 3) :]
    order = [
        body.index("_migrate_board_logs()"),
        body.index("state = AppState("),
        body.index("boardsync.BoardWriter()"),
        body.index("target=_recall_fleet"),
    ]
    assert order == sorted(order)
    moving = inspect.getsource(app._migrate_board_logs)
    assert moving.index("LOG.info(") < moving.index("boardsync.migrate()")


def test_a_closed_card_taken_off_the_fleet_says_so_in_its_file_and_is_not_recalled_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Why the recall tells the writer of every session it puts back: the removal
    of a card this process never ran is a change to a file it never created,
    and a writer that had not been told of the session would drop it in silence.
    """
    from pptmstr import sessionfile
    from pptmstr.intents import SessionDetached

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id = _ran_and_closed()
    state = _started(monkeypatch, lambda: [_row(session_id)])
    state.bridge.emit(SessionDetached(session_id))
    _frames(state)
    assert state.store.snapshot().off_fleet(session_id) and state.detach_refused == {}
    state.fleet_writer.close()
    state.board_writer.close()

    found = sessionfile.read(session_id)
    assert isinstance(found, sessionfile.SessionFile) and found.in_fleet is False
    assert found.ended_how == "closed"

    again = _started(monkeypatch, lambda: [_row(session_id)])
    assert again.fleet.sessions == () and again.fleet.detached == (session_id,)
    assert (session_id, None) not in again.store.snapshot().nodes
    again.fleet_writer.close()
    again.board_writer.close()


def test_a_session_file_that_is_not_this_builds_to_write_is_said_once_by_the_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The wiring of the drain. The writer latches a session whose file it cannot
    read and reports that once; a frame that did not drain it would leave the
    operator with a card whose file silently stopped changing.
    """
    from pptmstr import sessionfile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        session_id = run.session_id
        file = sessionfile.file_path(session_id)
        assert file is not None and file.is_file()
        file.write_text("{somebody else's text")
        mark = LOG.snapshot()[1]
        _ends(run)
        for _ in range(3):
            run.frame()
        said = [text for level, text in _fleet_log_since(mark, "fleet") if level is Level.WARN]

    assert len(said) == 1 and session_id in said[0] and "not written again" in said[0]
    assert file.read_text() == "{somebody else's text"


def test_a_removal_that_did_not_reach_the_file_is_an_error_line_and_a_missed_time_is_not() -> None:
    from pptmstr import app, fleetsync, sessionfile

    lost = sessionfile.Unwritten("no space left on device")
    mark = LOG.snapshot()[1]
    app._read_fleet_reports(
        (
            fleetsync.Outcome("s-1", {"in_fleet": False}, lost),
            fleetsync.Outcome("s-2", {"last_active": 1.0}, lost),
        )
    )
    said = [(lv, t) for lv, t in _fleet_log_since(mark, "fleet") if lv is not Level.DEBUG]
    assert len(said) == 1 and said[0][0] is Level.ERROR
    assert "s-1" in said[0][1] and "no space left" in said[0][1]


@pytest.mark.parametrize(
    ("settings_string", "contained"), [("  ", False), ('{"sandbox": {}}', True)]
)
def test_the_file_says_contained_as_the_drivers_guard_judges_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings_string: str, contained: bool
) -> None:
    """
    A blank settings string is no containment: the CLI is started with nothing
    of it. "A string was given" would write ``contained: true`` for a session
    with no sandbox, and the gate menu reads that key when the card comes back.
    """
    from pptmstr import sessionfile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(cli_version, "check_installed_cli", lambda *a, **k: _MEETS)
    where = tmp_path / "work"
    where.mkdir()
    with _Process(None, None) as run:
        spec = LaunchSpec(
            task="t", model="claude-sonnet-5", cwd=str(where), containment=settings_string
        )
        run.app._launch(run.state, spec)
        [launch] = run.state.launches
        assert isinstance(launch.future.result(timeout=5.0), run.app.LaunchStarted)
        run.frame()
        _goes_live(run)
        found = sessionfile.read(run.session_id)
    assert isinstance(found, sessionfile.SessionFile) and found.contained is contained


def test_a_closed_card_whose_transcript_is_gone_is_given_no_path_to_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptmstr.model import TranscriptPresence

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id = _ran_and_closed()
    state = _started(monkeypatch, tuple)
    card = state.store.snapshot().nodes[(session_id, None)]
    assert card.recalled is not None and card.recalled.transcript is TranscriptPresence.GONE
    assert state.closed_paths == {}
    state.fleet_writer.close()
    state.board_writer.close()


def test_a_session_with_nothing_to_restore_that_may_not_be_appended_to_is_not_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``Found.unrecorded`` with no board beside it: the read could not say the
    session's own directory is its whole history. "Nothing logged" would
    otherwise be adopted outright and a log started behind whatever is there.
    """
    from pptmstr import boardsync

    monkeypatch.setattr(
        boardsync,
        "find",
        lambda sid, root=None, legacy=None: boardsync.Found(
            boardsync.NoLog("nothing is logged for this session"),
            boardsync.BoardWriter(root).directory(sid),
            unrecorded="its legacy directory could not be listed",
        ),
    )
    with _Process(tmp_path, _one_more_task("t-1")) as run:
        run.launch(resume="s-unsure")
        run.frame()
        run.frame()
        log = run.log("s-unsure")
        declared = "t-1" in run.state.store.snapshot().tasks

    assert declared and not log.recording and not log.awaiting
    assert log.why_not == "its legacy directory could not be listed"
    assert list(tmp_path.iterdir()) == []


# -- the card's own levers, wired: remove, close a failed card, resume, the gate -------


def _file(session_id: str):  # type: ignore[no-untyped-def]
    from pptmstr import sessionfile

    found = sessionfile.read(session_id)
    assert isinstance(found, sessionfile.SessionFile), found
    return found


def _closed_card(monkeypatch: pytest.MonkeyPatch, says: object = _a_working_team):  # type: ignore[no-untyped-def]
    """
    A session run and closed by one process, and the next process started with
    its card recalled: ``(session id, state)``.
    """
    session_id = _ran_and_closed(says)
    state = _started(monkeypatch, lambda: [_row(session_id)])
    assert state.store.snapshot().nodes[(session_id, None)].recalled is not None
    return session_id, state


def _shut(state: object) -> None:
    state.fleet_writer.close()  # type: ignore[attr-defined]
    if state.board_writer is not None:  # type: ignore[attr-defined]
        state.board_writer.close()  # type: ignore[attr-defined]


def test_the_cards_menu_is_handed_every_lever_and_the_three_things_no_record_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Unwired, each of these is an item greyed "not wired in this build" or a row
    that says "not known" -- which reads as covered. So: what ``_card_wiring``
    hands the fleet pane, asked of a real run.
    """
    from pptmstr.ui import session_menu

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        node = (run.session_id, None)
        run.state.frame_snap = run.state.store.snapshot()
        wiring = run.app._card_wiring(run.state)

        assert wiring.actions.wired == frozenset(
            {
                session_menu.Action.REMOVE,
                session_menu.Action.RESUME_AS,
                session_menu.Action.SET_GATE,
            }
        )
        assert wiring.state is run.state.cards
        facts = wiring.facts(node)
        assert facts.snap is run.state.frame_snap
        assert facts.run_held is True and facts.contained is False
        assert wiring.gate(node) is Policy.STRICT

        run.running.clear()
        assert run.app._card_wiring(run.state).facts(node).run_held is False
        # A card this window holds no session object for: not known, not "no".
        assert run.app._card_wiring(run.state).facts(("nobody", None)).contained is None
    source = __import__("inspect").getsource(run.app._panels)
    assert "_card_wiring(state)" in source and "session_menu.Wiring(" not in source


def test_a_closed_card_removed_from_its_menu_is_off_the_fleet_until_it_is_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Remove, through the action the menu calls. The card goes and the file says
    so; the next start does not put it back; and resuming it -- the picker's
    path, a launch that names its id -- does.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id, state = _closed_card(monkeypatch)
    node = (session_id, None)

    from pptmstr import app

    state.frame_snap = state.store.snapshot()
    detach = app._card_actions(state).detach
    assert detach is not None
    detach(node)
    _frames(state)
    assert state.store.snapshot().off_fleet(session_id)
    assert state.not_done == [] and state.detach_refused == {}
    _shut(state)
    assert _file(session_id).in_fleet is False

    again = _started(monkeypatch, lambda: [_row(session_id)])
    assert again.fleet.sessions == () and node not in again.store.snapshot().nodes

    with _Process(None, None, state=again) as third:
        third.launch(resume=session_id)
        third.frame()
        _goes_live(third, session_id)
        assert not again.store.snapshot().off_fleet(session_id)
    assert _file(session_id).in_fleet is True


def test_a_session_that_ran_here_is_removed_once_its_run_is_over_and_not_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    F1. The store sees a root that has ended; whether a process is still
    attached is the pool's to say, and it is asked first. While it says yes
    nothing is asked of the store, the card stays and the operator is told why.
    """
    from pptmstr.intents import SessionDetached
    from pptmstr.ui import session_menu

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        session_id = run.session_id
        node = (session_id, None)
        _ends(run)
        assert run.state.store.snapshot().detach_refusal(session_id) is None

        # The store would let it go. The pool still has a run under the id.
        run.running.add(session_id)
        run.state.frame_snap = run.state.store.snapshot()
        run.app._detach(run.state, node)
        run.frame()
        assert not [i for i in run.applied if isinstance(i, SessionDetached)]
        assert not run.state.store.snapshot().off_fleet(session_id)
        assert run.state.not_done == [f"not removed from the fleet: {session_menu.RUN_ATTACHED}"]
        assert _file(session_id).in_fleet is True

        run.running.discard(session_id)
        run.state.not_done.clear()
        run.app._detach(run.state, node)
        run.frame()
        assert run.state.store.snapshot().off_fleet(session_id) and run.state.not_done == []
    assert _file(session_id).in_fleet is False


def test_a_removal_the_store_refuses_is_put_in_front_of_the_operator_in_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The session owes something: a sub-agent of it failed and its row has not
    been dismissed. The pool has no run, so the store is asked, and its refusal
    comes back as an effect nobody is parked on.
    """
    import time

    from pptmstr.intents import AgentFinished, AgentSpawned, FailureAcknowledged
    from pptmstr.model import AgentState, DetachRefusal
    from pptmstr.ui import session_menu

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        session_id = run.session_id
        node, worker = (session_id, None), (session_id, "agent-9")
        run.bridge.emit(AgentSpawned(worker, node, "check it", "m", time.monotonic()))
        run.bridge.emit(AgentFinished(worker, AgentState.FAILED, time.monotonic(), error="x"))
        _ends(run)

        run.app._detach(run.state, node)
        run.frame()
        assert not run.state.store.snapshot().off_fleet(session_id)
        assert run.state.detach_refused == {session_id: DetachRefusal.OWES}
        words = session_menu.REMOVAL_REFUSED[DetachRefusal.OWES]
        assert run.state.not_done == [f"not removed from the fleet: {words}"]

        # Dismissed, the failed sub-agent is no bar: only the root must be closed.
        run.bridge.emit(FailureAcknowledged(worker))
        run.state.not_done.clear()
        run.app._detach(run.state, node)
        run.frame()
        assert run.state.store.snapshot().off_fleet(session_id)
        assert run.state.detach_refused == {} and run.state.not_done == []


def _fails(run: _Process, error: str = "the CLI went away") -> None:
    import time

    from pptmstr.intents import AgentFinished
    from pptmstr.model import AgentState

    run.bridge.emit(
        AgentFinished((run.session_id, None), AgentState.FAILED, time.monotonic(), error=error)
    )
    run.frame()


def test_a_failed_card_with_no_run_is_closed_as_ended_and_can_then_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The card that could not be got rid of. Its run died, so the pool has nothing
    to close and would report nothing; the store would keep the root in FAILED,
    and FAILED cannot leave the fleet. Close marks it ended in the store, on the
    pool's word that no run is left, and never asks the pool to close it.
    """
    from pptmstr.intents import FailedSessionClosed
    from pptmstr.model import AgentState, DetachRefusal

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        session_id = run.session_id
        node = (session_id, None)
        _fails(run)
        run.running.discard(session_id)
        snap = run.state.store.snapshot()
        assert snap.detach_refusal(session_id) is DetachRefusal.FAILED_NOT_CLOSED
        assert len(snap.needs_you) == 1
        as_it_failed = _file(session_id)
        assert as_it_failed.ended_how == "failed" and as_it_failed.error == "the CLI went away"
        assert as_it_failed.ended_at is not None

        run.state.frame_snap = snap
        run.app._card_actions(run.state).close(node)
        run.bridge.submit(_nothing()).result(timeout=5.0)
        run.frame()
        closed = run.state.store.snapshot()
        assert [i for i in run.applied if isinstance(i, FailedSessionClosed)] == [
            FailedSessionClosed(node, run_held=False)
        ]
        assert run.closed == [], "the pool had nothing to close and was not asked"
        assert closed.nodes[node].state is AgentState.DONE and closed.needs_you == ()
        # The close is said, and nothing else about the end is: the file keeps
        # when the session failed and what it said, and the card is not moved
        # up the next start's order by the click.
        as_closed = _file(session_id)
        assert as_closed.ended_how == "closed"
        assert as_closed.error == "the CLI went away"
        assert as_closed.ended_at == as_it_failed.ended_at
        assert as_closed.last_active == as_it_failed.last_active

        run.state.frame_snap = closed
        run.app._detach(run.state, node)
        run.frame()
        assert run.state.store.snapshot().off_fleet(session_id) and run.state.not_done == []
    assert _file(session_id).in_fleet is False


def test_a_failed_card_whose_process_is_still_attached_is_closed_through_the_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    An error result leaves the client connected. That session is closed the way
    any other is, by the pool, which ends the process and reports the end: the
    store is not told it is over by anything that has not ended it.
    """
    from pptmstr.intents import FailedSessionClosed
    from pptmstr.model import AgentState

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        node = (run.session_id, None)
        _fails(run, "HTTP 529: overloaded")

        run.state.frame_snap = run.state.store.snapshot()
        run.app._card_actions(run.state).close(node)
        run.bridge.submit(_nothing()).result(timeout=5.0)
        run.frame()
        assert run.closed == [node]
        assert not [i for i in run.applied if isinstance(i, FailedSessionClosed)]
        assert run.state.store.snapshot().nodes[node].state is AgentState.FAILED


def test_a_live_session_is_closed_through_the_pool_whatever_the_snapshot_holds(
    tmp_path: Path,
) -> None:
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        node = (run.session_id, None)
        run.running.clear()
        run.state.frame_snap = run.state.store.snapshot()
        run.app._close(run.state, node)
        run.bridge.submit(_nothing()).result(timeout=5.0)
        run.frame()
        assert run.closed == [node]


def _press(state: object, session_id: str, message: str, policy: Policy = Policy.STRICT):  # type: ignore[no-untyped-def]
    """
    The resume form's button, as ``resume_form.draw`` presses it: the form is
    read once for the frame it was drawn on, and pressed against that reading.
    """
    from pptmstr.ui import launcher, resume_form

    forms = state.resume  # type: ignore[attr-defined]
    assert forms.showing == session_id
    form = forms.form
    form.message = message
    form.policy = policy
    form.refresh()
    gate = launcher.VersionGate()
    pressed = resume_form.press(form, gate)
    assert isinstance(pressed, resume_form.Launch), pressed
    return pressed


def _brief_entries(directory: str) -> list[str]:
    return sorted(p.name for p in Path(directory).iterdir())


def test_resuming_a_closed_card_keeps_its_title_sends_what_was_typed_and_keeps_its_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The whole of a resume from a card, through the form the menu opens. The
    hazards, each of which is a session that starts and starts wrong: the card
    retitled by the message; the session sent its first task again; the message
    appended to the team's brief as a new premise; a team started solo.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id, state = _closed_card(monkeypatch)
    node = (session_id, None)
    rec = state.store.snapshot().nodes[node]
    brief = rec.brief
    assert brief is not None and rec.task == "build the thing" and rec.template == "feature"
    before = _brief_entries(brief)
    assert len(before) == 1

    from pptmstr import app

    app._card_actions(state).resume(rec)
    form = state.resume.form
    assert (form.facts.task, form.template, form.brief) == ("build the thing", "feature", brief)
    assert form.message == "build the thing", "prefilled with the last thing sent"
    assert form.policy is Policy.STRICT

    with _Process(None, None, state=state) as second:
        pressed = _press(state, session_id, "ok, carry on with step 3")
        app._resume_pressed(state, pressed)
        for entry in state.launches:
            entry.future.exception(timeout=5.0)
        second.bridge.submit(_nothing()).result(timeout=5.0)
        second.frame()
        session = second.sessions[node]
        assert (session.task, session.message) == ("build the thing", "ok, carry on with step 3")
        assert session.resume == session_id and session.brief == brief
        assert session.template.name == "feature"
        announced = state.store.snapshot().nodes[node]
        assert announced.recalled is None and announced.task == "build the thing"
        # Announced and not yet started: the file still says what was last sent.
        assert _file(session_id).last_message == "build the thing"

        _goes_live(second, session_id)
        assert state.last_sent[session_id] == "ok, carry on with step 3"
        app._reap_launches(state)
        assert state.resume.forms[session_id].failures == [] and state.launcher.failures == []
    after = _file(session_id)
    assert after.task == "build the thing" and after.last_message == "ok, carry on with step 3"
    assert after.template == "feature" and after.brief == brief
    assert _brief_entries(brief) == before, "the message is not a premise"


def test_a_resume_that_starts_nothing_goes_back_to_its_own_form_with_what_was_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Routed to the launcher instead, the reason would sit over the New Task box
    and the typed message would be gone: it is not a launcher draft.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id, state = _closed_card(monkeypatch)
    rec = state.store.snapshot().nodes[(session_id, None)]

    from pptmstr import app

    app._card_actions(state).resume(rec)
    with _Process(None, None, state=state, takes=False):
        pressed = _press(state, session_id, "try again, more slowly")
        app._resume_pressed(state, pressed)
        form = state.resume.form
        form.launched()
        state.resume.close()
        assert form.message == "" and not state.resume._open_requested
        for entry in state.launches:
            entry.future.exception(timeout=5.0)
        app._reap_launches(state)

    assert state.launches == [] and state.launcher.failures == []
    (failure,) = form.failures
    assert failure.draft == pressed.draft and "shutting down" in failure.reason
    assert form.message == "try again, more slowly"
    assert state.resume.showing == session_id and state.resume._open_requested
    assert _file(session_id).last_message == "build the thing"


def test_a_resume_that_never_reaches_the_cli_leaves_the_files_gate_and_message_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    F4d. The pool takes the session and announces it, and the run is then
    refused before it connects. Written at the pool's answer, the file would
    hold the rung and the message of a run that never existed, and the next
    resume form would offer that message as the last one sent.
    """
    import time

    from pptmstr.intents import AgentFinished
    from pptmstr.model import AgentState

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id, state = _closed_card(monkeypatch)
    node = (session_id, None)
    rec = state.store.snapshot().nodes[node]

    from pptmstr import app

    app._card_actions(state).resume(rec)
    with _Process(None, None, state=state) as second:
        pressed = _press(state, session_id, "never sent", Policy.PERMISSIVE)
        app._resume_pressed(state, pressed)
        for entry in state.launches:
            entry.future.exception(timeout=5.0)
        second.bridge.submit(_nothing()).result(timeout=5.0)
        second.frame()
        assert second.sessions[node].policy is Policy.PERMISSIVE
        second.running.discard(session_id)
        second.bridge.emit(
            AgentFinished(node, AgentState.FAILED, time.monotonic(), error="refused at start")
        )
        second.frame()
    after = _file(session_id)
    assert after.policy is Policy.STRICT and after.last_message == "build the thing"
    assert after.model == "claude-sonnet-5" and after.launched_at is not None
    assert after.ended_how == "failed" and after.in_fleet
    assert session_id not in state.last_sent


def test_a_team_resumed_with_no_brief_on_its_spec_is_given_its_own_and_nothing_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Every resume, from any surface. A spec that names the session and no brief
    is what a surface that did not look builds; the directory the launch would
    seed is the session's own, so seeding it appends the resume text to the
    brief the team already has.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id, state = _closed_card(monkeypatch)
    node = (session_id, None)
    brief = state.store.snapshot().nodes[node].brief
    assert brief is not None
    before = _brief_entries(brief)

    with _Process(None, None, state=state) as second:
        second.launch(resume=session_id)
        assert second.sessions[node].brief == brief
        assert _brief_entries(brief) == before

    # And one with no brief of its own to find gets none, and none is made.
    with _Process(None, None) as lone:
        lone.launch(resume="11111111-2222-3333-4444-555555555555")
        (session,) = lone.sessions.values()
        assert session.brief is None
    made = Path(brief).parent / "11111111-2222-3333-4444-555555555555"
    assert not made.exists()


def _contained_launch(run: _Process, monkeypatch: pytest.MonkeyPatch, cwd: str) -> AgentSession:
    monkeypatch.setattr(cli_version, "check_installed_cli", lambda *a, **k: _MEETS)
    spec = LaunchSpec(
        task="build the thing", model="claude-sonnet-5", cwd=cwd, containment=CONTAINMENT
    )
    run.app._launch(run.state, spec)
    for entry in run.state.launches:
        entry.future.exception(timeout=5.0)
    run.bridge.submit(_nothing()).result(timeout=5.0)
    run.frame()
    return run.sessions[(run.session_id, None)]


def test_a_live_widen_the_driver_admits_moves_the_gate_and_the_file_follows_every_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    F2. The gate in a session's file is what its closed card says it last ran
    under. Every move the driver takes is written: the widen, a narrow by the
    menu, and the narrow to strict that HEALTH also has.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    work = tmp_path / "work"
    work.mkdir()
    with _Process(None, None) as run:
        session = _contained_launch(run, monkeypatch, str(work))
        node = session.node_id
        _goes_live(run)
        assert _file(session.session_id).policy is Policy.STRICT
        set_policy = run.app._card_actions(run.state).set_policy
        assert set_policy is not None

        for rung in (Policy.AUTONOMOUS, Policy.PERMISSIVE):
            set_policy(node, rung)
            run.frame()
            assert session.policy is rung and run.state.not_done == []
            assert _file(session.session_id).policy is rung

        run.app._revoke_policy(run.state, node)
        run.frame()
        assert session.policy is Policy.STRICT
        assert _file(session.session_id).policy is Policy.STRICT


def test_a_live_widen_the_drivers_guard_refuses_changes_nothing_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A session started with no containment, asked for the rung that needs it. The
    menu offers that as a resume, so this is the menu drawn before the facts
    moved; the raise is caught here and shown, and the file is not told.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        session = run.sessions[(run.session_id, None)]
        told: list[object] = []
        submit = run.state.fleet_writer.submit

        def noting(change: object) -> int:
            told.append(change)
            return submit(change)  # type: ignore[arg-type]

        run.state.fleet_writer.submit = noting  # type: ignore[method-assign]
        run.app._set_policy(run.state, session.node_id, Policy.AUTONOMOUS)
        run.frame()

        assert session.policy is Policy.STRICT
        assert told == [], "a move that was not made is not written, even as no change"
        (said,) = run.state.not_done
        assert said.startswith("the gate was not moved to autonomous: ")
        assert "no containment" in said
        assert _file(run.session_id).policy is Policy.STRICT

        # The rung below needs none, and is taken.
        run.state.not_done.clear()
        run.app._set_policy(run.state, session.node_id, Policy.PERMISSIVE)
        run.frame()
        assert session.policy is Policy.PERMISSIVE and run.state.not_done == []
        assert _file(run.session_id).policy is Policy.PERMISSIVE


def test_a_session_in_pptmstrs_own_checkout_is_not_widened_to_a_rung_a_launch_is_refused_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    F5. ``AgentSession.set_policy`` asks only whether the session has
    containment, and the menu is the first thing that can widen a live session.
    The directory here was somewhere else when the session started and leads
    into the checkout now -- the case each start asks about again for the same
    reason. The driver would take the move; the shell does not make it.
    """
    import os

    from pptmstr.ui import launcher

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    work = tmp_path / "work"
    work.mkdir()
    with _Process(None, None) as run:
        session = _contained_launch(run, monkeypatch, str(work))
        _goes_live(run)
        os.rmdir(work)
        os.symlink(launcher.OWN_CHECKOUT, work)

        run.app._set_policy(run.state, session.node_id, Policy.AUTONOMOUS)
        run.frame()
        assert session.policy is Policy.STRICT
        (said,) = run.state.not_done
        assert said.startswith("the gate was not moved to autonomous: ")
        assert "this session's directory" in said
        assert _file(session.session_id).policy is Policy.STRICT

        # The guard itself has no objection: it is the shell's question.
        session.set_policy(Policy.AUTONOMOUS)
        session.set_policy(Policy.STRICT)
        # A rung that needs no containment is not held to the directory.
        run.state.not_done.clear()
        run.app._set_policy(run.state, session.node_id, Policy.PERMISSIVE)
        assert session.policy is Policy.PERMISSIVE and run.state.not_done == []


def test_a_gate_move_on_a_card_no_session_is_held_for_is_said_and_writes_nothing() -> None:
    from pptmstr import app

    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    submitted: list[object] = []
    state.fleet_writer.submit = submitted.append  # type: ignore[method-assign,assignment]
    app._set_policy(state, ("nobody", None), Policy.PERMISSIVE)
    app._revoke_policy(state, ("nobody", None))
    assert len(state.not_done) == 1 and "no running session is held" in state.not_done[0]
    assert submitted == []


def test_a_narrow_that_changes_nothing_writes_nothing(tmp_path: Path) -> None:
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        node = (run.session_id, None)
        submitted: list[object] = []
        run.state.fleet_writer.submit = submitted.append  # type: ignore[method-assign,assignment]
        run.app._revoke_policy(run.state, node)
        run.app._set_policy(run.state, node, Policy.STRICT)
        assert submitted == [] and run.state.not_done == []


def test_healths_fork_does_not_start_a_session_from_a_cards_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    F3. HEALTH forks ``LaunchSpec.from_record`` at one click. For a closed card
    whose file recorded no task, the record's task is the CLI's label for the
    conversation, and the click would start a paid session instructed by it.
    """
    from pptmstr import app
    from pptmstr.intents import SessionRecalled
    from pptmstr.model import Recalled
    from pptmstr.ui import session_menu

    store = Store()
    store.apply_all(
        [
            SessionRecalled("s-label", "A summary of it", "m", Recalled(task_recorded=False)),
            SessionRecalled("s-task", "build the thing", "m", Recalled(task_recorded=True)),
        ],
        now=1.0,
    )
    state = app.AppState(store=store, bridge=Bridge(), settings=Settings())
    state.frame_snap = store.snapshot()
    launched: list[tuple[LaunchSpec, str]] = []
    monkeypatch.setattr(
        app, "_launch", lambda state, spec, draft=None, *, origin: launched.append((spec, origin))
    )

    label = LaunchSpec.from_record(state.frame_snap.nodes[("s-label", None)])
    app._fork_of(state, ("s-label", None), label)
    assert launched == []
    assert state.not_done == [f"not forked: {session_menu.TASK_NOT_RECORDED}"]

    real = LaunchSpec.from_record(state.frame_snap.nodes[("s-task", None)])
    app._fork_of(state, ("s-task", None), real)
    assert launched == [(real, "fork")]
    assert "_fork_of(state, node, spec)" in __import__("inspect").getsource(app._panels)


def test_a_closed_card_with_no_task_or_team_on_file_opens_a_form_that_claims_neither(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The "not known" path. A record's fields have defaults -- solo, strict, the
    label as the task -- and a form filled from them would state each as a fact
    about a session whose file said none of them.
    """
    from pptmstr import app, sessionfile
    from pptmstr.intents import SessionRecalled
    from pptmstr.model import Recalled
    from pptmstr.ui import resume_form

    store = Store()
    store.apply_all(
        [
            SessionRecalled(
                "s-old", "A summary of it", "", Recalled(task_recorded=False), cwd="/tmp"
            )
        ],
        now=1.0,
    )
    state = app.AppState(store=store, bridge=Bridge(), settings=Settings())
    state.frame_snap = store.snapshot()
    rec = state.frame_snap.nodes[("s-old", None)]

    app._open_resume(state, rec)

    form = state.resume.form
    assert form is not None
    # The label is passed, as a label: drawn as the title and never as the task.
    assert (form.facts.task, form.facts.task_is_label) == ("A summary of it", True)
    assert resume_form.task_fact(form.facts) == (
        "title",
        "A summary of it",
        resume_form.TITLE_IS_LABEL,
    )
    assert not form.team_known
    assert isinstance(form.facts.template, resume_form.UnknownTeam)
    assert form.facts.policy == sessionfile.UnknownPolicy()
    assert form.facts.contained is False and form.facts.last_message is None
    assert form.message == "" and form.model, "an empty box, and a model to offer"


def test_a_closed_card_opens_its_form_with_what_its_file_said(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptmstr import app
    from pptmstr.intents import SessionRecalled
    from pptmstr.model import Recalled

    said = Recalled(
        policy=Policy.AUTONOMOUS,
        contained=True,
        last_message="and then the tests",
        subagent_cap=3,
        task_recorded=True,
    )
    store = Store()
    store.apply_all(
        [SessionRecalled("s-1", "build it", "claude-opus-5", said, cwd="/tmp", template="feature")],
        now=1.0,
    )
    state = app.AppState(store=store, bridge=Bridge(), settings=Settings())
    rec = store.snapshot().nodes[("s-1", None)]

    app._open_resume(state, rec, Policy.PERMISSIVE)

    form = state.resume.form
    assert form is not None
    facts = form.facts
    assert (facts.task, facts.template, facts.model) == ("build it", "feature", "claude-opus-5")
    assert (facts.policy, facts.contained) == (Policy.AUTONOMOUS, True)
    assert (facts.last_message, facts.subagent_cap) == ("and then the tests", 3)
    assert form.message == "and then the tests" and form.subagent_cap == 3
    assert form.policy is Policy.PERMISSIVE, "the rung the menu's row named, not the file's"


def test_a_session_that_ran_here_opens_its_form_with_the_last_thing_this_window_sent_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        session = run.sessions[(run.session_id, None)]
        session.set_policy(Policy.PERMISSIVE)
        run.state.last_sent[run.session_id] = "the second message"
        _ends(run)
        rec = run.state.store.snapshot().nodes[session.node_id]

        run.app._open_resume(run.state, rec)

        form = run.state.resume.form
        assert form.facts.last_message == "the second message"
        assert form.facts.policy is Policy.PERMISSIVE, "the session object's, not the record's"
        assert form.facts.subagent_cap == Settings().subagent_cap
        assert form.facts.task == "build the thing" and form.template == "feature"


def test_a_recall_that_gave_no_answer_is_on_the_status_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    F4f. The worker's exception is logged once. ``fleet`` stays None, which is
    also what a recall still on its way looks like, so without its own segment
    a start with no closed cards looks like a start with none to show.
    """
    from pptmstr import app, fleetsync

    def broken(*a: object, **k: object) -> object:
        raise RuntimeError("the sessions directory is on a dead mount")

    monkeypatch.setattr(fleetsync, "recall", broken)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    mark = LOG.snapshot()[1]
    app._recall_fleet(state, None)

    assert state.fleet is None and state.recall_failed is not None
    assert "dead mount" in state.recall_failed
    said = [level for level, _ in _fleet_log_since(mark, "fleet") if level is not Level.DEBUG]
    assert said == [Level.ERROR]
    assert app.startup_shortfalls(state.migration, state.fleet, state.recall_failed) == (
        "at startup: closed sessions not recalled (see LOG)"
    )
    assert app.startup_shortfalls(None, None) is None


def test_the_log_cites_a_heading_the_readme_has() -> None:
    from pptmstr import app

    readme = (Path(app.__file__).parent.parent / "README.md").read_text()
    assert f"### {app.README_BOARD_LOG_HEADING}\n" in readme
    source = __import__("inspect").getsource(app._migrate_board_logs)
    assert source.count("_README_BOARD_LOG") == 2 and "'Board log'" not in source


class _Drawn:
    """
    An ImGui context to draw one root-level thing in, and what it said.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self.said: list[str] = []
        self.buttons: dict[str, tuple[float, float]] = {}

    def __enter__(self) -> _Drawn:
        from imgui_bundle import imgui

        from pptmstr import app

        self.imgui = imgui
        self._context = imgui.create_context()
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(1280.0, 900.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")
        io.add_focus_event(True)
        self.io = io
        said, buttons = self.said, self.buttons

        class Recording:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            @staticmethod
            def text_colored(colour: object, text: str) -> None:
                said.append(text)
                imgui.text_colored(colour, text)  # type: ignore[arg-type]

            @staticmethod
            def button(label: str) -> bool:
                pressed = imgui.button(label)
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                buttons[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)
                return bool(pressed)

        self._monkeypatch.setattr(app, "imgui", Recording())
        return self

    def __exit__(self, *exc: object) -> None:
        self.imgui.destroy_context(self._context)

    def frame(self, draw: object, count: int = 1) -> None:
        for _ in range(count):
            self.said.clear()
            self.buttons.clear()
            self.imgui.new_frame()
            draw()  # type: ignore[operator]
            self.imgui.end_frame()

    def key(self, draw: object, key: object) -> None:
        self.io.add_key_event(key, True)  # type: ignore[arg-type]
        self.frame(draw)
        self.io.add_key_event(key, False)  # type: ignore[arg-type]
        self.frame(draw)

    def click(self, draw: object, at: tuple[float, float]) -> None:
        self.io.add_mouse_pos_event(*at)
        self.frame(draw)
        self.io.add_mouse_button_event(0, True)
        self.frame(draw)
        self.io.add_mouse_button_event(0, False)
        self.frame(draw, 2)


def test_a_refused_control_is_on_screen_until_it_is_dismissed_and_not_by_the_key_that_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The menu closes on the Enter that chose the item. Read on its first frame,
    that same Enter would dismiss the refusal before it was drawn twice.
    """
    from imgui_bundle import imgui

    from pptmstr import app

    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())

    def draw() -> None:
        app._draw_not_done(state)

    with _Drawn(monkeypatch) as drawn:
        drawn.frame(draw, 2)
        assert drawn.said == [], "nothing refused, nothing drawn"

        drawn.io.add_key_event(imgui.Key.enter, True)
        app._not_done(state, "not removed from the fleet: a process is still attached to it")
        app._not_done(state, "not removed from the fleet: a process is still attached to it")
        app._not_done(state, "the gate was not moved to autonomous: no containment")
        drawn.frame(draw)
        drawn.io.add_key_event(imgui.Key.enter, False)
        drawn.frame(draw, 3)
        assert drawn.said == [
            "not removed from the fleet: a process is still attached to it",
            "the gate was not moved to autonomous: no containment",
        ]

        drawn.key(draw, imgui.Key.enter)
        assert state.not_done == [] and drawn.said == []

        app._not_done(state, "not forked: its task is not recorded")
        drawn.frame(draw, 3)
        drawn.click(draw, drawn.buttons["ok"])
        assert state.not_done == [] and state.not_done_frames == 0


def test_the_frame_draws_the_resume_form_and_its_press_is_a_launch_that_knows_its_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The wiring of the overlay. ``_draw_overlays`` draws the form every frame,
    with the operator's settings and the live sessions while it is up, and what
    its button is handed reaches the pool as a resume carrying the message --
    and comes back to the form, not the launcher, when it starts nothing.
    """
    from pptmstr.ui import launcher, resume_form

    handed: list[dict[str, object]] = []
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
    _no_settings_dialog(monkeypatch)
    monkeypatch.setattr(launcher, "draw", lambda state, **kw: None)
    monkeypatch.setattr(
        resume_form, "draw", lambda state, **kw: handed.append({"state": state, **kw})
    )

    with _Launching(monkeypatch) as run:
        state = run.state
        state.store = _roots_and_a_worker()
        state.frame_snap = state.store.snapshot()
        run.app._draw_overlays(state)
        state.resume.is_open = True
        run.app._draw_overlays(state)
        assert [kw["live"] for kw in handed] == [frozenset(), frozenset({"live", "waiting"})]
        assert handed[1]["state"] is state.resume
        assert handed[1]["subagent_cap"] == state.settings.subagent_cap
        assert handed[1]["wrap"] == state.settings.wrap_inputs
        assert handed[1]["models"] == state.catalog.models

        form = state.resume.open_for(
            session_id="s-9",
            cwd="/tmp",
            task="build the thing",
            template="solo",
            policy=Policy.STRICT,
            contained=False,
            model="claude-sonnet-5",
            subagent_cap=None,
            brief=None,
            last_message=None,
        )
        form.message = "carry on"
        form.refresh()
        pressed = resume_form.press(form, launcher.VersionGate())
        assert isinstance(pressed, resume_form.Launch)
        handed[1]["launch"](pressed)  # type: ignore[operator]
        run.settle()
        (session,) = run.sessions
        assert (session.resume, session.task, session.message) == (
            "s-9",
            "build the thing",
            "carry on",
        )
        (flight,) = state.launches
        assert flight.resumed == ("s-9", pressed.draft) and flight.origin == "resume"


def test_a_failed_resume_whose_form_is_gone_is_still_said_by_the_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A refusal with nowhere to go is a launch that vanished.
    """
    from pptmstr import app
    from pptmstr.ui import resume_form

    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    draft = resume_form.Draft("carry on", Policy.STRICT, "m", None, 0)
    spec = LaunchSpec(task="t", model="m", resume="s-gone", message="carry on")
    app._launch(state, spec, origin="resume", resumed=("s-gone", draft))
    app._reap_launches(state)

    (failure,) = state.launcher.failures
    assert "no session pool" in failure.reason


def test_while_either_form_is_up_the_frames_bare_keys_stand_down() -> None:
    """
    The card key and the layout keys defer to New Task because its focus can sit
    on a combo, where the keyboard is not captured. The resume form has the same
    combos.
    """
    from pptmstr import app

    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    assert not app._modal_up(state)
    state.resume.is_open = True
    assert app._modal_up(state)
    state.resume.is_open = False
    state.launcher.is_open = True
    assert app._modal_up(state)
    for reader in (app._handle_card_key, app._handle_layout_keys):
        body = __import__("inspect").getsource(reader)
        assert "_modal_up(state)" in body and "launcher.is_open" not in body


def test_a_card_whose_transcript_is_gone_can_always_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    F4a/b. A file is created when a run reaches the CLI, which is before the
    CLI is certain to have written a transcript, so a launch that died in its
    first turn can come back as a card with nothing to resume. Removing it is
    the only thing left to do with it, and nothing may stand in the way: it has
    no process and owes nothing, whatever its file says of how it ended.
    """
    from pptmstr import app, sessionfile
    from pptmstr.model import TranscriptPresence
    from pptmstr.ui import session_menu

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id = _ran_and_closed()
    done = sessionfile.update(
        session_id, {"ended_how": "failed", "error": "died in its first turn"}
    )
    assert isinstance(done, sessionfile.Written)
    state = _started(monkeypatch, lambda: [])
    node = (session_id, None)
    rec = state.store.snapshot().nodes[node]
    assert rec.recalled is not None and rec.recalled.transcript is TranscriptPresence.GONE

    state.frame_snap = state.store.snapshot()
    offered = session_menu.entries(rec, None, app._card_wiring(state).facts(node))
    assert [e.label for e in offered if isinstance(e, session_menu.Item)] == [
        "Details",
        "Remove from fleet",
    ]
    assert all(e.enabled for e in offered if isinstance(e, session_menu.Item))

    app._detach(state, node)
    _frames(state)
    assert state.store.snapshot().off_fleet(session_id) and state.not_done == []
    _shut(state)
    assert _file(session_id).in_fleet is False


# -- a session picked in New Task is resumed by New Task ----------------------------


def _picked_row(session_id: str, cwd: str | None) -> object:
    from pptmstr.sessions import SessionRow

    return SessionRow(
        session_id=session_id,
        cwd=cwd,
        git_branch="main",
        summary="what the CLI calls this conversation",
        first_prompt=None,
        created_at=None,
        last_modified=0,
        tag=None,
    )


class _Overlays:
    """
    ``app._draw_overlays`` with the two modals' draws replaced by recorders, so
    what is under test is what the app hands New Task and what it does with a
    press -- and not either modal's drawing.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, store: Store) -> None:
        from pptmstr import app
        from pptmstr.ui import launcher, resume_form

        self.app = app
        self.handed: list[dict[str, object]] = []
        monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
        _no_settings_dialog(monkeypatch)
        monkeypatch.setattr(launcher, "draw", lambda state, **kw: self.handed.append(kw))
        monkeypatch.setattr(resume_form, "draw", lambda state, **kw: None)
        self.launched: list[tuple[tuple[object, ...], dict[str, object]]] = []
        monkeypatch.setattr(app, "_launch", lambda *a, **k: self.launched.append((a, k)))

        class _IdlePool:
            running_count = 0
            queued_count = 0
            cap = 4

            def session_for(self, node: object) -> None:
                return None

            def has_run(self, node: object) -> bool:
                return False

        self.state = app.AppState(store=store, bridge=Bridge(), settings=Settings())
        self.state.pool = _IdlePool()  # type: ignore[assignment]
        self.state.frame_snap = store.snapshot()

    def frame(self) -> None:
        self.app._draw_overlays(self.state)

    def press(self, picked: object, message: str = "carry on", **fields: object) -> LaunchSpec:
        """
        New Task's launch button with ``picked`` picked and ``message`` in the box,
        as ``launcher.draw`` makes the press: the state's own spec and draft through
        the callback the frame handed it. Returns what reached ``_launch``.
        """
        draft = self.state.launcher
        draft.task = message
        for name, value in fields.items():
            setattr(draft, name, value)
        draft.picker.pick(None, draft.cwd)
        draft.picker.pick(picked, draft.cwd)  # type: ignore[arg-type]
        self.frame()
        assert draft.launch_hold() is None
        spec = draft.press()
        assert spec is not None
        self.handed[-1]["launch"](spec, draft.draft())  # type: ignore[operator]
        args, kwargs = self.launched[-1]
        assert args[0] is self.state and args[2] == draft.draft()
        assert kwargs == {}, "a press in New Task is New Task's, and fails back to it"
        sent = args[1]
        assert isinstance(sent, LaunchSpec)
        return sent


def test_new_task_is_handed_no_way_to_open_the_resume_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The property: the resume form opens from a closed card and from nowhere else.
    The frame hands New Task a launch callback and nothing a pick could be
    reported through, and nothing in the frame opens a form for a pick made on
    the launcher's state.
    """
    import inspect

    run = _Overlays(monkeypatch, Store())
    run.frame()
    assert "launch" in run.handed[-1]
    assert not [name for name in run.handed[-1] if "pick" in name or "resume" in name]

    run.state.launcher.picker.pick(_picked_row("s-1", str(tmp_path)), ".")  # type: ignore[arg-type]
    for _ in range(3):
        run.frame()
    assert run.state.resume.form is None and not run.state.resume.busy
    assert run.launched == []

    # And every way the form is asked for is a card's.
    openers = [
        name
        for name, member in inspect.getmembers(run.app, inspect.isfunction)
        if "resume.open_for(" in inspect.getsource(member)
    ]
    assert openers == ["_open_resume"]
    callers = sorted(
        name
        for name, member in inspect.getmembers(run.app, inspect.isfunction)
        if "_open_resume(" in inspect.getsource(member) and name != "_open_resume"
    )
    assert callers and all("card" in name or "gate" in name for name in callers), callers


def test_a_resume_pressed_in_new_task_reaches_launch_with_the_typed_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A session the store has no record of: the spec is the launcher's, untouched.
    The id and directory are the row's, the box is the message, and the title is
    the row's label, said to be one.
    """
    run = _Overlays(monkeypatch, Store())

    sent = run.press(_picked_row("s-foreign", str(tmp_path)), "  carry on from the tests ")

    assert (sent.resume, sent.message) == ("s-foreign", "carry on from the tests")
    assert sent.cwd == str(tmp_path.resolve())
    assert (sent.task, sent.task_is_label) == ("what the CLI calls this conversation", True)
    assert run.state.resume.form is None


def test_a_resume_pressed_in_new_task_keeps_the_title_its_card_has(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The announce replaces the session's record with one titled by the spec, and
    the launcher titles a resume by the CLI's label because a row carries no task.
    For a session with a card that would rename the card. The record's task goes
    instead, with the record's word on whether it is a task at all; the message
    and everything chosen in New Task are left alone.
    """
    from pptmstr.intents import AgentSpawned, SessionRecalled, StateChanged
    from pptmstr.model import AgentState, Recalled

    store = Store()
    store.apply(
        AgentSpawned(("s-ran", None), None, "build the thing", "claude-opus-5", 0.0, cwd="/srv"),
        now=0.0,
    )
    store.apply(StateChanged(("s-ran", None), AgentState.DONE), now=1.0)
    store.apply_all(
        [
            SessionRecalled("s-closed", "port the parser", "m", Recalled(task_recorded=True)),
            SessionRecalled("s-labelled", "a summary", "m", Recalled(task_recorded=False)),
        ],
        now=2.0,
    )
    run = _Overlays(monkeypatch, store)

    for session_id, title, is_label in (
        ("s-ran", "build the thing", False),
        ("s-closed", "port the parser", False),
        ("s-labelled", "a summary", True),
    ):
        sent = run.press(
            _picked_row(session_id, str(tmp_path)), "carry on", policy=Policy.PERMISSIVE
        )
        assert (sent.task, sent.task_is_label) == (title, is_label), session_id
        assert (sent.resume, sent.message) == (session_id, "carry on")
        assert sent.cwd == str(tmp_path.resolve()) and sent.policy is Policy.PERMISSIVE

    # A new session's spec has no record to be titled by, whatever the store holds.
    fresh = LaunchSpec(task="s-ran", model="m", cwd=str(tmp_path))
    run.app._launcher_pressed(run.state, fresh, run.state.launcher.draft())
    assert run.launched[-1][0][1] is fresh


def test_a_resume_titled_by_a_label_is_given_the_task_its_file_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A session removed from the fleet in an earlier run: no record, a transcript and
    a session file. New Task knows it by the CLI's label; the file knows its task,
    and that is what the session is announced under. The read is off the asyncio
    loop, and only a resume that has nothing but a label pays for it.
    """
    import dataclasses

    from pptmstr import sessionfile

    written = sessionfile.update("s-removed", {"in_fleet": False, "task": "port the parser"})
    assert isinstance(written, sessionfile.Written)
    read_on: list[str] = []
    real_read = sessionfile.read

    def read(session_id: str, *a: object, **k: object) -> object:
        read_on.append(threading.current_thread().name)
        return real_read(session_id, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(sessionfile, "read", read)

    def resume(session_id: str, **kw: object) -> AgentSession:
        spec = dataclasses.replace(_spec(resume=session_id, message="carry on"), **kw)  # type: ignore[arg-type]
        out = _run_launch(spec, monkeypatch)
        (session,) = out.sessions
        assert out.pool_thread is not None and out.pool_thread.name not in read_on
        return session

    session = resume("s-removed", task="the CLI's label", task_is_label=True)
    assert (session.task, session.task_is_label) == ("port the parser", False)
    assert (session.resume, session.message) == ("s-removed", "carry on")
    assert len(read_on) == 1

    # No file, or a file with no task: the label stands and is still called one.
    session = resume("s-foreign", task="the CLI's label", task_is_label=True)
    assert (session.task, session.task_is_label) == ("the CLI's label", True)
    assert isinstance(sessionfile.update("s-blank", {"in_fleet": True}), sessionfile.Written)
    session = resume("s-blank", task="the CLI's label", task_is_label=True)
    assert (session.task, session.task_is_label) == ("the CLI's label", True)

    # A title that is a task is not second-guessed, and a new session reads nothing.
    before = len(read_on)
    session = resume("s-removed", task="what its card says")
    assert (session.task, session.task_is_label) == ("what its card says", False)
    out = _run_launch(_spec(task_is_label=True), monkeypatch)
    assert [s.task for s in out.sessions] == ["do a thing"]
    assert len(read_on) == before


def test_a_resume_from_new_task_that_starts_nothing_comes_back_to_new_task_as_that_resume(
    tmp_path: Path,
) -> None:
    """
    The press clears the box and the pick. A refusal read on a later frame puts
    both back, in New Task and not on a resume form: the modal that was pressed is
    the one that says why, and a retry from it resumes the same session.
    """
    from pptmstr import app

    row = _picked_row("s-1", str(tmp_path))
    # No pool: the refusal ``_launch`` makes without constructing a session.
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    draft = state.launcher
    draft.cwd, draft.task = str(tmp_path), "carry on"
    draft.picker.pick(row, draft.cwd)  # type: ignore[arg-type]
    assert draft.launch_hold() is None
    spec = draft.press()
    assert spec is not None and spec.resume == "s-1"

    app._launcher_pressed(state, spec, draft.draft())
    draft.launched()
    assert draft.picker.picked is None and draft.task == ""
    app._reap_launches(state)

    assert draft.task == "carry on" and draft.picker.picked == row
    assert [failure.reason for failure in draft.failures] == [
        "there is no session pool to start it in"
    ]
    assert draft._open_requested, "New Task is what comes back up"
    assert state.resume.form is None and not state.resume.busy
    again = draft.press()
    assert again is not None and (again.resume, again.message) == ("s-1", "carry on")


def test_in_dear_imgui_a_session_picked_in_new_task_is_resumed_there_and_no_form_opens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The operator's correction, end to end: both modals drawn by ``_draw_overlays``
    in a real Dear ImGui context. With a session picked New Task stays up and the
    resume form does not come up, on that frame or any after it; Ctrl+Enter then
    resumes that session with what is in the box, and the form still has not been
    asked for. The click that makes the pick is ``tests/test_launcher.py``'s.
    """
    from imgui_bundle import imgui

    from pptmstr import app
    from pptmstr.intents import SessionRecalled
    from pptmstr.model import Recalled
    from pptmstr.ui import launcher, resume_form

    store = Store()
    store.apply(
        SessionRecalled(
            "s-1", "build it", "m", Recalled(task_recorded=True), cwd=str(tmp_path), template="solo"
        ),
        now=1.0,
    )
    row = _picked_row("s-1", str(tmp_path))
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
    _no_settings_dialog(monkeypatch)
    monkeypatch.setattr(
        launcher, "draw", functools.partial(launcher.draw, scanner=lambda cwd: ((row,), {}))
    )
    monkeypatch.setattr(cli_version, "check_installed_cli", lambda *a, **k: _MEETS)
    launched: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(app, "_launch", lambda *a, **k: launched.append((a, k)))

    class _IdlePool:
        running_count = 0
        queued_count = 0
        cap = 4

        def session_for(self, node: object) -> None:
            return None

        def has_run(self, node: object) -> bool:
            return False

    state = app.AppState(store=store, bridge=Bridge(), settings=Settings())
    state.pool = _IdlePool()  # type: ignore[assignment]
    state.frame_snap = store.snapshot()
    monkeypatch.setattr(state.catalog, "request", lambda: None)
    state.launcher.cwd = str(tmp_path)
    state.launcher.task = "half a thought"

    open_popups: dict[str, bool] = {}

    def drawn(title: str) -> bool:
        """Dear ImGui holds the popup open and drew its window on the last frame."""
        window = imgui.internal.find_window_by_name(title)
        return open_popups[title] and window is not None and bool(window.active)

    def frames(count: int) -> None:
        for _ in range(count):
            imgui.new_frame()
            app._draw_overlays(state)
            # Asked inside the frame, at the level both modals are opened from.
            for title in (launcher.TITLE, resume_form.TITLE):
                open_popups[title] = bool(imgui.is_popup_open(title))
            imgui.end_frame()

    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(1280.0, 900.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")
        io.add_focus_event(True)
        state.launcher.request_open()
        frames(3)
        assert drawn(launcher.TITLE) and not drawn(resume_form.TITLE)

        state.launcher.picker.pick(row, state.launcher.cwd)  # type: ignore[arg-type]
        frames(5)

        assert open_popups[launcher.TITLE] and drawn(launcher.TITLE)
        assert state.launcher.is_open, "New Task stays open with the session picked"
        assert not open_popups[resume_form.TITLE] and not drawn(resume_form.TITLE)
        assert state.resume.form is None and not state.resume.busy
        assert state.launcher.task == "half a thought" and launched == []

        keys = (imgui.Key.mod_ctrl, imgui.Key.left_ctrl, imgui.Key.enter)
        for key in keys:
            io.add_key_event(key, True)
        frames(1)
        for key in reversed(keys):
            io.add_key_event(key, False)
        frames(3)

        ((args, kwargs),) = launched
        spec = args[1]
        assert isinstance(spec, LaunchSpec)
        assert (spec.resume, spec.message) == ("s-1", "half a thought")
        assert (spec.task, spec.task_is_label) == ("build it", False), "the card's title"
        assert spec.cwd == str(tmp_path.resolve())
        assert kwargs == {}
        assert not state.launcher.is_open and state.launcher.picker.picked is None
        assert state.resume.form is None and not drawn(resume_form.TITLE)
    finally:
        imgui.destroy_context(context)


def test_the_menu_bar_spells_a_second_step_the_way_the_card_menu_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    An item that opens something more ends in the ellipsis character: the card
    menu's ``Resume…`` is ``widgets.RESUME_ITEM``, which five panes point at, and
    the menu bar's own such items, and a closed card's as the bar shows them, are
    spelled with the same character, at the end, and not with three dots.
    """
    from pptmstr import app
    from pptmstr.intents import AgentFinished, AgentSpawned
    from pptmstr.model import AgentState
    from pptmstr.ui import session_menu, widgets
    from pptmstr.ui.focus import OnNode

    assert session_menu.RESUME_LABEL == widgets.RESUME_ITEM == "Resume…"
    root = ("s1", None)
    store = Store()
    store.apply(
        AgentSpawned(node_id=root, parent=None, task="t", model="m", started_at=0.0, cwd="/x"),
        now=1.0,
    )
    store.apply(AgentFinished(root, AgentState.DONE, 2.0), now=2.0)
    state = app.AppState(store=store, bridge=Bridge(), settings=Settings())
    state.frame_snap = store.snapshot()
    state.focus.target = OnNode(root, pinned=True)

    labels = list(_draw_menus(monkeypatch, state, "").items)
    assert {"New task…", "Settings…", "Resume…"} <= set(labels)
    assert not [label for label in labels if "..." in label]
    assert all(label.endswith("…") == ("…" in label) for label in labels)


# -- what the review of the fleet wiring found ------------------------------------------

_LABEL = "the CLI's summary of the conversation"


def _labelled_row(session_id: str) -> object:
    import dataclasses

    return dataclasses.replace(_row(session_id), summary=_LABEL)  # type: ignore[type-var]


def _closed_card_with_no_recorded_task(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """
    A closed card of a session first run under a build that wrote no session
    file: its file has no ``task``, and the card is titled from the CLI's label.
    """
    import json

    from pptmstr import sessionfile

    session_id = _ran_and_closed()
    path = sessionfile.file_path(session_id)
    assert path is not None
    held = json.loads(path.read_text())
    assert held.pop("task") == "build the thing"
    path.write_text(json.dumps(held))
    state = _started(monkeypatch, lambda: [_labelled_row(session_id)])
    card = state.store.snapshot().nodes[(session_id, None)]
    assert card.task == _LABEL and card.recalled.task_recorded is False
    return session_id, state


def test_resuming_a_card_with_no_recorded_task_keeps_its_title_and_fork_and_retry_stay_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The hazard is the typed message becoming the session's task: the card is
    retitled by "ok, carry on", the record that replaces the recalled one says
    nothing of its title being a label, and one click on Fork or Retry starts a
    new paid session with "ok, carry on" as its prompt. So the resume is made
    through the form with a message, and each surface is asked afterwards.
    """
    import time

    from pptmstr.intents import AgentFinished
    from pptmstr.model import AgentState
    from pptmstr.ui import health, session_menu

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    session_id, state = _closed_card_with_no_recorded_task(monkeypatch)
    node = (session_id, None)

    from pptmstr import app

    app._card_actions(state).resume(state.store.snapshot().nodes[node])
    with _Process(None, None, state=state) as second:
        pressed = _press(state, session_id, "ok, carry on")
        assert pressed.spec.message == "ok, carry on" and pressed.spec.task == _LABEL
        app._resume_pressed(state, pressed)
        for entry in state.launches:
            entry.future.exception(timeout=5.0)
        second.bridge.submit(_nothing()).result(timeout=5.0)
        second.frame()
        assert second.sessions[node].message == "ok, carry on"
        _goes_live(second, session_id)

        resumed = state.store.snapshot().nodes[node]
        assert resumed.recalled is None, "the announce built a fresh record"
        assert resumed.task == _LABEL, "the card keeps the title it had"
        assert session_menu.task_missing(resumed)
        assert health.fork_refusal(resumed) == session_menu.TASK_NOT_RECORDED
        fork = session_menu.find(
            session_menu.entries(resumed, Policy.STRICT), session_menu.Action.FORK
        )
        assert fork is not None and fork.refused == session_menu.TASK_NOT_RECORDED

        # It fails: the card menu's Retry is greyed, and the failure row's and
        # HEALTH's buttons, which hand over a spec with no question asked of the
        # record, start nothing.
        second.bridge.emit(AgentFinished(node, AgentState.FAILED, time.monotonic(), error="x"))
        second.frame()
        failed = state.store.snapshot().nodes[node]
        offered = session_menu.entries(failed, Policy.STRICT)
        for action in (session_menu.Action.RETRY, session_menu.Action.FORK):
            item = session_menu.find(offered, action)
            assert item is not None and item.refused == session_menu.TASK_NOT_RECORDED
        state.frame_snap = state.store.snapshot()
        spec = LaunchSpec.from_record(failed)
        app._reap_launches(state)
        assert state.launches == []
        app._relaunch(state, spec)
        app._fork(state, spec)
        assert state.launches == [] and list(second.sessions) == [node]
        assert state.not_done == [
            f"not relaunched: {session_menu.TASK_NOT_RECORDED}",
            f"not forked: {session_menu.TASK_NOT_RECORDED}",
        ]
    # The file is told no task by a resume, so the next start reads a label again.
    assert _file(session_id).task == ""
    again = _started(monkeypatch, lambda: [_labelled_row(session_id)])
    card = again.store.snapshot().nodes[node]
    assert card.task == _LABEL and card.recalled.task_recorded is False
    _shut(again)


_UP = ("nothing", "new task", "details", "not done", "another form")


@pytest.mark.parametrize("up", _UP)
def test_in_dear_imgui_a_resume_that_started_nothing_is_told_without_closing_what_is_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, up: str
) -> None:
    """
    A resume is refused seconds after its press (the CLI version read), and the
    operator has moved on. Two hazards. Asked for at root level over another
    modal, the form *closes* that modal: Dear ImGui holds one popup per level,
    and this is run against it because nothing else can show it. And with
    another session's form up, the failure is appended to a form nobody is
    looking at and nothing is said at all.

    So: whatever is up stays up with what it held, the failed message is on its
    own session's form, and the operator is told by a notice that waits its turn.
    """
    import concurrent.futures

    from imgui_bundle import imgui

    from pptmstr import app
    from pptmstr.intents import SessionRecalled
    from pptmstr.model import Recalled
    from pptmstr.ui import launcher, resume_form, session_details

    store = Store()
    for session_id, task in (("s-a", "build it"), ("s-b", "review it")):
        store.apply(
            SessionRecalled(
                session_id,
                task,
                "m",
                Recalled(task_recorded=True),
                cwd=str(tmp_path),
                template="solo",
            ),
            now=1.0,
        )
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
    _no_settings_dialog(monkeypatch)
    monkeypatch.setattr(launcher, "scan_sessions", lambda cwd: ((), {}))
    monkeypatch.setattr(cli_version, "check_installed_cli", lambda *a, **k: _MEETS)
    monkeypatch.setattr(app, "_launch", lambda *a, **k: pytest.fail("something was launched"))

    class _IdlePool:
        running_count = 0
        queued_count = 0
        cap = 4

        def session_for(self, node: object) -> None:
            return None

        def has_run(self, node: object) -> bool:
            return False

    state = app.AppState(store=store, bridge=Bridge(), settings=Settings())
    state.pool = _IdlePool()  # type: ignore[assignment]
    state.frame_snap = store.snapshot()
    monkeypatch.setattr(state.catalog, "request", lambda: None)
    state.launcher.cwd = str(tmp_path)
    state.launcher.task = "half a thought"

    # A's form was pressed with a message and has closed, as ``draw`` leaves it.
    app._open_resume(state, state.frame_snap.nodes[("s-a", None)])
    form_a = state.resume.forms["s-a"]
    form_a.message = "try again, slowly"
    draft = form_a.draft()
    form_a.launched()
    state.resume.close()

    titles = (launcher.TITLE, resume_form.TITLE, session_details.TITLE, app.NOT_DONE_TITLE)
    open_popups: dict[str, bool] = {}

    def frames(count: int) -> None:
        for _ in range(count):
            imgui.new_frame()
            app._draw_overlays(state)
            for title in titles:
                open_popups[title] = bool(imgui.is_popup_open(title))
            imgui.end_frame()

    def up_now() -> list[str]:
        return [title for title in titles if open_popups[title]]

    def escape() -> None:
        io.add_key_event(imgui.Key.escape, True)
        frames(1)
        io.add_key_event(imgui.Key.escape, False)
        frames(3)

    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(1280.0, 900.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")
        io.add_focus_event(True)
        frames(2)
        assert up_now() == []

        if up == "new task":
            state.launcher.request_open()
            held = launcher.TITLE
        elif up == "details":
            state.cards.details.ask(("s-b", None))
            held = session_details.TITLE
        elif up == "not done":
            app._not_done(state, "not forked: something else")
            held = app.NOT_DONE_TITLE
        elif up == "another form":
            app._open_resume(state, state.frame_snap.nodes[("s-b", None)])
            state.resume.forms["s-b"].message = "B's half a thought"
            held = resume_form.TITLE
        else:
            held = None
        frames(4)
        assert up_now() == ([] if held is None else [held])

        refused: concurrent.futures.Future[object] = concurrent.futures.Future()
        refused.set_result(app.LaunchRefused("this CLI cannot contain the session"))
        state.launches.append(app._InFlight(refused, None, "resume", ("s-a", draft)))  # type: ignore[arg-type]
        frames(5)

        # The message is back on its own session's form whatever else is true.
        assert [f.reason for f in form_a.failures] == ["this CLI cannot contain the session"]
        assert form_a.message == "try again, slowly"
        told = [line for line in state.not_done if "this CLI cannot contain the session" in line]

        if held is None:
            # Nothing was in the way: the form is the telling, and at once.
            assert up_now() == [resume_form.TITLE] and state.resume.showing == "s-a"
            assert told == []
            return

        assert up_now() == [held], "what the operator was in stays up"
        assert state.launcher.task == "half a thought"
        assert len(told) == 1 and "build it" in told[0], state.not_done
        if up == "another form":
            assert state.resume.showing == "s-b" and state.resume.is_open
            assert state.resume.forms["s-b"].message == "B's half a thought"
        if up == "not done":
            assert state.not_done[0] == "not forked: something else"
            return

        # What was up is put away, and the notice takes its turn.
        escape()
        assert up_now() == [app.NOT_DONE_TITLE], (up, up_now())
        assert told[0] in state.not_done
        escape()
        assert up_now() == [] and state.not_done == []
        assert state.launcher.task == "half a thought"
        assert state.resume.forms["s-a"].message == "try again, slowly"
    finally:
        imgui.destroy_context(context)


def test_every_modal_of_the_applications_counts_as_in_the_way_of_a_notice() -> None:
    """
    ``_form_up`` is the list of things a root-level popup would close. A modal
    left off it is one a late refusal takes down, so each is set alone and asked.
    """
    from pptmstr import app
    from pptmstr.ui import session_menu

    def fresh():  # type: ignore[no-untyped-def]
        return app.AppState(store=Store(), bridge=Bridge(), settings=Settings())

    state = fresh()
    assert not app._form_up(state)

    state = fresh()
    state.launcher.is_open = True
    assert app._form_up(state)

    state = fresh()
    state.cards.details.ask(("s-1", None))
    assert app._form_up(state)

    state = fresh()
    state.cards.confirming = ("s-1", None)
    assert app._form_up(state)

    state = fresh()
    state.cards.gating = session_menu.GateAsk(
        ("s-1", None),
        session_menu.Action.SET_GATE,
        Policy.AUTONOMOUS,
        "label",
        "what it does",
        True,
    )
    assert app._form_up(state)

    # A resume form on screen, or asked for and not yet drawn; not the form of
    # the session the question is asked for.
    state = fresh()
    state.resume.open_for(
        session_id="s-1",
        cwd="/tmp",
        task="t",
        template=None,
        policy=Policy.STRICT,
        contained=False,
        model="m",
        subagent_cap=None,
        brief=None,
        last_message=None,
    )
    assert app._form_up(state) and not app._form_up(state, but="s-1")
    assert app._form_up(state, but="s-2")
    state.resume.close()
    assert not app._form_up(state)
    state.resume.is_open = True
    assert app._form_up(state) and not app._form_up(state, but="s-1")


def test_the_resume_form_warns_of_a_failed_session_whose_process_is_still_attached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    An error result leaves the client connected, the root FAILED. The snapshot
    calls that session ended, so asked of the snapshot alone the form says
    nothing, and the press ends a process the operator was not told about.
    """
    import inspect

    from pptmstr.ui import launcher
    from pptmstr.ui.resume_form import _row as form_row

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        session_id = run.session_id
        state = run.state
        state.frame_snap = state.store.snapshot()
        assert run.app._resume_ends(state, None) == frozenset({session_id}), "the snapshot says"

        _fails(run, "HTTP 529: overloaded")
        state.frame_snap = state.store.snapshot()
        failed = state.frame_snap.nodes[(session_id, None)]
        assert run.app._live_sessions(state.frame_snap) == frozenset()
        # No form on the session and no pick of it, nothing to warn on.
        assert run.app._resume_ends(state, state.resume.showing) == frozenset()
        # Picked in New Task, it is warned about there as it is on the form.
        assert run.app._resume_ends(state, session_id) == frozenset({session_id})

        run.app._open_resume(state, failed)
        form = state.resume.form
        assert form is not None
        ends = run.app._resume_ends(state, state.resume.showing)
        assert ends == frozenset({session_id})
        line = launcher.live_resume_line(form_row(form.facts), ends)
        # The sentence as it stands today: sub-agents end with the run.
        assert line is not None and "the ones still working are cancelled" in line
        assert "left as they are" not in line

        # Its process gone, a resume ends nothing and the form says nothing.
        run.running.discard(session_id)
        assert run.app._resume_ends(state, state.resume.showing) == frozenset()
        assert run.app._resume_ends(state, session_id) == frozenset()
    overlays = inspect.getsource(run.app._draw_overlays)
    assert "live=_resume_ends(state, state.resume.showing)" in overlays
    assert "_resume_ends(state, state.launcher.picker.selected)" in overlays


def test_a_gate_moved_while_the_launch_write_is_being_made_is_what_the_file_ends_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The launch write is made on the asyncio thread from the gate the session
    holds at that moment; a move is made and queued on the draw thread. The
    hazard is the read landing before the move and its queueing after: the
    writer keeps the later value, which is then the older rung, under a session
    running on the newer one.

    The move is made here at exactly that point -- after the gate has been read
    for the launch write, before the write is queued -- from another thread.
    """
    import threading

    from pptmstr import fleetsync

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        node = (run.session_id, None)
        session = run.sessions[node]
        assert session.policy is Policy.STRICT
        movers: list[threading.Thread] = []
        real = fleetsync.launched

        def launched(spec, session_id, policy_granted, *args, **kwargs):  # type: ignore[no-untyped-def]
            # ``policy_granted`` has been read. The move either cannot begin
            # until this write is queued, or it is made right here.
            mover = threading.Thread(
                target=lambda: run.app._set_policy(run.state, node, Policy.PERMISSIVE)
            )
            movers.append(mover)
            mover.start()
            mover.join(0.2)
            return real(spec, session_id, policy_granted, *args, **kwargs)

        monkeypatch.setattr(fleetsync, "launched", launched)
        _goes_live(run)
        for mover in movers:
            mover.join(5.0)
        run.frame()
        assert len(movers) == 1 and session.policy is Policy.PERMISSIVE
        assert _file(run.session_id).policy is Policy.PERMISSIVE


def test_a_gate_moved_on_a_session_whose_run_is_over_is_not_written_to_its_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The pool keeps the object of a session whose run ended by itself, and
    HEALTH's narrow reaches it. The file's gate is the rung the session last
    ran under -- what its closed card and its resume form show -- and narrowing
    an object nothing runs behind would rewrite that to strict.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with _Process(None, None) as run:
        run.launch()
        run.frame()
        _goes_live(run)
        node = (run.session_id, None)
        run.app._set_policy(run.state, node, Policy.PERMISSIVE)
        run.frame()
        assert _file(run.session_id).policy is Policy.PERMISSIVE, "a running session's move is"

        _ends(run)
        run.app._revoke_policy(run.state, node)
        run.frame()
        assert run.sessions[node].policy is Policy.STRICT, "the object was narrowed"
        assert _file(run.session_id).policy is Policy.PERMISSIVE


def test_a_card_with_no_recorded_directory_is_resumed_from_its_row_in_new_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The card's own Resume… has nowhere to start it and holds, and says where it
    can be resumed. New Task's session list is the CLI's listing, which says where
    the conversation ran: picked there, New Task resumes it in the row's directory,
    and in its own working directory field for a row that says nowhere.
    """
    from pptmstr import app
    from pptmstr.intents import SessionRecalled
    from pptmstr.model import Recalled
    from pptmstr.ui import launcher, resume_form

    store = Store()
    store.apply(
        SessionRecalled("s-nowhere", "build it", "m", Recalled(task_recorded=True)), now=1.0
    )
    run = _Overlays(monkeypatch, store)
    nowhere = store.snapshot().nodes[("s-nowhere", None)]

    app._card_actions(run.state).resume(nowhere)
    form = run.state.resume.form
    assert form is not None and form.facts.cwd == ""
    form.message = "carry on"
    form.refresh()
    held = resume_form.press(form, launcher.VersionGate())
    assert held == resume_form.Hold(resume_form.HOLD_UNRECORDED)
    assert "New Task's session list can resume it" in resume_form.HOLD_UNRECORDED
    run.state.resume.close()

    sent = run.press(_picked_row("s-nowhere", str(tmp_path)))
    assert (sent.resume, sent.cwd) == ("s-nowhere", str(tmp_path.resolve()))
    assert (sent.task, sent.task_is_label) == ("build it", False)

    elsewhere = tmp_path / "typed"
    elsewhere.mkdir()
    sent = run.press(_picked_row("s-nowhere", None), cwd=str(elsewhere))
    assert (sent.resume, sent.cwd) == ("s-nowhere", str(elsewhere.resolve()))
    assert run.state.resume.forms.keys() == {"s-nowhere"}, "no form was opened by either"


# -- break-glass commands: the shell runs what the store orders -------------------------
#
# Real here: the bridge, the store, ``app.apply_intents``, the rows' three actions,
# ``app.CommandFiles``, ``commandrun.Runner`` and the files under ``tmp_path``. Not
# real: the shell. It is the runner tests' fake -- a script that writes its own
# argument vector to a file and becomes ``/bin/sh -c "$3"`` -- so no profile is read
# and what the shell was handed is on disk to compare byte for byte. Nothing here
# was asked for by a model, and no row of it was drawn.

_CS = "s-cmd"
_CROOT = (_CS, None)


@pytest.fixture
def command_mark():  # type: ignore[no-untyped-def]
    """
    A mark for the environment of every process a test starts, and the check
    that nothing carrying it, and no new child of this process, outlives the
    test. The runner tests' own check, asked for by name here.
    """
    import os
    import signal
    import time
    import uuid

    from tests.test_commandrun import _children, _marked

    before = _children()
    value = uuid.uuid4().hex
    yield value
    deadline = time.monotonic() + 3.0
    while (_marked(value) or _children() - before) and time.monotonic() < deadline:
        time.sleep(0.02)
    left, children = _marked(value), _children() - before
    for pid in left:
        os.kill(pid, signal.SIGKILL)
    assert not left, f"processes left running: {left}"
    assert not children, f"children of the test process left: {children}"


class _Commands:
    """
    One window as far as a command is concerned: a session with a directory, a
    runner over a fake shell, and the writer of the records the runner does not
    write. ``fake`` is the scripted driver's process, which has neither.
    """

    def __init__(self, tmp_path: Path, mark: str, *, fake: bool = False, **options: object) -> None:
        import os

        from pptmstr import app, commandrun
        from pptmstr.intents import AgentSpawned, CommandEnded, StateChanged
        from pptmstr.model import AgentState
        from tests.test_commandrun import FAKE_SHELL, MARK

        self.app = app
        self.root = tmp_path / "sessions"
        self.cwd = tmp_path / "work tree"
        self.cwd.mkdir()
        self.argv_out = tmp_path / "argv"
        self.shell = tmp_path / "fake-shell"
        self.shell.write_text(FAKE_SHELL)
        self.shell.chmod(0o755)
        self.state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings(), fake=fake)
        self.state.command_host = app.CommandHost(str(self.shell), ())
        if not fake:
            self.state.command_files = app.CommandFiles(self.root)
            self.state.command_runner = commandrun.Runner(
                app._command_ended(self.state),
                root=self.root,
                environ={**os.environ, MARK: mark, "ARGV_OUT": str(self.argv_out)},
                **{"grace_s": 0.2, "drain_s": 0.2, **options},  # type: ignore[arg-type]
            )
        self.applied: list[object] = []
        drain = self.state.bridge.drain

        def noting(max_items: int = 10_000) -> list[object]:
            taken = drain(max_items)
            self.applied.extend(taken)
            return taken  # type: ignore[return-value]

        self.state.bridge.drain = noting  # type: ignore[method-assign,assignment]
        self._ended = CommandEnded
        self._serial = 0
        self.state.bridge.emit(
            AgentSpawned(
                node_id=_CROOT,
                parent=None,
                task="rotate the key",
                model="claude-opus-5",
                started_at=0.0,
                cwd=str(self.cwd),
            )
        )
        self.state.bridge.emit(StateChanged(_CROOT, AgentState.THINKING))
        self.frame()

    def frame(self) -> None:
        import time

        self.state.frame_now = time.monotonic()
        self.state.frame_wall = time.time()
        self.app.apply_intents(self.state)
        self.state.frame_snap = self.state.store.snapshot()
        self.app._check_for_overdue_commands(self.state)

    def until(self, done, within: float = 10.0) -> None:  # type: ignore[no-untyped-def]
        import time

        deadline = time.monotonic() + within
        while True:
            self.frame()
            if done():
                return
            assert time.monotonic() < deadline, "did not happen in time"
            time.sleep(0.01)

    def held(self, command_id: str):  # type: ignore[no-untyped-def]
        return self.state.store.snapshot().commands[command_id]

    def ask(self, text: str = "true", *, settle: bool = True) -> str:
        import time

        from pptmstr.command import Command
        from pptmstr.intents import CommandRequested

        self._serial += 1
        command_id = f"cmd-20261013T101500Z-{self._serial:012x}"
        asked = Command(
            id=command_id, asker=_CROOT, asked=text, reason="why", expects="what", asked_at=1.0
        )
        self.state.bridge.emit(CommandRequested(_CROOT, asked, None, time.time()))
        self.frame()
        if settle and self.state.command_files is not None:
            self.until(lambda: command_id not in self.state.command_pending)
        return command_id

    @property
    def actions(self):  # type: ignore[no-untyped-def]
        rows = self.app._command_rows(self.state)
        assert rows is not None and rows.wired is True
        return rows.actions

    def run(self, command_id: str, text: str, timeout_s: float = 600) -> None:
        self.actions.on_run(command_id, text, timeout_s)
        self.frame()

    def run_until_started(self, command_id: str, text: str) -> None:
        """Approve it and wait for the shell to exist: it writes its arguments first."""
        self.run(command_id, text)
        self.until(self.argv_out.exists)

    def ended(self, command_id: str):  # type: ignore[no-untyped-def]
        """Wait for the command's end to be applied, and hand it back."""
        self.until(lambda: self.held(command_id).end is not None)
        # Anything a second end would be is on the queue by now or never.
        self.frame()
        return self.held(command_id).end

    def ends_emitted(self, command_id: str) -> list[object]:
        return [
            i for i in self.applied if isinstance(i, self._ended) and i.command_id == command_id
        ]

    def directory(self, command_id: str) -> Path:
        return self.root / _CS / "commands" / command_id

    def files(self, command_id: str) -> list[str]:
        directory = self.directory(command_id)
        return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []

    def close(self) -> None:
        self.app._close_commands(self.state)


@pytest.fixture
def commands(tmp_path: Path, command_mark: str):  # type: ignore[no-untyped-def]
    window = _Commands(tmp_path, command_mark)
    yield window
    window.close()


def test_an_approved_command_reaches_the_shell_byte_for_byte_in_the_sessions_directory(
    commands: _Commands,
) -> None:
    """
    The property's positive half: the row's press, the store's order, the
    runner, one end. The string is the one pressed and not the one asked.
    """
    from pptmstr.command import CommandState, Exited

    text = "printf 'a  b\\n' ;  pwd  # café \"q\" $HOME\t-x"
    command_id = commands.ask("make install")
    assert commands.files(command_id) == ["request.json"]

    commands.run(command_id, text, 600)
    running = commands.held(command_id)
    assert running.state is CommandState.RUNNING
    assert (running.run.text, running.run.shell, running.run.cwd, running.run.timeout_s) == (
        text,
        str(commands.shell),
        str(commands.cwd),
        600,
    )
    end = commands.ended(command_id)

    assert end.outcome == Exited(0)
    assert end.tail == f"a  b\n{commands.cwd}\n"
    argv = commands.argv_out.read_bytes().split(b"\0")[:-1]
    assert argv == [str(commands.shell).encode(), b"-l", b"-c", text.encode()]
    assert len(commands.ends_emitted(command_id)) == 1
    assert commands.files(command_id) == [
        "end.json",
        "output.log",
        "request.json",
        "run.json",
        "started.json",
    ]
    assert commands.state.not_done == [] and commands.state.command_unrecorded == {}
    assert commands.state.command_handed == {}
    assert not any(isinstance(o, _owed()) for o in commands.state.store.snapshot().needs_you)


def _owed():  # type: ignore[no-untyped-def]
    from pptmstr.model import CommandOwed

    return CommandOwed


def test_a_command_that_fails_ends_once_with_its_exit_code(commands: _Commands) -> None:
    from pptmstr.command import Exited

    command_id = commands.ask()
    commands.run(command_id, "echo no >&2; exit 3")

    end = commands.ended(command_id)
    assert end.outcome == Exited(3) and end.tail == "no\n"
    assert len(commands.ends_emitted(command_id)) == 1


def test_a_command_that_outlives_its_timeout_is_killed_and_ends_once_as_timed_out(
    commands: _Commands, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptmstr.command import TimedOut

    # The reducer takes only the row's choices, and the shortest is two minutes.
    monkeypatch.setattr("pptmstr.model.TIMEOUT_CHOICES_S", (0.3, 600))
    command_id = commands.ask()
    commands.run(command_id, "sleep 30", 0.3)

    end = commands.ended(command_id)
    assert end.outcome == TimedOut()
    assert len(commands.ends_emitted(command_id)) == 1


def test_the_rows_cancel_kills_a_running_command_and_it_ends_once_as_cancelled(
    commands: _Commands,
) -> None:
    from pptmstr.command import Cancelled, CancelledBy

    command_id = commands.ask()
    commands.run_until_started(command_id, "sleep 30")
    assert commands.held(command_id).end is None

    commands.actions.on_stop(command_id)
    end = commands.ended(command_id)

    assert end.outcome == Cancelled(CancelledBy.OPERATOR)
    assert commands.held(command_id).stop_asked_at is not None
    assert len(commands.ends_emitted(command_id)) == 1
    assert commands.state.not_done == []


def test_a_running_command_can_still_be_cancelled_after_its_session_has_ended(
    commands: _Commands,
) -> None:
    """
    Closing a session does not stop its command, so the row has to outlive the
    session: it is the only place the cancel is.
    """
    import time

    from pptmstr.command import Cancelled, CancelledBy
    from pptmstr.intents import AgentFinished
    from pptmstr.model import AgentState

    command_id = commands.ask()
    commands.run_until_started(command_id, "sleep 30")
    commands.state.bridge.emit(AgentFinished(_CROOT, AgentState.DONE, time.monotonic()))
    commands.frame()

    snap = commands.state.store.snapshot()
    assert snap.commands[command_id].end is None
    assert [o.command.id for o in snap.needs_you if isinstance(o, _owed())] == [command_id]
    commands.actions.on_stop(command_id)
    assert commands.ended(command_id).outcome == Cancelled(CancelledBy.OPERATOR)


def test_an_order_the_runner_raises_on_is_ended_by_the_shell_and_never_lost(
    commands: _Commands, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptmstr import commandfile
    from pptmstr.command import NotStarted

    def refuses(command: object) -> None:
        raise RuntimeError("no slot")

    monkeypatch.setattr(commands.state.command_runner, "start", refuses)
    command_id = commands.ask()
    commands.run(command_id, "echo hi")

    end = commands.ended(command_id)
    assert end.outcome == NotStarted("the runner did not take it: RuntimeError: no slot")
    assert len(commands.ends_emitted(command_id)) == 1
    assert commands.state.not_done == [
        f"{command_id} was approved and did not start: the runner did not take it:"
        " RuntimeError: no slot"
    ]
    # On disk as an end with no run: nothing was handed to a shell.
    commands.until(lambda: "end.json" in commands.files(command_id))
    assert commands.files(command_id) == ["end.json", "request.json"]
    read = commandfile.read_command(_CS, command_id, commands.root)
    assert read.end.outcome == end.outcome and read.run is None  # type: ignore[union-attr]
    assert not commands.argv_out.exists()
    assert commands.state.command_handed == {}


def test_a_run_the_runner_cannot_record_starts_nothing_and_ends_once(commands: _Commands) -> None:
    """
    ``run.json`` follows ``request.json``. With the request gone from under the
    window the runner refuses to start anything, and says so as the end.
    """
    from pptmstr.command import NotStarted

    command_id = commands.ask()
    (commands.directory(command_id) / "request.json").unlink()
    commands.run(command_id, "echo hi")

    end = commands.ended(command_id)
    assert isinstance(end.outcome, NotStarted)
    assert end.outcome.reason.startswith("the run could not be recorded: no request.json")
    assert len(commands.ends_emitted(command_id)) == 1
    assert not commands.argv_out.exists()
    # The runner could not write the end either, and the row is told.
    commands.until(lambda: command_id in commands.state.command_unrecorded)
    assert commands.state.command_unrecorded[command_id].startswith(
        "how it ended is not recorded on disk: no request.json"
    )


def test_a_command_whose_request_never_reached_the_disk_is_not_sent_to_run(
    tmp_path: Path, command_mark: str
) -> None:
    """
    Decided at the row and not left to the runner's ``NotStarted``: the request
    stays asked, the operator is told why, and refusing it still works.
    """
    from pptmstr.command import CommandState, Refused
    from pptmstr.intents import CommandApproved

    window = _Commands(tmp_path, command_mark)
    window.root.write_text("a file where the sessions directory would be")
    command_id = window.ask()

    assert command_id in window.state.command_unrecorded
    assert window.state.command_unrecorded[command_id].startswith(
        "what was asked is not recorded on disk: "
    )
    rows = window.app._command_rows(window.state)
    assert rows.unrecorded[command_id] == window.state.command_unrecorded[command_id]

    window.run(command_id, "echo hi")
    assert window.held(command_id).state is CommandState.ASKED
    assert not any(isinstance(i, CommandApproved) for i in window.applied)
    assert len(window.state.not_done) == 1
    assert window.state.not_done[0].startswith(f"not run: {window.app.REQUEST_UNRECORDED}")

    window.actions.on_refuse(command_id, "no record, no run")
    window.frame()
    assert window.held(command_id).end.outcome == Refused("no record, no run")
    window.close()


def test_run_is_not_sent_while_the_request_is_still_on_its_way_to_the_disk(
    commands: _Commands, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from pptmstr import commandfile
    from pptmstr.command import CommandState, Exited

    gate = threading.Event()
    write = commandfile.write_request

    def slow(command: object, root: object = None) -> object:
        gate.wait(10.0)
        return write(command, root)  # type: ignore[arg-type]

    monkeypatch.setattr(commandfile, "write_request", slow)
    command_id = commands.ask(settle=False)
    assert command_id in commands.state.command_pending

    commands.run(command_id, "true")
    assert commands.held(command_id).state is CommandState.ASKED
    assert commands.state.not_done == [f"not run: {commands.app.REQUEST_PENDING}"]

    gate.set()
    commands.until(lambda: command_id not in commands.state.command_pending)
    commands.run(command_id, "true")
    assert commands.ended(command_id).outcome == Exited(0)


def test_under_the_fake_driver_an_approval_is_ended_at_once_as_never_started(
    tmp_path: Path, command_mark: str
) -> None:
    from pptmstr.command import NotStarted

    window = _Commands(tmp_path, command_mark, fake=True)
    assert window.state.command_runner is None and window.state.command_files is None
    command_id = window.ask()
    window.run(command_id, "echo hi")
    window.frame()

    end = window.held(command_id).end
    assert end.outcome == NotStarted(
        "this build is not running commands: the fake driver runs nothing on this machine"
    )
    assert len(window.ends_emitted(command_id)) == 1
    assert not window.root.exists() and not window.argv_out.exists()
    assert window.state.not_done[0].startswith(f"{command_id} was approved and did not start")
    window.close()


def test_a_real_start_has_a_runner_and_a_fake_one_has_none() -> None:
    from pptmstr import app, commandrun
    from pptmstr.settings import Loaded

    real = app._start(_args(), Loaded(Settings(board_log=False)), list_sessions=tuple)  # type: ignore[arg-type]
    assert isinstance(real.command_runner, commandrun.Runner)
    assert isinstance(real.command_files, app.CommandFiles)
    assert real.command_files.root == real.fleet_writer.root
    assert real.command_runner._root == real.fleet_writer.root
    fake = app._start(_args(fake=True), Loaded(Settings()))  # type: ignore[arg-type]
    assert fake.command_runner is None and fake.command_files is None


def test_with_no_shell_nothing_is_sent_and_the_operator_is_told_why(commands: _Commands) -> None:
    from pptmstr.command import CommandState, Refused
    from pptmstr.intents import CommandApproved

    commands.state.command_host = commands.app.CommandHost(None, (), no_shell="$SHELL is not set")
    command_id = commands.ask()
    assert commands.app._command_rows(commands.state).shell is None

    commands.run(command_id, "echo hi")
    assert commands.held(command_id).state is CommandState.ASKED
    assert not any(isinstance(i, CommandApproved) for i in commands.applied)
    assert commands.state.not_done == [
        "not run: there is no shell to give it to ($SHELL is not set)"
    ]

    commands.actions.on_refuse(command_id, "")
    commands.frame()
    assert commands.held(command_id).end.outcome == Refused("")


def test_a_shell_that_is_gone_by_the_time_it_is_needed_ends_the_command_as_not_started(
    commands: _Commands,
) -> None:
    from pptmstr.command import NotStarted

    command_id = commands.ask()
    commands.shell.unlink()
    commands.run(command_id, "echo hi")

    end = commands.ended(command_id)
    assert end.outcome == NotStarted(f"the shell {commands.shell} is not a file")
    assert len(commands.ends_emitted(command_id)) == 1


def test_a_session_with_no_directory_on_its_record_runs_nothing(
    tmp_path: Path, command_mark: str
) -> None:
    import time

    from pptmstr.command import Command, CommandState
    from pptmstr.intents import AgentSpawned, CommandRequested

    window = _Commands(tmp_path, command_mark)
    other = ("s-nowhere", None)
    window.state.bridge.emit(
        AgentSpawned(node_id=other, parent=None, task="t", model="m", started_at=0.0)
    )
    command_id = "cmd-20261013T101500Z-0000000000aa"
    asked = Command(id=command_id, asker=other, asked="x", reason="", expects="", asked_at=1.0)
    window.state.bridge.emit(CommandRequested(other, asked, None, time.time()))
    window.until(lambda: command_id in window.state.store.snapshot().commands)
    window.until(lambda: command_id not in window.state.command_pending)

    window.run(command_id, "echo hi")

    assert window.held(command_id).state is CommandState.ASKED
    assert window.state.not_done == [f"not run: {window.app.NO_SESSION_DIRECTORY}"]
    window.close()


def test_an_approval_the_store_would_not_apply_is_not_sent_and_says_why(
    commands: _Commands,
) -> None:
    """
    The reducer's arm is a silent no-op for each of these, so without the
    question asked first the operator presses run and nothing happens.
    """
    from pptmstr.command import CommandState, RequestRefusal
    from pptmstr.intents import CommandApproved
    from pptmstr.model import CommandRefusal

    first, second = commands.ask(), commands.ask()
    commands.run(first, "sleep 30")
    sent = [i for i in commands.applied if isinstance(i, CommandApproved)]
    assert len(sent) == 1

    commands.run(second, "echo hi")
    commands.run(second, "-x echo hi")
    commands.run(second, "echo hi", 601)
    commands.run(first, "echo again")
    commands.run("cmd-20261013T101500Z-ffffffffffff", "echo hi")

    assert [i for i in commands.applied if isinstance(i, CommandApproved)] == sent
    assert commands.held(second).state is CommandState.ASKED
    assert commands.state.not_done == [
        "not run: " + refusal.sentence
        for refusal in (
            CommandRefusal.ALREADY_RUNNING_IN_SESSION,
            RequestRefusal.LEADS_WITH_OPTION,
            CommandRefusal.BAD_TIMEOUT,
            CommandRefusal.NOT_ASKED,
            CommandRefusal.NO_SUCH_COMMAND,
        )
    ]
    commands.actions.on_stop(first)
    commands.ended(first)


def test_a_refusal_and_a_withdrawal_are_each_written_as_an_end_with_no_run(
    commands: _Commands,
) -> None:
    import time

    from pptmstr import commandfile
    from pptmstr.command import Refused, Withdrawn
    from pptmstr.intents import CommandWithdrawn

    refused, withdrawn = commands.ask(), commands.ask()

    commands.actions.on_refuse(refused, "not on\nthis machine")
    commands.state.bridge.emit(CommandWithdrawn(_CROOT, withdrawn, None, time.time()))
    commands.until(
        lambda: "end.json" in commands.files(refused) and "end.json" in commands.files(withdrawn)
    )

    for command_id, outcome in (
        (refused, Refused("not on\nthis machine")),
        (withdrawn, Withdrawn()),
    ):
        assert commands.files(command_id) == ["end.json", "request.json"]
        read = commandfile.read_command(_CS, command_id, commands.root)
        assert read.end.outcome == outcome and read.run is None  # type: ignore[union-attr]
        assert commands.held(command_id).end.outcome == outcome
    commands.frame()
    assert commands.state.command_unrecorded == {} and commands.state.not_done == []


def test_a_refusal_too_long_for_the_store_is_not_sent_and_not_cut(commands: _Commands) -> None:
    from pptmstr.command import CommandState
    from pptmstr.intents import CommandRefused
    from pptmstr.model import MAX_COMMAND_REFUSAL_CHARS

    command_id = commands.ask()

    commands.actions.on_refuse(command_id, "n" * (MAX_COMMAND_REFUSAL_CHARS + 7))
    commands.frame()

    assert commands.held(command_id).state is CommandState.ASKED
    assert not any(isinstance(i, CommandRefused) for i in commands.applied)
    assert len(commands.state.not_done) == 1
    assert commands.state.not_done[0].startswith("not refused: the reason is 7 characters over")

    commands.actions.on_refuse(command_id, "n" * MAX_COMMAND_REFUSAL_CHARS)
    commands.frame()
    assert commands.held(command_id).state is CommandState.ENDED


def test_a_press_on_a_command_that_has_moved_on_says_so(commands: _Commands) -> None:
    command_id = commands.ask()
    commands.actions.on_stop(command_id)
    commands.actions.on_refuse(command_id, "no")
    commands.frame()
    commands.actions.on_refuse(command_id, "no again")
    commands.actions.on_stop("cmd-20261013T101500Z-ffffffffffff")

    assert commands.state.not_done == [
        "not cancelled: it is not running",
        "not refused: it is no longer waiting for you",
        "not cancelled: it is not running",
    ]


def test_no_frame_touches_a_commands_files_or_waits_on_its_process(
    commands: _Commands, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Every write and read of a command's files is seen from inside
    ``commandfile``, with the thread it ran on; and a frame taken while a
    command sleeps returns at once.
    """
    import threading
    import time

    from pptmstr import commandfile
    from pptmstr.command import Cancelled, CancelledBy

    seen: list[tuple[str, int]] = []
    for name in ("write_request", "write_run", "write_end", "open_log", "restore"):
        real = getattr(commandfile, name)

        def noting(*args: object, _real: object = real, _name: str = name) -> object:
            seen.append((_name, threading.get_ident()))
            return _real(*args)  # type: ignore[operator]

        monkeypatch.setattr(commandfile, name, noting)

    draw = threading.get_ident()
    refused, ran = commands.ask(), commands.ask()
    commands.actions.on_refuse(refused, "no")
    commands.run_until_started(ran, "sleep 30")

    began = time.monotonic()
    for _ in range(20):
        commands.frame()
    assert time.monotonic() - began < 1.0
    assert commands.held(ran).end is None

    commands.actions.on_stop(ran)
    assert commands.ended(ran).outcome == Cancelled(CancelledBy.OPERATOR)
    commands.until(lambda: "end.json" in commands.files(refused))

    assert {name for name, _ in seen} == {"write_request", "write_run", "write_end", "open_log"}
    assert draw not in {thread for _, thread in seen}


def test_quitting_kills_what_is_running_and_the_file_says_how_it_ended(
    commands: _Commands,
) -> None:
    """
    No frame follows a quit, so the ``CommandEnded`` is never applied: the
    store still says running, and ``end.json`` is the record.
    """
    from pptmstr import commandfile
    from pptmstr.command import Cancelled, CancelledBy

    mark = LOG.snapshot()[1]
    command_id = commands.ask()
    asked_only = commands.ask()
    commands.run_until_started(command_id, "sleep 30 & sleep 30")

    commands.app._close_commands(commands.state)

    read = commandfile.read_command(_CS, command_id, commands.root)
    assert read.end.outcome == Cancelled(CancelledBy.SHUTDOWN)  # type: ignore[union-attr]
    assert commands.held(command_id).end is None
    assert commands.files(asked_only) == ["request.json"]
    said = [text for _, text in _fleet_log_since(mark, "command")]
    assert any("1 running command(s) were killed at exit" in text for text in said)


def test_the_quit_closes_the_commands_before_the_pool_and_survives_their_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptmstr import app

    order: list[str] = []
    bridge = _QuittingBridge(None)
    state = app.AppState(store=Store(), bridge=bridge, settings=Settings())  # type: ignore[arg-type]

    class _Runner:
        def close(self, timeout: float) -> tuple[object, ...]:
            order.append(f"runner {timeout}")
            raise RuntimeError("boom")

    class _Pool:
        shutdown_bound_s = 1.0

        async def shutdown(self) -> None:
            return None

    state.command_runner = _Runner()  # type: ignore[assignment]
    state.pool = _Pool()  # type: ignore[assignment]
    submit = bridge.submit
    monkeypatch.setattr(bridge, "submit", lambda coro: (order.append("pool"), submit(coro))[1])

    app._shut_down(state)

    assert order == [f"runner {app.commandrun.CLOSE_TIMEOUT}", "pool"]
    assert bridge.stopped == 1


def test_a_command_the_runner_never_ends_is_ended_by_the_shell_once_it_is_overdue(
    commands: _Commands, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The runner ends everything it takes. A worker that died is the case where
    it did not, and then nothing else would ever close the command.
    """
    import time

    from pptmstr.command import End, EndUnknown, Exited
    from pptmstr.intents import CommandEnded

    monkeypatch.setattr(commands.state.command_runner, "start", lambda command: None)
    command_id = commands.ask()
    commands.run(command_id, "echo hi", 600)
    due = commands.state.command_handed[command_id]
    assert (
        600 + commands.app.COMMAND_OVERDUE_S - 5
        < due - time.monotonic()
        <= (600 + commands.app.COMMAND_OVERDUE_S)
    )

    commands.app._check_for_overdue_commands(commands.state, now=due - 0.1)
    commands.frame()
    assert commands.held(command_id).end is None and commands.state.not_done == []

    commands.app._check_for_overdue_commands(commands.state, now=due)
    commands.app._check_for_overdue_commands(commands.state, now=due + 100)
    commands.frame()

    assert commands.held(command_id).end.outcome == EndUnknown()
    assert commands.state.command_handed == {}
    # This runner never took it, so there was nothing it could be told to kill.
    assert commands.state.not_done == [
        f"{command_id}: {commands.app.OVERDUE}. {commands.app.NOT_HELD}"
    ]
    # A runner that reports after all changes nothing: the command ended once.
    commands.state.bridge.emit(CommandEnded(command_id, End(outcome=Exited(0), at=2.0)))
    commands.frame()
    assert commands.held(command_id).end.outcome == EndUnknown()
    assert commands.files(command_id) == ["request.json"]


def test_every_frame_looks_for_an_overdue_command() -> None:
    import inspect

    from pptmstr import app

    assert "_check_for_overdue_commands(state)" in inspect.getsource(app.begin_frame)


def test_an_end_the_runner_could_not_write_is_reported_to_the_row(commands: _Commands) -> None:
    import dataclasses

    from pptmstr import commandfile, commandrun
    from pptmstr.command import End, Exited, Run

    command_id = commands.ask()
    commands.run(command_id, "true")
    commands.ended(command_id)
    over = dataclasses.replace(
        commands.held(command_id),
        run=Run("true", "/bin/sh", "/", 600, 1.0),
        end=End(outcome=Exited(0), at=2.0),
    )

    commands.app._command_ended(commands.state)(
        commandrun.Ended(over, commandfile.Unwritten("OSError: disk full"))
    )
    commands.frame()

    assert commands.state.command_unrecorded == {
        command_id: "how it ended is not recorded on disk: OSError: disk full"
    }
    # Said once: a second failure of the same command does not replace the first.
    commands.app._command_ended(commands.state)(
        commandrun.Ended(over, commandfile.Unwritten("another"))
    )
    commands.frame()
    assert "disk full" in commands.state.command_unrecorded[command_id]


def _commands_on_disk(root: Path | None, session_id: str) -> dict[str, str]:
    """
    Four command directories under a session, as an earlier process left them:
    one that ran and exited, one only asked, one approved with no end, and one
    whose request is not JSON.
    """
    import dataclasses

    from pptmstr import commandfile
    from pptmstr.command import Command, End, Exited, Run

    def asked(n: int) -> Command:
        return Command(
            id=f"cmd-20261012T09000{n}Z-{n:012x}",
            asker=(session_id, None),
            asked=f"make step-{n}",
            reason="",
            expects="",
            asked_at=1000.0 + n,
        )

    run = Run("make step", "/bin/sh", "/tmp", 600, 1100.0)
    exited = dataclasses.replace(asked(1), run=run)
    assert isinstance(commandfile.write_request(exited, root), commandfile.Written)
    assert isinstance(commandfile.write_run(exited, root), commandfile.Written)
    over = dataclasses.replace(exited, end=End(Exited(0), 1200.0, 3.0, 3, "ok\n"))
    assert isinstance(commandfile.write_end(over, root), commandfile.Written)
    lapsed = asked(2)
    assert isinstance(commandfile.write_request(lapsed, root), commandfile.Written)
    unknown = dataclasses.replace(asked(3), run=run)
    assert isinstance(commandfile.write_request(unknown, root), commandfile.Written)
    assert isinstance(commandfile.write_run(unknown, root), commandfile.Written)
    broken = asked(4)
    assert isinstance(commandfile.write_request(broken, root), commandfile.Written)
    directory = commandfile.command_dir(session_id, broken.id, root)
    assert directory is not None
    (directory / "request.json").chmod(0o600)
    (directory / "request.json").write_text("{ not json")
    return {"exited": exited.id, "lapsed": lapsed.id, "unknown": unknown.id, "broken": broken.id}


def test_a_start_reads_each_recalled_sessions_commands_back_ended_and_says_what_it_could_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptmstr import app
    from pptmstr.command import CommandState, EndUnknown, Exited, Lapsed

    session_id = _ran_and_closed()
    ids = _commands_on_disk(None, session_id)
    mark = LOG.snapshot()[1]

    state = _started(monkeypatch, lambda: [_row(session_id)])
    try:
        held = state.store.snapshot().commands
        assert set(held) == {ids["exited"], ids["lapsed"], ids["unknown"]}
        assert all(c.state is CommandState.ENDED for c in held.values())
        assert held[ids["exited"]].end.outcome == Exited(0)
        assert held[ids["lapsed"]].end.outcome == Lapsed()
        assert held[ids["unknown"]].end.outcome == EndUnknown()
        # Nothing a file holds owes a row, and none of it is handed to a runner.
        assert not any(isinstance(o, _owed()) for o in state.store.snapshot().needs_you)
        assert state.command_handed == {} and state.command_pending == set()
        assert state.command_restored == set(held)
        assert app._command_rows(state).restored == set(held)

        assert state.command_files.shortfalls == app.CommandShortfalls(unreadable=1)
        said = [text for _, text in _fleet_log_since(mark, "command")]
        assert any(ids["broken"] in text and "cannot be read" in text for text in said)
        assert app.startup_shortfalls(None, None, None, state.command_files.shortfalls) == (
            "at startup: 1 command record unreadable (see LOG)"
        )
        # Read back once: a second ask for the same session reads nothing.
        before = len(state.bridge.to_ui.queue)
        app._restore_commands(state, session_id)
        assert len(state.bridge.to_ui.queue) == before
        assert state.command_files.shortfalls == app.CommandShortfalls(unreadable=1)
    finally:
        _shut(state)


def test_the_shortfalls_segment_counts_each_kind_of_command_record_it_could_not_read() -> None:
    from pptmstr import app

    assert app.startup_shortfalls(None, None, None, app.CommandShortfalls()) is None
    assert app.startup_shortfalls(None, None, None, app.CommandShortfalls(2, 1, 3)) == (
        "at startup: 2 command records unreadable, 1 command record from a newer build,"
        " command records not all read (see LOG)"
    )


def test_a_resume_reads_the_sessions_commands_back_before_the_session_is_announced(
    tmp_path: Path,
) -> None:
    """
    A session that is on no card -- removed from the fleet, or picked from the
    CLI's list -- is read back when it is resumed, ahead of anything its run
    can ask.
    """
    from pptmstr import app
    from pptmstr.intents import AgentSpawned, CommandsRestored

    root = tmp_path / "sessions"
    ids = _commands_on_disk(root, "s-old")

    with _Process(None, None) as run:
        run.state.command_files = app.CommandFiles(root)
        run.launch(resume="s-old")
        run.frame()

        kinds = [type(i) for i in run.applied if isinstance(i, CommandsRestored | AgentSpawned)]
        assert kinds == [CommandsRestored, AgentSpawned]
        held = run.state.store.snapshot().commands
        assert set(held) == {ids["exited"], ids["lapsed"], ids["unknown"]}
        assert all(c.end is not None for c in held.values())
        assert run.state.command_restored == set(held)
        assert run.state.command_files.shortfalls.unreadable == 1

        # A launch that resumes nothing reads nothing back.
        assert run.state.command_files.claim_restore("s-old") is False


def test_a_command_asked_in_this_window_is_not_marked_as_read_back_from_a_file(
    commands: _Commands,
) -> None:
    command_id = commands.ask()
    commands.actions.on_refuse(command_id, "no")
    commands.frame()

    assert commands.state.command_restored == set()
    assert commands.app._command_rows(commands.state).restored == set()


def test_a_request_the_store_refuses_writes_no_file(commands: _Commands) -> None:
    """Derived from what the store holds afterwards, not from what was sent."""
    import time

    from pptmstr.command import Command
    from pptmstr.intents import CommandRequested

    command_id = "cmd-20261013T101500Z-0000000000bb"
    asked = Command(id=command_id, asker=_CROOT, asked="-rf /", reason="", expects="", asked_at=1.0)
    commands.state.bridge.emit(CommandRequested(_CROOT, asked, None, time.time()))
    commands.frame()
    commands.frame()

    assert command_id not in commands.state.store.snapshot().commands
    assert commands.state.command_pending == set() and not commands.root.exists()


def test_the_readme_states_the_runners_numbers_as_the_code_has_them() -> None:
    """
    The README's section for the operator spells numbers the code owns. Each
    is held to the constant here, so one cannot move without the other.
    """
    from pptmstr import app, command, commandfile, commandrun

    readme = (Path(app.__file__).parent.parent / "README.md").read_text()
    section = readme.split(
        "### A contained agent can ask you to run a command, and pptmstr runs it as you\n"
    )[1].split("\n## ")[0]
    flat = " ".join(section.split())

    assert command.TIMEOUT_CHOICES_S == (120, 600, 1_800, 7_200)
    assert command.DEFAULT_TIMEOUT_S == 600
    assert "2 minutes, 10 (the default), 30, or 2 hours" in flat
    assert commandrun.KILL_GRACE_S == 2.0 and "then has two seconds to finish" in flat
    assert commandrun.REAP_S == 5.0 and "still there five seconds after `SIGKILL`" in flat
    assert commandfile.MAX_LOG_BYTES == 8 << 20 and "up to 8 MiB" in flat
    assert commandfile.MAX_TAIL_FILE_BYTES == 64 << 10 and "the last 64 KiB" in flat
    for name in (
        commandfile.REQUEST_NAME,
        commandfile.RUN_NAME,
        commandfile.STARTED_NAME,
        commandfile.END_NAME,
        commandfile.UNCONFIRMED_NAME,
        commandfile.GONE_NAME,
        commandfile.LOG_NAME,
        commandfile.TAIL_NAME,
    ):
        assert f"| `{name}` |" in section
    # The two lines the row of a command left running gives, as the row spells them.
    assert "`kill -- -<group> …`" in flat and "STILL RUNNING, UNSUPERVISED" in flat
    assert "`ps -o pid,pgid,lstart,args -s <session>`" in flat
    assert "strace -f -e trace=openat,execve $SHELL -l -c 'true'" in section
    assert "sessions/<session id>/commands/<command id>/" in flat
    assert "nothing deletes them" in flat
    assert "permission rules do not apply" in flat


# -- a command and the things that can go wrong around it, with real processes ----


def test_an_overdue_command_is_killed_and_its_session_can_run_the_next(
    commands: _Commands,
) -> None:
    """
    The store ending a command the runner still holds used to leave the two
    disagreeing: the session free in one, taken in the other, the process alive.
    """
    import time

    from pptmstr import commandfile, commandrun
    from pptmstr.command import EndUnknown, Exited

    first = commands.ask()
    commands.run_until_started(first, "trap '' TERM; sleep 1000 & wait")
    commands.until(lambda: commandfile.read_started(_CS, first, commands.root) is not None)
    started = commandfile.read_started(_CS, first, commands.root)
    assert started is not None
    commands.until(lambda: len(commandrun.session_members(started.pid) or ()) == 2)

    due = commands.state.command_handed[first]
    commands.app._check_for_overdue_commands(commands.state, now=due)

    # Killed on this thread, with no grace: the owner has stopped waiting.
    deadline = time.monotonic() + 2
    while commandrun.session_members(started.pid) != ():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert commands.ended(first).outcome == EndUnknown()
    assert commands.state.not_done == [f"{first}: {commands.app.OVERDUE}. {commands.app.ABANDONED}"]
    commands.until(lambda: "end.json" in commands.files(first))
    read = commandfile.read_command(_CS, first, commands.root)
    assert read.end.outcome == EndUnknown()  # type: ignore[union-attr]

    commands.argv_out.unlink()
    second = commands.ask()
    commands.run(second, "echo next")
    assert commands.ended(second).outcome == Exited(0)


def test_a_command_found_alive_after_a_restart_is_shown_and_holds_its_session(
    commands: _Commands, command_mark: str
) -> None:
    """
    What a pptmstr that died mid-command leaves: a request, a run, the record
    of the process, no end, and the process. The next window finds it, says
    how to end it, runs nothing beside it, and lets go when it is gone.
    """
    import dataclasses
    import os
    import subprocess

    from pptmstr import commandfile, commandrun
    from pptmstr.command import Command, EndUnknown, Exited, Run, Survivor
    from pptmstr.model import CommandRefusal
    from pptmstr.ui import inbox
    from tests.test_commandrun import MARK

    orphan = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 1000 & wait"],
        start_new_session=True,
        env={**os.environ, MARK: command_mark},
    )
    try:
        old = "cmd-20261012T101500Z-00000000dead"
        record = Command(
            id=old,
            asker=_CROOT,
            asked="make install",
            reason="",
            expects="",
            asked_at=1.0,
            run=Run("make install", str(commands.shell), str(commands.cwd), 600, 2.0),
        )
        assert isinstance(commandfile.write_request(record, commands.root), commandfile.Written)
        assert isinstance(commandfile.write_run(record, commands.root), commandfile.Written)
        started = commandrun.started_now(orphan.pid, at=2.5)
        commandfile.write_started(_CS, old, started, commands.root)
        commands.until(lambda: len(commandrun.session_members(orphan.pid) or ()) == 2)

        commands.app._restore_commands(commands.state, _CS)
        commands.frame()

        found = Survivor(pid=orphan.pid, pgid=orphan.pid, members=2, groups=(orphan.pid,))
        held = commands.held(old)
        assert held.end.outcome == EndUnknown(survivor=found)
        # Ended as a record: there is nothing to approve or to cancel here.
        assert not held.is_open and commands.state.command_handed == {}
        snap = commands.state.store.snapshot()
        (owed,) = [o for o in snap.needs_you if isinstance(o, _owed())]
        assert owed.command.id == old
        assert owed.summary.startswith("command STILL RUNNING, unsupervised, from ")
        assert f"its shell's session {orphan.pid}" in owed.summary
        lines = inbox.survivor_lines(found)
        assert f"kill -- -{orphan.pid}" in lines and lines[0] == "STILL RUNNING, UNSUPERVISED."

        # Nothing else of the session runs beside it, and the press says why.
        blocked = commands.ask()
        commands.run(blocked, "echo beside")
        assert commands.held(blocked).run is None
        assert commands.state.not_done == [
            commands.app.NOT_RUN + CommandRefusal.ALREADY_RUNNING_IN_SESSION.sentence
        ]
        assert not commands.argv_out.exists()

        def looked() -> bool:
            commands.app._read_survivor_checks(commands.state)
            return old in commands.state.command_checked

        rows = commands.app._command_rows(commands.state)
        rows.on_check(old)
        commands.until(looked)
        assert commands.state.command_checked[old].startswith(commands.app.STILL_THERE)
        assert commands.held(old).unsupervised == found

        # The operator runs the line the row gives.
        kill = commandrun.kill_line(found)
        assert subprocess.run(["/bin/sh", "-c", kill], check=False).returncode == 0
        orphan.wait(timeout=5)
        commands.until(lambda: commandrun.session_members(orphan.pid) == ())
        del commands.state.command_checked[old]
        rows.on_check(old)
        commands.until(looked)
        assert commands.state.command_checked[old].startswith(commands.app.GONE)
        commands.frame()

        assert commands.held(old).end == dataclasses.replace(held.end, outcome=EndUnknown())
        snap = commands.state.store.snapshot()
        assert [o.command.id for o in snap.needs_you if isinstance(o, _owed())] == [blocked]
        commands.run(blocked, "echo beside")
        assert commands.ended(blocked).outcome == Exited(0)
        # Looking again wrote no end: how it ended is still not known. What it
        # wrote is that the process was seen gone, so no later start asks.
        assert commands.files(old) == ["gone.json", "request.json", "run.json", "started.json"]
        assert commandfile.read_gone(_CS, old, commands.root) == commandrun.NOTHING_ALIVE
    finally:
        try:
            os.killpg(orphan.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        orphan.wait()


def test_a_run_with_no_end_and_nothing_alive_reads_back_as_before(
    commands: _Commands,
) -> None:
    from pptmstr import commandfile
    from pptmstr.command import Command, EndUnknown, Run, Started

    old = "cmd-20261012T101500Z-00000000dead"
    record = Command(
        id=old,
        asker=_CROOT,
        asked="true",
        reason="",
        expects="",
        asked_at=1.0,
        run=Run("true", str(commands.shell), str(commands.cwd), 600, 2.0),
    )
    commandfile.write_request(record, commands.root)
    commandfile.write_run(record, commands.root)
    # This test's own pid and group, under a start time that is not its own:
    # the number is alive, and it is not the process the record names.
    me = os.getpid()
    commandfile.write_started(
        _CS,
        old,
        Started(pid=me, pgid=os.getpgid(me), at=2.5, start_ticks=1, boot_id="x"),
        commands.root,
    )
    commands.app._restore_commands(commands.state, _CS)
    commands.frame()
    assert commands.held(old).end.outcome == EndUnknown()
    assert not any(isinstance(o, _owed()) for o in commands.state.store.snapshot().needs_you)


def _orphan(commands: _Commands, command_mark: str, script: str):  # type: ignore[no-untyped-def]
    """
    What a pptmstr that died mid-command leaves: the records of a run with no
    end, and the process. The command's id, the process, and its record.
    """
    import os
    import subprocess

    from pptmstr import commandfile, commandrun
    from pptmstr.command import Command, Run
    from tests.test_commandrun import MARK

    orphan = subprocess.Popen(
        ["/bin/sh", "-c", script], start_new_session=True, env={**os.environ, MARK: command_mark}
    )
    old = "cmd-20261012T101500Z-00000000dead"
    record = Command(
        id=old,
        asker=_CROOT,
        asked="make install",
        reason="",
        expects="",
        asked_at=1.0,
        run=Run("make install", str(commands.shell), str(commands.cwd), 600, 2.0),
    )
    assert isinstance(commandfile.write_request(record, commands.root), commandfile.Written)
    assert isinstance(commandfile.write_run(record, commands.root), commandfile.Written)
    started = commandrun.started_now(orphan.pid, at=2.5)
    assert isinstance(
        commandfile.write_started(_CS, old, started, commands.root), commandfile.Written
    )
    return old, orphan, started


def _later_window(commands: _Commands, command_mark: str) -> _Commands:
    """Another pptmstr, started afterwards, over the same sessions' records."""
    home = commands.root.parent / "a later window"
    home.mkdir()
    later = _Commands(home, command_mark)
    later.root = commands.root
    later.state.command_files = later.app.CommandFiles(commands.root)
    return later


def _end_orphan(orphan, command_mark: str) -> None:  # type: ignore[no-untyped-def]
    from tests.test_commandrun import _marked

    for pid in _marked(command_mark):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    orphan.wait()


def _looked(commands: _Commands, command_id: str) -> str:
    """Press 'check again' and wait for what the look said."""
    commands.state.command_checked.pop(command_id, None)
    commands.app._command_rows(commands.state).on_check(command_id)

    def answered() -> bool:
        commands.app._read_survivor_checks(commands.state)
        return command_id in commands.state.command_checked

    commands.until(answered)
    commands.frame()
    return commands.state.command_checked[command_id]


def test_a_look_that_could_not_read_proc_leaves_the_row_and_the_hold_and_says_so(
    commands: _Commands, command_mark: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    'check again' on a command that is running, at a moment ``/proc`` cannot be
    read. Answered as "gone", the row would leave and the session would be
    given another command beside a process that is alive.
    """
    import errno

    from pptmstr import commandfile, commandrun
    from pptmstr.command import EndUnknown, Survivor
    from pptmstr.model import CommandRefusal

    old, orphan, started = _orphan(commands, command_mark, "sleep 1000 & wait")
    try:
        commands.until(lambda: len(commandrun.session_members(orphan.pid) or ()) == 2)
        commands.app._restore_commands(commands.state, _CS)
        commands.frame()
        found = commands.held(old).unsupervised
        assert isinstance(found, Survivor)

        real = commandrun._alive

        def unreadable() -> None:
            raise OSError(errno.EMFILE, "Too many open files")

        monkeypatch.setattr(commandrun, "_alive", unreadable)
        said = _looked(commands, old)
        assert said.startswith(commands.app.NOT_LOOKED) and "Too many open files" in said
        assert commands.held(old).unsupervised == found
        assert [o.command.id for o in _owed_rows(commands)] == [old]
        assert "gone.json" not in commands.files(old)
        blocked = commands.ask()
        commands.run(blocked, "echo beside")
        assert commands.held(blocked).run is None
        assert commands.state.not_done == [
            commands.app.NOT_RUN + CommandRefusal.ALREADY_RUNNING_IN_SESSION.sentence
        ]

        # A record of its process that can no longer be read is the same answer.
        monkeypatch.setattr(commandrun, "_alive", real)
        monkeypatch.setattr(commandfile, "read_started", lambda *args, **kwargs: None)
        said = _looked(commands, old)
        assert said.startswith(commands.app.NOT_LOOKED + commands.app.RECORD_UNREADABLE)
        assert commands.held(old).unsupervised == found

        monkeypatch.undo()
        assert _looked(commands, old).startswith(commands.app.STILL_THERE)

        # At a start it is the same answer: no row to press, the record a plain
        # end unknown, which claims nothing about what is alive -- and it is
        # said, where a look that found nothing says nothing.
        monkeypatch.setattr(commandrun, "_alive", unreadable)
        mark = LOG.snapshot()[1]
        later = _later_window(commands, command_mark)
        later.app._restore_commands(later.state, _CS)
        later.frame()
        unknown = later.held(old)
        assert unknown.end.outcome == EndUnknown() and unknown.end.unconfirmed is None
        assert unknown.unsupervised is None and _owed_rows(later) == []
        assert "gone.json" not in commands.files(old)
        said_in_log = [text for _, text in _fleet_log_since(mark, "command")]
        assert any(
            old in text and "could not be looked for" in text and "Too many open files" in text
            for text in said_in_log
        ), said_in_log
        later.close()
    finally:
        monkeypatch.undo()
        _end_orphan(orphan, command_mark)


def _owed_rows(commands: _Commands) -> list:  # type: ignore[type-arg]
    return [o for o in commands.state.store.snapshot().needs_you if isinstance(o, _owed())]


def test_processes_left_without_their_shell_are_reported_and_given_no_kill_line(
    commands: _Commands, command_mark: str
) -> None:
    """
    At a start, and at 'check again' on a row whose shell has died since. What
    is in the session may be a stranger's, so nothing names it as the command:
    no row, no survivor, no line that kills -- and not "gone" either.
    """
    from pptmstr import commandbus, commandfile, commandrun
    from pptmstr.command import EndUnknown, Survivor
    from pptmstr.ui import command_view

    old, orphan, started = _orphan(commands, command_mark, "sleep 1000 & wait")
    mark = LOG.snapshot()[1]
    try:
        commands.until(lambda: len(commandrun.session_members(orphan.pid) or ()) == 2)
        commands.app._restore_commands(commands.state, _CS)
        commands.frame()
        assert isinstance(commands.held(old).unsupervised, Survivor)

        os.kill(orphan.pid, signal.SIGKILL)
        orphan.wait()
        said = _looked(commands, old)
        assert said.startswith(commands.app.UNIDENTIFIED)
        held = commands.held(old)
        found = commandrun.Unidentified(session=orphan.pid, members=1)
        line = commandrun.unidentified_line(found)
        assert held.end.outcome == EndUnknown() and held.end.unconfirmed == line
        assert held.unsupervised is None and _owed_rows(commands) == []
        assert "kill --" not in line and "kill --" not in said
        # What the two surfaces that read the record make of it.
        shown = command_view.row_state(held, 0.0)
        assert shown.text.startswith(command_view.END_UNKNOWN + " - NOT CONFIRMED DEAD: ")
        assert shown.tone is command_view.Tone.ALARM and "kill --" not in shown.text
        assert "MAY STILL BE RUNNING" in " ".join(commandbus._remarks(held.end))
        assert "gone.json" not in commands.files(old)
    finally:
        _end_orphan(orphan, command_mark)

    # The same found by a window that starts afterwards, with the process back,
    # beside what the pptmstr that quit on it had left to say.
    again, orphan, started = _orphan_again(commands, command_mark)
    try:
        commandfile.write_unconfirmed(_CS, again, "it quit", 3.0, orphan.pid, commands.root)
        other = _later_window(commands, command_mark)
        other.app._restore_commands(other.state, _CS)
        other.frame()
        held = other.held(again)
        assert held.end.outcome == EndUnknown() and held.unsupervised is None
        line = commandrun.unidentified_line(commandrun.Unidentified(orphan.pid, 1))
        assert held.end.unconfirmed == f"it quit. Also: {line}"
        said_in_log = [text for _, text in _fleet_log_since(mark, "command")]
        assert any(again in text and "names nothing to kill" in text for text in said_in_log)
        assert not any(again in text and "STILL RUNNING" in text for text in said_in_log)
        other.close()
    finally:
        _end_orphan(orphan, command_mark)


def _orphan_again(commands: _Commands, command_mark: str):  # type: ignore[no-untyped-def]
    """A second orphan, under another id, whose shell is already gone."""
    import subprocess

    from pptmstr import commandfile, commandrun
    from pptmstr.command import Command, Run
    from tests.test_commandrun import MARK

    orphan = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 1000 & wait"],
        start_new_session=True,
        env={**os.environ, MARK: command_mark},
    )
    again = "cmd-20261012T101500Z-0000000dead2"
    record = Command(
        id=again,
        asker=_CROOT,
        asked="make install",
        reason="",
        expects="",
        asked_at=1.0,
        run=Run("make install", str(commands.shell), str(commands.cwd), 600, 2.0),
    )
    commandfile.write_request(record, commands.root)
    commandfile.write_run(record, commands.root)
    started = commandrun.started_now(orphan.pid, at=2.5)
    commandfile.write_started(_CS, again, started, commands.root)
    deadline = time.monotonic() + 5
    while len(commandrun.session_members(orphan.pid) or ()) != 2:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    os.kill(orphan.pid, signal.SIGKILL)
    orphan.wait()
    return again, orphan, started


def test_a_process_seen_gone_is_not_looked_for_at_any_later_start(
    commands: _Commands, command_mark: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The number a record holds is some other process's in time. A record asked
    about at every start is in the end asked about that process.
    """
    from pptmstr import commandfile, commandrun
    from pptmstr.command import EndUnknown

    old, orphan, started = _orphan(commands, command_mark, "true")
    orphan.wait()
    assert commandrun.survivor(started) == commandrun.Gone(commandrun.NOTHING_ALIVE)
    commands.app._restore_commands(commands.state, _CS)
    commands.frame()
    assert commands.held(old).end.outcome == EndUnknown()
    assert commands.held(old).end.unconfirmed is None
    assert commandfile.read_gone(_CS, old, commands.root) == commandrun.NOTHING_ALIVE

    asked: list[object] = []
    monkeypatch.setattr(commandrun, "survivor", lambda started: asked.append(started))
    later = _later_window(commands, command_mark)
    try:
        later.app._restore_commands(later.state, _CS)
        later.frame()
        assert later.held(old).end.outcome == EndUnknown() and asked == []
    finally:
        later.close()


def test_a_session_resumed_in_the_window_running_its_command_is_not_told_it_is_unheld(
    commands: _Commands, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    On disk a running command is a run with no end and a live process, which
    is also what an orphan is. This window holds it, and says nothing else.
    """
    from pptmstr import commandfile, commandrun
    from pptmstr.command import CommandState

    first = commands.ask()
    commands.run_until_started(first, "sleep 1000 & wait")
    commands.until(lambda: commandfile.read_started(_CS, first, commands.root) is not None)
    asked: list[object] = []
    real = commandrun.survivor
    monkeypatch.setattr(
        commandrun, "survivor", lambda started: (asked.append(started), real(started))[1]
    )
    mark = LOG.snapshot()[1]

    commands.app._restore_commands(commands.state, _CS)
    commands.frame()

    said = [text for _, text in _fleet_log_since(mark, "command")]
    assert not any("STILL RUNNING" in text for text in said), said
    assert asked == [], "a command this window is running was looked for as an orphan"
    assert commands.held(first).state is CommandState.RUNNING


def test_a_process_record_that_was_not_written_is_said_on_the_commands_row(
    commands: _Commands, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptmstr import commandfile
    from pptmstr.command import Exited

    refused = commandfile.Unwritten("the disk is full")
    monkeypatch.setattr(commandfile, "write_started", lambda *args, **kwargs: refused)
    command_id = commands.ask()
    commands.run(command_id, "echo ran")
    assert commands.ended(command_id).outcome == Exited(0)
    commands.until(lambda: command_id in commands.state.command_unrecorded)
    assert commands.state.command_unrecorded[command_id] == (
        "which process it became is not recorded on disk: the disk is full"
    )
    assert commands.app._command_rows(commands.state).unrecorded[command_id]


def test_what_a_commands_own_directory_cost_its_environment_is_logged_at_its_end(
    commands: _Commands,
) -> None:
    """
    The row lists what is taken out for every directory. What is taken out
    because of this one is known when the command is run, and said then.
    """
    from pptmstr.command import Exited

    planted = commands.cwd / "bin"
    planted.mkdir()
    runner = commands.state.command_runner
    runner._environ = {**runner._environ, "PATH": f"{planted}:{runner._environ['PATH']}"}
    commands.state.command_host = commands.app.CommandHost(
        str(commands.shell), commands.app.commandrun.environment_changes(runner._environ)
    )
    mark = LOG.snapshot()[1]
    command_id = commands.ask()
    commands.run(command_id, "echo ran")
    assert commands.ended(command_id).outcome == Exited(0)
    said = [text for _, text in _fleet_log_since(mark, "command")]
    assert said == [
        f"{command_id} was run without it: 1 PATH entry inside the directory the command"
        f" runs in ('{planted}') is taken off: this session writes the files there"
    ]


_SIGNALLED_TWICE = """
import signal, sys, time
from pptmstr import app

asked = []
def request():
    asked.append(1)
    print("asked", len(asked), flush=True)

app.exit_on_signals(request)
print("ready", flush=True)
while True:      # a shutdown that has hung
    time.sleep(0.05)
"""


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (signal.SIGHUP, signal.SIGTERM),
        (signal.SIGTERM, signal.SIGHUP),
        (signal.SIGINT, signal.SIGTERM),
        (signal.SIGTERM, signal.SIGINT),
        (signal.SIGTERM, signal.SIGTERM),
    ],
)
def test_a_second_signal_of_any_kind_kills_a_shutdown_that_has_hung(
    first: signal.Signals, second: signal.Signals
) -> None:
    """
    A terminal that closes sends one signal and a session manager follows with
    another. The second is the operator's way out, whichever it is.
    """
    import subprocess
    import sys

    process = subprocess.Popen([sys.executable, "-c", _SIGNALLED_TWICE], stdout=subprocess.PIPE)
    try:
        assert process.stdout is not None and process.stdout.readline() == b"ready\n"
        process.send_signal(first)
        assert process.stdout.readline() == b"asked 1\n"
        process.send_signal(second)
        try:
            code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
        assert code == -second, "the second signal asked again and killed nothing"
        assert process.stdout.read() == b""
    finally:
        process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()


_SIGNALLED_AFTER_THE_LOOP = """
import os, signal, sys, time
from pptmstr import app

def request():
    print("asked of a loop that has returned", flush=True)

the_loop_returned = app.exit_on_signals(request)
the_loop_returned()
os.kill(os.getpid(), int(sys.argv[1]))
time.sleep(0.2)                       # the handler runs between two bytecodes
print("defaults", all(signal.getsignal(s) is signal.SIG_DFL for s in app.EXIT_SIGNALS), flush=True)
os.kill(os.getpid(), int(sys.argv[2]))
time.sleep(5)
print("not killed", flush=True)
"""


def test_a_signal_after_the_loop_has_returned_asks_nothing_and_arms_the_next() -> None:
    """
    ``request_exit`` reaches into a window loop. Once that has returned there
    is nothing to ask and the shutdown is under way; what is left to do is to
    make the next signal fatal.
    """
    import subprocess
    import sys

    done = subprocess.run(
        [
            sys.executable,
            "-c",
            _SIGNALLED_AFTER_THE_LOOP,
            str(int(signal.SIGTERM)),
            str(int(signal.SIGHUP)),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert done.stdout == b"defaults True\n", done.stdout + done.stderr
    assert done.returncode == -signal.SIGHUP


def test_a_quit_that_does_not_see_a_command_end_says_so_in_its_record(
    tmp_path: Path, command_mark: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The log is gone with the window. The command's own directory is not.
    """
    import json

    from pptmstr import commandfile, commandrun
    from pptmstr.command import Cancelled, CancelledBy, EndUnknown

    window = _Commands(tmp_path, command_mark, drain_s=1.0)
    try:
        command_id = window.ask()
        # Holds the pipe from outside the group, so the worker is still
        # draining when a quit with no patience returns.
        window.run_until_started(command_id, "setsid sleep 3 & sleep 1000")
        window.until(lambda: commandfile.read_started(_CS, command_id, window.root) is not None)
        started = commandfile.read_started(_CS, command_id, window.root)
        assert started is not None
        monkeypatch.setattr(commandrun, "CLOSE_TIMEOUT", 0.0)

        window.app._close_commands(window.state)

        assert "unconfirmed.json" in window.files(command_id)
        left = json.loads((window.directory(command_id) / "unconfirmed.json").read_text())
        assert left["pid"] == started.pid and commandrun.NOT_RECORDED_IN_TIME in left["reason"]
        if "end.json" not in window.files(command_id):
            read = commandfile.read_command(_CS, command_id, window.root)
            assert read.end.outcome == EndUnknown()  # type: ignore[union-attr]
            assert commandrun.NOT_RECORDED_IN_TIME in read.end.unconfirmed  # type: ignore[union-attr,operator]
        # The worker outlives the bound here, and its end is then the account.
        monkeypatch.undo()
        deadline = time.monotonic() + 10
        while "end.json" not in window.files(command_id):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        read = commandfile.read_command(_CS, command_id, window.root)
        assert read.end.outcome == Cancelled(CancelledBy.SHUTDOWN)  # type: ignore[union-attr]
        assert read.end.unconfirmed is None  # type: ignore[union-attr]
    finally:
        from tests.test_commandrun import _marked

        window.close()
        for pid in _marked(command_mark):
            os.kill(pid, signal.SIGKILL)


def test_an_approval_that_lost_to_a_withdrawal_is_said_and_not_silent(
    commands: _Commands,
) -> None:
    import time

    from pptmstr.command import Withdrawn
    from pptmstr.intents import CommandWithdrawn

    command_id = commands.ask()
    # The withdrawal is in the queue behind the snapshot the row was drawn from.
    commands.state.bridge.emit(CommandWithdrawn(_CROOT, command_id, None, time.time()))
    commands.actions.on_run(command_id, "true", 600)
    assert commands.state.not_done == [] and commands.state.command_approving == {command_id}

    commands.frame()
    commands.app._check_for_unstarted_runs(commands.state)

    assert commands.held(command_id).end.outcome == Withdrawn()
    assert commands.state.not_done == [commands.app.NOT_RUN + commands.app.WITHDRAWN_FIRST]
    assert commands.state.command_approving == set() and not commands.argv_out.exists()

    # Pressed on a row that already shows the withdrawal: the same words.
    commands.state.not_done.clear()
    commands.actions.on_run(command_id, "true", 600)
    assert commands.state.not_done == [commands.app.NOT_RUN + commands.app.WITHDRAWN_FIRST]


def test_an_approval_that_was_applied_says_nothing(commands: _Commands) -> None:
    from pptmstr.command import Exited

    command_id = commands.ask()
    commands.run(command_id, "echo ran")
    commands.app._check_for_unstarted_runs(commands.state)
    assert commands.ended(command_id).outcome == Exited(0)
    assert commands.state.not_done == [] and commands.state.command_approving == set()


def test_every_frame_looks_for_an_approval_that_started_nothing_and_reads_the_looks() -> None:
    import inspect

    from pptmstr import app

    source = inspect.getsource(app.begin_frame)
    assert "_check_for_unstarted_runs(state)" in source
    assert "_read_survivor_checks(state)" in source
    assert source.index("apply_intents(state)") < source.index("_check_for_unstarted_runs(state)")
    main = inspect.getsource(app.main)
    assert "exit_on_signals(_ask_window_to_close)" in main
    # Told that the loop is over before anything is torn down behind it.
    assert main.index("immapp.run(runner)") < main.index("the_loop_returned()")
    assert main.index("the_loop_returned()") < main.index("_shut_down(state)")


_SIGNALLED = """
import os, sys, threading, time
from pathlib import Path
from pptmstr import app, commandfile, commandrun
from pptmstr.command import Command, Run

root, shell, cwd, handled = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4] == "handled"
asked = Command(id="cmd-20261013T101500Z-00000000beef", asker=("s", None), asked="x",
                reason="", expects="", asked_at=1.0)
commandfile.write_request(asked, root)
runner = commandrun.Runner(lambda ended: None, root=root, grace_s=0.3, drain_s=0.2)
asked_to_exit = threading.Event()
if handled:
    app.exit_on_signals(asked_to_exit.set)
import dataclasses
runner.start(dataclasses.replace(asked, run=Run("sleep 1000 & wait", shell, cwd, 600.0, 2.0)))
while commandfile.read_started("s", asked.id, root) is None:
    time.sleep(0.01)
print("ready", flush=True)
try:
    while not asked_to_exit.wait(0.05):   # the window loop
        pass
finally:
    runner.close(5.0)
"""


@pytest.mark.parametrize("handled", [False, True])
def test_a_sigterm_or_sighup_closes_the_runner_and_without_the_handler_orphans_the_command(
    tmp_path: Path, command_mark: str, handled: bool
) -> None:
    """
    A whole process, signalled as a session manager, a closed terminal or a
    Ctrl-C would signal pptmstr. Without the handler the command outlives it,
    unbounded, and is found from its record; with it, the session is dead
    before the process is.
    """
    import subprocess
    import sys

    from pptmstr import app, commandfile, commandrun
    from pptmstr.command import Cancelled, CancelledBy, EndUnknown, Survivor
    from tests.test_commandrun import FAKE_SHELL, MARK, _marked

    root, cwd, shell = tmp_path / "sessions", tmp_path / "cwd", tmp_path / "fake-shell"
    cwd.mkdir()
    shell.write_text(FAKE_SHELL)
    shell.chmod(0o755)
    command_id = "cmd-20261013T101500Z-00000000beef"
    asked_by = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
    assert set(asked_by) == set(app.EXIT_SIGNALS)
    for signum in asked_by if handled else (signal.SIGTERM,):
        sessions = root / signum.name
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _SIGNALLED,
                str(sessions),
                str(shell),
                str(cwd),
                "handled" if handled else "default",
            ],
            stdout=subprocess.PIPE,
            env={**os.environ, MARK: command_mark, "ARGV_OUT": str(tmp_path / "argv")},
        )
        try:
            assert process.stdout is not None and process.stdout.readline() == b"ready\n"
            started = commandfile.read_started("s", command_id, sessions)
            assert started is not None
            assert isinstance(commandrun.survivor(started), Survivor)
            process.send_signal(signum)
            code = process.wait(timeout=20)
            read = commandfile.read_command("s", command_id, sessions)
            if handled:
                assert code == 0
                assert commandrun.survivor(started) == commandrun.Gone(
                    commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED
                )
                assert read.end.outcome == Cancelled(CancelledBy.SHUTDOWN)  # type: ignore[union-attr]
            else:
                assert code == -signum
                time.sleep(0.5)
                found = commandrun.survivor(started)
                assert (
                    isinstance(found, Survivor) and found.members == 2
                ), "the orphan is the defect"
                assert read.end.outcome == EndUnknown()  # type: ignore[union-attr]
        finally:
            process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
            for pid in _marked(command_mark):
                os.kill(pid, signal.SIGKILL)
            deadline = time.monotonic() + 3
            while _marked(command_mark) and time.monotonic() < deadline:
                time.sleep(0.02)


# -- a command known by the tag its processes carry --------------------------------


def _tagged_orphan(commands: _Commands, command_mark: str, script: str, old: str):  # type: ignore[no-untyped-def]
    """
    ``_orphan`` as this build's runner leaves one: the shell was started with
    a value made for the run in its environment, and its record holds the
    digest of that.
    """
    import subprocess

    from pptmstr import commandfile, commandrun
    from pptmstr.command import Command, Run
    from tests.test_commandrun import MARK

    value = commandrun.mint_tag()
    orphan = subprocess.Popen(
        ["/bin/sh", "-c", script],
        start_new_session=True,
        stdout=subprocess.PIPE,
        env={**os.environ, MARK: command_mark, commandrun.COMMAND_TAG: value},
    )
    record = Command(
        id=old,
        asker=_CROOT,
        asked="make install",
        reason="",
        expects="",
        asked_at=1.0,
        run=Run("make install", str(commands.shell), str(commands.cwd), 600, 2.0),
    )
    assert isinstance(commandfile.write_request(record, commands.root), commandfile.Written)
    assert isinstance(commandfile.write_run(record, commands.root), commandfile.Written)
    started = commandrun.started_now(orphan.pid, at=2.5, tag=commandrun.tag_digest(value))
    assert isinstance(
        commandfile.write_started(_CS, old, started, commands.root), commandfile.Written
    )
    assert orphan.stdout is not None
    child = int(orphan.stdout.readline().split()[1])
    orphan.stdout.close()
    return orphan, started, child


def test_a_command_whose_shell_died_owes_a_row_and_holds_its_session_by_its_tag(
    commands: _Commands, command_mark: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The shell killed and what it started alive, found at a start. Reported
    and not named, the session would be given a second command beside it.
    """
    import subprocess

    from pptmstr import commandfile, commandrun
    from pptmstr.command import EndUnknown, Exited, Survivor
    from pptmstr.model import CommandRefusal
    from pptmstr.ui import inbox

    old = "cmd-20261012T101500Z-0000000dea11"
    orphan, started, child = _tagged_orphan(
        commands, command_mark, "sleep 1000 & echo child $!; wait", old
    )
    mark = LOG.snapshot()[1]
    try:
        os.kill(orphan.pid, signal.SIGKILL)
        orphan.wait()
        commands.app._restore_commands(commands.state, _CS)
        commands.frame()

        found = Survivor(
            pid=orphan.pid,
            pgid=orphan.pid,
            members=1,
            groups=(orphan.pid,),
            marked=(child,),
            shell=False,
        )
        held = commands.held(old)
        assert held.end.outcome == EndUnknown(survivor=found) and held.end.unconfirmed is None
        (owed,) = _owed_rows(commands)
        assert owed.command.id == old and "its shell is gone, 1 of its processes" in owed.summary
        assert f"kill -- -{orphan.pid}" in inbox.survivor_lines(found)
        said_in_log = [text for _, text in _fleet_log_since(mark, "command")]
        assert any(
            old in text
            and f"its shell (pid {orphan.pid}) is gone and 1 process(es) started with its"
            " PPTMSTR_COMMAND are alive" in text
            and text.endswith(f"To end it: kill -- -{orphan.pid}")
            for text in said_in_log
        ), said_in_log

        blocked = commands.ask()
        commands.run(blocked, "echo beside")
        assert commands.held(blocked).run is None
        assert commands.state.not_done == [
            commands.app.NOT_RUN + CommandRefusal.ALREADY_RUNNING_IN_SESSION.sentence
        ]
        assert _looked(commands, old).startswith(commands.app.STILL_THERE)

        kill = commandrun.kill_line(found)
        assert subprocess.run(["/bin/sh", "-c", kill], check=False).returncode == 0
        commands.until(lambda: commandrun.session_members(orphan.pid) == ())
        said = _looked(commands, old)
        why = commandrun.NOTHING_ALIVE + commandrun.NONE_TAGGED
        assert said.startswith(commands.app.GONE + why + " (")
        assert "setsid" not in said
        commands.frame()
        assert commands.held(old).unsupervised is None
        assert [o.command.id for o in _owed_rows(commands)] == [blocked]
        commands.run(blocked, "echo beside")
        assert commands.ended(blocked).outcome == Exited(0)
        assert commandfile.read_gone(_CS, old, commands.root) == why

        # Seen gone, it is not looked for again, by its session or by its tag.
        asked: list[object] = []
        monkeypatch.setattr(commandrun, "survivor", lambda started: asked.append(started))
        later = _later_window(commands, command_mark)
        try:
            later.app._restore_commands(later.state, _CS)
            later.frame()
            assert later.held(old).end.outcome == EndUnknown() and asked == []
        finally:
            later.close()
    finally:
        _end_orphan(orphan, command_mark)


def test_a_command_with_only_a_process_outside_its_session_left_is_a_row_and_a_hold(
    commands: _Commands, command_mark: str
) -> None:
    """
    No end on record and something the command started still alive: it is
    running unsupervised wherever that process went. Said as a remark on an
    ended command, the session would be given another beside it.
    """
    from pptmstr.command import EndUnknown, Survivor
    from pptmstr.ui import inbox

    old = "cmd-20261012T101500Z-0000000dea12"
    orphan, started, out = _tagged_orphan(
        commands, command_mark, "setsid sleep 1000 >/dev/null 2>&1 & echo out $!", old
    )
    try:
        orphan.wait()
        commands.until(lambda: os.getsid(out) == out)
        commands.app._restore_commands(commands.state, _CS)
        commands.frame()
        found = Survivor(
            pid=orphan.pid, pgid=orphan.pid, members=0, groups=(out,), escaped=(out,), shell=False
        )
        assert commands.held(old).end.outcome == EndUnknown(survivor=found)
        assert [o.command.id for o in _owed_rows(commands)] == [old]
        lines = inbox.survivor_lines(found)
        assert f"kill -- -{out}" in lines and f"1 outside it (pid {out})" in lines[1]
        assert "gone.json" not in commands.files(old)
    finally:
        _end_orphan(orphan, command_mark)


def test_a_look_that_found_nothing_says_what_it_could_not_have_found() -> None:
    """
    The line under a row after 'check again'. A record with no tag is found
    by its session alone, and what left that session is then not looked for.
    """
    from pptmstr import app, commandrun

    tagged = commandrun.Gone(commandrun.NUMBER_REUSED + commandrun.NONE_TAGGED)
    assert app._looked_again(tagged) == app.GONE + tagged.why
    untagged = commandrun.Gone(commandrun.NOTHING_ALIVE)
    assert app._looked_again(untagged) == app.GONE + untagged.why + app.GONE_UNTAGGED
    assert "setsid" in app.GONE_UNTAGGED
    rebooted = commandrun.Gone(commandrun.ANOTHER_BOOT)
    assert app._looked_again(rebooted) == app.GONE + commandrun.ANOTHER_BOOT
    assert app._looked_again(commandrun.Unidentified(5, 1)) == app.UNIDENTIFIED
    left = commandrun.Survivor(pid=5, pgid=5, members=1)
    assert app._looked_again(left) == app.STILL_THERE


def test_what_a_command_left_outside_its_session_is_logged_when_it_ends(
    commands: _Commands,
) -> None:
    import dataclasses

    from pptmstr import commandfile, commandrun
    from pptmstr.command import End, Exited

    first = commands.ask()
    commands.run(first, "true")
    commands.ended(first)
    held = commands.held(first)
    mark = LOG.snapshot()[1]
    for escaped in ((4301,), (), None):
        end = End(Exited(0), at=3.0, escaped=escaped)
        commands.app._command_ended(commands.state)(
            commandrun.Ended(
                dataclasses.replace(held, end=end), commandfile.Written(Path("/end.json"))
            )
        )
    said = [text for _, text in _fleet_log_since(mark, "command") if "ALIVE outside" in text]
    assert said == [f"{first}: {End(Exited(0), at=3.0, escaped=(4301,)).escaped_remark}"]


def test_the_row_is_handed_the_line_that_says_the_command_is_given_its_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    What the row lists is what was found at startup. A change made in the
    runner and not found there is one the operator approves without seeing.
    """
    from pptmstr import app, commandrun

    monkeypatch.delenv(commandrun.COMMAND_TAG, raising=False)
    assert app._command_host().stripped_env[-1] == commandrun.TAG_SET
    monkeypatch.setenv(commandrun.COMMAND_TAG, "cmd-outer")
    assert app._command_host().stripped_env[-1] == commandrun.TAG_OVERWRITTEN
    assert "cmd-outer" not in "".join(app._command_host().stripped_env)


def _released(commands: _Commands, command_id: str) -> None:
    """Press 'release' on a held row, as its second press does, and wait for it to apply."""
    rows = commands.app._command_rows(commands.state)
    assert rows.on_release is not None, "a held session has no way out but /proc"
    rows.on_release(command_id)
    commands.until(lambda: commands.held(command_id).unsupervised is None)


def test_the_operator_can_release_a_session_a_live_command_holds_and_nothing_is_signalled(
    commands: _Commands, command_mark: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A command that started a server on purpose, from a pptmstr that died: the
    operator wants it left running. Before this, only its processes ending let
    go of the session. Released, the session runs another command, the
    processes are untouched, the record says the operator released it and how
    many were still found, and a later window neither raises the row nor looks.
    """
    import dataclasses

    from pptmstr import commandfile, commandrun
    from pptmstr.command import EndUnknown, Exited, Released

    old, orphan, started = _orphan(commands, command_mark, "sleep 1000 & wait")
    sent: list[tuple[int, int]] = []
    try:
        commands.until(lambda: len(commandrun.session_members(orphan.pid) or ()) == 2)
        commands.app._restore_commands(commands.state, _CS)
        commands.frame()
        before = commands.held(old)
        assert before.unsupervised is not None and before.unsupervised.alive == 2

        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
        monkeypatch.setattr(os, "killpg", lambda pid, sig: sent.append((pid, sig)))
        _released(commands, old)
        monkeypatch.undo()

        assert sent == []
        assert len(commandrun.session_members(orphan.pid) or ()) == 2, "nothing was signalled"
        end = commands.held(old).end
        assert isinstance(end.outcome, EndUnknown) and end.outcome.survivor is None
        released = end.outcome.released
        assert isinstance(released, Released) and released.alive == 2
        assert end == dataclasses.replace(before.end, outcome=EndUnknown(released=released))
        assert _owed_rows(commands) == []
        assert commands.files(old) == [
            "released.json",
            "request.json",
            "run.json",
            "started.json",
        ]
        assert commandfile.read_released(_CS, old, commands.root) == released

        beside = commands.ask()
        commands.run(beside, "echo beside")
        assert commands.ended(beside).outcome == Exited(0)

        later = _later_window(commands, command_mark)
        try:

            def no_look(_: object) -> object:
                raise AssertionError("a released command is not looked for again")

            monkeypatch.setattr(commandrun, "survivor", no_look)
            later.app._restore_commands(later.state, _CS)
            later.frame()
            assert later.held(old).end.outcome == EndUnknown(released=released)
            assert _owed_rows(later) == []
        finally:
            later.close()
    finally:
        try:
            os.killpg(orphan.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        orphan.wait()


def test_a_session_held_only_by_what_left_it_is_released_the_same_way(
    commands: _Commands, command_mark: str
) -> None:
    """
    The shell is gone and the one process left alive left its session: still
    a row and a hold, and the same press lets go of it without touching it.
    """
    from pptmstr.command import EndUnknown

    old = "cmd-20261012T101500Z-0000000dea13"
    orphan, started, out = _tagged_orphan(
        commands, command_mark, "setsid sleep 1000 >/dev/null 2>&1 & echo out $!", old
    )
    try:
        orphan.wait()
        commands.until(lambda: os.getsid(out) == out)
        commands.app._restore_commands(commands.state, _CS)
        commands.frame()
        left = commands.held(old).unsupervised
        assert left is not None and left.members == 0 and left.escaped == (out,)

        _released(commands, old)

        outcome = commands.held(old).end.outcome
        assert isinstance(outcome, EndUnknown) and outcome.released is not None
        assert outcome.released.alive == 1 and _owed_rows(commands) == []
        assert commands.state.store.snapshot().unsupervised_command(_CS) is None
        os.kill(out, 0)  # still there
        assert "released.json" in commands.files(old) and "gone.json" not in commands.files(old)
    finally:
        _end_orphan(orphan, command_mark)
