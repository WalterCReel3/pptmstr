"""
The pure decisions behind the launcher modal.

Only the parts checkable without a GL context: what an open request does to the
state machine, what a draft turns into when it is handed to ``_launch``, and the
readiness rule that decides whether Enter does anything at all. The drawing itself
needs pixels.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from pptmstr.model import LaunchSpec
from pptmstr.sessions import Overlay, SessionMark, SessionRow, load_overlay, overlay_path
from pptmstr.ui.launcher import MODELS, LauncherState, SessionPicker, age_label


def _row(session_id: str, *, last_modified: int = 0, bookmarked: bool = False) -> SessionRow:
    return SessionRow(
        session_id=session_id,
        cwd="/srv/repo",
        git_branch="main",
        summary=f"summary for {session_id}",
        first_prompt=None,
        created_at=None,
        last_modified=last_modified,
        tag=None,
        bookmarked=bookmarked,
    )


def _loaded(*rows: SessionRow, overlay: Overlay | None = None) -> SessionPicker:
    """
    A picker holding a finished scan, without going near a real transcript tree.
    """
    picker = SessionPicker()
    worker = picker.request_scan("/srv/repo", scanner=lambda cwd: (rows, overlay or {}))
    assert worker is not None
    worker.join(timeout=5.0)
    picker.poll()
    return picker


def test_starts_closed_with_no_request() -> None:
    state = LauncherState()
    assert not state.is_open
    assert not state._open_requested


def test_request_open_is_deferred_not_immediate() -> None:
    """
    The flag is set now and consumed by the draw. ``is_open`` must not flip here:
    the layout key handler reads it before any drawing, and a modal that claimed to
    be open a frame early would swallow an Esc meant for the layout.
    """
    state = LauncherState()
    state.request_open()
    assert state._open_requested
    assert not state.is_open


def test_request_open_while_open_is_a_no_op() -> None:
    """
    Ctrl+N pressed with the modal already up must not re-issue ``open_popup``.
    Reopening resets the window position, which would yank a half-typed task box
    back to the centre of the viewport.
    """
    state = LauncherState(is_open=True)
    state.request_open()
    assert not state._open_requested


def test_opening_the_modal_starts_the_scan_before_the_section_is_expanded(
    tmp_path: Path,
) -> None:
    """
    The scan is on a worker and never on a draw call, so deferring it until the
    resume section was expanded made a fresh launch no faster. It only bought a click
    and a wait at the one moment the operator is hunting for lost work. Opening the
    modal is what starts it; the section itself is still collapsed.
    """
    seen: list[str] = []

    def scanner(cwd: str) -> tuple[tuple[SessionRow, ...], Overlay]:
        seen.append(cwd)
        return (_row("a"),), {}

    state = LauncherState(cwd=str(tmp_path))
    state.request_open()
    worker = state.begin_open(scanner=scanner)
    assert worker is not None
    worker.join(timeout=5.0)
    state.picker.poll()

    assert state.is_open
    assert not state._open_requested
    # The launcher's directory, not the process's -- the picker is scoped by the cwd
    # field above it.
    assert seen == [str(tmp_path.resolve())]
    assert [row.session_id for row in state.picker.rows] == ["a"]


def test_reopening_the_modal_rescans_rather_than_reusing_the_last_listing() -> None:
    """
    A listing from an hour ago is not a listing of what is there, and this screen is
    read by someone deciding which session to trust.
    """
    scans: list[str] = []

    def scanner(cwd: str) -> tuple[tuple[SessionRow, ...], Overlay]:
        scans.append(cwd)
        return (), {}

    state = LauncherState()
    for _ in range(2):
        state.request_open()
        worker = state.begin_open(scanner=scanner)
        assert worker is not None
        worker.join(timeout=5.0)
        state.picker.poll()
        # What the draw does on cancel or launch.
        state.is_open = False

    assert len(scans) == 2


def _recording_scanner(seen: list[str]) -> Callable[[str], tuple[tuple[SessionRow, ...], Overlay]]:
    """
    A scanner that logs the directory it was asked for and answers with one row named
    after it, so a listing can be traced back to the question that produced it.
    """

    def scanner(cwd: str) -> tuple[tuple[SessionRow, ...], Overlay]:
        seen.append(cwd)
        return (_row(cwd),), {}

    return scanner


def test_expanding_after_the_cwd_field_moved_scans_the_directory_now_in_the_field(
    tmp_path: Path,
) -> None:
    """
    The regression this pins: Ctrl+N, type the project you meant, expand resume. The
    scan went out when the modal opened, so without re-asking the operator is handed
    the *previous* directory's sessions at the moment they are hunting for lost work
    -- and working across projects is the axis this tool is sold on.
    """
    other = tmp_path / "other"
    other.mkdir()
    seen: list[str] = []
    scanner = _recording_scanner(seen)

    state = LauncherState(cwd=str(tmp_path))
    state.request_open()
    opening = state.begin_open(scanner=scanner)
    assert opening is not None
    opening.join(timeout=5.0)
    state.picker.poll()

    # The operator types a different project into the cwd field, then expands.
    state.cwd = str(other)
    worker = state.picker.set_expanded(True, state.cwd, scanner=scanner)
    assert worker is not None
    worker.join(timeout=5.0)
    state.picker.poll()

    assert seen == [str(tmp_path.resolve()), str(other.resolve())]
    assert [row.session_id for row in state.picker.rows] == [str(other.resolve())]


def test_expanding_without_touching_the_cwd_reuses_the_scan_the_open_started(
    tmp_path: Path,
) -> None:
    """
    The common case must stay free. Expanding over an unchanged field costs a header
    toggle and no disk read, which is the whole reason the scan moved to modal-open.
    """
    seen: list[str] = []
    scanner = _recording_scanner(seen)

    state = LauncherState(cwd=str(tmp_path))
    state.request_open()
    opening = state.begin_open(scanner=scanner)
    assert opening is not None
    opening.join(timeout=5.0)
    state.picker.poll()

    assert state.picker.set_expanded(True, state.cwd, scanner=scanner) is None
    assert seen == [str(tmp_path.resolve())]


def test_typing_in_the_cwd_field_below_an_open_section_does_not_scan_per_keystroke(
    tmp_path: Path,
) -> None:
    """
    The cwd field is on screen beside an expanded section, so a per-frame comparison
    would put a transcript scan -- open, stat and head/tail-read every file in the
    project, plus a ``git worktree`` -- behind every character typed. Only the expand
    re-asks. The caption and ``rescan`` cover the edit that follows one.
    """
    seen: list[str] = []
    scanner = _recording_scanner(seen)

    state = LauncherState(cwd=str(tmp_path))
    state.request_open()
    opening = state.begin_open(scanner=scanner)
    assert opening is not None
    opening.join(timeout=5.0)
    state.picker.poll()
    state.picker.set_expanded(True, state.cwd, scanner=scanner)

    # One frame per character of a directory being typed under the open section.
    typed = ""
    for char in "/srv/elsewhere":
        typed += char
        assert state.picker.set_expanded(True, typed, scanner=scanner) is None

    assert seen == [str(tmp_path.resolve())]


def test_collapsing_and_reopening_the_section_re_asks_for_the_new_directory(
    tmp_path: Path,
) -> None:
    """
    The expand is the trigger, so an edit made under an open section is picked up by
    shutting the header and opening it again -- without which the only route back to
    a correct listing would be the ``rescan`` button.
    """
    other = tmp_path / "other"
    other.mkdir()
    seen: list[str] = []
    scanner = _recording_scanner(seen)

    state = LauncherState(cwd=str(tmp_path))
    state.request_open()
    opening = state.begin_open(scanner=scanner)
    assert opening is not None
    opening.join(timeout=5.0)
    state.picker.poll()

    state.picker.set_expanded(True, state.cwd, scanner=scanner)
    state.cwd = str(other)
    assert state.picker.set_expanded(True, state.cwd, scanner=scanner) is None
    state.picker.set_expanded(False, state.cwd, scanner=scanner)
    worker = state.picker.set_expanded(True, state.cwd, scanner=scanner)
    assert worker is not None
    worker.join(timeout=5.0)

    assert seen == [str(tmp_path.resolve()), str(other.resolve())]


def test_an_expand_that_lands_mid_scan_is_held_rather_than_dropped(tmp_path: Path) -> None:
    """
    ``request_scan`` drops a request made while one is in flight. Dropping *this* one
    would restore the wrong-directory listing it exists to replace, and leave no
    second edge to retry on, so it is held and started when the earlier scan lands.
    """
    other = tmp_path / "other"
    other.mkdir()
    release = threading.Event()
    seen: list[str] = []

    def scanner(cwd: str) -> tuple[tuple[SessionRow, ...], Overlay]:
        seen.append(cwd)
        if not seen[1:]:
            release.wait(timeout=5.0)
        return (_row(cwd),), {}

    state = LauncherState(cwd=str(tmp_path))
    state.request_open()
    opening = state.begin_open(scanner=scanner)
    assert opening is not None

    # The field moves and the section is expanded while the opening scan is stuck.
    state.cwd = str(other)
    assert state.picker.set_expanded(True, state.cwd, scanner=scanner) is None

    release.set()
    opening.join(timeout=5.0)
    state.picker.poll()

    # The frame after the opening scan lands.
    worker = state.picker.set_expanded(True, state.cwd, scanner=scanner)
    assert worker is not None
    worker.join(timeout=5.0)
    state.picker.poll()

    assert seen == [str(tmp_path.resolve()), str(other.resolve())]
    assert [row.session_id for row in state.picker.rows] == [str(other.resolve())]


def test_the_field_is_compared_unresolved_so_no_realpath_runs_per_frame(
    tmp_path: Path,
) -> None:
    """
    ``scanned_cwd`` is resolved for the caption; ``requested_cwd`` is the raw string
    the field held. The comparison is against the raw one because it happens in a draw
    call and ``realpath`` is a stat per path component.
    """
    picker = SessionPicker()
    unresolved = str(tmp_path / "." / "")
    worker = picker.request_scan(unresolved, scanner=lambda cwd: ((), {}))
    assert worker is not None
    worker.join(timeout=5.0)
    picker.poll()

    assert picker.requested_cwd == unresolved
    assert picker.scanned_cwd == str(tmp_path.resolve())
    # And the field it came from does not read as having moved.
    assert picker.set_expanded(True, unresolved, scanner=lambda cwd: ((), {})) is None


@pytest.mark.parametrize(
    "task,ready",
    [
        ("", False),
        ("   ", False),
        ("\n\t ", False),
        ("do the thing", True),
        ("  do the thing  ", True),
    ],
)
def test_ready_ignores_whitespace_only_drafts(task: str, ready: bool) -> None:
    assert LauncherState(task=task).ready is ready


def test_spec_strips_task_and_resolves_model() -> None:
    state = LauncherState(task="  audit the parser  ", cwd="/tmp/x", model_index=1)
    assert state.spec() == LaunchSpec(
        task="audit the parser", model=MODELS[1], cwd="/tmp/x", template="solo", brief=None
    )


def test_spec_defaults_to_a_lone_agent() -> None:
    """
    Index 0 is "solo". Teams are opt-in: launching without choosing one must behave
    exactly as it did before templates existed, or every existing habit changes
    meaning at once.
    """
    assert LauncherState(task="x").spec().template == "solo"


def test_spec_carries_the_chosen_team() -> None:
    from pptmstr import templates

    state = LauncherState(task="x", template_index=templates.names().index("feature"))
    assert state.spec().template == "feature"


def test_spec_defaults_blank_cwd_to_repo_root() -> None:
    """
    An empty directory field means "here", not "". The value reaches
    ``AgentSession`` and is the FLEET rail's grouping key, so a blank string would
    file the session under a project whose name is the empty string.
    """
    assert LauncherState(task="t", cwd="   ").spec().cwd == "."


def test_spec_strips_cwd_whitespace() -> None:
    assert LauncherState(task="t", cwd="  /srv/repo \n").spec().cwd == "/srv/repo"


def test_default_model_is_first_in_the_list() -> None:
    assert LauncherState(task="t").spec().model == MODELS[0]


def test_every_model_index_is_addressable() -> None:
    """Guards against a combo whose index outruns the tuple it is drawn from."""
    for i in range(len(MODELS)):
        assert LauncherState(task="t", model_index=i).spec().model == MODELS[i]


def test_a_brief_is_optional_and_absent_by_default() -> None:
    """
    Most sessions are solo with a one-line task. A brief is the shape a team needs,
    and making it mandatory would tax the common case for a problem it does not have.
    """
    assert LauncherState(task="t").spec().brief is None


def test_a_blank_brief_is_absent_rather_than_an_empty_path() -> None:
    """
    An empty string is a path to nothing, and it would reach `AgentRecord.brief` as
    a value that reads as present. None is the only honest spelling of "no brief".
    """
    assert LauncherState(task="t", brief="   ").spec().brief is None


def test_a_brief_path_is_carried_and_stripped() -> None:
    state = LauncherState(task="t", brief="  /home/x/.claude/briefs/s1  ")
    assert state.spec().brief == "/home/x/.claude/briefs/s1"


# ---------------------------------------------------------------------------
# "how long ago"
# ---------------------------------------------------------------------------


def test_ages_are_read_as_milliseconds_not_seconds() -> None:
    """
    The one arithmetic mistake this whole surface can make.

    ``last_modified`` is epoch milliseconds. Compared against ``time.time()``
    unscaled, a session modified three hours ago dates fifty thousand years into the
    future -- which renders as a plausible-looking string, not as an error, so
    nothing downstream catches it.
    """
    now = 1_800_000_000.0
    assert age_label(int(now * 1000) - 3 * 3_600_000, now) == "3h ago"


@pytest.mark.parametrize(
    "ago_ms,label",
    [
        (0, "just now"),
        (59_000, "just now"),
        (60_000, "1m ago"),
        (3_599_000, "59m ago"),
        (3_600_000, "1h ago"),
        (86_399_000, "23h ago"),
        (86_400_000, "1d ago"),
        (9 * 86_400_000, "9d ago"),
    ],
)
def test_age_label_picks_the_coarsest_unit_that_still_says_something(
    ago_ms: int, label: str
) -> None:
    now = 1_800_000_000.0
    assert age_label(int(now * 1000) - ago_ms, now) == label


def test_a_future_timestamp_reads_as_just_now_rather_than_a_negative_age() -> None:
    """
    A moved clock makes "now" wrong. A negative age would make the *session* look
    wrong, on a screen whose only job is deciding which session to trust.
    """
    now = 1_800_000_000.0
    assert age_label(int(now * 1000) + 5 * 3_600_000, now) == "just now"


# ---------------------------------------------------------------------------
# The picker: scanning, failing, bookmarking, picking
# ---------------------------------------------------------------------------


def test_a_fresh_picker_is_idle_rather_than_empty_handed() -> None:
    """
    A picker nobody has opened the modal for holds nothing and claims nothing. The
    draw distinguishes it from an empty project by ``scanning``, which ``begin_open``
    raises before the section can be looked at.
    """
    picker = SessionPicker()
    assert picker.rows == ()
    assert not picker.scanning
    assert picker.error is None


def test_a_scan_that_found_nothing_is_not_an_error() -> None:
    picker = _loaded()
    assert picker.rows == ()
    assert picker.error is None
    assert not picker.scanning


def test_a_scan_that_raised_surfaces_the_failure_instead_of_an_empty_list() -> None:
    """
    The worst outcome this feature can produce is an empty picker shown to someone
    hunting for lost work: it is indistinguishable from "you have no sessions".
    ``enumerate_sessions`` refuses to swallow a listing error and the thread boundary
    must not undo that.
    """
    picker = SessionPicker()

    def boom(cwd: str) -> tuple[tuple[SessionRow, ...], Overlay]:
        raise OSError("transcripts unreadable")

    worker = picker.request_scan("/srv/repo", scanner=boom)
    assert worker is not None
    worker.join(timeout=5.0)
    picker.poll()

    assert not picker.scanning
    assert picker.error is not None
    assert "transcripts unreadable" in picker.error
    assert picker.rows == ()


def test_a_failed_rescan_keeps_the_rows_it_already_had() -> None:
    """
    A listing a minute stale beats a blank one, and the error beside it says which
    it is. Blanking would destroy the row the operator opened the picker to find.
    """
    picker = _loaded(_row("a"), _row("b"))

    def boom(cwd: str) -> tuple[tuple[SessionRow, ...], Overlay]:
        raise RuntimeError("git worktree exploded")

    worker = picker.request_scan("/srv/repo", scanner=boom)
    assert worker is not None
    worker.join(timeout=5.0)
    picker.poll()

    assert [row.session_id for row in picker.rows] == ["a", "b"]
    assert picker.error is not None


def test_polling_with_nothing_in_flight_is_a_no_op() -> None:
    """``poll`` runs every frame. It must cost a failed queue read and nothing else."""
    picker = _loaded(_row("a"))
    picker.poll()
    picker.poll()
    assert [row.session_id for row in picker.rows] == ["a"]
    assert not picker.scanning


def test_a_second_scan_request_while_one_is_in_flight_is_dropped() -> None:
    picker = SessionPicker()
    picker.scanning = True
    assert picker.request_scan("/srv/repo", scanner=lambda cwd: ((), {})) is None


def test_the_scan_records_the_resolved_directory_it_answered_for(tmp_path: Path) -> None:
    """
    The launcher's cwd defaults to ``"."``. A picker captioned "sessions in ."
    answers a question the operator did not ask, and the SDK canonicalises the
    directory anyway -- so the resolved form is what is scanned and what is shown.
    """
    seen: list[str] = []

    def scanner(cwd: str) -> tuple[tuple[SessionRow, ...], Overlay]:
        seen.append(cwd)
        return (), {}

    picker = SessionPicker()
    worker = picker.request_scan(str(tmp_path / "." / ""), scanner=scanner)
    assert worker is not None
    worker.join(timeout=5.0)
    picker.poll()

    assert picker.scanned_cwd == str(tmp_path.resolve())
    assert seen == [str(tmp_path.resolve())]


def test_bookmark_toggle_round_trips_through_the_overlay_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Written at the click rather than at exit, as the theme and wrap preferences are:
    a bookmark that survives only a clean shutdown is lost to the crash it was made
    to protect against.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    picker = _loaded(_row("a"), _row("b"))

    picker.toggle_bookmark(picker.rows[0])
    assert load_overlay(overlay_path())["a"].bookmarked
    assert [row.bookmarked for row in picker.rows] == [True, False]

    picker.toggle_bookmark(picker.rows[0])
    assert "a" not in load_overlay(overlay_path())
    assert [row.bookmarked for row in picker.rows] == [False, False]


def test_a_bookmark_from_a_previous_run_arrives_already_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    overlay = {"a": SessionMark(bookmarked=True)}
    picker = _loaded(_row("a", bookmarked=True), _row("b"), overlay=overlay)
    assert picker.rows[0].bookmarked

    picker.toggle_bookmark(picker.rows[0])
    assert not picker.rows[0].bookmarked
    assert "a" not in load_overlay(overlay_path())


def test_toggling_a_bookmark_does_not_move_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Bookmarked-first is the order the list was *scanned* in, not one held under the
    cursor. Re-sorting on click would slide the next row under a mouse still resting
    on the button that moved it, and the following click would land on a session the
    operator never read. The order settles on the next scan.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    picker = _loaded(_row("a"), _row("b"), _row("c"))
    picker.toggle_bookmark(picker.rows[2])
    assert [row.session_id for row in picker.rows] == ["a", "b", "c"]


def test_picking_a_row_puts_its_id_on_the_spec() -> None:
    state = LauncherState(task="carry on", cwd="/srv/repo")
    state.picker = _loaded(_row("a"), _row("b"))
    state.picker.select("b", state.cwd)
    assert state.spec().resume == "b"


def test_picking_nothing_leaves_todays_fresh_launch_spec_untouched() -> None:
    """
    The default path. A draft nobody opened the resume section on must produce byte
    for byte the spec it produced before the section existed.
    """
    state = LauncherState(task="  audit the parser  ", cwd="/tmp/x", model_index=1)
    assert state.spec() == LaunchSpec(
        task="audit the parser",
        model=MODELS[1],
        cwd="/tmp/x",
        template="solo",
        brief=None,
        resume=None,
    )


def test_a_scanned_but_unpicked_list_still_launches_fresh() -> None:
    """
    Opening the picker is not picking. Enumerating must have no effect on the spec.
    """
    state = LauncherState(task="t")
    state.picker = _loaded(_row("a"), _row("b"))
    assert state.spec().resume is None


def test_picking_the_same_row_twice_clears_the_pick() -> None:
    """The escape hatch: the row that got picked by accident is also the way out."""
    picker = _loaded(_row("a"))
    picker.select("a", "/srv/repo")
    assert picker.selected == "a"
    picker.select("a", "/srv/repo")
    assert picker.selected is None


def test_clearing_the_pick_drops_the_recovered_brief_with_it() -> None:
    picker = _loaded(_row("a"))
    picker.recovered_brief = "/somewhere"
    picker.select(None, "/srv/repo")
    assert picker.selected is None
    assert picker.recovered_brief is None


def test_selected_row_is_the_picked_one_and_none_when_nothing_is_picked() -> None:
    picker = _loaded(_row("a"), _row("b"))
    assert picker.selected_row is None
    picker.select("b", "/srv/repo")
    row = picker.selected_row
    assert row is not None and row.session_id == "b"


def test_a_picked_session_recovers_its_brief_directory_when_one_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The one thing resume genuinely restores besides the conversation, and the reason
    it is not a guess: the session id does not move, so ``brief.session_dir`` derives
    the same directory the original session wrote its premises into.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    slug = str(project.resolve()).replace("/", "-")
    expected = tmp_path / ".claude" / "projects" / slug / "briefs" / "a"
    expected.mkdir(parents=True)

    picker = _loaded(_row("a"))
    picker.select("a", str(project))
    assert picker.recovered_brief == str(expected)


def test_no_brief_is_offered_when_the_session_never_wrote_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Offering a path to a directory that does not exist would be the same lie as
    pre-filling the task with a guess at what the original session was doing.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    picker = _loaded(_row("a"))
    picker.select("a", str(tmp_path))
    assert picker.recovered_brief is None


def test_the_pick_does_not_survive_the_launch_it_was_made_for() -> None:
    """
    A pick that outlived its launch would silently resume the same session on the
    next Ctrl+N, with the header carrying it collapsed while it did. Cleared with
    the task, and for the same reason: it has gone somewhere.
    """
    state = LauncherState(task="t", cwd="/srv/repo")
    state.picker = _loaded(_row("a"))
    state.picker.select("a", state.cwd)
    assert state.spec().resume == "a"

    # What ``draw`` does on a successful launch.
    state.task = ""
    state.picker.select(None, state.cwd)
    assert state.spec().resume is None


def test_launch_hands_the_picked_session_to_the_driver() -> None:
    """
    The wiring, end to end, because this is the exact shape of defect this codebase
    has paid for twice. ``_launch`` builds an ``AgentSession`` by keyword from a
    ``LaunchSpec``, and a field the constructor call forgets is a field nothing
    complains about: the session starts, it just starts fresh, and the operator's
    lost conversation stays lost while the UI says it was resumed.
    """
    import time as _time

    from pptmstr.app import AppState, _launch
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.settings import Settings
    from pptmstr.store import Store

    started: list[AgentSession] = []

    class _RecordingPool:
        def submit(self, session: AgentSession) -> None:
            started.append(session)

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=Settings())
        state.pool = _RecordingPool()  # type: ignore[assignment]
        _launch(
            state,
            LaunchSpec(task="carry on", model=MODELS[0], cwd="/tmp", resume="sess-42"),
        )
        for _ in range(200):
            if started:
                break
            _time.sleep(0.005)
    finally:
        bridge.stop()

    assert [s.resume for s in started] == ["sess-42"]
    # And the id is the resumed one, not a freshly minted uuid -- which is what makes
    # `brief.session_dir` land on the directory the original session wrote into.
    assert [s.session_id for s in started] == ["sess-42"]


def test_launch_without_a_pick_mints_a_fresh_id() -> None:
    import time as _time

    from pptmstr.app import AppState, _launch
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.settings import Settings
    from pptmstr.store import Store

    started: list[AgentSession] = []

    class _RecordingPool:
        def submit(self, session: AgentSession) -> None:
            started.append(session)

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=Settings())
        state.pool = _RecordingPool()  # type: ignore[assignment]
        _launch(state, LaunchSpec(task="do a thing", model=MODELS[0], cwd="/tmp"))
        for _ in range(200):
            if started:
                break
            _time.sleep(0.005)
    finally:
        bridge.stop()

    assert [s.resume for s in started] == [None]
    assert started[0].session_id != "sess-42"


def test_the_toggle_flips_from_what_was_drawn_not_from_the_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Pins a contract rather than reproducing an observed bug: rows and overlay are
    joined at scan time and no path today separates them again.

    It is pinned because the two are the only candidates for "what is this bookmark
    flipping *from*", and they fail differently. On a divergence the row is what the
    operator's cursor is resting on and the overlay is a map they cannot see, so
    reading the overlay would make the click a no-op that redraws identically -- the
    button would look broken while every value involved was individually correct.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    picker = _loaded(_row("a", bookmarked=True), overlay={})

    picker.toggle_bookmark(picker.rows[0])

    assert not picker.rows[0].bookmarked
    assert "a" not in load_overlay(overlay_path())
