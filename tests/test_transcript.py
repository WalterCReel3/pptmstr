"""
Transcript: append-only streaming buffer (I7).

The property that matters is that a reader holding a length from frame N sees a
consistent prefix even while the writer is appending during frame N+1.
"""

from __future__ import annotations

import threading

from pptmstr.transcript import (
    OUTPUT_CAP,
    SegmentKind,
    ToolEntry,
    Transcript,
    cap_output,
    tool_call_line,
    tool_result_line,
)


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


def test_turn_prose_starts_at_the_prompt_that_opened_the_turn() -> None:
    """SYSTEM is written only by AgentSession.send, so a prompt after an ended turn opens one."""
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "> first task\n")
    t.append(SegmentKind.OUTPUT, "done with the first thing")
    t.end_turn()
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
    assert t.turn_prose() == "I'll check the tests.\n\n\nThree are failing. Fix them?"


def test_turn_prose_breaks_the_paragraph_where_a_tool_call_separated_the_prose() -> None:
    """
    The driver appends each text block raw, so a sentence before a tool call need
    not end on a newline. Joined bare, a fence the next block opens with would sit
    on the sentence's line, and markdown would not open it.
    """
    fence = "```diff\n--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-b = 2\n+c = 3\n```\n"
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "\n> go\n")
    t.append(SegmentKind.OUTPUT, "Here is the change, done.")
    t.append_tool_call("t1", "Bash", {"command": "true"})
    t.append_tool_result("t1", "Bash", "ok", is_error=False)
    t.append(SegmentKind.OUTPUT, fence)
    assert t.turn_prose() == "Here is the change, done.\n\n" + fence


def test_turn_prose_keeps_output_segments_nothing_separated_joined_as_written() -> None:
    """A closed segment is a seam in storage, not in what was said."""
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "\n> go\n")
    t.append(SegmentKind.OUTPUT, "half a ")
    t.close_segment()
    t.append(SegmentKind.OUTPUT, "sentence")
    assert len([s for s in t.segments() if s.kind is SegmentKind.OUTPUT]) == 2
    assert t.turn_prose() == "half a sentence"


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
    t.end_turn()
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


# -- where a turn starts and ends ------------------------------------------------


def _last_prompt_prose(t: Transcript) -> str:
    """The rule before turns were recorded: OUTPUT after the last SYSTEM segment."""
    segments = t.segments()
    start = next((s.end for s in reversed(segments) if s.kind is SegmentKind.SYSTEM), 0)
    return "".join(
        t.read(max(s.start, start), s.end)
        for s in segments
        if s.kind is SegmentKind.OUTPUT and s.end > start
    )


def test_a_prompt_sent_mid_turn_leaves_the_turn_where_it_began() -> None:
    """
    ``send`` writes the prompt line at once, mid-turn too. The turn going on is
    still the one its line came after, and what is said after the line is still
    that turn's, until the turn ends.
    """
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "\n> build it\n")
    began = t.published_length
    t.append(SegmentKind.OUTPUT, "building. ")
    t.append(SegmentKind.SYSTEM, "\n> and then the docs\n")
    t.append(SegmentKind.OUTPUT, "built.")
    assert (t.turn_start(), t.turn_open()) == (began, True)
    assert t.turn_prose() == "building. \n\nbuilt."


def test_the_prompt_sent_mid_turn_opens_its_turn_where_that_one_ends() -> None:
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "\n> build it\n")
    t.append(SegmentKind.OUTPUT, "building.")
    t.append(SegmentKind.SYSTEM, "\n> and then the docs\n")
    t.append(SegmentKind.OUTPUT, " built.")
    t.end_turn()
    ended = t.published_length
    assert (t.turn_start(), t.turn_open(), t.turn_prose()) == (ended, True, "")
    t.append(SegmentKind.OUTPUT, "the docs")
    assert t.turn_prose() == "the docs"
    t.end_turn()
    assert (t.turn_start(), t.turn_open(), t.turn_prose()) == (ended, False, "the docs")


def test_the_turn_ends_at_the_result_and_stays_the_last_one_until_a_prompt() -> None:
    """
    An ended turn is still "this turn" for DETAIL -- the question it finished on is
    what the operator reads -- but nothing of it is going on.
    """
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "\n> go\n")
    began = t.published_length
    t.append(SegmentKind.OUTPUT, "Shall I?")
    t.end_turn()
    assert (t.turn_start(), t.turn_open(), t.turn_prose()) == (began, False, "Shall I?")
    t.end_turn()
    t.append(SegmentKind.OUTPUT, " (late)")
    assert (t.turn_start(), t.turn_open()) == (began, False), "a second end moves nothing"
    t.append(SegmentKind.SYSTEM, "\n> yes\n")
    assert (t.turn_start(), t.turn_open(), t.turn_prose()) == (t.published_length, True, "")


def test_each_prompt_sent_during_a_turn_opens_one_turn_in_the_order_sent() -> None:
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "\n> one\n")
    t.append(SegmentKind.SYSTEM, "\n> two\n")
    t.append(SegmentKind.OUTPUT, "x")
    t.append(SegmentKind.SYSTEM, "\n> three\n")
    starts = []
    for _ in range(3):
        t.end_turn()
        starts.append((t.turn_start(), t.turn_open()))
        t.append(SegmentKind.OUTPUT, "y")
    lengths = [s for s, _ in starts]
    assert [o for _, o in starts] == [True, True, False]
    assert lengths[0] < lengths[1] and lengths[2] == lengths[1], "the third end opened nothing"


def test_the_turn_is_read_as_it_stood_at_a_pinned_length() -> None:
    """A frame pins a length (I7); the turn it reads is the one going on there."""
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "\n> one\n")
    first = t.published_length
    t.append(SegmentKind.OUTPUT, "a")
    said = t.published_length
    t.append(SegmentKind.SYSTEM, "\n> two\n")
    t.end_turn()
    # No bytes between the second prompt and the end: a length read there is
    # already past the end, which is what the next frame will read.
    ended = t.published_length
    t.append(SegmentKind.OUTPUT, "b")
    t.end_turn()
    assert (t.turn_start(0), t.turn_open(0)) == (0, True), "before any prompt"
    assert (t.turn_start(said), t.turn_open(said)) == (first, True)
    assert (t.turn_start(ended), t.turn_open(ended)) == (ended, True)
    assert (t.turn_start(), t.turn_open()) == (ended, False)
    assert t.turn_start(10**9) == t.turn_start(), "a limit past the end is the end"


def test_a_transcript_with_no_recorded_end_reads_its_turn_as_before() -> None:
    """
    A session resumed, or one whose stream died before its result, has a prompt
    and no end. Its turn is the one after the last prompt, as it always was, and
    the start of the buffer when nothing was ever sent (a sub-agent).
    """
    worker = Transcript()
    worker.append(SegmentKind.OUTPUT, "the answer")
    assert (worker.turn_start(), worker.turn_open()) == (0, True)
    assert worker.turn_prose() == _last_prompt_prose(worker) == "the answer"

    resumed = Transcript()
    resumed.append(SegmentKind.TOOL_RESULT, "a result written before any prompt\n")
    resumed.append(SegmentKind.OUTPUT, "said before")
    resumed.append(SegmentKind.SYSTEM, "\n> carry on\n")
    assert resumed.turn_start() == resumed.published_length, "the first prompt opens a turn"
    resumed.append(SegmentKind.OUTPUT, "carrying on")
    assert resumed.turn_open()
    assert resumed.turn_prose() == _last_prompt_prose(resumed) == "carrying on"


def test_a_turn_with_no_prompt_ends_at_the_result_too() -> None:
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "said")
    t.end_turn()
    assert (t.turn_start(), t.turn_open(), t.turn_prose()) == (0, False, "said")


def test_the_turn_is_never_read_past_what_is_published() -> None:
    """The writer thread ends and opens turns while a reader pins lengths (I7)."""
    t = Transcript()
    done = threading.Event()
    bad: list[tuple[int, int]] = []

    def writer() -> None:
        for n in range(2000):
            t.append(SegmentKind.SYSTEM, f"\n> {n}\n")
            t.append(SegmentKind.OUTPUT, "x" * (n % 7))
            if n % 2:
                t.end_turn()
        done.set()

    def reader() -> None:
        while not done.is_set():
            pinned = t.published_length
            start = t.turn_start(pinned)
            if start > pinned:
                bad.append((start, pinned))

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert bad == []


# -- tool entries --------------------------------------------------------------


def test_an_output_under_the_cap_is_kept_whole() -> None:
    text = "x" * OUTPUT_CAP
    assert cap_output(text) == (text, 0)


def test_an_output_over_the_cap_is_cut_and_says_by_how_much() -> None:
    kept, dropped = cap_output("x" * (OUTPUT_CAP + 5))
    assert (len(kept), dropped) == (OUTPUT_CAP, 5)


def test_the_cap_counts_utf8_bytes_and_never_splits_a_character() -> None:
    """
    The cap is a memory bound, so it is measured in bytes, and a cut that halved a
    character would leave a string that no longer decodes to what the tool said.
    """
    # Three bytes each: the cap lands one byte into the last character that fits.
    text = "\u2500" * ((OUTPUT_CAP // 3) + 10)
    kept, dropped = cap_output(text)
    assert kept == "\u2500" * (OUTPUT_CAP // 3)
    assert len(kept.encode()) + dropped == len(text.encode())
    assert len(kept.encode()) <= OUTPUT_CAP


def test_the_cap_measures_a_lone_surrogate_rather_than_failing_on_it() -> None:
    text = "\udc80" * (OUTPUT_CAP // 3 + 1)
    kept, dropped = cap_output(text)
    assert kept == "\udc80" * (OUTPUT_CAP // 3) and dropped == 3


def test_a_result_over_the_cap_is_held_cut_and_draws_the_line_it_always_did() -> None:
    t = Transcript()
    t.append_tool_call("t1", "Bash", {"command": "yes"})
    t.append_tool_result("t1", "Bash", "y\n" * OUTPUT_CAP, is_error=False)
    entry = t.tool_entry("t1")
    assert entry is not None and entry.result is not None
    assert entry.result.truncated and entry.result.dropped_bytes == OUTPUT_CAP
    assert len(entry.result.output) == OUTPUT_CAP
    assert t.text().endswith("Bash -> " + "y\n" * 148 + "y...\n")


def test_a_non_text_result_is_held_as_the_text_its_line_was_cut_from() -> None:
    content = [{"type": "text", "text": "A page."}]
    output, dropped = cap_output(content)
    assert (output, dropped) == (repr(content), 0)
    assert cap_output(None) == ("None", 0)


def test_coalesced_calls_each_find_their_own_entry() -> None:
    """
    Two calls in one message are one TOOL_CALL segment -- adjacent lines of a kind
    coalesce -- so a renderer looking up the segment must get both, in order.
    """
    t = Transcript()
    t.append_tool_call("a", "Read", {"file_path": "a.py"})
    t.append_tool_call("b", "Read", {"file_path": "b.py"})
    t.append_tool_result("a", "Read", "1 line", is_error=False)
    t.append_tool_result("b", "Read", "2 lines", is_error=False)
    calls, results = t.segments()
    assert (calls.kind, results.kind) == (SegmentKind.TOOL_CALL, SegmentKind.TOOL_RESULT)
    ids = [e.tool_use_id for e in t.tool_entries(calls.start, calls.end)]
    assert ids == ["a", "b"]
    ids = [e.tool_use_id for e in t.tool_entries(results.start, results.end)]
    assert ids == ["a", "b"]
    whole = t.tool_entries(0, t.published_length)
    assert [e.tool_use_id for e in whole] == ["a", "b"]
    assert all(e.result is not None for e in whole)


def test_an_entry_span_is_the_bytes_of_its_line() -> None:
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "before\n")
    t.append_tool_call("a", "Bash", {"command": "ls \u00e9"})
    t.append_tool_result("a", "Bash", "x\x00", is_error=True)
    entry = t.tool_entry("a")
    assert entry is not None and entry.call is not None and entry.result is not None
    assert t.read(*entry.call) == tool_call_line("Bash", {"command": "ls \u00e9"})
    assert t.read(entry.result.start, entry.result.end) == "Bash -> x\\u0000\n"
    assert t.segments()[-1].kind is SegmentKind.ERROR


def test_a_range_outside_every_line_finds_nothing() -> None:
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "prose\n")
    t.append_tool_call("a", "Bash", {})
    assert t.tool_entries(0, 6) == ()
    assert t.tool_entries(t.published_length, t.published_length + 100) == ()


def test_a_reused_id_is_a_new_entry_and_the_earlier_line_keeps_its_call() -> None:
    t = Transcript()
    t.append_tool_call("a", "Read", {"file_path": "one"})
    first_end = t.published_length
    t.append(SegmentKind.OUTPUT, "between\n")
    t.append_tool_call("a", "Read", {"file_path": "two"})
    t.append_tool_result("a", "Read", "ok", is_error=False)
    (earlier,) = t.tool_entries(0, first_end)
    assert earlier.input == {"file_path": "one"} and earlier.result is None
    latest = t.tool_entry("a")
    assert latest is not None and latest.input == {"file_path": "two"}
    assert latest.result is not None


def test_an_entry_cannot_be_changed_through_the_arguments_it_was_given() -> None:
    args = {"command": "ls"}
    t = Transcript()
    t.append_tool_call("a", "Bash", args)
    args["command"] = "rm -rf /"
    entry = t.tool_entry("a")
    assert entry is not None and entry.input == {"command": "ls"}


def test_the_line_helpers_are_what_the_segments_hold() -> None:
    t = Transcript()
    t.append_tool_call("a", "Grep", {"pattern": "x"})
    t.append_tool_result("a", "Grep", "3 matches", is_error=False)
    assert t.text() == tool_call_line("Grep", {"pattern": "x"}) + tool_result_line(
        "Grep", "3 matches"
    )


def test_a_reader_never_sees_an_entry_whose_line_is_unpublished() -> None:
    """
    I7 for entries: the line and the entry land under one lock, so every entry a
    reader can find has its span inside the published length, and every published
    call line has its entry.
    """
    t = Transcript()
    stop = threading.Event()
    bad: list[ToolEntry] = []

    def reader() -> None:
        while not stop.is_set():
            published = t.published_length
            for entry in t.tool_entries(0, 1 << 62):
                if entry.call is not None and entry.call[1] > t.published_length:
                    bad.append(entry)
            calls = [s for s in t.segments(published) if s.kind is SegmentKind.TOOL_CALL]
            if calls and not t.tool_entries(calls[-1].start, calls[-1].end):
                bad.append(ToolEntry("missing", "", None, None))

    thread = threading.Thread(target=reader)
    thread.start()
    for i in range(2000):
        t.append_tool_call(f"t{i}", "Bash", {"command": str(i)})
        t.append_tool_result(f"t{i}", "Bash", "ok", is_error=False)
    stop.set()
    thread.join()
    assert not bad
    assert len(t.tool_entries(0, t.published_length)) == 2000


# -- a turn read whole --------------------------------------------------------------


class _EndsBetweenReads(Transcript):
    """
    A transcript whose writer ends the open turn right after a frame's first read
    of the turn: the interleaving of the asyncio thread with the UI thread, made
    to happen every time. ``reads`` is the accessors read, in order.
    """

    __slots__ = ("reads",)

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    def _read(self, name: str) -> None:
        self.reads.append(name)
        if len(self.reads) == 1:
            self.end_turn()

    def turn_start(self, limit: int | None = None) -> int:
        try:
            return super().turn_start(limit)
        finally:
            self._read("turn_start")

    def turn_open(self, limit: int | None = None) -> bool:
        try:
            return super().turn_open(limit)
        finally:
            self._read("turn_open")

    def turn(self, limit: int | None = None) -> tuple[int, bool]:
        try:
            return super().turn(limit)
        finally:
            self._read("turn")


def test_a_frame_reads_a_turns_start_and_whether_it_is_open_at_one_moment() -> None:
    """
    A prompt queued behind the open turn opens its own when that one ends, and
    ``end_turn`` writes no bytes, so the frame's pinned length does not move. The
    frame reads the turn in one call, so whatever the writer does between two of
    the frame's reads, its start and its open state are of one moment.
    """
    from pptmstr.ui import transcript_pane

    t = _EndsBetweenReads()
    t.append(SegmentKind.SYSTEM, "\n> first\n")
    first = t.published_length
    t.append_tool_call("a", "Bash", {"command": "sleep 9"})
    t.append(SegmentKind.SYSTEM, "\n> second\n")
    blocks = transcript_pane._Blocks(
        transcript_pane.TranscriptState(),
        ("s1", None),
        t,
        transcript_pane.NodeTranscript(),
        t.published_length,
        True,
        16.0,
    )
    assert t.reads == ["turn"]
    assert (blocks.turn_start, blocks.turn_open) == (first, True), "read before it ended"


def test_turn_is_turn_start_and_turn_open_together() -> None:
    t = Transcript()
    assert t.turn() == (0, True)
    t.append(SegmentKind.SYSTEM, "\n> go\n")
    start = t.published_length
    t.append(SegmentKind.OUTPUT, "said")
    assert t.turn() == (start, True) == (t.turn_start(), t.turn_open())
    t.end_turn()
    assert t.turn() == (start, False) == (t.turn_start(), t.turn_open())
    assert t.turn(start) == (start, True), "read at a length before it ended"


def test_a_withdrawn_prompt_that_opened_a_turn_leaves_the_turns_as_they_were() -> None:
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "\n> one\n")
    t.end_turn()
    before = t.turn()
    line = t.append(SegmentKind.SYSTEM, "\n> lost\n")
    assert t.turn() == (line, True)
    t.withdraw_prompt(line)
    assert t.turn() == before


def test_a_withdrawn_prompt_queued_behind_a_turn_gives_up_its_place() -> None:
    t = Transcript()
    t.append(SegmentKind.SYSTEM, "\n> one\n")
    lost = t.append(SegmentKind.SYSTEM, "\n> lost\n")
    kept = t.append(SegmentKind.SYSTEM, "\n> kept\n")
    t.withdraw_prompt(lost)
    t.end_turn()
    assert t.turn() == (t.published_length, True), "kept's turn, opened where one ended"
    t.append(SegmentKind.OUTPUT, "for kept")
    t.end_turn()
    assert t.turn_prose() == "for kept" and t.turn() == (kept, False)


def test_a_withdrawn_turn_hands_its_place_to_the_prompt_queued_behind_it() -> None:
    """
    The session answers the queued prompt at once, since the withdrawn one never
    reached it: that prompt's turn starts at its own line.
    """
    t = Transcript()
    lost = t.append(SegmentKind.SYSTEM, "\n> lost\n")
    kept = t.append(SegmentKind.SYSTEM, "\n> kept\n")
    t.withdraw_prompt(lost)
    assert t.turn() == (kept, True)
    t.append(SegmentKind.OUTPUT, "for kept")
    t.end_turn()
    assert t.turn_prose() == "for kept" and t.turn() == (kept, False)


def test_withdrawing_a_prompt_whose_turn_has_ended_changes_nothing() -> None:
    t = Transcript()
    line = t.append(SegmentKind.SYSTEM, "\n> answered\n")
    t.end_turn()
    before = (t.turn(), list(t._turn_starts), list(t._turn_ends))
    t.withdraw_prompt(line)
    t.withdraw_prompt(12345)
    assert (t.turn(), list(t._turn_starts), list(t._turn_ends)) == before
