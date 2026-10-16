#!/usr/bin/env python3
"""
Measure the session pane's side margins and the launcher's placement from rendered
frames, for notes/2026-10-16-pane-margins-and-launcher-placement.md.

    scripts/xvfb_shot.sh -- .venv/bin/python scripts/probe_pane_layout.py 1500x900 1920x1080
    .venv/bin/python scripts/probe_pane_layout.py --analyse 1500x900 1920x1080
    XVFB_SHOT_SCREEN=2000x1200x24 has to be set for a size past the default screen.

The first size is the one each run starts in: the window is resized to it before
anything is measured, and to each later one in turn. At each size, on the --fake
fleet in FOCUS:

* the pane: the "report" session, whose log overflows (scrollbar shown), with and
  without the find row, then the first root session whose log does not overflow
  (scrollbar hidden). Every window under SESSION is recorded (its rect, inner rect,
  work rect, scrollbar), with the horizontal extent of what its draw list holds: the
  glyph quads and the shapes, apart by whether a vertex samples the atlas's white
  pixel. After the run each frame is read back and scanned for the first and last
  pixel that differs from the pane's ground, row band by row band: the typeset text,
  each tool block, the controls/find row and the composer.
* the launcher: opened fresh, its rect against the viewport and its work area every
  frame for a second, then the window is resized under it and it is watched again.

Everything is written to shots/layout-probe/ (the PNGs and measurements.json), and the
table the note quotes is printed. Nothing in pptmstr/ is changed: the hooks read.
"""

from __future__ import annotations

import ctypes
import json
import struct
import sys
import zlib
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

OUT = ROOT / "shots" / "layout-probe"

_VERT = struct.Struct("<ffffI")  # ImDrawVert: pos, uv, col


def _sizes(argv: list[str]) -> list[tuple[int, int]]:
    sizes = []
    for word in argv or ["1500x900", "1920x1080"]:
        w, h = word.lower().split("x")
        sizes.append((int(w), int(h)))
    return sizes


def _rect(r: Any) -> list[float]:
    return [round(r.min.x, 1), round(r.min.y, 1), round(r.max.x, 1), round(r.max.y, 1)]


def _verts(window: Any) -> dict[str, Any]:
    """
    The x extent of the window's own draw list inside its clip rect: glyph quads
    (``uv`` off the atlas's white pixel) and shapes apart. A vertex with zero alpha
    is not ink. AA fringes put a shape's extent up to a pixel past its edge.
    """
    from imgui_bundle import imgui

    dl = window.draw_list
    n = dl.vtx_buffer.size()
    if n == 0:
        return {"glyph": None, "shape": None, "count": 0}
    raw = (ctypes.c_char * (n * _VERT.size)).from_address(dl.vtx_buffer.data_address())
    white = imgui.get_io().fonts.tex_uv_white_pixel
    clip = window.outer_rect_clipped
    out: dict[str, list[float] | None] = {"glyph": None, "shape": None}
    for x, y, u, v, col in _VERT.iter_unpack(bytes(raw)):
        if col >> 24 == 0 or not (clip.min.y <= y <= clip.max.y):
            continue
        x = min(max(x, clip.min.x), clip.max.x)
        kind = "shape" if abs(u - white.x) < 1e-6 and abs(v - white.y) < 1e-6 else "glyph"
        got = out[kind]
        out[kind] = [x, x] if got is None else [min(got[0], x), max(got[1], x)]
    return {
        k: (None if e is None else [round(e[0], 1), round(e[1], 1)]) for k, e in out.items()
    } | {"count": n}


def _tree(root_prefix: str) -> list[dict[str, Any]]:
    """Every window drawn this frame whose root is the one named ``root_prefix``."""
    from imgui_bundle import imgui

    found = []
    for w in imgui.get_current_context().windows:
        if not w.name.startswith(root_prefix):
            continue
        parent = w.parent_window
        found.append(
            {
                "name": w.name,
                "active": bool(w.active),
                "was_active": bool(w.was_active),
                "hidden": bool(w.hidden),
                "parent": None if parent is None else parent.name,
                "rect": [
                    round(w.pos.x, 1),
                    round(w.pos.y, 1),
                    round(w.pos.x + w.size.x, 1),
                    round(w.pos.y + w.size.y, 1),
                ],
                "inner": _rect(w.inner_rect),
                "work": _rect(w.work_rect),
                "content_region": _rect(w.content_region_rect),
                "clip": _rect(w.outer_rect_clipped),
                "padding": [w.window_padding.x, w.window_padding.y],
                "scrollbar_y": bool(w.scrollbar_y),
                "scroll_max": [round(w.scroll_max.x, 1), round(w.scroll_max.y, 1)],
                "draw": _verts(w),
            }
        )
    return found


def _style() -> dict[str, Any]:
    from imgui_bundle import imgui

    from pptmstr.ui import tool_block, transcript_pane, typeset

    s = imgui.get_style()
    body = typeset.body_px()
    return {
        "font_px": imgui.get_font_size(),
        "body_px": body,
        "window_padding": [s.window_padding.x, s.window_padding.y],
        "scrollbar_size": s.scrollbar_size,
        "item_spacing": [s.item_spacing.x, s.item_spacing.y],
        "frame_padding": [s.frame_padding.x, s.frame_padding.y],
        "margin_px(body)": transcript_pane.margin_px(body),
        "log_sides(body, gutter)": list(transcript_pane.log_sides(body, s.scrollbar_size)),
        "tool_block._PADDING": list(tool_block._PADDING),
    }


def _launcher_rect(state: Any) -> dict[str, Any] | None:
    from imgui_bundle import imgui

    from pptmstr.ui import launcher

    w = imgui.internal.find_window_by_name(launcher.TITLE)
    vp = imgui.get_main_viewport()
    if w is None:
        return None
    top, height = w.pos.y, w.size.y
    body = imgui.internal.find_window_by_name(f"{launcher.TITLE}/{launcher.BODY_ID}")
    if body is None:
        body = next(
            (
                x
                for x in imgui.get_current_context().windows
                if x.name.startswith(f"{launcher.TITLE}/{launcher.BODY_ID}")
            ),
            None,
        )
    work_top, work_h = vp.work_pos.y, vp.work_size.y
    return {
        "frame": imgui.get_frame_count(),
        "open": state.launcher.is_open,
        "active": bool(w.active),
        "appearing": bool(w.appearing),
        "rect": [
            round(w.pos.x, 1),
            round(top, 1),
            round(w.pos.x + w.size.x, 1),
            round(top + height, 1),
        ],
        "size": [round(w.size.x, 1), round(height, 1)],
        "body_size": None if body is None else [round(body.size.x, 1), round(body.size.y, 1)],
        "viewport": [vp.pos.x, vp.pos.y, vp.size.x, vp.size.y],
        "work": [vp.work_pos.x, work_top, vp.work_size.x, work_h],
        "gap_above_work": round(top - work_top, 1),
        "gap_below_work": round(work_top + work_h - (top + height), 1),
        "centre_y": round(top + height / 2, 1),
        "viewport_centre_y": vp.pos.y + vp.size.y / 2,
        "work_centre_y": work_top + work_h / 2,
    }


RECORD: dict[str, Any] = {"pane": [], "launcher": []}


def scenario(state: Any, fixtures: Path, sizes: list[tuple[int, int]]) -> Iterator[str | None]:
    import screenshot
    from imgui_bundle import hello_imgui, imgui

    from pptmstr.ui.focus import OnNode

    yield from screenshot._until(
        "the fake fleet",
        lambda: state.driver is not None
        and "report" in state.driver.cast
        and state.frame_snap is not None,
    )
    yield from screenshot._frames(30)
    state.pending_layout = "FOCUS"
    yield from screenshot._frames(3)
    hello_imgui.get_runner_params().docking_params.focus_dockable_window("SESSION")

    def pane(tag: str) -> str:
        io = imgui.get_io()
        RECORD["pane"].append(
            {
                "tag": tag,
                "display": [io.display_size.x, io.display_size.y],
                "style": _style(),
                "find_open": state.transcripts.find.open,
                # Whether the log's scrollbar is drawn in ink this frame (_begin_log).
                "overflowing": [bool(v) for v in state.transcripts.overflowing.values()],
                "windows": _tree("SESSION"),
            }
        )
        return tag

    for w, h in sizes:
        size = f"{w}x{h}"
        hello_imgui.change_window_size((w, h))
        yield from screenshot._frames(20)
        state.pending_layout = "FOCUS"
        yield from screenshot._frames(3)
        hello_imgui.get_runner_params().docking_params.focus_dockable_window("SESSION")
        state.focus.target = OnNode(state.driver.cast["report"], pinned=True)
        yield from screenshot._frames(40)
        yield pane(f"{size}-report-bar")
        state.transcripts.find.show()
        state.transcripts.find.needle = "checksum"
        yield from screenshot._frames(10)
        yield pane(f"{size}-report-bar-find")
        state.transcripts.find.open = False
        yield from screenshot._frames(5)
        # Every root surveyed; the one whose log does not overflow (the scrollbar's
        # ink hidden) and has the most tool blocks drawn is measured.
        survey = []
        for name, node in sorted(state.driver.cast.items()):
            if node[1] is not None:
                continue
            state.focus.target = OnNode(node, pinned=True)
            yield from screenshot._frames(20)
            wins = [x for x in _tree("SESSION") if x["active"] and not x["hidden"]]
            log = next((x for x in wins if _leaf(x) == "##transcript"), None)
            if log is None:
                continue
            tools = sum(1 for x in wins if _leaf(x) == "##tool" and x["parent"] == log["name"])
            survey.append((name, log["scroll_max"][1], tools))
        RECORD.setdefault("survey", {})[size] = survey
        short = sorted((s for s in survey if s[1] == 0.0), key=lambda s: -s[2])
        if short:
            name = short[0][0]
            state.focus.target = OnNode(state.driver.cast[name], pinned=True)
            yield from screenshot._frames(20)
            yield pane(f"{size}-{name}-nobar")
            state.transcripts.find.show()
            yield from screenshot._frames(10)
            yield pane(f"{size}-{name}-nobar-find")
            state.transcripts.find.open = False
            yield from screenshot._frames(5)

        # The launcher, opened fresh at this size, watched for a second.
        state.launcher.request_open()
        frames: list[dict[str, Any]] = []
        for _ in range(60):
            yield None
            got = _launcher_rect(state)
            if got is not None and got["open"]:
                frames.append(got)
        RECORD["launcher"].append({"case": f"opened at {size}", "frames": frames})
        yield f"{size}-launcher"
        # The window resized under it, to the next size (or the first).
        others = [s for s in sizes if s != (w, h)] or [(1280, 800)]
        ow, oh = others[0]
        hello_imgui.change_window_size((ow, oh))
        frames = []
        for _ in range(60):
            yield None
            got = _launcher_rect(state)
            if got is not None:
                frames.append(got)
        RECORD["launcher"].append(
            {"case": f"opened at {size}, resized to {ow}x{oh}", "frames": frames}
        )
        yield f"{size}-launcher-resized-to-{ow}x{oh}"
        yield from screenshot._close_launcher(state)
        hello_imgui.change_window_size((w, h))
        yield from screenshot._frames(10)


# -- reading the frames back ----------------------------------------------------


def _read_png(path: Path) -> tuple[int, int, list[bytes]]:
    """Read back what ``screenshot.write_png`` wrote: RGB, filter 0 on every row."""
    data = path.read_bytes()
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = w * 3 + 1
    rows = [raw[y * stride + 1 : (y + 1) * stride] for y in range(h)]
    return w, h, rows


def _ink(
    rows: list[bytes],
    y0: float,
    y1: float,
    x0: float,
    x1: float,
    skip: Sequence[tuple[float, float]] = (),
) -> list[int] | None:
    """
    First and last column in [x0, x1) holding a pixel off the ground, over rows
    [y0, y1). The ground is each row's own pixel at x0 + 2, inside the pane's padding.
    A row that is ink across nearly all of the span (a separator) is passed over.
    Columns inside a ``skip`` span (the scrollbar) are not ink.
    """
    a, b = int(x0), int(x1)
    spans: list[list[int]] = []
    for y in range(max(0, int(y0)), int(y1)):
        row = rows[y]
        ground = row[(a + 2) * 3 : (a + 2) * 3 + 3]
        cols = [
            x
            for x in range(a, b)
            if sum(abs(row[x * 3 + i] - ground[i]) for i in range(3)) > 24
            and not any(s0 <= x < s1 for s0, s1 in skip)
        ]
        spans.append(cols)
    lo: int | None = None
    hi: int | None = None

    def full(c: list[int]) -> bool:
        return len(c) > 0.95 * (b - a)

    for i, cols in enumerate(spans):
        if not cols:
            continue
        # A one-pixel rule across the span (a separator) is not an item's ink; a
        # frame (the composer's box) is as wide but taller.
        if (
            full(cols)
            and not (i > 0 and full(spans[i - 1]))
            and not (i + 1 < len(spans) and full(spans[i + 1]))
        ):
            continue
        lo = cols[0] if lo is None else min(lo, cols[0])
        hi = cols[-1] if hi is None else max(hi, cols[-1])
    return None if lo is None or hi is None else [lo, hi]


def _leaf(entry: dict[str, Any]) -> str:
    return str(entry["name"]).rsplit("/", 1)[-1].split("_")[0]


def analyse() -> list[dict[str, Any]]:
    table = []
    for shot in RECORD["pane"]:
        png = OUT / f"{shot['tag']}.png"
        if not png.exists():
            continue
        _, _, rows = _read_png(png)
        wins = [x for x in shot["windows"] if x["active"] and not x["hidden"]]
        session = next(x for x in wins if x["name"] == "SESSION")
        scroll = next(x for x in wins if _leaf(x) == "##scrollback")
        log = next(x for x in wins if _leaf(x) == "##transcript")
        blocks = [x for x in wins if _leaf(x) == "##tool" and x["parent"] == log["name"]]
        px0, _, px1, py1 = session["rect"]
        sx0, sy0, sx1, sy1 = scroll["rect"]
        lx0, ly0, lx1, ly1 = log["rect"][0], log["clip"][1], log["rect"][2], log["clip"][3]
        # The gutter is always reserved (always_vertical_scrollbar); whether the bar
        # in it is drawn in ink is ``overflowing``.
        bar = (log["inner"][2], log["rect"][2])
        skip = [bar]
        edges = {"pane": (px0, px1), "scrollback": (sx0, sx1), "log_child": (lx0, lx1)}

        def gaps(ink: list[int] | None, edges: Any = edges, bar: Any = bar) -> Any:
            if ink is None:
                return None
            first, last = ink
            out: dict[str, Any] = {"ink": [first, last]}
            for edge, (e0, e1) in edges.items():
                out[f"from_{edge}"] = [first - e0, e1 - 1 - last]
            out["to_gutter"] = round(bar[0] - 1 - last, 1)
            return out

        # Typeset text: the log's rows no block covers.
        covered = sorted((max(b["clip"][1], ly0), min(b["clip"][3], ly1)) for b in blocks)
        text_bands, at = [], ly0
        for b0, b1 in covered:
            if b0 > at:
                text_bands.append((at, b0 - 2))  # item spacing above a block
            at = max(at, b1 + 2)
        if at < ly1:
            text_bands.append((at, ly1))
        text_ink = None
        for b0, b1 in text_bands:
            got = _ink(rows, b0, b1, px0, px1 - 1, skip)
            if got:
                text_ink = (
                    got
                    if text_ink is None
                    else [min(text_ink[0], got[0]), max(text_ink[1], got[1])]
                )
        block_ink = None
        for b in blocks:
            got = _ink(rows, max(b["clip"][1], ly0), min(b["clip"][3], ly1), px0, px1 - 1, skip)
            if got:
                block_ink = (
                    got
                    if block_ink is None
                    else [min(block_ink[0], got[0]), max(block_ink[1], got[1])]
                )
        controls = _ink(rows, sy0, ly0, px0, px1 - 1)
        composer = _ink(rows, sy1 + 1, py1 - 1, px0, px1 - 1)
        table.append(
            {
                "tag": shot["tag"],
                "style": shot["style"],
                "pane": session["rect"],
                "scrollback": scroll["rect"],
                "log_child": log["rect"],
                "log_inner": log["inner"],
                "log_work": log["work"],
                "log_scrollbar": log["scrollbar_y"],
                "overflowing": shot.get("overflowing"),
                "log_scroll_max": log["scroll_max"],
                "log_draw": log["draw"],
                "blocks_rect_x": sorted({(b["rect"][0], b["rect"][2]) for b in blocks}),
                "text": gaps(text_ink),
                "tool_block": gaps(block_ink),
                "controls_row": gaps(controls),
                "composer": gaps(composer),
                "composer_windows": [
                    {"name": x["name"], "rect": x["rect"], "draw": x["draw"]}
                    for x in wins
                    if x["parent"] == session["name"] and _leaf(x) != "##scrollback"
                ],
                "session_draw": session["draw"],
            }
        )
    return table


def main() -> int:
    import screenshot

    args = sys.argv[1:]
    offline = bool(args) and args[0] == "--analyse"
    sizes = _sizes(args[1:] if offline else args)
    tag = "-".join(f"{w}x{h}" for w, h in sizes)
    raw_path = OUT / f"raw-{tag}.json"
    OUT.mkdir(parents=True, exist_ok=True)
    if offline:
        # Again from a run's record and its PNGs, without a display.
        RECORD.update(json.loads(raw_path.read_text()))
        code = 0
    else:
        screenshot.SCENARIOS["layout-probe"] = lambda state, fixtures: scenario(
            state, fixtures, sizes
        )
        sys.argv = [
            "screenshot.py",
            "--scenario",
            "layout-probe",
            "--out-dir",
            str(OUT),
            "--argv",
            "--fake",
        ]
        code = screenshot.main()
        raw_path.write_text(json.dumps(RECORD, indent=1))
    table = analyse()
    (OUT / f"measurements-{tag}.json").write_text(
        json.dumps({"pane": table, "launcher": RECORD["launcher"]}, indent=1)
    )
    for row in table:
        print(
            f"PANE {row['tag']}: bar ink={row['overflowing']}"
            f" sides={row['style']['log_sides(body, gutter)']}"
        )
        for part in ("text", "tool_block", "controls_row", "composer"):
            print(f"  {part:13} {row[part]}")
    print("SURVEY (root, log scroll_max_y, tool blocks drawn):", RECORD.get("survey"))
    for case in RECORD["launcher"]:
        fr = case["frames"]
        if not fr:
            print(f"LAUNCHER {case['case']}: no frames")
            continue
        sizes_seen = sorted({tuple(f["size"]) for f in fr})
        print(f"LAUNCHER {case['case']}: sizes seen {sizes_seen}")
        prev = None
        for f in fr:
            key = (tuple(f["rect"]), f["appearing"], tuple(f.get("body_size") or ()))
            if key != prev:
                print(
                    f"  frame {f['frame']}: rect {f['rect']} body {f.get('body_size')}"
                    f" appearing={f['appearing']} work {f['work']}"
                    f" above {f['gap_above_work']} below {f['gap_below_work']}"
                    f" centre_y {f['centre_y']} (viewport {f['viewport_centre_y']},"
                    f" work {f['work_centre_y']})"
                )
            prev = key
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
