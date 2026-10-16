"""
DETAIL acts where NEEDS YOU is not on screen (planning/2026-10-15-ui-rework.md,
"DETAIL acts in FOCUS only"): in each layout there is exactly one place to act
on a parked item, and the same code acts in both.

Drawn in real Dear ImGui frames with no backend, in the order the application
draws a frame: the cursor settled, the keys read, the queue drawn -- in TRIAGE,
and then told it drew, as ``app._reviewing`` tells it -- and DETAIL. FOCUS is
the same frame with no queue. Every button and small button is recorded by the
pane it was drawn in and clicked where it was drawn.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui as real_imgui

from pptmstr.command import CommandState
from pptmstr.intents import (
    ApprovalRequested,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
)
from pptmstr.model import MAX_DECISION_TEXT_CHARS, Snapshot
from pptmstr.store import Store
from pptmstr.ui import (
    command_view,
    compose,
    decision_view,
    detail,
    inbox,
    review,
    widgets,
)
from pptmstr.ui.command_view import Actions
from pptmstr.ui.focus import FocusState
from tests import test_command_rows as cr
from tests import test_decision_view as dv
from tests.test_inbox_rail import approval

DETAIL, QUEUE = "detail", "needs you"


class _Bridge:
    """What the panes hand the bridge: the approvals resolved and the intents emitted."""

    def __init__(self) -> None:
        self.resolved: list[tuple[str, Any]] = []
        self.emitted: list[Any] = []

    def resolve(self, pending_id: str, decision: Any) -> None:
        self.resolved.append((pending_id, decision))

    def emit(self, intent: Any) -> None:
        self.emitted.append(intent)


class Desk:
    """The queue and DETAIL, or DETAIL alone, a frame at a time."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, *, triage: bool) -> None:
        monkeypatch.setattr("pptmstr.settings.config_dir", lambda: tmp_path)
        monkeypatch.setattr("pptmstr.ui.typeset.enabled", lambda pane=None: False)
        self.triage = triage
        self.store: Store = cr.team()
        self.focus = FocusState()
        self.review = review.ReviewState()
        self.compose = compose.ComposeState()
        self.decisions = decision_view.DecisionState()
        self.detail = detail.DetailState()
        self.bridge = _Bridge()
        self.ran: list[tuple[object, ...]] = []
        self.refused: list[tuple[object, ...]] = []
        self.stopped: list[object] = []
        self.rows = cr.rows(
            wired=True,
            actions=Actions(
                on_run=lambda *a: self.ran.append(a),
                on_refuse=lambda *a: self.refused.append(a),
                on_stop=self.stopped.append,
            ),
        )
        # Where each label was drawn in the last frame, by the pane that drew it.
        self.at: dict[tuple[str, str], tuple[float, float]] = {}
        self.texts: dict[str, list[str]] = {DETAIL: [], QUEUE: []}
        self.pane = DETAIL
        self._context = real_imgui.create_context()
        self.io = real_imgui.get_io()
        self.io.display_size = real_imgui.ImVec2(2400.0, 2000.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= real_imgui.BackendFlags_.renderer_has_textures
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        desk = self

        def centre() -> tuple[float, float]:
            low, high = real_imgui.get_item_rect_min(), real_imgui.get_item_rect_max()
            return (low.x + high.x) / 2.0, (low.y + high.y) / 2.0

        class Recording:
            def __getattr__(self, name: str) -> object:
                return getattr(real_imgui, name)

            def button(self, label: str, *args: Any) -> bool:
                pressed = real_imgui.button(label, *args)
                desk.at.setdefault((desk.pane, label.split("##")[0]), centre())
                return bool(pressed)

            def text_disabled(self, text: str) -> None:
                desk.texts[desk.pane].append(text)
                real_imgui.text_disabled(text)

        real_small = widgets.small_button

        def small_button(label: str, *args: Any) -> bool:
            pressed = real_small(label, *args)
            desk.at.setdefault((desk.pane, label.split("##")[0]), centre())
            return bool(pressed)

        for module in (inbox, detail, command_view, decision_view):
            monkeypatch.setattr(module, "imgui", Recording())
        monkeypatch.setattr(widgets, "small_button", small_button)

    def close(self) -> None:
        real_imgui.destroy_context(self._context)

    @property
    def snap(self) -> Snapshot:
        return self.store.snapshot()

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            snap = self.snap
            self.focus.settle(snap)
            review.handle_keys(snap, self.focus, self.review, self.bridge)  # type: ignore[arg-type]
            self.at.clear()
            for said in self.texts.values():
                said.clear()
            real_imgui.new_frame()
            if self.triage:
                self.pane = QUEUE
                real_imgui.set_next_window_pos(real_imgui.ImVec2(0.0, 0.0))
                real_imgui.set_next_window_size(real_imgui.ImVec2(1200.0, 1900.0))
                real_imgui.begin(QUEUE)
                inbox.draw(
                    snap,
                    self.focus,
                    self.review,
                    self.compose,
                    self.bridge,  # type: ignore[arg-type]
                    MagicMock(),
                    100.0,
                    decisions=self.decisions,
                    commands=self.rows,
                )
                real_imgui.end()
                # What ``app._reviewing`` says of every draw of the queue.
                self.review.pane_drew(review.INBOX, int(real_imgui.get_frame_count()), True)
            self.pane = DETAIL
            real_imgui.set_next_window_pos(real_imgui.ImVec2(1200.0, 0.0))
            real_imgui.set_next_window_size(real_imgui.ImVec2(1200.0, 1900.0))
            real_imgui.begin(DETAIL)
            detail.draw(
                snap,
                self.focus,
                self.review,
                self.detail,
                100.0,
                self.decisions,
                self.rows,
                self.bridge,  # type: ignore[arg-type]
            )
            real_imgui.end()
            self.review.pane_drew(review.DETAIL, int(real_imgui.get_frame_count()), True)
            real_imgui.end_frame()

    def click(self, pane: str, label: str) -> None:
        assert (pane, label) in self.at, (pane, label, sorted(self.at))
        self.io.add_mouse_pos_event(*self.at[(pane, label)])
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(3)

    def key(self, key: real_imgui.Key) -> None:
        self.io.add_key_event(key, True)
        self.frame(2)
        self.io.add_key_event(key, False)
        self.frame(2)

    def wait(self) -> None:
        """Long enough for a press held back by ``ARM_DELAY_S`` to answer."""
        self.frame(int(command_view.ARM_DELAY_S * 60) + 6)

    def labels(self, pane: str) -> set[str]:
        return {label for where, label in self.at if where == pane}


@pytest.fixture
def focus_desk(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[Desk]:
    desk = Desk(monkeypatch, tmp_path, triage=False)
    try:
        yield desk
    finally:
        desk.close()


@pytest.fixture
def triage_desk(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[Desk]:
    desk = Desk(monkeypatch, tmp_path, triage=True)
    try:
        yield desk
    finally:
        desk.close()


def _parked(desk: Desk, pid: str = "p1") -> None:
    desk.store.apply(ApprovalRequested(cr.ROOT, approval(cr.ROOT, pid).approval), now=1.0)
    desk.frame(3)


def _decided(desk: Desk) -> None:
    asked = dv.decision("d-1", asker=cr.CHILD, blocks=())
    desk.store.apply(DecisionRaised(cr.CHILD, asked), now=2.0)
    desk.frame(3)


ACTING = {"approve", "reject", "edit", detail.OPEN_COMMAND, command_view.RUN, decision_view.SEND}


# -- which pane acts: the queue's last draw, not the layout's name ---------------------


def test_the_queue_is_on_screen_when_it_drew_this_frame_or_the_one_before() -> None:
    state = review.ReviewState()
    assert not review.queue_on_screen(state, 10)
    state.pane_drew(review.INBOX, 10, True)
    assert review.queue_on_screen(state, 10) and review.queue_on_screen(state, 11)
    assert not review.queue_on_screen(state, 12)
    # DETAIL's own draw says nothing about the queue.
    state.pane_drew(review.DETAIL, 12, True)
    assert not review.queue_on_screen(state, 12)


def test_detail_with_nothing_to_act_through_never_acts(focus_desk: Desk) -> None:
    _parked(focus_desk)
    real = detail.draw
    focus_desk.frame()
    assert "approve" in focus_desk.labels(DETAIL)
    focus_desk.bridge = None  # type: ignore[assignment]
    focus_desk.frame(2)
    assert not focus_desk.labels(DETAIL) & ACTING
    assert real is detail.draw


# -- FOCUS: DETAIL is the place to act ----------------------------------------------


def test_in_focus_an_approval_is_approved_from_detail(focus_desk: Desk) -> None:
    _parked(focus_desk)
    assert {"approve", "reject", "edit"} <= focus_desk.labels(DETAIL)
    focus_desk.click(DETAIL, "approve")
    ((pid, decision),) = focus_desk.bridge.resolved
    assert pid == "p1" and decision.approved and decision.edited_args is None


def test_in_focus_an_approval_is_rejected_from_detail_with_its_reason(focus_desk: Desk) -> None:
    _parked(focus_desk)
    focus_desk.review.reasons["p1"] = "not this file"
    focus_desk.click(DETAIL, "reject")
    ((pid, decision),) = focus_desk.bridge.resolved
    assert pid == "p1" and not decision.approved and decision.reason == "not this file"


def test_in_focus_edit_opens_the_editor_in_detail_and_approve_sends_the_edit(
    focus_desk: Desk, monkeypatch: pytest.MonkeyPatch
) -> None:
    edited: list[str] = []
    real = inbox.editor

    def editor(state: Any, pending: Any, **kw: Any) -> None:
        edited.append(pending.id)
        real(state, pending, **kw)

    monkeypatch.setattr(inbox, "editor", editor)
    _parked(focus_desk)
    focus_desk.click(DETAIL, "edit")
    assert focus_desk.review.editing == "p1" and edited
    assert "cancel edit" in focus_desk.labels(DETAIL)
    # The editor is what approve is answered from now, read on every frame it is drawn.
    focus_desk.frame()
    drawn, unset = focus_desk.review.bodies["p1"][review.DETAIL]
    assert drawn == real_imgui.get_frame_count() and unset == 0
    focus_desk.review.edits["p1"] = '{"file_path": "/tmp/y"}'
    focus_desk.click(DETAIL, "approve")
    ((_, decision),) = focus_desk.bridge.resolved
    assert decision.approved and decision.edited_args == {"file_path": "/tmp/y"}


def test_in_focus_the_e_key_opens_the_editor_in_detail(focus_desk: Desk) -> None:
    _parked(focus_desk)
    focus_desk.key(real_imgui.Key.e)
    assert focus_desk.review.editing == "p1"
    assert "cancel edit" in focus_desk.labels(DETAIL)


def test_in_focus_the_approve_guard_still_withholds_a_body_not_drawn_whole(
    focus_desk: Desk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lines of DETAIL's body left unset hold approve, by the button and by ``a``."""
    monkeypatch.setattr(detail, "_arguments", lambda pane, pending, key: 7)
    _parked(focus_desk)
    focus_desk.click(DETAIL, "approve")
    focus_desk.key(real_imgui.Key.a)
    assert focus_desk.bridge.resolved == []
    assert focus_desk.review.refusal is not None
    assert focus_desk.review.refusal.text == review.NOT_WHOLE


def test_in_focus_a_command_is_opened_run_and_its_run_confirmed_from_detail(
    focus_desk: Desk,
) -> None:
    cr.request(focus_desk.store, 1, now=5.0, text="make install")
    focus_desk.frame(3)
    assert command_view.RUN not in focus_desk.labels(DETAIL), "closed until opened"
    focus_desk.click(DETAIL, detail.OPEN_COMMAND)
    assert focus_desk.rows.state.is_open(cr.cid(1))
    focus_desk.wait()
    focus_desk.click(DETAIL, command_view.RUN)
    assert focus_desk.ran == [], "the first press arms and runs nothing"
    focus_desk.wait()
    focus_desk.click(DETAIL, command_view.CONFIRM_RUN)
    ((command_id, text, _),) = focus_desk.ran
    assert command_id == cr.cid(1) and text == "make install"


def test_in_focus_a_command_is_refused_from_detail(focus_desk: Desk) -> None:
    cr.request(focus_desk.store, 1, now=5.0)
    focus_desk.frame(3)
    focus_desk.click(DETAIL, detail.OPEN_COMMAND)
    focus_desk.rows.state.reasons[cr.cid(1)] = "not on this machine"
    focus_desk.wait()
    focus_desk.click(DETAIL, command_view.REFUSE)
    assert focus_desk.refused == [(cr.cid(1), "not on this machine")]


def test_in_focus_a_running_command_is_cancelled_from_detail_with_a_second_press(
    focus_desk: Desk,
) -> None:
    cr.request(focus_desk.store, 1, now=5.0)
    cr.approve(focus_desk.store, 1, now=6.0)
    assert focus_desk.snap.commands[cr.cid(1)].state is CommandState.RUNNING
    focus_desk.frame(3)
    focus_desk.click(DETAIL, detail.OPEN_COMMAND)
    focus_desk.wait()
    focus_desk.click(DETAIL, command_view.CANCEL)
    assert focus_desk.stopped == []
    focus_desk.wait()
    focus_desk.click(DETAIL, command_view.CONFIRM_STOP)
    assert focus_desk.stopped == [cr.cid(1)]


def test_in_focus_a_decision_is_answered_from_detail_after_its_confirm(focus_desk: Desk) -> None:
    _decided(focus_desk)
    focus_desk.decisions.drafts["d-1"] = "keep it"
    focus_desk.frame()
    focus_desk.click(DETAIL, decision_view.SEND)
    assert focus_desk.bridge.emitted == [], "the first press only asks to confirm"
    focus_desk.click(DETAIL, decision_view.CONFIRM[decision_view.Act.SEND])
    assert focus_desk.bridge.emitted == [DecisionAnswered(decision_id="d-1", answer="keep it")]


def test_in_focus_a_decision_is_dismissed_from_detail_after_its_confirm(focus_desk: Desk) -> None:
    _decided(focus_desk)
    focus_desk.click(DETAIL, decision_view.DISMISS)
    focus_desk.click(DETAIL, decision_view.CONFIRM[decision_view.Act.DISMISS])
    assert focus_desk.bridge.emitted == [DecisionDismissed(decision_id="d-1")]


def test_in_focus_the_lines_that_send_the_operator_to_the_row_are_gone(focus_desk: Desk) -> None:
    cr.request(focus_desk.store, 1, now=5.0)
    focus_desk.frame(3)
    assert detail.COMMAND_FROM_ROW not in focus_desk.texts[DETAIL]
    _decided(focus_desk)
    focus_desk.focus.to_obligation(
        next(o for o in focus_desk.snap.needs_you if o.key == "decision:d-1")
    )
    focus_desk.frame(3)
    assert decision_view.ANSWER_FROM_ROW not in focus_desk.texts[DETAIL]


# -- TRIAGE: the queue's row acts, and DETAIL does not ----------------------------------


def test_in_triage_detail_has_no_control_and_says_where_they_are(triage_desk: Desk) -> None:
    _parked(triage_desk)
    assert not triage_desk.labels(DETAIL) & ACTING
    assert "approve" in triage_desk.labels(QUEUE)

    cr.request(triage_desk.store, 1, now=5.0)
    triage_desk.focus.to_obligation(
        next(o for o in triage_desk.snap.needs_you if o.key == f"command:{cr.cid(1)}")
    )
    triage_desk.frame(3)
    assert detail.COMMAND_FROM_ROW in triage_desk.texts[DETAIL]
    assert not triage_desk.labels(DETAIL) & ACTING

    _decided(triage_desk)
    triage_desk.focus.to_obligation(
        next(o for o in triage_desk.snap.needs_you if o.key == "decision:d-1")
    )
    triage_desk.frame(3)
    assert decision_view.ANSWER_FROM_ROW in triage_desk.texts[DETAIL]
    assert not triage_desk.labels(DETAIL) & ACTING
    assert decision_view.SEND in triage_desk.labels(QUEUE)


def test_in_triage_the_queues_row_still_approves(triage_desk: Desk) -> None:
    _parked(triage_desk)
    triage_desk.click(QUEUE, "approve")
    ((pid, decision),) = triage_desk.bridge.resolved
    assert pid == "p1" and decision.approved


def test_closing_the_queue_hands_the_controls_to_detail_a_frame_later(triage_desk: Desk) -> None:
    """One place to act at every frame, whichever pane it is."""
    _parked(triage_desk)
    triage_desk.triage = False
    triage_desk.frame()
    assert not triage_desk.labels(DETAIL) & ACTING, "the queue drew the frame before"
    triage_desk.frame()
    assert "approve" in triage_desk.labels(DETAIL)


# -- one function, called by both panes ------------------------------------------------


@pytest.mark.parametrize("triage", [True, False], ids=["triage", "focus"])
def test_the_controls_are_one_function_whichever_pane_draws_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, triage: bool
) -> None:
    desk = Desk(monkeypatch, tmp_path, triage=triage)
    calls: list[tuple[str, str]] = []
    for module, name in (
        (inbox, "approval_controls"),
        (decision_view, "draw_answer_box"),
        (inbox, "attend"),
    ):
        real = getattr(module, name)

        def spy(*args: Any, _real: Any = real, _name: str = name, **kw: Any) -> Any:
            calls.append((desk.pane, _name))
            return _real(*args, **kw)

        monkeypatch.setattr(module, name, spy)
    try:
        _parked(desk)
        _decided(desk)
        desk.focus.to_obligation(next(o for o in desk.snap.needs_you if o.key == "decision:d-1"))
        desk.frame(2)
        desk.focus.to_obligation(next(o for o in desk.snap.needs_you if o.key == "approval:p1"))
        desk.frame(2)
    finally:
        desk.close()
    where = QUEUE if triage else DETAIL
    assert {pane for pane, _ in calls} == {where}, "one pane acts"
    assert {name for _, name in calls} == {"approval_controls", "draw_answer_box", "attend"}
    # Once a frame, by the pane that acts.
    assert calls.count((where, "attend")) == 3 + 3 + 2 + 2


def test_detail_carries_no_copy_of_the_rows_controls() -> None:
    """Read off DETAIL's source: it calls the row's functions and draws none of their buttons."""
    source = inspect.getsource(detail)
    assert "inbox.approval_controls(" in source and "inbox.editor(" in source
    for label in ('"approve"', '"reject"', "SEND", "CONFIRM_RUN", "CONFIRM_STOP"):
        assert label not in source, label


def test_a_decision_is_drawn_whole_in_detail_before_it_is_answered_there() -> None:
    assert decision_view.DETAIL_CHARS >= MAX_DECISION_TEXT_CHARS
