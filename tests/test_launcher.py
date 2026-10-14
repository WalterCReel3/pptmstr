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

from pptmstr import cli_version, sandbox, templates
from pptmstr.approval import Policy
from pptmstr.model import LaunchSpec
from pptmstr.model_catalog import DEFAULT_MODEL, FALLBACK_MODELS
from pptmstr.sessions import Overlay, SessionMark, SessionRow, load_overlay, overlay_path
from pptmstr.ui import launcher, widgets
from pptmstr.ui.launcher import LauncherState, SessionPicker, age_label


def _row(
    session_id: str,
    *,
    last_modified: int = 0,
    bookmarked: bool = False,
    cwd: str | None = "/srv/repo",
) -> SessionRow:
    return SessionRow(
        session_id=session_id,
        cwd=cwd,
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


def test_spec_strips_task_and_resolves_model(tmp_path: Path) -> None:
    # An already-resolved directory, so `spec`'s realpath is the identity here and
    # the expectation stays a literal. A fabricated path would not be: `/tmp` is a
    # symlink to `/private/tmp` on macOS.
    cwd = str(tmp_path.resolve())
    state = LauncherState(task="  audit the parser  ", cwd=cwd, model=FALLBACK_MODELS[1])
    assert state.spec() == LaunchSpec(
        task="audit the parser",
        model=FALLBACK_MODELS[1],
        cwd=cwd,
        # A directory no repository encloses is its own base, which is what makes
        # adopting the field a no-op for a scratch directory.
        session_base=cwd,
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


def test_an_untouched_draft_launches_on_the_named_default() -> None:
    """
    The default is a name, not a position. The offered list is probed at startup and
    ordered by the API, so "whatever is first" would hand the choice of default -- and
    its price -- to whichever model shipped most recently.
    """
    assert LauncherState(task="t").spec().model == DEFAULT_MODEL


def test_a_pick_the_offered_list_does_not_contain_survives_to_the_spec() -> None:
    """
    A model pinned by ``--model``, or offered by a probe that has since failed, reaches
    the session as chosen. An index could not express this: there is no position for a
    model the list does not hold, so the pick would be silently rewritten to one it does.
    """
    pinned = "claude-a-model-no-list-offers"
    assert pinned not in FALLBACK_MODELS

    assert LauncherState(task="t", model=pinned).spec().model == pinned


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


def _unpicked_drafts(tmp_path: Path) -> list[LauncherState]:
    """
    Every kind of ``LauncherState`` this file can build with no session picked:
    nothing listed, rows listed, a pick made and undone, and a draft loaded back
    from a failed launch, each under every rung.
    """
    there = tmp_path / "there"
    there.mkdir(exist_ok=True)
    rows = (
        _row("a", cwd=str(there.resolve())),
        _row("b", cwd=str(tmp_path / "gone")),
        _row("c", cwd=None),
        _row("d", cwd=INSIDE_THE_CHECKOUT),
    )
    drafts: list[LauncherState] = []
    for policy in launcher.POLICIES:
        for task in ("t", A_PREMISE):
            bare = LauncherState(task=task, cwd=str(tmp_path), policy=policy)
            listed = LauncherState(task=task, cwd=str(tmp_path), policy=policy)
            listed.picker = _loaded(*rows)
            drafts += [bare, listed]
            for row in rows:
                undone = LauncherState(task=task, cwd=str(tmp_path), policy=policy)
                undone.picker = _loaded(*rows)
                undone.picker.pick(row, undone.cwd)
                undone.picker.pick(row, undone.cwd)
                restored = LauncherState(cwd=str(tmp_path))
                restored.picker = _loaded(*rows)
                restored.report_failure(bare.draft(), "it did not start")
                assert restored.task == task
                drafts += [undone, restored]
    return drafts


def test_a_draft_with_nothing_picked_never_resumes_whatever_the_list_holds(
    tmp_path: Path,
) -> None:
    """
    A listing is not a pick, and neither is a pick that was undone. Such a draft
    starts a new session where the field says, and asking it for a resume is
    refused and not answered with a guess.
    """
    field = str(tmp_path.resolve())
    drafts = _unpicked_drafts(tmp_path)
    assert len(drafts) > 50

    for state in drafts:
        spec = state.spec()
        assert spec.resume is None and spec.message is None
        assert spec.cwd == field and spec.session_base == field
        assert state.launch_directory() == launcher.FieldDirectory(str(tmp_path))
        pressed = state.press()
        assert pressed is None or (pressed.resume is None and pressed.message is None)
        with pytest.raises(ValueError):
            state.resumed()


def _picked(
    tmp_path: Path, row: SessionRow, *, task: str = "  carry on  ", **fields: object
) -> LauncherState:
    """A draft with ``row`` picked, its field naming ``tmp_path``, and a CLI that answered."""
    state = LauncherState(task=task, cwd=str(tmp_path), **fields)  # type: ignore[arg-type]
    state.picker = _loaded(row)
    state.picker.pick(row, state.cwd)
    _settled(state)
    return state


def test_a_picked_session_is_resumed_from_new_task_with_the_box_as_its_message(
    tmp_path: Path,
) -> None:
    """
    The route the operator asked for: the session picked in New Task is continued
    by New Task's own launch. The box is the message and never the title, the
    directory is the row's and not the field's, and the model, team, brief and cap
    are the ones on this modal.
    """
    main, worktree = _two_trees(tmp_path)
    state = LauncherState(
        task="  carry on\nfrom the lexer  ",
        cwd=main,
        brief=" /some/brief ",
        model=FALLBACK_MODELS[1],
        template_index=1,
        policy=Policy.PERMISSIVE,
        subagent_cap=2,
    )
    row = _row("sess-42", cwd=worktree)
    state.picker = _loaded(row)
    state.picker.pick(row, state.cwd)
    from pptmstr import templates

    assert state.launch_hold() is None
    spec = state.press()

    assert spec == LaunchSpec(
        task="summary for sess-42",
        task_is_label=True,
        message="carry on\nfrom the lexer",
        resume="sess-42",
        cwd=worktree,
        session_base=worktree,
        model=FALLBACK_MODELS[1],
        template=templates.names()[1],
        brief="/some/brief",
        policy=Policy.PERMISSIVE,
        containment=None,
        subagent_cap=2,
    )
    assert state.resumed() == spec


def test_a_picked_draft_has_no_new_session_spec(tmp_path: Path) -> None:
    """
    ``spec`` is a new session's. Built for a picked draft it would start one whose
    task is the message meant for the picked session, so it refuses.
    """
    main, worktree = _two_trees(tmp_path)
    state = _picked(Path(main), _row("a", cwd=worktree))
    with pytest.raises(ValueError):
        state.spec()
    pressed = state.press()
    assert pressed is not None and pressed.resume == "a"


def test_a_picked_session_whose_directory_is_gone_is_held_under_every_rung(
    tmp_path: Path,
) -> None:
    """
    The field names a tree the conversation was not about. Nothing is started
    there, and nothing is started at all.
    """
    for policy in launcher.POLICIES:
        state = _picked(tmp_path, _row("a", cwd=str(tmp_path / "gone")), policy=policy)
        assert state.launch_hold() == launcher._HOLD_GONE
        assert state.resumed() == launcher.ResumeHeld(launcher._HOLD_GONE)
        assert state.press() is None


def test_a_picked_session_that_recorded_no_directory_runs_where_the_field_says(
    tmp_path: Path,
) -> None:
    """
    The one screen that can recover such a session does not refuse it: there is no
    recorded tree to be faithful to, and the field is on screen to be read.
    """
    state = _picked(tmp_path, _row("a", cwd=None))
    assert state.launch_directory() == launcher.UnrecordedDirectory(str(tmp_path))
    assert state.launch_hold() is None
    pressed = state.press()
    assert pressed is not None
    assert (pressed.resume, pressed.cwd) == ("a", str(tmp_path.resolve()))


def test_a_refused_rung_holds_a_picked_session_and_is_never_downgraded(
    tmp_path: Path,
) -> None:
    """
    A new session in pptmstr's own checkout is started under the ordinary gate and
    told so. A resume there is held: its directory is not a field to correct, so
    the lower rung is the operator's to choose. Either way nothing is contained
    in that directory.
    """
    row = _row("a", cwd=INSIDE_THE_CHECKOUT)
    held = _picked(tmp_path, row, task=A_PREMISE, policy=Policy.AUTONOMOUS)
    refused = launcher.RESUME_HOLD_REFUSED.format(rung="autonomous")

    assert held.granted_policy is Policy.AUTONOMOUS, "asked for, and not seated lower"
    assert held.launch_hold() == refused
    assert held.resumed() == launcher.ResumeHeld(refused)
    assert held.press() is None
    shown = held.containment_refusal()
    assert shown is not None and launcher.REFUSAL_HOLDS in shown
    assert launcher.REFUSAL_DOWNGRADES not in shown

    # The same directory under a rung that needs no containment is resumed.
    for policy in (Policy.STRICT, Policy.PERMISSIVE):
        plain = _picked(tmp_path, row, policy=policy)
        assert plain.launch_hold() is None
        pressed = plain.press()
        assert pressed is not None and pressed.containment is None
        assert pressed.policy is policy and pressed.resume == "a"

    # And the new session beside it, for the contrast the rule is about.
    fresh = LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, policy=Policy.AUTONOMOUS)
    _settled(fresh)
    downgraded = fresh.press()
    assert downgraded is not None
    assert downgraded.policy is Policy.STRICT and downgraded.containment is None
    told = fresh.containment_refusal()
    assert told is not None and launcher.REFUSAL_DOWNGRADES in told


def test_a_picked_session_under_the_top_rung_is_contained_and_waits_for_the_cli(
    tmp_path: Path,
) -> None:
    """
    Both or neither: the top rung is granted with the containment or not at all.
    A one-line message is enough, which a new session's task is not.
    """
    main, worktree = _two_trees(tmp_path)
    row = _row("a", cwd=worktree)
    state = LauncherState(task="carry on", cwd=main, policy=Policy.AUTONOMOUS)
    state.picker = _loaded(row)
    state.picker.pick(row, state.cwd)

    assert not state.premise_is_thin
    assert state.launch_hold() == launcher.RESUME_HOLD_CHECKING
    assert state.press() is None

    _settled(state)
    assert state.launch_hold() is None
    pressed = state.press()
    assert pressed is not None and pressed.policy is Policy.AUTONOMOUS
    assert pressed.containment == sandbox.containment_settings()
    assert (pressed.resume, pressed.cwd, pressed.message) == ("a", worktree, "carry on")

    below = LauncherState(task="carry on", cwd=main, policy=Policy.AUTONOMOUS)
    below.picker = _loaded(row)
    below.picker.pick(row, below.cwd)
    worker = below.version_gate.request(prober=_below)
    assert worker is not None
    worker.join(timeout=5.0)
    below.version_gate.poll()
    assert below.launch_hold() == launcher.RESUME_HOLD_CLI
    assert below.press() is None


def test_a_contained_resume_pressed_under_another_reading_starts_nothing(
    tmp_path: Path,
) -> None:
    """
    The section above the button was drawn from a remembered answer about the
    directory. A press that finds a different one was made under a description of
    some other resume -- and a press nothing was drawn for agrees with nothing.
    """
    main, worktree = _two_trees(tmp_path)
    row = _row("a", cwd=worktree)

    unread = LauncherState(task="carry on", cwd=main, policy=Policy.AUTONOMOUS)
    unread.picker = _loaded(row)
    unread.picker.pick(row, unread.cwd)
    _settled(unread)
    assert unread.press() is None, "nothing was remembered, so nothing was shown"
    assert unread.press() is not None, "and that press left its reading to agree with"

    moved = _picked(Path(main), row, policy=Policy.AUTONOMOUS)
    assert moved.launch_hold() is None
    moved._refusal = "what the screen said, and no longer what is found"
    assert moved.press() is None
    assert moved.containment_refusal() is None, "the next frame draws what the press found"

    # An uncontained resume asks the directory nothing it could have misread.
    plain = LauncherState(task="carry on", cwd=main)
    plain.picker = _loaded(row)
    plain.picker.pick(row, plain.cwd)
    assert plain.press() is not None


def test_the_one_rule_for_a_resumes_rung_is_what_both_surfaces_ask() -> None:
    """
    ``resume_gate_hold`` and ``resume_spec`` are the only places a resume's rung is
    granted, and the resume form reaches its spec through them: read off the two
    modules' source, so a second ``LaunchSpec(resume=...)`` built beside them, or a
    form that stopped asking, fails here.
    """
    import ast
    import inspect

    from pptmstr.ui import resume_form

    def builders(module: object) -> list[str]:
        """The functions of ``module`` that construct a ``LaunchSpec`` naming a resume."""
        found = []
        for node in ast.walk(ast.parse(inspect.getsource(module))):  # type: ignore[arg-type]
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "LaunchSpec"
                    and any(k.arg == "resume" for k in call.keywords)
                ):
                    found.append(node.name)
        return found

    assert builders(launcher) == ["resume_spec"]
    assert builders(resume_form) == []
    form = inspect.getsource(resume_form)
    assert "launcher.resume_spec(" in inspect.getsource(resume_form.press)
    assert "launcher.resume_gate_hold(" in inspect.getsource(resume_form.ResumeForm.hold)
    assert "containment_settings" not in form
    for user in (LauncherState.launch_hold, launcher.resume_spec):
        assert "resume_gate_hold(" in inspect.getsource(user)
    assert "resume_spec(" in inspect.getsource(LauncherState.resumed)

    # And the rule itself, over every rung and every answer.
    answered, unanswered = launcher.VersionGate(), launcher.VersionGate()
    worker = answered.request(prober=_meets)
    assert worker is not None
    worker.join(timeout=5.0)
    answered.poll()
    cannot = launcher.VersionGate()
    worker = cannot.request(prober=_below)
    assert worker is not None
    worker.join(timeout=5.0)
    cannot.poll()
    for policy in launcher.POLICIES:
        needs = policy is Policy.AUTONOMOUS
        refused = launcher.RESUME_HOLD_REFUSED.format(rung=policy.value)
        hold = launcher.resume_gate_hold
        assert hold(policy, None, answered) is None
        assert hold(policy, "no", answered) == (refused if needs else None)
        assert hold(policy, "no", unanswered) == (refused if needs else None)
        assert hold(policy, None, unanswered) == (launcher.RESUME_HOLD_CHECKING if needs else None)
        assert hold(policy, None, cannot) == (launcher.RESUME_HOLD_CLI if needs else None)
        for refusal, gate in (("no", answered), (None, unanswered), (None, cannot)):
            built = launcher.resume_spec(
                session_id="a",
                cwd="/srv/repo",
                policy=policy,
                refusal=refusal,
                gate=gate,
                task="t",
                task_is_label=False,
                message="m",
                model=DEFAULT_MODEL,
                template=None,
                brief=None,
                subagent_cap=None,
            )
            if needs:
                assert isinstance(built, launcher.ResumeHeld)
            else:
                assert isinstance(built, LaunchSpec) and built.containment is None


def test_a_failed_resume_comes_back_as_a_resume_of_the_same_session(
    tmp_path: Path,
) -> None:
    """
    The press clears the pick with the task. A failure handed back without it
    would put the message into the box of a draft that starts a new session, so
    the draft carries the row and the restore picks it again.
    """
    main, worktree = _two_trees(tmp_path)
    row = _row("a", cwd=worktree)
    state = _picked(Path(main), row, task="carry on")
    pressed = state.press()
    draft = state.draft()
    assert pressed is not None and draft.picked == row

    state.launched()
    assert state.picker.picked is None and state.task == ""
    assert state.draft() == launcher.left_by_launch(draft)
    after = state.press()
    assert after is not None and after.resume is None

    state.report_failure(draft, "it did not start")
    assert state.task == "carry on" and state.picker.picked == row
    assert state.failure_loaded
    again = state.press()
    assert again is not None and (again.resume, again.message) == ("a", "carry on")

    # Restored over fields that already have that session picked, it stays picked:
    # picking the row that is the pick is how a pick is undone, and a restore is not.
    state.failures.clear()
    state.task = "something typed since"
    assert state.picker.picked == row
    state.report_failure(draft, "nor this time")
    state.restore_failed()
    assert state.task == "carry on" and state.picker.picked == row

    # A new session's failure restored over a pick clears it: the draft is whole.
    fresh = LauncherState(task="a new task", cwd=main).draft()
    state.failures.clear()
    state.report_failure(fresh, "nor did that")
    state.restore_failed()
    assert state.task == "a new task" and state.picker.picked is None


def test_picking_nothing_leaves_todays_fresh_launch_spec_untouched(tmp_path: Path) -> None:
    """
    The default path. A draft nobody opened the resume section on must produce byte
    for byte the spec it produced before the section existed.
    """
    cwd = str(tmp_path.resolve())
    state = LauncherState(task="  audit the parser  ", cwd=cwd, model=FALLBACK_MODELS[1])
    assert state.spec() == LaunchSpec(
        task="audit the parser",
        model=FALLBACK_MODELS[1],
        cwd=cwd,
        session_base=cwd,
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


def test_the_pick_is_the_row_that_was_clicked_and_none_when_nothing_is_picked() -> None:
    picker = _loaded(_row("a"), _row("b"))
    assert picker.picked is None
    picker.select("b", "/srv/repo")
    assert picker.picked is picker.rows[1]


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

    picker = _loaded(_row("a", cwd=str(project)))
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


def test_launch_hands_the_picked_session_to_the_driver() -> None:
    """
    The wiring, end to end, because this is the exact shape of defect this codebase
    has paid for twice. ``_launch`` builds an ``AgentSession`` by keyword from a
    ``LaunchSpec``, and a field the constructor call forgets is a field nothing
    complains about: the session starts, it just starts fresh, and the operator's
    lost conversation stays lost while the UI says it was resumed.
    """
    import time as _time

    from pptmstr.app import AppState, _launch, _why_not_started
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.settings import Settings
    from pptmstr.store import Store

    started: list[AgentSession] = []

    class _RecordingPool:
        def submit(self, session: AgentSession) -> bool:
            started.append(session)
            return True

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=Settings())
        state.pool = _RecordingPool()  # type: ignore[assignment]
        _launch(
            state,
            LaunchSpec(task="carry on", model=DEFAULT_MODEL, cwd="/tmp", resume="sess-42"),
        )
        outcome = state.launches[0].future
        for _ in range(400):
            if outcome.done():
                break
            _time.sleep(0.005)
    finally:
        bridge.stop()

    # Started, and decided as started: a pool that answers falsy is the pool saying
    # it is shut, and ``_launch`` would be reporting this as a launch refused.
    assert _why_not_started(outcome) is None
    assert [s.resume for s in started] == ["sess-42"]
    # And the id is the resumed one, not a freshly minted uuid -- which is what makes
    # `brief.session_dir` land on the directory the original session wrote into.
    assert [s.session_id for s in started] == ["sess-42"]


def test_launch_without_a_pick_mints_a_fresh_id() -> None:
    import time as _time

    from pptmstr.app import AppState, _launch, _why_not_started
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.settings import Settings
    from pptmstr.store import Store

    started: list[AgentSession] = []

    class _RecordingPool:
        def submit(self, session: AgentSession) -> bool:
            started.append(session)
            return True

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=Settings())
        state.pool = _RecordingPool()  # type: ignore[assignment]
        _launch(state, LaunchSpec(task="do a thing", model=DEFAULT_MODEL, cwd="/tmp"))
        outcome = state.launches[0].future
        for _ in range(400):
            if outcome.done():
                break
            _time.sleep(0.005)
    finally:
        bridge.stop()

    assert _why_not_started(outcome) is None
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


def _measures(fake: MagicMock) -> None:
    """
    The numbers the modal does arithmetic on: how tall the display is and where the
    cursor stands. ``draw`` bounds its body with them, and a bare mock compares
    with nothing.
    """
    fake.FLT_MAX = 3.0e38
    fake.get_main_viewport.return_value.work_size.y = 900.0
    fake.get_cursor_pos.return_value.y = 0.0
    fake.get_style.return_value.window_padding.y = 8.0
    fake.get_style.return_value.display_safe_area_padding.y = 3.0
    fake.get_style.return_value.item_spacing.y = 4.0
    fake.get_frame_height_with_spacing.return_value = 24.0


def _run_draw(
    monkeypatch: pytest.MonkeyPatch,
    state: LauncherState,
    *,
    click_launch: bool = False,
    submit: bool = False,
    prober: launcher.Prober = _meets,
    settle: bool = True,
    subagent_cap: int | None = None,
    drafts: list[launcher.Draft] | None = None,
    press: str | None = None,
    cancel: bool = False,
    live: frozenset[str] = frozenset(),
    expand: bool = False,
    click_row: int | None = None,
    recorder: launcher.Recorder = lambda session_id: None,
) -> tuple[MagicMock, list[LaunchSpec]]:
    """
    Run ``draw`` against a fake imgui and hand back the fake and what was launched.

    ``drafts`` collects the second argument of the launch callback, for the tests
    that are about what a launch can be handed back. ``press`` is the label of a
    small button to answer True for, and ``cancel`` presses the cancel button.

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

    ``expand`` opens the session list and ``click_row`` is the index of the row in
    it that answers as clicked.
    """
    if settle:
        worker = state.version_gate.request(prober=prober)
        if worker is not None:
            worker.join(timeout=5.0)
        state.version_gate.poll()

    fake = MagicMock()
    fake.begin_popup_modal.side_effect = lambda *a, **k: (True, True)
    fake.collapsing_header.side_effect = lambda *a, **k: expand
    drawn_rows = iter(range(10_000))
    fake.selectable.side_effect = lambda *a, **k: (next(drawn_rows) == click_row, False)
    fake.get_content_region_avail.return_value.x = 400.0
    fake.calc_text_size.return_value.x = 100.0
    fake.checkbox.side_effect = lambda label, value: (False, value)
    fake.combo.side_effect = lambda label, index, items: (False, index)
    fake.input_text_with_hint.side_effect = lambda label, hint, value: (False, value)
    fake.input_int.side_effect = lambda label, value, *a, **k: (False, value)
    fake.button.side_effect = lambda label, *a, **k: (
        (click_launch and label == "launch") or (cancel and label == "cancel")
    )
    fake.small_button.side_effect = lambda label, *a, **k: label == press
    fake.is_key_pressed.side_effect = lambda *a, **k: False
    # No window from an earlier frame, so ``draw`` has nothing to move back into
    # view; a bare mock here would be a window whose position cannot be compared.
    fake.internal.find_window_by_name.return_value = None
    _measures(fake)

    fake_widgets = MagicMock()
    fake_widgets.ellipsis.side_effect = lambda text, width: text
    fake_widgets.flat_line.side_effect = widgets.flat_line
    fake_widgets.multiline_input.side_effect = lambda *a, **k: (submit, state.task)
    # The two gate helpers run for real, because what they return is the thing under
    # test rather than scenery. They are pure -- set lookups and the shell scan, no
    # imgui and no IO -- so calling through costs nothing a fake would have saved. Left
    # as bare mocks they are worse than useless here: ``MagicMock`` iterates empty and
    # is truthy, so ``adds`` came back as the string "adds " and a test asserting the
    # rung's grant is named would have passed against every rung equally.
    fake_widgets.gate_adds.side_effect = widgets.gate_adds
    fake_widgets.gate_parks.side_effect = widgets.gate_parks
    # The values a draw builds are the real ones, so what was said can be read off
    # the calls; the drawing of them is recorded and not done.
    fake_widgets.Help = widgets.Help
    fake_widgets.Notice = widgets.Notice
    fake_widgets.Tone = widgets.Tone
    fake_widgets.path_tail.side_effect = widgets.path_tail
    fake_widgets.help_showing.return_value = False
    fake.widgets = fake_widgets

    monkeypatch.setattr(launcher, "imgui", fake)
    monkeypatch.setattr(launcher, "widgets", fake_widgets)

    launched: list[LaunchSpec] = []

    def launch(spec: LaunchSpec, draft: launcher.Draft) -> None:
        launched.append(spec)
        if drafts is not None:
            drafts.append(draft)

    state.is_open = True
    launcher.draw(
        state,
        running=0,
        queued=0,
        cap=4,
        launch=launch,
        wrap=True,
        scanner=lambda cwd: ((), {}),
        prober=prober,
        subagent_cap=subagent_cap,
        live=live,
        recorder=recorder,
    )
    return fake, launched


def _lines(fake: MagicMock) -> list[str]:
    """
    Everything the frame put into words on screen, whatever colour it used to do
    it: what is drawn without a question mark being pressed.
    """
    lines = [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]
    lines += [str(a) for call in fake.text_disabled.call_args_list for a in call.args]
    lines += [str(call.args[0]) for call in fake.checkbox.call_args_list]
    lines += [call.args[0].line for call in fake.widgets.notice.call_args_list]
    return lines


def _notices(fake: MagicMock) -> list[widgets.Notice]:
    """Every one line the frame drew through ``widgets.notice``, as the value it was."""
    return [call.args[0] for call in fake.widgets.notice.call_args_list]


def _helps(fake: MagicMock) -> list[widgets.Help]:
    """Every explanation the frame put behind a question mark."""
    helps = [said.help for said in _notices(fake) if said.help is not None]
    helps += [widgets.Help(*call.args) for call in fake.widgets.help_mark.call_args_list]
    return helps


def _behind(fake: MagicMock) -> list[str]:
    """Every paragraph behind a question mark on the frame: one press away."""
    return [paragraph for said in _helps(fake) for paragraph in said.paragraphs]


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


def _explained(
    monkeypatch: pytest.MonkeyPatch,
    state: LauncherState,
    *,
    prober: launcher.Prober = _meets,
    settle: bool = True,
    subagent_cap: int | None = None,
) -> tuple[list[str], list[str]]:
    """
    What one frame draws, as ``_drawn`` has it, and beside it what is behind the
    frame's question marks.
    """
    fake, _ = _run_draw(monkeypatch, state, prober=prober, settle=settle, subagent_cap=subagent_cap)
    return _lines(fake), _behind(fake)


def _told(
    monkeypatch: pytest.MonkeyPatch,
    state: LauncherState,
    *,
    prober: launcher.Prober = _meets,
    settle: bool = True,
    subagent_cap: int | None = None,
) -> tuple[list[str], list[LaunchSpec]]:
    """
    What the operator is told on one frame by pressing its question marks: the
    paragraphs behind them, in place of what ``_drawn`` says is on the screen.
    """
    fake, launched = _run_draw(
        monkeypatch, state, prober=prober, settle=settle, subagent_cap=subagent_cap
    )
    return _behind(fake), launched


# -- what the draft turns into -----------------------------------------------------


def test_the_mode_is_off_until_it_is_asked_for() -> None:
    """
    The default launch is today's launch. Everything else in this section is about
    what happens when an operator departs from it deliberately.
    """
    assert LauncherState().policy is Policy.STRICT


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
    spec = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS).spec()
    assert spec.containment == sandbox.containment_settings()
    assert spec.policy is Policy.AUTONOMOUS


def test_nothing_about_the_rest_of_the_draft_moves_when_the_mode_is_on(
    tmp_path: Path,
) -> None:
    """
    The mode is two fields and not a different launch. Anything else it changed would
    be a second behaviour riding on a checkbox whose label says one thing.
    """
    picked = FALLBACK_MODELS[1]
    off = LauncherState(task=A_PREMISE, cwd=str(tmp_path), model=picked, brief=" b ").spec()
    on = LauncherState(
        task=A_PREMISE,
        cwd=str(tmp_path),
        model=picked,
        brief=" b ",
        policy=Policy.AUTONOMOUS,
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
    state = LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, policy=Policy.AUTONOMOUS)
    assert state.policy is Policy.AUTONOMOUS
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
    for rung in Policy:
        fake, _ = _run_draw(
            monkeypatch, LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=rung)
        )
        assert "gate" in [call.args[0] for call in fake.combo.call_args_list], rung
        # Not the control alone: what the chosen rung adds is the line under it, and
        # the top rung's is the alarm, so the posture is readable without working it
        # out from a one-word name. What it still holds is one press away.
        under = (
            launcher.LOUD_LINE if rung is Policy.AUTONOMOUS else launcher.dial_text(rung, rung).adds
        )
        assert under in _lines(fake), rung
        assert any("still parks" in paragraph for paragraph in _behind(fake)), rung
        assert not any("still parks" in line for line in _lines(fake)), rung


def test_the_dial_does_not_understate_what_the_top_rung_grants(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The hazard is a description narrower than the grant, which is worse than none: an
    operator who reads "run Bash unattended" and chooses it has consented to one tool
    and been given every tool the gate would have held. ``AUTONOMOUS`` releases the
    whole of ``approval._REVIEW``, so the screen has to name more than one kind.

    The line under the dial is the alarm, and it names two kinds -- files written
    and commands run -- in one sentence the operator cannot miss. The whole of what
    the rung adds is behind its question mark, derived from the classifier by
    ``widgets.gate_adds`` rather than written out, which is what stops it
    understating a rung that later grows: the former hand-written label could be
    widened by an edit to ``approval.py`` alone.
    """
    lines, behind = _explained(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS),
    )
    assert launcher.LOUD_LINE in lines
    assert "write files" in launcher.LOUD_LINE and "run commands" in launcher.LOUD_LINE
    adds = next(paragraph for paragraph in behind if paragraph.startswith("adds "))
    # More than one kind, and none of them the single tool the old label named.
    assert "writes" in adds and "spawns" in adds and "messages" in adds


def test_the_middle_rung_is_not_described_as_the_top_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The rung whose name most oversells it. ``PERMISSIVE`` reads as "the gate is off"
    and it is not -- it adds read-only shell and nothing else -- so the screen has to
    say so where the choice is made, or the name is the only thing calibrated against.

    The shell probes are the discriminating pair: a rung that admitted both would be
    admitting commands that act, which is the claim ``shellscan`` exists to refuse.
    """
    lines, behind = _explained(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.PERMISSIVE),
    )
    adds = next(line for line in lines if line.startswith("adds "))
    parks = next(paragraph for paragraph in behind if paragraph.startswith("still parks "))
    assert "shell reads" in adds
    assert "shell that writes" in parks
    for held in ("writes", "spawns", "messages", "the web"):
        assert held in parks, held


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
    lines, behind = _explained(
        monkeypatch, LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    )
    assert launcher.LOUD_LINE in lines
    released = next(paragraph for paragraph in behind if paragraph.startswith("on -- "))
    assert "writes files" in released and "spawns sub-agents" in released
    assert "never heard of is still not released" in released
    assert any(sandbox.ALLOWED_DOMAIN in paragraph for paragraph in behind)
    assert not any("credential" in line for line in [*lines, *behind])


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
    _, behind = _explained(
        monkeypatch, LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    )
    uncontained = [paragraph for paragraph in behind if "not contained" in paragraph]
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
    _, behind = _explained(
        monkeypatch, LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    )
    sandboxed = next(p for p in behind if "contained:" in p and "Bash" in p)
    assert sandbox.ALLOWED_DOMAIN in sandboxed
    gated = next(p for p in behind if "held to that same directory" in p)
    assert "CLI process" in gated and "refused" in gated


def test_the_uncontained_edge_is_not_said_when_the_mode_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``WebFetch`` parks at the operator under ``STRICT``, so there is no uncontained edge
    to warn about -- the operator is the bound. A warning shown at every policy would
    train the eye past it by the time it means something.
    """
    lines, behind = _explained(monkeypatch, LauncherState(task="t", cwd=str(tmp_path)))
    assert not any("not contained" in line for line in [*lines, *behind])


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
    _, behind = _explained(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS),
        subagent_cap=8,
    )
    capped = next(p for p in behind if "sub-agents are capped" in p)
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
    lines, behind = _explained(
        monkeypatch, LauncherState(task="t", cwd=str(tmp_path)), subagent_cap=8
    )
    assert not any("sub-agents are capped" in line for line in [*lines, *behind])


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
    _, behind = _explained(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS, subagent_cap=2),
        subagent_cap=8,
    )

    capped = next(p for p in behind if "sub-agents are capped" in p)
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
    state = LauncherState(task="make it autonomous", cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    assert state.launch_hold() == launcher._HOLD_THIN

    _, launched = _drawn(monkeypatch, state, click_launch=True)
    assert launched == []
    assert state.task == "make it autonomous"
    assert state.policy is Policy.AUTONOMOUS

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
    state = LauncherState(task="one line", cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
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
    state = LauncherState(
        task="continue", cwd=str(tmp_path), policy=Policy.AUTONOMOUS, brief="/tmp/b"
    )
    assert state.premise_is_thin is False
    # Not "is None": this draft has not read a version yet, so the version gate holds it
    # for its own reason. What is asserted is that the premise is no longer one of them.
    assert state.launch_hold() != launcher._HOLD_THIN
    assert LauncherState(
        task="continue", cwd=str(tmp_path), policy=Policy.AUTONOMOUS
    ).premise_is_thin


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
    state = LauncherState(task="one line", cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
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
        LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, policy=Policy.AUTONOMOUS),
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
    refusal is the line under the dial, and why is behind its question mark.
    """
    lines, behind = _explained(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, policy=Policy.AUTONOMOUS),
    )
    assert launcher.REFUSED_RUNS_LOWER.format(asked="autonomous", granted="strict") in lines
    assert any("refused" in p and "checkout" in p for p in behind)
    assert not any("checkout" in line for line in lines)


def test_the_modal_launches_the_mode_it_was_showing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The wiring, not the unit. ``spec()`` carrying the fields buys nothing unless the
    button hands *that* spec to the launch callback.
    """
    _, launched = _drawn(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS),
        click_launch=True,
    )
    assert [s.policy for s in launched] == [Policy.AUTONOMOUS]
    assert launched[0].containment == sandbox.containment_settings()


def test_a_launch_from_a_refused_directory_carries_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, launched = _drawn(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, policy=Policy.AUTONOMOUS),
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
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    _, launched = _drawn(monkeypatch, state, click_launch=True)

    assert launched[0].containment is not None
    assert state.policy is Policy.STRICT
    assert state.spec().containment is None


def test_cancelling_keeps_the_mode_the_operator_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The draft survives a dismissal on purpose -- going to look up a directory must not
    cost what was already chosen. Only the launch clears it.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    _drawn(monkeypatch, state)
    assert state.policy is Policy.AUTONOMOUS


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
    state = LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, policy=Policy.AUTONOMOUS)

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
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)

    fake, launched = _run_draw(monkeypatch, state, click_launch=True, prober=_below)
    lines, behind = _lines(fake), _behind(fake)

    assert launched == []
    assert state.task == A_PREMISE
    # And the mode is still ticked, so correcting the machine does not also mean
    # remembering what was asked for.
    assert state.policy is Policy.AUTONOMOUS
    assert launcher.CLI_HOLDS.format(rung="autonomous", what="launch") in lines
    assert any("2.1.9" in p and cli_version.SANDBOX_FLOOR in p for p in behind)
    # Both halves of the refusal: the button is visibly dead, and something beside it
    # says why a press did nothing.
    assert fake.begin_disabled.called
    assert launcher.hold_notice(launcher._HOLD_REFUSED) in _notices(fake)


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
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)

    fake, launched = _run_draw(monkeypatch, state, click_launch=True, prober=_unreadable)

    assert launched == []
    assert state.task == A_PREMISE
    assert any("could not be read" in p and "no claude on PATH" in p for p in _behind(fake))


def test_a_cli_that_clears_the_floor_launches_the_mode_it_was_showing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The control the three refusals above are worthless without. A gate that held every
    launch would pass every one of them and ship a mode nobody can use.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)

    fake, launched = _run_draw(monkeypatch, state, click_launch=True, prober=_meets)
    said = [*_lines(fake), *_behind(fake)]

    assert [s.policy for s in launched] == [Policy.AUTONOMOUS]
    assert launched[0].containment == sandbox.containment_settings()
    assert not any("refused on this machine" in line for line in said)
    assert not any(launcher._HOLD_LINE in line for line in said)


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

    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    try:
        fake, launched = _run_draw(monkeypatch, state, click_launch=True, prober=slow, settle=False)

        assert launched == []
        assert state.task == A_PREMISE
        assert state.launch_hold() == launcher._HOLD_CHECKING
        # Not silence: the line beside the button says the launch is waiting, and
        # what it is waiting on is behind the marks of that line and the alarm's.
        assert launcher.HOLD_LINES[launcher._HOLD_CHECKING] in _lines(fake)
        assert launcher._HOLD_CHECKING in _behind(fake)
        assert any(launcher._HOLD_LINE in p for p in _behind(fake))
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
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)

    _, refused = _drawn(monkeypatch, state, submit=True, prober=_below)
    assert refused == []
    assert state.task == A_PREMISE

    # The same key, on a machine that clears the floor, still launches.
    ok = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
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
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)

    behind: list[str] = []
    for _ in range(400):
        fake, launched = _run_draw(
            monkeypatch, state, click_launch=True, prober=prober, settle=False
        )
        behind = _behind(fake)
        assert launched == []
        if state.version_gate.answered:
            break
        time.sleep(0.005)

    assert calls == [1]
    assert state.version_gate.answered
    assert any("2.1.9" in p for p in behind)


def test_the_version_is_read_once_and_not_once_a_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The memo, which is the difference between one subprocess and sixty a second. The
    modal redraws continuously while it is up, so an unmemoised read is a fork per
    frame for as long as the box stays ticked.
    """
    prober, calls = _counted(_meets)
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)

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


# -- the model combo, once the list is probed rather than fixed ----------------------


def test_a_pinned_model_the_offered_list_lacks_still_draws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The combo needs a *position* even though the draft holds a *name*, and a name the
    list does not contain has none. Drawing it means prepending it to the options; not
    doing so raises ``ValueError`` out of ``list.index`` on the frame the modal opens,
    which takes the whole UI down rather than mis-selecting quietly.

    Reachable today from ``--model`` and, once a probe has run, from any pick made
    while the probed list was live and offered again after it failed.
    """
    pinned = "claude-a-model-no-list-offers"
    assert pinned not in FALLBACK_MODELS

    state = LauncherState(task="t", model=pinned)
    state.request_open()

    _drawn(monkeypatch, state)

    assert state.model == pinned


# ---------------------------------------------------------------------------
# A resume runs where the session was recorded
# ---------------------------------------------------------------------------
#
# The picker lists through the SDK's worktree fold, so a row's own directory and the
# directory in the cwd field are routinely different. Every test below constructs that
# difference, because a test where the two agree cannot tell a launch that reads the
# row from one that reads the field.


def _two_trees(tmp_path: Path) -> tuple[str, str]:
    """
    A main checkout and a worktree of it, as two real directories. Returns both
    resolved, in that order: the one the field names and the one the row recorded.
    """
    main = tmp_path / "repo"
    worktree = tmp_path / "repo-worktrees" / "feature"
    main.mkdir()
    worktree.mkdir(parents=True)
    return str(main.resolve()), str(worktree.resolve())


def test_a_pick_recovers_the_brief_under_the_recorded_directory_and_not_the_one_handed_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Both candidates exist on disk, so the assertion is about which one was chosen
    rather than about which one happened to be there. The field's is the decoy: a
    brief some other run wrote under the main checkout's slug for the same id.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    main, worktree = _two_trees(tmp_path)
    briefs = {
        tree: tmp_path / ".claude" / "projects" / tree.replace("/", "-") / "briefs" / "a"
        for tree in (main, worktree)
    }
    for directory in briefs.values():
        directory.mkdir(parents=True)

    picker = SessionPicker()
    picker.pick(_row("a", cwd=worktree), main)

    assert picker.recovered_brief == str(briefs[worktree])


def test_a_resume_in_the_directory_it_is_compared_with_is_not_flagged_as_differing(
    tmp_path: Path,
) -> None:
    """
    "Obvious when it differs" has two halves: a line that warned every time would
    carry nothing on the one resume where it matters. The resume form hands in the
    session's own directory, so there the quiet sentence is the only one drawn.
    """
    main, worktree = _two_trees(tmp_path)
    directory = launcher.launch_directory(_row("a", cwd=f"{main}/../repo"), ".")
    assert directory == launcher.RecordedDirectory(main)

    same = launcher.resume_directory_line(directory, main)
    other = launcher.resume_directory_line(directory, worktree)

    assert same == f"runs in {main} -- where this session was recorded"
    assert other is not None and other.startswith(f"runs in {main} ")
    assert "NOT the working directory above" in other


def test_a_picked_session_says_where_it_runs_and_what_resuming_it_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    What the launch button does has changed, and the lines that say so are drawn
    whether or not the list is open: a pick behind a shut header is still what the
    button does. With nothing picked none of them is drawn.
    """
    main, worktree = _two_trees(tmp_path)
    state = LauncherState(task="t", cwd=main)
    state.picker = _loaded(_row("a", cwd=worktree))

    for expand in (False, True):
        fake, launched = _run_draw(monkeypatch, state, live=frozenset({"a"}), expand=expand)
        said = [*_lines(fake), *_behind(fake)]
        assert not any("runs in" in line or "was recorded" in line for line in said)
        assert launcher._LIVE_RESUME not in said and launcher._PICKED_SENDS not in said
        assert launcher._TASK_CAPTION in said and launcher._MESSAGE_CAPTION not in said
        assert launched == []

    state.picker.pick(state.picker.rows[0], state.cwd)
    where = (
        f"runs in {worktree} -- where this session was recorded, NOT the working directory above"
    )
    for expand in (False, True):
        fake, launched = _run_draw(monkeypatch, state, live=frozenset({"a"}), expand=expand)
        lines, behind = _lines(fake), _behind(fake)
        # One line each for where it runs and for what resuming it ends, with the
        # sentences behind their marks; what the button now does, and what the
        # pick set, behind the mark beside "launch fresh instead".
        assert launcher.directory_notice(launcher.RecordedDirectory(worktree), main) in _notices(
            fake
        )
        assert any(line.endswith(", NOT the working directory above") for line in lines)
        assert where in behind and where not in lines
        assert launcher._LIVE_LINE in lines
        assert launcher._LIVE_RESUME in behind and launcher._LIVE_RESUME not in lines
        assert launcher._PICKED_SENDS in behind and launcher._PICKED_CHOSEN_HERE in behind
        assert launcher._PICKED_SENDS not in lines and launcher._PICKED_CHOSEN_HERE not in lines
        assert launcher._MESSAGE_CAPTION in lines and launcher._TASK_CAPTION not in lines
        assert launched == []
        (header,) = [str(call.args[0]) for call in fake.collapsing_header.call_args_list[:1]]
        assert header == f"resuming: summary for a{launcher._RESUME_ID}"

    # Not live: the directory is still said and the ending of a run is not.
    fake, _ = _run_draw(monkeypatch, state)
    assert where in _behind(fake) and launcher._LIVE_RESUME not in _behind(fake)
    assert launcher._LIVE_LINE not in _lines(fake)


def test_a_message_to_a_picked_session_is_not_called_a_teams_premises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Nothing is written into a brief for a resume, so the line that says the box
    becomes every worker's premises is a new session's only.
    """
    premises = "on a team, this also becomes the premises every worker reads"
    state = LauncherState(task="t", cwd=str(tmp_path), template_index=1)
    state.picker = _loaded(_row("a", cwd=str(tmp_path)))
    fake, _ = _run_draw(monkeypatch, state)
    assert premises in _behind(fake) and premises not in _lines(fake)
    state.picker.pick(state.picker.rows[0], state.cwd)
    fake, _ = _run_draw(monkeypatch, state)
    assert premises not in [*_lines(fake), *_behind(fake)]


def test_a_session_recorded_through_a_symlink_has_one_brief_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A resume's spec carries the recorded directory resolved (``resolved_directory``
    of ``launch_directory``, which is what ``resume_form.press`` takes) and the pick
    derives the brief from the same reading. ``app._seed_brief`` builds the brief
    directory from the spec's, so the two have to name one place or a resumed team
    reads a brief the pick did not find.
    """
    from pptmstr import brief as brief_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    row = _row("a", cwd=str(link))

    started_in = launcher.resolved_directory(launcher.launch_directory(row, str(tmp_path)))
    assert started_in == str(real.resolve()) != str(link)
    seeded = brief_mod.session_dir(brief_mod.default_root(), started_in, "a")
    seeded.mkdir(parents=True)

    picker = SessionPicker()
    picker.pick(row, str(tmp_path))
    assert picker.recovered_brief == str(seeded)


def test_an_id_the_listing_does_not_carry_is_not_a_pick() -> None:
    """
    Where a resume runs is read off its row. An id with no row would be a resume with
    nothing to say where, and the field is what would answer.
    """
    picker = _loaded(_row("a"))
    picker.select("nobody", "/srv/repo")
    assert picker.selected is None
    assert picker.picked is None


def test_a_draft_stats_nothing_to_find_its_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The draw asks every frame. With nothing picked the directory is the field's and
    there is no recorded one to look for, so looking costs nothing.
    """

    def _no_stat(path: object) -> bool:
        raise AssertionError(f"stat of {path!r} to find where a new task runs")

    state = LauncherState(task="t", cwd=str(tmp_path))
    state.picker = _loaded(_row("a"), _row("b"))
    expected = state.spec()
    monkeypatch.setattr(os.path, "isdir", _no_stat)

    assert state.launch_directory() == launcher.FieldDirectory(str(tmp_path))
    assert state.launch_hold() is None
    assert state.spec() == expected


def test_the_contained_region_is_named_as_the_resumed_sessions_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    "Writes only inside the working directory above" is false for a resume, whose
    directory is not a field, and it is false about the one boundary the operator
    is being asked to trust. ``contained_text`` is what the resume form draws.
    """
    main, worktree = _two_trees(tmp_path)

    contained = launcher.contained_text(
        launcher.RecordedDirectory(worktree), launcher.git_layout(worktree)
    ).sandbox
    assert contained.startswith("contained:") and worktree in contained
    assert "the working directory above" not in contained

    # And New Task's is about its field.
    _, behind = _explained(
        monkeypatch, LauncherState(task=A_PREMISE, cwd=main, policy=Policy.AUTONOMOUS)
    )
    contained = next(p for p in behind if p.startswith("contained:"))
    assert "writes only inside the working directory above." in contained


def test_the_scanner_is_still_asked_about_the_field_and_not_about_the_pick(
    tmp_path: Path,
) -> None:
    """
    The listing answers "what can be recovered from here", which is a question about
    the field, whatever was picked on it.
    """
    main, worktree = _two_trees(tmp_path)
    state = LauncherState(task="carry on", cwd=main)
    state.picker = _loaded(_row("a", cwd=worktree))
    state.picker.pick(state.picker.rows[0], state.cwd)
    seen: list[str] = []
    state.request_open()

    worker = state.begin_open(scanner=_recording_scanner(seen))
    assert worker is not None
    worker.join(timeout=5.0)

    assert seen == [main]


# ---------------------------------------------------------------------------
# A launch that started nothing gives its draft back
# ---------------------------------------------------------------------------
#
# The press clears the fields and closes the modal on the frame it happens; whether a
# session started is decided later, on another thread. What these pin is that the
# draft handed out with the spec is enough to undo the clears, and that a failure
# reported back cannot be missed or cost text -- the failed launch's or anyone else's.


def _failed_tasks(failures: list[launcher.FailedLaunch]) -> list[str | None]:
    """
    The task text each failed launch was pressed from, None for one with no draft.
    """
    return [None if f.draft is None else f.draft.task for f in failures]


def _full_draft(tmp_path: Path) -> LauncherState:
    """
    A draft with every field moved off its default, and the top rung asked for in a
    directory that refuses it -- so what was *asked for* and what the spec was
    *granted* differ, and a restore built from the spec would show it.
    """
    return LauncherState(
        task="  fix the parser\nand say why  ",
        cwd=INSIDE_THE_CHECKOUT,
        brief="  /some/brief  ",
        model=FALLBACK_MODELS[1],
        template_index=1,
        policy=Policy.AUTONOMOUS,
        subagent_cap=2,
    )


def _press_launch(
    monkeypatch: pytest.MonkeyPatch, state: LauncherState
) -> tuple[LaunchSpec, launcher.Draft]:
    drafts: list[launcher.Draft] = []
    _, launched = _run_draw(monkeypatch, state, click_launch=True, drafts=drafts)
    assert len(launched) == 1 and len(drafts) == 1
    return launched[0], drafts[0]


def test_a_launch_that_started_nothing_gets_every_field_back_as_it_was_typed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Field by field against what was on screen, not against the spec: the spec strips
    the task, resolves the directory and carries the policy as granted.
    """
    state = _full_draft(tmp_path)
    before = state.draft()
    spec_before = state.spec()

    spec, draft = _press_launch(monkeypatch, state)

    # The press did what it always does, so there is something to restore from.
    assert state.task == "" and state.policy is Policy.STRICT
    assert state.subagent_cap is None
    assert not state.is_open

    state.report_failure(draft, "git worktree add failed: path exists")

    assert state.draft() == before
    assert state.task == "  fix the parser\nand say why  "
    assert state.draft().policy is Policy.AUTONOMOUS
    # And it is the same launch again.
    assert state.spec() == spec_before == spec


class _RealFrames:
    """
    ``launcher.draw`` against Dear ImGui itself: a context with a display size and no
    backend. Nothing is rendered and nothing needs to be, because what these tests
    are about is in the widgets' update -- which the fake cannot show, since it
    echoes whatever value it is handed.

    ``imgui`` is the module with ``small_button`` and ``selectable`` wrapped to note
    where each one was drawn, so a test can press one with the mouse.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, state: LauncherState) -> None:
        from imgui_bundle import imgui

        self.imgui = imgui
        self.state = state
        self.launched: list[tuple[LaunchSpec, launcher.Draft]] = []
        self.picked: list[SessionRow] = []
        self.scanner: launcher.Scanner = lambda cwd: ((), {})
        # Set to draw the session list open, as a click on its header leaves it.
        self.open_list = False
        self.popup_open = False
        self.buttons: dict[str, tuple[float, float]] = {}
        # For ``button``: the rectangle it was given, top and bottom, and the name
        # of the window it was drawn in.
        self.placed: dict[str, tuple[float, float, str]] = {}
        self._monkeypatch = monkeypatch

    def __enter__(self) -> _RealFrames:
        imgui = self.imgui
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 900.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        buttons = self.buttons
        outer = self

        class Noting:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            def small_button(self, label: str) -> bool:
                pressed = imgui.small_button(label)
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                buttons[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)
                return bool(pressed)

            def selectable(self, label: str, selected: bool) -> tuple[bool, bool]:
                answer = imgui.selectable(label, selected)
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                buttons[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)
                return answer

            def button(self, label: str, size: object) -> bool:
                pressed = imgui.button(label, size)  # type: ignore[arg-type]
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                buttons[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)
                outer.placed[label] = (low.y, high.y, imgui.internal.get_current_window().name)
                return bool(pressed)

            def collapsing_header(self, label: str) -> bool:
                if outer.open_list:
                    imgui.set_next_item_open(True)
                return bool(imgui.collapsing_header(label))

        self._monkeypatch.setattr(launcher, "imgui", Noting())
        return self

    def __exit__(self, *exc: object) -> None:
        self.imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            self.imgui.new_frame()
            launcher.draw(
                self.state,
                running=0,
                queued=0,
                cap=4,
                launch=lambda spec, draft: self.launched.append((spec, draft)),
                wrap=True,
                scanner=self.scanner,
                prober=_meets,
            )
            # Asked inside the frame, at the level ``draw`` opened it from.
            self.popup_open = bool(self.imgui.is_popup_open(launcher.TITLE))
            self.imgui.end_frame()

    def type(self, text: str) -> None:
        self.io.add_input_characters_utf8(text)
        self.frame(2)

    def ctrl_enter(self) -> None:
        keys = (self.imgui.Key.mod_ctrl, self.imgui.Key.left_ctrl, self.imgui.Key.enter)
        for key in keys:
            self.io.add_key_event(key, True)
        self.frame()
        for key in reversed(keys):
            self.io.add_key_event(key, False)
        self.frame()

    def click(self, label: str) -> None:
        x, y = self.buttons[label]
        self.io.add_mouse_pos_event(x, y)
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(3)

    def modal_was_drawn(self) -> bool:
        """
        Whether Dear ImGui held the popup open and drew its window on the last frame:
        its own account, which ``LauncherState.is_open`` is only this module's copy
        of. Both, because a popup left open behind a draw that returned early is
        still on Dear ImGui's stack with no window to show for it.
        """
        window = self.imgui.internal.find_window_by_name(launcher.TITLE)
        drawn = window is not None and bool(window.active)
        assert drawn == self.popup_open, "the popup and its window disagree"
        return drawn

    def body(self) -> object:
        """The scrolling child the modal's fields are drawn in, as Dear ImGui holds it."""
        prefix = f"{launcher.TITLE}/{launcher.BODY_ID}"
        (found,) = [
            window
            for window in self.imgui.get_current_context().windows
            if window.name.startswith(prefix) and window.name.count("/") == 1
        ]
        return found

    def task_box_is_focused(self) -> bool:
        active = self.imgui.internal.get_active_id()
        return active != 0 and self.imgui.internal.get_input_text_state(active) is not None


def test_a_draft_restored_under_the_focused_task_box_is_still_there_a_frame_later(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The rule: a focused InputText keeps its own copy of the text and copies it over
    the caller's every frame. The operator launches, opens the modal again with the
    cursor in the empty task box, and the failure lands. The draft assigned into
    ``state.task`` lasts one frame unless the widget is told to read it, and the
    notice above it says nothing was lost.

    The last step assigns from outside *without* telling the widget, so the rule is
    observed here and not assumed -- if a later Dear ImGui drops it, that assertion
    is what says so.
    """
    state = LauncherState(cwd=str(tmp_path))
    state.request_open()
    with _RealFrames(monkeypatch, state) as real:
        real.frame(3)
        real.type("the first task")
        real.ctrl_enter()
        assert [spec.task for spec, _ in real.launched] == ["the first task"]
        assert not state.is_open and state.task == ""

        state.request_open()
        real.frame(3)
        assert state.is_open and state.task == "" and real.task_box_is_focused()

        state.report_failure(real.launched[0][1], "no such base ref")
        assert state.task == "the first task"
        real.frame(3)
        assert real.task_box_is_focused()
        assert state.task == "the first task"
        assert state.failure_loaded

        # Typing lands in the restored text, so the widget holds it and not merely
        # the variable.
        real.type("!")
        assert state.task == "the first task!"

        state.task = "assigned from outside, and the widget not told"
        real.frame()
        assert state.task == "the first task!"


def test_a_failure_that_reopens_the_modal_puts_its_draft_in_the_box_dear_imgui_draws(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The ordinary order: the press closes the modal and the answer reopens it. The
    box is not focused on the frame the draft arrives and is on the next.
    """
    state = LauncherState(cwd=str(tmp_path))
    state.request_open()
    with _RealFrames(monkeypatch, state) as real:
        real.frame(3)
        real.type("the first task")
        real.ctrl_enter()
        assert not state.is_open and state.task == ""

        state.report_failure(real.launched[0][1], "no such base ref")
        real.frame(4)

        assert state.is_open and real.task_box_is_focused()
        assert state.task == "the first task"
        real.type(" again")
        assert state.task == "the first task again"


def test_restore_pressed_with_the_mouse_over_a_box_holding_typed_text_swaps_the_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The button takes the focus on the way down, so the box is *not* active when the
    draft is assigned -- the other state the load has to be right in. Word wrap is
    on, as it is by default: telling an inactive wrapped box to reload its text is
    an assertion inside Dear ImGui, and a ``RuntimeError`` out of ``draw``.
    """
    state = LauncherState(cwd=str(tmp_path))
    state.request_open()
    with _RealFrames(monkeypatch, state) as real:
        real.frame(3)
        real.type("the first task")
        real.ctrl_enter()
        state.request_open()
        real.frame(3)
        real.type("a second task, half typed")
        state.report_failure(real.launched[0][1], "no such base ref")
        real.frame(2)
        assert state.task == "a second task, half typed" and not state.failure_loaded

        real.click("restore that draft")
        assert state.task == "the first task" and state.failure_loaded
        assert [d.task for d in state.set_aside] == ["a second task, half typed"]

        real.click("bring back what was set aside")
        assert state.task == "a second task, half typed"
        assert state.set_aside == [] and len(state.failures) == 1


def test_a_failed_launch_reopens_the_modal_with_the_reason_on_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = LauncherState(task="carry on", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    assert not state.is_open

    state.report_failure(draft, "the CLI reports 2.1.9, below the floor")

    assert state._open_requested
    fake, launched = _run_draw(monkeypatch, state)
    assert launched == []
    # On screen, one line with the start of the reason; behind its mark, the reason
    # whole and what became of the draft.
    assert any("did not start a session" in line and "2.1.9" in line for line in _lines(fake))
    assert any("nothing was lost" in p for p in _behind(fake))
    assert not any("nothing was lost" in line for line in _lines(fake))


def test_a_launch_that_started_leaves_no_notice_and_no_draft_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Nothing reports a launch that worked, so this is the press alone: the clears it
    made stand, the modal is shut, and nothing asks for it back.
    """
    state = _full_draft(tmp_path)

    _press_launch(monkeypatch, state)

    assert state.failures == [] and not state.failure_loaded
    assert not state.is_open and not state._open_requested
    assert state.task == "" and state.policy is Policy.STRICT
    assert state.subagent_cap is None
    lines, _ = _drawn(monkeypatch, state)
    assert not any("did not start" in line for line in lines)


def test_a_failure_does_not_overwrite_what_the_operator_typed_since(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The answer comes from another thread and can arrive after the next draft is half
    made. Every field of that draft is the operator's, the ones a press leaves alone
    included, and restoring over it would spend one draft to save another -- by the
    load, or by the button.
    """
    state = LauncherState(task="the first task", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    state.task = "a second task, half typed"
    state.cwd, state.brief = "/somewhere/else", "/a/brief/typed/since"
    state.model, state.template_index = FALLBACK_MODELS[1], 1
    state.policy, state.subagent_cap = Policy.PERMISSIVE, 3
    typed = state.draft()

    state.report_failure(draft, "no such base ref")

    assert state.draft() == typed
    assert not state.failure_loaded
    assert _failed_tasks(state.failures) == ["the first task"]
    lines, behind = _explained(monkeypatch, state)
    assert any("no such base ref" in line for line in lines)
    assert any("its draft is kept" in p for p in behind)
    assert not any("set aside by a restore" in line for line in [*lines, *behind])

    # The restore is the operator's press, it is wired to the button, and it is a
    # swap: the frame that restores says where the displaced draft went.
    fake, _ = _run_draw(monkeypatch, state, press="restore that draft")
    assert state.draft() == draft
    assert state.failure_loaded
    assert state.set_aside == [typed]
    assert any("1 draft(s) set aside by a restore are kept" in line for line in _lines(fake))

    # And back again, to exactly where the restore was pressed from.
    _run_draw(monkeypatch, state, press="bring back what was set aside")
    assert state.draft() == typed
    assert state.set_aside == []
    assert _failed_tasks(state.failures) == ["the first task"]
    assert not state.failure_loaded


@pytest.mark.parametrize(
    "moved",
    [
        {"cwd": "/somewhere/else"},
        {"brief": "/a/brief/typed/since"},
        {"model": FALLBACK_MODELS[1]},
        {"template_index": 1},
        {"policy": Policy.PERMISSIVE},
        {"subagent_cap": 3},
    ],
)
def test_one_field_moved_since_the_press_is_enough_to_keep_the_failed_draft_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, moved: dict[str, object]
) -> None:
    """
    The task box is empty in every case, so a rule that read only it would load the
    failed draft over each of these.
    """
    state = LauncherState(task="the first task", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    for name, value in moved.items():
        setattr(state, name, value)
    typed = state.draft()
    assert typed.task == ""

    state.report_failure(draft, "reason")

    assert state.draft() == typed
    assert not state.failure_loaded

    state.restore_failed()
    assert state.draft() == draft
    assert state.set_aside == [typed]


def test_what_a_launch_leaves_is_what_the_press_actually_left(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``left_by_launch`` is a second spelling of ``LauncherState.launched``'s clears,
    and "nothing typed since" is measured against it. If the two drift, a failure
    either never loads or loads over something.
    """
    state = _full_draft(tmp_path)
    before = state.draft()
    assert before != launcher.left_by_launch(before)

    _press_launch(monkeypatch, state)

    assert state.draft() == launcher.left_by_launch(before)


def test_restoring_over_fields_already_held_sets_nothing_aside(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Two failures, the first loaded. Dismissing it leaves its text in the fields with
    nothing holding it; restoring the second over that has to keep it. Restoring the
    first again over the second, which the queue still holds, has nothing to keep.
    """
    state = LauncherState(task="task one", cwd=str(tmp_path))
    _, first = _press_launch(monkeypatch, state)
    state.task = "task two"
    _, second = _press_launch(monkeypatch, state)
    state.report_failure(first, "reason one")
    state.report_failure(second, "reason two")
    assert state.failure_loaded and state.task == "task one"

    state.discard_failed()
    assert state.task == "task one" and not state.failure_loaded

    state.restore_failed()
    assert state.task == "task two" and state.failure_loaded
    assert state.set_aside == [first]

    state.restore_set_aside()
    assert state.task == "task one"
    assert state.set_aside == []
    assert _failed_tasks(state.failures) == ["task two"]


def test_discarding_what_was_set_aside_is_a_press_and_touches_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = LauncherState(task="the first task", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    state.task = "a second task"
    state.report_failure(draft, "reason")
    state.restore_failed()
    assert len(state.set_aside) == 1

    _run_draw(monkeypatch, state, press="discard what was set aside")

    assert state.set_aside == []
    assert state.task == "the first task" and state.failure_loaded
    assert len(state.failures) == 1


def test_a_draft_set_aside_is_still_offered_once_the_failure_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The retry of the restored draft takes the failure with it. What the restore
    displaced is not part of that launch and has to outlive it, with a notice of its
    own on a modal that has no failure to show.
    """
    state = LauncherState(task="the first task", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    state.task = "a second task"
    state.report_failure(draft, "reason")
    state.restore_failed()

    spec, _ = _press_launch(monkeypatch, state)

    assert spec.task == "the first task"
    assert state.failures == [] and state.task == ""
    assert [d.task for d in state.set_aside] == ["a second task"]
    lines, _ = _drawn(monkeypatch, state)
    assert not any("did not start" in line for line in lines)
    assert any("1 draft(s) set aside by a restore are kept" in line for line in lines)

    _run_draw(monkeypatch, state, press="bring back what was set aside")
    assert state.task == "a second task" and state.set_aside == []


def test_bringing_a_draft_back_over_text_nothing_holds_sets_that_aside_in_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The swap in the other direction. The failure was dismissed with its draft in the
    fields, so nothing holds that text any more; bringing the set-aside draft back
    over it has to keep it, or the bring-back is the press that costs a draft.
    """
    state = LauncherState(task="the first task", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    state.task = "a second task"
    state.report_failure(draft, "reason")
    state.restore_failed()
    state.discard_failed()
    assert state.failures == [] and state.task == "the first task"

    state.restore_set_aside()

    assert state.task == "a second task"
    assert state.set_aside == [draft]


def test_the_notice_stops_saying_as_it_was_typed_once_the_box_is_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Loaded is a comparison of the fields on this frame. Remembered instead, a box
    emptied after the load goes on reading "nothing was lost" with no way back to
    the text.
    """
    state = LauncherState(task="carry on", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    state.report_failure(draft, "reason")
    _, behind = _explained(monkeypatch, state)
    assert any("nothing was lost" in p for p in behind)

    for emptied_or_edited in ("", "carry on, differently"):
        state.task = emptied_or_edited
        fake, _ = _run_draw(monkeypatch, state)
        behind = _behind(fake)
        assert not any("nothing was lost" in p for p in behind)
        assert any("its draft is kept" in p for p in behind)
        assert any(
            call.args[0] == "restore that draft" for call in fake.small_button.call_args_list
        )

    _run_draw(monkeypatch, state, press="restore that draft")
    assert state.task == "carry on"
    _, behind = _explained(monkeypatch, state)
    assert any("nothing was lost" in p for p in behind)


def test_an_edited_retry_does_not_take_the_failed_draft_with_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The fields are not the failed draft once they are edited, so the press is not
    its retry and its text is still owed: it loads again behind the launch.
    """
    state = LauncherState(task="carry on", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    state.report_failure(draft, "reason")
    state.task = "carry on, differently"

    spec, _ = _press_launch(monkeypatch, state)

    assert spec.task == "carry on, differently"
    assert _failed_tasks(state.failures) == ["carry on"]
    assert state.task == "carry on" and state.failure_loaded
    assert state._open_requested


def test_discarding_a_failure_drops_it_and_leaves_the_typed_text_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = LauncherState(task="the first task", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    state.task = "a second task"
    state.report_failure(draft, "no such base ref")

    _run_draw(monkeypatch, state, press="discard it")

    assert state.failures == []
    assert state.task == "a second task"


def test_two_failed_launches_each_keep_their_own_draft_and_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Two presses in flight, both refused. The second report must not replace the
    first's draft, and retrying the first must bring the second up rather than
    leaving it in a list nothing draws.
    """
    state = LauncherState(task="task one", cwd=str(tmp_path))
    _, first = _press_launch(monkeypatch, state)
    state.task = "task two"
    _, second = _press_launch(monkeypatch, state)

    state.report_failure(first, "reason one")
    state.report_failure(second, "reason two")

    assert list(
        zip(_failed_tasks(state.failures), [f.reason for f in state.failures], strict=True)
    ) == [
        ("task one", "reason one"),
        ("task two", "reason two"),
    ]
    assert state.task == "task one" and state.failure_loaded
    lines, behind = _explained(monkeypatch, state)
    assert any("reason one" in line for line in lines)
    assert not any("reason two" in line for line in [*lines, *behind])
    assert any("(1 more behind it)" in line for line in lines)
    assert any("1 more launch" in p for p in behind)

    # The retry of the first. It takes its failure with it and the second loads.
    spec, _ = _press_launch(monkeypatch, state)
    assert spec.task == "task one"
    assert list(
        zip(_failed_tasks(state.failures), [f.reason for f in state.failures], strict=True)
    ) == [("task two", "reason two")]
    assert state.task == "task two" and state.failure_loaded
    assert state._open_requested


def test_a_launch_pressed_past_an_unrestored_failure_does_not_drop_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The failure is only a notice here -- the operator launched what they had typed
    since. Its draft has been neither restored nor discarded, so it is still owed.
    """
    state = LauncherState(task="task one", cwd=str(tmp_path))
    _, first = _press_launch(monkeypatch, state)
    state.task = "task two"
    state.report_failure(first, "reason one")
    assert not state.failure_loaded

    spec, _ = _press_launch(monkeypatch, state)

    assert spec.task == "task two"
    assert _failed_tasks(state.failures) == ["task one"]
    # The fields are blank after the press, so the kept draft is what fills them.
    assert state.task == "task one" and state.failure_loaded


def test_cancel_closes_a_modal_that_is_showing_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Unmissable is not the same as inescapable. The failure stays for the next open;
    the modal does not reopen itself over an operator who pressed cancel.
    """
    state = LauncherState(task="carry on", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    state.report_failure(draft, "reason")
    state.begin_open(scanner=lambda cwd: ((), {}))

    _run_draw(monkeypatch, state, cancel=True)

    assert not state.is_open and not state._open_requested
    assert len(state.failures) == 1 and state.task == "carry on"


def test_a_restored_top_rung_is_asked_for_its_premise_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The rung comes back as asked for, and the thin-premise hold is computed from the
    fields it came back into. The version gate's half of "held again" is
    ``test_a_failure_puts_the_version_gate_back_to_unanswered``; here the gate has
    answered fine, so the premise is the only thing holding.
    """
    state = LauncherState(task="one line", cwd=str(tmp_path), policy=Policy.STRICT)
    draft = replace(state.draft(), policy=Policy.AUTONOMOUS)
    state.task = ""

    state.report_failure(draft, "reason")

    assert state.policy is Policy.AUTONOMOUS
    assert state.launch_hold() == launcher._HOLD_THIN
    _, launched = _run_draw(monkeypatch, state, click_launch=True)
    assert launched == []
    assert state.version_gate.answered and state.version_gate.refusal is None
    assert state.launch_hold() == launcher._HOLD_THIN


def test_a_failure_puts_the_version_gate_back_to_unanswered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The gate said the CLI was fine, the launch path read it again and refused. Handed
    back under the gate's old answer, the draft has a live button that launches into
    the same refusal. The second read is what holds it -- and here it disagrees with
    the first, which is the only way the refusal could have reached this far.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    drafts: list[launcher.Draft] = []
    _, launched = _run_draw(monkeypatch, state, click_launch=True, prober=_meets, drafts=drafts)
    assert len(launched) == 1 and state.version_gate.answered

    state.report_failure(drafts[0], "the CLI reports 2.1.9, below the floor")

    assert state.draft().policy is Policy.AUTONOMOUS
    assert not state.version_gate.answered
    assert state.launch_hold() == launcher._HOLD_CHECKING

    _, relaunched = _run_draw(monkeypatch, state, click_launch=True, prober=_below)
    assert relaunched == []
    assert state.launch_hold() == launcher._HOLD_REFUSED
    assert state.task == A_PREMISE


def test_a_failure_the_modal_did_not_launch_is_shown_without_touching_the_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A relaunch pressed in another pane. There is no draft to restore, so the hazard
    runs the other way: a reason drawn above a blank task box, or above the
    operator's own text, with nothing saying it is about neither.
    """
    state = LauncherState(cwd=str(tmp_path))

    state.report_failure(None, "the CLI reports 2.1.9, below the floor", "relaunch")

    assert state._open_requested
    assert state.task == "" and not state.failure_loaded
    lines, behind = _explained(monkeypatch, state)
    assert any(
        "that relaunch did not start a session" in line and "2.1.9" in line for line in lines
    )
    assert any("not made from this window" in p for p in behind)
    assert not any(
        "nothing was lost" in line or "its draft is kept" in line for line in [*lines, *behind]
    )

    # Typing and launching past it neither drops it nor reopens the modal over it.
    state.task = "something else"
    _press_launch(monkeypatch, state)
    assert len(state.failures) == 1 and not state.failure_loaded
    assert state.task == "" and not state._open_requested

    _run_draw(monkeypatch, state, press="dismiss this notice")
    assert state.failures == []


def test_a_draftless_failure_does_not_hide_the_drafts_behind_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = LauncherState(task="task one", cwd=str(tmp_path))
    _, first = _press_launch(monkeypatch, state)

    state.report_failure(None, "fork reason", "fork")
    state.report_failure(first, "reason one")

    assert state.task == "" and not state.failure_loaded
    lines, behind = _explained(monkeypatch, state)
    assert any("(1 more behind it)" in line for line in lines)
    assert any("1 more launch" in p for p in behind)

    state.discard_failed()

    assert state.task == "task one" and state.failure_loaded


def test_dismissing_the_notice_over_a_restored_draft_keeps_the_draft(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = LauncherState(task="carry on", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    state.report_failure(draft, "reason")
    assert state.failure_loaded

    _run_draw(monkeypatch, state, press="dismiss this notice")

    assert state.failures == [] and not state.failure_loaded
    assert state.task == "carry on"


# ---------------------------------------------------------------------------
# The contained half does not claim a boundary narrower than the measured one
# ---------------------------------------------------------------------------
#
# planning/2026-10-07-a-session-can-run-in-its-own-worktree.md D2: in a linked git
# worktree a contained Bash also writes the git directory shared with the main
# repository. "Writes only inside the working directory" is false there, and it is
# the sentence the operator is trusting when they press launch.


def _git_dir(path: Path) -> Path:
    """
    A directory holding what git asks of a git directory before it will use one: a
    ``HEAD`` naming a ref, ``objects`` and ``refs``. An empty directory called
    ``.git`` is not one, to git or to ``git_layout``.
    """
    (path / "objects").mkdir(parents=True)
    (path / "refs").mkdir()
    (path / "HEAD").write_text("ref: refs/heads/main\n")
    return path


def _worktree_of(shared: Path, worktree: Path) -> tuple[str, str]:
    """
    ``worktree`` as a linked worktree sharing the git directory ``shared``, in the
    layout ``git worktree add`` leaves. Returns both, resolved.
    """
    admin = shared / "worktrees" / "x"
    admin.mkdir(parents=True)
    (admin / "commondir").write_text("../..\n")
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / ".git").write_text(f"gitdir: {admin}\n")
    return str(worktree.resolve()), str(shared.resolve())


def _linked_worktree(
    tmp_path: Path, *, relative: bool = False, at: Path | None = None
) -> tuple[str, str]:
    """
    A main repository and a linked worktree of it, built by hand in the layout
    ``git worktree add`` leaves (pinned against real git by
    ``test_git_itself_lays_worktrees_and_submodules_out_the_way_the_read_assumes``).
    Returns the worktree and the shared git directory, both resolved.

    ``at`` is a directory that already exists and is to become the worktree.
    """
    git_dir = tmp_path / "main" / ".git"
    admin = git_dir / "worktrees" / "wt"
    admin.mkdir(parents=True)
    (admin / "commondir").write_text("../..\n")
    worktree = at or tmp_path / "wt"
    worktree.mkdir(exist_ok=True)
    pointer = "../main/.git/worktrees/wt" if relative else str(admin)
    (worktree / ".git").write_text(f"gitdir: {pointer}\n")
    return str(worktree.resolve()), str(git_dir.resolve())


def _submodule_shaped(tmp_path: Path) -> str:
    """
    A directory whose ``.git`` is a pointer file with no ``commondir`` behind it, at
    the one path where the pointer's *shape* reads as a worktree's.
    """
    module = tmp_path / "super" / ".git" / "modules" / "worktrees" / "x"
    module.mkdir(parents=True)
    checkout = tmp_path / "super" / "worktrees" / "x"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: ../../.git/modules/worktrees/x\n")
    return str(checkout.resolve())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("gitdir: /r/.git/worktrees/w\n", "/r/.git/worktrees/w"),
        ("gitdir: ../.git/modules/sub\n", "../.git/modules/sub"),
        ("gitdir:/no/space", "/no/space"),
        ("gitdir: /first\ngitdir: /second\n", "/first"),
        ("gitdir: \n", None),
        ("", None),
        ("ref: refs/heads/main\n", None),
        ("  gitdir: /indented\n", None),
    ],
)
def test_only_a_gitdir_line_is_read_as_a_pointer(text: str, expected: str | None) -> None:
    assert launcher.gitdir_pointer(text) == expected


def test_git_itself_lays_worktrees_and_submodules_out_the_way_the_read_assumes(
    tmp_path: Path,
) -> None:
    """
    The measurement the classifier rests on, re-taken against whatever git is
    installed: a linked worktree's admin directory has a ``commondir`` and nothing
    else's does. The hand-built fixtures above are only as good as this.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("no git on this machine")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
        LC_ALL="C",
    )

    def git(cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", *args],
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
            timeout=60,
        )

    def repo(name: str) -> Path:
        path = tmp_path / name
        git(tmp_path, "init", "-q", "-b", "main", name)
        (path / "f").write_text("x\n")
        git(path, "add", "f")
        git(path, "commit", "-qm", "one")
        return path

    main, other = repo("R"), repo("S")
    git(main, "worktree", "add", "-q", "-b", "feat", str(tmp_path / "W"))
    git(main, "submodule", "add", "-q", str(other), "sub")
    git(main, "submodule", "add", "-q", str(other), "worktrees/x")
    git(main / "sub", "worktree", "add", "-q", "-b", "subfeat", str(tmp_path / "SW"))
    git(tmp_path, "clone", "-q", "--bare", "R", "B.git")
    git(tmp_path / "B.git", "worktree", "add", "-q", "-b", "bw", str(tmp_path / "BW"))
    git(tmp_path, "init", "-q", f"--separate-git-dir={tmp_path / 'SEP.git'}", "SEP")

    def shared(path: Path) -> str | None:
        return launcher.shared_git_dir(str(path))

    assert shared(tmp_path / "W") == str((main / ".git").resolve())
    assert shared(tmp_path / "BW") == str((tmp_path / "B.git").resolve())
    assert shared(tmp_path / "SW") == str((main / ".git" / "modules" / "sub").resolve())
    # Not worktrees: the main checkout, a subdirectory, two submodules -- one of them
    # at the path whose pointer ends ``worktrees/x`` -- and a separate git dir.
    assert shared(main) is None
    assert shared(main / "sub") is None
    assert shared(main / "worktrees" / "x") is None
    assert shared(tmp_path / "SEP") is None
    (tmp_path / "W" / "deeper").mkdir()
    assert shared(tmp_path / "W" / "deeper") is None

    # And the layout each of them is told it is in, which is what words the boundary.
    def layout(path: Path) -> launcher.GitLayout:
        return launcher.git_layout(str(path))

    def worktree(root: Path, git_dir: Path, *, at_root: bool = True) -> launcher.LinkedWorktree:
        return launcher.LinkedWorktree(
            shared=str(git_dir.resolve()), root=str(root.resolve()), at_root=at_root
        )

    git(tmp_path, "clone", "-q", "R", "C")
    assert layout(main) is None
    assert layout(tmp_path / "C") is None
    assert layout(tmp_path) is None
    assert layout(tmp_path / "W") == worktree(tmp_path / "W", main / ".git")
    assert layout(tmp_path / "BW") == worktree(tmp_path / "BW", tmp_path / "B.git")
    assert layout(tmp_path / "W" / "deeper") == worktree(
        tmp_path / "W", main / ".git", at_root=False
    )
    outside = launcher.OutsideGit
    assert layout(main / "sub") == outside(str((main / ".git" / "modules" / "sub").resolve()))
    assert layout(tmp_path / "SEP") == outside(str((tmp_path / "SEP.git").resolve()))
    (main / "src").mkdir()
    assert layout(main / "src") == outside(str((main / ".git").resolve()))
    (tmp_path / "C" / "src").mkdir()
    assert layout(tmp_path / "C" / "src") == outside(str((tmp_path / "C" / ".git").resolve()))

    # And which worktree each one is. Only the first is the layout D2 measured.
    git(main, "worktree", "add", "-q", "-b", "nest", str(main / ".claude" / "worktrees" / "n"))
    nested = layout(main / ".claude" / "worktrees" / "n")
    assert nested == worktree(main / ".claude" / "worktrees" / "n", main / ".git")
    kinds = {
        "W": launcher.WorktreeKind.BESIDE,
        "BW": launcher.WorktreeKind.UNORDINARY,
        "SW": launcher.WorktreeKind.UNORDINARY,
    }
    for name, kind in kinds.items():
        found = layout(tmp_path / name)
        assert isinstance(found, launcher.LinkedWorktree) and found.kind is kind, name
    assert isinstance(nested, launcher.LinkedWorktree)
    assert nested.kind is launcher.WorktreeKind.NESTED


def test_a_submodule_at_a_worktree_shaped_path_is_not_called_a_worktree(tmp_path: Path) -> None:
    """
    Its pointer ends ``modules/worktrees/x``. Read by shape, the parent directory is
    ``worktrees`` and this is a linked worktree; nothing was measured about a submodule.
    """
    assert launcher.shared_git_dir(_submodule_shaped(tmp_path)) is None


def test_a_relative_pointer_resolves_against_the_worktree_and_not_this_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree, git_dir = _linked_worktree(tmp_path, relative=True)
    # Somewhere the pointer does not resolve from: ``../main/...`` taken from inside
    # ``main/.git`` names ``main/main``, which does not exist.
    monkeypatch.chdir(tmp_path / "main" / ".git")
    assert launcher.shared_git_dir(worktree) == git_dir


def test_a_pointer_to_nowhere_is_not_an_error_on_the_draw_thread(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").write_text(f"gitdir: {tmp_path / 'gone' / '.git' / 'worktrees' / 'w'}\n")
    assert launcher.shared_git_dir(str(broken)) is None
    assert launcher.shared_git_dir(str(tmp_path / "no-such-directory")) is None
    assert launcher.shared_git_dir(str(tmp_path)) is None


def test_a_linked_worktree_is_not_told_bash_writes_only_inside_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The sentence that is false there, and what replaces it: the shared git directory
    by path, what was reached in it, what was refused, and the CLI it was read on.
    """
    worktree, git_dir = _linked_worktree(tmp_path)
    state = LauncherState(task=A_PREMISE, cwd=worktree, policy=Policy.AUTONOMOUS)

    fake, launched = _run_draw(monkeypatch, state)
    lines = _behind(fake)

    assert not any("writes only inside" in line for line in lines)
    assert not any("held to that same directory" in line for line in lines)
    wider = next(line for line in lines if "linked git worktree" in line)
    assert "that directory is a linked git worktree, and Bash also writes" in wider
    assert git_dir in wider
    assert "HEAD" in wider and "branches" in wider
    assert "2.1.292" in wider
    # Drawn as a warning and not as greyed reassurance.
    assert wider not in _lines(fake)
    gate = next(line for line in lines if line.startswith("file writes are held to"))
    assert "alone" in gate and "shared git directory included" in gate
    assert launched == []


def test_the_worktree_line_denies_only_the_paths_that_were_tried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    D2 measured three named paths refused, and ``git config`` refused on the
    config's lock, and lists the other routes to the same hooks and config as not
    attempted: the denial names those four and says the rest is open. It measured
    the main checkout's files refused and its branch movable, so the two are said
    together and neither reads as the other. Each row that was one run or two says
    so. Sub-agents were not measured in a worktree at all.
    """
    worktree, _ = _linked_worktree(tmp_path)
    lines, _ = _told(
        monkeypatch, LauncherState(task=A_PREMISE, cwd=worktree, policy=Policy.AUTONOMOUS)
    )
    wider = next(line for line in lines if "linked git worktree" in line)

    assert "Not its hooks or config" not in wider
    assert "at the three paths tried: hooks/pre-commit, config, and" in wider
    assert "config.worktree" in wider
    assert "and git config, refused on the config's lock in three runs" in wider
    assert "No other route to the hooks or the config was tried" not in wider
    assert "No route to the hooks or the config beyond those four was tried" in wider

    assert "so the main checkout and every other worktree of it" not in wider
    assert "the main checkout's own files were refused" in wider
    assert "its index in the one run that tried" in wider
    # The three rows of D2's table that are writes and were one run or two.
    assert (
        "a new file at the top of that git directory (two runs, and a third by another "
        "spelling of the path)"
    ) in wider
    assert "a rebase-merge/ directory there (one run)" in wider
    assert "a file in another worktree's admin directory (one run)" in wider

    contained = next(line for line in lines if line.startswith("contained:"))
    assert "fan-out reaches no further" not in contained
    assert "Every sub-agent gets that same boundary" not in contained
    assert "Whether a sub-agent gets that same boundary was not measured" in contained


def test_a_directory_that_is_not_a_worktree_reads_exactly_as_it_did(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Character for character, for the two layouts the narrow measurement was taken
    in: a directory under no git at all, and a checkout launched at its root with
    its git directory inside it.
    """
    checkout = tmp_path / "checkout"
    _git_dir(checkout / ".git")
    for directory in (str(tmp_path), str(checkout)):
        lines, _ = _told(
            monkeypatch, LauncherState(task=A_PREMISE, cwd=directory, policy=Policy.AUTONOMOUS)
        )
        assert (
            f"contained: Bash and anything it starts run in a sandbox that reaches "
            f"{sandbox.ALLOWED_DOMAIN} and nothing else, and writes only inside the working "
            "directory above. Every sub-agent gets that same boundary, so a fan-out reaches "
            "no further than one agent does."
        ) in lines
        assert (
            "file writes are held to that same directory by the gate rather than by the "
            "sandbox, because Write and Edit run in the CLI process -- a target that "
            "cannot be shown to be inside it is refused."
        ) in lines
        assert not any("linked git worktree" in line for line in lines)
        assert not any("not measured" in line for line in lines)


def _unmeasured_layouts(tmp_path: Path) -> dict[str, tuple[str, str]]:
    """
    The layouts whose git directory lies outside the launch directory and which are
    not a linked worktree, each as (launch directory, the git directory to be named).
    Built by hand in the shapes git leaves; the real ones are pinned by
    ``test_git_itself_lays_worktrees_and_submodules_out_the_way_the_read_assumes``.
    """
    module = tmp_path / "super" / ".git" / "modules" / "sub"
    module.mkdir(parents=True)
    submodule = tmp_path / "super" / "sub"
    submodule.mkdir()
    (submodule / ".git").write_text("gitdir: ../.git/modules/sub\n")

    separate_git = tmp_path / "SEP.git"
    separate_git.mkdir()
    separate = tmp_path / "SEP"
    separate.mkdir()
    (separate / ".git").write_text(f"gitdir: {separate_git}\n")

    under_checkout = tmp_path / "plain" / "src" / "deep"
    under_checkout.mkdir(parents=True)
    _git_dir(tmp_path / "plain" / ".git")

    elsewhere = _git_dir(tmp_path / "elsewhere.git")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").symlink_to(elsewhere)

    # Governed from above: git passes over the empty ``.git`` beside it.
    passed_over = tmp_path / "plain" / "vendored"
    (passed_over / ".git").mkdir(parents=True)

    def r(path: Path) -> str:
        return str(path.resolve())

    return {
        "a submodule": (r(submodule), r(module)),
        "a worktree-shaped submodule": (
            _submodule_shaped(tmp_path),
            r(tmp_path / "super" / ".git" / "modules" / "worktrees" / "x"),
        ),
        "a separate git dir": (r(separate), r(separate_git)),
        "a directory under a checkout": (r(under_checkout), r(tmp_path / "plain" / ".git")),
        "a .git that is a symlink out": (r(linked), r(elsewhere)),
        "an empty .git under a checkout": (r(passed_over), r(tmp_path / "plain" / ".git")),
    }


def test_a_layout_nothing_was_measured_for_is_not_told_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    "Only" is a measurement taken for a directory git does not reach out of. Each of
    these is governed by a git directory outside it, none is a linked worktree, and
    nothing was measured for any: the section says that, names the git directory,
    and claims neither the narrow boundary nor the worktree's wide one as theirs.
    """
    for name, (directory, git_dir) in _unmeasured_layouts(tmp_path).items():
        assert launcher.git_layout(directory) == launcher.OutsideGit(git_dir), name
        fake, _ = _run_draw(
            monkeypatch, LauncherState(task=A_PREMISE, cwd=directory, policy=Policy.AUTONOMOUS)
        )
        lines = _behind(fake)

        assert not any("writes only inside" in line for line in lines), name
        assert not any("linked git worktree" in line for line in lines), name
        assert not any("fan-out reaches no further" in line for line in lines), name
        contained = next(line for line in lines if line.startswith("contained:"))
        assert "is not measured for this layout" in contained, name
        unmeasured = next(line for line in lines if line.startswith("not measured:"))
        assert git_dir in unmeasured, name
        assert "is not known" in unmeasured, name
        # A warning, not greyed reassurance.
        assert unmeasured not in _lines(fake)
        gate = next(line for line in lines if line.startswith("file writes are held to"))
        assert "alone" in gate and "that git directory included" in gate, name


def test_a_directory_under_a_worktree_is_shown_the_worktrees_boundary_as_unmeasured_there(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    It has no ``.git`` of its own, so asked about by itself it looks like nothing in
    particular. The nearest measurement is the wide one taken at the root, and it is
    shown labelled as that -- not as a measurement of this directory.
    """
    worktree, git_dir = _linked_worktree(tmp_path)
    deeper = Path(worktree) / "src" / "deeper"
    deeper.mkdir(parents=True)
    assert launcher.shared_git_dir(str(deeper)) is None
    assert launcher.git_layout(str(deeper)) == launcher.LinkedWorktree(
        shared=git_dir, root=worktree, at_root=False
    )

    lines, _ = _told(
        monkeypatch, LauncherState(task=A_PREMISE, cwd=str(deeper), policy=Policy.AUTONOMOUS)
    )

    assert not any("writes only inside" in line for line in lines)
    wider = next(line for line in lines if "linked git worktree" in line)
    assert f"that directory is inside a linked git worktree, {worktree}." in wider
    assert "Nothing was measured from a directory under one" in wider
    assert "that directory is a linked git worktree, and" not in wider
    assert git_dir in wider
    assert "at the three paths tried" in wider


def test_the_layout_read_is_total_on_the_draw_thread(tmp_path: Path) -> None:
    """
    A ``.git`` file is bytes off a disk. A NUL in its first line is refused by
    ``open`` with a ``ValueError`` rather than an ``OSError``, and so is a directory
    whose own name carries one.
    """
    nul = tmp_path / "nul"
    nul.mkdir()
    (nul / ".git").write_bytes(b"gitdir: /x\x00y\n")

    assert launcher.shared_git_dir(str(nul)) is None
    assert launcher.git_layout(str(nul)) == launcher.UnreadGit(str(nul.resolve() / ".git"))
    assert launcher.git_layout("a\x00b") is None


def test_a_layout_read_that_raises_does_not_leave_the_last_directorys_answer_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The memo's key belongs to the answer stored beside it. With the key written
    first, a read that raises leaves this directory's key over the previous
    directory's answer, and the next frame serves it.
    """
    worktree, git_dir = _linked_worktree(tmp_path)
    real = launcher.git_layout

    def raising(directory: str) -> launcher.GitLayout:
        if directory == str(tmp_path):
            raise RuntimeError("a read nobody anticipated")
        return real(directory)

    monkeypatch.setattr(launcher, "git_layout", raising)
    state = LauncherState(task=A_PREMISE, cwd=worktree, policy=Policy.AUTONOMOUS)
    assert isinstance(state.git_layout(), launcher.LinkedWorktree)

    state.cwd = str(tmp_path)
    with pytest.raises(RuntimeError):
        state.git_layout()
    with pytest.raises(RuntimeError):
        state.git_layout()


def test_the_refusal_memo_does_not_serve_the_last_directorys_answer_after_a_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The same shape on the memo that decides whether the mode is granted, where the
    stale answer is "not refused" and the directory it is served for is one the
    check never finished looking at.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    assert state.containment_refusal() is None

    def raising(cwd: str, **kwargs: object) -> str | None:
        raise RuntimeError("a resolve nobody anticipated")

    monkeypatch.setattr(launcher, "refusal_for", raising)
    state.cwd = str(tmp_path / "elsewhere")
    with pytest.raises(RuntimeError):
        state.containment_refusal()
    with pytest.raises(RuntimeError):
        state.containment_refusal()


def test_a_directory_with_no_location_is_refused_and_not_raised_about(tmp_path: Path) -> None:
    """
    A NUL in the path raises ``ValueError`` out of the resolve. That is a path that
    cannot be located, which the refusal already covers, and the draw is what asks.
    """
    assert launcher.refusal_for("a\x00b", checkout=str(tmp_path)) is not None

    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=Policy.AUTONOMOUS)
    assert state.dangerous_engaged
    state.cwd = "a\x00b"
    assert state.containment_refusal() is not None
    assert not state.dangerous_engaged


def test_a_worktree_made_while_the_modal_was_shut_is_seen_when_it_opens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The path is typed, judged as a plain directory, and the modal cancelled. Then
    ``git worktree add`` -- here, the files it leaves -- at that same path. The key
    of the memo has not moved and the boundary has.
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()
    state = LauncherState(task=A_PREMISE, cwd=str(worktree), policy=Policy.AUTONOMOUS)
    lines, _ = _told(monkeypatch, state)
    assert any("writes only inside" in line for line in lines)

    _, git_dir = _linked_worktree(tmp_path / "later", at=worktree)
    # Still open: the answer is the one read for this open.
    lines, _ = _told(monkeypatch, state)
    assert any("writes only inside" in line for line in lines)

    state.is_open = False
    state.request_open()
    worker = state.begin_open(scanner=lambda cwd: ((), {}))
    assert worker is not None
    worker.join(timeout=5.0)

    lines, _ = _told(monkeypatch, state)
    assert not any("writes only inside" in line for line in lines)
    assert any("linked git worktree" in line and git_dir in line for line in lines)


def test_the_wider_boundary_is_judged_on_the_directory_the_session_runs_in(
    tmp_path: Path,
) -> None:
    """
    Both ways round, as the resume form asks it: of the session's own directory.
    A session in a worktree is owed the wider statement; one in a plain directory
    would be told something about a tree it is not launched in.
    """
    worktree, git_dir = _linked_worktree(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()

    into = launcher.contained_text(
        launcher.RecordedDirectory(worktree), launcher.git_layout(worktree)
    )
    assert into.wider is not None and "linked git worktree" in into.wider
    assert git_dir in into.wider and "writes only inside" not in into.sandbox

    out = launcher.contained_text(
        launcher.RecordedDirectory(str(plain.resolve())), launcher.git_layout(str(plain))
    )
    assert out.wider is None and "writes only inside" in out.sandbox


def test_the_worktree_read_happens_once_a_directory_and_not_once_a_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree, _ = _linked_worktree(tmp_path)
    asked: list[str] = []
    real = launcher.git_layout

    def counted(directory: str) -> launcher.GitLayout:
        asked.append(directory)
        return real(directory)

    monkeypatch.setattr(launcher, "git_layout", counted)
    state = LauncherState(task=A_PREMISE, cwd=worktree, policy=Policy.AUTONOMOUS)

    for _ in range(3):
        _told(monkeypatch, state)
    assert asked == [worktree]

    state.cwd = str(tmp_path)
    lines, _ = _told(monkeypatch, state)
    assert asked == [worktree, str(tmp_path)]
    # And the answer followed the directory rather than staying the first one's.
    assert not any("linked git worktree" in line for line in lines)


def test_a_launch_that_is_not_taking_the_mode_never_reads_for_a_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The ordinary launch, and the top rung in a directory that refuses it: neither
    draws the contained half, so neither pays for a read to word it.
    """
    worktree, _ = _linked_worktree(tmp_path)

    def never(directory: str) -> launcher.GitLayout:
        raise AssertionError(f"read {directory!r} for a launch with no contained half")

    monkeypatch.setattr(launcher, "git_layout", never)

    _told(monkeypatch, LauncherState(task=A_PREMISE, cwd=worktree, policy=Policy.STRICT))
    _told(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, policy=Policy.AUTONOMOUS),
    )


def test_a_refusal_names_the_directory_it_is_about_in_the_modals_word_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    New Task's refusal is about its field and says so. One about a session's
    recorded directory names that directory: "this directory" there would point at
    the one directory that is not the problem.
    """
    lines, _ = _told(
        monkeypatch,
        LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, policy=Policy.AUTONOMOUS),
    )
    refusal = next(line for line in lines if line.startswith("refused for"))
    assert refusal.startswith("refused for the working directory above --")

    named = launcher._region_phrase(launcher.RecordedDirectory(INSIDE_THE_CHECKOUT))
    about_a_session = launcher.refusal_for(INSIDE_THE_CHECKOUT, named=named)
    assert about_a_session is not None
    assert f"the resumed session's directory, {INSIDE_THE_CHECKOUT}" in about_a_session
    assert "this directory" not in about_a_session


# ---------------------------------------------------------------------------
# Only the worktree that was measured is told the measurement as its own
# ---------------------------------------------------------------------------
#
# D2's worktree arm was one layout: a worktree beside the main checkout of an
# ordinary repository, launched at its root. Every other directory that reads as a
# linked worktree is shown D2's findings as taken there, not as its own.

_AS_ITS_OWN = "that directory is a linked git worktree, and Bash also writes"
_MEASURED_AT = (
    "the root of a linked worktree that sits beside the main checkout of an ordinary repository"
)


def _other_worktrees(tmp_path: Path) -> dict[str, tuple[str, str, launcher.WorktreeKind]]:
    """
    The worktrees D2 had no fixture for, each as (worktree, shared git directory,
    kind). Built by hand; the real ones are pinned by
    ``test_git_itself_lays_worktrees_and_submodules_out_the_way_the_read_assumes``.
    """
    nested = _worktree_of(tmp_path / "R" / ".git", tmp_path / "R" / ".claude" / "worktrees" / "x")
    of_submodule = _worktree_of(tmp_path / "S" / ".git" / "modules" / "sub", tmp_path / "SW")
    of_bare = _worktree_of(tmp_path / "B.git", tmp_path / "BW")
    return {
        "nested in its main checkout": (*nested, launcher.WorktreeKind.NESTED),
        "of a submodule": (*of_submodule, launcher.WorktreeKind.UNORDINARY),
        "of a bare repository": (*of_bare, launcher.WorktreeKind.UNORDINARY),
    }


def test_a_worktree_d2_had_no_fixture_for_is_not_told_its_findings_as_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A nested worktree, a worktree of a submodule and a worktree of a bare
    repository all have a ``commondir`` and all read as linked worktrees. None was
    measured: each is told so, told which layout the findings are from, and shown
    them in the past tense about that one.
    """
    for name, (worktree, shared, kind) in _other_worktrees(tmp_path).items():
        layout = launcher.git_layout(worktree)
        assert layout == launcher.LinkedWorktree(shared=shared, root=worktree, at_root=True), name
        assert isinstance(layout, launcher.LinkedWorktree) and layout.kind is kind, name

        fake, _ = _run_draw(
            monkeypatch, LauncherState(task=A_PREMISE, cwd=worktree, policy=Policy.AUTONOMOUS)
        )
        lines = _behind(fake)

        assert not any(_AS_ITS_OWN in line for line in lines), name
        assert not any("Bash also writes" in line for line in lines), name
        assert not any("writes only inside" in line for line in lines), name
        wider = next(line for line in lines if "linked git worktree" in line)
        assert wider.startswith("and outside it, not measured: that directory is a linked"), name
        assert f"What follows was measured from {_MEASURED_AT}" in wider, name
        assert "In that layout Bash also wrote the git directory that worktree shares" in wider
        assert shared in wider, name
        # The findings themselves are still there to be read.
        assert launcher._WORKTREE_MEASURED in wider, name
        assert wider not in _lines(fake), name


def test_each_unmeasured_worktree_is_told_what_does_not_carry_over(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    "The main checkout's own files were refused" was measured from outside that
    checkout, and a nested worktree is inside it. A worktree of a bare repository
    or a submodule has no main checkout for the branch sentence to be about.
    """
    wider = {}
    for name, (worktree, _, _) in _other_worktrees(tmp_path).items():
        lines, _ = _told(
            monkeypatch, LauncherState(task=A_PREMISE, cwd=worktree, policy=Policy.AUTONOMOUS)
        )
        wider[name] = next(line for line in lines if "linked git worktree" in line)

    nested = wider["nested in its main checkout"]
    assert f"lies inside its main checkout, {(tmp_path / 'R').resolve()}," in nested
    assert "here they surround it, so their refusal is not known to hold" in nested
    for name in ("of a submodule", "of a bare repository"):
        assert "is not the .git of an ordinary checkout" in wider[name], name
        assert "what it says of a main checkout is about that repository's" in wider[name], name
        assert "surround" not in wider[name], name


def test_a_directory_under_an_unmeasured_worktree_is_unmeasured_twice_over(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree, shared, _ = _other_worktrees(tmp_path)["nested in its main checkout"]
    deeper = Path(worktree) / "src"
    deeper.mkdir()

    lines, _ = _told(
        monkeypatch, LauncherState(task=A_PREMISE, cwd=str(deeper), policy=Policy.AUTONOMOUS)
    )

    wider = next(line for line in lines if "linked git worktree" in line)
    assert wider.startswith(
        f"and outside it, not measured: that directory is inside a linked git worktree, "
        f"{worktree}, that lies inside its main checkout"
    )
    assert "Bash also writes" not in wider and shared in wider


def test_the_kind_of_a_worktree_is_read_off_its_two_paths() -> None:
    """
    A sibling whose name merely starts with the main checkout's is beside it, not
    inside it: ``/r/repo-wt`` is not under ``/r/repo``.
    """

    def kind(shared: str, root: str) -> launcher.WorktreeKind:
        return launcher.LinkedWorktree(shared=shared, root=root, at_root=True).kind

    assert kind("/r/repo/.git", "/r/repo-wt") is launcher.WorktreeKind.BESIDE
    assert kind("/r/repo/.git", "/r/elsewhere/wt") is launcher.WorktreeKind.BESIDE
    assert kind("/r/repo/.git", "/r/repo/.claude/worktrees/x") is launcher.WorktreeKind.NESTED
    assert kind("/r/bare.git", "/r/wt") is launcher.WorktreeKind.UNORDINARY
    assert kind("/r/repo/.git/modules/sub", "/r/wt") is launcher.WorktreeKind.UNORDINARY
    # A main checkout at the filesystem root holds every worktree there is.
    assert kind("/.git", "/wt") is launcher.WorktreeKind.NESTED


def test_the_unmeasured_text_names_the_measured_layout_in_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    "Except a linked worktree's root" would cite a nested worktree, or a bare
    repository's, as measured. The exception is the one layout that was.
    """
    for name, (directory, _) in _unmeasured_layouts(tmp_path).items():
        lines, _ = _told(
            monkeypatch, LauncherState(task=A_PREMISE, cwd=directory, policy=Policy.AUTONOMOUS)
        )
        unmeasured = next(line for line in lines if line.startswith("not measured:"))
        assert f"except at {_MEASURED_AT} --" in unmeasured, name
        assert "a linked worktree's root" not in unmeasured, name


# ---------------------------------------------------------------------------
# A .git that git would not use does not speak for the directory holding it
# ---------------------------------------------------------------------------


def test_git_passes_over_a_git_directory_the_read_passes_over(tmp_path: Path) -> None:
    """
    The measurement ``_reads_as_git_dir`` rests on, re-taken against whatever git is
    installed. For each shape of ``.git`` directory under a real checkout, git either
    answers for the directory holding it or passes over it to the checkout above.
    The read may be stricter than git and is never looser: a yes git does not share
    is the one answer that words a boundary as narrower than it is.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("no git on this machine")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")

    def toplevel(cwd: Path) -> str:
        ran = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return ran.stdout.strip()

    subprocess.run(
        ["git", "init", "-q", "-b", "main", "R"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        timeout=60,
    )
    main = (tmp_path / "R").resolve()
    oid = "0123456789" * 4

    def shape(name: str, head: str | None, *directories: str) -> Path:
        git_dir = main / name / ".git"
        git_dir.mkdir(parents=True)
        for directory in directories:
            (git_dir / directory).mkdir()
        if head is not None:
            (git_dir / "HEAD").write_text(head)
        return main / name

    both = ("objects", "refs")
    shapes = {
        shape("empty", None): False,
        shape("no-head", None, *both): False,
        shape("head-alone", "ref: refs/heads/main\n"): False,
        shape("no-refs", "ref: refs/heads/main\n", "objects"): False,
        shape("no-objects", "ref: refs/heads/main\n", "refs"): False,
        shape("garbage-head", "garbage\n", *both): False,
        shape("ref-outside-refs", "ref: heads/main\n", *both): False,
        shape("short-oid", "0123456\n", *both): False,
        shape("whole", "ref: refs/heads/main\n", *both): True,
        shape("no-space", "ref:refs/heads/main\n", *both): True,
        shape("detached", oid + "\n", *both): True,
        shape("detached-sha256", "0" * 64 + "\n", *both): True,
    }
    for directory, used in shapes.items():
        git_uses_it = toplevel(directory) == str(directory)
        assert git_uses_it is used, directory.name
        assert git_uses_it or toplevel(directory) == str(main), directory.name
        assert launcher._reads_as_git_dir(str(directory / ".git")) is used, directory.name
        expected = None if used else launcher.OutsideGit(str(main / ".git"))
        assert launcher.git_layout(str(directory)) == expected, directory.name

    # Stricter than git, in the one place it is: a symlink for a HEAD.
    legacy = shape("symlink-head", None, *both)
    (legacy / ".git" / "HEAD").symlink_to("refs/heads/main")
    assert toplevel(legacy) == str(legacy)
    assert not launcher._reads_as_git_dir(str(legacy / ".git"))


def test_an_empty_git_directory_does_not_earn_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    One ``mkdir`` by a previous session produces it. Under a worktree or a checkout
    the directory is governed from above and is told what a subdirectory of one is
    told; with nothing above, it is a directory with something broken in it, which
    is not what the narrow measurement was taken for.
    """
    worktree, shared = _linked_worktree(tmp_path)
    _git_dir(tmp_path / "plain" / ".git")
    under = {
        "a worktree": Path(worktree) / "inner",
        "a checkout": tmp_path / "plain" / "inner",
        "nothing": tmp_path / "lone" / "inner",
    }
    for directory in under.values():
        (directory / ".git").mkdir(parents=True)
    expected: dict[str, launcher.GitLayout] = {
        "a worktree": launcher.LinkedWorktree(shared=shared, root=worktree, at_root=False),
        "a checkout": launcher.OutsideGit(str((tmp_path / "plain" / ".git").resolve())),
        "nothing": launcher.UnreadGit(str((under["nothing"] / ".git").resolve())),
    }

    for name, directory in under.items():
        assert launcher.git_layout(str(directory)) == expected[name], name
        lines, _ = _told(
            monkeypatch,
            LauncherState(task=A_PREMISE, cwd=str(directory), policy=Policy.AUTONOMOUS),
        )
        assert not any("writes only inside" in line for line in lines), name
        assert not any("fan-out reaches no further" in line for line in lines), name
        assert not any(_AS_ITS_OWN in line for line in lines), name


def test_the_nearest_git_directory_passed_over_is_the_one_named(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "a" / "inner"
    (inner / ".git").mkdir(parents=True)
    (outer / ".git").mkdir()

    assert launcher.git_layout(str(inner)) == launcher.UnreadGit(str(inner.resolve() / ".git"))
    assert launcher.git_layout(str(outer / "a")) == launcher.UnreadGit(
        str(outer.resolve() / ".git")
    )


def test_a_git_entry_that_cannot_be_read_is_not_called_a_git_directory_outside(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A ``.git`` file that is no pointer, and an empty ``.git`` directory with nothing
    above it. Either is inside the launch directory and neither is a git directory,
    so the line may not say a governing git directory lies outside it and may not
    have the gate refusing "that git directory".
    """
    garbled = tmp_path / "garbled"
    garbled.mkdir()
    (garbled / ".git").write_text("not a pointer\n")
    empty = tmp_path / "empty"
    (empty / ".git").mkdir(parents=True)

    for directory in (garbled, empty):
        entry = str(directory.resolve() / ".git")
        assert launcher.git_layout(str(directory)) == launcher.UnreadGit(entry)
        fake, _ = _run_draw(
            monkeypatch,
            LauncherState(task=A_PREMISE, cwd=str(directory), policy=Policy.AUTONOMOUS),
        )
        lines = _behind(fake)

        assert not any("writes only inside" in line for line in lines)
        assert not any("lies outside it" in line for line in lines)
        assert not any("git directory included" in line for line in lines)
        contained = next(line for line in lines if line.startswith("contained:"))
        assert "is not measured for this layout" in contained
        unmeasured = next(line for line in lines if line.startswith("not measured:"))
        assert unmeasured.startswith(
            f"not measured: {entry} could not be read as a git directory or as a pointer to one"
        )
        assert f"The nearest layout measured is {_MEASURED_AT} --" in unmeasured
        assert "is not known" in unmeasured
        assert unmeasured not in _lines(fake)
        gate = next(line for line in lines if line.startswith("file writes are held to"))
        assert gate.endswith("a target that cannot be shown to be inside it is refused.")


# ---------------------------------------------------------------------------
# A failure reloaded behind a press is not read as the answer to that press
# ---------------------------------------------------------------------------

_EARLIER = "an earlier launch did not start a session: reason"
_THAT = "that launch did not start a session: reason"


def _failed_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> LauncherState:
    state = LauncherState(task="carry on", cwd=str(tmp_path))
    _, draft = _press_launch(monkeypatch, state)
    state.report_failure(draft, "reason")
    return state


def test_a_retry_that_started_does_not_look_like_a_retry_that_failed_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Two histories that end with the same draft in the fields and the same failure
    on screen. In one the retry was pressed unedited and failed again for the same
    reason. In the other the retry was edited, pressed, and nothing has been
    reported about it -- it may be running. The modal reopens over both.
    """
    failed_again = _failed_once(monkeypatch, tmp_path)
    _, retry = _press_launch(monkeypatch, failed_again)
    failed_again.report_failure(retry, "reason")

    started = _failed_once(monkeypatch, tmp_path)
    started.task = "carry on, differently"
    _press_launch(monkeypatch, started)

    assert failed_again.draft() == started.draft()
    assert failed_again.failure_loaded and started.failure_loaded
    assert failed_again._open_requested and started._open_requested

    again, again_explained = _explained(monkeypatch, failed_again)
    past, past_explained = _explained(monkeypatch, started)
    assert again != past

    assert _THAT in again and _EARLIER not in again
    assert launcher._PRESSED_PAST not in again_explained
    assert _EARLIER in past and _THAT not in past
    assert launcher._PRESSED_PAST in past_explained
    assert "not about that one" in launcher._PRESSED_PAST
    # No text lost on either: what is said about the draft is said on both.
    kept = "the draft below is that launch, as it was typed -- nothing was lost"
    assert kept in again_explained and kept in past_explained


def test_every_failure_still_queued_at_a_press_is_an_earlier_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The retry of the first failure brings the second up in a reopened modal, and the
    press just made was not the second's. A failure reported after a press is that
    press's or a later one's, and is not marked.
    """
    state = LauncherState(task="task one", cwd=str(tmp_path))
    _, first = _press_launch(monkeypatch, state)
    state.task = "task two"
    _, second = _press_launch(monkeypatch, state)
    state.report_failure(first, "reason")
    state.report_failure(second, "reason two")
    assert [f.pressed_past for f in state.failures] == [False, False]
    lines, _ = _drawn(monkeypatch, state)
    assert "that launch did not start a session (1 more behind it): reason" in lines

    _, retry = _press_launch(monkeypatch, state)
    assert retry.task == "task one"
    assert [(f.reason, f.pressed_past) for f in state.failures] == [("reason two", True)]
    lines, behind = _explained(monkeypatch, state)
    assert "an earlier launch did not start a session: reason two" in lines
    assert launcher._PRESSED_PAST in behind

    state.report_failure(retry, "reason three")
    assert [(f.reason, f.pressed_past) for f in state.failures] == [
        ("reason two", True),
        ("reason three", False),
    ]


def test_a_draftless_failure_pressed_past_is_an_earlier_one_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = LauncherState(task="typed here", cwd=str(tmp_path))
    state.report_failure(None, "reason", "relaunch")
    lines, _ = _drawn(monkeypatch, state)
    assert "that relaunch did not start a session: reason" in lines

    _press_launch(monkeypatch, state)

    lines, behind = _explained(monkeypatch, state)
    assert "an earlier relaunch did not start a session: reason" in lines
    assert launcher._PRESSED_PAST in behind
    assert "it was not made from this window, so nothing typed below is part of it" in behind


def test_a_launch_decided_at_once_is_still_not_reported_from_inside_the_press() -> None:
    """
    ``draw`` clears the fields after its ``launch`` callback returns, and
    ``launched`` drops a failure whose draft is in the fields as the one being
    retried. A failure reported from inside the callback would be dropped by its own
    press, text and all. The callback the application passes is ``_launch``, and the
    launch it can refuse without waiting for anything is the one with no pool: that
    one, too, is left for ``_reap_launches`` to report on a frame of its own.
    """
    from pptmstr.app import AppState, _launch, _reap_launches
    from pptmstr.bridge import Bridge
    from pptmstr.settings import Settings
    from pptmstr.store import Store

    state = AppState(store=Store(), bridge=Bridge(), settings=Settings())
    assert state.pool is None
    state.launcher.task = "carry on"
    draft = state.launcher.draft()

    _launch(state, LaunchSpec(task="carry on", model=DEFAULT_MODEL, cwd="/tmp"), draft)

    assert state.launcher.failures == []
    assert len(state.launches) == 1 and state.launches[0].future.done()

    # What ``draw`` does next, and then the frame after it.
    state.launcher.launched()
    assert state.launcher.task == ""
    _reap_launches(state)
    assert _failed_tasks(state.launcher.failures) == ["carry on"]
    assert state.launcher.task == "carry on"


# ---------------------------------------------------------------------------
# One id listed under two directories: the click picks the row clicked
# ---------------------------------------------------------------------------


def _listed_twice(tmp_path: Path) -> tuple[SessionRow, SessionRow]:
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    return _row("a", cwd=str(one.resolve())), _row("a", cwd=str(two.resolve()))


def _click_row(
    monkeypatch: pytest.MonkeyPatch, picker: SessionPicker, clicked: int
) -> tuple[list[str], list[bool], list[bool]]:
    """
    Draw every row of ``picker`` with the selectable at index ``clicked`` answering
    as pressed. Returns the IDs pushed, which rows were drawn as selected, and what
    each row's draw answered.
    """
    fake = MagicMock()
    answers = iter(index == clicked for index in range(len(picker.rows)))
    fake.selectable.side_effect = lambda *a, **k: (next(answers), False)
    fake.small_button.side_effect = lambda *a, **k: False
    fake.get_content_region_avail.return_value.x = 400.0
    fake.calc_text_size.return_value.x = 100.0
    fake_widgets = MagicMock()
    fake_widgets.ellipsis.side_effect = lambda text, width: text
    fake_widgets.flat_line.side_effect = widgets.flat_line
    monkeypatch.setattr(launcher, "imgui", fake)
    monkeypatch.setattr(launcher, "widgets", fake_widgets)
    said = [launcher._session_row(picker, row, 0.0, frozenset()) for row in picker.rows]
    return (
        [call.args[0] for call in fake.push_id.call_args_list],
        [call.args[1] for call in fake.selectable.call_args_list],
        said,
    )


def test_a_rows_draw_reports_the_click_and_lights_the_pick(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The row's draw says it was clicked; the list makes the pick once every row of
    the frame has been drawn. The picked row is lit, and of two rows under one id
    only the one that was picked.
    """
    first, second = _listed_twice(tmp_path)
    picker = _loaded(first, second)

    ids, selected, said = _click_row(monkeypatch, picker, 1)

    assert said == [False, True]
    assert picker.picked is None, "the row reports; the list picks"
    assert selected == [False, False]
    # Two rows, two widgets: under one ID the second selectable is the first's.
    assert len(set(ids)) == 2

    picker.pick(second, ".")
    _, selected, said = _click_row(monkeypatch, picker, -1)
    assert selected == [False, True] and said == [False, False]


def test_a_click_on_the_second_row_of_one_id_picks_the_second_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A lookup by id answers with the first row carrying it. The click is on a row,
    the row says where the resume runs, and it is the row that is picked and
    resumed.
    """
    first, second = _listed_twice(tmp_path)
    assert first.session_id == second.session_id and first.cwd != second.cwd
    state = LauncherState(task="t", cwd=str(tmp_path))
    state.picker = _loaded(first, second)

    _run_draw(monkeypatch, state, expand=True, click_row=1)

    assert state.picker.picked == second
    _, launched = _run_draw(monkeypatch, state, click_launch=True)
    assert [(spec.resume, spec.cwd) for spec in launched] == [("a", second.cwd)]


def _typed_draft(tmp_path: Path) -> LauncherState:
    """A New Task draft with something in every field, and a listing of two rows."""
    state = LauncherState(
        task="  a new task\nhalf typed  ",
        cwd=str(tmp_path),
        brief=" /some/brief ",
        model=FALLBACK_MODELS[1],
        template_index=1,
        policy=Policy.PERMISSIVE,
        subagent_cap=2,
    )
    state.picker = _loaded(_row("a", cwd=str(tmp_path)), _row("b", cwd=str(tmp_path)))
    return state


def test_a_click_on_a_row_picks_it_and_new_task_stays_open_with_its_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The operator's correction, at the modal: a session picked in New Task is
    resumed from New Task. The click picks the row and does nothing else -- the
    modal is not closed, nothing is launched, and every field is as it stood.
    """
    state = _typed_draft(tmp_path)
    before = state.draft()
    clicked = state.picker.rows[1]

    fake, launched = _run_draw(monkeypatch, state, expand=True, click_row=1)

    assert state.picker.picked == clicked
    assert state.is_open and not state._open_requested
    fake.close_current_popup.assert_not_called()
    assert launched == []
    assert state.draft() == replace(before, picked=clicked)
    assert state.failures == [] and state.set_aside == []

    # A click on the pick is the way back out.
    again = _typed_draft(tmp_path)
    again.picker.pick(clicked, again.cwd)
    _run_draw(monkeypatch, again, expand=True, click_row=1)
    assert again.picker.picked is None and again.is_open


def test_the_launch_after_a_pick_resumes_that_session_with_what_was_typed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The press that follows: the session id of the row, the box as the message,
    through the same callback a new session goes through. Then the press's clears,
    the pick among them, so the next thing typed is not sent to that conversation.
    """
    state = _typed_draft(tmp_path)
    clicked = state.picker.rows[1]
    _run_draw(monkeypatch, state, expand=True, click_row=1)
    drafts: list[launcher.Draft] = []

    fake, launched = _run_draw(monkeypatch, state, click_launch=True, drafts=drafts)

    (spec,) = launched
    assert spec.resume == "b" and spec.message == "a new task\nhalf typed"
    assert spec.task == "summary for b" and spec.task_is_label
    assert spec.cwd == str(tmp_path.resolve())
    assert [draft.picked for draft in drafts] == [clicked]
    fake.close_current_popup.assert_called_once_with()
    assert state.picker.picked is None and state.task == "" and not state.is_open

    # Ctrl+Enter in the box is the same press.
    again = _typed_draft(tmp_path)
    again.picker.pick(again.picker.rows[0], again.cwd)
    _, launched = _run_draw(monkeypatch, again, submit=True)
    assert [(spec.resume, spec.message) for spec in launched] == [("a", "a new task\nhalf typed")]


def test_a_held_resume_is_not_started_by_the_button_or_by_ctrl_enter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _typed_draft(tmp_path)
    gone = _row("g", cwd=str(tmp_path / "gone"))
    state.picker = _loaded(gone)
    state.picker.pick(gone, state.cwd)

    for how in ({"click_launch": True}, {"submit": True}):
        fake, launched = _run_draw(monkeypatch, state, **how)  # type: ignore[arg-type]
        assert launched == [] and state.is_open and state.picker.picked == gone
        assert launcher.hold_notice(launcher._HOLD_GONE) in _notices(fake)
        assert launcher._HOLD_GONE in _behind(fake)
        fake.begin_disabled.assert_called()


def test_draw_has_no_way_to_hand_a_picked_row_to_anything_else() -> None:
    """
    The resume form opens from a closed card and from nowhere else. ``draw`` takes
    no callback for a pick, so there is nothing a caller could open one from.
    """
    import ast
    import inspect

    parameters = inspect.signature(launcher.draw).parameters
    assert "on_pick_resume" not in parameters
    callbacks = [
        name for name, parameter in parameters.items() if "Callable" in str(parameter.annotation)
    ]
    assert callbacks == ["launch"]
    # And the module cannot reach the form to open it: it imports nothing of it.
    imported = {
        alias.name
        for node in ast.walk(ast.parse(inspect.getsource(launcher)))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "widgets" in imported and "resume_form" not in imported


def test_a_row_cannot_be_picked_while_the_list_is_shut_or_without_a_click(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for expand, click_row in ((False, 0), (True, None), (True, 7)):
        state = _typed_draft(tmp_path)
        fake, launched = _run_draw(monkeypatch, state, expand=expand, click_row=click_row)
        assert state.picker.picked is None and launched == [] and state.is_open
        fake.close_current_popup.assert_not_called()


def test_the_way_back_to_a_new_session_is_under_the_pick_and_nowhere_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    With a row picked there is one more button, and it undoes the pick. Nothing on
    the modal says a click closes it: it does not.
    """
    state = _typed_draft(tmp_path)

    fake, _ = _run_draw(monkeypatch, state, expand=True)
    buttons = {str(call.args[0]) for call in fake.small_button.call_args_list}
    assert buttons == {"+", "rescan"}
    (header,) = [str(call.args[0]) for call in fake.collapsing_header.call_args_list[:1]]
    assert header == f"resume an existing session instead{launcher._RESUME_ID}"

    state = _typed_draft(tmp_path)
    state.picker.pick(state.picker.rows[0], state.cwd)
    fake, _ = _run_draw(monkeypatch, state, expand=True)
    buttons = {str(call.args[0]) for call in fake.small_button.call_args_list}
    assert buttons == {"+", "rescan", launcher._UNPICK}
    assert not any("closes New Task" in line or "resume form" in line for line in _lines(fake))
    assert state.brief == " /some/brief "

    # Under a shut header too, where it is the only way out on screen.
    fake, launched = _run_draw(monkeypatch, state, press=launcher._UNPICK)
    assert state.picker.picked is None and launched == [] and state.is_open


def test_in_dear_imgui_a_row_clicked_with_the_mouse_is_resumed_from_new_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The click as Dear ImGui delivers it: a selectable in a child of the modal's
    scrolling body. The popup stays up with what was typed in the box, the row is
    the pick, and the launch button then resumes that session with that text.
    """
    rows = (_row("a", cwd=str(tmp_path)), _row("b", cwd=str(tmp_path)))
    state = LauncherState(cwd=str(tmp_path))
    state.request_open()
    with _RealFrames(monkeypatch, state) as real:
        real.scanner = lambda cwd: (rows, {})
        real.open_list = True
        real.frame(3)
        real.type("carry on from here")
        for _ in range(200):
            if state.picker.rows:
                break
            time.sleep(0.01)
            real.frame()
        assert state.picker.rows == rows
        real.frame(2)
        assert real.modal_was_drawn()
        label = next(name for name in real.buttons if name.startswith("summary for b"))

        real.click(label)

        assert state.picker.picked == rows[1]
        assert state.is_open and real.modal_was_drawn()
        assert real.launched == []
        assert state.task == "carry on from here"

        # The lines under the pick make the body taller; the child, the window and
        # the move back onto the display each follow a frame behind the last.
        real.frame(3)
        real.click("launch")

        ((spec, draft),) = real.launched
        assert (spec.resume, spec.message) == ("b", "carry on from here")
        assert draft.picked == rows[1]
        assert not state.is_open and state.picker.picked is None


def test_everything_the_resume_form_takes_from_this_module_is_still_here() -> None:
    """
    The form asks every question about a directory, a rung and a CLI through this
    module, and New Task giving up its resume must not take one of them with it.
    Read off the form's source, so a name it starts using is checked without this
    list being told.
    """
    import ast
    import inspect

    from pptmstr.ui import resume_form

    tree = ast.parse(inspect.getsource(resume_form))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "launcher" and node.level == 1
        for alias in node.names
    }
    reached = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "launcher"
    }
    # The floor, so a parse that found nothing cannot pass for one that found all.
    assert {"VersionGate", "RecordedDirectory", "GoneDirectory"} <= imported
    assert {
        "launch_directory",
        "resolved_directory",
        "refusal_for",
        "live_notice",
        "resume_directory_line",
        "gate_notice",
        "hold_notice",
        "git_layout",
        "SessionPicker",
        "OWN_CHECKOUT",
    } <= reached
    missing = sorted(name for name in imported | reached if not hasattr(launcher, name))
    assert missing == []
    # What the form does with the picker, which is not an attribute of the module.
    picker = SessionPicker()
    picker.pick(_row("a", cwd=None), "")
    assert picker.recovered_brief is None and picker.selected == "a"


def test_a_rescanned_row_is_still_drawn_as_the_pick(tmp_path: Path) -> None:
    """
    A rescan restamps the row, so the picked one is no longer equal to anything in
    the listing. It is still the same session in the same directory.
    """
    first, second = _listed_twice(tmp_path)
    picker = _loaded(first, second)
    picker.pick(second, ".")

    assert picker.is_picked(replace(second, last_modified=99))
    assert not picker.is_picked(first)
    picker.pick(replace(second, last_modified=99), ".")
    assert picker.picked is None


# ---------------------------------------------------------------------------
# The own-checkout refusal is about where a path leads, and a path can be made to
# lead somewhere else without its spelling changing
# ---------------------------------------------------------------------------


def _relinked(path: Path, target: str) -> None:
    """Replace the directory or link at ``path`` with a link to ``target``."""
    if path.is_symlink():
        path.unlink()
    else:
        path.rmdir()
    path.symlink_to(target)


def _judged_outside(tmp_path: Path) -> tuple[LauncherState, Path]:
    """
    A top-rung draft on a real directory outside the checkout, with the refusal
    already asked and answered "allowed" -- the state a frame leaves behind.
    """
    typed = tmp_path / "x"
    typed.mkdir()
    state = LauncherState(task=A_PREMISE, cwd=str(typed), policy=Policy.AUTONOMOUS)
    assert state.dangerous_engaged
    return state, typed


def test_a_path_that_became_a_link_into_the_checkout_is_refused_on_the_next_open(
    tmp_path: Path,
) -> None:
    """
    The verdict is remembered against the field as typed. The field has not changed;
    the disk under it has, and the modal opening is when the screen reads it again.
    """
    state, typed = _judged_outside(tmp_path)
    _relinked(typed, INSIDE_THE_CHECKOUT)

    worker = state.begin_open(scanner=lambda cwd: ((), {}))
    assert worker is not None
    worker.join(timeout=5.0)

    assert state.containment_refusal() is not None
    assert not state.dangerous_engaged
    assert state.granted_policy is Policy.STRICT


def test_a_press_is_not_granted_the_mode_on_a_remembered_verdict(tmp_path: Path) -> None:
    """
    The hazard itself: the screen judged the directory allowed, the path now leads
    into pptmstr's own checkout, and the modal has not been reopened. The spec is
    what starts the session, so the spec is what must not carry the rung there --
    neither half of it.
    """
    state, typed = _judged_outside(tmp_path)
    _relinked(typed, INSIDE_THE_CHECKOUT)

    spec = state.spec()

    assert spec.cwd == INSIDE_THE_CHECKOUT
    assert (spec.policy, spec.containment) == (Policy.STRICT, None)


def test_a_press_that_finds_another_rung_than_the_screen_showed_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The frame drew "on" from the remembered answer and the press found a refusal.
    Launching the downgrade would be a session under a gate the screen had just
    described as off; so the press launches nothing, keeps the draft, and the next
    frame says what the press found.
    """
    state, typed = _judged_outside(tmp_path)
    _relinked(typed, INSIDE_THE_CHECKOUT)

    fake, launched = _run_draw(monkeypatch, state, click_launch=True)

    assert launched == []
    assert state.task == A_PREMISE and state.policy is Policy.AUTONOMOUS
    assert state.is_open
    assert launcher.LOUD_LINE in _lines(fake)
    assert not any(p.startswith("refused for") for p in _behind(fake))
    fake, launched = _run_draw(monkeypatch, state)
    assert launcher.LOUD_LINE not in _lines(fake)
    assert any(p.startswith("refused for") for p in _behind(fake))

    # And the press after that is the one the screen now describes.
    _, launched = _drawn(monkeypatch, state, click_launch=True)
    assert [(s.policy, s.containment) for s in launched] == [(Policy.STRICT, None)]


def test_a_press_the_other_way_is_not_granted_the_mode_behind_a_refusal_on_screen(
    tmp_path: Path,
) -> None:
    """
    The same disagreement in the direction that widens: the screen says refused, the
    path no longer leads into the checkout. A press there must not start an
    unattended session under a section that was describing the ordinary gate.
    """
    typed = tmp_path / "x"
    typed.symlink_to(INSIDE_THE_CHECKOUT)
    state = LauncherState(task=A_PREMISE, cwd=str(typed), policy=Policy.AUTONOMOUS)
    assert state.containment_refusal() is not None
    outside = tmp_path / "outside"
    outside.mkdir()
    _relinked(typed, str(outside))

    assert state.press() is None
    assert state.dangerous_engaged

    spec = state.press()
    assert spec is not None
    assert (spec.cwd, spec.policy) == (str(outside), Policy.AUTONOMOUS)
    assert spec.containment == sandbox.containment_settings()


def test_the_spec_takes_its_rung_and_its_containment_from_one_look_at_the_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    "Both or neither" is only true if both come from one answer. Here every look at
    the directory and every asking of the refusal is counted, and the refusal is
    made to change its mind each time it is asked -- so a spec assembled from two
    askings carries a rung from one and a containment from the other.
    """
    looks: list[str] = []
    real_look = launcher.launch_directory

    def look(row: SessionRow | None, field: str) -> launcher.LaunchDirectory:
        looks.append(field)
        return real_look(row, field)

    asked: list[str] = []

    def fickle(cwd: str, **kwargs: object) -> str | None:
        asked.append(cwd)
        return None if len(asked) % 2 else "refused"

    monkeypatch.setattr(launcher, "launch_directory", look)
    monkeypatch.setattr(launcher, "refusal_for", fickle)
    # A link, so the field as typed and the directory it names are two strings.
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "typed").symlink_to(real)
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path / "typed"), policy=Policy.AUTONOMOUS)

    for _ in range(4):
        looks.clear()
        before = len(asked)
        spec = state.spec()
        assert len(looks) == 1 and len(asked) == before + 1
        # Asked of the string the session is started with.
        assert asked[-1] == spec.cwd == str(real.resolve())
        assert (spec.policy is Policy.AUTONOMOUS) == (spec.containment is not None)


def test_a_launch_below_the_top_rung_does_not_ask_the_refusal_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The fresh question is the top rung's. An ordinary launch does not pay two
    resolves for an answer nothing reads.
    """

    def unasked(cwd: str, **kwargs: object) -> str | None:
        raise AssertionError("the own-checkout question was asked for a rung that has none")

    monkeypatch.setattr(launcher, "refusal_for", unasked)
    for policy in (Policy.STRICT, Policy.PERMISSIVE):
        spec = LauncherState(task="t", cwd=str(tmp_path), policy=policy).spec()
        assert (spec.policy, spec.containment) == (policy, None)


# ---------------------------------------------------------------------------
# A recorded directory reached through a link runs where the link leads, and the
# line under the picker says that directory and not the recorded spelling
# ---------------------------------------------------------------------------


def test_a_recorded_directory_replaced_by_a_link_is_not_shown_as_where_the_session_runs(
    tmp_path: Path,
) -> None:
    """
    ``isdir`` follows links, so the replaced path still reads as a directory. The
    resume goes where the link leads; the line has to name that place after "runs
    in", name the recorded spelling as the other one, and it is the directory a
    resume is started in.
    """
    _, recorded = _two_trees(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    row = _row("a", cwd=recorded)
    assert launcher.launch_directory(row, ".") == launcher.RecordedDirectory(recorded)
    _relinked(Path(recorded), str(elsewhere))
    leads_to = str(elsewhere.resolve())

    directory = launcher.launch_directory(row, ".")

    assert directory == launcher.RedirectedDirectory(leads_to, recorded)
    assert launcher.resolved_directory(directory) == leads_to
    said = launcher.resume_directory_line(directory, leads_to)
    assert said is not None
    assert said.startswith(f"runs in {leads_to} -- NOT in {recorded} ")
    assert "symbolic link" in said and "NOT the working directory above" not in said
    elsewhere_too = launcher.resume_directory_line(directory, str(tmp_path))
    assert elsewhere_too is not None and "NOT the working directory above" in elsewhere_too


def test_a_link_in_a_component_above_the_recorded_directory_is_seen_too(tmp_path: Path) -> None:
    """
    The last component is a real directory here and ``islink`` on the path says no.
    It is the parent that was replaced.
    """
    real = tmp_path / "real"
    (real / "work").mkdir(parents=True)
    (tmp_path / "via").symlink_to(real)
    recorded = str(tmp_path / "via" / "work")
    assert not os.path.islink(recorded)

    directory = launcher.launch_directory(_row("a", cwd=recorded), ".")

    assert directory == launcher.RedirectedDirectory(str((real / "work").resolve()), recorded)


def test_a_recorded_directory_spelled_with_a_trailing_separator_is_not_called_a_link(
    tmp_path: Path,
) -> None:
    """
    A different spelling of the same path, with no link anywhere along it. Calling
    that a link would put a warning on a resume nothing is wrong with.
    """
    recorded = str(tmp_path.resolve())
    for spelling in (recorded + os.sep, os.path.join(recorded, "sub", "..")):
        (tmp_path / "sub").mkdir(exist_ok=True)
        assert launcher.launch_directory(
            _row("a", cwd=spelling), "."
        ) == launcher.RecordedDirectory(recorded)


# ---------------------------------------------------------------------------
# Resuming a session this process is running ends its run, and the form is told so
# ---------------------------------------------------------------------------


def test_the_live_line_is_about_the_row_it_is_asked_of_and_says_what_ends() -> None:
    """
    The words it has to carry: that a run ends, that it can be mid-turn, and what
    becomes of its sub-agents -- which end with the run, and are not left running.
    "The conversation continues" alone reads as sending a message.
    """
    row = _row("a")
    assert launcher.live_resume_line(None, frozenset({"a"})) is None
    assert launcher.live_resume_line(row, frozenset()) is None
    assert launcher.live_resume_line(row, frozenset({"b"})) is None

    line = launcher.live_resume_line(row, frozenset({"a"}))
    assert line is not None
    assert "ends its run" in line and "mid-turn" in line
    assert "its sub-agents end with the run" in line and "are cancelled" in line
    assert "left as they are" not in line


def test_a_live_session_is_marked_on_its_row_before_it_is_clicked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Only the live row carries the tag, so it tells the rows apart rather than
    decorating the list.
    """
    fake = MagicMock()
    fake.small_button.side_effect = lambda *a, **k: False
    fake.selectable.side_effect = lambda label, selected, *a, **k: (False, selected)
    fake.get_content_region_avail.return_value.x = 400.0
    fake.calc_text_size.return_value.x = 100.0
    fake_widgets = MagicMock()
    fake_widgets.ellipsis.side_effect = lambda text, width: text
    fake_widgets.flat_line.side_effect = widgets.flat_line
    monkeypatch.setattr(launcher, "imgui", fake)
    monkeypatch.setattr(launcher, "widgets", fake_widgets)
    picker = _loaded(_row("a"), _row("b"))

    for row in picker.rows:
        launcher._session_row(picker, row, 0.0, frozenset({"b"}))

    labels = [str(call.args[0]) for call in fake.selectable.call_args_list]
    assert [launcher._LIVE_TAG in label for label in labels] == [False, True]


def test_a_session_labelled_by_a_prompt_typed_on_several_lines_is_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A session with no summary is labelled by its first prompt, which is whatever
    was typed. The list is rows of one line; the rule that makes it one is the
    shared one, so the picker and the rail cannot flatten a task differently.
    """
    fake = MagicMock()
    fake.small_button.side_effect = lambda *a, **k: False
    fake.selectable.side_effect = lambda label, selected, *a, **k: (False, selected)
    fake.get_content_region_avail.return_value.x = 400.0
    fake.calc_text_size.return_value.x = 100.0
    fake_widgets = MagicMock()
    fake_widgets.ellipsis.side_effect = lambda text, width: text
    fake_widgets.flat_line.side_effect = widgets.flat_line
    monkeypatch.setattr(launcher, "imgui", fake)
    monkeypatch.setattr(launcher, "widgets", fake_widgets)
    typed = replace(_row("a"), summary=None, first_prompt="where are we?\n\nBTW  also this")
    picker = _loaded(typed)

    launcher._session_row(picker, picker.rows[0], 0.0, frozenset())

    (label,) = [str(call.args[0]) for call in fake.selectable.call_args_list]
    assert label.startswith("where are we? BTW also this")
    assert "\n" not in label


# ---------------------------------------------------------------------------
# What the modal looked like once it was drawn
# ---------------------------------------------------------------------------


def test_a_window_hanging_off_the_area_is_moved_back_inside_it() -> None:
    """
    The measured case first: a modal centred at y=151 that grew to 870 tall on a
    900-tall display, with its last 121 px and the launch button off the bottom.
    """
    area = ((0.0, 0.0), (1500.0, 900.0))
    assert launcher.modal_origin((440.0, 151.0), (620.0, 870.0), *area) == (440.0, 30.0)
    # Inside already: left where the operator put it.
    assert launcher.modal_origin((440.0, 19.0), (620.0, 870.0), *area) is None
    assert launcher.modal_origin((0.0, 0.0), (1500.0, 900.0), *area) is None
    # Off any edge, on either axis, and the area need not start at the origin.
    assert launcher.modal_origin((-50.0, 100.0), (620.0, 300.0), *area) == (0.0, 100.0)
    assert launcher.modal_origin((1200.0, 100.0), (620.0, 300.0), *area) == (880.0, 100.0)
    assert launcher.modal_origin((440.0, 5.0), (620.0, 300.0), (0.0, 22.0), (1500.0, 856.0)) == (
        440.0,
        22.0,
    )
    # Taller than the area: the top is the end that is kept.
    assert launcher.modal_origin((440.0, 151.0), (620.0, 950.0), *area) == (440.0, 0.0)


def _modal_rect(real: _RealFrames) -> tuple[float, float, float, float, float]:
    """
    The modal as Dear ImGui holds it: left, top, width, height, and how far it scrolls.
    """
    window = real.imgui.internal.find_window_by_name(launcher.TITLE)
    assert window is not None
    return window.pos.x, window.pos.y, window.size.x, window.size.y, window.scroll_max.y


def _settled(state: LauncherState) -> None:
    worker = state.version_gate.request(prober=_meets)
    if worker is not None:
        worker.join(timeout=5.0)
    state.version_gate.poll()


def test_the_modal_stays_on_the_display_when_it_grows_after_opening(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The order an operator meets it in: the modal opens with the session list shut,
    centred at that height, and the list is opened afterwards. What the list adds
    is taller than the room left under a centred window, so the window is moved,
    and its buttons with it.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path))
    _settled(state)
    state.request_open()
    with _RealFrames(monkeypatch, state) as real:
        real.io.display_size = real.imgui.ImVec2(1280.0, 720.0)
        real.frame(4)
        _, top_before, _, height_before, _ = _modal_rect(real)

        real.open_list = True
        real.frame(6)
        left, top, width, height, _ = _modal_rect(real)

        assert height > height_before + 100.0, "opening the list no longer grows the modal"
        # The fixture has to be the hazard: grown from where it stood, it overhangs.
        assert top_before + height > 720.0
        assert top >= 0.0 and top + height <= 720.0
        assert left >= 0.0 and left + width <= 1280.0
        for label in ("launch", "cancel"):
            assert top <= real.placed[label][0] and real.placed[label][1] <= top + height


@pytest.mark.parametrize("policy", list(Policy))
@pytest.mark.parametrize("picked", [False, True], ids=["new", "resume"])
@pytest.mark.parametrize("height", [700.0, 480.0])
def test_the_buttons_are_under_a_scrolling_body_and_on_the_display_on_every_rung(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, policy: Policy, picked: bool, height: float
) -> None:
    """
    What does not fit scrolls, and the launch and cancel buttons are not part of
    what scrolls: they are items of the modal itself, under the child the fields
    are in, so they are on the display at every rung, with the session list open
    and a session picked, on a work area 700 pixels tall and on one much shorter.
    """
    row = _row("a", cwd=str(tmp_path))
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path), policy=policy)
    if picked:
        state.picker.pick(row, state.cwd)
    _settled(state)
    state.request_open()
    with _RealFrames(monkeypatch, state) as real:
        real.scanner = lambda cwd: ((row,), {})
        real.open_list = True
        real.io.display_size = real.imgui.ImVec2(1280.0, height)
        real.frame(6)
        _, top, _, tall, modal_scroll = _modal_rect(real)
        body = real.body()

        assert top >= 0.0 and top + tall <= height
        assert modal_scroll == 0.0, "the window itself scrolls, and its buttons with it"
        for label in ("launch", "cancel"):
            low, high, window = real.placed[label]
            assert window == launcher.TITLE, f"{label} is drawn inside {window}"
            assert body.pos.y + body.size.y <= low, f"{label} overlaps the body"  # type: ignore[attr-defined]
            assert top <= low and high <= top + tall
            assert 0.0 <= low and high <= height
        if policy is Policy.AUTONOMOUS or height < 700.0:
            # The fixture has to be the hazard: there is more than fits.
            assert body.scroll_max.y > 0.0  # type: ignore[attr-defined]

        # Scrolled to its end, the body moves and the buttons do not.
        before = dict(real.placed)
        real.imgui.internal.set_scroll_y(body, body.scroll_max.y)  # type: ignore[attr-defined]
        real.frame(3)
        assert {k: v[:2] for k, v in real.placed.items()} == {k: v[:2] for k, v in before.items()}


def test_the_body_is_given_what_the_display_leaves_and_never_nothing() -> None:
    assert launcher.body_room(900.0, 30.0, 12.0, 58.0) == 800.0
    assert launcher.body_room(700.0, 30.0, 12.0, 58.0) == 600.0
    # A taller footer -- a hold reason wrapped under the buttons -- comes off the body.
    assert launcher.body_room(700.0, 30.0, 12.0, 98.0) == 560.0
    # A display too short for both still leaves the task box in reach.
    assert launcher.body_room(200.0, 30.0, 12.0, 58.0) == launcher._BODY_FLOOR
    assert launcher._BODY_FLOOR > launcher._TASK_HEIGHT


def test_no_line_of_text_widens_the_modal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    A hold reason beside the buttons and the listing's heading are each one long
    line. Unwrapped, either sets the width of the whole modal.
    """
    deep = tmp_path / ("a-directory-with-a-long-name-" * 3) / ("and-another-under-it-" * 3)
    deep.mkdir(parents=True)
    state = LauncherState(task="one line", cwd=str(deep), policy=Policy.AUTONOMOUS)
    _settled(state)
    assert state.launch_hold() == launcher._HOLD_THIN
    state.request_open()
    with _RealFrames(monkeypatch, state) as real:
        real.frame(4)
        modal = real.imgui.internal.find_window_by_name(launcher.TITLE)
        # The header is an item of the body, so its open flag is the body's to keep.
        body = real.body()
        body.state_storage.set_int(body.get_id(launcher._RESUME_ID), 1)  # type: ignore[attr-defined]
        real.frame(4)
        assert state.picker.expanded
        _, _, width, _, _ = _modal_rect(real)
        assert width == launcher._WIDTH
        # Held to the width by wrapping and not by being cut off at it, in the body
        # and in the footer the hold reason is drawn in.
        assert modal.content_size.x <= width
        assert body.content_size.x <= body.size.x <= width  # type: ignore[attr-defined]


def test_a_reopened_modal_is_centred_and_not_put_where_the_last_one_stood(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Dear ImGui keeps the window of a closed popup under its name. Moved back inside
    a display that has narrowed since, it would open against the edge.
    """
    state = LauncherState(task=A_PREMISE, cwd=str(tmp_path))
    state.request_open()
    with _RealFrames(monkeypatch, state) as real:
        real.frame(4)
        assert _modal_rect(real)[0] == (1280.0 - launcher._WIDTH) / 2.0
        real.io.add_key_event(real.imgui.Key.escape, True)
        real.frame()
        real.io.add_key_event(real.imgui.Key.escape, False)
        real.frame(2)
        assert not state.is_open

        real.io.display_size = real.imgui.ImVec2(800.0, 900.0)
        state.request_open()
        real.frame(4)
        assert state.is_open
        assert _modal_rect(real)[0] == (800.0 - launcher._WIDTH) / 2.0


def test_every_rung_still_parks_something_and_says_what() -> None:
    """
    The top rung parks none of the probed kinds, and the line read "still parks"
    with nothing after it. What it does park is the tool no build knows, which the
    classifier holds at every rung.
    """
    for rung in Policy:
        parks = launcher.dial_text(rung, rung).parks
        assert parks.startswith("still parks ") and parks != "still parks ", rung
        assert "never heard of" in parks, rung
    top = launcher.dial_text(Policy.AUTONOMOUS, Policy.AUTONOMOUS)
    assert top.parks == "still parks only a tool this build has never heard of"
    assert "only" not in launcher.dial_text(Policy.STRICT, Policy.STRICT).parks


def test_the_lines_under_the_dial_are_about_the_rung_the_launch_gets() -> None:
    asked = launcher.dial_text(Policy.AUTONOMOUS, Policy.AUTONOMOUS)
    assert asked.downgrade is None and asked.adds.startswith("adds ")

    refused = launcher.dial_text(Policy.AUTONOMOUS, Policy.STRICT)
    assert refused.downgrade is not None
    assert "autonomous is refused" in refused.downgrade and "strict" in refused.downgrade
    strict = launcher.dial_text(Policy.STRICT, Policy.STRICT)
    assert (refused.adds, refused.parks) == (strict.adds, strict.parks)
    assert refused.adds == "nothing runs unattended"


def test_a_refused_directory_is_not_described_as_granted_under_the_dial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The screen as drawn: the dial reads autonomous, the refusal below says the
    launch uses the ordinary gate, and the lines between them have to be on the
    refusal's side.
    """
    state = LauncherState(task=A_PREMISE, cwd=INSIDE_THE_CHECKOUT, policy=Policy.AUTONOMOUS)
    fake, launched = _run_draw(monkeypatch, state, click_launch=True)
    lines, behind = _lines(fake), _behind(fake)

    assert "autonomous is refused for this directory — this launch runs strict" in lines
    assert launcher.LOUD_LINE not in lines
    assert not any(line.startswith("adds ") for line in [*lines, *behind])
    assert any("refused" in p and "ordinary gate" in p for p in behind)
    assert "nothing runs unattended" in behind
    assert any(p.startswith("still parks ") and "writes" in p for p in behind)
    # And the launch is the one those lines describe.
    assert [spec.policy for spec in launched] == [Policy.STRICT]


def test_no_boundary_is_stated_for_a_directory_that_is_gone(tmp_path: Path) -> None:
    """
    A missing path reads as the layout the narrow measurement was taken for, so a
    section drawn from it would say ``Bash`` writes only inside a directory the
    line above it said was not there. The resume form draws this in its place.
    """
    gone = str(tmp_path / "removed-last-week")
    directory = launcher.launch_directory(_row("a", cwd=gone), ".")
    assert directory == launcher.GoneDirectory(gone)

    unstated = launcher.boundary_unstated(directory)

    assert unstated is not None and gone in unstated
    assert "no boundary is stated" in unstated and "nothing launches" in unstated


def test_a_boundary_is_stated_for_every_directory_that_is_there() -> None:
    for directory in (
        launcher.FieldDirectory("/srv/repo"),
        launcher.RecordedDirectory("/srv/repo"),
        launcher.RedirectedDirectory("/srv/elsewhere", "/srv/repo"),
        launcher.UnrecordedDirectory("/srv/repo"),
    ):
        assert launcher.boundary_unstated(directory) is None, directory


def test_a_worktree_whose_root_holds_its_main_checkout_is_not_the_measured_one() -> None:
    """
    ``git worktree add ../..`` from a nested repository: the worktree's root is an
    ancestor of the main checkout. It is not under that checkout, and it is not
    beside it -- the checkout and the shared git directory are both inside the
    session's own directory, which is nothing D2 ran.
    """
    around = launcher.LinkedWorktree(shared="/a/b/main/.git", root="/a", at_root=True)
    assert around.kind is launcher.WorktreeKind.AROUND
    # A worktree at the filesystem root holds every checkout there is.
    assert (
        launcher.LinkedWorktree(shared="/main/.git", root="/", at_root=True).kind
        is launcher.WorktreeKind.AROUND
    )
    # A name that merely starts the same is still beside.
    assert (
        launcher.LinkedWorktree(shared="/a-main/.git", root="/a", at_root=True).kind
        is launcher.WorktreeKind.BESIDE
    )

    for at_root in (True, False):
        layout = launcher.LinkedWorktree(shared="/a/b/main/.git", root="/a", at_root=at_root)
        text = launcher.contained_text(launcher.FieldDirectory("/a"), layout)
        assert text.wider is not None
        assert text.wider.startswith("and outside it, not measured:")
        assert "Bash also writes" not in text.wider
        assert "/a/b/main" in text.wider and _MEASURED_AT in text.wider


# ---------------------------------------------------------------------------
# A pick continues the session as what it was
# ---------------------------------------------------------------------------


def _record(
    model: str = "claude-opus-5", template: str | None = "feature", cap: int | None = 2
) -> tuple[launcher.Recorder, list[str]]:
    asked: list[str] = []

    def recorder(session_id: str) -> launcher.Recorded | None:
        asked.append(session_id)
        return launcher.Recorded(model, template, cap)

    return recorder, asked


def test_a_pick_sets_model_team_and_cap_from_the_sessions_record(tmp_path: Path) -> None:
    """
    New Task's combos start at the default model and solo, so a resume built from
    them as they stood restarted a team as one agent. The click is where the
    session's own answer is put in them; they are the operator's after it.
    """
    state = LauncherState(task="t", cwd=str(tmp_path))
    state.picker = _loaded(_row("a", cwd=str(tmp_path)))
    recorder, asked = _record()

    state.pick_session(state.picker.rows[0], recorder=recorder)

    assert asked == ["a"]
    assert state.model == "claude-opus-5"
    assert templates.names()[state.template_index] == "feature"
    assert state.subagent_cap == 2
    outcome = state.resumed()
    assert isinstance(outcome, LaunchSpec)
    assert (outcome.model, outcome.template, outcome.subagent_cap) == (
        "claude-opus-5",
        "feature",
        2,
    )

    # Changed afterwards, they stay changed; a click that clears the pick reads
    # nothing and puts nothing back.
    state.template_index = 0
    state.pick_session(state.picker.rows[0], recorder=recorder)
    assert state.picker.picked is None and asked == ["a"]
    assert state.template_index == 0 and state.model == "claude-opus-5"


@pytest.mark.parametrize(
    "recorded",
    [None, launcher.Recorded("", None, None), launcher.Recorded("", "no-such-team", None)],
    ids=["no record", "a record that says nothing", "a team this build does not have"],
)
def test_what_the_record_does_not_say_is_left_as_it_was(
    tmp_path: Path, recorded: launcher.Recorded | None
) -> None:
    state = LauncherState(task="t", cwd=str(tmp_path), model="claude-sonnet-5", template_index=2)
    state.subagent_cap = 5
    state.picker = _loaded(_row("a", cwd=str(tmp_path)))

    state.pick_session(state.picker.rows[0], recorder=lambda session_id: recorded)

    assert state.picker.picked is not None
    assert (state.model, state.template_index, state.subagent_cap) == ("claude-sonnet-5", 2, 5)


def test_a_click_on_a_row_in_the_drawn_list_fills_the_combos_from_its_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The wiring: the list's click is what asks the recorder ``draw`` was handed."""
    state = LauncherState(task="t", cwd=str(tmp_path))
    state.picker = _loaded(_row("a", cwd=str(tmp_path)))
    recorder, asked = _record(template="research", cap=None)

    _run_draw(monkeypatch, state, expand=True, click_row=0, recorder=recorder)

    assert state.picker.selected == "a" and asked == ["a"]
    assert templates.names()[state.template_index] == "research"
    assert state.subagent_cap is None
