"""
The transcript pane: one agent's output, styled by segment kind.

Three problems collide here, which is why the design expected this part to fight
the library.

**Cost.** A transcript grows without bound and the UI rebuilds every frame, so
re-splitting the whole buffer into lines each frame is O(n) forever. The cache
below converts only the bytes that arrived since last frame, keyed off the
published length a snapshot pinned (I7) -- so steady-state cost is proportional to
new output, not to history.

**Wrapping.** ``ImGuiListClipper`` needs uniform row heights, and wrapped text does
not have them. Wrapping also cannot be combined with per-line colour and selectable
text -- that is a real gap in ImGui, not a workaround failing. So it is a toggle:
off by default, giving uniform rows and a clipper that makes 100k-line transcripts
free; on when reading prose, at the cost of rendering only a bounded window.

**Liveness.** Root sessions stream token by token. Sub-agents do not -- their output
arrives in complete messages only (§2.5.1). The pane says which it is looking at
rather than letting the operator infer that a quiet sub-agent is a stuck one.

**Copying.** ImGui text is not selectable, so a run of output is copied whole rather
than dragged over. That is a better unit than a selection anyway: no partial first
line, no dropped trailing newline, and it round-trips into an issue.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from imgui_bundle import imgui

from ..model import NodeId, Snapshot
from ..theme import P
from ..transcript import SegmentKind, Transcript

# How many lines to draw when wrapping is on and the clipper cannot be used.
# Without a bound, turning wrap on over a large transcript stalls the frame.
_WRAP_WINDOW = 400


@dataclass(frozen=True, slots=True)
class Line:
    kind: SegmentKind
    text: str
    # Which run this line belongs to -- see NodeTranscript.run_starts.
    run: int


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
    last_frame_touched: int = 0
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
    show_reasoning: bool = True
    wrap: bool = False
    follow_tail: bool = True
    search: str = ""
    frame: int = 0
    # The run the context menu acts on. Latched while the menu is closed, because
    # opening it moves the hover onto the popup and would otherwise clear the target
    # out from under the item the operator just right-clicked.
    context_run: int | None = None

    def cache_for(self, node_id: NodeId) -> NodeTranscript:
        cache = self.caches.get(node_id)
        if cache is None:
            cache = NodeTranscript()
            self.caches[node_id] = cache
        cache.last_frame_touched = self.frame
        return cache

    def prune(self, frame: int) -> None:
        """
        Drop caches for nodes nobody is looking at.

        A transcript cache is the largest thing this UI holds, so keeping one per
        node ever selected is how a long session turns into a memory leak.
        """
        self.frame = frame
        stale = [n for n, c in self.caches.items() if c.last_frame_touched < frame - 120]
        for node_id in stale:
            del self.caches[node_id]


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

    visible = _visible(state, cache)
    _draw_controls(state, record.node_id, visible)
    imgui.separator()
    _draw_lines(state, cache, visible)


def _draw_controls(state: TranscriptState, node_id: NodeId, visible: list[Line]) -> None:
    changed, state.show_reasoning = imgui.checkbox("reasoning", state.show_reasoning)
    imgui.same_line()
    _, state.wrap = imgui.checkbox("wrap", state.wrap)
    imgui.same_line()
    _, state.follow_tail = imgui.checkbox("follow", state.follow_tail)
    imgui.same_line()
    imgui.set_next_item_width(200 * imgui.get_font_size() / 16.0)
    _, state.search = imgui.input_text_with_hint("##search", "filter", state.search)
    imgui.same_line()
    # Copies what is on screen, filters included -- the button sits next to the
    # filters that decide its contents. Right-clicking a run copies that run whole,
    # unfiltered; the two affordances are deliberately different units.
    if imgui.small_button("copy") and visible:
        imgui.set_clipboard_text(copy_text(visible))

    if node_id[1] is not None:
        # Sub-agent output does not stream (§2.5.1). Saying so beats letting a quiet
        # row be read as a stuck one.
        imgui.same_line()
        imgui.text_colored(P.warn.vec4, "sub-agent: complete messages only, not live")


def _visible(state: TranscriptState, cache: NodeTranscript) -> list[Line]:
    needle = state.search.lower()
    return [
        line
        for line in cache.lines
        if (state.show_reasoning or line.kind is not SegmentKind.REASONING)
        and (not needle or needle in line.text.lower())
    ]


def _draw_lines(state: TranscriptState, cache: NodeTranscript, lines: list[Line]) -> None:
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

    if state.wrap:
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


def _draw_context_menu(state: TranscriptState, cache: NodeTranscript, visible: list[Line]) -> None:
    if not imgui.begin_popup_context_window(_CONTEXT_ID, imgui.PopupFlags_.mouse_button_right):
        return

    if state.context_run is not None:
        start, end = cache.run_bounds(state.context_run)
        # The range can be empty if the selection moved between latching and opening.
        if end > start:
            label = cache.lines[start].kind.value.replace("_", " ")
            count = end - start
            plural = "" if count == 1 else "s"
            if imgui.menu_item_simple(f"copy {label} ({count} line{plural})"):
                imgui.set_clipboard_text(cache.run_text(state.context_run))

    if imgui.menu_item_simple("copy visible"):
        imgui.set_clipboard_text(copy_text(visible))
    imgui.end_popup()


def _handle_scroll(state: TranscriptState) -> None:
    """
    Follow the tail until the operator scrolls away from it.

    Scrolling up is an intent to read something, and yanking the view back to the
    bottom on the next token is the single most annoying thing a log pane can do.
    Re-enabled by the checkbox, or by scrolling back to the bottom.
    """
    hovered = imgui.is_window_hovered()
    wheel = imgui.get_io().mouse_wheel

    if state.follow_tail:
        # Disengage on an upward wheel rather than on scroll position. Position
        # cannot work while following: set_scroll_here_y pins it to the bottom every
        # frame, so the view snaps back before any position test could notice the
        # operator trying to leave.
        if hovered and wheel > 0.0:
            state.follow_tail = False
        else:
            imgui.set_scroll_here_y(1.0)
        return

    if imgui.get_scroll_max_y() > 0.0 and imgui.get_scroll_y() >= imgui.get_scroll_max_y() - 1.0:
        state.follow_tail = True
