"""
Block segmentation: markdown structure over the line cache.

No ImGui, no live frame -- everything here is plain data, per the module
docstring in pptmstr/ui/blocks.py.
"""

from __future__ import annotations

from pptmstr.transcript import SegmentKind, Transcript
from pptmstr.ui.blocks import Block, BlockCursor, BlockKind
from pptmstr.ui.inline import InlineToken
from pptmstr.ui.transcript_pane import Line, NodeTranscript


def feed_lines(*pairs: tuple[SegmentKind, str]) -> BlockCursor:
    """Build a cursor from (kind, text) pairs, run index unused by the segmenter."""
    lines = [Line(kind, text, 0) for kind, text in pairs]
    cursor = BlockCursor()
    cursor.feed(lines)
    return cursor


def kinds(cursor: BlockCursor) -> list[BlockKind]:
    return [b.kind for b in cursor.blocks]


# -- one block kind at a time ---------------------------------------------------


def test_paragraph_single_and_multi_line() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "one"),
        (SegmentKind.OUTPUT, "two"),
        (SegmentKind.OUTPUT, ""),
        (SegmentKind.OUTPUT, "three"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.PARAGRAPH, BlockKind.PARAGRAPH]
    assert cursor.blocks[0].lines == ("one", "two")
    assert cursor.blocks[1].lines == ("three",)
    assert all(b.closed for b in cursor.blocks)


def test_atx_heading_level() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "### Title"),
        (SegmentKind.OUTPUT, "body"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.HEADING, BlockKind.PARAGRAPH]
    assert cursor.blocks[0].level == 3
    assert cursor.blocks[0].lines == ("### Title",)


def test_atx_heading_requires_space_or_end() -> None:
    """ "#foo" is not a heading in CommonMark -- no space after the hashes."""
    cursor = feed_lines((SegmentKind.OUTPUT, "#foo"), (SegmentKind.OUTPUT, ""))
    assert kinds(cursor) == [BlockKind.PARAGRAPH]


def test_fenced_code_closes_properly() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "```py"),
        (SegmentKind.OUTPUT, "code"),
        (SegmentKind.OUTPUT, "```"),
        (SegmentKind.OUTPUT, "after"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.FENCE, BlockKind.PARAGRAPH]
    fence = cursor.blocks[0]
    assert fence.lines == ("```py", "code", "```")
    assert fence.closed is True
    assert fence.meta == (("lang", "py"),)


def test_unbalanced_fence_is_force_closed_by_kind_change() -> None:
    """
    The catastrophic case the design calls out: without this, one unterminated
    fence in a model message would style the rest of the transcript as code.
    """
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "```py"),
        (SegmentKind.OUTPUT, "code"),
        (SegmentKind.TOOL_RESULT, "some result"),
        # A further kind change to force the TOOL_RESULT literal closed too --
        # feed() only closes on a boundary, never at end-of-input on its own.
        (SegmentKind.SYSTEM, "sentinel"),
    )
    assert kinds(cursor) == [BlockKind.FENCE, BlockKind.LITERAL]
    fence = cursor.blocks[0]
    assert fence.lines == ("```py", "code")
    assert fence.closed is False
    literal = cursor.blocks[1]
    assert literal.lines == ("some result",)
    assert literal.segment_kind is SegmentKind.TOOL_RESULT


def test_thematic_break() -> None:
    cursor = feed_lines((SegmentKind.OUTPUT, "---"), (SegmentKind.OUTPUT, ""))
    assert kinds(cursor) == [BlockKind.THEMATIC_BREAK]


def test_setext_like_sequence_is_paragraph_then_rule() -> None:
    """
    Excluded permanently, not deferred: a setext heading would let a later byte
    change an earlier block's kind, which breaks the streaming-prefix property.
    """
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "para"),
        (SegmentKind.OUTPUT, "---"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.PARAGRAPH, BlockKind.THEMATIC_BREAK]
    assert cursor.blocks[0].lines == ("para",)


def test_bullet_items_are_separate_blocks() -> None:
    """A marker line always starts a new item -- it never grows an existing one."""
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "- one"),
        (SegmentKind.OUTPUT, "- two"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.BULLET_ITEM, BlockKind.BULLET_ITEM]
    assert cursor.blocks[0].lines == ("- one",)
    assert cursor.blocks[1].lines == ("- two",)


def test_bullet_item_continuation_text_stays_in_the_item() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "- one"),
        (SegmentKind.OUTPUT, "  more of one"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.BULLET_ITEM]
    assert cursor.blocks[0].lines == ("- one", "  more of one")


def test_ordered_items_carry_their_start_number() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "1. one"),
        (SegmentKind.OUTPUT, "2. two"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.ORDERED_ITEM, BlockKind.ORDERED_ITEM]
    assert cursor.blocks[0].meta == (("start", "1"),)
    assert cursor.blocks[1].meta == (("start", "2"),)


def test_blockquote_is_one_block_across_lines() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "> one"),
        (SegmentKind.OUTPUT, "> two"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.BLOCKQUOTE]
    assert cursor.blocks[0].lines == ("> one", "> two")
    assert cursor.blocks[0].level == 1


def test_table_detected_on_finalize() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "a|b"),
        (SegmentKind.OUTPUT, "-|-"),
        (SegmentKind.OUTPUT, "1|2"),
        (SegmentKind.OUTPUT, ""),
        (SegmentKind.OUTPUT, "after"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.TABLE, BlockKind.PARAGRAPH]
    assert cursor.blocks[0].lines == ("a|b", "-|-", "1|2")


def test_two_line_paragraph_without_delimiter_row_stays_a_paragraph() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "a|b"),
        (SegmentKind.OUTPUT, "not a delimiter row"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.PARAGRAPH]


# -- inline parsing wiring (step 4) -----------------------------------------------
#
# The parser mechanics (emphasis override, code spans, links) belong to
# test_inline.py. What belongs here is that feed() attaches the right *content* --
# marker stripped -- to the right block kinds, and that live_block() never does.


def test_paragraph_inline_is_the_joined_lines() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "one **two**"),
        (SegmentKind.OUTPUT, "three"),
        (SegmentKind.OUTPUT, ""),
    )
    block = cursor.blocks[0]
    assert block.inline == (
        InlineToken("text", "one "),
        InlineToken("strong_open"),
        InlineToken("text", "two"),
        InlineToken("strong_close"),
        InlineToken("text", ""),
        # The newline joining the two source lines becomes a softbreak token, not
        # literal "\n" folded into surrounding text -- markdown-it's own inline
        # model, not something this layer invents.
        InlineToken("softbreak"),
        InlineToken("text", "three"),
    )


def test_heading_inline_has_the_hashes_stripped() -> None:
    cursor = feed_lines((SegmentKind.OUTPUT, "## Title"), (SegmentKind.OUTPUT, ""))
    assert cursor.blocks[0].inline == (InlineToken("text", "Title"),)


def test_bullet_item_inline_has_the_marker_stripped() -> None:
    cursor = feed_lines((SegmentKind.OUTPUT, "- one"), (SegmentKind.OUTPUT, ""))
    assert cursor.blocks[0].inline == (InlineToken("text", "one"),)


def test_ordered_item_inline_has_the_marker_stripped() -> None:
    cursor = feed_lines((SegmentKind.OUTPUT, "1. one"), (SegmentKind.OUTPUT, ""))
    assert cursor.blocks[0].inline == (InlineToken("text", "one"),)


def test_blockquote_inline_has_the_angle_bracket_stripped() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "> one"),
        (SegmentKind.OUTPUT, "> two"),
        (SegmentKind.OUTPUT, ""),
    )
    assert cursor.blocks[0].inline == (
        InlineToken("text", "one"),
        InlineToken("softbreak"),
        InlineToken("text", "two"),
    )


def test_fence_thematic_break_and_literal_are_never_inline_parsed() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "```"),
        (SegmentKind.OUTPUT, "__init__ *not* emphasis, it is code"),
        (SegmentKind.OUTPUT, "```"),
        (SegmentKind.OUTPUT, "---"),
        (SegmentKind.TOOL_RESULT, "__also__ literal"),
        (SegmentKind.SYSTEM, "sentinel"),  # forces the trailing LITERAL closed
    )
    kinds_found = {b.kind: b for b in cursor.blocks}
    assert kinds_found[BlockKind.FENCE].inline is None
    assert kinds_found[BlockKind.THEMATIC_BREAK].inline is None
    assert kinds_found[BlockKind.LITERAL].inline is None


def test_table_is_not_inline_parsed_here() -> None:
    """Cell splitting -- and therefore per-cell inline parsing -- is step 5's job."""
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "a|b"),
        (SegmentKind.OUTPUT, "-|-"),
        (SegmentKind.OUTPUT, "1|2"),
        (SegmentKind.OUTPUT, ""),
    )
    assert kinds(cursor) == [BlockKind.TABLE]
    assert cursor.blocks[0].inline is None


def test_live_block_is_never_inline_parsed() -> None:
    """The perf-sensitive path: live_block() reruns every frame, so attaching
    inline here would parse the same growing paragraph every frame it streams."""
    lines = [Line(SegmentKind.OUTPUT, "**bold** so far", 0)]
    cursor = BlockCursor()
    live = cursor.live_block(lines)
    assert live is not None
    assert live.kind is BlockKind.PARAGRAPH
    assert live.inline is None


# -- kind boundaries --------------------------------------------------------------


def test_non_output_lines_become_literal_blocks() -> None:
    cursor = feed_lines(
        (SegmentKind.TOOL_CALL, "call one"),
        (SegmentKind.TOOL_CALL, "call two"),
        # Force the trailing run closed -- see the fence test's comment.
        (SegmentKind.SYSTEM, "sentinel"),
    )
    assert kinds(cursor) == [BlockKind.LITERAL]
    assert cursor.blocks[0].lines == ("call one", "call two")
    assert cursor.blocks[0].segment_kind is SegmentKind.TOOL_CALL


def test_kind_change_force_closes_open_block() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "still writing"),
        (SegmentKind.ERROR, "boom"),
        (SegmentKind.SYSTEM, "sentinel"),
    )
    assert kinds(cursor) == [BlockKind.PARAGRAPH, BlockKind.LITERAL]
    assert cursor.blocks[0].closed is True
    assert cursor.blocks[0].lines == ("still writing",)
    assert cursor.blocks[1].lines == ("boom",)
    assert cursor.blocks[1].segment_kind is SegmentKind.ERROR


def test_literal_round_trip_when_no_output_segments() -> None:
    """The existing (pre-markdown) path, untouched: every block is LITERAL and the
    lines survive unchanged."""
    lines_in = ["one", "two", "three"]
    pairs = [(SegmentKind.SYSTEM, text) for text in lines_in] + [(SegmentKind.OUTPUT, "")]
    cursor = feed_lines(*pairs)
    assert kinds(cursor) == [BlockKind.LITERAL]
    assert list(cursor.blocks[0].lines) == lines_in


# -- live block -------------------------------------------------------------------


def test_live_block_reflects_an_uncommitted_line() -> None:
    lines = [Line(SegmentKind.OUTPUT, "one", 0), Line(SegmentKind.OUTPUT, "two", 0)]
    cursor = BlockCursor()
    cursor.feed(lines[:1])
    live = cursor.live_block(lines)
    assert live is not None
    assert live.kind is BlockKind.PARAGRAPH
    assert live.lines == ("one", "two")
    assert live.closed is False
    # live_block must not have committed anything.
    assert cursor.blocks == []
    assert cursor.consumed_lines == 1


def test_live_block_is_none_with_nothing_open() -> None:
    lines = [Line(SegmentKind.OUTPUT, "one", 0), Line(SegmentKind.OUTPUT, "", 0)]
    cursor = BlockCursor()
    cursor.feed(lines)
    assert cursor.live_block(lines) is None


def test_live_fence_shows_open_and_unclosed() -> None:
    lines = [Line(SegmentKind.OUTPUT, "```py", 0), Line(SegmentKind.OUTPUT, "code so far", 0)]
    cursor = BlockCursor()
    cursor.feed(lines[:1])
    live = cursor.live_block(lines)
    assert live is not None
    assert live.kind is BlockKind.FENCE
    assert live.closed is False


# -- the property the whole design rests on ----------------------------------------


_DOC = (
    "# Title\n"
    "\n"
    "para one\n"
    "still para one\n"
    "\n"
    "- item one\n"
    "- item two\n"
    "more item two text\n"
    "\n"
    "```py\n"
    "code line\n"
    "```\n"
    "\n"
    "a|b\n"
    "-|-\n"
    "1|2\n"
    "\n"
    "> quoted line\n"
    "\n"
    "---\n"
    "\n"
    "trailing paragraph\n"
    "\n"
)


def _blocks_for(text: str) -> list[Block]:
    t = Transcript()
    t.append(SegmentKind.OUTPUT, text)
    cache = NodeTranscript()
    cache.sync(t, t.published_length)
    stable = len(cache.lines) - (1 if cache.open_line else 0)
    cursor = BlockCursor()
    cursor.feed(cache.lines[:stable])
    return cursor.blocks


def test_one_shot_parse_covers_every_kind() -> None:
    """Sanity check on the fixture itself: it exercises every block kind once."""
    found = {b.kind for b in _blocks_for(_DOC)}
    assert found == {
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.BULLET_ITEM,
        BlockKind.FENCE,
        BlockKind.TABLE,
        BlockKind.BLOCKQUOTE,
        BlockKind.THEMATIC_BREAK,
    }


def test_streaming_prefix_matches_one_shot() -> None:
    """
    The property the block layer rests on: for every prefix of the document,
    ``cursor.blocks`` is a prefix of the one-shot parse of the whole thing. This is
    what makes byte-at-a-time streaming safe to render through the same path as a
    finished transcript, and it doubles as the enforcement mechanism for excluding
    setext headings and link reference definitions.
    """
    final = _blocks_for(_DOC)

    t = Transcript()
    cache = NodeTranscript()
    cursor = BlockCursor()
    for ch in _DOC:
        t.append(SegmentKind.OUTPUT, ch)
        cache.sync(t, t.published_length)
        stable = len(cache.lines) - (1 if cache.open_line else 0)
        cursor.feed(cache.lines[:stable])
        prefix = final[: len(cursor.blocks)]
        assert (
            cursor.blocks == prefix
        ), f"revised an earlier block after {ch!r}: {cursor.blocks} vs {prefix}"

    assert cursor.blocks == final


def test_streaming_prefix_matches_one_shot_across_mixed_kinds() -> None:
    """Same property, with kind changes forcing closes along the way."""
    events = [
        (SegmentKind.OUTPUT, "# Heading\n"),
        (SegmentKind.OUTPUT, "para text\n"),
        (SegmentKind.TOOL_CALL, "{'name': 'x'}"),
        (SegmentKind.TOOL_RESULT, "ok"),
        (SegmentKind.OUTPUT, "\nmore para\n\n"),
        (SegmentKind.OUTPUT, "```\ncode\n```\n"),
    ]

    t = Transcript()
    for kind, text in events:
        t.append(kind, text)
    cache = NodeTranscript()
    cache.sync(t, t.published_length)
    stable = len(cache.lines) - (1 if cache.open_line else 0)
    final_cursor = BlockCursor()
    final_cursor.feed(cache.lines[:stable])
    final = final_cursor.blocks

    t2 = Transcript()
    cache2 = NodeTranscript()
    cursor2 = BlockCursor()
    for kind, text in events:
        for ch in text:
            t2.append(kind, ch)
            cache2.sync(t2, t2.published_length)
            stable2 = len(cache2.lines) - (1 if cache2.open_line else 0)
            cursor2.feed(cache2.lines[:stable2])
            assert cursor2.blocks == final[: len(cursor2.blocks)]

    assert cursor2.blocks == final


# -- finish(): the one-shot terminator ------------------------------------------


def test_finish_flushes_a_trailing_paragraph() -> None:
    """
    ``feed`` finalises a block only when the next line proves it complete, so the
    last paragraph of a settled turn is still inside the parser when the input runs
    out -- and in DETAIL that paragraph is usually the question itself.
    """
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "I looked at the parser."),
        (SegmentKind.OUTPUT, ""),
        (SegmentKind.OUTPUT, "Fix it, or only report it?"),
    )
    assert kinds(cursor) == [BlockKind.PARAGRAPH]

    cursor.finish()

    assert kinds(cursor) == [BlockKind.PARAGRAPH, BlockKind.PARAGRAPH]
    assert cursor.blocks[-1].lines == ("Fix it, or only report it?",)
    assert cursor.blocks[-1].closed


def test_finish_attaches_inline_to_the_flushed_block() -> None:
    """The flushed tail goes through the same ``_with_inline`` as any other
    finalised block -- unlike ``live_block``, which never gets one."""
    cursor = feed_lines((SegmentKind.OUTPUT, "run `pytest` first"))
    cursor.finish()

    assert cursor.blocks[-1].inline == (
        InlineToken("text", "run "),
        InlineToken("code_inline", "pytest"),
        InlineToken("text", " first"),
    )


def test_finish_reports_an_unterminated_fence_as_unclosed() -> None:
    """Same honesty ``_advance`` applies when a kind change cuts a fence off: the
    terminator was never seen, so ``closed`` stays False."""
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "```python"),
        (SegmentKind.OUTPUT, "x = 1"),
    )
    cursor.finish()

    assert kinds(cursor) == [BlockKind.FENCE]
    assert not cursor.blocks[-1].closed


def test_finish_on_nothing_open_is_a_no_op() -> None:
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "done"),
        (SegmentKind.OUTPUT, ""),
    )
    before = list(cursor.blocks)
    cursor.finish()
    cursor.finish()
    assert cursor.blocks == before


def test_finish_preserves_the_table_promotion() -> None:
    """A table is recognised at finalisation, so a table ending the input must not
    be flushed out as a bare paragraph."""
    cursor = feed_lines(
        (SegmentKind.OUTPUT, "| a | b |"),
        (SegmentKind.OUTPUT, "| - | - |"),
        (SegmentKind.OUTPUT, "| 1 | 2 |"),
    )
    cursor.finish()
    assert kinds(cursor) == [BlockKind.TABLE]
