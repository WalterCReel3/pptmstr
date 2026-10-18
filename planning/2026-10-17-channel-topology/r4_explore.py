#!/usr/bin/env python3
"""
Mechanical checks behind r4-scenarios.md: counts that follow from a topology's
shape and from the store rules the plan and Decided 1-4 fix, not from how a model
behaves.

usage: r4_explore.py [--max-n N]

Stdlib only; reads nothing and writes nothing. Every number it prints is a count
under a model this file states. None of it is a measurement of agents. The two
latency constants are R3's figures, re-run against the 2026-10-17 folders by R4
(r3_count.py printed the same values).

Sections:
  1. receipt and answered states a needs-answer message can be in, per option;
  2. sends, wakes, parks and hops as N grows (S-a broadcast, S-b leaf to leaf);
  3. operator answers and relays for one decision every team needs (S-a);
  4. parks on a cross-team request under every rung assignment (S-h);
  5. interleavings that produce a double answer (S-c);
  6. forward-on-receipt copies on symmetric links, by forwarding rule (loops);
  7. pairs of teams cut off while one lead is down (S-d, S-g);
  8. what joining, leaving and ganging costs in links and records (S-e, S-f);
  9. reply-all wakes in one group round (added scenario S-j);
 10. reply latency from hop count, using R3's per-hop figure.
"""

from __future__ import annotations

import argparse
import itertools
import math
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

# R3, 2026-10-17 run, re-run by R4: median ack latency (the per-hop cost of a
# forward, a lower bound) and median needs-answer round trip with no hop between.
HOP_MEDIAN_MIN = 0.61
HOP_P90_MIN = 0.89
DIRECT_RT_MEDIAN_MIN = 0.71
DIRECT_RT_P90_MIN = 1.81

# Per-reader state of one delivered-or-held message. ANSWERED implies READ.
READER_STATES = ("held", "delivered", "read", "answered")

Graph = dict[str, set[str]]


# --------------------------------------------------------------------------- graphs


def _add(g: Graph, a: str, b: str) -> None:
    g.setdefault(a, set()).add(b)
    g.setdefault(b, set()).add(a)


def teams(n: int) -> list[str]:
    return [f"T{i}" for i in range(1, n + 1)]


def mesh(n: int) -> Graph:
    """
    Every pair of teams linked: pairs only, no coordinator.
    """
    g: Graph = {t: set() for t in teams(n)}
    for a, b in itertools.combinations(teams(n), 2):
        _add(g, a, b)
    return g


def star(n: int) -> Graph:
    """
    One coordinator lead C linked to each of n teams; teams not linked to each other.
    """
    g: Graph = {t: set() for t in teams(n)}
    for t in teams(n):
        _add(g, "C", t)
    return g


def tree(n: int, branching: int) -> Graph:
    """
    n teams as leaves under coordinator leads, each with at most ``branching``
    children, levels added until one root remains.
    """
    g: Graph = {t: set() for t in teams(n)}
    level = teams(n)
    depth = 0
    while len(level) > 1:
        depth += 1
        parents: list[str] = []
        for i in range(0, len(level), branching):
            group = level[i : i + branching]
            if len(group) == 1 and parents:
                # A lone child joins the previous parent rather than getting its own.
                _add(g, parents[-1], group[0])
                continue
            p = f"C{depth}.{len(parents)}"
            for child in group:
                _add(g, p, child)
            parents.append(p)
        level = parents
    return g


def distance(g: Graph, a: str, b: str) -> int:
    seen = {a: 0}
    q = deque([a])
    while q:
        x = q.popleft()
        if x == b:
            return seen[x]
        for y in g[x]:
            if y not in seen:
                seen[y] = seen[x] + 1
                q.append(y)
    return -1


def path(g: Graph, a: str, b: str) -> list[str]:
    prev: dict[str, str | None] = {a: None}
    q = deque([a])
    while q:
        x = q.popleft()
        if x == b:
            break
        for y in sorted(g[x]):
            if y not in prev:
                prev[y] = x
                q.append(y)
    out = [b]
    while prev[out[-1]] is not None:
        out.append(prev[out[-1]])  # type: ignore[arg-type]
    return out[::-1]


def edges(g: Graph) -> int:
    return sum(len(v) for v in g.values()) // 2


def farthest_teams(g: Graph, n: int) -> tuple[str, str]:
    """
    The pair of teams farthest apart: S-b's "leaf in a different branch".
    """
    best = ("T1", "T2", -1)
    for a, b in itertools.combinations(teams(n), 2):
        d = distance(g, a, b)
        if d > best[2]:
            best = (a, b, d)
    return best[0], best[1]


# --------------------------------------------------------------------------- 1


def section_1(max_n: int) -> None:
    print(
        "== 1. States one needs-answer message can be in (per-reader state in", READER_STATES, ")"
    )
    print("   pair (O2/O3 per hop, O4 per copy): one reader -> 4 states, single-valued")
    print("   O1 group of N: N-1 readers, one record holding a per-reader state each")
    print("   O4 fan-out to K=N-1: K pair records sharing a send_id; the aggregate over them")
    print(
        "   'ambiguous' = joint states where 'answered' differs between any-reader and all-readers"
    )
    print(f"   {'N':>3} {'joint states':>13} {'ambiguous':>10} {'max NEEDS YOU rows':>19}")
    for n in range(2, max_n + 1):
        r = n - 1
        joint = 0
        ambiguous = 0
        for combo in itertools.product(READER_STATES, repeat=r):
            joint += 1
            answered = [s == "answered" for s in combo]
            if any(answered) != all(answered):
                ambiguous += 1
        # Decided 4: a row per (message, reader still owing) once aged or the reader is dead.
        print(f"   {n:>3} {joint:>13} {ambiguous:>10} {r:>19}")
    print(
        "   The joint count is the same for O1 and O4 with the same readers: O4 stores it as K"
        " single-valued records, O1 as one multi-valued record. The any/all question is the same."
    )
    # O2 chain A -> C -> B: the second hop exists only once C has read the first.
    chain = 0
    for s1 in READER_STATES:
        if s1 in ("held", "delivered"):
            chain += 1  # nothing forwarded yet
        else:
            chain += 1 + len(READER_STATES)  # not forwarded, or forwarded in any state
    print(
        f"   O2 one-coordinator chain A->C->B: {chain} reachable (hop1, hop2) states;"
        " each record single-valued, but 'A's question answered' is a convention across two"
    )
    print()


# --------------------------------------------------------------------------- 2


@dataclass(frozen=True)
class Cost:
    sends: int  # tool calls that send (each one parks on a reviewed sender)
    messages: int  # message records created
    wakes: int  # SessionWoken effects before coalescing
    hops: int  # sequential lead turns on the critical path


def broadcast_cost(option: str, n: int) -> Cost:
    """
    S-a style: one team tells all others one thing. Pair topologies route through the
    shortest paths; a coordinator re-sends once per neighbour it forwards to.
    """
    if option == "O1 group":
        return Cost(1, 1, n - 1, 1)
    if option == "O2 mesh":
        return Cost(n - 1, n - 1, n - 1, 1)
    if option == "O4 mesh+fanout":
        return Cost(1, n - 1, n - 1, 1)
    if option == "O2 star":
        # T1 -> C, then C sends to each of the other n-1.
        return Cost(1 + (n - 1), 1 + (n - 1), 1 + (n - 1), 2)
    if option == "O4 star+fanout":
        return Cost(2, 1 + (n - 1), 1 + (n - 1), 2)
    if option.startswith("O2/O3 tree"):
        g = tree(n, 3)
        # Flood from T1 along the tree: every node but T1 receives once; every
        # non-leaf receiver (and T1) sends once per child-or-parent it forwards to.
        msgs = len(g) - 1
        far = max(distance(g, "T1", t) for t in teams(n))
        return Cost(msgs, msgs, msgs, far)
    raise ValueError(option)


def leaf_to_leaf_cost(option: str, n: int) -> Cost:
    """
    S-b: a request from one team to the farthest other team, and its answer.
    """
    if option == "O1 group":
        # Every send on the channel wakes every other member.
        return Cost(2, 2, 2 * (n - 1), 2)
    if option in ("O2 mesh", "O4 mesh+fanout"):
        return Cost(2, 2, 2, 2)
    g = star(n) if "star" in option else tree(n, 3)
    a, b = farthest_teams(g, n)
    d = distance(g, a, b)
    return Cost(2 * d, 2 * d, 2 * d, 2 * d)


OPTIONS_2 = (
    "O1 group",
    "O2 mesh",
    "O4 mesh+fanout",
    "O2 star",
    "O4 star+fanout",
    "O2/O3 tree(b=3)",
)


def links(option: str, n: int) -> int:
    if option == "O1 group":
        return 1
    if "mesh" in option:
        return edges(mesh(n))
    if "star" in option:
        return edges(star(n))
    return edges(tree(n, 3))


def coordinators(option: str, n: int) -> int:
    if "star" in option:
        return 1
    if "tree" in option:
        return len(tree(n, 3)) - n
    return 0


def section_2(max_n: int) -> None:
    print("== 2. Growth with N teams (sends = parks when every sender is on a reviewed rung)")
    print("   broadcast = one team tells all; leaf->leaf = request to the farthest team + answer")
    header = (
        f"   {'option':<17}{'N':>3}{'links':>6}{'coord':>6}"
        f" | {'bc sends':>8}{'msgs':>5}{'wakes':>6}{'hops':>5}"
        f" | {'l2l sends':>9}{'wakes':>6}{'hops':>5}"
    )
    print(header)
    for n in sorted({3, 4, 6, 9, 12, max_n}):
        for opt in OPTIONS_2:
            bc = broadcast_cost(opt, n)
            ll = leaf_to_leaf_cost(opt, n)
            print(
                f"   {opt:<17}{n:>3}{links(opt, n):>6}{coordinators(opt, n):>6}"
                f" | {bc.sends:>8}{bc.messages:>5}{bc.wakes:>6}{bc.hops:>5}"
                f" | {ll.sends:>9}{ll.wakes:>6}{ll.hops:>5}"
            )
        print()
    print("   O1 here wakes every member on every send; an O1 that addresses a send to one")
    print("   member wakes 2 for leaf->leaf, but then 'the channel' no longer means 'everyone'.")
    print(
        "   Every option also adds one declaration park on the receiving board when the"
        " request becomes work (Decided 1), the same in all rows."
    )
    print()


# --------------------------------------------------------------------------- 3


def section_3() -> None:
    print("== 3. One operator decision that three teams need (S-a), under Decided 3")
    print("   A table of definitions, not a computation: it states what each option's records")
    print("   allow, so the count is visible next to the others.")
    print("   (a decision carrying a channel is readable by every lead on that channel)")
    rows = [
        ("O1 group of 3", 1, 0, "raised on the group channel; all three read it"),
        ("O2 mesh (3 pairs)", 1, 1, "raised on T1-T2; T3 needs a relay (failure 2 again)"),
        ("O2 mesh, raise per pair", 2, 0, "raised on T1-T2 and T1-T3: operator answers twice"),
        ("O2 star (C + 3)", 1, 2, "raised on T1-C; C forwards to T2 and T3"),
        ("O2 star + forwarded decision", 1, 2, "same sends, but by reference: no paraphrase"),
        # Revised (R5, S1 finding 2): this row said "0 relays if the root raises",
        # which contradicted r4-scenarios.md. Under Decided 3 as written a decision
        # sits on the one channel it was raised on, whoever raises it.
        ("O3 tree", 1, 2, "as O2 star, whoever raises: a decision sits on one pair"),
        ("O4 star + fan-out", 1, 1, "C fans the forward: one send, one park, two copies"),
        ("O4 gang-scoped decision", 1, 0, "needs gang membership in decisions_readable_by"),
        ("O5 shared board", 1, 0, "decision on the shared board; every member reads"),
    ]
    print(f"   {'option':<30}{'answers':>8}{'relays':>7}  how")
    for name, answers, relays, how in rows:
        print(f"   {name:<30}{answers:>8}{relays:>7}  {how}")
    print("   Generalised to N teams: relays are 0 (O1, gang-scoped O4, O5), N-2 (mesh), N-1")
    print("   from a star's coordinator, or a fan-out of 1 send (O4).")
    print()


# --------------------------------------------------------------------------- 4


def section_4() -> None:
    print("== 4. Parks for one request + answer, every rung assignment (S-h, Decided 1)")
    print("   A sends, (C forwards), B declares the work and replies, (C forwards back).")
    print("   1 = reviewed (STRICT/PERMISSIVE), 0 = AUTONOMOUS. Parks = reviewed sends +")
    print("   B's declaration if B is reviewed.")
    print("   direct pair (O1, O2 mesh, O4):")
    for a, b in itertools.product((0, 1), repeat=2):
        parks = a + b + b  # A's send, B's reply, B's declaration
        print(f"     A={a} B={b}: {parks} parks")
    print("   via one coordinator C (O2 star, O3):")
    worst_extra = 0
    for a, c, b in itertools.product((0, 1), repeat=3):
        parks = a + c + b + c + b  # A->C, C->B, B->C, C->A, B's declaration
        worst_extra = max(worst_extra, 2 * c)
        print(f"     A={a} C={c} B={b}: {parks} parks")
    print(
        f"   A reviewed coordinator adds {worst_extra} parks to every exchange it carries,"
        " whatever A and B are. Each extra park is an operator response time R3 could not measure."
    )
    print()


# --------------------------------------------------------------------------- 5


def interleavings(seqs: Sequence[Sequence[str]]) -> Iterator[list[str]]:
    """
    Every merge of the sequences that keeps each one's order.
    """
    if all(not s for s in seqs):
        yield []
        return
    for i, s in enumerate(seqs):
        if s:
            rest = list(seqs)
            rest[i] = s[1:]
            for tail in interleavings(rest):
                yield [s[0], *tail]


def double_answer(k: int, guarded: bool) -> tuple[int, int, int]:
    """
    k recipients each do check-then-reply. Unguarded: a recipient replies when its
    check saw no answer, and the store takes every reply. Guarded: the store refuses
    a reply once an answer is recorded (first answer wins); a refused reply is a
    wasted turn. Returns (interleavings, with >= 2 accepted replies, with a wasted turn).
    """
    seqs = [[f"c{i}", f"r{i}"] for i in range(k)]
    total = doubles = wasted = 0
    for order in interleavings(seqs):
        total += 1
        accepted, refused = replay(order, guarded)
        doubles += accepted >= 2
        wasted += refused >= 1
    return total, doubles, wasted


def replay(order: Sequence[str], guarded: bool) -> tuple[int, int]:
    """
    One interleaving of check-then-reply steps, as ``double_answer`` models it.
    Returns (accepted replies, refused replies).
    """
    answered = False
    saw_open: set[str] = set()
    accepted = refused = 0
    for op in order:
        who = op[1:]
        if op[0] == "c":
            if not answered:
                saw_open.add(who)
        elif who in saw_open:
            if guarded and answered:
                refused += 1
            else:
                accepted += 1
                answered = True
    return accepted, refused


def section_5() -> None:
    print("== 5. Two or more recipients and one needs-answer (S-c)")
    print("   model: each recipient checks 'answered?' then replies; no other coordination")
    print(
        f"   {'k':>3}{'interleavings':>14}{'double, unguarded':>19}{'double, guarded':>17}{'wasted turn, guarded':>22}"
    )
    for k in (2, 3, 4):
        t, d, _ = double_answer(k, guarded=False)
        _, dg, w = double_answer(k, guarded=True)
        print(f"   {k:>3}{t:>14}{d:>19}{dg:>17}{w:>22}")
    print("   Timed model: one delivery wakes all k at once, so every check precedes every")
    print("   reply (a turn takes ~0.4-1.9 min in R3). That is the 'checks first' subset:")
    for k in (2, 3):
        seqs = [[f"c{i}", f"r{i}"] for i in range(k)]
        checks_first = [
            o
            for o in interleavings(seqs)
            if max(o.index(f"c{i}") for i in range(k)) < min(o.index(f"r{i}") for i in range(k))
        ]
        # Replayed through the same model, not assumed (R5, S1 finding 5a). Every
        # check precedes every reply, so the double is a consequence of the
        # subset's definition: this confirms the model, it measures no agent.
        doubles = sum(replay(o, guarded=False)[0] >= 2 for o in checks_first)
        print(f"     k={k}: {len(checks_first)} such interleavings, {doubles} double unguarded")
    print("   Per option: O1 wakes all members on one delivery (the timed case).")
    print("   O4 has k independent copies, each answered on its own: k answers by construction;")
    print(
        "   a per-copy guard never fires. O2/O3 route to one recipient chosen by the coordinator:"
    )
    print("   1 answer, serialised in the coordinator. O6 awards to one bidder: 1 actor, k bids.")
    print("   A guard on 'answered' does not stop two recipients both *acting* (declaring work);")
    print("   that needs a take/claim on the message, as claim_task is for a task.")
    print()


# --------------------------------------------------------------------------- 6


def simple_paths_from(g: Graph, origin: str) -> int:
    """
    Copies made when every receiver forwards to every neighbour not already on the
    message's provenance chain: one copy per simple path from origin of length >= 1.
    """
    count = 0
    stack: list[tuple[str, frozenset[str]]] = [(origin, frozenset({origin}))]
    while stack:
        x, chain = stack.pop()
        for y in g[x]:
            if y not in chain:
                count += 1
                stack.append((y, chain | {y}))
    return count


def naive_copies(g: Graph, origin: str, cap: int) -> int:
    """
    Forward to every neighbour but the one it came from; stops only at ``cap``.
    """
    count = 0
    q: deque[tuple[str, str | None]] = deque([(origin, None)])
    while q and count < cap:
        x, came = q.popleft()
        for y in g[x]:
            if y != came:
                count += 1
                q.append((y, x))
    return count


def seen_set_copies(g: Graph, origin: str) -> int:
    """
    Each lead forwards a given original id at most once (the store refuses a second
    forward of an id it already forwarded), to every neighbour but its sender.
    """
    count = 0
    forwarded = {origin}
    q: deque[tuple[str, str | None]] = deque([(origin, None)])
    while q:
        x, came = q.popleft()
        for y in g[x]:
            if y == came:
                continue
            count += 1
            if y not in forwarded:
                forwarded.add(y)
                q.append((y, x))
    return count


def section_6() -> None:
    print("== 6. Forward-on-receipt over symmetric links (O2 lets the operator link a cycle)")
    print("   copies of one message created before the flood stops, by forwarding rule")
    cap = 10_000
    print(
        f"   {'graph':<16}{'links':>6}{'naive (cap)':>13}{'provenance chain':>18}{'seen-set':>10}"
    )
    graphs: list[tuple[str, Graph]] = [
        ("tree b=3, 9", tree(9, 3)),
        ("star 6", star(6)),
        ("triangle", mesh(3)),
        ("mesh 4", mesh(4)),
        ("mesh 5", mesh(5)),
        ("mesh 6", mesh(6)),
        ("mesh 7", mesh(7)),
    ]
    for name, g in graphs:
        n = naive_copies(g, "T1", cap)
        naive = f"{n}" if n < cap else f">={cap}"
        print(
            f"   {name:<16}{edges(g):>6}{naive:>13}{simple_paths_from(g, 'T1'):>18}"
            f"{seen_set_copies(g, 'T1'):>10}"
        )
    print("   On a tree all three rules agree. On a cycle the naive rule never stops; a")
    print("   provenance chain stops it but copies grow factorially with a mesh; a per-lead")
    print("   seen-set bounds copies at 2*links - (leads - 1). Under O3 (tree enforced) the")
    print("   rules agree; under O2 that depends on the operator never linking a cycle.")
    for name, g in graphs:
        assert seen_set_copies(g, "T1") == 2 * edges(g) - (len(g) - 1), name
    print()


# --------------------------------------------------------------------------- 7


def cut_pairs(g: Graph, n: int, down: str) -> tuple[int, int]:
    """
    Pairs of surviving teams with no path that avoids ``down``, out of all such pairs.
    """
    h: Graph = {x: {y for y in ys if y != down} for x, ys in g.items() if x != down}
    alive = [t for t in teams(n) if t != down]
    cut = 0
    pairs = 0
    for a, b in itertools.combinations(alive, 2):
        pairs += 1
        cut += distance(h, a, b) < 0
    return cut, pairs


def section_7() -> None:
    print("== 7. One lead down (S-d member, S-g coordinator): surviving team pairs cut off")
    print("   'cut' = held until the lead resumes (Decided 4 holds, not drops), not lost")
    n = 9
    rows: list[tuple[str, Graph, str]] = [
        ("O2 mesh, a team down", mesh(n), "T1"),
        ("O2 star, a team down", star(n), "T1"),
        ("O2 star, coordinator down", star(n), "C"),
        ("O3 tree b=3, root down", tree(n, 3), "C2.0"),
        ("O3 tree b=3, mid coord down", tree(n, 3), "C1.0"),
    ]
    for name, g, down in rows:
        c, p = cut_pairs(g, n, down)
        print(f"   N={n} {name:<30} {c:>3} of {p:>3} pairs")
    print("   'a team down' counts pairs among the other N-1 teams; a coordinator is not a team.")
    print(
        f"   N={n} O1 group, a member down        {0:>3} of {math.comb(n - 1, 2):>3} pairs"
        " (the channel is not a lead; others keep talking)"
    )
    print()


# --------------------------------------------------------------------------- 8


def section_8() -> None:
    print("== 8. Membership change and ganging: links/records the operator creates")
    print("   A table of definitions, not a computation; the mesh counts are a*b and N.")
    a, b = 4, 5
    print(f"   join one team to N=6;  gang two groups of {a} and {b}")
    rows = [
        (
            "O1 group",
            "1 member record (+ does it read history?)",
            f"1 new channel of {a + b}, or 1 bridge channel of 2",
        ),
        ("O2 mesh", "6 links", f"{a * b} links"),
        (
            "O2 star/tree",
            "1 link (to a coordinator)",
            "1 link between the two coordinators, or 2 to a new one",
        ),
        (
            "O3 tree",
            "1 directed link; refused if 2nd parent",
            "2 links under a new root (peer coordinators = cross-link, refused)",
        ),
        ("O4 star+fanout", "1 link + edit the gang", "1 gang record over the 2 coordinator links"),
        ("O5 shared board", "1 member grant", "1 merged board or a cross-reference"),
    ]
    for name, join, gang in rows:
        print(f"   {name:<16} join: {join:<44} gang: {gang}")
    print("   Leaving: O1 = 1 member record, and messages it owes must be released or reassigned;")
    print("   pairs = unlink 1..k links; a leaving coordinator strands its children (section 7).")
    print()


# --------------------------------------------------------------------------- 9


def section_9(max_n: int) -> None:
    print("== 9. Added S-j (from R2 S18 / MAST FM-1.5): one round where every member replies once")
    print(f"   {'N':>3}{'O1 wakes':>10}{'O2 star wakes':>15}{'O4 mesh+fanout wakes':>22}")
    for n in range(3, max_n + 1):
        o1 = n * (n - 1)
        # star: each team sends to C (1 wake), C fans each on to n-1 (n-1 wakes).
        st = n * (1 + (n - 1))
        o4 = n * (n - 1)
        print(f"   {n:>3}{o1:>10}{st:>15}{o4:>22}")
    print(
        "   app._hold_wake coalesces wakes per session inside WAKE_WINDOW_S, so turns, not wakes,"
    )
    print("   are the cost; each member still gets a turn per window while the round lasts.")
    print()


# --------------------------------------------------------------------------- 10


def section_10() -> None:
    print(
        "== 10. Request->answer latency from hop count (R3 constants; queueing and parks excluded)"
    )
    print(
        f"   direct RT median {DIRECT_RT_MEDIAN_MIN}, p90 {DIRECT_RT_P90_MIN}; per extra hop median"
        f" {HOP_MEDIAN_MIN}, p90 {HOP_P90_MIN} (min)"
    )
    for n in (3, 9, 27):
        for name, g in (("O2 star", star(n)), ("O2/O3 tree b=3", tree(n, 3))):
            a, b = farthest_teams(g, n)
            d = distance(g, a, b)
            extra = 2 * (d - 1)
            med = DIRECT_RT_MEDIAN_MIN + extra * HOP_MEDIAN_MIN
            p90 = DIRECT_RT_P90_MIN + extra * HOP_P90_MIN
            print(
                f"   N={n:<3}{name:<16} path {'-'.join(path(g, a, b))}: {extra} extra hops,"
                f" median {med:.2f}, p90 {p90:.2f}"
            )
        print(
            f"   N={n:<3}{'O1 / mesh / O4':<16} 0 extra hops, median {DIRECT_RT_MEDIAN_MIN:.2f}, p90 {DIRECT_RT_P90_MIN:.2f}"
        )
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    ap.add_argument("--max-n", type=int, default=7)
    args = ap.parse_args()
    section_1(args.max_n)
    section_2(args.max_n)
    section_3()
    section_4()
    section_5()
    section_6()
    section_7()
    section_8()
    section_9(args.max_n)
    section_10()


if __name__ == "__main__":
    main()
