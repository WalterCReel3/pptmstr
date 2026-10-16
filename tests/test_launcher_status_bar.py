"""
The status bar the launcher and the resume form keep clear of, as hello_imgui itself
draws it.

``launcher.modal_area`` finds the bar by its window name, and the chrome the
placement tests draw uses the same name, so a hello_imgui that renamed its window
would pass those tests while every modal went back to covering the bar. This runs
the real runner, on its null backends in a child process as ``test_tabs``'s
saved-layout tests do, with the bar shown, and reads the window from inside it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _child(ini: str) -> None:
    """One launch with the menu and status bars shown; prints what frame 10 saw."""
    from imgui_bundle import hello_imgui, imgui

    from pptmstr.ui import launcher

    runner = hello_imgui.RunnerParams()
    runner.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
    )
    runner.imgui_window_params.show_menu_bar = True
    runner.imgui_window_params.show_status_bar = True
    runner.ini_folder_type = hello_imgui.IniFolderType.absolute_path
    runner.ini_filename = ini
    runner.platform_backend_type = hello_imgui.PlatformBackendType.null
    runner.renderer_backend_type = hello_imgui.RendererBackendType.null
    runner.fps_idling.enable_idling = False
    frame = [0]
    seen: dict[str, object] = {}

    def fonts() -> None:
        imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures

    def gui() -> None:
        # Inside the frame, where the launcher asks: the bar is last frame's.
        frame[0] += 1
        if frame[0] == 10:
            viewport = imgui.get_main_viewport()
            bar = imgui.internal.find_window_by_name(launcher.STATUS_BAR_WINDOW)
            seen["viewport"] = [viewport.pos.x, viewport.pos.y, viewport.size.x, viewport.size.y]
            seen["work"] = [
                viewport.work_pos.x,
                viewport.work_pos.y,
                viewport.work_size.x,
                viewport.work_size.y,
            ]
            seen["frame_height"] = imgui.get_frame_height()
            if bar is not None:
                seen["bar"] = [bar.pos.x, bar.pos.y, bar.size.x, bar.size.y]
                seen["bar_drawn"] = bool(bar.active or bar.was_active)
            (left, top), (width, height) = launcher.modal_area()
            seen["area"] = [left, top, width, height]
        if frame[0] == 12:
            runner.app_shall_exit = True

    runner.callbacks.load_additional_fonts = fonts
    runner.callbacks.show_gui = gui
    runner.callbacks.show_status = lambda: imgui.text("11 agents")
    hello_imgui.run(runner)
    print(json.dumps(seen))


@pytest.fixture(scope="module")
def seen(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[float]]:
    pytest.importorskip("imgui_bundle")
    ini = tmp_path_factory.mktemp("status-bar") / "pptmstr.ini"
    done = subprocess.run(
        [sys.executable, __file__, str(ini)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    answer: dict[str, list[float]] = json.loads(done.stdout.strip().splitlines()[-1])
    return answer


def test_the_runner_drew_a_display(seen: dict[str, list[float]]) -> None:
    """
    The null platform backend keeps a display of its own and does not take
    ``window_geometry.size`` (measured on imgui_bundle 1.92.9: 1920x1080 whatever is
    asked), so nothing below depends on the size.
    """
    vx, vy, vwidth, vheight = seen["viewport"]
    assert (vx, vy) == (0.0, 0.0) and vwidth > 0.0 and vheight > 0.0


def test_hello_imgui_draws_its_status_bar_under_the_name_the_launcher_looks_for(
    seen: dict[str, list[float]],
) -> None:
    """
    If this fails on an imgui_bundle bump, hello_imgui's menu_statusbar.cpp has
    renamed the window: ``launcher.STATUS_BAR_WINDOW`` follows it, and so do the
    chrome the placement tests draw.
    """
    assert "bar" in seen, "no window by launcher.STATUS_BAR_WINDOW's name"
    assert seen["bar_drawn"]


def test_that_window_is_the_bar_across_the_bottom_of_the_viewport(
    seen: dict[str, list[float]],
) -> None:
    """
    The window found by the name is the bar and not some other one: the full width
    of the viewport, its top 1.4 frame heights over the viewport's foot, as
    hello_imgui puts it. Its height is not held to that: Dear ImGui's minimum window
    size takes the 26.6 px asked for at a 19 px frame to 32, past the foot. And the
    work area hello_imgui hands the panes runs under the bar, which is why the modals
    take it out themselves.
    """
    x, y, width, height = seen["bar"]
    vx, vy, vwidth, vheight = seen["viewport"]
    _, work_top, _, work_height = seen["work"]
    frame_height = float(seen["frame_height"])  # type: ignore[arg-type]
    assert (x, width) == (vx, vwidth)
    assert abs(y - (vy + vheight - 1.4 * frame_height)) <= 1.0, (y, vheight, frame_height)
    assert y + height >= vy + vheight
    assert work_top + work_height > y, "the work area already leaves the bar out"


def test_the_modal_area_stops_at_the_real_bar(seen: dict[str, list[float]]) -> None:
    _, bar_top, _, _ = seen["bar"]
    left, top, width, height = seen["area"]
    work_left, work_top, work_width, _ = seen["work"]
    assert (left, top, width) == (work_left, work_top, work_width)
    assert top > 0.0, "the menu bar is not drawn"
    assert top + height == bar_top


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    _child(sys.argv[1])
