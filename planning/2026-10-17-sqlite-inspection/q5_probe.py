#!/usr/bin/env python3
"""
Q5: S3-h4-skeptic's needs-a-run items A, B, C and E, against this worktree's
pptmstr modules, with every log on disk under a temporary directory.

usage:
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<worktree> \\
        /home/wreel/Source/pptmstr/.venv/bin/python q5_probe.py [--trials N]

A  a record about a thing its log never introduced: stray, short, unadoptable,
   and still so on every later restore (S3 F1). Today's kinds only.
B  a resumed session's announce and its BoardRestored, emitted as ``_launch``
   emits them (``AgentSpawned`` then the real ``app._open_board_log``), against
   a draw loop that drains and applies as ``app`` does: how often do they land in
   separate batches, and what does the snapshot between them hold (S3 F2).
C  an operator intent emitted from another thread while B runs: how often does
   it land between the announce and the restore, and, when it carries a fact for
   the resumed session (as an H4 link record would), what ``_record_board`` does
   with it (S3 F9(v)). Today no operator intent yields a fact for another
   session, so the fact is injected by wrapping ``boardfacts.outcomes``; that
   wrapper is the only stand-in here and it models the H4 link fact.
E  the price of a synchronous whole read of 1, 10 and 100 channel logs, as H2(a)
   would do it at the ``_migrate_board_logs`` slot (S3 F5). Warm page cache only.

Asserts each claim it checks, so a refuted claim is a traceback naming it.
"""

from __future__ import annotations

import argparse
import os
import statistics
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pptmstr import app, boardfacts, boardlog, boardsync, bus
from pptmstr.boardlog import Clock
from pptmstr.bridge import Bridge
from pptmstr.intents import AgentSpawned, BoardRestored, DecisionDismissed, Intent
from pptmstr.effects import SessionWoken, WakeReason
from pptmstr.model import NodeId, Snapshot
from pptmstr.store import Store
from pptmstr.ui import board_pane


def clock() -> Clock:
    return Clock(wall=time.time(), monotonic=time.monotonic())


def spawn(node: NodeId) -> AgentSpawned:
    return AgentSpawned(
        node_id=node,
        parent=None,
        task="lead",
        model="m",
        started_at=time.monotonic(),
        agent_type=None,
        cwd="/w",
    )


def write(root: Path, session_id: str, records: list[boardlog.Record]) -> None:
    writer = boardsync.BoardWriter(root, root / "legacy")
    for record in records:
        writer.submit(session_id, record)
    for report in writer.close(timeout=10.0):
        assert isinstance(report, boardsync.Outcome) and report.kept, report


def restore(store: Store, root: Path, session_id: str) -> None:
    found = boardsync.find(session_id, root, root / "legacy")
    assert isinstance(found.board, boardlog.Board), found.board
    reading = clock()
    store.apply(BoardRestored(session_id, found.board, reading), now=reading.monotonic)


# -- A -------------------------------------------------------------------------


def probe_a(tmp: Path) -> None:
    print("== A. a record about something the log never introduced")
    root = tmp / "a-sessions"
    wall = time.time()
    cases: dict[str, list[boardlog.Record]] = {
        # An orphan of each family the fold matches on. Under H4 a link-kind
        # orphan (unlink X in a log that never saw link X) is the same shape.
        "claimed, never declared": [boardlog.Claimed(wall, "t-never", "builder")],
        "edited, never posted": [boardlog.Edited(wall, "c-never", "body")],
        "delivered, never posted": [boardlog.Delivered(wall, ("c-never",))],
    }
    for label, records in cases.items():
        sid = str(uuid.uuid4())
        write(root, sid, records)
        log = boardlog.read(boardsync.BoardWriter(root, root / "legacy").directory(sid))  # type: ignore[arg-type]
        board = boardlog.restore_log(log)
        print(f"  {label}: stray={len(board.stray)} Board.short={board.short}")
        assert len(board.stray) == 1 and board.short

        store = Store()
        store.apply(spawn((sid, None)), now=time.monotonic())
        restore(store, root, sid)
        first = store.snapshot().restores[sid]
        why = board_pane.unrecorded_reason(first)
        print(
            f"    restore 1: log_short={first.log_short} log_stray={first.log_stray}"
            f" short={first.short} adoptable={first.adoptable}"
        )
        print(f"    _settle_restore would say: {why!r}")
        assert first.log_short and not first.adoptable and why

        # Nothing cures it. A process that did not adopt the log may not append
        # to it (P12), and a fresh restore of the unchanged log is short again.
        refuser = boardsync.BoardWriter(root, root / "legacy")
        refuser.submit(sid, boardlog.Declared(wall + 1, "t-later", "later", "lead"))
        (refused,) = [r for r in refuser.close(timeout=10.0) if isinstance(r, boardsync.Outcome)]
        print(f"    a later append by a writer that did not adopt it: kept={refused.kept}")
        assert not refused.kept
        store2 = Store()
        store2.apply(spawn((sid, None)), now=time.monotonic())
        restore(store2, root, sid)
        second = store2.snapshot().restores[sid]
        print(f"    restore 2 (fresh store): adoptable={second.adoptable}")
        assert not second.adoptable

    # S3 F10's trap: the same board handed over twice leaves the snapshot as it
    # was, because ``_restored`` returns None when the report equals the prior.
    sid = str(uuid.uuid4())
    write(root, sid, [boardlog.Declared(wall, "t-a5", "x", "lead")])
    store = Store()
    store.apply(spawn((sid, None)), now=time.monotonic())
    restore(store, root, sid)
    before = store.snapshot()
    restore(store, root, sid)
    after = store.snapshot()
    print(
        f"  same log restored twice: restores unchanged={after.restores == before.restores}"
        f" tasks unchanged={after.tasks == before.tasks}"
    )
    assert after.restores == before.restores and after.tasks == before.tasks


# -- B and C -------------------------------------------------------------------


class Frames:
    """
    The draw loop's half of ``app._frame`` that matters here: drain, then
    ``apply_all`` under the real ``_record_board`` observer, as one batch. Each
    batch's intents and the snapshot after it are kept.
    """

    def __init__(self, state: Any, writer: boardsync.BoardWriter) -> None:
        self.state = state
        self.observe = app._record_board(state, writer)
        self.batches: list[tuple[list[Intent], Snapshot]] = []
        self.stop = threading.Event()

    def once(self) -> None:
        drained = self.state.bridge.drain()
        if drained:
            self.state.store.apply_all(drained, now=time.monotonic(), observe=self.observe)
            self.batches.append((drained, self.state.store.snapshot()))

    def run(self) -> None:
        while not self.stop.is_set():
            self.once()
        self.once()


def resumed_state(tmp: Path) -> tuple[Any, boardsync.BoardWriter]:
    writer = boardsync.BoardWriter(tmp / "b-sessions", tmp / "b-legacy")
    state = SimpleNamespace(store=Store(), bridge=Bridge(), board_writer=writer, board_logs={})
    return state, writer


def resume(state: Any, sid: str, board: boardlog.Board) -> None:
    """What ``_launch.start`` does from ``pool.submit`` on, with no await."""
    state.bridge.emit(spawn((sid, None)))  # ``AgentSession.announce``
    app._open_board_log(state, sid, app._Logged(board=board, clock=clock()))


def probe_b(tmp: Path, trials: int) -> None:
    print("== B. announce and restore: one batch or two?")
    board = boardlog.Board()
    split = 0
    between_seen = 0
    for i in range(trials):
        state, writer = resumed_state(tmp / f"b{i}")
        frames = Frames(state, writer)
        drawer = threading.Thread(target=frames.run)
        drawer.start()
        sid = str(uuid.uuid4())
        resume(state, sid, board)
        frames.stop.set()
        drawer.join()
        writer.close(timeout=5.0)
        where = {}
        for n, (intents, snap) in enumerate(frames.batches):
            for intent in intents:
                where[type(intent).__name__] = n
            if snap.get((sid, None)) is not None and sid not in snap.restores:
                between_seen += 1
        if where["AgentSpawned"] != where["BoardRestored"]:
            split += 1
        entry = state.board_logs[sid]
        assert entry.recording and not entry.awaiting, entry
    print(
        f"  {trials} resumes: announce and restore in separate batches {split} time(s);"
        f" a snapshot with the root present and no restore report {between_seen} time(s)"
    )
    print("  every resumed session ended recorded (adopted)")

    # Deterministic: the split, forced, and what a reader sees between.
    state, writer = resumed_state(tmp / "b-forced")
    frames = Frames(state, writer)
    sid = str(uuid.uuid4())
    state.bridge.emit(spawn((sid, None)))
    frames.once()
    mid = state.store.snapshot()
    print(
        f"  forced split, after the announce batch: root present={mid.get((sid, None)) is not None}"
        f" restores[sid] present={sid in mid.restores}"
        f" board_logs entry present={sid in state.board_logs}"
    )
    assert mid.get((sid, None)) is not None and sid not in mid.restores
    app._open_board_log(state, sid, app._Logged(board=board, clock=clock()))
    frames.once()
    assert state.board_logs[sid].recording
    writer.close(timeout=5.0)

    # What a wake carries today: a pointer, no content.
    line = bus.wake_line(
        mid,
        SessionWoken(
            session_id=sid, reason=WakeReason.CONCERN_TO_LEAD, subject_id="c-x", sender=None
        ),
    )
    print(f"  a concern wake's text: {line!r}")
    assert "read_inbox()" in line


def probe_c(tmp: Path, trials: int) -> None:
    print("== C. an operator intent landing between a resume's announce and restore")
    board = boardlog.Board()
    marker = "link-X"
    original = boardfacts.outcomes
    target: dict[str, str] = {}

    def with_link_fact(before: Snapshot, intent: Intent, after: Snapshot, now: float):  # type: ignore[no-untyped-def]
        out = original(before, intent, after, now)
        if isinstance(intent, DecisionDismissed) and intent.decision_id == marker:
            # The stand-in for an H4 link record: one fact for the member that
            # is being resumed, from an act that is not that session's.
            out = (*out, boardfacts.Fact(target["sid"], boardlog.Edited(now, "c-link", "x")))
        return out

    boardfacts.outcomes = with_link_fact  # type: ignore[assignment]
    try:
        # Deterministic: announce, the operator's act, then the restore, in one
        # batch. The entry is awaiting when the act's fact is observed.
        state, writer = resumed_state(tmp / "c-forced")
        frames = Frames(state, writer)
        sid = str(uuid.uuid4())
        target["sid"] = sid
        state.bridge.emit(spawn((sid, None)))
        state.bridge.emit(DecisionDismissed(decision_id=marker))
        # The entry is written by ``_open_board_log`` before it emits, so the
        # observer sees it awaiting whichever batch the act lands in.
        app._open_board_log(state, sid, app._Logged(board=board, clock=clock()))
        frames.once()
        entry = state.board_logs[sid]
        print(
            f"  forced order (announce, act, restore): recording={entry.recording}"
            f" why_not={entry.why_not!r}"
        )
        assert not entry.recording and "before its log had been applied" in entry.why_not
        writer.close(timeout=5.0)

        # Raced: the act is emitted from a second thread at a random point. Each
        # run ends one of three ways for the resumed member: its log gets the
        # fact; the fact is skipped because the observer saw no entry yet (the
        # act was applied in a batch before ``_open_board_log`` wrote one), and
        # the member is then adopted without it; or the entry was awaiting, and
        # the member runs unrecorded for the process.
        tally = {"written": 0, "skipped, then adopted": 0, "unrecorded": 0}
        for i in range(trials):
            state, writer = resumed_state(tmp / f"c{i}")
            frames = Frames(state, writer)
            drawer = threading.Thread(target=frames.run)
            sid = str(uuid.uuid4())
            target["sid"] = sid
            go = threading.Barrier(2)

            def operator() -> None:
                go.wait()
                state.bridge.emit(DecisionDismissed(decision_id=marker))

            op = threading.Thread(target=operator)
            drawer.start()
            op.start()
            go.wait()
            resume(state, sid, board)
            op.join()
            frames.stop.set()
            drawer.join()
            reports = writer.close(timeout=5.0)
            wrote = any(
                isinstance(r, boardsync.Outcome)
                and r.session_id == sid
                and isinstance(r.record, boardlog.Edited)
                and r.kept
                for r in reports
            )
            entry = state.board_logs[sid]
            if not entry.recording:
                tally["unrecorded"] += 1
            elif wrote:
                tally["written"] += 1
            else:
                tally["skipped, then adopted"] += 1
        print(f"  {trials} raced resumes, the member's outcome: {tally}")
        assert sum(tally.values()) == trials
    finally:
        boardfacts.outcomes = original  # type: ignore[assignment]


# -- E -------------------------------------------------------------------------


def probe_e(tmp: Path) -> None:
    print("== E. a synchronous whole read of N channel logs (warm cache)")
    wall = time.time()
    for per_log in (10, 200):
        for count in (1, 10, 100):
            root = tmp / f"e-{per_log}-{count}"
            dirs = []
            for n in range(count):
                directory = root / f"chan-{n}" / "board"
                directory.mkdir(parents=True)
                for k in range(per_log):
                    rec = boardlog.Posted(
                        wall + k, f"c-{n}-{k}", "lead", "peer", "subject " * 4, "body " * 40
                    )
                    written = boardlog.append(directory, rec, durable=False)
                    assert isinstance(written, boardlog.Written), written
                dirs.append(directory)
            runs = []
            for _ in range(7):
                start = time.perf_counter()
                for directory in dirs:
                    board = boardlog.restore_log(boardlog.read(directory))
                    assert len(board.concerns) == per_log and not board.short
                runs.append(time.perf_counter() - start)
            print(
                f"  {count:>3} log(s) x {per_log:>3} records: median"
                f" {statistics.median(runs) * 1000:8.2f} ms  (min {min(runs) * 1000:.2f},"
                f" max {max(runs) * 1000:.2f}; 7 runs)"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=500)
    args = parser.parse_args()
    base = os.environ.get("TMPDIR") or None
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        probe_a(Path(tmp))
        probe_b(Path(tmp), args.trials)
        probe_c(Path(tmp), args.trials)
        probe_e(Path(tmp))
    print("ALL ASSERTIONS HELD")


if __name__ == "__main__":
    main()
