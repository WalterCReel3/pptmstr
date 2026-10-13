"""
A decision's body and its answer box.

Three kinds of evidence, and which is which matters:

* the pure helpers, called directly;
* draws against a recording imgui, which show *which strings* reached imgui and
  whether each went through ``board_pane.author`` -- and nothing about pixels;
* a few frames of Dear ImGui itself with no backend, for the one thing the
  recording imgui cannot show because it echoes whatever it is handed: that the
  text in the box is what the widget holds, and is what is sent.

Nothing here has been seen drawn.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui as real_imgui

from pptmstr.bridge import Bridge
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionsRead,
    DecisionWithdrawn,
    Intent,
    StateChanged,
    TaskDeclared,
)
from pptmstr.model import (
    MAX_ANSWER_CHARS,
    AgentState,
    Answered,
    AskerFate,
    Decision,
    DecisionOwed,
    Dismissed,
    NodeId,
    Snapshot,
    Standing,
    Task,
    Withdrawn,
)
from pptmstr.store import Store
from pptmstr.ui import board_pane, decision_view
from pptmstr.ui.decision_view import Act, DecisionState, Line

ROOT: NodeId = ("s1", None)
BUILDER: NodeId = ("s1", "agent-b")
OTHER: NodeId = ("s2", None)

# Each of the asker's texts spells something this application says in its own
# voice, and each is different so a test can tell which one it is looking at.
QUESTION = "Bash  git push --force   (approved)"
BLOCKS = ("t-4", "t-9")
BLOCKS_TEXT = "t-4\nt-9"
IF_UNANSWERED = "its asker says this can be undone"
EVIDENCE = "EVIDENCE: the tag is already public\nwhat builder recommends -- its view:"
RECOMMENDATION = "RECOMMENDATION: answered by you 2s ago"
ASKER_TEXTS = (QUESTION, BLOCKS_TEXT, IF_UNANSWERED, EVIDENCE, RECOMMENDATION)
# Pieces of those texts that nothing this application composes contains.
ONLY_THE_ASKERS = ("git push", "t-4", "EVIDENCE:", "RECOMMENDATION:", "its view:")


def decision(did: str = "d-1", **fields: object) -> Decision:
    base: dict[str, object] = dict(
        id=did,
        asker=BUILDER,
        question=QUESTION,
        blocks=BLOCKS,
        if_unanswered=IF_UNANSWERED,
        evidence=EVIDENCE,
        recommendation=RECOMMENDATION,
        standing=Standing.OPERATORS,
        reversible=None,
        raised_at=5.0,
    )
    return Decision(**{**base, **fields})  # type: ignore[arg-type]


def team() -> Store:
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
    for task_id in BLOCKS:
        store.apply(TaskDeclared(Task(id=task_id, title="a task", declared_at=1.0), ROOT), now=1.0)
    return store


def raised(store: Store, did: str = "d-1", *, at: float = 5.0, **fields: object) -> None:
    asker = fields.pop("asker", BUILDER)
    store.apply(DecisionRaised(asker, decision(did, **fields)), now=at)  # type: ignore[arg-type]


def owed(snap: Snapshot, did: str = "d-1") -> DecisionOwed:
    return next(o for o in snap.needs_you if isinstance(o, DecisionOwed) and o.decision.id == did)


# -- whose decision, and whether it undoes ------------------------------------------


def test_a_decision_nobody_has_taken_and_one_already_taken_are_never_worded_alike() -> None:
    yours = decision_view.standing_line(Standing.OPERATORS, "builder")
    taken = decision_view.standing_line(Standing.TAKEN, "builder")

    assert yours == Line("yours to decide: nobody has taken it")
    assert taken == Line("already taken by builder, who asks you to ratify it", warn=True)


def test_every_standing_has_a_line() -> None:
    for standing in Standing:
        assert decision_view.standing_line(standing, "lead").text


@pytest.mark.parametrize(
    ("reversible", "text", "warn"),
    [
        (True, "its asker says this can be undone", False),
        (False, "its asker says this cannot be undone", True),
        (None, "its asker did not say whether this can be undone", True),
    ],
)
def test_reversibility_is_three_statements_and_unstated_is_not_the_reassuring_one(
    reversible: bool | None, text: str, warn: bool
) -> None:
    assert decision_view.reversible_line(reversible) == Line(text, warn)


def test_only_an_asker_that_is_running_gets_no_line_about_who_will_read_the_answer() -> None:
    assert decision_view.fate_line(AskerFate.LIVE) is None
    lines = {
        fate: decision_view.fate_line(fate) for fate in AskerFate if fate is not AskerFate.LIVE
    }

    assert all(line is not None and line.warn for line in lines.values())
    # A different thing to do next for each, so a different sentence for each.
    assert len({line.text for line in lines.values() if line is not None}) == len(lines)
    gone = lines[AskerFate.GONE]
    assert gone is not None and "dismiss" in gone.text


def test_the_facts_above_the_body_are_structure_and_hold_none_of_the_askers_text() -> None:
    store = team()
    raised(store, standing=Standing.TAKEN, reversible=False)
    snap = store.snapshot()

    lines = decision_view.facts(owed(snap), "builder")

    assert [line.text for line in lines] == [
        "already taken by builder, who asks you to ratify it",
        "its asker says this cannot be undone",
        "its asker recommends an answer: the last block below",
    ]
    assert not any(text in line.text for line in lines for text in ASKER_TEXTS)


def test_a_stopped_askers_decision_says_so_among_its_facts() -> None:
    store = team()
    raised(store)
    store.apply(AgentFinished(BUILDER, AgentState.DONE, ended_at=7.0), now=7.0)
    row = owed(store.snapshot())

    assert row.asker is AskerFate.STOPPED
    assert decision_view.fate_line(AskerFate.STOPPED) in decision_view.facts(row, "builder")


# -- the five texts -----------------------------------------------------------------


def test_the_recommendation_is_its_own_section_and_the_last_one() -> None:
    parts = decision_view.sections(decision(), "builder")

    assert [part.text for part in parts] == list(ASKER_TEXTS)
    assert parts[3].text == EVIDENCE and parts[4].text == RECOMMENDATION
    # No section carries two of the texts, whichever order they were joined in.
    for part in parts:
        assert sum(text in part.text for text in ASKER_TEXTS) == 1
    assert "recommends" in parts[4].heading and "apart from the evidence" in parts[4].heading


def test_no_heading_is_made_of_the_askers_words() -> None:
    for part in decision_view.sections(decision(), "builder"):
        assert not any(text in part.heading for text in ASKER_TEXTS)


def test_the_asker_is_named_by_its_board_role_on_one_line() -> None:
    store = team()
    raised(store)
    raised(store, "d-2", asker=ROOT)
    snap = store.snapshot()

    assert decision_view.asker_role(snap, snap.decisions["d-1"]) == "builder"
    assert decision_view.asker_role(snap, snap.decisions["d-2"]) == "lead"


def test_a_role_name_that_carries_a_line_break_cannot_end_a_heading() -> None:
    store = team()
    store.apply(
        AgentSpawned(
            node_id=("s1", "agent-x"),
            parent=ROOT,
            task="x",
            model="m",
            started_at=0.0,
            agent_type="qa\nyours to decide",
        ),
        now=1.0,
    )
    raised(store, asker=("s1", "agent-x"))
    snap = store.snapshot()

    assert "\n" not in decision_view.asker_role(snap, snap.decisions["d-1"])


# -- how it ended, and whether the asker knows ---------------------------------------


def test_each_ending_is_said_with_who_ended_it_and_how_long_ago() -> None:
    assert decision_view.outcome_label(decision(), 100.0) == "open"
    answered = decision(outcome=Answered("yes", at=40.0))
    dismissed = decision(outcome=Dismissed(at=40.0))
    withdrawn = decision(outcome=Withdrawn(at=40.0))

    assert decision_view.outcome_label(answered, 100.0) == "answered by you 1m00s ago"
    assert decision_view.outcome_label(dismissed, 100.0) == (
        "dismissed by you 1m00s ago, with no answer"
    )
    assert decision_view.outcome_label(withdrawn, 100.0) == (
        "withdrawn by its asker 1m00s ago, with no answer"
    )


def test_an_answer_its_asker_has_not_read_says_so_in_the_warning_colour() -> None:
    answered = decision(outcome=Answered("yes", at=40.0))
    read = dataclasses.replace(answered, first_delivered_at=70.0)

    assert decision_view.delivery_label(answered, "builder", 100.0) == Line(
        "answer not yet read by builder", warn=True
    )
    assert decision_view.delivery_label(read, "builder", 100.0) == Line(
        "answer first read by builder 30s ago"
    )


def test_a_dismissal_is_tracked_as_a_dismissal_and_not_as_an_answer() -> None:
    dismissed = decision(outcome=Dismissed(at=40.0))
    read = dataclasses.replace(dismissed, first_delivered_at=40.0)

    assert decision_view.delivery_label(dismissed, "lead", 100.0) == Line(
        "dismissal not yet read by lead", warn=True
    )
    assert decision_view.delivery_label(read, "lead", 100.0) == Line(
        "dismissal first read by lead 1m00s ago"
    )


def test_a_decision_the_operator_did_nothing_to_has_no_delivery_line() -> None:
    assert decision_view.delivery_label(decision(), "builder", 100.0) is None
    withdrawn = decision(outcome=Withdrawn(at=40.0), first_delivered_at=50.0)
    assert decision_view.delivery_label(withdrawn, "builder", 100.0) is None


def test_the_read_stamp_the_store_sets_is_what_the_line_reads() -> None:
    """Against the reducer and not a hand-built record: the asker's read is the stamp."""
    store = team()
    raised(store)
    store.apply(DecisionAnswered("d-1", "yes"), now=10.0)
    before = store.snapshot().decisions["d-1"]
    # By id: a read that names the decision is the one reply its answer is in.
    store.apply(DecisionsRead(BUILDER, "req-1", at=25.0, decision_id="d-1"), now=25.0)
    after = store.snapshot().decisions["d-1"]

    assert decision_view.delivery_label(before, "builder", 30.0) == Line(
        "answer not yet read by builder", warn=True
    )
    assert decision_view.delivery_label(after, "builder", 30.0) == Line(
        "answer first read by builder 5s ago"
    )


def test_closed_decisions_are_one_sessions_ended_ones_newest_ending_first() -> None:
    store = team()
    raised(store, "d-1")
    raised(store, "d-2")
    raised(store, "d-3")
    raised(store, "d-open")
    raised(store, "d-other", asker=OTHER, blocks=())
    store.apply(DecisionAnswered("d-2", "yes"), now=10.0)
    store.apply(DecisionWithdrawn(BUILDER, "d-1"), now=20.0)
    store.apply(DecisionDismissed("d-3"), now=15.0)
    store.apply(DecisionAnswered("d-other", "no"), now=30.0)
    snap = store.snapshot()

    assert [d.id for d in decision_view.closed(snap, "s1")] == ["d-1", "d-3", "d-2"]
    assert [d.id for d in decision_view.closed(snap, "s2")] == ["d-other"]
    assert decision_view.closed(snap, "nobody") == ()
    assert decision_view.closed_heading(3) == "decisions closed in this session (3)"
    assert decision_view.closed_label(snap.decisions["d-2"], "builder", 70.0) == (
        "d-2 from builder: answered by you 1m00s ago"
    )


# -- what may be sent -----------------------------------------------------------------


@pytest.mark.parametrize("draft", ["", " ", "\n\n", "\t \n"])
def test_a_draft_with_nothing_to_read_in_it_is_not_sendable(draft: str) -> None:
    assert not decision_view.sendable(draft)


@pytest.mark.parametrize("draft", ["no", "  yes  ", "\nno\n", "0"])
def test_a_draft_with_anything_to_read_in_it_is_sendable(draft: str) -> None:
    assert decision_view.sendable(draft)


def test_the_second_press_is_told_what_it_does_and_that_it_is_final() -> None:
    send = decision_view.confirm_line(Act.SEND, "builder", "  yes ", AskerFate.LIVE)
    assert "6 characters" in send and "builder" in send and "cannot change" in send
    # The draft is counted and never quoted: the box above it is the quotation.
    assert "yes" not in send

    dead = decision_view.confirm_line(Act.SEND, "builder", "yes", AskerFate.STOPPED)
    stopped = decision_view.fate_line(AskerFate.STOPPED)
    assert stopped is not None and stopped.text in dead

    bare = decision_view.confirm_line(Act.DISMISS, "builder", "", AskerFate.LIVE)
    held = decision_view.confirm_line(Act.DISMISS, "builder", "half an answer", AskerFate.LIVE)
    assert "without an answer" in bare and "cannot be reopened" in bare
    assert "discarded" not in bare and "14 characters) is discarded" in held


def test_both_acts_have_a_second_press_label() -> None:
    assert set(decision_view.CONFIRM) == set(Act)


# -- the clipboard --------------------------------------------------------------------


def test_the_clipboard_marks_every_line_the_asker_wrote_and_none_of_its_own() -> None:
    store = team()
    raised(store, evidence="", recommendation="one\x00two")
    snap = store.snapshot()

    lines = decision_view.plain_text(snap, owed(snap))

    for text in (QUESTION, *BLOCKS, IF_UNANSWERED):
        assert f"| {text}" in lines
    assert "| one<NUL>two" in lines
    # The operator's mark is on no line of a decision nobody has answered.
    assert not any(line.startswith("> ") for line in lines)
    own = [line for line in lines if not line.startswith("| ")]
    assert not any(mark in line for line in own for mark in ONLY_THE_ASKERS)
    # The line an asker imitated is there once as the application's, unmarked.
    assert own.count("yours to decide: nobody has taken it") == 1
    assert "the evidence builder gives:" in own
    assert own[own.index("the evidence builder gives:") + 1] == decision_view.NOTHING_WRITTEN


# -- drafts ---------------------------------------------------------------------------


def test_a_draft_is_there_when_the_cursor_comes_back_to_its_row() -> None:
    store = team()
    raised(store, "d-1")
    raised(store, "d-2")
    snap = store.snapshot()
    state = DecisionState(drafts={"d-1": "  half an answer\n", "d-2": "another"})

    for focused in ("d-2", None, "d-1", "d-2"):
        state.attend(snap, focused)

    assert state.drafts == {"d-1": "  half an answer\n", "d-2": "another"}


def test_a_draft_goes_when_the_store_shows_the_decision_answered_or_dismissed() -> None:
    store = team()
    raised(store, "d-1")
    raised(store, "d-2")
    raised(store, "d-3")
    state = DecisionState(drafts={"d-1": "yes", "d-2": "unsent", "d-3": "kept", "d-gone": "x"})
    store.apply(DecisionAnswered("d-1", "yes"), now=9.0)
    store.apply(DecisionDismissed("d-2"), now=9.0)

    state.attend(store.snapshot(), None)

    assert state.drafts == {"d-3": "kept"}


def test_a_draft_outlives_a_decision_its_asker_withdrew() -> None:
    """The operator did not choose that ending, and the text is theirs."""
    store = team()
    raised(store)
    state = DecisionState(drafts={"d-1": "a long considered answer"})
    store.apply(DecisionWithdrawn(BUILDER, "d-1"), now=9.0)

    state.attend(store.snapshot(), None)
    state.attend(store.snapshot(), None)

    assert state.drafts == {"d-1": "a long considered answer"}


def test_an_act_waiting_for_its_second_press_is_dropped_when_the_cursor_leaves_its_row() -> None:
    store = team()
    raised(store, "d-1")
    raised(store, "d-2")
    snap = store.snapshot()
    state = DecisionState(confirming=("d-1", Act.SEND))

    state.attend(snap, "d-1")
    assert state.pending("d-1") is Act.SEND and state.pending("d-2") is None

    state.attend(snap, "d-2")
    assert state.confirming is None


def test_an_act_waiting_on_a_decision_that_has_ended_is_dropped() -> None:
    store = team()
    raised(store)
    state = DecisionState(confirming=("d-1", Act.DISMISS))
    store.apply(DecisionWithdrawn(BUILDER, "d-1"), now=9.0)

    state.attend(store.snapshot(), "d-1")

    assert state.confirming is None


ASKED_SHUT = frozenset({"everything its asker wrote"})

# -- draws against a recording imgui --------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Said:
    text: str
    # Drawn by ``board_pane.author``, which is the bar; False is the application.
    quoted: bool
    how: str = "text"


class Screen:
    """
    A recording imgui under ``decision_view`` and ``board_pane``.

    ``press`` is the buttons that report a click this frame. ``typed`` is what
    the answer box returns in place of the value it was handed; None echoes.
    ``shut`` is the triangles that stay closed while ``tree_open`` opens the rest.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        press: frozenset[str] = frozenset(),
        typed: str | None = None,
        tree_open: bool = True,
        shut: frozenset[str] = frozenset(),
    ) -> None:
        self.said: list[Said] = []
        self.clipboard: list[str] = []
        self.boxes: list[tuple[str, str, int]] = []
        self.press = press
        self.typed = typed
        inside = [False]
        real_author = board_pane.author

        def author(*args: object, **kwargs: object) -> None:
            inside[0] = True
            try:
                real_author(*args, **kwargs)  # type: ignore[arg-type]
            finally:
                inside[0] = False

        def record(position: int, how: str = "text"):  # type: ignore[no-untyped-def]
            def call(*args: object, **kwargs: object) -> MagicMock:
                self.said.append(Said(str(args[position]), inside[0], how))
                return MagicMock()

            return call

        def button(label: str, *args: object, **kwargs: object) -> bool:
            self.said.append(Said(label, inside[0], "button"))
            return label in self.press

        def node(label: str, *args: object, **kwargs: object) -> bool:
            shown = label.split("###")[0]
            self.said.append(Said(shown, inside[0], "node"))
            return tree_open and shown not in shut

        def box(label: str, value: str, size: object, *, wrap: bool, flags: int = 0):  # type: ignore[no-untyped-def]
            self.boxes.append((label, value, flags))
            return False, (value if self.typed is None else self.typed)

        fake = MagicMock()
        fake.ImVec2 = real_imgui.ImVec2
        fake.InputTextFlags_ = real_imgui.InputTextFlags_
        fake.ChildFlags_ = real_imgui.ChildFlags_
        fake.TreeNodeFlags_ = real_imgui.TreeNodeFlags_
        fake.text_colored.side_effect = record(1)
        fake.text_disabled.side_effect = record(0)
        fake.text.side_effect = record(0)
        fake.text_wrapped.side_effect = record(0)
        fake.separator.side_effect = lambda: self.said.append(Said("----", inside[0], "rule"))
        fake.button.side_effect = button
        fake.small_button.side_effect = button
        fake.tree_node_ex.side_effect = node
        fake.is_key_pressed.return_value = False
        fake.set_clipboard_text.side_effect = self.clipboard.append
        self.imgui = fake
        monkeypatch.setattr(decision_view, "imgui", fake)
        monkeypatch.setattr(board_pane, "imgui", fake)
        monkeypatch.setattr(board_pane, "author", author)
        monkeypatch.setattr(decision_view, "multiline_input", box)

    def own(self) -> list[str]:
        return [line.text for line in self.said if not line.quoted]

    def quoted(self) -> list[str]:
        return [line.text for line in self.said if line.quoted]


def _snap_with_one() -> tuple[Store, Snapshot]:
    store = team()
    raised(store)
    return store, store.snapshot()


def test_every_text_the_asker_wrote_is_drawn_behind_the_bar_and_nowhere_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snap = _snap_with_one()
    screen = Screen(monkeypatch)

    decision_view.draw_body(snap, owed(snap), limit=decision_view.DETAIL_CHARS, rest="elsewhere")

    assert screen.quoted() == list(ASKER_TEXTS)
    assert not any(mark in line for line in screen.own() for mark in ONLY_THE_ASKERS)
    # And the lines the texts were written to imitate are there, as the
    # application's own, exactly once each.
    assert screen.own().count("yours to decide: nobody has taken it") == 1
    assert screen.own().count("its asker did not say whether this can be undone") == 1


def test_the_recommendation_is_drawn_in_a_block_of_its_own_after_a_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snap = _snap_with_one()
    screen = Screen(monkeypatch)

    decision_view.draw_body(snap, owed(snap), limit=decision_view.DETAIL_CHARS, rest="elsewhere")

    texts = [line.text for line in screen.said]
    evidence, recommendation = texts.index(EVIDENCE), texts.index(RECOMMENDATION)
    between = screen.said[evidence + 1 : recommendation]
    # One draw call each, and between them a rule and a heading of the pane's.
    assert not any(RECOMMENDATION in line.text for line in screen.said[: evidence + 1])
    assert [line.how for line in between] == ["rule", "text"]
    assert not between[1].quoted and "recommends" in between[1].text
    # The only rule in the body is that one.
    assert [line.how for line in screen.said].count("rule") == 1


def test_a_text_the_asker_left_empty_is_said_to_be_empty_by_the_pane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store, recommendation="  \n", if_unanswered="")
    snap = store.snapshot()
    screen = Screen(monkeypatch)

    decision_view.draw_body(snap, owed(snap), limit=100, rest="elsewhere")

    assert screen.own().count(decision_view.NOTHING_WRITTEN) == 2
    assert screen.quoted() == [QUESTION, BLOCKS_TEXT, EVIDENCE]


def test_a_text_cut_at_the_bound_says_how_much_was_cut_and_where_the_rest_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store, evidence="e" * 130)
    snap = store.snapshot()
    screen = Screen(monkeypatch)

    decision_view.draw_body(snap, owed(snap), limit=100, rest="DETAIL shows more of it")

    assert "e" * 100 in screen.quoted() and "e" * 101 not in screen.quoted()
    assert "... 30 more characters not shown here - DETAIL shows more of it" in screen.own()


def test_a_nul_in_an_askers_text_is_drawn_and_does_not_end_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store, question="before\x00after")
    snap = store.snapshot()
    screen = Screen(monkeypatch)

    decision_view.draw_body(snap, owed(snap), limit=100, rest="elsewhere")

    assert "before<NUL>after" in screen.quoted()


def test_the_inbox_row_draws_the_body_and_then_the_answer_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snap = _snap_with_one()
    screen = Screen(monkeypatch)
    state = DecisionState(drafts={"d-1": "so far"})

    decision_view.draw_row(snap, state, Bridge(), owed(snap), wrap=False)

    assert screen.quoted() == list(ASKER_TEXTS)
    assert [(label, value) for label, value, _ in screen.boxes] == [("##answer", "so far")]
    labels = [line.text for line in screen.said if line.how == "button"]
    assert labels == [decision_view.SEND, decision_view.DISMISS]
    assert decision_view.FINAL_HINT in screen.own()


# -- the answer box, against the recording imgui --------------------------------------

# Leading and trailing blanks, a blank line, a tab, and trailing newlines: every
# one of them is something a tidy-up would remove.
AS_TYPED = "  yes, but\n\n\tnot on main \n\n"


class _Outbox:
    """The half of the Bridge the box uses, keeping what it was handed."""

    def __init__(self) -> None:
        self.sent: list[Intent] = []

    def emit(self, intent: Intent) -> None:
        self.sent.append(intent)


def _box(
    monkeypatch: pytest.MonkeyPatch,
    snap: Snapshot,
    state: DecisionState,
    outbox: _Outbox,
    *,
    press: frozenset[str] = frozenset(),
    typed: str | None = None,
) -> Screen:
    screen = Screen(monkeypatch, press=press, typed=typed)
    state.attend(snap, "d-1")
    decision_view.draw_answer_box(snap, state, outbox, owed(snap))  # type: ignore[arg-type]
    return screen


def test_what_is_typed_is_kept_as_typed_and_nothing_is_sent_by_typing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snap = _snap_with_one()
    state, outbox = DecisionState(), _Outbox()

    _box(monkeypatch, snap, state, outbox, typed=AS_TYPED)
    _box(monkeypatch, snap, state, outbox)
    _box(monkeypatch, snap, state, outbox)

    assert state.drafts["d-1"] == AS_TYPED
    assert outbox.sent == [] and state.confirming is None


def test_the_first_press_of_send_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _, snap = _snap_with_one()
    state, outbox = DecisionState(drafts={"d-1": AS_TYPED}), _Outbox()

    _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.SEND}))

    assert outbox.sent == []
    assert state.confirming == ("d-1", Act.SEND)


def test_the_second_press_sends_the_draft_byte_for_byte_as_the_operators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, snap = _snap_with_one()
    state, outbox = DecisionState(drafts={"d-1": AS_TYPED}), _Outbox()
    _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.SEND}))

    screen = _box(
        monkeypatch, snap, state, outbox, press=frozenset({decision_view.CONFIRM[Act.SEND]})
    )

    assert outbox.sent == [DecisionAnswered(decision_id="d-1", answer=AS_TYPED)]
    (intent,) = outbox.sent
    assert isinstance(intent, DecisionAnswered) and intent.node_id is None
    assert state.confirming is None
    # The second press is told what it does before it is made.
    assert decision_view.confirm_line(Act.SEND, "builder", AS_TYPED, AskerFate.LIVE) in screen.own()
    # The draft is not cleared here: the store clearing it is what says it landed.
    assert state.drafts["d-1"] == AS_TYPED

    # And through the reducer, the bytes are the record's.
    store.apply_all(outbox.sent, now=9.0)
    assert store.snapshot().decisions["d-1"].answer == AS_TYPED
    assert not store.snapshot().needs_you
    state.attend(store.snapshot(), None)
    assert state.drafts == {}


def test_while_the_second_press_is_awaited_the_box_is_read_only_and_cannot_change_the_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What is sent is what was on screen when the first press was made."""
    _, snap = _snap_with_one()
    state, outbox = DecisionState(drafts={"d-1": "the answer"}), _Outbox()
    open_box = _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.SEND}))

    held = _box(monkeypatch, snap, state, outbox, typed="something slipped in")
    sent = _box(
        monkeypatch,
        snap,
        state,
        outbox,
        typed="something slipped in",
        press=frozenset({decision_view.CONFIRM[Act.SEND]}),
    )

    read_only = int(real_imgui.InputTextFlags_.read_only)
    assert not open_box.boxes[0][2] & read_only
    assert held.boxes[0][2] & read_only and sent.boxes[0][2] & read_only
    assert state.drafts["d-1"] == "the answer"
    assert outbox.sent == [DecisionAnswered(decision_id="d-1", answer="the answer")]


def test_keep_editing_sends_nothing_and_leaves_the_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    _, snap = _snap_with_one()
    state, outbox = DecisionState(drafts={"d-1": AS_TYPED}), _Outbox()
    _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.SEND}))

    _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.BACK}))
    after = _box(monkeypatch, snap, state, outbox)

    assert outbox.sent == [] and state.confirming is None
    assert state.drafts["d-1"] == AS_TYPED
    assert not after.boxes[0][2] & int(real_imgui.InputTextFlags_.read_only)


@pytest.mark.parametrize("draft", ["", "   ", "\n \t\n"])
def test_an_empty_answer_is_not_sent_even_if_the_greyed_button_reports_a_click(
    monkeypatch: pytest.MonkeyPatch, draft: str
) -> None:
    _, snap = _snap_with_one()
    state, outbox = DecisionState(drafts={"d-1": draft}), _Outbox()

    screen = _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.SEND}))
    _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.CONFIRM[Act.SEND]}))

    assert outbox.sent == [] and state.confirming is None
    assert decision_view.EMPTY_HINT in screen.own()
    assert decision_view.FINAL_HINT not in screen.own()
    assert screen.imgui.begin_disabled.call_count == 1
    assert screen.imgui.end_disabled.call_count == 1
    # Judged on a copy: the draft itself is as it was.
    assert state.drafts["d-1"] == draft


def test_a_sendable_draft_is_told_an_answer_is_final_before_the_first_press(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snap = _snap_with_one()
    screen = _box(monkeypatch, snap, DecisionState(drafts={"d-1": "no"}), _Outbox())

    assert decision_view.FINAL_HINT in screen.own()
    assert screen.imgui.begin_disabled.call_count == 0


def test_dismiss_is_two_presses_and_closes_it_with_no_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, snap = _snap_with_one()
    state, outbox = DecisionState(drafts={"d-1": "half an answer"}), _Outbox()

    _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.DISMISS}))
    assert outbox.sent == [] and state.confirming == ("d-1", Act.DISMISS)

    screen = _box(
        monkeypatch, snap, state, outbox, press=frozenset({decision_view.CONFIRM[Act.DISMISS]})
    )

    assert outbox.sent == [DecisionDismissed(decision_id="d-1")]
    assert any("14 characters) is discarded" in line for line in screen.own())
    store.apply_all(outbox.sent, now=9.0)
    ended = store.snapshot().decisions["d-1"]
    assert isinstance(ended.outcome, Dismissed) and ended.answer is None
    assert not store.snapshot().needs_you


def test_the_operators_draft_is_never_drawn_as_a_line_of_the_panes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snap = _snap_with_one()
    state, outbox = DecisionState(drafts={"d-1": "MY DRAFT"}), _Outbox()

    first = _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.SEND}))
    second = _box(monkeypatch, snap, state, outbox)

    for screen in (first, second):
        assert not any("MY DRAFT" in line.text for line in screen.said)
        assert [value for _, value, _ in screen.boxes] == ["MY DRAFT"]


def test_an_answer_sent_after_its_asker_withdrew_is_not_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The withdrawal is applied first, so the answer changes nothing. The draft is
    still in hand afterwards, because only the store showing it answered clears it.
    """
    store, snap = _snap_with_one()
    state, outbox = DecisionState(drafts={"d-1": AS_TYPED}), _Outbox()
    _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.SEND}))
    _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.CONFIRM[Act.SEND]}))

    store.apply(DecisionWithdrawn(BUILDER, "d-1"), now=8.0)
    store.apply_all(outbox.sent, now=9.0)
    after = store.snapshot()
    state.attend(after, None)

    assert isinstance(after.decisions["d-1"].outcome, Withdrawn)
    assert state.drafts == {"d-1": AS_TYPED}


# -- where an answered decision is read afterwards ------------------------------------


def test_an_answered_decision_is_listed_with_its_question_its_answer_and_whether_it_was_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store)
    store.apply(DecisionAnswered("d-1", AS_TYPED), now=10.0)
    snap = store.snapshot()
    screen = Screen(monkeypatch, shut=ASKED_SHUT)

    decision_view.draw_closed(snap, DecisionState(), "s1", 70.0)

    assert Said("decisions closed in this session (1)", False, "node") in screen.said
    assert "d-1 from builder: answered by you 1m00s ago" in screen.own()
    assert "answer not yet read by builder" in screen.own()
    assert decision_view.YOUR_ANSWER in screen.own()
    # The question and the answer, each whole and each behind the bar.
    assert screen.quoted() == [QUESTION, AS_TYPED]


def test_the_closed_list_stays_shut_until_it_is_opened_and_is_absent_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store)
    nothing = Screen(monkeypatch)
    decision_view.draw_closed(store.snapshot(), DecisionState(), "s1", 70.0)
    assert nothing.said == []

    store.apply(DecisionAnswered("d-1", "yes"), now=10.0)
    shut = Screen(monkeypatch, tree_open=False)
    decision_view.draw_closed(store.snapshot(), DecisionState(), "s1", 70.0)

    # The one line that is not behind the triangle is the one still to act on.
    assert [line.text for line in shut.said] == [
        "answered, not yet read by builder: d-1",
        "decisions closed in this session (1)",
    ]
    assert shut.imgui.tree_pop.call_count == 0


def test_a_dismissed_and_a_withdrawn_decision_are_listed_with_no_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store, "d-1")
    raised(store, "d-2")
    store.apply(DecisionDismissed("d-1"), now=10.0)
    store.apply(DecisionWithdrawn(BUILDER, "d-2"), now=11.0)
    screen = Screen(monkeypatch, shut=ASKED_SHUT)

    decision_view.draw_closed(store.snapshot(), DecisionState(), "s1", 70.0)

    assert decision_view.YOUR_ANSWER not in screen.own()
    assert screen.quoted() == [QUESTION, QUESTION]
    assert "dismissal not yet read by builder" in screen.own()


def test_an_empty_answer_on_record_is_said_to_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """The box does not send one, and the store will hold one if it is handed one."""
    store = team()
    raised(store)
    store.apply(DecisionAnswered("d-1", ""), now=10.0)
    screen = Screen(monkeypatch, shut=ASKED_SHUT)

    decision_view.draw_closed(store.snapshot(), DecisionState(), "s1", 70.0)

    assert decision_view.EMPTY_ANSWER in screen.own()
    assert screen.quoted() == [QUESTION]


def test_a_withdrawn_decision_shows_the_unsent_draft_and_lets_go_of_it_only_when_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store)
    store.apply(DecisionWithdrawn(BUILDER, "d-1"), now=10.0)
    snap = store.snapshot()
    state = DecisionState(drafts={"d-1": AS_TYPED})

    shown = Screen(monkeypatch, shut=ASKED_SHUT)
    decision_view.draw_closed(snap, state, "s1", 70.0)
    assert decision_view.WITHDRAWN_DRAFT in shown.own()
    assert shown.quoted() == [QUESTION, AS_TYPED]
    assert state.drafts == {"d-1": AS_TYPED}

    Screen(monkeypatch, press=frozenset({"discard draft"}))
    decision_view.draw_closed(snap, state, "s1", 70.0)
    assert state.drafts == {}


def test_the_closed_list_is_bounded_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    store = team()
    count = decision_view._MAX_CLOSED + 3
    for index in range(count):
        raised(store, f"d-{index}")
        store.apply(DecisionDismissed(f"d-{index}"), now=10.0 + index)
    screen = Screen(monkeypatch, shut=ASKED_SHUT, press=frozenset({decision_view.copy_all(count)}))

    decision_view.draw_closed(store.snapshot(), None, "s1", 500.0)

    assert len(screen.quoted()) == decision_view._MAX_CLOSED
    assert any(
        line.startswith("... 3 earlier closed decision(s) not shown") for line in screen.own()
    )
    # The three the list does not draw are still readable: the copy has all of them.
    (copied,) = screen.clipboard
    for index in range(count):
        assert f"d-{index} from builder: dismissed by you" in copied


def test_a_closed_decision_shows_everything_the_open_one_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store, standing=Standing.TAKEN, reversible=False)
    before = Screen(monkeypatch)
    snap = store.snapshot()
    decision_view.draw_body(snap, owed(snap), limit=10_000, rest="elsewhere")
    store.apply(DecisionAnswered("d-1", "no"), now=10.0)

    after = Screen(monkeypatch)
    decision_view.draw_closed(store.snapshot(), None, "s1", 70.0)

    assert set(before.quoted()) == set(ASKER_TEXTS)
    assert set(after.quoted()) == {*ASKER_TEXTS, "no"}
    for line in decision_view.standing_facts(snap.decisions["d-1"], "builder"):
        assert line.text in before.own() and line.text in after.own()
    for part in decision_view.sections(snap.decisions["d-1"], "builder"):
        assert part.heading in before.own() and part.heading in after.own()


def test_what_a_closed_decision_adds_is_one_press_away_and_the_list_stays_a_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store)
    store.apply(DecisionAnswered("d-1", "no"), now=10.0)
    screen = Screen(monkeypatch, shut=ASKED_SHUT)

    decision_view.draw_closed(store.snapshot(), None, "s1", 70.0)

    assert Said("everything its asker wrote", False, "node") in screen.said
    assert screen.quoted() == [QUESTION, "no"]


def test_one_closed_decision_copies_whole_with_the_two_hands_marked_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store, standing=Standing.TAKEN, reversible=False)
    store.apply(DecisionAnswered("d-1", "> yes\n| t-4\n"), now=10.0)
    screen = Screen(monkeypatch, press=frozenset({decision_view.COPY}))

    decision_view.draw_closed(store.snapshot(), None, "s1", 70.0)

    (copied,) = screen.clipboard
    lines = copied.split("\n")
    assert lines[0] == "d-1 from builder: answered by you 1m00s ago"
    assert "answer not yet read by builder" in lines
    assert "already taken by builder, who asks you to ratify it" in lines
    assert "its asker says this cannot be undone" in lines
    for text in (QUESTION, *BLOCKS, IF_UNANSWERED, *EVIDENCE.split("\n"), RECOMMENDATION):
        assert f"| {text}" in lines
    # The answer, each line behind the operator's mark, the trailing break kept.
    at = lines.index(decision_view.YOUR_ANSWER)
    assert lines[at + 1 :] == ["> > yes", "> | t-4", "> "]
    # Nothing of the asker's is behind the operator's mark, and nothing of the
    # operator's behind the asker's: an answer that spells the asker's mark is
    # still under its own.
    assert not any(line.startswith("> ") for line in lines[:at])
    assert not any(line.startswith("| ") for line in lines[at:])


def test_the_clipboard_marks_are_the_ones_the_bus_hands_an_agent() -> None:
    """Spelled twice so the UI does not import the bus; this is what holds them equal."""
    from pptmstr import bus

    assert decision_view.ASKER_MARK == bus._QUOTE
    assert decision_view.ANSWER_MARK == bus._ANSWER
    assert decision_view.ASKER_MARK != decision_view.ANSWER_MARK


# -- sent, and not read ---------------------------------------------------------------


def _three_unread() -> Store:
    store = team()
    raised(store, "d-1")
    raised(store, "d-2")
    raised(store, "d-3", asker=ROOT)
    raised(store, "d-4")
    raised(store, "d-open")
    raised(store, "d-other", asker=OTHER, blocks=())
    store.apply(DecisionAnswered("d-1", "yes"), now=10.0)
    store.apply(DecisionAnswered("d-2", "no"), now=11.0)
    store.apply(DecisionDismissed("d-3"), now=12.0)
    store.apply(DecisionWithdrawn(BUILDER, "d-4"), now=13.0)
    store.apply(DecisionAnswered("d-other", "no"), now=14.0)
    return store


def test_what_the_operator_sent_and_nobody_read_is_listed_by_who_has_not_read_it() -> None:
    snap = _three_unread().snapshot()

    assert [d.id for d in decision_view.unread(snap, "s1")] == ["d-1", "d-2", "d-3"]
    assert decision_view.unread_lines(snap, "s1") == (
        Line("answered, not yet read by builder: d-1, d-2", warn=True),
        Line("dismissed, not yet read by lead: d-3", warn=True),
    )
    assert decision_view.unread_lines(snap, "s2") == (
        Line("answered, not yet read by lead: d-other", warn=True),
    )


def test_a_decision_its_asker_has_been_handed_is_no_longer_listed_as_unread() -> None:
    store = _three_unread()
    read = dataclasses.replace(store.snapshot().decisions["d-1"], first_delivered_at=20.0)
    snap = dataclasses.replace(
        store.snapshot(), decisions={**store.snapshot().decisions, "d-1": read}
    )

    assert [d.id for d in decision_view.unread(snap, "s1")] == ["d-2", "d-3"]
    assert decision_view.unread_answers(snap, lambda asker: asker == BUILDER) == 1


def test_a_card_counts_the_answers_its_own_agents_have_not_read_and_no_dismissal() -> None:
    snap = _three_unread().snapshot()

    assert decision_view.unread_answers(snap, lambda asker: asker == BUILDER) == 2
    # d-3 was dismissed: nothing its asker was waiting for is sitting unread.
    assert decision_view.unread_answers(snap, lambda asker: asker == ROOT) == 0
    assert decision_view.unread_answers(snap, lambda asker: asker[0] == "s1") == 2
    assert decision_view.unread_answers(snap, lambda asker: asker[0] == "s2") == 1
    assert decision_view.unread_mark(1) == "1 answer unread"
    assert decision_view.unread_mark(2) == "2 answers unread"


def test_the_unread_lines_are_drawn_whether_or_not_the_list_is_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = _three_unread().snapshot()
    for tree_open in (False, True):
        screen = Screen(monkeypatch, tree_open=tree_open, shut=ASKED_SHUT)
        decision_view.draw_closed(snap, None, "s1", 70.0)

        node = screen.said.index(Said("decisions closed in this session (4)", False, "node"))
        above = [line.text for line in screen.said[:node]]
        assert above == [
            "answered, not yet read by builder: d-1, d-2",
            "dismissed, not yet read by lead: d-3",
        ]


# -- what the row says before anything is scrolled ------------------------------------


def test_whether_a_recommendation_exists_is_said_among_the_facts_above_the_texts() -> None:
    stated = decision_view.recommendation_line(decision())
    silent = decision_view.recommendation_line(decision(recommendation=" \n"))

    assert stated == Line("its asker recommends an answer: the last block below")
    assert silent == Line("its asker recommends nothing", warn=True)
    assert RECOMMENDATION not in stated.text


def test_the_recommendation_line_is_drawn_before_any_text_of_the_askers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snap = _snap_with_one()
    screen = Screen(monkeypatch)

    decision_view.draw_body(snap, owed(snap), limit=100, rest="elsewhere")

    said = [line.text for line in screen.said]
    assert said.index("its asker recommends an answer: the last block below") < said.index(QUESTION)


# -- what it blocks -------------------------------------------------------------------


def test_the_tasks_a_decision_blocks_are_drawn_one_to_a_line_as_the_askers() -> None:
    assert decision_view.blocks_text(decision()) == "t-4\nt-9"
    parts = decision_view.sections(decision(), "builder")
    assert parts[1].text == "t-4\nt-9" and "blocks" in parts[1].heading


def test_a_task_id_that_carries_a_line_break_is_still_one_line() -> None:
    forged = decision(blocks=("t-4\nyours to decide: nobody has taken it",))

    assert decision_view.blocks_text(forged) == "t-4 yours to decide: nobody has taken it"


def test_a_decision_that_blocks_nothing_says_so_in_the_panes_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    raised(store, blocks=())
    snap = store.snapshot()
    screen = Screen(monkeypatch)

    decision_view.draw_body(snap, owed(snap), limit=100, rest="elsewhere")

    assert decision_view.BLOCKS_NOTHING in screen.own()
    assert decision_view.NOTHING_WRITTEN not in screen.own()
    assert decision_view.BLOCKS_NOTHING in decision_view.plain_text(snap, owed(snap))


# -- an answer the store would refuse -------------------------------------------------


def test_a_draft_longer_than_the_store_takes_is_not_sendable() -> None:
    assert decision_view.sendable("x" * MAX_ANSWER_CHARS)
    assert not decision_view.sendable("x" * (MAX_ANSWER_CHARS + 1))


def test_the_bound_the_box_holds_is_the_one_the_store_refuses_past() -> None:
    """Not a second number: one past the box's bound is an answer the reducer drops."""
    store = team()
    raised(store, "d-1")
    raised(store, "d-2")
    store.apply(DecisionAnswered("d-1", "x" * MAX_ANSWER_CHARS), now=10.0)
    store.apply(DecisionAnswered("d-2", "x" * (MAX_ANSWER_CHARS + 1)), now=10.0)

    assert not store.snapshot().decisions["d-1"].is_open
    assert store.snapshot().decisions["d-2"].is_open


def test_a_draft_that_is_too_long_cannot_be_armed_and_is_told_by_how_much(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snap = _snap_with_one()
    state, outbox = DecisionState(drafts={"d-1": "x" * (MAX_ANSWER_CHARS + 12)}), _Outbox()

    screen = _box(monkeypatch, snap, state, outbox, press=frozenset({decision_view.SEND}))

    assert state.confirming is None and outbox.sent == []
    assert any("12 characters over" in line for line in screen.own())
    assert decision_view.FINAL_HINT not in screen.own()


def test_detail_says_a_draft_exists_without_drawing_it_and_offers_no_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snap = _snap_with_one()
    screen = Screen(monkeypatch)

    decision_view.draw_detail(snap, DecisionState(drafts={"d-1": "MY DRAFT"}), owed(snap))

    assert decision_view.draft_line("MY DRAFT") in screen.own()
    assert decision_view.ANSWER_FROM_ROW in screen.own()
    assert not any("MY DRAFT" in line.text for line in screen.said)
    assert not any(line.how == "button" for line in screen.said)
    assert screen.boxes == []


# -- Dear ImGui itself ----------------------------------------------------------------
#
# A context with a display size and no backend: nothing is rendered. What these
# can show and the recording imgui cannot is what the widget does with the text.


class _Frames:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, store: Store, state: DecisionState) -> None:
        self.store = store
        self.state = state
        self.outbox = _Outbox()
        self.at: dict[str, tuple[float, float]] = {}
        self._monkeypatch = monkeypatch

    def __enter__(self) -> _Frames:
        imgui = real_imgui
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 900.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        at = self.at

        def note(label: str) -> None:
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            at[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)

        class Noting:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            def button(self, label: str) -> bool:
                pressed = imgui.button(label)
                note(label)
                return bool(pressed)

        real_box = decision_view.multiline_input

        def box(label: str, value: str, size: object, *, wrap: bool, flags: int = 0):  # type: ignore[no-untyped-def]
            result = real_box(label, value, size, wrap=wrap, flags=flags)  # type: ignore[arg-type]
            note(label)
            return result

        self._monkeypatch.setattr(decision_view, "imgui", Noting())
        self._monkeypatch.setattr(decision_view, "multiline_input", box)
        return self

    def __exit__(self, *exc: object) -> None:
        real_imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            self.store.apply_all(self.outbox.sent, now=50.0)
            self.outbox.sent.clear()
            snap = self.store.snapshot()
            row = next((o for o in snap.needs_you if isinstance(o, DecisionOwed)), None)
            self.state.attend(snap, None if row is None else row.decision.id)
            real_imgui.new_frame()
            real_imgui.set_next_window_pos(real_imgui.ImVec2(0.0, 0.0))
            real_imgui.set_next_window_size(real_imgui.ImVec2(900.0, 600.0))
            real_imgui.begin("needs you")
            if row is not None:
                decision_view.draw_answer_box(snap, self.state, self.outbox, row)  # type: ignore[arg-type]
            real_imgui.end()
            real_imgui.end_frame()

    def click(self, label: str) -> None:
        x, y = self.at[label]
        self.io.add_mouse_pos_event(x, y)
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(3)

    def type(self, text: str) -> None:
        self.io.add_input_characters_utf8(text)
        self.frame(2)

    def enter(self) -> None:
        self.io.add_key_event(real_imgui.Key.enter, True)
        self.frame()
        self.io.add_key_event(real_imgui.Key.enter, False)
        self.frame()


@pytest.fixture
def frames(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Frames]:
    store = team()
    raised(store)
    with _Frames(monkeypatch, store, DecisionState()) as real:
        real.frame(3)
        yield real


def test_in_dear_imgui_the_text_typed_into_the_box_is_the_text_stored_blanks_included(
    frames: _Frames,
) -> None:
    frames.click("##answer")
    frames.type("  yes, but ")
    frames.enter()
    frames.enter()
    frames.type(" not on main  ")
    frames.frame(5)

    assert frames.state.drafts["d-1"] == "  yes, but \n\n not on main  "
    assert frames.outbox.sent == [] and frames.store.snapshot().decisions["d-1"].is_open


def test_in_dear_imgui_two_presses_send_exactly_what_the_box_held(frames: _Frames) -> None:
    frames.click("##answer")
    frames.type("  yes, but ")
    frames.enter()
    frames.type(" not on main  ")

    frames.click(decision_view.SEND)
    assert frames.state.confirming == ("d-1", Act.SEND)
    assert frames.store.snapshot().decisions["d-1"].is_open

    frames.click(decision_view.CONFIRM[Act.SEND])

    ended = frames.store.snapshot().decisions["d-1"]
    assert ended.answer == "  yes, but \n not on main  "
    assert not frames.store.snapshot().needs_you
    assert frames.state.drafts == {}


def test_in_dear_imgui_typing_while_the_second_press_is_awaited_changes_nothing(
    frames: _Frames,
) -> None:
    frames.click("##answer")
    frames.type("the answer")
    frames.click(decision_view.SEND)
    assert frames.state.confirming == ("d-1", Act.SEND)

    frames.click("##answer")
    frames.type(" and a late addition")
    frames.frame(3)
    assert frames.state.drafts["d-1"] == "the answer"

    frames.click(decision_view.CONFIRM[Act.SEND])
    assert frames.store.snapshot().decisions["d-1"].answer == "the answer"


def test_in_dear_imgui_send_does_nothing_on_an_empty_box_and_dismiss_still_works(
    frames: _Frames,
) -> None:
    frames.click("##answer")
    frames.type("   ")
    frames.click(decision_view.SEND)
    assert frames.state.confirming is None

    frames.click(decision_view.DISMISS)
    assert frames.state.confirming == ("d-1", Act.DISMISS)
    frames.click(decision_view.CONFIRM[Act.DISMISS])

    ended = frames.store.snapshot().decisions["d-1"]
    assert isinstance(ended.outcome, Dismissed) and ended.answer is None


def test_in_dear_imgui_keep_editing_returns_to_a_box_that_takes_text_again(
    frames: _Frames,
) -> None:
    frames.click("##answer")
    frames.type("first")
    frames.click(decision_view.SEND)
    frames.click(decision_view.BACK)
    assert frames.state.confirming is None

    frames.click("##answer")
    frames.type("!")
    frames.frame(2)

    assert frames.state.drafts["d-1"].count("!") == 1
    assert sorted(frames.state.drafts["d-1"].replace("!", "")) == sorted("first")
    assert frames.outbox.sent == []


def test_in_dear_imgui_escape_leaves_the_box_and_keeps_the_draft(frames: _Frames) -> None:
    """
    Escape in a text box puts back the text the box held when the caret entered
    it. That is the draft as it was before this visit, which on a first visit is
    nothing at all.
    """
    frames.click("##answer")
    frames.type("yes, but not on main")

    frames.io.add_key_event(real_imgui.Key.escape, True)
    frames.frame()
    frames.io.add_key_event(real_imgui.Key.escape, False)
    frames.frame(3)

    assert frames.state.drafts["d-1"] == "yes, but not on main"
    frames.type(" - ignored, the box is not active")
    assert frames.state.drafts["d-1"] == "yes, but not on main"
