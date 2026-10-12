"""
A parked call holding what ImGui cannot draw, in front of its approver.

``json.loads('"\\udc80"')`` is a lone surrogate, so a tool call can carry one, and
every ImGui text call raises ``TypeError`` on a string holding one. A NUL raises
nothing and is worse: ImGui reads a string to its first NUL, so the rest of a
command is approved undrawn. Two things are held here, apart, because one is a
cause and the other must hold whatever the cause:

- **Such a call is drawn**, in both panes that show a parked call and under either
  renderer, with each such code unit written out as ``\\uXXXX``, and **what
  approve releases is the call as the agent made it**: the gate's own answer is
  read back off the real park.
- **Nothing is approved over a pane that failed to draw**, by key, click or batch,
  whatever made it fail; a rejection is still sent.

The panes are the application's: ``app._panels`` under ``app.guarded``, as
``app._window`` docks them, with ``review.handle_keys`` ahead of them in the frame
as ``app.begin_frame`` has it. A pane drawn by a test's own call would say nothing
about whether the application records that it failed.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from pptmstr import app, approval
from pptmstr.bridge import Bridge, Decision
from pptmstr.driver import AgentSession
from pptmstr.intents import AgentSpawned, ApprovalRequested
from pptmstr.model import NodeId, PendingApproval, Snapshot
from pptmstr.pool import SessionPool
from pptmstr.settings import Settings
from pptmstr.store import Store
from pptmstr.transcript import Transcript
from pptmstr.ui import detail, inbox, review, typeset

ROOT: NodeId = ("sess-1", None)
INBOX, DETAIL = "NEEDS YOU", "DETAIL"

# What a tool's arguments can hold that ImGui cannot draw, by name, and how each is
# written where it is drawn. The surrogates are made the way one arrives.
LOW = json.loads('"\\udc80"')
HIGH = json.loads('"\\ud800"')
UNDRAWABLE = {"U+DC80": (LOW, "\\udc80"), "U+D800": (HIGH, "\\ud800"), "NUL": ("\x00", "\\u0000")}


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


def call_holding(tool: str, bad: str, tmp_path: Path, ident: str = "p1") -> PendingApproval:
    """
    A Write, an Edit or a Bash call whose substance holds ``bad`` between words.
    """
    if tool == "Write":
        content = f"first = 1\nsecond = 'x{bad}y'\nthird = 3\n"
        return parked("Write", {"file_path": "no/such/dir/made.py", "content": content}, ident)
    if tool == "Edit":
        target = tmp_path / "edited.py"
        target.write_text("first = 1\nsecond = 2\nthird = 3\n")
        args = {
            "file_path": str(target),
            "old_string": "second = 2",
            "new_string": f"s = 'x{bad}y'",
        }
        return parked("Edit", args, ident)
    assert tool == "Bash"
    return parked("Bash", {"command": f"printf 'x{bad}y' > out && rm -rf build"}, ident)


def clean(ident: str) -> PendingApproval:
    return parked("Bash", {"command": f"echo {ident}"}, ident)


@pytest.fixture(params=["imgui", "typeset"])
def im(request: pytest.FixtureRequest) -> Iterator[Any]:
    """
    A context to draw the panes in, once with ImGui's own text and once with the
    typeset seam at the settings the product starts with. A pane that raises
    part-way leaves ImGui's stack open, and ``app.guarded`` is safe only where
    ImGui recovers from that, so recovery is set as ``app.run`` sets it.
    """
    if request.param == "typeset":
        context = request.getfixturevalue("seam")
    else:
        context = request.getfixturevalue("imgui")
        typeset.configure(typeset=False)
    io = context.get_io()
    held = (
        io.config_error_recovery,
        io.config_error_recovery_enable_assert,
        io.config_error_recovery_enable_tooltip,
    )
    io.config_error_recovery = True
    io.config_error_recovery_enable_assert = False
    io.config_error_recovery_enable_tooltip = False
    caches = (inbox._VIEWS, inbox._PARTS, inbox._PREVIEWS, inbox._WIDTHS, inbox._WHOLE)
    for cache in caches:
        cache.clear()
    app._PANEL_ERRORS.clear()
    yield context
    for cache in caches:
        cache.clear()
    app._PANEL_ERRORS.clear()
    (
        io.config_error_recovery,
        io.config_error_recovery_enable_assert,
        io.config_error_recovery_enable_tooltip,
    ) = held


class Desk:
    """
    The application's two review panes over one session's parked calls, the cursor
    on the first, and every decision the bridge was asked to carry.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *calls: PendingApproval) -> None:
        self.app = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
        self.app.pool = SessionPool(self.app.bridge, cap=1)
        self.app.store.apply(
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
            self.app.store.apply(ApprovalRequested(ROOT, call))
        self.app.frame_snap = self.snap
        self.app.focus.to_obligation(self.snap.needs_you[0])
        panels = app._panels(self.app)
        self.panes = {label: app.guarded(label, panels[label]) for label in (INBOX, DETAIL)}
        self.sent: list[tuple[str, Decision]] = []
        monkeypatch.setattr(
            self.app.bridge, "resolve", lambda ident, decision: self.sent.append((ident, decision))
        )

    @property
    def snap(self) -> Snapshot:
        return self.app.store.snapshot()

    @property
    def state(self) -> review.ReviewState:
        return self.app.review

    def call(self, ident: str) -> PendingApproval:
        """
        The call as the store holds it, which is what the panes are handed.
        """
        (found,) = (p for p in self.snap.approvals if p.id == ident)
        return found

    def frame(self, im: Any, *keys: Any) -> None:
        """
        One frame of the application's order: the keys, then each pane in a window
        of its own; and, where keys went down, a frame to let go of them in.
        """
        io = im.get_io()
        for key in keys:
            io.add_key_event(key, True)
        typeset.pre_frame()
        im.new_frame()
        try:
            assert all(im.is_key_down(key) for key in keys)
            assert not keys or not io.want_capture_keyboard
            review.handle_keys(self.snap, self.app.focus, self.state, self.app.bridge)
            for column, (label, pane) in enumerate(self.panes.items()):
                im.set_next_window_pos(im.ImVec2(10.0 + 590.0 * column, 10.0))
                im.set_next_window_size(im.ImVec2(580.0, 760.0))
                im.begin(label)
                pane()
                im.end()
        finally:
            im.render()
        if keys:
            for key in keys:
                io.add_key_event(key, False)
            self.frame(im)

    def approved(self) -> list[str]:
        return [ident for ident, decision in self.sent if decision.approved]

    def rejected(self) -> list[str]:
        return [ident for ident, decision in self.sent if not decision.approved]


def failed() -> dict[str, str]:
    """
    Which of the two panes ``app.guarded`` caught, and what each raised.
    """
    return {name: said for name, said in app._PANEL_ERRORS.items() if name in (INBOX, DETAIL)}


@pytest.fixture
def handed(monkeypatch: pytest.MonkeyPatch, im: Any) -> list[str]:
    """
    Every string a pane hands one of ImGui's text calls, in order, measured or drawn.
    """
    strings: list[str] = []

    def recording(name: str, at: int) -> None:
        real = getattr(im, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            if len(args) > at and isinstance(args[at], str):
                strings.append(args[at])
            return real(*args, **kwargs)

        monkeypatch.setattr(im, name, call)

    for name in ("text", "text_unformatted", "text_wrapped", "text_disabled", "calc_text_size"):
        recording(name, 0)
    recording("text_colored", 1)
    return strings


def set_by_the_seam(desk: Desk) -> list[str]:
    """
    Every text the two panes hold for the typeset seam to set, and the texts of
    each diff the library's view was made from.
    """
    out: list[str] = []
    for cache in (inbox._VIEWS, desk.app.detail_pane_state.views):
        for key in cache.keys():
            # A diff view is made from the texts below, and holds rows, not blocks.
            if all(hasattr(view, "texts") for view in cache.views(key)):
                out += cache.texts(key)
    for held in desk.state.diffs.values():
        for text in held.texts:  # type: ignore[attr-defined]
            out += [text.before or "", text.after or ""]
    return out


# -- the cause: such a call is drawn, and says what it holds ---------------------------


@pytest.mark.parametrize("bad", sorted(UNDRAWABLE))
@pytest.mark.parametrize("tool", ["Write", "Edit", "Bash"])
def test_a_call_holding_what_imgui_cannot_draw_is_drawn_whole_in_both_panes(
    im: Any, handed: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tool: str, bad: str
) -> None:
    char, written = UNDRAWABLE[bad]
    desk = Desk(monkeypatch, call_holding(tool, char, tmp_path))

    desk.frame(im)
    desk.frame(im)

    assert failed() == {}
    readings = desk.state.bodies.get("p1", {})
    assert {pane: unset for pane, (_, unset) in readings.items()} == {"inbox": 0, "detail": 0}

    drawn = handed + set_by_the_seam(desk)
    assert not [text for text in drawn if char in text], "handed to be drawn as it arrived"
    # Between the words that stood on either side of it: nothing after it is lost,
    # which is what a NUL costs a string ImGui reads to its first one.
    assert [text for text in drawn if f"x{written}y" in text]


def test_each_pane_says_above_such_a_call_that_what_is_drawn_is_not_all_text(
    im: Any, handed: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``printf '\\udc80'`` is a command a shell understands. The escape alone would
    read as something the agent typed, so each pane that draws the body says what
    it stands for and that approve sends the call as it was made.
    """
    desk = Desk(monkeypatch, call_holding("Bash", LOW, tmp_path), clean("p2"))
    desk.frame(im)
    assert handed.count(review.ESCAPED) == 2
    assert failed() == {}

    # The control: a call that is all text is not said to be anything else.
    del handed[:]
    desk = Desk(monkeypatch, clean("p1"))
    desk.frame(im)
    assert review.ESCAPED not in handed and handed


@pytest.mark.parametrize("bad", sorted(UNDRAWABLE))
def test_a_row_the_cursor_is_not_on_is_drawn_and_so_is_every_row_after_it(
    im: Any, handed: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad: str
) -> None:
    """
    A closed row is its summary, which for a Bash call is the command: the one line
    of the queue every frame draws, whichever call the cursor is on.
    """
    char, written = UNDRAWABLE[bad]
    desk = Desk(monkeypatch, clean("p1"), call_holding("Bash", char, tmp_path, "p2"), clean("p3"))

    desk.frame(im)

    assert failed() == {}
    assert [text for text in handed if f"x{written}y" in text]
    assert "echo p3" in handed
    assert not [text for text in handed if char in text]


def test_a_sequence_written_out_in_a_call_is_not_drawn_as_the_code_unit_it_spells(
    im: Any, handed: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``printf '\\udc80'`` with a backslash and five characters is another command
    than one holding the code unit, and the two are approved from what is drawn.
    """
    spelled = parked("Bash", {"command": "printf 'x\\udc80y' > out"}, "p1")
    held = call_holding("Bash", LOW, tmp_path, "p2")
    desk = Desk(monkeypatch, spelled, held)

    desk.frame(im)

    rows = [text for text in handed if text.startswith("printf")]
    assert [text for text in rows if "x\\u005cudc80y" in text]
    assert [text for text in rows if "x\\udc80y" in text]
    assert len({text for text in rows}) >= 2


# -- what approve releases is the call as the agent made it ---------------------------


def _answer(tool_input: dict[str, Any], decide: Callable[[PendingApproval, Bridge], None]) -> Any:
    """
    The real gate's park of a call, as far as its answer to the CLI: what the
    hook returns once ``decide`` has settled the call the store was told of.
    """
    bridge = Bridge()
    bridge.start()
    try:
        session = AgentSession(bridge, task="lead")
        answer = bridge.submit(session._park("Bash", tool_input, "tu-1", session.node_id))
        store = Store()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not store.snapshot().approvals:
            store.apply_all(bridge.drain())
            time.sleep(0.005)
        (pending,) = store.snapshot().approvals
        decide(pending, bridge)
        return answer.result(timeout=5.0)
    finally:
        bridge.stop()


@pytest.mark.parametrize("bad", sorted(UNDRAWABLE))
def test_an_approved_call_runs_with_the_arguments_the_agent_sent(bad: str) -> None:
    """
    An approval with no edit answers the CLI with no ``updatedInput``, so the tool
    runs on the input the CLI already holds; and the gate did not alter the
    mapping it was handed on the way.
    """
    char, written = UNDRAWABLE[bad]
    command = f"printf 'x{char}y' > out"
    tool_input = {"command": command}
    seen: list[PendingApproval] = []

    def approve(pending: PendingApproval, bridge: Bridge) -> None:
        seen.append(pending)
        state = review.ReviewState()
        state.body_shown("inbox", pending.id, 0, frame=1)
        assert review.resolve(bridge, state, pending, approved=True)

    answer = _answer(tool_input, approve)

    specific = answer["hookSpecificOutput"]
    assert specific["permissionDecision"] == "allow"
    assert "updatedInput" not in specific
    assert tool_input == {"command": command} and tool_input["command"] is command
    # The control: the record the panes drew is the escaped one, so the assertion
    # above is not true merely because nothing anywhere was changed.
    assert seen[0].summary == f"printf 'x{written}y' > out"
    assert seen[0].raw_args == {"command": command}


@pytest.mark.parametrize("bad", sorted(UNDRAWABLE))
def test_an_edit_opened_and_approved_unchanged_sends_the_arguments_the_agent_sent(bad: str) -> None:
    """
    The one route on which pptmstr itself hands the tool its arguments: the draft
    the editor opens on is what approve sends as ``updatedInput``.
    """
    char, _ = UNDRAWABLE[bad]
    command = f"printf 'x{char}y' > out"

    def edit_then_approve(pending: PendingApproval, bridge: Bridge) -> None:
        state = review.ReviewState()
        state.edits[pending.id] = review.pretty_args(pending)
        # The draft is what the editor draws, and ImGui's input is handed it.
        assert state.edits[pending.id].isascii() and "\x00" not in state.edits[pending.id]
        state.body_shown("inbox", pending.id, 0, frame=1)
        assert review.resolve(bridge, state, pending, approved=True)

    answer = _answer({"command": command}, edit_then_approve)

    sent = answer["hookSpecificOutput"]["updatedInput"]
    assert sent == {"command": command}
    assert [ord(c) for c in sent["command"]] == [ord(c) for c in command]


# -- the invariant: nothing is approved over a pane that failed to draw ---------------


def breaks(monkeypatch: pytest.MonkeyPatch, pane: str) -> None:
    """
    Make one pane raise on every frame, after it has drawn its rows: a defect in a
    pane, of no particular cause.
    """
    if pane == INBOX:
        real = inbox._batch_controls

        def raising(*args: Any, **kwargs: Any) -> None:
            real(*args, **kwargs)
            raise RuntimeError("a slip in the inbox")

        monkeypatch.setattr(inbox, "_batch_controls", raising)
    else:

        def no_header(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("a slip in DETAIL")

        monkeypatch.setattr(detail, "_header", no_header)


def clicks(monkeypatch: pytest.MonkeyPatch, im: Any) -> list[str]:
    """
    Every button whose label starts with one of the strings in the list reports a
    click on every frame it is drawn.
    """
    labels: list[str] = []
    real = im.button

    def button(label: str, *rest: Any) -> bool:
        pressed = bool(real(label, *rest))
        return pressed or any(label.startswith(prefix) for prefix in labels)

    monkeypatch.setattr(im, "button", button)
    return labels


@pytest.mark.parametrize("pane", [INBOX, DETAIL])
def test_a_is_not_handled_while_a_pane_showing_the_call_fails_to_draw(
    im: Any, handed: list[str], monkeypatch: pytest.MonkeyPatch, pane: str
) -> None:
    desk = Desk(monkeypatch, clean("p1"), clean("p2"))
    # The control: both panes drew the call whole, and ``a`` would be sent.
    desk.frame(im)
    desk.frame(im)
    assert failed() == {} and review.withheld(desk.state, desk.call("p1")) is None

    breaks(monkeypatch, pane)
    del handed[:]
    desk.frame(im)
    assert list(failed()) == [pane]
    # Said once, in the pane that failed, under what it raised.
    assert handed.count(app._APPROVE_HELD) == 1
    desk.frame(im, im.Key.a)

    assert desk.sent == []
    said = review.refusal_for(desk.state, "p1")
    assert said is not None and "failed to draw" in said


@pytest.mark.parametrize("pane", [INBOX, DETAIL])
def test_shift_a_and_approve_everything_send_nothing_while_a_pane_fails_to_draw(
    im: Any, monkeypatch: pytest.MonkeyPatch, pane: str
) -> None:
    """
    The batch forms approve calls nobody opened, so "no pane drew it" does not hold
    them; a pane that was asked to draw and could not is another thing.
    """
    desk = Desk(monkeypatch, clean("p1"), clean("p2"))
    breaks(monkeypatch, pane)
    desk.frame(im)
    desk.frame(im)

    desk.frame(im, im.Key.mod_shift, im.Key.a)
    assert desk.sent == []
    said = review.refusal_for(desk.state, "p2")
    assert said is not None and "failed to draw" in said

    assert review.approve_everything(desk.app.bridge, desk.snap, desk.state) == 0
    assert desk.sent == []


def test_a_click_on_approve_sends_nothing_while_a_pane_fails_to_draw(
    im: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The row's own button is reached only in a frame the inbox drew that row in, so
    the pane that can be failing under a click is the other one, or the inbox
    further down than the row.
    """
    for pane in (INBOX, DETAIL):
        with monkeypatch.context() as patched:
            desk = Desk(patched, clean("p1"), clean("p2"))
            clicking = clicks(patched, im)
            breaks(patched, pane)
            desk.frame(im)
            desk.frame(im)

            clicking.append("approve")
            desk.frame(im)
            assert desk.sent == [], pane
            del clicking[:]
        app._PANEL_ERRORS.clear()


def test_the_batch_buttons_send_nothing_while_detail_fails_to_draw(
    im: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    desk = Desk(monkeypatch, clean("p1"), clean("p2"))
    clicking = clicks(monkeypatch, im)
    breaks(monkeypatch, DETAIL)
    desk.frame(im)
    desk.frame(im)

    clicking.append("approve all 2 from")
    desk.frame(im)
    assert desk.sent == []

    del clicking[:]
    desk.state.confirming_global = True
    clicking.append("yes, approve all")
    desk.frame(im)
    assert desk.sent == []


@pytest.mark.parametrize("pane", [INBOX, DETAIL])
def test_approve_is_handled_again_once_the_pane_draws(
    im: Any, monkeypatch: pytest.MonkeyPatch, pane: str
) -> None:
    desk = Desk(monkeypatch, clean("p1"), clean("p2"))
    with monkeypatch.context() as patched:
        breaks(patched, pane)
        desk.frame(im)
        desk.frame(im, im.Key.a)
        assert desk.sent == []

    # The frame the pane first draws in again is the one the key is read ahead of.
    desk.frame(im, im.Key.a)
    assert desk.sent == []
    desk.frame(im, im.Key.a)
    assert desk.approved() == ["p1"]


@pytest.mark.parametrize("pane", [INBOX, DETAIL])
def test_a_rejection_is_still_sent_while_a_pane_fails_to_draw(
    im: Any, monkeypatch: pytest.MonkeyPatch, pane: str
) -> None:
    desk = Desk(monkeypatch, clean("p1"), clean("p2"))
    breaks(monkeypatch, pane)
    desk.frame(im)
    desk.frame(im)

    assert review.resolve(desk.app.bridge, desk.state, desk.call("p1"), approved=False)
    assert desk.rejected() == ["p1"] and desk.approved() == []


def test_r_rejects_outright_where_the_row_that_takes_a_reason_was_not_drawn(
    im: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``r`` puts the keyboard in the row's reason field and the row's button sends.
    With the inbox not drawing its rows there is neither, and a call nobody can
    refuse is an agent parked for good.
    """
    desk = Desk(monkeypatch, clean("p1"), clean("p2"))

    def no_rows(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("a slip in the row")

    with monkeypatch.context() as patched:
        patched.setattr(inbox, "_row", no_rows)
        desk.frame(im)
        desk.frame(im, im.Key.r)
        assert desk.rejected() == ["p1"] and desk.approved() == []
        assert not desk.state.focus_reason

    # The control: with the row drawn, ``r`` asks for the reason and sends nothing.
    del desk.sent[:]
    desk.frame(im)
    desk.frame(im)
    desk.frame(im, im.Key.r)
    assert desk.sent == []


def test_what_a_pane_raised_cannot_stop_the_guard_that_caught_it(
    im: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``guarded`` draws what was raised, and an exception can carry the text that
    could not be drawn. The guard raising is the application going down.
    """

    def pane() -> None:
        raise ValueError(f"could not set 'x{LOW}y\x00z'")

    said: list[str] = []
    real = im.text_wrapped

    def wrapped(text: str) -> None:
        said.append(text)
        real(text)

    monkeypatch.setattr(im, "text_wrapped", wrapped)
    typeset.pre_frame()
    im.new_frame()
    try:
        im.begin("guarded")
        app.guarded("a pane", pane)()
        im.end()
    finally:
        im.render()
    assert said == ["ValueError: could not set 'x\\udc80y\\u0000z'"]
