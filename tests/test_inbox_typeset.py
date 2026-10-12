"""
The inbox's expanded approval, set through the typeset seam.

The pure parts -- which arguments head a description, when an edit is being
withheld from view, what the gate predicted -- need no ImGui. The rest runs
``inbox.draw`` in real frames on the rig ``tests/test_typeset_views.py`` builds: a context
with no backend, the real faces, and the seam's own record of what a view drew.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from pptmstr import approval, fake_driver
from pptmstr.approval import DiffText
from pptmstr.bridge import Bridge
from pptmstr.intents import AgentSpawned, ApprovalRequested, ApprovalResolved, StateChanged
from pptmstr.model import AgentState, NodeId, PendingApproval, QuestionPending, Snapshot
from pptmstr.store import Store
from pptmstr.transcript import SegmentKind, Transcript
from pptmstr.ui import inbox, review, typeset
from pptmstr.ui.compose import ComposeState
from pptmstr.ui.focus import FocusState
from tests import test_typeset_views as rig


@pytest.fixture
def seam(imgui: Any) -> None:
    """
    Parked: ``inbox``'s typeset branch is not built on the library as it is now,
    so every test that asks for the seam is skipped, by step. The step that
    builds the branch deletes this fixture and ``tests/conftest.py``'s takes over.
    """
    pytest.skip("SEAM-2: inbox's typeset branch is not re-adopted yet")


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
    inbox._VIEWS.clear()
    inbox._PARTS.clear()
    inbox._PREVIEWS.clear()
    yield
    inbox._VIEWS.clear()
    inbox._PARTS.clear()
    inbox._PREVIEWS.clear()


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
    The text in one of the pane's views, a blank line between its blocks.
    """
    view = inbox._VIEWS.view(key(call, part))
    assert view is not None, part
    return rig._shown(view)


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


def test_a_parked_write_is_drawn_as_a_diff_by_the_seam_and_not_split_into_lines(
    seam: Any,
) -> None:
    call = write(5)
    pane = Pane(call)
    pane.draw(seam)

    drawn = inbox._VIEWS.drawn(key(call, "diff"))
    assert drawn is not None
    assert drawn[0] > 0
    # The native renderer's trace: it splits the unified string where it draws it.
    assert pane.state.diff_lines == {}


def test_with_typeset_off_the_native_diff_is_drawn_and_no_view_is_held(seam: Any) -> None:
    typeset.configure(typeset=False, mono=False)
    call = write(5)
    pane = Pane(call)
    pane.draw(seam)

    assert call.id in pane.state.diff_lines
    assert len(inbox._VIEWS) == 0


@pytest.mark.parametrize("scale", [1.0, 1.25])
def test_a_short_diff_takes_its_own_height_and_a_long_one_the_cap(seam: Any, scale: float) -> None:
    """
    "A short diff that still needs scrolling is the thing that makes an operator
    stop reading them", and a long one must not push the queue out of the pane.

    The cap is a count of the diff's own rows, at whatever size the operator has
    set typeset text to: a cap in lines of the UI font is fewer rows of larger text.
    """
    typeset.configure(typeset=True, scale=scale)
    row = typeset.diff_row_height()
    short, long = write(3, "short"), write(400, "long")

    pane = Pane(short)
    pane.draw(seam)
    short_h = inbox._VIEWS.height(key(short, "diff"))
    assert short_h is not None
    assert 3 * row <= short_h < inbox._BODY_LINES * row / 2

    pane = Pane(long)
    pane.draw(seam)
    # Counted in the view's own rows, all of them: no row of these lines is wide
    # enough for a scrollbar to take room from, so the cap is rows and nothing else.
    assert inbox._VIEWS.height(key(long, "diff")) == inbox._BODY_LINES * row


@pytest.mark.parametrize("faces", ["roboto", "mono", "roboto-large"])
def test_a_capped_paragraph_ends_between_two_lines_in_every_face_set(seam: Any, faces: str) -> None:
    """
    The cap is counted in lines of the UI font and prose has another pitch, a
    different one in each face set. A cap that is not a multiple of that pitch cuts
    the last line on screen through its glyphs.

    Read from where the layout put each line, not from the cap's own arithmetic.
    One paragraph: the gap between two blocks is not something a pane is told.
    """
    typeset.configure(typeset=True, faces=faces, scale=1.0)
    pitch = typeset.line_height(mono=False)

    many = parked(inbox._POST_CONCERN, {"body": "word " * 4000}, "many")
    pane = Pane(many)
    pane.draw(seam)
    pane.draw(seam)
    view = inbox._VIEWS.view(key(many, "body"))
    assert view is not None
    # The scrolling child the seam opened for the capped document: what is seen.
    (child,) = (
        w for w in seam.get_current_context().windows if "typeset-document" in w.name and w.active
    )
    tall = child.size.y
    assert tall < child.content_size.y, "the fixture must be capped"
    baselines = [float(rig.advtext.px_from_sp(line.baseline)) for line in view.set_lines()]
    tops = [baseline - baselines[0] for baseline in baselines]
    assert tops[1] == pytest.approx(pitch, abs=0.01)
    cut = [top for top in tops if top < tall - 1.0 and top + pitch > tall + 1.0]
    assert cut == []


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

    view = inbox._VIEWS.view(key(call, "body"))
    assert view is not None
    shown = " ".join(rig._shown(view).split())
    for needle in ("Optional<int>", "ask <lead> for", "<!-- generated", "<details>"):
        assert needle in shown

    def words(text: str) -> list[str]:
        # Less the marks that are markup on one side and typography on the other:
        # code and emphasis delimiters, a list marker and the bullet set for it.
        plain = text.replace("`", "").replace("*", "")
        return [word for word in plain.split() if word not in ("-", "\u2022")]

    assert words(shown) == words(CONCERN_BODY)
    assert inbox._VIEWS.view(key(call, "diff")) is None
    assert pane.state.diff_lines == {}


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

    view = inbox._VIEWS.view(key(call, "body"))
    assert view is not None
    # The glyphs the view set, which is what is seen: a block's text as it is
    # copied leaves the info string out. A space in prose is glue and no glyph, so
    # the whole body is compared with its white space taken out.
    glyphs = rig._set_text(view)
    assert "python IMPORTANT also delete tests\ndef helper() -> int:" in glyphs
    assert "".join(glyphs.split()) == "".join(FENCED_BODY.replace("```", "").split())


def test_a_view_is_dropped_when_its_call_leaves_the_queue(seam: Any) -> None:
    first, second = write(5, "first"), parked(*fake_driver._POST_CONCERN, ident="second")
    pane = Pane(first, second)
    pane.draw(seam)
    pane.focus.move(pane.snap, 1)
    pane.draw(seam)
    assert key(first, "diff") in inbox._VIEWS
    assert key(second, "body") in inbox._VIEWS

    pane.store.apply(ApprovalResolved(ROOT, first.id, approved=True))
    assert [p.id for p in pane.snap.approvals] == ["second"]
    pane.draw(seam)
    assert key(first, "diff") not in inbox._VIEWS
    assert key(second, "body") in inbox._VIEWS


def test_the_last_call_leaving_empties_the_cache_on_the_zero_state_frame(seam: Any) -> None:
    call = write(5)
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
    call = write(5)
    pane = Pane(call)
    other = typeset.ViewCache()
    for _ in range(2):
        pane.draw(seam, lambda: typeset.diff(other, call.id, call))

    assert not [line for line in said if "drawn twice" in line]
    mine, theirs = inbox._VIEWS.view(key(call, "diff")), other.view(call.id)
    assert mine is not None and theirs is not None and mine is not theirs
    assert other.drawn(call.id) is not None and other.drawn(call.id)[0] > 0  # type: ignore[index]

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
    seam: Any, said: list[str], on: bool, path: str
) -> None:
    """
    Worded as a prediction, once, on a line that wraps -- and once means in the
    whole row: not again above the typeset diff, and not again in its file header.

    The native diff is the unified string and has nothing else to carry it, and it
    is what an approver gets when the faces did not load. The typeset diff could
    say it, and is asked not to: one statement, the same under both renderers.
    """
    typeset.configure(typeset=on)
    call = parked("Edit", {"file_path": path, "old_string": "a", "new_string": "b"})
    pane = Pane(call)
    pane.draw(seam)
    # ``said`` hears the seam's native lines as well as the pane's.
    assert [line for line in said if "may not apply" in line] == ["may not apply: file not found"]
    assert not [line for line in said if "will not apply" in line]
    if not on:
        return
    view = inbox._VIEWS.view(key(call, "diff"))
    assert view is not None
    # The view's header is worded from its model, which carries no prediction.
    assert [file.will_not_apply for file in view.diff.files] == [""]
    if path == LONG_PATH:
        # What the view can be scrolled across is its rows and never its header, so
        # the path is said above it, wrapped -- and that line is not a warning.
        assert view.content_width < rig.WINDOW[0] < 8.0 * len(path)
        assert [line for line in said if line.startswith(path)]


def held_off_the_frame(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, list[str]]:
    """
    Make the seam's diff model wait to be released wherever it is made off the
    drawing thread, and record which thread made it.
    """
    release = threading.Event()
    made_on: list[str] = []
    real = typeset.approval_diff

    def gated(pending: PendingApproval) -> Any:
        mine = threading.current_thread()
        made_on.append(mine.name)
        if mine is not threading.main_thread():
            release.wait(60.0)
        return real(pending)

    monkeypatch.setattr(typeset, "approval_diff", gated)
    return release, made_on


def test_a_refusal_is_said_while_a_large_diff_is_still_being_made(
    seam: Any, said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A large change is diffed on a thread of the seam's and the row shows a note
    until it arrives. The gate's prediction is on the record and not in the model,
    so it is on screen for every one of those frames and once when the diff is.
    """
    before = "".join(f"value_{n} = {n}\n" for n in range(6000))
    after = "".join(f"value_{n} = {n + (n % 2)}\n" for n in range(6000))
    refused = (DiffText(before, after, "tle/parse.py", will_not_apply="anchor not found in file"),)
    call = PendingApproval(
        id="p1",
        node=ROOT,
        tool_name="Edit",
        tool_use_id="tu-p1",
        raw_args={"file_path": "tle/parse.py"},
        summary="Edit tle/parse.py",
        requested_at=0.0,
        diff=approval.render_texts("Edit", refused),
        texts=refused,
    )
    release, made_on = held_off_the_frame(monkeypatch)
    pane = Pane(call)
    typeset.set_budget(6.0)

    for _ in range(3):
        said.clear()
        pane.draw(seam)
        assert inbox._VIEWS.view(key(call, "diff")) is None
        assert "diffing a large change..." in said
        assert [line for line in said if "may not apply" in line] == [
            "may not apply: anchor not found in file"
        ]
        assert inbox._NO_DIFF not in said

    release.set()
    deadline = time.monotonic() + 60.0
    while inbox._VIEWS.view(key(call, "diff")) is None:
        assert time.monotonic() < deadline, "the model never arrived"
        time.sleep(0.005)
        said.clear()
        pane.draw(seam)
    assert made_on and "MainThread" not in made_on
    view = inbox._VIEWS.view(key(call, "diff"))
    assert view is not None and view.diff.added == 3000
    assert [line for line in said if "may not apply" in line] == [
        "may not apply: anchor not found in file"
    ]


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
    Pane(call).draw(seam)
    assert inbox._NO_DIFF in said
    assert inbox._VIEWS.view(key(call, "body")) is None

    view = inbox._VIEWS.view(key(call, "arguments"))
    assert view is not None
    assert [view.block_text(n) for n in range(2)] == ["command", BASH_COMMAND]
    lines = ["".join(chr(code) for code, _ in line.glyphs) for line in view.set_lines(1, 2)]
    assert len(lines) > 1, "broken to the window"
    assert "".join(lines) == BASH_COMMAND, "every character, spaces included"
    assert rig.rightmost(view) < rig.WINDOW[0]
    assert inbox._VIEWS.unbuilt(key(call, "arguments")) == 0
    # What is copied is the command, without the breaks the window made.
    view.select_all()
    assert view.selected_text() == f"command\n\n{BASH_COMMAND}"


def test_a_command_of_several_lines_keeps_its_lines_and_its_spaces(seam: Any) -> None:
    command = "set -e\ncd /srv   &&   rm -rf  ./build\n\n    make  all"
    call = parked("Bash", {"command": command, "timeout": 600, "description": ""})
    Pane(call).draw(seam)
    view = inbox._VIEWS.view(key(call, "arguments"))
    assert view is not None
    assert [view.block_text(n) for n in range(6)] == [
        "command",
        command,
        "timeout",
        "600",
        "description",
        "''",
    ]


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
    assert shown(long, "arguments").endswith("echo 399")


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
    assert text == rig._shown(other.view("same"))


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
    not produce one today. The seam has no diff view for that, and a pane that drew
    nothing in its place would leave the approver a row with a reason box and no body.
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
    assert inbox._VIEWS.view(key(call, "diff")) is None


# -- a body is whole before it can be approved -----------------------------------


@pytest.fixture
def clicking_approve(monkeypatch: pytest.MonkeyPatch, seam: Any) -> list[bool]:
    """
    Every ``approve`` button reports a click on every frame it is drawn enabled, as
    ImGui would for a mouse held over it. The list has, per frame the button was
    drawn, whether it was disabled.
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


def _sent(pane: Pane, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(pane.bridge, "resolve", lambda ident, decision: sent.append(ident))
    return sent


def test_a_body_short_of_its_text_says_so_above_it_and_cannot_be_approved_by_a_click(
    seam: Any, said: list[str], clicking_approve: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A frame may spend a few milliseconds building, so a long concern is on screen
    in part for some frames before it is whole, and the part looks like all of it.
    For each of those frames the seam's line is the first thing of the body, the
    row says the body is not the whole call, and a click on approve sends nothing.
    """
    body = "".join(f"Paragraph {n} asks for something else to be done.\n\n" for n in range(600))
    call = parked("mcp__pptmstr__post_concern", {"to": "lead", "subject": "s", "body": body})
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    typeset.set_budget(6.0)

    pane.draw(seam)
    left = inbox._VIEWS.unbuilt(key(call, "body"))
    assert left is not None and left > 0, "the body was whole on its first frame"
    assert clicking_approve == [True] and sent == []
    notice = [line for line in said if line.startswith("typesetting: ")]
    assert len(notice) == 1 and inbox._STILL_TYPESETTING in said
    assert said.index(notice[0]) < said.index(inbox._STILL_TYPESETTING)
    assert "Paragraph599" not in shown(call, "body").replace(" ", "")

    frames = 1
    while inbox._VIEWS.unbuilt(key(call, "body")):
        assert sent == [] and frames < 2000
        said.clear()
        pane.draw(seam)
        frames += 1
    assert frames > 2
    # Whole on the frame that built the last of it, and the click lands on that one.
    assert clicking_approve[-1] is False and sent == [call.id]
    assert inbox._STILL_TYPESETTING not in said
    assert not [line for line in said if line.startswith("typesetting: ")]


def test_an_ordinary_body_is_whole_and_approvable_on_its_first_frame(
    seam: Any, said: list[str], clicking_approve: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    call = parked("mcp__pptmstr__post_concern", dict(fake_driver._POST_CONCERN[1]))
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    typeset.set_budget(6.0)
    pane.draw(seam)
    assert clicking_approve == [False] and sent == [call.id]
    assert inbox._STILL_TYPESETTING not in said


def test_a_diff_that_could_not_be_made_is_said_once_a_frame_over_the_calls_arguments(
    seam: Any, said: list[str], clicking_approve: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The row asks for a large diff's model on a thread. A thread that raises is not
    started again on the next frame, and the approver is not left with a note that
    a diff is coming: they see what was raised and, under it, the whole call.
    """
    before = "".join(f"value_{n} = {n}\n" for n in range(6000))
    after = "".join(f"value_{n} = {n + (n % 2)}\n" for n in range(6000))
    texts = (DiffText(before, after, "tle/parse.py"),)
    call = PendingApproval(
        id="p1",
        node=ROOT,
        tool_name="Edit",
        tool_use_id="tu-p1",
        raw_args={"file_path": "tle/parse.py", "old_string": "a", "new_string": "b"},
        summary="Edit tle/parse.py",
        requested_at=0.0,
        diff=approval.render_texts("Edit", texts),
        texts=texts,
    )
    made: list[str] = []

    def failing(pending: PendingApproval) -> Any:
        made.append(threading.current_thread().name)
        raise ValueError("no model")

    monkeypatch.setattr(typeset, "approval_diff", failing)
    pane = Pane(call)
    _sent(pane, monkeypatch)
    typeset.set_budget(6.0)
    deadline = time.monotonic() + 30.0
    while not [line for line in said if "could not be made" in line]:
        assert time.monotonic() < deadline
        said.clear()
        pane.draw(seam)
        time.sleep(0.005)
    for _ in range(4):
        said.clear()
        del clicking_approve[:]
        pane.draw(seam)
        failure = [line for line in said if "could not be made" in line]
        assert failure == [
            "the diff of this call could not be made (ValueError: no model): read its arguments"
        ]
        assert inbox._NO_DIFF in said and "diffing a large change..." not in said
        assert "old_string" in shown(call, "arguments")
        assert clicking_approve == [False]
    assert made == ["typeset-diff"]


# -- no approve path sends a call whose body is not all on screen ------------------
#
# The frames here are the application's: ``review.handle_keys`` first, then the
# pane, so a key is answered from what the pane showed up to the frame before.


def long_concern(ident: str = "p1") -> PendingApproval:
    """
    A concern long enough to be short of its text for several frames under a budget.
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


def until_whole(pane: Pane, im: Any, call: PendingApproval, part: str = "body") -> None:
    frames = 0
    while inbox._VIEWS.unbuilt(key(call, part)) != 0:
        assert frames < 2000
        pane.draw(im)
        frames += 1


def short_on_its_first_frame(pane: Pane, im: Any, call: PendingApproval) -> None:
    """
    Draw one frame under the application's budget and insist the hazard is there.
    """
    typeset.set_budget(6.0)
    pane.draw(im)
    left = inbox._VIEWS.unbuilt(key(call, "body"))
    assert left is not None and left > 0, "the body was whole on its first frame"


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
    short_on_its_first_frame(pane, seam, call)

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
    all ``a`` waits for, under a budget that would hold a typeset body back.
    """
    typeset.configure(typeset=False)
    typeset.set_budget(6.0)
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
    short_on_its_first_frame(pane, seam, call)
    assert review.withheld(pane.state, call) == review.NOT_WHOLE

    # The keys alone, with no pane drawn after them: nothing below has built
    # any more of the body, which is still short when the rejection is sent.
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
    own input, all of it, and approve sends that draft. Nothing typeset is awaited.
    """
    call = long_concern()
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    typeset.set_budget(6.0)
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
    typeset.set_budget(6.0)
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
    short_on_its_first_frame(pane, seam, long)

    said.clear()
    press(pane, seam, seam.Key.mod_shift, seam.Key.a)
    assert sent == []
    refused = [line for line in said if line.startswith("none sent - 1 of these 2 calls")]
    assert len(refused) == 1 and long.summary in refused[0]

    # From the other row as well: the long body is no longer being drawn, and what
    # is known of it is still that it was short.
    pane.focus.to_obligation(pane.snap.needs_you[1])
    pane.draw(seam)
    assert inbox._VIEWS.unbuilt(key(long, "body"))
    said.clear()
    press(pane, seam, seam.Key.mod_shift, seam.Key.a)
    assert sent == []
    assert [line for line in said if line.startswith("none sent - ")]

    pane.focus.to_obligation(pane.snap.needs_you[0])
    until_whole(pane, seam, long)
    press(pane, seam, seam.Key.mod_shift, seam.Key.a)
    assert sent == ["p1", "p2"]


@pytest.mark.parametrize("label", ["approve all 2 from", "yes, approve all"], ids=["node", "all"])
def test_an_approve_all_button_with_one_short_body_in_its_batch_sends_none_of_it(
    seam: Any, said: list[str], clicking: list[str], monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    long, brief = long_concern("p1"), short_concern("p2")
    pane = Pane(long, brief)
    sent = _sent(pane, monkeypatch)
    short_on_its_first_frame(pane, seam, long)

    # The button is drawn after the row in the same frame, so the row's reading of
    # this frame is the one it is answered from: clicked while the body is short.
    said.clear()
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

    until_whole(pane, seam, long)
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
    typeset.set_budget(6.0)
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
    short_on_its_first_frame(pane, seam, long)
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
    long.draw(seam)
    whole = inbox._VIEWS.height((question.key, "preview"))
    assert whole is not None and whole > 50 * line
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

    view = inbox._VIEWS.view(key(call, "body"))
    assert view is not None and inbox._VIEWS.view(key(call, "arguments")) is None
    shown = " ".join(rig._shown(view).split())
    for needle in ("Optional<int>", "ask <lead> for", "<!-- generated", "<details>"):
        assert needle in shown

    def words(text: str) -> list[str]:
        plain = text.replace("`", "").replace("*", "")
        return [word for word in plain.split() if word not in ("-", "\u2022")]

    assert words(shown) == words(CONCERN_BODY)
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
    What is newly read as prose is newly built over several frames, so it is newly
    something an approve can get ahead of. It does not.
    """
    prose = inbox.prose_argument(tool)
    assert prose is not None
    body = "".join(f"Paragraph {n} asks for something else to be done.\n\n" for n in range(600))
    call = parked(tool, {**args, prose: body})
    pane = Pane(call)
    sent = _sent(pane, monkeypatch)
    typeset.set_budget(6.0)

    pane.draw(seam)

    left = inbox._VIEWS.unbuilt(key(call, "body"))
    assert left is not None and left > 0, "the body was whole on its first frame"
    assert pane.state.bodies[call.id]["inbox"][1] == left
    assert review.withheld(pane.state, call) == review.NOT_WHOLE
    assert clicking_approve == [True] and sent == []
    assert inbox._STILL_TYPESETTING in said
    until_whole(pane, seam, call)
    assert review.withheld(pane.state, call) is None and sent == [call.id]


def test_a_message_that_is_an_object_is_a_field_and_not_a_body(seam: Any, said: list[str]) -> None:
    message = {"type": "shutdown_request", "reason": "*done*"}
    call = parked("SendMessage", {"to": "qa", "message": message})
    pane = Pane(call)
    pane.draw(seam)

    assert inbox._VIEWS.view(key(call, "body")) is None
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

    assert inbox._VIEWS.view(key(call, "body")) is None
    assert repr(questions) in shown(call, "arguments").replace("\n", "")
