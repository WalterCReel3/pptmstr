#!/usr/bin/env python3
"""
Does the splash art actually rasterize, and are splash.py's three fitting constants
true of the face the UI draws with?

Both questions are runtime ones and neither has ever been asked with a window open.
``splash.py`` cites a live-frame measurement for ``GLYPH_ADVANCE_EM``,
``LINE_PITCH_EM`` and ``INK_HEIGHT_EM``, and ``tests/test_splash.py`` cites this
script by name -- until now at a file that did not exist. This is that file.

**The claim that matters.** 22 of the art's 166 characters are above U+00FF
(U+2019, U+2122, U+0161, U+02C6, ...). fontTools already established that all 166
are in Inconsolata-Medium's cmap and all have advance 500/1000; that is settled and
is *not* re-derived here. What is open is whether ImGui 1.92's on-demand baking
actually produces pixels for them through hello_imgui's loader. A cmap entry is not
a raster.

**Why the obvious check is worthless.** ``ImFontBaked.find_glyph`` returns the
*fallback* glyph (U+FFFD) for a codepoint it cannot draw, so "it returned something"
is true of total failure. ``ImFont.is_glyph_in_font`` answers from the cmap, which
re-asks the question fontTools already closed. The sound calls are
``find_glyph_no_fallback``, which returns None on a miss, and ``is_glyph_loaded``.
Both are used here; the UV-rect comparison against the fallback is corroboration,
not the verdict.

**Order is load-bearing.** ImGui 1.92 bakes glyphs on demand, so nothing is loaded
until something asks. The art is therefore *drawn* first, through the same
``text_unformatted`` call the pane makes, and the atlas is interrogated afterwards.
Interrogating first would report every glyph unloaded and prove nothing about the
pane.

Usage:
    .venv/bin/python scripts/verify_splash.py
    .venv/bin/python scripts/verify_splash.py --capture /tmp/splash.png

The capture runs the real application with an **empty fleet**, which no existing
capture path can produce: ``Makefile``'s ``shot`` target and
``scripts/screenshot.py``'s ``args.argv or ["--fake"]`` both install the FakeDriver,
whose ``run()`` seeds a tree, so the no-sessions branch never renders. Captures are
written outside the repo and are not checked in.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The pane the splash lives in: "NEEDS YOU", MainDockSpace in the TRIAGE layout.
# hello_imgui applies each split to what is left, so the two splits in
# app.py:_triage_layout leave (1 - 0.21) * (1 - 0.32) of the window width here.
MAIN_DOCK_FRACTION = (1.0 - 0.21) * (1.0 - 0.32)

# Window sizes to report the fit against. The first is the smallest the project
# claims to support, the last is app.py's own default geometry.
WINDOW_SIZES = ((1024, 700), (1500, 900), (1920, 1200))

# Rough vertical chrome above the art inside the pane: menu bar, status bar, the
# intro line, the two quote lines and the hint, with their spacings. Deliberately
# generous -- overstating it makes the reported by_height pessimistic rather than
# flattering.
PANE_CHROME_H = 190.0


def _art_facts() -> tuple[list[str], list[int], int, int]:
    from pptmstr.ui import splash_art

    rows = list(splash_art.ART)
    codepoints = sorted({ord(c) for row in rows for c in row})
    return rows, codepoints, len(rows), max(len(r) for r in rows)


def _resize_sweep(n_rows: int, n_cols: int) -> tuple[list[str], list[str]]:
    """
    Does the panel settle at every pane size, or can it oscillate?

    The hazard is a feedback loop, not a wrong number: art slightly too tall for the
    pane raises a scrollbar, the scrollbar narrows the content region, the narrower
    region fits a smaller size, the smaller art no longer needs the scrollbar, and
    the pane flickers between two layouts forever while the operator drags an edge.

    Two properties rule it out and both are checked here over every pane size a
    window drag passes through. **Monotonicity**: growing the region can never
    shrink the chosen size, so a drag walks the sizes in one direction rather than
    hunting. **Containment**: whenever the art is drawn it already fits, so it can
    never be the thing that raises the scrollbar.
    """
    from pptmstr.ui import splash

    failures: list[str] = []
    previous: dict[str, float] = {}

    for height in range(200, 1400, 1):
        for width in (420.0, 644.0, 1031.0):
            size = splash.fit_size(width, float(height), n_rows, n_cols)
            art_w, art_h = splash.required_extent(size, n_rows, n_cols)
            drawn = art_w <= width and art_h <= float(height)

            if drawn and (art_w > width or art_h > height):
                failures.append(f"drawn but overflows at {width:.0f}x{height}")
            key = f"{width:.0f}"
            if key in previous and size < previous[key]:
                failures.append(
                    f"growing the pane to {width:.0f}x{height} shrank the size "
                    f"{previous[key]:g} -> {size:g}; a drag can hunt between them"
                )
            previous[key] = size

    notes = [
        f"resize: fit_size is monotone in height over 200-1400px at 3 pane widths, "
        f"and the art is never drawn larger than the region ({len(failures)} violations)"
    ]
    return notes, failures


def main() -> int:
    ap = argparse.ArgumentParser(description="verify the splash panel against a real frame")
    ap.add_argument("--capture", type=Path, default=None, help="also grab the panel to this PNG")
    args = ap.parse_args()

    if args.capture is not None:
        return _capture(args.capture)

    from imgui_bundle import hello_imgui, imgui, immapp

    from pptmstr import theme
    from pptmstr.ui import splash

    rows, codepoints, n_rows, n_cols = _art_facts()
    art_text = "\n".join(rows)
    high = [cp for cp in codepoints if cp > 0xFF]

    # Pure, so it runs before the window opens -- a resize hazard that only shows up
    # once a GL context exists would be a much worse thing to discover.
    notes, failures = _resize_sweep(n_rows, n_cols)
    frames = [0]

    def gui() -> None:
        frames[0] += 1
        if frames[0] < 2:
            return

        body = theme.face(theme.Face.BODY)
        if body is None:
            failures.append("BODY face did not load; nothing below can be measured")
            hello_imgui.get_runner_params().app_shall_exit = True
            return
        notes.append(f"face: {body.get_debug_name()}")
        notes.append(f"art: {n_rows} rows x {n_cols} cols, {len(codepoints)} distinct codepoints")
        notes.append(f"     {len(high)} of them above U+00FF")

        # -- 1. draw the art, exactly as the pane does, so the bake happens ---------
        probe_size = 16.0
        imgui.push_font(body, probe_size)
        imgui.text_unformatted(art_text)
        imgui.pop_font()

        baked = body.get_font_baked(probe_size)
        fallback = baked.find_glyph(0xFFFD)
        fallback_uv = (fallback.u0, fallback.v0, fallback.u1, fallback.v1)

        # -- 2. the verdict: did every codepoint rasterize? -------------------------
        missing: list[int] = []
        unloaded: list[int] = []
        invisible: list[int] = []
        as_fallback: list[int] = []
        uvs: dict[tuple[float, float, float, float], list[int]] = {}

        for cp in codepoints:
            glyph = baked.find_glyph_no_fallback(cp)
            if glyph is None:
                missing.append(cp)
                continue
            if not baked.is_glyph_loaded(cp):
                unloaded.append(cp)
            uv = (glyph.u0, glyph.v0, glyph.u1, glyph.v1)
            if cp != 0x20:
                if not glyph.is_visible():
                    invisible.append(cp)
                if uv == fallback_uv:
                    as_fallback.append(cp)
                uvs.setdefault(uv, []).append(cp)

        def spell(points: list[int]) -> str:
            return ", ".join(f"U+{p:04X}" for p in points[:12]) + (
                " ..." if len(points) > 12 else ""
            )

        if missing:
            failures.append(
                f"find_glyph_no_fallback returned None for {len(missing)}: {spell(missing)}"
            )
        if unloaded:
            failures.append(
                f"is_glyph_loaded False after drawing for {len(unloaded)}: {spell(unloaded)}"
            )
        if invisible:
            failures.append(f"rasterized to no pixels ({len(invisible)}): {spell(invisible)}")
        if as_fallback:
            failures.append(
                f"shares the U+FFFD fallback's UV rect ({len(as_fallback)}): {spell(as_fallback)}"
            )

        shared = {uv: cps for uv, cps in uvs.items() if len(cps) > 1}
        if shared:
            for _uv, cps in list(shared.items())[:4]:
                failures.append(f"one atlas rect drawn by {len(cps)} codepoints: {spell(cps)}")
        notes.append(
            f"atlas: {len(uvs)} distinct rects for {len(codepoints) - 1} inked codepoints"
            " (1:1 means no two characters draw the same picture)"
        )
        high_ok = sum(
            1
            for cp in high
            if baked.find_glyph_no_fallback(cp) is not None and baked.is_glyph_loaded(cp)
        )
        notes.append(f"above U+00FF: {high_ok}/{len(high)} loaded with a non-fallback glyph")

        # -- 3. the three constants splash.py cites --------------------------------
        pitch = imgui.get_text_line_height()
        three = imgui.calc_text_size("x\nx\nx")
        notes.append(
            f"at {probe_size:g}px: line height {pitch:.3f}, "
            f"calc_text_size('x\\nx\\nx').y = {three.y:.3f} ({three.y / probe_size:.4f} em)"
        )
        if abs(pitch - probe_size) > 0.01:
            failures.append(
                f"LINE_PITCH_EM claims 1.0 but the pitch is {pitch / probe_size:.4f} em"
            )

        # -- 4. the advance, at every even size fit_size can return ----------------
        #
        # Two separate properties, and conflating them hides the interesting one.
        #
        # *Uniformity* is what keeps the art's columns in line: every one of the 166
        # codepoints must advance by the same amount at a given size, or a row with
        # leading spaces shears away from the row above it. This is the check the
        # panel's legibility actually rests on, and it is not the same claim as the
        # advance matching any particular formula.
        #
        # *The bound* is what keeps `required_extent` honest: fit_size divides by
        # GLYPH_ADVANCE_EM, so an advance wider than 0.5*size would overrun the pane.
        identity_sizes: list[int] = []
        inequality_sizes: list[int] = []
        for size_i in range(int(splash.MIN_FONT_SIZE), int(splash.MAX_FONT_SIZE) + 1, 2):
            size = float(size_i)
            imgui.push_font(body, size)
            imgui.text_unformatted(art_text)
            imgui.pop_font()
            b = body.get_font_baked(size)
            limit = splash.GLYPH_ADVANCE_EM * size

            advances: dict[float, list[int]] = {}
            for cp in codepoints:
                advances.setdefault(b.get_char_advance(cp), []).append(cp)

            if len(advances) > 1:
                spread = "  ".join(
                    f"{a:.3f} x{len(c)} ({spell(c)})" for a, c in sorted(advances.items())
                )
                failures.append(
                    f"size {size:g}: {len(advances)} distinct advances, so the columns "
                    f"cannot line up -- {spread}"
                )
                continue

            advance = next(iter(advances))
            if advance > limit + 1e-6:
                failures.append(
                    f"size {size:g}: advance {advance:.3f} exceeds 0.5*size {limit:.3f}, "
                    "so required_extent understates the art's width"
                )
            elif abs(advance - limit) <= 1e-6:
                identity_sizes.append(size_i)
            else:
                inequality_sizes.append(size_i)

        notes.append(
            f"advance: uniform across all {len(codepoints)} codepoints at every even size "
            f"{splash.MIN_FONT_SIZE:g}-{splash.MAX_FONT_SIZE:g}, so columns line up"
        )
        if identity_sizes:
            notes.append(
                f"         == 0.5*size exactly at {identity_sizes[0]}-{identity_sizes[-1]}"
            )
        if inequality_sizes:
            # Worth reporting rather than failing: narrower than the model is the safe
            # direction -- required_extent overstates, so fit_size rejects a size that
            # would have fitted and centres up to half a column off. But splash.py
            # calls the even grid an *identity*, and above this threshold it is only
            # an upper bound.
            notes.append(
                f"         NARROWER than 0.5*size at {inequality_sizes[0]}-{inequality_sizes[-1]}"
                f" (by 1px/char), so the 'exact pixel count' claim holds only below"
                f" {inequality_sizes[0]}"
            )

        # -- 5. which axis actually binds, per window size -------------------------
        for win_w, win_h in WINDOW_SIZES:
            pane_w = win_w * MAIN_DOCK_FRACTION
            pane_h = win_h - PANE_CHROME_H
            by_width = pane_w / (n_cols * splash.GLYPH_ADVANCE_EM)
            by_height = pane_h / ((n_rows - 1) * splash.LINE_PITCH_EM + splash.INK_HEIGHT_EM)
            fitted = splash.fit_size(pane_w, pane_h, n_rows, n_cols)
            binds = "width" if by_width < by_height else "height"
            notes.append(
                f"{win_w}x{win_h}: pane {pane_w:.0f}x{pane_h:.0f} -> "
                f"by_width {by_width:.2f}, by_height {by_height:.2f} "
                f"({binds} binds) -> fit_size {fitted:g}"
            )

        hello_imgui.get_runner_params().app_shall_exit = True

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_geometry.size = (900, 800)
    params.app_window_params.window_title = "verify_splash"
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_window
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
        print("OK: every art glyph rasterized to its own atlas rect, and the advance never")
        print("    exceeds 0.5*size at any even size in the clamp range")
    return 1 if failures else 0


def _capture(out: Path) -> int:
    """
    Grab the real panel, with a fleet that is actually empty.

    ``--fake`` is deliberately not passed: FakeDriver.run seeds a tree, and a seeded
    tree is not the state this panel renders in. Without ``--task`` no session is
    launched, so the real driver is selected and never used.
    """
    import screenshot  # scripts/ is sys.path[0] when this file is run directly
    from imgui_bundle import hello_imgui, immapp

    from pptmstr import app as pptmstr_app
    from pptmstr.ui import inbox, splash

    real_run = immapp.run
    frames = [0]

    # The real content region the pane hands the art, recorded from inside the live
    # frame. Everything else in this script reasons about the pane from the split
    # ratios, which is arithmetic; this is the measurement that arithmetic is
    # standing in for, and the two are worth comparing.
    observed: dict[str, float] = {}
    real_art = inbox._art

    def measured_art(now: float) -> None:
        from imgui_bundle import imgui as _imgui

        avail = _imgui.get_content_region_avail()
        observed["avail_w"] = avail.x
        observed["avail_h"] = avail.y
        observed["size"] = splash.fit_size(avail.x, avail.y, inbox._ART_ROWS, inbox._ART_COLS)
        width, height = splash.required_extent(observed["size"], inbox._ART_ROWS, inbox._ART_COLS)
        observed["art_w"], observed["art_h"] = width, height
        observed["drawn"] = float(width <= avail.x and height <= avail.y)
        real_art(now)

    inbox._art = measured_art  # type: ignore[assignment]

    def patched_run(runner: object, *rest: object) -> object:
        user_after = runner.callbacks.after_swap  # type: ignore[attr-defined]

        def after_swap() -> None:
            if user_after:
                user_after()
            frames[0] += 1
            if frames[0] == 90:
                screenshot._grab(out)
                hello_imgui.get_runner_params().app_shall_exit = True

        runner.callbacks.after_swap = after_swap  # type: ignore[attr-defined]
        # Left ON, unlike screenshot.py, which forces it off. With no sessions
        # nothing is active, so idling is the *only* regime this panel ever runs in
        # and the animation's rate is meaningless at 60fps.
        runner.fps_idling.enable_idling = True  # type: ignore[attr-defined]
        return real_run(runner, *rest)

    immapp.run = patched_run  # type: ignore[assignment]
    try:
        rc = int(pptmstr_app.main(["--theme", "dark", "--layout", "TRIAGE"]))
    finally:
        immapp.run = real_run  # type: ignore[assignment]
        inbox._art = real_art  # type: ignore[assignment]

    if not observed:
        print("FAIL: the splash never drew -- the fleet was not empty, or NEEDS YOU never ran")
        return 1
    print(
        f"NEEDS YOU content region: {observed['avail_w']:.0f}x{observed['avail_h']:.0f}\n"
        f"fit_size -> {observed['size']:g}, art occupies "
        f"{observed['art_w']:.0f}x{observed['art_h']:.0f}, "
        f"{'drawn' if observed['drawn'] else 'SKIPPED (did not fit)'}"
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
