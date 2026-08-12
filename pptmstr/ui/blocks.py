"""
Markdown block segmentation: lines in, blocks out.

Step 3 of planning/2026-08-10-transcript-markdown.md. No ImGui here, same reason
``NodeTranscript`` has none -- this is where the design gets subtle, so it needs to
be testable without a live frame.

::

    Transcript (bytes) --sync--> list[Line] --feed--> list[Block] + live Block
         consumed                stable/open_line        consumed_lines

``BlockCursor`` is not wired into ``NodeTranscript`` yet. There is no renderer to
consume it until step 5 (span layout/draw), and paying the parse cost every frame
for a structure nothing reads would be pure waste -- the pane still draws
line-by-line, unchanged. A cursor is meant to be owned per node, the same way a
``NodeTranscript`` is, once something does consume it.

Markdown parsing only applies to ``SegmentKind.OUTPUT`` lines -- see the planning
doc's opening section for why the other kinds must stay literal. A line of any
other kind becomes (or extends) a ``LITERAL`` block; the whole block layer is a
single state machine so a kind change is just one more transition it recognises,
which is what makes "a kind change force-closes the open block" a single code path
rather than a special case bolted on top.

Three rules drive the shape below, and each one costs something on purpose:

- **The list item is the modelled unit, not the list.** A blank line ends an item
  under this parser (unlike CommonMark, where a blank line only makes a list
  loose). Tight-vs-loose spacing is consequently unknowable -- not tracked,
  because it cannot be under streaming.
- **A segment-kind change force-closes the open block, including an open fence.**
  Skipping this would let one unbalanced fence in a model message style the rest
  of the transcript as code. ``Block.closed`` is how the renderer is told the fence
  never saw its terminator.
- **A table is recognised when a paragraph finalises, not while it accumulates.**
  GFM's delimiter row is always the paragraph's second line by the time a blank
  line (or anything else) ends it, so detection is a regex check made once, at
  finalisation -- never a reason to revise a block already handed to a caller.

Setext headings and link reference definitions are excluded permanently, not
deferred: both would let bytes arriving later change a block already finalised,
which breaks the one property every consumer gets to rely on -- see
``BlockCursor``'s docstring.

**Inline parsing (step 4) is attached in ``feed()``, not in ``_advance``.**
``_advance`` is the single state machine both ``feed()`` and ``live_block()``
share -- that unification is what step 3 promises. But inline parsing is a
separate, non-free pass (planning doc: ~1.7 ms extrapolated at 4 KB), and
``live_block()`` reruns ``_advance`` from scratch every frame while a block is
still streaming. Folding ``parse_inline`` into ``_advance`` would mean paying
that cost every frame for the live tail -- exactly the hazard the planning doc
calls out under "a very long live paragraph is the worst case". So ``.inline``
is populated only for blocks ``feed()`` actually commits, where it runs exactly
once; a block returned by ``live_block()`` always has ``inline=None``. Deciding
whether the live block is short enough to parse anyway is a rendering-policy
call the planning doc defers to step 5, not something this layer should guess at.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from ..transcript import SegmentKind
from .inline import InlineToken, parse_inline


class _LineLike(Protocol):
    """
    What the segmenter needs from a line -- structurally, not by import.

    ``transcript_pane.Line`` satisfies this without either module importing the
    other. Steps 4 and 5 are what will need both ``Block`` and ``Line`` in the same
    place; importing ``Line`` here now would just be a circular import waiting for
    that day to arrive.

    Declared as read-only properties, not plain attributes: a bare ``kind:
    SegmentKind`` member makes the protocol expect a *settable* attribute, which a
    frozen dataclass's fields are not -- mypy then rejects ``Line`` (frozen) as
    non-conforming even though every value it needs is right there.
    """

    @property
    def kind(self) -> SegmentKind: ...
    @property
    def text(self) -> str: ...


class BlockKind(enum.Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    FENCE = "fence"
    BULLET_ITEM = "bullet_item"
    ORDERED_ITEM = "ordered_item"
    BLOCKQUOTE = "blockquote"
    THEMATIC_BREAK = "thematic_break"
    TABLE = "table"
    # Anything that is not SegmentKind.OUTPUT. Verbatim, never markdown-parsed.
    LITERAL = "literal"


@dataclass(frozen=True, slots=True)
class Block:
    """
    A finalised (or provisionally-live) unit of block structure.

    ``lines`` is the verbatim source, not parsed content -- copy-as-markdown
    (planning doc, "Copy-block replaces text selection") depends on this being
    exactly what the model emitted.
    """

    kind: BlockKind
    # The originating SegmentKind: OUTPUT for every markdown block, or the literal
    # kind (TOOL_RESULT, ERROR, ...) it stayed loyal to for a LITERAL block.
    segment_kind: SegmentKind
    lines: tuple[str, ...]
    # False only for a FENCE that was cut off by a kind change or end of stream --
    # every other kind always closes cleanly by construction.
    closed: bool
    level: int = 0
    meta: tuple[tuple[str, str], ...] = ()
    # None for FENCE, THEMATIC_BREAK and LITERAL (never markdown-parsed) and for
    # TABLE (cells are split and parsed individually in step 5) -- and for any
    # block ``live_block()`` returns, regardless of kind. See the module
    # docstring for why the live tail never gets this populated.
    inline: tuple[InlineToken, ...] | None = None


# -- line-start classification ------------------------------------------------
#
# Order matters: a thematic break and a bullet item both match "- - -", and a
# thematic break wins per CommonMark's own precedence. Nothing here attempts
# nested lists or blockquote depth beyond 1 -- see the planning doc's "Open"
# section, "correct nesting semantics are not attempted".

_ATX_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+.*)?$")
_THEMATIC_RE = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([ \t]*\S*).*$")
_BULLET_RE = re.compile(r"^ {0,3}[-+*](?:[ \t]+.*)?$")
_ORDERED_RE = re.compile(r"^ {0,3}(\d{1,9})[.)](?:[ \t]+.*)?$")
_BLOCKQUOTE_RE = re.compile(r"^ {0,3}>[ \t]?.*$")
# A GFM delimiter row: one or more `---`/`:-:`-style cells joined by pipes. Applied
# only to a paragraph's second line, at finalisation -- see the module docstring.
_TABLE_DELIM_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)+\|?[ \t]*$")


@dataclass(frozen=True, slots=True)
class _StartInfo:
    """Whatever ``_classify_start`` learned beyond the kind itself."""

    level: int = 0
    meta: tuple[tuple[str, str], ...] = ()
    fence_char: str = ""
    fence_len: int = 0


def _classify_start(text: str) -> tuple[BlockKind, _StartInfo]:
    """What block a line would *start*, ignoring whatever is currently open."""
    if (m := _FENCE_RE.match(text)) is not None:
        run = m.group(1)
        lang = m.group(2).strip()
        info = _StartInfo(
            meta=(("lang", lang),) if lang else (),
            fence_char=run[0],
            fence_len=len(run),
        )
        return BlockKind.FENCE, info
    if _ATX_RE.match(text) is not None:
        stripped = text.lstrip(" ")
        level = len(stripped) - len(stripped.lstrip("#"))
        return BlockKind.HEADING, _StartInfo(level=level)
    if _THEMATIC_RE.match(text) is not None:
        return BlockKind.THEMATIC_BREAK, _StartInfo()
    if _BULLET_RE.match(text) is not None:
        return BlockKind.BULLET_ITEM, _StartInfo()
    if (m := _ORDERED_RE.match(text)) is not None:
        return BlockKind.ORDERED_ITEM, _StartInfo(meta=(("start", m.group(1)),))
    if _BLOCKQUOTE_RE.match(text) is not None:
        return BlockKind.BLOCKQUOTE, _StartInfo(level=1)
    return BlockKind.PARAGRAPH, _StartInfo()


def _fence_closes(text: str, char: str, length: int) -> bool:
    stripped = text.strip()
    return bool(stripped) and set(stripped) == {char} and len(stripped) >= length


# -- inline content extraction ---------------------------------------------------
#
# What markdown-it's inline parser should see is the *content*, not the block
# marker -- "### Title" inline-parsed as-is would render the literal hashes
# alongside heading styling. Each regex captures what is left after stripping the
# marker this block kind's own classify_start regex matched.

_ATX_STRIP_RE = re.compile(r"^ {0,3}#{1,6}[ \t]*(.*?)[ \t]*#*[ \t]*$")
_BULLET_STRIP_RE = re.compile(r"^ {0,3}[-+*][ \t]*(.*)$")
_ORDERED_STRIP_RE = re.compile(r"^ {0,3}\d{1,9}[.)][ \t]*(.*)$")
_BLOCKQUOTE_STRIP_RE = re.compile(r"^ {0,3}>[ \t]?(.*)$")


def _inline_content(kind: BlockKind, lines: tuple[str, ...]) -> str | None:
    """
    The substring of a block's verbatim source that inline parsing applies to, or
    None for a kind it does not apply to at all: FENCE and LITERAL are never
    markdown, THEMATIC_BREAK carries no text, and TABLE's cells are split and
    parsed individually in step 5, not as one inline run.
    """
    if kind is BlockKind.PARAGRAPH:
        return "\n".join(lines)
    if kind is BlockKind.HEADING:
        m = _ATX_STRIP_RE.match(lines[0])
        return m.group(1) if m else lines[0]
    if kind is BlockKind.BULLET_ITEM:
        m = _BULLET_STRIP_RE.match(lines[0])
        first = m.group(1) if m else lines[0]
        return "\n".join((first, *(line.strip() for line in lines[1:])))
    if kind is BlockKind.ORDERED_ITEM:
        m = _ORDERED_STRIP_RE.match(lines[0])
        first = m.group(1) if m else lines[0]
        return "\n".join((first, *(line.strip() for line in lines[1:])))
    if kind is BlockKind.BLOCKQUOTE:
        stripped = []
        for line in lines:
            m = _BLOCKQUOTE_STRIP_RE.match(line)
            stripped.append(m.group(1) if m else line)
        return "\n".join(stripped)
    return None


def _with_inline(block: Block) -> Block:
    """Attach the parsed inline stream, if this block kind gets one. See the
    module docstring for why this is called from ``feed()`` and nowhere else."""
    content = _inline_content(block.kind, block.lines)
    if content is None:
        return block
    return replace(block, inline=parse_inline(content))


def with_live_inline(block: Block, limit: int) -> Block:
    """
    The live-block counterpart to ``feed()``'s automatic attachment -- but opt-in,
    for the renderer to call explicitly once it has decided a still-streaming
    block is short enough to be worth a per-frame parse. ``live_block()`` itself
    never calls this; see the module docstring for why.

    A no-op (returns ``block`` unchanged) when this kind never gets inline content
    at all, or when the content is longer than ``limit`` -- the caller's size
    threshold, not a value this module has an opinion about.
    """
    content = _inline_content(block.kind, block.lines)
    if content is None or len(content) > limit:
        return block
    return replace(block, inline=parse_inline(content))


# -- the parser -----------------------------------------------------------------


@dataclass
class _ParseState:
    """Everything ``_advance`` needs, and nothing it doesn't -- cheap to clone."""

    active_kind: SegmentKind | None = None
    open_kind: BlockKind | None = None
    open_lines: list[str] = field(default_factory=list)
    open_level: int = 0
    open_meta: tuple[tuple[str, str], ...] = ()
    fence_char: str = ""
    fence_len: int = 0

    def clone(self) -> _ParseState:
        return _ParseState(
            self.active_kind,
            self.open_kind,
            list(self.open_lines),
            self.open_level,
            self.open_meta,
            self.fence_char,
            self.fence_len,
        )


def _finalize_state(state: _ParseState, closed: bool) -> Block:
    kind = state.open_kind
    assert kind is not None
    lines = tuple(state.open_lines)
    # Table recognition happens exactly here, once, per the module docstring --
    # never while the paragraph is still accumulating.
    if kind is BlockKind.PARAGRAPH and len(lines) >= 2 and _TABLE_DELIM_RE.match(lines[1]):
        kind = BlockKind.TABLE
    segment_kind = state.active_kind if kind is BlockKind.LITERAL else SegmentKind.OUTPUT
    assert segment_kind is not None
    return Block(kind, segment_kind, lines, closed, state.open_level, state.open_meta)


def _close_open(state: _ParseState, closed: bool = True) -> Block | None:
    if state.open_kind is None:
        return None
    block = _finalize_state(state, closed)
    state.open_kind = None
    state.open_lines = []
    state.open_level = 0
    state.open_meta = ()
    state.fence_char = ""
    state.fence_len = 0
    return block


def _advance(state: _ParseState, line: _LineLike) -> list[Block]:
    """Feed one line through the state machine, mutating ``state`` in place."""
    finalized: list[Block] = []

    if state.active_kind is not None and line.kind is not state.active_kind:
        # An open fence never saw its terminator -- say so honestly rather than
        # reporting it as a clean close. Every other open block kind has no
        # "terminator" of its own, so a kind change is an ordinary clean end for it.
        cleanly = state.open_kind is not BlockKind.FENCE
        closed = _close_open(state, closed=cleanly)
        if closed is not None:
            finalized.append(closed)
    state.active_kind = line.kind

    if line.kind is not SegmentKind.OUTPUT:
        if state.open_kind is BlockKind.LITERAL:
            state.open_lines.append(line.text)
        else:
            closed = _close_open(state)
            if closed is not None:
                finalized.append(closed)
            state.open_kind = BlockKind.LITERAL
            state.open_lines = [line.text]
        return finalized

    if state.open_kind is BlockKind.FENCE:
        state.open_lines.append(line.text)
        if _fence_closes(line.text, state.fence_char, state.fence_len):
            closed = _close_open(state, closed=True)
            if closed is not None:
                finalized.append(closed)
        return finalized

    if not line.text.strip():
        # A blank line ends whatever paragraph, item or blockquote is open. It
        # never resumes -- that is the "model the item, not the list" trade.
        closed = _close_open(state)
        if closed is not None:
            finalized.append(closed)
        return finalized

    start_kind, meta = _classify_start(line.text)

    if start_kind in (BlockKind.HEADING, BlockKind.THEMATIC_BREAK):
        # Single-line blocks, always. `para\n---` is therefore paragraph + rule,
        # not a setext h2 -- the excluded-permanently decision in the planning doc.
        closed = _close_open(state)
        if closed is not None:
            finalized.append(closed)
        finalized.append(
            Block(
                start_kind,
                SegmentKind.OUTPUT,
                (line.text,),
                True,
                meta.level,
                meta.meta,
            )
        )
        return finalized

    if start_kind is BlockKind.FENCE:
        closed = _close_open(state)
        if closed is not None:
            finalized.append(closed)
        state.open_kind = BlockKind.FENCE
        state.open_lines = [line.text]
        state.open_meta = meta.meta
        state.fence_char = meta.fence_char
        state.fence_len = meta.fence_len
        return finalized

    if start_kind in (BlockKind.BULLET_ITEM, BlockKind.ORDERED_ITEM):
        # A marker line always starts a *new* item, even if one of the same kind
        # is already open -- "- one\n- two" is two items, never one that grew.
        closed = _close_open(state)
        if closed is not None:
            finalized.append(closed)
        state.open_kind = start_kind
        state.open_lines = [line.text]
        state.open_meta = meta.meta
        return finalized

    if start_kind is BlockKind.PARAGRAPH and state.open_kind in (
        BlockKind.BULLET_ITEM,
        BlockKind.ORDERED_ITEM,
    ):
        # Unmarked text while an item is open is that item's continuation, not a
        # paragraph interrupting it.
        state.open_lines.append(line.text)
        return finalized

    if state.open_kind is start_kind:
        state.open_lines.append(line.text)
    else:
        closed = _close_open(state)
        if closed is not None:
            finalized.append(closed)
        state.open_kind = start_kind
        state.open_lines = [line.text]
        state.open_level = meta.level
        state.open_meta = meta.meta
    return finalized


class BlockCursor:
    """
    Incrementally parses one node's lines into blocks.

    The property every caller gets to rely on: **finalised blocks are never
    revised.** For any prefix of the input, ``cursor.blocks`` after that prefix is
    a prefix of ``cursor.blocks`` after the full input. That is what makes
    byte-at-a-time streaming safe to render through the same path as a one-shot
    parse, and it is the reason setext headings and link reference definitions are
    excluded rather than deferred -- both would violate it.

    Mirrors ``NodeTranscript``'s shape: ``blocks`` is append-only and
    ``consumed_lines`` only moves forward, for the same reason -- a line already
    folded into a finalised block is never revisited.
    """

    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.consumed_lines = 0
        self._state = _ParseState()

    def feed(self, lines: Sequence[_LineLike]) -> None:
        """
        Consume newly *stable* lines.

        Callers pass ``lines[:stable]`` where
        ``stable = len(cache.lines) - (1 if cache.open_line else 0)`` -- never the
        still-mutable last line while it can still change. That one is
        ``live_block``'s job.
        """
        for line in lines[self.consumed_lines :]:
            # Inline parsing runs here, once, on a block newly committed by this
            # call -- never inside _advance itself, which live_block() also uses
            # every frame. See the module docstring.
            for block in _advance(self._state, line):
                self.blocks.append(_with_inline(block))
        self.consumed_lines = len(lines)

    def finish(self) -> None:
        """
        Close whatever block is still open, because the input has ended.

        ``feed`` finalises a block only when the *next* line proves it complete, so
        a settled turn whose last line is an ordinary paragraph leaves that
        paragraph -- usually the question itself -- inside the parser rather than in
        ``blocks``. A streaming caller does not care, because it has ``live_block``
        for the tail; a one-shot caller parsing text that is already complete has no
        next line coming and no live tail to draw, and must say so.

        An unterminated fence closes as ``closed=False``, the same honesty
        ``_advance`` applies when a kind change cuts one off. Every other kind ends
        cleanly, having no terminator of its own to miss.

        Calling this asserts the input is complete. Feeding more lines afterwards is
        not an error but is a mistake: the continuation starts a fresh block, so
        what should have been one paragraph arrives as two.
        """
        cleanly = self._state.open_kind is not BlockKind.FENCE
        block = _close_open(self._state, closed=cleanly)
        if block is not None:
            self.blocks.append(_with_inline(block))

    def live_block(self, lines: Sequence[_LineLike]) -> Block | None:
        """
        The in-progress block, re-derived from scratch every call.

        ``lines`` is the *full* line list, mutable last entry included. Running it
        through a throwaway clone of the parser state -- the same ``_advance`` used
        for every finalised block -- is what keeps live and final rendering from
        disagreeing: there is one parser, not a streaming one and a batch one.
        Nothing here is allowed to touch ``self._state`` or ``self.blocks``, since
        the trailing line can still change shape on the next frame (a blank line
        mid-token-stream might not stay blank).
        """
        state = self._state.clone()
        tail: list[Block] = []
        for line in lines[self.consumed_lines :]:
            tail.extend(_advance(state, line))
        if state.open_kind is not None:
            return _finalize_state(state, closed=False)
        if tail:
            return replace(tail[-1], closed=False)
        return None
