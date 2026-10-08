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
over the layout library). It is the one mode that wraps, colours by kind, selects
text and culls at once, and it is made of a few views a frame, not one per run:

- *closed* runs are the ``typeset.Part``s of a ``typeset.document``: OUTPUT and
  REASONING as markdown, every other kind literal, monospace and unparsed, in its
  ``_KIND_COLOUR`` role. One view is one selection across its runs and one culled
  draw. A document holds one ``Generation`` of runs, ``_GENERATION_LINES`` lines or
  so, and the oldest generation is what a long watch drops (``_TYPESET_KEEP``): it
  is a view, so the room it took is known when it goes and nothing on screen moves
  (``_trim``).
- the *open* run -- the last one, which can still grow -- is a ``typeset.stream`` when
  it is markdown, so text already committed is laid out once, read twenty times a
  second (``_STREAM_EVERY_MS``), and ``typeset.plain`` when it is literal
  (``_draw_run``).
- a run that has *just closed* keeps the view it had until its document has built
  its part, which for a long run is several frames (``_draw_generation``).

A kind change closes the open run, so a fence a model left open ends with its run
instead of styling the rest of the session. Nothing else closes it: an open stream
shows everything it was given, and a turn that ends on a run leaves it open for
the next one to go on. ``TypesetFeed`` is the part of this that needs no ImGui.
What the mode costs is on ``DEFAULT_MODE``, and where it is bounded on
``_TYPESET_WINDOW`` and ``_TYPESET_KEEP``.

The seam has no find-in-text, so a search demotes this mode to the line path as it
does RICH. REASONING is a dimmed markdown part, left out of the parts when the
checkbox is off. Follow-the-tail has one owner, ``widgets.follow_tail``: the stream is
made with the seam's ``follow`` off.
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

Parsing and laying out markdown is paid once per line and is not cheap, about
0.4 ms, and without a bound a long session is seconds of it. The seam spreads a
document and a stream over frames and this pane declines the frame's free
allowance for both (``_stream``, ``_draw_generation``), so the window does not set
the length of the first frame, only how many frames pass before the text is
whole. Measured on a session of 5,000 lines: the first frame is 37 ms at 200
lines, 56 to 61 at 400 and 38 at 800 -- it follows what the first piece under the
window's top happens to be -- and the rest arrives over 10, 16 and 31 frames of
15 to 18 ms at the median, none longer than the first. When the tail is a single
open markdown run the first frame is 39, 25 to 30 and 20 ms, and the stream then
reads a piece of forty lines at each reading (``_STREAM_EVERY_MS``), a frame of 20
to 36 ms in every three: five of them at 200 lines, ten at 400, twenty at 800.

400 is ten panes' worth of scrollback inside a third of a second, on a first frame
shorter than RICH's return to a node (67 ms at the median, 120 at the 95th
percentile). 800 is half a second of building with a full collection of 60 ms in
it, and four fifths of ``_TYPESET_KEEP``: a feed that was then watched would start
dropping what it had just built. The pane says how many lines it left out and
where to read them (RAW). The bound is on what one advance takes in, not on what
the feed holds: lines that arrive while the node is watched are added as they
come, up to ``_TYPESET_KEEP``.
"""

_TYPESET_KEEP = 1000
"""
How many lines of closed runs a ``TypesetFeed`` holds before it drops its oldest
``Generation``.

The window above bounds one advance, so a transcript watched while it streams
would otherwise hold every run it was ever shown. Measured over 20,000 lines
streamed into a watched pane with this bound: 5.3 ms at the median over the first
2,000 lines and 5.9 over the last, 32 and 33 ms at the slowest, and a full
collection by Python's collector, which lands in a frame, 109 ms at the median
and 134 at most. RICH gains the same 0.8 ms over that stream: it is the line
cache, which every mode has.

What the bound buys is the collector's pause, which follows what is alive. With
no bound, and with the stream read on every frame (not run since it is read
every ``_STREAM_EVERY_MS``), the median frame went from 7 ms to 9.4 over that
stream, the slowest frame a run closed in from 36 ms to 126, and a full
collection reached 646 ms; holding 1,000, 2,000 and 4,000 lines the pause was
121, 166 and 243 ms and the frame 6.8 ms at each, a generation out of sight
costing 0.015 ms. A thousand is two and a half windows of scrollback.

The lines dropped are counted in the row that says where to read them.
"""

_GENERATION_LINES = 250
"""
How many lines of closed runs go into one ``Generation`` before the next is started.

A generation is what the feed drops whole, so this is how far past
``_TYPESET_KEEP`` it gets and how much scrollback goes at once; it is also how many
parts a document walks when a run closes. Holding 1,000 lines of the bench's
report is three or four of them.
"""

_STREAM_EVERY_MS = 50.0
"""
How often the open run's stream reads what has arrived, in milliseconds.

Each reading lays the block still arriving out again, which is most of what a
token costs in this mode and nearly all of what a stream gives Python's collector
to do. Read on every frame that is paid sixty times a second; read every 50 ms it
is paid twenty, and what arrives between two readings is on screen at most three
frames after it arrived. Measured on the bench's stream of a token a frame: the
median frame is 5.9 ms where it was 7.9, the frame that reads still 9 to 10, and
the frames of a 20,000-line watch ran 10,000 collections where they ran 13,800.

The seam keeps frames coming while a stream holds text it has not read
(``typeset.pending``), so the last words of a turn do not wait for the mouse.
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
again, on each ``j``/``k`` between two agents being watched in turn. Kept, the return
costs the line rebuild (4 ms at 5,000 lines) and one culled draw.
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

The log is markdown by default, and of the two modes that read markdown RICH is
the one with no long frame in it; TYPESET is one radio button away. The rule is
that TYPESET is the default if it streams within 2 ms a frame of RICH at 1,000
and at 10,000 lines **and** leaves no recurring frame over 30 ms in a long watch.
It meets the first half and not the second. Measured here, in a pane of 900 by
700 on text that does not repeat (``scripts/bench_panes.py --typeset on --only
transcript``, twice, and 20,000 lines streamed sixteen characters a frame into a
watched pane, with the collector on and its pauses timed apart from the frames):

- **A token a frame.** TYPESET's median frame is 5.9 ms at 1,000 lines, 5.9 at
  10,000 and 6.7 at 100,000; RICH's is 4.2, 4.4 and 6.7. Idle they are 5.1 to 5.3
  against 4.2 to 5.8. Two frames in three read nothing (``_STREAM_EVERY_MS``) and
  are the median; the one that reads is the 95th percentile, 9.6 to 10.4 ms
  against RICH's 4.3 to 4.7, and 6.9 at 100,000 lines.
- **A run that closes** is parsed and laid out again as a part, twenty source
  lines a frame: 25 to 34 ms for each of the two or three frames a markdown run of
  thirty lines or more takes, about 4 ms of it the parse and the rest laying the
  piece out and drawing it. Over the watch 83 frames in 42,300 were over 30 ms
  and the longest was 34.5; RICH's longest was 7.9. ``_draw_generation`` keeps the
  text on screen meanwhile and cannot make it cheaper.
- **The collector** is what fails the rule. TYPESET's frames ran 9,980
  collections and RICH's 138, and a full one stops the frame it lands in for as
  long as there is heap to walk: 69 of them for TYPESET, one in every 600 frames,
  109 ms at the median and 134 at most with ``_TYPESET_KEEP`` lines alive; one for
  RICH, 48 ms. Over 6,000 lines with everything alive at start-up put out of the
  collector's reach (``gc.freeze``) a full collection was 59 ms at the median,
  and 35 holding 400 lines: still over, and as often.
"""

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

    ``fresh`` is the text not yet handed to the run's stream; ``pieces`` is all of it.
    ``lines`` counts the transcript lines fed whole.
    """

    run: int
    kind: SegmentKind
    pieces: list[str] = field(default_factory=list)
    fresh: list[str] = field(default_factory=list)
    lines: int = 0

    def add(self, text: str) -> None:
        self.pieces.append(text)
        self.fresh.append(text)

    @property
    def text(self) -> str:
        if len(self.pieces) > 1:
            self.pieces = ["".join(self.pieces)]
        return self.pieces[0] if self.pieces else ""

    def take(self) -> str:
        """
        The text that arrived since the last ``take``.
        """
        taken = "".join(self.fresh)
        self.fresh.clear()
        return taken


@dataclass
class Generation:
    """
    Consecutive closed runs set as one ``typeset.document``, under one key.

    ``runs[:settled]`` are the runs the document has shown whole. The ones after
    them are being *handed over*: the document has been given their parts and has
    not finished building them, and meanwhile each is still drawn by the view it
    had while it was the open run (``_draw_generation``).

    The measurements are of the last frame the generation was drawn: ``height`` and
    ``room`` are what its document claimed and how far it moved the cursor when it
    showed the settled runs and nothing else, ``extent`` is how far the whole
    generation moved it, and ``in_sight`` whether any of that was on screen.
    """

    serial: int
    runs: list[ClosedRun] = field(default_factory=list)
    lines: int = 0
    settled: int = 0
    height: float = 0.0
    room: float = 0.0
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
    as one (``TranscriptState.prune``): a stream that outlived its cursor would be
    fed the same text again.

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
    # TYPESET's context-menu latch: the view under the mouse, by its key in the
    # node's ``TypesetFeed.views``, and the block of it. Latched as context_run is.
    typeset_target: tuple[Hashable, int] | None = None

    def shown_mode(self) -> RenderMode:
        """
        The mode drawn this frame: the chosen one, or the default until one is
        (``DEFAULT_MODE``, and WRAP with the seam off).

        TYPESET needs the seam, and without it the pane is in WRAP. Derived each
        frame and never written back, so switching the seam off and on again
        returns the operator to what they had chosen.
        """
        seam = typeset.enabled(typeset.Pane.CONTEXT)
        if self.mode is None:
            return DEFAULT_MODE if seam else RenderMode.WRAP
        if self.mode is RenderMode.TYPESET and not seam:
            return RenderMode.WRAP
        return self.mode

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


def draw(snap: Snapshot, state: TranscriptState, selected: NodeId | None) -> None:
    if selected is None or (record := snap.get(selected)) is None:
        imgui.text_disabled("select an agent to read its transcript")
        return

    cache = state.cache_for(selected)
    # Pin the length once. Everything below renders a consistent prefix even while
    # the asyncio thread keeps appending (I7).
    pinned = record.transcript.published_length
    cache.sync(record.transcript, pinned)

    mode = state.shown_mode()

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
    The key of the view a run is drawn by while it is open, and while it is handed over.
    """
    return ("open", run)


def _tail_key(run: int) -> tuple[str, int]:
    """
    The key of the view that draws an open literal run's unfinished last line.
    """
    return ("tail", run)


def _stream(views: typeset.ViewCache, run: int) -> typeset.Stream:
    """
    The stream a markdown run is read into, made on first asking.

    Every call site asks through here: the seam reads a stream's arguments when
    it makes one, so whichever asked first would otherwise decide them.
    ``free_allowance=False`` is for a stream opened on a run that is already long,
    which is then read a piece a frame.
    """
    return typeset.stream(views, _run_key(run), every_ms=_STREAM_EVERY_MS, free_allowance=False)


def _draw_run(
    views: typeset.ViewCache,
    run: int,
    kind: SegmentKind,
    text: str,
    live: list[Hashable],
    *,
    closed: bool = False,
) -> None:
    """
    One run in a view of its own: a stream for markdown, literal text for the rest.
    The keys it drew under, and the ones it wants kept, are added to ``live``.

    An open literal run is two views, its whole lines and the line still arriving.
    ``typeset.plain`` builds the last sixty lines or so of a text again whenever the
    text changes (measured here: 6 ms), and a line that arrives a token at a time
    would pay that every frame; the second view is one line, so those frames cost
    what a frame with nothing new does, and the 6 ms is paid once a line.
    """
    ink = getattr(P, _KIND_COLOUR[kind])
    key = _run_key(run)
    if kind in _MARKDOWN_KINDS:
        _stream(views, run).draw(ink=None if kind is SegmentKind.OUTPUT else ink)
        live.append(key)
        return
    cut = len(text) if closed else text.rfind("\n") + 1
    if whole := _literal(text[:cut]):
        typeset.plain(views, key, whole, colour=ink)
        live.append(key)
    if closed:
        return
    if text[cut:]:
        if whole:
            # Straight under the lines above, where it will be once it is one of them.
            imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() - imgui.get_style().item_spacing.y)
        typeset.plain(views, _tail_key(run), text[cut:], colour=ink)
    # Kept between the lines it draws: a view is dearer to make than to change.
    live.append(_tail_key(run))


def _draw_typeset(state: TranscriptState, feed: TypesetFeed, cache: NodeTranscript) -> None:
    # The scrollbar is always there so that the measure does not change, and every
    # paragraph re-break, on the frame the text first outgrows the pane. Literal
    # runs and fenced code are never broken, hence the horizontal one.
    flags = (
        imgui.WindowFlags_.always_vertical_scrollbar.value
        | imgui.WindowFlags_.horizontal_scrollbar.value
    )
    if not imgui.begin_child("##transcript", imgui.ImVec2(0, 0), 0, flags):
        imgui.end_child()
        return

    show = state.show_reasoning
    run = feed.open
    # Every view drawn this frame, in the order drawn.
    live: list[Hashable] = []
    if run is not None and run.kind in _MARKDOWN_KINDS:
        # Fed whether or not it is drawn, so showing reasoning again does not
        # lay out everything that arrived while it was hidden in one frame.
        _stream(feed.views, run.run).append(run.take())

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
    for generation in tuple(feed.generations):
        _draw_generation(feed, generation, show, live)
    if shown is not None:
        _draw_run(feed.views, shown.run, shown.kind, shown.text, live)
    # An open run the filter hides keeps its stream: it is still being fed.
    feed.views.prune(live if run is None else [*live, _run_key(run.run)])

    if not imgui.is_popup_open(_CONTEXT_ID):
        hovered = imgui.is_window_hovered()
        state.typeset_target = _typeset_pointed(feed, live) if hovered else None
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
    One generation: its document, and under it the runs still being handed over.

    **A run that closes is not swapped for its part in one frame.** The document is
    given the part at once and builds it as the seam's budget allows, which for a
    long run is several frames, and it shows what it has built so far. Until it has
    built all of it the run stays on screen in the view it already had -- its
    stream, closed -- and the document is drawn clipped to the height it had before,
    with the cursor put back where that ended. The frame after the document is
    whole the run's own view is dropped and the clip with it.

    **The document is asked to build wherever it is, and a small piece a frame**
    (``build_hidden``, ``free_allowance=False``). By the seam's rule a document that
    is whole and out of sight builds nothing, so one that scrolled away would
    never take its new runs and their own views would be kept for ever. And the
    frame's free allowance is the whole of an ordinary run in the frame it closes,
    30 to 45 ms, which nothing here needs: the run's own view is what is read
    until ``ViewCache.unbuilt`` says the part is whole.
    """
    start = imgui.get_cursor_pos_y()
    wide = max(1.0, imgui.get_content_region_avail().x)
    generation.in_sight = generation.extent > 0.0 and imgui.is_rect_visible(
        imgui.ImVec2(wide, generation.extent)
    )
    parts = generation.parts(show)
    handing = generation.runs[generation.settled :]
    bridged = [
        run
        for run in handing
        if (_run_key(run.run) in feed.views or _tail_key(run.run) in feed.views)
        and (show or run.kind is not SegmentKind.REASONING)
    ]
    whole = True
    # What the document claims this frame, for all the runs it has been given.
    height = room = 0.0
    if parts:
        live.append(generation.key)
        if bridged:
            at = imgui.get_cursor_screen_pos()
            edge = imgui.get_window_pos().x + imgui.get_window_size().x
            imgui.push_clip_rect(at, imgui.ImVec2(edge, at.y + generation.height), True)
        height = typeset.document(
            feed.views, generation.key, parts, build_hidden=True, free_allowance=False
        )
        room = imgui.get_cursor_pos_y() - start
        whole = not feed.views.unbuilt(generation.key)
        if bridged:
            imgui.pop_clip_rect()
            imgui.set_cursor_pos_y(start + generation.room)
    if whole or not bridged:
        # From the next frame the document is drawn for what it holds now.
        generation.height, generation.room = height, room
    for run in bridged:
        if run.kind in _MARKDOWN_KINDS:
            # Whatever arrived in the frame the run closed, and the end of the
            # text: the last block is settled and an open fence closed.
            _stream(feed.views, run.run).set_text(run.part.text, closed=True)
        _draw_run(feed.views, run.run, run.kind, run.part.text, live, closed=True)
    generation.extent = imgui.get_cursor_pos_y() - start

    if whole:
        generation.settled = len(generation.runs)


def _typeset_pointed(feed: TypesetFeed, drawn: Sequence[Hashable]) -> tuple[Hashable, int] | None:
    """
    The view and block under the mouse, or ``None`` between blocks.

    The last view drawn is asked first: a document being handed a run is laid out
    under that run's own view, where it is clipped away.
    """
    for key in reversed(drawn):
        view = feed.views.view(key)
        block = None if view is None else view.under_mouse().block
        if block is not None:
            return (key, block)
    return None


def _typeset_run(feed: TypesetFeed, target: tuple[Hashable, int], show: bool) -> int | None:
    """
    The run a block of a view belongs to, as its index in ``NodeTranscript.run_starts``.
    """
    key, block = target
    generation = feed.generation(key)
    if generation is None:
        # A run's own view, which is keyed by the run.
        return key[1] if isinstance(key, tuple) and isinstance(key[1], int) else None
    part = feed.views.part_at(key, block)
    runs = generation.shown(show)
    return runs[part].run if part is not None and part < len(runs) else None


def _draw_typeset_menu(state: TranscriptState, feed: TypesetFeed, cache: NodeTranscript) -> None:
    """
    Copy the selection, the block or the run that was right-clicked, or everything shown.

    A block is a paragraph, a fence or a literal run. The run is copied as the line
    modes copy it: whole and unfiltered, from the lines, so a run the window cut
    short at the top is copied from its first line.
    """
    if not imgui.begin_popup_context_window(_CONTEXT_ID, imgui.PopupFlags_.mouse_button_right):
        return

    for key in feed.views.keys():
        view = feed.views.view(key)
        if view.owns_selection and (selected := view.selected_text()):
            if imgui.menu_item_simple("copy selection"):
                imgui.set_clipboard_text(selected)
    if state.typeset_target is not None:
        key, block = state.typeset_target
        view = feed.views.view(key)
        # The view can be gone, or shorter, if a run closed while the menu was open.
        if view is not None and block < len(view.document.blocks):
            if imgui.menu_item_simple("copy block"):
                imgui.set_clipboard_text(view.block_text(block))
            run = _typeset_run(feed, state.typeset_target, state.show_reasoning)
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
