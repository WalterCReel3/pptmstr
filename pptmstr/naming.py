"""
The addresses a session's agents answer to: what the root is called, and the one
allocator every sub-agent's address comes from.

Pure: no clock, no IO, and no import from pptmstr. That is what lets the driver,
the board log and the board share it without any of them importing the others,
and it is held by ``tests/test_layers.py``.

What a node in a snapshot is called on a board (``board.role_name``) is not here.
It reads a ``Snapshot``, and ``model`` reaches ``board`` through ``approval``'s
lazy imports, so a module that imports ``model`` cannot be one that ``board``'s
importers use to avoid ``board``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping

# What the root session of a team is called. Not read from the record: the driver
# emits the root's `AgentSpawned` with no `agent_type` at all, so the usual
# `record.agent_type or record.task` idiom would put the operator's entire task
# prompt in a name column. `role_of` answers "lead" for this node, and an agent_id
# of None is the same fact without reaching into the driver.
LEAD = "lead"

# The names that mean the root of a session and so are never a sub-agent's address.
# Here and not in the driver, which imports this: the driver pulls in the agent SDK,
# and this module is testable without a runtime.
ROOT_NAMES = (LEAD, "main", "root")


def address_for(
    agent_id: str,
    agent_type: str,
    held: Mapping[str, str | None],
    declared: Collection[str],
) -> str:
    """
    The address a sub-agent answers to. **The one place that is decided.**

    ``held`` is every address of the session that is spoken for, with the agent
    id it belongs to: the ones allocated so far in this process, and the ones an
    earlier process's log names (``reserved``). The driver asks this when a
    sub-agent starts and routes mail by the answer; ``board._addresses`` asks it
    once per record in spawn order and that is what every board read, the pane
    and the log's records show. Two callers of one function over the same table
    and the same order cannot name an agent differently, and neither has a rule
    of its own to drift.

    An id ``held`` already names gets that address back, wherever in the order it
    starts: a sub-agent woken under its id keeps its name, in this process or in
    one that restored the log.

    Otherwise the first agent of a type takes the bare role name and later ones
    ``builder-2``, ``builder-3`` -- the convention ``templates.lead_briefing``
    hands the lead. A candidate that is in ``held``, that is one of the root's
    names, or that another role in the template is called (``declared``) is
    stepped over: an address in ``held`` under another id, or under none, is
    never handed out again.
    """
    for address, holder in held.items():
        if holder == agent_id:
            return address
    base = agent_type.strip().lower() or "agent"
    ordinal = 1
    while True:
        candidate = base if ordinal == 1 else f"{base}-{ordinal}"
        spoken_for = (
            candidate in held
            or candidate in ROOT_NAMES
            or (candidate != base and candidate in declared)
        )
        if not spoken_for:
            return candidate
        ordinal += 1


def reserved(named: Iterable[tuple[str, str | None]]) -> dict[str, str | None]:
    """
    The table ``address_for`` is given, from ``(address, agent id)`` pairs in any
    order: each address once, as an agent types it, with the one agent id it
    belongs to.

    The id is None where no pair gives one, and **None where two pairs give
    different ones**. An address two agents were written under belongs to
    neither as far as anything here can tell, so neither is handed it back and
    nobody new takes it. Deciding by which pair came first would make the answer
    depend on the order of the source, and the two sources of one session's
    table -- a log's ``Board.addresses`` and the ``Restore.parties`` the store
    made of the same log -- do not share an order.

    An empty address and the root's names are left out: nothing is ever
    allocated either, so neither is a thing to reserve.
    """
    ids: dict[str, set[str]] = {}
    for address, agent_id in named:
        key = address.strip().lower()
        if not key or key in ROOT_NAMES:
            continue
        known = ids.setdefault(key, set())
        if agent_id is not None:
            known.add(agent_id)
    return {key: next(iter(known)) if len(known) == 1 else None for key, known in ids.items()}
