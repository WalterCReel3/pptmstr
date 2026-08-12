"""
Append-only transcript buffer (I7).

Written by the asyncio thread as tokens stream in, read by the UI thread while it
builds a frame. Append-only is the whole trick: the immutable-record copy-on-write
that works everywhere else in the store degrades to O(n^2) on a token stream,
because every delta would rebuild the entire text.

Readers take ``(transcript, length)`` at snapshot time and only ever look below
that length, so a frame renders a consistent prefix even while the writer keeps
appending past it.

**Deviation from the design's "lock-free":** reads take a short lock rather than
relying on GIL atomicity of ``bytearray`` slicing. The cost is bounded -- the lock
is held for a copy of the *visible window*, a few KB, not the whole transcript --
and it does not bet on an implementation detail that free-threaded builds are
actively removing. The property that mattered (append-only, so no O(n^2) rebuild)
is unaffected.
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass


class SegmentKind(enum.Enum):
    REASONING = "reasoning"
    OUTPUT = "output"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    SYSTEM = "system"
    # Emitted where the PreCompact hook fired. Marks the point at which the session
    # discarded reasoning, so the UI can show that later output came from an agent
    # that had already lost its history (§2.4).
    COMPACTION = "compaction"


@dataclass(frozen=True, slots=True)
class Segment:
    """A styled run of bytes. Offsets index into the transcript buffer."""

    kind: SegmentKind
    start: int
    end: int
    # Free-form: tool name, block index, message id -- whatever the writer needs to
    # key styling or a jump target off.
    meta: tuple[tuple[str, str], ...] = ()

    @property
    def length(self) -> int:
        return self.end - self.start


class Transcript:
    """
    One agent's output stream.

    Single writer (the asyncio task feeding this node), many readers (the UI). The
    writer opens a segment, appends into it, and closes it; ``published_length``
    advances only after the bytes have landed, so a reader can never address a
    region that is not yet written.
    """

    __slots__ = ("_lock", "_buf", "_segments", "_published_len", "_open_kind", "_open_start")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buf = bytearray()
        self._segments: list[Segment] = []
        self._published_len = 0
        self._open_kind: SegmentKind | None = None
        self._open_start = 0

    # -- writer side (asyncio thread) -------------------------------------------

    def append(self, kind: SegmentKind, text: str, meta: tuple[tuple[str, str], ...] = ()) -> None:
        """
        Append text, extending the currently open segment when the kind matches.

        Coalescing on kind is what keeps the segment list proportional to the number
        of *blocks* rather than to the number of streamed deltas -- a token stream
        would otherwise produce one segment per token and make every read a walk over
        thousands of entries.
        """
        data = text.encode("utf-8")
        with self._lock:
            if self._open_kind is kind:
                self._buf.extend(data)
                last = self._segments[-1]
                self._segments[-1] = Segment(kind, last.start, len(self._buf), last.meta)
            else:
                self._close_open_locked()
                self._open_kind = kind
                self._open_start = len(self._buf)
                self._buf.extend(data)
                self._segments.append(Segment(kind, self._open_start, len(self._buf), meta))
            # Published last, after the bytes are in: a reader that sees this length
            # is guaranteed the bytes below it exist.
            self._published_len = len(self._buf)

    def close_segment(self) -> None:
        """Close the open segment so the next append starts a new one."""
        with self._lock:
            self._close_open_locked()

    def _close_open_locked(self) -> None:
        self._open_kind = None
        self._open_start = len(self._buf)

    # -- reader side (UI thread) -------------------------------------------------

    @property
    def published_length(self) -> int:
        """
        Bytes safe to read. O(1); this is the value a frame pins at snapshot time.
        """
        return self._published_len

    def read(self, start: int, end: int) -> str:
        """
        Decode the byte range [start, end).

        Clamped to the published length, so passing a stale end from an earlier frame
        is safe rather than an error. Decoding is error-tolerant because a range can
        land mid-codepoint: the visible window is chosen by byte offset, and a
        multi-byte character straddling the boundary must not take down the frame.
        """
        with self._lock:
            end = min(end, self._published_len)
            start = max(0, min(start, end))
            chunk = bytes(self._buf[start:end])
        return chunk.decode("utf-8", errors="replace")

    def segments(self, limit: int | None = None) -> tuple[Segment, ...]:
        """Segments clipped to ``limit`` bytes (defaults to the published length)."""
        with self._lock:
            cap = self._published_len if limit is None else min(limit, self._published_len)
            out = []
            for seg in self._segments:
                if seg.start >= cap:
                    break
                out.append(seg if seg.end <= cap else Segment(seg.kind, seg.start, cap, seg.meta))
        return tuple(out)

    def text(self, limit: int | None = None) -> str:
        """Whole transcript up to ``limit``. For tests and export, not per-frame use."""
        cap = self._published_len if limit is None else limit
        return self.read(0, cap)

    def turn_prose(self) -> str:
        """
        What the agent has *said* this turn, with the machinery left out.

        ``tail`` is a byte window over every kind, so a turn whose prose sits
        behind a large tool result renders the tool result instead -- under a
        heading promising the agent's own words. This returns ``OUTPUT`` only, and
        only from the current turn.

        **The turn boundary is already in the stream.** ``SegmentKind.SYSTEM`` is
        appended by ``AgentSession.send`` and by nothing else, and every root turn
        goes through ``send`` -- the opening task included. So the last ``SYSTEM``
        segment is where this turn began, and no separate marker is needed. A
        sub-agent is spawned by a tool call rather than by ``send`` and so has no
        marker at all; its whole life is one turn, which is what makes the start of
        the buffer the right fallback rather than an error.

        Unbounded, unlike ``tail``, and safe on the frame path for a different
        reason: the cost is one turn's prose, which does not grow with the length
        of the session. Callers bound it themselves so the bound can announce
        itself -- a surface whose premise is "this is what it said" must not
        quietly show less than that.
        """
        with self._lock:
            cap = self._published_len
            start = 0
            for segment in reversed(self._segments):
                if segment.kind is SegmentKind.SYSTEM and segment.end <= cap:
                    start = segment.end
                    break
            chunks = [
                bytes(self._buf[max(segment.start, start) : min(segment.end, cap)])
                for segment in self._segments
                if segment.kind is SegmentKind.OUTPUT and segment.end > start
            ]
        # Decoded as one buffer rather than per segment: coalescing means a
        # multi-byte character cannot straddle two OUTPUT segments, but it can
        # straddle the published cap, and joining first keeps that the only place
        # the replacement char can appear.
        return b"".join(chunks).decode("utf-8", errors="replace")

    def tail(self, nbytes: int) -> str:
        """
        The last ``nbytes`` of published output.

        Safe on the frame path in a way ``text()`` is not: the cost is the size of
        the window asked for, not the size of the transcript, so a pane showing the
        end of a long-running session's output does not get more expensive as that
        session talks. Decoding tolerates a split codepoint at the start, since the
        cut is by byte offset.
        """
        end = self._published_len
        return self.read(max(0, end - nbytes), end)
