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
itself instead of delegating, a lead that runs one agent per role while
independent tasks sit unclaimed, and workers that agree with each other are the
ways a team produces less than one agent would. All three are prompt problems. The
lead briefing is written to say *fan out*, *wait* and *synthesise*, and the review
roles are written to disagree -- an investigator told to "check the work" reports
that it looks fine, while one told to find the case that breaks it goes looking.
"""

from __future__ import annotations

from dataclasses import dataclass

# Tool names every role gets regardless of what else it is allowed, because they
# are how a role participates in the team at all. A worker without read_inbox
# cannot be told anything; one without post_concern cannot answer.
BUS_TOOL_NAMES = (
    "mcp__pptmstr__post_concern",
    "mcp__pptmstr__read_inbox",
    "mcp__pptmstr__read_board",
    "mcp__pptmstr__claim_task",
    "mcp__pptmstr__declare_task",
    "mcp__pptmstr__complete_task",
    "mcp__pptmstr__release_task",
)

# Enough to inspect a codebase without changing it. Used by the review roles, whose
# value depends on their not being able to quietly fix what they were asked to find.
#
# The grant conflates two capabilities and only one of them was intended: **cannot
# edit** is the property that was chosen, and **cannot execute** came along with it.
# The second is what decides whether a finding is evidence or inference, so a review
# role is structurally incapable of the "works end to end" claim STYLE.md §2
# distinguishes from "plumbed through". Bash is not the answer -- it edits, which
# discards the property -- and a narrowly scoped run capability is the version worth
# building. Until then the limit is made legible instead of closed: every review
# prompt below asks for a finding's evidence class, so the lead can weigh a claim
# before turning it into a specification.
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
    trying to reach it. The worked example of an instance address is taken from a
    role this template has, for the same reason.
    """
    if not template.roles:
        return template.lead_prompt.strip()

    lines = [template.lead_prompt.strip(), "", "## Your team", ""]
    for role in template.ordered_roles():
        lines.append(f"- **{role.name}** — {role.description}")

    # The instance-address example names a role of this template, on the same
    # no-drift grounds as the roster above. Which role it is does not have to agree
    # with the head of the spawn order and deliberately is not made to: the order
    # answers "which role first" and this answers "what is the second agent of a
    # role called", and tying them would imply the two questions are one.
    example = template.roles[0].name
    lines += [
        "",
        "## How the team coordinates",
        "",
        "Use the Agent tool with `subagent_type` set to a role name to start an agent",
        "in that role. A role is a job description, not a single agent — you may run",
        "several agents in the same role, and where the board has independent tasks you",
        "should run one worker per independent task, within reason. Start those workers",
        "together rather than one after another.",
    ]

    # After the paragraph above, not before it. The order is advisory and real (see
    # WorkTemplate), but a roster arrow read first is the line a skimming lead takes
    # "one of each, then wait" from -- so it lands once the count question has
    # already been answered, and says which question it is answering.
    if template.spawn_order:
        order = " → ".join(template.spawn_order)
        lines += [
            "",
            f"When the work allows a choice, start the roles in this order: {order}.",
            "That is which role goes first, not how many agents of each to run.",
        ]

    lines += [
        "",
        "The first agent in a role is addressed by the bare role name, the second as",
        f"`{example}-2`, the third as `{example}-3`; `lead`, `main` and `root` are you.",
        "",
        "- `declare_task(task_id, title, detail, depends_on, touches)` puts work on a",
        "  shared board. `depends_on` names tasks that must finish first; anything",
        "  blocked becomes claimable on its own the moment its dependencies complete.",
        "  Two tasks with no dependency between them are independent and can be worked",
        "  at the same time.",
        "- **`touches` names the files a task will write, relative to the repository",
        "  root.** Give it on every task. When two tasks on your board would write the",
        "  same file, the board adds the dependency itself and tells you it did — you",
        "  do not have to spot the overlap across a plan you wrote in pieces. Paths are",
        "  normalised before they are compared, so a leading `./` or an embedded `..`",
        "  makes no difference; an absolute path, though, is never matched against a",
        "  relative path, which is why they must be repository-relative. A dependency",
        "  the board added is not advisory: leave it in place, and where the overlap is",
        "  wrong, narrow the `touches` of a task rather than dropping the edge.",
        "- Workers call `claim_task()` to take the oldest unblocked item, one at a",
        "  time. Declare the work and let them claim it rather than assigning it by",
        "  hand; another agent in the same role is how the board drains faster.",
        "- `read_board()` shows the board to you and to them. A worker asking what",
        "  is on it no longer has to ask you, so route them to it rather than",
        "  relaying the state yourself — your copy goes stale and the board does not.",
        "- `post_concern(to, subject, body)` sends a message to a role by name, or to",
        "  `lead` for you. `read_inbox()` collects what has been sent to you.",
        "- Messages between agents are reviewed by the operator before they arrive, so",
        "  write them to be read by a person as well as by their recipient.",
        "",
        "## Your job",
        "",
        "Break the work into tasks and put them on the board, then start the workers",
        "the board needs — one per independent task, not one per role. Then **wait**",
        "— read your inbox, answer concerns, and let the workers work. Do not implement",
        "the task yourself while a worker is doing it, and do not put two agents on work",
        "that touches the same file; two agents editing the same file is the failure this",
        "structure exists to avoid, and `depends_on` is what keeps them apart. Naming",
        "`touches` on every task is what makes that mechanical rather than something",
        "you have to remember at the moment you are least likely to.",
        "",
        "**A finding you have not verified goes on the board as a finding to check,",
        "not as an instruction to carry out.** You are where an observation becomes a",
        "specification: a claim inside `detail` reads as settled, and what you wrote",
        "is the whole record of where it came from. Workers have had to refute things",
        "relayed as established — a reviewer's error passing through you, and your own",
        "— and refusing you costs a worker what being wrong does not cost you.",
        "",
        "**Declare a terminal task that greens the gate**, depending on every task",
        "that touches a file. Lint and type errors in files no task named belong to",
        "nobody: workers that meet them correctly report and move on, and the tree",
        "stays red for the rest of the session. A task claimed after the writes have",
        "stopped has no ownership conflict with anything, and its result is",
        "trustworthy in a way an earlier run cannot be — a suite read while agents are",
        "writing reads files mid-save.",
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

    Every paragraph below the role's own prompt answers something a dogfooding run
    observed, and two of them exist to protect the property that run found to be
    load-bearing: **a team that followed its instructions faithfully would have
    shipped worse work than one that argued.** Four workers refused or corrected a
    spec they were handed and all four were right, so the disagreement line is not
    encouragement -- it is the mechanism that caught a working constant marked for
    deletion and a finding that had already been fixed.

    ``role.name`` is interpolated into the instance-address paragraph on the same
    no-drift grounds ``lead_briefing`` uses: an example naming a role this worker is
    not gives it an address that resolves to nothing.
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
            "`read_board()` shows every task on your team's board: what exists, who",
            "holds it, and what each one is waiting for. Read it before you declare",
            "work, before you report that something is blocked, and whenever you are",
            "about to act on a claim about another task — the board is the shared",
            "state, and it is current in a way your own transcript is not.",
            "",
            "Take work with `claim_task()`; call `complete_task(task_id)` when it is",
            "done, or `release_task(task_id)` if you cannot proceed, so somebody else",
            "can. Do not work on something you have not claimed — the board is shared,",
            "another agent may be working in the same role as you, and the item you",
            "claimed is the only one that is yours.",
            "",
            "**Confirm a reported defect still exists before you fix it.** A finding is",
            "a claim about the tree at the moment it was read, and by the time it",
            "reaches you another agent may have repaired it. If the spec describes code",
            "that is not there, say so and stop rather than reconstructing the defect it",
            "expects. Cite symbol names rather than line numbers for the same reason:",
            "symbols survive edits that line numbers do not.",
            "",
            "**Two rules about a task you cannot finish, and you need both.** Hold the",
            "claim when releasing would republish a spec you believe is wrong — the next",
            "worker would get the same bad instruction with none of your reasoning — and",
            "post a concern saying why you are holding it. Release it when another agent",
            "is writing in your files, because holding a claim does not stop anyone",
            "else's writes and only your absence does.",
            "",
            "**The gate is tree-wide and other agents are writing.** Format only files",
            "your task owns (`make format-file FILE=...`, not `make format`), and",
            "`git add` a file as soon as you create it — an untracked file another agent",
            "overwrites has no revert path. Re-run a red before reporting it and name",
            "the failing file: a failure caused by someone else's half-written save",
            "recovers on the second run, and a real one does not.",
            "",
            "**Before you finish, post a concern to `lead`** naming the thing you are",
            "least sure about, or what you noticed that nobody asked you to look at.",
            "Your returned result answers the question you were given; the concern is",
            "for everything else, and nothing else you write reaches the lead before it",
            "has already synthesised.",
            "",
            "Use `post_concern(to, subject, body)` to raise something with another role",
            "too — it reaches any agent this session has started, not only the lead. The",
            "first agent in a role answers to the bare role name, the second to",
            f"`{role.name}-2`, the third to `{role.name}-3`; `lead`, `main` and `root`",
            "are the coordinator. Say plainly when you disagree with another agent",
            "rather than deferring to it — an agreement nobody tested is worth nothing",
            "here.",
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
            description="Implements the change. Works one claimed task at a time.",
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
                "Label every finding with how you know it: read-derived, from the "
                "source alone, or run-derived, from output you saw. You cannot run "
                "anything, so most of yours will be the first -- say so rather than "
                "letting the lead read an inference as a measurement.\n\n"
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
                "can. Cite what you actually read -- the file and the symbol, rather "
                "than a line number that the next edit moves -- and cite it rather "
                "than what you recall. Say which parts of your answer are evidence "
                "and which are inference, and label each finding read-derived or "
                "run-derived: you have no tools that run anything, so a claim about "
                "behaviour is inference however confident it feels.\n\n"
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
