"""
The display list a Mermaid diagram is drawn from (``figures.displaylist``): the SVG
parser and its bounds, which colours are the author's, the legibility guard, and a
golden record of what merman 0.9.1's output becomes for the five families and the
21-node board graph.

**The golden is pinned to merman 0.9.1** and fails, saying to re-baseline, under any
other version. The parser reads merman's structure and a table of its class names
(``displaylist._CLASS_DASH``, ``_CSS_PAINTED``) and does not run CSS, so an upgrade
can change what is drawn without changing anything here. Re-baselining means: run
the diagrams under the new merman, look at them drawn in a real window in both
themes (the window check in the planning record §11), and only then
write the new numbers in ``GOLDEN``. The numbers were taken on 2026-10-22 from this
parser, after the window check of planning record §11; they detect change, and the
screenshots are what said the drawing was right.

Tests marked ``needs_merman`` run merman in this process (a test, not the GUI) and
skip where the optional extra is not installed.
"""

from __future__ import annotations

import collections
import json
import math
import xml.etree.ElementTree as ET
from typing import Any

import pytest

from pptmstr import theme
from pptmstr.figures import displaylist as D
from pptmstr.figures import host, mermaid
from pptmstr.figures.sanitise import NAMED_COLOURS, Sanitised

needs_merman = pytest.mark.skipif(
    mermaid.version() is None, reason="the optional 'mermaid' extra (merman) is not installed"
)
PINNED = "0.9.1"
RE_BASELINE = (
    "merman is {found}, and the display-list golden was taken on merman "
    + PINNED
    + ". The parser keys on merman's class names and markup, which an upgrade can move "
    "silently: re-baseline GOLDEN in tests/test_figure_displaylist.py after checking the "
    "six diagrams drawn in a real window in both themes (see this module's docstring)."
)


def close(a: Any, b: Any, eps: float = 1e-6) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= eps for x, y in zip(a, b, strict=True))


SVG_HEAD = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'


# -- paths, transforms, shapes ---------------------------------------------------------


def test_paths_parse_to_absolute_segments() -> None:
    # Relative, implicit lineto after M, H/V, Z returning to the start.
    ops, xy = D.parse_path("m10 10 20 0 v5 h-5 z l1 1")
    assert ops == "MLLLZL"
    assert close(xy, [10, 10, 30, 10, 30, 15, 25, 15, 11, 11])
    # S reflects the last control point.
    ops, xy = D.parse_path("M0 0 C 0 10 10 10 10 0 S 20 -10 20 0")
    assert ops == "MCC" and close(xy[8:10], [10, -10])
    # Numbers run together: "1.5.5" is 1.5 then .5; exponents.
    _, xy = D.parse_path("M1.5.5L1e1-2")
    assert close(xy, [1.5, 0.5, 10, -2])


def test_an_arc_with_fused_flags_ends_where_it_says_and_bulges_the_right_way() -> None:
    ops, xy = D.parse_path("M0 0a5 5 0 0110 0")
    assert ops in ("MCC", "MC")
    assert close(xy[-2:], [10, 0])
    pts = D.flatten_units(ops, xy, 1.0)[0][0]
    assert min(p[1] for p in pts) < -4.9, "sweep 1 from (0,0) to (10,0) bulges to -y"


@pytest.mark.parametrize("d, ops", [("M0 0 L 5 5 L 3", "ML"), ("L 5 5", ""), ("", "")])
def test_a_malformed_path_keeps_what_parsed(d: str, ops: str) -> None:
    assert D.parse_path(d)[0] == ops


def test_transforms_compose_in_svg_order() -> None:
    m = D.parse_transform("translate(10,20) scale(2)")
    assert close(D.apply(m, 1, 1), [12, 22])
    m = D.parse_transform("rotate(90 10 10)")
    assert close(D.apply(m, 20, 10), [10, 20], 1e-9)


def test_a_rounded_rect_is_closed_and_inside_its_box() -> None:
    ops, xy = D.rect_path(0, 0, 10, 4, 2, None)
    assert ops.endswith("Z") and min(xy) >= 0 and max(xy[0::2]) <= 10 and max(xy[1::2]) <= 4


def test_fills_are_wound_clockwise_and_convexity_is_told() -> None:
    ccw = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    assert D.clockwise(ccw) == ccw[::-1]
    assert D.convex([(0, 0), (2, 0), (2, 2), (0, 2)])
    assert not D.convex([(0, 0), (2, 0), (1, 1), (2, 2), (0, 2)])


def test_a_dash_pattern_cuts_a_line_into_its_pieces() -> None:
    pieces = D.dashed([(0.0, 0.0), (10.0, 0.0)], False, (2.0, 3.0))
    assert pieces is not None
    assert [(p[0][0], p[-1][0]) for p in pieces] == [(0.0, 2.0), (5.0, 7.0)]


def test_a_dash_cut_past_its_budget_is_refused_from_the_length_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = [(0.0, 0.0), (100.0, 0.0)]
    assert len(D.dashed(line, False, (1.0, 1.0), most=50) or []) == 50
    hypot = math.hypot
    calls: list[int] = []

    def counted(*a: float) -> float:
        calls.append(1)
        return hypot(*a)

    monkeypatch.setattr(math, "hypot", counted)
    assert D.dashed(line, False, (1.0, 1.0), most=49) is None
    assert len(calls) == 1, "one pass over the points, nothing cut"
    assert D.dashed(line, False, (0.01, 0.01)) is None, "past MAX_DASH_PIECES"


def test_a_fill_that_returns_to_its_start_is_filled_as_separate_loops() -> None:
    """
    merman's cylinder: round the top ellipse back to the start, then down the body.
    One polygon would touch itself; the loops are each simple.
    """
    ops, xy = D.parse_path(
        "M0,10 a50,10 0,0,0 100,0 a50,10 0,0,0 -100,0 l0,50 a50,10 0,0,0 100,0 l0,-50"
    )
    ((pts, closed),) = D.flatten_units(ops, xy, 1.0)
    assert not closed
    top, body = D.fill_loops(pts)
    assert close(top[0], top[-1]) and close(top[-1], body[0]), "both start where the path did"
    assert max(p[1] for p in top) <= 20.0 + 1e-6 and max(p[1] for p in body) > 60.0
    assert D.fill_loops([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]) == [
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    ]


# -- the SVG walk ------------------------------------------------------------------------

MARKED = (
    SVG_HEAD + '<defs><marker id="a" refX="10" refY="5" markerWidth="10" '
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


def test_a_marker_lands_at_the_path_end_turned_along_it_and_belongs_to_the_edge() -> None:
    dl = D.from_svg(MARKED)
    marker = next(i for i in dl.items if isinstance(i, D.PathItem) and i.role == D.MARKER)
    tip = max(range(0, len(marker.xy), 2), key=lambda i: marker.xy[i + 1])
    assert close(marker.xy[tip : tip + 2], [20, 70])
    assert marker.edge == 0
    assert [n.id for n in dl.nodes] == ["n-A", "n-B"] and dl.edges[0].ends == (0, 1)
    run = next(i for i in dl.items if isinstance(i, D.TextRun))
    assert (run.text, run.anchor, run.baseline, run.size) == (
        "hi",
        D.ANCHOR_MIDDLE,
        D.BASE_CENTRAL,
        12.0,
    )


def test_a_runs_size_is_in_imgui_units_for_the_panes_face() -> None:
    """
    SVG's font-size is an em; ImGui sizes by ascent less descent. At Roboto's 0.853
    em per unit, merman's 12 px em is a 14.06 ImGui size.
    """
    run = next(i for i in D.from_svg(MARKED, em_ratio=0.853).items if isinstance(i, D.TextRun))
    assert run.size == pytest.approx(12.0 / 0.853)


def test_two_class_rules_on_one_element_give_the_later_rules_width_whatever_the_order() -> None:
    """
    Two ``#root .class`` rules are equally specific, so the later one in the sheet
    wins, as in CSS, and not whichever class a set happens to give first.
    """
    names = [f"c{k}" for k in range(20)]
    css = "".join(f"#r .{n}{{stroke-width:{k + 1}}}" for k, n in enumerate(names))
    root = ET.fromstring(
        f'<svg xmlns="http://www.w3.org/2000/svg" id="r"><style>{css}</style></svg>'
    )
    sheet = D._Sheet(root)
    for shuffled in (names, names[::-1], names[7:] + names[:7]):
        assert sheet.width("path", set(shuffled)) == 20.0
    assert sheet.width("path", {"c3", "c1"}) == 4.0


def test_a_marker_in_stroke_width_units_scales_by_the_stylesheets_width() -> None:
    """
    merman's sequence-number badge: a zero-length line whose attribute says width 0
    and whose stylesheet (``#merman line``) says 2, carrying a marker circle of radius
    6 in stroke-width units. resvg draws it 24 across; the probe drew it at 12.
    A class rule outranks the element rule, and width 0 draws no marker, as in SVG.
    """
    head = (
        '<svg xmlns="http://www.w3.org/2000/svg" id="m" width="100" height="100" '
        'viewBox="0 0 100 100"><style>#m .thin{stroke-width:1px}#m .x circle,#m line'
        "{stroke-width:2px}</style>"
        '<defs><marker id="b" refX="15" refY="15" markerWidth="60" markerHeight="40" '
        'orient="auto"><circle cx="15" cy="15" r="6"/></marker></defs>'
    )

    def badge(line: str) -> float | None:
        dl = D.from_svg(head + line + "</svg>")
        found = [i for i in dl.items if isinstance(i, D.PathItem) and i.role == D.MARKER]
        return found[0].bbox[2] - found[0].bbox[0] if found else None

    common = 'x1="50" y1="50" x2="50" y2="50" marker-start="url(#b)"'
    assert badge(f'<line {common} stroke-width="0"/>') == pytest.approx(24.0)
    assert badge(f'<line class="thin" {common} stroke-width="0"/>') == pytest.approx(12.0)
    assert badge(f'<line {common} style="stroke-width:0"/>') is None


def test_an_open_node_outline_with_no_fill_of_its_own_is_filled() -> None:
    """
    The cylinder carries no fill attribute; merman's stylesheet fills it, and so
    does the raster. An open outline that says ``fill="none"`` stays ink only.
    """
    svg = (
        SVG_HEAD + '<g class="node" id="merman-n-E">'
        '<path class="basic label-container outer-path" d="M0,10 a40,8 0,0,0 80,0 '
        'a40,8 0,0,0 -80,0 l0,40 a40,8 0,0,0 80,0 l0,-40"/>'
        '<path fill="none" stroke="#28253D" d="M0 0 L10 0 L10 10"/></g></svg>'
    )
    cylinder, outline = (i for i in D.from_svg(svg).items if isinstance(i, D.PathItem))
    assert cylinder.fill and cylinder.role == D.NODE
    assert not outline.fill and outline.stroke


# -- bounds --------------------------------------------------------------------------------


def test_an_out_of_range_coordinate_is_refused() -> None:
    with pytest.raises(D.Refusal):
        D.from_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            '<path d="M0 0 L1e9 0"/></svg>'
        )


def test_a_long_run_is_cut_and_not_refused() -> None:
    dl = D.from_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text x="0" y="5">'
        + "x" * 5000
        + "</text></svg>"
    )
    (run,) = dl.items
    assert (
        isinstance(run, D.TextRun) and len(run.text) == D.MAX_RUN_CHARS and run.text.endswith("…")
    )


def test_more_nodes_than_the_bound_are_refused() -> None:
    many = "".join(
        f'<g class="node" id="merman-n{i}"><rect width="1" height="1"/></g>'
        for i in range(D.MAX_NODES + 1)
    )
    with pytest.raises(D.Refusal, match="nodes"):
        D.from_svg(f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">{many}</svg>')


@pytest.mark.parametrize(
    "svg",
    [
        "<svg",
        '<svg xmlns="http://www.w3.org/2000/svg"/>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="20000" height="10"/>',
    ],
)
def test_a_document_without_a_drawable_size_is_refused(svg: str) -> None:
    with pytest.raises(D.Refusal):
        D.from_svg(svg)


def _path(**changed: Any) -> D.PathItem:
    base = D.PathItem(
        D.NODE, True, 0, True, 1.0, 0, 0, (), "ML", (0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0)
    )
    return D.PathItem(**{**{f: getattr(base, f) for f in D.PathItem.__slots__}, **changed})


@pytest.mark.parametrize(
    "item, why",
    [
        (_path(width=999.0), "path"),
        (_path(ops="MLL"), "path"),
        (_path(ops="LL"), "path"),
        (_path(ops="MX", xy=(0.0, 0.0, 1.0, 1.0)), "path"),
        (_path(xy=(0.0, 0.0, math.nan, 1.0)), "coordinate"),
        (_path(fill_rgb=0x1000000), "colour"),
        (_path(dash=(0.1, 1.0)), "dash"),
        (_path(node=3), "owner"),
        (_path(role=99), "path"),
        (D.TextRun("t", 0.0, 0.0, 999.0, 0, 0, D.LABEL, False, 0.0), "text"),
        (D.TextRun("t", 0.0, 0.0, 12.0, 0, 0, D.LABEL, False, 0.0, ink_rgb=-7), "colour"),
        ("not an item", "item"),
    ],
)
def test_validate_catches_what_a_broken_child_might_send(item: Any, why: str) -> None:
    dl = D.DisplayList(10.0, 10.0, (0.0, 0.0), (item,), (), ())
    assert D.validate(dl) == why


def test_validate_answers_rather_than_raising_on_the_wrong_types() -> None:
    assert D.validate("a string") == "not a display list"
    broken = D.DisplayList(10.0, 10.0, (0.0, 0.0), (_path(),), ("not a node",), ())  # type: ignore[arg-type]
    assert D.validate(broken) == "node"
    worse = D.DisplayList("10", 10.0, (0.0, 0.0), (), (), ())  # type: ignore[arg-type]
    assert D.validate(worse) is not None and D.validate(worse).startswith("malformed")  # type: ignore[union-attr]
    edge = D.DisplayList(10.0, 10.0, (0.0, 0.0), (), (), (D.HitEdge("e", (0.0, 0.0), (0, 1)),))
    assert D.validate(edge) == "edge", "an end that names no node"


@pytest.mark.parametrize(
    "text",
    ["a\udc80b", "a\x00b", "tab\there", "bell\x07"],
)
def test_validate_refuses_text_and_ids_no_text_call_can_take(text: str) -> None:
    """
    A lone surrogate raises in every ImGui text call and a NUL ends the string there
    (STYLE.md §3); no parsed SVG holds any C0 control in a run, so a list with one
    did not come from the parser.
    """
    run = D.TextRun(text, 0.0, 0.0, 12.0, 0, 0, D.LABEL, False, 0.0)
    assert D.validate(D.DisplayList(10.0, 10.0, (0.0, 0.0), (run,), (), ())) == "text content"
    node = D.HitNode(text, (0.0, 0.0, 1.0, 1.0), ())
    assert D.validate(D.DisplayList(10.0, 10.0, (0.0, 0.0), (), (node,), ())) == "node id"
    edge = D.HitEdge(text, (0.0, 0.0, 1.0, 1.0), (-1, -1))
    assert D.validate(D.DisplayList(10.0, 10.0, (0.0, 0.0), (), (), (edge,))) == "edge id"
    stats = D.DisplayList(10.0, 10.0, (0.0, 0.0), (), (), (), ((text, 1),))
    assert D.validate(stats) == "stats"


def test_the_parser_writes_a_control_an_attribute_carries_as_a_space() -> None:
    svg = (
        SVG_HEAD + '<g class="node" id="a&#9;b&#10;c"><rect width="10" height="10"/>'
        '<text x="1" y="5">x&#9;y</text></g></svg>'
    )
    dl = D.from_svg(svg)
    assert [n.id for n in dl.nodes] == ["a b c"]
    assert [i.text for i in dl.items if isinstance(i, D.TextRun)] == ["x y"]


# -- the wire: what crosses from the child, read without evaluating it ------------------


def _wire_list() -> D.DisplayList:
    return D.DisplayList(
        100.0,
        50.0,
        (1.0, 2.0),
        (
            _path(dash=(3.0, 3.0), fill_rgb=0x123456, node=0, edge=0),
            D.TextRun('é 😀 "q" \\', 5.0, 6.0, 12.0, 1, 1, D.LABEL, True, 30.0, 0, -1, 1, 2),
        ),
        (D.HitNode("n", (0.0, 0.0, 1.0, 1.0), ((0.0, 0.0, 1.0, 1.0),)),),
        (D.HitEdge("e", (0.0, 0.0, 1.0, 1.0), (0, -1)),),
        (("segments", 2),),
    )


def test_a_display_list_crosses_the_wire_unchanged_and_as_ascii_lines() -> None:
    dl = _wire_list()
    wire = D.encode(dl)
    assert wire.isascii() and wire.count(b"\n") == 1 + 2 + 1 + 1 - 1
    assert D.decode(wire) == dl
    assert D.decode(b"head line\n" + wire, len(b"head line\n")) == dl


@needs_merman
@pytest.mark.parametrize("name", ["sequence", "er", "flowchart", "class", "state", "board21"])
def test_each_golden_diagram_crosses_the_wire_unchanged(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    dl = _laid(SOURCES[name], monkeypatch)
    assert D.decode(D.encode(dl)) == dl


def _lines(dl: D.DisplayList) -> list[bytes]:
    return D.encode(dl).split(b"\n")


@pytest.mark.parametrize(
    "wire, why",
    [
        (lambda: b"\n".join(_lines(_wire_list())[:-1]), "fewer records than its header counts"),
        (lambda: D.encode(_wire_list()) + b"\n[]", "more records than its header counts"),
        (
            lambda: D.encode(_wire_list()).replace(b'"v":1', b'"v":2', 1),
            "not a display list of this version",
        ),
        (
            lambda: D.encode(_wire_list()).replace(b'"counts":[2,1,1]', b'"counts":[10001,1,1]'),
            "count",
        ),
        (
            lambda: D.encode(_wire_list()).replace(b'["p",', b'["x",'),
            "an item that is neither a path nor a text run",
        ),
        (lambda: D.encode(_wire_list()).replace(b'"e",[', b'["e"],['), "a field of the wrong type"),
        (
            lambda: D.encode(_wire_list()).replace(b"[0,-1]", b"[0.0,-1]"),
            "a field of the wrong type",
        ),
        (lambda: D.encode(_wire_list()).replace(b"5.0,6.0", b"NaN,6.0"), "coordinate"),
        (lambda: D.encode(_wire_list()).replace(b"5.0,6.0", b"1e999,6.0"), "coordinate"),
        (
            lambda: D.encode(_wire_list()).replace(b'"segments",2', b'"segments",true'),
            "a field of the wrong type",
        ),
        (lambda: b"[" * 100_000, "malformed (RecursionError)"),
        (lambda: b"\xff\xfe", "malformed (JSONDecodeError)"),
    ],
)
def test_a_wire_that_is_not_a_display_list_is_named_and_never_raised(wire: Any, why: str) -> None:
    assert D.decode(wire()) == why


def test_json_s_nan_and_infinity_do_not_get_past_the_finite_checks() -> None:
    """
    ``json.loads`` reads ``NaN`` and ``Infinity`` as floats; ``validate``'s finite
    checks are what refuse them, wherever they stand.
    """
    for field, at in ((b"100.0,50.0", "size"), (b"1.0,2.0", "origin"), (b"5.0,6.0", "coordinate")):
        for word in (b"NaN", b"Infinity", b"-Infinity"):
            wire = D.encode(_wire_list()).replace(field, word + field[field.index(b",") :], 1)
            assert D.decode(wire) == at, (field, word)


def test_a_record_past_the_longest_one_is_refused_before_it_is_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed: list[int] = []
    real = json.loads

    def loads(b: bytes) -> Any:
        parsed.append(len(b))
        return real(b)

    monkeypatch.setattr(json, "loads", loads)
    head = _lines(_wire_list())[0]
    long = b'["t","' + b"x" * D.MAX_LINE_BYTES + b'"]'
    assert D.decode(head + b"\n" + long) == "a record past the longest one may be"
    assert parsed == [len(head)], "only the header was parsed"


def test_the_wire_bounds_hold_a_list_at_every_bound_written_at_its_longest() -> None:
    """
    ``MAX_WIRE_BYTES`` is what the GUI process reads of a reply and ``MAX_LINE_BYTES``
    what it parses at once: a list at every count bound, every float at its longest
    JSON spelling and every character at its longest escape, fits both.
    """
    big = -2.2250738585072014e-308  # in range, and the longest repr a float has
    wide = "\U0001f600"  # one character, twelve bytes as ASCII-only JSON
    box = (big, big, big, big)
    paths = [
        D.PathItem(
            D.NODE,
            True,
            0,
            True,
            63.123456789012345,
            0,
            0,
            (1234.5678901234567,) * D.MAX_DASHES,
            "M" + "C" * (D.MAX_SEGS_PER_PATH - 1),
            (big,) * (2 + 6 * (D.MAX_SEGS_PER_PATH - 1)),
            box,
            D.MAX_NODES - 1,
            D.MAX_EDGES - 1,
            0xFFFFFF,
            0xFFFFFF,
        )
        for _ in range(D.MAX_SEGS_TOTAL // D.MAX_SEGS_PER_PATH)
    ]
    room = D.MAX_SEGS_TOTAL - sum(len(p.ops) for p in paths)
    runs = [
        D.TextRun(
            wide * (D.MAX_TEXT_CHARS // D.MAX_TEXT_RUNS),
            big,
            big,
            255.12345678901234,
            1,
            1,
            D.LABEL,
            False,
            1234.5678901234567,
            D.MAX_NODES - 1,
            D.MAX_EDGES - 1,
            0xFFFFFF,
            0xFFFFFF,
        )
        for _ in range(D.MAX_TEXT_RUNS)
    ]
    dots = [
        replace_path(paths[0], ops="M", xy=(big, big))
        for _ in range(min(room, D.MAX_ITEMS - len(paths) - len(runs)))
    ]
    nodes = tuple(
        D.HitNode(wide * D.MAX_ID_CHARS, box, (box,) * D.MAX_ATTACH) for _ in range(D.MAX_NODES)
    )
    edges = tuple(
        D.HitEdge(
            wide * D.MAX_ID_CHARS,
            (big,) * (2 * D.MAX_HIT_POINTS),
            (D.MAX_NODES - 1, D.MAX_NODES - 1),
        )
        for _ in range(D.MAX_EDGES)
    )
    stats = tuple((wide * D.MAX_ID_CHARS, 10**15) for _ in range(D.MAX_STATS))
    dl = D.DisplayList(
        D.MAX_SIDE, D.MAX_SIDE, (big, big), (*paths, *dots, *runs), nodes, edges, stats
    )
    assert D.validate(dl) is None
    wire = D.encode(dl)
    assert len(wire) <= D.MAX_WIRE_BYTES
    assert max(len(line) for line in wire.split(b"\n")) <= D.MAX_LINE_BYTES
    assert len(wire) > D.MAX_WIRE_BYTES // 2, "the bound is not far above what it bounds"


def replace_path(item: D.PathItem, **changed: Any) -> D.PathItem:
    return D.PathItem(**{**{f: getattr(item, f) for f in D.PathItem.__slots__}, **changed})


# -- colours: what is the author's -------------------------------------------------------


@pytest.mark.parametrize(
    "said, rgb",
    [
        ("#f96", 0xFF9966),
        ("#F96A", 0xFF9966),
        ("#ff9966", 0xFF9966),
        ("#ff996680", 0xFF9966),
        ("#bbf !important", 0xBBBBFF),
        ("red", 0xFF0000),
        ("RebeccaPurple", 0x663399),
        ("rgb(0, 128, 0)", 0x008000),
        ("rgb(0 128 0 / 50%)", 0x008000),
        ("rgba(100%,0%,0%,0.5)", 0xFF0000),
        ("hsl(120, 100%, 25%)", 0x008000),
        ("hsl(-120, 0%, 80%)", 0xCCCCCC),
        ("none", None),
        ("transparent", None),
        ("currentColor", None),
        ("url(#g)", None),
        ("#ggg", None),
        ("rgb(1,2)", None),
        ("", None),
        ("rgb(1e999,0,0)", None),
        ("rgb(0,-1e999%,0)", None),
        ("hsl(1e999,50%,50%)", None),
        ("hsl(-1e999deg,50%,50%)", None),
        ("hsl(0,1e999%,50%)", None),
    ],
)
def test_css_colours_parse_to_rgb(said: str, rgb: int | None) -> None:
    assert D.parse_colour(said) == rgb


@pytest.mark.parametrize(
    "said, alpha",
    [
        ("#fff", 1.0),
        ("#fff0", 0.0),
        ("#ffffff00", 0.0),
        ("#ff000080", 128 / 255),
        ("rgba(0,0,0,0.1)", 0.1),
        ("rgb(0 0 0 / 50%)", 0.5),
        ("hsla(0,0%,0%,0)", 0.0),
        ("rgba(0,0,0,1e999)", 1.0),
        ("red", 1.0),
        (None, 1.0),
    ],
)
def test_a_colours_alpha_is_read(said: str | None, alpha: float) -> None:
    assert D.opacity(said) == pytest.approx(alpha)


@needs_merman
def test_a_transparent_author_fill_is_no_fill_and_a_translucent_one_is_the_themes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``classDef ghost fill:#ffffff00`` is a node with no fill, not an opaque white
    box; ``#ff000080`` is drawn in the theme's node fill, since how it looks depends
    on the pane behind it.
    """
    source = (
        "flowchart LR\n  A[Ghost] --> B[Half]\n"
        "  classDef ghost fill:#ffffff00,stroke:#f00\n  class A ghost\n"
        "  classDef half fill:#ff000080\n  class B half\n"
    )
    dl = _laid(source, monkeypatch)
    shapes = {
        dl.nodes[i.node].id: (i.fill, i.fill_rgb, i.stroke_rgb)
        for i in dl.items
        if isinstance(i, D.PathItem) and i.role == D.NODE
    }
    assert shapes == {
        "flowchart-A-0": (False, D.NO_COLOUR, 0xFF0000),
        "flowchart-B-1": (True, D.NO_COLOUR, D.NO_COLOUR),
    }


def test_the_named_colours_are_the_sanitisers_and_css_values() -> None:
    assert set(D._NAMED) == set(NAMED_COLOURS) - {"transparent", "currentcolor"}
    pil = pytest.importorskip("PIL.ImageColor")
    for name, rgb in D._NAMED.items():
        r, g, b = pil.getrgb(name)[:3]
        assert rgb == (r << 16) | (g << 8) | b, name


def test_the_authors_colours_are_read_from_style_classdef_and_linkstyle_lines() -> None:
    source = (
        "flowchart LR\n"
        "    A[Start] --> B\n"
        "    classDef hot fill:#f96,stroke:rgb(0, 128, 0),stroke-width:4px,color:#fff\n"
        "  style A fill:#bbf,stroke:#f66,color:red\n"
        "    linkStyle 0 stroke:#ff3,stroke-width:2px\n"
        "    %% style B fill:#123456 is a comment, and a comment is not a style line\n"
        "    A --> C[fill:#abcdef]\n"
    )
    assert D.authored_colours(source) == D.Authored(
        classes={"hot": P(fill={0xFF9966}, stroke={0x008000}, color={0xFFFFFF})},
        ids={"A": P(fill={0xBBBBFF}, stroke={0xFF6666}, color={0xFF0000})},
        links={0: P(stroke={0xFFFF33})},
    )
    assert D.authored_colours("flowchart LR\n  A --> B\n") == D.NOTHING_AUTHORED


def P(fill: Any = (), stroke: Any = (), color: Any = ()) -> D.Paints:  # noqa: N802
    return D.Paints(frozenset(fill), frozenset(stroke), frozenset(color))


def test_each_statement_paints_what_it_names() -> None:
    said = D.authored_colours(
        "flowchart LR\n"
        "  classDef a,b fill:#abc\n"
        "  linkStyle 0,2 stroke:#123\n"
        "  linkStyle default stroke:#456,color:#789\n"
        "  linkStyle x stroke:#fff\n"
        "  style A fill:#f00;stroke:#00f\n"
    )
    assert said.classes == {"a": P(fill={0xAABBCC}), "b": P(fill={0xAABBCC})}
    assert said.links == {
        0: P(stroke={0x112233}),
        2: P(stroke={0x112233}),
        -1: P(stroke={0x445566}, color={0x778899}),
    }
    assert said.ids == {"A": P(fill={0xFF0000}, stroke={0x0000FF})}, "merman reads `;` within one"
    assert said.for_edge(2) == P(stroke={0x112233, 0x445566}, color={0x778899})
    assert said.for_edge(1) == said.links[-1]
    assert said.for_node(["node", "default", "b"], "A") == P(
        fill={0xAABBCC, 0xFF0000}, stroke={0x0000FF}
    )
    assert said.for_node(["node", "default"], "B") == D.NO_PAINTS


def test_a_colour_too_large_to_be_a_number_is_no_colour_and_not_a_failed_diagram() -> None:
    said = D.authored_colours("flowchart LR\n  A-->B\n  style A fill:rgb(1e999,0,0),stroke:#f00\n")
    assert said == D.Authored(ids={"A": P(stroke={0xFF0000})})


def test_a_style_statement_after_a_semicolon_is_read_and_background_color_is_not_color() -> None:
    """
    merman 0.9.1 applies ``A-->B; style A fill:#f00``: a ``;`` ends a statement as
    a line does. ``background-color`` is not the label ink ``color``.
    """
    said = D.authored_colours(
        "flowchart LR\n  A-->B; style A fill:#f00\n  B-->C;classDef x stroke:#0f0\n"
        "  style C background-color:#00f,color:#123456\n"
    )
    assert said == D.Authored(
        classes={"x": P(stroke={0x00FF00})},
        ids={"A": P(fill={0xFF0000}), "C": P(color={0x123456})},
    )


def test_a_colour_is_the_authors_only_inline_on_a_node_and_named_in_the_source() -> None:
    """
    merman writes its own palette into the same SVG, inline as well as in its
    stylesheet. A colour that the source's style lines do not name is merman's and
    is drawn by role, however it is written; the same colour named in the source is
    the author's. A label's colour counts only against ``color``.
    """
    svg = (
        SVG_HEAD + "<style>#merman .node rect{fill:#ECECFF}</style>"
        '<g class="node" id="merman-n-A"><rect width="20" height="20" '
        'style="fill:#FDF4FF !important; stroke:#E879F9"/>'
        '<text x="10" y="10" style="fill:#FDF4FF">A</text></g>'
        '<g class="node" id="merman-n-B" transform="translate(40,0)"><rect width="20" '
        'height="20" fill="#bbbbff"/></g></svg>'
    )

    def colours(authored: D.Authored) -> list[tuple[int, int] | int]:
        dl = D.from_svg(svg, authored)
        return [
            (i.fill_rgb, i.stroke_rgb) if isinstance(i, D.PathItem) else i.ink_rgb for i in dl.items
        ]

    none = D.NO_COLOUR
    assert colours(D.NOTHING_AUTHORED) == [(none, none), none, (none, none)]
    named = D.Authored(
        ids={"n-A": P(fill={0xFDF4FF}, stroke={0xE879F9}), "n-B": P(fill={0xBBBBFF})}
    )
    assert colours(named) == [
        (0xFDF4FF, 0xE879F9),
        none,
        (none, none),
    ], "a presentation attribute is not an inline declaration, and fill is not color"
    elsewhere = D.Authored(ids={"n-B": P(fill={0xFDF4FF}, stroke={0xE879F9}, color={0xFDF4FF})})
    assert colours(elsewhere) == [(none, none), none, (none, none)], "named for B, written on A"
    by_class = D.Authored(classes={"hot": P(fill={0xFDF4FF})})
    assert colours(by_class) == [(none, none), none, (none, none)], "A's g has no class hot"
    hot = D.from_svg(
        svg.replace('class="node" id="merman-n-A"', 'class="node hot" id="merman-n-A"'), by_class
    )
    assert hot.items[0].fill_rgb == 0xFDF4FF  # type: ignore[union-attr]
    inked = D.Authored(ids={"n-A": P(fill={0xFDF4FF}, color={0xFDF4FF})})
    dl = D.from_svg(svg, inked)
    run = next(i for i in dl.items if isinstance(i, D.TextRun))
    assert (run.ink_rgb, run.ground_rgb) == (0xFDF4FF, 0xFDF4FF), "its ground is its node's fill"


def test_an_edges_marker_takes_the_authors_stroke() -> None:
    svg = MARKED.replace(
        'class="flowchart-link" data-id="L_A_B"',
        'class="flowchart-link" data-id="L_A_B" style="stroke:#ff3"',
    )
    dl = D.from_svg(svg, D.Authored(links={0: P(stroke={0xFFFF33})}))
    other = D.from_svg(svg, D.Authored(links={1: P(stroke={0xFFFF33})}))
    assert all(i.stroke_rgb == D.NO_COLOUR for i in other.items if isinstance(i, D.PathItem))
    edge = next(i for i in dl.items if isinstance(i, D.PathItem) and i.role == D.EDGE)
    marker = next(i for i in dl.items if isinstance(i, D.PathItem) and i.role == D.MARKER)
    assert edge.stroke_rgb == marker.stroke_rgb == marker.fill_rgb == 0xFFFF33


# -- legibility ------------------------------------------------------------------------------


def test_a_legible_colour_is_left_as_written() -> None:
    assert D.legible(0x000000, 0xFFFFFF, D.TEXT_CONTRAST) == 0x000000
    assert D.legible(0xFF9966, 0x14161C, D.STROKE_CONTRAST) == 0xFF9966


@pytest.mark.parametrize(
    "ink, ground, minimum",
    [
        (0x333333, 0x14161C, D.TEXT_CONTRAST),  # merman's default ink on the dark pane
        (0xFFFFFF, 0xFF9966, D.TEXT_CONTRAST),  # white on an orange fill
        (0x777777, 0x808080, D.TEXT_CONTRAST),  # grey on grey: either way works
        (0x1A1D26, 0x14161C, D.STROKE_CONTRAST),
        (0xFFFF33, 0xF4F5F8, D.STROKE_CONTRAST),  # yellow line on the light pane
    ],
)
def test_an_illegible_colour_moves_the_least_way_that_reaches_the_threshold(
    ink: int, ground: int, minimum: float
) -> None:
    drawn = D.legible(ink, ground, minimum)
    assert D.contrast(drawn, ground) >= minimum
    assert drawn != ink
    # The least mix that reaches it: just over the threshold, not far past it, and
    # no nearer the ink than the least mix toward either pole, found by scanning.
    assert D.contrast(drawn, ground) < minimum + 0.25

    def distance(a: int, b: int) -> float:
        return math.dist(
            [(a >> s) & 0xFF for s in (16, 8, 0)], [(b >> s) & 0xFF for s in (16, 8, 0)]
        )

    nearest = min(
        distance(ink, D.mix(ink, pole, t / 512))
        for pole in (0xFFFFFF, 0x000000)
        for t in range(513)
        if D.contrast(D.mix(ink, pole, t / 512), ground) >= minimum
    )
    assert distance(ink, drawn) <= nearest + 1.0


def test_the_thresholds_are_wcags_for_text_and_for_graphics() -> None:
    assert D.TEXT_CONTRAST == 4.5 and D.STROKE_CONTRAST == 3.0
    # Black or white reaches 4.5 against any ground, so the guard always succeeds.
    for ground in (0x777777, 0x767676, 0x808080, 0x14161C, 0xF4F5F8, 0xFF0000):
        assert max(D.contrast(0xFFFFFF, ground), D.contrast(0x000000, ground)) >= 4.5


@pytest.mark.parametrize("name", ["dark", "light"])
def test_the_pane_guards_author_colours_against_its_theme_and_never_changes_a_fill(
    name: str,
) -> None:
    palette = theme.THEMES[name]
    ground = host._rgb(palette.bg)
    paints = [
        (host._FILL, D.NODE, 0x000000, D.NO_COLOUR, -1),  # an author's fill: as written
        (host._STROKE, D.EDGE, 0x222222, D.NO_COLOUR, -1),  # dark on dark
        (host._STROKE, D.EDGE, 0xFFFF33, D.NO_COLOUR, -1),  # yellow on light
        (host._INK, D.LABEL, D.NO_COLOUR, 0xFF9966, -1),  # the theme's text on an orange fill
        (host._INK, D.LABEL, 0xFFFFFF, D.NO_COLOUR, -1),  # white text on the pane
        (host._INK, D.LABEL, D.NO_COLOUR, D.NO_COLOUR, -1),  # the theme's own: untouched
        (host._STROKE, D.NODE, D.NO_COLOUR, D.NO_COLOUR, -1),
    ]
    drawn, notes = host.resolve(paints, palette, ground)
    roles = host.role_colours(palette, ground)
    assert drawn[0] == 0x000000
    assert D.contrast(drawn[1], ground) >= D.STROKE_CONTRAST
    assert D.contrast(drawn[2], ground) >= D.STROKE_CONTRAST
    assert D.contrast(drawn[3], 0xFF9966) >= D.TEXT_CONTRAST
    assert D.contrast(drawn[4], ground) >= D.TEXT_CONTRAST
    assert drawn[5] == roles[D.LABEL][1] and drawn[6] == roles[D.NODE][1]
    assert all(before != after for _, before, after, _ in notes)
    expected = {"dark": 2, "light": 2}[name]  # the dark stroke or the yellow, and white text
    assert sum(1 for what, *_ in notes if what == "stroke") == 1
    assert len(notes) >= expected


@pytest.mark.parametrize("name", ["dark", "light"])
def test_an_authors_ink_on_a_node_the_theme_fills_is_guarded_against_that_fill(
    name: str,
) -> None:
    """
    A label the author inked, in a node the author did not fill, sits on the theme's
    node fill and not on the pane: an ink legible on the pane but not on that fill
    is moved.
    """
    palette = theme.THEMES[name]
    ground = host._rgb(palette.bg)
    node_fill = host.role_colours(palette, ground)[D.NODE][0]
    inks = [
        rgb
        for v in range(256)
        for rgb in ((v << 16) | (v << 8) | v, (v << 16) | 0x40, v)
        if D.contrast(rgb, ground) >= D.TEXT_CONTRAST > D.contrast(rgb, node_fill)
    ]
    assert inks, "an ink legible on the pane and not on the node fill"
    ink = inks[len(inks) // 2]
    on_pane = (host._INK, D.LABEL, ink, D.NO_COLOUR, -1)
    on_node = (host._INK, D.LABEL, ink, D.NO_COLOUR, D.NODE)
    drawn, notes = host.resolve([on_pane, on_node], palette, ground)
    assert drawn[0] == ink and len(notes) == 1
    assert D.contrast(drawn[1], node_fill) >= D.TEXT_CONTRAST


def test_an_arrowhead_lands_on_the_same_colour_as_its_guarded_line() -> None:
    """
    linkStyle's colour reaches an edge's marker as its fill and its stroke. The fill
    of an arrowhead is a line's colour, not an area's: guarded as the line is.
    """
    palette = theme.THEMES["dark"]
    ground = host._rgb(palette.bg)
    paints = [
        (host._STROKE, D.EDGE, 0x15171D, D.NO_COLOUR, -1),
        (host._FILL, D.MARKER, 0x15171D, D.NO_COLOUR, -1),
        (host._STROKE, D.MARKER, 0x15171D, D.NO_COLOUR, -1),
        (host._FILL, D.NODE, 0x15171D, D.NO_COLOUR, -1),
    ]
    drawn, notes = host.resolve(paints, palette, ground)
    assert drawn[0] == drawn[1] == drawn[2] != 0x15171D
    assert drawn[3] == 0x15171D, "a node's fill is the author's, as written"
    assert len(notes) == 1, "one adjustment, said once"


def test_role_colours_follow_the_theme() -> None:
    dark = host.role_colours(theme.THEMES["dark"], host._rgb(theme.THEMES["dark"].bg))
    light = host.role_colours(theme.THEMES["light"], host._rgb(theme.THEMES["light"].bg))
    assert len(dark) == len(D.ROLE_NAMES) == len(light)
    assert dark[D.LABEL] != light[D.LABEL] and dark[D.EDGE] != light[D.EDGE]
    for roles, name in ((dark, "dark"), (light, "light")):
        ground = host._rgb(theme.THEMES[name].bg)
        assert D.contrast(roles[D.LABEL][1], ground) >= D.TEXT_CONTRAST, name
        assert D.contrast(roles[D.EDGE][1], ground) >= D.STROKE_CONTRAST, name


# -- merman 0.9.1: what is the author's, end to end ----------------------------------------


def _laid(source: str, monkeypatch: pytest.MonkeyPatch) -> D.DisplayList:
    monkeypatch.setattr(mermaid, "in_render_child", lambda: True)
    laid = mermaid.layout(source)
    assert isinstance(laid, Sanitised), laid
    return D.from_mermaid(source, laid.svg)


@needs_merman
@pytest.mark.parametrize("name", ["sequence", "er", "flowchart", "class", "state"])
def test_a_diagram_without_style_lines_has_no_authored_colour(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    merman's own palette (pastel participants and entities, its default ink) is in
    the SVG, and none of it is the author's: the diagram follows the theme.
    """
    dl = _laid(SOURCES[name], monkeypatch)
    for item in dl.items:
        if isinstance(item, D.PathItem):
            assert (item.fill_rgb, item.stroke_rgb) == (D.NO_COLOUR, D.NO_COLOUR), item
        else:
            assert (item.ink_rgb, item.ground_rgb) == (D.NO_COLOUR, D.NO_COLOUR), item


STYLED = (
    "flowchart LR\n"
    "    A[Start] --> B{Is it}\n"
    "    B -->|Yes| C[OK]\n"
    "    C --> D[(DB)]\n"
    "    classDef hot fill:#f96,stroke:#333,stroke-width:4px,color:#fff\n"
    "    class C hot\n"
    "    style A fill:#bbf,stroke:#f66,color:#f00\n"
    "    linkStyle 0 stroke:#ff3,stroke-width:2px\n"
)


@needs_merman
def test_style_classdef_and_linkstyle_reach_the_display_list_and_nothing_else_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dl = _laid(STYLED, monkeypatch)
    nodes = {n.id: k for k, n in enumerate(dl.nodes)}
    coloured: dict[str, set[tuple[str, int]]] = collections.defaultdict(set)
    for item in dl.items:
        owner = dl.nodes[item.node].id if item.node >= 0 else f"edge {item.edge}"
        if isinstance(item, D.PathItem):
            for what, rgb in (("fill", item.fill_rgb), ("stroke", item.stroke_rgb)):
                if rgb != D.NO_COLOUR:
                    coloured[f"{D.ROLE_NAMES[item.role]} {owner}"].add((what, rgb))
        elif item.ink_rgb != D.NO_COLOUR or item.ground_rgb != D.NO_COLOUR:
            coloured[f"text {item.text}"].add(("ink", item.ink_rgb))
            coloured[f"text {item.text}"].add(("ground", item.ground_rgb))
    assert "flowchart-A-0" in nodes and "flowchart-C-3" in nodes
    assert dict(coloured) == {
        "node flowchart-A-0": {("fill", 0xBBBBFF), ("stroke", 0xFF6666)},
        "node flowchart-C-3": {("fill", 0xFF9966), ("stroke", 0x333333)},
        "edge edge 0": {("stroke", 0xFFFF33)},
        "marker edge 0": {("fill", 0xFFFF33), ("stroke", 0xFFFF33)},
        "text Start": {("ink", 0xFF0000), ("ground", 0xBBBBFF)},
        "text OK": {("ink", 0xFFFFFF), ("ground", 0xFF9966)},
    }


@needs_merman
def test_the_pointer_on_a_sequence_note_or_message_is_on_no_participant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    merman draws each participant twice, top and bottom, under one id, so its extent
    runs the whole column. The points are read off merman 0.9.1's layout: the note
    inside API Gateway's column, a message label inside Auth Service's, and each
    participant's two actors.
    """
    dl = _laid(SOURCES["sequence"], monkeypatch)
    ids = {n.id: k for k, n in enumerate(dl.nodes)}
    assert D.node_at(dl, 380.0, 580.0) == -1, "the note over C and G"
    assert D.node_at(dl, 530.0, 158.0) == -1, "validate(credentials), in A's column"
    for name, x in (("C", 125.0), ("G", 387.0), ("A", 589.0), ("D", 935.0)):
        assert D.node_at(dl, x, 47.0) == ids[name]
        assert D.node_at(dl, x, 658.0) == ids[name]
        assert D.node_at(dl, x, 400.0) == -1, f"{name}'s lifeline"
    assert all(e.ends[0] >= 0 and e.ends[1] >= 0 for e in dl.edges), "messages still attach"


@needs_merman
def test_the_pointer_on_a_flowchart_node_is_on_it_and_not_on_its_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dl = _laid(SOURCES["flowchart"], monkeypatch)
    for k, n in enumerate(dl.nodes):
        if n.id.startswith("flowchart-"):
            b = n.attach[0]
            assert D.node_at(dl, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2) == k, n.id


@needs_merman
@pytest.mark.parametrize(
    "source, owner",
    [
        (
            "flowchart LR\n  A[Ink] --> B[Plain]\n  B --> C[Also plain]\n"
            "  classDef ink color:#28253D\n  class A ink\n",
            "flowchart-A-0",
        ),
        (
            "flowchart LR\n  A[Ink] --> B[Plain]\n  B --> C[Also plain]\n  style A color:#28253D\n",
            "flowchart-A-0",
        ),
        (
            "stateDiagram-v2\n  Ink --> Plain\n  classDef ink color:#28253D\n  class Ink ink\n",
            "state-Ink-0",
        ),
    ],
)
def test_an_author_colour_that_is_also_mermans_paints_only_what_its_statement_names(
    source: str, owner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``#28253D`` is merman 0.9.1's own label ink, written on every label. Named by a
    statement for one node, it is the author's on that node's label alone.
    """
    dl = _laid(source, monkeypatch)
    inked = {
        dl.nodes[i.node].id if i.node >= 0 else i.text
        for i in dl.items
        if isinstance(i, D.TextRun) and i.ink_rgb != D.NO_COLOUR
    }
    assert inked == {owner}


# -- the golden: merman 0.9.1's six diagrams ------------------------------------------------

SOURCES: dict[str, str] = {
    "flowchart": (
        "flowchart TD\n"
        "    A[User submits form] --> B{Valid input?}\n"
        "    B -->|Yes| C[Save to database]\n"
        "    B -->|No| D[Show validation errors]\n"
        "    D --> A\n"
        "    C --> E[(PostgreSQL)]\n"
        "    C --> F[Send confirmation email]\n"
        '    subgraph Background["Background jobs"]\n'
        "        F --> G[Queue: email-worker]\n"
        "        G --> H[SMTP relay]\n"
        "    end\n"
        "    H --> I([Done])\n"
    ),
    "sequence": (
        "sequenceDiagram\n"
        "    autonumber\n"
        "    participant C as Client\n"
        "    participant G as API Gateway\n"
        "    participant A as Auth Service\n"
        "    participant D as Database\n"
        "    C->>G: POST /login {user, password}\n"
        "    G->>A: validate(credentials)\n"
        "    A->>D: SELECT * FROM users WHERE name = ?\n"
        "    D-->>A: user row\n"
        "    alt password matches\n"
        "        A-->>G: 200 + JWT\n"
        "        G-->>C: Set-Cookie: session=...\n"
        "    else mismatch\n"
        "        A-->>G: 401 Unauthorized\n"
        "        G-->>C: 401\n"
        "    end\n"
        "    Note over C,G: The token expires after 15 minutes\n"
    ),
    "class": (
        "classDiagram\n"
        "    class Animal {\n"
        "        <<abstract>>\n"
        "        +String name\n"
        "        +int age\n"
        "        +makeSound()* void\n"
        "    }\n"
        "    class Dog {\n"
        "        +String breed\n"
        "        +fetch(item: Ball) bool\n"
        "    }\n"
        "    class Cat {\n"
        "        -int lives\n"
        "        +purr() void\n"
        "    }\n"
        "    class Owner {\n"
        "        +List~Animal~ pets\n"
        "        +adopt(a: Animal)\n"
        "    }\n"
        "    Animal <|-- Dog\n"
        "    Animal <|-- Cat\n"
        '    Owner "1" o-- "*" Animal : owns\n'
    ),
    "state": (
        "stateDiagram-v2\n"
        "    [*] --> Idle\n"
        "    Idle --> Running : start()\n"
        "    Running --> Paused : pause()\n"
        "    Paused --> Running : resume()\n"
        "    Running --> Failed : error\n"
        "    Failed --> Idle : reset()\n"
        "    Running --> [*] : complete\n"
        "    state Running {\n"
        "        [*] --> Fetching\n"
        "        Fetching --> Processing\n"
        "        Processing --> Fetching : more data\n"
        "    }\n"
        "    note right of Failed : Retries are exhausted\n"
    ),
    "er": (
        "erDiagram\n"
        "    CUSTOMER ||--o{ ORDER : places\n"
        "    ORDER ||--|{ LINE_ITEM : contains\n"
        '    PRODUCT ||--o{ LINE_ITEM : "ordered in"\n'
        "    CUSTOMER {\n"
        "        int id PK\n"
        "        string name\n"
        "        string email UK\n"
        "    }\n"
        "    ORDER {\n"
        "        int id PK\n"
        "        int customer_id FK\n"
        "        date created_at\n"
        "    }\n"
        "    LINE_ITEM {\n"
        "        int order_id FK\n"
        "        int product_id FK\n"
        "        int quantity\n"
        "    }\n"
        "    PRODUCT {\n"
        "        int id PK\n"
        "        string sku\n"
        "        decimal price\n"
        "    }\n"
    ),
    "board21": (
        "flowchart TD\n"
        '    t0["withdraw-task"]\n'
        '    t1["graph-core"]\n'
        '    t2["all-writers"]\n'
        '    t3["bus-graph-tools"]\n'
        '    t4["review-withdraw"]\n'
        '    t5["review-graph-core"]\n'
        '    t6["review-all-writers"]\n'
        '    t7["review-bus-tools"]\n'
        '    t8["record"]\n'
        '    t9["graph-gate"]\n'
        '    t10["digest-withdrawn"]\n'
        '    t11["graph-core-fix"]\n'
        '    t12["graph-core-fix-2"]\n'
        '    t13["withdraw-notice"]\n'
        '    t14["withdraw-fixes"]\n'
        '    t15["all-writers-fix"]\n'
        '    t16["review-fixes"]\n'
        '    t17["fix-tail"]\n'
        '    t18["escaped-dep-wording"]\n'
        '    t19["final-fixes"]\n'
        '    t20["record-fix"]\n'
        "    t0 --> t2\n"
        "    t1 --> t3\n"
        "    t2 --> t3\n"
        "    t0 --> t3\n"
        "    t0 --> t4\n"
        "    t1 --> t5\n"
        "    t2 --> t6\n"
        "    t3 --> t7\n"
        "    t3 --> t8\n"
        "    t4 --> t8\n"
        "    t5 --> t8\n"
        "    t6 --> t8\n"
        "    t7 --> t8\n"
        "    t0 --> t9\n"
        "    t1 --> t9\n"
        "    t2 --> t9\n"
        "    t3 --> t9\n"
        "    t8 --> t9\n"
        "    t4 --> t9\n"
        "    t5 --> t9\n"
        "    t6 --> t9\n"
        "    t7 --> t9\n"
        "    t0 --> t10\n"
        "    t5 --> t11\n"
        "    t3 --> t11\n"
        "    t5 --> t12\n"
        "    t11 --> t12\n"
        "    t0 --> t13\n"
        "    t2 --> t13\n"
        "    t13 --> t14\n"
        "    t3 --> t14\n"
        "    t2 --> t14\n"
        "    t6 --> t15\n"
        "    t3 --> t15\n"
        "    t13 --> t15\n"
        "    t14 --> t15\n"
        "    t14 --> t16\n"
        "    t15 --> t16\n"
        "    t15 --> t17\n"
        "    t17 --> t18\n"
        "    t16 --> t19\n"
        "    t8 --> t20\n"
    ),
}

GOLDEN: dict[str, dict[str, Any]] = {
    "flowchart": {
        "natural": (462.0, 860.0),
        "paths": {"cluster": 1, "edge": 9, "edge_label": 2, "marker": 9, "node": 10},
        "runs": {"edge_label": 2, "label": 12},
        "labels": [
            "Background jobs",
            "Yes",
            "No",
            "Send confirmation",
            "email",
            "Queue: email-worker",
            "SMTP relay",
            "User submits form",
            "Valid input?",
            "Save to database",
            "Show validation",
            "errors",
            "PostgreSQL",
            "Done",
        ],
        "nodes": [
            "Background",
            "flowchart-F-11",
            "flowchart-G-13",
            "flowchart-H-15",
            "flowchart-A-0",
            "flowchart-B-1",
            "flowchart-C-3",
            "flowchart-D-5",
            "flowchart-E-9",
            "flowchart-I-17",
        ],
        "edges": [
            ("flowchart-A-0", "flowchart-B-1"),
            ("flowchart-B-1", "flowchart-C-3"),
            ("flowchart-B-1", "flowchart-D-5"),
            ("flowchart-D-5", "flowchart-A-0"),
            ("flowchart-C-3", "flowchart-E-9"),
            ("flowchart-C-3", "Background"),
            ("flowchart-F-11", "Background"),
            ("flowchart-G-13", "Background"),
            ("flowchart-H-15", "flowchart-I-17"),
        ],
        "fills": 21,
        "dashed": 0,
    },
    "sequence": {
        "natural": (1060.0, 706.0),
        "paths": {"edge": 8, "frame": 6, "lifeline": 4, "marker": 16, "node": 8, "note": 1},
        "runs": {"badge_text": 8, "label": 20},
        "labels": [
            "Database",
            "Auth Service",
            "API Gateway",
            "Client",
            "Database",
            "Auth Service",
            "API Gateway",
            "Client",
            "alt",
            "[password matches]",
            "[mismatch]",
            "The token expires after 15 minutes",
            "POST /login {user, password}",
            "1",
            "validate(credentials)",
            "2",
            "SELECT * FROM users WHERE name = ?",
            "3",
            "user row",
            "4",
            "200 + JWT",
            "5",
            "Set-Cookie: session=...",
            "6",
            "401 Unauthorized",
            "7",
            "401",
            "8",
        ],
        "nodes": ["D", "A", "G", "C"],
        "edges": [
            ("C", "G"),
            ("G", "A"),
            ("A", "D"),
            ("D", "A"),
            ("A", "G"),
            ("G", "C"),
            ("A", "G"),
            ("G", "C"),
        ],
        "fills": 26,
        "dashed": 10,
    },
    "class": {
        "natural": (337.7, 657.0),
        "paths": {"edge": 3, "edge_label": 1, "marker": 3, "node": 16},
        "runs": {"edge_label": 1, "label": 16},
        "labels": [
            "owns",
            "1",
            "*",
            "«abstract»",
            "Animal",
            "+String name",
            "+int age",
            "+makeSound() : void",
            "Dog",
            "+String breed",
            "+fetch(item: Ball) : bool",
            "Cat",
            "-int lives",
            "+purr() : void",
            "Owner",
            "+List<Animal> pets",
            "+adopt(a: Animal)",
        ],
        "nodes": ["classId-Animal-0", "classId-Dog-1", "classId-Cat-2", "classId-Owner-3"],
        "edges": [
            ("classId-Animal-0", "classId-Dog-1"),
            ("classId-Animal-0", "classId-Cat-2"),
            ("classId-Owner-3", "classId-Animal-0"),
        ],
        "fills": 5,
        "dashed": 0,
    },
    "state": {
        "natural": (415.1, 885.0),
        "paths": {
            "cluster": 2,
            "edge": 11,
            "edge_label": 7,
            "marker": 10,
            "node": 9,
            "note": 2,
            "solid": 2,
        },
        "runs": {"edge_label": 7, "label": 8},
        "labels": [
            "Running",
            "start()",
            "pause()",
            "resume()",
            "error",
            "reset()",
            "complete",
            "more data",
            "Idle",
            "Paused",
            "Failed",
            "Fetching",
            "Processing",
            "Retries are",
            "exhausted",
        ],
        "nodes": [
            "state-Running-7",
            "state-root_start-0",
            "state-Idle-5",
            "state-Paused-3",
            "state-Failed-10",
            "state-root_end-6",
            "state-Running_start-7",
            "state-Fetching-9",
            "state-Processing-9",
            "state-Failed----note-10",
        ],
        "edges": [
            ("state-root_start-0", "state-Idle-5"),
            ("state-Idle-5", "state-Running-7"),
            ("state-Running-7", "state-Paused-3"),
            ("state-Paused-3", "state-Running-7"),
            ("state-Running-7", "state-Failed-10"),
            ("state-Failed-10", "state-Idle-5"),
            ("state-Running-7", "state-root_end-6"),
            ("state-Running_start-7", "state-Running-7"),
            ("state-Fetching-9", "state-Running-7"),
            ("state-Processing-9", "state-Running-7"),
            ("state-Failed-10", "state-Failed----note-10"),
        ],
        "fills": 29,
        "dashed": 1,
    },
    "er": {
        "natural": (402.1, 695.0),
        "paths": {"edge": 3, "edge_label": 3, "marker": 8, "node": 52},
        "runs": {"edge_label": 3, "label": 35},
        "labels": [
            "places",
            "contains",
            "ordered in",
            "CUSTOMER",
            "int",
            "id",
            "PK",
            "string",
            "name",
            "string",
            "email",
            "UK",
            "ORDER",
            "int",
            "id",
            "PK",
            "int",
            "customer_id",
            "FK",
            "date",
            "created_at",
            "LINE_ITEM",
            "int",
            "order_id",
            "FK",
            "int",
            "product_id",
            "FK",
            "int",
            "quantity",
            "PRODUCT",
            "int",
            "id",
            "PK",
            "string",
            "sku",
            "decimal",
            "price",
        ],
        "nodes": ["entity-CUSTOMER-0", "entity-ORDER-1", "entity-LINE_ITEM-2", "entity-PRODUCT-3"],
        "edges": [
            ("entity-CUSTOMER-0", "entity-ORDER-1"),
            ("entity-ORDER-1", "entity-LINE_ITEM-2"),
            ("entity-PRODUCT-3", "entity-LINE_ITEM-2"),
        ],
        "fills": 23,
        "dashed": 0,
    },
    "board21": {
        "natural": (1433.8, 871.0),
        "paths": {"edge": 42, "marker": 42, "node": 21},
        "runs": {"label": 21},
        "labels": [
            "withdraw-task",
            "graph-core",
            "all-writers",
            "bus-graph-tools",
            "review-withdraw",
            "review-graph-core",
            "review-all-writers",
            "review-bus-tools",
            "record",
            "graph-gate",
            "digest-withdrawn",
            "graph-core-fix",
            "graph-core-fix-2",
            "withdraw-notice",
            "withdraw-fixes",
            "all-writers-fix",
            "review-fixes",
            "fix-tail",
            "escaped-dep-wording",
            "final-fixes",
            "record-fix",
        ],
        "nodes": [
            "flowchart-t0-0",
            "flowchart-t1-1",
            "flowchart-t2-2",
            "flowchart-t3-3",
            "flowchart-t4-4",
            "flowchart-t5-5",
            "flowchart-t6-6",
            "flowchart-t7-7",
            "flowchart-t8-8",
            "flowchart-t9-9",
            "flowchart-t10-10",
            "flowchart-t11-11",
            "flowchart-t12-12",
            "flowchart-t13-13",
            "flowchart-t14-14",
            "flowchart-t15-15",
            "flowchart-t16-16",
            "flowchart-t17-17",
            "flowchart-t18-18",
            "flowchart-t19-19",
            "flowchart-t20-20",
        ],
        "edges": [
            ("flowchart-t0-0", "flowchart-t2-2"),
            ("flowchart-t1-1", "flowchart-t3-3"),
            ("flowchart-t2-2", "flowchart-t3-3"),
            ("flowchart-t0-0", "flowchart-t3-3"),
            ("flowchart-t0-0", "flowchart-t4-4"),
            ("flowchart-t1-1", "flowchart-t5-5"),
            ("flowchart-t2-2", "flowchart-t6-6"),
            ("flowchart-t3-3", "flowchart-t7-7"),
            ("flowchart-t3-3", "flowchart-t8-8"),
            ("flowchart-t4-4", "flowchart-t8-8"),
            ("flowchart-t5-5", "flowchart-t8-8"),
            ("flowchart-t6-6", "flowchart-t8-8"),
            ("flowchart-t7-7", "flowchart-t8-8"),
            ("flowchart-t0-0", "flowchart-t9-9"),
            ("flowchart-t1-1", "flowchart-t9-9"),
            ("flowchart-t2-2", "flowchart-t9-9"),
            ("flowchart-t3-3", "flowchart-t9-9"),
            ("flowchart-t8-8", "flowchart-t9-9"),
            ("flowchart-t4-4", "flowchart-t9-9"),
            ("flowchart-t5-5", "flowchart-t9-9"),
            ("flowchart-t6-6", "flowchart-t9-9"),
            ("flowchart-t7-7", "flowchart-t9-9"),
            ("flowchart-t0-0", "flowchart-t10-10"),
            ("flowchart-t5-5", "flowchart-t11-11"),
            ("flowchart-t3-3", "flowchart-t11-11"),
            ("flowchart-t5-5", "flowchart-t12-12"),
            ("flowchart-t11-11", "flowchart-t12-12"),
            ("flowchart-t0-0", "flowchart-t13-13"),
            ("flowchart-t2-2", "flowchart-t13-13"),
            ("flowchart-t13-13", "flowchart-t14-14"),
            ("flowchart-t3-3", "flowchart-t14-14"),
            ("flowchart-t2-2", "flowchart-t14-14"),
            ("flowchart-t6-6", "flowchart-t15-15"),
            ("flowchart-t3-3", "flowchart-t15-15"),
            ("flowchart-t13-13", "flowchart-t15-15"),
            ("flowchart-t14-14", "flowchart-t15-15"),
            ("flowchart-t14-14", "flowchart-t16-16"),
            ("flowchart-t15-15", "flowchart-t16-16"),
            ("flowchart-t15-15", "flowchart-t17-17"),
            ("flowchart-t17-17", "flowchart-t18-18"),
            ("flowchart-t16-16", "flowchart-t19-19"),
            ("flowchart-t8-8", "flowchart-t20-20"),
        ],
        "fills": 63,
        "dashed": 0,
    },
}


@needs_merman
def test_an_er_entitys_authored_fill_is_on_every_row_its_text_sits_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    merman 0.9.1 paints ``style CUSTOMER fill:#003`` on the entity's box and on
    each attribute row, so a label's ``ground_rgb`` (its node's authored fill) is
    the surface it is drawn on. Were a row left in the theme's fill, the guard
    would measure that row's text against the wrong colour.
    """
    source = (
        "erDiagram\n  CUSTOMER ||--o{ ORDER : places\n"
        "  CUSTOMER {\n    string name\n    string email\n  }\n"
        "  style CUSTOMER fill:#003\n"
    )
    dl = _laid(source, monkeypatch)
    customer = next(k for k, n in enumerate(dl.nodes) if "CUSTOMER" in n.id)
    filled = [i for i in dl.items if isinstance(i, D.PathItem) and i.node == customer and i.fill]
    labels = [i for i in dl.items if isinstance(i, D.TextRun) and i.node == customer]
    assert len(filled) >= 3 and all(i.fill_rgb == 0x000033 for i in filled)
    assert [i.text for i in labels] == ["CUSTOMER", "string", "name", "string", "email"]
    assert all(i.ground_rgb == 0x000033 for i in labels)


@needs_merman
@pytest.mark.parametrize("name", ["sequence", "er", "flowchart", "class", "state", "board21"])
def test_a_held_display_list_costs_about_word_bytes_a_word(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    What the held layouts' budget assumes a word of ``weight`` costs, against what
    the list decoded on arrival takes, by tracemalloc.
    """
    import tracemalloc

    wire = D.encode(_laid(SOURCES[name], monkeypatch))
    tracemalloc.start()
    try:
        dl = D.decode(wire)
        held = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    assert isinstance(dl, D.DisplayList)
    assert 0.75 * D.WORD_BYTES <= held / dl.weight() <= 1.25 * D.WORD_BYTES


PATTERNS: dict[str, tuple[str, list[tuple[str, tuple[float, ...], float]]]] = {
    "flowchart": (
        "flowchart LR\n  A -.-> B\n  B ==> C\n  C --> D\n  D -.- E\n",
        [
            ("L_A_B_0", (2.0, 2.0), 1.5),  # edge-pattern-dotted
            ("L_B_C_0", (), 3.5),  # edge-thickness-thick
            ("L_C_D_0", (), 1.5),
            ("L_D_E_0", (2.0, 2.0), 1.5),
        ],
    ),
    "class": (
        "classDiagram\n  A ..> B\n  C ..|> D\n  E --> F\n  G .. H\n",
        [
            ("id_A_B_1", (3.0, 3.0), 1.5),  # edge-pattern-dashed
            ("id_C_D_2", (3.0, 3.0), 1.5),
            ("id_E_F_3", (), 1.5),
            ("id_G_H_4", (3.0, 3.0), 1.5),
        ],
    ),
    "er": (
        "erDiagram\n  A ||..o{ B : soft\n  C ||--o{ D : hard\n",
        [
            ("id_entity-A-0_entity-B-1_0", (3.0, 3.0), 1.5),
            ("id_entity-C-2_entity-D-3_1", (), 1.5),
        ],
    ),
    "state": (
        "stateDiagram-v2\n  A --> B\n  note right of A : n\n",
        [("edge0", (), 1.5), ("A-A----note-1", (5.0, 5.0), 1.5)],  # note-edge over dashed
    ),
}


@needs_merman
@pytest.mark.parametrize("name", list(PATTERNS))
def test_the_golden_dash_and_width_of_each_edge_pattern(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Every edge pattern merman 0.9.1 marks by class (``_CLASS_DASH``, the thick
    class), with the dash and width it is drawn at: a renamed class draws solid or
    thin, and fails here.
    """
    found = mermaid.version()
    if found != PINNED:
        pytest.fail(RE_BASELINE.format(found=found))
    source, want = PATTERNS[name]
    dl = _laid(source, monkeypatch)
    got = [
        (dl.edges[i.edge].id, i.dash, i.width)
        for i in dl.items
        if isinstance(i, D.PathItem) and i.role == D.EDGE
    ]
    assert got == want


@needs_merman
def test_merman_is_the_version_the_golden_was_taken_on() -> None:
    found = mermaid.version()
    assert found == PINNED, RE_BASELINE.format(found=found)


@needs_merman
@pytest.mark.parametrize("name", list(GOLDEN))
def test_the_golden_per_role_counts_edge_attachment_and_label_texts(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = mermaid.version()
    if found != PINNED:
        pytest.fail(RE_BASELINE.format(found=found))
    dl = _laid(SOURCES[name], monkeypatch)
    want = GOLDEN[name]
    paths = [i for i in dl.items if isinstance(i, D.PathItem)]
    runs = [i for i in dl.items if isinstance(i, D.TextRun)]
    got = {
        "natural": (round(dl.width, 1), round(dl.height, 1)),
        "paths": dict(sorted(collections.Counter(D.ROLE_NAMES[p.role] for p in paths).items())),
        "runs": dict(sorted(collections.Counter(D.ROLE_NAMES[r.role] for r in runs).items())),
        "labels": [r.text for r in runs],
        "nodes": [n.id for n in dl.nodes],
        "edges": [tuple(dl.nodes[k].id if k >= 0 else None for k in e.ends) for e in dl.edges],
        "fills": sum(1 for p in paths if p.fill),
        "dashed": sum(1 for p in paths if p.dash),
    }
    for key in want:
        assert got[key] == want[key], f"{name}: {key}"
    assert all(None not in ends for ends in got["edges"]), "every edge attaches at both ends"
    assert D.validate(dl) is None
    # The host fills each subpath on its own and ignores the fill rule (no holes):
    # true to merman 0.9.1's output only while no filled path has two closed loops.
    for p in paths:
        if p.fill:
            loops = [pl for pl, _ in D.flatten_units(p.ops, p.xy, 1.0) if len(pl) > 2]
            assert len(loops) <= 1, f"{name}: a filled path with a hole the host would fill"


# -- what a list may cost to build -------------------------------------------------------


def _bent(n: int, x: float = 100.0, y: float = 100.0, reach: float = 3000.0) -> D.PathItem:
    """
    A stroked path of ``n`` cubics bent far enough that each takes the step cap.
    """
    xy = [x, y]
    for _ in range(n):
        xy += [x + reach, y - reach, x - reach, y + reach, x + 10.0, y + 10.0]
    return D.PathItem(
        D.EDGE, False, 0, True, 1.5, 0, 0, (), "M" + "C" * n, tuple(xy), (0.0, 0.0, 4000.0, 4000.0)
    )


def _costly(points: int) -> D.DisplayList:
    per = D.path_points("M" + "C" * 40, _bent(40).xy, 1.0)
    count = -(-points // per)
    return D.DisplayList(4000.0, 4000.0, (0.0, 0.0), (_bent(40),) * count, (), ())


@pytest.mark.parametrize("seed", range(40))
def test_the_counted_points_are_never_fewer_than_the_pane_flattens(seed: int) -> None:
    """
    ``drawn_points`` is a bound only if no width the pane draws at flattens more:
    random paths of every kind, at the largest scale and below it.
    """
    import random

    r = random.Random(seed)
    ops = "M" + "".join(r.choice("LCQZM") for _ in range(r.randrange(1, 60)))
    xy = tuple(r.uniform(-2000, 6000) for _ in range(sum(D._OP_ARGS[op] for op in ops)))
    counted = D.path_points(ops, xy, 1.0)
    for scale in (1.0, 0.7, 0.31, 0.05):
        made = sum(len(pl) for pl, _ in D.flatten_units(ops, xy, scale))
        assert made <= counted, (scale, made, counted)


def test_a_list_over_the_point_budget_is_refused_and_one_under_it_is_not() -> None:
    under = _costly(D.MAX_DRAWN_POINTS - 3_000)
    over = _costly(D.MAX_DRAWN_POINTS + 1)
    assert D.drawn_points(under) <= D.MAX_DRAWN_POINTS < D.drawn_points(over)
    assert D.validate(under) is None
    assert D.validate(over) == "too costly to draw"


def test_a_list_too_costly_to_draw_is_refused_on_receipt() -> None:
    """
    What a taken-over child could send: within every other bound, and refused by
    the I/O thread before the frame thread builds anything from it.
    """
    from pptmstr.figures import render

    over = _costly(D.MAX_DRAWN_POINTS + 1)
    assert sum(len(i.ops) for i in over.items if isinstance(i, D.PathItem)) <= D.MAX_SEGS_TOTAL
    got = render.received("k", D.encode(over))
    assert got == render.Failed("k", "display list refused: too costly to draw")


def test_a_list_drawn_narrower_than_a_unit_is_counted_at_the_scale_it_is_drawn_at() -> None:
    """
    The pane draws a figure at least one unit of ImGui wide, so one whose natural
    width is under a unit is drawn larger than natural, and counted so.
    """
    item = _bent(4, 0.001, 0.001, 0.003)
    tiny = D.DisplayList(0.01, 0.01, (0.0, 0.0), (item,), (), ())
    assert D.drawn_points(tiny) == D.path_points(item.ops, item.xy, 100.0)
    assert D.drawn_points(tiny) > D.path_points(item.ops, item.xy, 1.0)


def _flowchart(n: int) -> str:
    import random

    r = random.Random(3)
    lines = ["flowchart TD"]
    for i in range(1, n):
        lines.append(f"    n{r.randrange(i)}[Node {r.randrange(i)} label] --> n{i}[Node {i} label]")
    for _ in range(n // 2):
        a, b = sorted(r.sample(range(n), 2))
        lines.append(f"    n{a} -.-> n{b}")
    return "\n".join(lines)


def _sequence(n: int) -> str:
    import random

    r = random.Random(5)
    lines = ["sequenceDiagram", *(f"    participant P{i}" for i in range(12))]
    for k in range(n):
        a, b = r.sample(range(12), 2)
        lines.append(f"    P{a}->>P{b}: message {k}")
        if k % 7 == 0:
            lines.append(f"    Note over P{a},P{b}: note {k}")
    return "\n".join(lines)


def _classes(n: int) -> str:
    import random

    r = random.Random(6)
    lines = ["classDiagram"]
    for i in range(n):
        lines += [f"    class C{i} {{", "      +int field", "      +method() void", "    }"]
    lines += [f"    C{r.randrange(i)} <|-- C{i}" for i in range(1, n)]
    return "\n".join(lines)


def _states(n: int) -> str:
    import random

    r = random.Random(7)
    lines = ["stateDiagram-v2", "    [*] --> S0"]
    lines += [f"    S{r.randrange(i)} --> S{i}: go {i}" for i in range(1, n)]
    return "\n".join([*lines, f"    S{n - 1} --> [*]"])


def _entities(n: int) -> str:
    import random

    r = random.Random(8)
    lines = ["erDiagram"]
    for i in range(n):
        lines += [f"    E{i} {{", "        int id PK", "        string name", "    }"]
    lines += [f"    E{r.randrange(i)} ||--o{{ E{i} : has" for i in range(1, n)]
    return "\n".join(lines)


# The largest of each family's shape here that merman 0.9.1 and the sanitiser accept,
# found by bisection to within a few (each refused at 110, 130, 97, 160 and 43:
# the size cap, the element cap or merman's own limits; 2026-10-22,
# shots/native-cost/merman_max.py): what the budget was set from.
LARGEST = {
    "flowchart": (_flowchart, 105),
    "sequence": (_sequence, 125),
    "class": (_classes, 95),
    "state": (_states, 155),
    "er": (_entities, 42),
}


@needs_merman
@pytest.mark.parametrize("family", list(LARGEST))
def test_the_largest_diagram_merman_lays_out_in_each_family_still_draws(
    family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = mermaid.version()
    if found != PINNED:
        pytest.fail(RE_BASELINE.format(found=found))
    make, n = LARGEST[family]
    dl = _laid(make(n), monkeypatch)
    assert D.validate(dl) is None
    assert D.drawn_points(dl) <= D.MAX_DRAWN_POINTS // 1.5, "headroom under the budget"
    assert sum(len(i.ops) for i in dl.items if isinstance(i, D.PathItem)) <= D.MAX_SEGS_TOTAL // 2
    assert all(len(e.points) // 2 < D.MAX_HIT_POINTS for e in dl.edges), "no edge is thinned"


# -- linkStyle counts edges in merman's order, chained and & syntax included --------------

LINKED = {
    "chained": ("flowchart LR\n  A-->B-->C\n", [("A", "B"), ("B", "C")]),
    "and": ("flowchart LR\n  A & B --> C\n", [("A", "C"), ("B", "C")]),
    "and on both sides": (
        "flowchart LR\n  A & B --> C & D\n",
        [("A", "C"), ("A", "D"), ("B", "C"), ("B", "D")],
    ),
    "mixed": (
        "flowchart LR\n  X --> Y\n  A & B --> C --> D\n  Y --> A\n",
        [("X", "Y"), ("A", "C"), ("B", "C"), ("C", "D"), ("Y", "A")],
    ),
}
LINK_COLOURS = (0xFF0000, 0x00FF00, 0x0000FF, 0xFF00FF, 0x00FFFF)


@needs_merman
@pytest.mark.parametrize("name", list(LINKED))
def test_link_style_n_paints_the_nth_edge_mermaid_counts(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Mermaid counts ``A-->B-->C`` as two edges, A-B then B-C, and ``A & B --> C & D``
    as four, each start with each end in turn; ``linkStyle N`` paints the Nth. Each
    edge here is styled its own colour, and each must be drawn in its number's.
    """
    found = mermaid.version()
    if found != PINNED:
        pytest.fail(RE_BASELINE.format(found=found))
    source, ends = LINKED[name]
    source += "".join(f"  linkStyle {i} stroke:#{LINK_COLOURS[i]:06x}\n" for i in range(len(ends)))
    dl = _laid(source, monkeypatch)
    drawn = [
        (tuple(dl.nodes[k].id.split("-")[1] for k in dl.edges[i.edge].ends), i.stroke_rgb)
        for i in dl.items
        if isinstance(i, D.PathItem) and i.role == D.EDGE
    ]
    assert drawn == list(zip(ends, LINK_COLOURS[: len(ends)], strict=True))
