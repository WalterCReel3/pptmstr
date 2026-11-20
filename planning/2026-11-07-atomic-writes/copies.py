"""
Diff the atomic-write copies at one commit, by AST, and table what varies.

Read-only. Run from the repository root with the repo venv (stdlib only):

    .venv/bin/python planning/2026-11-07-atomic-writes/copies.py [commit] > out.txt

Sources come from ``git show <commit>:<path>``, never the working tree, so the
table describes the commit named and not whatever is checked out.

Two outputs:

- a feature table per copy, every cell computed from the copy's AST;
- the *core* of each copy -- the ``mkstemp`` assignment and the ``try`` after it --
  unparsed with the copy's own identifiers mapped to shared ones, and diffed
  against ``boardlog.append``'s core with difflib. The mapping only renames
  locals (``handle``, ``tmp``, ``target``...), so a line that still differs is a
  difference in behaviour or in spelling, and the reader decides which.
"""

from __future__ import annotations

import ast
import difflib
import subprocess
import sys
from dataclasses import dataclass

COMMIT = sys.argv[1] if len(sys.argv) > 1 else "66af79e"

# (module path, function name, kind relayed by the task)
COPIES = (
    ("pptmstr/boardlog.py", "append", "create-once"),
    ("pptmstr/brief.py", "write_entry", "create-once"),
    ("pptmstr/commandfile.py", "_create", "create-once"),
    ("pptmstr/requests.py", "_append", "create-once"),
    ("pptmstr/sessionfile.py", "_update", "create-once|replace"),
    ("pptmstr/sessions.py", "save_overlay", "replace"),
    ("pptmstr/settings.py", "save", "replace"),
    ("pptmstr/layout_ini.py", "keep_layout_for", "replace"),
)

# Locals that name the same role in every copy.
ROLES = {
    "fd": "fd",
    "tmp": "tmp",
    "handle": "handle",
    "target": "target",
    "path": "target",
    "data": "data",
    "text": "data",
    "patched": "data",
    "directory": "directory",
    "ordinal": "n",
    "serial": "n",
}


def source(path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{COMMIT}:{path}"], check=True, capture_output=True, text=True
    ).stdout


def function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise LookupError(name)


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return "?"


def calls(fn: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)]


def kw(call: ast.Call, name: str, pos: int | None = None) -> str:
    for k in call.keywords:
        if k.arg == name:
            return ast.unparse(k.value)
    if pos is not None and len(call.args) > pos:
        return ast.unparse(call.args[pos])
    return "-"


def handled(fn: ast.AST) -> list[str]:
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.ExceptHandler):
            out.append("bare" if n.type is None else ast.unparse(n.type))
    return out


def suppressed(fn: ast.AST) -> list[str]:
    out = []
    for c in calls(fn):
        if dotted(c.func) == "contextlib.suppress":
            out.extend(ast.unparse(a) for a in c.args)
    return out


@dataclass
class Row:
    copy: str
    kind: str
    mkstemp_prefix: str
    mkstemp_suffix: str
    fdopen: str
    file_fsync: str
    dir_fsync: str
    place: str
    loop: str
    on_exists: str
    cleanup: str
    raises_out: str


def core(fn: ast.FunctionDef) -> list[ast.stmt]:
    """The mkstemp assignment and the try statement after it, in whatever block holds them."""
    for node in ast.walk(fn):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body):
            if (
                isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Call)
                and dotted(stmt.value.func) == "tempfile.mkstemp"
            ):
                return body[i : i + 2]
    raise LookupError("no mkstemp")


class Rename(ast.NodeTransformer):
    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = ROLES.get(node.id, node.id)
        return node


def row(path: str, name: str, kind: str, tree: ast.Module) -> Row:
    fn = function(tree, name)
    cs = calls(fn)
    names = [dotted(c.func) for c in cs]
    mk = next(c for c in cs if dotted(c.func) == "tempfile.mkstemp")
    fdo = next(c for c in cs if dotted(c.func) == "os.fdopen")
    file_sync = "yes" if "os.fsync" in names else "no"
    if "durable" in {a.arg for a in fn.args.kwonlyargs}:
        file_sync = "if durable"
    # A directory fsync can live in a helper this function calls.
    dir_sync = "no"
    for helper in {n for n in names if n.startswith("_")}:
        try:
            h = function(tree, helper)
        except LookupError:
            continue
        if any("O_DIRECTORY" in ast.unparse(c) for c in calls(h)) and any(
            dotted(c.func) == "os.fsync" for c in calls(h)
        ):
            dir_sync = f"yes ({helper}, best effort)"
    place = "+".join(sorted({n for n in names if n in ("os.link", "os.replace")}))
    loops = [n for n in ast.walk(fn) if isinstance(n, (ast.While, ast.For))]
    loop = ",".join(
        f"{type(n).__name__.lower()}:{ast.unparse(n.iter) if isinstance(n, ast.For) else ast.unparse(n.test)}"
        for n in loops
    ) or "-"
    on_exists = []
    for n in ast.walk(fn):
        if (
            isinstance(n, ast.ExceptHandler)
            and n.type is not None
            and ast.unparse(n.type) == "FileExistsError"
        ):
            on_exists.append(" ; ".join(ast.unparse(s) for s in n.body))
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    cleanup = []
    for t in tries:
        text = ast.unparse(t)
        if "os.unlink(tmp)" not in text:
            continue
        if t.finalbody and "os.unlink(tmp)" in "".join(ast.unparse(s) for s in t.finalbody):
            cleanup.append("finally")
        for h in t.handlers:
            if "os.unlink(tmp)" in ast.unparse(h):
                cleanup.append(f"except {ast.unparse(h.type) if h.type else 'bare'}")
    outer = [h for h in handled(fn) if h not in ("FileExistsError",)]
    return Row(
        copy=f"{path.split('/')[-1][:-3]}.{name}",
        kind=kind,
        mkstemp_prefix=kw(mk, "prefix"),
        mkstemp_suffix=kw(mk, "suffix"),
        fdopen=f"{kw(fdo, 'mode', 1)} enc={kw(fdo, 'encoding')} nl={kw(fdo, 'newline')}",
        file_fsync=file_sync,
        dir_fsync=dir_sync,
        place=place,
        loop=loop,
        on_exists=" | ".join(on_exists) or "-",
        cleanup=",".join(cleanup) or "?",
        raises_out=(
            "catches " + ", ".join(outer) if outer else "raises"
        )
        + (f"; suppress {', '.join(suppressed(fn))}" if suppressed(fn) else ""),
    )


def main() -> None:
    rows = []
    cores = {}
    for path, name, kind in COPIES:
        tree = ast.parse(source(path))
        rows.append(row(path, name, kind, tree))
        block = ast.Module(body=[Rename().visit(s) for s in core(function(tree, name))], type_ignores=[])
        cores[rows[-1].copy] = ast.unparse(block).splitlines()

    fields = list(Row.__dataclass_fields__)
    print(f"# Copies at {COMMIT}\n")
    for r in rows:
        print(f"## {r.copy}")
        for f in fields[1:]:
            print(f"  {f:15} {getattr(r, f)}")
        print()

    ref = "boardlog.append"
    print(f"# Similarity of each core to {ref}'s (difflib ratio over unparsed lines)\n")
    for name, lines in cores.items():
        ratio = difflib.SequenceMatcher(a=cores[ref], b=lines).ratio()
        print(f"  {name:28} {ratio:.2f}")
    print()
    for name, lines in cores.items():
        if name == ref:
            continue
        print(f"# Core diff: {ref} -> {name}\n")
        for line in difflib.unified_diff(cores[ref], lines, ref, name, lineterm="", n=1):
            print(line)
        print()


if __name__ == "__main__":
    main()
