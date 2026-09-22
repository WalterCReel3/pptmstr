#!/usr/bin/env python3
"""
How much of a real session's Bash traffic does the read-only table admit, and which
candidate widenings actually move that number?

`Policy.PERMISSIVE` auto-approves a `Bash` call only when `shellscan.refusal` returns
None, so the value of the whole preset is proportional to one number: how often that
happens on commands a session actually issues. This measures it against the live
classifier, then re-measures it under candidate table rows that do not exist yet, so
the table can be shaped by usage as well as by danger.

It also answers the two questions that hang off that number. Whether the admit rate is
higher at the *start* of a session, which is the claim
`planning/2026-08-11-research-phase-auto-approval.md` §3 rests on; and how much Bash a
session does before its first sub-agent spawn, which is what an act-shaped spawn would
have cost a lead.

Settles the usage figures quoted in
`planning/2026-09-17-a-research-policy-reduces-the-orienting-burst.md`.

=== WHAT IT READS ===

Every `~/.claude/projects/**/*.jsonl` transcript on this machine. Each `Bash` tool_use
command goes through `pptmstr.shellscan.refusal` -- the real one, not a copy -- and each
`Task`/`Agent` call is recorded in document order so the spawn arm can ask how much Bash
precedes the first one. Nothing is written anywhere; the operator's own commands appear
in the output only as aggregates and as a handful of 64-character excerpts.

=== HOW THE CANDIDATE WIDENINGS ARE SIMULATED ===

A widening is simulated by *injecting a row into the real table* for the duration of one
pass, not by short-circuiting on `argv[0]`. That matters: the whole verdict stays
`shellscan.refusal`, so the metacharacter scan, the segment split, the tokeniser and the
flag walker are the ones that would actually decide, and a simulated row cannot come out
more permissive than a real one. A candidate that answered for itself would be measuring
its own shortcut -- and a shortcut that skipped the split once scored `grep x | rm -rf /`
as admitted.

Two things cannot be expressed as a row and are modelled beside the table, both marked
SIMULATION where they are written: the `sed` print-range shape, which constrains an
argument rather than a flag, and discarding a trailing `2>/dev/null`.

One arm runs the other way. `segments=False` withholds `shellscan`'s own segment support
to show what that support alone buys; it is a COUNTERFACTUAL NARROWING, not a widening,
and the tree has not looked like it since segment support landed.

Any candidate whose widenings are a strict superset of another's must admit at least as
much. The script checks every such pair and exits non-zero if one is violated: an
impossible result is the only cheap evidence that the simulation has drifted from what
it claims to model, and it is worth more than a number that merely looks plausible.

=== HOW TO READ THE OUTPUT ===

  MOST FREQUENT         what sessions run, per segment. The shopping list for a new row.
  SIMULATION STATE      which simulated rows the tree has already grown. A candidate
                        naming one of those measures no delta.
  CANDIDATES            one row per table shape, counts and percent of the whole corpus.
                        `B` is marked as the tree today; `F` is the widest proposal; `A`
                        and `G`-`J` are the counterfactual no-segment-support arm.
  SELF CHECK            fixed verdicts the simulation must produce.
  MONOTONICITY          both must say OK, and the exit status is non-zero if either does
                        not. A failure means the simulation is wrong and every number
                        above it is suspect.
  ORIENTING BURST       the same two table shapes over the first N calls of each session.
                        A higher rate at small N is what "orienting burst" means.
  STILL REFUSED         what the widest candidate does not admit. Read the second block:
                        a command whose own head segment passes and whose later segments
                        do not is not unlocked by adding another row.
  SPAWN                 how much Bash precedes the first spawn, in sessions that spawn.

=== WHAT THIS CORPUS IS NOT ===

It is every Claude Code session on this machine across every project -- not pptmstr
operator sessions, and not filtered to the orienting phase. Rates here estimate "a
session like the ones this machine has run". The ORIENTING BURST block is the closest
thing to a phase-filtered figure it can offer, and it is a proxy: the first N Bash calls
of a transcript, not the calls made before the model started editing.

One transcript file counts as one session. Sub-agent (sidechain) calls live in their
parent's file and are counted with it, so a team run reads as one long session rather
than several.

The corpus is live, and the session running this probe is being written into it, so two
runs minutes apart disagree slightly. Quote a figure with the corpus size beside it.

Usage:  .venv/bin/python scripts/measure_bash_burst.py
"""

from __future__ import annotations

import collections
import contextlib
import json
import re
import shlex
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pptmstr import shellscan  # noqa: E402

CORPUS = Path.home() / ".claude" / "projects"

SPAWN_TOOLS = frozenset({"Task", "Agent"})

# SIMULATION. Discarding stderr is the one redirection an orienting command routinely
# carries. Stripping it can only admit more: every spelling below contains `>`, which the
# metacharacter scan refuses outright.
_DISCARD_STDERR = re.compile(r"\s*2>\s*/dev/null|\s*2>&1|\s*>\s*/dev/null")

# SIMULATION. `sed -n '1,80p' f` is a pure read, and it is the dominant `sed` form in the
# corpus. The shape is a constraint on the script argument, which `_Rule` cannot express,
# so the row injection is paired with this. `$` as a line address is deliberately absent:
# it is a shell metacharacter, so `sed -n '1,$p' f` never reaches this test.
_PRINT_RANGE = re.compile(r"^[0-9]+(,[0-9]+)?p$")

# A refusal reason quotes the token that caused it, and a segment's reason quotes the
# whole segment, so the raw strings make one bucket per command rather than one per rule
# -- and put the operator's command line in a histogram label. Both are stripped back to
# the shape. The one exception is `shell metacharacter '|'`, where the quoted part is a
# single character from a fixed set and is the whole information in the bucket.
#
# Greedy `.*` in the segment prefix on purpose: a segment can contain `): `, and no
# refusal reason does, so the last one is the delimiter.
_SEGMENT_PREFIX = re.compile(r"^segment (\d+) \(.*\): ", re.DOTALL)
_TOO_MANY_SEGMENTS = re.compile(r"^\d+ segments is more than")
_QUOTED = re.compile(r"'[^']*'")
_REASON_SHAPES = (
    ("shell metacharacter", None),
    ("is not a recognised read-only flag", "<cmd> <flag> is not a recognised read-only flag"),
    ("not parseable as one command", "not parseable as one command: <error>"),
)

_EXCERPT = 64


@dataclass(frozen=True, slots=True)
class Widening:
    """
    One candidate table shape, as the set of things it admits beyond the bare table.

    The fields are the comparison key as well as the configuration: a candidate whose
    true fields are a strict superset of another's must score at least as high, and
    `enabled` is what makes that checkable rather than asserted by hand.

    ``segments`` is the odd one. It is the only field the tree has already grown, so
    setting it False is a counterfactual *narrowing* -- "what the table scored before
    `shellscan` split a command up" -- rather than a widening. It stays on the positive
    axis so the superset relation keeps meaning "admits at least as much".
    """

    key: str
    label: str
    segments: bool = False
    discard_stderr: bool = False
    grep: bool = False
    sed_range: bool = False
    cd: bool = False

    def enabled(self) -> frozenset[str]:
        chosen = {
            "segments": self.segments,
            "discard_stderr": self.discard_stderr,
            "grep": self.grep,
            "sed_range": self.sed_range,
            "cd": self.cd,
        }
        return frozenset(name for name, on in chosen.items() if on)


# Lettered as the four throwaway probes this replaces lettered them, so a figure quoted
# from one of those runs can still be checked row for row. `B` rather than `A` is the
# tree, because segment support landed after the letters were assigned.
AS_BUILT = Widening("B", "segment support only", segments=True)
WIDEST = Widening(
    "F",
    "E + cd",
    segments=True,
    discard_stderr=True,
    grep=True,
    sed_range=True,
    cd=True,
)

CANDIDATES: tuple[Widening, ...] = (
    Widening("A", "no segment support"),
    Widening("G", "A + grep/rg", grep=True),
    Widening("H", "G + sed print-range", grep=True, sed_range=True),
    Widening("I", "H + cd", grep=True, sed_range=True, cd=True),
    Widening("J", "I + 2>/dev/null", grep=True, sed_range=True, cd=True, discard_stderr=True),
    AS_BUILT,
    Widening("C", "B + 2>/dev/null", segments=True, discard_stderr=True),
    Widening("D", "C + grep/rg", segments=True, discard_stderr=True, grep=True),
    Widening(
        "E",
        "D + sed print-range",
        segments=True,
        discard_stderr=True,
        grep=True,
        sed_range=True,
    ),
    WIDEST,
)

# Printed with these breaks so the two arms read as arms rather than as one ranked list.
_SECTION_BEFORE = {
    "A": "-- counterfactual: no segment support, which the tree no longer matches --",
    "B": "-- with segment support, which the tree has --",
}


@dataclass(frozen=True, slots=True)
class Transcript:
    """
    One session file, reduced to the two orderings this script asks questions about.
    """

    path: Path
    commands: tuple[str, ...]
    tools: tuple[str, ...]


def _tool_uses(node: object) -> Iterator[dict[str, Any]]:
    """
    Every tool_use block under ``node``, in document order.

    Order is the point, and it is why this recurses rather than walking an explicit
    stack: the orienting and spawn arms both ask "which came first", and a LIFO walk
    answers a different question that looks the same in aggregate.
    """
    if isinstance(node, dict):
        if node.get("type") == "tool_use":
            yield node
        for value in node.values():
            yield from _tool_uses(value)
    elif isinstance(node, list):
        for item in node:
            yield from _tool_uses(item)


def read_corpus(root: Path) -> tuple[list[Transcript], int, int]:
    """
    Every transcript under ``root``, plus how many files were seen and how many failed.
    """
    transcripts: list[Transcript] = []
    seen = 0
    unreadable = 0

    for path in sorted(root.rglob("*.jsonl")):
        seen += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            unreadable += 1
            continue

        commands: list[str] = []
        tools: list[str] = []
        for line in text.splitlines():
            if '"tool_use"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            for block in _tool_uses(record):
                name = block.get("name")
                if name in SPAWN_TOOLS:
                    tools.append(str(name))
                elif name == "Bash":
                    command = (block.get("input") or {}).get("command")
                    if isinstance(command, str) and command.strip():
                        commands.append(command)
                        tools.append("Bash")

        if commands or tools:
            transcripts.append(Transcript(path, tuple(commands), tuple(tools)))

    return transcripts, seen, unreadable


def _injected_rows(widening: Widening) -> dict[str, shellscan._Rule]:
    rows: dict[str, shellscan._Rule] = {}
    if widening.grep:
        rows["grep"] = shellscan._Rule()
        rows["rg"] = shellscan._Rule()
    if widening.cd:
        rows["cd"] = shellscan._Rule()
    if widening.sed_range:
        # Paired with `_is_sed_print_range`: the row decides the flags, the predicate
        # decides the script argument.
        rows["sed"] = shellscan._Rule(allowed=frozenset({"-n"}))
    return rows


@contextlib.contextmanager
def simulated_table(widening: Widening) -> Iterator[None]:
    """
    Run the body with ``widening``'s rows present in the real table, then put it back.

    Injecting rows is what keeps the raw metacharacter scan, `shlex.split` and the flag
    walker in the path for a candidate command. A candidate that decided on ``argv[0]``
    itself would be measuring its own shortcut rather than the table it proposes.
    """
    rows = _injected_rows(widening)
    saved_table = dict(shellscan._TABLE)
    saved_never = dict(shellscan._NEVER_ALLOWED)
    try:
        shellscan._TABLE.update(rows)
        for name in rows:
            shellscan._NEVER_ALLOWED.pop(name, None)
        yield
    finally:
        shellscan._TABLE.clear()
        shellscan._TABLE.update(saved_table)
        shellscan._NEVER_ALLOWED.clear()
        shellscan._NEVER_ALLOWED.update(saved_never)


def split_segments(command: str) -> list[str]:
    """
    The command cut up the way `shellscan` cuts it, so a per-segment question here and
    the classifier's own verdict cannot be answering about different pieces.
    """
    return shellscan._SEPARATORS.split(command)


def _is_sed_print_range(segment: str) -> bool:
    try:
        argv = shlex.split(segment)
    except ValueError:
        return False
    operands = [token for token in argv[1:] if not token.startswith("-")]
    return bool(operands) and _PRINT_RANGE.match(operands[0]) is not None


def _sed_shape_ok(command: str, widening: Widening) -> bool:
    """
    SIMULATION, second half. The injected `sed` row can only decide flags, so the
    print-range shape is checked here -- per segment, because a pipeline's `sed` is as
    much a `sed` as a bare one.
    """
    if not widening.sed_range:
        return True
    for segment in split_segments(command):
        try:
            argv = shlex.split(segment)
        except ValueError:
            return False
        if argv[:1] == ["sed"] and not _is_sed_print_range(segment):
            return False
    return True


def admitted(command: str, widening: Widening) -> bool:
    """
    Whether ``command`` runs unattended under ``widening``. Call inside `simulated_table`.

    The verdict is `shellscan.refusal` on the whole command. Nothing here re-derives it
    per segment: `shellscan` splits the command itself, and a second splitter deciding
    the same question is how `grep x | rm -rf /` gets scored on its `grep`.
    """
    if widening.discard_stderr:
        command = _DISCARD_STDERR.sub("", command)
    if not widening.segments and shellscan._SEPARATORS.search(command):
        # COUNTERFACTUAL, not a widening: the table before it learned to split. A
        # separator was a metacharacter then, and the command was refused for it.
        return False
    if shellscan.refusal(command) is not None:
        return False
    return _sed_shape_ok(command, widening)


def score(commands: Sequence[str], widening: Widening) -> int:
    with simulated_table(widening):
        return sum(1 for command in commands if admitted(command, widening))


def monotonicity_violations(scores: dict[str, int]) -> list[str]:
    """
    Every pair where a strictly wider candidate admitted strictly less.

    A superset of widenings cannot admit less, so a hit here is a defect in this script
    and not a finding about the table.
    """
    violations = []
    for wide in CANDIDATES:
        for narrow in CANDIDATES:
            if not narrow.enabled() < wide.enabled():
                continue
            if scores[wide.key] < scores[narrow.key]:
                violations.append(
                    f"{wide.key} ({sorted(wide.enabled())}) admitted {scores[wide.key]}, "
                    f"but its subset {narrow.key} ({sorted(narrow.enabled())}) "
                    f"admitted {scores[narrow.key]}"
                )
    return violations


def _argv0(command: str) -> str:
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    return argv[0] if argv else ""


def _excerpt(command: str) -> str:
    flat = command.replace("\n", "\\n").replace("\r", "\\r")
    return flat[:_EXCERPT] + ("…" if len(flat) > _EXCERPT else "")


def _reason_bucket(reason: str) -> str:
    prefix = ""
    match = _SEGMENT_PREFIX.match(reason)
    if match:
        prefix = f"segment {match.group(1)}: "
        reason = reason[match.end() :]
    if _TOO_MANY_SEGMENTS.match(reason):
        return prefix + _TOO_MANY_SEGMENTS.sub("<n> segments is more than", reason)
    for needle, shape in _REASON_SHAPES:
        if needle in reason:
            return prefix + (reason if shape is None else shape)
    return prefix + _QUOTED.sub("<x>", reason)


def report_corpus(transcripts: Sequence[Transcript], seen: int, unreadable: int) -> None:
    with_bash = [t for t in transcripts if t.commands]
    total = sum(len(t.commands) for t in transcripts)
    print("=== CORPUS ===")
    print(f"  transcript files found          {seen}")
    if unreadable:
        print(f"  unreadable, skipped             {unreadable}")
    print(f"  files with a Bash or spawn call {len(transcripts)}")
    print(f"  files with at least one Bash    {len(with_bash)}")
    print(f"  Bash calls                      {total}")


def report_frequency(commands: Sequence[str]) -> None:
    """
    What a session actually runs, which is the list a new table row is shopped from.

    Two columns because a row has to pay in both. `as head` is what the operator typed
    first; `any segment` counts `grep` in `cd x && grep y` as well, and a row is only
    worth adding if the second column says the sequence support to reach it exists.

    The segment column skips commands past `shellscan._MAX_SEGMENTS`, which is where a
    multi-line heredoc lands. Those lines are the body of a script rather than commands,
    and counting them puts Python keywords in a table of shell binaries.
    """
    heads: collections.Counter[str] = collections.Counter()
    parts_seen: collections.Counter[str] = collections.Counter()
    for command in commands:
        head = _argv0(command)
        if head:
            heads[head[:32]] += 1
        parts = split_segments(command)
        if len(parts) > shellscan._MAX_SEGMENTS:
            continue
        for part in parts:
            name = _argv0(part)
            if name:
                parts_seen[name[:32]] += 1

    print("\n=== MOST FREQUENT COMMANDS ===")
    print(f"  {'as head':>8}  {'any segment':>11}   command")
    for name, count in parts_seen.most_common(16):
        print(f"  {heads[name]:8d}  {count:11d}   {name}")


def report_reasons(commands: Sequence[str]) -> None:
    counts: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    for command in commands:
        reason = shellscan.refusal(command)
        if reason is None:
            continue
        bucket = _reason_bucket(reason)
        counts[bucket] += 1
        sample = _excerpt(command)
        if len(examples[bucket]) < 2 and sample not in examples[bucket]:
            examples[bucket].append(sample)

    print("\n=== WHY THE TABLE AS BUILT REFUSES ===")
    for bucket, count in counts.most_common(12):
        print(f"  {count:5d}  {100.0 * count / max(len(commands), 1):5.1f}%  {bucket}")
        for sample in examples[bucket]:
            print(f"           e.g. {sample}")


def report_simulation_state() -> None:
    """
    Which of the simulated rows the tree has already grown.

    The table moves, and a candidate labelled "+ grep" measures nothing once `grep` is a
    real row. Printing the state is cheaper than keeping the labels in sync by hand, and
    it is the line that tells a reader six months on whether the widening arm was still
    a counterfactual when this output was produced.
    """
    already = sorted(
        name
        for name in ("grep", "rg", "cd", "sed")
        if name in shellscan._TABLE and name not in shellscan._NEVER_ALLOWED
    )
    # Probed rather than read off a constant: the question is whether a sequence is
    # decided segment by segment, and only a verdict answers that.
    splits = shellscan.refusal("pwd && pwd") is None
    print("\n=== SIMULATION STATE ===")
    print(
        f"  segment support in shellscan: {'yes' if splits else 'no'} "
        f"(max {shellscan._MAX_SEGMENTS} segments)"
    )
    print(f"  simulated rows already real:  {', '.join(already) if already else 'none'}")
    if already:
        print("  Those candidates measure no delta; the widening they name has landed.")


def report_candidates(commands: Sequence[str]) -> dict[str, int]:
    total = len(commands)
    scores = {w.key: score(commands, w) for w in CANDIDATES}
    print("\n=== CANDIDATES ===")
    for widening in CANDIDATES:
        heading = _SECTION_BEFORE.get(widening.key)
        if heading:
            print(f"  {heading}")
        count = scores[widening.key]
        marker = "  <- the tree today" if widening.key == AS_BUILT.key else ""
        print(
            f"  {widening.key}  {widening.label:<22} "
            f"{count:5d}  {100.0 * count / max(total, 1):5.1f}%{marker}"
        )
    return scores


# Fixed verdicts the simulation must produce, checked before the corpus is believed.
#
# Monotonicity cannot see all of this. Deleting the counterfactual guard makes arm A
# identical to arm B, which is wrong and yet violates no superset relation -- the arm
# just silently stops being a counterfactual. Each row below is a claim about the
# SIMULATION rather than about the table's contents, so landing a new row does not
# make one stale.
_SELF_CHECK: tuple[tuple[str, str, bool], ...] = (
    # The counterfactual arm withholds segment support; the tree's arm has it.
    ("pwd && pwd", "A", False),
    ("pwd && pwd", "B", True),
    # No widening may admit a sequence on the strength of its first segment, and the
    # heads that matter are the widened ones -- a shortcut is only ever written for a
    # row the table does not have, so a probe headed by `pwd` cannot find one.
    ("pwd | rm -rf /tmp/probe", "F", False),
    ("cat f | rm -rf /tmp/probe", "F", False),
    ("grep x f | rm -rf /tmp/probe", "F", False),
    ("grep x f | rm -rf /tmp/probe", "G", False),
    ("cd /tmp && rm -rf /tmp/probe", "F", False),
    ("sed -n 1,80p f | rm -rf /tmp/probe", "F", False),
    # The 2>/dev/null simulation is live, and only in the arms that claim it.
    ("ls 2>/dev/null", "B", False),
    ("ls 2>/dev/null", "C", True),
    # The sed row admits the print-range shape and nothing else about sed.
    ("sed -n 1,80p f", "F", True),
    ("sed -i s/a/b/ f", "F", False),
    ("sed s/a/b/ f", "F", False),
)


def report_self_check() -> bool:
    by_key = {w.key: w for w in CANDIDATES}
    failures = []
    for command, key, expected in _SELF_CHECK:
        widening = by_key[key]
        with simulated_table(widening):
            actual = admitted(command, widening)
        if actual != expected:
            failures.append(f"{key} on {command!r}: expected {expected}, got {actual}")

    print("\n=== SELF CHECK ===")
    if not failures:
        print(f"  OK -- {len(_SELF_CHECK)} fixed verdicts all as claimed.")
        return True
    print("  FAILED. The simulation no longer does what it says; the numbers are not")
    print("  measuring the candidates they are labelled with.")
    for line in failures:
        print(f"    {line}")
    return False


def report_monotonicity(scores: dict[str, int]) -> bool:
    violations = monotonicity_violations(scores)
    print("\n=== MONOTONICITY ===")
    if not violations:
        print("  OK -- every candidate scored at least as high as each of its subsets.")
        return True
    print("  VIOLATED. A wider candidate admitted less than a narrower one, which is")
    print("  impossible. This script is wrong; do not use the numbers above.")
    for line in violations:
        print(f"    {line}")
    return False


def report_orienting(transcripts: Sequence[Transcript]) -> None:
    with_bash = [t for t in transcripts if t.commands]
    print("\n=== ORIENTING BURST ===")
    print(f"  sessions with at least one Bash call: {len(with_bash)}")
    print(f"  {'first N':>8}  {'calls':>6}  {'B tree today':>16}  {'F widest':>16}")

    with simulated_table(AS_BUILT):
        built = {t.path: [admitted(c, AS_BUILT) for c in t.commands] for t in with_bash}
    with simulated_table(WIDEST):
        widest = {t.path: [admitted(c, WIDEST) for c in t.commands] for t in with_bash}

    for n in (3, 5, 10, 20, 0):
        head = sum(len(built[t.path][:n] if n else built[t.path]) for t in with_bash)
        a = sum(sum(built[t.path][:n] if n else built[t.path]) for t in with_bash)
        f = sum(sum(widest[t.path][:n] if n else widest[t.path]) for t in with_bash)
        label = str(n) if n else "all"
        print(
            f"  {label:>8}  {head:6d}  {a:6d} {100.0 * a / max(head, 1):8.1f}%  "
            f"{f:6d} {100.0 * f / max(head, 1):8.1f}%"
        )

    barren = sum(1 for t in with_bash if not any(widest[t.path][:10]))
    print(
        f"  sessions where not one of the first 10 Bash calls is admitted, "
        f"under F: {barren}/{len(with_bash)}"
    )


def report_still_refused(commands: Sequence[str]) -> None:
    with simulated_table(WIDEST):
        refused = [c for c in commands if not admitted(c, WIDEST)]
        head_passes = 0
        for command in refused:
            parts = split_segments(_DISCARD_STDERR.sub("", command))
            if len(parts) > 1 and admitted(parts[0].strip(), WIDEST):
                head_passes += 1

    by_name: collections.Counter[str] = collections.Counter()
    for command in refused:
        by_name[_argv0(command)[:32]] += 1

    print("\n=== STILL REFUSED UNDER F, by argv[0] ===")
    for name, count in by_name.most_common(12):
        print(f"  {count:5d}  {name}")
    print(
        f"\n  of {len(refused)} still-refused calls, {head_passes} have a head segment F"
        f" admits\n  and fail on a later segment. Those are not unlocked by another row."
    )


def report_spawn(transcripts: Sequence[Transcript]) -> None:
    never = 0
    before: list[int] = []
    for transcript in transcripts:
        if not any(name in SPAWN_TOOLS for name in transcript.tools):
            never += 1
            continue
        count = 0
        for name in transcript.tools:
            if name in SPAWN_TOOLS:
                break
            count += 1
        before.append(count)

    print("\n=== SPAWN ===")
    print(f"  sessions with tool calls:  {len(transcripts)}")
    print(f"  never spawn:               {never}")
    print(f"  spawn at least once:       {len(before)}")
    if not before:
        return
    before.sort()
    zero = sum(1 for n in before if n == 0)
    print("  Bash calls before the first spawn, in sessions that spawn:")
    print(
        f"    median {before[len(before) // 2]}   "
        f"mean {sum(before) / len(before):.1f}   max {max(before)}"
    )
    print(f"    spawned first, zero Bash before it: {zero}/{len(before)}")
    buckets: collections.Counter[str] = collections.Counter()
    for n in before:
        buckets["0" if n == 0 else "1-2" if n <= 2 else "3-9" if n <= 9 else "10+"] += 1
    for key in ("0", "1-2", "3-9", "10+"):
        print(f"    {key:>4}: {buckets[key]}")


def main() -> int:
    if not CORPUS.is_dir():
        print(f"No transcript store at {CORPUS} -- nothing to measure.")
        print("This probe reads Claude Code session history, so it only has a corpus on")
        print("a machine that has run some.")
        return 0

    transcripts, seen, unreadable = read_corpus(CORPUS)
    commands = [c for t in transcripts for c in t.commands]
    report_corpus(transcripts, seen, unreadable)
    if not commands:
        print("\nNo Bash calls in the corpus -- nothing to measure.")
        return 0

    report_frequency(commands)
    report_reasons(commands)
    report_simulation_state()
    scores = report_candidates(commands)
    sound = report_self_check()
    sound = report_monotonicity(scores) and sound
    report_orienting(transcripts)
    report_still_refused(commands)
    report_spawn(transcripts)
    return 0 if sound else 1


if __name__ == "__main__":
    raise SystemExit(main())
