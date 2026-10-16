"""
The fields the store holds as they arrived, in front of every pane.

``store._VERBATIM`` names the fields something is run with, reused for, or written
back to pptmstr's own files from: a session's directory, its task, team and brief,
how its run failed, the message a resume opens on, and a command's texts and
output. Those are held exactly as they came, which is what a fork, a resume and a
file need, and so they can hold what no ImGui text call takes: a lone surrogate
(``os.getcwd`` in a directory whose name is not UTF-8 gives one), on which every
text call raises, and a NUL, at which ImGui stops reading. Each pane draws them
through one of the helpers that take any ``str`` (``widgets.author_text`` and the
three built on it, and ``commandtext.visible``).

Two checks, because each misses what the other sees:

- **Every pane of the application is drawn** (``app._panels`` under
  ``app.guarded``, as the window docks them) with the cursor on a live session and
  on a recalled one whose every such field holds both characters. The binding is
  the recorder: a field handed to it raw raises, and ``app.guarded`` records it.
  What it reaches is only what those frames reach.
- **No ImGui call in ``pptmstr/ui`` is handed such a field** by attribute, or by a
  name assigned from one in the same function, except through a helper. Read from
  the source, so it covers branches no frame here reaches, and it is only as good as
  that reading: a field carried in another record, or returned from a function,
  is not followed.

A tool call's entry is the other verbatim surface: ``ToolEntry.name``, ``.input``
and ``ToolResult.output`` are held as the SDK sent them (``transcript``'s
docstring), and the session pane draws them as blocks (``ui.tool_block``). The
live session's transcript holds calls whose name, arguments and output carry both
characters, so the frames draw those blocks; and the three are fields the source
check knows by name.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pptmstr import app
from pptmstr.bridge import Bridge
from pptmstr.command import Command, End, Exited, Run
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    CommandsRestored,
    SessionRecalled,
    StateChanged,
)
from pptmstr.model import (
    AgentState,
    EndedHow,
    NodeId,
    Recalled,
    TranscriptPresence,
)
from pptmstr.pool import SessionPool
from pptmstr.settings import Settings
from pptmstr.store import _VERBATIM, Store
from pptmstr.transcript import SegmentKind, Transcript
from pptmstr.ui import focus, session_details, typeset, widgets

LOW = json.loads('"\\udc80"')
NUL = "\x00"
HOME = "/x/caf" + json.loads('"\\udce9"')
ODD = f"a{NUL}b{LOW}c\\u0000d"

LIVE: NodeId = ("sess-live", None)
SUB: NodeId = ("sess-live", "a1")
CLOSED: NodeId = ("sess-closed", None)
CMD = "cmd-20261015T120000Z-0123456789ab"


def _tool_calls() -> Transcript:
    """
    A transcript holding tool calls the way the driver writes them, every field of
    each entry carrying a lone surrogate and a NUL: a done call with a long output,
    an error, one with no result, an MCP call, and a call of a tool with no block of
    its own.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "Looking.\n")
    long = "".join(f"line {n} {ODD}\n" for n in range(40)) + ODD * 200
    calls: tuple[tuple[str, str, dict[str, Any], object, bool | None], ...] = (
        ("t1", "Bash", {"command": f"ls {ODD}", "description": f"list {ODD}"}, long, False),
        ("t2", "Read", {"file_path": f"{HOME}/a.py"}, f"     1\t{ODD}\n", True),
        ("t3", "Edit", {"file_path": HOME, "old_string": ODD, "new_string": f"{ODD}!"}, None, None),
        (
            "t4",
            "mcp__pptmstr__post_concern",
            {"to": ODD, "body": ODD},
            [{"type": "text", "text": ODD}],
            False,
        ),
        ("t5", f"Frob{ODD}", {f"k{ODD}": ODD}, ODD, False),
    )
    for tool_use_id, name, args, output, failed in calls:
        transcript.append_tool_call(tool_use_id, name, args)
        if failed is not None:
            transcript.append_tool_result(tool_use_id, name, output, is_error=failed)
    return transcript


def _store() -> Store:
    store = Store()
    store.apply(
        AgentSpawned(
            node_id=LIVE,
            parent=None,
            task=f"task {ODD}",
            model="claude-opus-5",
            started_at=0.0,
            template=f"team {ODD}",
            brief=f"{HOME}/brief",
            cwd=HOME,
            session_base=HOME,
            transcript=_tool_calls(),
        ),
        now=0.0,
    )
    store.apply(
        AgentSpawned(
            node_id=SUB,
            parent=LIVE,
            task=f"sub {ODD}",
            model="claude-haiku-5",
            started_at=1.0,
            agent_type="builder",
            transcript=_tool_calls(),
        ),
        now=1.0,
    )
    store.apply(StateChanged(SUB, AgentState.THINKING, "reading"), now=2.0)
    store.apply(AgentFinished(LIVE, AgentState.FAILED, ended_at=3.0, error=f"err {ODD}"), now=3.0)
    store.apply(
        SessionRecalled(
            session_id=CLOSED[0],
            task=f"task {ODD}",
            model="claude-opus-5",
            recalled=Recalled(
                transcript=TranscriptPresence.PRESENT,
                created_at=10.0,
                last_active=20.0,
                ended_how=EndedHow.FAILED,
                error=f"err {ODD}",
                last_message=f"message {ODD}",
                task_recorded=True,
            ),
            cwd=HOME,
            session_base=HOME,
            template=f"team {ODD}",
            brief=f"{HOME}/brief",
        ),
        now=4.0,
    )
    tail = f"out {ODD}\n"
    store.apply(
        CommandsRestored(
            LIVE[0],
            (
                Command(
                    id=CMD,
                    asker=SUB,
                    asked="echo \\u0000",
                    reason="to see",
                    expects="nothing",
                    asked_at=1.0,
                    run=Run(
                        text="echo \\u0000",
                        shell="/bin/bash",
                        cwd=HOME,
                        timeout_s=60.0,
                        started_at=2.0,
                    ),
                    end=End(Exited(0), at=3.0, duration_s=1.0, output_bytes=len(tail), tail=tail),
                ),
            ),
        ),
        now=5.0,
    )
    return store


@pytest.fixture
def im(imgui: Any) -> Iterator[Any]:
    """The module's context, recovering from a pane that raised as ``app.run`` sets it."""
    typeset.configure(typeset=False)
    io = imgui.get_io()
    held = (
        io.config_error_recovery,
        io.config_error_recovery_enable_assert,
        io.config_error_recovery_enable_tooltip,
    )
    io.config_error_recovery = True
    io.config_error_recovery_enable_assert = False
    io.config_error_recovery_enable_tooltip = False
    app._PANEL_ERRORS.clear()
    yield imgui
    app._PANEL_ERRORS.clear()
    (
        io.config_error_recovery,
        io.config_error_recovery_enable_assert,
        io.config_error_recovery_enable_tooltip,
    ) = held


@pytest.fixture
def handed(monkeypatch: pytest.MonkeyPatch, im: Any) -> list[str]:
    """Every string a pane hands one of ImGui's text calls."""
    strings: list[str] = []

    def recording(name: str, at: int) -> None:
        real = getattr(im, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            if len(args) > at and isinstance(args[at], str):
                strings.append(args[at])
            return real(*args, **kwargs)

        monkeypatch.setattr(im, name, call)

    for name in (
        "text",
        "text_unformatted",
        "text_wrapped",
        "text_disabled",
        "calc_text_size",
        "button",
        "small_button",
        "selectable",
        "set_tooltip",
        "set_clipboard_text",
    ):
        recording(name, 0)
    recording("text_colored", 1)
    return strings


@pytest.mark.parametrize("node", [LIVE, SUB, CLOSED], ids=["live", "sub-agent", "recalled"])
def test_every_pane_draws_a_session_whose_verbatim_fields_no_text_call_takes(
    node: NodeId, im: Any, handed: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_details, "_session_files", lambda session_id: None)
    state = app.AppState(store=_store(), bridge=Bridge(), settings=Settings())
    state.pool = SessionPool(state.bridge, cap=1)
    snap = state.store.snapshot()
    state.frame_snap = snap
    state.frame_now = 6.0
    state.frame_wall = 30.0
    state.focus.to_node(snap, node, scope=focus.Scope.AGENT)
    panels = app._panels(state)
    guarded = {name: app.guarded(name, draw) for name, draw in panels.items()}

    for _ in range(2):
        typeset.pre_frame()
        im.new_frame()
        try:
            for name, pane in guarded.items():
                im.set_next_window_size(im.ImVec2(900.0, 700.0))
                im.begin(name)
                pane()
                im.end()
        finally:
            im.render()

    assert app._PANEL_ERRORS == {}
    assert not [s for s in handed if NUL in s]
    # And the fields were drawn, not left out: the directory, and the task.
    assert any("CAF<U+DCE9>" in s for s in handed)
    assert any(f"task a{widgets.NUL_SHOWN}b<U+DC80>c" in s for s in handed)


@pytest.mark.parametrize("node", [LIVE, CLOSED], ids=["live", "recalled"])
def test_every_copy_in_session_details_reaches_the_clipboard(
    node: NodeId, im: Any, handed: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The clipboard is another text call: ``set_clipboard_text`` raises on a lone
    surrogate and keeps a string to its first NUL. Every copy button is pressed.
    """
    shown = session_details.view(
        _store().snapshot(),
        node,
        held=None,
        looked=None,
        logging=None,
        now=6.0,
        wall_now=30.0,
    )
    assert shown is not None
    copies = [row.copy for section in shown.sections for row in section.rows if row.copy]
    assert any(HOME in copy for copy in copies)
    # Every copy is pressed through the wrapper the rows draw it with.
    monkeypatch.setattr(widgets, "small_button", lambda label: True)
    # What reaches the clipboard, apart from what ``handed`` also notes for the rows'
    # values: a row draws the same text it copies.
    clipped: list[str] = []
    through = im.set_clipboard_text

    def copying(text: str) -> None:
        clipped.append(text)
        through(text)

    monkeypatch.setattr(im, "set_clipboard_text", copying)
    state = session_details.DetailsState()
    state.ask(node)
    for _ in range(2):
        im.new_frame()
        try:
            session_details.draw(state, shown)
        finally:
            im.render()

    assert set(clipped) == {widgets.author_text(c) for c in copies}
    assert not [s for s in handed if NUL in s]


# -- the source -------------------------------------------------------------------

UI = Path(app.__file__).parent / "ui"

# What makes any ``str`` something a text call takes, so a field passed through
# one of them is drawn whatever it holds.
HELPERS = frozenset(
    {
        "author_text",
        "one_line",
        "flat_line",
        "ellipsis",
        "ellipsis_by",
        "visible",
        "asker_value",
        "_authored",
        "_marked",
        # Read the field and answer something else: a count, a yes or no.
        "len",
        "bool",
        "output_heading",
    }
)

# ``drawable.text`` is a helper as well; it is matched by its module, since
# ``imgui.text`` is not.
QUALIFIED_HELPERS = frozenset({("drawable", "text")})

# Calls the check flags and that draw no field of a record: the launcher's own
# boxes, which hold what the operator typed into them and what a failed launch of
# those same boxes hands back (``LauncherState._load``), and nothing a record held.
NOT_A_RECORD = frozenset(
    {
        ("launcher.py", "input_text_with_hint", ".cwd"),
        ("launcher.py", "input_text_with_hint", ".brief"),
    }
)

# A field of the verbatim set, by name. A few names (``text``, ``reason``,
# ``answer``, ``shell``) are also everyday attribute names of records that are
# drawable, and a check that flagged them all would be ignored; those are left to
# the frames above.
GENERIC = frozenset({"text", "reason", "answer", "shell"})
# A tool call's entry: ``ToolEntry.name`` and ``.input``, and ``ToolResult.output``
# (rv-tool-entries, item 1). Not store fields, so named here.
TOOL_ENTRY = frozenset({"name", "input", "output"})
VERBATIM_NAMES = TOOL_ENTRY | frozenset(
    name for fields in _VERBATIM.values() for name in fields if name not in GENERIC
)


def _raw_uses(node: ast.AST, tainted: set[str]) -> list[str]:
    """
    Every verbatim attribute, or name assigned from one, in ``node`` that does not
    sit inside a call to a helper.
    """
    found: list[str] = []

    def walk(at: ast.AST) -> None:
        if isinstance(at, ast.Compare):
            return
        if isinstance(at, ast.IfExp):
            # The test is a yes or no; what is drawn is one of the two branches.
            walk(at.body)
            walk(at.orelse)
            return
        if isinstance(at, ast.Call):
            func = at.func
            if isinstance(func, ast.Attribute):
                owner = func.value.id if isinstance(func.value, ast.Name) else None
                if (owner, func.attr) in QUALIFIED_HELPERS:
                    return
                if func.attr in HELPERS and owner != "imgui":
                    return
            elif isinstance(func, ast.Name) and func.id in HELPERS:
                return
        if isinstance(at, ast.Attribute) and at.attr in VERBATIM_NAMES:
            found.append(f".{at.attr}")
            return
        if isinstance(at, ast.Name) and at.id in tainted:
            found.append(at.id)
            return
        for child in ast.iter_child_nodes(at):
            walk(child)

    walk(node)
    return found


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), str(path))
    out: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        tainted: set[str] = set()
        for statement in ast.walk(function):
            if isinstance(statement, ast.Assign) and _raw_uses(statement.value, tainted):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        tainted.add(target.id)
        for call in ast.walk(function):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "imgui"
            ):
                continue
            for argument in [*call.args, *(k.value for k in call.keywords)]:
                for use in _raw_uses(argument, tainted):
                    if (path.name, call.func.attr, use) in NOT_A_RECORD:
                        continue
                    out.append(f"{path.name}:{call.lineno} imgui.{call.func.attr}(... {use} ...)")
    return out


def test_no_imgui_call_in_the_panes_is_handed_a_verbatim_field_except_through_a_helper() -> None:
    found = [v for path in sorted(UI.rglob("*.py")) for v in _violations(path)]
    assert found == []


def test_the_source_check_sees_a_raw_draw_and_passes_one_through_a_helper(tmp_path: Path) -> None:
    source = tmp_path / "pane.py"
    source.write_text(
        "def draw(rec):\n"
        "    imgui.text(rec.cwd)\n"
        "    title = rec.task or 'x'\n"
        "    imgui.text_colored(c, title)\n"
        "    imgui.text(one_line(rec.cwd))\n"
        "    shown = flat_line(rec.task)\n"
        "    imgui.text(shown)\n"
        "    imgui.text(one_line(rec.cwd) if rec.cwd else 'none')\n"
        "    imgui.text(drawable.text(rec.cwd))\n"
        "    imgui.text(rec.brief if rec.cwd != rec.brief else 'x')\n"
        "    imgui.text(entry.result.output)\n"
        "    command = entry.input['command']\n"
        "    imgui.text_colored(c, command)\n"
        "    imgui.set_item_tooltip(entry.name)\n"
        "    imgui.text(drawable.text(entry.result.output))\n"
    )
    assert _violations(source) == [
        "pane.py:2 imgui.text(... .cwd ...)",
        "pane.py:4 imgui.text_colored(... title ...)",
        "pane.py:10 imgui.text(... .brief ...)",
        "pane.py:11 imgui.text(... .output ...)",
        "pane.py:13 imgui.text_colored(... command ...)",
        "pane.py:14 imgui.set_item_tooltip(... .name ...)",
    ]


def test_a_resume_box_opens_on_the_message_written_out_where_no_box_takes_it(
    tmp_path: Path,
) -> None:
    """
    The box is an ``input_text``, which raises on a lone surrogate and keeps a
    string to its first NUL. It opens on what it can show, and what is sent is
    what it shows; a message every box takes is opened on as it was sent.
    """
    from pptmstr.approval import Policy
    from pptmstr.ui.resume_form import ResumeForms

    def opened(message: str) -> str:
        form = ResumeForms().open_for(
            session_id="sess-r",
            cwd=str(tmp_path),
            task="t",
            template="solo",
            policy=Policy.STRICT,
            contained=False,
            model="claude-opus-5",
            subagent_cap=None,
            brief=None,
            last_message=message,
        )
        return form.message

    assert opened(f"go {ODD}") == widgets.author_text(f"go {ODD}")
    assert opened("go \\u0000 on") == "go \\u0000 on"
