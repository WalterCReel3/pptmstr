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

import pytest

from pptmstr import cli_version
from pptmstr.approval import Policy
from pptmstr.bridge import Bridge
from pptmstr.cli_version import BelowFloor, FloorCheck, MeetsFloor, Unreadable
from pptmstr.driver import AgentSession
from pptmstr.model import LaunchSpec
from pptmstr.settings import Settings
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
    from pptmstr.ui import launcher

    seen: list[object] = []
    monkeypatch.setattr(
        launcher, "draw", lambda state, **kw: seen.append(kw.get("subagent_cap", "ABSENT"))
    )
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)

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

    before = (typeset._STATE.typeset, typeset._STATE.faces, typeset._STATE.scale)
    yield typeset._STATE
    typeset.configure(typeset=before[0], faces=before[1], scale=before[2])


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
        lambda: Settings(typeset=False, typeset_faces="mono", typeset_scale=1.25, theme="dark"),
    )
    monkeypatch.setattr(app, "AppState", stop)
    typeset.configure(typeset=True, mono=False, scale=1.0)
    with pytest.raises(_Reached):
        app.main([])
    assert (typeset._STATE.typeset, typeset._STATE.faces) == (False, "mono")
    assert typeset.mono() and typeset.scale() == 1.25


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

    app._switch_typeset(state, faces="mono")
    assert state.settings.typeset_faces == "mono" and saved[-1].typeset_faces == "mono"
    assert state.settings.typeset is False, "one switch must not move the other"
    assert typeset.mono() is True
    assert typeset._STATE.epoch == epoch + 1, "documents built in the other faces are stale"

    app._switch_typeset(state, scale=1.25)
    assert state.settings.typeset_scale == 1.25 and saved[-1].typeset_scale == 1.25
    assert state.settings.typeset_faces == "mono", "nor the size the faces"
    assert typeset.scale() == 1.25
    assert typeset._STATE.epoch == epoch + 2, "documents built at the other size are stale"


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


@pytest.mark.parametrize(
    ("click", "faces", "scale"),
    [
        ("Inconsolata throughout", "mono", 1.0),
        ("Roboto prose a size up, Inconsolata code", "roboto-large", 1.0),
        ("Droid Sans prose, Roboto emphasis, Inconsolata code", "roboto", 1.0),
        ("125%", "roboto", 1.25),
        ("87.5%", "roboto", 0.875),
    ],
)
def test_the_text_menu_offers_every_preset_and_size_and_a_click_chooses_it(
    monkeypatch: pytest.MonkeyPatch, typeset_state: object, click: str, faces: str, scale: float
) -> None:
    """
    The presets and the scale are reachable from nowhere else in the window. The
    Droid Sans entry is the one that cannot be clicked here: no font is loaded in
    this test, and a preset whose own file is not on the atlas is offered disabled.
    """
    from pptmstr import app
    from pptmstr import settings as settings_mod
    from pptmstr.ui import typeset

    menu = _Menu(click)
    saved: list[Settings] = []
    monkeypatch.setattr(app, "imgui", menu)
    monkeypatch.setattr(settings_mod, "save", lambda settings, path=None: saved.append(settings))
    monkeypatch.setattr(typeset, "available", lambda: True)
    monkeypatch.setattr(typeset._STATE, "files", {})
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())

    app._menus(state)

    assert (state.settings.typeset_faces, state.settings.typeset_scale) == (faces, scale)
    assert (typeset._STATE.faces, typeset.scale()) == (faces, scale)
    assert menu.menus["Faces"] is True and menu.menus["Text size"] is True
    labels = {preset.label: preset for preset in typeset.PRESETS}
    assert set(labels) <= set(menu.items)
    assert {f"{each * 100:g}%" for each in typeset.SCALES} <= set(menu.items)
    for label, preset in labels.items():
        assert menu.items[label] == (preset.name == "roboto", preset.prose_file is None), label
    # Ticked as drawn: an entry above it that took the click has already moved the tick.
    assert menu.items["100%"] == (click != "87.5%", True)
    assert bool(saved) == ((faces, scale) != ("roboto", 1.0))


def test_the_face_and_size_menus_do_not_open_while_text_is_not_typeset(
    monkeypatch: pytest.MonkeyPatch, typeset_state: object
) -> None:
    from pptmstr import app
    from pptmstr.ui import typeset

    menu = _Menu("")
    monkeypatch.setattr(app, "imgui", menu)
    monkeypatch.setattr(typeset, "available", lambda: True)
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings(typeset=False))
    app._menus(state)
    assert menu.menus["Faces"] is False and menu.menus["Text size"] is False
    assert not any(preset.label in menu.items for preset in typeset.PRESETS)


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
    monkeypatch.setattr(settings_mod, "load", lambda: Settings(typeset=False, typeset_faces="mono"))
    monkeypatch.setattr(settings_mod, "save", lambda settings, path=None: saved.append(settings))
    monkeypatch.setattr(app, "AppState", stop)
    with pytest.raises(_Reached):
        app.main(argv)
    assert (typeset._STATE.typeset, typeset._STATE.faces, typeset._STATE.scale) == expected
    assert saved == [], "an override for one run must not become the saved setting"
