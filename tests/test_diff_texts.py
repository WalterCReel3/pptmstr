"""
The texts a parked approval's diff is made from.

``approval.diff_texts`` decides what a file-mutating call does to a file, and
``approval.render_diff`` is its unified rendering. These pin three things: the bytes
of the unified diff, which other consumers split and count; the texts and what they
say about themselves; and that both construction sites of ``PendingApproval`` take
the two from a single read of the file.
"""

from __future__ import annotations

import asyncio
import dataclasses
import difflib
import itertools
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from pptmstr import approval
from pptmstr.approval import DiffText, diff_texts, render_diff, render_texts
from pptmstr.bridge import Bridge
from pptmstr.driver import AgentSession
from pptmstr.fake_driver import FakeDriver
from pptmstr.intents import ApprovalRequested
from pptmstr.model import PendingApproval

# -- the unified bytes ----------------------------------------------------------------
#
# ``ApprovedWrites`` counts lines in the unified string, the copy button copies it and
# the inbox splits it, so its bytes are an interface. The reference below states them
# without going through ``DiffText`` at all: straight from the call's arguments and
# the file to ``difflib``.


def _reference_unified(before: str | None, after: str, label: str) -> str:
    prefix_a, prefix_b = ("", "") if label.startswith("/") else ("a/", "b/")
    return "".join(
        difflib.unified_diff(
            (before or "").splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{prefix_a}{label}" if before is not None else "/dev/null",
            tofile=f"{prefix_b}{label}",
            n=3,
        )
    )


def _reference_read(path: str) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _reference_render(tool_name: str, tool_input: Mapping[str, Any]) -> str | None:
    if tool_name == "Write":
        path = str(tool_input.get("file_path") or "")
        return _reference_unified(_reference_read(path), str(tool_input.get("content") or ""), path)
    if tool_name == "Edit":
        path = str(tool_input.get("file_path") or "")
        old = str(tool_input.get("old_string") or "")
        new = str(tool_input.get("new_string") or "")
        current = _reference_read(path)
        if current is not None and old and old in current:
            count = -1 if tool_input.get("replace_all") else 1
            return _reference_unified(current, current.replace(old, new, count), path)
        return _reference_unified(old, new, path or "(anchor not found in file)")
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return None
        parts = [
            _reference_unified(
                str(edit.get("old_string") or ""), str(edit.get("new_string") or ""), f"edit {i}"
            )
            for i, edit in enumerate(edits, 1)
            if isinstance(edit, dict)
        ]
        return "\n".join(p for p in parts if p) or None
    return None


_FRAGMENTS: tuple[Any, ...] = (
    "",
    "foo",
    "foo\n",
    "bar",
    "bar\n",
    "foo\nbeta",
    "alpha\nfoo\n",
    "\n",
    "zzz",
    "a\nb\nc",
    None,
    3,
)


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One file per condition the read can meet, and the cwd set so relative paths land."""
    (tmp_path / "lf.txt").write_text("alpha\nfoo\nbeta\nfoo\ngamma\n")
    (tmp_path / "nonl.txt").write_text("alpha\nfoo\nbeta\nfoo")
    (tmp_path / "one.txt").write_text("foo")
    (tmp_path / "empty.txt").write_text("")
    (tmp_path / "crlf.txt").write_bytes(b"alpha\r\nfoo\r\nbeta\r\n")
    (tmp_path / "blob").write_bytes(b"\xff\xfe\x00\x01")
    (tmp_path / "dir").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _battery(root: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    absolute = ("lf.txt", "nonl.txt", "one.txt", "empty.txt", "crlf.txt", "blob", "dir")
    paths: list[Any] = [str(root / name) for name in absolute]
    paths += [str(root / "missing.txt"), str(root / "lf.txt" / "under")]
    paths += ["lf.txt", "nonl.txt", "missing.txt", "", None, 7]
    for path in paths:
        yield "Write", {"file_path": path}
        yield "Edit", {"file_path": path}
        for content in _FRAGMENTS:
            yield "Write", {"file_path": path, "content": content}
        for old, new in itertools.product(_FRAGMENTS, _FRAGMENTS):
            yield "Edit", {"file_path": path, "old_string": old, "new_string": new}
            for replace_all in (False, True, "yes"):
                yield "Edit", {
                    "file_path": path,
                    "old_string": old,
                    "new_string": new,
                    "replace_all": replace_all,
                }
    edits = [
        {"old_string": old, "new_string": new}
        for old, new in itertools.product(_FRAGMENTS[:6], _FRAGMENTS[:6])
    ]
    for start in range(0, len(edits), 3):
        yield "MultiEdit", {"file_path": paths[0], "edits": edits[start : start + 3]}
    for edit in edits:
        yield "MultiEdit", {"edits": [edit]}
    yield "MultiEdit", {"edits": []}
    yield "MultiEdit", {"edits": None}
    yield "MultiEdit", {}
    yield "MultiEdit", {"edits": "nope"}
    yield "MultiEdit", {"edits": ({"old_string": "a", "new_string": "b"},)}
    yield "MultiEdit", {"edits": [1, {"old_string": "a", "new_string": "b"}, "x", {}]}
    yield "MultiEdit", {"edits": [{"old_string": "a", "new_string": "a"}]}
    yield "Write", {}
    yield "Edit", {}
    yield "Bash", {"command": "rm"}
    yield "NotebookEdit", {"file_path": paths[0]}
    yield "write", {"file_path": paths[0], "content": "x"}
    yield "", {}


def test_the_unified_diff_is_byte_for_byte_what_the_arguments_and_the_file_give(
    files: Path,
) -> None:
    calls = list(_battery(files))
    assert len(calls) > 8000
    wrong = [call for call in calls if render_diff(*call) != _reference_render(*call)]
    assert not wrong, wrong[:3]
    # The battery reaches each of the three answers a consumer branches on, so the
    # comparison above is not agreement about None alone.
    answers = [render_diff(*call) for call in calls]
    assert any(a is None for a in answers)
    assert any(a == "" for a in answers)
    assert sum(bool(a) for a in answers) > 5000


def test_a_call_that_changes_nothing_is_empty_for_one_file_and_none_for_a_multiedit(
    files: Path,
) -> None:
    """
    Both are falsy and every consumer today tests truthiness, but they are different
    bytes and ``render_texts`` is told the tool so that it can keep them apart.
    """
    path = str(files / "lf.txt")
    assert (
        render_diff("Write", {"file_path": path, "content": (files / "lf.txt").read_text()}) == ""
    )
    assert render_diff("Edit", {"file_path": path, "old_string": "foo", "new_string": "foo"}) == ""
    same = {"edits": [{"old_string": "a", "new_string": "a"}]}
    assert render_diff("MultiEdit", same) is None
    assert diff_texts("MultiEdit", same) == (DiffText("a", "a", "edit 1"),)


def test_render_diff_is_the_rendering_of_diff_texts(files: Path) -> None:
    """Held over the whole battery, so no branch renders from anything else."""
    for call in _battery(files):
        assert render_diff(*call) == render_texts(call[0], diff_texts(*call)), call


def test_a_deleted_side_is_dev_null_in_the_header() -> None:
    lines = (render_texts("Write", (DiffText("gone\n", None, "/x/y"),)) or "").splitlines()
    assert lines[:2] == ["--- /x/y", "+++ /dev/null"]
    assert lines[-1] == "-gone"


# -- the texts ------------------------------------------------------------------------


def _one(tool_name: str, tool_input: Mapping[str, Any]) -> DiffText:
    texts = diff_texts(tool_name, tool_input)
    assert texts is not None and len(texts) == 1
    return texts[0]


def test_the_record_is_frozen_and_positional_in_the_order_consumers_unpack() -> None:
    text = DiffText("b", "a", "label", "why")
    assert (text.before, text.after, text.label, text.will_not_apply) == ("b", "a", "label", "why")
    assert text.unreadable is False
    assert [f.name for f in dataclasses.fields(DiffText)] == [
        "before",
        "after",
        "label",
        "will_not_apply",
        "unreadable",
    ]
    assert not hasattr(text, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        text.before = "x"  # type: ignore[misc]


def test_a_write_over_an_existing_file_carries_the_whole_file(files: Path) -> None:
    path = str(files / "lf.txt")
    assert _one("Write", {"file_path": path, "content": "new\n"}) == DiffText(
        "alpha\nfoo\nbeta\nfoo\ngamma\n", "new\n", path
    )


def test_a_write_to_a_file_that_is_not_there_has_no_before(files: Path) -> None:
    path = str(files / "missing.txt")
    assert _one("Write", {"file_path": path, "content": "new\n"}) == DiffText(None, "new\n", path)


@pytest.mark.parametrize("name", ["blob", "dir"])
def test_a_write_over_a_file_that_cannot_be_read_is_not_mistaken_for_a_new_file(
    files: Path, name: str
) -> None:
    """
    The unified diff draws both as ``/dev/null`` and always has. The record is where
    they differ: replacing a binary file is not creating one.
    """
    path = str(files / name)
    over = _one("Write", {"file_path": path, "content": "text"})
    fresh = _one("Write", {"file_path": str(files / "missing.txt"), "content": "text"})
    assert (over.before, over.unreadable) == (None, True)
    assert (fresh.before, fresh.unreadable) == (None, False)
    assert "/dev/null" in (render_diff("Write", {"file_path": path, "content": "text"}) or "")


def test_a_path_beneath_a_regular_file_is_missing_rather_than_unreadable(files: Path) -> None:
    """``unreadable`` says a file is there. Nothing is at a path whose parent is a file."""
    text = _one("Write", {"file_path": str(files / "lf.txt" / "under"), "content": "x"})
    assert (text.before, text.unreadable) == (None, False)


def test_a_path_no_filesystem_can_hold_does_not_raise() -> None:
    """A NUL in the path is a ``ValueError`` from the read, not an ``OSError``."""
    text = _one("Write", {"file_path": "a\x00b", "content": "x"})
    assert (text.before, text.unreadable) == (None, True)
    assert render_diff("Edit", {"file_path": "a\x00b", "old_string": "a", "new_string": "b"})


def test_an_edit_whose_anchor_is_present_carries_the_file_before_and_after(files: Path) -> None:
    path = str(files / "lf.txt")
    text = _one("Edit", {"file_path": path, "old_string": "foo", "new_string": "FOO"})
    assert text == DiffText(
        "alpha\nfoo\nbeta\nfoo\ngamma\n", "alpha\nFOO\nbeta\nfoo\ngamma\n", path
    )


def test_an_edit_with_replace_all_replaces_every_occurrence_in_the_after_text(
    files: Path,
) -> None:
    path = str(files / "lf.txt")
    text = _one(
        "Edit", {"file_path": path, "old_string": "foo", "new_string": "FOO", "replace_all": True}
    )
    assert text.after == "alpha\nFOO\nbeta\nFOO\ngamma\n"
    assert text.will_not_apply is None


@pytest.mark.parametrize(
    ("name", "old", "reason", "unreadable"),
    [
        ("missing.txt", "foo", "file not found", False),
        ("blob", "foo", "file is not readable as text", True),
        ("dir", "foo", "file is not readable as text", True),
        ("lf.txt", "", "old_string is empty", False),
        ("lf.txt", "absent", "anchor not found in file", False),
        # The file's condition is named first: an anchor is not looked for, empty or
        # otherwise, in a file that was never read.
        ("missing.txt", "", "file not found", False),
        ("blob", "", "file is not readable as text", True),
    ],
)
def test_an_edit_with_nowhere_to_land_is_fragments_and_says_which_of_four_reasons(
    files: Path, name: str, old: str, reason: str, unreadable: bool
) -> None:
    path = str(files / name)
    text = _one("Edit", {"file_path": path, "old_string": old, "new_string": "bar"})
    assert text == DiffText(old, "bar", path, reason, unreadable)


def test_an_edit_with_no_path_is_labelled_by_a_stand_in() -> None:
    text = _one("Edit", {"old_string": "a", "new_string": "b"})
    assert text == DiffText("a", "b", "(anchor not found in file)", "file not found")


def test_a_multiedit_is_one_fragment_pair_per_edit_numbered_by_position() -> None:
    """A malformed entry leaves a gap, so ``edit 4`` is the fourth thing the call sent."""
    texts = diff_texts(
        "MultiEdit",
        {"edits": [{"old_string": "a", "new_string": "b"}, "junk", {"new_string": "c"}, {}]},
    )
    assert texts == (
        DiffText("a", "b", "edit 1"),
        DiffText("", "c", "edit 3"),
        DiffText("", "", "edit 4"),
    )


def test_a_multiedit_does_not_read_the_file(files: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Which is why its parts claim nothing about whether they apply."""

    def refuse(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("read")

    monkeypatch.setattr(Path, "read_text", refuse)
    call = {"file_path": str(files / "lf.txt"), "edits": [{"old_string": "x", "new_string": "y"}]}
    assert diff_texts("MultiEdit", call) == (DiffText("x", "y", "edit 1"),)


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("Bash", {"command": "rm -rf /"}),
        ("NotebookEdit", {"file_path": "x.ipynb"}),
        ("MultiEdit", {}),
        ("MultiEdit", {"edits": "nope"}),
        ("MultiEdit", {"edits": []}),
        ("MultiEdit", {"edits": ["junk"]}),
    ],
)
def test_nothing_diff_shaped_is_none_and_never_an_empty_tuple(
    tool_name: str, tool_input: dict[str, Any]
) -> None:
    assert diff_texts(tool_name, tool_input) is None
    assert render_diff(tool_name, tool_input) is None


# -- a removal and an addition on one physical line -------------------------------------
#
# ``difflib.unified_diff`` emits each line with whatever end it was given. A last line
# with no newline is emitted with none, and the line after it starts where it stopped.
# These pin where that happens in ``render_diff`` and where it does not; the bytes
# are left as they are because consumers count them.


def _lines(diff: str | None) -> list[str]:
    assert diff
    return diff.splitlines()


def test_an_edit_in_the_middle_of_a_file_keeps_removal_and_addition_apart(files: Path) -> None:
    """
    The main Edit path, with strings as unterminated as they ever are. The lines being
    diffed are the file's, and those have ends.
    """
    diff = render_diff(
        "Edit", {"file_path": str(files / "lf.txt"), "old_string": "beta", "new_string": "bar"}
    )
    assert _lines(diff)[2:] == [
        "@@ -1,5 +1,5 @@",
        " alpha",
        " foo",
        "-beta",
        "+bar",
        " foo",
        " gamma",
    ]


def test_an_edit_to_the_last_line_of_a_file_with_no_final_newline_joins_them(files: Path) -> None:
    """The main Edit path again: the anchor is in the file. What differs is the file."""
    path = str(files / "one.txt")
    call = {"file_path": path, "old_string": "foo", "new_string": "bar"}
    assert _one("Edit", call) == DiffText("foo", "bar", path)
    assert _lines(render_diff("Edit", call))[2:] == ["@@ -1 +1 @@", "-foo+bar"]


def test_an_edit_above_an_unterminated_last_line_runs_the_context_into_nothing(
    files: Path,
) -> None:
    """
    The changed line is terminated, so removal and addition stay apart. The file's
    last line is context here and simply ends the diff without a newline.
    """
    diff = render_diff(
        "Edit", {"file_path": str(files / "nonl.txt"), "old_string": "beta", "new_string": "bar"}
    )
    assert diff is not None and not diff.endswith("\n")
    assert _lines(diff)[2:] == ["@@ -1,4 +1,4 @@", " alpha", " foo", "-beta", "+bar", " foo"]


def test_an_edit_whose_anchor_is_absent_joins_them(files: Path) -> None:
    call = {"file_path": str(files / "lf.txt"), "old_string": "absent", "new_string": "bar"}
    assert _one("Edit", call).will_not_apply == "anchor not found in file"
    assert _lines(render_diff("Edit", call))[2:] == ["@@ -1 +1 @@", "-absent+bar"]


def test_an_edit_whose_file_is_absent_joins_them(files: Path) -> None:
    call = {"file_path": str(files / "missing.txt"), "old_string": "foo", "new_string": "bar"}
    assert _lines(render_diff("Edit", call))[2:] == ["@@ -1 +1 @@", "-foo+bar"]


def test_a_multiedit_joins_them_and_still_separates_its_parts() -> None:
    diff = render_diff(
        "MultiEdit",
        {
            "edits": [
                {"old_string": "foo", "new_string": "bar"},
                {"old_string": "one", "new_string": "two"},
            ]
        },
    )
    assert _lines(diff) == [
        "--- a/edit 1",
        "+++ b/edit 1",
        "@@ -1 +1 @@",
        "-foo+bar",
        "--- a/edit 2",
        "+++ b/edit 2",
        "@@ -1 +1 @@",
        "-one+two",
    ]


def test_a_write_of_unterminated_content_over_an_unterminated_file_joins_them(
    files: Path,
) -> None:
    diff = render_diff("Write", {"file_path": str(files / "one.txt"), "content": "bar"})
    assert _lines(diff)[2:] == ["@@ -1 +1 @@", "-foo+bar"]


def test_fragments_with_their_own_newlines_do_not_join(files: Path) -> None:
    """The join is a property of the missing newline, not of the fallback path."""
    call = {"file_path": str(files / "missing.txt"), "old_string": "foo\n", "new_string": "bar\n"}
    assert _lines(render_diff("Edit", call))[2:] == ["@@ -1 +1 @@", "-foo", "+bar"]


# -- the construction sites -----------------------------------------------------------


def test_a_pending_approval_built_without_texts_has_none() -> None:
    pending = PendingApproval(
        id="p",
        node=("s", None),
        tool_name="Bash",
        tool_use_id="t",
        raw_args={},
        summary="",
        requested_at=0.0,
    )
    assert pending.texts is None


class _Reads:
    """Counts whole-file reads, which is what ``diff_texts`` does to the target."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paths: list[str] = []
        real = Path.read_text

        def counted(path: Path, *args: Any, **kwargs: Any) -> str:
            self.paths.append(str(path))
            return real(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", counted)


def _real_park(tool_name: str, tool_input: dict[str, Any]) -> PendingApproval:
    """
    Run the real gate's park as far as the intent it emits, then tear the host down.
    """
    bridge = Bridge()
    bridge.start()
    try:
        session = AgentSession(bridge, task="lead")
        bridge.submit(session._park(tool_name, tool_input, "tu-1", session.node_id))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            requested = [i for i in bridge.drain() if isinstance(i, ApprovalRequested)]
            if requested:
                return requested[0].pending
            time.sleep(0.005)
        raise AssertionError("the park emitted nothing")
    finally:
        bridge.stop()


def _fake_park(tool_name: str, tool_input: dict[str, Any]) -> PendingApproval:
    return FakeDriver(Bridge())._pending(("s", None), (tool_name, tool_input))


@pytest.mark.parametrize("park", [_real_park, _fake_park])
def test_a_parked_edit_carries_the_texts_and_the_diff_of_one_read(
    files: Path, monkeypatch: pytest.MonkeyPatch, park: Any
) -> None:
    """
    Two reads could straddle a write to the file, and the texts would then describe a
    different edit from the diff the copy button and the write tally use.
    """
    path = str(files / "lf.txt")
    call = {"file_path": path, "old_string": "beta", "new_string": "bar"}
    reads = _Reads(monkeypatch)
    pending = park("Edit", dict(call))
    assert reads.paths.count(path) == 1
    assert pending.texts == (
        DiffText("alpha\nfoo\nbeta\nfoo\ngamma\n", "alpha\nfoo\nbar\nfoo\ngamma\n", path),
    )
    assert pending.diff == _reference_render("Edit", call)
    assert pending.diff == render_texts("Edit", pending.texts)


@pytest.mark.parametrize("park", [_real_park, _fake_park])
def test_a_parked_multiedit_carries_one_text_per_edit(park: Any) -> None:
    call = {
        "file_path": "x.py",
        "edits": [
            {"old_string": "a", "new_string": "b"},
            {"old_string": "c", "new_string": "d"},
        ],
    }
    pending = park("MultiEdit", dict(call))
    assert pending.texts == (DiffText("a", "b", "edit 1"), DiffText("c", "d", "edit 2"))
    assert pending.diff == _reference_render("MultiEdit", call)


@pytest.mark.parametrize("park", [_real_park, _fake_park])
def test_a_parked_call_with_nothing_diff_shaped_carries_neither(park: Any) -> None:
    pending = park("Bash", {"command": "make install"})
    assert pending.texts is None and pending.diff is None


def test_every_approval_the_demo_fleet_parks_agrees_with_its_own_texts() -> None:
    driver = FakeDriver(Bridge())
    asyncio.run(driver._seed_tree())
    parked = [i.pending for i in driver.bridge.drain() if isinstance(i, ApprovalRequested)]
    with_texts = [p for p in parked if p.texts is not None]
    assert {p.tool_name for p in with_texts} >= {"Write", "Edit", "MultiEdit"}
    for pending in parked:
        assert pending.diff == render_texts(pending.tool_name, pending.texts), pending.summary
        assert (pending.texts is None) == (diff_texts(pending.tool_name, pending.raw_args) is None)


def test_only_the_approval_module_decides_what_a_call_does_to_a_file() -> None:
    """
    Neither driver calls ``render_diff``: that would be a second read beside the one
    ``diff_texts`` made for the texts.
    """
    for module in ("driver.py", "fake_driver.py"):
        source = (Path(approval.__file__).parent / module).read_text(encoding="utf-8")
        assert "render_diff(" not in source, module
        assert source.count("diff_texts(") == 1, module
