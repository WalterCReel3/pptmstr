"""
A committed ```` ```mermaid ```` fence drawn as a figure in its block's place: natively,
in the pane's own colours, from a bounded display list.

``views._captioned`` asks ``mark`` about a fence once it may host one: settled
documents always, a stream only for a fence it has committed or after it closed
(``views._Captioned``). A fence is marked (``FigureMark``) and the view's host
dispatcher answers it with a ``FigureWidget``; one refused at ``mark`` is never
marked and stays code, under a line in the warn ink saying why. A ```` ```svg ````
fence is not a figure (planning record §8): it is code, as any unlisted language is.

**A mermaid fence is marked without being laid out**: merman runs in the render
child, and the SVG it makes is sanitised and parsed into a display list there
(``render._laid``, ``displaylist``). The pane receives only that list, checked
against every bound on arrival, and never SVG or the source. Until it arrives the
figure stands at ``PLACEHOLDER_HEIGHT`` and jumps once, to its real height, when it
does; the list is then held by the fence's text and the merman version
(``_State.layouts``), so a re-hosted figure answers its real height at once, and by
the mark that asked for it, so a figure on screen never loses it to the bound on
the others. Where merman is not installed the fence is refused at ``mark`` and
stays code; a merman error arrives later and is drawn as a failure, below.

**Drawn natively, with no texture and no card** (planning record §11). Paths go to
the window's draw list as polylines and convex or concave fills, labels in the
pane's own prose face; nothing is rasterised. Colours are the current theme's, by
role (node, edge, note, ...), so a diagram follows a theme switch at no cost, and
the diagram stands on the pane itself. A colour the diagram's author set (``style``,
``classDef``, ``linkStyle``) is drawn as written, with one guard: an author's text
colour, or the theme's text over an author's fill, that falls under
``displaylist.TEXT_CONTRAST`` against what is behind it is moved to the nearest
colour that reaches it, and an author's stroke under ``STROKE_CONTRAST`` against
the pane likewise; a fill is never changed. Each adjustment is logged once per
figure and theme. The thresholds' reasons are with them.

**Geometry is built once per drawn scale and font** (``_Geometry``): curves
flattened, dashes cut, fills wound, labels measured, all in pixels from the
figure's corner. Each frame then culls by the clip rectangle and draws; the
draw list moves the built vertices to the figure's origin in one pass
(``shade_verts_transform_pos``), so scrolling builds nothing. **A width change
rebuilds at most one figure a frame** (``_may_rebuild``), the one that has waited
longest: a drag across a reply with many diagrams cost 3-16 ms a frame in Python
when every figure rebuilt at once (``probe-displaylist.md`` §4d). The others draw
their last geometry scaled to the new width meanwhile, which is still vectors,
not a stretched image.

**Hover lights a node and its edges; a click reports the node** to
``node_clicked``, which only logs. What a click should do is the operator's
decision, not yet taken.

**The height is the display list's**: ``min(w, natural_w) * natural_h /
natural_w``, answered at once once the list is held.

**Results are taken between frames** (``pump``, from the app's pre-new-frame
callback), every frame whether or not a figure is on screen: that is also where the
render process's timeout is enforced, so a hostile layout is stopped with nothing
drawing.

**A layout that fails** leaves the fence's source on screen, under the reason in
the warn ink, drawn here by ImGui since the block is already a host's by then. A
failure the renderer says would happen again (``render.Failed.transient`` false:
merman refused it, a bound) is kept for the content and not asked for again. One
the machine may have caused (the child died, ran out of time or memory, did not
start) is asked for once more after ``RETRY_AFTER_S``; a second such failure is
kept as the first kind is.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import displaylist as dlm
from . import fences, mermaid, render
from .displaylist import DisplayList, PathItem, TextRun
from .sanitise import Refused

RETRY_AFTER_S = 5.0
"""
How long after a transient failure a figure is asked for again: past a render
process's restart, and long enough that a machine that killed one child for memory
has had a moment to recover.
"""

PLACEHOLDER_HEIGHT = 120.0
"""
The height a mermaid figure stands at until the render child has laid it out: its
natural size is known only after layout, so it jumps once, to its real height, when
the layout arrives (planning record §3.6). A re-hosted figure whose layout is held
answers its real height at once.
"""

LAYOUT_BUDGET_WORDS = (64 << 20) // dlm.WORD_BYTES
"""
What the held display lists may add up to, in ``DisplayList.weight``: 64 MiB as
held (``displaylist.WORD_BYTES``), least recently read first out. The golden
diagrams weigh 1-5 thousand words each (40-180 KiB) and a 100-node flowchart 25
thousand (0.9 MiB), so this is several hundred typical diagrams. A list at every
bound weighs 3.3 million (109 MiB) and is held alone, past the budget, since the
newest list is never evicted.

Outside the budget: a figure on screen keeps its own list once it has read it
(``FigureMark.display``), so this bounds what a re-hosting finds, and a list
evicted before its figure's next read is asked for again; and each figure's
built geometry (``_Geometry``), about as large as its list (0.8 MiB for the
100-node flowchart at 900 px), held by the widget and gone with it.
"""

LABEL_OVERFLOW = 1.15
"""
How far past merman's label box a label may run before it is set smaller. merman
measures with its own metrics, narrower than the pane's face (``mermaid.visible_labels``),
and shrinking every label that ran a pixel over made sizes uneven from one box to
the next.
"""

HIGHLIGHT_WIDTH = 1.5
"""
How much wider a hovered node's outline and its edges are drawn, in pixels.
"""

MAX_DASHES_DRAWN = 16_384
"""
The most dash pieces one figure is cut into at one scale; a dashed stroke past the
budget is drawn solid. What bounds the build where the display list's own bounds
do not: 3,000 long dashed paths, within them, took 12 s to cut on the frame thread
(2026-10-22). A 100-node flowchart drawn 900 px wide needs about 4,500.
"""

MIN_DASH_PERIOD_PX = 4.0
"""
The shortest a dash pattern's period (one dash and one gap) is cut at on screen; a
shorter one is stretched to it, its proportions kept. Below about two pixels each,
an anti-aliased line one pixel wide does not show a dash and a gap but a mottled
line, and every dash is a draw call: a 100-node flowchart's 50 dotted edges cut
at merman's 3-unit dashes made 23,000 of them at any scale, which took 35-99 ms to
build and 9 ms a frame to draw at 900 px wide (2026-10-22, ``shots/native-fix``).
"""

_MARKS_KEPT = 64
_FAILURES_KEPT = 256
_NOTED_KEPT = 256
# The first element of a layout request's key.
_LAYOUT = "layout"


class FigureMark(str):
    """
    The info string ``views._captioned`` gives a fence that is drawn as a figure.

    Empty, as a block that names no language has, so nothing captions it or colours
    it; it carries the info string as written and what the figure is drawn from. A
    subclass and not a word, as ``views._Warned`` is: the library's reader gives every
    fence a plain ``str``, so no fence can be written that is taken for one.

    A mark holds the fence's ``source``, and ``sha`` is of the source and the merman
    version: its display list is made in the render child and held by ``sha``
    (``_State.layouts``), so ``display`` is ``None`` until then and a mark made again
    for the same text finds it at once.
    """

    said: str
    sha: str
    source: str
    _display: DisplayList | None

    def __new__(cls, said: str, source: str, sha: str) -> FigureMark:
        made = super().__new__(cls, "")
        made.said = said
        made._display = None
        made.source = source
        made.sha = sha
        return made

    @property
    def display(self) -> DisplayList | None:
        """
        The display list the figure is drawn from, or ``None`` while the fence has not
        been laid out. A list found is kept by the mark from then on, so the bound
        on the held layouts cannot take it from a figure on screen.
        """
        if self._display is None:
            self._display = _STATE.layouts.get(self.sha)
        return self._display


_word = fences.word


def is_figure_fence(said: str) -> bool:
    """
    Whether a fence's info string asks for a figure: its first word is a figure row
    of the table of drawn fences (``fences.FIGURE_WORDS``: ``mermaid``).
    """
    return _word(said) in fences.FIGURE_WORDS


_MARKS: OrderedDict[tuple[str, str], FigureMark | Refused] = OrderedDict()


def mark(said: str, text: str) -> FigureMark | Refused:
    """
    What a fence whose info string asks for a figure becomes: marked, or refused
    with the reason it stays code. Kept for the last few texts, because a stream made
    again (``Stream._forget``) reads every fence again.

    A fence is only marked: merman runs in the render child, never here. It is refused
    here only where merman is not installed (``mermaid.EXTRA_REASON``), which is read
    from metadata without importing it.
    """
    key = (said, text)
    held = _MARKS.get(key)
    if held is None:
        held = _mermaid_mark(said, text)
        _MARKS[key] = held
        while len(_MARKS) > _MARKS_KEPT:
            _MARKS.popitem(last=False)
    else:
        _MARKS.move_to_end(key)
    return held


def _mermaid_mark(said: str, text: str) -> FigureMark | Refused:
    version = mermaid.version()
    if version is None:
        return Refused(mermaid.missing_reason())
    keyed = f"merman {version}\0{text}".encode("utf-8", "surrogatepass")
    return FigureMark(said, text, hashlib.sha256(keyed).hexdigest())


def refusal_line(reason: str, said: str) -> str:
    """
    The line set above a fence that is not drawn as a figure.
    """
    return f"{_word(said) or 'figure'} not drawn: {reason}"


def fence_text(said: str, text: str) -> str:
    """
    The fence as Markdown, info string included: what a copy of the figure gives.

    Its marks are three backticks, or one more than the longest run of backticks
    in the text, so the copy reads back as the same fence.
    """
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    marks = "`" * max(3, longest + 1)
    return f"{marks}{said}\n{text}\n{marks}"


def _log_click(sha: str, node_id: str) -> None:
    from ..log import LOG

    LOG.info("figures", f"node {node_id!r} clicked in figure {sha[:10]}")


node_clicked: Callable[[str, str], None] = _log_click
"""
What a click on a diagram's node reports to, with the figure's sha and the node's
id. It only logs: what a click should do (select the task, open the file, ...) is
an operator decision not yet taken, and this is the one place it will be wired.
"""


# Held layouts ----------------------------------------------------------


class _Layouts:
    """
    Display lists by their mark's sha, bounded by their ``weight``, the least recently
    read evicted first: a read refreshes an entry.
    """

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self._held: OrderedDict[str, tuple[DisplayList, int]] = OrderedDict()
        self._words = 0

    def __len__(self) -> int:
        return len(self._held)

    def __contains__(self, sha: object) -> bool:
        return sha in self._held

    def get(self, sha: str) -> DisplayList | None:
        held = self._held.get(sha)
        if held is None:
            return None
        self._held.move_to_end(sha)
        return held[0]

    def put(self, sha: str, laid: DisplayList) -> None:
        old = self._held.pop(sha, None)
        if old is not None:
            self._words -= old[1]
        weight = laid.weight()
        self._held[sha] = (laid, weight)
        self._words += weight
        while self._words > self.budget and len(self._held) > 1:
            _, (_, evicted) = self._held.popitem(last=False)
            self._words -= evicted

    def clear(self) -> None:
        self._held.clear()
        self._words = 0


@dataclass
class _State:
    # Failures shown in place of the figure, by sha: kept for good.
    failures: OrderedDict[str, str] = field(default_factory=OrderedDict)
    # Layouts whose first transient failure is waiting out its back-off: when they
    # may be asked again, by sha.
    backoff: OrderedDict[str, float] = field(default_factory=OrderedDict)
    # Layouts asked again after a transient failure; another one is kept for good.
    retried: OrderedDict[str, None] = field(default_factory=OrderedDict)
    # Mermaid diagrams laid out, by their mark's sha (source and merman version).
    layouts: _Layouts = field(default_factory=lambda: _Layouts(LAYOUT_BUDGET_WORDS))
    # Mermaid diagrams asked of the render child and not yet answered, by sha.
    laying: set[str] = field(default_factory=set)
    # The frame a figure last rebuilt its geometry in, and the figures waiting to,
    # each with the frame it started waiting and the frame it last asked.
    rebuilt_at: int = -1
    stale: dict[int, tuple[int, int]] = field(default_factory=dict)
    # Colour adjustments already logged: (sha, palette, ground).
    noted: OrderedDict[tuple[str, str, int], None] = field(default_factory=OrderedDict)
    renderer: Callable[[], render.Renderer] = render.renderer
    clock: Callable[[], float] = time.monotonic


_STATE = _State()


def pump() -> None:
    """
    Once a frame, between frames (the app's pre-new-frame callback): take what the
    renderer has finished (which is also where it stops a request past its time),
    and hold it.

    Never raises. A result that cannot be taken is that figure's failure and does
    not cost the others.
    """
    state = _STATE
    try:
        results = state.renderer().poll()
    except Exception:
        return
    for result in results:
        key = result.key
        if not (isinstance(key, tuple) and len(key) == 2 and key[0] == _LAYOUT):
            continue
        sha = key[1]
        state.laying.discard(sha)
        if isinstance(result, render.Laid):
            state.layouts.put(sha, result.display)
            state.backoff.pop(sha, None)
        elif isinstance(result, render.Failed):
            _fail(state, sha, result)


def _fail(state: _State, sha: str, result: render.Failed) -> None:
    """
    A layout that failed: backed off once if the machine may have caused it, else
    kept, and the figure shows its source under the reason.
    """
    if result.transient and sha not in state.retried:
        _keep(state.backoff, sha, state.clock() + RETRY_AFTER_S)
    else:
        state.backoff.pop(sha, None)
        _keep(state.failures, sha, result.reason)


def _keep(held: OrderedDict[Any, Any], sha: Any, value: Any, kept: int = _FAILURES_KEPT) -> None:
    held[sha] = value
    held.move_to_end(sha)
    while len(held) > kept:
        held.popitem(last=False)


def _may_ask(sha: str) -> bool:
    """
    Whether a layout may be asked of the renderer now: not while its back-off from
    a transient failure runs. The ask that ends one is its one retry.
    """
    state = _STATE
    at = state.backoff.get(sha)
    if at is None:
        return True
    if state.clock() < at:
        return False
    del state.backoff[sha]
    _keep(state.retried, sha, None)
    return True


def _may_rebuild(who: int, frame: int) -> bool:
    """
    Whether the figure ``who``, which has geometry at another scale, may rebuild it
    in ``frame``: one figure a frame, the one that has waited longest among those
    that asked in this frame or the last. One that may not keeps its place.
    """
    state = _STATE
    since, _ = state.stale.get(who, (frame, frame))
    state.stale[who] = (since, frame)
    if state.rebuilt_at == frame:
        return False
    live = {k: v for k, v in state.stale.items() if v[1] >= frame - 1}
    state.stale = live
    if since > min(v[0] for v in live.values()):
        return False
    state.rebuilt_at = frame
    del state.stale[who]
    return True


def release_all() -> None:
    """
    Let go of every layout and failure held. For application exit, and for tests.
    """
    _STATE.failures.clear()
    _STATE.backoff.clear()
    _STATE.retried.clear()
    _STATE.layouts.clear()
    _STATE.laying.clear()
    _STATE.stale.clear()
    _STATE.noted.clear()
    _STATE.rebuilt_at = -1


# Colours ----------------------------------------------------------------------------


def _rgb(colour: Any) -> int:
    """
    A ``theme.Color`` or an ``ImVec4`` as ``0xRRGGBB``.
    """
    v = getattr(colour, "vec4", colour)
    r, g, b = (max(0, min(255, round(float(c) * 255))) for c in (v.x, v.y, v.z))
    return (r << 16) | (g << 8) | b


def role_colours(palette: Any, ground: int) -> tuple[tuple[int, int], ...]:
    """
    ``(fill, stroke)`` as ``0xRRGGBB`` for each role of ``displaylist.ROLE_NAMES``,
    from ``palette`` over a pane whose background is ``ground``. A diagram with no
    colours of its author's is drawn in these alone.
    """
    mix = dlm.mix
    text, accent, warn = _rgb(palette.text), _rgb(palette.accent), _rgb(palette.warn)
    border, dim = _rgb(palette.border), _rgb(palette.text_dim)
    ink = mix(ground, text, 0.70)
    return (
        (mix(ground, accent, 0.14), mix(ground, text, 0.55)),  # node
        (mix(ground, text, 0.05), border),  # cluster
        (ink, ink),  # edge
        (ink, ink),  # marker
        (text, text),  # label
        (ground, text),  # edge label: its knockout, its ink
        (mix(ground, warn, 0.16), mix(ground, warn, 0.75)),  # note
        (mix(ground, text, 0.08), dim),  # frame
        (border, mix(ground, text, 0.35)),  # lifeline
        (mix(ground, text, 0.80), mix(ground, text, 0.80)),  # solid
        (ground, ground),  # badge text, on a marker-coloured badge
        (ground, ink),  # hollow marker: the pane's ground, edge ink
    )


# A paint, as the geometry names it: (kind, role, the author's colour, the author's
# fill behind it, the role whose theme fill is behind it or -1 for the pane).
# Resolved to a packed colour once per palette and ground.
_FILL, _STROKE, _INK, _KNOCK = range(4)
Paint = tuple[int, int, int, int, int]


def resolve(
    paints: list[Paint], palette: Any, ground: int
) -> tuple[list[int], list[tuple[str, int, int, float]]]:
    """
    Each paint as ``0xRRGGBB`` for ``palette`` over ``ground``, and every adjustment
    the legibility guard made: ``(what, as written, as drawn, contrast as written)``.

    An author's fill is drawn as written. An author's stroke under
    ``STROKE_CONTRAST`` against the pane, and a label whose ink or ground is an
    author's under ``TEXT_CONTRAST`` against its ground (the author's fill, else
    the theme's fill of the shape it sits on, else the pane), are moved to the
    nearest colour that reaches it (``displaylist.legible``).
    A marker's fill is its edge's stroke colour (an arrowhead), so it is guarded as
    a stroke and lands on the same colour as its line.
    """
    roles = role_colours(palette, ground)
    out: list[int] = []
    notes: list[tuple[str, int, int, float]] = []
    for kind, role, rgb, under, surface in paints:
        fill, stroke = roles[role]
        if kind == _FILL and role == dlm.MARKER and rgb != dlm.NO_COLOUR:
            kind = _STROKE
        if kind in (_FILL, _KNOCK):
            out.append(fill if rgb == dlm.NO_COLOUR else rgb)
            continue
        if kind == _STROKE:
            if rgb == dlm.NO_COLOUR:
                out.append(stroke)
                continue
            drawn = dlm.legible(rgb, ground, dlm.STROKE_CONTRAST)
            note = ("stroke", rgb, drawn, dlm.contrast(rgb, ground))
            if drawn != rgb and note not in notes:
                notes.append(note)
            out.append(drawn)
            continue
        if rgb == dlm.NO_COLOUR and under == dlm.NO_COLOUR:
            out.append(stroke)
            continue
        ink = stroke if rgb == dlm.NO_COLOUR else rgb
        if under != dlm.NO_COLOUR:
            behind = under
        else:
            behind = roles[surface][0] if surface >= 0 else ground
        drawn = dlm.legible(ink, behind, dlm.TEXT_CONTRAST)
        note = ("text", ink, drawn, dlm.contrast(ink, behind))
        if drawn != ink and note not in notes:
            notes.append(note)
        out.append(drawn)
    return out, notes


def _pack(rgb: int) -> int:
    from imgui_bundle import imgui as im

    return int(im.IM_COL32((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF, 255))


def _ground(im: Any) -> int:
    """
    The colour behind the figure: the current window's background, a child window's
    own where it paints one.
    """
    from imgui_bundle.imgui import internal as imi

    try:
        child = bool(imi.get_current_window_read().flags & im.WindowFlags_.child_window.value)
    except Exception:
        child = False
    if child:
        bg = im.get_style_color_vec4(im.Col_.child_bg)
        if bg.w >= 0.5:
            return _rgb(bg)
    return _rgb(im.get_style_color_vec4(im.Col_.window_bg))


# Geometry ---------------------------------------------------------------------------

# What a built op draws.
_POLY_CONVEX, _POLY_CONCAVE, _LINE, _TEXT, _KNOCKOUT = range(5)


@dataclass(slots=True)
class _Geometry:
    """
    A display list resolved at one scale and font, in pixels from the figure's
    corner: ``ops`` in paint order, each ``(kind, box, paint, what, node, edge,
    width)``, where ``what`` is a fill's ``ImVec2`` points, a stroke's polylines
    each with whether it is closed (every dash of one subpath in the one op, so the
    cull walks items and not dashes), a text's ``(x, top, size, text, bold)``, or a
    knockout's rectangle. ``box`` is what the op covers, strokes not counted;
    ``pad`` is how far, in screen pixels, the widest stroke and its highlight reach
    past any box. ``paints`` are what the ops' ``paint`` index into.
    """

    scale: float
    key: tuple[Any, ...]
    ops: list[tuple[Any, ...]]
    paints: list[Paint]
    shrunk_labels: int = 0
    pad: float = 1.0 + HIGHLIGHT_WIDTH
    colours: dict[tuple[str, int], list[int]] = field(default_factory=dict)
    """
    ``paints`` packed for the draw list, by palette name and the pane's ground.
    """


def _advance(face: Any, size: float, text: str) -> float:
    baked = face.get_font_baked(size)
    return float(sum(baked.get_char_advance(ord(ch)) for ch in text))


def build(dl: DisplayList, scale: float, font: Any, bold: Any, key: tuple[Any, ...]) -> _Geometry:
    """
    ``dl`` at ``scale`` pixels a unit, its labels in ``font`` and ``bold``.
    """
    from imgui_bundle import imgui as im

    V = im.ImVec2
    paints: dict[Paint, int] = {}
    ops: list[tuple[Any, ...]] = []
    shrunk = 0

    def paint(p: Paint) -> int:
        got = paints.get(p)
        if got is None:
            got = paints[p] = len(paints)
        return got

    ox, oy = dl.origin
    widest = 0.0
    dashes = MAX_DASHES_DRAWN
    # The role whose theme fill each node's label sits on: the node's shape filled
    # last before it in paint order, where the author gave that shape no fill.
    surface: dict[int, int] = {}
    for it in dl.items:
        if isinstance(it, PathItem):
            xy = [v - (ox if i % 2 == 0 else oy) for i, v in enumerate(it.xy)]
            subs = dlm.flatten_units(it.ops, xy, scale)
            b = (
                (it.bbox[0] - ox) * scale,
                (it.bbox[1] - oy) * scale,
                (it.bbox[2] - ox) * scale,
                (it.bbox[3] - oy) * scale,
            )
            if it.fill and it.node >= 0:
                surface[it.node] = it.role if it.fill_rgb == dlm.NO_COLOUR else -1
            if it.fill:
                fp = paint((_FILL, it.role, it.fill_rgb, dlm.NO_COLOUR, -1))
                for pl, _ in subs:
                    for loop in dlm.fill_loops(pl):
                        pts = dlm.clockwise(dlm.dedupe(loop, True))
                        if len(pts) < 3:
                            continue
                        kind = _POLY_CONVEX if dlm.convex(pts) else _POLY_CONCAVE
                        ops.append((kind, b, fp, [V(x, y) for x, y in pts], it.node, it.edge))
            if it.stroke:
                sp = paint((_STROKE, it.role, it.stroke_rgb, dlm.NO_COLOUR, -1))
                w = max(1.0, it.width * scale)
                widest = max(widest, w)
                dash = on_screen_dash(it.dash, scale)
                lines: list[tuple[list[Any], bool]] = []
                for pl, closed in subs:
                    pts = dlm.dedupe(pl, closed)
                    if len(pts) < 2:
                        continue
                    pieces = dlm.dashed(pts, closed, dash, dashes) if dash else None
                    if pieces is None:
                        lines.append(([V(x, y) for x, y in pts], closed))
                    else:
                        dashes -= len(pieces)
                        lines += [([V(x, y) for x, y in piece], False) for piece in pieces]
                if lines:
                    ops.append((_LINE, b, sp, lines, it.node, it.edge, w))
            continue
        assert isinstance(it, TextRun)
        # Whole pixels: every distinct size is a bake on the atlas, so a size that
        # followed the scale continuously would bake one per resize frame.
        size = max(1.0, float(round(it.size * scale)))
        face = bold if it.bold else font
        tw = _advance(face, size, it.text)
        if it.box > 0 and tw > it.box * scale * LABEL_OVERFLOW:
            fit = it.box * scale * LABEL_OVERFLOW / tw
            if fit >= 0.6:
                size = max(1.0, float(math.floor(size * fit)))
                tw = _advance(face, size, it.text)
                shrunk += 1
        x = (it.x - ox) * scale
        y = (it.y - oy) * scale
        if it.anchor == dlm.ANCHOR_MIDDLE:
            x -= tw / 2
        elif it.anchor == dlm.ANCHOR_END:
            x -= tw
        if it.baseline == dlm.BASE_CENTRAL:
            top = y - size / 2
        else:
            top = y - float(face.get_font_baked(size).ascent)
        # Glyph quads at fractional positions lose one-pixel strokes (a hyphen read
        # as a dot): imtypeset snaps for the same reason.
        x, top = float(round(x)), float(round(top))
        b = (x, top, x + tw, top + size)
        if it.role == dlm.EDGE_LABEL:
            kp = paint((_KNOCK, it.role, dlm.NO_COLOUR, dlm.NO_COLOUR, -1))
            rect = (x - 2, top - 1, x + tw + 2, top + size + 1)
            ops.append((_KNOCKOUT, b, kp, rect, it.node, it.edge))
        under = surface.get(it.node, -1) if it.node >= 0 and it.role == dlm.LABEL else -1
        ip = paint((_INK, it.role, it.ink_rgb, it.ground_rgb, under))
        ops.append((_TEXT, b, ip, (x, top, size, it.text, it.bold), it.node, it.edge))
    return _Geometry(scale, key, ops, list(paints), shrunk, widest / 2 + HIGHLIGHT_WIDTH + 1.0)


def on_screen_dash(dash: tuple[float, ...], scale: float) -> tuple[float, ...]:
    """
    A dash pattern in diagram units as it is cut at ``scale`` pixels a unit: scaled,
    and stretched, proportions kept, to a period of at least ``MIN_DASH_PERIOD_PX``.
    """
    if not dash:
        return dash
    px = tuple(d * scale for d in dash)
    period = sum(px) * (1 if len(px) % 2 == 0 else 2)
    stretch = MIN_DASH_PERIOD_PX / period if 0 < period < MIN_DASH_PERIOD_PX else 1.0
    return tuple(d * stretch for d in px)


# The widget -------------------------------------------------------------------------


def snapped(x: float, y: float, density: float = 1.0) -> tuple[float, float]:
    """
    An origin on the nearest whole pixel of the framebuffer, ``density`` of them to a
    unit of ImGui's: a polyline's one-pixel line at a fractional origin blurs across
    two.
    """
    return round(x * density) / density, round(y * density) / density


class FigureWidget:
    """
    What draws one marked fence natively: the ``Hosted`` imscribe's ``host_for``
    answers with.
    """

    def __init__(self, figure: FigureMark, text: str) -> None:
        self._mark = figure
        self._text = fence_text(figure.said, text)
        # The failed form's measure: (drawn width, font size, reason) to the heights
        # of its reason and its source. Measuring the source is a pass over up to a
        # megabyte, and height_for is asked every frame, on screen or not.
        self._measured: tuple[tuple[float, float, str], tuple[float, float]] | None = None
        self._geometry: _Geometry | None = None
        self.hovered: str | None = None
        """
        The id of the node under the pointer at the last draw, or ``None``.
        """
        self.drawn: tuple[int, int] = (0, 0)
        """
        Ops drawn at the last draw, and ops built: what culling left.
        """
        self.rebuilds = 0

    @property
    def mark(self) -> FigureMark:
        return self._mark

    @property
    def text(self) -> str:
        return self._text

    def _failure(self) -> str | None:
        return _STATE.failures.get(self._mark.sha)

    def _display(self) -> DisplayList | None:
        """
        What the figure is drawn from. For a fence not yet laid out, ``None``, and the
        layout is asked of the render child if it has not been.
        """
        display = self._mark.display
        if display is None:
            self._lay_out(self._mark.source)
        return display

    def _lay_out(self, source: str) -> None:
        sha = self._mark.sha
        if sha in _STATE.laying or sha in _STATE.failures or not _may_ask(sha):
            return
        _STATE.laying.add(sha)
        _STATE.renderer().lay_out((_LAYOUT, sha), source)

    def size_for(self, width_px: float) -> tuple[float, float]:
        """
        The figure's drawn size in a block ``width_px`` wide: no wider than its
        natural width or the block, at its aspect. ``PLACEHOLDER_HEIGHT`` tall while
        it is laid out.
        """
        display = self._display()
        if display is None:
            return max(1.0, width_px), PLACEHOLDER_HEIGHT
        wide = max(1.0, min(width_px, display.width))
        return wide, wide * display.height / display.width

    def height_for(self, width_px: float) -> float:
        failure = self._failure()
        if failure is not None:
            said, source = self._failed_heights(self._refusal(failure), width_px)
            return said + _spacing() + source
        return self.size_for(width_px)[1]

    def _refusal(self, reason: str) -> str:
        return refusal_line(reason, self._mark.said)

    def draw(self, *, palette: Any = None, width_px: float | None = None) -> object:
        from imgui_bundle import imgui as im

        wide = float(width_px if width_px is not None else im.get_content_region_avail().x)
        at = im.get_cursor_screen_pos()
        failure = self._failure()
        if failure is not None:
            self._draw_failed(im, self._refusal(failure), at.x, at.y, wide)
            return True
        display = self._display()
        w, h = self.size_for(wide)
        # The block's own rectangle: nothing drawn here reaches outside it.
        im.push_clip_rect(im.ImVec2(at.x, at.y), im.ImVec2(at.x + wide, at.y + h), True)
        try:
            if display is None:
                from .. import theme

                dl = im.get_window_draw_list()
                dl.add_rect(
                    im.ImVec2(at.x, at.y), im.ImVec2(at.x + w, at.y + h), theme.P.border.u32
                )
                self.hovered, self.drawn = None, (0, 0)
            else:
                self._draw_native(im, display, at.x, at.y, w, h)
        finally:
            im.pop_clip_rect()
        return True

    def _geometry_for(self, im: Any, display: DisplayList, scale: float) -> _Geometry | None:
        """
        The geometry to draw at ``scale``: built now if it is the first, or if this
        figure's turn to rebuild has come (``_may_rebuild``), else the last one.
        """
        from ..ui import typeset

        font = typeset.imfont(typeset.Weight.REGULAR) or im.get_font()
        bold = typeset.imfont(typeset.Weight.BOLD) or font
        # font_id, not id(font): the binding hands back a new wrapper on every call.
        key = (round(scale, 4), int(font.font_id), int(bold.font_id))
        held = self._geometry
        if held is not None and held.key == key:
            return held
        # A first build waits its turn as a rebuild does: a view made again
        # (``Stream._forget``, a tab come back to) has every figure on it without
        # geometry, and four 100-node flowcharts built in one frame made it 287 ms.
        # Until its turn the figure stands at its height as an outline.
        if not _may_rebuild(id(self), im.get_frame_count()):
            return held
        self._geometry = build(display, scale, font, bold, key)
        self.rebuilds += 1
        return self._geometry

    def _paints(self, im: Any, geo: _Geometry) -> list[int]:
        from .. import theme

        palette = theme.current()
        ground = _ground(im)
        got = geo.colours.get((palette.name, ground))
        if got is None:
            rgbs, notes = resolve(geo.paints, palette, ground)
            got = geo.colours[(palette.name, ground)] = [_pack(rgb) for rgb in rgbs]
            noted = (self._mark.sha, palette.name, ground)
            if notes and noted not in _STATE.noted:
                _keep(_STATE.noted, noted, None, _NOTED_KEPT)
                from ..log import LOG

                said = "; ".join(
                    f"{what} #{before:06x} drawn #{after:06x} (contrast {ratio:.1f})"
                    for what, before, after, ratio in notes[:8]
                )
                LOG.info(
                    "figures",
                    f"figure {self._mark.sha[:10]}: author colours made legible on the "
                    f"{palette.name} theme: {said}",
                )
        return got

    def _draw_native(
        self, im: Any, display: DisplayList, x: float, y: float, w: float, h: float
    ) -> None:
        from imgui_bundle.imgui import internal as imi

        from .. import theme

        scale = w / display.width
        geo = self._geometry_for(im, display, scale)
        if geo is None:
            from .. import theme

            dlist = im.get_window_draw_list()
            dlist.add_rect(im.ImVec2(x, y), im.ImVec2(x + w, y + h), theme.P.border.u32)
            self.hovered, self.drawn = None, (0, 0)
            return
        colours = self._paints(im, geo)
        k = scale / geo.scale  # 1, or the stand-in geometry's stretch to this width
        ox, oy = snapped(x, y, _density(im))
        dlist = im.get_window_draw_list()
        # The node under the pointer (``displaylist.node_at``), tested without an
        # item so the pane's own selection still gets the drag.
        hovered = -1
        if im.is_window_hovered() and im.is_mouse_hovering_rect(
            im.ImVec2(ox, oy), im.ImVec2(ox + w, oy + h)
        ):
            mp = im.get_mouse_pos()
            ux = (mp.x - ox) / scale + display.origin[0]
            uy = (mp.y - oy) / scale + display.origin[1]
            hovered = dlm.node_at(display, ux, uy)
            if hovered >= 0 and im.is_mouse_clicked(0):
                try:
                    node_clicked(self._mark.sha, display.nodes[hovered].id)
                except Exception:
                    pass
        self.hovered = display.nodes[hovered].id if hovered >= 0 else None
        lit = (
            {e for e, edge in enumerate(display.edges) if hovered in edge.ends}
            if hovered >= 0
            else set()
        )
        focus = theme.P.focus.u32
        # What is on screen, in the geometry's pixels, widened by how far a stroke
        # reaches past the box it is culled by: that reach is in screen pixels at
        # any stretch, since a line is drawn ``k`` times thinner and then stretched.
        cmin, cmax = dlist.get_clip_rect_min(), dlist.get_clip_rect_max()
        reach = geo.pad / k
        vx0, vy0 = (max(cmin.x, ox) - ox) / k - reach, (max(cmin.y, oy) - oy) / k - reach
        vx1, vy1 = (min(cmax.x, ox + w) - ox) / k + reach, (min(cmax.y, oy + h) - oy) / k + reach
        closed_flag = im.ImDrawFlags_.closed.value
        origin = im.ImVec2(ox, oy)
        zero = im.ImVec2(0.0, 0.0)
        drawn = 0
        v0 = dlist.vtx_buffer.size()
        regular = bold = None
        for op in geo.ops:
            b = op[1]
            if b[2] < vx0 or b[0] > vx1 or b[3] < vy0 or b[1] > vy1:
                continue
            drawn += 1
            kind = op[0]
            if kind >= _TEXT:
                # Text goes in screen space: AddText culls lines against the clip
                # at the position it is given. Moved geometry before it is moved now.
                v1 = dlist.vtx_buffer.size()
                if v1 > v0:
                    imi.shade_verts_transform_pos(dlist, v0, v1, zero, k, 0.0, origin)
                if kind == _KNOCKOUT:
                    r = op[3]
                    dlist.add_rect_filled(
                        im.ImVec2(ox + r[0] * k, oy + r[1] * k),
                        im.ImVec2(ox + r[2] * k, oy + r[3] * k),
                        colours[op[2]],
                        2.0,
                    )
                else:
                    tx, top, size, text, is_bold = op[3]
                    if regular is None:
                        from ..ui import typeset

                        regular = typeset.imfont(typeset.Weight.REGULAR) or im.get_font()
                        bold = typeset.imfont(typeset.Weight.BOLD) or regular
                    dlist.add_text(
                        bold if is_bold else regular,
                        size if k == 1.0 else max(1.0, float(round(size * k))),
                        im.ImVec2(ox + round(tx * k), oy + round(top * k)),
                        colours[op[2]],
                        text,
                    )
                v0 = dlist.vtx_buffer.size()
                continue
            on = (hovered >= 0 and op[4] == hovered) or (op[5] >= 0 and op[5] in lit)
            if kind == _LINE:
                ink = focus if on else colours[op[2]]
                thick = op[6] / k + (HIGHLIGHT_WIDTH / k if on else 0.0)
                for line, closed in op[3]:
                    dlist.add_polyline(line, ink, thick, closed_flag if closed else 0)
            elif kind == _POLY_CONVEX:
                dlist.add_convex_poly_filled(op[3], colours[op[2]])
            else:
                dlist.add_concave_poly_filled(op[3], colours[op[2]])
        v1 = dlist.vtx_buffer.size()
        if v1 > v0:
            imi.shade_verts_transform_pos(dlist, v0, v1, zero, k, 0.0, origin)
        self.drawn = (drawn, len(geo.ops))

    def _failed_heights(self, reason: str, wide: float) -> tuple[float, float]:
        """
        How tall the reason and the source are, wrapped at ``wide`` in the current
        font: measured once for each width, size and reason.
        """
        from imgui_bundle import imgui as im

        key = (wide, float(im.get_font_size()), reason)
        if self._measured is None or self._measured[0] != key:
            said = float(im.calc_text_size(reason, None, False, wide).y)
            source = float(im.calc_text_size(self._text, None, False, wide).y)
            self._measured = key, (said, source)
        return self._measured[1]

    def _draw_failed(self, im: Any, reason: str, x: float, y: float, wide: float) -> None:
        """
        The reason in the warn ink and the fence's source under it, wrapped to the
        rectangle ``height_for`` gave and clipped to it.

        Drawn on the draw list rather than as text items: a text item measures the
        whole of a wrapped text every frame to size itself, where the draw list's
        text stops at the bottom of the clip.
        """
        from .. import theme

        said, source = self._failed_heights(reason, wide)
        bottom = y + said + _spacing() + source
        im.push_clip_rect(im.ImVec2(x, y), im.ImVec2(x + wide, bottom), True)
        try:
            dl = im.get_window_draw_list()
            font, size = im.get_font(), im.get_font_size()
            dl.add_text(font, size, im.ImVec2(x, y), theme.P.warn.u32, reason, None, wide)
            under = im.ImVec2(x, y + said + _spacing())
            dl.add_text(font, size, under, theme.P.text_dim.u32, self._text, None, wide)
        finally:
            im.pop_clip_rect()


def _density(im: Any) -> float:
    """
    Pixels of the framebuffer to a unit of ImGui's: 2 on a Retina display, else 1.
    """
    scale = float(im.get_io().display_framebuffer_scale.x)
    return scale if math.isfinite(scale) and scale >= 1.0 else 1.0


def _spacing() -> float:
    from imgui_bundle import imgui as im

    return float(im.get_style().item_spacing.y)


def figure_for(view: Any, number: int, block: Any) -> FigureWidget | None:
    """
    The host for a block ``views._captioned`` marked, or ``None`` for any other.
    """
    figure = getattr(block, "language", None)
    text = getattr(block, "text", None)
    if not isinstance(figure, FigureMark) or not isinstance(text, str):
        return None
    was = view.hosted(number) if hasattr(view, "hosted") else None
    if isinstance(was, FigureWidget) and was.mark is figure:
        return was
    return FigureWidget(figure, text)
