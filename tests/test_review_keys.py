"""
The review keys, driven through a real ImGui frame.

``handle_keys`` reads the keyboard from ImGui's own IO rather than taking it as an
argument, so a mock of ``imgui`` would only assert what the mock was told to say.
These run a headless context instead -- no window, no renderer, one ``new_frame``
per keystroke -- which is the cheapest thing that still exercises ImGui's real
answer to "was this key pressed, and what was held with it".

The hazard is a chord landing on a bare-letter binding. A text view that binds
Ctrl+A to select-all submits no ImGui item, so ``want_capture_keyboard`` is false
while the operator is over it and the capture guard does not see the chord coming.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from pptmstr.bridge import Decision
from pptmstr.intents import AgentSpawned, ApprovalRequested
from pptmstr.model import NodeId, PendingApproval, Snapshot
from pptmstr.store import Store
from pptmstr.ui import review
from pptmstr.ui.focus import FocusState, OnObligation

ROOT: NodeId = ("s1", None)
OTHER: NodeId = ("s2", None)

CTRL = imgui.Key.mod_ctrl
ALT = imgui.Key.mod_alt
SUPER = imgui.Key.mod_super
SHIFT = imgui.Key.mod_shift


class RecordingBridge:
    """
    The one method ``handle_keys`` can reach, recording what it was asked.
    """

    def __init__(self) -> None:
        self.resolved: list[tuple[str, Decision]] = []

    def resolve(self, pending_id: str, decision: Decision) -> None:
        self.resolved.append((pending_id, decision))


@pytest.fixture
def frame() -> Iterator[None]:
    """
    A headless ImGui context. ``renderer_has_textures`` lets ``new_frame`` run
    without a font atlas having been uploaded, and the ini is disabled so a test
    run leaves no ``imgui.ini`` behind in the working directory.
    """
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(800, 600)
    io.delta_time = 1 / 60
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    try:
        yield
    finally:
        imgui.destroy_context(ctx)


def pending(node: NodeId, pid: str, *, at: float) -> PendingApproval:
    return PendingApproval(
        id=pid,
        node=node,
        tool_name="Write",
        tool_use_id=f"tu-{pid}",
        raw_args={"path": pid},
        summary=f"Write {pid}",
        requested_at=at,
    )


def queue() -> Snapshot:
    """
    Two approvals from ROOT and one from OTHER, oldest first: p1, p2, p3.
    """
    store = Store()
    for node in (ROOT, OTHER):
        store.apply(
            AgentSpawned(
                node_id=node,
                parent=None,
                task="a task",
                model="claude-sonnet-5",
                started_at=0.0,
                cwd="/x/proj",
            ),
            now=0.0,
        )
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)), now=1.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p2", at=2.0)), now=2.0)
    store.apply(ApprovalRequested(OTHER, pending(OTHER, "p3", at=3.0)), now=3.0)
    return store.snapshot()


def read_whole(state: review.ReviewState | None = None) -> review.ReviewState:
    """
    A review state in which a pane has drawn every call in the queue, whole.

    ``a`` approves nothing a pane has not shown, so this is the state every test
    of a binding starts from: a "must not approve" over a queue nobody had drawn
    would pass with the chord guard deleted.
    """
    state = review.ReviewState() if state is None else state
    for pid in ("p1", "p2", "p3"):
        state.body_shown("inbox", pid, 0, frame=1)
    return state


def press(
    key: imgui.Key,
    *mods: imgui.Key,
    start: int = 0,
    state: review.ReviewState | None = None,
) -> tuple[RecordingBridge, FocusState, review.ReviewState]:
    """
    One frame with ``key`` pressed and ``mods`` held, the cursor on the
    ``start``-th obligation (p1 unless told otherwise), over a queue whose bodies
    have all been read whole unless a ``state`` says otherwise.

    Asserts the two things every test here rests on: ImGui reports the key as
    pressed, and nothing has captured the keyboard. Without them a test that
    expects "nothing happened" would pass because no key arrived at all.
    """
    snap = queue()
    focus = FocusState()
    focus.to_obligation(snap.needs_you[start])
    state = read_whole() if state is None else state
    bridge = RecordingBridge()

    io = imgui.get_io()
    for mod in mods:
        io.add_key_event(mod, True)
    io.add_key_event(key, True)
    imgui.new_frame()
    try:
        assert imgui.is_key_pressed(key)
        assert not io.want_capture_keyboard
        review.handle_keys(snap, focus, state, bridge)  # type: ignore[arg-type]
    finally:
        imgui.end_frame()
        # Let go again, and give ImGui a frame to see it. A key still down from an
        # earlier call is held rather than pressed, and a modifier still down
        # would turn the next bare letter into a chord.
        for held in (key, *mods):
            io.add_key_event(held, False)
        imgui.new_frame()
        imgui.end_frame()
    return bridge, focus, state


# -- the bindings themselves ---------------------------------------------------
#
# Positive controls. If these fail the harness is not delivering keys, and every
# "must not" below is passing for the wrong reason.


def test_a_approves_the_focused_call(frame: None) -> None:
    bridge, _, _ = press(imgui.Key.a)

    assert [(pid, d.approved) for pid, d in bridge.resolved] == [("p1", True)]


def test_shift_a_approves_the_focused_agents_queue_and_nobody_elses(frame: None) -> None:
    bridge, _, _ = press(imgui.Key.a, SHIFT)

    assert [(pid, d.approved) for pid, d in bridge.resolved] == [("p1", True), ("p2", True)]


def test_the_bare_letters_move_reject_and_edit(frame: None) -> None:
    _, focus, _ = press(imgui.Key.j)
    assert isinstance(focus.target, OnObligation)
    assert focus.index(queue()) == 1

    _, _, state = press(imgui.Key.r)
    assert state.focus_reason

    _, _, state = press(imgui.Key.e)
    assert state.editing == "p1"


# -- the hazard ----------------------------------------------------------------


@pytest.mark.parametrize("mod", [CTRL, ALT, SUPER], ids=["ctrl", "alt", "super"])
def test_a_chord_on_a_does_not_approve_the_focused_call(frame: None, mod: imgui.Key) -> None:
    """
    Ctrl+A is select-all in the text views. Selecting a diff must not approve it.
    """
    bridge, _, _ = press(imgui.Key.a, mod)

    assert bridge.resolved == []


@pytest.mark.parametrize("mod", [CTRL, ALT, SUPER], ids=["ctrl", "alt", "super"])
def test_a_chord_with_shift_does_not_approve_the_agents_whole_queue(
    frame: None, mod: imgui.Key
) -> None:
    """
    Shift keeps its meaning only on a bare A. Ctrl+Shift+A is somebody else's chord.
    """
    bridge, _, _ = press(imgui.Key.a, mod, SHIFT)

    assert bridge.resolved == []


@pytest.mark.parametrize("mod", [CTRL, ALT, SUPER], ids=["ctrl", "alt", "super"])
def test_a_chord_does_not_move_the_cursor_or_open_a_draft(frame: None, mod: imgui.Key) -> None:
    """
    The other letters are quieter than approve but not harmless: a cursor that
    moves under Ctrl+K puts the next bare ``a`` on a call the operator was not
    reading.
    """
    snap = queue()

    for key in (imgui.Key.j, imgui.Key.k):
        _, focus, _ = press(key, mod)
        assert focus.index(snap) == 0

    # From the middle of the queue as well: k from the top is clamped, so the
    # loop above cannot tell an ignored Ctrl+K from an honoured one.
    _, focus, _ = press(imgui.Key.k, mod, start=1)
    assert focus.index(snap) == 1

    _, _, state = press(imgui.Key.r, mod)
    assert not state.focus_reason

    _, _, state = press(imgui.Key.e, mod)
    assert state.editing is None
    assert state.edits == {}


def test_escape_still_cancels_with_a_modifier_held(frame: None) -> None:
    """
    Cancelling is the safe direction, and it is not a letter: a modifier left down
    must not be what keeps an edit or a pending approve-everything confirm open.
    """
    draft = review.ReviewState(editing="p1", confirming_global=True, edits={"p1": "{}"})

    bridge, _, state = press(imgui.Key.escape, CTRL, state=draft)

    assert state.editing is None
    assert not state.confirming_global
    assert state.edits == {}
    assert bridge.resolved == []


# -- what a pane showed decides what ``a`` may send --------------------------------
#
# The readings here are written as a pane writes them. The panes writing them, and
# a body really held short by the seam's budget, are tests/test_inbox_typeset.py's
# and tests/test_detail.py's.


def test_a_over_a_call_no_pane_has_drawn_sends_nothing_and_says_why(frame: None) -> None:
    bridge, _, state = press(imgui.Key.a, state=review.ReviewState())

    assert bridge.resolved == []
    assert review.refusal_for(state, "p1") == review.NOT_DRAWN
    assert review.refusal_for(state, "p2") is None


def test_a_over_a_body_last_drawn_short_sends_nothing_and_says_why(frame: None) -> None:
    state = read_whole()
    state.body_shown("inbox", "p1", 40, frame=2)

    bridge, _, state = press(imgui.Key.a, state=state)

    assert bridge.resolved == []
    assert review.refusal_for(state, "p1") == review.NOT_WHOLE

    state.body_shown("inbox", "p1", 0, frame=3)
    bridge, _, state = press(imgui.Key.a, state=state)
    assert [(pid, d.approved) for pid, d in bridge.resolved] == [("p1", True)]
    assert state.refusal is None


def test_one_pane_whole_does_not_make_the_other_so_in_either_order(frame: None) -> None:
    """
    Two panes show one call in views of their own. The reading is the worse of the
    two whichever wrote last, and whichever of them the frame drew first.
    """
    readings = [("inbox", 0), ("detail", 7)]
    for order in (readings, readings[::-1]):
        state = review.ReviewState()
        for pane, unbuilt in order:
            state.body_shown(pane, "p1", unbuilt, frame=5)
        assert review.unset_lines(state, "p1") == 7

    # Part-way through a frame: one pane has this frame's reading, the other the
    # last frame's, and a click is answered between them.
    state = review.ReviewState()
    state.body_shown("detail", "p1", 7, frame=5)
    state.body_shown("inbox", "p1", 0, frame=6)
    assert review.unset_lines(state, "p1") == 7


def test_a_pane_that_stopped_drawing_a_call_does_not_hold_it_for_good(frame: None) -> None:
    """
    DETAIL drew the body short and then went behind another tab. Its reading is of
    a view nobody is looking at, and the inbox has had the body whole since.
    """
    state = read_whole()
    state.body_shown("detail", "p1", 300, frame=5)
    state.body_shown("inbox", "p1", 300, frame=5)
    for at in (6, 7):
        state.body_shown("inbox", "p1", 0, frame=at)

    bridge, _, _ = press(imgui.Key.a, state=state)

    assert [(pid, d.approved) for pid, d in bridge.resolved] == [("p1", True)]


def test_shift_a_with_a_body_known_short_in_the_agents_queue_sends_none_of_it(
    frame: None,
) -> None:
    state = read_whole()
    state.body_shown("inbox", "p2", 12, frame=2)

    bridge, _, state = press(imgui.Key.a, SHIFT, state=state)

    assert bridge.resolved == []
    said = review.refusal_for(state, "p1")
    assert said is not None and said.startswith("none sent - 1 of these 2 calls")
    assert "Write p2" in said and review.refusal_for(state, "p2") == said
    assert review.refusal_for(state, "p3") is None


def test_a_short_body_in_another_agents_queue_does_not_hold_this_ones(frame: None) -> None:
    state = read_whole()
    state.body_shown("inbox", "p3", 12, frame=2)

    bridge, _, _ = press(imgui.Key.a, SHIFT, state=state)

    assert [pid for pid, _ in bridge.resolved] == ["p1", "p2"]


def test_approve_everything_with_one_body_known_short_sends_nothing(frame: None) -> None:
    snap = queue()
    state = read_whole(review.ReviewState(confirming_global=True))
    state.body_shown("inbox", "p3", 12, frame=2)
    bridge = RecordingBridge()

    assert review.approve_everything(bridge, snap, state) == 0  # type: ignore[arg-type]
    assert bridge.resolved == []
    assert not state.confirming_global
    assert state.refusal is not None and state.refusal.asked == {"p1", "p2", "p3"}

    state.body_shown("inbox", "p3", 0, frame=3)
    assert review.approve_everything(bridge, snap, state) == 3  # type: ignore[arg-type]
    assert [pid for pid, _ in bridge.resolved] == ["p1", "p2", "p3"]


def test_the_batch_forms_send_calls_no_pane_has_drawn(frame: None) -> None:
    """
    They approve what nobody opened, by design; only a body known short holds one.
    """
    bridge, _, _ = press(imgui.Key.a, SHIFT, state=review.ReviewState())

    assert [pid for pid, _ in bridge.resolved] == ["p1", "p2"]


def test_reject_and_edit_ask_nothing_of_the_body(frame: None) -> None:
    short = read_whole()
    short.body_shown("inbox", "p1", 40, frame=9)
    for state in (review.ReviewState(), short):
        assert review.withheld(state, queue().approvals[0]) is not None
        _, _, after = press(imgui.Key.r, state=state)
        assert after.focus_reason
        _, _, after = press(imgui.Key.e, state=state)
        assert after.editing == "p1"
        bridge = RecordingBridge()
        call = queue().approvals[0]
        assert review.resolve(bridge, state, call, approved=False)  # type: ignore[arg-type]
        assert [(pid, d.approved) for pid, d in bridge.resolved] == [("p1", False)]


def test_escape_takes_a_refusal_down_and_readings_leave_with_their_calls(frame: None) -> None:
    refused = read_whole()
    refused.refusal = review.Refusal(frozenset({"p1"}), review.NOT_WHOLE)

    _, _, state = press(imgui.Key.escape, state=refused)
    assert state.refusal is None

    state.refusal = review.Refusal(frozenset({"p1"}), review.NOT_WHOLE)
    state.prune({"p2"})
    assert state.bodies.keys() == {"p2"}
    assert state.refusal is None
