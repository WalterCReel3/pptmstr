"""
Probe for board task ``displaylist-proto``: Mermaid drawn natively, from a bounded
display list, with no raster.

    .venv/bin/python -B scripts/displaylist_proto.py selftest   # pure checks, no display
    .venv/bin/python -B scripts/displaylist_proto.py dump       # child conversion, no display
    XVFB_SHOT_SCREEN=1600x1400x24 scripts/xvfb_shot.sh -- \\
        .venv/bin/python -B scripts/displaylist_proto.py run    # the window

The write-up is ``notes/vector-rendering/probe-displaylist.md``. Nothing in
``pptmstr/`` is changed: the figure host's ``figure_for`` is replaced in this
process only, so a mermaid fence in a pane chosen as native is drawn by
``NativeFigure`` and one in any other pane by today's ``FigureWidget``.

**Where the display list comes from.** merman 0.9.1's ``layout_json`` gives node
boxes, edge points and cluster boxes, but not shapes, text runs, markers or the
sequence diagram's lifelines and frames: drawing from it would mean writing
merman's SVG renderer again for each family. So the source is the SVG pptmstr
already makes (``mermaid.layout``: directives and config out, labels let
overflow, sanitised), parsed in the render child into the display list. The
child hands back the list; the GUI process never sees SVG.

**Colours are roles, never the SVG's.** Every path and text run carries a role
(node, cluster, edge, note, ...), and the colour is the current theme's for that
role, looked up each frame. A diagram's own ``style``/``classDef`` colours are
therefore not drawn: that is a fidelity loss this probe accepts and measures.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "shots" / "displaylist"

# ----------------------------------------------------------------------------------
# The schema and its bounds
# ----------------------------------------------------------------------------------

MAX_SIDE = 16_384.0
"""The largest natural width or height: the sanitiser's ``MAX_SIDE``."""
COORD_MIN, COORD_MAX = -MAX_SIDE, 2.0 * MAX_SIDE
"""Every coordinate after transforms, in diagram units. Outside it: refused."""
MAX_ITEMS = 10_000
"""Paths plus text runs, marker instances included."""
MAX_SEGS_PER_PATH = 4_096
MAX_SEGS_TOTAL = 100_000
MAX_SUBPATHS_PER_PATH = 256
MAX_TEXT_RUNS = 5_000
MAX_RUN_CHARS = 512
"""A longer run is cut, with an ellipsis: a bound on a string, not a refusal."""
MAX_TEXT_CHARS = 200_000
FONT_SIZE_MIN, FONT_SIZE_MAX = 1.0, 256.0
STROKE_MAX = 64.0
MAX_DASHES = 8
DASH_MIN, DASH_MAX = 0.5, 4_096.0
MAX_NODES = 2_000
MAX_EDGES = 5_000
MAX_HIT_POINTS = 256
MAX_ID_CHARS = 128
MAX_USE_DEPTH = 4
"""``<use>`` inside ``<use>``: the sanitiser caps the expanded tree; this caps the walk."""

# Drawing bounds, in the GUI process.
FLATTEN_TOL_PX = 0.25
MAX_CURVE_STEPS = 64
MAX_POINTS_PER_ITEM = 16_384
MAX_DASH_PIECES = 2_048
"""A dashed path that would split into more pieces is drawn solid."""

# Roles. A role names what a primitive is, and the theme picks its colour.
NODE, CLUSTER, EDGE, MARKER, LABEL, EDGE_LABEL, NOTE, FRAME, LIFELINE, SOLID, BADGE_TEXT, HOLLOW = (
    range(12)
)
ROLE_NAMES = (
    "node",
    "cluster",
    "edge",
    "marker",
    "label",
    "edge_label",
    "note",
    "frame",
    "lifeline",
    "solid",
    "badge_text",
    "hollow",
)
NONZERO, EVENODD = 0, 1
JOIN_MITER, JOIN_ROUND, JOIN_BEVEL = 0, 1, 2
CAP_BUTT, CAP_ROUND, CAP_SQUARE = 0, 1, 2
ANCHOR_START, ANCHOR_MIDDLE, ANCHOR_END = 0, 1, 2
BASE_ALPHABETIC, BASE_CENTRAL = 0, 1


@dataclass(frozen=True, slots=True)
class PathItem:
    """
    One path. ``ops`` is one character per segment (``M L C Q Z``) and ``xy`` the
    absolute coordinates they consume (2, 2, 6, 4, 0), in diagram units.
    """

    role: int
    fill: bool
    fill_rule: int
    stroke: bool
    width: float
    join: int
    cap: int
    dash: tuple[float, ...]
    ops: str
    xy: tuple[float, ...]
    bbox: tuple[float, float, float, float]
    node: int = -1
    edge: int = -1


@dataclass(frozen=True, slots=True)
class TextRun:
    """
    One axis-aligned line of text at ``(x, y)``: ``anchor`` says which end or the
    middle is at ``x``, ``baseline`` whether ``y`` is the alphabetic baseline or the
    middle. ``box`` is the width merman laid the label out in, or 0.
    """

    text: str
    x: float
    y: float
    size: float
    anchor: int
    baseline: int
    role: int
    bold: bool
    box: float
    node: int = -1
    edge: int = -1


@dataclass(frozen=True, slots=True)
class HitNode:
    id: str
    box: tuple[float, float, float, float]
    attach: tuple[tuple[float, float, float, float], ...]


@dataclass(frozen=True, slots=True)
class HitEdge:
    id: str
    points: tuple[float, ...]
    ends: tuple[int, int]


@dataclass(frozen=True, slots=True)
class DisplayList:
    width: float
    height: float
    origin: tuple[float, float]
    """The viewBox's top-left: diagram units at the figure's top-left corner."""
    items: tuple[PathItem | TextRun, ...]
    nodes: tuple[HitNode, ...]
    edges: tuple[HitEdge, ...]
    stats: dict[str, Any] = field(default_factory=dict)


class Refusal(Exception):
    """A diagram the display list will not carry, and why."""


def validate(dl: DisplayList) -> str | None:
    """
    Every bound, checked again on the GUI's side of the pipe: ``None`` or the first
    one broken. The child already refuses past these; this is the receiving end not
    trusting the sending one.
    """
    fin = math.isfinite
    if not (0 < dl.width <= MAX_SIDE and 0 < dl.height <= MAX_SIDE):
        return "size"
    if len(dl.items) > MAX_ITEMS or len(dl.nodes) > MAX_NODES or len(dl.edges) > MAX_EDGES:
        return "count"
    segs = chars = runs = 0
    for it in dl.items:
        if isinstance(it, PathItem):
            segs += len(it.ops)
            if len(it.ops) > MAX_SEGS_PER_PATH or not 0 <= it.width <= STROKE_MAX:
                return "path"
            if len(it.dash) > MAX_DASHES or any(not DASH_MIN <= d <= DASH_MAX for d in it.dash):
                return "dash"
            if any(not (fin(v) and COORD_MIN <= v <= COORD_MAX) for v in it.xy):
                return "coordinate"
        else:
            runs += 1
            chars += len(it.text)
            if len(it.text) > MAX_RUN_CHARS or not FONT_SIZE_MIN <= it.size <= FONT_SIZE_MAX:
                return "text"
            if not (fin(it.x) and fin(it.y) and COORD_MIN <= it.x <= COORD_MAX):
                return "coordinate"
        if not -1 <= it.node < len(dl.nodes) or not -1 <= it.edge < len(dl.edges):
            return "owner"
    if segs > MAX_SEGS_TOTAL or runs > MAX_TEXT_RUNS or chars > MAX_TEXT_CHARS:
        return "total"
    for n in dl.nodes:
        if len(n.id) > MAX_ID_CHARS:
            return "id"
    for e in dl.edges:
        if len(e.id) > MAX_ID_CHARS or len(e.points) > 2 * MAX_HIT_POINTS:
            return "edge"
    return None


# ----------------------------------------------------------------------------------
# SVG to display list (render child)
# ----------------------------------------------------------------------------------

Affine = tuple[float, float, float, float, float, float]
IDENTITY: Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
SVG = "{http://www.w3.org/2000/svg}"


def mul(m: Affine, n: Affine) -> Affine:
    """``m`` after ``n``: a point goes through ``n`` first."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a * a2 + c * b2,
        b * a2 + d * b2,
        a * c2 + c * d2,
        b * c2 + d * d2,
        a * e2 + c * f2 + e,
        b * e2 + d * f2 + f,
    )


def apply(m: Affine, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


_NUM = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")


def parse_transform(text: str | None) -> Affine:
    if not text or text.strip() == "none":
        return IDENTITY
    m = IDENTITY
    for name, args in _TRANSFORM.findall(text):
        v = [float(x) for x in _NUM.findall(args)]
        if name == "matrix" and len(v) == 6:
            t: Affine = (v[0], v[1], v[2], v[3], v[4], v[5])
        elif name == "translate" and v:
            t = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            t = (v[0], 0.0, 0.0, v[1] if len(v) > 1 else v[0], 0.0, 0.0)
        elif name == "rotate" and v:
            r = math.radians(v[0])
            t = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0.0, 0.0)
            if len(v) == 3:
                t = mul(mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX" and v:
            t = (1.0, 0.0, math.tan(math.radians(v[0])), 1.0, 0.0, 0.0)
        elif name == "skewY" and v:
            t = (1.0, math.tan(math.radians(v[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        m = mul(m, t)
    return m


_PATH_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def parse_path(d: str) -> tuple[str, list[float]]:
    """
    ``d`` as absolute ``M L C Q Z`` segments: ``H V`` become ``L``, ``S T`` their
    reflected ``C Q``, and ``A`` up to four ``C`` per arc. Malformed input stops the
    parse where it went wrong, as SVG renderers do.
    """
    raw: list[str] = []
    for cmd, num in _PATH_TOKEN.findall(d):
        raw.append(cmd or num)
    ops: list[str] = []
    xy: list[float] = []
    x = y = sx = sy = 0.0
    last_c: tuple[float, float] | None = None
    last_q: tuple[float, float] | None = None
    i = 0
    cmd = ""
    while i < len(raw):
        tok = raw[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                ops.append("Z")
                x, y = sx, sy
                last_c = last_q = None
                continue
        elif not cmd or cmd in "Zz":
            break
        up = cmd.upper()
        rel = cmd != up
        n = _ARGS[up]
        if up == "A":
            # Flags may be fused with what follows ("01" or "11.5").
            args: list[float] = []
            j = i
            while len(args) < 7 and j < len(raw):
                t = raw[j]
                if t.isalpha():
                    break
                if len(args) in (3, 4) and len(t) > 1 and t[0] in "01" and t[1] in "01.":
                    args.append(float(t[0]))
                    raw[j] = t[1:]
                    continue
                args.append(float(t))
                j += 1
            if len(args) < 7:
                break
            i = j
        else:
            if i + n > len(raw) or any(raw[k].isalpha() for k in range(i, i + n)):
                break
            args = [float(raw[k]) for k in range(i, i + n)]
            i += n
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if up == "M":
            x, y = args[0] + ox, args[1] + oy
            sx, sy = x, y
            ops.append("M")
            xy += [x, y]
            cmd = "l" if rel else "L"
            last_c = last_q = None
        elif up in "LHV":
            if up == "L":
                x, y = args[0] + ox, args[1] + oy
            elif up == "H":
                x = args[0] + (x if rel else 0.0)
            else:
                y = args[0] + (y if rel else 0.0)
            ops.append("L")
            xy += [x, y]
            last_c = last_q = None
        elif up in "CS":
            if up == "C":
                x1, y1 = args[0] + ox, args[1] + oy
                rest = args[2:]
            else:
                x1, y1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
                rest = args
            x2, y2 = rest[0] + ox, rest[1] + oy
            x, y = rest[2] + ox, rest[3] + oy
            ops.append("C")
            xy += [x1, y1, x2, y2, x, y]
            last_c, last_q = (x2, y2), None
        elif up in "QT":
            if up == "Q":
                x1, y1 = args[0] + ox, args[1] + oy
                x, y = args[2] + ox, args[3] + oy
            else:
                x1, y1 = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
                x, y = args[0] + ox, args[1] + oy
            ops.append("Q")
            xy += [x1, y1, x, y]
            last_q, last_c = (x1, y1), None
        else:  # A
            ex, ey = args[5] + ox, args[6] + oy
            for c in arc_to_cubics(
                x, y, args[0], args[1], args[2], args[3] != 0, args[4] != 0, ex, ey
            ):
                ops.append("C")
                xy += c
            x, y = ex, ey
            last_c = last_q = None
    return "".join(ops), xy


def arc_to_cubics(
    x1: float,
    y1: float,
    rx: float,
    ry: float,
    phi_deg: float,
    large: bool,
    sweep: bool,
    x2: float,
    y2: float,
) -> list[list[float]]:
    """SVG's endpoint arc as cubics, one per quarter turn or less (SVG 1.1 F.6.5)."""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [[x1, y1, x2, y2, x2, y2]]
    phi = math.radians(phi_deg % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux: float, uy: float, vx: float, vy: float) -> float:
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, math.ceil(abs(dt) / (math.pi / 2) - 1e-9))
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    out = []
    t = t1
    for _ in range(n):
        c1, s1 = math.cos(t), math.sin(t)
        c2, s2 = math.cos(t + step), math.sin(t + step)
        p = [
            (c1 - k * s1, s1 + k * c1),
            (c2 + k * s2, s2 - k * c2),
            (c2, s2),
        ]
        seg = []
        for ux, uy in p:
            ux, uy = ux * rx, uy * ry
            seg += [cp * ux - sp * uy + cx, sp * ux + cp * uy + cy]
        out.append(seg)
        t += step
    return out


def _style(el: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for decl in el.get("style", "").split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _length(v: str | None, size: float = 16.0) -> float | None:
    if v is None:
        return None
    m = _NUM.match(v.strip())
    if not m:
        return None
    n = float(m.group())
    unit = v.strip()[m.end() :].strip()
    if unit == "em":
        return n * size
    if unit == "pt":
        return n * 4 / 3
    return n


def _dash_for(classes: set[str], style: dict[str, str], el: ET.Element) -> tuple[float, ...]:
    """
    The dash pattern merman's stylesheet gives a class, else an inline one. Explicit
    zero patterns (``0 0``, merman's marker-margin trick) are solid.
    """
    v = style.get("stroke-dasharray")
    if v and v != "none":
        nums = [abs(float(x)) for x in _NUM.findall(v)]
        if len(nums) == 1:
            nums *= 2
        if nums and all(n >= DASH_MIN for n in nums) and len(nums) <= MAX_DASHES:
            return tuple(min(DASH_MAX, n) for n in nums)
        # A pattern with a zero in it (`0 0 34.5 0`) is merman leaving room for a
        # marker, not a dash: the class decides.
    for name, dash in _CLASS_DASH:
        if name in classes:
            return dash
    v = el.get("stroke-dasharray")
    if v and v != "none":
        nums = [abs(float(x)) for x in _NUM.findall(v)]
        if len(nums) == 1:
            nums *= 2
        if nums and all(n >= DASH_MIN for n in nums) and len(nums) <= MAX_DASHES:
            return tuple(min(DASH_MAX, n) for n in nums)
    return ()


# merman 0.9.1's stylesheet, as far as dashes go (read from its CSS in each family).
_CLASS_DASH: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("messageLine1", (2.0, 2.0)),
    ("loopLine", (2.0, 2.0)),
    ("note-edge", (5.0, 5.0)),
    ("edge-pattern-dashed", (3.0, 3.0)),
    ("edge-pattern-dotted", (2.0, 2.0)),
    ("dashed-line", (3.0, 3.0)),
    ("dotted-line", (1.0, 2.0)),
)

# Classes merman's stylesheet strokes or fills, which beat a presentation attribute
# on the same element (CSS outranks attributes): `stroke="none"` on a
# `messageLine0` is overridden by `.messageLine0{stroke:...}`.
_CSS_PAINTED = frozenset(
    {
        "messageLine0",
        "messageLine1",
        "loopLine",
        "actor-line",
        "note",
        "labelBox",
        "actor",
        "flowchart-link",
        "relation",
        "transition",
        "relationshipLine",
    }
)
_EDGE_CLASSES = frozenset(
    {"flowchart-link", "relation", "transition", "relationshipLine", "messageLine0", "messageLine1"}
)


@dataclass(slots=True)
class _Ctx:
    m: Affine
    role: int
    node: int
    edge_label: str | None
    size: float
    bold: bool
    anchor: int
    baseline: int
    box: float
    in_marker: bool
    depth: int
    marker_fill: str | None = None
    """
    ``none`` or ``hollow`` where merman's stylesheet overrides a marker's fill with
    ``!important``.
    """


class _Builder:
    def __init__(self, root: ET.Element) -> None:
        self.root = root
        self.ids: dict[str, ET.Element] = {}
        for el in root.iter():
            i = el.get("id")
            if i:
                self.ids[i] = el
        self.items: list[PathItem | TextRun] = []
        self.nodes: list[HitNode] = []
        self.node_index: dict[str, int] = {}
        self.node_boxes: list[list[float]] = []
        self.node_attach: list[list[tuple[float, float, float, float]]] = []
        self.edges: list[tuple[str, list[float]]] = []
        self.edge_index: dict[str, int] = {}
        self.pending_labels: list[tuple[int, str]] = []
        self.segs = 0
        self.chars = 0
        self.runs = 0
        self.dropped: dict[str, int] = {}

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1

    # -- nodes and edges --

    def node_for(self, nid: str) -> int:
        nid = nid[:MAX_ID_CHARS]
        got = self.node_index.get(nid)
        if got is None:
            if len(self.node_index) >= MAX_NODES:
                raise Refusal(f"more than {MAX_NODES} nodes")
            got = self.node_index[nid] = len(self.node_boxes)
            self.node_boxes.append([math.inf, math.inf, -math.inf, -math.inf])
            self.node_attach.append([])
            self.nodes.append(HitNode(nid, (0, 0, 0, 0), ()))
        return got

    def grow(self, node: int, bbox: tuple[float, float, float, float]) -> None:
        b = self.node_boxes[node]
        b[0], b[1] = min(b[0], bbox[0]), min(b[1], bbox[1])
        b[2], b[3] = max(b[2], bbox[2]), max(b[3], bbox[3])

    # -- the walk --

    def walk(self, el: ET.Element, ctx: _Ctx) -> None:
        tag = el.tag.replace(SVG, "") if isinstance(el.tag, str) else ""
        if tag in ("defs", "marker", "symbol", "style", "title", "desc", "clipPath", "mask"):
            return
        style = _style(el)
        if style.get("display") == "none" or el.get("display") == "none":
            return
        if style.get("visibility") == "hidden":
            return
        op = style.get("opacity", el.get("opacity"))
        if op is not None and _length(op) == 0:
            return
        classes = set(el.get("class", "").split())
        m = mul(ctx.m, parse_transform(el.get("transform")))
        if "transform" in style:
            m = mul(m, parse_transform(style["transform"]))
        ctx = _Ctx(**{k: getattr(ctx, k) for k in _Ctx.__slots__})
        ctx.m = m
        size = _length(style.get("font-size") or el.get("font-size"), ctx.size)
        if size:
            ctx.size = size
        weight = style.get("font-weight") or el.get("font-weight")
        if weight:
            ctx.bold = weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600)
        anchor = style.get("text-anchor") or el.get("text-anchor")
        if anchor:
            ctx.anchor = {"middle": ANCHOR_MIDDLE, "end": ANCHOR_END}.get(anchor, ANCHOR_START)
        base = style.get("dominant-baseline") or el.get("dominant-baseline")
        if base:
            ctx.baseline = BASE_CENTRAL if base in ("central", "middle") else BASE_ALPHABETIC
        # Roles from structure.
        if tag == "g" and "node" in classes and el.get("id"):
            nid = el.get("id", "").removeprefix("merman-")
            ctx.node = self.node_for(nid)
            ctx.role = NOTE if "statediagram-note" in classes else NODE
        elif el.get("data-et") == "participant" and el.get("data-id"):
            ctx.node = self.node_for(el.get("data-id", ""))
            ctx.role = NODE
        elif el.get("data-et") == "note":
            ctx.role = NOTE
        if "cluster" in classes or "statediagram-cluster" in classes:
            ctx.role = CLUSTER
            # A subgraph or composite state is something an edge can end on, so it
            # is a hit node too; hover picks the smallest box under the pointer.
            if tag == "g" and el.get("id"):
                ctx.node = self.node_for(el.get("id", "").removeprefix("merman-"))
        if "edgeLabel" in classes:
            ctx.role = EDGE_LABEL
        if tag == "g" and "label" in classes and el.get("data-id") and ctx.role == EDGE_LABEL:
            ctx.edge_label = el.get("data-id")
        if tag == "svg" and el is not self.root:
            ctx.m = mul(ctx.m, self._viewport(el))
            ctx.box = _length(el.get("width")) or 0.0
        if tag == "use":
            href = (el.get("href") or "").lstrip("#")
            target = self.ids.get(href)
            if target is None or ctx.depth >= MAX_USE_DEPTH:
                self.drop("use")
                return
            ctx.m = mul(
                ctx.m, (1, 0, 0, 1, _length(el.get("x")) or 0.0, _length(el.get("y")) or 0.0)
            )
            ctx.depth += 1
            if target.tag.replace(SVG, "") == "symbol":
                for child in target:
                    self.walk(child, ctx)
            else:
                self.walk(target, ctx)
            return
        if tag == "text":
            self.text(el, ctx, style, classes)
            return
        if tag in ("path", "rect", "circle", "ellipse", "line", "polyline", "polygon"):
            self.shape(tag, el, ctx, style, classes)
            return
        for child in el:
            self.walk(child, ctx)

    def _viewport(self, el: ET.Element) -> Affine:
        x = _length(el.get("x")) or 0.0
        y = _length(el.get("y")) or 0.0
        t: Affine = (1, 0, 0, 1, x, y)
        vb = [float(v) for v in _NUM.findall(el.get("viewBox", ""))]
        w, h = _length(el.get("width")), _length(el.get("height"))
        if len(vb) == 4 and w and h and vb[2] > 0 and vb[3] > 0:
            sx, sy = w / vb[2], h / vb[3]
            if el.get("preserveAspectRatio", "") != "none":
                sx = sy = min(sx, sy)
            t = mul(t, (sx, 0, 0, sy, -vb[0] * sx, -vb[1] * sy))
        return t

    # -- shapes --

    def shape(
        self, tag: str, el: ET.Element, ctx: _Ctx, style: dict[str, str], classes: set[str]
    ) -> None:
        def f(k: str) -> float:
            return _length(el.get(k)) or 0.0

        if tag == "path":
            ops, xy = parse_path(el.get("d", ""))
        elif tag == "rect":
            ops, xy = rect_path(
                f("x"),
                f("y"),
                f("width"),
                f("height"),
                _length(el.get("rx")),
                _length(el.get("ry")),
            )
        elif tag in ("circle", "ellipse"):
            r = f("r")
            ops, xy = ellipse_path(f("cx"), f("cy"), r or f("rx"), r or f("ry"))
        elif tag == "line":
            ops, xy = "ML", [f("x1"), f("y1"), f("x2"), f("y2")]
        else:
            pts = [float(v) for v in _NUM.findall(el.get("points", ""))]
            pts = pts[: len(pts) // 2 * 2]
            ops = "M" + "L" * (len(pts) // 2 - 1) if pts else ""
            if tag == "polygon" and pts:
                ops += "Z"
            xy = pts
        if not ops:
            return
        role = ctx.role
        if ctx.in_marker:
            role = MARKER
        elif classes & _EDGE_CLASSES or el.get("data-edge") == "true":
            role = EDGE
        elif "actor-line" in classes:
            role = LIFELINE
        elif "loopLine" in classes or "labelBox" in classes:
            role = FRAME
        elif "note" in classes:
            role = NOTE
        elif "state-start" in classes or "state-end" in classes:
            role = SOLID
        elif "actor" in classes and el.get("name"):
            ctx.node = self.node_for(el.get("name", ""))
            role = NODE
        closed = "Z" in ops or tag in ("rect", "circle", "ellipse", "polygon")
        authored = not (classes & _CSS_PAINTED)
        fill_v = style.get("fill") or (el.get("fill") if authored else None)
        stroke_v = style.get("stroke") or (el.get("stroke") if authored else None)
        default_fill = role in (NODE, CLUSTER, NOTE, MARKER, SOLID) or (role == FRAME and closed)
        default_fill = default_fill and (tag != "line")
        if role == NODE and tag == "path" and not closed:
            # merman's neo look: an open outline is ink, the fill has its own path.
            default_fill = fill_v not in (None, "none", "transparent")
        fill = default_fill if fill_v is None else fill_v not in ("none", "transparent")
        if ctx.in_marker and ctx.marker_fill == "none":
            fill = False
        elif ctx.in_marker and ctx.marker_fill == "hollow":
            role = HOLLOW
        if role in (EDGE, LIFELINE) or tag == "line":
            fill = False
        if stroke_v is not None:
            stroke = stroke_v not in ("none", "transparent")
        elif role == FRAME:
            # SVG's default stroke is none; merman strokes frames by class.
            stroke = bool(classes & _CSS_PAINTED)
        else:
            stroke = role != SOLID
        width = _length(style.get("stroke-width") or el.get("stroke-width"))
        if width is None:
            width = {EDGE: 1.5, FRAME: 1.5, LIFELINE: 0.5}.get(role, 1.0)
        if "edge-thickness-thick" in classes:
            width = 3.5
        if width <= 0:
            stroke = False
        scale = math.sqrt(abs(ctx.m[0] * ctx.m[3] - ctx.m[1] * ctx.m[2]))
        width = min(STROKE_MAX, width * scale)
        rule = EVENODD if (style.get("fill-rule") or el.get("fill-rule")) == "evenodd" else NONZERO
        dash = _dash_for(classes, style, el) if stroke else ()
        if scale != 1.0:
            dash = tuple(min(DASH_MAX, max(DASH_MIN, d * scale)) for d in dash)
        txy = self.transform(ops, xy, ctx.m)
        edge = -1
        if role == EDGE:
            eid = (el.get("data-id") or el.get("id") or f"edge{len(self.edges)}").removeprefix(
                "merman-"
            )
            edge = self.add_edge(eid[:MAX_ID_CHARS], ops, txy)
        if fill or stroke:
            self.add(
                PathItem(
                    role,
                    fill,
                    rule,
                    stroke,
                    width,
                    JOIN_MITER,
                    CAP_BUTT,
                    dash,
                    ops,
                    tuple(txy),
                    _bbox(txy),
                    ctx.node,
                    edge,
                )
            )
        for attr, at_start in (("marker-start", True), ("marker-end", False)):
            ref = style.get(attr) or el.get(attr)
            if ref:
                mm = re.search(r"url\(#([^)]+)\)", ref)
                target = self.ids.get(mm.group(1)) if mm else None
                if target is not None:
                    self.marker(target, ops, txy, at_start, width, ctx, edge)

    def transform(self, ops: str, xy: list[float], m: Affine) -> list[float]:
        out = []
        for i in range(0, len(xy), 2):
            px, py = apply(m, xy[i], xy[i + 1])
            if not (COORD_MIN <= px <= COORD_MAX and COORD_MIN <= py <= COORD_MAX):
                raise Refusal("a coordinate is out of range")
            out += [px, py]
        return out

    def marker(
        self,
        mk: ET.Element,
        ops: str,
        xy: list[float],
        at_start: bool,
        width: float,
        ctx: _Ctx,
        edge: int,
    ) -> None:
        if len(xy) < 2:
            return
        if at_start:
            px, py = xy[0], xy[1]
            dx, dy = _direction(xy, from_start=True)
        else:
            px, py = xy[-2], xy[-1]
            dx, dy = _direction(xy, from_start=False)
        orient = mk.get("orient", "0")
        if orient == "auto-start-reverse" and at_start:
            dx, dy = -dx, -dy
        if orient.startswith("auto"):
            ang = math.atan2(dy, dx)
        else:
            fixed = _NUM.match(orient)
            ang = math.radians(float(fixed.group())) if fixed else 0.0
        mw = _length(mk.get("markerWidth")) or 3.0
        mh = _length(mk.get("markerHeight")) or 3.0
        vb = [float(v) for v in _NUM.findall(mk.get("viewBox", ""))]
        s = 1.0
        if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
            s = min(mw / vb[2], mh / vb[3])
        if mk.get("markerUnits", "strokeWidth") == "strokeWidth" and width > 0:
            # At stroke-width 0 resvg draws the marker at unit scale (merman's
            # sequence numbers ride on such a line), so that is what is matched.
            s *= width
        rx, ry = _length(mk.get("refX")) or 0.0, _length(mk.get("refY")) or 0.0
        c, si = math.cos(ang), math.sin(ang)
        m = (c * s, si * s, -si * s, c * s, px, py)
        m = mul(m, (1, 0, 0, 1, -rx, -ry))
        mclass = set(mk.get("class", "").split())
        mfill = None
        if mclass & {"aggregation", "extension"} or "er" in mclass:
            mfill = "none"
        elif "lollipop" in mclass:
            mfill = "hollow"
        sub = _Ctx(
            m,
            MARKER,
            ctx.node,
            None,
            ctx.size,
            False,
            ANCHOR_START,
            BASE_ALPHABETIC,
            0.0,
            True,
            ctx.depth + 1,
            mfill,
        )
        if sub.depth > MAX_USE_DEPTH:
            return
        before = len(self.items)
        for child in mk:
            self.walk(child, sub)
        if edge >= 0:
            for i in range(before, len(self.items)):
                it = self.items[i]
                if isinstance(it, PathItem):
                    self.items[i] = PathItem(
                        it.role,
                        it.fill,
                        it.fill_rule,
                        it.stroke,
                        it.width,
                        it.join,
                        it.cap,
                        it.dash,
                        it.ops,
                        it.xy,
                        it.bbox,
                        it.node,
                        edge,
                    )

    def add_edge(self, eid: str, ops: str, xy: list[float]) -> int:
        if len(self.edges) >= MAX_EDGES:
            raise Refusal(f"more than {MAX_EDGES} edges")
        pts = flatten_units(ops, xy, 1.0)
        flat = [p for pl, _ in pts for q in pl for p in q]
        while len(flat) > 2 * MAX_HIT_POINTS:
            # Every other point, keeping the last: the ends are what attach the edge.
            last = flat[-2:]
            flat = [v for k, v in enumerate(flat) if (k // 2) % 2 == 0]
            if flat[-2:] != last:
                flat += last
        self.edge_index.setdefault(eid, len(self.edges))
        self.edges.append((eid, flat))
        return len(self.edges) - 1

    def add(self, item: PathItem | TextRun) -> None:
        if len(self.items) >= MAX_ITEMS:
            raise Refusal(f"more than {MAX_ITEMS} display items")
        if isinstance(item, PathItem):
            if len(item.ops) > MAX_SEGS_PER_PATH:
                raise Refusal(f"a path of more than {MAX_SEGS_PER_PATH} segments")
            if item.ops.count("M") > MAX_SUBPATHS_PER_PATH:
                raise Refusal(f"a path of more than {MAX_SUBPATHS_PER_PATH} subpaths")
            self.segs += len(item.ops)
            if self.segs > MAX_SEGS_TOTAL:
                raise Refusal(f"more than {MAX_SEGS_TOTAL} path segments")
            if item.node >= 0 and item.role not in (LIFELINE,):
                self.grow(item.node, item.bbox)
            if item.node >= 0:
                self.node_attach[item.node].append(item.bbox)
        else:
            self.runs += 1
            self.chars += len(item.text)
            if self.runs > MAX_TEXT_RUNS or self.chars > MAX_TEXT_CHARS:
                raise Refusal("too much text")
            if item.node >= 0:
                self.grow(item.node, (item.x, item.y, item.x, item.y))
        self.items.append(item)

    # -- text --

    def text(self, el: ET.Element, ctx: _Ctx, style: dict[str, str], classes: set[str]) -> None:
        a, b, c, d = ctx.m[:4]
        if abs(b) > 1e-6 or abs(c) > 1e-6 or a <= 0 or d <= 0:
            self.drop("transformed text")
            return
        role = ctx.role if ctx.role in (EDGE_LABEL,) else LABEL
        if "sequenceNumber" in classes:
            role = BADGE_TEXT
        x = _length(el.get("x")) or 0.0
        y = (_length(el.get("y")) or 0.0) + (_length(el.get("dy"), ctx.size) or 0.0)
        lines: list[tuple[str, float, float]] = []
        if el.text and el.text.strip():
            lines.append((el.text, x, y))
        for ts in el:
            if ts.tag.replace(SVG, "") != "tspan":
                continue
            tstyle = _style(ts)
            if "font-weight" in tstyle:
                pass
            if ts.get("x") is not None:
                x = _length(ts.get("x")) or 0.0
            if ts.get("y") is not None:
                y = _length(ts.get("y")) or 0.0
            y += _length(ts.get("dy"), ctx.size) or 0.0
            if ts.text and ts.text.strip():
                lines.append((ts.text, x, y))
        edge = self.edge_index.get(ctx.edge_label, -1) if ctx.edge_label else -1
        for s, tx, ty in lines:
            s = " ".join(s.split())
            if len(s) > MAX_RUN_CHARS:
                s = s[: MAX_RUN_CHARS - 1] + "…"
            px, py = apply(ctx.m, tx, ty)
            if not (COORD_MIN <= px <= COORD_MAX and COORD_MIN <= py <= COORD_MAX):
                raise Refusal("a coordinate is out of range")
            size = min(FONT_SIZE_MAX, max(FONT_SIZE_MIN, ctx.size * d))
            run = TextRun(
                s,
                px,
                py,
                size,
                ctx.anchor,
                ctx.baseline,
                role,
                ctx.bold,
                ctx.box * a,
                ctx.node,
                edge,
            )
            if ctx.edge_label and edge < 0:
                self.pending_labels.append((len(self.items), ctx.edge_label))
            self.add(run)

    def finish(self, width: float, height: float, origin: tuple[float, float]) -> DisplayList:
        # Edge labels drawn before their edge was seen.
        for i, eid in self.pending_labels:
            e = self.edge_index.get(eid, -1)
            it = self.items[i]
            if e >= 0 and isinstance(it, TextRun):
                self.items[i] = TextRun(
                    it.text,
                    it.x,
                    it.y,
                    it.size,
                    it.anchor,
                    it.baseline,
                    it.role,
                    it.bold,
                    it.box,
                    it.node,
                    e,
                )
        nodes = []
        for k, n in enumerate(self.nodes):
            b = self.node_boxes[k]
            box = (b[0], b[1], b[2], b[3]) if b[0] <= b[2] else (0.0, 0.0, 0.0, 0.0)
            nodes.append(HitNode(n.id, box, tuple(self.node_attach[k][:16])))
        edges = []
        for eid, pts in self.edges:
            ends = (
                (_end_node(nodes, pts[0], pts[1]), _end_node(nodes, pts[-2], pts[-1]))
                if pts
                else (-1, -1)
            )
            edges.append(HitEdge(eid, tuple(pts), ends))
        return DisplayList(
            width,
            height,
            origin,
            tuple(self.items),
            tuple(nodes),
            tuple(edges),
            {
                "segments": self.segs,
                "text_runs": self.runs,
                "chars": self.chars,
                "dropped": self.dropped,
            },
        )


ATTACH_SLOP = 24.0
"""
How far outside a node's shape an edge's end may stop and still be that node's:
merman stops an edge short of its node by the marker's margin, 17 units on a class
diagram's inheritance triangle (measured), so 16 attached none of them.
"""


def _end_node(nodes: list[HitNode], x: float, y: float) -> int:
    best, best_d = -1, ATTACH_SLOP
    for k, n in enumerate(nodes):
        for b in (n.box, *n.attach):
            dx = max(b[0] - x, 0.0, x - b[2])
            dy = max(b[1] - y, 0.0, y - b[3])
            dd = math.hypot(dx, dy)
            if dd < best_d or (
                dd == best_d == 0.0 and best >= 0 and _area(n.box) < _area(nodes[best].box)
            ):
                best, best_d = k, dd
    return best


def _area(b: tuple[float, float, float, float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _direction(xy: list[float], *, from_start: bool) -> tuple[float, float]:
    pts = [(xy[i], xy[i + 1]) for i in range(0, len(xy), 2)]
    if not from_start:
        pts = pts[::-1]
    p0 = pts[0]
    for q in pts[1:]:
        if abs(q[0] - p0[0]) + abs(q[1] - p0[1]) > 1e-9:
            return (q[0] - p0[0], q[1] - p0[1]) if from_start else (p0[0] - q[0], p0[1] - q[1])
    return (1.0, 0.0)


def _bbox(xy: list[float]) -> tuple[float, float, float, float]:
    xs, ys = xy[0::2], xy[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


def rect_path(
    x: float, y: float, w: float, h: float, rx: float | None, ry: float | None
) -> tuple[str, list[float]]:
    if w <= 0 or h <= 0:
        return "", []
    if rx is None and ry is None:
        rx = ry = 0.0
    rx = ry if rx is None else rx
    ry = rx if ry is None else ry
    rx, ry = min(abs(rx or 0.0), w / 2), min(abs(ry or 0.0), h / 2)
    if rx == 0 or ry == 0:
        return "MLLLZ", [x, y, x + w, y, x + w, y + h, x, y + h]
    k = 0.5522847498
    ops = "MLCLCLCLCZ"
    xy = [
        x + rx,
        y,
        x + w - rx,
        y,
        x + w - rx + k * rx,
        y,
        x + w,
        y + ry - k * ry,
        x + w,
        y + ry,
        x + w,
        y + h - ry,
        x + w,
        y + h - ry + k * ry,
        x + w - rx + k * rx,
        y + h,
        x + w - rx,
        y + h,
        x + rx,
        y + h,
        x + rx - k * rx,
        y + h,
        x,
        y + h - ry + k * ry,
        x,
        y + h - ry,
        x,
        y + ry,
        x,
        y + ry - k * ry,
        x + rx - k * rx,
        y,
        x + rx,
        y,
    ]
    return ops, xy


def ellipse_path(cx: float, cy: float, rx: float, ry: float) -> tuple[str, list[float]]:
    if rx <= 0 or ry <= 0:
        return "", []
    k = 0.5522847498
    return "MCCCCZ", [
        cx + rx,
        cy,
        cx + rx,
        cy + k * ry,
        cx + k * rx,
        cy + ry,
        cx,
        cy + ry,
        cx - k * rx,
        cy + ry,
        cx - rx,
        cy + k * ry,
        cx - rx,
        cy,
        cx - rx,
        cy - k * ry,
        cx - k * rx,
        cy - ry,
        cx,
        cy - ry,
        cx + k * rx,
        cy - ry,
        cx + rx,
        cy - k * ry,
        cx + rx,
        cy,
    ]


def flatten_units(
    ops: str, xy: list[float] | tuple[float, ...], scale: float, tol: float = FLATTEN_TOL_PX
) -> list[tuple[list[tuple[float, float]], bool]]:
    """
    Subpaths as polylines at ``scale`` pixels a unit, each with whether it was closed.
    Curves get Wang's step count for ``tol`` pixels, at most ``MAX_CURVE_STEPS``.
    """
    out: list[tuple[list[tuple[float, float]], bool]] = []
    cur: list[tuple[float, float]] = []
    k = 0
    x = y = 0.0
    total = 0
    for op in ops:
        if op == "M":
            if len(cur) > 1:
                out.append((cur, False))
            x, y = xy[k] * scale, xy[k + 1] * scale
            k += 2
            cur = [(x, y)]
        elif op == "L":
            x, y = xy[k] * scale, xy[k + 1] * scale
            k += 2
            cur.append((x, y))
        elif op == "C":
            x1, y1, x2, y2, x3, y3 = (v * scale for v in xy[k : k + 6])
            k += 6
            dd = max(
                math.hypot(x - 2 * x1 + x2, y - 2 * y1 + y2),
                math.hypot(x1 - 2 * x2 + x3, y1 - 2 * y2 + y3),
            )
            n = min(MAX_CURVE_STEPS, max(1, math.ceil(math.sqrt(0.75 * dd / tol))))
            for i in range(1, n + 1):
                t = i / n
                u = 1 - t
                cur.append(
                    (
                        u * u * u * x + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
                        u * u * u * y + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3,
                    )
                )
            x, y = x3, y3
        elif op == "Q":
            x1, y1, x2, y2 = (v * scale for v in xy[k : k + 4])
            k += 4
            dd = math.hypot(x - 2 * x1 + x2, y - 2 * y1 + y2)
            n = min(MAX_CURVE_STEPS, max(1, math.ceil(math.sqrt(0.25 * dd / tol))))
            for i in range(1, n + 1):
                t = i / n
                u = 1 - t
                cur.append(
                    (
                        u * u * x + 2 * u * t * x1 + t * t * x2,
                        u * u * y + 2 * u * t * y1 + t * t * y2,
                    )
                )
            x, y = x2, y2
        elif op == "Z":
            if len(cur) > 1:
                out.append((cur, True))
            if cur:
                x, y = cur[0]
            cur = [(x, y)]
        total += 1
        if sum(len(c) for c, _ in out) + len(cur) > MAX_POINTS_PER_ITEM:
            break
    if len(cur) > 1:
        out.append((cur, False))
    return out


def from_svg(svg: str) -> DisplayList:
    """Sanitised SVG to a display list, or ``Refusal``."""
    root = ET.fromstring(svg)
    vb = [float(v) for v in _NUM.findall(root.get("viewBox", ""))]
    w = _length(root.get("width"))
    h = _length(root.get("height"))
    if len(vb) != 4:
        if not (w and h):
            raise Refusal("no size")
        vb = [0.0, 0.0, w, h]
    w, h = w or vb[2], h or vb[3]
    if not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
        raise Refusal("natural size out of range")
    sx, sy = w / vb[2], h / vb[3]
    s = min(sx, sy)
    m: Affine = (s, 0, 0, s, -vb[0] * s, -vb[1] * s)
    b = _Builder(root)
    ctx = _Ctx(m, -1, -1, None, 14.0, False, ANCHOR_START, BASE_ALPHABETIC, 0.0, False, 0)
    ctx.role = FRAME
    for child in root:
        b.walk(child, ctx)
    dl = b.finish(w, h, (0.0, 0.0))
    bad = validate(dl)
    if bad:
        raise Refusal(f"display list out of bounds: {bad}")
    return dl


def child_layout(source: str) -> tuple[Any, dict[str, float]]:
    """
    In the render child: ``mermaid.layout`` (merman, post-pass, sanitiser), then
    ``from_svg``. Answers the display list, or a reason string, and where the time went.
    """
    sys.path.insert(0, str(ROOT))
    from pptmstr.figures import mermaid
    from pptmstr.figures.sanitise import Sanitised

    t0 = time.perf_counter()
    laid = mermaid.layout(source)
    t1 = time.perf_counter()
    if not isinstance(laid, Sanitised):
        return getattr(laid, "reason", "refused"), {"layout_ms": (t1 - t0) * 1e3}
    try:
        dl = from_svg(laid.svg)
    except Refusal as r:
        return str(r), {"layout_ms": (t1 - t0) * 1e3}
    t2 = time.perf_counter()
    import pickle

    size = len(pickle.dumps(dl))
    return dl, {
        "layout_ms": (t1 - t0) * 1e3,
        "convert_ms": (t2 - t1) * 1e3,
        "pickle_bytes": size,
        "svg_bytes": len(laid.svg),
    }


# ----------------------------------------------------------------------------------
# Drawing (GUI process)
# ----------------------------------------------------------------------------------

FILL_CONVEX, FILL_CONCAVE, STROKE, TEXT, KNOCKOUT = range(5)


def _clockwise(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    a = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        a += x0 * y1 - x1 * y0
    return pts if a >= 0 else pts[::-1]


def _convex(pts: list[tuple[float, float]]) -> bool:
    n = len(pts)
    if n < 4:
        return True
    sign = 0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        x2, y2 = pts[(i + 2) % n]
        cr = (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)
        if abs(cr) > 1e-9:
            s = 1 if cr > 0 else -1
            if sign and s != sign:
                return False
            sign = s
    return True


def _dedupe(pts: list[tuple[float, float]], closed: bool) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) > 1e-3:
            out.append(p)
    if (
        closed
        and len(out) > 1
        and abs(out[0][0] - out[-1][0]) + abs(out[0][1] - out[-1][1]) <= 1e-3
    ):
        out.pop()
    return out


def dashed(
    pts: list[tuple[float, float]], closed: bool, dash: tuple[float, ...]
) -> list[list[tuple[float, float]]] | None:
    """The polyline cut by ``dash`` (pixels), or ``None`` past ``MAX_DASH_PIECES``."""
    if closed:
        pts = pts + [pts[0]]
    pieces: list[list[tuple[float, float]]] = []
    k, left, on = 0, dash[0], True
    cur: list[tuple[float, float]] = [pts[0]]
    for i in range(1, len(pts)):
        (x0, y0), (x1, y1) = pts[i - 1], pts[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        at = 0.0
        while seg - at > left:
            at += left
            t = at / seg
            p = (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            if on:
                cur.append(p)
                pieces.append(cur)
                if len(pieces) > MAX_DASH_PIECES:
                    return None
            cur = [p]
            on = not on
            k = (k + 1) % len(dash)
            left = dash[k]
        left -= seg - at
        if on:
            cur.append((x1, y1))
    if on and len(cur) > 1:
        pieces.append(cur)
    return pieces


@dataclass(slots=True)
class Geometry:
    """
    A display list resolved at one scale and font: ImVec2 lists, in pixels from the
    figure's corner.
    """

    scale: float
    runs: list[tuple[bool, list[tuple[Any, ...]]]]
    """``(is_text, ops)`` in paint order; geometry runs are drawn at (0, 0) and moved."""
    count: int
    points: int
    build_ms: float
    concave: int
    multi_subpath_fills: int
    shrunk_labels: int
    overflow_labels: int


def _text_width(im: Any, face: Any, size: float, text: str) -> float:
    # ImFont.CalcTextSizeA is not bound in 1.92.9; the pushed font is.
    im.push_font(face, size)
    w = float(im.calc_text_size(text).x)
    im.pop_font()
    return w


LABEL_OVERFLOW = 1.15
"""
How far past merman's label box a label may run before it is set smaller. merman
measures with its own metrics, narrower than the pane's face; the raster path lets
labels overflow (``mermaid.visible_labels``), and shrinking every label that ran a
pixel over made sizes uneven from one box to the next.
"""


def build_geometry(
    dl: DisplayList, scale: float, font: Any, im: Any, em_ratio: float = 1.0, bold: Any = None
) -> Geometry:
    """
    ``em_ratio`` is the face's em over its ImGui size (ascent plus descent): SVG's
    ``font-size`` is an em, and ImGui sizes a font by its height, so a label set at
    the SVG's number would come out smaller than the raster draws it.
    """
    t0 = time.perf_counter()
    V = im.ImVec2
    runs: list[tuple[bool, list[tuple[Any, ...]]]] = []
    points = concave = multi = shrunk = overflow = count = 0
    ox, oy = dl.origin
    for it in dl.items:
        if isinstance(it, PathItem):
            ops_out: list[tuple[Any, ...]] = []
            xy = [v - (ox if i % 2 == 0 else oy) for i, v in enumerate(it.xy)]
            subs = flatten_units(it.ops, xy, scale)
            b = (
                it.bbox[0] * scale - 2,
                it.bbox[1] * scale - 2,
                it.bbox[2] * scale + 2,
                it.bbox[3] * scale + 2,
            )
            if it.fill:
                fills = [s for s in subs if len(s[0]) >= 3]
                if len(fills) > 1:
                    multi += 1
                for pl, _ in fills:
                    pts = _clockwise(_dedupe(pl, True))
                    if len(pts) < 3:
                        continue
                    kind = FILL_CONVEX if _convex(pts) else FILL_CONCAVE
                    concave += kind == FILL_CONCAVE
                    ops_out.append(
                        (kind, b, it.role, [V(p[0], p[1]) for p in pts], it.node, it.edge, 0.0)
                    )
                    points += len(pts)
            if it.stroke:
                w = max(1.0, it.width * scale)
                dash = tuple(d * scale for d in it.dash)
                for pl, closed in subs:
                    pts = _dedupe(pl, closed)
                    if len(pts) < 2:
                        continue
                    pieces = dashed(pts, closed, dash) if dash else None
                    if pieces is None:
                        ops_out.append(
                            (
                                STROKE,
                                b,
                                it.role,
                                [V(p[0], p[1]) for p in pts],
                                it.node,
                                it.edge,
                                w,
                                closed,
                            )
                        )
                        points += len(pts)
                    else:
                        for piece in pieces:
                            ops_out.append(
                                (
                                    STROKE,
                                    b,
                                    it.role,
                                    [V(p[0], p[1]) for p in piece],
                                    it.node,
                                    it.edge,
                                    w,
                                    False,
                                )
                            )
                            points += len(piece)
            if ops_out:
                count += len(ops_out)
                if runs and not runs[-1][0]:
                    runs[-1][1].extend(ops_out)
                else:
                    runs.append((False, ops_out))
        else:
            # Whole pixels: every distinct size is a bake on the atlas, so a size
            # that followed the scale continuously would bake one per resize frame.
            size = max(1.0, float(round(it.size * scale / em_ratio)))
            face = (bold or font) if it.bold else font

            tw = _text_width(im, face, size, it.text)
            if it.box > 0 and tw > it.box * scale + 0.5:
                overflow += 1
                if tw > it.box * scale * LABEL_OVERFLOW:
                    fit = it.box * scale * LABEL_OVERFLOW / tw
                    if fit >= 0.6:
                        size = max(1.0, float(math.floor(size * fit)))
                        tw = _text_width(im, face, size, it.text)
                        shrunk += 1
            x = (it.x - ox) * scale
            y = (it.y - oy) * scale
            if it.anchor == ANCHOR_MIDDLE:
                x -= tw / 2
            elif it.anchor == ANCHOR_END:
                x -= tw
            if it.baseline == BASE_CENTRAL:
                top = y - size / 2
            else:
                top = y - face.get_font_baked(size).ascent
            # Glyph quads at fractional positions lose one-pixel strokes (a hyphen
            # read as a dot, underscores went missing): imtypeset snaps for this.
            x, top = float(round(x)), float(round(top))
            b = (x, top, x + tw, top + size)
            ops_t: list[tuple[Any, ...]] = []
            if it.role == EDGE_LABEL:
                ops_t.append(
                    (
                        KNOCKOUT,
                        b,
                        it.role,
                        (x - 2, top - 1, x + tw + 2, top + size + 1),
                        it.node,
                        it.edge,
                    )
                )
            ops_t.append((TEXT, b, it.role, (x, top, size, it.text, it.bold), it.node, it.edge))
            count += len(ops_t)
            if runs and runs[-1][0]:
                runs[-1][1].extend(ops_t)
            else:
                runs.append((True, ops_t))
    return Geometry(
        scale,
        runs,
        count,
        points,
        (time.perf_counter() - t0) * 1e3,
        concave,
        multi,
        shrunk,
        overflow,
    )


def mix(a: Any, b: Any, t: float) -> int:
    from imgui_bundle import imgui as im

    va, vb = a.vec4, b.vec4
    return int(
        im.IM_COL32(
            int(255 * (va.x + (vb.x - va.x) * t)),
            int(255 * (va.y + (vb.y - va.y) * t)),
            int(255 * (va.z + (vb.z - va.z) * t)),
            255,
        )
    )


_INKS: dict[str, tuple[tuple[int, int], ...]] = {}


def inks(p: Any) -> tuple[tuple[int, int], ...]:
    """``(fill, stroke)`` per role for palette ``p``, made once per palette."""
    got = _INKS.get(p.name)
    if got is None:
        bg, text = p.bg, p.text
        got = _INKS[p.name] = (
            (mix(bg, p.accent, 0.14), mix(bg, text, 0.55)),  # node
            (mix(bg, text, 0.05), p.border.u32),  # cluster
            (mix(bg, text, 0.70), mix(bg, text, 0.70)),  # edge
            (mix(bg, text, 0.70), mix(bg, text, 0.70)),  # marker
            (p.text.u32, p.text.u32),  # label
            (p.bg.u32, p.text.u32),  # edge label: knockout, ink
            (mix(bg, p.warn, 0.16), mix(bg, p.warn, 0.75)),  # note
            (mix(bg, text, 0.08), p.text_dim.u32),  # frame
            (p.border.u32, mix(bg, text, 0.35)),  # lifeline
            (mix(bg, text, 0.80), mix(bg, text, 0.80)),  # solid
            (p.bg.u32, p.bg.u32),  # badge text
            (p.bg.u32, mix(bg, text, 0.70)),  # hollow marker: the pane's ground, edge ink
        )
    return got


_EM: list[float] = []


def _em_ratio() -> float:
    """The prose face's em per unit of ImGui size, asked of imscribe once."""
    if not _EM:
        try:
            import imscribe

            _EM.append(imscribe.em_size_for_pixel_height(imscribe.Face.PROSE, 100.0) / 100.0)
        except Exception:
            _EM.append(1.0)
    return _EM[0]


CLICKS: list[tuple[str, str]] = []


def on_node_click(sha: str, node_id: str) -> None:
    """The stub a click on a node reports to. What it should do is the operator's call."""
    CLICKS.append((sha, node_id))
    print(f"click: figure {sha[:10]} node {node_id}", flush=True)


@dataclass(slots=True)
class DrawStat:
    ms: float = 0.0
    drawn: int = 0
    total: int = 0
    vertices: int = 0
    rebuilt: bool = False
    hovered: str | None = None
    hovered_edges: int = 0


class _Native:
    """Display lists by fence sha, asked of a child of their own."""

    def __init__(self) -> None:
        self.held: dict[str, Any] = {}
        self.timing: dict[str, dict[str, float]] = {}
        self.pending: dict[str, Any] = {}
        self.asked_at: dict[str, float] = {}
        self.arrived_at: dict[str, float] = {}
        self.pool: Any = None

    def ask(self, sha: str, source: str) -> None:
        if sha in self.held or sha in self.pending:
            return
        if self.pool is None:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor

            self.pool = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"))
        self.asked_at[sha] = time.perf_counter()
        self.pending[sha] = self.pool.submit(child_layout, source)

    def pump(self) -> None:
        for sha, fut in list(self.pending.items()):
            if fut.done():
                del self.pending[sha]
                try:
                    got, timing = fut.result()
                except Exception as exc:  # the child died: shown as a failure
                    got, timing = f"child: {type(exc).__name__}", {}
                if isinstance(got, DisplayList):
                    t = time.perf_counter()
                    bad = validate(got)
                    timing["validate_ms"] = (time.perf_counter() - t) * 1e3
                    if bad:
                        got = f"out of bounds: {bad}"
                self.held[sha] = got
                self.timing[sha] = timing
                self.arrived_at[sha] = time.perf_counter()

    def shutdown(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(cancel_futures=True)
            self.pool = None


NATIVE = _Native()


def native_figure_class() -> Any:
    from pptmstr.figures import host

    class NativeFigure(host.FigureWidget):  # type: ignore[misc, valid-type]
        """A mermaid fence drawn from its display list: no card, no texture."""

        def __init__(self, figure: Any, text: str) -> None:
            super().__init__(figure, text)
            self.geo: Geometry | None = None
            self.geo_key: tuple[Any, ...] | None = None
            self.stat = DrawStat()
            self.heights: list[float] = []
            self.rebuilds = 0

        def _dl(self) -> DisplayList | str | None:
            got = NATIVE.held.get(self.mark.sha)
            if got is None:
                NATIVE.ask(self.mark.sha, self.mark.source)
            return got

        def size_for(self, width_px: float) -> tuple[float, float]:
            dl = self._dl()
            room = max(1.0, width_px)
            if not isinstance(dl, DisplayList):
                return room, host.PLACEHOLDER_HEIGHT
            w = max(1.0, min(room, dl.width))
            return w, w * dl.height / dl.width

        def height_for(self, width_px: float) -> float:
            dl = self._dl()
            if isinstance(dl, str):
                return 40.0
            h = self.size_for(width_px)[1]
            self.heights.append(h)
            return h

        def draw(self, *, palette: Any = None, width_px: float | None = None) -> object:
            from imgui_bundle import imgui as im
            from imgui_bundle.imgui import internal as imi

            from pptmstr import theme
            from pptmstr.ui import typeset

            t0 = time.perf_counter()
            st = self.stat = DrawStat()
            wide = float(width_px if width_px is not None else im.get_content_region_avail().x)
            at = im.get_cursor_screen_pos()
            dl = self._dl()
            w, h = self.size_for(wide)
            dlist = im.get_window_draw_list()
            if not isinstance(dl, DisplayList):
                col = theme.P.warn.u32 if isinstance(dl, str) else theme.P.border.u32
                dlist.add_rect(at, im.ImVec2(at.x + w, at.y + h), col)
                if isinstance(dl, str):
                    dlist.add_text(im.ImVec2(at.x + 4, at.y + 4), col, dl)
                st.ms = (time.perf_counter() - t0) * 1e3
                return True
            scale = w / dl.width
            font = typeset.imfont(typeset.Weight.REGULAR) or im.get_font()
            bold = typeset.imfont(typeset.Weight.BOLD) or font
            # font_id, not id(font): every call hands back a new Python wrapper, and
            # keying on its id rebuilt the geometry every frame.
            key = (round(scale, 4), int(font.font_id), int(bold.font_id))
            if self.geo is None or self.geo_key != key:
                self.geo = build_geometry(dl, scale, font, im, _em_ratio(), bold)
                self.geo_key = key
                self.rebuilds += 1
                st.rebuilt = True
            geo = self.geo
            ox, oy = float(round(at.x)), float(round(at.y))
            origin = im.ImVec2(ox, oy)
            # Hover and click through an item, so another window over it wins.
            im.push_id(self.mark.sha)
            im.set_cursor_screen_pos(im.ImVec2(ox, oy))
            im.invisible_button("dl", im.ImVec2(max(1.0, w), max(1.0, h)))
            hovered_node = -1
            if im.is_item_hovered():
                mp = im.get_mouse_pos()
                ux, uy = (mp.x - ox) / scale + dl.origin[0], (mp.y - oy) / scale + dl.origin[1]
                best = math.inf
                for k, n in enumerate(dl.nodes):
                    b = n.box
                    if b[0] <= ux <= b[2] and b[1] <= uy <= b[3] and _area(b) < best:
                        hovered_node, best = k, _area(b)
                if hovered_node >= 0 and im.is_item_clicked(0):
                    on_node_click(self.mark.sha, dl.nodes[hovered_node].id)
            im.pop_id()
            lit_edges = {
                k for k, e in enumerate(dl.edges) if hovered_node >= 0 and hovered_node in e.ends
            }
            if hovered_node >= 0:
                st.hovered = dl.nodes[hovered_node].id
                st.hovered_edges = len(lit_edges)
            # What is on screen, in the figure's pixels.
            cmin, cmax = dlist.get_clip_rect_min(), dlist.get_clip_rect_max()
            vx0, vy0 = max(cmin.x, ox) - ox, max(cmin.y, oy) - oy
            vx1, vy1 = min(cmax.x, ox + w) - ox, min(cmax.y, oy + h) - oy
            ink = inks(theme.P)
            hi = theme.P.focus.u32
            closed_flag = im.ImDrawFlags_.closed.value
            v_start = dlist.vtx_buffer.size()
            drawn = 0
            for is_text, ops in geo.runs:
                if is_text:
                    for op in ops:
                        b = op[1]
                        if b[2] < vx0 or b[0] > vx1 or b[3] < vy0 or b[1] > vy1:
                            continue
                        drawn += 1
                        if op[0] == KNOCKOUT:
                            r = op[3]
                            dlist.add_rect_filled(
                                im.ImVec2(ox + r[0], oy + r[1]),
                                im.ImVec2(ox + r[2], oy + r[3]),
                                ink[EDGE_LABEL][0],
                                2.0,
                            )
                        else:
                            x, top, size, text, is_bold = op[3]
                            dlist.add_text(
                                bold if is_bold else font,
                                size,
                                im.ImVec2(ox + x, oy + top),
                                ink[op[2]][1],
                                text,
                            )
                    continue
                v0 = dlist.vtx_buffer.size()
                for op in ops:
                    b = op[1]
                    if b[2] < vx0 or b[0] > vx1 or b[3] < vy0 or b[1] > vy1:
                        continue
                    drawn += 1
                    kind = op[0]
                    lit = (hovered_node >= 0 and op[4] == hovered_node) or (
                        op[5] >= 0 and op[5] in lit_edges
                    )
                    if kind == STROKE:
                        # 1.92.8 put thickness before flags and moved Closed to 1 << 9.
                        dlist.add_polyline(
                            op[3],
                            hi if lit else ink[op[2]][1],
                            op[6] + (1.5 if lit else 0.0),
                            closed_flag if op[7] else 0,
                        )
                    elif kind == FILL_CONVEX:
                        dlist.add_convex_poly_filled(op[3], ink[op[2]][0])
                    else:
                        dlist.add_concave_poly_filled(op[3], ink[op[2]][0])
                v1 = dlist.vtx_buffer.size()
                if v1 > v0:
                    imi.shade_verts_transform_pos(dlist, v0, v1, im.ImVec2(0, 0), 1.0, 0.0, origin)
            st.drawn, st.total = drawn, geo.count
            st.vertices = dlist.vtx_buffer.size() - v_start
            st.ms = (time.perf_counter() - t0) * 1e3
            return True

    return NativeFigure


# ----------------------------------------------------------------------------------
# Sources
# ----------------------------------------------------------------------------------


def diagrams() -> dict[str, str]:
    """The five families of ``figure_render_probe.py`` and the 21-node board graph."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import ast

    tree = ast.parse((ROOT / "scripts" / "figure_render_probe.py").read_text())
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "DIAGRAMS" for t in node.targets
        ):
            found = ast.literal_eval(node.value)
    import bundle193_probe

    found["board21"] = bundle193_probe.board_graph()
    return found


def fence(src: str) -> str:
    return f"```mermaid\n{src.rstrip()}\n```\n"


def hostile() -> dict[str, str]:
    out = {}
    out["chain-500"] = "flowchart TD\n" + "\n".join(f"    n{i} --> n{i + 1}" for i in range(500))
    out["chain-2000"] = "flowchart TD\n" + "\n".join(f"    n{i} --> n{i + 1}" for i in range(2000))
    out["fan-1000"] = "flowchart LR\n" + "\n".join(f"    hub --> n{i}" for i in range(1000))
    out["label-20k"] = "flowchart TD\n    A[" + "word " * 4000 + "] --> B"
    # Below merman's own limits: how the display list grows.
    for n in (100, 200, 400):
        out[f"chain-{n}"] = "flowchart TD\n" + "\n".join(f"    n{i} --> n{i + 1}" for i in range(n))
    for n in (200, 400):
        out[f"fan-{n}"] = "flowchart LR\n" + "\n".join(f"    hub --> n{i}" for i in range(n))
    import random

    rng = random.Random(1)
    for nodes, links in ((60, 120), (100, 250)):
        out[f"dense-{nodes}x{links}"] = "flowchart TD\n" + "\n".join(
            f"    n{rng.randrange(nodes)} --> n{rng.randrange(nodes)}" for _ in range(links)
        )
    return out


# ----------------------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------------------


def dump() -> int:
    """Each diagram through a real child: time, size, counts, refusals. No display."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    out: dict[str, Any] = {}
    srcs = {**diagrams(), **hostile()}
    with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn")) as pool:
        pool.submit(child_layout, srcs["flowchart"]).result()  # warm the child
        for name, src in srcs.items():
            times = []
            got: Any = None
            for _ in range(3 if not name.startswith(("chain", "fan", "label", "dense")) else 1):
                t = time.perf_counter()
                got, timing = pool.submit(child_layout, src).result(timeout=120)
                times.append(((time.perf_counter() - t) * 1e3, timing))
            rec: dict[str, Any] = {
                "round_trip_ms": [round(t, 1) for t, _ in times],
                "timing": {k: round(v, 2) for k, v in times[-1][1].items()},
            }
            if isinstance(got, DisplayList):
                paths = [i for i in got.items if isinstance(i, PathItem)]
                rec.update(
                    items=len(got.items),
                    natural=(round(got.width, 1), round(got.height, 1)),
                    paths=len(paths),
                    text_runs=sum(1 for i in got.items if isinstance(i, TextRun)),
                    segments=got.stats["segments"],
                    chars=got.stats["chars"],
                    dropped=got.stats["dropped"],
                    nodes=len(got.nodes),
                    edges=len(got.edges),
                    edges_with_both_ends=sum(1 for e in got.edges if min(e.ends) >= 0),
                    roles={
                        ROLE_NAMES[r]: sum(1 for p in paths if p.role == r)
                        for r in range(len(ROLE_NAMES))
                    },
                    multi_subpath_fills=sum(1 for p in paths if p.fill and p.ops.count("M") > 1),
                    evenodd=sum(1 for p in paths if p.fill_rule == EVENODD),
                    dashed=sum(1 for p in paths if p.dash),
                    node_ids=[n.id for n in got.nodes][:30],
                )
            else:
                rec["refused"] = got
            out[name] = rec
            print(name, json.dumps(rec)[:600], flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dump.json").write_text(json.dumps(out, indent=1))
    return 0


def summary(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    xs = sorted(xs)
    return {
        "n": len(xs),
        "median": round(statistics.median(xs), 3),
        "min": round(xs[0], 3),
        "max": round(xs[-1], 3),
        "p90": round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 3),
    }


def run() -> int:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    from figure_probe import grab, save
    from imgui_bundle import hello_imgui, imgui, immapp

    from pptmstr import theme
    from pptmstr.figures import host, render
    from pptmstr.ui import typeset

    OUT.mkdir(parents=True, exist_ok=True)
    Native = native_figure_class()
    srcs = diagrams()
    cache = typeset.ViewCache()
    results: dict[str, Any] = {}
    log: list[str] = []
    frames: list[dict[str, Any]] = []
    # Panes: (key, text, width, height, native). Drawn left to right.
    ui: dict[str, Any] = {
        "panes": [],
        "grab": None,
        "done": False,
        "mouse": None,
        "click": 0,
        "scroll": None,
        "native": False,
    }
    widgets: dict[tuple[str, str], Any] = {}  # (pane key, sha) -> widget, for reading their state
    raster_state: dict[str, Any] = {}

    orig_figure_for = host.figure_for

    def figure_for(view: Any, number: int, block: Any) -> Any:
        figure = getattr(block, "language", None)
        if not ui["native"] or not isinstance(figure, host.FigureMark):
            return orig_figure_for(view, number, block)
        was = view.hosted(number) if hasattr(view, "hosted") else None
        if isinstance(was, Native) and was.mark is figure:
            return was
        w = Native(figure, block.text)
        widgets[(ui["pane"], figure.sha)] = w
        return w

    host.figure_for = figure_for

    orig_raster = host.FigureWidget._raster

    def raster(self: Any, im: Any, natural: Any, w: float, scale: float) -> Any:
        got = orig_raster(self, im, natural, w, scale)
        wanted, _ = render.raster_size(natural, w, scale)
        raster_state[self.mark.sha] = got is not None and got.width == wanted
        return got

    host.FigureWidget._raster = raster  # type: ignore[method-assign]

    orig_draw = host.FigureWidget.draw
    raster_ms: list[float] = []

    def timed_draw(self: Any, **kw: Any) -> object:
        t = time.perf_counter()
        out = orig_draw(self, **kw)
        if type(self) is host.FigureWidget:
            raster_ms.append((time.perf_counter() - t) * 1e3)
        return out

    host.FigureWidget.draw = timed_draw  # type: ignore[method-assign]

    def natives() -> list[Any]:
        return [w for (pk, _), w in widgets.items() if any(p[0] == pk for p in ui["panes"])]

    def idle(k: int) -> Iterator[None]:
        for _ in range(k):
            yield None

    def until(cond: Callable[[], bool], timeout: float = 30.0) -> Iterator[None]:
        deadline = time.monotonic() + timeout
        yield None
        while not cond() and time.monotonic() < deadline:
            yield None

    def shot(name: str) -> Iterator[None]:
        ui["grab"] = name
        yield None
        while ui["grab"] is not None:
            yield None

    def show(panes: list[tuple[str, str, float, float, bool]]) -> Iterator[None]:
        old = [p[0] for p in ui["panes"]]
        ui["panes"] = []
        yield from idle(2)
        for k in old:
            cache.drop(k)
        ui["panes"] = panes
        yield None

    def all_ready() -> bool:
        ok = True
        rasters = False
        for pk, text, _w, _h, native in ui["panes"]:
            if native:
                ok &= all(w_.mark.sha in NATIVE.held for (k, _), w_ in widgets.items() if k == pk)
                ok &= any(k == pk for (k, _) in widgets)
            else:
                rasters |= "```mermaid" in text
        if rasters:
            ok &= bool(raster_state) and all(raster_state.values())
        return ok

    def frame_stats(f0: int) -> dict[str, Any]:
        fs = frames[f0:]
        return {
            "gui_ms": summary([f["gui_ms"] for f in fs]),
            "native_ms_per_frame": summary([sum(s.ms for s in f["native"]) for f in fs]),
            "native_ms_per_figure": summary([s.ms for f in fs for s in f["native"]]),
            "raster_ms_per_frame": summary([f["raster_ms"] for f in fs]),
            "raster_ms_per_figure": summary([x for f in fs for x in f["raster_each"]]),
            "figures_native": summary([float(len(f["native"])) for f in fs]),
            "figures_raster": summary([float(len(f["raster_each"])) for f in fs]),
            "vertices_native": summary([float(sum(s.vertices for s in f["native"])) for f in fs]),
            "items_drawn": summary([float(sum(s.drawn for s in f["native"])) for f in fs]),
            "items_total": summary([float(sum(s.total for s in f["native"])) for f in fs]),
            "render_ms": summary([f["render_ms"] for f in fs if f.get("render_ms") is not None]),
            "frame_ms": summary([f["frame_ms"] for f in fs if f.get("frame_ms")]),
            "rebuild_frames": sum(1 for f in fs if any(s.rebuilt for s in f["native"])),
        }

    def geometry_report() -> list[dict[str, Any]]:
        out = []
        for (pk, sha), w in widgets.items():
            if w.geo is None or not any(p[0] == pk for p in ui["panes"]):
                continue
            g = w.geo
            out.append(
                {
                    "pane": pk,
                    "scale": round(g.scale, 4),
                    "ops": g.count,
                    "points": g.points,
                    "build_ms": round(g.build_ms, 2),
                    "concave_fills": g.concave,
                    "multi_subpath_fills": g.multi_subpath_fills,
                    "labels_shrunk": g.shrunk_labels,
                    "labels_overflowing": g.overflow_labels,
                    "child": NATIVE.timing.get(sha),
                }
            )
        return out

    W1, H1 = 1590, 1390

    def steps() -> Iterator[None]:
        theme.set_theme("dark")
        theme.apply_style()
        yield from show([("warm", "Prose only, to load the atlas.\n", 800, 0, False)])
        yield from idle(20)

        # (a) fidelity, side by side, both themes ------------------------------------
        for th in ("dark", "light"):
            theme.set_theme(th)
            theme.apply_style()
            for name, src in srcs.items():
                doc = f"**{name}**\n\n" + fence(src)
                yield from show(
                    [(f"r-{name}-{th}", doc, 790, 0, False), (f"n-{name}-{th}", doc, 790, 0, True)]
                )
                raster_state.clear()
                yield from until(all_ready, 40)
                yield from idle(3)
                yield from shot(f"fid-{name}-{th}")
                results.setdefault("fidelity", []).append(
                    {"name": name, "theme": th, "geometry": geometry_report()}
                )
        theme.set_theme("dark")
        theme.apply_style()

        # (c) height before the first draw ---------------------------------------------
        for trial in range(3):
            nonce = f"h{trial}"
            src = srcs["flowchart"].replace("User submits form", f"User submits form {nonce}")
            doc = "Above.\n\n" + fence(src) + "\nBelow the figure.\n"
            yield from show([(f"h-{nonce}", doc, 790, 0, True)])
            f0 = len(frames)
            yield from until(lambda: any(w.geo is not None for w in natives()), 20)
            yield from idle(5)
            ws = natives()
            hist = [
                (i, round(f["native_h"][0], 1)) for i, f in enumerate(frames[f0:]) if f["native_h"]
            ]
            changes = [hist[0]] + [
                hist[i] for i in range(1, len(hist)) if hist[i][1] != hist[i - 1][1]
            ]
            sha = ws[0].mark.sha if ws else ""
            results.setdefault("height", []).append(
                {
                    "trial": trial,
                    "height_changes": changes,
                    "first_geometry_frame": next(
                        (i for i, f in enumerate(frames[f0:]) if any(s.total for s in f["native"])),
                        None,
                    ),
                    "child_round_trip_ms": round(
                        (NATIVE.arrived_at.get(sha, 0) - NATIVE.asked_at.get(sha, 0)) * 1e3, 1
                    ),
                    "child": NATIVE.timing.get(sha),
                }
            )

        # Hover and click on the 21-node graph ---------------------------------------
        doc = fence(srcs["board21"])
        yield from show([("hover", doc, 1500, 0, True)])
        yield from until(lambda: any(w.geo is not None for w in natives()), 20)
        yield from idle(3)
        w = natives()[0]
        dl = NATIVE.held[w.mark.sha]
        degree = [0] * len(dl.nodes)
        for e in dl.edges:
            for k in set(e.ends):
                if k >= 0:
                    degree[k] += 1
        k = max(range(len(dl.nodes)), key=lambda i: degree[i])
        b = dl.nodes[k].box
        ox, oy = frames[-1]["origin"][w.mark.sha]
        sc = w.geo.scale
        ui["mouse"] = (ox + (b[0] + b[2]) / 2 * sc, oy + (b[1] + b[3]) / 2 * sc)
        yield from idle(4)
        hov = [s for s in frames[-1]["native"] if s.hovered]
        yield from shot("hover-board21-dark")
        theme.set_theme("light")
        theme.apply_style()
        yield from idle(3)
        yield from shot("hover-board21-light")
        theme.set_theme("dark")
        theme.apply_style()
        ui["click"] = 1
        yield from idle(4)
        results["hover"] = {
            "node": dl.nodes[k].id,
            "degree": degree[k],
            "edges_with_both_ends": sum(1 for e in dl.edges if min(e.ends) >= 0),
            "edges": len(dl.edges),
            "hovered": hov[0].hovered if hov else None,
            "hovered_edges": hov[0].hovered_edges if hov else 0,
            "clicks": list(CLICKS),
        }
        ui["mouse"] = (5.0, 5.0)
        yield from idle(2)

        # Culling: the graph in a short pane, scrolled -------------------------------
        yield from show([("cull", doc, 1500, 420, True)])
        yield from until(lambda: any(w.geo is not None for w in natives()), 20)
        yield from idle(3)
        cull: dict[str, Any] = {}
        for at in (0.0, 0.5, 1.0):
            ui["scroll"] = at
            yield from idle(3)
            s = frames[-1]["native"]
            cull[str(at)] = {
                "drawn": sum(x.drawn for x in s),
                "total": sum(x.total for x in s),
                "vertices": sum(x.vertices for x in s),
                "ms": round(sum(x.ms for x in s), 3),
            }
            if at == 0.5:
                yield from shot("cull-board21-mid")
        ui["scroll"] = None
        results["cull"] = cull

        # (b) per-frame cost --------------------------------------------------------
        fam = [n for n in srcs if n != "board21"]

        def ten(native: bool, tag: str) -> list[tuple[str, str, float, float, bool]]:
            panes: list[tuple[str, str, float, float, bool]] = []
            for n in fam:
                a = srcs[n]
                b2 = srcs[n] + "\n"  # a second, distinct fence of the same family
                b2 = b2.replace("\n", "\n    %% copy\n", 1) if n != "er" else b2 + "    %% copy\n"
                panes.append((f"c-{tag}-{n}", fence(a) + "\n" + fence(b2), 310, 0, native))
            return panes

        for native, tag in ((False, "raster"), (True, "native")):
            yield from show([("k0", "Nothing.\n", 300, 0, native)])
            yield from idle(10)
            f0 = len(frames)
            yield from idle(150)
            results.setdefault("cost", []).append({"label": f"{tag}-k0", **frame_stats(f0 + 10)})
            raster_state.clear()
            yield from show(ten(native, tag))
            yield from until(all_ready, 60)
            yield from idle(10)
            if native:
                yield from shot("cost-k10-native")
            else:
                yield from shot("cost-k10-raster")
            f0 = len(frames)
            yield from idle(150)
            results.setdefault("cost", []).append(
                {
                    "label": f"{tag}-k10",
                    **frame_stats(f0 + 10),
                    "geometry": geometry_report() if native else None,
                }
            )
            raster_state.clear()
            yield from show([(f"b21-{tag}", doc, 1500, 0, native)])
            yield from until(all_ready, 60)
            yield from idle(10)
            f0 = len(frames)
            yield from idle(150)
            results.setdefault("cost", []).append(
                {
                    "label": f"{tag}-board21",
                    **frame_stats(f0 + 10),
                    "geometry": geometry_report() if native else None,
                }
            )

        # (d) resize ---------------------------------------------------------------
        five = "".join(fence(srcs[n]) + "\n" for n in fam)
        yield from show([("resize", five, 900, 0, True)])
        yield from until(all_ready, 60)
        yield from idle(5)
        for n in (1, 30):
            for a, b3 in ((900.0, 300.0), (300.0, 760.0), (760.0, 900.0)):
                f0 = len(frames)
                for i in range(1, n + 1):
                    p = ui["panes"][0]
                    ui["panes"] = [(p[0], p[1], a + (b3 - a) * i / n, p[3], p[4])]
                    yield None
                    if n == 30 and i == n and a == 900.0:
                        yield from shot("resize-300-native")
                yield from idle(3)
                fs = frames[f0:]
                results.setdefault("resize", []).append(
                    {
                        "from": a,
                        "to": b3,
                        "drag_frames": n,
                        "frames_not_sharp": sum(
                            1 for f in fs if any(s.total == 0 for s in f["native"])
                        ),
                        "rebuild_frames": sum(1 for f in fs if any(s.rebuilt for s in f["native"])),
                        "native_ms_per_frame": summary(
                            [sum(s.ms for s in f["native"]) for f in fs]
                        ),
                        "native_ms_rebuild_frames": summary(
                            [
                                sum(s.ms for s in f["native"])
                                for f in fs
                                if any(s.rebuilt for s in f["native"])
                            ]
                        ),
                        "gui_ms": summary([f["gui_ms"] for f in fs]),
                    }
                )

        # (d) theme switch ------------------------------------------------------------
        p = ui["panes"][0]
        ui["panes"] = [(p[0], p[1], 900.0, p[3], p[4])]
        yield from idle(5)
        for i in range(5):
            for name in ("light", "dark"):
                f0 = len(frames)
                t = time.perf_counter()
                theme.set_theme(name)
                theme.apply_style()
                apply_ms = (time.perf_counter() - t) * 1e3
                yield from idle(6)
                fs = frames[f0:]
                results.setdefault("theme", []).append(
                    {
                        "to": name,
                        "apply_ms": round(apply_ms, 2),
                        "rebuild_frames": sum(1 for f in fs if any(s.rebuilt for s in f["native"])),
                        "native_ms": [round(sum(s.ms for s in f["native"]), 3) for f in fs],
                        "frames_without_geometry": sum(
                            1 for f in fs if any(s.total == 0 for s in f["native"])
                        ),
                    }
                )
                if i == 0:
                    yield from shot(f"theme-five-{name}")
        theme.set_theme("dark")
        theme.apply_style()

    run_it = steps()
    last_t: list[float] = []

    def gui() -> None:
        t = time.perf_counter()
        frame: dict[str, Any] = {
            "t": t,
            "frame_ms": (t - last_t[0]) * 1e3 if last_t else None,
            "native": [],
            "raster_each": [],
            "native_h": [],
            "origin": {},
        }
        frames.append(frame)
        last_t[:] = [t]
        r0 = len(raster_ms)
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(vp.work_pos)
        imgui.set_next_window_size(vp.work_size)
        flags = imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_move.value
        imgui.begin("displaylist", None, flags)
        for i, (pk, text, w, h, native) in enumerate(ui["panes"]):
            if i:
                imgui.same_line()
            imgui.begin_child(pk, imgui.ImVec2(w, h))
            if ui["scroll"] is not None and h:
                imgui.set_scroll_y(ui["scroll"] * imgui.get_scroll_max_y())
            ui["native"], ui["pane"] = native, pk
            typeset.markdown(cache, pk, text)
            imgui.end_child()
        imgui.end()
        ui["native"] = False
        for (pk, _sha), wdg in list(widgets.items()):
            if not any(p[0] == pk for p in ui["panes"]):
                continue
            if wdg.stat.ms or wdg.stat.total:
                frame["native"].append(wdg.stat)
                if wdg.heights:
                    frame["native_h"].append(wdg.heights[-1])
            wdg.stat = DrawStat()
        frame["raster_each"] = raster_ms[r0:]
        frame["raster_ms"] = sum(raster_ms[r0:])
        frame["gui_ms"] = (time.perf_counter() - t) * 1e3
        frame["t_end"] = time.perf_counter()

    def pre_frame() -> None:
        typeset.pre_frame()
        host.pump()
        NATIVE.pump()
        io = imgui.get_io()
        if ui["mouse"] is not None:
            io.add_mouse_pos_event(*ui["mouse"])
        if ui["click"] == 1:
            io.add_mouse_button_event(0, True)
            ui["click"] = 2
        elif ui["click"] == 2:
            io.add_mouse_button_event(0, False)
            ui["click"] = 0
        if ui["done"]:
            hello_imgui.get_runner_params().app_shall_exit = True
            return
        if ui["grab"] is None:
            try:
                next(run_it)
            except StopIteration:
                ui["done"] = True

    def before_swap() -> None:
        if frames:
            frames[-1]["render_ms"] = (time.perf_counter() - frames[-1]["t_end"]) * 1e3
        name = ui["grab"]
        if name is None:
            return
        save(OUT / f"{name}.png", grab())
        ui["grab"] = None

    # The origin each native figure drew at, for aiming the mouse.
    NativeDraw = Native.draw

    def draw_and_note(self: Any, **kw: Any) -> object:
        at = imgui.get_cursor_screen_pos()
        if frames:
            frames[-1]["origin"][self.mark.sha] = (float(round(at.x)), float(round(at.y)))
        return NativeDraw(self, **kw)

    Native.draw = draw_and_note  # type: ignore[method-assign]

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "displaylist"
    params.app_window_params.window_geometry.size = (W1, H1)
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.ini_disable = True
    params.fps_idling.enable_idling = False
    params.callbacks.load_additional_fonts = theme.load_fonts
    params.callbacks.pre_new_frame = pre_frame
    params.callbacks.show_gui = gui
    params.callbacks.before_swap = before_swap
    theme.set_theme("dark")
    typeset.configure(typeset=True, mono=False)
    try:
        immapp.run(params)
    finally:
        host.release_all()
        render.shutdown()
        NATIVE.shutdown()
    results["frames"] = len(frames)
    results["ok"] = ui["done"]
    (OUT / "results.json").write_text(json.dumps(results, indent=1, default=str))
    print("\n".join(log))
    print(json.dumps(results, default=str)[:4000])
    return 0 if ui["done"] else 1


# ----------------------------------------------------------------------------------
# Self-test: the parts that are easy to get subtly wrong
# ----------------------------------------------------------------------------------


def selftest() -> int:
    def close(a: list[float] | tuple[float, ...], b: list[float], eps: float = 1e-6) -> bool:
        return len(a) == len(b) and all(abs(x - y) <= eps for x, y in zip(a, b, strict=True))

    # Relative, implicit lineto after M, H/V, S reflection, Z returns to start.
    ops, xy = parse_path("m10 10 20 0 v5 h-5 z l1 1")
    assert ops == "MLLLZL", ops
    assert close(xy, [10, 10, 30, 10, 30, 15, 25, 15, 11, 11]), xy
    ops, xy = parse_path("M0 0 C 0 10 10 10 10 0 S 20 -10 20 0")
    assert ops == "MCC" and close(xy[8:10], [10, -10]), xy
    # Numbers run together: "1.5.5" is 1.5 then .5; exponents.
    ops, xy = parse_path("M1.5.5L1e1-2")
    assert close(xy, [1.5, 0.5, 10, -2]), xy
    # Arc with fused flags; a half circle ends where it says and bulges the right way.
    ops, xy = parse_path("M0 0a5 5 0 0110 0")
    assert ops in ("MCC", "MC"), ops
    assert close(xy[-2:], [10, 0]), xy
    pts = flatten_units(ops, xy, 1.0)[0][0]
    assert min(p[1] for p in pts) < -4.9, "sweep 1 from (0,0) to (10,0) bulges to -y"
    # Malformed: stops, keeps what parsed.
    ops, xy = parse_path("M0 0 L 5 5 L 3")
    assert ops == "ML", ops
    # Transforms: translate then scale applies scale first to the point.
    m = parse_transform("translate(10,20) scale(2)")
    assert close(apply(m, 1, 1), [12, 22])
    m = parse_transform("rotate(90 10 10)")
    assert close(apply(m, 20, 10), [10, 20], 1e-9)
    # Rounded rect is closed and inside its box.
    ops, xy = rect_path(0, 0, 10, 4, 2, None)
    assert ops.endswith("Z") and min(xy) >= 0 and max(xy[0::2]) <= 10 and max(xy[1::2]) <= 4
    # Winding: clockwise on screen (y down) is a positive shoelace sum.
    ccw = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    assert _clockwise(ccw) == ccw[::-1]
    assert _convex([(0, 0), (2, 0), (2, 2), (0, 2)]) and not _convex(
        [(0, 0), (2, 0), (1, 1), (2, 2), (0, 2)]
    )
    # Dashes: a 10 px line, 2 on 3 off, gives 2 dashes of 2 px (0-2, 5-7).
    pieces = dashed([(0.0, 0.0), (10.0, 0.0)], False, (2.0, 3.0))
    assert pieces is not None and [(p[0][0], p[-1][0]) for p in pieces] == [
        (0.0, 2.0),
        (5.0, 7.0),
    ], pieces
    # A marker lands at the path end, turned along it.
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" '
        'viewBox="0 0 100 100"><defs><marker id="a" refX="10" refY="5" markerWidth="10" '
        'markerHeight="10" viewBox="0 0 10 10" markerUnits="userSpaceOnUse" orient="auto">'
        '<path d="M0 0 L10 5 L0 10 z"/></marker></defs>'
        '<g class="node default" id="merman-n-A" transform="translate(10,10)">'
        '<rect width="20" height="20"/></g>'
        '<g class="node default" id="merman-n-B" transform="translate(10,70)">'
        '<rect width="20" height="20"/></g>'
        '<path class="flowchart-link" data-id="L_A_B" d="M20 30 L20 70" marker-end="url(#a)"/>'
        '<text x="50" y="50" text-anchor="middle" dominant-baseline="central" '
        'style="font-size:12px">hi</text></svg>'
    )
    dl = from_svg(svg)
    marker = [i for i in dl.items if isinstance(i, PathItem) and i.role == MARKER][0]
    tip = max(range(0, len(marker.xy), 2), key=lambda i: marker.xy[i + 1])
    assert close(marker.xy[tip : tip + 2], [20, 70], 1e-6), marker.xy
    assert marker.edge == 0
    assert [n.id for n in dl.nodes] == ["n-A", "n-B"] and dl.edges[0].ends == (0, 1), dl.edges
    t = [i for i in dl.items if isinstance(i, TextRun)][0]
    assert (t.text, t.anchor, t.baseline, t.size) == ("hi", ANCHOR_MIDDLE, BASE_CENTRAL, 12.0)
    # Bounds: a coordinate out of range refuses; a long run is cut, not refused.
    try:
        from_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            '<path d="M0 0 L1e9 0"/></svg>'
        )
        raise AssertionError("out-of-range coordinate passed")
    except Refusal:
        pass
    long = from_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text x="0" y="5">'
        + "x" * 5000
        + "</text></svg>"
    )
    assert len(long.items[0].text) == MAX_RUN_CHARS  # type: ignore[union-attr]
    many_nodes = "".join(
        f'<g class="node" id="merman-n{i}"><rect width="1" height="1"/></g>'
        for i in range(MAX_NODES + 1)
    )
    try:
        from_svg(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">{many_nodes}</svg>'
        )
        raise AssertionError("node cap passed")
    except Refusal:
        pass
    # validate catches what a broken child might send.
    bad = DisplayList(
        10,
        10,
        (0, 0),
        (PathItem(NODE, True, 0, True, 999.0, 0, 0, (), "ML", (0, 0, 1, 1), (0, 0, 1, 1)),),
        (),
        (),
    )
    assert validate(bad) == "path"
    print("selftest: ok")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    sys.exit({"selftest": selftest, "dump": dump, "run": run}[mode]())
