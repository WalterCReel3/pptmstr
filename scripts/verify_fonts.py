#!/usr/bin/env python3
"""
Are the faces the ones we think they are, and is the UI font still the UI font?

Font loading fails quietly. A wrong path, a shadowed asset, or a bold face that is
really a second copy of the regular one all produce a window that opens and renders
text -- just not the text intended. Three things are worth pinning:

  1. **Both faces load, and they are distinct objects** from the files named.
  2. **Bold is actually bold.** Inconsolata is monospaced, so Medium and Bold have
     identical advance widths and no width comparison can tell them apart. The ink
     is what differs: the glyph's drawn box is wider inside the same advance. That
     is the check that catches vendoring the wrong file.
  3. **Inconsolata is still Fonts[0]**, the application default. Adding a face is
     the operation most likely to break this, and breaking it reskins every panel
     rather than failing.

Also re-checks that the FontAwesome 6 range survived, since the icon merge and the
new face are both arguments to the same loader.

Usage:  .venv/bin/python scripts/verify_fonts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ICON_FA_BRAIN. Beyond FontAwesome 4's ceiling, so it is the glyph that proves the
# FA6 default is in effect rather than merely configured.
BRAIN = 0xF5DC
PROBE_CHAR = ord("M")


def main() -> int:
    from imgui_bundle import hello_imgui, imgui, immapp

    from pptmstr import theme

    failures: list[str] = []
    notes: list[str] = []
    frames = [0]

    def gui() -> None:
        frames[0] += 1
        if frames[0] < 2:
            return

        body = theme.face(theme.Face.BODY)
        bold = theme.face(theme.Face.BOLD)

        for name, font in (("BODY", body), ("BOLD", bold)):
            if font is None:
                failures.append(f"{name} did not load")
            elif not font.is_loaded():
                failures.append(f"{name} loaded but reports is_loaded() False")
        if body is None or bold is None:
            hello_imgui.get_runner_params().app_shall_exit = True
            return

        notes.append(f"BODY: {body.get_debug_name()}")
        notes.append(f"BOLD: {bold.get_debug_name()}")
        if body is bold:
            failures.append("BODY and BOLD are the same ImFont")
        if "Bold" not in bold.get_debug_name():
            failures.append(f"BOLD came from {bold.get_debug_name()!r}")

        # Same advance, more ink: the monospace signature of a real bold face.
        size = 16.0
        thin = body.get_font_baked(size).find_glyph(PROBE_CHAR)
        thick = bold.get_font_baked(size).find_glyph(PROBE_CHAR)
        thin_w, thick_w = thin.x1 - thin.x0, thick.x1 - thick.x0
        notes.append(
            f"'M' at {size:g}px -- advance {thin.advance_x:.2f}/{thick.advance_x:.2f}, "
            f"ink {thin_w:.2f}/{thick_w:.2f}"
        )
        if abs(thin.advance_x - thick.advance_x) > 0.01:
            failures.append("advance widths differ; the pane's column alignment relies on them")
        if thick_w <= thin_w:
            failures.append(f"bold ink {thick_w:.2f} is not wider than regular {thin_w:.2f}")

        # Fonts[0] is what NewFrame binds as the default.
        first = imgui.get_io().fonts.fonts[0]
        notes.append(f"Fonts[0]: {first.get_debug_name()}")
        if first is not body:
            failures.append(f"the app default is {first.get_debug_name()!r}, not the body face")

        if not body.is_glyph_in_font(BRAIN):
            failures.append("ICON_FA_BRAIN missing from the body face (FontAwesome 6 regressed)")
        if bold.is_glyph_in_font(BRAIN):
            notes.append("note: bold carries FontAwesome glyphs too")

        hello_imgui.get_runner_params().app_shall_exit = True

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_geometry.size = (640, 400)
    params.app_window_params.window_title = "verify_fonts"
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.callbacks.load_additional_fonts = theme.load_fonts
    params.callbacks.show_gui = gui
    params.fps_idling.enable_idling = False
    immapp.run(params)

    for note in notes:
        print(note)
    for line in failures:
        print(f"FAIL: {line}")
    if not failures:
        print("OK: both faces load, bold is bold, Inconsolata is still the default")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
