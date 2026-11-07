"""
What a node in a snapshot is called: the address its own session's bus routes to
it (``address_in_session``), and the name a board gives it (``role_name``), which
qualifies that address by the board asking.

**Named for what it requires: a ``Snapshot``.** ``naming`` holds the allocator
and the root's names over string maps and imports nothing from pptmstr; these
read the records, the restored board and the launched template, so they cannot
live there without ``naming`` importing ``model``. They are not the board's
either: the board log's records (``boardfacts``) are written with
``address_in_session`` and the board's rows (``board.rows``) are drawn with
``role_name``, and this module is what the two halves share instead of an edge
between them.

Pure: every function here reads one ``Snapshot`` and nothing else -- no clock,
no IO. Imports no imgui.
"""

from __future__ import annotations

from .model import EARLIER_RUN, NodeId, Snapshot, is_address_only, is_operator
from .naming import LEAD, address_for, reserved
from .templates import by_name

# What a node with no record in the snapshot is called.
#
# Two different situations reach this and the snapshot cannot tell them apart: a
# node `AgentRemoved` pruned (which leaves the tasks it claimed behind), and one
# that has not spawned yet (the store files a concern to an unknown recipient
# deliberately -- store.py's ConcernPosted arm). Neither leaves a record, so a
# label reading "gone" or "not started" would assert a cause on a coin flip. It
# says only what is known: nothing here answers to this id.
UNKNOWN = "(unknown)"

# What qualifies a node belonging to a different session than the board showing it.
# Reachable because the task map is fleet-wide and ``store._pick_claim`` applies no
# session filter, so a worker in one session can claim work another declared.
#
# It says "another session" rather than anything suggesting the node is unknown or
# broken, because it is neither: it is a real agent doing real work, under a role
# name that is correct in its own session and misleading in this one.
FOREIGN = "another session"


# Who replaced a specification when no agent did, and who sent a concern the
# store filed on the operator's action (``model.operator_party``).
OPERATOR = "the operator"


def role_name(snap: Snapshot, node: NodeId, session_id: str) -> str:
    """
    What to call a node on ``session_id``'s board.

    The root of a session is the lead by construction. Anything else renders the
    address its own bus answers to -- ``agent_type`` lowercased for the first agent
    in a role, ``builder-2`` for the second -- so an "Explore" record and the
    "explore" an agent must type to reach it read as one name, and two builders read
    as two.

    ``session_id`` is required rather than inferred because a role name is only
    meaningful relative to a board. Every node here is a role of *its own*
    session. ``store._pick_claim`` and the restore both keep a task among its
    own session's agents, so a node from elsewhere on this board is a snapshot
    the store did not build -- and it would otherwise render as this team's own
    member. "lead" is the worst case: it is the one name an operator would never
    think to doubt.

    An agent of an earlier run of the session is ``builder-2, from an earlier
    run`` and never ``builder-2`` (``Snapshot.of_earlier_run``), whether a
    restored board names it or this process still has its record. The
    qualifier says the claim or the mail is not waiting on anything that is
    running, and it comes off the moment that agent is started again.
    """
    base = _role_within_session(snap, node)
    if node[0] != session_id:
        # Named, not hidden. The row is real and someone is working it; what it
        # must not do is claim to be one of this board's own agents.
        return f"{base}, {FOREIGN}"
    return base


def _role_within_session(snap: Snapshot, node: NodeId) -> str:
    """The name a node answers to inside its own session, ignoring which board asked."""
    if node[1] is None:
        return LEAD
    if is_operator(node):
        # The sender of the store's notice to a holder whose task the operator
        # closed (``model.operator_party``). It has no record and no address by
        # design, and a restored board names it like any other party, so every
        # test below would call it an agent nobody can name, of an earlier run.
        return OPERATOR
    address = address_in_session(snap, node) or UNKNOWN
    return f"{address}, {EARLIER_RUN}" if snap.of_earlier_run(node) else address


def address_in_session(snap: Snapshot, node: NodeId) -> str:
    """
    The address ``node`` has inside its own session and nothing after it, or the
    empty string when the snapshot has no name for it.

    What ``role_name`` qualifies and what a log record is written with
    (``boardfacts``): a qualifier belongs to a reading of the board at one
    moment, and an address is the name.

    A node with no record has the address its session's restored board had for
    it, when one names it.
    """
    if node[1] is None:
        return LEAD
    record = snap.get(node)
    if record is None:
        return snap.left_behind(node) or ""
    if not record.agent_type:
        # Deliberately not `record.task`: a sub-agent's task is a prompt, and the
        # rail's `agent_type or task` fallback works only because a sub-agent
        # always has a type. A record without one is not worth a paragraph here.
        return node[1]
    return _addresses(snap, node[0]).get(node, record.agent_type.lower())


def restored_table(snap: Snapshot, session_id: str) -> dict[str, str | None]:
    """
    The addresses ``session_id``'s restored board names, as ``reserved`` makes a
    table of them: what ``_addresses`` starts from. A party the log knew by
    address alone has a stand-in for a node and no agent id.

    Equal to what ``app._addresses_of`` makes of the log the restore came from,
    which is what the session's driver starts from. That is two derivations of
    one table and it is pinned by test, not by construction: the store builds
    ``Restore.parties`` from the log and this reads it back.
    """
    restore = snap.restores.get(session_id)
    return reserved(
        (address, None if is_address_only(node) else node[1])
        for node, address in (() if restore is None else restore.parties.items())
    )


def _addresses(snap: Snapshot, session_id: str) -> dict[NodeId, str]:
    """
    Every sub-agent of one session that has a record, under the address the bus
    routes to it.

    Derived, not stored: the driver allocates an address per sub-agent in spawn
    order with ``address_for``, and this asks ``address_for`` again in the order
    the snapshot holds the records (``Snapshot.subagents_of``), starting from the
    table the driver started from -- the addresses the session's restored board
    names (``Restore.parties``; ``app._addresses_of`` builds the driver's from
    the same log).

    **Where it cannot agree**, because the two tables or the two orders differ:

    - ``AgentRemoved`` prunes a record; the driver's table keeps the address, and
      every later ordinal here would shift. Nothing emits that intent (its
      docstring asks that it stay that way).
    - A restore the store refused whole leaves ``Restore.parties`` without the
      log's addresses, while the driver was constructed with them before the
      store had answered.
    - A session relaunched over a run that is still ending copies that run's
      table when it is constructed. A sub-agent the ending run starts after
      that has a record here and no entry there.

    The fix for all three is the address moving onto the record, carried by
    ``AgentSpawned``, instead of being recomputed from what the snapshot holds.
    """
    # ``address_for``'s last rule -- a declared role's name is stepped over --
    # needs the template, which the root record carries by name.
    root = (session_id, None)
    root_record = snap.get(root)
    template = by_name(root_record.template or "") if root_record is not None else None
    declared = frozenset(template.role_names()) if template is not None else frozenset()

    held = restored_table(snap, session_id)
    out: dict[NodeId, str] = {}
    for record in snap.subagents_of(root):
        agent_id = record.node_id[1]
        if agent_id is None or not record.agent_type:
            continue
        address = address_for(agent_id, record.agent_type, held, declared)
        held[address] = agent_id
        out[record.node_id] = address
    return out
