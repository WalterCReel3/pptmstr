"""
Work templates: the team shape, and the briefing generated from it.

Templates are configuration, so these are ordinary data tests -- no SDK, no
threads. The properties worth pinning are the ones that fail silently: a briefing
that names a role the SDK was never given, a worker that cannot be reached because
its tool list omitted the bus, and a default that quietly stops being solo.
"""

from __future__ import annotations

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
