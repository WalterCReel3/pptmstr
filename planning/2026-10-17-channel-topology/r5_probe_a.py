#!/usr/bin/env python3
"""
S1 needs-a-run item A: a concern from one session's lead to another session's lead,
read, written to disk through the real ``BoardWriter``, and restored through
``boardsync.find`` and ``BoardRestored`` into a fresh store.

usage: python r5_probe_a.py   (from the repository root, with pptmstr importable)

Writes only under a temporary directory it makes and removes. Asserts each claim
of S1 finding 1 and prints what it saw, so a failed claim is a traceback naming it.
The address is made in the store, not through the bus: no bus tool can name another
session's lead today (``AgentSession.resolve_role`` is session-local), so this
tests the record and the restore, not routing.
"""

from __future__ import annotations

import tempfile
import time
import uuid
from pathlib import Path

from pptmstr import boardfacts, boardlog, boardsync
from pptmstr.boardlog import Clock
from pptmstr.intents import AgentSpawned, BoardRestored, ConcernPosted, InboxRead, Intent
from pptmstr.model import Concern, ConcernState, NodeId, Snapshot
from pptmstr.store import Store, _apply

A = str(uuid.uuid4())
B = str(uuid.uuid4())
A_LEAD: NodeId = (A, None)
B_LEAD: NodeId = (B, None)


def spawn(node: NodeId, at: float) -> AgentSpawned:
    return AgentSpawned(
        node_id=node, parent=None, task="lead", model="m", started_at=at, agent_type=None, cwd="/w"
    )


def drive(
    snap: Snapshot, intents: list[Intent], now: float
) -> tuple[Snapshot, list[boardfacts.Fact]]:
    facts: list[boardfacts.Fact] = []
    for intent in intents:
        after, _ = _apply(snap, intent, now)
        facts.extend(boardfacts.outcomes(snap, intent, after, now))
        snap = after
    return snap, facts


def write(root: Path, facts: list[boardfacts.Fact]) -> None:
    clock = Clock(wall=time.time(), monotonic=time.monotonic())
    writer = boardsync.BoardWriter(root, root / "legacy")
    for fact in facts:
        writer.submit(fact.session_id, fact.on_the_wall(clock))
    reports = writer.close(timeout=10.0)
    for r in reports:
        assert isinstance(r, boardsync.Outcome) and r.kept, r
    print(f"  wrote {len(reports)} record(s): {[type(r.record).__name__ for r in reports]}")


def restore_into(root: Path, sessions: list[str], present: list[NodeId]) -> Snapshot:
    store = Store()
    for node in present:
        store.apply(spawn(node, 1.0), now=1.0)
    clock = Clock(wall=time.time(), monotonic=time.monotonic())
    for sid in sessions:
        found = boardsync.find(sid, root, root / "legacy")
        if isinstance(found.board, boardsync.NoLog):
            print(f"  {sid[:8]}: NoLog ({found.board.reason})")
            continue
        store.apply(BoardRestored(sid, found.board, clock), now=clock.monotonic)
    return store.snapshot()


def report(snap: Snapshot, sid: str, label: str) -> None:
    r = snap.restores.get(sid)
    if r is None:
        print(f"  {label}: no restore report")
        return
    print(
        f"  {label}: concerns={r.concerns} unplaced={r.unplaced} short={r.short}"
        f" adoptable={r.adoptable}"
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "sessions"
        snap = Snapshot.empty()
        snap, _ = _apply(snap, spawn(A_LEAD, 0.0), 0.0)
        snap, _ = _apply(snap, spawn(B_LEAD, 0.0), 0.0)
        concern = Concern("c-x", A_LEAD, B_LEAD, "subject", "body", posted_at=3.0)

        print("== A1. post A-lead -> B-lead; check B's inbox before the read")
        snap, posted = drive(snap, [ConcernPosted(A_LEAD, concern)], now=3.0)
        assert [c.id for c in snap.inbox_of(B_LEAD)] == ["c-x"]
        print(f"  inbox_of(B lead) = {[c.id for c in snap.inbox_of(B_LEAD)]}")
        print(f"  facts: {[(f.session_id == A, type(f.record).__name__) for f in posted]}")
        assert all(f.session_id == A for f in posted)

        print("== A2. B reads; where does the receipt go")
        snap, read = drive(snap, [InboxRead(B_LEAD, "rq", 6.0)], now=6.0)
        print(f"  facts: {[(f.session_id == A, type(f.record).__name__) for f in read]}")
        assert [type(f.record) for f in read] == [boardlog.Delivered]
        assert all(f.session_id == A for f in read)
        assert snap.concerns["c-x"].state is ConcernState.DELIVERED

        write(root, posted + read)
        b_dir = boardsync.BoardWriter(root).directory(B)
        print(f"  B's log directory exists: {b_dir is not None and b_dir.exists()}")

        print("== A3. restore A's log into a fresh store that holds B's root too")
        after = restore_into(root, [A], [A_LEAD, B_LEAD])
        c = after.concerns["c-x"]
        print(
            f"  placed: sender={c.sender == A_LEAD} recipient={c.recipient == B_LEAD}"
            f" state={c.state.name} delivered_at set={c.delivered_at is not None}"
        )
        assert c.recipient == B_LEAD and c.state is ConcernState.DELIVERED
        assert c.delivered_at is not None
        report(after, A, "A's restore")
        assert after.restores[A].unplaced == () and not after.restores[A].short
        assert after.restores[A].adoptable

        print("== A4. the undelivered case: post, no read, restore A only, B's root present")
        root2 = Path(tmp) / "sessions2"
        s2 = Snapshot.empty()
        s2, _ = _apply(s2, spawn(A_LEAD, 0.0), 0.0)
        s2, _ = _apply(s2, spawn(B_LEAD, 0.0), 0.0)
        s2, f2 = drive(s2, [ConcernPosted(A_LEAD, concern)], now=3.0)
        write(root2, f2)
        after2 = restore_into(root2, [A], [A_LEAD, B_LEAD])
        print(f"  inbox_of(B lead) after restoring A = {[x.id for x in after2.inbox_of(B_LEAD)]}")
        assert [x.id for x in after2.inbox_of(B_LEAD)] == ["c-x"]
        report(after2, A, "A's restore")
        assert after2.restores[A].adoptable

        print("== A5. same log, but only B is resumed (A's log never restored)")
        after3 = restore_into(root2, [B], [B_LEAD])
        print(
            f"  inbox_of(B lead) = {[x.id for x in after3.inbox_of(B_LEAD)]}  (held message invisible)"
        )
        assert after3.inbox_of(B_LEAD) == ()
        report(after3, B, "B's restore")

        print("== A6. restore A's log where B's root is NOT in the store")
        after4 = restore_into(root2, [A], [A_LEAD])
        c4 = after4.concerns.get("c-x")
        print(
            f"  placed={c4 is not None} recipient is B lead={c4 is not None and c4.recipient == B_LEAD}"
        )
        report(after4, A, "A's restore")
        assert after4.restores[A].adoptable

    print("ALL ASSERTIONS HELD")


if __name__ == "__main__":
    main()
