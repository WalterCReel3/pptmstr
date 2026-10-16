#!/usr/bin/env python3
"""
Render the app under a few look-and-feel variants, on the same screens, for the
operator to pick one (planning/2026-10-15-ui-rework.md U9, U10).

Nothing here changes what the app draws. A variant is a set of ImGuiStyle
overrides laid over what ``theme.apply_style`` set, and it exists only in this
process: the probe wraps ``theme.apply_style`` at runtime so the variant lands
after it every time the app pushes its palette, and wraps ``app._draw_overlays``
to add the two drawings no style value can produce -- the inset tab shape and
the terminal-style tool block. ``theme.py`` and every module under ``pptmstr/``
are left as they are.

    scripts/style_probe.py --all                 # every variant, each on its own Xvfb
    scripts/style_probe.py --variant soft        # one variant, on the display there is

``--all`` runs ``scripts/xvfb_shot.sh -- <this script> --variant NAME`` once per
variant, so it works headless. Each run drives ``scripts/screenshot.py``'s
machinery -- its frame grab, its scratch configuration, its scenario loop -- with
one scenario registered here, and writes ``shots/style-probe/<variant>/<screen>.png``.

The variant table is data so that a test can hold it to its shape and so that the
planning record can quote exact values: ``VARIANTS``, ``apply`` and ``revert``
are what ``tests/test_style_probe.py`` exercises against a bare ImGui context.

**The terminal block is a mock-up.** It draws the intended look of U1/U5's tool
block from fixed text; nothing in it is read from a transcript.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "shots" / "style-probe"
SCENARIO = "style-probe"

# The screens every variant is shot on, in the order the scenario reaches them.
SCREENS = ("triage", "board", "focus", "terminal-block", "settings", "launcher")


@dataclass(frozen=True, slots=True)
class Mix:
    """
    A colour as ``theme.mixed`` makes one: ``over`` at ``alpha`` on ``base``,
    opaque, both palette roles. Read from the palette in force when applied, so a
    variant follows a theme switch.
    """

    base: str
    over: str
    alpha: float

    def __str__(self) -> str:
        return f"mix({self.base}, {self.over}, {self.alpha:g})"


@dataclass(frozen=True, slots=True)
class Variant:
    """
    One look. ``scalars`` are ImGuiStyle attributes set as written (a pair is an
    ImVec2); ``colors`` are ``imgui.Col_`` slots by name. ``inset_tabs`` turns on
    the drawn tab shape, which no style value reaches (see ``draw_inset_tabs``).
    """

    name: str
    summary: str
    scalars: tuple[tuple[str, float | tuple[float, float]], ...] = ()
    colors: tuple[tuple[str, Mix], ...] = ()
    inset_tabs: bool = False


CURRENT = Variant("current", "today's style, unmodified: the baseline")

TIDY = Variant(
    "tidy",
    "the smallest change that answers every note: same borders, a little more of everything",
    scalars=(
        ("scrollbar_size", 14.0),
        ("scrollbar_rounding", 7.0),
        ("scrollbar_padding", 2.0),
        ("grab_rounding", 3.0),
        ("frame_rounding", 3.0),
        # Tabs are frame-height in a dock node, so this is also what makes them
        # taller: 16 + 2*5 = 26 px against 24, +8 %.
        ("frame_padding", (8.0, 5.0)),
        ("tab_rounding", 3.0),
        ("popup_rounding", 3.0),
    ),
    colors=(
        # A button gets a fill of its own; the wells keep theirs. Borders stay.
        ("button", Mix("panel_alt", "accent", 0.16)),
        ("button_hovered", Mix("panel_alt", "accent", 0.32)),
        ("button_active", Mix("panel_alt", "accent", 0.48)),
    ),
)

SOFT = Variant(
    "soft",
    "flat rounded buttons, recessed wells, larger rounded scrollbars, inset lowercase tabs",
    scalars=(
        ("scrollbar_size", 16.0),
        ("scrollbar_rounding", 8.0),
        ("scrollbar_padding", 3.0),
        ("grab_rounding", 5.0),
        ("frame_rounding", 5.0),
        # 16 + 2*6 = 28 px against 24: +17 %, one pixel over the brief's 15 % and
        # the nearest whole-pixel padding to it.
        ("frame_padding", (9.0, 6.0)),
        ("frame_border_size", 0.0),
        ("item_spacing", (8.0, 6.0)),
        ("tab_rounding", 5.0),
        ("popup_rounding", 4.0),
    ),
    colors=(
        ("button", Mix("panel_alt", "accent", 0.20)),
        ("button_hovered", Mix("panel_alt", "accent", 0.36)),
        ("button_active", Mix("panel_alt", "accent", 0.52)),
        # With no border, a well is told from a button by being darker than the
        # surface it sits on, and a button by being lighter.
        ("frame_bg", Mix("bg", "panel", 0.35)),
        ("scrollbar_grab", Mix("bg", "text_dim", 0.45)),
    ),
    inset_tabs=True,
)

VARIANTS: tuple[Variant, ...] = (CURRENT, TIDY, SOFT)
BY_NAME = {v.name: v for v in VARIANTS}

# What ``apply`` returns and ``revert`` takes: every attribute and slot it wrote,
# with the value it held before.
Saved = tuple[
    tuple[tuple[str, float | tuple[float, float]], ...],
    tuple[tuple[int, tuple[float, float, float, float]], ...],
]


def _read(value: Any) -> float | tuple[float, float]:
    if hasattr(value, "x"):
        return (float(value.x), float(value.y))
    return float(value)


def color_of(mix: Mix) -> Any:
    from pptmstr import theme

    palette = theme.current()
    return theme.mixed(getattr(palette, mix.base), getattr(palette, mix.over), mix.alpha)


def apply(variant: Variant) -> Saved:
    """
    Lay ``variant`` over the style as it stands, and return what it replaced.
    Needs an ImGui context; no frame.
    """
    from imgui_bundle import imgui

    style = imgui.get_style()
    scalars = []
    for name, value in variant.scalars:
        scalars.append((name, _read(getattr(style, name))))
        setattr(style, name, imgui.ImVec2(*value) if isinstance(value, tuple) else value)
    colors = []
    for slot, mix in variant.colors:
        idx = int(getattr(imgui.Col_, slot))
        was = style.color_(idx)
        colors.append((idx, (was.x, was.y, was.z, was.w)))
        style.set_color_(idx, color_of(mix))
    return tuple(scalars), tuple(colors)


def revert(saved: Saved) -> None:
    """Put back what ``apply`` replaced, in reverse so a repeated name ends as it began."""
    from imgui_bundle import imgui

    style = imgui.get_style()
    scalars, colors = saved
    for name, value in reversed(scalars):
        setattr(style, name, imgui.ImVec2(*value) if isinstance(value, tuple) else value)
    for idx, rgba in reversed(colors):
        style.set_color_(idx, imgui.ImVec4(*rgba))


# -- the drawings no style value reaches ------------------------------------------


def _label(window: Any) -> str:
    """What a tab shows for ``window``: its name up to an ``##``, lowercased."""
    name = str(window.name)
    return name.split("##", 1)[0].lower()


def draw_inset_tabs() -> None:
    """
    Every dock node's tab bar drawn again over itself: the lane flat, each tab a
    rounded rectangle inset into it on all four sides, its label lowercase and
    centred between equal top and bottom margins.

    ImGui gives a dock tab bar no draw hook and rounds a tab's upper corners only
    (``tab_rounding``). What it does keep is where each tab is
    (``TabBar.bar_rect``, ``TabItem.offset``/``width``), so this paints over the
    tab bar in the host window's own draw list, after every window has ended. That
    list is rendered in the host's z-order, so a modal over the docks still covers
    what is drawn here -- the foreground list would not. Input is untouched: ImGui
    hit-tests its own tabs, which are exactly under these.

    A prototype: a tab being dragged, a scrolled tab bar's arrows and the close
    button on hover are drawn over, not reproduced.
    """
    from imgui_bundle import imgui

    style = imgui.get_style()
    seen: set[int] = set()
    lane = imgui.get_color_u32(imgui.Col_.title_bg)
    font = imgui.get_font()
    size = imgui.get_font_size()
    inset = 3.0
    for window in imgui.get_current_context().windows:
        node = window.dock_node
        if node is None or int(node.id_) in seen:
            continue
        seen.add(int(node.id_))
        bar = node.tab_bar
        host = node.host_window
        # ImGui's IsVisible and IsFocused are bitfields the binding does not reach;
        # the frame stamps beside them say the same of this frame.
        frame = imgui.get_frame_count()
        if bar is None or host is None or not len(bar.tabs) or node.last_frame_active != frame:
            continue
        if node.is_hidden_tab_bar() or node.is_no_tab_bar():
            continue
        lo, hi = bar.bar_rect.min, bar.bar_rect.max
        if hi.x <= lo.x or hi.y <= lo.y:
            continue
        dl = host.draw_list
        dl.push_clip_rect(imgui.ImVec2(lo.x, lo.y), imgui.ImVec2(hi.x, hi.y), False)
        dl.add_rect_filled(imgui.ImVec2(lo.x, lo.y), imgui.ImVec2(hi.x, hi.y), lane)
        for tab in bar.tabs:
            if tab.window is None:
                continue
            x0 = lo.x + tab.offset - bar.scrolling_anim
            selected = int(tab.id_) == int(bar.selected_tab_id)
            focused = selected and node.last_frame_focused == frame
            slot = (
                imgui.Col_.tab_selected
                if focused
                else imgui.Col_.tab_dimmed_selected if selected else imgui.Col_.tab
            )
            a = imgui.ImVec2(x0 + inset * 0.5, lo.y + inset)
            b = imgui.ImVec2(x0 + tab.width - inset * 0.5, hi.y - 1.0)
            dl.add_rect_filled(a, b, imgui.get_color_u32(slot), style.tab_rounding)
            if focused:
                dl.add_rect_filled(
                    imgui.ImVec2(a.x + style.tab_rounding, b.y - 2.0),
                    imgui.ImVec2(b.x - style.tab_rounding, b.y),
                    imgui.get_color_u32(imgui.Col_.check_mark),
                )
            text = _label(tab.window)
            ty = a.y + ((b.y - a.y) - size) * 0.5
            colour = imgui.Col_.text if selected else imgui.Col_.text_disabled
            dl.add_text(
                font,
                size,
                imgui.ImVec2(x0 + style.frame_padding.x, ty),
                imgui.get_color_u32(colour),
                text,
            )
        dl.pop_clip_rect()


# The mock-up's text. Fixed, and the same in every variant.
_BLOCKS = (
    (
        "List the package and its tests  ·  timeout 2 min",
        "tree tle tests",
        (
            "tle",
            "├── __init__.py",
            "├── offsets.py",
            "└── parse.py",
            "tests",
            "├── data",
            "│   └── catalog.html",
        ),
        "3 more lines",
        False,
    ),
    (
        "Run the parser's tests  ·  timeout 10 min  ·  exit 1",
        "pytest -q tests/test_parse.py",
        (
            "FAILED tests/test_parse.py::test_checksum_counts_minus - AssertionError: 4 != 5",
            "1 failed, 37 passed in 0.41s",
        ),
        None,
        True,
    ),
)


def _caption_font() -> Any:
    """
    A proportional face for the caption, if the text library put one on the atlas;
    the UI face otherwise. ImGui's own fonts are Inconsolata only.
    """
    from imgui_bundle import imgui

    for font in imgui.get_io().fonts.fonts:
        if "inconsolata" not in font.get_debug_name().lower() and "proggy" not in (
            font.get_debug_name().lower()
        ):
            return font
    return None


def draw_terminal_mockup() -> None:
    """
    The terminal-style tool block, drawn as a floating window over the session
    pane. A mock-up of U1/U5's look: near-black ground, grey monospace, a small
    variable-width caption over the command, the output cut short with an expand
    affordance. Its rounding and padding are the variant's, as the real one's
    would be.
    """
    from imgui_bundle import imgui

    from pptmstr import theme

    session = imgui.internal.find_window_by_name("SESSION")
    if session is None:
        return
    pos, size = session.pos, session.size
    imgui.set_next_window_pos(imgui.ImVec2(pos.x + 24.0, pos.y + 70.0))
    imgui.set_next_window_size(imgui.ImVec2(min(size.x - 48.0, 760.0), 0.0))
    flags = (
        imgui.WindowFlags_.no_decoration
        | imgui.WindowFlags_.no_saved_settings
        | imgui.WindowFlags_.always_auto_resize
        | imgui.WindowFlags_.no_focus_on_appearing
    )
    imgui.begin("##style-probe-terminal", None, flags)
    imgui.text_colored(
        theme.P.warn.vec4, "MOCK-UP -- drawn by scripts/style_probe.py, not by the app"
    )
    caption = _caption_font()
    near_black = imgui.ImVec4(0.043, 0.043, 0.047, 1.0)
    grey = imgui.ImVec4(0.78, 0.78, 0.78, 1.0)
    dim = imgui.ImVec4(0.55, 0.55, 0.57, 1.0)
    style = imgui.get_style()
    for n, (said, command, output, more, failed) in enumerate(_BLOCKS):
        imgui.push_style_color(imgui.Col_.child_bg, near_black)
        imgui.push_style_var(imgui.StyleVar_.child_rounding, style.frame_rounding)
        imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(10.0, 6.0))
        imgui.begin_child(
            f"##block{n}",
            imgui.ImVec2(0.0, 0.0),
            imgui.ChildFlags_.always_use_window_padding | imgui.ChildFlags_.auto_resize_y,
        )
        if failed:
            lo = imgui.get_window_pos()
            hi = imgui.ImVec2(lo.x + 3.0, lo.y + imgui.get_window_height())
            imgui.get_window_draw_list().add_rect_filled(
                lo, hi, imgui.color_convert_float4_to_u32(theme.P.danger.vec4)
            )
        imgui.push_font(caption, imgui.get_font_size() * 0.8)
        imgui.text_colored(dim, said)
        imgui.pop_font()
        imgui.text_colored(grey, f"$ {command}")
        for line in output:
            imgui.text_colored(dim, line)
        if more is not None:
            imgui.small_button(f"expand  ({more})##more{n}")
        imgui.end_child()
        imgui.pop_style_var(2)
        imgui.pop_style_color()
    imgui.end()


# -- the run ------------------------------------------------------------------------


class _Shown:
    """What the overlay hook draws this frame, set by the scenario."""

    terminal = False


def _scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    """
    The same screens for every variant: triage, the board in triage, focus on the
    session whose transcript holds the fake report's tool calls, the terminal-block
    mock-up over it, the settings dialog, the launcher.
    """
    import screenshot
    from imgui_bundle import hello_imgui, imgui

    from pptmstr.ui.focus import OnNode

    del fixtures
    yield from screenshot._until(
        "the fake fleet",
        lambda: state.driver is not None
        and "report" in state.driver.cast
        and state.frame_snap is not None,
    )
    yield from screenshot._frames(30)
    print(f"en dash at the UI font: {imgui.calc_text_size(chr(0x2013)).x:g} px", flush=True)
    yield "triage"

    # The board is a team's: the cursor on the fake fleet's one team, its tab in front.
    yield from screenshot._until("the fake team", lambda: screenshot._team(state) is not None)
    state.focus.target = OnNode(screenshot._team(state), pinned=True)
    docking = hello_imgui.get_runner_params().docking_params
    docking.focus_dockable_window("BOARD")
    yield from screenshot._frames()
    yield "board"

    # Typeset is the mode the rework settles on (U2); with no library it is WRAP.
    state.settings = dataclasses.replace(state.settings, context_mode="typeset")
    state.pending_layout = "FOCUS"
    yield from screenshot._frames(3)
    # What a click on a card with nothing waiting does: the session itself, pinned,
    # rather than the oldest obligation it answers for, which may be a sub-agent's.
    state.focus.target = OnNode(state.driver.cast["report"], pinned=True)
    docking.focus_dockable_window("SESSION")
    yield from screenshot._frames(20)
    # The fake report's tool calls are at the top of its transcript and the pane
    # follows the bottom, so the wheel is turned over it as an operator would.
    session = screenshot._window("SESSION")
    io = imgui.get_io()
    io.add_mouse_pos_event(session.pos.x + session.size.x * 0.5, session.pos.y + 200.0)
    for _ in range(30):
        io.add_mouse_wheel_event(0.0, 20.0)
        yield None
    io.add_mouse_pos_event(-1.0, -1.0)
    yield from screenshot._frames(3)
    yield "focus"

    _Shown.terminal = True
    yield from screenshot._frames()
    yield "terminal-block"
    _Shown.terminal = False

    state.settings_dialog.request_open()
    yield from screenshot._until("settings to open", lambda: state.settings_dialog.is_open)
    yield from screenshot._frames()
    yield "settings"
    state.settings_dialog.request_close()
    yield from screenshot._until("settings to close", lambda: not state.settings_dialog.is_open)
    yield from screenshot._frames(3)

    yield from screenshot._open_launcher(state)
    yield "launcher"
    yield from screenshot._close_launcher(state)


def _run_one(variant: Variant, out_dir: Path) -> int:
    """
    One app run under ``variant`` with screenshot.py's machinery. Everything the
    variant changes is patched into this process before ``app.main`` runs.
    """
    import screenshot

    from pptmstr import app as pptmstr_app
    from pptmstr import theme

    real_apply = theme.apply_style

    @functools.wraps(real_apply)
    def apply_style() -> None:
        real_apply()
        apply(variant)

    theme.apply_style = apply_style  # type: ignore[assignment]

    real_overlays = pptmstr_app._draw_overlays

    def overlays(state: Any) -> None:
        if variant.inset_tabs:
            draw_inset_tabs()
        if _Shown.terminal:
            draw_terminal_mockup()
        real_overlays(state)

    pptmstr_app._draw_overlays = overlays  # type: ignore[assignment]
    screenshot.SCENARIOS[SCENARIO] = _scenario
    sys.argv = [
        "screenshot.py",
        "--scenario",
        SCENARIO,
        "--out-dir",
        str(out_dir),
        "--argv",
        "--fake",
    ]
    print(f"variant {variant.name}: {variant.summary}", flush=True)
    return int(screenshot.main())


def _run_all(names: list[str]) -> int:
    """Each variant on its own Xvfb, one after another; the first failure ends it."""
    for name in names:
        out_dir = OUT / name
        started = time.monotonic()
        done = subprocess.run(
            [
                str(ROOT / "scripts" / "xvfb_shot.sh"),
                "--",
                str(ROOT / ".venv" / "bin" / "python"),
                str(Path(__file__).resolve()),
                "--variant",
                name,
                "--out-dir",
                str(out_dir),
            ],
            cwd=ROOT,
        )
        print(f"{name}: exit {done.returncode} in {time.monotonic() - started:.0f}s", flush=True)
        if done.returncode != 0:
            return done.returncode
        missing = [s for s in SCREENS if not (out_dir / f"{s}.png").is_file()]
        if missing:
            print(f"{name}: no image for {', '.join(missing)}", file=sys.stderr)
            return 1
    return 0


def describe(variant: Variant) -> list[str]:
    """The variant's exact values, one per line, as the planning record quotes them."""
    lines = [f"{name} = {value}" for name, value in variant.scalars]
    lines += [f"Col_.{slot} = {mix}" for slot, mix in variant.colors]
    if variant.inset_tabs:
        lines.append("inset tabs drawn by draw_inset_tabs (lowercase, four rounded corners)")
    return lines or ["(no overrides)"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--variant", choices=sorted(BY_NAME))
    which.add_argument("--all", action="store_true", help="every variant, each on its own Xvfb")
    which.add_argument("--describe", action="store_true", help="print every variant's values")
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args()
    if args.describe:
        for variant in VARIANTS:
            print(f"{variant.name}: {variant.summary}")
            for line in describe(variant):
                print(f"    {line}")
        return 0
    if args.all:
        return _run_all([v.name for v in VARIANTS])
    return _run_one(BY_NAME[args.variant], args.out_dir or OUT / args.variant)


if __name__ == "__main__":
    raise SystemExit(main())
