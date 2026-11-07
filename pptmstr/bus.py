"""
The in-process bus the agents talk over (§2.7).

An MCP server built with ``create_sdk_mcp_server``, one per session, registering
``TOOLS``: one ``replies.Tool`` record per tool, each naming a module-level
handler that is handed the session, its ``Bridge`` and the stamped sender. No
subprocess: the CLI reaches these handlers back through the same control channel
it uses for hooks.

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
``read_board``, ``read_task``, ``read_digest``, ``read_decisions``,
``why_blocked``, ``ready`` and ``claim_task``, which ask questions, and the
board writes and the two decision writes, which assert facts the reducer is
entitled to refuse. ``why_blocked``, ``ready`` and a dry-run ``declare_task``
are board reads whose answer is computed here, by ``board.graph``, from the
snapshot the read delivered; a dry run emits no write.

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
and words are ``commandbus``'s, which ``build_server`` is handed by its caller
and registers when ``session.contained`` says the CLI process was started with
containment. The bus imports nothing of ``commandbus``: both lay their replies
out with ``replies``. They ask
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
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import brief, ids
from .board import graph
from .board.digesttext import _bounded_digest_text
from .board.graph import is_open
from .board.graphtext import (
    _CUT_NOTE_ROOM,
    _dry_run_text,
    _loop_has_a_gate,
    _ready_text,
    _why_blocked_text,
)
from .board.rows import BoardConcern, BoardTask
from .board.text import (
    _CLOSED_IS_NOT_DONE,
    _NOT_ON_BOARD,
    _OPERATOR_CLOSES,
    _PART_NOT_RUN,
    _PART_RESULT,
    _SHOWN_NOTE_ROOM,
    _agent_notes,
    _already_behind_text,
    _amended_text,
    _auto_depends_text,
    _behind,
    _board_text,
    _claim_refusal_text,
    _declare_refusal_text,
    _edited_note,
    _escaped_dependency_text,
    _held_live_text,
    _landed_behind_text,
    _legend,
    _part_text,
    _refusal_text,
    _task_text,
    _unshown,
    _withdrawal_heading,
    _withdrawal_reason,
)
from .board.words import basis_label, incident_marker, property_label
from .bridge import Bridge
from .channels.records import EVERY_CHANNEL, PeerRefusal, PeerWithdrawRefusal, channel_refusal
from .channels.text import (
    _PEER_LEGEND,
    _cited_text,
    _lead_of,
    _peer_messages_text,
    _peer_refusal_text,
    _peer_wake_said,
    _withdraw_refusal_text,
)
from .concerntext import _operators_notice, _withdrawal_notice_lines
from .decisiontext import (
    _BLOCKS_ARE,
    _REVERSIBLE_SAID,
    _blocks_said,
    _decision_refusal_text,
    _decisions_text,
    _standing_said,
)
from .effects import (
    BoardDelivered,
    ClaimSettled,
    DecisionsDelivered,
    DecisionSettled,
    DigestDelivered,
    Effect,
    InboxDelivered,
    PeerInboxDelivered,
    PeerSettled,
    PeerWithdrawSettled,
    TaskWriteSettled,
    between_quotes,
)
from .intents import (
    BoardRead,
    ConcernPosted,
    ConcernWithdrawn,
    DecisionRaised,
    DecisionsRead,
    DecisionWithdrawn,
    DigestRead,
    InboxRead,
    PeerInboxRead,
    PeerSent,
    TaskAmended,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
    TaskVersionHanded,
    TaskWithdrawn,
)
from .model import (
    ALL_WRITERS,
    BEFORE_RESTART,
    MAX_BLOCKED_TASKS,
    MAX_DECISION_TEXT_CHARS,
    MAX_ID_CHARS,
    MAX_OPEN_DECISIONS_PER_ASKER,
    MAX_PROPERTY_CHARS,
    MAX_WITHDRAWAL_REASON_CHARS,
    Basis,
    ClaimRefusal,
    Concern,
    ConcernId,
    Decision,
    DecisionRefusal,
    NodeId,
    Provenance,
    Snapshot,
    Standing,
    Task,
    TaskId,
    TaskRefusal,
    TaskState,
    is_operator,
    is_withdrawal_notice,
    kept_provenance,
    normalised_property,
    property_overrun,
    stated_flag,
    stated_incident,
    stated_provenance,
    stored_as_sent,
    withdrawal_notice_id,
)
from .replies import (
    _MAX_BOARD_CHARS,
    _QUOTE_LEGEND,
    MAX_PATH_CHARS,
    WAKE_SAID,
    WAKE_SAID_SUBAGENT,
    Tool,
    _named,
    _one_line,
    _quoted,
    _schema,
)

SERVER_NAME = "pptmstr"

# -- what a call may carry ---------------------------------------------------------
#
# Every text an agent sends is bounded where it is accepted, because the board log
# refuses a record over ``journal.files.MAX_RECORD_BYTES`` (1,048,576) and the first
# record that does not land stops that session's log for the rest of the process
# (``boardsync``). An unbounded argument is therefore a way for one call to end the
# record of a whole session.
#
# The arithmetic. ``journal.codec.encode`` escapes to ASCII, and the most a character
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
# with ``journal.codec.encode`` and fails when one stops fitting.
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
# the task's structure, and for any one other text read alone (``board.text._part_text``).
# It is not what makes a read a showing -- the length of the whole reply is.
MAX_TEXT_CHARS = 24_000

# A title or a subject: the fields laid out on one line.
MAX_LINE_CHARS = 500

# A task id, wherever one is written rather than looked up, is at most
# ``model.MAX_ID_CHARS``: imported above, and defined there because a declaration's
# provenance is bounded by it on the parked row as well as here.

# One path in ``touches`` is at most ``replies.MAX_PATH_CHARS``: imported above,
# and defined there because ``commandbus`` lays a path out on a reply by it too.

# How many entries ``depends_on`` and ``touches`` may each have.
MAX_LISTED = 64

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

# The key the gate writes the instant an agent made the call into: the
# monotonic time the call reached the gate, which for a parked call is before
# the operator approved it. Same convention and reason as FROM_KEY.
#
# Read by the two tools that reach another team's lead over a channel name,
# ``send_to_peer`` and a ``raise_decision`` naming a channel. The name is
# resolved to a link when the store files the call, and on a reviewed rung
# that is after a wait in which the operator may close the link and make the
# name again to another session. The store refuses a link that went live after
# this instant (``model.Snapshot.linked_after``), so a call reaches only a link
# the agent could have been writing for.
CALLED_AT_KEY = "_called_at"


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


# Said when an operator's edit drops a declarer's charter mark. Whole, and not "for
# the same reason" as the basis sentence: that one is said only when a basis was
# given, and this one is said alone as often.
_CHARTER_UNKEPT = (
    " Your inside_charter mark was not kept: the operator edited this declaration "
    "before approving it, and the mark is your statement about the request as you "
    "sent it. The board shows it as unstated."
)


def _stated_provenance(args: Mapping[str, Any]) -> Provenance | None | str:
    """
    The provenance a declaration gave, None when it gave none, or the sentence
    it is refused with.

    **Shapes only** (``stated_provenance``, which the parked row reads with, so
    the two agree on every refusal): no channel is checked against a link --
    none exists to check against yet -- and ``re`` is not looked up, for the
    reason ``Concern.task_id`` is not.
    """
    try:
        return stated_provenance(args.get("channel"), args.get("re"), args.get("inside_charter"))
    except ValueError as bad:
        return str(bad)


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


def _called_at(args: dict[str, Any]) -> float:
    """
    The gate's stamp of when the call was made (``CALLED_AT_KEY``). Raised on
    as ``_sender`` is: a call that reaches a handler unstamped bypassed the
    gate, and guessing the instant would bind it to whatever link is live now.
    """
    raw = args.get(CALLED_AT_KEY)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise UnstampedCall(f"bus call carried no call time: {args!r}")
    return float(raw)


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


def _premises_text(session: Team) -> str:
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


# -- decisions -----------------------------------------------------------------------


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


@dataclasses.dataclass(frozen=True, slots=True)
class _PeerMessage:
    """What a ``send_to_peer`` call says, read and checked; the sender is the gate's."""

    channel: str
    subject: str
    body: str
    needs_answer: bool
    re: ConcernId | None
    quiet: bool


def _stated_peer_message(args: Mapping[str, Any]) -> _PeerMessage | str:
    """
    ``send_to_peer``'s arguments as the message they state, or the sentence the
    call is refused with.

    Everything here is a fact of the arguments alone; whether the channel is a
    live link this sender is on is the store's (``model.Snapshot.peer_route``).

    **``incident`` is refused whatever its value**, false included. An incident
    reports something going wrong inside a team to its own lead, and a peer is
    not that lead; taking ``incident=false`` would teach a sender the argument
    exists here.
    """
    if "incident" in args:
        return (
            "send_to_peer takes no incident: an incident reports something that went "
            "wrong inside your own team, to your own lead or the operator. Leave the "
            "argument out, and report a slip with post_concern(incident=true)."
        )
    channel = str(args.get("channel") or "").strip()
    if not channel:
        return "channel is the name of a link the operator made, and none was given."
    bad = channel_refusal(channel)
    if bad is not None:
        return bad
    flags: dict[str, bool] = {}
    for key in ("needs_answer", "quiet"):
        flag = stated_flag(args.get(key))
        if flag is None:
            return f"{key} is {str(args.get(key))[:32]!r}, which is neither true nor false."
        flags[key] = flag
    if flags["needs_answer"] and flags["quiet"]:
        return (
            "quiet and needs_answer cannot both be set: a message that needs an answer "
            "is one the other lead should be told about. Drop quiet."
        )
    re_raw = args.get("re")
    if re_raw is not None and not isinstance(re_raw, str):
        return "re is not text. It is a message id: give it as a string, or omit it."
    answers = (re_raw or "").strip() or None
    if answers is not None and not ids.is_concern_id(answers):
        return (
            "re is not a message id. A peer message's id is the one the bus gave it: "
            "c- and 12 hex digits, as read_peer_messages shows it."
        )
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    too_long = _length_refusal(
        "subject", subject, MAX_LINE_CHARS, "It is one line; put the rest in body."
    ) or _length_refusal("body", body, MAX_TEXT_CHARS)
    if too_long is not None:
        return too_long
    return _PeerMessage(channel, subject, body, flags["needs_answer"], answers, flags["quiet"])


# -- the tools -----------------------------------------------------------------------


class Team(Protocol):
    """
    As much of ``driver.AgentSession`` as the bus reads: the bridge its handlers
    emit on and park against, whether the CLI process was started with
    containment, the brief's directory, and the session's answers about the roles
    it spawned.

    Narrow on purpose, as ``commandbus.Roles`` is, so a test can hand the server a
    session that is only these. A ``Team`` is a ``Roles``, which is what lets one
    session be handed to both tables (``replies.Tool``).
    """

    @property
    def bridge(self) -> Bridge: ...

    @property
    def contained(self) -> bool: ...

    @property
    def brief(self) -> str | None: ...

    def resolve_role(self, name: str) -> NodeId | None: ...

    def role_status(self, name: str) -> str: ...

    def role_of(self, node: NodeId) -> str | None: ...


# The five reads a handler makes of the board beside its own call. Each takes the
# bridge first because a handler is handed it rather than closing over it.


async def board_row(bridge: Bridge, me: NodeId, task_id: TaskId) -> BoardTask | None:
    """
    One task as ``me``'s board holds it now, or None when it is not there or
    the store never answered. A read of its own, for a handler that has to know
    something about a task before or after it writes.
    """
    request_id = ids.request()
    future = bridge.ask(request_id, BoardDelivered(request_id=request_id, tasks=()))
    bridge.emit(BoardRead(node_id=me, request_id=request_id))
    answer = await future
    if not isinstance(answer, BoardDelivered):
        return None
    return next((t for t in answer.tasks if t.id == task_id), None)


async def withdrawn_row(
    bridge: Bridge, me: NodeId, task_id: TaskId
) -> tuple[BoardTask | None, BoardConcern | None]:
    """
    ``task_id`` as ``me``'s board holds it now, and the notice its withdrawal
    filed to the agent that held it (``model.withdrawal_notice_id``), or None
    for either that is not there or when the store never answered.
    """
    request_id = ids.request()
    future = bridge.ask(request_id, BoardDelivered(request_id=request_id, tasks=()))
    bridge.emit(BoardRead(node_id=me, request_id=request_id))
    answer = await future
    if not isinstance(answer, BoardDelivered):
        return None, None
    wanted = withdrawal_notice_id(task_id)
    return (
        next((t for t in answer.tasks if t.id == task_id), None),
        next((c for c in answer.concerns if c.id == wanted), None),
    )


async def withdrawn_because(bridge: Bridge, me: NodeId, task_id: TaskId) -> str:
    """
    The declarer's reason for withdrawing ``task_id``, laid out to follow
    ``board.text._WITHDRAWN_UNDER_YOU``, or a pointer to ``read_task`` when the row
    cannot be read back. A read of its own because the refusal is an enum and
    the reason is on the row.
    """
    row = await board_row(bridge, me, task_id)
    if row is None or not row.withdrawn:
        return " read_task with this id shows the reason it was withdrawn with."
    reason = _withdrawal_reason(row)
    lines, _ = _quoted(reason, len(reason), "", None)
    heading = _withdrawal_heading(row.withdrawn_by_lead, row.withdrawal_edited)
    return "\n".join(["", "", _QUOTE_LEGEND, heading[0].upper() + heading[1:], *lines])


async def board_ids(bridge: Bridge, me: NodeId) -> frozenset[TaskId] | None:
    """
    The id of every task on ``me``'s board now, or None when the store never
    answered -- which is not an empty board.
    """
    request_id = ids.request()
    unanswered = BoardDelivered(request_id=request_id, tasks=())
    future = bridge.ask(request_id, unanswered)
    bridge.emit(BoardRead(node_id=me, request_id=request_id))
    answer = await future
    if answer is unanswered or not isinstance(answer, BoardDelivered):
        return None
    return frozenset(t.id for t in answer.tasks)


async def board_world(bridge: Bridge, me: NodeId) -> tuple[BoardDelivered, Snapshot] | None:
    """
    ``me``'s board and the snapshot it was projected from, in one read, or
    None when the store never answered.

    The snapshot crosses from the UI thread to this one, and the handler
    never reaches the ``Store``. That is safe for the reason
    ``BoardDelivered`` gives, which is narrower than "a frozen value": its
    maps are fresh per apply and never written, and the queries read only
    ``tasks`` and ``nodes``, frozen records, and none of the mutable things an
    ``AgentRecord`` reaches (its ``Transcript``,
    ``PendingApproval.raw_args``). Every graph query a reply makes is
    asked of this one snapshot, so a reply never mixes two moments of the
    board.
    """
    request_id = ids.request()
    future = _park(bridge, request_id)
    bridge.emit(BoardRead(node_id=me, request_id=request_id))
    answer = await future
    if not isinstance(answer, BoardDelivered) or answer.snapshot is None:
        return None
    return answer, answer.snapshot


async def post_concern(session: Team, bridge: Bridge, sender: NodeId, args: dict[str, Any]) -> str:
    to = str(args.get("to", "")).strip()
    recipient = session.resolve_role(to)
    if recipient is None:
        # Named rather than silently dropped. A bare failure is what sent the
        # wake-path probe's worker into retrying the same wrong name; the
        # session distinguishes "not started yet" from "no such role", which are
        # different mistakes with different fixes.
        return session.role_status(to)
    try:
        basis = _stated_basis(args)
    except InvalidBasis as bad:
        return f"Concern NOT sent to {to}. {bad}"
    incident = stated_incident(args.get("incident"))
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    too_long = _length_refusal(
        "subject", subject, MAX_LINE_CHARS, "It is one line; put the rest in body."
    ) or _length_refusal("body", body, MAX_TEXT_CHARS)
    if too_long is not None:
        return f"Concern NOT sent to {to}. {too_long}"
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
        # The shape ``ids.is_concern_id`` reads a declaration's ``re`` by.
        id=ids.concern(),
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
        # Mail inside the sender's team: ``to`` names a role, never a channel.
        channel=None,
    )
    bridge.emit(ConcernPosted(sender, concern))
    if incident:
        # Said back because the flag is read generously (``stated_incident``),
        # so a sender that set it by accident finds out here.
        return f"Concern delivered to {to}, marked as an incident.{unlinked}"
    return f"Concern delivered to {to}.{unlinked}"


POST_CONCERN = Tool(
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
    post_concern,
)


async def read_inbox(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    request_id = ids.request()
    # Registered before the intent is emitted. Reversed, the UI thread could
    # apply and answer before the future exists, and this agent would wait
    # forever on a reply that had nowhere to land.
    future = bridge.ask(request_id, InboxDelivered(request_id=request_id, concerns=()))
    bridge.emit(InboxRead(node_id=me, request_id=request_id, at=time.monotonic()))
    answer = await future

    if not isinstance(answer, InboxDelivered) or not answer.concerns:
        return "Your inbox is empty."
    closes = any(is_operator(c.sender) and c.body.strip() for c in answer.concerns)
    lines = [f"{len(answer.concerns)} concern(s). {_legend(closes)}"]
    for c in answer.concerns:
        if is_operator(c.sender):
            lines += ["", *_operators_notice(c)]
            continue
        who = _one_line(session.role_of(c.sender) or "another agent")
        if is_withdrawal_notice(c.id):
            lines += ["", *_withdrawal_notice_lines(c, who)]
            continue
        lines += [
            "",
            f"[from {who}, {basis_label(c.basis)}{_edited_note(c.edited)}] "
            f"{incident_marker(c.incident)}{between_quotes(c.subject) if c.subject else ''}",
            *_quoted(c.body, len(c.body), "")[0],
        ]
    return "\n".join(lines)


READ_INBOX = Tool(
    "read_inbox",
    "Read concerns other agents have sent you. Returns everything waiting and "
    "marks it read. A lead is woken by pptmstr when one of its own workers "
    "sends it a concern; nothing else wakes you for one, so call this when you "
    "finish a piece of work as well.",
    {},
    read_inbox,
)


async def send_to_peer(session: Team, bridge: Bridge, sender: NodeId, args: dict[str, Any]) -> str:
    refused = "Message NOT sent."
    stated = _stated_peer_message(args)
    if isinstance(stated, str):
        return f"{refused} {stated}"
    if sender[1] is not None:
        return f"{refused} {_peer_refusal_text(PeerRefusal.NOT_A_LEAD, stated.channel)}"
    # Minted here in the shape ``ids.is_concern_id`` reads, so a peer's
    # declaration can name this message in its ``re``.
    concern_id = ids.concern()
    request_id = ids.request()
    future = bridge.ask(
        request_id, PeerSettled(request_id=request_id, refusal=PeerRefusal.NOT_APPLIED)
    )
    bridge.emit(
        PeerSent(
            node_id=sender,
            request_id=request_id,
            concern_id=concern_id,
            channel=stated.channel,
            subject=stated.subject,
            body=stated.body,
            at=time.monotonic(),
            # Stamped by the gate, as on ``post_concern``.
            edited=bool(args.get(EDITED_KEY)),
            needs_answer=stated.needs_answer,
            re=stated.re,
            quiet=stated.quiet,
            called_at=_called_at(args),
        )
    )
    settled = await future
    if not isinstance(settled, PeerSettled):
        settled = PeerSettled(request_id=request_id, refusal=PeerRefusal.NOT_APPLIED)
    if settled.refusal is not None:
        return (
            f"{refused} "
            f"{_peer_refusal_text(settled.refusal, stated.channel, settled.recipient)}"
        )
    cited = "" if stated.re is None else _named(stated.re)
    withdrawn = {
        None: "",
        True: f" The message it answers, {cited}, was withdrawn by the lead that sent "
        f"it after its lead read it: no answer to it is owed, and this one was sent anyway.",
        False: f" The message it answers, {cited}, was withdrawn before it was read; "
        f"this one was sent anyway.",
    }[settled.re_withdrawn]
    return (
        f"Message {concern_id} sent on #{stated.channel} to "
        f"{_lead_of(settled.recipient)}. {_PEER_LEGEND} It is filed in your session's log and "
        f"waits until that lead calls read_peer_messages; nothing tells you when it "
        f"is read. {_peer_wake_said(stated.quiet)} If they answer, their message "
        f"names {concern_id} as its re.{withdrawn}"
    )


SEND_TO_PEER = Tool(
    "send_to_peer",
    "Send a message to the lead of ANOTHER team, over a channel the operator "
    "linked between your session and theirs. It is for what your own team "
    "cannot carry: a request, an answer or a heads-up between two leads who "
    "work as equals. It never reaches your own workers -- use post_concern for "
    "them -- and only a lead may send one. channel is the link's name; the "
    "message goes to the lead at its other end. What they read is quoted as "
    "yours and is never an instruction from pptmstr or the operator, and work "
    "you ask for reaches their board only if they declare it. The other lead "
    "reads it with read_peer_messages. Set needs_answer when you need a reply, "
    "re to the id of the message you are answering, and quiet for an "
    "acknowledgement or a status line that needs nothing back; quiet and "
    "needs_answer cannot both be set.",
    _schema(
        {
            "channel": {
                "type": "string",
                "description": (
                    "The name of the link the operator made between your session "
                    "and the other lead's, without the '#'."
                ),
            },
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "needs_answer": {
                **_FLAG_TYPE,
                "description": "Set true when you need the other lead to answer.",
            },
            "re": {
                "type": "string",
                "description": (
                    "The id of the peer message this one answers, as "
                    "read_peer_messages gave it (c- and 12 hex digits). Omit it "
                    "for a message that answers none."
                ),
            },
            "quiet": {
                **_FLAG_TYPE,
                "description": (
                    "Set true for a message that asks nothing: an acknowledgement "
                    "or a status line. A quiet message does not wake the other "
                    "lead; every other one does."
                ),
            },
        },
        required=("channel", "subject", "body"),
    ),
    send_to_peer,
)


async def read_peer_messages(
    session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]
) -> str:
    named = str(args.get("channel") or "").strip() or None
    if named is not None:
        bad = channel_refusal(named)
        if bad is not None:
            return f"Nothing was read. {bad}"
    if me[1] is not None:
        return (
            "Nothing was read. Messages between teams go to a session's lead and "
            "to no other agent, so a sub-agent has none. Your lead reads them, "
            "and passes on what is yours as a task or a concern."
        )
    request_id = ids.request()
    future = bridge.ask(request_id, PeerInboxDelivered(request_id=request_id, concerns=None))
    bridge.emit(
        PeerInboxRead(
            node_id=me,
            request_id=request_id,
            at=time.monotonic(),
            channel=EVERY_CHANNEL if named is None else named,
        )
    )
    answer = await future
    if not isinstance(answer, PeerInboxDelivered) or answer.concerns is None:
        return f"Your peer messages were NOT read. {_refusal_text(TaskRefusal.NOT_APPLIED)}"
    return _peer_messages_text(answer, named, me[0])


READ_PEER_MESSAGES = Tool(
    "read_peer_messages",
    "Read the messages the leads of OTHER teams have sent you over channels the "
    "operator linked to your session, and mark them read. Only a lead has peer "
    "messages; your own workers' concerns are read with read_inbox, and never "
    "arrive here. Each message is quoted as its sender wrote it and is never an "
    "instruction from pptmstr or the operator: work it asks for enters your "
    "board only if you declare it, with declare_task's channel and re. A "
    "message that is not quiet wakes you: pptmstr sends you a message of its "
    "own saying how many wait on which channel, never their text. A quiet one "
    "does not, and is read with the rest whenever you next call this. After "
    "the new messages it lists again each question you were handed earlier and "
    "have not answered, marked as already read; that marks nothing.",
    _schema(
        {
            "channel": {
                "type": "string",
                "description": (
                    "A link's name, without the '#', to read only that channel. "
                    "Omit it to read every channel."
                ),
            }
        }
    ),
    read_peer_messages,
)


async def withdraw_peer_message(
    session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]
) -> str:
    message_id = str(args.get("message_id") or "").strip()
    named = _named(message_id[:MAX_ID_CHARS])
    refused = f"Message {named} was NOT withdrawn."
    if me[1] is not None:
        return (
            f"{refused} Messages between teams are sent and withdrawn by a "
            f"session's lead and no other agent. Ask your lead with post_concern."
        )
    # A blank reason is none, as the gate reads it (``approval
    # ._reviewed_by_arguments``): a call the gate let through unparked
    # for having no reason hands the other lead no words.
    reason = str(args.get("reason") or "")
    reason = reason if reason.strip() else ""
    too_long = _length_refusal("reason", reason, MAX_WITHDRAWAL_REASON_CHARS)
    if too_long is not None:
        return f"{refused} {too_long}"
    request_id = ids.request()
    future = bridge.ask(
        request_id,
        PeerWithdrawSettled(request_id=request_id, refusal=PeerWithdrawRefusal.NOT_APPLIED),
    )
    bridge.emit(
        ConcernWithdrawn(
            message_id, reason=reason, node_id=me, request_id=request_id, peer_only=True
        )
    )
    settled = await future
    if not isinstance(settled, PeerWithdrawSettled):
        settled = PeerWithdrawSettled(
            request_id=request_id, refusal=PeerWithdrawRefusal.NOT_APPLIED
        )
    if settled.refusal is not None:
        return f"{refused} {_withdraw_refusal_text(settled.refusal, named)}"
    said = [
        f"Message {named} on #{_named(settled.channel or '')} to "
        f"{_lead_of(settled.recipient)} is withdrawn. {_PEER_LEGEND}"
    ]
    if settled.read:
        said.append(
            "That lead had already read it: its next read_peer_messages says you "
            f"withdrew it, {'with your reason' if reason else 'and that you gave no reason'}"
            ", and nothing wakes it for that. It owes no answer to it."
        )
    else:
        said.append("That lead had not read it, and it never arrives.")
    if settled.answers is not None and settled.cited is not None:
        said.append(_cited_text(settled.answers, settled.cited))
    return " ".join(said)


WITHDRAW_PEER_MESSAGE = Tool(
    "withdraw_peer_message",
    "Withdraw a message YOU sent to another team's lead with send_to_peer. If "
    "that lead has not read it, it never arrives. If it has, its next "
    "read_peer_messages says you withdrew it, with your reason quoted as yours "
    "if you gave one, and a question you withdraw is no longer owed an answer; "
    "it is not woken for it. A reason is text that lead reads, so a withdrawal "
    "that gives one waits for the operator's approval wherever send_to_peer "
    "does; one without a reason never waits. Only a message's sender can "
    "withdraw it, only over a link your session is still on, and not once it "
    "has been answered.",
    _schema(
        {
            "message_id": {
                "type": "string",
                "description": ("The id send_to_peer gave the message (c- and 12 hex digits)."),
            },
            "reason": {
                "type": "string",
                "description": (
                    "Optional: why you are withdrawing it, quoted to the other "
                    "lead if it had read the message. Giving one makes the call "
                    "wait for the operator where send_to_peer would. "
                    f"At most {MAX_WITHDRAWAL_REASON_CHARS} characters."
                ),
            },
        },
        required=("message_id",),
    ),
    withdraw_peer_message,
)


async def claim_task(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    wanted = str(args.get("task_id", "")).strip() or None
    request_id = ids.request()
    future = bridge.ask(request_id, ClaimSettled(request_id=request_id, task=None))
    bridge.emit(TaskClaimRequested(node_id=me, request_id=request_id, task_id=wanted))
    answer = await future

    if not isinstance(answer, ClaimSettled) or answer.task is None:
        why = answer.refusal if isinstance(answer, ClaimSettled) else None
        if why is ClaimRefusal.HELD and wanted is not None:
            # A second read, on this path only: the refusal says the task is
            # held and not whether its holder is one a concern can reach.
            row = await board_row(bridge, me, wanted)
            return _claim_refusal_text(why, wanted, row, root=me[1] is None)
        if why is not None:
            return _claim_refusal_text(why, wanted)
        # No reason beside no task is the store never having seen the claim
        # (``effects.ClaimSettled``), so nothing here knows why.
        if wanted is not None:
            # It lists the causes and names the read that says which,
            # and does not state the one that is most often true.
            return (
                f"Task {_named(wanted)} was NOT claimed. Either it is not on your "
                f"board, or it is held or finished, or it still waits on an "
                f"unfinished dependency, or you replaced its specification -- an "
                f"agent that amended a task may not then claim it. "
                f"read_task shows which; nothing changed."
            )
        return (
            "Nothing claimable right now -- the board is empty or the remaining "
            "work is still blocked by unfinished dependencies."
        )
    won = answer.task
    said = [f"You now own task {_named(won.id)}: {between_quotes(won.title)}"]
    if won.property:
        # Ahead of the specification, where the board puts it: the claimer is
        # the reader the sentence is for.
        said.append(property_label(between_quotes(won.property)))
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
    return "\n".join(said) + _premises_text(session)


CLAIM_TASK = Tool(
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
    claim_task,
)


async def read_board(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    open_only = stated_flag(args.get("open_only"))
    if open_only is None:
        # Refused rather than read as either: an unreadable flag taken as
        # false hands back the whole board to a caller that asked for less,
        # and taken as true hides finished tasks from one that did not.
        return (
            "The board was NOT read: open_only is "
            f"{between_quotes(str(args.get('open_only')))}, "
            f"and it takes true or false."
        )
    request_id = ids.request()
    future = bridge.ask(request_id, BoardDelivered(request_id=request_id, tasks=()))
    bridge.emit(BoardRead(node_id=me, request_id=request_id))
    answer = await future

    if not isinstance(answer, BoardDelivered) or not answer.tasks:
        # An empty board is an ordinary answer and a common one early in a
        # session. Said plainly, and distinguished from the claim tool's reply:
        # "nothing claimable" and "nothing declared" send an agent to different
        # places, and only one of them means the lead has not planned yet.
        return "Your board has no tasks on it yet."
    # Every concern on the board, unfiltered. Which of them reach a row is
    # already decided by `board._open_concerns_by_task`, which skips the ones
    # naming no task -- a second filter here would be the same rule in two
    # places, free to drift from the one the pane reads.
    by_id = _agent_notes(answer.concerns)
    if not open_only:
        return _board_text(answer.tasks, by_id, root=me[1] is None)
    rows = [row for row in answer.tasks if is_open(row)]
    finished = len(answer.tasks) - len(rows)
    if not rows:
        return (
            f"No task on your board is open: all {finished} of them are finished -- "
            f"completed, closed or withdrawn. read_board() lists them."
        )
    return _board_text(rows, by_id, root=me[1] is None, finished=finished)


READ_BOARD = Tool(
    "read_board",
    "See every task on your team's board: what exists, who holds it, what each "
    "one is waiting for, and its full specification. Read it before declaring "
    "work or reporting a conflict, and re-read it to check the spec of a task "
    "you hold -- the board is authoritative and your copy of it is not. "
    "open_only=true leaves out every finished task -- completed, closed or "
    "withdrawn -- for a board whose history has outgrown the reply.",
    _schema(
        {
            "open_only": {
                **_FLAG_TYPE,
                "description": (
                    "true to list only the tasks still open: pending or held. "
                    "Omit it for every task, finished ones included."
                ),
            }
        }
    ),
    read_board,
)


async def read_task(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    task_id = str(args.get("task_id", "")).strip()
    request_id = ids.request()
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
        return f"Task {task_id} was NOT read. {_refusal_text(TaskRefusal.NOT_APPLIED)}"
    row = next((t for t in answer.tasks if t.id == task_id), None)
    if row is None:
        # Two mistakes, named apart: an id that is misspelled, and an id that is
        # right and belongs to another session. The recovery for both is the
        # listing, and it is said rather than implied.
        return f"No task with the id {task_id!r} is on your board. {_NOT_ON_BOARD}"
    by_id = _agent_notes(answer.concerns)
    part = str(args.get("part") or "").strip()
    if part:
        # One text, and never a showing: the specification a holder is
        # recorded against is not in this reply.
        return _part_text(row, by_id, part)
    text = _task_text(row, by_id, root=me[1] is None, ceiling=_MAX_BOARD_CHARS - _SHOWN_NOTE_ROOM)
    if not _unshown(row, me):
        return text
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
        return text + shown
    # ``board.text._task_text`` keeps the specification and the task's structure
    # whatever they cost, so this is a specification only the operator's
    # hand can have written, or a task whose lists and note headings alone
    # crowd a reply.
    return (
        text + f"\n\nYou hold this task and had not been shown version {row.version} of "
        f"its specification, which is the one above. This reply is "
        f"{len(text)} characters with that specification whole, longer than the "
        f"{_MAX_BOARD_CHARS} a reply is known to be readable at, so you are NOT "
        f"recorded as having been shown it: your result will be recorded as "
        f"written against the version you were handed."
    )


READ_TASK = Tool(
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
    read_task,
)


async def read_digest(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    request_id = ids.request()
    # The fallback is no digest at all. An empty one would be read as "nothing
    # changed and nothing is unverified", which at shutdown nobody established.
    future = bridge.ask(request_id, DigestDelivered(request_id=request_id, digest=None))
    bridge.emit(DigestRead(node_id=me, request_id=request_id, at=time.monotonic()))
    answer = await future

    if not isinstance(answer, DigestDelivered) or answer.digest is None:
        return f"The digest was NOT read. {_refusal_text(TaskRefusal.NOT_APPLIED)}"
    return _bounded_digest_text(answer.digest)


READ_DIGEST = Tool(
    "read_digest",
    "Read the digest of your session's round: what changed since you last read "
    "one, what is waiting on the operator, every statement on the board nobody "
    "said was run, and every concern marked as an incident. It is the report a "
    "lead makes to the operator at the END of a round, in place of relaying "
    "each worker's return as it arrives. Reading it moves your mark, so the "
    "next one starts where this one stopped.",
    {},
    read_digest,
)


async def why_blocked(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    task_id = str(args.get("task_id", "")).strip()
    if not task_id:
        return "Nothing was asked: give the task_id of a task on your board."
    world = await board_world(bridge, me)
    if world is None:
        return f"The board was NOT read. {_refusal_text(TaskRefusal.NOT_APPLIED)}"
    _, snap = world
    return _why_blocked_text(graph.why_blocked(snap, me[0], task_id), snap)


WHY_BLOCKED = Tool(
    "why_blocked",
    "Say why one task on your board cannot be claimed: every unfinished task "
    "at the end of the chain it waits on, each once, with the path to it, its "
    "state and its holder, and the files each wait shares. One call in place "
    "of reading the whole board and tracing depends_on by hand, and the "
    "answer names who to ask: a held root's holder, an unheld root's need "
    "for a claimer. Open to every agent on the board.",
    _schema(
        {
            "task_id": {
                "type": "string",
                "description": "The id read_board lists the task under.",
            }
        },
        required=("task_id",),
    ),
    why_blocked,
)


async def ready(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    world = await board_world(bridge, me)
    if world is None:
        return f"The board was NOT read. {_refusal_text(TaskRefusal.NOT_APPLIED)}"
    board, snap = world
    return _ready_text(graph.ready(snap, me[0]), board.tasks)


READY = Tool(
    "ready",
    "List the tasks on your board that can be claimed now, the one the most "
    "pending tasks wait on (directly or through other tasks) first, each with "
    "that count. "
    "It is the critical path read off the board: use it to choose what to "
    "start, or how many workers to start, instead of working it out from "
    "read_board.",
    {},
    ready,
)


async def declare_task(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    dry_run = stated_flag(args.get("dry_run"))
    if dry_run is None:
        # Refused before anything else: read as false, a dry run the caller
        # meant would put a task on the board.
        return (
            f"Nothing was declared: dry_run is {between_quotes(str(args.get('dry_run')))}, and "
            f"it takes true or false."
        )
    task_id = str(args.get("task_id", "")).strip() or ids.task()
    raw_deps = args.get("depends_on") or []
    deps = tuple(str(d).strip() for d in raw_deps if str(d).strip())
    raw_touches = args.get("touches") or []
    touches = tuple(str(p) for p in raw_touches)
    title = str(args.get("title", "")).strip()
    detail = str(args.get("detail", ""))
    if len(task_id) > MAX_ID_CHARS:
        # Named by its length and not echoed: the refusal is not a place to
        # print back what was too long to accept.
        return (
            f"The task is NOT on the board. task_id is {len(task_id)} characters "
            f"and the limit is {MAX_ID_CHARS}. It is a short name other calls "
            f"repeat; put the description in title."
        )
    if not stored_as_sent(task_id):
        # Before the dry run branches off, so the two answer it in one reply,
        # and before anything is emitted. The store refuses it too, for every
        # other emitter. Not echoed: the id is what cannot be shown as sent.
        return f"The task is NOT on the board. {_refusal_text(TaskRefusal.ESCAPED_ID)}"
    escaped_deps = [n for n, d in enumerate(raw_deps, 1) if not stored_as_sent(str(d).strip())]
    if escaped_deps:
        # The same refusal for an entry of depends_on, at the same point and
        # for the same reasons: such an entry names a task that can never
        # be declared, so the store would accept a task that waits forever.
        return f"The task is NOT on the board. {_escaped_dependency_text(escaped_deps)}"
    try:
        basis = _stated_basis(args)
    except InvalidBasis as bad:
        return f"Task {task_id} is NOT on the board. {bad}"
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
        return f"Task {task_id} is NOT on the board. {too_long}"
    provenance = _stated_provenance(args)
    if isinstance(provenance, str):
        return f"Task {task_id} is NOT on the board. {provenance}"
    # Stamped by the gate when the operator rewrote the call before approving
    # it. It says the arguments are not the declarer's and not which of them
    # changed, so the declarer's basis cannot be left over a specification
    # that may be the operator's words: the weaker thing to store is none.
    # The charter mark is the declarer's statement in the same way, and goes
    # the same way; the channel and the message stay, being what the
    # operator approved whoever wrote them (``kept_provenance``, the rule the
    # parked call's warning predicts this by).
    rewritten = bool(args.get(EDITED_KEY))
    unkept = (
        " The basis you gave was not kept: the operator edited this declaration "
        "before approving it, and a basis is your statement about the text as you "
        "wrote it. The board shows the specification as basis unstated."
        if rewritten and basis is not None
        else ""
    )
    kept = kept_provenance(provenance, rewritten)
    if kept != provenance:
        provenance = kept
        unkept += _CHARTER_UNKEPT
    task = Task(
        id=task_id,
        title=title,
        detail=detail,
        depends_on=deps,
        touches=touches,
        declared_at=time.monotonic(),
        basis=None if rewritten else basis,
        property=held_to,
        provenance=provenance,
    )
    if dry_run:
        # The same Task the real declaration would emit, applied to one
        # snapshot through the real arm and discarded
        # (``board.graph.dry_run_declare``). Nothing is emitted but the read.
        world = await board_world(bridge, me)
        if world is None:
            return f"The dry run was NOT made. {_refusal_text(TaskRefusal.NOT_APPLIED)}"
        board, snap = world
        dry = graph.dry_run_declare(snap, me[0], task, me, time.monotonic())
        mine = any(row.id == task_id for row in board.tasks)
        # What an operator's edit dropped is said as the real reply says it:
        # the rehearsed task is the one without it.
        rehearsal = _dry_run_text(dry, task_id, deps, snap, mine)
        if unkept:
            rehearsal += f"\nThe real reply would say:{unkept}"
        return rehearsal
    request_id = ids.request()
    future = _park(bridge, request_id)
    bridge.emit(TaskDeclared(task=task, node_id=me, request_id=request_id))

    settled = await _outcome(future)
    if settled.refusal is not None:
        listed: bool | None = None
        if settled.refusal is TaskRefusal.DUPLICATE_ID and settled.held_off_fleet is None:
            # A second read, on this path only: the store refuses an id any
            # session has and does not say whether it is one this board lists.
            on_board = await board_ids(bridge, me)
            listed = None if on_board is None else task_id in on_board
        gated = False
        if settled.cycle:
            # A read on this path only, as for the duplicate: the store names
            # the loop and not which of its tasks wait on every writer.
            # Whether a task does is fixed when it is declared, so a read
            # after the refusal answers it as the apply saw it.
            world = await board_world(bridge, me)
            gated = _loop_has_a_gate(settled.cycle, deps, None if world is None else world[1])
        why = _declare_refusal_text(
            settled.refusal, settled.held_off_fleet, settled.cycle, listed, deps, gated
        )
        return f"Task {task_id} is NOT on the board. {why}"
    said = [f"Task {task_id} is on the board."]
    if settled.auto_depends:
        said.append(_auto_depends_text(settled.auto_depends))
    if settled.already_behind:
        said.append(_already_behind_text(settled.already_behind))
    return " ".join(said) + unkept


DECLARE_TASK = Tool(
    "declare_task",
    "Put a unit of work on the shared board for any agent to claim. "
    "depends_on names tasks that must finish first, and touches names the files "
    "the work will write -- the board adds a dependency for you when two tasks "
    "would write the same file. With dry_run=true nothing is declared: the reply "
    "says what the declaration would do -- the dependencies the board would add "
    "and the files behind each, the chain it would wait on, whether it could be "
    "claimed at once, or why it would be refused -- so touches can be narrowed or "
    "the work split before the task exists.",
    _schema(
        {
            "dry_run": {
                **_FLAG_TYPE,
                "description": (
                    "true to see what this declaration would do without making "
                    "it: nothing is put on the board. Omit it to declare."
                ),
            },
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
                "description": (
                    "Task ids that must finish first. Omit it for none. Put "
                    f"{ALL_WRITERS!r} in it and the task also waits on every "
                    "unfinished task on this board that has touches, including "
                    "ones declared after it, for a gate such as a final review "
                    "or a full test run. The board works out who those are on "
                    "every read; you never list or amend them. A task that writes "
                    "files and would wait on such a gate is refused as a cycle."
                ),
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
            "channel": {
                "type": "string",
                "description": (
                    "Only when another lead asked for this work: the channel the "
                    "request came on. Omit it for work your own team decided on, "
                    "and then omit re and inside_charter too."
                ),
            },
            "re": {
                "type": "string",
                "description": (
                    "The id of the peer message that asked for this work, as the "
                    "bus gave it (c- and 12 hex digits)."
                ),
            },
            "inside_charter": {
                **_FLAG_TYPE,
                "description": (
                    "yes or no (true or false is read the same): whether you judge "
                    "the request to be inside the channel's charter. It is shown as "
                    "your word. Omit it, or say unstated, to leave it unstated."
                ),
            },
        },
        required=("title",),
    ),
    declare_task,
)


async def amend_task(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
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
        return f"{refused} {bad}"
    # The two ways of touching the property do not share a spelling, for the
    # reason ``not_run`` and ``all_run`` do not: an empty string is what a
    # model writes into an optional field without meaning anything, and here
    # it would withdraw the one sentence the work is held to.
    stated = normalised_property(args.get("property"))
    withdraw = stated_flag(args.get("withdraw_property"))
    if withdraw is None:
        return (
            f"{refused} withdraw_property is {str(args.get('withdraw_property'))!r}, "
            f"which is neither true nor false. Set it true to remove the property, "
            f"or leave it out."
        )
    if withdraw and stated:
        return (
            f"{refused} You gave a property and set withdraw_property. Give the new "
            f"property, or withdraw it, not both."
        )
    if detail is None and not stated and not withdraw:
        return (
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
        return f"{refused} {too_long}"
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
    request_id = ids.request()
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
        return f"{refused} {_refusal_text(settled.refusal)}"
    # Read back, because the reply to a write says only that it landed and
    # the version it made is the thing the amender needs to be told.
    row = await board_row(bridge, me, task_id)
    return _amended_text(task_id, row, at, basis_sent=basis is not None, unkept=unkept)


AMEND_TASK = Tool(
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
    amend_task,
)


async def complete_task(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
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
    request_id = ids.request()
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
    if settled.refusal is TaskRefusal.WITHDRAWN:
        refused = f"Task {task_id} is NOT complete. {_refusal_text(settled.refusal)}"
        return refused + await withdrawn_because(bridge, me, task_id)
    if settled.refusal is not None:
        return f"Task {task_id} is NOT complete. {_refusal_text(settled.refusal)}"
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
    row = await board_row(bridge, me, task_id)
    if row is not None and _behind(row, me):
        # Whether a concern can reach whoever replaced the text is the
        # session's answer about the name, and not something read off it.
        by = _one_line(row.superseded[-1].replaced_by) if row.superseded else ""
        reachable = bool(by) and session.resolve_role(by) is not None
        return " ".join(said) + "\n\n" + _landed_behind_text(row, me[1] is None, reachable)
    return " ".join(said)


COMPLETE_TASK = Tool(
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
    complete_task,
)


async def release_task(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    task_id = str(args.get("task_id", "")).strip()
    request_id = ids.request()
    future = _park(bridge, request_id)
    bridge.emit(
        TaskReleased(node_id=me, task_id=task_id, request_id=request_id, at=time.monotonic())
    )

    settled = await _outcome(future)
    if settled.refusal is None:
        return f"Task {task_id} is back on the board."
    said = f"Task {task_id} was NOT released. {_refusal_text(settled.refusal)}"
    if settled.refusal is TaskRefusal.WITHDRAWN:
        said += await withdrawn_because(bridge, me, task_id)
    if settled.refusal is TaskRefusal.NOT_YOURS and me[1] is None:
        # The lead has one release that is not its own, and a lead refused
        # here tried it on a holder it does not cover.
        said += (
            " As this session's lead you can release another agent's claim only "
            "when that agent is of an earlier run of this session and is not "
            f"running, and this holder is not. If its holder will not finish or "
            f"release it, {_OPERATOR_CLOSES}, {_CLOSED_IS_NOT_DONE}."
        )
    return said


RELEASE_TASK = Tool(
    "release_task",
    "Give a task you claimed back to the board without completing it. The lead "
    "of a session may also release a task whose holder is an agent of an earlier "
    "run of that session -- from before pptmstr was restarted or the session was "
    "resumed -- and is not running. read_board says so on such a row, and the "
    "release puts it back for another agent to claim; nobody else's claim can be "
    "released for them.",
    {"task_id": str},
    release_task,
)


async def withdraw_task(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    task_id = str(args.get("task_id", "")).strip()
    refused = f"Task {_named(task_id)} was NOT withdrawn."
    reason = str(args.get("reason") or "")
    if not reason.strip():
        return (
            f"{refused} You gave no reason. Say why the work is no longer wanted: "
            f"it is what the board shows in place of a result."
        )
    too_long = _length_refusal(
        "reason",
        reason,
        MAX_WITHDRAWAL_REASON_CHARS,
        "Put the long form in a file and name its path in the reason.",
    )
    if too_long is not None:
        return f"{refused} {too_long}"
    edited = bool(args.get(EDITED_KEY))
    request_id = ids.request()
    future = _park(bridge, request_id)
    bridge.emit(
        TaskWithdrawn(
            node_id=me,
            task_id=task_id,
            reason=reason,
            request_id=request_id,
            at=time.monotonic(),
            edited=edited,
        )
    )
    settled = await _outcome(future)
    if settled.refusal is TaskRefusal.WITHDRAW_HELD_LIVE:
        # A read of its own, because the refusal is an enum and the holder
        # is on the row. Named only while the row still says it holds it.
        row = await board_row(bridge, me, task_id)
        holder = row.owner if row is not None and row.state is TaskState.CLAIMED else None
        return f"{refused} {_held_live_text(holder)}"
    if settled.refusal is not None:
        return f"{refused} {_refusal_text(settled.refusal)}"
    # Read after the write, and off what the write left: the withdrawal
    # files its notice to whoever held the task as it landed, and to nobody
    # when nobody did. A read before the write would race a claim or a
    # release landing between the two.
    row, notice = await withdrawn_row(bridge, me, task_id)
    said = [f"Task {_named(task_id)} is withdrawn, with your reason in place of a result."]
    if edited:
        said.append(
            "The operator edited your reason before releasing the call: the board "
            "shows it as the operator's edit, and read_task shows the words it holds."
        )
    if row is not None and row.withdrawn_by_lead:
        said.append(
            "Its declarer has stopped, so the board shows you as the one who "
            "withdrew it, in its place."
        )
    said.append("Anything waiting on it is no longer waiting on it.")
    if notice is not None:
        holder = _one_line(notice.recipient)
        if holder.endswith(f", {BEFORE_RESTART}"):
            # The board's label for an agent of an earlier run is an
            # appositive, closed before the verb that follows it.
            holder += ","
        said.append(
            f"{holder} held it, and has been sent a notice "
            f"with your reason saying its files are released and its next "
            f"complete_task or release_task on it is refused."
        )
    return " ".join(said)


WITHDRAW_TASK = Tool(
    "withdraw_task",
    "Withdraw a task YOU declared that is no longer wanted. It is finished as "
    "withdrawn, with your reason in place of a result, and every task waiting on "
    "it stops waiting -- the files it named included. You may withdraw a task "
    "you amended, and one somebody holds: its holder is sent a notice with your "
    "reason saying its files are released, and its later complete_task is "
    "refused. Only the declarer can withdraw a task, except that a session's "
    "lead may withdraw one whose declarer has stopped and that no agent which "
    "can still be woken holds, and the board then says the lead did. A "
    "finished task cannot be withdrawn.",
    _schema(
        {
            "task_id": {"type": "string"},
            "reason": {
                "type": "string",
                "description": (
                    "Why the work is no longer wanted. Required, and shown on "
                    "the task in place of a result. "
                    f"At most {MAX_WITHDRAWAL_REASON_CHARS} characters."
                ),
            },
        },
        required=("task_id", "reason"),
    ),
    withdraw_task,
)


async def raise_decision(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    root = me[1] is None
    refused = "The decision was NOT raised."
    texts = {
        name: str(args.get(name) or "")
        for name in ("question", "if_unanswered", "evidence", "recommendation")
    }
    if not texts["question"].strip():
        return (
            f"{refused} question is what you are asking the operator to decide, "
            f"and you gave none."
        )
    if not texts["if_unanswered"].strip():
        # Refused and not defaulted: it is what the operator weighs a late
        # answer against, and in a session nobody is watching it is the
        # whole of what happens next.
        return (
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
            return f"{refused} {too_long}"
    blocks, unlisted = _stated_blocks(args.get("blocks"))
    if unlisted is not None:
        return f"{refused} {unlisted}"
    try:
        standing = _stated_standing(args.get("standing"))
    except InvalidStanding as bad:
        return f"{refused} {bad}"
    reversible, unkept = _stated_reversible(args.get("reversible"))
    channel = str(args.get("channel") or "").strip() or None
    if channel is not None:
        if not root:
            return f"{refused} {_decision_refusal_text(DecisionRefusal.NOT_A_LEAD, '', root)}"
        unnamed = channel_refusal(channel)
        if unnamed is not None:
            return f"{refused} {unnamed}"

    # Minted here and never read from the arguments: the store refuses an id
    # it already holds and cannot tell a minted one from one an agent chose,
    # so an id taken from a caller would let it aim at another decision.
    decision_id = ids.decision()
    at = time.monotonic()
    request_id = ids.request()
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
            channel=channel,
            called_at=None if channel is None else _called_at(args),
        )
    )
    settled = await future
    why = settled.refusal if isinstance(settled, DecisionSettled) else DecisionRefusal.NOT_APPLIED
    if why is DecisionRefusal.UNKNOWN_TASK:
        # The store says one is not there and not which. A second read, on
        # this path only, so the refusal can name it.
        on_board = await board_ids(bridge, me)
        strangers = () if on_board is None else tuple(b for b in blocks if b not in on_board)
        return f"{refused} {_decision_refusal_text(why, decision_id, root, strangers)}"
    peer = settled.peer if isinstance(settled, DecisionSettled) else None
    if why is not None:
        return f"{refused} {_decision_refusal_text(why, decision_id, root, now_on=peer)}"
    said = [
        f"Decision {decision_id} is in front of the operator "
        f"({_standing_said(standing, 'you')}; reversible: "
        f"{_REVERSIBLE_SAID[reversible]}; {_blocks_said(blocks)}). "
        f"Nothing is parked: keep "
        f'working, and call read_decisions(decision_id="{decision_id}") when you '
        f"reach what this blocks and again before you finish. It returns the "
        f"operator's answer every time you ask. "
        f"{WAKE_SAID if root else WAKE_SAID_SUBAGENT} withdraw_decision takes "
        f"the decision back if it stops mattering. It is kept in your session's "
        f"log, and is back after a restart of pptmstr once your session is "
        f"resumed."
    ]
    if channel is not None:
        said.append(
            f"It is raised on #{channel}: {_lead_of(peer)} can read it and its "
            f"answer with read_decisions while the link is open. {_PEER_LEGEND} If its "
            f"session is running, pptmstr wakes that lead with a message naming "
            f"this decision's id and not its text, and wakes it again when the "
            f"operator answers or dismisses it."
        )
    if unkept:
        said.append(unkept)
    return " ".join(said)


RAISE_DECISION = Tool(
    "raise_decision",
    "Put a decision in front of the operator as a record they answer in their "
    "own pane, and keep working: nothing waits on the answer. Use "
    "it for what only the operator can decide -- a tradeoff the task did not "
    "settle, or a choice you already took and want ratified. For something "
    "another agent can answer, post a concern instead. The answer is handed to "
    "you, the agent that asked, when you call read_decisions with the id this "
    "returns, in the operator's words; your session's lead can read it too, and "
    "no other agent can. It is a question and not a document: each text is at "
    f"most {MAX_DECISION_TEXT_CHARS} characters, so put the long form in a file "
    f"and name its path. You may have {MAX_OPEN_DECISIONS_PER_ASKER} open at "
    "once. Each is kept in your session's log, and is back after a restart of "
    "pptmstr once your session is resumed. A session's lead may raise "
    "one on a channel, a link the operator made to another team's lead: that "
    "lead can then read it and its answer too, for as long as the link is "
    "open. One raised on a channel is a message to that lead as well, so the "
    "call itself is held for the operator's approval before it is raised "
    "wherever send_to_peer would be; its answer still waits on nothing.",
    _schema(
        {
            "question": {
                "type": "string",
                "description": "What you are asking the operator to decide.",
            },
            "channel": {
                "type": "string",
                "description": (
                    "Leads only. The name of a live link the operator made between "
                    "your session and another team's lead, without the '#', when "
                    "the decision is one that lead should read too. Omit it for a "
                    "decision inside your team."
                ),
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
    raise_decision,
)


async def read_decisions(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    wanted = str(args.get("decision_id") or "").strip() or None
    now = time.monotonic()
    request_id = ids.request()
    # The fallback is no answer at all, for ``read_digest``'s reason: an
    # empty list says this agent raised nothing, which nobody established.
    future = bridge.ask(request_id, DecisionsDelivered(request_id=request_id, decisions=None))
    bridge.emit(DecisionsRead(node_id=me, request_id=request_id, at=now, decision_id=wanted))
    answer = await future

    if not isinstance(answer, DecisionsDelivered) or answer.decisions is None:
        return f"Your decisions were NOT read. {_refusal_text(TaskRefusal.NOT_APPLIED)}"
    return _decisions_text(
        answer.decisions,
        wanted,
        now,
        me,
        lambda node: session.role_of(node) or "another agent",
        {peer.session: peer for peer in answer.peers},
    )


READ_DECISIONS = Tool(
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
    "marked with who raised it, and can read and not withdraw another agent's. "
    "A lead is also shown a decision the lead at the other end of a live "
    "channel raised on it, and is woken when it is raised and when the "
    "operator answers or dismisses it.",
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
    read_decisions,
)


async def withdraw_decision(session: Team, bridge: Bridge, me: NodeId, args: dict[str, Any]) -> str:
    root = me[1] is None
    decision_id = str(args.get("decision_id") or "").strip()
    named = _named(decision_id[:MAX_ID_CHARS])
    request_id = ids.request()
    future = bridge.ask(
        request_id, DecisionSettled(request_id=request_id, refusal=DecisionRefusal.NOT_APPLIED)
    )
    bridge.emit(
        DecisionWithdrawn(
            node_id=me, decision_id=decision_id, request_id=request_id, at=time.monotonic()
        )
    )
    settled = await future
    why = settled.refusal if isinstance(settled, DecisionSettled) else DecisionRefusal.NOT_APPLIED
    if why is None:
        return (
            f"Decision {named} is withdrawn: it has left the operator's queue and "
            f"no answer will come. read_decisions still lists it."
        )
    return (
        f"Decision {named} was NOT withdrawn. " f"{_decision_refusal_text(why, decision_id, root)}"
    )


WITHDRAW_DECISION = Tool(
    "withdraw_decision",
    "Take back a decision YOU raised that the operator has not answered: it "
    "leaves their queue and no answer will come. Use it when the question has "
    "stopped mattering. An answered or dismissed decision cannot be withdrawn.",
    _schema({"decision_id": {"type": "string"}}, required=("decision_id",)),
    withdraw_decision,
)


# Every tool every session's server has, in the order ``tools/list`` announces
# them. The order is the SDK's: it lists what it is handed, as it was handed.
TOOLS: tuple[Tool[Team], ...] = (
    POST_CONCERN,
    READ_INBOX,
    SEND_TO_PEER,
    READ_PEER_MESSAGES,
    WITHDRAW_PEER_MESSAGE,
    READ_BOARD,
    READ_TASK,
    READ_DIGEST,
    WHY_BLOCKED,
    READY,
    CLAIM_TASK,
    DECLARE_TASK,
    AMEND_TASK,
    COMPLETE_TASK,
    RELEASE_TASK,
    WITHDRAW_TASK,
    RAISE_DECISION,
    READ_DECISIONS,
    WITHDRAW_DECISION,
)


def _registered(spec: Tool[Team], session: Team, bridge: Bridge) -> Any:
    """
    ``spec`` as the SDK registers it for ``session``: the gate's stamp read off
    the call as its sender, and the handler's text as the reply.
    """

    @tool(spec.name, spec.description, spec.schema)
    async def call(args: dict[str, Any]) -> dict[str, Any]:
        return _text(await spec.handler(session, bridge, _sender(args), args))

    return call


def build_server(session: Team, command_tools: Sequence[Tool[Team]]) -> Any:
    """
    An MCP server bound to one session: ``TOOLS``, then ``command_tools`` when
    ``session.contained`` says so.

    Per-session rather than shared: the handlers need that session's ``Bridge`` to
    emit on and to park against, and role resolution is scoped to the agents that
    session actually spawned.

    ``command_tools`` are ``commandbus.TOOLS``, handed in by the caller so the bus
    imports nothing of ``commandbus``; their names are ``COMMAND_TOOLS``.
    """
    bridge = session.bridge
    # The session's own answer and no default: a session object that cannot say
    # whether it is contained raises here and is offered nothing.
    offered = (*TOOLS, *(command_tools if session.contained else ()))
    return create_sdk_mcp_server(
        SERVER_NAME, "1.0.0", [_registered(spec, session, bridge) for spec in offered]
    )


# The three tools between leads of linked sessions. On every session's server and
# stamped like the rest, and apart from ``BUS_TOOLS`` because that list is also
# what every restricted role is given (``templates.BUS_TOOL_NAMES``): a worker has
# no peer to write to, and its handler refuses one that calls them anyway.
PEER_TOOLS = frozenset(
    qualified(name) for name in ("send_to_peer", "read_peer_messages", "withdraw_peer_message")
)

# Every other tool on every session's server, which the gate must stamp. Read off
# the table, so a tool registered is a tool stamped: one missing here would reach
# its handler unstamped and raise.
BUS_TOOLS = frozenset(qualified(spec.name) for spec in TOOLS) - PEER_TOOLS

# The three tools only a contained session's server has, which the gate stamps
# for that session and for no other. Spelled here and not read off
# ``commandbus.TOOLS`` because the bus imports nothing of that module (it is handed
# the tools, ``build_server``); tests/test_bus.py holds the two spellings and the
# server's own list together.
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
    offered = BUS_TOOLS | PEER_TOOLS
    return offered | COMMAND_TOOLS if contained else offered
