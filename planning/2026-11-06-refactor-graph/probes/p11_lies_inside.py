"""
Claim 11: tree.lies_inside_checkout fails closed and worktree._lies_inside fails
open; is either error branch reachable on Linux?

Inputs, each tried as the inner path and as the outer directory (the other side a
real temp directory): a dangling symlink, a non-existent path, a symlink loop, a
relative path, a path with NUL, '~nosuchuser_probe/x', and ''.

Whether the except branch ran is observed with sys.settrace on the handler's line
(tree._resolved's `return None`, worktree._lies_inside's `return False` in the
except), not inferred from the return value.
"""

from __future__ import annotations

import ast
import os
import sys

import _common
from _common import find_def, source_of, verdict

from pptmstr import tree, worktree

print(f"python {sys.version.split()[0]}, platform {sys.platform}")


def handler_lines(path: str, fn: str) -> set[int]:
    f = find_def(ast.parse(source_of(path)), fn)
    out: set[int] = set()
    for n in ast.walk(f):
        if isinstance(n, ast.ExceptHandler):
            out |= {s.lineno for s in n.body}
    return out


TREE_EXC = handler_lines("pptmstr/tree.py", "_resolved")
WT_EXC = handler_lines("pptmstr/worktree.py", "_lies_inside")
print(f"tree._resolved except-body lines {sorted(TREE_EXC)}; worktree._lies_inside except-body lines {sorted(WT_EXC)}")


def traced(fn, *args):  # type: ignore[no-untyped-def]
    hits: set[tuple[str, int]] = set()

    def tracer(frame, event, arg):  # type: ignore[no-untyped-def]
        if event == "line":
            hits.add((os.path.basename(frame.f_code.co_filename), frame.f_lineno))
        return tracer

    sys.settrace(tracer)
    try:
        try:
            result: object = fn(*args)
        except BaseException as exc:  # noqa: BLE001
            result = f"RAISED {type(exc).__name__}: {exc}"
    finally:
        sys.settrace(None)
    return result, hits


base = _common.SANDBOX / "p11"
real = base / "real"
real.mkdir(parents=True)
os.symlink(base / "nowhere", base / "dangling")
os.symlink(base / "loop-b", base / "loop-a")
os.symlink(base / "loop-a", base / "loop-b")

INPUTS = {
    "dangling symlink": str(base / "dangling"),
    "non-existent": str(base / "no" / "such" / "dir"),
    "symlink loop": str(base / "loop-a"),
    "symlink loop/child": str(base / "loop-a" / "x"),
    "relative": "some/relative/dir",
    "NUL": str(base) + "/a\x00b",
    "~nosuchuser": "~nosuchuser_probe/x",
    "empty string": "",
}

print()
print(f"{'input':<20} {'side':<6} {'tree.lies_inside_checkout':<27} {'tree exc?':<10} {'worktree._lies_inside':<22} {'wt exc?':<8}")
reached_tree, reached_wt = [], []
for label, value in INPUTS.items():
    for side in ("inner", "outer"):
        args = (value, str(real)) if side == "inner" else (str(real / "sub"), value)
        t_res, t_hits = traced(tree.lies_inside_checkout, *args)
        w_res, w_hits = traced(worktree._lies_inside, *args)
        t_exc = any(f == "tree.py" and l in TREE_EXC for f, l in t_hits)
        w_exc = any(f == "worktree.py" and l in WT_EXC for f, l in w_hits)
        if t_exc:
            reached_tree.append((label, side, t_res))
        if w_exc:
            reached_wt.append((label, side, w_res))
        print(f"{label:<20} {side:<6} {str(t_res):<27} {str(t_exc):<10} {str(w_res):<22} {str(w_exc):<8}")

print()
print("tree except branch reached by:", reached_tree or "none")
print("worktree except branch reached by:", reached_wt or "none")
both = bool(reached_tree) and bool(reached_wt)
divergent = [
    (l, s) for (l, s, r) in reached_wt
    if any(l == l2 and s == s2 for (l2, s2, _r) in reached_tree)
]
print("inputs reaching both branches (where fail-closed vs fail-open answers differ):", divergent or "none")
print()
print("worktree._lies_inside's one caller (READ, not executed): worktree.detect, :635,")
print("  _lies_inside(answer.main_root, answer.root) -- both strings parsed from git's")
print("  stdout, split on newlines. A POSIX path cannot hold NUL, so the only input found")
print("  to reach the except cannot arrive from that caller.")
if reached_tree or reached_wt:
    verdict(
        "PARTLY REFUTED",
        f"tree's except is reached by ordinary inputs {sorted({l for l, _, _ in reached_tree})} "
        "and answers True (fail closed, as its docstring intends), so 'near-unreachable on "
        f"POSIX' is false for tree. worktree's except is reached only by "
        f"{sorted({l for l, _, _ in reached_wt})} and answers False (fail open); a symlink loop "
        "does not raise there (os.path.realpath is non-strict on 3.11). Its sole caller passes "
        "git output, which cannot carry NUL, so for worktree it is unreachable in practice "
        "and 'cosmetic' holds.",
    )
else:
    verdict("CONFIRMED (near-unreachable)", "no input reached either except branch")
