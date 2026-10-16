"""
A tool call in the session log, drawn as a block from its ``ToolEntry``.

The operator picked the stacked block ("'Stacked' tool block looks great for
bash!") and signed off the per-tool proofs drawn in ``scripts/tool_block_probe.py``
("Those look really good"); planning/2026-10-15-tool-block-variants.md, "Per-tool
proofs", has each tool's design, what it reads and why it is cut where it is.
What is built here is those proofs:

- **One well per call**, on the theme's terminal ground (``Palette.terminal_bg``,
  never a fixed near-black), with an outline where the theme asks for one.
- **A caption line** in small proportional type: the tool's word in accent (Bash
  has none: it is the terminal), its details dim, and the state last -- dim for
  done, accent for running, warn for no result, danger for an error.
- **A head**, what the call asked, and **a body**, what came back (or what a write
  carries). Each tool shows its own: a ``$`` command and the last lines of the
  output for Bash, a numbered excerpt for Read, a diff for Edit, and so on.
- **An error** is a 3 px danger rule down the left edge (the operator: "Errors
  getting a red rule is fine and works well to my eyes").

**The state** is not read off ``result is None`` alone: a call whose turn was
interrupted has no result and never will (tool-entries' review, item 2), so the
caller says whether the call can still be running (``layout``'s ``live``).

**Nothing the entry holds is out of reach.** Where a block shows less than the
entry holds -- rows left out, a line cut at ``LINE_CUT``, an argument cut to one
line -- its affordance (``Layout.more``) opens **the viewer** in place: every
input field and every line of the output, a long line in ``LINE_CUT`` pieces,
in a scrolling well of its own drawn through a clipper, so a 1 MiB result is
never handed to one text call. What is drawn collapsed may still be wider than
the pane; the block scrolls sideways to it, as a code block does.

**Verbatim in, drawable out.** ``ToolEntry.name``, ``.input`` and
``ToolResult.output`` are held as the SDK sent them (``transcript``'s
docstring), so every string taken from them is made drawable here, by ``_d``
or ``_chunks``, and nothing else is handed to ImGui
(``tests/test_verbatim_drawn.py``).

**A list of content blocks** -- an MCP tool's result, an Agent's report -- is held
as its ``repr`` (tool-entries' decision), and the block reads the text blocks back
out of that ``repr`` where it can (``_blocks_text``). The viewer shows the
``repr`` itself, which is what the entry holds; that is a known limit until the
entry keeps the structure.

Pure (``layout``, ``viewer_rows``) and a thin draw (``draw``).
"""

from __future__ import annotations

import ast
import difflib
import enum
import functools
import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import urlsplit

from imgui_bundle import imgui

from .. import drawable
from ..theme import Color, LinkInks, P, terminal_ink
from ..theme import link_inks as theme_link_inks
from ..transcript import ToolEntry, ToolResult

_T = TypeVar("_T")


class State(enum.Enum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    NO_RESULT = "no result"


# A span is (face, ink, text). Face: "mono" (the code face), "prose" (proportional,
# wrapped when alone on its row), "caption" (proportional, small), or "tip" (not
# drawn: it makes its row a link whose tooltip is the text). Ink: a name ``_ink``
# resolves against the theme in force.
Span = tuple[str, str, str]
Row = tuple[Span, ...]
Where = tuple[str | None, int]
"""
A line of what an entry holds, as the viewer holds it: line ``n`` of an input
key's value (split at ``\\n``), or with no key, line ``n`` of the output.
"""
FACES = ("mono", "prose", "caption", "tip")
INKS = ("text", "dim", "add", "del", "ctx", "accent", "link", "ok", "warn", "danger")

LINE_CUT = 400
"""
The longest line a collapsed block draws; the viewer draws a longer one in pieces
of this many characters. A terminal block does not wrap, and a line can be the
whole 1 MiB (a minified file, an MCP ``repr``).
"""

COMMAND_LINES = 3
ARG_ROWS = 6
ARG_WIDTH = 72
URL_REST = 56

SHOWN = {
    "Bash": 6,
    "Read": 6,
    "Edit": 8,
    "Write": 5,
    "Grep": 6,
    "Glob": 6,
    "WebFetch": 3,
    "WebSearch": 4,
    "Agent": 3,
    "TodoWrite": 8,
    "NotebookEdit": 6,
    "mcp": 4,
    "other": 4,
}
"""
How many body rows each tool shows before its affordance, as the proofs settle it:
the last ones for Bash, where the verdict of a build or a test run is, and the
first ones for every other tool.
"""

SHOW_ALL = "show all"
"""The affordance's label where nothing was left out but something was cut short."""

_EXIT = re.compile(r"Exit code (\d+)")
_FOUND = re.compile(r"Found (\d+) ")
_TOOL_USE_ERROR = re.compile(r"\A\s*<tool_use_error>([\s\S]*)</tool_use_error>\s*\Z")
_REDIRECT = re.compile(r"^Redirect URL \(from the server's Location header[^)]*\): (\S+)", re.M)
_USAGE = re.compile(r"<usage>subagent_tokens: (\d+) tool_uses: (\d+) duration_ms: (\d+)</usage>")


@dataclass(frozen=True, slots=True)
class Layout:
    """
    What one block draws, every string drawable. ``more`` is the affordance's
    label, ``""`` where the block shows everything; it is drawn above the body
    when ``tail`` (the last rows are the ones kept) and under it otherwise.
    ``shown`` is the viewer's lines its head and body draw whole (``find``).
    """

    tool: str
    state: State
    caption: tuple[Span, ...]
    head: tuple[Row, ...]
    body: tuple[Row, ...]
    more: str = ""
    tail: bool = False
    shown: frozenset[Where] = frozenset()


def state_of(entry: ToolEntry, live: bool) -> State:
    """
    What a block says of a call. A call with no result is running only while
    ``live`` -- its turn has not ended.
    """
    if entry.result is None:
        return State.RUNNING if live else State.NO_RESULT
    return State.ERROR if entry.result.is_error else State.DONE


def _d(value: object) -> str:
    """A verbatim value as text a text call takes: ``drawable.text`` of it or its repr."""
    return drawable.text(value if isinstance(value, str) else repr(value))


def _as_text(value: object) -> str:
    """An input value as it is shown: a string as it is, anything else as JSON."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def _timeout(ms: object) -> str | None:
    """
    A timeout in milliseconds, said exactly: in minutes or seconds where it is a
    whole number of them, and in milliseconds otherwise, since ``1234.57 s`` is not
    what was asked for.
    """
    if not isinstance(ms, int | float) or isinstance(ms, bool):
        return None
    if isinstance(ms, float) and ms.is_integer():
        ms = int(ms)
    if isinstance(ms, int) and ms > 0 and ms % 60000 == 0:
        return f"timeout {ms // 60000} min"
    if isinstance(ms, int) and ms > 0 and ms % 1000 == 0:
        return f"timeout {ms // 1000} s"
    return f"timeout {ms!r} ms"


def _mcp_parts(name: str) -> tuple[str, str] | None:
    if not name.startswith("mcp__"):
        return None
    server, _, tool = name[5:].partition("__")
    return server, tool or server


def _unwrap_error(output: str) -> str:
    found = _TOOL_USE_ERROR.match(output)
    return found[1].strip() if found else output


def _blocks(output: str) -> list[dict[Any, Any]] | None:
    """
    The content blocks of a list-of-content-blocks result, read back out of the
    ``repr`` the entry holds, or ``None`` where ``output`` is not one.
    """
    if not output.startswith("[{"):
        return None
    try:
        blocks = ast.literal_eval(output)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    if not isinstance(blocks, list) or not all(isinstance(b, dict) for b in blocks):
        return None
    return blocks


def _all_text(blocks: Sequence[Mapping[Any, Any]]) -> bool:
    """Whether every block is a text block and holds nothing but its text."""
    return all(
        b.get("type") == "text" and isinstance(b.get("text"), str) and set(b) <= {"type", "text"}
        for b in blocks
    )


def _blocks_text(output: str) -> str:
    """
    The text of a list-of-content-blocks result, read back out of the ``repr`` the
    entry holds; ``output`` itself where it is not one.
    """
    blocks = _blocks(output)
    if blocks is None:
        return output
    texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    return "\n".join(t for t in texts if isinstance(t, str)) or output


def block_summary(block: Mapping[Any, Any]) -> str:
    """
    One content block as the viewer names it: its type, and for an image or a
    document its media type and the size of its data.
    """
    kind = _as_text(block.get("type", "?"))
    source = block.get("source")
    if isinstance(source, Mapping):
        media = source.get("media_type")
        data = source.get("data")
        said = [kind]
        if media is not None:
            said.append(_as_text(media))
        if isinstance(data, str):
            said.append(f"{_kb(len(data) * 3 // 4)} as base64 {_kb(len(data))}")
        return " · ".join(said)
    if kind == "text" and isinstance(block.get("text"), str):
        return f"text · {_count(len(block['text']), 'character')}"
    return kind


def _redirect_target(output: str) -> str | None:
    """The target a WebFetch result names when the CLI did not follow a redirect."""
    if not output.startswith("REDIRECT DETECTED:"):
        return None
    found = _REDIRECT.search(output)
    return found[1] if found else None


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _one_text(todo: object) -> bool:
    """
    Whether a todo item is shown whole by its checklist row: its status is one the
    row marks, it holds nothing but its two texts, and the one not drawn says the
    same as the one drawn.
    """
    if not isinstance(todo, dict) or not set(todo) <= {"content", "status", "activeForm"}:
        return False
    if todo.get("status") not in ("completed", "in_progress", "pending"):
        return False
    shown, other = ("activeForm", "content")
    if todo["status"] != "in_progress":
        shown, other = other, shown
    return other not in todo or todo.get(other) == todo.get(shown)


def _kb(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB"


def _line_count(value: object) -> int:
    """
    How many lines the viewer holds ``value`` in.
    """
    return _as_text(value).count("\n") + 1


@dataclass
class _Builder:
    """
    The rows of one block, and the account of what of the entry they draw.

    The account is positive: a row records the line it draws, whole, as it is
    built (``drawing``), and whatever of the entry no row recorded -- an input
    key, an output line -- turns the affordance on (``_leaves_out``). A line is
    never counted as drawn because its text occurs somewhere in the block.
    """

    args: Mapping[str, Any] = field(default_factory=dict)
    # Whether anything of the entry was shortened on the way: what turns the
    # affordance on where no rows were left out.
    short: bool = False
    # How many times a line was shortened so far; a row built while it rose does
    # not draw its line whole.
    cuts: int = 0
    caption: list[Span] = field(default_factory=list)
    # The lines rows drew whole.
    drew: set[Where] = field(default_factory=set)
    # Input keys the block shows in a form of its own (a timeout in minutes, a flag
    # as a word, a description in the caption).
    took: set[str] = field(default_factory=set)
    # The output lines the layout reads, which ``drew`` and ``said`` count in, and
    # those of them it says in a form of its own (an exit code in the badge) or
    # that hold nothing to draw.
    out: list[str] = field(default_factory=list)
    said: set[int] = field(default_factory=set)
    # Input keys whose value's lines are the lines the layout reads one row each
    # (``value_lines``): only then is line ``n`` of the rows line ``n`` of the viewer.
    aligned: set[str] = field(default_factory=set)

    def cut(self, line: str) -> str:
        """``line`` drawable, cut at ``LINE_CUT``."""
        if len(line) > LINE_CUT:
            self.short = True
            self.cuts += 1
            line = line[: LINE_CUT - 1] + "…"
        return _d(line)

    def one_line(self, value: object, width: int) -> str:
        """``value`` on one drawn line of at most ``width`` characters, drawable."""
        text = " ".join(_as_text(value).split())
        if text != _as_text(value) or len(text) > width:
            self.short = True
            self.cuts += 1
        return _d(text if len(text) <= width else text[: width - 1] + "…")

    def drawing(self, where: Where, build: Callable[[], Row]) -> Row:
        """
        ``build``'s row, recording that it draws ``where`` unless it shortened it.
        """
        cuts = self.cuts
        row = build()
        if self.cuts == cuts:
            self.drew.add(where)
        return row

    def value(self, key: str, build: Callable[[], Row]) -> Row:
        """
        ``build``'s row, which draws the whole of ``key``'s value unless it shortened it.
        """
        cuts = self.cuts
        row = build()
        if self.cuts == cuts and key in self.args:
            self.drew.update((key, n) for n in range(_line_count(self.args[key])))
        return row

    def value_lines(self, key: str) -> list[str]:
        """
        ``key``'s value as the lines a layout draws one row each. They are the
        viewer's lines only where joining them gives the value back: a trailing
        newline or a ``\\r`` is not a row, so such a value is never drawn whole.
        An empty value is no rows, and drawn so.
        """
        text = _as_text(self.args.get(key, ""))
        lines = text.splitlines()
        if key in self.args and "\n".join(lines) == text:
            self.aligned.add(key)
            if not text:
                self.drew.add((key, 0))
        return lines

    def value_line(self, key: str, n: int, build: Callable[[], Row]) -> Row:
        """
        ``build``'s row, drawing line ``n`` of ``key``'s ``value_lines``.
        """
        if key not in self.aligned:
            return build()
        return self.drawing((key, n), build)

    def out_line(self, n: int, build: Callable[[], Row]) -> Row:
        """
        ``build``'s row, drawing output line ``n``.
        """
        return self.drawing((None, n), build)

    def flags(self, args: Mapping[str, Any], *keys: str) -> None:
        """``keys`` are flags the block says as a word when true and not at all when false."""
        self.took.update(k for k in keys if isinstance(args.get(k), bool))

    def kind(self, word: str) -> None:
        self.caption.append(("caption", "accent", word))

    def detail(self, text: str) -> None:
        self.caption.append(("caption", "dim", text))

    def mono(self, text: str, ink: str = "text") -> Row:
        return (("mono", ink, self.cut(text)),)

    def out_rows(self, count: int, ink: str = "dim") -> list[Row]:
        """
        The first ``count`` output lines, a row each.
        """
        return [self.out_row(ink)(n) for n in range(min(count, len(self.out)))]

    def out_row(self, ink: str = "dim") -> Callable[[int], Row]:
        """
        What ``out_rows`` makes of output line ``n``, for ``_cut_rows`` to build on demand.
        """
        return lambda n: self.out_line(n, lambda: self.mono(self.out[n].expandtabs(8), ink))

    def kv_rows(self) -> list[Row]:
        """
        The arguments a row each, ``key: value``, the value on one line.
        """
        rows: list[Row] = []
        for key, value in self.args.items():
            if len(rows) == ARG_ROWS:
                self.short = True
                rows.append((("mono", "dim", f"… {len(self.args) - ARG_ROWS} more arguments"),))
                break
            rows.append(self.value(key, functools.partial(self._kv_row, key, value)))
        return rows

    def _kv_row(self, key: str, value: object) -> Row:
        return (("mono", "dim", f"{_d(key)}: "), ("mono", "text", self.one_line(value, ARG_WIDTH)))

    def url_row(self, url: str) -> Row:
        """
        A url as a link: the scheme and the rest dim, the host in the link ink, a
        long path or query losing its middle, and the whole url as the tooltip.
        """
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return (("mono", "link", self.cut(url)), ("tip", "link", self.cut(url)))
        rest = url[len(parts.scheme) + 3 + len(parts.netloc) :]
        if len(rest) > URL_REST:
            self.short = True
            self.cuts += 1
            keep = URL_REST - 1
            rest = rest[: keep // 3] + "…" + rest[-(keep - keep // 3) :]
        return (
            ("mono", "dim", _d(f"{parts.scheme}://")),
            ("mono", "link", _d(parts.netloc)),
            ("mono", "dim", self.cut(rest)),
            ("tip", "link", self.cut(url)),
        )


def _count(n: int, what: str) -> str:
    """``n`` and ``what``, plural but for one: ``1 line``, ``3 lines``, ``2 matches``."""
    if n == 1:
        return f"{n:,} {what}"
    return f"{n:,} {what}{'es' if what.endswith(('ch', 'sh', 's', 'x')) else 's'}"


def _cut_rows(
    items: Sequence[_T],
    shown: int,
    build: Callable[[_T], Row],
    *,
    tail: bool = False,
    what: str = "line",
    after: str = "",
) -> tuple[list[Row], str]:
    """
    The first ``shown`` of ``items``, or the last where ``tail``, as rows, and the
    affordance's label for the rest: ``"N more lines"``, or ``"N earlier lines"``.

    Only what is kept is built (``build``): a result of 100,000 lines is six rows
    of work and a count, not 100,000 rows made and dropped.
    """
    if len(items) <= shown:
        return [build(item) for item in items], ""
    hidden = len(items) - shown
    kept = items[-shown:] if tail else items[:shown]
    return [build(item) for item in kept], _count(
        hidden, f"{'earlier' if tail else 'more'} {what}"
    ) + after


EDIT_DIFF_LINES = 1000
"""
The most lines of old and new string together an Edit's block diffs: 1,000 is
about 8 ms of ``difflib`` (measured 2026-10-16), and 10,000 was 250 ms, laid out
again each time the block came back into view.
"""


def _diff_rows(old: Sequence[str], new: Sequence[str]) -> tuple[list[str], bool]:
    """
    The rows of a unified diff of ``old`` against ``new`` with one line of context,
    without its headers, and whether they are the whole diff.

    Over ``EDIT_DIFF_LINES``, the lines the two share at either end are set aside
    first, one kept as context, and what is left is diffed when it fits; where it
    still does not, only its first ``EDIT_DIFF_LINES`` are, and the rows are a
    beginning.
    """
    if len(old) + len(new) > EDIT_DIFF_LINES:
        top = 0
        while top < len(old) and top < len(new) and old[top] == new[top]:
            top += 1
        foot = 0
        while foot < len(old) - top and foot < len(new) - top and old[-1 - foot] == new[-1 - foot]:
            foot += 1
        low = max(0, top - 1)
        old = old[low : len(old) - max(0, foot - 1)]
        new = new[low : len(new) - max(0, foot - 1)]
    whole = len(old) + len(new) <= EDIT_DIFF_LINES
    if not whole:
        half = EDIT_DIFF_LINES // 2
        old, new = old[:half], new[:half]
    rows = [
        row
        for row in difflib.unified_diff(old, new, lineterm="", n=1)
        if not row.startswith(("---", "+++", "@@"))
    ]
    return rows, whole


def _badge(state: State, word: str | None = None) -> Span:
    ink = {
        State.RUNNING: "accent",
        State.DONE: "dim",
        State.ERROR: "danger",
        State.NO_RESULT: "warn",
    }[state]
    text = word or ("no result · turn ended" if state is State.NO_RESULT else state.value)
    return ("caption", ink, text)


def _read_out(b: _Builder, output: str) -> list[str]:
    """
    The lines of a result that may be a list of content blocks: their text, read
    back. A block that is not text -- an image -- is drawn only in the viewer, so
    the block is shortened by it.
    """
    blocks = _blocks(output)
    if blocks is not None and not _all_text(blocks):
        b.short = True
    return _blocks_text(output).splitlines()


def _kept(
    lines: Sequence[str], keep: Callable[[str], bool] = lambda ln: bool(ln.strip())
) -> list[int]:
    """
    The indices of the ``lines`` that ``keep`` keeps: by default, those not blank.
    """
    return [n for n, ln in enumerate(lines) if keep(ln)]


def layout(entry: ToolEntry, live: bool) -> Layout:
    """
    The block for ``entry``: its tool's own caption, head and body, as the proofs
    settle them. ``live`` is whether the call's turn can still be running.
    """
    args: Mapping[str, Any] = entry.input or {}
    name = entry.name
    state = state_of(entry, live)
    result: ToolResult | None = entry.result
    output = "" if result is None else result.output
    failed = state is State.ERROR
    if failed:
        output = _unwrap_error(output)
    b = _Builder(args=args)
    b.out = lines = output.splitlines()
    head: list[Row] = []
    body: list[Row] = []
    more = ""
    tail = False
    badge: str | None = None

    if name == "Bash":
        # The terminal: no tool word, the description and limits over a $ line,
        # and the last lines of the output, all in the code face.
        if "description" in args:
            b.detail(_d(args["description"]))
            b.took.add("description")
        if (limit := _timeout(args.get("timeout"))) is not None:
            b.detail(limit)
            b.took.add("timeout")
        if args.get("run_in_background"):
            b.detail("background")
        if args.get("dangerouslyDisableSandbox"):
            b.caption.append(("caption", "warn", "unsandboxed"))
        b.flags(args, "run_in_background", "dangerouslyDisableSandbox")
        command = b.value_lines("command") or [""]
        head = [
            b.value_line("command", n, functools.partial(b.mono, ("$ " if n == 0 else "  ") + c))
            for n, c in enumerate(command[:COMMAND_LINES])
        ]
        if len(command) > COMMAND_LINES:
            b.short = True
            hidden = len(command) - COMMAND_LINES
            head.append((("mono", "dim", f"  … {_count(hidden, 'more line')}"),))
        start = 0
        if failed and lines and (code := _EXIT.fullmatch(lines[0])):
            badge = f"exit {code[1]}"
            b.said.add(0)
            start = 1
        body, more = _cut_rows(range(start, len(lines)), SHOWN["Bash"], b.out_row(), tail=True)
        tail = True
    elif name == "Read":
        b.kind("read")
        offset, limit = args.get("offset"), args.get("limit")
        b.took.update(k for k in ("offset", "limit") if _whole(args.get(k)))
        if isinstance(offset, int) and isinstance(limit, int):
            b.detail(f"lines {offset}–{offset + limit - 1}")
        elif isinstance(offset, int):
            b.detail(f"from line {offset}")
        elif isinstance(limit, int):
            b.detail(f"first {_count(limit, 'line')}")
        else:
            b.detail("whole file")
        if "pages" in args:
            b.detail(f"pages {_d(args['pages'])}")
            b.took.add("pages")
        head = [b.value("file_path", lambda: b.mono(_as_text(args.get("file_path", ""))))]
        if failed:
            body, more = _cut_rows(range(len(lines)), SHOWN["Read"], b.out_row("danger"))
        else:

            def numbered(n: int) -> Row:
                line = lines[n]
                num, tab, text = line.partition("\t")
                if tab and num.strip().isdigit():
                    return (
                        ("mono", "dim", f"{num.strip():>4} "),
                        ("mono", "text", b.cut(text.expandtabs(8))),
                    )
                return b.mono(line, "dim")

            if lines and result is not None:
                b.detail(_count(len(lines), "line"))
            body, more = _cut_rows(
                range(len(lines)), SHOWN["Read"], lambda n: b.out_line(n, lambda: numbered(n))
            )
    elif name == "Edit":
        b.kind("edit")
        old = b.value_lines("old_string")
        new = b.value_lines("new_string")
        diff_rows, whole = _diff_rows(old, new)
        if whole:
            b.caption.append(("caption", "add", f"+{sum(1 for r in diff_rows if r[:1] == '+')}"))
            b.caption.append(("caption", "del", f"−{sum(1 for r in diff_rows if r[:1] == '-')}"))
        else:
            # Too long to diff in a frame: the counts would be of a part, so the
            # caption says the sizes, and the strings are in the viewer.
            b.detail(f"{_count(len(old), 'line')} → {len(new):,}")
            b.short = True
        if args.get("replace_all"):
            b.detail("every occurrence")
        b.flags(args, "replace_all")
        head = [b.value("file_path", lambda: b.mono(_as_text(args.get("file_path", ""))))]
        # Context is one line each side: the strings themselves are in the viewer.
        every = whole and len(diff_rows) == len(old) + len(new)
        b.short = b.short or not every
        cuts = b.cuts
        body, more = _cut_rows(
            diff_rows,
            SHOWN["Edit"],
            lambda row: b.mono(
                row, "add" if row[:1] == "+" else "del" if row[:1] == "-" else "ctx"
            ),
            what="diff line",
        )
        # A diff with no context rows has a row for every line of both strings;
        # drawn with none left out or cut, it draws both whole.
        if every and not more and b.cuts == cuts:
            for key, held in (("old_string", old), ("new_string", new)):
                if key in b.aligned:
                    b.drew.update((key, n) for n in range(len(held)))
        if failed:
            body = body + b.out_rows(2, "danger")
            b.short = b.short or len(lines) > 2
    elif name == "Write":
        b.kind("write")
        content = _as_text(args.get("content", ""))
        content_lines = b.value_lines("content")
        if result is not None and not failed:
            created = output.startswith("File created successfully")
            b.caption.append(
                ("caption", "add" if created else "warn", "new file" if created else "overwrote")
            )
        size = len(content.encode("utf-8", "surrogatepass"))
        b.detail(f"{_kb(size)} · {_count(len(content_lines), 'line')}")
        head = [b.value("file_path", lambda: b.mono(_as_text(args.get("file_path", ""))))]
        if failed:
            # The error in place of the preview: the content is in the viewer.
            body = b.out_rows(2, "danger")
            b.short = b.short or len(lines) > 2
        else:
            body, more = _cut_rows(
                range(len(content_lines)),
                SHOWN["Write"],
                lambda n: b.value_line(
                    "content",
                    n,
                    lambda: (
                        ("mono", "add", "+ "),
                        ("mono", "text", b.cut(content_lines[n].expandtabs(8))),
                    ),
                ),
                after=" in the file",
            )
    elif name in ("Grep", "Glob"):
        b.kind(name.lower())
        if "path" in args:
            b.detail(f"in {_d(args['path'])}")
            b.took.add("path")
        for key in ("glob", "type"):
            if key in args:
                b.detail(f"{key} {_d(args[key])}")
                b.took.add(key)
        mode = args.get("output_mode", "files_with_matches") if name == "Grep" else None
        if name == "Grep" and mode != "files_with_matches":
            b.detail(_d(str(mode)))
        b.flags(args, "-i", "-n", "-o", "multiline")
        if name == "Grep" and isinstance(mode, str):
            b.took.add("output_mode")
        flags = [k for k in ("-i", "-n", "-o", "multiline") if args.get(k)]
        numbers = [k for k in ("-A", "-B", "-C", "context", "head_limit") if k in args]
        flags += [f"{k} {args[k]}" for k in numbers]
        b.took.update(numbers)
        if flags:
            b.detail(" ".join(_d(f) for f in flags))
        pattern = _as_text(args.get("pattern", ""))
        head = [b.value("pattern", lambda: b.mono(f"/{pattern}/" if name == "Grep" else pattern))]
        if failed:
            body, more = _cut_rows(range(len(lines)), SHOWN[name], b.out_row("danger"))
        elif result is not None:
            found = _kept(
                lines, lambda ln: bool(ln) and not ln.startswith(("Found ", "No files found"))
            )
            # An empty line holds nothing to draw. The CLI's count is the caption's
            # only where it is the count of the lines drawn under it; "no matches"
            # says a "No files found" that is all there is.
            b.said.update(n for n, ln in enumerate(lines) if not ln)
            for n, ln in enumerate(lines):
                count = _FOUND.match(ln)
                if count and int(count[1]) == len(found):
                    b.said.add(n)
            if lines and lines[0] == "No files found":
                b.caption.append(("caption", "warn", "no matches"))
                if not found:
                    b.said.add(0)
            elif mode == "content":
                b.caption.append(("caption", "text", _count(len(found), "line")))
            else:
                b.caption.append(("caption", "text", _count(len(found), "file")))

            def hit(n: int) -> Row:
                ln = lines[n]
                where, sep, rest = ln.partition(":")
                line_no, sep2, text = rest.partition(":")
                if mode == "content" and sep and sep2 and line_no.isdigit():
                    return (
                        ("mono", "accent", b.cut(f"{where}:{line_no}")),
                        ("mono", "dim", "  " + b.cut(text.strip())),
                    )
                return b.mono(ln, "text")

            unit = "match" if mode == "content" else "file"
            body, more = _cut_rows(
                found, SHOWN[name], lambda n: b.out_line(n, lambda: hit(n)), what=unit
            )
    elif name == "WebFetch":
        b.kind("fetch")
        head = [
            b.value("url", lambda: b.url_row(_as_text(args.get("url", "")))),
            b.value("prompt", lambda: (("prose", "text", _d(_as_text(args.get("prompt", "")))),)),
        ]
        if failed:
            body, more = _cut_rows(range(len(lines)), SHOWN["WebFetch"], b.out_row("danger"))
        elif (target := _redirect_target(output)) is not None:
            # A cross-host redirect is not fetched, and the result names its target
            # as the server gave it; a same-host one is followed and not named.
            badge = "redirect · not fetched"
            b.short = True
            body = [
                (("caption", "warn", "redirects to (server-supplied, unverified)"),),
                b.url_row(target),
            ]
        else:
            # A paragraph a row; the empty lines between them hold nothing to draw.
            prose = _kept(lines)
            b.said.update(set(range(len(lines))) - set(prose))
            body, more = _cut_rows(
                prose,
                SHOWN["WebFetch"],
                lambda n: b.out_line(n, lambda: (("prose", "dim", _d(lines[n])),)),
                what="paragraph",
            )
    elif name == "WebSearch":
        b.kind("search")
        for key, word in (("allowed_domains", "only"), ("blocked_domains", "not")):
            if args.get(key):
                b.detail(f"{word} {_d(', '.join(map(str, args[key])))}")
            value = args.get(key)
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                b.took.add(key)
        head = [
            b.value(
                "query", lambda: (("prose", "text", f"“{_d(_as_text(args.get('query', '')))}”"),)
            )
        ]
        if failed:
            body, more = _cut_rows(range(len(lines)), SHOWN["WebSearch"], b.out_row("danger"))
        elif result is not None:
            links: list[Any] = []
            for ln in lines:
                if ln.startswith("Links: "):
                    try:
                        found_links = json.loads(ln[len("Links: ") :])
                    except ValueError:
                        found_links = []
                    if isinstance(found_links, list):
                        links += [x for x in found_links if isinstance(x, dict)]
            b.caption.append(("caption", "text", _count(len(links), "result")))
            # The rest of the result -- the CLI's own wording around the links --
            # is in the viewer.
            b.short = True
            for link in links[: SHOWN["WebSearch"]]:
                body.append((("prose", "text", b.one_line(link.get("title", ""), 80)),))
                body.append(b.url_row(_as_text(link.get("url", ""))))
            if len(links) > SHOWN["WebSearch"]:
                more = _count(len(links) - SHOWN["WebSearch"], "more result")
    elif name in ("Agent", "Task"):
        b.kind("agent")
        b.detail(_d(args.get("subagent_type", "general-purpose")))
        for key in ("subagent_type", "model", "effort", "isolation"):
            if key in args:
                if key != "subagent_type":
                    b.detail(_d(args[key]))
                b.took.add(key)
        if args.get("run_in_background"):
            b.detail("background")
        b.flags(args, "run_in_background")
        prompt = _as_text(args.get("prompt", ""))
        head = [
            b.value(
                "description",
                lambda: (("prose", "text", _d(_as_text(args.get("description", "")))),),
            ),
            b.value(
                "prompt",
                lambda: (
                    ("mono", "dim", "› "),
                    ("caption", "dim", b.one_line(prompt, 110)),
                    ("caption", "dim", f"  {_count(len(prompt.splitlines()), 'line')} of prompt"),
                ),
            ),
        ]
        text = "\n".join(_read_out(b, output))
        if text.startswith("Async agent launched"):
            badge = "launched · runs on"
            b.short = True
        else:
            usage = _USAGE.search(text)
            if usage:
                b.detail(f"{int(usage[2])} tool uses · {int(usage[3]) / 1000:.0f} s")
                if "agentId: " in text:
                    text = text[: text.find("agentId: ")].rstrip()
                    b.short = True
            ink = "danger" if failed else "dim"
            b.out = report = text.splitlines()
            # A paragraph a row; the empty lines between them hold nothing to draw.
            kept = _kept(report)
            b.said.update(set(range(len(report))) - set(kept))
            body, more = _cut_rows(
                kept,
                SHOWN["Agent"],
                lambda n: b.out_line(n, lambda: (("prose", ink, _d(report[n])),)),
                after=" of its report",
            )
    elif name == "TodoWrite":
        b.kind("todos")
        held_todos = args.get("todos", [])
        todos = (
            [t for t in held_todos if isinstance(t, dict)] if isinstance(held_todos, list) else []
        )
        if isinstance(held_todos, list) and all(map(_one_text, held_todos)):
            b.took.add("todos")
        counts = {
            s: sum(1 for t in todos if t.get("status") == s)
            for s in ("completed", "in_progress", "pending")
        }
        b.detail(
            f"{counts['completed']} done · {counts['in_progress']} doing ·"
            f" {counts['pending']} to do"
        )

        def item(t: Mapping[str, Any]) -> Row:
            status = t.get("status")
            if status == "completed":
                return (
                    ("mono", "ok", "[x] "),
                    ("prose", "dim", _d(_as_text(t.get("content", "")))),
                )
            if status == "in_progress":
                return (
                    ("mono", "accent", "[>] "),
                    ("prose", "text", _d(_as_text(t.get("activeForm", "")))),
                )
            return (("mono", "dim", "[ ] "), ("prose", "text", _d(_as_text(t.get("content", "")))))

        body, more = _cut_rows(todos, SHOWN["TodoWrite"], item, what="item")
        if failed:
            body = body + b.out_rows(2, "danger")
            b.short = b.short or len(lines) > 2
    elif name == "NotebookEdit":
        b.kind("notebook")
        mode = _as_text(args.get("edit_mode", "replace"))
        cell = args.get("cell_id")
        b.detail(f"{_d(mode)} cell {_d(cell)}" if cell is not None else f"{_d(mode)} at the top")
        if "edit_mode" in args:
            b.took.add("edit_mode")
        if cell is not None:
            b.took.add("cell_id")
        if "cell_type" in args:
            b.detail(_d(args["cell_type"]))
            b.took.add("cell_type")
        head = [b.value("notebook_path", lambda: b.mono(_as_text(args.get("notebook_path", ""))))]
        ink = "del" if mode == "delete" else "add"
        if failed:
            # The error in place of the source: the source is in the viewer.
            body = b.out_rows(2, "danger")
            b.short = b.short or len(lines) > 2
        elif mode != "delete":
            source = b.value_lines("new_source")
            body, more = _cut_rows(
                range(len(source)),
                SHOWN["NotebookEdit"],
                lambda n: b.value_line("new_source", n, lambda: b.mono(source[n], ink)),
            )
    elif (parts := _mcp_parts(name)) is not None:
        server, tool = parts
        b.caption.append(("caption", "accent", f"mcp · {_d(server)}"))
        head = [(("prose", "text", _d(tool)),), *b.kv_rows()]
        ink = "danger" if failed else "dim"
        b.out = _read_out(b, output)
        body, more = _cut_rows(range(len(b.out)), SHOWN["mcp"], b.out_row(ink))
    else:
        b.caption.append(("caption", "warn", _d(name)))
        b.detail("no block of its own")
        head = b.kv_rows() or [(("mono", "dim", "(no arguments)"),)]
        ink = "danger" if failed else "dim"
        b.out = _read_out(b, output)
        body, more = _cut_rows(range(len(b.out)), SHOWN["other"], b.out_row(ink))

    b.caption.append(_badge(state, badge))
    if result is not None and result.dropped_bytes:
        more = (more or SHOW_ALL) + f" · {result.dropped_bytes:,} B past the 1 MiB cap"
    if not more and (b.short or _leaves_out(entry, b)):
        more = SHOW_ALL
    return Layout(
        tool=_d(name),
        state=state,
        caption=tuple(b.caption),
        head=tuple(head),
        body=tuple(body),
        more=more,
        tail=tail and bool(body),
        shown=_shown(entry, b),
    )


def _leaves_out(entry: ToolEntry, b: _Builder) -> bool:
    """
    Whether the block draws less than ``entry`` holds, by the builder's account
    of what its rows drew: an input key with a line no row drew and not said in a
    form of its own (``_Builder.took``), or an output line likewise
    (``_Builder.said``).

    The layout's own count of what it cut is not trusted for this: a tool that
    leaves out a field, or the whole of a result, without saying so is the case
    this exists for.
    """
    for key, value in (entry.input or {}).items():
        if key in b.took:
            continue
        if any((key, n) not in b.drew for n in range(_line_count(value))):
            return True
    if entry.result is None:
        return False
    return any(n not in b.said and (None, n) not in b.drew for n in range(len(b.out)))


def _shown(entry: ToolEntry, b: _Builder) -> frozenset[Where]:
    """
    The viewer's lines a row draws whole. An output line counts only where the
    lines the layout read are the output's own lines, in place: an error unwrapped
    or text read back out of content blocks is not, and none of it is counted.
    """
    shown = {where for where in b.drew if where[0] is not None}
    out = {where for where in b.drew if where[0] is None}
    if out and entry.result is not None:
        held = entry.result.output.split("\n", len(b.out))
        if held[: len(b.out)] == b.out:
            shown |= out
    return frozenset(shown)


def _chunks(text: str) -> Iterator[tuple[int, str]]:
    """
    ``text``'s lines, each in pieces of at most ``LINE_CUT`` characters, every
    piece drawable and with the number of the line it is of: what the viewer
    draws, so every character of ``text`` is in exactly one piece and no piece is
    a line too long to draw.
    """
    for n, line in enumerate(text.split("\n")):
        line = line.expandtabs(8)
        if not line:
            yield n, ""
            continue
        for start in range(0, len(line), LINE_CUT):
            yield n, _d(line[start : start + LINE_CUT])


def viewer_rows(entry: ToolEntry) -> tuple[Row, ...]:
    """
    Everything ``entry`` holds, for the viewer: each input field by name, then the
    whole output, in rows of one line each (``_chunks``).

    One row a line so the viewer can be drawn through a clipper whatever the
    size; built once for an entry and kept by the caller.
    """
    return _viewer_of(entry)[0]


def _viewer_of(entry: ToolEntry) -> tuple[tuple[Row, ...], tuple[Where | None, ...]]:
    """
    ``viewer_rows``, and for each row the line of the entry's own text it holds,
    or ``None`` for a label.
    """
    rows: list[Row] = []
    held: list[Where | None] = []

    def label(row: Row) -> None:
        rows.append(row)
        held.append(None)

    def content(row: Row, where: Where) -> None:
        rows.append(row)
        held.append(where)

    args: Mapping[str, Any] = entry.input or {}
    if entry.input is None:
        label((("mono", "dim", "(the call is not in this transcript)"),))
    for key, value in args.items():
        label((("mono", "accent", f"{_d(key)}:"),))
        for n, piece in _chunks(_as_text(value)):
            content((("mono", "text", "  " + piece),), (key, n))
    result = entry.result
    if result is None:
        label((("mono", "dim", "(no result)"),))
        return tuple(rows), tuple(held)
    said = f"output · {len(result.output.encode('utf-8', 'surrogatepass')):,} B"
    if result.dropped_bytes:
        said += f" · {result.dropped_bytes:,} B past the 1 MiB cap were not kept"
    label((("mono", "danger" if result.is_error else "accent", said),))
    blocks = _blocks(result.output)
    if blocks is not None and not _all_text(blocks):
        # What a block that is not text is -- an image is base64 in the ``repr``
        # below -- said before the ``repr`` that holds it.
        label((("mono", "dim", f"  {_count(len(blocks), 'content block')}:"),))
        for n, block in enumerate(blocks):
            label((("mono", "text", f"  {n + 1}. {_d(block_summary(block))}"),))
    ink = "danger" if result.is_error else "dim"
    for n, piece in _chunks(result.output):
        content((("mono", ink, piece),), (None, n))
    return tuple(rows), tuple(held)


@dataclass(frozen=True, slots=True)
class Hit:
    """
    One match of a find in a block: the viewer row it is in, and whether the
    collapsed block draws that row's text, so that bringing it into view needs no
    expansion.
    """

    row: int
    shown: bool


def find(entry: ToolEntry, block: Layout, needle: str) -> list[Hit]:
    """
    Every match of ``needle`` in what ``entry`` holds, ignoring case, in viewer
    order, once for each time a row holds it: the input values and the output,
    and not the viewer's labels or the block's own wording (a caption, ``$``).

    Searched in the viewer's rows, which hold every character of the entry once
    (``_chunks``), so a match is something the viewer can scroll to. A match is
    ``shown`` when the collapsed block has a row that draws its line whole
    (``Layout.shown``); a line drawn cut, or said only in the caption, is not. A
    needle across two of a long line's ``LINE_CUT`` pieces is not found. The rows
    are built only for an entry that holds the needle.
    """
    if not needle:
        return []
    wanted = needle.casefold()
    values = [_as_text(v) for v in (entry.input or {}).values()]
    if entry.result is not None:
        values.append(entry.result.output)
    if not any(wanted in _d(value).casefold() for value in values):
        return []
    rows, held = _viewer_of(entry)
    hits: list[Hit] = []
    for number, (row, where) in enumerate(zip(rows, held, strict=True)):
        if where is None:
            continue
        text = "".join(span[2] for span in row)
        count = text.casefold().count(wanted)
        if count:
            hits += [Hit(number, where in block.shown)] * count
    return hits


def strings(block: Layout) -> Iterator[str]:
    """Every string of ``block`` the drawing code can hand to ImGui."""
    yield block.tool
    yield block.more
    for _, _, text in block.caption:
        yield text
    for row in (*block.head, *block.body):
        for _, _, text in row:
            yield text


# -- drawing --------------------------------------------------------------------------

VIEWER_ROWS = 16
"""How many rows the viewer's well is tall before it scrolls."""

COLLAPSE = "collapse"
_RULE = 3.0
_ROUNDING = 5.0
_PADDING = (10.0, 6.0)
_SPACING = (8.0, 2.0)


def _ink(name: str) -> Color:
    """A span's ink as a block draws it: a role's, at 4.5:1 on the terminal ground."""
    if name == "text":
        return P.terminal_text
    if name == "dim":
        return P.terminal_dim
    role = {
        "add": P.diff_add,
        "del": P.diff_remove,
        "ctx": P.diff_context,
        "accent": P.accent,
        "link": P.accent,
        "ok": P.ok,
        "warn": P.warn,
        "danger": P.danger,
    }[name]
    return terminal_ink(role)


def link_inks() -> LinkInks:
    """
    The inks of a link inside a block (``widgets.link``): the accent as the block
    draws its links (``_ink("link")``), on the terminal ground, moving toward the
    block's text, with the block's dim ink for a disabled one.
    """
    return theme_link_inks(_ink("link"), (P.terminal_bg,), P.terminal_text, P.terminal_dim)


def _affordance(more: str) -> bool:
    """
    The block's footer: what is left out, in the block's dim ink, then ``show
    all`` as a link, on the id the button it replaced had (``more`` and
    ``##more``). One text line, on that line's baseline.
    """
    from . import widgets

    said = more.removeprefix(SHOW_ALL).removeprefix(" · ")
    if said:
        imgui.text_colored(P.terminal_dim.vec4, f"{said} ·")
        imgui.same_line()
    return widgets.link(SHOW_ALL, inks=link_inks(), ident=f"{more}##more")


@dataclass(frozen=True, slots=True)
class Faces:
    """
    The faces a block is drawn in, as context managers that push them: the code
    face, the prose face, and the prose face at caption size. The caller's, so
    the log's typeset faces are used where the library has them and the UI
    font where it has not.
    """

    mono: Callable[[], Any]
    prose: Callable[[], Any]
    caption: Callable[[], Any]


def _span(faces: Faces, face: str, colour: Color, text: str, wrap: bool) -> None:
    with getattr(faces, face)():
        if face == "mono" or not wrap:
            imgui.text_colored(colour.vec4, text)
        else:
            imgui.push_style_color(imgui.Col_.text, colour.vec4)
            imgui.text_wrapped(text)
            imgui.pop_style_color()


def _row(faces: Faces, row: Row) -> None:
    """
    One row of spans on one line. A ``tip`` span is not drawn: it underlines the
    row in its ink, and is the row's tooltip.
    """
    drawn = [span for span in row if span[0] != "tip"]
    tip = next((span for span in row if span[0] == "tip"), None)
    imgui.begin_group()
    for n, (face, ink, text) in enumerate(drawn):
        if n:
            imgui.same_line(0.0, 0.0)
        _span(faces, face, _ink(ink), text, wrap=len(drawn) == 1 and tip is None)
    imgui.end_group()
    if tip is not None:
        low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        imgui.get_window_draw_list().add_line(
            imgui.ImVec2(low.x, high.y), imgui.ImVec2(high.x, high.y), _ink(tip[1]).u32, 1.0
        )
        imgui.set_item_tooltip(tip[2])


def _caption(faces: Faces, spans: Sequence[Span]) -> None:
    for n, (face, ink, text) in enumerate(spans):
        if n:
            imgui.same_line(0.0, 0.0)
            _span(faces, face, P.terminal_dim, " · ", wrap=False)
            imgui.same_line(0.0, 0.0)
        _span(faces, face, _ink(ink), text, wrap=False)


def _viewer(faces: Faces, rows: Sequence[Row], mark: Mark | None = None) -> None:
    """
    Every row in a well of its own, ``VIEWER_ROWS`` tall at most, scrolling both
    ways, and drawn through a clipper: only the rows in sight are handed over.
    ``mark``'s row is marked, and scrolled to where it asks.
    """
    with faces.mono():
        pitch = imgui.get_text_line_height_with_spacing()
    tall = min(len(rows), VIEWER_ROWS) * pitch + imgui.get_style().scrollbar_size
    imgui.begin_child(
        "##viewer", imgui.ImVec2(0.0, tall), 0, imgui.WindowFlags_.horizontal_scrollbar.value
    )
    if mark is not None and mark.reveal and mark.row is not None:
        imgui.set_scroll_y(max(0.0, mark.row * pitch - tall / 3.0))
    clipper = imgui.ListClipper()
    clipper.begin(len(rows), pitch)
    if mark is not None and mark.row is not None:
        clipper.include_item_by_index(mark.row)
    while clipper.step():
        for index in range(clipper.display_start, clipper.display_end):
            _row(faces, rows[index])
            if mark is not None and index == mark.row:
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                right = imgui.get_window_pos().x + imgui.get_window_size().x
                imgui.get_window_draw_list().add_rect_filled(
                    low, imgui.ImVec2(right, high.y), mark.ink
                )
    clipper.end()
    imgui.end_child()


@dataclass(frozen=True, slots=True)
class Mark:
    """
    A find's mark on a block: ``ink`` (an ImU32) outlines the block, and fills
    the viewer row ``row`` where there is one; ``reveal`` scrolls the viewer to
    that row this frame.
    """

    ink: int
    row: int | None = None
    reveal: bool = False


def draw(
    block: Layout,
    *,
    key: str,
    faces: Faces,
    expanded: bool = False,
    viewer: Callable[[], Sequence[Row]] | None = None,
    mark: Mark | None = None,
) -> bool:
    """
    One block at the cursor, as one item: a child the width of the window and as
    tall as what it holds. ``expanded`` draws the viewer (``viewer()``'s rows) in
    place of the body. ``mark`` is a find's (``Mark``). Returns whether the
    affordance was pressed this frame: the caller keeps what is expanded.
    """
    from . import widgets

    outline = P.terminal_outline
    imgui.push_id(key)
    imgui.push_style_color(imgui.Col_.child_bg, P.terminal_bg.vec4)
    imgui.push_style_color(imgui.Col_.border, P.border.vec4)
    imgui.push_style_var(imgui.StyleVar_.child_rounding, _ROUNDING)
    imgui.push_style_var(imgui.StyleVar_.child_border_size, 1.0 if outline else 0.0)
    imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(*_PADDING))
    flags = imgui.ChildFlags_.always_use_window_padding | imgui.ChildFlags_.auto_resize_y
    if outline:
        flags |= imgui.ChildFlags_.borders
    pressed = False
    imgui.begin_child(
        "##tool", imgui.ImVec2(0.0, 0.0), flags, imgui.WindowFlags_.horizontal_scrollbar.value
    )
    # Inside the block only: the block itself is spaced from what follows it as
    # every item of the log is.
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(*_SPACING))
    try:
        if block.state is State.ERROR:
            low = imgui.get_window_pos()
            high = imgui.ImVec2(low.x + _RULE, low.y + imgui.get_window_height())
            imgui.get_window_draw_list().add_rect_filled(low, high, P.danger.u32, _ROUNDING)
        _caption(faces, block.caption)
        for row in block.head:
            _row(faces, row)
        if block.body or block.more:
            imgui.dummy(imgui.ImVec2(0.0, 2.0))
        if expanded and viewer is not None:
            pressed = widgets.link(COLLAPSE, inks=link_inks(), ident=f"{COLLAPSE}##more")
            _viewer(faces, viewer(), mark)
        else:
            if block.more and block.tail:
                pressed = _affordance(block.more)
            for row in block.body:
                _row(faces, row)
            if block.more and not block.tail:
                pressed = _affordance(block.more)
    finally:
        imgui.pop_style_var()
        imgui.end_child()
        imgui.pop_style_var(3)
        imgui.pop_style_color(2)
        imgui.pop_id()
    if mark is not None:
        imgui.get_window_draw_list().add_rect(
            imgui.get_item_rect_min(), imgui.get_item_rect_max(), mark.ink, _ROUNDING, 2.0
        )
    return pressed
