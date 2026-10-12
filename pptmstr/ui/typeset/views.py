"""
What a pane holds between frames, and the calls that set text at the cursor.

``ViewCache`` is the holder: one per pane, keyed by what is shown, pruned by the
pane with the keys still live. **The panes hold the views**; there is no table
in this package keyed by approval or node.

``markdown``, ``document`` and ``plain`` draw at the cursor and return the
height they claimed. ``label`` and ``font`` are for a native ImGui line in a
typeset face. ``stream`` is not built on the library yet and raises
``NotAdopted``; so does ``diff_row_height``.

## Who draws what

**Markdown is the library's.** Each run of consecutive markdown parts is one
``advtext.TypesetView``: one selection, one layout, one draw that culls to the
window. A part is read by the library's own reader (``advtext.parse_markdown``)
with the theme of its ``demote``, and the blocks of the run are handed to the
view together, so parts of different heading levels share a view.

**Text that is not markdown is ImGui's**, in the chrome's font at the role's
pixel height: a literal, wrapped or exact part, and ``plain()``. ``themes`` has
the measurement behind that (monospace set by the library does not keep its
columns; request P21). What it costs: such a part is an ImGui text item, so a
selection stops at its edges and it is not copied with the markdown around it;
``Kind.WRAPPED`` and ``Kind.WRAPPED_EXACT`` are both ImGui's wrap, which keeps
every character and breaks at a space where the line has one.

So ``document`` is one view only where every part is markdown.
``ViewCache.views(key)`` has every view of a key, in order.

## When the library stops

A view can raise ``advtext.TypesetUnavailable`` from inside its draw, with the
pane already in its typeset branch. Every call here catches it, says the reason
at the cursor in the pane's own text, returns the height of what it drew, and
tells the gate (``gate.stopped``). ``enabled()`` answers as it did for the rest
of that frame, so a pane that asks it more than once does not change branch
half way down, and is false from the next: the pane is back on its own
renderer. Anything else raised is a defect and is not caught.

## What is not here

Nothing is built over more than one frame. Timed in pptmstr on 2026-10-12
(headless, load average 2.5; the library's ``demo/sample.md``): reading 112
lines of markdown and making their view is 3.7 ms and the first draw 1.8 ms;
1,120 lines are 32.5 ms and 3.0 ms; a frame after that is 0.07 ms. The bodies
the panes built on this hand over are bounded well under the first figure
(``brief_pane._MAX_BODY_CHARS``, ``board_pane._MAX_DETAIL_CHARS``).
``build_hidden``, ``free_allowance`` and ``notice_above`` are accepted and
change nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Hashable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from ... import theme as ui_theme
from ...theme import Color
from . import gate, roles, themes
from .parts import Kind, Part
from .roles import Role, Slot, Weight

_MIN_WIDTH_PX = 80.0
"""
The narrowest measure a view is given; the library's own floor.
"""


class NotAdopted(RuntimeError):
    """
    A drawing call of the seam that is not built on the library yet was made.

    A pane reaches one only by not asking ``enabled(pane)`` first.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"typeset.{name} draws nothing yet; ask typeset.enabled(pane) before drawing"
        )


@dataclass
class _Segment:
    """
    What one draw makes of consecutive parts: a view of a run of markdown parts,
    or one part ImGui draws.
    """

    # Indices into the entry's parts.
    parts: tuple[int, ...]
    # The library's view, or ``None`` for a part ImGui draws.
    view: Any = None
    # For each block of the view, the index of the part it came from.
    owner: tuple[int, ...] = ()
    # For a fenced block set with its info string as its first line: how many
    # characters of the block's text that line is.
    captions: dict[int, int] = field(default_factory=dict)
    # How far right the view's text reaches from its left edge, and the view's
    # revision and the measure that was worked out for.
    reach: tuple[int, float, float] | None = None


@dataclass
class _Entry:
    """
    What is held under one key: the parts last given, and what draws them.
    """

    kind: str
    epoch: int
    # The parts as the caller gave them (identity is proof nothing changed), and
    # as a tuple of our own.
    token: object = None
    parts: tuple[Part, ...] = ()
    segments: list[_Segment] = field(default_factory=list)
    # A markdown part's blocks, by its text and demotion: a part that only
    # changed its ink is not read again.
    read: dict[tuple[str, int], tuple[Any, ...]] = field(default_factory=dict)
    # The pptmstr palette the parts' inks were resolved in.
    inked: object = None
    # The height claimed when it was last drawn, and the frame that was.
    height: float | None = None
    frame: int = -1
    # Under a cap: how tall the content was when it was last drawn inside its
    # child, and the measure that was at. ``None`` until it has been drawn.
    natural: tuple[float, float] | None = None

    @property
    def view(self) -> Any:
        for segment in self.segments:
            if segment.view is not None:
                return segment.view
        return None


class ViewCache:
    """
    The views one pane holds between frames, by a key of the pane's choosing.

    Imperative shell, like ``review.ReviewState``: presentation state the store does
    not own. One per pane. A key is anything hashable that names *what is shown* --
    an approval id, ``(node, "deliverable")`` -- and the pane prunes with the set of
    keys still live, the way ``ReviewState.prune`` does.
    """

    def __init__(self) -> None:
        self._entries: dict[Hashable, _Entry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def keys(self) -> tuple[Hashable, ...]:
        return tuple(self._entries)

    def prune(self, live: Collection[Hashable]) -> None:
        """
        Drop every view whose key is not in ``live``.
        """
        for stale in [key for key in self._entries if key not in live]:
            del self._entries[stale]

    def drop(self, key: Hashable) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def view(self, key: Hashable) -> Any:
        """
        The library's own view object for a key, or ``None``: the first one, where
        a key's parts took more than one (``views``).
        """
        entry = self._entries.get(key)
        return None if entry is None else entry.view

    def views(self, key: Hashable) -> tuple[Any, ...]:
        """
        Every view of the library's a key holds, in the order drawn. Text ImGui
        draws has none.
        """
        entry = self._entries.get(key)
        if entry is None:
            return ()
        return tuple(segment.view for segment in entry.segments if segment.view is not None)

    def texts(self, key: Hashable) -> tuple[str, ...]:
        """
        The text under a key as it was handed over to be drawn, in order: each
        block of a view as the library has it, and each part ImGui draws whole.
        """
        entry = self._entries.get(key)
        if entry is None:
            return ()
        out: list[str] = []
        for segment in entry.segments:
            if segment.view is None:
                out.append(entry.parts[segment.parts[0]].text)
            else:
                out += segment.view.texts
        return tuple(out)

    def height(self, key: Hashable) -> float | None:
        """
        The height the view claimed when it was last drawn, or ``None``.
        """
        entry = self._entries.get(key)
        return None if entry is None else entry.height

    def unbuilt(self, key: Hashable) -> int | None:
        """
        How many source lines of the view are waiting to be typeset, or ``None``
        for no view. A view that is held is whole.
        """
        return None if key not in self._entries else 0

    def part_at(self, key: Hashable, block: int, view: Any = None) -> int | None:
        """
        Which part of a ``document`` a block of one of its views came from, or
        ``None``. ``view`` says which view the block is of; left out, the first.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        for segment in entry.segments:
            if segment.view is None or (view is not None and segment.view is not view):
                continue
            return segment.owner[block] if 0 <= block < len(segment.owner) else None
        return None

    def _held(self, key: Hashable, kind: str) -> _Entry:
        """
        The entry under a key, made new when there is none, when it was of another
        kind, and when a document built before would be wrong now (the epoch).
        """
        entry = self._entries.get(key)
        if entry is None or entry.kind != kind or entry.epoch != roles._STATE.epoch:
            entry = self._entries[key] = _Entry(kind, roles._STATE.epoch)
        return entry


class Stream(Protocol):
    """
    Markdown that is still arriving, as a pane holds it: what ``stream()`` answers.
    """

    @property
    def text(self) -> str: ...

    @property
    def closed(self) -> bool: ...

    @property
    def view(self) -> Any: ...

    @property
    def following(self) -> bool: ...

    @property
    def unbuilt(self) -> int: ...

    def follow_tail(self) -> None: ...

    def append(self, text: str) -> None: ...

    def close(self) -> None: ...

    def set_text(self, text: str, *, closed: bool = False) -> None: ...

    def draw(self, *, width: float | None = None, ink: Color | None = None) -> float: ...


def pending() -> bool:
    """
    Whether a view on screen is short of its text, so the application's loop
    should not idle. No view is: nothing is built over more than one frame.
    """
    return False


def _imgui() -> Any:
    from imgui_bundle import imgui

    return imgui


def _frame() -> Any:
    """
    ImGui, for a call that draws. Raises outside a frame.

    Every draw here reads the current window, and ImGui with no frame in progress
    has none: the read is a null dereference that takes the interpreter with it.
    A test that reaches the seam through a pane with a mocked ``imgui`` is the way
    to get there, and an exception names the mistake where a crash names nothing.
    """
    im = _imgui()
    context = im.get_current_context()
    if context is None or not context.within_frame_scope:
        raise RuntimeError("typeset: drawn outside an ImGui frame")
    return im


def _width(im: Any, width: float | None) -> float:
    """
    The measure in whole pixels: a pane being dragged reports fractions, and
    re-breaking every paragraph for 480.0 to 480.4 is a layout no line can show.
    """
    wide = im.get_content_region_avail().x if width is None else width
    return float(max(_MIN_WIDTH_PX, int(wide)))


def _said(im: Any, text: str, ink: Color | None = None) -> float:
    """
    One wrapped native line: what the seam says about a view instead of drawing
    it. Its height.
    """
    before = im.get_cursor_pos().y
    im.push_style_color(im.Col_.text.value, (ink or ui_theme.P.text_dim).vec4)
    im.push_text_wrap_pos(0.0)
    im.text_unformatted(text)
    im.pop_text_wrap_pos()
    im.pop_style_color()
    return float(im.get_cursor_pos().y - before)


def _colour(ink: Color | str | None) -> Color | None:
    """
    A part's ink in the theme that is live: a role's name is looked up now.
    """
    if isinstance(ink, str):
        found: Color = getattr(ui_theme.P, ink)
        return found
    return ink


def _open(entry: _Entry, parts: tuple[Part, ...], on_link: Callable[[str], None] | None) -> None:
    """
    Make of ``parts`` what draws them, keeping every view that still has a run of
    markdown to show: a view handed blocks it already has lays nothing out again.
    """
    from advtext import TypesetView

    kept = [segment.view for segment in entry.segments if segment.view is not None]
    read: dict[tuple[str, int], tuple[Any, ...]] = {}
    segments: list[_Segment] = []
    run: list[int] = []

    def close_run() -> None:
        if not run:
            return
        blocks: list[Any] = []
        owner: list[int] = []
        for index in run:
            part = parts[index]
            key = (part.text, part.demote)
            made = read.get(key, entry.read.get(key))
            if made is None:
                made = _captioned(gate.read(part.text, themes.demoted(part.demote)).blocks)
            read[key] = made
            blocks += made
            owner += [index] * len(made)
        view = kept.pop(0) if kept else None
        if view is None:
            view = TypesetView(theme=themes.document_theme(), palette=themes.palette())
        view.on_link = on_link
        view.set_document(tuple(blocks))
        captions = {
            at: len(said)
            for at, block in enumerate(blocks)
            if (said := getattr(block, "language", ""))
        }
        segments.append(_Segment(tuple(run), view, tuple(owner), captions))
        run.clear()

    for index, part in enumerate(parts):
        if not part.text.strip():
            continue
        if part.kind is Kind.MARKDOWN:
            run.append(index)
        else:
            close_run()
            segments.append(_Segment((index,)))
    close_run()
    entry.parts = parts
    entry.segments = segments
    entry.read = read
    entry.inked = None
    entry.natural = None


def _captioned(blocks: Sequence[Any]) -> tuple[Any, ...]:
    """
    The blocks with each fence's info string set as the first line of its code.

    The library keeps what follows a fence's opening marks on the block and
    draws none of it. ``python IMPORTANT also delete tests`` is text somebody
    wrote for a reader, so all of it is set. The block keeps the string where
    the library put it as well.
    """
    return tuple(
        (
            replace(block, text=f"{said}\n{block.text}")
            if (said := getattr(block, "language", ""))
            else block
        )
        for block in blocks
    )


def _ink(entry: _Entry) -> None:
    """
    Lay each coloured part's ink over its blocks, in the theme that is live.

    The library drops the inks of a block an edit replaced and keeps the rest,
    so every view is inked whole whenever its parts or the theme changed.
    """
    for segment in entry.segments:
        view = segment.view
        if view is None:
            continue
        view.clear_colours()
        texts = view.texts
        for block, index in enumerate(segment.owner):
            ink = _colour(entry.parts[index].colour)
            # A fence's info string is told from its code by ink.
            said = segment.captions.get(block, 0)
            spans = [(0, said, ui_theme.P.accent.u32)] if said else []
            if ink is not None and len(texts[block]) > said:
                spans.append((said, len(texts[block]), ink.u32))
            if spans:
                view.set_colours(block, spans)
    entry.inked = ui_theme.current()


@contextmanager
def _native_font(im: Any, part: Part) -> Iterator[None]:
    """
    The font ImGui draws a part that is not markdown in: the chrome's for a role
    that is monospace by nature, the library's own entry for the proportional
    face otherwise, at the role's pixel height.
    """
    proportional = roles.style(part.role).slot is Slot.PROPORTIONAL
    im.push_font(gate.face_font("PROSE") if proportional else None, roles.px(part.role))
    try:
        yield
    finally:
        im.pop_font()


def _native(im: Any, part: Part, wide: float) -> None:
    """
    One part that is not markdown, drawn by ImGui exactly as written.

    ``Kind.LITERAL`` is never broken: the window scrolls sideways to a long line
    if it was opened to.
    """
    with _native_font(im, part):
        im.push_style_color(im.Col_.text.value, (_colour(part.colour) or ui_theme.P.text).vec4)
        im.push_text_wrap_pos(-1.0 if part.kind is Kind.LITERAL else im.get_cursor_pos_x() + wide)
        try:
            im.text_unformatted(part.text)
        finally:
            im.pop_text_wrap_pos()
            im.pop_style_color()
    if part.tight:
        # ImGui has put its item spacing under the text; a tight part has none.
        im.set_cursor_pos_y(im.get_cursor_pos_y() - im.get_style().item_spacing.y)


def _show(
    im: Any, entry: _Entry, wide: float, palette: Any, on_link: Callable[[str], None] | None
) -> float:
    """
    Draw every part at the cursor. Returns where the last of them ended, in
    pixels down the screen: the foot of the text, with no spacing under it.
    """
    foot = float(im.get_cursor_screen_pos().y)
    for segment in entry.segments:
        if segment.view is None:
            _native(im, entry.parts[segment.parts[0]], wide)
            foot = float(im.get_item_rect_max().y)
        else:
            segment.view.on_link = on_link
            segment.view.draw(palette=palette, width_px=wide)
            foot = float(im.get_item_rect_max().y)
            reach = _reach(segment, wide)
            if reach > wide + 0.5:
                # Glyphs on a draw list are not items: text past the measure
                # adds nothing to the window's scroll range by being drawn. The
                # window is told, with the cursor left where it was.
                at = im.get_cursor_pos()
                im.dummy(im.ImVec2(reach, 0.0))
                im.set_cursor_pos(at)
    return foot


def _reach(segment: _Segment, wide: float) -> float:
    """
    How far right of its left edge a view's text reaches at the measure it was
    just drawn at, in pixels: past ``wide`` where something could not be broken.

    The library breaks a line at a space and nowhere else, never breaks code, and
    does not say how wide the result is (requests P10 and P12). It is asked where
    it set the lines of the blocks that could run over -- code, a table, and text
    holding a run with no space in it that would not fit at one and a half body
    heights a character, which is wider than any glyph of any role is set --
    once for a revision of the view at a measure, since asking lays those blocks
    out: about 0.05 ms a block, measured on a document of 370.
    """
    view = segment.view
    held = segment.reach
    if held is not None and held[0] == view.revision and held[1] == wide:
        return held[2]
    reach = 0.0
    longest = wide / (1.5 * roles.body_px())
    for block, text in enumerate(view.texts):
        role = view.block_role(block)
        if role == "TABLE":
            geometry = view.table_geometry(block)
            if geometry is not None:
                reach = max(reach, float(geometry.max[0]))
        elif role == "CODE" or any(len(word) > longest for word in text.split()):
            for line in view.set_lines(block, block + 1):
                reach = max(reach, float(line.right))
    segment.reach = (view.revision, wide, reach)
    return reach


def _estimate(im: Any, entry: _Entry, wide: float) -> float:
    """
    How tall the parts will be ``wide`` across, before anything is drawn: what a
    capped view's child is opened at on its first frame. The library's answer
    for a view is its own estimate until the text has been in sight.
    """
    gap = im.get_style().item_spacing.y
    tall = under = 0.0
    for segment in entry.segments:
        # The spacing under a part counts only when another part follows it.
        tall += under
        if segment.view is not None:
            tall += float(segment.view.layout_for(wide))
            under = gap
            continue
        part = entry.parts[segment.parts[0]]
        with _native_font(im, part):
            wrap = -1.0 if part.kind is Kind.LITERAL else wide
            tall += float(im.calc_text_size(part.text, wrap_width=wrap).y)
        under = 0.0 if part.tight else gap
    return tall


def _capped(
    im: Any,
    key: Hashable,
    entry: _Entry,
    wide: float,
    cap: float,
    palette: Any,
    on_link: Callable[[str], None] | None,
) -> None:
    """
    The parts in a child as tall as they are, up to ``cap``, which scrolls past it.

    The child's measure leaves room for a scrollbar whether or not one is
    showing, so the text is not broken again on the frame one appears.
    """
    style = im.get_style()
    inner = max(_MIN_WIDTH_PX, wide - style.scrollbar_size)
    if entry.natural is None or entry.natural[1] != inner:
        entry.natural = (_estimate(im, entry, inner), inner)
    tall = max(1.0, min(entry.natural[0], float(cap)))
    im.begin_child(
        f"##typeset:{key!r}",
        im.ImVec2(wide, tall),
        0,
        im.WindowFlags_.horizontal_scrollbar.value,
    )
    try:
        top = float(im.get_cursor_screen_pos().y)
        content = _show(im, entry, inner, palette, on_link) - top
        if im.get_scroll_max_x() > 0.0:
            # A scrollbar along the bottom takes its height out of the child.
            content += style.scrollbar_size
        entry.natural = (max(0.0, content), inner)
    finally:
        im.end_child()


def _draw(
    cache: ViewCache,
    key: Hashable,
    kind: str,
    parts: Sequence[Part],
    *,
    width: float | None,
    height_cap: float | None,
    ink: Color | None = None,
    on_link: Callable[[str], None] | None = None,
) -> float:
    """
    Draw what is held under ``key`` for ``parts``, building what changed. The
    height claimed.
    """
    from advtext import TypesetUnavailable

    im = _frame()
    entry = cache._held(key, kind)
    frame = im.get_frame_count()
    if entry.frame == frame:
        return _said(im, f"typeset: {key!r} drawn twice in one frame", ui_theme.P.danger)
    entry.frame = frame
    top = im.get_cursor_pos().y
    try:
        if not (isinstance(parts, tuple) and entry.token is parts):
            given = parts if isinstance(parts, tuple) else tuple(parts)
            if given != entry.parts:
                _open(entry, given, on_link)
            entry.token = given
        if entry.inked is not ui_theme.current():
            _ink(entry)
        wide = _width(im, width)
        if height_cap is None:
            _show(im, entry, wide, themes.palette(ink), on_link)
        else:
            _capped(im, key, entry, wide, height_cap, themes.palette(ink), on_link)
    except TypesetUnavailable as stopped:
        gate.stopped(stopped)
        _said(im, f"typeset text stopped: {stopped}", ui_theme.P.warn)
    entry.height = float(im.get_cursor_pos().y - top)
    return entry.height


def markdown(
    cache: ViewCache,
    key: Hashable,
    source: str,
    *,
    width: float | None = None,
    height_cap: float | None = None,
    ink: Color | None = None,
    on_link: Callable[[str], None] | None = None,
    demote: int = 0,
    build_hidden: bool = False,
    free_allowance: bool = True,
) -> float:
    """
    A markdown document at the cursor: selectable, culled, as wide as the window.
    Returns the height it claimed.

    The source is read when it changes and laid out when the width does; a frame
    where neither moved costs the draw of what is on screen. A source that
    changes keeps its view.

    ``height_cap`` puts a document taller than it in a scrolling child of that
    height; a shorter one takes only its own. ``ink`` draws everything in one
    colour. ``demote`` sets headings that many levels down (``Part.demote``).
    ``on_link`` is called with a link's destination on a click.
    """
    return _draw(
        cache,
        key,
        "markdown",
        (Part(source, demote=demote),),
        width=width,
        height_cap=height_cap,
        ink=ink,
        on_link=on_link,
    )


def document(
    cache: ViewCache,
    key: Hashable,
    parts: Sequence[Part],
    *,
    width: float | None = None,
    height_cap: float | None = None,
    on_link: Callable[[str], None] | None = None,
    build_hidden: bool = False,
    free_allowance: bool = True,
    notice_above: bool = False,
) -> float:
    """
    Several parts in sequence under one key, each read its own way and in its own
    ink. Returns the height claimed.

    Consecutive markdown parts are one view with one selection across them; a
    part that is not markdown is drawn by ImGui between views (the module
    docstring says why).

    **Pass a tuple, and the same one while nothing has changed**: the call then
    compares nothing. Any other sequence is compared part by part on every
    call. A part whose text is as it was is not read again, so parts that only
    changed their ink are inked and nothing is laid out.

    A table inside a coloured markdown part takes the part's ink in its cells.
    ``height_cap`` is ``markdown``'s.
    """
    return _draw(cache, key, "document", parts, width=width, height_cap=height_cap, on_link=on_link)


def plain(
    cache: ViewCache,
    key: Hashable,
    text: str,
    *,
    mono: bool = True,
    wrap: bool = False,
    exact: bool = False,
    colour: Color | None = None,
    width: float | None = None,
    height_cap: float | None = None,
    role: Role = Role.LITERAL,
) -> float:
    """
    Text that is not markdown, set exactly as written: an asterisk is an asterisk.

    Drawn by ImGui (the module docstring says why), so it is not selectable.

    - ``wrap=False`` (the default): every source line is one line, spacing and
      indentation kept to the column. A line longer than the window is not
      broken; the window scrolls to it if it was opened with
      ``horizontal_scrollbar`` (with ``height_cap``, the child made here does).
    - ``wrap=True``: broken to the window by ImGui, at a space where the line has
      one. Every character is kept. ``exact`` changes nothing while ImGui draws.

    ``colour`` is the ink; the default is the theme's text. ``role`` is whose
    size the text is set in; ``mono=False`` with ``wrap`` is ``Role.PROSE``, in
    the proportional face.
    """
    if wrap and not exact:
        kind = Kind.WRAPPED
        if not mono:
            role = Role.PROSE
    else:
        kind = Kind.WRAPPED_EXACT if wrap else Kind.LITERAL
    return _draw(
        cache,
        key,
        "plain",
        (Part(text, kind, colour, tight=True, role=role),),
        width=width,
        height_cap=height_cap,
    )


def stream(
    cache: ViewCache,
    key: Hashable,
    *,
    follow: bool = False,
    every_ms: float = 0.0,
    free_allowance: bool = True,
) -> Stream:
    """
    The stream held under a key, made on first asking.
    """
    raise NotAdopted("stream")


def line_height(*, mono: bool = True, role: Role | None = None) -> float:
    """
    The pitch of one line of a role's text as it is drawn, in pixels: to turn "at
    most 24 lines" into a ``height_cap``.

    With no ``role`` it is literal text's, or prose's with ``mono=False``. A line
    the library sets may be taller than its pitch where a glyph on it is (request
    P13), so a cap in lines is near a whole line and not on one.
    """
    if role is None:
        role = Role.LITERAL if mono else Role.PROSE
    return themes.line_height(role)


def diff_row_height() -> float:
    """
    The height of one row of a ``diff()``, in pixels: to size a cap.
    """
    raise NotAdopted("diff_row_height")


_WEIGHT_FACE: dict[Weight, str] = {
    Weight.REGULAR: "PROSE",
    Weight.BOLD: "PROSE_BOLD",
    Weight.ITALIC: "PROSE_ITALIC",
    Weight.BOLD_ITALIC: "PROSE_BOLD_ITALIC",
    Weight.CODE: "CODE",
    Weight.CODE_BOLD: "CODE_BOLD",
}

_MONO_FACE: dict[Weight, str] = {
    Weight.REGULAR: "CODE",
    Weight.BOLD: "CODE_BOLD",
    Weight.ITALIC: "CODE",
    Weight.BOLD_ITALIC: "CODE_BOLD",
    Weight.CODE: "CODE",
    Weight.CODE_BOLD: "CODE_BOLD",
}
"""
The library's face for each weight, by the name of its ``Face`` member, where
``Role.LABEL`` is proportional and where it is monospace.
"""


def imfont(weight: Weight) -> Any:
    """
    The ``ImFont`` a weight's native line is drawn in, or ``None``, which
    ``push_font`` reads as "keep the current font": the UI font.

    The library's own atlas entry for the face (``gate.face_font``). Without a
    bold monospace file the bold is given up and the columns kept.
    """
    monospace = roles.style(Role.LABEL).slot is Slot.MONOSPACE
    face = (_MONO_FACE if monospace else _WEIGHT_FACE)[weight]
    if face == "CODE_BOLD" and not themes._bold_monospace():
        face = "CODE"
    return gate.face_font(face)


@contextmanager
def font(weight: Weight, size: float | None = None) -> Iterator[None]:
    """
    Push a typeset face for ordinary ImGui widgets: a table cell, a header line.

        with typeset.font(typeset.Weight.BOLD):
            imgui.text_unformatted(title)

    What a pane uses when the honest layout is a native table with cells in the
    document's faces. Text drawn this way is an ImGui item: not selectable, and
    wrapped by ImGui if at all.

    **A native line has one font and no fallback.** A typeset view borrows a
    character its face lacks from another face; ImGui does not, and draws its
    replacement glyph. Keep a character the prose faces lack (``→``, ``✓``, box
    drawing) out of a line set in a prose weight, or set the line in
    ``Weight.CODE``.
    """
    im = _frame()
    im.push_font(imfont(weight), roles.px(Role.LABEL) if size is None else size)
    try:
        yield
    finally:
        im.pop_font()


def label(
    text: str,
    *,
    weight: Weight = Weight.BOLD,
    colour: Color | None = None,
    size: float | None = None,
) -> None:
    """
    One unwrapped line in a typeset face: a row title, a key in a key-value block.

    One font, no fallback: see ``font`` for which characters a prose weight cannot draw.
    """
    im = _imgui()
    with font(weight, size):
        if colour is None:
            im.text_unformatted(text)
        else:
            im.text_colored(colour.vec4, text)
