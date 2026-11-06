"""
Claim 9 (plan B3): boardsync.BoardWriter.close vs channels.ChannelWriter.close, line
by line, plus submit / drain / _run, which are near-copies too.

The diffs are difflib over the source segments (docstrings stripped, so only code
lines are compared; a second pass keeps them). The semantic differences listed at
the end are READ from these diffs. One is then EXECUTED: the reason close() gives
the oldest pending record when the worker has NOT begun its append (worker held on
the queue), which is where the _in_flight flag changes the answer.
"""

from __future__ import annotations

import ast
import difflib
import queue
import inspect
import textwrap
import threading
import time

import _common  # noqa: F401
from _common import verdict

from pptmstr import boardsync, channels

METHODS = ["close", "submit", "drain", "_run"]


def code_only(fn) -> list[str]:  # type: ignore[no-untyped-def]
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)
    f = tree.body[0]
    assert isinstance(f, ast.FunctionDef)
    doc = ast.get_docstring(f, clean=False)
    lines = src.splitlines()
    if doc is not None:
        d = f.body[0]
        lines = lines[: d.lineno - 1] + lines[d.end_lineno :]  # type: ignore[operator]
    return lines


total_changed = {}
for m in METHODS:
    a = code_only(getattr(boardsync.BoardWriter, m))
    b = code_only(getattr(channels.ChannelWriter, m))
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    d = list(difflib.unified_diff(a, b, "boardsync.BoardWriter." + m, "channels.ChannelWriter." + m, n=99, lineterm=""))
    changed = sum(1 for x in d if x[:1] in "+-" and not x.startswith(("+++", "---")))
    total_changed[m] = (changed, round(ratio, 3))
    print(f"\n==== {m}: line similarity {ratio:.3f}, {changed} changed line(s)")
    print("\n".join(d) if d else "  (identical)")

print("\n==== executed: close() while the worker has not begun the oldest record's append")
# Hold each writer's worker before it takes the first item, so the record is pending
# and NOT in flight, then close with a short timeout and read the reason it is given.


def held_reason(writer, submit_args, settle_attr: str) -> str:  # type: ignore[no-untyped-def]
    started = threading.Event()
    release = threading.Event()

    class HeldQueue(queue.SimpleQueue):  # type: ignore[type-arg]
        def get(self, *a, **k):  # type: ignore[no-untyped-def]
            started.set()
            release.wait(5)
            return super().get(*a, **k)

    writer._queue = HeldQueue()
    setattr(writer, settle_attr, lambda *a, **k: ())
    writer.submit(*submit_args)
    started.wait(5)
    out = writer.close(timeout=0.05)
    release.set()
    time.sleep(0.05)
    return "; ".join(getattr(r.result, "reason", repr(r)) for r in out)


from pptmstr.boardlog import RecordingStopped  # noqa: E402

bw = boardsync.BoardWriter(root=_common.SANDBOX / "p09-sessions", legacy=_common.SANDBOX / "p09-legacy")
r_board = held_reason(bw, ("sess-x", RecordingStopped(at=0.0, reason="r")), "_settle")
cw = channels.ChannelWriter.__new__(channels.ChannelWriter)
channels.ChannelWriter.__init__(cw, log=None)  # type: ignore[arg-type]
link = channels.LinkKey(name="n", link_id="l")
r_chan = held_reason(cw, (link, object()), "_settle")
print(f"  BoardWriter.close   -> {r_board!r}")
print(f"  ChannelWriter.close -> {r_chan!r}")

print("""
==== semantic differences (READ from the diffs above)
close:
  1. Board distinguishes 'its append had begun' using _in_flight (position 0 AND
     _in_flight). Channel says 'may have begun' for position 0 unconditionally: it has
     no _in_flight, so a record the worker never picked up is reported as possibly
     reaching disk. (Executed above.) Wording differs too ('had begun and had not
     returned' vs 'may have begun').
  2. Outcome payload type: boardsync.Unwritten vs channels.NotAppended; key is
     session_id vs LinkKey. Otherwise the same statements in the same order.
_run:
  3. Board: `while True` / `if item is None: return`; channel: walrus loop. Same.
  4. Board sets _in_flight = True before settling and False after; channel has no flag.
  5. After settling when already abandoned: board `continue`s (next get returns the
     None sentinel close put, or another item, and the abandoned check returns);
     channel `return`s at once. Both end the worker; board may dequeue one more item
     first. No report differs: close already reported every pending item.
submit:
  6. Thread name 'boardsync' vs 'channels'; Unwritten vs NotAppended. Otherwise identical.
drain:
  7. Identical code.""")

print(f"\nchanged lines per method: {total_changed}")
verdict(
    "CONFIRMED with differences named",
    "the four methods are near-copies; the one behavioural difference is close()'s "
    "first-pending reason (board: conditional on _in_flight; channels: unconditional), "
    "shown executed above. Adopting board's close in channels changes that reason "
    "string and needs its own test, as the plan says.",
)
