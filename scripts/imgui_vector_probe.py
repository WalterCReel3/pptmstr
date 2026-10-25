#!/usr/bin/env python3
"""
What Dear ImGui's own ImDrawList does with vector input, measured headless.

    .venv/bin/python scripts/imgui_vector_probe.py

For notes/vector-rendering/imgui-vector-survey.md. One ImGui context with no
renderer and no window system: every figure is read back from the draw list's
own vertex and index buffers, so nothing here says what pixels a GPU would make.

  sizes      ImDrawVert / ImDrawIdx in this build, and whether a 100k-vertex
             polyline stays in one draw command (it can only with 32-bit indices).
  fill       AddConcavePolyFilled on a square with a square hole, given the one
             way a simple-polygon API can take a hole (a zero-width bridge, a
             "keyhole"), and on a self-intersecting pentagram. Coverage is a
             point-in-triangle test at the centre of every unit cell, with
             anti-aliased fill turned off so only the fill triangles count.
  strokes    vertices and triangles per segment of a thin polyline, per
             thickness, with each anti-aliasing flag combination.
  throughput Python-side cost of handing N path vertices to the draw list, per
             entry point: a path_line_to loop, add_polyline from a list,
             add_convex/concave_poly_filled, and prim_* writes from Python.
"""

from __future__ import annotations

import ctypes
import math
import statistics
import time
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar, cast

from imgui_bundle import imgui

Point = tuple[float, float]
Vert = tuple[float, float, int]
Mesh = tuple[list[Vert], list[int]]
T = TypeVar("T")
WHITE = imgui.IM_COL32(255, 255, 255, 255)
LIST_FLAGS = imgui.ImDrawListFlags_


def vecs(points: Sequence[Point] | Sequence[imgui.ImVec2]) -> list[imgui.ImVec2Like]:
    """
    The same list, typed as the binding's parameter. A cast and no copy, so a
    timed call converts the tuples itself, as an untyped caller's would.
    """
    return cast(list[imgui.ImVec2Like], points)


def make_context() -> None:
    """
    A headless context: display size, delta time and a renderer that says it
    manages textures, which is all NewFrame needs.
    """
    imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(4000.0, 4000.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.backend_flags |= imgui.BackendFlags_.renderer_has_vtx_offset


def in_frame(body: Callable[[imgui.ImDrawList], T]) -> T:
    """
    Run `body` against a fresh window's draw list inside one frame.
    """
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
    imgui.set_next_window_size(imgui.ImVec2(3900.0, 3900.0))
    imgui.begin("probe", None, imgui.WindowFlags_.no_decoration)
    try:
        return body(imgui.get_window_draw_list())
    finally:
        imgui.end()
        imgui.end_frame()


def vertices(dl: imgui.ImDrawList, start: int = 0) -> list[Vert]:
    """
    (x, y, alpha) of every vertex from `start`, read from the raw buffer.
    """
    n = dl.vtx_buffer.size()
    raw = (ctypes.c_ubyte * (n * imgui.VERTEX_SIZE)).from_address(dl.vtx_buffer.data_address())
    out = []
    for i in range(start, n):
        base = i * imgui.VERTEX_SIZE
        x, y = ctypes.c_float.from_buffer(raw, base + imgui.VERTEX_BUFFER_POS_OFFSET).value, (
            ctypes.c_float.from_buffer(raw, base + imgui.VERTEX_BUFFER_POS_OFFSET + 4).value
        )
        col = ctypes.c_uint32.from_buffer(raw, base + imgui.VERTEX_BUFFER_COL_OFFSET).value
        out.append((x, y, col >> 24))
    return out


def indices(dl: imgui.ImDrawList, start: int = 0) -> list[int]:
    """
    Every index from `start`, read at the build's index width.
    """
    n = dl.idx_buffer.size()
    ctype = {2: ctypes.c_uint16, 4: ctypes.c_uint32}[imgui.INDEX_SIZE]
    return [int(i) for i in (ctype * n).from_address(dl.idx_buffer.data_address())[start:]]


def captured(dl: imgui.ImDrawList, draw: Callable[[], None]) -> Mesh:
    """
    The vertices and the (vertex-relative) triangle indices that `draw` added.
    """
    v0, i0 = dl.vtx_buffer.size(), dl.idx_buffer.size()
    draw()
    verts = vertices(dl, v0)
    first = min(indices(dl, i0), default=v0)
    # Indices are numbered from the command's vertex offset; rebase on the first
    # vertex this call wrote, which is the lowest index it used.
    return verts, [i - first for i in indices(dl, i0)]


def sizes() -> dict[str, object]:
    def body(dl: imgui.ImDrawList) -> dict[str, object]:
        cmds0 = dl.cmd_buffer.size()
        n = 100_000
        pts = [(10.0 + (i % 1000) * 3.0, 10.0 + (i // 1000) * 3.0) for i in range(n)]
        dl.flags = 0
        dl.add_polyline(vecs(pts), WHITE, 1.0, 0)
        idx = indices(dl)
        return {
            "INDEX_SIZE": imgui.INDEX_SIZE,
            "VERTEX_SIZE": imgui.VERTEX_SIZE,
            "idx_buffer type": type(dl.idx_buffer).__name__,
            "polyline vertices": dl.vtx_buffer.size(),
            "max index": max(idx),
            "draw commands added": dl.cmd_buffer.size() - cmds0,
        }

    return in_frame(body)


def inside(p: Point, a: Point, b: Point, c: Point) -> bool:
    def cross(o: Point, u: Point, v: Point) -> float:
        return (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])

    d1, d2, d3 = cross(a, b, p), cross(b, c, p), cross(c, a, p)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def coverage(verts: list[Vert], tris: list[int], points: Iterable[Point]) -> dict[Point, int]:
    """
    How many triangles cover each point: 0 is a gap, 2 or more is overdraw,
    which a translucent fill would show.
    """
    triangles = [
        tuple((verts[tris[k + j]][0], verts[tris[k + j]][1]) for j in range(3))
        for k in range(0, len(tris), 3)
    ]
    return {p: sum(inside(p, *t) for t in triangles) for p in points}


def grid(x0: int, y0: int, x1: int, y1: int) -> list[Point]:
    # Off the cell centre on purpose: a centre lies on the diagonal of any
    # integer-cornered square, and a point on an edge shared by two triangles
    # would read as overdraw.
    return [(x + 0.31, y + 0.57) for y in range(y0, y1) for x in range(x0, x1)]


def fill_case(name: str, polygon: Sequence[Point], expect: Callable[[Point], bool]) -> None:
    def body(dl: imgui.ImDrawList) -> Mesh:
        dl.flags = 0
        return captured(dl, lambda: dl.add_concave_poly_filled(vecs(polygon), WHITE))

    verts, tris = in_frame(body)
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    cells = grid(int(min(xs)) - 2, int(min(ys)) - 2, int(max(xs)) + 2, int(max(ys)) + 2)
    cov = coverage(verts, tris, cells)
    wrong_gap = sum(1 for p in cells if expect(p) and cov[p] == 0)
    wrong_ink = sum(1 for p in cells if not expect(p) and cov[p] > 0)
    over = sum(1 for p in cells if cov[p] > 1)
    want = sum(1 for p in cells if expect(p))
    print(
        f"  {name}: {len(polygon)} points -> {len(verts)} vertices, {len(tris) // 3} triangles;"
        f" cells expected inked {want}, missing {wrong_gap}, inked where empty {wrong_ink},"
        f" covered twice or more {over}"
    )


def fills() -> None:
    # Screen coordinates (y down); clockwise as seen on screen, which is what
    # ImGui's AA fringe expects. The bridge runs along y=50 from x=0 to x=30.
    outer = [(10.0, 60.0), (10.0, 10.0), (110.0, 10.0), (110.0, 110.0), (10.0, 110.0)]
    hole = [(40.0, 60.0), (40.0, 80.0), (80.0, 80.0), (80.0, 40.0), (40.0, 40.0)]
    keyhole = outer + [(10.0, 60.0)] + hole + [(40.0, 60.0)]

    def ring(p: Point) -> bool:
        return 10 <= p[0] <= 110 and 10 <= p[1] <= 110 and not (40 < p[0] < 80 and 40 < p[1] < 80)

    fill_case("square with keyhole-bridged hole (duplicate bridge vertices)", keyhole, ring)
    keyhole_open = outer + hole
    fill_case("square with hole, bridge not closed (outer then hole once)", keyhole_open, ring)

    def plain(p: Point) -> bool:
        return 10 <= p[0] <= 110 and 10 <= p[1] <= 110

    fill_case("plain square (control)", outer[1:] + outer[:1], plain)

    star = [
        (60.0 + 50.0 * math.sin(k * 4 * math.pi / 5), 60.0 - 50.0 * math.cos(k * 4 * math.pi / 5))
        for k in range(5)
    ]
    star_poly = [(x, y) for x, y in star]

    def star_nonzero(p: Point) -> bool:
        # Nonzero fills the pentagon at the centre too; inside the convex hull of
        # the star's outline, computed by winding number.
        return winding(p, star_poly) != 0

    def star_evenodd(p: Point) -> bool:
        return winding(p, star_poly) % 2 != 0

    fill_case("pentagram (self-intersecting), judged against nonzero", star_poly, star_nonzero)
    fill_case("pentagram (self-intersecting), judged against even-odd", star_poly, star_evenodd)


def opacity(verts: list[Vert], tris: list[int], p: Point) -> float:
    """
    The opacity at `p` after every triangle that covers it is blended over the
    one before, each at its vertex alpha interpolated: "over" compositing.
    """
    clear = 1.0
    for k in range(0, len(tris), 3):
        (ax, ay, aa), (bx, by, ba), (cx, cy, ca) = (verts[tris[k + j]] for j in range(3))
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if den == 0:
            continue
        u = ((by - cy) * (p[0] - cx) + (cx - bx) * (p[1] - cy)) / den
        v = ((cy - ay) * (p[0] - cx) + (ax - cx) * (p[1] - cy)) / den
        w = 1.0 - u - v
        if min(u, v, w) >= 0:
            clear *= 1.0 - (u * aa + v * ba + w * ca) / 255.0
    return 1.0 - clear


def seam() -> None:
    """
    The keyhole again with anti-aliased fill on: the fringe runs along both
    sides of the bridge, which lies inside the ink.
    """
    outer = [(10.0, 60.0), (10.0, 10.0), (110.0, 10.0), (110.0, 110.0), (10.0, 110.0)]
    hole = [(40.0, 60.0), (40.0, 80.0), (80.0, 80.0), (80.0, 40.0), (40.0, 40.0)]
    keyhole = outer + [(10.0, 60.0)] + hole + [(40.0, 60.0)]

    def body(dl: imgui.ImDrawList) -> Mesh:
        dl.flags = int(LIST_FLAGS.anti_aliased_fill)
        return captured(dl, lambda: dl.add_concave_poly_filled(vecs(keyhole), WHITE))

    verts, tris = in_frame(body)
    for label, pt in (
        ("on the bridge (25, 60)", (25.0, 60.0)),
        ("solid ink (25, 30)", (25.0, 30.0)),
    ):
        print(f"  AA fill on, opacity {label}: {opacity(verts, tris, pt):.3f}")


def winding(p: Point, poly: Sequence[Point]) -> int:
    w = 0
    for (x0, y0), (x1, y1) in zip(poly, list(poly[1:]) + [poly[0]], strict=True):
        if y0 <= p[1] < y1 and (x1 - x0) * (p[1] - y0) - (p[0] - x0) * (y1 - y0) > 0:
            w += 1
        elif y1 <= p[1] < y0 and (x1 - x0) * (p[1] - y0) - (p[0] - x0) * (y1 - y0) < 0:
            w -= 1
    return w


def strokes() -> None:
    segments = 100
    pts = [(10.0 + i * 7.0, 10.0 + (i % 2) * 5.0) for i in range(segments + 1)]
    combos = {
        "no AA": 0,
        "AA lines": int(LIST_FLAGS.anti_aliased_lines),
        "AA lines + use tex": int(
            LIST_FLAGS.anti_aliased_lines | LIST_FLAGS.anti_aliased_lines_use_tex
        ),
    }
    for thickness in (0.5, 1.0, 1.5, 2.0, 4.0):
        row = []
        for label, flags in combos.items():

            def body(
                dl: imgui.ImDrawList, flags: int = flags, thickness: float = thickness
            ) -> Mesh:
                dl.flags = flags
                return captured(dl, lambda: dl.add_polyline(vecs(pts), WHITE, thickness, 0))

            verts, tris = in_frame(body)
            alpha0 = sum(1 for v in verts if v[2] == 0)
            row.append(
                f"{label}: {len(verts) / segments:.1f} vtx/{len(tris) / 3 / segments:.1f} tri"
                f" per seg ({alpha0} alpha-0 fringe vtx)"
            )
        print(f"  thickness {thickness}: " + "; ".join(row))


def timed(body: Callable[[imgui.ImDrawList], None], repeats: int = 7) -> float:
    """
    Median seconds of `body` alone, each run in a frame of its own.
    """
    samples: list[float] = []
    for _ in range(repeats):

        def once(dl: imgui.ImDrawList) -> float:
            t = time.perf_counter()
            body(dl)
            return time.perf_counter() - t

        samples.append(in_frame(once))
    return float(statistics.median(samples))


def throughput() -> None:
    def circle(n: int) -> list[Point]:
        return [
            (
                500.0 + 400.0 * math.cos(2 * math.pi * k / n),
                500.0 + 400.0 * math.sin(2 * math.pi * k / n),
            )
            for k in range(n)
        ]

    def spikes(n: int) -> list[Point]:
        # Alternating radii: every other vertex is reflex, so ear clipping has to
        # search rather than take the first ear it tries.
        return [
            (
                500.0 + (400.0 if k % 2 else 250.0) * math.cos(2 * math.pi * k / n),
                500.0 + (400.0 if k % 2 else 250.0) * math.sin(2 * math.pi * k / n),
            )
            for k in range(n)
        ]

    def zigzag(n: int) -> list[Point]:
        return [
            (10.0 + (k % 3000) * 1.2, 10.0 + (k // 3000) * 4.0 + (k % 2) * 2.0) for k in range(n)
        ]

    def line_to_loop(pts: list[Point]) -> Callable[[imgui.ImDrawList], None]:
        def run(dl: imgui.ImDrawList) -> None:
            for p in pts:
                dl.path_line_to(p)
            dl.path_stroke(WHITE, 1.0, 0)

        return run

    def prim_tris(pts: list[Point]) -> Callable[[imgui.ImDrawList], None]:
        # One triangle per input vertex, written from Python: the cost floor of
        # a Python-side tessellator that hands ImGui finished triangles.
        uv = imgui.get_font_tex_uv_white_pixel()

        def run(dl: imgui.ImDrawList) -> None:
            n = len(pts) - 2
            dl.prim_reserve(n * 3, n * 3)
            for k in range(n):
                dl.prim_vtx(pts[k], uv, WHITE)
                dl.prim_vtx(pts[k + 1], uv, WHITE)
                dl.prim_vtx(pts[k + 2], uv, WHITE)

        return run

    cases: list[
        tuple[str, Callable[[int], Callable[[imgui.ImDrawList], None]], tuple[int, ...]]
    ] = [
        (
            "path_line_to loop + path_stroke",
            lambda n: line_to_loop(zigzag(n)),
            (1_000, 10_000, 100_000),
        ),
        (
            "add_polyline(list of tuples)",
            lambda n: (lambda pts: lambda dl: dl.add_polyline(vecs(pts), WHITE, 1.0, 0))(zigzag(n)),
            (1_000, 10_000, 100_000),
        ),
        (
            "add_polyline(list of ImVec2)",
            lambda n: (lambda pts: lambda dl: dl.add_polyline(pts, WHITE, 1.0, 0))(
                [imgui.ImVec2(*p) for p in zigzag(n)]
            ),
            (1_000, 10_000, 100_000),
        ),
        (
            "add_convex_poly_filled",
            lambda n: (lambda pts: lambda dl: dl.add_convex_poly_filled(vecs(pts), WHITE))(
                circle(n)
            ),
            (1_000, 10_000, 100_000),
        ),
        (
            "add_convex_poly_filled(list of ImVec2)",
            lambda n: (lambda pts: lambda dl: dl.add_convex_poly_filled(pts, WHITE))(
                [imgui.ImVec2(*p) for p in circle(n)]
            ),
            (1_000, 10_000, 100_000),
        ),
        (
            "add_concave_poly_filled(ImVec2), spiky star",
            lambda n: (lambda pts: lambda dl: dl.add_concave_poly_filled(pts, WHITE))(
                [imgui.ImVec2(*p) for p in spikes(n)]
            ),
            (250, 1_000, 4_000),
        ),
        (
            "add_concave_poly_filled (convex input)",
            lambda n: (lambda pts: lambda dl: dl.add_concave_poly_filled(vecs(pts), WHITE))(
                circle(n)
            ),
            (250, 1_000, 4_000),
        ),
        (
            "prim_reserve + prim_vtx from Python (3 vtx per input pt)",
            lambda n: prim_tris(zigzag(n)),
            (1_000, 10_000),
        ),
    ]
    for name, make, ns in cases:
        cells = []
        for n in ns:
            run = make(n)
            s = timed(run)
            cells.append(f"N={n}: {s * 1e3:.2f} ms ({s / n * 1e9:.0f} ns/pt)")
        print(f"  {name}: " + "; ".join(cells))


def main() -> None:
    make_context()
    print(f"imgui {imgui.get_version()}")
    print("sizes:")
    for k, v in sizes().items():
        print(f"  {k}: {v}")
    print("fill (AddConcavePolyFilled, AA fill off):")
    fills()
    seam()
    print("strokes (AddPolyline, open, flags=0):")
    strokes()
    print("throughput (median of 7 frames, time inside the call(s) only):")
    throughput()


if __name__ == "__main__":
    main()
