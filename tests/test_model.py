"""
The pure helpers the reducer compares declarations and writes with, and the launch
value an operator's choices arrive on.

``tests/test_bus.py`` and ``tests/test_store.py`` exercise the helpers through a live
bus and a real ``Store``; this file pins them directly, because the whitespace policy
below is a control rather than a formatting choice and a control wants a test at the
function it constrains. The ``LaunchSpec`` tests at the end are here for the same
reason: what ``from_record`` declines to carry is a permission decision, and a
decision expressed only by an absence needs a test that fails when the absence ends.

The last test reaches past this module into ``approval`` and ``driver``, which is why
those imports are here: the default policy is declared in three modules and the whole
value of pinning it is that no one of them can drift alone.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import Any

from pptmstr.approval import Policy, classify
from pptmstr.driver import AgentSession
from pptmstr.model import (
    BEFORE_RESTART,
    CLOSED_BY_OPERATOR,
    EARLIER_RUN,
    AgentRecord,
    AgentState,
    LaunchSpec,
    NotPlaced,
    Restore,
    Snapshot,
    Unplaced,
    closing_result,
    is_address_only,
    normalised_touches,
    relative_write,
    restored_party,
    written_path,
)
from pptmstr.sandbox import containment_settings


def test_a_padded_declaration_cannot_miss_the_overlap_check() -> None:
    """
    The declaring caller is a model that chooses its own spelling. If padding
    survived, ``store._auto_depends`` would hold two spellings of one file against
    each other and find no collision -- the concurrent write ``touches`` exists to
    prevent. ``test_a_caller_cannot_evade_the_overlap_check_with_its_own_spelling``
    pins this through the bus; this pins the function the bus reaches.
    """
    assert normalised_touches(("  pptmstr/store.py  ",)) == ("pptmstr/store.py",)


def test_two_spellings_of_one_path_collapse_to_one_entry() -> None:
    assert normalised_touches(("pptmstr/store.py", "./pptmstr/store.py")) == ("pptmstr/store.py",)


def test_an_ordinary_path_is_left_exactly_as_declared() -> None:
    assert normalised_touches(("pptmstr/store.py", "tests/test_store.py")) == (
        "pptmstr/store.py",
        "tests/test_store.py",
    )


def test_a_filename_with_edge_whitespace_cannot_be_declared() -> None:
    """
    The cost of the control, asserted rather than left to a docstring to claim. A
    file genuinely named with a leading or trailing space has no spelling that
    survives here, so no declaration can hold one.
    """
    assert normalised_touches(("report.md ",)) == ("report.md",)
    assert normalised_touches((" report.md",)) == ("report.md",)


def test_a_name_made_only_of_whitespace_is_dropped_rather_than_stored() -> None:
    assert normalised_touches((" ", "\t", "pptmstr/store.py")) == ("pptmstr/store.py",)


def test_the_declared_and_the_measured_side_trim_identically() -> None:
    """
    The invariant that makes trimming everywhere the only consistent pair. A write
    measured by ``relative_write`` must arrive in a spelling a declaration is able
    to hold; if either side trimmed alone, a write to a file whose name ends in a
    space would be reported as outside a declaration naming that very file -- the
    false accusation ``ApprovedWrites.unplaced`` exists to avoid.

    The absolute assertion is the load-bearing one: the relative branch ends in
    ``normalised_touches`` and is trimmed by it whatever ``relative_write`` does, so
    only the absolute branch rests on ``relative_write`` trimming for itself.
    """
    declared = normalised_touches(("report.md ",))
    assert relative_write("/repo/report.md ", "/repo") == declared[0]
    assert relative_write("report.md ", "/repo", "/repo") == declared[0]


def test_a_blank_file_path_names_no_file_rather_than_a_blank_one() -> None:
    assert written_path("Write", {"file_path": "   "}) is None
    assert written_path("Write", {"file_path": "pptmstr/store.py"}) == "pptmstr/store.py"


def test_a_launch_that_asks_for_neither_is_uncontained_and_strictly_gated() -> None:
    """
    The OFF path. A spec built the way every existing call site builds one carries no
    containment and the gate's long-standing classification, so adding the two fields
    moves no launch that does not name them.
    """
    spec = LaunchSpec(task="t", model="m")

    assert spec.containment is None
    assert spec.policy is Policy.STRICT


@dataclasses.dataclass(frozen=True, slots=True)
class _RecordThatHasThemToGive(AgentRecord):
    """
    A record that carries the containment ``AgentRecord`` deliberately does not.

    The stand-in is what gives the containment half of the test below teeth. A plain
    ``AgentRecord`` has no containment to offer, so a ``from_record`` that declines to
    carry it and one that has nothing to carry produce the same result and the
    assertion could not fail. This subclass supplies the value, so the only thing
    keeping it out of the fork is ``from_record`` naming its fields explicitly.

    The policy half needs no stand-in: ``AgentRecord`` holds a policy, and the test
    sets it to ``AUTONOMOUS`` so that a ``from_record`` carrying it would be seen.
    """

    containment: str | None = None


def test_a_fork_inherits_neither_the_containment_nor_the_policy() -> None:
    """
    ``ui/health.py`` forks with ``actions.fork(LaunchSpec.from_record(root))`` -- no
    modal, no combo, no confirmation. Anything ``from_record`` carries is therefore
    re-armed by a button pressed for an unrelated reason, so both must come back at
    their defaults even when the record is offering them.

    This is the test that stops the apparent omission being tidied away later.
    """
    launched = LaunchSpec(
        task="audit the parser",
        model="claude-sonnet-5",
        cwd="/srv/repo",
        session_base="/srv/repo",
        template="feature",
        brief="/briefs/s1",
        containment=containment_settings(),
        policy=Policy.AUTONOMOUS,
    )
    record = _RecordThatHasThemToGive(
        node_id=("s1", None),
        parent=None,
        depth=0,
        state=AgentState.DONE,
        topic="",
        task=launched.task,
        model=launched.model,
        cwd=launched.cwd,
        session_base=launched.session_base,
        template=launched.template,
        brief=launched.brief,
        containment=launched.containment,
        policy=launched.policy,
    )

    forked = LaunchSpec.from_record(record)

    assert forked.containment is None
    assert forked.policy is Policy.STRICT
    # The premises still travel. Without this the two above would also hold for a
    # `from_record` that had stopped carrying anything at all.
    assert (forked.task, forked.cwd, forked.session_base, forked.template, forked.brief) == (
        "audit the parser",
        "/srv/repo",
        "/srv/repo",
        "feature",
        "/briefs/s1",
    )


def test_a_fork_is_sized_by_the_setting_rather_than_by_the_session_it_copies() -> None:
    """
    The cap drops from ``from_record`` too, but not on the containment's argument, and
    the difference is worth pinning rather than leaving to look like the same rule.

    A cap is capacity, not permission, so "a permission arriving through a surface
    nobody saw" does not reach it -- a fork inheriting a cap of 2 would be bounded, not
    armed. It drops because ``AgentRecord`` has nowhere to keep one, which makes
    ``None`` the only answer available and the operator's current setting the thing a
    relaunch is then sized by. If a cap is ever added to the record, this test goes
    red and the decision gets made deliberately instead of by a field appearing.
    """
    on_the_record = {f.name for f in dataclasses.fields(AgentRecord)}
    assert "subagent_cap" not in on_the_record

    record = AgentRecord(
        node_id=("s1", None),
        parent=None,
        depth=0,
        state=AgentState.DONE,
        topic="",
        task="audit the parser",
        model="claude-sonnet-5",
        cwd="/srv/repo",
    )

    assert LaunchSpec.from_record(record).subagent_cap is None


def test_the_record_a_fork_reads_has_nowhere_to_keep_the_containment() -> None:
    """
    What makes the containment drop structural rather than a line someone can add.
    ``AgentRecord`` is the only thing ``from_record`` reads and it has no containment
    field, so making a fork inherit the sandbox takes a change to the store's own
    record shape and fails here first.

    The fence covers containment alone, and the half it no longer covers is worth
    meeting here rather than discovering. ``AgentRecord.policy`` exists so the operator
    can see which running sessions are under-gated and so a record agrees with the gate
    that classifies its calls; the cost is that a fork inheriting the under-gated
    allowlist is now one line in ``from_record`` rather than a change of record shape.
    Only ``test_a_fork_inherits_neither_the_containment_nor_the_policy`` stands in front
    of that line, and it is behavioural rather than structural -- adding
    ``policy=record.policy`` to ``from_record`` turns it red. Since the containment and
    the allowlist are not separable (``planning/2026-09-03``), the weaker of the two
    guards is the one that decides how hard the pair is to re-arm by accident.
    """
    on_the_record = {f.name for f in dataclasses.fields(AgentRecord)}

    assert "containment" not in on_the_record


def test_a_policy_that_defaults_in_a_signature_or_a_field_defaults_to_strict() -> None:
    """
    Four independent spellings of the safe direction, tied together here.

    ``approval.classify``, ``AgentSession.__init__``, ``AgentRecord.policy`` and
    ``LaunchSpec.policy`` each declare what a caller that names no policy is measured
    against, and nothing else makes them agree. The store supplies the keyword on every
    path it owns, so the record's field default is load-bearing only for a hand-built
    record: flipping it to ``AUTONOMOUS`` leaves the rest of the suite green. That is
    the unpinned duplication STYLE.md section 3 names, sitting under the one value in
    this feature where drifting the wrong way hands out an allowlist nobody asked for.

    The defaults are read out of the signatures and the fields rather than restated, so
    this pins the four and does not become a fifth declaration of its own.

    Signatures and fields only. ``store._apply`` twice spells ``Policy.STRICT`` as the
    fallback for a node with no parent to inherit from; those are expressions rather
    than declared defaults, they are not reachable by reflection, and this test says
    nothing about them.
    """

    def declared(function: Callable[..., Any]) -> Any:
        return inspect.signature(function).parameters["policy"].default

    def field_default(record: Any) -> Any:
        return next(f.default for f in dataclasses.fields(record) if f.name == "policy")

    defaults = {
        "approval.classify": declared(classify),
        "AgentSession.__init__": declared(AgentSession.__init__),
        "AgentRecord.policy": field_default(AgentRecord),
        "LaunchSpec.policy": field_default(LaunchSpec),
    }

    assert defaults == dict.fromkeys(defaults, Policy.STRICT)


def test_a_write_tool_is_read_on_the_key_it_declares_not_the_one_that_is_filled_in() -> None:
    """
    The decoy. A call carrying both keys has one target and one other thing, and
    reading whichever was truthy let the other one answer: a ``NotebookEdit`` naming
    a harmless ``file_path`` measured to that while the tool acted on
    ``notebook_path``. The gate then bounded the wrong path, and the queue row an
    operator reads still shows the wrong one.

    Whether the CLI forwards an argument a tool's schema does not declare is
    unmeasured, and that is the reason this is a defect rather than the reason it is
    not: ``driver._stamp_bus_call`` records the same gap and writes its stamp
    unconditionally rather than resting on it.
    """
    decoyed = {"file_path": "inside.txt", "notebook_path": "/etc/outside.ipynb"}
    assert written_path("NotebookEdit", decoyed) == "/etc/outside.ipynb"

    reversed_decoy = {"file_path": "/etc/outside.txt", "notebook_path": "inside.ipynb"}
    assert written_path("Write", reversed_decoy) == "/etc/outside.txt"
    assert written_path("Edit", reversed_decoy) == "/etc/outside.txt"
    assert written_path("MultiEdit", reversed_decoy) == "/etc/outside.txt"


def test_a_write_tool_naming_only_the_other_tools_key_names_no_file() -> None:
    """
    The consequence of reading one key, stated rather than left implicit. A
    ``NotebookEdit`` with only ``file_path`` is a call whose declared target is
    absent, and None is the honest answer -- the gate turns that into a refusal,
    which is the right end for a call that cannot be shown to write anywhere in
    particular. Guessing from the other key would be the decoy again, wearing the
    word "fallback".
    """
    assert written_path("NotebookEdit", {"file_path": "x.txt"}) is None
    assert written_path("Write", {"notebook_path": "x.ipynb"}) is None


# -- how an author says it knows ----------------------------------------------------


def test_a_basis_is_one_of_three_and_unstated_is_not_one_of_them() -> None:
    """
    The closed set, pinned as a decision rather than as an enum's current contents:
    three ways a claim arrives, and no member for the text that arrived without
    one. A fourth -- "unstated", "unknown", "verified" -- would be either a default
    in disguise or the board vouching for something.
    """
    from pptmstr.model import Basis, Concern, Task

    assert [b.value for b in Basis] == ["run", "read", "relayed"]
    assert Task(id="t", title="t").basis is None
    assert Task(id="t", title="t").result_basis is None
    assert (
        Concern(
            id="c", sender=("s", None), recipient=("s", "a"), subject="", body="", posted_at=0.0
        ).basis
        is None
    )


# -- a property, what was not run, and an incident ----------------------------------


def test_the_three_new_facts_are_absent_until_somebody_states_them() -> None:
    """
    The defaults, pinned as the decision they are. ``not_run`` in particular
    defaults to None and not to the empty string, which is the spelling reserved
    for a claimer that stated nothing was left unrun.
    """
    from pptmstr.model import Concern, Task

    blank = Task(id="t", title="t")
    assert (blank.property, blank.not_run) == ("", None)
    assert (
        Concern(
            id="c", sender=("s", None), recipient=("s", "a"), subject="", body="", posted_at=0.0
        ).incident
        is False
    )


def test_a_basis_is_read_from_an_authors_spelling_in_one_place() -> None:
    import pytest

    from pptmstr.model import Basis

    assert [Basis.stated(raw) for raw in (None, "", "   ")] == [None, None, None]
    assert [Basis.stated(raw) for raw in ("run", " READ ", "Relayed")] == list(Basis)
    for wrong in ("verified", "ran", "unstated", 3):
        with pytest.raises(ValueError):
            Basis.stated(wrong)


def test_a_property_is_one_line_and_its_overrun_is_measured_against_the_limit() -> None:
    from pptmstr.model import MAX_PROPERTY_CHARS, normalised_property, property_overrun

    assert normalised_property("  never lose\n\ta   row. \n") == "never lose a row."
    assert [normalised_property(raw) for raw in (None, "", " \n ")] == ["", "", ""]
    assert MAX_PROPERTY_CHARS == 300
    assert property_overrun("x" * MAX_PROPERTY_CHARS) == 0
    assert property_overrun("x" * (MAX_PROPERTY_CHARS + 1)) == 1
    assert property_overrun("") == 0


def test_a_flag_that_is_neither_yes_nor_no_is_not_guessed() -> None:
    """
    ``stated_flag`` answers None for what it cannot read, and the two callers take
    it in opposite directions: an incident is marked, a claim that everything was
    run is not accepted. Both directions are the one that does not overstate.
    """
    from pptmstr.model import stated_flag, stated_incident

    assert [stated_flag(raw) for raw in (True, "true", " Yes ", "1")] == [True] * 4
    assert [stated_flag(raw) for raw in (None, False, "", "false", "NO", "0", "null")] == [
        False
    ] * 7
    assert [stated_flag(raw) for raw in ("mostly", "a rule was broken", 2)] == [None] * 3

    assert stated_incident("a rule was broken") is True
    assert stated_incident(None) is False and stated_incident("no") is False


def test_a_tasks_version_and_who_last_amended_it_are_read_off_its_history() -> None:
    """
    None of the four is stored. A history with a different last entry is the
    input that tells a derived answer from a copied one.
    """
    from pptmstr.model import Task, TaskVersion

    lead = ("s", None)
    fresh = Task(id="t1", title="x")
    amended = Task(
        id="t1",
        title="x",
        superseded=(
            TaskVersion("one", "", None, replaced_at=3.0, replaced_by=lead),
            TaskVersion("two", "", None, replaced_at=8.0, replaced_by=None),
        ),
    )

    assert (fresh.version, fresh.amended_at, fresh.amended_by) == (1, None, None)
    assert (amended.version, amended.amended_at, amended.amended_by) == (3, 8.0, None)
    earlier = dataclasses.replace(amended, superseded=amended.superseded[:1])
    assert (earlier.version, earlier.amended_at, earlier.amended_by) == (2, 3.0, lead)

    assert {f.name for f in dataclasses.fields(Task)}.isdisjoint(
        {"version", "amended_at", "amended_by", "completed_version"}
    )


def test_a_completed_version_exists_only_on_a_finished_task() -> None:
    """
    A claimed task carries the version its holder was handed, and that is not yet
    the version of any result.
    """
    from pptmstr.model import Task, TaskState

    held = Task(id="t1", title="x", state=TaskState.CLAIMED, claimed_version=2)

    assert held.completed_version is None
    assert dataclasses.replace(held, state=TaskState.COMPLETED).completed_version == 2
    assert Task(id="t1", title="x", state=TaskState.COMPLETED).completed_version is None


def test_an_agent_amended_a_task_when_it_replaced_any_of_its_versions() -> None:
    from pptmstr.model import Task, TaskVersion

    lead, dev = ("s", None), ("s", "agent-dev")
    task = Task(
        id="t1",
        title="x",
        declared_by=dev,
        superseded=(
            TaskVersion("one", "", None, replaced_at=3.0, replaced_by=lead),
            TaskVersion("two", "", None, replaced_at=8.0, replaced_by=None),
        ),
    )

    assert task.was_amended_by(lead)
    # Declaring is not amending, and the operator's None is nobody's node.
    assert not task.was_amended_by(dev)
    assert not Task(id="t1", title="x").was_amended_by(lead)


# -- what became of a logged board ----------------------------------------------------

_WHOLE = Restore("s", at=10.0, tasks=3, concerns=1)


def test_a_log_may_be_appended_behind_only_when_the_store_holds_all_it_says() -> None:
    """
    ``adoptable`` is the one question the shell asks before it writes behind a
    log. Each of these is a way the store's board is not the log's, and each
    alone is a no.
    """
    left_out = (NotPlaced("task", "t1", Unplaced.OTHER_SESSION_HAS_ID),)

    assert _WHOLE.adoptable and not _WHOLE.short and not _WHOLE.refused
    assert not dataclasses.replace(_WHOLE, at=None).adoptable, "nothing from a log is in the store"
    assert not dataclasses.replace(_WHOLE, unplaced=left_out).adoptable, "one task was left out"
    assert not dataclasses.replace(_WHOLE, log_short=True).adoptable, "the log could not give all"
    assert not Restore("s").adoptable


def test_a_restore_is_short_for_what_it_left_out_and_for_what_the_log_could_not_give() -> None:
    left_out = (NotPlaced("concern", "c1", Unplaced.NOT_THIS_SESSIONS),)

    assert dataclasses.replace(_WHOLE, unplaced=left_out).short
    assert dataclasses.replace(_WHOLE, log_short=True).short
    # The counts say why a log was short. They are not what makes it so.
    assert not dataclasses.replace(_WHOLE, log_stray=2).short


def test_refused_is_a_restore_that_placed_nothing_and_not_one_that_left_something_out() -> None:
    def left(why: Unplaced) -> Restore:
        return dataclasses.replace(_WHOLE, unplaced=(NotPlaced("task", "t1", why),))

    refusing = {Unplaced.STORE_DIFFERS, Unplaced.STORE_ONLY, Unplaced.WITHHELD}

    assert {why for why in Unplaced if left(why).refused} == refusing


def test_a_stand_in_for_an_address_is_not_an_id_an_agent_can_have() -> None:
    """
    An agent id names a file, so it is one path component. The stand-in is not,
    and it keeps the address it stands for.
    """
    node = restored_party("s", "builder-2")

    assert node[0] == "s" and node[1] is not None
    assert "/" in node[1] and node[1].endswith("builder-2")
    assert is_address_only(node)
    assert not is_address_only(("s", "builder-2")) and not is_address_only(("s", None))
    assert restored_party("s", "builder") != restored_party("s", "builder-2")


def test_gone_is_named_by_a_restored_board_and_absent_from_this_process() -> None:
    """Both halves: a record makes a node present, and no log makes it merely unheard of."""
    old, live, unheard = ("s", "old"), ("s", "live"), ("s", "unheard")
    record = AgentRecord(
        node_id=live,
        parent=None,
        depth=0,
        state=AgentState.DONE,
        topic="",
        task="",
        model="m",
        started_at=0.0,
    )
    snap = dataclasses.replace(
        Snapshot.empty(),
        nodes={live: record},
        restores={"s": dataclasses.replace(_WHOLE, parties={old: "builder-2", live: "builder"})},
    )

    assert snap.left_behind(old) == "builder-2"
    assert snap.left_behind(live) is None, "finished, and still here to be woken"
    assert snap.left_behind(unheard) is None
    assert snap.left_behind(("z", "old")) is None, "another session's node of the same id"


def _agent(node: tuple[str, str | None], state: AgentState, started_at: float) -> AgentRecord:
    return AgentRecord(
        node_id=node,
        parent=None if node[1] is None else (node[0], None),
        depth=0 if node[1] is None else 1,
        state=state,
        topic="",
        task="",
        model="m",
        started_at=started_at,
    )


def test_an_earlier_run_is_another_processs_or_this_ones_before_the_root_was_announced() -> None:
    """
    ``Snapshot.of_earlier_run``, every clause. The root was last announced at
    10.0: a terminal sub-agent spawned before that is of an earlier run, and
    nothing else with a record is -- not one still running, not one spawned at or
    after the announce however it ended, not the root, and not anything of a
    session whose root has no record to measure against.
    """
    root, restored = ("s", None), ("s", "restored")
    early = {state: ("s", f"early-{state.value}") for state in AgentState}
    nodes = {
        root: _agent(root, AgentState.DONE, 10.0),
        ("s", "same-stamp"): _agent(("s", "same-stamp"), AgentState.DONE, 10.0),
        ("s", "later"): _agent(("s", "later"), AgentState.FAILED, 11.0),
        ("z", "rootless"): _agent(("z", "rootless"), AgentState.DONE, 1.0),
        **{node: _agent(node, state, 9.0) for state, node in early.items()},
    }
    snap = dataclasses.replace(
        Snapshot.empty(),
        nodes=nodes,
        restores={"s": dataclasses.replace(_WHOLE, parties={restored: "builder-2"})},
    )

    assert snap.of_earlier_run(restored), "left behind by another process"
    assert {state for state, node in early.items() if snap.of_earlier_run(node)} == {
        AgentState.DONE,
        AgentState.FAILED,
        AgentState.CANCELLED,
    }
    assert not snap.of_earlier_run(("s", "same-stamp"))
    assert not snap.of_earlier_run(("s", "later"))
    assert not snap.of_earlier_run(root)
    assert not snap.of_earlier_run(("z", "rootless"))
    assert not snap.of_earlier_run(("s", "unheard"))


def test_one_qualifier_names_an_earlier_runs_agent_whichever_way_the_run_ended() -> None:
    """
    The driver and the bus import it as ``BEFORE_RESTART``. A second value there
    would have a bus reply and a board row call one agent two things, and the
    words must be true of a session resumed in a window nobody restarted.
    """
    assert BEFORE_RESTART == EARLIER_RUN == "from an earlier run"
    assert "restart" not in EARLIER_RUN


def test_a_closing_result_is_the_label_and_then_the_reason_exactly_as_typed() -> None:
    assert closing_result(" kept  as\ttyped\n") == f"{CLOSED_BY_OPERATOR}:  kept  as\ttyped\n"
    assert closing_result("") == closing_result(" \n\t") == CLOSED_BY_OPERATOR
    assert CLOSED_BY_OPERATOR == "closed by the operator"


def test_the_operators_notice_is_the_operators_about_the_task_with_the_reason_alone() -> None:
    """
    ``closing_notice``: sent by a node that is no agent's and is not the lead's,
    on the board of the task's own session, with the reason as its whole body.
    """
    from pptmstr.model import (
        MAX_ANSWER_CHARS,
        MAX_CLOSE_REASON_CHARS,
        Task,
        closing_notice,
        is_closing_notice,
        is_operator,
        operator_party,
    )

    holder = ("other-session", "agent-a")
    task = Task(id="t1", title="T", declared_by=("s", None))

    notice = closing_notice(task, holder, " as  typed\n", 7.0)
    undeclared = closing_notice(Task(id="t2", title="U"), holder, "", 7.0)

    assert notice.sender == operator_party("s") != ("s", None)
    assert undeclared.sender == operator_party("other-session")
    assert is_operator(notice.sender) and not is_operator(("s", None))
    assert not is_operator(("s", "operator")) and not is_operator(holder)
    assert (notice.recipient, notice.task_id, notice.body) == (holder, "t1", " as  typed\n")
    assert notice.posted_at == 7.0 and "t1" in notice.subject
    assert is_closing_notice(notice.id) and notice.id != undeclared.id
    assert not is_closing_notice("c-0123456789ab")
    assert MAX_CLOSE_REASON_CHARS == MAX_ANSWER_CHARS


# -- decisions --------------------------------------------------------------------


def _decision(**fields: Any) -> Any:
    from pptmstr.model import Decision, Standing

    base: dict[str, Any] = dict(
        id="d-1",
        asker=("s", "a1"),
        question="q",
        blocks=(),
        if_unanswered="",
        evidence="e",
        recommendation="r",
        standing=Standing.OPERATORS,
        reversible=None,
        raised_at=1.0,
    )
    return Decision(**{**base, **fields})


def test_an_answer_exists_exactly_when_the_operator_gave_one_and_empty_is_one() -> None:
    from pptmstr.model import Answered, Dismissed, Withdrawn

    assert _decision().answer is None and _decision().is_open
    assert _decision(outcome=Answered("", at=3.0)).answer == ""
    assert _decision(outcome=Answered("", at=3.0)).answered_at == 3.0
    assert not _decision(outcome=Answered("", at=3.0)).is_open
    for ended in (Dismissed(at=3.0), Withdrawn(at=3.0)):
        assert _decision(outcome=ended).answer is None
        assert _decision(outcome=ended).answered_at is None
        assert not _decision(outcome=ended).is_open


def test_reversibility_and_standing_have_no_default_to_put_in_the_askers_mouth() -> None:
    from pptmstr.model import Decision

    required = {
        f.name
        for f in dataclasses.fields(Decision)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    assert {"reversible", "standing", "evidence", "recommendation"} <= required
    assert {"blocks", "if_unanswered"} <= required
    assert _decision().session_id == "s"


def test_the_root_reads_every_decision_of_its_session_and_a_sub_agent_only_its_own() -> None:
    from pptmstr.model import Snapshot

    root, worker, sibling, elsewhere = ("s", None), ("s", "a1"), ("s", "a2"), ("t", None)
    held = {
        "d-w": _decision(id="d-w", asker=worker, raised_at=2.0),
        "d-r": _decision(id="d-r", asker=root, raised_at=3.0),
        "d-s": _decision(id="d-s", asker=sibling, raised_at=1.0),
        "d-t": _decision(id="d-t", asker=elsewhere, raised_at=0.0),
    }
    snap = dataclasses.replace(Snapshot.empty(), decisions=held)

    def ids(node: tuple[str, str | None]) -> list[str]:
        return [d.id for d in snap.decisions_readable_by(node)]

    assert ids(root) == ["d-s", "d-w", "d-r"], "its session's, oldest first"
    assert ids(worker) == ["d-w"] and ids(sibling) == ["d-s"]
    assert ids(elsewhere) == ["d-t"]
    # What an agent may withdraw is narrower than what it may read.
    assert [d.id for d in snap.decisions_of(root)] == ["d-r"]


def test_an_asker_that_has_ended_is_three_fates_and_an_idle_one_is_not_among_them() -> None:
    """
    ``has_ended`` is what frees a place in a session's allowance. An agent whose
    turn is over can be woken and one that has not announced itself may be
    working, so neither has ended.
    """
    from pptmstr.model import AskerFate

    assert {fate for fate in AskerFate if fate.has_ended} == {
        AskerFate.STOPPED,
        AskerFate.FAILED,
        AskerFate.GONE,
    }
    assert {fate.value for fate in AskerFate} == {
        "live",
        "idle",
        "stopped",
        "failed",
        "unannounced",
        "gone",
    }


def test_the_decision_bounds_are_the_sizes_one_reply_was_built_from() -> None:
    """
    The numbers, pinned: ``bus`` sums them against the bound on a reply, and the
    answer box reads ``MAX_ANSWER_CHARS`` to refuse a draft before it is sent.
    """
    from pptmstr import model

    assert model.MAX_DECISION_TEXT_CHARS == 1_000
    assert model.MAX_ANSWER_CHARS == 4_000
    assert model.MAX_BLOCKED_TASKS == 8
    assert model.MAX_OPEN_DECISIONS_PER_ASKER == 8
    assert model.MAX_OPEN_SUBAGENT_DECISIONS == 24
    assert model.MAX_OPEN_DECISIONS_PER_ASKER < model.MAX_OPEN_SUBAGENT_DECISIONS
    assert not hasattr(model, "MAX_OPEN_DECISIONS")


def test_a_dependency_is_resolved_on_the_tasks_own_board() -> None:
    """
    One map holds every session's tasks. A finished task of another session
    under the id is not this task's dependency, and neither is one with no
    declarer, which is on no board at all.
    """
    from pptmstr.model import Task, TaskState

    mine = Task(id="m", title="m", depends_on=("x",), declared_by=("s1", None))
    done = TaskState.COMPLETED

    def over(declared_by: tuple[str, str | None] | None) -> dict[str, Task]:
        return {"x": Task(id="x", title="x", state=done, declared_by=declared_by)}

    assert mine.blocked_on(over(("s1", "agent-a"))) == ()
    assert mine.blocked_on(over(("s2", None))) == ("x",)
    assert mine.blocked_on(over(None)) == ("x",)
    assert mine.dependency(over(("s2", None)), "x") is None
    assert not mine.is_claimable(over(("s2", None)))


def test_a_task_on_no_board_has_its_dependencies_looked_up_as_they_are() -> None:
    from pptmstr.model import Task, TaskState

    loose = Task(id="m", title="m", depends_on=("x",))
    theirs = {"x": Task(id="x", title="x", state=TaskState.COMPLETED, declared_by=("s2", None))}

    assert loose.blocked_on(theirs) == () and loose.dependency(theirs, "x") is theirs["x"]


# -- final states, a recalled record, and the off-fleet predicate --------------------


def test_failed_is_terminal_and_not_final() -> None:
    """
    The distinction a session leaving the fleet rests on: the driver reports an
    error result as FAILED with the client connected, and the next message makes
    the session ordinary again. An end that a status update undoes is not one a
    session can be hidden behind.
    """
    from pptmstr.model import FINAL_STATES

    assert FINAL_STATES == {AgentState.DONE, AgentState.CANCELLED}
    assert {s for s in AgentState if s.is_final} == FINAL_STATES
    assert AgentState.FAILED.is_terminal and not AgentState.FAILED.is_final
    assert all(s.is_terminal for s in FINAL_STATES)


def test_a_recalled_value_claims_nothing_the_file_did_not_say() -> None:
    from pptmstr.model import Recalled, TranscriptPresence

    nothing = Recalled()

    assert nothing.transcript is TranscriptPresence.UNKNOWN
    said = {f.name: getattr(nothing, f.name) for f in dataclasses.fields(nothing)}
    del said["transcript"]
    assert set(said.values()) == {None}


def test_the_ended_how_spellings_are_the_session_files() -> None:
    """Deliberate duplication, pinned: the file's module owns the spellings."""
    from pptmstr import sessionfile
    from pptmstr.model import EndedHow

    assert tuple(how.value for how in EndedHow) == sessionfile.ENDED_HOW


def _rec(node: tuple[str, str | None], state: AgentState, **fields: Any) -> AgentRecord:
    return AgentRecord(
        node_id=node,
        parent=None if node[1] is None else (node[0], None),
        depth=0 if node[1] is None else 1,
        state=state,
        topic="",
        task="t",
        model="m",
        **fields,
    )


def _world(*records: AgentRecord, needs_you: tuple[Any, ...] = ()) -> Snapshot:
    return dataclasses.replace(
        Snapshot.empty(),
        nodes={r.node_id: r for r in records},
        order=tuple(r.node_id for r in records),
        needs_you=needs_you,
    )


def test_the_gate_shown_for_a_recalled_record_is_the_files_or_nothing() -> None:
    """
    ``policy`` on a recalled record is STRICT because the field cannot say
    nothing. Shown as the session's gate, that describes a session that ran
    autonomous as strict.
    """
    from pptmstr.model import Recalled

    ran = _rec(("s", None), AgentState.DONE, policy=Policy.PERMISSIVE)
    unknown = _rec(("s", None), AgentState.DONE, recalled=Recalled())
    autonomous = _rec(("s", None), AgentState.DONE, recalled=Recalled(policy=Policy.AUTONOMOUS))

    assert ran.granted_policy is Policy.PERMISSIVE
    assert unknown.policy is Policy.STRICT and unknown.granted_policy is None
    assert autonomous.granted_policy is Policy.AUTONOMOUS


def test_off_the_fleet_is_asked_of_the_snapshot_and_not_read_off_the_flag() -> None:
    """
    A snapshot built by hand, with the flag set on sessions that may not be off
    the fleet: the store never leaves one like this, and the predicate does not
    rely on that.
    """
    from pptmstr.model import ApprovalNeeded, DetachRefusal, PendingApproval

    over = _world(
        _rec(("s", None), AgentState.DONE, detached=True),
        _rec(("s", "a"), AgentState.CANCELLED),
        _rec(("other", None), AgentState.THINKING),
    )
    assert over.detach_refusal("s") is None and over.off_fleet("s")
    assert not over.off_fleet("other") and not over.off_fleet("nobody")
    assert over.detach_refusal("nobody") is DetachRefusal.NO_SUCH_SESSION

    failed = _world(_rec(("s", None), AgentState.FAILED, detached=True, acknowledged=True))
    assert failed.detach_refusal("s") is DetachRefusal.FAILED_NOT_CLOSED
    assert not failed.off_fleet("s")

    woken = _world(
        _rec(("s", None), AgentState.DONE, detached=True),
        _rec(("s", "a"), AgentState.THINKING),
    )
    assert woken.detach_refusal("s") is DetachRefusal.NOT_ENDED and not woken.off_fleet("s")
    # A live agent beside a failed one is the first mistake, not the second.
    both = _world(
        _rec(("s", None), AgentState.FAILED, detached=True),
        _rec(("s", "a"), AgentState.AWAITING_INPUT),
    )
    assert both.detach_refusal("s") is DetachRefusal.NOT_ENDED

    # A sub-agent whose parent link is missing is still of the session.
    orphan = dataclasses.replace(_rec(("s", "a"), AgentState.RUNNING_TOOL), parent=None)
    stray = _world(_rec(("s", None), AgentState.DONE, detached=True), orphan)
    assert not stray.off_fleet("s")

    approval = PendingApproval(
        id="p",
        node=("s", "a"),
        tool_name="Write",
        tool_use_id="t",
        raw_args={},
        summary="w",
        requested_at=1.0,
    )
    owed = _world(
        _rec(("s", None), AgentState.DONE, detached=True),
        _rec(("s", "a"), AgentState.DONE),
        needs_you=(ApprovalNeeded(node=("s", "a"), since=1.0, summary="w", approval=approval),),
    )
    assert owed.detach_refusal("s") is DetachRefusal.OWES and not owed.off_fleet("s")

    never_asked = _world(_rec(("s", None), AgentState.DONE))
    assert never_asked.detach_refusal("s") is None and not never_asked.off_fleet("s")


def test_a_recalled_root_never_makes_a_sub_agent_one_of_an_earlier_run() -> None:
    """
    ``of_earlier_run`` compares start stamps with the root's, and a recalled
    root's is 0.0: no sub-agent started before it.
    """
    from pptmstr.model import Recalled

    world = _world(
        _rec(("s", None), AgentState.DONE, recalled=Recalled()),
        _rec(("s", "a"), AgentState.DONE, started_at=5.0),
    )

    assert not world.of_earlier_run(("s", "a")) and not world.of_earlier_run(("s", None))


def test_a_collision_names_the_session_that_holds_the_id() -> None:
    held = NotPlaced("task", "t1", Unplaced.OTHER_SESSION_HAS_ID, holder="sess-2")

    assert held.holder == "sess-2"
    assert NotPlaced("task", "t1", Unplaced.REPEATED).holder is None


def test_a_fork_starts_from_the_task_and_never_from_a_resumes_message() -> None:
    """
    ``message`` is one line of the conversation a resume continues. A fork or a
    retry begins a new conversation, and a spec built from a record has no
    message to carry whatever the session was last sent.
    """
    assert LaunchSpec(task="t", model="m").message is None
    record = AgentRecord(
        node_id=("s1", None),
        parent=None,
        depth=0,
        state=AgentState.DONE,
        topic="",
        task="audit the parser",
        model="claude-sonnet-5",
    )
    assert LaunchSpec.from_record(record).message is None
    carried = {f.name for f in dataclasses.fields(LaunchSpec)} - {
        "containment",
        "policy",
        "subagent_cap",
        "resume",
        "message",
    }
    assert carried == {
        "task",
        "model",
        "cwd",
        "session_base",
        "template",
        "brief",
        "task_is_label",
    }


def _titled(task: str, **over: object) -> AgentRecord:
    return AgentRecord(
        node_id=("s1", None),
        parent=None,
        depth=0,
        state=AgentState.DONE,
        topic="",
        task=task,
        model="claude-sonnet-5",
        **over,  # type: ignore[arg-type]
    )


def test_a_title_that_is_only_a_label_says_so_on_a_closed_card_and_after_its_resume() -> None:
    """
    Two records of one session: the one recalled from a file with no task, and
    the one a resume of it announces. A spec built from either says its task is
    no prompt, which is what the shell refuses a fork or a relaunch on.
    """
    from pptmstr.model import Recalled

    closed = _titled("A summary", recalled=Recalled(task_recorded=False))
    resumed = _titled("A summary", task_is_label=True)
    for record in (closed, resumed):
        assert record.titled_by_label
        assert LaunchSpec.from_record(record).task_is_label is True

    # A file that says nothing either way is not a file that says "a label", and
    # a recalled record's own flag is not read: its file's account is.
    for record in (
        _titled("build it"),
        _titled("build it", recalled=Recalled(task_recorded=True)),
        _titled("build it", recalled=Recalled()),
        _titled("build it", recalled=Recalled(task_recorded=True), task_is_label=True),
    ):
        assert not record.titled_by_label
        assert LaunchSpec.from_record(record).task_is_label is False
