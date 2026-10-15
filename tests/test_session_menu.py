"""
The controls on a fleet card: what each kind of card offers, what choosing an item
calls, and how the menu is opened and worked in frames of a real Dear ImGui context.

Three hazards are held here. An item that does something other than the control it
stands for, which is why the routing is compared against what HEALTH's and the
failure row's own buttons call and not only against a list written in this file. A
session ended by one keypress, which is why Close on a session mid-turn is followed
through its second step. And a key meant for the menu being read by something else
-- an ``a`` over an open menu approving a call, the Enter that chooses an item
switching the layout, an ``m`` from a sentence opening a menu.

The frames are real because every one of those is about what Dear ImGui does with
the keyboard and the mouse, with keyboard navigation off as the application has it.
Nothing is rendered: the context has a display size and no backend.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Callable, Iterator
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui

from pptmstr.approval import Policy, requires_containment
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    ApprovalRequested,
    FailureAcknowledged,
    SessionRecalled,
    StateChanged,
)
from pptmstr.model import (
    AgentRecord,
    AgentState,
    DetachRefusal,
    EndedHow,
    LaunchSpec,
    NodeId,
    PendingApproval,
    Recalled,
    Snapshot,
    TranscriptPresence,
)
from pptmstr.pool import SessionPool
from pptmstr.settings import Settings
from pptmstr.store import Store
from pptmstr.ui import gate_menu, health, inbox, launcher, rail, session_details, session_menu
from pptmstr.ui.session_menu import Action, Facts, Heading, Item, Kind, Line, Rule
from pptmstr.ui.widgets import gate_adds

ROOT: NodeId = ("s1", None)
BUILDER: NodeId = ("s1", "agent-b")
OTHER: NodeId = ("s2", None)

LIVE = [s for s in AgentState if not s.is_terminal]
ENDED = [s for s in AgentState if s.is_terminal]
GATES: list[Policy | None] = [None, *Policy]


def record(
    node: NodeId = ROOT,
    state: AgentState = AgentState.THINKING,
    *,
    acknowledged: bool = False,
) -> AgentRecord:
    return AgentRecord(
        node_id=node,
        parent=None if node[1] is None else (node[0], None),
        depth=0 if node[1] is None else 1,
        state=state,
        topic="working",
        task="rotate the signing key",
        model="claude-opus-5",
        agent_type=None if node[1] is None else "builder",
        cwd="/x/keys",
        session_base="/x/keys",
        template="team",
        brief="/x/briefs/s1",
        acknowledged=acknowledged,
    )


def _actions() -> session_menu.Actions:
    return session_menu.Actions(
        interrupt=MagicMock(),
        close=MagicMock(),
        fork=MagicMock(),
        revoke_policy=MagicMock(),
        relaunch=MagicMock(),
        dismiss=MagicMock(),
        resume=MagicMock(),
        health=MagicMock(),
    )


def _wired() -> session_menu.Actions:
    """``_actions`` with the menu's own three levers supplied as well."""
    return dataclasses.replace(
        _actions(), detach=MagicMock(), resume_as=MagicMock(), set_policy=MagicMock()
    )


_OWN = ("detach", "resume_as", "set_policy")


def _called(actions: session_menu.Actions) -> dict[str, list[tuple[object, ...]]]:
    """Every action that was called, with what it was called with."""
    out = {}
    for name in (
        "interrupt",
        "close",
        "fork",
        "revoke_policy",
        "relaunch",
        "dismiss",
        "resume",
        *_OWN,
    ):
        lever = getattr(actions, name)
        calls = [] if lever is None else [call.args for call in lever.call_args_list]
        if calls:
            out[name] = calls
    calls = [call.args for call in actions.health.call_args_list]  # type: ignore[attr-defined]
    if calls:
        out["health"] = calls
    return out


def _gate_row(now: Policy, rung: Policy, *, live: bool, contained: bool = False) -> gate_menu.Row:
    """``gate_menu``'s row for ``rung``, of a session on ``now``."""
    (row,) = [r for r in gate_menu.rows(now, contained, live, adds=gate_adds) if r.rung is rung]
    return row


def _consequence(now: Policy, rung: Policy, *, live: bool, contained: bool = False) -> str:
    """What ``gate_menu`` says at length of ``rung``'s row for a session on ``now``."""
    return _gate_row(now, rung, live=live, contained=contained).consequence


def _row(action: Action, label: str, now: Policy, rung: Policy, refused: str | None) -> Item:
    """
    A Gate row of a running session nobody said the containment of. ``label`` is
    the line as the menu draws it, written out; a line that ends in an ellipsis is
    one that asks first.
    """
    row = _gate_row(now, rung, live=True)
    return Item(
        action,
        label,
        refused,
        grouped=True,
        rung=rung,
        note=row.consequence,
        asks=label.endswith("…"),
        question=row.label,
        sentence=row.sentence,
    )


def _now(rung: Policy) -> Line:
    return Line(f"{rung.value} (now)", _consequence(rung, rung, live=True))


_S, _P, _A = Policy.STRICT, Policy.PERMISSIVE, Policy.AUTONOMOUS
_UNSAID = Line(session_menu.CONTAINMENT_NOT_KNOWN)

# The Gate group of a running session's card, by the gate it answers with, as a
# menu that was told nothing else draws it. Written out, rung by rung: the one
# row that can act is the narrow to the lowest rung, which is HEALTH's lever.
_GATE_UNTOLD: dict[Policy | None, tuple[session_menu.Entry, ...]] = {
    None: (Line(session_menu.GATE_NOT_HELD),),
    _S: (
        _now(_S),
        _row(Action.SET_GATE, "permissive…", _S, _P, session_menu.NOT_WIRED),
        _row(Action.RESUME_AS, "autonomous  restart…", _S, _A, session_menu.NOT_WIRED),
        _UNSAID,
    ),
    _P: (
        _row(Action.SET_GATE, "strict", _P, _S, None),
        _now(_P),
        _row(Action.RESUME_AS, "autonomous  restart…", _P, _A, session_menu.NOT_WIRED),
        _UNSAID,
    ),
    _A: (
        _row(Action.SET_GATE, "strict", _A, _S, None),
        _row(Action.SET_GATE, "permissive", _A, _P, session_menu.NOT_WIRED),
        _now(_A),
        _UNSAID,
    ),
}


# -- which card gets which menu -------------------------------------------------------


@pytest.mark.parametrize("state", list(AgentState))
def test_a_cards_kind_is_its_node_and_whether_it_has_ended(state: AgentState) -> None:
    assert session_menu.kind_of(record(BUILDER, state)) is Kind.SUB_AGENT
    want = Kind.ENDED_ROOT if state in ENDED else Kind.LIVE_ROOT
    assert session_menu.kind_of(record(ROOT, state)) is want


def test_a_sub_agent_recovered_without_a_parent_is_still_a_sub_agent() -> None:
    orphan = record(BUILDER).with_(parent=None)
    assert session_menu.kind_of(orphan) is Kind.SUB_AGENT


@pytest.mark.parametrize(
    ("state", "policy", "acknowledged"),
    list(itertools.product(AgentState, GATES, [False, True])),
)
def test_a_sub_agents_card_offers_details_and_nothing_else(
    state: AgentState, policy: Policy | None, acknowledged: bool
) -> None:
    offered = session_menu.entries(record(BUILDER, state, acknowledged=acknowledged), policy)
    assert offered == (Item(Action.DETAILS, "Details"),)


@pytest.mark.parametrize(("state", "policy"), list(itertools.product(LIVE, GATES)))
def test_a_live_roots_card_offers_every_control_and_says_why_one_cannot_act(
    state: AgentState, policy: Policy | None
) -> None:
    """
    Written out here rather than derived, state by state: the expectation is the
    table in ``planning/2026-10-13-a-fleet-that-survives-the-window.md``.
    """
    turn = state is not AgentState.AWAITING_INPUT
    if policy is None:
        interrupt = close = session_menu.NOT_HELD
    else:
        interrupt = None if turn else "no turn is under way"
        close = None

    assert session_menu.entries(record(ROOT, state), policy) == (
        Item(Action.INTERRUPT, "Interrupt", interrupt),
        Item(Action.FORK, "Fork"),
        Heading("Gate"),
        *_GATE_UNTOLD[policy],
        Item(Action.DETAILS, "Details"),
        Item(Action.HEALTH, "Health"),
        Rule(),
        Item(Action.CLOSE, "Close…" if turn else "Close", close),
    )


@pytest.mark.parametrize(
    ("state", "policy", "acknowledged"), list(itertools.product(ENDED, GATES, [False, True]))
)
def test_an_ended_roots_card_offers_resume_fork_and_details_and_a_failed_one_its_two_more(
    state: AgentState, policy: Policy | None, acknowledged: bool
) -> None:
    """
    As a menu nobody told anything draws it: the Gate rows and the removal are
    there, greyed, and say it is the build that lacks them.
    """
    why = session_menu.NOT_WIRED
    want: list[session_menu.Entry] = [
        Item(Action.RESUME, "Resume…"),
        Item(Action.FORK, "Fork"),
        Heading("Gate"),
        Line("strict (last run)", _consequence(Policy.STRICT, Policy.STRICT, live=False)),
        *(
            Item(
                Action.RESUME_AS,
                f"{rung.value}…",
                why,
                grouped=True,
                rung=rung,
                note=_consequence(Policy.STRICT, rung, live=False),
                # The resume form is the confirmation: no question in front of it.
                asks=False,
                question=f"Resume as {rung.value}…",
                sentence=_gate_row(Policy.STRICT, rung, live=False).sentence,
            )
            for rung in (Policy.PERMISSIVE, Policy.AUTONOMOUS)
        ),
        Item(Action.DETAILS, "Details"),
    ]
    if state is AgentState.FAILED:
        want += [
            Rule(),
            Item(Action.RETRY, "Retry in a new session"),
            Item(
                Action.DISMISS,
                "Dismiss",
                "already dismissed from NEEDS YOU" if acknowledged else None,
            ),
            # Nobody said whether a process is attached, and Close is two
            # different acts by the answer.
            Item(Action.CLOSE, "Close", session_menu.RUN_NOT_KNOWN),
        ]
    want += [Rule(), Item(Action.REMOVE, "Remove from fleet", why)]
    offered = session_menu.entries(record(ROOT, state, acknowledged=acknowledged), policy)
    assert offered == tuple(want)


def test_a_refused_item_is_drawn_with_its_reason_on_its_own_line() -> None:
    refused = Item(Action.CLOSE, "Close", "already closed")
    assert not refused.enabled
    assert refused.text == "Close  -  already closed"
    assert Item(Action.FORK, "Fork").text == "Fork"


def test_a_refused_item_is_drawn_with_a_few_words_of_its_reason_and_keeps_the_sentence() -> None:
    """
    The sentence is what a notice or a log quotes, and it is what the item is
    compared by; the line in the menu carries the few words.
    """
    why = session_menu.NOT_HELD
    refused = Item(Action.INTERRUPT, "Interrupt", why)
    assert refused.refused == "no running session is held for this card"
    assert refused.text == "Interrupt  -  no running session held"
    assert f"not done: {why}" == "not done: no running session is held for this card"
    assert Line(session_menu.CONTAINMENT_NOT_KNOWN).text == "containment not known"


def _reasons() -> list[session_menu.Reason]:
    """Every reason the module names, the ones in its table of removals included."""
    named = [v for v in vars(session_menu).values() if isinstance(v, session_menu.Reason)]
    return [*named, *session_menu.REMOVAL_REFUSED.values()]


def test_every_reason_a_menu_can_draw_is_a_few_words() -> None:
    reasons = _reasons()
    assert len(reasons) >= 12, "the module's reasons were not found"
    for reason in reasons:
        assert 0 < len(reason.words.split()) <= session_menu.FEW_WORDS, reason
        assert "\n" not in reason.words


def test_no_menu_draws_a_line_longer_than_a_label_and_a_few_words() -> None:
    """
    Over every state, gate and fact: a line nobody chooses is a few words, an
    item that cannot act says why with a reason that has its few words, and no
    line is the sentence kept as its note.
    """
    seen = 0
    for state, policy, contained, held, wired in itertools.product(
        AgentState, GATES, SAID, SAID, [frozenset(), ALL_WIRED]
    ):
        facts = Facts(contained=contained, run_held=held, wired=wired)
        for node in (ROOT, BUILDER):
            for entry in session_menu.entries(record(node, state), policy, facts):
                if isinstance(entry, Line):
                    assert len(entry.text.split()) <= session_menu.FEW_WORDS, entry
                    assert entry.note not in entry.text or not entry.note
                elif isinstance(entry, Item):
                    seen += 1
                    assert entry.refused is None or isinstance(
                        entry.refused, session_menu.Reason
                    ), entry
                    assert entry.note not in entry.text or not entry.note
                    assert len(entry.label.split()) <= 5, entry
    assert seen


@pytest.mark.parametrize("state", list(AgentState))
def test_a_turn_is_under_way_unless_the_session_ended_or_waits_for_the_operator(
    state: AgentState,
) -> None:
    resting = {
        AgentState.AWAITING_INPUT,
        AgentState.DONE,
        AgentState.FAILED,
        AgentState.CANCELLED,
    }
    assert session_menu.mid_turn(record(ROOT, state)) is (state not in resting)


# -- the keyboard's highlight ---------------------------------------------------------


def test_the_highlight_walks_only_the_items_that_can_act_and_wraps() -> None:
    offered = session_menu.entries(record(ROOT, AgentState.AWAITING_INPUT), Policy.STRICT)
    able = [Action.FORK, Action.DETAILS, Action.HEALTH, Action.CLOSE]

    assert session_menu.step(offered, None, 1) is Action.FORK
    assert session_menu.step(offered, None, -1) is Action.CLOSE
    walked = [Action.FORK]
    for _ in range(4):
        walked.append(session_menu.step(offered, walked[-1], 1))  # type: ignore[arg-type]
    assert walked == [*able, Action.FORK]
    assert session_menu.step(offered, Action.FORK, -1) is Action.CLOSE


def test_a_highlight_on_an_item_that_stopped_being_able_to_act_starts_over() -> None:
    offered = session_menu.entries(record(ROOT, AgentState.AWAITING_INPUT), Policy.STRICT)
    assert session_menu.step(offered, Action.INTERRUPT, 1) is Action.FORK
    assert session_menu.step(offered, Action.RESUME, -1) is Action.CLOSE


def test_a_menu_with_nothing_that_can_act_has_nowhere_for_the_highlight() -> None:
    assert session_menu.step((Rule(), Item(Action.CLOSE, "Close", "no")), None, 1) is None


# -- what choosing an item calls ------------------------------------------------------


def test_each_item_of_a_live_card_calls_the_one_action_it_names() -> None:
    rec = record(ROOT, AgentState.THINKING)
    spec = LaunchSpec.from_record(rec)
    want: dict[Action, dict[str, list[tuple[object, ...]]]] = {
        Action.INTERRUPT: {"interrupt": [(ROOT,)]},
        Action.FORK: {"fork": [(spec,)]},
        # Under permissive with nothing else said, the one gate row that can act
        # is the narrow to the lowest rung.
        Action.SET_GATE: {"revoke_policy": [(ROOT,)]},
        Action.DETAILS: {},
        Action.HEALTH: {"health": [(ROOT,)]},
        # Mid-turn: the close is asked about, not made.
        Action.CLOSE: {},
    }
    for action, calls in want.items():
        actions, state = _actions(), session_menu.MenuState()
        session_menu.choose(action, rec, Policy.PERMISSIVE, state, actions)
        assert _called(actions) == calls, action
        assert (state.details.node == ROOT) is (action is Action.DETAILS), action
        assert (state.confirming == ROOT) is (action is Action.CLOSE), action


def test_each_item_of_a_failed_card_calls_the_one_action_it_names() -> None:
    rec = record(ROOT, AgentState.FAILED)
    spec = LaunchSpec.from_record(rec)
    want: dict[Action, dict[str, list[tuple[object, ...]]]] = {
        Action.RESUME: {"resume": [(rec,)]},
        Action.FORK: {"fork": [(spec,)]},
        Action.DETAILS: {},
        Action.RETRY: {"relaunch": [(spec,)], "dismiss": [(ROOT,)]},
        Action.DISMISS: {"dismiss": [(ROOT,)]},
    }
    for action, calls in want.items():
        actions, state = _actions(), session_menu.MenuState()
        session_menu.choose(action, rec, None, state, actions)
        assert _called(actions) == calls, action
        assert state.confirming is None


def test_a_fork_carries_no_permission_the_card_was_running_under() -> None:
    """
    ``LaunchSpec.from_record`` drops the rung and the containment, and says why.
    The menu's fork is HEALTH's: it must not be the route by which a widened gate
    is inherited in one click.
    """
    rec = record(ROOT, AgentState.THINKING).with_(policy=Policy.AUTONOMOUS)
    actions = _actions()
    session_menu.choose(Action.FORK, rec, Policy.AUTONOMOUS, session_menu.MenuState(), actions)

    (spec,) = actions.fork.call_args.args  # type: ignore[attr-defined]
    assert spec.policy is Policy.STRICT and spec.containment is None and spec.resume is None


@pytest.mark.parametrize("state", [s for s in LIVE if s is not AgentState.AWAITING_INPUT])
def test_close_on_a_session_mid_turn_closes_nothing_until_it_is_confirmed(
    state: AgentState,
) -> None:
    actions, menu = _actions(), session_menu.MenuState()
    session_menu.choose(Action.CLOSE, record(ROOT, state), Policy.STRICT, menu, actions)

    assert _called(actions) == {}
    assert menu.confirming == ROOT


def test_close_on_a_session_waiting_for_the_operator_closes_it_as_its_button_does() -> None:
    actions, menu = _actions(), session_menu.MenuState()
    rec = record(ROOT, AgentState.AWAITING_INPUT)
    session_menu.choose(Action.CLOSE, rec, Policy.STRICT, menu, actions)

    assert _called(actions) == {"close": [(ROOT,)]}
    assert menu.confirming is None


@pytest.mark.parametrize(
    ("action", "state", "policy", "acknowledged"),
    [
        (Action.INTERRUPT, AgentState.AWAITING_INPUT, Policy.STRICT, False),
        (Action.INTERRUPT, AgentState.THINKING, None, False),
        (Action.SET_GATE, AgentState.THINKING, Policy.STRICT, False),
        (Action.SET_GATE, AgentState.THINKING, None, False),
        (Action.RESUME_AS, AgentState.THINKING, Policy.STRICT, False),
        (Action.RESUME_AS, AgentState.DONE, None, False),
        (Action.REMOVE, AgentState.DONE, None, False),
        (Action.CLOSE, AgentState.THINKING, None, False),
        (Action.CLOSE, AgentState.AWAITING_INPUT, None, False),
        (Action.DISMISS, AgentState.FAILED, None, True),
    ],
)
def test_choosing_an_item_that_is_refused_does_nothing(
    action: Action, state: AgentState, policy: Policy | None, acknowledged: bool
) -> None:
    """
    The keypress that chooses and the frame that drew the item are not always one
    frame: the turn can end between them.
    """
    actions, menu = _actions(), session_menu.MenuState()
    rec = record(ROOT, state, acknowledged=acknowledged)
    session_menu.choose(action, rec, policy, menu, actions)

    assert _called(actions) == {}
    assert menu.confirming is None and menu.details.node is None


@pytest.mark.parametrize(
    ("action", "node", "state"),
    [
        (Action.CLOSE, ROOT, AgentState.DONE),
        (Action.INTERRUPT, ROOT, AgentState.CANCELLED),
        (Action.RESUME, ROOT, AgentState.THINKING),
        (Action.RETRY, ROOT, AgentState.DONE),
        (Action.DISMISS, ROOT, AgentState.CANCELLED),
        (Action.CLOSE, BUILDER, AgentState.THINKING),
        (Action.FORK, BUILDER, AgentState.THINKING),
        (Action.HEALTH, ROOT, AgentState.DONE),
        (Action.REMOVE, ROOT, AgentState.THINKING),
        (Action.REMOVE, BUILDER, AgentState.DONE),
        (Action.SET_GATE, ROOT, AgentState.DONE),
        (Action.SET_GATE, BUILDER, AgentState.THINKING),
    ],
)
def test_choosing_an_item_the_card_does_not_offer_does_nothing(
    action: Action, node: NodeId, state: AgentState
) -> None:
    actions, menu = _actions(), session_menu.MenuState()
    session_menu.choose(action, record(node, state), Policy.PERMISSIVE, menu, actions)

    assert _called(actions) == {}
    assert menu.confirming is None and menu.details.node is None


# -- the same calls the existing controls make ----------------------------------------


class _Pressing:
    """
    ``imgui`` for one pane, in which the button with one label is pressed on every
    frame and nothing else is.
    """

    def __init__(self, label: str) -> None:
        self._label = label

    def __getattr__(self, name: str) -> object:
        return getattr(imgui, name)

    def button(self, label: str, *args: object) -> bool:
        imgui.button(label, *args)  # type: ignore[arg-type]
        return label == self._label


@pytest.fixture
def context() -> Iterator[imgui.IO]:
    made = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1280.0, 900.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.set_ini_filename("")
    io.add_focus_event(True)
    yield io
    imgui.destroy_context(made)


def _store(state: AgentState = AgentState.THINKING, *, policy: Policy = Policy.PERMISSIVE) -> Store:
    """Two sessions, the first with a builder under it. The first is in ``state``."""
    store = Store()
    for node, parent, kind in ((ROOT, None, None), (BUILDER, ROOT, "builder"), (OTHER, None, None)):
        store.apply(
            AgentSpawned(
                node_id=node,
                parent=parent,
                task="rotate the signing key" if node[0] == "s1" else "audit the mirror",
                model="claude-opus-5",
                started_at=0.0,
                agent_type=kind,
                cwd=None if parent else "/x/keys",
                template=None if parent else "team",
                policy=None if parent else policy,
            ),
            now=1.0,
        )
        store.apply(StateChanged(node, AgentState.THINKING), now=1.0)
    if state.is_terminal:
        store.apply(AgentFinished(ROOT, state, 2.0, error="the CLI exited 1"), now=2.0)
    elif state is not AgentState.THINKING:
        store.apply(StateChanged(ROOT, state), now=2.0)
    return store


def _health_button(
    monkeypatch: pytest.MonkeyPatch, store: Store, label: str, policy: Policy | None
) -> MagicMock:
    """What HEALTH's button with this label calls, for the first session."""
    actions = MagicMock()
    monkeypatch.setattr(health, "imgui", _Pressing(label))
    imgui.new_frame()
    imgui.begin("health")
    health.draw(store.snapshot(), ROOT, actions, 50.0, policy)
    imgui.end()
    imgui.end_frame()
    return actions


@pytest.mark.parametrize(
    ("label", "action", "name"),
    [
        ("interrupt", Action.INTERRUPT, "interrupt"),
        ("fork", Action.FORK, "fork"),
        ("narrow to strict", Action.SET_GATE, "revoke_policy"),
    ],
)
def test_a_menu_item_calls_what_healths_button_of_the_same_name_calls(
    monkeypatch: pytest.MonkeyPatch,
    context: imgui.IO,
    label: str,
    action: Action,
    name: str,
) -> None:
    store = _store()
    rec = store.snapshot().nodes[ROOT]
    pane = _health_button(monkeypatch, store, label, Policy.PERMISSIVE)
    menu = _actions()
    rung = Policy.STRICT if action is Action.SET_GATE else None
    session_menu.choose(action, rec, Policy.PERMISSIVE, session_menu.MenuState(), menu, rung=rung)

    (pressed,) = [call for call in pane.mock_calls if call[0] == name]
    assert _called(menu) == {name: [pressed.args]}


def test_closing_from_the_menu_calls_what_healths_close_button_calls(
    monkeypatch: pytest.MonkeyPatch, context: imgui.IO
) -> None:
    store = _store(AgentState.AWAITING_INPUT)
    rec = store.snapshot().nodes[ROOT]
    pane = _health_button(monkeypatch, store, "close", Policy.STRICT)
    menu = _actions()
    session_menu.choose(Action.CLOSE, rec, Policy.STRICT, session_menu.MenuState(), menu)

    assert _called(menu) == {"close": [pane.close.call_args.args]}


def test_retry_and_dismiss_call_what_the_failure_rows_buttons_call(
    monkeypatch: pytest.MonkeyPatch, context: imgui.IO
) -> None:
    """
    The failure row makes two calls for a retry, in an order, and the menu's Retry
    is that pair. Compared call for call, argument for argument.
    """
    store = _store(AgentState.FAILED)
    snap = store.snapshot()
    (owed,) = [o for o in snap.needs_you if o.node == ROOT]

    def row(label: str) -> list[tuple[str, tuple[object, ...]]]:
        actions = MagicMock()
        monkeypatch.setattr(inbox, "imgui", _Pressing(label))
        imgui.new_frame()
        imgui.begin("needs you")
        inbox._expand_failure(snap, actions, owed)  # type: ignore[arg-type]
        imgui.end()
        imgui.end_frame()
        return [(call[0], call.args) for call in actions.mock_calls]

    def menu(action: Action) -> list[tuple[str, tuple[object, ...]]]:
        actions = MagicMock()
        wired = session_menu.Actions(
            interrupt=actions.interrupt,
            close=actions.close,
            fork=actions.fork,
            revoke_policy=actions.revoke_policy,
            relaunch=actions.relaunch,
            dismiss=actions.dismiss,
            resume=actions.resume,
            health=actions.health,
        )
        session_menu.choose(action, snap.nodes[ROOT], None, session_menu.MenuState(), wired)
        return [(call[0], call.args) for call in actions.mock_calls]

    retried = row("retry in a new session")
    assert [name for name, _ in retried] == ["relaunch", "dismiss"]
    assert menu(Action.RETRY) == retried
    dismissed = row("dismiss")
    assert [name for name, _ in dismissed] == ["dismiss"]
    assert menu(Action.DISMISS) == dismissed


# -- the application's wiring ---------------------------------------------------------


class _Layouts:
    """Stands in for ``hello_imgui``: the layout shown, and every switch asked for."""

    def __init__(self, shown: str) -> None:
        self.shown = shown
        self.switched: list[str] = []

    def current_layout_name(self) -> str:
        return self.shown

    def switch_layout(self, name: str) -> None:
        self.switched.append(name)
        self.shown = name


def _app_state(monkeypatch: pytest.MonkeyPatch, store: Store, shown: str = "TRIAGE") -> object:
    from pptmstr import app

    monkeypatch.setattr(app, "hello_imgui", _Layouts(shown))
    state = app.AppState(store=store, bridge=MagicMock(), settings=Settings())
    state.frame_snap = store.snapshot()
    return state


def _pool_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, ...]]:
    """
    Every call the session controls make, as ``app._session_action`` and
    ``app._launch`` are handed them: the pool method with its arguments, or the
    launch with its spec and origin.
    """
    from pptmstr import app

    made: list[tuple[object, ...]] = []

    def acted(state: object, factory: Callable[[object], object]) -> None:
        pool = MagicMock()
        factory(pool)
        ((name, args, _),) = pool.mock_calls
        made.append((name, *args))

    def launched(state: object, spec: object, draft: object = None, *, origin: str) -> None:
        made.append(("launch", spec, origin))

    monkeypatch.setattr(app, "_session_action", acted)
    monkeypatch.setattr(app, "_launch", launched)
    return made


def test_the_menu_is_handed_the_functions_the_other_panes_are_handed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The wiring, and the property the menu exists under: one code path per action.
    Each pane's actions are caught as ``app._panels`` hands them over, called, and
    compared with the same call made through the card's.
    """
    from pptmstr import app
    from pptmstr.ui import compose

    state = _app_state(monkeypatch, _store())
    state.pool = MagicMock()  # type: ignore[attr-defined]
    made = _pool_calls(monkeypatch)
    handed: dict[str, object] = {}
    monkeypatch.setattr(health, "draw", lambda *a, **kw: handed.update(health=a[2]))
    monkeypatch.setattr(inbox, "draw", lambda *a, **kw: handed.update(inbox=a[5]))
    monkeypatch.setattr(compose, "draw_conversation", lambda *a, **kw: handed.update(session=kw))
    monkeypatch.setattr(app.transcript_pane, "draw", lambda *a, **kw: None)
    monkeypatch.setattr(app.imgui, "begin_child", lambda *a, **kw: True)
    monkeypatch.setattr(app.imgui, "end_child", lambda: None)
    monkeypatch.setattr(app.imgui, "separator", lambda: None)
    monkeypatch.setattr(app.imgui, "get_content_region_avail", lambda: imgui.ImVec2(400.0, 400.0))
    # _reviewing reads ImGui's frame count, and there is no context here.
    monkeypatch.setattr(app.imgui, "get_frame_count", lambda: 1)
    panels = app._panels(state)  # type: ignore[arg-type]
    for name in ("HEALTH", "NEEDS YOU", "SESSION"):
        panels[name]()
    card = app._card_actions(state)  # type: ignore[arg-type]
    spec = LaunchSpec(task="t", model="m")

    def both(*calls: Callable[[], object]) -> list[tuple[object, ...]]:
        made.clear()
        for call in calls:
            call()
        return list(made)

    pane_health: health.HealthActions = handed["health"]  # type: ignore[assignment]
    pane_inbox: inbox.InboxActions = handed["inbox"]  # type: ignore[assignment]
    session: dict[str, Callable[[NodeId], None]] = handed["session"]  # type: ignore[assignment]

    assert (
        both(
            lambda: card.interrupt(ROOT),
            lambda: pane_health.interrupt(ROOT),
            lambda: pane_inbox.interrupt(ROOT),
            lambda: session["interrupt"](ROOT),
        )
        == [("interrupt", ROOT)] * 4
    )
    assert (
        both(
            lambda: card.close(ROOT),
            lambda: pane_health.close(ROOT),
            lambda: pane_inbox.close(ROOT),
            lambda: session["close"](ROOT),
        )
        == [("close", ROOT)] * 4
    )
    assert (
        both(lambda: card.fork(spec), lambda: pane_health.fork(spec))
        == [("launch", spec, "fork")] * 2
    )
    assert (
        both(lambda: card.relaunch(spec), lambda: pane_inbox.relaunch(spec))
        == [("launch", spec, "relaunch")] * 2
    )

    state.bridge.reset_mock()  # type: ignore[attr-defined]
    card.dismiss(ROOT)
    pane_inbox.dismiss(ROOT)
    emitted = [call.args for call in state.bridge.emit.call_args_list]  # type: ignore[attr-defined]
    assert emitted == [(FailureAcknowledged(ROOT),)] * 2


def test_narrowing_from_the_menu_narrows_the_session_healths_button_narrows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptmstr import app

    state = _app_state(monkeypatch, _store())
    session = MagicMock()
    state.pool = MagicMock()  # type: ignore[attr-defined]
    state.pool.session_for.return_value = session  # type: ignore[attr-defined]
    handed: dict[str, health.HealthActions] = {}
    monkeypatch.setattr(health, "draw", lambda *a, **kw: handed.update(health=a[2]))
    app._panels(state)["HEALTH"]()  # type: ignore[arg-type]

    app._card_actions(state).revoke_policy(ROOT)  # type: ignore[arg-type]
    assert session.mock_calls == [(("revoke_policy"), (), {})]
    handed["health"].revoke_policy(ROOT)
    assert [call[0] for call in session.mock_calls] == ["revoke_policy"] * 2
    assert [call.args for call in state.pool.session_for.call_args_list] == [  # type: ignore[attr-defined]
        (ROOT,),
        (ROOT,),
    ]


def test_resume_opens_the_resume_form_for_the_cards_session_and_launches_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """
    The form and not New Task: the session, its directory and its team are the
    card's and are not fields. Nothing the operator had in the launcher is
    touched, and nothing reaches the pool until the form's own button.
    """
    from pptmstr import app

    where = str(tmp_path)
    state = _app_state(monkeypatch, _store(AgentState.CANCELLED))
    made = _pool_calls(monkeypatch)
    rec = state.frame_snap.nodes[ROOT].with_(cwd=where, template="feature")  # type: ignore[attr-defined]
    state.launcher.cwd = "/somewhere/else"  # type: ignore[attr-defined]
    state.launcher.task = "half a thought"  # type: ignore[attr-defined]

    app._card_actions(state).resume(rec)  # type: ignore[arg-type]

    draft = state.launcher  # type: ignore[attr-defined]
    assert not draft._open_requested and draft.picker.selected is None and made == []
    assert draft.task == "half a thought", "what the operator had typed is still theirs"
    forms = state.resume  # type: ignore[attr-defined]
    assert forms.showing == "s1" and forms._open_requested
    form = forms.form
    assert (form.facts.session_id, form.facts.cwd, form.facts.task) == ("s1", where, rec.task)
    assert form.template == "feature" and form.team_known
    assert form.policy is Policy.STRICT


def test_asking_to_resume_a_session_again_keeps_what_was_typed_on_its_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    from pptmstr import app

    state = _app_state(monkeypatch, _store(AgentState.CANCELLED))
    rec = state.frame_snap.nodes[ROOT].with_(cwd=str(tmp_path))  # type: ignore[attr-defined]
    app._card_actions(state).resume(rec)  # type: ignore[arg-type]
    state.resume.form.message = "pick up at step 3"  # type: ignore[attr-defined]
    for _ in range(2):
        app._card_actions(state).resume(rec)  # type: ignore[arg-type]
        assert state.resume.showing == "s1"  # type: ignore[attr-defined]
        assert state.resume.form.message == "pick up at step 3"  # type: ignore[attr-defined]


def test_resume_as_opens_the_same_form_under_the_rung_that_was_chosen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """
    The gate menu's "Resume as …": the choice already made, and still only a form.
    """
    from pptmstr import app

    state = _app_state(monkeypatch, _store(AgentState.CANCELLED))
    made = _pool_calls(monkeypatch)
    rec = state.frame_snap.nodes[ROOT].with_(cwd=str(tmp_path))  # type: ignore[attr-defined]
    resume_as = app._card_actions(state).resume_as  # type: ignore[arg-type]
    assert resume_as is not None
    resume_as(rec, Policy.AUTONOMOUS)

    assert state.resume.form.policy is Policy.AUTONOMOUS and made == []  # type: ignore[attr-defined]
    # And the plain item puts it back to strict, whatever was chosen before.
    app._card_actions(state).resume(rec)  # type: ignore[arg-type]
    assert state.resume.form.policy is Policy.STRICT  # type: ignore[attr-defined]


def test_health_puts_the_cursor_on_the_session_and_asks_for_the_pane_and_its_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptmstr import app
    from pptmstr.ui.focus import OnNode

    state = _app_state(monkeypatch, _store())
    app._card_actions(state).health(OTHER)  # type: ignore[arg-type]

    assert state.focus.target == OnNode(OTHER, pinned=True)  # type: ignore[attr-defined]
    assert state.pending_layout == "FOCUS"  # type: ignore[attr-defined]
    assert state.pending_pane == "HEALTH"  # type: ignore[attr-defined]


def test_health_asks_for_no_layout_when_the_one_with_the_pane_is_showing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptmstr import app

    state = _app_state(monkeypatch, _store(), shown="FOCUS")
    app._card_actions(state).health(ROOT)  # type: ignore[arg-type]

    assert state.pending_layout is None  # type: ignore[attr-defined]
    assert state.pending_pane == "HEALTH"  # type: ignore[attr-defined]


def test_health_on_a_session_that_owes_something_lands_on_what_it_owes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a click on the card does, through the same call."""
    from pptmstr import app
    from pptmstr.ui.focus import OnObligation

    store = _store()
    store.apply(_parked("p1", OTHER, 6.0), now=6.0)
    state = _app_state(monkeypatch, store)
    app._card_actions(state).health(OTHER)  # type: ignore[arg-type]

    assert isinstance(state.focus.target, OnObligation)  # type: ignore[attr-defined]
    assert state.focus.node(state.frame_snap) == OTHER  # type: ignore[attr-defined]


def test_the_frame_brings_the_pane_forward_after_the_layout_it_lives_in_is_switched_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptmstr import app
    from pptmstr.bridge import Bridge

    order: list[str] = []
    layouts = _Layouts("TRIAGE")
    monkeypatch.setattr(layouts, "switch_layout", lambda name: order.append(f"layout {name}"))
    monkeypatch.setattr(app, "hello_imgui", layouts)
    monkeypatch.setattr(app, "_apply_theme_if_dirty", lambda state: None)
    monkeypatch.setattr(app, "_handle_keys", lambda state: None)
    state = app.AppState(store=_store(), bridge=Bridge(), settings=Settings())
    state.runner = MagicMock()
    state.runner.docking_params.focus_dockable_window.side_effect = lambda name: order.append(
        f"pane {name}"
    )
    state.pending_layout, state.pending_pane = "FOCUS", "HEALTH"

    app.begin_frame(state)
    app.begin_frame(state)

    assert order == ["layout FOCUS", "pane HEALTH"], "once, and in that order"
    assert state.pending_pane is None


# -- frames ---------------------------------------------------------------------------


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


class _Fleet:
    """
    The frame as the application runs it, as far as a card's menu is concerned:
    ``app._handle_keys``, the FLEET pane as ``app._panels`` builds it, and
    ``app._draw_overlays``. The actions are recorded in place of the pool.

    ``cards`` is where each card was last drawn, in the order the pane drew them,
    and ``items`` where each line of an open menu was, by its label.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, store: Store, shown: str) -> None:
        from pptmstr import app

        self.app = app
        self.store = store
        self.layouts = _Layouts(shown)
        monkeypatch.setattr(app, "hello_imgui", self.layouts)
        self.state = app.AppState(store=store, bridge=MagicMock(), settings=Settings())
        # The queue draws only for a host with a pool. An empty one: the sessions
        # here are the store's, and no action reaches the pool (``_actions``).
        self.state.pool = SessionPool(self.state.bridge, cap=1)
        self.actions = _actions()
        self.gate: dict[NodeId, Policy | None] = {ROOT: Policy.PERMISSIVE, OTHER: Policy.STRICT}
        monkeypatch.setattr(app, "_card_actions", lambda state: self.actions)
        monkeypatch.setattr(app, "_policy_of", lambda state, node: self.gate.get(node))
        # What the menus are told beyond the record and the gate. The shell
        # says three things of its own (``app._card_wiring``, held to in
        # tests/test_app.py); here they are left out unless a test says them
        # (``_told``), so each test states the facts its menu was drawn from.
        self.told: dict[str, object] = {}
        wiring = session_menu.Wiring

        def told_only(**given: object) -> session_menu.Wiring:
            said = {k: given[k] for k in ("state", "actions", "gate")}
            return wiring(**said, **self.told)  # type: ignore[arg-type]

        monkeypatch.setattr(session_menu, "Wiring", told_only)
        monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)
        self.cards: list[tuple[float, float]] = []
        self.items: dict[str, tuple[float, float]] = {}
        self.said: list[str] = []
        self.dim: list[str] = []
        self.box = False
        self._focused = False
        self._monkeypatch = monkeypatch

    def __enter__(self) -> _Fleet:
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 900.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        cards, items, said, dim = self.cards, self.items, self.said, self.dim

        def middle() -> tuple[float, float]:
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            return ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)

        class Cards:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            @staticmethod
            def invisible_button(label: str, size: imgui.ImVec2) -> bool:
                pressed = imgui.invisible_button(label, size)
                if label == "##card":
                    cards.append(middle())
                return pressed

        class Items:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            @staticmethod
            def selectable(label: str, selected: bool) -> tuple[bool, bool]:
                result = imgui.selectable(label, selected)
                items[label.split("##")[0]] = middle()
                if selected:
                    said.append(f"> {label.split('##')[0]}")
                return result

            @staticmethod
            def button(label: str) -> bool:
                pressed = imgui.button(label)
                items[label] = middle()
                return pressed

            @staticmethod
            def text_colored(colour: object, text: str) -> None:
                said.append(text)
                imgui.text_colored(colour, text)  # type: ignore[arg-type]

            @staticmethod
            def text_disabled(text: str) -> None:
                dim.append(text)
                imgui.text_disabled(text)

        self._monkeypatch.setattr(rail, "imgui", Cards())
        self._monkeypatch.setattr(session_menu, "imgui", Items())
        self.frame(3)
        return self

    def __exit__(self, *exc: object) -> None:
        imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        state = self.state
        for _ in range(count):
            snap = self.store.snapshot()
            self.cards.clear()
            self.items.clear()
            self.said.clear()
            self.dim.clear()
            imgui.new_frame()
            state.frame_snap = snap
            state.frame_now = 50.0
            state.cards.prune(snap)
            state.focus.settle(snap)
            self.app._handle_keys(state)
            imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
            imgui.set_next_window_size(imgui.ImVec2(320.0, 880.0))
            imgui.begin("FLEET")
            self.app._panels(state)["FLEET"]()
            imgui.end()
            # The queue, as it is docked beside the fleet: a key approves only a
            # call a pane that shows parked calls drew whole (``review.withheld``).
            imgui.set_next_window_pos(imgui.ImVec2(720.0, 0.0))
            imgui.set_next_window_size(imgui.ImVec2(460.0, 880.0))
            imgui.begin("NEEDS YOU")
            self.app._panels(state)["NEEDS YOU"]()
            imgui.end()
            imgui.set_next_window_pos(imgui.ImVec2(400.0, 0.0))
            imgui.set_next_window_size(imgui.ImVec2(300.0, 200.0))
            imgui.begin("pane")
            if self.box:
                if not self._focused:
                    imgui.set_keyboard_focus_here()
                    self._focused = True
                imgui.input_text("##box", "")
            else:
                self._focused = False
            imgui.end()
            self.app._draw_overlays(state)
            imgui.end_frame()

    def click(self, at: tuple[float, float], button: int = 0) -> None:
        self.io.add_mouse_pos_event(*at)
        self.frame(2)
        self.io.add_mouse_button_event(button, True)
        self.frame()
        self.io.add_mouse_button_event(button, False)
        self.frame(2)

    def key(self, key: imgui.Key, char: str = "") -> None:
        self.io.add_key_event(key, True)
        if char:
            self.io.add_input_characters_utf8(char)
        self.frame()
        self.io.add_key_event(key, False)
        self.frame()

    @property
    def open(self) -> bool:
        """Whether a card's menu was drawn on the last frame."""
        cards = self.state.cards
        return bool(self.items) and cards.confirming is None and cards.gating is None

    def called(self) -> dict[str, list[tuple[object, ...]]]:
        return _called(self.actions)

    def approved(self) -> list[str]:
        return [call.args[0] for call in self.state.bridge.resolve.call_args_list]

    def type_then_lose_the_box(self) -> None:
        self.box = True
        self.frame(3)
        assert self.io.want_text_input, "the box did take the keyboard"
        self.io.add_input_characters_utf8("more")
        self.frame(2)
        self.box = False
        self.frame(3)
        assert not self.io.want_capture_keyboard, "the keyboard is free again"
        assert self.state.review.hold.held


@pytest.fixture
def fleet(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Fleet]:
    """
    Two sessions in one project, both thinking, the first with a builder. The
    second has a call parked, so the cursor starts on it and ``a`` would approve.
    """
    store = _store()
    store.apply(_parked("p1", OTHER, 6.0), now=6.0)
    with _Fleet(monkeypatch, store, "TRIAGE") as real:
        yield real


def test_the_harness_draws_a_card_per_session_and_its_keys_reach_the_shortcuts(
    fleet: _Fleet,
) -> None:
    assert len(fleet.cards) == 2, "the group is collapsed: one card a session"
    assert not fleet.open

    fleet.key(imgui.Key.a, "a")
    assert fleet.approved() == ["p1"]


def test_a_right_click_on_a_card_opens_that_cards_menu(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)

    assert fleet.open
    assert list(fleet.items) == [
        "Interrupt",
        "Fork",
        "strict",
        f"autonomous  restart…  -  {session_menu.NOT_WIRED.words}",
        "Details",
        "Health",
        "Close…",
    ]
    assert fleet.called() == {}, "opening a menu does nothing"


def test_a_right_click_does_not_move_the_cursor(fleet: _Fleet) -> None:
    """The cursor is what ``a`` answers for. Looking at a card's menu is not a move."""
    before = fleet.state.focus.target
    fleet.click(fleet.cards[0], button=1)

    assert fleet.state.focus.target == before
    assert fleet.state.focus.node(fleet.state.frame_snap) == OTHER


def test_a_left_click_on_a_card_opens_no_menu(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0])
    assert not fleet.open


def test_a_menu_is_the_card_it_was_opened_on_not_the_one_the_cursor_is_on(
    fleet: _Fleet,
) -> None:
    fleet.gate[ROOT] = Policy.STRICT
    fleet.gate[OTHER] = Policy.AUTONOMOUS
    fleet.click(fleet.cards[1], button=1)
    fleet.click(fleet.items["strict"])

    assert fleet.called() == {"revoke_policy": [(OTHER,)]}


def test_clicking_an_item_does_it_once_and_closes_the_menu(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["Interrupt"])

    assert fleet.called() == {"interrupt": [(ROOT,)]}
    assert not fleet.open


def test_a_refused_item_is_on_screen_with_its_reason_and_a_click_on_it_does_nothing(
    fleet: _Fleet,
) -> None:
    fleet.gate[ROOT] = Policy.STRICT
    fleet.click(fleet.cards[0], button=1)
    refused = f"permissive…  -  {session_menu.NOT_WIRED.words}"
    assert refused in fleet.items

    fleet.click(fleet.items[refused])
    assert fleet.called() == {}
    assert fleet.open, "a click on a greyed line is not a choice, and not a dismissal"


def test_a_card_no_session_is_held_for_refuses_every_lever_that_needs_one(
    fleet: _Fleet,
) -> None:
    """The fake driver's sessions: on screen, live, and in no pool."""
    fleet.gate[ROOT] = None
    fleet.click(fleet.cards[0], button=1)

    why = session_menu.NOT_HELD.words
    assert f"Interrupt  -  {why}" in fleet.items
    assert session_menu.GATE_NOT_HELD.words in fleet.dim
    assert session_menu.GATE_NOT_HELD not in fleet.dim, "the sentence is not drawn"
    assert not [label for label in fleet.items if "strict" in label], "and no row to choose"
    assert f"Close…  -  {why}" in fleet.items
    fleet.click(fleet.items[f"Close…  -  {why}"])
    assert fleet.called() == {} and fleet.state.cards.confirming is None


def test_a_click_elsewhere_closes_the_menu_and_does_nothing(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)
    fleet.click((900.0, 700.0))

    assert not fleet.open
    assert fleet.called() == {}


def test_a_right_click_on_another_card_moves_the_menu_to_it(fleet: _Fleet) -> None:
    fleet.gate[OTHER] = Policy.AUTONOMOUS
    first, second = fleet.cards
    fleet.click(first, button=1)
    fleet.click(second, button=1)

    assert fleet.open
    fleet.click(fleet.items["strict"])
    assert fleet.called() == {"revoke_policy": [(OTHER,)]}


def test_a_sub_agents_card_offers_details_alone(fleet: _Fleet) -> None:
    fleet.state.rail.expanded.add("s1")
    fleet.frame(2)
    assert len(fleet.cards) == 3
    fleet.click(fleet.cards[1], button=1)

    assert list(fleet.items) == ["Details"]


def test_an_ended_cards_menu_resumes_through_the_action_and_ends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _Fleet(monkeypatch, _store(AgentState.FAILED), "TRIAGE") as fleet:
        fleet.click(fleet.cards[0], button=1)
        why = session_menu.NOT_WIRED.words
        assert list(fleet.items) == [
            "Resume…",
            "Fork",
            f"strict…  -  {why}",
            f"autonomous…  -  {why}",
            "Details",
            "Retry in a new session",
            "Dismiss",
            f"Close  -  {session_menu.RUN_NOT_KNOWN.words}",
            f"Remove from fleet  -  {why}",
        ]
        fleet.click(fleet.items["Resume…"])
        rec = fleet.store.snapshot().nodes[ROOT]
        assert fleet.called() == {"resume": [(rec,)]}


# -- the key ---------------------------------------------------------------------------


def test_m_opens_the_menu_of_the_card_the_cursor_is_on(fleet: _Fleet) -> None:
    fleet.key(imgui.Key.m, "m")

    assert fleet.open
    fleet.key(imgui.Key.down_arrow)
    fleet.key(imgui.Key.down_arrow)
    assert [line for line in fleet.said if line.startswith("> ")] == ["> Fork"]
    fleet.key(imgui.Key.enter)
    spec = LaunchSpec.from_record(fleet.store.snapshot().nodes[OTHER])
    assert fleet.called() == {"fork": [(spec,)]}, "the cursor was on the second session"
    assert fleet.approved() == []


def test_m_then_enter_does_nothing_because_a_menu_opens_with_nothing_highlighted(
    fleet: _Fleet,
) -> None:
    """
    The first item of a working session's menu is Interrupt. Opened on it, two
    keys -- one of them the layout key -- would abandon a turn.
    """
    fleet.key(imgui.Key.m, "m")
    assert not [line for line in fleet.said if line.startswith("> ")]
    fleet.key(imgui.Key.enter)

    assert fleet.called() == {}
    assert fleet.open
    assert fleet.layouts.switched == []


def test_an_open_menu_says_which_keys_it_answers(fleet: _Fleet) -> None:
    said: list[str] = []
    real = imgui.text_disabled
    fleet._monkeypatch.setattr(
        imgui, "text_disabled", lambda text: (said.append(text), real(text))[1]
    )
    fleet.key(imgui.Key.m, "m")

    assert session_menu.KEYS_HINT in said
    assert "audit the mirror" in said, "and which card it is the menu of"


def test_the_menu_the_key_opens_is_beside_its_card_wherever_the_pointer_is(
    fleet: _Fleet,
) -> None:
    fleet.io.add_mouse_pos_event(1100.0, 800.0)
    fleet.frame(2)
    card = fleet.cards[1]
    fleet.key(imgui.Key.m, "m")

    x, y = fleet.items["Fork"]
    assert abs(y - card[1]) < 120.0 and x < 400.0


def test_an_m_typed_after_a_text_box_vanished_opens_no_menu(fleet: _Fleet) -> None:
    """
    The hazard ``review.Hold`` exists for, at the new key: the box goes from under
    the typist, and the next letter of the sentence is ``m``.
    """
    fleet.type_then_lose_the_box()
    for _ in range(3):
        fleet.key(imgui.Key.m, "m")

    assert not fleet.open
    assert not fleet.state.cards.open_from_key


def test_m_answers_again_once_escape_has_ended_the_hold(fleet: _Fleet) -> None:
    fleet.type_then_lose_the_box()
    fleet.key(imgui.Key.escape)
    fleet.key(imgui.Key.m, "m")

    assert fleet.open


def test_an_m_typed_into_a_box_that_is_still_there_opens_no_menu(fleet: _Fleet) -> None:
    fleet.box = True
    fleet.frame(3)
    fleet.key(imgui.Key.m, "m")

    assert not fleet.open


def test_ctrl_m_is_not_the_key(fleet: _Fleet) -> None:
    fleet.io.add_key_event(imgui.Key.mod_ctrl, True)
    fleet.key(imgui.Key.m)
    fleet.io.add_key_event(imgui.Key.mod_ctrl, False)
    fleet.frame()

    assert not fleet.open


def test_m_with_the_launcher_open_opens_no_menu(fleet: _Fleet) -> None:
    fleet.state.launcher.is_open = True
    fleet.key(imgui.Key.m, "m")

    assert not fleet.open


def test_an_ask_no_card_took_is_gone_on_the_next_frame(fleet: _Fleet) -> None:
    """
    The fleet pane was not drawn, or nothing has the cursor. The ask must not wait
    for a card that takes it on some later frame.
    """
    cards = fleet.state.cards
    cards.open_from_key = True
    assert not cards.claim_keys()
    assert not cards.open_from_key


def test_the_menu_bars_entry_opens_the_focused_cards_menu_on_the_next_frame(
    fleet: _Fleet,
) -> None:
    fleet.state.cards.ask_next_frame = True
    fleet.frame(2)

    assert fleet.open


def test_the_menu_bar_names_the_key_the_handler_reads() -> None:
    """A key with no menu entry is a key nobody finds, and one that names another lies."""
    import inspect

    from pptmstr import app

    assert app.CARD_MENU_KEY is imgui.Key.m
    assert '"Controls for the focused card…", "M"' in inspect.getsource(app._menus)
    assert "cards.ask_next_frame = True" in inspect.getsource(app._menus)


# -- the keys while a menu is open ----------------------------------------------------


def test_keyboard_navigation_is_off_so_a_popup_reads_no_key_by_itself(fleet: _Fleet) -> None:
    """
    The premise the menu's own key handling rests on. Were it false -- the flag
    turned on -- every key below would be read twice.
    """
    assert not fleet.io.config_flags & imgui.ConfigFlags_.nav_enable_keyboard
    fleet.click(fleet.cards[0], button=1)
    assert fleet.open
    assert not fleet.io.want_capture_keyboard, "an open popup does not claim the keyboard"


def test_an_a_pressed_over_an_open_menu_approves_nothing(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)
    for key, char in ((imgui.Key.a, "a"), (imgui.Key.j, "j"), (imgui.Key.r, "r")):
        fleet.key(key, char)

    assert fleet.approved() == []
    assert fleet.state.review.focus_reason is False
    assert fleet.open


def test_the_arrows_move_the_highlight_and_enter_chooses_what_it_is_on(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)
    assert not [line for line in fleet.said if line.startswith("> ")], "the mouse opened it"

    fleet.key(imgui.Key.down_arrow)
    fleet.key(imgui.Key.down_arrow)
    assert [line for line in fleet.said if line.startswith("> ")] == ["> Fork"]
    fleet.key(imgui.Key.up_arrow)
    assert [line for line in fleet.said if line.startswith("> ")] == ["> Interrupt"]
    fleet.key(imgui.Key.enter)

    assert fleet.called() == {"interrupt": [(ROOT,)]}
    assert not fleet.open


def test_enter_in_a_menu_does_not_switch_the_layout(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)
    fleet.key(imgui.Key.down_arrow)
    fleet.key(imgui.Key.enter)

    assert fleet.layouts.switched == []


def test_enter_with_nothing_highlighted_chooses_nothing(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)
    fleet.key(imgui.Key.enter)

    assert fleet.called() == {}
    assert fleet.layouts.switched == []
    assert fleet.open


def test_escape_closes_the_menu_and_switches_no_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store()
    with _Fleet(monkeypatch, store, "FOCUS") as fleet:
        fleet.click(fleet.cards[0], button=1)
        fleet.key(imgui.Key.escape)

        assert not fleet.open
        assert fleet.called() == {}
        assert fleet.layouts.switched == [], "FOCUS reads Escape as a return to TRIAGE"

        fleet.key(imgui.Key.escape)
        assert fleet.layouts.switched == ["TRIAGE"], "and does again once the menu is gone"


def test_the_shortcuts_answer_again_once_the_menu_is_closed(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)
    fleet.key(imgui.Key.escape)
    fleet.key(imgui.Key.a, "a")

    assert fleet.approved() == ["p1"]


def test_a_menu_whose_pane_stops_being_drawn_does_not_keep_the_keys(fleet: _Fleet) -> None:
    """
    ``shown`` is set by whoever draws and cleared by the frame. A flag the drawer
    had to clear would be left standing by a pane that was closed under its menu.
    """
    fleet.click(fleet.cards[0], button=1)
    assert fleet.state.cards.shown
    assert fleet.state.cards.claim_keys()
    assert not fleet.state.cards.claim_keys(), "nothing drew it in between"


# -- closing a session that is mid-turn -----------------------------------------------


def test_close_on_a_session_mid_turn_asks_first_and_closes_on_the_answer(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["Close…"])

    assert fleet.called() == {}
    assert fleet.state.cards.confirming == ROOT
    assert any("mid-turn (thinking)" in line for line in fleet.said)

    fleet.click(fleet.items["close session"])
    assert fleet.called() == {"close": [(ROOT,)]}
    assert fleet.state.cards.confirming is None


def test_cancelling_the_question_closes_nothing(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["Close…"])
    fleet.click(fleet.items["cancel"])

    assert fleet.called() == {}
    assert fleet.state.cards.confirming is None
    fleet.frame(2)
    assert "close session" not in fleet.items


def test_the_enter_that_chose_close_is_not_the_enter_that_confirms_it(fleet: _Fleet) -> None:
    """
    The whole of the keyboard's route, and the hazard in it: the confirmation is
    drawn on the frame the key that asked for it is still down.
    """
    fleet.click(fleet.cards[0], button=1)
    fleet.key(imgui.Key.up_arrow)
    assert [line for line in fleet.said if line.startswith("> ")] == ["> Close…"]

    fleet.io.add_key_event(imgui.Key.enter, True)
    fleet.frame(40)
    assert fleet.called() == {}, "held down for two thirds of a second, it confirms nothing"
    assert fleet.state.cards.confirming == ROOT
    fleet.io.add_key_event(imgui.Key.enter, False)
    fleet.frame()

    fleet.key(imgui.Key.enter)
    assert fleet.called() == {"close": [(ROOT,)]}
    assert fleet.layouts.switched == []


def test_escape_cancels_the_question_and_switches_no_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _Fleet(monkeypatch, _store(), "FOCUS") as fleet:
        fleet.click(fleet.cards[0], button=1)
        fleet.click(fleet.items["Close…"])
        fleet.key(imgui.Key.escape)

        assert fleet.called() == {}
        assert fleet.state.cards.confirming is None
        assert fleet.layouts.switched == []


def test_a_key_pressed_over_the_question_approves_nothing(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["Close…"])
    fleet.key(imgui.Key.a, "a")

    assert fleet.approved() == []
    assert fleet.called() == {}


def test_a_session_that_ends_while_the_question_is_up_takes_the_question_with_it(
    fleet: _Fleet,
) -> None:
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["Close…"])
    fleet.store.apply(AgentFinished(ROOT, AgentState.CANCELLED, 9.0), now=9.0)
    fleet.frame(3)

    assert fleet.state.cards.confirming is None
    assert "close session" not in fleet.items
    assert fleet.called() == {}
    fleet.key(imgui.Key.a, "a")
    assert fleet.approved() == ["p1"], "and the keys are the shortcuts' again"


def test_the_question_says_so_when_the_turn_it_was_asked_over_has_ended(
    fleet: _Fleet,
) -> None:
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["Close…"])
    fleet.store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT), now=9.0)
    fleet.frame(2)

    assert any("has ended since" in line for line in fleet.said)
    assert not any("mid-turn" in line for line in fleet.said)
    fleet.click(fleet.items["close session"])
    assert fleet.called() == {"close": [(ROOT,)]}


def test_what_the_question_says_follows_the_session() -> None:
    assert "mid-turn (REVIEW)" in session_menu.confirm_text(
        record(ROOT, AgentState.AWAITING_APPROVAL)
    )
    assert "has ended since" in session_menu.confirm_text(record(ROOT, AgentState.AWAITING_INPUT))


@pytest.mark.parametrize("state", [AgentState.THINKING, AgentState.AWAITING_INPUT])
def test_the_close_question_is_one_sentence(state: AgentState) -> None:
    said = session_menu.confirm_text(record(ROOT, state))
    assert said.endswith(".") and ". " not in said


# -- Details, from the menu ------------------------------------------------------------


def test_details_opens_the_modal_for_the_card_and_reads_the_disk_once(
    fleet: _Fleet, monkeypatch: pytest.MonkeyPatch
) -> None:
    looks: list[NodeId] = []
    monkeypatch.setattr(
        session_details,
        "look",
        lambda rec, reported: looks.append(rec.node_id) or session_details.Looked(),
    )
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["Details"])
    fleet.frame(20)

    assert fleet.state.cards.details.node == ROOT
    assert looks == [ROOT], "at the click, and not on every frame after it"
    assert fleet.called() == {}


def test_a_key_pressed_over_details_approves_nothing_and_escape_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    store.apply(_parked("p1", OTHER, 6.0), now=6.0)
    monkeypatch.setattr(session_details, "look", lambda rec, reported: session_details.Looked())
    with _Fleet(monkeypatch, store, "FOCUS") as fleet:
        fleet.click(fleet.cards[0], button=1)
        fleet.click(fleet.items["Details"])
        fleet.key(imgui.Key.a, "a")
        fleet.key(imgui.Key.enter)
        assert fleet.approved() == [] and fleet.layouts.switched == []
        assert fleet.state.cards.details.node == ROOT

        fleet.key(imgui.Key.escape)
        assert fleet.state.cards.details.node is None
        assert fleet.layouts.switched == []

        fleet.key(imgui.Key.a, "a")
        assert fleet.approved() == ["p1"]


def test_an_m_pressed_over_details_opens_no_menu_and_leaves_details_up(
    fleet: _Fleet, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    No text box has the keyboard, so the hold is free: what stops the key is that
    a modal has it.
    """
    monkeypatch.setattr(session_details, "look", lambda rec, reported: session_details.Looked())
    fleet.state.cards.details.ask(ROOT)
    fleet.frame(3)
    assert fleet.state.review.hold.free and fleet.io.want_capture_keyboard
    fleet.key(imgui.Key.m, "m")

    assert not fleet.open
    assert fleet.state.cards.details.node == ROOT


def test_the_frame_lets_go_of_what_the_menus_kept_for_a_node_that_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through ``app.begin_frame``: a prune nothing calls prunes nothing."""
    from pptmstr import app
    from pptmstr.bridge import Bridge

    monkeypatch.setattr(app, "hello_imgui", _Layouts("TRIAGE"))
    monkeypatch.setattr(app, "_apply_theme_if_dirty", lambda state: None)
    monkeypatch.setattr(app, "_handle_keys", lambda state: None)
    state = app.AppState(store=_store(), bridge=Bridge(), settings=Settings())
    cards = state.cards
    cards.details.ask(("nowhere", None))
    cards.confirming = ("nowhere", None)
    cards.keyed = ("nowhere", None)

    app.begin_frame(state)
    assert cards.details.node is None and cards.confirming is None and cards.keyed is None

    cards.details.ask(ROOT)
    cards.confirming = cards.keyed = ROOT
    app.begin_frame(state)
    assert cards.details.node == ROOT and cards.confirming == ROOT and cards.keyed == ROOT


# -- the Gate group --------------------------------------------------------------------

ALL_WIRED = frozenset({Action.REMOVE, Action.RESUME_AS, Action.SET_GATE})
SAID: list[bool | None] = [None, False, True]


def _gate_group(offered: tuple[session_menu.Entry, ...]) -> list[session_menu.Entry]:
    """What is under the Gate heading: everything up to the next ungrouped line."""
    at = offered.index(Heading("Gate")) + 1
    group: list[session_menu.Entry] = []
    for entry in offered[at:]:
        if not isinstance(entry, Item | Line) or not entry.grouped:
            break
        group.append(entry)
    return group


def _card(policy: Policy, live: bool) -> tuple[AgentRecord, Policy | None]:
    """A root launched under ``policy``, and the live gate reading for it."""
    state = AgentState.THINKING if live else AgentState.DONE
    return record(ROOT, state).with_(policy=policy), policy if live else None


@pytest.mark.parametrize(
    ("policy", "contained", "live"), list(itertools.product(Policy, SAID, [True, False]))
)
def test_the_gate_group_is_gate_menus_rows_one_line_a_rung_and_nothing_of_its_own(
    policy: Policy, contained: bool | None, live: bool
) -> None:
    rec, reading = _card(policy, live)
    offered = session_menu.entries(rec, reading, Facts(contained=contained, wired=ALL_WIRED))
    group = _gate_group(offered)
    unsaid = Line(session_menu.CONTAINMENT_NOT_KNOWN)
    lines = [entry for entry in group if entry != unsaid]
    rows = gate_menu.rows(policy, contained is True, live, adds=gate_adds)

    assert len(lines) == len(rows) == len(Policy)
    for entry, row in zip(lines, rows, strict=True):
        if row.kind is gate_menu.Kind.CURRENT:
            assert entry == Line(row.short, row.consequence)
            continue
        assert isinstance(entry, Item)
        live_move = row.kind in (gate_menu.Kind.NARROW_LIVE, gate_menu.Kind.WIDEN_LIVE)
        assert entry.action is (Action.SET_GATE if live_move else Action.RESUME_AS)
        assert (entry.rung, entry.note, entry.grouped) == (row.rung, row.consequence, True)
        assert (entry.label, entry.question, entry.sentence) == (
            row.short,
            row.label,
            row.sentence,
        )
        assert entry.enabled
    # Nobody said whether a running process is contained: the rows are the ones
    # for one that is not, and the group says so. An ended one is not asked.
    assert (unsaid in group) is (live and contained is None)
    if live and contained is None:
        assert lines == [
            entry
            for entry in _gate_group(
                session_menu.entries(rec, reading, Facts(contained=False, wired=ALL_WIRED))
            )
        ]


@pytest.mark.parametrize("policy", Policy)
@pytest.mark.parametrize("containment", [None, "", " ", "{}", '{"sandbox": {}}'])
def test_no_gate_item_is_one_the_driver_would_refuse(
    policy: Policy, containment: str | None
) -> None:
    """
    Against a real session, with containment judged by the function the shell is
    to use: every live move the menu offers is taken, and every rung it offers
    only as a resume is one the running process refuses.
    """
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession, UncontainedAutonomy

    contained = session_details.is_contained(containment)
    rec, reading = _card(policy, True)
    offered = session_menu.entries(rec, reading, Facts(contained=contained, wired=ALL_WIRED))
    items = [entry for entry in _gate_group(offered) if isinstance(entry, Item)]
    assert len(items) == len(Policy) - 1

    for item in items:
        assert item.rung is not None
        session = AgentSession(Bridge(), "t", policy=policy, containment=containment)
        if item.action is Action.SET_GATE:
            session.set_policy(item.rung)
            assert session.policy is item.rung
        else:
            assert item.action is Action.RESUME_AS
            with pytest.raises(UncontainedAutonomy):
                session.set_policy(item.rung)


@pytest.mark.parametrize("containment", [None, "", " ", "\n\t", "{}", " {} ", "x"])
def test_contained_is_the_drivers_guards_answer_and_not_whether_a_string_was_given(
    containment: str | None,
) -> None:
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    session = AgentSession(Bridge(), "t", containment=containment)
    needing = [rung for rung in Policy if requires_containment(rung)]
    assert needing
    for rung in needing:
        assert session_details.is_contained(containment) is not session._unpaired(rung)


def test_a_narrowed_sessions_rows_are_measured_from_the_rung_it_is_on_now() -> None:
    """The record keeps the rung it was launched under. The session moved."""
    rec = record(ROOT, AgentState.THINKING).with_(policy=Policy.AUTONOMOUS)
    group = _gate_group(
        session_menu.entries(rec, Policy.STRICT, Facts(contained=True, wired=ALL_WIRED))
    )

    assert group[0] == _now(Policy.STRICT)
    assert [e.label for e in group[1:] if isinstance(e, Item)] == [
        "permissive…",
        "autonomous…",
    ]


def _recalled(
    policy: Policy | None = Policy.PERMISSIVE,
    transcript: TranscriptPresence = TranscriptPresence.PRESENT,
) -> AgentRecord:
    """A root as ``SessionRecalled`` leaves it, with what its file said."""
    store = Store()
    store.apply(
        SessionRecalled(
            session_id="s1",
            task="rotate the signing key",
            model="claude-opus-5",
            recalled=Recalled(transcript=transcript, policy=policy, ended_how=EndedHow.CLOSED),
            cwd="/x/keys",
        ),
        now=5.0,
    )
    return store.snapshot().nodes[ROOT]


def test_a_closed_cards_rows_are_measured_from_the_rung_its_file_recorded() -> None:
    rec = _recalled(Policy.PERMISSIVE)
    assert rec.policy is Policy.STRICT, "the record's own field says nothing"
    group = _gate_group(session_menu.entries(rec, None, Facts(wired=ALL_WIRED)))

    assert [e.label for e in group] == [
        "strict…",
        "permissive (last run)",
        "autonomous…",
    ]


@pytest.mark.parametrize("wired", [frozenset(), ALL_WIRED])
def test_a_closed_card_whose_file_names_no_rung_has_no_gate_rows_and_says_why(
    wired: frozenset[Action],
) -> None:
    offered = session_menu.entries(_recalled(None), None, Facts(wired=wired))

    assert _gate_group(offered) == [Line(session_menu.GATE_NOT_KNOWN)]
    assert "Resume…" in session_menu.GATE_NOT_KNOWN, "it names the item that chooses one"
    assert session_menu.find(offered, Action.RESUME_AS) is None
    assert session_menu.find(offered, Action.RESUME) == Item(Action.RESUME, "Resume…")


@pytest.mark.parametrize(
    ("policy", "contained", "live"), list(itertools.product(Policy, SAID, [True, False]))
)
def test_a_row_that_widens_a_running_sessions_gate_asks_first_and_a_narrowing_never_does(
    policy: Policy, contained: bool | None, live: bool
) -> None:
    """
    The line in the menu is a rung's name, so the question is where a move that
    releases something says what it releases -- every widening of a running
    session, to a rung that needs containment or not, and the move that ends its
    run to get there.
    """
    rec, reading = _card(policy, live)
    facts = Facts(contained=contained, wired=ALL_WIRED)
    items = [
        e for e in _gate_group(session_menu.entries(rec, reading, facts)) if isinstance(e, Item)
    ]
    ladder = list(Policy)

    for item in items:
        assert item.rung is not None
        widens = ladder.index(item.rung) > ladder.index(policy)
        # Asked only of a running session: for one that has ended the row opens
        # the resume form, which is itself the step before anything starts.
        assert item.asks is (widens and live)
        assert item.label.endswith("…") is (item.asks or item.action is Action.RESUME_AS)

        actions, state = _wired(), session_menu.MenuState()
        session_menu.choose(item.action, rec, reading, state, actions, rung=item.rung, facts=facts)
        if item.asks:
            assert _called(actions) == {}, "nothing moves at the first step"
            assert state.gating == session_menu.GateAsk(
                ROOT, item.action, item.rung, item.question, item.sentence, live
            )
            # One sentence, and it is part of the whole account.
            assert item.sentence and item.sentence in item.note
            assert item.sentence != item.note
        else:
            assert state.gating is None
            if item.action is Action.RESUME_AS:
                assert _called(actions) == {"resume_as": [(rec, item.rung)]}
            elif item.rung is ladder[0]:
                assert _called(actions) == {"revoke_policy": [(ROOT,)]}
            else:
                assert _called(actions) == {"set_policy": [(ROOT, item.rung)]}


def test_a_gate_row_is_chosen_by_its_rung_and_not_by_being_first() -> None:
    rec, reading = _card(Policy.AUTONOMOUS, True)
    facts = Facts(contained=True, wired=ALL_WIRED)
    offered = session_menu.entries(rec, reading, facts)
    strict = session_menu.find(offered, Action.SET_GATE, Policy.STRICT)
    permissive = session_menu.find(offered, Action.SET_GATE, Policy.PERMISSIVE)
    assert strict is not None and permissive is not None and strict != permissive
    assert strict.key == (Action.SET_GATE, Policy.STRICT)
    assert session_menu.at(offered, permissive.key) is permissive
    assert Item(Action.FORK, "Fork").key is Action.FORK

    # The highlight passes over each row once.
    walked: list[session_menu.Key | None] = [None]
    for _ in range(4):
        walked.append(session_menu.step(offered, walked[-1], 1))
    assert walked[1:] == [Action.INTERRUPT, Action.FORK, strict.key, permissive.key]

    actions = _wired()
    session_menu.choose(
        Action.SET_GATE,
        rec,
        reading,
        session_menu.MenuState(),
        actions,
        rung=Policy.PERMISSIVE,
        facts=facts,
    )
    assert _called(actions) == {"set_policy": [(ROOT, Policy.PERMISSIVE)]}


def test_a_lever_the_build_did_not_supply_is_greyed_and_choosing_it_calls_nothing() -> None:
    """
    ``facts.wired`` can claim more than ``actions`` has. What acts is read off
    the actions.
    """
    assert _actions().wired == frozenset()
    assert _wired().wired == ALL_WIRED
    assert dataclasses.replace(_actions(), detach=MagicMock()).wired == {Action.REMOVE}

    claimed = Facts(snap=_snapshot("done"), run_held=False, contained=True, wired=ALL_WIRED)
    for action, rec, reading, rung in (
        (Action.SET_GATE, *_card(Policy.STRICT, True), Policy.PERMISSIVE),
        (Action.RESUME_AS, *_card(Policy.STRICT, False), Policy.PERMISSIVE),
        # The two that would ask first: a question about a lever nothing pulls.
        (Action.SET_GATE, *_card(Policy.STRICT, True), Policy.AUTONOMOUS),
        (Action.RESUME_AS, *_card(Policy.STRICT, False), Policy.AUTONOMOUS),
        (Action.REMOVE, *_card(Policy.STRICT, False), None),
    ):
        item = session_menu.find(session_menu.entries(rec, reading, claimed), action, rung)
        assert item is not None and item.enabled, action
        actions, state = _actions(), session_menu.MenuState()
        session_menu.choose(action, rec, reading, state, actions, rung=rung, facts=claimed)
        assert _called(actions) == {} and state.gating is None, action


# -- Remove from fleet ----------------------------------------------------------------


def _snapshot(shape: str) -> Snapshot:
    """
    One session in the store, ended in the way ``shape`` names: ``done`` and
    ``cancelled`` owe nothing and can leave, ``running`` has a sub-agent that has
    not ended, ``failed`` ended in a failure nobody closed, and ``owes`` is done
    with an approval still waiting on one of its nodes.
    """
    store = Store()
    for node, parent in ((ROOT, None), (BUILDER, ROOT)):
        store.apply(
            AgentSpawned(
                node_id=node,
                parent=parent,
                task="rotate the signing key",
                model="claude-opus-5",
                started_at=0.0,
                agent_type="builder" if parent else None,
            ),
            now=1.0,
        )
    if shape != "running":
        store.apply(AgentFinished(BUILDER, AgentState.DONE, 2.0), now=2.0)
    end = {"failed": AgentState.FAILED, "cancelled": AgentState.CANCELLED}.get(
        shape, AgentState.DONE
    )
    store.apply(AgentFinished(ROOT, end, 3.0, error="x" if shape == "failed" else None), now=3.0)
    if shape == "owes":
        store.apply(_parked("p9", BUILDER, 4.0), now=4.0)
    return store.snapshot()


_SHAPES: dict[str, DetachRefusal | None] = {
    "done": None,
    "cancelled": None,
    "running": DetachRefusal.NOT_ENDED,
    "failed": DetachRefusal.FAILED_NOT_CLOSED,
    "owes": DetachRefusal.OWES,
}

_WORDS = {
    DetachRefusal.NOT_ENDED: "an agent of this session is still running",
    DetachRefusal.FAILED_NOT_CLOSED: "it failed and has not been closed — close it first",
    DetachRefusal.OWES: "it still needs you — answer or dismiss its rows first",
    DetachRefusal.NO_SUCH_SESSION: "the store holds no session under this id",
}


def test_every_refusal_the_store_can_give_has_words() -> None:
    assert session_menu.REMOVAL_REFUSED == _WORDS
    assert set(_WORDS) == set(DetachRefusal)


@pytest.mark.parametrize(
    ("shape", "run_held", "policy"),
    list(itertools.product(_SHAPES, SAID, [None, Policy.STRICT])),
)
def test_remove_is_greyed_with_the_reason_until_the_store_and_the_pool_both_let_it_go(
    shape: str, run_held: bool | None, policy: Policy | None
) -> None:
    """
    The fixture is checked against the store first: a shape that stopped being
    refused the way its name says would leave this asserting the wrong row.
    """
    snap = _snapshot(shape)
    refusal = _SHAPES[shape]
    assert snap.detach_refusal("s1") is refusal
    rec = snap.nodes[ROOT]
    facts = Facts(snap=snap, run_held=run_held, wired=ALL_WIRED)
    offered = session_menu.entries(rec, policy, facts)

    if refusal is not None:
        want: str | None = _WORDS[refusal]
    elif run_held is True or (run_held is None and policy is not None):
        # The pool's answer rules where it gave one: it keeps the object of a
        # session whose run ended by itself, so a gate reading alone is not a run.
        want = "a process is still attached to it"
    elif run_held is None:
        want = "not known whether a process is still attached"
    else:
        want = None
    assert offered[-2:] == (Rule(), Item(Action.REMOVE, "Remove from fleet", want))

    actions = _wired()
    session_menu.choose(Action.REMOVE, rec, policy, session_menu.MenuState(), actions, facts=facts)
    assert _called(actions) == ({"detach": [(ROOT,)]} if want is None else {})


def test_remove_is_refused_by_a_menu_the_store_was_not_asked_for() -> None:
    rec = _snapshot("done").nodes[ROOT]
    facts = Facts(run_held=False, wired=ALL_WIRED)
    item = session_menu.find(session_menu.entries(rec, None, facts), Action.REMOVE)

    assert item == Item(Action.REMOVE, "Remove from fleet", session_menu.STORE_NOT_ASKED)


def test_remove_on_a_session_the_snapshot_does_not_have_is_refused_in_words() -> None:
    rec = record(OTHER, AgentState.DONE)
    facts = Facts(snap=_snapshot("done"), run_held=False, wired=ALL_WIRED)
    item = session_menu.find(session_menu.entries(rec, None, facts), Action.REMOVE)

    assert item is not None and item.refused == _WORDS[DetachRefusal.NO_SUCH_SESSION]


@pytest.mark.parametrize(
    ("node", "state", "policy"),
    [
        (node, state, policy)
        for node, state, policy in itertools.product([ROOT, BUILDER], AgentState, GATES)
        if node == BUILDER or state in LIVE
    ],
)
def test_a_card_that_has_not_ended_and_a_sub_agents_card_do_not_offer_to_be_removed(
    node: NodeId, state: AgentState, policy: Policy | None
) -> None:
    facts = Facts(snap=_snapshot("done"), run_held=False, contained=True, wired=ALL_WIRED)
    offered = session_menu.entries(record(node, state), policy, facts)

    assert session_menu.find(offered, Action.REMOVE) is None
    assert not [e for e in offered if isinstance(e, Item) and "emove" in e.label]


@pytest.mark.parametrize("state", ENDED)
def test_every_ended_roots_card_offers_to_be_removed(state: AgentState) -> None:
    offered = session_menu.entries(record(ROOT, state), None)
    assert [e.action for e in offered if isinstance(e, Item)].count(Action.REMOVE) == 1


@pytest.mark.parametrize("presence", TranscriptPresence)
@pytest.mark.parametrize("run_held", SAID)
def test_a_closed_card_whose_transcript_is_gone_offers_details_and_removal_only(
    presence: TranscriptPresence, run_held: bool | None
) -> None:
    store = Store()
    rec = _recalled(Policy.PERMISSIVE, presence)
    store.apply(SessionRecalled("s1", rec.task, rec.model, Recalled(transcript=presence)), now=1.0)
    snap = store.snapshot()
    assert snap.detach_refusal("s1") is None
    facts = Facts(snap=snap, run_held=run_held, wired=ALL_WIRED)
    offered = session_menu.entries(rec, None, facts)
    actions = [e.action for e in offered if isinstance(e, Item)]

    removal = Item(
        Action.REMOVE,
        "Remove from fleet",
        {
            None: "not known whether a process is still attached",
            True: "a process is still attached to it",
            False: None,
        }[run_held],
    )
    if presence is TranscriptPresence.GONE:
        assert offered == (Item(Action.DETAILS, "Details"), Rule(), removal)
    else:
        assert actions == [
            Action.RESUME,
            Action.FORK,
            Action.RESUME_AS,
            Action.RESUME_AS,
            Action.DETAILS,
            Action.REMOVE,
        ]
        assert offered[-1] == removal


# -- what the fleet pane hands a menu ---------------------------------------------------


def test_a_wiring_that_supplies_nothing_tells_the_menu_nothing() -> None:
    state = session_menu.MenuState()
    bare = session_menu.Wiring(state=state, actions=_actions(), gate=lambda node: None)
    assert bare.facts(ROOT) == Facts() == session_menu.NOTHING_SAID

    snap = _snapshot("done")
    asked: list[tuple[str, NodeId]] = []
    full = session_menu.Wiring(
        state=state,
        actions=_wired(),
        gate=lambda node: None,
        snap=snap,
        running=lambda node: (asked.append(("running", node)), False)[1],
        contained=lambda node: (asked.append(("contained", node)), True)[1],
    )
    assert full.facts(OTHER) == Facts(snap=snap, run_held=False, contained=True, wired=ALL_WIRED)
    assert asked == [("running", OTHER), ("contained", OTHER)]


# -- the Gate rows in frames ------------------------------------------------------------


def _told(monkeypatch: pytest.MonkeyPatch, fleet: _Fleet, **said: object) -> None:
    """
    Wire the menu's own levers and hand the fleet pane what ``said`` says, in
    place of what the shell would say: ``app._panels`` builds its ``Wiring`` by
    name on every frame (``_Fleet.told``).
    """
    fleet.actions = _wired()
    fleet.told = dict(said)
    fleet.frame(2)


def test_a_gate_row_is_drawn_as_its_rung_and_no_sentence_is_drawn_under_it(fleet: _Fleet) -> None:
    fleet.click(fleet.cards[0], button=1)

    drawn = [*fleet.dim, *fleet.said, *fleet.items]
    for row in gate_menu.rows(Policy.PERMISSIVE, False, True, adds=gate_adds):
        assert row.consequence not in drawn and row.sentence not in drawn, row.label
    assert "permissive (now)" in fleet.dim and "permissive (now)" not in fleet.items
    assert session_menu.CONTAINMENT_NOT_KNOWN.words in fleet.dim
    assert session_menu.CONTAINMENT_NOT_KNOWN not in fleet.dim


def test_what_a_menu_draws_dim_is_its_headings_its_lines_and_the_keys_and_nothing_else(
    fleet: _Fleet,
) -> None:
    """
    The property the menu is held to: nothing is drawn in it but labels. Dim text
    is where a sentence under an item was drawn, so everything dim is accounted
    for: the card's title, a heading, a line nobody chooses, the keys.
    """
    for card, node in ((0, ROOT), (1, OTHER)):
        fleet.click(fleet.cards[card], button=1)
        assert fleet.open
        rec = fleet.store.snapshot().nodes[node]
        offered = session_menu.entries(rec, fleet.gate[node], Facts())
        allowed = {session_menu.KEYS_HINT, rec.task}
        allowed |= {e.label for e in offered if isinstance(e, Heading)}
        allowed |= {e.text for e in offered if isinstance(e, Line)}
        assert set(fleet.dim) <= allowed, set(fleet.dim) - allowed
        assert not fleet.said, "and nothing in a colour"
        assert not [text for text in fleet.dim if "(?)" in text]
        assert not [label for label in fleet.items if "(?)" in label]
        fleet.click((900.0, 700.0))


def test_a_live_widen_that_needs_no_containment_asks_first_too_with_what_it_releases(
    monkeypatch: pytest.MonkeyPatch, fleet: _Fleet
) -> None:
    """
    The menu line is "permissive…" and says nothing of what the rung releases, so
    the question is the one place the move is described before it is made.
    """
    _told(monkeypatch, fleet)
    fleet.gate[ROOT] = Policy.STRICT
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["permissive…"])

    assert fleet.called() == {}, "nothing moves at the click"
    row = _gate_row(Policy.STRICT, Policy.PERMISSIVE, live=True)
    assert "shell reads run without asking" in row.sentence
    assert row.sentence in fleet.said and "Widen to permissive" in fleet.said
    assert row.consequence not in fleet.said, "one sentence, not the whole account"

    fleet.click(fleet.items["move to permissive"])
    assert fleet.called() == {"set_policy": [(ROOT, Policy.PERMISSIVE)]}
    assert fleet.state.cards.gating is None and not fleet.open


def test_a_widen_to_a_rung_that_needs_containment_asks_first_and_moves_on_the_answer(
    monkeypatch: pytest.MonkeyPatch, fleet: _Fleet
) -> None:
    _told(monkeypatch, fleet, contained=lambda node: True)
    fleet.click(fleet.cards[0], button=1)
    assert "autonomous  restart…" not in fleet.items, "a contained process can be moved"
    fleet.click(fleet.items["autonomous…"])

    assert fleet.called() == {}
    asked = fleet.state.cards.gating
    assert asked is not None and (asked.node, asked.action) == (ROOT, Action.SET_GATE)
    row = _gate_row(Policy.PERMISSIVE, Policy.AUTONOMOUS, live=True, contained=True)
    assert row.sentence in fleet.said and row.consequence not in fleet.said

    fleet.click(fleet.items["move to autonomous"])
    assert fleet.called() == {"set_policy": [(ROOT, Policy.AUTONOMOUS)]}
    assert fleet.state.cards.gating is None
    fleet.frame(2)
    assert "move to autonomous" not in fleet.items


def test_continuing_as_a_rung_the_process_cannot_take_asks_first_then_opens_the_form(
    monkeypatch: pytest.MonkeyPatch, fleet: _Fleet
) -> None:
    _told(monkeypatch, fleet, contained=lambda node: False)
    fleet.click(fleet.cards[0], button=1)
    assert "autonomous…" not in fleet.items, "the driver would refuse it"
    fleet.click(fleet.items["autonomous  restart…"])

    assert fleet.called() == {}
    row = _gate_row(Policy.PERMISSIVE, Policy.AUTONOMOUS, live=True)
    assert "ends the running session where it stands" in row.sentence
    assert row.sentence in fleet.said and row.consequence not in fleet.said

    fleet.click(fleet.items["open the resume form"])
    rec = fleet.store.snapshot().nodes[ROOT]
    assert fleet.called() == {"resume_as": [(rec, Policy.AUTONOMOUS)]}
    assert fleet.state.cards.gating is None


def test_cancelling_the_gate_question_moves_nothing(
    monkeypatch: pytest.MonkeyPatch, fleet: _Fleet
) -> None:
    _told(monkeypatch, fleet, contained=lambda node: True)
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["autonomous…"])
    fleet.click(fleet.items["cancel"])

    assert fleet.called() == {}
    assert fleet.state.cards.gating is None
    fleet.frame(2)
    assert "move to autonomous" not in fleet.items


def test_the_enter_that_chose_a_gate_row_is_not_the_enter_that_confirms_it(
    monkeypatch: pytest.MonkeyPatch, fleet: _Fleet
) -> None:
    _told(monkeypatch, fleet, contained=lambda node: True)
    fleet.click(fleet.cards[0], button=1)
    for _ in range(4):
        fleet.key(imgui.Key.down_arrow)
    assert [line for line in fleet.said if line.startswith("> ")] == ["> autonomous…"]

    fleet.io.add_key_event(imgui.Key.enter, True)
    fleet.frame(40)
    assert fleet.called() == {}, "held down, it confirms nothing"
    assert fleet.state.cards.gating is not None
    fleet.io.add_key_event(imgui.Key.enter, False)
    fleet.frame()

    fleet.key(imgui.Key.enter)
    assert fleet.called() == {"set_policy": [(ROOT, Policy.AUTONOMOUS)]}
    assert fleet.layouts.switched == []


def test_escape_cancels_the_gate_question_and_a_key_over_it_approves_nothing(
    monkeypatch: pytest.MonkeyPatch, fleet: _Fleet
) -> None:
    _told(monkeypatch, fleet, contained=lambda node: True)
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["autonomous…"])

    fleet.key(imgui.Key.a, "a")
    assert fleet.approved() == []
    fleet.key(imgui.Key.escape)
    assert fleet.called() == {} and fleet.state.cards.gating is None
    assert fleet.layouts.switched == []


def test_a_session_that_ends_while_the_gate_question_is_up_takes_the_question_with_it(
    monkeypatch: pytest.MonkeyPatch, fleet: _Fleet
) -> None:
    _told(monkeypatch, fleet, contained=lambda node: True)
    fleet.click(fleet.cards[0], button=1)
    fleet.click(fleet.items["autonomous…"])
    assert "move to autonomous" in fleet.items

    fleet.store.apply(AgentFinished(ROOT, AgentState.DONE, 9.0), now=9.0)
    fleet.frame(3)
    assert fleet.state.cards.gating is None
    assert "move to autonomous" not in fleet.items
    assert fleet.called() == {}

    fleet.key(imgui.Key.a, "a")
    assert fleet.approved() == ["p1"], "and the keyboard is free again"


def test_a_gate_question_stands_only_while_its_session_is_as_it_was_asked() -> None:
    ask = session_menu.GateAsk(ROOT, Action.SET_GATE, Policy.AUTONOMOUS, "l", "c", live=True)
    closed = dataclasses.replace(ask, action=Action.RESUME_AS, live=False)

    assert session_menu.gate_ask_stands(ask, record(ROOT, AgentState.THINKING))
    assert not session_menu.gate_ask_stands(ask, record(ROOT, AgentState.DONE))
    assert not session_menu.gate_ask_stands(ask, None)
    assert session_menu.gate_ask_stands(closed, record(ROOT, AgentState.DONE))
    assert not session_menu.gate_ask_stands(closed, record(ROOT, AgentState.THINKING))


def test_the_menus_keep_no_gate_question_for_a_node_that_is_gone() -> None:
    snap = _snapshot("done")
    state = session_menu.MenuState()
    state.gating = session_menu.GateAsk(OTHER, Action.SET_GATE, Policy.AUTONOMOUS, "l", "c", True)
    state.gating_frames = 3
    state.prune(snap)
    assert state.gating is None and state.gating_frames == 0

    kept = session_menu.GateAsk(ROOT, Action.RESUME_AS, Policy.AUTONOMOUS, "l", "c", False)
    state.gating = kept
    state.prune(snap)
    assert state.gating is kept


def test_an_ended_card_is_removed_through_the_action_once_the_shell_says_no_run_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(AgentState.DONE)
    store.apply(AgentFinished(BUILDER, AgentState.DONE, 3.0), now=3.0)
    with _Fleet(monkeypatch, store, "TRIAGE") as fleet:
        fleet.gate[ROOT] = None
        _told(monkeypatch, fleet, snap=store.snapshot())
        fleet.click(fleet.cards[0], button=1)
        unknown = f"Remove from fleet  -  {session_menu.RUN_NOT_KNOWN.words}"
        assert unknown in fleet.items
        fleet.click(fleet.items[unknown])
        assert fleet.called() == {}
        fleet.click((900.0, 700.0))

        _told(monkeypatch, fleet, snap=store.snapshot(), running=lambda node: False)
        fleet.click(fleet.cards[0], button=1)
        fleet.click(fleet.items["Remove from fleet"])
        assert fleet.called() == {"detach": [(ROOT,)]}


# -- one spelling, a failed card's Close, and a title that is not a task ---------------


def _every_label() -> set[str]:
    """
    Every item label the three menus can draw, over every state, gate and fact.
    """
    labels: set[str] = set()
    for state, policy, contained, held in itertools.product(AgentState, GATES, SAID, SAID):
        facts = Facts(contained=contained, run_held=held, wired=ALL_WIRED)
        for node in (ROOT, BUILDER):
            for entry in session_menu.entries(record(node, state), policy, facts):
                if isinstance(entry, Item):
                    labels.add(entry.label)
    return labels


def test_the_menu_calls_resume_what_every_pane_that_points_at_it_calls_it() -> None:
    """
    Five panes say "Resume… to continue" of a closed card (``widgets.closed_line``),
    and the item they mean is this menu's. One name, held here.
    """
    from pptmstr.ui import widgets

    offered = session_menu.entries(record(ROOT, AgentState.DONE), None)
    item = session_menu.find(offered, Action.RESUME)
    assert item is not None and item.label == widgets.RESUME_ITEM == "Resume…"
    line = widgets.closed_line(
        record(ROOT, AgentState.DONE).with_(recalled=Recalled()), unloaded=""
    )
    assert line is not None and item.label in line
    assert item.label in session_menu.GATE_NOT_KNOWN
    assert item.label in session_menu.TASK_NOT_RECORDED


def test_an_item_that_leads_to_a_second_step_ends_in_the_one_ellipsis() -> None:
    labels = _every_label()
    assert {"Resume…", "Close…", "autonomous  restart…", "autonomous…", "permissive…"} <= labels
    assert not [label for label in labels if "..." in label]


@pytest.mark.parametrize("acknowledged", [False, True])
def test_a_failed_card_offers_close_and_says_which_of_two_acts_it_will_be(
    acknowledged: bool,
) -> None:
    """
    A failed session stays on the fleet until it is closed, so the control that
    closes it has to be on its card. With a process attached Close ends it; with
    none it marks the session ended. The item's note says which, and is not
    drawn; both reach the one ``close`` action.
    """
    rec = record(ROOT, AgentState.FAILED, acknowledged=acknowledged)
    for held, note in (
        (True, session_menu.CLOSE_FAILED_RUN),
        (False, session_menu.CLOSE_FAILED_NO_RUN),
    ):
        facts = Facts(run_held=held, wired=ALL_WIRED)
        item = session_menu.find(session_menu.entries(rec, None, facts), Action.CLOSE)
        assert item == Item(Action.CLOSE, "Close", note=note)

        actions, state = _wired(), session_menu.MenuState()
        session_menu.choose(Action.CLOSE, rec, None, state, actions, facts=facts)
        assert _called(actions) == {"close": [(ROOT,)]}
        assert state.confirming is None, "nothing is mid-turn to confirm over"

    untold = session_menu.find(session_menu.entries(rec, None, Facts()), Action.CLOSE)
    assert untold is not None and untold.refused == session_menu.RUN_NOT_KNOWN
    actions = _wired()
    session_menu.choose(Action.CLOSE, rec, None, session_menu.MenuState(), actions)
    assert _called(actions) == {}


@pytest.mark.parametrize("state", [AgentState.DONE, AgentState.CANCELLED])
def test_an_ended_card_that_did_not_fail_has_no_close(state: AgentState) -> None:
    facts = Facts(run_held=False, wired=ALL_WIRED)
    assert (
        session_menu.find(session_menu.entries(record(ROOT, state), None, facts), Action.CLOSE)
        is None
    )


def _label_only(state: AgentState = AgentState.DONE) -> AgentRecord:
    """
    A closed card whose file held no task: its title is the CLI's label.
    """
    return record(ROOT, state).with_(
        task="Refactor the parser (summary)", recalled=Recalled(task_recorded=False)
    )


def test_a_card_titled_by_a_label_cannot_be_forked_or_retried_and_can_be_resumed() -> None:
    """
    F3. Fork and Retry launch ``LaunchSpec.from_record`` in one step, and its
    prompt is the record's task. On a card whose file recorded no task that is
    the CLI's summary of the conversation, and a paid session would be started
    with it as its instruction.
    """
    for rec in (
        _label_only(),
        _label_only(AgentState.FAILED),
        record(ROOT, AgentState.DONE).with_(task="  "),
    ):
        assert session_menu.task_missing(rec)
        facts = Facts(run_held=False, wired=ALL_WIRED)
        offered = session_menu.entries(rec, None, facts)
        fork = session_menu.find(offered, Action.FORK)
        assert fork is not None and fork.refused == session_menu.TASK_NOT_RECORDED
        retry = session_menu.find(offered, Action.RETRY)
        assert retry is None or retry.refused == session_menu.TASK_NOT_RECORDED
        assert (retry is not None) is (rec.state is AgentState.FAILED)

        for action in (Action.FORK, Action.RETRY):
            actions = _wired()
            session_menu.choose(action, rec, None, session_menu.MenuState(), actions, facts=facts)
            assert _called(actions) == {}, action
        resume = session_menu.find(offered, Action.RESUME)
        assert resume is not None and resume.enabled


def test_a_card_whose_file_recorded_its_task_forks_as_any_other() -> None:
    rec = record(ROOT, AgentState.DONE).with_(recalled=Recalled())
    assert not session_menu.task_missing(rec)
    actions = _wired()
    session_menu.choose(Action.FORK, rec, None, session_menu.MenuState(), actions)
    assert _called(actions) == {"fork": [(LaunchSpec.from_record(rec),)]}


def test_resume_as_on_an_ended_card_opens_the_form_with_no_question_in_front_of_it() -> None:
    """
    The form shows the rung and starts nothing until its own button: a question
    before it would be a confirmation of a confirmation. A running session's
    row to the same rung still asks.
    """
    for live, asks in ((False, False), (True, True)):
        rec, reading = _card(Policy.STRICT, live)
        facts = Facts(contained=False, wired=ALL_WIRED)
        actions, state = _wired(), session_menu.MenuState()
        session_menu.choose(
            Action.RESUME_AS, rec, reading, state, actions, rung=Policy.AUTONOMOUS, facts=facts
        )
        assert (state.gating is not None) is asks
        assert _called(actions) == ({} if asks else {"resume_as": [(rec, Policy.AUTONOMOUS)]})


# -- what the review of the gate and resume wiring found -------------------------------


@pytest.mark.parametrize("state", LIVE + [AgentState.FAILED, AgentState.DONE])
def test_a_session_resumed_under_a_label_cannot_be_forked_or_retried_while_it_runs_either(
    state: AgentState,
) -> None:
    """
    The record a resume announces is not a recalled one, and the hazard is that
    it stops being a label on that account: Fork on the running card, one click,
    starts a paid session whose prompt is the CLI's summary of another.
    """
    rec = record(ROOT, state).with_(task="Refactor the parser (summary)", task_is_label=True)
    assert session_menu.task_missing(rec)
    facts = Facts(run_held=not state.is_terminal, wired=ALL_WIRED)
    reading = None if state.is_terminal else Policy.STRICT
    offered = session_menu.entries(rec, reading, facts)
    fork = session_menu.find(offered, Action.FORK)
    assert fork is not None and fork.refused == session_menu.TASK_NOT_RECORDED
    retry = session_menu.find(offered, Action.RETRY)
    assert (retry is not None) is (state is AgentState.FAILED)
    assert retry is None or retry.refused == session_menu.TASK_NOT_RECORDED
    for action in (Action.FORK, Action.RETRY):
        actions = _wired()
        session_menu.choose(action, rec, reading, session_menu.MenuState(), actions, facts=facts)
        assert _called(actions) == {}, action

    # The control: the same card with its task on record forks.
    plain = record(ROOT, state)
    fork = session_menu.find(session_menu.entries(plain, reading, facts), Action.FORK)
    assert fork is not None and fork.enabled


def test_a_failed_session_with_a_process_attached_is_given_a_running_sessions_gate_rows() -> None:
    """
    An error result leaves the client connected and the root FAILED. The ended
    card's words for the gate are "the gate this session last ran under; it is
    not running", read off the record -- false of that process, and the wrong
    rung if it was narrowed while it ran. With a run held and a session object
    answering, the group is the one a running session gets: the object's rung,
    and a narrowing that acts on it.
    """
    launched_under = record(ROOT, AgentState.FAILED).with_(policy=_P)
    attached = Facts(run_held=True, contained=False, wired=ALL_WIRED)
    # Narrowed to strict while it ran: the object says strict, the record permissive.
    group = _gate_group(session_menu.entries(launched_under, _S, attached))
    assert group[0] == _now(_S)
    assert not [e for e in group if "it is not running" in e.note]
    assert [e.label for e in group if isinstance(e, Item)] == [
        "permissive…",
        "autonomous  restart…",
    ]

    on_permissive = _gate_group(session_menu.entries(launched_under, _P, attached))
    narrow = on_permissive[0]
    assert isinstance(narrow, Item) and narrow.label == "strict" and narrow.enabled
    actions = _wired()
    session_menu.choose(
        Action.SET_GATE,
        launched_under,
        _P,
        session_menu.MenuState(),
        actions,
        rung=_S,
        facts=attached,
    )
    assert _called(actions) == {"revoke_policy": [(ROOT,)]}

    # Its process gone, or nobody able to say: the ended card's words, off the record.
    for facts, reading in (
        (Facts(run_held=False, wired=ALL_WIRED), None),
        (Facts(run_held=False, wired=ALL_WIRED), _S),
        (Facts(run_held=None, wired=ALL_WIRED), _S),
        (Facts(run_held=True, wired=ALL_WIRED), None),
    ):
        assert not session_menu.process_attached(reading, facts)
        group = _gate_group(session_menu.entries(launched_under, reading, facts))
        assert Line("permissive (last run)", _consequence(_P, _P, live=False)) in group, (
            facts,
            reading,
        )
        assert not [e for e in group if isinstance(e, Item) and e.action is Action.SET_GATE]
