"""
Claim 3: store._raise_refusal vs store._request_refusal -- identical modulo names?

Three comparisons, each after alpha-renaming every Name/arg by first appearance:
  A. whole bodies (docstring excluded), attributes kept
  B. the allowance tail only -- from the ``still_open = ...`` statement to the end --
     attributes kept
  C. the allowance tail after the ``still_open`` line (the counting and the caps),
     attributes kept
Every differing node is printed with its source line.

The open-set filter equivalence (decisions' ``is_open and session_id == asker[0]``
vs ``commands_awaiting(asker[0])`` then ``is_open``) is checked by reading the
property bodies, printed below; it is not executed against constructed records.
"""

from __future__ import annotations

import ast
import inspect

import _common  # noqa: F401
from _astdiff import canon, diff, report
from _common import body_without_docstring, find_def, source_of, verdict

SRC = source_of("pptmstr/store.py")
LINES = SRC.splitlines()
tree = ast.parse(SRC)
ra = find_def(tree, "_raise_refusal")
rq = find_def(tree, "_request_refusal")
print(f"store._raise_refusal at :{ra.lineno}, store._request_refusal at :{rq.lineno}")


def tail(fn: ast.FunctionDef) -> list[ast.stmt]:
    body = body_without_docstring(fn)
    for i, st in enumerate(body):
        if (
            isinstance(st, ast.Assign)
            and isinstance(st.targets[0], ast.Name)
            and st.targets[0].id == "still_open"
        ):
            return body[i:]
    raise LookupError(fn.name)


def compare(label: str, a: list[ast.stmt], b: list[ast.stmt], attrs: bool) -> int:
    ca, na = canon(a, attrs)
    cb, nb = canon(b, attrs)
    d = diff(ca, cb)
    print(f"\n== {label}: {len(d)} differing node(s)")
    print(f"   renames raise:   {na.names}")
    print(f"   renames request: {nb.names}")
    if attrs:
        print(f"   attr renames raise:   {na.attrs_map}")
        print(f"   attr renames request: {nb.attrs_map}")
    report(d, LINES, LINES, "store", "store")
    return len(d)


n_whole = compare("A. whole bodies, attributes kept", body_without_docstring(ra), body_without_docstring(rq), False)
ta, tq = tail(ra), tail(rq)
print(f"\ntail of _raise_refusal: lines {ta[0].lineno}-{ta[-1].end_lineno}")
print(f"tail of _request_refusal: lines {tq[0].lineno}-{tq[-1].end_lineno}")
n_tail = compare("B. allowance tail, attributes kept", ta, tq, False)
n_tail_attr = compare("C. allowance tail after the still_open line, attributes kept", ta[1:], tq[1:], False)

print("\n== open-set filters, by reading (not executed)")
from pptmstr import command, model  # noqa: E402

print(inspect.getsource(model.Snapshot.commands_awaiting).split('"""')[-1].strip())
print("Command.owes_row:", inspect.getsource(command.Command.owes_row.fget).strip().splitlines()[-1].strip())  # type: ignore[union-attr]
print("Command.is_open: ", inspect.getsource(command.Command.is_open.fget).strip().splitlines()[-1].strip())  # type: ignore[union-attr]
print("Command.session_id:", inspect.getsource(command.Command.session_id.fget).strip().splitlines()[-1].strip())  # type: ignore[union-attr]
print("Decision.session_id:", inspect.getsource(model.Decision.session_id.fget).strip().splitlines()[-1].strip())  # type: ignore[union-attr]
print("Decision.is_open:", inspect.getsource(model.Decision.is_open.fget).strip().splitlines()[-1].strip())  # type: ignore[union-attr]
print(
    "=> owes_row = is_open OR unsupervised, so commands_awaiting(s) filtered by is_open is\n"
    "   exactly {c : c.is_open and c.asker[0] == s}; the decision side is\n"
    "   {d : d.is_open and d.asker[0] == s}. Same shape; order differs (asked_at vs dict\n"
    "   order) but only counts are taken."
)

print()
print(f"summary: whole={n_whole} tail={n_tail} tail-after-still_open={n_tail_attr}")
if n_tail_attr == 0:
    verdict(
        "CONFIRMED (tail only, with one named exception)",
        f"after alpha-renaming, the counting/cap statements are AST-identical (0 diffs; the "
        f"enum members TOO_MANY_OPEN/SESSION_FULL even share attribute names). The tail's "
        f"first statement, the open-set filter, differs at {n_tail} node(s): "
        f"snap.decisions.values() + 'is_open and session_id == asker[0]' vs "
        f"snap.commands_awaiting(asker[0]) + 'is_open'. Equivalent by reading only. The "
        f"whole functions are not identical modulo names: the preambles differ entirely.",
    )
else:
    verdict("REFUTED", f"the counting tail differs at {n_tail_attr} node(s) after renaming")
