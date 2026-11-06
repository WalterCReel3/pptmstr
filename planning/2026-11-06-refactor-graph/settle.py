"""
Settling runs for the graph headline "prefix modularity 0.079 vs Louvain 0.42",
plus per-file git churn.

Run from the repository root with the repo venv (stdlib only):

    .venv/bin/python planning/2026-11-06-refactor-graph/settle.py

Imports ``graph.py`` beside it for the module graph, Louvain and modularity, and
does not call its ``main()``, so none of graph.py's outputs are rewritten. Writes
``settle.json`` and ``churn.csv`` beside this file.

1. Constrained Louvain: each prefix cluster contracted to one node, the rest free.
2. Null models for Q: degree-preserving edge swaps (weights ride on the edges) and
   a strength-preserving weighted configuration model.
3. Reweighting: unit and log(1+w) weights; does the board* split and C3 survive?
4. Churn from ``git log --numstat``.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import random
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path.cwd()
sys.dont_write_bytecode = True  # importing graph.py must leave nothing beside it

_spec = importlib.util.spec_from_file_location("graph", HERE / "graph.py")
assert _spec and _spec.loader
G = importlib.util.module_from_spec(_spec)
sys.modules["graph"] = G  # dataclasses resolves annotations through sys.modules
sys.argv = sys.argv[:1]  # graph.py reads sys.argv[1] as the root
_spec.loader.exec_module(G)

Graph = dict[str, dict[str, float]]
Part = dict[str, int]

RUNS = 30  # shuffled Louvain runs, as in graph.py
NULLS = 100
TODAY = date(2026, 11, 6)
READ_HALF = {"pptmstr.board", "pptmstr.boardgraph", "pptmstr.digest"}
PERSIST_HALF = {"pptmstr.boardlog", "pptmstr.boardsync", "pptmstr.boardfacts"}


def q(g: Graph, part: Part) -> float:
    return round(G.modularity(g, part), 4)


def core_graph(weight=lambda w: w) -> Graph:
    """graph.py's g_core (app and __main__ removed), with each directed weight mapped."""
    return G.undirected({k: weight(v) for k, v in G.MOD_EDGES.items()}, G.COMPOSITION_ROOTS)


def groups(part: Part) -> dict[int, set[str]]:
    out: dict[int, set[str]] = defaultdict(set)
    for v, c in part.items():
        out[c].add(v)
    return out


def short_set(ms) -> list[str]:
    return sorted(G.short(m) for m in ms)


# ---------------------------------------------------------------- 1. constrained


def contract(g: Graph, blocks: dict[str, set[str]]) -> tuple[Graph, dict[str, str]]:
    """
    ``g`` with each block merged into one node. An edge inside a block becomes a
    self-loop carrying both directions' weight, so node strength and 2m are
    unchanged and graph.py's modularity() and louvain() score it consistently.
    """
    rep = {v: v for v in g}
    for name, ms in blocks.items():
        for v in ms:
            if v in g:
                rep[v] = f"<{name}>"
    h: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for v, nb in g.items():
        h[rep[v]]  # keep isolated nodes
        for w, x in nb.items():
            h[rep[v]][rep[w]] += x
    return {k: dict(v) for k, v in h.items()}, rep


def constrained(g: Graph, blocks: dict[str, set[str]], seed: int | None = None) -> Part:
    h, rep = contract(g, blocks)
    p = G.louvain(h, seed)
    return {v: p[rep[v]] for v in g}


def prefix_blocks(g: Graph) -> dict[str, set[str]]:
    b: dict[str, set[str]] = defaultdict(set)
    for m in g:
        if m in G.PREFIX_OF:
            b[G.PREFIX_OF[m]].add(m)
    return {k: v for k, v in b.items() if len(v) > 1}


def settle_constrained(g: Graph, free: Part) -> dict[str, object]:
    blocks = prefix_blocks(g)
    domain = {k: v for k, v in blocks.items() if not k.startswith("ui ")}
    q_free = G.modularity(g, free)
    q_free_runs = [G.modularity(g, G.louvain(g, s)) for s in range(RUNS)]

    def run(bl: dict[str, set[str]]) -> dict[str, object]:
        det = constrained(g, bl)
        qs = [G.modularity(g, constrained(g, bl, s)) for s in range(RUNS)]
        return {
            "q": round(G.modularity(g, det), 4),
            "cost_vs_free": round(q_free - G.modularity(g, det), 4),
            "q_shuffled_max": round(max(qs), 4),
            "cost_best_vs_best": round(max(q_free_runs) - max(qs), 4),
            "communities": len(set(det.values())),
        }

    # graph.py's own prefix partition, reproduced: unclustered core modules are
    # singletons and every unclustered ui module is one block
    gp: Part = {}
    ids: dict[str, int] = {}
    for m in g:
        lab = G.PREFIX_OF.get(m) or ("ui" if G.is_ui(m) else G.short(m))
        gp[m] = ids.setdefault(lab, len(ids))

    # prefix members take their prefix label; everyone else keeps the free run's community
    def hybrid(base: Part, bl: dict[str, set[str]]) -> Part:
        out = {m: base[m] for m in g}
        top = max(base.values()) + 1
        for i, (_, ms) in enumerate(sorted(bl.items())):
            for m in ms:
                out[m] = top + i
        return out

    det_all = constrained(g, blocks)
    return {
        "q_free": round(q_free, 4),
        "q_free_shuffled": [round(min(q_free_runs), 4), round(max(q_free_runs), 4)],
        "graph_py_prefix_partition": q(g, gp),
        "all_prefix_clusters_contracted": run(blocks),
        "domain_prefixes_only_contracted": {"clusters": sorted(domain), **run(domain)},
        "one_cluster_at_a_time": {
            name: {"n": len(ms), **run({name: ms})} for name, ms in sorted(blocks.items())
        },
        "fixed_prefix_labels_rest_take_free_louvain": q(g, hybrid(free, blocks)),
        "fixed_domain_prefix_labels_rest_take_free_louvain": q(g, hybrid(free, domain)),
        "contracted_partition": {
            f"K{c}": short_set(ms) for c, ms in sorted(groups(det_all).items())
        },
    }


# ---------------------------------------------------------------- 2. null models


def edge_list(g: Graph) -> list[tuple[str, str, float]]:
    return [(a, b, w) for a in sorted(g) for b, w in sorted(g[a].items()) if a < b]


def from_edges(nodes, edges) -> Graph:
    h: dict[str, dict[str, float]] = {v: {} for v in nodes}
    for a, b, w in edges:
        h[a][b] = h[a].get(b, 0.0) + w
        h[b][a] = h[b].get(a, 0.0) + w
    return h


SWAP_STATS: dict[str, list[float]] = defaultdict(list)


def swap_null(
    g: Graph, rng: random.Random, per_edge: int = 10, equal_weight: bool = False, tag: str = ""
) -> Graph:
    """
    Maslov-Sneppen double-edge swaps on the simple graph; each weight stays with
    its edge slot, so the degree sequence and the weight multiset are exact but a
    node's strength is not: a hub's heavy edges can land on a leaf. With
    ``equal_weight`` only edges of equal weight swap, which keeps every strength
    exact too, at the price of freezing every edge whose weight is unique.
    """
    edges = [[a, b, w] for a, b, w in edge_list(g)]
    present = {frozenset((a, b)) for a, b, _ in edges}
    n, done, tries = len(edges), 0, 0
    by_w: dict[float, list[int]] = defaultdict(list)
    for i, (_, _, w) in enumerate(edges):
        by_w[w].append(i)
    while done < per_edge * n and tries < per_edge * n * 20:
        tries += 1
        i = rng.randrange(n)
        j = rng.choice(by_w[edges[i][2]]) if equal_weight else rng.randrange(n)
        if i == j:
            continue
        a, b, wi = edges[i]
        c, d, wj = edges[j]
        if rng.random() < 0.5:
            c, d = d, c
        if len({a, b, c, d}) < 4:
            continue
        e1, e2 = frozenset((a, d)), frozenset((c, b))
        if e1 in present or e2 in present:
            continue
        present -= {frozenset((a, b)), frozenset((c, d))}
        present |= {e1, e2}
        edges[i] = [a, d, wi]
        edges[j] = [c, b, wj]
        done += 1
    SWAP_STATS[tag].append(done / n)
    return from_edges(g, [tuple(e) for e in edges])


def config_null(g: Graph, rng: random.Random) -> tuple[Graph, float]:
    """
    Weighted configuration model on integer strength stubs: strengths exact before
    self-loops are dropped. Returns the graph and the fraction of weight lost to
    self-loops.
    """
    stubs = [v for v in sorted(g) for _ in range(round(sum(g[v].values())))]
    rng.shuffle(stubs)
    edges, lost = [], 0
    for a, b in zip(stubs[::2], stubs[1::2]):
        if a == b:
            lost += 1
        else:
            edges.append((a, b, 1.0))
    return from_edges(g, edges), lost / max(1, len(stubs) // 2)


def settle_null(g: Graph, q_real: float) -> dict[str, object]:
    """
    Four nulls, because no single one preserves both degree and strength on a
    graph whose heavy edges sit on hubs. Each is scored with the same
    deterministic Louvain as the real graph.
    """
    rng = random.Random(20261106)
    unit = core_graph(lambda w: 1)
    q_unit = G.modularity(unit, G.louvain(unit))
    weights = Counter(w for _, _, w in edge_list(g))
    mobile = sum(w * c for w, c in weights.items() if c > 1) / sum(
        w * c for w, c in weights.items()
    )
    out: dict[str, object] = {
        "q_real": round(q_real, 4),
        "q_real_unit_weights": round(q_unit, 4),
        "n": NULLS,
    }
    plans = {
        "weighted_swap_weights_ride_edges": (g, q_real, lambda: swap_null(g, rng, tag="w")),
        "weighted_swap_equal_weight_only": (
            g,
            q_real,
            lambda: swap_null(g, rng, equal_weight=True, tag="eq"),
        ),
        "unit_weight_swap_vs_unit_real": (unit, q_unit, lambda: swap_null(unit, rng, tag="u")),
        "strength_configuration_model": (g, q_real, None),
    }
    for name, (base, real, make) in plans.items():
        qs, lost = [], []
        for _ in range(NULLS):
            if make is None:
                h, l = config_null(base, rng)
                lost.append(l)
            else:
                h = make()
            qs.append(G.modularity(h, G.louvain(h)))
        mu, sd = statistics.mean(qs), statistics.stdev(qs)
        out[name] = {
            "real": round(real, 4),
            "mean": round(mu, 4),
            "sd": round(sd, 4),
            "min": round(min(qs), 4),
            "max": round(max(qs), 4),
            "z_of_real": round((real - mu) / sd, 2),
            **({"self_loop_weight_dropped_mean": round(statistics.mean(lost), 4)} if lost else {}),
        }
    for name, tag in (
        ("weighted_swap_weights_ride_edges", "w"),
        ("weighted_swap_equal_weight_only", "eq"),
        ("unit_weight_swap_vs_unit_real", "u"),
    ):
        out[name]["swaps_done_per_edge_mean"] = round(statistics.mean(SWAP_STATS[tag]), 2)
    out["weighted_swap_equal_weight_only"]["weight_on_swappable_edges"] = round(mobile, 3)
    return out


# ---------------------------------------------------------------- 3. reweighting

C3 = {
    f"pptmstr.{m}"
    for m in json.loads((HERE / "communities.json").read_text())["louvain_without_app"][3][
        "members"
    ]
}


def together(p: Part, ms: set[str]) -> bool:
    return len({p[m] for m in ms if m in p}) == 1


def board_split(p: Part) -> bool:
    return (
        together(p, READ_HALF)
        and together(p, PERSIST_HALF)
        and p[min(READ_HALF)] != p[min(PERSIST_HALF)]
    )


def c3_match(p: Part) -> dict[str, object]:
    best = max(groups(p).values(), key=lambda ms: len(ms & C3) / len(ms | C3))
    return {
        "jaccard": round(len(best & C3) / len(best | C3), 2),
        "kept": short_set(best & C3),
        "lost": short_set(C3 - best),
        "gained": short_set(best - C3),
    }


def settle_weighting(name: str, weight) -> dict[str, object]:
    g = core_graph(weight)
    det = G.louvain(g)
    runs = [G.louvain(g, s) for s in range(RUNS)]
    c3 = sorted(C3 & set(g))
    pairs = [(a, b) for i, a in enumerate(c3) for b in c3[i + 1 :]]
    pair_rate = statistics.mean(sum(r[a] == r[b] for r in runs) / RUNS for a, b in pairs)
    return {
        "weighting": name,
        "q": q(g, det),
        "communities": len(set(det.values())),
        "board_split_deterministic": board_split(det),
        "board_split_fraction_of_shuffled_runs": round(sum(board_split(r) for r in runs) / RUNS, 2),
        "read_half_together_fraction": round(sum(together(r, READ_HALF) for r in runs) / RUNS, 2),
        "persistence_half_together_fraction": round(
            sum(together(r, PERSIST_HALF) for r in runs) / RUNS, 2
        ),
        "c3_best_match_deterministic": c3_match(det),
        "c3_mean_pair_coassignment": round(pair_rate, 2),
        "partition": {f"C{c}": short_set(ms) for c, ms in sorted(groups(det).items())},
    }


# ---------------------------------------------------------------- 4. churn

HOT = ["app.py", "store.py", "bus.py", "model.py", "driver.py", "ui/launcher.py", "ui/inbox.py"]
MONTHS = ["2026-08", "2026-09", "2026-10", "2026-11"]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def churn() -> tuple[list[dict[str, object]], dict[str, object]]:
    """
    One pass over first-parent-agnostic, merge-free history of pptmstr/. Author
    dates. A path's history is by path, so a file's life under an earlier name is
    not counted (``all_time_follow`` adds ``git log --follow`` for comparison).
    """
    head = git("rev-parse", "HEAD").strip()
    log = git("log", "--no-merges", "--numstat", "--format=@%H %aI", "HEAD", "--", "pptmstr/")
    d30, d90 = TODAY - timedelta(days=30), TODAY - timedelta(days=90)
    stats: dict[str, dict[str, object]] = defaultdict(lambda: Counter())
    when = None
    for line in log.splitlines():
        if line.startswith("@"):
            when = datetime.fromisoformat(line.split()[1]).date()
            continue
        if not line.strip():
            continue
        add, rem, path = line.split("\t", 2)
        if "=>" in path or not path.endswith(".py"):
            continue
        s = stats[path]
        s["all"] += 1
        s["lines"] += (int(add) if add != "-" else 0) + (int(rem) if rem != "-" else 0)
        if when >= d90:
            s["d90"] += 1
            s["lines90"] += (int(add) if add != "-" else 0) + (int(rem) if rem != "-" else 0)
        if when >= d30:
            s["d30"] += 1
        s[when.strftime("%Y-%m")] += 1
    rows = []
    for m, p in sorted(G.SOURCES.items(), key=lambda kv: str(kv[1])):
        rel = str(p.relative_to(ROOT))
        s = stats.get(rel, Counter())
        follow = len(
            git("log", "--no-merges", "--follow", "--format=%H", "HEAD", "--", rel).split()
        )
        rows.append(
            {
                "file": rel,
                "loc": G.LOC[m],
                "commits_30d": s["d30"],
                "commits_90d": s["d90"],
                "commits_all": s["all"],
                "commits_all_follow": follow,
                "lines_changed_90d": s["lines90"],
                "lines_changed_all": s["lines"],
                **{f"commits_{mo}": s[mo] for mo in MONTHS},
            }
        )
    rows.sort(
        key=lambda r: (-r["commits_90d"], -r["commits_30d"], -r["lines_changed_90d"], r["file"])
    )
    hot = {f: next(r for r in rows if r["file"] == f"pptmstr/{f}") for f in HOT}
    meta = {
        "head": head,
        "today": TODAY.isoformat(),
        "since_30d": d30.isoformat(),
        "since_90d": d90.isoformat(),
        "per_month_hot_files": {
            f: {mo: r[f"commits_{mo}"] for mo in MONTHS} for f, r in hot.items()
        },
        "top10_by_commits_90d": [
            {
                k: r[k]
                for k in (
                    "file",
                    "commits_30d",
                    "commits_90d",
                    "commits_all",
                    "lines_changed_90d",
                    "lines_changed_all",
                )
            }
            for r in rows[:10]
        ],
        "top10_by_lines_changed_90d": [
            (r["file"], r["lines_changed_90d"])
            for r in sorted(rows, key=lambda r: -r["lines_changed_90d"])[:10]
        ],
    }
    return rows, meta


# ---------------------------------------------------------------- run


def main() -> None:
    g = core_graph()
    free = G.louvain(g)
    result = {
        "graph": {
            "nodes": len(g),
            "undirected_edges": len(edge_list(g)),
            "two_m": sum(sum(n.values()) for n in g.values()),
        },
        "constrained": settle_constrained(g, free),
        "null": settle_null(g, G.modularity(g, free)),
        "reweighting": [
            settle_weighting("raw (graph.py)", lambda w: w),
            settle_weighting("unit", lambda w: 1),
            settle_weighting("log1p", lambda w: math.log1p(w)),
        ],
    }
    rows, meta = churn()
    result["churn"] = meta
    with open(HERE / "churn.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (HERE / "settle.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: v for k, v in result.items()}, indent=1))


if __name__ == "__main__":
    main()
