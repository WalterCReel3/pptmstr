"""
The inbox's expanded approval, set through the typeset seam.

The pure parts -- which arguments head a description, when an edit is being
withheld from view, what the gate predicted -- need no ImGui. The rest runs
``inbox.draw`` in real frames on the rig ``tests/test_typeset_views.py`` builds: a context
with no backend, the real faces, and what the seam was handed to set under each key.

**What is the library's and what is not.** A description's prose (a concern's
body, a spawn's prompt, a plan) is set by the library; arguments are ImGui's
text through the seam, because their columns are content and the library does
not keep them outside its diff view. A diff is the library's diff view where
the library that is running keeps a column (``typeset.diff_adopted``, a
measurement, which holds on the one pptmstr is built against) and the pane's
own rows where that view is not what draws it: both are drawn here, the
first on ``columns_hold`` and the second on ``own_rows``
(``tests/conftest.py``).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from pptmstr import approval, fake_driver
from pptmstr.bridge import Bridge
from pptmstr.intents import AgentSpawned, ApprovalRequested, ApprovalResolved, StateChanged
from pptmstr.model import AgentState, NodeId, PendingApproval, QuestionPending, Snapshot
from pptmstr.store import Store
from pptmstr.transcript import SegmentKind, Transcript
from pptmstr.ui import detail, inbox, review, typeset
from pptmstr.ui.compose import ComposeState
from pptmstr.ui.focus import FocusState
from tests import test_typeset_views as rig

ROOT: NodeId = ("sess-1", None)

ACTIONS = inbox.InboxActions(
    send=lambda node, text: None,
    interrupt=lambda node: None,
    close=lambda node: None,
    dismiss=lambda node: None,
    relaunch=lambda spec: None,
)

CONCERN_BODY = str(fake_driver._POST_CONCERN[1]["body"])
BASH_COMMAND = str(fake_driver._BASH[1]["command"])
_NONE = inbox.Description((), "")


@pytest.fixture(autouse=True)
def no_views_held() -> Iterator[None]:
    """
    The pane's view cache is module state; a test starts and ends with it empty.
    """
    held = (inbox._VIEWS, inbox._PARTS, inbox._PREVIEWS, inbox._WIDTHS, inbox._WHOLE)
    for cache in held:
        cache.clear()
    yield
    for cache in held:
        cache.clear()


def parked(tool_name: str, raw_args: Mapping[str, Any], ident: str = "p1") -> PendingApproval:
    """
    A parked call as the driver builds one: the texts read once, the string from them.
    """
    texts = approval.diff_texts(tool_name, raw_args)
    return PendingApproval(
        id=ident,
        node=ROOT,
        tool_name=tool_name,
        tool_use_id=f"tu-{ident}",
        raw_args=raw_args,
        summary=approval.summarize(tool_name, raw_args),
        requested_at=0.0,
        diff=approval.render_texts(tool_name, texts),
        texts=texts,
    )


def write(lines: int, ident: str = "p1") -> PendingApproval:
    content = "".join(f"line_{n} = {n}\n" for n in range(lines))
    return parked("Write", {"file_path": "no/such/dir/file.py", "content": content}, ident)


class Pane:
    """
    One session with some calls parked and the cursor on the first, and everything
    ``inbox.draw`` is handed.
    """

    def __init__(self, *calls: PendingApproval) -> None:
        self.store = Store()
        self.store.apply(
            AgentSpawned(
                node_id=ROOT,
                parent=None,
                task="a session",
                model="claude-sonnet-5",
                started_at=0.0,
                cwd="/x/proj",
                transcript=Transcript(),
            )
        )
        for call in calls:
            self.store.apply(ApprovalRequested(ROOT, call))
        self.focus = FocusState()
        self.state = review.ReviewState()
        self.compose = ComposeState()
        self.bridge = Bridge()
        snap = self.snap
        if snap.needs_you:
            self.focus.to_obligation(snap.needs_you[0])
        self.height = 0.0

    @property
    def snap(self) -> Snapshot:
        return self.store.snapshot()

    def draw(self, im: Any, extra: Any = None) -> None:
        """
        One frame of the pane, recording how much of the window it claimed.
        """

        def body() -> None:
            before = im.get_cursor_pos().y
            inbox.draw(self.snap, self.focus, self.state, self.compose, self.bridge, ACTIONS, 1.0)
            self.height = im.get_cursor_pos().y - before
            if extra is not None:
                extra()

        rig.frame(im, body)


def key(call: PendingApproval, part: str) -> tuple[str, str]:
    return (f"approval:{call.id}", part)


def shown(call: PendingApproval, part: str) -> str:
    """
    The text one of the pane's keys was handed to set, a blank line between its
    blocks: the library's and ImGui's alike.
    """
    assert key(call, part) in inbox._VIEWS, part
    return "\n\n".join(inbox._VIEWS.texts(key(call, part)))


def typeset_child(im: Any) -> Any:
    """
    The scrolling child the seam opened for a capped document, as the last frame
    left it: what is seen of the document.
    """
    (window,) = (w for w in im.get_current_context().windows if "##typeset:" in w.name and w.active)
    return window


def native_body(im: Any) -> Any:
    """
    The window of the native body's child as the last frame left it.
    """
    (window,) = (w for w in im.get_current_context().windows if "##body" in w.name and w.active)
    return window


def cap(im: Any) -> float:
    """
    The cap on an expanded body, read between frames, when the font is the UI's.
    """
    im.new_frame()
    line_h = im.get_text_line_height_with_spacing()
    im.render()
    return float(inbox._BODY_LINES * line_h)


@pytest.fixture
def said(monkeypatch: pytest.MonkeyPatch, seam: Any) -> list[str]:
    """
    Every string the pane hands ``imgui.text_colored``, in order.
    """
    lines: list[str] = []
    real = seam.text_colored

    def recording(colour: Any, text: str) -> None:
        lines.append(text)
        real(colour, text)

    monkeypatch.setattr(seam, "text_colored", recording)
    return lines


# -- the pure parts ------------------------------------------------------------


def test_the_bus_tool_names_are_the_ones_the_gate_parks() -> None:
    """
    The pane spells the two names rather than importing a private of ``approval``.
    A drift would send a parked concern down the arguments branch, one escaped line.
    """
    assert inbox._POST_CONCERN == approval._BUS_POST
    assert inbox._DECLARE_TASK == approval._BUS_DECLARE


def test_a_concern_reads_as_who_and_what_and_then_its_body() -> None:
    described = inbox.describe(parked(*fake_driver._POST_CONCERN))
    assert described is not None
    assert [name for name, _ in described.fields] == ["to", "subject", "task_id"]
    assert described.body == CONCERN_BODY


def test_a_declaration_lists_what_it_writes_and_waits_on() -> None:
    described = inbox.describe(parked(*fake_driver._DECLARE_TASK))
    assert described is not None
    assert dict(described.fields) == {
        "task_id": "t9",
        "title": "cut the 0.4.0 release notes",
        "depends_on": "t3",
        "touches": "CHANGELOG.md, docs/release-0.4.0.md",
    }
    assert described.body.startswith("Builder task.")


def test_a_list_field_of_the_wrong_shape_is_shown_as_what_it_is() -> None:
    """
    ``touches`` is the set of files a task may write. A string there and a list
    holding something that is not a name must not read like a well-formed list.
    """
    shown = {
        repr(value): dict(
            (inbox.describe(parked(inbox._DECLARE_TASK, {"touches": value})) or _NONE).fields
        )["touches"]
        for value in (["a.py", "b.py"], "a.py, b.py", ["a.py", 3], [])
    }
    assert shown == {
        "['a.py', 'b.py']": "a.py, b.py",
        "'a.py, b.py'": "'a.py, b.py'",
        "['a.py', 3]": "['a.py', 3]",
        "[]": "[]",
    }


@pytest.mark.parametrize("tool", ["Agent", "Task"])
def test_a_spawn_reads_as_its_type_and_then_its_prompt(tool: str) -> None:
    call = parked(tool, {"prompt": "Do the thing.", "description": "d", "subagent_type": "x"})
    described = inbox.describe(call)
    assert described is not None
    assert described.fields == (("subagent_type", "x"), ("description", "d"))
    assert described.body == "Do the thing."


def test_an_argument_the_pane_has_never_heard_of_is_still_shown() -> None:
    """
    The hazard is a header that lists only the names it expects: an extra argument
    on a spawn -- which model, whether it runs detached -- would be approved unseen.
    """
    call = parked(
        "Agent",
        {"run_in_background": True, "prompt": "p", "model": "opus", "subagent_type": "x"},
    )
    described = inbox.describe(call)
    assert described is not None
    assert described.fields == (
        ("subagent_type", "x"),
        ("run_in_background", "True"),
        ("model", "opus"),
    )


@pytest.mark.parametrize("body", [["not", "a", "string"], 7, None, "", "  \n"])
def test_a_body_that_is_not_prose_is_shown_as_a_field_and_not_dropped(body: object) -> None:
    call = parked(inbox._POST_CONCERN, {"to": "lead", "body": body})
    described = inbox.describe(call)
    assert described is not None
    assert described.body == ""
    assert described.fields[-1][0] == "body"
    if not isinstance(body, str):
        assert described.fields[-1][1] == repr(body)


def test_a_call_with_no_prose_has_no_description() -> None:
    assert inbox.describe(parked(*fake_driver._BASH)) is None
    assert inbox.describe(write(3)) is None


def test_an_edit_is_withheld_only_while_it_differs_and_its_editor_is_closed() -> None:
    call = write(3)
    state = review.ReviewState()
    assert not inbox.edit_withheld(state, call)

    state.edits[call.id] = review.pretty_args(call)
    assert not inbox.edit_withheld(state, call)

    state.edits[call.id] = '{"file_path": "/etc/passwd", "content": ""}'
    assert inbox.edit_withheld(state, call)

    state.editing = call.id
    assert not inbox.edit_withheld(state, call)


def test_the_gates_prediction_is_each_reason_once() -> None:
    absent = parked("Edit", {"file_path": "no/such/file.py", "old_string": "a", "new_string": "b"})
    assert inbox.refusals(absent) == ("file not found",)
    assert inbox.refusals(write(3)) == ()
    assert inbox.refusals(parked(*fake_driver._BASH)) == ()

    twice = PendingApproval(
        id="p2",
        node=ROOT,
        tool_name="Edit",
        tool_use_id="tu",
        raw_args={},
        summary="",
        requested_at=0.0,
        texts=(*(absent.texts or ()), *(absent.texts or ())),
    )
    assert inbox.refusals(twice) == ("file not found",)


# -- drawn ---------------------------------------------------------------------


def test_a_parked_write_is_the_panes_own_rows_under_either_renderer(own_rows: Any) -> None:
    """
    Where the library's diff view is not what draws a diff (``own_rows``: the
    override, since the library as it is keeps its columns), the row draws the
    unified string a line to a row with typeset text on as with it off, and
    hands the seam nothing.
    """
    seam = own_rows
    limit = cap(seam)
    call = write(400)
    pane = Pane(call)
    pane.draw(seam)
    pane.draw(seam)

    assert typeset.enabled(typeset.Pane.INBOX)
    assert call.id in pane.state.diff_lines
    assert len(inbox._VIEWS) == 0
    body = native_body(seam)
    assert body.size.y == pytest.approx(limit, abs=1.0)
    assert body.scroll_max.y > 300 * seam.get_text_line_height()
    assert review.unset_lines(pane.state, call.id) == 0


def test_with_typeset_off_the_native_diff_is_drawn_and_no_view_is_held(seam: Any) -> None:
    typeset.configure(typeset=False, mono=False)
    call = write(5)
    pane = Pane(call)
    pane.draw(seam)

    assert call.id in pane.state.diff_lines
    assert len(inbox._VIEWS) == 0


def wide_write(lines: int, ident: str = "p1") -> PendingApproval:
    """
    ``write`` with one line of 300 characters in the middle of it, ending in a ``Z``.
    """
    rows = [f"line_{n} = {n}\n" for n in range(lines)]
    rows[lines // 2] = "x = '" + "a" * 293 + "Z'\n"
    return parked("Write", {"file_path": "no/such/dir/file.py", "content": "".join(rows)}, ident)


@pytest.fixture(params=["typeset", "native"])
def either_renderer(request: pytest.FixtureRequest, imgui: Any) -> Any:
    """
    The row's own renderer of a diff, reached both ways: with typeset text on,
    where the library draws and no diff is its view's (``own_rows``), and with
    it off, which needs no library.
    """
    if request.param == "typeset":
        return request.getfixturevalue("own_rows")
    typeset.configure(typeset=False)
    return imgui


def test_a_diff_row_wider_than_the_row_has_a_bar_and_its_last_character_in_reach(
    either_renderer: Any,
) -> None:
    """
    The row the approve button is on does not break a diff's rows. The end of a
    300-character one is inside what the child scrolls to, and the child shows
    that it scrolls: with no bar the wheel still reaches it and nothing says so.
    """
    im = either_renderer
    call = wide_write(5)
    pane = Pane(call)
    # Three: a window knows its scroll range from the frame before, and gains its
    # scrollbars on the frame after that.
    for _ in range(3):
        pane.draw(im)

    child = native_body(im)
    left, right = child.dc.cursor_start_pos.x, child.inner_clip_rect.max.x
    reach, bar = child.scroll_max.x, child.scrollbar_x
    # Measured in a frame of its own, which is the end of the child's.
    im.new_frame()
    widest = max(im.calc_text_size(line).x for line in pane.state.diff_lines[call.id])
    im.render()
    assert left + widest > right + 1000.0, "the fixture must run past the row"
    assert left + widest <= right + reach
    assert bar, "and there is a bar to reach it by"


def test_a_bar_under_a_short_diff_costs_it_no_line(either_renderer: Any) -> None:
    """
    A diff that fits is not scrolled down to be read, with a wide row in it or
    without: the child is taller by the bar, and only when there is one.
    """
    im = either_renderer
    narrow, wide = write(5, "narrow"), wide_write(5, "wide")
    seen = {}
    for call in (narrow, wide):
        pane = Pane(call)
        for _ in range(3):
            pane.draw(im)
        seen[call.id] = native_body(im)
    assert len(pane.state.diff_lines[wide.id]) == len((narrow.diff or "").splitlines())

    assert not seen["narrow"].scrollbar_x and seen["wide"].scrollbar_x
    bar = im.get_style().scrollbar_size
    assert seen["wide"].size.y == pytest.approx(seen["narrow"].size.y + bar)
    assert seen["wide"].scroll_max.y == pytest.approx(seen["narrow"].scroll_max.y)


def test_a_diffs_width_is_measured_once_and_forgotten_with_the_call(
    either_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    im = either_renderer
    call = wide_write(400)
    pane = Pane(call)
    pane.draw(im)
    assert key(call, "diff") in inbox._WIDTHS

    measured: list[str] = []
    real = im.calc_text_size

    def counting(text: str, *rest: Any) -> Any:
        measured.append(text)
        return real(text, *rest)

    monkeypatch.setattr(im, "calc_text_size", counting)
    pane.draw(im)
    assert not [text for text in measured if text in pane.state.diff_lines[call.id]]

    pane.store.apply(ApprovalResolved(ROOT, call.id, approved=True))
    pane.draw(im)
    assert inbox._WIDTHS == {}


def test_a_long_body_costs_the_queue_no_more_than_the_cap(seam: Any) -> None:
    limit = cap(seam)
    one = parked(inbox._POST_CONCERN, {"to": "lead", "subject": "s", "body": "One line."}, "one")
    many = parked(
        inbox._POST_CONCERN,
        {"to": "lead", "subject": "s", "body": "A paragraph of it.\n\n" * 200},
        "many",
    )
    pane = Pane(one)
    pane.draw(seam)
    short_h = pane.height
    pane = Pane(many)
    pane.draw(seam)
    assert short_h < pane.height <= short_h + limit


def test_every_word_of_a_parked_concern_is_set_including_what_reads_as_html(
    seam: Any,
) -> None:
    """
    The fixture's body has ``Optional<int>``, ``<lead>``, an unclosed ``<!--`` and a
    ``<details>`` on a line of its own. A CommonMark reader drops the first two
    tags, swallows everything after the comment opener, and hides the last; the
    approver would then release a message they had read half of.
    """
    call = parked(*fake_driver._POST_CONCERN)
    pane = Pane(call)
    pane.draw(seam)

    said = " ".join(shown(call, "body").split())
    for needle in ("Optional<int>", "ask <lead> for", "<!-- generated", "<details>"):
        assert needle in said

    def words(text: str) -> list[str]:
        # Less the marks that are markup on one side and typography on the other:
        # code and emphasis delimiters, a list marker and the bullet set for it.
        plain = text.replace("`", "").replace("*", "")
        return [word for word in plain.split() if word not in ("-", "\u2022")]

    assert words(said) == words(CONCERN_BODY)
    assert key(call, "diff") not in inbox._VIEWS
    assert pane.state.diff_lines == {call.id: []}


FENCED_BODY = (
    "Replace the helper as below.\n\n"
    "```python IMPORTANT also delete tests\n"
    "def helper() -> int:\n"
    "    return 1\n"
    "```\n\n"
    "Nothing else changes."
)


def test_what_follows_a_fences_opening_marks_is_set_with_the_body(seam: Any) -> None:
    """
    A fence's info string is text the recipient gets and a markdown reader shows
    none of: ``python IMPORTANT also delete tests`` is an instruction on a line the
    approver would never have seen.
    """
    call = parked(inbox._POST_CONCERN, {"to": "lead", "subject": "s", "body": FENCED_BODY})
    pane = Pane(call)
    pane.draw(seam)

    assert inbox._VIEWS.texts(key(call, "body")) == (
        "Replace the helper as below.",
        "python IMPORTANT also delete tests\ndef helper() -> int:\n    return 1",
        "Nothing else changes.",
    )


def test_a_view_is_dropped_when_its_call_leaves_the_queue(seam: Any) -> None:
    first, second = parked(*fake_driver._BASH, ident="first"), short_concern("second")
    pane = Pane(first, second)
    pane.draw(seam)
    pane.focus.move(pane.snap, 1)
    pane.draw(seam)
    assert key(first, "arguments") in inbox._VIEWS
    assert key(second, "body") in inbox._VIEWS

    pane.store.apply(ApprovalResolved(ROOT, first.id, approved=True))
    assert [p.id for p in pane.snap.approvals] == ["second"]
    pane.draw(seam)
    assert key(first, "arguments") not in inbox._VIEWS
    assert key(second, "body") in inbox._VIEWS


def test_the_last_call_leaving_empties_the_cache_on_the_zero_state_frame(seam: Any) -> None:
    call = short_concern("p1")
    pane = Pane(call)
    pane.draw(seam)
    assert len(inbox._VIEWS) > 0
    pane.store.apply(ApprovalResolved(ROOT, call.id, approved=True))
    assert not pane.snap.needs_you
    pane.draw(seam)
    assert len(inbox._VIEWS) == 0


def test_another_pane_draws_the_same_call_in_the_same_frame_in_a_view_of_its_own(
    seam: Any, said: list[str]
) -> None:
    """
    DETAIL follows this pane's cursor, so the two show one parked call every frame.
    A shared view would be refused by the seam as drawn twice; two views must not
    both hold a selection, or Ctrl+C copies from whichever drew last.
    """
    call = short_concern("p1")
    parts = typeset.prose_parts(CONCERN_BODY)
    pane = Pane(call)
    other = typeset.ViewCache()
    for _ in range(2):
        pane.draw(seam, lambda: typeset.document(other, call.id, parts))

    assert not [line for line in said if "drawn twice" in line]
    mine, theirs = inbox._VIEWS.view(key(call, "body")), other.view(call.id)
    assert mine is not None and theirs is not None and mine is not theirs
    assert list(mine.texts) == list(theirs.texts)

    mine.select_all()
    assert mine.owns_selection
    theirs.select_all()
    assert theirs.owns_selection
    assert not mine.owns_selection


LONG_PATH = (
    "/home/someone/worktrees/project/experiment/package/no/such/dir/" + "deeper/" * 6 + "tle.py"
)


@pytest.mark.parametrize("on", [True, False], ids=["typeset", "native"])
@pytest.mark.parametrize("path", ["no/such/file.py", LONG_PATH], ids=["short", "long"])
def test_a_predicted_refusal_is_on_a_line_of_the_panes_own_under_either_renderer(
    own_rows: Any, said: list[str], on: bool, path: str
) -> None:
    """
    Worded as a prediction, once, on a line that wraps -- and once means in the
    whole row. The diff here is the unified string under either renderer and has
    nothing else to carry it.
    """
    seam = own_rows
    typeset.configure(typeset=on)
    call = parked("Edit", {"file_path": path, "old_string": "a", "new_string": "b"})
    pane = Pane(call)
    pane.draw(seam)
    # ``said`` hears the seam's native lines as well as the pane's.
    assert [line for line in said if "may not apply" in line] == ["may not apply: file not found"]
    assert not [line for line in said if "will not apply" in line]
    assert call.id in pane.state.diff_lines and key(call, "diff") not in inbox._VIEWS


def test_a_call_that_applies_carries_no_warning_line(seam: Any, said: list[str]) -> None:
    Pane(write(5)).draw(seam)
    assert not [line for line in said if "may not apply" in line or "YOUR EDIT" in line]


def test_a_diff_of_the_agents_arguments_says_so_while_an_edit_would_be_sent_instead(
    seam: Any, said: list[str]
) -> None:
    """
    ``a`` sends ``state.edits``; the diff above it is of ``pending.texts``. With the
    editor moved to another row the two differ and nothing else on screen says so.
    """
    call, elsewhere = write(5, "mine"), write(5, "elsewhere")
    pane = Pane(call, elsewhere)
    pane.state.edits[call.id] = '{"file_path": "/etc/passwd", "content": "x"}'
    pane.state.editing = elsewhere.id
    pane.draw(seam)
    assert len([line for line in said if "YOUR EDIT" in line]) == 1

    typeset.configure(typeset=False)
    said.clear()
    pane.draw(seam)
    assert len([line for line in said if "YOUR EDIT" in line]) == 1, "the native path too"


def test_a_parked_command_is_on_screen_to_its_last_character(seam: Any, said: list[str]) -> None:
    """
    The most dangerous call approved here, and the row may be all of it the approver
    looks at. Set as written and broken to the pane, so that its end is in the
    window: not one line the pane's edge cuts, and not a ``repr`` with its quotes
    escaped.
    """
    call = parked(*fake_driver._BASH)
    pane = Pane(call)
    # Twice: a window knows how far it scrolls from the frame before.
    pane.draw(seam)
    pane.draw(seam)
    assert inbox._NO_DIFF in said
    assert key(call, "body") not in inbox._VIEWS

    # Literal text: the seam hands it to ImGui, every character, and holds no view.
    assert inbox._VIEWS.texts(key(call, "arguments")) == ("command", BASH_COMMAND)
    assert inbox._VIEWS.views(key(call, "arguments")) == ()
    assert inbox._VIEWS.unbuilt(key(call, "arguments")) == 0
    # Broken to the window: taller than its two source lines, and nothing of it
    # past the edge of the child it is in.
    assert (inbox._VIEWS.height(key(call, "arguments")) or 0.0) > 3 * typeset.line_height()
    child = typeset_child(seam)
    assert child.content_size.x <= child.size.x and child.scroll_max.x == 0.0


def test_a_command_of_several_lines_keeps_its_lines_and_its_spaces(seam: Any) -> None:
    command = "set -e\ncd /srv   &&   rm -rf  ./build\n\n    make  all"
    call = parked("Bash", {"command": command, "timeout": 600, "description": ""})
    Pane(call).draw(seam)
    assert inbox._VIEWS.texts(key(call, "arguments")) == (
        "command",
        command,
        "timeout",
        "600",
        "description",
        "''",
    )


def test_a_command_longer_than_the_cap_scrolls_in_a_view_no_taller_than_it(seam: Any) -> None:
    limit = cap(seam)
    command = "".join(f"echo {n}\n" for n in range(400))
    short, long = parked(*fake_driver._BASH, ident="short"), parked("Bash", {"command": command})

    pane = Pane(short)
    pane.draw(seam)
    short_h = pane.height
    pane = Pane(long)
    pane.draw(seam)
    assert short_h < pane.height <= short_h + limit
    assert shown(long, "arguments").rstrip().endswith("echo 399")


def test_with_typeset_off_a_command_is_the_string_wrapped_and_all_in_its_block(
    seam: Any, said: list[str]
) -> None:
    """
    The native path is the fallback when the faces fail, so it is held to the same
    thing: the command and not its ``repr``, and none of it past an edge.
    """
    typeset.configure(typeset=False)
    call = parked(*fake_driver._BASH)
    pane = Pane(call)
    # Twice: a window knows how far it scrolls from the frame before.
    pane.draw(seam)
    pane.draw(seam)
    assert f"  command = {BASH_COMMAND}" in said
    assert not [line for line in said if "\\'" in line]
    body = native_body(seam)
    line = seam.get_text_line_height()
    assert body.size.y > 3 * line, "sized by the wrapped lines, not by the argument count"
    assert body.content_size.x <= body.size.x
    assert body.content_size.y + 2 * body.window_padding.y == pytest.approx(body.size.y, abs=1.0)
    assert body.scroll_max.y == 0.0, "under the cap nothing needs scrolling to"
    assert len(inbox._VIEWS) == 0


def test_with_typeset_off_a_command_longer_than_the_cap_is_reached_by_scrolling(
    seam: Any,
) -> None:
    typeset.configure(typeset=False)
    limit = cap(seam)
    command = "".join(f"echo {n}\n" for n in range(400))
    pane = Pane(parked("Bash", {"command": command}))
    pane.draw(seam)
    pane.draw(seam)
    body = native_body(seam)
    assert body.size.y == pytest.approx(limit, abs=1.0)
    assert body.scroll_max.y > 300 * seam.get_text_line_height()


def test_a_links_destination_is_on_screen_where_the_message_is_released(seam: Any) -> None:
    """
    An approver releasing a concern releases its links, and link text is whatever
    its writer chose. The destination is set beside it.
    """
    body = "The fix is described in [the upstream notes](https://example.invalid/x) already."
    call = parked(inbox._POST_CONCERN, {"to": "lead", "subject": "s", "body": body})
    Pane(call).draw(seam)
    text = " ".join(shown(call, "body").split())
    assert "the upstream notes (https://example.invalid/x) already." in text


def test_a_body_is_read_by_the_seams_one_rule_and_not_as_markdown_outright(seam: Any) -> None:
    """
    A worker with no moment to format writes one fact a line and pastes a traceback.
    Markdown proper folds the facts into a paragraph and reads the indented frames
    as it likes; ``typeset.prose_parts`` keeps both, and is what DETAIL, the board
    and the brief read the same body with.
    """
    body = (
        "exit: 1\nfile: tle/parse.py\nstage: verify\n\n"
        "Traceback (most recent call last):\n"
        '  File "tle/parse.py", line 3, in parse\n'
        "    raise ValueError(line)\n"
    )
    parts = typeset.prose_parts(body)
    assert typeset.Kind.LITERAL in {part.kind for part in parts}, "the fixture must need the rule"

    call = parked(inbox._POST_CONCERN, {"to": "lead", "body": body})
    Pane(call).draw(seam)
    text = shown(call, "body")
    assert "exit: 1\nfile: tle/parse.py\nstage: verify" in text
    assert '  File "tle/parse.py", line 3, in parse\n    raise ValueError(line)' in text

    other = typeset.ViewCache()
    rig.frame(seam, lambda: typeset.document(other, "same", parts))
    assert text == "\n\n".join(other.texts("same"))


def test_a_bodys_parts_are_made_once_and_forgotten_with_the_call(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = typeset.prose_parts

    def counting(text: str, **kwargs: Any) -> tuple[typeset.Part, ...]:
        calls.append(text)
        return real(text, **kwargs)

    monkeypatch.setattr(typeset, "prose_parts", counting)
    call = parked(*fake_driver._POST_CONCERN)
    pane = Pane(call)
    for _ in range(3):
        pane.draw(seam)
    assert calls == [CONCERN_BODY]
    assert key(call, "body") in inbox._PARTS

    pane.store.apply(ApprovalResolved(ROOT, call.id, approved=True))
    pane.draw(seam)
    assert inbox._PARTS == {}


def test_an_arguments_name_is_set_tight_over_its_value() -> None:
    name, value = inbox.argument_parts({"command": "ls"})
    assert (name.text, name.tight) == ("command", True)
    assert not value.tight


def test_a_command_of_several_lines_is_broken_at_the_rows_edge_whatever_the_role_says() -> None:
    """
    The seam sets an argument of several lines unbroken, for a pane that scrolls
    sideways to it (DETAIL). This row is where the approve button is, under a cap:
    text past its edge is text approved unread, so here every value is broken at
    the edge with its spaces kept, and stays so if the role's entry changes.
    """
    command = "set -e\ncd /srv   &&   rm -rf  ./build\n" + "x" * 300
    assert typeset.multiline_kind(typeset.Role.ARGUMENT) is typeset.Kind.LITERAL
    name, value = inbox.argument_parts({"command": command})
    assert value.kind is typeset.Kind.WRAPPED_EXACT and value.text == command
    assert name.role is value.role is typeset.Role.ARGUMENT


def test_a_call_parked_with_no_parts_shows_its_arguments_and_not_an_empty_diff(
    seam: Any, said: list[str]
) -> None:
    """
    ``texts`` may be an empty tuple: the record allows it, though ``diff_texts`` does
    not produce one today. A pane that drew nothing for it would leave the approver
    a row with a reason box and no body.
    """
    call = PendingApproval(
        id="p1",
        node=ROOT,
        tool_name="MultiEdit",
        tool_use_id="tu",
        raw_args={"file_path": "tle/parse.py", "edits": []},
        summary="MultiEdit tle/parse.py",
        requested_at=0.0,
        diff="",
        texts=(),
    )
    Pane(call).draw(seam)
    assert inbox._NO_DIFF in said
    assert shown(call, "arguments").split("\n\n") == ["file_path", "tle/parse.py", "edits", "[]"]
    assert key(call, "diff") not in inbox._VIEWS


# -- nothing the agent wrote is absent from what the row can show ------------------

UNBREAKABLE = "/srv/releases/" + "a-very-long-directory-name/" * 9 + "the-end-of-the-path.tar.gz"


def test_nothing_written_in_a_concern_is_absent_from_what_the_row_can_bring_on_screen(
    seam: Any, said: list[str]
) -> None:
    """
    The body an approval releases, with everything in it a markdown reader hides
    some of (``rig.STREAMED``) and a path no line can hold. Every word is in what
    the row was handed to set, the fields in the chrome's font, and the capped
    child the body is in scrolls sideways as far as the path runs: the library
    does not break it, and its end would otherwise be off the row.
    """
    body = f"{rig.STREAMED}\nUnpack {UNBREAKABLE} first.\n"
    args = {"to": "lead", "subject": "fix → deploy ✓", "body": body}
    call = parked(inbox._POST_CONCERN, args)
    pane = Pane(call)
    # Three: a window knows its scroll range from the frame before, and gains its
    # scrollbars on the frame after that.
    for _ in range(3):
        said.clear()
        pane.draw(seam)

    handed = "\n".join(inbox._VIEWS.texts(key(call, "body")))
    assert [word for word in rig.STREAMED_WORDS if word not in handed] == []
    for cell in ("b<br>c", "<i>x</i>", "**/*"):
        assert cell in handed
    assert f"Unpack {UNBREAKABLE} first." in handed
    assert inbox._VIEWS.unbuilt(key(call, "body")) == 0
    # The fields are ImGui's own lines, in a font that has the arrow and the tick.
    assert "fix → deploy ✓" in said and "lead" in said
    chrome = seam.get_io().fonts.fonts[0]
    assert chrome.is_glyph_in_font(ord("→")) and chrome.is_glyph_in_font(ord("✓"))

    reach = max(
        float(line.right)
        for view in inbox._VIEWS.views(key(call, "body"))
        for line in view.set_lines()
    )
    child = typeset_child(seam)
    visible = child.inner_rect.max.x - child.inner_rect.min.x
    assert reach > visible + 200.0, "the fixture must run past the row"
    assert child.scroll_max.x + visible >= reach - 1.0
    assert child.scrollbar_x, "and there is a bar to reach it by"


# -- where the library keeps a column, the diff is its diff view --------------------
#
# ``columns_hold`` (tests/conftest.py) is the product as it starts, held to the
# library keeping a monospace column by the row's own measurement, with
# nothing faked. The tests above are the branch a row takes wherever the
# library's view is not what draws a diff (``own_rows``).


def diff_view(call: PendingApproval) -> Any:
    return inbox._VIEWS.view(key(call, "diff"))


def added(view: Any) -> list[str]:
    return [
        row.text
        for file in view.diff.files
        for hunk in file.hunks
        for row in hunk.rows
        if row.kind.value == "+"
    ]


def no_native_body(im: Any) -> bool:
    return not [w for w in im.get_current_context().windows if "##body" in w.name and w.active]


def test_where_columns_hold_a_parked_write_is_the_librarys_diff_view_no_taller_than_the_cap(
    columns_hold: Any, said: list[str]
) -> None:
    """
    The same call as ``test_a_parked_write_is_the_panes_own_rows_under_either_
    renderer``, where the library that is running keeps a column: the rows are
    the library's diff view of the call's texts, every one of them in it, in a
    view that is ``_BODY_LINES`` of its own rows tall and scrolls inside itself.
    The row's own child is not drawn.
    """
    im = columns_hold
    call = write(400)
    pane = Pane(call)
    pane.draw(im)
    pane.draw(im)

    assert typeset.diff_adopted() and typeset.diff_draws(call)
    view = diff_view(call)
    assert view is not None and no_native_body(im)
    assert len(added(view)) == 400 and added(view)[-1] == "line_399 = 399"
    assert view.row_count > 400
    row = typeset.diff_row_height()
    assert view.grid.row == row and view.height_cap == inbox._BODY_LINES * row
    tall = inbox._VIEWS.height(key(call, "diff"))
    label = im.get_text_line_height_with_spacing()
    assert tall is not None and tall - label <= inbox._BODY_LINES * row + 8.0
    assert "no/such/dir/file.py  (new file)" in said, "the header row's words, above the view"
    assert not [line for line in said if line.startswith("+line_")]
    assert review.unset_lines(pane.state, call.id) == 0


def test_where_columns_hold_a_row_wider_than_the_view_is_one_row_and_its_end_is_in_reach(
    columns_hold: Any,
) -> None:
    im = columns_hold
    call = wide_write(5)
    pane = Pane(call)
    pane.draw(im)
    view = diff_view(call)
    widest = max(added(view), key=len)
    assert len(widest) == 300 and widest.endswith("Z'")
    measure = rig.WINDOW[0]
    assert view.content_width >= 300 * 8.0 > 2 * measure
    assert view.height_for(measure) > view.content_height, "taller by the bar that reaches it"
    view.scroll_x = 10**6
    pane.draw(im)
    assert view.scroll_x > 1500
    assert view.content_width - view.scroll_x <= measure


def test_where_columns_hold_a_call_with_no_texts_is_still_the_rows_own_child(
    columns_hold: Any, said: list[str]
) -> None:
    """
    A record carrying a unified string and no texts is drawn as that string: read
    back into a model it can hold a line the diff view draws nowhere.
    """
    import dataclasses

    diff = "said over the header\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    call = dataclasses.replace(parked("Bash", {"command": "ls"}), texts=None, diff=diff)
    pane = Pane(call)
    pane.draw(columns_hold)
    assert typeset.diff_adopted() and not typeset.diff_draws(call)
    assert diff_view(call) is None and not no_native_body(columns_hold)
    assert [line for line in diff.splitlines() if line not in said] == []


def test_where_columns_hold_the_prediction_is_said_before_the_path_and_not_by_the_view(
    columns_hold: Any, said: list[str]
) -> None:
    call = parked("Edit", {"file_path": "no/such.py", "old_string": "a", "new_string": "b"})
    pane = Pane(call)
    pane.draw(columns_hold)
    assert [line for line in said if "may not apply" in line] == ["may not apply: file not found"]
    assert said.index("may not apply: file not found") < said.index("no/such.py")
    assert [file.will_not_apply for file in diff_view(call).diff.files] == [""]


@pytest.mark.parametrize("held", [False, True], ids=["own rows", "the library's view"])
def test_a_write_over_a_file_that_is_not_text_says_so_above_the_diff(
    request: pytest.FixtureRequest, said: list[str], tmp_path: Any, held: bool
) -> None:
    """
    A Write over a binary is every line added, as a file being created is. The
    record knows the difference, and the row says it above the diff whoever
    draws the rows.
    """
    im = request.getfixturevalue("columns_hold" if held else "own_rows")
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\xff\xfe\x00\x01 not text")
    call = parked("Write", {"file_path": str(target), "content": "now text\n"})
    pane = Pane(call)
    pane.draw(im)
    (line,) = inbox.shown_as_new(call.texts)
    assert "not readable as text" in line and said.count(line) == 1
    assert (diff_view(call) is not None) is held
    assert ("+now text" in said) is not held
    # Once: the view's file header is set above it as well, and is the path
    # and its state without the row's sentence over again.
    assert not [text for text in said if "shown as new)" in text]
    if held:
        assert said.index(line) < said.index(f"{target}  (new file)")


FENCED = (
    "Apply this before the merge.\n\n"
    "```diff\n--- a/pool.py\n+++ b/pool.py\n@@ -1 +1 @@ def size():\n-cap = 4\n+cap = 8\n```\n\n"
    "```diff\nAlso run the cleanup afterwards.\n"
    "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\n\n"
    + "".join(f"Paragraph {n} of the reasons.\n\n" for n in range(40))
)
"""
A concern's body with two fenced diffs and enough prose under them to pass the
row's cap: the first an honest diff, the second with a sentence over its header.
"""


@pytest.mark.parametrize("held", [False, True], ids=["own rows", "the library's view"])
def test_every_line_of_a_fenced_diff_in_a_concern_is_set_whoever_draws_a_diff(
    request: pytest.FixtureRequest, held: bool
) -> None:
    """
    The body an approve releases can hold a diff of its own. Where the library
    keeps a column the honest one is a diff view inside the body's document,
    in the capped child the row scrolls, with its header lines set over it;
    where it does not, it is the code of the document. The fence with a
    sentence where no diff view would draw it is code either way, and no line
    of either is missing from what the row was handed to set.
    """
    im = request.getfixturevalue("columns_hold" if held else "own_rows")
    call = parked("mcp__pptmstr__post_concern", {"to": "lead", "subject": "s", "body": FENCED})
    pane = Pane(call)
    for _ in range(3):
        pane.draw(im)
    (view,) = inbox._VIEWS.views(key(call, "body"))
    texts = inbox._VIEWS.texts(key(call, "body"))
    assert inbox._VIEWS.unbuilt(key(call, "body")) == 0
    assert review.unset_lines(pane.state, call.id) == 0
    assert texts[0] == "Apply this before the merge."
    sentence = "diff\nAlso run the cleanup afterwards.\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b"
    assert sentence in texts, "code, under the word it was opened with"
    first = "--- a/pool.py\n+++ b/pool.py\n@@ -1 +1 @@ def size():\n-cap = 4\n+cap = 8"
    if not held:
        assert not view.hosts and texts[1] == "diff\n" + first
        return
    assert list(view.hosts) == [2]
    assert texts[1] == "--- a/pool.py\n+++ b/pool.py\n@@ -1 +1 @@ def size():"
    hosted = view.hosted(2)
    assert hosted.text == first + "\n" and hosted.view.origin is not None
    assert (hosted.view.diff.added, hosted.view.diff.removed) == (1, 1)
    child = typeset_child(im)
    assert child.size.y <= cap(im) + 1.0 and child.scroll_max.y > 0, "capped, and it scrolls"


def diff_view_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make the library's diff view stop inside its draw, and refuse to be made.
    """

    def stopped(self: Any, *given: Any, **named: Any) -> None:
        raise rig.advtext.TypesetUnavailable("imtypeset stopped: the library said no")

    monkeypatch.setattr(rig.advtext.TypesetDiffView, "draw", stopped)
    monkeypatch.setattr(rig.advtext.TypesetDiffView, "__init__", stopped)


@pytest.mark.parametrize("first", [False, True], ids=["a view held", "a key never drawn"])
def test_where_columns_hold_a_diff_the_library_stopped_under_cannot_be_approved_by_a_click(
    columns_hold: Any,
    said: list[str],
    monkeypatch: pytest.MonkeyPatch,
    first: bool,
) -> None:
    """
    The hazard an approval cannot have, on the branch that hands the diff to the
    library. The library stops inside the diff view's draw, or refuses to make
    a view for a row the cursor has just reached: the row shows a sentence
    where the diff would be, says the body is not on screen, and a click on
    approve sends nothing. The next frame is the row's own rows, and the click
    lands.
    """
    im = columns_hold
    call = write(5)
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    if not first:
        pane.draw(im)
        assert review.unset_lines(pane.state, call.id) == 0
    clicks = request_clicks(im, monkeypatch)
    diff_view_stops(monkeypatch)

    said.clear()
    pane.draw(im)
    assert inbox._VIEWS.unbuilt(key(call, "diff")) == 5
    assert pane.state.bodies[call.id]["inbox"][1] == 5
    assert review.withheld(pane.state, call) == review.NOT_WHOLE
    assert clicks == [True] and sent == []
    assert inbox._NOT_ON_SCREEN in said
    assert not [line for line in said if line.startswith("+line_")]

    said.clear()
    pane.draw(im)
    assert not typeset.enabled(typeset.Pane.INBOX)
    assert [line for line in said if line.startswith("+line_")], "the row's own rows"
    assert clicks[-1] is False and sent == [call.id]
    assert inbox._NOT_ON_SCREEN not in said


# -- a body is on screen before it can be approved ---------------------------------


def request_clicks(seam: Any, monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """
    Every ``approve`` button reports a click on every frame it is drawn enabled, as
    ImGui would for a mouse held over it, from here on. The list has, per frame
    the button was drawn, whether it was disabled.
    """
    depth: list[bool] = []
    drawn: list[bool] = []
    real_begin, real_end, real_button = seam.begin_disabled, seam.end_disabled, seam.button

    def begin(disabled: bool = True) -> None:
        depth.append(disabled)
        real_begin(disabled)

    def end() -> None:
        depth.pop()
        real_end()

    def button(label: str, *rest: Any) -> bool:
        pressed = bool(real_button(label, *rest))
        if label != "approve":
            return pressed
        drawn.append(any(depth))
        return not any(depth)

    monkeypatch.setattr(seam, "begin_disabled", begin)
    monkeypatch.setattr(seam, "end_disabled", end)
    monkeypatch.setattr(seam, "button", button)
    return drawn


@pytest.fixture
def clicking_approve(monkeypatch: pytest.MonkeyPatch, seam: Any) -> list[bool]:
    """
    ``request_clicks``, from the start of the test.
    """
    return request_clicks(seam, monkeypatch)


def _sent(pane: Pane, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(pane.bridge, "resolve", lambda ident, decision: sent.append(ident))
    return sent


def library_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make every view of the library's stop inside its draw, as the library does
    when it fails: the one way text handed to the seam is not on screen.
    """

    def stopped(self: Any, **given: Any) -> bool:
        raise rig.advtext.TypesetUnavailable("imtypeset stopped: the library said no")

    monkeypatch.setattr(rig.advtext.TypesetView, "draw", stopped)


def test_a_body_the_library_stopped_under_says_so_and_cannot_be_approved_by_a_click(
    seam: Any, said: list[str], clicking_approve: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The frame the library stops in shows a sentence where the body would be. The
    row says the body is not on screen and a click on approve sends nothing; the
    next frame is the row's own renderer, all of it, and the click lands.
    """
    call = long_concern()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    library_stops(monkeypatch)

    pane.draw(seam)
    left = inbox._VIEWS.unbuilt(key(call, "body"))
    assert left == call.raw_args["body"].count("\n") + 1
    assert pane.state.bodies[call.id]["inbox"][1] == left
    assert clicking_approve == [True] and sent == []
    assert inbox._NOT_ON_SCREEN in said

    said.clear()
    pane.draw(seam)
    assert not typeset.enabled(typeset.Pane.INBOX)
    assert call.id in pane.state.diff_lines, "the row's own renderer drew it"
    assert clicking_approve[-1] is False and sent == [call.id]
    assert inbox._NOT_ON_SCREEN not in said


def test_a_body_first_drawn_after_the_library_stopped_cannot_be_approved_by_a_click(
    seam: Any, said: list[str], clicking_approve: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The library stopped under another pane earlier in the frame the cursor
    reached this call, so the row's keys are new and no view can be made for
    them. The body is a sentence, the row says it is not on screen and a click
    on approve sends nothing; the next frame is the row's own renderer.
    """
    call = long_concern()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    rig.no_view_can_be_made(monkeypatch)

    pane.draw(seam)
    assert inbox._VIEWS.unbuilt(key(call, "body")) == call.raw_args["body"].count("\n") + 1
    assert review.withheld(pane.state, call) == review.NOT_WHOLE
    assert clicking_approve == [True] and sent == []
    assert inbox._NOT_ON_SCREEN in said

    pane.draw(seam)
    assert not typeset.enabled(typeset.Pane.INBOX)
    assert clicking_approve[-1] is False and sent == [call.id]


def test_a_long_body_is_whole_and_approvable_on_its_first_frame(
    seam: Any, said: list[str], clicking_approve: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Nothing is built over frames: 1,200 lines of concern are read and laid out in
    the frame that first draws them, so the body that frame shows is the body.
    """
    call = long_concern()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    pane.draw(seam)
    assert inbox._VIEWS.unbuilt(key(call, "body")) == 0
    assert "Paragraph599" in shown(call, "body").replace(" ", "")
    assert len(inbox._VIEWS.texts(key(call, "body"))) == 600
    assert clicking_approve == [False] and sent == [call.id]
    assert inbox._NOT_ON_SCREEN not in said


def test_an_ordinary_body_is_whole_and_approvable_on_its_first_frame(
    seam: Any, said: list[str], clicking_approve: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    call = parked("mcp__pptmstr__post_concern", dict(fake_driver._POST_CONCERN[1]))
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    pane.draw(seam)
    assert clicking_approve == [False] and sent == [call.id]
    assert inbox._NOT_ON_SCREEN not in said


# -- a body past the bound is set as far as the bound, and the rest when asked ------


def paragraphs(count: int) -> str:
    """
    ``count`` paragraphs, two lines each as ``review.head_lines`` counts them, and
    one more line for the end of the last.
    """
    return "".join(f"Paragraph {n} asks for something else to be done.\n\n" for n in range(count))


def past_the_bound(ident: str = "p1") -> PendingApproval:
    """
    A concern 201 lines longer than a pane sets unasked.
    """
    args = {"to": "lead", "subject": "s", "body": paragraphs(review.SETTLED_LINES // 2 + 100)}
    return parked(inbox._POST_CONCERN, args, ident)


@pytest.fixture
def setting_all(monkeypatch: pytest.MonkeyPatch, seam: Any) -> list[str]:
    """
    Every ``set all`` button drawn, by label; each reports a click while the list
    it returns holds ``"click"``.
    """
    drawn: list[str] = []
    real = seam.small_button

    def small_button(label: str) -> bool:
        pressed = bool(real(label))
        if not label.startswith("set all"):
            return pressed
        clicked = "click" in drawn
        drawn.append(label)
        return clicked

    monkeypatch.setattr(seam, "small_button", small_button)
    return drawn


def test_the_bound_on_a_body_is_details_bound_counted_as_detail_counts(seam: Any) -> None:
    """
    One rule for a document set in one frame, not one a pane: this row and DETAIL
    cut a concern's body at the same line and count the same lines after it.
    """
    limit = review.SETTLED_LINES
    for lines in (1, limit - 1, limit, limit + 1, limit + 500):
        body = "\n".join(f"line {n}" for n in range(lines))
        head, more = review.head_lines(body, limit)
        assert inbox._unset_tail(("k", "body"), body) == more, lines
        assert inbox._head(body) == head, lines
        assert detail.bounded(body, False) == (head, more), lines
        assert detail.lines_past_bound([body])[0] == more, lines


def test_a_body_past_the_bound_is_set_to_the_bound_and_holds_its_approve(
    seam: Any,
    said: list[str],
    setting_all: list[str],
    clicking_approve: list[bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The frame the cursor reaches the row reads and lays out no more than DETAIL
    would unasked. What is past that is not on screen, and the row says so three
    ways: the count, the button that sets it, and an approve that sends nothing.
    """
    call = past_the_bound()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    pane.draw(seam)

    handed = shown(call, "body").replace(" ", "")
    last = review.SETTLED_LINES // 2 - 1
    assert f"Paragraph{last}asks" in handed and f"Paragraph{last + 1}asks" not in handed
    assert inbox._VIEWS.unbuilt(key(call, "body")) == 0
    assert review.unset_lines(pane.state, call.id) == 201
    assert [line for line in said if "201 more lines not set: all 2,201" in line]
    assert setting_all == ["set all 2,201 lines"]
    assert clicking_approve == [True] and sent == []
    assert inbox._NOT_ON_SCREEN in said

    press(pane, seam, seam.Key.a)
    assert sent == [] and pane.state.refusal is not None
    assert pane.state.refusal.text == review.NOT_WHOLE


def test_a_body_past_the_bound_is_whole_and_approvable_once_asked_for(
    seam: Any,
    said: list[str],
    setting_all: list[str],
    clicking_approve: list[bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = past_the_bound()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    pane.draw(seam)
    assert sent == []

    # The click lands under the body it asks for: that frame is still the head.
    setting_all.append("click")
    pane.draw(seam)
    setting_all.clear()
    assert sent == []

    said.clear()
    pane.draw(seam)
    handed = shown(call, "body").replace(" ", "")
    assert f"Paragraph{review.SETTLED_LINES // 2 + 99}asks" in handed
    assert review.unset_lines(pane.state, call.id) == 0
    assert setting_all == [] and not [line for line in said if "more lines not set" in line]
    assert clicking_approve[-1] is False and sent == [call.id]


def test_a_body_at_the_bound_is_whole_and_offers_nothing(
    seam: Any, said: list[str], setting_all: list[str]
) -> None:
    body = "\n\n".join(f"Paragraph {n}." for n in range(review.SETTLED_LINES // 2)) + "\nEnd."
    assert body.count("\n") + 1 == review.SETTLED_LINES
    call = parked(inbox._POST_CONCERN, {"to": "lead", "body": body})
    pane = Pane(call)
    pane.draw(seam)

    assert "End." in shown(call, "body")
    assert review.unset_lines(pane.state, call.id) == 0
    assert setting_all == [] and not [line for line in said if "more lines not set" in line]


def test_a_body_asked_for_whole_is_forgotten_with_its_call(
    seam: Any, setting_all: list[str]
) -> None:
    call = past_the_bound()
    pane = Pane(call)
    setting_all.append("click")
    pane.draw(seam)
    assert inbox._WHOLE == {key(call, "body")}

    pane.store.apply(ApprovalResolved(ROOT, call.id, approved=True))
    pane.draw(seam)
    assert inbox._WHOLE == set()


def test_with_typeset_off_a_body_past_the_bound_is_all_drawn_and_approvable(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The bound is on what the library reads and lays out in one frame. ImGui's own
    text has no such frame, and the native block scrolls to all of it.
    """
    typeset.configure(typeset=False)
    call = past_the_bound()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    pane.draw(imgui)
    assert review.unset_lines(pane.state, call.id) == 0

    press(pane, imgui, imgui.Key.a)
    assert sent == [call.id]


def test_detail_holds_back_the_same_end_of_a_body_and_says_how_much() -> None:
    """
    Why the row sets the rest itself and does not point at DETAIL: DETAIL holds
    its own view of the call to the same bound, and is asked separately.
    """
    body = str(past_the_bound().raw_args["body"])
    parts = detail.argument_parts(inbox._POST_CONCERN, {"body": body}, 80)
    shown = "".join(p.text for p in parts)
    assert f"Paragraph {review.SETTLED_LINES // 2 - 1} " in shown
    assert f"Paragraph {review.SETTLED_LINES // 2} " not in shown
    assert parts[-1].text == f"… {inbox._unset_tail(('k', 'body'), body):,} more lines not set"


# -- no approve path sends a call whose body is not all on screen ------------------
#
# The frames here are the application's: ``review.handle_keys`` first, then the
# pane, so a key is answered from what the pane showed up to the frame before.


def long_concern(ident: str = "p1") -> PendingApproval:
    """
    A concern of 1,200 lines: long enough that a seam building over frames would
    show part of it first.
    """
    body = "".join(f"Paragraph {n} asks for something else to be done.\n\n" for n in range(600))
    args = {"to": "lead", "subject": "s", "body": body}
    return parked("mcp__pptmstr__post_concern", args, ident)


def short_concern(ident: str) -> PendingApproval:
    return parked("mcp__pptmstr__post_concern", dict(fake_driver._POST_CONCERN[1]), ident)


def press(pane: Pane, im: Any, *keys: Any) -> None:
    """
    One frame with ``keys`` going down in it -- letters and modifiers alike -- read
    by ``review.handle_keys`` before the pane draws, and a frame to let go in.

    Asserts each key arrived and nothing had the keyboard: without that a test that
    expects nothing to be sent passes because no key was read at all.
    """
    io = im.get_io()
    for key in keys:
        io.add_key_event(key, True)

    def body() -> None:
        assert all(im.is_key_down(key) for key in keys)
        assert not io.want_capture_keyboard
        review.handle_keys(pane.snap, pane.focus, pane.state, pane.bridge)
        inbox.draw(pane.snap, pane.focus, pane.state, pane.compose, pane.bridge, ACTIONS, 1.0)

    rig.frame(im, body)
    for key in keys:
        io.add_key_event(key, False)
    rig.frame(im, lambda: None)


def until_whole(pane: Pane, im: Any, call: PendingApproval) -> None:
    """
    The frame after the library stopped: the row's own renderer, all of the body.
    """
    pane.draw(im)
    assert not typeset.enabled(typeset.Pane.INBOX)
    assert review.unset_lines(pane.state, call.id) == 0


def short_on_its_first_frame(
    pane: Pane, im: Any, call: PendingApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Draw one frame in which the library stops under the body, and insist the
    hazard is there: the row's reading is of text that is not on screen.
    """
    library_stops(monkeypatch)
    pane.draw(im)
    left = inbox._VIEWS.unbuilt(key(call, "body"))
    assert left is not None and left > 0, "the body was on screen on its first frame"
    assert review.unset_lines(pane.state, call.id) == left


@pytest.fixture
def clicking(monkeypatch: pytest.MonkeyPatch, seam: Any) -> list[str]:
    """
    Every button whose label starts with one of the strings in the list reports a
    click on every frame it is drawn.
    """
    labels: list[str] = []
    real = seam.button

    def button(label: str, *rest: Any) -> bool:
        pressed = bool(real(label, *rest))
        return pressed or any(label.startswith(prefix) for prefix in labels)

    monkeypatch.setattr(seam, "button", button)
    return labels


def test_a_over_a_body_short_of_its_text_sends_nothing_and_says_why(
    seam: Any, said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    call = long_concern()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    short_on_its_first_frame(pane, seam, call, monkeypatch)

    said.clear()
    press(pane, seam, seam.Key.a)

    assert sent == []
    assert review.NOT_WHOLE in said

    until_whole(pane, seam, call)
    press(pane, seam, seam.Key.a)
    assert sent == [call.id]
    assert pane.state.refusal is None


def test_a_on_the_frame_the_cursor_lands_on_a_row_never_drawn_sends_nothing(
    seam: Any, said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``j`` and ``a`` in one frame: the cursor is on the second call when ``a`` is
    read, and no pane has drawn that call's body, short or whole.
    """
    first, second = short_concern("p1"), short_concern("p2")
    pane = Pane(first, second)
    sent = _sent(pane, monkeypatch)
    pane.draw(seam)

    said.clear()
    press(pane, seam, seam.Key.j, seam.Key.a)

    assert pane.focus.index(pane.snap) == 1
    assert sent == []
    assert review.NOT_DRAWN in said

    # The frame above drew it, whole; the same key now approves it.
    press(pane, seam, seam.Key.a)
    assert sent == ["p2"]


def test_a_before_any_pane_has_drawn_sends_nothing_under_either_renderer(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    for on in (True, False):
        typeset.configure(typeset=on)
        inbox._VIEWS.clear()
        pane = Pane(short_concern("p1"))
        sent = _sent(pane, monkeypatch)

        press(pane, seam, seam.Key.a)
        assert sent == [], on
        press(pane, seam, seam.Key.a)
        assert sent == ["p1"], on


@pytest.mark.parametrize("call", [long_concern(), write(400)], ids=["concern", "write"])
def test_with_typeset_off_a_approves_a_drawn_row_whatever_its_length(
    seam: Any, monkeypatch: pytest.MonkeyPatch, call: PendingApproval
) -> None:
    """
    The native body is whole on the frame it is first drawn, so one drawn frame is
    all ``a`` waits for.
    """
    typeset.configure(typeset=False)
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    pane.draw(seam)

    press(pane, seam, seam.Key.a)

    assert sent == [call.id]
    assert len(inbox._VIEWS) == 0


def test_reject_and_edit_are_never_held_by_a_short_body(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = long_concern()
    pane = Pane(call)
    decided: list[tuple[str, bool]] = []
    monkeypatch.setattr(pane.bridge, "resolve", lambda i, d: decided.append((i, d.approved)))
    short_on_its_first_frame(pane, seam, call, monkeypatch)
    assert review.withheld(pane.state, call) == review.NOT_WHOLE

    # The keys alone, with no pane drawn after them: the reading is still of a
    # body that was not on screen when the rejection is sent.
    io = seam.get_io()
    for letter in (seam.Key.r, seam.Key.e):
        io.add_key_event(letter, True)
        seam.new_frame()
        assert seam.is_key_pressed(letter) and not io.want_capture_keyboard
        review.handle_keys(pane.snap, pane.focus, pane.state, pane.bridge)
        seam.render()
        io.add_key_event(letter, False)
        seam.new_frame()
        seam.render()
    assert pane.state.focus_reason
    assert pane.state.editing == call.id and call.id in pane.state.edits

    assert review.withheld(pane.state, call) == review.NOT_WHOLE
    assert review.resolve(pane.bridge, pane.state, call, approved=False)
    assert decided == [(call.id, False)]

    # And a call never drawn at all can be refused too.
    other = Pane(short_concern("p9"))
    monkeypatch.setattr(other.bridge, "resolve", lambda i, d: decided.append((i, d.approved)))
    assert review.resolve(other.bridge, other.state, other.snap.approvals[0], approved=False)
    assert decided[-1] == ("p9", False)


def test_an_open_editor_is_what_is_approved_and_does_not_wait_for_a_body(
    seam: Any, clicking_approve: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``e`` on a row nobody has drawn yet: the row then shows the draft in ImGui's
    own input, all of it, and approve sends that draft.
    """
    call = long_concern()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    pane.state.editing = call.id
    pane.state.edits[call.id] = review.pretty_args(call)

    pane.draw(seam)

    assert clicking_approve == [False] and sent == [call.id]


def test_a_cancelled_edit_takes_the_editors_reading_with_it(
    seam: Any, clicking: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    While the editor is open the row shows the draft and no body. Cancelling drops
    the draft, so ``a`` on the next frame would send the agent's call -- of which
    this row has drawn nothing yet.
    """
    call = long_concern()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    pane.state.editing = call.id
    pane.state.edits[call.id] = review.pretty_args(call)
    pane.draw(seam)
    assert review.withheld(pane.state, call) is None

    clicking.append("cancel edit")
    pane.draw(seam)
    del clicking[:]
    assert pane.state.editing is None and call.id not in pane.state.edits

    press(pane, seam, seam.Key.a)
    assert sent == []
    assert pane.state.refusal is not None and pane.state.refusal.text == review.NOT_DRAWN


def test_shift_a_with_one_short_body_in_the_agents_queue_sends_none_of_it(
    seam: Any, said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    long, brief = long_concern("p1"), short_concern("p2")
    pane = Pane(long, brief)
    sent = _sent(pane, monkeypatch)
    short_on_its_first_frame(pane, seam, long, monkeypatch)

    said.clear()
    press(pane, seam, seam.Key.mod_shift, seam.Key.a)
    assert sent == []
    refused = [line for line in said if line.startswith("none sent - 1 of these 2 calls")]
    assert len(refused) == 1 and long.summary in refused[0]

    # That frame drew the row with its own renderer, all of it.
    assert review.unset_lines(pane.state, long.id) == 0
    press(pane, seam, seam.Key.mod_shift, seam.Key.a)
    assert sent == ["p1", "p2"]


@pytest.mark.parametrize("label", ["approve all 2 from", "yes, approve all"], ids=["node", "all"])
def test_an_approve_all_button_with_one_short_body_in_its_batch_sends_none_of_it(
    seam: Any, said: list[str], clicking: list[str], monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    long, brief = long_concern("p1"), short_concern("p2")
    pane = Pane(long, brief)
    sent = _sent(pane, monkeypatch)

    # The button is drawn after the row in the same frame, so the row's reading of
    # this frame is the one it is answered from: clicked in the frame the library
    # stopped under the body.
    library_stops(monkeypatch)
    pane.state.confirming_global = True
    clicking.append(label)
    pane.draw(seam)
    del clicking[:]
    assert inbox._VIEWS.unbuilt(key(long, "body"))
    assert sent == []
    assert not pane.state.confirming_global or label != "yes, approve all"

    said.clear()
    pane.draw(seam)
    assert [line for line in said if line.startswith("none sent - 1 of these 2 calls")]

    assert review.unset_lines(pane.state, long.id) == 0
    pane.state.confirming_global = True
    clicking.append(label)
    pane.draw(seam)
    assert sent == ["p1", "p2"]


def test_a_batch_still_sends_the_calls_nobody_opened(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The batch forms approve rows whose bodies were never on screen: that is what
    they are for, and what the confirm is for. Only a body known short holds one.
    """
    calls = [short_concern("p1"), long_concern("p2"), write(300, "p3")]
    pane = Pane(*calls)
    sent = _sent(pane, monkeypatch)
    pane.draw(seam)
    assert review.unset_lines(pane.state, "p1") == 0
    assert review.unset_lines(pane.state, "p2") is None

    press(pane, seam, seam.Key.mod_shift, seam.Key.a)
    assert sent == ["p1", "p2", "p3"]

    del sent[:]
    assert review.approve_everything(pane.bridge, pane.snap, pane.state) == 3
    assert sent == ["p1", "p2", "p3"]


def test_a_batch_refused_with_no_approval_row_open_is_said_by_the_buttons(
    seam: Any, said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With the cursor off the queue no row is open to carry the line, and the batch
    buttons are still there to be pressed.
    """
    long = long_concern("p1")
    pane = Pane(long)
    sent = _sent(pane, monkeypatch)
    short_on_its_first_frame(pane, seam, long, monkeypatch)
    pane.focus = FocusState()
    assert review.approve_everything(pane.bridge, pane.snap, pane.state) == 0
    assert sent == []

    said.clear()
    pane.draw(seam)

    assert len([line for line in said if line.startswith("none sent - 1 of these 1 calls")]) == 1


# -- the question row's preview ------------------------------------------------

REPORT = fake_driver._REPORT_OPENING + fake_driver._REPORT_BODY
FENCE = "```python\n" + "".join(f"line_{n} = {n}\n\n" for n in range(30)) + "```"


def asked(prose: str) -> Pane:
    """
    One session that said ``prose`` and ended its turn, the cursor on its question.
    """
    pane = Pane()
    pane.snap.nodes[ROOT].transcript.append(SegmentKind.OUTPUT, prose)
    pane.store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT))
    (question,) = pane.snap.needs_you
    assert isinstance(question, QuestionPending)
    pane.focus.to_obligation(question)
    return pane


def preview(pane: Pane) -> Any:
    (question,) = pane.snap.needs_you
    return inbox._VIEWS.view((question.key, "preview"))


def test_a_turn_that_fits_is_previewed_whole() -> None:
    prose = "Right, here is the plan.\n\n- one\n- two\n\nFix it, or only report it?\n"
    assert inbox.preview_tail(prose) == (prose.rstrip(), 0)
    assert inbox.preview_tail("  \n\n") == ("", 0)


@pytest.mark.parametrize("lines", [1, 3, 6, 10, 40])
def test_a_preview_is_the_end_of_the_turn_from_the_start_of_a_block(lines: int) -> None:
    """
    Whatever the budget: what is taken is the turn's own text to its last line,
    what is counted as left out is every line ahead of it, and the line ahead of
    the cut is blank -- it is not the middle of a paragraph, a list or a table.
    """
    source = REPORT.rstrip().split("\n")
    tail, dropped = inbox.preview_tail(REPORT, lines=lines)
    assert tail.split("\n") == source[dropped:]
    assert 0 < dropped < len(source)
    assert source[dropped - 1] == ""
    assert tail.endswith("should that stay with its caller?")


def test_a_longer_budget_takes_no_less_of_the_turn() -> None:
    kept = [inbox.preview_tail(REPORT, lines=n)[1] for n in (1, 3, 6, 10, 40, 400)]
    assert kept == sorted(kept, reverse=True)
    assert kept[-1] == 0


def test_a_fence_is_taken_whole_or_not_at_all() -> None:
    """
    A cut inside a fence turns its second half into prose and the prose after it
    into code. The fence here has blank lines in it, which are not block ends.
    """
    before = "Some words first.\n\n"
    # The last block, however long: all of it.
    assert inbox.preview_tail(before + FENCE, lines=6) == (FENCE, 2)
    # Not the last and too long for what is left: none of it.
    ending = FENCE + "\n\nWhich of these should go?"
    assert inbox.preview_tail(before + ending, lines=6) == ("Which of these should go?", 65)
    # And with room, from its opening line.
    assert inbox.preview_tail(before + ending, lines=80) == (before + ending, 0)


def test_a_fence_nobody_closed_runs_to_the_end_of_the_turn() -> None:
    prose = "Words.\n\n```\na\n\nb\n\nc"
    assert inbox.preview_tail(prose, lines=1) == ("```\na\n\nb\n\nc", 2)


def test_a_preview_never_begins_on_what_belongs_to_the_block_above_it() -> None:
    """
    An item's second paragraph, set without its item, is an indented code block.
    The cut moves up to the item.
    """
    prose = (
        "A paragraph that the budget leaves out.\n\n"
        "- the first item\n\n"
        "- the second item\n\n"
        "  its second paragraph\n\n"
        "      and a snippet under it"
    )
    tail, dropped = inbox.preview_tail(prose, lines=1)
    assert tail.startswith("- the second item")
    assert dropped == 4


def test_a_question_row_sets_the_end_of_the_turn_as_markdown(seam: Any, said: list[str]) -> None:
    """
    The fixture's report ends on a rule, a fence and a paragraph with emphasis and
    code spans in it. Read as markdown none of the marks is on screen; as the
    string, all of them are.
    """
    pane = asked(REPORT)
    pane.draw(seam)

    view = preview(pane)
    assert view is not None
    text = rig._shown(view)
    assert "One question before I go further" in text
    assert "should tle/ingest.py call verify itself" in text
    assert "**" not in text and "`" not in text
    _, dropped = inbox.preview_tail(REPORT)
    assert f"… {dropped:,} earlier lines of this turn - DETAIL has all of it" in said
    # No box of ImGui's own text beside it.
    assert not any("##body" in w.name and w.active for w in seam.get_current_context().windows)


def test_a_preview_reads_the_turn_as_the_panes_showing_all_of_it_do(seam: Any) -> None:
    """
    DETAIL sets the same turn beside this row, as markdown. Three lines a writer
    ended early are one paragraph there, and a body's rule would keep their breaks
    here: one text, two readings, in one frame.
    """
    prose = "Right, here is the plan.\nIt is computed but never compared.\nFix it, or report it?"
    pane = asked(prose)
    cache = typeset.ViewCache()
    pane.draw(seam, lambda: typeset.markdown(cache, "whole", prose))

    assert rig._shown(preview(pane)) == rig._shown(cache.view("whole"))
    assert rig._shown(preview(pane)) == prose.replace("\n", " ")


def test_a_previewed_table_is_a_table(seam: Any) -> None:
    table = "| Input | Result |\n|---|---|\n| ISS | pass |\n| NOAA 19 | **pass** |"
    pane = asked("Checked:\n\n" + table)
    pane.draw(seam)

    text = rig._shown(preview(pane))
    assert "NOAA 19" in text
    assert "|" not in text and "**" not in text


def test_a_turn_previewed_whole_says_nothing_is_left_out(seam: Any, said: list[str]) -> None:
    pane = asked("Fix it, or *only* report it?")
    pane.draw(seam)

    assert rig._shown(preview(pane)) == "Fix it, or only report it?"
    assert not [line for line in said if "earlier lines" in line]


def test_a_preview_is_as_tall_as_its_text_and_no_taller_than_its_cap(seam: Any) -> None:
    """
    The native box is 110 px whatever is in it. A one-line ask costs the queue one
    line, and a turn ending on a sixty-line fence costs it the cap.
    """
    line = typeset.line_height(role=typeset.Role.PROSE)
    (question,) = (short := asked("Fix it, or only report it?")).snap.needs_you
    short.draw(seam)
    one_line = inbox._VIEWS.height((question.key, "preview"))
    assert one_line is not None and one_line <= 2 * line

    inbox._VIEWS.clear()
    inbox._PREVIEWS.clear()
    long = asked("Words.\n\n" + FENCE)
    # Twice: a window knows how tall its content is from the frame before.
    long.draw(seam)
    long.draw(seam)
    whole = typeset_child(seam).content_size.y
    assert whole > 50 * line
    # The line saying what is left out is the row's own, over the cap.
    said = seam.get_text_line_height_with_spacing()
    assert short.height < long.height <= short.height + inbox._PREVIEW_LINES * line + said


def test_a_preview_is_cut_when_the_turn_grows_and_not_once_a_frame(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = asked("First answer.")
    reads: list[int] = []
    real = Transcript.turn_prose

    def turn_prose(self: Transcript) -> str:
        reads.append(1)
        return real(self)

    monkeypatch.setattr(Transcript, "turn_prose", turn_prose)
    pane.draw(seam)
    pane.draw(seam)
    assert len(reads) == 1
    assert rig._shown(preview(pane)) == "First answer."

    pane.snap.nodes[ROOT].transcript.append(SegmentKind.OUTPUT, "\n\nAnd a second.")
    pane.draw(seam)
    assert len(reads) == 2
    assert rig._shown(preview(pane)) == "First answer.\n\nAnd a second."


def test_a_turn_that_said_nothing_says_so_and_holds_no_view(seam: Any, said: list[str]) -> None:
    pane = asked("")
    pane.draw(seam)

    assert "no output on this turn" in said
    assert preview(pane) is None


def test_a_question_is_not_a_body_any_approve_waits_on(seam: Any) -> None:
    pane = asked(REPORT)
    pane.draw(seam)

    assert pane.state.bodies == {}


@pytest.mark.parametrize("switch", ["master", "pane"])
def test_with_typeset_off_a_question_is_previewed_in_the_native_box(
    seam: Any, said: list[str], switch: str
) -> None:
    if switch == "master":
        typeset.configure(typeset=False, mono=False)
    else:
        typeset.configure(typeset=True, mono=False, panes={typeset.Pane.INBOX: False})
    pane = asked(REPORT)
    pane.draw(seam)

    assert len(inbox._VIEWS) == 0 and not inbox._PREVIEWS
    assert REPORT[-inbox._PREVIEW_CHARS :] in said
    assert native_body(seam).size.y == 110.0


def test_a_preview_is_dropped_when_its_question_is_answered(seam: Any) -> None:
    pane = asked(REPORT)
    pane.draw(seam)
    assert len(inbox._VIEWS) == 1 and len(inbox._PREVIEWS) == 1

    pane.store.apply(StateChanged(ROOT, AgentState.THINKING))
    pane.draw(seam)

    assert len(inbox._VIEWS) == 0 and not inbox._PREVIEWS


# -- the other calls whose argument is prose -----------------------------------

PROSE_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("ExitPlanMode", {"plan": CONCERN_BODY, "planFilePath": "/home/x/.claude/plans/a.md"}),
    ("SendMessage", {"to": "qa", "summary": "t2 as specified", "message": CONCERN_BODY}),
    ("TaskCreate", {"subject": "hold t2", "description": CONCERN_BODY, "activeForm": "Holding"}),
    ("TaskUpdate", {"taskId": "7", "status": "in_progress", "description": CONCERN_BODY}),
    ("WebFetch", {"url": "https://example.invalid/tle-fmt.php", "prompt": CONCERN_BODY}),
]


def test_every_described_call_names_a_prose_argument_and_none_that_is_acted_on() -> None:
    """
    The table is the whole of what either pane reads as prose. The names on the
    right are arguments a tool acts on character for character.
    """
    never = {"command", "content", "old_string", "new_string", "pattern", "file_path", "url"}
    assert {tool for tool, _ in PROSE_CALLS} | {
        inbox._POST_CONCERN,
        inbox._DECLARE_TASK,
        "Agent",
        "Task",
    } == inbox.DESCRIBED.keys()
    assert not never & {prose for prose, _ in inbox.DESCRIBED.values()}
    assert not {"query", "edits", "new_source"} & {p for p, _ in inbox.DESCRIBED.values()}
    assert inbox.prose_argument("Bash") is None and inbox.prose_argument("WebSearch") is None
    for tool, (prose, _) in inbox.DESCRIBED.items():
        assert inbox.prose_argument(tool) == prose


@pytest.mark.parametrize(("tool", "args"), PROSE_CALLS, ids=[tool for tool, _ in PROSE_CALLS])
def test_a_call_whose_argument_is_prose_is_set_word_for_word_and_its_fields_over_it(
    seam: Any, said: list[str], tool: str, args: dict[str, Any]
) -> None:
    """
    The body that hides nothing of a concern hides nothing of a plan: every word of
    the fixture's text is set, the tags a markdown reader would drop included, and
    every other argument is a line of the row's own over it.
    """
    call = parked(tool, args)
    assert call.diff is None
    pane = Pane(call)
    pane.draw(seam)

    assert key(call, "arguments") not in inbox._VIEWS
    set_ = " ".join(shown(call, "body").split())
    for needle in ("Optional<int>", "ask <lead> for", "<!-- generated", "<details>"):
        assert needle in set_

    def words(text: str) -> list[str]:
        plain = text.replace("`", "").replace("*", "")
        return [word for word in plain.split() if word not in ("-", "\u2022")]

    assert words(set_) == words(CONCERN_BODY)
    prose = inbox.prose_argument(tool)
    for name, value in args.items():
        if name != prose:
            assert value in said
    assert inbox._NO_DIFF not in said


@pytest.mark.parametrize(("tool", "args"), PROSE_CALLS, ids=[tool for tool, _ in PROSE_CALLS])
def test_a_long_prose_argument_of_any_described_call_holds_its_approve(
    seam: Any,
    said: list[str],
    clicking_approve: list[bool],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    args: dict[str, Any],
) -> None:
    """
    Whatever is read as prose is the library's to set, so it is what an approve
    could be offered over if the library stopped under it. It is not.
    """
    prose = inbox.prose_argument(tool)
    assert prose is not None
    body = "".join(f"Paragraph {n} asks for something else to be done.\n\n" for n in range(600))
    call = parked(tool, {**args, prose: body})
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    library_stops(monkeypatch)

    pane.draw(seam)

    left = inbox._VIEWS.unbuilt(key(call, "body"))
    assert left is not None and left > 0
    assert pane.state.bodies[call.id]["inbox"][1] == left
    assert review.withheld(pane.state, call) == review.NOT_WHOLE
    assert clicking_approve == [True] and sent == []
    assert inbox._NOT_ON_SCREEN in said
    until_whole(pane, seam, call)
    assert review.withheld(pane.state, call) is None and sent == [call.id]


def test_a_message_that_is_an_object_is_a_field_and_not_a_body(seam: Any, said: list[str]) -> None:
    message = {"type": "shutdown_request", "reason": "*done*"}
    call = parked("SendMessage", {"to": "qa", "message": message})
    pane = Pane(call)
    pane.draw(seam)

    assert key(call, "body") not in inbox._VIEWS
    assert repr(message) in said


def test_questions_put_to_the_user_are_set_as_the_structure_they_are(seam: Any) -> None:
    """
    ``AskUserQuestion`` carries a list of questions with options and no one argument
    that is its prose. Every character of it is in the arguments view, as written.
    """
    questions = [
        {
            "question": "Which *one*?",
            "header": "Pick",
            "options": [{"label": "a_b", "description": "the `first`"}],
        }
    ]
    call = parked("AskUserQuestion", {"questions": questions})
    pane = Pane(call)
    pane.draw(seam)

    assert key(call, "body") not in inbox._VIEWS
    assert repr(questions) in shown(call, "arguments").replace("\n", "")


# -- a body or a diff the library set less of than it was handed, or raised under ------

WIDE_TABLE = "|" + "c|" * 1100 + "\n|" + "-|" * 1100 + "\n|" + "v|" * 1100 + "\n"


def test_a_concern_whose_table_the_library_cut_waits_a_frame_and_is_then_all_on_screen(
    seam: Any, said: list[str], clicking_approve: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The branch the product takes today. A concern whose body holds a table of
    1,100 columns: the library sets 1,024 cells of a row, and its draw says it
    set everything. In the frame it is first drawn the row says the body is
    not on screen and a click on approve sends nothing; the next frame the
    body is ImGui's, as written, every cell in the text handed over, and the
    click lands. Typeset text stays on for everything else.
    """
    body = "The matrix follows.\n\n" + WIDE_TABLE
    call = parked("mcp__pptmstr__post_concern", {"to": "lead", "subject": "s", "body": body})
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    drawn: list[str] = []
    real = seam.text_unformatted
    monkeypatch.setattr(seam, "text_unformatted", lambda text: (drawn.append(text), real(text))[1])

    pane.draw(seam)
    assert inbox._VIEWS.unbuilt(key(call, "body")) == body.count("\n") + 1
    assert review.withheld(pane.state, call) == review.NOT_WHOLE
    assert clicking_approve == [True] and sent == []
    assert inbox._NOT_ON_SCREEN in said

    said.clear()
    drawn.clear()
    pane.draw(seam)
    assert typeset.enabled(typeset.Pane.INBOX)
    assert inbox._VIEWS.unbuilt(key(call, "body")) == 0
    assert body in drawn, "as written, every cell"
    assert [line for line in drawn if line.endswith("shown as written")]
    assert clicking_approve[-1] is False and sent == [call.id]
    assert inbox._NOT_ON_SCREEN not in said


def _raises_attribute_error(*given: Any, **named: Any) -> Any:
    raise AttributeError("'X' object has no attribute 'renamed'")


def test_a_body_the_library_raises_under_does_not_take_the_pane_and_cannot_be_approved(
    seam: Any, said: list[str], clicking_approve: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Today's branch again: the view of a concern's body raises something that is
    not the library's own stop, on every frame. The pane draws -- nothing
    leaves ``inbox.draw`` -- the row says the body is not on screen and holds
    the click; the next frame is the row's own renderer and the click lands.
    """
    call = long_concern()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    monkeypatch.setattr(rig.advtext.TypesetView, "draw", _raises_attribute_error)

    pane.draw(seam)
    assert inbox._VIEWS.unbuilt(key(call, "body")) == call.raw_args["body"].count("\n") + 1
    assert clicking_approve == [True] and sent == []
    assert inbox._NOT_ON_SCREEN in said

    said.clear()
    pane.draw(seam)
    assert not typeset.enabled(typeset.Pane.INBOX)
    assert "AttributeError" in (typeset.unavailable_reason() or "")
    assert clicking_approve[-1] is False and sent == [call.id]
    monkeypatch.undo()
    assert typeset.load_fonts(rig.BOLD)


def _diff_draw_raises(patched: pytest.MonkeyPatch) -> None:
    patched.setattr(rig.advtext.TypesetDiffView, "draw", _raises_attribute_error)


def _diff_attribute_raises(patched: pytest.MonkeyPatch) -> None:
    patched.setattr(rig.advtext.FileDiff, "invisible_characters", property(_raises_attribute_error))


def _diff_model_raises(patched: pytest.MonkeyPatch) -> None:
    patched.setattr(rig.advtext.Diff, "from_texts", _raises_attribute_error)


@pytest.mark.parametrize(
    "fault",
    [_diff_draw_raises, _diff_attribute_raises, _diff_model_raises],
    ids=["draw", "an attribute read above the view", "making the model"],
)
def test_where_columns_hold_a_diff_the_library_raises_under_does_not_take_the_pane(
    columns_hold: Any, said: list[str], monkeypatch: pytest.MonkeyPatch, fault: Any
) -> None:
    """
    The adopted branch. Anything the diff view or its model raises that is not
    the library's own stop used to leave ``typeset.diff`` and with it the pane,
    on every frame the approval was queued. It is the library stopping: the
    row shows the sentence, holds the click, and is its own rows from the next
    frame, where the click lands.
    """
    im = columns_hold
    call = write(5)
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    clicks = request_clicks(im, monkeypatch)
    fault(monkeypatch)

    pane.draw(im)
    assert (inbox._VIEWS.unbuilt(key(call, "diff")) or 0) > 0
    assert review.withheld(pane.state, call) == review.NOT_WHOLE
    assert clicks == [True] and sent == []
    assert inbox._NOT_ON_SCREEN in said
    assert not [line for line in said if line.startswith("+line_")]

    said.clear()
    pane.draw(im)
    assert not typeset.enabled(typeset.Pane.INBOX)
    assert [line for line in said if line.startswith("+line_")], "the row's own rows"
    assert clicks[-1] is False and sent == [call.id]
    monkeypatch.undo()
    assert typeset.load_fonts(rig.BOLD)


def _diff_draw_answers_false(patched: pytest.MonkeyPatch) -> None:
    real = rig.advtext.TypesetDiffView.draw
    patched.setattr(
        rig.advtext.TypesetDiffView,
        "draw",
        lambda self, **given: bool(real(self, **given)) and False,
    )


def _diff_edit_is_cut(patched: pytest.MonkeyPatch) -> None:
    real = rig.advtext.TypesetDiffView.draw

    def cut(self: Any, **given: Any) -> bool:
        rig.advtext.typesetter.note("a diff edit was cut: block 2: the text is longer than 16 MiB")
        return bool(real(self, **given))

    patched.setattr(rig.advtext.TypesetDiffView, "draw", cut)


@pytest.mark.parametrize(
    "short",
    [_diff_draw_answers_false, _diff_edit_is_cut],
    ids=["draw answers false", "an edit was cut and draw answers true"],
)
def test_where_columns_hold_a_diff_the_view_set_less_of_than_it_was_handed_holds_the_click(
    columns_hold: Any, said: list[str], monkeypatch: pytest.MonkeyPatch, short: Any
) -> None:
    """
    The diff view says it set less than it was handed in two ways, and neither
    is an exception: its draw answers false, or the library notes an edit it
    cut while the draw answers true. Each is made here by its signal, since a
    real one needs a row of 16 MiB. In that frame every changed row is counted
    as not on screen: the row says so and a click on approve sends nothing.
    The next frame the diff is the row's own rows, every line of it, and the
    click lands. The library is not stopped for one diff.
    """
    im = columns_hold
    call = write(5)
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    clicks = request_clicks(im, monkeypatch)

    with monkeypatch.context() as patched:
        short(patched)
        pane.draw(im)
    assert inbox._VIEWS.unbuilt(key(call, "diff")) == 5
    assert review.withheld(pane.state, call) == review.NOT_WHOLE
    assert clicks == [True] and sent == []
    assert inbox._NOT_ON_SCREEN in said
    assert not [line for line in said if line.startswith("+line_")]

    said.clear()
    pane.draw(im)
    assert typeset.enabled(typeset.Pane.INBOX) and typeset.diff_adopted()
    assert diff_view(call) is None and not no_native_body(im)
    assert len([line for line in said if line.startswith("+line_")]) == 5, "the row's own rows"
    assert review.unset_lines(pane.state, call.id) == 0
    assert clicks[-1] is False and sent == [call.id]
    assert inbox._NOT_ON_SCREEN not in said


def test_where_columns_hold_a_diff_with_a_character_the_code_face_lacks_is_the_rows_own(
    columns_hold: Any, said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The measurement answers for ASCII in the code face. A Write with a Greek
    letter in it is drawn by the row's own child, under one line saying why,
    with the menu's sentence about the library unchanged; every row is on
    screen, so the click lands on the first frame it can.
    """
    im = columns_hold
    call = parked("Write", {"file_path": "no/such/dir/area.py", "content": "area = π * r\n"})
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    clicks = request_clicks(im, monkeypatch)
    drawn: list[str] = []
    real = im.text_unformatted
    monkeypatch.setattr(im, "text_unformatted", lambda text: (drawn.append(text), real(text))[1])

    pane.draw(im)
    assert typeset.diff_adopted() and typeset.diff_renderer().startswith("diffs are the library's")
    assert diff_view(call) is None and not no_native_body(im)
    assert "+area = π * r" in said
    (why,) = [line for line in drawn if line.startswith("this diff is the pane's own rows")]
    assert "U+03C0" in why
    assert review.unset_lines(pane.state, call.id) == 0
    assert clicks == [False] and sent == [call.id]


def test_where_columns_hold_a_diff_past_the_bound_is_the_rows_own_until_the_view_is_asked_for(
    columns_hold: Any, said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The library's model of a diff is ``difflib`` over the lines the call
    changes, in the frame that first asks for it: a second and a half for
    changes scattered through 20,000 lines. Past ``review.SETTLED_LINES`` of
    them the row does not ask: it draws its own rows, which are the whole
    diff already, says why in one line and offers the view. Nothing is held
    on it. Asked, the view is made and is every row; the answer is the
    call's, so DETAIL asks nothing more.
    """
    im = columns_hold
    lines = review.SETTLED_LINES + 100
    call = write(lines)
    pane = Pane(call)
    built: list[object] = []
    real = typeset.diffs.diff_model
    monkeypatch.setattr(
        typeset.diffs,
        "diff_model",
        lambda texts, **how: (built.append(texts), real(texts, **how))[1],
    )
    asked: list[str] = []
    button = im.small_button
    monkeypatch.setattr(im, "small_button", lambda label: bool(button(label)) or label in asked)

    pane.draw(im)
    notice = (
        "this diff is the pane's own rows: the library's view of it would match "
        f"{lines:,} changed lines in one frame"
    )
    assert notice in said
    assert diff_view(call) is None and not no_native_body(im) and built == []
    assert review.unset_lines(pane.state, call.id) == 0

    asked.append("use the library's view")
    pane.draw(im)
    said.clear()
    pane.draw(im)
    assert call.id in pane.state.diff_whole and notice not in said
    view = diff_view(call)
    assert view is not None and len(added(view)) == lines and len(built) == 1
    assert inbox.library_diff(pane.state, call) is pane.state.diffs[call.id]

    under = write(review.SETTLED_LINES, "p2")
    assert inbox.library_diff(pane.state, under) is not None, "at the bound it is the view"


def test_where_columns_hold_the_row_and_detail_draw_one_call_from_one_model(
    columns_hold: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    DETAIL follows this pane's cursor, so the two draw one parked call in every
    frame. Each used to make the library's model of it for itself. Both are
    handed what ``library_diff`` keeps for the call: one model, made once, in
    two views, and forgotten when the call leaves the queue.
    """
    im = columns_hold
    call = write(40)
    pane = Pane(call)
    built: list[object] = []
    real = typeset.diffs.diff_model
    monkeypatch.setattr(
        typeset.diffs,
        "diff_model",
        lambda texts, **how: (built.append(texts), real(texts, **how))[1],
    )
    beside = detail.DetailState()

    def detail_too() -> None:
        detail.draw(pane.snap, pane.focus, pane.state, beside, 1.0)

    for _ in range(3):
        pane.draw(im, detail_too)
    mine = diff_view(call)
    theirs = beside.views.view((f"approval:{call.id}", "diff"))
    assert mine is not None and theirs is not None and mine is not theirs
    assert mine.diff is theirs.diff
    assert len(built) == 1
    assert review.unset_lines(pane.state, call.id) == 0

    pane.state.prune(set())
    assert pane.state.diffs == {} and pane.state.diff_whole == set()
