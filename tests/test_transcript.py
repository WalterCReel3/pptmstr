"""
Transcript: append-only streaming buffer (I7).

The property that matters is that a reader holding a length from frame N sees a
consistent prefix even while the writer is appending during frame N+1.
"""

from __future__ import annotations

import threading

from pptmstr.transcript import SegmentKind, Transcript


def test_append_and_read() -> None:
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "hello ")
    t.append(SegmentKind.OUTPUT, "world")
    assert t.text() == "hello world"


def test_same_kind_coalesces_into_one_segment() -> None:
    """
    A token stream must not produce one segment per token, or every read becomes a
    walk over thousands of entries for a single paragraph of text.
    """
    t = Transcript()
    for _ in range(100):
        t.append(SegmentKind.OUTPUT, "x")
    segs = t.segments()
    assert len(segs) == 1
    assert segs[0].kind is SegmentKind.OUTPUT
    assert segs[0].length == 100


def test_kind_change_starts_a_new_segment() -> None:
    t = Transcript()
    t.append(SegmentKind.REASONING, "thinking")
    t.append(SegmentKind.OUTPUT, "answer")
    kinds = [s.kind for s in t.segments()]
    assert kinds == [SegmentKind.REASONING, SegmentKind.OUTPUT]


def test_close_segment_splits_same_kind() -> None:
    """Two consecutive tool calls are two segments even though the kind matches."""
    t = Transcript()
    t.append(SegmentKind.TOOL_CALL, "first")
    t.close_segment()
    t.append(SegmentKind.TOOL_CALL, "second")
    assert len(t.segments()) == 2


def test_segments_carry_their_offsets() -> None:
    t = Transcript()
    t.append(SegmentKind.REASONING, "abc")
    t.append(SegmentKind.OUTPUT, "de")
    a, b = t.segments()
    assert (a.start, a.end) == (0, 3)
    assert (b.start, b.end) == (3, 5)
    assert t.read(b.start, b.end) == "de"


def test_published_length_tracks_bytes_not_characters() -> None:
    """
    Offsets index bytes. Conflating the two silently truncates any non-ASCII output,
    which is exactly where it would go unnoticed longest.
    """
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "héllo")
    assert t.published_length == 6
    assert t.text() == "héllo"


def test_read_clamps_a_stale_end_offset() -> None:
    """
    The UI passes a length pinned at snapshot time. Passing one from an earlier frame
    must clamp rather than raise -- an off-by-one here would crash the render loop.
    """
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "abc")
    assert t.read(0, 9_999) == "abc"
    assert t.read(-5, 2) == "ab"
    assert t.read(2, 1) == ""


def test_read_across_a_split_codepoint_does_not_raise() -> None:
    """
    The visible window is chosen by byte offset, so it can land mid-character. That
    must degrade to a replacement character, not take down the frame.
    """
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "é")
    assert t.read(0, 1) == "�"


def test_segments_are_clipped_to_the_pinned_length() -> None:
    """
    Frame N must not render bytes that arrived during frame N+1: a segment straddling
    the pinned length is truncated to it, not shown whole.
    """
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "12345")
    pinned = t.published_length
    t.append(SegmentKind.OUTPUT, "6789")

    clipped = t.segments(limit=pinned)
    assert len(clipped) == 1
    assert clipped[0].end == pinned
    assert t.read(0, pinned) == "12345"


def test_a_pinned_length_stays_valid_while_the_writer_appends() -> None:
    """
    I7 in one test: a reader holding an old length keeps seeing the same prefix no
    matter how much has been written since.
    """
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "stable")
    pinned = t.published_length
    for _ in range(500):
        t.append(SegmentKind.OUTPUT, "more")
    assert t.read(0, pinned) == "stable"


def test_concurrent_writer_and_reader() -> None:
    """
    The real shape of the thing: the asyncio thread appends while the UI thread
    reads. Every read must decode cleanly and be a prefix of the final text -- a
    torn read would show up here as a decode error or a garbled prefix.
    """
    t = Transcript()
    chunks = 2_000
    done = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for i in range(chunks):
                t.append(SegmentKind.OUTPUT, f"{i:04d};")
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)
        finally:
            done.set()

    def reader() -> None:
        try:
            while not done.is_set():
                pinned = t.published_length
                text = t.read(0, pinned)
                assert len(text.encode("utf-8")) == pinned
                assert "�" not in text
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)

    assert not errors, errors
    assert t.text() == "".join(f"{i:04d};" for i in range(chunks))


def test_empty_transcript_reads_clean() -> None:
    t = Transcript()
    assert t.published_length == 0
    assert t.text() == ""
    assert t.segments() == ()


# -- turn_prose ----------------------------------------------------------------
#
# The reading surfaces (DETAIL's "what it said", the inbox's expanded question)
# used to render `tail`, which is a byte window over every kind -- so a turn whose
# prose sat behind a large tool result rendered the tool result under a heading
# promising the agent's own words. See
# planning/2026-08-11-what-it-said-is-a-byte-tail.md.


def test_turn_prose_excludes_machinery() -> None:
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "> audit the parser\n")
    t.append(SegmentKind.REASONING, "the user probably means the TLE one\n")
    t.append(SegmentKind.TOOL_CALL, "Read(parser.py)\n")
    t.append(SegmentKind.TOOL_RESULT, "Read -> 268 lines\n")
    t.append(SegmentKind.OUTPUT, "The checksum is never validated.")
    assert t.turn_prose() == "The checksum is never validated."


def test_turn_prose_is_the_defect_tail_used_to_show() -> None:
    """
    The regression this exists for: prose sitting behind a tool result big enough
    to fill the reading surface.

    `tail` does not *lose* the prose -- it is a window on the end, so the last
    words are in it. It buries them: the question is one line under 1200 bytes of
    grep output, which in the inbox's fixed-height preview box puts it below the
    fold, and in DETAIL makes the operator scroll past machinery to reach the
    words the heading promised.
    """
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "> go\n")
    t.append(SegmentKind.TOOL_RESULT, "grep -> " + ("x" * 5000) + "\n")
    t.append(SegmentKind.OUTPUT, "Which of these should I fix first?")

    question = "Which of these should I fix first?"
    windowed = t.tail(1200)
    assert windowed.count("x") > len(question)
    assert not windowed.startswith(question)

    assert t.turn_prose() == question


def test_turn_prose_starts_at_the_last_system_segment() -> None:
    """SYSTEM is written only by AgentSession.send, so it *is* the turn boundary."""
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "> first task\n")
    t.append(SegmentKind.OUTPUT, "done with the first thing")
    t.append(SegmentKind.SYSTEM, "> now do the other one\n")
    t.append(SegmentKind.OUTPUT, "and here is the second answer")
    assert t.turn_prose() == "and here is the second answer"


def test_turn_prose_joins_several_output_runs_in_the_turn() -> None:
    """Prose interleaved with tool calls is still one turn's worth of speech."""
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "> go\n")
    t.append(SegmentKind.OUTPUT, "I'll check the tests.\n")
    t.append(SegmentKind.TOOL_CALL, "Bash(pytest)\n")
    t.append(SegmentKind.TOOL_RESULT, "Bash -> 3 failed\n")
    t.append(SegmentKind.OUTPUT, "Three are failing. Fix them?")
    assert t.turn_prose() == "I'll check the tests.\nThree are failing. Fix them?"


def test_turn_prose_without_a_marker_is_the_whole_buffer() -> None:
    """
    A sub-agent is spawned by a tool call rather than by `send`, so it carries no
    SYSTEM segment. Its whole life is one turn, which is why the fallback is the
    start of the buffer and not an error.
    """
    t = Transcript()
    t.append(SegmentKind.REASONING, "thinking\n")
    t.append(SegmentKind.OUTPUT, "the answer")
    assert t.turn_prose() == "the answer"


def test_turn_prose_is_empty_when_the_turn_has_only_machinery() -> None:
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "> go\n")
    t.append(SegmentKind.OUTPUT, "from the previous turn")
    t.append(SegmentKind.SYSTEM, "> and again\n")
    t.append(SegmentKind.TOOL_CALL, "Read(x.py)\n")
    assert t.turn_prose() == ""


def test_turn_prose_respects_the_published_length() -> None:
    """
    A partially written SYSTEM segment must not be treated as this turn's start:
    the boundary is only real once its bytes are published, or a frame lands
    mid-write showing the new turn as already empty.
    """
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "> go\n")
    t.append(SegmentKind.OUTPUT, "the answer")
    assert t.turn_prose() == "the answer"


def test_turn_prose_on_an_empty_transcript() -> None:
    assert Transcript().turn_prose() == ""
