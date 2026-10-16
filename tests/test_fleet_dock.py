"""
The fleet pane's dock (planning/2026-10-15-ui-rework.md, U7): between a least and a
greatest width in the UI font's advance, never undocked, never docked into, and
headed by its "fleet" tab.

The docking tests run hello_imgui on its null backends in a child process, as
``tests/test_tabs.py`` does, and drive the mouse through Dear ImGui's own input
events: what is measured is what the runner lays out after a real drag. The
saved-layout test launches twice over one ini, the second time over a file whose
fleet width was written out of bounds.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pptmstr import app

ROOT = Path(__file__).resolve().parent.parent


# -- the bounds ----------------------------------------------------------------------


def test_the_bounds_are_forty_and_sixty_four_advances() -> None:
    assert app.fleet_bounds(8.0) == (320.0, 512.0), "at 16 px, whose advance is 8 px"
    assert app.fleet_bounds(10.0) == (400.0, 640.0), "and they follow the font"


@pytest.mark.parametrize(
    ("width", "held"),
    [(100.0, 320.0), (320.0, 320.0), (400.0, 400.0), (512.0, 512.0), (900.0, 512.0)],
)
def test_a_width_is_held_between_the_bounds(width: float, held: float) -> None:
    assert app.fleet_width(width, 8.0) == held


# What shots/fleet-dock/probe_widths.py measured in the running app, UI font at 16 px
# (2026-10-16): the parts of a card's first line besides its title.
MEASURED = {
    "advance": 8.0,
    "badges": (50.0, 54.0, 52.0),
    "badge_gap": 3.0,
    "pad": 6.0,
    "rain": 8.0,
    "rain_gap": 5.0,
    "title_min": 40.0,
    "window_padding": 8.0,
    "scrollbar": 16.0,
    "group_indent": 14.0,
}


def test_a_card_owing_three_kinds_fits_at_the_least_width() -> None:
    """
    The arithmetic the least width rests on (``FLEET_MIN_CHARS``), the card's own
    (``rail._draw_card``'s title room): a sub-agent's card, indented, owing three
    kinds, still has the start of its title.
    """
    m = MEASURED
    badges = sum(m["badges"]) + m["badge_gap"] * (len(m["badges"]) - 1)
    beside = (m["pad"] * 2 + 30.0) + (badges + 16.0) + (m["rain_gap"] + m["rain"])
    card = app.fleet_bounds(m["advance"])[0] - 2 * m["window_padding"] - m["scrollbar"]
    assert card - m["group_indent"] - beside >= m["title_min"]
    # And one advance less would not hold it: the bound is the least, not a guess.
    smaller = card - m["advance"] - m["group_indent"] - beside
    assert smaller < m["title_min"]


# -- the dock, in the runner ---------------------------------------------------------


def _panels() -> dict[str, Any]:
    names = ("FLEET", "NEEDS YOU", "CONTEXT", "DETAIL", "BOARD", "DIGEST", "BRIEF")
    return {name: (lambda: None) for name in (*names, "SESSION", "HEALTH", "LOG")}


def _child(ini: str, layout: str, script: str) -> None:
    """
    One launch over ``ini`` in ``layout``, running ``script``'s steps; prints
    what each step read, as JSON.
    """
    from imgui_bundle import hello_imgui, imgui

    layouts = [app._triage_layout(_panels()), app._focus_layout(_panels())]
    if layout == app.FOCUS:
        layouts.reverse()
    runner = hello_imgui.RunnerParams()
    runner.app_window_params.window_geometry.size = (1600, 1000)
    runner.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
    )
    runner.docking_params = layouts[0]
    runner.alternative_docking_layouts = layouts[1:]
    runner.remember_selected_alternative_layout = True
    runner.ini_folder_type = hello_imgui.IniFolderType.absolute_path
    runner.ini_filename = ini
    runner.platform_backend_type = hello_imgui.PlatformBackendType.null
    runner.renderer_backend_type = hello_imgui.RendererBackendType.null
    runner.fps_idling.enable_idling = False
    frame = [0]
    seen: dict[str, Any] = {}
    io = [None]

    def fonts() -> None:
        imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures

    def window(n: str) -> Any:
        return imgui.internal.find_window_by_name(n)

    def node(n: str) -> Any:
        w = window(n)
        return None if w is None else w.dock_node

    def fleet() -> dict[str, Any]:
        n = node("FLEET")
        w = window("FLEET")
        return {
            "width": float(n.size.x),
            "right": float(n.pos.x + n.size.x),
            "mid_x": float(n.pos.x + n.size.x / 2),
            "mid_y": float(n.pos.y + n.size.y / 2),
            "flags": int(n.local_flags),
            "tab_bar": not (n.is_hidden_tab_bar() or n.is_no_tab_bar()),
            "close": bool(w.has_close_button),
            "dock": int(w.dock_id),
            "floating": bool(n.is_floating_node()),
            "advance": float(imgui.calc_text_size("M").x),
        }

    def tab(n: str) -> tuple[float, float]:
        bar = node(n).tab_bar
        item = next(t for t in bar.tabs if t.id_ == window(n).tab_id)
        return float(bar.bar_rect.min.x + item.offset + item.width / 2), float(
            (bar.bar_rect.min.y + bar.bar_rect.max.y) / 2
        )

    # Each step: at which frame, and what to do. A drag is a press, moves, a release.
    steps: list[tuple[int, Any]] = []

    def at(f: int, fn: Any) -> None:
        steps.append((f, fn))

    def drag(start: int, frm: Any, to: Any) -> None:
        def press() -> None:
            x, y = frm()
            io[0].add_mouse_pos_event(x, y)

        def down() -> None:
            io[0].add_mouse_button_event(0, True)

        def moves(k: int) -> Any:
            def go() -> None:
                (x0, y0), (x1, y1) = frm_at[0], to()
                t = k / 8.0
                io[0].add_mouse_pos_event(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)

            return go

        frm_at: list[Any] = [None]

        def remember() -> None:
            frm_at[0] = frm()

        at(start, remember)
        at(start, press)
        at(start + 2, down)
        for k in range(1, 9):
            at(start + 3 + k, moves(k))
        at(start + 14, lambda: io[0].add_mouse_button_event(0, False))

    def record(key: str) -> Any:
        return lambda: seen.__setitem__(key, fleet())

    def docks() -> None:
        seen["docks"] = {
            n: int(window(n).dock_id)
            for n in ("FLEET", "NEEDS YOU", "DETAIL", "BOARD", "DIGEST", "SESSION", "HEALTH")
            if window(n) is not None
        }

    at(8, record("start"))
    at(8, docks)
    if script == "splitter":
        drag(
            10,
            lambda: (seen["start"]["right"] + 1.0, seen["start"]["mid_y"]),
            lambda: (60.0, 500.0),
        )
        at(40, record("narrowest"))
        drag(42, lambda: (seen["narrowest"]["right"] + 1.0, 500.0), lambda: (1500.0, 500.0))
        at(72, record("widest"))
    elif script == "undock":
        drag(10, lambda: tab("FLEET"), lambda: (800.0, 500.0))
        at(40, record("after"))
    elif script == "dock_into":
        other = "BOARD"
        # Onto the middle of the fleet's node: where ImGui draws the target
        # that docks a pane as a tab there.
        drag(10, lambda: tab(other), lambda: (seen["start"]["mid_x"], seen["start"]["mid_y"]))
        at(40, docks)
    at(80, lambda: setattr(runner, "app_shall_exit", True))

    def pre() -> None:
        frame[0] += 1
        io[0] = imgui.get_io()
        for f, fn in steps:
            if f == frame[0]:
                fn()

    runner.callbacks.load_additional_fonts = fonts
    runner.callbacks.pre_new_frame = pre
    hello_imgui.run(runner)
    print(json.dumps(seen))


def _launch(ini: Path, layout: str, script: str) -> dict[str, Any]:
    done = subprocess.run(
        [sys.executable, __file__, str(ini), layout, script],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
        # The child imports this tree's pptmstr, whatever is installed: this
        # file's own directory is the child's first path, and not the tree.
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert done.returncode == 0, done.stderr[-2000:]
    seen: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    return seen


LAYOUTS = pytest.mark.parametrize("layout", [app.TRIAGE, app.FOCUS])


@LAYOUTS
def test_the_fleet_is_headed_by_its_tab_with_no_close_and_its_flags(
    tmp_path: Path, layout: str
) -> None:
    pytest.importorskip("imgui_bundle")
    start = _launch(tmp_path / "x.ini", layout, "none")["start"]
    assert start["tab_bar"], "the 'fleet' header is the node's tab, and it shows"
    assert not start["close"]
    assert start["flags"] & app.FLEET_NODE_FLAGS == app.FLEET_NODE_FLAGS
    low, high = app.fleet_bounds(start["advance"])
    assert low <= start["width"] <= high


@LAYOUTS
def test_a_drag_of_the_splitter_stops_at_each_bound(tmp_path: Path, layout: str) -> None:
    pytest.importorskip("imgui_bundle")
    seen = _launch(tmp_path / "x.ini", layout, "splitter")
    low, high = app.fleet_bounds(seen["start"]["advance"])
    assert seen["narrowest"]["width"] == pytest.approx(low, abs=1.0)
    assert seen["widest"]["width"] == pytest.approx(high, abs=1.0)


@LAYOUTS
def test_the_fleet_cannot_be_dragged_out_of_its_node(tmp_path: Path, layout: str) -> None:
    pytest.importorskip("imgui_bundle")
    seen = _launch(tmp_path / "x.ini", layout, "undock")
    assert seen["after"]["dock"] == seen["start"]["dock"] and not seen["after"]["floating"]


def test_no_pane_can_be_dragged_into_the_fleets_node(tmp_path: Path) -> None:
    pytest.importorskip("imgui_bundle")
    seen = _launch(tmp_path / "x.ini", app.TRIAGE, "dock_into")
    fleet = seen["start"]["dock"]
    assert seen["docks"]["FLEET"] == fleet
    assert seen["docks"]["BOARD"] != fleet


def _rewrite_fleet_width(ini: Path, width: int) -> None:
    """The saved layout's fleet node, asked to be ``width`` wide."""
    text = ini.read_text()
    block = re.search(r"\[Window\]\[FLEET\][^\[]*?DockId=(0x[0-9A-Fa-f]+)", text)
    assert block, text
    node = block.group(1)
    pattern = re.compile(rf"(DockNode\s+ID={node}\b[^\n]*?SizeRef=)(\d+)")
    assert pattern.search(text), text
    ini.write_text(pattern.sub(lambda m: f"{m.group(1)}{width}", text))


@pytest.mark.parametrize("width", [40, 3000], ids=["too-narrow", "too-wide"])
def test_a_saved_width_out_of_bounds_is_held_and_every_other_pane_keeps_its_place(
    tmp_path: Path, width: int
) -> None:
    pytest.importorskip("imgui_bundle")
    ini = tmp_path / "pptmstr.ini"
    first = _launch(ini, app.TRIAGE, "none")
    _rewrite_fleet_width(ini, width)
    second = _launch(ini, app.TRIAGE, "none")
    low, high = app.fleet_bounds(second["start"]["advance"])
    assert low <= second["start"]["width"] <= high
    assert second["docks"] == first["docks"]
    # ImGui saves none of the flags that keep the node shut: they are set again.
    assert second["start"]["flags"] & app.FLEET_NODE_FLAGS == app.FLEET_NODE_FLAGS


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    _child(sys.argv[1], sys.argv[2], sys.argv[3])


@pytest.mark.parametrize("build", [app._triage_layout, app._focus_layout], ids=["triage", "focus"])
def test_both_layouts_declare_the_fleet_shut_shown_and_unclosable(build: Any) -> None:
    params = build(_panels())
    (split,) = [s for s in params.docking_splits if s.new_dock == "RailSpace"]
    assert split.node_flags == app.FLEET_NODE_FLAGS
    (fleet,) = [w for w in params.dockable_windows if w.label == "FLEET"]
    assert fleet.dock_space_name == "RailSpace"
    assert not fleet.can_be_closed and fleet.is_visible and not fleet.remember_is_visible
    # The header stays: nothing that hides the node's tab bar is among the flags.
    hide = (
        int(app.imgui.DockNodeFlags_.auto_hide_tab_bar)
        | int(app.imgui.internal.DockNodeFlagsPrivate_.no_tab_bar)
        | int(app.imgui.internal.DockNodeFlagsPrivate_.hidden_tab_bar)
    )
    assert not app.FLEET_NODE_FLAGS & hide
