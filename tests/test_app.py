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

import threading
import types
from typing import Any

import pytest

from pptmstr import cli_version
from pptmstr.approval import Policy
from pptmstr.bridge import Bridge
from pptmstr.cli_version import BelowFloor, FloorCheck, MeetsFloor, Unreadable
from pptmstr.driver import AgentSession
from pptmstr.model import LaunchSpec
from pptmstr.settings import RoleChoice, Settings, legacy_faces
from pptmstr.store import Store

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
        def submit(self, session: AgentSession) -> None:
            out.pool_thread = threading.current_thread()
            out.sessions.append(session)

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


class _Reached(Exception):
    """
    Raised by a stand-in to stop a function at the call under test.
    """


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
    from pptmstr import app
    from pptmstr import settings as settings_mod
    from pptmstr.ui import typeset

    def stop(**kwargs: object) -> None:
        raise _Reached

    monkeypatch.setattr(
        settings_mod,
        "load",
        lambda: Settings(typeset=False, typeset_scale=1.25, typeset_family="droid", theme="dark")
        .with_role("prose", slot="monospace")
        .with_role("code-block", step=2)
        .with_pane("context", False),
    )
    monkeypatch.setattr(app, "AppState", stop)
    typeset.configure(typeset=True, mono=False, scale=1.0, panes={})
    with pytest.raises(_Reached):
        app.main([])
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
    monkeypatch.setattr(settings_mod, "save", lambda settings, path=None: saved.append(settings))
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
    The five ImGui calls ``app._menus`` makes, with one entry that reports a click.

    A disabled menu does not open and a disabled entry cannot be clicked, as in ImGui.
    """

    def __init__(self, click: str) -> None:
        self.click = click
        self.menus: dict[str, bool] = {}
        self.items: dict[str, tuple[bool, bool]] = {}
        self.said: list[str] = []

    def begin_menu(self, label: str, enabled: bool = True) -> bool:
        self.menus[label] = enabled
        return enabled

    def end_menu(self) -> None:
        pass

    def menu_item(
        self, label: str, shortcut: str, selected: bool, enabled: bool = True
    ) -> tuple[bool, bool]:
        self.items[label] = (selected, enabled)
        return (enabled and label == self.click, selected)

    def separator(self) -> None:
        pass

    def text_disabled(self, text: str) -> None:
        self.said.append(text)


def _text_menu(
    monkeypatch: pytest.MonkeyPatch, click: str, settings: Settings | None = None
) -> tuple[_Menu, Any, list[Settings]]:
    """
    ``app._menus`` drawn once against a stand-in ImGui, with one entry clicked.
    """
    from pptmstr import app
    from pptmstr import settings as settings_mod
    from pptmstr.ui import typeset

    menu = _Menu(click)
    saved: list[Settings] = []
    monkeypatch.setattr(app, "imgui", menu)
    monkeypatch.setattr(settings_mod, "save", lambda settings, path=None: saved.append(settings))
    monkeypatch.setattr(typeset, "available", lambda: True)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=settings or Settings())
    app._configure_typeset(state.settings)
    app._menus(state)
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

    menu, state, saved = _text_menu(monkeypatch, "Settings...")

    assert set(menu.menus) == {"Session", "Theme", "Text"}
    text = [label for label in menu.items if label not in {"New Task..."}]
    assert text[-3:] == ["Wrap composers", "Typeset text", "Settings..."]
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

    menu = _Menu("Typeset text")
    saved: list[Settings] = []
    monkeypatch.setattr(app, "imgui", menu)
    monkeypatch.setattr(settings_mod, "save", lambda settings, path=None: saved.append(settings))
    monkeypatch.setattr(typeset, "available", lambda: available)
    monkeypatch.setattr(typeset, "obtainable", lambda: obtainable)
    monkeypatch.setattr(typeset, "unavailable_reason", lambda: reason)
    monkeypatch.setattr(typeset, "configure", lambda **kwargs: None)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings(typeset=False))
    app._menus(state)
    assert menu.items["Typeset text"] == (False, offered)
    assert state.settings.typeset is offered, "a click on an entry that is offered is taken"
    under = [text for text in menu.said if text.startswith("  ")]
    assert under == ([] if reason is None else [f"  {reason}"])


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
    monkeypatch.setattr(settings_mod, "save", lambda settings, path=None: saved.append(settings))
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
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
    monkeypatch.setattr(settings_mod, "save", lambda settings, path=None: saved.append(settings))

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

        app._apply_settings(state, state.settings.merged(context_mode="typeset"))
        assert state.transcripts.mode is None, "the pane's own pick would hide the change"

        app._apply_settings(state, state.settings.merged(concurrency_cap=6))
        # A round trip through the loop: the resize is queued behind nothing else.
        bridge.submit(_nothing()).result(timeout=5)
        assert pool.cap == 6
        assert pool.resized_on is not None and pool.resized_on is not threading.current_thread()

        assert len(saved) == 4
        assert saved[-1] == Settings(
            theme="light", fps_idle=15.0, context_mode="typeset", concurrency_cap=6
        )

        # Nothing moved: nothing written, nothing told.
        state.theme_dirty = False
        app._apply_settings(state, state.settings.merged(theme="light"))
        assert len(saved) == 4 and not state.theme_dirty
    finally:
        bridge.stop()
        theme.set_theme("dark")


async def _nothing() -> None:
    return None


def test_the_context_log_is_drawn_in_the_mode_the_settings_start_it_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Both panes that draw the log -- CONTEXT in TRIAGE, SESSION in FOCUS -- read the
    setting each frame. A pane that did not would start in the build's mode
    whatever the dialog said.
    """
    from pptmstr import app
    from pptmstr.ui import compose, transcript_pane

    started: list[object] = []
    monkeypatch.setattr(
        transcript_pane,
        "draw",
        lambda snap, state, node, *, start_in=None: started.append(start_in),
    )
    monkeypatch.setattr(compose, "draw_conversation", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        app,
        "imgui",
        types.SimpleNamespace(
            get_content_region_avail=lambda: types.SimpleNamespace(y=400.0),
            begin_child=lambda *args: True,
            end_child=lambda: None,
            separator=lambda: None,
            ImVec2=lambda x, y: (x, y),
        ),
    )
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings(context_mode="raw"))
    state.frame_snap = state.store.snapshot()
    panels = app._panels(state)

    panels["CONTEXT"]()
    panels["SESSION"]()
    assert started == [transcript_pane.RenderMode.RAW] * 2

    state.settings = Settings()
    panels["CONTEXT"]()
    assert started[-1] is None, "an empty name is the build's own default"


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


def test_the_application_stays_awake_while_a_view_is_still_being_typeset() -> None:
    """
    A long text is built a piece a frame. An idling loop gives it a frame every few
    hundred milliseconds, so the loop has to be told there is work. The wiring is
    what is checked: ``typeset.pending`` unread would leave every test of the seam
    passing and a long document crawling.
    """
    import inspect

    from pptmstr import app

    source = inspect.getsource(app)
    wired = source[source.index("def pre_frame() -> None:") :]
    wired = wired[: wired.index("runner.callbacks.load_additional_fonts")]
    assert "typeset.pending()" in wired and "enable_idling = not working" in wired


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
    from pptmstr import app
    from pptmstr import settings as settings_mod
    from pptmstr.ui import typeset

    def stop(**kwargs: object) -> None:
        raise _Reached

    saved: list[Settings] = []
    monkeypatch.setattr(
        settings_mod, "load", lambda: Settings(typeset=False).merged(**legacy_faces("mono"))
    )
    monkeypatch.setattr(settings_mod, "save", lambda settings, path=None: saved.append(settings))
    monkeypatch.setattr(app, "AppState", stop)
    with pytest.raises(_Reached):
        app.main(argv)
    on, faces, scale = expected
    family, roles = typeset.legacy(faces)
    assert (typeset._STATE.typeset, typeset._STATE.scale) == (on, scale)
    assert (typeset._STATE.family, typeset._STATE.roles) == (family, typeset.checked(roles))
    assert saved == [], "an override for one run must not become the saved setting"
