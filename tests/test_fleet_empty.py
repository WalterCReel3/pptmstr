"""
A mostly empty fleet invites work (planning/2026-10-16-fleet-empty-variants.md,
variant (a), worded the app's way): under the cards, or alone on a fleet with none,
the quiet line "New Task · Ctrl+N", whose link does what the menu's New Task does.

Drawn in real Dear ImGui frames with no backend, over the fleet in each of the four
states the variants probe drew (``scripts/fleet_empty_probe.State``), and clicked
where it was drawn.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

from pptmstr import app
from pptmstr.bridge import Bridge
from pptmstr.intents import AgentSpawned
from pptmstr.settings import Settings
from pptmstr.store import Store
from pptmstr.ui import launcher, main_menu, projects, rail, widgets
from pptmstr.ui.focus import FocusState
from tests import test_typeset_views as rig

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("fleet_empty_probe")
STATES = pytest.mark.parametrize("state", list(probe.State), ids=lambda s: s.name)


# -- the words --------------------------------------------------------------------------


def test_the_chord_shown_is_the_one_new_task_answers_to() -> None:
    """Read off the binding, so the line and the menu name one key."""
    assert rail.new_task_label() == f" · {main_menu.chord_label(launcher.CHORD)}"
    assert rail.new_task_label() == " · Ctrl+N"
    facts = MagicMock(card=None, find_refused=None, settings=Settings())
    (item,) = [
        row
        for row in main_menu._session(facts).rows
        if getattr(row, "act", None) == main_menu.NewTask()
    ]
    assert rail.new_task_label() == f" · {item.hint}", "the menu's own chord label"
    assert rail.NEW_TASK == "New Task", "the app's own word for it, not 'start a session'"


@pytest.mark.parametrize(("ran", "said"), [(1, "1 session"), (2, "2 sessions"), (0, "0 sessions")])
def test_a_project_of_one_session_says_one_session(ran: int, said: str) -> None:
    store = Store()
    for n in range(ran):
        store.apply(AgentSpawned((f"s{n}", None), None, task="t", model="m", started_at=0.0))
    snap = store.snapshot()
    assert rail.header_counts(snap, [(f"s{n}", None) for n in range(ran)]) == said


# -- the line, drawn -------------------------------------------------------------------


class _Line:
    """Every New Task link drawn in a frame: where, and whether it was live."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, imgui: Any) -> None:
        self.at: list[tuple[float, float]] = []
        self.imgui = imgui
        real = widgets.link

        def link(label: str, **kw: Any) -> bool:
            pressed = real(label, **kw)
            if label.startswith(rail.NEW_TASK):
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                self.at.append(((low.x + high.x) / 2, (low.y + high.y) / 2))
            return pressed

        monkeypatch.setattr(widgets, "link", link)


def _draw(imgui: Any, snap: Any, new_task: Any) -> None:
    rig.frame(
        imgui,
        lambda: rail.draw(
            snap,
            FocusState(),
            rail.RailState(),
            probe.MONOTONIC,
            None,
            now_wall=probe.WALL,
            new_task=new_task,
        ),
    )


@STATES
def test_the_line_is_drawn_once_in_every_state_of_the_fleet(
    imgui: Any, monkeypatch: pytest.MonkeyPatch, state: Any
) -> None:
    line = _Line(monkeypatch, imgui)
    snap = probe.snapshot(state)
    _draw(imgui, snap, MagicMock())
    assert len(line.at) == 1
    said: list[str] = []
    monkeypatch.setattr(imgui, "text_disabled", lambda text: said.append(text))
    line.at.clear()
    _draw(imgui, snap, MagicMock())
    assert rail.new_task_label() in said
    assert "start one from the launcher below." not in said


def test_on_an_empty_fleet_the_line_is_at_the_top_and_under_the_cards_otherwise(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    line = _Line(monkeypatch, imgui)
    _draw(imgui, probe.snapshot(probe.State.NONE), MagicMock())
    empty_y = line.at[-1][1]
    tops: list[float] = []
    real = rail._group

    def group(*args: Any, **kw: Any) -> None:
        real(*args, **kw)
        tops.append(float(imgui.get_cursor_screen_pos().y))

    monkeypatch.setattr(rail, "_group", group)
    _draw(imgui, probe.snapshot(probe.State.SHORT), MagicMock())
    assert line.at[-1][1] > max(tops) > empty_y


@STATES
def test_a_click_on_new_task_does_what_it_was_handed_and_nothing_else(
    imgui: Any, monkeypatch: pytest.MonkeyPatch, state: Any
) -> None:
    line = _Line(monkeypatch, imgui)
    snap = probe.snapshot(state)
    pressed = MagicMock()
    io = imgui.get_io()
    _draw(imgui, snap, pressed)
    io.add_mouse_pos_event(*line.at[-1])
    _draw(imgui, snap, pressed)
    io.add_mouse_button_event(0, True)
    _draw(imgui, snap, pressed)
    io.add_mouse_button_event(0, False)
    _draw(imgui, snap, pressed)
    _draw(imgui, snap, pressed)
    io.add_mouse_pos_event(-1.0, -1.0)
    _draw(imgui, snap, pressed)
    assert pressed.call_count == 1


def test_without_an_action_there_is_no_line(imgui: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A line that offers what nothing can do would be the pane pretending to act."""
    line = _Line(monkeypatch, imgui)
    for state in probe.State:
        _draw(imgui, probe.snapshot(state), None)
    assert line.at == []


# -- the wiring: the menu's own New Task ----------------------------------------------


def test_the_fleet_is_handed_the_menus_new_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    What app's FLEET pane hands ``rail.draw`` as ``new_task``, called, does what
    choosing New Task in the menu does (``app._perform``): asks the launcher to open,
    as it was left.
    """
    handed: dict[str, Any] = {}
    monkeypatch.setattr(rail, "draw", lambda *a, **kw: handed.update(kw))
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    state.frame_snap = state.store.snapshot()
    app._panels(state)["FLEET"]()
    new_task = handed["new_task"]

    opened: list[str] = []
    monkeypatch.setattr(state.launcher, "request_open", lambda: opened.append("line"))
    new_task()
    monkeypatch.setattr(state.launcher, "request_open", lambda: opened.append("menu"))
    app._perform(state, main_menu.NewTask())
    assert opened == ["line", "menu"]


# -- a cwd holding a NUL -----------------------------------------------------------------


def test_a_session_whose_directory_holds_a_nul_is_grouped_and_drawn(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A directory holding a NUL is placed as the string it arrived as, since no
    directory can be named that, and its header is drawn with the NUL spelled out
    (``widgets.one_line``, STYLE.md section 3) rather than cutting the name or raising.
    """
    store = Store()
    cwd = "/home/x/pro\x00ject"
    store.apply(AgentSpawned(("s1", None), None, task="t", model="m", started_at=0.0, cwd=cwd))
    snap = store.snapshot()
    ((project, roots),) = projects.group_roots(snap)
    assert project == "pro\x00ject" and roots == [("s1", None)]

    drawn: list[str] = []
    real = imgui.text_colored
    monkeypatch.setattr(
        imgui, "text_colored", lambda colour, text: (drawn.append(text), real(colour, text))
    )
    _draw(imgui, snap, MagicMock())
    assert widgets.one_line("PRO\x00JECT") in drawn, drawn
    assert not any("\x00" in text for text in drawn)
