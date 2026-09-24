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
from pptmstr.approval import Policy
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
    "when two tasks on your board would write the same file",
)


def _counted_clauses(text: str) -> list[str]:
    """Every clause of ``text`` that puts a number on something, whitespace flattened."""
    flat = " ".join(text.split())
    return [c.strip() for c in re.split(r"[.;—]", flat) if _COUNT_WORD.search(c.lower())]


@pytest.mark.parametrize("policy", list(Policy))
@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_a_lead_is_not_told_to_drain_a_parallelisable_board_through_one_worker(
    template: WorkTemplate, policy: Policy
) -> None:
    """
    The hazard is a lead that declares four independent tasks, starts one agent of
    each role, and then waits while a single worker claims them in turn. Every
    count noun in this briefing is what decides that: a role has to read as a job
    description that several agents can hold, and the fan-out has to be tied to the
    number of independent tasks rather than to the number of roles.

    No number is stated on purpose. A figure in the prose is a figure that
    disagrees with whatever ceiling the operator is actually running under.

    Run over every policy, because the allowlist is over the generated body and
    each policy generates a different one. The unattended text talks about a cap
    on how many agents run at once, which is exactly the subject matter this
    excludes -- it is allowed to say what the cap is and not allowed to turn that
    into an instruction about fan-out.
    """
    briefing = lead_briefing(template, policy)
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


# -- the premises a worker is told about (row 5, step 3) ---------------------------
#
# Prose is the only channel, and that is measured rather than assumed: run `84cb7f`
# set `AgentDefinition.initialPrompt` on one probe role with its canary nowhere else
# and the worker reported NONE. What the same run established is that a worker *can*
# read an absolute path outside cwd, with the canary captured off the wire.


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_a_worker_with_no_brief_is_told_nothing_about_one(template: WorkTemplate) -> None:
    """
    Most sessions have none. A heading about premises that do not exist sends a
    worker looking for a directory it will not find.
    """
    for role in template.roles:
        assert "premises this session" not in worker_prompt(role)


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_a_worker_is_given_the_path_and_not_a_description_of_it(
    template: WorkTemplate,
) -> None:
    path = "/home/x/.claude/projects/-x-orbital/briefs/sess-1"
    for role in template.roles:
        assert path in worker_prompt(role, path)


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_a_worker_is_told_to_read_the_directory_not_a_file(template: WorkTemplate) -> None:
    """
    Append-only plus permitted contradiction means a superseded premise is still on
    disk. A worker that opened one obvious filename would act on the version that
    was overturned, and that footgun only surfaces in a session already gone wrong.
    """
    for role in template.roles:
        prompt = worker_prompt(role, "/briefs/s1")
        assert "every file in" in prompt
        assert "directory and not a file" in prompt
        assert "brief.md" not in prompt


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_a_worker_is_told_supersession_changes_which_entry_is_current(
    template: WorkTemplate,
) -> None:
    for role in template.roles:
        prompt = worker_prompt(role, "/briefs/s1")
        assert "supersedes" in prompt
        assert "overturn" in prompt


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_premises_are_framed_as_a_finding_not_an_order(template: WorkTemplate) -> None:
    """
    The only check on a wrong amendment is a worker arguing from evidence, which the
    dogfooding run credits with saving it. The wording is lifted from the builder
    role's framing of a reviewer's concern rather than invented as a second register
    -- one operator writing for every worker concentrates authority further than any
    concern does, so the framing matters more here, not less.
    """
    for role in template.roles:
        assert "confirmed or refuted, not an" in worker_prompt(role, "/briefs/s1")


def test_the_premises_framing_reuses_the_builders_own_words() -> None:
    """Pinned, so the two registers cannot drift into disagreeing with each other."""
    builder = FEATURE.role("builder")
    assert builder is not None
    assert "confirmed or refuted, not an order" in builder.prompt
    assert "confirmed or refuted, not an order" in worker_prompt(builder, "/briefs/s1")


# -- who is watching, and what the prompts say about it ----------------------------
#
# Under `Policy.AUTONOMOUS` every reviewed tool is auto-approved, so `post_concern`
# and `declare_task` reach nobody on the way past. The prompts are the only thing
# that tells an agent that, and a sentence that was true under STRICT is not made
# false loudly -- an agent acts on it and waits.


_UNATTENDED_HEADING = "## This session runs unattended"


def _unattended_block(text: str) -> str:
    """The unattended section of a prompt, or "" when the prompt has none."""
    if _UNATTENDED_HEADING not in text:
        return ""
    return text.split(_UNATTENDED_HEADING, 1)[1].split("\n## ", 1)[0]


def test_the_policy_defaults_to_strict_in_both_prompts() -> None:
    """
    Every existing call site passes no policy, so the default is what decides
    whether this change is invisible to them. Asserted as equality with STRICT
    rather than by reading the signature: a default flipped here would turn every
    ordinary session into one whose agents are told nobody is watching.
    """
    role = Role(name="w", description="d", prompt="p")
    assert lead_briefing(FEATURE) == lead_briefing(FEATURE, Policy.STRICT)
    assert worker_prompt(role, "/briefs/s1") == worker_prompt(role, "/briefs/s1", Policy.STRICT)


def test_the_strict_briefing_generates_the_bytes_it_generated_before() -> None:
    """
    The whole of the STRICT text, captured from this function before it learned
    about policies. A session under the default must read exactly what it read,
    and "the parts I remembered to assert are still there" is a weaker claim than
    that -- prose is additive, so every presence assertion in this file stays green
    while a paragraph is inserted next to it.

    The template's own ``lead_prompt`` is referenced rather than transcribed: it is
    data this function is handed, not text it generates, and pinning it here would
    make editing FEATURE's prompt fail a test about the briefing.
    """
    assert (
        lead_briefing(FEATURE, Policy.STRICT) == FEATURE.lead_prompt.strip() + _FEATURE_STRICT_BODY
    )


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_a_strict_prompt_says_nothing_about_running_unattended(template: WorkTemplate) -> None:
    # An operator is attached, so the section would be false. Checked on the worker
    # prompt too: the lead briefing is not read by anybody else.
    assert _UNATTENDED_HEADING not in lead_briefing(template)
    for role in template.roles:
        assert _UNATTENDED_HEADING not in worker_prompt(role, "/briefs/s1")


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_an_unattended_lead_is_not_told_a_person_reads_its_messages(
    template: WorkTemplate,
) -> None:
    """
    The sentence this task exists for. `post_concern` parks under STRICT and an
    operator reads it on the way past; under AUTONOMOUS it is auto-approved and
    lands. A lead told otherwise writes its concerns for a reader that does not
    exist, and can wait for a reply a person was supposed to prompt.

    Both halves are asserted, because deleting the false sentence without saying
    what replaced it leaves the lead with no account of who reads a concern.
    """
    strict = _flat(lead_briefing(template, Policy.STRICT))
    unattended = _flat(lead_briefing(template, Policy.AUTONOMOUS))
    assert "reviewed by the operator before they arrive" in strict
    assert "reviewed by the operator" not in unattended
    assert "nobody else reads it" in unattended
    assert "rather than reaching a person" in unattended


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_an_unattended_team_is_told_no_answer_is_coming(template: WorkTemplate) -> None:
    """
    The failure this prevents is an agent that raises a question and stops. With
    nobody to answer it, a question is only worth asking if the team answers it
    itself, so the instruction has to name what to do with it instead.

    The wording deliberately avoids "no operator is attached", which is the
    driver's *headless* denial -- a run nobody could have watched. This mode is a
    run nobody is watching on purpose, and the two send an agent to different
    conclusions about whether trying again later would help.
    """
    prompts = [lead_briefing(template, Policy.AUTONOMOUS)] + [
        worker_prompt(role, "/briefs/s1", Policy.AUTONOMOUS) for role in template.roles
    ]
    for prompt in prompts:
        flat = _flat(prompt)
        assert "This session parks nothing at a person" in flat
        assert "a choice the operator made rather than an accident" in flat
        assert "no operator is attached" not in flat.lower()
        assert "record the decision and the reason" in flat
        # And that the premises will not grow a clarification mid-run, which is the
        # other thing an agent waits for.
        assert "nothing is added to them while it runs" in flat


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_an_unattended_team_is_told_where_the_work_is_bounded(template: WorkTemplate) -> None:
    """
    The containment is real and an agent that does not know its shape spends turns
    rediscovering it: a denied write reads as a bug rather than as a boundary, and
    the useful response -- record the step as blocked -- is not the obvious one.

    `WebFetch` is named because it is the carve-out. An agent that inferred "the
    sandbox denies the network" from the Bash clause would conclude documentation
    is unreachable and stop looking it up, which is a capability the operator chose
    to grant knowing it is not bounded by the sandbox.
    """
    prompts = [lead_briefing(template, Policy.AUTONOMOUS)] + [
        worker_prompt(role, "/briefs/s1", Policy.AUTONOMOUS) for role in template.roles
    ]
    for prompt in prompts:
        flat = _flat(prompt)
        assert "Writes outside the directory this session was launched in are denied" in flat
        assert "`WebFetch` and `WebSearch` are not bounded that way" in flat
        assert "record as blocked" in flat


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_an_unattended_team_is_told_the_cap_is_a_capacity_answer(
    template: WorkTemplate,
) -> None:
    """
    Once spawns auto-approve, `subagent_cap` is the only volume control left, so a
    refusal at the cap becomes something agents meet routinely. `_at_cap_reason` in
    the driver already tells the caller the call itself was fine; this says the same
    thing in advance, and the two must not disagree -- an agent told a refusal is a
    rejection rewrites a request that was never wrong.

    Not asserted against the driver's string, deliberately: these tests import no
    SDK, and `driver.py` does. The agreement is a reading obligation on whoever
    edits either one.
    """
    prompts = [lead_briefing(template, Policy.AUTONOMOUS)] + [
        worker_prompt(role, "/briefs/s1", Policy.AUTONOMOUS) for role in template.roles
    ]
    for prompt in prompts:
        flat = _flat(prompt)
        assert "`subagent_cap` bounds how many agents run at once" in flat
        assert "an answer about capacity rather than about the call" in flat


@pytest.mark.parametrize("template", [FEATURE, RESEARCH])
def test_the_lead_and_its_workers_are_told_the_same_thing(template: WorkTemplate) -> None:
    """
    One block for both, because these are facts about the session rather than
    instructions to a role. A lead that believes a write outside the directory
    lands and a worker that knows it does not will disagree about why a task
    failed, and that disagreement is expensive to find in a transcript.
    """
    block = _unattended_block(lead_briefing(template, Policy.AUTONOMOUS))
    assert block.strip()
    for role in template.roles:
        worker = _unattended_block(worker_prompt(role, "/briefs/s1", Policy.AUTONOMOUS))
        assert worker == block, role.name


def test_an_unattended_worker_is_still_told_about_the_premises() -> None:
    # The two sections are adjacent and the unattended one is the reason the first
    # matters more: the brief is all there is, so a worker that skipped it has no
    # second source.
    role = Role(name="w", description="d", prompt="p")
    prompt = worker_prompt(role, "/briefs/s1", Policy.AUTONOMOUS)
    assert "premises this session" in prompt
    assert prompt.index("## The premises") < prompt.index(_UNATTENDED_HEADING)


_FEATURE_STRICT_BODY = "\n\n" + """\
## Your team

- **reviewer** — Reads what the builder produced and tries to break it. Cannot edit.
- **builder** — Implements the change. Works one claimed task at a time.

## How the team coordinates

Use the Agent tool with `subagent_type` set to a role name to start an agent
in that role. A role is a job description, not a single agent — you may run
several agents in the same role, and where the board has independent tasks you
should run one worker per independent task, within reason. Start those workers
together rather than one after another.

When the work allows a choice, start the roles in this order: reviewer → builder.
That is which role goes first, not how many agents of each to run.

The first agent in a role is addressed by the bare role name, the second as
`builder-2`, the third as `builder-3`; `lead`, `main` and `root` are you.

- `declare_task(task_id, title, detail, depends_on, touches)` puts work on a
  shared board. `depends_on` names tasks that must finish first; anything
  blocked becomes claimable on its own the moment its dependencies complete.
  Two tasks with no dependency between them are independent and can be worked
  at the same time.
- **`touches` names the files a task will write, relative to the directory
  this session was launched in.** Give it on every task. When two tasks on
  your board would write the same file, the board adds the dependency itself
  and tells you it did — you do not have to spot the overlap across a plan you
  wrote in pieces. Paths are normalised before they are compared, so a leading
  `./` or an embedded `..` makes no difference; an absolute path, though, is
  never matched against a relative path, which is why they must be relative to
  the launch directory and to nothing else. A dependency the board added is
  not advisory: leave it in place, and where the overlap is wrong, narrow the
  `touches` of a task rather than dropping the edge.
- Workers call `claim_task()` to take the oldest unblocked item, one at a
  time. Declare the work and let them claim it rather than assigning it by
  hand; another agent in the same role is how the board drains faster.
- `read_board()` shows the board to you and to them. A worker asking what
  is on it no longer has to ask you, so route them to it rather than
  relaying the state yourself — your copy goes stale and the board does not.
- `post_concern(to, subject, body)` sends a message to a role by name, or to
  `lead` for you. `read_inbox()` collects what has been sent to you.
- Messages between agents are reviewed by the operator before they arrive, so
  write them to be read by a person as well as by their recipient.

## Your job

Break the work into tasks and put them on the board, then start the workers
the board needs — one per independent task, not one per role. Then **wait**
— read your inbox, answer concerns, and let the workers work. Do not implement
the task yourself while a worker is doing it, and do not put two agents on work
that touches the same file; two agents editing the same file is the failure this
structure exists to avoid, and `depends_on` is what keeps them apart. Naming
`touches` on every task is what makes that mechanical rather than something
you have to remember at the moment you are least likely to.

**A finding you have not verified goes on the board as a finding to check,
not as an instruction to carry out.** You are where an observation becomes a
specification: a claim inside `detail` reads as settled, and what you wrote
is the whole record of where it came from. Workers have had to refute things
relayed as established — a reviewer's error passing through you, and your own
— and refusing you costs a worker what being wrong does not cost you.

**Declare a terminal task that greens the gate**, depending on every task
that touches a file. Lint and type errors in files no task named belong to
nobody: workers that meet them correctly report and move on, and the tree
stays red for the rest of the session. A task claimed after the writes have
stopped has no ownership conflict with anything, and its result is
trustworthy in a way an earlier run cannot be — a suite read while agents are
writing reads files mid-save.

**Call `read_inbox()` before you write your final answer**, every time. A
worker's concern is not the same thing as its result: the result is what it
was asked for, and the concern is what it noticed on the way, which is
usually the part you did not know to ask about.

Then synthesise the result yourself. That synthesis is your output, not a
list of what each worker said."""
