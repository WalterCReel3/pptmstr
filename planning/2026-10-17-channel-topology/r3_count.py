#!/usr/bin/env python3
"""
Measure the 2026-10-17 two-lead file-protocol run from its two message folders.

usage: r3_count.py PPTMSTR_OUTBOX BRAND_CONCERNS [--cross-window SECONDS]

Both folders are only read. The clock is each file's modification time, never the
time an agent typed into its name. Times are printed in this machine's local zone,
which must be the zone the run happened in for the name-vs-mtime skew to mean
anything (it was America/New_York, -0400, on 2026-10-17).

The CLASSIFY table below is the one part that is not mechanical: each entry was set
by reading the message. The KEYWORDS scan beside it is mechanical and is printed next
to it so a reader can see where the two disagree.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import statistics
import sys
from dataclasses import dataclass, field

PROTOCOL_NAME = re.compile(
    r"^(?P<stamp>\d{8}T\d{4})(?P<suffix>[a-z]?)--(?P<topic>[^-][\w-]*?)--"
    r"(?P<sender>brand-lead|pptmstr-lead)--(?P<kind>request|reply|delivery|question|ack)\.md$"
)
# The one message written before PROTOCOL.md existed: the brand lead's handoff.
PRE_PROTOCOL_NAME = re.compile(r"^.+--lead--handoff\.md$")

SIDE_OF_FOLDER = ("pptmstr-lead", "brand-lead")

# Set by reading each message (basis: read). Primary class is one of the five the
# task names: request, answer, approval, status, other. "parties" lists who other
# than the two leads the message carries, and in which direction:
#   in  = that party's decision, approval, instruction or result is relayed in;
#   out = the message says its content is being handed on to that party.
CLASSIFY: dict[str, tuple[str, list[tuple[str, str]], str]] = {
    "pptmstr-copy--lead--handoff.md": (
        "request",
        [("founder", "in")],
        "pre-protocol handoff; founder's pick quoted verbatim",
    ),
    "20261017T1000--lander-shots--pptmstr-lead--reply.md": (
        "request",
        [("operator", "in")],
        "asks size/theme/H2f/copy; 'the operator's go-ahead'",
    ),
    "20261017T1209--lander-shots--brand-lead--reply.md": (
        "answer",
        [("founder", "in")],
        "answers the four; 'The founder picked H2d ... (relayed)'",
    ),
    "20261017T1215--lander-shots--pptmstr-lead--ack.md": (
        "status",
        [("builder", "out")],
        "'passed to the screenshot builder as given'",
    ),
    "20261017T1212--lander-video--brand-lead--request.md": (
        "request",
        [("founder", "in")],
        "v2 scope; 'The operator said yes ... (relayed: the founder's words'",
    ),
    "20261017T1220--lander-video--pptmstr-lead--reply.md": (
        "answer",
        [("builder", "out")],
        "v2 accepted; capture feasibility is 'the builder's first finding'",
    ),
    "20261017T1235--lander-claims--pptmstr-lead--delivery.md": (
        "answer",
        [("builder", "in")],
        "claim verdicts; the runner is named as builder-50 in T1245",
    ),
    "20261017T1219--lander-claims--brand-lead--reply.md": (
        "approval",
        [("founder", "out")],
        "'Accepted'; rewrites 'in front of the founder'",
    ),
    "20261017T1245--lander-claims--pptmstr-lead--reply.md": (
        "answer",
        [("builder", "in")],
        "'builder-50's check'",
    ),
    "20261017T1320--lander-shots--pptmstr-lead--request.md": (
        "request",
        [("builder", "in"), ("operator", "in")],
        "seven points for rulings; 'the builder's reading', 'the operator asked for the board pane'",
    ),
    "20261017T1231--lander-shots--brand-lead--reply.md": (
        "approval",
        [("founder", "out")],
        "rulings on seven points; Let go variant 'a visual choice for the founder'",
    ),
    "20261017T1330--lander-shots--pptmstr-lead--ack.md": (
        "status",
        [("builder", "out")],
        "'Your rulings have all gone to the builder'",
    ),
    "20261017T1345--lander-shots--pptmstr-lead--request.md": (
        "request",
        [],
        "separate board image acceptable?; @1x-only finding",
    ),
    "20261017T1240--lander-shots--brand-lead--reply.md": (
        "approval",
        [],
        "board image accepted; @1x accepted; two string notes",
    ),
    "20261017T1400--lander-shots--pptmstr-lead--reply.md": (
        "status",
        [("builder", "out"), ("operator", "in")],
        "'New from the operator: See is being reshot'",
    ),
    "20261017T1410--lander-shots--pptmstr-lead--delivery.md": (
        "answer",
        [("builder", "in"), ("founder", "out")],
        "v1 delivered; patch 'goes to the founder'",
    ),
    "20261017T1305--lander-shots--brand-lead--reply.md": (
        "approval",
        [("founder", "in"), ("founder", "out")],
        "picks the 41-char reason; 'the founder, today' made the page pptmstr's; 'I'm telling the founder'",
    ),
    "20261017T1420--lander-shots--pptmstr-lead--ack.md": (
        "status",
        [("builder", "out")],
        "'has gone to the builder'",
    ),
    "20261017T1324--lander-masters--brand-lead--request.md": (
        "request",
        [("founder", "in")],
        "v3 scope; 'The founder said yes (relayed'",
    ),
    "20261017T1430--lander-masters--pptmstr-lead--reply.md": (
        "answer",
        [("builder", "out")],
        "v3 on board; 'The builder will measure 1440x900 first'",
    ),
    "20261017T1445--lander-video--pptmstr-lead--request.md": (
        "request",
        [("builder", "in")],
        "'Interim notes from the v2 builder (run)'; asks keep/drop Hold loop",
    ),
    "20261017T1340--lander-video--brand-lead--reply.md": (
        "approval",
        [("founder", "out")],
        "keep the loop; 'the founder decides from the delivered clip'",
    ),
    "20261017T1500--lander-video--pptmstr-lead--delivery.md": (
        "answer",
        [],
        "v2 delivered; no party named",
    ),
    "20261017T1350--lander-video--brand-lead--request.md": (
        "request",
        [("builder", "in")],
        "end-frame ask; 'relayed from our builder's run' (the brand side's builder)",
    ),
    "20261017T1520--lander-video--pptmstr-lead--delivery.md": (
        "answer",
        [("builder", "in")],
        "retimed Hold; 'The builder ran it'; asks (a)/(b)/(c)",
    ),
    "20261017T1525--lander-video--pptmstr-lead--ack.md": (
        "status",
        [("builder", "out")],
        "names the crossing with T1350; end-frame ask 'has gone to the builder'",
    ),
    "20261017T1355--lander-video--brand-lead--reply.md": ("approval", [], "picks (b)"),
    "20261017T1530--lander-video--pptmstr-lead--ack.md": (
        "status",
        [("builder", "out")],
        "'(b) has gone to the builder'",
    ),
    "20261017T1353--lander-masters--brand-lead--request.md": (
        "request",
        [("founder", "in")],
        "v3 rescoped; 'The founder has chosen the page's structure (relayed'",
    ),
    "20261017T1535--lander-masters--pptmstr-lead--ack.md": (
        "status",
        [("builder", "out")],
        "'has gone to the builder in full'",
    ),
    "20261017T1600--lander-masters--pptmstr-lead--delivery.md": (
        "status",
        [("builder", "in")],
        "'Progress from the builder (run)'; asks if 1572x900 is a problem",
    ),
    "20261017T1420--lander-masters--brand-lead--reply.md": (
        "approval",
        [("founder", "out")],
        "1572x900 fine; 'I'll tell the founder'",
    ),
    "20261017T1640--lander-masters--pptmstr-lead--delivery.md": (
        "answer",
        [("builder", "in")],
        "v3 delivered; 'The builder ran all of it'; asks mask option",
    ),
    "20261017T1438--lander-masters--brand-lead--reply.md": (
        "approval",
        [("founder", "out")],
        "picks (b); worktree copy 'the founder's to word'",
    ),
    "20261017T1503--lander-mobile--brand-lead--request.md": (
        "request",
        [("founder", "in")],
        "v4; 'The founder asked for pre-cut mobile crops (relayed'",
    ),
    "20261017T1700--lander-mobile--pptmstr-lead--ack.md": (
        "status",
        [("builder", "out")],
        "'a builder is on it'",
    ),
    "20261017T1530b--lander-mobile--pptmstr-lead--delivery.md": (
        "answer",
        [("builder", "in")],
        "v4 delivered; 'The builder ran all of it'; corrects its own name stamps",
    ),
    "20261017T1537--lander-mobile--brand-lead--reply.md": (
        "approval",
        [],
        "serve the master; H3 rect accepted",
    ),
    "20261017T1537--lander-copy--brand-lead--delivery.md": (
        "status",
        [("founder", "in")],
        "See line decided; 'The founder answered \"3.\" (relayed'",
    ),
    "20261017T1545--lander-copy--pptmstr-lead--reply.md": (
        "other",
        [("investigator", "in")],
        "unrequested caveat; 'read by our investigator'",
    ),
    "20261017T1545--lander-copy--brand-lead--delivery.md": (
        "status",
        [("founder", "in")],
        "tagline decided (relayed, founder's words)",
    ),
    "20261017T1554--lander-assets--brand-lead--delivery.md": (
        "status",
        [("founder", "in")],
        "founder authorised the copy (relayed)",
    ),
}

KEYWORDS = ("founder", "operator", "builder", "investigator", "relayed")


@dataclass
class Message:
    path: str
    name: str
    side: str
    mtime: float
    protocol: bool
    stamp: dt.datetime | None
    kind: str
    re: str
    needs_answer: bool
    keyword_hits: dict[str, int] = field(default_factory=dict)

    @property
    def when(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.mtime)


def parse_header(text: str) -> tuple[str, bool]:
    re_field, needs = "", False
    for line in text.splitlines()[:6]:
        if line.startswith("re:"):
            re_field = line[3:].strip()
        elif line.startswith("needs-answer:"):
            needs = line.split(":", 1)[1].strip().lower() == "yes"
    return re_field, needs


def load(folder: str, side: str) -> tuple[list[Message], list[tuple[str, float]]]:
    messages, others = [], []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        mtime = os.stat(path).st_mtime
        m = PROTOCOL_NAME.match(name)
        if m and m.group("sender") == side:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            re_field, needs = parse_header(text)
            low = text.lower()
            messages.append(
                Message(
                    path,
                    name,
                    side,
                    mtime,
                    True,
                    dt.datetime.strptime(m.group("stamp"), "%Y%m%dT%H%M"),
                    m.group("kind"),
                    re_field,
                    needs,
                    {k: low.count(k) for k in KEYWORDS},
                )
            )
        elif PRE_PROTOCOL_NAME.match(name):
            with open(path, encoding="utf-8") as fh:
                low = fh.read().lower()
            messages.append(
                Message(
                    path,
                    name,
                    side,
                    mtime,
                    False,
                    None,
                    "handoff",
                    "",
                    False,
                    {k: low.count(k) for k in KEYWORDS},
                )
            )
        else:
            others.append((name, mtime))
    return messages, others


def hm(t: dt.datetime) -> str:
    return t.strftime("%H:%M:%S")


def mins(seconds: float) -> str:
    return f"{seconds / 60:6.2f}"


def nearest_rank(sorted_vals: list[float], p: float) -> float:
    k = max(1, round(p / 100 * len(sorted_vals) + 0.5 - 1e-9))
    return sorted_vals[min(k, len(sorted_vals)) - 1]


def describe(label: str, seconds: list[float]) -> None:
    if not seconds:
        print(f"  {label:<44} n=0")
        return
    s = sorted(seconds)
    print(
        f"  {label:<44} n={len(s):>2}  min {mins(s[0])}  p25 {mins(nearest_rank(s, 25))}"
        f"  median {mins(statistics.median(s))}  p75 {mins(nearest_rank(s, 75))}"
        f"  p90 {mins(nearest_rank(s, 90))}  max {mins(s[-1])}  (minutes)"
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("pptmstr_outbox")
    ap.add_argument("brand_concerns")
    ap.add_argument(
        "--cross-window",
        type=float,
        default=120.0,
        help="seconds within which an unreferenced reply counts as a likely crossing",
    )
    args = ap.parse_args(argv)

    all_msgs: list[Message] = []
    other_files: dict[str, list[tuple[str, float]]] = {}
    for folder, side in zip((args.pptmstr_outbox, args.brand_concerns), SIDE_OF_FOLDER):
        msgs, others = load(folder, side)
        all_msgs += msgs
        other_files[side] = others
    all_msgs.sort(key=lambda m: m.mtime)
    by_name = {m.name: m for m in all_msgs}
    proto = [m for m in all_msgs if m.protocol]

    # 1. Counts --------------------------------------------------------------------
    print("== 1. Messages (ordered by mtime)")
    for side in SIDE_OF_FOLDER:
        p = [m for m in proto if m.side == side]
        pre = [m for m in all_msgs if m.side == side and not m.protocol]
        kinds = {k: sum(1 for m in p if m.kind == k) for k in sorted({m.kind for m in p})}
        na = sum(1 for m in p if m.needs_answer)
        print(
            f"  {side:<13} protocol {len(p):>2}  pre-protocol {len(pre)}  needs-answer:yes {na:>2}  by kind {kinds}"
        )
    print(
        f"  total protocol messages {len(proto)}; first {hm(proto[0].when)}, last {hm(proto[-1].when)}, "
        f"span {mins(proto[-1].mtime - proto[0].mtime)} min"
    )
    for m in all_msgs:
        if not m.protocol:
            print(f"  pre-protocol: {m.name} by {m.side} at {m.when:%Y-%m-%d %H:%M:%S}")

    lo, hi = proto[0].mtime, proto[-1].mtime
    print("  non-message files written into each folder during the run window:")
    for side, others in other_files.items():
        inside = sorted((t, n) for n, t in others if lo <= t <= hi)
        print(f"    {side}'s folder: {len(inside)}")
        for t, n in inside:
            print(f"      {hm(dt.datetime.fromtimestamp(t))}  {n}")

    print("\n== 1a. Name stamp versus mtime (positive = name ahead of the real clock)")
    for side in SIDE_OF_FOLDER:
        vals = [
            (m.stamp - m.when.replace(second=0, microsecond=0)).total_seconds()
            for m in proto
            if m.side == side and m.stamp is not None
        ]
        print(
            f"  {side:<13} min {mins(min(vals))}  median {mins(statistics.median(vals))}  max {mins(max(vals))} min"
        )
    print(
        "  name order versus mtime order: "
        f"{sum(1 for a, b in zip(sorted(proto, key=lambda m: m.name), proto) if a is not b)} "
        f"of {len(proto)} positions differ"
    )

    # Latencies -------------------------------------------------------------------
    print("\n== 1b. Reply latency")
    re_lat: dict[str, list[float]] = {"all": [], "ack": [], "non-ack": []}
    for m in proto:
        target = by_name.get(m.re)
        if target is None or target.side == m.side or not target.protocol:
            continue
        d = m.mtime - target.mtime
        re_lat["all"].append(d)
        re_lat["ack" if m.kind == "ack" else "non-ack"].append(d)
    describe("re: latency, reply mtime - named file mtime", re_lat["all"])
    describe("  of which kind=ack", re_lat["ack"])
    describe("  of which other kinds", re_lat["non-ack"])

    first_reply: list[float] = []
    na_first: list[float] = []
    for m in proto:
        nxt = next((n for n in proto if n.mtime > m.mtime and n.side != m.side), None)
        if nxt is None:
            continue
        first_reply.append(nxt.mtime - m.mtime)
        if m.needs_answer:
            na_first.append(nxt.mtime - m.mtime)
    describe("first other-side write after each message", first_reply)
    describe("  after needs-answer:yes messages", na_first)

    runs: list[list[Message]] = []
    for m in proto:
        if runs and runs[-1][-1].side == m.side:
            runs[-1].append(m)
        else:
            runs.append([m])
    gaps = [b[0].mtime - a[-1].mtime for a, b in zip(runs, runs[1:])]
    describe("turn gap (end of one side's run -> other's)", gaps)
    print(f"  turns (maximal same-side runs): {len(runs)}; run lengths {[len(r) for r in runs]}")

    print("  needs-answer:yes threads (first other-side message whose re: names it):")
    answered: list[float] = []
    for m in proto:
        if not m.needs_answer:
            continue
        ans = next((n for n in proto if n.re == m.name and n.side != m.side), None)
        if ans is None:
            print(f"    {hm(m.when)} {m.name}: NO reply names it")
        else:
            answered.append(ans.mtime - m.mtime)
            print(
                f"    {hm(m.when)} {m.name} -> {hm(ans.when)} {ans.kind:<8} {mins(ans.mtime - m.mtime)} min"
            )
    describe("needs-answer:yes -> first reply naming it", answered)

    # Added by R5 (S1 finding 14): an ack names the message it acknowledges, so the
    # figure above is the first acknowledgement, not the first answer. This is the
    # first other-side reply naming it whose kind is not ack.
    print("  needs-answer:yes threads, first NON-ACK other-side reply naming it (R5):")
    substantive: list[float] = []
    unanswered = 0
    for m in proto:
        if not m.needs_answer:
            continue
        ans = next(
            (n for n in proto if n.re == m.name and n.side != m.side and n.kind != "ack"), None
        )
        if ans is None:
            unanswered += 1
            print(f"    {hm(m.when)} {m.name}: NO non-ack reply names it")
        else:
            substantive.append(ans.mtime - m.mtime)
            print(
                f"    {hm(m.when)} {m.name} -> {hm(ans.when)} {ans.kind:<8} {mins(ans.mtime - m.mtime)} min"
            )
    describe("needs-answer:yes -> first non-ack reply", substantive)
    print(f"  needs-answer:yes with no non-ack reply naming it: {unanswered}")

    # Crossings -------------------------------------------------------------------
    print(
        f"\n== 1c. Crossing candidates: a message whose re: does not name the other side's newest "
        f"message before it (window {args.cross_window:.0f} s flags likely crossings)"
    )
    crossings = 0
    for y in proto:
        x = next((m for m in reversed(proto) if m.mtime < y.mtime and m.side != y.side), None)
        if x is None or y.re == x.name:
            continue
        d = y.mtime - x.mtime
        flag = "LIKELY CROSSING" if d <= args.cross_window else "older thread / new topic"
        crossings += d <= args.cross_window
        print(
            f"  {hm(y.when)} {y.name}\n      newest other-side before it: {hm(x.when)} {x.name} "
            f"({d:5.0f} s earlier)\n      its re: {y.re}  -> {flag}"
        )
    print(f"  likely crossings within window: {crossings}")

    # 2/3. Classification and parties ---------------------------------------------
    print("\n== 2. Classification (set by reading; CLASSIFY table in this script)")
    unclassified = [m.name for m in all_msgs if m.name not in CLASSIFY]
    counts: dict[tuple[str, str], int] = {}
    for m in all_msgs:
        cls = CLASSIFY.get(m.name, ("unclassified", [], ""))[0]
        counts[(m.side, cls)] = counts.get((m.side, cls), 0) + 1
    classes = ["request", "answer", "approval", "status", "other", "unclassified"]
    print(f"  {'side':<13}" + "".join(f"{c:>13}" for c in classes))
    for side in SIDE_OF_FOLDER:
        print(f"  {side:<13}" + "".join(f"{counts.get((side, c), 0):>13}" for c in classes))
    if unclassified:
        print(f"  unclassified files: {unclassified}")

    print("\n== 3. Parties other than the two leads (reading) beside a keyword count (mechanical)")
    involve = {"any": 0}
    for m in all_msgs:
        cls, parties, note = CLASSIFY.get(m.name, ("unclassified", [], ""))
        tag = ", ".join(f"{who}:{way}" for who, way in parties) or "-"
        hits = " ".join(f"{k[:4]}={v}" for k, v in m.keyword_hits.items() if v)
        print(f"  {hm(m.when)} {m.side[:7]:<7} {cls:<8} {tag:<34} [{hits}]  {m.name}")
        if parties:
            involve["any"] += 1
        for who, way in parties:
            involve[f"{who}:{way}"] = involve.get(f"{who}:{way}", 0) + 1
        for who in {who for who, _ in parties}:
            involve[who] = involve.get(who, 0) + 1
        disagree = [
            k
            for k in ("founder", "operator", "builder", "investigator")
            if m.keyword_hits.get(k) and k not in {who for who, _ in parties}
        ]
        missed = [who for who, _ in parties if not m.keyword_hits.get(who)]
        if disagree or missed:
            print(
                f"      keyword/reading disagree: in text but not tagged {disagree}; tagged but not in text {missed}"
            )
    print(f"  messages carrying at least one other party: {involve['any']} of {len(all_msgs)}")
    for k in sorted(k for k in involve if k != "any"):
        print(f"    {k:<16} {involve[k]}")
    scope = [
        m.name
        for m in all_msgs
        if CLASSIFY.get(m.name, ("", [], ""))[0] == "request"
        and ("founder", "in") in CLASSIFY[m.name][1]
    ]
    print(f"  requests whose authority is a relayed founder/operator yes: {len(scope)}")
    for n in scope:
        print(f"    {n}")

    # 4. Per-hop cost ------------------------------------------------------------
    print(
        "\n== 4. What one extra hop would have cost (model: a coordinator lead receives and re-sends,"
    )
    print("   which in this run looks like an ack: read, hand on, write)")
    if re_lat["ack"]:
        a = sorted(re_lat["ack"])
        med, p90 = statistics.median(a), nearest_rank(a, 90)
        n_threads = len(answered)
        print(f"  ack latency median {mins(med)} min, p90 {mins(p90)} min")
        print(
            f"  a request -> answer round trip through one coordinator adds 2 hops: "
            f"median {mins(2 * med)} min, p90 {mins(2 * p90)} min"
        )
        all_med = statistics.median(answered) if answered else 0.0
        print(
            f"  measured needs-answer round trip today (one hop each way): median {mins(all_med)} min"
        )
        print(
            f"  over this run's {n_threads} answered needs-answer threads: "
            f"+{mins(n_threads * 2 * med)} min at the median, +{mins(n_threads * 2 * p90)} min at p90, "
            f"against a {mins(hi - lo)} min run"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
