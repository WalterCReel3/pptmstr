"""
The rules that keep free text on the one line a pane gave it.

A task is typed into a multi-line composer and a topic is whatever an agent said, so
neither is one line by construction, and ImGui draws a line break as one: the rest of
the string lands on whatever was laid out beneath. These are the helpers every
one-line slot goes through, checked without a GL context.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui

from pptmstr.approval import Policy
from pptmstr.bridge import Bridge
from pptmstr.effects import SessionDetachSettled
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    SessionDetached,
    SessionRecalled,
    TaskDeclared,
    UsageAccrued,
)
from pptmstr.model import (
    AgentState,
    EndedHow,
    NodeId,
    Recalled,
    Snapshot,
    Task,
    TranscriptPresence,
    UsageRollup,
)
from pptmstr.store import Store
from pptmstr.theme import STATE_GLYPH, STATE_LABEL
from pptmstr.ui import board_pane, compose, digest_pane, health, transcript_pane, widgets
from pptmstr.ui.focus import FocusState, Scope

THREE_LINES = "Where are we at with this?\n\nBTW this session is also dogfooding"

# Every character ``str.splitlines`` ends a line on.
BREAKS = ("\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", " ", " ")


@pytest.fixture()
def measured(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    Every string handed to ``calc_text_size``, measured at a flat 7px a character
    by its widest line -- which is how ImGui measures a string with a break in it.
    """
    asked: list[str] = []

    def size(text: str) -> imgui.ImVec2:
        asked.append(text)
        lines = text.split("\n")
        return imgui.ImVec2(7.0 * max(len(line) for line in lines), 14.0 * len(lines))

    fake = MagicMock()
    fake.calc_text_size.side_effect = size
    monkeypatch.setattr(widgets, "imgui", fake)
    return asked


def test_a_task_typed_on_three_lines_is_one_line_with_single_spaces() -> None:
    assert widgets.flat_line(THREE_LINES) == (
        "Where are we at with this? BTW this session is also dogfooding"
    )


@pytest.mark.parametrize("brk", BREAKS)
def test_no_character_that_ends_a_line_survives_flattening(brk: str) -> None:
    flat = widgets.flat_line(f"first{brk}second{brk}{brk}third")
    assert flat == "first second third"
    assert flat.splitlines() == [flat]


def test_runs_of_whitespace_collapse_and_the_ends_are_trimmed() -> None:
    assert widgets.flat_line("  a \t b  c  \n") == "a b c"
    assert widgets.flat_line("") == ""
    assert widgets.flat_line(" \n\t ") == ""


def test_a_nul_is_shown_rather_than_ending_the_string() -> None:
    assert widgets.flat_line("be\x00fore\nafter") == f"be{widgets.NUL_SHOWN}fore after"
    assert widgets.author_text("a\x00\nb") == f"a{widgets.NUL_SHOWN}\nb"


def test_a_lone_surrogate_is_spelled_out_so_that_no_text_makes_the_binding_raise() -> None:
    """
    Measured, not assumed: Dear ImGui's binding raises ``TypeError`` on a
    string holding a lone surrogate, and JSON can carry one in a tool argument
    or a record. A row that raises while drawing can be neither read nor
    answered, so every path that draws an author's text has to be total.
    """
    from imgui_bundle import imgui

    odd = "a\ud800b\udfff\x00c"
    shown = [widgets.author_text(odd), widgets.one_line(odd + "\n"), widgets.flat_line(odd)]
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800.0, 600.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")
        imgui.new_frame()
        imgui.begin("text")
        with pytest.raises(TypeError):
            imgui.text_unformatted(odd)
        for text in shown:
            imgui.text_unformatted(text)
            text.encode("utf-8")
        imgui.end()
        imgui.end_frame()
    finally:
        imgui.destroy_context(context)

    assert shown[0] == f"a<U+D800>b<U+DFFF>{widgets.NUL_SHOWN}c"
    assert widgets.surrogate_shown("\udc80") == "<U+DC80>"
    # A whole pair is a character and is left alone, as is either neighbour of the block.
    assert widgets.author_text("\U0001f600 \ud7ff \ue000") == "\U0001f600 \ud7ff \ue000"
    # Any string at all.
    for code in range(0xD7F0, 0xE010):
        widgets.author_text(f"x{chr(code)}y").encode("utf-8")


def test_one_line_keeps_the_authors_spacing_and_flat_line_does_not() -> None:
    """
    The two rules differ on purpose: an id or a path is echoed as written, a title
    in a slot measured in pixels is not owed its blank line as two spaces.
    """
    assert widgets.one_line("a  b\n\nc") == "a  b  c"
    assert widgets.flat_line("a  b\n\nc") == "a b c"


def test_the_board_pane_and_the_shared_module_hold_one_rule_each() -> None:
    assert board_pane.one_line is widgets.one_line
    assert board_pane.author_text is widgets.author_text
    assert board_pane.NUL_SHOWN is widgets.NUL_SHOWN


def test_a_string_that_fits_is_still_flattened(measured: list[str]) -> None:
    """
    The hazard itself: a three-line task whose every line is narrower than the slot
    measures as fitting, so a rule that flattened only on the way to a cut would
    hand it back with its breaks.
    """
    assert max(len(line) for line in THREE_LINES.split("\n")) * 7.0 < 500.0
    assert widgets.ellipsis(THREE_LINES, 500.0) == (
        "Where are we at with this? BTW this session is also dogfooding"
    )
    assert all("\n" not in text for text in measured), "measured before it was flattened"


def test_the_cut_falls_in_the_first_line_that_does_not_fit(measured: list[str]) -> None:
    """
    Measured by its widest line, an unflattened task is cut somewhere in its last
    line and every line before it is drawn whole, under the first.
    """
    clipped = widgets.ellipsis(THREE_LINES, 7.0 * 20)
    assert clipped == "Where are we at wit…"
    assert len(clipped) * 7.0 <= 7.0 * 20
    assert "\n" not in clipped


def test_a_slot_with_no_width_draws_nothing(measured: list[str]) -> None:
    assert widgets.ellipsis(THREE_LINES, 0.0) == ""
    assert measured == []


# -- a session recalled from its file, and one taken off the fleet ------------------
#
# Built by the real reducer, and shared by every pane's own test file. A snapshot
# assembled by hand would be the tests' idea of a recalled record -- and the
# defect every one of them is about is a pane drawing the record's field defaults
# as facts, which only the record the store really builds can show.

CLOSED = "sess-closed"
CLOSED_ROOT: NodeId = (CLOSED, None)
RAN = "sess-ran"
RAN_ROOT: NodeId = (RAN, None)
REMOVED = "sess-removed"
REMOVED_ROOT: NodeId = (REMOVED, None)
REMOVED_CHILD: NodeId = (REMOVED, "agent-r")

# Wall-clock seconds, as a session file holds them.
WALL_NOW = 1_760_010_800.0
FROM_FILE = Recalled(
    transcript=TranscriptPresence.PRESENT,
    created_at=WALL_NOW - 86_400.0,
    last_active=WALL_NOW - 3 * 3600.0 - 120.0,
    ended_how=EndedHow.CLOSED,
    policy=Policy.PERMISSIVE,
    contained=True,
    cost_usd=1.25,
)


def recall(
    store: Store,
    recalled: Recalled = FROM_FILE,
    *,
    session_id: str = CLOSED,
    task: str = "port the\nparser",
    model: str = "claude-opus-5",
    template: str | None = "feature",
) -> None:
    """A team session's file, read at startup: a closed card and no process."""
    store.apply(
        SessionRecalled(
            session_id=session_id,
            task=task,
            model=model,
            recalled=recalled,
            cwd="/w/project",
            session_base="/w/project",
            template=template,
            brief="/briefs/sess-closed",
        ),
        now=500.0,
    )


def run(store: Store, root: NodeId = RAN_ROOT, *, cost: float = 2.0) -> None:
    """A team session this process launched, mid-turn, that has spent ``cost``."""
    store.apply(
        AgentSpawned(
            node_id=root,
            parent=None,
            task=f"the task of {root[0]}",
            model="claude-sonnet-5",
            started_at=1.0,
            cwd="/w/project",
            template="feature",
        ),
        now=1.0,
    )
    store.apply(UsageAccrued(root, UsageRollup(total_cost_usd=cost)), now=2.0)


def run_and_remove(store: Store, *, declares: tuple[str, ...] = ()) -> None:
    """
    A team session that ran here with one sub-agent, declared ``declares``,
    ended, and was taken off the fleet by the operator.
    """
    run(store, REMOVED_ROOT, cost=5.0)
    store.apply(
        AgentSpawned(
            node_id=REMOVED_CHILD,
            parent=REMOVED_ROOT,
            task="build",
            model="claude-sonnet-5",
            started_at=2.0,
            agent_type="builder",
        ),
        now=2.0,
    )
    for task_id in declares:
        store.apply(
            TaskDeclared(Task(id=task_id, title=f"do {task_id}"), node_id=REMOVED_ROOT), now=2.5
        )
    store.apply(AgentFinished(REMOVED_CHILD, AgentState.DONE, ended_at=3.0), now=3.0)
    store.apply(AgentFinished(REMOVED_ROOT, AgentState.DONE, ended_at=4.0), now=4.0)
    (answer,) = store.apply(SessionDetached(REMOVED, request_id="rq"), now=5.0)
    assert isinstance(answer, SessionDetachSettled) and answer.refusal is None
    assert store.snapshot().off_fleet(REMOVED)


def recalled_snapshot(recalled: Recalled = FROM_FILE, **file: Any) -> Snapshot:
    store = Store()
    recall(store, recalled, **file)
    return store.snapshot()


def pointed_at(snap: Snapshot, node: NodeId) -> FocusState:
    """The cursor as a click on ``node``'s card leaves it."""
    focus = FocusState()
    focus.to_node(snap, node, scope=Scope.SESSION)
    return focus


@dataclasses.dataclass
class Said:
    """What a pane handed imgui: every string, and the name of every call."""

    strings: list[str]
    calls: list[str]

    def has(self, text: str) -> bool:
        return any(text in s for s in self.strings)


def said_by(monkeypatch: pytest.MonkeyPatch, draw: Callable[[], None], *modules: Any) -> Said:
    """
    Run ``draw`` with ``imgui`` replaced in ``modules`` (and in the shared
    widgets), and report everything it said.

    Every string argument of every call, and not only the text calls, so a
    button's label and a checkbox's are in the answer: "no live controls" is a
    claim about those. It cannot say what anything looked like.
    """
    fake = MagicMock()
    # Nothing is pressed, ticked or typed: a mock's truthy answer would be a click
    # on every button the pane draws.
    for pressed in ("button", "small_button", "radio_button", "menu_item_simple"):
        getattr(fake, pressed).return_value = False
    fake.checkbox.side_effect = lambda label, value: (False, value)
    fake.input_text_with_hint.side_effect = lambda label, hint, value, *a: (False, value)
    fake.get_font_size.return_value = 16.0
    monkeypatch.setattr(widgets, "multiline_input", lambda label, text, *a, **k: (False, text))
    monkeypatch.setattr(widgets, "follow_tail", lambda following: following)
    for module in (*modules, widgets):
        monkeypatch.setattr(module, "imgui", fake)
        if hasattr(module, "ellipsis") and module is not widgets:
            # The real one measures text, and a mock's width compares to nothing.
            monkeypatch.setattr(module, "ellipsis", lambda text, width: widgets.flat_line(text))
        if hasattr(module, "ellipsis_by") and module is not widgets:
            monkeypatch.setattr(
                module, "ellipsis_by", lambda text, width, measure: widgets.flat_line(text)
            )
    draw()
    strings = [a for call in fake.mock_calls for a in call.args if isinstance(a, str)]
    return Said(strings, [call[0] for call in fake.mock_calls])


def test_the_closed_line_is_for_a_recalled_record_and_no_other() -> None:
    store = Store()
    recall(store)
    run(store)
    snap = store.snapshot()

    assert widgets.closed_line(snap.nodes[CLOSED_ROOT]) == (
        "closed — not loaded; Resume… to continue"
    )
    assert widgets.closed_line(snap.nodes[CLOSED_ROOT], unloaded="") == (
        "closed — Resume… to continue"
    )
    assert widgets.closed_line(snap.nodes[RAN_ROOT]) is None
    assert widgets.closed_line(None) is None


def test_a_session_whose_transcript_is_gone_is_not_sent_to_a_resume() -> None:
    gone = dataclasses.replace(FROM_FILE, transcript=TranscriptPresence.GONE)
    line = widgets.closed_line(recalled_snapshot(gone).nodes[CLOSED_ROOT])

    assert line is not None and line.startswith("closed") and "gone" in line
    assert widgets.RESUME_ITEM not in line and "to continue" not in line


def test_an_age_is_between_two_wall_clocks_or_is_not_known() -> None:
    assert widgets.ago(WALL_NOW - 3 * 3600 - 120, WALL_NOW) == "3h02m ago"
    assert widgets.ago(None, WALL_NOW) == "not known"
    assert widgets.ago(WALL_NOW - 60, None) == "not known"
    # A clock that was set back does not make a negative age.
    assert widgets.ago(WALL_NOW + 500, WALL_NOW) == "0s ago"


def test_a_run_that_was_killed_or_unrecorded_is_not_called_closed() -> None:
    unknown = widgets.ended_how_label(None)

    assert "closed" not in unknown and "not recorded" in unknown
    assert [widgets.ended_how_label(how) for how in EndedHow] == [
        "its last run ended when it was closed",
        "its last run ended when it failed",
        "its last run ended when pptmstr was shut down under it",
    ]


def _every_pane(monkeypatch: pytest.MonkeyPatch, snap: Snapshot, node: NodeId) -> dict[str, Said]:
    """Each pane that follows the cursor, drawn for ``node`` by its real ``draw``."""
    focus = pointed_at(snap, node)
    selected = focus.node(snap)
    fork = MagicMock()
    actions = health.HealthActions(
        interrupt=MagicMock(), close=MagicMock(), fork=fork, revoke_policy=MagicMock()
    )
    panes: dict[str, tuple[Callable[[], None], tuple[Any, ...]]] = {
        "board": (
            lambda: board_pane.draw(snap, focus, board_pane.BoardState(), Bridge()),
            (board_pane,),
        ),
        "digest": (lambda: digest_pane.draw(snap, focus, 0.0), (digest_pane, board_pane)),
        "transcript": (
            lambda: transcript_pane.draw(snap, transcript_pane.TranscriptState(), selected),
            (transcript_pane,),
        ),
        "compose": (
            lambda: compose.draw_conversation(
                snap,
                compose.ComposeState(),
                selected,
                send=MagicMock(),
                interrupt=MagicMock(),
                close=MagicMock(),
            ),
            (compose,),
        ),
        "health": (
            lambda: health.draw(snap, selected, actions, 9_000.0, None, WALL_NOW),
            (health,),
        ),
    }
    return {name: said_by(monkeypatch, draw, *mods) for name, (draw, mods) in panes.items()}


# What a pane draws from a record's field defaults. Each is a true reading of a
# session that ran here and a false one of a session nothing here observed.
_DEFAULTS_AS_FACTS = (
    "$0.00",
    " in · ",
    "to compaction",
    "not a team",
    "no tasks declared yet",
    "(nothing yet)",
    "can no longer be messaged",
    "WAITING ON YOU",
    "nothing taken without you",
)


@pytest.mark.parametrize(
    "recalled",
    [
        FROM_FILE,
        # A file that said nothing: every field at "the file did not say".
        Recalled(),
        dataclasses.replace(FROM_FILE, transcript=TranscriptPresence.GONE),
        dataclasses.replace(FROM_FILE, ended_how=EndedHow.FAILED, error="the API\nsaid no"),
    ],
    ids=["as-filed", "file-said-nothing", "transcript-gone", "had-failed"],
)
def test_no_pane_draws_a_default_as_a_fact_about_a_recalled_session(
    monkeypatch: pytest.MonkeyPatch, recalled: Recalled
) -> None:
    """
    The property the closed card rests on, over every pane at once: each says in
    one line that the session is closed, and none says "done", a cost of zero, a
    token count, a duration, or that there is nothing here yet.
    """
    snap = recalled_snapshot(recalled)
    done = STATE_LABEL[AgentState.DONE]
    assert snap.nodes[CLOSED_ROOT].state is AgentState.DONE

    for name, said in _every_pane(monkeypatch, snap, CLOSED_ROOT).items():
        assert any(s.startswith("closed — ") for s in said.strings), (name, said.strings)
        for text in said.strings:
            assert not any(false in text for false in _DEFAULTS_AS_FACTS), (name, text)
            # The state label alone, or leading a line: "done", "session done - ...".
            assert text != done and STATE_GLYPH[AgentState.DONE] not in text, (name, text)
            assert not text.startswith((f"{done} ", f"session {done}")), (name, text)
            assert "\n" not in text, (name, text)
        # No control acts on a process that does not exist. Fork needs none.
        controls = {"checkbox", "radio_button", "input_text_with_hint", "small_button"}
        assert not controls & set(said.calls), (name, said.calls)
        labels = [s for s in said.strings if s in {"interrupt", "close", "close session", "send"}]
        assert labels == [], (name, labels)


def test_the_same_panes_still_say_those_things_about_a_session_that_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The control for the property above. Without it, a pane that drew nothing at
    all for anybody would pass.
    """
    store = Store()
    run(store, cost=0.0)
    store.apply(AgentFinished(RAN_ROOT, AgentState.DONE, ended_at=4.0), now=4.0)
    panes = _every_pane(monkeypatch, store.snapshot(), RAN_ROOT)

    assert panes["health"].has("$0.00") and panes["health"].has(" in · ")
    assert STATE_LABEL[AgentState.DONE] in panes["health"].strings
    assert panes["compose"].has("can no longer be messaged")
    assert panes["transcript"].has("(nothing yet)") and "small_button" in panes["transcript"].calls
    assert panes["board"].has("no tasks declared yet")
    assert panes["digest"].has("nothing taken without you")
    assert not any(s.startswith("closed — ") for said in panes.values() for s in said.strings)
