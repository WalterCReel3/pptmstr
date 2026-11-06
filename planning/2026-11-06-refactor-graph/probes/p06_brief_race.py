"""
Claim 6: brief.write_entry can lose an entry when two writers race on one
directory at the same ordinal (exists() check, then os.replace onto the name).

Three runs, each on fresh directories under the probe's temp HOME/XDG:
  A. two threads, unmodified write_entry, released together by a barrier, N trials
  B. two processes, unmodified write_entry, released together by a barrier, N trials
  C. forced interleaving: two threads, with tempfile.mkstemp (called after the
     exists() loop and before os.replace) wrapped to wait on a barrier, so both
     writers are known to have passed the check before either renames. This shows
     the window exists; A and B show how often it is hit without help.
Each trial pre-seeds one entry (000-premises), so both writers race for ordinal 001
as amendments. A trial "loses" when fewer than 3 entries are on disk afterwards,
and the lost body is named.

Usage: p06_brief_race.py [N_threads] [N_procs]   (defaults 2000, 500)
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import tempfile
import threading
from pathlib import Path

import _common
from _common import verdict

from pptmstr import brief

N_THREADS = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
N_PROCS = int(sys.argv[2]) if len(sys.argv) > 2 else 500
BASE = _common.SANDBOX / "p06"
BASE.mkdir()


def fresh(label: str, i: int) -> Path:
    d = BASE / f"{label}-{i:05d}"
    brief.write_entry(d, "seed")
    return d


def outcome(d: Path) -> tuple[int, list[str]]:
    entries = brief.read_entries(d)
    return len(entries), sorted(p.name for p in d.iterdir())


def trial_threads(d: Path) -> list[str]:
    gate = threading.Barrier(2)
    errors: list[str] = []

    def write(body: str) -> None:
        gate.wait()
        try:
            brief.write_entry(d, body)
        except Exception as exc:  # report, do not hide
            errors.append(f"{type(exc).__name__}: {exc}")

    ts = [threading.Thread(target=write, args=(b,)) for b in ("writer-A", "writer-B")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return errors


def _proc_writer(d: str, body: str, gate: "mp.synchronize.Barrier") -> None:  # type: ignore[name-defined]
    gate.wait()
    brief.write_entry(Path(d), body)


def summarise(label: str, results: list[tuple[int, list[str]]], errors: list[str]) -> int:
    lost = [r for r in results if r[0] < 3]
    print(f"  {label}: trials={len(results)} lost={len(lost)} errors={len(errors)}")
    for n, names in lost[:3]:
        print(f"    example loss: {n} entries on disk, files={names}")
    for e in errors[:3]:
        print(f"    error: {e}")
    return len(lost)


print(f"== A. threads, unmodified, N={N_THREADS}")
res, errs = [], []
for i in range(N_THREADS):
    d = fresh("thr", i)
    errs += trial_threads(d)
    res.append(outcome(d))
lost_a = summarise("threads", res, errs)

print(f"\n== B. processes, unmodified, N={N_PROCS}")
ctx = mp.get_context("fork")
res = []
errs = []
for i in range(N_PROCS):
    d = fresh("proc", i)
    gate = ctx.Barrier(2)
    ps = [ctx.Process(target=_proc_writer, args=(str(d), b, gate)) for b in ("writer-A", "writer-B")]
    for p in ps:
        p.start()
    for p in ps:
        p.join()
        if p.exitcode != 0:
            errs.append(f"exit {p.exitcode}")
    res.append(outcome(d))
lost_b = summarise("processes", res, errs)

print("\n== C. forced interleaving (both past the exists() check before either renames), N=20")
real_mkstemp = tempfile.mkstemp
forced: list[tuple[int, list[str]]] = []
for i in range(20):
    d = fresh("forced", i)
    gate = threading.Barrier(2)

    def held_mkstemp(*a, **k):  # type: ignore[no-untyped-def]
        gate.wait(timeout=5)
        return real_mkstemp(*a, **k)

    brief.tempfile.mkstemp = held_mkstemp  # type: ignore[attr-defined]
    try:
        errs = trial_threads(d)
    finally:
        brief.tempfile.mkstemp = real_mkstemp  # type: ignore[attr-defined]
    forced.append(outcome(d))
lost_c = summarise("forced", forced, errs)
if forced:
    n, names = forced[0]
    texts = [e.body.strip() for e in brief.read_entries(BASE / "forced-00000")]
    print(f"    trial 0 bodies on disk: {texts}")

print()
print("== production writers (by reading, not executed)")
print("  app._seed_brief -> brief.write_entry: once per new launch, into the new session's")
print("    own directory (a fresh session id); not run for a resume. Runs in app._launch's")
print("    coroutine on the loop thread.")
print("  ui/brief_pane._append -> brief.write_entry: the operator's 'append' button, on the")
print("    draw thread. One draw thread per process, so in-process appends are serial.")
print("  fake_driver: three sequential writes into a fresh mkdtemp dir.")
print("  In-process concurrency on ONE directory needs the pane to hold a directory the seed")
print("    is still writing to; session.brief is set only after the seed's write returns, so")
print("    not by reading. Cross-process: two pptmstr windows showing the same brief directory")
print("    (a resumed session in a second window, or two launches given one existing brief")
print("    directory) each with the pane's append pressed together. No single-instance lock")
print("    was found (grep for flock / instance lock). That path was NOT reached here.")
print()
print(f"losses: threads {lost_a}/{N_THREADS}, processes {lost_b}/{N_PROCS}, forced {lost_c}/20")
if lost_c and (lost_a or lost_b):
    verdict("CONFIRMED", "entries are lost without assistance and deterministically under a forced interleaving")
elif lost_c:
    verdict(
        "CONFIRMED (window exists; unassisted rate measured above)",
        "a forced interleaving loses one entry every time; unassisted runs lost "
        f"{lost_a}+{lost_b}",
    )
else:
    verdict("REFUTED", "no loss even under forced interleaving")
