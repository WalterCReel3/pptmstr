"""
A committed ```` ```svg ```` or ```` ```mermaid ```` fence drawn as a figure in its
block's place.

``views._captioned`` asks ``mark`` about a fence once it may host one: settled
documents always, a stream only for a fence it has committed or after it closed
(``views._Captioned``). A fence ``sanitise`` passes is marked (``FigureMark``) and
the view's host dispatcher answers it with a ``FigureWidget``; one it refuses is
never marked and stays code, under a line in the warn ink saying why. So code stays
code, and only SVG that passed the policy reaches a widget.

**A mermaid fence is marked without being laid out**: merman runs in the render
child (``mermaid.layout``), which hands back SVG that has passed the same sanitiser,
and from then on the figure is drawn exactly as an svg one. Until that arrives the
figure stands at ``PLACEHOLDER_HEIGHT``, so it jumps once more than an svg figure
does; the layout is then held by the fence's text and the merman version
(``_State.layouts``), so a re-hosted figure answers its real height at once, and
by the mark that asked for it, so a figure on screen never loses it to the bound
on the others. Where merman is not installed the fence is refused at ``mark`` and
stays code; a merman error arrives later and is drawn as a render failure is, below.

**Every figure stands on a light card** (``BACKDROP``): a rounded rectangle
``FIGURE_PAD`` wider than the image on each side, painted inside the figure's own
clip before the image or the outline that stands for it. SVG and Mermaid are
authored for a white page: on the dark pane merman's dark ink was lost
(``shots/mermaid-window/3-drawn.png``). One card fixes both routes and does not
depend on the app's theme; merman's own ``dark`` theme was rejected because it
fixes Mermaid alone and would make a figure's pixels depend on the app's theme.
The cost is a bright card in the dark theme. A failed figure has no card: its
source is text in the theme's ink.

**The height is the sanitiser's, not the raster's.** ``height_for(w)`` is
``min(w - 2p, natural_w) * natural_h / natural_w + 2p`` from the mark, ``p`` being
``FIGURE_PAD``, answered at once in
the first frame and after every re-hosting (``Stream._forget`` makes a new view),
so a figure jumps only once, at commit, when the code block becomes it.

**Pixels come from the render process** (``figures.render``), over frames: a
widget submits when it has no raster at its drawn width times the display's
density, and once the width has been still for ``STABLE_FRAMES`` when it has one at
another width to show meanwhile. That one is drawn stretched to the rectangle,
which T1 measured acceptable as a transient and not as a design: there are no
mipmaps, and labels break up minified by 3x and more. With nothing to show yet, a
quiet outline of the right size stands there.

**Textures are held by content** (``_Textures``): ``(sha of the sanitised SVG,
density, raster width)`` to the ``TextureGpu``, bounded by bytes, least recently
drawn first out. A view made again finds them.

**Results are taken between frames** (``pump``, from the app's pre-new-frame
callback), every frame whether or not a figure is on screen: that is also where the
render process's timeout is enforced, so a hostile render is stopped with nothing
drawing. Uploads and evictions happen there too, so a texture is never let go of
inside a frame; an evicted one is parked and dropped at the next pump all the same.
T1: a texture dropped between ``imgui.image`` and that frame's render is a GL error
and a blank figure.

**A render that fails** leaves the fence's source on screen, under the reason in
the warn ink, drawn here by ImGui since the block is already a host's by then. A
failure the renderer says would happen again (``render.Failed.transient`` false:
resvg refused it, a cap) is kept for the content and the figure is not asked for
again. One the machine may have caused (the child died, ran out of time or memory,
did not start) is asked for once more after ``RETRY_AFTER_S``, showing the outline
meanwhile; a second such failure is kept as the first kind is. A mermaid figure's
layout and its raster each have that one retry (``_budget``).
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import fences, mermaid, render
from .sanitise import Refused, Sanitised, sanitise

STABLE_FRAMES = 6
"""
How many frames a drawn width must hold before a figure that has a raster at
another width asks for one at this width: a pane being dragged changes it every
frame, and each request is a render of up to a second.
"""

TEXTURE_BUDGET_BYTES = 256 << 20
"""
The RGBA held as textures for figures, across every pane: four rasters at the
render cap (4096x4096), or about thirty 800x600 figures at a density of 2. On an
integrated GPU this is system memory.
"""

RETRY_AFTER_S = 5.0
"""
How long after a transient failure a figure is asked for again: past a render
process's restart, and long enough that a machine that killed one child for memory
has had a moment to recover.
"""

PLACEHOLDER_HEIGHT = 120.0
"""
The height of the space a mermaid figure's image stands in until the render child
has laid it out, its card's padding not counted: its natural size is known only
after layout, so it jumps once more, to its real height, when the layout arrives
(planning record §3.6). A re-hosted figure whose layout is held answers its real
height at once.
"""

FIGURE_PAD = 6.0
"""
The card's margin around a figure's image on each side, in ImGui units: counted
in ``height_for`` and in the placeholder's height.
"""

BACKDROP = 0xFAFAF7
"""
The card every figure is drawn on, as ``0xRRGGBB``: a near-white neutral, the same
in every theme. No palette entry is light in every theme (the dark palette has none
this light), so it is a constant and not a theme's colour.
"""

BACKDROP_ROUNDING = 4.0

LAYOUT_BUDGET_CHARS = 64 << 20
"""
The SVG text the held layouts may add up to, in characters, least recently read
first out. Each is a diagram's sanitised SVG: 30-105 KB for the probe's five, and
the sanitiser's cap is 1 MiB, so this is several hundred typical diagrams and
sixty-four at the cap. A figure on screen keeps its own once it has read it
(``FigureMark.sanitised``), so this bounds what a re-hosting finds; a layout
evicted before its figure's next read is asked for once more.
"""

_MARKS_KEPT = 64
_FAILURES_KEPT = 256
# The first element of a layout request's key; a raster's is a sha.
_LAYOUT = "layout"


class FigureMark(str):
    """
    The info string ``views._captioned`` gives a fence that is drawn as a figure.

    Empty, as a block that names no language has, so nothing captions it or colours
    it; it carries the info string as written and what the figure is drawn from. A
    subclass and not a word, as ``views._Warned`` is: the library's reader gives every
    fence a plain ``str``, so no fence can be written that is taken for one.

    An svg fence's mark holds what ``sanitise`` made of it, and ``sha`` is that SVG's.
    A mermaid fence's mark holds its ``source`` instead, and ``sha`` is of the source
    and the merman version: its SVG is made in the render child (``mermaid.layout``)
    and held by ``sha`` (``_State.layouts``), so ``sanitised`` is ``None`` until then
    and a mark made again for the same text finds it at once.
    """

    said: str
    sha: str
    source: str | None
    _sanitised: Sanitised | None

    def __new__(
        cls, said: str, sanitised: Sanitised | None, *, source: str | None = None, sha: str = ""
    ) -> FigureMark:
        made = super().__new__(cls, "")
        made.said = said
        made._sanitised = sanitised
        made.source = source
        if sanitised is not None:
            sha = hashlib.sha256(sanitised.svg.encode("utf-8", "surrogatepass")).hexdigest()
        made.sha = sha
        return made

    @property
    def sanitised(self) -> Sanitised | None:
        """
        The sanitised SVG the figure is drawn from, or ``None`` while a mermaid fence
        has not been laid out. A layout found is kept by the mark from then on, so
        the bound on the held layouts cannot take it from a figure on screen.
        """
        if self._sanitised is None:
            self._sanitised = _STATE.layouts.get(self.sha)
        return self._sanitised


_word = fences.word


def is_figure_fence(said: str) -> bool:
    """
    Whether a fence's info string asks for a figure: its first word is a figure row
    of the table of drawn fences (``fences.FIGURE_WORDS``: ``svg``, ``mermaid``).
    """
    return _word(said) in fences.FIGURE_WORDS


_MARKS: OrderedDict[tuple[str, str], FigureMark | Refused] = OrderedDict()


def mark(said: str, text: str) -> FigureMark | Refused:
    """
    What a fence whose info string asks for a figure becomes: marked, or refused
    with the reason it stays code. Kept for the last few texts, because a stream made
    again (``Stream._forget``) reads every fence again.

    An svg fence is sanitised here. A mermaid fence is only marked: merman runs in the
    render child, never here. It is refused here only where merman is not installed
    (``mermaid.EXTRA_REASON``), which is read from metadata without importing it.
    """
    key = (said, text)
    held = _MARKS.get(key)
    if held is None:
        held = _mermaid_mark(said, text) if _word(said) == "mermaid" else _svg_mark(said, text)
        _MARKS[key] = held
        while len(_MARKS) > _MARKS_KEPT:
            _MARKS.popitem(last=False)
    else:
        _MARKS.move_to_end(key)
    return held


def _svg_mark(said: str, text: str) -> FigureMark | Refused:
    outcome = sanitise(text)
    return FigureMark(said, outcome) if isinstance(outcome, Sanitised) else outcome


def _mermaid_mark(said: str, text: str) -> FigureMark | Refused:
    version = mermaid.version()
    if version is None:
        return Refused(mermaid.missing_reason())
    keyed = f"merman {version}\0{text}".encode("utf-8", "surrogatepass")
    return FigureMark(said, None, source=text, sha=hashlib.sha256(keyed).hexdigest())


def refusal_line(reason: str, said: str = "svg") -> str:
    """
    The line set above a fence that is not drawn as a figure.
    """
    return f"{_word(said) or 'svg'} not drawn: {reason}"


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


# Textures ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Raster:
    texture: Any
    width: int
    height: int

    @property
    def nbytes(self) -> int:
        return self.width * self.height * 4


_Key = tuple[str, float, int]


class _Layouts:
    """
    Laid-out diagrams by their mark's sha, bounded by the characters of their SVG,
    the least recently read evicted first: a read refreshes an entry.
    """

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self._held: OrderedDict[str, Sanitised] = OrderedDict()
        self._chars = 0

    def __len__(self) -> int:
        return len(self._held)

    def __contains__(self, sha: object) -> bool:
        return sha in self._held

    def get(self, sha: str) -> Sanitised | None:
        laid = self._held.get(sha)
        if laid is not None:
            self._held.move_to_end(sha)
        return laid

    def put(self, sha: str, laid: Sanitised) -> None:
        old = self._held.pop(sha, None)
        if old is not None:
            self._chars -= len(old.svg)
        self._held[sha] = laid
        self._chars += len(laid.svg)
        while self._chars > self.budget and len(self._held) > 1:
            _, evicted = self._held.popitem(last=False)
            self._chars -= len(evicted.svg)

    def clear(self) -> None:
        self._held.clear()
        self._chars = 0


class _Textures:
    """
    Rasters by content, bounded by bytes, the least recently drawn evicted first.

    An evicted raster is parked with the pump it was evicted in and let go of only
    at a later one (``release``).
    """

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self._held: OrderedDict[_Key, _Raster] = OrderedDict()
        self._bytes = 0
        self.parked: list[tuple[int, _Raster]] = []

    def __len__(self) -> int:
        return len(self._held)

    def get(self, key: _Key) -> _Raster | None:
        raster = self._held.get(key)
        if raster is not None:
            self._held.move_to_end(key)
        return raster

    def any_of(self, sha: str, scale: float) -> _Raster | None:
        """
        The most recently drawn raster of this content at this density, any width.
        """
        for (held_sha, held_scale, _), raster in reversed(self._held.items()):
            if held_sha == sha and held_scale == scale:
                return raster
        return None

    def put(self, key: _Key, raster: _Raster, at: int) -> None:
        old = self._held.pop(key, None)
        if old is not None:
            self._bytes -= old.nbytes
            self.parked.append((at, old))
        self._held[key] = raster
        self._bytes += raster.nbytes
        while self._bytes > self.budget and len(self._held) > 1:
            _, evicted = self._held.popitem(last=False)
            self._bytes -= evicted.nbytes
            self.parked.append((at, evicted))

    def release(self, now: int) -> int:
        """
        Let go of every raster parked before ``now``. How many that was.
        """
        keep = [(at, raster) for at, raster in self.parked if at >= now]
        dropped = len(self.parked) - len(keep)
        self.parked = keep
        return dropped

    def clear(self) -> None:
        self._held.clear()
        self.parked.clear()
        self._bytes = 0


def _upload(rgba: bytes, width: int, height: int) -> Any:
    """
    A ``TextureGpu`` of ``rgba``. On the GUI thread, with a backend running.
    """
    import numpy as np
    from imgui_bundle import hello_imgui

    pixels = np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4)
    return hello_imgui.create_texture_gpu_from_rgba_data(pixels)


@dataclass
class _State:
    textures: _Textures
    # Failures shown in place of the figure, by sha: kept for good.
    failures: OrderedDict[str, str]
    # What has been asked of the renderer and not answered: the raster width, by
    # (sha, density).
    asked: dict[tuple[str, float], int]
    # Requests whose first transient failure is waiting out its back-off: when they
    # may be asked again, by ``_budget``.
    backoff: OrderedDict[str, float]
    # Requests asked again after a transient failure; another one is kept for good.
    retried: OrderedDict[str, None]
    # Mermaid diagrams laid out, by their mark's sha (source and merman version): the
    # natural size a re-hosted figure answers its height from at once.
    layouts: _Layouts = field(default_factory=lambda: _Layouts(LAYOUT_BUDGET_CHARS))
    # Mermaid diagrams asked of the render child and not yet answered, by sha.
    laying: set[str] = field(default_factory=set)
    pumps: int = 0
    upload: Callable[[bytes, int, int], Any] = _upload
    renderer: Callable[[], render.Renderer] = render.renderer
    clock: Callable[[], float] = time.monotonic


_STATE = _State(_Textures(TEXTURE_BUDGET_BYTES), OrderedDict(), {}, OrderedDict(), OrderedDict())


def pump() -> None:
    """
    Once a frame, between frames (the app's pre-new-frame callback): let go of what
    an earlier pump parked, take what the renderer has finished (which is also where
    it stops a render past its time), and upload it.

    Never raises. A result that cannot be taken, an upload that fails among them,
    is that figure's failure and does not cost the others.
    """
    state = _STATE
    state.pumps += 1
    try:
        state.textures.release(state.pumps)
        results = state.renderer().poll()
    except Exception:
        return
    for result in results:
        key = result.key
        if not (isinstance(key, tuple) and len(key) == 2):
            continue
        if key[0] == _LAYOUT:
            sha = key[1]
            state.laying.discard(sha)
            if isinstance(result, render.Laid):
                state.layouts.put(sha, result.sanitised)
                state.backoff.pop(_budget(sha, _LAYOUT), None)
            elif isinstance(result, render.Failed):
                _fail(state, sha, result, _LAYOUT)
            continue
        sha, scale = key
        state.asked.pop(key, None)
        try:
            _take(state, sha, scale, result)
        except Exception as exc:
            _keep(state.failures, sha, f"texture upload failed: {render._one_line(exc)}")


def _take(state: _State, sha: str, scale: float, result: render.Result) -> None:
    if isinstance(result, render.Rendered):
        texture = state.upload(result.rgba, result.width, result.height)
        raster = _Raster(texture, result.width, result.height)
        state.textures.put((sha, scale, result.width), raster, state.pumps)
        state.backoff.pop(_budget(sha), None)
    elif isinstance(result, render.Failed):
        _fail(state, sha, result)


def _budget(sha: str, kind: str = "") -> str:
    """
    What a request's one retry is kept by: the content's sha for a raster, and the
    sha under ``kind`` for a layout, so a layout that was retried leaves its
    raster's retry unspent.
    """
    return f"{kind}:{sha}" if kind else sha


def _fail(state: _State, sha: str, result: render.Failed, kind: str = "") -> None:
    """
    A render or a layout that failed: backed off once if the machine may have caused
    it, else kept, and the figure shows its source under the reason.
    """
    budget = _budget(sha, kind)
    if result.transient and budget not in state.retried:
        _keep(state.backoff, budget, state.clock() + RETRY_AFTER_S)
    else:
        state.backoff.pop(budget, None)
        _keep(state.failures, sha, result.reason)


def _keep(held: OrderedDict[str, Any], sha: str, value: Any, kept: int = _FAILURES_KEPT) -> None:
    held[sha] = value
    held.move_to_end(sha)
    while len(held) > kept:
        held.popitem(last=False)


def _may_ask(sha: str, kind: str = "") -> bool:
    """
    Whether content may be asked of the renderer now, for a raster or under
    ``kind``: not while that request's back-off from a transient failure runs. The
    ask that ends one is its one retry.
    """
    state = _STATE
    budget = _budget(sha, kind)
    at = state.backoff.get(budget)
    if at is None:
        return True
    if state.clock() < at:
        return False
    del state.backoff[budget]
    _keep(state.retried, budget, None)
    return True


def release_all() -> None:
    """
    Let go of every texture and failure held. For application exit, while the GL
    context is still current, and for tests.
    """
    _STATE.textures.clear()
    _STATE.failures.clear()
    _STATE.asked.clear()
    _STATE.backoff.clear()
    _STATE.retried.clear()
    _STATE.layouts.clear()
    _STATE.laying.clear()
    _STATE.pumps = 0


# The widget -------------------------------------------------------------------------


def snapped(x: float, y: float, density: float = 1.0) -> tuple[float, float]:
    """
    An origin on the nearest whole pixel of the framebuffer, ``density`` of them to a
    unit of ImGui's. T1: a raster drawn at a fractional origin lost 41% of its dark
    ink to the sampler's smear.
    """
    return round(x * density) / density, round(y * density) / density


class FigureWidget:
    """
    What draws one marked fence: the ``Hosted`` imscribe's ``host_for`` answers with.
    """

    def __init__(self, figure: FigureMark, text: str) -> None:
        self._mark = figure
        self._text = fence_text(figure.said, text)
        # The raster width last wanted, at which density, and the frame from which
        # it has been wanted.
        self._wanted: tuple[float, int] | None = None
        self._since = 0
        # The failed form's measure: (drawn width, font size, reason) to the heights
        # of its reason and its source. Measuring the source is a pass over up to a
        # megabyte, and height_for is asked every frame, on screen or not.
        self._measured: tuple[tuple[float, float, str], tuple[float, float]] | None = None

    @property
    def mark(self) -> FigureMark:
        return self._mark

    @property
    def text(self) -> str:
        return self._text

    def _failure(self) -> str | None:
        return _STATE.failures.get(self._mark.sha)

    def _sanitised(self) -> Sanitised | None:
        """
        What the figure is drawn from. For a mermaid fence not yet laid out, ``None``,
        and the layout is asked of the render child if it has not been.
        """
        natural = self._mark.sanitised
        if natural is None and self._mark.source is not None:
            self._lay_out(self._mark.source)
        return natural

    def _lay_out(self, source: str) -> None:
        sha = self._mark.sha
        if sha in _STATE.laying or sha in _STATE.failures or not _may_ask(sha, _LAYOUT):
            return
        _STATE.laying.add(sha)
        _STATE.renderer().lay_out((_LAYOUT, sha), source)

    def size_for(self, width_px: float) -> tuple[float, float]:
        """
        The image's drawn size in a block ``width_px`` wide: no wider than the
        natural width or the block less the card's padding, at its aspect.
        ``PLACEHOLDER_HEIGHT`` tall while a mermaid fence is laid out.
        """
        room = width_px - 2.0 * FIGURE_PAD
        natural = self._sanitised()
        if natural is None:
            return max(1.0, room), PLACEHOLDER_HEIGHT
        wide = max(1.0, min(room, natural.width))
        return wide, wide * natural.height / natural.width

    def height_for(self, width_px: float) -> float:
        failure = self._failure()
        if failure is not None:
            said, source = self._failed_heights(self._refusal(failure), width_px)
            return said + _spacing() + source
        return self.size_for(width_px)[1] + 2.0 * FIGURE_PAD

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
        from .. import theme

        natural = self._sanitised()
        w, h = self.size_for(wide)
        pad = FIGURE_PAD
        # The block's own rectangle: nothing drawn here reaches above or left of it.
        im.push_clip_rect(im.ImVec2(at.x, at.y), im.ImVec2(at.x + wide, at.y + h + 2.0 * pad), True)
        try:
            dl = im.get_window_draw_list()
            dl.add_rect_filled(
                im.ImVec2(at.x, at.y),
                im.ImVec2(at.x + w + 2.0 * pad, at.y + h + 2.0 * pad),
                _backdrop(),
                BACKDROP_ROUNDING,
            )
            scale = _density(im)
            x, y = snapped(at.x + pad, at.y + pad, scale)
            raster = None if natural is None else self._raster(im, natural, w, scale)
            if raster is None:
                dl.add_rect(im.ImVec2(x, y), im.ImVec2(x + w, y + h), theme.P.border.u32)
            else:
                im.set_cursor_screen_pos(im.ImVec2(x, y))
                exact = raster.width == round(w * scale)
                size = (raster.width / scale, raster.height / scale) if exact else (w, h)
                im.image(im.ImTextureRef(raster.texture.texture_id()), im.ImVec2(*size))
        finally:
            im.pop_clip_rect()
        return True

    def _raster(self, im: Any, natural: Sanitised, w: float, scale: float) -> _Raster | None:
        """
        The raster to draw at drawn width ``w``, asking for the right one where it is
        not held.
        """
        width, _ = render.raster_size(natural, w, scale)
        frame = im.get_frame_count()
        if self._wanted != (scale, width):
            self._wanted, self._since = (scale, width), frame
        sha = self._mark.sha
        held = _STATE.textures.get((sha, scale, width))
        if held is not None:
            return held
        meanwhile = _STATE.textures.any_of(sha, scale)
        if meanwhile is None or frame - self._since >= STABLE_FRAMES:
            if _STATE.asked.get((sha, scale)) != width and _may_ask(sha):
                _STATE.asked[(sha, scale)] = width
                _STATE.renderer().submit((sha, scale), natural, w, scale)
        return meanwhile

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


_PACKED: dict[int, int] = {}


def _backdrop() -> int:
    """
    ``BACKDROP`` packed for the draw list, once.
    """
    packed = _PACKED.get(BACKDROP)
    if packed is None:
        from .. import theme

        packed = _PACKED[BACKDROP] = int(theme.col(BACKDROP).u32)
    return packed


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
