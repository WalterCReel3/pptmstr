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
from collections.abc import Iterator, Mapping
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
    ap.add_argument("--out-dir", type=Path, default=OUT)
    args = ap.parse_args()
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
