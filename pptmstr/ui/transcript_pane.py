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
drawn, and the lines above them are reached through the earlier-lines view: RAW,
the one renderer that draws every line. Uniform rows and a clipper keep its
*drawing* flat, and the per-frame filter and join above are what a long one costs
there: 0.14 ms a frame idle at 1k lines, 0.9 ms at 10k, 14 ms at 100k.

**Liveness.** Root sessions stream token by token. Sub-agents do not -- their output
arrives in complete messages only (§2.5.1). The pane says which it is looking at
rather than letting the operator infer that a quiet sub-agent is a stuck one.

**Copying.** ImGui text is not selectable, so a run of output is copied whole rather
than dragged over. That is a better unit than a selection anyway: no partial first
line, no dropped trailing newline, and it round-trips into an issue.

**Rendering.** There is no choice of renderer: the log is TYPESET while the seam
can draw it and WRAP while it cannot (``TranscriptState.shown_mode``). RAW is the
earlier-lines view, shown while the operator reads what the other two left out,
and left again by the pane's ``latest`` button.

**Prompts.** A SYSTEM run is a prompt: ``AgentSession.send`` writes every one, the
opening text included, as ``> `` and what was sent, and nothing else writes the
kind. It is what an agent was given, so every mode shows every character of it
and reads no markup out of it -- a ``#``, a ``- ``, a backtick is what was typed --
broken at the pane's width in the prose face, in the dim ink and behind the ``>``
that marks it as the operator's: WRAP's wrapped lines, and in TYPESET ImGui's wrapped
text in the library's prose face (``typeset.prompt_parts`` says why it is not the
library's), outside any selection of the library's. **A NOTICE run is not a
prompt**: pptmstr writes it (``AgentSession.wake``) to tell the session that
something it waited on has arrived, and it is drawn as the literal it is, in its
own ink and with no ``>``, so it never reads as the operator's.

**Typesetting.** ``RenderMode.TYPESET`` sets the log through ``typeset`` (the seam
over the layout library). OUTPUT and REASONING runs are read as markdown and set
by the library, wrapped, selectable and culled; a prompt is set as above; every
other kind -- a tool call, its result, an error -- is literal text whose columns are content,
set unbroken in its ``_KIND_COLOUR`` and never read as markdown. **The library
sets a literal run too, as a code block of its own, where it is measured to keep
its columns** (``typeset.views.literal_unfit``: every character of the run
drawn as it is, a column that is ImGui's advance, ``gate.literal_columns``), and
it is then selectable; a run holding a control character such as a terminal's
colour escape, and every run where the measurement says no, is ImGui's, in the
chrome's font, as it was. A fence inside a markdown run goes with its run.

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
  literal (``_draw_run``), which the library sets as a closed literal run is set.
- a run that *closes* is a part of its document in the frame it closes. The
  seam reads and lays out what it is handed in the call that draws it, so there
  is no frame in which the run is in neither place or in both.

A kind change closes the open run, so a fence a model left open ends with its run
instead of styling the rest of the session. An open stream shows everything it
was given; once the node's turn is over its stream is closed too, so a message
that ends on a fence's closing marks with no line end has that fence closed and
coloured (``_draw_run``). The run itself stays open, and a stream closed early
is read again, open, if more arrives. ``TypesetFeed`` is the part of this that needs no ImGui.
What the mode costs is on ``DEFAULT_MODE``, and where it is bounded on
``_TYPESET_WINDOW`` and ``_TYPESET_KEEP``.

What the split costs: a selection is inside one of the library's views, so it
ends with its run; a literal run ImGui draws is copied whole from the context
menu and not by dragging. A tool block's rows in the code face are views of
the library's of their own (``_Blocks.select``, ``tool_block.draw``), held in
``TranscriptState.block_views`` while the block's layout is, so a selection in
a block ends at the run of rows it is in, and the menu's "copy selection" copies
it as it copies a run's. A table changes its columns' widths in the
frame its run closes (request P19).

**Find.** ``Ctrl+F`` over the pane opens a find row (``FindBar``). In typeset text
it searches the library's views (``typeset.find_matches``), marks every
match with ``TypesetView.set_marks`` and brings the current one into view with
``scroll_to``; text ImGui draws in this mode -- a prompt, a literal run the library
does not set -- is not searched, and nor are the lines above the typeset window.
On the line paths it searches the lines drawn, every line in RAW and the last
``_WRAP_WINDOW`` in WRAP, and scrolls to the line. REASONING is a dimmed markdown
part, left out of the parts when ``Settings.show_reasoning`` is off.

**Following.** On while the view is at its foot, off from an upward wheel or a
drag of the scrollbar, on again at the foot (``_handle_scroll``); a match brought
into view turns it off. ``widgets.follow_tail`` owns it: the stream is made with the
seam's ``follow`` off.

**When the library stops.** A view can stop inside its draw. That frame says why
where the text would be (the seam's sentence); from the next the seam is off and
the pane is in WRAP until it is back. The feeds are dropped with it (``draw``).
"""

from __future__ import annotations

import dataclasses
import enum
import re
import time
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from typing import Any

from imgui_bundle import imgui

from ..model import AgentState, NodeId, Snapshot
from ..theme import P, hint_colour
from ..transcript import SegmentKind, ToolEntry, Transcript
from . import tool_block, typeset, widgets
from .typeset.views import Hosted

# How many lines to draw when wrapping is on and the clipper cannot be used.
# Without a bound, turning wrap on over a large transcript stalls the frame.
_WRAP_WINDOW = 400

_TYPESET_WINDOW = 400
"""
How many lines one ``TypesetFeed.advance`` will typeset; older ones are skipped.

A text is read and laid out in the frame that first draws it, once, and that is
the frame this bounds: the first on a node, and a sub-agent's whole message. The
first on a node is met on every j/k to a session not shown in the last
``_FEED_KEEP_S`` -- CONTEXT follows the cursor -- so it is a stall felt often,
and the rule it is set by is the stall the operator has used the pane with: the
one at 400, which this was before 2026-10-15.

Measured on 2026-10-15 by ``scripts/bench_typeset_window.py`` (its 10,000-line
log, headless, a pane of 900 by 700, load average about 0.7, the library warmed
first; the median first frame of three or five passes): 22 ms at 100 lines, 37 at
250, 53 at 400, 74 at 600, 97 to 114 at 800, 116 to 117 at 1,000, 190 at 1,500,
199 at 2,000, 322 at 3,000, and 1,149 ms with all 10,000 taken in. Up to 1,000
that is about 12 ms that do not depend on the window and about 10.5 ms for every
100 lines that do, so the window is most of the first frame: 400 lines is about
two and a half frames at sixty a second over the fixed part, and every 100 more
is about another two thirds of a frame.

The pane says how many lines it left out on a button that shows them, in the
earlier-lines view (RAW), which draws every line, until the output viewer takes
that over. The bound is on what one advance takes in, not on what the feed
holds: lines that arrive while the node is watched are added as they come, up to
``_TYPESET_KEEP``.
"""

_TYPESET_KEEP = 1500
"""
How many lines of closed runs a ``TypesetFeed`` holds before it drops its oldest
``Generation``.

The window above bounds one advance, so a transcript watched while it streams
would otherwise hold every run it was ever shown, each markdown run a view of
the library's. Holding is cheap while the width stands still, because a
generation out of the pane is not drawn: the idle frame is 0.81 ms holding 400
lines, 1.12 at 1,000, 1.17 at 1,500, 1.24 at 2,000 and 3.38 at 10,000 (same
runs as above; Python's own allocations 3.2, 5.2, 7.3, 8.5 and 37 MB, the
library's not counted).

**What sets it is a frame at another width**, when every generation held is laid
out again whether or not it is in sight: a frame of a splitter drag is 8 ms
holding 400 lines, 20 at 1,000, 30 at 1,500, 40 at 2,000, 60 at 3,000 and 198 at
10,000. 1,500 keeps that frame within two of sixty a second;
the ten thousand lines of planning/2026-10-15-ui-rework.md U3 would make a drag
five frames a second. Lines past the bound are in the earlier-lines view, which
draws every line for 0.9 ms a frame at 10,000 (the module docstring).

The lines dropped are counted on the earlier-lines button, and shown by it.
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
measured (``scripts/bench_panes.py --only prune``).
"""

_CACHE_KEEP_S = 2.0
"""
How long a ``NodeTranscript`` outlives the last frame its node was shown, in seconds.
"""


class RenderMode(enum.Enum):
    """
    How the log is drawn. TYPESET and WRAP are chosen by ``shown_mode`` from the
    seam; RAW is only the earlier-lines view.
    """

    RAW = "raw"
    WRAP = "wrap"
    TYPESET = "typeset"


@dataclass(frozen=True, slots=True)
class Line:
    kind: SegmentKind
    text: str
    # Which run this line belongs to -- see NodeTranscript.run_starts.
    run: int


DEFAULT_MODE = RenderMode.TYPESET
"""
What the pane shows while the seam is on; with it off, WRAP, the pane as it was
before there was a seam. The only renderer offered: the operator chose it
(planning/2026-10-15-ui-rework.md, U2).

The rule recorded for making TYPESET the default was about cost: it streams
within 2 ms a frame of the markdown renderer it was measured against at 1,000
and at 10,000 lines **and** leaves no recurring frame over 30 ms in a long
watch. By measurement it meets both (one full collection in a watch of 52,313
frames, twice).

Measured here on 2026-10-12 (``scripts/bench_panes.py --typeset on --only
transcript watch``; headless, nothing rasterised, CPU ms inside this pane's
draw, a pane of 900 by 700, text that does not repeat, load average 3 to 10):

- **Nothing new.** TYPESET's median frame is 0.23 ms at 1,080 lines and 0.27 at
  10,080; WRAP's 1.0 and 2.2.
- **A token a frame.** TYPESET 1.2 and 1.4 ms at the median and 2.0 and 1.6 at
  the 95th percentile; WRAP 1.1 and 2.7.
- **The first frame** on a session is 23 to 71 ms for TYPESET, which takes in
  the last ``_TYPESET_WINDOW`` lines whatever the length; WRAP's is 2.5 ms at
  1,080 lines and 12 at 10,080.
- **A session watched from its first line**, 20,040 lines sixteen characters a
  frame, 52,313 frames, the collector on and its pauses timed apart, twice:
  TYPESET's median frame is 1.1 to 1.3 ms and its 95th percentile 1.8 to 2.2,
  with one full collection in each watch, 40 and 56 ms. WRAP: 1.9 to 2.6
  and 4.7 to 5.4 ms, no full collection. Frames over 16.6 ms were 1 and 16
  for TYPESET, 0 and 40 for WRAP: the 40 are in a watch
  with no collection at all, on a machine running other work, so that count
  says more about the machine than the mode. In three watches of 5,040 lines
  TYPESET had no full collection, and its longest frame was 5.6, 9.1 and
  14.0 ms.
"""


_MARKDOWN_KINDS = frozenset({SegmentKind.OUTPUT, SegmentKind.REASONING})

_TOOL_KINDS = frozenset({SegmentKind.TOOL_CALL, SegmentKind.TOOL_RESULT, SegmentKind.ERROR})
"""
The kinds a tool's call and result are written as: a run of them that holds an
entry's line is drawn as blocks (``_Blocks``).
"""

_IN_TURN = frozenset(
    {
        AgentState.SPAWNING,
        AgentState.THINKING,
        AgentState.CALLING_TOOL,
        AgentState.AWAITING_APPROVAL,
        AgentState.RUNNING_TOOL,
    }
)
"""
The states in which a node's turn is going on, so a call of that turn with no
result yet can still be running. In any other a call with no result has none
coming: its turn ended under it.
"""

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
    the transcript's lines it holds. ``text`` is the run as it is set, and what
    is copied of it; ``parts`` is how: one part, or none for a prompt with
    nothing in it (``typeset.prompt_parts``).
    """

    run: int
    kind: SegmentKind
    text: str
    parts: tuple[typeset.Part, ...]
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
    _prompt: tuple[str, tuple[typeset.Part, ...]] = ("", ())
    _hosted: tuple[str, tuple[typeset.Part, ...]] = ("", ())

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

    @property
    def prompt(self) -> tuple[typeset.Part, ...]:
        """
        The text as a prompt is set (``_prompt``); the same tuple until more
        arrives, which is what lets the seam compare nothing.
        """
        text = self.text
        if self._prompt[0] is not text:
            self._prompt = (text, _prompt(text))
        return self._prompt[1]

    @property
    def hosted(self) -> tuple[typeset.Part, ...]:
        """
        The run as the blocks a host draws (``_hosted``); the same tuple until more
        arrives, as ``prompt`` is.
        """
        literal = self.literal
        if self._hosted[0] is not literal:
            self._hosted = (literal, _hosted(literal, self.kind))
        return self._hosted[1]


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
    # Where it started in the window's content, for a find that has to bring a
    # generation the window is not drawing into view before its view can.
    top: float = 0.0
    _revision: int = 0
    _shown: tuple[ClosedRun, ...] = ()
    _parts: tuple[typeset.Part, ...] = ()
    _owners: tuple[ClosedRun, ...] = ()
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
        The runs the reasoning filter lets through.
        """
        self._filter(show_reasoning)
        return self._shown

    def owners(self, show_reasoning: bool) -> tuple[ClosedRun, ...]:
        """
        The run each of ``parts`` is of, one for each.
        """
        self._filter(show_reasoning)
        return self._owners

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
            self._parts = tuple(part for run in self._shown for part in run.parts)
            self._owners = tuple(run for run in self._shown for _ in run.parts)
            self._parts_for = want


def _closed(run: OpenRun, hosted: bool = False) -> ClosedRun:
    """
    ``run`` as it is held once it can no longer grow. ``hosted`` is whether its
    lines hold a tool entry's, so that it is drawn as blocks.
    """
    kind = run.kind
    if kind is SegmentKind.SYSTEM:
        text = run.literal
        return ClosedRun(run.run, kind, text, run.prompt, run.lines)
    if hosted and kind in _TOOL_KINDS:
        return ClosedRun(run.run, kind, run.literal, run.hosted, run.lines)
    if kind is SegmentKind.OUTPUT:
        # No colour: markdown keeps the palette's role inks for headings, code and links.
        part = typeset.Part(run.text)
    elif kind in _MARKDOWN_KINDS:
        part = typeset.Part(run.text, colour=_KIND_COLOUR[kind])
    else:
        part = typeset.Part(run.literal, typeset.Kind.LITERAL, colour=_KIND_COLOUR[kind])
    return ClosedRun(run.run, kind, part.text, (part,), run.lines)


def _hosted(literal: str, kind: SegmentKind) -> tuple[typeset.Part, ...]:
    """
    A run of tool lines as the one part a host draws: its blocks
    (``_Blocks.draw_run``), and where it draws none of them its lines, set as the
    literal part it also is.
    """
    return (Hosted(literal, typeset.Kind.LITERAL, colour=_KIND_COLOUR[kind]),)


def _prompt(text: str) -> tuple[typeset.Part, ...]:
    """
    A prompt as the log sets it: every character of it, the mark the log wrote
    included, in the prose face and its kind's ink (``typeset.prompt_parts``).
    """
    return typeset.prompt_parts(_literal(text), colour=_KIND_COLOUR[SegmentKind.SYSTEM])


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
                self._close(source)
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

    def _close(self, source: NodeTranscript) -> None:
        """
        Put the open run at the end of the newest generation, or start one for it.
        """
        run = self.open
        if run is None:
            return
        self.open = None
        closed = _closed(run, run.run in source.hosted)
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
            run.text for generation in self.generations for run in generation.shown(show_reasoning)
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
    # The byte span of each run in the transcript, [start, end): what a run's tool
    # entries are looked up by (``Transcript.tool_entries``). A run starts at a
    # segment's start, since only a segment changes the kind.
    run_spans: list[list[int]] = field(default_factory=list)
    # The runs of tool kinds whose bytes hold an entry's line: drawn as blocks.
    hosted: set[int] = field(default_factory=set)
    # Whether the last line can still be extended -- true when the bytes so far did
    # not end on a newline. Tracking this explicitly, rather than inferring it from
    # the next chunk, is what keeps a streamed token appending to the line it
    # belongs to instead of starting a new one.
    open_line: bool = False

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
                        self.run_spans.append([start, segment.end])
                    self.lines.append(Line(segment.kind, piece, len(self.run_starts) - 1))
            # Every piece of a segment is in the last run: a run changes only with
            # the kind, which only a segment can change.
            self.run_spans[-1][1] = segment.end
            if segment.kind in _TOOL_KINDS and transcript.tool_entries(start, segment.end):
                self.hosted.add(len(self.run_starts) - 1)

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


def line_matches(lines: Sequence[Line], needle: str) -> list[int]:
    """
    The index in ``lines`` of every line that holds ``needle``, ignoring case, once
    for each time it holds it, in order. An empty needle finds nothing.
    """
    if not needle:
        return []
    wanted = needle.casefold()
    found: list[int] = []
    for index, line in enumerate(lines):
        found += [index] * line.text.casefold().count(wanted)
    return found


@dataclass
class FindBar:
    """
    The find row over the pane: what is looked for, which match is current, and
    what the next draw owes it. ``count`` is how many matches the last draw found.

    The matches themselves are not kept: they are found again by every draw from
    what it draws (``_typeset_matches``, ``line_matches``), so a run that grows or a
    generation that is dropped can never leave one pointing at text that moved.
    """

    open: bool = False
    needle: str = ""
    current: int = 0
    count: int = 0
    # Bring the current match into view in the next draw: a new needle, Enter.
    reveal: bool = False
    # Give the field the keyboard in the next draw.
    focus: bool = False
    # Whether the field had the keyboard when the last frame ended.
    active: bool = False
    # Which views were handed marks, and the marks handed, by ``id``.
    marked: dict[int, tuple[Any, tuple[Any, ...]]] = field(default_factory=dict)
    # The ImGui frame whose Escape closed the row, or -1, and whether that press
    # has been seen held since (``spent_escape``).
    escaped_in: int = -1
    escape_held: bool = False

    def show(self) -> None:
        self.open = True
        self.focus = True
        self.reveal = True

    def close(self) -> None:
        """
        Close the row. The needle is kept for the next ``Ctrl+F``; the marks go with
        the next draw.
        """
        self.open = False
        self.focus = self.reveal = self.active = False

    def escape(self, frame: int) -> None:
        """
        Close the row on the Escape of ImGui frame ``frame``, which is then the row's.
        """
        self.close()
        self.escaped_in = frame
        self.escape_held = True

    def spent_escape(self, frame: int, down: bool) -> bool:
        """
        Whether Escape on ImGui frame ``frame`` is the row's: the press that closed
        the row, on its own frame or on any repeat of it while it is held. ``down``
        is whether Escape is down on ``frame``. The application's key readers ask
        this (``app._handle_keys``), once a frame and on every frame, because asking
        is also what lets the press go: the first frame the key is seen up ends it,
        and the next press is nobody's but its reader's.

        Not ``open``: the application reads its keys between frames, after the pane
        has drawn, so the row is already closed when the key is read. The frame is
        tested as well as the hold so that a press and its release that reach one
        frame are still the row's.
        """
        if not down:
            self.escape_held = False
        return frame == self.escaped_in or self.escape_held

    def step(self, by: int) -> None:
        """
        The match ``by`` places on, wrapping at both ends, brought into view.
        """
        if self.count:
            self.current = (self.current + by) % self.count
            self.reveal = True

    def found(self, count: int) -> None:
        """
        What the draw found: the current match is kept where there are still that
        many, and the last one where there are fewer.
        """
        self.count = count
        self.current = min(self.current, max(count - 1, 0))

    def status(self) -> str:
        if not self.needle:
            return ""
        return f"{self.current + 1} of {self.count}" if self.count else "no matches"


@dataclass
class TranscriptState:
    """Presentation state for the pane. Never enters the store (design §6)."""

    caches: dict[NodeId, NodeTranscript] = field(default_factory=dict)
    # What RenderMode.TYPESET draws for a node, made by the first draw in that mode
    # and kept longer than the node's line cache; see _FEED_KEEP_S.
    feeds: dict[NodeId, TypesetFeed] = field(default_factory=dict)
    # ``Settings.show_reasoning`` as the last ``draw`` was told it.
    show_reasoning: bool = True
    # RAW while the earlier-lines view is up, and None otherwise; nothing in the
    # pane sets another (a script may). What is drawn is ``shown_mode()``.
    mode: RenderMode | None = None
    # The line of the visible ones the earlier-lines view opens on, consumed by
    # the draw that brings it into view.
    raw_from: int | None = None
    follow_tail: bool = True
    # Following held off until the operator scrolls: the pane moved the view for
    # them (a match brought into view, the earlier-lines view opened), and the
    # foot it may have moved them to is not them asking to follow.
    held: bool = False
    find: FindBar = field(default_factory=FindBar)
    # The node the last ``draw`` was handed, once one has been; see ``switched_to``.
    node: NodeId | None = None
    drawn: bool = False
    # Each log's vertical bar held on, by the id of its child: the context log and
    # the session pane are two children with one state. The value is what the log
    # was drawn from when the bar was taken (``_end_log``).
    bars: dict[int, Hashable] = field(default_factory=dict)
    # Whether each log's text was taller than the log last frame, by its child's id.
    overflowed: dict[int, bool] = field(default_factory=dict)
    # What this frame's log is drawn from, as ``draw`` read it: a held bar is let
    # go only once this, or the log's size, has moved (``holds_bar``).
    log_key: Hashable = None
    frame: int = 0
    # When the frame being drawn began, on the clock ``prune`` was given.
    now: float = 0.0
    # The run the context menu acts on. Latched while the menu is closed, because
    # opening it moves the hover onto the popup and would otherwise clear the target
    # out from under the item the operator just right-clicked.
    context_run: int | None = None
    # TYPESET's context-menu latch: the run under the mouse, where there is one,
    # and the text of the block of it. Latched as context_run is, and as text
    # rather than as a place in a view: a run can close while the menu is open,
    # and what is copied is what was clicked.
    typeset_target: tuple[int | None, str] | None = None
    # The line paths' "copy block": the text of the tool block right-clicked, or
    # "" where the menu was opened over no block. Latched as context_run is.
    context_block: str = ""
    # The tool blocks the operator opened the viewer of, by node and by where the
    # block is in its transcript (``_Blocks``).
    expanded: set[tuple[NodeId, int]] = field(default_factory=set)
    # What the blocks drawn lately were built into, kept while they are drawn: a
    # block is laid out once and not every frame (``_Blocks``). ``block_frames`` is
    # the ImGui frame each was last drawn in: the log and CONTEXT draw with this
    # one state, and each keeps what the other drew in the same frame.
    layouts: dict[Hashable, tool_block.Layout] = field(default_factory=dict)
    viewers: dict[Hashable, tuple[tool_block.Row, ...]] = field(default_factory=dict)
    block_frames: dict[Hashable, int] = field(default_factory=dict)
    # When each layout was last drawn, on the clock ``prune`` keeps (``_Blocks.prune``).
    block_seen: dict[Hashable, float] = field(default_factory=dict)
    # Each block's find matches, by its key, with the needle they are of.
    block_hits: dict[Hashable, tuple[str, list[tool_block.Hit]]] = field(default_factory=dict)
    # The block the find is bringing into view, by node and anchor, until it is drawn.
    block_reveal: tuple[NodeId, int] | None = None
    # The library's views of the blocks' rows in TYPESET (``_Blocks.select``), by
    # a block's key and the part of it; kept while the block's layout is.
    block_views: typeset.ViewCache = field(default_factory=typeset.ViewCache)

    def shown_mode(self) -> RenderMode:
        """
        The mode drawn this frame: ``mode`` while one is set (the earlier-lines
        view), and otherwise ``DEFAULT_MODE``, which is WRAP with the seam off.

        TYPESET needs the seam, and without it the pane is in WRAP. Derived each
        frame and never written back, so switching the seam off and on again
        returns the pane to TYPESET.
        """
        seam = typeset.enabled(typeset.Pane.CONTEXT)
        wanted = DEFAULT_MODE if self.mode is None else self.mode
        if wanted is RenderMode.TYPESET and not seam:
            return RenderMode.WRAP
        return wanted

    def switched_to(self, node: NodeId | None) -> None:
        """
        The pane is handed another node: it opens at that node's latest text,
        following, in the log as it is set.

        What the operator did to the last node's view -- the earlier-lines view, a
        scroll off the foot, a match brought into view -- was about that node, and
        none of it is kept for a return to it: the log is one child for every
        node, so ImGui keeps one scroll position for all of them, and an
        earlier-lines view or a hold given back at another node's offset would
        open on nothing the operator had chosen. The find row stays open with its
        needle, which is about what is looked for and not where; its place starts
        again at the first match of this node's, and is not brought into view
        until the operator steps to it.
        """
        self.node = node
        if self.mode is RenderMode.RAW:
            self.mode = None
        self.raw_from = None
        self.follow_tail = True
        self.held = False
        self.find.current = 0
        self.find.reveal = False

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


@dataclass(frozen=True, slots=True)
class BlockHit:
    """
    A find's match in a run of tool lines: the run, where in it (a block's anchor,
    or the start of bytes drawn as text), the viewer row it is in (``None`` for
    text), and whether the collapsed block draws it.
    """

    run: int
    anchor: int
    row: int | None
    shown: bool


@dataclass
class _Blocks:
    """
    The tool blocks of one node's log in one frame: what a run of tool lines is
    drawn as, on both line paths that are not RAW.

    **Where a block goes.** A call's block is drawn where its call line is, and
    holds its result as well; the result's line, in the run after, then draws
    nothing of it. A result whose call is not on screen -- above what the pane
    took in (``floor``), or never in this transcript (a resumed session, a call
    written to another node's buffer) -- is drawn where its own line is. Bytes of
    a tool run that are no entry's line (an error that is not a tool's, a line
    a writer appended by hand) are drawn as they were before there were blocks,
    in their kind's ink. So every line of a tool run is in exactly one place.

    In TYPESET a run of tool lines is a ``Hosted`` part of its generation's
    document, so its blocks scroll in the one window with the runs the library
    sets, and a generation out of sight rests as it did. In WRAP a run is drawn
    where its first line falls.

    ``pinned`` is the frame's published length (I7): a result whose line is past
    it is treated as not arrived, as its line is not drawn.
    """

    state: TranscriptState
    node: NodeId
    transcript: Transcript
    cache: NodeTranscript
    pinned: int
    in_turn: bool
    text_px: float
    floor: int = 0
    # Whether the blocks' rows in the code face are the library's, and so
    # selectable: in TYPESET, where the library sets literal text at the size
    # the blocks' code face is pushed at (``tool_block.draw``'s ``select``).
    select: bool = False
    # The block under the pointer this frame, as the run it was drawn in and the
    # lines it covers (what "copy block" copies), or None.
    pointed: tuple[int | None, str] | None = None
    # The find's matches in blocks this frame (``find``): the anchors that hold
    # one, the current match where it is a block's, and whether it is brought
    # into view this frame.
    marked: frozenset[int] = frozenset()
    current: BlockHit | None = None
    reveal: bool = False
    # The transcript's turn at ``pinned`` (``Transcript.turn``), read in one call
    # so the start and the open state are of one moment: a call before its start,
    # or of a turn that has ended, has no result coming.
    turn_start: int = field(init=False)
    turn_open: bool = field(init=False)

    def __post_init__(self) -> None:
        self.turn_start, self.turn_open = self.transcript.turn(self.pinned)

    def find(self, found: Sequence[Any], current: int) -> None:
        """
        Take the find's matches for this frame's draw: ``found`` as the pane
        merged them, ``found[current]`` the current one. The current one is
        brought into view by the draw that reaches it while the pane's
        ``block_reveal`` names it, which may be a later frame than the step to
        it: a generation at rest draws no block.
        """
        self.marked = frozenset(hit.anchor for hit in found if isinstance(hit, BlockHit))
        held = found[current] if 0 <= current < len(found) else None
        self.current = held if isinstance(held, BlockHit) else None
        target = self.state.block_reveal
        self.reveal = (
            self.current is not None
            and target is not None
            and target == (self.node, self.current.anchor)
        )

    def faces(self) -> tool_block.Faces:
        px = self.text_px
        small = float(round(px * 0.8))
        return tool_block.Faces(
            mono=lambda: typeset.font(typeset.Weight.CODE, px),
            prose=lambda: typeset.font(typeset.Weight.REGULAR, px),
            caption=lambda: typeset.font(typeset.Weight.REGULAR, small),
        )

    def draw_run(self, run: int) -> bool | None:
        """
        A run's blocks and the rest of its bytes, in buffer order, as one item.
        ``False`` where none of the run is an entry's (it is drawn as its lines),
        ``None`` where every entry in it is drawn at its call, elsewhere.
        """
        if not 0 <= run < len(self.cache.run_spans) or run not in self.cache.hosted:
            return False
        start, end = self.cache.run_spans[run]
        pieces = self._pieces(start, end)
        if not pieces:
            return None
        imgui.begin_group()
        kind = self.cache.lines[self.cache.run_starts[run]].kind
        ink = getattr(P, _KIND_COLOUR[kind]).vec4
        faces = self.faces()
        for anchor, entry, text in pieces:
            if entry is None:
                self._bring(anchor)
                imgui.text_colored(ink, text)
                if anchor in self.marked:
                    _mark_line(self.current is not None and self.current.anchor == anchor)
            else:
                self._block(run, anchor, entry, faces)
        imgui.end_group()
        return True

    def _pieces(self, start: int, end: int) -> list[tuple[int, ToolEntry | None, str]]:
        """
        What the bytes ``[start, end)`` are drawn as: each block at its anchor, and
        every span of bytes no entry's line covers, as text.
        """
        drawn: list[tuple[int, ToolEntry | None, str]] = []
        covered: list[tuple[int, int]] = []
        for entry in self.transcript.tool_entries(start, end):
            entry = self._as_pinned(entry)
            call, result = entry.call, entry.result
            if call is not None and start <= call[0] < end:
                covered.append(call)
            if result is not None and start <= result.start < end:
                covered.append((result.start, result.end))
            if call is not None and start <= call[0] < end:
                drawn.append((call[0], entry, ""))
            elif result is not None and start <= result.start < end:
                # The call is drawn at its own line when it is on screen.
                if call is None or call[0] < self.floor:
                    drawn.append((result.start, entry, ""))
        at = start
        for low, high in sorted(covered):
            if low > at:
                drawn += self._uncovered(at, low)
            at = max(at, high)
        if at < end:
            drawn += self._uncovered(at, end)
        drawn.sort(key=lambda piece: piece[0])
        return drawn

    def _uncovered(self, low: int, high: int) -> list[tuple[int, ToolEntry | None, str]]:
        text = self.transcript.read(low, min(high, self.pinned)).strip("\n")
        return [(low, None, text)] if text else []

    def _as_pinned(self, entry: ToolEntry) -> ToolEntry:
        """``entry`` as this frame has it: a result whose line is past ``pinned`` is not yet."""
        result = entry.result
        if result is not None and result.start >= self.pinned:
            return dataclasses.replace(entry, result=None)
        return entry

    def _covered(self, entry: ToolEntry) -> str:
        """The lines of the transcript a block stands for: its call's and its result's."""
        spans = [entry.call] if entry.call is not None else []
        if entry.result is not None:
            spans.append((entry.result.start, entry.result.end))
        return "\n".join(
            self.transcript.read(low, min(high, self.pinned)).strip("\n") for low, high in spans
        )

    def _live(self, entry: ToolEntry) -> bool:
        """Whether a call with no result can still be running: its turn is going on."""
        return (
            self.in_turn
            and self.turn_open
            and entry.result is None
            and (entry.call is None or entry.call[0] >= self.turn_start)
        )

    def _key(self, anchor: int, entry: ToolEntry) -> tuple[Hashable, bool]:
        """What a block's layout is kept under, and whether its call is live."""
        live = self._live(entry)
        result = entry.result
        key = (
            self.node,
            id(self.transcript),
            anchor,
            entry.tool_use_id,
            None if result is None else (result.start, result.end),
            live,
        )
        return key, live

    def _layout(self, key: Hashable, entry: ToolEntry, live: bool) -> tool_block.Layout:
        self.state.block_seen[key] = self.state.now
        block = self.state.layouts.get(key)
        if block is None:
            block = self.state.layouts[key] = tool_block.layout(entry, live)
        return block

    def hits(self, run: int, needle: str) -> list[BlockHit]:
        """
        Every match of ``needle`` in what a run of tool lines is drawn as, in
        buffer order: in each block's entry (``tool_block.find``), and in the
        bytes of the run that are drawn as text. Empty for a run that is not a
        hosted one.

        A block's matches are kept by its key and the needle (``block_hits``),
        so a frame with nothing new finds nothing again.
        """
        if not needle or not 0 <= run < len(self.cache.run_spans) or run not in self.cache.hosted:
            return []
        wanted = needle.casefold()
        found: list[BlockHit] = []
        for anchor, entry, text in self._pieces(*self.cache.run_spans[run]):
            if entry is None:
                found += [BlockHit(run, anchor, None, True)] * text.casefold().count(wanted)
                continue
            key, live = self._key(anchor, entry)
            kept = self.state.block_hits.get(key)
            if kept is None or kept[0] != needle:
                block = self._layout(key, entry, live)
                kept = self.state.block_hits[key] = (needle, tool_block.find(entry, block, needle))
            found += [BlockHit(run, anchor, hit.row, hit.shown) for hit in kept[1]]
        return found

    def _mark(self, anchor: int) -> tool_block.Mark | None:
        """The find's mark on the block or text at ``anchor``, or None."""
        if anchor not in self.marked:
            return None
        current = self.current
        if current is None or current.anchor != anchor:
            return tool_block.Mark(_HIT_INK)
        row = None if current.shown else current.row
        return tool_block.Mark(_CURRENT_INK, row, self.reveal)

    def _bring(self, anchor: int) -> None:
        """Scroll the log to what is drawn next if it is the find's to bring into view."""
        if self.reveal and self.current is not None and self.current.anchor == anchor:
            imgui.set_scroll_here_y(1.0 / 3.0)
            self.state.block_reveal = None

    def _block(self, run: int, anchor: int, entry: ToolEntry, faces: tool_block.Faces) -> None:
        key, live = self._key(anchor, entry)
        self.state.block_frames[key] = imgui.get_frame_count()
        block = self._layout(key, entry, live)
        opened = (self.node, anchor) in self.state.expanded
        mark = self._mark(anchor)
        self._bring(anchor)

        def viewer() -> tuple[tool_block.Row, ...]:
            rows = self.state.viewers.get(key)
            if rows is None:
                rows = self.state.viewers[key] = tool_block.viewer_rows(entry)
            return rows

        pressed = tool_block.draw(
            block,
            key=str(anchor),
            faces=faces,
            expanded=opened,
            viewer=viewer,
            mark=mark,
            select=(self.state.block_views, key) if self.select else None,
        )
        if widgets.log_hovered() and imgui.is_mouse_hovering_rect(
            imgui.get_item_rect_min(), imgui.get_item_rect_max(), False
        ):
            self.pointed = (run, self._covered(entry))
        if pressed:
            self.state.expanded ^= {(self.node, anchor)}

    def prune(self) -> None:
        """
        Let go of a viewer's rows once no block drew them this frame or the last,
        and of a layout once its block has not been drawn for ``_FEED_KEEP_S``.

        A layout is a few rows, and a block goes out of sight whenever its
        generation rests, so it is kept as long as the feed that would draw it
        again is (``_FEED_KEEP_S``): a scroll back up does not lay out again. A
        viewer's rows are the whole entry, up to a mebibyte, and are made again
        on the next expansion.
        """
        frame = imgui.get_frame_count()
        state = self.state
        for key in [k for k, at in state.block_frames.items() if at < frame - 1]:
            del state.block_frames[key]
            state.viewers.pop(key, None)
        stale = state.now - _FEED_KEEP_S
        for key in [k for k, at in state.block_seen.items() if at < stale]:
            del state.block_seen[key]
            state.layouts.pop(key, None)
            state.block_hits.pop(key, None)
        views = state.block_views
        if len(views):
            views.prune([k for k in views.keys() if isinstance(k, tuple) and k[0] in state.layouts])


_KIND_COLOUR = {
    SegmentKind.REASONING: "text_dim",
    SegmentKind.OUTPUT: "text",
    SegmentKind.TOOL_CALL: "accent",
    SegmentKind.TOOL_RESULT: "diff_context",
    SegmentKind.ERROR: "danger",
    SegmentKind.SYSTEM: "text_dim",
    SegmentKind.COMPACTION: "warn",
    # pptmstr's own message to the session (``AgentSession.wake``): never the
    # operator's dim ink, and never behind the operator's ``>``.
    SegmentKind.NOTICE: "focus",
}


def draw(
    snap: Snapshot,
    state: TranscriptState,
    selected: NodeId | None,
    closed_path: str | None = None,
    *,
    show_reasoning: bool | None = None,
) -> None:
    """
    One agent's transcript, or one line for a session that has none loaded.

    A session recalled from its file (``AgentRecord.recalled``) has an empty
    transcript because nothing read it, and the controls, the copy button and
    "(nothing yet)" over that would say a session ran here and has not spoken.
    It gets the closed line and no controls. ``closed_path`` is where the CLI
    keeps that conversation, for the operator who wants to read it without
    resuming: a value the shell read once and hands over, since working it out
    here would be a directory listing per frame. None draws the line alone.

    ``show_reasoning`` is ``Settings.show_reasoning``; left out, the pane keeps
    what it was last told, which starts as shown.
    """
    if show_reasoning is not None:
        state.show_reasoning = show_reasoning
    if not state.drawn:
        state.node, state.drawn = selected, True
    elif selected != state.node:
        state.switched_to(selected)
    if selected is None or (record := snap.get(selected)) is None:
        imgui.text_disabled("select an agent to read its transcript")
        return

    closed = widgets.closed_line(snap.get((selected[0], None)))
    if closed is not None:
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(closed)
        if closed_path:
            imgui.text_disabled(f"transcript: {widgets.one_line(closed_path)}")
        imgui.pop_text_wrap_pos()
        return

    cache = state.cache_for(selected)
    # Pin the length once. Everything below renders a consistent prefix even while
    # the asyncio thread keeps appending (I7).
    pinned = record.transcript.published_length
    cache.sync(record.transcript, pinned)
    _find_keys(state.find)

    mode = state.shown_mode()
    if (state.feeds or len(state.block_views)) and not typeset.enabled(typeset.Pane.CONTEXT):
        # A feed is the library's views of lines this pane still has. With the
        # seam off they are views nothing draws, and after the library stopped
        # they are views of a library that may never draw again: the lines set
        # them again in one frame if it does. The blocks' views likewise.
        state.feeds.clear()
        state.block_views.clear()

    bar = state.find
    text_px = typeset.body_px() if mode is RenderMode.TYPESET else imgui.get_font_size()
    blocks = _Blocks(
        state,
        selected,
        record.transcript,
        cache,
        pinned,
        record.state in _IN_TURN,
        text_px,
        select=mode is RenderMode.TYPESET and typeset.px(typeset.Role.LITERAL) == text_px,
    )
    # Everything the log's height follows but the width its bar takes.
    state.log_key = (selected, pinned, mode, state.show_reasoning, len(state.expanded))
    side = margin_px(text_px)
    if mode is RenderMode.TYPESET:
        feed = state.feed_for(selected)
        feed.advance(cache)
        # Found in the views the last frame drew, before this one draws them, so
        # the marks and a scroll are this frame's.
        found = (
            _typeset_found(feed, cache, blocks, state.show_reasoning, bar.needle)
            if bar.open
            else []
        )
        bar.found(len(found))
        # Joined when the button is pressed: unlike the line modes' value, this one
        # is not needed to draw anything.
        _draw_controls(state, record.node_id, lambda: feed.visible_text(state.show_reasoning), side)
        imgui.separator()
        _draw_typeset(state, feed, cache, found, blocks)
        blocks.prune()
        return

    visible = _visible(state, cache)
    drawn = visible if mode is RenderMode.RAW else visible[-_WRAP_WINDOW:]
    found_lines: list[int | BlockHit] = []
    if bar.open and mode is RenderMode.WRAP:
        found_lines = _wrap_found(drawn, cache, blocks, bar.needle)
    elif bar.open:
        found_lines = list(line_matches(drawn, bar.needle))
    bar.found(len(found_lines))
    visible_text = copy_text(visible)
    _draw_controls(state, record.node_id, lambda: visible_text, side)
    imgui.separator()
    _draw_lines(state, cache, visible, mode, found_lines, blocks)
    blocks.prune()


def _in_block(cache: NodeTranscript, line: Line) -> bool:
    """Whether ``line`` is drawn as part of a tool block rather than as itself."""
    return line.kind in _TOOL_KINDS and line.run in cache.hosted


def _wrap_found(
    drawn: Sequence[Line], cache: NodeTranscript, blocks: _Blocks, needle: str
) -> list[int | BlockHit]:
    """
    WRAP's matches in the order drawn: a line's as its index in ``drawn``, once
    each time it holds the needle, and a run of tool lines' as its blocks and its
    text hold them (``_Blocks.hits``), where the run is drawn, at its first line.
    """
    wanted = needle.casefold()
    if not wanted:
        return []
    found: list[int | BlockHit] = []
    done: int | None = None
    for index, line in enumerate(drawn):
        if _in_block(cache, line):
            if line.run != done:
                done = line.run
                found += blocks.hits(line.run, needle)
            continue
        found += [index] * line.text.casefold().count(wanted)
    return found


FIND_CHORD = int(imgui.Key.mod_ctrl) | int(imgui.Key.f)
"""
The chord ``_find_keys`` opens the find row on, and the one the Session menu's
Find item shows (``main_menu``). Its exact modifiers: Ctrl+Shift+F is not it.
"""


def _find_keys(bar: FindBar) -> None:
    """
    ``FIND_CHORD`` over the pane opens the find row, and Escape over the pane closes
    it.

    Only while the pane, or a window inside it, has the focus, and not while any
    item is active: a composer being typed in keeps ``Ctrl+F``, and so does the
    find field itself, which is the open row already. Escape while the field has
    the keyboard is the field's (``_draw_find``).
    """
    if not imgui.is_window_focused(imgui.FocusedFlags_.child_windows):
        return
    if bar.open and not bar.active and imgui.is_key_pressed(imgui.Key.escape, False):
        bar.escape(imgui.get_frame_count())
        return
    if imgui.is_any_item_active():
        return
    # Not on a repeat, and with exactly the chord's modifiers down.
    if imgui.is_key_chord_pressed(FIND_CHORD):
        bar.show()


_SUB_AGENT_NOTE = "sub-agent: complete messages only, not live"


def _draw_controls(
    state: TranscriptState, node_id: NodeId, copy_value: Callable[[], str], side: float = 0.0
) -> None:
    """
    The row over the log, in from the pane's edge by ``side``, the log's margin, so
    that it starts where the log's text does.
    """
    if side:
        imgui.indent(side)
    _draw_control_row(state, node_id, copy_value, side)
    if side:
        imgui.unindent(side)


def _draw_control_row(
    state: TranscriptState, node_id: NodeId, copy_value: Callable[[], str], side: float
) -> None:
    if state.mode is RenderMode.RAW:
        # The earlier-lines view: the way back to the log as it is set, at its foot.
        if widgets.small_button("back to latest"):
            state.mode = None
            state.follow_tail = True
            state.held = False
        imgui.same_line()
    # Copies what is on screen, reasoning as it is shown. Right-clicking a run (or
    # block) copies that run/block whole; the two affordances are deliberately
    # different units.
    if widgets.small_button("copy") and (copied := copy_value()):
        imgui.set_clipboard_text(copied)
    if state.find.open:
        imgui.same_line()
        _draw_find(state.find)
    if node_id[1] is not None:
        # Sub-agent output does not stream (§2.5.1). Saying so beats letting a quiet
        # row be read as a stuck one. A row of its own, broken in a narrow dock,
        # and short of the right edge by the margin it starts in by.
        imgui.push_text_wrap_pos(
            imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x - side
        )
        imgui.text_colored(P.warn.vec4, _SUB_AGENT_NOTE)
        imgui.pop_text_wrap_pos()


def _draw_find(bar: FindBar) -> None:
    """
    The find field, how many it found, and its close button. Enter is the next
    match and Shift+Enter the one before; Escape in the field closes the row.
    """
    if bar.focus:
        imgui.set_keyboard_focus_here()
        bar.focus = False
    imgui.set_next_item_width(200.0 * imgui.get_font_size() / 16.0)
    with hint_colour(imgui, dialog=False):
        entered, needle = imgui.input_text_with_hint(
            "##find", "find", bar.needle, imgui.InputTextFlags_.enter_returns_true
        )
    active = imgui.is_item_active()
    escaped = (active or bar.active) and imgui.is_key_pressed(imgui.Key.escape, False)
    # ImGui's Escape hands back what the field held when it took the keyboard; what
    # was typed is the needle kept for the next Ctrl+F.
    if needle != bar.needle and not escaped:
        bar.needle = needle
        bar.current = 0
        bar.reveal = True
    if entered:
        bar.step(-1 if imgui.get_io().key_shift else 1)
        # Enter gives the keyboard up, and the next match is one more Enter.
        bar.focus = True
    bar.active = active
    imgui.same_line()
    imgui.text_disabled(bar.status() or " ")
    imgui.same_line()
    clicked = widgets.small_button("x##find")
    if escaped:
        bar.escape(imgui.get_frame_count())
    elif clicked:
        bar.close()


def _visible(state: TranscriptState, cache: NodeTranscript) -> list[Line]:
    return [
        line
        for line in cache.lines
        if state.show_reasoning or line.kind is not SegmentKind.REASONING
    ]


def _visible_before(state: TranscriptState, cache: NodeTranscript, line: int) -> int:
    """
    How many of the visible lines come before the transcript's line ``line``.
    """
    if state.show_reasoning:
        return min(line, len(cache.lines))
    return sum(1 for each in cache.lines[:line] if each.kind is not SegmentKind.REASONING)


def _earlier(state: TranscriptState, count: int, first: int) -> None:
    """
    The lines the pane left out, as a button that shows them: RAW, which draws
    every line, opened on the last of them (``first`` of the visible lines is the
    first one the pane drew) and left by ``back to latest``.
    """
    if widgets.small_button(f"{count:,} earlier lines##earlier"):
        state.mode = RenderMode.RAW
        state.raw_from = first
        state.follow_tail = False


# A line holding a match, and the current one: ImU32, A in the top byte. The
# library's find marks behind the text; these are over it, so fainter.
_HIT_INK = 0x4039B3E8
_CURRENT_INK = 0x701A7AF2


def _mark_line(current: bool) -> None:
    """
    The last line drawn marked as holding a match, the whole width of the pane.
    """
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    right = imgui.get_window_pos().x + imgui.get_window_size().x
    imgui.get_window_draw_list().add_rect_filled(
        low, imgui.ImVec2(right, high.y), _CURRENT_INK if current else _HIT_INK
    )


def _draw_lines(
    state: TranscriptState,
    cache: NodeTranscript,
    lines: list[Line],
    mode: RenderMode | None = None,
    found: Sequence[int | BlockHit] = (),
    blocks: _Blocks | None = None,
) -> None:
    """
    ``lines`` as RAW or WRAP draws them. ``found`` is where the find row's matches
    are, as indices into the lines drawn: every line in RAW, the last
    ``_WRAP_WINDOW`` in WRAP. In WRAP a run of tool lines is drawn as ``blocks``
    draws it, where its first line falls; RAW draws every line as it is.
    """
    if mode is None:
        mode = state.shown_mode()
    if not lines:
        imgui.text_disabled("(nothing yet)")
        return

    log = _begin_log(state, imgui.get_font_size())
    if log is None:
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
    # Over a block too: a block is a child of the log, and part of it.
    live = widgets.log_hovered()
    mouse_y = imgui.get_io().mouse_pos.y
    hovered: int | None = None
    row_top: float | None = None

    def track(line: Line) -> None:
        nonlocal hovered, row_top
        top = imgui.get_item_rect_min().y if row_top is None else row_top
        row_top = imgui.get_item_rect_max().y
        if live and top <= mouse_y < row_top:
            hovered = line.run

    bar = state.find
    marked = {at for at in found if isinstance(at, int)}
    held = found[bar.current] if found else None
    reveal = bar.reveal and held is not None
    current = held if isinstance(held, int) else None
    if reveal:
        state.block_reveal = None
        if isinstance(held, BlockHit):
            _reveal_block(state, held)
    if blocks is not None:
        blocks.find(found, bar.current)
    # The view is moved for the operator this frame, so it is not following.
    moved = reveal
    if mode is RenderMode.WRAP:
        drawn = lines[-_WRAP_WINDOW:]
        if len(drawn) < len(lines):
            _earlier(state, len(lines) - len(drawn), len(lines) - len(drawn))
        if blocks is not None and drawn:
            blocks.floor = cache.run_spans[drawn[0].run][0]
        # The run whose blocks were drawn at its first line, and whose lines are not.
        done: int | None = None
        # No clipper: wrapped rows have no uniform height for it to work from. The
        # window bound is what keeps that affordable.
        for index, line in enumerate(drawn):
            if line.run == done:
                continue
            if blocks is not None and _in_block(cache, line):
                drew = blocks.draw_run(line.run)
                if drew is not False:
                    done = line.run
                    if drew:
                        track(line)
                    continue
            imgui.push_style_color(imgui.Col_.text, getattr(P, _KIND_COLOUR[line.kind]).vec4)
            imgui.text_wrapped(line.text or " ")
            imgui.pop_style_color()
            track(line)
            if index in marked:
                _mark_line(index == current)
                if reveal and index == current:
                    imgui.set_scroll_here_y(1.0 / 3.0)
    else:
        row = imgui.get_text_line_height_with_spacing()
        top = imgui.get_cursor_pos_y()
        high = imgui.get_window_height()
        if reveal and current is not None:
            imgui.set_scroll_y(max(0.0, top + current * row - high / 3.0))
        if state.raw_from is not None:
            # The last line left out at the foot of the view, the one above it
            # above that: where the other renderer's top was.
            imgui.set_scroll_y(max(0.0, top + state.raw_from * row - high + row))
            state.raw_from = None
            moved = True
        clipper = imgui.ListClipper()
        clipper.begin(len(lines))
        while clipper.step():
            for index in range(clipper.display_start, clipper.display_end):
                line = lines[index]
                imgui.text_colored(getattr(P, _KIND_COLOUR[line.kind]).vec4, line.text or " ")
                track(line)
                if index in marked:
                    _mark_line(index == current)
        clipper.end()
    if reveal:
        bar.reveal = False

    if not imgui.is_popup_open(_CONTEXT_ID):
        state.context_run = hovered
        pointed = None if blocks is None else blocks.pointed
        state.context_block = "" if pointed is None else pointed[1]
    _draw_context_menu(state, cache, lines)

    if moved:
        state.follow_tail = False
        state.held = True
    _handle_scroll(state)
    _end_log(state, log)


def margin_px(text_px: float) -> float:
    """
    The log's side margin for text ``text_px`` tall: about one en dash, which is
    half an em, in whole pixels.
    """
    return float(round(text_px / 2.0))


def holds_bar(
    held: bool, shown: bool, moved: bool, content: float, room: float, slack: float
) -> bool:
    """
    Whether a log's vertical bar is held on for the next frame: once it has shown
    for the log's own text (``shown``: ImGui drew it, and the text it drew it for,
    last frame's, was taller than the log), it stays until what the log is drawn
    from has moved and the text, at the width the bar leaves it, is shorter than
    the log by ``slack``. A bar ImGui shows for a window's earlier content, which
    this log's text never asked for, is not taken.

    The bar takes width, so the text re-wraps when it comes and goes. For a log
    that wraps as text does, a narrower measure is never shorter, and slack alone
    would do. A log can lay out otherwise -- a block or a table set one way when
    wide and another when narrow -- and then a log at its edge overflows without
    the bar and fits with it, every frame. So letting go also waits for something
    other than the bar to have moved (``moved``): the bytes, the mode, the size.
    Each flip is then one change of the log's own, and never two frames' answers
    to each other. ``slack`` keeps a log one line short of full from taking the bar
    back with its next line.
    """
    if not held:
        return shown
    return not (moved and content + slack <= room)


def _begin_log(state: TranscriptState, text_px: float, flags: int = 0) -> int | None:
    """
    Open the log's child: the margin ``margin_px`` either side of the text, and a
    vertical scrollbar where there is something to scroll. Returns the child's id,
    for ``_end_log``, or ``None`` when ImGui has it clipped.

    The margin is the child's own padding, which a child without a border is not
    given by ImGui, on both sides. ImGui puts the bar outside the padding, so with
    the bar shown the right margin is between the text and the bar. Nothing is
    reserved for a bar that is not there: the text is as wide as the log allows,
    and re-wraps once when the bar comes or goes. ImGui shows the bar the frame
    after the text outgrew the log; from then it is held (``holds_bar``).
    """
    ident = imgui.get_id("##transcript")
    side = margin_px(text_px)
    if ident in state.bars:
        flags |= imgui.WindowFlags_.always_vertical_scrollbar.value
    imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(side, 0.0))
    opened = imgui.begin_child(
        "##transcript",
        imgui.ImVec2(0.0, 0.0),
        imgui.ChildFlags_.always_use_window_padding,
        flags,
    )
    imgui.pop_style_var()
    if not opened:
        return None
    return ident


def _end_log(state: TranscriptState, ident: int) -> None:
    """
    Close the log's child, noting whether its bar is held for the next frame.
    """
    window = imgui.internal.get_current_window()
    key = (state.log_key, window.size.x, window.size.y)
    held = state.bars.get(ident, _NOT_HELD)
    content = window.dc.cursor_max_pos.y - window.dc.cursor_start_pos.y
    room = window.inner_rect.max.y - window.inner_rect.min.y
    # ImGui decides the bar from the content of the frame before, so that is the
    # frame whose text the bar is for.
    overflowed = state.overflowed.get(ident, False)
    state.overflowed[ident] = content > room
    if holds_bar(
        held is not _NOT_HELD,
        bool(window.scrollbar_y) and overflowed,
        key != held,
        content,
        room,
        imgui.get_text_line_height_with_spacing(),
    ):
        if held is _NOT_HELD:
            state.bars[ident] = key
    else:
        state.bars.pop(ident, None)
    imgui.end_child()


# What ``TranscriptState.bars`` holds for a log whose bar is not held: no key a log
# is drawn from is this.
_NOT_HELD = object()


def _run_key(run: int) -> tuple[str, int]:
    """
    The key of the view the open run is drawn by.
    """
    return ("open", run)


def _draw_run(
    views: typeset.ViewCache, run: OpenRun, live: list[Hashable], blocks: _Blocks | None = None
) -> None:
    """
    The open run in a view of its own: a stream for markdown, which lays out once
    what is committed and reads what arrived, a prompt as the parts it is set as
    closed, a run of tool lines as its blocks (``_Blocks``), and literal text for
    the rest, the library's where it keeps the run's columns (``typeset.plain``).
    """
    ink = getattr(P, _KIND_COLOUR[run.kind])
    key = _run_key(run.run)
    live.append(key)
    if run.kind is SegmentKind.SYSTEM:
        typeset.document(views, key, run.prompt, apart=True, literal=True)
        return
    if blocks is not None and run.kind in _TOOL_KINDS and run.run in blocks.cache.hosted:
        number = run.run
        typeset.document(
            views,
            key,
            run.hosted,
            apart=True,
            literal=True,
            host=lambda index, part: blocks.draw_run(number),
        )
        return
    if run.kind not in _MARKDOWN_KINDS:
        typeset.plain(views, key, run.literal, colour=ink, literal=True)
        return
    stream = typeset.stream(views, key)
    text = run.text
    over = blocks is not None and not blocks.in_turn
    if stream.closed and (not over or len(stream.text) < len(text)):
        # More arrived after the turn was taken as over, or a turn began: read
        # again from the start, open.
        stream.set_text(text)
    # By length and not by what was last handed over: a stream made again (the
    # reasoning shown after being hidden, a feed kept across a stop) is given
    # everything, and one that has it all is given nothing.
    if len(stream.text) < len(text):
        stream.append(text[len(stream.text) :])
    if over and not stream.closed:
        # The message is over with its turn, so its last line is finished: a
        # fence ending on its closing marks with no line end is closed, and
        # coloured (``Stream._ink``).
        stream.close()
    stream.draw(ink=None if run.kind is SegmentKind.OUTPUT else ink)


def _draw_typeset(
    state: TranscriptState,
    feed: TypesetFeed,
    cache: NodeTranscript,
    found: Sequence[Any] = (),
    blocks: _Blocks | None = None,
) -> None:
    # Literal runs are never broken, hence the horizontal scrollbar.
    log = _begin_log(state, typeset.body_px(), imgui.WindowFlags_.horizontal_scrollbar.value)
    if log is None:
        imgui.end_child()
        return

    show = state.show_reasoning
    # Every key drawn this frame, in the order drawn.
    live: list[Hashable] = []

    announced = feed.skipped > 0
    lost = _trim(feed)
    top = imgui.get_cursor_pos_y()
    if feed.skipped:
        _earlier(state, feed.skipped, _visible_before(state, cache, feed.skipped))
    if lost:
        if not announced:
            lost = max(0.0, lost - (imgui.get_cursor_pos_y() - top))
        # The room the dropped generation took stays for this frame: the window's
        # scroll position is the last frame's, and everything below would be drawn
        # that much too high.
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + lost)

    _hand_marks(state.find, found)
    moved = _reveal(state, feed, found)
    if blocks is not None:
        blocks.find(found, state.find.current)
    shown = feed.shown_open(show)
    if not feed.generations and shown is None and not feed.skipped:
        imgui.text_disabled("(nothing yet)")
    if blocks is not None:
        first = feed.generations[0].runs[0].run if feed.generations else None
        if first is None and feed.open is not None:
            first = feed.open.run
        if first is not None and first < len(cache.run_spans):
            blocks.floor = cache.run_spans[first][0]
    for generation in feed.generations:
        _draw_generation(feed, generation, show, live, blocks)
    if shown is not None:
        _draw_run(feed.views, shown, live, blocks)
    feed.views.prune(live)

    if not imgui.is_popup_open(_CONTEXT_ID):
        hovered = widgets.log_hovered()
        pointed = _typeset_pointed(feed, live, show) if hovered else None
        if pointed is None and hovered and blocks is not None:
            pointed = blocks.pointed
        state.typeset_target = pointed
    _draw_typeset_menu(state, feed, cache)

    end = imgui.get_cursor_pos_y()
    if moved:
        state.follow_tail = False
        state.held = True
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
    _end_log(state, log)


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
    feed: TypesetFeed,
    generation: Generation,
    show: bool,
    live: list[Hashable],
    blocks: _Blocks | None = None,
) -> None:
    """
    One generation, as one document. A run that closed is in it from the frame it
    closed: the seam reads and lays out what it is handed in the call that draws it.
    A run of tool lines is a part the document hands back to be drawn
    (``typeset.views.Hosted``): its blocks, by ``blocks``.
    Each run is a view of its own (``apart``), as it was while it was open, so it
    is where it was: joined to a markdown run above it, it would be set the
    library's block gap under that run and not ImGui's item spacing, 2.1 px
    lower for a paragraph under a paragraph, in the frame it closed.

    Out of the window and unchanged, the document rests (``typeset.document``):
    a generation is twenty views or more, and the scrollback is most of the log.
    """
    start = imgui.get_cursor_pos_y()
    generation.top = start
    wide = max(1.0, imgui.get_content_region_avail().x)
    generation.in_sight = generation.extent > 0.0 and imgui.is_rect_visible(
        imgui.ImVec2(wide, generation.extent)
    )
    parts = generation.parts(show)
    if parts:
        live.append(generation.key)
        owners = generation.owners(show)

        def host(index: int, part: typeset.Part) -> bool | None:
            return False if blocks is None else blocks.draw_run(owners[index].run)

        typeset.document(
            feed.views,
            generation.key,
            parts,
            rests=True,
            apart=True,
            literal=True,
            host=host,
        )
    generation.extent = imgui.get_cursor_pos_y() - start


def _typeset_matches(
    feed: TypesetFeed, show_reasoning: bool, needle: str
) -> list[tuple[Hashable, Any, Any]]:
    """
    Every match of ``needle`` in the library's views of the feed, in reading order,
    as the key of the view, the view, and the library's match in it
    (``typeset.find_matches``).

    The views are the ones the last frame drew. Text ImGui draws here has no view
    and is not searched, and the lines above the window are not in any view. The
    log's views hold no block a host draws: a literal run is a code block.
    """
    keys = [generation.key for generation in feed.generations]
    shown = feed.shown_open(show_reasoning)
    if shown is not None:
        keys.append(_run_key(shown.run))
    return typeset.find_matches(feed.views, keys, needle)


def _typeset_found(
    feed: TypesetFeed,
    cache: NodeTranscript,
    blocks: _Blocks | None,
    show_reasoning: bool,
    needle: str,
) -> list[Any]:
    """
    Every match in TYPESET, in run order: the library's in its views
    (``_typeset_matches``, as ``(key, view, match)``) and each hosted run's in its
    blocks (``_Blocks.hits``, as ``BlockHit``).
    """
    ordered: list[tuple[int, int, Any]] = []
    for item in _typeset_matches(feed, show_reasoning, needle):
        ordered.append((_match_run(feed, item, show_reasoning), len(ordered), item))
    if blocks is not None and needle:
        hosted = [
            run.run
            for generation in feed.generations
            for run in generation.shown(show_reasoning)
            if run.kind in _TOOL_KINDS and run.run in cache.hosted
        ]
        shown = feed.shown_open(show_reasoning)
        if shown is not None and shown.kind in _TOOL_KINDS and shown.run in cache.hosted:
            hosted.append(shown.run)
        for run in hosted:
            ordered += [(run, len(ordered), hit) for hit in blocks.hits(run, needle)]
    ordered.sort(key=lambda each: (each[0], each[1]))
    return [item for _, _, item in ordered]


def _match_run(feed: TypesetFeed, item: tuple[Hashable, Any, Any], show: bool) -> int:
    """The run a match of the library's is in: what orders it among the blocks'."""
    key, view, match = item
    generation = feed.generation(key)
    if generation is None:
        return key[1] if isinstance(key, tuple) and isinstance(key[1], int) else -1
    owners = generation.owners(show)
    part = feed.views.part_at(key, match.block, view)
    if part is None or not 0 <= part < len(owners):
        return owners[0].run if owners else -1
    return owners[part].run


def _reveal_block(state: TranscriptState, hit: BlockHit) -> None:
    """
    Bring a block's match into view: the block, from where it is drawn, and its
    viewer opened at the row where the collapsed block does not draw the match.
    """
    node = state.node
    if node is None:
        return
    state.block_reveal = (node, hit.anchor)
    if not hit.shown and hit.row is not None:
        state.expanded.add((node, hit.anchor))


def _hand_marks(bar: FindBar, found: Sequence[Any]) -> None:
    """
    Give each view the marks of its matches, the current one stronger, and take
    them from a view that has none now. A view is handed marks only when they
    changed: it packs them for the library each time.
    """
    grouped: dict[int, tuple[Any, list[Any], int | None]] = {}
    for number, item in enumerate(found):
        if isinstance(item, BlockHit):
            continue
        _, view, match = item
        held, matches, current = grouped.get(id(view), (view, [], None))
        if number == bar.current:
            current = len(matches)
        matches.append(match)
        grouped[id(view)] = (held, matches, current)
    handed: dict[int, tuple[Any, tuple[Any, ...]]] = {}
    for ident, (view, matches, current) in grouped.items():
        marks = typeset.find_marks(matches, current)
        if bar.marked.get(ident, (None, ()))[1] != marks:
            view.set_marks(marks)
        handed[ident] = (view, marks)
    for ident, (view, _) in bar.marked.items():
        if ident not in handed:
            view.set_marks(None)
    bar.marked = handed


def _reveal(state: TranscriptState, feed: TypesetFeed, found: Sequence[Any]) -> bool:
    """
    Bring the current match into view if the find row asked for it. Returns whether
    it did.

    The view's own ``scroll_to`` does it, in the view's next draws. A generation
    that was out of the window last frame is not drawn (``_draw_generation``), so
    the window is first scrolled to where that generation starts.
    """
    bar = state.find
    if not bar.reveal or not found:
        return False
    bar.reveal = False
    item = found[bar.current]
    state.block_reveal = None
    if isinstance(item, BlockHit):
        _reveal_block(state, item)
        for held in feed.generations:
            if any(run.run == item.run for run in held.runs):
                if not held.in_sight:
                    imgui.set_scroll_y(max(0.0, held.top - imgui.get_window_height() / 3.0))
                break
        return True
    key, view, match = item
    generation = feed.generation(key)
    if generation is not None and not generation.in_sight:
        imgui.set_scroll_y(max(0.0, generation.top - imgui.get_window_height() / 3.0))
    view.scroll_to(match.block, match.start, align=1.0 / 3.0)
    return True


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
    runs = generation.owners(show)
    return runs[part].run if 0 <= part < len(runs) else None


def _draw_typeset_menu(state: TranscriptState, feed: TypesetFeed, cache: NodeTranscript) -> None:
    """
    Copy the selection, the block or the run that was right-clicked, or everything shown.
    The selection is a view's of the runs or of a tool block's rows.

    The run is copied as the line modes copy it: whole and unfiltered, from the
    lines, so a run the window cut short at the top is copied from its first line.
    A selection is inside one of the library's views, so it ends with its run.
    """
    if not widgets.begin_context_menu(_CONTEXT_ID):
        return

    for views in (feed.views, state.block_views):
        for key in views.keys():
            if (selected := views.selected(key)) and imgui.menu_item_simple("copy selection"):
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
    if not widgets.begin_context_menu(_CONTEXT_ID):
        return

    if state.context_block:
        if imgui.menu_item_simple("copy block"):
            imgui.set_clipboard_text(state.context_block)
    if state.context_run is not None:
        _copy_run_item(cache, state.context_run)

    if imgui.menu_item_simple("copy visible"):
        imgui.set_clipboard_text(copy_text(visible))
    imgui.end_popup()


def _handle_scroll(state: TranscriptState) -> None:
    """
    Follow the tail until the operator scrolls away from it, and again from when
    they are back at its foot.

    The behaviour, and the reason position cannot drive the disengage, live in
    ``widgets.follow_tail`` -- DETAIL's narration needs the same thing, and the two
    panes drifting apart on it would be invisible until one of them felt wrong.
    Two things are added here. A drag of the scrollbar lets go as an upward wheel
    does: with no checkbox it is the other way to leave the foot, and while
    following the view is put back at the foot every frame. And while ``held``,
    nothing follows until the operator scrolls.
    """
    window = imgui.internal.get_current_window()
    dragging = imgui.internal.get_active_id() == imgui.internal.get_window_scrollbar_id(
        window, imgui.internal.Axis.y
    )
    if state.held:
        wheel = widgets.wheels_here() and imgui.get_io().mouse_wheel != 0.0
        if not (wheel or dragging):
            state.follow_tail = False
            return
        state.held = False
    if state.follow_tail and dragging:
        state.follow_tail = False
        return
    state.follow_tail = widgets.follow_tail(state.follow_tail)
