"""
Effects: what the reducer has to tell the shell (§2.7).

``_apply`` is a pure function of (snapshot, intent, instant). That is what makes
the store testable without a UI or an event loop, and it is why the store can be
confined to one thread without a lock. But two of the bus tools -- ``claim_task``
and ``read_inbox`` -- are *questions*, and a pure reducer has no way to answer one:
it returns the next world, not a reply to the agent parked on a future.

The functional answer is to widen the return rather than to smuggle the reply
through the world. ``_apply`` returns ``(Snapshot, tuple[Effect, ...])``; the app
loop hands each effect to the ``Bridge``, which completes the waiting future. The
same shape as ``Intent`` in the other direction: a value, matched exhaustively,
inspectable in a test with no threads in it.

Two properties come from this rather than from care:

- **An effect exists only because an intent was applied.** The alternative --
  answering every outstanding request each frame -- is wrong, because a future is
  registered *before* its intent is queued, so a request can be outstanding a
  frame before the store has seen it. "Answer what you applied" is unstateable as
  a bug here; there is nothing else to answer.
- **No correlation token lands in the domain.** ``Task`` and ``Concern`` describe
  work and messages, not the requests that touched them, so nothing has to
  remember to clear one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Concern, Task


@dataclass(frozen=True, slots=True)
class ClaimSettled:
    """
    A worker asked for work and this is the answer.

    ``task`` None means nothing was claimable -- a real answer, and the common one
    on a board whose remaining items are all blocked. The agent is told so and can
    stop asking; it is not left parked.
    """

    request_id: str
    task: Task | None


@dataclass(frozen=True, slots=True)
class InboxDelivered:
    """
    A recipient read its inbox, and these are the concerns that went with the reply.

    Carries the records as they were delivered, which is not always as they were
    posted: an operator can edit a concern in flight, and this is the text the
    recipient actually received.
    """

    request_id: str
    concerns: tuple[Concern, ...]


# Explicit union, for the same reason ``Intent`` is one: the app loop matches over
# it and mypy reports an unhandled member as a type error rather than letting an
# agent stay parked on a future nobody completes.
Effect = ClaimSettled | InboxDelivered
