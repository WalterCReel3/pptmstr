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
from typing import TYPE_CHECKING, Any, assert_never

from claude_agent_sdk import create_sdk_mcp_server, tool

from .bridge import Bridge
from .effects import ClaimSettled, Effect, InboxDelivered, TaskWriteSettled
from .intents import (
    ConcernPosted,
    InboxRead,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
)
from .model import Concern, NodeId, Task, TaskRefusal

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to the checker
    from .driver import AgentSession

SERVER_NAME = "pptmstr"

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


async def _outcome(future: asyncio.Future[Effect]) -> TaskRefusal | None:
    """
    Wait for the store's answer to a board write. None means it took effect.

    An effect of the wrong shape is reported as ``NOT_APPLIED`` rather than as
    success. It cannot happen -- futures are settled by request id -- but the two
    ways to be wrong here are not symmetric: a spurious refusal makes an agent
    re-check the board, and a spurious success makes it build on a task that is not
    there.
    """
    answer = await future
    if not isinstance(answer, TaskWriteSettled):
        return TaskRefusal.NOT_APPLIED
    return answer.refusal


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
        "role name, or 'lead' for the agent that started the work.",
        {"to": str, "subject": str, "body": str},
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
        "declare_task",
        "Put a unit of work on the shared board for any agent to claim. "
        "depends_on names tasks that must finish first.",
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
            },
            required=("title",),
        ),
    )
    async def declare_task(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        task_id = str(args.get("task_id", "")).strip() or f"t-{uuid.uuid4().hex[:8]}"
        raw_deps = args.get("depends_on") or []
        deps = tuple(str(d).strip() for d in raw_deps if str(d).strip())
        request_id = f"d-{uuid.uuid4().hex[:12]}"
        future = _park(bridge, request_id)
        bridge.emit(
            TaskDeclared(
                task=Task(
                    id=task_id,
                    title=str(args.get("title", "")).strip(),
                    detail=str(args.get("detail", "")),
                    depends_on=deps,
                    declared_at=time.monotonic(),
                ),
                node_id=me,
                request_id=request_id,
            )
        )

        refusal = await _outcome(future)
        if refusal is None:
            return _text(f"Task {task_id} is on the board.")
        return _text(f"Task {task_id} is NOT on the board. {_refusal_text(refusal)}")

    @tool("complete_task", "Mark a task you claimed as finished.", {"task_id": str})
    async def complete_task(args: dict[str, Any]) -> dict[str, Any]:
        me = _sender(args)
        task_id = str(args.get("task_id", "")).strip()
        request_id = f"f-{uuid.uuid4().hex[:12]}"
        future = _park(bridge, request_id)
        bridge.emit(
            TaskCompleted(node_id=me, task_id=task_id, at=time.monotonic(), request_id=request_id)
        )

        refusal = await _outcome(future)
        if refusal is None:
            return _text(
                f"Task {task_id} marked complete. Anything waiting on it is now claimable."
            )
        return _text(f"Task {task_id} is NOT complete. {_refusal_text(refusal)}")

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

        refusal = await _outcome(future)
        if refusal is None:
            return _text(f"Task {task_id} is back on the board.")
        return _text(f"Task {task_id} was NOT released. {_refusal_text(refusal)}")

    return create_sdk_mcp_server(
        SERVER_NAME,
        "1.0.0",
        [post_concern, read_inbox, claim_task, declare_task, complete_task, release_task],
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
        "claim_task",
        "declare_task",
        "complete_task",
        "release_task",
    )
)
