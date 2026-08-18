"""
Work templates: the team shape, and the briefing generated from it.

Templates are configuration, so these are ordinary data tests -- no SDK, no
threads. The properties worth pinning are the ones that fail silently: a briefing
that names a role the SDK was never given, a worker that cannot be reached because
its tool list omitted the bus, and a default that quietly stops being solo.
"""

from __future__ import annotations

import re

import pytest

from pptmstr import templates
from pptmstr.templates import (
    BUS_TOOL_NAMES,
    FEATURE,
    RESEARCH,
    SOLO,
    Role,
    WorkTemplate,
    lead_briefing,
    worker_prompt,
)


def test_solo_is_first_so_teams_are_opt_in() -> None:
    # The launcher's default is index 0. If this order changes, every launch that
    # does not touch the combo silently becomes a team.
    assert templates.names()[0] == "solo"
    assert templates.BUILT_IN[0] is SOLO


def test_solo_briefs_nothing() -> None:
    # No roles, no briefing, no system-prompt append -- a lone agent must behave
    # exactly as it did before templates existed.
    assert lead_briefing(SOLO) == ""


def test_every_built_in_has_a_unique_name() -> None:
    names = templates.names()
    assert len(set(names)) == len(names)


def test_role_names_are_lowercase_because_they_are_addresses() -> None:
    """
    A role name is what the model writes in post_concern(to=...), and
    AgentSession.resolve_role lowercases its argument. Two roles differing only in
    case would be one address, and the second would be unreachable.
    """
    for template in templates.BUILT_IN:
        for role in template.roles:
            assert role.name == role.name.lower(), f"{template.name}:{role.name}"


def test_lookup_is_case_insensitive_and_trimmed() -> None:
    assert FEATURE.role("  BUILDER ") is not None
    assert templates.by_name("  FEATURE  ") is FEATURE
    assert templates.by_name("nope") is None


# -- the briefing -----------------------------------------------------------------


def test_the_briefing_names_exactly_the_roles_that_exist() -> None:
    """
    Generated rather than written out, so the prose cannot drift from the roles
    actually handed to the SDK. A briefing naming a teammate that does not exist
    costs the lead turns discovering it cannot be reached.
    """
    for template in templates.BUILT_IN:
        briefing = lead_briefing(template)
        for role in template.roles:
            assert f"**{role.name}**" in briefing
        for other in templates.BUILT_IN:
            for role in other.roles:
                if template.role(role.name) is None:
                    assert f"**{role.name}**" not in briefing


def test_the_briefing_tells_the_lead_to_wait() -> None:
    # The failure mode a lead prompt exists to prevent: a lead that implements the
    # work itself while a worker is doing the same thing.
    briefing = lead_briefing(FEATURE)
    assert "wait" in briefing.lower()
    assert "do not implement" in briefing.lower()


# -- the count nouns the briefing is allowed to use -------------------------------
#
# The rewrite that removed "one agent per role" was argued from an **absence**: the
# retired briefing contained no "several", no "more than one", nothing plural
# applied to a role, so the singular was the whole instruction the lead had. Prose
# is additive, so an assertion that the new wording is present cannot keep the old
# one out -- a sentence appended to the fan-out paragraph, or to `## Your job`,
# restores "start one of each and wait" with every presence assertion still green.
#
# So this is an allowlist over count nouns rather than a blocklist of phrasings.
# Every clause of the generated body that puts a number on something has to be one
# of the ones below; anything else is by construction a statement about how many
# agents something gets, which is the class of instruction being excluded. Adding
# an entry here is a decision about fan-out, not a formatting fix.

_COUNT_WORD = re.compile(r"\b(one|two|single|only|exactly)\b")

_ALLOWED_COUNTS = (
    # A role is not an agent, and the fan-out is tied to the board's shape rather
    # than to the roster's length.
    "not a single agent",
    "one worker per independent task",
    "one per independent task, not one per role",
    "rather than one after another",
    # A worker's own throughput, stated on the tool that makes it true.
    "unblocked item, one at a time",
    # The bound on parallelism that is real: two writers, one file.
    "two tasks with no dependency",
    "two agents on work",
    "two agents editing the same file",
)


def _counted_clauses(text: str) -> list[str]:
    """Every clause of ``text`` that puts a number on something, whitespace flattened."""
    flat = " ".join(text.split())
    return [c.strip() for c in re.split(r"[.;—]", flat) if _COUNT_WORD.search(c.lower())]


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_a_lead_is_not_told_to_drain_a_parallelisable_board_through_one_worker(
    template: WorkTemplate,
) -> None:
    """
    The hazard is a lead that declares four independent tasks, starts one agent of
    each role, and then waits while a single worker claims them in turn. Every
    count noun in this briefing is what decides that: a role has to read as a job
    description that several agents can hold, and the fan-out has to be tied to the
    number of independent tasks rather than to the number of roles.

    No number is stated on purpose. A figure in the prose is a figure that
    disagrees with whatever ceiling the operator is actually running under.
    """
    briefing = lead_briefing(template)
    assert "several agents in the same role" in briefing
    assert "one per independent task, not one per role" in briefing
    assert "independent" in briefing

    # And nothing anywhere below the roster caps a role at one agent. Scoped from
    # this heading because a lead_prompt above it is the template's own words --
    # RESEARCH's asks for "one answer" -- while everything after it is generated
    # here and is the text this rewrite owns.
    body = briefing.split("## How the team coordinates", 1)[1]
    for clause in _counted_clauses(body):
        assert any(ok in clause.lower() for ok in _ALLOWED_COUNTS), clause

    # The one retired phrasing with no number in it. "Them" is the roster, and a
    # roster instantiated once is exactly the reading being removed.
    assert "start them in this order" not in briefing.lower()


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_the_briefing_gives_the_address_of_the_second_agent_in_a_role(
    template: WorkTemplate,
) -> None:
    """
    Fan-out without this line produces agents the lead cannot answer. A role's bus
    address is instance-keyed -- the first agent holds the bare name and later ones
    take a suffix -- and nothing else in the session tells the lead that, so a
    concern meant for the second builder goes to the first.

    The worked example is generated from a role this template has, for the same
    reason the roster is: a hardcoded `builder-2` would name a teammate the
    research team does not have.
    """
    example = template.roles[0].name
    briefing = lead_briefing(template)
    assert f"`{example}-2`" in briefing
    for other in templates.BUILT_IN:
        for role in other.roles:
            if template.role(role.name) is None:
                assert f"{role.name}-2" not in briefing


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_running_several_agents_does_not_licence_two_writers_on_one_file(
    template: WorkTemplate,
) -> None:
    """
    Parallelism is granted across *independent* tasks only. `depends_on` is the
    mechanism that makes a task independent, so the briefing has to name it where
    it tells the lead to fan out -- otherwise "one worker per task" reads as
    permission to put two agents on the same file.
    """
    job = lead_briefing(template).split("## Your job", 1)[1]
    assert "two agents editing the same file" in job
    assert "`depends_on`" in job
    assert "**wait**" in job


# The only count a description may carry. A description is one line of the lead's
# roster, so a number in it is read as a number of agents no matter what it was
# written about -- "Run exactly one builder" and "Give it one task at a time" both
# land there as a ceiling. This one is a fact about the worker's own claimed work
# and says so grammatically: the subject is the worker, not the lead.
_ALLOWED_COUNTS_IN_A_DESCRIPTION = ("works one claimed task at a time",)


def test_a_role_description_bounds_the_worker_not_the_leads_fan_out() -> None:
    """
    A description is rendered into the lead's roster, so it is read as an
    instruction to the lead. "Give it one task at a time" is true of the worker --
    `claim_task()` returns a single item -- and false as a cap on how many agents
    the role may run, which is what the lead sees. The throughput claim belongs on
    the tool that makes it true.

    Checked as an absence over every count noun rather than as a blocklist of the
    phrasing that was there before: the roster is generated, so any description can
    put a ceiling in the briefing, and the ceiling does not have to be spelled the
    way the retired one was.
    """
    for template in templates.BUILT_IN:
        for role in template.roles:
            where = f"{template.name}:{role.name}"
            for clause in _counted_clauses(role.description):
                assert any(
                    ok in clause.lower() for ok in _ALLOWED_COUNTS_IN_A_DESCRIPTION
                ), f"{where}: {clause}"
            # The retired phrasing itself, named because it is what was there.
            assert "give it" not in role.description.lower(), where
    assert "take the oldest unblocked item, one at a" in lead_briefing(FEATURE)


def test_the_briefing_carries_the_spawn_order_when_there_is_one() -> None:
    assert "reviewer → builder" in lead_briefing(FEATURE)


def test_a_template_without_a_spawn_order_says_nothing_about_one() -> None:
    plain = WorkTemplate(
        name="plain",
        description="d",
        lead_prompt="p",
        roles=(Role(name="w", description="d", prompt="p"),),
    )
    assert "→" not in lead_briefing(plain)


def test_ordered_roles_puts_listed_ones_first_and_keeps_the_rest() -> None:
    template = WorkTemplate(
        name="t",
        description="d",
        lead_prompt="p",
        roles=(
            Role(name="a", description="d", prompt="p"),
            Role(name="b", description="d", prompt="p"),
            Role(name="c", description="d", prompt="p"),
        ),
        spawn_order=("c", "a"),
    )
    # Nothing is dropped by being unlisted. A role missing from the briefing is a
    # role the lead never starts.
    assert [r.name for r in template.ordered_roles()] == ["c", "a", "b"]


def test_an_unknown_name_in_the_spawn_order_is_skipped_not_fatal() -> None:
    template = WorkTemplate(
        name="t",
        description="d",
        lead_prompt="p",
        roles=(Role(name="a", description="d", prompt="p"),),
        spawn_order=("typo", "a"),
    )
    assert [r.name for r in template.ordered_roles()] == ["a"]


# -- tools ------------------------------------------------------------------------


def test_a_restricted_role_still_gets_the_bus() -> None:
    """
    The bus is how a worker is a teammate rather than a sub-agent running nearby.
    A restricted role that lost it would look like a hung agent -- reachable in the
    lead's mental model, silent in fact -- so tool_list adds it rather than trusting
    whoever wrote the role.
    """
    role = Role(name="r", description="d", prompt="p", tools=("Read",))
    tools = role.tool_list()
    assert tools is not None
    assert set(BUS_TOOL_NAMES) <= set(tools)
    assert "Read" in tools


def test_an_unrestricted_role_inherits_everything() -> None:
    # None means "inherit", and adding the bus names here would turn an inherit
    # into a restriction that silently drops Edit and Bash.
    assert Role(name="r", description="d", prompt="p").tool_list() is None


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_review_roles_cannot_edit(template: WorkTemplate) -> None:
    """
    A reviewer that can quietly fix what it was asked to find stops reporting.
    These roles are read-only on purpose, and the restriction is the feature.
    """
    for role in template.roles:
        if role.name in ("reviewer", "skeptic"):
            tools = role.tool_list()
            assert tools is not None
            assert not {"Edit", "Write", "MultiEdit", "Bash"} & set(tools), role.name


def test_the_bus_names_in_templates_match_the_bus() -> None:
    # templates.py is SDK-free and spells the tool names rather than importing
    # them from bus.py, which is not. This keeps the two copies honest.
    from pptmstr.bus import BUS_TOOLS

    assert set(BUS_TOOL_NAMES) == set(BUS_TOOLS)


# -- worker prompts ---------------------------------------------------------------


def test_a_worker_is_told_to_read_its_inbox_and_claim_work() -> None:
    prompt = worker_prompt(Role(name="w", description="d", prompt="You build things."))
    assert "You build things." in prompt
    assert "read_inbox()" in prompt
    assert "claim_task()" in prompt
    assert "release_task" in prompt


def test_the_adversarial_roles_are_told_to_disagree() -> None:
    """
    The whole reason a reviewer is worth a second agent. One told to "check the
    work" reports that it looks fine; one told to find what breaks it goes looking.
    """
    reviewer = FEATURE.role("reviewer")
    skeptic = RESEARCH.role("skeptic")
    assert reviewer is not None and skeptic is not None
    assert "break" in reviewer.prompt.lower()
    assert "refute" in skeptic.prompt.lower()
    # And told that finding nothing is a real answer, so they do not manufacture
    # objections to justify their existence.
    assert "cannot find one" in reviewer.prompt.lower()
    assert "survives" in skeptic.prompt.lower()


def test_a_worker_is_required_to_post_a_concern_before_finishing() -> None:
    """
    Measured, not assumed. The first live team run declared tasks, claimed them and
    completed them without posting a single concern -- because a sub-agent's result
    already returns to the lead through the Agent tool, so the model had no reason
    to use the bus at all. The bus only earns its place if the prompt says what it
    is *for*: the thing the result does not carry.
    """
    prompt = worker_prompt(Role(name="w", description="d", prompt="p"))
    assert "post a concern to `lead`" in prompt
    assert "least sure about" in prompt


def test_the_lead_is_required_to_read_its_inbox_before_answering() -> None:
    # The other half of the same finding: a posted concern nobody reads is the same
    # as no concern.
    briefing = lead_briefing(RESEARCH)
    assert "read_inbox()` before you write your final answer" in briefing


# -- what a dogfooding run said the prompts get wrong ------------------------------
#
# Each of these pins a sentence a worker or lead was observed to act on, or the
# absence of one it was observed to be misled by. They are string assertions and
# that is what they are worth: prose does not bind, and a test that a sentence is
# present is a test that nobody deleted it, not that anybody followed it.


def _flat(text: str) -> str:
    """
    The prompt with its line wrapping removed.

    These prompts are written as lists of source lines, so a phrase can be split by
    a newline that means nothing to the model reading it. Asserting on the wrapped
    form would make rewrapping a paragraph fail a test about its content.
    """
    return " ".join(text.split())


def test_a_worker_is_not_told_the_lead_is_the_only_agent_it_can_reach() -> None:
    """
    ``post_concern`` resolves any address the session has spawned -- a builder can
    post to `reviewer` or to `builder-2` and it lands. The prompt nonetheless said
    of posting to the lead that *"it is the only way that reaches anyone"*, and that
    is the sentence a worker acts on. The channel needed nothing; the sentence did.
    """
    prompt = _flat(worker_prompt(Role(name="w", description="d", prompt="p")))
    assert "the only way that reaches anyone" not in prompt
    assert "not only the lead" in prompt


def test_a_worker_is_told_the_instance_addresses_the_lead_already_gets() -> None:
    """
    ``lead_briefing`` explains that the second agent in a role is `builder-2`;
    nothing told the workers. A session running six builders was six agents each of
    which could reach only the roles it could guess.

    Interpolated from the role, so a worker is given an address that resolves --
    the same no-drift rule the briefing's roster follows.
    """
    prompt = _flat(worker_prompt(Role(name="builder", description="d", prompt="p")))
    assert "`builder-2`" in prompt
    assert "`lead`, `main` and `root`" in prompt


def test_both_the_hold_rule_and_the_release_rule_are_stated_together() -> None:
    """
    Two workers did opposite things and both were right: one held a task rather
    than republish a spec the operator had superseded, one released a task because
    another agent was writing in its files. The hazards differ, so the rules differ,
    and a worker given only the rule it happened to read applies it to the other
    case. Whoever writes one owes the other beside it.
    """
    prompt = _flat(worker_prompt(Role(name="w", description="d", prompt="p")))
    hold = prompt.index("Hold the")
    release = prompt.index("Release it when")
    assert hold < release, "both rules are present, and in the same paragraph"
    assert "republish a spec you believe is wrong" in prompt
    assert "only your absence does" in prompt


def test_a_worker_is_told_to_confirm_a_defect_before_fixing_it() -> None:
    """
    A finding is a claim about the tree at an instant; a task is an instruction
    about the tree later. Three times in one session the tree had moved -- once
    inside the same task that found the defect, which is the shortest window
    possible and still produced a wrong instruction.
    """
    prompt = _flat(worker_prompt(Role(name="w", description="d", prompt="p")))
    assert "Confirm a reported defect still exists before you fix it" in prompt
    assert "symbols survive edits that line numbers do not" in prompt.lower()


def test_the_lead_is_told_an_unverified_finding_is_not_an_instruction() -> None:
    """
    The lead is where an observation acquires authority, and nothing in the passage
    tests it. Twice, a worker with less authority than the sender had to refute a
    claim the lead had relayed as established -- which is the opposite of how this
    structure is supposed to fail, and it is taxed: refusing costs a worker
    something that being wrong does not cost the lead.
    """
    briefing = _flat(lead_briefing(FEATURE))
    assert "finding to check" in briefing
    assert "not as an instruction to carry out" in briefing


def test_the_lead_is_told_to_declare_a_task_that_greens_the_gate() -> None:
    """
    A gate failure in a file no task named belongs to nobody: every worker that met
    one correctly reported it and moved on, and the tree stayed red all session.
    ``depends_on`` already expresses the fix and was simply never declared.

    It also buys the only trustworthy gate run of the session, because a full-suite
    result taken while other agents are writing reads files mid-save.
    """
    briefing = _flat(lead_briefing(FEATURE))
    assert "terminal task that greens the gate" in briefing
    assert "depending on every task that" in briefing


def test_a_worker_is_pointed_at_the_target_that_cannot_cross_a_boundary() -> None:
    """
    ``make format`` is the one target that writes source files, and one agent used
    it to reformat another agent's untracked file -- no revert path, because
    untracked. The rule is worth nothing without the invocation beside it.
    """
    prompt = _flat(worker_prompt(Role(name="w", description="d", prompt="p")))
    assert "make format-file FILE=" in prompt
    assert "not `make format`" in prompt
    assert "`git add` a file as soon as you create it" in prompt


def test_a_worker_is_told_to_re_run_a_red_before_reporting_it() -> None:
    """
    A shared tree plus a tree-wide suite is a shared mutable resource: `make test`
    went red twice mid-session inside a file another builder was writing, and
    nothing was ever broken. A transient recovers on the second run and a real
    failure does not, which separates them at nearly zero cost.
    """
    prompt = _flat(worker_prompt(Role(name="w", description="d", prompt="p")))
    assert "Re-run a red before reporting it and name" in prompt


@pytest.mark.parametrize("role", [FEATURE.role("reviewer"), RESEARCH.role("investigator")])
def test_a_role_that_cannot_run_anything_says_so_on_its_findings(role: Role | None) -> None:
    """
    ``READ_ONLY_TOOLS`` was chosen to stop a reviewer quietly fixing what it was
    asked to find. It also stops it running anything, which was not the intent and
    is what decides whether a finding is evidence or inference -- STYLE.md §2's own
    distinction, applied to the role that structurally cannot make the second claim.

    Both reviewers on the observed run disclosed this voluntarily. The prompt makes
    it reliable, which is what lets the lead weigh a finding before turning it into
    a specification.
    """
    assert role is not None
    assert "run-derived" in _flat(role.prompt)
    assert "read-derived" in _flat(role.prompt)


def test_the_target_the_worker_prompt_names_is_a_target_that_exists() -> None:
    """
    The prompt tells a worker to run `make format-file FILE=...` instead of the
    tree-wide `make format`, and the rule is worth nothing if the invocation is
    not real: a worker that meets "No rule to make target" falls back to the one
    command it knows works, which is the one that crosses file boundaries.

    STYLE.md §3 names this shape -- a duplicated constant with no test pinning it.
    The duplication is deliberate, because a prompt cannot import a Makefile, so
    this is the pin.
    """
    import re
    from pathlib import Path

    makefile = (Path(__file__).resolve().parents[1] / "Makefile").read_text()
    targets = set(re.findall(r"^([a-zA-Z_-]+):", makefile, re.MULTILINE))
    prompt = _flat(worker_prompt(Role(name="w", description="d", prompt="p")))

    for named in re.findall(r"make ([a-z-]+)", prompt):
        assert named in targets, f"worker_prompt names `make {named}`, which is not a target"
    # And the one it is pointed away from is still there to be pointed away from.
    assert {"format", "format-file"} <= targets
