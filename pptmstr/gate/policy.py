"""
The gate's rungs, and what each one asks of a session.

``Disposition`` is what ``approval.classify`` answers; ``Policy`` is the rung it
answers under. ``requires_containment`` and ``inherits_to_subagents`` are
properties of a rung, so they sit beside it, each a ``match`` closed by
``assert_never``.

Imports nothing from pptmstr. ``model``, ``templates``, ``intents``, the store
and the session file carry a ``Policy``, so an edge from here into the package
would close a cycle through them.
"""

from __future__ import annotations

import enum
from typing import assert_never


class Disposition(enum.Enum):
    AUTO_APPROVE = "auto_approve"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class Policy(enum.Enum):
    """
    How much a session's gate admits without an operator.

    An enum rather than a ``bool research_mode`` because the recorded preference
    (2026-08-11 §"Two corrections", point 2) is to build the general shape -- a
    policy value on the session -- and ship presets over it, so that a second
    preset is a member here rather than a retrofit around a boolean.

    The members are rungs on a ladder of postures (2026-08-22; 2026-09-03 §7),
    not alternatives. A rung names the posture and never the permission, and none
    is named "read-only" or "safe": mutation and egress are independent axes, and
    one reassuring word over both is what lets something mutation-free and
    egress-positive onto an allowlist without a reviewer noticing (2026-08-11
    §"The reframe"). A posture name cannot carry a scope, so each rung states its
    own.

    A parameter of ``classify`` rather than module state: this module's purity is
    the reason the dial is built over the gate at all, and a mode that worked by
    mutating a global would make every test here order-dependent.

    **The two widened rungs rest on different things, and neither subsumes the
    other.** ``PERMISSIVE`` rests on a syntactic claim about the command -- it is
    decidable, fail-closed, and needs no containment, which is why it is the rung
    that survives where a sandbox breaks the work. ``AUTONOMOUS`` abandons the
    claim about the command entirely and rests on a bound around the process, so
    it cannot start without one. That is why the ladder is ordered by how much is
    released and not by how much is trusted.
    """

    # Today's behaviour, and the default: everything off the standing allowlist
    # waits for an operator.
    STRICT = "strict"
    # Adds shellscan-passing `Bash`, and nothing else. Writes, spawns, messages,
    # `WebFetch`, `WebSearch` and unknown tools park as they do under `STRICT`.
    # Pinned row by row by test_the_corpus_under_permissive.
    #
    # Egress stays denied because `WebFetch` pairs with an admitted `cat` of any
    # absolute path into an unattended read-then-send. Context still reaches the
    # API, as it does under `STRICT` (2026-09-03 §8b.8).
    #
    # Deliberately not paired with containment, and the reason is a cost
    # judgement rather than a claim that containment buys nothing. What the
    # sandbox bounds is writes and network, which this rung already refuses
    # syntactically. On the read axis it adds exactly one thing: a deny at
    # `sandbox.DENIED_CREDENTIAL_FILES`' four prefixes, since upstream's sandbox
    # read policy is the rest of the computer. Four paths against a rung whose
    # read reach is the filesystem, and whether that key is honoured at all is
    # unmeasured -- 2026-09-03 §11 U1's residue, with
    # `scripts/verify_read_bound.py`'s K arms as the instrument and no recorded
    # run. Against that sits a real operator cost: an X server connection and a
    # venv interpreter both break under the sandbox, which is what this rung
    # exists to survive.
    #
    # The two routes do not share a downstream enforcer, so no parity argument
    # is available here. Measured 2026-09-23: the CLI's `Read` refuses
    # `/proc/self/environ` outright while an admitted `cat` returns it, so this
    # rung reaches bytes `STRICT` cannot (2026-09-03 §12 U11). That is a reach
    # containment would not close either -- the sandbox's read policy covers
    # `/proc` -- which is why it is recorded rather than answered by flipping
    # `requires_containment`.
    #
    # Rungs above this one carry their warning in their own name.
    PERMISSIVE = "permissive"
    # Named for the mode rather than for a tool, because what it releases is the
    # whole of ``REVIEW``: under it the set of calls that would have waited for a
    # human is empty, and the only thing still reaching ``REQUIRE_APPROVAL`` is a
    # tool nobody has heard of.
    #
    # Containment under it is no longer the allowlist's width. ``Bash`` and its
    # children are bounded by the CLI's sandbox; the write tools run inside the CLI
    # process, which that sandbox does not cover, and are bounded instead to the
    # session's directory by the driver's gate. The two halves are one decision --
    # widening here without that write-region check leaves the CLI-process writers
    # unbounded, which is why `driver.AgentSession` refuses to start under this rung
    # with no containment configured. ``WebFetch``/``WebSearch`` are bounded by
    # neither, and are released anyway: what a URL and a prompt can carry out is
    # small beside an unattended agent that cannot read documentation.
    AUTONOMOUS = "autonomous"


def requires_containment(policy: Policy) -> bool:
    """
    Whether a session may not start under this rung without a containment settings blob.

    The ladder's cap, expressed as a property of the rung rather than as a name
    checked at the one place that enforces it. A rung releases writes and spawns
    only by taking this on: the allowlist stops being what bounds the session, so
    something else has to be, and the sandbox is the only other thing there is.

    ``test_a_rung_that_releases_writes_pays_for_it_with_containment`` is what makes
    that a rule rather than a description -- it reads both answers off this module,
    so a fourth rung that released writes and returned False here would fail before
    it could put a reassuring line on the launcher.

    ``PERMISSIVE`` returns False and is not an oversight, but it rests on a cost
    judgement rather than on containment being free of value. What the sandbox bounds
    is writes and network, which that rung already refuses syntactically. Reads are
    the axis it opens, and containment bounds those only at
    ``sandbox.DENIED_CREDENTIAL_FILES``' four prefixes -- narrow but not nothing, and
    itself unmeasured (2026-09-03 §11 U1). Set against an X server connection and a
    venv interpreter that stop working under the sandbox, the rung declines the trade
    and states its cost rather than claiming there is none.

    **The reach this leaves open is real and is recorded at §12 U11**: an admitted
    ``cat`` returns ``/proc/self/environ``, which the CLI's ``Read`` refuses. Flipping
    this to True would not close it, because the sandbox's read policy is the whole
    computer minus those four prefixes and ``/proc`` is not among them. So U11 is a
    reason to know what the rung reaches, not a reason to change this answer.
    """
    match policy:
        case Policy.STRICT:
            return False
        case Policy.PERMISSIVE:
            return False
        case Policy.AUTONOMOUS:
            return True
    assert_never(policy)


def inherits_to_subagents(policy: Policy) -> bool:
    """
    Whether a sub-agent is gated by its session's policy or falls back to ``STRICT``.

    A property of the rung, so it lives beside the rung rather than in the driver:
    it is decided by what the rung rests on, and ``assert_never`` makes a third
    widened rung say which answer it takes.

    Both widened rungs inherit, and they get there by different routes because they
    rest on different things.

    ``AUTONOMOUS`` inherits because sandbox configuration is per CLI process and a
    sub-agent shares its parent's, so each additional agent has the same bounded
    reach as the first (2026-09-03 §8, reversing 2026-08-11 §4 for that rung).

    ``PERMISSIVE`` inherits because its claim is per *command*: ``shellscan`` is
    re-run, fail-closed, against every call a sub-agent makes, exactly as it is for
    the root. Nothing about that verdict weakens with fan-out. 2026-08-11 §4 argued
    the other way on the grounds that one approval would relax the gate for an
    unbounded number of downstream calls, and that is no longer the shape of the
    tree: ``Task``/``Agent`` stay in ``REVIEW`` under this rung, so every agent
    costs its own approval, and ``subagent_cap``'s deny runs ahead of ``classify``
    where no policy can widen it (2026-09-24, amending 2026-09-17 §4).

    What fan-out multiplies under either rung is volume, and the volume that matters
    here is calls that auto-approve and so construct no ``PendingApproval``: they are
    in the transcript but are not reviewable as decisions. ``subagent_cap`` is the
    only thing bounding that, since the automatic revoke was removed on 2026-09-21.

    A property of the rung, so it lives beside the rung rather than in the driver:
    it is decided by what the rung rests on, and ``assert_never`` makes a fourth rung
    say which answer it takes rather than inheriting one by position.
    """
    match policy:
        case Policy.STRICT:
            return False
        case Policy.PERMISSIVE:
            return True
        case Policy.AUTONOMOUS:
            return True
    assert_never(policy)
