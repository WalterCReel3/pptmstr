"""
The transcript pane: one agent's output, styled by segment kind.

Three problems collide here, which is why the design expected this part to fight
the library.

**Cost.** A transcript grows without bound and the UI rebuilds every frame, so
re-splitting the whole buffer into lines each frame is O(n) forever. The cache
below splits only the bytes that arrived since last frame, keyed off the published
length a snapshot pinned (I7). That makes a frame with nothing new free, and it does
not make the pane's cost independent of history: taking in one token walks every
segment (``Transcript.segments``; 0.05 ms at 10k lines, 0.5 ms at 100k), and the
line modes filter and join every line each frame for the copy button (``_visible``,
``copy_text``). ``scripts/bench_panes.py`` measures all of it.

**Wrapping.** ``ImGuiListClipper`` needs uniform row heights, and wrapped text does
not have them. Wrapping also cannot be combined with per-line colour and selectable
text -- that is a real gap in ImGui, not a workaround failing. WRAP is what the pane
shows with the seam off anyway, because prose is what the operator reads, and it
pays a price for that: with no clipper only the last ``_WRAP_WINDOW`` lines are
drawn, so scrollback beyond them is unreachable until the mode is switched. RAW is
the way to reach it -- uniform rows and a clipper keep the *drawing* flat, and the
per-frame filter and join above are what a long one costs there: 0.14 ms a frame
idle at 1k lines, 0.9 ms at 10k, 14 ms at 100k.

**Liveness.** Root sessions stream token by token. Sub-agents do not -- their output
arrives in complete messages only (§2.5.1). The pane says which it is looking at
rather than letting the operator infer that a quiet sub-agent is a stuck one.

**Copying.** ImGui text is not selectable, so a run of output is copied whole rather
than dragged over. That is a better unit than a selection anyway: no partial first
line, no dropped trailing newline, and it round-trips into an issue.

**Rendering.** ``RenderMode.RICH`` (planning/archive/2026-08-10-transcript-markdown.md,
step 5) draws markdown structure instead of literal lines, via ``rich_pane`` --
imported as a module rather than name-by-name, since ``rich_pane`` needs
``Block``/``BlockCursor`` types this module also owns and a symbol-level import
either direction risks a cycle as both grow. Search demotes RICH back to the line
path: ``_visible()`` filters at line granularity, which would shred a multi-line
fence into disconnected rows if rich rendering tried to honour it too. While the
seam is on, RICH is what the pane shows until the operator picks a mode
(``DEFAULT_MODE``, which has the measurements that decided it).

**Typesetting.** ``RenderMode.TYPESET`` sets the log through ``typeset`` (the seam
over the layout library). **What is markdown is the library's and what is not is
ImGui's**: OUTPUT and REASONING runs are read as markdown and set by the
library, wrapped, selectable and culled; every other kind -- a tool call, its
result, an error, a system line -- is literal text whose columns are content,
and the library does not keep a monospace column (``typeset`` has the
measurement; request P21 of ``HANDOFF-advtext.md``), so ImGui draws it in the
chrome's font, unbroken, in its ``_KIND_COLOUR``. A fence inside a markdown run
goes with its run and is the library's.

- *closed* runs are the ``typeset.Part``s of a ``typeset.document``, which makes
  one view of the library's for each markdown run. A document holds one
  ``Generation`` of runs, ``_GENERATION_LINES`` lines
  or so, and the oldest generation is what a long watch drops
  (``_TYPESET_KEEP``): the room it took is known when it goes and nothing on
  screen moves (``_trim``). A generation that is out of the pane is not drawn at
  all (``_draw_generation``): a view costs a call whether or not it is in sight,
  and the scrollback is most of a log.
- the *open* run -- the last one, which can still grow -- is a ``typeset.stream``
  when it is markdown, so text already committed is laid out once and what
  arrived is read in the frame that draws it, and ``typeset.plain`` when it is
  literal (``_draw_run``).
- a run that *closes* is a part of its document in the frame it closes. The
  seam reads and lays out what it is handed in the call that draws it, so there
  is no frame in which the run is in neither place or in both.

A kind change closes the open run, so a fence a model left open ends with its run
instead of styling the rest of the session. Nothing else closes it: an open stream
shows everything it was given, and a turn that ends on a run leaves it open for
the next one to go on. ``TypesetFeed`` is the part of this that needs no ImGui.
What the mode costs is on ``DEFAULT_MODE``, and where it is bounded on
``_TYPESET_WINDOW`` and ``_TYPESET_KEEP``.

What the split costs: a selection is inside one of the library's views, so it
ends with its run, and a literal run is copied whole from the context menu and
not by dragging. A table changes its columns' widths in the
frame its run closes (request P19).

A search demotes this mode to the line path as it does RICH: nothing here marks
or scrolls to a match in typeset text. REASONING is a dimmed markdown part, left
out of the parts when the checkbox is off. Follow-the-tail has one owner,
``widgets.follow_tail``: the stream is made with the seam's ``follow`` off.

**When the library stops.** A view can stop inside its draw. That frame says why
where the text would be (the seam's sentence); from the next the seam is off and
the pane is in WRAP, with the operator's choice of mode kept for the day it is
back. The feeds are dropped with it (``draw``).
"""

from __future__ import annotations

import enum
import re
import time
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field

from imgui_bundle import imgui

from ..model import NodeId, Snapshot
from ..theme import P
from ..transcript import SegmentKind, Transcript
from . import rich_pane, typeset, widgets
from .blocks import Block, BlockCursor

# How many lines to draw when wrapping is on and the clipper cannot be used.
# Without a bound, turning wrap on over a large transcript stalls the frame.
_WRAP_WINDOW = 400

_TYPESET_WINDOW = 400
"""
How many lines one ``TypesetFeed.advance`` will typeset; older ones are skipped.

A text is read and laid out in the frame that first draws it, once, and that is
the frame this bounds: the first on a node, and a sub-agent's whole message.
Measured here (``scripts/bench_panes.py --typeset on``, headless, load average 3
to 10, a pane of 900 by 700): the first frame on a session of 1,080 to 10,080
lines is 23 to 71 ms with this window, against RICH's 25 and 227; on 400 turns
of three runs each it is 26 to 93 ms for the last 400 lines (50 views) and 176
to 362 ms with every one of 3,199 lines taken in (400 views), 593 once with the
machine at other work. About half of it is reading the markdown, a part at a
time.

400 is ten panes' worth of scrollback in a first frame no longer than RICH's.
The pane says how many lines it left out and where to read them (RAW). The
bound is on what one advance takes in, not on what the feed holds: lines that
arrive while the node is watched are added as they come, up to
``_TYPESET_KEEP``.
"""

_TYPESET_KEEP = 1000
"""
How many lines of closed runs a ``TypesetFeed`` holds before it drops its oldest
``Generation``.

The window above bounds one advance, so a transcript watched while it streams
would otherwise hold every run it was ever shown, each markdown run a view of
the library's. What a held line costs a frame is small, because a generation out
of the pane is not drawn: on 400 turns the idle frame is 0.6 to 1.1 ms holding
399 lines in 50 views and 0.9 to 1.6 ms holding 3,199 in 400 (same runs). What it
costs to hold is memory, and the first frame at another width, when every
generation is laid out again whether or not it is in sight. A thousand is two
and a half windows of scrollback.

The lines dropped are counted in the row that says where to read them.
"""

_GENERATION_LINES = 250
"""
How many lines of closed runs go into one ``Generation`` before the next is started.

A generation is what the feed drops whole, so this is how far past
``_TYPESET_KEEP`` it gets and how much scrollback goes at once. It is also what
is drawn or left alone as one: every view of a generation any part of which is
in the pane is drawn, thirty or so at this size, which is most of the idle
frame above; and it is how many parts the seam walks when a run closes.
"""

_FEED_KEEP_S = 60.0
"""
How long a ``TypesetFeed`` outlives the last frame its node was shown, in seconds,
against the two a line cache is kept (``_CACHE_KEEP_S``).

In seconds and not in frames because the application does not draw at one rate:
it idles at ``Settings.fps_idle``, nine frames a second by default, and runs at
sixty while an agent works.

The line cache is dropped early because it holds a whole transcript. A feed holds
at most ``_TYPESET_KEEP`` lines of one, and laid out, which is the expensive part:
dropping it with the lines would make every return to a node the first frame
again, on each ``j``/``k`` between two agents being watched in turn. Kept, the
return costs the line rebuild and one culled draw: 4.0 ms at 5,040 lines,
measured (``scripts/bench_panes.py --only prune``), against RICH's 68.
"""

_CACHE_KEEP_S = 2.0
"""
How long a ``NodeTranscript`` outlives the last frame its node was shown, in seconds.
"""


class RenderMode(enum.Enum):
    """Replaces the old ``wrap: bool`` -- a third state (RICH) made a boolean
    the wrong shape, not just a name."""

    RAW = "raw"
    WRAP = "wrap"
    RICH = "rich"
    TYPESET = "typeset"


@dataclass(frozen=True, slots=True)
class Line:
    kind: SegmentKind
    text: str
    # Which run this line belongs to -- see NodeTranscript.run_starts.
    run: int


DEFAULT_MODE = RenderMode.RICH
"""
What the pane shows until the operator picks a mode, while the seam is on. With
the seam off it is WRAP, the pane as it was before there was a seam.

The log is markdown by default, and TYPESET is one radio button away. The rule
recorded for moving it there is about cost: TYPESET is the default if it streams
within 2 ms a frame of RICH at 1,000 and at 10,000 lines **and** leaves no
recurring frame over 30 ms in a long watch. **By measurement it now meets the
first half outright and the second as RICH does (one full collection in a watch
of 52,313 frames, twice), and the default has not moved**, for a reason the
rule does not hold: nobody has looked at the library's text in this pane, and a
fenced block inside a markdown run is set by the library, which does not keep a
monospace column (request P21 of ``HANDOFF-advtext.md``). What an operator
reads first is theirs to choose after seeing it.

Measured here on 2026-10-12 (``scripts/bench_panes.py --typeset on --only
transcript watch``; headless, nothing rasterised, CPU ms inside this pane's
draw, a pane of 900 by 700, text that does not repeat, load average 3 to 10):

- **Nothing new.** TYPESET's median frame is 0.23 ms at 1,080 lines and 0.27 at
  10,080; RICH's is 5.9 and 6.0, WRAP's 1.0 and 2.2.
- **A token a frame.** TYPESET 1.2 and 1.4 ms at the median and 2.0 and 1.6 at
  the 95th percentile; RICH 6.2 and 6.5; WRAP 1.1 and 2.7.
- **The first frame** on a session is 23 to 71 ms for TYPESET, which takes in
  the last ``_TYPESET_WINDOW`` lines whatever the length; RICH's is 25 ms at
  1,080 lines and 227 at 10,080; WRAP's 2.5 and 12.
- **A session watched from its first line**, 20,040 lines sixteen characters a
  frame, 52,313 frames, the collector on and its pauses timed apart, twice:
  TYPESET's median frame is 1.1 to 1.3 ms and its 95th percentile 1.8 to 2.2,
  with one full collection in each watch, 40 and 56 ms. RICH: 5.5 to 5.8 and
  8.3 to 8.4 ms, one full collection in each, 43 and 61 ms. WRAP: 1.9 to 2.6
  and 4.7 to 5.4 ms, no full collection. Frames over 16.6 ms were 1 and 16
  for TYPESET, 35 and 24 for RICH, 0 and 40 for WRAP: the 40 are in a watch
  with no collection at all, on a machine running other work, so that count
  says more about the machine than the mode. In three watches of 5,040 lines
  TYPESET had no full collection, and its longest frame was 5.6, 9.1 and
  14.0 ms.
"""


def mode_named(name: str) -> RenderMode | None:
    """
    The mode a setting names (``Settings.context_mode``), or ``None`` for an empty
    name and for one this build does not have: both mean ``DEFAULT_MODE``.
    """
    try:
        return RenderMode(name)
    except ValueError:
        return None


_MARKDOWN_KINDS = frozenset({SegmentKind.OUTPUT, SegmentKind.REASONING})

_CLOSED_KEY = "closed"

_FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")


def open_fence(texts: Sequence[str]) -> str | None:
    """
    The line that opened the fence these lines end inside, or ``None`` outside one.
    """
    opener: str | None = None
    mark = ""
    for text in texts:
        found = _FENCE_RE.match(text)
        if found is None:
            continue
        ticks, rest = found[1], found[2]
        if opener is None:
            # A backtick fence may not have a backtick in its info string.
            if ticks[0] != "`" or "`" not in rest:
                opener, mark = text, ticks
        elif ticks[0] == mark[0] and len(ticks) >= len(mark) and not rest.strip():
            opener = None
    return opener


@dataclass(frozen=True, slots=True)
class ClosedRun:
    """
    A run that can no longer grow, as the typeset view shows it.

    ``run`` is its index in ``NodeTranscript.run_starts`` and ``lines`` how many of
    the transcript's lines it holds.
    """

    run: int
    kind: SegmentKind
    part: typeset.Part
    lines: int


@dataclass
class OpenRun:
    """
    The last run, which the next bytes may extend.

    ``pieces`` is its text as it arrived and ``lines`` counts the transcript lines
    fed whole.
    """

    run: int
    kind: SegmentKind
    pieces: list[str] = field(default_factory=list)
    lines: int = 0
    _literal: tuple[str, str] = ("", "")

    def add(self, text: str) -> None:
        self.pieces.append(text)

    @property
    def text(self) -> str:
        """
        **The same object until more arrives**, which is what lets a frame with
        nothing new compare nothing.
        """
        if len(self.pieces) > 1:
            self.pieces = ["".join(self.pieces)]
        return self.pieces[0] if self.pieces else ""

    @property
    def literal(self) -> str:
        """
        The text as a literal run is set (``_literal``); the same object until
        more arrives, as ``text`` is.
        """
        text = self.text
        if self._literal[0] is not text:
            self._literal = (text, _literal(text))
        return self._literal[1]


@dataclass
class Generation:
    """
    Consecutive closed runs set as one ``typeset.document``, under one key.

    The measurements are of the last frame the generation was drawn: ``extent``
    is how far it moved the cursor and ``in_sight`` whether any of that was on
    screen.
    """

    serial: int
    runs: list[ClosedRun] = field(default_factory=list)
    lines: int = 0
    extent: float = 0.0
    in_sight: bool = False
    _revision: int = 0
    _shown: tuple[ClosedRun, ...] = ()
    _parts: tuple[typeset.Part, ...] = ()
    _parts_for: tuple[int, bool] | None = None

    @property
    def key(self) -> Hashable:
        return (_CLOSED_KEY, self.serial)

    def append(self, run: ClosedRun) -> None:
        self.runs.append(run)
        self.lines += run.lines
        self._revision += 1

    def shown(self, show_reasoning: bool) -> tuple[ClosedRun, ...]:
        """
        The runs the reasoning filter lets through, one for each of ``parts``.
        """
        self._filter(show_reasoning)
        return self._shown

    def parts(self, show_reasoning: bool) -> tuple[typeset.Part, ...]:
        """
        The runs as parts. **The same tuple until a run is added or taken or the
        filter moves**, which is what lets the seam skip comparing them every frame.
        """
        self._filter(show_reasoning)
        return self._parts

    def _filter(self, show_reasoning: bool) -> None:
        want = (self._revision, show_reasoning)
        if self._parts_for != want:
            self._shown = tuple(
                run for run in self.runs if show_reasoning or run.kind is not SegmentKind.REASONING
            )
            self._parts = tuple(run.part for run in self._shown)
            self._parts_for = want


def _part(kind: SegmentKind, text: str) -> typeset.Part:
    if kind is SegmentKind.OUTPUT:
        # No colour: markdown keeps the palette's role inks for headings, code and links.
        return typeset.Part(text)
    if kind in _MARKDOWN_KINDS:
        return typeset.Part(text, colour=_KIND_COLOUR[kind])
    return typeset.Part(_literal(text), typeset.Kind.LITERAL, colour=_KIND_COLOUR[kind])


def _literal(text: str) -> str:
    """
    A literal run without the newlines around it: the gap between parts separates
    runs, and a leading or trailing newline would be set as an empty line besides.
    """
    return text.strip("\n")


@dataclass
class TypesetFeed:
    """
    One node's lines as what ``RenderMode.TYPESET`` draws: generations of closed
    runs, oldest first, and an open run.

    Fed from ``NodeTranscript.lines`` by a line cursor, lazily -- only by a draw in
    this mode. The cursor and the views it has fed are one object and are dropped
    as one (``TranscriptState.prune``).

    A feed may outlive the ``NodeTranscript`` it read (``_FEED_KEEP_S``). That is
    sound because the lines are a function of the transcript's bytes alone, however
    they were chunked into ``sync`` calls, and the transcript is append-only: a line
    cache built again has the same lines and runs at the same indices, so the cursor
    still points where it did.

    Every line the cursor has passed is in exactly one place: ``skipped`` counts the
    ones above everything typeset, each generation counts its own, and the open run
    has the rest.

    ``advance`` needs no ImGui and builds no view; the views are made by the draw,
    which is also what drops a generation (``_trim``): only a draw knows how much
    room one took.
    """

    views: typeset.ViewCache = field(default_factory=typeset.ViewCache)
    last_shown: float = 0.0
    generations: list[Generation] = field(default_factory=list)
    open: OpenRun | None = None
    # Lines above everything typeset; see _TYPESET_WINDOW and _TYPESET_KEEP.
    skipped: int = 0
    # The first line not fed whole, and how many of its characters have been.
    next_line: int = 0
    partial: int = 0
    # Where the text ended in the window when it was last drawn, from its top.
    end: float = 0.0
    _serial: int = 0

    def advance(self, source: NodeTranscript) -> None:
        """
        Take in the lines that arrived, closing the open run at each change of run.

        More than ``_TYPESET_WINDOW`` new lines at once -- the first frame on a long
        transcript, a sub-agent's whole message -- restarts the feed at the last
        window of them, so what is left out is always above what is shown.
        """
        lines = source.lines
        preamble = ""
        # Fewer lines than were fed is a different transcript under the same node,
        # and nothing typeset from the old one belongs on screen.
        behind = self.next_line > len(lines)
        if behind or len(lines) - self.next_line > _TYPESET_WINDOW:
            start = max(0, len(lines) - _TYPESET_WINDOW)
            self.views.clear()
            self.generations.clear()
            self.open = None
            self.skipped = self.next_line = start
            self.partial = 0
            first = lines[start] if lines else None
            if first is not None and first.kind in _MARKDOWN_KINDS:
                # Starting inside a fence would set its code as prose and read the
                # closing fence as an opening one.
                begin = source.run_bounds(first.run)[0]
                opener = open_fence([line.text for line in lines[begin:start]])
                if opener is not None:
                    preamble = opener + "\n"

        last = len(lines) - 1
        for index in range(self.next_line, len(lines)):
            line = lines[index]
            complete = index < last or not source.open_line
            if self.open is None or self.open.run != line.run:
                self._close()
                self.open = OpenRun(line.run, line.kind)
                if preamble:
                    self.open.add(preamble)
                    preamble = ""
            text = line.text[self.partial :] + ("\n" if complete else "")
            if text:
                self.open.add(text)
            if complete:
                self.open.lines += 1
                self.next_line = index + 1
                self.partial = 0
            else:
                self.partial = len(line.text)

    def _close(self) -> None:
        """
        Put the open run at the end of the newest generation, or start one for it.
        """
        run = self.open
        if run is None:
            return
        self.open = None
        closed = ClosedRun(run.run, run.kind, _part(run.kind, run.text), run.lines)
        newest = self.generations[-1] if self.generations else None
        if newest is None or newest.lines >= _GENERATION_LINES:
            newest = self._generation()
            self.generations.append(newest)
        newest.append(closed)

    def _generation(self) -> Generation:
        self._serial += 1
        return Generation(self._serial)

    @property
    def runs(self) -> list[ClosedRun]:
        """
        Every closed run held, oldest first.
        """
        return [run for generation in self.generations for run in generation.runs]

    @property
    def held(self) -> int:
        """
        How many lines the closed runs hold.
        """
        return sum(generation.lines for generation in self.generations)

    def generation(self, key: Hashable) -> Generation | None:
        """
        The generation a view key belongs to, or ``None`` for any other key.
        """
        for generation in self.generations:
            if generation.key == key:
                return generation
        return None

    def drop_oldest(self) -> Generation:
        """
        Forget the oldest generation. Its lines join the ones above everything shown.
        """
        oldest = self.generations.pop(0)
        self.skipped += oldest.lines
        return oldest

    def shown_open(self, show_reasoning: bool) -> OpenRun | None:
        """
        The open run if the reasoning filter lets it be drawn.
        """
        hidden = (
            self.open is not None and self.open.kind is SegmentKind.REASONING and not show_reasoning
        )
        return None if hidden else self.open

    def visible_text(self, show_reasoning: bool) -> str:
        """
        What the typeset view shows, as source text, for the copy button.
        """
        texts = [
            part.text
            for generation in self.generations
            for part in generation.parts(show_reasoning)
        ]
        shown = self.shown_open(show_reasoning)
        if shown is not None:
            texts.append(shown.text)
        return "\n".join(text.strip("\n") for text in texts if text.strip())


@dataclass
class NodeTranscript:
    """
    Incrementally built lines for one node.

    ``consumed`` is how far into the byte buffer the cache has caught up. It only
    ever moves forward, which is what makes this cheap -- and is safe precisely
    because the transcript is append-only.
    """

    lines: list[Line] = field(default_factory=list)
    consumed: int = 0
    last_shown: float = 0.0
    # Start index of each run; run i spans lines[run_starts[i]:run_starts[i+1]], and
    # the last run ends at len(lines). Append-only for the same reason ``lines`` is.
    #
    # A run is a maximal same-kind span, which is *not* quite the same as a
    # Transcript segment: close_segment() can split two same-kind segments mid-line,
    # and sync() then extends one Line across that split. Segment identity therefore
    # is not representable at line granularity, but a kind change always falls on a
    # line boundary -- so a run is what the operator sees, one coloured span of text.
    run_starts: list[int] = field(default_factory=list)
    # Whether the last line can still be extended -- true when the bytes so far did
    # not end on a newline. Tracking this explicitly, rather than inferring it from
    # the next chunk, is what keeps a streamed token appending to the line it
    # belongs to instead of starting a new one.
    open_line: bool = False
    # Markdown block structure, fed lazily -- only when RenderMode.RICH is actually
    # selected (see draw()). A node nobody is viewing in RICH mode pays nothing for
    # this; blocks.py's own docstring is why it does not feed itself eagerly here.
    block_cursor: BlockCursor = field(default_factory=BlockCursor)

    def sync(self, transcript: Transcript, limit: int) -> None:
        """Convert newly published bytes into lines, up to ``limit``."""
        if limit <= self.consumed:
            return
        for segment in transcript.segments(limit=limit):
            if segment.end <= self.consumed:
                continue
            start = max(segment.start, self.consumed)
            text = transcript.read(start, segment.end)
            if not text:
                continue

            ends_with_newline = text.endswith("\n")
            pieces = text.split("\n")
            if ends_with_newline:
                # "a\n" splits to ["a", ""]. Emitting that empty tail as a row is
                # what double-spaces a transcript; the newline terminates a line
                # rather than starting a blank one.
                pieces = pieces[:-1]

            for index, piece in enumerate(pieces):
                extends = (
                    index == 0
                    and self.open_line
                    and bool(self.lines)
                    and self.lines[-1].kind is segment.kind
                )
                if extends:
                    last = self.lines[-1]
                    self.lines[-1] = Line(last.kind, last.text + piece, last.run)
                else:
                    if not self.lines or self.lines[-1].kind is not segment.kind:
                        self.run_starts.append(len(self.lines))
                    self.lines.append(Line(segment.kind, piece, len(self.run_starts) - 1))

            self.open_line = not ends_with_newline
        self.consumed = limit

    def run_bounds(self, run: int) -> tuple[int, int]:
        """The half-open line range of a run, or an empty range if it does not exist."""
        if not 0 <= run < len(self.run_starts):
            return (0, 0)
        start = self.run_starts[run]
        end = self.run_starts[run + 1] if run + 1 < len(self.run_starts) else len(self.lines)
        return (start, end)

    def run_text(self, run: int) -> str:
        """
        A whole run, for the clipboard.

        Deliberately reads through the *unfiltered* lines: what is copied is the run
        as the agent emitted it, not the subset a search happens to be showing. The
        filtered view has its own affordance.
        """
        start, end = self.run_bounds(run)
        return copy_text(self.lines[start:end])


@dataclass
class TranscriptState:
    """Presentation state for the pane. Never enters the store (design §6)."""

    caches: dict[NodeId, NodeTranscript] = field(default_factory=dict)
    # What RenderMode.TYPESET draws for a node, made by the first draw in that mode
    # and kept longer than the node's line cache; see _FEED_KEEP_S.
    feeds: dict[NodeId, TypesetFeed] = field(default_factory=dict)
    show_reasoning: bool = True
    # What the operator chose, or None until they choose. What is drawn is
    # ``shown_mode()``.
    mode: RenderMode | None = None
    # The mode the operator's settings start the pane in, as the last ``draw`` was
    # told it: written by every draw and by nothing else, so it is the setting and
    # never a copy of it that could go stale.
    start_in: RenderMode | None = None
    follow_tail: bool = True
    search: str = ""
    frame: int = 0
    # When the frame being drawn began, on the clock ``prune`` was given.
    now: float = 0.0
    # The run the context menu acts on. Latched while the menu is closed, because
    # opening it moves the hover onto the popup and would otherwise clear the target
    # out from under the item the operator just right-clicked.
    context_run: int | None = None
    # RICH mode's own hover/context-menu latch -- see RichState's docstring for why
    # it is a separate field rather than reusing context_run.
    rich: rich_pane.RichState = field(default_factory=rich_pane.RichState)
    # TYPESET's context-menu latch: the run under the mouse, where there is one,
    # and the text of the block of it. Latched as context_run is, and as text
    # rather than as a place in a view: a run can close while the menu is open,
    # and what is copied is what was clicked.
    typeset_target: tuple[int | None, str] | None = None

    def shown_mode(self) -> RenderMode:
        """
        The mode drawn this frame: the one chosen in the pane, or until one is the
        one the settings start it in (``start_in``), or with neither the default
        (``DEFAULT_MODE``, and WRAP with the seam off).

        TYPESET needs the seam, and without it the pane is in WRAP. Derived each
        frame and never written back, so switching the seam off and on again
        returns the operator to what they had chosen.
        """
        seam = typeset.enabled(typeset.Pane.CONTEXT)
        wanted = self.start_in if self.mode is None else self.mode
        if wanted is None:
            return DEFAULT_MODE if seam else RenderMode.WRAP
        if wanted is RenderMode.TYPESET and not seam:
            return RenderMode.WRAP
        return wanted

    def cache_for(self, node_id: NodeId) -> NodeTranscript:
        cache = self.caches.get(node_id)
        if cache is None:
            cache = NodeTranscript()
            self.caches[node_id] = cache
        cache.last_shown = self.now
        return cache

    def feed_for(self, node_id: NodeId) -> TypesetFeed:
        feed = self.feeds.get(node_id)
        if feed is None:
            feed = TypesetFeed()
            self.feeds[node_id] = feed
        feed.last_shown = self.now
        return feed

    def prune(self, frame: int, now: float | None = None) -> None:
        """
        Drop caches for nodes nobody is looking at. ``now`` is the monotonic clock,
        passed by a test that needs time to pass.

        A transcript cache is the largest thing this UI holds, so keeping one per
        node ever selected is how a long session turns into a memory leak.
        """
        self.frame = frame
        self.now = time.monotonic() if now is None else now
        stale = [n for n, c in self.caches.items() if c.last_shown < self.now - _CACHE_KEEP_S]
        for node_id in stale:
            del self.caches[node_id]
        idle = [n for n, f in self.feeds.items() if f.last_shown < self.now - _FEED_KEEP_S]
        for node_id in idle:
            del self.feeds[node_id]


_CONTEXT_ID = "##transcript_context"


def copy_text(lines: Sequence[Line]) -> str:
    """
    Lines as one clipboard string.

    Unbounded on purpose: the window caps in this module are about frame cost, and
    the clipboard has no frame.
    """
    return "\n".join(line.text for line in lines)


_KIND_COLOUR = {
    SegmentKind.REASONING: "text_dim",
    SegmentKind.OUTPUT: "text",
    SegmentKind.TOOL_CALL: "accent",
    SegmentKind.TOOL_RESULT: "diff_context",
    SegmentKind.ERROR: "danger",
    SegmentKind.SYSTEM: "text_dim",
    SegmentKind.COMPACTION: "warn",
}


def draw(
    snap: Snapshot,
    state: TranscriptState,
    selected: NodeId | None,
    *,
    start_in: RenderMode | None = None,
) -> None:
    """
    ``start_in`` is the mode the operator's settings start the log in
    (``mode_named(Settings.context_mode)``); left out, it is ``DEFAULT_MODE``.
    """
    state.start_in = start_in
    if selected is None or (record := snap.get(selected)) is None:
        imgui.text_disabled("select an agent to read its transcript")
        return

    cache = state.cache_for(selected)
    # Pin the length once. Everything below renders a consistent prefix even while
    # the asyncio thread keeps appending (I7).
    pinned = record.transcript.published_length
    cache.sync(record.transcript, pinned)

    mode = state.shown_mode()
    if state.feeds and not typeset.enabled(typeset.Pane.CONTEXT):
        # A feed is the library's views of lines this pane still has. With the
        # seam off they are views nothing draws, and after the library stopped
        # they are views of a library that may never draw again: the lines set
        # them again in one frame if it does.
        state.feeds.clear()

    # RICH and TYPESET demote to the line path while a search filter is active --
    # see the module docstring's "Rendering" section for why block-level filtering
    # does not compose with _visible()'s line-granular one.
    if mode is RenderMode.TYPESET and not state.search:
        feed = state.feed_for(selected)
        feed.advance(cache)
        # Joined when the button is pressed: unlike the line modes' value, this one
        # is not needed to draw anything.
        _draw_controls(state, record.node_id, lambda: feed.visible_text(state.show_reasoning), mode)
        imgui.separator()
        _draw_typeset(state, feed, cache)
        return

    if mode is RenderMode.RICH and not state.search:
        stable = len(cache.lines) - (1 if cache.open_line else 0)
        cache.block_cursor.feed(cache.lines[:stable])
        live = cache.block_cursor.live_block(cache.lines)
        shown = rich_pane.windowed(cache.block_cursor.blocks)
        copy_value = rich_pane.block_copy_text(shown + ([live] if live is not None else []))
        _draw_controls(state, record.node_id, lambda: copy_value, mode)
        imgui.separator()
        _draw_rich(state, cache, live)
        return

    visible = _visible(state, cache)
    visible_text = copy_text(visible)
    _draw_controls(state, record.node_id, lambda: visible_text, mode)
    imgui.separator()
    _draw_lines(state, cache, visible, mode)


def _draw_controls(
    state: TranscriptState, node_id: NodeId, copy_value: Callable[[], str], shown: RenderMode
) -> None:
    changed, state.show_reasoning = imgui.checkbox("reasoning", state.show_reasoning)
    imgui.same_line()
    # Four modes fill a docked pane's first row. With the seam on, a control that
    # does not fit starts a second row rather than sitting off the right edge,
    # where the filter cannot be reached at all, and the filter gives up width
    # before it takes a third.
    flows = typeset.enabled(typeset.Pane.CONTEXT)
    scale = imgui.get_font_size() / 16.0
    style = imgui.get_style()
    gap = style.item_spacing.x
    box = imgui.get_frame_height() + style.item_inner_spacing.x
    copy_width = imgui.calc_text_size("copy").x + 2 * style.frame_padding.x
    filter_width = 200 * scale

    def flow(width: float) -> None:
        if flows and imgui.get_content_region_avail().x < width:
            imgui.new_line()

    for mode in RenderMode:
        if mode is RenderMode.TYPESET and not flows:
            continue
        flow(box + imgui.calc_text_size(mode.value).x)
        if imgui.radio_button(mode.value, shown is mode):
            state.mode = mode
        imgui.same_line()
    flow(box + imgui.calc_text_size("follow").x + 80 * scale + copy_width + 2 * gap)
    _, state.follow_tail = imgui.checkbox("follow", state.follow_tail)
    imgui.same_line()
    if flows:
        room = imgui.get_content_region_avail().x - copy_width - gap
        filter_width = max(80 * scale, min(filter_width, room))
    imgui.set_next_item_width(filter_width)
    _, state.search = imgui.input_text_with_hint("##search", "filter", state.search)
    imgui.same_line()
    # Copies what is on screen, filters included -- the button sits next to the
    # filters that decide its contents. Right-clicking a run (or block) copies
    # that run/block whole, unfiltered; the two affordances are deliberately
    # different units.
    if imgui.small_button("copy") and (copied := copy_value()):
        imgui.set_clipboard_text(copied)

    if shown is RenderMode.RICH and not flows:
        imgui.same_line()
        imgui.text_disabled("(rich: search falls back to raw lines)")

    if flows:
        # With the seam on the filter has taken the rest of the controls' row, so
        # a note has a row of its own, and a second in a dock narrower than it is.
        imgui.push_text_wrap_pos(0.0)
    if node_id[1] is not None:
        # Sub-agent output does not stream (§2.5.1). Saying so beats letting a quiet
        # row be read as a stuck one.
        if not flows:
            imgui.same_line()
        imgui.text_colored(P.warn.vec4, "sub-agent: complete messages only, not live")
    if flows and state.search and shown in (RenderMode.RICH, RenderMode.TYPESET):
        # The only sign that what is below is not the log as the mode sets it.
        imgui.text_disabled(f"filtering: raw lines ({shown.value} has no find)")
    if flows:
        imgui.pop_text_wrap_pos()


def _visible(state: TranscriptState, cache: NodeTranscript) -> list[Line]:
    needle = state.search.lower()
    return [
        line
        for line in cache.lines
        if (state.show_reasoning or line.kind is not SegmentKind.REASONING)
        and (not needle or needle in line.text.lower())
    ]


def _draw_lines(
    state: TranscriptState,
    cache: NodeTranscript,
    lines: list[Line],
    mode: RenderMode | None = None,
) -> None:
    if mode is None:
        mode = state.shown_mode()
    if not lines:
        imgui.text_disabled("(nothing yet)" if not cache.lines else "(no matching lines)")
        return

    if not imgui.begin_child("##transcript"):
        imgui.end_child()
        return

    # Hit-test on the row's vertical span alone, so the whole width of a short line
    # is a target. Testing the item rect in x instead would make a two-character line
    # a sliver, which is exactly the line an operator reaches for.
    #
    # A row's band runs from the *previous* row's bottom to its own, which hands the
    # inter-row spacing to the row below it. Using the item rect at both ends instead
    # leaves a few dead pixels between every line -- a gutter that swallows a
    # right-click and answers with a menu that has lost its target.
    live = imgui.is_window_hovered()
    mouse_y = imgui.get_io().mouse_pos.y
    hovered: int | None = None
    row_top: float | None = None

    def track(line: Line) -> None:
        nonlocal hovered, row_top
        top = imgui.get_item_rect_min().y if row_top is None else row_top
        row_top = imgui.get_item_rect_max().y
        if live and top <= mouse_y < row_top:
            hovered = line.run

    if mode is RenderMode.WRAP:
        # No clipper: wrapped rows have no uniform height for it to work from. The
        # window bound is what keeps that affordable.
        for line in lines[-_WRAP_WINDOW:]:
            imgui.push_style_color(imgui.Col_.text, getattr(P, _KIND_COLOUR[line.kind]).vec4)
            imgui.text_wrapped(line.text or " ")
            imgui.pop_style_color()
            track(line)
    else:
        clipper = imgui.ListClipper()
        clipper.begin(len(lines))
        while clipper.step():
            for index in range(clipper.display_start, clipper.display_end):
                line = lines[index]
                imgui.text_colored(getattr(P, _KIND_COLOUR[line.kind]).vec4, line.text or " ")
                track(line)
        clipper.end()

    if not imgui.is_popup_open(_CONTEXT_ID):
        state.context_run = hovered
    _draw_context_menu(state, cache, lines)

    _handle_scroll(state)
    imgui.end_child()


def _draw_rich(state: TranscriptState, cache: NodeTranscript, live: Block | None) -> None:
    if not imgui.begin_child("##transcript"):
        imgui.end_child()
        return

    if not cache.block_cursor.blocks and live is None:
        imgui.text_disabled("(nothing yet)")
    else:
        rich_pane.draw(state.rich, cache.block_cursor.blocks, live)

    _handle_scroll(state)
    imgui.end_child()


def _run_key(run: int) -> tuple[str, int]:
    """
    The key of the view the open run is drawn by.
    """
    return ("open", run)


def _draw_run(views: typeset.ViewCache, run: OpenRun, live: list[Hashable]) -> None:
    """
    The open run in a view of its own: a stream for markdown, which lays out once
    what is committed and reads what arrived, and ImGui's text for the rest.
    """
    ink = getattr(P, _KIND_COLOUR[run.kind])
    key = _run_key(run.run)
    live.append(key)
    if run.kind not in _MARKDOWN_KINDS:
        typeset.plain(views, key, run.literal, colour=ink)
        return
    stream = typeset.stream(views, key)
    text = run.text
    # By length and not by what was last handed over: a stream made again (the
    # reasoning shown after being hidden, a feed kept across a stop) is given
    # everything, and one that has it all is given nothing.
    if len(stream.text) < len(text):
        stream.append(text[len(stream.text) :])
    stream.draw(ink=None if run.kind is SegmentKind.OUTPUT else ink)


def _draw_typeset(state: TranscriptState, feed: TypesetFeed, cache: NodeTranscript) -> None:
    # The scrollbar is always there so that the measure does not change, and every
    # paragraph re-break, on the frame the text first outgrows the pane. Literal
    # runs are never broken, hence the horizontal one.
    flags = (
        imgui.WindowFlags_.always_vertical_scrollbar.value
        | imgui.WindowFlags_.horizontal_scrollbar.value
    )
    if not imgui.begin_child("##transcript", imgui.ImVec2(0, 0), 0, flags):
        imgui.end_child()
        return

    show = state.show_reasoning
    # Every key drawn this frame, in the order drawn.
    live: list[Hashable] = []

    announced = feed.skipped > 0
    lost = _trim(feed)
    top = imgui.get_cursor_pos_y()
    if feed.skipped:
        imgui.text_disabled(f"{feed.skipped:,} earlier lines: switch to raw")
    if lost:
        if not announced:
            lost = max(0.0, lost - (imgui.get_cursor_pos_y() - top))
        # The room the dropped generation took stays for this frame: the window's
        # scroll position is the last frame's, and everything below would be drawn
        # that much too high.
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + lost)

    shown = feed.shown_open(show)
    if not feed.generations and shown is None and not feed.skipped:
        imgui.text_disabled("(nothing yet)")
    for generation in feed.generations:
        _draw_generation(feed, generation, show, live)
    if shown is not None:
        _draw_run(feed.views, shown, live)
    feed.views.prune(live)

    if not imgui.is_popup_open(_CONTEXT_ID):
        hovered = imgui.is_window_hovered()
        state.typeset_target = _typeset_pointed(feed, live, show) if hovered else None
    _draw_typeset_menu(state, feed, cache)

    end = imgui.get_cursor_pos_y()
    following = state.follow_tail
    _handle_scroll(state)
    if lost:
        # Next frame the room kept above is gone, and the window must be scrolled
        # that much less for what is on screen to stay where it is. Pinned to the
        # tail, that is the furthest it could scroll last frame, and however much
        # the text has grown since.
        pinned = following and state.follow_tail
        base = imgui.get_scroll_max_y() + end - feed.end if pinned else imgui.get_scroll_y()
        imgui.set_scroll_y(max(0.0, base - lost))
    feed.end = end - lost
    imgui.end_child()


def _trim(feed: TypesetFeed) -> float:
    """
    Drop the oldest generation once the feed holds more than ``_TYPESET_KEEP`` lines.
    Returns the room it took in the window last frame, or ``0.0`` if none was dropped.

    One a frame. A generation the operator has on screen is left until the feed
    holds twice the bound: taking away what is being read is the worse of the two
    until then.
    """
    held = feed.held
    if len(feed.generations) < 2 or held <= _TYPESET_KEEP:
        return 0.0
    if feed.generations[0].in_sight and held <= 2 * _TYPESET_KEEP:
        return 0.0
    return feed.drop_oldest().extent


def _draw_generation(
    feed: TypesetFeed, generation: Generation, show: bool, live: list[Hashable]
) -> None:
    """
    One generation, as one document. A run that closed is in it from the frame it
    closed: the seam reads and lays out what it is handed in the call that draws it.
    Each run is a view of its own (``apart``), as it was while it was open, so it
    is where it was: joined to a markdown run above it, it would be set the
    library's block gap under that run and not ImGui's item spacing, 2.1 px
    lower for a paragraph under a paragraph, in the frame it closed.

    Out of the window and unchanged, the document rests (``typeset.document``):
    a generation is twenty views or more, and the scrollback is most of the log.
    """
    start = imgui.get_cursor_pos_y()
    wide = max(1.0, imgui.get_content_region_avail().x)
    generation.in_sight = generation.extent > 0.0 and imgui.is_rect_visible(
        imgui.ImVec2(wide, generation.extent)
    )
    parts = generation.parts(show)
    if parts:
        live.append(generation.key)
        typeset.document(feed.views, generation.key, parts, rests=True, apart=True)
    generation.extent = imgui.get_cursor_pos_y() - start


def _typeset_pointed(
    feed: TypesetFeed, drawn: Sequence[Hashable], show: bool
) -> tuple[int | None, str] | None:
    """
    The run under the mouse and the text of the block of it, or ``None`` between
    blocks. A block is a paragraph, a fence, a table, or a whole literal run.
    """
    for key in drawn:
        found = feed.views.pointed(key)
        if found is not None:
            part, text = found
            return (_typeset_run(feed, key, part, show), text)
    return None


def _typeset_run(feed: TypesetFeed, key: Hashable, part: int, show: bool) -> int | None:
    """
    The run a part of a key belongs to, as its index in ``NodeTranscript.run_starts``.

    A generation's document holds only the parts the reasoning filter lets
    through, so its part numbers are not the generation's run numbers.
    """
    generation = feed.generation(key)
    if generation is None:
        # The open run's view, which is keyed by the run.
        return key[1] if isinstance(key, tuple) and isinstance(key[1], int) else None
    runs = generation.shown(show)
    return runs[part].run if 0 <= part < len(runs) else None


def _draw_typeset_menu(state: TranscriptState, feed: TypesetFeed, cache: NodeTranscript) -> None:
    """
    Copy the selection, the block or the run that was right-clicked, or everything shown.

    The run is copied as the line modes copy it: whole and unfiltered, from the
    lines, so a run the window cut short at the top is copied from its first line.
    A selection is inside one of the library's views, so it ends with its run.
    """
    if not imgui.begin_popup_context_window(_CONTEXT_ID, imgui.PopupFlags_.mouse_button_right):
        return

    for key in feed.views.keys():
        if (selected := feed.views.selected(key)) and imgui.menu_item_simple("copy selection"):
            imgui.set_clipboard_text(selected)
    if state.typeset_target is not None:
        run, block = state.typeset_target
        if block and imgui.menu_item_simple("copy block"):
            imgui.set_clipboard_text(block)
        if run is not None:
            _copy_run_item(cache, run)
    if imgui.menu_item_simple("copy visible"):
        imgui.set_clipboard_text(feed.visible_text(state.show_reasoning))
    imgui.end_popup()


def _copy_run_item(cache: NodeTranscript, run: int) -> None:
    """
    The menu item that copies one run whole, named for its kind and length.
    """
    start, end = cache.run_bounds(run)
    # The range can be empty if the selection moved between latching and opening.
    if end <= start:
        return
    label = cache.lines[start].kind.value.replace("_", " ")
    count = end - start
    plural = "" if count == 1 else "s"
    if imgui.menu_item_simple(f"copy {label} ({count} line{plural})"):
        imgui.set_clipboard_text(cache.run_text(run))


def _draw_context_menu(state: TranscriptState, cache: NodeTranscript, visible: list[Line]) -> None:
    if not imgui.begin_popup_context_window(_CONTEXT_ID, imgui.PopupFlags_.mouse_button_right):
        return

    if state.context_run is not None:
        _copy_run_item(cache, state.context_run)

    if imgui.menu_item_simple("copy visible"):
        imgui.set_clipboard_text(copy_text(visible))
    imgui.end_popup()


def _handle_scroll(state: TranscriptState) -> None:
    """
    Follow the tail until the operator scrolls away from it.

    The behaviour, and the reason position cannot drive the disengage, live in
    ``widgets.follow_tail`` -- DETAIL's narration needs the same thing, and the two
    panes drifting apart on it would be invisible until one of them felt wrong.
    Here the flag is additionally re-enabled by the pane's own checkbox.
    """
    state.follow_tail = widgets.follow_tail(state.follow_tail)
