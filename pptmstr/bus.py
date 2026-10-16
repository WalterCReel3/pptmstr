"""
The in-process bus the agents talk over (§2.7).

An MCP server built with ``create_sdk_mcp_server``, one per session, closed over
that session's ``Bridge``. No subprocess: the CLI reaches these handlers back
through the same control channel it uses for hooks.

**The handler does not know who called it.** The CLI delivers an SDK MCP call as a
``mcp_message`` control request carrying ``server_name`` and a raw JSON-RPC body,
and the body carries ``name`` and ``arguments`` -- no session, no agent, no
tool-use id. The ``can_use_tool`` branch on the same switch is handed both
``tool_use_id`` and ``agent_id``, so the omission is the protocol's design rather
than an oversight.

A sender passed as an ordinary argument would therefore be a sender the *model*
chose. The gate supplies one instead: ``PreToolUse`` sees the call with
``agent_id`` attached and rewrites the arguments through ``updatedInput`` to carry
an authenticated ``_from`` (``driver.AgentSession._stamp_bus_call``). Measured end
to end in ``scripts/verify_message_bus.py``.

Two consequences worth stating plainly:

- **An unstamped call is refused, never defaulted.** A call with no ``_from`` did
  not come through the gate, and the only honest response is to fail loudly. A
  default would silently attribute a message to whichever node seemed likely.
- **The stamp is applied after the operator's edit**, so it cannot be forged by
  the edit-then-approve path either (§5.3).

Everything a handler returns to the model is text. Everything it changes goes
through the intent queue, and **a handler never composes an outcome it did not
wait for**. Every tool but one parks on the Bridge's third crossing and is
answered by the store on the next frame (``effects.py``): ``read_inbox``,
``read_board``, ``read_task``, ``read_digest``, ``read_decisions`` and
``claim_task``, which ask questions, and the four board writes and the two
decision writes, which assert facts the reducer is entitled to refuse.

**The bus raises and withdraws a decision and never ends one.** An answer and a
dismissal are the operator's, and the reducer tells them from an agent's only by
the intent naming no node (``store._ended_by_operator``), so nothing here builds
``DecisionAnswered`` or ``DecisionDismissed``: a handler that did would be the
only thing between an agent and writing its own answer.

``post_concern`` is the exception and is not in this class. Its only refusal is the
``resolve_role`` check it makes here, before emitting, and it reports that one
honestly out of ``role_status``.

**Three tools are on the server of a contained session and of no other**:
``request_command``, ``read_commands`` and ``withdraw_command``, whose handlers
and words are ``commandbus``'s and which ``build_server`` registers when
``session.contained`` says the CLI process was started with containment. They ask
the operator to run a command outside the sandbox, so a session with no sandbox
has nothing to ask for: its ``Bash`` already reaches the operator through the
ordinary gate, under the operator's own CLI permission rules, which a command
pptmstr runs never passes through. The session's rung is not consulted: a
contained session narrowed to ``STRICT`` is still inside the sandbox.
``offered_tools`` is the list as one session has it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, assert_never

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import brief
from .board import (
    UNDELIVERABLE,
    BoardConcern,
    BoardTask,
    BoardVersion,
    basis_label,
    closed_label,
    foreign_wait_label,
    incident_marker,
    not_run_label,
    property_label,
    role_name,
    stale_label,
    version_label,
)
from .bridge import Bridge
from .digest import (
    EARLIER_INCIDENTS,
    ChangeKind,
    Digest,
    Owed,
    Reported,
    TaskChange,
    Unverified,
    change_label,
    changed_heading,
    incident_attribution,
    incident_label,
    incidents_heading,
    owed_counts,
    owed_standing,
    unverified_heading,
    unverified_label,
)
from .effects import (
    BoardDelivered,
    ClaimSettled,
    DecisionsDelivered,
    DecisionSettled,
    DigestDelivered,
    Effect,
    InboxDelivered,
    SessionWoken,
    TaskWriteSettled,
    WakeReason,
)
from .intents import (
    BoardRead,
    ConcernPosted,
    DecisionRaised,
    DecisionsRead,
    DecisionWithdrawn,
    DigestRead,
    InboxRead,
    TaskAmended,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
    TaskVersionHanded,
)
from .model import (
    BEFORE_RESTART,
    MAX_BLOCKED_TASKS,
    MAX_DECISION_TEXT_CHARS,
    MAX_OPEN_DECISIONS_PER_ASKER,
    MAX_OPEN_SUBAGENT_DECISIONS,
    MAX_PROPERTY_CHARS,
    Answered,
    Basis,
    ClaimRefusal,
    Concern,
    ConcernId,
    ConcernState,
    Decision,
    DecisionRefusal,
    Dismissed,
    NodeId,
    ObligationKind,
    Snapshot,
    Standing,
    Task,
    TaskId,
    TaskRefusal,
    TaskState,
    Withdrawn,
    closing_result,
    is_closing_notice,
    is_operator,
    normalised_property,
    property_overrun,
    stated_flag,
    stated_incident,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to the checker
    from .driver import AgentSession

SERVER_NAME = "pptmstr"

# One task's specification in a board read. Generous: this is the field the tool
# exists to expose, and a lead that has to ask for the rest of its own spec is the
# defect being fixed. Bounded at all because a board read is a reply an agent pays
# for by the token, and one 40,000-character detail would crowd out every other row.
_MAX_DETAIL_CHARS = 2000

# One result in a board read: what a claimer reported when it finished. The same
# size as a specification because it is the same kind of text -- a paragraph one
# agent wrote for the next -- and the reader of a finished row needs it for the
# reason the reader of an open one needs the specification.
_MAX_RESULT_CHARS = 2000

# One agent note in a board read. Tighter than a specification: a concern is one
# finding, and a reader scanning a board wants the finding rather than the essay.
# Announced when it bites, like every other bound here.
_MAX_CONCERN_CHARS = 1200

# What a claimer listed as not run, in a board read. A note's size, because it is
# the same kind of text: a short list of findings about the work, not an account
# of it. Whether such a list exists at all is never cut -- see ``_board_line``.
_MAX_NOT_RUN_CHARS = 1200

# The whole reply. The two bounds above are per row, and twelve bounded rows still
# came to 50.8 KB in one measured read and 62.8 KB in another -- past the tool's
# output cap both times, so the agent that was told the board is authoritative could
# not read it. A bound on each row is not a bound on their sum.
#
# The number is a judgement and the measurements are what it answers: both overflows
# were above it, and 30,000 characters is roughly 7.5k tokens, which is a large but
# affordable slice of a worker's context to spend on one board read. Nothing here
# knows the tool's actual ceiling -- it is the harness's, not ours -- so this is set
# below both observations rather than derived from it.
_MAX_BOARD_CHARS = 30_000

# The first line of a result, in a digest. A digest names what finished and the
# board holds the account; a line is enough to tell which finished row to open.
_MAX_DIGEST_LINE_CHARS = 200

# -- what a call may carry ---------------------------------------------------------
#
# Every text an agent sends is bounded where it is accepted, because the board log
# refuses a record over ``boardlog.MAX_RECORD_BYTES`` (1,048,576) and the first
# record that does not land stops that session's log for the rest of the process
# (``boardsync``). An unbounded argument is therefore a way for one call to end the
# record of a whole session.
#
# The arithmetic. ``boardlog.encode`` escapes to ASCII, and the most a character
# costs there is twelve bytes: one outside the Basic Multilingual Plane is written
# as two ``\uXXXX`` escapes. The records the bus can cause, at their largest:
#
#   declaration   12 x (24,000 detail + 500 title + 300 property + 200 id
#                       + 64 x 200 depends_on + 64 x 300 touches)   =  684,000
#   completion    12 x (24,000 result + 24,000 not_run + 200 id)    =  578,400
#   concern       12 x (24,000 body + 500 subject + 200 task id)    =  296,400
#   amendment     12 x (24,000 detail + 300 property + 200 id)      =  294,000
#
# so the largest is about two thirds of the bound, and the rest is room for the
# keys, the addresses and the punctuation. tests/test_bus.py encodes each of these
# with ``boardlog.encode`` and fails when one stops fitting.
#
# Each limit is several times anything this project has seen an agent write: the
# longest specification on a measured board was about 6,000 characters and the
# longest result under 10,000.

# A specification, a result, a list of what was not run, and a concern's body. A
# decision's texts have a bound of their own, far under this one
# (``model.MAX_DECISION_TEXT_CHARS``).
#
# Under ``_MAX_BOARD_CHARS`` on purpose, and a test holds the two to it: that
# ceiling sits below two replies an agent was measured unable to read, so a text
# accepted here is one a reply can carry whole with room for what stands round
# it. ``read_task`` relies on that twice: for the current specification beside
# the task's structure, and for any one other text read alone (``_part_text``).
# It is not what makes a read a showing -- the length of the whole reply is.
MAX_TEXT_CHARS = 24_000

# A title or a subject: the fields laid out on one line.
MAX_LINE_CHARS = 500

# A task id, wherever one is written rather than looked up.
MAX_ID_CHARS = 200

# One path in ``touches``.
MAX_PATH_CHARS = 300

# How many entries ``depends_on`` and ``touches`` may each have.
MAX_LISTED = 64

# What a cut result or list ends with, at most. Reserved out of ``MAX_TEXT_CHARS``
# so the text and the sentence saying it was cut are together inside the bound.
_CUT_NOTE_ROOM = 200

# The key the gate writes the authenticated sender into. Leading underscore so it
# reads as ours rather than as something the model was meant to fill, and absent
# from every tool's declared schema so a model has no reason to invent it.
FROM_KEY = "_from"

# The key the gate writes when the operator rewrote a call before approving it.
# Same convention and the same reason as FROM_KEY: the gate is the only
# participant that can know, so a handler reading it out of its own arguments
# would be reading something the model wrote.
#
# It exists because ``Concern.edited`` is otherwise unsettable. The operator's
# rewrite reaches the handler as ordinary edited arguments, and a Concern built
# from them is indistinguishable from one the sender wrote itself -- so the store
# kept the edited text and no record that it was edited.
EDITED_KEY = "_edited"


# The argument three tools share: how the author knows what its text asserts.
#
# No ``enum`` in the schema, and that is a choice. A closed schema is enforced
# ahead of the handler, with the validator's sentence rather than ours, and it is
# exact about case -- so the ``READ`` every agent in the session that prompted this
# was already writing by hand would be refused before anything here could accept
# it. The handler normalises and refuses instead (``_stated_basis``).
_BASIS_PROPERTY: dict[str, Any] = {
    "type": "string",
    "description": (
        "How you know what this text asserts: 'run' (you executed something and "
        "are reporting its output), 'read' (you established it by reading code or "
        "records and executed nothing), or 'relayed' (you are passing on what "
        "another agent or a record said and did not check it). If the text mixes "
        "these, give the weakest that applies to anything in it -- relayed, then "
        "read, then run -- and say the rest in the text. It is shown beside the "
        "text as your own statement, so a reader knows whether to check it before "
        "building on it. Omit it and the text is shown as 'basis unstated'."
    ),
}


# The type a yes-or-no argument is announced under.
#
# Both, for the reason ``_BASIS_PROPERTY`` carries no ``enum``: the server checks a
# call against its schema ahead of the handler, so under a bare ``boolean`` the
# ``"true"`` a model writes is refused with the validator's sentence before
# ``model.stated_flag`` can read it. Boolean first, because that is the spelling
# the description asks for.
_FLAG_TYPE: dict[str, Any] = {"type": ["boolean", "string"]}


class InvalidBasis(Exception):
    """
    A ``basis`` that is none of the three members.

    Never coerced to the nearest member, which would state a standing the author
    did not choose. ``declare_task`` and ``post_concern`` refuse the call: nothing
    is held by either, so the author corrects the word and sends it again.
    ``complete_task`` cannot refuse on those terms -- a refused completion leaves
    the task claimed and its result nowhere -- so it stores no basis and says so
    back. Either way the message is the whole recovery, so it names the three and
    what each means.
    """

    def __init__(self, raw: object) -> None:
        super().__init__(
            f"basis must be 'run', 'read' or 'relayed', and {str(raw)!r} is none of "
            f"them. 'run': you executed something and are reporting its output. "
            f"'read': you established it by reading and executed nothing. 'relayed': "
            f"you are passing on what another agent or a record said. Or omit basis, "
            f"and the text is shown as basis unstated."
        )


def _stated_basis(args: dict[str, Any]) -> Basis | None:
    """
    The basis a caller gave, None when it gave none, and ``InvalidBasis`` otherwise.

    The reading itself is ``Basis.stated``, which the approval row shares; this
    turns its ``ValueError`` into the sentence an agent is refused with.
    """
    raw = args.get("basis")
    try:
        return Basis.stated(raw)
    except ValueError:
        raise InvalidBasis(raw) from None


def _property_refusal(text: str) -> str | None:
    """
    Why a stated property cannot go on the board, or None when it can.

    Refused, not cut. The board shows a property whole on every row and every
    read, which is only affordable because it is one sentence; a longer one cut to
    fit would be a constraint missing its last clause, shown as though complete.
    The message says the limit and where the rest belongs.
    """
    over = property_overrun(text)
    if not over:
        return None
    return (
        f"property is {len(text)} characters and the limit is {MAX_PROPERTY_CHARS}. "
        f"It is the ONE sentence the work may not break, shown whole on every board "
        f"read and never cut, so it has to stay a sentence. Shorten it by {over} "
        f"character(s) and put the rest in detail."
    )


_OVER_ADVICE = "Split it across more than one call, or put it in a file and name the path."


def _length_refusal(what: str, text: str, limit: int, advice: str = _OVER_ADVICE) -> str | None:
    """
    Why ``text`` is too long to be accepted as ``what``, or None when it is not.

    Refused and not cut, for the tools that hold nothing while they refuse: the
    author still has the text and sends less of it. The sentence states the
    limit and the two ways to stay under it, because a refusal that says only
    "too long" is answered with the same text one word shorter.
    """
    if len(text) <= limit:
        return None
    return f"{what} is {len(text)} characters and the limit is {limit}. {advice}"


def _listed_refusal(what: str, values: Sequence[str], each: int) -> str | None:
    """Why a list of ids or paths is not accepted, or None when it is."""
    if len(values) > MAX_LISTED:
        return (
            f"{what} has {len(values)} entries and the limit is {MAX_LISTED}. Split the "
            f"work into more than one task."
        )
    longest = max(values, key=len, default="")
    if len(longest) > each:
        return (
            f"an entry of {what} is {len(longest)} characters and the limit is {each}. "
            f"Each entry is one name, not a description."
        )
    return None


def _kept(what: str, text: str) -> tuple[str, str]:
    """
    ``text`` as a completion stores it, and a sentence for the caller when that is
    not all of it.

    **Cut, where every other tool refuses.** A completion is never turned away
    for what it carries: refused, the task stays claimed and the account of the
    work is nowhere, which is worse than an account missing its end. So what fits
    is stored, and the stored text itself ends by saying it was cut -- the board
    shows a result to readers who never see this reply, and one that stopped
    mid-sentence with nothing after it would read as the whole of what its author
    had to say. That closing line is the one place a line of the board's stands
    behind an author's quote mark.
    """
    if len(text) <= MAX_TEXT_CHARS:
        return text, ""
    room = MAX_TEXT_CHARS - _CUT_NOTE_ROOM
    dropped = len(text) - room
    note = (
        f"\n[the board stored the first {room} characters of this {what}; "
        f"{dropped} more were sent and are not here]"
    )
    return text[:room] + note[:_CUT_NOTE_ROOM], (
        f"Your {what} was {len(text)} characters and the board stores at most "
        f"{MAX_TEXT_CHARS}: the first {room} were kept, with a line saying so, and "
        f"{dropped} were NOT stored. Put the rest in a file and name its path in a "
        f"concern that names this task."
    )


def _stated_not_run(args: dict[str, Any], has_result: bool) -> tuple[str | None, str]:
    """
    What a completion says it did not execute, in ``model.Task.not_run``'s three
    values, and a sentence for the caller when something it gave was not kept.

    Two arguments, because the two statements must not share a spelling. ``not_run``
    is text, and an empty one is "did not say" for the reason an empty ``basis``
    is: a model fills an optional string with ``""`` without meaning anything by
    it. If that were also how a claimer said "nothing was left unrun", every
    careless completion would read as a verified one. So that statement has an
    argument of its own, ``all_run``, which nothing sets by accident.

    **Every doubt resolves toward the weaker claim, and none of them refuses.** A
    refused completion strands a claim and everything waiting on it
    (``approval._BUS_AUTO``), and here there is always an honest thing to store
    instead:

    - both given: the list stands and ``all_run`` is dropped, since a claimer that
      named something unrun has not run everything;
    - ``all_run`` with no result: dropped to unsaid, since "everything my result
      names" names nothing;
    - ``all_run`` that is neither true nor false: unsaid.

    Each is said back, so the caller knows what the board is showing.
    """
    listed = str(args.get("not_run") or "").strip()
    all_run = stated_flag(args.get("all_run"))
    if listed:
        unkept = (
            "all_run was not kept: you also listed what was not run, and the list is "
            "what the board shows."
            if all_run is not False
            else ""
        )
        return listed, unkept
    if all_run is None:
        return None, (
            f"all_run was not kept: {str(args.get('all_run'))!r} is neither true nor "
            f"false, so the board shows this task as 'not run: unstated'."
        )
    if all_run and not has_result:
        return None, (
            "all_run was not kept: it says everything your result names was run, and "
            "you gave no result."
        )
    return ("" if all_run else None), ""


def qualified(tool_name: str) -> str:
    """The name a tool is announced to the model under, and seen by the gate as."""
    return f"mcp__{SERVER_NAME}__{tool_name}"


class UnstampedCall(Exception):
    """
    A bus tool ran without the gate having stamped a sender.

    Raised rather than defaulted. The gate is the only participant that knows who
    called, so a call that reaches a handler unstamped bypassed it -- and the two
    ways that can happen (a hook that failed open, a tool added to the server but
    not to the stamp list) are both bugs that must not degrade into a message
    attributed to a guess.
    """


def _sender(args: dict[str, Any]) -> NodeId:
    raw = args.get(FROM_KEY)
    # Arrives as a list: the stamp survives a JSON round trip to the CLI and back,
    # and JSON has no tuples.
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise UnstampedCall(f"bus call carried no authenticated sender: {args!r}")
    session_id, agent_id = raw
    return (str(session_id), None if agent_id is None else str(agent_id))


def _text(body: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": body}]}


def _park(bridge: Bridge, request_id: str) -> asyncio.Future[Effect]:
    """
    Register the future a board write waits on, before the intent is emitted.
    ``read_task`` parks here too, for the same reason a write does.

    The abandon answer is a *refusal*, not a success. Every other fallback in this
    module can afford to be the ordinary negative -- ``claim_task`` abandons as "the
    board had nothing", which is true often enough to be harmless. A write has no
    such answer: the only outcome that is certainly wrong at shutdown is the one
    saying it landed. Nor has a read of one named task: its ordinary negative is
    "that task is not on your board", which at shutdown is a claim about a board
    nobody consulted.
    """
    return bridge.ask(
        request_id, TaskWriteSettled(request_id=request_id, refusal=TaskRefusal.NOT_APPLIED)
    )


async def _outcome(future: asyncio.Future[Effect]) -> TaskWriteSettled:
    """
    Wait for the store's answer to a board write. A None ``refusal`` means it took
    effect.

    An effect of the wrong shape is reported as ``NOT_APPLIED`` rather than as
    success. It cannot happen -- futures are settled by request id -- but the two
    ways to be wrong here are not symmetric: a spurious refusal makes an agent
    re-check the board, and a spurious success makes it build on a task that is not
    there.

    Returns the settled effect rather than the refusal alone, because a declaration
    has two things to tell its caller and only one of them is whether it landed.
    Pulling the refusal out here would mean a second waiter for the second answer.
    """
    answer = await future
    if not isinstance(answer, TaskWriteSettled):
        return TaskWriteSettled(request_id="", refusal=TaskRefusal.NOT_APPLIED)
    return answer


def _premises_text(session: AgentSession) -> str:
    """
    What a worker is told about the session's premises when it takes work.

    **The claim is the re-read trigger, and it is a mechanism rather than a habit.**
    A brief nobody re-reads is a frozen brief with extra steps, and the three
    candidates its record lists are all things somebody has to remember: prose in
    the worker prompt, which this project records as the weakest lever it has; the
    lead posting a concern, when the lead is precisely the participant nothing
    checks; and the gate's rejection channel, which fires on tool calls rather than
    on work. A claim fires every time, needs nobody, and a worker that wants work
    cannot skip it.

    It is also already the moment a worker's "what am I building" is set -- the
    reply above carries the specification, and the premises are the context that
    specification sits inside.

    **The count is the point, not the reminder.** "Go and read it" on every claim is
    a ritual a worker performs or stops performing; "5 entries" is a fact it can
    compare against the three it saw last time. The store cannot supply it -- the
    brief is on disk and ``_apply`` is pure -- so it is read here, on the asyncio
    thread, which is where ``brief.py`` says reading belongs.

    **This does not reach a worker mid-task**, and that is the case the
    operator-instruction record is about: a builder redirected while holding a claim
    hears about it at its *next* claim, by which time it has built the superseded
    version. Closing that needs the gate's channel, which is a separate decision.
    """
    directory = getattr(session, "brief", None)
    if not directory:
        return ""
    count = brief.count_entries(Path(directory))
    if not count:
        return ""
    entries = "1 entry" if count == 1 else f"{count} entries"
    return (
        f"\n\nThe premises for this session are in {directory} ({entries}). Re-read them"
        " before you start. Entries are appended and never edited, so a later one may"
        " overturn an earlier one -- if the count has grown since you last looked, what"
        " you are working from may no longer be current."
    )


def _board_line(
    row: BoardTask,
    reasons: Mapping[ConcernId, BoardConcern] | None = None,
    budget: int | None = None,
    root: bool = False,
) -> str:
    """
    One board row, as the asking agent reads it. ``root`` says that agent is the
    session's lead, which changes one phrase: what a row whose holder is gone
    says can be done about it (``_stranded``).

    Says who holds a task rather than only its state, because "claimed" without an
    owner is the answer that sends a worker to ask the lead what it already could
    have known. The owner is the address the bus routes to, so a reader can act on
    it -- ``post_concern(to="builder-2")`` -- rather than only recognise it.

    A dependency this board does not have is called out separately from one that
    is merely unfinished. They look identical on the record and they are different
    problems: the first will never clear on its own, and nothing else in the
    session would ever say so. It is itself two things with two remedies, and
    the row knows which (``BoardTask.held_elsewhere``): an id nothing in the
    window has is a typo in somebody's ``depends_on`` or a task not declared
    yet (``_UNDECLARED``), and an id another session's task has can never be
    declared here at all (``board.foreign_wait_label``).

    **Carries ``detail``, which is the point of the tool rather than a detail of
    it.** ``Task.detail`` is the closest thing to an authoritative specification
    this system has, and ``claim_task`` hands it over once, to the agent that wins
    the task. A lead reads back what it wrote here, and so does the worker holding
    the task, which cannot claim it a second time: ``_pick_claim`` requires
    ``is_claimable``, which requires PENDING, so the current owner is told
    "Nothing claimable right now".

    An amended specification depends on that. What a worker is asked to do about
    one is re-read the board instead of trusting its transcript copy, and a row
    without the text would make that a discipline it cannot practise
    (`what-the-board-does-not-carry.md`).

    ``touches`` rides along because a worker that cannot see which files a task
    claims cannot honour the boundary the auto-dependency exists to enforce, and an
    open concern is named because a row stalled for a recorded reason is a different
    row from one stalled silently.

    **``budget`` is this row's whole allowance for prose**, shared by the
    specification, the result, the claimer's list of what it did not run and the
    note bodies, or None for the per-row bounds alone. It
    exists because the sum of bounded rows is not itself bounded (see
    ``_MAX_BOARD_CHARS``). Nothing above the prose is charged against it: the
    identifier, state, owner, blockers, files, the property the work may not break,
    and every note's sender and subject are what a reader needs in order to claim
    correctly and to know who to ask, and a board that has lost those has lost the
    thing it is for.

    **The property is the one sentence on a row that is never cut.** It is bounded
    where it is written (``model.MAX_PROPERTY_CHARS``), so it can be shown whole
    here, directly under the title.

    **A finished row always says where it stands on what was not run**, in the
    result's heading or in a heading of its own, and that survives a cut that
    takes every word under it. An unstated row reading as a verified one is the
    default this refuses.

    **A row the operator closed says that on its headline and has no claimer's
    standing at all** (``board.closed_label``): it is finished and nothing was
    built, so a basis and a "not run" beside it would describe work nobody
    reported. The operator's reason is laid out where a result would be, behind
    the operator's mark and not an agent's (``_ANSWER``, ``_closing_reason``).

    The specification is served before the notes when the two compete. A worker
    reads this row to decide whether to claim it and then to build from it, and a
    note is a finding *about* work whose description it presumes.

    **A result is served before either, and laid out after the specification.**
    Only a completed row has one, and a completed row is read for what happened
    rather than for what was asked: the work the specification describes is over,
    and the reader is the worker building on it. Serving the specification first
    would spend a crowded board's whole share on text whose job is done: a
    finished row's specification alone can be longer than its share. The layout
    stays in the order the two were written, so a row reads as asked-then-answered
    whichever of them was cut.

    **A result and a list of what was not run are the two paragraphs here no
    operator saw first, under any policy.** Both ride ``complete_task``, which the
    gate never parks (``approval._BUS_AUTO``). A specification and a note are parked
    at ``declare_task`` and ``post_concern`` wherever those are reviewed, which is
    not every rung. Each is the account of the agent its heading names, and a
    reader should weigh it as that.

    **No line an agent wrote has the form of a line this function writes, and no
    field an agent wrote can pass for the words beside it.** Every line of a body
    is laid out behind ``_QUOTE``, which nothing here starts a line of its own
    with: a body indented under its heading and otherwise emitted as written lets
    its second line be a row headline, a result heading with another agent's name
    in it, or a note attributed to the lead. Every one-line field has its line
    breaks taken out and is delimited (``_delimited_task``), because a title laid
    out bare can end in ``· held by builder-2 · specification says: run`` and an
    id can carry a state of its own ahead of the real one.

    What a cut leaves behind names ``read_task``, which returns the row whole.
    """
    row = _delimited_task(row)
    parts = [f"- {row.id} [{row.state.value}] {row.title}"]
    if row.owner is not None:
        parts.append(f"held by {row.owner}")
    if row.owner_gone:
        parts.append(f"but {_stranded(row, root)}")
    if row.blocked_on:
        parts.append(f"waiting on {', '.join(row.blocked_on)}")
    if _undeclared(row):
        parts.append(f"{_UNDECLARED}: {', '.join(_undeclared(row))}")
    if row.held_elsewhere:
        parts.append(foreign_wait_label(row))
    if row.touches:
        parts.append(f"writes {', '.join(row.touches)}")
    if row.closed_by_operator:
        # On the headline, beside the state it qualifies: "completed" alone
        # reads as work done, and this outlives a cut that takes the reason.
        parts.append(closed_label(row))
    # Which specification this is and whether its holder has seen it, on the
    # headline for the reason the basis is: both outlive a cut that takes the
    # text, and a replaced specification shown without its version reads as the
    # one that was always there.
    parts += [fact for fact in (version_label(row), stale_label(row)) if fact]
    if row.detail:
        # On the headline rather than on the paragraph it qualifies, so it is
        # structure: a crowded board that cuts the specification to nothing still
        # says how its author stood behind it, and a standing that vanished with
        # the text would leave what remains reading as the settled kind.
        parts.append(f"specification {basis_label(row.basis)}")
    if row.state is TaskState.COMPLETED and not row.result and row.not_run is None:
        # A finished row with no account at all still says where it stands. The
        # result heading carries this for a row that has one.
        parts.append(not_run_label(None))
    line = " · ".join(parts)
    if row.property:
        # Directly under the title and charged to no budget: it is one bounded
        # sentence (``model.MAX_PROPERTY_CHARS``), and it is what a reader ranks
        # everything below it against.
        line += f"{_DETAIL_INDENT}{property_label(row.property)}"
    spare = None if budget is None else max(0, budget)

    # Measured ahead of the result and the specification and emitted after both:
    # what a claimer wrote and did not run is the part of a finished row a reader
    # most needs and is least likely to go looking for.
    unrun, unrun_dropped = _quoted(row.not_run or "", _MAX_NOT_RUN_CHARS, _DETAIL_LEAD, spare)
    if spare is not None:
        spare -= _cost(unrun)

    # Likewise ahead of the specification: the budget goes to the result before
    # it, and the row still reads in the order it was written.
    account = _account(row)
    result, result_dropped = _quoted(
        account, _MAX_RESULT_CHARS, _DETAIL_LEAD, spare, _account_mark(row)
    )
    if spare is not None:
        spare -= _cost(result)

    if row.detail:
        # Under its row rather than joined with the separator: a specification is
        # a paragraph, and run into a one-line summary it makes the summary
        # unreadable.
        body, dropped = _quoted(row.detail, _MAX_DETAIL_CHARS, _DETAIL_LEAD, spare)
        line += _laid(body)
        if dropped:
            line += f"\n    {_rest(dropped, row.id)}"
        if spare is not None:
            spare -= _cost(body)
    if account:
        # The heading is structure, like a note's sender: it is what says a result
        # exists when the board could not afford a word of it.
        line += f"\n    -- {_result_heading(row)}"
        line += _laid(result)
        if result_dropped:
            line += f"\n    {_rest(result_dropped, row.id)}"
    if row.not_run:
        # A heading of its own, so a board that could not afford a word of the
        # list still says there is one.
        line += f"\n    -- {_not_run_heading(row)}"
        line += _laid(unrun)
        if unrun_dropped:
            line += f"\n    {_rest(unrun_dropped, row.id)}"
    line += _reasons_text(row, reasons or {}, spare)
    return line


def _stranded(row: BoardTask, root: bool) -> str:
    """
    What a row whose holder is gone says about it, after the holder's name.

    **Only the session's lead is told a release will work, and only where it
    will.** The store takes a release that is not the holder's from the root of
    the task's session when the holder is an agent of an earlier run of it
    (``store._may_reclaim``), and from nobody else. Told to a worker, or about a
    holder that merely finished its turn, the sentence sends an agent to make a
    call that is refused. Which holder it is comes from the row
    (``BoardTask.holder_left_behind``) and never from the holder's name, which
    an agent's address can be made to resemble. ``row`` is as
    ``_delimited_task`` leaves it.

    **Each of the other two readers is told who can move it**, since "stranded"
    alone leaves an agent with a row it can do nothing about and no name to take
    it to: a worker is sent to the lead for the release only the lead can make,
    and every reader of a row no agent's call can move is told the operator can
    close it (``_OPERATOR_CLOSES``) and what that leaves (``_CLOSED_IS_NOT_DONE``).
    """
    if root and row.holder_left_behind:
        return (
            f"its holder is not running and will not come back to it -- "
            f"release_task(task_id={_argument(row.id)}) puts it back for another agent "
            f"to claim, which only you, this session's lead, can do"
        )
    if row.holder_left_behind:
        return (
            "its holder is not running and will not come back to it -- only this "
            "session's lead can release it, so say so in a concern to lead that "
            "names this task"
        )
    return (
        f"its owner has stopped -- this task is stranded, and {_OPERATOR_CLOSES}, "
        f"{_CLOSED_IS_NOT_DONE}"
    )


# What an agent is told wherever a task cannot be moved by any call an agent can
# make: a claim whose holder has stopped and is nobody's to release, a task only
# its amender would work, a dependency on an id this board can never have. One
# spelling, so the remedy reads the same wherever an agent meets it.
#
# True of every unfinished task, whoever holds it: the pane offers the close on
# each row that is not finished and asks the operator for a reason
# (``ui.board_pane.closable``).
_OPERATOR_CLOSES = "the operator can close it from the board pane"

# What follows ``_OPERATOR_CLOSES`` wherever the task named is one whose work may
# still be wanted. A close is not a release: the row reads completed, reports no
# work, keeps its id, and lets go of every task that waited on it
# (``intents.TaskClosed``). An agent told only that the operator can close a
# stranded task would wait for it to come back.
_CLOSED_IS_NOT_DONE = (
    "which ends it unfinished -- it is not put back, and work still wanted is "
    "declared again under a new id"
)

# What an agent is told when it tries to finish or give back a task the operator
# closed (``TaskRefusal.CLOSED_BY_OPERATOR``), and under the operator's notice in
# the inbox of the agent that held it (``model.closing_notice``). One text, so
# the two places an agent learns of a close cannot describe it differently.
#
# It does not say the agent was stopped, because nothing stops it: the close
# takes the row away and lets go of what waited on it, the files it named
# included, while its holder may still be writing them.
_CLOSED_UNDER_YOU = (
    "The operator closed this task. It is finished as closed, not as done, and it "
    "is nobody's: complete_task and release_task on it are refused, and nothing "
    "sent with either is recorded -- a result included. What waited on it has been "
    "released, the files it named with it, so another agent may be writing them "
    "now: stop work on this task. If what you did, or left half-done in those "
    "files, matters to whoever is next, post it to the lead as a concern. read_task "
    "with this id shows the operator's reason."
)

# A dependency on an id nothing in this window has, on a row. Capitals because
# it is a blocker that never clears by waiting. An id another session's task has
# is not said this way: a reader told "never declared" goes on to declare it
# and is refused (``board.foreign_wait_label`` is what such a row says).
_UNDECLARED = "NEVER DECLARED ON THIS BOARD"

# What a dependency on such an id needs, said where one task is read whole.
# Definite, because the row is: no session's task has any id it is said of.
_UNDECLARED_ACCOUNT = (
    "No task of any session in this window has an id above. This task waits on "
    "each of them until a task with that id is declared on this board and "
    "finished."
)

# What an agent is told about an id its board does not list, by every call that
# looks one up. One account for the two things it can be, because none of those
# calls can tell them apart: nothing has the id, or a task of another session in
# this window does -- which is on no board this agent can read and holds the id
# against a declaration here.
_NOT_ON_BOARD = (
    "read_board lists every id that is. A task another session in this window "
    "declared is not on your board and cannot be read, claimed or changed from "
    "here, and its id cannot be declared here either."
)


def _argument(task_id: TaskId) -> str:
    """A task id, as ``_named`` leaves it, written as a call's argument."""
    return task_id if task_id.startswith('"') else f'"{task_id}"'


def _undeclared(row: BoardTask) -> tuple[TaskId, ...]:
    """
    The dependencies of ``row`` that nothing in the window has an id for:
    ``missing`` without the ones another session's task holds.
    """
    return tuple(d for d in row.missing if d not in row.held_elsewhere)


def _closing_reason(result: str) -> str:
    """
    The operator's reason for closing a task, as typed, out of the result the
    store composed around it -- or of that result's first line, which is what a
    digest carries. Empty when the operator gave none.

    The inverse of ``model.closing_result`` and written against it rather than
    against its words. **Only for a result known to be a close's**
    (``BoardTask.closed_by_operator``, ``ChangeKind.CLOSED``): a claimer can
    open a result of its own with the same words, and this would then hand part
    of an agent's text to the operator's mark.
    """
    # What is left after the label is nothing, or ": " and the reason, or -- on
    # a first line that was trimmed, of a reason that began with a line break --
    # the colon alone.
    return result[len(closing_result("")) :].removeprefix(":").removeprefix(" ")


def _account(row: BoardTask) -> str:
    """
    The text a finished row lays out where a result goes: the claimer's result,
    or the operator's reason on a row the operator closed.
    """
    return _closing_reason(row.result) if row.closed_by_operator else row.result


def _account_mark(row: BoardTask) -> str:
    """Whose mark ``_account``'s lines stand behind: the operator's, or an agent's."""
    return _ANSWER if row.closed_by_operator else _QUOTE


def _legend(closed: bool) -> str:
    """
    The legend of a reply that quotes agents, with the operator's mark explained
    beside it when the reply shows a reason the operator typed.
    """
    return f"{_QUOTE_LEGEND} {_CLOSING_LEGEND}" if closed else _QUOTE_LEGEND


def _agent_notes(concerns: Sequence[BoardConcern]) -> dict[ConcernId, BoardConcern]:
    """
    The concerns a board read lays out as notes on its rows, by id: every one
    but the operator's notice to the holder of a task it closed
    (``model.closing_notice``).

    That one is not a note an agent made, and a row lays each note out as an
    agent's, behind the mark that says an agent is being quoted. Nothing is
    lost with it: its body is the reason, which the closed row it names shows
    behind the operator's mark, and its recipient reads it in its inbox.
    """
    return {c.id: c for c in concerns if not is_closing_notice(c.id)}


def _operators_notice(notice: Concern) -> list[str]:
    """
    The operator's notice that a task was closed under its holder
    (``model.closing_notice``), as the lines an inbox shows it on.

    Not laid out as an agent's concern, because no agent wrote any of it: the
    heading and what the close means are this tool's words, and the body is the
    operator's reason, which stands behind the operator's mark and not behind
    the one that says an agent is being quoted.
    """
    about = f" {_named(notice.task_id)}" if notice.task_id else ""
    lines = [
        f"[from the operator, not from an agent] ABOUT TASK{about}, WHICH YOU HELD. "
        f"{_CLOSED_UNDER_YOU}"
    ]
    if not notice.body.strip():
        return [*lines, "The operator gave no reason."]
    return [
        *lines,
        "The operator's reason:",
        *_quoted(notice.body, len(notice.body), "", mark=_ANSWER)[0],
    ]


def _result_heading(row: BoardTask) -> str:
    """
    What a result is introduced as: whose account it is.

    On a row the operator closed there is no claimer and no account of work, so
    the heading introduces the one thing under it, the operator's reason. That
    the row was closed, and what that means, is said above it wherever this is
    laid out (``board.closed_label``).

    Attributed because the reader has to weigh it, and "held by" earlier in the row
    says who holds the task rather than who is speaking in this paragraph. The
    claimer's basis rides in the heading for the reason the specification's rides
    on the headline: it outlives a cut that takes the whole body.

    Where the claimer stands on what it did not run rides here too, when that is
    one of the two short answers -- it did not say, or it says nothing was left.
    A list of what was not run has ``_not_run_heading`` instead.
    """
    if row.closed_by_operator:
        return "the operator's reason for closing it:"
    said = basis_label(row.result_basis)
    if not row.not_run:
        said += f"; {not_run_label(row.not_run)}"
    return f"result, as {row.owner} reported it ({said}):" if row.owner else f"result ({said}):"


def _not_run_heading(row: BoardTask) -> str:
    """
    What a claimer's list of what it did not run is introduced as.

    Attributed like the result it sits beside, and for the same reason: it is the
    claimer's account, unreviewed, and may be shorter than the truth.
    """
    return f"not run, as {row.owner} states it:" if row.owner else "not run:"


def _rest(dropped: int, task_id: TaskId, part: str = "") -> str:
    """
    What stands in for text a read cut, and where the whole of it is.

    Names the call rather than a participant. "Ask the lead for the rest" made the
    lead a relay for text the board already held, and the lead's copy was its own
    transcript -- the thing a board exists to not depend on.

    ``part`` is for the one read that cuts with no wider read behind it:
    ``read_task`` on a task too large for a reply names the argument that
    returns the one text (``_part_text``). A board read and a digest name the
    task alone, and ``read_task`` is then where a text still too long to ride
    with the others says which part it is.
    """
    which = f', part="{part}"' if part else ""
    return (
        f"... {dropped} more character(s) -- "
        f"read_task(task_id={_argument(task_id)}{which}) returns it whole"
    )


def _reasons_text(
    row: BoardTask, by_id: Mapping[ConcernId, BoardConcern], budget: int | None = None
) -> str:
    """
    What other agents have recorded about this task, in their own words.

    **The count is not enough, which is the whole of this.** A row saying "1 open
    concern" tells a reader that a conclusion exists and not what it was, so the
    reader has to go and derive it again -- which is the relitigating the board is
    meant to end. The subject alone has the same defect one level up: "checksum
    ignored in sixty places" names a finding without giving the reader anything to
    evaluate.

    **Only concerns that named a task are broadcast.** A concern with no
    ``task_id`` is a message between two agents -- a question to the lead, an
    answer, a heads-up -- and putting it on everyone's board would turn a
    point-to-point channel into a public one. ``Concern.task_id`` is what makes
    that line drawable: a finding *about shared work* is shared state, and the
    rest is mail.

    What is rendered is the text as *delivered* -- the operator's edit included,
    which is the version the recipient acted on -- and a note the operator rewrote
    says so beside its sender's basis, since that basis was given for the words the
    sender wrote. Whether an operator saw a body first depends on the policy:
    ``post_concern`` is parked wherever it is reviewed and is not under the rung
    that releases it (``approval._AUTONOMOUS_AUTO``). So a body is quoted line by
    line like every other text an agent wrote (``_quoted``).

    **``budget`` bounds the bodies and never the attributions.** Under a whole-board
    ceiling the bodies are the first thing a long board can afford to lose, and the
    sender and subject are the last: they are what tells a reader that a finding
    exists and who to ask for it, which is the count-versus-subject distinction
    above taken one step further rather than abandoned. A note whose body did not
    fit still appears, and still says how much of it is missing.
    """
    shown = [_delimited_note(by_id[cid]) for cid in row.concerns if cid in by_id]
    if not shown:
        return ""
    spare = budget
    out = [f"\n    -- {len(shown)} agent note(s) on this task:"]
    for concern in shown:
        body, dropped = _quoted(concern.body, _MAX_CONCERN_CHARS, _NOTE_LEAD, spare)
        said = f"{basis_label(concern.basis)}{_edited_note(concern.edited)}{_unread_note(concern)}"
        out.append(
            f"\n       [{concern.sender}, {said}] "
            f"{incident_marker(concern.incident)}{concern.subject or '(no subject)'}"
        )
        out.append(_laid(body))
        if dropped:
            out.append(f"\n       {_rest(dropped, row.id)}")
        if spare is not None:
            spare -= _cost(body)
    return "".join(out)


def _edited_note(edited: bool) -> str:
    """
    What follows a sender's basis when the operator rewrote the body under it.

    Beside the basis on every read that shows one, because the basis is the
    sender's statement about the text it wrote and the two are only readable
    together (``model.Concern.basis``).
    """
    return ", edited by the operator before delivery" if edited else ""


def _unread_note(note: BoardConcern) -> str:
    """
    What follows a note's attribution when it is waiting for an agent that is not
    running (``BoardConcern.undeliverable``): nothing in this process reads that
    inbox, so a reader that takes the note for delivered, or for about to be, is
    wrong about who knows what it says.
    """
    return f", {UNDELIVERABLE}" if note.undeliverable else ""


# What starts every line of a text an agent wrote, on every reply. Nothing in this
# module starts a line of its own with it, which is the whole of its value: a row
# headline, a heading, an attribution, a digest slot and a cut notice are all told
# from an agent's words by form, so an agent's words may not take any of those forms.
_QUOTE = "| "

# Said once in each reply that quotes anything, ahead of the first quoted line.
# Both halves describe a form and not a column: a body is indented under the row,
# note or item it belongs to, so its lines begin with that indent and then the mark.
_QUOTE_LEGEND = (
    "A line whose first characters after any indent are '| ' is text an agent "
    "wrote, quoted as it wrote it, and is never this tool speaking. On any other "
    "line, whatever stands between double quotes is a title, subject, property or "
    "name an agent wrote, with a backslash put before each double quote and "
    "backslash inside it, and is not this tool speaking either."
)

# What a quoted line is laid out on under a row and under a note. Named because
# they are charged against a row's budget rather than only written (``_quoted``).
_DETAIL_LEAD = "    "
_NOTE_LEAD = "       "

# The indent a row's own one-line property is laid out on.
_DETAIL_INDENT = "\n    "


def _one_line(text: str) -> str:
    """
    ``text`` with every line break replaced by a space, and otherwise untouched.

    For the fields a reply lays out on a line of its own making -- an id, a title,
    a subject, a path, a role name. None of them is checked for line breaks where
    it is written, and one that carries a break ends the board's line and starts
    another in whatever form its author chose.
    """
    return " ".join(text.splitlines())


def _said(text: str) -> str:
    """
    A sentence an agent wrote -- a title, a subject, a property -- as it stands on
    a line this module writes: one line, between double quotes, with a quote mark
    and a backslash inside it escaped.

    A line break is not the only way onto a line of the tool's. A field laid out
    bare beside the tool's own words can finish that line for it: a title ending
    ``· held by builder-2 · specification says: run`` reads as two segments of the
    headline it sits on, and a subject opening ``INCIDENT`` reads as the marker
    ahead of it. Between quotes the same text is a title that contains those
    words. The escape is what keeps the closing quote the tool's: an unescaped
    quote mark on such a line always opens or closes a field, so a field cannot
    end itself early and go on outside.
    """
    flat = _one_line(text).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{flat}"'


# What a name may be made of and still be laid out bare: nothing the tool uses to
# build a line -- no space, bracket, colon, comma, quote mark or middle dot.
_BARE_NAME = re.compile(r"[A-Za-z0-9_./*@+-]+")


def _named(text: str) -> str:
    """
    A name an agent wrote -- a task id, a path -- as it stands on a line this
    module writes: bare when it is made only of ``_BARE_NAME``'s characters, and
    otherwise delimited like a sentence (``_said``).

    Bare where it can be, because a bare name is what a reader copies into the
    next call, and ``t1`` or ``pptmstr/bus.py`` holds nothing that could be taken
    for the tool's words. None of these is checked where it is written, so an id
    of ``t5 [completed] parser fixed ·`` is one the board has to be able to show.
    """
    flat = _one_line(text)
    return flat if _BARE_NAME.fullmatch(flat) else _said(flat)


def _delimited_task(row: BoardTask) -> BoardTask:
    """
    A row whose one-line fields can each be told from the line they are laid out
    on: ids and paths as ``_named`` leaves them, the title and the property as
    ``_said`` does. A property nobody stated stays empty, which is how every
    reader of the field tells there is none.

    The owner is only made one line. A role name is the address the bus routes
    to and the board's own words for a holder it cannot name (``board.UNKNOWN``,
    ``board.FOREIGN``); between quotes those would read as something an agent
    wrote.
    """

    def each(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_named(value) for value in values)

    return dataclasses.replace(
        row,
        id=_named(row.id),
        title=_said(row.title),
        owner=None if row.owner is None else _one_line(row.owner),
        blocked_on=each(row.blocked_on),
        missing=each(row.missing),
        held_elsewhere=each(row.held_elsewhere),
        touches=each(row.touches),
        depends_on=each(row.depends_on),
        property=_said(row.property) if row.property else "",
    )


def _delimited_note(note: BoardConcern) -> BoardConcern:
    """
    A note whose one-line fields can each be told from the line they are laid out
    on (``_delimited_task``). An absent subject stays empty for the same reason
    an absent property does.
    """
    return dataclasses.replace(
        note,
        sender=_one_line(note.sender),
        recipient=_one_line(note.recipient),
        subject=_said(note.subject) if note.subject else "",
        task_id=None if note.task_id is None else _named(note.task_id),
    )


def _quoted(
    text: str, cap: int, lead: str, spare: int | None = None, mark: str = _QUOTE
) -> tuple[list[str], int]:
    """
    A text an agent wrote, as the lines it is shown on, and how many of its
    characters were left out.

    One line out for each line in, each behind ``lead`` and ``mark``: ``_QUOTE``
    for everything an agent wrote, and ``_ANSWER`` for the one text on a board
    that the operator typed, the reason a task was closed. Line breaks
    are whatever ``str.splitlines`` takes for one, so a carriage return or a form
    feed cannot stand in for the newline this removes.

    ``cap`` bounds the author's characters and ``spare`` bounds what laying them
    out costs, quote, lead and newline included (``_cost``), or None for the cap
    alone. Both are emitted, so both are charged: measuring only the author's
    characters is what puts a whole-board ceiling over the ceiling it claims to
    keep, by one indent a line.

    Says what it dropped for the reason every other bound in this project does: a
    silent cap on a specification is how a worker builds confidently against half
    of one.
    """
    overhead = len(lead) + len(mark) + 1
    lines: list[str] = []
    used = cost = 0
    for raw in text[:cap].splitlines(keepends=True):
        content = "".join(raw.splitlines())
        if spare is not None:
            room = spare - cost - overhead
            if room < 0 or (room == 0 and content):
                break
            if len(content) > room:
                lines.append(f"{lead}{mark}{content[:room]}")
                used += room
                break
        lines.append(f"{lead}{mark}{content}")
        used += len(raw)
        cost += overhead + len(content)
    return lines, len(text) - used


def _cost(lines: Sequence[str]) -> int:
    """What ``lines`` cost a budget once laid out: each one and the newline before it."""
    return sum(len(line) + 1 for line in lines)


def _laid(lines: Sequence[str]) -> str:
    """``lines`` as they continue a row: each on a line of its own."""
    return "".join(f"\n{line}" for line in lines)


def _board_text(
    tasks: Sequence[BoardTask], by_id: Mapping[ConcernId, BoardConcern], root: bool = False
) -> str:
    """
    The whole board reply, bounded.

    **Rows are never dropped.** An agent reads this to find out what exists, what is
    held and what is blocked, and it has to know about the task it is *not* going to
    claim -- that is how it stays off another agent's files. A listing short of a row
    is wrong in a way a listing short of a paragraph is not, and it is wrong
    invisibly, which is the shape every other bound here avoids.

    So the degradation is in the prose. Each row is measured with no prose at all;
    what the ceiling leaves over that floor is divided equally between the rows, and
    each spends its share on its result if it has one, then its specification, then
    its note bodies.
    Equally rather than by need: a proportional split would spend the board's budget
    on whichever tasks were declared with the longest descriptions, and length is
    not importance.

    Under the ceiling nothing is shared out: a board that fits is rendered with the
    per-row bounds alone, which keeps every board small enough to fit out of the
    business of being bounded.

    **The structural floor wins if even it does not fit.** Then the reply is the
    floor plus the notice explaining it, over the ceiling, rather than rows dropped
    to get under it. A board of that many tasks has a problem this function cannot
    solve, and hiding rows from the agent that has to work around them is not a fix.

    So the bound this holds is ``max(_MAX_BOARD_CHARS, floor + notice)``, and it is
    the ceiling itself for every board whose structure fits inside it.
    """
    closed = any(row.closed_by_operator and _account(row) for row in tasks)
    preamble = f"{len(tasks)} task(s) on your board. {_legend(closed)}"
    full = "\n".join([preamble, *(_board_line(row, by_id, root=root) for row in tasks)])
    if len(full) <= _MAX_BOARD_CHARS:
        return full

    # Rendered a second time rather than estimated. The floor is what the rows cost
    # with every paragraph removed, and measuring it is the only way to divide what
    # is left without the arithmetic and the renderer disagreeing.
    floor = "\n".join([preamble, *(_board_line(row, by_id, budget=0, root=root) for row in tasks)])
    notice = (
        "\n\nThis board did not fit, so the specification, result and notes of each "
        "task below share {share} characters. Nothing is hidden: every task is listed "
        "and every note names its sender and subject. read_task(task_id) returns any "
        "one task: its specification whole, then its result and every note on it, and "
        "for a task too large for one reply, how to read each of those whole."
    )
    spare = _MAX_BOARD_CHARS - len(floor) - len(notice)
    share = max(0, spare // len(tasks))
    return "\n".join(
        [
            preamble + notice.format(share=share),
            *(_board_line(row, by_id, share, root) for row in tasks),
        ]
    )


# What ``read_task`` may add under a task: the sentence saying whether the holder
# is now recorded as shown the version above. Kept out of the task's own
# allowance so the two together are inside ``_MAX_BOARD_CHARS``, which is the
# only size a reply is recorded as a showing at.
_SHOWN_NOTE_ROOM = 500

# The names ``read_task``'s ``part`` takes for the two texts a task has one of.
# A note is ``note:<its id>`` and an earlier specification ``version:<number>``.
_PART_RESULT = "result"
_PART_NOT_RUN = "not_run"


def _task_text(
    row: BoardTask,
    by_id: Mapping[ConcernId, BoardConcern],
    root: bool = False,
    ceiling: int | None = None,
) -> str:
    """
    One task: its current specification whole, and everything else any agent
    wrote onto its row -- whole when the reply has room, and otherwise cut with
    the call that returns each text whole.

    The counterpart to ``_board_line`` and deliberately not a mode of it. A board
    read is bounded because it is every row at once; this is one row, asked for by
    name, and every bound ``_board_line`` applies ends by pointing here. Text on
    the board that no agent on the board can reach is the one property the pair
    exists to rule out, and it still is: what this cuts is behind ``part``
    (``_part_text``), one text to a reply.

    **``ceiling`` is what the reply may come to, or None for no bound.** It is
    there because this is also the read that records a holder as shown a
    replaced specification (``read_task``), and a reply is evidence of a
    showing only at a size an agent can read: a task on its third
    20,000-character specification, each earlier one whole, is over 60,000
    characters, past both replies ``_MAX_BOARD_CHARS`` was set under.

    **The current specification is never what gives way.** It is laid out first
    of the texts and whole at any ceiling, since it is what the holder is held
    to and what the record says it was shown. The result, the list of what was
    not run, the note bodies and the earlier specifications share equally what
    the ceiling leaves over everything else -- ``_board_text``'s rule and for
    its reason, that length is not importance -- and each one cut ends in
    ``_rest``'s line naming its part.

    **Structure is never dropped, so the ceiling can be exceeded.** The headline,
    the dependencies and files, each note's attribution and each earlier
    version's heading are laid out whatever they cost, and so is the
    specification. A caller that needs the bound kept compares the length it
    was given with the ceiling it asked for; ``read_task`` does, and records no
    showing when the reply is over.

    Under the ceiling nothing is shared out, which keeps every task small enough
    to fit out of the business of being bounded: the reply is every character.

    Built from the same ``BoardTask`` and ``BoardConcern`` rows as the board read,
    so it is scoped as that is scoped and names agents as that names them. The
    notes are the ones the row lists -- concerns that named this task and were not
    withdrawn -- and each says who it was to as well as who it was from, because a
    reader of one task is reconstructing a conversation rather than scanning for
    one.

    Says what is absent rather than leaving a gap. A reader that asked for a whole
    task and got no result section cannot tell "none was given" from "this tool
    does not show them".

    Each text is quoted line by line (``_quoted``): this is the read a worker
    builds from, so it is the one where a forged heading would cost most.
    """
    whole = _task_lines(row, by_id, root, None)
    if ceiling is None or _cost(whole) - 1 <= ceiling:
        return "\n".join(whole)
    # Rendered again rather than estimated, as ``_board_text`` does: the floor
    # is what the task costs with every text but the specification removed.
    floor = _cost(_task_lines(row, by_id, root, 0)) - 1
    pieces = sum(1 for _ in _cuttable(row, by_id))
    # The notice names the share, and was measured naming a share of one digit.
    spare = ceiling - floor - len(str(ceiling))
    share = max(0, spare // max(1, pieces))
    return "\n".join(_task_lines(row, by_id, root, share))


def _cuttable(row: BoardTask, by_id: Mapping[ConcernId, BoardConcern]) -> Iterator[str]:
    """
    Every text of a task that ``_task_text`` may cut, each once and none empty:
    all of them but the current specification.
    """
    yield from (text for text in (_account(row), row.not_run or "") if text)
    yield from (by_id[cid].body for cid in row.concerns if cid in by_id and by_id[cid].body)
    yield from (prior.detail for prior in row.superseded if prior.detail)


def _task_lines(
    row: BoardTask, by_id: Mapping[ConcernId, BoardConcern], root: bool, share: int | None
) -> list[str]:
    """
    ``_task_text``'s lines, with each cuttable text given ``share`` characters
    laid out, or None for all of it. The notice saying texts were cut is one of
    the lines whenever ``share`` is a number, so the floor measured at zero has
    already paid for it.
    """
    task_id = _named(row.id)
    row = _delimited_task(row)

    def piece(text: str, part: str, mark: str = _QUOTE) -> list[str]:
        lines, dropped = _quoted(text, len(text), "", share, mark)
        return [*lines, _rest(dropped, task_id, part)] if dropped else lines

    lines = [f"Task {row.id} [{row.state.value}] {row.title}"]
    stated = property_label(row.property)
    lines.append(stated[:1].upper() + stated[1:] if stated else "Must not break: (none was stated)")
    if row.closed_by_operator:
        # In place of a holder: the row has none, and "held by nobody" under
        # "completed" would leave a reader looking for who finished it.
        closed = closed_label(row)
        lines.append(closed[:1].upper() + closed[1:])
    elif row.owner is None:
        lines.append("held by nobody")
    elif row.owner_gone:
        lines.append(f"held by {row.owner}, but {_stranded(row, root)}")
    else:
        lines.append(f"held by {row.owner}")
    if row.depends_on:
        lines.append(f"depends on: {', '.join(row.depends_on)}")
    if row.blocked_on:
        lines.append(f"still waiting on: {', '.join(row.blocked_on)}")
    if _undeclared(row):
        lines += [f"{_UNDECLARED}: {', '.join(_undeclared(row))}", _UNDECLARED_ACCOUNT]
    if row.held_elsewhere:
        waits = foreign_wait_label(row)
        lines.append(f"{waits[:1].upper()}{waits[1:]}.")
    if row.touches:
        lines.append(f"writes: {', '.join(row.touches)}")
    for fact in (version_label(row), stale_label(row)):
        if fact:
            lines.append(fact[:1].upper() + fact[1:])
    account = _account(row)
    lines.append(_legend(row.closed_by_operator and bool(account)))
    if share is not None:
        lines.append(
            f"This task did not fit one reply. Its specification is whole below; its "
            f"result, list of what was not run, note bodies and earlier "
            f"specifications are each cut to {share} characters, and each one cut "
            f"ends in a line giving how much was left out and the read_task call "
            f"that returns it whole. Nothing is hidden: every note and every earlier "
            f"version is listed."
        )

    lines += ["", *_specification_lines(row)]

    if account:
        heading = _result_heading(row)
        lines += [
            "",
            heading[:1].upper() + heading[1:],
            *piece(account, _PART_RESULT, _account_mark(row)),
        ]
    elif row.closed_by_operator:
        lines += ["", "The operator's reason for closing it:", "(none was given)"]
    elif row.state is TaskState.COMPLETED and row.not_run is None:
        lines += ["", "Result:", f"(completed without one; {not_run_label(None)})"]
    elif row.state is TaskState.COMPLETED:
        lines += ["", "Result:", "(completed without one)"]
    else:
        lines += ["", "Result:", "(none yet -- the task is not complete)"]
    if row.not_run:
        heading = _not_run_heading(row)
        lines += ["", heading[:1].upper() + heading[1:], *piece(row.not_run, _PART_NOT_RUN)]

    notes = [(cid, _delimited_note(by_id[cid])) for cid in row.concerns if cid in by_id]
    lines += ["", f"{len(notes)} agent note(s) on this task" + (":" if notes else ".")]
    for cid, note in notes:
        lines += ["", _note_heading(note), *piece(note.body, f"note:{_one_line(cid)}")]

    if row.superseded:
        # Last, so a reader of the task as it stands is not made to read past what
        # it used to say: these are the texts a finished result may have been
        # written against.
        lines += ["", f"{len(row.superseded)} earlier specification(s), oldest first:"]
        for number, prior in enumerate(row.superseded, start=1):
            lines += [
                "",
                *_version_heading(number, prior),
                *(
                    piece(prior.detail, f"version:{number}")
                    if prior.detail
                    else ["(no specification text)"]
                ),
            ]
    return lines


def _note_heading(note: BoardConcern) -> str:
    """The line over a note's body where one task is read. ``note`` is delimited."""
    return (
        f"[from {note.sender} to {note.recipient}, "
        f"{basis_label(note.basis)}{_edited_note(note.edited)}{_unread_note(note)}] "
        f"{incident_marker(note.incident)}{note.subject or '(no subject)'}"
    )


def _version_heading(number: int, prior: BoardVersion) -> list[str]:
    """The two lines over an earlier specification: which it was, and its property."""
    held = property_label(_said(prior.property)) if prior.property else ""
    return [
        f"Version {number} ({basis_label(prior.basis)}), replaced by "
        f"{_one_line(prior.replaced_by)}:",
        held[:1].upper() + held[1:] if held else "Must not break: (none was stated)",
    ]


def _part_text(row: BoardTask, by_id: Mapping[ConcernId, BoardConcern], part: str) -> str:
    """
    One text of a task, whole and alone: what ``read_task`` returns for ``part``.

    Where the rest of a text ``_task_text`` cut is. Every text an agent can put
    on a task is accepted under ``MAX_TEXT_CHARS``, which is under the reply
    ceiling, so each fits a reply of its own; only the operator's hand can write
    a longer one, and it is returned whole all the same, since a bound here
    would be the unreachable text this exists to rule out.

    A part the task does not have is answered with the parts it does: a note's
    id and a version's number are things a caller copies from the cut line, and
    one mistyped should not cost a second guess.
    """
    task_id = _named(row.id)
    shown = _delimited_task(row)

    def alone(heading: str, text: str, mark: str = _QUOTE) -> str:
        body = _quoted(text, len(text), "", None, mark)[0] if text else ["(it is empty)"]
        legend = _CLOSING_LEGEND if mark == _ANSWER else _QUOTE_LEGEND
        return "\n".join([f"Task {task_id}: {heading}", legend, *body])

    notes = {cid: by_id[cid] for cid in row.concerns if cid in by_id}
    kind, _, which = part.partition(":")
    account = _account(row)
    if part == _PART_RESULT and account:
        return alone(_result_heading(shown), account, _account_mark(row))
    if part == _PART_NOT_RUN and row.not_run:
        return alone(_not_run_heading(shown), row.not_run)
    if kind == "note" and which in notes:
        return alone(_note_heading(_delimited_note(notes[which])), notes[which].body)
    if kind == "version" and which.isdecimal() and 1 <= int(which) <= len(row.superseded):
        prior = row.superseded[int(which) - 1]
        heading, held = _version_heading(int(which), prior)
        return alone(f"{heading[:-1]}. {held}:", prior.detail)
    has = [
        *([_PART_RESULT] if account else []),
        *([_PART_NOT_RUN] if row.not_run else []),
        *(f"note:{_one_line(cid)}" for cid in notes),
        *(f"version:{number}" for number in range(1, len(row.superseded) + 1)),
    ]
    listed = ", ".join(_named(name) for name in has) if has else "none"
    return (
        f"Task {task_id} has no part {_said(part[:MAX_ID_CHARS])}. The parts it has "
        f"are: {listed}. Leave part out for the task itself, which always carries "
        f"its current specification whole."
    )


def _specification_lines(row: BoardTask) -> list[str]:
    """
    A task's current specification, whole and quoted, under a heading that says
    which version it is once there has been more than one.

    ``row`` is as ``_delimited_task`` leaves it.
    """
    which = f", version {row.version}" if row.version > 1 else ""
    if not row.detail:
        return [f"Specification{which}:", "(none was given)"]
    return [
        f"Specification{which} ({basis_label(row.basis)}):",
        *_quoted(row.detail, len(row.detail), "")[0],
    ]


def _unshown(row: BoardTask, me: NodeId) -> bool:
    """
    Whether ``me`` holds ``row`` open against a specification it was never handed.

    A claim that recorded no version counts as handed the first: while a task has
    had one text there is only one it can have been.
    """
    return (
        row.state is TaskState.CLAIMED
        and row.holder == me
        and row.version > (row.claimed_version or 1)
    )


def _behind(row: BoardTask, me: NodeId) -> bool:
    """
    Whether ``me`` finished ``row`` against a specification older than the one
    the board holds now. ``_unshown`` for a row that is over.
    """
    return (
        row.state is TaskState.COMPLETED
        and row.holder == me
        and row.version > (row.claimed_version or 1)
    )


def _landed_behind_text(row: BoardTask, root: bool, reachable: bool) -> str:
    """
    What a holder is told when its result stands against a specification older
    than the one the board holds: which version its result is recorded against,
    which the board holds, the current text, and what to do if the work does not
    meet it.

    **It does not say when the text was replaced.** A finished task can be
    amended too, and the row read back here does not carry when the completion
    landed, so "while you held it" would be said of an amendment made after the
    holder had finished. "Since you were handed it" is true of both.

    ``reachable`` is whether the name of whoever replaced the text is an address
    a concern can be sent to, which is the session's to answer
    (``AgentSession.resolve_role``). The operator is not one, and neither is an
    amender of an earlier run; read off the name instead, either could be
    imitated by an agent's address.

    **The completion is not turned away for this.** A refusal here would drop the
    result the holder sent and leave the claim with an agent that may never call
    again; and a refusal that showed the new text and then counted the holder as
    having read it would record a reading nobody can vouch for, since a reply of
    this size is one an agent has been measured unable to read
    (``_MAX_BOARD_CHARS``). So the result is stored against the version the holder
    was handed, which is what it is an account of, and the board says so on the
    row (``board.stale_label``).

    The current text is bounded as a board row's is and names ``read_task`` for
    the rest: this reply is a notice, and the read is where the text is whole.
    """
    row = _delimited_task(row)
    had = (
        "a version the claim did not record"
        if row.claimed_version is None
        else f"version {row.claimed_version}"
    )
    by = _one_line(row.superseded[-1].replaced_by) if row.superseded else ""
    if by and reachable:
        whom = f"say so in a concern to {by} that names this task"
    elif root:
        whom = "say so to the operator -- raise_decision puts it in front of them"
    else:
        whom = "say so in a concern to lead that names this task"
    stated = property_label(row.property)
    body, dropped = _quoted(row.detail, _MAX_DETAIL_CHARS, "")
    return "\n".join(
        [
            f"Its specification has been replaced since you were handed it, and "
            f"nothing told you. "
            f"Your result is recorded as written against {had}, which is the one you "
            f"were handed, and the board holds version {row.version}"
            f"{f', replaced by {by}' if by else ''}. The board shows that beside your "
            f"result. Below is version {row.version}: if your work does not meet it, "
            f"{whom}. A finished task cannot be completed a second time.",
            _QUOTE_LEGEND,
            "",
            stated[:1].upper() + stated[1:] if stated else "Must not break: (none is stated)",
            (
                f"Specification, version {row.version} ({basis_label(row.basis)}):"
                if row.detail
                else f"Specification, version {row.version}: (none was given)"
            ),
            *body,
            *([_rest(dropped, row.id)] if dropped else []),
        ]
    )


def _amended_text(
    task_id: TaskId, row: BoardTask | None, at: float, basis_sent: bool = False, unkept: str = ""
) -> str:
    """
    What an amender is told once the board has taken its amendment: the version it
    made and which of the text and the property that version replaced, or that it
    made none, and who is still working to an earlier one.

    ``at`` is the stamp the amendment carried, which is how the version it made is
    found among the task's history: the reply to a write says only that it landed.
    Which half changed is read off that history too, and not off what was sent --
    a text sent back as it stands replaced nothing.

    ``basis_sent`` is whether the amender stated a basis, and ``unkept`` is the
    sentence for one the operator's edit discarded. Both are about a basis
    beside a *replaced* text, so neither is said of a text that stands except to
    say the basis went nowhere: the store keeps the author's
    (``store._apply``'s ``TaskAmended`` arm).
    """
    named = _named(task_id)
    if row is None:
        return (
            f"Task {named}'s specification was replaced, but the board could not be "
            f"read back to say which version that made. read_task shows it." + unkept
        )
    made = next((n for n, prior in enumerate(row.superseded) if prior.replaced_at == at), None)
    if made is None:
        return (
            f"Task {named} is unchanged: what you sent is the specification and the "
            f"property it already has, so no new version was made. It is at version "
            f"{row.version}."
        )
    before = row.superseded[made]
    after = row.superseded[made + 1] if made + 1 < len(row.superseded) else row
    reworded = after.detail != before.detail
    held_to = (
        ""
        if after.property == before.property
        else "withdrawn" if not after.property else "replaced"
    )
    if reworded and held_to:
        which = f"Its text was replaced and its property was {held_to}."
    elif reworded:
        which = "Its text was replaced and its property stands."
    else:
        which = (
            f"Its property was {held_to} and its text stands, under the basis its "
            f"author gave it."
        )
        if basis_sent:
            which += (
                " The basis you sent was not kept: a basis is a statement about a "
                "text, and this amendment replaced none."
            )
    said = [f"Task {named}'s specification is now version {made + 2}.", which]
    if reworded and unkept:
        said.append(unkept.strip())
    if row.version > made + 2:
        said.append(f"It has been replaced again since and is at version {row.version}.")
    stale = stale_label(row)
    if stale and row.state is TaskState.CLAIMED:
        said.append(
            f"It is held by {_one_line(row.owner or '')}: {stale}. Nothing tells a holder "
            f"that its specification changed. It sees the new text when it next calls "
            f"read_task on this task; if it completes first, its result lands recorded "
            f"against the version it was handed and it is told then. Post it a concern "
            f"if it should stop and read it now."
        )
    elif stale:
        said.append(f"It is already finished: {stale}.")
    return " ".join(said)


# What follows the heading of the two slots a lead may not summarise, when they
# hold anything. Said in the reply because that is where the lead is reading when
# it decides what to pass on; the headings themselves are shared with the pane,
# whose reader is the operator and is not being told what to do.
_IN_FULL = " Report every one of these to the operator."

# How a digest renderer is handed its allowance: text, the piece's own cap and
# the mark its lines stand behind in, the quoted lines that may be shown and how
# much was dropped out.
_Prose = Callable[[str, int, str], tuple[list[str], int]]

# What a quoted line is laid out on under a digest item.
_ITEM_LEAD = "    "


def _digest_text(digest: Digest, budget: int | None = None) -> str:
    """
    A digest as the asking agent reads it: four headed slots, every one present.

    **A slot is never left out and an item is never dropped.** An empty slot says
    what its emptiness means, in words, because "INCIDENTS" missing from a report
    reads as a report with no such section. The unverified slot and both incident
    lists are one line per item whatever the budget, since the digest may not show
    fewer of either than the board holds; what a budget takes is the prose under a
    line, and each cut says where the rest is.

    ``budget`` is the allowance for each piece of prose -- a result's first line, a
    list of what was not run, an incident's body -- laid out, or None for the
    per-piece bounds alone. ``_bounded_digest_text`` sets it.

    **Every label is one line, every field an agent wrote on it is delimited
    (``_said``, ``_named``) and every piece of prose is quoted**, for the reason
    ``_board_line`` gives: a slot heading is a line like any other, and a list of
    what was not run that could write one would put a forged INCIDENTS slot under
    the sentence telling the lead to report every item to the operator.

    **An incident's subject and text are shown only when a board read shows
    them.** See ``_incident_lines``.

    **The waiting slot names who is waiting and for what kind of thing, and not
    what about.** ``Owed.summary`` is the operator's queue row, and for a parked
    approval it is the text of a call nobody has approved: another agent's concern
    still in review, a command not yet run. This tool is auto-approved on the
    grounds that everything it returns was reviewed or is the caller's own board
    (``approval._BUS_AUTO``), and one summary would make it a way round the gate.
    The operator's pane shows them.

    Says nothing about clock time. The stamps behind "since" order events inside
    this process and mean nothing outside it.
    """

    def prose(text: str, cap: int, mark: str) -> tuple[list[str], int]:
        return _quoted(text, cap, _ITEM_LEAD, budget, mark)

    if digest.since is None and digest.restored_at is not None:
        # A resumed lead has no mark, so this is the whole restored board, and
        # "from its start" above the sentence telling it what to report would
        # have it report an earlier run's work as this round's.
        opening = (
            f"Digest of this session's board as it was restored from its log: you "
            f"have not read one in this run, so CHANGED lists the whole board. What "
            f"is marked '{BEFORE_RESTART}' there is history and is not this round's "
            f"news."
        )
    elif digest.since is None:
        opening = "Digest of this session from its start: you have not read one before."
    else:
        opening = "Digest of this session since you last read one."
    closed = any(
        change.kind is ChangeKind.CLOSED and _closing_reason(change.line)
        for change in digest.changed
    )
    lines = [f"{opening} {_legend(closed)}"]

    lines += ["", changed_heading(digest.changed)]
    for change in digest.changed:
        lines += _change_lines(change, prose)

    lines += ["", _waiting_heading(digest.waiting)]
    lines += [_waiting_line(owed) for owed in digest.waiting]

    lines += ["", unverified_heading(digest.unverified) + (_IN_FULL if digest.unverified else "")]
    for item in digest.unverified:
        lines += _unverified_lines(item, prose)

    lines += ["", incidents_heading(digest) + (_IN_FULL if digest.incidents else "")]
    for reported in digest.incidents:
        lines += _incident_lines(reported, prose)
    if digest.earlier_incidents:
        # Listed, not counted. They were in an earlier digest in full, and a
        # reader of this one still has to be able to see that they exist.
        lines.append(f"  {EARLIER_INCIDENTS}:")
        lines += [
            f"  - {_incident_headline(reported)}{'' if reported.on_board else _NO_SUBJECT}"
            for reported in digest.earlier_incidents
        ]
    return "\n".join(lines)


def _change_lines(change: TaskChange, prose: _Prose) -> list[str]:
    named = dataclasses.replace(
        change,
        task_id=_named(change.task_id),
        title=_said(change.title),
        holder=None if change.holder is None else _one_line(change.holder),
    )
    out = [f"- {change_label(named)}"]
    # A close's line is the store's label and then the operator's reason. The
    # label is on the line above already; what is left is the operator's, and
    # behind an agent's mark it would be attributed to nobody who wrote it.
    closed = change.kind is ChangeKind.CLOSED
    line = _closing_reason(change.line) if closed else change.line
    if line:
        shown, dropped = prose(line, _MAX_DIGEST_LINE_CHARS, _ANSWER if closed else _QUOTE)
        out += shown
        if dropped:
            out.append(f"    {_rest(dropped, named.task_id)}")
    return out


def _waiting_line(owed: Owed) -> str:
    """
    One thing the operator owes, as an agent is told of it: who and what kind.

    A decision and a command are each worded apart from the rest. An approval, a
    question and a failure each stop the node they name, so "is waiting" is true
    of it; an agent that raised a decision or asked for a command was told to
    carry on, and may be working. A command says which of its two open states it
    is in (``digest.owed_standing``), because one the operator has started is
    owed a cancel and not a reading, and "has not run" would be false of it.
    """
    who = _one_line(owed.who)
    if owed.kind is ObligationKind.DECISION:
        return f"- {owed.kind.value}: {who} asked the operator and has no answer yet"
    if owed.kind is ObligationKind.COMMAND:
        return (
            f"- {owed.kind.value}: {who} asked the operator to run it; it is {owed_standing(owed)}"
        )
    return f"- {owed.kind.value}: {who} is waiting"


def _waiting_heading(waiting: Sequence[Owed]) -> str:
    if not waiting:
        return "WAITING ON THE OPERATOR (0): nothing in this session is parked on the operator."
    return (
        f"WAITING ON THE OPERATOR ({len(waiting)}), oldest first: {owed_counts(waiting)}. "
        f"What each is about is the operator's to read and is not shown here."
    )


def _unverified_lines(item: Unverified, prose: _Prose) -> list[str]:
    named = dataclasses.replace(
        item,
        task_id=_named(item.task_id),
        title=_said(item.title),
        owner=None if item.owner is None else _one_line(item.owner),
    )
    out = [f"- {unverified_label(named)}"]
    if item.text:
        shown, dropped = prose(item.text, _MAX_NOT_RUN_CHARS, _QUOTE)
        out += shown
        if dropped:
            out.append(f"    {_rest(dropped, named.task_id)}")
    return out


# What stands where a subject would, on a line that has nothing under it to say why.
_NO_SUBJECT = " (subject not shown)"


def _incident_headline(reported: Reported) -> str:
    """
    The one line every incident has, whatever else is shown of it: who reported
    it to whom and what became of it, and its subject only when a board read
    shows that subject (``_incident_lines``).

    A subject is as much the sender's text as a body is -- up to
    ``MAX_LINE_CHARS`` of it -- so a headline that always carried one would hand
    every agent a withheld message on the line meant to say only that it exists.
    The same goes for the id of a task that is on no row of the board: it is
    whatever the sender typed, and no board read shows it.
    """
    c = _delimited_note(reported.concern)
    # After the subject, which stands between quotes, so it reads as the tool's.
    unread = f" -- {UNDELIVERABLE}" if c.undeliverable else ""
    if reported.on_board:
        return incident_label(dataclasses.replace(reported, concern=c)) + unread
    if c.task_missing:
        c = dataclasses.replace(c, task_id=None)
    return incident_attribution(dataclasses.replace(reported, concern=c)) + unread


def _incident_lines(reported: Reported, prose: _Prose) -> list[str]:
    """
    One incident: its attribution always, and its subject and text only when a
    board read hands the same reader the same subject and text.

    ``read_digest`` is open to every agent in the session and is never parked, so
    whatever it prints is broadcast. A board read broadcasts a concern on one
    condition -- a row carries it (``Reported.on_board``) -- and withholds a
    concern the operator withdrew and one that named no task on the board, which
    is mail between two agents. Printing those here would make the incident flag
    a way to publish a message its sender addressed to one agent, and would
    deliver a message the operator stopped under a label saying it was not
    delivered.

    The line itself stays, so the slot never holds fewer incidents than the board
    does, and it says why nothing the sender wrote is on or under it.
    """
    c = _delimited_note(reported.concern)
    out = [f"- {_incident_headline(reported)}"]
    if not reported.on_board:
        out.append(f"    ({_withheld(c)})")
        return out
    shown, dropped = prose(c.body, _MAX_CONCERN_CHARS, _QUOTE)
    out += shown
    if dropped and c.task_id is not None:
        out.append(f"    {_rest(dropped, c.task_id)}")
    return out


def _withheld(c: BoardConcern) -> str:
    """
    Why an incident's subject and text are not in a digest. The two mistakes a reader could
    make about the gap are named apart: one is the operator's act and the other
    is the sender's choice of address.
    """
    if c.state is ConcernState.WITHDRAWN:
        return "its subject and text are not shown here: the operator withdrew it before delivery"
    if c.task_id is None or c.task_missing:
        return (
            f"its subject and text are not shown here: it named no task on this "
            f"board, so it is mail between {c.sender} and {c.recipient}, and "
            f"{c.recipient} reads it with read_inbox"
        )
    return "its subject and text are not shown here: no row of this board carries it"


def _bounded_digest_text(digest: Digest) -> str:
    """
    The whole digest reply, under the ceiling a board read keeps.

    ``_board_text``'s rule and for its reason: structure is never dropped and
    prose degrades. Rendered once whole; if that fits it is the reply. Otherwise
    it is rendered with no prose to measure the floor, and what the ceiling leaves
    over the floor is shared equally between the pieces of prose.

    The floor wins when even it does not fit. Every unverified item and every
    incident is a line of the floor, and dropping one to get under a number is the
    thing a digest may not do.
    """
    full = _digest_text(digest)
    if len(full) <= _MAX_BOARD_CHARS:
        return full
    floor = _digest_text(digest, budget=0)
    pieces = (
        sum(1 for change in digest.changed if change.line)
        + sum(1 for item in digest.unverified if item.text)
        + sum(1 for reported in digest.incidents if reported.on_board and reported.concern.body)
    )
    notice = (
        "\n\nThis digest did not fit, so each result line, list of what was not run "
        "and incident body below is cut to {share} characters. Nothing is hidden: "
        "every change, every unverified statement and every incident is listed. "
        "read_task(task_id) returns any one task, its specification whole."
    )
    # A share is what a piece may cost laid out, which ``_quoted`` charges whole.
    spare = _MAX_BOARD_CHARS - len(floor) - len(notice)
    share = max(0, spare // max(1, pieces))
    head, _, rest = _digest_text(digest, budget=share).partition("\n")
    return head + notice.format(share=share) + "\n" + rest


def _refusal_text(refusal: TaskRefusal) -> str:
    """
    What the agent is told, and what to do about it.

    The words live here rather than in the store because ``_apply`` is pure and
    prose is presentation. Each one names the recovery: a refusal that only says no
    sends a worker back to make the same mistake, which STYLE.md §3 records as
    having already happened once at this exact boundary.

    The match is exhaustive on purpose -- a new ``TaskRefusal`` member with no
    sentence is a mypy error at the final arm rather than a ``KeyError`` reaching an
    agent mid-run.
    """
    match refusal:
        case TaskRefusal.DUPLICATE_ID:
            return _duplicate_id_text(None)
        case TaskRefusal.WOULD_CYCLE:
            return (
                "Its depends_on would close a dependency cycle, which leaves every task "
                "in the loop permanently unclaimable. Check what the tasks you named "
                "already depend on."
            )
        case TaskRefusal.NO_SUCH_TASK:
            return f"No task with that id is on your board. {_NOT_ON_BOARD}"
        case TaskRefusal.ALREADY_COMPLETE:
            return "It is already complete. Nothing changed, and nothing is left to do on it."
        case TaskRefusal.CLOSED_BY_OPERATOR:
            return _CLOSED_UNDER_YOU
        case TaskRefusal.NOT_CLAIMED:
            return "Nobody holds it. Claim it first, with claim_task and that id."
        case TaskRefusal.NOT_YOURS:
            return (
                "Another agent holds the claim, and only the claimer can change a task's "
                "state. Send them a concern rather than acting on it yourself."
            )
        case TaskRefusal.NOT_THE_DECLARER:
            return (
                "Only the agent that declared a task, or the operator, can replace its "
                "specification, and you did not declare this one. Send its declarer a "
                "concern saying what should change."
            )
        case TaskRefusal.AMENDER_HELD_IT:
            return (
                "You declared it, and you also hold it or have held it, so the "
                "specification is one you were bound by and is not yours to replace. "
                "Nothing changed. The operator can amend it; a retry will not."
            )
        case TaskRefusal.CLAIMER_AMENDED_IT:
            return (
                "You replaced this task's specification, so it is not yours to work to: "
                "the agent that sets a specification and the agent bound by it are "
                "different agents. Leave it for another agent to claim; if none "
                f"will, {_OPERATOR_CLOSES}, {_CLOSED_IS_NOT_DONE}."
            )
        case TaskRefusal.NOT_APPLIED:
            return "The board never saw the request -- the application is shutting down."
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(refusal)


def _duplicate_id_text(on_my_board: bool | None, held_off_fleet: str | None = None) -> str:
    """
    What a declarer is told when the id it chose is taken.

    **Two mistakes under one refusal, named apart when the caller knows which.**
    Task ids are one namespace for every session in the window
    (``store._declaration_refusal``), so the id may be a row of the declarer's
    own board or a task of another session that no read of this board shows.
    "Claim the task that is there" is the recovery for the first and a dead end
    for the second -- that claim is answered "not on your board" -- and a task
    waiting on the second can never be satisfied. ``on_my_board`` is whether the
    declarer's own board lists the id, or None when nothing read the board to
    find out, and then the sentence covers both.

    **A third, when the store names it** (``TaskWriteSettled.held_off_fleet``):
    the id is a task of a session the operator removed from the fleet. That
    board is on no card and no agent of it is running, so "another session in
    this window" would send the lead to an operator who can see no such
    session; and nothing frees the id but a restart, which is the one fact here
    the other two sentences do not have. The store's answer is about the apply
    and outranks ``on_my_board``, which a later read made.
    """
    if held_off_fleet is not None:
        return (
            f"That id belongs to a task of session {_named(held_off_fleet)}, which the "
            "operator removed from the fleet in this window. Ids are shared by every "
            "session pptmstr has run since it started, a removed session keeps its "
            "board, and so the id stays held until pptmstr is restarted. Use a "
            "different id: that task cannot be read, claimed or declared from here, "
            "and a task on your board that waits on it never becomes claimable -- "
            f"{_OPERATOR_CLOSES}, and the work is then declared again under a new id "
            "that does not wait on it."
        )
    if on_my_board:
        return (
            "That id is already on your board, and re-declaring it would overwrite "
            "work another agent may already be doing. Use a different id, or claim "
            "the task that is there."
        )
    elsewhere = (
        "it cannot be read, claimed or declared from here. Use a different id. A "
        "task on your board that waits on this one says so on its row and never "
        f"becomes claimable: {_OPERATOR_CLOSES}, and the work is then declared "
        "again under a new id that does not wait on it."
    )
    if on_my_board is None:
        return (
            "That id is already taken, and re-declaring it would overwrite work "
            "another agent may already be doing. If read_board lists it, use a "
            "different id or claim the task that is there. If it does not, a task "
            f"of another session in this window has the id, and {elsewhere}"
        )
    return (
        "That id belongs to a task of another session in this window. Ids are "
        "shared by every session pptmstr is running, that task is not on your "
        f"board, and {elsewhere}"
    )


def _held_text(lost: str, row: BoardTask | None, root: bool) -> str:
    """
    What an agent is told when the task it asked for by id is held.

    ``row`` is the task as the asker's board has it, or None when it could not
    be read back. A holder that is working is somebody to send a concern to. A
    holder that has stopped is not, and the sentence for it is the row's own
    (``_stranded``): sent to "send them a concern", a lead writes to an agent
    of an earlier run that nothing delivers to, about a claim that only
    the lead's own ``release_task`` gives back.
    """
    if row is None or not row.owner_gone:
        return (
            f"{lost} Another agent holds it. read_board says who; send them a "
            f"concern if it matters, and claim something else."
        )
    row = _delimited_task(row)
    return f"{lost} {row.owner} holds it, but {_stranded(row, root)}. Nothing changed."


def _claim_refusal_text(
    refusal: ClaimRefusal, wanted: TaskId | None, row: BoardTask | None = None, root: bool = False
) -> str:
    """
    Why a claim won nothing, and what to do about each reason.

    Apart because each has a different next step, and "was not claimed" covering
    all of them sends an agent to read the board to find out which mistake it
    made. Exhaustive for ``_refusal_text``'s reason.

    ``row`` and ``root`` are read for a held task only (``_held_text``).
    """
    named = _named(wanted or "")
    lost = f"Task {named} was NOT claimed."
    match refusal:
        case ClaimRefusal.NOTHING_CLAIMABLE:
            # An ordinary condition, not a failure. Said plainly so the agent
            # stops asking and does not retry into a loop.
            return (
                "Nothing claimable right now -- the board is empty, or everything "
                "left on it is blocked by unfinished dependencies, held by another "
                "agent, or a task you gave back or wrote the specification of. "
                "read_board shows which; a task you gave back can still be claimed "
                "by its id."
            )
        case ClaimRefusal.NOT_ON_YOUR_BOARD:
            return f"{lost} No task with that id is on your board. {_NOT_ON_BOARD}"
        case ClaimRefusal.HELD:
            return _held_text(lost, row, root)
        case ClaimRefusal.ALREADY_YOURS:
            return (
                f"{lost} You already hold it, and nothing changed. read_task returns "
                f"its specification; complete_task or release_task ends your claim."
            )
        case ClaimRefusal.COMPLETE:
            return f"{lost} It is already complete. read_task shows its result."
        case ClaimRefusal.WAITING:
            return (
                f"{lost} It still waits on an unfinished dependency, or on an id no "
                f"task on this board has. read_task shows which: it becomes claimable "
                f"when the first kind finish, and says what the second needs."
            )
        case ClaimRefusal.AMENDED_BY_YOU:
            return f"{lost} {_refusal_text(TaskRefusal.CLAIMER_AMENDED_IT)}"
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(refusal)


# -- decisions -----------------------------------------------------------------------

# What starts every line the operator typed, and nothing else in any reply: an
# answer to a decision, and the reason a task was closed. Its own mark and not
# ``_QUOTE``: that one is announced as text an agent wrote, and these are the
# texts here whose whole value is that no agent wrote them. A line of an agent's
# own is always behind ``_QUOTE`` first, so no agent's text can begin a line
# with this. Each of the two has its own legend, since no reply shows both: a
# decision is read by its own tool.
_ANSWER = "> "

_CLOSING_LEGEND = (
    "A line whose first characters after any indent are '> ' is the reason the "
    "operator gave for closing a task, as the operator typed it, and is neither "
    "this tool speaking nor text an agent wrote."
)

_ANSWER_LEGEND = (
    "A line whose first characters after any indent are '> ' is the operator's "
    "answer, as the operator typed it, and is neither this tool speaking nor text "
    "an agent wrote."
)

# How much of a question a list of decisions shows on its one line. The read of
# one decision by id returns the question whole.
_MAX_QUESTION_LINE_CHARS = 300

# What a read of one decision may spend on everything that is not one of the
# asker's texts, a blocked task's id, or the operator's answer: the two legends,
# the headline, the headings and the sentences of this module's own.
#
# It is the last term of the sum that makes that reply fit ``_MAX_BOARD_CHARS``
# by construction, with the answer in it whole. Laid out, a text of n characters
# costs at most 3n + 2 -- every character can be a line break, and each line
# carries a two-character mark and a newline -- and an id at most twice its
# length and two quote marks. So, at their largest:
#
#   4 texts    4 x (3 x 1,000 + 2)   = 12,008
#   answer          3 x 4,000 + 2    = 12,002
#   8 ids      8 x (2 x 200 + 4)     =  3,232
#   this                             =  2,000
#                                      29,242  <=  30,000
#
# tests/test_bus.py computes the sum from the constants and builds that reply.
_DECISION_FRAME_CHARS = 2_000

# What a list of decisions keeps back for the line saying it left some out.
_LIST_NOTICE_ROOM = 400

# How much of a role name a reply about a decision shows.
_MAX_ROLE_CHARS = 80

_REVERSIBLE_SAID: dict[bool | None, str] = {True: "yes", False: "no", None: "unstated"}


def _standing_said(standing: Standing, whose: str) -> str:
    """Whose decision it is, with ``whose`` the asker as its reader names it."""
    match standing:
        case Standing.OPERATORS:
            return "the operator's to take"
        case Standing.TAKEN:
            return f"taken by {whose}, for the operator to ratify"
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(standing)


class InvalidStanding(Exception):
    """
    A ``standing`` that is neither member, an absent one included.

    Refused and not defaulted. The two are drawn apart for the operator because
    an answer that disagrees with a decision already taken means work to undo,
    so either default would put a statement about that in the asker's mouth. A
    refused raise holds nothing: the asker says which and sends it again.
    """

    def __init__(self, raw: object) -> None:
        super().__init__(
            f"standing must be 'operators' or 'taken', and {str(raw)[:80]!r} is "
            f"neither. 'operators': nobody has decided, and what this blocks is "
            f"waiting for the answer. 'taken': you already decided and went on, and "
            f"are asking the operator to ratify it."
        )


def _stated_standing(raw: object) -> Standing:
    word = str(raw or "").strip().lower().replace("'", "")
    if word in ("operators", "operator"):
        return Standing.OPERATORS
    if word == "taken":
        return Standing.TAKEN
    raise InvalidStanding(raw)


def _stated_reversible(raw: object) -> tuple[bool | None, str]:
    """
    Whether a decision can be undone, as its asker said it: True, False, or None
    for "did not say" -- and a sentence for the asker when it said something that
    is neither.

    Three-valued, so not ``model.stated_flag``, which reads an absent flag as
    False: here False is the statement "this cannot be undone". An unreadable
    value is not guessed in either direction. It is stored as unstated, which
    claims nothing, and said back.
    """
    if raw is True or raw is False:
        return raw, ""
    word = str("" if raw is None else raw).strip().lower()
    if word in ("true", "yes"):
        return True, ""
    if word in ("false", "no"):
        return False, ""
    if word in ("", "none", "null", "unstated"):
        return None, ""
    return None, (
        f"reversible was not kept: {str(raw)[:80]!r} is neither true nor false, so the "
        f"operator is shown this decision as 'reversible: unstated'."
    )


_BLOCKS_ARE = (
    "blocks is a list of the ids of tasks on your board that wait for this answer, "
    "as read_board shows them, and it may be empty. Anything else that waits "
    "belongs in the question or in if_unanswered."
)


def _stated_blocks(raw: object) -> tuple[tuple[TaskId, ...], str | None]:
    """
    The task ids a raise names as blocked, each once and in the order given, and
    why they are not accepted when they are not.

    A list and never a sentence. A bare string is taken as one id, which is what
    a model writes for a list of one; a sentence in its place is then one id no
    board has, and the store refuses it by name.
    """
    if raw is None:
        return (), None
    listed = raw if isinstance(raw, list | tuple) else [raw]
    ids = tuple(dict.fromkeys(str(entry).strip() for entry in listed if str(entry).strip()))
    if len(ids) > MAX_BLOCKED_TASKS:
        return (), (
            f"blocks names {len(ids)} tasks and the limit is {MAX_BLOCKED_TASKS}. Name "
            f"the ones that wait on this answer directly; what waits on those follows "
            f"from the board."
        )
    longest = max(ids, key=len, default="")
    if len(longest) > MAX_ID_CHARS:
        return (), (
            f"an entry of blocks is {len(longest)} characters and the limit is "
            f"{MAX_ID_CHARS}. {_BLOCKS_ARE}"
        )
    return ids, None


def _blocks_said(blocks: Sequence[TaskId]) -> str:
    """What a decision blocks, on one line: never silence for an empty list."""
    if not blocks:
        return "blocks nothing on the board"
    return "blocks " + ", ".join(_named(task_id[:MAX_ID_CHARS]) for task_id in blocks)


def _decision_refusal_text(
    refusal: DecisionRefusal, decision_id: str, root: bool, strangers: Sequence[TaskId] = ()
) -> str:
    """
    Why a raise or a withdrawal did not take effect, and what to do instead.
    Exhaustive for ``_refusal_text``'s reason.

    **Every remedy named is one the refused agent can take.** ``root`` is whether
    it is its session's lead, which has no lead to pass a question to, holds none
    of the sub-agents' allowance, and can read every decision of its session.

    **To a sub-agent, somebody else's decision and one that does not exist get
    one sentence.** Its ``read_decisions`` cannot tell them apart -- it is handed
    its own and nothing else -- and a withdrawal that could would be a way to
    ask whether an id is another agent's. The root is shown every decision of
    its session, so it is told which.

    ``strangers`` is the entries of ``blocks`` the board does not have, when the
    caller found out which.
    """
    match refusal:
        case DecisionRefusal.TOO_MANY_OPEN:
            return (
                f"You have {MAX_OPEN_DECISIONS_PER_ASKER} decisions open in front of "
                f"the operator, which is the most one agent may have. The limit "
                f"counts only your own. read_decisions lists them: withdraw one that "
                f"no longer matters with withdraw_decision, or call again once the "
                f"operator has answered one."
            )
        case DecisionRefusal.SESSION_FULL:
            return (
                f"The sub-agents of this session have {MAX_OPEN_SUBAGENT_DECISIONS} "
                f"decisions open in front of the operator between them, which is the "
                f"most they may have. read_decisions lists yours, and withdraw_decision "
                f"frees a place if one of them no longer matters. If you have none to "
                f"withdraw, put this to your lead as a concern: the lead has an "
                f"allowance of its own that the sub-agents do not spend, and can raise "
                f"it for you."
            )
        case DecisionRefusal.UNKNOWN_TASK:
            named = ", ".join(_named(task_id[:MAX_ID_CHARS]) for task_id in strangers)
            which = f"blocks names {named}, and" if named else "An entry of blocks is"
            return (
                f"{which} no task with that id is on your board. {_BLOCKS_ARE} Leave "
                f"blocks out and the decision is recorded as blocking nothing on the "
                f"board."
            )
        case DecisionRefusal.ALREADY_RAISED:
            return "The id generated for it was already in use. Call again; nothing changed."
        case DecisionRefusal.NOT_THE_ASKER if root:
            return (
                "Another agent of your session raised it, and only the agent that "
                "raised a decision withdraws it. read_decisions shows whose it is; "
                "send that agent a concern if it should be taken back. The operator "
                "can dismiss it from their own pane."
            )
        case DecisionRefusal.NO_SUCH_DECISION | DecisionRefusal.NOT_THE_ASKER:
            return _unknown_decision_text(decision_id, root)
        case DecisionRefusal.ALREADY_ENDED:
            return (
                "It has already ended -- answered, dismissed or withdrawn -- and an "
                "ended decision stays as it ended. read_decisions shows which."
            )
        case DecisionRefusal.NOT_APPLIED:
            return _refusal_text(TaskRefusal.NOT_APPLIED)
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(refusal)


def _unknown_decision_text(decision_id: str, root: bool) -> str:
    """
    The answer to an id the reader is shown no decision under.

    In words and never an empty list. Decisions are not in the board log, so an
    agent resumed after a restart has a transcript saying it is waiting on one
    that no longer exists anywhere, and the operator's queue no longer shows it.
    """
    named = _named(decision_id[:MAX_ID_CHARS])
    if root:
        return (
            f"No decision {named} is recorded in your session; decisions do not "
            f"survive a restart of pptmstr -- if it still matters, raise it again. "
            f"read_decisions with no id lists every decision raised in your session."
        )
    return (
        f"No decision {named} is recorded for you; decisions do not survive a "
        f"restart of pptmstr -- if it still matters, raise it again. A decision "
        f"another agent raised is shown to that agent and to the session's lead, "
        f"and never here."
    )


def _age(seconds: float) -> str:
    """How long ago, in the one unit a reader needs."""
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole} second(s)"
    if whole < 3600:
        return f"{whole // 60} minute(s)"
    return f"{whole // 3600} hour(s) {whole % 3600 // 60} minute(s)"


def _answer_lines(text: str) -> list[str]:
    """
    The operator's answer, whole, as the lines it is shown on: every one behind
    ``_ANSWER``, none cut, none dropped.

    A line break of any kind ``str.splitlines`` knows starts a new marked line,
    so no character of an answer can put what follows it on a line that reads as
    the tool's. Nothing else is changed: no stripping, and an answer ending in a
    line break ends in an empty marked line, so one made only of ``\n`` breaks is
    exactly the marked lines joined by ``\n``.
    """
    pieces = text.splitlines(keepends=True)
    lines = ["".join(piece.splitlines()) for piece in pieces]
    if pieces and pieces[-1] != lines[-1]:
        lines.append("")
    return [f"{_ANSWER}{line}" for line in lines]


def _unread(decision: Decision) -> bool:
    """Whether the operator ended ``decision`` and its asker has not read that by id."""
    return (
        isinstance(decision.outcome, Answered | Dismissed) and decision.first_delivered_at is None
    )


def _own_block(heading: str, text: str) -> list[str]:
    """One of the asker's own texts, whole, under a heading of its own."""
    if not text.strip():
        return [f"{heading}: (nothing was stated)"]
    return [f"{heading}:", *_quoted(text, len(text), "")[0]]


def _state_word(decision: Decision) -> str:
    match decision.outcome:
        case None:
            return "open"
        case Answered():
            return "answered"
        case Dismissed():
            return "dismissed"
        case Withdrawn():
            return "withdrawn"
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(decision.outcome)


def _headline(decision: Decision, asker: str | None) -> str:
    """
    The line a decision opens with in either reply. ``asker`` is the role that
    raised it when that is not the reader, and None when the reader did.
    """
    whose = "you" if asker is None else asker
    line = (
        f"Decision {_named(decision.id)} [{_state_word(decision)}] "
        f"{_standing_said(decision.standing, whose)}; "
        f"reversible: {_REVERSIBLE_SAID[decision.reversible]}"
    )
    if asker is None:
        return line
    return f"{line}; raised by {asker}, not by you: you can read it and cannot withdraw it"


def _decision_whole(decision: Decision, now: float, asker: str | None) -> list[str]:
    """
    One decision with everything on it, as the read by id returns it.

    **The operator's answer is whole on every read**, behind its own mark
    (``_answer_lines``), whether this is the first read or the fortieth. It has
    no other copy an agent can reach: a concern cut in a board read is whole in
    ``read_task``, and an answer cut here would be gone. So nothing here cuts,
    and the reply fits because each thing in it was bounded where it was
    accepted (``_DECISION_FRAME_CHARS``).

    **The evidence and the recommendation are two blocks and are never joined**:
    they are held apart on the record so that what is known can be read before
    what the asker wants done with it, and a reply that ran them together would
    hand the next reader the framing as though it were the finding.

    The asker's texts are quoted like any agent's (``_quoted``): this reply may
    be passed on, and they are still not the tool's words. ``asker`` is as on
    ``_headline``; a reader that is not the asker is told whether the asker has
    read the answer, because nothing hands it to the asker but its own read.
    """
    mine = asker is None
    by = "you" if mine else asker
    named = _named(decision.id)
    lines = [_headline(decision, asker)]
    lines += _own_block(f"Question, as {by} asked it", decision.question)

    read_at = decision.first_delivered_at
    if mine:
        delivery = (
            "handed to you now for the first time"
            if read_at is None
            else f"first delivered {_age(now - read_at)} ago"
        )
    else:
        delivery = (
            f"which {by} has not read"
            if read_at is None
            else f"which {by} first read {_age(now - read_at)} ago"
        )
    match decision.outcome:
        case None:
            if not mine:
                lines.append(
                    f"The operator has not answered. When they do, pptmstr sends this "
                    f"session a message naming it, and {by} is not woken: "
                    f"read_decisions(decision_id={_argument(named)}) then returns it."
                )
            elif decision.asker[1] is None:
                lines.append(
                    f"The operator has not answered. {WAKE_SAID} Ask again when you "
                    f"reach what this blocks."
                )
            else:
                lines.append(
                    f"The operator has not answered. {WAKE_SAID_SUBAGENT} Ask again "
                    f"when you reach what this blocks."
                )
        case Answered(text=text):
            lines.append(f"The operator's answer, {delivery}:")
            lines += _answer_lines(text) or ["(the operator answered with no text)"]
        case Dismissed():
            lines.append(
                f"The operator closed it without answering ({delivery}). No answer "
                f"exists: go on as you said you would if it went unanswered, or raise "
                f"it again if it still matters."
                if mine
                else f"The operator closed it without answering, {delivery}. No answer " f"exists."
            )
        case Withdrawn():
            lines.append(
                "You withdrew it. No answer exists."
                if mine
                else f"{by} withdrew it. No answer exists."
            )

    lines.append(f"It {_blocks_said(decision.blocks)}, as {by} stated it.")
    lines += _own_block(f"If unanswered, as {by} stated it", decision.if_unanswered)
    lines += ["", *_own_block(f"Evidence, as {by} gave it", decision.evidence)]
    lines += ["", *_own_block(f"Recommendation, as {by} gave it", decision.recommendation)]
    return lines


def _decision_listed(decision: Decision, now: float, asker: str | None) -> list[str]:
    """
    One decision in the list: what it is, one line of its question, and what
    became of it. **Never the operator's answer.** A list has no bound on how
    many answers it would hold, so it says an answer exists and names the read
    that returns it, and the store records an answer as read only for that read.
    """
    mine = asker is None
    by = "you" if mine else asker
    named = _named(decision.id)
    by_id = f"read_decisions(decision_id={_argument(named)})"
    flat = _one_line(decision.question)
    lines = [
        _headline(decision, asker),
        f"Question: {_said(flat[:_MAX_QUESTION_LINE_CHARS])}",
    ]
    cut = len(flat) - _MAX_QUESTION_LINE_CHARS
    if cut > 0:
        lines.append(f"... {cut} more character(s) -- {by_id} returns it whole")

    read_at = decision.first_delivered_at
    match decision.outcome:
        case None:
            lines.append(
                "The operator has not answered. Nothing will interrupt you when they "
                "do: ask again when you reach what this blocks."
                if mine
                else "The operator has not answered."
            )
        case Answered():
            if mine and read_at is None:
                lines.append(
                    f"THE OPERATOR HAS ANSWERED and you have not read the answer: "
                    f"{by_id} returns it whole."
                )
            elif mine:
                assert read_at is not None
                lines.append(
                    f"The operator answered, and you first read the answer "
                    f"{_age(now - read_at)} ago: {by_id} returns it again, whole."
                )
            else:
                seen = (
                    "has not read the answer"
                    if read_at is None
                    else f"first read the answer {_age(now - read_at)} ago"
                )
                lines.append(f"The operator answered, and {by} {seen}: {by_id} returns it whole.")
        case Dismissed():
            lines.append(
                "The operator closed it without answering. No answer exists: go on as "
                "you said you would if it went unanswered, or raise it again if it "
                "still matters."
                if mine
                else "The operator closed it without answering. No answer exists."
            )
        case Withdrawn():
            lines.append(
                "You withdrew it. No answer exists."
                if mine
                else f"{by} withdrew it. No answer exists."
            )
    return lines


def _decisions_text(
    shown: Sequence[Decision],
    wanted: str | None,
    now: float,
    reader: NodeId,
    role_of: Callable[[NodeId], str],
) -> str:
    """
    The reply to ``read_decisions``: one decision whole when an id was given,
    and otherwise a list of every decision the reader is shown.

    ``shown`` is what the store handed over -- the reader's own, and for the
    root of a session every decision raised in it. ``role_of`` names the asker
    of one that is not the reader's.

    **Both forms fit ``_MAX_BOARD_CHARS``.** The read by id fits by what a
    decision may hold (``_DECISION_FRAME_CHARS``). The list is filled against
    that bound and says what it left out, keeping first what the reader has to
    act on: the open ones, then the ones whose ending it has not read.
    """
    root = reader[1] is None

    def asker(decision: Decision) -> str | None:
        if decision.asker == reader:
            return None
        return _one_line(role_of(decision.asker))[:_MAX_ROLE_CHARS]

    if wanted is not None:
        chosen = next((d for d in shown if d.id == wanted), None)
        if chosen is None:
            return _unknown_decision_text(wanted, root)
        legend = f"{_QUOTE_LEGEND} {_ANSWER_LEGEND}"
        return "\n".join([legend, "", *_decision_whole(chosen, now, asker(chosen))])

    if not shown:
        whose = "in your session" if root else "for you"
        return (
            f"No decision is recorded {whose}. Decisions do not survive a restart "
            f"of pptmstr: one raised before a restart is gone, and has to be raised "
            f"again if it still matters."
        )

    mine = [d for d in shown if d.asker == reader]
    still = sum(1 for d in shown if d.is_open)
    counted = (
        f"Your session has {len(shown)} decision(s), {still} still open; "
        f"{len(mine)} of them you raised yourself. As the session's lead you are "
        f"shown every one, each marked with who raised it."
        if root
        else f"You have raised {len(shown)} decision(s), {still} still open."
    )
    head = (
        f"{counted} No answer is in this list: read_decisions(decision_id=...) returns "
        f"one decision with everything on it and the operator's answer whole. "
        f"{_QUOTE_LEGEND}"
    )

    def rank(decision: Decision) -> tuple[int, float]:
        if decision.is_open:
            return 0, decision.raised_at
        if decision.asker == reader and _unread(decision):
            return 1, decision.raised_at
        return 2, -decision.raised_at

    spare = _MAX_BOARD_CHARS - len(head) - _LIST_NOTICE_ROOM
    kept: dict[str, list[str]] = {}
    for decision in sorted(shown, key=rank):
        entry = ["", *_decision_listed(decision, now, asker(decision))]
        if _cost(entry) > spare:
            break
        spare -= _cost(entry)
        kept[decision.id] = entry

    lines = [head]
    for decision in shown:
        lines += kept.get(decision.id, [])
    left = [d for d in shown if d.id not in kept]
    if left:
        still_open = sum(1 for d in left if d.is_open)
        unread = sum(1 for d in left if d.asker == reader and _unread(d))
        lines += [
            "",
            f"... {len(left)} more decision(s) did not fit and are not listed "
            f"({still_open} open, {unread} with an ending you have not read). Each is "
            f"still returned by read_decisions(decision_id=...) under the id "
            f"raise_decision gave for it.",
        ]
    return "\n".join(lines)


def _auto_depends_text(added: tuple[TaskId, ...]) -> str:
    """
    What the declarer is told when the board added a dependency it did not ask for.

    Said at declaration rather than left for the declarer to discover, because the
    alternative is finding out from a worker that cannot claim the task -- the same
    fact, arriving later and looking like a bug. This is the one place the board
    changes what an agent asked for, and an agent planning a sequence needs to know
    its plan was edited.

    Names the tasks and the reason together. "Depends on t-4" without the reason
    invites the lead to strip it back off, which is the concurrent write the field
    exists to prevent; the file overlap is what makes the edge non-negotiable.

    Each id is one another declaration chose, so it is laid out as ``_named``
    leaves it, like every other id in a reply.
    """
    ids = ", ".join(_named(task_id) for task_id in added)
    subject = "an unfinished task" if len(added) == 1 else "unfinished tasks"
    return (
        f"It also now depends on {ids}, added by the board: it writes files {subject} "
        f"on this board will also write. Leave the dependency in place -- it is what "
        f"keeps the two off the same file. If the overlap is wrong, narrow the touches "
        f"of one of them rather than removing the edge."
    )


def _already_behind_text(behind: tuple[TaskId, ...]) -> str:
    """
    What the declarer is told when the board left an overlap without a dependency
    because the other task already waits on this one.

    Said for the reason ``_auto_depends_text`` is, about the opposite order. Every
    other overlap queues the new task behind the older one; here the older one is
    behind the new, and a declarer that planned on the older task writing the
    shared file first has the order backwards. "Do not add the edge" is said
    because that is the repair a lead would reach for, and it is the cycle the
    board declined to make. The ids are laid out as ``_auto_depends_text`` lays
    its own.
    """
    ids = ", ".join(_named(task_id) for task_id in behind)
    one = len(behind) == 1
    return (
        f"It writes files {ids} also {'writes' if one else 'write'}, and the board "
        f"added no dependency on {'it' if one else 'them'}: "
        f"{'that task already waits' if one else 'those tasks already wait'} on this "
        f"one, so the edge would have left both sides waiting on each other. This "
        f"task is therefore worked first, and {ids} cannot be claimed until it is "
        f"finished, which is what keeps them off the same file. Do not add the "
        f"dependency yourself."
    )


# ``commandbus`` lays its replies out with this module's marks, helpers and
# bounds, and imports them under the names they have here: ``_ANSWER``,
# ``_QUOTE``, ``_QUOTE_LEGEND``, ``_MAX_BOARD_CHARS``, ``_LIST_NOTICE_ROOM``,
# ``_MAX_ROLE_CHARS``, ``_age``, ``_argument``, ``_cost``, ``_named``,
# ``_one_line``, ``_quoted`` and ``_schema``. The underscore on each says "not
# for a caller outside the bus", and ``commandbus`` is the bus: three more of its
# tools, kept in a module of their own. A second spelling of a mark there would
# be two marks the day one changed. Renaming one of these fails ``commandbus``
# at import.


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    """
    An explicit JSON Schema, for the tools that have an argument a caller may omit.

    ``@tool`` takes either spelling. Given a mapping carrying a string ``type`` and a
    ``properties`` key it announces that mapping verbatim; given anything else it
    reads the mapping as ``{name: python_type}`` and puts **every** declared name in
    ``required`` (``claude_agent_sdk.__init__._build_schema``, 0.2.134). The shorthand
    therefore cannot say "optional", and a description inviting a caller to omit an
    argument is refused by validation before the handler's default is ever reached.

    Built here rather than written out at each call site because the fallback is
    silent: a literal dict missing either key expands as the shorthand and marks
    everything required again, with nothing to notice.

    No ``additionalProperties: false``, and it must stay that way -- the gate adds
    ``FROM_KEY`` through ``updatedInput``, so a closed schema would reject every
    stamped call the bus runs on.
    """
    return {"type": "object", "properties": properties, "required": list(required)}


def build_server(session: AgentSession) -> Any:
    """
    An MCP server bound to one session.

    Per-session rather than shared: the handlers need that session's ``Bridge`` to
    emit on and to park against, and role resolution is scoped to the agents that
    session actually spawned.
    """
    bridge = session.bridge

    async def board_row(me: NodeId, task_id: TaskId) -> BoardTask | None:
        """
        One task as ``me``'s board holds it now, or None when it is not there or
        the store never answered. A read of its own, for a handler that has to know
        something about a task before or after it writes.
        """
        request_id = f"v-{uuid.uuid4().hex[:12]}"
        future = bridge.ask(request_id, BoardDelivered(request_id=request_id, tasks=()))
        bridge.emit(BoardRead(node_id=me, request_id=request_id))
        answer = await future
        if not isinstance(answer, BoardDelivered):
            return None
        return next((t for t in answer.tasks if t.id == task_id), None)

    async def board_ids(me: NodeId) -> frozenset[TaskId] | None:
        """
        The id of every task on ``me``'s board now, or None when the store never
        answered -- which is not an empty board.
        """
        request_id = f"v-{uuid.uuid4().hex[:12]}"
        unanswered = BoardDelivered(request_id=request_id, tasks=())
        future = bridge.ask(request_id, unanswered)
        bridge.emit(BoardRead(node_id=me, request_id=request_id))
        answer = await future
        if answer is unanswered or not isinstance(answer, BoardDelivered):
            return None
        return frozenset(t.id for t in answer.tasks)

    @tool(
        "post_concern",
        "Send a concern to another agent working on this task. Use the agent's "
        "role name, or 'lead' for the agent that started the work. Name task_id "
        "when the concern is about a task on the board. A worker's concern to its "
        "own lead wakes the lead: pptmstr sends the lead a message of its own "
        "naming the sender, read as a turn of its own once any turn in progress "
        "ends. No other concern wakes anyone -- not one to a worker, nor one to "
        "another session -- and its recipient reads it with read_inbox when it "
        "next looks.",
        _schema(
            {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "task_id": {
                    "type": "string",
                    "description": (
                        "The board task this concern is about. Omit it for a message "
                        "that is not about one. Naming it is what puts the reason on "
                        "the board next to the task, so an operator watching a row "
                        "that is not moving can see why."
                    ),
                },
                "basis": _BASIS_PROPERTY,
                "incident": {
                    **_FLAG_TYPE,
                    "description": (
                        "Set true when this concern reports something that went "
                        "wrong: a rule you or another agent broke, a file written "
                        "outside a task's touches, a tool that damaged the tree. It "
                        "puts a marker beside the subject and lets the slip be "
                        "counted; it changes nothing about who receives the "
                        "concern or when. Omit it for ordinary findings."
                    ),
                },
            },
            required=("to", "subject", "body"),
        ),
    )
    async def post_concern(args: dict[str, Any]) -> dict[str, Any]:
        sender = _sender(args)
        to = str(args.get("to", "")).strip()
        recipient = session.resolve_role(to)
        if recipient is None:
            # Named rather than silently dropped. A bare failure is what sent the
            # wake-path probe's worker into retrying the same wrong name; the
            # session distinguishes "not started yet" from "no such role", which are
            # different mistakes with different fixes.
            return _text(session.role_status(to))
        try:
            basis = _stated_basis(args)
        except InvalidBasis as bad:
            return _text(f"Concern NOT sent to {to}. {bad}")
        incident = stated_incident(args.get("incident"))
        subject = str(args.get("subject", "")).strip()
        body = str(args.get("body", ""))
        too_long = _length_refusal(
            "subject", subject, MAX_LINE_CHARS, "It is one line; put the rest in body."
        ) or _length_refusal("body", body, MAX_TEXT_CHARS)
        if too_long is not None:
            return _text(f"Concern NOT sent to {to}. {too_long}")
        about = str(args.get("task_id", "")).strip() or None
        unlinked = ""
        if about is not None and len(about) > MAX_ID_CHARS:
            # Dropped and said, for the reason an id that does not resolve is
            # not refused below: the message is the part that was certainly
            # meant, and no task on any board has an id this long.
            about, unlinked = None, (
                f" Its task_id was not kept: it was over the {MAX_ID_CHARS} characters "
                f"a task id may be, so the concern is not linked to a task."
            )

        concern = Concern(
            id=f"c-{uuid.uuid4().hex[:12]}",
            sender=sender,
            recipient=recipient,
            subject=subject,
            body=body,
            posted_at=time.monotonic(),
            # Stamped by the gate, never by the sender. What the recipient was
            # told differs from what the sender wrote, and this is the only place
            # that fact survives the approval being resolved.
            edited=bool(args.get(EDITED_KEY)),
            # Not checked against the board here. The tool's other refusal is a
            # role that does not resolve, which stops the message reaching anyone;
            # a task id that does not resolve costs the link and nothing else, and
            # refusing the whole message over an optional field would lose the one
            # part that was certainly correct. The pane reports the dangling id.
            task_id=about,
            # The sender's own statement, stored as given. The gate stamps who is
            # speaking; nothing can stamp how they know.
            basis=basis,
            incident=incident,
        )
        bridge.emit(ConcernPosted(sender, concern))
        if incident:
            # Said back because the flag is read generously (``stated_incident``),
            # so a sender that set it by accident finds out here.
            return _text(f"Concern delivered to {to}, marked as an incident.{unlinked}")
        return _text(f"Concern delivered to {to}.{unlinked}")

    @tool(
        "read_inbox",
        "Read concerns other agents have sent you. Returns everything waiting and "
        "marks it read. A lead is woken by pptmstr when one of its own workers "
        "sends it a concern; nothing else wakes you for one, so call this when you "
        "finish a piece of work as well.",
        {},
    )
    async def read_inbox(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        request_id = f"r-{uuid.uuid4().hex[:12]}"
        # Registered before the intent is emitted. Reversed, the UI thread could
        # apply and answer before the future exists, and this agent would wait
        # forever on a reply that had nowhere to land.
        future = bridge.ask(request_id, InboxDelivered(request_id=request_id, concerns=()))
        bridge.emit(InboxRead(node_id=me, request_id=request_id, at=time.monotonic()))
        answer = await future

        if not isinstance(answer, InboxDelivered) or not answer.concerns:
            return _text("Your inbox is empty.")
        closes = any(is_operator(c.sender) and c.body.strip() for c in answer.concerns)
        lines = [f"{len(answer.concerns)} concern(s). {_legend(closes)}"]
        for c in answer.concerns:
            if is_operator(c.sender):
                lines += ["", *_operators_notice(c)]
                continue
            who = _one_line(session.role_of(c.sender) or "another agent")
            lines += [
                "",
                f"[from {who}, {basis_label(c.basis)}{_edited_note(c.edited)}] "
                f"{incident_marker(c.incident)}{_said(c.subject) if c.subject else ''}",
                *_quoted(c.body, len(c.body), "")[0],
            ]
        return _text("\n".join(lines))

    @tool(
        "claim_task",
        "Take a unit of work off the shared board. Omit task_id to be given the "
        "oldest task whose dependencies are all met.",
        _schema(
            {
                "task_id": {
                    "type": "string",
                    "description": "The task to take. Omit it for the oldest claimable one.",
                }
            }
        ),
    )
    async def claim_task(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        wanted = str(args.get("task_id", "")).strip() or None
        request_id = f"k-{uuid.uuid4().hex[:12]}"
        future = bridge.ask(request_id, ClaimSettled(request_id=request_id, task=None))
        bridge.emit(TaskClaimRequested(node_id=me, request_id=request_id, task_id=wanted))
        answer = await future

        if not isinstance(answer, ClaimSettled) or answer.task is None:
            why = answer.refusal if isinstance(answer, ClaimSettled) else None
            if why is ClaimRefusal.HELD and wanted is not None:
                # A second read, on this path only: the refusal says the task is
                # held and not whether its holder is one a concern can reach.
                row = await board_row(me, wanted)
                return _text(_claim_refusal_text(why, wanted, row, root=me[1] is None))
            if why is not None:
                return _text(_claim_refusal_text(why, wanted))
            # No reason beside no task is the store never having seen the claim
            # (``effects.ClaimSettled``), so nothing here knows why.
            if wanted is not None:
                # It lists the causes and names the read that says which,
                # and does not state the one that is most often true.
                return _text(
                    f"Task {_named(wanted)} was NOT claimed. Either it is not on your "
                    f"board, or it is held or finished, or it still waits on an "
                    f"unfinished dependency, or you replaced its specification -- an "
                    f"agent that amended a task may not then claim it. "
                    f"read_task shows which; nothing changed."
                )
            return _text(
                "Nothing claimable right now -- the board is empty or the remaining "
                "work is still blocked by unfinished dependencies."
            )
        won = answer.task
        said = [f"You now own task {_named(won.id)}: {_said(won.title)}"]
        if won.property:
            # Ahead of the specification, where the board puts it: the claimer is
            # the reader the sentence is for.
            said.append(property_label(_said(won.property)))
        # Always, the first version included: it is the number a later
        # "specification v3" on the board is read against.
        said.append(f"You were handed version {won.version} of its specification.")
        if won.detail:
            # Beside what its declarer said of it, as every other read shows it:
            # this reply is where a worker's "what am I building" is set, so it is
            # where an unchecked claim in a specification most needs its marker.
            said += [
                _QUOTE_LEGEND,
                f"Specification ({basis_label(won.basis)}):",
                *_quoted(won.detail, len(won.detail), "")[0],
            ]
        return _text("\n".join(said) + _premises_text(session))

    @tool(
        "read_board",
        "See every task on your team's board: what exists, who holds it, what each "
        "one is waiting for, and its full specification. Read it before declaring "
        "work or reporting a conflict, and re-read it to check the spec of a task "
        "you hold -- the board is authoritative and your copy of it is not.",
        {},
    )
    async def read_board(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        request_id = f"b-{uuid.uuid4().hex[:12]}"
        future = bridge.ask(request_id, BoardDelivered(request_id=request_id, tasks=()))
        bridge.emit(BoardRead(node_id=me, request_id=request_id))
        answer = await future

        if not isinstance(answer, BoardDelivered) or not answer.tasks:
            # An empty board is an ordinary answer and a common one early in a
            # session. Said plainly, and distinguished from the claim tool's reply:
            # "nothing claimable" and "nothing declared" send an agent to different
            # places, and only one of them means the lead has not planned yet.
            return _text("Your board has no tasks on it yet.")
        # Every concern on the board, unfiltered. Which of them reach a row is
        # already decided by `board._open_concerns_by_task`, which skips the ones
        # naming no task -- a second filter here would be the same rule in two
        # places, free to drift from the one the pane reads.
        by_id = _agent_notes(answer.concerns)
        return _text(_board_text(answer.tasks, by_id, root=me[1] is None))

    @tool(
        "read_task",
        "Read ONE task on your team's board: its current specification, always "
        "whole, then its result, every note on it and every earlier version of its "
        "specification. read_board bounds each row so the whole board fits in one "
        "reply; this is where the rest of a row is. A task too large for one reply "
        "cuts everything but the specification and names, under each cut, the part "
        "that returns that text whole. Use it before building on a finished task, "
        "and to re-read the specification of a task you hold.",
        _schema(
            {
                "task_id": {
                    "type": "string",
                    "description": "The id read_board lists the task under.",
                },
                "part": {
                    "type": "string",
                    "description": (
                        "Omit it to read the task. Give it only when a read of the "
                        "task cut a text and named the part that returns it whole: "
                        f"'{_PART_RESULT}', '{_PART_NOT_RUN}', 'note:<id>' or "
                        "'version:<number>'. The reply is then that one text."
                    ),
                },
            },
            required=("task_id",),
        ),
    )
    async def read_task(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        task_id = str(args.get("task_id", "")).strip()
        request_id = f"o-{uuid.uuid4().hex[:12]}"
        # The same question ``read_board`` asks, and the same answer: one task is
        # found in the board the store projects for this sender rather than by a
        # lookup of its own, so there is no second scoping rule to keep in step
        # with ``Task.belongs_to`` and nothing here can show a task the board read
        # would not have listed.
        #
        # Parked with a write's fallback rather than ``read_board``'s empty board.
        # An empty board would be reported below as "no such task", which is a
        # statement about the board; a request the store never saw is not one.
        future = _park(bridge, request_id)
        bridge.emit(BoardRead(node_id=me, request_id=request_id))
        answer = await future

        if not isinstance(answer, BoardDelivered):
            return _text(f"Task {task_id} was NOT read. {_refusal_text(TaskRefusal.NOT_APPLIED)}")
        row = next((t for t in answer.tasks if t.id == task_id), None)
        if row is None:
            # Two mistakes, named apart: an id that is misspelled, and an id that is
            # right and belongs to another session. The recovery for both is the
            # listing, and it is said rather than implied.
            return _text(f"No task with the id {task_id!r} is on your board. {_NOT_ON_BOARD}")
        by_id = _agent_notes(answer.concerns)
        part = str(args.get("part") or "").strip()
        if part:
            # One text, and never a showing: the specification a holder is
            # recorded against is not in this reply.
            return _text(_part_text(row, by_id, part))
        text = _task_text(
            row, by_id, root=me[1] is None, ceiling=_MAX_BOARD_CHARS - _SHOWN_NOTE_ROOM
        )
        if not _unshown(row, me):
            return _text(text)
        shown = (
            f"\n\nYou hold this task and had not been shown version {row.version} of "
            f"its specification, which is the one above. You are now recorded as "
            f"having been shown it, and your result will be recorded as written "
            f"against it."
        )
        if len(text) + len(shown) <= _MAX_BOARD_CHARS:
            # This reply is the holder being shown the current text, whole, so it
            # is recorded as that -- with the version this reply carries and not
            # whatever the board holds by the time the record lands.
            #
            # Only when the reply itself, this sentence included, is within the
            # size the bus holds its board reads to, which sits under the
            # replies an agent was measured unable to read
            # (``_MAX_BOARD_CHARS``). The size of the specification alone is not
            # the test: it is one text of several the reply carries.
            bridge.emit(TaskVersionHanded(node_id=me, task_id=task_id, version=row.version))
            return _text(text + shown)
        # ``_task_text`` keeps the specification and the task's structure
        # whatever they cost, so this is a specification only the operator's
        # hand can have written, or a task whose lists and note headings alone
        # crowd a reply.
        return _text(
            text + f"\n\nYou hold this task and had not been shown version {row.version} of "
            f"its specification, which is the one above. This reply is "
            f"{len(text)} characters with that specification whole, longer than the "
            f"{_MAX_BOARD_CHARS} a reply is known to be readable at, so you are NOT "
            f"recorded as having been shown it: your result will be recorded as "
            f"written against the version you were handed."
        )

    @tool(
        "read_digest",
        "Read the digest of your session's round: what changed since you last read "
        "one, what is waiting on the operator, every statement on the board nobody "
        "said was run, and every concern marked as an incident. It is the report a "
        "lead makes to the operator at the END of a round, in place of relaying "
        "each worker's return as it arrives. Reading it moves your mark, so the "
        "next one starts where this one stopped.",
        {},
    )
    async def read_digest(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        request_id = f"g-{uuid.uuid4().hex[:12]}"
        # The fallback is no digest at all. An empty one would be read as "nothing
        # changed and nothing is unverified", which at shutdown nobody established.
        future = bridge.ask(request_id, DigestDelivered(request_id=request_id, digest=None))
        bridge.emit(DigestRead(node_id=me, request_id=request_id, at=time.monotonic()))
        answer = await future

        if not isinstance(answer, DigestDelivered) or answer.digest is None:
            return _text(f"The digest was NOT read. {_refusal_text(TaskRefusal.NOT_APPLIED)}")
        return _text(_bounded_digest_text(answer.digest))

    @tool(
        "declare_task",
        "Put a unit of work on the shared board for any agent to claim. "
        "depends_on names tasks that must finish first, and touches names the files "
        "the work will write -- the board adds a dependency for you when two tasks "
        "would write the same file.",
        _schema(
            {
                "task_id": {
                    "type": "string",
                    "description": "Omit it and the board generates one.",
                },
                "title": {"type": "string"},
                "detail": {
                    "type": "string",
                    "description": "The full specification. Omit it for a title-only task.",
                },
                "depends_on": {
                    "type": "array",
                    "description": "Task ids that must finish first. Omit it for none.",
                },
                "touches": {
                    "type": "array",
                    "description": (
                        "Paths this task will write, relative to the directory this "
                        "session was launched in, e.g. 'pptmstr/store.py'. Any "
                        "unfinished task on this board that writes one of them "
                        "becomes a dependency automatically. Give them relative to "
                        "that directory: an absolute path is not matched against a "
                        "relative one."
                    ),
                },
                "basis": _BASIS_PROPERTY,
                "property": {
                    "type": "string",
                    "description": (
                        "ONE sentence: the property this work may not break, "
                        "whatever else it trades away. It is shown under the title "
                        "on every board read, never cut, handed to whoever claims "
                        "the task, and is what a reviewer ranks findings against. "
                        f"At most {MAX_PROPERTY_CHARS} characters; a longer one is "
                        "refused. Omit it and the task states none."
                    ),
                },
            },
            required=("title",),
        ),
    )
    async def declare_task(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        task_id = str(args.get("task_id", "")).strip() or f"t-{uuid.uuid4().hex[:8]}"
        raw_deps = args.get("depends_on") or []
        deps = tuple(str(d).strip() for d in raw_deps if str(d).strip())
        raw_touches = args.get("touches") or []
        touches = tuple(str(p) for p in raw_touches)
        title = str(args.get("title", "")).strip()
        detail = str(args.get("detail", ""))
        if len(task_id) > MAX_ID_CHARS:
            # Named by its length and not echoed: the refusal is not a place to
            # print back what was too long to accept.
            return _text(
                f"The task is NOT on the board. task_id is {len(task_id)} characters "
                f"and the limit is {MAX_ID_CHARS}. It is a short name other calls "
                f"repeat; put the description in title."
            )
        try:
            basis = _stated_basis(args)
        except InvalidBasis as bad:
            return _text(f"Task {task_id} is NOT on the board. {bad}")
        held_to = normalised_property(args.get("property"))
        too_long = (
            _property_refusal(held_to)
            or _length_refusal(
                "title", title, MAX_LINE_CHARS, "It is one line; put the rest in detail."
            )
            or _length_refusal(
                "detail",
                detail,
                MAX_TEXT_CHARS,
                "Split the work into more than one task, or put the text in a file "
                "and name its path in detail.",
            )
            or _listed_refusal("depends_on", deps, MAX_ID_CHARS)
            or _listed_refusal("touches", touches, MAX_PATH_CHARS)
        )
        if too_long is not None:
            return _text(f"Task {task_id} is NOT on the board. {too_long}")
        # Stamped by the gate when the operator rewrote the call before approving
        # it. It says the arguments are not the declarer's and not which of them
        # changed, so the declarer's basis cannot be left over a specification
        # that may be the operator's words: the weaker thing to store is none.
        rewritten = bool(args.get(EDITED_KEY))
        unkept = (
            " The basis you gave was not kept: the operator edited this declaration "
            "before approving it, and a basis is your statement about the text as you "
            "wrote it. The board shows the specification as basis unstated."
            if rewritten and basis is not None
            else ""
        )
        request_id = f"d-{uuid.uuid4().hex[:12]}"
        future = _park(bridge, request_id)
        bridge.emit(
            TaskDeclared(
                task=Task(
                    id=task_id,
                    title=title,
                    detail=detail,
                    depends_on=deps,
                    touches=touches,
                    declared_at=time.monotonic(),
                    basis=None if rewritten else basis,
                    property=held_to,
                ),
                node_id=me,
                request_id=request_id,
            )
        )

        settled = await _outcome(future)
        if settled.refusal is TaskRefusal.DUPLICATE_ID and settled.held_off_fleet is not None:
            # The store said whose it is, as of the apply. No second read: a
            # session off the fleet is not the declarer's.
            said_why = _duplicate_id_text(False, settled.held_off_fleet)
            return _text(f"Task {task_id} is NOT on the board. {said_why}")
        if settled.refusal is TaskRefusal.DUPLICATE_ID:
            # A second read, on this path only: the store refuses an id any
            # session has and does not say whether it is one this board lists.
            on_board = await board_ids(me)
            mine = None if on_board is None else task_id in on_board
            return _text(f"Task {task_id} is NOT on the board. {_duplicate_id_text(mine)}")
        if settled.refusal is not None:
            return _text(f"Task {task_id} is NOT on the board. {_refusal_text(settled.refusal)}")
        said = [f"Task {task_id} is on the board."]
        if settled.auto_depends:
            said.append(_auto_depends_text(settled.auto_depends))
        if settled.already_behind:
            said.append(_already_behind_text(settled.already_behind))
        return _text(" ".join(said) + unkept)

    @tool(
        "amend_task",
        "Replace the specification of a task YOU declared and have never held. The "
        "old text is kept as an earlier version, and whoever holds the task sees the "
        "new one when it next reads the task; a holder that completes first has its "
        "result recorded against the version it was handed. Use it to correct "
        "what you asked for; to tell a holder something without changing what it is "
        "held to, post a concern instead. Only a task's declarer or the operator can "
        "amend it, and an agent that amends a task cannot afterwards claim it.",
        _schema(
            {
                "task_id": {"type": "string"},
                "detail": {
                    "type": "string",
                    "description": (
                        "The WHOLE specification as it should now read, not a patch: "
                        "it replaces the current one. Omit it, or leave it empty, and "
                        "the text stands with the basis its author gave it: that is "
                        "how to change only the property. An amendment has to give "
                        "detail, property or withdraw_property. "
                        f"At most {MAX_TEXT_CHARS} characters."
                    ),
                },
                "property": {
                    "type": "string",
                    "description": (
                        "ONE sentence replacing the property the work may not break. "
                        "Omit it, or leave it empty, and the stated property stands. "
                        f"At most {MAX_PROPERTY_CHARS} characters."
                    ),
                },
                "withdraw_property": {
                    **_FLAG_TYPE,
                    "description": (
                        "Set true ONLY to remove the task's property, leaving it "
                        "stating none. An empty property does not do this."
                    ),
                },
                "basis": _BASIS_PROPERTY,
            },
            required=("task_id",),
        ),
    )
    async def amend_task(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        task_id = str(args.get("task_id", "")).strip()
        refused = f"Task {_named(task_id)} was NOT amended."
        # Blank is absent, and absent is None: the text stands. The empty string
        # never reaches the intent, whose arm reads it as "replace the text with
        # nothing" -- the most a model filling in a string by habit could do to
        # a specification somebody is working to.
        sent = args.get("detail")
        detail = None if sent is None or not str(sent).strip() else str(sent)
        try:
            basis = _stated_basis(args)
        except InvalidBasis as bad:
            return _text(f"{refused} {bad}")
        # The two ways of touching the property do not share a spelling, for the
        # reason ``not_run`` and ``all_run`` do not: an empty string is what a
        # model writes into an optional field without meaning anything, and here
        # it would withdraw the one sentence the work is held to.
        stated = normalised_property(args.get("property"))
        withdraw = stated_flag(args.get("withdraw_property"))
        if withdraw is None:
            return _text(
                f"{refused} withdraw_property is {str(args.get('withdraw_property'))!r}, "
                f"which is neither true nor false. Set it true to remove the property, "
                f"or leave it out."
            )
        if withdraw and stated:
            return _text(
                f"{refused} You gave a property and set withdraw_property. Give the new "
                f"property, or withdraw it, not both."
            )
        if detail is None and not stated and not withdraw:
            return _text(
                f"{refused} You gave no detail, no property and did not set "
                f"withdraw_property, so there is nothing to replace. Give the whole "
                f"new text in detail, the new sentence in property, or both."
            )
        too_long = _property_refusal(stated) or _length_refusal(
            "detail",
            detail or "",
            MAX_TEXT_CHARS,
            "Put the text in a file and name its path in detail, or declare the "
            "rest as a task of its own.",
        )
        if too_long is not None:
            return _text(f"{refused} {too_long}")
        # As ``declare_task`` reads the same stamp: the arguments are no longer
        # all the amender's, so its basis is not left over them.
        rewritten = bool(args.get(EDITED_KEY))
        unkept = (
            " The basis you gave was not kept: the operator edited this amendment "
            "before approving it, and a basis is your statement about the text as you "
            "wrote it. The board shows the specification as basis unstated."
            if rewritten and basis is not None
            else ""
        )
        at = time.monotonic()
        request_id = f"a-{uuid.uuid4().hex[:12]}"
        future = _park(bridge, request_id)
        bridge.emit(
            TaskAmended(
                task_id=task_id,
                detail=detail,
                node_id=me,
                property="" if withdraw else (stated or None),
                request_id=request_id,
                at=at,
                basis=None if rewritten else basis,
            )
        )

        settled = await _outcome(future)
        if settled.refusal is not None:
            return _text(f"{refused} {_refusal_text(settled.refusal)}")
        # Read back, because the reply to a write says only that it landed and
        # the version it made is the thing the amender needs to be told.
        row = await board_row(me, task_id)
        return _text(_amended_text(task_id, row, at, basis_sent=basis is not None, unkept=unkept))

    @tool(
        "complete_task",
        "Mark a task you claimed as finished. Give result: what you did, what you "
        "ran, and what you did not run. It is stored on the task, where the next "
        "worker to build on it and the operator will read it -- neither was in your "
        "transcript, and what you return to the agent that started you reaches only "
        "that agent. A completion is never turned away for what it says: if the "
        "task's specification was replaced after you were handed it, your result is "
        "recorded against the version you were handed and the reply shows you the "
        "current one.",
        _schema(
            {
                "task_id": {"type": "string"},
                "result": {
                    "type": "string",
                    "description": (
                        "What was done, what was run, and what was not. Shown on the "
                        "board to every agent on it, unreviewed, as your account. "
                        f"At most {MAX_TEXT_CHARS} characters: a longer one is cut "
                        "there and says so, so put a long account in a file and "
                        "name its path. Omit it and the task is completed with no "
                        "result."
                    ),
                },
                "basis": _BASIS_PROPERTY,
                "not_run": {
                    "type": "string",
                    "description": (
                        "What this work did NOT execute: a script written and never "
                        "run, a path no test reaches, a platform or a live service "
                        "it was not tried against. Shown beside your result and "
                        "kept on a ledger of unverified work. Omit it and the task "
                        "is shown as 'not run: unstated' -- never as nothing."
                    ),
                },
                "all_run": {
                    **_FLAG_TYPE,
                    "description": (
                        "Set true ONLY to state that everything your result names "
                        "was executed by you and nothing was left unrun. It is "
                        "shown as your statement. An empty not_run does not say "
                        "this; only all_run does. Leave it out if you gave not_run."
                    ),
                },
            },
            required=("task_id",),
        ),
    )
    async def complete_task(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        task_id = str(args.get("task_id", "")).strip()
        # Stripped, so a result of only whitespace is the absence of one rather
        # than a heading on the board with nothing under it.
        result = str(args.get("result") or "").strip()
        # A basis that is none of the three is not a reason to refuse: the task
        # would stay claimed and the result would be nowhere. It is resolved the
        # way ``_stated_not_run`` resolves its own doubts, toward the weaker thing
        # to store, and said back.
        unread = ""
        try:
            basis = _stated_basis(args)
        except InvalidBasis as bad:
            basis, unread = None, str(bad)
        not_run, unkept = _stated_not_run(args, bool(result))
        # Too long is one more doubt resolved toward the weaker thing to store:
        # what fits, ending in a line that says it was cut (``_kept``).
        result, result_cut = _kept("result", result)
        listed_cut = ""
        if not_run:
            not_run, listed_cut = _kept("list of what was not run", not_run)
        request_id = f"f-{uuid.uuid4().hex[:12]}"
        future = _park(bridge, request_id)
        bridge.emit(
            TaskCompleted(
                node_id=me,
                task_id=task_id,
                at=time.monotonic(),
                request_id=request_id,
                result=result,
                basis=basis,
                not_run=not_run,
            )
        )

        settled = await _outcome(future)
        if settled.refusal is not None:
            return _text(f"Task {task_id} is NOT complete. {_refusal_text(settled.refusal)}")
        # The result lands in the arm that sets the state, so the one answer
        # covers both.
        said = [
            (
                f"Task {task_id} marked complete, and your result is on the board with it."
                if result
                else f"Task {task_id} marked complete."
            ),
            "Anything waiting on it is now claimable.",
        ]
        if unread:
            said.append(
                f"The basis you gave was not kept, and the board shows your result as "
                f"basis unstated: {unread} A finished task cannot be completed a second "
                f"time, so if the basis matters, say it in a concern that names the task."
            )
        elif basis is not None and not result:
            # Said rather than dropped in silence: the caller asserted something
            # and the board is not showing it.
            said.append(
                "The basis you gave was not kept: it says how you know what a result "
                "asserts, and you gave no result."
            )
        if unkept:
            said.append(unkept)
        said += [cut for cut in (result_cut, listed_cut) if cut]
        # Read back for the one thing the reply to a write does not carry: which
        # specification this result is recorded against. A specification replaced
        # under a holder turns no completion away -- the result has landed, at
        # the version the holder was handed -- and this is where it is told so.
        # A row that cannot be read back costs that paragraph and nothing else.
        row = await board_row(me, task_id)
        if row is not None and _behind(row, me):
            # Whether a concern can reach whoever replaced the text is the
            # session's answer about the name, and not something read off it.
            by = _one_line(row.superseded[-1].replaced_by) if row.superseded else ""
            reachable = bool(by) and session.resolve_role(by) is not None
            return _text(
                " ".join(said) + "\n\n" + _landed_behind_text(row, me[1] is None, reachable)
            )
        return _text(" ".join(said))

    @tool(
        "release_task",
        "Give a task you claimed back to the board without completing it. The lead "
        "of a session may also release a task whose holder is an agent of an earlier "
        "run of that session -- from before pptmstr was restarted or the session was "
        "resumed -- and is not running. read_board says so on such a row, and the "
        "release puts it back for another agent to claim; nobody else's claim can be "
        "released for them.",
        {"task_id": str},
    )
    async def release_task(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        task_id = str(args.get("task_id", "")).strip()
        request_id = f"x-{uuid.uuid4().hex[:12]}"
        future = _park(bridge, request_id)
        bridge.emit(
            TaskReleased(node_id=me, task_id=task_id, request_id=request_id, at=time.monotonic())
        )

        settled = await _outcome(future)
        if settled.refusal is None:
            return _text(f"Task {task_id} is back on the board.")
        said = f"Task {task_id} was NOT released. {_refusal_text(settled.refusal)}"
        if settled.refusal is TaskRefusal.NOT_YOURS and me[1] is None:
            # The lead has one release that is not its own, and a lead refused
            # here tried it on a holder it does not cover.
            said += (
                " As this session's lead you can release another agent's claim only "
                "when that agent is of an earlier run of this session and is not "
                f"running, and this holder is not. If its holder will not finish or "
                f"release it, {_OPERATOR_CLOSES}, {_CLOSED_IS_NOT_DONE}."
            )
        return _text(said)

    @tool(
        "raise_decision",
        "Put a decision in front of the operator as a record they answer in their "
        "own pane, and keep working: nothing is parked and nothing waits on it. Use "
        "it for what only the operator can decide -- a tradeoff the task did not "
        "settle, or a choice you already took and want ratified. For something "
        "another agent can answer, post a concern instead. The answer is handed to "
        "you, the agent that asked, when you call read_decisions with the id this "
        "returns, in the operator's words; your session's lead can read it too, and "
        "no other agent can. It is a question and not a document: each text is at "
        f"most {MAX_DECISION_TEXT_CHARS} characters, so put the long form in a file "
        f"and name its path. You may have {MAX_OPEN_DECISIONS_PER_ASKER} open at "
        "once, and none survives a restart of pptmstr.",
        _schema(
            {
                "question": {
                    "type": "string",
                    "description": "What you are asking the operator to decide.",
                },
                "standing": {
                    "type": "string",
                    "description": (
                        "'operators' if nobody has decided and what this blocks is "
                        "waiting for the answer; 'taken' if you already decided, "
                        "went on, and are asking the operator to ratify it."
                    ),
                },
                "if_unanswered": {
                    "type": "string",
                    "description": (
                        "What happens if no answer comes: what you will do, and "
                        "what stays undone. Required -- in a session nobody is "
                        "watching, this is what will happen."
                    ),
                },
                "blocks": {
                    # A string as well, for ``_FLAG_TYPE``'s reason: a model
                    # writes one id bare, and under ``array`` alone it would be
                    # refused in the validator's words before ``_stated_blocks``
                    # could take it or the store could say what is wrong with it.
                    "type": ["array", "string"],
                    "items": {"type": "string"},
                    "description": (
                        "The ids of the tasks on your board that cannot proceed "
                        "until this is answered, as read_board shows them. Ids "
                        f"only, at most {MAX_BLOCKED_TASKS}, each checked against "
                        "the board. Omit it or give [] and the operator is told "
                        "this blocks nothing on the board."
                    ),
                },
                "evidence": {
                    "type": "string",
                    "description": (
                        "What is known that bears on the decision, and how you know "
                        "it. Not what you think should be done: that is "
                        "recommendation, and the operator is shown the two apart."
                    ),
                },
                "recommendation": {
                    "type": "string",
                    "description": "What you would decide, and why. Omit it if you have none.",
                },
                "reversible": {
                    # ``null`` as well: it is what a model writes for "I do not
                    # know", which here is an answer and not a missing one.
                    "type": [*_FLAG_TYPE["type"], "null"],
                    "description": (
                        "true if the decision can be undone afterwards, false if it "
                        "cannot. Omit it if you do not know, and it is shown as "
                        "unstated."
                    ),
                },
            },
            required=("question", "standing", "if_unanswered"),
        ),
    )
    async def raise_decision(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        root = me[1] is None
        refused = "The decision was NOT raised."
        texts = {
            name: str(args.get(name) or "")
            for name in ("question", "if_unanswered", "evidence", "recommendation")
        }
        if not texts["question"].strip():
            return _text(
                f"{refused} question is what you are asking the operator to decide, "
                f"and you gave none."
            )
        if not texts["if_unanswered"].strip():
            # Refused and not defaulted: it is what the operator weighs a late
            # answer against, and in a session nobody is watching it is the
            # whole of what happens next.
            return _text(
                f"{refused} if_unanswered is what happens if no answer comes -- "
                f"what you will do and what stays undone -- and you gave none. Say "
                f"it even when it is 'I stop here'."
            )
        for name, text in texts.items():
            too_long = _length_refusal(
                name,
                text,
                MAX_DECISION_TEXT_CHARS,
                "A decision is a question and not a document: put the detail in a "
                "file and name the path.",
            )
            if too_long is not None:
                return _text(f"{refused} {too_long}")
        blocks, unlisted = _stated_blocks(args.get("blocks"))
        if unlisted is not None:
            return _text(f"{refused} {unlisted}")
        try:
            standing = _stated_standing(args.get("standing"))
        except InvalidStanding as bad:
            return _text(f"{refused} {bad}")
        reversible, unkept = _stated_reversible(args.get("reversible"))

        # Minted here and never read from the arguments: the store refuses an id
        # it already holds and cannot tell a minted one from one an agent chose,
        # so an id taken from a caller would let it aim at another decision.
        decision_id = f"dq-{uuid.uuid4().hex[:12]}"
        at = time.monotonic()
        request_id = f"q-{uuid.uuid4().hex[:12]}"
        future = bridge.ask(
            request_id, DecisionSettled(request_id=request_id, refusal=DecisionRefusal.NOT_APPLIED)
        )
        bridge.emit(
            DecisionRaised(
                node_id=me,
                decision=Decision(
                    id=decision_id,
                    asker=me,
                    question=texts["question"],
                    blocks=blocks,
                    if_unanswered=texts["if_unanswered"],
                    evidence=texts["evidence"],
                    recommendation=texts["recommendation"],
                    standing=standing,
                    reversible=reversible,
                    raised_at=at,
                ),
                request_id=request_id,
                at=at,
            )
        )
        settled = await future
        why = (
            settled.refusal if isinstance(settled, DecisionSettled) else DecisionRefusal.NOT_APPLIED
        )
        if why is DecisionRefusal.UNKNOWN_TASK:
            # The store says one is not there and not which. A second read, on
            # this path only, so the refusal can name it.
            on_board = await board_ids(me)
            strangers = () if on_board is None else tuple(b for b in blocks if b not in on_board)
            return _text(f"{refused} {_decision_refusal_text(why, decision_id, root, strangers)}")
        if why is not None:
            return _text(f"{refused} {_decision_refusal_text(why, decision_id, root)}")
        said = [
            f"Decision {decision_id} is in front of the operator "
            f"({_standing_said(standing, 'you')}; reversible: "
            f"{_REVERSIBLE_SAID[reversible]}; {_blocks_said(blocks)}). "
            f"Nothing is parked: keep "
            f'working, and call read_decisions(decision_id="{decision_id}") when you '
            f"reach what this blocks and again before you finish. It returns the "
            f"operator's answer every time you ask. "
            f"{WAKE_SAID if root else WAKE_SAID_SUBAGENT} withdraw_decision takes "
            f"the decision back if it stops mattering, and it is not kept across a "
            f"restart of pptmstr."
        ]
        if unkept:
            said.append(unkept)
        return _text(" ".join(said))

    @tool(
        "read_decisions",
        "Read decisions and what has become of each: still open, answered, "
        "dismissed by the operator, or withdrawn. Give decision_id for one decision "
        "with everything on it and the operator's answer, returned whole and in "
        "the operator's words every time you ask, not only the first. Omit it for "
        "a list, which says which have an answer and shows none. When the operator "
        "answers or dismisses one, pptmstr sends the session's lead a message "
        "naming it, read as a turn of its own once any turn in progress ends; a "
        "sub-agent is not woken, and a session that is closed or has failed is "
        "sent nothing. So call this when you reach the work a decision blocks, "
        "whether or not you were woken. A sub-agent is shown only its own. The "
        "session's lead is shown every decision raised in its session, each "
        "marked with who raised it, and can read and not withdraw another agent's.",
        _schema(
            {
                "decision_id": {
                    "type": "string",
                    "description": (
                        "The id raise_decision gave. Omit it for the list of "
                        "decisions you are shown."
                    ),
                }
            }
        ),
    )
    async def read_decisions(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        wanted = str(args.get("decision_id") or "").strip() or None
        now = time.monotonic()
        request_id = f"u-{uuid.uuid4().hex[:12]}"
        # The fallback is no answer at all, for ``read_digest``'s reason: an
        # empty list says this agent raised nothing, which nobody established.
        future = bridge.ask(request_id, DecisionsDelivered(request_id=request_id, decisions=None))
        bridge.emit(DecisionsRead(node_id=me, request_id=request_id, at=now, decision_id=wanted))
        answer = await future

        if not isinstance(answer, DecisionsDelivered) or answer.decisions is None:
            return _text(f"Your decisions were NOT read. {_refusal_text(TaskRefusal.NOT_APPLIED)}")
        return _text(
            _decisions_text(
                answer.decisions,
                wanted,
                now,
                me,
                lambda node: session.role_of(node) or "another agent",
            )
        )

    @tool(
        "withdraw_decision",
        "Take back a decision YOU raised that the operator has not answered: it "
        "leaves their queue and no answer will come. Use it when the question has "
        "stopped mattering. An answered or dismissed decision cannot be withdrawn.",
        _schema({"decision_id": {"type": "string"}}, required=("decision_id",)),
    )
    async def withdraw_decision(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        root = me[1] is None
        decision_id = str(args.get("decision_id") or "").strip()
        named = _named(decision_id[:MAX_ID_CHARS])
        request_id = f"w-{uuid.uuid4().hex[:12]}"
        future = bridge.ask(
            request_id, DecisionSettled(request_id=request_id, refusal=DecisionRefusal.NOT_APPLIED)
        )
        bridge.emit(
            DecisionWithdrawn(
                node_id=me, decision_id=decision_id, request_id=request_id, at=time.monotonic()
            )
        )
        settled = await future
        why = (
            settled.refusal if isinstance(settled, DecisionSettled) else DecisionRefusal.NOT_APPLIED
        )
        if why is None:
            return _text(
                f"Decision {named} is withdrawn: it has left the operator's queue and "
                f"no answer will come. read_decisions still lists it."
            )
        return _text(
            f"Decision {named} was NOT withdrawn. "
            f"{_decision_refusal_text(why, decision_id, root)}"
        )

    tools: list[Any] = [
        post_concern,
        read_inbox,
        read_board,
        read_task,
        read_digest,
        claim_task,
        declare_task,
        amend_task,
        complete_task,
        release_task,
        raise_decision,
        read_decisions,
        withdraw_decision,
    ]
    # The session's own answer and no default: a session object that cannot say
    # whether it is contained raises here and is offered nothing.
    if session.contained:
        # Imported here and not at the top: ``commandbus`` imports this module
        # when it loads.
        from . import commandbus

        def offered(spec: commandbus.Tool) -> Any:
            @tool(spec.name, spec.description, spec.schema)
            async def call(args: dict[str, Any]) -> dict[str, Any]:
                return _text(await spec.handler(session, bridge, _sender(args), args))

            return call

        tools += [offered(spec) for spec in commandbus.TOOLS]
    return create_sdk_mcp_server(SERVER_NAME, "1.0.0", tools)


# Every tool the gate must stamp. Kept beside the server rather than derived from
# it, because a tool added to the server and forgotten here would reach its handler
# unstamped and raise -- which is the right failure, but a list in one place is a
# cheaper way to not have it.
BUS_TOOLS = frozenset(
    qualified(name)
    for name in (
        "post_concern",
        "read_inbox",
        "read_board",
        "read_task",
        "read_digest",
        "claim_task",
        "declare_task",
        "amend_task",
        "complete_task",
        "release_task",
        "raise_decision",
        "read_decisions",
        "withdraw_decision",
    )
)

# The three tools only a contained session's server has, which the gate stamps
# for that session and for no other. Spelled here and not read off
# ``commandbus.TOOLS`` because that module imports this one when it loads;
# tests/test_bus.py holds the two spellings and the server's own list together.
COMMAND_TOOLS = frozenset(
    qualified(name) for name in ("request_command", "read_commands", "withdraw_command")
)


def offered_tools(contained: bool) -> frozenset[str]:
    """
    Every tool one session's server offers, and so every name its gate stamps.

    ``contained`` is ``AgentSession.contained``: whether the CLI process was
    started with containment, which is fixed for the session's life and is not
    its rung. ``build_server`` registers by the same fact, so the two cannot name
    different sessions.
    """
    return BUS_TOOLS | COMMAND_TOOLS if contained else BUS_TOOLS


# -- waking a session ----------------------------------------------------------------
#
# What ``AgentSession.wake`` sends a session when something it waits on arrives
# (``effects.SessionWoken``). The session reads it as a user message, so the
# first line says whose it is: a wake must never be taken for the operator.

WAKE_HEADING = "This message is from pptmstr, not from the operator. Waiting for you:"

# What a tool reply says about being woken, to the session's lead and to a
# sub-agent. Concerns between workers wake nobody, and neither does anything
# for a session that is closed or has failed.
WAKE_SAID = (
    "Nothing interrupts a turn in progress. When the operator acts on it, pptmstr "
    "sends this session a message of its own naming it (never the operator's "
    "words), read as a turn of its own once any turn in progress ends: if you end "
    "your turn to wait, that message wakes you. A closed or failed session is sent "
    "nothing."
)
WAKE_SAID_SUBAGENT = (
    "Nothing interrupts you, and you are not woken: when the operator acts on it, "
    "pptmstr sends your lead a message of its own naming it, so your lead learns "
    "of it even if you do not pass it on."
)


def wake_message(snap: Snapshot, wakes: Sequence[SessionWoken]) -> str:
    """
    One message for every wake a session is owed at once: the heading, then a
    line for each thing to read, in the order the store gave them, each said
    once however many times it was woken for.

    Short and factual, and each line names the call that returns what arrived,
    so the session spends its turn reading it and not asking where it is.
    """
    lines = [WAKE_HEADING]
    seen: set[tuple[WakeReason, str]] = set()
    for wake in wakes:
        key = (wake.reason, wake.subject_id)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {wake_line(snap, wake)}")
    return "\n".join(lines)


def wake_line(snap: Snapshot, wake: SessionWoken) -> str:
    """
    The line of a wake for one thing: what happened to which id, and the call
    that returns it. An id, and a role an agent's address names, are each one
    line (``_one_line``).
    """
    said = _one_line(wake.subject_id)
    match wake.reason:
        case WakeReason.DECISION_ANSWERED | WakeReason.DECISION_DISMISSED:
            how = "answered" if wake.reason is WakeReason.DECISION_ANSWERED else "dismissed"
            decision = snap.decisions.get(wake.subject_id)
            whose = _relayed(snap, decision.asker if decision is not None else None)
            return (
                f'decision {said}{whose} was {how}. read_decisions(decision_id="{said}") '
                "returns it."
            )
        case WakeReason.COMMAND_ENDED:
            command = snap.commands.get(wake.subject_id)
            whose = _relayed(snap, command.asker if command is not None else None)
            return (
                f'command {said}{whose} has ended. read_commands(command_id="{said}") '
                "returns how."
            )
        case WakeReason.CONCERN_TO_LEAD:
            sender = wake.sender
            who = "a worker" if sender is None else _one_line(_role(snap, sender))
            return f"{who} sent you a concern. read_inbox() returns it."
        case _:
            assert_never(wake.reason)


def _role(snap: Snapshot, node: NodeId) -> str:
    return role_name(snap, node, node[0])


def _relayed(snap: Snapshot, asker: NodeId | None) -> str:
    """
    For what a sub-agent asked, the words that say whose it was and that the
    lead is the one woken: the sub-agent has no turn of its own to wake.
    """
    if asker is None or asker[1] is None:
        return ""
    role = _one_line(_role(snap, asker))
    return f", which {role} asked (it is not woken; pass it on)"
