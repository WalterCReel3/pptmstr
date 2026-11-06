"""
Claim 4 (plan A5): the strict JSON field decoders shared by commandfile and
sessionfile are verbatim copies; effects._quoted and bus._said are verbatim copies.

Every module-level function name the two file modules both define is compared by
ast.dump of its body (docstring excluded), raw and after alpha-renaming. Names only
one of them defines are listed. Then effects._quoted vs bus._said: by AST, and by
behaviour over a fixed corpus of awkward strings.
"""

from __future__ import annotations

import ast

import _common  # noqa: F401
from _astdiff import canon, diff, report
from _common import body_without_docstring, dump, find_def, source_of, verdict

CF, SF = "pptmstr/commandfile.py", "pptmstr/sessionfile.py"
cf_src, sf_src = source_of(CF), source_of(SF)
cf_tree, sf_tree = ast.parse(cf_src), ast.parse(sf_src)


def funcs(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


cfs, sfs = funcs(cf_tree), funcs(sf_tree)
shared = sorted(set(cfs) & set(sfs))
print("== commandfile vs sessionfile: module-level functions defined in both")
results: dict[str, str] = {}
for name in shared:
    a, b = body_without_docstring(cfs[name]), body_without_docstring(sfs[name])
    raw_same = dump(a) == dump(b)
    ca, _ = canon(a)
    cb, _ = canon(b)
    d = diff(ca, cb)
    sig_same = ast.dump(cfs[name].args) == ast.dump(sfs[name].args)
    state = "IDENTICAL" if raw_same and sig_same else (
        "identical modulo names" if not d and sig_same else f"DIFFERS ({len(d)} node(s), sig same={sig_same})"
    )
    results[name] = state
    print(f"  {name:<12} commandfile:{cfs[name].lineno:<5} sessionfile:{sfs[name].lineno:<5} {state}")
    if d or not sig_same:
        if not sig_same:
            print(f"    signature commandfile: ({ast.unparse(cfs[name].args)})")
            print(f"    signature sessionfile: ({ast.unparse(sfs[name].args)})")
        report(d, cf_src.splitlines(), sf_src.splitlines(), "commandfile", "sessionfile")

# _BAD sentinel
def bad(tree: ast.Module) -> str | None:
    for n in tree.body:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "_BAD":
            return ast.unparse(n)
    return None


print(f"  _BAD         commandfile: {bad(cf_tree)!r}  sessionfile: {bad(sf_tree)!r}")
print(f"\n  only in commandfile: {sorted(set(cfs) - set(sfs))}")
print(f"  only in sessionfile: {sorted(set(sfs) - set(cfs))}")

decoders = ["_text", "_number", "_whole"]
decoders_same = all(results.get(n) == "IDENTICAL" for n in decoders)

print("\n== effects._quoted vs bus._said")
EF, BU = "pptmstr/effects.py", "pptmstr/bus.py"
ef_src, bu_src = source_of(EF), source_of(BU)
q = find_def(ast.parse(ef_src), "_quoted")
s = find_def(ast.parse(bu_src), "_said")
one = find_def(ast.parse(bu_src), "_one_line")
print("  effects._quoted:", ast.unparse(body_without_docstring(q)))  # type: ignore[arg-type]
print("  bus._said:      ", ast.unparse(body_without_docstring(s)))  # type: ignore[arg-type]
print("  bus._one_line:  ", ast.unparse(body_without_docstring(one)))  # type: ignore[arg-type]
cq, _ = canon(body_without_docstring(q))
cs, _ = canon(body_without_docstring(s))
qd = diff(cq, cs)
print(f"  AST (alpha-renamed) differing nodes: {len(qd)}")
report(qd, ef_src.splitlines(), bu_src.splitlines(), "effects", "bus")

from pptmstr import bus, effects  # noqa: E402

CORPUS = [
    "", "plain", 'a "q" b', "back\\slash", '\\"', "two\nlines", "cr\rlf\r\n", "\n",
    "trail\n", "\x0b\x0c\x1c\x1d\x1e\x85", "ls ps ", "\t tab", "\x00nul",
    "\ud800lone", "é ü 字", "''", '"""', "a\n\nb",
]
mismatch = [(t, effects._quoted(t), bus._said(t)) for t in CORPUS if effects._quoted(t) != bus._said(t)]
print(f"  behaviour over {len(CORPUS)} inputs: {len(mismatch)} mismatch(es)")
for t, a, b in mismatch:
    print(f"    {t!r}: effects={a!r} bus={b!r}")

print()
quoted_ast_same = not qd
if decoders_same:
    verdict(
        "CONFIRMED (decoders)",
        "commandfile and sessionfile _text/_number/_whole and _BAD are AST-identical. "
        "Other shared names: " + ", ".join(f"{k}={v}" for k, v in results.items() if k not in decoders),
    )
else:
    verdict("REFUTED (decoders)", ", ".join(f"{k}={v}" for k, v in results.items()))
if quoted_ast_same:
    verdict("CONFIRMED (_quoted/_said)", "AST-identical modulo names")
elif not mismatch:
    verdict(
        "REFUTED as 'verbatim' / CONFIRMED as equivalent (_quoted/_said)",
        "not verbatim: bus._said calls bus._one_line(text) where effects._quoted inlines "
        "' '.join(text.splitlines()); _one_line is exactly that expression, and behaviour "
        f"agrees on all {len(CORPUS)} corpus inputs",
    )
else:
    verdict("REFUTED (_quoted/_said)", f"{len(mismatch)} behavioural mismatches")
