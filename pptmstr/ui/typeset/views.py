"""
What a pane holds between frames, and the calls that set text at the cursor.

``ViewCache`` is the holder: one per pane, keyed by what is shown, pruned by the
pane with the keys still live. **The panes hold the views**; there is no table
in this package keyed by approval or node.

``markdown``, ``document`` and ``plain`` draw at the cursor and return the
height they claimed. ``stream`` answers markdown still arriving, which a pane
appends to and draws (``Stream``). ``label`` and ``font`` are for a native
ImGui line in a typeset face.

## Who draws what

**Markdown is the library's.** Each run of consecutive markdown parts is one
``imscribe.TypesetView``: one selection, one layout, one draw that culls to the
window. A part is read by the library's own reader (``imscribe.parse_markdown``)
with the theme of its ``demote``, and the blocks of the run are handed to the
view together, so parts of different heading levels share a view.

**Text that is not markdown is ImGui's**, in the chrome's font at the role's
pixel height: a literal, wrapped or exact part, and ``plain()``. What it costs:
such a part is an ImGui text item, so a selection stops at its edges and it is
not copied with the markdown around it; ``Kind.WRAPPED`` and
``Kind.WRAPPED_EXACT`` are both ImGui's wrap, which keeps every character and
breaks at a space where the line has one.

**Except a literal part whose caller asks** (``document(literal=True)``,
``plain(literal=True)``) **and that ``literal_unfit`` finds nothing against**:
that is a view of the library's of its own, holding one host code block
(``MarkdownCode``) of the part's text in ``themes.literal_style``, made as
``gate.literal_view`` makes one (code runs on and the window scrolls to it;
every line one height under the last). Nothing in it is read as markdown and
a copy of it is its text. It is asked for by the log's tool calls, results
and errors and by DETAIL's error, and **by no approval surface**: an
approval's arguments are literal parts of the same roles, and they stay
ImGui's until every hold on an approve is shown on this path as well
(``tests/test_typeset_literal.py`` reads the callers).

So ``document`` is one view only where every part is markdown.
``ViewCache.views(key)`` has every view of a key, in order.

**A part its caller draws** (``Hosted``) is drawn by the ``host`` handed to
``document``, where it falls among the others: the log's tool blocks, which are
ImGui widgets, scroll in one window with the runs the library sets. A host that
does not draw one, and every one with no host, is set as the literal part it
also is, by ImGui. Nothing of it is the library's: it is not searched or selected.

**A fenced diff in a settled document is the library's diff view where a diff
is** (``diffs.adopted``) **and the fence is an honest one** (``diffs.fence_diff``):
hosted in its document's view by the library's diff fences (``_Fences``), at
the diff role's size, with its header lines set above it as code because a
header row of the view is cut at the view's edge. Everywhere else a fence is
the code of its document under its info string. **An open stream does not
host one**: whether a fence still arriving is an honest diff changes with each
line of it, and a block that turned from code into a diff view and back under a
reader is worse than one that is code until its turn settles. **A closed
stream does** (``Stream._ink``): its text has ended, so the session pane's last
run, which stays a stream after its turn ends, draws a diff fence as DETAIL's
settled document of the same text does.

## When the library stops

A view can raise ``imscribe.TypesetUnavailable`` from inside its draw, with the
pane already in its typeset branch. Every call here catches it, says the reason
at the cursor in the pane's own text, returns the height of what it drew, and
tells the gate (``gate.stopped``). ``enabled()`` answers as it did for the rest
of that frame, so a pane that asks it more than once does not change branch
half way down, and is false from the next: the pane is back on its own
renderer.

**Whatever else a call into the library raises is the library stopping too**
(``gate.into_library``): each such call is made inside one of the ``with`` blocks
named at the top of this module, and nothing of pptmstr's own is inside one.
What pptmstr's own code raises here is a defect and is not caught.

## When the library sets less than it was handed

It does that without raising (``gate`` has what was measured): a view's
``draw`` answers false, or the library notes an edit it cut and the draw
answers true. Either way some of the text is on no screen, and on an approval
surface that is text the recipient reads and the approver does not. So a draw
under a key is read both ways (``_short``), and where either says so:

- that frame the key reports every line unset, with a sentence under what was
  drawn, so no decision is offered over it;
- from the next frame **that key's parts are ImGui's, as written**, under one
  line saying why, in the chrome's font and wrapped to the measure: every
  character is then on screen and nothing is reported unset. Markdown is not
  rendered there; its source is what is read.

One key and not the library: a table of 1,100 columns in one agent's message
is no reason to take typeset text from every pane. The parts are asked of the
library again when they change. A stream has no such fallback: it reports its
lines unset and says so for as long as it is drawn, and what it holds is set
again as a document when its turn settles.

**What a caller that decides on the text is told** (``ViewCache.unbuilt``):
the lines the last draw did not set. Zero for anything drawn whole, and all of
them when the library stopped under it or set less than all of it, because
what is on screen then is not the text.

## Nothing is built over more than one frame

A text is read and laid out in the call that first draws it, and a stream
reads what arrived in the draw after it arrived. Timed in pptmstr on
2026-10-12 (``scripts/profile_seam.py``: headless, no rasteriser, load average
0.4, the library's ``demo/sample.md`` repeated, a 700 px window):

- settled markdown, first draw: 15.5 ms for 400 lines and 65.9 ms for 2,000;
  a frame after it 0.09 ms at either length; the first frame at another width
  1.5 ms and 6.7 ms;
- a reply arriving 40 characters a frame, every frame reading the open tail
  again: a median of 0.70 ms and a 95th percentile of 1.17 ms with up to 400
  lines held, 1.86 and 3.09 ms with up to 2,000; no frame of the first run and
  one of 2,638 of the second (37 ms, 400 characters a frame) was over 16.6 ms.

So there is no budget, no piece and no pacing here: the machinery that spread
a long text over frames answered a core whose layout was a second for 2,000
lines, and each of its pieces was a frame in which a pane showed less than it
had been handed. What is left of that question is the first draw of a very
long text, linear in its length and paid once, which a pane with no bound of
its own on what it hands over pays in one frame (DETAIL asks first, past
``review.SETTLED_LINES``; the log bounds what one frame takes in,
``transcript_pane._TYPESET_WINDOW``).

## What a frame costs when nothing moved

A view that is drawn is a call into the library whether or not any of it is in
the window: the library culls its glyphs, and the call, the hit test and the
keys are still paid. Measured in pptmstr on 2026-10-12 (``scripts/bench_panes.py
--typeset on --only views``, four runs: headless, load average 3 to 10): 400
views in one window, a pane's worth of them in sight, are 11 to 17 ms a frame
with nothing new, 0.03 to 0.04 ms a view. A pane of a few views does not notice.
The log is a view for every markdown run, so
``document(rests=True)`` leaves a document that is out of the window and as it
was last drawn alone, and claims the room it took (``_rest``): the same 400
views are then 0.9 to 1.6 ms.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable, Collection, Hashable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from ... import theme as ui_theme
from ...theme import Color
from . import gate, roles, syntax, themes
from .parts import Kind, Part
from .roles import Role, Slot, Weight

_MIN_WIDTH_PX = 80.0
"""
The narrowest measure a view is given; the library's own floor.
"""

_READING = gate.into_library("reading markdown")
_MAKING = gate.into_library("making a view")
_HANDING = gate.into_library("handing a view its text")
_INKING = gate.into_library("inking a view")
_DRAWING = gate.into_library("drawing a view")
_ASKING = gate.into_library("asking a view about its text")
"""
Where this module calls into the library while a pane draws (``gate.into_library``).
Around the calls and nothing else.
"""

_NOT_ALL_SET = "could not set something in view as it was asked to"
"""
What is said of a draw that answered false with no note from the library: its
own words for that answer.
"""


@dataclass(frozen=True, slots=True)
class Hosted(Part):
    """
    A part of a ``document`` its caller draws: ``document``'s ``host`` is asked to,
    where the part falls among the others, with the part's index and the part.

    It is a ``Kind.LITERAL`` part as well, and its text is what a copy of the
    document and ``ViewCache.texts`` give for it, and what is drawn, by ImGui,
    where the host does not draw it. A subclass and not a ``Kind``, because the
    seam reads nothing of it: its kind is the one its text is set as when the
    host declines.
    """


Host = Callable[[int, Part], bool | None]
"""
What draws a ``Hosted`` part at the cursor, handed the part's index in the
document and the part. It answers true when it drew **one item** (a group or a
child), ``None`` when the part is to take no room at all, and false when it
drew nothing and the part is to be set as written.
"""


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
    # For a block of lines set above a hosted diff: how many characters of its
    # text are warnings, drawn in the warn ink (``_Warned``).
    warned: dict[int, int] = field(default_factory=dict)
    # How far right the view's text reaches from its left edge, and the view's
    # revision and the measure that was worked out for.
    reach: tuple[int, float, float] | None = None
    # Where the last draw put it, top and bottom, in pixels down the screen.
    band: tuple[float, float] = (0.0, 0.0)
    # What the view's last draw answered: false when something in view could
    # not be set as asked.
    whole: bool = True
    # Whether the view is of one literal part, set as a host's code block.
    literal: bool = False
    # Whether the part is a ``Hosted`` one, drawn by the caller's host.
    hosted: bool = False


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
    # A markdown part's blocks, by its text, its demotion, whether its fenced
    # diffs were dressed for a diff view and whether it has an ink of its own:
    # a part that only changed its ink for another is not read again.
    read: dict[tuple[str, int, bool, bool], tuple[Any, ...]] = field(default_factory=dict)
    # The pptmstr palette the parts' inks were resolved in.
    inked: object = None
    # The blocks left plain when they were last inked, the frame's lexing
    # being spent (``_Lexing``): each one's segment and its block's index.
    pending: list[tuple[_Segment, int]] = field(default_factory=list)
    # The height claimed when it was last drawn, and the frame that was.
    height: float | None = None
    frame: int = -1
    # Under a cap: how tall the content was when it was last drawn inside its
    # child, and the measure that was at. ``None`` until it has been drawn.
    natural: tuple[float, float] | None = None
    # What the parts were last drawn at: the measure, how far right they ran
    # from the cursor, and pptmstr's theme. ``None`` until they have been.
    shown: tuple[float, float, object] | None = None
    # Whether the last draw left the parts alone, out of the window (``_rest``).
    resting: bool = False
    # Whether each markdown part has a view of its own (``document``).
    apart: bool = False
    # Whether a literal part may be set by the library (``document``).
    literal: bool = False
    # Whether the call draws everything in one ink (``markdown``'s ``ink``): no
    # fence is then coloured by token or told it is too long to be.
    bare: bool = False
    # Source lines handed over that the last draw did not put on screen: all of
    # them when the library stopped inside it or set less than all of them,
    # none otherwise.
    missing: int = 0
    # Why the parts are ImGui's and not the library's: what the library said
    # when it set less than it was handed. ``None`` while they are the library's.
    unset: str | None = None
    # The parts as ImGui draws them then: markdown as its source.
    written: tuple[Part, ...] = ()
    # For the kind ``stream``: the ``Stream`` a pane appends to.
    stream: Stream | None = None
    # For the kind ``diff``: the library's model of what was last handed over,
    # and the model ``diffs.unfit`` was last asked of, with what was read of
    # it then: the lines said above the view (``diffs.above``), and the
    # sentence naming what its rows hold that cannot be seen.
    model: Any = None
    scanned: Any = None
    said: tuple[tuple[str, bool], ...] = ()
    hidden: str | None = None

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
        How many source lines under a key the last draw did not set, or ``None``
        for no view.

        Text is read and laid out in the call that draws it, so this is zero
        for anything that was drawn whole. It is every line of the text when
        the library stopped inside that draw, and when it set less than it was
        handed (the module docstring): what is on screen then is not the text,
        and a caller that offers a decision on the text (``review.withheld``)
        must not offer it.
        """
        entry = self._entries.get(key)
        return None if entry is None else entry.missing

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

    def pointed(self, key: Hashable) -> tuple[int, str] | None:
        """
        What of a key is under the mouse, as it was drawn this frame: the index
        of the part and the text of the block of it, or ``None`` between blocks
        and for a key not drawn. A part ImGui draws is one block, the whole
        width of the window; a stream is part 0.

        A fenced block's text is its code, without the info string the seam
        sets over it. Ask inside the window the key was drawn in, and only when
        that window is hovered: a part ImGui draws is found by where it is, and
        nothing here knows what is in front of it.

        Asked outside a draw, so a library that has stopped is caught here as a
        draw catches it: the answer is ``None`` and the gate is told.
        """
        from imscribe import TypesetUnavailable

        entry = self._entries.get(key)
        if entry is None or entry.resting or entry.missing:
            return None
        im = _imgui()
        if entry.frame != im.get_frame_count():
            return None
        at = im.get_io().mouse_pos.y
        try:
            for segment in entry.segments:
                view = segment.view
                if view is None:
                    index = segment.parts[0]
                    if segment.band[0] <= at < segment.band[1]:
                        return (index, entry.parts[index].text)
                    continue
                with _ASKING:
                    block = view.block_under_mouse()
                    text = "" if block is None else str(view.texts[block])
                if block is None:
                    continue
                said = segment.captions.get(block)
                if said is not None:
                    text = text[said + 1 :]
                return (segment.owner[block] if block < len(segment.owner) else 0, text)
        except TypesetUnavailable as stopped:
            gate.stopped(stopped)
        return None

    def selected(self, key: Hashable) -> str:
        """
        The text selected under a key if the selection is the one in force, or
        ``""``. A selection is inside one of the library's views: it does not
        cross a part ImGui draws.

        Reading a selection is a call into the library, made outside any draw
        (a pane asks from inside a popup). A library that has stopped is caught
        here as a draw catches it: the answer is ``""`` and the gate is told.
        """
        from imscribe import TypesetUnavailable

        try:
            for view in self.views(key):
                with _ASKING:
                    text = view.selected_text() if view.owns_selection else ""
                if text:
                    return str(text)
        except TypesetUnavailable as stopped:
            gate.stopped(stopped)
        return ""

    def _held(self, key: Hashable, kind: str) -> _Entry:
        """
        The entry under a key, made new when there is none, when it was of another
        kind, and when a document built before would be wrong now (the epoch).
        """
        entry = self._entries.get(key)
        if entry is None or entry.kind != kind or entry.epoch != roles._STATE.epoch:
            entry = self._entries[key] = _Entry(kind, roles._STATE.epoch)
        return entry


class _Captioned:
    """
    A library stream whose fenced blocks are read with their info string as the
    first line of their code (``_captioned``); everything else is the stream's.

    A stream view reads its blocks from its stream, so this stands between the
    two. A block that is the object it was is answered with the object it was
    answered with, which is how the view finds the first block that changed.
    ``plain`` is whether the stream is drawn in an ink of its own, so that its
    fences are never coloured and are not told they are too long; the stream
    sets it before the view reads its blocks.

    ``hosting`` is whether an honest diff fence is left for the diff view, as
    ``_open`` leaves one in a settled document; the stream sets it once it is
    closed (``Stream._ink``). Such a fence can be two blocks here, its header
    lines and the fence, so what the view reads beside the blocks is answered
    for these blocks and not the stream's: ``committed`` is as many of them as
    stand for the stream's committed ones, ``sources`` has ``None`` for each
    block that is not the stream's own (the diff fences then read the fence
    from its block, as in a document, which has no source map), and
    ``revision`` changes with ``hosting`` as well, because the view reads the
    blocks again only when it changed.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._made: dict[int, tuple[Any, bool, bool, tuple[Any, ...]]] = {}
        # The blocks last answered, the index of the stream's block each stands
        # for, and whether each has that block's source.
        self._out: tuple[Any, ...] = ()
        self._from: list[int] = []
        self._own: list[bool] = []
        self.plain = False
        self.hosting = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    @property
    def revision(self) -> Any:
        return (self._stream.revision, self.hosting)

    @property
    def blocks(self) -> tuple[Any, ...]:
        made: dict[int, tuple[Any, bool, bool, tuple[Any, ...]]] = {}
        out: list[Any] = []
        came: list[int] = []
        own: list[bool] = []
        plain, hosting = self.plain, self.hosting
        for at, block in enumerate(self._stream.blocks):
            if not getattr(block, "language", ""):
                out.append(block)
                came.append(at)
                own.append(True)
                continue
            held = self._made.get(id(block))
            if held is None or held[0] is not block or held[1:3] != (plain, hosting):
                held = (block, plain, hosting, _captioned((block,), hosting, plain=plain))
            made[id(block)] = held
            answer = held[3]
            out += answer
            came += [at] * len(answer)
            mine = len(answer) == 1 and answer[0].language != _HOSTED_INFO
            own += [mine] * len(answer)
        self._made = made
        self._out, self._from, self._own = tuple(out), came, own
        return self._out

    @property
    def committed(self) -> tuple[Any, ...]:
        done = len(self._stream.committed)
        count = sum(1 for at in self._from if at < done)
        return self._out[:count]

    @property
    def sources(self) -> tuple[Any, ...]:
        theirs = tuple(getattr(self._stream, "sources", ()))
        return tuple(
            theirs[at] if mine and at < len(theirs) else None
            for at, mine in zip(self._from, self._own, strict=True)
        )


class Stream:
    """
    Markdown that is still arriving, as a pane holds it: what ``stream()`` answers.

    The text is kept here and handed to the library's stream when it is drawn,
    so a pane may append before the first frame and while the library cannot
    draw, and a change of face or size (the epoch) sets again what was fed.
    The library reads what arrived at the next draw: committed blocks are laid
    out once and the open tail again at each reading.

    ``draw`` is ``markdown``'s contract: it catches the library stopping, says
    so at the cursor and answers the height it claimed. Draw it in a child that
    scrolls; with ``follow`` the view keeps that child's end in sight until the
    reader scrolls up.
    """

    def __init__(self, entry: _Entry, follow: bool) -> None:
        self._entry = entry
        self._follow = follow
        self._text = ""
        self._closed = False
        self._source: Any = None
        self._captioned: _Captioned | None = None
        self._view: Any = None
        # How much of the text the library's stream has been given, and whether
        # it has been told the text ended.
        self._fed = 0
        self._ended = False
        # The view's revision, pptmstr's theme, the ink it was drawn in and
        # how many blocks were committed when the fences were last inked, or
        # ``None`` when a fence was left plain for a later frame (``_Lexing``).
        self._inked: tuple[int, object, int | None, int] | None = None
        # What the library said when it set less of the text than it was fed.
        self._unset: str | None = None

    @property
    def text(self) -> str:
        return self._text

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def view(self) -> Any:
        """
        The library's view, or ``None`` until the stream has been drawn.
        """
        return self._view

    @property
    def following(self) -> bool:
        return bool(self._view is not None and self._view.following)

    @property
    def unbuilt(self) -> int:
        """
        Source lines given and not yet set (``ViewCache.unbuilt``).
        """
        return self._entry.missing

    def follow_tail(self) -> None:
        """
        Go to the end and follow it again, whatever the reader had scrolled to.
        """
        if self._view is not None:
            self._view.follow_tail()

    def append(self, text: str) -> None:
        """
        The next piece: any size, cut anywhere. Raises ``ValueError`` once closed.
        """
        if self._closed:
            raise ValueError("append() on a closed stream")
        self._text += text

    def close(self) -> None:
        """
        The text has ended: its unfinished last line is set as a finished one.
        """
        self._closed = True

    def set_text(self, text: str, *, closed: bool = False) -> None:
        """
        Make the stream hold ``text``. Text that extends what is held is
        appended; anything else, and any text given to a closed stream, is read
        again from its start.
        """
        if self._closed or not text.startswith(self._text):
            self._forget()
        self._text = text
        self._closed = closed

    def _forget(self) -> None:
        """
        Let go of the library's stream and view: the next draw makes them again
        from the text held.
        """
        if self._view is not None:
            self._view.close()
        self._source = self._view = self._captioned = None
        self._fed = 0
        self._ended = False
        self._inked = None
        self._unset = None
        self._entry.segments = []

    def _catch_up(self) -> None:
        """
        Give the library what it has not had, making its stream and view first
        where there are none. Raises ``TypesetUnavailable`` when there is no
        library to make them with.
        """
        from imscribe import MarkdownStream, TypesetStreamView

        if self._view is None:
            theme, palette = themes.document_theme(), themes.palette()
            with _MAKING:
                self._source = MarkdownStream(theme)
                self._captioned = _Captioned(self._source)
                self._view = TypesetStreamView(
                    self._captioned, palette=palette, follow=self._follow
                )
            self._entry.segments = [_Segment((), self._view)]
        if self._fed < len(self._text):
            arrived = self._text[self._fed :]
            with _HANDING:
                self._source.append(arrived)
            self._fed = len(self._text)
        if self._closed and not self._ended:
            with _HANDING:
                self._source.close()
            self._ended = True

    def _ink(self, ink: Color | None, frame: int) -> None:
        """
        Tell each fence's info string from its code by ink, and colour the
        code by token, as ``_ink`` does for a settled view. Again whenever the
        blocks changed: the library drops the inks of a block an edit replaced.

        A fence is coloured by token once the stream has committed it, which
        the library does once its closing line ends or the stream is closed,
        and until then only its caption is: the block being written is
        replaced at every arrival, and lexing it whole each time costs a frame
        its length allows (500 lines of Python: about 30 ms a lex, measured
        2026-10-15). A closing line that has not ended is not taken as one:
        ``"```"`` inside an open fence is as often the start of
        ``"```python"`` still arriving. So a message that ends on its fence's
        closing marks, with no line end after them, has that fence coloured
        once its run is over and set as a settled document, and not before.
        A stream drawn in an ink of its own is not coloured by token
        (``syntax``). A fence the frame's lexing budget is spent for is left
        plain and coloured on a later frame (``_Lexing``).

        **A closed stream hosts an honest diff fence** as a settled document
        does (``_open``, ``diffs.adopted``): nothing more will arrive, so the
        fence will not turn back into code under its reader, which is why an
        open stream does not host one. The text a stream is closed on is the
        text its turn settled on, so the fence is drawn as the settled
        document of that text draws it, in this view and with no other drawn
        in its place, its warnings in the warn ink (``_Warned``) as well.
        """
        from . import diffs

        view = self._view
        captioned = self._captioned
        if captioned is not None:
            captioned.plain = ink is not None
            captioned.hosting = self._closed and diffs.adopted()
            with _HANDING:
                view.host_for = _fences() if captioned.hosting else None
        with _HANDING:
            view.sync()
            revision = view.revision
        with _ASKING:
            done = len(self._source.committed if captioned is None else captioned.committed)
        now = (revision, ui_theme.current(), None if ink is None else ink.u32, done)
        if self._inked == now:
            return
        with _ASKING:
            blocks = tuple(view.blocks)
            texts = [str(text) for text in view.texts]
            hosted = set(view.hosts)
        captions = {
            at: _caption_length(block)
            for at, block in enumerate(blocks)
            if (said := getattr(block, "language", ""))
            and said != _HOSTED_INFO
            and at not in hosted
        }
        self._entry.segments[0].captions = captions
        warned = self._entry.segments[0].warned = _warned_lengths(blocks)
        for block, length in warned.items():
            with _INKING:
                view.set_colours(block, _warned((), length))
        accent = ui_theme.P.accent.u32
        inks = None if ink is not None else themes.syntax_inks()
        deferred = False
        for block, said in captions.items():
            spans = _LEXING.spans(
                frame, texts[block], said, accent, None, inks if block < done else None
            )
            if spans is None:
                deferred = True
                spans = syntax.block_spans(texts[block], said, accent, None, None)
            with _INKING:
                view.set_colours(block, spans)
        self._inked = None if deferred else now

    def draw(self, *, width: float | None = None, ink: Color | None = None) -> float:
        """
        Take in what has arrived and draw it at the cursor. The height claimed.
        """
        from imscribe import TypesetUnavailable

        im = _frame()
        entry = self._entry
        frame = im.get_frame_count()
        if entry.frame == frame:
            return _said(im, "typeset: a stream drawn twice in one frame", ui_theme.P.danger)
        entry.frame = frame
        top = im.get_cursor_pos().y
        try:
            noted = gate.noted()
            self._catch_up()
            self._ink(ink, frame)
            _show_view(im, entry.segments[0], _width(im, width), themes.palette(ink), None)
            self._unset = _short(entry, noted) or self._unset
            entry.missing = 0
            if self._unset is not None:
                entry.missing = self._text.count("\n") + 1
                _said(im, f"typeset: the library {self._unset}", ui_theme.P.warn)
        except TypesetUnavailable as stopped:
            gate.stopped(stopped)
            entry.missing = self._text.count("\n") + 1
            _said(im, f"typeset text stopped: {stopped}", ui_theme.P.warn)
        entry.height = float(im.get_cursor_pos().y - top)
        return entry.height


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
    from imscribe import TypesetView

    from . import diffs

    hosting = diffs.adopted()
    kept = [
        segment.view
        for segment in entry.segments
        if segment.view is not None and not segment.literal
    ]
    kept_literal = [segment.view for segment in entry.segments if segment.literal]
    read: dict[tuple[str, int, bool, bool], tuple[Any, ...]] = {}
    segments: list[_Segment] = []
    run: list[int] = []

    def close_run() -> None:
        if not run:
            return
        blocks: list[Any] = []
        owner: list[int] = []
        for index in run:
            part = parts[index]
            plain = part.colour is not None or entry.bare
            key = (part.text, part.demote, hosting, plain)
            made = read.get(key, entry.read.get(key))
            if made is None:
                theme = themes.demoted(part.demote)
                with _READING:
                    parsed = gate.read(part.text, theme).blocks
                made = _captioned(parsed, hosting, plain=plain)
            read[key] = made
            blocks += made
            owner += [index] * len(made)
        view: Any = kept.pop(0) if kept else None
        if view is None:
            theme, palette = themes.document_theme(), themes.palette()
            with _MAKING:
                view = TypesetView(theme=theme, palette=palette)
        fences = _fences() if hosting else None
        with _HANDING:
            view.on_link = on_link
            view.host_for = fences
            view.set_document(tuple(blocks))
        captions = {
            at: _caption_length(block)
            for at, block in enumerate(blocks)
            if (said := getattr(block, "language", "")) and said != _HOSTED_INFO
        }
        segment = _Segment(tuple(run), view, tuple(owner), captions)
        segment.warned = _warned_lengths(blocks)
        segments.append(segment)
        run.clear()

    for index, part in enumerate(parts):
        if not part.text.strip():
            continue
        if part.kind is Kind.MARKDOWN:
            if entry.apart:
                close_run()
            run.append(index)
        else:
            close_run()
            if isinstance(part, Hosted):
                segments.append(_Segment((index,), hosted=True))
            elif entry.literal and literal_unfit(part) is None:
                segments.append(_literal_segment(index, part, kept_literal))
            else:
                segments.append(_Segment((index,)))
    close_run()
    entry.parts = parts
    entry.segments = segments
    entry.read = read
    entry.inked = None
    entry.pending = []
    entry.natural = None
    entry.unset = None
    entry.written = ()


def literal_unfit(part: Part) -> str | None:
    """
    Why a literal part is not set by the library, as the words after "ImGui
    draws it because it", or ``None`` for one the library sets
    (``document(literal=True)``). Asked when the parts are read.

    The library sets such a part as a host's code block: never read as
    markdown, every character in it, one source line a line (``views`` module
    docstring). It is given one only where that is the text as written **and
    its columns are measured to hold** (``gate.literal_columns``):

    - unbroken (``Kind.LITERAL``). A part ImGui wraps stays ImGui's: the
      library hangs a wrapped code line by two ems, and a text block sets
      every run of white space as one;
    - in a monospace role;
    - with no control or invisible character (``diffs._hides``). Measured in
      pptmstr on 2026-10-13: the library sets ESC, BEL, BS, DEL, U+200B and
      U+202E as nothing, and a lone carriage return as a line break; ImGui
      draws each as its own glyph or as none, as it always did;
    - with no combining mark the code face has no glyph for (``UNSET_MARKS``).
      Measured in pptmstr on 2026-10-14: the library sets U+FE00, U+FE0E,
      U+FE0F, U+E0100, U+034F, U+180B, U+17B4 and U+20DD as nothing, in no
      cell, where ImGui draws its fallback glyph a cell wide; a mark the face
      has (U+0300, U+0301) both draw in no cell of their own, and a space
      the face lacks (U+2000 to U+2006, U+202F, U+3000) the library sets as
      a blank cell where ImGui draws its fallback glyph, in the same column;
    - not ending in a line break, which the library sets as nothing and a
      copy leaves out;
    - no line longer than the measured row answers for (``Columns.reach``),
      counting two columns a character as the library sets a letter the code
      face lacks.
    """
    from . import diffs

    if part.kind is not Kind.LITERAL:
        return "is broken at the window"
    if roles.style(part.role).slot is not Slot.MONOSPACE:
        return "is in a proportional face"
    if part.text.endswith("\n"):
        return "ends in a line break"
    text = part.text
    if not text.isascii() or not text.isprintable():
        if any(diffs._hides(char) for char in set(text) if char not in "\t\n"):
            return "holds a control or invisible character"
    try:
        with _MAKING:
            theme, style = themes.document_theme(), themes.literal_style(part.role)
    except Exception:
        return "could not be given a style"
    measured = gate.literal_columns(theme, style)
    if not measured.holds:
        return f"is set where the library {measured.why}"
    if 2 * max(len(line) for line in text.split("\n")) > measured.reach:
        return "has a line longer than the measured columns answer for"
    if not text.isascii():
        marks = "".join(c for c in set(text) if unicodedata.category(c) in UNSET_MARKS)
        if marks and gate.code_lacks(marks):
            return "holds a combining mark the code face has no glyph for"
    return None


# Unicode's nonspacing and enclosing marks: set as nothing by the library where
# the code face has no glyph for one (``literal_unfit``, ``diffs.unfit``).
UNSET_MARKS = ("Mn", "Me")

TAB_CELLS = 4
"""
Where a tab puts the next character in literal text: the next multiple of this
many cells from the start of the line, as the library sets it in a code block
(P29) and as a terminal's stops do. Measured in pptmstr on 2026-10-14 at 16 px:
``ab\tc`` puts ``c`` at column 4 in the library and at 6 in ImGui, which draws
a tab as a fixed four spaces; so ImGui is given the tab as spaces to the stop
(``at_tab_stops``) and the same text lines up the same in either renderer.
"""


def at_tab_stops(im: Any, text: str) -> str:
    """
    ``text`` with each tab replaced by the spaces that reach the next stop of
    ``TAB_CELLS`` cells, by ImGui's own pen in the font that is pushed: for
    display only, since a copy is of the text as written.

    The pen and not a count of characters, so that a mark drawn in no cell of
    its own, or a glyph wider than a cell, puts the stop where ImGui will draw.
    """
    if "\t" not in text:
        return text
    cell = float(im.calc_text_size(" ").x) or 1.0
    lines = []
    for line in text.split("\n"):
        if "\t" in line:
            pieces = line.split("\t")
            built = pieces[0]
            for piece in pieces[1:]:
                column = round(float(im.calc_text_size(built).x) / cell)
                built += " " * (TAB_CELLS - column % TAB_CELLS) + piece
            line = built
        lines.append(line)
    return "\n".join(lines)


def _literal_segment(index: int, part: Part, kept: list[Any]) -> _Segment:
    """
    One literal part as a view of the library's holding one host code block,
    keeping a view the part's entry already had (``gate.literal_view``).
    """
    from imscribe import MarkdownCode

    view: Any = kept.pop(0) if kept else None
    if view is None:
        with _MAKING:
            view = gate.literal_view(themes.document_theme())
    with _HANDING:
        view.on_link = None
        view.host_for = None
        view.set_document([MarkdownCode(themes.literal_style(part.role), part.text)])
    return _Segment((index,), view, (index,), literal=True)


def _short(entry: _Entry, noted: object) -> str | None:
    """
    What the library said if the draw that has just ended set less than it was
    handed, or ``None``: a view's ``draw`` answered false, or the library
    noted something since ``noted`` was read (``gate.noted``; an edit it cut).
    """
    now = gate.noted()
    if now is not noted and now:
        return f"said: {now}"
    if not all(segment.whole for segment in entry.segments):
        return _NOT_ALL_SET
    return None


def _as_written(entry: _Entry, why: str) -> None:
    """
    Hand the parts held to ImGui, each as written, from the next draw on (the
    module docstring says when and why).

    Markdown is its source in the chrome's font at the literal role's size,
    wrapped to the measure by ImGui, which breaks inside a run with no space
    in it: the reason this is asked for at all is text that was not on screen.
    The library's views are let go of.
    """
    for segment in entry.segments:
        if segment.view is not None:
            with _HANDING:
                segment.view.close()
    entry.unset = why
    entry.written = tuple(
        (
            replace(part, kind=Kind.WRAPPED, role=Role.LITERAL, tight=False)
            if part.kind is Kind.MARKDOWN
            else part
        )
        for part in entry.parts
    )
    entry.segments = [
        _Segment((index,), hosted=isinstance(part, Hosted))
        for index, part in enumerate(entry.parts)
        if part.text.strip()
    ]
    entry.read = {}
    entry.pending = []
    entry.natural = None
    entry.shown = None


def _captioned(
    blocks: Sequence[Any], hosting: bool = False, *, plain: bool = False
) -> tuple[Any, ...]:
    """
    The blocks with each fence's info string set as the first line of its code.

    The library keeps what follows a fence's opening marks on the block and
    draws none of it. ``python IMPORTANT also delete tests`` is text somebody
    wrote for a reader, so all of it is set. The block keeps the string where
    the library put it as well. A fence too long to colour by token says so
    after it (``syntax.too_long``), in the caption's ink, unless ``plain``:
    its part has an ink of its own, so it is never coloured at any length.

    ``hosting``, a fence that is an honest diff (``diffs.fence_diff``) keeps the
    text the library read and is marked for the diff view that will draw it
    (``_HOSTED_INFO``, ``_Fences``): its info string was the one word that
    says so. The lines the view draws as header rows go above it as a code
    block of their own, which the library breaks at the measure where the view
    would cut them at its edge, led by the fence's warnings
    (``diffs.fence_warnings``), which that block's info string marks for the
    warn ink (``_Warned``).
    """
    from . import diffs

    out: list[Any] = []
    for block in blocks:
        said = getattr(block, "language", "")
        if not said:
            out.append(block)
        elif hosting and (model := diffs.fence_diff(said, block.text)) is not None:
            warnings = diffs.fence_warnings(block.text, model)
            headers = (*warnings, *diffs.fence_header_rows(block.text))
            if headers:
                warned = _Warned(len("\n".join(warnings)))
                out.append(replace(block, text="\n".join(headers), language=warned))
            out.append(replace(block, language=_HOSTED_INFO))
        else:
            note = "" if plain or not syntax.too_long(said, block.text) else syntax.TOO_LONG
            out.append(replace(block, text=f"{said}{note}\n{block.text}"))
    return tuple(out)


def _caption_length(block: Any) -> int:
    """
    How many characters of a block ``_captioned`` captioned its caption is: its
    first line, the info string and any note after it (``syntax.TOO_LONG``).
    """
    return len(block.text.partition("\n")[0])


_HOSTED_INFO = "diff \u2063hosted"
"""
The info string ``_captioned`` gives a fence it left for a diff view, and the
only one ``_Fences`` answers for. A mark on the block and not the rule asked
again, because the rule asked of a block that *was* captioned reads the caption
as a line of the diff: a fence refused for a mail's first line is an honest
fragment once ``diff`` is the line over it. The first word is what the
library's own fences accept. A fence somebody opened with this very string is
two words to ``is_diff_fence``, so it is captioned, and then every line of it
and its info string are on screen whichever of the two draws it.
"""


class _Warned(str):
    """
    The info string ``_captioned`` gives the block of lines it sets above a
    hosted diff: empty, as a block that names no language has, so nothing
    captions it and the library counts it as such; and carrying ``length``,
    how many of the block's first characters are its warnings
    (``diffs.fence_warnings``), which are drawn in the theme's warn ink
    (``_warned``) wherever the block is inked.

    On the info string because the block is the library's frozen
    ``MarkdownCode``, and it is the block, held as it is in a document's
    reading and in a stream's (``_Captioned``), that both inkers are handed.
    A subclass and not a word, as ``_HOSTED_INFO`` is: the library's reader
    gives every fence a plain ``str``, so no fence can be written that is
    taken for one.
    """

    length: int

    def __new__(cls, length: int) -> _Warned:
        made = super().__new__(cls, "")
        made.length = length
        return made


def _warned_lengths(blocks: Sequence[Any]) -> dict[int, int]:
    """
    For each block ``_captioned`` led with warnings, how many characters of it
    they are: what ``_warned`` is handed for that block.
    """
    return {
        at: said.length
        for at, block in enumerate(blocks)
        if isinstance(said := getattr(block, "language", None), _Warned) and said.length
    }


def _warned(spans: Sequence[syntax.Span], length: int | None) -> tuple[syntax.Span, ...]:
    """
    A block's spans with its first ``length`` characters in the warn ink, over
    whatever ink they had: the warnings that lead the lines set above a hosted
    diff (``_Warned``). The spans as they were where ``length`` is none.
    """
    if not length:
        return tuple(spans)
    out: list[syntax.Span] = [(0, length, ui_theme.P.warn.u32)]
    for start, end, ink in spans:
        start = max(start, length)
        if end > start:
            out.append((start, end, ink))
    return tuple(out)


class _Fences:
    """
    The hook that draws a fenced diff in a settled view: the library's own diff
    fences, asked only about a fence ``_captioned`` marked as an honest diff,
    and answering only with a view that draws every line it was given.

    The view reads the fence again from the block as the document view holds
    it, and pptmstr's rule is asked of that reading as well; a fence the two
    readings disagreed about would be code with no ``diff`` over it
    (``tests/test_typeset_diffs.py`` holds them to one text).

    The library gives a fenced diff the theme of the document it is in. A diff
    is ``Role.DIFF`` wherever it is drawn, and it is that theme the columns
    were measured for. Measured in pptmstr on 2026-10-12 with the diff role at
    22 px in a document whose code is 20 (headless, imscribe ``bb240c3``): the
    hosted view's rows are the diff role's pitch, 25 px, in the frame that
    first hosts it, and the height the document claims in that frame is the
    height it claims in the next.

    **The hook is the gate's** (``gate.diff_fences``) and is kept for as long
    as the process runs: a view it hosts is made as ``gate.diff_view`` makes
    one, which is the configuration ``gate.columns`` answers for.
    """

    __slots__ = ("inner",)

    def __init__(self) -> None:
        self.inner: Any = None

    def __call__(self, view: Any, number: int, block: Any) -> Any:
        from . import diffs

        said = getattr(block, "language", None)
        text = getattr(block, "text", None)
        if said != _HOSTED_INFO or not isinstance(text, str):
            return None
        if diffs.fence_diff("diff", text) is None:
            return None
        with _MAKING:
            self.inner = gate.diff_fences(self.inner)
        hosted = self.inner(view, number, block)
        if hosted is None or not diffs.accounted(hosted.text, hosted.view.diff):
            return None
        hosted.view.theme = themes.diff_theme()
        return hosted


_FENCES: list[_Fences] = []


def _fences() -> _Fences:
    """
    The one hook every settled view is given. It keeps nothing of a view: what
    the library's fences hold is on each view's own hosted widgets.
    """
    if not _FENCES:
        _FENCES.append(_Fences())
    return _FENCES[0]


def _ink(entry: _Entry, frame: int) -> None:
    """
    Lay each coloured part's ink over its blocks, in the theme that is live.

    The library drops the inks of a block an edit replaced and keeps the rest,
    so every view is inked whole whenever its parts or the theme changed. A
    block the frame's lexing budget is spent for is left plain and kept in
    ``entry.pending``; a later frame inks those alone, so a document of many
    fences is coloured over a few frames and each is lexed once. An entry drawn
    in one ink (``_Entry.bare``) colours no token. The warnings set above a
    hosted diff are in the warn ink whatever the part's ink (``_Warned``).
    """
    current = ui_theme.current()
    accent = ui_theme.P.accent.u32
    inks = None if entry.bare else themes.syntax_inks()
    work: list[tuple[_Segment, set[int] | None]]
    if entry.inked is not current:
        work = [(segment, None) for segment in entry.segments]
    else:
        left: dict[int, set[int]] = {}
        for segment, block in entry.pending:
            left.setdefault(id(segment), set()).add(block)
        work = [(segment, left[id(segment)]) for segment in entry.segments if id(segment) in left]
    pending: list[tuple[_Segment, int]] = []
    for segment, only in work:
        view = segment.view
        if view is None:
            continue
        with _INKING:
            if only is None:
                view.clear_colours()
            texts = [str(text) for text in view.texts]
            hosted = set(view.hosts)
        for block, index in enumerate(segment.owner):
            if block in hosted:
                # A diff view's inks are its own.
                continue
            if only is not None and block not in only:
                continue
            ink = _colour(entry.parts[index].colour)
            if ink is None and segment.literal:
                # The text's own ink, as ImGui draws it, and not the code role's.
                ink = ui_theme.P.text
            # A fence's info string is told from its code by ink, and the code
            # coloured by token where it names its language (``syntax``).
            said = segment.captions.get(block, 0)
            own = None if ink is None else ink.u32
            spans = _LEXING.spans(frame, texts[block], said, accent, own, inks)
            if spans is None:
                pending.append((segment, block))
                if only is not None:
                    continue
                spans = syntax.block_spans(texts[block], said, accent, own, None)
            spans = _warned(spans, segment.warned.get(block))
            if spans:
                with _INKING:
                    view.set_colours(block, spans)
    entry.pending = pending
    entry.inked = current


class _Lexing:
    """
    How long a frame has spent lexing, across every view it inks, and the
    budget past which a block that would be lexed is left plain for a later
    frame. A lex is not cut short: a frame spends at most the budget and the
    one lex that crossed it, and a block is at most ``syntax.BOUND``
    characters, some 150 ms of the slowest lexer measured. The budget is half
    a 60 Hz frame, the most a frame can give away and still be drawn on time
    with the rest of the window's work; a fence of a few hundred lines is
    some 10 ms, so most frames colour one or two fences each.
    """

    BUDGET = 0.008

    def __init__(self) -> None:
        self.frame = -1
        self.spent = 0.0
        self.clock: Callable[[], float] = time.perf_counter

    def spans(
        self,
        frame: int,
        text: str,
        said: int,
        accent: int,
        ink: int | None,
        inks: themes.SyntaxInks | None,
    ) -> tuple[syntax.Span, ...] | None:
        """
        ``syntax.block_spans``, or ``None`` where it would lex and this frame
        has spent its budget.
        """
        if not syntax.needs_lex(text, said, ink, inks, accent=accent):
            return syntax.block_spans(text, said, accent, ink, inks)
        if frame != self.frame:
            self.frame, self.spent = frame, 0.0
        if self.spent >= self.BUDGET:
            return None
        began = self.clock()
        try:
            return syntax.block_spans(text, said, accent, ink, inks)
        finally:
            self.spent += self.clock() - began


_LEXING = _Lexing()


@contextmanager
def _native_font(im: Any, part: Part) -> Iterator[None]:
    """
    The font ImGui draws a part that is not markdown in: the chrome's for a role
    that is monospace by nature, the library's own entry for the proportional
    face otherwise, at the role's pixel height.
    """
    proportional = roles.style(part.role).slot is Slot.PROPORTIONAL
    im.push_font(
        gate.face_font("PROSE") if proportional else None, gate.pushed(roles.px(part.role))
    )
    try:
        yield
    finally:
        im.pop_font()


def _native(im: Any, part: Part, wide: float) -> None:
    """
    One part that is not markdown, drawn by ImGui exactly as written.

    ``Kind.LITERAL`` is never broken: the window scrolls sideways to a long line
    if it was opened to. In a monospace role its tabs are drawn to the stops
    the library sets them to (``at_tab_stops``).
    """
    with _native_font(im, part):
        literal = part.kind is Kind.LITERAL
        text = part.text
        if literal and "\t" in text and roles.style(part.role).slot is Slot.MONOSPACE:
            text = at_tab_stops(im, text)
        im.push_style_color(im.Col_.text.value, (_colour(part.colour) or ui_theme.P.text).vec4)
        im.push_text_wrap_pos(-1.0 if literal else im.get_cursor_pos_x() + wide)
        try:
            im.text_unformatted(text)
        finally:
            im.pop_text_wrap_pos()
            im.pop_style_color()
    if part.tight:
        # ImGui has put its item spacing under the text; a tight part has none.
        im.set_cursor_pos_y(im.get_cursor_pos_y() - im.get_style().item_spacing.y)


def _show(
    im: Any,
    entry: _Entry,
    wide: float,
    palette: Any,
    on_link: Callable[[str], None] | None,
    host: Host | None = None,
) -> float:
    """
    Draw every part at the cursor. Returns where the last of them ended, in
    pixels down the screen: the foot of the text, with no spacing under it.
    """
    at = im.get_cursor_screen_pos()
    foot = float(at.y)
    right = 0.0
    for segment in entry.segments:
        top = float(im.get_cursor_screen_pos().y)
        if segment.view is None:
            index = segment.parts[0]
            part = (entry.written or entry.parts)[index]
            drew = host(index, part) if segment.hosted and host is not None else False
            if drew is None:
                segment.band = (top, top)
                continue
            if not drew:
                _native(im, part, wide)
            foot = float(im.get_item_rect_max().y)
            right = max(right, float(im.get_item_rect_max().x - at.x))
        else:
            foot = _show_view(im, segment, wide, palette, on_link)
            right = max(right, wide, segment.reach[2] if segment.reach else 0.0)
            if segment.literal and entry.parts[segment.parts[0]].tight:
                im.set_cursor_pos_y(im.get_cursor_pos_y() - im.get_style().item_spacing.y)
        segment.band = (top, foot)
    entry.shown = (wide, right, ui_theme.current())
    entry.resting = False
    return foot


def _rest(im: Any, entry: _Entry, wide: float) -> bool:
    """
    Claim the room the parts took when they were last drawn, and draw nothing,
    if they are out of the window and would be drawn as they were: the same
    parts, measure and theme. False where they have to be drawn.

    The room is the last draw's own measurement, so a document that rests
    moves nothing under it. A view that holds the selection in force is drawn
    wherever it is: the keys that copy it are read in its draw. A document the
    library stopped under is never asked: the way back is a load of the fonts,
    which moves the epoch, and its entry is made again.
    """
    if entry.height is None or entry.shown is None or not entry.segments:
        return False
    was, right, theme = entry.shown
    if was != wide or theme is not ui_theme.current():
        return False
    if im.is_rect_visible(im.ImVec2(max(right, 1.0), entry.height)):
        return False
    for segment in entry.segments:
        view = segment.view
        if view is None:
            continue
        with _ASKING:
            selecting = view.owns_selection and view.selection is not None
        if selecting:
            return False
    # An item's spacing follows the dummy as it followed the last part.
    im.dummy(im.ImVec2(right, max(0.0, entry.height - float(im.get_style().item_spacing.y))))
    entry.resting = True
    return True


def _show_view(
    im: Any, segment: _Segment, wide: float, palette: Any, on_link: Callable[[str], None] | None
) -> float:
    """
    Draw one of the library's views at the cursor and tell the window how far
    right its text runs. Returns the view's foot, in pixels down the screen.
    """
    with _DRAWING:
        segment.view.on_link = on_link
        segment.whole = bool(segment.view.draw(palette=palette, width_px=wide))
    foot = float(im.get_item_rect_max().y)
    reach = _reach(segment, wide)
    if reach > wide + 0.5:
        # Glyphs on a draw list are not items: text past the measure adds
        # nothing to the window's scroll range by being drawn. The window is
        # told, with the cursor left where it was.
        at = im.get_cursor_pos()
        im.dummy(im.ImVec2(reach, 0.0))
        im.set_cursor_pos(at)
    return foot


_WIDEST_EM = 1.5
"""
More than the widest character is, as a multiple of the pixel height it is set
at. Measured in pptmstr on 2026-10-12 over 1,850 characters of Latin, Greek,
Cyrillic, punctuation and symbols in a first-level heading: the widest is
U+2031 at 1.21, and a capital ``W`` is 0.75.
"""


def _tallest_px() -> float:
    """
    The tallest pixel height any text of a document is set at: a role's own, or
    the first heading level's over the heading role's.
    """
    heading = roles.px(Role.HEADING) * max(scale for _, scale, _, _ in themes.HEADINGS)
    return max(heading, *(roles.px(role) for role in Role))


def _reach(segment: _Segment, wide: float) -> float:
    """
    How far right of its left edge a view's text reaches at the measure it was
    just drawn at, in pixels: past ``wide`` where something could not be broken.

    The library breaks a line at a space and nowhere else, never breaks code, and
    does not say how wide the result is (requests P10 and P12). It is asked where
    it set the lines of the blocks that could run over -- code, a table, and text
    holding a run with no space in it that would not fit at ``_WIDEST_EM`` of
    the tallest text a character -- once for a revision of the view at a
    measure, since asking lays those blocks out: about 0.05 ms a block,
    measured on a document of 370.
    """
    view = segment.view
    held = segment.reach
    with _ASKING:
        revision = view.revision
    if held is not None and held[0] == revision and held[1] == wide:
        return held[2]
    reach = 0.0
    longest = wide / (_WIDEST_EM * _tallest_px())
    with _ASKING:
        hosted = set(view.hosts)
        texts = tuple(view.texts)
        roles_of = [view.block_role(block) for block in range(len(texts))]
    for block, text in enumerate(texts):
        if block in hosted:
            # A diff view scrolls its own rows and is as wide as the measure.
            continue
        role = roles_of[block]
        if role == "TABLE":
            with _ASKING:
                geometry = view.table_geometry(block)
                right = None if geometry is None else float(geometry.max[0])
            if right is not None:
                reach = max(reach, right)
        elif role == "CODE" or any(len(word) > longest for word in text.split()):
            with _ASKING:
                rights = [float(line.right) for line in view.set_lines(block, block + 1)]
            reach = max(reach, *rights) if rights else reach
    segment.reach = (revision, wide, reach)
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
            with _ASKING:
                tall += float(segment.view.layout_for(wide))
            literal = segment.literal and entry.parts[segment.parts[0]].tight
            under = 0.0 if literal else gap
            continue
        part = (entry.written or entry.parts)[segment.parts[0]]
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
    host: Host | None = None,
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
        content = _show(im, entry, inner, palette, on_link, host) - top
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
    rests: bool = False,
    apart: bool = False,
    literal: bool = False,
    host: Host | None = None,
) -> float:
    """
    Draw what is held under ``key`` for ``parts``, building what changed. The
    height claimed.
    """
    from imscribe import TypesetUnavailable

    im = _frame()
    entry = cache._held(key, kind)
    frame = im.get_frame_count()
    if entry.frame == frame:
        return _said(im, f"typeset: {key!r} drawn twice in one frame", ui_theme.P.danger)
    entry.frame = frame
    top = im.get_cursor_pos().y
    try:
        wide = _width(im, width)
        same = isinstance(parts, tuple) and entry.token is parts
        if rests and same and height_cap is None and _rest(im, entry, wide):
            return float(entry.height or 0.0)
        noted = gate.noted()
        bare = ink is not None
        if not same or entry.bare is not bare:
            given = parts if isinstance(parts, tuple) else tuple(parts)
            if given != entry.parts or entry.bare is not bare:
                entry.apart = apart
                entry.literal = literal
                entry.bare = bare
                _open(entry, given, on_link)
            entry.token = given
        if entry.inked is not ui_theme.current() or entry.pending:
            _ink(entry, frame)
        if entry.unset is not None:
            _said(im, f"typeset: the library {entry.unset} - shown as written", ui_theme.P.warn)
        if height_cap is None:
            _show(im, entry, wide, themes.palette(ink), on_link, host)
        else:
            _capped(im, key, entry, wide, height_cap, themes.palette(ink), on_link, host)
        entry.missing = 0
        short = None if entry.unset is not None else _short(entry, noted)
        if short is not None:
            # What is on screen is less than was handed over: no line of it
            # is answered for in this frame, and the next is ImGui's.
            entry.missing = sum(part.text.count("\n") + 1 for part in parts)
            _said(im, f"typeset: the library {short}", ui_theme.P.warn)
            _as_written(entry, short)
    except TypesetUnavailable as stopped:
        gate.stopped(stopped)
        # Of the parts handed in, and not of ``entry.parts``: an entry holds
        # its parts only once every view of them was made, and the library can
        # refuse the first.
        entry.missing = sum(part.text.count("\n") + 1 for part in parts)
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
) -> float:
    """
    A markdown document at the cursor: selectable, culled, as wide as the window.
    Returns the height it claimed.

    The source is read when it changes and laid out when the width does; a frame
    where neither moved costs the draw of what is on screen. A source that
    changes keeps its view.

    ``height_cap`` puts a document taller than it in a scrolling child of that
    height; a shorter one takes only its own. ``ink`` draws everything in one
    colour: no fence's code is coloured by token or told it is too long to be,
    and a fence's info string keeps its accent, as in a stream drawn in an ink
    (``Stream.draw``). ``demote`` sets headings that many levels down (``Part.demote``).
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
    rests: bool = False,
    apart: bool = False,
    literal: bool = False,
    host: Host | None = None,
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

    ``rests`` is for a pane that holds many documents in one window: one that
    is out of the window, and is handed the tuple, measure and theme it was
    last drawn with, claims the room it took then and is not drawn (the module
    docstring has what that saves). Nothing of a resting document is under the
    mouse (``ViewCache.pointed``).

    ``apart`` gives every markdown part a view of its own, for a pane whose
    parts arrive one at a time and were each on screen before they were parts
    (the log's runs). Joined, a part is set a block's gap under the markdown
    part before it, which is the library's and depends on what that part ends
    with; apart, it is ImGui's item spacing under the view before it, as it was
    when it was drawn alone, so it does not move when it becomes a part. The
    price is the selection, which then ends with each part. Taken when the
    parts are read: the same for every call under a key.

    ``literal`` lets the library set a ``Kind.LITERAL`` part, as a code block of
    its own, where ``literal_unfit`` finds nothing against it; every other
    literal part, and every one without it, is ImGui's. **A caller asks for it,
    and no role brings it**: an approval's arguments are literal parts of the
    same roles and do not ask (the module docstring). Taken when the parts are
    read, as ``apart`` is.

    ``host`` draws each ``Hosted`` part where it falls (``Host``). It is asked
    in every draw that is not a rest, so what it draws can change from frame to
    frame with the parts unchanged; a resting document claims the room the
    host's drawing took when it was last drawn.
    """
    return _draw(
        cache,
        key,
        "document",
        parts,
        width=width,
        height_cap=height_cap,
        on_link=on_link,
        rests=rests,
        apart=apart,
        literal=literal,
        host=host,
    )


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
    literal: bool = False,
) -> float:
    """
    Text that is not markdown, set exactly as written: an asterisk is an asterisk.

    Drawn by ImGui (the module docstring says why), so it is not selectable,
    unless ``literal`` asks for the library and the library is measured to keep
    its columns.

    - ``wrap=False`` (the default): every source line is one line, spacing and
      indentation kept to the column. A line longer than the window is not
      broken; the window scrolls to it if it was opened with
      ``horizontal_scrollbar`` (with ``height_cap``, the child made here does).
    - ``wrap=True``: broken to the window by ImGui, at a space where the line has
      one. Every character is kept. ``exact`` changes nothing while ImGui draws.

    ``colour`` is the ink; the default is the theme's text. ``role`` is whose
    size the text is set in; ``mono=False`` with ``wrap`` is ``Role.PROSE``, in
    the proportional face. ``literal`` is ``document``'s: with ``wrap=False`` the
    library sets the text where ``literal_unfit`` finds nothing against it, and
    it is then selectable.
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
        literal=literal,
    )


def stream(
    cache: ViewCache,
    key: Hashable,
    *,
    follow: bool = False,
) -> Stream:
    """
    The stream held under a key, made on first asking.

    Nothing is drawn here and the library is not asked for anything, so this
    does not raise where the library cannot draw. ``follow`` is taken when the
    stream is made. What arrived is read at the next draw (the module docstring
    has what that costs).
    """
    entry = cache._entries.get(key)
    if entry is None or entry.kind != "stream" or entry.stream is None:
        entry = cache._entries[key] = _Entry("stream", roles._STATE.epoch)
        entry.stream = Stream(entry, follow)
    elif entry.epoch != roles._STATE.epoch:
        # Another face or size: the text is set again, in the theme there is now.
        entry.epoch = roles._STATE.epoch
        entry.stream._forget()
    return entry.stream


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
    wrapped by ImGui if at all. ``size`` is the pixel height drawn, as a role's
    is, and not ``push_font``'s base (``gate.pushed``).

    **A native line has one font and no fallback.** A typeset view borrows a
    character its face lacks from another face; ImGui does not, and draws its
    replacement glyph. Keep a character the prose faces lack (``→``, ``✓``, box
    drawing) out of a line set in a prose weight, or set the line in
    ``Weight.CODE``.
    """
    im = _frame()
    im.push_font(imfont(weight), gate.pushed(roles.px(Role.LABEL) if size is None else size))
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
