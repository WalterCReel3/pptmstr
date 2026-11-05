#!/usr/bin/env python3
"""
What imgui-bundle 1.93.0's rich_md Mermaid and Dear ImGui 1.93's stroking draw,
measured beside 1.92.900. For notes/vector-rendering/probe-bundle193.md.

Runs only from a scratch venv, never this checkout's .venv: one venv per
bundle version, each with imgui-bundle and numpy and nothing else.

    PY=$TMPDIR/b193/bin/python
    $PY scripts/bundle193_probe.py all --out OUTDIR --scan pptmstr ~/Source/imscribe/imscribe
    xvfb-run -a -s "-screen 0 1600x1200x24" $PY scripts/bundle193_probe.py shots --out OUTDIR

Modes. Each prints one JSON object per measurement on stdout, prefixed "@@ ",
and writes PNGs under OUTDIR.

  info     versions; every ImDrawFlags_ member; style fields named in the 1.93
           notes; rich_md's module surface and render_mermaid's docstring.
  api      every `imgui.x`, `imgui.x.y`, `hello_imgui.x`, `immapp.x` name the
           --scan trees use, resolved against the installed bundle: the names
           that no longer exist. Renames only; not signatures or return types.
  strokes  headless: vertex and index counts, vertex bounding boxes and spike
           length for polylines, lines and rects at several thicknesses under
           every stroke flag the build has. No GPU.
  mermaid  (needs a display) one hello_imgui process per case, run as a child
           of this one so a crash is a result and not the end of the probe:
           the five diagrams, the 21-node board graph, theme, font size,
           a fixed-size child, hover, streaming, and hostile input.
  shots    (needs a display) the stroke panel and each diagram as PNGs.
  all      info, api and strokes (the headless modes).
"""

from __future__ import annotations

import argparse
import collections
import ctypes
import json
import math
import os
import re
import resource
import statistics
import struct
import subprocess
import sys
import time
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from imgui_bundle import imgui

OUT = Path(".")


def emit(kind: str, **fields: Any) -> None:
    """
    One measurement as a JSON line, flushed so a crash keeps what came before.
    """
    print("@@ " + json.dumps({"kind": kind, **fields}, default=str), flush=True)


def bundle_version() -> str:
    from importlib.metadata import version

    return version("imgui-bundle")


# --------------------------------------------------------------------- diagrams

# The five in scripts/figure_render_probe.py (DIAGRAMS), copied so this script
# runs from a venv that cannot import the checkout.
DIAGRAMS: dict[str, str] = {
    "flowchart": """flowchart TD
    A[User submits form] --> B{Valid input?}
    B -->|Yes| C[Save to database]
    B -->|No| D[Show validation errors]
    D --> A
    C --> E[(PostgreSQL)]
    C --> F[Send confirmation email]
    subgraph Background["Background jobs"]
        F --> G[Queue: email-worker]
        G --> H[SMTP relay]
    end
    H --> I([Done])
""",
    "sequence": """sequenceDiagram
    autonumber
    participant C as Client
    participant G as API Gateway
    participant A as Auth Service
    participant D as Database
    C->>G: POST /login {user, password}
    G->>A: validate(credentials)
    A->>D: SELECT * FROM users WHERE name = ?
    D-->>A: user row
    alt password matches
        A-->>G: 200 + JWT
        G-->>C: Set-Cookie: session=...
    else mismatch
        A-->>G: 401 Unauthorized
        G-->>C: 401
    end
    Note over C,G: The token expires after 15 minutes
""",
    "class": """classDiagram
    class Animal {
        <<abstract>>
        +String name
        +int age
        +makeSound()* void
    }
    class Dog {
        +String breed
        +fetch(item: Ball) bool
    }
    class Cat {
        -int lives
        +purr() void
    }
    class Owner {
        +List~Animal~ pets
        +adopt(a: Animal)
    }
    Animal <|-- Dog
    Animal <|-- Cat
    Owner "1" o-- "*" Animal : owns
""",
    "state": """stateDiagram-v2
    [*] --> Idle
    Idle --> Running : start()
    Running --> Paused : pause()
    Paused --> Running : resume()
    Running --> Failed : error
    Failed --> Idle : reset()
    Running --> [*] : complete
    state Running {
        [*] --> Fetching
        Fetching --> Processing
        Processing --> Fetching : more data
    }
    note right of Failed : Retries are exhausted
""",
    "er": """erDiagram
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--|{ LINE_ITEM : contains
    PRODUCT ||--o{ LINE_ITEM : "ordered in"
    CUSTOMER {
        int id PK
        string name
        string email UK
    }
    ORDER {
        int id PK
        int customer_id FK
        date created_at
    }
    LINE_ITEM {
        int order_id FK
        int product_id FK
        int quantity
    }
    PRODUCT {
        int id PK
        string sku
        decimal price
    }
""",
}

# The board operator session 7b792e36 drew by hand (board 399eb899,
# feat-graph-tooling): 21 tasks and 42 depends_on edges, as its call at
# transcript line 101 printed them. Edges run dependency --> dependent.
BOARD_DEPS: dict[str, list[str]] = {
    "withdraw-task": [],
    "graph-core": [],
    "all-writers": ["withdraw-task"],
    "bus-graph-tools": ["graph-core", "all-writers", "withdraw-task"],
    "review-withdraw": ["withdraw-task"],
    "review-graph-core": ["graph-core"],
    "review-all-writers": ["all-writers"],
    "review-bus-tools": ["bus-graph-tools"],
    "record": [
        "bus-graph-tools",
        "review-withdraw",
        "review-graph-core",
        "review-all-writers",
        "review-bus-tools",
    ],
    "graph-gate": [
        "withdraw-task",
        "graph-core",
        "all-writers",
        "bus-graph-tools",
        "record",
        "review-withdraw",
        "review-graph-core",
        "review-all-writers",
        "review-bus-tools",
    ],
    "digest-withdrawn": ["withdraw-task"],
    "graph-core-fix": ["review-graph-core", "bus-graph-tools"],
    "graph-core-fix-2": ["review-graph-core", "graph-core-fix"],
    "withdraw-notice": ["withdraw-task", "all-writers"],
    "withdraw-fixes": ["withdraw-notice", "bus-graph-tools", "all-writers"],
    "all-writers-fix": [
        "review-all-writers",
        "bus-graph-tools",
        "withdraw-notice",
        "withdraw-fixes",
    ],
    "review-fixes": ["withdraw-fixes", "all-writers-fix"],
    "fix-tail": ["all-writers-fix"],
    "escaped-dep-wording": ["fix-tail"],
    "final-fixes": ["review-fixes"],
    "record-fix": ["record"],
}


def board_graph() -> str:
    """
    The 21-node board as a Mermaid flowchart, ids made Mermaid-safe.
    """
    nid = {t: f"t{i}" for i, t in enumerate(BOARD_DEPS)}
    lines = ["flowchart TD"]
    lines += [f'    {nid[t]}["{t}"]' for t in BOARD_DEPS]
    lines += [f"    {nid[d]} --> {nid[t]}" for t, deps in BOARD_DEPS.items() for d in deps]
    return "\n".join(lines) + "\n"


DIAGRAMS["board21"] = board_graph()


def chain(n: int) -> str:
    return "flowchart TD\n" + "".join(f"    n{i} --> n{i + 1}\n" for i in range(n))


def fan(n: int) -> str:
    return "flowchart TD\n" + "".join(f"    root --> n{i}\n" for i in range(n))


def dense(n: int, e: int) -> str:
    """
    n nodes, e pseudo-random forward edges (a DAG, so no back-edge reversal),
    deterministic.
    """
    out, x = ["flowchart TD"], 12345
    for _ in range(e):
        x = (1103515245 * x + 12345) % (1 << 31)
        a = x % n
        x = (1103515245 * x + 12345) % (1 << 31)
        b = x % n
        if a != b:
            out.append(f"    n{min(a, b)} --> n{max(a, b)}")
    return "\n".join(out) + "\n"


def nested(depth: int) -> str:
    body = ["flowchart TD"]
    body += [f"{'  ' * i}subgraph s{i}" for i in range(depth)]
    body += ["    a --> b"]
    body += ["end"] * depth
    return "\n".join(body) + "\n"


def seq_long(n: int) -> str:
    return "sequenceDiagram\n" + "".join(
        f"    A->>B: message {i}\n" if i % 2 == 0 else f"    B-->>A: reply {i}\n" for i in range(n)
    )


def seq_frames(depth: int) -> str:
    return (
        "sequenceDiagram\n"
        + "".join(f"{'  ' * i}loop L{i}\n" for i in range(depth))
        + "    A->>B: hi\n"
        + "end\n" * depth
    )


def class_ns(depth: int) -> str:
    return "classDiagram\n" + f"    class {'.'.join(f'N{i}' for i in range(depth))}.Leaf\n"


HOSTILE: dict[str, Callable[[], str]] = {
    "chain-500": lambda: chain(500),
    "chain-5000": lambda: chain(5000),
    "chain-50000": lambda: chain(50000),
    "fan-2000": lambda: fan(2000),
    "dense-60x300": lambda: dense(60, 300),
    "dense-150x1200": lambda: dense(150, 1200),
    "nested-50": lambda: nested(50),
    "nested-2000": lambda: nested(2000),
    "seq-5000": lambda: seq_long(5000),
    "seq-frames-2000": lambda: seq_frames(2000),
    "class-ns-2000": lambda: class_ns(2000),
    "label-1mb": lambda: "flowchart TD\n    A[" + "x" * 1_000_000 + "] --> B\n",
    "malformed-unclosed": lambda: "flowchart TD\n    A[open --> B{\n    subgraph X\n",
    "malformed-binary": lambda: "flowchart TD\n    "
    + bytes(range(1, 256)).decode("latin-1")
    + "\n",
    "empty": lambda: "",
    "header-only": lambda: "flowchart TD\n",
    "unsupported-pie": lambda: 'pie title Pets\n    "Dogs" : 386\n    "Cats" : 85\n',
}


# ------------------------------------------------------------------- draw lists


def make_context() -> None:
    """
    A headless context with no renderer: enough for NewFrame and geometry.
    """
    imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(2000.0, 2000.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.backend_flags |= imgui.BackendFlags_.renderer_has_vtx_offset


Vert = tuple[float, float, float, float, int, int]  # x, y, u, v, rgb, alpha


def vertices(dl: imgui.ImDrawList, start: int = 0) -> list[Vert]:
    n = dl.vtx_buffer.size()
    if n <= start:
        return []
    size = imgui.VERTEX_SIZE
    raw = (ctypes.c_ubyte * (n * size)).from_address(dl.vtx_buffer.data_address())
    pos, uv, col = (
        imgui.VERTEX_BUFFER_POS_OFFSET,
        imgui.VERTEX_BUFFER_UV_OFFSET,
        imgui.VERTEX_BUFFER_COL_OFFSET,
    )
    out = []
    for i in range(start, n):
        b = i * size
        x, y = struct.unpack_from("ff", raw, b + pos)
        u, v = struct.unpack_from("ff", raw, b + uv)
        (c,) = struct.unpack_from("I", raw, b + col)
        out.append((x, y, u, v, c & 0xFFFFFF, c >> 24))
    return out


def flag(name: str) -> int | None:
    """
    An ImDrawFlags_ member by its Python name, or None where this build lacks it.
    """
    member = getattr(imgui.ImDrawFlags_, name, None)
    return None if member is None else int(member)


def flag_names() -> list[str]:
    return sorted(imgui.ImDrawFlags_.__members__)


# ------------------------------------------------------------------------- info


def mode_info() -> None:
    emit("version", imgui_bundle=bundle_version(), imgui=imgui.get_version(), python=sys.version)
    emit("draw_flags", names=flag_names())
    emit(
        "draw_list_flags",
        present=hasattr(imgui, "ImDrawListFlags_"),
        names=(
            sorted(imgui.ImDrawListFlags_.__members__) if hasattr(imgui, "ImDrawListFlags_") else []
        ),
    )
    style_fields = [
        "anti_aliased_lines",
        "anti_aliased_lines_use_tex",
        "anti_aliased_line_ends",
        "anti_aliased_fill",
        "curve_tessellation_tol",
        "curve_tessellation_max_error",
        "circle_tessellation_max_error",
    ]
    make_context()
    style = imgui.get_style()
    emit("style_fields", **{f: getattr(style, f, "ABSENT") for f in style_fields})
    dl_methods = [
        m
        for m in dir(imgui.ImDrawList)
        if re.search(r"dash|stroke|push_draw_flag|pop_draw_flag|line_h|line_v", m)
    ]
    emit("draw_list_methods_of_interest", names=sorted(dl_methods))
    emit(
        "dash_anywhere",
        names=sorted(n for n in dir(imgui) if "dash" in n.lower())
        + sorted(n for n in dir(imgui.ImDrawList) if "dash" in n.lower()),
    )
    imgui.destroy_context()
    try:
        from imgui_bundle import rich_md  # type: ignore[attr-defined]
    except ImportError as e:
        emit("rich_md", present=False, error=str(e))
        return
    names = sorted(n for n in dir(rich_md) if not n.startswith("_"))
    emit(
        "rich_md",
        present=True,
        names=names,
        render_mermaid_doc=getattr(rich_md.render_mermaid, "__doc__", None),
        has_latex=_call(rich_md, "has_latex"),
        has_url_images=_call(rich_md, "has_url_images"),
        has_code_editor=_call(rich_md, "has_code_editor"),
    )
    try:
        from imgui_bundle import imgui_md  # type: ignore[attr-defined]

        emit("imgui_md_alias", same_module=imgui_md is rich_md, module=str(imgui_md))
    except ImportError as e:
        emit("imgui_md_alias", present=False, error=str(e))


def _call(mod: Any, name: str) -> Any:
    fn = getattr(mod, name, None)
    try:
        return fn() if fn else "ABSENT"
    except Exception as e:  # a capability query that throws is itself a finding
        return f"raised {e!r}"


# -------------------------------------------------------------------------- api

NAME_RE = re.compile(
    r"\b(imgui|im|hello_imgui|immapp|imgui_md)\.([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*))?"
)


def mode_api(roots: list[str]) -> None:
    import importlib

    mods: dict[str, Any] = {}
    for name in ("imgui", "hello_imgui", "immapp", "imgui_md"):
        try:
            mods[name] = importlib.import_module(f"imgui_bundle.{name}")
        except ImportError:
            mods[name] = None
    mods["im"] = mods["imgui"]
    used: dict[tuple[str, str, str | None], set[str]] = collections.defaultdict(set)
    for root in roots:
        for path in Path(os.path.expanduser(root)).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in NAME_RE.finditer(text):
                used[(m.group(1), m.group(2), m.group(3))].add(str(path))
    missing = []
    checked = 0
    for (mod, a, b), where in sorted(
        used.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")
    ):
        top = mods.get(mod)
        if top is None:
            missing.append({"name": f"{mod} (module)", "files": sorted(where)[:3]})
            continue
        checked += 1
        obj = getattr(top, a, None)
        if obj is None:
            missing.append({"name": f"{mod}.{a}", "files": sorted(where)[:3]})
            continue
        # A second component only counts when the first is a namespace (class,
        # enum, submodule); on a function it is a call result, not a name.
        if b and (isinstance(obj, type) or type(obj).__name__ == "module"):
            if not hasattr(obj, b):
                missing.append({"name": f"{mod}.{a}.{b}", "files": sorted(where)[:3]})
    emit("api", roots=roots, distinct_names=len(used), checked=checked, missing=missing)


# ---------------------------------------------------------------------- strokes

# Zigzag with 150, 90 and 30 degree interior angles, then a hairpin.
ZIGZAG = [
    (20.0, 100.0),
    (120.0, 73.2),
    (220.0, 100.0),
    (320.0, 0.0),
    (420.0, 100.0),
    (447.0, 0.0),
    (460.0, 100.0),
]


def poly_from_path(
    points: list[tuple[float, float]], origin: tuple[float, float]
) -> list[imgui.ImVec2]:
    ox, oy = origin
    return [imgui.ImVec2(ox + x, oy + y) for x, y in points]


def seg_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1.0)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def stroke_configs() -> list[tuple[str, int]]:
    """
    Every stroke-relevant flag this build has, alone, plus a few pairs. Missing
    ones are reported, not silently dropped.
    """
    single = [
        "none",
        "join_miter",
        "cap_square",
        "aa_line_ends",
        "stroke_inside",
        "stroke_center",
        "stroke_center_biased",
        "stroke_outside",
        "stroke_legacy",
    ]
    out: list[tuple[str, int]] = []
    for n in single:
        v = 0 if n == "none" else flag(n)
        if v is None:
            emit("stroke_flag_absent", flag=n)
        else:
            out.append((n, v))
    pairs = [
        ("join_miter", "cap_square"),
        ("cap_square", "aa_line_ends"),
        ("join_miter", "aa_line_ends"),
    ]
    for a, b in pairs:
        fa, fb = flag(a), flag(b)
        if fa is not None and fb is not None:
            out.append((f"{a}|{b}", fa | fb))
    return out


def add_polyline(
    dl: imgui.ImDrawList, pts: list[imgui.ImVec2], col: int, flags: int, thickness: float
) -> None:
    """
    By keyword: the order of thickness and flags differs between Dear ImGui
    versions (1.92.900's binding is thickness, flags), and a positional call
    would pass one for the other without an error on some builds.
    """
    dl.add_polyline(cast(list[imgui.ImVec2Like], pts), col, thickness=thickness, flags=flags)


def mode_strokes() -> None:
    make_context()
    io = imgui.get_io()
    io.fonts.add_font_default()
    style = imgui.get_style()
    emit(
        "stroke_env",
        imgui=imgui.get_version(),
        anti_aliased_lines=style.anti_aliased_lines,
        aa_line_ends_default=getattr(style, "anti_aliased_line_ends", "ABSENT"),
    )
    white = imgui.IM_COL32(255, 255, 255, 255)
    origin = (100.0, 100.0)
    rows = []

    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(1900, 1900))
    imgui.begin(
        "strokes", None, imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_background
    )
    dl = imgui.get_window_draw_list()
    pts = poly_from_path(ZIGZAG, origin)
    abs_pts = [(p.x, p.y) for p in pts]
    for name, fl in stroke_configs():
        for thick in (1.0, 1.5, 3.0, 8.0):
            for closed in (False, True):
                v0, i0 = dl.vtx_buffer.size(), dl.idx_buffer.size()
                f = fl | (int(imgui.ImDrawFlags_.closed) if closed else 0)
                add_polyline(dl, pts, white, f, thick)
                vs = vertices(dl, v0)
                segs = list(zip(abs_pts, abs_pts[1:], strict=False)) + (
                    [(abs_pts[-1], abs_pts[0])] if closed else []
                )
                # Spike: furthest any vertex lies from the centre line, over half
                # the thickness. A miter at 30 degrees is ~3.9; a bevel stays ~1.
                spike = max(
                    (min(seg_distance((x, y), a, b) for a, b in segs) for x, y, *_ in vs),
                    default=0.0,
                )
                opaque = [(x, y) for x, y, _u, _v, _c, a in vs if a > 0]
                rows.append(
                    {
                        "flags": name,
                        "thickness": thick,
                        "closed": closed,
                        "vtx": len(vs),
                        "idx": dl.idx_buffer.size() - i0,
                        "spike_over_half_thickness": round(spike / (thick / 2), 3),
                        "alpha0_vtx": sum(1 for *_, a in vs if a == 0),
                        "opaque_bbox": _bbox(opaque),
                    }
                )
    for r in rows:
        emit("stroke_polyline", **r)

    # AddLine's +0.5 offset and AddRect's stroke side, as vertex bounding boxes.
    for thick in (1.0, 2.0, 3.0):
        for name, fl in [("default", 0)] + [
            (n, v)
            for n in ("stroke_inside", "stroke_center", "stroke_outside", "stroke_legacy")
            if (v := flag(n)) is not None
        ]:
            v0 = dl.vtx_buffer.size()
            _add_line(dl, (100.0, 1000.0), (200.0, 1000.0), white, thick, fl)
            line_bbox = _bbox([(x, y) for x, y, *_r, a in vertices(dl, v0) if a > 0])
            v0 = dl.vtx_buffer.size()
            _add_rect(dl, (100.0, 1100.0), (200.0, 1150.0), white, thick, fl)
            rect_bbox = _bbox([(x, y) for x, y, *_r, a in vertices(dl, v0) if a > 0])
            emit(
                "stroke_line_rect",
                flags=name,
                thickness=thick,
                line_from_100_1000_to_200_1000=line_bbox,
                rect_100_1100_200_1150=rect_bbox,
            )
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context()


def _add_line(
    dl: imgui.ImDrawList,
    a: tuple[float, float],
    b: tuple[float, float],
    col: int,
    t: float,
    fl: int,
) -> None:
    # 1.92.900's add_line takes no flags; a flag passed to it is reported, not dropped.
    if fl:
        try:
            dl.add_line(imgui.ImVec2(*a), imgui.ImVec2(*b), col, thickness=t, flags=fl)  # type: ignore[call-arg]
        except TypeError as e:
            emit("add_line_takes_no_flags", error=str(e).splitlines()[0])
        return
    dl.add_line(imgui.ImVec2(*a), imgui.ImVec2(*b), col, thickness=t)


def _add_rect(
    dl: imgui.ImDrawList,
    a: tuple[float, float],
    b: tuple[float, float],
    col: int,
    t: float,
    fl: int,
) -> None:
    dl.add_rect(imgui.ImVec2(*a), imgui.ImVec2(*b), col, rounding=0.0, thickness=t, flags=fl)


def _bbox(pts: list[tuple[float, float]]) -> list[float] | None:
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]


# ------------------------------------------------------------- windowed runner


def save_png(path: Path, rgb: Any) -> None:
    """
    An (h, w, 3|4) uint8 array as a PNG, with zlib only.
    """
    h, w, ch = rgb.shape
    kind = {3: 2, 4: 6}[ch]
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, kind, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def run_window(
    gui: Callable[[int], bool],
    shot: Path | None,
    size: tuple[int, int] = (1400, 1000),
    light: bool = False,
    before_swap: Callable[[], None] | None = None,
) -> None:
    """
    One hello_imgui window. gui(frame) draws inside a full-window ImGui window
    and returns False to stop; the last frame is saved to `shot`.
    """
    from imgui_bundle import hello_imgui, immapp

    frame = [0]

    def body() -> None:
        if frame[0] == 0:
            (imgui.style_colors_light if light else imgui.style_colors_dark)()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.get_io().display_size)
        imgui.begin("probe", None, imgui.WindowFlags_.no_decoration)
        go_on = gui(frame[0])
        imgui.end()
        frame[0] += 1
        if not go_on:
            hello_imgui.get_runner_params().app_shall_exit = True

    p = hello_imgui.RunnerParams()
    p.callbacks.show_gui = body
    if before_swap:
        p.callbacks.before_swap = before_swap
    p.app_window_params.window_geometry.size = size
    p.app_window_params.window_title = "bundle193 probe"
    p.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    p.fps_idling.enable_idling = False
    p.ini_folder_type = hello_imgui.IniFolderType.absolute_path
    p.ini_filename = str((OUT / "probe.ini").resolve())
    immapp.run(p)
    if shot is not None:
        save_png(shot, hello_imgui.final_app_window_screenshot())


def rss_kb() -> int:
    """
    Current resident set, from /proc (Linux only).
    """
    return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024


def white_uv() -> tuple[float, float]:
    uv = imgui.get_font_tex_uv_white_pixel()
    return (uv.x, uv.y)


def classify(vs: list[Vert]) -> dict[str, Any]:
    """
    Vertices split by texture coordinate: at the atlas's white pixel is flat
    geometry; elsewhere is glyphs (and, on builds that draw AA lines from the
    atlas, baked line texels). Plus the commonest opaque geometry colours.
    """
    wu, wv = white_uv()
    geo = [v for v in vs if abs(v[2] - wu) < 1e-6 and abs(v[3] - wv) < 1e-6]
    cols = collections.Counter(
        f"#{v[4] & 0xFF:02x}{(v[4] >> 8) & 0xFF:02x}{(v[4] >> 16) & 0xFF:02x}"
        for v in geo
        if v[5] == 255
    )
    return {
        "vtx": len(vs),
        "white_uv_vtx": len(geo),
        "other_uv_vtx": len(vs) - len(geo),
        "top_colours": cols.most_common(6),
    }


def style_colours() -> dict[str, str]:
    out = {}
    for name in ("text", "frame_bg", "window_bg", "text_disabled", "frame_bg_hovered", "border"):
        c = imgui.get_style().color_(getattr(imgui.Col_, name))
        out[name] = "#{:02x}{:02x}{:02x}".format(*(round(255 * k) for k in (c.x, c.y, c.z)))
    return out


def render_case(
    source: str,
    frames: int,
    *,
    font_size: float | None = None,
    child: tuple[float, float] | None = None,
    hover: bool = False,
    stream: bool = False,
    light: bool = False,
    shot: Path | None = None,
) -> dict[str, Any]:
    """
    Draw `source` with rich_md.render_mermaid for `frames` frames and measure.
    """
    from imgui_bundle import rich_md  # type: ignore[attr-defined]

    res: dict[str, Any] = {"times_ms": []}
    lines = source.splitlines(keepends=True)
    total_vtx = [0]

    def gui(f: int) -> bool:
        src = "".join(lines[: 1 + f % len(lines)]) if stream else source
        imgui.text(f"frame {f}")
        if font_size:
            imgui.push_font(None, font_size)
        if child:
            imgui.begin_child("host", imgui.ImVec2(*child), imgui.ChildFlags_.borders)
        dl = imgui.get_window_draw_list()
        v0 = dl.vtx_buffer.size()
        cur = imgui.get_cursor_screen_pos()
        t = time.perf_counter()
        rich_md.render_mermaid(src)
        res["times_ms"].append(1000 * (time.perf_counter() - t))
        if f == 5:
            res["rss_kb_frame5"] = rss_kb()
        if f == frames - 1:
            res["rss_kb_last_frame"] = rss_kb()
        if f == frames - 1 or f == 1:
            r0, r1 = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            key = "last" if f == frames - 1 else "first"
            res[key] = {
                "cursor": [cur.x, cur.y],
                "item_rect": [r0.x, r0.y, r1.x, r1.y],
                "item_size": [r1.x - r0.x, r1.y - r0.y],
                "item_hovered": imgui.is_item_hovered(),
                **classify(vertices(dl, v0)),
            }
            if child:
                wp, ws = imgui.get_window_pos(), imgui.get_window_size()
                res[key]["child_rect"] = [wp.x, wp.y, wp.x + ws.x, wp.y + ws.y]
                vs = vertices(dl, v0)
                res[key]["vtx_outside_child"] = sum(
                    1
                    for x, y, *_ in vs
                    if not (wp.x <= x <= wp.x + ws.x and wp.y <= y <= wp.y + ws.y)
                )
                res[key]["child_scroll_max"] = [imgui.get_scroll_max_x(), imgui.get_scroll_max_y()]
            if f == frames - 1:
                res["style"] = style_colours()
                res["font_size"] = imgui.get_font_size()
        if hover and f >= 2:
            r0, r1 = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            imgui.get_io().add_mouse_pos_event((r0.x + r1.x) / 2, r0.y + 40)
        if child:
            imgui.end_child()
        if font_size:
            imgui.pop_font()
        return f < frames - 1

    def count() -> None:
        dd = imgui.get_draw_data()
        if dd is not None:
            total_vtx[0] = dd.total_vtx_count
            res["draw_lists"] = getattr(dd, "cmd_lists_count", None)

    run_window(gui, shot, light=light, before_swap=count)
    ts = res["times_ms"]
    res["frame0_ms"] = round(ts[0], 3) if ts else None
    res["warm_median_ms"] = round(statistics.median(ts[min(10, len(ts) - 1) :]), 4) if ts else None
    res["warm_max_ms"] = round(max(ts[min(10, len(ts) - 1) :]), 4) if ts else None
    res["total_vtx_last_frame"] = total_vtx[0]
    res["maxrss_kb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if not stream:
        del res["times_ms"]
    else:
        res["times_ms"] = [round(t, 3) for t in ts]
    return res


def child_main(case: str, out: Path) -> None:
    """
    One case in this process. Kinds: d:<diagram>, h:<hostile>, plus variants.
    """
    kind, _, name = case.partition(":")
    if kind == "d":
        r = render_case(DIAGRAMS[name], 90, shot=out / f"mermaid-{name}.png")
    elif kind == "light":
        r = render_case(DIAGRAMS[name], 30, light=True, shot=out / f"mermaid-{name}-light.png")
    elif kind == "font":
        r = render_case(DIAGRAMS[name], 30, font_size=24.0, shot=out / f"mermaid-{name}-font24.png")
    elif kind == "child":
        r = render_case(
            DIAGRAMS[name], 30, child=(360.0, 220.0), shot=out / f"mermaid-{name}-child.png"
        )
    elif kind == "hover":
        r = render_case(DIAGRAMS[name], 30, hover=True, shot=out / f"mermaid-{name}-hover.png")
    elif kind == "stream":
        r = render_case(DIAGRAMS[name], 2 * len(DIAGRAMS[name].splitlines()) + 20, stream=True)
    elif kind == "h":
        src = HOSTILE[name]()
        r = render_case(src, 15, shot=out / f"hostile-{name}.png")
        r["source_bytes"] = len(src)
    else:
        raise SystemExit(f"unknown case {case}")
    emit("mermaid_case", case=case, **r)


def mode_mermaid(out: Path, timeout_s: float) -> None:
    try:
        from imgui_bundle import rich_md  # type: ignore[attr-defined]  # noqa: F401
    except ImportError as e:
        emit("mermaid_skipped", reason=f"no rich_md in imgui-bundle {bundle_version()}: {e}")
        return
    cases = [f"d:{n}" for n in DIAGRAMS]
    cases += [
        "light:flowchart",
        "font:flowchart",
        "child:board21",
        "hover:flowchart",
        "stream:board21",
        "stream:sequence",
    ]
    cases += [f"h:{n}" for n in HOSTILE]
    for case in cases:
        t = time.perf_counter()
        try:
            p = subprocess.run(
                [
                    sys.executable,
                    os.path.abspath(__file__),
                    "child",
                    "--case",
                    case,
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                preexec_fn=_limit_memory,
            )
            outcome = "exit" if p.returncode >= 0 else "signal"
            code = p.returncode
            stdout, stderr = p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            outcome, code = "timeout", None
            stdout = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        for line in stdout.splitlines():
            if line.startswith("@@ "):
                print(line, flush=True)
        emit(
            "mermaid_process",
            case=case,
            outcome=outcome,
            code=code,
            wall_s=round(time.perf_counter() - t, 2),
            stderr_tail=stderr[-600:],
        )


def _limit_memory() -> None:
    # 4 GiB of address space: a runaway layout fails in its child, not the host.
    resource.setrlimit(resource.RLIMIT_AS, (4 << 30, 4 << 30))


# ------------------------------------------------------------------------ shots


def mode_shots(out: Path) -> None:
    """
    The stroke panel on screen: the zigzag under each flag at 1, 1.5, 3, 8 px.
    """
    configs = stroke_configs()

    def gui(f: int) -> bool:
        dl = imgui.get_window_draw_list()
        base = imgui.get_cursor_screen_pos()
        white = imgui.IM_COL32(230, 230, 230, 255)
        imgui.text(f"imgui {imgui.get_version()}  bundle {bundle_version()}")
        for row, (name, fl) in enumerate(configs):
            y = base.y + 30 + row * 75
            dl.add_text(imgui.ImVec2(base.x + 5, y + 20), white, name)
            for col, thick in enumerate((1.0, 1.5, 3.0, 8.0)):
                x = base.x + 200 + col * 300
                small = [(px * 0.55, py * 0.55) for px, py in ZIGZAG]
                add_polyline(dl, poly_from_path(small, (x, y)), white, fl, thick)
        y = base.y + 30 + len(configs) * 75
        for col, thick in enumerate((1.0, 2.0, 3.0)):
            x = base.x + 200 + col * 300
            _add_line(dl, (x, y + 10), (x + 200, y + 10), white, thick, 0)
            _add_rect(dl, (x, y + 25), (x + 200, y + 60), white, thick, 0)
        return f < 10

    run_window(gui, out / f"strokes-{imgui.get_version()}.png", size=(1500, 1100))


# ------------------------------------------------------------------------- main


def main() -> None:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["info", "api", "strokes", "mermaid", "shots", "all", "child"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--scan", nargs="*", default=[])
    ap.add_argument("--case")
    ap.add_argument("--timeout", type=float, default=90.0)
    a = ap.parse_args()
    OUT = Path(a.out).resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.mode == "child":
        os.chdir(OUT)
        child_main(a.case, OUT)
        return
    if a.mode in ("info", "all"):
        mode_info()
    if a.mode in ("api", "all"):
        mode_api(a.scan)
    if a.mode in ("strokes", "all"):
        mode_strokes()
    if a.mode == "mermaid":
        mode_mermaid(OUT, a.timeout)
    if a.mode == "shots":
        os.chdir(OUT)
        mode_shots(OUT)


if __name__ == "__main__":
    main()
