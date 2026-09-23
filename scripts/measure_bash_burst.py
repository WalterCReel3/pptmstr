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

A simulated widening the tree has since grown is switched off rather than run beside the
real rule, and which ones those are is decided by asking `shellscan.refusal` rather than
by reading a table. Nothing here keeps its own copy of a rule the classifier owns: the
splitter, the `2>/dev/null` pattern and sed's print-range grammar are imported from
`shellscan`, because a copy is always the older of the two and the arm that runs it
measures the copy.

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
  ORIENTING BURST       the same two table shapes over the first N calls of each
                        session, sub-agent transcripts excluded. A higher rate at small
                        N is what "orienting burst" means.
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

A sub-agent gets a transcript of its own, under `<session-id>/subagents/`, so a file is
either a session or one agent's slice of one. Every block counts both kinds, and
ORIENTING BURST is the single exception: first-N is asked of sessions only, because a
sub-agent file has no orienting phase of its own to measure.

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

# SIMULATION, both of them, and both are the classifier's own object rather than a copy
# of it. A private name is imported on purpose: the alternative is a second spelling of
# the same rule, and the arm that ran the second spelling was measuring it -- a local
# `^[0-9]+(,[0-9]+)?p$` refused `sed -n 1,2,3p f` that `_SED_PRINT_RANGE` admits, which
# made the sed arm a narrowing of the tree dressed as a widening of it. If either name
# goes away in `shellscan`, this script must fail at import rather than measure something
# else quietly.
_DISCARD_STDERR = shellscan._DISCARD_STDERR
_PRINT_RANGE = shellscan._SED_PRINT_RANGE

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


def _landed_widenings() -> frozenset[str]:
    """
    Which of the simulated widenings `shellscan` now decides for itself.

    Probed by verdict, not by looking for a row name. `cd` and `sed` are reached by name
    dispatch in `_refuse_segment` and are in no table, and `2>/dev/null` is a strip
    rather than a row, so a `name in _TABLE` test sees none of the three and reports a
    counterfactual that has already landed.

    A landed widening is switched off in `admitted` rather than simulated on top of the
    real rule. Simulating it measures this script's model of the rule against the tree's
    implementation of it, and `sed` is the case that shows why: the model was the
    narrower of the two, so arms E and F admitted 300-odd fewer commands than their own
    subsets.

    ``segments`` is absent because it is on the other axis: withholding it is a
    counterfactual narrowing that stays meaningful precisely because the tree has it.
    """
    probes = {
        "grep": "grep x f",
        "sed_range": "sed -n 1,80p f",
        "cd": "cd /tmp",
        "discard_stderr": "ls 2>/dev/null",
    }
    return frozenset(name for name, probe in probes.items() if shellscan.refusal(probe) is None)


LANDED = _landed_widenings()


@dataclass(frozen=True, slots=True)
class Transcript:
    """
    One transcript file, reduced to the two orderings this script asks questions about.

    ``sidechain`` is what keeps a sub-agent's file out of the first-N table, and it is
    read off the path because the path is what decides whether a file is a session: the
    harness writes an agent's transcript to `<session-id>/subagents/`. ``flagged`` is the
    same question answered by the records' own `isSidechain`, kept so the two can be
    compared -- the first-N figure rests on the two agreeing, and a run that found them
    disagreeing would be measuring a corpus laid out differently than this assumes.
    """

    path: Path
    commands: tuple[str, ...]
    tools: tuple[str, ...]
    sidechain: bool
    flagged: bool


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
        flagged = False
        for line in text.splitlines():
            if '"tool_use"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("isSidechain"):
                flagged = True
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
            transcripts.append(
                Transcript(
                    path,
                    tuple(commands),
                    tuple(tools),
                    sidechain="subagents" in path.parts,
                    flagged=flagged,
                )
            )

    return transcripts, seen, unreadable


def _injected_rows(widening: Widening) -> dict[str, shellscan._Rule]:
    """
    The rows ``widening`` adds to the real table, minus any the tree already decides.

    A row is withheld once its widening has landed, and for `cd` and `sed` withholding it
    is the only honest option rather than a saving: `_refuse_segment` dispatches on those
    two names before it consults `_TABLE`, so injecting them changes no verdict at all.
    An arm reading "+ cd" against an unchanged count invites the reader to conclude `cd`
    buys nothing, when what it buys is already inside the baseline via `_refuse_cd`.
    """
    rows: dict[str, shellscan._Rule] = {}
    if widening.grep and "grep" not in LANDED:
        rows["grep"] = shellscan._Rule()
        rows["rg"] = shellscan._Rule()
    if widening.cd and "cd" not in LANDED:
        rows["cd"] = shellscan._Rule()
    if widening.sed_range and "sed_range" not in LANDED:
        # Paired with `_is_sed_print_range`: the row decides the flags, the predicate
        # decides the script argument.
        rows["sed"] = shellscan._Rule(allowed=shellscan._SED_ALLOWED_FLAGS)
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

    `shellscan._split_segments` and not `_SEPARATORS.split`. The regex is still there,
    used by the splitter through `.match`, so calling `.split` on it raises nothing and
    silently restores the quote-blind cut: `grep -i "append|add_attr" f.py` comes back as
    two segments, the second of which carries an unbalanced quote.
    """
    return shellscan._split_segments(command)


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
    if not widening.sed_range or "sed_range" in LANDED:
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
    if widening.discard_stderr and "discard_stderr" not in LANDED:
        # A single space and not the empty string, so stripping cannot splice two tokens
        # into one. Applied to the whole command rather than per segment, which is where
        # this differs from `_refuse_segment`: the pattern's right anchor is space-or-end,
        # so `ls 2>/dev/null|head` keeps its redirect here and the arm is a lower bound.
        command = _DISCARD_STDERR.sub(" ", command)
    if not widening.segments and len(split_segments(command)) > 1:
        # COUNTERFACTUAL, not a widening: the table before it learned to split. A
        # separator was a metacharacter then, and the command was refused for it. Asked
        # of the splitter and not of `_SEPARATORS.search`, so that a quoted `|` does not
        # read as a separator the old table never had to face.
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
    print(f"    of those, sessions            {sum(1 for t in with_bash if not t.sidechain)}")
    print(f"    of those, sub-agent slices    {sum(1 for t in with_bash if t.sidechain)}")
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
    already = sorted(LANDED)
    outstanding = sorted({"grep", "sed_range", "cd", "discard_stderr"} - LANDED)
    # Probed rather than read off a constant: the question is whether a sequence is
    # decided segment by segment, and only a verdict answers that.
    splits = shellscan.refusal("pwd && pwd") is None
    print("\n=== SIMULATION STATE ===")
    print(
        f"  segment support in shellscan: {'yes' if splits else 'no'} "
        f"(max {shellscan._MAX_SEGMENTS} segments)"
    )
    print(f"  landed, simulation off:       {', '.join(already) if already else 'none'}")
    print(f"  still counterfactual:         {', '.join(outstanding) if outstanding else 'none'}")
    if already:
        print("  Those widenings measure no delta; the tree decides them itself, and the")
        print("  arms naming them score as their subsets do.")
    if not outstanding:
        print("  Every widening this script can simulate has landed, so B is the widest")
        print("  candidate as well as the tree, and C-F are B under other names.")


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
#
# The fourth field names a widening the row's claim depends on being a counterfactual. A
# row carrying one asserts that two arms DIFFER, so it stops being a claim about the
# simulation the moment the tree grows the thing one of them simulates -- both arms then
# admit, and flipping the expected boolean would keep a row that no longer tests the
# separation it was written for. Those rows are dropped and the drop is printed.
_SELF_CHECK: tuple[tuple[str, str, bool, str | None], ...] = (
    # The counterfactual arm withholds segment support; the tree's arm has it.
    ("pwd && pwd", "A", False, None),
    ("pwd && pwd", "B", True, None),
    # No widening may admit a sequence on the strength of its first segment, and the
    # heads that matter are the widened ones -- a shortcut is only ever written for a
    # row the table does not have, so a probe headed by `pwd` cannot find one.
    ("pwd | rm -rf /tmp/probe", "F", False, None),
    ("cat f | rm -rf /tmp/probe", "F", False, None),
    ("grep x f | rm -rf /tmp/probe", "F", False, None),
    ("grep x f | rm -rf /tmp/probe", "G", False, None),
    ("cd /tmp && rm -rf /tmp/probe", "F", False, None),
    ("sed -n 1,80p f | rm -rf /tmp/probe", "F", False, None),
    # A quoted separator is one command, and the arm that splits must agree with the
    # classifier about that or the two are answering about different pieces.
    ('grep -i "append|add_attr" f.py', "B", True, None),
    ('grep -i "append|add_attr" f.py', "A", True, None),
    # The 2>/dev/null simulation is live, and only in the arms that claim it.
    ("ls 2>/dev/null", "B", False, "discard_stderr"),
    ("ls 2>/dev/null", "C", True, None),
    # The sed row admits the print-range shape and nothing else about sed.
    ("sed -n 1,80p f", "F", True, None),
    ("sed -i s/a/b/ f", "F", False, None),
    ("sed s/a/b/ f", "F", False, None),
)


def report_self_check() -> bool:
    by_key = {w.key: w for w in CANDIDATES}
    failures = []
    dropped = []
    for command, key, expected, counterfactual in _SELF_CHECK:
        if counterfactual is not None and counterfactual in LANDED:
            dropped.append(f"{key} on {command!r}: {counterfactual} has landed")
            continue
        widening = by_key[key]
        with simulated_table(widening):
            actual = admitted(command, widening)
        if actual != expected:
            failures.append(f"{key} on {command!r}: expected {expected}, got {actual}")

    print("\n=== SELF CHECK ===")
    for line in dropped:
        print(f"  dropped, no longer a claim about the simulation -- {line}")
    if not failures:
        print(f"  OK -- {len(_SELF_CHECK) - len(dropped)} fixed verdicts all as claimed.")
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


def _window(verdicts: Sequence[Sequence[bool]], n: int) -> tuple[int, int]:
    """
    Admitted and total over the first ``n`` calls of each transcript, or all when n is 0.
    """
    windows = [v[:n] if n else v for v in verdicts]
    return sum(sum(w) for w in windows), sum(len(w) for w in windows)


def report_orienting(transcripts: Sequence[Transcript]) -> None:
    """
    The admit rate over the first N Bash calls of a session.

    Sub-agent transcripts are excluded here and nowhere else, and it changes the answer
    rather than tidying it. A sub-agent file is not a session: it opens mid-task against
    a brief somebody else wrote, and its opening calls are admitted at roughly twice a
    session's rate, so counting one as a session reads an agent's first reads as though
    an operator had typed them. They are half the corpus's files and over half of every
    first-3 window. Every other block keeps them, because the gate sees those calls too
    and the overall rate is a question about calls rather than about sessions.
    """
    sessions = [t for t in transcripts if t.commands and not t.sidechain]
    agents = [t for t in transcripts if t.commands and t.sidechain]
    print("\n=== ORIENTING BURST ===")
    print(f"  sessions with at least one Bash call:   {len(sessions)}")
    print(f"  sub-agent transcripts excluded:         {len(agents)}")

    disagree = [t for t in transcripts if t.sidechain != t.flagged]
    if disagree:
        print(
            f"  WARNING: {len(disagree)} transcripts whose path and whose own isSidechain"
            " flag disagree.\n  The session/sub-agent split below is not trustworthy."
        )

    with simulated_table(AS_BUILT):
        built = {t.path: [admitted(c, AS_BUILT) for c in t.commands] for t in transcripts}
    with simulated_table(WIDEST):
        widest = {t.path: [admitted(c, WIDEST) for c in t.commands] for t in transcripts}

    print(f"  {'first N':>8}  {'calls':>6}  {'B tree today':>16}  {'F widest':>16}")
    for n in (3, 5, 10, 20, 0):
        a, head = _window([built[t.path] for t in sessions], n)
        f, _ = _window([widest[t.path] for t in sessions], n)
        label = str(n) if n else "all"
        print(
            f"  {label:>8}  {head:6d}  {a:6d} {100.0 * a / max(head, 1):8.1f}%  "
            f"{f:6d} {100.0 * f / max(head, 1):8.1f}%"
        )

    barren = sum(1 for t in sessions if not any(widest[t.path][:10]))
    print(
        f"  sessions where not one of the first 10 Bash calls is admitted, "
        f"under F: {barren}/{len(sessions)}"
    )
    if agents:
        agent_a, agent_head = _window([built[t.path] for t in agents], 3)
        total_a, total_head = _window([built[t.path] for t in agents], 0)
        print(
            f"  the excluded sub-agent transcripts, for comparison: {agent_a}/{agent_head}"
            f" = {100.0 * agent_a / max(agent_head, 1):.1f}% on their own first 3 under B,"
            f"\n  against {100.0 * total_a / max(total_head, 1):.1f}% over all"
            f" {total_head} of their calls."
        )


def report_still_refused(commands: Sequence[str]) -> None:
    with simulated_table(WIDEST):
        refused = [c for c in commands if not admitted(c, WIDEST)]
        head_passes = 0
        for command in refused:
            # No strip before the split: `admitted` runs the whole classifier on the head
            # segment, and `_refuse_segment` does its own `2>/dev/null` strip in there.
            parts = split_segments(command)
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
