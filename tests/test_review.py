"""
The keyboard shortcuts, and when a key is not one.

The hazard these hold in place: a text box is removed from under the operator
typing in it, the queue's cursor lands on the next row, and the next letter of
the sentence is read as that row's shortcut. Most of this file drives real Dear
ImGui frames -- a context with a display size and no backend, so nothing is
rendered -- in the order ``app.begin_frame`` runs them: settle the cursor, read
the keys, draw the inbox. A recording imgui cannot show this, because the defect
is in what the widget library does with the keyboard on the frame after a widget
stops being drawn.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from imgui_bundle import hello_imgui, imgui

from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionWithdrawn,
    StateChanged,
)
from pptmstr.model import (
    AgentState,
    ApprovalNeeded,
    Decision,
    NodeId,
    PendingApproval,
    QuestionPending,
    Standing,
)
from pptmstr.store import Store
from pptmstr.ui import decision_view, inbox, review
from pptmstr.ui.compose import ComposeState
from pptmstr.ui.focus import FocusState, OnObligation

ROOT: NodeId = ("s1", None)
BUILDER: NodeId = ("s1", "agent-b")
OTHER: NodeId = ("s2", None)

ANSWER = "##answer"
REASON = "##reason"
REPLY = "##reply"
EDIT = "##edit"


# -- the rule, with no imgui ----------------------------------------------------------


def test_keys_are_shortcuts_until_a_text_box_has_had_the_keyboard() -> None:
    hold = review.Hold()
    assert hold.step(text_input=False, gesture=False)
    assert not hold.held


def test_a_text_box_going_away_on_its_own_leaves_the_keys_held() -> None:
    hold = review.Hold()
    assert not hold.step(text_input=True, gesture=False)
    assert not hold.held, "typing in a box is not a hold the operator needs explained"
    for _ in range(600):
        assert not hold.step(text_input=False, gesture=False)
        assert hold.held


@pytest.mark.parametrize("frames_held", [0, 1, 30])
def test_escape_or_a_click_ends_the_hold_and_that_frame_acts_on_nothing(frames_held: int) -> None:
    hold = review.Hold()
    hold.step(text_input=True, gesture=False)
    for _ in range(frames_held):
        hold.step(text_input=False, gesture=False)

    assert not hold.step(text_input=False, gesture=True)
    assert not hold.held
    assert hold.step(text_input=False, gesture=False)


def test_leaving_a_box_by_escape_or_a_click_does_not_hold_the_keys() -> None:
    hold = review.Hold()
    hold.step(text_input=True, gesture=False)
    hold.step(text_input=True, gesture=True)

    assert not hold.step(text_input=False, gesture=False)
    assert not hold.held
    assert hold.step(text_input=False, gesture=False)


def test_a_click_inside_a_box_that_stays_is_not_what_ends_the_typing() -> None:
    """
    The caret is placed with the mouse and the sentence goes on. The box is then
    removed: the click was not the operator leaving.
    """
    hold = review.Hold()
    hold.step(text_input=True, gesture=True)
    hold.step(text_input=True, gesture=False)

    assert not hold.step(text_input=False, gesture=False)
    assert hold.held


def test_typing_again_after_a_hold_ended_holds_again() -> None:
    hold = review.Hold()
    hold.step(text_input=True, gesture=False)
    hold.step(text_input=False, gesture=True)
    assert hold.step(text_input=False, gesture=False)

    hold.step(text_input=True, gesture=False)
    assert not hold.step(text_input=False, gesture=False)
    assert hold.held


def test_the_hold_keeps_what_its_last_step_answered_for_the_other_reader_of_keys() -> None:
    """
    ``app._handle_keys`` reads ``free`` after ``handle_keys`` has stepped, to
    decide the layout keys. The frame that matters is the one that ends a hold:
    ``typing`` is False after it, as it is on a frame nothing was typed before,
    and the keys of the first act on nothing where the keys of the second do.
    """
    hold = review.Hold()
    assert hold.free, "before any step, nothing has been typed"
    said = []
    for text_input, gesture in [(False, False), (True, False), (False, False), (False, True)]:
        answer = hold.step(text_input=text_input, gesture=gesture)
        assert hold.free is answer
        said.append(answer)
    assert said == [True, False, False, False]
    assert not hold.typing and not hold.free, "the frame that ended the hold acted on nothing"

    assert hold.step(text_input=False, gesture=False) and hold.free


# -- Dear ImGui itself ----------------------------------------------------------------


def _decision(did: str = "d-1") -> Decision:
    return Decision(
        id=did,
        asker=BUILDER,
        question="force-push the release tag?",
        blocks=(),
        if_unanswered="the release waits",
        evidence="the tag is public",
        recommendation="do not",
        standing=Standing.OPERATORS,
        reversible=None,
        raised_at=5.0,
    )


def _parked(pid: str, node: NodeId, at: float) -> ApprovalRequested:
    return ApprovalRequested(
        node,
        PendingApproval(
            id=pid,
            node=node,
            tool_name="Bash",
            tool_use_id=f"tu-{pid}",
            raw_args={"command": "git push --force"},
            summary="Bash  git push --force",
            requested_at=at,
        ),
    )


def _team() -> Store:
    store = Store()
    for node, parent, kind in ((ROOT, None, None), (BUILDER, ROOT, "builder"), (OTHER, None, None)):
        store.apply(
            AgentSpawned(
                node_id=node,
                parent=parent,
                task="rotate the signing key",
                model="claude-opus-5",
                started_at=0.0,
                agent_type=kind,
            ),
            now=1.0,
        )
        store.apply(StateChanged(node, AgentState.THINKING), now=1.0)
    return store


class _Frames:
    """
    The frame loop's first three steps and the inbox, against a real context.

    ``at`` is where each text box and button was last drawn, so a test clicks the
    widget and not a coordinate.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, store: Store) -> None:
        self.store = store
        self.focus = FocusState()
        self.review = review.ReviewState()
        self.compose = ComposeState()
        self.decisions = decision_view.DecisionState()
        self.bridge = MagicMock()
        self.actions = MagicMock()
        self.at: dict[str, tuple[float, float]] = {}
        # What the inbox said in its own voice on the last frame drawn.
        self.said: list[str] = []
        self._monkeypatch = monkeypatch

    def __enter__(self) -> _Frames:
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 900.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        at, said = self.at, self.said

        def note(label: str) -> None:
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            at[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)

        def noting(real: Callable[..., object]) -> Callable[..., object]:
            def call(label: str, *args: object, **kwargs: object) -> object:
                result = real(label, *args, **kwargs)
                note(label)
                return result

            return call

        class Noting:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            input_text_with_hint = staticmethod(noting(imgui.input_text_with_hint))
            button = staticmethod(noting(imgui.button))

            @staticmethod
            def text_colored(colour: object, text: str) -> None:
                said.append(text)
                imgui.text_colored(colour, text)  # type: ignore[arg-type]

            @staticmethod
            def text_disabled(text: str) -> None:
                said.append(text)
                imgui.text_disabled(text)

        self._monkeypatch.setattr(inbox, "imgui", Noting())
        self._monkeypatch.setattr(inbox, "multiline_input", noting(inbox.multiline_input))
        self._monkeypatch.setattr(
            decision_view, "multiline_input", noting(decision_view.multiline_input)
        )
        return self

    def __exit__(self, *exc: object) -> None:
        imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            snap = self.store.snapshot()
            self.said.clear()
            imgui.new_frame()
            self.focus.settle(snap)
            review.handle_keys(snap, self.focus, self.review, self.bridge)
            imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
            imgui.set_next_window_size(imgui.ImVec2(1200.0, 880.0))
            imgui.begin("needs you")
            inbox.draw(
                snap,
                self.focus,
                self.review,
                self.compose,
                self.bridge,
                self.actions,
                50.0,
                decisions=self.decisions,
            )
            imgui.end()
            imgui.end_frame()

    def click(self, label: str) -> None:
        x, y = self.at[label]
        self.io.add_mouse_pos_event(x, y)
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(2)

    def type(self, text: str) -> None:
        self.io.add_input_characters_utf8(text)
        self.frame(2)

    def key(self, key: imgui.Key, char: str = "", *, shift: bool = False) -> None:
        """One key going down and up, with the character a keyboard sends beside it."""
        if shift:
            self.io.add_key_event(imgui.Key.mod_shift, True)
        self.io.add_key_event(key, True)
        if char:
            self.io.add_input_characters_utf8(char)
        self.frame()
        self.io.add_key_event(key, False)
        if shift:
            self.io.add_key_event(imgui.Key.mod_shift, False)
        self.frame()

    def on(self) -> str | None:
        return self.focus.target.key if isinstance(self.focus.target, OnObligation) else None

    def approved(self) -> list[str]:
        return [call.args[0] for call in self.bridge.resolve.call_args_list]


@pytest.fixture
def frames(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Frames]:
    """A decision from the builder, then two calls parked by another session."""
    store = _team()
    store.apply(DecisionRaised(BUILDER, _decision()), now=5.0)
    store.apply(_parked("p1", OTHER, 6.0), now=6.0)
    store.apply(_parked("p2", OTHER, 7.0), now=7.0)
    with _Frames(monkeypatch, store) as real:
        real.frame(3)
        yield real


def _withdrawn(store: Store) -> None:
    store.apply(DecisionWithdrawn(BUILDER, "d-1"), now=40.0)


def _answered(store: Store) -> None:
    store.apply(DecisionAnswered("d-1", "no"), now=40.0)


def _dismissed(store: Store) -> None:
    store.apply(DecisionDismissed("d-1"), now=40.0)


ENDINGS = {"withdrawn": _withdrawn, "answered": _answered, "dismissed": _dismissed}


def test_the_harness_presses_keys_the_shortcuts_can_see(frames: _Frames) -> None:
    """Without this every 'nothing was approved' below could be a key never sent."""
    frames.focus.move(frames.store.snapshot(), 1)
    frames.frame()
    assert frames.on() == "approval:p1"

    frames.key(imgui.Key.a, "a")

    assert frames.approved() == ["p1"]


@pytest.mark.parametrize("ending", sorted(ENDINGS))
@pytest.mark.parametrize("shift", [False, True])
def test_a_decision_ending_under_the_typist_does_not_turn_the_next_letter_into_an_approval(
    frames: _Frames, ending: str, shift: bool
) -> None:
    frames.click(ANSWER)
    frames.type("yes, but not on m")
    assert frames.decisions.drafts["d-1"] == "yes, but not on m"
    assert frames.on() == "decision:d-1"

    ENDINGS[ending](frames.store)
    frames.frame()
    assert frames.on() == "approval:p1", "the cursor did land on a parked call"

    for _ in range(4):
        frames.key(imgui.Key.a, "A" if shift else "a", shift=shift)
    frames.key(imgui.Key.e, "e")
    frames.key(imgui.Key.r, "r")
    frames.key(imgui.Key.j, "j")

    assert frames.bridge.mock_calls == []
    assert frames.review.editing is None and not frames.review.focus_reason
    assert frames.on() == "approval:p1"
    assert frames.review.hold.held


def test_the_keys_stay_held_however_long_the_operator_waits(frames: _Frames) -> None:
    frames.click(ANSWER)
    frames.type("yes")
    _withdrawn(frames.store)

    frames.frame(600)
    frames.key(imgui.Key.a, "a")

    assert frames.approved() == []


def test_escape_gives_the_keys_back_and_is_not_itself_read_as_a_shortcut(
    frames: _Frames,
) -> None:
    """
    The cursor was moved to a call whose arguments are part-edited. The Escape
    that ends the hold must not also be the Escape that throws that edit away.
    """
    frames.click(ANSWER)
    frames.type("yes")
    frames.review.edits["p1"] = '{"command": "git push"}'
    _withdrawn(frames.store)
    frames.frame(2)

    frames.key(imgui.Key.escape)

    assert not frames.review.hold.held
    assert frames.review.edits == {"p1": '{"command": "git push"}'}
    assert frames.approved() == []

    frames.review.edits.clear()
    frames.key(imgui.Key.a, "a")
    assert frames.approved() == ["p1"]


def test_a_click_gives_the_keys_back(frames: _Frames) -> None:
    frames.click(ANSWER)
    frames.type("yes")
    _withdrawn(frames.store)
    frames.frame(2)
    frames.key(imgui.Key.a, "a")
    assert frames.approved() == []

    frames.io.add_mouse_pos_event(1100.0, 860.0)
    frames.frame()
    frames.io.add_mouse_button_event(0, True)
    frames.frame()
    frames.io.add_mouse_button_event(0, False)
    frames.frame()

    frames.key(imgui.Key.a, "a")
    assert frames.approved() == ["p1"]


def test_a_rejection_reason_whose_call_is_settled_elsewhere_does_not_approve_the_next_call(
    frames: _Frames,
) -> None:
    """
    The same event from the other side: the box is an approval's, and the row goes
    because its agent's call was settled without the operator.
    """
    frames.focus.move(frames.store.snapshot(), 1)
    frames.frame(2)
    frames.click(REASON)
    frames.type("not on m")
    assert frames.review.reasons["p1"] == "not on m"

    frames.store.apply(ApprovalResolved(OTHER, "p1", approved=False), now=40.0)
    frames.frame()
    assert frames.on() == "approval:p2"

    frames.key(imgui.Key.a, "a")
    frames.key(imgui.Key.a, "A", shift=True)

    assert frames.approved() == []


def test_an_argument_editor_whose_call_is_settled_elsewhere_does_not_approve_the_next_call(
    frames: _Frames,
) -> None:
    frames.focus.move(frames.store.snapshot(), 1)
    frames.frame(2)
    frames.key(imgui.Key.e, "e")
    frames.frame(2)
    frames.click(EDIT)
    frames.type(" ")

    frames.store.apply(ApprovalResolved(OTHER, "p1", approved=False), now=40.0)
    frames.frame()
    assert frames.on() == "approval:p2"

    frames.key(imgui.Key.a, "a")

    assert frames.approved() == []


def test_a_reply_box_whose_session_ends_does_not_approve_the_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _team()
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT), now=5.0)
    store.apply(_parked("p1", OTHER, 6.0), now=6.0)
    with _Frames(monkeypatch, store) as frames:
        frames.frame(3)
        assert isinstance(frames.focus.obligation(store.snapshot()), QuestionPending)
        frames.click(REPLY)
        frames.type("carry on, but")

        store.apply(AgentFinished(ROOT, AgentState.CANCELLED, ended_at=40.0), now=40.0)
        frames.frame()
        assert isinstance(frames.focus.obligation(store.snapshot()), ApprovalNeeded)

        frames.key(imgui.Key.a, "a")

        assert frames.approved() == []


def test_leaving_a_box_with_escape_leaves_the_shortcuts_working(frames: _Frames) -> None:
    """The rule must not cost a gesture the operator has already made."""
    frames.click(ANSWER)
    frames.type("yes")
    frames.key(imgui.Key.escape)
    assert not frames.review.hold.held

    frames.key(imgui.Key.j, "j")
    assert frames.on() == "approval:p1"
    frames.key(imgui.Key.a, "a")
    assert frames.approved() == ["p1"]


def test_leaving_a_box_by_clicking_another_row_leaves_the_shortcuts_working(
    frames: _Frames,
) -> None:
    frames.focus.move(frames.store.snapshot(), 1)
    frames.frame(2)
    frames.click(REASON)
    frames.type("no")

    frames.click("edit")
    assert frames.review.editing == "p1"
    frames.key(imgui.Key.escape)
    assert frames.review.editing is None

    frames.key(imgui.Key.a, "a")
    assert frames.approved() == ["p1"]


def test_typing_into_a_box_that_is_still_there_is_never_a_shortcut(frames: _Frames) -> None:
    frames.focus.move(frames.store.snapshot(), 1)
    frames.frame(2)
    frames.click(REASON)

    frames.key(imgui.Key.a, "a")
    frames.key(imgui.Key.j, "j")

    assert frames.review.reasons["p1"] == "aj"
    assert frames.approved() == [] and frames.on() == "approval:p1"


@pytest.mark.parametrize("frames_between", [0, 1, 2, 5])
@pytest.mark.parametrize("opener", ["r", "e"])
def test_the_key_that_opens_a_box_holds_the_keys_before_the_box_has_the_keyboard(
    frames: _Frames, frames_between: int, opener: str
) -> None:
    """
    ``r`` asks for the reason box and Dear ImGui hands it the keyboard a frame
    later; ``e`` opens the argument editor, which is never handed it at all. A
    reason or an edit beginning with ``a``, typed straight after, lands on
    frames where no text box has the keyboard -- and approved the call being
    rejected. The hold begins at the keypress.
    """
    frames.focus.move(frames.store.snapshot(), 1)
    frames.frame(2)
    assert frames.on() == "approval:p1"
    key = {"r": imgui.Key.r, "e": imgui.Key.e}[opener]

    frames.io.add_key_event(key, True)
    frames.io.add_input_characters_utf8(opener)
    frames.frame()
    frames.io.add_key_event(key, False)
    frames.frame(frames_between)
    frames.io.add_key_event(imgui.Key.a, True)
    frames.io.add_input_characters_utf8("a")
    frames.frame()
    frames.io.add_key_event(imgui.Key.a, False)
    frames.frame(2)
    frames.key(imgui.Key.a, "A", shift=True)

    assert frames.approved() == []
    assert frames.on() == "approval:p1"


def test_r_and_a_arriving_in_one_frame_open_the_reason_and_approve_nothing(
    frames: _Frames,
) -> None:
    """Two key events can share a frame; the one that opens a box wins it."""
    frames.focus.move(frames.store.snapshot(), 1)
    frames.frame(2)

    frames.io.add_key_event(imgui.Key.r, True)
    frames.io.add_key_event(imgui.Key.a, True)
    frames.frame()
    frames.io.add_key_event(imgui.Key.r, False)
    frames.io.add_key_event(imgui.Key.a, False)
    frames.frame(3)

    assert frames.approved() == []


def test_after_r_the_reason_box_takes_the_typing_and_escape_gives_the_keys_back(
    frames: _Frames,
) -> None:
    """
    The other half: a hold that began at ``r`` is an ordinary one. The box gets
    the letters, Escape ends it, and the shortcuts answer again.
    """
    frames.focus.move(frames.store.snapshot(), 1)
    frames.frame(2)

    frames.key(imgui.Key.r, "r")
    frames.frame(2)
    frames.type("ab")
    assert frames.review.reasons.get("p1") == "ab" and frames.approved() == []

    frames.key(imgui.Key.escape)
    frames.key(imgui.Key.a, "a")
    assert frames.approved() == ["p1"]


def test_keyboard_navigation_is_off_in_a_context_configured_as_the_applications_is(
    frames: _Frames,
) -> None:
    """
    With ``NavEnableKeyboard`` on, ``want_capture_keyboard`` is true whenever a
    window has focus, so no shortcut here would ever act; and Space or Enter
    would press the button under the navigation cursor, which is a second way
    for a typed key to approve that ``Hold`` does not cover.

    The application's context is configured by ``hello_imgui``'s
    ``setup_imgui_config`` callback and by nothing of its own, so this runs the
    installed default of that callback on the context the keys are then pressed
    in, and pins that nothing in the application replaces the callback or sets
    the flag. What it does not do is read the flag off a running application.
    """
    import pptmstr

    hello_imgui.imgui_default_settings.setup_default_imgui_config()
    assert not frames.io.config_flags & imgui.ConfigFlags_.nav_enable_keyboard
    sources = Path(pptmstr.__file__).parent.rglob("*.py")
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "nav_enable_keyboard" not in text, source
        assert "setup_imgui_config" not in text, source

    frames.focus.move(frames.store.snapshot(), 1)
    frames.frame(2)
    frames.click(REASON)
    frames.key(imgui.Key.escape)
    for key in (imgui.Key.space, imgui.Key.enter, imgui.Key.tab):
        frames.key(key)

    assert frames.approved() == []


# -- what the header says the keys do -------------------------------------------------


def test_the_header_says_the_keys_are_off_for_as_long_as_they_are(frames: _Frames) -> None:
    frames.click(ANSWER)
    frames.type("yes")
    assert not any(inbox.KEYS_HELD in line for line in frames.said), "typing is not a hold"

    _withdrawn(frames.store)
    frames.frame(3)
    assert any(inbox.KEYS_HELD in line for line in frames.said)
    assert not any("a approve" in line for line in frames.said)

    frames.key(imgui.Key.escape)
    assert not any(inbox.KEYS_HELD in line for line in frames.said)
    assert any("a approve" in line for line in frames.said)


def test_the_header_over_a_decision_row_offers_no_approval_key(frames: _Frames) -> None:
    assert frames.on() == "decision:d-1"
    assert any(
        inbox.keys_hint(frames.focus.obligation(frames.store.snapshot())) in line
        for line in frames.said
    )
    assert not any("a approve" in line or "r reject" in line for line in frames.said)

    frames.key(imgui.Key.j, "j")
    assert frames.on() == "approval:p1"
    assert any("a approve · r reject · e edit" in line for line in frames.said)


def test_each_kind_of_row_is_told_what_the_keyboard_does_on_it() -> None:
    snap = _team()
    snap.apply(DecisionRaised(BUILDER, _decision()), now=5.0)
    snap.apply(_parked("p1", OTHER, 6.0), now=6.0)
    snap.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT), now=7.0)
    snap.apply(AgentFinished(BUILDER, AgentState.FAILED, ended_at=8.0, error="died"), now=8.0)
    hints = {o.kind: inbox.keys_hint(o) for o in snap.snapshot().needs_you}

    assert len(hints) == 4, "one of every kind is waiting"
    assert all(hint.startswith("j/k move") for hint in hints.values())
    approving = [kind for kind, hint in hints.items() if "approve" in hint or "reject" in hint]
    assert [kind.value for kind in approving] == ["approval"]
    assert inbox.keys_hint(None) == "j/k move"


def test_escape_in_the_reply_box_leaves_the_box_and_keeps_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Escape is how the keys are got back after typing, so it must not also be how
    a reply is lost: Dear ImGui answers it by restoring the text the box held
    when the caret entered it.
    """
    store = _team()
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT), now=5.0)
    with _Frames(monkeypatch, store) as frames:
        frames.frame(3)
        frames.click(REPLY)
        frames.type("carry on, but not on main")

        frames.key(imgui.Key.escape)
        frames.frame(2)

        assert frames.compose.replies[ROOT] == "carry on, but not on main"
        assert frames.actions.send.call_count == 0
        assert not frames.review.hold.held
