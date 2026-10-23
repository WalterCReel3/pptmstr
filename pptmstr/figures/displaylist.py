"""
A Mermaid diagram as a bounded display list: paths, text runs and hit regions that
the pane draws natively (``host.FigureWidget``), with no raster.

**Where it is made.** In the render child (``render._laid``), after merman, the
post-pass and the sanitiser (``mermaid.layout``): ``from_mermaid`` parses the
sanitised SVG into a ``DisplayList`` and the child sends that, not the SVG and not
pixels, as plain JSON a record a line (``encode``). The GUI process never parses
SVG or a diagram's source, and never unpickles what the child sends: it reads the
list as data and checks every bound again on arrival (``decode``, ``validate``, on
the render I/O thread), because the receiving end does not trust the sending one.
Nothing here imports ImGui, so it is testable on its own.

**Why the SVG and not merman's layout API.** merman 0.9.1's ``layout_json`` has node
boxes and edge points, but no shapes, text runs, markers, or sequence lifelines and
frames. Drawing from it would mean writing merman's SVG renderer again for each
family (``notes/vector-rendering/probe-displaylist.md`` §1).

**The parser does not run CSS.** It reads structure (``g.node[id]``,
``[data-et=participant]``, ``path[data-edge]``, marker references) and a fixed
table of merman 0.9.1's class names for dashes and painted lines (``_CLASS_DASH``,
``_CSS_PAINTED``). The one thing it reads from the stylesheet is ``stroke-width`` in
rules whose selector is the root id and one class or one element name
(``_Sheet``), and only to scale markers drawn in stroke-width units: merman's
sequence-number badge rides on a line whose attribute says width 0 and whose
stylesheet says 2. A merman upgrade can move any of these names, which is why
``tests/test_figure_displaylist.py`` pins the output to merman 0.9.1.

**Colours: theme roles, except where the author wrote one** (operator decision,
2026-10-22, planning record §11). Every path and text run carries a role (node,
edge, note, ...), and the pane colours a role from the current theme. A colour the
diagram's own ``style``, ``classDef`` or ``linkStyle`` lines set is carried as a
literal beside the role (``fill_rgb``, ``stroke_rgb``, ``ink_rgb``). Which colours
are the author's is decided from the source, not the SVG (``authored_colours``):
merman writes its own palette (the pastel participant and entity fills, the
default ink) into the same SVG, inline and in its stylesheet, and nothing in the
markup tells the two apart by value. So a colour is the author's only where both
hold: an inline ``style`` declaration on a node's, cluster's or edge's shape, or
on a label's text, sets it, and a statement that names that shape sets that
colour for that property (fill, stroke, or ``color`` for text): a ``classDef``
whose name merman wrote on the node's ``g``, a ``style`` naming the node's id, a
``linkStyle`` naming the edge's number (``Authored``). merman's palette is never
in the source, so it maps to roles; a statement naming one node does not claim
the same value merman wrote on another; and a diagram with no style lines
follows the theme entirely. A colour written translucent is the theme's
(``opacity``).

**Legibility is the pane's job, not the child's** (``legible``): the pane's
background is the current theme's, which the child does not know. The rule and
its thresholds are with ``TEXT_CONTRAST``.
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

# ----------------------------------------------------------------------------------
# The schema and its bounds
# ----------------------------------------------------------------------------------

MAX_SIDE = 16_384.0
"""
The largest natural width or height, in diagram units: the sanitiser's ``MAX_SIDE``.
"""
COORD_MIN, COORD_MAX = -MAX_SIDE, 2.0 * MAX_SIDE
"""
Every coordinate after transforms, in diagram units. Outside it, refused.
"""
MAX_ITEMS = 10_000
"""
Paths plus text runs, marker instances included.
"""
MAX_SEGS_PER_PATH = 4_096
MAX_SEGS_TOTAL = 16_384
"""
Path segments in the whole list. The largest diagrams merman 0.9.1 lays out within
its own limits and the sanitiser's carry at most 6,488 (an entity-relationship
diagram of 42 entities, refused at 43 by the sanitiser's element cap; the largest
flowchart, class, state and sequence diagrams 3,550, 3,472, 4,123 and 1,104;
2026-10-22, ``shots/native-cost/merman_max.py``), so this is two and a half times
that. ``MAX_DRAWN_POINTS`` is what bounds a build; this bounds the wire and the
parse.
"""
MAX_SUBPATHS_PER_PATH = 256
MAX_TEXT_RUNS = 3_072
"""
The most runs merman 0.9.1 makes within its own limits and the sanitiser's is 1,473:
a pie of 1,472 slices, whose legend is a run a slice, refused at 1,536 by the
coordinate bound. A wrapped-label flowchart reaches 1,172 and a timeline 816
(2026-10-22, ``shots/native-cost/text_max.py``). This is twice the pie. Text costs
the build about 2 us a run and 0.04 us a character, so at the caps runs and
characters are each about 10 ms of the worst passing list.
"""
MAX_RUN_CHARS = 512
"""
A longer run is cut, with an ellipsis: a bound on a string, not a refusal.
"""
MAX_TEXT_CHARS = 200_000
"""
Not lowered toward what short labels make: characters follow label length, which
only the sanitiser's ``MAX_INPUT_BYTES`` bounds. A pie of 384 slices with 500-
character labels is 194,083 characters and one of 400 is refused here, so real
diagrams already meet this cap. Halving it would save about 4 ms of the worst
list's build (``shots/native-cost/test_text_cost.py``).
"""
FONT_SIZE_MIN, FONT_SIZE_MAX = 1.0, 256.0
STROKE_MAX = 64.0
MAX_DASHES = 8
DASH_MIN, DASH_MAX = 0.5, 4_096.0
MAX_NODES = 2_000
MAX_EDGES = 5_000
MAX_HIT_POINTS = 256
"""
Points an edge's hit line keeps, thinned to fit. The longest a real diagram made is
145 (a 105-node flowchart's longest edge), so this already holds it with headroom,
and is not lowered further: a lower cap would thin real edges' lines and change
the lists merman's diagrams make, for a saving only on the wire.
"""
MAX_ATTACH = 16
MAX_ID_CHARS = 128
MAX_USE_DEPTH = 4
"""
``<use>`` inside ``<use>``, and a marker's content: the sanitiser caps the expanded
tree; this caps the walk.
"""
MAX_STATS = 16
_UNDRAWABLE = re.compile(r"[\x00-\x1f\ud800-\udfff]")
"""
What a run's text or an id may not hold: a lone surrogate, on which every ImGui text
call raises, or a C0 control, NUL among them, at which ImGui stops reading (STYLE.md
§3, ``pptmstr.drawable``). Parsed XML holds neither but tab, newline and CR, so the
parser writes those as spaces (``_drawn``) and a list that holds one did not come
from it.
"""
NO_COLOUR = -1
"""
``fill_rgb``, ``stroke_rgb``, ``ink_rgb`` or ``ground_rgb`` where the role decides.
"""

# Drawing bounds, used by the pane.
FLATTEN_TOL_PX = 0.25
MAX_CURVE_STEPS = 64
MAX_POINTS_PER_ITEM = 16_384
MAX_DASH_PIECES = 2_048
"""
A dashed path that would split into more pieces is drawn solid.
"""
MAX_DRAWN_POINTS = 32_768
"""
The most points the pane may flatten a list's paths into at any width it draws it
(``drawn_points``), checked by ``validate`` on the I/O thread: a list past it is
refused as too costly to draw, before the frame thread builds anything from it.

A build costs about 0.9 us a point for a stroked curve and 1.8 us for a filled
shape (``host.build``, 2026-10-22, ``shots/native-cost/test_build_cost.py``). The
largest diagrams merman lays out need at most 17,777 (an entity-relationship
diagram of 42 entities; a 95-class diagram 10,592, a 155-state one 7,170, a
105-node flowchart 6,268), so this is 1.8 times that. A frame budget of 16 ms
would have refused that diagram, which takes 25 ms to build: the frame budget is
not one merman's own output keeps, and a first build waits its turn, one figure a
frame (``host._may_rebuild``). What a list at this bound costs to build, with its
dashes and text at theirs, is in the planning record (§11.3).
"""

# Roles. A role names what a primitive is, and the theme picks its colour.
(
    NODE,
    CLUSTER,
    EDGE,
    MARKER,
    LABEL,
    EDGE_LABEL,
    NOTE,
    FRAME,
    LIFELINE,
    SOLID,
    BADGE_TEXT,
    HOLLOW,
) = range(12)
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
_OP_ARGS = {"M": 2, "L": 2, "C": 6, "Q": 4, "Z": 0}

Box = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class PathItem:
    """
    One path. ``ops`` is one character per segment (``M L C Q Z``) and ``xy`` the
    absolute coordinates they consume (2, 2, 6, 4, 0), in diagram units. ``node`` and
    ``edge`` are the indices of what it belongs to, for highlighting, or -1.
    ``fill_rgb`` and ``stroke_rgb`` are the author's colour as ``0xRRGGBB``, or
    ``NO_COLOUR`` where the role decides.
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
    bbox: Box
    node: int = -1
    edge: int = -1
    fill_rgb: int = NO_COLOUR
    stroke_rgb: int = NO_COLOUR


@dataclass(frozen=True, slots=True)
class TextRun:
    """
    One axis-aligned line of text at ``(x, y)``: ``anchor`` says which end or the
    middle is at ``x``, ``baseline`` whether ``y`` is the alphabetic baseline or the
    middle. ``size`` is the ImGui font size (ascent to descent) that sets the run at
    the em merman laid it out for, at scale 1. ``box`` is the width merman laid the
    label out in, or 0. ``ink_rgb`` is the author's text colour and ``ground_rgb``
    the author's fill behind it, each ``0xRRGGBB`` or ``NO_COLOUR``.
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
    ink_rgb: int = NO_COLOUR
    ground_rgb: int = NO_COLOUR


@dataclass(frozen=True, slots=True)
class HitNode:
    """
    Something an edge can end on and the pointer can rest on: a node, a participant,
    a subgraph or a composite state. ``box`` is its whole extent, shapes and labels:
    what an edge's end is matched to, so a sequence participant's box runs from its
    top actor to its bottom one, over the lifeline its messages end on. ``attach``
    are its shapes' own boxes, which are what the pointer rests on (``node_at``):
    hovering that participant's column between the two actors is not hovering it.
    """

    id: str
    box: Box
    attach: tuple[Box, ...]


@dataclass(frozen=True, slots=True)
class HitEdge:
    """
    An edge's flattened centre line, and the nodes at each end, or -1.
    """

    id: str
    points: tuple[float, ...]
    ends: tuple[int, int]


@dataclass(frozen=True, slots=True)
class DisplayList:
    """
    A diagram: its natural size, what to draw in paint order, and what to hit-test.
    ``origin`` is the diagram unit at the figure's top-left corner.
    """

    width: float
    height: float
    origin: tuple[float, float]
    items: tuple[PathItem | TextRun, ...]
    nodes: tuple[HitNode, ...]
    edges: tuple[HitEdge, ...]
    stats: tuple[tuple[str, int], ...] = field(default=())

    def weight(self) -> int:
        """
        About how many numbers and four-character runs of text this holds: what the
        pane's held layouts are bounded by (``host.LAYOUT_BUDGET_WORDS``). Held as
        Python objects, a word costs ``WORD_BYTES``.
        """
        words = 8 * (len(self.items) + len(self.nodes) + len(self.edges))
        for item in self.items:
            words += len(item.xy) if isinstance(item, PathItem) else len(item.text) // 4
        for edge in self.edges:
            words += len(edge.points)
        return words


WORD_BYTES = 40
"""
What one word of ``DisplayList.weight`` costs held, in bytes: a float in a tuple is
a 24-byte object and an 8-byte slot, and the records around them add the rest.
tracemalloc measured 34-43 bytes a word on the six golden diagrams, a 100-node
flowchart and a list at every bound (2026-10-22, merman 0.9.1, CPython 3.11).
"""


class Refusal(Exception):
    """
    A diagram the display list will not carry, and why.
    """


def validate(dl: object) -> str | None:
    """
    Every bound and every type checked again on the GUI's side of the pipe: ``None``
    or the first thing wrong. The child already refuses past these; this is the
    receiving end not trusting the sending one, so a malformed list never reaches
    the draw loop, whose indexing trusts ``ops`` and ``xy`` to agree.
    """
    try:
        return _validate(dl)
    except Exception as exc:  # a wrong type anywhere: malformed, not a crash
        return f"malformed ({type(exc).__name__})"


def _validate(dl: object) -> str | None:
    if not isinstance(dl, DisplayList):
        return "not a display list"
    fin = math.isfinite
    if not (0 < dl.width <= MAX_SIDE and 0 < dl.height <= MAX_SIDE):
        return "size"
    if not all(fin(v) and COORD_MIN <= v <= COORD_MAX for v in dl.origin) or len(dl.origin) != 2:
        return "origin"
    if len(dl.items) > MAX_ITEMS or len(dl.nodes) > MAX_NODES or len(dl.edges) > MAX_EDGES:
        return "count"
    if len(dl.stats) > MAX_STATS or not all(
        len(s) == 2
        and isinstance(s[0], str)
        and len(s[0]) <= MAX_ID_CHARS
        and not _UNDRAWABLE.search(s[0])
        and type(s[1]) is int
        for s in dl.stats
    ):
        return "stats"
    nodes, edges = len(dl.nodes), len(dl.edges)
    segs = chars = runs = 0

    def colour(v: int) -> bool:
        return isinstance(v, int) and NO_COLOUR <= v <= 0xFFFFFF

    def coords(vs: Sequence[float]) -> bool:
        return all(
            isinstance(v, float | int) and fin(v) and COORD_MIN <= v <= COORD_MAX for v in vs
        )

    for it in dl.items:
        if isinstance(it, PathItem):
            segs += len(it.ops)
            if len(it.ops) > MAX_SEGS_PER_PATH or it.ops.count("M") > MAX_SUBPATHS_PER_PATH:
                return "path"
            if any(op not in _OP_ARGS for op in it.ops) or not it.ops.startswith("M"):
                return "path"
            if sum(_OP_ARGS[op] for op in it.ops) != len(it.xy):
                return "path"
            if not (fin(it.width) and 0 <= it.width <= STROKE_MAX):
                return "path"
            if it.fill_rule not in (NONZERO, EVENODD) or it.join not in (0, 1, 2):
                return "path"
            if it.cap not in (0, 1, 2) or not 0 <= it.role < len(ROLE_NAMES):
                return "path"
            if len(it.dash) > MAX_DASHES or any(not DASH_MIN <= d <= DASH_MAX for d in it.dash):
                return "dash"
            if not coords(it.xy) or not coords(it.bbox) or len(it.bbox) != 4:
                return "coordinate"
            if not (colour(it.fill_rgb) and colour(it.stroke_rgb)):
                return "colour"
        elif isinstance(it, TextRun):
            runs += 1
            chars += len(it.text)
            if not isinstance(it.text, str) or len(it.text) > MAX_RUN_CHARS:
                return "text"
            if _UNDRAWABLE.search(it.text):
                return "text content"
            if not (fin(it.size) and FONT_SIZE_MIN <= it.size <= FONT_SIZE_MAX):
                return "text"
            if it.anchor not in (0, 1, 2) or it.baseline not in (0, 1):
                return "text"
            if not 0 <= it.role < len(ROLE_NAMES) or not (fin(it.box) and 0 <= it.box <= COORD_MAX):
                return "text"
            if not coords((it.x, it.y)):
                return "coordinate"
            if not (colour(it.ink_rgb) and colour(it.ground_rgb)):
                return "colour"
        else:
            return "item"
        if not (-1 <= it.node < nodes and -1 <= it.edge < edges):
            return "owner"
    if segs > MAX_SEGS_TOTAL or runs > MAX_TEXT_RUNS or chars > MAX_TEXT_CHARS:
        return "total"
    if drawn_points(dl) > MAX_DRAWN_POINTS:
        return "too costly to draw"
    for n in dl.nodes:
        if not isinstance(n, HitNode) or not isinstance(n.id, str) or len(n.id) > MAX_ID_CHARS:
            return "node"
        if len(n.attach) > MAX_ATTACH or not all(len(b) == 4 and coords(b) for b in n.attach):
            return "node"
        if len(n.box) != 4 or not coords(n.box):
            return "node"
        if _UNDRAWABLE.search(n.id):
            return "node id"
    for e in dl.edges:
        if not isinstance(e, HitEdge) or not isinstance(e.id, str) or len(e.id) > MAX_ID_CHARS:
            return "edge"
        if _UNDRAWABLE.search(e.id):
            return "edge id"
        if len(e.points) > 2 * MAX_HIT_POINTS or len(e.points) % 2 or not coords(e.points):
            return "edge"
        if len(e.ends) != 2 or not all(isinstance(k, int) and -1 <= k < nodes for k in e.ends):
            return "edge"
    return None


# ----------------------------------------------------------------------------------
# The wire: how a display list crosses from the render child to the GUI process
# ----------------------------------------------------------------------------------

WIRE_VERSION = 1
"""
The first field of a list's header line. A list in any other version is refused.
"""

_FLOAT_BYTES = 25
"""
The most bytes JSON spends on one finite float and the comma after it:
``-2.2250738585072014e-308,``.
"""
_SCALAR_BYTES = 12
"""
The most on any other scalar a record holds (an int, a bool, a role), with its comma.
"""
_CHAR_BYTES = 12
"""
The most on one character of a string: JSON written ASCII-only spells a character
past the BMP as two ``\\uXXXX`` escapes.
"""
_RECORD_BYTES = 64
"""
A record's brackets, quotes, tag and newline, generously.
"""

MAX_LINE_BYTES = (
    _FLOAT_BYTES * (6 * MAX_SEGS_PER_PATH + 4 + 1 + MAX_DASHES)
    + _CHAR_BYTES * max(MAX_SEGS_PER_PATH, MAX_RUN_CHARS)
    + _SCALAR_BYTES * 16
    + _RECORD_BYTES
)
"""
The longest one record may be: a path at ``MAX_SEGS_PER_PATH`` cubics, the longest
record any bound allows (about 0.6 MB). ``decode`` refuses a longer line before
parsing it, because one ``json.loads`` is one C call that holds the GIL: a whole
list at every bound as one document is 64 MB and took 0.97 s to parse here
(2026-10-22, ``shots/native-fix/bounds.py``), the frame stalled for all of it.
Parsed a record at a time, the I/O thread gives the GIL back between records.
"""

MAX_WIRE_BYTES = (
    _FLOAT_BYTES
    * (
        6 * MAX_SEGS_TOTAL
        + (4 + 1 + MAX_DASHES) * MAX_ITEMS
        + 4 * (1 + MAX_ATTACH) * MAX_NODES
        + 2 * MAX_HIT_POINTS * MAX_EDGES
        + 4
    )
    + _CHAR_BYTES
    * (MAX_TEXT_CHARS + MAX_SEGS_TOTAL + MAX_ID_CHARS * (MAX_NODES + MAX_EDGES + MAX_STATS))
    + _SCALAR_BYTES * (16 * MAX_ITEMS + 2 * MAX_EDGES + MAX_STATS)
    + _RECORD_BYTES * (1 + MAX_ITEMS + MAX_NODES + MAX_EDGES)
)
"""
The longest a list within every bound can be on the wire (about 90 MB, most of
it the edges' hit points at ``MAX_EDGES`` x ``MAX_HIT_POINTS``): what the
GUI process will read of one reply (``render``), so a child cannot make it
allocate more. A real diagram is a few tens of kilobytes.
"""


def encode(dl: DisplayList) -> bytes:
    """
    ``dl`` as the render child sends it: a header line, then one JSON array per
    item, node and edge, a line each, in that order. Plain data only, so the
    receiving end evaluates nothing to read it (``decode``).
    """
    dumps = json.JSONEncoder(separators=(",", ":"), allow_nan=False).encode
    lines = [
        dumps(
            {
                "v": WIRE_VERSION,
                "size": [dl.width, dl.height],
                "origin": list(dl.origin),
                "counts": [len(dl.items), len(dl.nodes), len(dl.edges)],
                "stats": [list(s) for s in dl.stats],
            }
        )
    ]
    for it in dl.items:
        if isinstance(it, PathItem):
            lines.append(
                dumps(
                    [
                        "p",
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
                        it.edge,
                        it.fill_rgb,
                        it.stroke_rgb,
                    ]
                )
            )
        else:
            lines.append(
                dumps(
                    [
                        "t",
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
                        it.edge,
                        it.ink_rgb,
                        it.ground_rgb,
                    ]
                )
            )
    lines += [dumps([n.id, n.box, n.attach]) for n in dl.nodes]
    lines += [dumps([e.id, e.points, e.ends]) for e in dl.edges]
    return "\n".join(lines).encode("ascii")


class _Malformed(Exception):
    pass


def decode(blob: bytes, start: int = 0) -> DisplayList | str:
    """
    What the child sent, from ``start`` in ``blob``, read as ``encode`` writes it and
    checked (``validate``): the list, or the first thing wrong with it. Never raises.

    Reading evaluates nothing: each line is JSON, and each field is taken only if it
    is the one JSON type it may be (an int is not a bool, a float may be an int),
    so nothing the child wrote can name a class or run code here. A line longer
    than ``MAX_LINE_BYTES`` is refused unparsed, and so is a header whose counts are
    past a bound, before any record is read.
    """
    try:
        dl = _decode(blob, start)
    except _Malformed as bad:
        return str(bad)
    except Exception as exc:  # what JSON or a wrong type raises: malformed, not a crash
        return f"malformed ({type(exc).__name__})"
    return validate(dl) or dl


def _decode(blob: bytes, start: int) -> DisplayList:
    at = start

    def line() -> Any:
        nonlocal at
        if at > len(blob):
            raise _Malformed("fewer records than its header counts")
        end = blob.find(b"\n", at, at + MAX_LINE_BYTES + 1)
        if end < 0:
            end = len(blob)
            if end - at > MAX_LINE_BYTES:
                raise _Malformed("a record past the longest one may be")
        text, at = blob[at:end], end + 1
        return json.loads(text)

    head = line()
    if not isinstance(head, dict) or head.get("v") != WIRE_VERSION:
        raise _Malformed("not a display list of this version")
    width, height = _floats(head["size"], 2)
    origin = _floats(head["origin"], 2)
    n_items, n_nodes, n_edges = (_int(v) for v in _seq(head["counts"], 3))
    if not (0 <= n_items <= MAX_ITEMS and 0 <= n_nodes <= MAX_NODES and 0 <= n_edges <= MAX_EDGES):
        raise _Malformed("count")
    stats = tuple((_str(k), _int(v)) for k, v in (_seq(s, 2) for s in _seq(head["stats"])))
    items: list[PathItem | TextRun] = []
    for _ in range(n_items):
        rec = _seq(line())
        if rec and rec[0] == "p" and len(rec) == 16:
            items.append(
                PathItem(
                    _int(rec[1]),
                    _bool(rec[2]),
                    _int(rec[3]),
                    _bool(rec[4]),
                    _float(rec[5]),
                    _int(rec[6]),
                    _int(rec[7]),
                    _floats(rec[8]),
                    _str(rec[9]),
                    _floats(rec[10]),
                    _floats(rec[11], 4),  # type: ignore[arg-type]
                    _int(rec[12]),
                    _int(rec[13]),
                    _int(rec[14]),
                    _int(rec[15]),
                )
            )
        elif rec and rec[0] == "t" and len(rec) == 14:
            items.append(
                TextRun(
                    _str(rec[1]),
                    _float(rec[2]),
                    _float(rec[3]),
                    _float(rec[4]),
                    _int(rec[5]),
                    _int(rec[6]),
                    _int(rec[7]),
                    _bool(rec[8]),
                    _float(rec[9]),
                    _int(rec[10]),
                    _int(rec[11]),
                    _int(rec[12]),
                    _int(rec[13]),
                )
            )
        else:
            raise _Malformed("an item that is neither a path nor a text run")
    nodes = []
    for _ in range(n_nodes):
        nid, box, attach = _seq(line(), 3)
        boxes = tuple(_floats(b, 4) for b in _seq(attach))
        nodes.append(HitNode(_str(nid), _floats(box, 4), boxes))  # type: ignore[arg-type]
    edges = []
    for _ in range(n_edges):
        eid, points, ends = _seq(line(), 3)
        a, b = (_int(k) for k in _seq(ends, 2))
        edges.append(HitEdge(_str(eid), _floats(points), (a, b)))
    if at < len(blob):
        raise _Malformed("more records than its header counts")
    return DisplayList(
        width, height, (origin[0], origin[1]), tuple(items), tuple(nodes), tuple(edges), stats
    )


def _seq(v: object, n: int | None = None) -> list[Any]:
    if type(v) is not list or (n is not None and len(v) != n):
        raise _Malformed("a field of the wrong shape")
    return v


def _int(v: object) -> int:
    if type(v) is not int:
        raise _Malformed("a field of the wrong type")
    return v


def _bool(v: object) -> bool:
    if type(v) is not bool:
        raise _Malformed("a field of the wrong type")
    return v


def _float(v: object) -> float:
    if type(v) is not float and type(v) is not int:
        raise _Malformed("a field of the wrong type")
    return float(v)


def _floats(v: object, n: int | None = None) -> tuple[float, ...]:
    return tuple(_float(x) for x in _seq(v, n))


def _str(v: object) -> str:
    if type(v) is not str:
        raise _Malformed("a field of the wrong type")
    return v


# ----------------------------------------------------------------------------------
# Colours: what the author wrote, and legibility against the pane
# ----------------------------------------------------------------------------------

TEXT_CONTRAST = 4.5
"""
The least contrast an author-coloured label is drawn at, against what is behind it:
WCAG 2.x's AA minimum for normal-size text (1.4.3). Not the 3:1 WCAG allows large
text, because a diagram's labels are set at merman's 12-16 px and often scaled
down to fit the pane, well under WCAG's "large" (24 px, or 18.7 px bold). 4.5:1 is
always reachable: black or white reaches at least 4.58:1 against any colour.
"""

STROKE_CONTRAST = 3.0
"""
The least contrast an author-coloured stroke is drawn at against the pane: WCAG
2.1's minimum for graphical objects that carry meaning (1.4.11). An edge or a
node's outline is such an object; it is not text.
"""

_NAMED: dict[str, int] = {
    # CSS Color Module Level 4's named colours (the sanitiser's ``NAMED_COLOURS``).
    "aliceblue": 0xF0F8FF,
    "antiquewhite": 0xFAEBD7,
    "aqua": 0x00FFFF,
    "aquamarine": 0x7FFFD4,
    "azure": 0xF0FFFF,
    "beige": 0xF5F5DC,
    "bisque": 0xFFE4C4,
    "black": 0x000000,
    "blanchedalmond": 0xFFEBCD,
    "blue": 0x0000FF,
    "blueviolet": 0x8A2BE2,
    "brown": 0xA52A2A,
    "burlywood": 0xDEB887,
    "cadetblue": 0x5F9EA0,
    "chartreuse": 0x7FFF00,
    "chocolate": 0xD2691E,
    "coral": 0xFF7F50,
    "cornflowerblue": 0x6495ED,
    "cornsilk": 0xFFF8DC,
    "crimson": 0xDC143C,
    "cyan": 0x00FFFF,
    "darkblue": 0x00008B,
    "darkcyan": 0x008B8B,
    "darkgoldenrod": 0xB8860B,
    "darkgray": 0xA9A9A9,
    "darkgreen": 0x006400,
    "darkgrey": 0xA9A9A9,
    "darkkhaki": 0xBDB76B,
    "darkmagenta": 0x8B008B,
    "darkolivegreen": 0x556B2F,
    "darkorange": 0xFF8C00,
    "darkorchid": 0x9932CC,
    "darkred": 0x8B0000,
    "darksalmon": 0xE9967A,
    "darkseagreen": 0x8FBC8F,
    "darkslateblue": 0x483D8B,
    "darkslategray": 0x2F4F4F,
    "darkslategrey": 0x2F4F4F,
    "darkturquoise": 0x00CED1,
    "darkviolet": 0x9400D3,
    "deeppink": 0xFF1493,
    "deepskyblue": 0x00BFFF,
    "dimgray": 0x696969,
    "dimgrey": 0x696969,
    "dodgerblue": 0x1E90FF,
    "firebrick": 0xB22222,
    "floralwhite": 0xFFFAF0,
    "forestgreen": 0x228B22,
    "fuchsia": 0xFF00FF,
    "gainsboro": 0xDCDCDC,
    "ghostwhite": 0xF8F8FF,
    "gold": 0xFFD700,
    "goldenrod": 0xDAA520,
    "gray": 0x808080,
    "green": 0x008000,
    "greenyellow": 0xADFF2F,
    "grey": 0x808080,
    "honeydew": 0xF0FFF0,
    "hotpink": 0xFF69B4,
    "indianred": 0xCD5C5C,
    "indigo": 0x4B0082,
    "ivory": 0xFFFFF0,
    "khaki": 0xF0E68C,
    "lavender": 0xE6E6FA,
    "lavenderblush": 0xFFF0F5,
    "lawngreen": 0x7CFC00,
    "lemonchiffon": 0xFFFACD,
    "lightblue": 0xADD8E6,
    "lightcoral": 0xF08080,
    "lightcyan": 0xE0FFFF,
    "lightgoldenrodyellow": 0xFAFAD2,
    "lightgray": 0xD3D3D3,
    "lightgreen": 0x90EE90,
    "lightgrey": 0xD3D3D3,
    "lightpink": 0xFFB6C1,
    "lightsalmon": 0xFFA07A,
    "lightseagreen": 0x20B2AA,
    "lightskyblue": 0x87CEFA,
    "lightslategray": 0x778899,
    "lightslategrey": 0x778899,
    "lightsteelblue": 0xB0C4DE,
    "lightyellow": 0xFFFFE0,
    "lime": 0x00FF00,
    "limegreen": 0x32CD32,
    "linen": 0xFAF0E6,
    "magenta": 0xFF00FF,
    "maroon": 0x800000,
    "mediumaquamarine": 0x66CDAA,
    "mediumblue": 0x0000CD,
    "mediumorchid": 0xBA55D3,
    "mediumpurple": 0x9370DB,
    "mediumseagreen": 0x3CB371,
    "mediumslateblue": 0x7B68EE,
    "mediumspringgreen": 0x00FA9A,
    "mediumturquoise": 0x48D1CC,
    "mediumvioletred": 0xC71585,
    "midnightblue": 0x191970,
    "mintcream": 0xF5FFFA,
    "mistyrose": 0xFFE4E1,
    "moccasin": 0xFFE4B5,
    "navajowhite": 0xFFDEAD,
    "navy": 0x000080,
    "oldlace": 0xFDF5E6,
    "olive": 0x808000,
    "olivedrab": 0x6B8E23,
    "orange": 0xFFA500,
    "orangered": 0xFF4500,
    "orchid": 0xDA70D6,
    "palegoldenrod": 0xEEE8AA,
    "palegreen": 0x98FB98,
    "paleturquoise": 0xAFEEEE,
    "palevioletred": 0xDB7093,
    "papayawhip": 0xFFEFD5,
    "peachpuff": 0xFFDAB9,
    "peru": 0xCD853F,
    "pink": 0xFFC0CB,
    "plum": 0xDDA0DD,
    "powderblue": 0xB0E0E6,
    "purple": 0x800080,
    "rebeccapurple": 0x663399,
    "red": 0xFF0000,
    "rosybrown": 0xBC8F8F,
    "royalblue": 0x4169E1,
    "saddlebrown": 0x8B4513,
    "salmon": 0xFA8072,
    "sandybrown": 0xF4A460,
    "seagreen": 0x2E8B57,
    "seashell": 0xFFF5EE,
    "sienna": 0xA0522D,
    "silver": 0xC0C0C0,
    "skyblue": 0x87CEEB,
    "slateblue": 0x6A5ACD,
    "slategray": 0x708090,
    "slategrey": 0x708090,
    "snow": 0xFFFAFA,
    "springgreen": 0x00FF7F,
    "steelblue": 0x4682B4,
    "tan": 0xD2B48C,
    "teal": 0x008080,
    "thistle": 0xD8BFD8,
    "tomato": 0xFF6347,
    "turquoise": 0x40E0D0,
    "violet": 0xEE82EE,
    "wheat": 0xF5DEB3,
    "white": 0xFFFFFF,
    "whitesmoke": 0xF5F5F5,
    "yellow": 0xFFFF00,
    "yellowgreen": 0x9ACD32,
}


@dataclass(frozen=True, slots=True)
class Paints:
    """
    Colours by the property that sets them: ``fill``, ``stroke``, and ``color``,
    which is a label's ink.
    """

    fill: frozenset[int] = frozenset()
    stroke: frozenset[int] = frozenset()
    color: frozenset[int] = frozenset()

    def __or__(self, other: Paints) -> Paints:
        return Paints(self.fill | other.fill, self.stroke | other.stroke, self.color | other.color)


NO_PAINTS = Paints()


@dataclass(frozen=True, slots=True)
class Authored:
    """
    The colours a diagram's source sets, by what each statement names: ``classes``
    by ``classDef`` name, ``ids`` by the id a ``style`` line names, and ``links`` by
    the edge number a ``linkStyle`` line names (-1 for ``linkStyle default``).

    A colour on a shape is the author's only where a statement that names that
    shape set it: merman 0.9.1 writes a node's classDef names on its ``g`` (``node
    default hot``) and its source id in the ``g``'s id (``flowchart-A-0``), and
    draws edges in the order they are written, which is what ``linkStyle`` counts.
    So ``classDef x color:#28253D`` paints the nodes of class ``x``, and not every
    label merman inks in its own ``#28253D``.
    """

    classes: dict[str, Paints] = field(default_factory=dict)
    ids: dict[str, Paints] = field(default_factory=dict)
    links: dict[int, Paints] = field(default_factory=dict)

    def for_node(self, classes: Sequence[str], source_id: str | None) -> Paints:
        """
        What paints a node, a subgraph or a state with ``classes`` on its ``g`` and
        ``source_id`` in the source.
        """
        out = NO_PAINTS
        for name in classes:
            out |= self.classes.get(name, NO_PAINTS)
        if source_id is not None:
            out |= self.ids.get(source_id, NO_PAINTS)
        return out

    def for_edge(self, index: int) -> Paints:
        """
        What paints the edge written ``index``-th: its own ``linkStyle``, and
        ``linkStyle default``.
        """
        return self.links.get(index, NO_PAINTS) | self.links.get(-1, NO_PAINTS)


NOTHING_AUTHORED = Authored()

# A statement that styles: Mermaid's `style`, `classDef` and `linkStyle`, its target,
# and its declarations. It starts a line or follows a `;`, which separates
# statements as a line does (merman 0.9.1 applies `A-->B; style A fill:#f00`); its
# declarations run to the end of the line or to the next such statement, since
# merman also reads `style A fill:#f00;stroke:#00f` as one statement. The lazy run
# tests one fixed lookahead per character, so the scan is linear.
_STATEMENT = re.compile(
    r"(?:^|;)[ \t]*(style|classDef|linkStyle)[ \t]+([^\s;]+)"
    r"((?:(?!;[ \t]*(?:style|classDef|linkStyle)\b)[^\n])*)",
    re.MULTILINE,
)
# One colour declaration inside such a statement: the property starts the
# declaration, so `background-color` is not `color`. The value is a hex colour, a
# functional colour (its parentheses hold no parenthesis), or a word.
_DECLARATION = re.compile(
    r"(?<![-\w])(fill|stroke|color)\s*:\s*(#[0-9A-Fa-f]{3,8}\b|(?:rgba?|hsla?)\([^()]*\)|[A-Za-z]+)"
)


def authored_colours(source: str) -> Authored:
    """
    What ``source``'s ``style``, ``classDef`` and ``linkStyle`` statements paint, by
    what each names, as ``Authored``. A colour that does not parse
    (``parse_colour``) is not counted; nor is a ``linkStyle`` number that is not one.
    Linear in the source.
    """
    found: dict[str, dict[Any, dict[str, set[int]]]] = {
        "classDef": {},
        "style": {},
        "linkStyle": {},
    }
    for statement in _STATEMENT.finditer(source):
        kind, target, body = statement.groups()
        paints: dict[str, set[int]] = {"fill": set(), "stroke": set(), "color": set()}
        for prop, value in _DECLARATION.findall(body):
            rgb = parse_colour(value)
            if rgb is not None:
                paints[prop].add(rgb)
        names: list[Any] = target.split(",") if kind != "style" else [target]
        if kind == "linkStyle":
            names = [
                -1 if n == "default" else int(n) for n in names if n == "default" or n.isdigit()
            ]
        for name in names:
            into = found[kind].setdefault(name, {"fill": set(), "stroke": set(), "color": set()})
            for prop, rgbs in paints.items():
                into[prop] |= rgbs

    def frozen(by: dict[Any, dict[str, set[int]]]) -> dict[Any, Paints]:
        return {
            k: Paints(frozenset(v["fill"]), frozenset(v["stroke"]), frozenset(v["color"]))
            for k, v in by.items()
            if v["fill"] or v["stroke"] or v["color"]
        }

    return Authored(frozen(found["classDef"]), frozen(found["style"]), frozen(found["linkStyle"]))


_HEX = re.compile(r"#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})")
_FUNCTION = re.compile(r"(rgba?|hsla?)\(([^()]*)\)")
_PART = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?(%|deg)?")


def parse_colour(text: str | None) -> int | None:
    """
    A CSS colour as ``0xRRGGBB``, or ``None`` for anything else (``none``,
    ``transparent``, ``currentColor``, a URL, a malformed value). A trailing
    ``!important`` is ignored. Alpha is not part of the answer: ``opacity`` says
    it, and what the builder does with it is there.
    """
    if not text:
        return None
    value = text.strip().lower()
    if value.endswith("!important"):
        value = value[: -len("!important")].strip()
    if value in _NAMED:
        return _NAMED[value]
    hexed = _HEX.fullmatch(value)
    if hexed:
        digits = hexed.group(1)
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits[:3])
        return int(digits[:6], 16)
    func = _FUNCTION.fullmatch(value)
    if not func:
        return None
    found = list(_PART.finditer(func.group(2)))
    if len(found) < 3:
        return None
    numbers = [m.group(0) for m in found]
    if not all(math.isfinite(float(n.removesuffix("%").removesuffix("deg"))) for n in numbers[:3]):
        return None  # 1e999 is a number to the pattern and infinite to float
    try:
        if func.group(1).startswith("rgb"):
            channels = []
            for m in found[:3]:
                unit = m.group(1)
                v = float(m.group(0).removesuffix("%").removesuffix("deg"))
                channels.append(v * 2.55 if unit == "%" else v)
            r, g, b = (max(0, min(255, round(c))) for c in channels)
            return (r << 16) | (g << 8) | b
        hue = float(numbers[0].removesuffix("deg")) % 360.0
        sat = max(0.0, min(1.0, float(numbers[1].rstrip("%")) / 100.0))
        light = max(0.0, min(1.0, float(numbers[2].rstrip("%")) / 100.0))
    except ValueError:
        return None
    return _hsl(hue, sat, light)


def opacity(text: str | None) -> float:
    """
    The alpha a CSS colour is written with, 0 to 1: a ``#rgba`` or ``#rrggbbaa``
    hex's last digits, or the fourth number of a functional colour; 1 for anything
    else.

    The builder takes alpha 0 as no paint, which is what it draws as, and any other
    alpha under 1 as no colour of the author's, so the role's theme colour is drawn:
    a translucent colour's look depends on what is behind it, which is the pane's
    and known only when it is drawn, and the legibility guard measures against
    opaque colours.
    """
    if not text:
        return 1.0
    value = text.strip().lower().removesuffix("!important").strip()
    hexed = _HEX.fullmatch(value)
    if hexed:
        digits = hexed.group(1)
        if len(digits) == 4:
            return int(digits[3] * 2, 16) / 255.0
        if len(digits) == 8:
            return int(digits[6:], 16) / 255.0
        return 1.0
    func = _FUNCTION.fullmatch(value)
    if not func:
        return 1.0
    found = [m.group(0) for m in _PART.finditer(func.group(2))]
    if len(found) < 4:
        return 1.0
    try:
        alpha = float(found[3].removesuffix("%")) / (100.0 if found[3].endswith("%") else 1.0)
    except ValueError:
        return 1.0
    return max(0.0, min(1.0, alpha)) if math.isfinite(alpha) else 1.0


def _hsl(hue: float, sat: float, light: float) -> int:
    chroma = (1 - abs(2 * light - 1)) * sat
    x = chroma * (1 - abs((hue / 60.0) % 2 - 1))
    m = light - chroma / 2
    sector = int(hue // 60) % 6
    r, g, b = [
        (chroma, x, 0.0),
        (x, chroma, 0.0),
        (0.0, chroma, x),
        (0.0, x, chroma),
        (x, 0.0, chroma),
        (chroma, 0.0, x),
    ][sector]
    return (round((r + m) * 255) << 16) | (round((g + m) * 255) << 8) | round((b + m) * 255)


def luminance(rgb: int) -> float:
    """
    WCAG 2.x relative luminance of ``0xRRGGBB``.
    """

    def channel(v: int) -> float:
        c = v / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel((rgb >> 16) & 0xFF)
        + 0.7152 * channel((rgb >> 8) & 0xFF)
        + 0.0722 * channel(rgb & 0xFF)
    )


def contrast(a: int, b: int) -> float:
    """
    WCAG contrast ratio of two ``0xRRGGBB`` colours, 1 to 21.
    """
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix(a: int, b: int, t: float) -> int:
    """
    ``a`` moved ``t`` of the way to ``b``, per channel, as ``0xRRGGBB``.
    """
    out = 0
    for shift in (16, 8, 0):
        ca, cb = (a >> shift) & 0xFF, (b >> shift) & 0xFF
        out |= max(0, min(255, round(ca + (cb - ca) * t))) << shift
    return out


def legible(ink: int, ground: int, minimum: float) -> int:
    """
    ``ink`` as it is where it reaches ``minimum`` contrast against ``ground``, else
    the nearest variant that does: mixed toward white or toward black, whichever
    needs the smaller mix, by the least amount that reaches it. The hue is kept as
    far as the mix keeps it; the ground is never changed.
    """
    if contrast(ink, ground) >= minimum:
        return ink
    best: tuple[float, int] | None = None
    for pole in (0xFFFFFF, 0x000000):
        if contrast(pole, ground) < minimum:
            continue
        low, high = 0.0, 1.0
        for _ in range(16):
            mid = (low + high) / 2
            if contrast(mix(ink, pole, mid), ground) >= minimum:
                high = mid
            else:
                low = mid
        if best is None or high < best[0]:
            best = (high, mix(ink, pole, high))
    if best is None:  # unreachable for minimum <= 4.58: one pole always reaches it
        return 0xFFFFFF if luminance(ground) < 0.18 else 0x000000
    return best[1]


# ----------------------------------------------------------------------------------
# SVG to display list (render child)
# ----------------------------------------------------------------------------------

Affine = tuple[float, float, float, float, float, float]
IDENTITY: Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_SVG = "{http://www.w3.org/2000/svg}"


def mul(m: Affine, n: Affine) -> Affine:
    """
    ``m`` after ``n``: a point goes through ``n`` first.
    """
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
    """
    An SVG ``transform`` list as one affine. An unknown or short function is skipped.
    """
    if not text or text.strip() == "none":
        return IDENTITY
    m = IDENTITY
    for name, args in _TRANSFORM.findall(text):
        v = [float(x) for x in _NUM.findall(args)]
        t: Affine
        if name == "matrix" and len(v) == 6:
            t = (v[0], v[1], v[2], v[3], v[4], v[5])
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
    parse where it went wrong, as SVG renderers do. A path that does not start with
    a moveto is empty.
    """
    raw: list[str] = [cmd or num for cmd, num in _PATH_TOKEN.findall(d)]
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
            if not ops and tok not in "Mm":
                break
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
    """
    SVG's endpoint arc as cubics, one per quarter turn or less (SVG 1.1 F.6.5).
    """
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
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

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
        seg: list[float] = []
        for ux, uy in ((c1 - k * s1, s1 + k * c1), (c2 + k * s2, s2 - k * c2), (c2, s2)):
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
    unit = v.strip()[m.end() :].strip().removesuffix("!important").strip()
    if unit == "em":
        return n * size
    if unit == "pt":
        return n * 4 / 3
    return n


def _dashes(v: str | None) -> tuple[float, ...] | None:
    if not v or v == "none":
        return None
    nums = [abs(float(x)) for x in _NUM.findall(v)]
    if len(nums) == 1:
        nums *= 2
    if nums and all(n >= DASH_MIN for n in nums) and len(nums) <= MAX_DASHES:
        return tuple(min(DASH_MAX, n) for n in nums)
    return None


def _dash_for(classes: set[str], style: dict[str, str], el: ET.Element) -> tuple[float, ...]:
    """
    The dash pattern an inline style gives, else merman's stylesheet gives a class,
    else the attribute. A pattern with a zero in it (``0 0 34.5 0``) is merman
    leaving room for a marker, not a dash: the class decides.
    """
    inline = _dashes(style.get("stroke-dasharray"))
    if inline is not None:
        return inline
    for name, dash in _CLASS_DASH:
        if name in classes:
            return dash
    return _dashes(el.get("stroke-dasharray")) or ()


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
# Roles whose shapes may carry an author's fill or stroke.
_AUTHORED_SHAPES = frozenset({NODE, CLUSTER, NOTE})


class _Sheet:
    """
    ``stroke-width`` from a stylesheet's simplest rules: a selector that is the root's
    id and one class (``#merman .actor``) or one element name (``#merman line``).
    A class rule outranks an element rule, and a later rule an earlier one, as in
    CSS. Used only to scale markers drawn in stroke-width units, the way resvg does.
    """

    def __init__(self, root: ET.Element) -> None:
        # A class's width, with the place in the stylesheet of the rule that set it:
        # two class rules are equally specific, so the later one wins.
        self.by_class: dict[str, tuple[int, float]] = {}
        self.by_tag: dict[str, float] = {}
        rid = root.get("id")
        if not rid:
            return
        css = "".join(
            el.text or ""
            for el in root.iter()
            if isinstance(el.tag, str) and el.tag.replace(_SVG, "") == "style"
        )
        scope = f"#{rid}"
        for at, rule in enumerate(css.split("}")):
            selectors, _, body = rule.partition("{")
            width = None
            for decl in body.split(";"):
                name, _, value = decl.partition(":")
                if name.strip().lower() == "stroke-width":
                    width = _length(value)
            if width is None:
                continue
            for selector in selectors.split(","):
                parts = selector.split()
                if len(parts) != 2 or parts[0] != scope:
                    continue
                simple = parts[1]
                if (
                    simple.startswith(".")
                    and simple[1:].replace("-", "").replace("_", "").isalnum()
                ):
                    self.by_class[simple[1:]] = (at, width)
                elif simple.isalpha():
                    self.by_tag[simple] = width

    def width(self, tag: str, classes: set[str]) -> float | None:
        ruled = [self.by_class[name] for name in classes if name in self.by_class]
        if ruled:
            return max(ruled)[1]
        return self.by_tag.get(tag)


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
    marker_rgb: int = NO_COLOUR
    """
    The author's stroke of the edge a marker is drawn for: its arrowhead takes it.
    """
    paints: Paints = NO_PAINTS
    """
    What the source's statements paint the node, subgraph or state this is inside.
    """


class _Builder:
    def __init__(self, root: ET.Element, authored: Authored, em_ratio: float) -> None:
        self.root = root
        self.authored = authored
        self.em_ratio = em_ratio
        self.sheet = _Sheet(root)
        self.ids: dict[str, ET.Element] = {}
        for el in root.iter():
            i = el.get("id")
            if i:
                self.ids[i] = el
        self.items: list[PathItem | TextRun] = []
        self.nodes: list[HitNode] = []
        self.node_index: dict[str, int] = {}
        self.node_boxes: list[list[float]] = []
        self.node_attach: list[list[Box]] = []
        self.node_fill: dict[int, int] = {}
        self.edges: list[tuple[str, list[float]]] = []
        self.edge_index: dict[str, int] = {}
        # Edge labels drawn before their edge: the run, the edge's id, and the colour
        # the label's text was given, which is the author's if that edge's
        # ``linkStyle`` names it.
        self.pending_labels: list[tuple[int, str, str | None]] = []
        self.segs = 0
        self.chars = 0
        self.runs = 0
        self.dropped: dict[str, int] = {}

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1

    # -- nodes and edges --

    def node_for(self, nid: str) -> int:
        nid = _drawn(nid[:MAX_ID_CHARS])
        got = self.node_index.get(nid)
        if got is None:
            if len(self.node_index) >= MAX_NODES:
                raise Refusal(f"more than {MAX_NODES} nodes")
            got = self.node_index[nid] = len(self.node_boxes)
            self.node_boxes.append([math.inf, math.inf, -math.inf, -math.inf])
            self.node_attach.append([])
            self.nodes.append(HitNode(nid, (0.0, 0.0, 0.0, 0.0), ()))
        return got

    def grow(self, node: int, bbox: Box) -> None:
        b = self.node_boxes[node]
        b[0], b[1] = min(b[0], bbox[0]), min(b[1], bbox[1])
        b[2], b[3] = max(b[2], bbox[2]), max(b[3], bbox[3])

    # -- the walk --

    def walk(self, el: ET.Element, ctx: _Ctx) -> None:
        tag = el.tag.replace(_SVG, "") if isinstance(el.tag, str) else ""
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
            ctx.node = self.node_for(el.get("id", "").removeprefix("merman-"))
            ctx.role = NOTE if "statediagram-note" in classes else NODE
            ctx.paints = self.authored.for_node(sorted(classes), _source_id(el.get("id", "")))
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
                ctx.paints = self.authored.for_node(sorted(classes), _source_id(el.get("id", "")))
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
            if target.tag.replace(_SVG, "") == "symbol":
                for child in target:
                    self.walk(child, ctx)
            else:
                self.walk(target, ctx)
            return
        if tag == "text":
            self.text(el, ctx, classes)
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

    def _authored(self, value: str | None, palette: frozenset[int]) -> int:
        rgb = parse_colour(value)
        if rgb is None or rgb not in palette or opacity(value) < 1.0:
            return NO_COLOUR
        return rgb

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
        # A shape with no fill of its own is filled by merman's stylesheet where its
        # role is a filled one (`.node rect, .node path`): the flowchart cylinder is
        # one open path, top ellipse and body, and is filled in the raster too. An
        # open outline that is ink only says so (`fill="none"`).
        default_fill = role in (NODE, CLUSTER, NOTE, MARKER, SOLID) or (role == FRAME and closed)
        default_fill = default_fill and tag != "line"
        fill = default_fill if fill_v is None else _paints(fill_v) and opacity(fill_v) > 0
        if ctx.in_marker and ctx.marker_fill == "none":
            fill = False
        elif ctx.in_marker and ctx.marker_fill == "hollow":
            role = HOLLOW
        if role in (EDGE, LIFELINE) or tag == "line":
            fill = False
        if stroke_v is not None:
            stroke = _paints(stroke_v) and opacity(stroke_v) > 0
        elif role == FRAME:
            # SVG's default stroke is none; merman strokes frames by class.
            stroke = bool(classes & _CSS_PAINTED)
        else:
            stroke = role != SOLID
        inline_width = _length(style.get("stroke-width"))
        width = inline_width if inline_width is not None else _length(el.get("stroke-width"))
        # What a marker in stroke-width units is scaled by: the width the cascade
        # gives this element, as resvg reads it (inline, then a stylesheet rule,
        # then the attribute, then SVG's initial 1).
        cascaded = inline_width
        if cascaded is None:
            cascaded = self.sheet.width(tag, classes)
        if cascaded is None:
            cascaded = _length(el.get("stroke-width"))
        if cascaded is None:
            cascaded = 1.0
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
        # The author's colours: only an inline declaration on a node's, cluster's or
        # edge's shape, and only a colour a statement naming that shape sets (an
        # edge's number is the one ``add_edge`` gives it, next).
        fill_rgb = stroke_rgb = NO_COLOUR
        if ctx.in_marker:
            if ctx.marker_rgb != NO_COLOUR:
                fill_rgb = ctx.marker_rgb if role != HOLLOW else NO_COLOUR
                stroke_rgb = ctx.marker_rgb
        elif role in _AUTHORED_SHAPES or role == EDGE:
            paints = self.authored.for_edge(len(self.edges)) if role == EDGE else ctx.paints
            if fill and role != EDGE:
                fill_rgb = self._authored(style.get("fill"), paints.fill)
            if stroke:
                stroke_rgb = self._authored(style.get("stroke"), paints.stroke)
        txy = self.transform(xy, ctx.m)
        edge = -1
        if role == EDGE:
            eid = (el.get("data-id") or el.get("id") or f"edge{len(self.edges)}").removeprefix(
                "merman-"
            )
            edge = self.add_edge(_drawn(eid[:MAX_ID_CHARS]), ops, txy)
        if fill and fill_rgb != NO_COLOUR and ctx.node >= 0 and not ctx.in_marker:
            self.node_fill[ctx.node] = fill_rgb
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
                    fill_rgb,
                    stroke_rgb,
                )
            )
        for attr, at_start in (("marker-start", True), ("marker-end", False)):
            ref = style.get(attr) or el.get(attr)
            if ref:
                mm = re.search(r"url\(#([^)]+)\)", ref)
                target = self.ids.get(mm.group(1)) if mm else None
                if target is not None:
                    self.marker(target, txy, at_start, cascaded * scale, ctx, edge, stroke_rgb)

    def transform(self, xy: list[float], m: Affine) -> list[float]:
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
        xy: list[float],
        at_start: bool,
        width: float,
        ctx: _Ctx,
        edge: int,
        stroke_rgb: int,
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
        if mk.get("markerUnits", "strokeWidth") == "strokeWidth":
            if width <= 0:
                return  # scaled to nothing, as SVG and resvg draw it
            s *= width
        rx, ry = _length(mk.get("refX")) or 0.0, _length(mk.get("refY")) or 0.0
        c, si = math.cos(ang), math.sin(ang)
        m: Affine = (c * s, si * s, -si * s, c * s, px, py)
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
            stroke_rgb,
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
                    self.items[i] = _owned(it, edge)

    def add_edge(self, eid: str, ops: str, xy: list[float]) -> int:
        if len(self.edges) >= MAX_EDGES:
            raise Refusal(f"more than {MAX_EDGES} edges")
        flat = [v for pl, _ in flatten_units(ops, xy, 1.0) for p in pl for v in p]
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
            if item.node >= 0 and item.role != LIFELINE:
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

    def text(self, el: ET.Element, ctx: _Ctx, classes: set[str]) -> None:
        a, b, c, d = ctx.m[:4]
        if abs(b) > 1e-6 or abs(c) > 1e-6 or a <= 0 or d <= 0:
            self.drop("transformed text")
            return
        role = ctx.role if ctx.role == EDGE_LABEL else LABEL
        if "sequenceNumber" in classes:
            role = BADGE_TEXT
        x = _length(el.get("x")) or 0.0
        y = (_length(el.get("y")) or 0.0) + (_length(el.get("dy"), ctx.size) or 0.0)
        lines: list[tuple[str, float, float]] = []
        if el.text and el.text.strip():
            lines.append((el.text, x, y))
        spans = [ts for ts in el if isinstance(ts.tag, str) and ts.tag.replace(_SVG, "") == "tspan"]
        for ts in spans:
            if ts.get("x") is not None:
                x = _length(ts.get("x")) or 0.0
            if ts.get("y") is not None:
                y = _length(ts.get("y")) or 0.0
            y += _length(ts.get("dy"), ctx.size) or 0.0
            if ts.text and ts.text.strip():
                lines.append((ts.text, x, y))
        ink = ground = NO_COLOUR
        edge = self.edge_index.get(ctx.edge_label, -1) if ctx.edge_label else -1
        said: str | None = None
        if role in (LABEL, EDGE_LABEL):
            for source in (el, *spans):
                said = _style(source).get("fill") or source.get("fill")
                if said:
                    break
            if role == LABEL:
                ink = self._authored(said, ctx.paints.color)
            elif edge >= 0:
                ink = self._authored(said, self.authored.for_edge(edge).color)
            if role == LABEL and ctx.node >= 0:
                ground = self.node_fill.get(ctx.node, NO_COLOUR)
        size = min(FONT_SIZE_MAX, max(FONT_SIZE_MIN, ctx.size * d / self.em_ratio))
        for s, tx, ty in lines:
            s = " ".join(_drawn(s).split())
            if len(s) > MAX_RUN_CHARS:
                s = s[: MAX_RUN_CHARS - 1] + "…"
            px, py = apply(ctx.m, tx, ty)
            if not (COORD_MIN <= px <= COORD_MAX and COORD_MIN <= py <= COORD_MAX):
                raise Refusal("a coordinate is out of range")
            run = TextRun(
                s,
                px,
                py,
                size,
                ctx.anchor,
                ctx.baseline,
                role,
                ctx.bold,
                min(COORD_MAX, ctx.box * a),
                ctx.node,
                edge,
                ink,
                ground,
            )
            if ctx.edge_label and edge < 0:
                self.pending_labels.append((len(self.items), ctx.edge_label, said))
            self.add(run)

    def finish(self, width: float, height: float, origin: tuple[float, float]) -> DisplayList:
        # Edge labels drawn before their edge was seen.
        for i, eid, said in self.pending_labels:
            e = self.edge_index.get(eid, -1)
            it = self.items[i]
            if e >= 0 and isinstance(it, TextRun):
                ink = self._authored(said, self.authored.for_edge(e).color)
                self.items[i] = replace(it, edge=e, ink_rgb=ink)
        nodes = []
        for k, n in enumerate(self.nodes):
            b = self.node_boxes[k]
            box = (b[0], b[1], b[2], b[3]) if b[0] <= b[2] else (0.0, 0.0, 0.0, 0.0)
            nodes.append(HitNode(n.id, box, tuple(self.node_attach[k][:MAX_ATTACH])))
        edges = []
        for eid, pts in self.edges:
            ends = (
                (_end_node(nodes, pts[0], pts[1]), _end_node(nodes, pts[-2], pts[-1]))
                if pts
                else (-1, -1)
            )
            edges.append(HitEdge(eid, tuple(pts), ends))
        stats = [("segments", self.segs), ("text_runs", self.runs), ("chars", self.chars)]
        stats += [(f"dropped {why}", n) for why, n in sorted(self.dropped.items())]
        return DisplayList(
            width,
            height,
            origin,
            tuple(self.items),
            tuple(nodes),
            tuple(edges),
            tuple(stats[:MAX_STATS]),
        )


_FAMILY_ID = re.compile(r"(?:flowchart|classId|state|entity)-(.+)-\d+")


def _source_id(gid: str) -> str:
    """
    The id the source gave what merman drew as the ``g`` with id ``gid``: merman
    0.9.1 writes a node as ``merman-flowchart-A-0`` (``classId-``, ``state-``,
    ``entity-``), a subgraph as ``merman-A``.
    """
    gid = gid.removeprefix("merman-")
    named = _FAMILY_ID.fullmatch(gid)
    return named.group(1) if named else gid


def _drawn(text: str) -> str:
    """
    ``text`` with each character a run or an id may not hold (``_UNDRAWABLE``) as a
    space: the tab, newline or CR an attribute's character reference can leave.
    """
    return _UNDRAWABLE.sub(" ", text)


def _paints(value: str) -> bool:
    return value.strip().removesuffix("!important").strip() not in ("none", "transparent")


def _owned(it: PathItem | TextRun, edge: int) -> PathItem | TextRun:
    return replace(it, edge=edge)


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
                dd == best_d == 0.0 and best >= 0 and area(n.box) < area(nodes[best].box)
            ):
                best, best_d = k, dd
    return best


def area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def node_at(dl: DisplayList, x: float, y: float) -> int:
    """
    The node under the point ``(x, y)`` in diagram units, or -1: the one with the
    smallest shape there (a node over the cluster it sits in). A node is tested by
    its shapes' boxes (``HitNode.attach``), not its whole extent, which for a
    sequence participant is the column its messages and notes cross; one with no
    shape of its own is tested by its extent.
    """
    hit, best = -1, math.inf
    for n, node in enumerate(dl.nodes):
        for b in node.attach or (node.box,):
            if b[0] <= x <= b[2] and b[1] <= y <= b[3] and area(b) < best:
                hit, best = n, area(b)
    return hit


def _direction(xy: list[float], *, from_start: bool) -> tuple[float, float]:
    pts = [(xy[i], xy[i + 1]) for i in range(0, len(xy), 2)]
    if not from_start:
        pts = pts[::-1]
    p0 = pts[0]
    for q in pts[1:]:
        if abs(q[0] - p0[0]) + abs(q[1] - p0[1]) > 1e-9:
            return (q[0] - p0[0], q[1] - p0[1]) if from_start else (p0[0] - q[0], p0[1] - q[1])
    return (1.0, 0.0)


def _bbox(xy: list[float]) -> Box:
    xs, ys = xy[0::2], xy[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


def rect_path(
    x: float, y: float, w: float, h: float, rx: float | None, ry: float | None
) -> tuple[str, list[float]]:
    """
    An SVG ``rect`` as a closed path, its corners rounded where ``rx``/``ry`` say.
    """
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
    kx, ky = k * rx, k * ry
    r, b = x + w, y + h
    return "MLCLCLCLCZ", [
        *(x + rx, y, r - rx, y),
        *(r - rx + kx, y, r, y + ry - ky, r, y + ry),
        *(r, b - ry),
        *(r, b - ry + ky, r - rx + kx, b, r - rx, b),
        *(x + rx, b),
        *(x + rx - kx, b, x, b - ry + ky, x, b - ry),
        *(x, y + ry),
        *(x, y + ry - ky, x + rx - kx, y, x + rx, y),
    ]


def ellipse_path(cx: float, cy: float, rx: float, ry: float) -> tuple[str, list[float]]:
    """
    An ellipse as four cubics, closed.
    """
    if rx <= 0 or ry <= 0:
        return "", []
    k = 0.5522847498
    return "MCCCCZ", [
        *(cx + rx, cy),
        *(cx + rx, cy + k * ry, cx + k * rx, cy + ry, cx, cy + ry),
        *(cx - k * rx, cy + ry, cx - rx, cy + k * ry, cx - rx, cy),
        *(cx - rx, cy - k * ry, cx - k * rx, cy - ry, cx, cy - ry),
        *(cx + k * rx, cy - ry, cx + rx, cy - k * ry, cx + rx, cy),
    ]


# ----------------------------------------------------------------------------------
# Geometry the pane draws from (pure: pixels, no ImGui)
# ----------------------------------------------------------------------------------

Point = tuple[float, float]


def _cubic_steps(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    tol: float,
) -> int:
    """
    Wang's step count for a cubic to stay within ``tol``, at most ``MAX_CURVE_STEPS``.
    """
    dd = max(
        math.hypot(x0 - 2 * x1 + x2, y0 - 2 * y1 + y2),
        math.hypot(x1 - 2 * x2 + x3, y1 - 2 * y2 + y3),
    )
    return min(MAX_CURVE_STEPS, max(1, math.ceil(math.sqrt(0.75 * dd / tol))))


def _quad_steps(
    x0: float, y0: float, x1: float, y1: float, x2: float, y2: float, tol: float
) -> int:
    """
    Wang's step count for a quadratic to stay within ``tol``, at most
    ``MAX_CURVE_STEPS``.
    """
    dd = math.hypot(x0 - 2 * x1 + x2, y0 - 2 * y1 + y2)
    return min(MAX_CURVE_STEPS, max(1, math.ceil(math.sqrt(0.25 * dd / tol))))


def path_points(ops: str, xy: Sequence[float], scale: float, tol: float = FLATTEN_TOL_PX) -> int:
    """
    At least as many points as ``flatten_units`` makes of the path at ``scale``: one
    for each move, line and close, and each curve's steps, held to the per-item cap
    ``flatten_units`` stops at. Steps grow with the scale, so the count at the
    largest scale a figure is drawn at bounds every smaller one.
    """
    n = k = 0
    x = y = 0.0
    sx = sy = 0.0
    for op in ops:
        if op in "ML":
            x, y = xy[k] * scale, xy[k + 1] * scale
            if op == "M":
                sx, sy = x, y
            k += 2
            n += 1
        elif op == "C":
            x1, y1, x2, y2, x3, y3 = (v * scale for v in xy[k : k + 6])
            k += 6
            n += _cubic_steps(x, y, x1, y1, x2, y2, x3, y3, tol)
            x, y = x3, y3
        elif op == "Q":
            x1, y1, x2, y2 = (v * scale for v in xy[k : k + 4])
            k += 4
            n += _quad_steps(x, y, x1, y1, x2, y2, tol)
            x, y = x2, y2
        elif op == "Z":
            x, y = sx, sy
            n += 1
        if n > MAX_POINTS_PER_ITEM + MAX_CURVE_STEPS:
            break
    return min(n, MAX_POINTS_PER_ITEM + MAX_CURVE_STEPS)


def drawn_points(dl: DisplayList) -> int:
    """
    At least as many points as the pane flattens ``dl``'s paths into at any width it
    draws it: what a build's cost grows with (``host.build``). The pane draws a figure
    no wider than its natural width, so at most one pixel a unit, or a pixel's width
    when the natural width is under one (``host.FigureWidget.size_for``).
    """
    scale = max(1.0, 1.0 / dl.width)
    return sum(path_points(it.ops, it.xy, scale) for it in dl.items if isinstance(it, PathItem))


def flatten_units(
    ops: str, xy: Sequence[float], scale: float, tol: float = FLATTEN_TOL_PX
) -> list[tuple[list[Point], bool]]:
    """
    Subpaths as polylines at ``scale`` pixels a unit, each with whether it was closed.
    Curves get Wang's step count for ``tol`` pixels, at most ``MAX_CURVE_STEPS``; the
    whole stops at ``MAX_POINTS_PER_ITEM`` points.
    """
    out: list[tuple[list[Point], bool]] = []
    cur: list[Point] = []
    k = 0
    x = y = 0.0
    held = 0
    for op in ops:
        if op == "M":
            if len(cur) > 1:
                out.append((cur, False))
                held += len(cur)
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
            n = _cubic_steps(x, y, x1, y1, x2, y2, x3, y3, tol)
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
            n = _quad_steps(x, y, x1, y1, x2, y2, tol)
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
                held += len(cur)
            if cur:
                x, y = cur[0]
            cur = [(x, y)]
        if held + len(cur) > MAX_POINTS_PER_ITEM:
            break
    if len(cur) > 1:
        out.append((cur, False))
    return out


def clockwise(pts: list[Point]) -> list[Point]:
    """
    ``pts`` wound clockwise on screen (y down), as ImGui's anti-aliased fills want.
    """
    a = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        a += x0 * y1 - x1 * y0
    return pts if a >= 0 else pts[::-1]


def convex(pts: list[Point]) -> bool:
    """
    Whether the closed polygon ``pts`` turns one way only.
    """
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


def dedupe(pts: list[Point], closed: bool) -> list[Point]:
    """
    ``pts`` without consecutive repeats, and without a closing repeat of the first.
    """
    out: list[Point] = []
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


def fill_loops(pts: list[Point]) -> list[list[Point]]:
    """
    A filled subpath cut where it comes back to where it started, as loops each
    filled on its own. merman's cylinder is one subpath that runs round the top
    ellipse, back to its start, then down and round the body: as one polygon it
    touches itself, which an ear-clipping fill cannot take; as two, each is simple
    and their union is the cylinder.
    """
    loops: list[list[Point]] = []
    start = 0
    for i in range(1, len(pts) - 1):
        p, s = pts[i], pts[start]
        if i - start >= 2 and abs(p[0] - s[0]) + abs(p[1] - s[1]) <= 1e-3:
            loops.append(pts[start : i + 1])
            start = i
    loops.append(pts[start:])
    return loops


def dashed(
    pts: list[Point], closed: bool, dash: tuple[float, ...], most: int = MAX_DASH_PIECES
) -> list[list[Point]] | None:
    """
    The polyline cut by ``dash`` (pixels), or ``None`` when that would be more than
    ``most`` pieces, at most ``MAX_DASH_PIECES``: told from the line's length before
    anything is cut, so a refusal costs one pass over the points.
    """
    if closed:
        pts = pts + [pts[0]]
    most = min(most, MAX_DASH_PIECES)
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:], strict=False))
    period = sum(dash) * (1 if len(dash) % 2 == 0 else 2)
    if period <= 0 or length / period * math.ceil(len(dash) / 2) > most:
        return None
    pieces: list[list[Point]] = []
    k, left, on = 0, dash[0], True
    cur: list[Point] = [pts[0]]
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


# ----------------------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------------------


def from_svg(svg: str, authored: Authored = NOTHING_AUTHORED, em_ratio: float = 1.0) -> DisplayList:
    """
    Sanitised SVG to a display list, or ``Refusal``. ``authored`` is what the
    diagram's source paints (``authored_colours``); ``em_ratio`` is the pane's prose
    face's em per unit of ImGui font size, so a run's ``size`` is in the unit the
    pane draws text in.
    """
    if not (math.isfinite(em_ratio) and 0.25 <= em_ratio <= 4.0):
        em_ratio = 1.0
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise Refusal(f"the SVG does not parse: {exc}") from None
    vb = [float(v) for v in _NUM.findall(root.get("viewBox", ""))]
    w = _length(root.get("width"))
    h = _length(root.get("height"))
    if len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
        if not (w and h):
            raise Refusal("the diagram has no size")
        vb = [0.0, 0.0, w, h]
    w, h = w or vb[2], h or vb[3]
    if not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
        raise Refusal("the diagram's natural size is out of range")
    s = min(w / vb[2], h / vb[3])
    m: Affine = (s, 0, 0, s, -vb[0] * s, -vb[1] * s)
    b = _Builder(root, authored, em_ratio)
    ctx = _Ctx(m, FRAME, -1, None, 14.0, False, ANCHOR_START, BASE_ALPHABETIC, 0.0, False, 0)
    for child in root:
        b.walk(child, ctx)
    dl = b.finish(w, h, (0.0, 0.0))
    bad = validate(dl)
    if bad:
        raise Refusal(f"display list out of bounds: {bad}")
    return dl


def from_mermaid(source: str, svg: str, em_ratio: float = 1.0) -> DisplayList:
    """
    A diagram's display list from its ``source`` and the sanitised SVG merman made
    of it (``mermaid.layout``): what the render child answers with.
    """
    return from_svg(svg, authored_colours(source), em_ratio)


__all__: Sequence[str] = (
    "Authored",
    "DisplayList",
    "Paints",
    "HitEdge",
    "HitNode",
    "PathItem",
    "Refusal",
    "STROKE_CONTRAST",
    "TEXT_CONTRAST",
    "TextRun",
    "authored_colours",
    "contrast",
    "decode",
    "drawn_points",
    "encode",
    "from_mermaid",
    "from_svg",
    "legible",
    "node_at",
    "parse_colour",
    "validate",
)
