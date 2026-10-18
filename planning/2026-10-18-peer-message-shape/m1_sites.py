"""
Find every site in pptmstr/ and tests/ that touches a Concern, a concern intent,
a concern effect, a concern boardlog record, or the concern bus tools.

Usage: python m1_sites.py REPO_ROOT [--tests] [--m2]

Revised (S4 findings 2, 3 and 11; M2). ``--m2`` adds the patterns S4 showed
the M1 run could not see, and leaves the M1 run reproducible without it:

- the strings in ``M2_STRINGS``: tool-description and briefing prose that a
  peer message would make false, and the tests that pin that prose;
- a name in ``M2_NAMES``: the briefing builders whose text frames who reads
  a message;
- a boardlog record name used inside ``boardlog.py`` itself, where it is
  neither imported nor qualified.

Stdlib only. A *site* is the innermost function, method or class body holding
one or more matches, or the module-level statement when there is none, because
that is the unit a design change would have to edit. Each site is printed as
``path:line qualname  [reasons]`` with the lines that matched.

What counts as a match (by AST, so comments and docstrings never match):

- a name or attribute in ``NAMES`` (types, intents, effects, projections,
  helpers whose only subject is a concern);
- a boardlog record class (``Posted``/``Edited``/``Withdrawn``/``Delivered``)
  either qualified as ``boardlog.X`` or imported from ``boardlog`` in that file;
- an attribute or parameter named ``concern``/``concerns``/``concern_id``/
  ``concern_ids``/``incident``/``incidents``/``earlier_incidents``;
- any other name or attribute whose spelling contains ``concern`` (a call of
  ``_concern_wake``, ``WakeReason.CONCERN_TO_LEAD``, a theme key);
- a definition whose own name contains ``concern`` or ``incident``;
- a string constant (not a docstring) containing ``post_concern``,
  ``read_inbox`` or a boardlog concern KIND.

What it cannot see is listed in m1-consumers.md and was covered by reading.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

NAMES = frozenset(
    {
        # model
        "Concern",
        "ConcernId",
        "ConcernState",
        "closing_notice",
        "is_closing_notice",
        "inbox_of",
        "stated_incident",
        # intents and effects
        "ConcernPosted",
        "ConcernEdited",
        "ConcernWithdrawn",
        "InboxRead",
        "InboxDelivered",
        # board / boardfacts / boardlog projections
        "BoardConcern",
        "RestoredConcern",
        "board_concerns",
        "restored_concern",
        "_restored_concern",
        "_open_concerns_by_task",
        # digest
        "Reported",
        "incident_attribution",
        "incident_label",
        "incident_marker",
        "incidents",
    }
)
BOARDLOG_RECORDS = frozenset({"Posted", "Edited", "Withdrawn", "Delivered"})
FIELD_NAMES = frozenset(
    {
        "concern",
        "concerns",
        "concern_id",
        "concern_ids",
        "incident",
        "incidents",
        "earlier_incidents",
    }
)
# Not "inbox": that word is also the operator's NEEDS YOU pane (ui/inbox.py) and
# a model-catalog probe queue, and every concern-inbox site is already found by
# ``read_inbox``, ``InboxRead``, ``InboxDelivered`` or ``inbox_of``.
DEF_WORDS = ("concern", "incident")
STRINGS = (
    "post_concern",
    "read_inbox",
    "concern-posted",
    "concern-edited",
    "concern-withdrawn",
    "concerns-delivered",
)
# Revised (S4 findings 2 and 3; M2). Only with --m2.
M2_STRINGS = (
    "wakes anyone",
    "nothing else wakes you",
    "reviewed by the operator",
    "nobody else reads it",
)
M2_NAMES = frozenset({"lead_briefing", "_concern_audience_lines"})


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _boardlog_aliases(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("boardlog"):
            for alias in node.names:
                if alias.name in BOARDLOG_RECORDS:
                    names.add(alias.asname or alias.name)
    return names


class Finder(ast.NodeVisitor):
    def __init__(self, rel: str, tree: ast.AST, m2: bool = False) -> None:
        self.rel = rel
        self.stack: list[tuple[str, int]] = []
        self.docs = _docstring_ids(tree)
        self.boardlog = _boardlog_aliases(tree)
        # Revised (S4 finding 11; M2): inside boardlog.py the record classes are
        # bare names, so with --m2 they count there as well.
        if m2 and rel.endswith("pptmstr/boardlog.py"):
            self.boardlog |= BOARDLOG_RECORDS
        self.strings = STRINGS + (M2_STRINGS if m2 else ())
        self.m2_names = M2_NAMES if m2 else frozenset()
        self.hits: dict[tuple[str, int], dict[int, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self.top: ast.stmt | None = None

    def _site(self) -> tuple[str, int]:
        if self.stack:
            return (".".join(n for n, _ in self.stack), self.stack[-1][1])
        assert self.top is not None
        return ("<module>", self.top.lineno)

    def _hit(self, line: int, why: str) -> None:
        self.hits[self._site()][line].add(why)

    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            self.top = stmt
            self.visit(stmt)

    def _scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.stack.append((node.name, node.lineno))
        lowered = node.name.lower()
        if node.name in NAMES or any(w in lowered for w in DEF_WORDS):
            self._hit(node.lineno, f"def:{node.name}")
        if not isinstance(node, ast.ClassDef):
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if arg.arg in FIELD_NAMES:
                    self._hit(arg.lineno, f"param:{arg.arg}")
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _scope
    visit_AsyncFunctionDef = _scope
    visit_ClassDef = _scope

    def visit_match_case(self, node: ast.match_case) -> None:
        # One arm of a reducer or a fold is its own site: adding a record kind
        # adds an arm, and editing one arm leaves its neighbours alone.
        label = ast.unparse(node.pattern)
        if len(label) > 48:
            label = label[:45] + "..."
        self.stack.append((f"[case {label}]", node.pattern.lineno))
        self.generic_visit(node)
        self.stack.pop()

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in NAMES:
            self._hit(node.lineno, f"name:{node.id}")
        elif "concern" in node.id.lower():
            self._hit(node.lineno, f"word:{node.id}")
        elif node.id in self.boardlog:
            self._hit(node.lineno, f"boardlog:{node.id}")
        elif node.id in self.m2_names:
            self._hit(node.lineno, f"m2name:{node.id}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in NAMES:
            self._hit(node.lineno, f"name:{node.attr}")
        elif node.attr in FIELD_NAMES:
            self._hit(node.lineno, f"attr:.{node.attr}")
        elif "concern" in node.attr.lower():
            self._hit(node.lineno, f"word:.{node.attr}")
        elif (
            node.attr in BOARDLOG_RECORDS
            and isinstance(node.value, ast.Name)
            and node.value.id == "boardlog"
        ):
            self._hit(node.lineno, f"boardlog:{node.attr}")
        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg in FIELD_NAMES:
            self._hit(node.value.lineno, f"kw:{node.arg}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in NAMES or (
                alias.name in BOARDLOG_RECORDS and node.module and node.module.endswith("boardlog")
            ):
                self._hit(node.lineno, f"import:{alias.name}")

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and id(node) not in self.docs:
            for s in self.strings:
                if s in node.value:
                    self._hit(node.lineno, f"str:{s}")


def scan(
    root: Path, dirs: tuple[str, ...], m2: bool = False
) -> list[tuple[str, int, str, dict[int, set[str]]]]:
    sites = []
    for d in dirs:
        for path in sorted((root / d).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            finder = Finder(rel, tree, m2)
            finder.visit(tree)
            for (qual, line), hits in finder.hits.items():
                sites.append((rel, line, qual, dict(hits)))
    sites.sort(key=lambda s: (s[0], s[1]))
    return sites


def _import_only(hits: dict[int, set[str]]) -> bool:
    return all(w.startswith("import:") for ws in hits.values() for w in ws)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[1]).resolve()
    dirs = ("pptmstr", "tests") if "--tests" in argv else ("pptmstr",)
    sites = scan(root, dirs, "--m2" in argv)
    imports = [s for s in sites if _import_only(s[3])]
    real = [s for s in sites if not _import_only(s[3])]
    per_file: dict[str, int] = defaultdict(int)
    for rel, line, qual, hits in real:
        per_file[rel] += 1
        reasons = sorted({w for ws in hits.values() for w in ws})
        lines = ",".join(str(n) for n in sorted(hits))
        print(f"{rel}:{line} {qual}  [{' '.join(reasons)}]  lines={lines}")
    print()
    print(f"# {len(real)} sites ({len(imports)} import-only statements not counted)")
    for rel, n in sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"#   {n:4d} {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
