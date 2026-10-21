"""
A committed ```` ```svg ```` fence drawn as a figure in its block's place.

``views._captioned`` asks ``mark`` about a fence once it may host one: settled
documents always, a stream only for a fence it has committed or after it closed
(``views._Captioned``). A fence ``sanitise`` passes is marked (``FigureMark``) and
the view's host dispatcher answers it with a ``FigureWidget``; one it refuses is
never marked and stays code, under a line in the warn ink saying why. So code stays
code, and only SVG that passed the policy reaches a widget.

**The height is the sanitiser's, not the raster's.** ``height_for(w)`` is
``min(w, natural_w) * natural_h / natural_w`` from the mark, answered at once in
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
drawn first out. A view made again finds them. **An evicted texture is not freed
in the frame that evicted it**: it is parked and dropped in a later frame, after
the frame that may have drawn it was rendered. T1: a texture dropped between
``imgui.image`` and that frame's render is a GL error and a blank figure.

**A render that fails** leaves the fence's source on screen, under the reason in
the warn ink, drawn here by ImGui since the block is already a host's by then. The
failure is kept for the content, so the figure is not asked for again.
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import render
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

_MARKS_KEPT = 64
_FAILURES_KEPT = 256
_FENCE_WORDS = frozenset({"svg"})


class FigureMark(str):
    """
    The info string ``views._captioned`` gives a fence that is drawn as a figure.

    Empty, as a block that names no language has, so nothing captions it or colours
    it; it carries the info string as written and what ``sanitise`` made of the
    fence. A subclass and not a word, as ``views._Warned`` is: the library's reader
    gives every fence a plain ``str``, so no fence can be written that is taken for
    one.
    """

    said: str
    sanitised: Sanitised
    sha: str

    def __new__(cls, said: str, sanitised: Sanitised) -> FigureMark:
        made = super().__new__(cls, "")
        made.said = said
        made.sanitised = sanitised
        made.sha = hashlib.sha256(sanitised.svg.encode("utf-8", "surrogatepass")).hexdigest()
        return made


def is_figure_fence(said: str) -> bool:
    """
    Whether a fence's info string asks for a figure: its first word is ``svg``.
    """
    words = said.split(maxsplit=1)
    return bool(words) and words[0].lower() in _FENCE_WORDS


_MARKS: OrderedDict[tuple[str, str], FigureMark | Refused] = OrderedDict()


def mark(said: str, text: str) -> FigureMark | Refused:
    """
    What a fence whose info string asks for a figure becomes: marked, or refused
    with ``sanitise``'s reason. Kept for the last few texts, because a stream made
    again (``Stream._forget``) reads every fence again.
    """
    key = (said, text)
    held = _MARKS.get(key)
    if held is None:
        outcome = sanitise(text)
        held = FigureMark(said, outcome) if isinstance(outcome, Sanitised) else outcome
        _MARKS[key] = held
        while len(_MARKS) > _MARKS_KEPT:
            _MARKS.popitem(last=False)
    else:
        _MARKS.move_to_end(key)
    return held


def refusal_line(reason: str) -> str:
    """
    The line set above a fence that is not drawn as a figure.
    """
    return f"svg not drawn: {reason}"


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


class _Textures:
    """
    Rasters by content, bounded by bytes, the least recently drawn evicted first.

    An evicted raster is parked with the frame it was evicted in and let go of only
    in a later frame (``release``).
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

    def put(self, key: _Key, raster: _Raster, frame: int) -> None:
        old = self._held.pop(key, None)
        if old is not None:
            self._bytes -= old.nbytes
            self.parked.append((frame, old))
        self._held[key] = raster
        self._bytes += raster.nbytes
        while self._bytes > self.budget and len(self._held) > 1:
            _, evicted = self._held.popitem(last=False)
            self._bytes -= evicted.nbytes
            self.parked.append((frame, evicted))

    def release(self, frame: int) -> int:
        """
        Let go of every raster parked before ``frame``. How many that was.
        """
        keep = [(at, raster) for at, raster in self.parked if at >= frame]
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
    failures: OrderedDict[str, str]
    # What has been asked of the renderer and not answered: the raster width, by
    # (sha, density).
    asked: dict[tuple[str, float], int]
    pumped: int = -1
    upload: Callable[[bytes, int, int], Any] = _upload
    renderer: Callable[[], render.Renderer] = render.renderer


_STATE = _State(_Textures(TEXTURE_BUDGET_BYTES), OrderedDict(), {})


def pump(frame: int) -> None:
    """
    Once a frame, from the first figure drawn in it: take what the renderer has
    finished, upload it, and let go of what was parked in an earlier frame.
    """
    state = _STATE
    if state.pumped == frame:
        return
    state.pumped = frame
    state.textures.release(frame)
    for result in state.renderer().poll():
        key = result.key
        if not (isinstance(key, tuple) and len(key) == 2):
            continue
        sha, scale = key
        state.asked.pop(key, None)
        if isinstance(result, render.Rendered):
            texture = state.upload(result.rgba, result.width, result.height)
            raster = _Raster(texture, result.width, result.height)
            state.textures.put((sha, scale, result.width), raster, frame)
        else:
            state.failures[sha] = result.reason
            while len(state.failures) > _FAILURES_KEPT:
                state.failures.popitem(last=False)


def release_all() -> None:
    """
    Let go of every texture and failure held. For application exit, while the GL
    context is still current, and for tests.
    """
    _STATE.textures.clear()
    _STATE.failures.clear()
    _STATE.asked.clear()
    _STATE.pumped = -1


# The widget -------------------------------------------------------------------------


def snapped(x: float, y: float) -> tuple[float, float]:
    """
    An origin on whole pixels. T1: a raster drawn at a fractional origin lost 41% of
    its dark ink to the sampler's smear.
    """
    return float(math.floor(x)), float(math.floor(y))


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

    @property
    def mark(self) -> FigureMark:
        return self._mark

    @property
    def text(self) -> str:
        return self._text

    def _failure(self) -> str | None:
        return _STATE.failures.get(self._mark.sha)

    def size_for(self, width_px: float) -> tuple[float, float]:
        """
        The drawn size at ``width_px``: no wider than the natural width, at its aspect.
        """
        natural = self._mark.sanitised
        wide = max(1.0, min(width_px, natural.width))
        return wide, wide * natural.height / natural.width

    def height_for(self, width_px: float) -> float:
        failure = self._failure()
        if failure is not None:
            return _failed_height(refusal_line(failure), self._text, width_px)
        return self.size_for(width_px)[1]

    def draw(self, *, palette: Any = None, width_px: float | None = None) -> object:
        from imgui_bundle import imgui as im

        pump(im.get_frame_count())
        wide = float(width_px if width_px is not None else im.get_content_region_avail().x)
        failure = self._failure()
        if failure is not None:
            _draw_failed(im, refusal_line(failure), self._text, wide)
            return True
        at = im.get_cursor_screen_pos()
        x, y = snapped(at.x, at.y)
        w, h = self.size_for(wide)
        im.push_clip_rect(im.ImVec2(x, y), im.ImVec2(x + wide, y + h), True)
        try:
            raster = self._raster(im, w)
            if raster is None:
                from .. import theme

                im.get_window_draw_list().add_rect(
                    im.ImVec2(x, y), im.ImVec2(x + w, y + h), theme.P.border.u32
                )
            else:
                scale = _density(im)
                im.set_cursor_screen_pos(im.ImVec2(x, y))
                exact = raster.width == round(w * scale)
                size = (raster.width / scale, raster.height / scale) if exact else (w, h)
                im.image(im.ImTextureRef(raster.texture.texture_id()), im.ImVec2(*size))
        finally:
            im.pop_clip_rect()
        return True

    def _raster(self, im: Any, w: float) -> _Raster | None:
        """
        The raster to draw at drawn width ``w``, asking for the right one where it is
        not held.
        """
        scale = _density(im)
        width, _ = render.raster_size(self._mark.sanitised, w, scale)
        frame = im.get_frame_count()
        if self._wanted != (scale, width):
            self._wanted, self._since = (scale, width), frame
        sha = self._mark.sha
        held = _STATE.textures.get((sha, scale, width))
        if held is not None:
            return held
        meanwhile = _STATE.textures.any_of(sha, scale)
        if meanwhile is None or frame - self._since >= STABLE_FRAMES:
            if _STATE.asked.get((sha, scale)) != width:
                _STATE.asked[(sha, scale)] = width
                _STATE.renderer().submit((sha, scale), self._mark.sanitised, w, scale)
        return meanwhile


def _density(im: Any) -> float:
    """
    Pixels of the framebuffer to a unit of ImGui's: 2 on a Retina display, else 1.
    """
    scale = float(im.get_io().display_framebuffer_scale.x)
    return scale if math.isfinite(scale) and scale >= 1.0 else 1.0


def _failed_height(reason: str, text: str, wide: float) -> float:
    from imgui_bundle import imgui as im

    spacing = im.get_style().item_spacing.y
    said = im.calc_text_size(reason, None, False, wide).y
    source = im.calc_text_size(text, None, False, wide).y
    return float(said + spacing + source)


def _draw_failed(im: Any, reason: str, text: str, wide: float) -> None:
    """
    The reason in the warn ink and the fence's source under it, wrapped to the
    rectangle, which is ``_failed_height`` tall.
    """
    from .. import theme

    im.push_text_wrap_pos(im.get_cursor_pos().x + wide)
    try:
        im.push_style_color(im.Col_.text.value, theme.P.warn.vec4)
        im.text_unformatted(reason)
        im.pop_style_color()
        im.push_style_color(im.Col_.text.value, theme.P.text_dim.vec4)
        im.text_unformatted(text)
        im.pop_style_color()
    finally:
        im.pop_text_wrap_pos()


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
