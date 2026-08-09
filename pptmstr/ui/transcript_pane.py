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
"""

from __future__ import annotations

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
                    self.lines[-1] = Line(last.kind, last.text + piece)
                else:
                    self.lines.append(Line(segment.kind, piece))

            self.open_line = not ends_with_newline
        self.consumed = limit


@dataclass
class TranscriptState:
    """Presentation state for the pane. Never enters the store (design §6)."""

    caches: dict[NodeId, NodeTranscript] = field(default_factory=dict)
    show_reasoning: bool = True
    wrap: bool = False
    follow_tail: bool = True
    search: str = ""
    frame: int = 0

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

    _draw_controls(state, record.node_id, cache)
    imgui.separator()
    _draw_lines(state, cache)


def _draw_controls(state: TranscriptState, node_id: NodeId, cache: NodeTranscript) -> None:
    changed, state.show_reasoning = imgui.checkbox("reasoning", state.show_reasoning)
    imgui.same_line()
    _, state.wrap = imgui.checkbox("wrap", state.wrap)
    imgui.same_line()
    _, state.follow_tail = imgui.checkbox("follow", state.follow_tail)
    imgui.same_line()
    imgui.set_next_item_width(200 * imgui.get_font_size() / 16.0)
    _, state.search = imgui.input_text_with_hint("##search", "filter", state.search)

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


def _draw_lines(state: TranscriptState, cache: NodeTranscript) -> None:
    lines = _visible(state, cache)
    if not lines:
        imgui.text_disabled("(nothing yet)" if not cache.lines else "(no matching lines)")
        return

    if not imgui.begin_child("##transcript"):
        imgui.end_child()
        return

    if state.wrap:
        # No clipper: wrapped rows have no uniform height for it to work from. The
        # window bound is what keeps that affordable.
        for line in lines[-_WRAP_WINDOW:]:
            imgui.push_style_color(imgui.Col_.text, getattr(P, _KIND_COLOUR[line.kind]).vec4)
            imgui.text_wrapped(line.text or " ")
            imgui.pop_style_color()
    else:
        clipper = imgui.ListClipper()
        clipper.begin(len(lines))
        while clipper.step():
            for index in range(clipper.display_start, clipper.display_end):
                line = lines[index]
                imgui.text_colored(getattr(P, _KIND_COLOUR[line.kind]).vec4, line.text or " ")
        clipper.end()

    _handle_scroll(state)
    imgui.end_child()


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
