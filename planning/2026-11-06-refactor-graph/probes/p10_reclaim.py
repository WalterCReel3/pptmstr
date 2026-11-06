"""
Claim 10: board._holder_left_behind vs store._may_reclaim -- semantically equal modulo
the documented narrowing? And is the docstring's "the store cannot import this
module" false (store imports board at module level)?

Part 1 (executed): enumerate every combination of task state, holder (none; a
worker / the root of this session; a worker / the root of another session) and
whether the holder is of an earlier run. The asker is the root of session S, the
task is declared on S's board. For each, compute
  board  = board._holder_left_behind(snap, task, S)
  store  = whether the store's TaskReleased arm takes the root's release: the real
           _ownership_refusal, then the arm's own two lines
           (NOT_YOURS and _may_reclaim -> taken)
The snapshot is a stub exposing ``tasks`` and ``of_earlier_run`` -- the only two
things either function reads.

Part 2 (executed): the module-level imports of store.py and board.py, by AST, and
whether importing pptmstr.store loads pptmstr.board.
"""

from __future__ import annotations

import ast
import itertools
import sys
import types

import _common  # noqa: F401
from _common import source_of, verdict

from pptmstr import board, store
from pptmstr.model import Task, TaskState
from pptmstr.store import TaskRefusal  # type: ignore[attr-defined]

S, O = "sess-S", "sess-O"
ROOT = (S, None)
HOLDERS = {
    "none": None,
    "S worker": (S, "w1"),
    "S root": (S, None),
    "O worker": (O, "w1"),
    "O root": (O, None),
}

rows = []
for state, (hname, holder), earlier in itertools.product(TaskState, HOLDERS.items(), (False, True)):
    if holder is None and earlier:
        continue
    t = Task(id="t1", title="t", state=state, claimed_by=holder, declared_by=ROOT)
    snap = types.SimpleNamespace(
        tasks={"t1": t},
        of_earlier_run=(lambda n, h=holder, e=earlier: e and n == h),
    )
    b = board._holder_left_behind(snap, t, S)  # type: ignore[arg-type]
    refused = store._ownership_refusal(snap.tasks, "t1", ROOT)
    intent = types.SimpleNamespace(task_id="t1", node_id=ROOT)
    if refused is TaskRefusal.NOT_YOURS and store._may_reclaim(snap, intent):  # type: ignore[arg-type]
        refused = None
    s = refused is None
    mr = store._may_reclaim(snap, intent)  # type: ignore[arg-type]
    rows.append((state.name, hname, earlier, b, mr, s, refused))

print(f"{'state':<10} {'holder':<9} {'earlier':<8} {'board':<6} {'_may_reclaim':<13} {'store takes':<12} refusal / note")
mismatch = []
for st, h, e, b, mr, s, refused in rows:
    note = "" if refused is None else refused.name
    if b != s:
        if h == "S root" and not e and st == "CLAIMED":
            note += "  [root releasing its own claim: not a reclaim]"
        elif h.startswith("O") and e and st == "CLAIMED":
            note += "  [DOCUMENTED NARROWING: other session's earlier-run holder]"
        else:
            note += "  [UNDOCUMENTED]"
        mismatch.append((st, h, e, b, s, note))
    if b != mr and not (b == s):
        pass
    print(f"{st:<10} {h:<9} {str(e):<8} {str(b):<6} {str(mr):<13} {str(s):<12} {note}")

print()
print("rows where _may_reclaim is True but the store does not take the release (state guard):")
for st, h, e, b, mr, s, refused in rows:
    if mr and not s:
        print(f"  {st} {h} earlier={e}: _may_reclaim=True, refused {refused.name}; board={b}")

undocumented = [m for m in mismatch if "UNDOCUMENTED" in m[5]]
print(f"\nmismatches: {len(mismatch)}  undocumented: {len(undocumented)}")

print("\n== Part 2: module-level imports")


def top_imports(path: str) -> list[str]:
    tree = ast.parse(source_of(path))
    out = []
    for n in tree.body:
        if isinstance(n, ast.ImportFrom):
            out.append(f":{n.lineno} from {'.' * n.level}{n.module or ''} import "
                       + ", ".join(a.name for a in n.names))
        elif isinstance(n, ast.Import):
            out.append(f":{n.lineno} import " + ", ".join(a.name for a in n.names))
    return out


store_board = [l for l in top_imports("pptmstr/store.py") if " from .board " in f" {l.split(' ', 1)[1]} "]
board_store = [l for l in top_imports("pptmstr/board.py") if "store" in l]
print("  store.py imports of .board:", store_board)
print("  board.py imports naming store:", board_store or "none")
import subprocess  # noqa: E402

fresh = subprocess.run(
    [sys.executable, "-c", "import sys; import pptmstr.store; print('pptmstr.board' in sys.modules)"],
    cwd=str(_common.ROOT), capture_output=True, text=True, env={**__import__("os").environ},
)
print("  fresh interpreter, 'pptmstr.board' in sys.modules after only `import pptmstr.store`:",
      fresh.stdout.strip() or fresh.stderr.strip())

store_imports_board = bool(store_board)
print()
if not undocumented:
    verdict(
        "CONFIRMED (equivalence)",
        "board._holder_left_behind agrees with the store's acceptance of the root's release on "
        "every enumerated case except (a) the documented narrowing -- a holder of another "
        "session that is of an earlier run -- and (b) the root releasing its own current-run "
        "claim, which is ownership, not a reclaim. Note _may_reclaim alone does not check "
        "state: it is True for a COMPLETED task whose holder is of an earlier run, and the "
        "arm's _ownership_refusal (ALREADY_COMPLETE, before NOT_YOURS) is what guards it; a "
        "merged predicate must keep board's `state is CLAIMED` or that ordering.",
    )
else:
    verdict("REFUTED (equivalence)", f"undocumented mismatches: {undocumented}")
if store_imports_board:
    verdict(
        "CONFIRMED (docstring false)",
        f"store imports board at module level ({store_board[0]}); board does not import store; "
        "'the store cannot import this module' is false",
    )
else:
    verdict("REFUTED (docstring)", "store does not import board at module level")
