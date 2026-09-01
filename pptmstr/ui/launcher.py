"""
Starting a session: a modal invoked from anywhere, not a pane that is always there.

This replaces the omnibox that lived along the bottom of TRIAGE. Two things were
wrong with that arrangement, and only the second is about screen space.

The first is conceptual. The unit this application deals in is *intent per
session* -- a task, the directory it runs in, the model that runs it. An
always-present strip implies the opposite: that dispatch is an ambient property of
one arrangement of the screen. It was also absent from FOCUS entirely, so starting
work while attending to a running session meant leaving the session first. A modal
is reachable identically from both arrangements, which is what a per-session intent
actually needs.

The second is that one line of chrome bought a cramped single-line task field. The
prompt is the part of a launch worth the most care and it had the least room.

Imports imgui only for ``draw``; ``LauncherState`` and its decisions are pure, so
the parts worth testing do not need a GL context.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from imgui_bundle import imgui

from .. import brief as brief_mod
from .. import sessions as sessions_mod
from .. import templates
from ..model import LaunchSpec
from ..sessions import Overlay, SessionRow
from ..theme import P
from . import widgets

# Verified against the model-config docs at build time (design §7, trap 9). Listed
# rather than free-text so a typo cannot become a session that fails on first turn.
MODELS: tuple[str, ...] = (
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-haiku-4-5-20251001",
    "claude-fable-5",
)

TITLE = "New Task"

# Ctrl+N. Key members are plain ints here and do not support ``|`` as enums, so the
# chord is built from int() -- ``imgui.Key.mod_ctrl | imgui.Key.n`` raises.
CHORD = int(imgui.Key.mod_ctrl) | int(imgui.Key.n)

_WIDTH = 620.0
_TASK_HEIGHT = 132.0
_LIST_HEIGHT = 176.0

# ``###`` fixes the header's ID while its visible label changes with the pick, so
# naming the picked session does not slam the section shut mid-read.
_RESUME_ID = "###resume"

_MINUTE = 60.0
_HOUR = 3600.0
_DAY = 86400.0


def age_label(last_modified_ms: int, now: float) -> str:
    """
    How long ago, in the coarsest unit that still says something.

    ``last_modified`` is epoch **milliseconds** (see ``sessions.SessionRow``), and
    the division is the entire reason this is a function rather than a format
    string. Compared against ``time.time()`` unscaled, every session dates about
    fifty thousand years into the future -- far enough out that it reads as a
    corrupt transcript rather than as a units mistake, so the bug would be chased
    in the wrong module.

    A future timestamp is reported as "just now" rather than as a negative age. A
    clock that moved, or a transcript written on another machine, makes "now" wrong;
    "-3h ago" would make the *session* look wrong, which is the more expensive lie
    on a screen whose whole job is deciding which session to trust.
    """
    seconds = now - last_modified_ms / 1000.0
    if seconds < _MINUTE:
        return "just now"
    if seconds < _HOUR:
        return f"{int(seconds // _MINUTE)}m ago"
    if seconds < _DAY:
        return f"{int(seconds // _HOUR)}h ago"
    return f"{int(seconds // _DAY)}d ago"


def scan_sessions(cwd: str) -> tuple[tuple[SessionRow, ...], Overlay]:
    """
    The blocking half of the picker, named so that what must not run on the draw
    thread has somewhere obvious to be looked at.

    ``enumerate_sessions(cwd=...)`` and not ``same_cwd``. The two narrowings differ
    on git worktrees: the SDK's ``directory`` folds a directory's worktrees in,
    ``same_cwd`` is an exact string match. The fold is what a *recovery* picker
    wants -- work done in a worktree of this repo is this work, and the operator
    hunting for it does not think of it as a different project. The exact match is
    also unusable here for a second reason: the launcher's cwd defaults to ``"."``
    while ``SessionRow.cwd`` is the absolute path the CLI recorded, so ``same_cwd``
    would return nothing at all for the default. This repo has no worktrees, so the
    two have never been observed to disagree and this is a choice made on the
    reasoning rather than on a measurement.

    Errors are not caught here. ``enumerate_sessions`` refuses to turn an unreadable
    transcript tree into "no sessions", and swallowing that on the way past would
    undo it -- ``SessionPicker.poll`` renders the failure instead.
    """
    overlay = sessions_mod.load_overlay()
    rows = sessions_mod.enumerate_sessions(cwd=cwd, overlay=overlay, bookmarked_first=True)
    return rows, overlay


# What a worker hands back: rows and overlay, or the message from the failure that
# stopped it. Exactly one side is populated.
_ScanResult = tuple[tuple[SessionRow, ...] | None, Overlay | None, str | None]

Scanner = Callable[[str], tuple[tuple[SessionRow, ...], Overlay]]


@dataclass
class SessionPicker:
    """
    The resume list: one cached scan, the operator's overlay, and which row is picked.

    The cache is the whole design. ``sessions.enumerate_sessions`` opens, stats and
    head/tail-reads every transcript in the project and shells out to ``git
    worktree``; it is tens of milliseconds against a warm page cache here and
    unbounded cold, and a draw call paying that would stall every frame it ran on.
    So the scan runs on a worker and returns through a queue, and everything the
    draw does afterwards is arithmetic over the tuple the worker left behind.

    Nothing is shared mutable state across the two threads. The worker touches only
    the queue; ``poll`` -- called from the draw -- is the only writer of the fields.

    The scan starts when the modal opens, not when the section is expanded. It is on
    a worker either way, so a fresh launch cannot feel the difference; what deferring
    it bought was a click and a wait at the one moment the operator is hunting for
    lost work. See ``LauncherState.begin_open``.

    Opening early means the cwd field can move after the scan was fired, so the
    section asks again when it is expanded against a directory the last scan did not
    answer for. See ``set_expanded``.
    """

    rows: tuple[SessionRow, ...] = ()
    overlay: Overlay = field(default_factory=dict)
    # The message from a scan that failed, or None. Rendered rather than swallowed:
    # an empty picker shown to an operator hunting for lost work is
    # indistinguishable from "you have no sessions", which is the worst outcome this
    # feature can produce. Rows from the last good scan are kept alongside it.
    error: str | None = None
    scanning: bool = False
    # The directory the cached rows were scanned for, so the list can say which
    # question it is answering rather than leaving it to be assumed.
    scanned_cwd: str = ""
    # The cwd field's value as ``request_scan`` was handed it, unresolved. Compared
    # against the field to decide whether the listing still answers the question the
    # operator is asking. Raw rather than ``scanned_cwd`` because the comparison
    # happens in a draw call and ``realpath`` is a stat per component.
    requested_cwd: str = ""
    # Whether the resume section was open on the previous frame, and the directory an
    # expand asked for that no scan has been started for yet. Both are written only by
    # ``set_expanded``.
    expanded: bool = False
    pending_cwd: str | None = None
    selected: str | None = None
    # The brief directory the picked session wrote its premises into, if it exists.
    #
    # Recoverable, and not a guess: `brief.session_dir` is a function of cwd and
    # session id, and resume continues under the same id. It is the one thing about
    # the original session that does come back -- `task`, `model` and `template` do
    # not, because there is no `AgentRecord` on the far side of the CLI.
    recovered_brief: str | None = None
    _inbox: queue.SimpleQueue[_ScanResult] = field(default_factory=queue.SimpleQueue, repr=False)

    def request_scan(
        self, cwd: str, *, scanner: Scanner = scan_sessions
    ) -> threading.Thread | None:
        """
        Start a scan on a worker thread. Returns without waiting for it.

        A request made while one is already in flight is dropped rather than queued.
        Two scans of the same tree cannot disagree usefully, and the second would
        land after the first and re-order rows under a cursor that had stopped
        moving.

        The worker is returned so a caller that genuinely has to wait -- a test --
        can join it. The draw ignores it and must: waiting is the thing this exists
        to avoid.
        """
        if self.scanning:
            return None
        # Resolved here, and the resolved form is what the worker is given. The SDK
        # canonicalises ``directory`` itself, so this changes nothing about which
        # sessions come back -- it is so the list can *say* what it searched. The
        # launcher's cwd defaults to ``"."``, and a picker captioned "sessions for ."
        # answers a question the operator did not ask. One realpath on a click.
        self.requested_cwd = cwd
        cwd = os.path.realpath(cwd)
        self.scanning = True
        self.error = None
        self.scanned_cwd = cwd
        inbox = self._inbox

        def work() -> None:
            try:
                rows, overlay = scanner(cwd)
            except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
                # Caught at the thread boundary because an escaping exception here
                # goes to stderr and leaves ``scanning`` true forever, which renders
                # as a picker that spins and never says why. The message is put on
                # screen instead.
                inbox.put((None, None, f"{type(exc).__name__}: {exc}"))
                return
            inbox.put((rows, overlay, None))

        worker = threading.Thread(target=work, name="pptmstr-session-scan", daemon=True)
        worker.start()
        return worker

    def set_expanded(
        self, expanded: bool, cwd: str, *, scanner: Scanner = scan_sessions
    ) -> threading.Thread | None:
        """
        Tell the picker its section is open, and for which directory. Rescans if the
        listing is for a different one.

        The scan fires when the modal opens, which is before the operator has had the
        chance to type the directory they pressed Ctrl+N to work in. Without this the
        listing would be for wherever the *last* launch went, and the whole point of
        the cwd field is that those differ -- so the picker would confidently answer
        the wrong question at the moment its answer is trusted most.

        Only the expand re-asks, not every edit: the cwd field is on screen beside an
        expanded section, and comparing against it per frame would put a transcript
        scan behind every keystroke. Editing after expanding leaves a listing the
        caption still names honestly, with ``rescan`` beside it.

        A request that arrives while the modal-open scan is still in flight is held
        rather than dropped -- ``request_scan`` drops the second of two, and dropping
        this one would restore exactly the wrong-directory listing it exists to
        replace. It starts on the frame the earlier scan lands.

        Returns the worker so a test can join it; the draw ignores it and must.
        """
        if expanded and not self.expanded and cwd != self.requested_cwd:
            self.pending_cwd = cwd
        self.expanded = expanded
        if self.pending_cwd is None or self.scanning:
            return None
        pending, self.pending_cwd = self.pending_cwd, None
        return self.request_scan(pending, scanner=scanner)

    def poll(self) -> None:
        """
        Take a finished scan if one has landed. Costs a non-blocking read otherwise.
        """
        try:
            rows, overlay, error = self._inbox.get_nowait()
        except queue.Empty:
            return
        self.scanning = False
        if error is not None:
            # The previous rows stay. A listing that is a minute stale is worth more
            # than a blank one, and the error beside it says which it is.
            self.error = error
            return
        self.rows = rows or ()
        self.overlay = overlay or {}
        self.error = None

    def toggle_bookmark(self, target: SessionRow) -> None:
        """
        Flip a bookmark, persist it, and update the row in place.

        Takes the row rather than an id so that what is being flipped *from* is the
        state that was drawn. The overlay is the other candidate and it is the wrong
        one: it and the rows are joined at scan time and can only diverge afterwards,
        and on a divergence the operator's click should mean what the button under
        their cursor said, not what a map they cannot see thinks.

        Persisted at the click as the theme and wrap preferences are, rather than at
        exit: a bookmark that survives only a clean shutdown is a bookmark lost by
        the crash it was made to protect against.

        Deliberately does **not** re-sort. Bookmarked-first is the order the list was
        scanned in, not an invariant held under the cursor. Re-sorting on click would
        slide the next row under a mouse still resting on the button that moved it,
        and the operator's following click would land on a session they never read.
        The order settles on the next scan.
        """
        wanted = not target.bookmarked
        self.overlay = sessions_mod.set_bookmark(self.overlay, target.session_id, wanted)
        sessions_mod.save_overlay(self.overlay)
        self.rows = tuple(
            replace(row, bookmarked=wanted) if row.session_id == target.session_id else row
            for row in self.rows
        )

    def select(self, session_id: str | None, cwd: str) -> None:
        """
        Pick a session to resume, or clear the pick. Re-picking the current one clears.

        ``cwd`` is this launch's working directory rather than the row's, because the
        brief directory that matters is the one the resumed session will actually
        derive -- ``app._seed_brief`` builds it from the same two values, so the two
        agree by construction instead of by coincidence.
        """
        self.selected = None if session_id is None or session_id == self.selected else session_id
        self.recovered_brief = None
        if self.selected is None:
            return
        directory = brief_mod.session_dir(brief_mod.default_root(), cwd or ".", self.selected)
        # One stat, at the click. Offering a path to a directory that does not exist
        # would be the same lie as pre-filling the task.
        if directory.is_dir():
            self.recovered_brief = str(directory)

    @property
    def selected_row(self) -> SessionRow | None:
        for row in self.rows:
            if row.session_id == self.selected:
                return row
        return None


@dataclass
class LauncherState:
    """
    Draft intent for a session that does not exist yet.

    The draft survives a cancel on purpose: dismissing the modal to go look up a
    directory should not cost the paragraph already typed. It is cleared on launch,
    which is the only point at which the intent has gone somewhere.
    """

    task: str = ""
    cwd: str = "."
    # Path to a directory of premises, or empty for a session with only its task.
    # Optional on purpose: most sessions are solo with a one-line task, and making
    # a brief mandatory would tax the common case for a problem it does not have.
    brief: str = ""
    model_index: int = 0
    # Index into templates.names(). "solo" is first and is the default, so the
    # launcher behaves exactly as it did before teams existed unless asked otherwise.
    template_index: int = 0
    # True from the frame the modal is drawn until the frame it stops being drawn.
    # Read by the global key handler, which runs before any drawing and therefore
    # sees the previous frame's value -- which is the correct one, because the key
    # it is deciding about was pressed while that frame was on screen.
    is_open: bool = False
    # The resume list and the pick made in it. Lives on the draft rather than beside
    # it because a picked session is part of the intent -- it is what decides whether
    # the task typed above starts a conversation or continues one.
    picker: SessionPicker = field(default_factory=SessionPicker)
    _open_requested: bool = False
    _focus_task: bool = False

    def request_open(self) -> None:
        """Ask for the modal next time it draws. Safe to call when already open."""
        if not self.is_open:
            self._open_requested = True

    def begin_open(self, *, scanner: Scanner = scan_sessions) -> threading.Thread | None:
        """
        Consume a pending open request. The modal is up from this frame on.

        The session scan starts here rather than when the resume section is expanded.
        It runs on a worker and never touches a draw call, so a fresh launch -- still
        the common case and still the default -- cannot feel it either way. What
        deferring it to the expand did cost was a click and a wait at the one moment
        the operator is hunting for lost work, which is the moment the picker exists
        for. The section itself stays collapsed; only the rows behind it are ready.

        Every open rescans. A listing from an hour ago is not a listing of what is
        there, and the picker is read by someone deciding which session to trust.

        Returns the worker so a caller that genuinely has to wait -- a test -- can
        join it. The draw ignores it and must.
        """
        self._open_requested = False
        self.is_open = True
        self._focus_task = True
        return self.picker.request_scan(self.cwd.strip() or ".", scanner=scanner)

    @property
    def ready(self) -> bool:
        return bool(self.task.strip())

    def spec(self) -> LaunchSpec:
        """
        The draft as the value the driver is handed. Empty cwd means the repo root.

        A ``LaunchSpec`` rather than a tuple of strings, which is the correction row
        9 paid for: the callback took four loose positionals, so dropping one still
        typechecked and a relaunched team started solo. Adding the brief to a
        positional list would be the same defect waiting on the same signature.

        ``resume`` is the picked session or None, and None is the untouched default:
        a draft nobody opened the resume section on produces exactly the spec it did
        before the section existed.
        """
        return LaunchSpec(
            task=self.task.strip(),
            model=MODELS[self.model_index],
            cwd=self.cwd.strip() or ".",
            template=templates.names()[self.template_index],
            brief=self.brief.strip() or None,
            resume=self.picker.selected,
        )


def handle_shortcut(state: LauncherState) -> None:
    """
    Ctrl+N, from either layout and from inside a text field.

    ``route_over_active`` is the load-bearing flag. The other keyboard handlers in
    this application bail on ``want_capture_keyboard``, which is right for bare
    letter keys -- typing "a" into a rejection reason must not approve anything --
    but it would make this shortcut dead in exactly the place it is most wanted,
    the reply box of a session that just prompted the operator for something new.

    Must be called inside a frame, which is why it does not live with the other
    shortcut handling in ``begin_frame``.
    """
    if imgui.shortcut(CHORD, imgui.InputFlags_.route_global | imgui.InputFlags_.route_over_active):
        state.request_open()


def _session_row(picker: SessionPicker, row: SessionRow, cwd: str, now: float) -> None:
    """
    One line: bookmark toggle, then a selectable spanning the rest of the row.

    Label, branch and age go into a single selectable string rather than a selectable
    followed by dimmed metadata. A selectable sized to its own text leaves the branch
    and the age outside the hit box, and those are the parts an operator reads last
    and clicks from -- a dead half-row is worse than undifferentiated colour.
    """
    imgui.push_id(row.session_id)
    # Colour carries the state as well as the glyph. At this point size "•" and "+"
    # differ by a few pixels of ink, which is not a difference an operator scanning a
    # list of thirty rows for their own bookmarks can act on.
    if row.bookmarked:
        imgui.push_style_color(imgui.Col_.text, P.accent.vec4)
    if imgui.small_button("*" if row.bookmarked else "+"):
        picker.toggle_bookmark(row)
    if row.bookmarked:
        imgui.pop_style_color()
    imgui.set_item_tooltip("remove bookmark" if row.bookmarked else "bookmark this session")
    imgui.same_line()

    meta = "  ·  ".join(p for p in (row.git_branch, age_label(row.last_modified, now)) if p)
    tail = f"  ·  {meta}" if meta else ""
    # ``label`` can be a first prompt, which is a whole paragraph with newlines in
    # it. Flattened before measuring, or the row grows to the height of the prompt.
    head = " ".join(row.label.split())
    room = imgui.get_content_region_avail().x - imgui.calc_text_size(tail).x
    picked, _ = imgui.selectable(
        f"{widgets.ellipsis(head, max(80.0, room))}{tail}", row.session_id == picker.selected
    )
    if picked:
        picker.select(row.session_id, cwd)
    imgui.pop_id()


def _resume_section(state: LauncherState, *, scanner: Scanner) -> None:
    """
    The resume picker: collapsed until asked for, over rows already being scanned.

    The header starts shut so a fresh launch looks exactly as it did before this
    section existed, but ``LauncherState.begin_open`` put the scan in flight when the
    modal opened -- so unless the cwd field has moved since, expanding costs a header
    toggle and no disk read, and by the time an operator has read this far the rows
    are usually already here. When it has moved, expanding starts a scan for the
    directory now in the field; see ``SessionPicker.set_expanded``.
    """
    picker = state.picker
    picker.poll()

    imgui.spacing()
    row = picker.selected_row
    heading = (
        "resume an existing session" if row is None else f"resuming: {' '.join(row.label.split())}"
    )
    cwd = state.cwd.strip() or "."
    expanded = imgui.collapsing_header(f"{widgets.ellipsis(heading, _WIDTH - 60.0)}{_RESUME_ID}")
    picker.set_expanded(expanded, cwd, scanner=scanner)
    if not expanded:
        return

    imgui.text_disabled(f"sessions in {picker.scanned_cwd or cwd} and its git worktrees")
    if picker.error is not None:
        # Loud, and never a silently empty list. Someone reading this screen has
        # already lost work once.
        imgui.text_colored(P.danger.vec4, f"could not read the transcripts: {picker.error}")

    now = time.time()
    if imgui.begin_child("##sessions", imgui.ImVec2(-1.0, _LIST_HEIGHT), imgui.ChildFlags_.borders):
        if picker.scanning and not picker.rows:
            imgui.text_disabled("reading transcripts...")
        elif not picker.rows and picker.error is None:
            imgui.text_disabled("no sessions recorded for this directory")
        for entry in picker.rows:
            _session_row(picker, entry, cwd, now)
    imgui.end_child()

    if imgui.small_button("rescan") and not picker.scanning:
        picker.request_scan(cwd, scanner=scanner)
    if row is not None:
        imgui.same_line()
        if imgui.small_button("launch fresh instead"):
            picker.select(None, cwd)
        # The whole of what resume does and does not restore, on screen rather than
        # in a doc. The conversation comes back; nothing that lived in an
        # ``AgentRecord`` does, because there is no record on the far side of the CLI.
        imgui.text_colored(
            P.accent.vec4,
            "the conversation comes back and the task above is sent to it as the next message",
        )
        imgui.text_disabled(
            "model, team and task are this launch's -- they are not read back from the session"
        )
        if picker.recovered_brief is not None and picker.recovered_brief != state.brief.strip():
            # The one exception, and only because the session id does not move:
            # `brief.session_dir` derives the same directory it derived before.
            if imgui.small_button("use this session's brief"):
                state.brief = picker.recovered_brief
            imgui.same_line()
            # Clipped rather than allowed to run off the edge: a brief path is a
            # project slug and a uuid and is wider than the modal on every machine.
            imgui.text_disabled(
                widgets.ellipsis(picker.recovered_brief, imgui.get_content_region_avail().x)
            )


def draw(
    state: LauncherState,
    *,
    running: int,
    queued: int,
    cap: int,
    launch: Callable[[LaunchSpec], None],
    wrap: bool,
    scanner: Scanner = scan_sessions,
) -> None:
    """
    Draw the modal if it has been asked for.

    Belongs at root level -- ``post_render_dockable_windows`` -- rather than inside
    a panel. A popup opened from a docked window is scoped to that window, and the
    panel set differs between the two layouts, so a launcher parented to a TRIAGE
    pane would vanish on the switch to FOCUS.
    """
    if state._open_requested:
        state.begin_open(scanner=scanner)
        imgui.open_popup(TITLE)

    if not state.is_open:
        return

    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
    imgui.set_next_window_size(imgui.ImVec2(_WIDTH, 0.0), imgui.Cond_.appearing)
    opened, _ = imgui.begin_popup_modal(TITLE, None, imgui.WindowFlags_.always_auto_resize)
    if not opened:
        # Dismissed by something other than this function -- a click on the blocked
        # background, or ImGui's own Escape handling. Resync so the key handler
        # stops deferring to a modal that is gone.
        state.is_open = False
        return

    if state._focus_task:
        imgui.set_keyboard_focus_here()
        state._focus_task = False

    # The same chord the reply boxes use: Ctrl+Enter launches, Enter breaks the
    # line. Sharing it is the point -- a task box and a reply box are both prompts,
    # and the binding that differed here was the one whose misfire costs the most,
    # since a stray Enter mid-sentence spawns a session on half a sentence.
    # escape_clears_all is deliberately absent on top of it: it would eat the key
    # that dismisses the modal.
    submitted, state.task = widgets.multiline_input(
        "##task",
        state.task,
        imgui.ImVec2(-1, _TASK_HEIGHT),
        wrap=wrap,
        flags=widgets.CTRL_ENTER_SUBMITS,
    )
    imgui.text_disabled("what should a new session do?  Ctrl+Enter launches, Enter for a new line")
    # Said here rather than left to be discovered, because it changes what the box is
    # for: on a team, this text is the premises every worker is told to read, not
    # just the lead's opening message.
    if templates.BUILT_IN[state.template_index].roles:
        imgui.text_disabled("on a team, this also becomes the premises every worker reads")

    imgui.spacing()
    imgui.separator()
    imgui.spacing()

    # The working directory is the field worth the label. An orchestrator whose
    # agents all share the launching shell's cwd cannot run work across projects,
    # which is most of the point -- and it is the FLEET rail's grouping key, so a
    # typo does not fail, it quietly files the session under a project that does
    # not exist.
    imgui.set_next_item_width(-1)
    _, state.cwd = imgui.input_text_with_hint(
        "##cwd", "working directory (agents run here)", state.cwd
    )
    imgui.text_disabled("working directory")

    # After the directory because it is scoped by it, and before the brief because it
    # can fill the brief in -- the two fields it touches are the ones either side.
    _resume_section(state, scanner=scanner)

    # **Not how a brief is created.** A team session writes the task above as its
    # first premise at launch, because that is where the operator's premises already
    # are -- this field is for pointing a session at one that exists, which is what a
    # fork continuing its parent's work needs. Filling it in *suppresses* the seed,
    # so a session told to continue a brief does not bury it under a new one.
    #
    # Empty is the common case and must stay cheap to leave empty.
    imgui.spacing()
    imgui.set_next_item_width(-1)
    _, state.brief = imgui.input_text_with_hint(
        "##brief", "continue an existing brief (optional) -- leave empty for a new one", state.brief
    )
    imgui.text_disabled("brief directory")

    imgui.spacing()
    imgui.set_next_item_width(240.0)
    _, state.model_index = imgui.combo("model", state.model_index, list(MODELS))
    _, state.template_index = imgui.combo("team", state.template_index, list(templates.names()))
    # The shape is not obvious from a one-word name, and picking the wrong one is
    # only visible several turns later when workers start appearing -- so the
    # description is on screen rather than a tooltip away.
    imgui.text_disabled(templates.BUILT_IN[state.template_index].description)

    imgui.spacing()
    imgui.separator()
    imgui.spacing()

    ready = state.ready
    if not ready:
        imgui.begin_disabled()
    clicked = imgui.button("launch", imgui.ImVec2(110.0, 0.0))
    if not ready:
        imgui.end_disabled()

    imgui.same_line()
    cancelled = imgui.button("cancel", imgui.ImVec2(90.0, 0.0))

    # Only at cap, and only as a consequence. The running/cap counter itself is in
    # the status bar, which stays visible behind the modal -- repeating it here
    # would be the same duplication the omnibox was carrying. What the status bar
    # cannot say is what happens to *this* launch, and a session that sits in
    # SPAWNING while others work looks broken to whoever just pressed the button.
    if running >= cap:
        imgui.same_line()
        imgui.text_colored(P.warn.vec4, f"at cap - this one will queue ({queued} ahead of it)")

    # Escape is handled explicitly rather than left to ImGui so that the precedence
    # against the layout shortcuts is stated in one place instead of split between
    # this and NavUpdate's behaviour.
    dismissed = cancelled or imgui.is_key_pressed(imgui.Key.escape)

    if ready and (clicked or submitted):
        launch(state.spec())
        state.task = ""
        # Cleared with the task, and for the same reason it is: the pick has gone
        # somewhere. A pick that outlived its launch would silently resume the same
        # session again on the next Ctrl+N, and the header carrying it would be
        # collapsed while it did.
        state.picker.select(None, state.cwd)
        dismissed = True

    if dismissed:
        state.is_open = False
        imgui.close_current_popup()

    imgui.end_popup()
