"""
The session index: overlay persistence, and the join with the SDK's listing.

The real ``list_sessions`` is never called here. It reads the operator's actual
transcript tree, so a test that used it would assert against whatever work happened
to have been done that week.

The two things worth being careful about are both about the join being one-directional:
an overlay entry is decoration for a session the listing already reported, never
evidence that a session exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claude_agent_sdk import SDKSessionInfo

from pptmstr import sessions as sessions_mod
from pptmstr.sessions import (
    SessionMark,
    bookmarked,
    enumerate_sessions,
    load_overlay,
    merge_overlay,
    same_cwd,
    save_overlay,
    set_bookmark,
    set_title,
)


def info(
    session_id: str,
    *,
    last_modified: int = 1_000,
    summary: str = "a summary",
    cwd: str | None = "/home/op/proj",
    git_branch: str | None = "main",
    first_prompt: str | None = "the first prompt",
    tag: str | None = None,
    created_at: int | None = 500,
) -> SDKSessionInfo:
    return SDKSessionInfo(
        session_id=session_id,
        summary=summary,
        last_modified=last_modified,
        file_size=None,
        custom_title=None,
        first_prompt=first_prompt,
        git_branch=git_branch,
        cwd=cwd,
        tag=tag,
        created_at=created_at,
    )


class RecordingLister:
    """
    A stand-in for ``list_sessions`` that records the directory it was asked for.

    Passed where the module expects a ``Lister``, so mypy rejects this file if the
    fake's call shape and the SDK's ever drift apart.
    """

    def __init__(self, *infos: SDKSessionInfo) -> None:
        self.infos = list(infos)
        self.calls: list[str | None] = []

    def __call__(self, directory: str | None = None) -> list[SDKSessionInfo]:
        self.calls.append(directory)
        return list(self.infos)


# ---------------------------------------------------------------------------
# Overlay persistence
# ---------------------------------------------------------------------------


def test_overlay_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    overlay = set_title(set_bookmark({}, "abc", True), "abc", "the good one")
    save_overlay(overlay, path)
    assert load_overlay(path) == {"abc": SessionMark(bookmarked=True, title="the good one")}


def test_a_bookmark_survives_a_save_and_load_cycle(tmp_path: Path) -> None:
    """
    The feature's own promise: the operator marks a session, pptmstr dies, and the
    mark is still there next launch.
    """
    path = tmp_path / "sessions.json"
    save_overlay(set_bookmark({}, "abc", True), path)
    reloaded = load_overlay(path)
    assert reloaded["abc"].bookmarked is True


def test_missing_overlay_is_empty(tmp_path: Path) -> None:
    assert load_overlay(tmp_path / "nope.json") == {}


def test_corrupt_overlay_does_not_stop_the_list(tmp_path: Path) -> None:
    """
    Losing a bookmark is a nuisance; refusing to show the sessions is the defect the
    whole feature exists to fix.
    """
    path = tmp_path / "sessions.json"
    path.write_text("{not json at all")
    assert load_overlay(path) == {}
    rows = merge_overlay([info("abc")], load_overlay(path))
    assert [row.session_id for row in rows] == ["abc"]


def test_non_object_overlay_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text("[1, 2, 3]")
    assert load_overlay(path) == {}


def test_one_malformed_entry_does_not_take_the_others_with_it(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(
        json.dumps(
            {
                "sessions": {
                    "good": {"bookmarked": True, "title": None},
                    "wrong-type": {"bookmarked": 1, "title": None},
                    "bad-title": {"bookmarked": True, "title": ["a", "list"]},
                    "not-a-dict": "bookmarked!",
                }
            }
        )
    )
    assert load_overlay(path) == {"good": SessionMark(bookmarked=True)}


def test_a_numeric_bookmark_is_refused_rather_than_coerced(tmp_path: Path) -> None:
    """
    ``bool`` is an ``int``, so a JSON ``1`` would construct happily and then sort as
    truthy in a key that is meant to be a flag.
    """
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"sessions": {"abc": {"bookmarked": 1}}}))
    assert load_overlay(path) == {}


def test_an_entry_that_says_nothing_is_dropped_on_load(tmp_path: Path) -> None:
    """
    ``save_overlay`` never writes one, so this is the hand-edited and older-version
    case. Keeping it would make a round trip stop being idempotent and leave a record
    claiming the operator marked a session they did not.
    """
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"sessions": {"abc": {"bookmarked": False, "title": None}}}))
    assert load_overlay(path) == {}


def test_an_entry_that_says_nothing_is_not_written(tmp_path: Path) -> None:
    """
    A caller can hand us a ``SessionMark()`` directly -- the file must not grow a
    record per session the operator merely glanced at.
    """
    path = tmp_path / "sessions.json"
    save_overlay({"abc": SessionMark(), "def": SessionMark(bookmarked=True)}, path)
    assert json.loads(path.read_text())["sessions"] == {"def": {"bookmarked": True, "title": None}}


def test_unknown_top_level_keys_are_tolerated(tmp_path: Path) -> None:
    """A file written by a later version must still load here."""
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"version": 9, "sessions": {"abc": {"bookmarked": True}}}))
    assert load_overlay(path)["abc"].bookmarked is True


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    save_overlay(set_bookmark({}, "abc", True), path)
    save_overlay(set_bookmark({}, "def", True), path)
    assert list(load_overlay(path)) == ["def"]
    assert [p.name for p in tmp_path.iterdir()] == ["sessions.json"]


def test_save_creates_the_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "sessions.json"
    save_overlay(set_bookmark({}, "abc", True), path)
    assert load_overlay(path)["abc"].bookmarked is True


def test_unwritable_target_does_not_raise(tmp_path: Path) -> None:
    """Called when the operator clicks a bookmark; must not take down a frame."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    save_overlay(set_bookmark({}, "abc", True), blocked / "sessions.json")


def test_the_overlay_lives_beside_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    One config convention, not two. If this drifts, the operator has bookmarks in a
    directory their settings are not in.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
    assert sessions_mod.overlay_path() == Path("/custom/xdg/pptmstr/sessions.json")


# ---------------------------------------------------------------------------
# Overlay edits
# ---------------------------------------------------------------------------


def test_clearing_a_bookmark_removes_the_entry_entirely(tmp_path: Path) -> None:
    """
    An entry that says nothing must not be kept, or the file grows by one record per
    session the operator ever glanced at.
    """
    overlay = set_bookmark({}, "abc", True)
    assert set_bookmark(overlay, "abc", False) == {}


def test_clearing_a_bookmark_keeps_a_title() -> None:
    overlay = set_title(set_bookmark({}, "abc", True), "abc", "kept")
    assert set_bookmark(overlay, "abc", False) == {"abc": SessionMark(title="kept")}


def test_setting_a_title_on_an_unknown_session_creates_the_entry() -> None:
    assert set_title({}, "abc", "named") == {"abc": SessionMark(title="named")}


def test_an_empty_title_clears_rather_than_stores() -> None:
    """
    A title that renders as nothing is not a title. Storing one leaves a row whose
    label falls through to the summary while the overlay claims a title is set.
    """
    overlay = set_title({}, "abc", "named")
    assert set_title(overlay, "abc", "") == {}
    assert set_title(overlay, "abc", None) == {}


def test_edits_do_not_mutate_the_overlay_they_are_given() -> None:
    original = set_bookmark({}, "abc", True)
    set_bookmark(original, "def", True)
    set_title(original, "abc", "renamed")
    assert original == {"abc": SessionMark(bookmarked=True)}


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------


def test_a_session_with_no_overlay_entry_still_appears() -> None:
    """The common case: every session predating the feature has no entry."""
    (row,) = merge_overlay([info("abc")], {})
    assert row.session_id == "abc"
    assert row.bookmarked is False
    assert row.title is None


def test_an_overlay_entry_for_a_vanished_session_produces_no_row() -> None:
    """
    Do not crash, and do not resurrect it. The listing is the source of truth for
    what exists; the overlay only decorates.
    """
    overlay = set_title(set_bookmark({}, "deleted", True), "deleted", "gone")
    rows = merge_overlay([info("abc")], overlay)
    assert [row.session_id for row in rows] == ["abc"]


def test_a_vanished_session_is_not_pruned_from_the_overlay() -> None:
    """
    A listing narrowed to one project omits every session in every other one, so
    "absent from this listing" is not "deleted" and must not destroy a bookmark.
    """
    overlay = set_bookmark({}, "elsewhere", True)
    merge_overlay([info("abc")], overlay)
    assert overlay == {"elsewhere": SessionMark(bookmarked=True)}


def test_the_overlay_supplies_bookmark_and_title() -> None:
    overlay = set_title(set_bookmark({}, "abc", True), "abc", "the good one")
    (row,) = merge_overlay([info("abc")], overlay)
    assert row.bookmarked is True
    assert row.title == "the good one"


def test_rows_are_most_recent_first() -> None:
    rows = merge_overlay(
        [
            info("old", last_modified=10),
            info("new", last_modified=30),
            info("mid", last_modified=20),
        ]
    )
    assert [row.session_id for row in rows] == ["new", "mid", "old"]


def test_the_order_is_not_inherited_from_the_lister() -> None:
    """
    Most-recent-first is this module's guarantee. A cached or hand-built listing need
    not already have it, so the sort happens here rather than being trusted.
    """
    rows = merge_overlay([info("a", last_modified=1), info("b", last_modified=2)])
    assert [row.session_id for row in rows] == ["b", "a"]


def test_bookmarked_first_reorders_without_losing_recency_within_the_groups() -> None:
    overlay = set_bookmark({}, "old-and-marked", True)
    rows = merge_overlay(
        [
            info("newest", last_modified=30),
            info("old-and-marked", last_modified=10),
            info("middle", last_modified=20),
        ],
        overlay,
        bookmarked_first=True,
    )
    assert [row.session_id for row in rows] == ["old-and-marked", "newest", "middle"]


def test_bookmarked_rows_are_available_separately() -> None:
    overlay = set_bookmark({}, "marked", True)
    rows = merge_overlay(
        [info("marked", last_modified=10), info("plain", last_modified=20)], overlay
    )
    assert [row.session_id for row in bookmarked(rows)] == ["marked"]


def test_the_tag_is_passed_through_and_nothing_is_filtered_on_it() -> None:
    """
    Attribution is imperfect: there is no producer field, and an untagged session may
    well be the operator's own lost work. Hiding it is how they fail to get it back.
    """
    rows = merge_overlay([info("ours", tag="pptmstr"), info("theirs", tag=None)])
    assert {row.session_id: row.tag for row in rows} == {"ours": "pptmstr", "theirs": None}


def test_the_row_carries_what_identifies_it_to_a_human() -> None:
    (row,) = merge_overlay([info("abc", summary="fixing the gate", git_branch="topic")])
    assert row.git_branch == "topic"
    assert row.summary == "fixing the gate"
    assert row.created_at == 500
    assert row.last_modified == 1000


# ---------------------------------------------------------------------------
# The label
# ---------------------------------------------------------------------------


def test_an_operator_title_outranks_the_summary() -> None:
    (row,) = merge_overlay([info("abc", summary="auto")], set_title({}, "abc", "mine"))
    assert row.label == "mine"


def test_the_summary_is_the_label_when_there_is_no_title() -> None:
    (row,) = merge_overlay([info("abc", summary="auto")])
    assert row.label == "auto"


def test_the_first_prompt_carries_a_row_with_no_summary() -> None:
    (row,) = merge_overlay([info("abc", summary="", first_prompt="do the thing")])
    assert row.label == "do the thing"


def test_the_id_is_the_last_resort() -> None:
    """A transcript too damaged to yield either still has to be selectable."""
    (row,) = merge_overlay([info("abc", summary="", first_prompt=None)])
    assert row.label == "abc"


# ---------------------------------------------------------------------------
# cwd filtering
# ---------------------------------------------------------------------------


def test_enumerate_delegates_the_cwd_to_the_lister() -> None:
    """
    Passed as ``directory`` so the SDK scans one project rather than every project.
    Filtering afterwards would do the expensive part anyway, which is the reason the
    filter lives here at all.
    """
    lister = RecordingLister(info("abc"))
    enumerate_sessions(cwd="/home/op/proj", lister=lister)
    assert lister.calls == ["/home/op/proj"]


def test_enumerate_asks_for_every_project_when_given_no_cwd() -> None:
    lister = RecordingLister(info("abc"))
    enumerate_sessions(lister=lister)
    assert lister.calls == [None]


def test_enumerate_merges_the_overlay() -> None:
    lister = RecordingLister(info("abc"))
    (row,) = enumerate_sessions(overlay=set_bookmark({}, "abc", True), lister=lister)
    assert row.bookmarked is True


def test_same_cwd_narrows_a_listing_already_in_hand() -> None:
    rows = merge_overlay(
        [
            info("here", cwd="/home/op/proj", last_modified=30),
            info("there", cwd="/home/op/other", last_modified=20),
            info("nowhere", cwd=None, last_modified=10),
        ]
    )
    assert [row.session_id for row in same_cwd(rows, "/home/op/proj")] == ["here"]


def test_same_cwd_preserves_order() -> None:
    rows = merge_overlay(
        [
            info("a", cwd="/p", last_modified=30),
            info("elsewhere", cwd="/q", last_modified=25),
            info("b", cwd="/p", last_modified=20),
        ]
    )
    assert [row.session_id for row in same_cwd(rows, "/p")] == ["a", "b"]


def test_same_cwd_does_not_match_a_worktree_of_the_directory() -> None:
    """
    The strict counterpart to ``enumerate_sessions(cwd=...)``, which folds in the
    directory's git worktrees. The two answer different questions and a caller has to
    be able to tell which it is getting.
    """
    rows = merge_overlay([info("wt", cwd="/home/op/proj-worktree")])
    assert same_cwd(rows, "/home/op/proj") == ()


# ---------------------------------------------------------------------------
# The contract with the SDK
# ---------------------------------------------------------------------------


def test_the_row_covers_every_field_the_sdk_reports() -> None:
    """
    ``SessionRow`` is built field by field from ``SDKSessionInfo``. If the SDK grows a
    field this stays silent, but if it *renames* one the construction breaks -- this
    pins which names are being relied on, so the breakage names the field.
    """
    import dataclasses

    reported = {f.name for f in dataclasses.fields(SDKSessionInfo)}
    used = {
        "session_id",
        "summary",
        "last_modified",
        "first_prompt",
        "git_branch",
        "cwd",
        "tag",
        "created_at",
    }
    assert used <= reported


def test_list_sessions_is_not_a_coroutine() -> None:
    """
    The default lister is called directly, not awaited. If the SDK ever makes it
    async, ``enumerate_sessions`` would return a coroutine that merges nothing and
    the picker would show an empty list rather than fail.
    """
    import inspect

    from claude_agent_sdk import list_sessions

    assert not inspect.iscoroutinefunction(list_sessions)
