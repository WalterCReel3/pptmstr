"""
Static dependency and reference graph of ``pptmstr/``, with cluster metrics.

Run from the repository root with any Python >= 3.11 (stdlib only):

    python3 planning/2026-11-06-refactor-graph/graph.py

Writes CSV/JSON beside this file and ``tables.md`` (generated tables; the
narrative is in ``findings.md``, written by hand from these outputs).

Community detection is a deterministic stdlib Louvain (nodes visited in sorted
order) with a label-propagation cross-check, because networkx is not installed
in this environment.
"""

from __future__ import annotations

import ast
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
PKG = ROOT / "pptmstr"
TESTS = ROOT / "tests"
OUT = Path(__file__).resolve().parent

MONOLITHS = ["pptmstr.app", "pptmstr.bus", "pptmstr.model", "pptmstr.store", "pptmstr.driver"]
COMPOSITION_ROOTS = {"pptmstr.app", "pptmstr.__main__"}
EXPAND_LINES = 300  # a top-level def longer than this is split into its sub-units


# ---------------------------------------------------------------- discovery


def module_name(path: Path, base: Path) -> str:
    rel = path.relative_to(base).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


SOURCES: dict[str, Path] = {
    module_name(p, ROOT): p for p in sorted(PKG.rglob("*.py")) if "plugins/pptmstr" not in str(p)
}
PACKAGES = {m for m, p in SOURCES.items() if p.name == "__init__.py"}
TREES = {m: ast.parse(p.read_text(), str(p)) for m, p in SOURCES.items()}
LOC = {m: len(p.read_text().splitlines()) for m, p in SOURCES.items()}


def short(m: str) -> str:
    return m.removeprefix("pptmstr.") if m != "pptmstr" else "pptmstr"


def resolve_from(mod: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    base = mod.split(".")
    if mod not in PACKAGES:
        base = base[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def top_level_names(tree: ast.Module) -> dict[str, ast.AST]:
    names: dict[str, ast.AST] = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                for x in ast.walk(t):
                    if isinstance(x, ast.Name):
                        names[x.id] = n
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and isinstance(n.target, ast.Name):
            names[n.target.id] = n
        elif isinstance(n, ast.TypeAlias) if hasattr(ast, "TypeAlias") else False:
            names[n.name.id] = n  # type: ignore[attr-defined]
    return names


DEFS = {m: top_level_names(t) for m, t in TREES.items()}


# ---------------------------------------------------------------- imports


@dataclass
class Binding:
    module: str  # target module
    name: str | None  # None: the name is the module itself


@dataclass
class ImportEdge:
    src: str
    dst: str
    kind: str  # eager | lazy | typing
    line: int


def is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def collect_imports(mod: str, tree: ast.Module) -> tuple[list[ImportEdge], dict[str, Binding]]:
    """
    Every import in ``mod`` (pptmstr targets only for edges), and the module-level
    name bindings it creates. Imports inside a function are 'lazy'; under
    ``if TYPE_CHECKING`` they are 'typing'.
    """
    edges: list[ImportEdge] = []
    bindings: dict[str, Binding] = {}

    def visit(node: ast.AST, kind: str, top: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                visit(child, "lazy" if kind == "eager" else kind, False)
            elif isinstance(child, ast.If) and is_type_checking(child.test):
                for s in child.body:
                    visit_stmt(s, "typing", top)
                for s in child.orelse:
                    visit_stmt(s, kind, top)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                visit_stmt(child, kind, top)
            else:
                visit(child, kind, top)

    def visit_stmt(child: ast.AST, kind: str, top: bool) -> None:
        if isinstance(child, ast.Import):
            for a in child.names:
                if a.name.startswith("pptmstr"):
                    edges.append(ImportEdge(mod, a.name, kind, child.lineno))
                if top:
                    if a.asname:
                        bindings[a.asname] = Binding(a.name, None)
                    else:
                        bindings[a.name.split(".")[0]] = Binding(a.name.split(".")[0], None)
        elif isinstance(child, ast.ImportFrom):
            base = resolve_from(mod, child)
            if base is None:
                return
            for a in child.names:
                sub = f"{base}.{a.name}"
                target = sub if sub in SOURCES else base
                if target.startswith("pptmstr"):
                    edges.append(ImportEdge(mod, target, kind, child.lineno))
                if top:
                    bindings[a.asname or a.name] = (
                        Binding(sub, None) if sub in SOURCES else Binding(base, a.name)
                    )
        else:
            visit(child, kind, top)

    # module-level statements: top-level bindings are those not inside a def
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            visit_stmt(stmt, "eager", True)
        elif isinstance(stmt, ast.If) and is_type_checking(stmt.test):
            for s in stmt.body:
                visit_stmt(s, "typing", True)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit(stmt, "lazy", False)
        else:
            visit(stmt, "eager", isinstance(stmt, (ast.Try, ast.If)))
    return edges, bindings


IMPORTS: list[ImportEdge] = []
BINDINGS: dict[str, dict[str, Binding]] = {}
for m, t in TREES.items():
    e, b = collect_imports(m, t)
    IMPORTS += e
    BINDINGS[m] = b


def local_bindings(mod: str, fn: ast.AST) -> dict[str, Binding]:
    """Bindings created by imports inside one def (lazy imports)."""
    out: dict[str, Binding] = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.ImportFrom):
            base = resolve_from(mod, n)
            if base is None:
                continue
            for a in n.names:
                sub = f"{base}.{a.name}"
                out[a.asname or a.name] = Binding(sub, None) if sub in SOURCES else Binding(base, a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                out[a.asname or a.name.split(".")[0]] = Binding(a.asname and a.name or a.name.split(".")[0], None)
    return out


def follow_reexport(module: str, name: str, depth: int = 0) -> tuple[str, str] | None:
    """Resolve ``module.name`` to the module that defines it (one hop of re-export)."""
    if module not in SOURCES:
        return None
    if name in DEFS[module]:
        return module, name
    b = BINDINGS[module].get(name)
    if b and depth < 3:
        if b.name is None:
            return (b.module, "<module>") if b.module in SOURCES else None
        return follow_reexport(b.module, b.name, depth + 1)
    return None


# ---------------------------------------------------------------- references


@dataclass
class Ref:
    src_mod: str
    src_def: str
    dst_mod: str
    dst_name: str
    resolved: bool


def refs_of(mod: str, unit: str, node: ast.AST, extra: dict[str, Binding] | None = None) -> list[Ref]:
    """
    Every reference from ``node`` to a pptmstr top-level name: imported names,
    ``module.attr`` chains, and same-module top-level names (dst_mod == mod).
    """
    binds = dict(BINDINGS[mod])
    binds.update(local_bindings(mod, node))
    if extra:
        binds.update(extra)
    own = DEFS[mod]
    out: list[Ref] = []
    consumed: set[int] = set()

    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and id(n) not in consumed:
            # walk to the base of a dotted chain
            chain = [n]
            base = n.value
            while isinstance(base, ast.Attribute):
                chain.append(base)
                base = base.value
            if not isinstance(base, ast.Name) or base.id not in binds or binds[base.id].name is not None:
                continue
            target = binds[base.id].module
            if not target.startswith("pptmstr"):
                continue
            # descend through submodules: chain[-1] is the first attribute
            attrs = [a.attr for a in reversed(chain)]
            i = 0
            while i < len(attrs) and f"{target}.{attrs[i]}" in SOURCES:
                target = f"{target}.{attrs[i]}"
                i += 1
            consumed.update(id(c) for c in chain)
            consumed.add(id(base))
            if i < len(attrs):
                hit = follow_reexport(target, attrs[i])
                if hit:
                    out.append(Ref(mod, unit, hit[0], hit[1], True))
                else:
                    out.append(Ref(mod, unit, target, attrs[i], False))
            else:
                out.append(Ref(mod, unit, target, "<module>", True))
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and id(n) not in consumed:
            b = binds.get(n.id)
            if b is not None and b.module.startswith("pptmstr"):
                if b.name is None:
                    out.append(Ref(mod, unit, b.module, "<module>", b.module in SOURCES))
                else:
                    hit = follow_reexport(b.module, b.name)
                    out.append(
                        Ref(mod, unit, hit[0], hit[1], True) if hit else Ref(mod, unit, b.module, b.name, False)
                    )
            elif n.id in own and b is None:
                out.append(Ref(mod, unit, mod, n.id, True))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in own and n.value.isidentifier():
            # string annotations ("Foo") under `from __future__ import annotations` are
            # already strings in the AST only when quoted; count quoted ones too
            pass
    return out


def unit_name(n: ast.AST) -> str:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return n.name
    names = sorted(k for k, v in top_level_names(ast.Module(body=[n], type_ignores=[])).items())
    return names[0] if names else "<module>"


REFS: list[Ref] = []
for m, t in TREES.items():
    for stmt in t.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        u = unit_name(stmt)
        for r in refs_of(m, u, stmt):
            if not (r.dst_mod == m and r.dst_name == u):
                REFS.append(r)

# ---------------------------------------------------------------- module graph

import_kinds: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
for e in IMPORTS:
    if e.dst in SOURCES and e.dst != e.src:
        import_kinds[(e.src, e.dst)][e.kind] += 1

ref_weight: Counter[tuple[str, str]] = Counter()
for r in REFS:
    if r.dst_mod != r.src_mod and r.dst_mod in SOURCES:
        ref_weight[(r.src_mod, r.dst_mod)] += 1

# directed weighted module edges: references, floored at 1 for an import with no counted reference
MOD_EDGES: dict[tuple[str, str], int] = {}
for k in set(import_kinds) | set(ref_weight):
    if k[0] in SOURCES and k[1] in SOURCES and k[1] not in PACKAGES - {"pptmstr.plugins", "pptmstr.figures", "pptmstr.ui.typeset"}:
        MOD_EDGES[k] = max(ref_weight[k], 1)
# drop edges to empty package inits
MOD_EDGES = {k: v for k, v in MOD_EDGES.items() if LOC.get(k[1], 0) > 5}


def tarjan(nodes: list[str], adj: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: list[list[str]] = []
    counter = [0]
    sys.setrecursionlimit(10000)

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in sorted(adj.get(v, ())):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    for v in nodes:
        if v not in index:
            strong(v)
    return out


def adj_for(kinds: set[str]) -> dict[str, set[str]]:
    a: dict[str, set[str]] = defaultdict(set)
    for (s, d), c in import_kinds.items():
        if any(c[k] for k in kinds):
            a[s].add(d)
    return a


NODES = sorted(SOURCES)
SCC = {
    "eager": tarjan(NODES, adj_for({"eager"})),
    "eager+lazy": tarjan(NODES, adj_for({"eager", "lazy"})),
    "all (incl. TYPE_CHECKING)": tarjan(NODES, adj_for({"eager", "lazy", "typing"})),
}

# smallest cycles that only lazy imports close
lazy_cycles = []
eager_adj = adj_for({"eager", "typing"})
for (s, d), c in import_kinds.items():
    if c["lazy"] and not c["eager"] and not c["typing"]:
        # is s reachable from d by eager edges? then the lazy import closes a cycle
        seen, todo, parent = {d}, [d], {d: None}
        while todo:
            v = todo.pop()
            for w in eager_adj.get(v, ()):
                if w not in seen:
                    seen.add(w)
                    parent[w] = v
                    todo.append(w)
        if s in seen:
            path, v = [], s
            while v is not None:
                path.append(v)
                v = parent[v]
            lazy_cycles.append((s, d, list(reversed(path))))

fan_out = Counter()
fan_in = Counter()
for (s, d) in import_kinds:
    fan_out[s] += 1
    fan_in[d] += 1


def is_ui(m: str) -> bool:
    return m.startswith("pptmstr.ui")


LAYER_VIOLATIONS = sorted(
    (s, d, "+".join(k for k in ("eager", "lazy", "typing") if c[k]))
    for (s, d), c in import_kinds.items()
    if is_ui(d) and not is_ui(s) and s not in COMPOSITION_ROOTS
)
SDK_IMPORTERS = sorted(
    {
        m
        for m, t in TREES.items()
        for n in ast.walk(t)
        if (isinstance(n, ast.ImportFrom) and (n.module or "").startswith("claude_agent_sdk"))
        or (isinstance(n, ast.Import) and any(a.name.startswith("claude_agent_sdk") for a in n.names))
    }
)
IMGUI_IMPORTERS = sorted(
    {
        m
        for m, t in TREES.items()
        for n in ast.walk(t)
        if (isinstance(n, ast.ImportFrom) and (n.module or "").startswith("imgui_bundle"))
        or (isinstance(n, ast.Import) and any(a.name.startswith("imgui_bundle") for a in n.names))
    }
)

# ---------------------------------------------------------------- communities


def undirected(edges: dict[tuple[str, str], int], drop: set[str] = frozenset()) -> dict[str, dict[str, float]]:
    g: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for (s, d), w in edges.items():
        if s in drop or d in drop or s == d:
            continue
        g[s][d] += w
        g[d][s] += w
    return {k: dict(v) for k, v in g.items()}


def modularity(g: dict[str, dict[str, float]], part: dict[str, int]) -> float:
    m2 = sum(sum(n.values()) for n in g.values())
    if m2 == 0:
        return 0.0
    tot: Counter[int] = Counter()
    inn: Counter[int] = Counter()
    for v, nb in g.items():
        tot[part[v]] += sum(nb.values())
        for w, x in nb.items():
            if part[w] == part[v]:
                inn[part[v]] += x
    return sum(inn[c] / m2 - (tot[c] / m2) ** 2 for c in tot)


def louvain(g: dict[str, dict[str, float]], seed: int | None = None) -> dict[str, int]:
    """
    Louvain. Deterministic for ``seed=None`` (nodes in sorted order); a seed
    shuffles the visiting order, which is what the stability check varies.
    """
    import random

    nodes = sorted(g)
    rng = random.Random(seed) if seed is not None else None
    part = {v: i for i, v in enumerate(nodes)}  # original node -> community
    members = {i: [v] for i, v in enumerate(nodes)}  # aggregated node -> originals
    cur = {i: {part[w]: x for w, x in g[v].items()} for i, v in enumerate(nodes)}
    while True:
        m2 = sum(sum(nb.values()) for nb in cur.values())
        if m2 == 0:
            break
        k = {v: sum(nb.values()) for v, nb in cur.items()}
        comm = {v: v for v in cur}
        tot = dict(k)
        moved_any = False
        improved = True
        while improved:
            improved = False
            visit = sorted(cur)
            if rng:
                rng.shuffle(visit)
            for v in visit:
                cv = comm[v]
                links: Counter[int] = Counter()
                for w, x in cur[v].items():
                    if w != v:
                        links[comm[w]] += x
                tot[cv] -= k[v]
                best, gain = cv, links[cv] - tot[cv] * k[v] / m2
                for c in sorted(links):
                    gn = links[c] - tot[c] * k[v] / m2
                    if gn > gain + 1e-12:
                        best, gain = c, gn
                tot[best] = tot.get(best, 0) + k[v]
                if best != cv:
                    comm[v] = best
                    improved = moved_any = True
        if not moved_any:
            break
        ids = {c: i for i, c in enumerate(sorted(set(comm.values())))}
        new_members: dict[int, list[str]] = defaultdict(list)
        for v, c in comm.items():
            new_members[ids[c]] += members[v]
        new: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        for v, nb in cur.items():
            for w, x in nb.items():
                new[ids[comm[v]]][ids[comm[w]]] += x
        cur = {a: dict(b) for a, b in new.items()}
        for i in range(len(ids)):
            cur.setdefault(i, {})
        members = dict(new_members)
    out = {}
    # renumber communities by size, largest first
    order = sorted(members.values(), key=lambda ms: (-len(ms), sorted(ms)[0]))
    for i, ms in enumerate(order):
        for v in ms:
            out[v] = i
    return out


def label_propagation(g: dict[str, dict[str, float]], rounds: int = 50) -> dict[str, int]:
    lab = {v: v for v in g}
    for _ in range(rounds):
        changed = False
        for v in sorted(g):
            score: Counter[str] = Counter()
            for w, x in g[v].items():
                score[lab[w]] += x
            if not score:
                continue
            top = max(score.values())
            best = min(l for l, s in score.items() if s == top)
            if best != lab[v] and score[lab[v]] < top:
                lab[v] = best
                changed = True
        if not changed:
            break
    ids = {l: i for i, l in enumerate(sorted(set(lab.values()), key=lambda l: -list(lab.values()).count(l)))}
    return {v: ids[l] for v, l in lab.items()}


# ---------------------------------------------------------------- prefix clusters

S = lambda *ms: {f"pptmstr.{x}" for x in ms}  # noqa: E731
CLUSTERS: dict[str, set[str]] = {
    "board*+digest": S("board", "boardfacts", "boardgraph", "boardlog", "boardsync", "digest"),
    "command*+req/appr/shellscan/sandbox": S(
        "command", "commandbus", "commandfile", "commandrun", "commandtext", "requests", "approval", "shellscan", "sandbox"
    ),
    "session*/fleet*/links/channels": S("sessions", "sessionfile", "fleetsync", "links", "channels"),
    "ui *_pane": {m for m in SOURCES if is_ui(m) and m.endswith("_pane")},
    "ui *_view": {m for m in SOURCES if is_ui(m) and m.endswith("_view")},
    "ui *_form": {m for m in SOURCES if is_ui(m) and m.endswith("_form")},
    "ui *_menu": {m for m in SOURCES if is_ui(m) and m.endswith("_menu")},
}
PREFIX_OF = {}
for name, ms in CLUSTERS.items():
    for x in ms:
        PREFIX_OF.setdefault(x, name)


def cluster_metrics(edges: dict[tuple[str, str], int], members: set[str]) -> dict[str, object]:
    internal = sum(w for (s, d), w in edges.items() if s in members and d in members)
    out_w = sum(w for (s, d), w in edges.items() if s in members and d not in members)
    in_w = sum(w for (s, d), w in edges.items() if d in members and s not in members)
    partners: Counter[str] = Counter()
    for (s, d), w in edges.items():
        if s in members and d not in members:
            partners[d] += w
        elif d in members and s not in members:
            partners[s] += w
    total = internal + out_w + in_w
    return {
        "members": len(members),
        "internal": internal,
        "out": out_w,
        "in": in_w,
        "cohesion": round(internal / total, 3) if total else 0.0,
        "top_partners": [f"{short(p)}:{w}" for p, w in partners.most_common(5)],
    }


# ---------------------------------------------------------------- monoliths


def sub_units(mod: str, node: ast.AST) -> list[tuple[str, ast.AST]]:
    """
    A long def split into the units a split would actually move: methods for a
    class, nested defs and ``match`` arms for a function. Whatever is left in the
    body stays as the def's own unit.
    """
    name = node.name  # type: ignore[attr-defined]
    units: list[tuple[str, ast.AST]] = []
    if isinstance(node, ast.ClassDef):
        for s in node.body:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                units.append((f"{name}.{s.name}", s))
        return units
    for s in ast.walk(node):
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s is not node and _direct_child_def(node, s):
            units.append((f"{name}.{s.name}", s))
        elif isinstance(s, ast.Match) and _direct_match(node, s):
            for case in s.cases:
                units.append((f"{name}[case {_pattern_name(case.pattern)}]", case))
    return units


def _direct_child_def(fn: ast.AST, d: ast.AST) -> bool:
    for s in fn.body:  # type: ignore[attr-defined]
        if s is d:
            return True
    return False


def _direct_match(fn: ast.AST, m: ast.Match) -> bool:
    # a match not nested inside another nested def
    for s in ast.walk(fn):
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s is not fn:
            if any(x is m for x in ast.walk(s)):
                return False
    return True


def _pattern_name(p: ast.pattern) -> str:
    if isinstance(p, ast.MatchClass):
        return ast.unparse(p.cls)
    if isinstance(p, ast.MatchOr):
        return "|".join(_pattern_name(x) for x in p.patterns[:3]) + ("|…" if len(p.patterns) > 3 else "")
    if isinstance(p, ast.MatchAs) and p.pattern is None:
        return "_"
    return ast.unparse(p)[:30]


def monolith(mod: str) -> dict[str, object]:
    tree = TREES[mod]
    own = DEFS[mod]
    units: dict[str, ast.AST] = {}
    parent_of: dict[str, str] = {}
    expanded: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        u = unit_name(stmt)
        size = (stmt.end_lineno or stmt.lineno) - stmt.lineno + 1
        subs = sub_units(mod, stmt) if size > EXPAND_LINES and isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else []
        if subs:
            expanded.append(f"{u} ({size} lines -> {len(subs)} units)")
            for name, n in subs:
                units[name] = n
                parent_of[name] = u
        units.setdefault(u, stmt)  # the remainder: signature, docstring, fields, prelude
    # map a top-level name to the unit that owns it
    owner = {k: unit_name(v) if not isinstance(v, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else k for k, v in own.items()}
    method_of: dict[str, dict[str, str]] = defaultdict(dict)
    for name in units:
        if "." in name and "[" not in name:
            cls, meth = name.split(".", 1)
            method_of[cls][meth] = name
    edges: Counter[tuple[str, str]] = Counter()
    ext: dict[str, Counter[str]] = defaultdict(Counter)
    size_of: dict[str, int] = {}
    for name, node in units.items():
        size_of[name] = (getattr(node, "end_lineno", 0) or 0) - (getattr(node, "lineno", 0) or 0) + 1
        if name in parent_of.values():
            # the remainder of an expanded def: count only lines outside its sub-units
            size_of[name] = max(1, size_of[name] - sum(size_of.get(s, 0) for s, p in parent_of.items() if p == name))
        nodes_to_scan = [node]
        if name in parent_of.values():
            sub_nodes = {id(units[s]) for s, p in parent_of.items() if p == name}
            nodes_to_scan = [n for n in ast.iter_child_nodes(node) if id(n) not in sub_nodes]
            # a match whose arms are units: scan its subject only
            nodes_to_scan = [_strip_matches(n, sub_nodes) for n in nodes_to_scan]
        for scan in nodes_to_scan:
            for r in refs_of(mod, name, scan):
                if r.dst_mod == mod:
                    tgt = owner.get(r.dst_name, r.dst_name)
                    if tgt != name and tgt in units:
                        edges[(name, tgt)] += 1
                elif r.dst_mod.startswith("pptmstr"):
                    ext[name][short(r.dst_mod)] += 1
            # self.method calls inside a class's methods
            cls = parent_of.get(name)
            if cls in method_of:
                for n in ast.walk(scan):
                    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("self", "cls"):
                        t = method_of[cls].get(n.attr)
                        if t and t != name:
                            edges[(name, t)] += 1
        if name in parent_of:
            edges[(name, parent_of[name])] += 0  # keep the node; containment is not coupling
    g = undirected({k: v for k, v in edges.items() if v > 0})
    for u in units:
        g.setdefault(u, {})
    # connected components
    comp, seen = [], set()
    for v in sorted(g):
        if v in seen:
            continue
        stack, cc = [v], []
        seen.add(v)
        while stack:
            x = stack.pop()
            cc.append(x)
            for w in g[x]:
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        comp.append(sorted(cc))
    comp.sort(key=len, reverse=True)
    part = louvain(g)
    groups: dict[int, list[str]] = defaultdict(list)
    for v, c in part.items():
        groups[c].append(v)
    total_w = sum(edges.values())
    cut = sum(w for (a, b), w in edges.items() if part.get(a) != part.get(b))
    communities = []
    for c, ms in sorted(groups.items()):
        if len(ms) < 2 and not ext.get(ms[0]):
            continue
        e = Counter()
        for x in ms:
            e.update(ext[x])
        hubs = sorted(ms, key=lambda x: -sum(g[x].values()))
        communities.append(
            {
                "id": c,
                "size": len(ms),
                "lines": sum(size_of.get(x, 0) for x in ms),
                "hubs": hubs[:6],
                "external_modules": [f"{k}:{v}" for k, v in e.most_common(6)],
                "members": sorted(ms),
            }
        )
    return {
        "module": mod,
        "loc": LOC[mod],
        "units": len(units),
        "expanded": expanded,
        "intra_edges": len([1 for v in edges.values() if v]),
        "intra_weight": total_w,
        "components": len(comp),
        "largest_component": len(comp[0]) if comp else 0,
        "singletons": sum(1 for c in comp if len(c) == 1),
        "communities": len(groups),
        "modularity": round(modularity(g, part), 3) if g else 0.0,
        "cut_weight": cut,
        "community_detail": communities,
        "edges": [(a, b, w) for (a, b), w in sorted(edges.items()) if w],
    }


def _strip_matches(n: ast.AST, sub_nodes: set[int]) -> ast.AST:
    """A copy of ``n`` without the match arms already counted as units."""
    import copy

    n2 = copy.deepcopy(n) if any(id(x) in sub_nodes for x in ast.walk(n)) else n
    if n2 is n:
        return n
    # rebuild: drop cases whose originals are units
    orig_cases = [x for x in ast.walk(n) if isinstance(x, ast.match_case)]
    copy_cases = [x for x in ast.walk(n2) if isinstance(x, ast.match_case)]
    drop = {id(c2) for c1, c2 in zip(orig_cases, copy_cases) if id(c1) in sub_nodes}
    for x in ast.walk(n2):
        if isinstance(x, ast.Match):
            x.cases = [c for c in x.cases if id(c) not in drop]
    return n2


# ---------------------------------------------------------------- tests

TEST_FILES = sorted(TESTS.rglob("*.py"))
DOTTED = re.compile(r"^pptmstr(\.[A-Za-z_][A-Za-z0-9_]*)+$")
PATCH_FUNCS = {"setattr", "patch", "delattr", "setitem"}


def test_scan() -> tuple[list[tuple[str, str, str]], list[dict[str, object]], dict[str, object]]:
    imports: list[tuple[str, str, str]] = []
    patches: list[dict[str, object]] = []
    per_test_with_string_patch = 0
    tests_total = 0
    object_patches: Counter[str] = Counter()
    for p in TEST_FILES:
        tm = module_name(p, ROOT)
        tree = ast.parse(p.read_text(), str(p))
        tb: dict[str, str] = {}
        for n in ast.walk(tree):
            lazy = "eager"
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("pptmstr") and n.level == 0:
                for a in n.names:
                    sub = f"{n.module}.{a.name}"
                    t = sub if sub in SOURCES else n.module
                    imports.append((tm, t, lazy))
                    if sub in SOURCES:
                        tb[a.asname or a.name] = sub
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith("pptmstr"):
                        imports.append((tm, a.name, lazy))
                        if a.asname:
                            tb[a.asname] = a.name
        test_fns = [
            n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
        ]
        tests_total += len(test_fns)
        fn_of: dict[int, str] = {}
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for x in ast.walk(fn):
                fn_of.setdefault(id(x), fn.name) if fn.name.startswith("test") else None
        hit_tests: set[str] = set()
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fname = n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else ""
            if fname not in PATCH_FUNCS or not n.args:
                continue
            a0 = n.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str) and DOTTED.match(a0.value):
                parts = a0.value.split(".")
                target = next(
                    (".".join(parts[:i]) for i in range(len(parts), 0, -1) if ".".join(parts[:i]) in SOURCES), "?"
                )
                patches.append(
                    {"test_module": tm, "test": fn_of.get(id(n), "<fixture/module>"), "path": a0.value, "target_module": target, "line": n.lineno}
                )
                if id(n) in fn_of:
                    hit_tests.add(fn_of[id(n)])
            elif isinstance(a0, ast.Name) and a0.id in tb and fname == "setattr":
                object_patches[tb[a0.id]] += 1
        per_test_with_string_patch += len(hit_tests)
    return imports, patches, {
        "test_files": len(TEST_FILES),
        "test_functions": tests_total,
        "dotted_string_patches": len(patches),
        "tests_with_dotted_string_patch": per_test_with_string_patch,
        "module_object_setattr_patches_total": sum(object_patches.values()),
        "module_object_setattr_patches": dict(object_patches.most_common()),
    }


# ---------------------------------------------------------------- dynamic dispatch census


def dynamic_census() -> dict[str, object]:
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for m, t in TREES.items():
        for n in ast.walk(t):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("getattr", "setattr", "hasattr"):
                literal = len(n.args) > 1 and isinstance(n.args[1], ast.Constant)
                out[f"{n.func.id}({'literal' if literal else 'computed'} name)"][short(m)] += 1
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "import_module":
                out["importlib.import_module"][short(m)] += 1
            elif isinstance(n, ast.Match):
                kinds = {type(c.pattern).__name__ for c in n.cases}
                if "MatchClass" in kinds or any(
                    isinstance(c.pattern, ast.MatchOr) and any(isinstance(x, ast.MatchClass) for x in c.pattern.patterns)
                    for c in n.cases
                ):
                    out["match on class (static: seen)"][short(m)] += 1
                if any(
                    isinstance(c.pattern, ast.MatchValue) and isinstance(c.pattern.value, ast.Constant) and isinstance(c.pattern.value.value, str)
                    for c in n.cases
                ):
                    out["match on string literal"][short(m)] += 1
            elif isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
                for d in n.decorator_list:
                    f = d.func if isinstance(d, ast.Call) else d
                    if isinstance(f, ast.Name) and f.id == "tool":
                        out["@tool (MCP, dispatched by name string)"][short(m)] += 1
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("globals", "locals", "vars", "eval", "exec"):
                out[f"{n.func.id}()"][short(m)] += 1
    return {k: dict(v.most_common()) for k, v in out.items()}


# ---------------------------------------------------------------- run


def write_csv(name: str, header: list[str], rows: list) -> None:
    with open(OUT / name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main() -> None:
    write_csv(
        "imports.csv",
        ["src", "dst", "eager", "lazy", "typing"],
        [(short(s), short(d), c["eager"], c["lazy"], c["typing"]) for (s, d), c in sorted(import_kinds.items())],
    )
    ref_rows = Counter((short(r.src_mod), r.src_def, short(r.dst_mod), r.dst_name, r.resolved) for r in REFS if r.dst_mod != r.src_mod)
    write_csv("refs.csv", ["src_module", "src_def", "dst_module", "dst_name", "resolved", "count"], [(*k, v) for k, v in sorted(ref_rows.items())])
    unresolved = Counter((short(r.dst_mod), r.dst_name) for r in REFS if not r.resolved)
    write_csv("unresolved.csv", ["module", "name", "count"], [(*k, v) for k, v in unresolved.most_common()])
    write_csv("module_edges.csv", ["src", "dst", "weight"], [(short(s), short(d), w) for (s, d), w in sorted(MOD_EDGES.items())])

    g_all = undirected(MOD_EDGES)
    g_core = undirected(MOD_EDGES, COMPOSITION_ROOTS)
    lv_all, lv_core = louvain(g_all), louvain(g_core)
    lp_core = label_propagation(g_core)
    q_all, q_core = modularity(g_all, lv_all), modularity(g_core, lv_core)
    q_prefix = None

    # agreement: per prefix cluster, which communities its members fall in
    agree = {}
    for name, ms in CLUSTERS.items():
        ms = {m for m in ms if m in lv_core}
        spread = Counter(lv_core[m] for m in ms)
        agree[name] = {
            "communities": {f"C{c}": sorted(short(m) for m in ms if lv_core[m] == c) for c, _ in spread.most_common()},
            "purity": round(spread.most_common(1)[0][1] / len(ms), 2) if ms else 0,
        }
    comm_rows = defaultdict(list)
    for m, c in lv_core.items():
        comm_rows[c].append(m)
    communities = []
    for c, ms in sorted(comm_rows.items()):
        labels = Counter(PREFIX_OF.get(m, "ui (other)" if is_ui(m) else "core (other)") for m in ms)
        communities.append(
            {
                "id": f"C{c}",
                "size": len(ms),
                "loc": sum(LOC[m] for m in ms),
                "metrics": cluster_metrics(MOD_EDGES, set(ms)),
                "prefix_mix": dict(labels.most_common()),
                "members": sorted(short(m) for m in ms),
                "label_propagation": sorted({f"L{lp_core[m]}" for m in ms}),
            }
        )
    cluster_rows = {name: cluster_metrics(MOD_EDGES, ms) for name, ms in CLUSTERS.items()}
    cluster_rows_core = {name: cluster_metrics({k: v for k, v in MOD_EDGES.items() if not (set(k) & COMPOSITION_ROOTS)}, ms) for name, ms in CLUSTERS.items()}
    # modularity of the prefix partition itself, for comparison
    prefix_part = {}
    other_ids = {}
    for m in g_core:
        lab = PREFIX_OF.get(m) or ("ui" if is_ui(m) else short(m))
        prefix_part[m] = other_ids.setdefault(lab, len(other_ids))
    q_prefix = modularity(g_core, prefix_part)

    # stability: how often each same-prefix pair lands together over shuffled Louvain runs
    RUNS = 30
    runs = [louvain(g_core, seed) for seed in range(RUNS)]
    q_runs = [modularity(g_core, r) for r in runs]
    pair_rows = []
    for name, ms in CLUSTERS.items():
        ms = sorted(m for m in ms if m in g_core)
        for i, a in enumerate(ms):
            for b in ms[i + 1 :]:
                together = sum(r[a] == r[b] for r in runs) / RUNS
                pair_rows.append((name, short(a), short(b), round(together, 2)))
    write_csv("prefix_pair_stability.csv", ["cluster", "a", "b", "co_assigned_fraction"], pair_rows)
    # cross-prefix pairs that are almost always together (the 'should be one family' signal)
    cross = []
    keyed = sorted(g_core)
    for i, a in enumerate(keyed):
        for b in keyed[i + 1 :]:
            if PREFIX_OF.get(a) and PREFIX_OF.get(a) != PREFIX_OF.get(b) and b in g_core.get(a, {}):
                together = sum(r[a] == r[b] for r in runs) / RUNS
                if together >= 0.9:
                    cross.append((short(a), PREFIX_OF[a], short(b), PREFIX_OF.get(b, ""), round(together, 2), g_core[a][b]))
    write_csv("cross_prefix_cohabitants.csv", ["a", "a_prefix", "b", "b_prefix", "co_assigned_fraction", "edge_weight"], cross)

    # sensitivity: also drop the vocabulary hubs everything references
    HUBS = {"pptmstr.model", "pptmstr.theme", "pptmstr.log", "pptmstr.ui.widgets"}
    g_nohub = undirected(MOD_EDGES, COMPOSITION_ROOTS | HUBS)
    lv_nohub = louvain(g_nohub)
    nohub = {f"C{c}": sorted(short(m) for m in g_nohub if lv_nohub[m] == c) for c in sorted(set(lv_nohub.values()))}

    mono = {short(m): monolith(m) for m in MONOLITHS}
    for name, d in mono.items():
        write_csv(f"monolith_{name}_edges.csv", ["src", "dst", "weight"], d["edges"])
    t_imports, t_patches, t_summary = test_scan()
    write_csv("test_imports.csv", ["test_module", "source_module"], sorted({(a, short(b)) for a, b, _ in t_imports}))
    write_csv("test_dotted_patches.csv", ["test_module", "test", "path", "target_module", "line"], [list(p.values()) for p in t_patches])
    tests_per_source = Counter(short(b) for b in {(a, b) for a, b, _ in t_imports} for b in [b[1]])
    patch_per_source = Counter(short(p["target_module"]) for p in t_patches)

    modules = []
    for m in NODES:
        modules.append(
            {
                "module": short(m),
                "loc": LOC[m],
                "top_level_defs": len(DEFS[m]),
                "fan_in": fan_in[m],
                "fan_out": fan_out[m],
                "weighted_in": sum(w for (s, d), w in MOD_EDGES.items() if d == m),
                "weighted_out": sum(w for (s, d), w in MOD_EDGES.items() if s == m),
                "prefix_cluster": PREFIX_OF.get(m, ""),
                "community": f"C{lv_core[m]}" if m in lv_core else "",
                "label_prop": f"L{lp_core[m]}" if m in lp_core else "",
                "test_modules_importing": tests_per_source.get(short(m), 0),
                "dotted_patches": patch_per_source.get(short(m), 0),
            }
        )
    write_csv("modules.csv", list(modules[0]), [list(r.values()) for r in modules])

    reach, todo = {"pptmstr.__main__"}, ["pptmstr.__main__"]
    while todo:
        v = todo.pop()
        for (src, dst) in import_kinds:
            if src != v:
                continue
            parts = dst.split(".")
            for p in [".".join(parts[: i + 1]) for i in range(len(parts))]:
                if p not in reach:
                    reach.add(p)
                    todo.append(p)
    summary = {
        "unreachable_from_entry_point": sorted(short(m) for m in SOURCES if m not in reach),
        "modules": len(SOURCES),
        "loc": sum(LOC.values()),
        "import_edges": len(import_kinds),
        "import_edges_by_kind": {
            k: sum(1 for c in import_kinds.values() if c[k]) for k in ("eager", "lazy", "typing")
        },
        "lazy_only_edges": sum(1 for c in import_kinds.values() if c["lazy"] and not c["eager"] and not c["typing"]),
        "refs_total": len(REFS),
        "refs_cross_module": sum(1 for r in REFS if r.dst_mod != r.src_mod),
        "refs_unresolved": sum(1 for r in REFS if not r.resolved),
        "weighted_module_edges": len(MOD_EDGES),
        "scc": {k: [[short(x) for x in c] for c in v] for k, v in SCC.items()},
        "cycles_closed_only_by_lazy_import": [
            {"lazy_import": f"{short(s)} -> {short(d)}", "eager_path_back": [short(x) for x in p]} for s, d, p in lazy_cycles
        ],
        "layer_violations_non_ui_imports_ui": [(short(s), short(d), k) for s, d, k in LAYER_VIOLATIONS],
        "sdk_importers": [short(m) for m in SDK_IMPORTERS],
        "imgui_importers_outside_ui": [short(m) for m in IMGUI_IMPORTERS if not is_ui(m)],
        "modularity": {
            "louvain_with_app": round(q_all, 3),
            "louvain_without_app": round(q_core, 3),
            "prefix_partition_without_app": round(q_prefix, 3),
            "louvain_without_app_shuffled_runs": [round(min(q_runs), 3), round(max(q_runs), 3)],
            "louvain_without_app_or_hubs": round(modularity(g_nohub, lv_nohub), 3),
        },
        "louvain_with_app_communities": len(set(lv_all.values())),
        "louvain_without_app_communities": len(set(lv_core.values())),
        "label_prop_communities": len(set(lp_core.values())),
        "tests": t_summary,
        "dynamic": dynamic_census(),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    (OUT / "communities.json").write_text(
        json.dumps(
            {
                "louvain_without_app": communities,
                "louvain_with_app": {f"C{c}": sorted(short(m) for m in g_all if lv_all[m] == c) for c in sorted(set(lv_all.values()))},
                "prefix_clusters": cluster_rows,
                "prefix_clusters_without_app_edges": cluster_rows_core,
                "prefix_vs_community": agree,
                "louvain_without_app_or_hubs": nohub,
            },
            indent=1,
        )
    )
    (OUT / "monoliths.json").write_text(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "edges"} for k, v in mono.items()}, indent=1))

    # ------------------------------------------------ tables.md
    L: list[str] = ["# Generated tables (graph.py)\n"]
    L.append("## Headline\n")
    for k in ("unreachable_from_entry_point", "modules", "loc", "import_edges", "import_edges_by_kind", "lazy_only_edges", "refs_total", "refs_cross_module", "refs_unresolved", "weighted_module_edges", "modularity"):
        L.append(f"- {k}: {summary[k]}")
    L.append("\n## SCCs\n")
    for k, v in summary["scc"].items():
        L.append(f"- {k}: {len(v)} SCC(s); sizes {[len(c) for c in v]}")
        for c in v:
            L.append(f"  - {', '.join(c)}")
    L.append("\n## Fan-in / fan-out (top 15 by weighted in)\n")
    L.append("| module | loc | fan_in | fan_out | w_in | w_out | community |\n|---|---|---|---|---|---|---|")
    for r in sorted(modules, key=lambda r: -r["weighted_in"])[:15]:
        L.append(f"| {r['module']} | {r['loc']} | {r['fan_in']} | {r['fan_out']} | {r['weighted_in']} | {r['weighted_out']} | {r['community']} |")
    L.append("\n## Prefix clusters (edges touching app/__main__ removed)\n")
    L.append("| cluster | n | internal | out | in | cohesion | purity | top partners |\n|---|---|---|---|---|---|---|---|")
    for name, r in cluster_rows_core.items():
        L.append(f"| {name} | {r['members']} | {r['internal']} | {r['out']} | {r['in']} | {r['cohesion']} | {agree[name]['purity']} | {', '.join(r['top_partners'])} |")
    L.append("\n## Louvain communities (without app/__main__)\n")
    L.append("| id | n | loc | cohesion | prefix mix | members |\n|---|---|---|---|---|---|")
    for c in communities:
        L.append(f"| {c['id']} | {c['size']} | {c['loc']} | {c['metrics']['cohesion']} | {c['prefix_mix']} | {', '.join(c['members'])} |")
    L.append("\n## Prefix cluster -> community spread\n")
    for name, a in agree.items():
        L.append(f"- **{name}** (purity {a['purity']}): " + "; ".join(f"{c}: {', '.join(ms)}" for c, ms in a["communities"].items()))
    L.append("\n## Louvain without app/__main__ and hubs (model, theme, log, ui.widgets)\n")
    for c, ms in nohub.items():
        L.append(f"- {c}: {', '.join(ms)}")
    L.append("\n## Same-prefix pairs split in >= 70% of 30 shuffled runs\n")
    for row in pair_rows:
        if row[3] <= 0.3:
            L.append(f"- {row[0]}: {row[1]} / {row[2]} together {row[3]}")
    L.append("\n## Cross-prefix pairs together in >= 90% of runs (direct edge)\n")
    for row in cross:
        L.append(f"- {row[0]} ({row[1]}) + {row[2]} ({row[3] or '-'}) {row[4]} w={row[5]}")
    L.append("\n## Monoliths\n")
    L.append("| module | loc | units | edges | components | largest | singletons | communities | Q | cut |\n|---|---|---|---|---|---|---|---|---|---|")
    for k, d in mono.items():
        L.append(f"| {k} | {d['loc']} | {d['units']} | {d['intra_edges']} | {d['components']} | {d['largest_component']} | {d['singletons']} | {d['communities']} | {d['modularity']} | {d['cut_weight']}/{d['intra_weight']} |")
    for k, d in mono.items():
        L.append(f"\n### {k}\n\nexpanded: {d['expanded']}\n")
        L.append("| c | units | lines | hubs | uses |\n|---|---|---|---|---|")
        for c in sorted(d["community_detail"], key=lambda c: -c["lines"])[:10]:
            L.append(f"| {c['id']} | {c['size']} | {c['lines']} | {', '.join(c['hubs'][:4])} | {', '.join(c['external_modules'][:4])} |")
    L.append("\n## Tests\n")
    for k, v in t_summary.items():
        L.append(f"- {k}: {v}")
    L.append("\n| source module | dotted patches | test modules importing |\n|---|---|---|")
    for m, n in patch_per_source.most_common(15):
        L.append(f"| {m} | {n} | {tests_per_source.get(m, 0)} |")
    L.append("\n## Dynamic dispatch census\n")
    for k, v in summary["dynamic"].items():
        L.append(f"- {k}: total {sum(v.values())} — {dict(list(v.items())[:8])}")
    L.append("\n## Layer violations (non-ui importing ui, excluding app/__main__)\n")
    for s, d, k in summary["layer_violations_non_ui_imports_ui"]:
        L.append(f"- {s} -> {d} ({k})")
    L.append(f"\nimgui importers outside ui/: {summary['imgui_importers_outside_ui']}")
    L.append(f"\nSDK importers: {summary['sdk_importers']}")
    L.append("\n## Cycles closed only by a lazy import\n")
    for c in summary["cycles_closed_only_by_lazy_import"]:
        L.append(f"- {c['lazy_import']} (back: {' -> '.join(c['eager_path_back'])})")
    (OUT / "tables.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
