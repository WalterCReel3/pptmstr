#!/usr/bin/env python3
"""
Static mock of the card-rail + inbox layout, for looking at density decisions
with real font metrics and the real palette. Throwaway.

The rail is **no longer mocked**: the fixture is adapted into a ``Snapshot`` and
drawn by ``pptmstr.ui.rail`` itself, so what a capture shows is the shipped card
path rather than a second implementation of it. That was not a refinement --
while there were two, the fixture drifted and reproduced the very defect it was
built to catch. The inbox and the side panes are still drawn here, and are still
the thing to be sceptical of for the same reason.

No Store, no Bridge, no SDK, no intents: it builds frozen values by hand and runs
no threads. Deliberately not wired into the app.

    .venv/bin/python scripts/mock_cards.py --out /tmp/mock.png --view triage
    .venv/bin/python scripts/mock_cards.py --view question --theme light

Views: triage (cursor on an approval), question (cursor on a question),
empty (zero-state), fleet (rail only, wide, for density comparison).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from imgui_bundle import hello_imgui, imgui, immapp  # noqa: E402
from screenshot import write_png  # noqa: E402

from pptmstr import theme  # noqa: E402
from pptmstr.model import (  # noqa: E402
    AgentRecord,
    AgentState,
    ApprovalNeeded,
    ContextSnapshot,
    NodeId,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
    UsageRollup,
)
from pptmstr.model import Obligation as StoreObligation  # noqa: E402
from pptmstr.theme import STATE_GLYPH, P  # noqa: E402
from pptmstr.ui import rail, widgets  # noqa: E402
from pptmstr.ui.focus import FocusState, OnNode, OnObligation  # noqa: E402

NOW = 1000.0


# --------------------------------------------------------------------------
# Fixture. Shaped like the real records but not the real records: a mock that
# imports Snapshot starts wanting a Store, and then it is not a mock any more.
# --------------------------------------------------------------------------


def ctx(used: int, threshold: int, compactions: int = 0, model: str = "claude-sonnet-5"):
    return ContextSnapshot(
        used_tokens=used,
        max_tokens=200_000,
        raw_max_tokens=200_000,
        percentage=used / 200_000,
        auto_compact_enabled=True,
        auto_compact_threshold=threshold,
        model=model,
        polled_at=NOW,
        compactions=compactions,
    )


@dataclass
class Sub:
    """
    One sub-agent. Carries a topic, a model and a spend because the rail's
    expansion renders those per row -- the slots a sub-agent genuinely has, minus
    the context ring, which is session-scoped and has no per-sub-agent reading.
    """

    name: str
    state: AgentState
    topic: str = "working"
    model: str = "claude-haiku-4-5"
    cost_usd: float = 0.0


@dataclass
class Blocked:
    """
    One thing waiting on the operator, held by the session it belongs to.

    The rail badge, the sub-agent pip count and the inbox all read this list.
    They are not allowed separate copies: the first version of this mock authored
    the inbox and the card states independently and promptly rendered a screen
    showing "nothing needs you" beside two REVIEW badges, which is the exact
    failure the layout exists to prevent.
    """

    kind: str  # "approval" | "question" | "failure"
    who: str  # the node actually blocked; may be a sub-agent name
    summary: str
    since: float
    diff: str | None = None
    args: dict[str, str] | None = None
    detail: str | None = None


@dataclass
class Card:
    key: str
    name: str
    title: str
    project: str
    state: AgentState
    topic: str
    model: str
    context: ContextSnapshot | None
    cost_usd: float
    tokens: int
    started_at: float
    ended_at: float | None = None
    subs: list[Sub] = field(default_factory=list)
    blocked: list[Blocked] = field(default_factory=list)

    @property
    def waiting(self) -> int:
        """
        Everything this session wants from the operator -- approvals, questions
        and failures alike.

        Not ``approvals``. The first version of this counted only approvals, and a
        project whose sessions were merely stuck on a question and a crash showed a
        header reading "6 sessions" with no waiting count at all. That is defect 1
        of the proposal reproducing itself inside the fix for defect 1: the habit of
        treating an approval as the only kind of obligation is in the fingers, and
        it survives knowing better. Anything that counts must count the union.
        """
        return len(self.blocked)

    @property
    def pending(self) -> int:
        """Approvals only -- the subset that carries a diff to read."""
        return sum(1 for b in self.blocked if b.kind == "approval")

    @property
    def collapsed(self) -> bool:
        """
        Terminal, and so drawn as one line. How many lines that is belongs to
        ``pptmstr.ui.rail``, which this fixture now feeds rather than imitates;
        what stays here is the fixture invariant that a finished session cannot
        also be blocking (see ``check_fixture``).
        """
        return self.state in (AgentState.DONE, AgentState.CANCELLED)


@dataclass
class Obligation:
    kind: str
    card: str
    who: str
    summary: str
    since: float
    diff: str | None = None
    args: dict[str, str] | None = None
    detail: str | None = None


DIFF_WRITE = """--- /dev/null
+++ tests/test_bridge.py
@@ -0,0 +1,9 @@
+def test_parked_future_survives_a_late_state_change():
+    bridge = Bridge()
+    fut = bridge.park("s1-t1")
+    store.apply(StateChanged(node, AgentState.CALLING_TOOL))
+
+    assert store.snapshot().nodes[node].state is AgentState.AWAITING_APPROVAL
+    assert not fut.done()
"""

DIFF_EDIT = """--- pptmstr/theme.py
+++ pptmstr/theme.py
@@ -151,7 +151,7 @@
     ring_track=col(0x1F3E49),
-    diff_add=col(0x22D3B8),
+    diff_add=col(0x2FD9C0),
     diff_remove=col(0xFF5470),
"""

QUESTION = (
    "Two elements sets for the same NORAD id have epochs 40 minutes apart and\n"
    "disagree on mean motion in the 6th decimal. I can take the later epoch, or\n"
    "prefer the one from the operator-supplied file. Which do you want as the\n"
    "default, and should the other be logged or dropped silently?"
)

CARDS: list[Card] = [
    Card(
        key="c1",
        name="session",
        title="add a bridge regression test",
        project="pptmstr",
        # RUNNING_TOOL, not AWAITING_APPROVAL: the root is not the agent that parked.
        # Agent/Task are gated, so what this root is doing is waiting on the Agent
        # calls it made -- which is a tool running, and which is why it still throbs
        # while two of its sub-agents sit on the operator.
        state=AgentState.RUNNING_TOOL,
        topic="delegating to 2 sub-agents",
        model="claude-sonnet-5",
        context=ctx(90_000, 134_000),
        cost_usd=0.42,
        tokens=61_400,
        started_at=NOW - 612,
        subs=[
            Sub("Explore", AgentState.AWAITING_APPROVAL, "wants to write a test", cost_usd=0.07),
            Sub(
                "code-reviewer",
                AgentState.AWAITING_APPROVAL,
                "wants to edit theme.py",
                cost_usd=0.11,
            ),
            # Nothing reaps a finished sub-agent, so a session's expansion keeps
            # every one it ever had. That is what makes the height monotone, and it
            # is the case a fixture has to carry or nobody sees it.
            Sub("skeptic", AgentState.DONE, "checked the join", cost_usd=0.04),
        ],
        blocked=[
            Blocked(
                "approval", "Explore", "Write tests/test_bridge.py", NOW - 252, diff=DIFF_WRITE
            ),
            Blocked(
                "approval", "code-reviewer", "Edit pptmstr/theme.py", NOW - 121, diff=DIFF_EDIT
            ),
        ],
    ),
    Card(
        key="c2",
        name="session",
        title="audit store.py for lock-free reads",
        project="pptmstr",
        state=AgentState.THINKING,
        topic="reading store.py",
        model="claude-opus-5",
        context=ctx(148_000, 160_000),
        cost_usd=1.08,
        tokens=204_000,
        started_at=NOW - 1840,
    ),
    Card(
        key="c3",
        name="session",
        title="reconcile disagreeing TLE sets",
        project="orbital",
        state=AgentState.AWAITING_INPUT,
        topic="asked which TLE epoch to trust",
        model="claude-sonnet-5",
        context=ctx(52_000, 134_000, compactions=1),
        cost_usd=0.77,
        tokens=131_000,
        started_at=NOW - 2210,
        blocked=[
            Blocked(
                "question",
                "session",
                "which TLE epoch should it trust when two disagree?",
                NOW - 220,
                detail=QUESTION,
            )
        ],
    ),
    Card(
        key="c4",
        name="session",
        title="port the propagator to numpy",
        project="orbital",
        state=AgentState.FAILED,
        topic="hook timed out after 6h",
        model="claude-haiku-4-5-20251001",
        context=ctx(12_000, 134_000),
        cost_usd=0.03,
        tokens=8_200,
        started_at=NOW - 21600,
        ended_at=NOW - 30,
        blocked=[
            Blocked(
                "failure",
                "session",
                "session failed - hook timed out after 6h",
                NOW - 30,
                detail="PreToolUse hook exceeded APPROVAL_TIMEOUT_S with no operator attached.",
            )
        ],
    ),
    Card(
        key="c5",
        name="session",
        title="propagator benchmarks",
        project="orbital",
        state=AgentState.DONE,
        topic="propagator benchmarks",
        model="claude-sonnet-5",
        context=ctx(44_000, 134_000),
        cost_usd=0.31,
        tokens=57_000,
        started_at=NOW - 4200,
        ended_at=NOW - 900,
    ),
    Card(
        key="c6",
        name="session",
        title="run the suite before the commit",
        project="scratch",
        state=AgentState.AWAITING_APPROVAL,
        topic="running the test suite",
        model="claude-haiku-4-5-20251001",
        context=ctx(9_000, 134_000),
        cost_usd=0.01,
        tokens=6_100,
        started_at=NOW - 51,
        blocked=[
            Blocked(
                "approval",
                "session",
                "Bash  pytest -q",
                NOW - 51,
                args={"command": "pytest -q", "description": "run the test suite"},
            )
        ],
    ),
    Card(
        key="c7",
        name="session",
        title="find every call site of park()",
        project="scratch",
        state=AgentState.DONE,
        topic="one-off grep",
        model="claude-haiku-4-5-20251001",
        context=None,
        cost_usd=0.00,
        tokens=900,
        started_at=NOW - 8000,
        ended_at=NOW - 7900,
    ),
]


def build_obligations() -> list[Obligation]:
    """
    The inbox, projected from the cards. Oldest first.

    This is the shape ``Snapshot.needs_you`` has to take: one flattening of every
    blocking thing across every node, not three lists that agree by convention.
    """
    out = [
        Obligation(
            kind=b.kind,
            card=card.key,
            who=b.who,
            summary=b.summary,
            since=b.since,
            diff=b.diff,
            args=b.args,
            detail=b.detail,
        )
        for card in CARDS
        for b in card.blocked
    ]
    return sorted(out, key=lambda o: o.since)


def check_fixture() -> None:
    """
    Assert the card states agree with what is blocked on them.

    The app's own watchdog compares ``Bridge.parked_count`` to the review queue
    for exactly this reason: where an invariant spans two representations, check
    it at runtime rather than trusting both sides to stay in step. A mock that can
    render an impossible screen is worse than no mock.
    """
    for card in CARDS:
        # Scoped to the root, like the two checks below it. A parked state belongs to
        # the node that parked: the store sets AWAITING_APPROVAL on the approval's own
        # node and nothing propagates it upward, so a root whose sub-agent is waiting
        # is still RUNNING_TOOL -- the Agent call it made is the tool that is running.
        root = {b.kind for b in card.blocked if b.who == card.name}
        if "approval" in root and card.state is not AgentState.AWAITING_APPROVAL:
            raise AssertionError(f"{card.key}: approval pending but state is {card.state}")
        if "question" in root and card.state is not AgentState.AWAITING_INPUT:
            raise AssertionError(f"{card.key}: question pending but state is {card.state}")
        if "failure" in root and card.state is not AgentState.FAILED:
            raise AssertionError(f"{card.key}: failure listed but state is {card.state}")
        # The converse, which is the half that was missing. Checking only that a
        # parked card is in a parked state lets a card claim a state nothing parked
        # it in -- and that is what put a root in AWAITING_APPROVAL for its
        # sub-agents' calls, so every capture of an open group showed a root that
        # cannot exist.
        if card.state is AgentState.AWAITING_APPROVAL and "approval" not in root:
            raise AssertionError(f"{card.key}: parked on approval but owes none itself")
        for s in card.subs:
            owned = {b.kind for b in card.blocked if b.who == s.name}
            if s.state is AgentState.AWAITING_APPROVAL and "approval" not in owned:
                raise AssertionError(f"{card.key}/{s.name}: parked on approval but owes none")
        if card.collapsed and card.blocked:
            raise AssertionError(f"{card.key}: collapsed card cannot be blocking")


# Projects and titles for --sessions. Deliberately not random: a mock that draws a
# different picture each run cannot be used to compare two design decisions.
_PROJECTS = ("pptmstr", "orbital", "scratch", "ingest", "vendor-sync")
_TITLES = (
    "backfill the 2019 partitions",
    "chase a flaky integration test",
    "document the retry policy",
    "split the settings module",
    "add a smoke test for the probe",
    "trace a leaking subprocess",
    "rewrite the TLE parser",
    "measure cold-start latency",
    "unpick the circular import",
    "audit the hook timeouts",
    "port the CLI to argparse groups",
    "delete the dead fake driver",
    "profile the frame loop",
)
_STATES = (
    AgentState.THINKING,
    AgentState.RUNNING_TOOL,
    AgentState.AWAITING_APPROVAL,
    AgentState.DONE,
    AgentState.CALLING_TOOL,
    AgentState.AWAITING_INPUT,
    AgentState.THINKING,
    AgentState.DONE,
)


def scale_to(total: int) -> None:
    """Grow the fixture to ``total`` sessions, deterministically."""
    for i in range(len(CARDS), total):
        state = _STATES[i % len(_STATES)]
        project = _PROJECTS[(i * 3) % len(_PROJECTS)]
        title = _TITLES[i % len(_TITLES)]
        ended = NOW - (60 * (i % 7)) if state is AgentState.DONE else None
        card = Card(
            key=f"g{i}",
            name="session",
            title=title,
            project=project,
            state=state,
            topic=("waiting on you" if state is AgentState.AWAITING_INPUT else f"step {i % 5 + 1}"),
            model=("claude-opus-5" if i % 4 == 0 else "claude-sonnet-5"),
            context=ctx(
                20_000 + (i * 9_400) % 120_000, 134_000, compactions=1 if i % 6 == 0 else 0
            ),
            cost_usd=round(0.05 + (i % 9) * 0.23, 2),
            tokens=10_000 + i * 7_000,
            started_at=NOW - (120 + i * 137),
            ended_at=ended,
        )
        if state is AgentState.AWAITING_APPROVAL:
            card.blocked.append(
                Blocked(
                    "approval",
                    "session",
                    f"Edit {project}/module_{i}.py",
                    NOW - (40 + i * 11),
                    diff=DIFF_EDIT,
                )
            )
        elif state is AgentState.AWAITING_INPUT:
            card.blocked.append(
                Blocked(
                    "question",
                    "session",
                    f"needs a decision on {project} defaults",
                    NOW - (55 + i * 9),
                    detail=QUESTION,
                )
            )
        CARDS.append(card)


KIND_GLYPH = {
    "approval": STATE_GLYPH[AgentState.AWAITING_APPROVAL],
    "question": STATE_GLYPH[AgentState.AWAITING_INPUT],
    "failure": STATE_GLYPH[AgentState.FAILED],
}
KIND_ROLE = {
    "approval": "state_awaiting",
    "question": "state_awaiting_input",
    "failure": "state_failed",
}


@dataclass
class MockState:
    """The single cursor. The rail highlight is derived from it, never set beside it."""

    cursor: int = 0
    obligations: list[Obligation] = field(default_factory=list)

    @property
    def current(self) -> Obligation | None:
        if not self.obligations:
            return None
        return self.obligations[min(self.cursor, len(self.obligations) - 1)]

    @property
    def focused_card(self) -> str | None:
        cur = self.current
        return cur.card if cur else None


STATE = MockState()


def cards_by_project() -> list[tuple[str, list[Card]]]:
    groups: dict[str, list[Card]] = {}
    for c in CARDS:
        groups.setdefault(c.project, []).append(c)
    return list(groups.items())


def _small():
    imgui.push_font(None, 12.5)


def _normal():
    imgui.pop_font()


def _fmt_money(usd: float) -> str:
    return f"${usd:,.2f}"


def _waited(since: float) -> str:
    return widgets.format_elapsed(NOW - since)


# --------------------------------------------------------------------------
# The card rail
#
# Rendered by the real pane. This file used to carry a second implementation --
# its own density table, its own pip row -- and a fixture that reproduces the
# defect it exists to catch is worse than no fixture: the checked-in capture of
# the suppressed sub-agent row was taken here by someone who did not notice the
# pips had gone. So the fixture is adapted into a Snapshot and handed to
# ``pptmstr.ui.rail``, and there is one card path in the repository.
#
# Adapting costs the ~25 lines below. It also settles the objection recorded at
# the top of this file, that importing Snapshot means wanting a Store: it does
# not. A Snapshot is a frozen value and can be built by hand.
# --------------------------------------------------------------------------

# Presentation state the pane owns across frames -- which sessions are open, and
# whether the scroll has been pinned yet. The cursor is deliberately not here;
# see _focus.
_RAIL = rail.RailState()


def _node(card: Card, who: str) -> NodeId:
    """The node a fixture name refers to. A card's own name means its root."""
    return (card.key, None) if who == card.name else (card.key, who)


def _obligation(card: Card, b: Blocked) -> StoreObligation:
    node = _node(card, b.who)
    if b.kind == "question":
        return QuestionPending(node=node, since=b.since, summary=b.summary)
    if b.kind == "failure":
        return SessionFailed(node=node, since=b.since, summary=b.summary, error=b.summary)
    return ApprovalNeeded(
        node=node,
        since=b.since,
        summary=b.summary,
        approval=PendingApproval(
            id=f"{card.key}:{b.who}",
            node=node,
            tool_name="Edit",
            tool_use_id=f"tu-{card.key}",
            raw_args=b.args or {},
            summary=b.summary,
            requested_at=b.since,
        ),
    )


def _record(card: Card, sub: Sub | None) -> AgentRecord:
    """
    One fixture entry as the record the panes actually read.

    ``cwd`` is synthesised from the project name because grouping is derived from
    the directory, not stored -- ``projects.project_key`` walks to a git root and
    falls back to the last component, which for a path that does not exist is the
    project name back again.
    """
    root = sub is None
    return AgentRecord(
        node_id=(card.key, None if root else sub.name),  # type: ignore[union-attr]
        parent=None if root else (card.key, None),
        depth=0 if root else 1,
        state=card.state if root else sub.state,  # type: ignore[union-attr]
        topic=card.topic if root else sub.topic,  # type: ignore[union-attr]
        task=card.title if root else sub.name,  # type: ignore[union-attr]
        model=card.model if root else sub.model,  # type: ignore[union-attr]
        agent_type=None if root else sub.name,  # type: ignore[union-attr]
        cwd=f"/mock/{card.project}",
        usage=UsageRollup(
            total_cost_usd=card.cost_usd if root else sub.cost_usd  # type: ignore[union-attr]
        ),
        context=card.context if root else None,
        started_at=card.started_at,
        ended_at=card.ended_at if root else None,
    )


def _snapshot() -> Snapshot:
    """The fixture as one immutable world, rebuilt each frame like the real app."""
    records: list[AgentRecord] = []
    for card in CARDS:
        records.append(_record(card, None))
        records.extend(_record(card, s) for s in card.subs)
    owed = sorted((_obligation(c, b) for c in CARDS for b in c.blocked), key=lambda o: o.since)
    return Snapshot(
        seq=1,
        nodes=MappingProxyType({r.node_id: r for r in records}),
        order=tuple(r.node_id for r in records),
        needs_you=tuple(owed),
        any_active=any(c.state.is_active for c in CARDS),
    )


def _focus(snap: Snapshot) -> FocusState:
    """
    The mock's cursor, expressed the way the pane expects it.

    Rebuilt from ``STATE`` every frame rather than kept beside it. ``MockState``
    is the single cursor here, and a second one that the rail could write to is
    the two-cursor defect this layout exists to remove -- so a click on a card
    moves nothing, which is what a static fixture should do.
    """
    cur = STATE.current
    if cur is None:
        return FocusState()
    card = next(c for c in CARDS if c.key == cur.card)
    node = _node(card, cur.who)
    owed = next((o for o in snap.needs_you if o.node == node and o.kind.value == cur.kind), None)
    return FocusState(target=OnObligation(owed.key) if owed else OnNode(node, pinned=True))


def draw_fleet() -> None:
    snap = _snapshot()
    rail.draw(snap, _focus(snap), _RAIL, NOW)


def _ellipsis(text: str, width_px: float) -> str:
    if imgui.calc_text_size(text).x <= width_px:
        return text
    out = text
    while out and imgui.calc_text_size(out + "…").x > width_px:
        out = out[:-1]
    return out + "…"


# --------------------------------------------------------------------------
# The inbox
# --------------------------------------------------------------------------


def draw_inbox() -> None:
    if not STATE.obligations:
        _draw_zero_state()
        return

    imgui.spacing()
    total = len(STATE.obligations)
    oldest = _waited(min(o.since for o in STATE.obligations))
    imgui.text_colored(P.state_awaiting.vec4, f"{total} need you")
    imgui.same_line()
    imgui.text_colored(P.text_dim.vec4, f"· oldest {oldest} · j/k move · a approve · Tab next")
    imgui.separator()
    imgui.spacing()

    for i, ob in enumerate(STATE.obligations):
        _draw_row(i, ob, expanded=i == STATE.cursor)


def _draw_row(index: int, ob: Obligation, expanded: bool) -> None:
    card = next(c for c in CARDS if c.key == ob.card)
    colour = getattr(P, KIND_ROLE[ob.kind])
    draw = imgui.get_window_draw_list()
    lh = imgui.get_text_line_height_with_spacing()

    imgui.push_id(f"ob{index}")
    origin = imgui.get_cursor_screen_pos()
    width = imgui.get_content_region_avail().x
    imgui.invisible_button("##row", imgui.ImVec2(max(width, 40.0), lh + 6.0))
    hovered = imgui.is_item_hovered()

    lo = imgui.ImVec2(origin.x, origin.y)
    hi = imgui.ImVec2(origin.x + width, origin.y + lh + 6.0)
    if expanded or hovered:
        draw.add_rect_filled(lo, hi, P.panel_alt.u32)
    if expanded:
        draw.add_rect(lo, hi, P.focus.u32, 0.0, 2.0)
        draw.add_rect_filled(lo, imgui.ImVec2(lo.x + 3.0, hi.y), P.focus.u32)

    # Fixed columns, every field ellipsized to its own zone. Laying these out with
    # same_line() lets a long agent name push the wait time under the summary,
    # which is exactly the column you scan down.
    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + 10.0, origin.y + 3.0))
    imgui.text_colored(colour.vec4, KIND_GLYPH[ob.kind])

    # Identity is the session's task, not the node's name.
    #
    # At twenty sessions this column read "session" on six rows out of eight, which
    # is the same defect the cards had and it survived the first fix because the
    # inbox was looked at with seven. The sub-agent name is still needed -- an
    # approval belongs to the node that made the call -- but it is the qualifier,
    # not the identifier.
    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + 34.0, origin.y + 3.0))
    imgui.text_colored(P.text_strong.vec4, _ellipsis(card.title, 210.0))

    _small()
    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + 254.0, origin.y + 5.0))
    qualifier = card.project if ob.who == card.name else f"{card.project} / {ob.who}"
    imgui.text_colored(P.text_dim.vec4, _ellipsis(qualifier, 150.0))
    _normal()

    wait = _waited(ob.since)
    imgui.set_cursor_screen_pos(
        imgui.ImVec2(origin.x + 458.0 - imgui.calc_text_size(wait).x, origin.y + 3.0)
    )
    imgui.text_colored(colour.vec4, wait)

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + 476.0, origin.y + 3.0))
    imgui.text_colored(P.text.vec4, ob.summary)

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x, hi.y))

    if expanded:
        imgui.indent(10.0)
        {"approval": _expand_approval, "question": _expand_question, "failure": _expand_failure}[
            ob.kind
        ](ob)
        imgui.unindent(10.0)
        imgui.spacing()

    imgui.pop_id()


def _expand_approval(ob: Obligation) -> None:
    lh = imgui.get_text_line_height_with_spacing()
    if ob.diff:
        # Fit the diff, up to a cap. A short diff that still needs scrolling is
        # the thing that makes an operator stop reading them.
        body_h = min(24.0 * lh, lh * (ob.diff.rstrip("\n").count("\n") + 1) + 10.0)
    else:
        body_h = lh * (len(ob.args or {}) + 1) + 10.0
    if imgui.begin_child("##body", imgui.ImVec2(-8.0, body_h), imgui.ChildFlags_.borders.value):
        if ob.diff:
            for line in ob.diff.rstrip("\n").split("\n"):
                imgui.text_colored(_diff_colour(line).vec4, line)
        else:
            imgui.text_colored(P.text_dim.vec4, "no diff for this call - arguments:")
            for key, value in (ob.args or {}).items():
                imgui.text_colored(P.text.vec4, f"  {key} = {value!r}")
    imgui.end_child()
    imgui.spacing()
    imgui.set_next_item_width(360.0)
    imgui.input_text_with_hint("##reason", "reason (shown to the agent on reject)", "", 512)
    imgui.same_line()
    imgui.button("approve")
    imgui.same_line()
    imgui.button("reject")
    imgui.same_line()
    imgui.button("edit")
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, "a / r / e")
    _normal()


def _expand_question(ob: Obligation) -> None:
    if imgui.begin_child("##body", imgui.ImVec2(-8.0, 96.0), imgui.ChildFlags_.borders.value):
        for line in (ob.detail or "").split("\n"):
            imgui.text_colored(P.text.vec4, line)
    imgui.end_child()
    imgui.spacing()
    imgui.input_text_multiline("##reply", "", imgui.ImVec2(-8.0, 54.0))
    imgui.button("send")
    imgui.same_line()
    imgui.button("interrupt")
    imgui.same_line()
    imgui.button("close session")
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, "the same box the transcript pane used to own")
    _normal()


def _expand_failure(ob: Obligation) -> None:
    if imgui.begin_child("##body", imgui.ImVec2(-8.0, 48.0), imgui.ChildFlags_.borders.value):
        imgui.text_colored(P.danger.vec4, ob.detail or "")
    imgui.end_child()
    imgui.spacing()
    imgui.button("retry in a new session")
    imgui.same_line()
    imgui.button("dismiss")


def _diff_colour(line: str):
    if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
        return P.accent
    if line.startswith("+"):
        return P.diff_add
    if line.startswith("-"):
        return P.diff_remove
    return P.diff_context


def _draw_zero_state() -> None:
    imgui.spacing()
    imgui.text_colored(P.ok.vec4, f"{STATE_GLYPH[AgentState.DONE]}  nothing needs you")
    imgui.text_colored(P.text_dim.vec4, "agents run until they need to change something.")
    imgui.spacing()
    imgui.separator()
    imgui.spacing()
    live = [c for c in CARDS if not c.collapsed and c.state is not AgentState.FAILED]
    imgui.text_colored(P.text_strong.vec4, "what everyone is doing")
    imgui.spacing()
    for c in live:
        imgui.text_colored(P.state(c.state).vec4, f"  {STATE_GLYPH[c.state]}")
        imgui.same_line()
        imgui.text_colored(P.text.vec4, f"{c.project}/{c.name}")
        imgui.same_line()
        imgui.text_colored(P.text_dim.vec4, f"- {c.topic}")
    imgui.spacing()
    imgui.separator()
    imgui.spacing()
    spend = sum(c.cost_usd for c in CARDS)
    imgui.text_colored(P.text_dim.vec4, f"{len(CARDS)} sessions this run · {_fmt_money(spend)}")


# --------------------------------------------------------------------------
# Supporting panes
# --------------------------------------------------------------------------

TRANSCRIPT = [
    ("dim", "I need a regression test for the late-StateChanged clobber."),
    ("dim", "The store must treat pending as authoritative, so the test has to"),
    ("dim", "park a future and then deliver a competing state change."),
    ("accent", "Read(file_path=pptmstr/store.py)"),
    ("ctx", "268 lines"),
    ("accent", "Read(file_path=tests/test_store.py)"),
    ("ctx", "412 lines"),
    ("text", "The existing tests cover apply order but not the pending guard."),
    ("text", "Adding tests/test_bridge.py with one case for the clobber."),
    ("accent", "Write(file_path=tests/test_bridge.py)"),
    ("warn", "awaiting approval"),
]


def draw_context_pane() -> None:
    cur = STATE.current
    if cur is None:
        imgui.text_colored(P.text_dim.vec4, "nothing selected")
        return
    card = next(c for c in CARDS if c.key == cur.card)
    imgui.text_colored(P.text_strong.vec4, f"{card.project} / {cur.who}")
    _small()
    imgui.text_colored(P.text_dim.vec4, "follows the inbox cursor - not separately selectable")
    _normal()
    imgui.separator()
    imgui.spacing()
    roles = {
        "dim": P.text_dim,
        "accent": P.accent,
        "ctx": P.diff_context,
        "text": P.text,
        "warn": P.warn,
    }
    imgui.push_text_wrap_pos(imgui.get_content_region_avail().x)
    for kind, line in TRANSCRIPT:
        imgui.text_colored(roles[kind].vec4, line)
    imgui.pop_text_wrap_pos()


def draw_omnibox() -> None:
    imgui.spacing()
    tail = 200.0 + 170.0 + 80.0 + 70.0
    imgui.set_next_item_width(max(180.0, imgui.get_content_region_avail().x - tail))
    imgui.input_text_with_hint("##task", "task - what should a new session do?", "", 1024)
    imgui.same_line()
    imgui.set_next_item_width(190.0)
    imgui.input_text_with_hint("##cwd", "working directory", "~/Source/pptmstr", 512)
    imgui.same_line()
    imgui.set_next_item_width(160.0)
    imgui.combo("##model", 0, ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"])
    imgui.same_line()
    imgui.button("launch")
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, "2/4 live")
    _normal()


def draw_status() -> None:
    n = len(STATE.obligations)
    imgui.text_colored(P.text_dim.vec4, f"{len(CARDS)} sessions")
    imgui.same_line()
    imgui.text_colored(P.text_dim.vec4, "|")
    imgui.same_line()
    if n:
        oldest = _waited(min(o.since for o in STATE.obligations))
        imgui.text_colored(P.state_awaiting.vec4, f"{n} need you")
        imgui.same_line()
        imgui.text_colored(P.text_dim.vec4, f"| oldest {oldest}")
    else:
        imgui.text_colored(P.ok.vec4, "nothing needs you")
    imgui.same_line()
    imgui.text_colored(P.text_dim.vec4, f"| {_fmt_money(sum(c.cost_usd for c in CARDS))} this run")


# --------------------------------------------------------------------------


def build(view: str) -> hello_imgui.RunnerParams:
    params = hello_imgui.DockingParams()
    params.layout_name = "mock"

    def split(initial, new, direction, ratio):
        s = hello_imgui.DockingSplit()
        s.initial_dock = initial
        s.new_dock = new
        s.direction = direction
        s.ratio = ratio
        return s

    d = imgui.Dir
    rail_ratio = 0.95 if view == "fleet" else 0.21
    params.docking_splits = [
        split("MainDockSpace", "RailSpace", d.left, rail_ratio),
        # 0.40 starved the inbox: identity, wait and call summary all have to fit
        # between the rail and this pane, and the summary is what gets cut.
        split("MainDockSpace", "ContextSpace", d.right, 0.32),
        split("MainDockSpace", "OmniSpace", d.down, 0.10),
    ]

    def window(label, dock, fn):
        w = hello_imgui.DockableWindow()
        w.label = label
        w.dock_space_name = dock
        w.gui_function = fn
        return w

    params.dockable_windows = [
        window("FLEET", "RailSpace", draw_fleet),
        window("NEEDS YOU", "MainDockSpace", draw_inbox),
        window("CONTEXT", "ContextSpace", draw_context_pane),
        window("LAUNCH", "OmniSpace", draw_omnibox),
    ]

    runner = hello_imgui.RunnerParams()
    runner.app_window_params.window_title = "pptmstr - layout mock"
    runner.app_window_params.window_geometry.size = (1600, 950)
    runner.imgui_window_params.show_menu_bar = True
    runner.imgui_window_params.show_menu_app = False
    runner.imgui_window_params.show_menu_view = True
    runner.imgui_window_params.show_menu_view_themes = False
    runner.imgui_window_params.show_status_bar = True
    runner.imgui_window_params.show_status_fps = False
    runner.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
    )
    runner.imgui_window_params.background_color = P.bg.vec4
    runner.docking_params = params
    # No ini: a mock must render the layout it declares, not one dragged around
    # in a previous run.
    runner.ini_disable = True
    runner.fps_idling.enable_idling = False
    runner.callbacks.load_additional_fonts = theme.load_fonts

    # Not post_init: hello_imgui applies its own ImGui theme after post_init runs,
    # so a palette pushed there is overwritten before the first frame. The app
    # hits this too and solves it the same way (AppState.theme_dirty).
    applied = {"done": False}

    def pre_frame() -> None:
        if not applied["done"]:
            theme.apply_style()
            applied["done"] = True

    runner.callbacks.pre_new_frame = pre_frame
    runner.callbacks.show_status = draw_status
    return runner


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--theme", default="dark")
    ap.add_argument("--view", default="triage", choices=("triage", "question", "empty", "fleet"))
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--sessions", type=int, default=0, help="grow the fixture to N sessions")
    args = ap.parse_args()

    theme.set_theme(args.theme)
    if args.sessions > len(CARDS):
        scale_to(args.sessions)

    if args.view == "empty":
        # Clearing the inbox means clearing what the cards are blocked on: there is
        # one list, so a zero-state has to be produced by emptying it rather than
        # by suppressing the pane that reads it.
        for card in CARDS:
            card.blocked.clear()
            if card.state in (AgentState.AWAITING_APPROVAL, AgentState.AWAITING_INPUT):
                card.state = AgentState.THINKING
                card.topic = "working"
            card.subs = [replace(s, state=AgentState.RUNNING_TOOL) for s in card.subs]

    check_fixture()
    # Open the first session that has sub-agents, so one capture shows both halves
    # of the collapse rule. A fixture choosing what to open is not the pane's
    # default -- a real session stays collapsed until the operator opens it.
    _RAIL.expanded = {next((c.key for c in CARDS if c.subs), "")} - {""}
    STATE.obligations = build_obligations()
    if args.view == "question":
        STATE.cursor = next((i for i, o in enumerate(STATE.obligations) if o.kind == "question"), 0)

    runner = build(args.view)

    if args.out is None:
        immapp.run(runner)
        return 0

    counter = {"n": 0}

    def after_swap() -> None:
        counter["n"] += 1
        if counter["n"] == args.frames:
            _grab(args.out)
            hello_imgui.get_runner_params().app_shall_exit = True

    runner.callbacks.after_swap = after_swap
    immapp.run(runner)
    return 0


def _grab(out: Path) -> None:
    from OpenGL import GL

    io = imgui.get_io()
    width = int(io.display_size.x * io.display_framebuffer_scale.x)
    height = int(io.display_size.y * io.display_framebuffer_scale.y)
    GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
    GL.glReadBuffer(GL.GL_FRONT)
    data = GL.glReadPixels(0, 0, width, height, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
    if not isinstance(data, bytes):
        data = bytes(data)
    stride = width * 3
    rows = [data[y * stride : (y + 1) * stride] for y in range(height - 1, -1, -1)]
    write_png(out, width, height, rows)
    print(f"wrote {out} ({width}x{height})")


if __name__ == "__main__":
    raise SystemExit(main())
