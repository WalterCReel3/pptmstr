#!/usr/bin/env python3
"""
Draw variants of the terminal-style tool block from real ``ToolEntry`` records,
for the operator to pick one (planning/2026-10-15-tool-block-variants.md).

Nothing here changes what the app draws. The cases are tool calls and results
written into a ``Transcript`` through ``append_tool_call`` and
``append_tool_result`` -- the calls the driver's translator makes -- and every
block is built from the entry ``Transcript.tool_entry`` hands back. The sheets
are drawn over the running ``--fake`` app under the operator's picked style,
which is laid over ``theme.apply_style`` in this process only; ``pptmstr/`` and
``scripts/style_probe.py`` are not edited.

    scripts/tool_block_probe.py --all        # every sheet, on its own Xvfb
    scripts/tool_block_probe.py --render     # likewise, on the display there is
    scripts/tool_block_probe.py --describe   # every variant's values, as text

Writes ``shots/tool-block/<variant>.png`` and ``shots/tool-block/viewer-*.png``.

**Verbatim in, drawable out.** ``ToolEntry.input``, ``ToolEntry.name`` and
``ToolResult.output`` are verbatim (tool-entries' result): a lone surrogate or a
NUL in them is the tool's own. ``build`` passes every string it takes from an
entry through ``drawable.text`` before it reaches a ``Block``, and every text the
drawing code hands ImGui goes through ``_say``, which refuses a string that is
not drawable. ``tests/test_tool_block_probe.py`` holds both.

**``result is None`` is not "running".** An interrupted turn or a call whose
result landed in a sub-agent's buffer leaves it ``None`` for good
(rv-tool-entries, finding 2). A block is told which by ``live`` -- whether the
turn that made the call is still going -- which a real renderer has to read from
the node's state; the entry cannot say.
"""

from __future__ import annotations

import argparse
import difflib
import functools
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pptmstr import drawable  # noqa: E402
from pptmstr.transcript import ToolEntry, Transcript  # noqa: E402

OUT = ROOT / "shots" / "tool-block"
SCENARIO = "tool-block"
SHEET_SIZE = (1500, 1400)


# -- the operator's pick, applied here until style-preview's "picked" lands -------
#
# planning/2026-10-15-ui-style-probe.md, "The operator's pick": round two's base is
# soft, and the operator kept soft's values except where they named another. Every
# value soft sets is listed so the sheet says what is in force.
PICKED_SCALARS: tuple[tuple[str, float | tuple[float, float]], ...] = (
    ("scrollbar_size", 16.0),
    ("scrollbar_rounding", 8.0),
    ("scrollbar_padding", 3.0),
    ("grab_rounding", 5.0),
    ("frame_rounding", 5.0),  # buttons: rounding 5
    ("frame_padding", (9.0, 6.0)),  # buttons: frame_padding 9x6
    ("frame_border_size", 0.0),  # buttons "no frame border" and lines "fill only"
    ("item_spacing", (8.0, 6.0)),
    ("tab_rounding", 5.0),
    ("popup_border_size", 0.0),  # dropdown: no outline
    ("popup_rounding", 4.0),  # dropdown: popup rounding 4
)
# (slot, base role, over role, alpha), as theme.mixed makes them.
PICKED_COLORS: tuple[tuple[str, str, str, float], ...] = (
    ("button", "panel_alt", "accent", 0.20),
    ("button_hovered", "panel_alt", "accent", 0.36),
    ("button_active", "panel_alt", "accent", 0.52),
    ("frame_bg", "bg", "panel", 0.35),
    ("scrollbar_grab", "bg", "text_dim", 0.45),
)


def apply_picked() -> list[tuple[str, Any]]:
    """Lay the pick over the style in force; returns what it replaced. Needs a context."""
    from imgui_bundle import imgui

    from pptmstr import theme

    style = imgui.get_style()
    saved: list[tuple[str, Any]] = []
    for name, value in PICKED_SCALARS:
        was = getattr(style, name)
        saved.append((name, (was.x, was.y) if hasattr(was, "x") else float(was)))
        setattr(style, name, imgui.ImVec2(*value) if isinstance(value, tuple) else value)
    palette = theme.current()
    for slot, base, over, alpha in PICKED_COLORS:
        idx = int(getattr(imgui.Col_, slot))
        was = style.color_(idx)
        saved.append((f"Col_.{slot}", (was.x, was.y, was.z, was.w)))
        style.set_color_(idx, theme.mixed(getattr(palette, base), getattr(palette, over), alpha))
    return saved


def picked_label() -> str:
    parts = [
        f"{n} {v[0]:g}x{v[1]:g}" if isinstance(v, tuple) else f"{n} {v:g}"
        for n, v in PICKED_SCALARS
    ]
    parts += [f"Col_.{s} = mix({b}, {o}, {a:g})" for s, b, o, a in PICKED_COLORS]
    return " · ".join(parts)


# -- the variants -------------------------------------------------------------------

NEAR_BLACK = 0x0B0B0C  # round one's mock-up ground


@dataclass(frozen=True, slots=True)
class BlockStyle:
    """
    One way to draw a tool block. Every field is shown on the sheet as written.

    ``header``: ``"stacked"`` is a caption line in small variable-width type over
    the command in monospace; ``"one-line"`` puts the caption after the command.
    ``well``: the hex fill of the block's ground, or ``None`` for the pane's own.
    ``output``: ``"inside"`` the well with the header, or ``"below"`` it, the
    header on the pane. ``shown``: output lines drawn before the expand
    affordance. ``more``: ``"button"`` (``imgui.button`` at the pick's 9x6),
    ``"small_button"`` or ``"link"`` (``imgui.text_link``). ``error``: how a
    failed call reads -- ``"rule"`` (a bar down the left edge), ``"tint"`` (the
    well toward ``danger``) or ``"gutter"`` (the output's left rule in
    ``danger``).
    """

    name: str
    summary: str
    header: str
    well: int | None
    padding: tuple[float, float]
    rounding: float
    output: str
    shown: int
    more: str
    error: str
    caption_scale: float = 0.8
    gutter: float = 0.0
    # Which end of the output is kept: "head" (the first lines, the affordance
    # after them) or "tail" (the last, the affordance before them).
    cut: str = "head"

    def values(self) -> list[str]:
        well = "pane's own fill" if self.well is None else f"#{self.well:06x}"
        lines = [
            f"header {self.header}",
            f"well {well}",
            f"padding {self.padding[0]:g}x{self.padding[1]:g}",
            f"rounding {self.rounding:g}",
            f"output {self.output} the well" if self.well is not None else "output on the pane",
            (
                f"first {self.shown} output lines, then {self.more}"
                if self.cut == "head"
                else f"{self.more}, then the last {self.shown} output lines"
            ),
            f"error as {self.error}",
            f"caption {self.caption_scale:g}x the UI size",
        ]
        if self.gutter:
            lines.append(
                f"output indent {self.gutter:g} px with a 2 px rule in mix(bg, text_dim, 0.5)"
            )
        return lines


STACKED = BlockStyle(
    "stacked",
    "round one's mock-up made real: caption over the command, all in one near-black well",
    header="stacked",
    well=NEAR_BLACK,
    padding=(10.0, 6.0),
    rounding=5.0,
    output="inside",
    shown=6,
    more="button",
    error="rule",
)
ONE_LINE = BlockStyle(
    "one-line",
    "the command and its caption on one line, a tighter well, a link to expand",
    header="one-line",
    well=NEAR_BLACK,
    padding=(8.0, 4.0),
    rounding=3.0,
    output="inside",
    shown=4,
    more="link",
    error="tint",
    cut="tail",
)
HEADER_ON_PANE = BlockStyle(
    "header-on-pane",
    "caption and command on the pane's fill, only the output in the near-black well",
    header="stacked",
    well=NEAR_BLACK,
    padding=(10.0, 6.0),
    rounding=5.0,
    output="below",
    shown=8,
    more="small_button",
    error="rule",
)
GUTTER = BlockStyle(
    "gutter",
    "no well: one header line, the output indented on the pane behind a thin rule",
    header="one-line",
    well=None,
    padding=(0.0, 2.0),
    rounding=0.0,
    output="inside",
    shown=3,
    more="link",
    error="gutter",
    gutter=12.0,
    cut="tail",
)
STYLES: tuple[BlockStyle, ...] = (STACKED, ONE_LINE, HEADER_ON_PANE, GUTTER)
BY_NAME = {s.name: s for s in STYLES}


# -- the cases: real entries, written the way the driver writes them ---------------


@dataclass(frozen=True, slots=True)
class Case:
    """
    One tool call as a transcript holds it. ``live`` is whether the turn that made
    it is still going, which only matters while ``entry.result`` is ``None``.
    """

    name: str
    entry: ToolEntry
    live: bool = False


def _pytest_verbose(n: int) -> str:
    lines = ["============================= test session starts =============================="]
    lines.append("platform linux -- Python 3.12.7, pytest-8.3.3, pluggy-1.5.0")
    lines.append(f"collected {n} items")
    lines.append("")
    for i in range(n):
        lines.append(
            f"tests/test_parse.py::test_field_{i:03d} PASSED{' ' * 20}[{(i + 1) * 100 // n:3d}%]"
        )
    lines.append(
        f"============================== {n} passed in 2.31s =============================="
    )
    return "\n".join(lines)


def _big_log() -> str:
    """A journal over the 1 MiB cap, so the entry records dropped bytes."""
    row = (
        "2026-10-15T09:{m:02d}:{s:02d}.{u:06d} pptmstr.bridge DEBUG frame {i}"
        " delivered to node lead\n"
    )
    rows = [row.format(m=(i // 60) % 60, s=i % 60, u=i * 37 % 10**6, i=i) for i in range(16000)]
    # A lone surrogate where a viewer's first screen shows it: a log holds bytes.
    rows[2] = rows[2].replace("lead", "le\udc80d")
    return "".join(rows)


_READ_LINES = (
    "def checksum(line: str) -> int:",
    '    """The TLE checksum: digits count, a minus counts one, mod 10."""',
    "    total = 0",
    "    for ch in line[:68]:",
    "        if ch.isdigit():",
    "            total += int(ch)",
    '        elif ch == "-":',
    "            total += 1",
    "    return total % 10",
    "",
    "",
    "_RAW = b'\\x89PNG' + bytes([0])  # binary\x00tail",
)


def build_cases() -> tuple[Transcript, tuple[Case, ...]]:
    """
    Every case, written into one ``Transcript`` in order, and read back as entries.

    The writes are the driver translator's: ``append_tool_call(id, name, input)``
    for a ``ToolUseBlock``, ``append_tool_result(id, name, content, is_error=...)``
    for a ``ToolResultBlock``, where ``content`` is what the SDK gave -- a string,
    or for an MCP tool a list of content blocks.
    """
    t = Transcript()
    calls: list[tuple[str, str, dict[str, Any], object, bool | None, bool]] = [
        # id, name, input, content (None: no result), is_error, live
        (
            "toolu_bash_short",
            "Bash",
            {
                "command": "git status --short",
                "description": "Show working tree status",
                "timeout": 120000,
            },
            " M pptmstr/ui/transcript_pane.py\n?? notes/caf\udc80.txt\n?? shots/",
            False,
            False,
        ),
        (
            "toolu_bash_long",
            "Bash",
            {
                "command": ".venv/bin/python -m pytest -v tests/test_parse.py",
                "description": "Run the parser's tests verbosely",
                "timeout": 600000,
            },
            _pytest_verbose(42),
            False,
            False,
        ),
        (
            "toolu_bash_capped",
            "Bash",
            {
                "command": "cat ~/.config/pptmstr/logs/bridge.log",
                "description": "Read the bridge's whole log",
            },
            _big_log(),
            False,
            False,
        ),
        (
            "toolu_bash_error",
            "Bash",
            {
                "command": "pytest -q tests/test_parse.py -k checksum",
                "description": "Run the checksum tests",
                "timeout": 120000,
            },
            "Exit code 1\n"
            "F.\n"
            "=================================== FAILURES ===================================\n"
            "________________________ test_checksum_counts_minus ________________________\n"
            ">       assert checksum(LINE_1) == 5\n"
            "E       AssertionError: assert 4 == 5\n"
            "tests/test_parse.py:41: AssertionError\n"
            "1 failed, 1 passed in 0.12s",
            True,
            False,
        ),
        (
            "toolu_edit",
            "Edit",
            {
                "file_path": "/home/wreel/src/tle/tle/parse.py",
                "old_string": "        if ch.isdigit():\n            total += int(ch)\n",
                "new_string": "        if ch.isdigit():\n            total += int(ch)\n"
                '        elif ch == "-":\n            total += 1\n',
            },
            "The file /home/wreel/src/tle/tle/parse.py has been updated. Here's the result of"
            " running `cat -n` on a snippet of the edited file:\n"
            + "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(_READ_LINES[3:9], 4)),
            False,
            False,
        ),
        (
            "toolu_read",
            "Read",
            {"file_path": "/home/wreel/src/tle/tle/parse.py", "offset": 1, "limit": 12},
            "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(_READ_LINES, 1)),
            False,
            False,
        ),
        (
            "toolu_webfetch",
            "WebFetch",
            {
                "url": "https://celestrak.org/columns/v04n03/#FAQ04",
                "prompt": "How is the TLE checksum computed, and does a minus sign count?",
            },
            "The checksum is the sum of all digits on the line modulo 10. Letters, blanks,"
            " periods and plus signs count as 0; each minus sign counts as 1.\n"
            "The last digit of each line is that checksum.",
            False,
            False,
        ),
        (
            "toolu_mcp",
            "mcp__pptmstr__read_board",
            {},
            [
                {
                    "type": "text",
                    "text": "board: 4 tasks\n"
                    "- tool-block-variants [claimed] builder-22\n"
                    "- style-preview [claimed] builder-18",
                }
            ],
            False,
            False,
        ),
        (
            "toolu_running",
            "Bash",
            {
                "command": "make test",
                "description": "Run the whole suite",
                "timeout": 600000,
                "run_in_background": False,
            },
            None,
            None,
            True,
        ),
        (
            "toolu_no_result",
            "Bash",
            {"command": "make shot-xvfb", "description": "Take the headless screenshot"},
            None,
            None,
            False,
        ),
    ]
    for tool_use_id, name, args, _content, _err, _live in calls:
        t.append_tool_call(tool_use_id, name, args)
        if _content is not None:
            t.append_tool_result(tool_use_id, name, _content, is_error=bool(_err))
    cases = []
    for tool_use_id, _name, _args, _content, _err, live in calls:
        entry = t.tool_entry(tool_use_id)
        assert entry is not None
        cases.append(Case(tool_use_id.removeprefix("toolu_").replace("_", "-"), entry, live))
    return t, tuple(cases)


# -- the block: what is drawn, built from one entry --------------------------------

# A drawn output line is cut here: a terminal block does not wrap, and a 1 MiB line
# (an MCP repr, a minified file) is not measured whole every frame.
LINE_CUT = 400
COMMAND_LINES = 3
_EXIT = re.compile(r"\AExit code (\d+)\n?")


@dataclass(frozen=True, slots=True)
class Block:
    """
    One block, every string drawable. ``intro`` rows are ``(ink, text, mono)``,
    ink one of ``"del"``, ``"add"``, ``"prose"``, ``"dim"``.
    """

    state: str  # "running", "done", "error", "no result"
    badge: str
    caption: str
    command: tuple[str, ...]
    intro: tuple[tuple[str, str, bool], ...]
    lines: tuple[str, ...]
    hidden: int
    total: int
    kept_bytes: int
    dropped_bytes: int
    facts: str
    more_label: str = ""


def state_of(entry: ToolEntry, live: bool) -> str:
    """What the block says of the call. ``result is None`` is running only while the turn is."""
    if entry.result is None:
        return "running" if live else "no result"
    return "error" if entry.result.is_error else "done"


def _d(value: object) -> str:
    """A verbatim value as text a text call can take: ``drawable.text`` of its str or repr."""
    return drawable.text(value if isinstance(value, str) else repr(value))


def _timeout(ms: object) -> str | None:
    if not isinstance(ms, int | float) or isinstance(ms, bool):
        return None
    if ms >= 60000 and ms % 60000 == 0:
        return f"timeout {int(ms) // 60000} min"
    return f"timeout {ms / 1000:g} s"


def _cut(line: str) -> str:
    return line if len(line) <= LINE_CUT else line[: LINE_CUT - 1] + "…"


def _mcp_parts(name: str) -> tuple[str, str] | None:
    if not name.startswith("mcp__"):
        return None
    server, _, tool = name[5:].partition("__")
    return server, tool or server


def build(entry: ToolEntry, live: bool, shown: int, cut: str = "head") -> Block:
    """
    The block for ``entry`` with at most ``shown`` output lines, every string made
    drawable here and nowhere else.
    """
    args: Mapping[str, Any] = entry.input or {}
    name = entry.name
    state = state_of(entry, live)
    caption: list[str] = []
    command: list[str] = []
    intro: list[tuple[str, str, bool]] = []
    output = "" if entry.result is None else entry.result.output
    exit_code: str | None = None
    if entry.result is not None and entry.result.is_error and name == "Bash":
        found = _EXIT.match(output)
        if found:
            exit_code = found[1]
            output = output[found.end() :]
    out_lines = output.splitlines()
    collapse_to: int | None = None

    if name == "Bash":
        if "description" in args:
            caption.append(_d(args["description"]))
        if (limit := _timeout(args.get("timeout"))) is not None:
            caption.append(limit)
        if args.get("run_in_background"):
            caption.append("background")
        command = [_d(line) for line in str(args.get("command", "")).splitlines() or [""]]
    elif name in ("Edit", "MultiEdit"):
        caption.append("edit")
        if args.get("replace_all"):
            caption.append("replace all")
        command = [_d(args.get("file_path", ""))]
        old = str(args.get("old_string", "")).splitlines()
        new = str(args.get("new_string", "")).splitlines()
        rows = [
            row
            for row in difflib.unified_diff(old, new, lineterm="", n=0)
            if not row.startswith(("---", "+++", "@@"))
        ]
        added = sum(1 for row in rows if row.startswith("+"))
        removed = sum(1 for row in rows if row.startswith("-"))
        caption.append(f"+{added} -{removed}")
        for row in rows[:shown]:
            intro.append(("add" if row.startswith("+") else "del", _d(_cut(row)), True))
        if len(rows) > shown:
            intro.append(("dim", f"{len(rows) - shown} more diff lines", False))
        # The diff says what was done; the CLI's confirmation is one line of it.
        collapse_to = 1
    elif name == "Read":
        caption.append("read")
        offset, limit = args.get("offset"), args.get("limit")
        if isinstance(offset, int) and isinstance(limit, int):
            caption.append(f"lines {offset}–{offset + limit - 1}")
        elif isinstance(offset, int):
            caption.append(f"from line {offset}")
        else:
            caption.append("whole file")
        command = [_d(args.get("file_path", ""))]
    elif name == "WebFetch":
        url = str(args.get("url", ""))
        caption.append("fetch")
        caption.append(_d(urlsplit(url).netloc or url))
        command = [_d(url)]
        intro.append(("prose", _d(args.get("prompt", "")), False))
    elif (parts := _mcp_parts(name)) is not None:
        caption.append(f"mcp · {_d(parts[0])}")
        compact = json.dumps(dict(args), ensure_ascii=False) if args else ""
        command = [_d(parts[1] + (f" {compact}" if compact else ""))]
    else:
        caption.append(_d(name.lower()))
        command = [_d(json.dumps(dict(args), ensure_ascii=False, default=repr))]

    if len(command) > COMMAND_LINES:
        rest = len(command) - COMMAND_LINES
        command = command[:COMMAND_LINES] + [f"… {rest} more command lines"]
    command = [_cut(line) for line in command]

    limit_lines = shown if collapse_to is None else collapse_to
    kept_lines = out_lines[:limit_lines] if cut == "head" else out_lines[-limit_lines:]
    drawn = tuple(_d(_cut(line.expandtabs(8))) for line in kept_lines)
    hidden = max(0, len(out_lines) - limit_lines)
    kept = 0 if entry.result is None else len(entry.result.output.encode("utf-8", "surrogatepass"))
    dropped = 0 if entry.result is None else entry.result.dropped_bytes

    badge = {
        "running": "running",
        "done": "done",
        "error": "error" if exit_code is None else f"error · exit {exit_code}",
        "no result": "no result · turn ended",
    }[state]
    more = ""
    if hidden:
        more = f"{hidden:,} {'more' if cut == 'head' else 'earlier'} lines"
        if collapse_to is not None:
            more = f"result: {more}"
        if dropped:
            more += f" · {dropped:,} B past the 1 MiB cap"
    facts = (
        f"{_d(entry.tool_use_id)} · name {_d(name)} · input keys"
        f" {_d(','.join(args)) if args else '(none)'} · "
        + (
            "result None"
            if entry.result is None
            else f"output {len(out_lines):,} lines / {kept:,} B kept / {dropped:,} B dropped"
            f" · is_error {entry.result.is_error}"
        )
    )
    return Block(
        state=state,
        badge=badge,
        caption=" · ".join(caption),
        command=tuple(command),
        intro=tuple(intro),
        lines=drawn,
        hidden=hidden,
        total=len(out_lines),
        kept_bytes=kept,
        dropped_bytes=dropped,
        facts=facts,
        more_label=more,
    )


def block_strings(block: Block) -> Iterator[str]:
    """Every string of ``block`` the drawing code can hand to ImGui."""
    yield block.badge
    yield block.caption
    yield block.more_label
    yield block.facts
    yield from block.command
    yield from (text for _, text, _ in block.intro)
    yield from block.lines


# -- drawing ------------------------------------------------------------------------


def _say(text: str) -> str:
    """The one door to ImGui for text: refuses what a text call cannot take."""
    if drawable.undrawable(text):
        raise ValueError(f"not drawable: {text!r}")
    return text


class _Fonts:
    """Which faces drew the sheet, as said on it."""

    caption = "UI font"
    mono = "UI font"


def _rgb(hex_rgb: int) -> Any:
    from pptmstr import theme

    return theme.col(hex_rgb).vec4


def _mix(a: Any, b: Any, alpha: float) -> Any:
    from imgui_bundle import imgui

    return imgui.ImVec4(
        a.x + (b.x - a.x) * alpha, a.y + (b.y - a.y) * alpha, a.z + (b.z - a.z) * alpha, 1.0
    )


GREY = (0.78, 0.78, 0.78)
DIM = (0.55, 0.55, 0.57)


def _caption_text(text: str, colour: Any, scale: float) -> None:
    """One line in the proportional face at ``scale`` x the UI size, or the UI font."""
    from imgui_bundle import imgui

    from pptmstr.ui import typeset

    size = imgui.get_font_size() * scale
    if typeset.face_font("PROSE") is not None:
        _Fonts.caption = f"typeset PROSE at {size:g} px"
        with typeset.font(typeset.Weight.REGULAR, size):
            imgui.text_colored(colour, _say(text))
    else:
        _Fonts.caption = f"UI font at {size:g} px (no typeset library)"
        imgui.push_font(None, size)
        imgui.text_colored(colour, _say(text))
        imgui.pop_font()


def _mono(text: str, colour: Any) -> None:
    from imgui_bundle import imgui

    from pptmstr.ui import typeset

    if typeset.face_font("CODE") is not None:
        _Fonts.mono = "typeset CODE (Inconsolata)"
        with typeset.font(typeset.Weight.CODE, imgui.get_font_size()):
            imgui.text_colored(colour, _say(text))
    else:
        _Fonts.mono = "UI font (Inconsolata)"
        imgui.text_colored(colour, _say(text))


def _prose(text: str, colour: Any) -> None:
    from imgui_bundle import imgui

    from pptmstr.ui import typeset

    if typeset.face_font("PROSE") is not None:
        with typeset.font(typeset.Weight.REGULAR, imgui.get_font_size() * 0.9):
            imgui.push_style_color(imgui.Col_.text, colour)
            imgui.text_wrapped(_say(text))
            imgui.pop_style_color()
    else:
        imgui.push_style_color(imgui.Col_.text, colour)
        imgui.text_wrapped(_say(text))
        imgui.pop_style_color()


def _badge(block: Block) -> tuple[str, Any]:
    from pptmstr import theme

    p = theme.P
    ink = {
        "running": p.accent,
        "done": p.text_dim,
        "error": p.danger,
        "no result": p.warn,
    }[block.state]
    return block.badge, ink.vec4


def _more(block: Block, style: BlockStyle, key: str) -> None:
    from imgui_bundle import imgui

    if not block.more_label:
        return
    label = _say(block.more_label)
    if style.more == "button":
        imgui.button(f"{label}##more-{key}")
    elif style.more == "small_button":
        imgui.small_button(f"{label}##more-{key}")
    else:
        imgui.text_link(f"{label}##more-{key}")


def _header(block: Block, style: BlockStyle) -> None:
    from imgui_bundle import imgui

    text, ink = _badge(block)
    grey, dim = imgui.ImVec4(*GREY, 1.0), imgui.ImVec4(*DIM, 1.0)
    if style.header == "stacked":
        _caption_text(block.caption, dim, style.caption_scale)
        imgui.same_line()
        _caption_text(f"  {text}", ink, style.caption_scale)
        for n, line in enumerate(block.command):
            _mono(("$ " if n == 0 else "  ") + line, grey)
    else:
        _mono(block.command[0], grey)
        imgui.same_line(0.0, 12.0)
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + imgui.get_font_size() * 0.1)
        _caption_text(block.caption, dim, style.caption_scale)
        imgui.same_line()
        _caption_text(f"  {text}", ink, style.caption_scale)
        for line in block.command[1:]:
            _mono(line, grey)


def _intro(block: Block) -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    p = theme.P
    for ink, text, mono in block.intro:
        colour = {
            "del": p.diff_remove.vec4,
            "add": p.diff_add.vec4,
            "prose": imgui.ImVec4(*GREY, 1.0),
            "dim": imgui.ImVec4(*DIM, 1.0),
        }[ink]
        if mono:
            _mono(text, colour)
        elif ink == "prose":
            _prose(text, colour)
        else:
            _caption_text(text, colour, 0.8)


def _output(block: Block) -> None:
    from imgui_bundle import imgui

    dim = imgui.ImVec4(*DIM, 1.0)
    for line in block.lines:
        _mono(line, dim)


def _body(block: Block, style: BlockStyle, key: str) -> None:
    """The output and its affordance, in the order the style's cut puts them."""
    if style.cut == "tail":
        _more(block, style, key)
        _output(block)
    else:
        _output(block)
        _more(block, style, key)


def _well_bg(style: BlockStyle, failed: bool) -> Any:
    from pptmstr import theme

    assert style.well is not None
    ground = _rgb(style.well)
    if failed and style.error == "tint":
        return _mix(ground, theme.P.danger.vec4, 0.14)
    return ground


def _begin_well(style: BlockStyle, failed: bool, key: str) -> None:
    from imgui_bundle import imgui

    imgui.push_style_color(imgui.Col_.child_bg, _well_bg(style, failed))
    imgui.push_style_var(imgui.StyleVar_.child_rounding, style.rounding)
    imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(*style.padding))
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(8.0, 2.0))
    imgui.begin_child(
        key,
        imgui.ImVec2(0.0, 0.0),
        imgui.ChildFlags_.always_use_window_padding | imgui.ChildFlags_.auto_resize_y,
    )
    if failed and style.error == "rule":
        lo = imgui.get_window_pos()
        hi = imgui.ImVec2(lo.x + 3.0, lo.y + imgui.get_window_height())
        from pptmstr import theme

        imgui.get_window_draw_list().add_rect_filled(lo, hi, theme.P.danger.u32, style.rounding)


def _end_well() -> None:
    from imgui_bundle import imgui

    imgui.end_child()
    imgui.pop_style_var(3)
    imgui.pop_style_color()


def draw_block(block: Block, style: BlockStyle, key: str) -> float:
    """One block under ``style``; returns its height in pixels."""
    from imgui_bundle import imgui

    from pptmstr import theme

    failed = block.state == "error"
    top = imgui.get_cursor_screen_pos().y
    imgui.begin_group()
    if style.well is None:
        imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(8.0, 2.0))
        _header(block, style)
        _intro(block)
        if block.lines or block.more_label:
            start = imgui.get_cursor_screen_pos()
            imgui.indent(style.gutter)
            imgui.begin_group()
            _body(block, style, key)
            imgui.end_group()
            imgui.unindent(style.gutter)
            end = imgui.get_item_rect_max().y
            # Half text_dim on bg: the palette's border is too faint to be seen as a rule.
            rule = imgui.color_convert_float4_to_u32(theme.mixed(theme.P.bg, theme.P.text_dim, 0.5))
            ink = theme.P.danger.u32 if failed else rule
            imgui.get_window_draw_list().add_rect_filled(
                imgui.ImVec2(start.x + 3.0, start.y), imgui.ImVec2(start.x + 5.0, end), ink
            )
        imgui.pop_style_var()
    elif style.output == "inside":
        _begin_well(style, failed, f"##well-{key}")
        _header(block, style)
        _intro(block)
        _body(block, style, key)
        _end_well()
    else:
        imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(8.0, 2.0))
        _header(block, style)
        _intro(block)
        imgui.pop_style_var()
        if block.lines or block.more_label:
            _begin_well(style, failed, f"##well-{key}")
            _body(block, style, key)
            _end_well()
    imgui.end_group()
    return float(imgui.get_item_rect_max().y - top)


# -- the sheets ---------------------------------------------------------------------


def _note(text: str, *, warn: bool = False) -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    colour = theme.P.warn.vec4 if warn else imgui.get_style().color_(int(imgui.Col_.text_disabled))
    imgui.push_style_color(imgui.Col_.text, colour)
    imgui.text_wrapped(_say(text))
    imgui.pop_style_color()


# Measured heights, by (variant, case), printed for the planning record.
HEIGHTS: dict[tuple[str, str], float] = {}


def _sheet_window(first: bool) -> bool:
    from imgui_bundle import imgui

    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.pos)
    imgui.set_next_window_size(viewport.size)
    if first:
        imgui.set_next_window_focus()
    flags = (
        imgui.WindowFlags_.no_decoration
        | imgui.WindowFlags_.no_saved_settings
        | imgui.WindowFlags_.no_move
        | imgui.WindowFlags_.no_docking
    )
    return bool(imgui.begin("##tool-block-sheet", None, flags))


def _sheet_header(title: str, lines: list[str]) -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    imgui.text(_say(title))
    _note(
        "MOCK: drawn by scripts/tool_block_probe.py over the --fake app, not by the app. Every"
        " block is built from a ToolEntry that Transcript.append_tool_call/append_tool_result"
        " wrote and Transcript.tool_entry returned.",
        warn=True,
    )
    _note(" · ".join(lines))
    _note(
        f"theme {theme.current().name} · picked style in force: {picked_label()}"
        f" · caption font: {_Fonts.caption} · monospace: {_Fonts.mono}"
    )
    imgui.spacing()


def draw_variant(style: BlockStyle, cases: tuple[Case, ...], first: bool) -> None:
    from imgui_bundle import imgui

    if _sheet_window(first):
        _sheet_header(f"tool block, variant '{style.name}': {style.summary}", style.values())
        flags = imgui.TableFlags_.sizing_stretch_same | imgui.TableFlags_.pad_outer_x
        if imgui.begin_table("##cases", 2, flags):
            for n, case in enumerate(cases):
                if n % 2 == 0:
                    imgui.table_next_row()
                imgui.table_next_column()
                block = build(case.entry, case.live, style.shown, style.cut)
                height = draw_block(block, style, f"{style.name}-{case.name}")
                HEIGHTS[(style.name, case.name)] = height
                _note(f"^ case {case.name} · {height:g} px · {block.facts}")
                imgui.spacing()
            imgui.end_table()
    imgui.end()


VIEWER_LINES = 14
WINDOW_LINES = 36


def _numbered(entry: ToolEntry, first: int, count: int, key: str) -> None:
    """A line-numbered monospace well over ``entry``'s output, in a scrolling child."""
    from imgui_bundle import imgui

    assert entry.result is not None
    lines = entry.result.output.splitlines()
    width = len(f"{len(lines):,}")
    imgui.push_style_color(imgui.Col_.child_bg, _rgb(NEAR_BLACK))
    imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(10.0, 6.0))
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(8.0, 2.0))
    # Exactly ``count`` rows tall; a few more are drawn so the child overflows and
    # shows the scrollbar the real viewer would have over the whole output.
    height = count * (imgui.get_font_size() + 2.0) - 2.0 + 12.0
    imgui.begin_child(key, imgui.ImVec2(0.0, height), imgui.ChildFlags_.always_use_window_padding)
    num = imgui.ImVec4(0.38, 0.38, 0.40, 1.0)
    for i, line in enumerate(lines[first : first + count + 4], first + 1):
        _mono(f"{i:>{width},}", num)
        imgui.same_line()
        _mono(_d(_cut(line.expandtabs(8))), imgui.ImVec4(*DIM, 1.0))
    imgui.end_child()
    imgui.pop_style_var(2)
    imgui.pop_style_color()


def _viewer_toolbar(entry: ToolEntry, key: str, count: int) -> None:
    from imgui_bundle import imgui

    assert entry.result is not None
    total = len(entry.result.output.splitlines())
    imgui.text(_say(f"lines 1–{count} of {total:,}"))
    imgui.same_line()
    imgui.set_next_item_width(220.0)
    imgui.input_text_with_hint(f"##find-{key}", "find", "")
    for label in ("wrap", "copy all", "save…"):
        imgui.same_line()
        imgui.button(f"{label}##{key}")
    if entry.result.truncated:
        _note(
            f"the entry holds {len(entry.result.output.encode('utf-8', 'surrogatepass')):,} B;"
            f" {entry.result.dropped_bytes:,} B past the 1 MiB cap were dropped where it was"
            " made, so copy and save give the kept part only",
            warn=True,
        )


def draw_viewer_inpane(cases: tuple[Case, ...], first: bool) -> None:
    """The output expanded in place: the block grows to a scrolling well with a toolbar."""
    from imgui_bundle import imgui

    style = STACKED
    by = {c.name: c for c in cases}
    if _sheet_window(first):
        _sheet_header(
            "output viewer, mock 1 of 2: expanded in the session pane",
            [
                "the block's expand affordance swaps its cut output for a fixed-height"
                f" scrolling well of {VIEWER_LINES} lines with line numbers, a find field and"
                " copy/save; 'collapse' puts it back. Blocks around it are the 'stacked'"
                " variant.",
            ],
        )
        imgui.begin_child("##session-mock", imgui.ImVec2(820.0, 0.0), imgui.ChildFlags_.borders)
        _note("SESSION (mock column)")
        for name in ("bash-short",):
            draw_block(build(by[name].entry, False, style.shown), style, f"vi-{name}")
            imgui.spacing()
        entry = by["bash-capped"].entry
        block = build(entry, False, style.shown)
        _begin_well(style, False, "##vi-open")
        _header(block, style)
        _viewer_toolbar(entry, "vi", VIEWER_LINES)
        _numbered(entry, 0, VIEWER_LINES, "##vi-lines")
        imgui.button("collapse##vi")
        _end_well()
        _note(f"^ {block.facts}")
        imgui.spacing()
        draw_block(build(by["bash-error"].entry, False, style.shown), style, "vi-error")
        imgui.end_child()
    imgui.end()


def draw_viewer_window(cases: tuple[Case, ...], first: bool) -> None:
    """A separate top-level window, drawn as a frame on the sheet: a mock, not a window."""
    from imgui_bundle import imgui

    from pptmstr import theme

    entry = {c.name: c for c in cases}["bash-capped"].entry
    assert entry.result is not None
    if _sheet_window(first):
        _sheet_header(
            "output viewer, mock 2 of 2: a separate top-level window",
            [
                "MOCK FRAME: the rectangle below stands for an OS window of its own (an ImGui"
                " multi-viewport window or a second hello_imgui window); no such window is"
                " opened. One per expanded output, titled by the call, the session pane left"
                " as it was.",
            ],
        )
        dl = imgui.get_window_draw_list()
        origin = imgui.get_cursor_screen_pos()
        x0, y0 = origin.x + 40.0, origin.y + 10.0
        w, h = 1100.0, 900.0
        title_h = 30.0
        dl.add_rect_filled(
            imgui.ImVec2(x0 + 6, y0 + 8),
            imgui.ImVec2(x0 + w + 6, y0 + h + 8),
            imgui.color_convert_float4_to_u32(imgui.ImVec4(0, 0, 0, 0.45)),
            8.0,
        )
        dl.add_rect_filled(imgui.ImVec2(x0, y0), imgui.ImVec2(x0 + w, y0 + h), theme.P.bg.u32, 8.0)
        dl.add_rect_filled(
            imgui.ImVec2(x0, y0),
            imgui.ImVec2(x0 + w, y0 + title_h),
            theme.P.panel_alt.u32,
            8.0,
            imgui.ImDrawFlags_.round_corners_top,
        )
        dl.add_rect(imgui.ImVec2(x0, y0), imgui.ImVec2(x0 + w, y0 + h), theme.P.border.u32, 8.0)
        command = _d(str((entry.input or {}).get("command", "")))
        dl.add_text(
            imgui.ImVec2(x0 + 12, y0 + 7),
            theme.P.text.u32,
            _say(f"output · {command} · report · pptmstr   (MOCK FRAME)"),
        )
        for i, c in enumerate((theme.P.danger, theme.P.warn, theme.P.ok)):
            cx = x0 + w - 20 - i * 20
            dl.add_circle_filled(imgui.ImVec2(cx, y0 + title_h / 2), 6.0, c.u32)
        imgui.set_cursor_screen_pos(imgui.ImVec2(x0 + 12, y0 + title_h + 10))
        imgui.begin_child("##vw-body", imgui.ImVec2(w - 24, h - title_h - 20))
        block = build(entry, False, STACKED.shown)
        _caption_text(block.caption, imgui.ImVec4(*DIM, 1.0), 0.8)
        _mono("$ " + block.command[0], imgui.ImVec4(*GREY, 1.0))
        _viewer_toolbar(entry, "vw", WINDOW_LINES)
        _numbered(entry, 0, WINDOW_LINES, "##vw-lines")
        _note(
            f"{block.total:,} lines · {block.kept_bytes:,} B kept · {block.dropped_bytes:,} B"
            f" dropped · entry {_d(entry.tool_use_id)}"
        )
        imgui.end_child()
    imgui.end()


SHEETS = (*(s.name for s in STYLES), "viewer-inpane", "viewer-window")


class _Shown:
    sheet: str | None = None
    first = False


_CASES: tuple[Case, ...] | None = None


def _cases() -> tuple[Case, ...]:
    global _CASES
    if _CASES is None:
        _CASES = build_cases()[1]
    return _CASES


def draw_sheet(name: str, first: bool) -> None:
    cases = _cases()
    if name in BY_NAME:
        draw_variant(BY_NAME[name], cases, first)
    elif name == "viewer-inpane":
        draw_viewer_inpane(cases, first)
    else:
        draw_viewer_window(cases, first)


# -- per-tool proofs: every tool in the stacked frame, on the theme's own well ------
#
# The operator: "bash has a certain look like others are distinct in other ways.
# Stacked for them but we need to proof them", "Errors getting a red rule is fine",
# "Theme dictates the 'terminal' background". Each tool keeps the stacked frame --
# a caption line in small proportional type, then its own head and body, in one
# well, a red rule down the left edge on error -- and differs by what it shows.
#
# The input field names are the CLI's, not guessed: the Agent SDK's TypeScript
# reference, "Tool Input Types" (code.claude.com/docs/en/agent-sdk/typescript),
# checked against the bundled CLI 2.1.301 in .venv. The result texts are the ones
# that CLI's ``mapToolResultToToolResultBlockParam`` makes; where none was found
# verbatim the case says so in ``Proof.note``. planning/2026-10-15-tool-block-
# variants.md, "Per-tool proofs", cites each.

PROOF_OUT = OUT / "proofs"
PROOF_THEMES: tuple[str, ...] = ("dark", "light", "xterm", "high_contrast")
PROOF_SHEET_SIZE = (1800, 2760)
# The CLI wraps a refusal it made itself (validation, permissions, unknown tool) in
# these, and strips them when it shows one; so does a block.
_TOOL_USE_ERROR = re.compile(r"\A\s*<tool_use_error>([\s\S]*)</tool_use_error>\s*\Z")


# Spans, rows and the block. A span is (face, ink, text): face "mono" (the code
# face at UI size), "prose" (proportional, UI size, wraps when alone on its row),
# "caption" (proportional, small); ink a name ``_ink`` resolves against the theme.
Span = tuple[str, str, str]
Row = tuple[Span, ...]
INKS = ("text", "dim", "num", "add", "del", "ctx", "accent", "link", "ok", "warn", "danger")


@dataclass(frozen=True, slots=True)
class Proof:
    """
    One tool's block, every string drawable. ``head`` is what the call asked,
    ``body`` what came back (or the call's own content, for a write); ``more`` the
    expand affordance's label, drawn before the body when ``tail`` (the last lines
    are kept) and after it otherwise.
    """

    tool: str
    state: str
    caption: tuple[Span, ...]
    head: tuple[Row, ...]
    body: tuple[Row, ...]
    more: str = ""
    tail: bool = False
    facts: str = ""


def _m(text: str, ink: str = "text") -> Row:
    return (("mono", ink, text),)


def _p(text: str, ink: str = "text") -> Row:
    return (("prose", ink, text),)


def _one_line(value: object, width: int) -> str:
    """``value`` on one drawn line of at most ``width`` characters, drawable."""
    text = " ".join(_d(value).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _unwrap_error(output: str) -> str:
    found = _TOOL_USE_ERROR.match(output)
    return found[1].strip() if found else output


def _blocks_text(output: str) -> str:
    """
    A list-of-content-blocks result's text. The transcript holds such a result as
    its ``repr`` (tool-entries' decision); this reads it back for the probe only.
    """
    import ast

    if not output.startswith("[{"):
        return output
    try:
        blocks = ast.literal_eval(output)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return output
    if not isinstance(blocks, list):
        return output
    texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(t for t in texts if isinstance(t, str)) or output


def _kb(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB"


def _cut_rows(
    rows: Sequence[Row], shown: int, *, tail: bool = False, what: str = "lines"
) -> tuple[list[Row], str]:
    if len(rows) <= shown:
        return list(rows), ""
    hidden = len(rows) - shown
    kept = list(rows[-shown:] if tail else rows[:shown])
    return kept, f"{hidden:,} {'earlier' if tail else 'more'} {what}"


# How many rows each tool keeps before its affordance, and from which end. The
# builder's choice; the record argues each.
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
# Arguments shown as key: value rows, and a value's width on its one line.
ARG_ROWS = 6
ARG_WIDTH = 72


def _badge_span(state: str, word: str | None = None) -> Span:
    ink = {"running": "accent", "done": "dim", "error": "danger", "no result": "warn"}[state]
    text = word or {"no result": "no result · turn ended"}.get(state, state)
    return ("caption", ink, text)


def _kv_rows(args: Mapping[str, Any], skip: tuple[str, ...] = ()) -> list[Row]:
    rows: list[Row] = []
    for key, value in args.items():
        if key in skip:
            continue
        shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        rows.append(
            (("mono", "dim", f"{_d(key)}: "), ("mono", "text", _one_line(shown, ARG_WIDTH)))
        )
    if len(rows) > ARG_ROWS:
        rows = rows[:ARG_ROWS] + [_m(f"… {len(rows) - ARG_ROWS} more arguments", "dim")]
    return rows


def _out_rows(lines: list[str], ink: str = "dim") -> list[Row]:
    return [_m(_d(_cut(line.expandtabs(8))), ink) for line in lines]


# A drawn url: scheme and path in dim, the host in "link", the whole underlined as a
# link with the full url as its tooltip. A long path or query loses its middle, so
# the host and the end stay; the tooltip carries the rest.
URL_REST = 56


def url_row(url: str) -> Row:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return (("mono", "link", _d(_cut(url))), ("tip", "link", _d(_cut(url))))
    rest = url[len(parts.scheme) + 3 + len(parts.netloc) :]
    if len(rest) > URL_REST:
        keep = URL_REST - 1
        rest = rest[: keep // 3] + "…" + rest[-(keep - keep // 3) :]
    return (
        ("mono", "dim", _d(f"{parts.scheme}://")),
        ("mono", "link", _d(parts.netloc)),
        ("mono", "dim", _d(rest)),
        ("tip", "link", _d(_cut(url))),
    )


_REDIRECT = re.compile(r"^Redirect URL \(from the server's Location header[^)]*\): (\S+)", re.M)


def _redirect_target(output: str) -> str | None:
    """The target a WebFetch result names when the CLI did not follow a redirect."""
    if not output.startswith("REDIRECT DETECTED:"):
        return None
    found = _REDIRECT.search(output)
    return found[1] if found else None


def proof(entry: ToolEntry, live: bool) -> Proof:
    """The block for ``entry``: its tool's own head and body in the stacked frame."""
    args: Mapping[str, Any] = entry.input or {}
    name = entry.name
    state = state_of(entry, live)
    result = entry.result
    output = "" if result is None else result.output
    if result is not None and result.is_error:
        output = _unwrap_error(output)
    lines = output.splitlines()
    caption: list[Span] = []
    head: list[Row] = []
    body: list[Row] = []
    rows: list[Row] = []
    more = ""
    tail = False
    badge: str | None = None
    failed = state == "error"

    def kind(word: str) -> None:
        caption.append(("caption", "accent", word))

    def detail(text: str) -> None:
        caption.append(("caption", "dim", text))

    if name == "Bash":
        # The terminal: no tool word, the description and limits over a $ line, the
        # last lines of the output, all in the code face.
        if "description" in args:
            detail(_d(args["description"]))
        if (limit := _timeout(args.get("timeout"))) is not None:
            detail(limit)
        if args.get("run_in_background"):
            detail("background")
        if args.get("dangerouslyDisableSandbox"):
            caption.append(("caption", "warn", "unsandboxed"))
        command = str(args.get("command", "")).splitlines() or [""]
        head = [_m(("$ " if n == 0 else "  ") + _d(_cut(c))) for n, c in enumerate(command)]
        if len(head) > COMMAND_LINES:
            head = head[:COMMAND_LINES] + [_m(f"  … {len(head) - COMMAND_LINES} more lines", "dim")]
        if failed and (code := _EXIT.match(output)):
            badge = f"exit {code[1]}"
            lines = output[code.end() :].splitlines()
        kept, more = _cut_rows(_out_rows(lines), SHOWN["Bash"], tail=True)
        body, tail = kept, True
        if more and result is not None and result.dropped_bytes:
            more += f" · {result.dropped_bytes:,} B past the 1 MiB cap"
    elif name == "Read":
        kind("read")
        offset, limit = args.get("offset"), args.get("limit")
        if isinstance(offset, int) and isinstance(limit, int):
            detail(f"lines {offset}–{offset + limit - 1}")
        elif isinstance(offset, int):
            detail(f"from line {offset}")
        elif isinstance(limit, int):
            detail(f"first {limit} lines")
        else:
            detail("whole file")
        if "pages" in args:
            detail(f"pages {_d(args['pages'])}")
        head = [_m(_d(args.get("file_path", "")))]
        if failed:
            body = _out_rows(lines, "danger")
        else:
            numbered: list[Row] = []
            for line in lines:
                num, tab, text = line.partition("\t")
                if tab and num.strip().isdigit():
                    numbered.append(
                        (
                            ("mono", "num", f"{num.strip():>4} "),
                            ("mono", "text", _d(_cut(text.expandtabs(8)))),
                        )
                    )
                else:
                    numbered.append(_m(_d(_cut(line)), "dim"))
            if numbered and result is not None:
                detail(f"{len(numbered):,} lines")
            body, more = _cut_rows(numbered, SHOWN["Read"])
    elif name == "Edit":
        kind("edit")
        old = str(args.get("old_string", "")).splitlines()
        new = str(args.get("new_string", "")).splitlines()
        diff_rows = [
            row
            for row in difflib.unified_diff(old, new, lineterm="", n=1)
            if not row.startswith(("---", "+++", "@@"))
        ]
        added = sum(1 for row in diff_rows if row.startswith("+"))
        removed = sum(1 for row in diff_rows if row.startswith("-"))
        caption.append(("caption", "add", f"+{added}"))
        caption.append(("caption", "del", f"−{removed}"))
        if args.get("replace_all"):
            detail("every occurrence")
        head = [_m(_d(args.get("file_path", "")))]
        diff = [
            _m(
                _d(_cut(row)),
                "add" if row.startswith("+") else "del" if row.startswith("-") else "ctx",
            )
            for row in diff_rows
        ]
        body, more = _cut_rows(diff, SHOWN["Edit"], what="diff lines")
        if failed:
            # What was asked stays; what the CLI said replaces nothing of it.
            body = body + _out_rows(lines[:2], "danger")
    elif name == "Write":
        kind("write")
        content = str(args.get("content", ""))
        content_lines = content.splitlines()
        if result is not None and not failed:
            created = output.startswith("File created successfully")
            caption.append(
                ("caption", "add" if created else "warn", "new file" if created else "overwrote")
            )
        size = len(content.encode("utf-8", "surrogatepass"))
        detail(f"{_kb(size)} · {len(content_lines):,} lines")
        head = [_m(_d(args.get("file_path", "")))]
        preview = [
            (("mono", "add", "+ "), ("mono", "text", _d(_cut(row.expandtabs(8)))))
            for row in content_lines
        ]
        body, more = _cut_rows(preview, SHOWN["Write"], what="lines in the file")
        if failed:
            body = _out_rows(lines[:2], "danger")
            more = ""
    elif name in ("Grep", "Glob"):
        kind(name.lower())
        if "path" in args:
            detail(f"in {_d(args['path'])}")
        for key in ("glob", "type"):
            if key in args:
                detail(f"{key} {_d(args[key])}")
        mode = args.get("output_mode", "files_with_matches") if name == "Grep" else None
        if name == "Grep" and mode != "files_with_matches":
            detail(_d(str(mode)))
        flags = [k for k in ("-i", "-n", "-o", "multiline") if args.get(k)]
        flags += [
            f"{k} {args[k]}" for k in ("-A", "-B", "-C", "context", "head_limit") if k in args
        ]
        if flags:
            detail(" ".join(_d(f) for f in flags))
        pattern = _d(args.get("pattern", ""))
        head = [_m(f"/{pattern}/" if name == "Grep" else pattern)]
        if failed:
            body = _out_rows(lines, "danger")
        elif result is not None:
            found = [ln for ln in lines if ln and not ln.startswith(("Found ", "No files found"))]
            if lines and lines[0] == "No files found":
                caption.append(("caption", "warn", "no matches"))
            elif mode == "content":
                caption.append(("caption", "text", f"{len(found):,} lines"))
            else:
                caption.append(("caption", "text", f"{len(found):,} files"))
            hits: list[Row] = []
            for ln in found:
                where, sep, rest = ln.partition(":")
                line_no, sep2, text = rest.partition(":")
                if mode == "content" and sep and sep2 and line_no.isdigit():
                    hits.append(
                        (
                            ("mono", "accent", _d(f"{where}:{line_no}")),
                            ("mono", "dim", "  " + _d(_cut(text.strip()))),
                        )
                    )
                else:
                    hits.append(_m(_d(_cut(ln)), "text"))
            unit = "matches" if mode == "content" else "files"
            body, more = _cut_rows(hits, SHOWN[name], what=unit)
    elif name == "WebFetch":
        kind("fetch")
        head = [url_row(str(args.get("url", ""))), _p(_d(args.get("prompt", "")), "text")]
        if failed:
            body = _out_rows(lines, "danger")
        elif (target := _redirect_target(output)) is not None:
            # The CLI follows a same-host redirect and says nothing of it; a cross-host
            # one is not fetched, and the result names its target as unverified.
            badge = "redirect · not fetched"
            body = [
                (("caption", "warn", "redirects to (server-supplied, unverified)"),),
                url_row(target),
            ]
        else:
            prose = [_p(_d(ln), "dim") for ln in lines if ln.strip()]
            body, more = _cut_rows(prose, SHOWN["WebFetch"], what="paragraphs")
    elif name == "WebSearch":
        kind("search")
        for key, word in (("allowed_domains", "only"), ("blocked_domains", "not")):
            if args.get(key):
                detail(f"{word} {_d(', '.join(map(str, args[key])))}")
        head = [_p(f"“{_d(args.get('query', ''))}”", "text")]
        if failed:
            body = _out_rows(lines, "danger")
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
            caption.append(("caption", "text", f"{len(links)} results"))
            shown = links[: SHOWN["WebSearch"]]
            for link in shown:
                body.append(_p(_one_line(link.get("title", ""), 80), "text"))
                body.append(url_row(str(link.get("url", ""))))
            if len(links) > len(shown):
                more = f"{len(links) - len(shown)} more results"
    elif name in ("Agent", "Task"):
        kind("agent")
        detail(_d(args.get("subagent_type", "general-purpose")))
        for key in ("model", "effort", "isolation"):
            if key in args:
                detail(_d(args[key]))
        if args.get("run_in_background"):
            detail("background")
        head = [
            _p(_d(args.get("description", "")), "text"),
            (
                ("mono", "dim", "› "),
                ("caption", "dim", _one_line(args.get("prompt", ""), 110)),
                (
                    "caption",
                    "num",
                    f"  {len(str(args.get('prompt', '')).splitlines()):,} lines of prompt",
                ),
            ),
        ]
        text = _blocks_text(output)
        if text.startswith("Async agent launched"):
            badge = "launched · runs on"
            body = []
        else:
            usage = re.search(
                r"<usage>subagent_tokens: (\d+) tool_uses: (\d+) duration_ms: (\d+)</usage>", text
            )
            if usage:
                detail(f"{int(usage[2])} tool uses · {int(usage[3]) / 1000:.0f} s")
                text = text[: text.find("agentId: ")].rstrip() if "agentId: " in text else text
            ink = "danger" if failed else "dim"
            prose = [_p(_d(ln), ink) for ln in text.splitlines() if ln.strip()]
            body, more = _cut_rows(prose, SHOWN["Agent"], what="lines of its report")
    elif name == "TodoWrite":
        kind("todos")
        todos = [t for t in args.get("todos", []) if isinstance(t, dict)]
        counts = {
            s: sum(1 for t in todos if t.get("status") == s)
            for s in ("completed", "in_progress", "pending")
        }
        detail(
            f"{counts['completed']} done · {counts['in_progress']} doing ·"
            f" {counts['pending']} to do"
        )
        rows = []
        for t in todos:
            status = t.get("status")
            if status == "completed":
                rows.append((("mono", "ok", "[x] "), ("prose", "dim", _d(t.get("content", "")))))
            elif status == "in_progress":
                rows.append(
                    (("mono", "accent", "[>] "), ("prose", "text", _d(t.get("activeForm", ""))))
                )
            else:
                rows.append((("mono", "dim", "[ ] "), ("prose", "text", _d(t.get("content", "")))))
        body, more = _cut_rows(rows, SHOWN["TodoWrite"], what="items")
        if failed:
            body = body + _out_rows(lines[:2], "danger")
    elif name == "NotebookEdit":
        kind("notebook")
        mode = str(args.get("edit_mode", "replace"))
        cell = args.get("cell_id")
        detail(f"{_d(mode)} cell {_d(cell)}" if cell is not None else f"{_d(mode)} at the top")
        if "cell_type" in args:
            detail(_d(args["cell_type"]))
        head = [_m(_d(args.get("notebook_path", "")))]
        ink = "del" if mode == "delete" else "add"
        rows = [_m(_d(_cut(r)), ink) for r in str(args.get("new_source", "")).splitlines()]
        body, more = _cut_rows(rows if mode != "delete" else [], SHOWN["NotebookEdit"])
        if failed:
            body = _out_rows(lines[:2], "danger")
            more = ""
    elif (parts := _mcp_parts(name)) is not None:
        server, tool = parts
        caption.append(("caption", "accent", f"mcp · {_d(server)}"))
        head = [(("prose", "text", _d(tool)),)] + _kv_rows(args)
        text = _blocks_text(output)
        ink = "danger" if failed else "dim"
        body, more = _cut_rows(_out_rows(text.splitlines(), ink), SHOWN["mcp"])
    else:
        caption.append(("caption", "warn", _d(name)))
        detail("no block of its own")
        head = _kv_rows(args) or [_m("(no arguments)", "dim")]
        ink = "danger" if failed else "dim"
        body, more = _cut_rows(_out_rows(_blocks_text(output).splitlines(), ink), SHOWN["other"])

    caption.append(_badge_span(state, badge))
    facts = f"name {_d(name)} · input {_d(','.join(args)) if args else '(none)'} · " + (
        "result None"
        if result is None
        else f"{len(result.output.splitlines()):,} lines · is_error {result.is_error}"
    )
    return Proof(
        tool=_d(name),
        state=state,
        caption=tuple(caption),
        head=tuple(head),
        body=tuple(body),
        more=more,
        tail=tail,
        facts=facts,
    )


def proof_strings(p: Proof) -> Iterator[str]:
    """Every string of ``p`` the drawing code can hand to ImGui."""
    yield p.tool
    yield p.more
    yield p.facts
    for _, _, text in p.caption:
        yield text
    for row in (*p.head, *p.body):
        for _, _, text in row:
            yield text


# -- the theme's terminal well ------------------------------------------------------


def _lum(c: Any) -> float:
    def ch(v: float) -> float:
        v = int(min(1.0, max(0.0, v)) * 255.0 + 0.5) / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(c.x) + 0.7152 * ch(c.y) + 0.0722 * ch(c.z)


@dataclass(frozen=True, slots=True)
class Terminal:
    """The well a block sits in, derived from the palette in force, and how."""

    ground: Any
    text: Any
    dim: Any
    outline: Any | None
    how: str


def terminal_for(palette: Any) -> Terminal:
    """
    The block's ground from the theme (the operator: "Theme dictates the
    'terminal' background"). A dark pane goes deeper, toward black, so the block
    keeps the terminal's look and stays a step off the pane; a light pane takes the
    fix-ups table's soft slab, mix(bg, text, 0.06); a pane already black has
    nowhere deeper to go, so it steps toward the text and keeps the theme's frame
    outline. The inks are the palette's text and text_dim on every theme.
    """
    from pptmstr import theme

    bg = palette.bg.vec4
    black = theme.col(0x000000)
    if _lum(bg) >= 0.05:
        ground, how = theme.mixed(palette.bg, palette.text, 0.06), "mix(bg, text, 0.06)"
        outline = None
    elif _lum(bg) < 0.001:
        ground, how = (
            theme.mixed(palette.bg, palette.text, 0.10),
            "mix(bg, text, 0.10) + border outline",
        )
        outline = palette.border.vec4
    else:
        ground, how = theme.mixed(palette.bg, black, 0.60), "mix(bg, black, 0.60)"
        outline = None
    return Terminal(ground, palette.text.vec4, palette.text_dim.vec4, outline, how)


def _ink(name: str, term: Terminal) -> Any:
    from pptmstr import theme

    p = theme.P
    if name == "text":
        return term.text
    if name == "dim":
        return term.dim
    if name == "num":
        return _mix(term.dim, term.ground, 0.35)
    return {
        "add": p.diff_add,
        "del": p.diff_remove,
        "ctx": p.diff_context,
        "accent": p.accent,
        "link": p.accent,
        "ok": p.ok,
        "warn": p.warn,
        "danger": p.danger,
    }[name].vec4


def _span(face: str, colour: Any, text: str, wrap: bool) -> None:
    from imgui_bundle import imgui

    from pptmstr.ui import typeset

    base = imgui.get_style().font_size_base
    if face == "mono":
        if typeset.face_font("CODE") is not None:
            with typeset.font(typeset.Weight.CODE, base):
                imgui.text_colored(colour, _say(text))
        else:
            imgui.text_colored(colour, _say(text))
        return
    # Whole pixels: ImGui 1.92 bakes at whole sizes (preview-fixups' finding).
    size = float(round(base * (0.8 if face == "caption" else 1.0)))
    imgui.push_style_color(imgui.Col_.text, colour)
    if typeset.face_font("PROSE") is not None:
        with typeset.font(typeset.Weight.REGULAR, size):
            (imgui.text_wrapped if wrap else imgui.text)(_say(text))
    else:
        imgui.push_font(None, size)
        (imgui.text_wrapped if wrap else imgui.text)(_say(text))
        imgui.pop_font()
    imgui.pop_style_color()


def _row(row: Row, term: Terminal) -> None:
    """
    One row of spans on one line. A ``tip`` span is not drawn: it makes the row a
    link, underlined in its ink, whose tooltip is the span's text.
    """
    from imgui_bundle import imgui

    drawn = [span for span in row if span[0] != "tip"]
    tip = next((span for span in row if span[0] == "tip"), None)
    imgui.begin_group()
    for n, (face, ink, text) in enumerate(drawn):
        if n:
            imgui.same_line(0.0, 0.0)
        _span(face, _ink(ink, term), text, wrap=len(drawn) == 1 and tip is None)
    imgui.end_group()
    if tip is not None:
        lo, hi = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        colour = imgui.color_convert_float4_to_u32(_mix(_ink(tip[1], term), term.ground, 0.4))
        imgui.get_window_draw_list().add_line(
            imgui.ImVec2(lo.x, hi.y), imgui.ImVec2(hi.x, hi.y), colour, 1.0
        )
        imgui.set_item_tooltip(_say(tip[2]))


def _caption_row(spans: tuple[Span, ...], term: Terminal) -> None:
    from imgui_bundle import imgui

    for n, (face, ink, text) in enumerate(spans):
        if n:
            imgui.same_line(0.0, 0.0)
            _span(face, _ink("num", term), " · ", wrap=False)
            imgui.same_line(0.0, 0.0)
        _span(face, _ink(ink, term), text, wrap=False)


def draw_proof(p: Proof, key: str) -> float:
    """One block in the stacked frame on the theme's well; returns its height."""
    from imgui_bundle import imgui

    from pptmstr import theme

    term = terminal_for(theme.current())
    top = imgui.get_cursor_screen_pos().y
    imgui.push_style_color(imgui.Col_.child_bg, term.ground)
    imgui.push_style_color(imgui.Col_.border, term.outline or term.ground)
    imgui.push_style_var(imgui.StyleVar_.child_rounding, 5.0)
    imgui.push_style_var(imgui.StyleVar_.child_border_size, 1.0 if term.outline else 0.0)
    imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(10.0, 6.0))
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(8.0, 2.0))
    flags = imgui.ChildFlags_.always_use_window_padding | imgui.ChildFlags_.auto_resize_y
    if term.outline is not None:
        flags |= imgui.ChildFlags_.borders
    imgui.begin_child(f"##proof-{key}", imgui.ImVec2(0.0, 0.0), flags)
    if p.state == "error":
        lo = imgui.get_window_pos()
        hi = imgui.ImVec2(lo.x + 3.0, lo.y + imgui.get_window_height())
        imgui.get_window_draw_list().add_rect_filled(lo, hi, theme.P.danger.u32, 5.0)
    _caption_row(p.caption, term)
    for row in p.head:
        _row(row, term)
    if p.body or p.more:
        imgui.dummy(imgui.ImVec2(0.0, 2.0))
    if p.more and p.tail:
        imgui.small_button(f"{_say(p.more)}##more-{key}")
    for row in p.body:
        _row(row, term)
    if p.more and not p.tail:
        imgui.small_button(f"{_say(p.more)}##more-{key}")
    imgui.end_child()
    imgui.pop_style_var(4)
    imgui.pop_style_color(2)
    return float(imgui.get_item_rect_max().y - top)


# -- the proof cases: each tool's real input shape, written as the driver writes ---

# (tool, column) -> (name, input, content or None, is_error, live, note). Columns:
# "done", "second" (a long output, or the tool's other shape), "error", "running",
# "no result". ``note`` says where a result text was not found verbatim in the CLI.
_PY = "/home/wreel/src/tle/tle/parse.py"
_LONG_PROMPT = "\n".join(
    [
        "Read tle/parse.py and tests/test_parse.py. The checksum ignores a minus sign,",
        "which the format counts as one. Find every caller of checksum(), say which",
        "would change, and write the smallest fix with a test for a line that has a",
        "minus in its inclination field. Do not commit.",
    ]
)
_SEARCH_LINKS = [
    {
        "title": "CelesTrak: NORAD Two-Line Element Set Format",
        "url": "https://celestrak.org/columns/v04n03/",
    },
    {
        "title": "Two-line element set - Wikipedia",
        "url": "https://en.wikipedia.org/wiki/Two-line_element_set",
    },
    {
        "title": "TLE checksum algorithm explained",
        "url": "https://space.stackexchange.com/questions/5358",
    },
    {"title": "sgp4 - PyPI", "url": "https://pypi.org/project/sgp4/"},
    {
        "title": "Space-Track.org: Basic Description of the TLE format",
        "url": "https://www.space-track.org/documentation#tle",
    },
    {
        "title": "python-skyfield: Earth satellites",
        "url": "https://rhodesmill.org/skyfield/earth-satellites.html",
    },
]


_LONG_URL = (
    "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle"
    "&CATNR=25544&ORDER=EPOCH&utm_source=newsletter&utm_campaign=2026-10"
)
_REDIRECTED = (
    "https://www.space-track.org/basicspacedata/query/class/gp/NORAD_CAT_ID/25544/format/tle"
)


def _redirect_result(url: str, target: str, prompt: str) -> str:
    """WebFetch's result for a cross-host redirect, in the form CLI 2.1.301 builds it."""
    return (
        "REDIRECT DETECTED: The URL redirects to a location that was not fetched"
        f" automatically.\n\nOriginal URL: {url}\nRedirect URL (from the server's Location"
        f" header \u2014 server-supplied, not verified): {target}\nStatus: 301 Moved"
        " Permanently\n\nTo complete your request, I need to fetch content from the"
        " redirected URL. Please use WebFetch again with these parameters:\n"
        f'- url: "{target}"\n- prompt: "{prompt}"'
    )


def _search_result(query: str, links: list[dict[str, str]]) -> str:
    return (
        f'Web search results for query: "{query}"\n\n'
        f"Links: {json.dumps(links)}\n\n"
        "The TLE checksum is the sum of the digits on the line modulo 10, minus signs"
        " counting as 1.\n\n"
        "REMINDER: You MUST include the sources above in your response to the user using"
        " markdown hyperlinks."
    )


def _agent_result(text: str, tokens: int, uses: int, ms: int) -> list[dict[str, str]]:
    usage = (
        "agentId: a4f0c2e19b7d3e55 (use SendMessage with to: 'a4f0c2e19b7d3e55', summary:"
        " '<5-10 word recap>' to continue this agent) <usage>subagent_tokens:"
        f" {tokens} tool_uses: {uses} duration_ms: {ms}</usage>"
    )
    return [{"type": "text", "text": text}, {"type": "text", "text": usage}]


_TODOS = [
    {"content": "Read the parser", "status": "completed", "activeForm": "Reading the parser"},
    {
        "content": "Write a failing test for a minus sign",
        "status": "completed",
        "activeForm": "Writing a failing test",
    },
    {
        "content": "Count a minus as one in checksum()",
        "status": "in_progress",
        "activeForm": "Counting a minus as one in checksum()",
    },
    {"content": "Run the suite", "status": "pending", "activeForm": "Running the suite"},
    {
        "content": "Update the format notes",
        "status": "pending",
        "activeForm": "Updating the format notes",
    },
]
_WRITE_BODY = "\n".join(
    [
        '"""',
        "The TLE checksum: digits count, a minus counts one, mod 10.",
        '"""',
        "",
        "from tle.parse import checksum",
        "",
        "LINE_1 = '1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927'",
        "",
        "",
        "def test_checksum_counts_minus() -> None:",
        "    assert checksum(LINE_1) == 7",
    ]
)

PROOF_TOOLS: tuple[str, ...] = (
    "Bash",
    "Read",
    "Edit",
    "Write",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "Agent",
    "TodoWrite",
    "NotebookEdit",
    "mcp__pptmstr__read_board",
    "Skill",
)
PROOF_COLUMNS = ("done", "second", "error", "running", "no result")
# What "second" shows, per tool, said on the sheet.
SECOND = {
    "Bash": "long output, tail kept",
    "Read": "long excerpt, head kept",
    "Edit": "replace_all, a longer diff",
    "Write": "an overwrite",
    "Grep": "output_mode content, -n",
    "Glob": "a long list",
    "WebFetch": "a long url with a query; a cross-host redirect",
    "WebSearch": "allowed_domains",
    "Agent": "run_in_background",
    "TodoWrite": "a short list",
    "NotebookEdit": "insert, markdown",
    "mcp__pptmstr__read_board": "post_concern with arguments",
    "Skill": "with arguments",
}


def _proof_calls() -> list[tuple[str, str, str, dict[str, Any], object, bool, bool, str]]:
    """(tool, column, name, input, content, is_error, live, note) for every cell."""
    bash = {
        "command": "git status --short",
        "description": "Show working tree status",
        "timeout": 120000,
    }
    read = {"file_path": _PY, "offset": 1, "limit": 12}
    edit = {
        "file_path": _PY,
        "old_string": (
            "    for ch in line[:68]:\n        if ch.isdigit():\n            total += int(ch)\n  "
            "  return total % 10\n"
        ),
        "new_string": (
            "    for ch in line[:68]:\n        if ch.isdigit():\n            total += int(ch)\n"
            '        elif ch == "-":\n            total += 1\n    return total % 10\n'
        ),
    }
    write = {"file_path": "/home/wreel/src/tle/tests/test_minus.py", "content": _WRITE_BODY}
    grep = {"pattern": "checksum\\(", "path": "/home/wreel/src/tle"}
    glob = {"pattern": "**/test_*.py", "path": "/home/wreel/src/tle"}
    fetch = {
        "url": "https://celestrak.org/columns/v04n03/#FAQ04",
        "prompt": "How is the TLE checksum computed, and does a minus sign count?",
    }
    search = {"query": "TLE checksum minus sign counts as one"}
    agent = {
        "description": "Find checksum callers",
        "prompt": _LONG_PROMPT,
        "subagent_type": "Explore",
    }
    todos = {"todos": _TODOS}
    nb = {
        "notebook_path": "/home/wreel/src/tle/notes/orbits.ipynb",
        "cell_id": "c7f3a1",
        "new_source": (
            "from tle.parse import checksum\n\nfor line in LINES:\n    print(checksum(line))"
        ),
        "cell_type": "code",
        "edit_mode": "replace",
    }
    mcp: dict[str, Any] = {}
    skill = {"skill": "simplify"}
    board = (
        "board: 4 tasks\n- tool-proofs [claimed] builder-25\n- tool-block-variants [completed]"
        " builder-22\n- preview-fixups [completed] builder-23\n- apply-style [open]"
    )
    read_out = "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(_READ_LINES, 1))
    long_read = "\n".join(
        f"{n:>6}\t{line}" for n, line in enumerate([*_READ_LINES, *_READ_LINES[:9]] * 4, 1)
    )
    c = "content"
    return [
        # Bash
        (
            "Bash",
            "done",
            "Bash",
            bash,
            " M pptmstr/ui/transcript_pane.py\n?? notes/caf\udc80.txt\n?? shots/",
            False,
            False,
            "",
        ),
        (
            "Bash",
            "second",
            "Bash",
            {
                "command": ".venv/bin/python -m pytest -v tests/test_parse.py",
                "description": "Run the parser's tests verbosely",
                "timeout": 600000,
            },
            _pytest_verbose(42),
            False,
            False,
            "",
        ),
        (
            "Bash",
            "error",
            "Bash",
            {
                "command": "pytest -q tests/test_parse.py -k checksum",
                "description": "Run the checksum tests",
            },
            "Exit code 1\nF.\n>       assert checksum(LINE_1) == 7\nE       AssertionError: "
            "assert 6 == 7\n"
            "tests/test_minus.py:11: AssertionError\n1 failed, 1 passed in 0.12s",
            True,
            False,
            "",
        ),
        (
            "Bash",
            "running",
            "Bash",
            {"command": "make test", "description": "Run the whole suite", "timeout": 600000},
            None,
            False,
            True,
            "",
        ),
        (
            "Bash",
            "no result",
            "Bash",
            {
                "command": "make shot-xvfb",
                "description": "Take the headless screenshot",
                "run_in_background": True,
            },
            None,
            False,
            False,
            "",
        ),
        # Read
        ("Read", "done", "Read", read, read_out, False, False, ""),
        ("Read", "second", "Read", {"file_path": _PY}, long_read, False, False, ""),
        (
            "Read",
            "error",
            "Read",
            {"file_path": "/home/wreel/src/tle/tle/pars.py"},
            "<tool_use_error>File does not exist.</tool_use_error>",
            True,
            False,
            "",
        ),
        ("Read", "running", "Read", read, None, False, True, ""),
        ("Read", "no result", "Read", read, None, False, False, ""),
        # Edit
        (
            "Edit",
            "done",
            "Edit",
            edit,
            f"The file {_PY} has been updated successfully.",
            False,
            False,
            "",
        ),
        (
            "Edit",
            "second",
            "Edit",
            {
                "file_path": _PY,
                "old_string": "\n".join(_READ_LINES[2:9]),
                "new_string": "\n".join(_READ_LINES[2:9]).replace("total", "acc"),
                "replace_all": True,
            },
            f"The file {_PY} has been updated. All occurrences were successfully replaced.",
            False,
            False,
            "",
        ),
        (
            "Edit",
            "error",
            "Edit",
            edit,
            "<tool_use_error>String to replace not found in file.\nString:     for ch in "
            "line[:68]:</tool_use_error>",
            True,
            False,
            "",
        ),
        ("Edit", "running", "Edit", edit, None, False, True, ""),
        ("Edit", "no result", "Edit", edit, None, False, False, ""),
        # Write
        (
            "Write",
            "done",
            "Write",
            write,
            f"File created successfully at: {write['file_path']}",
            False,
            False,
            "",
        ),
        (
            "Write",
            "second",
            "Write",
            {"file_path": _PY, "content": "\n".join(_READ_LINES[:9])},
            f"The file {_PY} has been updated successfully.",
            False,
            False,
            "",
        ),
        (
            "Write",
            "error",
            "Write",
            {"file_path": _PY, "content": "x = 1"},
            "<tool_use_error>File has not been read yet. Read it first before writing to "
            "it.</tool_use_error>",
            True,
            False,
            "",
        ),
        ("Write", "running", "Write", write, None, False, True, ""),
        ("Write", "no result", "Write", write, None, False, False, ""),
        # Grep
        (
            "Grep",
            "done",
            "Grep",
            grep,
            "Found 3 "
            "files\n/home/wreel/src/tle/tle/parse.py\n/home/wreel/src/tle/tle/cli.py\n/home/wreel/src/tle/tests/test_parse.py",
            False,
            False,
            "",
        ),
        (
            "Grep",
            "second",
            "Grep",
            {
                "pattern": "checksum\\(",
                "path": "/home/wreel/src/tle",
                "output_mode": c,
                "-n": True,
                "type": "py",
            },
            "\n".join(
                f"/home/wreel/src/tle/{f}:{n}:    {t}"
                for f, n, t in [
                    ("tle/parse.py", 1, "def checksum(line: str) -> int:"),
                    ("tle/parse.py", 40, "if checksum(line) != int(line[68]):"),
                    ("tle/cli.py", 12, "print(checksum(args.line))"),
                    ("tle/cli.py", 31, "ok = checksum(l1) == int(l1[68])"),
                    ("tests/test_parse.py", 8, "assert checksum(LINE_1) == 5"),
                    ("tests/test_parse.py", 14, "assert checksum(LINE_2) == 7"),
                    ("tests/test_parse.py", 22, "assert checksum('') == 0"),
                    ("tests/test_minus.py", 11, "assert checksum(LINE_1) == 7"),
                ]
            ),
            False,
            False,
            "",
        ),
        (
            "Grep",
            "error",
            "Grep",
            {"pattern": "checksum", "path": "/home/wreel/src/tl"},
            "<tool_use_error>Path does not exist: /home/wreel/src/tl.</tool_use_error>",
            True,
            False,
            "the CLI appends a cwd note and a 'Did you mean' to this; cut here",
        ),
        ("Grep", "running", "Grep", grep, None, False, True, ""),
        ("Grep", "no result", "Grep", grep, None, False, False, ""),
        # Glob
        (
            "Glob",
            "done",
            "Glob",
            glob,
            "/home/wreel/src/tle/tests/test_parse.py\n/home/wreel/src/tle/tests/test_minus.py",
            False,
            False,
            "",
        ),
        (
            "Glob",
            "second",
            "Glob",
            {"pattern": "**/*.py"},
            "\n".join(
                f"/home/wreel/src/tle/{p}"
                for p in [
                    "tle/__init__.py",
                    "tle/parse.py",
                    "tle/cli.py",
                    "tle/orbit.py",
                    "tle/units.py",
                    "tle/fetch.py",
                    "tests/conftest.py",
                    "tests/test_parse.py",
                    "tests/test_minus.py",
                    "tests/test_orbit.py",
                    "setup.py",
                ]
            ),
            False,
            False,
            "",
        ),
        (
            "Glob",
            "error",
            "Glob",
            {"pattern": "*.py", "path": "/home/wreel/src/tl"},
            "<tool_use_error>Directory does not exist: /home/wreel/src/tl</tool_use_error>",
            True,
            False,
            "",
        ),
        ("Glob", "running", "Glob", glob, None, False, True, ""),
        ("Glob", "no result", "Glob", glob, None, False, False, ""),
        # WebFetch
        (
            "WebFetch",
            "done",
            "WebFetch",
            fetch,
            "The checksum is the sum of all digits on the line modulo 10. Letters, blanks, "
            "periods and plus signs count as 0; each minus sign counts as 1.",
            False,
            False,
            "",
        ),
        (
            "WebFetch",
            "second",
            "WebFetch",
            {"url": _LONG_URL, "prompt": "What is the latest element set for the ISS?"},
            _redirect_result(_LONG_URL, _REDIRECTED, "What is the latest element set for the ISS?"),
            False,
            False,
            "redirect text as the CLI builds it (CLI 2.1.301, WebFetch's REDIRECT DETECTED)",
        ),
        (
            "WebFetch",
            "error",
            "WebFetch",
            {"url": "https://celestrak.org/columns/v04n04/", "prompt": "What does the column say?"},
            "Request failed with status code 404",
            True,
            False,
            "",
        ),
        ("WebFetch", "running", "WebFetch", fetch, None, False, True, ""),
        ("WebFetch", "no result", "WebFetch", fetch, None, False, False, ""),
        # WebSearch
        (
            "WebSearch",
            "done",
            "WebSearch",
            search,
            _search_result(search["query"], _SEARCH_LINKS),
            False,
            False,
            "",
        ),
        (
            "WebSearch",
            "second",
            "WebSearch",
            {"query": "TLE format", "allowed_domains": ["celestrak.org", "space-track.org"]},
            _search_result("TLE format", [_SEARCH_LINKS[0], _SEARCH_LINKS[4]]),
            False,
            False,
            "",
        ),
        (
            "WebSearch",
            "error",
            "WebSearch",
            {"query": "TLE"},
            "<tool_use_error>InputValidationError: Error: Search query is too "
            "short</tool_use_error>",
            True,
            False,
            "error text illustrative: the wrapper and prefix are the CLI's, the message is not "
            "quoted from it",
        ),
        ("WebSearch", "running", "WebSearch", search, None, False, True, ""),
        ("WebSearch", "no result", "WebSearch", search, None, False, False, ""),
        # Agent
        (
            "Agent",
            "done",
            "Agent",
            agent,
            _agent_result(
                "checksum() has 4 callers outside tests: parse.py:40 and cli.py:12, 31 compare it"
                " to column 69, and would change for any line with a minus.\nNo caller depends on"
                " the old value.\nThe fix is two lines in parse.py.",
                18422,
                9,
                41200,
            ),
            False,
            False,
            "",
        ),
        (
            "Agent",
            "second",
            "Agent",
            {
                **agent,
                "description": "Fix the checksum",
                "subagent_type": "builder",
                "run_in_background": True,
                "model": "sonnet",
            },
            [
                {
                    "type": "text",
                    "text": (
                        "Async agent launched successfully. (This tool result is internal "
                        "metadata) agentId: b81d0e (internal ID - do not mention to user.) The "
                        "agent is working in the background."
                    ),
                }
            ],
            False,
            False,
            "launch text abridged from the CLI's",
        ),
        (
            "Agent",
            "error",
            "Agent",
            {**agent, "subagent_type": "explorer"},
            "<tool_use_error>InputValidationError: Agent type 'explorer' not "
            "found.</tool_use_error>",
            True,
            False,
            "error text illustrative: wrapper and prefix are the CLI's",
        ),
        ("Agent", "running", "Agent", agent, None, False, True, ""),
        ("Agent", "no result", "Agent", agent, None, False, False, ""),
        # TodoWrite
        (
            "TodoWrite",
            "done",
            "TodoWrite",
            todos,
            "Todos have been modified successfully. Ensure that you continue to use the todo "
            "list to track your progress. Please proceed with the current tasks if applicable",
            False,
            False,
            "",
        ),
        (
            "TodoWrite",
            "second",
            "TodoWrite",
            {"todos": _TODOS[2:4]},
            "Todos have been modified successfully. Ensure that you continue to use the todo "
            "list to track your progress. Please proceed with the current tasks if applicable",
            False,
            False,
            "",
        ),
        (
            "TodoWrite",
            "error",
            "TodoWrite",
            {"todos": [{"content": "", "status": "pending", "activeForm": "x"}]},
            "<tool_use_error>InputValidationError: Content cannot be empty</tool_use_error>",
            True,
            False,
            "message is the CLI's schema message; the wrapper is its",
        ),
        ("TodoWrite", "running", "TodoWrite", todos, None, False, True, ""),
        ("TodoWrite", "no result", "TodoWrite", todos, None, False, False, ""),
        # NotebookEdit
        (
            "NotebookEdit",
            "done",
            "NotebookEdit",
            nb,
            f"Updated cell c7f3a1 with {nb['new_source']}",
            False,
            False,
            "",
        ),
        (
            "NotebookEdit",
            "second",
            "NotebookEdit",
            {
                "notebook_path": nb["notebook_path"],
                "cell_id": "c7f3a1",
                "new_source": "## Checksums\n\nA minus counts one.",
                "cell_type": "markdown",
                "edit_mode": "insert",
            },
            "Inserted cell c7f3a1 with ## Checksums\n\nA minus counts one.",
            False,
            False,
            "",
        ),
        (
            "NotebookEdit",
            "error",
            "NotebookEdit",
            {"notebook_path": nb["notebook_path"], "new_source": "x", "edit_mode": "insert"},
            "<tool_use_error>Cell type is required when using edit_mode=insert.</tool_use_error>",
            True,
            False,
            "",
        ),
        ("NotebookEdit", "running", "NotebookEdit", nb, None, False, True, ""),
        ("NotebookEdit", "no result", "NotebookEdit", nb, None, False, False, ""),
        # MCP (pptmstr's own bus)
        (
            "mcp__pptmstr__read_board",
            "done",
            "mcp__pptmstr__read_board",
            mcp,
            [{"type": "text", "text": board}],
            False,
            False,
            "",
        ),
        (
            "mcp__pptmstr__read_board",
            "second",
            "mcp__pptmstr__post_concern",
            {
                "to": "lead",
                "subject": "tool-proofs: dark sheet rendered",
                "body": (
                    "The dark sheet is at shots/tool-block/proofs/dark.png.\nLight, xterm and "
                    "high_contrast follow."
                ),
                "task_id": "tool-proofs",
                "basis": "run",
            },
            [{"type": "text", "text": "sent to lead"}],
            False,
            False,
            "result text illustrative: the bus's reply is pptmstr's own",
        ),
        (
            "mcp__pptmstr__read_board",
            "error",
            "mcp__pptmstr__read_board",
            mcp,
            "MCP error -32000: Connection closed",
            True,
            False,
            "",
        ),
        (
            "mcp__pptmstr__read_board",
            "running",
            "mcp__pptmstr__read_board",
            mcp,
            None,
            False,
            True,
            "",
        ),
        (
            "mcp__pptmstr__read_board",
            "no result",
            "mcp__pptmstr__read_board",
            mcp,
            None,
            False,
            False,
            "",
        ),
        # A tool with no block of its own: the fallback
        (
            "Skill",
            "done",
            "Skill",
            skill,
            "Launching skill: simplify",
            False,
            False,
            "result text illustrative",
        ),
        (
            "Skill",
            "second",
            "Skill",
            {"skill": "code-review", "args": "high --fix"},
            [{"type": "text", "text": "Launching skill: code-review"}],
            False,
            False,
            "'args' from this session's own Skill tool schema; result illustrative",
        ),
        (
            "Skill",
            "error",
            "Frobnicate",
            {"x": 1},
            "<tool_use_error>Error: No such tool available: Frobnicate</tool_use_error>",
            True,
            False,
            "",
        ),
        ("Skill", "running", "Skill", skill, None, False, True, ""),
        ("Skill", "no result", "Skill", skill, None, False, False, ""),
    ]


@dataclass(frozen=True, slots=True)
class ProofCase:
    tool: str
    column: str
    entry: ToolEntry
    live: bool
    note: str


def build_proof_cases() -> tuple[Transcript, tuple[ProofCase, ...]]:
    """Every proof cell, written into one ``Transcript`` and read back as entries."""
    t = Transcript()
    calls = _proof_calls()
    for n, (_tool, _col, name, args, content, err, _live, _note) in enumerate(calls):
        tid = f"toolu_proof_{n:02d}"
        t.append_tool_call(tid, name, args)
        if content is not None:
            t.append_tool_result(tid, name, content, is_error=err)
    cases = []
    for n, (tool, col, _name, _args, _content, _err, live, note) in enumerate(calls):
        entry = t.tool_entry(f"toolu_proof_{n:02d}")
        assert entry is not None
        cases.append(ProofCase(tool, col, entry, live, note))
    return t, tuple(cases)


_PROOF_CASES: tuple[ProofCase, ...] | None = None


def _proof_cases() -> tuple[ProofCase, ...]:
    global _PROOF_CASES
    if _PROOF_CASES is None:
        _PROOF_CASES = build_proof_cases()[1]
    return _PROOF_CASES


# Sheet 1 is what a tool looks like when it worked; sheet 2 its other states.
PROOF_SHEETS: dict[str, tuple[str, ...]] = {
    "": ("done", "second"),
    "-states": ("error", "running", "no result"),
}


def draw_proof_sheet(suffix: str, first: bool) -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    columns = PROOF_SHEETS[suffix]
    by = {(c.tool, c.column): c for c in _proof_cases()}
    term = terminal_for(theme.current())
    if _sheet_window(first):
        imgui.text(
            _say(
                f"tool-block proofs, theme {theme.current().name}: "
                + (" · ".join(columns))
                + " (the stacked frame, every tool)"
            )
        )
        _note(
            "MOCK: drawn by scripts/tool_block_probe.py over the --fake app, not by the app."
            " Each block is a ToolEntry written through Transcript.append_tool_call/"
            "append_tool_result with the input shape CLI 2.1.301 sends.",
            warn=True,
        )
        _note(
            f"well = {term.how} ({_hex(term.ground)}) · ink text {_hex(term.text)} · dim"
            f" {_hex(term.dim)} · error: 3 px danger rule · caption"
            f" {round(imgui.get_style().font_size_base * 0.8)}"
            f" px proportional · affordance: imgui.small_button under the pick (button D) ·"
            f" style: style_probe.final_for({theme.current().name!r})"
        )
        flags = (
            imgui.TableFlags_.sizing_stretch_same
            | imgui.TableFlags_.pad_outer_x
            | imgui.TableFlags_.borders_inner_h
        )
        if imgui.begin_table("##proofs", 1 + len(columns), flags):
            imgui.table_setup_column("tool", imgui.TableColumnFlags_.width_fixed, 150.0)
            for col in columns:
                imgui.table_setup_column(col)
            imgui.table_headers_row()
            for tool in PROOF_TOOLS:
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text(
                    _say(tool.removeprefix("mcp__pptmstr__") if tool.startswith("mcp__") else tool)
                )
                if tool.startswith("mcp__"):
                    _note("MCP (pptmstr bus)")
                elif tool == "Skill":
                    _note("fallback: no block of its own")
                for col in columns:
                    imgui.table_next_column()
                    case = by[(tool, col)]
                    if col == "second":
                        _note(SECOND[tool])
                    p = proof(case.entry, case.live)
                    height = draw_proof(p, f"{tool}-{col}")
                    HEIGHTS[(f"proof-{tool}", col)] = height
                    _note(f"{height:g} px · {p.facts}" + (f" · {case.note}" if case.note else ""))
            imgui.end_table()
    imgui.end()


def _hex(c: Any) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        *(int(min(1.0, max(0.0, v)) * 255.0 + 0.5) for v in (c.x, c.y, c.z))
    )


# -- the run ------------------------------------------------------------------------


def _scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    import screenshot
    from imgui_bundle import hello_imgui, imgui

    del fixtures
    yield from screenshot._until(
        "the fake fleet", lambda: state.driver is not None and state.frame_snap is not None
    )
    hello_imgui.change_window_size(SHEET_SIZE)
    yield from screenshot._until(
        f"the window at {SHEET_SIZE}",
        lambda: tuple(int(v) for v in imgui.get_io().display_size) == SHEET_SIZE,
    )
    yield from screenshot._frames(30)
    for name in SHEETS:
        _Shown.sheet, _Shown.first = name, True
        yield from screenshot._frames(10)
        yield name
    _Shown.sheet = None
    for (variant, case), height in sorted(HEIGHTS.items()):
        print(f"height {variant} {case}: {height:g} px", flush=True)
    for style in STYLES:
        total = sum(h for (v, _), h in HEIGHTS.items() if v == style.name)
        print(f"height {style.name} total over {len(_cases())} cases: {total:g} px", flush=True)
    print(f"fonts: caption {_Fonts.caption}; monospace {_Fonts.mono}", flush=True)


def _render(out_dir: Path) -> int:
    import screenshot

    from pptmstr import app as pptmstr_app
    from pptmstr import theme

    real_apply = theme.apply_style

    @functools.wraps(real_apply)
    def apply_style() -> None:
        real_apply()
        apply_picked()

    theme.apply_style = apply_style  # type: ignore[assignment]
    real_overlays = pptmstr_app._draw_overlays

    def overlays(state: Any) -> None:
        if _Shown.sheet is not None:
            draw_sheet(_Shown.sheet, _Shown.first)
            _Shown.first = False
        real_overlays(state)

    pptmstr_app._draw_overlays = overlays  # type: ignore[assignment]
    screenshot.SCENARIOS[SCENARIO] = _scenario
    sys.argv = [
        "screenshot.py",
        "--scenario",
        SCENARIO,
        "--out-dir",
        str(out_dir),
        "--argv",
        "--fake",
    ]
    return int(screenshot.main())


class _Proofs:
    theme = "dark"
    sheet: str | None = None
    first = False


def _proof_scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    import screenshot
    from imgui_bundle import hello_imgui, imgui

    del fixtures
    yield from screenshot._until(
        "the fake fleet", lambda: state.driver is not None and state.frame_snap is not None
    )
    hello_imgui.change_window_size(PROOF_SHEET_SIZE)
    yield from screenshot._until(
        f"the window at {PROOF_SHEET_SIZE}",
        lambda: tuple(int(v) for v in imgui.get_io().display_size) == PROOF_SHEET_SIZE,
    )
    yield from screenshot._frames(30)
    for suffix in PROOF_SHEETS:
        _Proofs.sheet, _Proofs.first = suffix, True
        yield from screenshot._frames(10)
        yield f"{_Proofs.theme}{suffix}"
    _Proofs.sheet = None
    for (tool, col), height in sorted(HEIGHTS.items()):
        print(f"height {tool} {col}: {height:g} px", flush=True)


def _render_proofs(out_dir: Path, theme_name: str) -> int:
    """One app run on ``theme_name`` under preview-fixups' final style, both sheets."""
    import dataclasses

    import screenshot
    import style_probe

    from pptmstr import app as pptmstr_app
    from pptmstr import settings as settings_mod
    from pptmstr import theme

    real_read = settings_mod.read

    @functools.wraps(real_read)
    def read(path: Path | None = None) -> Any:
        found = real_read(path)
        return dataclasses.replace(
            found, settings=dataclasses.replace(found.settings, theme=theme_name)
        )

    settings_mod.read = read  # type: ignore[assignment]
    variant = style_probe.final_for(theme_name)
    if variant.micro is not None:
        style_probe.install_micro(variant.micro)
    real_apply = theme.apply_style

    @functools.wraps(real_apply)
    def apply_style() -> None:
        real_apply()
        style_probe.apply(variant)

    theme.apply_style = apply_style  # type: ignore[assignment]
    real_overlays = pptmstr_app._draw_overlays

    def overlays(state: Any) -> None:
        if _Proofs.sheet is not None:
            draw_proof_sheet(_Proofs.sheet, _Proofs.first)
            _Proofs.first = False
        real_overlays(state)

    pptmstr_app._draw_overlays = overlays  # type: ignore[assignment]
    _Proofs.theme = theme_name
    screenshot.SCENARIOS[SCENARIO] = _proof_scenario
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.argv = [
        "screenshot.py",
        "--scenario",
        SCENARIO,
        "--out-dir",
        str(out_dir),
        "--argv",
        "--fake",
    ]
    return int(screenshot.main())


def _proofs_on_xvfb(out_dir: Path, themes: tuple[str, ...]) -> int:
    worst = 0
    for name in themes:
        env = dict(os.environ)
        env["XVFB_SHOT_SCREEN"] = f"{PROOF_SHEET_SIZE[0] + 100}x{PROOF_SHEET_SIZE[1] + 100}x24"
        started = time.monotonic()
        done = subprocess.run(
            [
                str(ROOT / "scripts" / "xvfb_shot.sh"),
                "--",
                str(ROOT / ".venv" / "bin" / "python"),
                str(Path(__file__).resolve()),
                "--proofs-render",
                "--theme",
                name,
                "--out-dir",
                str(out_dir),
            ],
            cwd=ROOT,
            env=env,
        )
        print(f"{name}: exit {done.returncode} in {time.monotonic() - started:.0f}s", flush=True)
        missing = [s for s in PROOF_SHEETS if not (out_dir / f"{name}{s}.png").is_file()]
        if missing or done.returncode:
            print(f"{name}: missing {missing}", file=sys.stderr)
            worst = worst or done.returncode or 1
    return worst


def _on_xvfb(out_dir: Path) -> int:
    env = dict(os.environ)
    env["XVFB_SHOT_SCREEN"] = f"{SHEET_SIZE[0] + 100}x{SHEET_SIZE[1] + 100}x24"
    started = time.monotonic()
    done = subprocess.run(
        [
            str(ROOT / "scripts" / "xvfb_shot.sh"),
            "--",
            str(ROOT / ".venv" / "bin" / "python"),
            str(Path(__file__).resolve()),
            "--render",
            "--out-dir",
            str(out_dir),
        ],
        cwd=ROOT,
        env=env,
    )
    print(f"exit {done.returncode} in {time.monotonic() - started:.0f}s", flush=True)
    missing = [s for s in SHEETS if not (out_dir / f"{s}.png").is_file()]
    if missing:
        print(f"{out_dir}: no image for {', '.join(missing)}", file=sys.stderr)
        return done.returncode or 1
    return done.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="every sheet, on its own Xvfb")
    which.add_argument("--render", action="store_true", help="every sheet, on the display there is")
    which.add_argument("--describe", action="store_true", help="print every variant's values")
    which.add_argument("--proofs", action="store_true", help="per-tool proofs, every theme, Xvfb")
    which.add_argument("--proofs-render", action="store_true", help="per-tool proofs, one theme")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--theme", action="append", choices=PROOF_THEMES)
    args = ap.parse_args()
    if args.proofs or args.proofs_render:
        out = args.out_dir or PROOF_OUT
        themes = tuple(args.theme or PROOF_THEMES)
        if args.proofs:
            return _proofs_on_xvfb(out, themes)
        return _render_proofs(out, themes[0])
    args.out_dir = args.out_dir or OUT
    if args.describe:
        for style in STYLES:
            print(f"{style.name}: {style.summary}")
            for line in style.values():
                print(f"    {line}")
        return 0
    if args.all:
        return _on_xvfb(args.out_dir)
    return _render(args.out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
