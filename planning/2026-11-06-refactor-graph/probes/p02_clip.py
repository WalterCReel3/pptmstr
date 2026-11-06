"""
Claim 2: detail.clip, brief_pane.clip and board_pane.clip have identical bodies.
Compared by ast.dump of the bodies (docstring excluded and, separately, included),
plus the signatures.
"""

from __future__ import annotations

import ast

import _common  # noqa: F401
from _common import body_without_docstring, dump, find_def, source_of, verdict

FILES = ["pptmstr/ui/detail.py", "pptmstr/ui/brief_pane.py", "pptmstr/ui/board_pane.py"]

defs = {f: find_def(ast.parse(source_of(f)), "clip") for f in FILES}
bodies = {f: dump(body_without_docstring(fn)) for f, fn in defs.items()}
sigs = {f: ast.dump(fn.args) + " -> " + ast.dump(fn.returns) for f, fn in defs.items()}  # type: ignore[arg-type]
docs = {f: ast.get_docstring(fn) for f, fn in defs.items()}

for f, fn in defs.items():
    print(f"{f}:{fn.lineno}  ast.dump length of body={len(bodies[f])}")
print()
ref = FILES[0]
same_body = all(bodies[f] == bodies[ref] for f in FILES)
same_sig = all(sigs[f] == sigs[ref] for f in FILES)
same_doc = all(docs[f] == docs[ref] for f in FILES)
print(f"bodies (docstring excluded) identical: {same_body}")
print(f"signatures identical:                  {same_sig}")
print(f"docstrings identical:                  {same_doc}")
print()
print("body of", ref)
print(ast.unparse(body_without_docstring(defs[ref])))  # type: ignore[arg-type]
for f in FILES[1:]:
    if bodies[f] != bodies[ref]:
        print(f"--- {f} differs:")
        print(ast.unparse(body_without_docstring(defs[f])))  # type: ignore[arg-type]
    if docs[f] != docs[ref]:
        print(f"--- docstring of {f}:\n{docs[f]}")

if same_body and same_sig:
    verdict(
        "CONFIRMED",
        "the three clip bodies and signatures are AST-identical"
        + ("" if same_doc else " (docstrings differ)"),
    )
else:
    verdict("REFUTED", "bodies or signatures differ; see above")
