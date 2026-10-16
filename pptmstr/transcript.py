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

**Tool calls and results are also held as entries** (``ToolEntry``), beside the
segments rather than in a sibling structure the session owns. The entry's spans are
byte offsets into this buffer, so it belongs to the buffer it indexes: it has the
buffer's lifetime (the node record carries the one object), the same writer, and
the same lock, so a reader can never see an entry whose line is not yet published
or a line whose entry is missing. A sibling would need a second handoff to the
store and a second agreement about which object each side holds -- the defect
``Translator.subagent_transcripts`` already exists to prevent once.

The line a pane draws for a call or a result is *derived* from the entry
(``tool_call_line``, ``tool_result_line``) rather than written beside it, so the two
cannot disagree, and it is the same line the driver has always written.

**An entry is verbatim; the line is drawable.** ``ToolEntry.input`` and
``ToolResult.output`` hold the text as the SDK delivered it, lone surrogates and
NULs included (STYLE.md section 3). Two reasons, either sufficient: the line is
derived from the entry and must come out byte-identical to the line derived from
the message, which a drawable entry would not -- ``drawable.text`` lengthens a
string, so the 60- and 300-character cuts would land elsewhere; and an output
viewer that copies or saves what a tool returned must hand over what it returned,
not ``\\u0000``. So a renderer passes ``input`` values and ``output`` through
``drawable.text`` (or a helper built on it) at the point it draws, and caches the
result -- the entry is not re-escaped per frame. ``name`` and ``tool_use_id`` are
verbatim for the same reason and are drawn the same way; the segment's ``meta``
keeps its drawable copy of both, as before.
"""

from __future__ import annotations

import bisect
import enum
import threading
import types
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from . import drawable


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


# Per result, in UTF-8 bytes. What a tool returned beyond this is dropped where the
# entry is made and the count kept, so a renderer can say how much it is not showing.
OUTPUT_CAP = 1 << 20


@dataclass(frozen=True, slots=True)
class ToolResult:
    """
    What a tool returned. Verbatim (module docstring), capped at ``OUTPUT_CAP``.

    ``output`` is the result's text when the SDK gave a string, and the ``repr`` of
    whatever else it gave (a list of content blocks, ``None``) -- the same text the
    result line has always been cut from.
    """

    output: str
    # UTF-8 bytes of the output that are not in ``output``. 0 when it is whole.
    dropped_bytes: int
    is_error: bool
    # The result line's byte span in the transcript buffer.
    start: int
    end: int

    @property
    def truncated(self) -> bool:
        return self.dropped_bytes > 0


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """
    One tool call and, once it arrives, its result, joined by ``tool_use_id``.

    ``input`` is ``None`` when the result arrived in a transcript that never saw its
    call -- a resumed session, or a call written to another node's buffer -- and
    ``call`` is then ``None`` too. The result is still kept: the line was drawn, so
    what it was cut from is worth having.
    """

    tool_use_id: str
    name: str
    input: Mapping[str, Any] | None
    # The call line's byte span in the transcript buffer.
    call: tuple[int, int] | None
    result: ToolResult | None = None


def _compact_args(args: Mapping[str, Any], limit: int = 160) -> str:
    parts = []
    for key, value in args.items():
        text = value if isinstance(value, str) else repr(value)
        if len(text) > 60:
            text = text[:57] + "..."
        parts.append(f"{key}={text}")
    joined = ", ".join(parts)
    return joined if len(joined) <= limit else joined[: limit - 3] + "..."


def _compact_result(content: object, limit: int = 300) -> str:
    text = content if isinstance(content, str) else repr(content)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def tool_call_line(name: str, args: Mapping[str, Any]) -> str:
    """The line a TOOL_CALL segment holds for one call, before it is made drawable."""
    return f"\n{name}({_compact_args(args)})\n"


def tool_result_line(name: str, output: str) -> str:
    """The line a TOOL_RESULT or ERROR segment holds for one result."""
    return f"{name} -> {_compact_result(output)}\n"


def cap_output(content: object, cap: int = OUTPUT_CAP) -> tuple[str, int]:
    """
    ``content`` as text, cut to at most ``cap`` UTF-8 bytes on a character boundary.

    Returns the kept text and how many bytes were dropped. A lone surrogate counts
    as the three bytes ``surrogatepass`` gives it, so a result made of them is
    bounded too rather than failing to measure.
    """
    text = content if isinstance(content, str) else repr(content)
    # A str of n code points is at most 4n bytes, so most results need no encode.
    if len(text) * 4 <= cap:
        return text, 0
    data = text.encode("utf-8", "surrogatepass")
    if len(data) <= cap:
        return text, 0
    cut = cap
    # Back off continuation bytes so the cut lands at the start of a character.
    while cut > 0 and data[cut] & 0xC0 == 0x80:
        cut -= 1
    return data[:cut].decode("utf-8", "surrogatepass"), len(data) - cut


def _encoded(text: str) -> bytes:
    # A lone surrogate has no UTF-8, and a NUL ends the string at whichever text
    # call is handed it: both are written out before the bytes are made.
    return drawable.text(text).encode("utf-8")


class Transcript:
    """
    One agent's output stream.

    Single writer (the asyncio task feeding this node), many readers (the UI). The
    writer opens a segment, appends into it, and closes it; ``published_length``
    advances only after the bytes have landed, so a reader can never address a
    region that is not yet written.
    """

    __slots__ = (
        "_lock",
        "_buf",
        "_segments",
        "_published_len",
        "_open_kind",
        "_open_start",
        "_entries",
        "_latest",
        "_tool_spans",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buf = bytearray()
        self._segments: list[Segment] = []
        self._published_len = 0
        self._open_kind: SegmentKind | None = None
        self._open_start = 0
        # Every entry, in the order its call (or orphan result) was written. An
        # entry is replaced in place when its result lands.
        self._entries: list[ToolEntry] = []
        # tool_use_id -> index in _entries of the latest entry under that id.
        self._latest: dict[str, int] = {}
        # (start offset, index in _entries) for every call and result line, in
        # buffer order -- appended monotonically, so a span query is a bisect.
        self._tool_spans: list[tuple[int, int]] = []

    # -- writer side (asyncio thread) -------------------------------------------

    def append(self, kind: SegmentKind, text: str, meta: tuple[tuple[str, str], ...] = ()) -> None:
        """
        Append text, extending the currently open segment when the kind matches.

        Coalescing on kind is what keeps the segment list proportional to the number
        of *blocks* rather than to the number of streamed deltas -- a token stream
        would otherwise produce one segment per token and make every read a walk over
        thousands of entries.
        """
        data = _encoded(text)
        with self._lock:
            self._append_locked(kind, data, meta)

    def _append_locked(
        self, kind: SegmentKind, data: bytes, meta: tuple[tuple[str, str], ...]
    ) -> tuple[int, int]:
        """``append`` under a lock the caller holds; returns the bytes' span."""
        start = len(self._buf)
        if self._open_kind is kind:
            self._buf.extend(data)
            last = self._segments[-1]
            self._segments[-1] = Segment(kind, last.start, len(self._buf), last.meta)
        else:
            self._close_open_locked()
            self._open_kind = kind
            self._open_start = len(self._buf)
            self._buf.extend(data)
            self._segments.append(
                Segment(kind, self._open_start, len(self._buf), drawable.shown(meta))
            )
        # Published last, after the bytes are in: a reader that sees this length
        # is guaranteed the bytes below it exist.
        self._published_len = len(self._buf)
        return start, len(self._buf)

    def append_tool_call(self, tool_use_id: str, name: str, args: Mapping[str, Any]) -> None:
        """
        Record a tool call and append its line as a TOOL_CALL segment.

        The entry and the line land under one lock acquisition, so no reader sees
        either without the other. A second call under an id already held is a new
        entry: ``tool_entry`` and a later result answer to the newer one, and the
        earlier line still finds the earlier call through ``tool_entries``.
        """
        held = types.MappingProxyType(dict(args))
        data = _encoded(tool_call_line(name, held))
        meta = (("tool", name), ("tool_use_id", tool_use_id))
        with self._lock:
            span = self._append_locked(SegmentKind.TOOL_CALL, data, meta)
            index = len(self._entries)
            self._entries.append(ToolEntry(tool_use_id, name, held, span))
            self._latest[tool_use_id] = index
            self._tool_spans.append((span[0], index))

    def append_tool_result(
        self, tool_use_id: str, name: str, content: object, *, is_error: bool
    ) -> None:
        """
        Record a tool's result on its entry and append its line.

        ``name`` is the writer's, not looked up here, because the line has always
        named what the writer knew -- ``tool`` for an id it never saw called -- and
        a call held in this buffer is not the only source of that.
        """
        output, dropped = cap_output(content)
        data = _encoded(tool_result_line(name, output))
        kind = SegmentKind.ERROR if is_error else SegmentKind.TOOL_RESULT
        with self._lock:
            start, end = self._append_locked(kind, data, ())
            result = ToolResult(output, dropped, is_error, start, end)
            index = self._latest.get(tool_use_id)
            if index is None:
                index = len(self._entries)
                self._entries.append(ToolEntry(tool_use_id, name, None, None, result))
                self._latest[tool_use_id] = index
            else:
                self._entries[index] = replace(self._entries[index], result=result)
            self._tool_spans.append((start, index))

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

    def tool_entry(self, tool_use_id: str) -> ToolEntry | None:
        """The entry for one call, with its result if that has arrived."""
        with self._lock:
            index = self._latest.get(tool_use_id)
            return None if index is None else self._entries[index]

    def tool_entries(self, start: int, end: int) -> tuple[ToolEntry, ...]:
        """
        Entries with a call or result line starting in the byte range [start, end).

        What a renderer calls with a TOOL_CALL, TOOL_RESULT or ERROR segment's
        ``start`` and ``end``: one segment can hold several calls, since adjacent
        lines of a kind coalesce. Each entry appears once, in buffer order, even
        when both its lines fall in the range. Clamped to the published length.
        O(log n + k).
        """
        with self._lock:
            end = min(end, self._published_len)
            i = bisect.bisect_left(self._tool_spans, (start, -1))
            seen: dict[int, ToolEntry] = {}
            while i < len(self._tool_spans) and self._tool_spans[i][0] < end:
                index = self._tool_spans[i][1]
                if index not in seen:
                    seen[index] = self._entries[index]
                i += 1
        return tuple(seen.values())

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
