"""
The brief: appending entries, reading them back, and deriving what a reader sees.

Driven against a real directory rather than a fake filesystem, because two of the
properties under test are properties of the write primitive itself -- that an
interrupted write leaves no partial entry, and that a temp file mid-write is not
something a reader trips over.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pptmstr import brief


def entries(directory: Path) -> tuple[brief.BriefEntry, ...]:
    return brief.read_entries(directory)


# -- where a brief lives ----------------------------------------------------------


def test_a_session_brief_sits_beside_the_transcripts_that_explain_it(tmp_path: Path) -> None:
    """
    Outside the working tree, which is where this project's durable state already
    is. In the tree, every launch would dirty the working directory.
    """
    path = brief.session_dir(tmp_path, "/home/x/Source/orbital", "sess-1")

    assert path == tmp_path / "-home-x-Source-orbital" / "briefs" / "sess-1"


def test_two_sessions_in_one_directory_do_not_share_a_brief(tmp_path: Path) -> None:
    a = brief.session_dir(tmp_path, "/x", "sess-1")
    b = brief.session_dir(tmp_path, "/x", "sess-2")

    assert a != b


# -- appending --------------------------------------------------------------------


def test_the_first_entry_is_the_premises(tmp_path: Path) -> None:
    path = brief.write_entry(tmp_path, "the parser is fixed-width")

    assert path.name == "000-premises.md"


def test_every_entry_after_the_first_is_an_amendment(tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "first")
    second = brief.write_entry(tmp_path, "second")
    third = brief.write_entry(tmp_path, "third")

    assert (second.name, third.name) == ("001-amendment.md", "002-amendment.md")


def test_an_entry_never_overwrites_one_that_is_already_there(tmp_path: Path) -> None:
    """
    `os.replace` onto an existing name destroys it silently, and a name derived from
    a count is exactly how that happens -- two writers taking the same listing would
    both compute the same next ordinal.
    """
    brief.write_entry(tmp_path, "first")
    (tmp_path / "001-amendment.md").write_text("planted", encoding="utf-8")

    brief.write_entry(tmp_path, "second")

    assert (tmp_path / "001-amendment.md").read_text(encoding="utf-8") == "planted"
    assert (tmp_path / "002-amendment.md").exists()


def test_an_entry_reads_back_as_it_was_written(tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "the parser is fixed-width\n\nevery field is positional")

    (entry,) = entries(tmp_path)

    assert entry.body == "the parser is fixed-width\n\nevery field is positional"
    assert entry.supersedes == ()


def test_the_directory_is_created_if_it_is_not_there(tmp_path: Path) -> None:
    target = tmp_path / "briefs" / "sess-1"
    brief.write_entry(target, "x")

    assert (target / "000-premises.md").exists()


def test_a_failed_write_leaves_no_entry_behind(tmp_path: Path) -> None:
    """
    Unlike `settings.save`, a failure here is raised rather than swallowed: a theme
    that fails to persist costs a preference, and premises that fail to persist are
    invisible until a worker acts on their absence.
    """

    class _Unwritable:
        def rstrip(self, _chars: str) -> str:
            raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        brief.write_entry(tmp_path, _Unwritable())  # type: ignore[arg-type]

    assert entries(tmp_path) == ()
    assert list(tmp_path.iterdir()) == []


# -- reading ----------------------------------------------------------------------


def test_a_brief_that_does_not_exist_yet_is_empty_rather_than_an_error(tmp_path: Path) -> None:
    """
    A session launched without a brief is the common case. Distinguishing "no brief"
    from "no directory yet" would be two spellings of one fact.
    """
    assert entries(tmp_path / "nothing-here") == ()


def test_a_temp_file_mid_write_is_not_something_a_reader_trips_over(tmp_path: Path) -> None:
    """
    The consistent-prefix property exists to protect exactly the moment a write is
    in flight, so a reader that failed on the temp file would fail then.
    """
    brief.write_entry(tmp_path, "real")
    (tmp_path / ".entry-abc123.md").write_text("half a rec", encoding="utf-8")

    assert [e.ordinal for e in entries(tmp_path)] == [0]


def test_a_file_that_is_not_an_entry_is_skipped(tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "real")
    (tmp_path / "README.md").write_text("not an entry", encoding="utf-8")
    (tmp_path / "brief.md").write_text("especially not this one", encoding="utf-8")

    assert [e.path.name for e in entries(tmp_path)] == ["000-premises.md"]


def test_entries_come_back_in_the_order_they_were_written(tmp_path: Path) -> None:
    for i in range(12):
        brief.write_entry(tmp_path, f"entry {i}")

    assert [e.ordinal for e in entries(tmp_path)] == list(range(12))


def test_the_ordinal_is_zero_padded_so_ls_agrees_with_the_derivation(tmp_path: Path) -> None:
    """
    A worker reads this directory with the tools it has. Unpadded names would sort
    `10` before `2` under `ls`, handing it a different sequence than the pane shows.
    """
    for i in range(11):
        brief.write_entry(tmp_path, f"entry {i}")

    names = sorted(p.name for p in tmp_path.iterdir())
    assert [int(n[:3]) for n in names] == list(range(11))


# -- supersession -----------------------------------------------------------------


def test_an_entry_can_declare_what_it_overturns(tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "the board is per-session")
    brief.write_entry(tmp_path, "that was wrong", supersedes=(0,))

    assert [e.supersedes for e in entries(tmp_path)] == [(), (0,)]


def test_several_supersessions_are_carried(tmp_path: Path) -> None:
    for _ in range(3):
        brief.write_entry(tmp_path, "x")
    brief.write_entry(tmp_path, "all three were wrong", supersedes=(0, 2))

    assert entries(tmp_path)[-1].supersedes == (0, 2)


def test_supersedes_further_down_the_body_is_prose_not_a_directive(tmp_path: Path) -> None:
    """
    An operator writing "this supersedes the earlier assumption" mid-paragraph means
    it as English. Reading it as structure would let the body rewrite the log.
    """
    brief.write_entry(tmp_path, "first")
    brief.write_entry(tmp_path, "a paragraph\nsupersedes: 000\nand more")

    assert entries(tmp_path)[1].supersedes == ()


def test_an_overturned_premise_stays_visible_and_says_who_overturned_it(tmp_path: Path) -> None:
    """
    Obligation 1. Rendering only the current state would make the premise silently
    disappear -- the log would exist and nobody reading could tell, which is the
    `Concern.edited` defect: a fact about the message that exists nowhere in it.
    """
    brief.write_entry(tmp_path, "the board is per-session")
    brief.write_entry(tmp_path, "that was wrong", supersedes=(0,))

    first, second = brief.derive(entries(tmp_path))

    assert first.entry.body == "the board is per-session"
    assert (first.is_superseded, first.superseded_by) == (True, (1,))
    assert second.is_superseded is False


def test_nothing_is_dropped_or_merged_by_the_derivation(tmp_path: Path) -> None:
    for i in range(4):
        brief.write_entry(tmp_path, f"entry {i}", supersedes=(i - 1,) if i else ())

    derived = brief.derive(entries(tmp_path))

    assert [d.entry.ordinal for d in derived] == [0, 1, 2, 3]
    assert [d.is_superseded for d in derived] == [True, True, True, False]


def test_an_earlier_entry_cannot_overturn_a_later_one(tmp_path: Path) -> None:
    """
    The log's order is its version. Letting entry 0 supersede entry 1 would make the
    sequence stop meaning anything.
    """
    brief.write_entry(tmp_path, "first", supersedes=(1,))
    brief.write_entry(tmp_path, "second")

    assert [d.is_superseded for d in brief.derive(entries(tmp_path))] == [False, False]


def test_superseding_an_ordinal_that_does_not_exist_is_ignored(tmp_path: Path) -> None:
    """A typo, and the alternative is a brief that refuses to render."""
    brief.write_entry(tmp_path, "first")
    brief.write_entry(tmp_path, "second", supersedes=(9,))

    assert [d.is_superseded for d in brief.derive(entries(tmp_path))] == [False, False]


def test_a_malformed_supersedes_value_does_not_take_the_brief_down(tmp_path: Path) -> None:
    (tmp_path / "000-premises.md").write_text("supersedes: banana\n\nbody", encoding="utf-8")

    (entry,) = entries(tmp_path)
    assert (entry.supersedes, entry.body) == ((), "body")


def test_deriving_an_empty_brief_is_empty(tmp_path: Path) -> None:
    assert brief.derive(()) == ()


def test_a_racing_writer_does_not_destroy_the_entry_that_beat_it(
    tmp_path: Path, monkeypatch
) -> None:
    """
    The collision loop guards a race, so a single-threaded call can never reach it:
    the ordinal comes from the listing, and any file that could collide with the
    computed name is a file that listing already saw.

    The race is injected instead of waited for. A stale listing is exactly what a
    writer holds when another one lands between its `read_entries` and its
    `os.replace` -- and `os.replace` onto an existing name destroys it silently,
    with no error to notice afterwards.
    """
    brief.write_entry(tmp_path, "first")
    winner = brief.write_entry(tmp_path, "the entry that got there first")

    stale = brief.read_entries(tmp_path)[:1]
    monkeypatch.setattr(brief, "read_entries", lambda _d: stale)

    brief.write_entry(tmp_path, "the racer")

    monkeypatch.undo()
    assert winner.read_text(encoding="utf-8").strip() == "the entry that got there first"
    assert [e.ordinal for e in brief.read_entries(tmp_path)] == [0, 1, 2]
