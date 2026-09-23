"""
The pure decisions behind the launcher modal.

Mostly the parts checkable without a GL context: what an open request does to the
state machine, what a draft turns into when it is handed to ``_launch``, and the
readiness rule that decides whether Enter does anything at all.

``draw`` is exercised too, against a fake ``imgui``, the way the pane tests are.
Nothing about pixels is claimed by that -- what it covers is that a decision reaches
the screen and the launch callback at all, which is the half of the containment dial
no pure test can see.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pptmstr import cli_version, sandbox
from pptmstr.approval import Policy
from pptmstr.model import LaunchSpec
from pptmstr.sessions import Overlay, SessionMark, SessionRow, load_overlay, overlay_path
from pptmstr.ui import launcher
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
        task="audit the parser",
        model=MODELS[1],
        cwd="/tmp/x",
        # A directory no repository encloses is its own base, which is what makes
        # adopting the field a no-op for a scratch directory.
        session_base="/tmp/x",
        template="solo",
        brief=None,
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


def test_spec_defaults_blank_cwd_to_this_directory() -> None:
    """
    An empty directory field means "here", not "". The value reaches
    ``AgentSession`` and is the FLEET rail's grouping key, so a blank string would
    file the session under a project whose name is the empty string.

    "Here" is spelled absolutely because the store cannot resolve a relative one:
    ``model.relative_write`` needs an absolute cwd to place an absolute write, and
    the same value is what ``sessions.same_cwd`` narrows the resume picker by.
    """
    assert LauncherState(task="t", cwd="   ").spec().cwd == os.path.realpath(".")


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
        session_base="/tmp/x",
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


# -- the launched cwd is absolute (2026-09-04-two-mainline-defects, defect 1)


def test_the_draft_hands_the_driver_an_absolute_cwd() -> None:
    """
    A relative cwd reaches the store and the store cannot resolve one.

    ``model.relative_write`` returns None for an absolute write path unless the
    writing agent's cwd is itself absolute, and the caller records that as
    ``ApprovedWrites.unplaced``. So a session launched on the default "." puts every
    write in ``unplaced``, leaves ``Task.writes.paths`` empty, and makes
    ``wrote_outside_declaration`` read ``()`` for the whole run -- a measurement that
    is silent rather than wrong, which is the harder failure to notice.

    Pinned at the launcher because that is the one place a draft becomes a spec, and
    because ``LaunchSpec.cwd``'s own comment already claimed this was happening.
    """
    state = LauncherState(task="anything", cwd="")
    assert Path(state.spec().cwd).is_absolute()

    state = LauncherState(task="anything", cwd=".")
    assert Path(state.spec().cwd).is_absolute()


def test_a_relative_cwd_in_the_draft_survives_as_the_directory_it_names(
    tmp_path: Path,
) -> None:
    """
    Resolving must not relocate the agent, only spell where it already runs.

    The SDK resolves a relative ``ClaudeAgentOptions.cwd`` against this process's
    directory, so resolving here names the same directory rather than a different
    one. A fix that moved where agents run would be a behaviour change wearing a
    units fix's clothes.
    """
    state = LauncherState(task="anything", cwd=str(tmp_path))
    assert state.spec().cwd == str(Path(tmp_path).resolve())


# ---------------------------------------------------------------------------
# The dangerously autonomous mode
# ---------------------------------------------------------------------------
#
# Two questions, and they are not the same one. What the operator asked for lives on
# the draft; what this launch gets is derived, because the directory can refuse it.
# The spec is where the two meet, so most of what follows is about the spec.

# A directory that is certainly inside pptmstr's own checkout, derived from the module
# under test rather than from the process's directory -- so this says the same thing
# whichever directory pytest was started from.
INSIDE_THE_CHECKOUT = str(Path(launcher.__file__).resolve().parent)

# A directory at the checkout ROOT, beside the package rather than under it. Derived
# the same way and for the same reason. ``planning/`` is a real directory of this
# checkout, so a run from an installed pptmstr with no checkout around it would fail
# the assertions below by the fixture vanishing rather than by passing vacuously.
BESIDE_THE_PACKAGE = str(Path(launcher.__file__).resolve().parents[2] / "planning")

# A task that is not a subject line. Every test below that engages the mode and expects
# a launch, or expects a *particular* hold, uses this rather than "t": an unattended
# launch whose task box is one line is held by ``LauncherState.premise_is_thin``, so a
# one-character task would make those tests pass or fail for a reason none of them is
# about -- and would let the version gate break without a single one going red.
A_PREMISE = "do the thing\nand here is the reason it is worth doing"


def _meets() -> cli_version.FloorCheck:
    """A CLI comfortably above the floor: the machine these tests describe."""
    return cli_version.MeetsFloor("2.1.251", (2, 1, 251), cli_version.SANDBOX_FLOOR)


def _below() -> cli_version.FloorCheck:
    return cli_version.BelowFloor("2.1.9", (2, 1, 9), cli_version.SANDBOX_FLOOR)


def _unreadable() -> cli_version.FloorCheck:
    return cli_version.Unreadable("no claude on PATH", cli_version.SANDBOX_FLOOR)


def _counted(answer: Callable[[], cli_version.FloorCheck]) -> tuple[launcher.Prober, list[int]]:
    """
    A prober and the log of how many times it was asked, which is the whole question
    for the memo and for the off path.
    """
    calls: list[int] = []

    def prober() -> cli_version.FloorCheck:
        calls.append(1)
        return answer()

    return prober, calls


def _run_draw(
    monkeypatch: pytest.MonkeyPatch,
    state: LauncherState,
    *,
    click_launch: bool = False,
    submit: bool = False,
    prober: launcher.Prober = _meets,
    settle: bool = True,
    subagent_cap: int | None = None,
) -> tuple[MagicMock, list[LaunchSpec]]:
    """
    Run ``draw`` against a fake imgui and hand back the fake and what was launched.

    The same arrangement the pane tests use. It buys the one thing the pure decisions
    cannot cover: that the dial is *drawn*, and that the launch button hands the spec
    the dial produced to the callback rather than to nothing.

    ``prober`` stands in for reading the installed CLI's version, so nothing here
    spawns ``claude --version`` or depends on which CLI this machine has. ``settle``
    lets the read land before the frame, because a machine whose CLI clears the floor
    is what every test written before the version gate existed was describing; pass
    ``settle=False`` to draw against a gate that has not answered yet.

    ``submit`` is Ctrl+Enter in the task box, which reaches the launch branch without
    going through the button at all.
    """
    if settle:
        worker = state.version_gate.request(prober=prober)
        if worker is not None:
            worker.join(timeout=5.0)
        state.version_gate.poll()

    fake = MagicMock()
    fake.begin_popup_modal.side_effect = lambda *a, **k: (True, True)
    fake.collapsing_header.side_effect = lambda *a, **k: False
    fake.checkbox.side_effect = lambda label, value: (False, value)
    fake.combo.side_effect = lambda label, index, items: (False, index)
    fake.input_text_with_hint.side_effect = lambda label, hint, value: (False, value)
    fake.input_int.side_effect = lambda label, value, *a, **k: (False, value)
    fake.button.side_effect = lambda label, *a, **k: click_launch and label == "launch"
    fake.small_button.side_effect = lambda *a, **k: False
    fake.is_key_pressed.side_effect = lambda *a, **k: False

    fake_widgets = MagicMock()
    fake_widgets.ellipsis.side_effect = lambda text, width: text
    fake_widgets.multiline_input.side_effect = lambda *a, **k: (submit, state.task)

    monkeypatch.setattr(launcher, "imgui", fake)
    monkeypatch.setattr(launcher, "widgets", fake_widgets)

    launched: list[LaunchSpec] = []
    state.is_open = True
    launcher.draw(
        state,
        running=0,
        queued=0,
        cap=4,
        launch=launched.append,
        wrap=True,
        scanner=lambda cwd: ((), {}),
        prober=prober,
        subagent_cap=subagent_cap,
    )
    return fake, launched


def _lines(fake: MagicMock) -> list[str]:
    """Everything the frame put into words, whatever colour it used to do it."""
    lines = [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]
    lines += [str(a) for call in fake.text_disabled.call_args_list for a in call.args]
    lines += [str(call.args[0]) for call in fake.checkbox.call_args_list]
    return lines


def _drawn(
    monkeypatch: pytest.MonkeyPatch,
    state: LauncherState,
    *,
    click_launch: bool = False,
    submit: bool = False,
    prober: launcher.Prober = _meets,
    settle: bool = True,
    subagent_cap: int | None = None,
) -> tuple[list[str], list[LaunchSpec]]:
    fake, launched = _run_draw(
        monkeypatch,
        state,
        click_launch=click_launch,
        submit=submit,
        prober=prober,
        settle=settle,
        subagent_cap=subagent_cap,
    )
    return _lines(fake), launched


# -- what the draft turns into -----------------------------------------------------


def test_the_mode_is_off_until_it_is_asked_for() -> None:
    """
    The default launch is today's launch. Everything else in this section is about
    what happens when an operator departs from it deliberately.
    """
    assert LauncherState().dangerous is False


def test_a_draft_that_did_not_ask_for_the_mode_builds_the_spec_it_built_before(
    tmp_path: Path,
) -> None:
    """
    Byte-identical, not merely equivalent: the two fields must come back at the
    defaults ``LaunchSpec`` declares, which is what makes every existing test in this
    file still describe the launch the operator gets.

    In a directory the mode would be *allowed* in, so this fails if the default flips
    rather than passing because the refusal happened to catch it.
    """
    spec = LauncherState(task="t", cwd=str(tmp_path)).spec()
    assert spec.containment is None
    assert spec.policy is Policy.STRICT


def test_the_mode_puts_the_containment_and_the_policy_on_the_spec(tmp_path: Path) -> None:
    """
    Both fields or neither. The policy releases ``Bash`` from the gate and the
    containment is the only thing bounding what a released ``Bash`` reaches, so a spec
    carrying one of them is a worse state than a spec carrying neither.
    """
    spec = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True).spec()
    assert spec.containment == sandbox.containment_settings()
    assert spec.policy is Policy.AUTONOMOUS


def test_nothing_about_the_rest_of_the_draft_moves_when_the_mode_is_on(
    tmp_path: Path,
) -> None:
    """
    The mode is two fields and not a different launch. Anything else it changed would
    be a second behaviour riding on a checkbox whose label says one thing.
    """
    off = LauncherState(task=A_PREMISE, cwd=str(tmp_path), model_index=1, brief=" b ").spec()
    on = LauncherState(
        task=A_PREMISE, cwd=str(tmp_path), model_index=1, brief=" b ", dangerous=True
    ).spec()
    assert replace(on, containment=None, policy=Policy.STRICT) == off


# -- the refusal -------------------------------------------------------------------


def test_the_mode_is_refused_for_pptmstrs_own_source_tree() -> None:
    """
    The hazard the refusal exists for, spelled as the directory it actually protects.

    Under this mode the sandbox's writable region *is* the session's cwd, and its
    protected paths do not cover ordinary project source -- so a session launched here
    could rewrite the ``approval.py`` that gates the next one.
    """
    assert launcher.refusal_for(INSIDE_THE_CHECKOUT) is not None


def test_the_refusal_covers_the_checkout_root_and_not_only_the_package() -> None:
    """
    The narrowing this protects against has no error and no surface.

    ``OWN_CHECKOUT`` is derived from the package directory, and the honest short
    answer -- the package directory itself -- would leave ``planning/``, ``scripts/``,
    ``CLAUDE.md`` and this test file writable by an agent that writes unattended,
    while every other assertion in this file stayed green: they all name a path under
    ``pptmstr/``, which is inside the checkout either way.

    The directory really is outside the package, which is what makes the refusal
    discriminating rather than incidental.
    """
    assert not Path(BESIDE_THE_PACKAGE).is_relative_to(Path(INSIDE_THE_CHECKOUT).parent)
    assert Path(BESIDE_THE_PACKAGE).is_dir()
    assert launcher.refusal_for(BESIDE_THE_PACKAGE) is not None


def test_a_refused_directory_launches_under_the_ordinary_gate() -> None:
    """
    The refusal is not advice. A draft that asks for the mode in a directory it is
    refused for produces the defaults, which is today's launch.
    """
    state = LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, dangerous=True)
    assert state.dangerous is True
    assert state.dangerous_engaged is False
    spec = state.spec()
    assert spec.containment is None
    assert spec.policy is Policy.STRICT


def test_a_directory_that_cannot_be_located_at_all_is_refused_as_well() -> None:
    """
    ``~user`` with no home resolves to nowhere. Fail-closed is
    ``tree.lies_inside_checkout``'s choice and this is the caller honouring it: a yes
    costs a refusal the operator can correct, a no hands a directory nobody could
    locate to an agent that writes unattended.
    """
    assert launcher.refusal_for("~nosuchuser-pptmstr/work") is not None


def test_the_refusal_does_not_claim_which_of_the_two_states_it_found() -> None:
    """
    A bool cannot separate "inside the checkout" from "could not be located", and a
    message naming only the first is *false* for the second -- it sends an operator to
    move a directory when what they have is a typo. STYLE.md §3 names that smell: an
    error message that does not distinguish the two mistakes it covers.
    """
    message = launcher.refusal_for(INSIDE_THE_CHECKOUT)
    assert message is not None
    assert "checkout" in message
    assert "resolved" in message


def test_the_question_is_asked_about_the_cwd_and_not_about_the_checkout(
    tmp_path: Path,
) -> None:
    """
    Pins the direction of the containment relation, which is the one mistake here that
    a passing suite would otherwise hide: with the two arguments the other way round a
    checkout is not inside the session's directory, so every launch would be allowed.
    """
    checkout = tmp_path / "checkout"
    inner = checkout / "work"
    inner.mkdir(parents=True)

    assert launcher.refusal_for(str(inner), checkout=str(checkout)) is not None
    assert launcher.refusal_for(str(checkout), checkout=str(inner)) is None


def test_the_memoised_refusal_follows_the_directory_field(tmp_path: Path) -> None:
    """
    The answer is cached because the draw asks every frame and each answer resolves two
    paths through the filesystem. Cached against the field's raw value, so editing the
    field is what re-asks -- a cache keyed on "have I ever answered" would pin the
    first directory's verdict onto every later one.
    """
    state = LauncherState(task="t", cwd=str(tmp_path))
    assert state.containment_refusal() is None

    state.cwd = INSIDE_THE_CHECKOUT
    assert state.containment_refusal() is not None

    state.cwd = str(tmp_path)
    assert state.containment_refusal() is None


# -- what is on the screen ---------------------------------------------------------


def test_the_dial_is_drawn_whether_or_not_it_is_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    "Displaying the dial is part of shipping the dial" (2026-08-22 D3). An operator who
    cannot see that a session is under-gated cannot supervise it, so the control is on
    the modal every time rather than behind a disclosure.

    The danger is in the words, not only in the colour: hue is never the only channel
    here, and on ``high_contrast`` the danger role moves toward the text role.
    """
    lines, _ = _drawn(monkeypatch, LauncherState(task="t", cwd=str(tmp_path)))
    assert any("dangerous" in line for line in lines)


def test_the_label_does_not_understate_what_the_tick_grants(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The hazard is a label narrower than the grant, which is worse than no label: an
    operator who reads "run Bash unattended" and ticks it has consented to one tool and
    been given every tool the gate would have held. ``AUTONOMOUS`` releases the whole of
    ``approval._REVIEW``, so the checkbox has to name more than one kind of thing.

    Asserted against the **checkbox label** specifically rather than against the frame,
    because the detail below it is read after the decision and the label is read before.
    """
    lines, _ = _drawn(monkeypatch, LauncherState(task="t", cwd=str(tmp_path)))
    label = next(line for line in lines if "dangerous" in line)
    assert "unattended" in label
    # More than one kind, and none of them the single tool the old label named.
    assert "writes" in label and "spawns" in label and "messages" in label


def test_an_engaged_mode_says_what_it_releases_and_what_it_does_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    What an operator needs before pressing launch: which kinds of call now run unasked,
    and what still stops. A tool this build has never heard of is not released at any
    policy, and saying so is what stops "unattended" being read as "unbounded".

    **No credential claim, and the reason is stronger than the one this test first
    carried.** ``sandbox.credentials`` binds sandboxed ``Bash`` commands only, so a line
    telling the operator their keys are denied would be false for ``Read``, ``Edit`` and
    ``WebFetch``, all of which run in the CLI process. The absence is asserted because a
    reassurance is the kind of line that gets added back by someone filling out a screen.
    """
    lines, _ = _drawn(monkeypatch, LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True))
    released = next(line for line in lines if "without asking" in line)
    assert "writes files" in released and "spawns sub-agents" in released
    assert "never heard of is still not released" in released
    assert any(sandbox.ALLOWED_DOMAIN in line for line in lines)
    assert not any("credential" in line for line in lines)


def test_the_engaged_mode_names_the_boundary_the_released_tools_do_not_get(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``WebFetch``/``WebSearch`` are released and are bounded by nothing: they run inside
    the CLI process, which the sandbox does not wrap, so ``network.strictAllowlist`` --
    named two lines above on the same screen -- does not reach them.

    That is a cost the operator chose, and a chosen cost has to be legible at the moment
    of choosing or it was not chosen. The failure this guards is a screen that lists the
    containment and omits the hole in it, which reads as a stronger guarantee than the
    one being offered.
    """
    lines, _ = _drawn(monkeypatch, LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True))
    uncontained = [line for line in lines if "not contained" in line]
    assert len(uncontained) == 1
    assert "WebFetch" in uncontained[0] and "WebSearch" in uncontained[0]
    assert "CLI process" in uncontained[0]


def test_the_two_boundaries_are_named_as_two_mechanisms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The working directory bounds both ``Bash`` and the write tools, and it bounds them
    by different machinery: the CLI's sandbox wraps ``Bash`` and its children, and
    ``driver._escapes_write_region`` holds ``Write``/``Edit``/``MultiEdit``/
    ``NotebookEdit`` to the same region because those run in the CLI process, which the
    sandbox does not wrap. One sentence covering both would read as one guarantee, and
    an operator meeting a refusal needs to know which of the two produced it -- they
    fail differently and one of them is a ``PreToolUse`` decision they can see in the
    transcript.
    """
    lines, _ = _drawn(monkeypatch, LauncherState(task="t", cwd=str(tmp_path), dangerous=True))
    sandboxed = next(line for line in lines if "contained:" in line and "Bash" in line)
    assert sandbox.ALLOWED_DOMAIN in sandboxed
    gated = next(line for line in lines if "held to that same directory" in line)
    assert "CLI process" in gated and "refused" in gated


def test_the_uncontained_edge_is_not_said_when_the_mode_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``WebFetch`` parks at the operator under ``STRICT``, so there is no uncontained edge
    to warn about -- the operator is the bound. A warning shown at every policy would
    train the eye past it by the time it means something.
    """
    lines, _ = _drawn(monkeypatch, LauncherState(task="t", cwd=str(tmp_path)))
    assert not any("not contained" in line for line in lines)


# -- the cap, which is the only bound the mode cannot widen -------------------------


def test_the_cap_in_force_is_on_screen_when_the_mode_is_engaged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Once spawns auto-approve, ``subagent_cap`` is the only bound on fan-out left, and
    ``planning/2026-09-03`` §8 tells the operator to set it low -- which they cannot do
    against a number they cannot see. The at-cap deny runs ahead of ``classify`` in
    ``driver._gate_tool_use``, which is what makes it the one bound the policy cannot
    widen, and that is the part of it worth a sentence rather than just a figure.
    """
    lines, _ = _drawn(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True),
        subagent_cap=8,
    )
    capped = next(line for line in lines if "sub-agents are capped" in line)
    assert "8" in capped
    assert "cannot widen" in capped


def test_the_cap_line_never_invents_a_number(tmp_path: Path) -> None:
    """
    The hazard, and it is the reason the parameter is optional rather than defaulted to
    ``driver.DEFAULT_SUBAGENT_CAP``: a caller that does not know the cap in force must
    not cause a plausible one to be printed. The cap an operator is reading this line to
    check is precisely the one they changed away from the default, so a stand-in is wrong
    exactly when it is being relied on.

    Asserted as "no digit anywhere in the line", not as "does not equal the default" --
    the second passes for any other invented number.
    """
    assert not any(ch.isdigit() for ch in launcher._cap_line(None))
    assert "capped" in launcher._cap_line(None)
    assert "4" in launcher._cap_line(4)


def test_the_cap_is_not_shown_for_a_launch_that_is_not_taking_the_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The cap applies to every session; it is *load-bearing* only under this mode, where
    nothing else bounds fan-out. Shown unconditionally it would be one more line of
    chrome on the launch an operator makes a hundred times, and the point of putting it
    here is that it is surprising.
    """
    lines, _ = _drawn(monkeypatch, LauncherState(task="t", cwd=str(tmp_path)), subagent_cap=8)
    assert not any("sub-agents are capped" in line for line in lines)


# -- the cap an operator may resize for one launch ---------------------------------


def test_a_draft_nobody_resized_names_no_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``None`` is not "zero sub-agents" and it is not the default either -- it is "the
    setting decides", which is what every spec this modal built before the control
    existed meant. ``app._launch`` is what turns it into a number.
    """
    _, launched = _drawn(
        monkeypatch,
        LauncherState(task="t", cwd=str(tmp_path)),
        click_launch=True,
        subagent_cap=8,
    )

    assert [spec.subagent_cap for spec in launched] == [None]


def test_a_resized_draft_carries_the_number_to_the_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The override reaches the value the driver is handed, including when it is zero.

    Zero is asserted here rather than only at ``app._launch`` because the two hops
    lose it differently: ``spec()`` would lose it to a ``self.subagent_cap or`` and
    ``_launch`` to a ``spec.subagent_cap or``, and a test on one hop passes while the
    other eats it.
    """
    state = LauncherState(task="t", cwd=str(tmp_path), subagent_cap=0)
    _, launched = _drawn(monkeypatch, state, click_launch=True, subagent_cap=8)

    assert [spec.subagent_cap for spec in launched] == [0]


def test_the_cap_on_screen_is_the_one_this_launch_would_get(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The failure the two controls make possible: the containment section stating the
    *setting* while the box above it holds an override, so the modal shows two numbers
    and the operator cannot tell which is in force. The line has to follow the
    override, because the override is what the session will actually get.
    """
    lines, _ = _drawn(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True, subagent_cap=2),
        subagent_cap=8,
    )

    capped = next(line for line in lines if "sub-agents are capped" in line)
    assert "2" in capped
    assert "8" not in capped


def test_a_launch_clears_the_resize_with_the_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Sized for one piece of work and cleared with it, for the reason the mode is: the
    next Ctrl+N is the one the operator types into without re-reading the panel, and a
    cap left at 1 from the last launch would throttle it silently -- a session that
    never spawns looks like a lead that chose not to.
    """
    state = LauncherState(task="t", cwd=str(tmp_path), subagent_cap=1)
    _drawn(monkeypatch, state, click_launch=True, subagent_cap=8)

    assert state.subagent_cap is None


def test_no_resize_is_offered_when_the_setting_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The same hazard ``_cap_line`` refuses, one control along. An override box has to be
    seeded with a number, and the only number available without the setting is
    ``DEFAULT_SUBAGENT_CAP`` -- which is wrong for exactly the operator who changed the
    setting, and worse in a box than in a line because a box is what they then press
    "launch" on.

    **Asserted on the words as well as on the widget, and the words are the half that
    caught a defect.** An earlier version of this test named only ``input_int``, which
    was true of a frame that drew the control's caption anyway -- so the screen
    described a spinner that was not there and claimed a per-launch cap when nothing
    was overriding anything. A control is not only its widget, and a test that names
    the widget cannot see the text beside it.
    """
    fake, _ = _run_draw(monkeypatch, LauncherState(task="t", cwd=str(tmp_path)))

    assert fake.input_int.call_args_list == []
    assert not any("sub-agents" in line for line in _lines(fake))


# -- the premise, which is the whole specification when nobody will answer -----------


def test_an_attended_launch_is_never_held_for_a_one_line_task(tmp_path: Path) -> None:
    """
    The bar belongs to the mode and to nothing else. Every launch this application made
    before the mode existed was a one-line task pressed straight through, and most
    launches still are -- a bar applied at ``STRICT`` would tax the common case for a
    problem it does not have, because at ``STRICT`` the operator is answering for each
    call and is the thing a thin premise would otherwise have replaced.
    """
    state = LauncherState(task="fix the flaky test", cwd=str(tmp_path))
    assert state.premise_is_thin is True
    assert state.launch_hold() is None


def test_an_unattended_launch_is_held_until_the_premise_is_more_than_a_subject_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    With no operator to answer, the task box is the entire specification of a run nobody
    will correct -- for a team it is written out as entry ``000`` of the brief and is
    literally all the workers are given. The hold is on the button and the reason is
    beside it, and the draft survives, because the remedy is to keep typing in the field
    already on screen.
    """
    state = LauncherState(task="make it autonomous", cwd=str(tmp_path), dangerous=True)
    assert state.launch_hold() == launcher._HOLD_THIN

    _, launched = _drawn(monkeypatch, state, click_launch=True)
    assert launched == []
    assert state.task == "make it autonomous"
    assert state.dangerous is True

    state.task = "make it autonomous\nand here is what that has to mean"
    assert state.launch_hold() is None


def test_ctrl_enter_does_not_get_past_a_thin_premise_either(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``begin_disabled`` greys the button and does nothing to the keyboard, and the task
    box submits on Ctrl+Enter -- so a hold enforced only on the button is reachable from
    the key the operator's hands are already on. The same defect the version gate's hold
    was written against, and it does not stop being one for a different reason to hold.
    """
    state = LauncherState(task="one line", cwd=str(tmp_path), dangerous=True)
    _, launched = _drawn(monkeypatch, state, submit=True)
    assert launched == []


def test_naming_an_existing_brief_is_a_premise_and_lifts_the_hold(tmp_path: Path) -> None:
    """
    An empty brief field is the path that *creates* the premises -- ``app._seed_brief``
    writes the task as entry ``000`` -- and a filled one points at premises that already
    exist, which is what a fork continuing its parent's work carries. So the operator who
    named a directory has supplied a premise that is a directory of entries, and holding
    them for the length of the covering message would be refusing the better-specified of
    the two launches.
    """
    state = LauncherState(task="continue", cwd=str(tmp_path), dangerous=True, brief="/tmp/b")
    assert state.premise_is_thin is False
    # Not "is None": this draft has not read a version yet, so the version gate holds it
    # for its own reason. What is asserted is that the premise is no longer one of them.
    assert state.launch_hold() != launcher._HOLD_THIN
    assert LauncherState(task="continue", cwd=str(tmp_path), dangerous=True).premise_is_thin


def test_a_thin_premise_is_said_before_the_machine_is_asked_about(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Three things can hold this launch and only one of them is the operator's to fix now.
    A CLI below the floor is a once-per-machine answer arriving on a worker; the premise
    is a per-launch one they can act on in the field they are looking at, while that
    subprocess is still running. Showing them the machine's message first would spend the
    wait telling them about something they cannot do anything about.
    """
    state = LauncherState(task="one line", cwd=str(tmp_path), dangerous=True)
    # Deliberately the worst machine: even then the premise is what it says.
    _drawn(monkeypatch, state, prober=_below)
    assert state.launch_hold() == launcher._HOLD_THIN


def test_a_refused_directory_shows_no_grant_it_is_not_going_to_make(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A refused directory downgrades the launch to the ordinary gate, so every line
    describing what the mode grants -- the released kinds, the containment, the
    uncontained edge, the cap -- is describing a session that is not about to start.
    Printing them beside the refusal would be the screen arguing with itself.
    """
    lines, _ = _drawn(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, dangerous=True),
        subagent_cap=8,
    )
    assert any("refused" in line for line in lines)
    assert not any("not contained" in line for line in lines)
    assert not any("sub-agents are capped" in line for line in lines)
    assert not any("without asking" in line for line in lines)


def test_a_refused_directory_says_so_where_the_dial_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A refusal the operator cannot see is a control that looks broken, and they will
    fight it -- ticking the box again, or concluding the feature does not work. The
    reason is on screen beside the box that was ticked.
    """
    lines, _ = _drawn(
        monkeypatch, LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, dangerous=True)
    )
    assert any("refused" in line and "checkout" in line for line in lines)


def test_the_modal_launches_the_mode_it_was_showing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The wiring, not the unit. ``spec()`` carrying the fields buys nothing unless the
    button hands *that* spec to the launch callback.
    """
    _, launched = _drawn(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True),
        click_launch=True,
    )
    assert [s.policy for s in launched] == [Policy.AUTONOMOUS]
    assert launched[0].containment == sandbox.containment_settings()


def test_a_launch_from_a_refused_directory_carries_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, launched = _drawn(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, dangerous=True),
        click_launch=True,
    )
    assert [s.containment for s in launched] == [None]
    assert [s.policy for s in launched] == [Policy.STRICT]


def test_the_mode_does_not_survive_the_launch_it_was_made_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Cleared with the task, and for a stronger reason than the task has. 08-11 rejected
    a persisted toggle because "the operator sets it for the session they are watching
    and forgets it is set for the four they are not" -- and the fifth session of one
    run is as unwatched as the first of the next. The draft survives a cancel, as it
    always has; only a launch clears this.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True)
    _, launched = _drawn(monkeypatch, state, click_launch=True)

    assert launched[0].containment is not None
    assert state.dangerous is False
    assert state.spec().containment is None


def test_cancelling_keeps_the_mode_the_operator_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The draft survives a dismissal on purpose -- going to look up a directory must not
    cost what was already chosen. Only the launch clears it.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True)
    _drawn(monkeypatch, state)
    assert state.dangerous is True


# ---------------------------------------------------------------------------
# The version gate: a refusal the operator meets before it costs them anything
# ---------------------------------------------------------------------------
#
# ``app._launch`` refuses a contained launch whose CLI is below the sandbox floor, on a
# worker, after ``draw`` has already cleared the task box -- and it says so only to the
# log, which both layouts dock as a non-resident tab. The operator lost the paragraph
# they typed, saw no reason, and got no sign anything had been refused. What follows is
# about the property that replaces it: the mode's unavailability is on screen, and the
# draft is still in the box.


def test_the_mode_being_off_costs_no_version_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The default launch is today's launch and today's launch does not spawn
    ``claude --version``. A ten-second subprocess behind every Ctrl+N would be paid for
    by every session this application starts to buy a refusal that applies to none of
    them.
    """
    prober, calls = _counted(_meets)
    state = LauncherState(task="t", cwd=str(tmp_path))

    _, launched = _drawn(monkeypatch, state, click_launch=True, prober=prober, settle=False)

    assert calls == []
    assert (state.version_gate.answered, state.version_gate.checking) == (False, False)
    # And the launch it never read a version for is not held by the answer it never got.
    assert state.launch_hold() is None
    assert [s.policy for s in launched] == [Policy.STRICT]


def test_a_directory_already_refused_is_not_also_measured_against_the_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The two refusals are asked in order, and the second only if the first passed: a
    launch the directory refused is not a contained launch, so reading the CLI's
    version for it spawns a subprocess to answer a question nobody asks -- and would
    hold a button for a launch that is going to proceed under the ordinary gate.
    """
    prober, calls = _counted(_below)
    state = LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, dangerous=True)

    _, launched = _drawn(monkeypatch, state, click_launch=True, prober=prober, settle=False)

    assert calls == []
    assert [s.policy for s in launched] == [Policy.STRICT]


def test_a_cli_below_the_floor_holds_the_launch_and_keeps_the_draft(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The defect this exists for, stated as the operator's loss rather than as the check.

    Before: press launch, the task box empties, the modal shuts, and a worker decides
    several hundred milliseconds later that the CLI is too old -- into a log tab that is
    not on screen. A silent refusal that also destroys work reads as a broken button.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True)

    fake, launched = _run_draw(monkeypatch, state, click_launch=True, prober=_below)
    lines = _lines(fake)

    assert launched == []
    assert state.task == A_PREMISE
    # And the mode is still ticked, so correcting the machine does not also mean
    # remembering what was asked for.
    assert state.dangerous is True
    assert any("2.1.9" in line and cli_version.SANDBOX_FLOOR in line for line in lines)
    # Both halves of the refusal: the button is visibly dead, and something beside it
    # says why a press did nothing.
    assert fake.begin_disabled.called
    assert any(
        launcher._HOLD_REFUSED in str(a)
        for call in fake.text_colored.call_args_list
        for a in call.args[1:]
    )


def test_a_cli_whose_version_cannot_be_read_is_refused_as_readily(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``Unreadable`` is a refusal, not a pass. §8c measured that the CLI accepts an
    unrecognised settings key silently, so a version that could not be read is a CLI
    that may ignore every sandbox key and start a session the operator believes is
    contained -- which is the exact failure the containment exists to prevent, arrived
    at by trusting a "probably fine".
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True)

    lines, launched = _drawn(monkeypatch, state, click_launch=True, prober=_unreadable)

    assert launched == []
    assert state.task == A_PREMISE
    assert any("could not be read" in line and "no claude on PATH" in line for line in lines)


def test_a_cli_that_clears_the_floor_launches_the_mode_it_was_showing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The control the three refusals above are worthless without. A gate that held every
    launch would pass every one of them and ship a mode nobody can use.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True)

    lines, launched = _drawn(monkeypatch, state, click_launch=True, prober=_meets)

    assert [s.policy for s in launched] == [Policy.AUTONOMOUS]
    assert launched[0].containment == sandbox.containment_settings()
    assert not any("refused on this machine" in line for line in lines)
    assert not any(launcher._HOLD_LINE in line for line in lines)


def test_the_launch_is_held_until_the_version_has_been_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Fail-closed in the window between the tick and the answer, which is the window the
    press actually lands in. The read is on a worker precisely so the frame does not
    wait for it, so there is always a frame where the mode is asked for and unjudged --
    and letting that frame launch would restore the defect for the slowest CLIs, which
    are the ones most likely to come back unreadable.
    """
    release = threading.Event()

    def slow() -> cli_version.FloorCheck:
        release.wait(timeout=5.0)
        return _meets()

    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True)
    try:
        lines, launched = _drawn(monkeypatch, state, click_launch=True, prober=slow, settle=False)

        assert launched == []
        assert state.task == A_PREMISE
        assert state.launch_hold() == launcher._HOLD_CHECKING
        # Not silence: the section says what is being waited on, and says the launch is
        # waiting for it rather than leaving a dead button unexplained.
        assert any(launcher._HOLD_LINE in line for line in lines)
    finally:
        release.set()


def test_ctrl_enter_does_not_get_past_a_held_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``begin_disabled`` greys a button and does nothing whatever to the keyboard, and the
    task box submits on Ctrl+Enter -- so the disable alone leaves the whole defect
    reachable from the key most likely to be used, since the operator's hands are
    already in the box they typed the paragraph into.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True)

    _, refused = _drawn(monkeypatch, state, submit=True, prober=_below)
    assert refused == []
    assert state.task == A_PREMISE

    # The same key, on a machine that clears the floor, still launches.
    ok = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True)
    _, launched = _drawn(monkeypatch, ok, submit=True, prober=_meets)
    assert [s.policy for s in launched] == [Policy.AUTONOMOUS]


def test_the_check_is_started_and_landed_by_the_draw_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The wiring, which no state-level test here can see.

    A gate nothing asks holds the launch forever, and a gate nothing polls never stops
    holding it -- both render as a button that is dead for no stated reason, which is
    the failure this whole task is replacing. So: frames only, no help from the
    harness, and the refusal has to arrive on screen without the operator closing and
    reopening the modal.
    """
    prober, calls = _counted(_below)
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True)

    lines: list[str] = []
    for _ in range(400):
        lines, launched = _drawn(monkeypatch, state, click_launch=True, prober=prober, settle=False)
        assert launched == []
        if state.version_gate.answered:
            break
        time.sleep(0.005)

    assert calls == [1]
    assert state.version_gate.answered
    assert any("2.1.9" in line for line in lines)


def test_the_version_is_read_once_and_not_once_a_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The memo, which is the difference between one subprocess and sixty a second. The
    modal redraws continuously while it is up, so an unmemoised read is a fork per
    frame for as long as the box stays ticked.
    """
    prober, calls = _counted(_meets)
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), dangerous=True)

    for _ in range(5):
        _drawn(monkeypatch, state, prober=prober)

    assert calls == [1]


def test_a_read_in_flight_is_not_joined_by_a_second_request() -> None:
    """
    ``request`` is called from a draw that runs every frame, so the frames between the
    first and the answer must not each start their own subprocess.
    """
    release = threading.Event()

    def slow() -> cli_version.FloorCheck:
        release.wait(timeout=5.0)
        return _meets()

    gate = launcher.VersionGate()
    try:
        first = gate.request(prober=slow)
        assert first is not None
        assert gate.checking and not gate.answered
        assert gate.request(prober=slow) is None
        # And the frame did not wait for it: poll is a failed queue read, not a join.
        gate.poll()
        assert not gate.answered
    finally:
        release.set()

    first.join(timeout=5.0)
    gate.poll()
    assert gate.answered and gate.refusal is None


def test_an_answered_gate_is_not_asked_again() -> None:
    gate = launcher.VersionGate()
    worker = gate.request(prober=_meets)
    assert worker is not None
    worker.join(timeout=5.0)
    gate.poll()

    def never() -> cli_version.FloorCheck:
        raise AssertionError("the gate re-read a version it already had")

    assert gate.request(prober=never) is None


def test_a_prober_that_raises_becomes_a_refusal_rather_than_a_stuck_button() -> None:
    """
    ``check_installed_cli`` documents that it does not raise, and this is the boundary
    that does not depend on it: an exception escaping the worker would leave
    ``checking`` true for the life of the modal, which is a launch held forever with
    nothing on screen saying why.
    """

    def boom() -> cli_version.FloorCheck:
        raise OSError("the process table is full")

    gate = launcher.VersionGate()
    worker = gate.request(prober=boom)
    assert worker is not None
    worker.join(timeout=5.0)
    gate.poll()

    assert gate.answered and not gate.checking
    assert gate.refusal is not None
    assert "the process table is full" in gate.refusal


# -- what the refusals say ---------------------------------------------------------


def test_too_old_and_could_not_tell_do_not_read_as_the_same_refusal() -> None:
    """
    STYLE.md §3: an error message that does not distinguish the two mistakes it covers.
    One says upgrade the CLI; the other says find out which binary answered, and the
    detail naming it is the only thing that makes that possible. A message covering
    both would send an operator to upgrade a CLI that is not installed.
    """
    old = launcher.floor_refusal(_below())
    unknown = launcher.floor_refusal(_unreadable())
    assert old is not None and unknown is not None

    assert "2.1.9" in old and "could not be read" not in old
    assert "no claude on PATH" in unknown and "2.1.9" not in unknown
    # Both name the floor they were judged against, and both name the way past.
    for message in (old, unknown):
        assert cli_version.SANDBOX_FLOOR in message
        assert "untick" in message


def test_the_launcher_and_the_launch_path_refuse_the_same_readings() -> None:
    """
    The duplication, pinned. ``app._containment_refusal`` judges the same three values
    for the same reason and cannot be shared -- ``app`` imports this module, so the
    dependency cannot run the other way.

    What has to agree is the classification and not the wording: the launcher's message
    is read by an operator looking at a modal and app's by whoever opens the log. If the
    two ever disagreed the launcher would hold a button for a launch that would have
    been fine, or -- the expensive direction -- allow one that gets eaten on a worker,
    which is the defect this gate was added to remove.
    """
    from pptmstr.app import _containment_refusal

    for check in (_meets(), _below(), _unreadable()):
        assert (launcher.floor_refusal(check) is None) == (_containment_refusal(check) is None)

    # And the agreement is not vacuous in either direction.
    assert launcher.floor_refusal(_meets()) is None
    assert launcher.floor_refusal(_below()) is not None
