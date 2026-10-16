"""
An explanation is behind a question mark, and nothing else in the two launch
dialogs is more than one line.

Against Dear ImGui itself -- a context with a display size and no backend -- for
what Dear ImGui decides: that a modal opened from inside a dialog's popup is
stacked over it, that Escape closes the one on top and only that one, and that the
dialog under it keeps what was typed. The enumeration at the end draws both dialogs
in every state that says something different, and holds them to the property the
operator asked for: no paragraph of explanation is drawn unless the question mark
for it was pressed, and every paragraph either dialog used to draw is behind one.

Nothing is rendered, so nothing here says how it looks; the screenshots do that.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from pptmstr import cli_version, sessionfile, templates, theme
from pptmstr.approval import Policy
from pptmstr.sessions import SessionRow
from pptmstr.ui import launcher, resume_form, widgets
from pptmstr.ui.launcher import LauncherState, SessionPicker
from pptmstr.ui.resume_form import ResumeForms
from pptmstr.ui.widgets import Help, Notice, Tone
from tests import test_small_button as _buttons

SESSION = "11111111-2222-3333-4444-555555555555"
PREMISE = "do the thing\nand here is the reason it is worth doing"
INSIDE_THE_CHECKOUT = str(Path(launcher.__file__).resolve().parent)


def _meets() -> cli_version.FloorCheck:
    return cli_version.MeetsFloor("2.1.300", (2, 1, 300), cli_version.SANDBOX_FLOOR)


def _below() -> cli_version.FloorCheck:
    return cli_version.BelowFloor("2.1.9", (2, 1, 9), cli_version.SANDBOX_FLOOR)


# Holds a read of the CLI's version unanswered until the test that started it ends.
_UNANSWERED = threading.Event()


@pytest.fixture(autouse=True)
def _answer_at_the_end() -> Iterator[None]:
    _UNANSWERED.clear()
    yield
    _UNANSWERED.set()


def _slow() -> cli_version.FloorCheck:
    _UNANSWERED.wait(timeout=30.0)
    return _meets()


def _row(session_id: str, cwd: str | None) -> SessionRow:
    return SessionRow(
        session_id=session_id,
        cwd=cwd,
        git_branch="main",
        summary=f"summary for {session_id}",
        first_prompt=None,
        created_at=None,
        last_modified=0,
        tag=None,
    )


# The wrappers themselves, kept before a test puts ``_Drawn``'s in their place.
_REAL_SMALL_BUTTON = widgets.small_button
_REAL_LINK = widgets.link


class _Drawn:
    """
    Dear ImGui, for ``widgets`` and for the dialog drawing through it, noting every
    piece of text a frame draws and where every small button was, and answering
    True once for a small button a test asks to press.
    """

    _TEXT = ("text", "text_disabled", "text_wrapped")

    def __init__(self) -> None:
        self.said: list[str] = []
        self.buttons: dict[str, tuple[float, float]] = {}
        self.press: str | None = None

    def __getattr__(self, name: str) -> object:
        real = getattr(imgui, name)
        if name not in self._TEXT:
            return real

        def noted(*args: Any) -> object:
            self.said.append(str(args[0]))
            return real(*args)

        return noted

    def text_colored(self, colour: Any, text: str) -> None:
        self.said.append(text)
        imgui.text_colored(colour, text)

    def small_button(self, label: str) -> bool:
        return self._noted(label, _REAL_SMALL_BUTTON(label))

    def link(self, label: str, **kwargs: Any) -> bool:
        """A question mark is a link (``widgets.link``), noted as a button is."""
        return self._noted(label, _REAL_LINK(label, **kwargs))

    def _noted(self, label: str, pressed: bool) -> bool:
        low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        self.buttons[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)
        if self.press == label:
            self.press = None
            return True
        return pressed


def _new_context() -> Any:
    """A context made current: ``create_context`` makes one current only when none is."""
    made = imgui.create_context()
    imgui.set_current_context(made)
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1280.0, 900.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.set_ini_filename("")
    io.add_focus_event(True)
    theme.apply_style()
    return made


@pytest.fixture
def context() -> Iterator[None]:
    made = _new_context()
    yield
    imgui.destroy_context(made)


class _Frames:
    """One dialog drawn at the root, a frame at a time, through ``_Drawn``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, draw: Callable[[], None]) -> None:
        self.drawn = _Drawn()
        monkeypatch.setattr(widgets, "small_button", self.drawn.small_button)
        monkeypatch.setattr(widgets, "link", self.drawn.link)
        self._draw = draw
        for module in (widgets, launcher, resume_form):
            monkeypatch.setattr(module, "imgui", self.drawn)
        self.helps: list[Help] = []
        self.notices: list[Notice] = []
        real_notice, real_mark = widgets.notice, widgets.help_mark

        def notice(said: Notice) -> None:
            self.notices.append(said)
            real_notice(said)

        def help_mark(mark: str, title: str, paragraphs: Any) -> bool:
            self.helps.append(Help(mark, title, tuple(paragraphs)))
            return real_mark(mark, title, paragraphs)

        monkeypatch.setattr(widgets, "notice", notice)
        monkeypatch.setattr(widgets, "help_mark", help_mark)

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            self.drawn.said.clear()
            self.helps.clear()
            self.notices.clear()
            imgui.new_frame()
            imgui.push_font(None, theme._FONT_SIZE)
            try:
                self._draw()
            finally:
                imgui.pop_font()
            imgui.end_frame()

    @property
    def explained(self) -> list[Help]:
        """
        Every explanation behind a question mark on the last frame: each mark is
        drawn by ``help_mark``, a notice's included.
        """
        return list(self.helps)

    def click(self, label: str) -> None:
        io = imgui.get_io()
        x, y = self.drawn.buttons[label]
        io.add_mouse_pos_event(x, y)
        self.frame(2)
        io.add_mouse_button_event(0, True)
        self.frame()
        io.add_mouse_button_event(0, False)
        self.frame(3)

    def escape(self) -> None:
        io = imgui.get_io()
        io.add_key_event(imgui.Key.escape, True)
        self.frame()
        io.add_key_event(imgui.Key.escape, False)
        self.frame(2)


def _help_window(title: str) -> Any:
    for window in imgui.get_current_context().windows:
        if window.name.startswith(f"{title}###help:") and window.active:
            return window
    return None


def _launcher(
    monkeypatch: pytest.MonkeyPatch,
    state: LauncherState,
    *,
    prober: launcher.Prober = _meets,
    live: frozenset[str] = frozenset(),
    running: int = 0,
) -> _Frames:
    state.request_open()

    def draw() -> None:
        launcher.draw(
            state,
            running=running,
            queued=2,
            cap=4,
            launch=lambda spec, draft: None,
            wrap=True,
            scanner=lambda cwd: ((), {}),
            prober=prober,
            subagent_cap=4,
            live=live,
            recorder=lambda session_id: None,
        )

    return _Frames(monkeypatch, draw)


def _settled(gate: launcher.VersionGate, prober: launcher.Prober = _meets) -> None:
    worker = gate.request(prober=prober)
    if worker is not None:
        worker.join(timeout=5.0)
    gate.poll()


# -- the mark and the modal behind it ------------------------------------------------


def test_a_mark_opens_its_explanation_over_the_dialog_and_escape_closes_only_that(
    context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Pressed with the mouse, from inside New Task: the explanation is a second modal
    over the first, with the title it was given and its paragraphs. Escape takes it
    down and leaves New Task up with the task as it was typed; a second Escape is
    New Task's.
    """
    state = LauncherState(task=PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    _settled(state.version_gate)
    frames = _launcher(monkeypatch, state)
    frames.frame(4)
    title = "what autonomous releases, and what bounds it"
    assert _help_window(title) is None
    assert not widgets.help_showing()
    (alarm,) = [said for said in frames.explained if said.title == title]
    assert not set(alarm.paragraphs) & set(frames.drawn.said), "drawn before it was asked for"

    frames.click(f"{widgets.HELP_MARK}##gate")

    window = _help_window(title)
    assert window is not None
    assert imgui.internal.find_window_by_name(launcher.TITLE).active, "New Task is under it"
    assert set(alarm.paragraphs) <= set(frames.drawn.said)
    assert widgets.help_showing()
    stack = imgui.get_current_context().open_popup_stack
    assert stack.size() == 2, "stacked over New Task's popup, not in place of it"

    frames.escape()
    assert _help_window(title) is None and not widgets.help_showing()
    assert state.is_open, "the Escape that closed the explanation did not close New Task"
    assert state.task == PREMISE and state.policy is Policy.AUTONOMOUS

    frames.escape()
    assert not state.is_open, "and the next Escape is New Task's"
    assert state.task == PREMISE


def test_the_close_button_closes_the_explanation_and_the_dialog_stays(
    context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = LauncherState(task=PREMISE, cwd=str(tmp_path))
    frames = _launcher(monkeypatch, state)
    frames.frame(4)
    frames.click(f"{widgets.HELP_MARK}##gate")
    assert _help_window("the strict gate") is not None

    real_button = imgui.button
    at: dict[str, tuple[float, float]] = {}

    def button(label: str, size: Any = None) -> bool:
        pressed = real_button(label, size) if size is not None else real_button(label)
        low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        at[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)
        return bool(pressed)

    monkeypatch.setattr(frames.drawn, "button", button, raising=False)
    frames.frame(2)
    frames.drawn.buttons["close"] = at["close"]
    frames.click("close")

    assert _help_window("the strict gate") is None
    assert state.is_open and state.task == PREMISE


def test_the_resume_form_keeps_its_message_under_an_explanation(
    context: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    forms = ResumeForms()
    form = forms.open_for(
        session_id=SESSION,
        as_policy=Policy.AUTONOMOUS,
        cwd=str(work),
        task="rebuild the parser",
        template="review",
        policy=Policy.STRICT,
        contained=False,
        model="claude-test-model",
        subagent_cap=3,
        brief=None,
        last_message="carry on with the lexer",
    )
    _settled(forms.version_gate)

    def draw() -> None:
        resume_form.draw(
            forms,
            launch=lambda press: None,
            wrap=True,
            prober=_meets,
            subagent_cap=4,
            checkout=str(tmp_path / "own"),
        )

    frames = _Frames(monkeypatch, draw)
    frames.frame(4)
    frames.click(f"{widgets.HELP_MARK}##gate")
    assert widgets.help_showing()
    assert resume_form._BRIEFING in frames.drawn.said

    frames.escape()
    assert not widgets.help_showing()
    assert forms.is_open and form.message == "carry on with the lexer"
    assert form.policy is Policy.AUTONOMOUS


def test_a_notice_is_one_row_however_long_its_line(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Cut, never wrapped: a line too long for the row ends in an ellipsis and is one
    line high, with its mark on the same row.
    """
    heights: list[float] = []
    advances: list[float] = []
    tops: list[float] = []
    drawn = _Drawn()

    def body() -> None:
        imgui.set_next_window_size(imgui.ImVec2(300.0, 200.0))
        imgui.begin("w")
        imgui.push_text_wrap_pos(0.0)
        before = imgui.get_cursor_pos_y()
        widgets.notice(Notice("word " * 60, Tone.WARN, Help("x", "t", ("p",))))
        heights.append(imgui.get_item_rect_size().y)
        advances.append(imgui.get_cursor_pos_y() - before)
        tops.append(imgui.get_item_rect_min().y)
        imgui.pop_text_wrap_pos()
        imgui.end()

    monkeypatch.setattr(widgets, "imgui", drawn)
    monkeypatch.setattr(widgets, "link", drawn.link)
    for _ in range(2):
        drawn.said.clear()
        imgui.new_frame()
        body()
        imgui.end_frame()

    (line,) = [text for text in drawn.said if text.startswith("word")]
    assert line.endswith("…") and "\n" not in line
    # The mark is a link, one text line tall: the row is one line high, and the
    # cursor moves down by that line and nothing more: shorter than the 22 px
    # small button it was (tests/test_link.py has the 6 px at the 16 px face).
    one = imgui.get_text_line_height()
    assert heights[-1] == one, "the mark's row"
    assert advances[-1] == one + imgui.get_style().item_spacing.y, "one row high"
    assert heights[-1] < widgets.SMALL_BUTTON_TEXT_PX + 2.0 * widgets.SMALL_BUTTON_PADDING[1]
    assert f"{widgets.HELP_MARK}##x" in drawn.buttons


# -- every state, every paragraph ----------------------------------------------------


def _launcher_states(tmp_path: Path) -> dict[str, tuple[LauncherState, dict[str, Any]]]:
    """New Task in each state that says something different."""
    main = tmp_path / "repo"
    worktree = tmp_path / "repo-worktrees" / "feature"
    main.mkdir()
    worktree.mkdir(parents=True)
    linked = tmp_path / "link"
    linked.symlink_to(worktree)
    states: dict[str, tuple[LauncherState, dict[str, Any]]] = {}

    def made(name: str, **fields: Any) -> LauncherState:
        extra = fields.pop("_draw", {})
        state = LauncherState(task=PREMISE, cwd=str(main), **fields)
        if extra.get("prober", _meets) is _meets:
            _settled(state.version_gate)
        states[name] = (state, extra)
        return state

    for rung in Policy:
        made(f"new-{rung.value}", policy=rung)
    made("new-autonomous-refused", policy=Policy.AUTONOMOUS).cwd = INSIDE_THE_CHECKOUT
    made("new-autonomous-cli-below", policy=Policy.AUTONOMOUS, _draw={"prober": _below})
    made("new-autonomous-checking", policy=Policy.AUTONOMOUS, _draw={"prober": _slow})
    made("new-autonomous-thin", policy=Policy.AUTONOMOUS).task = "one line"
    made("new-team", template_index=1)
    made("new-at-cap", _draw={"running": 4})
    for name, cwd, live in (
        ("picked-elsewhere", str(worktree), frozenset({"a"})),
        ("picked-here", str(main), frozenset()),
        ("picked-through-a-link", str(linked), frozenset()),
        ("picked-gone", str(tmp_path / "gone"), frozenset()),
        ("picked-unrecorded", None, frozenset()),
    ):
        for rung in (Policy.STRICT, Policy.AUTONOMOUS):
            state = made(f"{name}-{rung.value}", policy=rung, _draw={"live": live})
            picker = SessionPicker()
            picker.pick(_row("a", cwd), state.cwd)
            state.picker = picker
    made("picked-refused", policy=Policy.AUTONOMOUS).picker.pick(
        _row("a", INSIDE_THE_CHECKOUT), str(main)
    )

    failed = made("failure-loaded")
    failed.report_failure(failed.draft(), "the CLI reports 2.1.9, below the floor")
    kept = made("failure-kept")
    kept.report_failure(kept.draft(), "no such base ref")
    kept.task = "typed since"
    kept.restore_failed()
    kept.report_failure(kept.draft(), "a second reason")
    kept.task = "typed again"
    past = made("failure-pressed-past-without-a-draft")
    past.report_failure(None, "reason", "relaunch")
    past.launched()
    return states


def _resume_states(tmp_path: Path) -> dict[str, tuple[ResumeForms, dict[str, Any]]]:
    work = tmp_path / "work"
    work.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(work)
    states: dict[str, tuple[ResumeForms, dict[str, Any]]] = {}
    for name, cwd, rung, extra in (
        ("strict", str(work), Policy.STRICT, {}),
        ("permissive", str(work), Policy.PERMISSIVE, {}),
        ("autonomous", str(work), Policy.AUTONOMOUS, {}),
        ("autonomous-live", str(work), Policy.AUTONOMOUS, {"live": frozenset({SESSION})}),
        ("autonomous-refused", INSIDE_THE_CHECKOUT, Policy.AUTONOMOUS, {}),
        ("autonomous-cli-below", str(work), Policy.AUTONOMOUS, {"prober": _below}),
        ("through-a-link", str(linked), Policy.STRICT, {}),
        ("gone", str(tmp_path / "gone"), Policy.AUTONOMOUS, {}),
        ("unrecorded", "", Policy.AUTONOMOUS, {}),
        ("label-and-unknown-team", str(work), Policy.STRICT, {"label": True}),
        ("failure", str(work), Policy.STRICT, {"failure": True}),
    ):
        forms = ResumeForms()
        form = forms.open_for(
            session_id=SESSION,
            as_policy=rung,
            cwd=cwd,
            task="rebuild the parser",
            task_is_label=bool(extra.get("label")),
            template=resume_form.UnknownTeam() if extra.get("label") else "review",
            policy=Policy.STRICT,
            contained=False,
            model="claude-test-model",
            subagent_cap=3,
            brief=None,
            last_message="carry on",
        )
        if extra.get("prober", _meets) is _meets:
            _settled(forms.version_gate)
        if extra.get("failure"):
            form.failed(form.draft(), "the CLI reports 2.1.9, below the floor")
            form.message = "typed since"
            form._prefill = ""
        states[name] = (forms, extra)
    return states


def _resume_frames(
    monkeypatch: pytest.MonkeyPatch, forms: ResumeForms, extra: dict[str, Any], own: str
) -> _Frames:
    def draw() -> None:
        resume_form.draw(
            forms,
            launch=lambda press: None,
            wrap=True,
            prober=extra.get("prober", _meets),
            subagent_cap=4,
            live=extra.get("live", frozenset()),
            checkout=own,
        )

    return _Frames(monkeypatch, draw)


def _every_dialog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[str, Callable[[], _Frames]]]:
    """
    Each state of each dialog, to be drawn in a Dear ImGui context of its own: a
    text box keeps what it holds under its id from one draw to the next, and the
    last dialog's task would be written into the next one's.
    """
    outer = imgui.get_current_context()
    for name, open_dialog in _every_state(monkeypatch, tmp_path):
        made = _new_context()
        try:
            yield name, open_dialog
        finally:
            imgui.destroy_context(made)
            imgui.set_current_context(outer)


def _every_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[str, Callable[[], _Frames]]]:
    def new_task(state: LauncherState, extra: dict[str, Any]) -> Callable[[], _Frames]:
        return lambda: _launcher(monkeypatch, state, **extra)

    def resume(forms: ResumeForms, extra: dict[str, Any]) -> Callable[[], _Frames]:
        return lambda: _resume_frames(monkeypatch, forms, extra, INSIDE_THE_CHECKOUT)

    for name, (state, extra) in _launcher_states(tmp_path / "launcher").items():
        yield f"launcher {name}", new_task(state, extra)
    for name, (forms, extra) in _resume_states(tmp_path / "resume").items():
        yield f"resume {name}", resume(forms, extra)


@pytest.fixture
def roots(tmp_path: Path) -> Path:
    (tmp_path / "launcher").mkdir()
    (tmp_path / "resume").mkdir()
    return tmp_path


def test_no_paragraph_is_drawn_until_its_mark_is_pressed_and_each_is_then(
    context: None, monkeypatch: pytest.MonkeyPatch, roots: Path
) -> None:
    """
    The operator's property, over every state of both dialogs: with no mark
    pressed, nothing behind any mark is on screen; pressing a mark draws its
    paragraphs, every one; and a paragraph is behind one mark on a frame, not two.
    """
    seen: dict[str, set[str]] = {}
    for name, open_dialog in _every_dialog(monkeypatch, roots):
        frames = open_dialog()
        frames.frame(4)
        explained = list(frames.explained)
        assert explained, name
        closed_text = set(frames.drawn.said)
        everything = [p for said in explained for p in said.paragraphs]
        assert len(everything) == len(set(everything)), f"{name}: a paragraph behind two marks"
        lines = {said.line for said in frames.notices}
        for said in explained:
            leaked = set(said.paragraphs) & closed_text
            # A line may open its own explanation -- what a rung adds is both the
            # line under the dial and the first paragraph behind its mark -- and
            # that is the only way a paragraph is on screen unasked.
            assert leaked <= lines, (name, leaked - lines)
            seen.setdefault(name, set()).update(said.paragraphs)

        for said in explained:
            frames.drawn.press = f"{widgets.HELP_MARK}##{said.mark}"
            frames.frame(2)
            missing = set(said.paragraphs) - set(frames.drawn.said)
            assert not missing, (name, said.title, missing)
            assert widgets.help_showing(), (name, said.title)
            frames.escape()
            assert not widgets.help_showing(), (name, said.title)
        monkeypatch.undo()
    assert len(seen) > 30


def _reachable(monkeypatch: pytest.MonkeyPatch, roots: Path) -> set[str]:
    out: set[str] = set()
    for _, open_dialog in _every_dialog(monkeypatch, roots):
        frames = open_dialog()
        frames.frame(4)
        out.update(p for said in frames.explained for p in said.paragraphs)
        monkeypatch.undo()
    return out


def test_every_paragraph_either_dialog_drew_is_behind_a_mark(
    context: None, monkeypatch: pytest.MonkeyPatch, roots: Path
) -> None:
    """
    Nothing was taken out of the product: each text either dialog drew under a
    control, in a section or behind its disclosure is reachable through a mark in
    some state. Listed by name, so a text that stops being reachable is named.
    """
    reachable = _reachable(monkeypatch, roots)
    joined = "\n".join(reachable)
    texts = {
        "_PICKED_SENDS": launcher._PICKED_SENDS,
        "_PICKED_CHOSEN_HERE": launcher._PICKED_CHOSEN_HERE,
        "_LIVE_RESUME": launcher._LIVE_RESUME,
        "_TEAM_PREMISES": launcher._TEAM_PREMISES,
        "_ON": launcher._ON,
        "_NOT_CONTAINED": launcher._NOT_CONTAINED,
        "_HOLD_LINE": launcher._HOLD_LINE,
        "_HOLD_THIN": launcher._HOLD_THIN,
        "_HOLD_REFUSED": launcher._HOLD_REFUSED,
        "_HOLD_CHECKING": launcher._HOLD_CHECKING,
        "_HOLD_GONE": launcher._HOLD_GONE,
        "_PRESSED_PAST": launcher._PRESSED_PAST,
        "_FAILED_NOT_HERE": launcher._FAILED_NOT_HERE,
        "_FAILED_LOADED": launcher._FAILED_LOADED,
        "_FAILED_KEPT": launcher._FAILED_KEPT,
        "floor_refusal(below)": launcher.floor_refusal(_below()) or "",
        "cap line": launcher._cap_line(4),
        "resume: _BRIEFING": resume_form._BRIEFING,
        "resume: _NO_THIN_HOLD": resume_form._NO_THIN_HOLD,
        "resume: _TEAM_UNKNOWN": resume_form._TEAM_UNKNOWN,
        "resume: TITLE_IS_LABEL": resume_form.TITLE_IS_LABEL,
        "resume: _FAILED_KEPT": resume_form._FAILED_KEPT,
        "resume: HOLD_UNRECORDED": resume_form.HOLD_UNRECORDED,
        "resume: HOLD_GONE": resume_form.HOLD_GONE,
    }
    for rung in Policy:
        dial = launcher.dial_text(rung, rung)
        texts[f"{rung.value} adds"] = dial.adds
        texts[f"{rung.value} parks"] = dial.parks
    missing = sorted(name for name, text in texts.items() if text not in reachable)
    assert missing == []
    # The ones whose words carry a path or a reason, by their fixed part.
    for fragment in (
        "refused for the working directory above -- this launch will use the ordinary gate.",
        "refused for this session's directory -- the resume is held",
        "contained: Bash and anything it starts run in a sandbox",
        "file writes are held to",
        "Every sub-agent gets that same boundary",
        "where this session was recorded, NOT the working directory above",
        "as that path reads",
        "is no longer a directory -- launch is held",
        "this session recorded no directory -- it runs in the working directory above",
        "no boundary is stated:",
        "did not start a session: the CLI reports 2.1.9",
        "set aside by a restore are kept. bringing the last one back",
        "that resume did not start: the CLI reports 2.1.9",
        "1 more launch(es) did not start",
    ):
        assert fragment in joined, fragment


def test_no_line_a_dialog_draws_by_itself_wraps_or_runs_past_one_row(
    context: None, monkeypatch: pytest.MonkeyPatch, roots: Path
) -> None:
    """
    Rule one: under a control, one line. Every notice either dialog draws is one
    line of text, and every one whose words are fixed -- not a path, not a reason
    another part of the application gave -- fits the row at the default size with
    its mark beside it, by count of the body face's characters.
    """
    fixed = {
        launcher.LOUD_LINE,
        launcher.NO_BOUNDARY,
        launcher.RUNS_GONE,
        launcher.RUNS_UNRECORDED,
        launcher._LIVE_LINE,
        launcher._TASK_CAPTION,
        launcher._MESSAGE_CAPTION,
        resume_form.RUNS_BY_LINK,
        resume_form.RUNS_GONE,
        resume_form.RUNS_UNRECORDED,
        resume_form._FILLED_CAPTION,
        resume_form._MESSAGE_CAPTION,
        *[launcher.REFUSED_RUNS_LOWER.format(asked=r.value, granted="strict") for r in Policy],
        *[launcher.REFUSED_HOLDS_RESUME.format(rung=r.value) for r in Policy],
        *[
            launcher.CLI_HOLDS.format(rung=r.value, what=what)
            for r in Policy
            for what in ("launch", "the resume")
        ],
        *[t.description for t in templates.BUILT_IN],
    }
    for line in fixed:
        assert len(line) <= launcher.LINE_CHARS + 8, line
    beside_buttons = {*launcher.HOLD_LINES.values(), *resume_form.HOLD_LINES.values()}
    for line in beside_buttons:
        assert len(line) <= 40, line

    for name, open_dialog in _every_dialog(monkeypatch, roots):
        frames = open_dialog()
        frames.frame(4)
        for said in frames.notices:
            assert "\n" not in said.line or said.tone is Tone.DANGER, (name, said.line)
        monkeypatch.undo()


def test_the_alarm_is_the_one_loud_line_and_only_on_a_top_rung_that_will_start(
    context: None, monkeypatch: pytest.MonkeyPatch, roots: Path
) -> None:
    loud: dict[str, list[str]] = {}
    for name, open_dialog in _every_dialog(monkeypatch, roots):
        frames = open_dialog()
        frames.frame(4)
        loud[name] = [said.line for said in frames.notices if said.tone is Tone.LOUD]
        monkeypatch.undo()
    for name, lines in loud.items():
        assert lines in ([], [launcher.LOUD_LINE]), name
    granted = {name for name, lines in loud.items() if lines}
    assert "launcher new-autonomous" in granted
    assert "launcher picked-elsewhere-autonomous" in granted
    assert "resume autonomous" in granted and "resume autonomous-live" in granted
    for refused in (
        "launcher new-autonomous-refused",
        "launcher new-autonomous-cli-below",
        "launcher picked-refused",
        "launcher picked-gone-autonomous",
        "resume autonomous-refused",
        "resume autonomous-cli-below",
        "resume gone",
        "resume unrecorded",
        "launcher new-strict",
        "launcher new-permissive",
    ):
        assert refused not in granted, refused


# -- the session file, read at the pick ----------------------------------------------


def test_read_recorded_reads_what_the_session_file_says_and_nothing_else() -> None:
    assert launcher.read_recorded("never-written") is None
    written = sessionfile.update(
        "recorded-one",
        {"model": "claude-opus-5", "template": "feature", "subagent_cap": 2},
    )
    assert isinstance(written, sessionfile.Written)
    assert launcher.read_recorded("recorded-one") == launcher.Recorded(
        "claude-opus-5", "feature", 2
    )


# -- on the line's baseline ----------------------------------------------------------
# The question mark is a link (``widgets.link``), which sits on the baseline of the
# text it follows: measured at each line that ends in one, as the code that draws
# the line draws it, at the faces tests/test_small_button.py measures its buttons at.

# The fixture of that file, under its own name here: a context in the UI's face at
# 13, 16 and 20 px, under the dock style.
faced = _buttons.faced


def _linked(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every text, button and link a frame draws, in order, with its baseline."""
    seen = _buttons._baselines(monkeypatch)
    real = widgets.link

    def link(label: str, **kwargs: Any) -> bool:
        answer = real(label, **kwargs)
        seen.append(_buttons._Seen("link", _buttons._text_baseline()))
        return answer

    monkeypatch.setattr(widgets, "link", link)
    return seen


@pytest.mark.parametrize("tone", [Tone.QUIET, Tone.LOUD], ids=["quiet", "loud"])
def test_a_notices_question_mark_is_on_its_lines_baseline(
    faced: None, monkeypatch: pytest.MonkeyPatch, tone: Tone
) -> None:
    """The (?) after a notice, on its own line or on the loud band."""
    seen = _linked(monkeypatch)
    said = Notice("nothing runs unattended", tone, Help("m", "why", ("so",)))

    def draw() -> dict[str, float]:
        seen.clear()
        widgets.notice(said)
        (text,) = [item for item in seen if item.name in ("text_disabled", "text_colored")]
        (mark,) = [item for item in seen if item.name == "link"]
        return {"apart": mark.baseline - text.baseline}

    assert _buttons._lines(draw)["apart"] == 0.0


def _command_warnings() -> Callable[[], None]:
    from pptmstr.ui import command_view

    lines = (command_view.Line("the first warning"), command_view.Line("the second"))
    return lambda: command_view._warnings(lines, "warned", ("why",))


def _inbox_release() -> Callable[[], None]:
    from pptmstr.ui import inbox
    from tests import test_command_rows

    rows = test_command_rows.rows(opened="c1")
    return lambda: inbox._release(rows, "c1", lambda _: None, True)  # type: ignore[arg-type]


def _resume_fact() -> Callable[[], None]:
    explained = Help("title", "this session's title", ("so",))
    return lambda: resume_form._fact("title", "rebuild the parser", explained)


def _resume_facts(tmp: Path) -> Callable[[], None]:
    """The whole block: a title fact, a directory fact and the unknown team's combo."""
    forms, _ = _resume_states(tmp)["label-and-unknown-team"]
    form = forms.form
    assert form is not None
    reading = form.refresh(checkout=INSIDE_THE_CHECKOUT)
    return lambda: resume_form._facts_block(form, reading, frozenset())


def _launcher_picked(tmp: Path) -> Callable[[], None]:
    state, _ = _launcher_states(tmp)["picked-here-strict"]
    return lambda: launcher._picked_lines(state, state.cwd, frozenset())


# Each line that ends in a question mark, drawn by the code that draws it.
_MARKED: dict[str, Callable[[Path], Callable[[], None]]] = {
    "command-warnings": lambda _: _command_warnings(),
    "inbox-release": lambda _: _inbox_release(),
    "resume-fact": lambda _: _buttons._in_a_dialog(_resume_fact()),
    "resume-facts-block": lambda tmp: _buttons._in_a_dialog(_resume_facts(tmp)),
    "launcher-picked": lambda tmp: _buttons._in_a_dialog(_launcher_picked(tmp)),
}


@pytest.mark.parametrize("site", list(_MARKED))
def test_at_every_site_each_question_mark_is_on_its_lines_baseline(
    faced: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, site: str
) -> None:
    """The mark's baseline minus the baseline of what it follows on its line, at each one."""
    draw = _MARKED[site](tmp_path)
    seen = _linked(monkeypatch)

    def measured() -> dict[str, float]:
        seen.clear()
        draw()
        return {
            f"{at}: after {before.name}": item.baseline - before.baseline
            for at, (before, item) in enumerate(itertools.pairwise(seen), 1)
            if item.name == "link" and item.beside(before)
        }

    apart = _buttons._lines(measured)
    assert apart and set(apart.values()) == {0.0}, apart
