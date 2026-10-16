"""
The session log's tool blocks: each tool's layout and state as the proofs settle
them (planning/2026-10-15-tool-block-variants.md, "Per-tool proofs"), the viewer
that makes every byte of an entry reachable, the theme's terminal colours, and the
blocks in the pane -- where each is drawn, on both line paths, expanded and not.

Every entry is written as the driver writes one, through
``Transcript.append_tool_call`` and ``append_tool_result``.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import pytest

from pptmstr import drawable, theme
from pptmstr.intents import AgentSpawned, StateChanged
from pptmstr.model import AgentState, NodeId, Snapshot
from pptmstr.store import Store
from pptmstr.theme import contrast_ratio, relative_luminance
from pptmstr.transcript import OUTPUT_CAP, SegmentKind, ToolEntry, ToolResult, Transcript
from pptmstr.ui import tool_block, transcript_pane, typeset, widgets
from pptmstr.ui.tool_block import LINE_CUT, SHOW_ALL, State
from pptmstr.ui.transcript_pane import NodeTranscript, RenderMode, TranscriptState
from tests import test_typeset_views as rig

LOW = json.loads('"\\udc80"')
NUL = "\x00"
ODD = f"a{NUL}b{LOW}c"


@pytest.fixture(autouse=True)
def seam_off_unless_asked(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam off for every test that did not ask for it, as the pane's tests do."""
    if "seam" not in request.fixturenames:
        monkeypatch.setattr(typeset, "enabled", lambda pane=None: False)


def entry(
    name: str,
    args: dict[str, Any],
    output: object = None,
    *,
    error: bool = False,
    answered: bool = True,
) -> ToolEntry:
    """One call, and its result unless ``answered`` is false, as the driver writes them."""
    transcript = Transcript()
    transcript.append_tool_call("t1", name, args)
    if answered:
        transcript.append_tool_result("t1", name, output, is_error=error)
    found = transcript.tool_entry("t1")
    assert found is not None
    return found


def texts(rows: tuple[tool_block.Row, ...]) -> list[str]:
    return ["".join(span[2] for span in row if span[0] != "tip") for row in rows]


def caption(block: tool_block.Layout) -> list[str]:
    return [span[2] for span in block.caption]


def numbered(count: int) -> str:
    """A Read result as the CLI writes one: ``cat -n``."""
    return "".join(f"{n:>6}\tline {n}\n" for n in range(1, count + 1))


# -- the state ----------------------------------------------------------------------


def test_a_call_with_no_result_is_running_only_while_its_turn_is() -> None:
    call = entry("Bash", {"command": "sleep 9"}, answered=False)
    assert tool_block.state_of(call, live=True) is State.RUNNING
    assert tool_block.state_of(call, live=False) is State.NO_RESULT
    assert tool_block.state_of(entry("Bash", {"command": "ls"}, "a"), live=True) is State.DONE
    failed = entry("Bash", {"command": "ls"}, "no", error=True)
    assert tool_block.state_of(failed, live=False) is State.ERROR


@pytest.mark.parametrize(
    ("live", "badge", "ink"),
    [(True, "running", "accent"), (False, "no result · turn ended", "warn")],
)
def test_the_badge_says_running_or_no_result_and_draws_only_what_was_asked(
    live: bool, badge: str, ink: str
) -> None:
    block = tool_block.layout(entry("Bash", {"command": "make"}, answered=False), live)
    assert block.caption[-1] == ("caption", ink, badge)
    assert texts(block.head) == ["$ make"] and block.body == () and block.more == ""


# -- each tool, as the proofs draw it -------------------------------------------------


def test_bash_is_the_terminal_and_keeps_the_last_lines() -> None:
    output = "".join(f"out {n}\n" for n in range(40))
    block = tool_block.layout(
        entry(
            "Bash",
            {"command": "pytest -q", "description": "run the tests", "timeout": 120000},
            output,
        ),
        live=False,
    )
    assert block.tool == "Bash" and block.state is State.DONE
    assert caption(block) == ["run the tests", "timeout 2 min", "done"]
    assert "accent" not in [span[1] for span in block.caption], "Bash has no tool word"
    assert texts(block.head) == ["$ pytest -q"]
    assert texts(block.body) == [f"out {n}" for n in range(34, 40)]
    assert block.more == "34 earlier lines" and block.tail


def test_a_failed_command_says_its_exit_code_and_drops_that_line() -> None:
    block = tool_block.layout(
        entry("Bash", {"command": "false"}, "Exit code 2\nboom", error=True), live=False
    )
    assert block.state is State.ERROR and block.caption[-1] == ("caption", "danger", "exit 2")
    assert texts(block.body) == ["boom"]


def test_a_long_command_shows_three_lines_and_says_how_many_more() -> None:
    command = "\n".join(f"step {n}" for n in range(5))
    block = tool_block.layout(entry("Bash", {"command": command}, "ok"), live=False)
    assert texts(block.head) == ["$ step 0", "  step 1", "  step 2", "  … 2 more lines"]
    assert block.more == SHOW_ALL, "what the head left out is in the viewer"


def test_read_is_a_numbered_excerpt_from_the_top() -> None:
    block = tool_block.layout(
        entry("Read", {"file_path": "/a.py", "offset": 1, "limit": 20}, numbered(20)), live=False
    )
    assert caption(block)[:3] == ["read", "lines 1–20", "20 lines"]
    assert texts(block.head) == ["/a.py"]
    assert texts(block.body)[0] == "   1 line 1" and len(block.body) == 6
    assert block.more == "14 more lines" and not block.tail


def test_edit_is_a_diff_in_the_diff_inks() -> None:
    block = tool_block.layout(
        entry(
            "Edit",
            {"file_path": "/a.py", "old_string": "a\nb\nc", "new_string": "a\nB\nc"},
            "The file /a.py has been updated successfully.",
        ),
        live=False,
    )
    assert caption(block)[:3] == ["edit", "+1", "−1"]
    inks = [row[0][1] for row in block.body]
    assert texts(block.body) == [" a", "-b", "+B", " c"] and inks == ["ctx", "del", "add", "ctx"]
    assert "updated" not in " ".join(texts(block.body)), "the result adds nothing to the diff"


def test_write_is_a_preview_marked_new_or_overwritten() -> None:
    content = "".join(f"row {n}\n" for n in range(9))
    made = tool_block.layout(
        entry(
            "Write",
            {"file_path": "/n.py", "content": content},
            "File created successfully at: /n.py",
        ),
        live=False,
    )
    assert ("caption", "add", "new file") in made.caption
    assert texts(made.body)[0] == "+ row 0" and len(made.body) == 5
    assert made.more == "4 more lines in the file"
    over = tool_block.layout(
        entry(
            "Write",
            {"file_path": "/n.py", "content": "x"},
            "The file /n.py has been updated successfully.",
        ),
        live=False,
    )
    assert ("caption", "warn", "overwrote") in over.caption


def test_grep_in_content_mode_puts_each_location_in_accent_and_counts_the_lines() -> None:
    output = "".join(f"src/a.py:{n}:hit {n}\n" for n in range(1, 9))
    block = tool_block.layout(
        entry("Grep", {"pattern": "hit", "output_mode": "content", "-n": True}, output), live=False
    )
    assert texts(block.head) == ["/hit/"]
    assert ("caption", "text", "8 lines") in block.caption
    assert block.body[0][0] == ("mono", "accent", "src/a.py:1")
    assert block.more == "2 more matches"


def test_glob_says_no_matches_in_warn() -> None:
    block = tool_block.layout(entry("Glob", {"pattern": "*.zz"}, "No files found"), live=False)
    assert ("caption", "warn", "no matches") in block.caption and block.body == ()


def test_webfetch_constructs_the_url_and_a_cross_host_redirect_is_named_unverified() -> None:
    url = "https://example.com/" + "p" * 80 + "?q=1"
    done = tool_block.layout(
        entry("WebFetch", {"url": url, "prompt": "what?"}, "One.\nTwo."), live=False
    )
    row = done.head[0]
    assert row[1] == ("mono", "link", "example.com")
    assert "…" in row[2][2] and row[2][2].endswith("?q=1"), "the host and the end are kept"
    assert row[3] == ("tip", "link", url), "the whole url is the tooltip"
    assert texts(done.body) == ["One.", "Two."]
    redirected = tool_block.layout(
        entry(
            "WebFetch",
            {"url": "https://a.example/x", "prompt": "p"},
            "REDIRECT DETECTED: away\n\nRedirect URL (from the server's Location header —"
            " server-supplied, not verified): https://b.example/y\n",
        ),
        live=False,
    )
    assert redirected.caption[-1][2] == "redirect · not fetched"
    assert redirected.body[1][1] == ("mono", "link", "b.example")


def test_websearch_lists_titles_over_urls() -> None:
    links = [{"title": f"T{n}", "url": f"https://s{n}.example/"} for n in range(6)]
    output = f'Web search results for query: "q"\n\nLinks: {json.dumps(links)}\n'
    block = tool_block.layout(entry("WebSearch", {"query": "q"}, output), live=False)
    assert ("caption", "text", "6 results") in block.caption
    assert texts(block.head) == ["“q”"]
    assert texts(block.body)[:2] == ["T0", "https://s0.example/"] and len(block.body) == 8
    assert block.more == "2 more results"


def test_an_agent_shows_its_description_its_prompt_collapsed_and_its_run_in_the_caption() -> None:
    report = [
        {"type": "text", "text": "Found it.\nLine two.\nLine three.\nLine four."},
        {
            "type": "text",
            "text": "agentId: x <usage>subagent_tokens: 9 tool_uses: 4 duration_ms: 12000</usage>",
        },
    ]
    block = tool_block.layout(
        entry(
            "Agent",
            {"description": "find it", "prompt": "look\nhard", "subagent_type": "explore"},
            report,
        ),
        live=False,
    )
    assert caption(block)[:3] == ["agent", "explore", "4 tool uses · 12 s"]
    assert texts(block.head)[0] == "find it"
    assert "2 lines of prompt" in texts(block.head)[1]
    assert texts(block.body) == ["Found it.", "Line two.", "Line three."]
    assert block.more == "1 more line of its report"


def test_todowrite_is_a_checklist() -> None:
    todos = [
        {"content": "a", "status": "completed", "activeForm": "doing a"},
        {"content": "b", "status": "in_progress", "activeForm": "doing b"},
        {"content": "c", "status": "pending", "activeForm": "doing c"},
    ]
    block = tool_block.layout(
        entry("TodoWrite", {"todos": todos}, "Todos have been modified"), live=False
    )
    assert caption(block)[:2] == ["todos", "1 done · 1 doing · 1 to do"]
    assert texts(block.body) == ["[x] a", "[>] doing b", "[ ] c"]


def test_notebookedit_shows_its_source_as_added() -> None:
    block = tool_block.layout(
        entry(
            "NotebookEdit",
            {"notebook_path": "/n.ipynb", "cell_id": "c1", "new_source": "x = 1"},
            "Updated cell",
        ),
        live=False,
    )
    assert caption(block)[:2] == ["notebook", "replace cell c1"]
    assert block.body == ((("mono", "add", "x = 1"),),)


def test_an_mcp_call_names_its_server_and_reads_its_text_blocks_back() -> None:
    block = tool_block.layout(
        entry(
            "mcp__pptmstr__post_concern",
            {"to": "lead", "subject": "s"},
            [{"type": "text", "text": "sent\nto lead"}],
        ),
        live=False,
    )
    assert caption(block)[0] == "mcp · pptmstr"
    assert texts(block.head) == ["post_concern", "to: lead", "subject: s"]
    assert texts(block.body) == ["sent", "to lead"]


def test_a_tool_with_no_block_of_its_own_says_so_in_warn() -> None:
    block = tool_block.layout(entry("Skill", {"skill": "x"}, "done"), live=False)
    assert block.caption[0] == ("caption", "warn", "Skill")
    assert texts(block.head) == ["skill: x"] and texts(block.body) == ["done"]


# -- nothing out of reach, nothing raw ---------------------------------------------


def test_a_line_past_the_cut_is_cut_and_the_whole_is_behind_the_affordance() -> None:
    block = tool_block.layout(entry("Bash", {"command": "cat"}, "x" * 1000), live=False)
    assert len(texts(block.body)[0]) == LINE_CUT and texts(block.body)[0].endswith("…")
    assert block.more == SHOW_ALL


def test_output_dropped_at_the_cap_is_said_on_the_affordance() -> None:
    block = tool_block.layout(
        entry("Bash", {"command": "cat"}, "y" * (OUTPUT_CAP + 10)), live=False
    )
    assert block.more.endswith("· 10 B past the 1 MiB cap")


def test_a_block_with_nothing_left_out_has_no_affordance() -> None:
    assert tool_block.layout(entry("Bash", {"command": "ls"}, "a\nb"), live=False).more == ""


TOOLS = (
    "Bash",
    "Read",
    "Edit",
    "Write",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "Agent",
    "TodoWrite",
    "NotebookEdit",
    "mcp__srv__tool",
    f"Frob{ODD}",
)


def odd_args(name: str) -> dict[str, Any]:
    """Every field a tool's block reads, each holding a NUL and a lone surrogate."""
    text = f"{ODD}\n{ODD}"
    return {
        "command": text,
        "description": ODD,
        "file_path": ODD,
        "old_string": text,
        "new_string": f"{text}!",
        "content": text,
        "pattern": ODD,
        "path": ODD,
        "url": f"https://h/{ODD}",
        "prompt": text,
        "query": ODD,
        "todos": [
            {"content": ODD, "status": s, "activeForm": ODD}
            for s in ("completed", "in_progress", "pending")
        ],
        "notebook_path": ODD,
        "new_source": text,
        f"k{ODD}": ODD,
    }


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("failed", [False, True], ids=["done", "error"])
def test_every_string_a_block_draws_is_drawable(name: str, failed: bool) -> None:
    output = "\n".join([ODD] * 12) + "\nLinks: " + json.dumps([{"title": ODD, "url": ODD}])
    held = entry(name, odd_args(name), output, error=failed)
    block = tool_block.layout(held, live=False)
    drawn = [*tool_block.strings(block), *(s[2] for r in tool_block.viewer_rows(held) for s in r)]
    assert all(not drawable.undrawable(s) for s in drawn), [
        s for s in drawn if drawable.undrawable(s)
    ]
    assert any(drawable.text(ODD) in s for s in drawn), "and the odd text is drawn, written out"


class EveryFieldOdd(Mapping[str, Any]):
    """
    Arguments that hold ``value`` under every key a layout asks for, whatever the
    key: a field a block reads is odd whether or not a test thought to name it.
    Iterated, it is the keys asked for so far and one no tool reads.
    """

    def __init__(self, value: Any) -> None:
        self.value = value
        self.asked: list[str] = [f"k{ODD}"]

    def __getitem__(self, key: str) -> Any:
        if key not in self.asked:
            self.asked.append(key)
        return self.value

    def __contains__(self, key: object) -> bool:
        return True

    def __iter__(self) -> Iterator[str]:
        return iter(list(self.asked))

    def __len__(self) -> int:
        return len(self.asked)


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("failed", [False, True], ids=["done", "error"])
@pytest.mark.parametrize("value", [ODD, [ODD, ODD], {ODD: ODD}], ids=["str", "list", "dict"])
def test_every_field_a_block_reads_is_drawn_drawable_named_or_not(
    name: str, failed: bool, value: Any
) -> None:
    """
    Every input value is walked, not a list of the fields known today: the
    arguments answer every key with odd text, so a field a layout reads and draws
    raw is caught however new it is.
    """
    args = EveryFieldOdd(value)
    output = f"{ODD}\n{ODD}"
    held = ToolEntry("t1", name, args, (0, 1), ToolResult(output, 0, failed, 1, 2))
    for live in (False, True):
        block = tool_block.layout(held, live)
        drawn = [
            *tool_block.strings(block),
            *(s[2] for r in tool_block.viewer_rows(held) for s in r),
        ]
        assert [s for s in drawn if drawable.undrawable(s)] == [], args.asked
    assert len(args.asked) > 1 or name.startswith("mcp__") or name.startswith("Frob"), args.asked


def output_back(rows: tuple[tool_block.Row, ...]) -> str:
    """The output as the viewer's rows hold it, put back together."""
    at = next(n for n, row in enumerate(rows) if row[0][2].startswith("output · "))
    pieces = [row[0][2] for row in rows[at + 1 :]]
    return "".join(pieces)


def test_the_viewer_holds_every_character_of_the_output_and_no_row_is_too_long() -> None:
    output = "short\n" + "w" * (LINE_CUT * 3 + 7) + f"\n{ODD}\n"
    held = entry("Bash", {"command": "cat"}, output)
    rows = tool_block.viewer_rows(held)
    assert all(len(span[2]) <= LINE_CUT + 2 for row in rows for span in row)
    # Every line's pieces in order are the line, made drawable piece by piece.
    assert output_back(rows) == "short" + "w" * (LINE_CUT * 3 + 7) + drawable.text(ODD)
    assert texts(rows)[:2] == ["command:", "  cat"]


def test_a_result_of_a_mebibyte_on_one_line_is_rows_the_clipper_can_skip() -> None:
    held = entry("Bash", {"command": "cat"}, "z" * OUTPUT_CAP)
    rows = tool_block.viewer_rows(held)
    output = [row for row in rows if row[0][2].startswith("z")]
    assert len(output) == -(-OUTPUT_CAP // LINE_CUT)
    assert sum(len(row[0][2]) for row in output) == OUTPUT_CAP


def test_the_viewer_says_what_the_cap_dropped_and_that_a_call_was_not_seen() -> None:
    transcript = Transcript()
    transcript.append_tool_result("t9", "tool", "v" * (OUTPUT_CAP + 5), is_error=False)
    orphan = transcript.tool_entry("t9")
    assert orphan is not None
    said = texts(tool_block.viewer_rows(orphan))
    assert said[0] == "(the call is not in this transcript)"
    assert said[1].endswith("5 B past the 1 MiB cap were not kept")


# -- the theme's terminal ------------------------------------------------------------

PALETTES = tuple(theme.THEMES.values())
IDS = [p.name for p in PALETTES]
ROLES = ("accent", "warn", "danger", "ok", "diff_add", "diff_remove", "diff_context")


def _mixed8(base: theme.Color, over: theme.Color, alpha: float) -> int:
    v = theme.mixed(base, over, alpha)
    r, g, b = (round(c * 255.0) for c in (v.x, v.y, v.z))
    return theme.col((r << 16) | (g << 8) | b).u32


@pytest.mark.parametrize("palette", PALETTES, ids=IDS)
def test_the_ground_is_the_themes_derived_from_its_pane(palette: theme.Palette) -> None:
    lum = relative_luminance(palette.bg)
    black, white = theme.col(0x000000), theme.col(0xFFFFFF)
    if lum < 0.001:
        expected, outline = _mixed8(palette.bg, palette.text, 0.10), True
    elif lum < 0.05:
        expected, outline = _mixed8(palette.bg, black, 0.60), False
    elif lum < 0.5:
        expected, outline = _mixed8(palette.bg, white, 0.20), False
    else:
        expected, outline = _mixed8(palette.bg, palette.text, 0.06), False
    assert palette.terminal_bg.u32 == expected
    assert palette.terminal_outline is outline
    assert palette.terminal_bg.u32 != theme.col(0x0B0B0C).u32, "never the fixed near-black"
    assert (palette.terminal_text.u32, palette.terminal_dim.u32) == (
        palette.text.u32,
        theme.terminal_ink(palette.text_dim, palette).u32,
    )
    assert 1.05 <= contrast_ratio(palette.terminal_bg, palette.bg), "a step off the pane"


@pytest.mark.parametrize("palette", PALETTES, ids=IDS)
def test_every_ink_a_block_draws_text_in_reads_at_aa_on_its_ground(palette: theme.Palette) -> None:
    ground = palette.terminal_bg
    assert contrast_ratio(palette.terminal_text, ground) >= theme.TERMINAL_FLOOR
    assert contrast_ratio(palette.terminal_dim, ground) >= theme.TERMINAL_FLOOR
    for role in ROLES:
        ink = theme.terminal_ink(getattr(palette, role), palette)
        assert contrast_ratio(ink, ground) >= theme.TERMINAL_FLOOR, role


def test_an_ink_that_reads_is_kept_and_one_that_does_not_is_moved_the_least_way() -> None:
    dark = theme.DARK
    assert theme.terminal_ink(dark.accent, dark).u32 == dark.accent.u32
    kitty = theme.KITTY
    moved = theme.terminal_ink(kitty.accent, kitty)
    assert moved.u32 != kitty.accent.u32 and moved.u32 != kitty.terminal_text.u32
    assert contrast_ratio(moved, kitty.terminal_bg) >= theme.TERMINAL_FLOOR


# -- the pane: which run draws which block ---------------------------------------------

ROOT: NodeId = ("s1", None)


def calls(*pairs: tuple[str, str]) -> Transcript:
    """
    Prose, then each ``(id, output)`` called and answered in turn as the driver does,
    the call line in one run and the result line in the next.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "Looking.\n")
    for tool_use_id, output in pairs:
        transcript.append_tool_call(tool_use_id, "Bash", {"command": f"echo {tool_use_id}"})
        transcript.append_tool_result(tool_use_id, "Bash", output, is_error=False)
    transcript.append(SegmentKind.OUTPUT, "Done.\n")
    return transcript


def synced(transcript: Transcript) -> NodeTranscript:
    cache = NodeTranscript()
    cache.sync(transcript, transcript.published_length)
    return cache


def blocks_for(
    transcript: Transcript,
    cache: NodeTranscript,
    *,
    in_turn: bool = False,
    pinned: int | None = None,
) -> transcript_pane._Blocks:
    return transcript_pane._Blocks(
        TranscriptState(),
        ROOT,
        transcript,
        cache,
        transcript.published_length if pinned is None else pinned,
        in_turn,
        16.0,
    )


def runs_of(cache: NodeTranscript, kind: SegmentKind) -> list[int]:
    return [n for n, start in enumerate(cache.run_starts) if cache.lines[start].kind is kind]


def test_a_tool_runs_span_and_whether_it_holds_an_entry_are_kept_with_its_lines() -> None:
    transcript = calls(("a", "one"))
    transcript.append(SegmentKind.TOOL_RESULT, "a line a writer appended by hand\n")
    cache = synced(transcript)
    (call,) = runs_of(cache, SegmentKind.TOOL_CALL)
    results = runs_of(cache, SegmentKind.TOOL_RESULT)
    assert cache.hosted == {call, results[0]}, "the hand-written line holds no entry"
    held = transcript.tool_entry("a")
    assert held is not None and held.call is not None and held.result is not None
    assert cache.run_spans[call] == list(held.call)
    assert cache.run_spans[results[0]] == [held.result.start, held.result.end]
    spans = [tuple(span) for span in cache.run_spans]
    assert spans == sorted(spans) and spans[0][0] == 0
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:], strict=False)), "contiguous"


def test_a_block_is_drawn_at_its_call_and_its_result_line_draws_nothing() -> None:
    transcript = calls(("a", "one"))
    cache = synced(transcript)
    blocks = blocks_for(transcript, cache)
    (call,) = runs_of(cache, SegmentKind.TOOL_CALL)
    (result,) = runs_of(cache, SegmentKind.TOOL_RESULT)
    start, end = cache.run_spans[call]
    pieces = blocks._pieces(start, end)
    assert [(entry.tool_use_id, entry.result is not None) for _, entry, _ in pieces if entry] == [
        ("a", True)
    ]
    assert blocks._pieces(*cache.run_spans[result]) == []


def test_a_result_whose_call_is_above_the_pane_or_unseen_is_drawn_at_its_line() -> None:
    transcript = calls(("a", "one"))
    transcript.append_tool_result("ghost", "tool", "from nowhere", is_error=True)
    cache = synced(transcript)
    blocks = blocks_for(transcript, cache)
    result, orphan = (
        runs_of(cache, SegmentKind.TOOL_RESULT)[0],
        runs_of(cache, SegmentKind.ERROR)[0],
    )
    (piece,) = blocks._pieces(*cache.run_spans[orphan])
    assert piece[1] is not None and piece[1].tool_use_id == "ghost"
    assert blocks._pieces(*cache.run_spans[result]) == []
    blocks.floor = cache.run_spans[result][0]
    (above,) = blocks._pieces(*cache.run_spans[result])
    assert above[1] is not None and above[1].tool_use_id == "a", "its call is above the floor"


def test_bytes_of_a_tool_run_no_entry_holds_are_drawn_as_they_are() -> None:
    transcript = Transcript()
    transcript.append_tool_call("a", "Bash", {"command": "x"})
    transcript.append_tool_result("a", "Bash", "one", is_error=False)
    transcript.append(SegmentKind.TOOL_RESULT, "appended by hand\n")
    cache = synced(transcript)
    blocks = blocks_for(transcript, cache)
    (result,) = runs_of(cache, SegmentKind.TOOL_RESULT)
    assert blocks._pieces(*cache.run_spans[result]) == [
        (cache.run_spans[result][0] + len("Bash -> one\n"), None, "appended by hand")
    ]


def test_a_result_past_the_frames_pinned_length_has_not_arrived() -> None:
    transcript = Transcript()
    transcript.append_tool_call("a", "Bash", {"command": "x"})
    pinned = transcript.published_length
    transcript.append_tool_result("a", "Bash", "late", is_error=False)
    cache = NodeTranscript()
    cache.sync(transcript, pinned)
    blocks = blocks_for(transcript, cache, pinned=pinned)
    (piece,) = blocks._pieces(*cache.run_spans[0])
    assert piece[1] is not None and piece[1].result is None


# -- the pane, drawn -----------------------------------------------------------------


def session(transcript: Transcript, state: AgentState = AgentState.AWAITING_INPUT) -> Snapshot:
    store = Store()
    store.apply(
        AgentSpawned(
            node_id=ROOT,
            parent=None,
            task="t",
            model="claude-sonnet-5",
            started_at=0.0,
            cwd="/x",
            transcript=transcript,
        )
    )
    if state is not AgentState.SPAWNING:
        store.apply(StateChanged(ROOT, state, ""), now=1.0)
    return store.snapshot()


@pytest.fixture
def drawn_blocks(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tool_block.Layout, bool, float]]:
    """Every block drawn: its layout, whether expanded, and how wide it was drawn."""
    seen: list[tuple[tool_block.Layout, bool, float]] = []
    real = tool_block.draw

    def spy(block: tool_block.Layout, **given: Any) -> bool:
        from imgui_bundle import imgui

        room = imgui.get_content_region_avail().x
        pressed = real(block, **given)
        width = imgui.get_item_rect_size().x
        assert width == room, "a block is as wide as the room it is given"
        seen.append((block, bool(given.get("expanded")), width))
        return pressed

    monkeypatch.setattr(tool_block, "draw", spy)
    return seen


def frames(im: Any, snap: Snapshot, state: TranscriptState, count: int = 3) -> None:
    for _ in range(count):
        rig.frame(im, lambda: transcript_pane.draw(snap, state, ROOT))


def wrapped_said(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every line the log drew as wrapped text."""
    from imgui_bundle import imgui

    said: list[str] = []
    real = imgui.text_wrapped

    def spy(text: str) -> None:
        said.append(text)
        real(text)

    monkeypatch.setattr(imgui, "text_wrapped", spy)
    return said


def test_without_the_library_each_call_is_one_block_and_its_lines_are_not_drawn(
    imgui: Any, monkeypatch: pytest.MonkeyPatch, drawn_blocks: list[Any]
) -> None:
    transcript = calls(("a", "one"), ("b", "two"))
    said = wrapped_said(monkeypatch)
    frames(imgui, session(transcript), TranscriptState())
    per_frame = len(drawn_blocks) // 3
    assert [b.head[0][0][2] for b, _, _ in drawn_blocks[:per_frame]] == ["$ echo a", "$ echo b"]
    assert all(b.state is State.DONE for b, _, _ in drawn_blocks)
    assert "Looking." in said and "Done." in said
    assert not [s for s in said if "Bash" in s], "no call or result line is drawn as itself"


def test_with_the_library_the_blocks_fall_between_the_runs_it_sets(
    seam: Any, drawn_blocks: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = calls(("a", "one"), ("b", "two"))
    tops: list[float] = []
    real = tool_block.draw

    def at(block: tool_block.Layout, **given: Any) -> bool:
        tops.append(seam.get_cursor_screen_pos().y)
        return real(block, **given)

    monkeypatch.setattr(tool_block, "draw", at)
    state = TranscriptState(mode=RenderMode.TYPESET)
    frames(seam, session(transcript), state)
    assert len(tops) % 2 == 0 and len(tops) >= 2
    first, second = tops[-2:]
    assert first < second
    feed = state.feeds[ROOT]
    texts_set = [text for g in feed.generations for text in feed.views.texts(g.key)]
    assert "Looking." in texts_set[0], "the prose above is the library's"


def test_a_call_whose_turn_is_on_is_running_and_one_from_before_the_last_prompt_is_not(
    imgui: Any, drawn_blocks: list[Any]
) -> None:
    transcript = Transcript()
    transcript.append_tool_call("old", "Bash", {"command": "old"})
    transcript.append(SegmentKind.SYSTEM, "> again\n")
    transcript.append_tool_call("new", "Bash", {"command": "new"})
    frames(imgui, session(transcript, AgentState.RUNNING_TOOL), TranscriptState(), 1)
    states = {b.head[0][0][2]: b.state for b, _, _ in drawn_blocks}
    assert states == {"$ old": State.NO_RESULT, "$ new": State.RUNNING}
    drawn_blocks.clear()
    frames(imgui, session(transcript, AgentState.AWAITING_INPUT), TranscriptState(), 1)
    assert {b.state for b, _, _ in drawn_blocks} == {State.NO_RESULT}


@pytest.mark.parametrize("typeset_on", [False, True], ids=["wrap", "typeset"])
def test_the_affordance_opens_the_viewer_in_place_and_closes_it_again(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    drawn_blocks: list[Any],
    typeset_on: bool,
) -> None:
    im = request.getfixturevalue("seam" if typeset_on else "imgui")
    output = "".join(f"row {n}\n" for n in range(5000))
    transcript = calls(("a", output))
    state = TranscriptState(mode=RenderMode.TYPESET if typeset_on else None)
    snap = session(transcript)
    frames(im, snap, state, 2)
    assert drawn_blocks[-1][0].more == "4,994 earlier lines" and not drawn_blocks[-1][1]

    press = {"4,994 earlier lines##more"}
    real = widgets.link
    linked: list[str] = []

    def link(label: str, *, inks: Any = None, ident: str | None = None) -> bool:
        linked.append(label)
        return real(label, inks=inks, ident=ident) or ident in press

    monkeypatch.setattr(widgets, "link", link)
    frames(im, snap, state, 1)
    assert linked[-1] == tool_block.SHOW_ALL, "the footer's link says what it does"
    press.clear()
    rows: list[str] = []
    real_text = im.text_colored

    def counted(colour: Any, text: str) -> None:
        rows.append(text)
        real_text(colour, text)

    monkeypatch.setattr(im, "text_colored", counted)
    frames(im, snap, state, 2)
    assert drawn_blocks[-1][1], "expanded"
    shown = [row for row in rows if row.startswith("row ")]
    assert 0 < len(shown) <= 2 * (tool_block.VIEWER_ROWS + 4), "the clipper drew what is in sight"

    assert linked[-1] == tool_block.COLLAPSE
    press.add(f"{tool_block.COLLAPSE}##more")
    frames(im, snap, state, 1)
    press.clear()
    frames(im, snap, state, 1)
    assert not drawn_blocks[-1][1], "collapsed again"


@pytest.mark.parametrize("typeset_on", [False, True], ids=["wrap", "typeset"])
def test_a_block_is_as_wide_as_the_log_less_the_bar_where_the_log_overflows(
    request: pytest.FixtureRequest, drawn_blocks: list[Any], typeset_on: bool
) -> None:
    """
    No strip is kept for a bar that is not there (pane-margins): a block is the
    log's measure, which in a long log is the bar's width narrower.
    """
    im = request.getfixturevalue("seam" if typeset_on else "imgui")
    mode = RenderMode.TYPESET if typeset_on else None
    short = calls(("a", "one"))
    frames(im, session(short), TranscriptState(mode=mode))
    narrow = drawn_blocks[-1][2]
    drawn_blocks.clear()
    long = Transcript()
    long.append(SegmentKind.OUTPUT, "".join(f"Paragraph {n}.\n\n" for n in range(200)))
    long.append_tool_call("a", "Bash", {"command": "echo a"})
    long.append_tool_result("a", "Bash", "one", is_error=False)
    frames(im, session(long), TranscriptState(mode=mode), 4)
    assert narrow - drawn_blocks[-1][2] == im.get_style().scrollbar_size


def test_find_without_the_library_counts_what_blocks_hold_and_prose_in_order(
    imgui: Any,
) -> None:
    transcript = calls(("a", "needle in output"))
    transcript.append(SegmentKind.OUTPUT, "a needle in prose\n")
    state = TranscriptState()
    state.find.open, state.find.needle = True, "needle"
    frames(imgui, session(transcript), state, 2)
    assert state.find.count == 2, "the tool output's and the prose's"


def test_find_with_the_library_reaches_into_a_block_in_run_order(seam: Any) -> None:
    transcript = calls(("a", "needle in output"))
    transcript.append(SegmentKind.OUTPUT, "a needle in prose\n")
    state = TranscriptState(mode=RenderMode.TYPESET)
    state.find.open, state.find.needle = True, "needle"
    frames(seam, session(transcript), state, 3)
    feed = state.feeds[ROOT]
    blocks = transcript_pane._Blocks(
        state, ROOT, transcript, state.caches[ROOT], transcript.published_length, False, 16.0
    )
    found = transcript_pane._typeset_found(feed, state.caches[ROOT], blocks, True, "needle")
    assert [type(item).__name__ for item in found] == ["BlockHit", "tuple"], "block, then prose"
    assert state.find.count == 2


def test_a_block_finds_its_collapsed_text_shown_and_its_hidden_output_in_the_viewer() -> None:
    output = "".join(f"row {n}{' needle' if n in (2, 39) else ''}\n" for n in range(40))
    held = entry("Bash", {"command": "make needle"}, output)
    block = tool_block.layout(held, live=False)
    hits = tool_block.find(held, block, "NEEDLE")
    rows = texts(tool_block.viewer_rows(held))
    assert [(rows[h.row].strip(), h.shown) for h in hits] == [
        ("make needle", True),
        ("row 2 needle", False),
        ("row 39 needle", True),
    ], "the command and the tail are drawn, row 2 is behind '34 earlier lines'"
    labelled = entry("Bash", {"command": "echo command"}, "")
    found = tool_block.find(labelled, tool_block.layout(labelled, live=False), "command")
    assert len(found) == 1, "the value's, and not the viewer's label 'command:'"


@pytest.mark.parametrize("typeset_on", [False, True], ids=["wrap", "typeset"])
def test_stepping_to_a_match_in_a_blocks_hidden_output_opens_it_at_that_row(
    request: pytest.FixtureRequest, drawn_blocks: list[Any], typeset_on: bool
) -> None:
    im = request.getfixturevalue("seam" if typeset_on else "imgui")
    output = "".join(f"row {n}{' needle' if n == 2 else ''}\n" for n in range(400))
    transcript = calls(("a", output))
    state = TranscriptState(mode=RenderMode.TYPESET if typeset_on else None)
    snap = session(transcript)
    frames(im, snap, state, 2)
    assert not drawn_blocks[-1][1]
    state.find.show()
    state.find.needle = "needle"
    state.find.focus = False
    frames(im, snap, state, 3)
    assert state.find.count == 1
    assert drawn_blocks[-1][1], "the block opened on its viewer"
    assert not state.follow_tail, "and the view was moved for the reader"


@pytest.mark.parametrize("typeset_on", [False, True], ids=["wrap", "typeset"])
def test_stepping_to_a_match_a_collapsed_block_draws_leaves_it_collapsed_and_marks_it(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, typeset_on: bool
) -> None:
    im = request.getfixturevalue("seam" if typeset_on else "imgui")
    marks: list[Any] = []
    real = tool_block.draw

    def spy(block: tool_block.Layout, **given: Any) -> bool:
        marks.append((given.get("expanded"), given.get("mark")))
        return real(block, **given)

    monkeypatch.setattr(tool_block, "draw", spy)
    output = "".join(f"row {n}{' needle' if n == 398 else ''}\n" for n in range(400))
    transcript = calls(("a", output))
    state = TranscriptState(mode=RenderMode.TYPESET if typeset_on else None)
    state.find.show()
    state.find.needle, state.find.focus = "needle", False
    frames(im, session(transcript), state, 3)
    expanded, mark = marks[-1]
    assert state.find.count == 1 and not expanded
    assert mark is not None and mark.ink == transcript_pane._CURRENT_INK and mark.row is None


def test_find_marks_text_a_tool_run_draws_as_itself(imgui: Any) -> None:
    transcript = Transcript()
    transcript.append_tool_call("a", "Bash", {"command": "x"})
    transcript.append_tool_result("a", "Bash", "one", is_error=False)
    transcript.append(SegmentKind.TOOL_RESULT, "a needle appended by hand\n")
    state = TranscriptState()
    state.find.open, state.find.needle = True, "needle"
    frames(imgui, session(transcript), state, 2)
    assert state.find.count == 1


# -- what the operator signed off -----------------------------------------------------


def _probe() -> Any:
    """``scripts/tool_block_probe.py``, whose per-tool proofs the operator signed off."""
    import importlib.util
    import sys
    from pathlib import Path

    if "tool_block_probe" in sys.modules:
        return sys.modules["tool_block_probe"]
    path = Path(__file__).resolve().parent.parent / "scripts" / "tool_block_probe.py"
    spec = importlib.util.spec_from_file_location("tool_block_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["tool_block_probe"] = module
    spec.loader.exec_module(module)
    return module


def _plain(rows: Any) -> list[str]:
    return ["".join(span[2] for span in row if span[0] != "tip") for row in rows]


def _singular(text: str) -> str:
    """The proofs said ``1 lines`` and ``1 more lines``; the block says ``1 line``."""
    import re

    return re.sub(r"(?<![\d,])1 (more )?lines", r"1 \1line", text)


def test_every_proof_cell_is_built_as_the_proof_drew_it() -> None:
    """
    Every cell of the per-tool proofs -- each tool done, its second case, error,
    running and no result -- read from the same entry, says what the proof said:
    the same caption, head and body, and the same affordance, except that a block
    that shortened something with no rows left out offers ``show all`` where the
    proof offered nothing (every byte of an entry is reachable from its block).
    """
    probe = _probe()
    _, cells = probe.build_proof_cases()
    assert len(cells) >= 60
    for cell in cells:
        proof = probe.proof(cell.entry, cell.live)
        block = tool_block.layout(cell.entry, cell.live)
        where = (cell.tool, cell.column)
        assert [_singular(s[2]) for s in proof.caption] == [s[2] for s in block.caption], where
        assert _plain(proof.head) == _plain(block.head), where
        assert _plain(proof.body) == _plain(block.body), where
        assert _singular(proof.more) == block.more or (not proof.more and block.more == SHOW_ALL), (
            where,
            proof.more,
            block.more,
        )


def test_with_the_library_a_result_whose_call_was_left_above_is_drawn_at_its_line(
    seam: Any, drawn_blocks: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The pane took in its last lines only (``_TYPESET_WINDOW``): the call's line is
    above them and the result's is not, so the block is the result's to draw.
    """
    monkeypatch.setattr(transcript_pane, "_TYPESET_WINDOW", 4)
    transcript = Transcript()
    transcript.append_tool_call("a", "Bash", {"command": "echo a"})
    transcript.append(SegmentKind.OUTPUT, "".join(f"prose {n}\n" for n in range(10)))
    transcript.append_tool_result("a", "Bash", "one", is_error=False)
    transcript.append(SegmentKind.OUTPUT, "after\n")
    state = TranscriptState(mode=RenderMode.TYPESET)
    frames(seam, session(transcript), state, 2)
    assert state.feeds[ROOT].skipped > 0, "the call's line is above what was taken in"
    assert [b.head[0][0][2] for b, _, _ in drawn_blocks[-1:]] == ["$ echo a"]
    assert drawn_blocks[-1][0].state is State.DONE


def test_without_the_library_a_result_whose_call_is_above_the_lines_drawn_is_drawn_at_its_line(
    imgui: Any, drawn_blocks: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transcript_pane, "_WRAP_WINDOW", 4)
    transcript = Transcript()
    transcript.append_tool_call("a", "Bash", {"command": "echo a"})
    transcript.append(SegmentKind.OUTPUT, "".join(f"prose {n}\n" for n in range(10)))
    transcript.append_tool_result("a", "Bash", "one", is_error=False)
    transcript.append(SegmentKind.OUTPUT, "after\n")
    frames(imgui, session(transcript), TranscriptState(), 1)
    assert [b.head[0][0][2] for b, _, _ in drawn_blocks] == ["$ echo a"]


def test_a_block_is_laid_out_once_though_two_panes_draw_it_every_frame(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The log and CONTEXT draw with one state, both in a frame: what one drew is
    not let go of by the other, so neither lays a block out again.
    """
    built: list[str] = []
    real = tool_block.layout

    def counted(held: ToolEntry, live: bool) -> tool_block.Layout:
        built.append(held.tool_use_id)
        return real(held, live)

    monkeypatch.setattr(tool_block, "layout", counted)
    snap = session(calls(("a", "one")))
    state = TranscriptState()

    def both() -> None:
        transcript_pane.draw(snap, state, ROOT)
        transcript_pane.draw(snap, state, ROOT)

    for _ in range(4):
        rig.frame(imgui, both)
    assert built == ["a"]
    assert len(state.layouts) == 1


# -- a block is part of the log: wheel, follow and the context menu ----------------------


def long_log_with_blocks() -> Transcript:
    """Prose enough to scroll, then calls whose blocks fill the foot of the log."""
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "".join(f"Paragraph {n}.\n\n" for n in range(80)))
    for n in range(8):
        tool_use_id = f"c{n}"
        transcript.append_tool_call(tool_use_id, "Bash", {"command": f"echo {n}"})
        output = "".join(f"out {n}.{k}\n" for k in range(5))
        transcript.append_tool_result(tool_use_id, "Bash", output, is_error=False)
    return transcript


class BlockPane:
    """
    ``tests.test_transcript_pane.Pane``, with where each block was drawn, and every
    menu item the context menu offered.
    """

    def __init__(
        self,
        im: Any,
        monkeypatch: pytest.MonkeyPatch,
        state: TranscriptState,
        transcript: Transcript | None = None,
    ) -> None:
        from tests.test_transcript_pane import Pane

        self.rects: list[tuple[float, float, float, float]] = []
        self.offered: list[str] = []
        real_draw, real_item = tool_block.draw, im.menu_item_simple

        def spy(block: tool_block.Layout, **given: Any) -> bool:
            pressed = real_draw(block, **given)
            low, high = im.get_item_rect_min(), im.get_item_rect_max()
            self.rects.append((low.x, low.y, high.x, high.y))
            return pressed

        def item(label: str, *rest: Any) -> bool:
            self.offered.append(label)
            return bool(real_item(label, *rest))

        monkeypatch.setattr(tool_block, "draw", spy)
        monkeypatch.setattr(im, "menu_item_simple", item)
        self.transcript = long_log_with_blocks() if transcript is None else transcript
        self.pane = Pane(im, monkeypatch, session(self.transcript), state)
        self.im = im

    def frames(self, count: int) -> None:
        self.rects.clear()
        self.pane.frames(count)

    def over_a_block(self) -> tuple[float, float]:
        """The middle of the last block drawn that is wholly inside the log."""
        left, top, right, bottom = self.pane.child
        inside = [r for r in self.rects if r[1] >= top and r[3] <= bottom]
        assert inside, (self.rects, self.pane.child)
        low_x, low_y, high_x, high_y = inside[-1]
        return ((low_x + high_x) / 2, (low_y + high_y) / 2)

    def leave(self) -> None:
        io = self.im.get_io()
        io.add_mouse_button_event(1, False)
        self.pane.leave()


@pytest.fixture
def block_pane(imgui: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    made: list[BlockPane] = []

    def make(state: TranscriptState, transcript: Transcript | None = None) -> BlockPane:
        made.append(BlockPane(imgui, monkeypatch, state, transcript))
        return made[-1]

    yield make
    for each in made:
        each.leave()


@pytest.mark.parametrize("typeset_on", [False, True], ids=["wrap", "typeset"])
def test_a_wheel_up_over_a_block_lets_go_of_the_foot_and_moves_the_view(
    request: pytest.FixtureRequest, block_pane: Any, typeset_on: bool
) -> None:
    if typeset_on:
        request.getfixturevalue("seam")
    view = block_pane(TranscriptState(mode=RenderMode.TYPESET if typeset_on else None))
    view.frames(5)
    assert view.pane.state.follow_tail and view.pane.at_foot()
    io = view.im.get_io()
    io.add_mouse_pos_event(*view.over_a_block())
    view.frames(2)
    io.add_mouse_wheel_event(0.0, 3.0)
    view.frames(3)
    assert not view.pane.state.follow_tail, "a wheel up over a block is a wheel up over the log"
    assert not view.pane.at_foot(), "and the view moved off the foot"


@pytest.mark.parametrize("typeset_on", [False, True], ids=["wrap", "typeset"])
def test_a_right_click_on_a_block_opens_the_logs_menu_with_the_blocks_copy_items(
    request: pytest.FixtureRequest, block_pane: Any, typeset_on: bool
) -> None:
    if typeset_on:
        request.getfixturevalue("seam")
    view = block_pane(TranscriptState(mode=RenderMode.TYPESET if typeset_on else None))
    view.frames(5)
    io = view.im.get_io()
    io.add_mouse_pos_event(*view.over_a_block())
    view.frames(2)
    io.add_mouse_button_event(1, True)
    view.frames(1)
    io.add_mouse_button_event(1, False)
    view.offered.clear()
    view.frames(2)
    assert "copy visible" in view.offered, "the log's menu opened over a block"
    if typeset_on:
        assert "copy block" in view.offered
    assert any(label.startswith("copy tool ") for label in view.offered), view.offered


# -- every byte of an entry: drawn whole, or behind "show all" ---------------------------
#
# Independent of each tool's layout: a block that leaves its affordance off must
# draw every input value and every line of the output. Compared with the whitespace
# taken out, since a block sets columns and rows of its own. The only exceptions
# are the renderings named here, each shown in another form that says the same.


def _flag(value: object) -> bool:
    return isinstance(value, bool)


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _names(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _checklist(value: object) -> bool:
    """Todos each drawn whole: the text not drawn, where there is one, is the one drawn."""
    if not isinstance(value, list):
        return False
    for todo in value:
        if not isinstance(todo, dict) or not set(todo) <= {"content", "status", "activeForm"}:
            return False
        if todo.get("status") not in ("completed", "in_progress", "pending"):
            return False
        if "content" in todo and "activeForm" in todo and todo["content"] != todo["activeForm"]:
            return False
    return True


DERIVED_KEYS: dict[str, dict[str, Callable[[object], bool]]] = {
    "Bash": {"timeout": _number, "run_in_background": _flag, "dangerouslyDisableSandbox": _flag},
    "Read": {"offset": _whole, "limit": _whole},
    "Edit": {"replace_all": _flag},
    "Grep": {
        "-i": _flag,
        "-n": _flag,
        "-o": _flag,
        "multiline": _flag,
        "output_mode": lambda v: isinstance(v, str),
    },
    "WebSearch": {"allowed_domains": _names, "blocked_domains": _names},
    "Agent": {"run_in_background": _flag},
    "Task": {"run_in_background": _flag},
    "TodoWrite": {"todos": _checklist},
}
"""
Input keys a block may show in a form of its own, and the values it can: a
number of milliseconds as minutes, a flag as a word (and a false one as no
word), a list of domains joined, a checklist whose two texts agree.
"""


def _squeezed(text: str) -> str:
    return "".join(text.split())


def _derived_output_line(name: str, line: str, failed: bool) -> bool:
    """Output lines a block says in a form of its own: the exit code, a match count."""
    if name == "Bash" and failed and line.startswith("Exit code "):
        return True
    if name == "Grep" and (line.startswith("Found ") or line == "No files found"):
        return True
    return False


def _text_of_text_blocks(output: str) -> str:
    """
    A result held as the ``repr`` of a list of text blocks and nothing else, as its
    text: the block draws the text and not the list around it. Any other block --
    an image -- keeps the ``repr`` as what has to be drawn.
    """
    import ast

    try:
        blocks = ast.literal_eval(output) if output.startswith("[{") else None
    except (ValueError, SyntaxError):
        return output
    if not isinstance(blocks, list) or not all(
        isinstance(b, dict) and set(b) == {"type", "text"} and b["type"] == "text" for b in blocks
    ):
        return output
    return "\n".join(b["text"] for b in blocks)


def unreached(held: ToolEntry, live: bool = False) -> list[str]:
    """
    What of ``held`` its collapsed block neither draws nor puts behind an
    affordance: the input keys and output lines it leaves out. Empty where the
    affordance is there.
    """
    block = tool_block.layout(held, live)
    if block.more:
        return []
    drawn = _squeezed("\n".join(tool_block.strings(block)))
    name = held.name
    missing: list[str] = []
    for key, value in (held.input or {}).items():
        derived = DERIVED_KEYS.get(name, {}).get(key)
        if derived is not None and derived(value):
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if _squeezed(drawable.text(text)) not in drawn:
            missing.append(f"input {key}")
    result = held.result
    if result is not None:
        output = result.output
        failed = result.is_error
        if failed:
            output = tool_block._unwrap_error(output)
        output = _text_of_text_blocks(output)
        for line in output.splitlines():
            if _derived_output_line(name, line, failed):
                continue
            if _squeezed(drawable.text(line)) not in drawn:
                missing.append(f"output {line[:40]!r}")
    return missing


def test_every_proof_cell_draws_all_of_its_entry_or_offers_show_all() -> None:
    _, cells = _probe().build_proof_cases()
    left = {(c.tool, c.column): unreached(c.entry, c.live) for c in cells}
    assert {where: gone for where, gone in left.items() if gone} == {}


REACH_CASES = [
    (
        "Edit",
        {"file_path": "/a.py", "old_string": "x", "new_string": "y"},
        "The file /a.py has been updated successfully.",
        False,
    ),
    ("Edit", {"file_path": "/a.py", "old_string": "x\n", "new_string": "y"}, "ok", False),
    (
        "Write",
        {"file_path": "/a.py", "content": "one\ntwo"},
        "File created successfully at: /a.py",
        False,
    ),
    (
        "TodoWrite",
        {"todos": [{"content": "write it", "status": "in_progress", "activeForm": "Writing it"}]},
        "Todos have been modified",
        False,
    ),
    (
        "TodoWrite",
        {"todos": [{"content": "wrote it", "status": "completed", "activeForm": "Writing it"}]},
        "ok",
        False,
    ),
    (
        "NotebookEdit",
        {"notebook_path": "/n.ipynb", "cell_id": "c1", "new_source": "x = 1"},
        "Updated cell c1",
        False,
    ),
    (
        "NotebookEdit",
        {"notebook_path": "/n.ipynb", "cell_id": "c1", "new_source": "gone", "edit_mode": "delete"},
        "Deleted cell c1",
        False,
    ),
    ("Bash", {"command": "ls", "frobnicate": "yes"}, "a", False),
    ("Read", {"file_path": "/a.py", "offset": "10"}, "     1\tx", False),
    ("Bash", {"command": "sleep 1", "timeout": "soon"}, "", False),
    (
        "mcp__srv__shot",
        {},
        repr(
            [
                {"type": "text", "text": "here"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="},
                },
            ]
        ),
        False,
    ),
]


@pytest.mark.parametrize(("name", "args", "output", "failed"), REACH_CASES)
def test_what_a_block_leaves_out_is_behind_show_all(
    name: str, args: dict[str, Any], output: str, failed: bool
) -> None:
    held = entry(name, args, output, error=failed)
    assert unreached(held) == []


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("failed", [False, True], ids=["done", "error"])
def test_every_tool_with_every_field_draws_all_of_it_or_offers_show_all(
    name: str, failed: bool
) -> None:
    for args in (odd_args(name), {"command": "c", "file_path": "/f", "pattern": "p"}):
        for output in ("one line", "a\nb", ""):
            held = entry(name, args, output, error=failed)
            assert unreached(held) == [], (args, output)


def test_the_viewer_names_a_content_block_that_is_not_text_by_its_type_and_size() -> None:
    data = "iVBORw0KGgo=" * 100
    shot = repr(
        [
            {"type": "text", "text": "here"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": data},
            },
        ]
    )
    held = entry("mcp__srv__shot", {}, shot)
    assert tool_block.layout(held, live=False).more == SHOW_ALL, "the image is not drawn"
    said = texts(tool_block.viewer_rows(held))
    at = said.index("  2 content blocks:")
    assert said[at + 1] == "  1. text · 4 characters"
    assert said[at + 2] == f"  2. image · image/png · {900} B as base64 {1.2:.1f} KB"
    assert output_back(tool_block.viewer_rows(held)).endswith(shot[-50:]), "and the repr is kept"


# -- a message that ends on its fence is coloured once its turn is over ------------------


def test_a_last_message_ending_on_its_closing_fence_is_coloured_when_the_turn_ends(
    seam: Any,
) -> None:
    from pptmstr.ui.typeset import themes

    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "Here it is.\n\n```python\ndef f(x):\n\treturn x\n```")
    state = TranscriptState(mode=RenderMode.TYPESET)
    frames(seam, session(transcript, AgentState.THINKING), state, 2)
    frames(seam, session(transcript, AgentState.AWAITING_INPUT), state, 3)
    feed = state.feeds[ROOT]
    assert feed.open is not None, "the run is the last one, and still the open run"
    view = typeset.stream(feed.views, ("open", feed.open.run)).view
    (block,) = [n for n, text in enumerate(view.texts) if "return x" in str(text)]
    text = str(view.texts[block])
    keyword = themes.syntax_inks().keyword
    coloured = {text[s:e] for s, e, c, *_ in view._colours.get(block, ()) if c == keyword}
    assert {"def", "return"} <= coloured


@pytest.mark.parametrize("palette", PALETTES, ids=IDS)
def test_a_link_in_a_block_is_the_blocks_link_ink_on_its_ground(palette: theme.Palette) -> None:
    """The footer's link is the accent as the block draws its links, on the terminal."""
    theme.set_theme(palette.name)
    inks = tool_block.link_inks()
    ground = palette.terminal_bg
    assert inks.rest.u32 == theme.terminal_ink(palette.accent).u32
    for state in (inks.rest, inks.hovered, inks.pressed, inks.disabled):
        assert contrast_ratio(state, ground) >= theme.LINK_FLOOR


@pytest.mark.parametrize(
    ("more", "said"),
    [
        ("41 more lines", ["41 more lines ·"]),
        (tool_block.SHOW_ALL, []),
        (f"{tool_block.SHOW_ALL} · 12 B past the 1 MiB cap", ["12 B past the 1 MiB cap ·"]),
    ],
    ids=["a-count", "show-all", "past-the-cap"],
)
def test_the_footer_says_what_is_left_out_then_links_show_all_on_the_buttons_id(
    monkeypatch: pytest.MonkeyPatch, more: str, said: list[str]
) -> None:
    texts: list[str] = []
    links: list[tuple[str, str | None]] = []
    monkeypatch.setattr(tool_block.imgui, "text_colored", lambda colour, text: texts.append(text))
    monkeypatch.setattr(tool_block.imgui, "same_line", lambda *args: None)
    monkeypatch.setattr(
        widgets,
        "link",
        lambda label, *, inks=None, ident=None: links.append((label, ident)) or True,
    )
    assert tool_block._affordance(more) is True
    assert texts == said
    assert links == [(tool_block.SHOW_ALL, f"{more}##more")]


def test_a_block_out_of_sight_for_a_while_is_not_laid_out_again(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A generation at rest draws none of its blocks: back in sight within the
    feed's keep, the layout made before is used; after it, made again.
    """
    built: list[str] = []
    real = tool_block.layout

    def counted(held: ToolEntry, live: bool) -> tool_block.Layout:
        built.append(held.tool_use_id)
        return real(held, live)

    monkeypatch.setattr(tool_block, "layout", counted)
    snap = session(calls(("a", "one")))
    state = TranscriptState()
    clock = [100.0]

    def shown(draw: bool) -> None:
        state.prune(imgui.get_frame_count(), now=clock[0])
        if draw:
            transcript_pane.draw(snap, state, ROOT)
        else:
            # The pane drew, and this block was not in it: what a draw lets go of.
            held = snap.get(ROOT)
            assert held is not None
            transcript_pane._Blocks(
                state, ROOT, held.transcript, synced(held.transcript), 0, False, 16.0
            ).prune()

    for draw in (True, False, False, False, True):
        rig.frame(imgui, functools.partial(shown, draw))
    assert built == ["a"] and len(state.layouts) == 1
    clock[0] += transcript_pane._FEED_KEEP_S + 1.0

    def let_go() -> None:
        state.prune(imgui.get_frame_count(), now=clock[0])
        transcript = snap.get(ROOT).transcript  # type: ignore[union-attr]
        transcript_pane._Blocks(state, ROOT, transcript, synced(transcript), 0, False, 16.0).prune()

    rig.frame(imgui, let_go)
    assert state.layouts == {}, "and let go of once its feed would be"


# -- a reply sent mid-turn does not end the turn ------------------------------------------


def test_a_reply_sent_while_a_call_runs_leaves_the_call_running(
    imgui: Any, drawn_blocks: list[Any]
) -> None:
    """
    ``AgentSession.send`` writes the prompt line at once, mid-turn too; the turn
    it begins starts when the running one ends. The call is still running.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.SYSTEM, "\n> build it\n")
    transcript.append_tool_call("slow", "Bash", {"command": "make"})
    transcript.append(SegmentKind.SYSTEM, "\n> and then the docs\n")
    frames(imgui, session(transcript, AgentState.RUNNING_TOOL), TranscriptState(), 1)
    assert {b.head[0][0][2]: b.state for b, _, _ in drawn_blocks} == {"$ make": State.RUNNING}


def test_a_call_with_no_result_has_none_coming_once_its_turn_ended(
    imgui: Any, drawn_blocks: list[Any]
) -> None:
    """
    The turn ends at its result (``Transcript.end_turn``), which the driver writes
    before it says what state the node is in: a frame between the two still reads
    the node mid-turn, and the call is not running.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.SYSTEM, "\n> build it\n")
    transcript.append_tool_call("slow", "Bash", {"command": "make"})
    transcript.end_turn()
    frames(imgui, session(transcript, AgentState.RUNNING_TOOL), TranscriptState(), 1)
    assert {b.head[0][0][2]: b.state for b, _, _ in drawn_blocks} == {"$ make": State.NO_RESULT}


def test_the_turn_a_reply_waited_for_runs_its_own_calls_and_not_the_last_ones(
    imgui: Any, drawn_blocks: list[Any]
) -> None:
    transcript = Transcript()
    transcript.append(SegmentKind.SYSTEM, "\n> build it\n")
    transcript.append_tool_call("slow", "Bash", {"command": "make"})
    transcript.append(SegmentKind.SYSTEM, "\n> and then the docs\n")
    transcript.end_turn()
    transcript.append_tool_call("docs", "Bash", {"command": "make docs"})
    frames(imgui, session(transcript, AgentState.RUNNING_TOOL), TranscriptState(), 1)
    states = {b.head[0][0][2]: b.state for b, _, _ in drawn_blocks}
    assert states == {"$ make": State.NO_RESULT, "$ make docs": State.RUNNING}


def test_a_calls_turn_is_read_at_the_frames_pinned_length() -> None:
    """
    A frame draws the bytes it pinned (I7). A turn that the writer ended and
    opened past them, with bytes since, is not this frame's: the call is still
    running in what it draws.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.SYSTEM, "\n> build it\n")
    transcript.append_tool_call("slow", "Bash", {"command": "make"})
    pinned = transcript.published_length
    transcript.append(SegmentKind.SYSTEM, "\n> and then the docs\n")
    transcript.end_turn()
    entry = transcript.tool_entry("slow")
    assert entry is not None
    at_pin = blocks_for(transcript, synced(transcript), in_turn=True, pinned=pinned)
    now = blocks_for(transcript, synced(transcript), in_turn=True)
    assert (at_pin._live(entry), now._live(entry)) == (True, False)


def test_a_wheel_over_a_viewer_that_scrolls_is_the_viewers_and_the_log_keeps_following(
    block_pane: Any,
) -> None:
    transcript = long_log_with_blocks()
    transcript.append_tool_call("big", "Bash", {"command": "cat big"})
    transcript.append_tool_result(
        "big", "Bash", "".join(f"big {n}\n" for n in range(500)), is_error=False
    )
    held = transcript.tool_entry("big")
    assert held is not None and held.call is not None
    state = TranscriptState(expanded={(ROOT, held.call[0])})
    view = block_pane(state, transcript)
    view.frames(5)
    assert view.pane.state.follow_tail
    left, top, right, bottom = view.rects[-1]
    io = view.im.get_io()
    # Inside the viewer's well, near the block's foot.
    io.add_mouse_pos_event((left + right) / 2, bottom - 40.0)
    view.frames(2)
    io.add_mouse_wheel_event(0.0, -1.0)
    view.frames(2)
    io.add_mouse_wheel_event(0.0, 1.0)
    following = []
    for _ in range(3):
        view.frames(1)
        following.append(view.pane.state.follow_tail)
    # Not even for a frame: a log let go of at its foot follows again on the next.
    assert following == [True] * 3, "the viewer scrolled, and the log did not leave its foot"


def test_a_right_click_on_a_block_copies_that_blocks_lines(
    block_pane: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = block_pane(TranscriptState())
    copied: list[str] = []
    monkeypatch.setattr(view.im, "set_clipboard_text", copied.append)
    real = view.im.menu_item_simple
    view.frames(5)
    io = view.im.get_io()
    io.add_mouse_pos_event(*view.over_a_block())
    view.frames(2)
    io.add_mouse_button_event(1, True)
    view.frames(1)
    # The harness focuses its window every frame, which closes a popup in the
    # frame after it opens: the item is picked in the frame the menu opens.
    monkeypatch.setattr(
        view.im, "menu_item_simple", lambda label, *r: bool(real(label)) or label == "copy block"
    )
    io.add_mouse_button_event(1, False)
    view.frames(1)
    assert copied and copied[0].startswith("Bash(command=echo 7)"), copied
    assert copied[0].endswith("out 7.4"), "the call's line and the result's"


def test_a_stream_closed_with_its_turn_takes_what_arrives_after_it(seam: Any) -> None:
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "First part")
    state = TranscriptState(mode=RenderMode.TYPESET)
    frames(seam, session(transcript, AgentState.AWAITING_INPUT), state, 2)
    feed = state.feeds[ROOT]
    assert feed.open is not None
    stream = typeset.stream(feed.views, ("open", feed.open.run))
    assert stream.closed
    transcript.append(SegmentKind.OUTPUT, ", and more.")
    frames(seam, session(transcript, AgentState.THINKING), state, 2)
    stream = typeset.stream(feed.views, ("open", feed.open.run))
    assert stream.text == "First part, and more." and not stream.closed


def test_an_edit_too_long_to_diff_in_a_frame_says_its_sizes_and_offers_show_all() -> None:
    old = "\n".join(f"line {n}" for n in range(3000))
    new = "\n".join(f"line {n}{'!' if n % 10 == 0 else ''}" for n in range(3000))
    block = tool_block.layout(
        entry("Edit", {"file_path": "/a", "old_string": old, "new_string": new}, "ok"), live=False
    )
    assert "3,000 lines → 3,000" in caption(block)
    assert not [s for s in caption(block) if s.startswith(("+", "−"))], "no counts of a part"
    assert texts(block.body)[:2] == ["-line 0", "+line 0!"] and block.more


def test_an_edit_with_one_change_in_a_long_string_is_diffed_whole() -> None:
    old = "\n".join(f"line {n}" for n in range(3000))
    new = old.replace("line 1500\n", "line 1500 changed\n")
    block = tool_block.layout(
        entry("Edit", {"file_path": "/a", "old_string": old, "new_string": new}, "ok"), live=False
    )
    assert caption(block)[1:3] == ["+1", "−1"]
    assert texts(block.body) == [" line 1499", "-line 1500", "+line 1500 changed", " line 1501"]


@pytest.mark.parametrize(
    "todos",
    [
        [{"content": "write it", "status": "in_progress", "activeForm": "Writing it"}],
        [{"content": "wrote it", "status": "completed", "activeForm": "Writing it"}],
        [{"content": "x", "status": "blocked"}],
        [{"content": "x", "status": "pending", "priority": "high"}],
    ],
)
def test_a_running_checklist_that_draws_one_of_two_texts_offers_show_all(
    todos: list[dict[str, str]],
) -> None:
    held = entry("TodoWrite", {"todos": todos}, answered=False)
    assert unreached(held, live=True) == []
    assert tool_block.layout(held, live=True).more == SHOW_ALL


def test_a_running_checklist_drawn_whole_needs_no_affordance() -> None:
    todos = [{"content": "Write it", "status": "in_progress", "activeForm": "Write it"}]
    held = entry("TodoWrite", {"todos": todos}, answered=False)
    assert tool_block.layout(held, live=True).more == ""


# -- positive accounting: what the block drew, and nothing else, is drawn ------------


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("Bash", {"command": "make test", "extra": "make"}),
        ("Bash", {"command": "echo 1", "retries": 1}),
        ("Bash", {"command": "true", "verbose": True}),
        ("Bash", {"command": "ls", "cwd": ""}),
        ("Read", {"file_path": "/a", "mode": "read"}),
    ],
    ids=["value-elsewhere", "number-in-command", "flag-as-json", "empty-value", "caption-word"],
)
def test_an_argument_the_block_does_not_draw_is_behind_show_all_whatever_it_holds(
    name: str, args: dict[str, Any]
) -> None:
    """
    An argument counts as drawn only where the layout drew it, and not where its
    text happens to occur elsewhere in the block: an empty value, a value inside
    the command, a word the caption says.
    """
    output = "     1\tx" if name == "Read" else "ok"
    assert tool_block.layout(entry(name, args, output), live=False).more == SHOW_ALL


def test_find_says_an_undrawn_argument_is_not_shown_though_its_text_is_drawn() -> None:
    held = entry("Bash", {"command": "make test", "extra": "make"}, "ok")
    block = tool_block.layout(held, live=False)
    rows = texts(tool_block.viewer_rows(held))
    hits = [(rows[h.row].strip(), h.shown) for h in tool_block.find(held, block, "make")]
    assert hits == [("make test", True), ("make", False)]


def test_find_tells_a_hidden_line_from_a_drawn_one_with_the_same_text() -> None:
    output = "error\n" + "".join(f"row {n}\n" for n in range(5)) + "error\n"
    held = entry("Bash", {"command": "make"}, output)
    block = tool_block.layout(held, live=False)
    assert block.more == "1 earlier line"
    rows = tool_block.viewer_rows(held)
    hits = tool_block.find(held, block, "error")
    assert [h.shown for h in hits] == [False, True], "the first is behind '1 earlier line'"
    assert texts(rows)[hits[0].row] == texts(rows)[hits[1].row] == "error"


@pytest.mark.parametrize(
    ("output", "more"),
    [("Found 2 files\na.py\nb.py", ""), ("Found 250 files\na.py\nb.py", SHOW_ALL)],
    ids=["count-drawn", "count-not-drawn"],
)
def test_greps_found_line_is_said_by_the_caption_only_when_its_count_is_the_drawn_one(
    output: str, more: str
) -> None:
    block = tool_block.layout(entry("Grep", {"pattern": "x"}, output), live=False)
    assert ("caption", "text", "2 files") in block.caption
    assert block.more == more


@pytest.mark.parametrize(
    ("output", "error", "answered", "live"),
    [
        ("The file /a has been updated successfully.", False, True, False),
        ("String to replace not found", True, True, False),
        (None, False, False, True),
    ],
    ids=["done", "error", "running"],
)
def test_an_edit_that_only_drops_a_trailing_newline_offers_show_all(
    output: str | None, error: bool, answered: bool, live: bool
) -> None:
    args = {"file_path": "/a", "old_string": "x\n", "new_string": "y"}
    held = entry("Edit", args, output, error=error, answered=answered)
    assert tool_block.layout(held, live).more == SHOW_ALL


@pytest.mark.parametrize(
    ("ms", "said"),
    [
        (1234567, "timeout 1234567 ms"),
        (1500, "timeout 1500 ms"),
        (2000, "timeout 2 s"),
        (120000, "timeout 2 min"),
        (90000, "timeout 90 s"),
        (1500.5, "timeout 1500.5 ms"),
    ],
)
def test_a_bash_timeout_is_said_exactly(ms: float, said: str) -> None:
    block = tool_block.layout(entry("Bash", {"command": "x", "timeout": ms}, "ok"), live=False)
    assert said in caption(block)
    assert block.more == ""


@pytest.mark.parametrize(
    ("name", "args", "output"),
    [
        ("Bash", {"command": "ls", "description": "list", "timeout": 2000}, "a\n\nb"),
        ("Bash", {"command": "", "run_in_background": False}, ""),
        ("Read", {"file_path": "/a", "offset": 1, "limit": 2, "pages": "1"}, numbered(2)),
        ("Edit", {"file_path": "/a", "old_string": "x", "new_string": "y"}, None),
        ("Edit", {"file_path": "/a", "old_string": "x\ny", "new_string": ""}, None),
        ("Write", {"file_path": "/a", "content": "one\ntwo"}, None),
        ("Grep", {"pattern": "p", "path": "src", "glob": "*.py", "-i": True}, "Found 1 file\na"),
        ("Grep", {"pattern": "p", "output_mode": "content", "-C": 2}, "a:1:x\n\na:3:y"),
        ("Glob", {"pattern": "*.zz", "path": "/"}, "No files found"),
        ("WebFetch", {"url": "https://h/p", "prompt": "what?"}, "One.\n\nTwo."),
        ("Agent", {"description": "d", "prompt": "p", "model": "m"}, "Done.\n\nAll."),
        ("NotebookEdit", {"notebook_path": "/n", "cell_id": "c", "new_source": "x"}, None),
        ("mcp__s__t", {"a": "1", "b": 2}, [{"type": "text", "text": "sent\nok"}]),
        ("Frob", {"a": "1"}, "done"),
    ],
)
def test_a_block_that_drew_every_line_of_its_entry_needs_no_affordance(
    name: str, args: dict[str, Any], output: object
) -> None:
    """
    The other side of the account: each layout records what it draws, so an
    entry it draws all of -- a running call where the result is one the block
    does not draw -- has no affordance.
    """
    held = entry(name, args, output, answered=output is not None)
    assert tool_block.layout(held, live=output is None).more == ""


def test_find_says_a_drawn_line_of_an_argument_is_shown_and_a_hidden_one_is_not() -> None:
    content = "".join(f"row {n} needle\n" for n in range(7)).rstrip("\n")
    held = entry("Write", {"file_path": "/a", "content": content}, answered=False)
    block = tool_block.layout(held, live=True)
    assert block.more == "2 more lines in the file"
    assert [h.shown for h in tool_block.find(held, block, "needle")] == [True] * 5 + [False] * 2


def test_find_says_no_line_of_an_unwrapped_error_is_shown() -> None:
    """
    The rows of an error the block unwrapped are not the output's lines in
    place, so none is counted as drawn, and a find opens the viewer to them.
    """
    held = entry(
        "Read", {"file_path": "/a"}, "<tool_use_error>no such file</tool_use_error>", error=True
    )
    block = tool_block.layout(held, live=False)
    assert texts(block.body) == ["no such file"]
    assert [h.shown for h in tool_block.find(held, block, "no such")] == [False]


def test_find_says_a_line_the_block_cut_is_not_shown() -> None:
    held = entry("Bash", {"command": "cat"}, "x" * (LINE_CUT * 2) + "needle")
    block = tool_block.layout(held, live=False)
    assert texts(block.body)[0].endswith("…")
    assert [h.shown for h in tool_block.find(held, block, "needle")] == [False]


def test_find_says_a_line_an_edits_diff_left_out_is_not_shown() -> None:
    old = "keep 0\nkeep 1\nchange\nkeep 3\nkeep 4"
    held = entry(
        "Edit",
        {"file_path": "/a", "old_string": old, "new_string": old.replace("change", "changed")},
        answered=False,
    )
    block = tool_block.layout(held, live=True)
    assert texts(block.body) == [" keep 1", "-change", "+changed", " keep 3"]
    assert [h.shown for h in tool_block.find(held, block, "keep 0")] == [False, False]
