#!/usr/bin/env python3
"""Open a real window via immapp, render a few frames, exit cleanly.

This is the only check in the probe that actually proves the stack works: the
wheel loaded, dyld resolved the bundled GLFW, a GL context came up, and the
event loop can be torn down without a crash. Everything else in probe.py is
diagnostics for when this fails.

Prints machine-readable lines on stdout:
    DPI_SCALE=<float>
    FRAMES=<int>
"""

from __future__ import annotations

import sys

TARGET_FRAMES = 3


def framebuffer_scale() -> float:
    from imgui_bundle import hello_imgui, imgui

    io = imgui.get_io()
    scale = getattr(io, "display_framebuffer_scale", None)
    if scale is not None:
        try:
            return float(scale.x)
        except AttributeError:
            return float(scale[0])
    for name in ("dpi_font_loading_factor", "dpi_window_size_factor"):
        fn = getattr(hello_imgui, name, None)
        if callable(fn):
            return float(fn())
    return 1.0


def main() -> int:
    try:
        from imgui_bundle import hello_imgui, imgui, immapp
    except Exception as exc:
        print(f"import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    state = {"frames": 0, "scale": 1.0}

    def gui() -> None:
        state["frames"] += 1
        if state["frames"] == 1:
            state["scale"] = framebuffer_scale()
        imgui.text(f"pptmstr probe frame {state['frames']}")
        # Immediate-mode teardown: there is no window object to close, only a flag
        # the runner reads at the end of the frame.
        if state["frames"] >= TARGET_FRAMES:
            hello_imgui.get_runner_params().app_shall_exit = True

    params = hello_imgui.RunnerParams()
    params.callbacks.show_gui = gui
    params.app_window_params.window_title = "pptmstr probe"
    params.app_window_params.window_geometry.size = (360, 200)
    params.fps_idling.enable_idling = False
    # hello_imgui slugifies the window title into an ini filename and, by default,
    # drops it in the current directory -- so a bare run leaves a stray
    # pptmstr_probe.ini in whatever tree it was invoked from. The probe must not
    # write to the repo it is checking.
    params.ini_folder_type = hello_imgui.IniFolderType.temp_folder

    try:
        immapp.run(params)
    except Exception as exc:
        print(f"runner failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    print(f"DPI_SCALE={state['scale']:g}")
    print(f"FRAMES={state['frames']}")
    return 0 if state["frames"] >= TARGET_FRAMES else 4


if __name__ == "__main__":
    raise SystemExit(main())
