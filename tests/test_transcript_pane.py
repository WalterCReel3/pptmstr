"""
Transcript pane: the incremental line cache, the filters, and the typeset feed.

No ImGui until the last section. The cache and the feed are where a transcript pane
gets slow or wrong, and they are plain data -- so they can be tested properly rather
than eyeballed in a screenshot. The last section draws the pane in real frames on
the rig ``tests/test_typeset.py`` builds: a context, an atlas, no window.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from pptmstr import fake_driver
from pptmstr.intents import AgentSpawned
from pptmstr.model import NodeId, Snapshot
from pptmstr.store import Store
from pptmstr.transcript import SegmentKind, Transcript
from pptmstr.ui import transcript_pane, typeset
from pptmstr.ui.transcript_pane import (
    NodeTranscript,
    RenderMode,
    TranscriptState,
    TypesetFeed,
    _visible,
    copy_text,
    open_fence,
)
from tests import test_typeset as rig

# The rig's fixtures, under the names pytest finds them by.
imgui = rig.imgui
seam = rig.seam


@pytest.fixture(autouse=True)
def old_renderer(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The seam off for every test that did not ask for it.

    The faces stay loaded from the first typeset test to the end of the module.
    """
    if "seam" not in request.fixturenames:
        monkeypatch.setattr(typeset, "enabled", lambda pane=None: False)


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
    state.prune(1, now=0.0)
    state.cache_for(("a", None))
    state.cache_for(("b", None))
    assert len(state.caches) == 2

    for frame in range(2, 200):
        state.prune(frame, now=frame / 60.0)
        state.cache_for(("a", None))

    assert ("a", None) in state.caches
    assert ("b", None) not in state.caches


def test_prune_keeps_recently_touched_caches() -> None:
    state = TranscriptState()
    state.prune(1, now=0.0)
    state.cache_for(("a", None))
    state.prune(5, now=4.0 / 60.0)
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


# -- the typeset feed: plain data ------------------------------------------------


def fed(*pairs: tuple[SegmentKind, str]) -> tuple[TypesetFeed, NodeTranscript, Transcript]:
    t = Transcript()
    for kind, text in pairs:
        t.append(kind, text)
    cache = NodeTranscript()
    sync(cache, t)
    feed = TypesetFeed()
    feed.advance(cache)
    return feed, cache, t


def shown(feed: TypesetFeed) -> list[tuple[SegmentKind, str]]:
    """Every run the feed holds, closed then open, as (kind, text as fed)."""
    runs = [(run.kind, run.part.text) for run in feed.runs]
    if feed.open is not None:
        runs.append((feed.open.kind, feed.open.text))
    return runs


def parts_of(feed: TypesetFeed, show_reasoning: bool = True) -> list[typeset.Part]:
    """The parts of every generation, oldest first."""
    return [part for held in feed.generations for part in held.parts(show_reasoning)]


def accounted(feed: TypesetFeed) -> int:
    """Every line the feed says it has passed: skipped, held closed, or open."""
    return feed.skipped + feed.held + (feed.open.lines if feed.open is not None else 0)


def test_model_prose_is_markdown_and_everything_else_is_literal_in_its_role() -> None:
    """
    A tool result is full of ``_`` and ``*``. Read as markdown it would lose them
    and gain emphasis nobody wrote.
    """
    feed, _, _ = fed(
        (SegmentKind.REASONING, "thinking *hard*\n"),
        (SegmentKind.OUTPUT, "# Title\n"),
        (SegmentKind.TOOL_CALL, "\nRead(file_path=a_b.py)\n"),
        (SegmentKind.TOOL_RESULT, '{"__init__": "*x*"}\n'),
        (SegmentKind.ERROR, "boom\n"),
        (SegmentKind.SYSTEM, "> again\n"),
        (SegmentKind.COMPACTION, "compacted\n"),
        (SegmentKind.OUTPUT, "tail"),
    )
    parts = parts_of(feed)
    assert [(part.kind, part.colour) for part in parts] == [
        (typeset.Kind.MARKDOWN, "text_dim"),
        (typeset.Kind.MARKDOWN, None),
        (typeset.Kind.LITERAL, "accent"),
        (typeset.Kind.LITERAL, "diff_context"),
        (typeset.Kind.LITERAL, "danger"),
        (typeset.Kind.LITERAL, "text_dim"),
        (typeset.Kind.LITERAL, "warn"),
    ]
    # Literal text is exactly what was written, less the newlines around it.
    assert parts[2].text == "Read(file_path=a_b.py)"
    assert parts[3].text == '{"__init__": "*x*"}'
    assert feed.open is not None and feed.open.kind is SegmentKind.OUTPUT
    assert feed.open.text == "tail"


def test_a_kind_change_closes_the_open_run_and_an_open_fence_with_it() -> None:
    """
    One unbalanced fence in a model message must not style the rest of the session
    as code: the fence ends with the run it was opened in.
    """
    feed, _, _ = fed(
        (SegmentKind.OUTPUT, "before\n```py\nx = 1\n"),
        (SegmentKind.TOOL_CALL, "Bash(ls)\n"),
        (SegmentKind.OUTPUT, "after the tool, *prose* again\n"),
    )
    assert [part.text for part in parts_of(feed)] == ["before\n```py\nx = 1\n", "Bash(ls)"]
    assert feed.open is not None
    assert feed.open.text == "after the tool, *prose* again\n"
    assert open_fence(feed.open.text.split("\n")) is None


def test_the_feed_is_the_same_however_the_bytes_were_chunked() -> None:
    script = [
        (SegmentKind.REASONING, "plan\nmore plan\n"),
        (SegmentKind.OUTPUT, "# Title\n\nSome *prose* here\nand more.\n\n```py\nx = 1\n```\n"),
        (SegmentKind.TOOL_CALL, "Read(x)\n"),
        (SegmentKind.OUTPUT, "the end, with no newline"),
    ]
    whole, _, _ = fed(*script)

    t = Transcript()
    cache = NodeTranscript()
    feed = TypesetFeed()
    taken: list[str] = []
    for kind, text in script:
        for start in range(0, len(text), 3):
            t.append(kind, text[start : start + 3])
            sync(cache, t)
            feed.advance(cache)
            # A frame with nothing new in between, as most frames are.
            feed.advance(cache)
            if feed.open is not None and feed.open.kind is SegmentKind.OUTPUT:
                taken.append(feed.open.take())
    assert shown(feed) == shown(whole)
    # A closed literal run is held without the newlines around it.
    assert shown(feed) == [script[0], script[1], (SegmentKind.TOOL_CALL, "Read(x)"), script[3]]
    # What the stream was handed, piece by piece, is the two OUTPUT runs once each.
    assert "".join(taken) == script[1][1] + script[3][1]
    assert [run.lines for run in feed.runs] == [2, 8, 1]
    assert accounted(feed) == feed.next_line == 11


def test_a_line_left_open_by_a_kind_change_is_ended() -> None:
    """
    The next run starts a new line whether or not this one ended in a newline, so
    the run that is closing gets the newline its last line never had.
    """
    t = Transcript()
    cache = NodeTranscript()
    feed = TypesetFeed()
    t.append(SegmentKind.OUTPUT, "half a li")
    sync(cache, t)
    feed.advance(cache)
    assert feed.open is not None and feed.open.text == "half a li"
    t.append(SegmentKind.OUTPUT, "ne")
    t.append(SegmentKind.ERROR, "boom")
    sync(cache, t)
    feed.advance(cache)
    assert shown(feed) == [(SegmentKind.OUTPUT, "half a line\n"), (SegmentKind.ERROR, "boom")]


def test_the_parts_are_one_object_until_a_run_closes_or_the_filter_moves() -> None:
    """
    The seam compares nothing when it is handed the same sequence, and compares
    every part when it is not. A new tuple a frame would be the second, every frame.
    """
    t = Transcript()
    cache = NodeTranscript()
    feed = TypesetFeed()
    t.append(SegmentKind.REASONING, "plan\n")
    t.append(SegmentKind.TOOL_CALL, "Read(x)\n")
    t.append(SegmentKind.OUTPUT, "answer ")
    sync(cache, t)
    feed.advance(cache)
    (held,) = feed.generations
    first = held.parts(True)
    assert len(first) == 2

    for token in ("is ", "forty ", "two\n"):
        t.append(SegmentKind.OUTPUT, token)
        sync(cache, t)
        feed.advance(cache)
        assert held.parts(True) is first

    without = held.parts(False)
    assert [part.text for part in without] == ["Read(x)"]
    assert held.parts(False) is without
    # One run for each part, so a block can be traced to the run it came from.
    assert [run.run for run in held.shown(False)] == [1]

    t.append(SegmentKind.TOOL_CALL, "Bash(ls)\n")
    sync(cache, t)
    feed.advance(cache)
    assert feed.generations == [held]
    grown = held.parts(True)
    assert grown is not first and len(grown) == 3
    # The parts that were there are the same objects: the seam finds them built.
    assert all(a is b for a, b in zip(grown, first, strict=False))


def test_hidden_reasoning_is_left_out_closed_or_open() -> None:
    feed, _, _ = fed(
        (SegmentKind.REASONING, "first thought\n"),
        (SegmentKind.OUTPUT, "said\n"),
        (SegmentKind.REASONING, "second thought, still arriving"),
    )
    assert feed.shown_open(True) is feed.open
    assert feed.shown_open(False) is None
    assert feed.visible_text(True) == "first thought\nsaid\nsecond thought, still arriving"
    assert feed.visible_text(False) == "said"


def test_a_long_transcript_is_typeset_from_its_last_window_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Layout is paid per line on the frame a node is first shown. Unbounded, that
    frame is seconds on a long session; bounded silently, the operator cannot know
    there is more.
    """
    monkeypatch.setattr(transcript_pane, "_TYPESET_WINDOW", 5)
    pairs = [(SegmentKind.OUTPUT, f"paragraph {n}\n") for n in range(8)]
    pairs.insert(4, (SegmentKind.TOOL_CALL, "Read(x)\n"))
    feed, cache, _ = fed(*pairs)
    assert len(cache.lines) == 9
    assert feed.skipped == 4
    # The last five lines: the tool call, then paragraphs 4 to 7.
    assert shown(feed) == [
        (SegmentKind.TOOL_CALL, "Read(x)"),
        (SegmentKind.OUTPUT, "paragraph 4\nparagraph 5\nparagraph 6\nparagraph 7\n"),
    ]
    assert accounted(feed) == 9


def test_a_window_that_starts_inside_a_fence_reopens_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Cut inside a fence, the code would be set as prose and the closing fence would
    open one over everything after it.
    """
    monkeypatch.setattr(transcript_pane, "_TYPESET_WINDOW", 3)
    feed, _, _ = fed(
        (SegmentKind.OUTPUT, "intro\n```python\na = 1\nb = 2\nc = 3\n```\nafter\n"),
    )
    assert feed.skipped == 4
    assert feed.open is not None
    assert feed.open.text == "```python\nc = 3\n```\nafter\n"
    assert open_fence(feed.open.text.split("\n")) is None
    # The line put back to open the fence is not one of the transcript's.
    assert feed.open.lines == 3


def test_a_window_that_starts_outside_a_fence_adds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transcript_pane, "_TYPESET_WINDOW", 2)
    feed, _, _ = fed((SegmentKind.OUTPUT, "```sh\nls\n```\none\ntwo\n"))
    assert feed.open is not None and feed.open.text == "one\ntwo\n"


def test_a_burst_larger_than_the_window_restarts_the_feed_at_its_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A sub-agent's whole message arrives at once. What is skipped must be above
    everything shown, so the row that announces it is true: nothing typeset before
    the burst is kept with a hole after it.
    """
    monkeypatch.setattr(transcript_pane, "_TYPESET_WINDOW", 4)
    feed, cache, t = fed((SegmentKind.OUTPUT, "early\n"), (SegmentKind.TOOL_CALL, "Read(x)\n"))
    assert feed.skipped == 0 and len(feed.runs) == 1
    t.append(SegmentKind.TOOL_RESULT, "".join(f"row {n}\n" for n in range(10)))
    sync(cache, t)
    feed.advance(cache)
    assert feed.skipped == 8
    assert shown(feed) == [(SegmentKind.TOOL_RESULT, "row 6\nrow 7\nrow 8\nrow 9\n")]
    assert feed.generations == []


@pytest.mark.parametrize(
    ("lines", "opener"),
    [
        (["text", "```py", "x"], "```py"),
        (["```py", "x", "```", "after"], None),
        (["~~~", "```", "still code"], "~~~"),
        (["````", "```", "still code"], "````"),
        (["```a`b", "not a fence: a backtick in the info string"], None),
        (["    ```", "indented four: code, not a fence"], None),
        (["```", "x", "``` trailing", "an info string cannot close"], "```"),
    ],
)
def test_open_fence(lines: list[str], opener: str | None) -> None:
    assert open_fence(lines) == opener


# -- the typeset feed: what it holds ---------------------------------------------


def alternating(runs: int) -> list[tuple[SegmentKind, str]]:
    """``runs`` runs, two lines of prose and one of tool call by turns."""
    return [
        (
            (SegmentKind.OUTPUT, f"Paragraph {n}, first line.\nIts second line.\n")
            if n % 2 == 0
            else (SegmentKind.TOOL_CALL, f"Read(file_{n}.py)\n")
        )
        for n in range(runs)
    ]


def test_closed_runs_fill_one_generation_and_then_start_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A generation is what the feed can drop whole, so it must not be the whole feed.
    """
    monkeypatch.setattr(transcript_pane, "_GENERATION_LINES", 6)
    feed, _, _ = fed(*alternating(13))
    # Three lines a pair of runs: a generation takes runs until it holds six.
    assert [[run.run for run in held.runs] for held in feed.generations] == [
        [0, 1, 2, 3],
        [4, 5, 6, 7],
        [8, 9, 10, 11],
    ]
    assert [held.lines for held in feed.generations] == [6, 6, 6]
    assert len({held.key for held in feed.generations}) == 3
    assert feed.held == 18 and accounted(feed) == feed.next_line == 20


def test_the_oldest_generation_is_dropped_past_the_bound_and_its_lines_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The window bounds one ``advance``. Without a bound on the feed, a transcript
    watched while it streams holds every run it was ever shown.
    """
    monkeypatch.setattr(transcript_pane, "_GENERATION_LINES", 6)
    monkeypatch.setattr(transcript_pane, "_TYPESET_KEEP", 12)
    feed, _, _ = fed(*alternating(13))
    oldest = feed.generations[0]
    oldest.extent = 77.0
    # One a call, and not below the bound.
    assert transcript_pane._trim(feed) == 77.0
    assert feed.skipped == 6 and feed.held == 12
    assert oldest not in feed.generations
    assert transcript_pane._trim(feed) == 0.0
    assert len(feed.generations) == 2
    assert accounted(feed) == feed.next_line


def test_a_generation_on_screen_is_kept_until_the_feed_holds_twice_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Dropping the oldest lines takes them from under an operator who scrolled up to
    read them. That is put off, and not for ever.
    """
    monkeypatch.setattr(transcript_pane, "_GENERATION_LINES", 6)
    monkeypatch.setattr(transcript_pane, "_TYPESET_KEEP", 8)
    feed, _, _ = fed(*alternating(9))
    assert feed.held == 12
    feed.generations[0].in_sight = True
    assert transcript_pane._trim(feed) == 0.0 and feed.skipped == 0

    monkeypatch.setattr(transcript_pane, "_TYPESET_KEEP", 5)
    feed.generations[0].extent = 40.0
    assert transcript_pane._trim(feed) == 40.0 and feed.skipped == 6


def test_the_only_generation_is_never_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcript_pane, "_TYPESET_KEEP", 2)
    feed, _, _ = fed(*alternating(5))
    assert len(feed.generations) == 1 and feed.held > 2
    assert transcript_pane._trim(feed) == 0.0
    assert len(feed.generations) == 1


# -- the typeset feed: lifetime --------------------------------------------------


def test_a_feed_outlives_its_line_cache_and_is_not_fed_the_same_text_twice() -> None:
    """
    The line cache is dropped two seconds after a node was last shown and built
    again on return. A feed whose cursor went back to zero with it would hand every
    line to a stream that already has them.
    """
    t = Transcript()
    t.append(SegmentKind.TOOL_CALL, "Read(x)\n")
    t.append(SegmentKind.OUTPUT, "one\ntwo\nthr")
    node: NodeId = ("a", None)
    state = TranscriptState()
    state.prune(1, now=100.0)
    cache = state.cache_for(node)
    sync(cache, t)
    feed = state.feed_for(node)
    feed.advance(cache)
    assert feed.open is not None and feed.open.take() == "one\ntwo\nthr"

    state.prune(2, now=100.0 + transcript_pane._CACHE_KEEP_S + 0.1)
    assert node not in state.caches
    assert state.feeds[node] is feed

    t.append(SegmentKind.OUTPUT, "ee\nfour\n")
    rebuilt = state.cache_for(node)
    assert rebuilt is not cache
    sync(rebuilt, t)
    again = state.feed_for(node)
    assert again is feed
    again.advance(rebuilt)
    assert feed.open.take() == "ee\nfour\n"
    assert feed.open.text == "one\ntwo\nthree\nfour\n"
    assert len(feed.runs) == 1


@pytest.mark.parametrize("fps", [9.0, 60.0, 144.0])
def test_a_feed_and_a_line_cache_are_kept_for_seconds_whatever_the_frame_rate(fps: float) -> None:
    """
    The application idles at nine frames a second and works at sixty. A lifetime
    counted in frames is a minute at one rate and nearly seven at the other.
    """
    state = TranscriptState()
    state.prune(0, now=50.0)
    state.cache_for(("a", None))
    state.feed_for(("a", None))
    gone: dict[str, float] = {}
    frame = 0
    while len(gone) < 2:
        frame += 1
        elapsed = frame / fps
        state.prune(frame, now=50.0 + elapsed)
        if ("a", None) not in state.caches:
            gone.setdefault("cache", elapsed)
        if ("a", None) not in state.feeds:
            gone.setdefault("feed", elapsed)
        assert elapsed < 600.0
    assert gone["cache"] == pytest.approx(transcript_pane._CACHE_KEEP_S, abs=2.0 / fps)
    assert gone["feed"] == pytest.approx(transcript_pane._FEED_KEEP_S, abs=2.0 / fps)


def test_a_node_being_shown_keeps_its_feed_and_one_that_is_not_loses_it() -> None:
    state = TranscriptState()
    state.prune(1, now=10.0)
    state.feed_for(("a", None))
    state.feed_for(("b", None))
    horizon = transcript_pane._FEED_KEEP_S
    state.prune(2, now=10.0 + horizon - 1.0)
    state.feed_for(("a", None))
    state.prune(3, now=10.0 + horizon)
    assert set(state.feeds) == {("a", None), ("b", None)}
    state.prune(4, now=10.0 + horizon + 0.5)
    assert set(state.feeds) == {("a", None)}


def test_the_clock_is_the_monotonic_one_when_none_is_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The application passes a frame number and no clock. The wiring: with the real
    clock a cache shown this frame is kept and one last shown long ago is not.
    """
    state = TranscriptState()
    state.prune(1)
    assert state.now == pytest.approx(time.monotonic(), abs=1.0)
    state.cache_for(("a", None))
    state.caches[("b", None)] = NodeTranscript(last_shown=state.now - 3600.0)
    state.prune(2)
    assert set(state.caches) == {("a", None)}


def test_a_feed_ahead_of_its_lines_starts_again() -> None:
    """
    Fewer lines than were fed is another transcript under the same node. Nothing
    from the old one may stay on screen above the new one.
    """
    feed, _, _ = fed((SegmentKind.OUTPUT, "a\nb\nc\n"), (SegmentKind.TOOL_CALL, "Read(x)\n"))
    assert feed.next_line == 4
    other = Transcript()
    other.append(SegmentKind.OUTPUT, "new\n")
    cache = NodeTranscript()
    sync(cache, other)
    feed.advance(cache)
    assert shown(feed) == [(SegmentKind.OUTPUT, "new\n")]
    assert feed.skipped == 0


# -- the mode --------------------------------------------------------------------


def test_the_log_is_markdown_by_default_with_the_seam_and_wrapped_lines_without(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Until the operator picks a mode the pane picks one, and with the seam off it is
    the one the pane had before there was a seam.
    """
    state = TranscriptState()
    assert state.mode is None
    assert state.shown_mode() is RenderMode.WRAP
    monkeypatch.setattr(typeset, "enabled", lambda pane=None: True)
    assert state.shown_mode() is transcript_pane.DEFAULT_MODE
    assert transcript_pane.DEFAULT_MODE in (RenderMode.RICH, RenderMode.TYPESET)
    assert state.mode is None, "the default is derived and never written back"


def test_a_chosen_mode_is_drawn_and_typeset_only_while_the_seam_can_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Without the seam TYPESET is WRAP. The operator's choice is not overwritten by
    that, so the seam coming back returns them to it.
    """
    state = TranscriptState(mode=RenderMode.TYPESET)
    assert state.shown_mode() is RenderMode.WRAP
    monkeypatch.setattr(typeset, "enabled", lambda pane=None: True)
    assert state.shown_mode() is RenderMode.TYPESET
    assert state.mode is RenderMode.TYPESET
    for chosen in (RenderMode.RAW, RenderMode.WRAP, RenderMode.RICH):
        state.mode = chosen
        assert state.shown_mode() is chosen
        monkeypatch.setattr(typeset, "enabled", lambda pane=None: False)
        assert state.shown_mode() is chosen
        monkeypatch.setattr(typeset, "enabled", lambda pane=None: True)


# -- the pane, drawn -------------------------------------------------------------

ROOT: NodeId = ("s1", None)
LAST_LINE = "should that stay with its caller?"

SETTLE_FRAMES = 6
"""
Frames enough, at the rig's sixty a second, for the open run's stream to have
read everything it was given (``_STREAM_EVERY_MS``) and for a run that closed to
have been swapped for its part.
"""


def session(transcript: Transcript) -> Snapshot:
    """One root session."""
    store = Store()
    store.apply(
        AgentSpawned(
            node_id=ROOT,
            parent=None,
            task="the session the test draws",
            model="claude-sonnet-5",
            started_at=0.0,
            cwd="/x",
            transcript=transcript,
        )
    )
    return store.snapshot()


def typeset_state(**fields: Any) -> TranscriptState:
    return TranscriptState(mode=RenderMode.TYPESET, **fields)


def open_stream(state: TranscriptState) -> Any:
    feed = state.feeds[ROOT]
    assert feed.open is not None
    return typeset.stream(feed.views, ("open", feed.open.run))


def report() -> Transcript:
    """The fake fleet's settled session: every kind, and the markdown report."""
    transcript = Transcript()
    for kind, text in fake_driver._REPORT_SCRIPT:
        transcript.append(kind, text)
    return transcript


def draw(im: Any, snap: Snapshot, state: TranscriptState) -> None:
    rig.frame(im, lambda: transcript_pane.draw(snap, state, ROOT))


def block_texts(feed: TypesetFeed) -> list[str]:
    """
    The text of every block of every view the feed holds: the generations in
    order, then the runs with views of their own, oldest first.
    """
    out: list[str] = []
    closed = [held.key for held in feed.generations if held.key in feed.views]
    own = sorted(
        (key for key in feed.views.keys() if key not in closed),
        key=lambda key: (key[1], key[0] == "tail"),  # type: ignore[index]
    )
    for key in [*closed, *own]:
        view = feed.views.view(key)
        out.extend(view.block_text(n) for n in range(len(view.document.blocks)))
    return out


def handing_over(feed: TypesetFeed) -> list[int]:
    """The runs whose parts their documents have not finished building."""
    return [run.run for held in feed.generations for run in held.runs[held.settled :]]


def paragraphs(count: int, tag: str) -> str:
    """``count`` paragraphs of three source lines each, no two alike."""
    return "".join(
        f"Paragraph {n} of {tag}, long enough to be broken over more than one line of the pane.\n"
        f"Its second line says a little more about {tag} and about {n}.\n\n"
        for n in range(count)
    )


def test_the_settled_report_is_two_views_and_shows_all_of_it(seam: Any) -> None:
    """
    Closed runs in one document, the open run in one stream: not a view per run.
    And nothing a markdown reader would hide is hidden: the report has a tag, a
    generic, an unclosed comment opener and a ``<details>`` line with more below it.
    """
    state = typeset_state()
    snap = session(report())
    draw(seam, snap, state)
    draw(seam, snap, state)
    feed = state.feeds[ROOT]
    (held,) = feed.generations
    assert feed.open is not None
    assert set(feed.views.keys()) == {held.key, ("open", feed.open.run)}
    assert feed.skipped == 0 and handing_over(feed) == []
    for key in feed.views.keys():
        glyphs, missing = feed.views.drawn(key) or (0, 0)
        assert glyphs > 0, key
    text = "\n".join(block_texts(feed))
    for needle in ("<lead>", "Optional<int>", "<!--", "<details>", LAST_LINE):
        assert needle in text, needle
    assert text.rstrip().endswith(LAST_LINE)


def test_identifiers_keep_their_underscores_and_dollars_are_not_math(seam: Any) -> None:
    """
    ``__init__`` set as a bold "init" is the defect the old renderer disabled
    underscore emphasis to avoid, and ``$PATH=$HOME`` is a shell line, not a formula.
    """
    state = typeset_state()
    draw(seam, session(report()), state)
    text = "\n".join(block_texts(state.feeds[ROOT]))
    for needle in ("__init__", "__post_init__", "foo_bar_baz", "parse_line_1", "$PATH=$HOME/bin"):
        assert needle in text, needle


def test_a_report_streamed_a_token_a_frame_ends_as_the_settled_one(seam: Any) -> None:
    """
    The same blocks, once each, whether the report was there on the first frame or
    arrived in pieces across four hundred of them.
    """
    settled = typeset_state()
    draw(seam, session(report()), settled)

    transcript = Transcript()
    snap = session(transcript)
    streamed = typeset_state()
    for kind, text in fake_driver._REPORT_SCRIPT:
        step = 23 if kind in (SegmentKind.OUTPUT, SegmentKind.REASONING) else len(text)
        for start in range(0, len(text), step):
            transcript.append(kind, text[start : start + step])
            draw(seam, snap, streamed)
    for _ in range(SETTLE_FRAMES):
        draw(seam, snap, streamed)
    feed = streamed.feeds[ROOT]
    assert handing_over(feed) == []
    assert block_texts(feed) == block_texts(settled.feeds[ROOT])
    assert LAST_LINE in block_texts(feed)[-1]
    assert accounted(feed) == feed.next_line


def test_returning_to_a_pruned_node_keeps_its_views_and_feeds_nothing_twice(seam: Any) -> None:
    state = typeset_state()
    snap = session(report())
    state.prune(1, now=5.0)
    draw(seam, snap, state)
    feed = state.feeds[ROOT]
    before = block_texts(feed)
    held = {key: feed.views.view(key) for key in feed.views.keys()}

    state.prune(2, now=5.0 + transcript_pane._CACHE_KEEP_S + 0.1)
    assert ROOT not in state.caches
    draw(seam, snap, state)
    assert state.feeds[ROOT] is feed
    assert {key: feed.views.view(key) for key in feed.views.keys()} == held
    assert block_texts(feed) == before


def test_the_stream_does_not_scroll_the_pane_follow_has_one_owner(seam: Any) -> None:
    """
    ``widgets.follow_tail`` pins the pane and lets go on an upward wheel. A stream
    that also followed would pull the view back on the frame the pane let go.
    """
    state = typeset_state()
    draw(seam, session(report()), state)
    assert open_stream(state).following is False


def test_hiding_reasoning_redraws_without_it_and_keeps_the_stream_fed(seam: Any) -> None:
    transcript = Transcript()
    transcript.append(SegmentKind.REASONING, "A closed thought.\n")
    transcript.append(SegmentKind.OUTPUT, "Said aloud.\n")
    transcript.append(SegmentKind.REASONING, "An open thou")
    snap = session(transcript)
    state = typeset_state(show_reasoning=False)
    draw(seam, snap, state)
    feed = state.feeds[ROOT]
    (held,) = feed.generations
    closed = feed.views.view(held.key)
    assert [closed.block_text(n) for n in range(len(closed.document.blocks))] == ["Said aloud."]

    transcript.append(SegmentKind.REASONING, "ght, finished.\n")
    draw(seam, snap, state)
    state.show_reasoning = True
    draw(seam, snap, state)
    assert block_texts(feed) == ["A closed thought.", "Said aloud.", "An open thought, finished."]


def test_a_search_demotes_the_typeset_log_to_lines(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The seam has no find. A filter that changed nothing on screen would read as
    "no matches hidden"; the line path is the one that can honour it.
    """
    calls: list[str] = []

    def spy(name: str) -> Any:
        real = getattr(typeset, name)

        def called(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            return real(*args, **kwargs)

        return called

    for name in ("document", "stream", "plain"):
        monkeypatch.setattr(typeset, name, spy(name))
    state = typeset_state(search="checksum")
    snap = session(report())
    draw(seam, snap, state)
    assert calls == [] and state.feeds == {}
    state.search = ""
    draw(seam, snap, state)
    assert "document" in calls and "stream" in calls


def test_with_the_seam_off_the_pane_draws_as_it_did(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The other half of the wiring: nothing reaches the seam, no feed is made, and
    the mode drawn is WRAP, for a state nobody chose a mode in and for one left in
    TYPESET.
    """
    calls: list[str] = []
    for name in ("document", "stream", "plain"):
        monkeypatch.setattr(typeset, name, lambda *a, _name=name, **k: calls.append(_name))
    wrapped: list[str] = []
    real = imgui.text_wrapped

    def text_wrapped(text: str) -> None:
        wrapped.append(text)
        real(text)

    monkeypatch.setattr(imgui, "text_wrapped", text_wrapped)
    for state in (TranscriptState(), typeset_state()):
        wrapped.clear()
        draw(imgui, session(report()), state)
        assert calls == [] and state.feeds == {}
        assert state.shown_mode() is RenderMode.WRAP
        assert any(LAST_LINE in text for text in wrapped)


def test_a_right_click_offers_the_block_and_the_run_under_the_mouse_and_the_selection(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The menu is built from what the views say is under the mouse, latched while the
    popup is closed: opening it moves the hover onto the popup. The run is copied
    from the lines, whole, as the line modes copy it.
    """
    state = typeset_state(follow_tail=False)
    snap = session(report())
    io = seam.get_io()
    offered: list[str] = []
    copied: list[str] = []
    monkeypatch.setattr(seam, "set_clipboard_text", copied.append)
    try:
        io.add_mouse_pos_event(80.0, 140.0)
        for _ in range(3):
            draw(seam, snap, state)
        feed = state.feeds[ROOT]
        (held,) = feed.generations
        assert state.typeset_target is not None
        key, block = state.typeset_target
        pointed = feed.views.view(key).block_text(block)
        assert pointed and pointed in block_texts(feed)
        run = transcript_pane._typeset_run(feed, state.typeset_target, True)
        assert run is not None
        cache = state.caches[ROOT]
        start, end = cache.run_bounds(run)
        assert pointed.split()[0] in cache.run_text(run)
        assert not any(pointed.split()[0] in cache.run_text(n) for n in range(run))
        kind = cache.lines[start].kind.value.replace("_", " ")
        run_item = f"copy {kind} ({end - start} line{'' if end - start == 1 else 's'})"

        feed.views.view(held.key).select_all()
        io.add_mouse_button_event(1, True)
        draw(seam, snap, state)
        io.add_mouse_button_event(1, False)
        draw(seam, snap, state)

        def labelled(pick: str | None = None) -> None:
            real = seam.menu_item_simple

            def item(label: str) -> bool:
                offered.append(label)
                return bool(real(label)) or label == pick

            seam.menu_item_simple = item
            try:
                transcript_pane.draw(snap, state, ROOT)
            finally:
                seam.menu_item_simple = real

        rig.frame(seam, labelled)
        assert offered == ["copy selection", "copy block", run_item, "copy visible"]
        # Latched: the target is the block that was clicked, not the popup's hover.
        assert state.typeset_target == (key, block)
        rig.frame(seam, lambda: labelled(run_item))
        assert copied == [cache.run_text(run)]
    finally:
        io.add_mouse_button_event(1, False)
        io.add_mouse_pos_event(-1.0, -1.0)
        left = state.feeds.get(ROOT)
        for held in left.generations if left is not None else []:
            if held.key in left.views:
                left.views.view(held.key).clear_selection()
        # A popup nobody submits is closed by ImGui; later tests start without one.
        for _ in range(2):
            rig.frame(seam, lambda: None)


def test_a_block_is_traced_to_its_run_through_the_reasoning_filter(seam: Any) -> None:
    """
    The document holds only the parts the filter lets through, so its part numbers
    are not the generation's run numbers.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.REASONING, "A thought.\n")
    transcript.append(SegmentKind.OUTPUT, "Said aloud.\n")
    transcript.append(SegmentKind.TOOL_CALL, "Read(x)\n")
    transcript.append(SegmentKind.OUTPUT, "open")
    state = typeset_state(show_reasoning=False)
    snap = session(transcript)
    draw(seam, snap, state)
    draw(seam, snap, state)
    feed = state.feeds[ROOT]
    (held,) = feed.generations
    assert feed.views.view(held.key).block_text(0) == "Said aloud."
    assert transcript_pane._typeset_run(feed, (held.key, 0), False) == 1
    assert transcript_pane._typeset_run(feed, (held.key, 1), False) == 2
    assert transcript_pane._typeset_run(feed, (held.key, 7), False) is None
    assert transcript_pane._typeset_run(feed, (("open", 3), 0), False) == 3
    state.show_reasoning = True
    draw(seam, snap, state)
    assert transcript_pane._typeset_run(feed, (held.key, 0), True) == 0


def test_an_open_run_shows_its_last_character_and_is_not_closed(seam: Any) -> None:
    """
    A turn ends on "All done!" and the session then waits, for as long as it waits.
    The stream is left open -- the same run can go on -- and shows all of it.
    """
    for said in ("All done!", "see <", "a `"):
        transcript = Transcript()
        transcript.append(SegmentKind.OUTPUT, said)
        state = typeset_state()
        snap = session(transcript)
        for _ in range(40):
            draw(seam, snap, state)
        assert not open_stream(state).closed
        assert block_texts(state.feeds[ROOT]) == [said]


def test_a_run_that_goes_on_after_a_pause_is_appended_to_and_never_replayed(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Replaying a stream is its whole layout again. An open run is only ever
    appended to, however long it sat.
    """
    replays: list[int] = []
    real = typeset.Stream._replay
    monkeypatch.setattr(
        typeset.Stream, "_replay", lambda self, text: (replays.append(len(text)), real(self, text))
    )
    transcript = Transcript()
    state = typeset_state()
    snap = session(transcript)
    transcript.append(SegmentKind.OUTPUT, "First sentence!")
    for _ in range(30):
        draw(seam, snap, state)
    handle = open_stream(state)
    transcript.append(SegmentKind.OUTPUT, " Second sentence.\n\nA new paragraph")
    draw(seam, snap, state)
    assert open_stream(state) is handle
    assert block_texts(state.feeds[ROOT]) == ["First sentence! Second sentence.", "A new paragraph"]
    transcript.append(SegmentKind.TOOL_CALL, "Read(x)\n")
    for _ in range(3):
        draw(seam, snap, state)
    assert replays == []
    assert block_texts(state.feeds[ROOT]) == [
        "First sentence! Second sentence.",
        "A new paragraph",
        "Read(x)",
    ]


def test_text_that_arrives_between_two_readings_waits_for_the_next_and_no_longer(
    seam: Any,
) -> None:
    """
    The open run's stream lays the block still arriving out again at each reading,
    so it reads every ``_STREAM_EVERY_MS`` and not every frame. What arrives in
    between is late by that much and no more, and frames keep coming until it is
    on screen: the application does not idle on ``typeset.pending()``.
    """
    every = transcript_pane._STREAM_EVERY_MS / 1000.0
    late = int(every * 60.0) + 1
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "Word 0")
    state = typeset_state()
    snap = session(transcript)
    draw(seam, snap, state)
    feed = state.feeds[ROOT]
    # The first reading is of whatever is there: nothing waits to be seen at all.
    assert block_texts(feed) == ["Word 0"]

    behind: list[int] = []
    for n in range(1, 31):
        transcript.append(SegmentKind.OUTPUT, f" {n}")
        draw(seam, snap, state)
        (text,) = block_texts(feed)
        behind.append(n - int(text.split()[-1]))
        assert bool(feed.views.unbuilt(("open", 0))) == (behind[-1] > 0)
        assert typeset.pending() == (behind[-1] > 0)
    assert behind[0] == 1, "read in the frame after a reading: nothing is throttled"
    assert max(behind) <= late
    # Twenty readings a second, at most, of thirty frames at sixty.
    assert behind.count(0) <= 30 / (late - 1) + 1

    for _ in range(late):
        draw(seam, snap, state)
    assert block_texts(feed) == ["Word 0 " + " ".join(str(n) for n in range(1, 31))]
    assert not typeset.pending()
    assert not open_stream(state).closed


def test_a_stream_opened_on_a_long_run_reads_it_a_piece_a_frame(seam: Any) -> None:
    """
    The first frame on a session whose tail is one long markdown run. A stream reads
    a piece and then whatever the frame's free allowance covers, which is the frame
    of 50 ms the pane declines: one piece, and the rest over the frames after.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, paragraphs(60, "the tail"))
    state = typeset_state()
    snap = session(transcript)
    typeset.set_budget(0.0)
    draw(seam, snap, state)
    feed = state.feeds[ROOT]
    # 180 lines, less a piece of forty, and one for "something is held".
    assert feed.views.unbuilt(("open", 0)) == 180 - 40 + 1
    assert typeset.pending()
    frames = 1
    while feed.views.unbuilt(("open", 0)):
        draw(seam, snap, state)
        frames += 1
        assert frames < 20
    assert frames >= 5
    texts = block_texts(feed)
    assert [text.startswith(f"Paragraph {n} of the tail") for n, text in enumerate(texts)] == [
        True
    ] * 60


def test_two_identical_runs_are_both_drawn_and_each_can_be_selected(seam: Any) -> None:
    """
    An agent reads the same file twice and says the same sentence twice. The seam
    builds equal parts once; the log still has two of each, each with its own
    place and its own stretch of the selection.
    """

    def drawn(first: str, second: str) -> tuple[TypesetFeed, Any]:
        transcript = Transcript()
        for said in (first, second):
            transcript.append(SegmentKind.OUTPUT, said)
            transcript.append(SegmentKind.TOOL_CALL, "Read(file_path=same.py)\n")
        transcript.append(SegmentKind.OUTPUT, "the end")
        state = typeset_state()
        snap = session(transcript)
        for _ in range(3):
            draw(seam, snap, state)
        feed = state.feeds[ROOT]
        (held,) = feed.generations
        return feed, held

    said = "The same sentence, said again.\n"
    feed, held = drawn(said, said)
    view = feed.views.view(held.key)
    assert [view.block_text(n) for n in range(len(view.document.blocks))] == [
        "The same sentence, said again.",
        "Read(file_path=same.py)",
        "The same sentence, said again.",
        "Read(file_path=same.py)",
    ]
    # Four lines, each lower than the last, and as tall together as four that differ.
    baselines = [line.baseline for line in view.set_lines()]
    assert len(baselines) == 4 and baselines == sorted(set(baselines))
    other, other_held = drawn(said, "A different one, said only once.\n")
    assert held.height == pytest.approx(other_held.height, abs=0.01)
    assert other.views.view(other_held.key).document.blocks[0] is not view.document.blocks[2]
    # Each of the four is its own part, and the second pair selects apart from the first.
    assert [feed.views.part_at(held.key, n) for n in range(5)] == [0, 1, 2, 3, None]
    assert [transcript_pane._typeset_run(feed, (held.key, n), True) for n in range(4)] == [
        0,
        1,
        2,
        3,
    ]
    spans = [view.layout.indices_of_block(n) for n in range(4)]
    assert all(a.stop <= b.start for a, b in zip(spans, spans[1:], strict=False))
    try:
        for n in (2, 3):
            view.select(type(view.selection)(spans[n].start, spans[n].stop))
            assert view.selected_text() == view.block_text(n)
            assert view.selected_blocks() == (n,)
    finally:
        view.clear_selection()


def test_an_open_literal_run_builds_its_lines_once_a_line(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``plain`` builds the end of a text again whenever the text changes. A result
    arriving a token at a time must pay that once a line and not once a token, and
    the line still arriving must be on screen all the same.
    """
    texts: dict[Any, list[str]] = {}
    real = typeset.plain

    def plain(cache: Any, key: Any, text: str, **kwargs: Any) -> float:
        seen = texts.setdefault(key, [])
        if not seen or seen[-1] != text:
            seen.append(text)
        return real(cache, key, text, **kwargs)

    monkeypatch.setattr(typeset, "plain", plain)
    transcript = Transcript()
    state = typeset_state()
    snap = session(transcript)
    transcript.append(SegmentKind.OUTPUT, "Looking.\n")
    for token in ("\nrow one, ", "first half", " and second\n", "row two", ", more", "\n", "row 3"):
        transcript.append(SegmentKind.TOOL_RESULT, token)
        draw(seam, snap, state)
        draw(seam, snap, state)
    feed = state.feeds[ROOT]
    assert texts[("open", 1)] == [
        "row one, first half and second",
        "row one, first half and second\nrow two, more",
    ]
    assert texts[("tail", 1)] == [
        "row one, ",
        "row one, first half",
        "row two",
        "row two, more",
        "row 3",
    ]
    assert block_texts(feed) == [
        "Looking.",
        "row one, first half and second\nrow two, more",
        "row 3",
    ]

    transcript.append(SegmentKind.OUTPUT, "Done.")
    for _ in range(3):
        draw(seam, snap, state)
    assert block_texts(feed) == [
        "Looking.",
        "row one, first half and second\nrow two, more\nrow 3",
        "Done.",
    ]
    assert ("tail", 1) not in feed.views and ("open", 1) not in feed.views


# -- the pane, drawn: a run that closes --------------------------------------------


def run_tops(monkeypatch: pytest.MonkeyPatch, seam: Any) -> dict[tuple[int, bool], list[float]]:
    """
    Where each run's own view is drawn, on screen, frame by frame: by (run, closed).
    """
    tops: dict[tuple[int, bool], list[float]] = {}
    real = transcript_pane._draw_run

    def spy(views: Any, run: int, *args: Any, closed: bool = False) -> None:
        tops.setdefault((run, closed), []).append(seam.get_cursor_screen_pos().y)
        real(views, run, *args, closed=closed)

    monkeypatch.setattr(transcript_pane, "_draw_run", spy)
    return tops


def test_a_long_run_that_closes_stays_on_screen_until_its_part_is_built(
    seam: Any, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """
    The part of a long run is built over several frames, and a document shows what
    it has built so far. Swapped in on the frame the run closed, the run's text
    would go and come back a piece at a time.

    While it waits the run is what is under the mouse there, not the part being
    built beneath it out of sight.
    """
    tops = run_tops(monkeypatch, seam)
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "A few words first.\n")
    transcript.append(SegmentKind.TOOL_CALL, "Read(x)\n")
    # The mouse rests on the long run, well below the two short ones.
    seam.get_io().add_mouse_pos_event(80.0, 300.0)
    request.addfinalizer(lambda: seam.get_io().add_mouse_pos_event(-1.0, -1.0))
    # Not following: the document stays at the top of the pane, in sight.
    state = typeset_state(follow_tail=False)
    snap = session(transcript)
    for tag in ("the long run", "its middle", "its end"):
        transcript.append(SegmentKind.OUTPUT, paragraphs(40, tag))
        draw(seam, snap, state)
    transcript.append(SegmentKind.OUTPUT, "```py\nnever_closed = 1\n")
    for _ in range(3):
        draw(seam, snap, state)
    feed = state.feeds[ROOT]
    (held,) = feed.generations
    settled_height = held.height
    assert handing_over(feed) == [] and settled_height > 0.0
    assert not open_stream(state).closed

    clips: list[float] = []
    real = seam.push_clip_rect

    def push_clip_rect(low: Any, high: Any, intersect: bool) -> None:
        clips.append(high.y - low.y)
        real(low, high, intersect)

    monkeypatch.setattr(seam, "push_clip_rect", push_clip_rect)
    typeset.set_budget(0.0)
    transcript.append(SegmentKind.TOOL_CALL, "Bash(ls)\n")
    frames = 0
    while True:
        clips.clear()
        draw(seam, snap, state)
        frames += 1
        assert frames < 60
        # The document is drawn no further down than it reached before the run
        # closed: what it has built of the run so far is under the run's own view.
        assert clips.count(pytest.approx(settled_height, abs=0.01)) == 1
        if handing_over(feed) == []:
            break
        # Still its own view, closed, and the document has not been let grow.
        assert typeset.stream(feed.views, ("open", 2)).closed
        assert held.height == settled_height
        assert state.typeset_target is not None and state.typeset_target[0] == ("open", 2)
    assert frames > 2, "one frame's allowance built it: the test did not reach the waiting"
    assert feed.generations == [held]
    # Drawn every frame of the wait, where it was while it was open.
    assert len(tops[(2, True)]) == frames
    assert set(tops[(2, True)]) == {tops[(2, False)][-1]}

    clips.clear()
    draw(seam, snap, state)
    assert ("open", 2) not in feed.views
    assert held.height > settled_height
    assert state.typeset_target is not None and state.typeset_target[0] == held.key
    assert pytest.approx(settled_height, abs=0.01) not in clips
    texts = block_texts(feed)
    assert texts.count("never_closed = 1") == 1
    for tag in ("the long run", "its middle", "its end"):
        assert sum(text.startswith(f"Paragraph 39 of {tag}") for text in texts) == 1
    assert texts[-1] == "Bash(ls)"


def test_a_run_of_ordinary_length_that_closes_is_built_a_small_piece_a_frame(
    seam: Any,
) -> None:
    """
    Sixty lines are inside the seam's free allowance, which builds them in the frame
    the run closes whatever the budget: the one long frame of a stream that is
    otherwise short ones. The run has a view until its part is whole, so the pane
    can wait, and it asks for the part without that allowance.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, paragraphs(20, "the report"))
    state = typeset_state(follow_tail=False)
    snap = session(transcript)
    for _ in range(SETTLE_FRAMES):
        draw(seam, snap, state)
    typeset.set_budget(0.0)
    transcript.append(SegmentKind.TOOL_CALL, "Bash(ls)\n")
    feed = state.feeds[ROOT]
    left: list[int] = []
    while not left or left[-1]:
        draw(seam, snap, state)
        (held,) = feed.generations
        left.append(feed.views.unbuilt(held.key) or 0)
        assert len(left) < 20
        if left[-1]:
            assert handing_over(feed) == [0]
            assert typeset.stream(feed.views, ("open", 0)).closed
    # Never more than half a piece of markdown in one frame.
    assert len(left) >= 3
    assert all(a - b <= 21 for a, b in zip(left, left[1:], strict=False))
    draw(seam, snap, state)
    assert handing_over(feed) == [] and ("open", 0) not in feed.views
    texts = block_texts(feed)
    assert sum(text.startswith("Paragraph 19 of the report") for text in texts) == 1
    assert texts[-1] == "Bash(ls)"


def test_a_run_that_closes_with_its_document_out_of_sight_is_handed_over_all_the_same(
    seam: Any,
) -> None:
    """
    A whole document that has scrolled away builds nothing unless it is asked to,
    the seam's rule. Not asked, the run that pushed it away would keep its own view
    for as long as the operator followed the tail, and so would every run after it.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "A few words first.\n")
    transcript.append(SegmentKind.TOOL_CALL, "Read(x)\n")
    transcript.append(SegmentKind.OUTPUT, paragraphs(40, "the tall run"))
    state = typeset_state()
    snap = session(transcript)
    for _ in range(3):
        draw(seam, snap, state)
    feed = state.feeds[ROOT]
    (first,) = feed.generations
    assert not first.in_sight, "the open run does not fill the pane: nothing would be tested"

    transcript.append(SegmentKind.TOOL_CALL, "Bash(ls)\n")
    transcript.append(SegmentKind.OUTPUT, "And on.")
    draw(seam, snap, state)
    assert not first.in_sight
    draw(seam, snap, state)
    assert handing_over(feed) == []
    assert feed.generations == [first] and [run.run for run in first.runs] == [0, 1, 2, 3]
    assert set(feed.views.keys()) == {first.key, ("open", 4)}
    texts = block_texts(feed)
    assert sum(text.startswith("Paragraph 39 of the tall run") for text in texts) == 1
    assert texts[-2:] == ["Bash(ls)", "And on."]


def test_runs_closing_every_frame_behind_a_document_out_of_sight_do_not_pile_up(
    seam: Any,
) -> None:
    """
    Each run that closes is in its document the frame after, wherever the document
    is: one view of its own at a time, and no generation started to hold them.

    And the document is given the room of every run it has settled, in the frame
    it settles them: clipped to what it was before the first of them closed, runs
    that closed a frame apart would be in it and never be seen.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "A few words first.\n")
    transcript.append(SegmentKind.TOOL_CALL, "Read(x)\n")
    transcript.append(SegmentKind.OUTPUT, paragraphs(40, "the tall run"))
    state = typeset_state()
    snap = session(transcript)
    for _ in range(3):
        draw(seam, snap, state)
    feed = state.feeds[ROOT]
    (held,) = feed.generations
    own: list[int] = []
    rooms: list[float] = [held.room]
    for kind, text in alternating(24)[1:]:
        transcript.append(kind, text)
        draw(seam, snap, state)
        own.append(len(feed.views) - len(feed.generations))
        rooms.append(held.room)
    assert max(own) <= 3
    assert feed.generations == [held]
    assert all(later > earlier for earlier, later in zip(rooms, rooms[1:], strict=False))


def test_a_hidden_reasoning_run_that_closes_is_not_drawn_while_it_is_handed_over(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Its stream is kept and fed while it is open and hidden, so it has a view of its
    own when it closes: the one thing between the filter and the text.
    """
    tops = run_tops(monkeypatch, seam)
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "Said first.\n")
    transcript.append(SegmentKind.REASONING, "A thought nobody asked to see")
    state = typeset_state(show_reasoning=False)
    snap = session(transcript)
    draw(seam, snap, state)
    feed = state.feeds[ROOT]
    assert ("open", 1) in feed.views and (1, False) not in tops
    typeset.set_budget(0.0)
    transcript.append(SegmentKind.REASONING, ", and more of it.\n")
    transcript.append(SegmentKind.OUTPUT, "Said after.")
    for _ in range(4):
        draw(seam, snap, state)
        # Never drawn by a view of its own, open or closed.
        assert not any(run == 1 for run, _ in tops)
    assert block_texts(feed) == ["Said first.", "Said after."]


# -- the pane, drawn: a watched transcript -----------------------------------------


def watched(
    seam: Any, state: TranscriptState, runs: int, said: list[str] | None = None
) -> tuple[Snapshot, Transcript]:
    """A transcript drawn as each of ``runs`` runs arrives, and twice more."""
    transcript = Transcript()
    snap = session(transcript)
    for kind, text in alternating(runs):
        transcript.append(kind, text)
        draw(seam, snap, state)
    for _ in range(SETTLE_FRAMES):
        draw(seam, snap, state)
    return snap, transcript


def test_a_transcript_watched_while_it_streams_stays_bounded_and_says_what_it_dropped(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transcript_pane, "_GENERATION_LINES", 12)
    monkeypatch.setattr(transcript_pane, "_TYPESET_KEEP", 60)
    said: list[str] = []
    real = seam.text_disabled
    monkeypatch.setattr(seam, "text_disabled", lambda text: (said.append(text), real(text))[1])
    state = typeset_state()
    held: list[int] = []
    own: list[int] = []
    transcript = Transcript()
    snap = session(transcript)
    for kind, text in alternating(200):
        transcript.append(kind, text)
        draw(seam, snap, state)
        feed = state.feeds[ROOT]
        held.append(feed.held)
        own.append(len(feed.views) - len(feed.generations))
    # Never twice the bound, and the bound itself once the oldest lines have
    # scrolled out of the pane.
    assert max(held) <= 2 * 60
    assert max(held[100:]) <= 60 + 2
    # A view for each generation; two for an open literal run; and one for the
    # run that closed in the frame before.
    assert max(own) <= 3
    assert len(feed.generations) <= 62 // 12 + 1
    assert feed.skipped >= 300 - 62
    assert accounted(feed) == feed.next_line == 300
    assert said[-1] == f"{feed.skipped:,} earlier lines: switch to raw"
    # What is left is the end of the transcript, in order and once.
    for _ in range(SETTLE_FRAMES):
        draw(seam, snap, state)
    assert handing_over(feed) == []
    texts = block_texts(feed)
    assert texts[-1] == "Read(file_199.py)"
    numbers = [int(text.split()[1].rstrip(",")) for text in texts if text.startswith("Paragraph")]
    assert numbers == list(range(numbers[0], 200, 2)) and len(numbers) >= 8


@pytest.mark.parametrize("following", [True, False])
def test_dropping_a_generation_moves_nothing_on_screen(
    seam: Any, monkeypatch: pytest.MonkeyPatch, following: bool
) -> None:
    """
    The dropped lines are above the pane and take their room with them. The scroll
    position a frame is drawn at is the last frame's, so for one frame everything
    below would sit that much too high: a blink, each time.
    """
    monkeypatch.setattr(transcript_pane, "_GENERATION_LINES", 12)
    state = typeset_state()
    snap, _ = watched(seam, state, 61)
    feed = state.feeds[ROOT]
    assert len(feed.generations) >= 5 and feed.skipped == 0

    if not following:
        # Scrolled up to somewhere in the middle, and left there.
        state.follow_tail = False
        real = transcript_pane._handle_scroll
        monkeypatch.setattr(
            transcript_pane,
            "_handle_scroll",
            lambda state: seam.set_scroll_y(seam.get_scroll_max_y() * 0.6),
        )
        draw(seam, snap, state)
        monkeypatch.setattr(transcript_pane, "_handle_scroll", real)
        for _ in range(2):
            draw(seam, snap, state)
        assert state.follow_tail is False
        assert not feed.generations[0].in_sight

    tops = run_tops(monkeypatch, seam)
    for _ in range(2):
        draw(seam, snap, state)
    monkeypatch.setattr(transcript_pane, "_TYPESET_KEEP", feed.held - 1)
    for _ in range(4):
        draw(seam, snap, state)
    assert feed.skipped > 0 and len(feed.generations) >= 3
    (open_run,) = tops
    assert len(tops[open_run]) == 6
    assert tops[open_run] == pytest.approx([tops[open_run][0]] * 6, abs=0.01)
    assert state.follow_tail is following


def test_a_generation_dropped_as_a_line_arrives_leaves_the_tail_pinned(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Following, the end of the text sits at the bottom of the pane one frame after
    it grows. A drop in the frame it grew must not cost a second one.
    """
    monkeypatch.setattr(transcript_pane, "_GENERATION_LINES", 12)
    state = typeset_state()
    snap, transcript = watched(seam, state, 61)
    feed = state.feeds[ROOT]
    ends: list[float] = []
    real = transcript_pane._handle_scroll
    monkeypatch.setattr(
        transcript_pane,
        "_handle_scroll",
        lambda state: (ends.append(seam.get_cursor_screen_pos().y), real(state))[1],
    )
    for _ in range(2):
        draw(seam, snap, state)
    transcript.append(SegmentKind.OUTPUT, "\nA paragraph more.\n\nAnd another.\n")
    monkeypatch.setattr(transcript_pane, "_TYPESET_KEEP", feed.held - 1)
    for _ in range(3):
        draw(seam, snap, state)
    assert feed.skipped > 0
    pinned, _, grown, after, later = ends
    assert grown > pinned + 20.0, "the text did not grow in the frame of the drop"
    assert after == later == pinned


# -- the pane, drawn: the controls -------------------------------------------------


def sub_agent(transcript: Transcript) -> tuple[Snapshot, NodeId]:
    """A root session and one sub-agent of it, whose transcript this is."""
    store = Store()
    node: NodeId = ("s1", "agent-1")
    for node_id, parent in ((ROOT, None), (node, ROOT)):
        store.apply(
            AgentSpawned(
                node_id=node_id,
                parent=parent,
                task="the session the test draws",
                model="claude-sonnet-5",
                started_at=0.0,
                cwd="/x",
                transcript=transcript if parent is not None else Transcript(),
            )
        )
    return store.snapshot(), node


def said_at(
    im: Any, monkeypatch: pytest.MonkeyPatch, state: TranscriptState, snap: Snapshot, node: NodeId
) -> dict[str, float]:
    """Every dim or coloured native line of one frame, and how far in it started."""
    notes: dict[str, float] = {}
    disabled, coloured = im.text_disabled, im.text_colored
    monkeypatch.setattr(
        im,
        "text_disabled",
        lambda text: (notes.setdefault(text, im.get_cursor_pos_x()), disabled(text))[1],
    )
    monkeypatch.setattr(
        im,
        "text_colored",
        lambda ink, text: (notes.setdefault(text, im.get_cursor_pos_x()), coloured(ink, text))[1],
    )
    rig.frame(im, lambda: transcript_pane.draw(snap, state, node))
    monkeypatch.setattr(im, "text_disabled", disabled)
    monkeypatch.setattr(im, "text_colored", coloured)
    return notes


SUB_AGENT_NOTE = "sub-agent: complete messages only, not live"
RICH_NOTE = "(rich: search falls back to raw lines)"


@pytest.mark.parametrize("mode", [RenderMode.RICH, RenderMode.TYPESET])
def test_with_the_seam_on_the_notes_have_rows_of_their_own(
    seam: Any, monkeypatch: pytest.MonkeyPatch, mode: RenderMode
) -> None:
    """
    The filter takes what is left of the controls' row, so a note put after it is
    clipped at the pane's edge: the one that says a quiet sub-agent is not a stuck
    one, and the one that says a filtered log is not the log as the mode sets it.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "A checksum, and a line without one.\n")
    snap, node = sub_agent(transcript)
    state = TranscriptState(mode=mode)
    plain = said_at(seam, monkeypatch, state, snap, node)
    left = min(plain.values())
    assert plain[SUB_AGENT_NOTE] == left
    assert RICH_NOTE not in plain and not any(note.startswith("filtering") for note in plain)

    state.search = "checksum"
    filtered = said_at(seam, monkeypatch, state, snap, node)
    assert filtered[f"filtering: raw lines ({mode.value} has no find)"] == left
    assert filtered[SUB_AGENT_NOTE] == left


def test_in_a_narrow_dock_a_note_is_broken_and_not_cut_off(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    "sub-agent: co" is what a 290 pixel dock showed of the note, and "not live" is
    the half of it that matters.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "A checksum, and a line without one.\n")
    snap, node = sub_agent(transcript)
    state = typeset_state(search="checksum")
    rects: dict[str, tuple[float, float]] = {}
    edge: list[float] = []
    disabled, coloured = seam.text_disabled, seam.text_colored

    def measured(text: str) -> None:
        rects[text] = (seam.get_item_rect_max().x, seam.get_item_rect_size().y)

    monkeypatch.setattr(seam, "text_disabled", lambda text: (disabled(text), measured(text))[1])
    monkeypatch.setattr(
        seam, "text_colored", lambda ink, text: (coloured(ink, text), measured(text))[1]
    )

    def narrow() -> None:
        seam.begin_child("##narrow-dock", seam.ImVec2(250.0, 500.0))
        edge.append(seam.get_window_pos().x + seam.get_window_size().x)
        transcript_pane.draw(snap, state, node)
        seam.end_child()

    rig.frame(seam, narrow)
    line = seam.get_text_line_height()
    for note in (SUB_AGENT_NOTE, "filtering: raw lines (typeset has no find)"):
        right, tall = rects[note]
        assert right <= edge[0], note
        assert tall >= 2 * line, note


def test_with_the_seam_off_the_notes_are_where_they_were(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "A checksum, and a line without one.\n")
    snap, node = sub_agent(transcript)
    state = TranscriptState(mode=RenderMode.RICH, search="checksum")
    notes = said_at(imgui, monkeypatch, state, snap, node)
    # After the copy button, on the controls' row, and no row about filtering.
    assert notes[RICH_NOTE] > 300.0 and notes[SUB_AGENT_NOTE] > notes[RICH_NOTE]
    assert not any(note.startswith("filtering") for note in notes)


def test_the_line_still_arriving_sits_where_it_will_once_it_is_whole(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The unfinished line of an open literal run is a view of its own. Set a gap
    below the lines above it, it would jump up by the gap when it ended.
    """
    ends: list[float] = []
    real = transcript_pane._draw_run

    def spy(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)
        ends.append(seam.get_cursor_screen_pos().y)

    monkeypatch.setattr(transcript_pane, "_draw_run", spy)
    transcript = Transcript()
    transcript.append(SegmentKind.TOOL_RESULT, "row one\nrow two\nrow thr")
    state = typeset_state(follow_tail=False)
    snap = session(transcript)
    for _ in range(2):
        draw(seam, snap, state)
    transcript.append(SegmentKind.TOOL_RESULT, "ee\n")
    for _ in range(2):
        draw(seam, snap, state)
    assert ends[1] == ends[0] and ends[3] == ends[2]
    assert ends[2] == pytest.approx(ends[0], abs=0.5)
