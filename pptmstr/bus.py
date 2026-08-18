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
wait for**. Five of the six tools park on the Bridge's third crossing and are
answered by the store on the next frame (``effects.py``): ``read_inbox`` and
``claim_task``, which ask questions, and the three board writes, which assert
facts the reducer is entitled to refuse.

``post_concern`` is the exception and is not in this class. Its only refusal is the
``resolve_role`` check it makes here, before emitting, and it reports that one
honestly out of ``role_status``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, assert_never

from claude_agent_sdk import create_sdk_mcp_server, tool

from .board import BoardConcern, BoardTask
from .bridge import Bridge
from .effects import (
    BoardDelivered,
    ClaimSettled,
    Effect,
    InboxDelivered,
    TaskWriteSettled,
)
from .intents import (
    BoardRead,
    ConcernPosted,
    InboxRead,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
)
from .model import Concern, ConcernId, NodeId, Task, TaskId, TaskRefusal

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to the checker
    from .driver import AgentSession

SERVER_NAME = "pptmstr"

# One task's specification in a board read. Generous: this is the field the tool
# exists to expose, and a lead that has to ask for the rest of its own spec is the
# defect being fixed. Bounded at all because a board read is a reply an agent pays
# for by the token, and one 40,000-character detail would crowd out every other row.
_MAX_DETAIL_CHARS = 2000

# One agent note in a board read. Tighter than a specification: a concern is one
# finding, and a reader scanning a board wants the finding rather than the essay.
# Announced when it bites, like every other bound here.
_MAX_CONCERN_CHARS = 1200

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

    The abandon answer is a *refusal*, not a success. Every other fallback in this
    module can afford to be the ordinary negative -- ``claim_task`` abandons as "the
    board had nothing", which is true often enough to be harmless. A write has no
    such answer: the only outcome that is certainly wrong at shutdown is the one
    saying it landed.
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
    now has two things to tell its caller and only one of them is whether it landed.
    Pulling the refusal out here would mean a second waiter for the second answer.
    """
    answer = await future
    if not isinstance(answer, TaskWriteSettled):
        return TaskWriteSettled(request_id="", refusal=TaskRefusal.NOT_APPLIED)
    return answer


def _board_line(row: BoardTask, reasons: Mapping[ConcernId, BoardConcern] | None = None) -> str:
    """
    One board row, as the asking agent reads it.

    Says who holds a task rather than only its state, because "claimed" without an
    owner is the answer that sends a worker to ask the lead what it already could
    have known. The owner is the address the bus routes to, so a reader can act on
    it -- ``post_concern(to="builder-2")`` -- rather than only recognise it.

    A dependency that was never declared is called out separately from one that is
    merely unfinished. They look identical on the record and they are different
    problems: the first is a typo in somebody's ``depends_on`` and will never clear
    on its own, and nothing else in the session would ever say so.

    **Carries ``detail``, which is the point of the tool rather than a detail of
    it.** ``Task.detail`` is the closest thing to an authoritative specification
    this system has, and until this line it was written by ``declare_task`` and read
    in exactly one place: the reply ``claim_task`` interpolates it into. So the lead
    could not read back what it wrote, and the worker holding a task could not ask
    for it again -- ``_pick_claim`` requires ``is_claimable``, which requires
    PENDING, so the current owner is told "Nothing claimable right now".

    That is what made the recorded fix for an amended specification impossible
    rather than merely undisciplined: the preferred option is that a worker re-reads
    the board instead of trusting its transcript copy, and
    `what-the-board-does-not-carry.md` corrects "a discipline the workers do not
    have" to **"one they cannot practise"**. This is the practising.

    ``touches`` rides along because a worker that cannot see which files a task
    claims cannot honour the boundary the auto-dependency exists to enforce, and an
    open concern is named because a row stalled for a recorded reason is a different
    row from one stalled silently.
    """
    parts = [f"- {row.id} [{row.state.value}] {row.title}"]
    if row.owner is not None:
        parts.append(f"held by {row.owner}")
    if row.owner_gone:
        parts.append("but its owner has stopped -- this task is stranded")
    if row.blocked_on:
        parts.append(f"waiting on {', '.join(row.blocked_on)}")
    if row.missing:
        parts.append(f"NEVER DECLARED: {', '.join(row.missing)}")
    if row.touches:
        parts.append(f"writes {', '.join(row.touches)}")
    line = " · ".join(parts)
    if row.detail:
        # Indented under its row rather than joined with the separator: a
        # specification is a paragraph, and running it into a one-line summary is
        # what made the summary unreadable when this was tried inline.
        body, dropped = _clipped(row.detail, _MAX_DETAIL_CHARS)
        line += f"\n    {body}"
        if dropped:
            line += f"\n    ... {dropped} more character(s) -- ask the lead for the rest"
    line += _reasons_text(row, reasons or {})
    return line


def _reasons_text(row: BoardTask, by_id: Mapping[ConcernId, BoardConcern]) -> str:
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

    Nothing here is unreviewed. Every body has already been through the approval
    gate at ``post_concern``, and what is rendered is the text as *delivered* --
    the operator's edit included, which is the version the recipient acted on.
    """
    shown = [by_id[cid] for cid in row.concerns if cid in by_id]
    if not shown:
        return ""
    out = [f"\n    -- {len(shown)} agent note(s) on this task:"]
    for concern in shown:
        body, dropped = _clipped(concern.body, _MAX_CONCERN_CHARS)
        out.append(f"\n       [{concern.sender}] {concern.subject or '(no subject)'}")
        if body:
            out.append(f"\n       {body}")
        if dropped:
            out.append(f"\n       ... {dropped} more character(s)")
    return "".join(out)


def _clipped(text: str, limit: int) -> tuple[str, int]:
    """
    Bound a specification, reporting what was dropped.

    Bounded per row rather than over the whole reply, because the row that matters
    to the reader is its own and a global budget would spend it on whichever tasks
    happened to be declared first. Says what it dropped for the same reason every
    other bound in this project does: a silent cap on a specification is how a
    worker builds confidently against half of one.
    """
    if len(text) <= limit:
        return text, 0
    return text[:limit], len(text) - limit


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
            return (
                "That id is already on the board, and re-declaring it would overwrite "
                "work another agent may already be doing. Use a different id, or claim "
                "the task that is there."
            )
        case TaskRefusal.WOULD_CYCLE:
            return (
                "Its depends_on would close a dependency cycle, which leaves every task "
                "in the loop permanently unclaimable. Check what the tasks you named "
                "already depend on."
            )
        case TaskRefusal.NO_SUCH_TASK:
            return "No task with that id is on the board."
        case TaskRefusal.ALREADY_COMPLETE:
            return "It is already complete. Nothing changed, and nothing is left to do on it."
        case TaskRefusal.NOT_CLAIMED:
            return "Nobody holds it. Claim it first, with claim_task and that id."
        case TaskRefusal.NOT_YOURS:
            return (
                "Another agent holds the claim, and only the claimer can change a task's "
                "state. Send them a concern rather than acting on it yourself."
            )
        case TaskRefusal.NOT_APPLIED:
            return "The board never saw the request -- the application is shutting down."
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(refusal)


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
    """
    ids = ", ".join(added)
    subject = "an unfinished task" if len(added) == 1 else "unfinished tasks"
    return (
        f"It also now depends on {ids}, added by the board: it writes files {subject} "
        f"on this board will also write. Leave the dependency in place -- it is what "
        f"keeps the two off the same file. If the overlap is wrong, narrow the touches "
        f"of one of them rather than removing the edge."
    )


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

    @tool(
        "post_concern",
        "Send a concern to another agent working on this task. Use the agent's "
        "role name, or 'lead' for the agent that started the work. Name task_id "
        "when the concern is about a task on the board.",
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

        concern = Concern(
            id=f"c-{uuid.uuid4().hex[:12]}",
            sender=sender,
            recipient=recipient,
            subject=str(args.get("subject", "")).strip(),
            body=str(args.get("body", "")),
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
            task_id=str(args.get("task_id", "")).strip() or None,
        )
        bridge.emit(ConcernPosted(sender, concern))
        return _text(f"Concern delivered to {to}.")

    @tool(
        "read_inbox",
        "Read concerns other agents have sent you. Returns everything waiting and "
        "marks it read.",
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
        lines = [f"{len(answer.concerns)} concern(s):"]
        for c in answer.concerns:
            who = session.role_of(c.sender) or "another agent"
            lines.append(f"\n[from {who}] {c.subject}\n{c.body}")
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
            # An ordinary condition, not a failure: the board may be empty or
            # everything left may still be blocked. Said plainly so the agent stops
            # asking rather than retrying into a loop.
            return _text(
                "Nothing claimable right now -- the board is empty or the remaining "
                "work is still blocked by unfinished dependencies."
            )
        won = answer.task
        detail = f"\n{won.detail}" if won.detail else ""
        return _text(f"You now own task {won.id}: {won.title}{detail}")

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
        by_id = {c.id: c for c in answer.concerns}
        lines = [f"{len(answer.tasks)} task(s) on your board:"]
        for row in answer.tasks:
            lines.append(_board_line(row, by_id))
        return _text("\n".join(lines))

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
                        "Repository-relative paths this task will write, e.g. "
                        "'pptmstr/store.py'. Any unfinished task on this board that "
                        "writes one of them becomes a dependency automatically. Give "
                        "them relative to the repository root: an absolute path is "
                        "not matched against a relative one."
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
        request_id = f"d-{uuid.uuid4().hex[:12]}"
        future = _park(bridge, request_id)
        bridge.emit(
            TaskDeclared(
                task=Task(
                    id=task_id,
                    title=str(args.get("title", "")).strip(),
                    detail=str(args.get("detail", "")),
                    depends_on=deps,
                    touches=touches,
                    declared_at=time.monotonic(),
                ),
                node_id=me,
                request_id=request_id,
            )
        )

        settled = await _outcome(future)
        if settled.refusal is not None:
            return _text(f"Task {task_id} is NOT on the board. {_refusal_text(settled.refusal)}")
        if settled.auto_depends:
            return _text(
                f"Task {task_id} is on the board. {_auto_depends_text(settled.auto_depends)}"
            )
        return _text(f"Task {task_id} is on the board.")

    @tool("complete_task", "Mark a task you claimed as finished.", {"task_id": str})
    async def complete_task(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        task_id = str(args.get("task_id", "")).strip()
        request_id = f"f-{uuid.uuid4().hex[:12]}"
        future = _park(bridge, request_id)
        bridge.emit(
            TaskCompleted(node_id=me, task_id=task_id, at=time.monotonic(), request_id=request_id)
        )

        settled = await _outcome(future)
        if settled.refusal is None:
            return _text(
                f"Task {task_id} marked complete. Anything waiting on it is now claimable."
            )
        return _text(f"Task {task_id} is NOT complete. {_refusal_text(settled.refusal)}")

    @tool(
        "release_task",
        "Give a task you claimed back to the board without completing it.",
        {"task_id": str},
    )
    async def release_task(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        task_id = str(args.get("task_id", "")).strip()
        request_id = f"x-{uuid.uuid4().hex[:12]}"
        future = _park(bridge, request_id)
        bridge.emit(TaskReleased(node_id=me, task_id=task_id, request_id=request_id))

        settled = await _outcome(future)
        if settled.refusal is None:
            return _text(f"Task {task_id} is back on the board.")
        return _text(f"Task {task_id} was NOT released. {_refusal_text(settled.refusal)}")

    return create_sdk_mcp_server(
        SERVER_NAME,
        "1.0.0",
        [
            post_concern,
            read_inbox,
            read_board,
            claim_task,
            declare_task,
            complete_task,
            release_task,
        ],
    )


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
        "claim_task",
        "declare_task",
        "complete_task",
        "release_task",
    )
)
