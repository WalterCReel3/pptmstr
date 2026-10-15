"""
The Gate menu's rows, and that they agree with the guard they describe.

The table is asked of a real ``AgentSession`` as well as written down: a row that
says "at once" is one ``set_policy`` accepts, and a row that says "Continue as ..."
is one it refuses. Without that half the menu is a second copy of the driver's rule,
and the copy is what goes stale.
"""

from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

from pptmstr.approval import Policy, requires_containment
from pptmstr.bridge import Bridge
from pptmstr.driver import AgentSession, UncontainedAutonomy
from pptmstr.ui import gate_menu
from pptmstr.ui.gate_menu import Kind, Row, rows
from pptmstr.ui.widgets import gate_adds

S, P, A = Policy.STRICT, Policy.PERMISSIVE, Policy.AUTONOMOUS
CUR, NARROW, WIDEN, CONT = Kind.CURRENT, Kind.NARROW_LIVE, Kind.WIDEN_LIVE, Kind.CONTINUE_AS

EVERY_CASE = list(itertools.product(Policy, (False, True), (False, True)))


def menu(policy: Policy, contained: bool, live: bool) -> tuple[Row, ...]:
    return rows(policy, contained, live, adds=gate_adds)


def kinds(policy: Policy, contained: bool, live: bool) -> dict[Policy, Kind]:
    return {row.rung: row.kind for row in menu(policy, contained, live)}


# -- the table -------------------------------------------------------------------

# (policy now, contained, live) -> kind per rung, in ladder order.
TABLE: dict[tuple[Policy, bool, bool], tuple[Kind, Kind, Kind]] = {
    # Running, started without containment: the top rung is a resume from below it.
    (S, False, True): (CUR, WIDEN, CONT),
    (P, False, True): (NARROW, CUR, CONT),
    (A, False, True): (NARROW, NARROW, CUR),
    # Running, contained: every rung is a live move.
    (S, True, True): (CUR, WIDEN, WIDEN),
    (P, True, True): (NARROW, CUR, WIDEN),
    (A, True, True): (NARROW, NARROW, CUR),
    # Ended: nothing is live, and what the last process was started with is not read.
    (S, False, False): (CUR, CONT, CONT),
    (P, False, False): (CONT, CUR, CONT),
    (A, False, False): (CONT, CONT, CUR),
    (S, True, False): (CUR, CONT, CONT),
    (P, True, False): (CONT, CUR, CONT),
    (A, True, False): (CONT, CONT, CUR),
}


def test_the_table_covers_every_case() -> None:
    assert set(TABLE) == set(EVERY_CASE)
    assert tuple(Policy) == (S, P, A), "the table's columns are the ladder, lowest first"


@pytest.mark.parametrize("case", EVERY_CASE, ids=lambda c: f"{c[0].value}-{c[1]}-{c[2]}")
def test_the_whole_table(case: tuple[Policy, bool, bool]) -> None:
    got = menu(*case)

    assert tuple(row.rung for row in got) == tuple(Policy), "one row per rung, in ladder order"
    assert tuple(row.kind for row in got) == TABLE[case]


# -- the property ----------------------------------------------------------------


@pytest.mark.parametrize("policy", Policy)
def test_a_rung_the_process_cannot_take_is_only_ever_a_resume(policy: Policy) -> None:
    """
    Asked of ``requires_containment`` and not of a rung's name, so a rung added
    later is held to it by the answer it already had to give the driver.
    """
    for row in menu(policy, False, True):
        if requires_containment(row.rung) and row.rung is not policy:
            assert row.kind is not Kind.WIDEN_LIVE, f"{row.rung} offered live with no containment"
        if row.kind is Kind.WIDEN_LIVE:
            assert not requires_containment(row.rung)


def test_containment_is_what_separates_a_live_widen_from_a_resume() -> None:
    """
    The same rung from the same place, differing only in the fact the guard reads.
    A menu that ignored ``contained`` would give one answer to both.
    """
    assert kinds(S, True, True)[A] is Kind.WIDEN_LIVE
    assert kinds(S, False, True)[A] is Kind.CONTINUE_AS
    assert kinds(P, True, True)[A] is Kind.WIDEN_LIVE
    assert kinds(P, False, True)[A] is Kind.CONTINUE_AS


@pytest.mark.parametrize("case", EVERY_CASE, ids=lambda c: f"{c[0].value}-{c[1]}-{c[2]}")
def test_no_row_offers_the_current_rung_as_an_action(case: tuple[Policy, bool, bool]) -> None:
    policy = case[0]
    got = menu(*case)

    current = [row for row in got if row.kind is Kind.CURRENT]
    assert [row.rung for row in current] == [policy], "exactly one row is the gate now"
    for row in got:
        assert row.acts is (row.rung is not policy)
        if row.rung is policy:
            for verb in ("Narrow", "Widen", "Continue", "Resume"):
                assert verb not in row.label


def test_an_ended_session_offers_no_live_move() -> None:
    for policy, contained in itertools.product(Policy, (False, True)):
        got = menu(policy, contained, False)

        assert {row.kind for row in got} == {Kind.CURRENT, Kind.CONTINUE_AS}
        assert menu(policy, True, False) == menu(policy, False, False)
        for row in got:
            if row.kind is Kind.CONTINUE_AS:
                assert row.label == f"Resume as {row.rung.value}…"
                assert "nothing starts until you send" in row.consequence


# -- every row says what it will do ------------------------------------------------


@pytest.mark.parametrize("case", EVERY_CASE, ids=lambda c: f"{c[0].value}-{c[1]}-{c[2]}")
def test_every_row_says_what_choosing_it_does_on_one_line(
    case: tuple[Policy, bool, bool],
) -> None:
    for row in menu(*case):
        assert row.label.strip() and row.consequence.strip()
        assert "\n" not in row.label and "\n" not in row.consequence
        assert row.rung.value in row.label, "the row names its rung"
        assert row.consequence.endswith(".")


@pytest.mark.parametrize("case", EVERY_CASE, ids=lambda c: f"{c[0].value}-{c[1]}-{c[2]}")
def test_a_rows_menu_line_is_its_rung_and_a_suffix_where_it_does_not_switch_at_once(
    case: tuple[Policy, bool, bool],
) -> None:
    """
    The menu draws ``short`` and nothing else of a row: the rung's name, a word
    on the rung in force, and a suffix on a row that does something other than
    switch at once -- a question first, or the resume form, or a restart.
    """
    _, _, live = case
    for row in menu(*case):
        name = row.rung.value
        match row.kind:
            case Kind.CURRENT:
                assert row.short == name + (gate_menu.NOW if live else gate_menu.LAST_RUN)
            case Kind.NARROW_LIVE:
                assert row.short == name
            case Kind.WIDEN_LIVE:
                assert row.short == name + gate_menu.ASKS
            case Kind.CONTINUE_AS:
                assert row.short == name + (gate_menu.RESTARTS if live else gate_menu.ASKS)


@pytest.mark.parametrize("case", EVERY_CASE, ids=lambda c: f"{c[0].value}-{c[1]}-{c[2]}")
def test_a_rows_one_sentence_is_one_sentence_of_what_it_says_at_length(
    case: tuple[Policy, bool, bool],
) -> None:
    """
    What the question in front of a move shows is one sentence, and it is taken
    from the whole account rather than written beside it, so the two cannot come
    to say different things.
    """
    for row in menu(*case):
        assert row.sentence.endswith("."), row
        assert ". " not in row.sentence, row
        assert row.sentence in row.consequence, row


def test_a_live_widen_says_what_it_adds_when_it_starts_and_what_it_leaves() -> None:
    (row,) = [r for r in menu(S, True, True) if r.rung is P]

    assert row.label == "Widen to permissive"
    for added in gate_adds(P):
        assert added in row.consequence
    assert "From the next tool call" in row.consequence
    assert "already waiting for you still waits" in row.consequence
    assert "briefing still describes the rung it started under" in row.consequence


def listed(labels: list[str]) -> str:
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} and {labels[-1]}"


def test_a_move_names_only_what_it_changes_and_not_everything_the_rung_releases() -> None:
    """
    Between permissive and autonomous, shell reads run unattended on both sides:
    a widen that listed them would overstate what it adds, and a narrow that
    listed them would say they go back to waiting when they do not.
    """
    shared = set(gate_adds(P))
    moved = [label for label in gate_adds(A) if label not in shared]
    assert shared and moved, "the case needs a rung that already releases something"

    (widen,) = [r for r in menu(P, True, True) if r.rung is A]
    (narrow,) = [r for r in menu(A, True, True) if r.rung is P]

    assert widen.consequence.startswith(
        f"From the next tool call the gate judges, {listed(moved)} run without asking;"
    )
    assert narrow.consequence.startswith(
        f"From the next tool call the gate judges, {listed(moved)} wait for you again;"
    )


def test_a_narrow_says_what_waits_again_and_what_it_does_not_stop() -> None:
    (row,) = [r for r in menu(A, True, True) if r.rung is P]

    assert row.label == "Narrow to permissive"
    returned = [label for label in gate_adds(A) if label not in gate_adds(P)]
    assert returned
    for label in returned:
        assert label in row.consequence
    assert "wait for you again" in row.consequence
    assert "From the next tool call" in row.consequence
    assert "a call already let through is not stopped" in row.consequence
    assert "briefing still describes the rung it started under" in row.consequence


def test_continuing_says_the_run_ends_and_what_becomes_of_a_turn_in_flight() -> None:
    (row,) = [r for r in menu(S, False, True) if r.rung is A]

    assert row.label == "Continue as autonomous…"
    assert "started without the containment autonomous requires" in row.consequence
    assert "resume form" in row.consequence
    assert "ends the running session" in row.consequence
    assert "a turn in flight is not finished" in row.consequence
    assert "approval it is waiting on goes with it" in row.consequence
    assert "From the next tool call" not in row.consequence, "it is not a live act"


def test_the_current_row_says_whether_the_session_is_running() -> None:
    (live,) = [r for r in menu(P, False, True) if r.kind is Kind.CURRENT]
    (ended,) = [r for r in menu(P, False, False) if r.kind is Kind.CURRENT]

    assert live.label == "permissive (now)"
    assert ended.label == "permissive (as last granted)"
    assert "it is not running" in ended.consequence
    assert "not running" not in live.consequence
    for added in gate_adds(P):
        assert added in live.consequence and added in ended.consequence


def test_the_lowest_rung_is_described_as_releasing_nothing() -> None:
    assert gate_adds(S) == ()
    (row,) = [r for r in menu(S, False, True) if r.kind is Kind.CURRENT]

    assert "nothing off the standing allowlist runs without asking" in row.consequence


def test_what_a_row_says_a_rung_releases_is_the_classifiers_answer() -> None:
    """The wording follows ``adds``; a list written into the module would not."""
    got = rows(S, True, True, adds=lambda rung: () if rung is S else (f"<{rung.value}>",))

    assert "<permissive> run without asking" in got[1].consequence
    assert "<autonomous> run without asking" in got[2].consequence


# -- agreement with the driver ------------------------------------------------------

# How a session's containment reaches the guard, and what the menu is told about it.
# The blanks are the guard's own edge: neither puts settings on the process.
CONTAINMENTS: tuple[tuple[str | None, bool], ...] = (
    (None, False),
    ("", False),
    (" ", False),
    ("{}", True),
)


@pytest.mark.parametrize("policy", Policy)
@pytest.mark.parametrize("containment,contained", CONTAINMENTS)
def test_every_live_row_is_one_the_driver_accepts_and_every_resume_one_it_refuses(
    policy: Policy, containment: str | None, contained: bool
) -> None:
    for row in menu(policy, contained, True):
        session = AgentSession(Bridge(), "t", policy=policy, containment=containment)
        assert session.policy is policy

        match row.kind:
            case Kind.CURRENT:
                assert row.rung is session.policy
            case Kind.NARROW_LIVE | Kind.WIDEN_LIVE:
                session.set_policy(row.rung)
                assert session.policy is row.rung
            case Kind.CONTINUE_AS:
                with pytest.raises(UncontainedAutonomy):
                    session.set_policy(row.rung)
                assert session.policy is policy, "a refusal leaves the gate where it was"


@pytest.mark.parametrize("policy", Policy)
@pytest.mark.parametrize("containment,contained", CONTAINMENTS)
def test_the_narrow_to_the_lowest_rung_is_what_revoke_policy_does(
    policy: Policy, containment: str | None, contained: bool
) -> None:
    lowest = tuple(Policy)[0]
    row = menu(policy, contained, True)[0]
    session = AgentSession(Bridge(), "t", policy=policy, containment=containment)

    session.revoke_policy()

    assert session.policy is lowest is row.rung
    assert row.kind is (Kind.CURRENT if policy is lowest else Kind.NARROW_LIVE)


def test_narrow_and_widen_follow_what_is_released_and_not_only_the_enums_order() -> None:
    """
    The module takes direction from ``Policy``'s definition order. This is what
    makes that order mean "releases more": each rung releases everything the one
    below it does, so a rung inserted out of place fails here.
    """
    ladder = tuple(Policy)
    for lower, higher in itertools.pairwise(ladder):
        assert set(gate_adds(lower)) < set(gate_adds(higher))

    for policy in Policy:
        for row in menu(policy, True, True):
            if row.kind is Kind.NARROW_LIVE:
                assert set(gate_adds(row.rung)) < set(gate_adds(policy))
            if row.kind is Kind.WIDEN_LIVE:
                assert set(gate_adds(row.rung)) > set(gate_adds(policy))


# -- the module's shape ---------------------------------------------------------------


def test_the_module_imports_no_imgui_and_names_no_rung() -> None:
    source = pathlib.Path(gate_menu.__file__).read_text()
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
            imported.update(alias.name for alias in node.names)
    assert not any("imgui" in name or "widgets" in name for name in imported), imported

    named = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Policy"
    }
    assert named == set(), f"rungs named in the module: {named}"
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
    }
    for rung in Policy:
        assert not any(rung.value in text for text in literals - docstrings), rung
