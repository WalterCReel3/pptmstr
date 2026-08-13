"""
Work templates: a team shape, as data (§2.7, §8 step 8).

A template names the roles a piece of work needs, what each of them is for, and
what the lead is supposed to do with them. The driver turns roles into
``AgentDefinition``s and the lead briefing into a system-prompt append; nothing in
here imports the SDK.

That boundary is deliberate, and it is the same one ``approval.py`` keeps. A
template is configuration -- the kind of thing that eventually comes from a file
the operator edits -- so it is worth being able to write, read and test one without
an agent runtime anywhere near it.

**The prompts are the feature.** Roles are cheap; a lead that implements the work
itself instead of delegating, or workers that agree with each other, are the two
ways a team produces less than one agent would. Both are prompt problems. The lead
briefing is written to say *wait* and *synthesise*, and the review roles are
written to disagree -- an investigator told to "check the work" reports that it
looks fine, while one told to find the case that breaks it goes looking.
"""

from __future__ import annotations

from dataclasses import dataclass

# Tool names every role gets regardless of what else it is allowed, because they
# are how a role participates in the team at all. A worker without read_inbox
# cannot be told anything; one without post_concern cannot answer.
BUS_TOOL_NAMES = (
    "mcp__pptmstr__post_concern",
    "mcp__pptmstr__read_inbox",
    "mcp__pptmstr__claim_task",
    "mcp__pptmstr__declare_task",
    "mcp__pptmstr__complete_task",
    "mcp__pptmstr__release_task",
)

# Enough to inspect a codebase without changing it. Used by the review roles, whose
# value depends on their not being able to quietly fix what they were asked to find.
READ_ONLY_TOOLS = ("Read", "Glob", "Grep", "NotebookRead", "WebFetch", "WebSearch")


@dataclass(frozen=True, slots=True)
class Role:
    """
    One named worker in a template.

    ``name`` is lowercase because it is also the bus address: the lead writes
    ``post_concern(to="qa")``, and ``AgentSession.resolve_role`` lowercases what it
    is given. Two roles differing only in case would be one address.

    ``tools`` None means "inherit everything the session has". A tuple restricts,
    and the bus tools are added to it by ``tool_list`` rather than by whoever wrote
    the role -- forgetting them would produce a worker that cannot be reached, which
    looks like a hung agent rather than a misconfigured one.
    """

    name: str
    description: str
    prompt: str
    tools: tuple[str, ...] | None = None
    model: str | None = None

    def tool_list(self) -> list[str] | None:
        if self.tools is None:
            return None
        return [*self.tools, *BUS_TOOL_NAMES]


@dataclass(frozen=True, slots=True)
class WorkTemplate:
    """
    A named team shape.

    ``spawn_order`` is advisory and lands in the lead's briefing rather than being
    enforced. We do not spawn workers -- the lead does, through the ``Agent`` tool --
    so the only lever available is telling it the order that works. It matters less
    here than it would with ``SendMessage``: the bus resolves a role name at the
    moment a concern is posted, so a worker only has to exist by the time someone
    writes to it, not by the time its correspondent started.
    """

    name: str
    description: str
    lead_prompt: str
    roles: tuple[Role, ...] = ()
    spawn_order: tuple[str, ...] = ()

    def role(self, name: str) -> Role | None:
        key = name.strip().lower()
        return next((r for r in self.roles if r.name == key), None)

    def role_names(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.roles)

    def ordered_roles(self) -> tuple[Role, ...]:
        """Roles in spawn order, with anything unlisted after the ones that are."""
        listed = [r for name in self.spawn_order if (r := self.role(name)) is not None]
        rest = [r for r in self.roles if r not in listed]
        return (*listed, *rest)


# -- the briefing -----------------------------------------------------------------


def lead_briefing(template: WorkTemplate) -> str:
    """
    What the lead is told, on top of its ordinary system prompt.

    Generated rather than written out per template so the role names in the prose
    cannot drift from the roles actually passed to the SDK. A briefing naming a
    teammate that does not exist is worse than no briefing: the lead spends turns
    trying to reach it.
    """
    if not template.roles:
        return template.lead_prompt.strip()

    lines = [template.lead_prompt.strip(), "", "## Your team", ""]
    for role in template.ordered_roles():
        lines.append(f"- **{role.name}** — {role.description}")

    if template.spawn_order:
        order = " → ".join(template.spawn_order)
        lines += ["", f"Start them in this order when the work allows it: {order}."]

    lines += [
        "",
        "## How the team coordinates",
        "",
        "Use the Agent tool with `subagent_type` set to a role name to start one.",
        "",
        "- `declare_task(task_id, title, detail, depends_on)` puts work on a shared",
        "  board. `depends_on` names tasks that must finish first; anything blocked",
        "  becomes claimable on its own the moment its dependencies complete.",
        "- Workers call `claim_task()` to take the oldest unblocked item. Declare the",
        "  work and let them claim it rather than assigning it by hand.",
        "- `post_concern(to, subject, body)` sends a message to a role by name, or to",
        "  `lead` for you. `read_inbox()` collects what has been sent to you.",
        "- Messages between agents are reviewed by the operator before they arrive, so",
        "  write them to be read by a person as well as by their recipient.",
        "",
        "## Your job",
        "",
        "Break the work into tasks and put them on the board. Start the roles you",
        "need. Then **wait** — read your inbox, answer concerns, and let the workers",
        "work. Do not implement the task yourself while a worker is doing it; two",
        "agents editing the same file is the failure this structure exists to avoid.",
        "",
        "**Call `read_inbox()` before you write your final answer**, every time. A",
        "worker's concern is not the same thing as its result: the result is what it",
        "was asked for, and the concern is what it noticed on the way, which is",
        "usually the part you did not know to ask about.",
        "",
        "Then synthesise the result yourself. That synthesis is your output, not a",
        "list of what each worker said.",
    ]
    return "\n".join(lines)


def worker_prompt(role: Role) -> str:
    """
    A role's own prompt, plus the part every worker needs.

    Appended here rather than repeated in each role for the obvious reason, and
    because the bus half is what makes a worker a teammate rather than a sub-agent
    that happens to run alongside others.
    """
    return "\n".join(
        [
            role.prompt.strip(),
            "",
            "## Working with the team",
            "",
            "Call `read_inbox()` when you start and again whenever you finish a piece",
            "of work — a teammate may have told you something that changes it.",
            "",
            "Take work with `claim_task()`; call `complete_task(task_id)` when it is",
            "done, or `release_task(task_id)` if you cannot proceed, so somebody else",
            "can. Do not work on something you have not claimed.",
            "",
            "**Before you finish, post a concern to `lead`** naming the thing you are",
            "least sure about, or what you noticed that nobody asked you to look at.",
            "Your returned result answers the question you were given; the concern is",
            "for everything else, and it is the only way that reaches anyone.",
            "",
            "Use `post_concern(to, subject, body)` to raise something with another",
            "role too. Say plainly when you disagree with another agent rather than",
            "deferring to it — an agreement nobody tested is worth nothing here.",
        ]
    )


# -- built-ins --------------------------------------------------------------------

SOLO = WorkTemplate(
    name="solo",
    description="One agent, no team. The default.",
    lead_prompt="",
)

FEATURE = WorkTemplate(
    name="feature",
    description="A lead that plans, a worker that builds, and a reviewer that attacks it.",
    lead_prompt=(
        "You are coordinating a small team on a software change. You plan and "
        "integrate; your team implements and reviews."
    ),
    spawn_order=("reviewer", "builder"),
    roles=(
        Role(
            name="builder",
            description="Implements the change. Give it one task at a time.",
            prompt=(
                "You implement changes. Work only on the task you have claimed, "
                "keep the change as small as it can be and still be correct, and "
                "write a test for anything non-obvious.\n\n"
                "When the reviewer raises a concern, treat it as a finding to be "
                "confirmed or refuted, not an order. If you think it is wrong, say "
                "why in a concern back to it."
            ),
        ),
        Role(
            name="reviewer",
            description=("Reads what the builder produced and tries to break it. Cannot edit."),
            prompt=(
                "You review changes by trying to break them, not by confirming they "
                "look reasonable. Your value is the case nobody thought of.\n\n"
                "For each change: find an input, ordering or failure mode that "
                "produces a wrong result, and say concretely what it is. If you "
                "cannot find one, say that plainly rather than inventing a stylistic "
                "objection to have something to report.\n\n"
                "You have no editing tools on purpose. Report; do not fix."
            ),
            tools=READ_ONLY_TOOLS,
        ),
    ),
)

RESEARCH = WorkTemplate(
    name="research",
    description="A coordinator and two investigators briefed to disagree with each other.",
    lead_prompt=(
        "You are coordinating an investigation. You do not investigate; you frame "
        "the question, put the lines of enquiry on the board, and reconcile what "
        "comes back into one answer that says how confident it is and why."
    ),
    spawn_order=("investigator", "skeptic"),
    roles=(
        Role(
            name="investigator",
            description="Builds the case. Gathers evidence for the most likely answer.",
            prompt=(
                "You investigate a question and build the best-supported answer you "
                "can. Cite what you actually read -- file and line, or the source -- "
                "rather than what you recall. Say which parts of your answer are "
                "evidence and which are inference.\n\n"
                "A skeptic is reading your work and looking for the hole in it. Make "
                "its job hard by being explicit about your weakest step."
            ),
            tools=READ_ONLY_TOOLS,
        ),
        Role(
            name="skeptic",
            description="Attacks the investigator's conclusion. Reports what would refute it.",
            prompt=(
                "Your job is to refute the investigator's conclusion, not to check "
                "it. Assume it is wrong and look for the reason.\n\n"
                "Attack the evidence first: was the thing actually read, or recalled? "
                "Does the cited source say what it is claimed to say? Then attack the "
                "inference. Report the specific observation that would settle it "
                "either way.\n\n"
                "Concluding that it survives your attack is a real and useful result. "
                "Say so, and say what you tried -- a refutation you did not attempt "
                "is not evidence of anything.\n\n"
                "Post your verdict to `lead` as a concern before you finish, and "
                "post it to `investigator` as well if it is still working. Saying "
                "only in your own result that the conclusion is unsound leaves the "
                "coordinator to notice the disagreement for itself."
            ),
            tools=READ_ONLY_TOOLS,
        ),
    ),
)

BUILT_IN: tuple[WorkTemplate, ...] = (SOLO, FEATURE, RESEARCH)


def by_name(name: str) -> WorkTemplate | None:
    key = name.strip().lower()
    return next((t for t in BUILT_IN if t.name == key), None)


def names() -> tuple[str, ...]:
    return tuple(t.name for t in BUILT_IN)
