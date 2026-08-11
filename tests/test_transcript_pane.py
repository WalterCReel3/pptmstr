"""
Transcript pane: the incremental line cache and the filters.

No ImGui here. The cache is where a transcript pane gets slow or wrong, and it is
plain data -- so it can be tested properly rather than eyeballed in a screenshot.
"""

from __future__ import annotations

import time

from pptmstr.transcript import SegmentKind, Transcript
from pptmstr.ui.transcript_pane import NodeTranscript, TranscriptState, _visible, copy_text


def sync(cache: NodeTranscript, transcript: Transcript) -> None:
    cache.sync(transcript, transcript.published_length)


def texts(cache: NodeTranscript) -> list[str]:
    return [line.text for line in cache.lines]


# -- line building -------------------------------------------------------------


def test_splits_on_newlines() -> None:
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "one\ntwo\nthree")
    cache = NodeTranscript()
    sync(cache, t)
    assert texts(cache) == ["one", "two", "three"]


def test_streamed_tokens_do_not_each_become_a_line() -> None:
    """
    The whole point of the cache. A token stream appends a few characters at a
    time; if each append started a row, a paragraph would render as a column.
    """
    t = Transcript()
    cache = NodeTranscript()
    for token in ("Hel", "lo ", "wor", "ld"):
        t.append(SegmentKind.OUTPUT, token)
        sync(cache, t)
    assert texts(cache) == ["Hello world"]


def test_a_kind_change_starts_a_new_line() -> None:
    t = Transcript()
    t.append(SegmentKind.REASONING, "thinking")
    t.append(SegmentKind.OUTPUT, "answer")
    cache = NodeTranscript()
    sync(cache, t)
    assert [(ln.kind, ln.text) for ln in cache.lines] == [
        (SegmentKind.REASONING, "thinking"),
        (SegmentKind.OUTPUT, "answer"),
    ]


def test_incremental_sync_matches_a_single_pass() -> None:
    """
    Syncing every frame must produce exactly what syncing once at the end would.
    A drift here shows up as duplicated or missing text under load only.
    """
    chunks = ["alpha\nbeta", " continued\n", "gamma\n", "delta"]

    live = Transcript()
    incremental = NodeTranscript()
    for chunk in chunks:
        live.append(SegmentKind.OUTPUT, chunk)
        sync(incremental, live)

    whole = Transcript()
    whole.append(SegmentKind.OUTPUT, "".join(chunks))
    at_once = NodeTranscript()
    sync(at_once, whole)

    assert texts(incremental) == texts(at_once)


def test_consumed_only_moves_forward() -> None:
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "abc")
    cache = NodeTranscript()
    sync(cache, t)
    before = list(cache.lines)
    # A stale limit from an earlier frame must be a no-op, not a rewind.
    cache.sync(t, 1)
    assert cache.lines == before
    assert cache.consumed == t.published_length


def test_resync_without_new_bytes_is_a_no_op() -> None:
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "one\ntwo")
    cache = NodeTranscript()
    for _ in range(5):
        sync(cache, t)
    assert texts(cache) == ["one", "two"]


def test_pinned_limit_excludes_later_bytes() -> None:
    """I7: a frame renders the prefix it pinned, not whatever arrived since."""
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "visible\n")
    pinned = t.published_length
    t.append(SegmentKind.OUTPUT, "arrived later")
    cache = NodeTranscript()
    cache.sync(t, pinned)
    assert "arrived later" not in "".join(texts(cache))


def test_trailing_newline_leaves_an_empty_line_to_continue() -> None:
    t = Transcript()
    cache = NodeTranscript()
    t.append(SegmentKind.OUTPUT, "first\n")
    sync(cache, t)
    t.append(SegmentKind.OUTPUT, "second")
    sync(cache, t)
    assert texts(cache) == ["first", "second"]


def test_multiline_tool_call_keeps_its_shape() -> None:
    t = Transcript()
    t.append(SegmentKind.TOOL_CALL, "\nBash(command=pytest -q)\n")
    cache = NodeTranscript()
    sync(cache, t)
    assert "Bash(command=pytest -q)" in texts(cache)


# -- filters -------------------------------------------------------------------


def build(*pairs: tuple[SegmentKind, str]) -> NodeTranscript:
    t = Transcript()
    for kind, text in pairs:
        t.append(kind, text)
        t.close_segment()
    cache = NodeTranscript()
    sync(cache, t)
    return cache


def test_reasoning_can_be_hidden() -> None:
    cache = build((SegmentKind.REASONING, "pondering"), (SegmentKind.OUTPUT, "answer"))
    state = TranscriptState(show_reasoning=False)
    assert [ln.text for ln in _visible(state, cache)] == ["answer"]


def test_reasoning_shown_by_default() -> None:
    """Goal #3: reasoning is the thing this tool exists to surface."""
    cache = build((SegmentKind.REASONING, "pondering"), (SegmentKind.OUTPUT, "answer"))
    assert len(_visible(TranscriptState(), cache)) == 2


def test_search_filters_case_insensitively() -> None:
    cache = build((SegmentKind.OUTPUT, "Alpha\nbeta\nGAMMA"))
    state = TranscriptState(search="gamma")
    assert [ln.text for ln in _visible(state, cache)] == ["GAMMA"]


def test_search_and_reasoning_filter_compose() -> None:
    cache = build((SegmentKind.REASONING, "match me"), (SegmentKind.OUTPUT, "match me too"))
    state = TranscriptState(show_reasoning=False, search="match")
    assert [ln.text for ln in _visible(state, cache)] == ["match me too"]


def test_empty_search_matches_everything() -> None:
    cache = build((SegmentKind.OUTPUT, "a\nb\nc"))
    assert len(_visible(TranscriptState(search=""), cache)) == 3


# -- runs and copying ----------------------------------------------------------


def test_a_kind_change_starts_a_new_run() -> None:
    cache = build(
        (SegmentKind.REASONING, "pondering\nstill pondering\n"),
        (SegmentKind.OUTPUT, "the answer\n"),
    )
    assert [line.run for line in cache.lines] == [0, 0, 1]


def test_streamed_tokens_stay_in_one_run() -> None:
    """
    The unit of a copy is what the agent said, not what a socket happened to
    deliver. A run that fragmented per delta would make copy useless on live output.
    """
    t = Transcript()
    cache = NodeTranscript()
    for token in ("Right, ", "here is ", "the plan.\nStep one"):
        t.append(SegmentKind.OUTPUT, token)
        sync(cache, t)
    assert {line.run for line in cache.lines} == {0}


def test_run_text_round_trips_the_lines() -> None:
    cache = build((SegmentKind.TOOL_RESULT, '{\n  "ok": true\n}\n'))
    assert cache.run_text(0) == '{\n  "ok": true\n}'


def test_run_text_keeps_blank_lines() -> None:
    """A paragraph break is structure. Dropping it reflows the paste."""
    cache = build((SegmentKind.OUTPUT, "first\n\nsecond\n"))
    assert cache.run_text(0) == "first\n\nsecond"


def test_run_bounds_are_contiguous_and_cover_every_line() -> None:
    cache = build(
        (SegmentKind.OUTPUT, "a\nb\n"),
        (SegmentKind.TOOL_CALL, "Bash(ls)\n"),
        (SegmentKind.TOOL_RESULT, "one\ntwo\nthree\n"),
    )
    covered = [cache.run_bounds(run) for run in range(len(cache.run_starts))]
    assert covered == [(0, 2), (2, 3), (3, 6)]


def test_run_text_ignores_the_filters() -> None:
    """
    Copying a run yields what the agent emitted, not the subset a search is
    showing. A paste that silently dropped the non-matching lines would be worse
    than no copy at all -- it looks complete.
    """
    cache = build((SegmentKind.OUTPUT, "alpha\nbeta\ngamma\n"))
    state = TranscriptState(search="beta")
    assert [line.text for line in _visible(state, cache)] == ["beta"]
    assert cache.run_text(0) == "alpha\nbeta\ngamma"


def test_copy_visible_follows_the_filters() -> None:
    """The other half of the trade: the button next to the filters obeys them."""
    cache = build((SegmentKind.REASONING, "pondering\n"), (SegmentKind.OUTPUT, "answer\n"))
    state = TranscriptState(show_reasoning=False)
    assert copy_text(_visible(state, cache)) == "answer"


def test_an_unknown_run_is_empty_rather_than_an_error() -> None:
    """
    The context menu latches a run index and can outlive it -- the operator can
    switch node between the right-click and the menu item. Empty beats a traceback
    on the frame path.
    """
    cache = build((SegmentKind.OUTPUT, "a\n"))
    assert cache.run_text(7) == ""
    assert cache.run_bounds(-1) == (0, 0)


def test_a_run_survives_a_segment_split_mid_line() -> None:
    """
    close_segment() can split two same-kind segments in the middle of a line, and
    sync() extends one Line across the split. A run is therefore a maximal same-kind
    span, not a segment -- pinned here because it is the one case where the two
    definitions disagree.
    """
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "half a ")
    t.close_segment()
    t.append(SegmentKind.OUTPUT, "line\n")
    cache = NodeTranscript()
    sync(cache, t)
    assert texts(cache) == ["half a line"]
    assert cache.run_starts == [0]


# -- cache lifetime ------------------------------------------------------------


def test_caches_are_pruned_when_unused() -> None:
    """
    The largest thing this UI holds. One cache per node ever selected is how a long
    session becomes a memory leak.
    """
    state = TranscriptState()
    state.frame = 1
    state.cache_for(("a", None))
    state.cache_for(("b", None))
    assert len(state.caches) == 2

    for frame in range(2, 200):
        state.prune(frame)
        state.cache_for(("a", None))

    assert ("a", None) in state.caches
    assert ("b", None) not in state.caches


def test_prune_keeps_recently_touched_caches() -> None:
    state = TranscriptState()
    state.frame = 1
    state.cache_for(("a", None))
    state.prune(5)
    assert ("a", None) in state.caches


# -- cost ----------------------------------------------------------------------


def test_steady_state_sync_is_proportional_to_new_output() -> None:
    """
    The property the pane rests on: appending to a large transcript must not cost
    more than appending to a small one. Re-splitting the whole buffer each frame
    would make a long-running agent progressively freeze the UI.
    """
    t = Transcript()
    cache = NodeTranscript()
    for i in range(4000):
        t.append(SegmentKind.OUTPUT, f"line {i}\n")
    sync(cache, t)
    assert len(cache.lines) >= 4000

    started = time.perf_counter()
    for i in range(200):
        t.append(SegmentKind.OUTPUT, f"tail {i}\n")
        sync(cache, t)
    elapsed = time.perf_counter() - started

    # Generous: this is a regression guard against O(n) per frame, not a benchmark.
    # An O(n) implementation takes seconds here.
    assert elapsed < 1.0, f"200 incremental syncs took {elapsed:.2f}s"


def test_a_trailing_newline_does_not_emit_a_blank_row() -> None:
    """
    Regression: "a\\n" splits to ["a", ""], and emitting that empty tail as a row
    double-spaced the whole transcript. A newline terminates a line rather than
    starting a blank one.
    """
    t = Transcript()
    t.append(SegmentKind.REASONING, "Working out where to start.\n")
    t.append(SegmentKind.OUTPUT, "Right, here is the plan.\n")
    cache = NodeTranscript()
    sync(cache, t)
    assert texts(cache) == ["Working out where to start.", "Right, here is the plan."]


def test_a_bare_newline_is_still_a_blank_line() -> None:
    """Deliberate blank lines in output must survive -- only the phantom tail goes."""
    t = Transcript()
    t.append(SegmentKind.OUTPUT, "a\n\nb\n")
    cache = NodeTranscript()
    sync(cache, t)
    assert texts(cache) == ["a", "", "b"]


def test_continuation_stops_at_a_newline_boundary() -> None:
    """
    A chunk ending in a newline closes its line, so the next chunk of the same kind
    starts a new one rather than being glued onto the previous sentence.
    """
    t = Transcript()
    cache = NodeTranscript()
    t.append(SegmentKind.OUTPUT, "finished.\n")
    sync(cache, t)
    t.append(SegmentKind.OUTPUT, "new thought")
    sync(cache, t)
    assert texts(cache) == ["finished.", "new thought"]
