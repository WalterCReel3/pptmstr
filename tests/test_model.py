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
    AgentRecord,
    AgentState,
    LaunchSpec,
    normalised_touches,
    relative_write,
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
