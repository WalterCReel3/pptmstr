"""
A fake agent driver, for building the UI without the SDK.

Emits intents on a timer so every state the tree pane can render is reachable
without spawning a subprocess or spending a token. It is not a simulator and should
not grow into one: its job is to make states reachable, and it gets deleted when
step 3 lands rather than maintained alongside the real driver.

Runs on the Bridge's asyncio thread and talks to the UI exactly the way the real
driver will -- ``bridge.emit(...)`` and nothing else. That is the point of building
against it: if the pane works here, the only thing step 3 changes is where the
intents come from.

**The text is the fixture, as much as the states are.** Every pane that sets prose
is judged from a screenshot of this fleet, so what the agents say, park and file
here is shaped like what a model writes: markdown with every block kind, hard
wrapped, with identifiers and shell lines in it; diffs long enough to scroll;
specifications and concerns that run to paragraphs. ``FakeDriver.cast`` names the
node each of those lives on.
"""

from __future__ import annotations

import asyncio
import itertools
import pathlib
import random
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import brief
from .approval import diff_texts, render_texts, summarize
from .bridge import Bridge
from .intents import (
    AgentFinished,
    AgentSpawned,
    ApprovalRequested,
    CompactionObserved,
    ConcernPosted,
    ConcernWithdrawn,
    ContextPolled,
    DecisionAnswered,
    DecisionRaised,
    InboxRead,
    StateChanged,
    SubagentDelivered,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
    TopicChanged,
)
from .model import (
    AWAITING_TOPIC,
    AgentState,
    Concern,
    ContextSnapshot,
    Decision,
    NodeId,
    PendingApproval,
    Standing,
    Task,
)
from .transcript import SegmentKind, Transcript, tool_call_line, tool_result_line

_MODELS = ("claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "claude-haiku-5-5")
_TOPICS = (
    "reading pptmstr/store.py",
    "running pytest",
    "grepping for call sites",
    "writing tests/test_bridge.py",
    "waiting on approval",
    "summarising findings",
)
_TASKS = (
    "wire the approval gate",
    "audit the threading boundary",
    "port the tree pane",
    "chase a flaky test",
)
# Distinct directories so --fake exercises the project axis. A fake fleet that all
# shares one cwd renders as a single lane, which is the one arrangement the rail's
# grouping cannot be judged from.
_CWDS = (
    "~/Source/pptmstr",
    "~/Source/orbital",
    "~/scratch",
)

# The bus tools as the gate sees them, spelled here rather than imported from
# pptmstr.bus for the reason approval.py spells them: bus.py pulls in the SDK, and
# this module's premise is that it runs without one. Pinned to ``bus.qualified`` by
# tests/test_fake_fixtures.py.
POST_CONCERN = "mcp__pptmstr__post_concern"
DECLARE_TASK = "mcp__pptmstr__declare_task"

# One source line of more than 200 columns, as a pasted data literal is. A diff view
# does not wrap, so this is the line that says what it does instead.
_REFERENCE_PAIR = (
    'REFERENCE = ("1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927", '
    '"2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537")'
    "  # noqa: E501 -- the pair from the format description, kept on one line so it"
    " can be compared by eye against the page it was copied from\n"
)

# A whole new file, long enough that its diff scrolls in every pane that shows one.
# The path is relative and names nothing in this repository, so ``render_diff``
# finds no current file and the diff is all additions against /dev/null.
_CHECKSUM_MODULE = (
    '''"""
The modulo-10 checksum of a two-line element set.

Kept beside the parser rather than inside it: a parser that validates cannot be
reused on a record that has not been checksummed yet.
"""

from __future__ import annotations

from dataclasses import dataclass

# Column 69 holds the checksum; the 68 before it are what it is computed over.
CHECKSUM_COLUMN = 68
LINE_LENGTH = 69

'''
    + _REFERENCE_PAIR
    + '''


class ChecksumError(ValueError):
    """A line whose last digit disagrees with the rest of it."""


@dataclass(frozen=True, slots=True)
class Verdict:
    line_number: int
    expected: int
    actual: int

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


def checksum(payload: str) -> int:
    """
    Sum of the digits, each minus sign counting as one, modulo ten.

    Letters, blanks, periods and plus signs count as zero, which is why this
    is not ``sum(map(int, ...))`` over a filtered string.
    """
    total = 0
    for ch in payload:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10


def inspect(line: str) -> Verdict:
    if len(line) != LINE_LENGTH:
        raise ChecksumError(f"expected {LINE_LENGTH} columns, got {len(line)}")
    if not line[CHECKSUM_COLUMN].isdigit():
        raise ChecksumError(f"column {LINE_LENGTH} is not a digit: {line[-1]!r}")
    return Verdict(
        line_number=int(line[0]),
        expected=int(line[CHECKSUM_COLUMN]),
        actual=checksum(line[:CHECKSUM_COLUMN]),
    )


def verify(line: str) -> None:
    verdict = inspect(line)
    if not verdict.ok:
        raise ChecksumError(
            f"line {verdict.line_number}: computed {verdict.actual}, "
            f"line says {verdict.expected}"
        )
'''
)

_WRITE = ("Write", {"file_path": "tle/checksum.py", "content": _CHECKSUM_MODULE})

# Neither string ends in a newline, which is how a model writes an anchor, and the
# file does not exist here -- so ``render_diff`` falls back to diffing the two
# strings themselves, and difflib, given a last line with no terminator, emits the
# removal and the addition after it on one physical line. This is the fixture for
# that row, and for "this edit will not apply".
_EDIT_ANCHOR_ABSENT = (
    "Edit",
    {
        "file_path": "tle/parse.py",
        "old_string": (
            "def checksum(line: str) -> int:\n"
            "    digits = [c for c in line[:68] if c.isdigit()]\n"
            "    return sum(int(c) for c in digits) % 10"
        ),
        "new_string": (
            "def checksum(line: str) -> int:\n"
            "    digits = [c for c in line[:68] if c.isdigit()]\n"
            '    minus = line[:68].count("-")\n'
            "    return (sum(int(c) for c in digits) + minus) % 10"
        ),
    },
)

# The ordinary path: a file that exists, with the anchor in it, so ``render_diff``
# diffs the whole file against its replacement and the hunk has real context and
# real line numbers. The anchor is unterminated here too, and the diff is clean
# all the same -- which is the contrast with the one above. Located from this
# module rather than the working directory, so it resolves wherever the app is
# launched from. Parked only: nothing here applies an edit.
_EDIT_IN_PLACE = (
    "Edit",
    {
        "file_path": str(pathlib.Path(__file__).with_name("transcript.py")),
        "old_string": '    REASONING = "reasoning"\n    OUTPUT = "output"',
        "new_string": (
            '    REASONING = "reasoning"\n'
            "    # What the agent said, as opposed to what it did.\n"
            '    OUTPUT = "output"\n'
            '    NARRATION = "narration"'
        ),
    },
)

# Three edits to one file. The first two are whole lines; the third is a
# mid-line anchor with no terminator, which is the commoner shape.
_MULTI_EDIT = (
    "MultiEdit",
    {
        "file_path": "tle/parse.py",
        "edits": [
            {
                "old_string": (
                    "def parse_line_1(line: str) -> Line1:\n"
                    "    fields = line.split()\n"
                    "    return Line1(catalog=int(fields[1][:5]), epoch=_epoch(fields[3]))\n"
                ),
                "new_string": (
                    "def parse_line_1(line: str) -> Line1:\n"
                    "    verify(line)\n"
                    "    return Line1(\n"
                    '        catalog=int(_field(line, "catalog")),\n'
                    '        epoch=_epoch(_field(line, "epoch")),\n'
                    "    )\n"
                ),
            },
            {
                "old_string": (
                    "def parse_line_2(line: str) -> Line2:\n"
                    "    fields = line.split()\n"
                    "    return Line2(inclination=float(fields[2]), raan=float(fields[3]))\n"
                ),
                "new_string": (
                    "def parse_line_2(line: str) -> Line2:\n"
                    "    verify(line)\n"
                    "    return Line2(\n"
                    '        inclination=float(_field(line, "inclination")),\n'
                    '        raan=float(_field(line, "raan")),\n'
                    "    )\n"
                ),
            },
            {
                "old_string": "from .offsets import _FIELD_OFFSETS",
                "new_string": "from .checksum import verify\nfrom .offsets import _FIELD_OFFSETS",
            },
        ],
    },
)

# Long on purpose. Every other command here fits a column, which is why the queue
# looked correct for as long as it did: a real Bash call runs off the right edge of
# the row *and* past summarize's 90-character clip, so the row is a lossy rendering
# of a command that could delete something. DETAIL is the pane that has to hold
# this one.
_BASH = (
    "Bash",
    {
        "command": (
            "for f in $(git ls-files '*.py'); do "
            'python -m compileall -q "$f" || echo "FAILED $f" >> /tmp/compile.log; '
            "done && rg -n 'TODO|FIXME' pptmstr tests "
            "| awk -F: '{print $1}' | sort | uniq -c | sort -rn | head -20"
        )
    },
)

# A parked concern. The body is what the operator is being asked to read, and to
# rewrite if it is wrong, before the recipient sees it.
_POST_CONCERN = (
    POST_CONCERN,
    {
        "to": "lead",
        "subject": "t2 as specified validates inside the parser, and the brief says not to",
        "task_id": "t2",
        "body": (
            "I am holding `t2` rather than building it as written, and this is why.\n"
            "\n"
            "The task detail says to call the checksum from `parse_line_1` and\n"
            "`parse_line_2`. Brief entry 002 says the opposite: the check belongs\n"
            "*beside* the parser, because a parser that validates cannot be reused on a\n"
            "record that has not been checksummed yet. The detail was written before\n"
            "that entry and was not amended.\n"
            "\n"
            "What I found in the tree:\n"
            "\n"
            "- `tle/ingest.py` calls `parse_line_1` on lines it has **already**\n"
            "  verified, so validating inside would do the work twice;\n"
            "- `tools/repair.py` calls it on lines it knows are corrupt, on purpose,\n"
            "  to recover the catalog number -- that caller would start raising;\n"
            "- nothing else imports the parser.\n"
            "\n"
            "Proposed instead: `tle/checksum.py` exposes `verify(line)` and the two\n"
            "callers that want validation call it first. That is a smaller diff and it\n"
            "keeps `tools/repair.py` working.\n"
            "\n"
            "Two smaller things from the same read: `_field` returns Optional<int> for\n"
            "a blank column, and the board clipped the detail, so I had to ask <lead>\n"
            "for the rest. The fixture file starts <!-- generated by tools/fetch.py and\n"
            "the comment is never closed.\n"
            "\n"
            "<details>\n"
            "\n"
            "is the whole of line 3 of `tests/data/catalog.html`, in case it matters.\n"
            "\n"
            "If you agree, amend `t2` and I will carry on. If the detail is right and\n"
            "the brief is stale, say so and I will build it as written.\n"
        ),
    },
)

# A parked declaration: the missing ``t9`` that ``t4`` on the seeded board is
# waiting for, at the moment the lead asks for it to exist.
_DECLARE_TASK = (
    DECLARE_TASK,
    {
        "task_id": "t9",
        "title": "cut the 0.4.0 release notes",
        "depends_on": ["t3"],
        "touches": ["CHANGELOG.md", "docs/release-0.4.0.md"],
        "detail": (
            "Builder task. `CHANGELOG.md` and a new `docs/release-0.4.0.md` only.\n"
            "\n"
            "**Why:** `t4` has been waiting on this id since the board was declared and\n"
            "nothing ever put it there, so the changelog row can never be claimed. The\n"
            "notes are also the one place the checksum change is explained to someone\n"
            "who was not in this session.\n"
            "\n"
            "Write, in this order:\n"
            "\n"
            "1. What changed for a caller: `parse_line_1` and `parse_line_2` no longer\n"
            "   split on whitespace, and `verify(line)` is a separate call.\n"
            "2. What did **not** change: the propagator, and the public record types.\n"
            "3. The one behaviour that can break someone: a line with a wrong\n"
            "   checksum used to parse and now raises `ChecksumError` *if* the caller\n"
            "   verifies.\n"
            "\n"
            "Do not describe the fixed-width table; it is internal. Do not claim the\n"
            "suite covers Alpha-5 catalog numbers -- it does not, and `t3` says so.\n"
            "\n"
            "Verify: `make lint test`; state which of the three points you could not\n"
            "confirm from the tree.\n"
        ),
    },
)

_FRAGMENTS = (
    "checking the call sites ",
    "that looks right.\n",
    "hmm, the offsets do not line up ",
    "-> 3 matches\n",
    "re-reading the store to be sure ",
    "done.\n",
)

# -- the report ---------------------------------------------------------------------
#
# One turn of a session, as the ordered segments the real driver would have
# appended. The two OUTPUT segments together are a document with every block kind a
# model produces, and with the inline cases a renderer has to decide about: dunder
# and snake_case identifiers outside code spans, which are not emphasis, and a
# shell line full of ``$``.
#
# The same script is written whole onto one node and streamed onto another, so the
# settled and the in-flight rendering of identical text can be put side by side.

_REPORT_OPENING = """\
# Parser audit: the checksum is computed and never compared

The fixed-width reader in `tle/parse.py` is **correct on every field it reads**
and wrong about the one it does not: the modulo-10 checksum in column 69 is
computed by `checksum()` and then *dropped*. Nothing compares it to the digit on
the line, so a corrupted element set parses cleanly and is handed on.

Three things are worth knowing before the fix:

- The offsets live in one table, `_FIELD_OFFSETS`, and every reader goes through
  it.
  - `Field.__init__` validates the span, so a bad offset fails at import.
    - The exception is foo_bar_baz, which the test helper builds by hand.
  - Nothing splits on whitespace any more.
- A minus sign counts as **1**; letters, blanks, periods and plus signs count
  as 0.
- The dunder names (__init__, __post_init__) and the snake_case ones
  (foo_bar_baz, parse_line_1) in this report are identifiers, not emphasis.

What I did, in order:

1. Read `tle/parse.py` and the sixty call sites of `parse_line_1`.
2. Ran the suite. One test fails, and it is the *right* one to fail.
3. Wrote the comparison beside the parser rather than inside it.
4. Left the propagator alone, which is out of scope for this session.

> A parser that validates cannot be reused on a record that has not been
> checksummed yet. The check belongs beside the parser, not inside it.
>
> *-- the brief, entry 002*

"""

_REPORT_BODY = (
    """\
## What changes

### The comparison

The new function takes the line and not the parsed record, so it can run before
`parse_line_1` or after it, and `tools/repair.py` can skip it altogether:

```python
def verify(line: str) -> None:
    # Column 69 is the checksum; the 68 before it are what it covers.
    expected = int(line[68])
    actual = checksum(line[:68])
    if actual != expected:
        raise ChecksumError(f"line {line[0]}: {actual} != {expected}")
```

### The one-line defect

```diff
--- a/tle/checksum.py
+++ b/tle/checksum.py
@@ -12,2 +12,3 @@ def checksum(line: str) -> int:
     digits = [c for c in line if c.isdigit()]
-    return sum(int(c) for c in digits) % 10
+    minus = line.count("-")
+    return (sum(int(c) for c in digits) + minus) % 10
```

## Where it was checked

| Input | Lines | Result | Notes |
|---|---:|---|---|
| ISS (ZARYA) | 2 | pass | the reference pair from the format description |
| NOAA 19 | 2 | pass | negative drag term, so the minus rule is exercised |
"""
    "| Alpha-5 catalog | 2 | **not run** | the catalog number carries a letter and "
    '`_field(line, "catalog")` still calls `int()` on it, so this row fails before '
    "the checksum is reached; that is a separate defect and `t3` records it |\n"
    """\
| truncated line | 1 | raises | `ChecksumError`, as intended |

The format is described in the
[CelesTrak TLE documentation](https://celestrak.org/NORAD/documentation/tle-fmt.php),
which is also where the reference pair comes from.

![alt text](http://example.invalid/x.png)

### Text that looks like markup and is not

Ask <lead> for the rest of the task detail, because the board clips it. `_field`
returns Optional<int> for a blank column, not 0. The fixture header began <!--
generated and was never closed, so everything after it read as a comment.

<details>

That tag is the whole of line 3 of the catalog page. Pipeline as it stands:
parse \u2192 verify \u2192 ingest \u2713 \U0001f680, checked against the \u885b\u661f catalog.

The hook that runs the suite sees $PATH=$HOME/bin and nothing else, so the
interpreter is named in full:

```sh
$ env -i PATH=$HOME/bin $HOME/bin/python -m pytest -q tests/test_parse.py
```

---

*Not verified:* the Alpha-5 row above, and anything about the propagator. **One
question** before I go further: should `tle/ingest.py` call `verify` itself, or
should that stay with its caller?
"""
)


@dataclass(frozen=True)
class _Call:
    """A tool call in a fixture script, written as an entry the way the driver does."""

    tool_use_id: str
    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True)
class _Result:
    """A tool result in a fixture script."""

    tool_use_id: str
    name: str
    output: str
    is_error: bool = False


_ReportStep = tuple[SegmentKind, str] | _Call | _Result

# The report as the stream delivers it. Tool calls and results are entries, so a
# probe or screenshot has a structured call to draw from; their lines are derived
# from them by the same functions the driver uses.
_REPORT_STEPS: tuple[_ReportStep, ...] = (
    (SegmentKind.REASONING, "The offsets are fine. The checksum is where this goes wrong.\n"),
    _Call("report-read", "Read", {"file_path": "tle/parse.py"}),
    _Result(
        "report-read",
        "Read",
        '{"lines": 212, "symbols": ["__init__", "parse_line_1", "parse_line_2", '
        '"_field"], "globs": ["tle/*_offsets.py", "tests/**/test_*.py"], "imports": '
        '["dataclasses", "datetime", "tle.offsets._FIELD_OFFSETS"], "first_line": '
        '"1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927"}',
    ),
    _Call("report-tree", "Bash", {"command": "tree tle tests"}),
    _Result(
        "report-tree",
        "Bash",
        "tle\n"
        "\u251c\u2500\u2500 __init__.py\n"
        "\u251c\u2500\u2500 offsets.py\n"
        "\u2514\u2500\u2500 parse.py\n"
        "tests\n"
        "\u251c\u2500\u2500 data\n"
        "\u2502   \u2514\u2500\u2500 catalog.html\n"
        "\u251c\u2500\u2500 helpers.py\n"
        "\u2514\u2500\u2500 test_parse.py",
    ),
    _Call("report-pytest", "Bash", {"command": "pytest -q tests/test_parse.py"}),
    _Result(
        "report-pytest",
        "Bash",
        "exit 1\n"
        "FAILED tests/test_parse.py::test_checksum_counts_minus - AssertionError: 4 != 5\n"
        "1 failed, 37 passed in 0.41s",
        is_error=True,
    ),
    (SegmentKind.OUTPUT, _REPORT_OPENING),
    _Call("report-grep", "Grep", {"pattern": "foo_bar_baz", "path": "tle"}),
    _Result(
        "report-grep",
        "Grep",
        "tests/helpers.py:41: foo_bar_baz = Field.__new__(Field)  # *not* validated",
    ),
    (SegmentKind.OUTPUT, _REPORT_BODY),
)


def _step_text(step: _ReportStep) -> tuple[SegmentKind, str]:
    """The segment a step appends, as ``Transcript`` writes it."""
    match step:
        case _Call(name=name, args=args):
            return SegmentKind.TOOL_CALL, tool_call_line(name, args)
        case _Result(name=name, output=output, is_error=is_error):
            kind = SegmentKind.ERROR if is_error else SegmentKind.TOOL_RESULT
            return kind, tool_result_line(name, output)
        case (kind, text):
            return kind, text
    raise AssertionError(step)


def _write_step(transcript: Transcript, step: _ReportStep) -> None:
    """Append a step whole: a tool step as an entry, anything else as text."""
    match step:
        case _Call(tool_use_id=tool_use_id, name=name, args=args):
            transcript.append_tool_call(tool_use_id, name, args)
        case _Result(tool_use_id=tool_use_id, name=name, output=output, is_error=is_error):
            transcript.append_tool_result(tool_use_id, name, output, is_error=is_error)
        case (kind, text):
            transcript.append(kind, text)


# The same report as (kind, text) pairs, for the panes' tests and benchmarks that
# read it as text.
_REPORT_SCRIPT: tuple[tuple[SegmentKind, str], ...] = tuple(_step_text(s) for s in _REPORT_STEPS)

# The kinds that arrive a few characters at a time. Tool calls, results and errors
# reach the real transcript whole, from one message block each.
_STREAMED_KINDS = frozenset({SegmentKind.REASONING, SegmentKind.OUTPUT})

# Uneven and coprime-ish with the line length, so chunk boundaries land inside
# ``**`` pairs, fence markers and table rows rather than between them -- the
# half-written states are what a streaming renderer is judged on.
_STREAM_CHUNKS = (3, 11, 7, 23, 5, 17, 2, 13)
_STREAM_INTERVAL = 0.05
_TICK_EVERY = 12
# Steps to hold the finished report before streaming it again.
_STREAM_REST = 120

# What a sub-agent hands back on SubagentStop: the whole answer, as markdown.
_DELIVERABLE = """\
## t1: the element set parses from fixed columns

`tle/parse.py` now reads every field through `_FIELD_OFFSETS`. Nothing splits on
whitespace.

**Changed**

- `parse_line_1`, `parse_line_2`: positional reads via `_field(line, name)`.
- `tle/offsets.py`: the table, one row per field, 1-based columns as the format
  description gives them.

**Not changed, on purpose**

- The checksum. It is computed and not compared; that is `t2`.
- Alpha-5 catalog numbers, which still fail in `int()`.

| Field | Columns | Read as |
|---|---|---|
| catalog number | 3-7 | `int` |
| epoch | 19-32 | `_epoch`, two-digit year pivoting at 57 |
| first derivative of mean motion | 34-43 | `float`, sign in column 34 |

Ran `pytest -q tests/test_parse.py`: 37 passed.
"""

# The board: (task, ...). Details are written the way a lead writes them.
_BOARD_TASKS = (
    Task(
        id="t1",
        title="parse the element set",
        detail=(
            "Builder task. `tle/parse.py` only.\n"
            "\n"
            "Read the TLE two-line format straight from the bytes. Keep the column\n"
            "offsets in **one table** -- the format is fixed-width and every field is\n"
            "positional, so a split on whitespace is wrong on the lines where a value\n"
            "runs into its neighbour.\n"
            "\n"
            "- `parse_line_1(line) -> Line1` and `parse_line_2(line) -> Line2`;\n"
            "- columns are 1-based in the format description and 0-based in the\n"
            "  table: convert once, in `_field`;\n"
            "- do not validate the checksum here (see `t2`).\n"
            "\n"
            "Verify: `pytest -q tests/test_parse.py`."
        ),
        touches=("tle/parse.py",),
        declared_at=1.0,
        property="A line that fails to parse is reported, never skipped.",
    ),
    Task(
        id="t2",
        title="validate the checksum",
        detail=(
            "Builder task. Modulo-10 over the first 68 columns, minus signs counting\n"
            "as one; letters, blanks, periods and plus signs count as zero.\n"
            "\n"
            "Call it from `parse_line_1` and `parse_line_2` so that no caller can\n"
            "forget to. Raise `ChecksumError` naming the line number and both digits.\n"
            "\n"
            "> Written before brief entry 002. If the two disagree, the brief is the\n"
            "> later word."
        ),
        depends_on=("t1",),
        touches=("tle/parse.py", "tle/checksum.py"),
        declared_at=2.0,
    ),
    Task(
        id="t3",
        title="cover the parser in tests",
        detail=(
            "Builder task. A new `tests/test_parse.py`.\n"
            "\n"
            "1. The reference pair from the format description, field by field.\n"
            "2. A line where two values touch, which is the case a whitespace split\n"
            "   gets wrong.\n"
            "3. A negative drag term, so the minus rule is exercised.\n"
            "\n"
            "Mutation-test it: break `_field` by one column and confirm a failure.\n"
            "Record what is *not* covered -- Alpha-5 catalog numbers are known not to\n"
            "be."
        ),
        touches=("tests/test_parse.py",),
        depends_on=("t2",),
        declared_at=3.0,
    ),
    # A dependency nobody declared. Unclaimable forever, and this pane is the only
    # place that is visible.
    Task(id="t4", title="update the changelog", depends_on=("t9",), declared_at=4.0),
    # Unblocked on purpose. A claim is refused while its dependencies are
    # unfinished, so a task behind t2 could not reach the "owner stopped" state
    # this row exists to show.
    Task(
        id="t5",
        title="profile the hot loop",
        detail=(
            "Reviewer task. Writes no file.\n"
            "\n"
            "`parse_line_1` is called once per record and the catalog is ~30,000\n"
            "records. Measure it before anyone optimises it: `python -m cProfile -s\n"
            "cumtime tools/bench.py`, and report the top ten rows."
        ),
        declared_at=5.0,
    ),
    # Declares no dependency and gets one: it writes a file t2 also writes, and the
    # board sequences the two. A wait whose cause is visible only in `touches`.
    Task(
        id="t6",
        title="document the checksum rule",
        detail=(
            "Builder task. The module docstring of `tle/checksum.py` and a section in\n"
            "`docs/format.md`.\n"
            "\n"
            "Say what counts and what does not, with one worked line. The rule is\n"
            "short and is the thing every reimplementation gets wrong: a minus sign is\n"
            "**1**, not 0 and not its code point."
        ),
        touches=("tle/checksum.py", "docs/format.md"),
        declared_at=6.0,
    ),
    Task(
        id="t7",
        title="pivot the two-digit epoch year",
        detail=(
            "Builder task. `tle/epoch.py` and `tests/test_epoch.py`.\n"
            "\n"
            "Years 57-99 are 1957-1999 and 00-56 are 2000-2056. The pivot is in the\n"
            "format description and nowhere in the code, which reads every year as\n"
            "20xx.\n"
            "\n"
            "Open question, not for the builder to settle alone: whether a year past\n"
            "the pivot should raise or wrap."
        ),
        touches=("tle/epoch.py", "tests/test_epoch.py"),
        declared_at=7.0,
    ),
    Task(
        id="t8",
        title="pin the test dependencies",
        detail="`pyproject.toml` only. `pytest>=8`, nothing else moves.",
        touches=("pyproject.toml",),
        declared_at=8.0,
    ),
)

_ids = itertools.count(1)


def _context(
    used: int, *, threshold: int | None = 120_000, compactions: int = 0
) -> ContextSnapshot:
    return ContextSnapshot(
        used_tokens=used,
        max_tokens=180_000,
        raw_max_tokens=200_000,
        percentage=100.0 * used / 180_000,
        auto_compact_enabled=threshold is not None,
        auto_compact_threshold=threshold,
        model="claude-opus-5",
        polled_at=time.time(),
        compactions=compactions,
    )


class FakeDriver:
    """Drives a small tree of pretend agents through plausible transitions."""

    def __init__(self, bridge: Bridge, *, seed: int = 0) -> None:
        self.bridge = bridge
        self.rng = random.Random(seed)
        self.nodes: list[NodeId] = []
        self.transcripts: dict[NodeId, Transcript] = {}
        # The nodes that carry a particular fixture, by what they are for:
        # ``report`` (the whole report, turn ended), ``streaming`` (the same report
        # arriving), ``asked``, ``failed``, and the team's ``lead``, ``builder``,
        # ``reviewer`` and ``scout``.
        self.cast: dict[str, NodeId] = {}
        # Nodes a tick leaves alone. A fixture that is there to be looked at has to
        # hold still: a random state change clears a question, and a random
        # fragment lands in the middle of a document.
        self._still: set[NodeId] = set()
        self._stream_segment = 0
        self._stream_offset = 0
        self._stream_rest = 0
        self._stream_chunks = itertools.cycle(_STREAM_CHUNKS)
        self._stopping = False

    async def run(self) -> None:
        """Spawn a starting tree, then keep it moving until cancelled."""
        try:
            await self._seed_tree()
            for step in itertools.count(1):
                if self._stopping:
                    break
                await asyncio.sleep(_STREAM_INTERVAL)
                self._stream_step()
                if step % _TICK_EVERY == 0:
                    self._tick()
        except asyncio.CancelledError:
            # Expected on shutdown; nothing to unwind.
            raise

    async def _seed_tree(self) -> None:
        root = self._spawn(parent=None, agent_type=None)
        await asyncio.sleep(0.1)
        for _ in range(2):
            self._spawn(parent=root, agent_type=self.rng.choice(("Explore", "code-reviewer")))
        second = self._spawn(parent=None, agent_type=None)
        third = self._spawn(parent=second, agent_type="general-purpose")
        # A grandchild, so the pane's depth handling is exercised rather than assumed.
        self._spawn(parent=third, agent_type="Explore")

        # One of each interesting terminal/edge state, so the pane's less common
        # branches are visible on launch rather than only after a long wait.
        self.bridge.emit(ContextPolled(root, _context(30_000)))
        self.bridge.emit(ContextPolled(self.nodes[1], _context(112_000)))
        self.bridge.emit(ContextPolled(self.nodes[2], _context(60_000, threshold=None)))
        self.bridge.emit(ContextPolled(second, _context(90_000)))
        # The segment and the intent together, as the real driver's PreCompact hook
        # produces them.
        self.transcripts[second].append(
            SegmentKind.COMPACTION, "\n--- context compacted; earlier reasoning discarded ---\n"
        )
        self.bridge.emit(CompactionObserved(second, at=time.time(), trigger="auto"))
        self.bridge.emit(ContextPolled(second, _context(8_000)))

        # On two nodes here and two more on the team, so the queue is genuinely
        # cross-agent and the scoped batch button has something to act on. None on
        # ``second``, which has a report to stream, and none on the grandchild,
        # which is the one node left for a tick to move: a parked node stays parked.
        self._park(self.nodes[1], _WRITE)
        self._park(self.nodes[1], _BASH)
        self._park(self.nodes[2], _EDIT_ANCHOR_ABSENT)
        self._park(self.nodes[2], _EDIT_IN_PLACE)

        # The report, whole, on a session whose turn has ended with it -- so it is
        # in the context log as a settled document and in DETAIL as what the agent
        # said. The same script streams onto ``second`` from ``_stream_step``.
        for step in _REPORT_STEPS:
            _write_step(self.transcripts[root], step)
        self.bridge.emit(StateChanged(root, AgentState.AWAITING_INPUT, topic=AWAITING_TOPIC))
        self.bridge.emit(
            StateChanged(second, AgentState.THINKING, topic="writing the audit report")
        )
        self.cast["report"] = root
        self.cast["streaming"] = second

        # A turn that ended with a question, its prose sitting behind a bulky tool
        # result. AWAITING_INPUT with the answer buried is the exact shape that
        # made the inbox composer and DETAIL's "what it said" render machinery
        # instead of words, and the report above does not have it: its prose is
        # the last thing in the buffer.
        asked = self._spawn(parent=None, agent_type=None)
        asked_transcript = self.transcripts[asked]
        asked_transcript.append(SegmentKind.TOOL_CALL, "Grep(pattern=checksum)\n")
        asked_transcript.append(
            SegmentKind.TOOL_RESULT,
            "Grep -> " + "".join(f"tle/parse.py:{i}: checksum ignored\n" for i in range(60)),
        )
        asked_transcript.append(
            SegmentKind.OUTPUT,
            "The checksum is computed but never compared, in sixty places.\n"
            "Do you want me to fix the parser, or only report it?\n",
        )
        self.bridge.emit(StateChanged(asked, AgentState.AWAITING_INPUT, topic=AWAITING_TOPIC))
        self.cast["asked"] = asked

        # The transcript line and the intent together, as the real driver writes a
        # failure.
        self.transcripts[third].append(SegmentKind.ERROR, "\ntool call rejected\n")
        self.bridge.emit(
            AgentFinished(
                third,
                AgentState.FAILED,
                # monotonic, to match AgentRecord.started_at -- see model.AgentRecord.
                ended_at=time.monotonic(),
                error="tool call rejected",
            )
        )
        self.cast["failed"] = third

        # One team, so DETAIL's board section is reachable at all. Every other root
        # here is solo and correctly renders no board, which is exactly why the
        # section could not be judged from this fixture before.
        team = self._spawn(
            parent=None, agent_type=None, template="feature", brief=self._seed_brief()
        )
        self.cast["lead"] = team
        self._seed_board(team)
        self._still.update(self.cast.values())

    def _seed_brief(self) -> str:
        """
        A brief with one premise already overturned, so the pane's whole job is
        reachable.

        Without a superseded entry the fixture exercises the easy half: a list that
        renders. Obligation 1 -- an overturned premise stays visible and says what
        overturned it -- has no state to show, and a regression that dropped it
        would look correct in every screenshot.

        Written under the system temporary directory, as a real launch writes
        outside the working tree: an entry in the tree would dirty it on every
        launch (``brief.session_dir``).
        """
        directory = pathlib.Path(tempfile.mkdtemp(prefix="pptmstr-fake-")) / "sess-fake"
        # The seed a real launch writes: the operator's launch text, verbatim.
        brief.write_entry(
            directory,
            "# The TLE parser\n"
            "\n"
            "The TLE parser is fixed-width. Every field is positional, so a split on\n"
            "whitespace is wrong on the lines where a value runs into its neighbour.\n"
            "\n"
            "**Decided**\n"
            "\n"
            "- columns come from one table, `_FIELD_OFFSETS`, and nothing else knows\n"
            "  an offset;\n"
            "- the reference pair in the format description is the first test.\n"
            "\n"
            "**Open:** whether the checksum belongs in the parser or beside it.",
        )
        brief.write_entry(
            directory,
            "Out of scope: the orbit propagator. This session is the parser only.\n"
            "\n"
            "If a change to `tle/propagate.py` looks necessary, that is a finding to\n"
            "report, *not* a file to edit.",
        )
        brief.write_entry(
            directory,
            "The checksum belongs beside the parser, not inside it -- 000 left this "
            "open and the answer is that a parser that validates cannot be reused on "
            "a record that has not been checksummed yet.\n"
            "\n"
            "Concretely:\n"
            "\n"
            "1. `tle/checksum.py` exposes `verify(line)`;\n"
            "2. `parse_line_1` and `parse_line_2` do not call it;\n"
            "3. `tools/repair.py` keeps working on lines it knows are corrupt.",
            supersedes=(0,),
        )
        return str(directory)

    def _seed_board(self, root: NodeId) -> None:
        """
        One team session with a board on it, so the BOARD pane is reachable.

        Without this the whole fixture is solo sessions, and the board renders as
        absent everywhere -- which is correct behaviour and proves nothing about
        the drawing. Every state a row has is represented here:

        - ``t1`` completed by a worker that has since stopped;
        - ``t2`` claimed and being worked, with a concern naming it;
        - ``t3`` blocked on a dependency its declarer wrote;
        - ``t4`` blocked on an id that was never declared;
        - ``t5`` still claimed by a worker that stopped holding it;
        - ``t6`` blocked on a dependency the board added, because it writes a file
          ``t2`` also writes;
        - ``t7`` claimed, given back, and claimable again;
        - ``t8`` completed by a worker that is still running.

        ``t1`` and ``t5`` are the pair worth keeping distinct. Only one of them is a
        warning, and a fixture carrying just the stranded row cannot show that the
        ordinary success path is being flagged as one.

        The concern log has every state a concern has and every direction one
        travels: worker to lead, lead to worker, worker to worker; delivered,
        waiting, withdrawn, and one the operator rewrote. Bodies run to paragraphs,
        because the body is where the reasoning is and the subject is only a label.
        """
        builder = self._spawn(parent=root, agent_type="builder")
        reviewer = self._spawn(parent=root, agent_type="reviewer")
        scout = self._spawn(parent=root, agent_type="explore")
        self.cast.update(builder=builder, reviewer=reviewer, scout=scout)

        for task in _BOARD_TASKS:
            self.bridge.emit(TaskDeclared(task, node_id=root))

        now = time.monotonic
        # Taken and handed back before the work that was finished, which is the
        # order a worker that picked the wrong task first does it in.
        self.bridge.emit(TaskClaimRequested(scout, request_id="fake-k0", task_id="t7"))
        self.bridge.emit(TaskReleased(scout, "t7"))
        # Done properly and by an agent that has since stopped, which is what a
        # worker that did its job looks like. Not a warning, and the fixture needs
        # it precisely so that a regression putting the warning here is visible.
        self.bridge.emit(TaskClaimRequested(scout, request_id="fake-k1", task_id="t1"))
        # With a result and a list of what was not run, so the pane's ledger line
        # has a row to draw.
        self.bridge.emit(
            TaskCompleted(
                scout,
                "t1",
                at=now(),
                result="Offsets are in one table; 14 parser tests pass.",
                not_run="Nothing was tried against a real catalogue file.",
            )
        )
        # Claimed and still being worked, by a worker that has also finished one.
        self.bridge.emit(TaskClaimRequested(builder, request_id="fake-k4", task_id="t8"))
        self.bridge.emit(TaskCompleted(builder, "t8", at=now()))
        self.bridge.emit(TaskClaimRequested(builder, request_id="fake-k2", task_id="t2"))
        # Claimed by an agent that then stops, which is the stranded row: the task
        # stays CLAIMED, nothing releases it, and nobody is working it.
        self.bridge.emit(TaskClaimRequested(reviewer, request_id="fake-k3", task_id="t5"))

        def post(
            cid: str,
            sender: NodeId,
            recipient: NodeId,
            subject: str,
            body: str,
            *,
            about: str | None = None,
            edited: bool = False,
        ) -> None:
            self.bridge.emit(
                ConcernPosted(
                    sender,
                    Concern(
                        id=cid,
                        sender=sender,
                        recipient=recipient,
                        subject=subject,
                        body=body,
                        posted_at=now(),
                        edited=edited,
                        task_id=about,
                        # One marked row, so the marker is drawn beside two
                        # that are not.
                        incident=cid == "fake-c3",
                    ),
                )
            )

        post(
            "fake-c1",
            reviewer,
            root,
            "the retry loop never terminates",
            "From the third call onward `fetch_catalog` retries with the same\n"
            "arguments and no backoff, and the server answers `429` every time.\n"
            "\n"
            "Read, not run -- I have no shell. The loop is:\n"
            "\n"
            "```python\n"
            "while True:\n"
            "    response = session.get(url)\n"
            "    if response.status_code != 429:\n"
            "        break\n"
            "```\n"
            "\n"
            "There is no counter and no sleep. A builder should run it against the\n"
            "recorded fixture in `tests/data/throttled.json` before anyone fixes it,\n"
            "because the fix depends on whether the `Retry-After` header is present.",
        )
        # fake-c2 names t2, which is the row that is claimed and being worked: a
        # live, correctly reasoning owner that is deliberately waiting. `owner_gone`
        # is false there, so without the link that row is pixel-identical to
        # ordinary progress -- the exact case the task_id field exists for.
        post(
            "fake-c2",
            builder,
            root,
            "checksum ignored in sixty places",
            "`tle/parse.py` computes the checksum and never compares it. Sixty call\n"
            "sites of `parse_line_1` rely on that without knowing it.\n"
            "\n"
            "I am holding `t2` until you say where the comparison goes:\n"
            "\n"
            "1. inside the parser, as the task detail says, or\n"
            "2. beside it, as brief entry 002 says.\n"
            "\n"
            "They cannot both be right, and *the brief is the later word*.",
            about="t2",
            # The operator's rewrite, which is a stamped fact rather than a field
            # the sender can set.
            edited=True,
        )
        post(
            "fake-c3",
            reviewer,
            root,
            "two roles answer to one name",
            "Only the first is reachable. A second builder started after the first is\n"
            "`builder-2`, and a concern addressed to `builder` goes to the first one\n"
            "whether or not it is the one holding the task.\n"
            "\n"
            "Nothing is wrong in the code. The roster in your prompt is.",
        )
        post(
            "fake-c4",
            reviewer,
            builder,
            "test_checksum_counts_minus asserts less than its name",
            "The test is named for the minus rule and its input has no minus sign in\n"
            "it: `1 25544U 98067A   08264.51782528  .00002182  00000+0  16538+3 0  9993`\n"
            "\n"
            "It passes against a `checksum` that ignores minus signs entirely. Use the\n"
            "NOAA 19 pair, whose drag term is negative, and mutation-test against the\n"
            "**current** line rather than a broken variant invented for the purpose.",
            about="t3",
        )
        post(
            "fake-c5",
            root,
            builder,
            "beside the parser, and t2 is amended",
            "The brief is right and the detail was stale. Build `verify(line)` in\n"
            "`tle/checksum.py`; do not call it from the parser.\n"
            "\n"
            "- `tle/ingest.py` verifies, then parses;\n"
            "- `tools/repair.py` parses without verifying, as it does now.\n"
            "\n"
            "Carry on with `t2`.",
            about="t2",
        )
        post(
            "fake-c6",
            scout,
            reviewer,
            "what t1 did not cover",
            # Not markdown: one fact per line and a pasted traceback, which is how
            # a worker without a moment to format writes. Soft line breaks would
            # fold all of it into one paragraph.
            "ran: pytest -q tests/test_parse.py -k alpha5\n"
            "cwd: ~/Source/orbital\n"
            "exit: 1\n"
            "covered: no\n"
            "\n"
            "Traceback (most recent call last):\n"
            '  File "tests/test_parse.py", line 41, in test_alpha5\n'
            "    assert parse_line_1(LINE).catalog == 100001\n"
            '  File "tle/parse.py", line 88, in parse_line_1\n'
            '    catalog=int(_field(line, "catalog")),\n'
            "ValueError: invalid literal for int() with base 10: 'A0001'",
            about="t1",
        )
        # Everything above is read by the agent it was sent to.
        for n, recipient in enumerate((root, builder, reviewer), 1):
            self.bridge.emit(InboxRead(node_id=recipient, request_id=f"fake-r{n}", at=now()))

        # Posted after the lead last read its inbox, so the log shows delivered and
        # waiting side by side rather than every row in the same state.
        post(
            "fake-c7",
            builder,
            root,
            "verify() is in; ingest.py is not mine to edit",
            "`tle/checksum.py` is written and `tests/test_checksum.py` passes.\n"
            "\n"
            "`tle/ingest.py` has to call `verify` and no task declares that file, so\n"
            "I have not touched it. Either add it to `t2`'s `touches` or declare a\n"
            "task for it.",
            about="t2",
        )
        # A task id that is on no board: the dangling reference the projection
        # reports, on a concern the operator then stopped.
        post(
            "fake-c8",
            reviewer,
            root,
            "t12 has no owner",
            "Nobody has claimed `t12` and it blocks the release.",
            about="t12",
        )
        self.bridge.emit(ConcernWithdrawn("fake-c8", reason="there is no t12", node_id=root))

        # Parked by the two agents still running. The builder's are one message
        # with two tool calls in it, which is how one node comes to hold two.
        self._park(builder, _MULTI_EDIT)
        self._park(builder, _POST_CONCERN)
        self._park(root, _DECLARE_TASK)

        # The scout's answer and its ending arrive together, as SubagentStop
        # delivers them; the answer is also the last thing in its own transcript.
        self.transcripts[scout].append(SegmentKind.OUTPUT, _DELIVERABLE)
        self.bridge.emit(SubagentDelivered(scout, _DELIVERABLE))
        self.bridge.emit(AgentFinished(scout, AgentState.DONE, ended_at=now(), error=None))
        self.bridge.emit(AgentFinished(reviewer, AgentState.DONE, ended_at=now(), error=None))
        self._seed_decisions(root, builder)

    def _seed_decisions(self, lead: NodeId, worker: NodeId) -> None:
        """
        Two decisions on the team session, so the surfaces that draw one have both
        of its lives to draw: open in the operator's queue, and answered.

        The open one is the worker's, the operator's to take, not reversible, and
        names a task on the board -- every field the queue row is composed from
        is set. The answered one is the lead's, already taken and wanting
        ratification, blocking nothing on the board, with reversibility left
        unstated and an answer of more than one line, which is the text that has
        to come back to its asker exactly.

        Evidence and recommendation are both filled on both, because the reason
        they are separate fields is only visible when a surface shows them apart.

        The answer is emitted here as the operator's, with no node: this fixture
        stands in for the person at the pane, which nothing on the bus may do.
        """
        now = time.monotonic()
        asked = (
            Decision(
                id="fake-d1",
                asker=worker,
                question="The checksum is wrong on live data. Reject those lines or keep them?",
                blocks=("t2",),
                if_unanswered="I reject them and list each one in the result.",
                evidence=(
                    "Sixty lines of the sample catalogue fail modulo-10.\n"
                    "Every one of them parses; only the last digit disagrees."
                ),
                recommendation="Reject. A line that fails its checksum is not data.",
                standing=Standing.OPERATORS,
                reversible=False,
                raised_at=now,
            ),
            Decision(
                id="fake-d2",
                asker=lead,
                question="I split the parser task in two rather than widen it. Keep the split?",
                blocks=(),
                if_unanswered="The split stands.",
                evidence="t1 and t2 both write tle/parse.py, so the board ordered them.",
                recommendation="Keep it: the two halves are reviewed separately.",
                standing=Standing.TAKEN,
                reversible=None,
                raised_at=now,
            ),
        )
        for decision in asked:
            self.bridge.emit(DecisionRaised(decision.asker, decision, at=now))
        self.bridge.emit(
            DecisionAnswered(
                "fake-d2",
                "Keep the split.\nBut t3 waits for both halves, not only the second.",
                at=now,
            )
        )

    def _spawn(
        self,
        parent: NodeId | None,
        agent_type: str | None,
        template: str = "solo",
        brief: str | None = None,
    ) -> NodeId:
        n = next(_ids)
        node: NodeId = (f"sess-{n}", None) if parent is None else (parent[0], f"agent-{n}")
        # The transcript travels with the spawn, exactly as the real driver does it:
        # both sides must hold the same object or the pane reads an empty buffer.
        transcript = Transcript()
        self.transcripts[node] = transcript
        task = self.rng.choice(_TASKS)
        if parent is None:
            # A root's opening task goes through AgentSession.send, which writes a
            # SYSTEM segment -- and that segment is the only turn boundary the
            # transcript has (Transcript.turn_prose). A fixture that skipped it
            # would leave every fake session looking like one endless turn, so the
            # boundary would be exercised by nothing. Sub-agents are spawned by a
            # tool call rather than by send and correctly get no marker.
            transcript.append(SegmentKind.SYSTEM, f"\n> {task}\n")
        transcript.append(SegmentKind.REASONING, "Working out where to start.\n")
        transcript.append(SegmentKind.OUTPUT, "Right, here is the plan.\n")
        transcript.append(SegmentKind.TOOL_CALL, "Read(file_path=pptmstr/store.py)\n")
        transcript.append(SegmentKind.TOOL_RESULT, "268 lines\n")
        self.bridge.emit(
            AgentSpawned(
                node_id=node,
                parent=parent,
                task=task,
                model=self.rng.choice(_MODELS),
                started_at=time.monotonic(),
                agent_type=agent_type,
                topic="starting",
                # Roots only. A sub-agent inherits its session's directory in the
                # store, which is the same rule the real driver relies on.
                cwd=self.rng.choice(_CWDS) if parent is None else None,
                # Roots only, exactly as the real driver announces it: a sub-agent
                # has no template of its own and must not read as a team.
                template=template if parent is None else None,
                brief=brief if parent is None else None,
                transcript=transcript,
            )
        )
        self.nodes.append(node)
        return node

    def _park(self, node: NodeId, call: tuple[str, Mapping[str, Any]]) -> None:
        self.bridge.emit(ApprovalRequested(node, self._pending(node, call)))

    def _pending(self, node: NodeId, call: tuple[str, Mapping[str, Any]]) -> PendingApproval:
        name, args = call
        texts = diff_texts(name, args)
        return PendingApproval(
            id=f"pending-{next(_ids)}",
            node=node,
            tool_name=name,
            tool_use_id=f"tu-{next(_ids)}",
            raw_args=args,
            summary=summarize(name, args),
            # Monotonic, like the real driver's park and like every other instant an
            # age is measured from -- see PendingApproval.requested_at. A fixture
            # that stamped the other clock would render its own queue as uniformly
            # "0s" and sorted last, which is the defect rather than a picture of it.
            requested_at=time.monotonic(),
            diff=render_texts(name, texts),
            texts=texts,
        )

    def _stream_step(self) -> None:
        """
        Append the next piece of the report to the streaming session.

        Prose arrives a few characters at a time and a tool segment arrives whole,
        which is how the real stream delivers them. When the report is complete it
        is held for a while and then written again as a new turn, so there is
        always a document in flight however long the app has been open.
        """
        node = self.cast.get("streaming")
        if node is None:
            return
        transcript = self.transcripts[node]
        if self._stream_rest:
            self._stream_rest -= 1
            if not self._stream_rest:
                transcript.append(SegmentKind.SYSTEM, "\n> write it out again\n")
            return
        step = _REPORT_STEPS[self._stream_segment]
        kind, text = _step_text(step)
        if kind in _STREAMED_KINDS:
            size = next(self._stream_chunks)
            piece = text[self._stream_offset : self._stream_offset + size]
            self._stream_offset += size
            transcript.append(kind, piece)
        else:
            self._stream_offset = len(text)
            _write_step(transcript, step)
        if self._stream_offset >= len(text):
            self._stream_offset = 0
            self._stream_segment += 1
            if self._stream_segment == len(_REPORT_STEPS):
                self._stream_segment = 0
                self._stream_rest = _STREAM_REST
                # The report's turn ends with it, as the real driver's ends at its
                # result, and the next prompt opens the next one.
                transcript.end_turn()

    def _tick(self) -> None:
        """
        Move one random agent along. Deliberately shallow: this is a fixture.

        Parks nothing. Everything waiting on the operator is there once the seed
        has been applied and stays exactly that, so two screenshots taken at
        different moments are of the same queue.
        """
        movable = [node for node in self.nodes if node not in self._still]
        if not movable:
            return
        node = self.rng.choice(movable)
        # Stream a few characters at a time, the way real output arrives -- that is
        # what exercises the pane's incremental line cache rather than a bulk write.
        transcript = self.transcripts.get(node)
        if transcript is not None:
            kind = self.rng.choice(
                (SegmentKind.REASONING, SegmentKind.OUTPUT, SegmentKind.TOOL_RESULT)
            )
            transcript.append(kind, self.rng.choice(_FRAGMENTS))
        roll = self.rng.random()
        if roll < 0.45:
            self.bridge.emit(
                StateChanged(node, AgentState.THINKING, topic=self.rng.choice(_TOPICS))
            )
        elif roll < 0.7:
            self.bridge.emit(
                StateChanged(node, AgentState.RUNNING_TOOL, topic=self.rng.choice(_TOPICS))
            )
        elif roll < 0.9:
            self.bridge.emit(TopicChanged(node, self.rng.choice(_TOPICS)))
        else:
            self.bridge.emit(StateChanged(node, AgentState.RATE_LIMITED, topic="backing off"))

    def stop(self) -> None:
        self._stopping = True
